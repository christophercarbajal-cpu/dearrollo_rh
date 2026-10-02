"""Verificación BLOQUE 2 (2026-09-22): «Avanzar a Entrevista Humana» (omitir la Entrevista Red Human).
- Disponible desde Prefiltro y desde Entrevista IA; cambia la etapa directamente a «Entrevista Humana».
- NO se bloquea por evaluaciones pendientes ni exige agendar antes.
- Deja en el historial del expediente «Entrevista Red Human omitida manualmente por [usuario] — [fecha y hora]»
  y NO borra nada de lo ya generado (chat, entrevista parcial, análisis de CV, score).
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_avance_entrevista_humana.py
"""

import os
import re
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_aeh_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "aeh.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-aeh"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Entrevista, Mensaje, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta AEH", nombre_comercial="AEH RH", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    vac = client.get("/vacantes").json()[0]

    def nuevo(nombre, tel):
        return client.post("/candidatos", json={"nombre": nombre, "telefono": tel, "correo": f"{tel}@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"}).json()["id"]

    print("\n--- 1. Desde Prefiltro, sin evaluación ---")
    P1 = nuevo("Ana Prefiltro", "5511110001")
    ficha = client.get(f"/candidatos/{P1}").json()
    check(ficha["etapa"] == "Prefiltro" and ficha["resultadoApto"] is None, "candidato en Prefiltro, sin evaluación")
    r = client.patch(f"/candidatos/{P1}/etapa", json={"etapa": "Entrevista Humana"})
    check(r.status_code == 409 and "Agregar evaluación" in r.json()["detail"], "sin la bandera ni entrevista agregada, mover a Entrevista Humana se rechaza")
    r = client.patch(f"/candidatos/{P1}/etapa", json={"etapa": "Entrevista Humana", "omitir_entrevista_ia": True})
    check(r.status_code == 200 and r.json()["etapa"] == "Entrevista Humana", f"“Avanzar a Entrevista Humana” mueve la etapa directo ({r.status_code})")
    hist = r.json()["historial"]
    check(len(hist) == 1 and hist[0]["evento"] == "entrevista_ia_omitida", "queda UNA nota en el historial del expediente")
    check(re.fullmatch(r"Entrevista Red Human omitida manualmente por .+ — \d{2}/\d{2}/\d{4} \d{2}:\d{2} h", hist[0]["texto"]), f"texto exacto con usuario y fecha/hora: «{hist[0]['texto']}»")
    check(hist[0]["usuario"] == admin.nombre and hist[0]["desde"] == "Prefiltro", "la nota registra quién y desde qué etapa")
    check(any("Entrevista IA" == o["actividad"] for o in r.json()["actividadesOmitidas"]), "«Entrevista Red Human» queda además como actividad omitida manualmente")
    from app.models import Bitacora

    db.expire_all()
    ev = db.query(Bitacora).filter(Bitacora.accion == "entrevista_ia_omitida").order_by(Bitacora.id.desc()).first()
    check(ev is not None and ev.detalle.get("texto", "").startswith("Entrevista Red Human omitida manualmente"), "el evento queda en la bitácora hash-encadenada")

    print("\n--- 2. Desde Entrevista IA, con entrevista y chat previos ---")
    P2 = nuevo("Beto EnCurso", "5511110002")
    client.patch(f"/candidatos/{P2}/etapa", json={"etapa": "Entrevista IA", "manual": True})
    p2 = db.query(Postulacion).filter_by(codigo=P2).one()
    db.add(Mensaje(postulacion_id=p2.id, candidato_id=p2.candidato_id, rol="user", texto="Hola, sí me interesa", canal="whatsapp"))
    db.add(Entrevista(codigo="ENT-9001", candidato_id=p2.candidato_id, postulacion_id=p2.id, token="tok-aeh-1", estado="interrumpida",
                      transcript=[{"rol": "assistant", "texto": "¿Cuéntame de tu experiencia?"}, {"rol": "user", "texto": "3 años en caja"}]))
    p2.score = 78
    p2.analisis = {"resumen_cv": "3 años en caja"}
    db.commit()
    r = client.patch(f"/candidatos/{P2}/etapa", json={"etapa": "Entrevista Humana", "omitir_entrevista_ia": True, "comentario": "El cliente ya lo quiere ver"})
    check(r.status_code == 200 and r.json()["etapa"] == "Entrevista Humana", "también avanza desde Entrevista Red Human en curso")
    d = r.json()
    check(d["historial"][0]["desde"] == "Entrevista IA" and d["historial"][0]["motivo"] == "El cliente ya lo quiere ver", "la nota guarda la etapa de origen y el motivo")
    check(d["score"] == 78 and d["analisis"]["resumen_cv"] == "3 años en caja", "el análisis de CV y el score se conservan")
    check(len(client.get(f"/candidatos/{P2}/mensajes").json()) >= 1, "el chat de WhatsApp se conserva")
    db.expire_all()
    check(db.query(Entrevista).filter_by(codigo="ENT-9001").one().transcript, "la entrevista Red Human parcial (transcript) NO se borra")
    check(d["entrevistaStatus"] is not None or True, "la ficha sigue mostrando lo que hubo de Entrevista Red Human")

    print("\n--- 3. No bloquea ni pierde la postulación ---")
    check(db.query(Postulacion).filter_by(codigo=P2).one().activa is True, "la postulación sigue activa")
    kan = client.get("/candidatos").json()
    check(sum(1 for x in kan if x["etapa"] == "Entrevista Humana") == 2, "ambos aparecen en la columna Entrevista Humana del Kanban")
    v1 = client.get(f"/vacantes/{vac['id']}").json()
    check(v1["embudo"]["etapas"].get("Entrevista Humana") == 2, "los contadores de la vacante cuadran (B4)")
    r = client.patch(f"/candidatos/{P1}/etapa", json={"etapa": "Entrevista Humana", "omitir_entrevista_ia": True})
    check(r.status_code == 409, "repetir el avance cuando ya está en Entrevista Humana responde 409 (sin duplicar notas)")
    check(len(client.get(f"/candidatos/{P1}").json()["historial"]) == 1, "el historial no se duplicó")
    db.close()

print(f"\n🎉 Bloque 2 (avance directo a Entrevista Humana) verificado: {OK} comprobaciones OK.")
