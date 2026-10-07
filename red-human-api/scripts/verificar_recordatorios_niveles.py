"""Verificación (2026-09-17): recordatorios de documentos en 3 niveles progresivos.

1 ligero → 2 intermedio → 3 definitivo; después del definitivo no salen más automáticos (RH da
seguimiento, bitácora `recordatorios_agotados`); el botón manual comparte el mismo contador.
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_recordatorios_niveles.py
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_rec_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "rec.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-rec"
os.environ["SEMBRAR_DEMO"] = "true"
os.environ["META_PLANTILLA_RECORDATORIO_3"] = "recordatorio_definitivo_rh"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Bitacora, Cuenta, Expediente, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import notificaciones, recordatorios, whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
TEXTOS = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _fake_plantilla(tel, valores, texto, nivel=1):
    TEXTOS.append((nivel, texto))
    return {"enviado": True, "proveedor": "meta", "plantilla": whatsapp.plantilla_documentos_por_nivel(nivel)}


notificaciones.enviar_plantilla_documentos = _fake_plantilla

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta Rec", nombre_comercial="Rec", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ================= 1. Textos por nivel =================
    print("\n--- 1. Tono progresivo ---")
    t1 = notificaciones.texto_recordatorio_documentos(1, "Ana", "Cajera", ["INE", "CURP"], "https://x/exp")
    t2 = notificaciones.texto_recordatorio_documentos(2, "Ana", "Cajera", ["INE", "CURP"], "https://x/exp", fecha_limite=datetime(2026, 10, 1, tzinfo=timezone.utc))
    t3 = notificaciones.texto_recordatorio_documentos(3, "Ana", "Cajera", ["INE", "CURP"], "https://x/exp")
    check("Sin prisa" in t1 and "INE, CURP" in t1 and "último" not in t1.lower(), "nivel 1: amistoso, sin presión, lista lo que falta")
    check("seguimiento" in t2 and "fecha límite" in t2 and "dificultad" in t2, "nivel 2: estándar con fecha límite y ofrece ayuda")
    check("último recordatorio" in t3 and "en pausa" in t3 and "Recursos Humanos" in t3, "nivel 3: definitivo, firme y con consecuencia clara")
    check(all(x not in t3.lower() for x in ("inmediatamente", "obligatorio", "de lo contrario", "sanci")), "nivel 3 no usa lenguaje agresivo")
    check("https://x/exp" in t1 and "https://x/exp" in t3, "los tres traen la liga de subida")
    check(notificaciones.texto_recordatorio_documentos(7, "", "puesto", None).startswith("Hola. Este es el último"), "nivel >3 se acota a definitivo; sin pendientes cae a INE + comprobante")
    check(notificaciones._asunto_recordatorio(3).startswith("Último recordatorio"), "asunto del correo por nivel")
    check(whatsapp.plantilla_documentos_por_nivel(1) == "solicitud_documentos_rh" and whatsapp.plantilla_documentos_por_nivel(3) == "recordatorio_definitivo_rh", "plantilla de Meta por nivel (opcional; cae a la base)")

    # ================= 2. Contador compartido manual + automático =================
    print("\n--- 2. Contador de niveles: manual y automático ---")
    vac = client.get("/vacantes").json()[0]
    r = client.post("/candidatos", json={"nombre": "Rec Persona", "telefono": "5512120001", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    r = client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    exp_id = r.json()["expedienteId"]
    r = client.get(f"/contratacion/expedientes/{exp_id}").json()
    check(r["nivelRecordatorio"] == 1 and r["tonoRecordatorio"] == "ligero" and r["recordatoriosAgotados"] is False, "expediente nuevo: próximo recordatorio = nivel 1 (ligero)")
    r = client.get(f"/candidatos/{P}").json()
    check(r["recordatorioNivel"] == 1 and r["recordatoriosEnviados"] == 0, "la ficha del candidato expone el nivel")

    r = client.post(f"/contratacion/expedientes/{exp_id}/recordatorio", json={})
    check(r.status_code == 200 and r.json()["nivel"] == 1 and r.json()["tono"] == "ligero", "1er recordatorio manual → nivel 1")
    check(TEXTOS[-1][0] == 1 and "Sin prisa" in TEXTOS[-1][1], "…y el WhatsApp salió con el texto ligero")
    r = client.post(f"/contratacion/expedientes/{exp_id}/recordatorio", json={})
    check(r.json()["nivel"] == 2 and r.json()["expediente"]["recordatoriosEnviados"] == 2, "2º manual → nivel 2 (intermedio)")
    check(TEXTOS[-1][0] == 2 and "seguimiento" in TEXTOS[-1][1], "…texto intermedio")

    # automático: toca a nivel 3 y luego se detiene
    cfg = obtener(db)
    cfg.recordatorio_documentos_hora = 0  # el job usa la hora real; que no dependa de la hora de la corrida
    db.commit()
    db.expire_all()
    e = db.query(Expediente).get(exp_id)
    e.documentos_hasta = datetime.now(timezone.utc) + timedelta(days=10)
    e.ultimo_recordatorio_en = datetime.now(timezone.utc) - timedelta(days=5)
    db.commit()
    ahora = datetime.now(timezone.utc).replace(hour=23)
    check(recordatorios.toca_recordar(e, cfg, ahora) == "enviar", "el job decide enviar (2 enviados, toca el definitivo)")
    n = asyncio.run(recordatorios.revisar_recordatorios_documentos())
    check(n == 1, "el job manda el recordatorio automático")
    check(TEXTOS[-1][0] == 3 and "último recordatorio" in TEXTOS[-1][1], "…nivel 3 definitivo (mismo contador que los manuales)")
    db.expire_all()
    e = db.query(Expediente).get(exp_id)
    check(e.recordatorios_enviados == 3 and e.recordatorios_agotados, "contador = 3 → automáticos agotados")
    check(db.query(Bitacora).filter(Bitacora.accion == "recordatorios_agotados").count() == 1, "bitácora recordatorios_agotados (una vez) para que RH dé seguimiento")
    e.ultimo_recordatorio_en = datetime.now(timezone.utc) - timedelta(days=5)
    db.commit()
    check(recordatorios.toca_recordar(e, cfg, ahora) == "", "con los automáticos agotados el job ya no manda más")
    check(asyncio.run(recordatorios.revisar_recordatorios_documentos()) == 0, "…confirmado en la corrida")
    r = client.get("/contratacion/expedientes").json()
    check(any(x["expedienteId"] == exp_id and x["recordatoriosAgotados"] for x in r), "el tablero de Onboarding marca «recordatorios agotados»")
    r = client.post(f"/contratacion/expedientes/{exp_id}/recordatorio", json={})
    check(r.status_code == 200 and r.json()["nivel"] == 3 and TEXTOS[-1][0] == 3, "RH sí puede seguir mandando el definitivo a mano")
    check(db.query(Bitacora).filter(Bitacora.accion == "recordatorios_agotados").count() == 1, "sin duplicar el aviso de agotados")

    # liga genérica (candidatos.recordatorio-documentos) comparte contador
    r = client.post(f"/candidatos/{P}/recordatorio-documentos", json={})
    check(r.status_code == 200 and r.json()["nivel"] == 3, "el recordatorio desde la ficha (liga pública) usa el mismo contador")
    db.expire_all()
    check(db.query(Expediente).get(exp_id).recordatorios_enviados == 5, "contador total = 5")

    db.close()

print(f"\n🎉 Recordatorios en 3 niveles verificados: {OK} comprobaciones OK.")
