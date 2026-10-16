"""Demo UVAQ (2026-10-15): seed + código nuevo (agente por Cuenta, trato de «usted», plantillas por Cuenta, escala de
valores, dictamen médico, filtros de revisión). Base desechable; IA y Meta SIMULADAS (sin red).

    PYTHONIOENCODING=utf-8 python scripts/verificar_uvaq.py
"""

import asyncio
import json
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
_dir = _temporal.directorio("rh_uvaq_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "uvaq.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-uvaq"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cuenta, Evaluacion, NotificacionEnviada, Postulacion, Vacante  # noqa: E402
from app.routers.webhooks import procesar_entrante  # noqa: E402
from app.serial import nombre_empresa_candidato, vacante_dict  # noqa: E402
from app.services import escala_valores, ia, mensajeria, plantillas_cuenta  # noqa: E402
from app.services import evaluaciones as sev  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

import seed_uvaq as su  # noqa: E402

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


def plantillas_a(tel, desde=0):
    return [(c["template"]["name"], [x["text"] for x in (c["template"].get("components") or [{}])[0].get("parameters", [])])
            for c in ENVIADOS[desde:] if c.get("type") == "template" and c["to"] == "52" + tel]


def textos_a(tel, desde=0):
    return [c["text"]["body"] for c in ENVIADOS[desde:] if c.get("type") == "text" and c["to"] == "52" + tel]


def postulacion_de(nombre):
    return db.query(Postulacion).join(Candidato, Candidato.id == Postulacion.candidato_id).filter(Candidato.nombre == nombre).one()


with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    db.commit()

    # ===================================================== 1. seed
    print("\n--- 1. Seed ---")
    sys.argv = ["seed"]
    check(su.main() == 0, "simulacro sin errores")
    db.expire_all()
    check(db.query(Cuenta).filter_by(slug="uvaq").count() == 0, "el simulacro no escribe nada")
    sys.argv = ["seed", "--aplicar"]
    check(su.main() == 0, "seed_uvaq.py --aplicar (incluye su propia validación)")
    check(su.main() == 0, "segunda corrida")
    db.expire_all()
    cu = db.query(Cuenta).filter_by(slug="uvaq").one()
    vs = {v.titulo: v for v in db.query(Vacante).filter(Vacante.cuenta_id == cu.id)}
    check(len(vs) == 3 and db.query(Postulacion).filter(Postulacion.cuenta_id == cu.id).count() == 17,
          "idempotente: 3 vacantes y 17 candidatos (no duplica)")
    v1, v2, v3 = vs[su.V1], vs[su.V2], vs[su.V3]
    check(ia.presentacion_bot(nombre_empresa_candidato(v1)) == "Soy Vasco, asistente de Talento UVAQ.", "el agente se presenta como Vasco")
    otra = Cuenta(nombre="Otra", nombre_comercial="Otra Empresa", canal_mensajeria="whatsapp", estado="Activa", slug="otra")
    db.add(otra)
    db.commit()
    ia.refrescar_agentes()
    plantillas_cuenta.refrescar()
    check(ia.presentacion_bot("Otra Empresa") == "Soy Red Human, el agente virtual de Otra Empresa.", "otras Cuentas siguen con Red Human")
    check(not ENVIADOS, "sembrar no mandó ningún mensaje")

    # ===================================================== 2. trato de «usted»
    print("\n--- 2. Trato de «usted» (Director de Planeación) ---")
    from app.routers.candidatos import con_trato

    claudia = postulacion_de("Claudia Morales Echeverría")
    t = con_trato(claudia, "Tu siguiente paso es una entrevista. Te escribirán por este medio. ¿Tienes alguna duda?")
    check(t == "Su siguiente paso es una entrevista. Le escribirán por este medio. ¿Tiene alguna duda?", f"mensaje fijo en usted → «{t}»")
    laura = postulacion_de("Laura Gutiérrez Mendoza")
    check(con_trato(laura, "¿Tienes alguna duda?") == "¿Tienes alguna duda?", "Intendencia sigue en «tú»")
    from app.models import Entrevista
    from app.services.entrevistas import system_prompt

    e_cl = db.query(Entrevista).filter(Entrevista.postulacion_id == claudia.id).first()
    prompt = system_prompt(e_cl)
    check(ia.REGLA_USTED in prompt and "Vasco, asistente de Talento UVAQ" in prompt, "el prompt del avatar: trato de usted y nombre Vasco")
    check(su.CASO_RECTORIA in json.dumps(v3.guiones, ensure_ascii=False) and su.CASO_ZAMORA in json.dumps(v2.guiones, ensure_ascii=False),
          "guiones con el Caso Rectoría y el Caso Zamora")

    # ===================================================== 3. plantillas ancladas a las aprobadas
    print("\n--- 3. Plantillas de Meta solo aprobadas ---")
    tel = "4439900099"
    desde = len(ENVIADOS)
    with mensajeria.de_cuenta(cu.id):
        r = asyncio.run(whatsapp.enviar_en_cascada(tel, "Hola Juan, ¿empezamos?", "inicio_entrevista_rh", ["Juan Pérez", "UVAQ", "Intendencia"]))
    check(plantillas_a(tel, desde) == [("inicio_entrevista_rh", ["Juan"])], f"inicio: «inicio_entrevista_rh» con 1 variable → {plantillas_a(tel, desde)}")
    desde = len(ENVIADOS)
    with plantillas_cuenta.de_cuenta_plantillas(cu.id):
        r = asyncio.run(whatsapp.enviar_plantilla_evaluador("4439900098", "aviso_evaluador_rh",
                                                            ["Jefe", "Eduardo", su.V2, "martes 20 de octubre, 10:00 (hora de Ciudad de México)",
                                                             "Campus Morelia", "https://x/ev"], "Tienes una entrevista"))
    check(plantillas_a("4439900098", desde) == [("alerta_entrevista_asignada", ["Jefe", "Eduardo", su.V2, "martes 20 de octubre",
                                                                                "10:00 (hora de Ciudad de México)", "https://x/ev"])],
          f"aviso al jefe → «alerta_entrevista_asignada» (6 variables) → {plantillas_a('4439900098', desde)}")
    desde = len(ENVIADOS)
    medico = ["Dr. Juan", "Eduardo Rodríguez", su.V2, "martes 20 de octubre", "10:00 (hora de Ciudad de México)", "Campus Morelia",
              "https://x/ev"]
    with plantillas_cuenta.de_cuenta_plantillas(cu.id):
        r = asyncio.run(whatsapp.enviar_plantilla_evaluador("4439900097", "aviso_medico", medico, "Tienes un examen médico"))
    check(plantillas_a("4439900097", desde) == [("aviso_medico", medico)],
          f"aviso al médico → «aviso_medico» aprobada (7 variables) → {plantillas_a('4439900097', desde)}")
    desde = len(ENVIADOS)
    with mensajeria.de_cuenta(cu.id):
        r = asyncio.run(whatsapp.enviar_en_cascada(tel, "Gracias por tu interés en UVAQ…", "descarte_postulacion_rh", ["UVAQ"]))
    check(not plantillas_a(tel, desde) and r.get("estado") == plantillas_cuenta.ESTADO_VENTANA,
          "descarte (plantilla no aprobada): NO se llama a Meta; queda pendiente de la ventana")
    desde = len(ENVIADOS)
    with mensajeria.de_cuenta(cu.id):
        r = asyncio.run(whatsapp.enviar_mensaje(tel, "Tu entrevista con el jefe es el martes a las 10:00."))
    check(plantillas_a(tel, desde) == [("aviso_proceso_rh", [])], f"aviso de cita al candidato fuera de ventana → «aviso_proceso_rh» sin variables")
    with plantillas_cuenta.de_cuenta_plantillas(cu.id):
        r = asyncio.run(whatsapp.enviar_plantilla_evaluador("4439900098", "cita_cambio_evaluador_rh", ["a"] * 6, "Tu cita cambió"))
    check(r.get("estado") == plantillas_cuenta.ESTADO_VENTANA, "«cita_cambio_evaluador_rh» (no aprobada para UVAQ) no sale")
    pend = db.query(NotificacionEnviada).filter(NotificacionEnviada.destino == tel, NotificacionEnviada.estado == "ventana").count()
    check(pend == 2, f"2 mensajes del candidato quedan pendientes ({pend})")
    VENTANA.add("52" + tel)
    desde = len(ENVIADOS)
    msg = whatsapp.parsear_webhook({"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": "Juan"}, "wa_id": f"52{tel}"}],
        "messages": [{"from": f"52{tel}", "id": "wamid.in.uvaq.1", "type": "text", "text": {"body": "Hola"}}]}}]}]})
    asyncio.run(procesar_entrante(db, msg))
    salieron = textos_a(tel, desde)
    check("Gracias por tu interés en UVAQ…" in salieron and "Tu entrevista con el jefe es el martes a las 10:00." in salieron,
          f"al escribir el candidato, lo pendiente sale en texto libre → {salieron[:3]}")
    desde = len(ENVIADOS)
    asyncio.run(whatsapp.enviar_en_cascada("4439900097", "texto", "descarte_postulacion_rh", ["Otra"]))
    check(plantillas_a("4439900097", desde) and plantillas_a("4439900097", desde)[0][0] == "descarte_postulacion_rh",
          "sin Cuenta con política, las plantillas de siempre no cambian")

    # ===================================================== 4. escala de valores
    print("\n--- 4. Escala de valores UVAQ ---")
    ok_ = {v: 4 for v in escala_valores.VALORES}
    check(escala_valores.evaluar(ok_, "operativo")["cumple"] and escala_valores.evaluar(ok_, "jefatura")["cumple"]
          and not escala_valores.evaluar(ok_, "direccion")["cumple"], "promedio 4.0: Operativo y Jefatura cumplen; Dirección (4.2) no")
    r = escala_valores.evaluar({**ok_, "Honestidad": 2}, "operativo")
    check(not r["cumple"] and r["alertas"] == ["Honestidad: 2"], f"Operativo: un 2 → alerta ({r['detalle']})")
    r = escala_valores.evaluar({**{v: 5 for v in escala_valores.VALORES}, "Compromiso": 3}, "direccion")
    check(not r["cumple"] and r["alertas"] == ["Compromiso: 3"], "Dirección: un 3 (< 3.5) → alerta aunque el promedio sea alto")
    r = escala_valores.evaluar({**ok_, "Dignidad": None}, "operativo")
    check(not r["cumple"] and r["sin_dato"] == ["Dignidad"], "un valor sin dato → revisión (nunca se aprueba a ciegas)")
    miguel = postulacion_de("Miguel Ángel Ortiz Reyes")
    paso = next(x for x in sproc.estado_pasos(miguel) if x["id"] == "psicometria")
    check(paso["estadoUnificado"] == "pendiente_revision" and miguel.activa and sproc.bloqueo_no_aprobada(miguel, sproc.estado_pasos(miguel)),
          f"Miguel Ángel: valores bajos → «Requiere revisión», la ruta se detiene ({paso['estadoUnificadoTexto']})")
    paso = next(x for x in sproc.estado_pasos(claudia) if x["id"] == "psicometria")
    ev_cl = db.query(Evaluacion).filter_by(postulacion_id=claudia.id, tipo="psicometrica").one()
    check(paso["estadoUnificado"] in ("completada", "aprobada") and ev_cl.resultado_json["evaluacion_red_human"]["promedio"] == 4.5, "Claudia: valores 4.5 (Dirección) → cumple")

    # ===================================================== 5. examen médico
    print("\n--- 5. Examen médico ---")
    sandra = postulacion_de("Sandra Jiménez Castro")
    med = db.query(Evaluacion).filter_by(postulacion_id=sandra.id, tipo="medica").one()
    med.estado, med.conclusion, med.revisada_en = "con_resultado", "apto_con_restricciones", med.creado_en if hasattr(med, "creado_en") else None
    db.commit()
    paso = next(x for x in sproc.estado_pasos(sandra) if x["id"] == "medico")
    check(paso["estadoUnificado"] == "pendiente_revision", f"«Apto con restricciones» → «Requiere revisión» ({paso['estadoUnificadoTexto']})")
    med.conclusion = "no_apto"
    db.commit()
    asyncio.run(sproc.avanzar_seguro(db, sandra))
    db.refresh(sandra)
    check(not sandra.activa and sandra.motivo_cierre == "descartado" and sandra.historial[-1]["origen"] == "examen_medico",
          "«No apto» → descartada automáticamente (motivo en el historial)")
    raul = postulacion_de("Raúl Esquivel Navarro")
    sig = sproc.siguiente_actividad(raul)
    check(raul.etapa == "Contratación" and sig and sig["nombre"] == su.SAEKO, f"Raúl (Apto): «{su.SAEKO}» ({sig})")

    # ===================================================== 6. filtros de revisión (solicitud web)
    print("\n--- 6. Filtros: excluyentes y de revisión ---")

    def postular(v, nombre, tel, respuestas):
        r = client.post("/candidatos/postular", data={"vacante": v.slug, "nombre": nombre, "telefono": tel, "consentimiento": "true",
                                                      "respuestas": json.dumps([{"pregunta": q, "respuesta": a} for q, a in respuestas.items()])})
        assert r.status_code in (200, 201), r.text
        db.expire_all()
        return r.json(), db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()

    base2 = {c["pregunta"]: "Sí" for c in vacante_dict(v2)["criterios"]}
    r, p = postular(v2, "Prueba Salario", "4439800001", {**base2, su.Q_EXP_MKT: "5", su.Q_SUELDO_MKT: "35 mil"})
    paso = next(x for x in sproc.estado_pasos(p) if x["id"] == "solicitud")
    check(not r.get("descartado") and paso["estadoUnificado"] == "pendiente_revision" and "30,000" in paso["detalle"],
          f"expectativa $35,000 → «Requiere revisión» ({paso['detalle']})")
    r, p = postular(v2, "Prueba Exp", "4439800002", {**base2, su.Q_EXP_MKT: "2.5", su.Q_SUELDO_MKT: "28000"})
    paso = next(x for x in sproc.estado_pasos(p) if x["id"] == "solicitud")
    check(paso["estadoUnificado"] == "pendiente_revision", "2.5 años de experiencia → «Requiere revisión»")
    r, p = postular(v2, "Prueba Junior", "4439800003", {**base2, su.Q_EXP_MKT: "1", su.Q_SUELDO_MKT: "25000"})
    check(r.get("descartado") and not p.activa, "1 año de experiencia (< 2) → descartado")
    r, p = postular(v2, "Prueba Ok", "4439800004", {**base2, su.Q_EXP_MKT: "6", su.Q_SUELDO_MKT: "28000"})
    paso = next(x for x in sproc.estado_pasos(p) if x["id"] == "solicitud")
    check(not r.get("descartado") and paso["estadoUnificado"] in ("completada", "aprobada"), "6 años y $28,000 → la solicitud cumple")
    base3 = {c["pregunta"]: "Sí" for c in vacante_dict(v3)["criterios"]}
    r, p = postular(v3, "Prueba Maestría", "4439800005", {**base3, su.Q_MAESTRIA: "No"})
    check(r.get("descartado"), "Director sin maestría → descartado")
    # prefiltro por WhatsApp (Intendencia): experiencia < 6 meses → revisión
    maria = postulacion_de("María López Hernández")
    crit = pconv.criterios_de(v1, maria)
    exp = next((c for c in crit if c.get("revision_regla")), None)
    check(exp is not None and exp["chat"] == su.Q_EXP_INT, "el prefiltro por WhatsApp marca la pregunta de experiencia con su regla")
    e = {"respuestas": {c["id"]: {"respuesta": "Sí", "valor": "si"} for c in crit}}
    e["respuestas"][exp["id"]] = {"respuesta": "4 meses", "valor": "si"}
    check(pconv._evaluar(crit, e)[0] == "revision", "«4 meses» de experiencia → «Requiere revisión» (no descarta)")
    e["respuestas"][exp["id"]] = {"respuesta": "2 años", "valor": "si"}
    check(pconv._evaluar(crit, e)[0] == "cumple", "«2 años» → cumple")
    preguntas = [c["chat"] for c in crit]
    sede_c = next((c for c in crit if c.get("dato") == "terminal"), {})
    turno_c = next((c for c in crit if c.get("dato") == "turno"), {})
    check(sede_c.get("chat", "").startswith(su.Q_SEDE) and turno_c.get("chat", "").startswith(su.Q_TURNO)
          and sede_c.get("indispensable") and turno_c.get("indispensable"),
          f"prefiltro por WhatsApp: sede y turno como OPCIÓN y excluyentes → {preguntas[:2]}")

    # ===================================================== 9. Punto 4 (2026-10-16): sede, turno, vocabulario y continuidad
    print("\n--- 9. Sede y turno como opción, vocabulario por Cuenta y continuidad del prefiltro ---")
    from app.models import Ubicacion
    from app.routers.candidatos import _crear_candidato, crear_postulacion, procesar_prefiltro
    from app.services import ubicaciones as subic

    check(not any("dirección de demo" in (u.direccion or "") for u in db.query(Ubicacion).filter(Ubicacion.cuenta_id == cu.id))
          and db.query(Ubicacion).filter_by(cuenta_id=cu.id, nombre="Pátzcuaro").one().direccion == "Conocido, Pátzcuaro, Michoacán",
          "sedes sin «(dirección de demo)»: «Conocido, Pátzcuaro, Michoacán»")
    web1 = vacante_dict(v1)["criterios"]
    textos_web = [q["pregunta"] for q in web1]
    check(textos_web.count(su.Q_SEDE) == 1 and not any("terminal" in t.lower() for t in textos_web)
          and next(q for q in web1 if q["pregunta"] == su.Q_SEDE)["opciones"] == ["Morelia", "Pátzcuaro", "Zamora", "Ninguna de estas"],
          f"solicitud web: UNA sola pregunta de sede, con «sede» (no «terminal») y sus opciones → {textos_web}")
    q_t = next((q for q in web1 if q["pregunta"] == su.Q_TURNO), {})
    check(q_t.get("tipo") == "opcion" and q_t.get("opciones", [None])[0] == "De 6:00 a 14:00 (lunes a viernes)" and q_t.get("descarta"),
          f"solicitud web: el turno es una opción → {q_t.get('opciones')}")

    def conversa(nombre, tel, respuestas, web=None):
        c = _crear_candidato(db, cu.id, nombre, "Portal", False, telefono=tel)
        p = crear_postulacion(db, c, v1, cu.id, "web", consentimiento=True)
        if web:
            resp = [{"pregunta": q, "respuesta": a} for q, a in web.items()]
            p.analisis = {"respuestas_web": resp}
            subic.desde_respuestas_web(db, p, resp)
        db.commit()
        VENTANA.add("52" + tel)
        salida = []
        for t in respuestas:
            db.refresh(p)
            antes = len(p.mensajes)
            asyncio.run(procesar_prefiltro(db, p, t, "whatsapp"))
            db.refresh(p)
            salida.append([m.texto for m in p.mensajes[antes:] if m.rol == "assistant"])
        return p, salida

    p, sal = conversa("Pedro Pátzcuaro", "4439700001", ["Hola", "Vivo en Pátzcuaro", "sí", "de 8 a 4", "Terminé la secundaria", "2 años",
                                                         "9 mil", "Centro, Pátzcuaro", "15 minutos"])
    check(sal[1] == ["Tenemos sede en Pátzcuaro, ¿te queda bien?"], f"vive en Pátzcuaro → se le propone esa sede → {sal[1]}")
    check(sal[2][0].startswith(su.Q_TURNO) and p.ubicacion_id == db.query(Ubicacion).filter_by(cuenta_id=cu.id, nombre="Pátzcuaro").one().id,
          "«sí» elige Pátzcuaro y sigue con el turno")
    check(((p.analisis or {}).get("prefiltro_datos") or {}).get("turno_codigo") == "MAT-03", "«de 8 a 4» → turno MAT-03")
    check(sal[7] == ["Entendido. ¿Cuánto tiempo harías de tu casa a la sede Pátzcuaro?"] or sal[7][0].endswith("a la sede Pátzcuaro?"),
          f"el traslado nombra la sede elegida (sin «Conocido») → {sal[7]}")
    check(len(sal[-1]) == 1 and sal[-1][0].startswith("Listo, Pedro. Ahora te haré unas preguntas") and p.etapa == "Entrevista IA"
          and not any("revisará" in t or "te escribirá" in t for t in sal[-1]),
          f"cumple → avanza solo a la entrevista con UN mensaje, sin el cierre de RH → {sal[-1]}")
    p, sal = conversa("Nadia Ninguna", "4439700002", ["Hola", "Ninguna, vivo en Guadalajara"])
    check(not p.activa and "no continuaremos" in sal[-1][0], "«ninguna» sede (requisito excluyente) → descartado")
    p, sal = conversa("Toño Turno", "4439700003", ["Hola", "Zamora", "no puedo en ninguno"])
    check(not p.activa and "no continuaremos" in sal[-1][0], "ningún turno (excluyente) → descartado")
    p, sal = conversa("Wendy Web", "4439700004", ["Hola", "4 años", "Centro, Zamora", "30 minutos"],
                      web={su.Q_SEDE: "Zamora", su.Q_TURNO: "De 8:00 a 16:00 (lunes a viernes)", su.Q_ESTUDIOS: "Preparatoria",
                           "¿Cuál es tu expectativa de sueldo mensual?": "9000"})
    hechas = " ".join(t for x in sal for t in x)
    check(su.Q_SEDE not in hechas and su.Q_TURNO not in hechas and su.Q_ESTUDIOS not in hechas and "expectativa" not in hechas,
          "lo contestado en la web (sede, turno, estudios, sueldo) NO se vuelve a preguntar en WhatsApp")
    check(p.etapa == "Entrevista IA" and pconv.estado(p).get("resultado") == "cumple", "y avanza sola a la entrevista")
    # vocabulario: Fletes conserva «terminal»; las demás Cuentas dicen «sede»
    from app.services import opciones_vacante as ov

    check(ov.termino_ubicacion(v1) == "sede" and cu.termino_ubicacion == "sede", "UVAQ: «sede»")
    db.close()

print(f"\n{OK} verificaciones OK")
