"""Verificación del canal TELEGRAM (2026-10-06) — solo infraestructura, conectada al proceso configurable.

Recorre con la Bot API SIMULADA (sin red; `telegram.llamar` se reemplaza por un falso que registra las llamadas):
- webhook: secreto `X-Telegram-Bot-Api-Secret-Token` (403), sin token (503), dedupe PERSISTENTE de `update_id`;
- normalización: /start, contacto (propio / ajeno), botón (callback_query), foto, documento;
- identidad: «Compartir mi número» → `chats_telegram` (teléfono a 10 dígitos); la plataforma sigue por teléfono;
- el mensaje normalizado entra a la MISMA lógica que WhatsApp: menú → aviso de privacidad → consentimiento → prefiltro,
  con el proceso de la vacante congelado en la postulación;
- canal por Cuenta (`canal_mensajeria`): whatsapp / telegram / ambos para envíos automáticos (texto, solicitud de
  documentos, listas, archivos) y alcance de cada webhook;
- deep links: `vac_<VAC>`, `p_<token>` (amarrado al teléfono de la postulación) y `p_<token>_<paso>` → la respuesta
  la decide el proceso (paso no disponible / consentimiento médico con su liga);
- /postular en una Cuenta de Telegram entrega «Continuar en Telegram» y no intenta la plantilla de WhatsApp.
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_telegram.py
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_tg_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "tg.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY",
          "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET", "TELEGRAM_WEBHOOK_SECRET"):
    os.environ[k] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:PRUEBA-token-falso"
os.environ["TELEGRAM_BOT_USERNAME"] = "RedHumanPruebaBot"
os.environ["ADMIN_PASSWORD"] = "prueba-tg"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_admin, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Bitacora, Candidato, ChatTelegram, ConfiguracionSistema, Cuenta, Evaluacion, Mensaje, Postulacion, UpdateTelegram, Usuario, UsuarioCuenta,
)
from app.services import mensajeria, telegram  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp as swa  # noqa: E402

OK = 0
LLAMADAS = []  # (metodo, json, data, files)


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _llamar_falso(metodo, json=None, data=None, files=None):
    LLAMADAS.append((metodo, json or {}, data or {}, files or {}))
    return {"ok": True, "result": {"message_id": len(LLAMADAS), "file_path": "photos/file_1.jpg"}}


_llamar_real = telegram.llamar
telegram.llamar = _llamar_falso


def enviados(chat):
    return [j for (m, j, d, f) in LLAMADAS if m == "sendMessage" and str(j.get("chat_id")) == str(chat)]


def ultimo(chat):
    lista = enviados(chat)
    return lista[-1] if lista else {}


UID = iter(range(500000, 600000))
SECRETO = telegram.secreto_webhook()


def mensaje(chat, uid=None, nombre="Ana", **campos):
    uid = uid or next(UID)
    return {"update_id": uid, "message": {"message_id": uid, "chat": {"id": chat, "type": "private"},
                                          "from": {"id": chat, "first_name": nombre, "last_name": "Telegram"}, **campos}}


def boton(chat, dato, texto="Opción"):
    uid = next(UID)
    return {"update_id": uid, "callback_query": {"id": f"cb{uid}", "from": {"id": chat}, "data": dato,
                                                 "message": {"chat": {"id": chat, "type": "private"},
                                                             "reply_markup": {"inline_keyboard": [[{"text": texto, "callback_data": dato}]]}}}}


with TestClient(app) as client:
    def tg(cuerpo, secreto=SECRETO):
        return client.post("/api/webhooks/telegram", json=cuerpo, headers={"X-Telegram-Bot-Api-Secret-Token": secreto})

    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta Telegram", nombre_comercial="Logística TG", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    cfg = db.query(ConfiguracionSistema).first()
    if cfg:
        cfg.modo_prueba = False
    db.commit()
    for dep in (usuario_actual, usuario_decisor, usuario_admin):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    def post(codigo):
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=codigo).one()

    # ================= 0. Configuración por Cuenta =================
    print("\n--- 0. Canal activo por Cuenta ---")
    r = client.patch(f"/cuentas/{cuenta.id}", json={"canal_mensajeria": "fax"})
    check(r.status_code == 400, "un canal desconocido se rechaza (400)")
    r = client.patch(f"/cuentas/{cuenta.id}", json={"canal_mensajeria": "telegram"})
    check(r.status_code == 200, "la Cuenta elige Telegram")
    ficha = client.get(f"/cuentas/{cuenta.id}").json()
    check(ficha["canalMensajeria"] == "telegram" and ficha["telegramDisponible"] is True, "la ficha expone canal y disponibilidad del bot")
    pl = client.post("/procesos/plantillas/ejemplo/paralelo_medica_socioeconomica").json()
    r = client.post("/vacantes", json={
        "titulo": "Operador de almacén", "descripcion": "Descripción", "generar_si_falta": False, "publicar": True,
        "ubicacion_estado": "Jalisco", "ubicacion_municipio": "Zapopan", "sueldo_desde": 9000, "sueldo_hasta": 11000,
        "sueldo_periodicidad": "mensual", "proceso": {"plantilla_id": pl["id"]},
    })
    assert r.status_code == 201, r.text
    VAC = r.json()["id"]

    # ================= 1. Webhook: seguridad y dedupe =================
    print("\n--- 1. Webhook ---")
    check(tg(mensaje(7001, text="hola"), secreto="otro").status_code == 403, "secreto inválido → 403")
    token = settings.telegram_bot_token
    settings.telegram_bot_token = ""
    check(tg(mensaje(7001, text="hola")).status_code == 503, "sin TELEGRAM_BOT_TOKEN → 503")
    settings.telegram_bot_token = token
    r = tg(mensaje(7001, uid=424242, text="/start"))
    check(r.status_code == 200 and r.json() == {"ok": True}, "200 inmediato y proceso en segundo plano")
    n = len(LLAMADAS)
    r = tg(mensaje(7001, uid=424242, text="/start"))
    check(r.json().get("duplicado") is True and len(LLAMADAS) == n, "update_id repetido → duplicado, no se procesa otra vez")
    db.expire_all()
    check(db.get(UpdateTelegram, 424242) is not None, "la deduplicación es persistente (updates_telegram)")

    # ================= 2. Identidad: contacto nativo =================
    print("\n--- 2. Compartir mi número ---")
    teclado = ultimo(7001).get("reply_markup", {})
    check(teclado.get("keyboard", [[{}]])[0][0].get("request_contact") is True, "/start sin número → botón nativo «Compartir mi número»")
    tg(mensaje(7001, contact={"phone_number": "+5215541000009", "user_id": 99999}))
    db.expire_all()
    check(not db.get(ChatTelegram, "7001").telefono and "tu" in ultimo(7001)["text"].lower(), "contacto AJENO → no se guarda y se vuelve a pedir")
    tg(mensaje(7001, contact={"phone_number": "+52 1 55 4100 0001", "user_id": 7001}))
    db.expire_all()
    check(db.get(ChatTelegram, "7001").telefono == "5541000001", "contacto propio → chats_telegram con teléfono a 10 dígitos")
    menu = ultimo(7001)
    ids = [b["callback_data"] for fila in menu.get("reply_markup", {}).get("inline_keyboard", []) for b in fila]
    check(ids == [VAC], "tras el contacto arranca la MISMA lógica de WhatsApp: menú (solo vacantes de Cuentas con Telegram)")

    # ================= 3. Botón → aviso → consentimiento → prefiltro =================
    print("\n--- 3. Selección, privacidad y prefiltro ---")
    tg(boton(7001, VAC, "Operador de almacén"))
    check(any(m == "answerCallbackQuery" for (m, *_x) in LLAMADAS), "el botón se confirma con answerCallbackQuery")
    check("Aviso de Privacidad" in ultimo(7001)["text"], "elegir la vacante manda el aviso de privacidad (nunca es consentimiento)")
    db.expire_all()
    persona = db.query(Candidato).filter_by(telefono="5541000001", cuenta_id=cuenta.id).one()
    p = next(x for x in persona.postulaciones if x.vacante and x.vacante.codigo == VAC)
    P = p.codigo
    check(persona.fuente == "Telegram" and p.origen == "telegram" and not p.consentimiento, "persona nueva (fuente Telegram) y postulación con origen telegram")
    check(sproc.tiene_proceso(p), "la postulación congela el proceso configurado de la vacante")
    tg(mensaje(7001, text="Sí, acepto"))
    p = post(P)
    check(p.consentimiento and p.mensajes, "«Sí» explícito → consentimiento y arranca el prefiltro")
    canales = {m.canal for m in p.mensajes}
    check(canales == {"telegram"}, "el historial registra el canal real (telegram)")

    # ================= 4. Envíos automáticos según el canal de la Cuenta =================
    print("\n--- 4. Fachada de envío ---")

    async def envios():
        foto = []
        with mensajeria.de_cuenta(cuenta.id):
            a = await swa.enviar_mensaje("5541000001", "Recordatorio *importante*")
            foto.append(ultimo(7001).get("text"))
            b = await swa.enviar_plantilla_documentos("5541000001", {"nombre": "Ana"}, "Sube tus documentos", nivel=2)
            foto.append(ultimo(7001).get("text"))
            c = await swa.enviar_mensaje("5549999999", "A quien nunca habló con el bot")
            d = await swa.enviar_documento("5541000001", b"%PDF-1.4 prueba", "carta.pdf", "Tu carta")
            e = await swa.enviar_con_boton("5541000001", "Tu expediente", "Subir documentos", "https://app.redhuman.mx/expediente/x")
            foto.append(ultimo(7001).get("reply_markup"))
        f = await swa.enviar_mensaje("5541000001", "Sin Cuenta marcada: se infiere de la persona")
        return a, b, c, d, e, f, foto

    a, b, c, d, e, f, foto = asyncio.run(envios())
    check(a["enviado"] and a["proveedor"] == "telegram" and foto[0] == "Recordatorio <b>importante</b>",
          "texto → Telegram, con *negritas* convertidas a HTML")
    check(b["proveedor"] == "telegram" and foto[1] == "Sube tus documentos", "solicitud de documentos (plantilla) → su texto por Telegram")
    check(not c["enviado"] and c.get("sin_chat"), "número sin chat → no enviado (sin_chat), sin excepción")
    doc = [x for x in LLAMADAS if x[0] == "sendDocument"][-1]
    check(d["enviado"] and doc[2]["chat_id"] == "7001" and doc[3]["document"][0] == "carta.pdf", "enviar_documento → sendDocument multipart")
    check(foto[2]["inline_keyboard"][0][0]["url"].endswith("/expediente/x"), "liga de acción → botón en línea")
    check(f["proveedor"] == "telegram", "sin cuenta_id el canal se infiere de la persona (candidato de esa Cuenta)")

    # ================= 5. Alcance de cada webhook y modo «ambos» =================
    print("\n--- 5. Alcance y «ambos» ---")
    wa = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": "Ana"}, "wa_id": "5215541000001"}],
        "messages": [{"from": "5215541000001", "id": "wamid.tg.1", "type": "text", "text": {"body": "Hola"}}]}}]}]}
    client.post("/webhooks/whatsapp", json=wa)
    db.expire_all()
    check(db.query(Candidato).filter_by(telefono="5541000001", cuenta_id=cuenta.id).count() == 1
          and all(m.canal == "telegram" for m in post(P).mensajes), "un WhatsApp no entra a una Cuenta «solo Telegram»")
    cuenta.canal_mensajeria = "ambos"
    db.commit()
    check(mensajeria.resolver("5541000001", cuenta.id) == "telegram", "«ambos»: sale por el canal por el que escribió al último (Telegram)")
    wa["entry"][0]["changes"][0]["value"]["messages"][0]["id"] = "wamid.tg.2"
    client.post("/webhooks/whatsapp", json=wa)
    check(mensajeria.resolver("5541000001", cuenta.id) == "whatsapp", "«ambos»: si luego escribe por WhatsApp, se le responde por WhatsApp")
    cuenta.canal_mensajeria = "telegram"
    db.commit()
    check(mensajeria.resolver("5541000001", cuenta.id) == "telegram", "«telegram»: siempre Telegram")

    # ================= 6. Deep links del proceso =================
    print("\n--- 6. Deep links al proceso configurable ---")
    s = client.get(f"/procesos/postulaciones/{P}").json()
    liga = s["telegram"]["liga"]
    check(s["telegram"]["disponible"] and liga.startswith("https://t.me/RedHumanPruebaBot?start=p_") and "medica" in s["telegram"]["pasos"],
          "el seguimiento entrega la liga de Telegram de la postulación y de cada paso")
    payload = liga.split("start=")[1]
    tg(mensaje(7002, nombre="Beto", text="/start"))
    tg(mensaje(7002, nombre="Beto", contact={"phone_number": "5541000002", "user_id": 7002}))
    tg(mensaje(7002, nombre="Beto", text=f"/start {payload}"))
    db.expire_all()
    check("otro número" in ultimo(7002)["text"] and db.query(Bitacora).filter_by(accion="telegram_liga_rechazada").count() == 1,
          "la liga de una postulación solo sirve desde el chat con SU teléfono")
    tg(mensaje(7001, text=f"/start {s['telegram']['pasos']['medica'].split('start=')[1]}"))
    check("todavía no está disponible" in ultimo(7001)["text"], "paso aún no disponible → el proceso dice qué falta")
    p = post(P)
    p.prefiltro_completo, p.estado = True, "cumple"
    db.commit()
    check(client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Entrevista Humana"}).status_code == 200, "avanza a Filtro humano")
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "medica", "forma": "registro_directo", "paso_id": "medica"})
    assert r.status_code == 201, r.text
    n_msgs = len(post(P).mensajes)
    tg(mensaje(7001, text=f"/start {s['telegram']['pasos']['medica'].split('start=')[1]}"))
    tecla = ultimo(7001).get("reply_markup", {}).get("inline_keyboard", [[{}]])[0][0]
    check("consentimiento" in ultimo(7001)["text"].lower() and "/consentimiento/" in tecla.get("url", ""),
          "paso médico disponible → botón a la liga de consentimiento (lo decide el proceso)")
    check(len(post(P).mensajes) == n_msgs + 1 and post(P).mensajes[-1].canal == "telegram", "la respuesta queda en el historial de la postulación")
    tg(mensaje(7001, text=f"/start {payload}"))
    check("consentimiento" in ultimo(7001)["text"].lower(), "/start p_<token> → siguiente acción del candidato según su proceso")
    tg(mensaje(7001, photo=[{"file_id": "F1", "file_size": 10}, {"file_id": "F2", "file_size": 99}]))
    check("solo puedo leer mensajes de texto" in ultimo(7001)["text"], "foto fuera de Contratación/Onboarding → aviso (misma regla que WhatsApp)")

    # entrar por la vacante antes de compartir el número: el payload espera en la base
    tg(mensaje(7003, nombre="Caro", text=f"/start vac_{VAC}"))
    db.expire_all()
    check(db.get(ChatTelegram, "7003").inicio_pendiente == f"vac_{VAC}", "/start vac_<VAC> sin número → el payload espera en chats_telegram")
    tg(mensaje(7003, nombre="Caro", contact={"phone_number": "5541000003", "user_id": 7003}))
    check("Aviso de Privacidad" in ultimo(7003)["text"] and "Operador de almacén" in ultimo(7003)["text"],
          "al compartir el número entra directo a esa vacante (aviso de privacidad)")

    # ================= 7. Portal y cliente HTTP =================
    print("\n--- 7. /postular y cliente HTTP ---")
    slug = client.get(f"/vacantes/{VAC}").json().get("slug")
    r = client.post("/candidatos/postular", data={"vacante": slug or VAC, "nombre": "Dana Portal", "telefono": "5541000004", "correo": "dana@tg.mx", "consentimiento": "true"})
    assert r.status_code == 201, r.text
    check(r.json()["telegram"].startswith("https://t.me/RedHumanPruebaBot?start=p_"), "/postular en Cuenta de Telegram → «Continuar en Telegram»")
    db.expire_all()
    pd = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    check(not any("Plantilla de WhatsApp" in m.texto or "Fallo de envío Meta" in m.texto for m in pd.mensajes),
          "con «solo Telegram» no se intenta la plantilla de WhatsApp")
    check(telegram.separar_inicio("p_0123456789abcdef_medica") == ("p", "0123456789abcdef", "medica")
          and telegram.separar_inicio("p_xyz") == ("", "", "") and telegram.separar_inicio("vac_VAC-1") == ("vac", "VAC-1", ""),
          "payloads de /start: postulación, paso y vacante; basura → menú")
    # 2026-10-06: canal de la Cuenta en el portal + /start vac_<VAC> que nunca se queda pasmado
    d = r.json()
    check(d.get("canalCandidatos") == "telegram" and d.get("telegramBotUsername") == "RedHumanPruebaBot"
          and d.get("telegramLiga") == f"https://t.me/RedHumanPruebaBot?start=vac_{VAC}" and not d.get("whatsappLiga"),
          "/postular trae canalCandidatos + TELEGRAM_BOT_USERNAME + liga vac_<VAC> (sin WhatsApp en «solo Telegram»)")
    det = client.get(f"/vacantes/slug/{slug}").json() if slug else {}
    check(det.get("canalCandidatos") == "telegram" and det.get("telegramLiga", "").endswith(f"vac_{VAC}"),
          "el detalle público de la vacante trae el canal de la Cuenta")
    publicas = client.get(f"/vacantes/publicas?cuenta_id={cuenta.id}").json()
    check(publicas and all(v["cuentaId"] == cuenta.id for v in publicas), "/vacantes/publicas?cuenta_id= aísla la bolsa a esa Cuenta")
    n_post = db.query(Postulacion).count()
    tg(mensaje(7004, nombre="Dana", text=f"/start vac_{VAC}"))
    tg(mensaje(7004, nombre="Dana", contact={"phone_number": "5541000004", "user_id": 7004}))
    db.expire_all()
    check(db.query(Postulacion).count() == n_post, "vac_<VAC> tras postularse en el portal retoma SU postulación (no duplica)")
    texto_dana = ultimo(7004)["text"]
    check(bool(texto_dana.strip()) and "Esta liga" not in texto_dana and "Por ahora no estamos" not in texto_dana,
          "vac_<VAC> con postulación del portal → el agente sigue el proceso (no se queda pasmado)")
    check(not any(m.rol == "user" and m.texto.strip().upper() == VAC for m in pd.mensajes),
          "el código de la vacante nunca entra como respuesta del candidato")
    tg(mensaje(7004, nombre="Dana", text="/start vac_VAC-9999"))
    check(any("ya no está disponible" in (e.get("text") or "") for e in enviados(7004)[-3:]),
          "vac_ de una vacante inexistente → aviso + menú (nunca silencio)")
    # 2026-10-07: AMBIENTE_PRUEBA=true → Telegram es el ÚNICO canal (desarrollo), aunque la Cuenta diga WhatsApp
    from app.services import mensajeria
    from app.models import Vacante as _V

    cuenta.canal_mensajeria = "whatsapp"
    db.commit()
    vac_obj = db.query(_V).filter_by(codigo=VAC).one()
    check(mensajeria.canales_publicos(vac_obj)["canalCandidatos"] == "whatsapp" and mensajeria.resolver("5541000004", cuenta.id) == "whatsapp",
          "sin AMBIENTE_PRUEBA una Cuenta de WhatsApp sigue por WhatsApp")
    settings.ambiente_prueba = True
    cp = mensajeria.canales_publicos(vac_obj)
    check(cp["canalCandidatos"] == "telegram" and cp["telegramLiga"].endswith(f"vac_{VAC}") and not cp["whatsappLiga"],
          "AMBIENTE_PRUEBA: el portal manda a Telegram aunque la Cuenta tenga WhatsApp")
    check(mensajeria.resolver("5541000004", cuenta.id) == "telegram" and mensajeria.canal_de_cuenta(db, cuenta.id) == "telegram",
          "AMBIENTE_PRUEBA: todo envío al candidato sale por Telegram")
    check(mensajeria.alcance_canal([cuenta], "whatsapp") == [] and mensajeria.alcance_canal([cuenta], "telegram") == [cuenta],
          "AMBIENTE_PRUEBA: el webhook de WhatsApp no atiende a nadie; Telegram atiende a todas las Cuentas")
    r = client.post("/candidatos/postular", data={"vacante": slug or VAC, "nombre": "Elsa Dev", "telefono": "5541000009", "correo": "elsa@tg.mx", "consentimiento": "true"})
    db.expire_all()
    pe = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    check(r.json()["telegram"].startswith("https://t.me/RedHumanPruebaBot?start=p_")
          and not any("Plantilla de WhatsApp" in m.texto or "Fallo de envío Meta" in m.texto for m in pe.mensajes),
          "AMBIENTE_PRUEBA: /postular entrega la liga de Telegram y no intenta la plantilla de WhatsApp")
    settings.ambiente_prueba = False
    telegram.llamar = _llamar_real
    telegram.API_URL = "http://127.0.0.1:9"
    r = asyncio.run(telegram.llamar("getMe"))
    check(r.get("ok") is False and "error de red" in r.get("description", ""), "llamar() con la red caída regresa {ok: False}, nunca lanza")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — canal Telegram")
