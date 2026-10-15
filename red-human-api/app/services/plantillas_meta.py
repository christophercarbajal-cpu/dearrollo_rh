"""Plantillas oficiales de Meta que ABREN flujos de WhatsApp (2026-10-13, Cambios 28-29).

- `inicio_entrevista_rh` ({{1}} nombre del candidato): sale UNA vez cuando la actividad «Prefiltro por WhatsApp» de la
  ruta se habilita y el candidato todavía no ha escrito (`revisar_inicio_prefiltro`, desde `/candidatos/postular` y
  `proceso.avanzar_seguro`). La web no cuenta como «el candidato escribió primero»: solo una plantilla aprobada pasa.
- La respuesta del candidato a esa plantilla («Sí, empezar ahora» o cualquier confirmación) abre la ventana de 24 h
  (`marcar_respuesta`) y el bot contesta EN ESE MISMO TURNO con el saludo estricto y la primera pregunta del prefiltro
  (base fija en Masivos — Cambio 29 — o el prefiltro configurable en Corporativos — Cambio 19). No pide «/start».
- `alerta_entrevista_asig` (8 variables) al entrevistador: `notificaciones.parametros_plantilla_entrevista`.
- `aviso_proceso_rh` (SIN variables en Meta): cuando el candidato debe confirmar un horario (`enviar_aviso_agenda`).
Un envío fallido nunca bloquea el flujo; todo queda en la bitácora.
"""

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..config import settings
from ..models import Postulacion, registrar

CLAVE = "plantilla_inicio"
VENTANA_HORAS = 24
ACTOR = "Red Human (WhatsApp)"
_RE_CONFIRMA = re.compile(r"^(si|sii+|sip|claro|va|vale|dale|ok|okay|listo|lista|adelante|vamos|empecemos|comencemos|empezar|"
                          r"comenzar|de acuerdo|por supuesto|perfecto|sale|iniciar|continuar)\b")


def _n(t: str) -> str:
    return " ".join(unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower().split()).strip(" .!¡¿?")


def nombre_plantilla_inicio() -> str:
    return (settings.meta_plantilla_inicio or "inicio_entrevista_rh").strip()


def es_confirmacion_inicio(texto: str) -> bool:
    """«Sí, empezar ahora» (botón de la plantilla) o cualquier confirmación libre."""
    t = _n(texto)
    return bool(t) and (t.startswith("si, empezar") or t.startswith("si empezar") or bool(_RE_CONFIRMA.match(t)))


_RE_APLAZA = re.compile(r"^(no|ahorita no|ahora no|por ahora no|en este momento no|despues|mas tarde|luego|al rato|mañana|manana|"
                        r"no puedo|no tengo tiempo|estoy ocupad[oa])\b")


def es_aplazamiento(texto: str) -> bool:
    """Cambio 37: respuesta negativa a «¿Tienes 5 minutos para responder unas preguntas?»."""
    return bool(_RE_APLAZA.match(_n(texto)))


# Cambio 37: «Hola, [nombre]. Soy Red Human, el agente virtual de [empresa]. Recibimos tu postulación para [puesto]. ¿Tienes
# 5 minutos para responder unas preguntas?» — las variables de la plantilla en Meta van en el orden de
# META_PLANTILLA_INICIO_PARAMS (default «nombre,empresa,puesto»).
TEXTO_INICIO = ("Hola, {nombre}. Soy Red Human, el agente virtual de {empresa}. Recibimos tu postulación para {puesto}. ¿Tienes 5 "
                "minutos para responder unas preguntas?")


def valores_inicio(p: Postulacion) -> dict:
    from ..routers.candidatos import nombre_ficha
    from ..serial import nombre_empresa_candidato

    nombre = (nombre_ficha(p) or p.nombre or "").split(" ")[0]
    v = p.vacante
    return {"nombre": nombre if nombre and not nombre.startswith("Candidato") else "candidato(a)",
            "empresa": (nombre_empresa_candidato(v) if v is not None else "") or "Red Human",
            "puesto": (v.titulo if v is not None else "") or "la vacante"}


def parametros_inicio(p: Postulacion) -> list:
    valores = valores_inicio(p)
    claves = [k.strip().lower() for k in (settings.meta_plantilla_inicio_params or "nombre,empresa,puesto").split(",") if k.strip()]
    return [valores.get(k) or "-" for k in claves if k in valores]


def texto_inicio(p: Postulacion) -> str:
    return TEXTO_INICIO.format(**valores_inicio(p))


def estado(p: Postulacion) -> dict:
    return dict(((p.analisis or {}).get(CLAVE)) or {})


