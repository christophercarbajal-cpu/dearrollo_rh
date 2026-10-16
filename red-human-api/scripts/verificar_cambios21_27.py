"""Cambios 21-27 (2026-10-12). Base desechable; IA, Bot API y Meta SIMULADAS (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambios21_27.py

Bloque 1 (Cambios 22, 23, 24): el «/start» de la liga de una postulación responde SIEMPRE sobre ella con el texto de su
estado (Análisis de CV en proceso / terminado → prefiltro / En espera de decisión / Descartado); el Análisis de CV nunca
detiene la ruta (sin «mínimo 70») y sus brechas se vuelven preguntas del prefiltro (reprobar un indispensable descarta);
la ficha compara CV vs respuesta por indispensable; en la ruta corporativa no se pregunta colonia ni transporte, el
sueldo es la expectativa (> 20 % arriba → Puntos por validar) y la entrevista con avatar pregunta «¿La quieres hacer
ahora?» (sí → liga; no → 3 días).

Bloque 2 (Cambios 21 y 26): «Indicaciones para el entrevistador» en la cita (precargadas con la última cita de la vacante);
el aviso al entrevistador tiene texto fijo, sus indicaciones y la liga de su evaluación (una por cita, la misma al
editar: «Tu cita cambió»), nunca las indicaciones ni los adjuntos del candidato; su página no muestra las instrucciones
del candidato, deduplica requisitos, muestra «Lo que ya respondió en el prefiltro» y el guion no repite el prefiltro.

Bloque 3 (Cambio 25): «Documentos y fotos que se piden» en la vacante (la IA los toma de la descripción); con elementos la
ruta suma «Documentos» después del prefiltro de WhatsApp; el agente manda la liga una vez y ataja las fotos del chat con
«Súbela aquí, por favor: [liga]»; cada archivo es su propio registro (fotos del auto = 4; HEIC → JPG); RH aprueba, pide
corrección (reemplazo por WhatsApp) o marca excepción; estados Pendiente / Por revisar / Corrección pedida / Documentos
aprobados / Excepción.

Bloque 4 (Cambios 25 y 27): los formularios web ya no muestran «Parcial» ni «más de 90 min = alerta» (el límite de
traslado sale solo del indispensable); la plantilla de ruta define el enfoque (Masivos = Operativo) sin importar el
seniority; el selector de vacantes del tablero muestra «[folio] [puesto] [ubicación]» y abre con «Todas las vacantes».
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
_dir = _temporal.directorio("rh_c21_27_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c21.db").replace(chr(92), "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "RESEND_API_KEY",
          "TELEGRAM_WEBHOOK_SECRET", "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:PRUEBA-token-falso"
os.environ["TELEGRAM_BOT_USERNAME"] = "RedHumanPruebaBot"
os.environ["ADMIN_PASSWORD"] = "prueba-c21"
os.environ["SEMBRAR_DEMO"] = "true"

from _ruta_prueba import fijar_ruta  # noqa: E402  (Cambio 36: ruta inicial de vacantes sembradas)
from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Archivo, Cuenta, Postulacion, Vacante  # noqa: E402
from app.routers import webhooks_telegram  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion, procesar_prefiltro  # noqa: E402
from app.serial import postulacion_dict  # noqa: E402
from app.services import agenda_chat, canal_proceso, ia, telegram  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
LLAMADAS = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _llamar_falso(metodo, json=None, data=None, files=None):
    LLAMADAS.append((metodo, json or {}))
    return {"ok": True, "result": {"message_id": len(LLAMADAS)}}


telegram.llamar = _llamar_falso
UID = iter(range(900000, 999999))


def start(chat, payload):
    uid = next(UID)
    return {"update_id": uid, "message": {"message_id": uid, "chat": {"id": chat, "type": "private"},
                                          "from": {"id": chat, "first_name": "Tester"}, "text": f"/start {payload}".strip()}}


def enviados(chat, desde=0):
    return [j.get("text", "") for (m, j) in LLAMADAS[desde:] if m == "sendMessage" and str(j.get("chat_id")) == str(chat)]


INDISP = ["Licencia de manejo tipo B", "Experiencia en ventas de campo"]
FICHA = ia.FichaGuion(titulo="Ejecutivo de ventas", requisitos_indispensables=INDISP, sueldo="De $10,000 a $12,000 MXN mensuales",
                      ubicacion="Monterrey, Nuevo León")
WEB = [{"pregunta": "¿Tienes licencia de manejo tipo B?", "tipo": "si_no", "valida": INDISP[0], "respuesta_esperada": "Sí",
        "descarta": True, "opciones": ["Sí", "No"]}]
RUTA_CORP = {"pasos": [{"id": "sw", "tipo": "solicitud_web", "etapa": "Prefiltro", "con_cv": True},
                       {"id": "analisis_cv", "tipo": "analisis_cv", "etapa": "Prefiltro"},
                       {"id": "pwa", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"},
                       {"id": "entrevista_red_human", "tipo": "entrevista_agente", "etapa": "Entrevista IA"},
                       {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"}]}

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    cuenta = Cuenta(nombre="C21", nombre_comercial="Ventas MX", estado="Activa", slug="c21", canal_mensajeria="telegram")
    db.add(cuenta)
    db.flush()
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).first()
    v.cuenta_id, v.titulo = cuenta.id, "Ejecutivo de ventas"
    v.sueldo_desde, v.sueldo_hasta, v.sueldo_periodicidad, v.sueldo = 10000, 12000, "mensual", "De $10,000 a $12,000 MXN mensuales"
    v.preguntas_filtro = WEB
    v.preguntas_filtro_whatsapp = ia.asegurar_prefiltros([], [], FICHA, hay_web=False, hay_whatsapp=True)[1]
    v.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, RUTA_CORP)
    db.commit()

    def liga(p):
        tok = telegram.asegurar_token(p)
        db.commit()
        return f"p_{tok}"

    def postular(nombre, tel, analisis=None, cv=False):
        c = _crear_candidato(db, cuenta.id, nombre, "Portal", False, telefono=tel)
        p = crear_postulacion(db, c, v, cuenta.id, "web", consentimiento=True)
        p.analisis = {"respuestas_web": [{"pregunta": WEB[0]["pregunta"], "respuesta": "Sí"}], **(analisis or {})}
        if analisis:
            p.score = 40
        if cv:
            c.archivos.append(Archivo(tipo="cv", nombre="cv.pdf", ruta=str(Path(_dir) / "cv.pdf"), mime="application/pdf", tamano=10,
                                      estado="recibido", subido_por="candidato"))
            c.cv_datos = {"nombre": nombre, "experiencia_resumen": "Ventas"}
        db.commit()
        return p

    ANALISIS = {"ia": True, "requisitos_cumplidos": ["Experiencia en ventas de campo"],
                "brechas": ["No acredita licencia de manejo tipo B", "Sin evidencia de inglés intermedio"], "fortalezas_cv": ["Ventas"]}

    # ================= Cambio 22 · Análisis de CV continuo =================
    print("\n--- Cambio 22 · el Análisis de CV no detiene la ruta ---")
    p1 = postular("Luis Treviño", "8111000001", ANALISIS)
    paso = next(x for x in sproc.estado_pasos(p1) if x["tipo"] == "analisis_cv")
    check(paso["estado"] == "completada" and paso["cumpleRegla"] and "mínimo" not in paso["detalle"],
          f"score 40 sin «mínimo 70»: la actividad se cumple y no pide decisión de RH ({paso['detalle']})")
    check(sproc.detenido_por_no_aprobada(p1) is None and sproc.bloqueo_no_aprobada(p1, sproc.estado_pasos(p1)) is None, "la ruta no se detiene por el CV")
    check(sproc.es_corporativa(p1), "la ruta con Análisis de CV es corporativa")

    print("\n--- Cambios 22/24 · criterios del prefiltro corporativo ---")
    cs = pconv.criterios_de(v, p1)
    datos = [c.get("dato") for c in cs if c.get("dato")]
    check("zona" not in datos and "transporte" not in datos, f"ruta corporativa: sin colonia ni transporte ({datos})")
    # 2026-10-16 (bug del tester): la vacante publica su sueldo → la expectativa ya no se pregunta
    check(not any(c.get("dato") == "sueldo" for c in cs), "sueldo publicado: el prefiltro corporativo NO pregunta la expectativa")
    # Cambio 42: el Análisis de CV ya no agrega preguntas (omisión = «No lo menciona el CV»; contradicción → Puntos por validar)
    check(not any(c.get("por_brecha") or c.get("brecha") for c in cs), "Cambio 42: las brechas del CV ya NO se vuelven preguntas del prefiltro")
    lic = next(c for c in cs if c["indispensable"] and "licencia" in c["criterio"].lower())
    check(cs.index(lic) < min((cs.index(c) for c in cs if c.get("dato") and not c.get("indispensable")), default=len(cs)),
          "los indispensables van antes de los datos")
    check(all("?" in c["chat"] and not c["chat"].startswith("¿Cumples con") for c in cs), "todas en lenguaje natural")
    check(pconv.monto_de("20 mil") == 20000 and pconv.monto_de("$15,500 al mes") == 15500 and pconv.monto_de("18k") == 18000
          and pconv.monto_de("lo que sea") is None, "la expectativa se lee en cifras")

    # ================= Cambio 23 · /start por estado =================
    print("\n--- Cambio 23 · /start de la liga de la postulación ---")
    CHAT = 5550001
    telegram.guardar_contacto(db, str(CHAT), "8111000001", "Luis")
    otra = postular("Luis Treviño", "8111000001", ANALISIS)  # otra postulación ABIERTA de la misma persona
    db.commit()
    n = len(LLAMADAS)
    r = asyncio.run(webhooks_telegram.procesar_update(start(CHAT, liga(p1))))
    db.commit()
    txt = enviados(CHAT, n)
    check(r.get("estadoStart") == "cv_terminado" and txt and txt[0].startswith("Hola, Luis. Ya terminamos de analizar tu CV"),
          f"«Análisis de CV terminado»: {txt[:1]}")
    check(len(txt) >= 2 and "Soy Red Human, el agente virtual de" in txt[1], "…y en seguida empieza el prefiltro (saludo + primera pregunta)")
    db.refresh(p1)
    check(r.get("postulacion") in (None, p1.codigo) and pconv.estado(p1).get("pendiente"),
          "responde sobre la postulación de la liga aunque haya otra abierta")
    check(not pconv.estado(otra).get("pendiente"), "la otra postulación no se toca")

    def responder(p, texto):
        db.refresh(p)
        return asyncio.run(procesar_prefiltro(db, p, texto, "whatsapp"))

    vistos = []
    for _ in range(8):
        db.refresh(p1)
        e = pconv.estado(p1)
        if e.get("resultado") or not e.get("pendiente"):
            break
        c = next(x for x in pconv.criterios_de(v, p1) if x["id"] == e["pendiente"])
        vistos.append(c["id"])
        responder(p1, "Sí")
    db.refresh(p1)
    # Cambio 42: «No acredita licencia» es OMISIÓN del CV → «No lo menciona el CV»; el «Sí» del formulario no se vuelve a
    # preguntar y la ruta no se detiene
    check(lic["id"] not in vistos and pconv.estado(p1).get("resultado") == "cumple" and p1.activa,
          "una omisión del CV no se pregunta en el chat ni frena el prefiltro")
    fila = next(f for f in pconv.comparativo_indispensables(p1) if "licencia" in f["requisito"].lower())
    check(fila["cv"] == "No lo menciona el CV" and not fila["inconsistencia"], f"la ficha dice «No lo menciona el CV» ({fila['cv']})")
    p1.estado = "no_cumple"
    p1.cerrar("descartado")  # RH lo descarta (mismo cierre que POST /decision)
    db.commit()
    db.refresh(p1)
    n = len(LLAMADAS)
    asyncio.run(webhooks_telegram.procesar_update(start(CHAT, liga(p1))))
    txt = enviados(CHAT, n)
    check(txt and "Gracias por tu interés en" in txt[0] and "no continuaremos" in txt[0], f"«Descartado»: {txt[:1]}")

    # CV en proceso
    p3 = postular("Ana Garza", "8111000003", cv=True)
    CHAT3 = 5550003
    telegram.guardar_contacto(db, str(CHAT3), "8111000003", "Ana")
    db.commit()
    check(canal_proceso.estado_start(p3) == "cv_en_proceso", "CV subido sin análisis → «Análisis de CV en proceso»")
    paso3 = next(x for x in sproc.estado_pasos(p3) if x["tipo"] == "analisis_cv")
    check(paso3["espera"] == "Análisis de CV en proceso", "la actividad lo dice igual")
    n = len(LLAMADAS)
    asyncio.run(webhooks_telegram.procesar_update(start(CHAT3, liga(p3))))
    txt = enviados(CHAT3, n)
    check(len(txt) == 1 and "Estamos analizando tu CV" in txt[0],
          f"texto exacto y no arranca el prefiltro: {txt}")
    # En espera de decisión
    p3.analisis = {**(p3.analisis or {}), **ANALISIS}
    p3.score, p3.estado = 70, "revision"
    db.commit()
    n = len(LLAMADAS)
    asyncio.run(webhooks_telegram.procesar_update(start(CHAT3, liga(p3))))
    txt = enviados(CHAT3, n)
    check(len(txt) == 1 and "en espera de decisión" in txt[0], f"«En espera de decisión»: {txt}")

    # ================= Cambio 24 · expectativa, comparativo y «¿La quieres hacer ahora?» =================
    print("\n--- Cambios 22/24 · expectativa, comparativo e invitación ---")
    p4 = postular("Rosa Salinas", "8111000004", ANALISIS, cv=True)
    CHAT4 = 5550004
    telegram.guardar_contacto(db, str(CHAT4), "8111000004", "Rosa")
    db.commit()
    respuestas = []
    for _ in range(10):
        db.refresh(p4)
        e = pconv.estado(p4)
        if e.get("resultado"):
            break
        pend = next((x for x in pconv.criterios_de(v, p4) if x["id"] == e.get("pendiente")), None)
        texto = "Hola" if pend is None else ("Unos 20 mil" if pend.get("dato") == "sueldo" else
                                             "40 minutos" if pend.get("dato") == "traslado" else "Sí")
        respuestas.append(responder(p4, texto))
    db.refresh(p4)
    d = (p4.analisis or {}).get("prefiltro_datos") or {}
    # 2026-10-16 (bug del tester): con sueldo publicado no hay expectativa que comparar ni «Requiere revisión» por ella
    pts = (p4.analisis or {}).get("puntos_validar_prefiltro") or []
    check(d.get("sueldo_expectativa") is None and not any(x.startswith("Expectativa de sueldo") for x in pts),
          f"sueldo publicado: sin expectativa registrada ni en Puntos por validar ({pts})")
    check(p4.activa and pconv.estado(p4).get("resultado") == "cumple" and p4.estado != "revision",
          "la expectativa ya no congela: el prefiltro termina «cumple»")
    ficha = postulacion_dict(p4, detalle=True)
    comp = {f["requisito"]: f for f in ficha["comparativoIndispensables"]}
    lic = next(f for k, f in comp.items() if "icencia" in k)
    check(lic["cv"] == "No lo menciona el CV" and lic["cumple"] is True and not lic["inconsistencia"],
          "Cambio 42: «No acredita licencia» es omisión del CV → «No lo menciona el CV» (no es inconsistencia)")
    ven = next(f for k, f in comp.items() if "ventas" in k.lower())
    check(ven["cv"] == "Acreditado" and ven["cumple"] is True and not ven["inconsistencia"], "experiencia: acreditada y confirmada")
    check(not (ficha.get("prefiltroDatos") or {}).get("sueldoExpectativa"), "la ficha no muestra una expectativa que no se pidió")
    # la invitación con la liga: otro candidato con una expectativa dentro del rango
    p4 = postular("Rosa Salinas", "8111000014", ANALISIS, cv=True)
    telegram.guardar_contacto(db, "5550014", "8111000014", "Rosa")
    db.commit()
    respuestas = []
    for _ in range(10):
        db.refresh(p4)
        e = pconv.estado(p4)
        if e.get("resultado"):
            break
        pend = next((x for x in pconv.criterios_de(v, p4) if x["id"] == e.get("pendiente")), None)
        texto = "Hola" if pend is None else ("Unos 11 mil" if pend.get("dato") == "sueldo" else
                                             "40 minutos" if pend.get("dato") == "traslado" else "Sí")
        respuestas.append(responder(p4, texto))
    db.refresh(p4)
    check(pconv.estado(p4).get("resultado") == "cumple", "expectativa dentro del rango → cumple")
    ultimo = respuestas[-1]["respuesta"]
    # Cambio 42: la siguiente actividad inicia SOLA — la liga de la sala sale en el mismo turno (sin proponer horario)
    check("/entrevista/" in ultimo and "¿Te confirmo" not in ultimo,
          f"entrevista con avatar: inicia sola con su liga (sin proponer horario): {ultimo!r}")
    r = responder(p4, "Ahorita no puedo")
    db.refresh(p4)
    check("Tienes 3 días para hacerla" in r["respuesta"] and "/entrevista/" in r["respuesta"] and p4.videollamada_liga,
          f"«no» → la liga con 3 días: {r['respuesta']!r}")
    p5 = postular("Iván Pérez", "8111000005", ANALISIS)
    agenda_chat.invitacion(p5)
    p5.prefiltro_completo, p5.estado, p5.etapa = True, "cumple", "Entrevista IA"
    db.commit()
    check(agenda_chat.decidir(p5, "Sí, de una vez") == {"accion": "ahora"} and agenda_chat.decidir(p5, "mejor luego")["accion"] == "plazo"
          and agenda_chat.decidir(p5, "¿cuánto dura?")["accion"] == "responder", "sí → ahora · no → plazo · otra cosa → repregunta")

    # ================= Bloque 2 (Cambios 21 y 26) · citas y avisos al entrevistador =================
    print("\n--- Cambios 21/26 · aviso al entrevistador ---")
    import app.services.correo as correo_srv
    import app.services.notificaciones as notif
    import app.services.whatsapp as wa_srv
    from app.deps import cuenta_actual, usuario_actual, usuario_decisor
    from app.models import Evaluacion, Usuario, UsuarioCuenta
    from app.services import evaluaciones as sev

    ENV = []

    async def _wa(tel, texto, *a, **k):
        ENV.append(("whatsapp", str(tel), texto))
        return {"enviado": True, "proveedor": "meta", "detalle": "ok"}

    async def _wab(tel, texto, boton, url, *a, **k):
        return await _wa(tel, f"{texto} {url}")

    async def _correo(dest, asunto, html, adjuntos=None, *a, **k):
        ENV.append(("correo", str(dest), asunto, html))
        return {"enviado": True, "proveedor": "resend", "detalle": "ok"}

    wa_srv.enviar_mensaje = notif.enviar_mensaje = _wa
    wa_srv.enviar_con_boton = _wab
    correo_srv.enviar_correo = notif.enviar_correo = _correo
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta2 = Cuenta(nombre="C21 citas", nombre_comercial="Citas MX", estado="Activa", slug="c21-citas")
    db.add(cuenta2)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta2.id))
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta2
    v2 = db.query(Vacante).filter(Vacante.estado == "Publicada", Vacante.id != v.id).order_by(Vacante.id).first()
    v2.cuenta_id, v2.cliente_id, v2.titulo, v2.responsable_id = cuenta2.id, None, "Montacarguista", admin.id
    v2.requisitos = "Licencia de montacargas · Experiencia en almacén · licencia de montacargas."
    v2.requisitos_deseables = ["Experiencia en almacén", "Disponibilidad para rolar turnos"]
    v2.preguntas_filtro = [{"pregunta": "¿Tienes licencia de montacargas?", "tipo": "si_no", "valida": "Licencia de montacargas",
                            "respuesta_esperada": "Sí", "descarta": True, "opciones": ["Sí", "No"]}]
    v2.preguntas_filtro_whatsapp = []
    v2.proceso = sproc.proceso_para_vacante(db, cuenta2.id, {}, {"pasos": [
        {"id": "sw", "tipo": "solicitud_web", "etapa": "Prefiltro"}, {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"}]})
    db.commit()
    c2 = _crear_candidato(db, cuenta2.id, "Jorge Medina", "Portal", False, telefono="5512340001")
    q = crear_postulacion(db, c2, v2, cuenta2.id, "web", consentimiento=True)
    q.analisis = {"respuestas_web": [{"pregunta": "¿Tienes licencia de montacargas?", "respuesta": "Sí"}],
                  "respuestas_prefiltro": [{"criterio": "Sueldo", "pregunta": "El sueldo es desde $10,000. ¿Te parece bien?",
                                            "respuesta": "Sí, está bien"}]}
    db.commit()
    CITA2 = {"fecha": "2026-12-15", "hora": "10:00", "modalidad": "Presencial", "direccion": "Av. Uno 100, Tultitlán",
             "instrucciones": "Trae tu INE", "indicaciones_entrevistador": "Valida que sepa revisar el montacargas"}
    EVAL = {"tipo": "externo", "nombre": "Marta Ruiz", "correo": "marta@citas.mx", "whatsapp": "5512349999"}
    r = client.post(f"/evaluaciones/postulaciones/{q.codigo}", json={"tipo": "entrevista_humana", "forma": "asignada", "evaluador": EVAL, "cita": CITA2})
    check(r.status_code == 201, f"cita creada ({r.status_code} {r.text[:200]})")
    db.expire_all()
    ev = db.query(Evaluacion).filter_by(postulacion_id=q.id).order_by(Evaluacion.id.desc()).first()
    liga_ev = sev.liga_evaluador(ev)
    token_antes = ev.token_evaluador
    check(ev.indicaciones_entrevistador == "Valida que sepa revisar el montacargas", "se guardan las «Indicaciones para el entrevistador»")
    al_ent = [x for x in ENV if x[0] == "whatsapp" and x[1].endswith("5512349999")]
    check(len(al_ent) == 1, f"UN aviso al entrevistador ({len(al_ent)})")
    t = al_ent[0][2]
    check(t.startswith("Hola Marta, tienes una entrevista con Jorge Medina para Montacarguista") and "📅 Fecha:" in t
          and "📍 Lugar: Av. Uno 100" in t and "📝 Indicaciones para ti: Valida que sepa revisar el montacargas" in t and liga_ev in t,
          f"texto fijo con sus indicaciones y su liga: {t!r}")
    check("Trae tu INE" not in t and "archivo" not in t and "Pregunta por" not in t,
          "sin las indicaciones ni los adjuntos del candidato")
    correo_ent = [x for x in ENV if x[0] == "correo" and x[1] == "marta@citas.mx"]
    check(correo_ent and "Trae tu INE" not in correo_ent[0][3] and "Indicaciones para ti" in correo_ent[0][3],
          "el correo al entrevistador tampoco lleva lo del candidato")
    al_cand = [x for x in ENV if x[0] == "whatsapp" and x[1].endswith("5512340001")]
    check(al_cand and "Trae tu INE" in al_cand[0][2] and "Valida que sepa" not in al_cand[0][2],
          "el candidato recibe SUS indicaciones y nunca las del entrevistador")

    print("\n--- Cambio 21 · editar la cita: «Tu cita cambió» con la misma liga ---")
    n = len(ENV)
    r = client.patch(f"/evaluaciones/{ev.codigo}", json={"cita": {**CITA2, "indicaciones_entrevistador": "Valida montacargas y turnos"}})
    check(r.status_code == 200, f"editar solo sus indicaciones ({r.status_code})")
    nuevos = ENV[n:]
    check(not any(x[1].endswith("5512340001") for x in nuevos), "si solo cambian sus indicaciones, el candidato no recibe nada")
    cambio = [x for x in nuevos if x[0] == "whatsapp" and x[1].endswith("5512349999")]
    check(len(cambio) == 1 and cambio[0][2].startswith("Hola Marta, tu cita cambió") and liga_ev in cambio[0][2]
          and "Valida montacargas y turnos" in cambio[0][2], "el entrevistador recibe «Tu cita cambió» con la MISMA liga")
    n = len(ENV)
    r = client.patch(f"/evaluaciones/{ev.codigo}", json={"cita": {**CITA2, "hora": "12:00", "indicaciones_entrevistador": "Valida montacargas y turnos"}})
    db.expire_all()
    ev = db.get(Evaluacion, ev.id)
    nuevos = ENV[n:]
    ent = [x for x in nuevos if x[0] == "whatsapp" and x[1].endswith("5512349999")]
    cand = [x for x in nuevos if x[0] == "whatsapp" and x[1].endswith("5512340001")]
    check(ev.token_evaluador == token_antes and len(ent) == 1 and "tu cita cambió" in ent[0][2] and liga_ev in ent[0][2]
          and cand and "tu cita cambió" in cand[0][2], "cambiar la hora: «Tu cita cambió» a ambos y una sola liga de evaluación por cita")
    pre = client.get(f"/evaluaciones/postulaciones/{q.codigo}/ultima-cita?tipo=entrevista_humana").json()
    check(pre["cita"]["indicacionesEntrevistador"] == "Valida montacargas y turnos" and pre["cita"]["instrucciones"] == "Trae tu INE",
          "la siguiente cita de la vacante precarga las indicaciones para el entrevistador")

    print("\n--- Cambio 26 · página del entrevistador ---")
    pub = client.get(f"/evaluaciones/publica/{ev.token_evaluador}").json()
    check(pub["evaluacion"]["instrucciones"] == "" and pub["evaluacion"]["indicacionesEntrevistador"] == "Valida montacargas y turnos",
          "sin las instrucciones del candidato; solo «Indicaciones para el entrevistador»")
    reqs = pub["expediente"]["vacante"]["requisitosLista"]
    check(reqs == ["Licencia de montacargas", "Experiencia en almacén", "Disponibilidad para rolar turnos"], f"requisitos sin duplicados: {reqs}")
    pf = pub["expediente"]["prefiltro"]
    check(any("licencia" in x["pregunta"].lower() and x["respuesta"] == "Sí" for x in pf) and any("sueldo" in x["pregunta"].lower() for x in pf),
          f"«Lo que ya respondió en el prefiltro»: {[x['pregunta'] for x in pf]}")
    qs = ["¿Cuánto ganabas en tu último trabajo?", "¿Tienes licencia de montacargas vigente?", "¿Cómo revisas un montacargas antes de usarlo?",
          "¿Hasta qué año estudiaste?", "¿Cuánto tiempo haces de tu casa a la planta?"]
    check(sev.guion_sin_prefiltro(qs, q) == ["¿Cómo revisas un montacargas antes de usarlo?"],
          "el guion sugerido ya no pregunta lo del prefiltro (sueldo, licencia ya respondida, escolaridad, traslado)")
    check(not any(sev.guion_sin_prefiltro([x], q) != [x] for x in (pub["evaluacion"].get("guion") or {}).get("preguntas") or []),
          "el guion que ve el entrevistador ya viene filtrado")
    app.dependency_overrides.clear()

    # ================= Bloque 3 (Cambio 25) · módulo de documentos =================
    print("\n--- Cambio 25 · documentos de la vacante ---")
    import io

    from PIL import Image

    from app.models import ArchivoDocumento, Documento
    from app.routers import webhooks as wh
    from app.services import documentos_vacante as sdv
    from app.services.mensajeria import en_conversacion

    wh.enviar_mensaje = _wa
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    det = ia.documentos_de_descripcion("Chofer con auto propio. Requisitos: licencia de conducir vigente e INE.")
    check([d["nombre"] for d in det] == ["INE (frente y reverso)", "Licencia de conducir", "Fotos del auto"] and det[2] == {"id": "fotos_auto", "nombre": "Fotos del auto", "cantidad": 4, "foto": True},
          f"la IA llena «Documentos y fotos que se piden» con lo que menciona la descripción: {det}")
    check(ia.documentos_de_descripcion("Ayudante general para almacén, turno matutino.") == [], "sin menciones no inventa documentos")
    check(sdv.de_vacante(type("V", (), {"documentos_solicitados": ["INE", {"nombre": "ine"}, {"nombre": "Fotos del auto"}]})())
          == [{"id": "ine", "nombre": "INE (frente y reverso)", "cantidad": 1, "foto": False}, {"id": "fotos_auto", "nombre": "Fotos del auto", "cantidad": 4, "foto": True}],
          "sin duplicados; las fotos del auto exigen 4")

    cuenta3 = Cuenta(nombre="C25 docs", nombre_comercial="Docs MX", estado="Activa", slug="c25-docs")
    db.add(cuenta3)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta3.id))
    app.dependency_overrides[cuenta_actual] = lambda: cuenta3
    v3 = db.query(Vacante).filter(Vacante.estado == "Publicada", Vacante.id.notin_([v.id, v2.id])).order_by(Vacante.id).first()
    v3.cuenta_id, v3.cliente_id, v3.titulo, v3.responsable_id = cuenta3.id, None, "Repartidor con auto", admin.id
    v3.preguntas_filtro, v3.preguntas_filtro_whatsapp = [], []
    # Cambio 36 («El proceso manda»): la vacante ya no inyecta «Documentos»; la RUTA trae la actividad y ésta recoge los
    # documentos que pide la vacante
    RUTA_DOCS = {"pasos": [{"id": "sw", "tipo": "solicitud_web", "etapa": "Prefiltro"},
                           {"id": "pwa", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"},
                           {"id": "docs", "tipo": "documentos", "etapa": "Prefiltro", "depende_de": ["pwa"]},
                           {"id": "ewa", "tipo": "entrevista_whatsapp", "etapa": "Entrevista IA"}]}
    v3.proceso = sproc.proceso_para_vacante(db, cuenta3.id, {}, RUTA_DOCS)
    db.commit()
    r = client.patch(f"/vacantes/{v3.codigo}", json={"documentos_solicitados": [{"nombre": "INE"}, {"nombre": "Fotos del auto"}]})
    check(r.status_code == 200 and [d["nombre"] for d in r.json()["documentosSolicitados"]] == ["INE (frente y reverso)", "Fotos del auto"],
          f"la vacante guarda «Documentos y fotos que se piden» ({r.status_code} {r.text[:160]})")
    db.expire_all()

    def nueva_postulacion(nombre, tel, vac):
        c = _crear_candidato(db, cuenta3.id, nombre, "Portal", False, telefono=tel)
        p = crear_postulacion(db, c, vac, cuenta3.id, "web", consentimiento=True)
        db.commit()
        return p

    q3 = nueva_postulacion("Pedro Ramos", "5512350001", v3)
    pasos = [x["id"] for x in q3.proceso["pasos"]]
    paso_docs = next(x for x in q3.proceso["pasos"] if x["tipo"] == "documentos")
    check(pasos[:3] == ["sw", "pwa", "docs"] and paso_docs["etapa"] == "Prefiltro" and paso_docs["depende_de"] == ["pwa"]
          and paso_docs.get("documentos_vacante") and paso_docs["documentos"] == ["INE (frente y reverso)", "Fotos del auto"],
          f"la actividad «Documentos» de la ruta (después del prefiltro) recoge los documentos de la vacante: {pasos}")
    v4 = db.query(Vacante).filter(Vacante.estado == "Publicada", Vacante.id.notin_([v.id, v2.id, v3.id])).order_by(Vacante.id).first()
    v4.cuenta_id, v4.proceso, v4.documentos_solicitados = cuenta3.id, sproc.proceso_para_vacante(db, cuenta3.id, {}, RUTA_DOCS), []
    db.commit()
    q4 = nueva_postulacion("Sin Docs", "5512350002", v4)
    check(not any(x.get("documentos_vacante") for x in q4.proceso["pasos"]) and len(q4.proceso["pasos"]) == len(RUTA_DOCS["pasos"]),
          "sin documentos en la vacante la ruta no cambia")

    n = len(ENV)
    check(asyncio.run(sdv.revisar(db, q3)) is None and len(ENV) == n, "antes de terminar el prefiltro no se pide nada")
    q3.prefiltro_completo, q3.estado = True, "cumple"
    db.commit()
    asyncio.run(sproc.avanzar_seguro(db, q3))
    db.expire_all()
    q3 = db.get(Postulacion, q3.id)
    msgs = [x for x in ENV[n:] if x[0] == "whatsapp" and x[1].endswith("5512350001")]
    check(len(msgs) == 1 and "• INE" in msgs[0][2] and "• Fotos del auto (4 fotos)" in msgs[0][2] and "/expediente/" in msgs[0][2],
          f"al pasar el prefiltro el agente manda la liga con los documentos: {msgs[0][2] if msgs else None!r}")
    check(q3.etapa == "Prefiltro" and q3.expediente is not None, "la actividad obligatoria detiene la ruta hasta revisar los documentos")
    e3 = q3.expediente
    docs3 = {d.tipo: d for d in e3.documentos}
    check(docs3["Fotos del auto"].cantidad == 4 and docs3["Fotos del auto"].es_foto and docs3["INE (frente y reverso)"].solicitado_en is not None,
          "cada documento solicitado queda ligado al expediente (fotos del auto: 4)")
    asyncio.run(sproc.avanzar_seguro(db, q3))
    check(len([x for x in ENV[n:] if x[1].endswith("5512350001")]) == 1, "la liga sale una sola vez")
    pub = client.get(f"/expedientes/publica/{e3.token}").json()
    check([d["tipo"] for d in pub["documentos"]] == ["INE (frente y reverso)", "Fotos del auto"] and pub["documentos"][1]["cantidad"] == 4,
          f"la liga muestra un renglón por documento pedido: {[d['tipo'] for d in pub['documentos']]}")

    print("\n--- Cambio 25 · foto por el chat → liga ---")
    with en_conversacion("whatsapp", "52" + q3.telefono):
        res = asyncio.run(wh.procesar_entrante(db, {"telefono": "521" + q3.telefono, "texto": "mi ine", "nombre": "Pedro", "wa_id": "wamid.foto1",
                                                     "tipo": "image", "media": {"id": "m1"}, "id_seleccionado": "", "numero_receptor": "",
                                                     "canal": "whatsapp"}))
    check(res.get("accion") == "documento_a_liga" and res["respuesta"] == f"Súbela aquí, por favor: {sdv.liga(q3)}",
          f"una foto por el chat se ataja con la liga: {res.get('respuesta')!r} ({res.get('accion')})")

    print("\n--- Cambio 25 · subida: un registro por archivo, HEIC → JPG ---")

    def imagen(fmt="JPEG", color=(200, 30, 30)):
        b = io.BytesIO()
        Image.effect_noise((96, 96), 60).convert("RGB").save(b, format=fmt, quality=95) if fmt != "HEIF" else \
            Image.effect_noise((96, 96), 60).convert("RGB").save(b, format=fmt)
        return b.getvalue()

    for i in range(4):
        r = client.post(f"/expedientes/publica/{e3.token}/documentos", data={"tipo": "Fotos del auto"},
                        files={"archivo": (f"auto{i}.jpg", imagen(), "image/jpeg")})
        check(r.status_code == 200, f"foto {i + 1} del auto ({r.status_code} {r.text[:160]})")
    db.expire_all()
    fotos = db.query(ArchivoDocumento).filter(ArchivoDocumento.documento_id == docs3["Fotos del auto"].id).all()
    d_auto = db.get(Documento, docs3["Fotos del auto"].id)
    check(len(fotos) == 4 and all(f.vigente for f in fotos) and len({f.archivo for f in fotos}) == 4 and d_auto.estado == "revision",
          "las 4 fotos son 4 registros propios y el documento queda «Por revisar»")
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        heic = imagen("HEIF")
    except Exception:  # noqa: BLE001
        heic = None
    if heic:
        r = client.post(f"/expedientes/publica/{e3.token}/documentos", data={"tipo": "INE"}, files={"archivo": ("ine.heic", heic, "image/heic")})
        db.expire_all()
        a_ine = db.query(ArchivoDocumento).filter(ArchivoDocumento.documento_id == docs3["INE (frente y reverso)"].id).first()
        check(r.status_code == 200 and a_ine and a_ine.mime == "image/jpeg" and a_ine.convertido_de == "heic" and a_ine.nombre_archivo.endswith(".jpg"),
              f"una foto HEIC se convierte a JPG en el servidor ({r.status_code} {r.text[:160]})")
    else:
        r = client.post(f"/expedientes/publica/{e3.token}/documentos", data={"tipo": "INE"}, files={"archivo": ("ine.jpg", imagen(), "image/jpeg")})
        check(r.status_code == 200, "INE subida (sin codificador HEIF en este equipo)")
    db.expire_all()
    exp = client.get(f"/contratacion/expedientes/{e3.id}").json()
    est = exp.get("estadoDocumentos") or (exp.get("expediente") or {}).get("estadoDocumentos")
    check(est and est["clave"] == "por_revisar" and est["texto"] == "Por revisar", f"expediente «Por revisar»: {est}")

    print("\n--- Cambio 25 · RH: pedir corrección, aprobar, excepción ---")
    n = len(ENV)
    r = client.post(f"/contratacion/expedientes/{e3.id}/documentos/correccion", json={"tipos": ["Fotos del auto"], "motivo": "Se ven borrosas"})
    check(r.status_code == 200 and r.json()["expediente"]["estadoDocumentos"]["texto"] == "Corrección pedida", f"«Corrección pedida» ({r.status_code} {r.text[:160]})")
    m = [x for x in ENV[n:] if x[0] == "whatsapp" and x[1].endswith("5512350001")]
    check(m and "• Fotos del auto" in m[0][2] and "• INE" not in m[0][2] and "Se ven borrosas" in m[0][2] and sdv.liga(q3) in m[0][2],
          "el candidato recibe por WhatsApp solo lo que debe reemplazar, el motivo y la liga")
    db.expire_all()
    check(not any(f.vigente for f in db.query(ArchivoDocumento).filter(ArchivoDocumento.documento_id == docs3["Fotos del auto"].id)),
          "las fotos anteriores se conservan como no vigentes")
    pub = client.get(f"/expedientes/publica/{e3.token}").json()
    fila = next(d for d in pub["documentos"] if d["tipo"] == "Fotos del auto")
    check(fila["correccion"] == "Se ven borrosas" and fila["recibidos"] == 0 and fila["estadoRevision"] == "Corrección pedida",
          "la liga muestra la corrección pedida")
    for i in range(4):
        client.post(f"/expedientes/publica/{e3.token}/documentos", data={"tipo": "Fotos del auto"},
                    files={"archivo": (f"nueva{i}.png", imagen("PNG"), "image/png")})
    r = client.post(f"/contratacion/expedientes/{e3.id}/documentos/aprobar", json={"tipos": []})
    check(r.status_code == 200 and r.json()["expediente"]["estadoDocumentos"]["texto"] == "Documentos aprobados", f"«Documentos aprobados» ({r.status_code} {r.text[:160]})")
    db.expire_all()
    q3 = db.get(Postulacion, q3.id)
    paso = next(x for x in sproc.estado_pasos(q3) if x["id"] == "docs")  # Cambio 36: la actividad «Documentos» de la ruta
    check(paso["estado"] == "completada" and q3.etapa == "Entrevista IA", f"documentos aprobados → la ruta sigue ({paso['estado']}, {q3.etapa})")

    q5 = nueva_postulacion("Lalo Excepción", "5512350003", v3)
    q5.prefiltro_completo, q5.estado = True, "cumple"
    db.commit()
    asyncio.run(sproc.avanzar_seguro(db, q5))
    db.expire_all()
    q5 = db.get(Postulacion, q5.id)
    r = client.post(f"/contratacion/expedientes/{q5.expediente.id}/documentos/excepcion", json={"motivo": "corto"})
    check(r.status_code == 400, "la excepción exige motivo")
    r = client.post(f"/contratacion/expedientes/{q5.expediente.id}/documentos/excepcion", json={"motivo": "Entrega las fotos el primer día"})
    db.expire_all()
    q5 = db.get(Postulacion, q5.id)
    check(r.status_code == 200 and r.json()["expediente"]["estadoDocumentos"]["texto"] == "Excepción" and q5.etapa == "Entrevista IA",
          f"«Marcar excepción» deja la actividad cumplida con quién y por qué y la ruta sigue ({q5.etapa})")
    app.dependency_overrides.clear()

    # ================= Bloque 4 (Cambios 25 y 27) · ajustes menores y UI =================
    print("\n--- Cambio 27 · la ruta define el enfoque (Masivos = Operativo) ---")
    from app.serial import vacante_dict

    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta3
    masivos = sproc.ruta_base("masivos_sin_documentos")
    corporativo = sproc.ruta_base("corporativo")
    check(sproc.enfoque_de_ruta(masivos) == "operativo" and sproc.enfoque_de_ruta(masivos["pasos"]) == "operativo"
          and sproc.enfoque_de_ruta(corporativo) is None, "Masivos → Operativo; Corporativos → lo decide la vacante")
    db.expire_all()
    v4 = db.get(Vacante, v4.id)
    fijar_ruta(db, v4.codigo, json={"pasos": masivos["pasos"]})  # Cambio 36: con candidatos, la ruta se fija como configuración
    r = client.patch(f"/vacantes/{v4.codigo}", json={"seniority": "Senior", "enfoque_entrevista": "profesional"})
    db.expire_all()
    v4 = db.get(Vacante, v4.id)
    check(r.status_code == 200 and v4.enfoque_entrevista == "operativo" and v4.seniority == "Senior",
          f"vacante Senior con ruta Masivos: el enfoque queda Operativo ({r.status_code} {v4.enfoque_entrevista})")
    r = client.patch(f"/vacantes/{v4.codigo}", json={"enfoque_entrevista": "profesional"})
    db.expire_all()
    check(db.get(Vacante, v4.id).enfoque_entrevista == "operativo", "con ruta Masivos no se puede cambiar a otro enfoque")
    fijar_ruta(db, v4.codigo, json={"pasos": corporativo["pasos"]})  # Cambio 36: con candidatos, la ruta se fija como configuración
    r = client.patch(f"/vacantes/{v4.codigo}", json={"enfoque_entrevista": "profesional"})
    db.expire_all()
    check(db.get(Vacante, v4.id).enfoque_entrevista == "profesional", "con ruta Corporativos se respeta el que eligió RH")
    q6 = nueva_postulacion("Ruta Masiva", "5512350004", v3)
    v3 = db.get(Vacante, v3.id)
    v3.enfoque_entrevista = "profesional"
    db.commit()
    check(sproc.enfoque_entrevista_agente(q6, v3, "entrevista_whatsapp") == "operativo",
          "la entrevista de una postulación con ruta Masivos usa el enfoque Operativo aunque la vacante diga otro")

    print("\n--- Cambios 25/27 · formularios web sin «Parcial» ni «más de 90 min» ---")
    v4.preguntas_filtro = [{"pregunta": "¿Tienes licencia?", "tipo": "si_no", "valida": "Licencia", "respuesta_esperada": "Sí",
                            "descarta": True, "opciones": ["Sí", "No", "Parcial"]}]
    db.commit()
    check(vacante_dict(v4)["criterios"][0]["opciones"] == ["Sí", "No"], "un «Parcial» guardado antes nunca llega al formulario web")
    from app.services import ajustes_demo

    check(all("Parcial" not in (q.get("opciones") or []) for q in ajustes_demo.PREGUNTAS_AYUDANTE), "la demo ya no siembra «Parcial»")
    app_dir = RAIZ.parent / "red-human-app"
    form = (app_dir / "components/dashboard/vacantes/formulario-contenido.tsx").read_text(encoding="utf-8")
    seg = (app_dir / "components/dashboard/procesos/seguimiento-proceso.tsx").read_text(encoding="utf-8")
    check("90 min" not in form and "90 min" not in seg and "no recomendable" not in seg.split("const traslado")[1][:600],
          "sin «más de 90 min = alerta»: el límite de traslado sale solo del indispensable")
    # Cambio 36: el proceso manda — ya no hay selector de enfoque; con ruta Masivos el formulario lo fija en Operativo
    # Cambio 43: el formulario toma el «Enfoque» de la actividad de entrevista de la ruta (Masivos = Operativo)
    check('label="Enfoque de entrevista"' not in form and "enfoqueDeRuta(rutaGeneracion)" in form and "Usar Operativo" not in form,
          "el enfoque lo define la ruta (sin selector ni sugerencia por seniority)")

    print("\n--- Cambio 27 · selector de vacantes del tablero ---")
    tablero = (app_dir / "app/dashboard/candidatos/page.tsx").read_text(encoding="utf-8")
    sel = tablero[tablero.index('id="filtro-vacante"'):]
    sel = sel[:sel.index("</select>")]
    check(sel.index('<option value="">Todas las vacantes</option>') < sel.index("vacantes.map"), "la primera opción es «Todas las vacantes»")
    check("textoOpcionVacante(v)" in sel and "[v.id, v.titulo, v.ubicacion]" in tablero, "cada opción = «[folio] [puesto] [ubicación]»")
    app.dependency_overrides.clear()

print(f"\n🎉 {OK} comprobaciones OK — Cambios 21-27")
