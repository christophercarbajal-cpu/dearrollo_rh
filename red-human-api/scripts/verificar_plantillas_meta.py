"""Plantillas oficiales de Meta que abren flujos de WhatsApp (2026-10-13, Cambios 28-29). Base desechable; Meta SIMULADA.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_plantillas_meta.py

1. Al habilitarse «Prefiltro por WhatsApp» sale UNA vez `inicio_entrevista_rh` con {{1}} = nombre del candidato.
2. La respuesta al botón («Sí, empezar ahora») o cualquier confirmación abre la ventana de 24 h y el bot contesta YA con el
   saludo estricto y la primera pregunta (base fija en Masivos / prefiltro configurable en Corporativos), sin «/start».
3. Aviso de cita al evaluador (Cambio 35): `aviso_evaluador_rh` con 6 variables en orden; nunca texto libre; el error de
   Meta llega legible.
   ({{6}} = «Pregunta por»: el responsable o contacto en el lugar de la cita).
4. Confirmación de horario del candidato: `aviso_proceso_rh`, SIN variables (así está aprobada en Meta).
"""

import asyncio
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
_dir = _temporal.directorio("rh_plantillas_meta_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "pm.db").replace(chr(92), "/")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-pm"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Archivo, Cuenta, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers import candidatos as rcand  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.routers.webhooks import procesar_entrante  # noqa: E402
from app.services import ia, notificaciones, plantillas_meta  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
ENVIADOS = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


# Cambio 46: el aviso al evaluador intenta primero texto libre; estos números NO han escrito en 24 h (Meta lo rechaza) y
# el aviso sigue con la plantilla
FUERA_DE_VENTANA = {"523399998888", "523399997777"}


async def _meta_falso(cuerpo):
    ENVIADOS.append(cuerpo)
    if cuerpo.get("type") == "text" and cuerpo.get("to") in FUERA_DE_VENTANA:
        return {"enviado": False, "proveedor": "meta", "detalle": "131047: Re-engagement message", "codigo": 131047}
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "mensaje_id": f"wamid.{len(ENVIADOS)}"}


whatsapp._meta_post = _meta_falso


def plantillas(desde=0, nombre=""):
    return [c for c in ENVIADOS[desde:] if c.get("type") == "template" and (not nombre or c["template"]["name"] == nombre)]


def boton(tel, texto):
    """Respuesta del candidato al botón de la plantilla, tal como la manda Meta."""
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": "Candidato"}, "wa_id": f"52{tel}"}],
        "messages": [{"from": f"52{tel}", "id": f"wamid.in.{tel}.{len(ENVIADOS)}", "type": "button",
                      "button": {"text": texto, "payload": texto}}]}}]}]}


def texto(tel, t):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": "Candidato"}, "wa_id": f"52{tel}"}],
        "messages": [{"from": f"52{tel}", "id": f"wamid.in.{tel}.{len(ENVIADOS)}.t", "type": "text", "text": {"body": t}}]}}]}]}


