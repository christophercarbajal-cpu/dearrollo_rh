"""Proceso configurable y seguimiento de candidatos (2026-10-06). Ver services/proceso.py.

- Configuración → Procesos de selección: plantillas de la Cuenta (`/procesos/plantillas`). Editar sube la versión y
  nunca toca a vacantes ni candidatos. Eliminar = desactivar. Hay tres ejemplos listos (`/procesos/ejemplos`).
- Vacante: su copia personalizable viaja en `POST/PATCH /vacantes` (`proceso`); aquí solo se consulta / guarda aparte.
- Candidato: `/procesos/postulaciones/{codigo}` = vista de seguimiento; omitir / cancelar / reactivar un paso
  (obligatorio → justificación + permiso «Autorizar omisiones») y «Aplicar versión vigente» (explícito).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import cuenta_actual, usuario_actual, usuario_decisor
from ..models import (
    ENFOQUES_ENTREVISTA, ESTADOS_PASO, ETAPAS_CANDIDATO, ETAPAS_SIN_AVANCE_AUTOMATICO, REGLAS_APROBACION, RESPONSABLES_PASO,
    RESULTADOS_PASO, TIPOS_ENTREVISTA_HUMANA, TIPOS_PASO, Cuenta, PlantillaProceso, Usuario, Vacante, conclusiones_de,
    nombre_etapa, registrar,
)
from ..services import proceso as sproc
from ..services.modulos_rh import requiere_modulos_rh

router = APIRouter(prefix="/procesos", tags=["procesos"])


def _error(e: sproc.ErrorProceso):
    return HTTPException(e.status, e.mensaje)


# ------------------------------------------------------------ catálogo

@router.get("/opciones")
def opciones(_: Usuario = Depends(usuario_actual)):
    """Todo lo que necesita el editor ÚNICO de pasos (Configuración y formulario de vacante)."""
    return {
        "etapas": [{"valor": e, "texto": nombre_etapa(e), "permiteAvanceAutomatico": e not in ETAPAS_SIN_AVANCE_AUTOMATICO}
                   for e in ETAPAS_CANDIDATO],
        "tiposPaso": [{"valor": k, "texto": d["nombre"], "etapa": d["etapa"], "etapas": list(d["etapas"]), "regla": d["regla"],
                       "responsable": d["responsable"],
                       "dictamenes": [{"valor": c, "texto": t} for c, t in conclusiones_de(k).items()] if k in sproc.TIPOS_PASO_EVALUACION else []}
                      for k, d in TIPOS_PASO.items()],
        "reglas": [{"valor": k, "texto": t} for k, t in REGLAS_APROBACION.items()],
        "responsables": [{"valor": k, "texto": t} for k, t in RESPONSABLES_PASO.items()],
        "tiposEntrevistaHumana": [{"valor": k, "texto": t} for k, t in TIPOS_ENTREVISTA_HUMANA.items()],
        "enfoquesEntrevistaAgente": [{"valor": k, "texto": "Profesional" if k == "profesional" else "Profesional y personal"} for k in ENFOQUES_ENTREVISTA],
        "estados": [{"valor": k, "texto": t} for k, t in ESTADOS_PASO.items()],
        "resultados": [{"valor": k, "texto": t} for k, t in RESULTADOS_PASO.items()],
        "ejemplos": [{"clave": k, "nombre": e["nombre"], "descripcion": e["descripcion"]} for k, e in sproc.PROCESOS_EJEMPLO.items()],
    }


@router.get("/ejemplos/{clave}")
def ver_ejemplo(clave: str, _: Usuario = Depends(usuario_actual)):
    try:
        return sproc.ejemplo(clave)
    except sproc.ErrorProceso as e:
        raise _error(e)


# ------------------------------------------------------------ plantillas de la Cuenta

class PlantillaIn(BaseModel):
    nombre: str
    descripcion: str = ""
    pasos: List[dict] = []
    etapas: dict = {}
    predeterminada: bool = False


class EditarPlantillaIn(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    pasos: Optional[List[dict]] = None
    etapas: Optional[dict] = None
    predeterminada: Optional[bool] = None


def _plantilla(db: Session, pid: int, cuenta_id: int) -> PlantillaProceso:
    pl = db.query(PlantillaProceso).filter(PlantillaProceso.id == pid, PlantillaProceso.cuenta_id == cuenta_id).first()
    if not pl:
        raise HTTPException(404, "Plantilla de proceso no encontrada.")
    return pl


def _solo_una_predeterminada(db: Session, pl: PlantillaProceso) -> None:
    if pl.predeterminada:
        (db.query(PlantillaProceso)
         .filter(PlantillaProceso.cuenta_id == pl.cuenta_id, PlantillaProceso.id != pl.id, PlantillaProceso.predeterminada.is_(True))
         .update({PlantillaProceso.predeterminada: False}, synchronize_session=False))


@router.get("/plantillas", dependencies=[Depends(requiere_modulos_rh)])
def listar_plantillas(incluir_inactivas: bool = False, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual),
                      cuenta: Cuenta = Depends(cuenta_actual)):
    q = db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cuenta.id)
    if not incluir_inactivas:
        q = q.filter(PlantillaProceso.activa.is_(True))
    return [sproc.plantilla_dict(pl) for pl in q.order_by(PlantillaProceso.nombre).all()]


@router.post("/plantillas", status_code=201, dependencies=[Depends(requiere_modulos_rh)])
def crear_plantilla(datos: PlantillaIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                    cuenta: Cuenta = Depends(cuenta_actual)):
    nombre = datos.nombre.strip()[:200]
    if not nombre:
        raise HTTPException(400, "El nombre del proceso es obligatorio.")
    try:
        pasos = sproc.normalizar_pasos(datos.pasos)
    except sproc.ErrorProceso as e:
        raise _error(e)
    if not pasos:
        raise HTTPException(400, "Agrega al menos un paso al proceso.")
    pl = PlantillaProceso(cuenta_id=cuenta.id, nombre=nombre, descripcion=datos.descripcion.strip()[:2000], pasos=pasos,
                          etapas=sproc.normalizar_etapas(datos.etapas), predeterminada=datos.predeterminada, version=1,
                          creado_por=u.nombre, actualizada_por=u.nombre)
    db.add(pl)
    db.flush()
    _solo_una_predeterminada(db, pl)
    registrar(db, u.nombre, "plantilla_proceso_creada", "plantilla_proceso", str(pl.id),
              {"nombre": pl.nombre, "pasos": len(pasos), "predeterminada": pl.predeterminada, "correo_rh": u.correo})
    db.commit()
    return sproc.plantilla_dict(pl)


@router.post("/plantillas/ejemplo/{clave}", status_code=201, dependencies=[Depends(requiere_modulos_rh)])
def crear_desde_ejemplo(clave: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                        cuenta: Cuenta = Depends(cuenta_actual)):
    try:
        e = sproc.ejemplo(clave)
    except sproc.ErrorProceso as ex:
        raise _error(ex)
    return crear_plantilla(PlantillaIn(**e), db, u, cuenta)


@router.patch("/plantillas/{pid}", dependencies=[Depends(requiere_modulos_rh)])
def editar_plantilla(pid: int, datos: EditarPlantillaIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                     cuenta: Cuenta = Depends(cuenta_actual)):
    """Editar NUNCA toca a las vacantes que ya copiaron la plantilla ni a sus candidatos: sube la versión y las
    vacantes nuevas (o las que RH vuelva a asociar) toman la nueva."""
    pl = _plantilla(db, pid, cuenta.id)
    cambios = []
    if datos.nombre is not None:
        if not datos.nombre.strip():
            raise HTTPException(400, "El nombre del proceso es obligatorio.")
        pl.nombre = datos.nombre.strip()[:200]
        cambios.append("nombre")
    if datos.descripcion is not None:
        pl.descripcion = datos.descripcion.strip()[:2000]
        cambios.append("descripcion")
    contenido = False
    if datos.pasos is not None:
        try:
            pasos = sproc.normalizar_pasos(datos.pasos)
        except sproc.ErrorProceso as e:
            raise _error(e)
        if not pasos:
            raise HTTPException(400, "Agrega al menos un paso al proceso.")
        if pasos != (pl.pasos or []):
            pl.pasos = pasos
            contenido = True
    if datos.etapas is not None:
        etapas = sproc.normalizar_etapas(datos.etapas)
        if etapas != sproc.normalizar_etapas(pl.etapas):
            pl.etapas = etapas
            contenido = True
    if contenido:
        pl.version = (pl.version or 1) + 1
        cambios.append("contenido")
    if datos.predeterminada is not None:
        pl.predeterminada = bool(datos.predeterminada)
        _solo_una_predeterminada(db, pl)
        cambios.append("predeterminada")
    pl.actualizada_por = u.nombre
    registrar(db, u.nombre, "plantilla_proceso_editada", "plantilla_proceso", str(pl.id),
              {"cambios": cambios, "version": pl.version, "correo_rh": u.correo})
    db.commit()
    return sproc.plantilla_dict(pl)


@router.delete("/plantillas/{pid}", dependencies=[Depends(requiere_modulos_rh)])
def desactivar_plantilla(pid: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                         cuenta: Cuenta = Depends(cuenta_actual)):
    """Eliminar = desactivar: las vacantes que la copiaron conservan su proceso."""
    pl = _plantilla(db, pid, cuenta.id)
    pl.activa = False
    pl.predeterminada = False
    registrar(db, u.nombre, "plantilla_proceso_desactivada", "plantilla_proceso", str(pl.id), {"nombre": pl.nombre, "correo_rh": u.correo})
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------ proceso de una vacante

def _vacante(db: Session, codigo: str, cuenta_id: int) -> Vacante:
    v = db.query(Vacante).filter(Vacante.codigo == codigo, Vacante.cuenta_id == cuenta_id).first()
    if not v or v.estado == "Eliminada":
        raise HTTPException(404, "Vacante no encontrada.")
    return v


@router.get("/vacantes/{codigo}")
def proceso_vacante(codigo: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    v = _vacante(db, codigo, cuenta.id)
    activas = [p for p in v.postulaciones if p.activa]
    return {
        "proceso": v.proceso or {},
        "candidatosActivos": len(activas),
        "candidatosConVersionAnterior": sum(1 for p in activas if sproc.desactualizado(p)),
    }


@router.put("/vacantes/{codigo}")
def guardar_proceso_vacante(codigo: str, datos: dict, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                            cuenta: Cuenta = Depends(cuenta_actual)):
    """Asociar / personalizar / quitar el proceso de la vacante (mismo formato que `proceso` en POST/PATCH /vacantes).
    Los candidatos existentes conservan su versión."""
    v = _vacante(db, codigo, cuenta.id)
    try:
        nuevo = sproc.proceso_para_vacante(db, cuenta.id, v.proceso or {}, datos)
    except sproc.ErrorProceso as e:
        raise _error(e)
    if nuevo != (v.proceso or {}):
        v.proceso = nuevo
        registrar(db, u.nombre, "vacante_proceso_actualizado", "vacante", v.codigo,
                  {"version": nuevo.get("version"), "plantilla": nuevo.get("plantilla_nombre"), "personalizado": nuevo.get("personalizado"),
                   "pasos": len(nuevo.get("pasos") or []), "correo_rh": u.correo})
        db.commit()
    return proceso_vacante(codigo, db, u, cuenta)


# ------------------------------------------------------------ seguimiento del candidato

def _postulacion(db: Session, codigo: str, cuenta_id: int):
    from .candidatos import _por_codigo

    return _por_codigo(db, codigo, cuenta_id)


def _salida(p) -> dict:
    from ..serial import postulacion_dict

    return {"proceso": sproc.resumen(p), "candidato": postulacion_dict(p, detalle=True)}


@router.get("/postulaciones/{codigo}")
def seguimiento(codigo: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    return sproc.resumen(_postulacion(db, codigo, cuenta.id))


class MotivoIn(BaseModel):
    motivo: str = ""


@router.post("/postulaciones/{codigo}/pasos/{paso_id}/omitir")
def omitir_paso(codigo: str, paso_id: str, datos: MotivoIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                cuenta: Cuenta = Depends(cuenta_actual)):
    """Omitir un paso. Obligatorio → justificación + permiso «Autorizar omisiones» (403 sin él)."""
    p = _postulacion(db, codigo, cuenta.id)
    try:
        sproc.omitir(db, p, paso_id, u, datos.motivo, "omitida")
    except sproc.ErrorProceso as e:
        raise _error(e)
    db.commit()
    return _salida(p)


@router.post("/postulaciones/{codigo}/pasos/{paso_id}/cancelar")
def cancelar_paso(codigo: str, paso_id: str, datos: MotivoIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                  cuenta: Cuenta = Depends(cuenta_actual)):
    """Cancelar un paso (ya no se hará). Mismas reglas que omitir; no cancela la evaluación ligada (eso es aparte)."""
    p = _postulacion(db, codigo, cuenta.id)
    try:
        sproc.omitir(db, p, paso_id, u, datos.motivo, "cancelada")
    except sproc.ErrorProceso as e:
        raise _error(e)
    db.commit()
    return _salida(p)


@router.post("/postulaciones/{codigo}/pasos/{paso_id}/reactivar")
def reactivar_paso(codigo: str, paso_id: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                   cuenta: Cuenta = Depends(cuenta_actual)):
    p = _postulacion(db, codigo, cuenta.id)
    try:
        sproc.reactivar(db, p, paso_id, u)
    except sproc.ErrorProceso as e:
        raise _error(e)
    db.commit()
    return _salida(p)


@router.post("/postulaciones/{codigo}/aplicar-vigente")
def aplicar_vigente(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Explícito: la postulación toma la versión vigente del proceso de su vacante sin perder lo hecho."""
    p = _postulacion(db, codigo, cuenta.id)
    try:
        r = sproc.aplicar_version_vigente(db, p, u)
    except sproc.ErrorProceso as e:
        raise _error(e)
    db.commit()
    return {**_salida(p), "aplicado": r}
