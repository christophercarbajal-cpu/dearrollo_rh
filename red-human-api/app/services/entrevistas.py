"""
Creación del registro `Entrevista` (guion vía IA + liga pública con token) — lógica
compartida por dos caminos que la disparan por razones distintas:

  · routers/entrevistas.py `agendar()` — RH la agenda a mano desde el panel.
  · services/ia.agendar_videollamada_mock — el agente Zero-Touch, cuando el candidato
    confirma su disponibilidad por WhatsApp y hay avatar de Anam activo (ver ese archivo).

No manda WhatsApp ni hace `db.commit()`: cada camino avisa al candidato con un mensaje
distinto (uno fijo, el otro redactado por el agente), así que eso se queda con quien llama.

2026-10-09 (guiones por vacante): el guion SALE DE LA VACANTE (su plantilla de conversación de esa actividad, con las
ediciones de RH); si la vacante no lo tiene se genera Just-In-Time antes del primer mensaje (`guiones.asegurar`) — nunca
se arranca una conversación sin guion. Tres actividades de IA independientes: con avatar (sala), por WhatsApp (texto en
el chat, `turno_whatsapp`) y Llamada Red Human (voz, sala en modo llamada).
"""

import secrets
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from ..models import ETAPAS_LEGADO, Entrevista, Postulacion, registrar
from .proceso import enfoque_entrevista_agente
from . import ia
from .avatar import avatar_activo


def reabrir_entrevista(db: Session, e: Entrevista, actor: str, motivo: str = "", extra: Optional[dict] = None) -> dict:
    """Vuelve a aceptar respuestas tras un cierre. El intento anterior (transcript, evaluación,
    cierre) se archiva en `intentos_previos`, nunca se pisa; la liga (token) es la misma; la
    postulación vuelve a Entrevista IA si estaba en Evaluación. Fase 3 (2026-09-15): compartido
    entre RH (POST /entrevistas/{codigo}/reabrir) y el candidato por WhatsApp (solo interrumpida/
    parcial — ver candidatos._reanudar_entrevista_ia). No hace commit."""
    p = e.postulacion
    intento = {
        "estado": e.estado, "cierre": e.cierre, "iniciada_en": e.iniciada_en.isoformat() if e.iniciada_en else None,
        "finalizada_en": e.finalizada_en.isoformat() if e.finalizada_en else None,
        "transcript": list(e.transcript or []), "evaluacion": dict(e.evaluacion or {}),
    }
    e.intentos_previos = list(e.intentos_previos or []) + [intento]
    e.transcript = []
    e.evaluacion = {}
    e.cierre = ""
    e.motivo = ""
    e.iniciada_en = None
    e.finalizada_en = None
    e.estado = "programada"
    if p and p.etapa in ETAPAS_LEGADO:  # 2026-10-01: la columna «Evaluación» ya no existe
        p.etapa = ETAPAS_LEGADO[p.etapa]
    registrar(
        db, actor, "entrevista_reabierta", "entrevista", e.codigo,
        {"postulacion": p.codigo if p else None, "motivo": (motivo or "").strip()[:300], "intento_archivado": intento["estado"], **(extra or {})},
    )
    return intento


TIPO_DE_PASO = {"entrevista_whatsapp": "whatsapp", "llamada_agente": "llamada"}


def guion_de_vacante(db: Session, p: Postulacion, paso_tipo: str = "entrevista_agente") -> Tuple[dict, bool]:
    """Guion de la actividad tomado de la VACANTE (con las ediciones de RH). Si no existe se genera JIT y se guarda en la
    vacante. Sin vacante (postulación sin vacante todavía) se genera uno genérico, sin guardarlo."""
    from . import guiones as sgui

    v = p.vacante
    clave = sgui.SECCION_DE_PASO.get(paso_tipo, "entrevista_avatar")
    if v is not None:
        guion = sgui.asegurar(db, v, clave, motivo=f"primer mensaje de {p.codigo}")
        if not sgui.vacio(guion):
            meta = ((v.guiones or {}).get("meta") or {}).get(clave) or {}
            return {**guion, "seccion": clave}, bool(meta.get("generado_ia"))
    g, con_ia = ia.guion_entrevista(
        v.titulo if v else "vacante general", v.requisitos if v else "", p.experiencia or "",
        enfoque_entrevista=enfoque_entrevista_agente(p, v, paso_tipo), perfil_ideal=(v.perfil_ideal if v else "") or "",
        responsabilidades=list(v.responsabilidades or []) if v else [],
    )
    return g.model_dump(), con_ia


