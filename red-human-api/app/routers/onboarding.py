"""Onboarding v2 (2026-09-28) — Fase 1: Plantillas de Onboarding (Configuración) y tareas por persona.

* Plantilla = documentos requeridos, recursos internos (correo, equipo, accesos), responsables por defecto,
  curso de inducción y plazos RELATIVOS a la fecha de ingreso. Alcance «empresa» (toda la Cuenta o una razón
  social contratante) o «puesto». La de puesto prevalece sobre la de empresa (`services.onboarding`).
* «Eliminar» una plantilla = desactivarla; los Onboardings ya generados con ella no se tocan (se copiaron).
* Tareas: pendiente → realizada | cancelada (con motivo). Las tres fijas (Contrato firmado, Alta IMSS /
  nómina, Confirmar ingreso) son obligatorias y no se cancelan una por una; «Contrato firmado» solo se
  cierra al cargar el contrato firmado.
* Fase 2: «Iniciar Onboarding» (`POST /expedientes/{id}/iniciar`) es la ÚNICA ruta que lleva de Contratación a
  Onboarding (vía `candidatos.aplicar_movimiento`); el resto de las rutas de aquí nunca escribe la etapa (B5).
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import cuenta_actual, usuario_actual, usuario_decisor
from ..models import (
    ALCANCES_PLANTILLA_ONBOARDING,
    DOCUMENTOS_BASE,
    ESTADOS_DOCUMENTO_ONBOARDING,
    PLAZOS_ONBOARDING_DEFAULT,
    TAREAS_FIJAS_ONBOARDING,
    TIPOS_RECURSO_ONBOARDING,
    Candidato,
    Cuenta,
    Curso,
    Expediente,
    PlantillaOnboarding,
    TareaOnboarding,
    Usuario,
    Vacante,
    registrar,
)
from ..serial import plantilla_onboarding_dict, tarea_onboarding_dict
from ..services import onboarding as onb
from ..services.modulos_rh import requiere_modulos_rh

router = APIRouter(prefix="/onboarding", tags=["onboarding"], dependencies=[Depends(requiere_modulos_rh)])


def _plantilla(db: Session, pid: int, cuenta_id: int) -> PlantillaOnboarding:
    p = db.query(PlantillaOnboarding).filter(PlantillaOnboarding.id == pid, PlantillaOnboarding.cuenta_id == cuenta_id).first()
    if not p:
        raise HTTPException(404, "Plantilla de Onboarding no encontrada.")
    return p


def _curso_titulo(db: Session, curso_id: Optional[int], cuenta_id: int) -> str:
    if not curso_id:
        return ""
    c = db.query(Curso).filter(Curso.id == curso_id, Curso.cuenta_id == cuenta_id).first()
    return c.titulo if c else ""


def _dict(db: Session, p: PlantillaOnboarding) -> dict:
    return plantilla_onboarding_dict(p, _curso_titulo(db, p.curso_induccion_id, p.cuenta_id))


def _expediente(db: Session, exp_id: int, cuenta_id: int) -> Expediente:
    e = (
        db.query(Expediente)
        .join(Candidato, Expediente.candidato_id == Candidato.id)
        .filter(Expediente.id == exp_id, Candidato.cuenta_id == cuenta_id)
        .first()
    )
    if not e:
        raise HTTPException(404, "Expediente no encontrado.")
    return e


def _tarea(db: Session, tid: int, cuenta_id: int) -> TareaOnboarding:
    t = db.query(TareaOnboarding).filter(TareaOnboarding.id == tid, TareaOnboarding.cuenta_id == cuenta_id).first()
    if not t:
        raise HTTPException(404, "Tarea de Onboarding no encontrada.")
    return t


# ---------- Plantillas (Configuración) ----------

class PlantillaOnboardingIn(BaseModel):
    nombre: str
    alcance: str = "empresa"
    empresa: str = ""
    puesto: str = ""
    documentos: Optional[List[dict]] = None  # None = los predeterminados
    recursos: List[dict] = []
    responsables: dict = {}
    plazos: dict = {}
    curso_induccion_id: Optional[int] = None


class EditarPlantillaOnboardingIn(BaseModel):
    nombre: Optional[str] = None
    alcance: Optional[str] = None
    empresa: Optional[str] = None
    puesto: Optional[str] = None
    documentos: Optional[List[dict]] = None
    recursos: Optional[List[dict]] = None
    responsables: Optional[dict] = None
    plazos: Optional[dict] = None
    curso_induccion_id: Optional[int] = None
    quitar_curso: bool = False
    activa: Optional[bool] = None


def _validar(db: Session, cuenta: Cuenta, p: PlantillaOnboarding) -> None:
    """Reglas comunes a crear y editar (se llama con los valores ya aplicados, antes del commit)."""
    from .cuentas import razones_sociales_de

    if not (p.nombre or "").strip():
        raise HTTPException(400, "El nombre de la plantilla es obligatorio.")
    if p.alcance not in ALCANCES_PLANTILLA_ONBOARDING:
        raise HTTPException(400, "Alcance inválido: usa «empresa» o «puesto».")
    if p.alcance == "puesto" and not (p.puesto or "").strip():
        raise HTTPException(400, "Una plantilla de puesto necesita el nombre del puesto.")
    if p.alcance == "empresa":
        p.puesto = ""
    if p.empresa:
        razones = {r["razonSocial"].lower(): r["razonSocial"] for r in razones_sociales_de(db, cuenta)}
        if p.empresa.strip().lower() not in razones:
            raise HTTPException(400, "La empresa debe ser una razón social configurada en la Cuenta o en un Cliente activo.")
        p.empresa = razones[p.empresa.strip().lower()]
    if not p.documentos:
        raise HTTPException(400, "La plantilla necesita al menos un documento requerido.")
    if p.curso_induccion_id and not _curso_titulo(db, p.curso_induccion_id, cuenta.id):
        raise HTTPException(400, "El curso de inducción no existe en esta Cuenta.")
    if p.activa:
        dup = (
            db.query(PlantillaOnboarding)
            .filter(PlantillaOnboarding.cuenta_id == cuenta.id, PlantillaOnboarding.activa.is_(True), PlantillaOnboarding.alcance == p.alcance)
            .all()
        )
        for o in dup:
            if o.id != p.id and onb.norm(o.puesto) == onb.norm(p.puesto) and onb.norm(o.empresa) == onb.norm(p.empresa):
                quien = f"el puesto «{p.puesto}»" if p.alcance == "puesto" else "la empresa"
                raise HTTPException(409, f"Ya existe una plantilla activa para {quien}{' en ' + p.empresa if p.empresa else ''}: «{o.nombre}». Edítala o desactívala.")


@router.get("/plantillas")
def listar_plantillas(incluir_inactivas: bool = False, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    q = db.query(PlantillaOnboarding).filter(PlantillaOnboarding.cuenta_id == cuenta.id)
    if not incluir_inactivas:
        q = q.filter(PlantillaOnboarding.activa.is_(True))
    # puesto primero (prevalece), luego empresa
    lista = sorted(q.all(), key=lambda p: (0 if p.alcance == "puesto" else 1, onb.norm(p.puesto), onb.norm(p.nombre)))
    return [_dict(db, p) for p in lista]


@router.get("/plantillas/opciones")
def opciones(db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Catálogos para el formulario: razones sociales, puestos sugeridos, cursos y valores predeterminados."""
    from .cuentas import razones_sociales_de

    puestos = sorted({
        (v.titulo or "").strip()
        for v in db.query(Vacante).filter(Vacante.cuenta_id == cuenta.id, Vacante.estado != "Eliminada").all()
        if (v.titulo or "").strip()
    }, key=onb.norm)
    cursos = db.query(Curso).filter(Curso.cuenta_id == cuenta.id, Curso.estado != "Archivado").order_by(Curso.titulo).all()
    return {
        "razonesSociales": [r["razonSocial"] for r in razones_sociales_de(db, cuenta)],
        "puestos": puestos,
        "cursos": [{"id": c.id, "codigo": c.codigo, "titulo": c.titulo, "estado": c.estado} for c in cursos],
        "documentosBase": list(DOCUMENTOS_BASE),
        "tareasFijas": [{"clave": c, "nombre": n} for c, n in TAREAS_FIJAS_ONBOARDING],
        "tiposRecurso": list(TIPOS_RECURSO_ONBOARDING),
        "plazosDefault": dict(PLAZOS_ONBOARDING_DEFAULT),
        "estadosDocumento": list(ESTADOS_DOCUMENTO_ONBOARDING),
    }


