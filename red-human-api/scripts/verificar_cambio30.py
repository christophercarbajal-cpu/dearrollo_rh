"""Cambio 30 (2026-10-13): editor de procesos — catálogo, orden y actividades. Base desechable; Meta y Bot API SIMULADOS.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambio30.py

Bloque 1: catálogo («Solicitud web sin CV» / «con CV», «Oferta laboral»; sin «Análisis de CV», «Propuesta y aceptación»
ni «Carta de intención») y migración de plantillas y vacantes (las postulaciones conservan su copia).
Bloque 2: una sola solicitud (sin CV o con CV); con CV el archivo es obligatorio; «Oferta laboral»: carta con los datos de
la vacante, «Enviar oferta» por WhatsApp, vista del candidato con «Acepto» / «No acepto»; «Generar carta oferta» sin la
actividad (no bloquea, registra la respuesta con su fecha).
Bloque 3: Capacitación «En plataforma» (Filtro RH) / «Presencial» (Filtro humano) / a elegir (Contratación y Onboarding);
presencial = citas de la entrevista humana con «Instructor» y textos propios; en plataforma = curso por WhatsApp
(reprueba → «No aprobó el curso»); «Otra actividad» bloquea la ruta hasta «Marcar como realizada».
Bloque 4: motor de ordenamiento del editor (lib/orden-proceso.ts): posición sugerida al agregar, Prefiltro fijo y sin
flechas, «Contrato y firma» siempre al final de Contratación y sin flechas, flechas libres en las demás etapas.
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


# Cambio 46: el aviso al evaluador intenta primero texto libre; estos números NO han escrito en 24 h (Meta lo rechaza) y
# el aviso sigue con la plantilla
FUERA_DE_VENTANA = {"523311112222"}


async def _meta_falso(cuerpo):
    ENVIADOS.append(cuerpo)
    if cuerpo.get("type") == "text" and cuerpo.get("to") in FUERA_DE_VENTANA:
        return {"enviado": False, "proveedor": "meta", "detalle": "131047: Re-engagement message", "codigo": 131047}
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

    # ================= Bloque 3 · capacitación y otra actividad =================
    print("\n--- Bloque 3 · modalidades de Capacitación ---")
    op = client.get("/procesos/opciones").json()
    etapas = {e["valor"]: e["actividades"] for e in op["etapas"]}
    check(all("induccion" in etapas[e] for e in ("Entrevista IA", "Entrevista Humana", "Contratación", "Onboarding"))
          and "induccion" not in etapas["Prefiltro"], "Capacitación en Filtro RH, Filtro humano, Contratación y Onboarding")
    caps = sproc.normalizar_pasos([
        {"id": "c-ia", "tipo": "induccion", "etapa": "Entrevista IA", "modalidad_capacitacion": "presencial"},
        {"id": "c-eh", "tipo": "induccion", "etapa": "Entrevista Humana", "modalidad_capacitacion": "plataforma"},
        {"id": "c-co", "tipo": "induccion", "etapa": "Contratación", "modalidad_capacitacion": "presencial"},
        {"id": "c-on", "tipo": "induccion", "etapa": "Onboarding"}], previos=[])
    mods = {x["id"]: x["modalidad_capacitacion"] for x in caps}
    check(mods == {"c-ia": "plataforma", "c-eh": "presencial", "c-co": "presencial", "c-on": "plataforma"},
          "Filtro RH = solo «En plataforma»; Filtro humano = solo «Presencial»; Contratación/Onboarding = la que elija RH")
    ed = leer("components/dashboard/procesos/editor-proceso.tsx")
    check("En plataforma" in ed and "Presencial" in ed and 'p.etapa === "Contratación" || p.etapa === "Onboarding"' in ed,
          "el editor muestra el selector solo en Contratación y Onboarding")

    print("\n--- Bloque 3 · capacitación presencial (citas de la entrevista humana) ---")
    admin.telefono = "3311112222"
    db.commit()
    vp = Vacante(codigo="VAC-C30CP", titulo="Operador de montacargas", estado="Publicada", cuenta_id=cuenta.id,
                 requisitos="Secundaria · Montacargas · Turnos")
    db.add(vp)
    db.flush()
    vp.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": [
        {"id": "sol", "tipo": "solicitud_sin_cv"}, {"id": "cap", "tipo": "induccion", "etapa": "Entrevista Humana", "nombre": "Capacitación"},
        {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"}]})
    db.commit()
    cpp = _crear_candidato(db, cuenta.id, "Carla Curso", "Portal", False, telefono="5530300006")
    pp = crear_postulacion(db, cpp, vp, cuenta.id, "web", consentimiento=True)
    pp.etapa = "Entrevista Humana"
    db.commit()

    def paso_cap(pp):
        db.refresh(pp)
        return next(x for x in sproc.estado_pasos(pp) if x["id"] == "cap")

    x = paso_cap(pp)
    check(x["modalidadCapacitacion"] == "presencial" and x["estadoUnificado"] == "pendiente_agendar"
          and x["accion"]["clave"] == "programar_capacitacion", "sin cita → «Pendiente de agendar» y «Programar capacitación»")
    desde = len(ENVIADOS)
    r = client.post(f"/evaluaciones/postulaciones/{pp.codigo}", json={
        "tipo": "otra", "forma": "asignada", "evaluador": {"tipo": "interno", "usuario_id": admin.id}, "paso_id": "cap",
        "cita": {"fecha": "2030-02-11", "hora": "09:00", "modalidad": "Presencial", "direccion": "CEDIS Norte, Zapopan"}})
    textos = [c.get("text", {}).get("body", "") for c in ENVIADOS[desde:] if c.get("type") == "text"]
    check(r.status_code == 201 and any(t.startswith("Hola Carla, tienes una capacitación programada para Operador de montacargas") for t in textos),
          f"candidato: «Tienes una capacitación programada para [puesto]» ({r.status_code})")
    hsm = [c for c in ENVIADOS[desde:] if c.get("type") == "template" and c["template"]["name"] == "aviso_instructor"]
    ps = [x["text"] for x in hsm[0]["template"]["components"][0]["parameters"]] if hsm else []
    check(len(ps) == 6 and ps[2] == "Capacitación: Operador de montacargas",
          f"instructor: plantilla «aviso_instructor» (Cambio 46) con «Capacitación: [puesto]» → {ps}")
    x = paso_cap(pp)
    check(x["estado"] == "en_curso" and x["evaluacion"], "programada: la actividad queda en curso")
    ev_cod = r.json()["evaluacion"]["codigo"]
    r = client.post(f"/evaluaciones/{ev_cod}/resultado", data={"conclusion": "desfavorable", "comentarios": "No completó la práctica", "version": "0"})
    x = paso_cap(pp)
    check(r.status_code == 200 and x["estadoUnificadoTexto"] == "No aprobó el curso" and pp.activa,
          "dictamen desfavorable → «No aprobó el curso» (no descarta)")
    modal = leer("components/dashboard/procesos/capacitacion-presencial.tsx")
    citas_tsx = leer("components/dashboard/evaluaciones/campos-evaluacion.tsx")
    check('rotulo="Instructor"' in modal and 'rol="instructor"' in modal and "Indicaciones para el ${rol}" in citas_tsx,
          "el campo «Entrevistador» se llama «Instructor» en la capacitación")

    print("\n--- Bloque 3 · capacitación en plataforma ---")
    from app.models import AsignacionCurso, Curso

    curso = Curso(codigo="CUR-C30", titulo="Seguridad", estado="Publicado", cuenta_id=cuenta.id, calificacion_minima=70)
    db.add(curso)
    vq = Vacante(codigo="VAC-C30PL", titulo="Auxiliar", estado="Publicada", cuenta_id=cuenta.id, requisitos="A · B · C")
    db.add(vq)
    db.flush()
    vq.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": [
        {"id": "sol", "tipo": "solicitud_sin_cv"}, {"id": "cap", "tipo": "induccion", "etapa": "Entrevista IA", "curso": "CUR-C30"}]})
    db.commit()
    cq = _crear_candidato(db, cuenta.id, "Pedro Plataforma", "Portal", False, telefono="5530300007")
    pq = crear_postulacion(db, cq, vq, cuenta.id, "web", consentimiento=True)
    pq.etapa = "Entrevista IA"
    db.commit()
    desde = len(ENVIADOS)
    asyncio.run(sproc.avanzar_seguro(db, pq))
    a = db.query(AsignacionCurso).filter(AsignacionCurso.postulacion_id == pq.id).first()
    check(a is not None and any("capacitacion" in str(c) for c in ENVIADOS[desde:]), "asigna el curso y manda la liga por WhatsApp")
    a.estado, a.aprobado = "completado", False
    db.commit()
    check(paso_cap(pq)["estadoUnificadoTexto"] == "No aprobó el curso", "reprueba → «No aprobó el curso»")
    a.aprobado = True
    db.commit()
    check(paso_cap(pq)["cumpleRegla"], "aprueba → se completa sola")

    print("\n--- Bloque 3 · «Otra actividad» bloquea la ruta hasta «Marcar como realizada» ---")
    vr = Vacante(codigo="VAC-C30OT", titulo="Chofer", estado="Publicada", cuenta_id=cuenta.id, requisitos="A · B · C")
    db.add(vr)
    db.flush()
    vr.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": [
        {"id": "sol", "tipo": "solicitud_sin_cv"}, {"id": "otra", "tipo": "otra", "etapa": "Prefiltro", "nombre": "Revisar licencia",
                                                    "mensaje": "Trae tu licencia original."},
        {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"}]})
    db.commit()
    cr = _crear_candidato(db, cuenta.id, "Raúl Ruta", "Portal", False, telefono="5530300008")
    pr = crear_postulacion(db, cr, vr, cuenta.id, "web", consentimiento=True)
    db.commit()
    asyncio.run(sproc.avanzar_seguro(db, pr))
    db.refresh(pr)
    check(pr.etapa == "Prefiltro" and ((pr.proceso_estado or {}).get("otra") or {}).get("mensaje_enviado"),
          "el mensaje sale y la ruta se queda en Prefiltro mientras RH no la marque")
    r = client.post(f"/procesos/postulaciones/{pr.codigo}/pasos/otra/realizada", json={})
    db.refresh(pr)
    check(r.status_code == 200 and pr.etapa == "Entrevista Humana", "«Marcar como realizada» → la ruta sigue sola")

    # ================= Bloque 4 · motor de ordenamiento del editor =================
    print("\n--- Bloque 4 · auto-acomodo y flechas por etapa ---")
    import shutil
    import subprocess

    orden = [t["valor"] for t in client.get("/procesos/opciones").json()["tiposPaso"]]
    nodo = shutil.which("node")
    programa = r"""
