"""Lo que el canal de mensajería le dice al CANDIDATO sobre su proceso configurable (2026-10-06).

El canal (WhatsApp o Telegram) solo entrega: QUÉ responder lo decide el proceso congelado de la postulación
(`services/proceso.estado_pasos`). Este módulo traduce el estado DERIVADO de cada paso a un mensaje para el candidato
con, cuando aplica, UNA liga de acción que ya existe en la plataforma (formulario de la vacante, sala de la Entrevista
Red Human, consentimiento médico, liga de otro sistema o proveedor, expediente de documentos).

Reglas: nunca le muestra al candidato resultados, dictámenes ni decisiones internas (solo lo que le toca hacer o que
RH está revisando — HITL); nunca mueve etapas ni marca pasos. Lo usan los deep links de Telegram
(`/start p_<token>_<paso>`) y la respuesta de seguimiento del chat cuando ya no hay prefiltro que conducir.
"""

import secrets
from typing import List, Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..fechas import con_zona, local
from ..models import TIPOS_PASO_EVALUACION, Evaluacion, Postulacion, nombre_etapa
from . import proceso as sproc

ESTADOS_ABIERTOS = ("pendiente", "en_curso")


def _respuesta(texto: str, boton: str = "", url: str = "") -> dict:
    return {"texto": texto, "boton": boton if url else "", "url": url}


def _vacante(p: Postulacion) -> str:
    return p.vacante.titulo if p.vacante else "tu postulación"


def _ultima_pregunta(p: Postulacion) -> str:
    previas = [m.texto for m in p.mensajes if m.rol == "assistant" and (m.texto or "").strip()]
    return previas[-1] if previas else ""


def _liga_entrevista_agente(p: Postulacion) -> str:
    vivas = [e for e in (p.entrevistas or []) if e.estado not in ("evaluada", "cancelada") and e.token]
    return f"{settings.app_url}/entrevista/{vivas[-1].token}" if vivas else ""


def _liga_documentos(p: Postulacion) -> str:
    e = p.expediente
    if not e:
        return ""
    if not e.token:
        e.token = secrets.token_urlsafe(24)
    return f"{settings.app_url}/expediente/{e.token}"


def _evaluacion(db: Session, p: Postulacion, codigo: Optional[str]) -> Optional[Evaluacion]:
    if not codigo:
        return None
    return db.query(Evaluacion).filter(Evaluacion.postulacion_id == p.id, Evaluacion.codigo == codigo).first()


def accion_candidato(db: Session, p: Postulacion, paso: dict) -> Optional[dict]:
    """Lo que el candidato puede hacer AHORA en un paso disponible (None = le toca a RH o a un tercero)."""
    tipo, nombre = paso["tipo"], paso["nombre"]
    if tipo == "prefiltro_whatsapp":
        if p.prefiltro_completo:
            return None
        pregunta = _ultima_pregunta(p)
        return _respuesta(f"Retomemos tu postulación a *{_vacante(p)}*." + (f"\n\n{pregunta}" if pregunta else " Escríbeme «Hola» para continuar."))
    if tipo in ("prefiltro_web", "analisis_cv"):
        v = p.vacante
        if not v or not v.slug:
            return None
        texto = "Completa el formulario de la vacante" if tipo == "prefiltro_web" else "Comparte tu CV en el formulario de la vacante"
        return _respuesta(f"{texto} *{v.titulo}*.", "Abrir formulario", f"{settings.app_url}/aplicar/{v.slug}")
    if tipo == "entrevista_agente":
        url = _liga_entrevista_agente(p)
        if url:
            return _respuesta("Tu Entrevista Red Human está lista. Ábrela cuando tengas unos minutos en un lugar tranquilo.", "Iniciar entrevista", url)
        return None
    if tipo == "documentos":
        url = _liga_documentos(p)
        if not url:
            return None
        faltan = p.expediente.pendientes if p.expediente else []
        detalle = f" Me falta: {', '.join(faltan)}." if faltan else ""
        return _respuesta(f"Sube tus documentos desde tu expediente o mándamelos por aquí en PDF o foto.{detalle}", "Subir documentos", url)
    if tipo in TIPOS_PASO_EVALUACION:
        ev = _evaluacion(db, p, paso.get("evaluacion"))
        if ev is None:
            return None
        from . import evaluaciones as sev

        sev.asegurar_ligas(ev)
        liga = next((x for x in sev.ligas_de(db, ev) if x["para"] == "Candidato" and x.get("url")), None)
        if liga and liga["clave"] == "consentimiento":
            return _respuesta(f"Para tu {nombre.lower()} necesitamos tu consentimiento expreso por escrito. Revísalo y decide en la liga.",
                              "Revisar consentimiento", liga["url"])
        if liga:
            return _respuesta(f"Tu {nombre.lower()} está lista para que la respondas.", "Abrir evaluación", liga["url"])
        if ev.cita_fecha_hora and ev.estado == "pendiente":
            cuando = local(ev.cita_fecha_hora)
            lugar = ev.cita_direccion or ev.cita_liga_videollamada or ""
            texto = f"Tu {nombre.lower()} está programada el {cuando:%d/%m/%Y} a las {con_zona(f'{cuando:%H:%M}')}." + (f" Lugar / liga: {lugar}" if lugar else "")
            return _respuesta(texto, "Abrir videollamada", ev.cita_liga_videollamada) if ev.cita_liga_videollamada else _respuesta(texto)
    return None


