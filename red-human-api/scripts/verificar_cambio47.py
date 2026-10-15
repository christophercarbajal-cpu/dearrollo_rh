"""Cambio 47 (2026-10-14): multi-ubicación, autoagenda y «misma cita» + demo Fletes México. Base desechable; Meta simulada.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambio47.py

1. Ubicaciones con dirección, mapa, indicaciones y horarios con cupo (API); una vacante con varias ubicaciones.
2. «¿En qué terminal te gustaría trabajar?»: en el formulario web y en el prefiltro de WhatsApp; se guarda en la ficha y
   el traslado se pregunta hacia ESA terminal. Filtro del tablero por ubicación.
3. Vigencia > 3 meses (licencia, apto médico): la fecha se extrae del chat y una vigencia corta descarta.
4. Autoagenda: el motor ofrece 3 días hábiles con cupo, nunca un horario lleno; al agendar resta 1 al cupo, crea UNA
   evaluación por actividad (cada evaluador recibe su aviso) y manda UNA confirmación con «Ese día harás: …».
5. Recordatorios 24 h y 2 h (una sola vez cada uno) y reagendar («no voy a poder ir»).
6. «Misma cita»: el candidato no avanza hasta tener TODOS los resultados; un «No avanzar» detiene la ruta para RH.
7. `seed_fletes_mexico.py` levanta la demo exacta.
"""

import asyncio
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_c47_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c47.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-c47"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.fechas import TZ_ORG  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Cliente, Cuenta, Evaluacion, NotificacionEnviada, Postulacion, ReservaCita, Ubicacion, Usuario, UsuarioCuenta, Vacante,
)
from app.routers.webhooks import procesar_entrante  # noqa: E402
from app.services import autoagenda  # noqa: E402
from app.services import motor_ruta  # noqa: E402
from app.services import preguntas_filtro as pfu  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import ubicaciones as subic  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

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


def _msg(tel, cuerpo):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": "Candidato"}, "wa_id": f"52{tel}"}],
        "messages": [{"from": f"52{tel}", "id": f"wamid.in.{tel}.{len(ENVIADOS)}.{id(cuerpo)}", **cuerpo}]}}]}]}


def escribe(tel, t):
    VENTANA.add("52" + tel)
    return asyncio.run(procesar_entrante(db, whatsapp.parsear_webhook(_msg(tel, {"type": "text", "text": {"body": t}}))))


def pulsa(tel, t):
    VENTANA.add("52" + tel)
    return asyncio.run(procesar_entrante(db, whatsapp.parsear_webhook(_msg(tel, {"type": "button", "button": {"text": t, "payload": t}}))))


LICENCIA = "Licencia federal tipo B con vigencia mayor a 3 meses"
APTO = "Apto médico con vigencia mayor a 3 meses"
HORARIOS = [{"dias": ["lunes", "martes", "miércoles", "jueves", "viernes"], "horas": ["9:00", "12:00"], "cupo": 8}]


def respuesta_para(pregunta: str, terminal: str = "Monterrey", licencia: str = "diciembre de 2030") -> str:
    q = pregunta.lower()
    if "licencia" in q and "vigente" in q:
        return licencia
    if "apto" in q and "vigente" in q:
        return "15/06/2031"
    if "terminal" in q:
        return terminal
    if "años tienes" in q:
        return "34"
    if "te interesa" in q:
        return "sí"
    if "colonia" in q:
        return "Colonia Centro, Monterrey"
    if "tiempo harías" in q or "minutos" in q:
        return "40 minutos"
    return "sí"


