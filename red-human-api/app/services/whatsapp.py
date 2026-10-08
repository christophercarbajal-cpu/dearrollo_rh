"""
Servicio de WhatsApp — envío y recepción de mensajes del agente.

Proveedores (`WHATSAPP_PROVIDER` en .env):
  · meta       →  WhatsApp Cloud API oficial. Número verificado por Meta, sin
                  servidor extra que mantener. Se paga por conversación.
  · waha       →  https://waha.devlike.pro          (docker devlikeapro/waha)
  · evolution  →  https://github.com/EvolutionAPI/evolution-api
  · ""         →  modo demo: el mensaje se guarda en la base como "no enviado".

Webhook de entrada:  {API}/webhooks/whatsapp
  · Meta pide además un GET de verificación (hub.challenge) y firma cada POST
    con HMAC-SHA256 en la cabecera X-Hub-Signature-256.

Ventana de 24 horas (solo Meta): a un candidato que NO nos ha escrito en las
últimas 24 h no se le puede mandar texto libre; Meta lo rechaza con el error
131047. Para esos casos se usa una plantilla aprobada (`META_PLANTILLA_AVISO`),
y `enviar_mensaje` cae a ella automáticamente cuando existe.

Telegram (2026-10-06): esta es la FACHADA única de mensajería. Cada envío pregunta a `services/mensajeria.resolver`
(canal de la Cuenta — `cuenta_id` opcional — y canal de la conversación en curso) y, si toca Telegram, delega en
`services/telegram.py`. Telegram no tiene plantillas ni ventana de 24 h: las plantillas salen como su texto.
"""

import hashlib
import hmac
import re
from typing import List, Optional

import httpx

from ..config import settings
from . import telegram

GRAPH_URL = "https://graph.facebook.com"

# Meta rechaza texto libre fuera de la ventana de 24 h con estos códigos.
CODIGOS_FUERA_DE_VENTANA = {131047, 131026, 132000}


def _por_telegram(telefono: str, cuenta_id: Optional[int] = None) -> bool:
    """¿Este envío sale por Telegram? (canal de la conversación / de la Cuenta, ver services/mensajeria.py)."""
    from .mensajeria import resolver

    return resolver(telefono, cuenta_id) == "telegram"


def whatsapp_activo() -> bool:
    return proveedor() != "demo"


def proveedor() -> str:
    """Proveedor efectivo. Si no se declaró pero hay credenciales de Meta, es Meta:
    así un .env incompleto no deja la mensajería en modo demo sin avisar."""
    if settings.whatsapp_provider in ("meta", "waha", "evolution"):
        return settings.whatsapp_provider
    if settings.meta_whatsapp_token and settings.meta_phone_number_id:
        return "meta"
    return "demo"


def _solo_digitos(telefono: str) -> str:
    return re.sub(r"\D", "", telefono or "")


def clave_telefono(telefono: str) -> str:
    """Últimos 10 dígitos: la forma en que se guardan los teléfonos en la base.

    Sirve para que un mensaje entrante (que llega como 5213311112222) empate con
    el candidato que ya existe capturado como 3311112222.
    """
    digitos = _solo_digitos(telefono)
    return digitos[-10:] if len(digitos) > 10 else digitos


def numero_e164(telefono: str) -> str:
    """Número listo para la API, en E.164 sin '+'. Asume México si no trae lada."""
    digitos = _solo_digitos(telefono)
    if len(digitos) == 10:  # capturado sin lada internacional
        digitos = "52" + digitos
    # México ya no usa el '1' después del 52 para WhatsApp; Meta devuelve los
    # wa_id sin él, así que lo quitamos para que envío y webhook coincidan.
    if len(digitos) == 13 and digitos.startswith("521"):
        digitos = "52" + digitos[3:]
    return digitos


def _resultado(enviado: bool, detalle, prov: Optional[str] = None, **extra) -> dict:
    return {"enviado": enviado, "proveedor": prov or proveedor(), "detalle": detalle, **extra}


# --------------------------------------------------------------------------- #
# Envío
# --------------------------------------------------------------------------- #

def _meta_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.meta_whatsapp_token}",
        "Content-Type": "application/json",
    }


