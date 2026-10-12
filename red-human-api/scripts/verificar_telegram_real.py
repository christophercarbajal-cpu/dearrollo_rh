"""Cambios 17 y 18 (2026-10-12): Telegram por número real y cierre de la entrevista. Base desechable; la Bot API se
SIMULA (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_telegram_real.py

Cubre: C17 — al abrir el bot por primera vez (también con la liga demo) se pide «Compartir mi número» y se guarda el
número REAL; los avisos buscan el chat por ese número (y al candidato de la liga demo por su postulación); una cuenta que
es candidato Y entrevistador recibe «Aviso para el candidato» / «Aviso para el entrevistador»; «Hola» sin nada pendiente
responde el texto exacto. C18 — ya no existe «Marcar como realizada» (410) ni su estado; registrar el resultado deja la
entrevista cumplida con solo «Ver resultado»; la migración regresa «Realizada · Resultado pendiente» a «Pendiente».
"""

import asyncio
import os
import secrets
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_dir = tempfile.mkdtemp(prefix="rh_tg_real_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "tg_real.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "RESEND_API_KEY",
          "TELEGRAM_WEBHOOK_SECRET", "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID",
          "TEAMS_CLIENT_SECRET"):
    os.environ[k] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:PRUEBA-token-falso"
os.environ["TELEGRAM_BOT_USERNAME"] = "RedHumanPruebaBot"
os.environ["ADMIN_PASSWORD"] = "prueba-tg-real"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ChatTelegram, Cuenta, Evaluacion, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers import webhooks_telegram  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.services import evaluaciones as sev  # noqa: E402
from app.services import mensajeria, telegram  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
LLAMADAS = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _llamar_falso(metodo, json=None, data=None, files=None):
    LLAMADAS.append((metodo, json or data or {}))
    return {"ok": True, "result": {"message_id": len(LLAMADAS)}}


telegram.llamar = _llamar_falso
UID = iter(range(900000, 999999))


def mensaje(chat, texto=None, contact=None, nombre="Tester"):
    uid = next(UID)
    m = {"message_id": uid, "chat": {"id": chat, "type": "private"}, "from": {"id": chat, "first_name": nombre}}
    if texto is not None:
        m["text"] = texto
    if contact is not None:
        m["contact"] = contact
    return {"update_id": uid, "message": m}


def enviar(update):
    return asyncio.run(webhooks_telegram.procesar_update(update))


def enviados(chat, desde=0):
    return [str(j.get("text") or "") for (m, j) in LLAMADAS[desde:] if m == "sendMessage" and str(j.get("chat_id")) == str(chat)]


