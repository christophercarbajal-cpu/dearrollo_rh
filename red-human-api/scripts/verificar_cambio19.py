"""Cambio 19 (2026-10-12): prefiltro y entrevista operativa — CERO reglas fijas. SIN red (IA simulada).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambio19.py

Bloque 1 — generación: el prefiltro de WhatsApp sale de los indispensables («Descarta si…») y de las condiciones de la
descripción («Se registra»), más colonia, traslado, transporte y sueldo; validación estricta (todos los indispensables,
una idea por pregunta) con regeneración (máximo 2). El guion de la entrevista tiene 7 secciones dinámicas.
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN"):
    os.environ[k] = ""

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

print(f"\n🎉 {OK} comprobaciones OK — Cambio 19")
