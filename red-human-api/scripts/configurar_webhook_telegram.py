"""Registra (o revisa) el webhook del bot de Telegram (canal Telegram, 2026-10-06).

Lee TELEGRAM_BOT_TOKEN del entorno o del .env del servidor (nunca se escribe en código ni se pasa por la línea de
comandos) y llama a `setWebhook` con el MISMO secreto que valida la API (`services.telegram.secreto_webhook`).

Uso (en el servidor, desde red-human-api/):
    python scripts/configurar_webhook_telegram.py --url https://<api-publica>/api/webhooks/telegram
    python scripts/configurar_webhook_telegram.py --info      # solo muestra el estado actual
    python scripts/configurar_webhook_telegram.py --borrar    # quita el webhook
"""

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from app.config import settings  # noqa: E402
from app.services import telegram  # noqa: E402


def _api(metodo: str, **datos) -> dict:
    try:
        r = httpx.post(f"{telegram.API_URL}/bot{settings.telegram_bot_token.strip()}/{metodo}", json=datos or None, timeout=30)
        return r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "description": f"error de red: {e}"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="", help="URL pública https del webhook (…/api/webhooks/telegram)")
    ap.add_argument("--info", action="store_true", help="solo muestra getWebhookInfo")
    ap.add_argument("--borrar", action="store_true", help="elimina el webhook")
    args = ap.parse_args()

    if not telegram.activo():
        print("❌ Falta TELEGRAM_BOT_TOKEN en el entorno / .env del servidor.")
        return 1

    yo = _api("getMe")
    if not yo.get("ok"):
        print(f"❌ El token no es válido: {yo.get('description')}")
        return 1
    usuario = yo["result"].get("username")
    print(f"🤖 Bot: @{usuario} ({yo['result'].get('first_name')})")
    if telegram.usuario_bot() and telegram.usuario_bot().lower() != (usuario or "").lower():
        print(f"⚠️ TELEGRAM_BOT_USERNAME={telegram.usuario_bot()} no coincide con @{usuario}: las ligas t.me apuntarían a otro bot.")
    elif not telegram.usuario_bot():
        print(f"⚠️ Define TELEGRAM_BOT_USERNAME={usuario} para que la plataforma pueda armar las ligas t.me/{usuario}?start=…")

    if args.borrar:
        r = _api("deleteWebhook", drop_pending_updates=False)
        print("✅ Webhook eliminado" if r.get("ok") else f"❌ {r.get('description')}")
        return 0 if r.get("ok") else 1

    if not args.info:
        if not args.url.startswith("https://"):
            print("❌ Indica --url con la URL pública https del webhook (Telegram exige https).")
            return 1
        r = _api(
            "setWebhook",
            url=args.url,
            secret_token=telegram.secreto_webhook(),
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )
        if not r.get("ok"):
            print(f"❌ setWebhook rechazado: {r.get('description')}")
            return 1
        print(f"✅ Webhook registrado en {args.url}")

    info = _api("getWebhookInfo").get("result") or {}
    print(f"   url: {info.get('url') or '(ninguna)'}")
    print(f"   pendientes: {info.get('pending_update_count', 0)}")
    if info.get("last_error_message"):
        print(f"   ⚠️ último error: {info.get('last_error_message')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
