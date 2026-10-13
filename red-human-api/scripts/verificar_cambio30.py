"""Cambio 30 (2026-10-13): editor de procesos — catálogo, orden y actividades. Base desechable; Meta y Bot API SIMULADOS.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambio30.py

Bloque 1: catálogo («Solicitud web sin CV» / «con CV», «Oferta laboral»; sin «Análisis de CV», «Propuesta y aceptación»
ni «Carta de intención») y migración de plantillas y vacantes (las postulaciones conservan su copia).
Bloque 2: una sola solicitud (sin CV o con CV); con CV el archivo es obligatorio; «Oferta laboral»: carta con los datos de
la vacante, «Enviar oferta» por WhatsApp, vista del candidato con «Acepto» / «No acepto»; «Generar carta oferta» sin la
actividad (no bloquea, registra la respuesta con su fecha).
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
    con = sproc.normalizar_pasos([{"tipo": "solicitud_con_cv"}], previos=[])[0]
    sin = sproc.normalizar_pasos([{"tipo": "solicitud_sin_cv"}], previos=[])[0]
    check(con["tipo"] == sin["tipo"] == "solicitud_web" and con["con_cv"] is True and con["nombre"] == "Solicitud web con CV"
          and sin["con_cv"] is False, "las dos entradas se guardan como solicitud web con su `con_cv`")
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

    # ================= Bloque 2 · solicitud con CV y oferta laboral =================
    print("\n--- Bloque 2 · una sola solicitud (sin CV o con CV) ---")
    r = client.post("/procesos/plantillas", json={"nombre": "Dos solicitudes", "pasos": [
        {"tipo": "solicitud_sin_cv"}, {"tipo": "solicitud_con_cv", "id": "otra"}, {"tipo": "prefiltro_whatsapp"}]})
    check(r.status_code == 400 and "una sola solicitud" in r.json()["detail"], "un proceso no puede tener las dos solicitudes")
    ed = leer("components/dashboard/procesos/editor-proceso.tsx")
    check("se REEMPLAZAN entre sí" in ed and "esSolicitud" in ed, "en el editor, agregar una reemplaza a la otra")
    vcv = Vacante(codigo="VAC-C30CV", titulo="Analista", estado="Publicada", cuenta_id=cuenta.id, slug="analista-c30",
                  requisitos="Licenciatura · Excel avanzado · Inglés intermedio", descripcion="Análisis.")
    db.add(vcv)
    db.flush()
    vcv.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": [{"tipo": "solicitud_con_cv"}, {"tipo": "prefiltro_whatsapp"}]})
    db.commit()
    app.dependency_overrides.pop(cuenta_actual, None)
    r = client.post("/candidatos/postular", data={"vacante": vcv.slug, "nombre": "Sin CV", "telefono": "5530300002", "consentimiento": "true"})
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    check(r.status_code == 400 and "CV" in r.json()["detail"], "«Solicitud web con CV»: el archivo es obligatorio en la web")
    check(client.get(f"/vacantes/{vcv.codigo}").json()["pideCv"], "el formulario público pide el CV (el análisis corre al recibirlo)")

    print("\n--- Bloque 2 · oferta laboral de la ruta ---")
    vo = db.query(Vacante).filter(Vacante.id == v.id).one()
    vo.sueldo, vo.ubicacion, vo.modalidad, vo.beneficios = "Desde $12,000 MXN mensuales", "Zapopan, Jalisco", "Presencial", ["Vales de despensa"]
    vo.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": [{"tipo": "solicitud_sin_cv"},
                                                                         {"tipo": "oferta", "etapa": "Contratación"},
                                                                         {"tipo": "carta_contrato", "etapa": "Contratación"}]})
    db.commit()

    def en_contratacion(nombre, tel, vac):
        cc = _crear_candidato(db, cuenta.id, nombre, "Portal", False, telefono=tel)
        pp = crear_postulacion(db, cc, vac, cuenta.id, "web", consentimiento=True)
        db.commit()
        rr = client.patch(f"/candidatos/{pp.codigo}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True,
                                                                   "comentario": "Prueba de la oferta laboral"})
        assert rr.status_code == 200, rr.text
        db.refresh(pp)
        return pp

    def paso_oferta(pp):
        db.refresh(pp)
        return next(x for x in sproc.estado_pasos(pp) if x["tipo"] == "oferta")

    po = en_contratacion("Oscar Oferta", "5530300003", vo)
    x = paso_oferta(po)
    check(x["accion"] and x["accion"]["clave"] == "enviar_oferta" and x["estadoUnificadoTexto"] == "Pendiente: enviar oferta",
          "en Contratación la actividad pide «Enviar oferta»")
    r = client.get(f"/candidatos/{po.codigo}/oferta").json()
    check(r["enRuta"] and r["datos"]["puesto"] == vo.titulo and r["datos"]["sueldo"] == "Desde $12,000 MXN mensuales"
          and r["datos"]["ubicacion"] == "Zapopan, Jalisco" and "Vales de despensa" in r["datos"]["prestaciones"],
          "la carta oferta se genera con los datos de la vacante")
    desde = len(ENVIADOS)
    r = client.post(f"/candidatos/{po.codigo}/oferta", json={"datos": {**r["datos"], "horario": "L-V 8:00 a 17:00"}})
    t = [c for c in ENVIADOS[desde:] if c.get("type") == "template"]
    liga = r.json()["liga"]
    check(r.status_code == 200 and t and t[0]["template"]["name"] == "oferta_laboral_rh"
          and [y["text"] for y in t[0]["template"]["components"][0]["parameters"]] == ["Oscar", liga] and "/oferta/" in liga,
          "«Enviar oferta» → plantilla de WhatsApp ({{1}} nombre, {{2}} liga)")
    check(r.json()["texto"] == f"Oscar, tenemos una oferta para ti. Revísala aquí: {liga}", "texto exacto del aviso")
    check(paso_oferta(po)["estado"] == "en_curso", "esperando la respuesta del candidato")
    token = liga.rsplit("/", 1)[1]
    r = client.get(f"/ofertas/publica/{token}")
    check(r.status_code == 200 and r.json()["datos"]["horario"] == "L-V 8:00 a 17:00" and r.json()["respuesta"] is None,
          "el candidato ve la carta en su liga")
    r = client.post(f"/ofertas/publica/{token}/responder", json={"acepta": True})
    x = paso_oferta(po)
    check(r.status_code == 200 and x["estado"] == "completada" and x["cumpleRegla"], "«Acepto» → la actividad se cumple (avanza)")
    check(client.post(f"/ofertas/publica/{token}/responder", json={"acepta": False}).status_code == 409, "una sola respuesta por oferta")

    pn = en_contratacion("Nora Noacepta", "5530300004", vo)
    r = client.post(f"/candidatos/{pn.codigo}/oferta", json={})
    token = r.json()["liga"].rsplit("/", 1)[1]
    client.post(f"/ofertas/publica/{token}/responder", json={"acepta": False})
    x = paso_oferta(pn)
    db.refresh(pn)
    check(x["estadoUnificadoTexto"] == "Oferta no aceptada" and not x["cumpleRegla"] and pn.activa,
          "«No acepto» → «Oferta no aceptada» (no descarta: RH decide)")
    pag = leer("app/oferta/[token]/page.tsx")
    check("Acepto" in pag and "No acepto" in pag, "vista del candidato con «Acepto» y «No acepto»")

    print("\n--- Bloque 2 · «Generar carta oferta» sin la actividad en la ruta ---")
    vs = Vacante(codigo="VAC-C30SO", titulo="Ayudante", estado="Publicada", cuenta_id=cuenta.id, sueldo="Desde $9,000 MXN mensuales",
                 requisitos="Secundaria · Turnos · Mayor de edad")
    db.add(vs)
    db.flush()
    vs.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": [{"tipo": "solicitud_sin_cv"},
                                                                         {"tipo": "carta_contrato", "etapa": "Contratación"}]})
    db.commit()
    pb = en_contratacion("Beto Boton", "5530300005", vs)
    r = client.get(f"/candidatos/{pb.codigo}/oferta").json()
    check(not r["enRuta"], "la ruta no trae «Oferta laboral»")
    r = client.post(f"/candidatos/{pb.codigo}/oferta", json={})
    token = r.json()["liga"].rsplit("/", 1)[1]
    client.post(f"/ofertas/publica/{token}/responder", json={"acepta": True})
    db.refresh(pb)
    ficha = client.get(f"/candidatos/{pb.codigo}").json()
    check(ficha["oferta"]["estado"] == "aceptada" and ficha["oferta"]["respondidaEn"] and not ficha["oferta"]["enRuta"]
          and any(h.get("evento") == "oferta_aceptada" for h in pb.historial),
          "registra «Oferta aceptada» con su fecha sin bloquear la ruta")
    check(pb.etapa == "Contratación" and not any(x["tipo"] == "oferta" for x in sproc.estado_pasos(pb)), "la ruta no cambia")
    seg_tsx = leer("components/dashboard/procesos/seguimiento-proceso.tsx")
    check("BotonCartaOferta" in seg_tsx and 'x.tipo === "oferta"' in seg_tsx, "la ficha muestra «Generar carta oferta» si la ruta no la trae")

    app.dependency_overrides.clear()
    db.close()

print(f"\n🎉 Cambio 30: {OK} comprobaciones OK")
