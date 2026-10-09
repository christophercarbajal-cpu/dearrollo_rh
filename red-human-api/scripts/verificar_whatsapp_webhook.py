"""Verificación del webhook de WhatsApp para el cambio de cuenta de Meta (2026-10-08). Sin red, base desechable.

- GET /webhooks/whatsapp: `hub.challenge` en TEXTO PLANO (nunca JSON) con el token de WHATSAPP_VERIFY_TOKEN (o
  META_VERIFY_TOKEN); token incorrecto → 403; sin token configurado → 503.
- POST: contesta 200 rápido aunque el procesamiento tarde (sigue en segundo plano) y deduplica reintentos por wamid.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_whatsapp_webhook.py
"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_wa_webhook_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "wa.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "META_APP_SECRET", "META_VERIFY_TOKEN",
          "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN"):
    os.environ[k] = ""
os.environ["WHATSAPP_VERIFY_TOKEN"] = "token-nuevo-de-meta"
os.environ["ADMIN_PASSWORD"] = "prueba-wa-webhook"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
import app.routers.webhooks as rw  # noqa: E402
from app.services import whatsapp as wa  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


PROCESADOS: list = []
DEMORA = {"seg": 0.0}


async def _procesar_falso(db, msg):
    await asyncio.sleep(DEMORA["seg"])
    PROCESADOS.append(msg.get("wa_id"))
    return {"ok": True, "accion": "procesado", "wamid": msg.get("wa_id")}


rw.procesar_entrante = _procesar_falso


def mensaje(wamid, texto="Hola"):
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "5215500000000", "phone_number_id": "123"},
        "contacts": [{"profile": {"name": "Persona"}, "wa_id": "5215511112222"}],
        "messages": [{"from": "5215511112222", "id": wamid, "timestamp": "1760000000", "type": "text", "text": {"body": texto}}],
    }}]}]}


with TestClient(app) as client:
    print("\n— 1. Handshake GET —")
    r = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "token-nuevo-de-meta", "hub.challenge": "1158201444"})
    check(r.status_code == 200 and r.text == "1158201444", "token correcto → 200 con el challenge EXACTO")
    check(r.headers["content-type"].startswith("text/plain"), "el challenge sale como text/plain (no JSON)")
    r = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "1"})
    check(r.status_code == 403, "token incorrecto → 403")
    r = client.get("/webhooks/whatsapp", params={"hub.mode": "unsubscribe", "hub.verify_token": "token-nuevo-de-meta", "hub.challenge": "1"})
    check(r.status_code == 403, "modo distinto de subscribe → 403")
    original = settings.meta_verify_token
    settings.meta_verify_token = ""
    r = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"})
    check(r.status_code == 503, "sin WHATSAPP_VERIFY_TOKEN → 503 (nunca verifica contra vacío)")
    settings.meta_verify_token = original

    print("\n— 2. Credenciales solo por entorno —")
    fuente = (RAIZ / "app" / "config.py").read_text(encoding="utf-8")
    check("redhuman_webhook_verify_token" not in fuente, "el verify token ya no vive en el código")
    check(wa._meta_url() == f"https://graph.facebook.com/{settings.meta_api_version}/{settings.meta_phone_number_id}/messages",
          "la URL de envío se arma con META_API_VERSION y META_PHONE_NUMBER_ID")
    check(wa._meta_headers()["Authorization"] == f"Bearer {settings.meta_whatsapp_token}", "Authorization = Bearer META_WHATSAPP_TOKEN")

    print("\n— 3. POST: 200 rápido + dedupe por wamid —")
    r = client.post("/webhooks/whatsapp", json=mensaje("wamid.A"))
    check(r.status_code == 200 and r.json().get("accion") == "procesado", "mensaje rápido → 200 con el resultado del procesamiento")
    r = client.post("/webhooks/whatsapp", json=mensaje("wamid.A"))
    check(r.status_code == 200 and r.json().get("duplicado") and PROCESADOS.count("wamid.A") == 1, "reintento de Meta (mismo wamid) → 200 sin procesar dos veces")

    DEMORA["seg"] = 1.0
    settings.whatsapp_webhook_espera_seg = 0.1
    t0 = time.monotonic()
    r = client.post("/webhooks/whatsapp", json=mensaje("wamid.LENTO"))
    tardo = time.monotonic() - t0
    check(r.status_code == 200 and r.json().get("en_proceso") and tardo < 0.8, f"procesamiento lento → 200 inmediato ({tardo:.2f} s) con en_proceso")
    r = client.post("/webhooks/whatsapp", json=mensaje("wamid.LENTO"))
    check(r.json().get("duplicado"), "reintento mientras el primero sigue en curso → duplicado")
    time.sleep(1.5)
    check(PROCESADOS.count("wamid.LENTO") == 1, "el turno lento terminó en segundo plano UNA sola vez")

    r = client.post("/webhooks/whatsapp", json={"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "statuses": [{"id": "wamid.X", "status": "delivered"}]}}]}]})
    check(r.status_code == 200 and r.json().get("ignorado"), "acuse de entrega sin mensaje → 200 ignorado")

print(f"\n🎉 Webhook de WhatsApp: {OK} verificaciones OK")
