"""Cambio 37 (2026-10-14). Base desechable; Meta e IA SIMULADAS (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambio37.py

1. Indispensables generados: máximo 5, nunca tareas, «Sin experiencia» sin experiencia, condiciones explícitas «Propuesto
   por Red Human», el de disponibilidad por defecto, «preferentemente» → deseables. Publicar con 1 indispensable.
2. «Preguntas filtro» = UNA entidad (sin frases prohibidas; abiertas no descartan; base sin descarte; web ≥ 3; mismo
   número de indispensables y preguntas en formulario y publicación).
3. Bot: descarta solo con respuesta clara que no cumple; «10 años» / «los mismos 10 años» no descartan; respuesta
   completa sin repregunta; «Claro. Solo para tenerlo en cuenta…» solo en sueldo; «en carro» = 0 y «uno» = 1
   transportes; «uno» en tiempo → «¿Un año o un mes?»; repregunta fallida → «Requiere revisión»; historial.
4. Plantilla inicial nueva y conversación ligada a su vacante (sin «Elegir vacante»); tras el descarte no cambia de
   vacante; refutar el descarte → «Requiere revisión»; contadores «descartados · en revisión».
5. Modo Prueba: postulaciones ilimitadas del mismo número a la misma vacante («Prueba reemplazada»).
6. Caso VAC-1171: la postulación web queda en su vacante y Cuenta aunque el número tenga otro proceso en otra Cuenta.
7. Frontend: el aviso «Falta para publicar» es diferido; editor único; contadores con liga.
"""

import asyncio
import os
import re
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
_dir = _temporal.directorio("rh_c37_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c37.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-c37"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cuenta, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion, fijar_conversacion  # noqa: E402
from app.routers.webhooks import procesar_entrante  # noqa: E402
from app.services import ia  # noqa: E402
from app.services import plantillas_meta  # noqa: E402
from app.services import preguntas_filtro as pfu  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
ENVIADOS = []
APP = RAIZ.parent / "red-human-app"


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


# Cambio 48: el inicio del prefiltro va en cascada (texto libre → plantilla); el texto libre solo pasa a quien ya escribió
VENTANA = set()


async def _meta_falso(cuerpo):
    ENVIADOS.append(cuerpo)
    if cuerpo.get("type") == "text" and cuerpo.get("to") not in VENTANA:
        return {"enviado": False, "proveedor": "meta", "detalle": "131047: Re-engagement message", "codigo": 131047}
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "mensaje_id": f"wamid.{len(ENVIADOS)}"}


whatsapp._meta_post = _meta_falso


def plantillas(desde=0, nombre=""):
    return [c for c in ENVIADOS[desde:] if c.get("type") == "template" and (not nombre or c["template"]["name"] == nombre)]


def textos(desde=0, tel=""):
    return [c["text"]["body"] for c in ENVIADOS[desde:] if c.get("type") == "text" and (not tel or c["to"] == "52" + tel)]


def _msg(tel, cuerpo):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": "Candidato"}, "wa_id": f"52{tel}"}],
        "messages": [{"from": f"52{tel}", "id": f"wamid.in.{tel}.{len(ENVIADOS)}.{id(cuerpo)}", **cuerpo}]}}]}]}


def boton(tel, t):
    return _msg(tel, {"type": "button", "button": {"text": t, "payload": t}})


def escribe(tel, t):
    VENTANA.add("52" + tel)
    return asyncio.run(procesar_entrante(db, whatsapp.parsear_webhook(_msg(tel, {"type": "text", "text": {"body": t}}))))


def pulsa(tel, t):
    VENTANA.add("52" + tel)
    return asyncio.run(procesar_entrante(db, whatsapp.parsear_webhook(boton(tel, t))))


