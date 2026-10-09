"""Verificación FASE 4 (2026-09-15): ubicación estructurada (Estado/Municipio → texto derivado) y preguntas
de prefiltro de WhatsApp independientes de las de la postulación web. Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_fase4_vacantes.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_f4_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "f4.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-f4"
os.environ["SEMBRAR_DEMO"] = "true"  # los scripts de verificación sí usan los datos de ejemplo

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cuenta, Postulacion, Usuario, UsuarioCuenta, Vacante, texto_ubicacion  # noqa: E402
from app.services import ia  # noqa: E402
import app.routers.candidatos as rc  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


check(texto_ubicacion("Jalisco", "Zapopan") == "Zapopan, Jalisco", "texto_ubicacion: «Zapopan, Jalisco»")
check(texto_ubicacion("Ciudad de México", "Benito Juárez") == "Benito Juárez, Ciudad de México", "texto_ubicacion: alcaldía de CDMX")
check(texto_ubicacion("Querétaro", "Querétaro") == "Querétaro", "texto_ubicacion: municipio = estado → sin repetir")
check(texto_ubicacion("", "", "Guadalajara, JAL") == "Guadalajara, JAL", "texto_ubicacion: sin estructura conserva el texto libre")

PREGUNTAS_USADAS = []
_orig = ia.prefiltro_turno


def _espia(titulo, requisitos, preguntas, historial, **kw):
    PREGUNTAS_USADAS.append([p.get("pregunta") if isinstance(p, dict) else p for p in preguntas])
    return _orig(titulo, requisitos, preguntas, historial, **kw)


ia.prefiltro_turno = _espia
rc.ia = ia

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta F4", nombre_comercial="F4", estado="Activa")
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

    print("\n--- Ubicación estructurada ---")
    web = [{"pregunta": "¿Tienes experiencia en caja?", "tipo": "si_no", "valida": "", "respuesta_esperada": "Sí", "descarta": True},
           {"pregunta": "¿Años de experiencia?", "tipo": "numero", "valida": "", "respuesta_esperada": "≥ 1", "descarta": False}]
    wa = [{"pregunta": "¿Vives en Zapopan o cerca?", "tipo": "si_no", "valida": "", "respuesta_esperada": "Sí", "descarta": True}]
    r = client.post("/vacantes", json={
        "titulo": "Cajero F4", "descripcion": "x", "generar_si_falta": False,
        "ubicacion_estado": "Jalisco", "ubicacion_municipio": "Zapopan",
        "preguntas_filtro": web, "preguntas_filtro_whatsapp": wa,
    })
    check(r.status_code == 201, "POST /vacantes con Estado/Municipio y dos listas de prefiltro")
    v = r.json()
    VAC = v["id"]
    check(v["ubicacion"] == "Zapopan, Jalisco" and v["ubicacionEstado"] == "Jalisco" and v["ubicacionMunicipio"] == "Zapopan", "ubicación derivada «Zapopan, Jalisco» + campos estructurados")
    check(len(v["criterios"]) == 2 and len(v["criteriosWhatsapp"]) == 1, "criterios web (2) y WhatsApp (1) guardados por separado")
    r = client.patch(f"/vacantes/{VAC}", json={"ubicacion_estado": "Ciudad de México", "ubicacion_municipio": "Benito Juárez"})
    check(r.json()["ubicacion"] == "Benito Juárez, Ciudad de México", "PATCH Estado/Municipio → texto de ubicación se recalcula")
    r = client.patch(f"/vacantes/{VAC}", json={"preguntas_filtro_whatsapp": []})
    check(r.json()["criteriosWhatsapp"] == [] and len(r.json()["criterios"]) == 2, "vaciar las de WhatsApp no toca las de la web")
    r = client.post("/vacantes", json={"titulo": "Libre", "descripcion": "x", "generar_si_falta": False, "ubicacion": "Guadalajara, JAL"})
    check(r.json()["ubicacion"] == "Guadalajara, JAL" and r.json()["ubicacionEstado"] == "", "vacante con texto libre (compatibilidad) se conserva")

    print("\n--- Prefiltro por WhatsApp usa SUS preguntas ---")
    client.patch(f"/vacantes/{VAC}", json={"preguntas_filtro_whatsapp": wa})
    r = client.post("/candidatos", json={"nombre": "Ana WA", "telefono": "5511223344", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    # retro 2026-10-09: prefiltro conversacional en todas las Cuentas — solo indispensables, cerradas, de una en una
    r = client.post(f"/candidatos/{P}/prefiltro", json={"texto": "Hola, me interesa"})
    check(r.status_code == 200 and "¿Tienes experiencia en caja?" in r.json()["respuesta"], f"primero el indispensable de la web: {r.json()['respuesta'][:120]}")
    r = client.post(f"/candidatos/{P}/prefiltro", json={"texto": "Sí"})
    check("¿Vives en Zapopan o cerca?" in r.json()["respuesta"] and "Años de experiencia" not in r.json()["respuesta"],
          "después la pregunta PROPIA de WhatsApp (indispensable y cerrada); lo no indispensable no se pregunta")
    client.patch(f"/vacantes/{VAC}", json={"preguntas_filtro_whatsapp": []})
    r = client.post("/candidatos", json={"nombre": "Beto WA", "telefono": "5511223355", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P2 = r.json()["id"]
    r = client.post(f"/candidatos/{P2}/prefiltro", json={"texto": "Hola"})
    check("¿Tienes experiencia en caja?" in r.json()["respuesta"], "sin preguntas de WhatsApp → usa las de la web (fallback)")

    print("\n--- Plantillas ---")
    r = client.post("/plantillas", json={"nombre": "P F4", "titulo": "Cajero", "ubicacion_estado": "Nuevo León", "ubicacion_municipio": "Monterrey", "preguntas_filtro_whatsapp": wa})
    check(r.status_code == 201 and r.json()["ubicacion"] == "Monterrey, Nuevo León" and len(r.json()["preguntasFiltroWhatsapp"]) == 1, "plantilla guarda ubicación estructurada y prefiltro WhatsApp")
    r = client.post(f"/plantillas/desde-vacante/{VAC}", json={"nombre": "Desde VAC"})
    check(r.status_code == 201 and r.json()["ubicacionEstado"] == "Ciudad de México", "guardar vacante como plantilla copia Estado/Municipio (CAMPOS_PLANTILLA)")

print(f"\n🎉 FASE 4 verificada: {OK} comprobaciones OK.")
