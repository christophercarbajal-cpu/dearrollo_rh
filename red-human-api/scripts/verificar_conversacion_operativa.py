"""Conversación operativa condicional y descarte estricto del prefiltro (2026-10-11, Bloque 2). Base desechable, SIN red.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_conversacion_operativa.py

Cubre: arranque sin saludo (2026-10-11 tarde: «Listo, [nombre]. Soy Red Human…» + la primera pregunta); condicionales
«solo si responde sí / no»; respuesta vaga → UN dato concreto y sigue; «no entiendo» → reformula sin cambiar de tema;
prefiltro sin «¿cuál de estas opciones te describe?» (sí/no en lenguaje natural); y descarte inmediato cuando la respuesta
indica que no cumple («1er año de secundaria» cuando se pide terminada), también en la reconfirmación.
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
_dir = _temporal.directorio("rh_conv_op_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "conv_op.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "RESEND_API_KEY",
          "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-conv-op"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Postulacion, Vacante  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.services import entrevista_operativa as eop  # noqa: E402
from app.services import entrevistas as sent  # noqa: E402
from app.services import ia  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp as swa  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
WA = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _meta_falso(cuerpo):
    WA.append(cuerpo)
    return {"enviado": True, "proveedor": "meta", "detalle": "", "wa_id": f"wamid.conv.{len(WA)}"}


settings.whatsapp_provider = "meta"
settings.meta_whatsapp_token, settings.meta_phone_number_id = "x", "123"
swa._meta_post = _meta_falso


def enviados(tel10, desde=0):
    return [c["text"]["body"] for c in WA[desde:] if c.get("type") == "text" and c.get("to", "").endswith(tel10)]


# ================= 1. Prefiltro: sí/no en lenguaje natural =================
print("\n--- 1. Prefiltro sin opciones múltiples ---")
rc = ia.pregunta_reconfirmacion("Secundaria terminada")
check(rc["pregunta"] == "Para confirmar, ¿terminaste la secundaria?" and rc["tipo"] == "si_no" and "cuál de estas" not in rc["pregunta"],
      "la reconfirmación es sí/no en lenguaje natural («Para confirmar, ¿terminaste la secundaria?»)")
check(ia.pregunta_si_no_natural("Disponibilidad para rolar turnos") == "¿Tienes disponibilidad para rolar turnos?"
      and ia.pregunta_si_no_natural("Saber leer y escribir") == "¿Sabes leer y escribir?", "otros requisitos también se dicen con naturalidad")
check(ia.chat_natural("Para confirmar «Secundaria terminada»: ¿cuál de estas opciones te describe?", "Secundaria terminada")
      == "Para confirmar, ¿terminaste la secundaria?", "las reconfirmaciones ya guardadas con «¿cuál de estas opciones…?» se dicen como sí/no")
check(ia.chat_natural("¿Cumples con: Secundaria terminada?") == "¿Terminaste la secundaria?", "«¿Cumples con: X?» también se dice natural")

# ================= 2. Clasificación: lo parcial NO cumple =================
print("\n--- 2. Descarte estricto: un nivel a medias no cumple ---")
C = {"criterio": "Secundaria terminada", "pregunta": "¿Terminaste la secundaria?", "chat": "¿Terminaste la secundaria?", "indispensable": True, "esperada": "si"}
for t in ("Tengo 1er año de secundaria", "sí, primer año de secundaria", "llegué hasta segundo de secundaria", "me quedé en primero"):
    check(pconv.clasificar(C, t) == "no", f"«{t}» → NO cumple")
for t in ("Sí, la terminé", "tengo la prepa", "sí"):
    check(pconv.clasificar(C, t) == "si", f"«{t}» → cumple")

# ================= 3. Motor de conversación (sin IA) =================
print("\n--- 3. Conversación: condicionales, respuesta vaga y «no entiendo» ---")
QS = eop.armar_guion("Cargador", eop.perfil_base("cargador"), [], [])["preguntas"]


def conversar(respuestas):
    h = [{"rol": "assistant", "texto": "Listo. ¿Comenzamos?"}, {"rol": "user", "texto": "sí"}]
    bot = []
    for r in list(respuestas) + ["ok"] * 30:
        t, fin = eop.turno_demo(QS, h)
        if fin:
            break
        bot.append(t)
        h += [{"rol": "assistant", "texto": t}, {"rol": "user", "texto": r(t) if callable(r) else r}]
    return bot


def responde(regla):
    return lambda q: next((v for k, v in regla.items() if k in q.lower()), "Cargaba cajas de 20 kilos en Bimbo")


si = conversar([responde({"trabajando actualmente": "Sí, en una bodega"})] * 20)
check("¿Por qué quieres cambiar de trabajo?" in si and "¿Desde cuándo no trabajas?" not in si,
      "«¿Estás trabajando actualmente?» → «sí»: solo la condicional de sí")
no = conversar([responde({"trabajando actualmente": "No"})] * 20)
check("¿Desde cuándo no trabajas?" in no and "¿Por qué quieres cambiar de trabajo?" not in no, "→ «no»: solo la condicional de no")
check(not any("[" in q for q in si + no), "la marca de la condicional nunca llega al candidato")
vaga = conversar(["pues normal", "en Bimbo, 2 años"] + [responde({})] * 20)
check(vaga[1].startswith(eop.PREFIJO_DATO) and vaga[2] == eop.texto_pregunta(QS[1]),
      "respuesta vaga → pide UN dato concreto y luego continúa con la siguiente")
vaga2 = conversar(["pues normal", "normal"] + [responde({})] * 20)
check(sum(1 for x in vaga2[:4] if x.startswith(eop.PREFIJO_DATO)) == 1, "el dato concreto se pide UNA sola vez por pregunta")
dud = conversar(["no entiendo", "no entiendo", "no entiendo"] + [responde({})] * 20)
check(dud[1].startswith(eop.PREFIJO_REFORMULA) and "último trabajo" in dud[1] and dud[2].startswith(eop.PREFIJO_REFORMULA)
      and dud[3] == eop.texto_pregunta(QS[1]), "«no entiendo» → reformula la MISMA pregunta (máx. 2) y después sigue")
prompt = eop.bloque_prompt({"preguntas": QS, "operativo": eop.armar_guion("Cargador", eop.perfil_base("cargador"), [], [])["operativo"]}, [])
check("CONDICIONALES" in prompt and "RESPUESTA VAGA" in prompt and "NO ENTIENDO" in prompt and "sin saludo" in prompt,
      "el prompt de la IA trae las mismas reglas (condicionales, dato concreto, reformular, sin saludo)")


class _Falsa:
    def __init__(self):
        self.responses = self

    def parse(self, model, instructions, input, text_format):
        class R:
            output_parsed = ia.TurnoEntrevista(respuesta="[Solo si responde sí] ¿Por qué quieres cambiar de trabajo?", terminada=False)
        return R()


orig = ia._client
ia._client = lambda: _Falsa()
try:
    t, _ = ia.entrevista_turno("x", [{"rol": "user", "texto": "sí"}], QS, operativo=True)
finally:
    ia._client = orig
check(t.respuesta == "¿Por qué quieres cambiar de trabajo?", "si la IA copia la marca de la condicional, se quita antes de enviar")

# ================= 4. De punta a punta en el chat =================
print("\n--- 4. Chat: prefiltro → «Listo, … Soy Red Human…» + primera pregunta → entrevista ---")
RUTA = [{"id": "pwa", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"},
        {"id": "ewa", "tipo": "entrevista_whatsapp", "etapa": "Entrevista IA"},
        # Cambio 29: con la entrevista con avatar (opcional) la ruta NO es Masivos — aquí se prueba la conversación configurable;
        # la base fija de Masivos (prefiltro y entrevista) se prueba en verificar_cambios28_29.py
        {"id": "av", "tipo": "entrevista_agente", "etapa": "Entrevista IA", "obligatorio": False},
        {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"}]
FILTRO = [{"pregunta": "¿Cumples con: Secundaria terminada?", "tipo": "si_no", "valida": "Secundaria terminada", "respuesta_esperada": "Sí",
           "descarta": True, "opciones": ["Sí", "No", "Parcial"]},
          {"pregunta": "¿Cumples con: Disponibilidad para rolar turnos?", "tipo": "si_no", "valida": "Disponibilidad para rolar turnos",
           "respuesta_esperada": "Sí", "descarta": True, "opciones": ["Sí", "No", "Parcial"]}]

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    cuenta = Cuenta(nombre="Conv Op", nombre_comercial="Conv Op", estado="Activa", slug="conv-op")
    db.add(cuenta)
    db.flush()
    v, v2 = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).limit(2).all()
    v.cuenta_id, v.titulo, v.enfoque_entrevista, v.preguntas_filtro, v.guiones = cuenta.id, "Cargador", "operativo", FILTRO, {}
    v.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": RUTA})
    db.commit()
    tel = iter(range(5541110001, 5541119999))

    def nueva(nombre, vac=None):
        t = str(next(tel))
        c = _crear_candidato(db, cuenta.id, nombre, "WhatsApp", False, telefono=t)
        p = crear_postulacion(db, c, db.get(Vacante, (vac or v).id), cuenta.id, "whatsapp", consentimiento=True)
        db.commit()
        return p

    def turno(p, texto):
        return asyncio.run(pconv.turno(db, db.query(Postulacion).filter_by(codigo=p.codigo).one(), texto, "whatsapp"))

    p = nueva("Ana Torres")
    r = turno(p, "Hola")
    check("¿Terminaste la secundaria?" in r["respuesta"] and "Parcial" not in r["respuesta"],
          "el bot pregunta sí/no en lenguaje natural, sin opciones")
    r = turno(p, "Sí, la terminé")
    check("¿Tienes disponibilidad para rolar turnos?" in r["respuesta"], "una pregunta por mensaje")
    desde = len(WA)
    turno(p, "Sí, sin problema")
    db.expire_all()
    p = db.query(Postulacion).filter_by(codigo=p.codigo).one()
    check(p.activa and (p.analisis.get("prefiltro_web") or {}).get("resultado") == "cumple", "cumple los indispensables → aprobado")
    if sent.entrevista_whatsapp_activa(p) is None:
        asyncio.run(sent.iniciar_whatsapp(db, p, "prueba"))
        db.commit()
    intro = next((t for t in enviados(p.telefono, desde) if t.startswith("Listo")), "")
    # 2026-10-11 (tarde): UN mensaje sin reglas internas ni permiso, con la primera pregunta del guion
    # Cambio 19: texto exacto, sin volver a presentarse
    check(intro == "Listo, Ana. Ahora unas preguntas sobre tu experiencia. ¿Cuál fue tu último trabajo?",
          "la entrevista empieza SIN saludo y en el mismo mensaje hace la primera pregunta")
    check("Comenzamos" not in intro and "inteligencia artificial" not in intro, "…sin «¿Comenzamos?» (el aviso de IA va en el aviso de privacidad)")
    e = sent.entrevista_whatsapp_activa(p)
    s = asyncio.run(sent.turno_whatsapp(db, p, e, "Cargador en una bodega", "whatsapp"))
    check(s["respuesta"] == "¿Qué hacías en ese trabajo?", "la respuesta cuenta para la primera pregunta y sigue con la segunda")
    check(ia.mensaje_inicial_entrevista_whatsapp("Cargador", "Luis").startswith("Listo, Luis. Ahora unas preguntas"),
          "ya no dice «cumples con los requisitos» (sin promesas)")

    print("\n--- 5. Chat: «1er año de secundaria» → DESCARTA de inmediato ---")
    q = nueva("Beto Parcial")
    turno(q, "Hola")
    r = turno(q, "Tengo 1er año de secundaria")
    db.expire_all()
    q = db.query(Postulacion).filter_by(codigo=q.codigo).one()
    check(not q.activa and q.motivo_cierre == "descartado" and q.estado == "no_cumple", "el agente DESCARTA, no aprueba")
    check("necesitamos secundaria terminada" in r["respuesta"], "y le manda el mensaje de cierre con el requisito")

    print("\n--- 6. Reconfirmación con formato viejo: se dice natural y también descarta ---")
    v2.cuenta_id, v2.titulo, v2.preguntas_filtro = cuenta.id, "Cargador B", [dict(FILTRO[0])]
    v2.preguntas_filtro_whatsapp = [{"pregunta": "Para confirmar «Secundaria terminada»: ¿cuál de estas opciones te describe?", "tipo": "opcion",
                                     "valida": "Secundaria terminada", "reconfirma": "Secundaria terminada", "descarta": False,
                                     "opciones": ["Sí, y lo puedo comprobar", "Sí, pero no lo puedo comprobar", "No"],
                                     "opciones_validas": ["Sí, y lo puedo comprobar", "Sí, pero no lo puedo comprobar"]}]
    v2.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": [{"id": "pw", "tipo": "prefiltro_web", "etapa": "Prefiltro"}] + RUTA})
    db.commit()
    w = nueva("Carla Web", v2)
    w.analisis = {"respuestas_web": [{"pregunta": FILTRO[0]["pregunta"], "respuesta": "Sí"}]}
    db.commit()
    r = turno(w, "Hola")
    check("¿terminaste la secundaria?" in r["respuesta"].lower() and "cuál de estas" not in r["respuesta"],
          "la reconfirmación guardada con «¿cuál de estas opciones…?» se pregunta como sí/no")
    r = turno(w, "sí, primer año de secundaria")
    db.expire_all()
    w = db.query(Postulacion).filter_by(codigo=w.codigo).one()
    check(not w.activa and w.motivo_cierre == "descartado" and (w.analisis.get("inconsistencias") or [{}])[-1].get("fuente") == "reconfirmacion",
          "la reconfirmación indica que NO cumple → DESCARTA (y la contradicción con el formulario queda registrada)")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — conversación operativa y prefiltro estricto")