with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = True
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    admin.telefono = "5599990000"  # el número REAL de su Telegram (también se postula para probar: doble rol)
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    cuenta = Cuenta(nombre="TG Real", nombre_comercial="TG Real", estado="Activa", slug="tg-real", canal_mensajeria="ambos")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).first()
    v.cuenta_id, v.cliente_id, v.proceso, v.titulo = cuenta.id, None, {}, "Almacenista"
    db.commit()
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    c = _crear_candidato(db, cuenta.id, "Ana Real", "web", True, telefono="5551110001")  # número del FORMULARIO
    P = crear_postulacion(db, c, v, cuenta.id, "web", consentimiento=True)
    tok = telegram.asegurar_token(P)
    db.commit()
    CHAT = 515151

    # ================= C17. Pedir el número real al abrir el bot =================
    print("\n--- C17. «Compartir mi número» al abrir el bot por primera vez ---")
    desde = len(LLAMADAS)
    r = enviar(mensaje(CHAT, f"/start p_{tok}"))
    teclas = [j for (m, j) in LLAMADAS[desde:] if m == "sendMessage" and (j.get("reply_markup") or {}).get("keyboard")]
    check(r.get("accion") == "contacto_solicitado" and teclas and teclas[0]["reply_markup"]["keyboard"][0][0].get("request_contact"),
          "la liga demo pide primero «Compartir mi número» (botón nativo)")
    db.expire_all()
    ch = db.get(ChatTelegram, str(CHAT))
    check(ch.postulacion_id is None and ch.inicio_pendiente == f"p_{tok}", "la liga espera hasta recibir el número")
    desde = len(LLAMADAS)
    enviar(mensaje(CHAT, contact={"phone_number": "+52 1 55 9999 0000", "user_id": CHAT}))
    db.expire_all()
    ch = db.get(ChatTelegram, str(CHAT))
    check(ch.telefono == "5599990000" and ch.telefono_verificado, "se guarda el número REAL de su Telegram (no el del formulario)")
    check(ch.postulacion_id == P.id and len(enviados(CHAT, desde)) >= 2, "al compartirlo se abre la postulación de la liga y sigue")

    print("\n--- C17. Los avisos buscan el chat por el número real ---")
    check(telegram.chat_de_telefono("5599990000") == str(CHAT), "por su número real (entrevistador) se encuentra el chat")
    check(telegram.chat_de_telefono("5551110001") == str(CHAT), "el candidato de la liga demo se encuentra por su postulación")
    check(telegram.chat_de_telefono("5512345678") is None, "un número que nunca abrió el bot no tiene chat")
    check(mensajeria.resolver("5599990000") == "telegram", "al entrevistador que vinculó su número se le escribe por Telegram")

    print("\n--- C17. Una cuenta con ambos roles: encabezado por aviso ---")
    desde = len(LLAMADAS)
    r = client.post(f"/evaluaciones/postulaciones/{P.codigo}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "evaluador": {"tipo": "interno", "usuario_id": admin.id},
        "cita": {"fecha": "2026-12-15", "hora": "10:00", "modalidad": "Videollamada", "liga_videollamada": "https://meet.ejemplo.mx/x"}})
    check(r.status_code == 201, f"se programa la entrevista ({r.status_code})")
    COD = r.json()["evaluacion"]["codigo"]
    textos = enviados(CHAT, desde)
    para_ent = [t for t in textos if "Aviso para el entrevistador" in t]
    para_cand = [t for t in textos if "Aviso para el candidato" in t]
    check(para_ent and "/evaluacion/" in para_ent[0], "el aviso del entrevistador llega con «Aviso para el entrevistador» y su liga")
    check(para_cand and "quedó programada" in para_cand[0] and "/evaluacion/" not in para_cand[0],
          "el del candidato llega con «Aviso para el candidato» (sin la liga del entrevistador)")
    with telegram.como_rol("entrevistador"):
        solo = asyncio.run(telegram.enviar_texto("5599990000", "x"))
    otro = 626262
    db.add(ChatTelegram(chat_id=str(otro), telefono="5544440000", telefono_verificado=True))
    db.commit()
    desde = len(LLAMADAS)
    with telegram.como_rol("candidato"):
        asyncio.run(telegram.enviar_texto("5544440000", "Solo candidato"))
    check(solo["enviado"] and enviados(otro, desde) == ["Solo candidato"], "con un solo rol no se agrega encabezado")

    print("\n--- C17. «Hola» sin actividad pendiente ---")
    eva = Usuario(correo="eva@tgreal.mx", nombre="Eva Entrevistadora", rol="Usuario", activo=True, hash_pass="x", telefono="5577770000")
    db.add(eva)
    db.commit()
    EVA = 717171
    enviar(mensaje(EVA, contact={"phone_number": "5577770000", "user_id": EVA}, nombre="Eva"))
    desde = len(LLAMADAS)
    r = enviar(mensaje(EVA, "Hola"))
    check(r.get("accion") == "sin_actividad" and enviados(EVA, desde) == [
        "Hola, Eva. No tienes ninguna actividad pendiente por ahora. Te escribiré aquí cuando haya novedades."],
          "entrevistador sin citas: «Hola, [nombre]. No tienes ninguna actividad pendiente…» (texto exacto)")
    NUEVO = 818181
    enviar(mensaje(NUEVO, contact={"phone_number": "5566660000", "user_id": NUEVO}))
    desde = len(LLAMADAS)
    r = enviar(mensaje(NUEVO, "Hola"))
    check(r.get("accion") != "sin_actividad", "un número nuevo (quiere postularse) sigue el flujo normal")
    desde = len(LLAMADAS)
    r = enviar(mensaje(CHAT, "Hola"))
    check(r.get("accion") != "sin_actividad", "con una postulación o una cita pendiente no se responde «sin actividad»")

    # ================= C18. Cierre de la entrevista =================
    print("\n--- C18. Sin «Marcar como realizada»; registrar el resultado la cumple ---")
    ev = next(x for x in client.get(f"/evaluaciones/postulaciones/{P.codigo}").json() if x["codigo"] == COD)
    check("marcar_realizada" not in str(ev["acciones"]), "la tarjeta ya no ofrece «Marcar como realizada»")
    r = client.post(f"/evaluaciones/{COD}/realizada")
    check(r.status_code == 410, "el endpoint de «Marcar como realizada» ya no existe (410)")
    r = client.post(f"/evaluaciones/{COD}/resultado", data={"conclusion": "avanzar", "comentarios": "Bien", "realizada_por": "Admin", "version": "0"})
    check(r.status_code == 200, f"«Registrar resultado / Adjuntar reporte» ({r.status_code} {r.text[:120]})")
    ev = next(x for x in client.get(f"/evaluaciones/postulaciones/{P.codigo}").json() if x["codigo"] == COD)
    check(ev["estado"] == "con_resultado" and ev["acciones"]["principal"] == "ver_resultado" and not ev["acciones"]["secundaria"]
          and "marcar_revisada" not in ev["acciones"]["menu"], "queda cumplida y solo muestra «Ver resultado»")

    print("\n--- C18. Migración «Realizada · Resultado pendiente» → «Pendiente» ---")
    r = client.post(f"/evaluaciones/postulaciones/{P.codigo}", json={"tipo": "tecnica", "forma": "registro_directo"})
    vieja = db.query(Evaluacion).filter_by(codigo=r.json()["evaluacion"]["codigo"]).one()
    vieja.estado = "realizada_sin_resultado"  # como quedaron las marcadas antes del Cambio 18
    db.commit()
    s = sev.migrar_realizadas_sin_resultado(db, aplicar=False)
    db.expire_all()
    check(s["encontradas"] == 1 and db.get(Evaluacion, vieja.id).estado == "realizada_sin_resultado", "simulación: solo cuenta, no escribe")
    s = sev.migrar_realizadas_sin_resultado(db, aplicar=True)
    db.expire_all()
    check(s["migradas"] == 1 and db.get(Evaluacion, vieja.id).estado == "pendiente", "con --forzar regresa a «Pendiente»")
    check(sev.migrar_realizadas_sin_resultado(db, aplicar=True)["migradas"] == 0, "idempotente")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — Telegram por número real y cierre de la entrevista (C17, C18)")
