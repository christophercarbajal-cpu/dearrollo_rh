"""Avisos de estado al candidato (2026-10-10, Cambio 3).

* UN solo mensaje al completar una actividad, que dice cuál es el «Siguiente paso». Nunca felicita ni promete avance ni
  revela un resultado: solo informa en qué va su proceso. Máximo UN aviso de estado por día y por postulación (los
  mensajes de ACCIÓN — ligas, citas, documentos, propuesta — no cuentan ni se limitan).
* Fin de la selección (la postulación llega a Contratación): mensaje neutral; la actividad «Propuesta y aceptación» queda
  «Pendiente: capturar condiciones».
* Inactividad (job cada hora): a los 3 días HÁBILES sin movimiento, «Tu proceso para [puesto] en [empresa] sigue activo.
  Estamos esperando: [siguiente paso].» — una vez por periodo sin movimiento.

El estado previo vive en `Postulacion.analisis["avisos_estado"]` = {completadas[], etapa, ultimo_aviso_en,
inactividad_ref, inactividad_en}. La PRIMERA vez que se ve una postulación solo se toma la foto (nunca se avisa de golpe
a candidatos que ya estaban en proceso). Una postulación con el motor pausado (candidatos sembrados de la demo) nunca
recibe avisos.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import fechas
from ..models import Postulacion, registrar

CLAVE = "avisos_estado"
DIAS_HABILES_INACTIVIDAD = 3
ACTOR = "Red Human (avisos de estado)"

# Actividades cuyo cierre se le avisa al candidato (en las demás el siguiente mensaje ya es la acción misma: el
# prefiltro sigue en el chat, la liga de documentos, la propuesta, la firma…).
TIPOS_AVISO = ("entrevista_agente", "entrevista_whatsapp", "llamada_agente", "entrevista_humana", "medica", "tecnica",
               "psicometrica", "socioeconomica", "referencias", "documentos", "solicitud_documentos", "otra")

HECHO = {
    "entrevista_agente": "tu entrevista con Red Human", "entrevista_whatsapp": "tu entrevista con Red Human",
    "llamada_agente": "tu llamada con Red Human", "entrevista_humana": "tu entrevista", "medica": "tu evaluación médica",
    "tecnica": "tu evaluación técnica", "psicometrica": "tu evaluación psicométrica", "socioeconomica": "tu estudio socioeconómico",
    "referencias": "tus referencias laborales", "documentos": "tus documentos", "solicitud_documentos": "tus documentos",
}
SIGUIENTE = {
    "solicitud_web": "la revisión de tu solicitud", "analisis_cv": "la revisión de tu solicitud", "prefiltro_web": "unas preguntas sobre la vacante",
    "prefiltro_whatsapp": "unas preguntas sobre la vacante por este chat", "entrevista_agente": "tu entrevista con Red Human",
    "entrevista_whatsapp": "tu entrevista con Red Human por este chat", "llamada_agente": "tu llamada con Red Human",
    "entrevista_humana": "una entrevista con el equipo; te escribiremos para agendarla",
    "medica": "tu evaluación médica; te escribiremos para agendarla", "tecnica": "tu evaluación técnica; te escribiremos para agendarla",
    "psicometrica": "tu evaluación psicométrica", "socioeconomica": "tu estudio socioeconómico", "referencias": "tus referencias laborales",
    "documentos": "tus documentos de ingreso", "solicitud_documentos": "tus documentos", "condiciones": "la propuesta de trabajo, que te enviaremos por aquí",
    "carta_intencion": "tu carta de intención", "carta_contrato": "la firma de tu contrato", "induccion": "tu capacitación",
    "onboarding": "tus actividades de ingreso", "alta": "la confirmación de tu ingreso",
}
# «Estamos esperando: …» (inactividad): quién tiene la pelota según la actividad que sigue
ESPERANDO_CANDIDATO = {
    "prefiltro_whatsapp": "tus respuestas a las preguntas de la vacante", "prefiltro_web": "tus respuestas a las preguntas de la vacante",
    "entrevista_agente": "que realices tu entrevista con Red Human", "entrevista_whatsapp": "que realices tu entrevista con Red Human",
    "llamada_agente": "que realices tu llamada con Red Human", "psicometrica": "que completes tu evaluación psicométrica",
    "referencias": "tus referencias laborales", "documentos": "tus documentos de ingreso", "solicitud_documentos": "tus documentos",
    "carta_contrato": "la firma de tu contrato", "carta_intencion": "la firma de tu carta de intención",
}


def _estado(p: Postulacion) -> Optional[dict]:
    v = (p.analisis or {}).get(CLAVE)
    return dict(v) if isinstance(v, dict) else None


def _guardar(p: Postulacion, estado: dict) -> None:
    a = dict(p.analisis or {})
    a[CLAVE] = estado
    p.analisis = a
    flag_modified(p, "analisis")


def _pausado(p: Postulacion) -> bool:
    return bool(((p.analisis or {}).get("motor_ruta") or {}).get("pausado"))


def _hoy_local() -> str:
    return fechas.local(datetime.now(timezone.utc)).date().isoformat()


def _primer_nombre(p: Postulacion) -> str:
    n = (p.nombre or "").strip()
    return n.split(" ")[0] if n and not n.startswith("Candidato") else ""


def _empresa(p: Postulacion) -> str:
    from ..serial import nombre_empresa_candidato

    return nombre_empresa_candidato(p.vacante) if p.vacante else ""


def texto_siguiente(x: Optional[dict]) -> str:
    if not x:
        return ""
    return SIGUIENTE.get(x.get("tipo"), (x.get("nombre") or "").lower() or "la revisión del equipo de RH")


def texto_esperando(p: Postulacion, x: Optional[dict]) -> str:
    if not x:
        return "la revisión del equipo de RH"
    if x.get("tipo") == "condiciones":
        prop = (p.analisis or {}).get("propuesta") or {}
        return "tu respuesta a la propuesta de trabajo" if prop.get("estado") == "enviada" else "la propuesta de trabajo del equipo de RH"
    if x.get("tipo") in ESPERANDO_CANDIDATO and x.get("estadoUnificado") not in ("pendiente_revision", "pendiente_resultado", "pendiente_agendar"):
        return ESPERANDO_CANDIDATO[x["tipo"]]
    return "la revisión del equipo de RH"


def _siguiente_paso(p: Postulacion, pasos: List[dict]) -> Optional[dict]:
    """La siguiente actividad pendiente (misma regla que la tarjeta del tablero), con su tipo."""
    from . import proceso as sproc

    sig = sproc.siguiente_actividad(p, pasos=pasos)
    if not sig:
        return None
    return next((x for x in pasos if x["id"] == sig["id"]), None)


def texto_actividad_completada(p: Postulacion, hecho: str, siguiente: str) -> str:
    nombre = _primer_nombre(p)
    puesto = p.vacante.titulo if p.vacante else "la vacante"
    return f"Hola{(' ' + nombre) if nombre else ''}, registramos {hecho} para {puesto}. Siguiente paso: {siguiente}."


def texto_fin_seleccion(p: Postulacion) -> str:
    nombre = _primer_nombre(p)
    puesto = p.vacante.titulo if p.vacante else "la vacante"
    return (f"Hola{(' ' + nombre) if nombre else ''}, terminó la etapa de selección de tu proceso para {puesto}. "
            "Siguiente paso: el equipo de RH preparará la propuesta de trabajo y te la enviará por aquí.")


def texto_inactividad(p: Postulacion, esperando: str) -> str:
    puesto = p.vacante.titulo if p.vacante else "la vacante"
    empresa = _empresa(p) or "la empresa"
    return f"Tu proceso para {puesto} en {empresa} sigue activo. Estamos esperando: {esperando}."


async def _enviar(db: Session, p: Postulacion, texto: str, asunto: str, motivo: str) -> bool:
    from .motor_ruta import _avisar

    entregado, detalle = await _avisar(db, p, texto, asunto, "", motivo=motivo, actor=ACTOR)
    registrar(db, ACTOR, f"aviso_{motivo}", "postulacion", p.codigo, {"texto": texto[:300], "entregado": entregado, "detalle": detalle[:200]})
    return entregado


async def revisar(db: Session, p: Optional[Postulacion]) -> Optional[str]:
    """Tras cualquier evento de la ruta: si se COMPLETÓ una actividad (o terminó la selección) manda UN aviso de estado
    con el siguiente paso, máximo uno por día. Nunca truena. Regresa el texto enviado (o None)."""
    if p is None or not p.activa or _pausado(p):
        return None
    try:
        from . import proceso as sproc

        if not sproc.tiene_proceso(p):
            return None
        pasos = [x for x in sproc.estado_pasos(p) if not x.get("heredado")]
        completadas = sorted(x["id"] for x in pasos if x["estado"] == "completada")
        previo = _estado(p)
        if previo is None:
            _guardar(p, {"completadas": completadas, "etapa": p.etapa, "inactividad_ref": fechas.iso(datetime.now(timezone.utc))})
            return None
        nuevas = [x for x in pasos if x["id"] in set(completadas) - set(previo.get("completadas") or [])]
        entro_contratacion = p.etapa == "Contratación" and previo.get("etapa") not in ("Contratación", "Onboarding")
        estado = {**previo, "completadas": completadas, "etapa": p.etapa}
        if nuevas or p.etapa != previo.get("etapa"):
            estado["inactividad_ref"] = fechas.iso(datetime.now(timezone.utc))  # hubo movimiento
        texto, motivo = None, ""
        if entro_contratacion:
            texto, motivo = texto_fin_seleccion(p), "fin_seleccion"
        else:
            avisables = [x for x in nuevas if x["tipo"] in TIPOS_AVISO and x.get("cumpleRegla") and x.get("resultado") != "no_favorable"]
            sig = _siguiente_paso(p, pasos) if avisables else None
            if avisables and sig is not None:
                hecho = HECHO.get(avisables[-1]["tipo"], f"«{avisables[-1]['nombre']}»")
                texto, motivo = texto_actividad_completada(p, hecho, texto_siguiente(sig)), "estado"
        if texto and estado.get("ultimo_aviso_dia") != _hoy_local() and (p.telefono or p.correo):
            estado["ultimo_aviso_dia"] = _hoy_local()
            _guardar(p, estado)
            await _enviar(db, p, texto, f"Tu proceso — {p.vacante.titulo if p.vacante else 'vacante'}", motivo)
            db.commit()
            return texto
        _guardar(p, estado)
        return None
    except Exception as ex:  # noqa: BLE001 — un aviso nunca rompe la acción que lo disparó
        print(f"[avisos_estado] {getattr(p, 'codigo', '?')}: {ex}", flush=True)
        return None


# ------------------------------------------------------------ inactividad


def dias_habiles_entre(desde: datetime, hasta: datetime) -> int:
    """Días hábiles (lunes a viernes, hora de la organización) completos entre dos instantes."""
    a = fechas.local(desde).date()
    b = fechas.local(hasta).date()
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def ultimo_movimiento(p: Postulacion) -> datetime:
    candidatos = [x for x in (p.ultima_actividad_en, p.etapa_desde, getattr(p, "creado_en", None)) if x is not None]
    ref = (_estado(p) or {}).get("inactividad_ref")
    if ref:
        try:
            candidatos.append(datetime.fromisoformat(ref))
        except ValueError:
            pass
    candidatos = [fechas.a_utc(x) for x in candidatos]
    return max(candidatos) if candidatos else datetime.now(timezone.utc)


async def revisar_inactividad_postulacion(db: Session, p: Postulacion, ahora: Optional[datetime] = None) -> Optional[str]:
    """3 días hábiles sin movimiento → UN aviso (se reclama antes de enviar; no se repite hasta que haya movimiento)."""
    ahora = ahora or datetime.now(timezone.utc)
    if not p.activa or _pausado(p) or not (p.telefono or p.correo) or p.etapa == "Onboarding":
        return None
    from . import proceso as sproc

    if not sproc.tiene_proceso(p):
        return None
    mov = ultimo_movimiento(p)
    if dias_habiles_entre(mov, ahora) < DIAS_HABILES_INACTIVIDAD:
        return None
    estado = _estado(p) or {}
    if estado.get("inactividad_de") == fechas.iso(mov):
        return None  # ya se avisó por este periodo sin movimiento
    pasos = [x for x in sproc.estado_pasos(p) if not x.get("heredado")]
    texto = texto_inactividad(p, texto_esperando(p, _siguiente_paso(p, pasos)))
    estado.update({"inactividad_de": fechas.iso(mov), "inactividad_en": fechas.iso(ahora)})
    _guardar(p, estado)
    db.commit()  # se reclama ANTES de enviar: un reintento del job nunca lo duplica
    await _enviar(db, p, texto, f"Tu proceso sigue activo — {p.vacante.titulo if p.vacante else 'vacante'}", "inactividad")
    db.commit()
    return texto


async def revisar_inactividad() -> dict:
    """Job (cada hora): cada postulación activa en su propia sesión."""
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        ids = [x for (x,) in db.query(Postulacion.id).filter(Postulacion.activa.is_(True)).all()]
    finally:
        db.close()
    resumen = {"revisadas": 0, "avisadas": 0, "errores": 0}
    for pid in ids:
        s = SessionLocal()
        try:
            p = s.get(Postulacion, pid)
            if p is not None and await revisar_inactividad_postulacion(s, p):
                resumen["avisadas"] += 1
        except Exception as ex:  # noqa: BLE001
            s.rollback()
            resumen["errores"] += 1
            print(f"[avisos_estado] inactividad {pid}: {ex}", flush=True)
        finally:
            s.close()
        resumen["revisadas"] += 1
    return resumen