const ts = require(process.argv[1] + '/node_modules/typescript');
const fs = require('fs');
const src = fs.readFileSync(process.argv[1] + '/lib/orden-proceso.ts', 'utf8');
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const m = { exports: {} }; new Function('module', 'exports', js)(m, m.exports); const o = m.exports;
const orden = JSON.parse(process.argv[2]);
const P = (id, tipo, etapa, extra) => Object.assign({ id, tipo, etapa }, extra || {});
let pasos = [P('pw', 'prefiltro_whatsapp', 'Prefiltro'), P('cc', 'carta_contrato', 'Contratación'), P('eh', 'entrevista_humana', 'Entrevista Humana')];
const r = {};
r.solicitud = o.insertarSugerido(pasos, P('sol', 'solicitud_web', 'Prefiltro'), orden).filter(x => x.etapa === 'Prefiltro').map(x => x.id);
r.docs = o.insertarSugerido(pasos, P('doc', 'documentos', 'Prefiltro'), orden).filter(x => x.etapa === 'Prefiltro').map(x => x.id);
r.oferta = o.insertarSugerido(pasos, P('of', 'oferta', 'Contratación'), orden).filter(x => x.etapa === 'Contratación').map(x => x.id);
r.otraContratacion = o.insertarSugerido(pasos, P('ot', 'otra', 'Contratación'), orden).filter(x => x.etapa === 'Contratación').map(x => x.id);
r.medica = o.insertarSugerido(pasos, P('med', 'medica', 'Entrevista Humana'), orden).filter(x => x.etapa === 'Entrevista Humana').map(x => x.id);
const desordenado = [P('pw', 'prefiltro_whatsapp', 'Prefiltro'), P('sol', 'solicitud_web', 'Prefiltro'), P('cc', 'carta_contrato', 'Contratación'), P('of', 'oferta', 'Contratación')];
r.fijo = o.aplicarOrdenFijo(desordenado, orden).map(x => x.id);
r.flechas = [o.conFlechas(P('pw', 'prefiltro_whatsapp', 'Prefiltro')), o.conFlechas(P('cc', 'carta_contrato', 'Contratación')),
             o.conFlechas(P('of', 'oferta', 'Contratación')), o.conFlechas(P('eh', 'entrevista_humana', 'Entrevista Humana'))];
