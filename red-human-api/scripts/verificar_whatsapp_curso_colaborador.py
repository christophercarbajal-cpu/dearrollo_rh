"""Verificación (2026-09-18): aviso por WhatsApp (texto libre, sin plantilla) al asignar un curso a un colaborador,
con liga absoluta al curso y salvavidas de la ventana de 24 h (la asignación se guarda aunque Meta rechace).

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_whatsapp_curso_colaborador.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_wac_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "wac.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-wac"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AsignacionCurso, Colaborador, Cuenta, Usuario, UsuarioCuenta  # noqa: E402
from app.services import whatsapp as swa  # noqa: E402

OK = 0
POSTS = []
MODO = {"rechazo": None}


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _fake_meta_post(payload):
    POSTS.append(payload)
    if MODO["rechazo"] == "24h":
        return {"enviado": False, "proveedor": "meta", "codigo": 131047, "detalle": "(#131047) Re-engagement message"}
    if MODO["rechazo"] == "excepcion":
        raise RuntimeError("Meta caído")
    return {"enviado": True, "proveedor": "meta", "detalle": 200, "wa_id": "wamid.x"}


swa._meta_post = _fake_meta_post
settings.whatsapp_provider = "meta"
settings.meta_plantilla_aviso = "aviso_generico"  # existe, pero el aviso de curso NO debe usarla

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Grupo CARBE", nombre_comercial="Grupo Carbe", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    col = Colaborador(cuenta_id=cuenta.id, codigo="COL-9001", nombre="Laura Méndez Ríos", puesto="Cajera", telefono="5512345678", activo=True)
    col2 = Colaborador(cuenta_id=cuenta.id, codigo="COL-9002", nombre="Pedro Sin Tel", puesto="Auxiliar", telefono="", activo=True)
    db.add_all([col, col2])
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    r = client.post("/capacitacion/generar", data={"tema": "Atención al cliente", "duracion_horas": "1", "contexto": ""})
    CUR = r.json()["id"]
    titulo = r.json()["titulo"]
    client.patch(f"/capacitacion/{CUR}/publicar")

    print("\n--- 1. Texto libre con la liga absoluta ---")
    POSTS.clear()
    r = client.post(f"/capacitacion/{CUR}/asignar", json={"colaborador_ids": ["COL-9001"], "notificar": True})
    check(r.status_code == 201 and len(r.json()["asignaciones"]) == 1, "curso asignado al colaborador")
    a = r.json()["asignaciones"][0]
    check(len(POSTS) == 1 and POSTS[0]["type"] == "text" and POSTS[0]["to"] == "525512345678", "se manda UN mensaje tipo text (sin plantilla) al WhatsApp del colaborador")
    body = POSTS[0]["text"]["body"]
    esperado = f"Hola Laura, se te asignó el curso de capacitación de Grupo Carbe: {titulo}. Accede a tu curso, interactúa con el avatar y descarga tu material en la siguiente liga: {settings.app_url}/capacitacion/{a['token']}"
    check(body == esperado, f"texto exacto: «{body[:90]}…»")
    check(a["liga"] == f"{settings.app_url}/capacitacion/{a['token']}" and body.endswith(a["liga"]), "liga absoluta al frontend del curso con el token de la asignación")
    check(r.json()["envios"][0]["whatsapp"]["enviado"] is True, "resultado del envío visible en la respuesta")

    print("\n--- 2. Salvavidas: ventana de 24 h vencida ---")
    MODO["rechazo"] = "24h"
    POSTS.clear()
    r = client.post("/capacitacion/generar", data={"tema": "Seguridad", "duracion_horas": "1", "contexto": ""})
    CUR2 = r.json()["id"]
    client.patch(f"/capacitacion/{CUR2}/publicar")
    r = client.post(f"/capacitacion/{CUR2}/asignar", json={"colaborador_ids": ["COL-9001"], "notificar": True})
    check(r.status_code == 201, "la asignación se guarda (201) aunque Meta rechace el texto")
    env = r.json()["envios"][0]["whatsapp"]
    check(env["enviado"] is False and env.get("fuera_de_ventana") is True and "ventana de 24h" in env["detalle"], "warning de ventana de 24 h en el resultado (y en el log)")
    check(len(POSTS) == 1 and all(p["type"] == "text" for p in POSTS), "NO se reintenta con plantilla de Meta")
    db.expire_all()
    check(db.query(AsignacionCurso).filter(AsignacionCurso.colaborador_id == col.id).count() == 2, "las 2 asignaciones existen en la base")

    print("\n--- 3. Salvavidas: excepción del proveedor ---")
    MODO["rechazo"] = "excepcion"
    r = client.post("/capacitacion/generar", data={"tema": "Ventas", "duracion_horas": "1", "contexto": ""})
    CUR3 = r.json()["id"]
    client.patch(f"/capacitacion/{CUR3}/publicar")
    r = client.post(f"/capacitacion/{CUR3}/asignar", json={"colaborador_ids": ["COL-9001", "COL-9002"], "notificar": True})
    check(r.status_code == 201 and len(r.json()["asignaciones"]) == 2, "con Meta caído la asignación se guarda igual (201)")
    envs = {e["persona"]: e for e in r.json()["envios"]}
    check(envs["Laura Méndez Ríos"]["whatsapp"]["enviado"] is False and "Meta caído" in envs["Laura Méndez Ríos"]["whatsapp"]["detalle"], "el error queda en el resultado, sin 500")
    check(envs["Pedro Sin Tel"].get("whatsapp") is None, "sin teléfono no se intenta enviar")
    MODO["rechazo"] = None

    db.close()

print(f"\n🎉 WhatsApp de curso a colaborador verificado: {OK} comprobaciones OK.")
