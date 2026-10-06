"""Canal Telegram (2026-10-06) — SOLO infraestructura de entrega y recepción.

Telegram es un proveedor más detrás de la fachada de mensajería (`services/whatsapp.py`): ningún flujo del negocio
llama a este módulo directamente para decidir qué decir. La fachada decide el canal con `services/mensajeria.py`
(canal de la Cuenta + canal de la conversación) y delega aquí el envío; el webhook (`routers/webhooks_telegram.py`)
normaliza cada Update al MISMO formato que `whatsapp.parsear_webhook` y lo pasa a `webhooks.procesar_entrante`, que
consulta el proceso configurable de la postulación.

- Cliente: llamadas HTTP directas a la Bot API con httpx (`llamar`). NUNCA lanza: un error de red o un rechazo
  regresa `{"ok": False, ...}` y queda en el log.
- Identidad: un bot no puede escribirle a un número, solo a un chat que ya le habló. El bot pide «Compartir mi número»
  (`request_contact`, Telegram lo verifica) y guarda chat ↔ teléfono a 10 dígitos en `chats_telegram`. El resto de la
  plataforma sigue usando el teléfono.
- Deep links (`/start <payload>`, solo `[A-Za-z0-9_-]`, máx. 64): `vac_<VAC-####>` entra a una vacante;
  `p_<token>` abre la postulación; `p_<token>_<paso>` abre un paso de su proceso configurable.
"""

import hashlib
import hmac
import html
import re
import secrets
from typing import Optional, Tuple

import httpx

from ..config import settings

API_URL = "https://api.telegram.org"
TEXTO_BOTON_CONTACTO = "📱 Compartir mi número"
RE_PAYLOAD = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RE_TOKEN = re.compile(r"^[0-9a-f]{16}$")
PREFIJO_VACANTE = "vac"
PREFIJO_POSTULACION = "p"


def activo() -> bool:
    return bool((settings.telegram_bot_token or "").strip())


def usuario_bot() -> str:
    return (settings.telegram_bot_username or "").strip().lstrip("@")


def secreto_webhook() -> str:
    """Secreto de `setWebhook` (A-Z a-z 0-9 _ -). Sin TELEGRAM_WEBHOOK_SECRET se deriva del token del bot: el servidor y
    el script de registro llegan al mismo valor sin otra variable."""
    if (settings.telegram_webhook_secret or "").strip():
        return settings.telegram_webhook_secret.strip()
    return hmac.new((settings.telegram_bot_token or "").strip().encode(), b"redhuman-webhook-telegram", hashlib.sha256).hexdigest()


def secreto_valido(cabecera: str) -> bool:
    return activo() and hmac.compare_digest(secreto_webhook(), (cabecera or "").strip())


def _resultado(enviado: bool, detalle, **extra) -> dict:
    return {"enviado": enviado, "proveedor": "telegram", "detalle": detalle, **extra}


async def llamar(metodo: str, json: Optional[dict] = None, data: Optional[dict] = None, files: Optional[dict] = None) -> dict:
    """POST a la Bot API. Regresa {ok, result | description, error_code}; nunca lanza (red caída = ok False)."""
    if not activo():
        return {"ok": False, "description": "TELEGRAM_BOT_TOKEN sin configurar"}
    url = f"{API_URL}/bot{settings.telegram_bot_token.strip()}/{metodo}"
    try:
        async with httpx.AsyncClient(timeout=30) as cli:
            r = await cli.post(url, json=json, data=data, files=files)
        cuerpo = r.json()
    except Exception as e:  # noqa: BLE001 — la mensajería nunca tumba el flujo de RH
        print(f"[telegram] Error de red en {metodo}: {e}")
        return {"ok": False, "description": f"error de red: {e}"}
    if not isinstance(cuerpo, dict):
        return {"ok": False, "description": "respuesta no válida de Telegram"}
    if not cuerpo.get("ok"):
        print(f"[telegram] {metodo} rechazado ({cuerpo.get('error_code')}): {cuerpo.get('description')}")
    return cuerpo


# --------------------------------------------------------------------------- #
# chat ↔ teléfono
# --------------------------------------------------------------------------- #

def telefono_10(telefono: str) -> str:
    """+52 1 55 1234 5678 / 5215512345678 / 525512345678 → 5512345678 (como se guarda en la base)."""
    digitos = re.sub(r"\D", "", telefono or "")
    if digitos.startswith("521") and len(digitos) == 13:
        return digitos[3:]
    if digitos.startswith("52") and len(digitos) == 12:
        return digitos[2:]
    return digitos[-10:] if len(digitos) > 10 else digitos


