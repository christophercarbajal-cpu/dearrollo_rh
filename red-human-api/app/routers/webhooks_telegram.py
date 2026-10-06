"""Webhook de Telegram (2026-10-06) — `POST /api/webhooks/telegram` (alias `/webhooks/telegram`).

Solo canal: valida `X-Telegram-Bot-Api-Secret-Token`, deduplica `update_id` en la base (`updates_telegram`), contesta
200 de inmediato y procesa en segundo plano. El Update se normaliza (`telegram.parsear_update`) al formato de
`whatsapp.parsear_webhook` y entra por `webhooks.procesar_entrante` (la MISMA lógica de WhatsApp, que consulta el
proceso configurable de la postulación). Lo único propio de Telegram:

- Identidad: sin teléfono verificado el bot pide «Compartir mi número» (`request_contact`); solo se acepta el contacto
  PROPIO. chat ↔ teléfono a 10 dígitos queda en `chats_telegram`; la plataforma sigue identificando por teléfono.
- Deep links `/start <payload>`: `vac_<VAC-####>` = elegir esa vacante; `p_<token>` = la postulación (el teléfono del
  chat debe ser el de la postulación); `p_<token>_<paso>` = ese paso del proceso (respuesta de
  `services/canal_proceso.respuesta_paso`). Si llega antes del contacto, el payload espera en `ChatTelegram.inicio_pendiente`.
"""

import traceback
from collections import deque

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Postulacion, UpdateTelegram, ahora, registrar
from ..services import canal_proceso, telegram
from ..services.mensajeria import en_conversacion
from ..services.whatsapp import enviar_con_boton, enviar_mensaje
from .candidatos import fijar_conversacion, guardar_mensaje
from .webhooks import procesar_entrante

router = APIRouter(tags=["webhooks"])

_RESPALDO_DEDUP: deque = deque(maxlen=2000)  # solo si la tabla no existe (paso no fatal del arranque)


def _primera_vez(update_id) -> bool:
    """True si el update_id no se había recibido (dedupe persistente; Telegram reintenta si tardamos)."""
    if update_id is None:
        return True
    db = SessionLocal()
    try:
        db.add(UpdateTelegram(update_id=int(update_id)))
        db.commit()
        if int(update_id) % 200 == 0:  # limpieza ocasional de lo viejo
            from datetime import timedelta

            db.query(UpdateTelegram).filter(UpdateTelegram.recibido_en < ahora() - timedelta(days=7)).delete()
            db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
    except Exception as e:  # noqa: BLE001 — sin tabla: dedupe en memoria
        db.rollback()
        print(f"[telegram] dedupe en memoria ({e})")
        if update_id in _RESPALDO_DEDUP:
            return False
        _RESPALDO_DEDUP.append(update_id)
        return True
    finally:
        db.close()


@router.post("/api/webhooks/telegram")
@router.post("/webhooks/telegram", include_in_schema=False)
async def webhook_telegram(request: Request, background: BackgroundTasks):
    if not telegram.activo():
        raise HTTPException(503, "Telegram no está configurado (TELEGRAM_BOT_TOKEN).")
    if not telegram.secreto_valido(request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")):
        raise HTTPException(403, "Secreto del webhook de Telegram inválido.")
    try:
        update = await request.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "JSON no válido"}
    if not isinstance(update, dict):
        return {"ok": False, "error": "JSON no válido"}
    if not _primera_vez(update.get("update_id")):
        return {"ok": True, "duplicado": True}
    background.add_task(procesar_update, update)
    return {"ok": True}


async def procesar_update(update: dict) -> dict:
    """Procesa un Update en su propia sesión. Nunca lanza: un error queda en el log."""
    msg = telegram.parsear_update(update)
    if not msg or not msg.get("chat_id"):
        return {"ok": True, "ignorado": True}
    db = SessionLocal()
    try:
        return await _procesar(db, msg)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        print(f"[telegram] Error procesando update {update.get('update_id')}: {e}\n{traceback.format_exc()}")
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