def refrescar_guion(e: Entrevista) -> None:
    """Antes del PRIMER turno se toma la versión vigente del guion de la vacante: lo que RH editó después de mandar la
    liga también aplica. Ya iniciada, la entrevista conserva el guion con el que empezó (auditoría)."""
    from . import guiones as sgui

    seccion = (e.guion or {}).get("seccion")
    v = e.postulacion.vacante if e.postulacion is not None else None
    if not seccion or v is None or e.transcript or e.estado not in ("programada",):
        return
    actual = sgui.contenido_de(v, seccion)
    if not sgui.vacio(actual) and {k: actual.get(k) for k in ("enfoque", "temas", "preguntas")} != {
            k: (e.guion or {}).get(k) for k in ("enfoque", "temas", "preguntas")}:
        e.guion = {**actual, "seccion": seccion}


def crear_entrevista_para_candidato(
    db: Session,
    p: Postulacion,
    actor: str,
    programada_para: Optional[datetime] = None,
    paso_tipo: str = "entrevista_agente",
) -> Tuple[Entrevista, bool]:
    """Crea la `Entrevista` (con su token) de la actividad `paso_tipo` para la postulación `p`, con el guion de su
    vacante (JIT si falta). `actor` firma la bitácora — el nombre de RH en el camino manual, "agente-ia" en Zero-Touch,
    igual que el resto de acciones automáticas del agente."""
    guion, con_ia = guion_de_vacante(db, p, paso_tipo)
    e = Entrevista(
        codigo="TMP",
        candidato_id=p.candidato_id,
        token=secrets.token_urlsafe(24),
        tipo=TIPO_DE_PASO.get(paso_tipo) or ("avatar" if avatar_activo() else "texto"),
        guion=guion,
        programada_para=programada_para,
    )
    p.entrevistas.append(e)
    db.add(e)
    db.flush()
    e.codigo = f"ENT-{300 + e.id}"
    if p.etapa == "Prefiltro":
        p.etapa = "Entrevista IA"  # ver ETAPAS_CANDIDATO — la entrevista con avatar también es "IA"
    registrar(db, actor, "entrevista_agendada", "entrevista", e.codigo, {"candidato": p.candidato.codigo, "postulacion": p.codigo, "ia": con_ia,
                                                                        "tipo": e.tipo, "guion": (e.guion or {}).get("seccion") or "generico"})
    return e, con_ia


# ============================================================ Entrevista Red Human por WhatsApp (2026-10-09)

_AFIRMA = ("si", "sí", "listo", "lista", "adelante", "vamos", "claro", "ok", "va", "dale", "comencemos", "empecemos", "comenzar",
           "empezar", "acepto", "de acuerdo")
_NIEGA = ("no", "luego", "despues", "después", "mas tarde", "más tarde", "ahorita no", "ahora no", "mañana", "manana")


def entrevista_whatsapp_activa(p: Postulacion) -> Optional[Entrevista]:
    """La entrevista por WhatsApp que conduce el chat: solo mientras la postulación sigue en Filtro Red Human (si RH
    la movió u omitió la actividad, el chat ya no le pertenece)."""
    if not p.activa or p.etapa != "Entrevista IA":
        return None
    return next((e for e in reversed(list(p.entrevistas or [])) if e.tipo == "whatsapp" and e.estado in ("programada", "en_curso")), None)


def system_prompt(e: Entrevista) -> str:
    """El MISMO prompt del entrevistador para las tres actividades, con el guion de la vacante y su canal."""
    from ..serial import nombre_empresa_candidato
    from ..routers.entrevistas import _nombre_entrevistado as nombre_para_entrevista

    p = e.postulacion
    v = p.vacante if p else None
    guion = e.guion or {}
    return ia.prompt_entrevistador(
        v.titulo if v else "vacante general", v.requisitos if v else "", nombre_para_entrevista(e),
        list(guion.get("preguntas") or []), empresa=nombre_empresa_candidato(v) if v else "la empresa",
        temas=ia.temas_de_guion(guion), enfoque=guion.get("enfoque", ""),
        enfoque_entrevista=enfoque_entrevista_agente(p, v, _paso_de(e)), ubicacion=(v.ubicacion if v else "") or "",
        modalidad=(v.modalidad if v else "") or "", sueldo=(v.sueldo if v else "") or "",
        beneficios=list(v.beneficios or []) if v else [], area=(v.area if v else "") or "",
        canal=e.tipo, guion_de_vacante=bool(guion.get("seccion")),
    )


