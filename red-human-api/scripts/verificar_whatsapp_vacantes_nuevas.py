"""Verificación (2026-09-16): el agente de WhatsApp SIEMPRE ve las vacantes publicadas en tiempo real.

Bug reportado: se eliminaron todas las vacantes de prueba (baja lógica) y al crear una nueva el bot
contestaba «no tenemos vacantes abiertas». Causa: con varias Cuentas activas y ninguna con
`whatsapp_comunicacion`, el webhook se quedaba SIEMPRE con la Cuenta más antigua, mientras la vacante
nueva se creaba desde la Cuenta predeterminada del usuario. Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_whatsapp_vacantes_nuevas.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_wa_vac_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "wa.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-wa"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
import app.routers.webhooks as rw  # noqa: E402

OK = 0
ENVIOS = []


import itertools as _it
_WAMID = _it.count(1)  # wamid único por mensaje simulado (el webhook deduplica reintentos)


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _fake(tel, *a, **k):
    ENVIOS.append((tel, a))
    return {"enviado": True, "proveedor": "meta"}


rw.enviar_mensaje = _fake
rw.enviar_lista_interactiva = _fake


def ultimo():
    return ENVIOS[-1][1][0] if ENVIOS else ""


def webhook(client, tel, texto="", sel="", receptor=""):
    if sel:
        m = {"from": tel, "id": f"wamid.{next(_WAMID)}.{len(ENVIOS)}.{sel}", "type": "interactive",
             "interactive": {"type": "list_reply", "list_reply": {"id": sel, "title": "x"}}}
    else:
        m = {"from": tel, "id": f"wamid.{next(_WAMID)}.{len(ENVIOS)}.{texto[:8]}", "type": "text", "text": {"body": texto}}
    valor = {"contacts": [{"profile": {"name": "Persona WA"}, "wa_id": tel}], "messages": [m]}
    if receptor:
        valor["metadata"] = {"display_phone_number": receptor}
    r = client.post("/webhooks/whatsapp", json={"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": valor}]}]})
    return r.json()


def nueva_vacante(client, titulo, publicar=True):
    r = client.post("/vacantes", json={
        "titulo": titulo, "descripcion": "Descripción", "generar_si_falta": False, "publicar": publicar,
        "ubicacion_estado": "Jalisco", "ubicacion_municipio": "Zapopan",
        "sueldo_desde": 9000, "sueldo_hasta": 11000, "sueldo_periodicidad": "mensual",
    })
    assert r.status_code == 201, r.text
    return r.json()


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    ca = Cuenta(nombre="Cuenta original", nombre_comercial="Original", estado="Activa")
    cb = Cuenta(nombre="Cuenta nueva", nombre_comercial="Nueva", estado="Activa")
    db.add_all([ca, cb])
    db.flush()
    db.add_all([UsuarioCuenta(usuario_id=admin.id, cuenta_id=ca.id), UsuarioCuenta(usuario_id=admin.id, cuenta_id=cb.id)])
    for v in db.query(Vacante).all():
        v.cuenta_id = ca.id
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: ca

    TEL = "5215512340001"

    # ================= 1. Estado inicial =================
    print("\n--- 1. Con vacantes publicadas en la Cuenta original ---")
    r = webhook(client, TEL, "Hola")
    check(r["accion"] == "menu_vacantes" and "Vacantes disponibles" in ultimo(), "saludo → menú de vacantes publicadas")
    publicadas = [v for v in client.get("/vacantes").json() if v["estado"] == "Publicada"]
    r = webhook(client, TEL, sel=publicadas[0]["id"])
    check(r["accion"] == "aviso_privacidad_enviado", "elige vacante de la lista → aviso de privacidad (proceso en curso)")

    # ================= 2. Eliminar TODAS las vacantes =================
    print("\n--- 2. Eliminar todas las vacantes (baja lógica) ---")
    for v in client.get("/vacantes").json():
        assert client.delete(f"/vacantes/{v['id']}").status_code == 200
    db.expire_all()
    check(db.query(Vacante).filter(Vacante.estado != "Eliminada").count() == 0, "todas las vacantes quedaron Eliminadas")
    check(db.query(Postulacion).filter(Postulacion.activa.is_(True)).count() == 0, "sus postulaciones activas se cerraron")
    r = webhook(client, TEL, "Hola")
    check(r["accion"] == "menu_vacantes" and "no tenemos vacantes abiertas" in ultimo(), "sin vacantes publicadas → aviso honesto «no tenemos vacantes abiertas»")
    r = webhook(client, TEL, sel=publicadas[0]["id"])
    check(r["accion"] == "menu_vacantes" and "no tenemos vacantes abiertas" in ultimo(), "tocar la lista VIEJA (vacante eliminada) no abre postulación en ella")
    db.expire_all()
    check(db.query(Postulacion).filter(Postulacion.activa.is_(True), Postulacion.vacante_id.isnot(None)).count() == 0, "ninguna postulación activa apunta a una vacante eliminada")

    # ================= 3. Vacante nueva en la MISMA Cuenta =================
    print("\n--- 3. Vacante nueva (misma Cuenta) → el bot la ve en el siguiente mensaje ---")
    v1 = nueva_vacante(client, "Cajero Turno Matutino")
    check(v1["estado"] == "Publicada", "vacante creada y publicada")
    r = webhook(client, TEL, "Hola")
    check(r["accion"] == "menu_vacantes" and "Vacantes disponibles" in ultimo(), "candidato que ya había escrito → vuelve a ver el menú con la vacante nueva")
    r = webhook(client, "5215512340002", "Hola")
    check("Vacantes disponibles" in ultimo(), "candidato nuevo → menú con la vacante nueva")
    r = webhook(client, "5215512340002", sel=v1["id"])
    check(r["accion"] == "aviso_privacidad_enviado", "la vacante nueva se puede elegir de la lista")

    # ================= 4. Borrador no sale; Publicar la hace visible al instante =================
    print("\n--- 4. Borrador → Publicar ---")
    v2 = nueva_vacante(client, "Almacenista", publicar=False)
    r = webhook(client, "5215512340003", "Hola")
    check(v2["estado"] == "Borrador" and r["accion"] == "menu_vacantes", "una vacante en Borrador NO se ofrece por WhatsApp")
    r = webhook(client, "5215512340003", sel=v2["id"])
    check(r["accion"] == "menu_vacantes", "ni se puede elegir por código mientras es Borrador")
    assert client.post(f"/vacantes/{v2['id']}/publicar", json={"plataformas": ["WhatsApp"]}).status_code == 200
    r = webhook(client, "5215512340003", sel=v2["id"])
    check(r["accion"] == "aviso_privacidad_enviado", "recién publicada → elegible en el siguiente mensaje (sin caché)")

    # ================= 5. El bug del cliente: vacante nueva desde OTRA Cuenta =================
    print("\n--- 5. Varias Cuentas activas: la vacante nueva vive en la Cuenta predeterminada del usuario ---")
    for v in client.get("/vacantes").json():
        client.delete(f"/vacantes/{v['id']}")
    app.dependency_overrides[cuenta_actual] = lambda: cb
    v3 = nueva_vacante(client, "Chofer Repartidor")
    db.expire_all()
    check(db.query(Vacante).filter(Vacante.codigo == v3["id"]).one().cuenta_id == cb.id, "la vacante nueva pertenece a la Cuenta nueva")
    r = webhook(client, TEL, "Hola")
    check(r["accion"] == "menu_vacantes" and "Vacantes disponibles" in ultimo(), "candidato viejo (Cuenta original sin vacantes) → el webhook enruta a la Cuenta con vacantes publicadas")
    r = webhook(client, "5215512340004", "Hola")
    check("Vacantes disponibles" in ultimo(), "candidato nuevo → también ve la vacante de la otra Cuenta")
    r = webhook(client, "5215512340004", sel=v3["id"])
    check(r["accion"] == "aviso_privacidad_enviado", "la elige y arranca el proceso")
    db.expire_all()
    p = db.query(Postulacion).filter(Postulacion.activa.is_(True), Postulacion.vacante_id.isnot(None)).order_by(Postulacion.id.desc()).first()
    check(p is not None and p.cuenta_id == cb.id, "la postulación nace en la Cuenta de la vacante")
    r = webhook(client, "5215512340004", "Sí acepto")
    check(r.get("postulacion") == p.codigo, "un proceso en curso no cambia de Cuenta aunque otra tenga vacantes")

    # ================= 6. Ruteo por número sigue mandando =================
    print("\n--- 6. whatsapp_comunicacion capturado → enruta por número ---")
    ca.whatsapp_comunicacion = "5533001122"
    ca.whatsapp_exclusivo = True  # 2026-09-17: sin la marca el número es compartido (multi-tenant)
    db.commit()
    r = webhook(client, "5215512340005", "Hola", receptor="525533001122")
    check(r["accion"] == "menu_vacantes" and "no tenemos vacantes abiertas" in ultimo(), "mensaje al número de la Cuenta original (sin vacantes) → se respeta el ruteo por número")

    # ================= 7. Liga pública vieja =================
    print("\n--- 7. /postular a una vacante eliminada ---")
    r = client.post("/candidatos/postular", data={"vacante": publicadas[0]["id"], "nombre": "Liga Vieja", "telefono": "5512349999", "correo": "liga@x.mx", "consentimiento": "true"})
    check(r.status_code in (404, 410), f"la liga pública de una vacante eliminada ya no acepta postulaciones ({r.status_code})")

    db.close()

print(f"\n🎉 WhatsApp ve las vacantes nuevas: {OK} comprobaciones OK.")