async def _procesar(db: Session, msg: dict) -> dict:
    if msg.get("callback_id"):
        await telegram.responder_callback(msg["callback_id"])
    chat_id = msg["chat_id"]
    ch = telegram.chat(db, chat_id)
    ch.ultimo_entrante_en = ahora()
    if msg.get("nombre") and not ch.nombre:
        ch.nombre = msg["nombre"][:200]
    if msg.get("start"):
        ch.inicio_pendiente = msg["start"][:80]

    contacto = msg.get("contacto")
    if contacto:
        if not contacto.get("propio"):
            db.commit()
            await telegram.enviar_a_chat(chat_id, "Necesito *tu* número: tócalo con el botón de abajo (no el de otro contacto).")
            await telegram.pedir_contacto(chat_id)
            return {"ok": True, "accion": "contacto_ajeno"}
        tel = telegram.guardar_contacto(db, chat_id, contacto["telefono"], msg.get("nombre", ""))
        pendiente, ch.inicio_pendiente = ch.inicio_pendiente, ""
        registrar(db, "sistema", "telegram_contacto_vinculado", "chat_telegram", chat_id, {"telefono": tel})
        db.commit()
        await telegram.confirmar_contacto(chat_id)
        return await _iniciar(db, tel, pendiente, msg)

    tel = ch.telefono
    if not tel:
        db.commit()
        await telegram.pedir_contacto(chat_id, msg.get("nombre", ""))
        return {"ok": True, "accion": "contacto_solicitado"}
    if msg.get("start") is not None:  # /start con o sin payload
        ch.inicio_pendiente = ""
        db.commit()
        return await _iniciar(db, tel, msg.get("start") or "", msg)
    db.commit()
    return await _entrante(db, tel, msg)


async def _entrante(db: Session, tel: str, msg: dict) -> dict:
    with en_conversacion("telegram", tel):
        return await procesar_entrante(db, telegram.mensaje_para_agente(msg, tel))


async def _iniciar(db: Session, tel: str, payload: str, msg: dict) -> dict:
    """Arranque por deep link (o saludo si no hay payload válido)."""
    tipo, valor, paso = telegram.separar_inicio(payload)
    base = {**msg, "contacto": None, "media": None, "tipo": "text", "texto": "Hola", "id_seleccionado": ""}
    if tipo == "vac":
        return await _entrante(db, tel, {**base, "tipo": "interactive", "texto": valor, "id_seleccionado": valor})
    if tipo != "p":
        return await _entrante(db, tel, base)

    p = db.query(Postulacion).filter(Postulacion.telegram_token == valor).first()
    with en_conversacion("telegram", tel):
        if p is None or not p.activa:
            await enviar_mensaje(tel, "Esta liga ya no está vigente. Escríbeme «Hola» y te muestro las vacantes disponibles. 🙂")
            return {"ok": True, "accion": "liga_no_vigente"}
        if telegram.telefono_10(p.telefono) != tel:
            registrar(db, "sistema", "telegram_liga_rechazada", "postulacion", p.codigo, {"motivo": "telefono_distinto"})
            db.commit()
            await enviar_mensaje(tel, "Esta liga pertenece a otro número de celular. Ábrela desde la cuenta de Telegram con el "
                                      "número que registraste en tu postulación, o escríbele al equipo de RH.")
            return {"ok": True, "accion": "liga_otro_telefono", "postulacion": p.codigo}
        fijar_conversacion(p)  # abrir la liga es una acción del candidato
        registrar(db, "sistema", "telegram_vinculado", "postulacion", p.codigo, {"chat_id": msg["chat_id"], "paso": paso})
        db.commit()
        if not paso and p.espera_respuesta:
            return await procesar_entrante(db, telegram.mensaje_para_agente(base, tel))
        r = canal_proceso.respuesta_paso(db, p, paso) if paso else (
            canal_proceso.seguimiento(db, p) or {"texto": f"¡Listo! Por aquí te avisaremos de tu proceso para *{p.vacante.titulo if p.vacante else 'tu postulación'}*.", "url": ""})
        if r.get("url"):
            envio = await enviar_con_boton(tel, r["texto"], r.get("boton") or "Abrir", r["url"], cuenta_id=p.cuenta_id)
        else:
            envio = await enviar_mensaje(tel, r["texto"], cuenta_id=p.cuenta_id)
        guardar_mensaje(db, p, "assistant", r["texto"], "whatsapp", envio)
        db.commit()
        return {"ok": True, "accion": "paso_proceso" if paso else "seguimiento_proceso", "postulacion": p.codigo, "paso": paso, "envio": envio}