def _guardar(p: Postulacion, e: dict) -> None:
    a = dict(p.analisis or {})
    a[CLAVE] = e
    p.analisis = a
    flag_modified(p, "analisis")


def _ya_conversa(p: Postulacion) -> bool:
    """El candidato ya escribió (llegó por el chat o ya contestó algo): no hace falta abrir con plantilla."""
    from . import prefiltro_conversacional as pconv

    if (p.origen or "") in ("whatsapp", "telegram") or any(m.rol == "user" for m in p.mensajes or []):
        return True
    e = pconv.estado(p)
    # Cambio 50: las respuestas de «Unas preguntas» (fuente web) no son conversación por el chat
    por_chat = any(isinstance(r, dict) and r.get("fuente") != "web" for r in (e.get("respuestas") or {}).values())
    return bool(e.get("pendiente") or por_chat or e.get("resultado"))


def prefiltro_habilitado(p: Postulacion) -> bool:
    """La actividad «Prefiltro por WhatsApp» de la ruta está disponible y sin terminar."""
    from . import proceso as sproc

    if sproc.paso_de_tipo(p, "prefiltro_whatsapp") is None:
        return False
    x = next((y for y in sproc.estado_pasos(p) if y["tipo"] == "prefiltro_whatsapp" and not y["heredado"]), None)
    return bool(x and x["disponible"] and x["estado"] in ("pendiente", "en_curso"))


async def revisar_inicio_prefiltro(db: Session, p: Optional[Postulacion]) -> Optional[dict]:
    """Manda `inicio_entrevista_rh` UNA vez al habilitarse el prefiltro por WhatsApp. Reclama antes de enviar."""
    if p is None or not p.activa or not p.consentimiento or not p.telefono or p.vacante_id is None:
        return None
    if estado(p).get("enviada_en") or _ya_conversa(p) or not prefiltro_habilitado(p):
        return None
    from ..routers.candidatos import _disparar_plantilla_inicio
    from .whatsapp import numero_e164

    destino = numero_e164(p.telefono)
    if not (len(destino) == 12 and destino.startswith("52")):
        # número que Meta no puede entregar (no es 52 + 10 dígitos): se registra y no se llama a la API
        _guardar(p, {"error": f"Teléfono inválido para WhatsApp: «{p.telefono}» → «{destino}»", "plantilla": nombre_plantilla_inicio()})
        registrar(db, ACTOR, "plantilla_inicio_fallida", "postulacion", p.codigo, {"motivo": "telefono_invalido", "destino": destino})
        db.commit()
        print(f"[whatsapp][HSM] {p.codigo}: teléfono inválido para WhatsApp ({p.telefono!r} → {destino!r}); no se manda la plantilla", flush=True)
        return {"enviado": False, "detalle": "teléfono inválido"}
    _guardar(p, {"enviada_en": datetime.now(timezone.utc).isoformat(), "plantilla": nombre_plantilla_inicio()})
    db.commit()  # reclamada ANTES de llamar a Meta: nunca sale dos veces
    try:
        envio = await _disparar_plantilla_inicio(db, p)
    except Exception as ex:  # noqa: BLE001 — un error de Meta nunca rompe el flujo que la disparó
        db.rollback()
        envio = {"enviado": False, "detalle": f"{type(ex).__name__}: {str(ex)[:300]}"}
        registrar(db, ACTOR, "plantilla_inicio_fallida", "postulacion", p.codigo, {"motivo": "excepcion", "detalle": envio["detalle"]})
    if envio.get("enviado"):
        marcar_entregada(db, p, envio)
    else:
        e = estado(p)
        e["entregada"] = False
        if envio.get("estado") == "pendiente":
            # Cambio 48 (cascada del Cambio 46): la plantilla aún no está activa en Meta → «Pendiente de plantilla», nunca
            # fallido; `reintento_plantillas` la vuelve a intentar mientras el candidato no haya escrito
            e["pendiente_plantilla"] = True
            e["error"] = str(envio.get("error") or "")[:300]
        else:
            e["error"] = str(envio.get("error") or envio.get("detalle") or "")[:300]
        print(f"[whatsapp][HSM] {p.codigo}: «{nombre_plantilla_inicio()}» a {destino} NO salió — {e['error']}", flush=True)
        _guardar(p, e)
    if not envio.get("enviado"):
        from .reintento_plantillas import registrar_envio

        registrar_envio(db, p, "inicio_prefiltro", envio, "inicio_prefiltro")
    db.commit()
    return envio


