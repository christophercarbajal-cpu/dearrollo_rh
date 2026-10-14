"""Verificación del «Botón Mágico» de Capacitación (Expo, 2026-09-23).

`POST /capacitacion/{codigo}/demo` da una liga pública funcional al instante: sin asignar a una persona
real, sin mandar WhatsApp ni correo, con `?totem=1` para la pantalla vertical y sin tocar la tabla de
Colaboradores (el roster es la base maestra). Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_liga_demo_curso.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_demo_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "demo.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
# como en producción: WhatsApp configurado — la prueba comprueba que NO se use en esta ruta
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-de-prueba"
os.environ["META_PHONE_NUMBER_ID"] = "123456"
os.environ["ADMIN_PASSWORD"] = "prueba-demo"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AsignacionCurso, Colaborador, Cuenta, Usuario, UsuarioCuenta  # noqa: E402
from app.routers import capacitacion as rcap  # noqa: E402
from app.services import whatsapp as swa  # noqa: E402

OK = 0
ENVIOS = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _espia(*a, **k):
    ENVIOS.append(a[:1])
    return {"enviado": True, "proveedor": "meta", "detalle": 200}


# cualquier intento de avisar por WhatsApp o correo queda registrado aquí
rcap.enviar_mensaje = _espia
rcap.enviar_texto_sin_plantilla = _espia
rcap.enviar_correo = _espia
swa.enviar_mensaje = _espia

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
    colaboradores_antes = db.query(Colaborador).count()

    print("\n--- 1. Curso recién generado (todavía en Borrador) ---")
    r = client.post("/capacitacion/generar", data={"tema": "Uso del extintor", "duracion": "10 min", "modalidad": "instructor_ia"})
    check(r.status_code == 201 and r.json()["estado"] == "Borrador", f"curso generado en Borrador ({r.status_code})")
    CURSO = r.json()["id"]

    ENVIOS.clear()
    r = client.post(f"/capacitacion/{CURSO}/demo")
    check(r.status_code == 201, f"POST /capacitacion/{{codigo}}/demo → 201 aunque el curso siga en Borrador ({r.status_code}: {r.text[:160]})")
    d = r.json()
    check(d["liga"].endswith(d["token"]) and "/capacitacion/" in d["liga"], f"la liga es absoluta y trae el token: {d['liga']}")
    check(d["ligaTotem"] == f"{d['liga']}?totem=1", f"la liga de tótem agrega ?totem=1: {d['ligaTotem']}")
    check(not ENVIOS, "NO salió ningún WhatsApp ni correo (asignación silenciosa)")
    check(d["asignacion"]["tipo"] == "externo" and d["asignacion"]["organizacion"] == "Demo Expo", "queda como externo «Demo Expo», distinguible en el tablero")
    check(db.query(Colaborador).count() == colaboradores_antes, "no se creó ningún Colaborador (el roster maestro no se toca)")

    print("\n--- 2. La liga abre y funciona de inmediato ---")
    pub = client.get(f"/capacitacion/publica/{d['token']}")
    check(pub.status_code == 200, f"la sala pública abre sin sesión ({pub.status_code})")
    p = pub.json()
    check(p["requiereRegistro"] is False, "NO pide registro: entra directo (nombre de invitado ya puesto)")
    check(p["modalidad"] == "instructor_ia" and p["totalModulos"] >= 1, f"trae el contenido del curso ({p['totalModulos']} módulos)")
    r = client.post(f"/capacitacion/publica/{d['token']}/avanzar", json={"modulo": 1})
    check(r.status_code == 200 and r.json()["modulosCompletados"] == 1, "se puede avanzar el primer módulo (la liga es funcional, no un placeholder)")

    print("\n--- 3. Reutilizable y regenerable ---")
    r2 = client.post(f"/capacitacion/{CURSO}/demo")
    check(r2.json()["token"] == d["token"] and r2.json()["reutilizada"] is True, "volver a pulsar el botón reutiliza la misma liga (conserva el avance)")
    r3 = client.post(f"/capacitacion/{CURSO}/demo?nueva=true")
    check(r3.json()["token"] != d["token"] and r3.json()["reutilizada"] is False, "«Generar otra liga» entrega una liga limpia")
    check(client.get(f"/capacitacion/publica/{r3.json()['token']}").json()["modulosCompletados"] == 0, "la liga nueva empieza desde cero")
    demos = [a for a in client.get(f"/capacitacion/{CURSO}/asignaciones").json() if a["organizacion"] == "Demo Expo"]
    check(len(demos) == 2, f"las dos ligas de demo se ven en el tablero del curso ({len(demos)})")

    print("\n--- 4. Guardas ---")
    r = client.post("/capacitacion/CUR-9999/demo")
    check(r.status_code == 404, "un curso inexistente responde 404")
    db.expire_all()
    vacio = rcap._por_codigo(db, CURSO, cuenta.id)
    for m in list(vacio.modulos):
        db.delete(m)
    db.commit()
    r = client.post(f"/capacitacion/{CURSO}/demo?nueva=true")
    check(r.status_code == 409 and "contenido" in r.json()["detail"], "sin contenido generado se rechaza con un mensaje claro")
    check(db.query(AsignacionCurso).filter(AsignacionCurso.externo_organizacion == "Demo Expo").count() == 2, "el rechazo no dejó asignaciones basura")
    db.close()

print(f"\n🎉 Liga directa (Modo Expo) verificada: {OK} comprobaciones OK.")