def chat_de_telefono(telefono: str, db=None) -> Optional[str]:
    """Chat de Telegram de ese teléfono (el más reciente que lo compartió) o None si nunca habló con el bot."""
    from ..database import SessionLocal
    from ..models import ChatTelegram

    tel = telefono_10(telefono)
    if not tel:
        return None
    propia = db is None
    db = db or SessionLocal()
    try:
        fila = db.query(ChatTelegram).filter(ChatTelegram.telefono == tel).order_by(ChatTelegram.actualizado_en.desc()).first()
        return fila.chat_id if fila else None
    except Exception as e:  # noqa: BLE001 — tabla no creada (paso no fatal) → no hay a quién mandar
        print(f"[telegram] no se pudo leer chats_telegram: {e}")
        return None
    finally:
        if propia:
            db.close()


def chat(db, chat_id: str):
    from ..models import ChatTelegram

    fila = db.get(ChatTelegram, str(chat_id))
    if fila is None:
        fila = ChatTelegram(chat_id=str(chat_id))
        db.add(fila)
        db.flush()
    return fila


def guardar_contacto(db, chat_id: str, telefono: str, nombre: str) -> str:
    fila = chat(db, chat_id)
    fila.telefono = telefono_10(telefono)
    if nombre:
        fila.nombre = nombre[:200]
    db.flush()
    return fila.telefono


# --------------------------------------------------------------------------- #
# Deep links
# --------------------------------------------------------------------------- #

def asegurar_token(p) -> str:
    """Token del deep link de la postulación (16 hex). Se reutiliza: reenviar la liga nunca rompe la anterior."""
    if not p.telegram_token:
        p.telegram_token = secrets.token_hex(8)
    return p.telegram_token


def payload_postulacion(token: str, paso_id: str = "") -> str:
    payload = f"{PREFIJO_POSTULACION}_{token}" + (f"_{paso_id}" if paso_id else "")
    return payload if RE_PAYLOAD.match(payload) else f"{PREFIJO_POSTULACION}_{token}"


def liga(payload: str) -> str:
    """https://t.me/<bot>?start=<payload> (abre la app; en escritorio sin app, Telegram Web). Vacía sin bot configurado."""
    if not (usuario_bot() and payload and RE_PAYLOAD.match(payload)):
        return ""
    return f"https://t.me/{usuario_bot()}?start={payload}"


def liga_postulacion(p, paso_id: str = "") -> str:
    return liga(payload_postulacion(asegurar_token(p), paso_id))


def liga_vacante(codigo: str) -> str:
    return liga(f"{PREFIJO_VACANTE}_{codigo}") if codigo else ""


def separar_inicio(payload: str) -> Tuple[str, str, str]:
    """`vac_VAC-0001` → ("vac", "VAC-0001", ""); `p_<token>` → ("p", token, ""); `p_<token>_<paso>` →
    ("p", token, paso); vacío o desconocido → ("", "", "")."""
    payload = (payload or "").strip()
    if not payload or not RE_PAYLOAD.match(payload):
        return "", "", ""
    pref, _, resto = payload.partition("_")
    if pref == PREFIJO_VACANTE and resto:
        return "vac", resto, ""
    if pref == PREFIJO_POSTULACION and resto:
        token, separador, paso = resto[:16], resto[16:17], resto[17:]
        if RE_TOKEN.match(token) and (separador == "" or (separador == "_" and paso)):
            return "p", token, paso
    return "", "", ""


# --------------------------------------------------------------------------- #
# Envío
# --------------------------------------------------------------------------- #

def _html(texto: str) -> str:
    """Los textos de la plataforma usan el formato de WhatsApp (*negritas*): se pasan a HTML de Telegram."""
    seguro = html.escape(texto or "", quote=False)
    return re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", seguro)