FRASE = "Claro. Solo para tenerlo en cuenta"

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="C37 interna", nombre_comercial="Logística Norte", estado="Activa", slug="c37-norte")
    otra = Cuenta(nombre="C37 otra", nombre_comercial="Comercial Sur", estado="Activa", slug="c37-sur")
    db.add_all([cuenta, otra])
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    MASIVOS = sproc.ruta_base("masivos_sin_documentos")["pasos"]

    # ===================================================== 1. Indispensables generados
    print("\n--- 1. Reglas de los indispensables que genera Red Human ---")
    ind, prop, des = ia.reglas_indispensables(
        ["Secundaria terminada", "Licencia de manejo tipo B"],
        ["Atender a clientes en mostrador", "Realizar inventarios", "Experiencia de 1 año en almacén", "Disponibilidad para rolar turnos",
         "Saber usar montacargas", "Conocimiento de primeros auxilios", "Manejo de Excel básico"],
        responsabilidades=["Atender a clientes en mostrador", "Realizar inventarios semanales"], seniority="Junior")
    check(len(ind) <= 5 and ind[:2] == ["Secundaria terminada", "Licencia de manejo tipo B"],
          f"máximo 5 indispensables y los de RH primero, literal ({len(ind)}): {ind}")
    check(not any(ia.es_tarea(x, ["Atender a clientes en mostrador", "Realizar inventarios semanales"]) for x in prop)
          and "Atender a clientes en mostrador" not in ind and "Realizar inventarios" not in ind, "ninguno es una tarea o responsabilidad")

    r = client.post("/vacantes/generar", json={
        "titulo": "Ayudante de bodega", "area": "Operaciones", "seniority": "Sin experiencia", "ubicacion_estado": "Nuevo León",
        "ubicacion_municipio": "Monterrey", "modalidad": "Presencial", "sueldo_desde": 9000, "sueldo_periodicidad": "mensual",
        "descripcion": "Buscamos ayudante de bodega. Licencia tipo B. Preferentemente con experiencia en almacén. Horario de lunes a "
                       "viernes de 8 a 5. Se requiere experiencia de 2 años en ventas.",
        "requisitos_indispensables": [], "proceso": {"pasos": MASIVOS}})
    g = r.json()
    check(r.status_code == 200, f"/vacantes/generar ({r.status_code})")
    check(not any(ia.es_de_experiencia(x) for x in g["requisitos_indispensables"]),
          f"«Sin experiencia»: ningún indispensable de experiencia ({g['requisitos_indispensables']})")
    check(not any(q["descarta"] and q.get("dato") == "experiencia" for q in g["filtro"]), "ni preguntas que descarten por experiencia")
    check("Licencia de manejo tipo B" in g["requisitos_indispensables"] and "Licencia de manejo tipo B" in g["indispensables_propuestos"],
          "«licencia tipo B» en la descripción → indispensable «Propuesto por Red Human»")
    lic = next(q for q in g["filtro"] if q.get("requisito") == "Licencia de manejo tipo B")
    check(lic["etiqueta"] == "Propuesto por Red Human" and lic["descarta"] and lic["tipo"] == "si_no",
          f"su pregunta trae la etiqueta, tipo Sí/No y regla de descarte («{lic['pregunta']}» · {lic['regla']})")
    check(any("Experiencia en almac" in x for x in g["requisitos_deseables"]), f"«preferentemente…» → deseables ({g['requisitos_deseables']})")
    check(len(g["requisitos_indispensables"]) <= 5, "máximo 5")

    r = client.post("/vacantes/generar", json={
        "titulo": "Auxiliar de limpieza", "area": "Servicios", "seniority": "Junior", "ubicacion_estado": "Jalisco",
        "ubicacion_municipio": "Zapopan", "modalidad": "Presencial", "sueldo_periodicidad": "a_convenir",
        "descripcion": "Limpieza de oficinas.", "requisitos_indispensables": [], "proceso": {"pasos": MASIVOS}})
    g2 = r.json()
    defecto = "Disponibilidad para trabajar presencial en Zapopan, Jalisco"
    check(g2["requisitos_indispensables"] == [defecto] and g2["indispensables_propuestos"] == [defecto],
          f"sin indispensables ni condiciones → «{defecto}»")

    # ===================================================== 2. Entidad única
    print("\n--- 2. «Preguntas filtro»: una sola entidad ---")
    todas = g["filtro"] + g2["filtro"]
    prohibidas = [q["pregunta"] for q in todas if pfu.tiene_frase_prohibida(q["pregunta"]) or "cuál es tu caso" in q["pregunta"].lower()]
    check(not prohibidas, f"ninguna pregunta usa frases prohibidas («¿Cuál es tu caso?», «¿Cumples con…?») {prohibidas}")
    check(all(not q["descarta"] for q in todas if q["tipo"] == "abierta"), "las preguntas abiertas no descartan")
    check(all(not q["descarta"] for q in todas if q["base"] and not q.get("indispensable")), "las base no descartan")
    # 2026-10-16: sin edad ni «¿Te interesa?» (la edad solo si la vacante la pide); el sueldo es la expectativa
    check({q["dato"] for q in g["filtro"] if q["base"]} >= {"sueldo", "zona", "traslado"}
          and not {q["dato"] for q in g["filtro"] if q["base"]} & {"edad", "interes"}
          and next(q for q in g["filtro"] if q["base"] and q["dato"] == "sueldo").get("expectativa")
          and all(q["etiqueta"] == "Base" for q in g["filtro"] if q["base"]), "base: expectativa de sueldo, colonia y traslado con etiqueta «Base»")
    check(len(g2["preguntas_filtro"]) >= 3 and g2["preguntas_filtro_whatsapp"] == [], "web derivado con ≥ 3 preguntas; sin prefiltro de WhatsApp aparte")
    abierta = pfu.normalizar_item({"pregunta": "¿Qué hiciste?", "tipo": "abierta", "descarta": True})
    check(abierta["descarta"] is False and abierta["regla"] == pfu.REGLA_NO_DESCARTA, "una abierta marcada «descarta» se corrige en código")

    INDISP = ["5 años de experiencia en derecho corporativo", "Licencia de conducir tipo B", "No más de 2 transportes para llegar"]
    r = client.post("/vacantes", json={
        "titulo": "Gestor jurídico", "area": "Legal", "seniority": "Senior", "ubicacion_estado": "Nuevo León",
        "ubicacion_municipio": "Monterrey", "modalidad": "Presencial", "sueldo_desde": 20000, "sueldo_periodicidad": "mensual",
        "requisitos_indispensables": INDISP, "descripcion": "Gestión de trámites legales.", "responsabilidades": ["Gestionar trámites"],
        "proceso": {"pasos": MASIVOS}, "publicar": True, "plataformas": ["Portal"]})
    check(r.status_code == 201, f"alta de vacante con 3 indispensables ({r.status_code}: {r.text[:200]})")
    vac = r.json()
    check(len([q for q in vac["filtro"] if q.get("indispensable")]) == len(INDISP) == len(vac["preguntasRequisitos"]),
          "formulario: una pregunta por indispensable (mismo número)")
    pub = client.get(f"/vacantes/slug/{vac['slug']}").json()
    check(len([c for c in pub["criterios"] if not c.get("base")]) == len(INDISP) and len(pub["criterios"]) >= 3,
          f"publicación: el mismo número de indispensables y ≥ 3 preguntas ({len(pub['criterios'])})")
    v1 = db.query(Vacante).filter_by(codigo=vac["id"]).one()
    exp = next(q for q in vac["filtro"] if q.get("dato") == "experiencia")
    check(exp["tipo"] == "numero" and exp["descarta"] and "5 años" in exp["regla"], f"experiencia: número con regla («{exp['regla']}»)")

    r = client.post("/vacantes", json={"titulo": "Auxiliar general", "area": "Operaciones", "seniority": "Junior",
                                       "ubicacion_estado": "Nuevo León", "ubicacion_municipio": "Apodaca", "modalidad": "Presencial",
                                       "sueldo_periodicidad": "a_convenir", "descripcion": "Apoyo en planta.", "requisitos_indispensables": [],
                                       "proceso": {"pasos": MASIVOS}})
    v_def = r.json()
    check(r.status_code == 201 and v_def["requisitos"] and not v_def["faltantesPublicacion"],
          f"sin indispensables capturados: se genera el de disponibilidad y no falta nada ({v_def['requisitos']})")
    r = client.post(f"/vacantes/{v_def['id']}/publicar", json={"plataformas": ["Portal"]})
    check(r.status_code == 200, f"y se puede publicar (mínimo 1 indispensable) ({r.status_code})")

    # ===================================================== 3. Bot
    print("\n--- 3. Plantilla inicial y conversación ---")
    app.dependency_overrides.pop(cuenta_actual, None)
    desde = len(ENVIADOS)
    r = client.post("/candidatos/postular", data={"vacante": vac["slug"], "nombre": "Ana Ruiz Soto", "telefono": "5510468412",
                                                  "consentimiento": "true"})
    check(r.status_code == 201, f"postulación web ({r.status_code})")
    p1 = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    t = plantillas(desde, "inicio_entrevista_rh")
    params = [x["text"] for x in t[0]["template"]["components"][0]["parameters"]] if t else []
    check(params == ["Ana", "Logística Norte", "Gestor jurídico"], f"plantilla con nombre, empresa y puesto → {params}")
    ultimo = [m.texto for m in p1.mensajes if m.rol == "assistant"][-1]
    check("Hola, Ana. Soy Red Human, el agente virtual de Logística Norte. Recibimos tu postulación para Gestor jurídico. ¿Tienes 5 "
          "minutos para responder unas preguntas?" in ultimo, f"texto de la plantilla inicial ({ultimo[:160]})")

    r = pulsa("5510468412", "Sí, empezar ahora")
    check(r.get("postulacion") == p1.codigo and "Soy Red Human" not in r["respuesta"] and "experiencia" in r["respuesta"].lower(),
          f"la respuesta al botón es de SU postulación y arranca con la primera pregunta sin volver a presentarse ({r['respuesta']})")
    r = escribe("5510468412", "los mismos 10 años")
    check(r.get("prefiltro") is None and "licencia" in r["respuesta"].lower(), f"«los mismos 10 años» no descarta ({r['respuesta']})")
    r = escribe("5510468412", "sí")
    check("transportes" in r["respuesta"].lower(), "Sí/No completo: sin repregunta")
    r = escribe("5510468412", "en carro")
    db.refresh(p1)
    check((p1.analisis.get("prefiltro_datos") or {}).get("transportes") == 0, "«en carro» se registra como 0 transportes")
    check("edad" in r["respuesta"].lower() or "años tienes" in r["respuesta"].lower(), f"sigue con la edad ({r['respuesta']})")
    escribe("5510468412", "32")
    escribe("5510468412", "sí")
    r = escribe("5510468412", "mmm")
    check(r.get("aclaracion") and r["respuesta"].startswith(FRASE), f"sueldo evasivo → «{FRASE}…» ({r['respuesta']})")
    r = escribe("5510468412", "sí me interesa")
    check("colonia" in r["respuesta"].lower(), "sigue con la colonia")
    r = escribe("5510468412", "Colonia Centro, Monterrey")
    check(not r.get("aclaracion") and "tiempo" in r["respuesta"].lower(), f"respuesta completa a una abierta: sin repregunta ({r['respuesta']})")
    r = escribe("5510468412", "40 minutos")
    db.refresh(p1)
    check(p1.estado == "cumple" and p1.activa, f"cumple todo → aprobado ({p1.estado})")

    print("\n--- 3b. Repreguntas, «¿Un año o un mes?», descarte y post-descarte ---")
    app.dependency_overrides.pop(cuenta_actual, None)
    r = client.post("/candidatos/postular", data={"vacante": vac["slug"], "nombre": "Beto Lara", "telefono": "5510468413",
                                                  "consentimiento": "true"})
    p2 = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    pulsa("5510468413", "Sí, empezar ahora")
    r = escribe("5510468413", "uno")
    check(r["respuesta"] == "¿Un año o un mes?", f"«uno» sin unidad → «¿Un año o un mes?» ({r['respuesta']})")
    r = escribe("5510468413", "sí")
    check(r.get("aclaracion") and FRASE not in r["respuesta"], f"respuesta sin unidad otra vez → repregunta concreta, sin «{FRASE}» ({r['respuesta']})")
    r = escribe("5510468413", "un año")
    db.refresh(p2)
    check(not p2.activa and p2.motivo_cierre == "descartado", "un dato claro que no cumple (1 año de 5) descarta")
    hist = [h for h in p2.historial or [] if h.get("evento") in ("prefiltro_repregunta", "descartado")]
    check(any(h["evento"] == "prefiltro_repregunta" and h.get("motivo") for h in hist)
          and any(h["evento"] == "descartado" and "respondió" in h.get("motivo", "") for h in hist),
          "el historial guarda cada repregunta y el descarte con su motivo")
    # otra postulación activa del mismo número en otra vacante: el bot NO cambia de vacante tras el descarte
    otra_vac = Vacante(codigo="VAC-C37B", titulo="Chofer", estado="Publicada", cuenta_id=cuenta.id, slug="chofer-c37",
                       requisitos="Licencia de conducir tipo B")
    db.add(otra_vac)
    db.flush()
    p_otra = crear_postulacion(db, p2.candidato, otra_vac, cuenta.id, "rh_directo", consentimiento=True)
    db.commit()
    r = escribe("5510468413", "ok gracias")
    check(r.get("accion") == "post_descarte" and r.get("postulacion") == p2.codigo and "no continúa" in r["respuesta"],
          f"después del descarte el bot sigue en la misma vacante (no salta a {p_otra.codigo}) ({r.get('accion')})")
    check(not any(c.get("type") == "interactive" for c in ENVIADOS[-3:]), "sin «Elegir vacante»")
    r = escribe("5510468413", "pero sí tengo 6 años de experiencia")
    db.refresh(p2)
    check(r.get("accion") == "descarte_refutado" and r["respuesta"].startswith("Gracias por aclararlo"),
          f"refutación → «Gracias por aclararlo…» ({r['respuesta']})")
    check(p2.activa and p2.estado == "revision" and p2.vacante_id == v1.id, "la postulación vuelve activa, en su vacante, «Requiere revisión»")
    paso = next(x for x in sproc.estado_pasos(p2) if x["tipo"] == "prefiltro_whatsapp")
    check(paso["requiereRevision"] and paso["estadoUnificadoTexto"] == "Requiere revisión", "el paso muestra «Requiere revisión»")
    check(sproc.bloqueo_no_aprobada(p2, sproc.estado_pasos(p2)) is not None, "la ruta queda detenida para RH (continuar o descartar)")
    check(any(h.get("evento") == "descarte_refutado" for h in p2.historial), "refutación en el historial")

    print("\n--- 3c. «Requiere revisión» tras la repregunta ---")
    app.dependency_overrides.pop(cuenta_actual, None)
    r = client.post("/candidatos/postular", data={"vacante": vac["slug"], "nombre": "Carla Méndez", "telefono": "5510468414",
                                                  "consentimiento": "true"})
    p3 = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    pulsa("5510468414", "Sí, empezar ahora")
    r = escribe("5510468414", "sí tengo")
    check(r.get("aclaracion") and FRASE not in r["respuesta"], f"afirmativa sin el dato → UNA repregunta concreta ({r['respuesta']})")
    r = escribe("5510468414", "bastante")
    check(not r.get("aclaracion") and "licencia" in r["respuesta"].lower(), "sigue sin el dato: no repregunta otra vez, continúa")
    for resp in ("sí", "dos", "29", "sí", "sí", "Col. Del Valle, San Pedro", "media hora"):
        r = escribe("5510468414", resp)
    db.refresh(p3)
    check(p3.activa and p3.estado == "revision" and (p3.analisis.get("prefiltro_web") or {}).get("requiere_revision"),
          f"al terminar: «Requiere revisión», nunca descartado ({p3.estado})")
    check("revisará tu postulación" in r["respuesta"], "le avisa que RH revisará")
    check((p3.analisis.get("prefiltro_datos") or {}).get("transportes") == 2, "«dos» se registra como 2 transportes")
    check(pfu.transportes_de("uno") == 1 and pfu.transportes_de("en moto") == 0 and pfu.transportes_de("caminando") == 0,
          "«uno» = 1; «moto» y «caminando» = 0")

    print("\n--- 3d. Contadores de la vacante ---")
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    det = client.get(f"/vacantes/{vac['id']}").json()
    check(det["embudo"]["enRevision"] == 2 and det["embudo"]["descartados"] == 0,
          f"«[n] descartados · [n] en revisión» ({det['embudo']['descartados']} · {det['embudo']['enRevision']})")
    p3.cerrar("descartado")
    db.commit()
    det = client.get(f"/vacantes/{vac['id']}").json()
    lista = client.get("/candidatos", params={"vacante": vac["id"], "descartados": "true"}).json()
    check(det["embudo"]["descartados"] == 1 and [x["id"] for x in lista] == [p3.codigo],
          "la liga «descartados» lista exactamente las descartadas de la vacante")

    # ===================================================== 5. Modo Prueba
    print("\n--- 5. Modo Prueba: postulaciones ilimitadas del mismo número ---")
    obtener(db).modo_prueba = True
    db.commit()
    app.dependency_overrides.pop(cuenta_actual, None)
    codigos = []
    for _ in range(3):
        r = client.post("/candidatos/postular", data={"vacante": vac["slug"], "nombre": "Prueba Interna", "telefono": "5510468499",
                                                      "consentimiento": "true"})
        check(r.status_code == 201, f"postulación de prueba ({r.status_code})")
        codigos.append((r.json()["candidato"], r.json()["postulacion"]))
    check(len({c for c, _ in codigos}) == 3, "cada postulación crea un candidato nuevo")
    viejas = [db.query(Postulacion).filter_by(codigo=pc).one() for _, pc in codigos[:2]]
    nueva = db.query(Postulacion).filter_by(codigo=codigos[2][1]).one()
    check(all(not x.activa and x.motivo_cierre == "prueba_reemplazada" for x in viejas) and nueva.activa,
          "las anteriores pasan a «Prueba reemplazada»; la nueva queda activa")
    obtener(db).modo_prueba = False
    db.commit()

    # ===================================================== 6. Caso VAC-1171
    print("\n--- 6. Caso VAC-1171: la postulación web queda en su vacante aunque el número tenga otro proceso ---")
    vac_sur = Vacante(codigo="VAC-C37S", titulo="Vendedor", estado="Publicada", cuenta_id=otra.id, slug="vendedor-sur",
                      requisitos="Experiencia en ventas")
    db.add(vac_sur)
    db.flush()
    vac_sur.proceso = sproc.proceso_para_vacante(db, otra.id, {}, {"pasos": MASIVOS})
    persona_sur = _crear_candidato(db, otra.id, "Diego Paz", "WhatsApp", False, telefono="5522334455", wa_id="525522334455")
    p_sur = crear_postulacion(db, persona_sur, vac_sur, otra.id, "whatsapp", consentimiento=True)
    fijar_conversacion(p_sur)
    db.commit()
    r = client.post("/candidatos/postular", data={"vacante": vac["slug"], "nombre": "Diego Paz", "telefono": "+52 55 2233 4455",
                                                  "consentimiento": "true"})
    p_web = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    check(p_web.vacante_id == v1.id and p_web.cuenta_id == cuenta.id and p_web.candidato.cuenta_id == cuenta.id,
          "la postulación web nace en la vacante y la Cuenta de la liga")
    r = pulsa("5522334455", "Sí, empezar ahora")
    check(r.get("postulacion") == p_web.codigo and r.get("accion") != "elegir_postulacion",
          f"la respuesta a la plantilla va a ESA postulación, sin «Elegir vacante» ({r.get('postulacion')})")
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    ids = [x["id"] for x in client.get("/candidatos", params={"vacante": vac["id"]}).json()]
    check(p_web.codigo in ids, "aparece en la lista de candidatos de su vacante")

    print("\n--- 6b. Ruta corporativa: la misma lista con sus ajustes (sin colonia, expectativa de sueldo) ---")
    r = client.post("/vacantes", json={"titulo": "Analista contable", "area": "Finanzas", "seniority": "Senior",
                                       "ubicacion_estado": "Nuevo León", "ubicacion_municipio": "Monterrey", "modalidad": "Presencial",
                                       "sueldo_desde": 25000, "sueldo_periodicidad": "mensual", "descripcion": "Contabilidad general.",
                                       "requisitos_indispensables": ["Licenciatura en Contaduría", "3 años de experiencia en contabilidad"],
                                       "responsabilidades": ["Registrar pólizas"], "proceso": {"pasos": sproc.ruta_base("corporativo")["pasos"]}})
    vc = db.query(Vacante).filter_by(codigo=r.json()["id"]).one()
    pc = crear_postulacion(db, _crear_candidato(db, cuenta.id, "Eva Corp", "Portal", False, telefono="5510468477"), vc, cuenta.id, "web",
                           consentimiento=True)
    db.commit()
    crit = pconv.criterios_de(vc, pc)
    datos_c = [c.get("dato") for c in crit]
    check(crit[0]["dato"] == "escolaridad" and "zona" not in datos_c and "traslado" not in datos_c
          and next(c for c in crit if c.get("dato") == "sueldo").get("expectativa"),
          f"corporativo: indispensables primero, sin colonia ni traslado, el sueldo es la expectativa ({datos_c})")

    # ===================================================== 7. Frase restringida y frontend
    print("\n--- 7. Frase de sueldo y frontend ---")
    fuentes = "".join((RAIZ / "app" / "services" / f).read_text(encoding="utf-8") for f in
                      ("preguntas_filtro.py", "prefiltro_operativo.py", "prefiltro_conversacional.py"))
    usos = [ln for ln in fuentes.splitlines() if FRASE in ln and '"' in ln and "sueldo" not in ln.lower() and "cantidad" not in ln
            and "FRASE_SUELDO" not in ln and not ln.strip().startswith("#")]
    check(not usos, f"«{FRASE}…» solo en la pregunta de sueldo {usos[:2]}")
    form = (APP / "components/dashboard/vacantes/formulario-contenido.tsx").read_text(encoding="utf-8")
    check("Cómo pregunta Red Human" not in form and "Preguntas filtro" in form, "el formulario tiene UNA sola lista «Preguntas filtro»")
    check("mostrarFaltantes" in form and re.search(r"mostrarFaltantes\s*&&\s*avisosRuta", form) is not None,
          "«Falta para publicar» solo tras «Generar con Red Human» o al intentar publicar")
    check("Propuesto por Red Human" in form, "los indispensables propuestos llevan su etiqueta")
    detalle = (APP / "app/dashboard/vacantes/page.tsx").read_text(encoding="utf-8")
    check('descartados: "true"' in detalle and 'estado: "revision"' in detalle and "en revisión" in detalle,
          "Resumen de la vacante: «[n] descartados · [n] en revisión» con liga")
    check("intentoPub &&" in detalle, "en el detalle, «Falta para publicar» aparece al intentar publicar")
    cand = (APP / "app/dashboard/candidatos/page.tsx").read_text(encoding="utf-8")
    check('searchParams.get("descartados")' in cand and '"revision"' in cand, "la lista de candidatos abre filtrada desde esas ligas")
    aplicar = (APP / "app/aplicar/[slug]/formulario-aplicar.tsx").read_text(encoding="utf-8")
    check('type="number"' in aplicar, "el formulario web pregunta números (con su unidad) y opciones")

print(f"\n✅ Cambio 37: {OK} verificaciones OK")