console.log(JSON.stringify(r));
"""
    salida = subprocess.run([nodo, "-e", programa, str(APP_DIR), json.dumps(orden)], capture_output=True, text=True, encoding="utf-8")
    check(salida.returncode == 0, f"el motor de orden corre en Node ({salida.stderr[:200]})")
    r = json.loads(salida.stdout.strip().splitlines()[-1])
    check(r["solicitud"] == ["sol", "pw"] and r["docs"] == ["pw", "doc"], "al agregar, cada actividad cae en su posición sugerida (Prefiltro)")
    check(r["oferta"] == ["of", "cc"] and r["otraContratacion"] == ["ot", "cc"], "en Contratación todo queda ANTES de «Contrato y firma»")
    check(r["medica"] == ["eh", "med"], "en las demás etapas también se acomoda sola en su lugar sugerido")
    check(r["fijo"] == ["sol", "pw", "of", "cc"], "Prefiltro fijo en orden de catálogo y «Contrato y firma» siempre al final")
    check(r["flechas"] == [False, False, True, True], "sin flechas en Prefiltro ni en «Contrato y firma»; flechas libres en lo demás")
    ed = leer("components/dashboard/procesos/editor-proceso.tsx")
    check("{conFlechas(p) && (" in ed and "insertarSugerido(pasos, p, orden)" in ed and "aplicarOrdenFijo(lista, orden)" in ed,
          "el editor usa el motor: inserta en la posición sugerida, oculta las flechas y respeta lo fijo")

    app.dependency_overrides.clear()
    db.close()

print(f"\n🎉 Cambio 30: {OK} comprobaciones OK")
