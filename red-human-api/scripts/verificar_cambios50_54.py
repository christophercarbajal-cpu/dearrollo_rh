"""Cambios 50-54 (2026-10-15) + configuraciones Fletes México (procesos A y B) y Grupak (Operativo básico). Base desechable;
IA, Meta y Bot API SIMULADOS (sin red).

    PYTHONIOENCODING=utf-8 python scripts/verificar_cambios50_54.py

1. Cambio 50: «Prefiltro web» fuera del catálogo; «Unas preguntas» de la solicitud = preguntas filtro + interés (con
   ciudad y estado) + sueldo; 2026-10-16: un «No» que coincide con «Descarta si» ya NO descarta al enviar — se registra,
   sale la plantilla de inicio y el chat lo confirma (confirmado → «Descartado» con su motivo); migración de plantillas, vacantes y candidatos detenidos en «Prefiltro web».
2. Cambio 51: «No sé qué sea eso» → se refrasea (nunca se repite ni se pide «sí o no»); «¿Se puede negociar?» → «Eso lo
   puedes platicar con el equipo de RH de [empresa].» + la misma pregunta; otro estado → «¿Vives ahí o te cambiarías de
   ciudad?»; reglas en el prompt del entrevistador.
3. Cambio 52: entrevista fija — «Nunca» → bloque C directo («Sin experiencia laboral»); el cierre no repite puesto, sueldo ni
   ubicación ya dichos («¿Cuándo podrías empezar?»); lo respondido antes entra al prompt (regla de oro).
4. Cambio 53: CV vs respuesta solo en datos demostrables («No aplica» en disponibilidad); «Avanzar / Avanzar con reservas /
   No avanzar»; historial con «Sin empleo actual» y celdas vacías si la respuesta no es un dato.
5. Cambio 54: «Gracias» → «Con gusto, [nombre].» y silencio; aviso al cambiar de vacante; cierre una sola vez; documentos
   solo con la liga.
6. Fletes México: terminales con dirección real, procesos A y B, «Unas preguntas», prefiltro por WhatsApp (A), entrevista
   en video ofrecida por WhatsApp (B), contradicción con la web → «No avanzar», confirmación agrupada de la autoagenda.
7. Grupak: «Operativo básico» sin prefiltros; la entrevista por WhatsApp arranca sola (plantilla de inicio) y cierra EXACTO.
"""

import asyncio
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_c50_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c50.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-c50"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    CATALOGO_ACTIVIDADES, ACTIVIDADES_POR_ETAPA, Bitacora, Cuenta, Entrevista, PlantillaProceso, Postulacion, Usuario, Vacante,
)
from app.routers import candidatos as rcand  # noqa: E402
from app.routers.webhooks import procesar_entrante, texto_cambio_vacante  # noqa: E402
from app.services import canal_proceso  # noqa: E402
from app.services import entrevista_operativa as eop  # noqa: E402
from app.services import entrevista_whatsapp_fija as ewf  # noqa: E402
from app.services import evaluacion_integral as evi  # noqa: E402
from app.services import ia  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import solicitud_web as ssw  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

import configurar_grupak as cg  # noqa: E402
import seed_fletes_mexico as sf  # noqa: E402

OK = 0
ENVIADOS = []
VENTANA = set()


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _meta_falso(cuerpo):
    ENVIADOS.append(cuerpo)
    if cuerpo.get("type") == "text" and cuerpo.get("to") not in VENTANA:
        return {"enviado": False, "proveedor": "meta", "detalle": "131047: Re-engagement message", "codigo": 131047}
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "mensaje_id": f"wamid.{len(ENVIADOS)}"}


whatsapp._meta_post = _meta_falso


def textos(tel, desde=0):
    return [c["text"]["body"] for c in ENVIADOS[desde:] if c.get("type") == "text" and c["to"] == "52" + tel]


def plantillas(tel, desde=0):
    return [c["template"]["name"] for c in ENVIADOS[desde:] if c.get("type") == "template" and c["to"] == "52" + tel]


def _msg(tel, cuerpo):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": "Candidato"}, "wa_id": f"52{tel}"}],
        "messages": [{"from": f"52{tel}", "id": f"wamid.in.{tel}.{len(ENVIADOS)}.{id(cuerpo)}", **cuerpo}]}}]}]}


