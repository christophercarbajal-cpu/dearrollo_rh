"""Cambio 19 (2026-10-12): prefiltro y entrevista operativa — CERO reglas fijas. SIN red (IA simulada).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambio19.py

Bloque 1 — generación: el prefiltro de WhatsApp sale de los indispensables («Descarta si…») y de las condiciones de la
descripción («Se registra»), más colonia, traslado, transporte y sueldo; validación estricta (todos los indispensables,
una idea por pregunta) con regeneración (máximo 2). El guion de la entrevista tiene 7 secciones dinámicas.
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
_dir = tempfile.mkdtemp(prefix="rh_cambio19_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c19.db").replace(chr(92), "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-c19"
os.environ["SEMBRAR_DEMO"] = "true"

from app.services import entrevista_operativa as eop  # noqa: E402
from app.services import ia  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


FICHA = ia.FichaGuion(titulo="Ayudante general", requisitos_indispensables=["Cargar cajas de 25 kg", "Disponibilidad para rolar turnos"],
                      requisitos_deseables=["Experiencia en almacén"], sueldo="Desde $9,000 MXN mensuales",
                      ubicacion="Tultitlán, Estado de México", descripcion="Buscamos ayudante con secundaria terminada para el almacén.",
                      horario="Lunes a sábado de 7 a 15", modalidad="Presencial", enfoque_entrevista="operativo",
                      perfil_operativo=eop.perfil_base("ayudante_general"))

# ================= Bloque 1 · prefiltro de WhatsApp =================
print("\n--- Bloque 1 · prefiltro de WhatsApp desde la vacante ---")
_, wa = ia.asegurar_prefiltros([], [], FICHA, hay_web=False, hay_whatsapp=True)
check(wa[0]["descarta"] and wa[0]["regla"].startswith("Descarta si") and "cajas" in wa[0]["valida"],
      f"la primera pregunta es un indispensable con «Descarta si…»: {wa[0]['pregunta']}")
check(sorted(q["valida"] for q in wa if q["descarta"]) == sorted(FICHA.requisitos_indispensables),
      "cada indispensable de la vacante → una pregunta que descarta (y nada más descarta)")
reg = [q for q in wa if not q["descarta"]]
check(all(q["regla"] == "Se registra" for q in reg), "todo lo demás «Se registra»")
check(any(q.get("dato") == "escolaridad" and q["valida"] == "Secundaria terminada" for q in reg),
      "la escolaridad de la DESCRIPCIÓN se pregunta y solo se registra")
check(any(q["valida"] == "Experiencia en almacén" for q in reg), "el deseable se pregunta y se registra")
check([q.get("dato") for q in wa[-4:]] == ["zona", "traslado", "transporte", "sueldo"],
      "siempre al final: colonia → traslado → transporte → sueldo")
check(not ia.revisar_prefiltro_whatsapp(wa, FICHA.requisitos_indispensables), "pasa la validación estricta")
sin_desc = ia.FichaGuion(titulo="Cargador", requisitos_indispensables=["Cargar bultos"], sueldo="A convenir")
_, wa2 = ia.asegurar_prefiltros([], [], sin_desc, hay_web=False, hay_whatsapp=True)
check(not any(q.get("dato") == "escolaridad" for q in wa2) and not any(q.get("dato") == "sueldo" for q in wa2),
      "sin escolaridad en la vacante no se pregunta; sin sueldo capturado tampoco (nada inventado)")
nivel = ia.FichaGuion(titulo="Ayudante", requisitos_indispensables=["Secundaria terminada"])
_, wa3 = ia.asegurar_prefiltros([], [], nivel, hay_web=False, hay_whatsapp=True)
check(wa3[0].get("dato") == "escolaridad" and wa3[0]["descarta"], "escolaridad INDISPENSABLE → «¿Hasta qué año estudiaste?» que descarta")
check(any("Falta la pregunta del indispensable" in x for x in ia.revisar_prefiltro_whatsapp(wa[1:], FICHA.requisitos_indispensables)),
      "la validación detecta un indispensable sin pregunta")
check(any("une dos ideas" in x for x in ia.revisar_prefiltro_whatsapp([{"pregunta": "¿Dónde vives y cuánto tardas?"}], [])),
      "la validación detecta dos ideas unidas con «y»")


class _Resp:
    def __init__(self, parsed):
        self.output_parsed = parsed


class FalsaIA:
    """La primera respuesta olvida un indispensable y une dos ideas; la regeneración lo corrige."""

    def __init__(self):
        self.llamadas = []
        self.responses = self

    def parse(self, model, instructions, input, text_format):
        self.llamadas.append({"instructions": instructions, "input": input})
        if text_format is eop.GuionOperativoIA:
            raise RuntimeError("sin guion por IA en esta prueba")
        P = ia.PreguntaPrefiltroIA
        if len(self.llamadas) == 1:
            wa = [P(pregunta="¿Puedes cargar cajas y dónde lo hiciste?", tipo="si_no", valida="Cargar cajas de 25 kg", respuesta_esperada="Sí",
                    descarta=True, opciones=[], reconfirma="", regla="Descarta si no puede cargar cajas")]
        else:
            wa = [P(pregunta="¿Puedes cargar cajas de 25 kg?", tipo="si_no", valida="Cargar cajas de 25 kg", respuesta_esperada="Sí",
                    descarta=True, opciones=[], reconfirma="", regla="Descarta si no puede cargar cajas de 25 kg"),
                  P(pregunta="¿Puedes rolar turnos?", tipo="si_no", valida="Disponibilidad para rolar turnos", respuesta_esperada="Sí",
                    descarta=True, opciones=[], reconfirma="", regla="Descarta si no puede rolar turnos")]
        return _Resp(ia.GuionesIA(prefiltro_whatsapp=wa))


falsa = FalsaIA()
original = ia._client
ia._client = lambda: falsa
try:
    salida, con_ia = ia.generar_guiones(FICHA.model_copy(update={"enfoque_entrevista": "profesional"}), ["prefiltro_whatsapp"],
                                        hay_web=False, hay_whatsapp=True)
finally:
    ia._client = original
check(con_ia and len(falsa.llamadas) == 2 and "NO pasó la revisión" in falsa.llamadas[1]["input"],
      "la IA olvidó un indispensable → se regeneró con los problemas como retroalimentación")
check("Descarta si" in falsa.llamadas[0]["instructions"] and "Se registra" in falsa.llamadas[0]["instructions"],
      "el prompt pide «Descarta si…» para indispensables y «Se registra» para la descripción")
wa_ia = salida["prefiltro_whatsapp"]
check(not ia.revisar_prefiltro_whatsapp(wa_ia, FICHA.requisitos_indispensables) and "¿Puedes rolar turnos?" in [q["pregunta"] for q in wa_ia],
      "el resultado final usa la redacción corregida y cubre todos los indispensables")

# ================= Bloque 1 · guion de la entrevista =================
print("\n--- Bloque 1 · guion de la entrevista (7 secciones) ---")
g = eop.armar_guion(FICHA.titulo, FICHA.perfil_operativo, FICHA.requisitos_indispensables, [], horario=FICHA.horario, modalidad="Remoto")
claves = [s["clave"] for s in g["operativo"]["secciones"]]
check(claves == ["trayectoria", "oficio", "indispensables", "situacion_actual", "motivacion", "logistica", "condiciones"],
      "Trayectoria · Oficio · Evidencia de indispensables · Situación actual · Motivación · Inicio · Condiciones")
sec = {s["clave"]: s for s in g["operativo"]["secciones"]}
check(sec["trayectoria"]["preguntas"][0] == "¿Cuál fue tu último trabajo?" and any("anterior" in q for q in sec["trayectoria"]["preguntas"]),
      "Trayectoria = último y anterior trabajo")
check(1 <= len(sec["oficio"]["preguntas"]) <= 3 and sec["oficio"]["preguntas"][-1] == eop.situacion_verificable(FICHA.perfil_operativo),
      "Oficio = 1-2 preguntas concretas + la situación verificable")
check(len(eop.armar_guion("Recepcionista", None, [], [])["operativo"]["secciones"][1]["preguntas"]) == 1,
      "sin perfil del oficio: sin situación (no es verificable)")
check(len(sec["indispensables"]["preguntas"]) == len(sec["indispensables"]["requisitos"]) == 2, "una pregunta de evidencia por indispensable")
check(sec["logistica"]["preguntas"] == [eop.PREGUNTA_INICIO] and len(sec["condiciones"]["preguntas"]) == 2,
      "Inicio = solo la fecha; Condiciones = horario y modalidad de la vacante")
check(not eop.revisar_guion(g) and not any(eop.une_dos_ideas(q) or eop.marca_visible(eop.texto_pregunta(q)) for q in g["preguntas"]),
      "pasa la revisión: una idea por pregunta, sin «[Solo si…]» visible")
check(all("condiciones" not in [s["clave"] for s in eop.armar_guion("X", None, [], [])["operativo"]["secciones"]] for _ in [0])
      and "indispensables" not in [s["clave"] for s in eop.armar_guion("X", None, [], [])["operativo"]["secciones"]],
      "sin indispensables ni condiciones en la vacante, esas secciones no existen")

# ================= Bloque 2 · conversación =================
print("\n--- Bloque 2 · transición, «gracias», conteo ---")
check(ia.mensaje_inicial_entrevista_whatsapp("Ayudante", "Luis", "¿Cuál fue tu último trabajo?")
      == "Listo, Luis. Ahora unas preguntas sobre tu experiencia. ¿Cuál fue tu último trabajo?",
      "transición EXACTA, sin pedir permiso ni presentarse de nuevo")
from app.services import prefiltro_conversacional as pconv  # noqa: E402

est = {}
salidas = [pconv.con_acuse(est, "¿P?") for _ in range(9)]
con = [x for x in salidas if x != "¿P?"]
check(len(con) == 3 and salidas[2] != "¿P?" and len(set(con)) == 3,
      f"prefiltro: «gracias» solo 1 de cada 3 mensajes y con variantes ({con})")
h = [{"rol": "assistant", "texto": "Muy bien, gracias. ¿Qué hacías?"}, {"rol": "user", "texto": "cargaba"}]
check(ia.dosificar_agradecimiento("¡Gracias por contarme! ¿Cuánto tiempo estuviste?", h) == "¿Cuánto tiempo estuviste?",
      "entrevista: si ya agradeció hace poco, se quita el «gracias» y queda la pregunta")
check(ia.dosificar_agradecimiento("Gracias. ¿Cuánto tiempo?", [{"rol": "assistant", "texto": "¿Qué hacías?"}]) == "Gracias. ¿Cuánto tiempo?",
      "…y si no ha agradecido, se conserva")
check(ia.sin_conteo("Muy bien. Solo me faltan 2 preguntas. ¿Cuándo podrías empezar?") == "Muy bien. ¿Cuándo podrías empezar?"
      and ia.sin_conteo("Última pregunta: ¿qué te interesa?") == "¿Qué te interesa?", "nunca anuncia cuántas preguntas faltan")

print("\n--- Bloque 2 · memoria de los indispensables ---")
GM = eop.armar_guion("Ayudante general", None, ["Cargar cajas de 25 kg", "Disponibilidad para rolar turnos"], [])
q_cajas = next(s for s in GM["operativo"]["secciones"] if s["clave"] == "indispensables")["preguntas"][0]
t_ok = [{"rol": "assistant", "texto": "¿Qué hacías en ese trabajo?"}, {"rol": "user", "texto": "Era ayudante, cargaba cajas en la bodega"}]
t_no = [{"rol": "assistant", "texto": "¿Qué hacías en ese trabajo?"}, {"rol": "user", "texto": "Atendía clientes, nunca cargué cajas"}]
check(q_cajas not in eop.preguntas_para_conversacion(GM, [], t_ok), "ya dijo «cargaba cajas» → se omite la pregunta de carga")
check(q_cajas in eop.preguntas_para_conversacion(GM, [], t_no), "«nunca cargué cajas» no es evidencia → se pregunta")
check("MEMORIA" in eop.bloque_prompt(GM, [], t_ok) and eop.texto_pregunta(q_cajas) not in eop.bloque_prompt(GM, [], t_ok),
      "el prompt de la IA tampoco la trae")

print("\n--- Bloque 2 · repreguntas del motivo de salida ---")
for resp, esperada in (("Me corrieron", "¿Por qué te despidieron?"), ("Tuve problemas ahí", "¿Con quién tuviste problemas?"),
                       ("Renuncié", "¿Por qué renunciaste?"), ("Por cosas personales", "¿Por qué decidiste salir?"),
                       ("Cerró la empresa", None), ("Renuncié porque me cambié de casa", None)):
    check(eop.repregunta_salida(resp) == esperada, f"«{resp}» → {esperada or 'sin repregunta'}")
QS = GM["preguntas"]
hist = [{"rol": "assistant", "texto": "Listo. Ahora unas preguntas sobre tu experiencia. ¿Cuál fue tu último trabajo?"},
        {"rol": "user", "texto": "Ayudante en una bodega"}, {"rol": "assistant", "texto": "¿Qué hacías en ese trabajo?"},
        {"rol": "user", "texto": "Acomodaba mercancía"}, {"rol": "assistant", "texto": "¿Cuánto tiempo trabajaste ahí?"},
        {"rol": "user", "texto": "Un año"}, {"rol": "assistant", "texto": "¿Por qué saliste de ese trabajo?"},
        {"rol": "user", "texto": "Me corrieron"}]
t1, _ = eop.turno_demo(QS, hist)
check(t1 == "¿Por qué te despidieron?", "sin IA: despido → «¿Por qué te despidieron?»")
t2, _ = eop.turno_demo(QS, hist + [{"rol": "assistant", "texto": t1}, {"rol": "user", "texto": "pues ya"}])
check(t2 == "¿Tuviste otro trabajo antes de ese?", "solo se repregunta UNA vez y sigue con el guion")


class _NoLlamar:
    def __init__(self):
        self.responses = self

    def parse(self, **_):
        raise AssertionError("no debía llamar a la IA")


_orig = ia._client
ia._client = lambda: _NoLlamar()
try:
    t3, _ = ia.entrevista_turno("x", hist, QS, operativo=True)
finally:
    ia._client = _orig
check(t3.respuesta == "¿Por qué te despidieron?", "con IA: la repregunta está garantizada en código")

print("\n--- Bloque 2 · inconsistencia web vs. WhatsApp ---")
from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Postulacion, Vacante  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

WEB_TRASLADO = "¿Haces menos de 1 hora de tu casa a la planta?"
DATOS = ia.preguntas_datos_siempre(ia.FichaGuion(titulo="Ayudante"))

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    cuenta = Cuenta(nombre="C19", nombre_comercial="C19", estado="Activa", slug="c19")
    db.add(cuenta)
    db.flush()
    vi, vr = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).limit(2).all()
    for vac, indispensable in ((vi, True), (vr, False)):
        vac.cuenta_id, vac.titulo = cuenta.id, f"Ayudante {'A' if indispensable else 'B'}"
        vac.preguntas_filtro = [{"pregunta": WEB_TRASLADO, "tipo": "si_no", "valida": "Traslado de menos de 1 hora",
                                 "respuesta_esperada": "Sí", "descarta": indispensable, "opciones": ["Sí", "No"]}]
        vac.preguntas_filtro_whatsapp = DATOS
        vac.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": [
            {"id": "pw", "tipo": "prefiltro_web", "etapa": "Prefiltro"}, {"id": "pwa", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"},
            {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"}]})
    db.commit()

    def postular(nombre, vac, tel):
        c = _crear_candidato(db, cuenta.id, nombre, "Portal", False, telefono=tel)
        p = crear_postulacion(db, c, vac, cuenta.id, "web", consentimiento=True)
        p.analisis = {"respuestas_web": [{"pregunta": WEB_TRASLADO, "respuesta": "Sí"}]}
        db.commit()
        return p.codigo

    def turno(codigo, texto):
        p = db.query(Postulacion).filter_by(codigo=codigo).one()
        return asyncio.run(pconv.turno(db, p, texto, "whatsapp"))

    A = postular("Rosa Pérez", vi, "5531900001")
    r = turno(A, "Hola")
    check(r["respuesta"].startswith("Hola, Rosa. Soy Red Human, el asistente virtual") and "Primero confírmame: " in r["respuesta"]
          and "preguntas" not in r["respuesta"], f"saludo EXACTO y sin anunciar cuántas preguntas: {r['respuesta']!r}")
    turno(A, "En el Centro de Tultitlán")
    r = turno(A, "Como 3 horas")
    pa = db.query(Postulacion).filter_by(codigo=A).one()
    inc = (pa.analisis or {}).get("inconsistencias") or []
    check(inc and inc[-1]["criterio"] == "Traslado de menos de 1 hora" and inc[-1]["web"] == "Sí",
          "web «Sí, menos de 1 hora» vs. chat «3 horas» → «Inconsistencia: Traslado de menos de 1 hora»")
    check(not pa.activa and pa.motivo_cierre == "descartado", "la inconsistencia es en un INDISPENSABLE → el agente descarta")
    B = postular("Luis Gómez", vr, "5531900002")
    turno(B, "Hola")
    turno(B, "En el Centro de Tultitlán")
    r = turno(B, "Como 3 horas")
    pb = db.query(Postulacion).filter_by(codigo=B).one()
    check(((pb.analisis or {}).get("inconsistencias") or [{}])[-1].get("criterio") == "Traslado de menos de 1 hora" and pb.activa,
          "en un requisito NO indispensable solo se registra la inconsistencia y el prefiltro sigue")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — Cambio 19")