@router.get("/plantillas/resolver")
def resolver(puesto: str = "", empresa: str = "", db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Qué configuración le tocaría a una persona con ese puesto y empresa (vista previa; no guarda nada)."""
    cfg = onb.configuracion_para(db, cuenta.id, puesto, empresa)
    cfg["cursoInduccion"] = _curso_titulo(db, cfg.get("cursoInduccionId"), cuenta.id)
    return cfg


@router.post("/plantillas", status_code=201)
def crear_plantilla(datos: PlantillaOnboardingIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    docs = onb.normalizar_documentos(datos.documentos) if datos.documentos is not None else onb.configuracion_predeterminada()["documentos"]
    p = PlantillaOnboarding(
        cuenta_id=cuenta.id, nombre=(datos.nombre or "").strip()[:200], alcance=(datos.alcance or "").strip().lower(),
        empresa=(datos.empresa or "").strip(), puesto=(datos.puesto or "").strip()[:200], documentos=docs,
        recursos=onb.normalizar_recursos(datos.recursos), responsables=onb.normalizar_responsables(datos.responsables),
        plazos=onb.normalizar_plazos(datos.plazos), curso_induccion_id=datos.curso_induccion_id or None, activa=True, creado_por=u.nombre,
    )
    _validar(db, cuenta, p)
    db.add(p)
    db.flush()
    registrar(db, u.nombre, "plantilla_onboarding_creada", "onboarding", str(p.id),
              {"nombre": p.nombre, "alcance": p.alcance, "puesto": p.puesto, "empresa": p.empresa, "correo_rh": u.correo})
    db.commit()
    return _dict(db, p)


@router.get("/plantillas/{pid}")
def ver_plantilla(pid: int, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    return _dict(db, _plantilla(db, pid, cuenta.id))


@router.patch("/plantillas/{pid}")
def editar_plantilla(pid: int, datos: EditarPlantillaOnboardingIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Editar la plantilla NO toca los Onboardings ya generados (se copiaron al aplicarla)."""
    p = _plantilla(db, pid, cuenta.id)
    if datos.nombre is not None:
        p.nombre = datos.nombre.strip()[:200]
    if datos.alcance is not None:
        p.alcance = datos.alcance.strip().lower()
    if datos.empresa is not None:
        p.empresa = datos.empresa.strip()
    if datos.puesto is not None:
        p.puesto = datos.puesto.strip()[:200]
    if datos.documentos is not None:
        p.documentos = onb.normalizar_documentos(datos.documentos)
    if datos.recursos is not None:
        p.recursos = onb.normalizar_recursos(datos.recursos)
    if datos.responsables is not None:
        p.responsables = onb.normalizar_responsables(datos.responsables)
    if datos.plazos is not None:
        p.plazos = onb.normalizar_plazos(datos.plazos)
    if datos.quitar_curso:
        p.curso_induccion_id = None
    elif datos.curso_induccion_id is not None:
        p.curso_induccion_id = datos.curso_induccion_id or None
    if datos.activa is not None:
        p.activa = bool(datos.activa)
    try:
        _validar(db, cuenta, p)
    except HTTPException:
        db.rollback()
        raise
    registrar(db, u.nombre, "plantilla_onboarding_editada", "onboarding", str(p.id), {"nombre": p.nombre, "correo_rh": u.correo})
    db.commit()
    return _dict(db, p)


@router.delete("/plantillas/{pid}")
def eliminar_plantilla(pid: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Baja lógica: la plantilla deja de aplicarse; los Onboardings ya generados no se tocan."""
    p = _plantilla(db, pid, cuenta.id)
    p.activa = False
    registrar(db, u.nombre, "plantilla_onboarding_desactivada", "onboarding", str(p.id), {"nombre": p.nombre, "correo_rh": u.correo})
    db.commit()
    return _dict(db, p)


# ---------- Tareas de una persona ----------

@router.get("/expedientes/{exp_id}/tareas")
def listar_tareas(exp_id: int, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    e = _expediente(db, exp_id, cuenta.id)
    return [tarea_onboarding_dict(t) for t in onb.tareas_de(db, e)]


class TareaIn(BaseModel):
    nombre: str
    tipo: str = "otro"
    responsable: str = ""
    dias: Optional[int] = None  # relativo a la fecha de ingreso
    obligatoria: bool = True


@router.post("/expedientes/{exp_id}/tareas", status_code=201)
def agregar_tarea(exp_id: int, datos: TareaIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Tarea adicional SOLO para esta persona (no altera la plantilla)."""
    e = _expediente(db, exp_id, cuenta.id)
    nombre = (datos.nombre or "").strip()[:200]
    if not nombre:
        raise HTTPException(400, "Indica el nombre de la tarea.")
    if any(onb.norm(t.nombre) == onb.norm(nombre) and t.estado != "cancelada" for t in onb.tareas_de(db, e)):
        raise HTTPException(409, f"El Onboarding ya tiene la tarea «{nombre}».")
    tipo = datos.tipo if datos.tipo in TIPOS_RECURSO_ONBOARDING else "otro"
    t = TareaOnboarding(
        cuenta_id=cuenta.id, expediente_id=e.id, clave="otra", nombre=nombre, tipo=tipo, fija=False, obligatoria=datos.obligatoria,
        responsable=datos.responsable.strip()[:150], dias_relativos=datos.dias, fecha_limite=onb.fecha_limite(e.fecha_ingreso, datos.dias), creada_por=u.nombre,
    )
    db.add(t)
    db.flush()
    registrar(db, u.nombre, "tarea_onboarding_agregada", "expediente", str(e.id), {"tarea": nombre, "correo_rh": u.correo})
    db.commit()
    return tarea_onboarding_dict(t)


class EditarTareaIn(BaseModel):
    estado: Optional[str] = None  # pendiente | realizada | cancelada
    motivo: str = ""
    responsable: Optional[str] = None
    notas: Optional[str] = None
    fecha_limite: Optional[datetime] = None


@router.patch("/tareas/{tid}")
def editar_tarea(tid: int, datos: EditarTareaIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    t = _tarea(db, tid, cuenta.id)
    e = db.get(Expediente, t.expediente_id)
    if e and e.estado == "alta" and datos.estado and datos.estado != t.estado:
        # tras el alta solo se permite cerrar lo pendiente, nunca reabrir
        if datos.estado == "pendiente":
            raise HTTPException(409, "El colaborador ya fue dado de alta; las tareas cerradas no se reabren.")
    anterior = t.estado
    if datos.estado is not None:
        error = onb.cambiar_estado_tarea(t, datos.estado, datos.motivo, u.nombre)
        if error:
            codigo = 400 if ("motivo" in error.lower() or "inválido" in error) else 409
            raise HTTPException(codigo, error)
    if datos.responsable is not None:
        t.responsable = datos.responsable.strip()[:150]
    if datos.notas is not None:
        t.notas = datos.notas.strip()[:2000]
    if datos.fecha_limite is not None:
        t.fecha_limite = datos.fecha_limite
        t.dias_relativos = None  # fecha fija capturada por RH: ya no se recalcula con la de ingreso
    if e:
        onb.sincronizar_legado(db, e)  # las tareas son la fuente de verdad
    registrar(db, u.nombre, "tarea_onboarding_actualizada", "expediente", str(t.expediente_id),
              {"tarea": t.nombre, "de": anterior, "a": t.estado, "motivo": t.motivo_cancelacion[:300], "correo_rh": u.correo})
    db.commit()
    return tarea_onboarding_dict(t)


# ---------- Fase 2: de Contratación a Onboarding ----------

def _usuarios_cuenta(db: Session, cuenta_id: int) -> List[Usuario]:
    from ..models import UsuarioCuenta

    return (
        db.query(Usuario)
        .join(UsuarioCuenta, UsuarioCuenta.usuario_id == Usuario.id)
        .filter(UsuarioCuenta.cuenta_id == cuenta_id, Usuario.activo.is_(True))
        .order_by(Usuario.nombre)
        .all()
    )


def _usuario_de(usuarios: List[Usuario], responsable: str) -> Optional[Usuario]:
    r = onb.norm(responsable)
    return next((x for x in usuarios if r and (onb.norm(x.nombre) == r or onb.norm(x.correo) == r)), None)


@router.get("/expedientes/{exp_id}/resumen")
def resumen_inicio(exp_id: int, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Pantalla de resumen de «Enviar a Onboarding»: requisitos (condiciones + consentimiento), la configuración
    PRECARGADA de la plantilla que aplica (puesto > empresa > predeterminada) y los documentos que ya tiene el
    expediente. No guarda nada."""
    from ..services.configuracion import modo_prueba_activo

    e = _expediente(db, exp_id, cuenta.id)
    cfg = onb.configuracion_para(db, cuenta.id, e.puesto, e.empresa)
    cfg["cursoInduccion"] = _curso_titulo(db, cfg.get("cursoInduccionId"), cuenta.id)
    req = onb.requisitos_inicio(e)
    prueba = modo_prueba_activo(db)
    p = e.postulacion
    cursos = db.query(Curso).filter(Curso.cuenta_id == cuenta.id, Curso.estado != "Archivado").order_by(Curso.titulo).all()
    return {
        "expedienteId": e.id,
        "etapa": p.etapa if p else "",
        "requisitos": req,
        "modoPrueba": prueba,
        "puedeIniciar": (req["completos"] or prueba) and e.estado != "alta",
        "iniciado": onb.onboarding_iniciado(db, e),
        "configuracion": cfg,
        "documentosExpediente": [
            {"tipo": d.tipo, "obligatorio": d.obligatorio, "estado": d.estado, "tieneArchivo": bool(d.archivo)}
            for d in e.documentos if not d.interno
        ],
        "usuarios": [{"id": x.id, "nombre": x.nombre, "correo": x.correo} for x in _usuarios_cuenta(db, cuenta.id)],
        "cursos": [{"id": c.id, "titulo": c.titulo} for c in cursos],
        # Evaluaciones (2026-09-28): la vacante pidió «Avisar antes de Onboarding» → aviso (nunca bloquea)
        "avisosEvaluaciones": _avisos_evaluaciones(db, p),
    }


def _avisos_evaluaciones(db: Session, p) -> List[str]:
    if not p:
        return []
    try:
        from ..models import EvaluacionCandidato
        from ..services import evaluaciones as sev

        evs = db.query(EvaluacionCandidato).filter(EvaluacionCandidato.postulacion_id == p.id).all()
        return sev.avisos_antes_onboarding(p, evs)
    except Exception:  # noqa: BLE001
        db.rollback()
        return []


class IniciarOnboardingIn(BaseModel):
    """Selección para ESTA persona (precargada de la plantilla; «Cambiar selección» nunca altera la plantilla)."""
    documentos: List[dict]
    recursos: List[dict] = []
    responsables: dict = {}
    plazos: dict = {}
    curso_induccion_id: Optional[int] = None
    plantilla_id: Optional[int] = None
    solicitar_documentos: bool = True
    notificar_responsables: bool = True


@router.post("/expedientes/{exp_id}/iniciar")
async def iniciar_onboarding(exp_id: int, datos: IniciarOnboardingIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Iniciar Onboarding»: ÚNICO gatillo del paso Contratación → Onboarding (decisión del usuario). Aplica la
    selección de documentos, genera las tareas (tres fijas + recursos), mueve la etapa con el movimiento de
    siempre (`candidatos.aplicar_movimiento`), hace la primera solicitud de documentos al candidato y avisa a
    los responsables internos. Un aviso caído nunca bloquea: el resultado de cada envío viaja a RH."""
    from ..services.configuracion import modo_prueba_activo
    from . import candidatos as rcand

    e = _expediente(db, exp_id, cuenta.id)
    p = e.postulacion
    if not p:
        raise HTTPException(409, "El expediente no está ligado a una postulación.")
    if e.estado == "alta":
        raise HTTPException(409, "El colaborador ya fue dado de alta.")
    if p.etapa not in ("Contratación", "Onboarding"):
        raise HTTPException(409, "«Iniciar Onboarding» es para postulaciones en Contratación.")
    if onb.onboarding_iniciado(db, e) and p.etapa == "Onboarding":
        raise HTTPException(409, "El Onboarding de esta persona ya se inició.")
    prueba = modo_prueba_activo(db)
    req = onb.requisitos_inicio(e)
    if not req["completos"] and not prueba:
        raise HTTPException(409, f"Antes de enviar a Onboarding completa: {', '.join(req['faltan'])}.")
    if not onb.normalizar_documentos(datos.documentos):
        raise HTTPException(400, "Selecciona al menos un documento.")
    if datos.curso_induccion_id and not _curso_titulo(db, datos.curso_induccion_id, cuenta.id):
        raise HTTPException(400, "El curso de inducción no existe en esta Cuenta.")

    config = {
        "documentos": datos.documentos,
        "recursos": onb.normalizar_recursos(datos.recursos),
        "responsables": onb.normalizar_responsables(datos.responsables),
        "plazos": onb.normalizar_plazos(datos.plazos),
    }
    agregados, no_aplica, conservados = onb.aplicar_seleccion_documentos(db, e, datos.documentos, u.nombre)
    tareas = onb.generar_tareas(db, e, cuenta.id, config, u.nombre)
    onb.sincronizar_legado(db, e)
    if datos.plantilla_id:
        e.plantilla_onboarding_id = datos.plantilla_id
    registrar(db, u.nombre, "onboarding_iniciado", "expediente", str(e.id), {
        "postulacion": p.codigo, "plantilla": datos.plantilla_id, "documentos_agregados": agregados, "documentos_no_aplica": no_aplica,
        "tareas": [t.nombre for t in tareas], "curso_induccion": datos.curso_induccion_id,
        "modo_prueba": prueba and not req["completos"], "correo_rh": u.correo,
    })
    if p.etapa != "Onboarding":
        await rcand.aplicar_movimiento(db, p, rcand.EtapaIn(etapa="Onboarding", comentario="Iniciar Onboarding"), u, desde_iniciar=True)
    else:
        db.commit()

    solicitud: List[dict] = []
    if datos.solicitar_documentos and any(not d.aprobado for d in e.obligatorios):
        try:
            r = await rcand._disparar_mensaje_onboarding(db, p, "solicitud_documentos", "documentos_solicitados", rcand._liga_documentos(p), u)
            solicitud = r.get("resultados") or []
        except Exception as ex:  # noqa: BLE001
            solicitud = [{"destinatario": "Candidato", "canal": "whatsapp/correo", "destino": "", "enviado": False, "detalle": str(ex)[:200]}]

    avisos: List[dict] = []
    if datos.notificar_responsables:
        avisos = await _avisar_responsables(db, e, cuenta, onb.tareas_de(db, e))

    curso = None
    if datos.curso_induccion_id:
        curso = await _asignar_induccion(db, p, datos.curso_induccion_id, cuenta, u)
    db.commit()
    from ..serial import postulacion_dict

    return {
        "candidato": postulacion_dict(p, detalle=True),
        "tareas": [tarea_onboarding_dict(t) for t in onb.tareas_de(db, e)],
        "documentosAgregados": agregados,
        "documentosNoAplica": no_aplica,
        "documentosConservados": conservados,
        "solicitudDocumentos": solicitud,
        "avisosResponsables": avisos,
        "cursoInduccion": curso,
    }


async def _avisar_responsables(
    db: Session, e: Expediente, cuenta: Cuenta, tareas: List[TareaOnboarding], titulo: str = "", parrafo: str = "", solo_pendientes: bool = True,
) -> List[dict]:
    """Un correo por responsable interno con SUS tareas y plazos. El responsable se reconoce si su nombre o
    correo coincide con un Usuario de la Cuenta; si no, se reporta sin enviar (nunca silencioso)."""
    from ..services import plantillas_correo
    from ..services.correo import enviar_correo

    usuarios = _usuarios_cuenta(db, cuenta.id)
    nombre = e.candidato.nombre if e.candidato else "la persona"
    salida = []
    grupos = onb.tareas_por_responsable(tareas) if solo_pendientes else {}
    if not solo_pendientes:
        for t in tareas:
            if (t.responsable or "").strip():
                grupos.setdefault(t.responsable.strip(), []).append(t)
    for responsable, suyas in grupos.items():
        us = _usuario_de(usuarios, responsable)
        if not us or not us.correo:
            salida.append({"destinatario": responsable, "canal": "correo", "destino": "", "enviado": False,
                           "detalle": "No coincide con un usuario de la Cuenta con correo; avísale por otro medio."})
            continue
        filas = []
        for t in suyas:
            # fecha_limite es un DÍA de calendario (medianoche UTC): se lee tal cual, sin convertir de zona
            # (convertirla a México la corría al día anterior).
            filas.append((t.nombre, t.fecha_limite.strftime("%d/%m/%Y") if t.fecha_limite else "Sin fecha"))
        try:
            asunto, html = plantillas_correo.html_aviso(
                titulo or f"Onboarding de {nombre}: tienes tareas asignadas",
                parrafo or f"{nombre} ingresa como {e.puesto or 'nuevo colaborador'}. Estas tareas del Onboarding están a tu cargo:",
                cuenta.nombre_visible, filas,
            )
            r = await enviar_correo(us.correo, asunto, html)
        except Exception as ex:  # noqa: BLE001
            r = {"enviado": False, "detalle": str(ex)[:200]}
        salida.append({"destinatario": us.nombre, "canal": "correo", "destino": us.correo, "enviado": bool(r.get("enviado")), "detalle": str(r.get("detalle") or "")})
    registrar(db, "sistema", "onboarding_responsables_avisados", "expediente", str(e.id), {"avisos": salida})
    return salida


async def _asignar_induccion(db: Session, p, curso_id: int, cuenta: Cuenta, u: Usuario) -> Optional[dict]:
    """Asigna el curso de inducción a la persona (mismo mecanismo de Capacitación). Nunca bloquea el inicio."""
    try:
        from . import capacitacion as rcap

        curso = db.query(Curso).filter(Curso.id == curso_id, Curso.cuenta_id == cuenta.id).first()
        if not curso:
            return None
        a = await rcap.asignar_a_postulacion(db, p, curso, actor=u.nombre)
        return {"curso": curso.titulo, "asignacion": a.codigo}
    except Exception as ex:  # noqa: BLE001
        return {"curso": "", "error": str(ex)[:200]}


@router.post("/expedientes/{exp_id}/contrato-firmado")
async def cargar_contrato_firmado(
    exp_id: int, archivo: UploadFile = File(...), db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """«Cargar contrato firmado manualmente»: guarda el PDF FINAL firmado como documento INTERNO del expediente
    (no suma al porcentaje) con quién y cuándo, y SOLO así la tarea fija «Contrato firmado» queda Realizada.
    Reemplazarlo conserva la trazabilidad en bitácora."""
    from ..models import TIPO_CONTRATO_FIRMADO, Documento
    from ..services import archivos as fs

    e = _expediente(db, exp_id, cuenta.id)
    if e.estado == "alta":
        raise HTTPException(409, "El colaborador ya fue dado de alta; el expediente no admite cambios.")
    tarea = next((t for t in onb.tareas_de(db, e) if t.fija and t.clave == "contrato_firmado"), None)
    if not tarea:
        raise HTTPException(409, "Primero inicia el Onboarding («Enviar a Onboarding»).")
    validado = await fs.validar(archivo, "contrato firmado")
    if validado.extension != "pdf":
        raise HTTPException(400, "El contrato firmado debe ser un PDF.")
    reemplazo = onb.contrato_ya_firmado(e)
    ahora = datetime.now(timezone.utc)
    doc = onb.guardar_documento_firmado(db, e, TIPO_CONTRATO_FIRMADO, validado.contenido, validado.nombre, u.nombre, canal="rh")
    doc.notas_ia = f"Cargado manualmente por {u.nombre}."
    tarea.notas = f"Contrato firmado cargado por {u.nombre}."
    registrar(db, u.nombre, "contrato_firmado_cargado", "expediente", str(e.id),
              {"archivo": validado.nombre, "reemplazo": reemplazo, "correo_rh": u.correo})
    db.commit()
    return {
        "tarea": tarea_onboarding_dict(tarea),
        "documento": {"tipo": doc.tipo, "archivo": doc.nombre_archivo, "cargadoPor": u.nombre, "cargadoEn": ahora.isoformat()},
    }


@router.get("/expedientes/{exp_id}/contrato-firmado")
def descargar_contrato_firmado(exp_id: int, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    from fastapi.responses import FileResponse

    from ..models import TIPO_CONTRATO_FIRMADO
    from ..services import archivos as fs

    e = _expediente(db, exp_id, cuenta.id)
    doc = next((d for d in e.documentos if d.interno and d.tipo == TIPO_CONTRATO_FIRMADO), None)
    if not doc or not fs.existe(doc.archivo):
        raise HTTPException(404, "Todavía no se carga el contrato firmado.")
    return FileResponse(doc.archivo, media_type="application/pdf", filename=doc.nombre_archivo or "contrato-firmado.pdf")


# ---------- Fase 3: gestión activa, alta y cierre ----------

def _dia_mx(texto: str, campo: str) -> datetime:
    """«AAAA-MM-DD» capturado en México → mismo criterio que `fecha_ingreso` (medianoche UTC de ese día)."""
    try:
        return datetime.fromisoformat((texto or "")[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(400, f"{campo} inválida (usa AAAA-MM-DD).")


def _historial(p, evento: str, texto: str, u: Usuario, **extra) -> None:
    """Deja el hecho en el historial del candidato (solo se AGREGA)."""
    if p is None:
        return
    p.historial = list(p.historial or []) + [
        {"evento": evento, "texto": texto, "usuario": u.nombre, "fecha": datetime.now(timezone.utc).isoformat(), **extra}
    ]


@router.get("/expedientes/{exp_id}/estado")
def estado_onboarding(exp_id: int, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Estado completo para el tablero: documentos aprobados vs faltantes, «No aplica» aparte con motivo,
    tareas realizadas/pendientes/atrasadas, canceladas aparte con motivo, ingreso, alta y cierre."""
    e = _expediente(db, exp_id, cuenta.id)
    tareas = onb.tareas_de(db, e)
    return {**onb.resumen_tablero(e, tareas), "listaTareas": [tarea_onboarding_dict(t) for t in tareas]}


@router.post("/expedientes/{exp_id}/generar-tareas")
async def generar_tareas_legado(exp_id: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Botón «Generar tareas de Onboarding» para quien YA estaba en Onboarding antes de Onboarding v2: crea las
    tareas desde la plantilla que aplica (puesto > empresa > predeterminada) y agrega los documentos que falten,
    SIN mover la etapa, sin pedir requisitos y sin volver a solicitar documentos. Avisa a los responsables."""
    e = _expediente(db, exp_id, cuenta.id)
    p = e.postulacion
    if e.estado == "alta" or e.no_ingreso_en or e.onboarding_cerrado_en:
        raise HTTPException(409, "Este Onboarding ya terminó; no se generan tareas.")
    if onb.onboarding_iniciado(db, e):
        raise HTTPException(409, "Este Onboarding ya tiene tareas.")
    if not p or p.etapa != "Onboarding":
        raise HTTPException(409, "Para quien está en Contratación usa «Enviar a Onboarding».")
    cfg = onb.configuracion_para(db, cuenta.id, e.puesto, e.empresa)
    agregados = onb.aplicar_documentos(db, e, cfg["documentos"])
    tareas = onb.generar_tareas(db, e, cuenta.id, cfg, u.nombre)
    onb.sincronizar_legado(db, e)
    e.plantilla_onboarding_id = cfg.get("plantillaId")
    registrar(db, u.nombre, "onboarding_tareas_generadas", "expediente", str(e.id),
              {"legado": True, "plantilla": cfg.get("plantilla") or cfg["origen"], "tareas": [t.nombre for t in tareas], "documentos_agregados": agregados, "correo_rh": u.correo})
    db.commit()
    avisos = await _avisar_responsables(db, e, cuenta, tareas)
    db.commit()
    return {"tareas": [tarea_onboarding_dict(t) for t in tareas], "documentosAgregados": agregados, "avisosResponsables": avisos,
            "plantilla": cfg.get("plantilla") or "", "origen": cfg["origen"]}


class ConfirmarIngresoIn(BaseModel):
    fecha_real: str  # AAAA-MM-DD


@router.post("/expedientes/{exp_id}/confirmar-ingreso")
def confirmar_ingreso(exp_id: int, datos: ConfirmarIngresoIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Confirmar ingreso»: registra la fecha REAL de llegada (quién y cuándo), cierra la tarea fija y recalcula los
    plazos pendientes contra esa fecha. Es lo que habilita «Dar de alta», sin importar la fecha prevista."""
    from ..services.notificaciones import TZ_MEXICO

    e = _expediente(db, exp_id, cuenta.id)
    if e.estado == "alta":
        raise HTTPException(409, "El colaborador ya fue dado de alta.")
    if e.no_ingreso_en:
        raise HTTPException(409, "Esta persona quedó como «No ingresó».")
    tarea = next((t for t in onb.tareas_de(db, e) if t.clave == "confirmar_ingreso"), None)
    if not tarea:
        raise HTTPException(409, "Primero genera las tareas del Onboarding.")
    real = _dia_mx(datos.fecha_real, "fecha_real")
    if real.date() > datetime.now(TZ_MEXICO).date():
        raise HTTPException(400, "La fecha real de ingreso no puede ser futura: confírmala el día que la persona llegue.")
    ahora = datetime.now(timezone.utc)
    e.fecha_ingreso_real, e.ingreso_confirmado_por, e.ingreso_confirmado_en = real, u.nombre, ahora
    tarea.estado, tarea.realizada_por, tarea.realizada_en = "realizada", u.nombre, ahora
    tarea.cancelada_por, tarea.cancelada_en, tarea.motivo_cancelacion = "", None, ""
    tarea.notas = f"Ingreso real: {real.date().isoformat()}."
    recalculadas = onb.recalcular_fechas(db, e)
    prevista = e.fecha_ingreso.date().isoformat() if e.fecha_ingreso else None
    _historial(e.postulacion, "ingreso_confirmado", f"Ingreso confirmado por {u.nombre}: llegó el {real.date().isoformat()}"
               + (f" (prevista {prevista})" if prevista and prevista != real.date().isoformat() else ""), u)
    registrar(db, u.nombre, "ingreso_confirmado", "expediente", str(e.id),
              {"fecha_real": real.date().isoformat(), "fecha_prevista": prevista, "plazos_recalculados": recalculadas, "correo_rh": u.correo})
    db.commit()
    return {**onb.resumen_tablero(e, onb.tareas_de(db, e)), "listaTareas": [tarea_onboarding_dict(t) for t in onb.tareas_de(db, e)], "plazosRecalculados": recalculadas}


@router.post("/expedientes/{exp_id}/cerrar")
def cerrar_onboarding(exp_id: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Cerrar Onboarding»: acción MANUAL (nunca automática). Solo con el alta hecha, todas las tareas Realizadas o
    Canceladas (con motivo) y todos los documentos Aprobados o «No aplica» (con motivo). Modo Prueba se la salta."""
    from ..services.configuracion import modo_prueba_activo

    e = _expediente(db, exp_id, cuenta.id)
    if e.onboarding_cerrado_en:
        raise HTTPException(409, f"El Onboarding ya se cerró ({e.onboarding_cerrado_por}).")
    if e.estado != "alta":
        raise HTTPException(409, "Primero da de alta a la persona; el Onboarding se cierra después del alta.")
    tareas = onb.tareas_de(db, e)
    faltan = onb.pendientes_cierre(e, tareas)
    prueba = modo_prueba_activo(db)
    if faltan and not prueba:
        raise HTTPException(409, "Aún no se puede cerrar el Onboarding: " + "; ".join(faltan[:8]) + ("…" if len(faltan) > 8 else "") + ".")
    e.onboarding_cerrado_en, e.onboarding_cerrado_por = datetime.now(timezone.utc), u.nombre
    _historial(e.postulacion, "onboarding_cerrado", f"Onboarding cerrado por {u.nombre}", u)
    registrar(db, u.nombre, "onboarding_cerrado", "expediente", str(e.id), {"modo_prueba": bool(faltan and prueba), "pendientes": faltan, "correo_rh": u.correo})
    db.commit()
    return onb.resumen_tablero(e, tareas)


class NoIngresoIn(BaseModel):
    motivo: str = ""


@router.post("/expedientes/{exp_id}/no-ingreso")
async def no_ingreso(exp_id: int, datos: NoIngresoIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«No ingresó» (solo antes del alta): cancela las tareas abiertas (también las fijas), detiene los
    recordatorios, avisa a los responsables, cierra la postulación y deja el hecho en el historial del candidato."""
    e = _expediente(db, exp_id, cuenta.id)
    if e.estado == "alta":
        raise HTTPException(409, "La persona ya fue dada de alta; usa la baja del colaborador.")
    if e.no_ingreso_en:
        raise HTTPException(409, "Ya se registró que esta persona no ingresó.")
    motivo = (datos.motivo or "").strip()
    if not motivo:
        raise HTTPException(400, "Indica el motivo por el que la persona no ingresó.")
    ahora = datetime.now(timezone.utc)
    tareas = onb.tareas_de(db, e)
    abiertas = [t for t in tareas if t.estado == "pendiente"]
    for t in abiertas:
        t.estado, t.cancelada_por, t.cancelada_en = "cancelada", u.nombre, ahora
        t.motivo_cancelacion = f"No ingresó: {motivo}"[:1000]
    e.no_ingreso_en, e.no_ingreso_por, e.no_ingreso_motivo = ahora, u.nombre, motivo[:2000]
    e.documentos_hasta = None  # sin recordatorios automáticos
    onb.sincronizar_legado(db, e)
    p = e.postulacion
    _historial(p, "no_ingreso", f"No ingresó — registrado por {u.nombre}: {motivo}", u, motivo=motivo[:500])
    if p is not None and p.activa:
        p.cerrar("no_ingreso")
    registrar(db, u.nombre, "onboarding_no_ingreso", "expediente", str(e.id),
              {"motivo": motivo[:500], "tareas_canceladas": [t.nombre for t in abiertas], "postulacion": p.codigo if p else None, "correo_rh": u.correo})
    db.commit()
    nombre = e.candidato.nombre if e.candidato else "La persona"
    avisos = await _avisar_responsables(
        db, e, cuenta, abiertas, titulo=f"Onboarding cancelado: {nombre} no ingresó",
        parrafo=f"{nombre} no ingresó como {e.puesto or 'colaborador'}. Motivo: {motivo}. Estas tareas a tu cargo quedaron canceladas:",
        solo_pendientes=False,
    )
    db.commit()
    return {**onb.resumen_tablero(e, tareas), "listaTareas": [tarea_onboarding_dict(t) for t in tareas], "avisosResponsables": avisos}