INDISP = ["Secundaria terminada", "1 año de experiencia en almacén", "Disponibilidad para rolar turnos"]

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="PM interna", nombre_comercial="Almacenes PM", estado="Activa", slug="pm-meta")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ================= 1. Disparo del prefiltro =================
    print("\n--- 1. «Prefiltro por WhatsApp» habilitado → inicio_entrevista_rh ({{1}} = nombre) ---")
    vm = Vacante(codigo="VAC-PM1", titulo="Almacenista", estado="Publicada", cuenta_id=cuenta.id, slug="almacenista-pm",
                 requisitos=" · ".join(INDISP), descripcion="Surtir pedidos.", sueldo="Desde $9,500 MXN mensuales",
                 ubicacion="Zapopan, Jalisco", enfoque_entrevista="operativo")
    db.add(vm)
    db.flush()
    vm.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": sproc.ruta_base("masivos_sin_documentos")["pasos"]})
    db.commit()
    desde = len(ENVIADOS)
    app.dependency_overrides.pop(cuenta_actual, None)
    r = client.post("/candidatos/postular", data={"vacante": vm.slug, "nombre": "Luisa Pérez Gómez", "telefono": "5512770001",
                                                  "consentimiento": "true"})
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    check(r.status_code == 201, f"postulación web ({r.status_code})")
    p1 = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    t = plantillas(desde, "inicio_entrevista_rh")
    # Cambio 37: «Hola, {{1}}. Soy Red Human, el agente virtual de {{2}}. Recibimos tu postulación para {{3}}…»
    check(len(t) == 1 and [x["text"] for x in t[0]["template"]["components"][0]["parameters"]] == ["Luisa", "Almacenes PM", "Almacenista"]
          and t[0]["to"] == "525512770001", "sale `inicio_entrevista_rh` al número del candidato con nombre, empresa y puesto")
    db.refresh(p1)
    check(plantillas_meta.estado(p1).get("enviada_en") and not pconv.estado(p1).get("pendiente"),
          "queda reclamada; el bot todavía no pregunta nada (espera la respuesta a la plantilla)")
    asyncio.run(sproc.avanzar_seguro(db, p1))
    check(len(plantillas(desde, "inicio_entrevista_rh")) == 1, "no se repite")

    print("\n--- 1b. Desacoplado: si Meta truena, la postulación se guarda igual (sin 500 ni rollback) ---")
    original = whatsapp._meta_post

    async def _meta_truena(cuerpo):
        raise RuntimeError("Graph API respondió algo inesperado")

    whatsapp._meta_post = _meta_truena
    app.dependency_overrides.pop(cuenta_actual, None)
    r = client.post("/candidatos/postular", data={"vacante": vm.slug, "nombre": "Pablo Falla", "telefono": "+52 1 55 1277 0099",
                                                  "consentimiento": "true"})
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    whatsapp._meta_post = original
    pf = db.query(Postulacion).filter_by(codigo=(r.json() or {}).get("postulacion", "")).first() if r.status_code == 201 else None
    check(r.status_code == 201 and pf is not None and pf.activa and pf.etapa == "Prefiltro" and pf.telefono == "5512770099",
          f"Meta lanza una excepción → 201 y el candidato queda en Prefiltro ({r.status_code})")
    db.refresh(pf)
    check("RuntimeError" in (plantillas_meta.estado(pf).get("error") or ""), "el error de Meta queda registrado en la postulación (y en el log)")
    check(ENVIADOS and ENVIADOS[0]["to"] == "525512770001" and ENVIADOS[0]["recipient_type"] == "individual",
          "payload de la plantilla: messaging_product, recipient_type, to = 52 + 10 dígitos, template {name, language, components}")

    # ================= 2. Respuesta al botón =================
    print("\n--- 2. «Sí, empezar ahora» → ventana de 24 h + saludo estricto y primera pregunta ---")
    msg = whatsapp.parsear_webhook(boton("5512770001", "Sí, empezar ahora"))
    check(msg and msg["texto"] == "Sí, empezar ahora", "el webhook entiende la respuesta del botón de la plantilla")
    r = asyncio.run(procesar_entrante(db, msg))
    db.refresh(p1)
    resp = r.get("respuesta") or ""
    # Cambio 31: tras la plantilla el bot NO se vuelve a presentar — lanza directo la primera pregunta
    check(resp == "¿Cuál es tu último grado de estudios?",
          f"Masivos (Cambio 29): saludo estricto + primera pregunta de la base fija, sin «/start» → «{resp[:110]}…»")
    e = plantillas_meta.estado(p1)
    check(e.get("respondida_en") and e.get("ventana_hasta") and e.get("confirmo"), "la respuesta abre (y registra) la ventana de 24 h")
    check("Sí, empezar ahora" not in str((pconv.estado(p1).get("respuestas") or {})), "la confirmación NO se toma como respuesta a un criterio")
    r = asyncio.run(procesar_entrante(db, whatsapp.parsear_webhook(texto("5512770001", "Terminé la secundaria"))))
    check("llevas en almacén" in (r.get("respuesta") or ""), "el prefiltro sigue normal con la siguiente pregunta")

    print("\n--- 2b. Corporativos (Cambio 19): misma apertura con el prefiltro configurable ---")
    vc = Vacante(codigo="VAC-PM2", titulo="Analista de compras", estado="Publicada", cuenta_id=cuenta.id, slug="analista-pm",
                 requisitos="Licenciatura terminada · 2 años de experiencia en compras · Manejo de SAP", sueldo="Desde $20,000 MXN mensuales",
                 descripcion="Compras nacionales.", enfoque_entrevista="profesional")
    db.add(vc)
    db.flush()
    ficha = ia.FichaGuion(titulo=vc.titulo, requisitos_indispensables=vc.requisitos.split(" · "), sueldo=vc.sueldo)
    vc.preguntas_filtro_whatsapp = ia.asegurar_prefiltros([], [], ficha, hay_web=False, hay_whatsapp=True)[1]
    vc.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": sproc.ruta_base("corporativo")["pasos"]})
    db.commit()
    c2 = _crear_candidato(db, cuenta.id, "Jorge Ramírez", "Portal", False, telefono="5512770002")
    p2 = crear_postulacion(db, c2, vc, cuenta.id, "web", consentimiento=True)
    c2.cv_datos = {"nombre": "Jorge Ramírez"}
    c2.archivos.append(Archivo(tipo="cv", nombre="cv.pdf", ruta=str(Path(_dir) / "cv.pdf"), mime="application/pdf", tamano=10,
                               estado="recibido", subido_por="candidato"))
    db.commit()
    desde = len(ENVIADOS)
    asyncio.run(sproc.avanzar_seguro(db, p2))
    check(len(plantillas(desde, "inicio_entrevista_rh")) == 1, "Corporativos: al habilitarse el prefiltro sale la plantilla")
    db.expire_all()  # la sesión de la prueba se recarga como en una petición real del webhook
    r = asyncio.run(procesar_entrante(db, whatsapp.parsear_webhook(texto("5512770002", "Claro"))))
    resp = r.get("respuesta") or ""
    primera = pconv.criterios_de(vc, p2)[0]
    check(resp == primera["chat"] and not primera.get("base_operativa") and "Soy Red Human" not in resp,
          f"«Claro» también abre: saludo estricto + primera pregunta del prefiltro configurable → «{resp[:120]}…»")

    print("\n--- 2c. Quien ya escribe por el chat no recibe la plantilla ---")
    c3 = _crear_candidato(db, cuenta.id, "Ana Chat", "WhatsApp", False, telefono="5512770003")
    p3 = crear_postulacion(db, c3, vm, cuenta.id, "whatsapp", consentimiento=True)
    db.commit()
    desde = len(ENVIADOS)
    asyncio.run(sproc.avanzar_seguro(db, p3))
    check(not plantillas(desde, "inicio_entrevista_rh"), "origen WhatsApp: la conversación ya está abierta, no hay plantilla")

    # ================= 3. Aviso al entrevistador =================
    print("\n--- 3. aviso_evaluador_rh: 6 variables en orden (Cambio 35) ---")
    from app.fechas import ETIQUETA_ZONA

    d = {"entrevistador": "Mariana López", "candidato": "Luisa Pérez Gómez", "vacante": "Almacenista"}
    dev = {"fecha_texto": "jueves 15 de octubre", "horario_texto": "de 10:30 a.m. a 11:30 a.m.", "modalidad": "Presencial",
           "direccion": "Av. Patria 100, Zapopan", "liga_evaluador": "https://app/evaluacion/abc"}
    check(notificaciones.parametros_plantilla_evaluador(d, dev) == [
        "Mariana López", "Luisa Pérez Gómez", "Almacenista", f"jueves 15 de octubre, de 10:30 a.m. a 11:30 a.m. ({ETIQUETA_ZONA})",
        "Av. Patria 100, Zapopan", "https://app/evaluacion/abc"],
        "{{1}} evaluador · {{2}} candidato · {{3}} puesto · {{4}} fecha y hora · {{5}} lugar · {{6}} liga")
    check(notificaciones.parametros_plantilla_evaluador(d, {**dev, "capacitacion": True})[2] == "Capacitación: Almacenista",
          "capacitación presencial → {{3}} «Capacitación: [puesto]»")
    check(notificaciones.parametros_plantilla_evaluador(d, {**dev, "modalidad": "Videollamada", "liga_videollamada": "https://meet/x"})[4]
          == "Videollamada: https://meet/x", "lugar de una videollamada")
    check(all(str(x).strip() for x in notificaciones.parametros_plantilla_evaluador({}, {})), "nunca variables vacías (Meta las rechaza)")
    check([notificaciones.plantilla_evaluador(e) for e in ("evaluacion_asignada", "evaluacion_reprogramada", "evaluacion_cancelada", "recordatorio_evaluacion")]
          == ["aviso_evaluador_rh", "cita_cambio_evaluador_rh", "cita_cancelada_evaluador_rh", "aviso_evaluador_rh"],
          "aviso nuevo / «Reenviar» / recordatorio → aviso_evaluador_rh · «Tu cita cambió» → cita_cambio_evaluador_rh · cancelación")
    check(notificaciones.plantilla_evaluador("evaluacion_asignada", {"capacitacion": True}) == "aviso_instructor"
          and notificaciones.plantilla_evaluador("evaluacion_asignada", {"tipo": "medica"}) == "aviso_medico",
          "Cambio 46: instructor → aviso_instructor · médico → aviso_medico")
    desde = len(ENVIADOS)
    env = asyncio.run(whatsapp.enviar_plantilla_evaluador("3399998888", "aviso_evaluador_rh",
                                                          notificaciones.parametros_plantilla_evaluador(d, dev), "texto"))
    t = plantillas(desde)
    check(env.get("enviado") and t and t[0]["template"]["name"] == "aviso_evaluador_rh" and t[0]["to"] == "523399998888"
          and len(t[0]["template"]["components"][0]["parameters"]) == 6
          and [c.get("type") for c in ENVIADOS[desde:]] == ["text", "template"],
          "fuera de la ventana de 24 h sale la plantilla de 6 variables al número del evaluador (Cambio 46: el texto libre se intentó antes)")
    check(whatsapp.error_legible("131047: Re-engagement message").startswith("Fuera de la ventana de 24 horas")
          and whatsapp.error_legible("131026 Message undeliverable").startswith("Número inválido")
          and whatsapp.error_legible("132001: Template name does not exist").startswith("La plantilla no existe"),
          "los errores de Meta se traducen («fuera de la ventana de 24 horas», «número inválido», plantilla no aprobada)")

    print("\n--- 3b. De punta a punta: cita de entrevista humana con indicaciones ---")
    admin.telefono = "+52 1 (33) 9999-7777"  # mismo formateo que el candidato: llega a la API como 52 + 10
    db.commit()
    p1 = db.query(Postulacion).filter_by(id=p1.id).one()
    p1.etapa = "Entrevista Humana"
    db.commit()
    desde = len(ENVIADOS)
    r = client.post(f"/evaluaciones/postulaciones/{p1.codigo}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "evaluador": {"tipo": "interno", "usuario_id": admin.id},
        "cita": {"fecha": "2030-01-15", "hora": "10:30", "modalidad": "Presencial", "direccion": "Av. Patria 100, Zapopan",
                 "indicaciones_entrevistador": "Validar disponibilidad de turnos"}})
    t = plantillas(desde, "aviso_evaluador_rh")
    params = [x["text"] for x in t[0]["template"]["components"][0]["parameters"]] if t else []
    check(r.status_code in (200, 201) and t and t[0]["to"] == "523399997777" and len(params) == 6,
          f"la cita nueva le manda la plantilla al WhatsApp del entrevistador ({r.status_code})")
    check(params and params[1] == "Luisa Pérez Gómez" and params[2] == "Almacenista" and "Av. Patria 100" in params[4]
          and "5512770001" not in " ".join(params) and "/evaluacion/" in params[5],
          f"variables: candidato, puesto, fecha y hora, lugar y SU liga (nunca el teléfono del candidato) → {params}")
    ev_codigo = r.json()["evaluacion"]["codigo"]
    desde = len(ENVIADOS)
    r = client.patch(f"/evaluaciones/{ev_codigo}", json={"cita": {"fecha": "2030-01-16", "hora": "11:00", "modalidad": "Presencial",
                                                                   "direccion": "Av. Patria 100, Zapopan"}})
    t = plantillas(desde, "cita_cambio_evaluador_rh")
    check(r.status_code == 200 and t and len(t[0]["template"]["components"][0]["parameters"]) == 6,
          f"«Tu cita cambió» al evaluador también es plantilla ({r.status_code})")

    print("\n--- 3c. Error de Meta visible junto al «Fallido» ---")

    async def _meta_rechaza(cuerpo):
        ENVIADOS.append(cuerpo)
        if cuerpo.get("type") == "template" and cuerpo["template"]["name"] == "aviso_evaluador_rh":
            return {"enviado": False, "proveedor": "meta", "detalle": "131026: Message undeliverable", "codigo": "131026"}
        return await _meta_falso(cuerpo)

    original = whatsapp._meta_post
    whatsapp._meta_post = _meta_rechaza
    r = client.post(f"/evaluaciones/{ev_codigo}/ligas/evaluador/enviar")
    whatsapp._meta_post = original
    s = client.get(f"/procesos/postulaciones/{p1.codigo}").json()
    eh_paso = next(x for e in s["etapas"] for x in e["pasos"] if x["tipo"] == "entrevista_humana")
    env_ev = (eh_paso.get("envios") or {}).get("entrevistador") or {}
    check(env_ev.get("estado") == "fallido" and env_ev.get("error", "").startswith("Número inválido"),
          f"«Reenviar» con número inválido → «Fallido» + el error de Meta legible ({env_ev.get('estadoTexto')}: {env_ev.get('error')})")

    # ================= 4. Aviso de agenda =================
    print("\n--- 4. Confirmación de horario → aviso_proceso_rh ---")
    va = Vacante(codigo="VAC-PM3", titulo="Supervisor", estado="Publicada", cuenta_id=cuenta.id, requisitos="Liderazgo de equipos")
    db.add(va)
    db.flush()
    va.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": [
        {"id": "av", "tipo": "entrevista_agente", "etapa": "Entrevista IA"}, {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"}]})
    db.commit()
    c4 = _crear_candidato(db, cuenta.id, "Raúl Agenda", "Portal", False, telefono="5512770004")
    p4 = crear_postulacion(db, c4, va, cuenta.id, "web", consentimiento=True)
    p4.etapa = "Entrevista IA"
    db.commit()
    desde = len(ENVIADOS)
    r = asyncio.run(rcand._avisar_apto_e_iniciar_agenda(db, p4))
    db.commit()
    t = plantillas(desde, "aviso_proceso_rh")
    check(t and t[0]["to"] == "525512770004" and "components" not in t[0]["template"] and plantillas_meta.parametros_agenda(p4) == [],
          "se requiere confirmar horario → sale `aviso_proceso_rh` con 0 parámetros (sin variables en Meta)")
    textos = [c for c in ENVIADOS[desde:] if c.get("type") == "text"]
    check(textos and plantillas_meta.estado(p4) == {} and r.get("texto"), "después va la pregunta exacta de la agenda como texto")

    app.dependency_overrides.clear()
    db.close()

print(f"\n🎉 Plantillas oficiales de Meta: {OK} comprobaciones OK")