with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    admin.telefono = "5590000001"  # evaluador de la demo: recibe su aviso por WhatsApp
    cuenta = Cuenta(nombre="C47 interna", nombre_comercial="Transportes Prueba", estado="Activa", slug="c47-transportes")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    cliente = Cliente(cuenta_id=cuenta.id, nombre="Transportes Prueba", nombre_comercial="Transportes Prueba", estado="Activo")
    db.add(cliente)
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ===================================================== 1. Ubicaciones
    print("\n--- 1. Ubicaciones: horarios, cupo y mapa ---")
    r = client.post("/ubicaciones", json={"nombre": "Terminal Monterrey", "direccion": "Av. Industrial 100, Monterrey, Nuevo León",
                                          "indicaciones": "Pregunta en caseta por Recursos Humanos", "estado": "Nuevo León",
                                          "municipio": "Monterrey", "horarios": HORARIOS})
    check(r.status_code == 201, f"alta de ubicación ({r.status_code}: {r.text[:200]})")
    mty = r.json()
    check(mty["horarios"] == [{"dias": [0, 1, 2, 3, 4], "horas": ["09:00", "12:00"], "cupo": 8}],
          f"horarios normalizados (días, horas y cupo) → {mty['horarios']}")
    check(mty["mapa"].startswith("https://www.google.com/maps/search/") and "Monterrey" in mty["mapa"], "sin liga capturada, el mapa se arma con la dirección")
    check(mty["horariosTexto"] == "Lunes a viernes, 9:00 y 12:00 · 8 lugares por horario", f"texto de horarios → {mty['horariosTexto']}")
    r = client.post("/ubicaciones", json={"nombre": "Terminal Guadalajara", "direccion": "Periférico Sur 200, Guadalajara",
                                          "mapa": "https://maps.app.goo.gl/gdl", "estado": "Jalisco", "municipio": "Guadalajara",
                                          "horarios": HORARIOS})
    gdl = r.json()
    check(r.status_code == 201 and gdl["mapa"] == "https://maps.app.goo.gl/gdl", "la liga de Google Maps capturada se respeta")
    r = client.post("/ubicaciones", json={"nombre": "Terminal X", "horarios": [{"dias": ["lunes"], "horas": ["9:00"], "cupo": 0}]})
    check(r.status_code == 400 and "cupo" in r.json()["detail"], "un horario sin cupo se rechaza")
    r = client.patch(f"/ubicaciones/{gdl['id']}", json={"indicaciones": "Entrada por la puerta 3"})
    check(r.status_code == 200 and r.json()["indicaciones"] == "Entrada por la puerta 3", "edición")
    r = client.get(f"/ubicaciones/{mty['id']}/disponibilidad")
    check(r.status_code == 200 and len(r.json()["dias"]) == 3 and all(d["horarios"] for d in r.json()["dias"]),
          "disponibilidad: los próximos 3 días hábiles con sus horarios libres")
    check(all(datetime.fromisoformat(d["fecha"]).weekday() < 5 for d in r.json()["dias"]), "solo días hábiles")

    # ===================================================== proceso + vacante
    pasos = [
        {"id": "solicitud", "tipo": "solicitud_sin_cv", "etapa": "Prefiltro"},
        {"id": "prefiltro-whatsapp", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"},
        {"id": "prueba-manejo", "tipo": "tecnica", "nombre": "Prueba de manejo", "etapa": "Entrevista Humana", "tipo_agenda": "candidato",
         "misma_cita": True, "config": {"evaluador": {"tipo": "interno", "usuario_id": admin.id}}},
        {"id": "entrevista-jefe", "tipo": "entrevista_humana", "nombre": "Entrevista con jefe de operadores", "etapa": "Entrevista Humana",
         "misma_cita": True, "config": {"evaluador": {"tipo": "interno", "usuario_id": admin.id}}},
        {"id": "entrevista-sindicato", "tipo": "entrevista_humana", "nombre": "Entrevista con sindicato", "etapa": "Entrevista Humana",
         "misma_cita": True, "config": {"evaluador": {"tipo": "interno", "usuario_id": admin.id}}},
        {"id": "medico", "tipo": "medica", "nombre": "Examen médico interno y antidoping", "etapa": "Entrevista Humana", "tipo_agenda": "rh"},
        {"id": "contrato", "tipo": "carta_contrato", "nombre": "Contrato y firma", "etapa": "Contratación"},
    ]
    r = client.post("/procesos/plantillas", json={"nombre": "Operadores C47", "pasos": pasos})
    check(r.status_code == 201, f"proceso con autoagenda y misma cita ({r.status_code}: {r.text[:300]})")
    plantilla = r.json()
    guardados = {x["id"]: x for x in plantilla["pasos"]}
    check(all(guardados[x]["tipo_agenda"] == "candidato" and guardados[x]["misma_cita"] for x in ("prueba-manejo", "entrevista-jefe", "entrevista-sindicato")),
          "las 3 de la misma cita: las agenda el candidato (el bloque lo hereda de la primera)")
    check(guardados["medico"]["tipo_agenda"] == "rh" and not guardados["medico"].get("misma_cita"), "el médico lo agenda RH")
    r = client.post("/procesos/plantillas", json={"nombre": "Mal", "pasos": [
        {"id": "s", "tipo": "solicitud_sin_cv", "etapa": "Prefiltro"},
        {"id": "pw", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro", "misma_cita": True}]})
    check(r.status_code == 400 and "presencial" in r.json()["detail"], "«misma cita» solo en actividades presenciales")
    solo = sproc.normalizar_pasos([{"id": "a", "tipo": "entrevista_humana", "etapa": "Entrevista Humana", "misma_cita": True},
                                   {"id": "b", "tipo": "socioeconomica", "etapa": "Entrevista Humana"}], previos=[])
    check(not solo[0].get("misma_cita"), "una «misma cita» sin vecina presencial se quita")

    r = client.post("/vacantes", json={
        "titulo": "Operador de tractocamión", "area": "Operaciones", "seniority": "Semi-senior", "ubicacion_estado": "Nuevo León",
        "ubicacion_municipio": "Monterrey", "modalidad": "Presencial", "sueldo_desde": 25000, "sueldo_hasta": 32000,
        "sueldo_periodicidad": "mensual", "requisitos_indispensables": [LICENCIA, APTO], "descripcion": "Rutas nacionales.",
        "responsabilidades": ["Conducir en rutas nacionales"], "cliente_id": cliente.id, "proceso": {"plantilla_id": plantilla["id"]},
        "enfoque_entrevista": "operativo", "publicar": True, "plataformas": ["Portal"], "ubicaciones_ids": [mty["id"], gdl["id"]],
        "posiciones": 20, "generar_si_falta": False})
    check(r.status_code == 201, f"vacante con 2 ubicaciones ({r.status_code}: {r.text[:300]})")
    vac = r.json()
    check([u["nombre"] for u in vac["ubicaciones"]] == ["Terminal Monterrey", "Terminal Guadalajara"] and vac["posiciones"] == 20,
          "muchos-a-muchos: la vacante guarda sus ubicaciones (en orden) y sus posiciones")
    v = db.query(Vacante).filter_by(codigo=vac["id"]).one()
    terminal_web = next((c for c in vac["criterios"] if c["pregunta"] == subic.PREGUNTA_TERMINAL), None)
    check(terminal_web is not None and terminal_web["tipo"] == "opcion" and terminal_web["opciones"] == ["Terminal Monterrey", "Terminal Guadalajara"]
          and not terminal_web["descarta"], "formulario web: «¿En qué terminal te gustaría trabajar?» con las ubicaciones (no descarta)")
    web_si_no = [c["pregunta"] for c in vac["criterios"] if c.get("tipo") == "si_no"]
    check("¿Tu licencia federal tipo B tiene vigencia de más de 3 meses?" in web_si_no and
          "¿Tu apto médico tiene vigencia de más de 3 meses?" in web_si_no and not any(q.lower().startswith("cuéntame") for q in web_si_no),
          f"vigencias en el web: Sí/No directas ({web_si_no})")
    filtro = {q["requisito"]: q for q in vac["filtro"] if q.get("indispensable")}
    check(filtro[LICENCIA]["pregunta"] == "¿Hasta cuándo está vigente tu licencia federal tipo B?" and filtro[LICENCIA]["tipo"] == "fecha"
          and filtro[LICENCIA]["regla"] == "Descarta si vence en menos de 3 meses", "WhatsApp: pide la FECHA de vigencia y descarta si vence antes")
    r = client.patch(f"/vacantes/{vac['id']}", json={"ubicaciones_ids": [mty["id"], gdl["id"], 99999]})
    check(r.status_code == 400, "una ubicación ajena a la Cuenta se rechaza")

    # ===================================================== 3. vigencia
    print("\n--- 3. Vigencia > 3 meses ---")
    hoy = datetime.now(TZ_ORG).date()
    c_lic = {**filtro[LICENCIA], "dato": "vigencia", "criterio": LICENCIA, "chat": filtro[LICENCIA]["pregunta"]}
    for texto, esperado in (("diciembre de 2030", "si"), ("15/06/2031", "si"), ("en 1 mes", "no"), ("ya se me venció", "no"),
                            (f"{hoy.day}/{hoy.month}/{hoy.year}", "no"), ("le quedan 2 años", "si"), ("no sé", None)):
        check(pfu.clasificar(c_lic, texto)[0] == esperado, f"«{texto}» → {esperado}")

    # ===================================================== 2. terminal por WhatsApp + autoagenda
    print("\n--- 2 y 4. Terminal por WhatsApp y autoagenda ---")
    app.dependency_overrides.pop(cuenta_actual, None)

    def postular(nombre, tel, respuestas=None):
        import json

        datos = {"vacante": vac["slug"], "nombre": nombre, "telefono": tel, "consentimiento": "true"}
        if respuestas:
            datos["respuestas"] = json.dumps(respuestas)
        r = client.post("/candidatos/postular", data=datos)
        assert r.status_code == 201, r.text
        return db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()

    def contestar(tel, terminal="Monterrey", licencia="diciembre de 2030", limite=15):
        r = pulsa(tel, "Sí, empezar ahora")
        vistos = []
        for _ in range(limite):
            pregunta = r.get("respuesta") or ""
            vistos.append(pregunta)
            if "?" not in pregunta or r.get("clasificacion_final"):
                break
            r = escribe(tel, respuesta_para(pregunta, terminal, licencia))
        return r, vistos

    # días ofrecidos y un horario LLENO (8/8) antes de que el candidato agende
    u_mty = db.get(Ubicacion, mty["id"])
    dias = subic.dias_disponibles(db, u_mty)
    lleno = subic._inicio(dias[0], "09:00")
    for i in range(8):
        db.add(ReservaCita(cuenta_id=cuenta.id, ubicacion_id=u_mty.id, postulacion_id=900000 + i, inicio=lleno, pasos=[], estado="activa"))
    db.commit()

    p1 = postular("Juan Pérez López", "5547000001")
    desde = len(ENVIADOS)
    r, vistos = contestar("5547000001")
    db.refresh(p1)
    check(any(q.startswith(subic.PREGUNTA_TERMINAL) and "Monterrey y Guadalajara" in q for q in vistos),
          "el WhatsApp pregunta la terminal en una frase natural (sin lista numerada)")
    check(p1.ubicacion_id == mty["id"] and (p1.analisis.get("ubicacion") or {}).get("fuente") == "prefiltro por WhatsApp"
          and any(h.get("evento") == "ubicacion_elegida" for h in p1.historial), "la terminal elegida queda en la ficha (y en el historial)")
    check(any("¿Cuánto tiempo harías de tu casa a Terminal Monterrey?" == q for q in vistos), "el traslado se pregunta hacia ESA terminal")
    check(p1.etapa == "Entrevista Humana", f"prefiltro cumplido → Filtro humano ({p1.etapa})")
    oferta = next((t for t in textos("5547000001", desde) if "¿Qué día puedes venir?" in t), "")
    check(oferta.startswith("Juan, el siguiente paso es tu cita para: Prueba de manejo, Entrevista con jefe de operadores y Entrevista con "
                            "sindicato en Terminal Monterrey.") and all(subic.texto_dia(d) in oferta for d in dias),
          f"autoagenda: UNA oferta por todo el bloque con los 3 días hábiles con cupo → «{oferta}» "
          f"{'' if oferta else p1.analisis.get('autoagenda')}")
    paso = next(x for x in sproc.estado_pasos(p1) if x["id"] == "prueba-manejo")
    check(paso["estadoUnificadoTexto"] == "Esperando que el candidato agende" and paso["mismaCita"] == [
        "Prueba de manejo", "Entrevista con jefe de operadores", "Entrevista con sindicato"],
          f"la actividad espera al candidato (no es acción de RH) y muestra su bloque ({paso['estadoUnificadoTexto']})")
    check(sproc.accion_humana(p1) is None or sproc.accion_humana(p1)["paso"] == "medico", "no aparece «agendar» para RH en el bloque autoagendado")
    medico = next(x for x in sproc.estado_pasos(p1) if x["id"] == "medico")
    check(medico.get("pendiente_agendar") and medico["estadoUnificadoTexto"] == "Pendiente de agendar", "el médico (agenda RH) queda para RH")
    check(asyncio.run(autoagenda.revisar(db, p1)) is False, "la oferta no se repite")

    nombre_dia = autoagenda._DIAS[dias[0].weekday()]
    r = escribe("5547000001", f"el {nombre_dia}")
    check("9:00" not in r["respuesta"] and "12:00 p.m." in r["respuesta"], f"el horario LLENO (9:00, 8/8) no se ofrece → «{r['respuesta']}»")
    antes = subic.ocupados(db, u_mty, subic._inicio(dias[0], "12:00"))
    r = escribe("5547000001", "a las 12")
    db.refresh(p1)
    esperado = (f"Perfecto, te esperamos el {subic.texto_dia(dias[0])} a las 12:00 p.m. en Terminal Monterrey, Av. Industrial 100, "
                f"Monterrey, Nuevo León. Pregunta en caseta por Recursos Humanos. Mapa: {mty['mapa']}\n\nEse día harás: Prueba de manejo, "
                "Entrevista con jefe de operadores y Entrevista con sindicato.")
    check(r["respuesta"] == esperado, f"confirmación exacta → «{r['respuesta']}»")
    check(subic.ocupados(db, u_mty, subic._inicio(dias[0], "12:00")) == antes + 1, "agendar resta 1 al cupo")
    evs = db.query(Evaluacion).filter(Evaluacion.postulacion_id == p1.id, Evaluacion.estado != "cancelada").all()
    check(sorted(e.paso_id for e in evs) == ["entrevista-jefe", "entrevista-sindicato", "prueba-manejo"]
          and all(e.cita_fecha_hora == subic._inicio(dias[0], "12:00") for e in evs),
          "UNA evaluación por actividad, todas con la misma cita (cada una se evalúa por separado)")
    avisos_ev = db.query(NotificacionEnviada).filter(NotificacionEnviada.candidato_id == p1.candidato_id,
                                                     NotificacionEnviada.destinatario_tipo == "entrevistador",
                                                     NotificacionEnviada.canal == "whatsapp").all()
    check(len(avisos_ev) == 3 and all(a.enviado for a in avisos_ev), f"cada evaluador recibe SU aviso por WhatsApp (cascada del Cambio 46): {len(avisos_ev)}")
    al_candidato = [t for t in textos("5547000001", desde) if "Prueba de manejo" in t or "programada" in t.lower()]
    check(len([t for t in al_candidato if "quedó programada" in t]) == 0, "al candidato no le llegan 3 avisos sueltos: solo la confirmación")

    # otro candidato elige el MISMO horario cuando ya no queda lugar
    p_lleno = postular("Ana Ruiz", "5547000002", [{"pregunta": subic.PREGUNTA_TERMINAL, "respuesta": "Terminal Monterrey"}])
    check(p_lleno.ubicacion_id == mty["id"], "formulario web: la terminal elegida queda en la ficha")
    contestar("5547000002")
    db.refresh(p_lleno)
    check(p_lleno.etapa == "Entrevista Humana" and autoagenda.esperando(p_lleno), "el segundo candidato recibe su oferta")
    medio = subic._inicio(dias[0], "12:00")
    for i in range(8 - subic.ocupados(db, u_mty, medio) - 1):
        db.add(ReservaCita(cuenta_id=cuenta.id, ubicacion_id=u_mty.id, postulacion_id=910000 + i, inicio=medio, pasos=[], estado="activa"))
    db.commit()
    escribe("5547000002", f"el {nombre_dia}")
    db.add(ReservaCita(cuenta_id=cuenta.id, ubicacion_id=u_mty.id, postulacion_id=920000, inicio=medio, pasos=[], estado="activa"))
    db.commit()  # alguien tomó el último lugar mientras elegía
    r = escribe("5547000002", "a las 12")
    check(r["respuesta"].startswith("Ese día ya se llenó") or r["respuesta"].startswith("Ese horario se acaba de llenar"),
          f"horario lleno al confirmar → se le ofrece lo que queda ({r['respuesta'][:80]})")
    check(subic.ocupados(db, u_mty, medio) == 8, "el cupo nunca se sobrepasa (8/8)")

    # ===================================================== 5. recordatorios y reagendar
    print("\n--- 5. Recordatorios (24 h y 2 h) y reagendar ---")
    reserva = db.query(ReservaCita).filter_by(postulacion_id=p1.id, estado="activa").one()
    ahora = datetime.now(timezone.utc)
    reserva.creada_en = ahora - timedelta(days=2)
    reserva.inicio = ahora + timedelta(hours=20)
    db.commit()
    desde = len(ENVIADOS)
    r1 = asyncio.run(autoagenda.recordatorios(db))
    r2 = asyncio.run(autoagenda.recordatorios(db))
    recordatorio = [t for t in textos("5547000001", desde) if t.startswith("Hola, Juan. Te recordamos tu cita")]
    check(r1["24h"] == 1 and r2["24h"] == 0 and len(recordatorio) == 1 and "Ese día harás:" in recordatorio[0],
          f"recordatorio de 24 h: una sola vez, con el bloque ({r1}, {r2})")
    reserva.inicio = ahora + timedelta(hours=1, minutes=30)
    db.commit()
    r3 = asyncio.run(autoagenda.recordatorios(db))
    r4 = asyncio.run(autoagenda.recordatorios(db))
    check(r3["2h"] == 1 and r4["2h"] == 0 and any(t.startswith("Hola, Juan. Tu cita es hoy") for t in textos("5547000001", desde)),
          f"recordatorio de 2 h: una sola vez ({r3}, {r4})")
    main_py = (RAIZ / "app" / "main.py").read_text(encoding="utf-8")
    check('id="autoagenda_recordatorios"' in main_py, "el job de recordatorios está programado en el arranque")

    reserva.inicio = subic._inicio(dias[1], "09:00")
    for e in evs:
        e.cita_fecha_hora = reserva.inicio
    db.commit()
    r = escribe("5547000001", "oye no voy a poder ir, se me complicó")
    db.refresh(reserva)
    check(reserva.estado == "cancelada" and r.get("reagendada") and "¿Qué día puedes venir?" in r["respuesta"],
          f"«no voy a poder ir» → se libera el lugar y se ofrecen nuevas fechas ({r['respuesta'][:90]})")
    check(all(db.get(Evaluacion, e.id).estado == "cancelada" for e in evs), "las evaluaciones de esa cita se cancelan (cada evaluador recibe su aviso)")
    dias2 = [__import__("datetime").date.fromisoformat(x) for x in autoagenda.estado(db.get(Postulacion, p1.id))["dias"]]
    r = escribe("5547000001", f"el {autoagenda._DIAS[dias2[-1].weekday()]} a las 9")
    db.refresh(p1)
    check(r.get("agendada") and r["respuesta"].startswith(f"Perfecto, te esperamos el {subic.texto_dia(dias2[-1])} a las 9:00 a.m."),
          f"nueva cita con día y hora en un solo mensaje ({r['respuesta'][:80]})")
    evs = db.query(Evaluacion).filter(Evaluacion.postulacion_id == p1.id, Evaluacion.estado == "pendiente").all()
    check(len(evs) == 3, "tres evaluaciones nuevas para la nueva cita")

    # ===================================================== 6. misma cita: resultados
    print("\n--- 6. «Misma cita»: nadie avanza sin todos los resultados ---")
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    r = client.post(f"/procesos/postulaciones/{p1.codigo}/pasos/medico/omitir", json={"motivo": "Lo cubre el examen de la terminal"})
    check(r.status_code == 200, f"(se omite el médico para aislar el bloque) {r.status_code}")
    for paso_id, conclusion in (("prueba-manejo", "favorable"), ("entrevista-jefe", "avanzar")):
        r = client.post(f"/procesos/postulaciones/{p1.codigo}/pasos/{paso_id}/resultado", data={"conclusion": conclusion})
        check(r.status_code == 200, f"resultado de {paso_id} ({r.status_code}: {r.text[:150]})")
    db.refresh(p1)
    check(p1.etapa == "Entrevista Humana", "2 de 3 resultados → el candidato NO avanza")
    r = client.post(f"/procesos/postulaciones/{p1.codigo}/pasos/entrevista-sindicato/resultado", data={"conclusion": "avanzar"})
    db.refresh(p1)
    check(p1.etapa == "Contratación", f"los 3 con resultado favorable → avanza ({p1.etapa})")

    p3 = db.get(Postulacion, p_lleno.id)
    asyncio.run(autoagenda.confirmar(db, p3, u_mty, dias[1], {"hora": "12:00", "inicio": subic._inicio(dias[1], "12:00")},
                                     autoagenda.estado(p3), "whatsapp"))
    db.refresh(p3)
    client.post(f"/procesos/postulaciones/{p3.codigo}/pasos/medico/omitir", json={"motivo": "Lo cubre el examen de la terminal"})
    client.post(f"/procesos/postulaciones/{p3.codigo}/pasos/prueba-manejo/resultado", data={"conclusion": "favorable"})
    client.post(f"/procesos/postulaciones/{p3.codigo}/pasos/entrevista-jefe/resultado", data={"conclusion": "no_avanzar"})
    client.post(f"/procesos/postulaciones/{p3.codigo}/pasos/entrevista-sindicato/resultado", data={"conclusion": "avanzar"})
    db.refresh(p3)
    bloqueo = sproc.bloqueo_no_aprobada(p3, sproc.estado_pasos(p3))
    check(p3.etapa == "Entrevista Humana" and p3.activa and bloqueo and bloqueo["paso"] == "entrevista-jefe",
          "un «No avanzar» en el bloque detiene el avance y pasa a revisión de RH (nunca descarta)")

    # ===================================================== 2b. vigencia corta + filtro del tablero
    print("\n--- 2b. Vigencia corta y filtro del tablero ---")
    p_corta = postular("Luis Mora", "5547000003")
    r, vistos = contestar("5547000003", licencia="en 1 mes")
    db.refresh(p_corta)
    check(not p_corta.activa and p_corta.motivo_cierre == "descartado" and "no continuaremos" in r["respuesta"]
          and "licencia" not in r["respuesta"].lower(), "licencia que vence en 1 mes → descartado con el mensaje genérico")
    p_gdl = postular("Eva Soto", "5547000004", [{"pregunta": subic.PREGUNTA_TERMINAL, "respuesta": "Terminal Guadalajara"}])
    lista = client.get("/candidatos", params={"vacante": vac["id"], "ubicacion": gdl["id"]}).json()
    check([x["id"] for x in lista] == [p_gdl.codigo] and lista[0]["ubicacionNombre"] == "Terminal Guadalajara",
          "tablero: filtro por la ubicación elegida")

    # ===================================================== frontend
    print("\n--- Frontend ---")
    APP = RAIZ.parent / "red-human-app"
    editor = (APP / "components/dashboard/procesos/editor-proceso.tsx").read_text(encoding="utf-8")
    check("La agenda el candidato" in editor and "Misma cita" in editor and '"medica", "tecnica"' in editor,
          "editor de procesos: «Quién agenda» y «Misma cita» en las actividades presenciales")
    tablero = (APP / "app/dashboard/candidatos/page.tsx").read_text(encoding="utf-8")
    check("Todas las ubicaciones" in tablero and "filtroUbicacion" in tablero, "tablero: filtro por ubicación elegida")
    form = (APP / "components/dashboard/vacantes/formulario-contenido.tsx").read_text(encoding="utf-8")
    check("<CampoUbicaciones" in form and "ubicaciones_ids: c.ubicaciones_ids" in form, "formulario de vacante: varias ubicaciones")
    config = (APP / "app/dashboard/configuracion/page.tsx").read_text(encoding="utf-8")
    check("<SeccionUbicaciones />" in config, "Configuración → Ubicaciones (horarios, cupo y mapa)")

    # ===================================================== 7. seed Fletes México
    print("\n--- 7. Demo «Fletes México» ---")
    import seed_fletes_mexico as seed

    sys.argv = ["seed", "--aplicar"]
    db.close()
    check(seed.main() == 0, "seed_fletes_mexico.py --aplicar")
    db = SessionLocal()
    cu = db.query(Cuenta).filter_by(slug="fletes-mexico").one()
    terminales = db.query(Ubicacion).filter_by(cuenta_id=cu.id).order_by(Ubicacion.id).all()
    check([t.nombre for t in terminales] == ["Terminal Guadalajara", "Terminal Monterrey", "Terminal CDMX", "Terminal Querétaro",
                                             "Terminal Hermosillo"]
          and all(t.horarios_citas == [{"dias": [0, 1, 2, 3, 4], "horas": ["09:00", "12:00"], "cupo": 8}] for t in terminales),
          "5 terminales con lunes a viernes, 9:00 y 12:00, 8 lugares")
    fv = db.query(Vacante).filter_by(cuenta_id=cu.id, titulo="Operador de 5ª rueda").one()
    check(len(subic.de_vacante(db, fv)) == 5 and fv.posiciones == 20 and fv.sueldo_desde == 25000 and fv.sueldo_hasta == 32000
          and fv.modalidad == "Presencial" and fv.estado == "Publicada", "vacante: 5 ubicaciones, presencial, 20 posiciones, $25,000-$32,000")
    nombres = [x["nombre"] for x in fv.proceso["pasos"]]
    check(nombres == ["Solicitud web sin CV", "Prefiltro por WhatsApp", "Documentos", "Prueba de manejo", "Entrevista con jefe de operadores",
                      "Entrevista con sindicato", "Examen médico interno y antidoping", "Contrato y firma"], f"proceso «Fletes operadores» → {nombres}")
    docs = [d["nombre"] for d in fv.documentos_solicitados]
    check(any("Licencia" in d for d in docs) and "Apto médico" in docs, f"documentos: licencia (frente y reverso) y apto médico → {docs}")
    check(seed.main() == 0 and db.query(Ubicacion).filter_by(cuenta_id=cu.id).count() == 5, "el seed es idempotente")
    db.close()

print(f"\n🎉 Cambio 47: {OK} comprobaciones OK")
