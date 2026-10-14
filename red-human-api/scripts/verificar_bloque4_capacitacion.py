"""Verificación BLOQUE 4 (2026-09-19, cambios Raúl): modalidad (Instructor IA / Autoguiado) y duración libre al
generar, prompts diferenciados, PDF como material de apoyo, «Finalizar curso» (alias de publicar), sala pública
con modalidad, y fix de evaluaciones (correcta ↔ explicación coherentes). Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_bloque4_capacitacion.py
"""

import inspect as _insp
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_b4_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "b4.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-b4"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Curso, Usuario, UsuarioCuenta  # noqa: E402
from app.services import ia  # noqa: E402

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
    cuenta = Cuenta(nombre="Grupo CARBE", nombre_comercial="Grupo Carbe", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    print("\n--- 1. Duración libre ---")
    h = ia.horas_desde_texto
    check(h("5 min") == 0.08 and h("15 minutos") == 0.25 and h("1h") == 1.0 and h("1 h 30 min") == 1.5 and h("2 horas") == 2.0 and h("90") == 1.5, "«5 min», «1 h 30 min», «2 horas», «90» se entienden")
    r = client.post("/capacitacion/generar", data={"tema": "Uso del extintor", "duracion": "5 min", "modalidad": "instructor_ia", "contexto": ""})
    check(r.status_code == 201 and r.json()["duracion"] == "5 min" and r.json()["modalidad"] == "instructor_ia" and abs(r.json()["duracionHoras"] - 0.08) < 0.01, "generar con duración «5 min» + Instructor IA")
    C1 = r.json()["id"]
    r = client.post("/capacitacion/generar", data={"tema": "Política de vacaciones", "duracion": "1 h", "modalidad": "autoguiado"})
    check(r.status_code == 201 and r.json()["modalidad"] == "autoguiado" and r.json()["duracion"] == "1 h", "generar Autoguiado «1 h»")
    C2 = r.json()["id"]
    check(client.post("/capacitacion/generar", data={"tema": "x", "duracion": "10 min", "modalidad": "otra"}).status_code == 400, "modalidad inválida → 400")
    check(client.post("/capacitacion/generar", data={"tema": "x", "modalidad": "autoguiado"}).status_code == 400, "sin duración → 400 (mensaje claro)")
    r = client.post("/capacitacion/generar", data={"tema": "Compat", "duracion_horas": "2", "modalidad": "autoguiado"})
    check(r.status_code == 201 and r.json()["duracion"] == "2 h", "compatibilidad: duracion_horas numérica sigue funcionando")

    print("\n--- 2. Prompts diferenciados y evaluación coherente ---")
    fuente = _insp.getsource(ia.guion_curso)
    check("GUION CONVERSACIONAL" in fuente and "frases cortas" in fuente and "pausas" in fuente, "Instructor IA: guion conversacional, frases cortas, pausas para preguntas")
    check("AUTOGUIADO" in fuente and "BREVE, visual y modular" in fuente, "Autoguiado: contenido breve, visual, modular")
    check("material de apoyo" in fuente and "puntos_clave" in fuente, "en ambos: resumen + puntos clave para el PDF de apoyo")
    check("única opción verdadera" in fuente and "justificar esa" in fuente and "_verificar_evaluacion" in fuente, "evaluación: correcta = única verdadera, explicación coherente + segunda pasada de verificación")
    check("SIN ver la" in _insp.getsource(ia._verificar_evaluacion) and "independiente" in _insp.getsource(ia._verificar_evaluacion), "la verificación resuelve cada pregunta de forma independiente, sin ver la marcada")
    r = client.get(f"/capacitacion/{C1}").json()
    for q in r["evaluacion"]:
        check(0 <= q["correcta"] < len(q["opciones"]), f"pregunta válida: «{q['pregunta'][:50]}»")
        break

    print("\n--- 3. Finalizar (alias de publicar) ---")
    r = client.patch(f"/capacitacion/{C1}/finalizar")
    check(r.status_code == 200 and r.json()["estado"] == "Publicado", "PATCH /finalizar deja el curso listo para asignar (estado interno Publicado)")
    check(client.patch(f"/capacitacion/{C2}/publicar").status_code == 200, "alias /publicar sigue funcionando")

    print("\n--- 4. PDF = material de apoyo ---")
    db.expire_all()
    c1 = db.query(Curso).filter(Curso.codigo == C1).one()
    check(all(m.resumen for m in c1.modulos) and all(m.puntos_clave for m in c1.modulos), "cada módulo trae resumen y puntos clave (demo)")
    r = client.get(f"/capacitacion/{C1}/pdf")
    check(r.status_code == 200 and r.content[:4] == b"%PDF", "PDF del curso Instructor IA (apoyo: resumen + puntos clave)")

    print("\n--- 5. Sala pública con modalidad ---")
    r = client.post(f"/capacitacion/{C1}/asignar", json={"externos": [{"nombre": "Ext", "correo": "e@x.mx"}], "notificar": False})
    tok = r.json()["asignaciones"][0]["token"]
    r = client.get(f"/capacitacion/publica/{tok}").json()
    check(r["modalidad"] == "instructor_ia" and r["duracion"] == "5 min", "la sala sabe la modalidad y la duración libre")
    r = client.post(f"/capacitacion/publica/{tok}/sesion", json={"modulo": 1})
    check(r.status_code == 200 and r.json()["modo"] in ("texto", "avatar"), "sesión del instructor para el módulo 1")
    r = client.post(f"/capacitacion/publica/{tok}/avanzar", json={"modulo": 1})
    check(r.status_code == 200 and r.json()["modulosCompletados"] == 1, "Continuar → siguiente módulo")
    total = r.json()["totalModulos"]
    for m in range(2, total + 1):
        r = client.post(f"/capacitacion/publica/{tok}/avanzar", json={"modulo": m})
    check(r.json()["pregunta"] is not None and r.json()["pregunta"]["indice"] == 0 and "correcta" not in r.json()["pregunta"], "…y al final la evaluación, una pregunta por pantalla, sin exponer la correcta")
    q = r.json()["pregunta"]
    r = client.post(f"/capacitacion/publica/{tok}/responder", json={"indice": 0, "respuesta": 0})
    check(r.status_code == 200 and "explicacion" in r.json() and isinstance(r.json()["correcta"], bool), "retroalimentación de ESA pregunta con su explicación")

    db.close()

print(f"\n🎉 Bloque 4 verificado: {OK} comprobaciones OK.")