def _meta_url() -> str:
    return (
        f"{GRAPH_URL}/{settings.meta_api_version}"
        f"/{settings.meta_phone_number_id}/messages"
    )


def _meta_error(r: httpx.Response) -> dict:
    """Extrae {codigo, mensaje} del cuerpo de error de Graph."""
    try:
        err = (r.json() or {}).get("error") or {}
    except Exception:
        return {"codigo": r.status_code, "mensaje": r.text[:300]}
    return {
        "codigo": err.get("code", r.status_code),
        "mensaje": err.get("error_user_msg") or err.get("message") or r.text[:300],
    }


async def _meta_post(cuerpo: dict) -> dict:
    if not (settings.meta_whatsapp_token and settings.meta_phone_number_id):
        return _resultado(False, "Faltan META_WHATSAPP_TOKEN o META_PHONE_NUMBER_ID")
    try:
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.post(_meta_url(), headers=_meta_headers(), json=cuerpo)
    except Exception as e:  # red caída: no romper el flujo de RH
        print(f"[whatsapp] Error de red al llamar a Meta: {e}")
        return _resultado(False, f"error de red: {e}")

    if r.status_code < 300:
        datos = r.json()
        wamid = ((datos.get("messages") or [{}])[0]).get("id", "")
        return _resultado(True, r.status_code, wa_id=wamid)

    error = _meta_error(r)
    # Silencioso para el candidato/RH: el detalle completo solo queda en logs del
    # servidor para poder depurar (payload rechazado, plantilla no aprobada, etc.).
    template = cuerpo.get("template") or {}
    contexto_plantilla = (
        f" (idioma enviado: {(template.get('language') or {}).get('code')}, plantilla: {template.get('name')})"
        if template else ""
    )
    print(f"[whatsapp] Meta rechazó el mensaje{contexto_plantilla} ({r.status_code}): {r.text[:1000]}")
    return _resultado(False, f"{error['codigo']}: {error['mensaje']}", codigo=error["codigo"])


def _param_plantilla(valor: str) -> str:
    """Meta rechaza variables con saltos de línea, tabuladores o 4+ espacios seguidos."""
    limpio = re.sub(r"[\r\n\t]+", " ", valor or "")
    limpio = re.sub(r" {2,}", " ", limpio).strip()
    return limpio[:900]


async def enviar_plantilla(
    telefono: str,
    plantilla: str,
    parametros: Optional[List[str]] = None,
    idioma: Optional[str] = None,
    cuenta_id: Optional[int] = None,
    texto: str = "",
) -> dict:
    """Manda una plantilla aprobada (único formato válido fuera de la ventana de 24 h). Por Telegram sale `texto` (o los
    parámetros unidos): no existen plantillas."""
    if _por_telegram(telefono, cuenta_id):
        resultado = await telegram.enviar_texto(telefono, texto or " ".join(str(x) for x in (parametros or [])))
        resultado["formato"] = "texto"
        return resultado
    componentes = []
    if parametros:
        componentes.append({
            "type": "body",
            "parameters": [{"type": "text", "text": _param_plantilla(p)} for p in parametros],
        })
    cuerpo = {
        "messaging_product": "whatsapp",
        "to": numero_e164(telefono),
        "type": "template",
        "template": {
            "name": plantilla,
            "language": {"code": idioma or "es_MX"},
            **({"components": componentes} if componentes else {}),
        },
    }
    resultado = await _meta_post(cuerpo)
    resultado["formato"] = "plantilla"
    return resultado


PARAMS_PLANTILLA_DOCUMENTOS = ("nombre", "documentos", "liga", "empresa", "vacante")


def plantilla_documentos_por_nivel(nivel: int) -> str:
    """Nombre de la plantilla de Meta según el nivel del recordatorio (2026-09-17); sin plantilla
    específica cae a META_PLANTILLA_DOCUMENTOS."""
    especifica = {2: settings.meta_plantilla_recordatorio_2, 3: settings.meta_plantilla_recordatorio_3}.get(int(nivel or 1), "")
    return (especifica or settings.meta_plantilla_documentos or "").strip()