def _paso_de(e: Entrevista) -> str:
    from ..models import paso_de_entrevista

    return paso_de_entrevista(e.tipo)


def _norm(t: str) -> str:
    import unicodedata

    return " ".join(unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower().split())


async def iniciar_whatsapp(db: Session, p: Postulacion, actor: str, paso_id: str = "") -> Tuple[bool, str, Optional[bool], Entrevista]:
    """Crea (o reutiliza) la entrevista por WhatsApp y manda la presentación: el aviso de que la conduce la IA y
    «¿Comenzamos?». La respuesta afirmativa del candidato es su consentimiento para ESTA entrevista."""
    from .motor_ruta import _avisar

    e = entrevista_whatsapp_activa(p)
    if e is None:
        e, _ = crear_entrevista_para_candidato(db, p, actor, paso_tipo="entrevista_whatsapp")
        db.flush()
    if not p.telefono:
        return False, "El candidato no tiene teléfono: la entrevista por WhatsApp no se puede iniciar.", None, e
    texto = ia.mensaje_inicial_entrevista_whatsapp(p.vacante.titulo if p.vacante else "")
    e.ultima_actividad_en = datetime.now(timezone.utc)
    entregado, detalle = await _avisar(db, p, texto, "Tu entrevista con Red Human", "", motivo="entrevista", paso_id=paso_id, actor=actor)
    return True, (f"Entrevista iniciada por {detalle}" if entregado else f"Entrevista creada; el mensaje no salió ({detalle})"), entregado, e


async def turno_whatsapp(db: Session, p: Postulacion, e: Entrevista, texto: str, canal: str) -> dict:
    """Un turno de la Entrevista Red Human por WhatsApp (el mensaje del candidato ya está guardado en el chat). Mismo
    prompt, mismo cierre verificado y misma evaluación que la sala (`finalizar_entrevista`)."""
    from ..routers.candidatos import _actualizar_ultima_actividad, _enviar_whatsapp, guardar_mensaje
    from ..routers.entrevistas import finalizar_entrevista

    ahora = datetime.now(timezone.utc)

    async def responder(mensaje: str, **extra) -> dict:
        envio = await _enviar_whatsapp(p, mensaje, canal)
        guardar_mensaje(db, p, "assistant", mensaje, canal, envio)
        _actualizar_ultima_actividad(p)
        e.ultima_actividad_en = ahora
        db.commit()
        return {"respuesta": mensaje, "clasificacion": None, "ia": False, "whatsapp": envio, "entrevista": e.codigo, **extra}

    t = _norm(texto)
    if e.estado == "programada":
        palabras = set(t.split())
        afirma = bool(palabras & {_norm(x) for x in _AFIRMA if " " not in x}) or any(x in t for x in ("de acuerdo", "estoy listo", "estoy lista"))
        niega = t in {_norm(x) for x in _NIEGA} or t.startswith(("no ", "ahorita no", "ahora no", "mas tarde", "luego"))
        if niega or not afirma:
            if niega:
                return await responder("Sin problema. Cuando quieras comenzar tu entrevista, escríbeme «Comenzar». 🙂", esperando=True)
            return await responder("Para comenzar tu entrevista con Red Human respóndeme «Sí» cuando estés listo(a). 🙂", esperando=True)
        refrescar_guion(e)
        e.consentimiento = True
        e.consentimiento_fecha = ahora
        e.estado = "en_curso"
        e.iniciada_en = ahora
        intro = ia.mensaje_inicial_entrevista_whatsapp(p.vacante.titulo if p.vacante else "")
        e.transcript = [{"rol": "assistant", "texto": intro}]
        registrar(db, p.candidato.codigo if p.candidato else "candidato", "consentimiento_entrevista", "entrevista", e.codigo,
                  {"canal": canal, "respuesta": texto[:120]})
    historial = list(e.transcript or []) + [{"rol": "user", "texto": texto[:2000]}]
    turno, con_ia = ia.entrevista_turno(system_prompt(e), historial, preguntas=list((e.guion or {}).get("preguntas") or []))
    e.transcript = historial + [{"rol": "assistant", "texto": turno.respuesta}]
    terminada = turno.terminada or ia.DESPEDIDA_ENTREVISTA.lower() in (turno.respuesta or "").lower()
    salida = await responder(turno.respuesta, ia_entrevista=con_ia)
    if terminada:
        db.refresh(e)
        await finalizar_entrevista(db, e, None, "texto")
        salida["terminada"] = True
    return salida
