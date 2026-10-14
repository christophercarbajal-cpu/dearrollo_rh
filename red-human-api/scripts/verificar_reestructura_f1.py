"""Verificación REESTRUCTURA · FASE 1 (2026-09-16) — Modo Prueba flexible: con Modo Prueba activo el mismo
teléfono/correo se puede reutilizar en distintos candidatos (RH y formulario público) sin error ni fusión; al
apagarlo vuelve la deduplicación; el correo de login de Usuario sigue siendo único siempre.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_reestructura_f1.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_rf1_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "rf1.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["SEMBRAR_DEMO"] = "false"
os.environ["ADMIN_PASSWORD"] = "prueba-rf1"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cuenta, Postulacion, Usuario, UsuarioCuenta  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


# Cambio 28: con prefiltro en la ruta, publicar exige al menos 3 indispensables
REQ3 = ["Mayor de edad", "Disponibilidad de horario", "Saber leer y escribir"]
with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta RF1", nombre_comercial="RF1", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    r = client.post("/vacantes", json={"titulo": "Cajero RF1", "descripcion": "x", "generar_si_falta": False, "requisitos_indispensables": REQ3, "publicar": True})
    VAC = r.json()["id"]
    client.post(f"/vacantes/{VAC}/publicar", json={"plataformas": ["WhatsApp", "Portal"]})
    r = client.post("/vacantes", json={"titulo": "Vendedor RF1", "descripcion": "x", "generar_si_falta": False})
    VAC2 = r.json()["id"]
    slug = client.get(f"/vacantes/{VAC}").json()["slug"]

    print("\n--- Modo Prueba ACTIVO: teléfono/correo reutilizables ---")
    client.patch("/configuracion", json={"modo_prueba": True})
    r1 = client.post("/candidatos", json={"nombre": "Ana Uno", "telefono": "5511112222", "correo": "ana@demo.mx", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    r2 = client.post("/candidatos", json={"nombre": "Ana Dos", "telefono": "5511112222", "correo": "ana@demo.mx", "vacante": VAC2, "consentimiento": True, "fuente": "RH"})
    check(r1.status_code == 201 and r2.status_code == 201, "dos altas con el mismo teléfono y correo → 201 y 201 (sin error de unicidad)")
    check(r1.json()["candidatoCodigo"] != r2.json()["candidatoCodigo"] and not r2.json()["duplicado"], "son DOS personas distintas (no se fusionan ni se marca duplicado)")
    r3 = client.post("/candidatos/postular", data={"vacante": slug, "nombre": "Ana Tres", "telefono": "5511112222", "correo": "ana@demo.mx", "consentimiento": "true"})
    check(r3.status_code in (200, 201), f"formulario público con el mismo teléfono/correo → {r3.status_code} (permitido)")
    check(db.query(Candidato).filter(Candidato.telefono == "5511112222").count() == 3, "3 personas con el mismo teléfono en la base (Modo Prueba)")
    r = client.get("/candidatos")
    # Cambio 37: en Modo Prueba una postulación nueva del mismo número a la MISMA vacante reemplaza a la anterior
    # («Prueba reemplazada»); la de otra vacante sigue
    check(len([p for p in r.json() if p["telefono"] == "5511112222"]) == 2, "las 2 vigentes aparecen en el Kanban")
    p_r1 = db.query(Postulacion).filter_by(codigo=r1.json()["id"]).one()
    check(not p_r1.activa and p_r1.motivo_cierre == "prueba_reemplazada", "la anterior a la misma vacante queda «Prueba reemplazada»")

    print("\n--- Modo Prueba APAGADO: vuelve la deduplicación normal ---")
    client.patch("/configuracion", json={"modo_prueba": False})
    r4 = client.post("/candidatos", json={"nombre": "Beto Uno", "telefono": "5533334444", "correo": "beto@demo.mx", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    r5 = client.post("/candidatos", json={"nombre": "Beto Otra Vez", "telefono": "5533334444", "correo": "", "vacante": VAC2, "consentimiento": True, "fuente": "RH"})
    check(r4.status_code == 201 and r5.status_code == 201, "sin Modo Prueba: ambas altas responden 201 (nunca 409)…")
    check(r5.json()["duplicado"] is True and r5.json()["candidatoCodigo"] == r4.json()["candidatoCodigo"], "…pero la persona se REUTILIZA (dedup) y se marca duplicado=true")
    check(db.query(Candidato).filter(Candidato.telefono == "5533334444").count() == 1, "una sola persona con ese teléfono")

    print("\n--- Login: el correo de Usuario sigue siendo único siempre ---")
    client.patch("/configuracion", json={"modo_prueba": True})
    r = client.post("/auth/usuarios", json={"correo": "rh@demo.mx", "nombre": "RH Uno", "rol": "Usuario", "password": "ClaveSegura1!"})
    check(r.status_code == 201, "alta de usuario")
    r = client.post("/auth/usuarios", json={"correo": "rh@demo.mx", "nombre": "RH Dos", "rol": "Usuario", "password": "ClaveSegura1!"})
    check(r.status_code == 409, "mismo correo de login con Modo Prueba activo → 409 (excepción respetada)")

print(f"\n🎉 Reestructura F1 verificada: {OK} comprobaciones OK.")