async def enviar_plantilla_documentos(telefono: str, valores: dict, texto_fallback: str, nivel: int = 1, cuenta_id: Optional[int] = None) -> dict:
    """Solicitud/recordatorio de documentos (2026-09-15): primero la plantilla aprobada
    META_PLANTILLA_DOCUMENTOS (sirve también fuera de la ventana de 24 h), con sus variables en el
    orden de META_PLANTILLA_DOCUMENTOS_PARAMS; si no está configurada o Meta la rechaza, se manda
    el texto libre de siempre (que a su vez cae a META_PLANTILLA_AVISO fuera de ventana)."""
    if _por_telegram(telefono, cuenta_id):
        return await telegram.enviar_texto(telefono, texto_fallback)
    plantilla = plantilla_documentos_por_nivel(nivel)
    if settings.whatsapp_provider == "meta" and plantilla:
        claves = [k.strip().lower() for k in (settings.meta_plantilla_documentos_params or "").split(",") if k.strip()]
        parametros = [str(valores.get(k, "") or "-") for k in claves if k in PARAMS_PLANTILLA_DOCUMENTOS]
        resultado = await enviar_plantilla(telefono, plantilla, parametros, settings.meta_plantilla_idioma)
        if resultado.get("enviado"):
            resultado["plantilla"] = plantilla
            return resultado
        print(f"[whatsapp] plantilla de documentos «{plantilla}» no salió ({resultado.get('detalle')}); se manda texto libre")
        alterno = await enviar_mensaje(telefono, texto_fallback)
        alterno["motivo_fallback"] = f"plantilla {plantilla}: {resultado.get('detalle')}"
        return alterno
    return await enviar_mensaje(telefono, texto_fallback)


async def enviar_plantilla_entrevista(telefono: str, parametros: List[str], texto_fallback: str, cuenta_id: Optional[int] = None) -> dict:
    """Aviso al entrevistador de una Entrevista Humana asignada (2026-09-18): plantilla
    META_PLANTILLA_ENTREVISTA («alerta_entrevista_asignada») con EXACTAMENTE 6 parámetros posicionales
    [entrevistador, candidato, vacante, fecha, hora, liga al expediente]. Sirve fuera de la ventana de 24 h
    (el entrevistador casi nunca le ha escrito al número). Si no está configurada o Meta la rechaza, texto
    libre (que a su vez cae a META_PLANTILLA_AVISO)."""
    if _por_telegram(telefono, cuenta_id):
        return await telegram.enviar_texto(telefono, texto_fallback)
    plantilla = (settings.meta_plantilla_entrevista or "").strip()
    if settings.whatsapp_provider == "meta" and plantilla:
        if len(parametros) != 6:
            raise ValueError(f"La plantilla {plantilla} requiere 6 parámetros; llegaron {len(parametros)}")
        resultado = await enviar_plantilla(telefono, plantilla, [str(x or "-") for x in parametros], settings.meta_plantilla_idioma)
        if resultado.get("enviado"):
            resultado["plantilla"] = plantilla
            return resultado
        print(f"[whatsapp] plantilla de entrevista «{plantilla}» no salió ({resultado.get('detalle')}); se manda texto libre")
        alterno = await enviar_mensaje(telefono, texto_fallback)
        alterno["motivo_fallback"] = f"plantilla {plantilla}: {resultado.get('detalle')}"
        return alterno
    return await enviar_mensaje(telefono, texto_fallback)


# Meta → extensión aceptada por services/archivos.FORMATOS (documentos de expediente).
_EXT_POR_MIME = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


