"""Cambio 30 (2026-10-13): editor de procesos — catálogo, orden y actividades. Base desechable; Meta y Bot API SIMULADOS.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambio30.py

Bloque 1: catálogo («Solicitud web sin CV» / «con CV», «Oferta laboral»; sin «Análisis de CV», «Propuesta y aceptación»
ni «Carta de intención») y migración de plantillas y vacantes (las postulaciones conservan su copia).
"""

import asyncio
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
_dir = tempfile.mkdtemp(prefix="rh_c30_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c30.db").replace(chr(92), "/")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-c30"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CATALOGO_ACTIVIDADES, Cuenta, PlantillaProceso, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
ENVIADOS = []
APP_DIR = RAIZ.parent / "red-human-app"


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def leer(rel):
    return (APP_DIR / rel).read_text(encoding="utf-8")


async def _meta_falso(cuerpo):
    ENVIADOS.append(cuerpo)
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "mensaje_id": f"wamid.{len(ENVIADOS)}"}


whatsapp._meta_post = _meta_falso

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="C30", nombre_comercial="Logística C30", estado="Activa", slug="c30")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ================= Bloque 1 · catálogo y migración =================
    print("\n--- Bloque 1 · catálogo ---")
    op = client.get("/procesos/opciones").json()
    nombres = {t["valor"]: t["texto"] for t in op["tiposPaso"]}
    etapas = {e["valor"]: e["actividades"] for e in op["etapas"]}
    check(etapas["Prefiltro"][:2] == ["solicitud_sin_cv", "solicitud_con_cv"]
          and nombres["solicitud_sin_cv"] == "Solicitud web sin CV" and nombres["solicitud_con_cv"] == "Solicitud web con CV",
          "Prefiltro ofrece «Solicitud web sin CV» y «Solicitud web con CV»")
    check(not ({"solicitud_web", "analisis_cv", "condiciones", "carta_intencion"} & set(nombres)),
          "ya no existen «Solicitud web», «Análisis de CV», «Propuesta y aceptación» ni «Carta de intención» en el catálogo")
    check("oferta" in etapas["Contratación"] and nombres["oferta"] == "Oferta laboral", "Contratación ofrece «Oferta laboral»")
    pasos = sproc.normalizar_pasos([{"tipo": "solicitud_con_cv"}, {"tipo": "solicitud_sin_cv", "id": "otra-sol"}], previos=[])
    check(pasos[0]["tipo"] == "solicitud_web" and pasos[0]["con_cv"] is True and pasos[0]["nombre"] == "Solicitud web con CV"
          and pasos[1]["con_cv"] is False, "las dos entradas se guardan como solicitud web con su `con_cv`")
    check(sproc.es_corporativa([{"tipo": "solicitud_web", "con_cv": True}])
          and not sproc.ruta_es_masiva([{"tipo": "solicitud_web", "con_cv": True}, {"tipo": "entrevista_whatsapp"}]),
          "«con CV» es la ruta corporativa (el análisis de CV va con la solicitud)")
    corp = sproc.ruta_base("corporativo")["pasos"]
    check(not any(x["tipo"] == "analisis_cv" for x in corp) and corp[0]["con_cv"]
          and any(x["tipo"] == "oferta" for x in corp) and not any(x["tipo"] in ("condiciones", "carta_intencion") for x in corp),
          "las rutas base ya traen la estructura nueva")

    print("\n--- Bloque 1 · migración de procesos existentes ---")
    viejos = [{"id": "sw", "tipo": "solicitud_web", "nombre": "Solicitud web", "etapa": "Prefiltro"},
              {"id": "cv", "tipo": "analisis_cv", "etapa": "Prefiltro"},
              {"id": "pw", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro", "depende_de": ["cv"]},
              {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"},
              {"id": "prop", "tipo": "condiciones", "etapa": "Contratación"},
              {"id": "carta", "tipo": "carta_intencion", "etapa": "Contratación", "depende_de": ["prop"]},
              {"id": "cc", "tipo": "carta_contrato", "etapa": "Contratación", "depende_de": ["carta"]}]
    nuevos, cambio = sproc.pasos_cambio30(viejos)
    ids = [x["id"] for x in nuevos]
    sol = next(x for x in nuevos if x["tipo"] == "solicitud_web")
    check(cambio and "cv" not in ids and sol["con_cv"] and sol["nombre"] == "Solicitud web con CV",
          "«Análisis de CV» → la solicitud pasa a «con CV» y la actividad se quita")
    check([x["tipo"] for x in nuevos if x["etapa"] == "Contratación"] == ["oferta", "carta_contrato"],
          "«Propuesta y aceptación» + «Carta de intención» → UNA «Oferta laboral»")
    cc = next(x for x in nuevos if x["id"] == "cc")
    check("oferta" in cc["depende_de"] and "carta" not in cc["depende_de"]
          and "cv" not in next(x for x in nuevos if x["id"] == "pw")["depende_de"], "las dependencias se re-apuntan")
    check(sproc.pasos_cambio30(nuevos)[1] is False, "idempotente")
    pl = PlantillaProceso(cuenta_id=cuenta.id, nombre="Proceso viejo", pasos=sproc.normalizar_pasos(viejos), etapas={}, version=3,
                          activa=True, predeterminada=False, creado_por="prueba", actualizada_por="prueba", ruta_base="")
    db.add(pl)
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).first()
    v.cuenta_id = cuenta.id
    v.proceso = {"pasos": sproc.normalizar_pasos(viejos), "version": 2}
    db.commit()
    c = _crear_candidato(db, cuenta.id, "Congelada", "Portal", False, telefono="5530300001")
    pc = crear_postulacion(db, c, v, cuenta.id, "web", consentimiento=True)
    db.commit()
    antes_post = json.dumps(pc.proceso, sort_keys=True, default=str)
    r = sproc.migrar_procesos_cambio30(db, aplicar=False)
    db.rollback()
    check(pl.id in r["plantillas"] and v.codigo in r["vacantes"], "simulación: detecta la plantilla y la vacante")
    r = sproc.migrar_procesos_cambio30(db)
    db.commit()
    db.expire_all()
    pl, v, pc = db.get(PlantillaProceso, pl.id), db.get(Vacante, v.id), db.get(Postulacion, pc.id)
    check(pl.version == 4 and not any(x["tipo"] in sproc.TIPOS_CAMBIO30 for x in pl.pasos), "la plantilla queda migrada (sube versión)")
    check(v.proceso["version"] == 3 and any(x["tipo"] == "oferta" for x in v.proceso["pasos"]), "la vacante queda migrada (sube versión)")
    check(json.dumps(pc.proceso, sort_keys=True, default=str) == antes_post, "las postulaciones conservan su copia congelada")
    check(not sproc.migrar_procesos_cambio30(db)["plantillas"], "volver a correrla no cambia nada")
    check((RAIZ / "scripts/migrar_cambio30.py").exists(), "script de migración (simulación por defecto, --forzar aplica)")

    app.dependency_overrides.clear()
    db.close()

print(f"\n🎉 Cambio 30: {OK} comprobaciones OK")
