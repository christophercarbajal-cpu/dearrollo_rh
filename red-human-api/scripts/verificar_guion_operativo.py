"""Guion OPERATIVO definitivo: generación con IA + revisión automática (2026-10-11, Bloque 1). Base desechable; la IA se
SIMULA (nunca se llama a OpenAI).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_guion_operativo.py

Cubre: prompt «Eres un reclutador experto en personal operativo en México…» con sus reglas; condicionales «solo si
responde sí / no»; revisión (un solo «?», ≤ 15 palabras, palabras prohibidas, 6 secciones, sin documentos); regenera con
retroalimentación hasta 2 veces y, si el tercer intento falla, se guarda con los problemas a la vista de RH.
"""

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
_dir = _temporal.directorio("rh_guion_op_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "guion_op.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-guion-op"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Vacante  # noqa: E402
from app.services import entrevista_operativa as eop  # noqa: E402
from app.services import guiones as sgui  # noqa: E402
from app.services import ia  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


P, S = eop.PreguntaIA, eop.SeccionIA


def guion_ia(malo: bool = False):
    # 2026-10-11 (tarde): estructura exacta de 18 (7 · 3 · 3 · 3 · 1 · 1); la situación del oficio la completa el código
    sec = [
        S(clave="trayectoria", preguntas=[P(texto="¿Cuál fue tu último trabajo?"), P(texto="¿Qué hacías en ese trabajo?"),
                                          P(texto="¿Cuánto tiempo estuviste ahí?"), P(texto="¿Por qué saliste de ahí?"),
                                          P(texto="¿Tuviste otro trabajo antes de ese?"),
                                          P(texto="¿Qué hacías ahí?", condicion="si"),
                                          P(texto="¿Por qué lo dejaste?", condicion="si")]),
        S(clave="oficio", preguntas=[P(texto="¿Qué montacargas operabas?"), P(texto="¿A qué altura acomodabas tarimas?")]),
        S(clave="indispensables", preguntas=[P(texto="¿En qué trabajo te tocó rolar turnos?"),
                                             P(texto="¿Dónde adquiriste tu experiencia?")]),
        S(clave="situacion_actual", preguntas=[P(texto="¿Estás trabajando actualmente?"),
                                               P(texto="¿Por qué quieres cambiar de trabajo?", condicion="si"),
                                               P(texto="¿Desde cuándo no trabajas?", condicion="no")]),
        S(clave="motivacion", preguntas=[P(texto="¿Qué te interesa de este trabajo?")]),
        S(clave="logistica", preguntas=[P(texto="¿Cuándo podrías empezar?")]),
    ]
    if malo:
        sec[0] = S(clave="trayectoria", preguntas=[P(texto="¿Cuáles son tus fortalezas? ¿Y tus debilidades?"),
                                                   P(texto="Platícame con mucho detalle cómo era un día completo de trabajo en tu empleo "
                                                           "anterior desde que llegabas hasta que salías?")])
        sec = [x for x in sec if x.clave != "motivacion"]
        sec[2] = S(clave="indispensables", preguntas=[P(texto="¿Nos puedes mandar tu INE y comprobante?")])
    return eop.GuionOperativoIA(secciones=sec)


class _Resp:
    def __init__(self, parsed):
        self.output_parsed = parsed


class FalsaIA:
    """Cliente de OpenAI simulado: entrega en orden los guiones de `guiones` (True = malo)."""

    def __init__(self, malos):
        self.malos = list(malos)
        self.llamadas = []
        self.responses = self

    def parse(self, model, instructions, input, text_format):
        assert text_format is eop.GuionOperativoIA
        self.llamadas.append({"instructions": instructions, "input": input})
        return _Resp(guion_ia(self.malos.pop(0) if self.malos else False))


perfil = eop.perfil_base("montacarguista")
REQS = ["Disponibilidad para rolar turnos", "Experiencia de 1 año"]

# ================= 1. Revisión automática =================
print("\n--- 1. Revisión automática ---")
malo = eop._desde_ia(guion_ia(True), eop.armar_guion("Montacarguista", perfil, REQS, []))
problemas = eop.revisar_guion(malo)
texto = " | ".join(problemas)
check("UN solo signo de interrogación" in texto, "detecta dos preguntas en una línea")
check("máximo 15" in texto, "detecta más de 15 palabras")
check("palabra prohibida" in texto and "fortalezas" in texto, "detecta palabras prohibidas («fortalezas»)")
check("Motivación: lleva" in texto or "Faltan secciones" in texto or len(problemas) > 3, "detecta secciones mal armadas")
check("pide documentos" in texto, "detecta que pide documentos")
# 2026-10-11 (tarde): estructura exacta, Logística solo inicio y más palabras prohibidas
base18 = eop.armar_guion("Montacarguista", perfil, REQS, [])
# Cambio 19: 7 secciones con cupos (sin total fijo); sin horario ni modalidad capturados no hay sección 7
check([s["clave"] for s in base18["operativo"]["secciones"]] == ["trayectoria", "oficio", "indispensables", "situacion_actual", "motivacion", "logistica"]
      and not eop.revisar_guion(base18), "el guion determinista arma las secciones de la vacante y pasa la revisión")
check(base18["preguntas"][0] == "¿Cuál fue tu último trabajo?" and base18["preguntas"][-1] == eop.PREGUNTA_INICIO,
      "empieza con «¿Cuál fue tu último trabajo?» e Inicio solo pregunta la fecha de inicio")
corto = eop._desde_ia(eop.GuionOperativoIA(secciones=[S(clave=k, preguntas=[P(texto="¿Qué hacías ahí?")]) for k, _ in eop.ESTRUCTURA]), base18)
texto18 = " ".join(eop.revisar_guion(corto))
check("Trayectoria: lleva 1 pregunta (deben ser de 4 a 9)" in texto18, "detecta secciones incompletas (cupo por sección)")
check("Inicio solo pregunta la fecha de inicio" in texto18, "detecta Inicio con algo distinto a la fecha de inicio")
# Cambio 19: una idea por pregunta, sin marcas visibles y una pregunta de evidencia por indispensable
dos = {**base18, "preguntas": ["¿Qué hacías ahí y cuánto tiempo estuviste?"] + base18["preguntas"][1:]}
dos["operativo"] = {**base18["operativo"], "secciones": [{**base18["operativo"]["secciones"][0], "preguntas": dos["preguntas"][:8]}]
                    + base18["operativo"]["secciones"][1:]}
check(any("une dos ideas" in x for x in eop.revisar_guion(dos)), "detecta una pregunta que une dos ideas con «y»")
marca = {**base18, "preguntas": ["¿Cuál fue tu último trabajo? [Solo si responde sí]"] + base18["preguntas"][1:]}
marca["operativo"] = {**base18["operativo"], "secciones": [{**base18["operativo"]["secciones"][0], "preguntas": marca["preguntas"][:8]}]
                      + base18["operativo"]["secciones"][1:]}
check(any("marca «[Solo si…]»" in x for x in eop.revisar_guion(marca)), "detecta «[Solo si…]» en el texto visible")
check(not eop.une_dos_ideas("¿Puedes cargar cajas y tarimas?") and eop.une_dos_ideas("¿Dónde trabajaste y por qué saliste?"),
      "«y» entre dos cosas de una misma idea no cuenta; «y por qué…» sí")
ind = next(s for s in base18["operativo"]["secciones"] if s["clave"] == "indispensables")
check(len(ind["preguntas"]) == len(ind["requisitos"]) == 2 and all(q.startswith("¿") for q in ind["preguntas"]),
      "una pregunta de evidencia por indispensable")
for frase in ("¿Cómo te ves para crecer aquí?", "¿Qué quieres desarrollarte en este puesto?", "¿Cómo organizas tu día?",
              "Cuéntame un ejemplo de tu trabajo, ¿sí?", "¿Cómo manejas la presión?", "¿Cuál fue tu peor error?"):
    g1 = {**base18, "preguntas": [frase] + base18["preguntas"][1:]}
    g1["operativo"] = {**base18["operativo"], "secciones": [{**base18["operativo"]["secciones"][0], "preguntas": [frase] + base18["operativo"]["secciones"][0]["preguntas"][1:]}]
                       + base18["operativo"]["secciones"][1:]}
    check(any("palabra prohibida" in x for x in eop.revisar_guion(g1)), f"palabra prohibida: «{frase}»")
# C4 (prueba del 11 de octubre): ubicación, traslado, sueldo y escolaridad ya están en el prefiltro
for frase in ("¿Cuánto ganabas en ese trabajo?", "¿En qué colonia vives?", "¿Cuánto tiempo haces de tu casa?",
              "¿Qué transporte usas?", "¿Cuál es tu pretensión de sueldo?"):
    g1 = {**base18, "preguntas": [frase] + base18["preguntas"][1:]}
    g1["operativo"] = {**base18["operativo"], "secciones": [{**base18["operativo"]["secciones"][0], "preguntas": [frase] + base18["operativo"]["secciones"][0]["preguntas"][1:]}]
                       + base18["operativo"]["secciones"][1:]}
    check(any("ya están en el prefiltro" in x for x in eop.revisar_guion(g1)), f"tema del prefiltro en la entrevista: «{frase}»")
check("PROHIBIDOS en la entrevista: ubicación" in eop.PROMPT_GENERACION and "sueldo" in eop.PROMPT_GENERACION,
      "las instrucciones del LLM prohíben ubicación, traslado, sueldo y escolaridad")
check(all(not eop.revisar_guion(eop.armar_guion(x["nombre"], None, ["Rolar turnos"], [])) for x in eop.BIBLIOTECA.values()),
      "los guiones de los 10 oficios pasan la revisión (ninguno repite temas del prefiltro)")
check(not any(eop.es_alerta_escolaridad(q) for q in eop.armar_guion("Cargador", None, ["Secundaria terminada", "Rolar turnos"], [])["preguntas"]),
      "un indispensable de escolaridad nunca entra a la entrevista (se valida en el prefiltro)")
check(eop.revisar_guion(eop.armar_guion("Montacarguista", perfil, REQS, [], ubicacion="Tultitlán, Estado de México",
                                        horario="Lunes a sábado de 7 a 15")) == [],
      "el guion determinista (biblioteca + piezas fijas) cumple todas las reglas")
check(all(eop.revisar_guion(eop.armar_guion("Puesto", eop.perfil_base(k), [], [])) == [] for k in eop.BIBLIOTECA),
      "las 10 plantillas de oficio cumplen (≤ 15 palabras, un solo «?»)")
huerfana = {"preguntas": ["[Solo si responde sí] ¿Por qué?"], "operativo": {"secciones": [{"clave": "trayectoria", "preguntas": ["[Solo si responde sí] ¿Por qué?"]}]}}
check(any("condicional" in p for p in eop.revisar_guion(huerfana)), "una condicional sin pregunta previa de sí/no se marca")

# ================= 2. Condicionales =================
print("\n--- 2. Condicionales ---")
check(eop.condicion_de("[Solo si responde sí] ¿Por qué quieres cambiar?") == ("si", "¿Por qué quieres cambiar?")
      and eop.condicion_de("[solo si responde no] ¿Desde cuándo?") == ("no", "¿Desde cuándo?") and eop.condicion_de("¿Hola?") == ("", "¿Hola?"),
      "las marcas «[Solo si responde sí/no]» se leen sin importar mayúsculas")
base = eop.armar_guion("Montacarguista", perfil, REQS, [])
sit = next(s for s in base["operativo"]["secciones"] if s["clave"] == "situacion_actual")["preguntas"]
check(sit[1].startswith(eop.MARCA_SI) and sit[2].startswith(eop.MARCA_NO), "Situación actual trae sus condicionales sí / no")

# ================= 3. Generación con IA + regeneración =================
print("\n--- 3. Generación: prompt, regeneración y guardado con problemas ---")
original = ia._client
try:
    falsa = FalsaIA([False])
    ia._client = lambda: falsa
    g, con_ia = eop.generar_guion("Montacarguista", perfil, REQS, ubicacion="Tultitlán", horario="7 a 15")
    check(con_ia and len(falsa.llamadas) == 1 and g["operativo"]["revision"] == {"ok": True, "intentos": 1, "con_ia": True, "problemas": []},
          "guion bueno al primer intento: una sola llamada y revisión aprobada")
    ins = falsa.llamadas[0]["instructions"]
    check(ins.startswith("Eres un reclutador experto en personal operativo en México"), "el prompt empieza con «Eres un reclutador experto en personal operativo en México…»")
    check("Una sola pregunta por línea" in ins and "Máximo 15 palabras" in ins and "Estructura OBLIGATORIA" in ins
          and "1) trayectoria" in ins and "6) logistica (1): SOLO la fecha de inicio" in ins and "7) condiciones" in ins
          and "solo si responde sí" in ins and "nunca unas dos ideas con «y»" in ins and "NUNCA escribas «[Solo si…]»" in ins,
          "…con las reglas (Cambio 19): una idea por línea, 15 palabras, 7 secciones, condicionales sin marcas visibles")
    check(any(q.startswith(eop.MARCA_SI) for q in g["preguntas"]) and any(q.startswith(eop.MARCA_NO) for q in g["preguntas"]),
          "las condiciones de la IA quedan marcadas en el guion")
    check(perfil["situacion"]["pregunta"] in g["preguntas"], "la situación del oficio va tal cual (la evaluación B la reconoce)")

    falsa = FalsaIA([True, False])
    ia._client = lambda: falsa
    g, _ = eop.generar_guion("Montacarguista", perfil, REQS)
    check(len(falsa.llamadas) == 2 and g["operativo"]["revision"]["ok"] and g["operativo"]["revision"]["intentos"] == 2,
          "si falla, regenera y se queda con el que pasa")
    check("no pasó la revisión automática" in falsa.llamadas[1]["input"].lower() and "palabra prohibida" in falsa.llamadas[1]["input"],
          "la regeneración recibe los problemas como retroalimentación")

    falsa = FalsaIA([True, True, True, True])
    ia._client = lambda: falsa
    g, _ = eop.generar_guion("Montacarguista", perfil, REQS)
    rev = g["operativo"]["revision"]
    check(len(falsa.llamadas) == 3, "máximo 2 regeneraciones (3 intentos en total)")
    check(not rev["ok"] and rev["intentos"] == 3 and rev["problemas"] and g["preguntas"], "al tercer fallo se guarda IGUAL con los problemas")
finally:
    ia._client = original

# ================= 4. En la vacante: se guarda y RH ve qué falló =================
print("\n--- 4. Vacante: guardado, normalización y vista para RH ---")
with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = True
    from app.deps import usuario_actual, usuario_admin, usuario_decisor
    from app.models import Usuario, UsuarioCuenta
    from app.services import proceso as sproc

    cuenta = Cuenta(nombre="Guion Op SA", nombre_comercial="Guion Op", estado="Activa", slug="guion-op")
    db.add(cuenta)
    db.flush()
    sproc.asegurar_rutas_base(db, cuenta.id)
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for dep in (usuario_actual, usuario_decisor, usuario_admin):
        app.dependency_overrides[dep] = lambda: admin
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).first()
    v.cuenta_id, v.titulo, v.enfoque_entrevista, v.requisitos, v.guiones = cuenta.id, "Montacarguista", "operativo", " · ".join(REQS), {}
    v.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": sproc.ruta_base("masivos_sin_documentos")["pasos"]
                                                                  # Cambio 29: con avatar opcional la ruta NO es Masivos (en Masivos la entrevista es fija)
                                                                  + [{"id": "avatar", "tipo": "entrevista_agente", "etapa": "Entrevista IA", "obligatorio": False}]})
    db.commit()
    H = {"X-Cuenta-Id": str(cuenta.id)}
    falsa = FalsaIA([True, True, True])
    ia._client = lambda: falsa
    try:
        r = client.post(f"/vacantes/{v.codigo}/guiones/generar", headers=H, json={"sobrescribir_editadas": True})
    finally:
        ia._client = original
    check(r.status_code == 200, f"genera con la IA simulada ({r.status_code} {r.text[:200]})")
    db.expire_all()
    vista = sgui.vista(db.get(Vacante, v.id), db=db)
    sec = next(s for s in vista["secciones"] if s["clave"] == "entrevista_whatsapp")
    rev = sec["contenido"]["operativo"]["revision"]
    check(not rev["ok"] and rev["intentos"] == 3 and any("prohibida" in p for p in rev["problemas"]),
          "la vista de RH trae la revisión fallida con sus problemas")
    check(any(q.startswith(eop.MARCA_SI) for q in sec["contenido"]["preguntas"]), "al guardar, las preguntas de sí/no y sus condicionales no se «abren»")
    r = client.post(f"/vacantes/{v.codigo}/guiones/generar", headers=H, json={"sobrescribir_editadas": True})
    db.expire_all()
    sec = next(s for s in sgui.vista(db.get(Vacante, v.id), db=db)["secciones"] if s["clave"] == "entrevista_whatsapp")
    check(sec["contenido"]["operativo"]["revision"]["ok"], "sin IA el guion determinista pasa la revisión")
    g = dict(sec["contenido"])
    g["preguntas"] = g["preguntas"] + ["¿Cuál es tu mayor debilidad cuando trabajas bajo presión en el almacén de noche?"]
    limpio = sgui.normalizar_guion(g)
    check(not limpio["operativo"]["revision"]["ok"] and any("debilidad" in p or "presion" in p for p in limpio["operativo"]["revision"]["problemas"]),
          "si RH edita y mete algo que no cumple, la revisión se recalcula y lo señala")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — guion operativo (generación y revisión)")