async def descargar_media(media_id: str, mime: str = "") -> dict:
    """Descarga un medio recibido por el webhook (2026-09-15): Graph `GET /{media_id}` regresa la URL
    temporal + mime; la URL se lee con el mismo Bearer. Regresa {ok, contenido, mime, filename,
    detalle} y nunca lanza — el webhook le explica al candidato si algo falla. Si el mensaje entrante llegó por
    Telegram, `media_id` es el file_id y se descarga con getFile."""
    from .mensajeria import canal_conversacion

    if canal_conversacion() == "telegram":
        return await telegram.descargar_archivo(media_id, mime)
    if not media_id:
        return {"ok": False, "detalle": "sin media_id"}
    if settings.whatsapp_provider != "meta" or not settings.meta_whatsapp_token:
        return {"ok": False, "detalle": "descarga de medios solo disponible con WHATSAPP_PROVIDER=meta"}
    cabeceras = {"Authorization": f"Bearer {settings.meta_whatsapp_token}"}
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cli:
            meta = await cli.get(f"{GRAPH_URL}/{settings.meta_api_version}/{media_id}", headers=cabeceras)
            if meta.status_code >= 300:
                err = _meta_error(meta)
                return {"ok": False, "detalle": f"Graph {err['codigo']}: {err['mensaje']}"}
            info = meta.json() or {}
            url = info.get("url", "")
            if not url:
                return {"ok": False, "detalle": "Graph no regresó url del medio"}
            binario = await cli.get(url, headers=cabeceras)
            if binario.status_code >= 300:
                return {"ok": False, "detalle": f"descarga del medio: HTTP {binario.status_code}"}
    except Exception as e:  # red caída: no tumbar el webhook
        return {"ok": False, "detalle": f"error de red: {e}"}
    mime = (info.get("mime_type") or binario.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = _EXT_POR_MIME.get(mime, "")
    if not ext:
        return {"ok": False, "detalle": f"formato no admitido ({mime or 'desconocido'}); acepta PDF, JPG o PNG"}
    return {"ok": True, "contenido": binario.content, "mime": mime, "extension": ext, "filename": f"whatsapp_{media_id}.{ext}", "tamano": len(binario.content)}


async def enviar_texto_sin_plantilla(telefono: str, texto: str, cuenta_id: Optional[int] = None) -> dict:
    """Texto libre (type text) SIN respaldo de plantilla (2026-09-18, aviso de curso a colaboradores): si Meta
    lo rechaza por la ventana de 24 h se deja un warning en el log y se regresa {enviado: False,
    fuera_de_ventana: True}; nunca lanza. Con otro proveedor se comporta como enviar_mensaje."""
    if settings.whatsapp_provider != "meta" or _por_telegram(telefono, cuenta_id):
        return await enviar_mensaje(telefono, texto, cuenta_id=cuenta_id)
    resultado = await _meta_post({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": numero_e164(telefono),
        "type": "text",
        "text": {"preview_url": True, "body": texto},
    })
    if not resultado.get("enviado") and resultado.get("codigo") in CODIGOS_FUERA_DE_VENTANA:
        print(f"[whatsapp] ⚠️ Mensaje de texto rechazado por ventana de 24h ({numero_e164(telefono)}): {resultado.get('detalle')}", flush=True)
        resultado["fuera_de_ventana"] = True
        resultado["detalle"] = "Mensaje de texto rechazado por ventana de 24h: el colaborador no ha escrito al WhatsApp de la empresa en las últimas 24 h."
    return resultado


async def enviar_mensaje(telefono: str, texto: str, cuenta_id: Optional[int] = None) -> dict:
    """Envía un mensaje de texto. Regresa {enviado, proveedor, detalle}."""
    if _por_telegram(telefono, cuenta_id):
        return await telegram.enviar_texto(telefono, texto)
    if settings.whatsapp_provider == "meta":
        resultado = await _meta_post({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": numero_e164(telefono),
            "type": "text",
            "text": {"preview_url": True, "body": texto},
        })
        # Fuera de la ventana de 24 h el texto libre no pasa: reintenta con la
        # plantilla aprobada si está configurada.
        if not resultado["enviado"] and resultado.get("codigo") in CODIGOS_FUERA_DE_VENTANA:
            if settings.meta_plantilla_aviso:
                alterno = await enviar_plantilla(telefono, settings.meta_plantilla_aviso, [texto])
                alterno["motivo_fallback"] = resultado["detalle"]
                return alterno
            # 2026-09-15: es la causa típica de «al entrevistador/cliente no le llega el WhatsApp»:
            # nunca le ha escrito al número de la empresa, Meta solo acepta plantilla, y no hay
            # plantilla configurada. Que el motivo llegue hasta la pantalla de RH.
            resultado["detalle"] = (
                f"{resultado['detalle']} — el destinatario no ha escrito al WhatsApp de la empresa en las "
                "últimas 24 h; Meta solo permite plantilla aprobada y META_PLANTILLA_AVISO no está configurada."
            )
        return resultado

    numero = numero_e164(telefono)

    if settings.whatsapp_provider == "waha":
        try:
            async with httpx.AsyncClient(timeout=15) as cli:
                r = await cli.post(
                    f"{settings.waha_url.rstrip('/')}/api/sendText",
                    headers={"X-Api-Key": settings.waha_api_key} if settings.waha_api_key else {},
                    json={"session": settings.waha_session, "chatId": f"{numero}@c.us", "text": texto},
                )
            return _resultado(r.status_code < 300, r.status_code)
        except Exception as e:  # gateway caído: no romper el flujo
            return _resultado(False, str(e))

    if settings.whatsapp_provider == "evolution":
        try:
            async with httpx.AsyncClient(timeout=15) as cli:
                r = await cli.post(
                    f"{settings.evolution_url.rstrip('/')}/message/sendText/{settings.evolution_instance}",
                    headers={"apikey": settings.evolution_api_key} if settings.evolution_api_key else {},
                    json={"number": numero, "text": texto},
                )
            return _resultado(r.status_code < 300, r.status_code)
        except Exception as e:
            return _resultado(False, str(e))

    return _resultado(False, "WHATSAPP_PROVIDER sin configurar", prov="demo")


# --------------------------------------------------------------------------- #
# Recepción
# --------------------------------------------------------------------------- #

def firma_valida(cuerpo: bytes, cabecera: str) -> bool:
    """Valida X-Hub-Signature-256. Sin META_APP_SECRET no se puede validar."""
    if not settings.meta_app_secret:
        return False
    esperado = hmac.new(
        settings.meta_app_secret.encode(), cuerpo, hashlib.sha256
    ).hexdigest()
    recibida = (cabecera or "").removeprefix("sha256=").strip()
    return hmac.compare_digest(esperado, recibida)


def _id_seleccionado(m: dict) -> str:
    """Id de la opción elegida en una lista o botón (p. ej. 'VAC-1042').

    Es más confiable que el título para saber qué eligió el candidato, porque el
    título va recortado a 24 caracteres por Meta.
    """
    tipo = m.get("type")
    if tipo == "interactive":
        inter = m.get("interactive") or {}
        destino = inter.get("list_reply") or inter.get("button_reply") or {}
        return destino.get("id", "")
    if tipo == "button":
        return (m.get("button") or {}).get("payload", "")
    return ""


def _texto_de_meta(m: dict) -> str:
    """Saca el texto de un mensaje de Meta sea cual sea su tipo."""
    tipo = m.get("type")
    if tipo == "text":
        return (m.get("text") or {}).get("body", "")
    if tipo == "interactive":
        inter = m.get("interactive") or {}
        destino = inter.get("button_reply") or inter.get("list_reply") or {}
        return destino.get("title", "")
    if tipo == "button":
        return (m.get("button") or {}).get("text", "")
    # imagen/documento/video con pie de foto: el pie es el mensaje
    return (m.get(tipo) or {}).get("caption", "") if isinstance(m.get(tipo), dict) else ""


def acuses_de_entrega(payload: dict) -> List[dict]:
    """Acuses de Meta (`statuses`: sent/delivered/read/failed) → [{mensaje_id, estado, detalle}]. Alimentan la
    trazabilidad de envíos por destinatario (2026-10-08: «entregado» / «fallido» confirmados por el canal)."""
    salida: List[dict] = []
    if payload.get("object") != "whatsapp_business_account":
        return salida
    for entrada in payload.get("entry") or []:
        for cambio in entrada.get("changes") or []:
            for st in (cambio.get("value") or {}).get("statuses") or []:
                if not isinstance(st, dict) or not st.get("id"):
                    continue
                errores = st.get("errors") or []
                detalle = ""
                if errores and isinstance(errores[0], dict):
                    detalle = f"{errores[0].get('code', '')} {errores[0].get('title') or errores[0].get('message') or ''}".strip()
                salida.append({"mensaje_id": str(st["id"]), "estado": str(st.get("status") or ""), "detalle": detalle})
    return salida


def parsear_webhook(payload: dict) -> Optional[dict]:
    """Normaliza el webhook de Meta, WAHA o Evolution.

    Regresa {telefono, texto, nombre, wa_id, tipo} o None si el evento no es un
    mensaje entrante de una persona (acuses de entrega, mensajes propios, etc.).
    """
    # --- Meta Cloud API ---
    # {"object":"whatsapp_business_account","entry":[{"changes":[{"field":"messages",
    #   "value":{"contacts":[{"profile":{"name":...},"wa_id":...}],
    #            "messages":[{"from":...,"id":"wamid...","type":"text","text":{"body":...}}]}}]}]}
    if payload.get("object") == "whatsapp_business_account":
        for entrada in payload.get("entry") or []:
            for cambio in entrada.get("changes") or []:
                valor = cambio.get("value") or {}
                mensajes = valor.get("messages") or []
                if not mensajes:
                    continue  # "statuses": acuses de entrega/lectura, se ignoran
                m = mensajes[0]
                contactos = valor.get("contacts") or [{}]
                nombre = ((contactos[0].get("profile") or {}).get("name")) or ""
                elegido = _id_seleccionado(m)
                # 2026-09-15: adjuntos (image/document) — el webhook los descarga y los adjunta al
                # expediente cuando la postulación está en Contratación/Onboarding.
                media = None
                tipo_m = m.get("type", "text")
                if tipo_m in ("image", "document") and isinstance(m.get(tipo_m), dict):
                    cuerpo_m = m[tipo_m]
                    media = {
                        "id": cuerpo_m.get("id", ""),
                        "mime_type": cuerpo_m.get("mime_type", ""),
                        "filename": cuerpo_m.get("filename", ""),
                        "sha256": cuerpo_m.get("sha256", ""),
                    }
                return {
                    "telefono": str(m.get("from", "")),
                    # si la opción no trae título legible, el id sirve de texto
                    "texto": _texto_de_meta(m) or elegido,
                    "nombre": nombre,
                    "wa_id": m.get("id", ""),
                    "tipo": tipo_m,
                    "media": media,
                    "id_seleccionado": elegido,
                    # número de WhatsApp Business que RECIBIÓ el mensaje: con él se resuelve la
                    # Cuenta (ruteo por número) cuando hay más de una activa.
                    "numero_receptor": str((valor.get("metadata") or {}).get("display_phone_number", "")),
                }
        return None

    # --- WAHA ---
    if payload.get("event") == "message" and isinstance(payload.get("payload"), dict):
        p = payload["payload"]
        if p.get("fromMe"):
            return None
        tel = str(p.get("from", "")).split("@")[0]
        texto = p.get("body") or ""
        nombre = (p.get("_data") or {}).get("notifyName") or ""
        if tel and texto:
            return {"telefono": tel, "texto": texto, "nombre": nombre,
                    "wa_id": str(p.get("id", "")), "tipo": "text", "id_seleccionado": ""}

    # --- Evolution ---
    if payload.get("event") in ("messages.upsert", "MESSAGES_UPSERT") and isinstance(payload.get("data"), dict):
        d = payload["data"]
        key = d.get("key") or {}
        if key.get("fromMe"):
            return None
        tel = str(key.get("remoteJid", "")).split("@")[0]
        msg = d.get("message") or {}
        texto = msg.get("conversation") or (msg.get("extendedTextMessage") or {}).get("text") or ""
        nombre = d.get("pushName") or ""
        if tel and texto:
            return {"telefono": tel, "texto": texto, "nombre": nombre,
                    "wa_id": str(key.get("id", "")), "tipo": "text", "id_seleccionado": ""}

    return None


async def enviar_lista_interactiva(
    telefono: str,
    encabezado: str,
    cuerpo: str,
    boton: str,
    opciones: list,
    secciones: Optional[list] = None,
    cuenta_id: Optional[int] = None,
) -> dict:
    """Mensaje interactivo tipo lista (Meta Cloud API; en Telegram, botones en línea).

    opciones: [{"id": "VAC-1042", "titulo": "Cajero(a)", "descripcion": "Guadalajara · $9,500"}]
    El candidato elige y Meta responde con un list_reply; `parsear_webhook` ya extrae
    el título, y el webhook detecta el código VAC-XXXX en una segunda pasada.

    Meta limita: encabezado 60, cuerpo 1024, botón 20, título de fila 24,
    descripción 72, máximo 10 secciones y 10 filas EN TOTAL.

    `secciones` (2026-09-17, número compartido): [{"titulo": "Grupo CARBE", "opciones": [...]}] agrupa
    las filas por empresa; si se manda, `opciones` se ignora.
    """
    if _por_telegram(telefono, cuenta_id):
        return await telegram.enviar_lista(telefono, encabezado, cuerpo, opciones, secciones)
    if proveedor() != "meta":
        return _resultado(False, "Las listas interactivas solo existen en Meta Cloud API")
    if secciones:
        secciones = [sec for sec in secciones if sec.get("opciones")]
    if not opciones and not secciones:
        return _resultado(False, "No hay opciones que mostrar")

    def _filas(lista: list) -> list:
        return [
            {
                "id": str(o["id"])[:200],
                "title": str(o["titulo"])[:24],
                "description": str(o.get("descripcion") or "")[:72],
            }
            for o in lista
        ]

    if secciones:
        cupo = 10
        bloques = []
        for sec in secciones[:10]:
            if cupo <= 0:
                break
            filas = _filas(sec["opciones"][:cupo])
            cupo -= len(filas)
            bloques.append({"title": str(sec.get("titulo") or "Vacantes")[:24], "rows": filas})
    else:
        bloques = [{"title": "Vacantes", "rows": _filas(opciones[:10])}]

    return await _meta_post(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": numero_e164(telefono),
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": encabezado[:60]},
                "body": {"text": cuerpo[:1024]},
                "action": {
                    "button": boton[:20],
                    "sections": bloques,
                },
            },
        }
    )