def marcar_entregada(db: Session, p: Postulacion, envio: dict) -> None:
    """La plantilla de inicio SALIÓ (al primer intento o al reintentar): la conversación queda LIGADA a la vacante de la
    plantilla (su respuesta es de esta postulación) y el bot ya se presentó (Cambio 37). No hace commit."""
    from ..routers.candidatos import fijar_conversacion
    from .prefiltro_conversacional import marcar_presentado

    e = estado(p)
    e["entregada"] = True
    e.pop("pendiente_plantilla", None)
    e.pop("error", None)
    e.setdefault("enviada_en", datetime.now(timezone.utc).isoformat())
    if envio.get("formato"):
        e["formato"] = envio["formato"]
    _guardar(p, e)
    fijar_conversacion(p)
    marcar_presentado(p)


async def inicio_en_segundo_plano(postulacion_id: int) -> None:
    """Envío DESACOPLADO de `inicio_entrevista_rh` (después de responder `/candidatos/postular`): sesión propia y nunca
    lanza. La postulación ya está guardada; si Meta falla, queda en `analisis.plantilla_inicio.error` y en el log."""
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        p = db.get(Postulacion, postulacion_id)
        await revisar_inicio_prefiltro(db, p)
    except Exception as ex:  # noqa: BLE001
        db.rollback()
        print(f"[whatsapp][HSM] postulación {postulacion_id}: error al mandar la plantilla de inicio — {type(ex).__name__}: {ex}", flush=True)
    finally:
        db.close()


def marcar_respuesta(db: Session, p: Postulacion, texto: str) -> bool:
    """El candidato contestó a `inicio_entrevista_rh`: abre la ventana de 24 h (queda registrada) y regresa True si es
    la PRIMERA respuesta a la plantilla (el bot contesta con el saludo y la primera pregunta). No hace commit."""
    e = estado(p)
    if not e.get("enviada_en") or e.get("respondida_en"):
        return False
    ahora = datetime.now(timezone.utc)
    e.update({"respondida_en": ahora.isoformat(), "ventana_hasta": (ahora + timedelta(hours=VENTANA_HORAS)).isoformat(),
              "respuesta": str(texto or "")[:120], "confirmo": es_confirmacion_inicio(texto)})
    _guardar(p, e)
    registrar(db, ACTOR, "plantilla_inicio_respondida", "postulacion", p.codigo,
              {"plantilla": e.get("plantilla"), "respuesta": str(texto or "")[:120], "ventana_hasta": e["ventana_hasta"]})
    return True


# ============================================================ aviso de agenda

def parametros_agenda(p: Postulacion) -> list:
    """Variables de `aviso_proceso_rh`: la plantilla aprobada NO tiene variables (META_PLANTILLA_AGENDA_PARAMS vacío → []).
    Solo si se configura otra versión con variables se llenan en ese orden (nombre, vacante, empresa)."""
    from ..routers.candidatos import nombre_ficha
    from ..serial import nombre_empresa_candidato

    v = p.vacante
    nombre = nombre_ficha(p) or ""
    valores = {"nombre": "" if nombre.startswith("Candidato") else nombre.split(" ")[0],
               "vacante": (v.titulo if v else "") or "", "empresa": (nombre_empresa_candidato(v) if v else "") or ""}
    claves = [k.strip().lower() for k in (settings.meta_plantilla_agenda_params or "").split(",") if k.strip()]
    return [valores.get(k) or "-" for k in claves if k in valores]


async def enviar_aviso_agenda(db: Session, p: Postulacion, texto: str) -> dict:
    """`aviso_proceso_rh`: el candidato debe confirmar un horario. Sale la plantilla (sirve fuera de la ventana de
    24 h); después, quien llama manda la pregunta exacta como texto. Nunca lanza."""
    from .mensajeria import de_cuenta
    from .whatsapp import enviar_plantilla

    plantilla = (settings.meta_plantilla_agenda or "").strip()
    if not plantilla or not p.telefono:
        return {"enviado": False, "detalle": "sin plantilla de agenda o sin teléfono"}
    try:
        with de_cuenta(p.cuenta_id):
            envio = await enviar_plantilla(p.telefono, plantilla, parametros_agenda(p), settings.meta_plantilla_idioma, texto=texto)
    except Exception as ex:  # noqa: BLE001 — un aviso fallido nunca frena la agenda
        envio = {"enviado": False, "detalle": str(ex)[:200]}
    registrar(db, ACTOR, "plantilla_agenda_enviada", "postulacion", p.codigo,
              {"plantilla": plantilla, "enviado": bool(envio.get("enviado")), "detalle": str(envio.get("detalle") or "")[:200]})
    return {**envio, "plantilla": plantilla}
