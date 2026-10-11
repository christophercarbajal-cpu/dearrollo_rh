"""Proceso configurable y seguimiento de candidatos (2026-10-06). Ver services/proceso.py.

- Configuración → Procesos de selección: plantillas de la Cuenta (`/procesos/plantillas`). Editar sube la versión y
  nunca toca a vacantes ni candidatos. Eliminar = desactivar. Hay tres ejemplos listos (`/procesos/ejemplos`).
- Vacante: su copia personalizable viaja en `POST/PATCH /vacantes` (`proceso`); aquí solo se consulta / guarda aparte.
- Candidato: `/procesos/postulaciones/{codigo}` = vista de seguimiento; omitir / cancelar / reactivar un paso
  (obligatorio → justificación + permiso «Autorizar omisiones») y «Aplicar versión vigente» (explícito).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import cuenta_actual, usuario_actual, usuario_decisor
from ..models import (
    ENFOQUES_ENTREVISTA, TEXTO_ENFOQUE, ESTADOS_PASO, ETAPAS_CANDIDATO, ETAPAS_SIN_AVANCE_AUTOMATICO, REGLAS_APROBACION, RESPONSABLES_PASO,
    RESULTADOS_PASO, TIPOS_ENTREVISTA_HUMANA, TIPOS_PASO, CATALOGO_ACTIVIDADES, opcion_catalogo, Cuenta, PlantillaProceso, Usuario, Vacante, conclusiones_de,
    es_cuenta_demo, nombre_etapa, registrar,
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
        # 2026-10-10: las 22 actividades del catálogo, en el orden de la especificación (sin las de legado)
        "tiposPaso": [{"valor": k, "texto": d["nombre"], "etapa": d["etapa"], "etapas": list(d["etapas"]), "regla": d["regla"],
                       "responsable": d["responsable"], "obligatorio": d.get("obligatorio", True),
                       "dictamenes": [{"valor": c, "texto": t} for c, t in conclusiones_de(d["tipo_real"]).items()]
                       if d["tipo_real"] in sproc.TIPOS_PASO_EVALUACION else []}
                      for k, d in ((k, opcion_catalogo(k)) for k in CATALOGO_ACTIVIDADES)],
        "reglas": [{"valor": k, "texto": t} for k, t in REGLAS_APROBACION.items()],
        "responsables": [{"valor": k, "texto": t} for k, t in RESPONSABLES_PASO.items()],
        "tiposEntrevistaHumana": [{"valor": k, "texto": t} for k, t in TIPOS_ENTREVISTA_HUMANA.items()],
        "enfoquesEntrevistaAgente": [{"valor": k, "texto": TEXTO_ENFOQUE[k]} for k in ENFOQUES_ENTREVISTA],
        "estados": [{"valor": k, "texto": t} for k, t in ESTADOS_PASO.items()],
        "resultados": [{"valor": k, "texto": t} for k, t in RESULTADOS_PASO.items()],
        "ejemplos": [{"clave": k, "nombre": e["nombre"], "descripcion": e["descripcion"]} for k, e in sproc.PROCESOS_EJEMPLO.items()],
        # 2026-10-06: rutas base precargadas (sembradas como plantillas editables en cada Cuenta)
        "rutasBase": [{"clave": k, "nombre": e["nombre"], "descripcion": e["descripcion"], "pasos": len(e["pasos"]),
                       "respaldo": k == sproc.RUTA_RESPALDO} for k, e in sproc.RUTAS_BASE.items()],
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


@router.post("/plantillas/rutas-base", dependencies=[Depends(requiere_modulos_rh)])
def restaurar_rutas_base(db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Restaurar rutas base»: siembra las que falten y REACTIVA las que RH desactivó (con sus ediciones; nunca las
    sobrescribe)."""
    n = sproc.asegurar_rutas_base(db, cuenta.id, u.nombre)
    for pl in db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cuenta.id,
                                                PlantillaProceso.ruta_base.in_(list(sproc.RUTAS_BASE)),
                                                PlantillaProceso.activa.is_(False)).all():
        pl.activa = True
        pl.actualizada_por = u.nombre
        n += 1
    if n:
        registrar(db, u.nombre, "rutas_base_sembradas", "cuenta", str(cuenta.id), {"creadas": n, "correo_rh": u.correo})
    db.commit()
    return {"creadas": n}


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
    vacantes nuevas (o las que RH vuelva a asociar) toman la nueva. 2026-10-09: en una Cuenta DEMO, cambiar el contenido
    de una ruta base la DUPLICA («… (copia)») y la original queda intacta; la respuesta es la copia."""
    pl = _plantilla(db, pid, cuenta.id)
    if es_cuenta_demo(cuenta) and pl.ruta_base and (datos.pasos is not None or datos.etapas is not None):
        copia = PlantillaProceso(cuenta_id=cuenta.id, nombre=f"{(datos.nombre or pl.nombre).strip()[:190]} (copia)",
                                 descripcion=pl.descripcion or "", pasos=pl.pasos or [], etapas=pl.etapas or {}, version=1,
                                 predeterminada=False, activa=True, creado_por=u.nombre, actualizada_por=u.nombre, ruta_base="")
        db.add(copia)
        db.flush()
        registrar(db, u.nombre, "plantilla_proceso_duplicada", "plantilla_proceso", str(copia.id),
                  {"original": pl.id, "motivo": "cuenta_demo", "correo_rh": u.correo})
        pl = copia
        datos = datos.model_copy(update={"nombre": None})
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

def _sincronizar_contrato(db: Session, p) -> None:
    """2026-10-08: la tarea «Contrato firmado» de Onboarding refleja la actividad de contrato de Contratación (firmada,
    omitida o reactivada). No hace commit; nunca rompe la acción."""
    if p is None or p.expediente is None:
        return
    try:
        from ..services import onboarding as onb

        onb.sincronizar_contrato(db, p.expediente)
    except Exception:  # noqa: BLE001
        pass


def _postulacion(db: Session, codigo: str, cuenta_id: int):
    from .candidatos import _por_codigo

    return _por_codigo(db, codigo, cuenta_id)


def _salida(p) -> dict:
    from ..serial import postulacion_dict

    return {"proceso": sproc.resumen(p), "candidato": postulacion_dict(p, detalle=True)}


@router.get("/postulaciones/{codigo}")
def seguimiento(codigo: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    p = _postulacion(db, codigo, cuenta.id)
    if p.expediente is not None:
        from ..services import onboarding as onb

        try:
            if onb.sincronizar_contrato(db, p.expediente):
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
    salida = sproc.resumen(p)
    # Canal Telegram (2026-10-06): deep links de la postulación y de cada paso, solo si la Cuenta atiende por Telegram
    from ..services.canal_proceso import ligas_telegram
    from ..services.mensajeria import canal_de_cuenta

    salida["telegram"] = {"disponible": False, "liga": "", "pasos": {}}
    if salida.get("tieneProceso") and canal_de_cuenta(db, cuenta.id) in ("telegram", "ambos"):
        nuevo = not p.telegram_token
        salida["telegram"] = ligas_telegram(p)
        if nuevo and p.telegram_token:
            db.commit()
    return salida


class MotivoIn(BaseModel):
    motivo: str = ""


@router.post("/postulaciones/{codigo}/pasos/{paso_id}/omitir")
async def omitir_paso(codigo: str, paso_id: str, datos: MotivoIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                cuenta: Cuenta = Depends(cuenta_actual)):
    """Omitir un paso. Obligatorio → justificación + permiso «Autorizar omisiones» (403 sin él)."""
    p = _postulacion(db, codigo, cuenta.id)
    try:
        sproc.omitir(db, p, paso_id, u, datos.motivo, "omitida")
    except sproc.ErrorProceso as e:
        raise _error(e)
    _sincronizar_contrato(db, p)  # omitir el contrato en Contratación → «Omitida» en Onboarding
    db.commit()
    await sproc.avanzar_seguro(db, p)  # la etapa la define la ruta: si quedó lista, avanza sola
    return _salida(p)


@router.post("/postulaciones/{codigo}/pasos/{paso_id}/cancelar")
async def cancelar_paso(codigo: str, paso_id: str, datos: MotivoIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                  cuenta: Cuenta = Depends(cuenta_actual)):
    """Cancelar un paso (ya no se hará). Mismas reglas que omitir; no cancela la evaluación ligada (eso es aparte)."""
    p = _postulacion(db, codigo, cuenta.id)
    try:
        sproc.omitir(db, p, paso_id, u, datos.motivo, "cancelada")
    except sproc.ErrorProceso as e:
        raise _error(e)
    _sincronizar_contrato(db, p)
    db.commit()
    await sproc.avanzar_seguro(db, p)
    return _salida(p)


@router.post("/postulaciones/{codigo}/prefiltro/aprobar")
def aprobar_prefiltro(codigo: str, _: Usuario = Depends(usuario_decisor)):
    """Retirado (especificación 2026-10-10): el agente decide el prefiltro. Un descarte se revierte con «Reactivar»."""
    raise HTTPException(410, "«Aprobar prefiltro» ya no existe: Red Human decide el prefiltro. Para revertir un descarte usa «Reactivar».")


@router.post("/postulaciones/{codigo}/pasos/{paso_id}/reactivar")
def reactivar_paso(codigo: str, paso_id: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                   cuenta: Cuenta = Depends(cuenta_actual)):
    p = _postulacion(db, codigo, cuenta.id)
    try:
        sproc.reactivar(db, p, paso_id, u)
    except sproc.ErrorProceso as e:
        raise _error(e)
    _sincronizar_contrato(db, p)
    db.commit()
    return _salida(p)


class ActividadAdHocIn(BaseModel):
    tipo: str
    nombre: str = ""
    etapa: str = ""
    obligatorio: bool = False
    depende_de: List[str] = []
    responsable: Optional[dict] = None
    plazo_dias: Optional[int] = None
    tipo_entrevista: Optional[str] = None


@router.post("/postulaciones/{codigo}/pasos", status_code=201)
def agregar_actividad(codigo: str, datos: ActividadAdHocIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                      cuenta: Cuenta = Depends(cuenta_actual)):
    """«Más acciones → Agregar actividad»: un paso extra SOLO para este candidato (entrevista, prueba, documentos…).
    Nunca toca la plantilla ni la vacante. Por defecto no es obligatorio."""
    p = _postulacion(db, codigo, cuenta.id)
    if not p.activa:
        raise HTTPException(409, "La postulación está cerrada.")
    crudo = {k: v for k, v in datos.model_dump().items() if v not in (None, "", [])}
    crudo["obligatorio"] = datos.obligatorio
    try:
        paso = sproc.agregar_paso_adhoc(db, p, crudo, u)
    except sproc.ErrorProceso as e:
        raise _error(e)
    db.commit()
    return {**_salida(p), "paso": paso}


# ------------------------------------------------------------ operación de UNA actividad (2026-10-08)

class IniciarIn(BaseModel):
    """Solo lo que falte (la API dice qué con `faltan`); vacío = ejecutar lo configurado en la ruta."""
    forma: str = ""
    evaluador: Optional[dict] = None
    correo: str = ""
    prueba_ids: List[int] = []
    cita: Optional[dict] = None
    instrucciones: str = ""
    liga_externa_candidato: str = ""


@router.post("/postulaciones/{codigo}/pasos/{paso_id}/iniciar")
async def iniciar_actividad(codigo: str, paso_id: str, datos: IniciarIn, db: Session = Depends(get_db),
                            u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Iniciar» en UN paso: ejecuta lo configurado; si falta un dato crítico regresa `faltan` sin crear nada."""
    from ..services import actividades

    p = _postulacion(db, codigo, cuenta.id)
    r = await actividades.iniciar(db, p, paso_id, u, cuenta, datos.model_dump())
    db.refresh(p)
    return {**r, **_salida(p)}