async def enviar_con_boton(telefono: str, texto: str, boton: str, url: str, cuenta_id: Optional[int] = None) -> dict:
    """Texto con UNA liga de acción (p. ej. «Subir documentos»). Telegram: botón en línea; WhatsApp: la liga escrita."""
    if _por_telegram(telefono, cuenta_id):
        return await telegram.enviar_boton(telefono, texto, boton, url=url)
    return await enviar_mensaje(telefono, f"{texto}\n\n👉 {boton}: {url}" if url else texto, cuenta_id=cuenta_id)


async def enviar_documento(
    telefono: str, contenido: bytes, filename: str, caption: str = "", mime: str = "application/pdf", cuenta_id: Optional[int] = None,
) -> dict:
    """Manda un archivo (PDF/imagen). Telegram: sendDocument; Meta: sube el medio y lo manda como documento. Nunca
    lanza; con otro proveedor regresa no enviado."""
    if _por_telegram(telefono, cuenta_id):
        return await telegram.enviar_documento(telefono, contenido, filename, caption, mime)
    if settings.whatsapp_provider != "meta" or not (settings.meta_whatsapp_token and settings.meta_phone_number_id):
        return _resultado(False, "El envío de archivos por WhatsApp solo está disponible con WHATSAPP_PROVIDER=meta")
    try:
        async with httpx.AsyncClient(timeout=60) as cli:
            r = await cli.post(
                f"{GRAPH_URL}/{settings.meta_api_version}/{settings.meta_phone_number_id}/media",
                headers={"Authorization": f"Bearer {settings.meta_whatsapp_token}"},
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": (filename, contenido, mime)},
            )
    except Exception as e:  # red caída: no romper el flujo de RH
        return _resultado(False, f"error de red: {e}")
    if r.status_code >= 300:
        err = _meta_error(r)
        return _resultado(False, f"{err['codigo']}: {err['mensaje']}")
    media_id = (r.json() or {}).get("id", "")
    resultado = await _meta_post({
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": numero_e164(telefono), "type": "document",
        "document": {"id": media_id, "filename": filename, **({"caption": caption[:1024]} if caption else {})},
    })
    resultado["formato"] = "documento"
    return resultado
