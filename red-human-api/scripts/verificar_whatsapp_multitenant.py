"""Verificación (2026-09-17): WhatsApp multi-tenant — UN número maestro para varias Cuentas.

- número compartido: el menú lista las vacantes publicadas de TODAS las Cuentas activas (secciones
  por empresa; con >10, primero la empresa);
- al elegir, la postulación, sus mensajes y su expediente quedan en la cuenta_id de la vacante;
- aislamiento: cada Cuenta ve solo su persona/postulación; el candidato ve la empresa de la vacante;
- retrocompatibilidad: `Cuenta.whatsapp_exclusivo` + número → ruteo dedicado (solo esa Cuenta).
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_whatsapp_multitenant.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_mt_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "mt.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-mt"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cuenta, Mensaje, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
import app.routers.webhooks as rw  # noqa: E402

OK = 0
ENVIOS = []  # (tel, texto/encabezado, opciones, secciones)


import itertools as _it
_WAMID = _it.count(1)  # wamid único por mensaje simulado (el webhook deduplica reintentos)


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _fake_msg(tel, texto="", *a, **k):
    ENVIOS.append((tel, texto, None, None))
    return {"enviado": True, "proveedor": "meta"}


async def _fake_lista(tel, encabezado, cuerpo, boton, opciones, secciones=None):
    ENVIOS.append((tel, encabezado, opciones, secciones))
    return {"enviado": True, "proveedor": "meta"}


rw.enviar_mensaje = _fake_msg
rw.enviar_lista_interactiva = _fake_lista


def filas_ultima_lista():
    _, _, opciones, secciones = ENVIOS[-1]
    if secciones:
        return [o for sec in secciones for o in sec["opciones"]]
    return opciones or []


def webhook(client, tel, texto="", sel="", receptor=""):
    if sel:
        m = {"from": tel, "id": f"wamid.{next(_WAMID)}.{len(ENVIOS)}.{sel}", "type": "interactive",
             "interactive": {"type": "list_reply", "list_reply": {"id": sel, "title": "x"}}}
    else:
        m = {"from": tel, "id": f"wamid.{next(_WAMID)}.{len(ENVIOS)}.{texto[:8]}", "type": "text", "text": {"body": texto}}
    valor = {"contacts": [{"profile": {"name": "Persona WA"}, "wa_id": tel}], "messages": [m]}
    if receptor:
        valor["metadata"] = {"display_phone_number": receptor}
    return client.post("/webhooks/whatsapp", json={"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": valor}]}]}).json()


def nueva_vacante(client, titulo):
    r = client.post("/vacantes", json={
        "titulo": titulo, "descripcion": "Descripción", "generar_si_falta": False, "publicar": True,
        "ubicacion_estado": "Jalisco", "ubicacion_municipio": "Zapopan",
        "sueldo_desde": 9000, "sueldo_hasta": 11000, "sueldo_periodicidad": "mensual",
    })
    assert r.status_code == 201, r.text
    return r.json()


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    carbe = Cuenta(nombre="Grupo CARBE", nombre_comercial="Grupo CARBE", estado="Activa")
    growtea = Cuenta(nombre="Growtea", nombre_comercial="Growtea", estado="Activa")
    db.add_all([carbe, growtea])
    db.flush()
    db.add_all([UsuarioCuenta(usuario_id=admin.id, cuenta_id=carbe.id), UsuarioCuenta(usuario_id=admin.id, cuenta_id=growtea.id)])
    for v in db.query(Vacante).all():  # las de ejemplo estorban: se archivan
        v.estado = "Cerrada"
        v.cuenta_id = carbe.id
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin

    app.dependency_overrides[cuenta_actual] = lambda: carbe
    vc = nueva_vacante(client, "Abogado Fiscalista")
    app.dependency_overrides[cuenta_actual] = lambda: growtea
    vg = nueva_vacante(client, "Barista Growtea")

    # ================= 1. Número compartido: menú con todas las empresas =================
    print("\n--- 1. Número maestro compartido ---")
    TEL = "5215530000001"
    r = webhook(client, TEL, "Hola")
    check(r["accion"] == "menu_vacantes", "candidato nuevo → menú")
    filas = filas_ultima_lista()
    check({f["id"] for f in filas} == {vc["id"], vg["id"]}, "el menú trae las vacantes publicadas de AMBAS Cuentas")
    check(ENVIOS[-1][3] and [s["titulo"] for s in ENVIOS[-1][3]] == ["Grupo CARBE", "Growtea"], "agrupadas en secciones por empresa")

    # elige la de Growtea
    r = webhook(client, TEL, sel=vg["id"])
    check(r["accion"] == "aviso_privacidad_enviado", "elige «Barista Growtea» → aviso de privacidad")
    db.expire_all()
    p = db.query(Postulacion).filter(Postulacion.codigo == r["postulacion"]).one()
    check(p.cuenta_id == growtea.id and p.vacante_id and p.candidato.cuenta_id == growtea.id, "la postulación y la persona quedaron en la Cuenta de la vacante (Growtea)")
    check(all(m.candidato_id == p.candidato_id for m in p.mensajes) and len(p.mensajes) >= 1, "los mensajes del chat viajaron con la postulación")
    check(db.query(Candidato).filter(Candidato.cuenta_id == carbe.id, Candidato.wa_id == TEL).count() == 0, "no quedó persona huérfana en la Cuenta ancla (se movió, no se duplicó)")
    check("Growtea" in ENVIOS[-1][1] and "CARBE" not in ENVIOS[-1][1], "aislamiento: el aviso menciona solo a Growtea")
    r = webhook(client, TEL, "Sí acepto")
    check(r["accion"] in ("prefiltro_iniciado", "solicitando_nombre"), "consentimiento → arranca el proceso en la Cuenta correcta")

    # Aislamiento en el dashboard
    app.dependency_overrides[cuenta_actual] = lambda: growtea
    kan_g = client.get("/candidatos").json()
    app.dependency_overrides[cuenta_actual] = lambda: carbe
    kan_c = client.get("/candidatos").json()
    check(any(x["id"] == p.codigo for x in kan_g) and not any(x["id"] == p.codigo for x in kan_c), "Growtea ve la postulación en su Kanban; CARBE no")

    # ================= 2. La misma persona aplica después a CARBE =================
    print("\n--- 2. Misma persona, segunda empresa ---")
    db.expire_all()
    p.prefiltro_completo = True  # ya no espera respuesta (ver Postulacion.espera_respuesta)
    db.commit()
    r = webhook(client, TEL, sel=vc["id"])
    check(r["accion"] == "aviso_privacidad_enviado", "elige ahora «Abogado Fiscalista» (CARBE) desde el mismo número")
    db.expire_all()
    p2 = db.query(Postulacion).filter(Postulacion.codigo == r["postulacion"]).one()
    check(p2.cuenta_id == carbe.id and p2.id != p.id, "nace una postulación distinta en CARBE")
    check(p2.candidato_id != p.candidato_id and p2.candidato.cuenta_id == carbe.id and p2.candidato.wa_id == TEL, "con su propia fila de persona en CARBE (la de Growtea sigue intacta)")
    check(db.query(Postulacion).get(p.id).cuenta_id == growtea.id, "la postulación de Growtea no se movió")
    r = webhook(client, TEL, "Acepto")
    check(r.get("postulacion") == p2.codigo, "la conversación sigue con la postulación recién elegida (CARBE)")

    # ================= 3. Más de 10 vacantes → primero la empresa =================
    print("\n--- 3. >10 vacantes: pregunta la empresa primero ---")
    app.dependency_overrides[cuenta_actual] = lambda: growtea
    for i in range(10):
        nueva_vacante(client, f"Vacante Growtea {i}")
    TEL2 = "5215530000002"
    r = webhook(client, TEL2, "Hola")
    filas = filas_ultima_lista()
    check(ENVIOS[-1][1].startswith("🏢") and {f["id"] for f in filas} == {f"CTA-{carbe.id}", f"CTA-{growtea.id}"}, "con 12 vacantes se pregunta primero la empresa (CTA-<id>)")
    r = webhook(client, TEL2, sel=f"CTA-{carbe.id}")
    check(r["accion"] == "menu_vacantes_empresa" and [f["id"] for f in filas_ultima_lista()] == [vc["id"]], "elige CARBE → lista solo sus vacantes")
    r = webhook(client, TEL2, sel=vc["id"])
    db.expire_all()
    p3 = db.query(Postulacion).filter(Postulacion.codigo == r["postulacion"]).one()
    check(r["accion"] == "aviso_privacidad_enviado" and p3.cuenta_id == carbe.id, "y su postulación queda en CARBE")
    TEL3 = "5215530000003"
    webhook(client, TEL3, "Hola")
    r = webhook(client, TEL3, "2")
    filas = filas_ultima_lista()
    check(r["accion"] == "menu_vacantes_empresa" and len(filas) == 10 and filas[-1]["id"] == f"MAS-{growtea.id}-1",
          "«2» escrito elige la segunda empresa (Growtea, 11 vacantes) → 9 vacantes + «Ver más vacantes» (tope de Meta: 10 filas)")
    vistas = {f["id"] for f in filas[:-1]}
    r = webhook(client, TEL3, sel=f"MAS-{growtea.id}-1")
    filas = filas_ultima_lista()
    check(r["accion"] == "menu_vacantes_pagina" and len(filas) == 3 and filas[-1]["id"] == f"MAS-{growtea.id}-0",
          "«Ver más vacantes» → página 2 con las 2 restantes + «Volver al inicio»")
    check(not ({f["id"] for f in filas[:-1]} & vistas) and len(vistas) + 2 == 11, "entre las dos páginas se ofrecen las 11 vacantes, sin repetir")
    r = webhook(client, TEL3, sel=filas[0]["id"])
    check(r["accion"] == "aviso_privacidad_enviado", "una vacante de la página 2 se elige normal")

    # ================= 4. Retrocompatibilidad: número exclusivo (Premium) =================
    print("\n--- 4. Número exclusivo por Cuenta (Premium) ---")
    r = client.patch(f"/cuentas/{carbe.id}", json={"whatsapp_comunicacion": "5533001122", "whatsapp_exclusivo": True})
    check(r.status_code == 200 and r.json()["whatsappExclusivo"] is True, "PATCH /cuentas marca el número como exclusivo")
    TEL4 = "5215530000004"
    r = webhook(client, TEL4, "Hola", receptor="525533001122")
    filas = filas_ultima_lista()
    check(all(f["id"] == vc["id"] for f in filas) and len(filas) == 1, "mensaje al número exclusivo de CARBE → solo vacantes de CARBE")
    db.expire_all()
    persona4 = db.query(Candidato).filter(Candidato.wa_id == TEL4).one()
    check(persona4.cuenta_id == carbe.id, "la persona nace directamente en CARBE (ruteo dedicado)")
    r = webhook(client, "5215530000005", "Hola", receptor="525599999999")
    check(ENVIOS[-1][1].startswith("🏢"), "otro número (maestro) sigue compartido para todas las Cuentas")
    # 2026-10-09 (tenant routing): el número propio de una Cuenta enruta a ESA Cuenta aunque no sea Premium
    r = client.patch(f"/cuentas/{carbe.id}", json={"whatsapp_exclusivo": False})
    r = webhook(client, "5215530000006", "Hola", receptor="525533001122")
    filas = filas_ultima_lista()
    check(not ENVIOS[-1][1].startswith("🏢") and filas and all(f["id"] == vc["id"] for f in filas),
          "sin la marca de exclusivo, el número que es de CARBE sigue ofreciendo SOLO vacantes de CARBE")
    r = client.patch(f"/cuentas/{carbe.id}", json={"whatsapp_comunicacion": ""})
    r = webhook(client, "5215530000007", "Hola", receptor="525533001122")
    check(ENVIOS[-1][1].startswith("🏢"), "un número que ninguna Cuenta tiene registrado sigue compartido (número maestro)")

    db.close()

print(f"\n🎉 WhatsApp multi-tenant verificado: {OK} comprobaciones OK.")
