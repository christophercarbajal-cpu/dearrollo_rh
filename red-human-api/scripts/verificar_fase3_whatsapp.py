"""Verificación FASE 3 (2026-09-15): reanudar/reagendar Entrevista IA por WhatsApp y cron de recordatorios
de documentos con fecha límite. Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_fase3_whatsapp.py
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_f3_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "f3.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-f3"
os.environ["SEMBRAR_DEMO"] = "true"  # los scripts de verificación sí usan los datos de ejemplo

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cuenta, Entrevista, Expediente, NotificacionEnviada, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import notificaciones as notif  # noqa: E402
from app.services import recordatorios  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402
import app.routers.candidatos as rc  # noqa: E402
import app.routers.webhooks as rw  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


ENVIADOS = []


async def _fake_wa(telefono, texto):
    ENVIADOS.append({"telefono": telefono, "texto": texto})
    return {"enviado": True, "proveedor": "meta", "detalle": 200}


rc.enviar_mensaje = _fake_wa
rw.enviar_mensaje = _fake_wa
notif.enviar_mensaje = _fake_wa
from app.services import whatsapp as _wa  # noqa: E402
_wa.enviar_mensaje = _fake_wa  # la plantilla de documentos cae aquí en modo demo


def webhook(client, tel, texto):
    return client.post("/webhooks/whatsapp", json={"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": "Karla"}, "wa_id": tel}],
        "messages": [{"from": tel, "id": f"wamid.{len(ENVIADOS)}", "type": "text", "text": {"body": texto}}],
    }}]}]})


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta F3", nombre_comercial="F3", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta.id
        for p in c.postulaciones:
            p.cuenta_id = cuenta.id
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    r = client.post("/vacantes", json={"titulo": "Cajero F3", "descripcion": "x", "generar_si_falta": False})
    VAC = r.json()["id"]

    # ================= Entrevista IA interrumpida → reanudar por WhatsApp =================
    print("\n--- Entrevista IA interrumpida: reanudar / reagendar por WhatsApp ---")
    TEL = "5215511122233"
    r = client.post("/candidatos", json={"nombre": "Karla Demo", "telefono": "5511122233", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    r = client.post("/entrevistas", json={"candidato": P, "avisar_whatsapp": False})
    ENT, TOKEN = r.json()["id"], r.json()["token"]
    p = db.query(Postulacion).filter_by(codigo=P).one()
    p.estado = "cumple"
    p.prefiltro_completo = True
    p.videollamada_agendada_en = datetime.now(timezone.utc) - timedelta(hours=2)
    p.videollamada_liga = f"http://localhost:3000/entrevista/{TOKEN}"
    p.candidato.wa_id = TEL
    p.candidato.postulacion_conversacion_id = p.id
    db.commit()
    # La entrevista se interrumpe (desconexión con <2 turnos)
    client.post(f"/entrevistas/publica/{TOKEN}/consentimiento", json={"acepta": True})
    client.post(f"/entrevistas/publica/{TOKEN}/sesion", json={"modo": "texto"})
    r = client.post(f"/entrevistas/publica/{TOKEN}/finalizar", json={"cierre": "desconexion", "transcript": [{"rol": "assistant", "texto": "Hola"}]})
    check(r.json()["estado"] == "interrumpida", "entrevista interrumpida (desconexión sin respuestas)")

    ENVIADOS.clear()
    r = webhook(client, TEL, "Hola, se me cortó la llamada, ¿me pasas el enlace de nuevo?")
    db.expire_all()
    e = db.query(Entrevista).filter_by(codigo=ENT).one()
    check(r.status_code == 200 and r.json().get("entrevista_reabierta") == ENT, "el candidato escribe → la entrevista se REABRE desde WhatsApp")
    check(e.estado == "programada" and len(e.intentos_previos) == 1 and e.intentos_previos[0]["estado"] == "interrumpida", "misma entrevista: estado programada, intento archivado")
    check(ENVIADOS and TOKEN in ENVIADOS[-1]["texto"] and "reabrí" in ENVIADOS[-1]["texto"], "recibe la MISMA liga por WhatsApp")
    r = client.get(f"/entrevistas/publica/{TOKEN}")
    check(r.json()["estado"] == "programada", "la liga vuelve a aceptar la sala (GET /publica → programada)")
    # parcial también cuenta como fallida
    client.post(f"/entrevistas/publica/{TOKEN}/sesion", json={"modo": "texto"})
    e = db.query(Entrevista).filter_by(codigo=ENT).one()
    e.estado = "parcial"
    e.motivo = "parcial"
    db.commit()
    r = webhook(client, TEL, "quiero volver a hacer la entrevista")
    db.expire_all()
    e = db.query(Entrevista).filter_by(codigo=ENT).one()
    check(r.json().get("entrevista_reabierta") == ENT and e.estado == "programada" and len(e.intentos_previos) == 2, "parcial → también se reabre (2 intentos archivados)")
    # reagendar con la IA caída → agenda nueva en la misma postulación
    e.estado = "interrumpida"
    db.commit()
    ENVIADOS.clear()
    r = webhook(client, TEL, "no puedo ese día, ¿podemos reagendar para otro horario?")
    db.expire_all()
    p = db.query(Postulacion).filter_by(codigo=P).one()
    check(r.status_code == 200 and r.json().get("postulacion") == P and p.videollamada_agendada_en is None and (p.analisis or {}).get("videollamadas_anteriores"),
          "«reagendar» con entrevista interrumpida → se suelta la cita y el agente de agenda coordina una nueva")
    check(p.espera_respuesta, "la postulación vuelve a esperar respuesta (agenda abierta)")
    # una entrevista EVALUADA (exitosa) NO se reabre desde WhatsApp
    e = db.query(Entrevista).filter_by(codigo=ENT).one()
    e.estado = "evaluada"
    p.videollamada_agendada_en = datetime.now(timezone.utc)
    db.commit()
    r = webhook(client, TEL, "hola")
    db.expire_all()
    e = db.query(Entrevista).filter_by(codigo=ENT).one()
    check(e.estado == "evaluada" and not r.json().get("entrevista_reabierta"), "una entrevista evaluada NO se reabre por WhatsApp (solo RH)")

    # ================= Recordatorios automáticos de documentos =================
    print("\n--- Cron de recordatorios de documentos (fecha límite «hasta») ---")
    r = client.post("/candidatos", json={"nombre": "Doc Pendiente", "telefono": "5599900011", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P2 = r.json()["id"]
    r = client.patch(f"/candidatos/{P2}/etapa", json={"etapa": "Contratación", "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    EXP = r.json()["expedienteId"]
    cfg = obtener(db)
    cfg.recordatorio_documentos_dias = 2
    cfg.recordatorio_documentos_hora = 0
    db.commit()
    r = client.get("/configuracion")
    check(r.json()["recordatorioDocumentosDias"] == 2 and r.json()["recordatorioDocumentosHora"] == 0, "GET /configuracion expone dias/hora del cron")
    r = client.patch("/configuracion", json={"recordatorio_documentos_dias": 40})
    check(r.status_code == 400, "dias fuera de rango → 400")

    ENVIADOS.clear()
    n = asyncio.run(recordatorios.revisar_recordatorios_documentos())
    check(n == 0, "sin «recordar hasta» → el cron no manda nada")
    r = client.patch(f"/contratacion/expedientes/{EXP}/preparacion", json={"documentos_hasta": "2020-01-01"})
    check(r.status_code == 400, "fecha límite en el pasado → 400")
    hasta = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
    r = client.patch(f"/contratacion/expedientes/{EXP}/preparacion", json={"documentos_hasta": hasta})
    check(r.status_code == 200 and r.json()["documentosHasta"] and r.json()["documentosHasta"][:10] == hasta, f"PATCH preparación guarda documentos_hasta={hasta}")
    n = asyncio.run(recordatorios.revisar_recordatorios_documentos())
    check(n == 0, "expediente recién creado → todavía no toca (el primer recordatorio espera el intervalo)")
    e = db.get(Expediente, EXP)
    e.creado_en = datetime.now(timezone.utc) - timedelta(days=3)
    db.commit()
    n = asyncio.run(recordatorios.revisar_recordatorios_documentos())
    check(n == 1, "pasado el intervalo → manda 1 recordatorio")
    check(ENVIADOS and "me falta recibir" in ENVIADOS[-1]["texto"] and "fecha límite" in ENVIADOS[-1]["texto"], f"el WhatsApp lista pendientes y la fecha límite: «{ENVIADOS[-1]['texto'][:90]}…»")
    reg = db.query(NotificacionEnviada).filter_by(evento="recordatorio_documentos").count()
    check(reg == 1, "queda registrado en notificaciones_enviadas")
    n2 = asyncio.run(recordatorios.revisar_recordatorios_documentos())
    check(n2 == 0, "segunda corrida inmediata → nada (idempotente / respeta el intervalo)")
    db.expire_all()
    e = db.get(Expediente, EXP)
    check(e.ultimo_recordatorio_en is not None, "ultimo_recordatorio_en registrado")
    r = client.get(f"/contratacion/expedientes/{EXP}")
    check(r.json()["ultimoRecordatorioEn"], "el expediente expone ultimoRecordatorioEn")
    # vence la fecha límite → un aviso en bitácora, sin más mensajes
    e.ultimo_recordatorio_en = datetime.now(timezone.utc) - timedelta(days=3)
    e.documentos_hasta = datetime.now(timezone.utc) - timedelta(days=2)
    db.commit()
    ENVIADOS.clear()
    n = asyncio.run(recordatorios.revisar_recordatorios_documentos())
    db.expire_all()
    e = db.get(Expediente, EXP)
    check(n == 0 and not ENVIADOS and e.documentos_vencidos_avisado, "fecha límite vencida → NO se escribe al candidato; queda aviso en bitácora")
    n = asyncio.run(recordatorios.revisar_recordatorios_documentos())
    check(n == 0, "vencida: no vuelve a avisar")
    # quitar la fecha apaga el cron para ese expediente
    r = client.patch(f"/contratacion/expedientes/{EXP}/preparacion", json={"documentos_hasta": ""})
    check(r.status_code == 200 and r.json()["documentosHasta"] is None, "documentos_hasta vacío → recordatorios apagados")
    # expediente completo no recibe recordatorios
    e = db.get(Expediente, EXP)
    e.documentos_hasta = datetime.now(timezone.utc) + timedelta(days=5)
    e.ultimo_recordatorio_en = datetime.now(timezone.utc) - timedelta(days=3)
    for d in e.documentos:
        d.estado = "recibido"
    db.commit()
    n = asyncio.run(recordatorios.revisar_recordatorios_documentos())
    check(n == 0, "sin documentos pendientes → no manda nada")

print(f"\n🎉 FASE 3 verificada: {OK} comprobaciones OK.")