class ReenviarIn(BaseModel):
    a: str  # candidato | entrevistador | medico | evaluador


@router.post("/postulaciones/{codigo}/pasos/{paso_id}/reenviar")
async def reenviar_actividad(codigo: str, paso_id: str, datos: ReenviarIn, db: Session = Depends(get_db),
                             u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Reenvío GRANULAR (su propia liga a ese destinatario). No cambia el estado ni reinicia la actividad."""
    from ..services import actividades

    p = _postulacion(db, codigo, cuenta.id)
    r = await actividades.reenviar(db, p, paso_id, datos.a, u, cuenta)
    db.refresh(p)
    return {**r, **_salida(p)}


@router.post("/postulaciones/{codigo}/pasos/{paso_id}/excepcion")
async def excepcion_rh(codigo: str, paso_id: str, datos: MotivoIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                       cuenta: Cuenta = Depends(cuenta_actual)):
    """«Continuar por decisión de RH» sobre una actividad «No aprobada»: la libera SIN cambiar su resultado ni su score
    y SIN omitirla; el motor se recalcula en el mismo momento (avanza si la ruta quedó lista y la Cuenta lo permite)."""
    from ..services import prefiltro_conversacional as pconv

    p = _postulacion(db, codigo, cuenta.id)
    try:
        sproc.excepcion_rh(db, p, paso_id, u, datos.motivo)
    except sproc.ErrorProceso as e:
        raise _error(e)
    # 2026-10-08: si el prefiltro conversacional cerró la postulación, la excepción la REABRE (respuestas conservadas) y
    # el motor manda en esta misma petición la liga de la entrevista por el canal conectado.
    pconv.reabrir_por_excepcion(db, p, u)
    from .candidatos import _recalcular_resultado_apto

    _recalcular_resultado_apto(p)
    db.commit()
    await sproc.avanzar_seguro(db, p)
    db.refresh(p)
    return _salida(p)


@router.post("/postulaciones/{codigo}/pasos/{paso_id}/resultado")
async def registrar_resultado_actividad(
    codigo: str, paso_id: str,
    conclusion: str = Form(""), comentarios: str = Form(""), realizada_por: str = Form(""),
    referencias: str = Form(""),  # JSON: contactos verificados fuera del sistema (solo Referencias)
    archivos: Optional[List[UploadFile]] = File(None),
    db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual),
):
    """«Registrar resultado» de algo hecho fuera del sistema: dictamen + quién la aplicó (quién capturó = la sesión).
    Se guarda en la evaluación de la actividad, sin duplicados; tiene prioridad sobre un resultado tardío del proveedor."""
    from ..services import actividades

    p = _postulacion(db, codigo, cuenta.id)
    import json

    try:
        lista = json.loads(referencias) if referencias.strip() else None
    except ValueError:
        raise HTTPException(400, "Las referencias capturadas no tienen un formato válido.")
    r = await actividades.registrar_resultado(db, p, paso_id, u, conclusion=conclusion, comentarios=comentarios,
                                              realizada_por=realizada_por, archivos=archivos, referencias=lista)
    db.refresh(p)
    return {**r, **_salida(p)}


@router.get("/postulaciones/{codigo}/actividades/precarga")
def precarga_actividad(codigo: str, tipo: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual),
                       cuenta: Cuenta = Depends(cuenta_actual)):
    """Formulario dinámico «Agregar actividad»: lo que ya define la vacante para ese tipo (solo se pide lo faltante)."""
    from ..services import actividades

    return actividades.precarga(db, _postulacion(db, codigo, cuenta.id), tipo)


@router.post("/postulaciones/{codigo}/actividades", status_code=201)
async def agregar_actividad_configurada(
    codigo: str, datos: str = Form(...), archivos: Optional[List[UploadFile]] = File(None),
    db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual),
):
    """UNA actividad completamente configurada (o ya realizada con su resultado) en un solo paso. `datos` (JSON):
    {tipo, nombre?, obligatorio?, config{forma, evaluador, cita, instrucciones, liga_externa_candidato, proveedor,
    prueba_ids, examen, referencias{cantidad, datos[]}, iniciar_al_guardar}, ya_realizada?, resultado{conclusion,
    score, realizada_por, comentarios, referencias[]}}."""
    import json

    from ..services import actividades

    try:
        cuerpo = json.loads(datos)
    except ValueError:
        raise HTTPException(400, "Datos de la actividad inválidos.")
    p = _postulacion(db, codigo, cuenta.id)
    r = await actividades.agregar(db, p, u, cuenta, cuerpo if isinstance(cuerpo, dict) else {}, archivos)
    db.refresh(p)
    return {**r, **_salida(p)}


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