def _pendientes_candidato(db: Session, p: Postulacion, pasos: List[dict]) -> List[dict]:
    salida = []
    for paso in pasos:
        if paso["disponible"] and paso["estado"] in ESTADOS_ABIERTOS:
            r = accion_candidato(db, p, paso)
            if r:
                salida.append({**r, "paso": paso["id"]})
    return salida


def seguimiento(db: Session, p: Postulacion) -> Optional[dict]:
    """Respuesta general sobre el proceso: lo próximo que le toca al candidato o «RH está revisando». None sin proceso."""
    if not sproc.tiene_proceso(p):
        return None
    if not p.activa:
        return _respuesta(f"Tu proceso para *{_vacante(p)}* ya está cerrado. Gracias por tu interés.")
    pendientes = _pendientes_candidato(db, p, sproc.estado_pasos(p))
    if pendientes:
        return pendientes[0]
    return _respuesta(f"Tu proceso para *{_vacante(p)}* va en la etapa {nombre_etapa(p.etapa)}. Tu información está con el "
                      "equipo de RH; te avisaremos por aquí en cuanto haya un siguiente paso. 🙌")


def respuesta_paso(db: Session, p: Postulacion, paso_id: str) -> dict:
    """Respuesta al deep link de UN paso del proceso (`/start p_<token>_<paso>`)."""
    pasos = sproc.estado_pasos(p) if sproc.tiene_proceso(p) else []
    paso = next((x for x in pasos if x["id"] == paso_id), None)
    if paso is None:
        return seguimiento(db, p) or _respuesta(f"Seguimos con tu postulación a *{_vacante(p)}*. Te avisaremos por aquí.")
    if paso["estado"] not in ESTADOS_ABIERTOS:
        siguiente = seguimiento(db, p) or {}
        return {**siguiente, "texto": f"«{paso['nombre']}» ya quedó registrado ✅\n\n{siguiente.get('texto', '')}".strip()}
    if not paso["disponible"]:
        espera = f" {paso['espera']}." if paso["espera"] else ""
        return _respuesta(f"«{paso['nombre']}» todavía no está disponible.{espera} Te avisaremos por aquí cuando puedas hacerlo.")
    return accion_candidato(db, p, paso) or _respuesta(
        f"«{paso['nombre']}» lo coordina el equipo de RH ({paso['responsable'] or 'RH'}). Te contactaremos por aquí con los detalles.")


def ligas_telegram(p: Postulacion) -> dict:
    """Deep links de la postulación y de cada paso del proceso (para que RH los copie desde el seguimiento)."""
    from . import telegram

    if not (telegram.activo() and telegram.usuario_bot()):
        return {"disponible": False, "liga": "", "pasos": {}}
    pasos = (p.proceso or {}).get("pasos", []) if sproc.tiene_proceso(p) else []
    return {"disponible": True, "liga": telegram.liga_postulacion(p),
            "pasos": {x["id"]: telegram.liga_postulacion(p, x["id"]) for x in pasos}}


