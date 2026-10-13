"""Cambios 21-27 (2026-10-12). Base desechable; IA, Bot API y Meta SIMULADAS (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambios21_27.py

Bloque 1 (Cambios 22, 23, 24): el «/start» de la liga de una postulación responde SIEMPRE sobre ella con el texto de su
estado (Análisis de CV en proceso / terminado → prefiltro / En espera de decisión / Descartado); el Análisis de CV nunca
detiene la ruta (sin «mínimo 70») y sus brechas se vuelven preguntas del prefiltro (reprobar un indispensable descarta);
la ficha compara CV vs respuesta por indispensable; en la ruta corporativa no se pregunta colonia ni transporte, el
sueldo es la expectativa (> 20 % arriba → Puntos por validar) y la entrevista con avatar pregunta «¿La quieres hacer
ahora?» (sí → liga; no → 3 días).
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
_dir = tempfile.mkdtemp(prefix="rh_c21_27_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c21.db").replace(chr(92), "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "RESEND_API_KEY",
          "TELEGRAM_WEBHOOK_SECRET", "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:PRUEBA-token-falso"
os.environ["TELEGRAM_BOT_USERNAME"] = "RedHumanPruebaBot"
os.environ["ADMIN_PASSWORD"] = "prueba-c21"
os.environ["SEMBRAR_DEMO"] = "true"

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
    exp = next(c for c in cs if c.get("dato") == "sueldo")
    check(exp.get("expectativa") and exp["chat"] == "¿Cuánto esperas ganar al mes?", f"el sueldo pregunta la expectativa: {exp['chat']}")
    brecha = next((c for c in cs if c.get("por_brecha")), None)
    check(brecha is not None and brecha["indispensable"] and "licencia" in brecha["criterio"].lower(),
          "la brecha del CV en un indispensable se vuelve pregunta del prefiltro (y descarta)")
    check(any(c.get("brecha") and c.get("registra") and "inglés intermedio" in c["chat"] for c in cs),
          "una brecha que no es indispensable se pregunta y solo se registra")
    check(cs.index(brecha) < cs.index(exp), "las brechas de indispensables van con los indispensables, antes de los datos")
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
    check(len(txt) >= 2 and "Primero confírmame:" in txt[1], "…y en seguida empieza el prefiltro (saludo + primera pregunta)")
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
        responder(p1, "No, no tengo" if c.get("por_brecha") else "Sí")
    db.refresh(p1)
    check(any(x.startswith("cv-") for x in vistos) and not p1.activa and p1.motivo_cierre == "descartado",
          "reprobar el indispensable de la brecha del CV DESCARTA")
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
    check(d.get("sueldo_expectativa") == 20000 and d.get("sueldo_sobre_tope"), "expectativa de 20 mil sobre un tope de 12 mil")
    pts = (p4.analisis or {}).get("puntos_validar_prefiltro") or []
    check(any(x.startswith("Expectativa de sueldo: $20,000 (más de 20 %") for x in pts), f"→ Puntos por validar: {pts}")
    check(p4.activa and pconv.estado(p4).get("resultado") == "cumple", "la expectativa alta nunca descarta")
    ficha = postulacion_dict(p4, detalle=True)
    comp = {f["requisito"]: f for f in ficha["comparativoIndispensables"]}
    lic = next(f for k, f in comp.items() if "icencia" in k)
    check(lic["cv"] == "No acreditado" and lic["cumple"] is True and lic["inconsistencia"],
          "comparativo: licencia — CV no acreditado vs «Sí» → Inconsistencia")
    ven = next(f for k, f in comp.items() if "ventas" in k.lower())
    check(ven["cv"] == "Acreditado" and ven["cumple"] is True and not ven["inconsistencia"], "experiencia: acreditada y confirmada")
    check(ficha["prefiltroDatos"]["sueldoExpectativa"] == 20000 and ficha["prefiltroDatos"]["sueldoSobreTope"], "la ficha muestra la expectativa")
    ultimo = respuestas[-1]["respuesta"]
    check(ultimo.endswith(agenda_chat.PREGUNTA_AHORA) and "¿Te confirmo" not in ultimo,
          f"entrevista con avatar: «¿La quieres hacer ahora?» (sin proponer horario): {ultimo!r}")
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

print(f"\n🎉 {OK} comprobaciones OK — Cambios 21-27")