async def enviar_a_chat(chat_id: str, texto: str, teclado: Optional[dict] = None) -> dict:
    cuerpo = {"chat_id": chat_id, "text": _html(texto)[:4096], "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
    if teclado:
        cuerpo["reply_markup"] = teclado
    r = await llamar("sendMessage", json=cuerpo)
    if not r.get("ok") and "parse" in str(r.get("description", "")).lower():
        cuerpo.pop("parse_mode")
        cuerpo["text"] = (texto or "")[:4096]
        r = await llamar("sendMessage", json=cuerpo)
    if r.get("ok"):
        return _resultado(True, 200, wa_id=f"tg-msg-{(r.get('result') or {}).get('message_id', '')}")
    return _resultado(False, f"{r.get('error_code', '')}: {r.get('description', '')}".strip(": "), codigo=r.get("error_code"))


def _sin_chat(telefono: str) -> dict:
    return _resultado(
        False,
        f"El número {telefono_10(telefono) or '(vacío)'} no ha iniciado conversación con el bot de Telegram "
        "(o no compartió su número); Telegram no permite escribirle primero.",
        sin_chat=True,
    )


async def enviar_texto(telefono: str, texto: str) -> dict:
    destino = chat_de_telefono(telefono)
    if not destino:
        return _sin_chat(telefono)
    return await enviar_a_chat(destino, texto)


async def enviar_boton(telefono: str, texto: str, boton: str, url: str = "", callback: str = "") -> dict:
    """Texto con UN botón en línea: `url` (abre la liga) o `callback` (llega al webhook como selección). Si Telegram
    rechaza el botón (p. ej. una liga local), se manda el texto con la liga escrita: nunca se pierde."""
    destino = chat_de_telefono(telefono)
    if not destino:
        return _sin_chat(telefono)
    tecla = {"text": boton[:60], **({"url": url} if url else {"callback_data": (callback or boton)[:64]})}
    r = await enviar_a_chat(destino, texto, {"inline_keyboard": [[tecla]]})
    if not r.get("enviado") and url:
        r = await enviar_a_chat(destino, f"{texto}\n\n👉 {boton}: {url}")
    return r


async def enviar_lista(telefono: str, encabezado: str, cuerpo: str, opciones: list, secciones: Optional[list] = None) -> dict:
    """La lista interactiva de Meta como botones en línea (uno por opción): al tocarlo llega un callback_query con el
    id (VAC-####, P-####, CTA-<id>) que el webhook trata igual que un list_reply."""
    destino = chat_de_telefono(telefono)
    if not destino:
        return _sin_chat(telefono)
    grupos = [s for s in (secciones or []) if s.get("opciones")] or [{"titulo": "", "opciones": opciones or []}]
    filas, lineas = [], [f"*{encabezado}*", cuerpo]
    for g in grupos:
        if len(grupos) > 1 and g.get("titulo"):
            lineas.append(f"\n*{g['titulo']}*")
        for o in g["opciones"][:30]:
            titulo = str(o.get("titulo") or o["id"])
            desc = str(o.get("descripcion") or "")
            lineas.append(f"• {titulo}" + (f" — {desc}" if desc else ""))
            filas.append([{"text": titulo[:60], "callback_data": str(o["id"])[:64]}])
    if not filas:
        return _resultado(False, "No hay opciones que mostrar")
    return await enviar_a_chat(destino, "\n".join(lineas), {"inline_keyboard": filas})


async def enviar_documento(telefono: str, contenido: bytes, filename: str, caption: str = "", mime: str = "application/pdf") -> dict:
    destino = chat_de_telefono(telefono)
    if not destino:
        return _sin_chat(telefono)
    r = await llamar(
        "sendDocument",
        data={"chat_id": destino, "caption": _html(caption)[:1024], "parse_mode": "HTML"},
        files={"document": (filename, contenido, mime)},
    )
    if r.get("ok"):
        return _resultado(True, 200, wa_id=f"tg-msg-{(r.get('result') or {}).get('message_id', '')}", formato="documento")
    return _resultado(False, f"{r.get('error_code', '')}: {r.get('description', '')}".strip(": "))


async def pedir_contacto(chat_id: str, nombre: str = "") -> dict:
    texto = (f"¡Hola{', ' + nombre.split(' ')[0] if nombre else ''}! Soy Red Human. Para continuar necesito confirmar tu "
             f"número de celular: toca *{TEXTO_BOTON_CONTACTO}*.")
    teclado = {"keyboard": [[{"text": TEXTO_BOTON_CONTACTO, "request_contact": True}]], "resize_keyboard": True, "one_time_keyboard": True}
    return await enviar_a_chat(chat_id, texto, teclado)


async def confirmar_contacto(chat_id: str) -> dict:
    return await enviar_a_chat(chat_id, "¡Gracias! Ya tengo tu número ✅", {"remove_keyboard": True})


async def responder_callback(callback_id: str) -> None:
    if callback_id:
        await llamar("answerCallbackQuery", json={"callback_query_id": callback_id})


_EXT_POR_MIME = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp"}
_MIME_POR_EXT = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


async def descargar_archivo(file_id: str, mime: str = "") -> dict:
    """getFile → binario. Mismo contrato que `whatsapp.descargar_media` ({ok, contenido, mime, extension, filename,
    tamano, detalle}); nunca lanza."""
    if not file_id:
        return {"ok": False, "detalle": "sin file_id"}
    info = await llamar("getFile", json={"file_id": file_id})
    ruta = (info.get("result") or {}).get("file_path", "")
    if not info.get("ok") or not ruta:
        return {"ok": False, "detalle": f"getFile: {info.get('description', 'sin ruta')}"}
    try:
        async with httpx.AsyncClient(timeout=30) as cli:
            r = await cli.get(f"{API_URL}/file/bot{settings.telegram_bot_token.strip()}/{ruta}")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detalle": f"error de red: {e}"}
    if r.status_code >= 300:
        return {"ok": False, "detalle": f"descarga del archivo: HTTP {r.status_code}"}
    ext_ruta = ruta.rsplit(".", 1)[-1].lower() if "." in ruta else ""
    mime = (mime or _MIME_POR_EXT.get(ext_ruta, "")).lower()
    ext = _EXT_POR_MIME.get(mime, "")
    if not ext:
        return {"ok": False, "detalle": f"formato no admitido ({mime or 'desconocido'}); acepta PDF, JPG o PNG"}
    return {"ok": True, "contenido": r.content, "mime": mime, "extension": ext, "filename": f"telegram_{file_id[-12:]}.{ext}", "tamano": len(r.content)}


# --------------------------------------------------------------------------- #
# Recepción: Update → mensaje normalizado
# --------------------------------------------------------------------------- #

def _nombre(u: dict) -> str:
    return " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x).strip()


def parsear_update(update: dict) -> Optional[dict]:
    """Normaliza un Update. None si no es un mensaje de una persona en chat privado (ediciones, bots, grupos, estados
    del bot). Campos: chat_id, texto, nombre, update_id, tipo (text | interactive | contact | image | document), media,
    id_seleccionado, callback_id, contacto {telefono, propio}, start (None sin /start; "" o el payload con /start)."""
    cb = update.get("callback_query")
    if cb:
        m = cb.get("message") or {}
        chat_ = m.get("chat") or {}
        remitente = cb.get("from") or {}
        if remitente.get("is_bot") or chat_.get("type", "private") != "private":
            return None
        elegido = str(cb.get("data") or "")
        titulo = next((b.get("text", "") for fila in ((m.get("reply_markup") or {}).get("inline_keyboard") or [])
                       for b in fila if b.get("callback_data") == elegido), "")
        return {"chat_id": str(chat_.get("id") or remitente.get("id") or ""), "texto": titulo or elegido,
                "nombre": _nombre(remitente), "update_id": update.get("update_id"), "tipo": "interactive",
                "media": None, "id_seleccionado": elegido, "callback_id": cb.get("id", ""), "contacto": None, "start": None}

    m = update.get("message")
    if not m:
        return None  # edited_message, my_chat_member, etc.
    chat_ = m.get("chat") or {}
    remitente = m.get("from") or {}
    if remitente.get("is_bot") or chat_.get("type") != "private":
        return None
    contacto = None
    if m.get("contact"):
        c = m["contact"]
        contacto = {"telefono": str(c.get("phone_number", "")), "propio": bool(c.get("user_id")) and c.get("user_id") == remitente.get("id")}
    tipo, media = "text", None
    if m.get("photo"):
        mayor = sorted(m["photo"], key=lambda f: f.get("file_size") or 0)[-1]
        tipo, media = "image", {"id": mayor.get("file_id", ""), "mime_type": "image/jpeg", "filename": ""}
    elif m.get("document"):
        d = m["document"]
        tipo, media = "document", {"id": d.get("file_id", ""), "mime_type": d.get("mime_type", ""), "filename": d.get("file_name", "")}
    elif contacto:
        tipo = "contact"
    texto = m.get("text") or m.get("caption") or ""
    start = None
    partes = texto.strip().split(maxsplit=1)
    if partes and partes[0].lower().split("@")[0] == "/start":
        start = partes[1].strip() if len(partes) > 1 else ""
        texto = "Hola"
    return {"chat_id": str(chat_.get("id", "")), "texto": texto, "nombre": _nombre(remitente), "update_id": update.get("update_id"),
            "tipo": tipo, "media": media, "id_seleccionado": "", "callback_id": "", "contacto": contacto, "start": start}


def mensaje_para_agente(msg: dict, telefono: str) -> dict:
    """El mensaje normalizado con la MISMA forma que `whatsapp.parsear_webhook` (lo que procesa `procesar_entrante`)."""
    tel = telefono_10(telefono)
    return {
        "telefono": "52" + tel if len(tel) == 10 else tel,
        "texto": msg.get("texto", ""),
        "nombre": msg.get("nombre", ""),
        "wa_id": f"tg-{msg.get('update_id')}",
        "tipo": msg.get("tipo", "text"),
        "media": msg.get("media"),
        "id_seleccionado": msg.get("id_seleccionado", ""),
        "numero_receptor": "",
        "canal": "telegram",
    }
