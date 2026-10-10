"""Prueba de estrés del agente de WhatsApp (especificación 2026-10-10, sección 17). Base desechable, SIN red ni OpenAI.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_estres_prefiltro.py

Casos obligatorios: «ya la acabé», «no entiendo», «8», «creo que sí», «¿qué procesos?», una grosería con respuesta válida,
una respuesta larga que explica, «no» a un indispensable, respuestas en otro orden, emoji y mensaje de voz. En todos el
parser no se rompe (siempre hay respuesta), nunca ofrece opciones numeradas y «no entiendo» reformula sin repetir.
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

_dir = tempfile.mkdtemp(prefix="rh_estres_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "estres.db").replace("\\", "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET",
          "DROPBOX_SIGN_API_KEY", "DROPBOX_SIGN_CLIENT_ID"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-estres"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Postulacion, Vacante  # noqa: E402
from app.routers import webhooks  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402
from app.services.mensajeria import en_conversacion  # noqa: E402

OK = 0
WEB = [
    {"clave": "secundaria", "pregunta": "¿Terminaste la secundaria?", "valida": "Secundaria terminada", "tipo": "si_no",
     "respuesta_esperada": "Sí", "descarta": True},
    {"clave": "turnos", "pregunta": "¿Tienes disponibilidad para rolar turnos?", "valida": "Disponibilidad para rolar turnos",
     "tipo": "si_no", "respuesta_esperada": "Sí", "descarta": True},
    {"clave": "procesos", "pregunta": "¿Tienes experiencia en procesos de producción?", "valida": "Experiencia en procesos de producción",
     "tipo": "si_no", "respuesta_esperada": "Sí", "descarta": True},
    {"clave": "almacen", "pregunta": "¿Tienes al menos 2 años de experiencia en almacén?", "valida": "2 años de experiencia en almacén",
     "tipo": "si_no", "respuesta_esperada": "Sí", "descarta": True},
]
WA = [{"pregunta": "Para confirmar: ¿cuánto tiempo de experiencia en almacén tienes?", "tipo": "numero",
       "reconfirma": "2 años de experiencia en almacén", "opciones": ["Menos de 1 año", "1 a 2 años", "Más de 2 años"],
       "opciones_validas": ["Más de 2 años"], "minimo": 2}]
TODAS_SI = {"secundaria": "Sí", "turnos": "Sí", "procesos": "Sí", "almacen": "Sí"}
RESPUESTAS: list = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    cuenta = Cuenta(nombre="Estrés SA", nombre_comercial="Estrés", razon_social="Estrés SA de CV", estado="Activa", slug="estres-sa")
    db.add(cuenta)
    db.flush()
    sproc.asegurar_rutas_base(db, cuenta.id)
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").first()
    v.cuenta_id, v.titulo = cuenta.id, "Operador de almacén"
    v.preguntas_filtro, v.preguntas_filtro_whatsapp = WEB, WA
    v.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": sproc.ruta_base("masivos_sin_documentos")["pasos"]})
    db.commit()
    tel = iter(range(5530000001, 5530009999))

    def nueva(web: dict = None) -> str:
        t = str(next(tel))
        c = _crear_candidato(db, cuenta.id, "Erika Estrés", "WhatsApp", False, telefono=t, correo=f"e{t}@correo.mx")
        p = crear_postulacion(db, c, v, cuenta.id, "whatsapp", consentimiento=True)
        if web:
            p.analisis = {"respuestas_web": [{"pregunta": q["pregunta"], "respuesta": web[q["clave"]]} for q in WEB if q["clave"] in web]}
        db.commit()
        return p.codigo

    def turno(codigo: str, texto: str) -> str:
        db.expire_all()
        p = db.query(Postulacion).filter_by(codigo=codigo).one()
        r = asyncio.run(pconv.turno(db, p, texto, "whatsapp"))
        respuesta = (r or {}).get("respuesta") or ""
        RESPUESTAS.append(respuesta)
        return respuesta

    def post(codigo: str) -> Postulacion:
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=codigo).one()

    def respondida(codigo: str, clave: str) -> dict:
        return (post(codigo).analisis.get(pconv.ENTIDAD) or {}).get("respuestas", {}).get(clave) or {}

    print("\n--- 1. «ya la acabé» ---")
    P = nueva()
    check("¿Terminaste la secundaria?" in turno(P, "Hola"), "primera pregunta: un indispensable, de una en una")
    r = turno(P, "ya la acabé")
    check(respondida(P, "secundaria").get("valor") == "si" and "rolar turnos" in r, "«ya la acabé» = sí → sigue con el siguiente indispensable")

    print("\n--- 2. «no entiendo» → reformula, nunca repite ---")
    r1 = turno(P, "no entiendo")
    check(r1 and "¿Tienes disponibilidad para rolar turnos?" not in r1 and "turnos" in r1.lower(), "reformula la MISMA pregunta con otras palabras")
    r2 = turno(P, "no entiendo")
    check(r2 and r2 != r1 and "turnos" in r2.lower(), "un segundo «no entiendo» trae otra reformulación (no repite el texto)")
    check(post(P).activa and not respondida(P, "turnos"), "«no entiendo» nunca se toma como «No»")

    print("\n--- 3. grosería con respuesta válida ---")
    r = turno(P, "ya te dije pinche bot, sí puedo rolar turnos")
    check(respondida(P, "turnos").get("valor") == "si" and "procesos de producción" in r, "ignora el tono: la respuesta vale y avanza")

    print("\n--- 4. «¿qué procesos?» → reformula ---")
    r = turno(P, "¿qué procesos?")
    check(r and "¿Tienes experiencia en procesos de producción?" not in r and "procesos de producción" in r and not respondida(P, "procesos"),
          "una pregunta de vuelta se contesta reformulando, sin registrar respuesta")

    print("\n--- 5. «creo que sí» ---")
    turno(P, "creo que sí")
    check(respondida(P, "procesos").get("valor") == "si"
          and any("con duda" in x for x in post(P).analisis.get("puntos_validar_prefiltro") or []),
          "«creo que sí» cuenta como sí y queda en Puntos por validar")

    print("\n--- 6. emoji ---")
    turno(P, "👍")
    check(respondida(P, "almacen").get("valor") == "si", "👍 = sí")
    p = post(P)
    check(p.activa and p.analisis["prefiltro_web"]["resultado"] == "cumple" and p.etapa == "Entrevista IA",
          "con todo cumplido el agente decide solo: avanza a la entrevista")
    check(any(e.tipo == "whatsapp" for e in p.entrevistas), "…en el MISMO chat (Entrevista Red Human por WhatsApp)")

    print("\n--- 7. respuesta larga que explica ---")
    P2 = nueva()
    turno(P2, "Hola")
    turno(P2, "Sí, terminé la secundaria en 2010 en la Técnica 45 y después me puse a trabajar en una bodega cargando cajas")
    check(respondida(P2, "secundaria").get("valor") == "si", "una respuesta larga que explica se interpreta como sí")

    print("\n--- 8. respuestas en otro orden ---")
    r = turno(P2, "Sí, y también tengo experiencia en procesos de producción, estuve en una maquiladora")
    check(respondida(P2, "turnos").get("valor") == "si" and respondida(P2, "procesos").get("valor") == "si"
          and "procesos de producción" not in r, "lo que contestó por adelantado se registra y no se vuelve a preguntar")

    print("\n--- 9. «8» sin unidad ---")
    P3 = nueva(TODAS_SI)
    r = turno(P3, "Hola")
    check("cuánto tiempo" in r.lower(), "el «Sí» del formulario se reconfirma pidiendo el dato exacto")
    r = turno(P3, "8")
    check("8 años o 8 meses" in r, "«8» → «¿son 8 años o 8 meses?»")
    turno(P3, "años")
    p3 = post(P3)
    check(respondida(P3, "rc-almacen").get("valor") == "si" and p3.etapa == "Entrevista IA", "con la unidad, el dato cumple y avanza")

    print("\n--- 10. «no» a un indispensable ---")
    P4 = nueva()
    turno(P4, "Hola")
    r = turno(P4, "no")
    p4 = post(P4)
    check(not p4.activa and p4.motivo_cierre == "descartado" and "necesitamos secundaria terminada" in r,
          "«no» a un indispensable → «Descartado» con motivo y mensaje de cierre")

    print("\n--- 11. mensaje de voz ---")
    P5 = nueva()
    turno(P5, "Hola")
    p5 = post(P5)
    with en_conversacion("whatsapp", "52" + p5.telefono):
        res = asyncio.run(webhooks.procesar_entrante(db, {"telefono": "521" + p5.telefono, "texto": "", "nombre": "Erika",
                                                           "wa_id": "wamid.voz1", "tipo": "audio", "id_seleccionado": "",
                                                           "numero_receptor": "", "canal": "whatsapp"}))
    RESPUESTAS.append(res.get("respuesta", ""))
    check(res.get("accion") == "audio_no_soportado" and "escribes" in res["respuesta"] and "¿Terminaste la secundaria?" in res["respuesta"],
          "un mensaje de voz: pide que lo escriba y repite la pregunta pendiente")
    check(not respondida(P5, "secundaria") and post(P5).activa, "…sin tomarlo como respuesta")

    print("\n--- Reglas generales ---")
    check(all(r.strip() for r in RESPUESTAS), f"el parser nunca se rompe: {len(RESPUESTAS)} turnos, todos con respuesta")
    check(not any(re.search(r"(?m)^\s*\d+[.)]\s", r) for r in RESPUESTAS), "ningún mensaje ofrece opciones numeradas")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — estrés del prefiltro")
