"""Telegram demo, aislamiento de ligas y multipostulación (2026-10-11, Bloque 3). Base desechable; la Bot API y Meta se
SIMULAN (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_telegram_demo.py

Cubre: con Modo Prueba (o Cuenta demo) «Continuar en Telegram» existe en cualquier Cuenta y la liga `p_<token>` se abre
desde cualquier cuenta de Telegram sin validar el número; la liga abre EXACTAMENTE esa postulación (el chat se amarra a
ella, reemplaza lo anterior y no se mezcla con otra persona de teléfono repetido); con Modo Prueba apagado se sigue
validando el número; y un candidato real con varias postulaciones activas recibe «¿Sobre qué vacante me escribes?».
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_dir = tempfile.mkdtemp(prefix="rh_tg_demo_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "tg_demo.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "RESEND_API_KEY",
          "TELEGRAM_WEBHOOK_SECRET", "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD"):
    os.environ[k] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:PRUEBA-token-falso"
os.environ["TELEGRAM_BOT_USERNAME"] = "RedHumanPruebaBot"
os.environ["ADMIN_PASSWORD"] = "prueba-tg-demo"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Bitacora, ChatTelegram, Cuenta, Mensaje, Postulacion, Vacante  # noqa: E402
from app.routers import webhooks, webhooks_telegram  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion, fijar_conversacion  # noqa: E402
from app.services import mensajeria, telegram  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
LLAMADAS, LISTAS = [], []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _llamar_falso(metodo, json=None, data=None, files=None):
    LLAMADAS.append((metodo, json or {}))
    return {"ok": True, "result": {"message_id": len(LLAMADAS)}}


async def _lista_falsa(telefono, encabezado, cuerpo, boton, opciones, *a, **k):
    LISTAS.append({"telefono": telefono, "cuerpo": cuerpo, "opciones": [o["id"] for o in opciones]})
    return {"enviado": True, "proveedor": "meta", "detalle": "ok"}


telegram.llamar = _llamar_falso
webhooks.enviar_lista_interactiva = _lista_falsa

UID = iter(range(700000, 800000))


def start(chat, payload):
    uid = next(UID)
    return {"update_id": uid, "message": {"message_id": uid, "chat": {"id": chat, "type": "private"},
                                          "from": {"id": chat, "first_name": "Tester"}, "text": f"/start {payload}".strip()}}


def texto(chat, t):
    uid = next(UID)
    return {"update_id": uid, "message": {"message_id": uid, "chat": {"id": chat, "type": "private"},
                                          "from": {"id": chat, "first_name": "Tester"}, "text": t}}


def enviar(update):
    return asyncio.run(webhooks_telegram.procesar_update(update))


def enviados(chat, desde=0):
    return [j for (m, j) in LLAMADAS[desde:] if m == "sendMessage" and str(j.get("chat_id")) == str(chat)]


with TestClient(app) as client:
    db = SessionLocal()
    cfg = obtener(db)
    cfg.modo_prueba = True
    # Cuenta SOLO WhatsApp (real): en Modo Prueba igual ofrece Telegram
    cuenta = Cuenta(nombre="Solo WA", nombre_comercial="Solo WA", estado="Activa", slug="solo-wa", canal_mensajeria="whatsapp")
    db.add(cuenta)
    db.flush()
    vacs = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).limit(3).all()
    for x in vacs:
        x.cuenta_id = cuenta.id
    db.commit()
    v1, v2, v3 = vacs

    def nueva(nombre, tel, vac):
        c = _crear_candidato(db, cuenta.id, nombre, "web", True, telefono=tel)
        p = crear_postulacion(db, c, vac, cuenta.id, "web", consentimiento=True)
        db.commit()
        return p

    # ================= 1. «Continuar en Telegram» desde cualquier Cuenta =================
    print("\n--- 1. Telegram demo: disponible en cualquier Cuenta ---")
    check(mensajeria.canales_publicos(v1, db)["canalCandidatos"] == "ambos" and mensajeria.canales_publicos(v1, db)["telegramLiga"],
          "Modo Prueba: una Cuenta solo WhatsApp también ofrece «Continuar en Telegram»")
    cfg.modo_prueba = False
    db.commit()
    check(mensajeria.canales_publicos(v1, db)["canalCandidatos"] == "whatsapp", "Modo Prueba apagado: la Cuenta real conserva su canal")
    cfg.modo_prueba = True
    db.commit()

    # ================= 2. La liga abre esa postulación sin validar el número =================
    print("\n--- 2. Liga p_<token> desde cualquier cuenta de Telegram ---")
    P1 = nueva("Ana Demo", "5551110001", v1)
    tok1 = telegram.asegurar_token(P1)
    db.commit()
    CHAT = 424242  # cuenta de Telegram del probador: nunca compartió número y su número no es el de la postulación
    desde = len(LLAMADAS)
    r = enviar(start(CHAT, f"p_{tok1}"))
    db.expire_all()
    ch = db.get(ChatTelegram, str(CHAT))
    check(not any(j.get("reply_markup", {}).get("keyboard") for j in enviados(CHAT, desde)), "no pide «Compartir mi número»")
    check(ch.postulacion_id == P1.id and ch.telefono == "5551110001", "el chat queda amarrado a ESA postulación")
    check(enviados(CHAT, desde) and r.get("postulacion") in (None, P1.codigo) and "otro número" not in str(enviados(CHAT, desde)),
          "responde desde la postulación (sin «Esta liga pertenece a otro número»)")
    antes = db.query(Mensaje).filter(Mensaje.postulacion_id == P1.id).count()
    enviar(texto(CHAT, "Hola, sigo aquí"))
    db.expire_all()
    check(db.query(Mensaje).filter(Mensaje.postulacion_id == P1.id).count() > antes, "lo que escribe después se procesa en esa postulación")

    # ================= 3. Aislamiento: otra liga reemplaza y no mezcla =================
    print("\n--- 3. Otra liga en el mismo chat: reemplaza y empieza de cero ---")
    P2 = nueva("Beto Demo", "5551110002", v2)
    tok2 = telegram.asegurar_token(P2)
    db.commit()
    enviar(start(CHAT, f"p_{tok2}"))
    db.expire_all()
    ch = db.get(ChatTelegram, str(CHAT))
    check(ch.postulacion_id == P2.id and ch.telefono == "5551110002", "el chat ahora atiende SOLO la postulación nueva")
    b = db.query(Bitacora).filter(Bitacora.accion == "telegram_demo_vinculado", Bitacora.entidad_id == P2.codigo).first()
    check(b is not None and "reemplaza" in (b.detalle or ""), "queda en bitácora qué atendía antes el chat")
    n1 = db.query(Mensaje).filter(Mensaje.postulacion_id == P1.id).count()
    enviar(texto(CHAT, "Hola"))
    db.expire_all()
    check(db.query(Mensaje).filter(Mensaje.postulacion_id == P1.id).count() == n1
          and db.query(Mensaje).filter(Mensaje.postulacion_id == P2.id).count() > 0, "nada se mezcla con la postulación anterior")
    # misma persona de prueba con teléfono REPETIDO (Modo Prueba lo permite): la liga manda, no el teléfono
    P3 = nueva("Ana Duplicada", "5551110002", v3)
    enviar(texto(CHAT, "¿Qué sigue?"))
    db.expire_all()
    check(db.query(Mensaje).filter(Mensaje.postulacion_id == P3.id).count() == 0,
          "con teléfono repetido en Modo Prueba, el chat amarrado nunca contesta en la otra postulación")

    # ================= 4. Modo Prueba apagado: se valida el número =================
    print("\n--- 4. Cuenta real con Modo Prueba apagado ---")
    cfg.modo_prueba = False
    db.commit()
    OTRO = 535353
    desde = len(LLAMADAS)
    enviar(start(OTRO, f"p_{tok1}"))
    db.expire_all()
    ch2 = db.get(ChatTelegram, str(OTRO))
    check(ch2 is not None and not ch2.postulacion_id and any(j.get("reply_markup", {}).get("keyboard") for j in enviados(OTRO, desde)),
          "sin Modo Prueba la liga sigue pidiendo el número (no se amarra)")

    # ================= 5. Multipostulación: «¿Sobre qué vacante me escribes?» =================
    print("\n--- 5. Candidato real con varias postulaciones activas ---")
    cuenta.canal_mensajeria = "ambos"
    db.commit()
    c = _crear_candidato(db, cuenta.id, "Carla Real", "web", True, telefono="5551110009")
    q1 = crear_postulacion(db, c, v1, cuenta.id, "web", consentimiento=True)
    q2 = crear_postulacion(db, c, v2, cuenta.id, "web", consentimiento=True)
    for q in (q1, q2):
        q.prefiltro_completo = True
    c.postulacion_conversacion_id = None
    db.commit()
    LISTAS.clear()
    msg = {"telefono": "525551110009", "texto": "Hola, ¿cómo va mi proceso?", "nombre": "Carla", "wa_id": "wamid.multi1", "tipo": "text",
           "media": None, "id_seleccionado": "", "numero_receptor": "", "canal": "whatsapp"}
    r = asyncio.run(webhooks.procesar_entrante(db, msg))
    check(r.get("accion") == "elegir_postulacion" and LISTAS and "¿Sobre qué vacante me escribes?" in LISTAS[-1]["cuerpo"]
          and set(LISTAS[-1]["opciones"]) == {q1.codigo, q2.codigo}, "pregunta «¿Sobre qué vacante me escribes?» con sus vacantes")
    r = asyncio.run(webhooks.procesar_entrante(db, {**msg, "texto": "Almacén", "id_seleccionado": q2.codigo, "wa_id": "wamid.multi2"}))
    db.expire_all()
    check(r.get("postulacion") == q2.codigo or db.get(Postulacion, q2.id).candidato.postulacion_conversacion_id == q2.id,
          "al elegir, la conversación queda en esa postulación")
    LISTAS.clear()
    asyncio.run(webhooks.procesar_entrante(db, {**msg, "texto": "Gracias", "wa_id": "wamid.multi3"}))
    check(not LISTAS, "ya elegida, no se vuelve a preguntar en cada mensaje")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — Telegram demo, aislamiento y multipostulación")
