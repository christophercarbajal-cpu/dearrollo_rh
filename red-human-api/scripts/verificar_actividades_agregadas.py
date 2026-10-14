"""Actividades agregadas (prueba del 11 de octubre, Bloque 4 — cambio 12). Base desechable, SIN red.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_actividades_agregadas.py

Cubre: toda actividad agregada es OBLIGATORIA (aunque llegue `obligatorio: false`); si su columna es anterior, el
candidato REGRESA a ella (de Contratación a Filtro humano); lo siguiente queda bloqueado y «Enviar propuesta» responde 409;
la tarjeta dice «Actividad agregada: [nombre] pendiente»; con la propuesta ya enviada sale la advertencia; «Omitir» lo
regresa a su columna original y completarla también.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_agregadas_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "agregadas.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-agregadas"
os.environ["SEMBRAR_DEMO"] = "true"

from _ruta_prueba import fijar_ruta  # noqa: E402  (Cambio 36: ruta inicial de vacantes sembradas)
from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

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
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    cuenta = Cuenta(nombre="Agregadas SA", nombre_comercial="Agregadas", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    obtener(db).modo_prueba = False
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).first()
    v.cuenta_id, v.cliente_id, v.proceso, v.responsable_id, v.titulo = cuenta.id, None, {}, admin.id, "Almacenista"
    db.commit()
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    PASOS = [{"id": "solicitud", "tipo": "solicitud_web", "etapa": "Prefiltro"},
             {"id": "condiciones", "tipo": "condiciones", "etapa": "Contratación", "obligatorio": True},
             {"id": "contrato", "tipo": "carta_contrato", "etapa": "Contratación", "obligatorio": True, "depende_de": ["condiciones"]}]
    assert fijar_ruta(db, v.codigo, json={"pasos": PASOS}).status_code == 200
    tel = iter(range(5589000000, 5589999999))

    def nueva(nombre):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": str(next(tel)), "correo": f"{nombre.split()[0].lower()}@agregadas.mx",
                                             "vacante": v.codigo, "consentimiento": True, "fuente": "RH"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def post(codigo):
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=codigo).one()

    def a_contratacion(codigo):
        r = client.patch(f"/candidatos/{codigo}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True,
                                                               "comentario": "Prueba: llega a Contratación"})
        assert r.status_code == 200, r.text
        client.post(f"/candidatos/{codigo}/expediente")

    def seguimiento(codigo):
        return client.get(f"/procesos/postulaciones/{codigo}").json()

    def pasos_de(s):
        return [x for e in s["etapas"] for x in e["pasos"]]

    def agregar(codigo, tipo="entrevista_humana", obligatorio=False):
        return client.post(f"/procesos/postulaciones/{codigo}/actividades", data={"datos": json.dumps({
            "tipo": tipo, "obligatorio": obligatorio,
            "config": {"evaluador": {"tipo": "interno", "usuario_id": admin.id}, "iniciar_al_guardar": False}})})

    # ================= 1. Obligatoria y retroceso de columna =================
    print("\n--- 1. Agregar una entrevista humana en Contratación ---")
    P = nueva("Diana Agregada")
    a_contratacion(P)
    p = post(P)
    p.analisis = {**(p.analisis or {}), "propuesta": {"estado": "enviada"}}  # la propuesta ya había salido
    db.commit()
    check(post(P).etapa == "Contratación", "el candidato está en Contratación")
    r = agregar(P, obligatorio=False)
    check(r.status_code == 201, f"«Agregar actividad» ({r.status_code} {r.text[:150]})")
    d = r.json()
    paso_id = d["paso"]["id"]
    check(d["paso"]["obligatorio"] is True, "toda actividad agregada es OBLIGATORIA (aunque llegue «obligatorio: false»)")
    check(d["retroceso"] == {"desde": "Contratación", "a": "Entrevista Humana"} and post(P).etapa == "Entrevista Humana",
          "el candidato REGRESA de Contratación a Filtro humano (la columna de la actividad)")
    check(d["advertencia"].startswith("La propuesta ya fue enviada"), "con la propuesta ya enviada sale la advertencia")
    check(any(h.get("evento") == "retroceso_por_actividad" for h in post(P).historial or []), "el retroceso queda en el historial")

    # ================= 2. Bloqueos =================
    print("\n--- 2. Lo siguiente queda bloqueado ---")
    s = seguimiento(P)
    ag = s["actividadAgregada"] or {}
    check(ag.get("texto") == "Actividad agregada: Entrevista humana pendiente" and ag.get("desde") == "Contratación",
          "la ficha muestra «Actividad agregada: Entrevista humana pendiente»")
    cond = next(x for x in pasos_de(s) if x["id"] == "condiciones")
    check(not cond["disponible"] and cond["espera"].startswith("Bloqueada: primero «Entrevista humana»"),
          f"las actividades siguientes quedan bloqueadas: {cond['espera']}")
    r = client.post(f"/candidatos/{P}/propuesta", json={"puesto": "Almacenista", "sueldo": "$9,000", "tipo_contratacion": "Indeterminado",
                                                       "fecha_ingreso": "2026-12-01"})
    check(r.status_code == 409 and "Primero completa «Entrevista humana»" in r.json()["detail"], "«Enviar propuesta» queda desactivado (409)")
    lista = client.get(f"/candidatos?vacante={v.codigo}").json()
    t = next(x for x in lista if x["id"] == P)
    check(t["added_activity"] == "Actividad agregada: Entrevista humana pendiente", "la tarjeta dice «Actividad agregada: [nombre] pendiente»")
    check(client.get(f"/candidatos/{P}").json()["actividadAgregada"]["nombre"] == "Entrevista humana", "la ficha la trae en el detalle")

    # ================= 3. Omitir → vuelve a su columna =================
    print("\n--- 3. «Omitir» lo regresa a su columna original ---")
    r = client.post(f"/procesos/postulaciones/{P}/pasos/{paso_id}/omitir", json={"motivo": "Ya se entrevistó en otra vacante"})
    check(r.status_code == 200, f"omitir la actividad agregada ({r.status_code} {r.text[:120]})")
    p = post(P)
    check(p.etapa == "Contratación" and not sproc.agregadas_pendientes(p), "al omitirla, el candidato vuelve a Contratación")
    check(seguimiento(P)["actividadAgregada"] is None and not next(x for x in client.get(f"/candidatos?vacante={v.codigo}").json()
                                                                   if x["id"] == P).get("added_activity"),
          "la tarjeta y la ficha ya no muestran la actividad pendiente")

    # ================= 4. Completarla → también vuelve =================
    print("\n--- 4. Completarla también lo regresa ---")
    r = agregar(P)
    pid2 = r.json()["paso"]["id"]
    check(post(P).etapa == "Entrevista Humana", "una segunda actividad agregada lo vuelve a regresar")
    r = client.post(f"/procesos/postulaciones/{P}/pasos/{pid2}/resultado",
                    data={"conclusion": "avanzar", "realizada_por": "Ana Entrevistadora", "comentarios": "Bien"})
    check(r.status_code in (200, 201), f"el entrevistador registra «Avanzar» ({r.status_code} {r.text[:120]})")
    p = post(P)
    check(p.etapa == "Contratación" and not sproc.agregadas_pendientes(p), "al completarla, el candidato vuelve a Contratación")

    # ================= 5. En su propia columna no hay retroceso =================
    print("\n--- 5. Actividad de la columna actual ---")
    Q = nueva("Ernesto Agregado")
    r = client.post(f"/procesos/postulaciones/{Q}/pasos", json={"tipo": "documentos", "obligatorio": False})
    check(r.status_code == 201 and r.json()["paso"]["obligatorio"] is True and post(Q).etapa == "Prefiltro",
          "«documentos» en Prefiltro: obligatoria, sin retroceso (ya está en su columna)")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — actividades agregadas (C12)")