def escribe(tel, t):
    VENTANA.add("52" + tel)
    antes = len(ENVIADOS)
    asyncio.run(procesar_entrante(db, whatsapp.parsear_webhook(_msg(tel, {"type": "text", "text": {"body": t}}))))
    db.expire_all()
    return textos(tel, antes)


def ultimo(tel, t):
    salida = escribe(tel, t)
    return salida[-1] if salida else ""


with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    db.commit()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    admin.telefono = "5590000001"
    db.commit()

    # ================= 1. Cambio 50 — catálogo =================
    print("\n--- 1. Cambio 50: catálogo y «Unas preguntas» ---")
    check("prefiltro_web" not in CATALOGO_ACTIVIDADES and "prefiltro_web" not in ACTIVIDADES_POR_ETAPA["Prefiltro"],
          "«Prefiltro web» ya no está en el catálogo")
    from app.routers.procesos import opciones as _opciones

    try:
        ops = _opciones(_=admin)
    except TypeError:
        ops = _opciones()
    valores = [t.get("valor") for t in ops.get("tiposPaso") or []]
    check("prefiltro_web" not in valores and "solicitud_sin_cv" in valores, "el editor de procesos ya no ofrece «Prefiltro web»")

    # ================= 6. Fletes México (configuración) =================
    print("\n--- 6. Fletes México: configuración ---")
    cu = sf.asegurar_cuenta(db, "whatsapp")
    u = sf.vincular_admins(db, cu)
    cl = sf.asegurar_cliente(db, cu)
    terminales = sf.asegurar_ubicaciones(db, cu)
    # estado de PRODUCCIÓN: el proceso y la Vacante A ya se sembraron con la versión del Cambio 47 (sin experiencia como
    # indispensable y con los nombres anteriores de las actividades)
    ev_viejo = {"evaluador": {"tipo": "interno", "usuario_id": u.id}}
    viejos = [
        {"id": "solicitud", "tipo": "solicitud_sin_cv", "etapa": "Prefiltro"},
        {"id": "prefiltro-whatsapp", "tipo": "prefiltro_whatsapp", "nombre": "Prefiltro por WhatsApp", "etapa": "Prefiltro"},
        {"id": "documentos", "tipo": "documentos", "nombre": "Documentos", "etapa": "Prefiltro"},
        {"id": "prueba-manejo", "tipo": "tecnica", "nombre": "Prueba de manejo", "etapa": "Entrevista Humana", "tipo_agenda": "candidato",
         "misma_cita": True, "config": ev_viejo},
        {"id": "entrevista-jefe", "tipo": "entrevista_humana", "nombre": "Entrevista con jefe de operadores", "etapa": "Entrevista Humana",
         "tipo_entrevista": "jefe_directo", "tipo_agenda": "candidato", "misma_cita": True, "config": ev_viejo},
        {"id": "entrevista-sindicato", "tipo": "entrevista_humana", "nombre": "Entrevista con sindicato", "etapa": "Entrevista Humana",
         "tipo_entrevista": "general", "tipo_agenda": "candidato", "misma_cita": True, "config": ev_viejo},
        {"id": "medico", "tipo": "medica", "nombre": "Examen médico interno y antidoping", "etapa": "Entrevista Humana", "tipo_agenda": "rh"},
        {"id": "contrato", "tipo": "carta_contrato", "nombre": "Contrato y firma", "etapa": "Contratación"},
    ]
    pl_viejo = sf.asegurar_proceso(db, cu, u, sf.PROCESO_A, viejos, "A (Cambio 47)")
    from app.routers import vacantes as _rvac

    asyncio.run(_rvac.crear(_rvac.CrearIn(
        titulo=sf.VACANTES[0]["titulo"], area="Operaciones", seniority="Semi-senior", ubicacion_estado="Ciudad de México",
        ubicacion_municipio="Azcapotzalco", modalidad="Presencial", sueldo_desde=25000, sueldo_hasta=32000, sueldo_moneda="MXN",
        sueldo_periodicidad="mensual", descripcion="Operador de 5ª rueda.", requisitos_indispensables=[sf.LICENCIA, sf.APTO],
        cliente_id=cl.id, mostrar_cliente_candidato=True, proceso={"plantilla_id": pl_viejo.id}, generar_si_falta=False, publicar=False,
        enfoque_entrevista="operativo", ubicaciones_ids=[t.id for t in terminales], posiciones=20,
        preguntas_requisitos=[{"requisito": sf.LICENCIA, "pregunta": sf.PREGUNTAS_CHAT[sf.LICENCIA]},
                              {"requisito": sf.APTO, "pregunta": sf.PREGUNTAS_CHAT[sf.APTO]}]), db, u, cu))
    db.commit()
    v_vieja = db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == sf.VACANTES[0]["titulo"]).one()
    check(sf.validar(db, v_vieja, "A"), "la Vacante A sembrada con la versión anterior NO pasa la validación (estado de producción)")
    pa = sf.asegurar_proceso(db, cu, u, sf.PROCESO_A, sf.pasos_proceso_a(u.id), "A")
    pb = sf.asegurar_proceso(db, cu, u, sf.PROCESO_B, sf.pasos_proceso_b(u.id), "B")
    va = sf.asegurar_vacante(db, cu, cl, u, pa, terminales, sf.VACANTES[0], publicar=True)
    vb = sf.asegurar_vacante(db, cu, cl, u, pb, terminales, sf.VACANTES[1], publicar=True)
    db.commit()
    prob_a, prob_b = sf.validar(db, va, "A"), sf.validar(db, vb, "B")
    check(not prob_a, f"Vacante A pasa la validación del seed {prob_a}")
    check(not prob_b, f"Vacante B pasa la validación del seed {prob_b}")
    mty = next(t for t in terminales if t.nombre == "Terminal Monterrey")
    cdmx = next(t for t in terminales if t.nombre == "Terminal CDMX")
    check("Supermanzana Km 2.1" in mty.direccion and "Tultepec" in cdmx.direccion and cdmx.ubicacion_estado == "Estado de México",
          "terminales con dirección real (Monterrey, CDMX en Tultepec)")
    check(next(t for t in terminales if t.nombre == "Terminal Guadalajara").direccion == "Conocido, Guadalajara, Jalisco",
          "Guadalajara sin dirección exacta: «Conocido, Guadalajara, Jalisco» (nunca «(dirección de demo)»)")
    sf.asegurar_ubicaciones(db, cu)
    sf.asegurar_proceso(db, cu, u, sf.PROCESO_A, sf.pasos_proceso_a(u.id), "A")
    sf.asegurar_vacante(db, cu, cl, u, pa, terminales, sf.VACANTES[0], publicar=True)
    db.commit()
    # Cambio 58: A y B comparten el título; el seed reconoce cada una por su proceso (no duplica)
    check(db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == sf.VACANTES[0]["titulo"]).count() == 2
          and db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cu.id, PlantillaProceso.nombre == sf.PROCESO_A).count() == 1,
          "el seed de Fletes es idempotente")

    # ================= 1b. «Unas preguntas» y descarte al enviar =================
    from app.serial import vacante_dict

    crit = vacante_dict(va)["criterios"]
    preguntas = [c["pregunta"] for c in crit]
    # 2026-10-16: sin la confirmación «El puesto es… ¿Te interesa?»; el sueldo va UNA vez como expectativa
    check(not any(q.startswith("El puesto es") for q in preguntas) and "¿Cuántos años tienes?" not in preguntas,
          f"«Unas preguntas» sin confirmar el puesto ni pedir la edad → {preguntas}")
    # 2026-10-16 (bug del tester): Fletes publica su sueldo → «Unas preguntas» ya no pide la expectativa
    check("¿Cuál es tu expectativa de sueldo mensual?" not in preguntas, "«Unas preguntas» sin expectativa (sueldo publicado)")
    q_sue = "¿Cuál es tu expectativa de sueldo mensual?"

    def postular(v, nombre, tel, respuestas):
        datos = {"vacante": v.slug, "nombre": nombre, "telefono": tel, "consentimiento": "true",
                 "respuestas": __import__("json").dumps([{"pregunta": q, "respuesta": r} for q, r in respuestas.items()])}
        r = client.post("/candidatos/postular", data=datos)
        assert r.status_code in (200, 201), r.text
        db.expire_all()
        return r.json()

    todo_si = {**{q: "Sí" for q in sf.PREGUNTAS_WEB.values()}, "¿En qué terminal te gustaría trabajar?": "Terminal Monterrey",
               q_sue: "20000"}
    antes = len(ENVIADOS)
    r = postular(va, "Raúl Rechazo", "5560000001", {**todo_si, sf.PREGUNTAS_WEB[sf.LICENCIA]: "No"})
    p_rech = db.query(Postulacion).filter(Postulacion.codigo == r["postulacion"]).one()
    # 2026-10-16 (decisión del usuario): la solicitud web NUNCA descarta — se registra y el WhatsApp la confirma
    check(not r.get("descartado") and p_rech.activa
          and any(h.get("evento") == "respuesta_excluyente_web" and "Licencia" in (h.get("motivo") or "") for h in p_rech.historial or []),
          "un «No» a una pregunta con «Descarta si» NO descarta al enviar: queda registrado con su motivo")
    check("inicio_entrevista_rh" in plantillas("5560000001", antes), "le sale la plantilla de inicio")
    escribe("5560000001", "Sí, empezar ahora")
    escribe("5560000001", "Sí, es correcto")
    db.expire_all()
    p_rech = db.query(Postulacion).filter(Postulacion.codigo == r["postulacion"]).one()
    check(not p_rech.activa and p_rech.motivo_cierre == "descartado"
          and any(h.get("evento") == "descartado" and "Licencia" in (h.get("motivo") or "") for h in p_rech.historial or []),
          "confirmado en el chat → «Descartado» con el motivo en el historial")

    # ================= 6b. Fletes A: prefiltro por WhatsApp =================
    print("\n--- 6b. Fletes A: prefiltro por WhatsApp ---")
    antes = len(ENVIADOS)
    r = postular(va, "Mario Operador", "5560000002", todo_si)
    p_a = db.query(Postulacion).filter(Postulacion.codigo == r["postulacion"]).one()
    check(not r.get("descartado") and p_a.activa, "todo «Sí» → la solicitud pasa")
    check((p_a.analisis.get("prefiltro_datos") or {}).get("sueldo_expectativa") is None, "sin expectativa registrada (no se pidió)")
    check("inicio_entrevista_rh" in plantillas("5560000002", antes), "sale la plantilla de inicio del prefiltro")
    t = ultimo("5560000002", "Sí, empezar ahora")
    check(t == sf.PREGUNTAS_CHAT[sf.EXPERIENCIA], f"primera pregunta del chat: años de experiencia → «{t}»")
    t = ultimo("5560000002", "No sé qué sea eso")
    check(t != sf.PREGUNTAS_CHAT[sf.EXPERIENCIA] and "sí o no" not in t.lower() and t.startswith("Te lo pregunto de otra forma"),
          f"«No sé qué sea eso» → se refrasea → «{t}»")
    t = ultimo("5560000002", "¿Se puede negociar el sueldo?")
    check(t == f"Eso lo puedes platicar con el equipo de RH de Fletes México. {sf.PREGUNTAS_CHAT[sf.EXPERIENCIA]}",
          f"negociación → «{t}»")
    seguidas = []
    for resp in ("6 años", "Transportes del Norte", "LFB-123456", "diciembre de 2030", "15/06/2031"):  # 2026-10-16: sin edad
        seguidas.append(ultimo("5560000002", resp))
    check(sf.PROPIAS[0]["pregunta"] in seguidas[0] and sf.PROPIAS[1]["pregunta"] in seguidas[1], f"preguntas propias de RH en orden → {seguidas[:2]}")
    check(not any("te interesa" in x.lower() for x in seguidas) and not any("sueldo" in x.lower() for x in seguidas),
          "interés y sueldo NO se repiten: ya se contestaron en «Unas preguntas» (regla de oro)")
    check("colonia" in seguidas[-1].lower() and not any("años tienes" in x for x in seguidas),
          f"sin preguntar la edad: la colonia → «{seguidas[-1]}»")
    t = ultimo("5560000002", "Col. Centro, Hermosillo, Sonora")
    check(t.endswith("El trabajo es en Salinas Victoria, Nuevo León. ¿Vives ahí o te cambiarías de ciudad?"),
          f"vive en otro estado → «{t}»")
    t = ultimo("5560000002", "Me cambiaría sin problema")
    check(t == f"La Terminal Monterrey está en {mty.direccion}. ¿Cuánto tiempo harías de tu casa?", f"tiempo a SU terminal → «{t}»")
    escribe("5560000002", "40 minutos")
    p_a = db.query(Postulacion).filter(Postulacion.id == p_a.id).one()
    d = p_a.analisis.get("prefiltro_datos") or {}
    check(d.get("otro_estado") == "Sonora" and d.get("reubicacion") and p_a.activa, "la reubicación queda en el resumen y no descarta")
    check((d.get("respuestas_propias") or {}).get("Número de licencia") == "LFB-123456", "las respuestas propias quedan en la ficha")

    r = postular(va, "Vigencia Corta", "5560000003", todo_si)
    escribe("5560000003", "Sí, empezar ahora")
    for resp in ("3 años", "Fletes del Sur", "LFB-1"):
        escribe("5560000003", resp)
    t = ultimo("5560000003", "en un mes")
    p_v = db.query(Postulacion).filter(Postulacion.codigo == r["postulacion"]).one()
    check(not p_v.activa and t == pconv.texto_descarte("Fletes México"), f"licencia que vence en menos de 3 meses → descarte genérico → «{t}»")

    # ================= 6c. Fletes B: entrevista en video =================
    print("\n--- 6c. Fletes B: entrevista en video ---")
    antes = len(ENVIADOS)
    resp_b = {c["pregunta"]: ("Terminal Monterrey" if c["tipo"] == "opcion" else "Sí") for c in vacante_dict(vb)["criterios"]}
    r = postular(vb, "Beto Video", "5560000004", resp_b)
    p_b = db.query(Postulacion).filter(Postulacion.codigo == r["postulacion"]).one()
    check("entrevista_video_rh" in plantillas("5560000004", antes), "fuera de la ventana de 24 h → plantilla de la entrevista en video")
    inv = next((m.texto for m in p_b.mensajes if m.rol == "assistant"), "")
    check("entrevista en video de 5 minutos" in inv and inv.endswith("¿La quieres hacer ahora?")
          and "Soy Red Human, el agente virtual de Fletes México." in inv, f"invitación → «{inv}»")
    t = ultimo("5560000004", "Sí")
    p_b = db.query(Postulacion).filter(Postulacion.id == p_b.id).one()
    check("/entrevista/" in t and p_b.videollamada_liga, f"«sí» → la liga de la entrevista → «{t[:80]}…»")
    previas = [{"fuente": "el formulario web", "pregunta": sf.PREGUNTAS_WEB[sf.LICENCIA], "respuesta": "Sí"}]
    inc = ia.inconsistencias_demo([{"rol": "user", "texto": "La verdad mi licencia federal ya se me venció"}], previas)
    check(inc and ssw.contradice_indispensable(p_b, inc) == sf.LICENCIA,
          f"la entrevista contradice la web en la licencia → inconsistencia en un indispensable ({inc})")
    check(ssw.contradice_indispensable(p_b, ["En la entrevista dijo que le gusta el futbol"]) is None, "una inconsistencia ajena no cuenta")
    check(sf.GUION_AVATAR["preguntas"][2] in ((vb.guiones or {}).get("secciones", {}).get("entrevista_avatar") or {}).get("preguntas", []),
          "el guion del avatar trae las preguntas de RH")

    # ================= 7. Grupak =================
    print("\n--- 7. Grupak: Operativo básico ---")
    vg = cg.configurar(db)
    db.commit()
    check(not cg.validar(db, vg), f"Grupak pasa la validación del script {cg.validar(db, vg)}")
    vg.estado = "Publicada"
    db.commit()
    crit_g = vacante_dict(vg)["criterios"]
    respuestas_g = {}
    for c in crit_g:
        respuestas_g[c["pregunta"]] = {"numero": "25", "abierta": "Col. Mitras, Monterrey"}.get(c["tipo"], "Sí")
    antes = len(ENVIADOS)
    r = postular(vg, "Ana Grupak", "5560000005", respuestas_g)
    p_g = db.query(Postulacion).filter(Postulacion.codigo == r["postulacion"]).one()
    check(not r.get("descartado") and p_g.activa and (p_g.analisis.get("prefiltro_datos") or {}).get("edad") == 25,
          "Grupak: edad y colonia en «Unas preguntas» (no descartan)")
    check("inicio_entrevista_rh" in plantillas("5560000005", antes), "sin ventana de 24 h, la entrevista abre con la plantilla de inicio")
    t = ultimo("5560000005", "Sí, empezar ahora")
    check(t == "Listo, Ana. Ahora te haré unas preguntas sobre tu experiencia. ¿Trabajas actualmente?", f"transición → «{t}»")
    t = ultimo("5560000005", "Nunca he trabajado")
    check(t == "¿Cuándo podrías empezar?", f"«Nunca» → bloque C directo y el cierre solo pregunta el inicio → «{t}»")
    t = ultimo("5560000005", "El lunes")
    check(t == cg.CIERRE.format(nombre="Ana"), f"cierre EXACTO de Grupak → «{t}»")
    e_g = db.query(Entrevista).filter(Entrevista.postulacion_id == p_g.id).first()
    hist = ewf.historial(e_g)
    check(hist and hist[0]["empresa"] == ewf.SIN_EXPERIENCIA, f"historial: «Sin experiencia laboral» → {hist}")
    t = ultimo("5560000005", "Gracias")
    check(t == "Con gusto, Ana.", f"«Gracias» tras el cierre → «{t}»")
    check(escribe("5560000005", "Muchas gracias") == [], "un segundo «Gracias» → silencio")
    p_g = db.query(Postulacion).filter(Postulacion.id == p_g.id).one()
    check(sum(1 for m in p_g.mensajes if m.rol == "assistant" and m.texto == cg.CIERRE.format(nombre="Ana")) == 1, "el cierre sale UNA sola vez")

    # ================= 3/4. Cambios 52-53 (unitarios) =================
    print("\n--- 3-4. Cambios 52-53 ---")
    e_g.guion = {**(e_g.guion or {}), "fija": {"respuestas": {"trabaja": "No", "sin_trabajo": "8 meses"}, "actual": False,
                                               "trabajos": [{"empresa": "¿En dónde?", "puesto": "Cargador", "actual": False}]}}
    est = ewf._estado(e_g)
    ewf.registrar_respuesta(est, "empresa", "¿En dónde?", p_g)
    check(est["trabajos"][0]["empresa"] == "", "«¿En dónde?» no es un dato → celda vacía")
    e_g.guion = {**(e_g.guion or {}), "fija": {**est, "respuestas": {"trabaja": "No", "sin_trabajo": "8 meses"}, "actual": False}}
    hist = ewf.historial(e_g)
    check(hist[0]["empresa"] == ewf.SIN_EMPLEO_ACTUAL and hist[0]["duracion"] == "8 meses", f"no trabaja → fila «Sin empleo actual» → {hist[0]}")
    check(evi.ESTADOS_INTEGRAL["apto"] == "Avanzar" and evi.ESTADOS_INTEGRAL["con_observaciones"] == "Avanzar con reservas"
          and evi.ESTADOS_INTEGRAL["no_apto"] == "No avanzar" and eop.RECOMENDACIONES["no_recomendable"][0] == "No avanzar",
          "recomendación final: Avanzar / Avanzar con reservas / No avanzar")
    check(not pconv.demostrable_en_cv({"criterio": "Disponibilidad para rolar turnos"})
          and not pconv.demostrable_en_cv({"criterio": "Disponibilidad para cargar material pesado"})
          and pconv.demostrable_en_cv({"criterio": "Licencia federal tipo B"}) and pconv.demostrable_en_cv({"criterio": "3 años de experiencia"})
          and pconv.demostrable_en_cv({"criterio": "Secundaria terminada", "dato": "escolaridad"}),
          "CV vs respuesta solo en experiencia, escolaridad y licencias")
    prompt = ia.prompt_entrevistador("Operador", "Licencia B", "Luis", ["¿Has trabajado como operador?"], empresa="Fletes México",
                                     ya_respondido=[{"fuente": "el formulario web", "pregunta": "¿Tienes licencia?", "respuesta": "Sí"}])
    check("REGLA DE ORO" in prompt and "¿Tienes licencia?" in prompt and "Eso lo puedes platicar con el equipo de RH de Fletes México."
          in prompt and "REFRASEA" in prompt, "el prompt del entrevistador trae la regla de oro, el refraseo y los límites")

    # ================= 5. Cambio 54 =================
    print("\n--- 5. Cambio 54 ---")
    check(rcand.es_agradecimiento("Ok gracias!") and rcand.es_agradecimiento("muchas gracias 🙏") and not rcand.es_agradecimiento("Gracias, ¿y el sueldo?"),
          "reconoce un «Gracias» suelto")
    check(texto_cambio_vacante(p_a) == "Ahora seguimos con tu postulación a Operador de 5ª rueda en Fletes México.", "texto del cambio de vacante")
    import inspect

    fuente = inspect.getsource(canal_proceso)
    check("mándamelos por aquí" not in fuente and "Sube tus documentos en esta liga" in fuente, "los documentos se piden SOLO con la liga")
    # misma persona con dos postulaciones: la de Fletes A y una nueva de Grupak → al pasar a otra vacante se avisa
    antes = len(ENVIADOS)
    r = postular(vg, "Mario Operador", "5560000002", respuestas_g)
    p_mg = db.query(Postulacion).filter(Postulacion.codigo == r["postulacion"]).one()
    # la conversación iba en Fletes (la última que el candidato contestó); el siguiente mensaje cae en la entrevista de Grupak
    p_mg.candidato.postulacion_conversacion_id = None
    p_a = db.get(Postulacion, p_a.id)
    p_a.candidato.postulacion_conversacion_id = p_a.id
    db.commit()
    salida = escribe("5560000002", "Sí")
    check(salida and salida[0] == "Ahora seguimos con tu postulación a Ayudante general en Grupak.",
          f"al cambiar de vacante se avisa antes de seguir → {salida[:1]}")

    # ================= 1c. Cambio 50: migración =================
    print("\n--- 1c. Cambio 50: migración ---")
    pl_vieja = PlantillaProceso(cuenta_id=cu.id, nombre="Ruta vieja con prefiltro web", pasos=sproc.normalizar_pasos([
        {"id": "solicitud", "tipo": "solicitud_sin_cv"}, {"id": "pw", "tipo": "prefiltro_web"},
        {"id": "wa", "tipo": "prefiltro_whatsapp"}]), etapas=sproc.normalizar_etapas({}), version=1, creado_por="x", actualizada_por="x")
    db.add(pl_vieja)
    p_m = rcand.crear_postulacion(db, rcand._crear_candidato(db, cu.id, "Mina Migrar", "Portal", False, telefono="5560000009"), vb, cu.id,
                                  "web", consentimiento=True)
    p_m.proceso = {**(p_m.proceso or {}), "pasos": sproc.normalizar_pasos([{"id": "solicitud", "tipo": "solicitud_sin_cv"},
                                                                          {"id": "pw", "tipo": "prefiltro_web"},
                                                                          {"id": "humana", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"}])}
    db.commit()
    paso_pw = next(x for x in sproc.estado_pasos(p_m) if x["id"] == "pw")
    check(paso_pw["estado"] not in ("completada", "omitida"), "candidato detenido en «Prefiltro web»")
    check(db.query(Bitacora).filter(Bitacora.accion == ssw.MARCA_MIGRACION).count() == 1, "el arranque de la API ya corrió la migración (una vez)")
    db.query(Bitacora).filter(Bitacora.accion == ssw.MARCA_MIGRACION).delete()  # simula una base anterior al Cambio 50
    db.commit()
    r50 = asyncio.run(ssw.migrar_cambio50(db, aplicar=True, avanzar=True))
    db.expire_all()
    pl_vieja = db.get(PlantillaProceso, pl_vieja.id)
    check("prefiltro_web" not in [x["tipo"] for x in pl_vieja.pasos] and pl_vieja.version == 2, "la plantilla pierde «Prefiltro web» (sube versión)")
    p_m = db.get(Postulacion, p_m.id)
    paso_pw = next(x for x in sproc.estado_pasos(p_m) if x["id"] == "pw")
    check(p_m.codigo in r50["postulaciones"] and paso_pw["estado"] == "omitida", f"el candidato ya no está detenido ({paso_pw['estado']})")
    check(p_m.etapa == "Entrevista Humana", f"y su ruta avanza a la siguiente etapa ({p_m.etapa})")
    r50b = asyncio.run(ssw.migrar_cambio50(db, aplicar=True))
    check(r50b.get("yaAplicada"), "la migración es idempotente")
    check(db.query(Bitacora).filter(Bitacora.accion == ssw.MARCA_MIGRACION).count() == 1, "marca única en bitácora")

    db.close()

print(f"\n✅ {OK} comprobaciones")
