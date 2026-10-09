"""Verificación BLOQUE 1 (2026-09-20): generación de prefiltros.
- Web (`preguntas_filtro`): TODAS cerradas, respondibles solo con Sí / No / Parcial; sin texto libre.
- WhatsApp (`preguntas_filtro_whatsapp`): una pregunta por criterio (sin compuestas); respuestas abiertas permitidas.
Garantizado en código (`ia.cerrar_preguntas_web`, `ia.separar_preguntas_whatsapp`) y en el prompt. Modo demo.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_prefiltros_cerrados.py
"""

import inspect as _insp
import os
import re
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_b1p_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "b1p.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-b1p"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Usuario, UsuarioCuenta  # noqa: E402
from app.services import ia  # noqa: E402

OK = 0
ABIERTAS = re.compile(r"^\s*¿?\s*(describe|explica|cu[eé]ntanos|cu[eé]ntame|menciona|detalla|qu[eé]\s|c[oó]mo\s|cu[aá]ntos?\s)", re.IGNORECASE)


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


P = ia.PreguntaFiltro

print("\n--- 1. Prompt de generación ---")
fuente = _insp.getsource(ia)
check("PREFILTRO WEB" in fuente and "Sí / No / Parcial" in fuente and "PROHIBIDO" in fuente and "«Describe», «Explica», «Cuéntanos»" in fuente, "regla 5: prefiltro web cerrado, texto libre prohibido explícitamente")
check("PREFILTRO WHATSAPP" in fuente and "UNA pregunta por criterio" in fuente and "prohibidas las preguntas compuestas" in fuente and "sí se permiten respuestas abiertas" in fuente, "regla 5b: WhatsApp una pregunta por criterio, abiertas permitidas")
check("UN solo criterio por pregunta" in _insp.getsource(ia.prefiltro_turno), "el agente de WhatsApp también recibe la regla en cada turno")

print("\n--- 2. Web: cierre garantizado en código ---")
web = [
    P(pregunta="Describe tu experiencia manejando montacargas", tipo="texto_corto", valida="Experiencia en montacargas", respuesta_esperada="", descarta=True),
    P(pregunta="Cuéntanos por qué te interesa el puesto", tipo="texto_corto", valida="", respuesta_esperada="", descarta=False),
    P(pregunta="¿Cuántos años de experiencia tienes en un puesto similar?", tipo="numero", valida="Experiencia previa", respuesta_esperada=">= 1 año", descarta=False, opciones=ia._RANGO_ANOS_GENERICO),
    P(pregunta="¿Cuántos años de experiencia en ventas tienes?", tipo="numero", valida="2 años en ventas", respuesta_esperada=">= 2", descarta=True),
    P(pregunta="¿Qué software de nómina dominas?", tipo="opcion", valida="Manejo de SAP", respuesta_esperada="SAP", descarta=False, opciones=["SAP", "Otro"]),
    P(pregunta="¿Vives en Querétaro?", tipo="si_no", valida="Radicar en Querétaro", respuesta_esperada="Sí", descarta=True),
]
cerradas = ia.cerrar_preguntas_web(web)
check(len(cerradas) == 6 and all(q.tipo == "si_no" for q in cerradas), "todas quedan tipo si_no")
check(all(q.opciones == ["Sí", "No", "Parcial"] for q in cerradas), "todas con opciones exactas Sí / No / Parcial")
check(all(not ABIERTAS.match(q.pregunta) for q in cerradas), "ninguna empieza con Describe/Explica/Cuéntanos/Qué/Cómo/Cuántos")
check(all(q.pregunta.startswith("¿") and q.pregunta.endswith("?") for q in cerradas), "todas son preguntas bien formadas (¿…?)")
check(cerradas[0].pregunta == "¿Cumples con este requisito: Experiencia en montacargas?", "texto libre → cumplimiento del requisito que valida")
check(cerradas[1].pregunta == "¿Cuentas con por qué te interesa el puesto?" or cerradas[1].pregunta.startswith("¿Cuentas con"), "texto libre sin requisito → pregunta cerrada igualmente")
check(cerradas[2].pregunta == "¿Tienes al menos 1 año de experiencia en un puesto similar?", "numérica → umbral cerrado (1 año)")
check(cerradas[3].pregunta == "¿Tienes al menos 2 años de experiencia en ventas?", "numérica → umbral cerrado (2 años)")
check(cerradas[4].pregunta == "¿Cumples con este requisito: Manejo de SAP?", "opción abierta («¿Qué…?») → cerrada")
check(cerradas[5].pregunta == "¿Vives en Querétaro?" and cerradas[5].descarta, "una si_no válida se respeta tal cual (y conserva descarta)")
check(all(q.respuesta_esperada in ("Sí", "No", "Parcial") for q in cerradas), "respuesta esperada siempre es una de las 3 opciones")

print("\n--- 3. WhatsApp: una pregunta por criterio ---")
wa = [
    P(pregunta="¿Cuántos años de experiencia tienes? ¿Has usado SAP?", tipo="numero", valida="Experiencia y SAP", respuesta_esperada=">=2", descarta=True),
    P(pregunta="Cuéntame, ¿cuánto tiempo llevas haciendo algo parecido a este puesto?", tipo="numero", valida="exp", respuesta_esperada=">= 1", descarta=False),
    P(pregunta="¿Vives en Querétaro o puedes trasladarte?", tipo="si_no", valida="ubic", respuesta_esperada="Sí", descarta=True),
]
sep = ia.separar_preguntas_whatsapp(wa)
check(len(sep) == 4, "la compuesta «¿…? ¿…?» se divide en 2 preguntas (3 → 4)")
check(sep[0].pregunta == "¿Cuántos años de experiencia tienes?" and sep[1].pregunta == "¿Has usado SAP?", "cada parte es una pregunta independiente")
check(sep[0].descarta and not sep[1].descarta and sep[0].valida == sep[1].valida, "la primera conserva el criterio eliminatorio; la segunda solo complementa")
check(sep[2].tipo == "numero" and sep[3].tipo == "si_no", "las preguntas simples NO se tocan (abiertas permitidas en WhatsApp)")
check(all(q.pregunta.count("?") == 1 for q in sep), "ninguna pregunta de WhatsApp lleva dos interrogaciones")

print("\n--- 4. Generación completa (demo + _asegurar_capturado) ---")
g, con_ia = ia.generar_vacante(ia.FichaVacante(titulo="Cajero", requisitos_indispensables=["Secundaria terminada", "Disponibilidad de fines de semana", "Manejo de efectivo"]))
check(not con_ia and len(g.preguntas_filtro) >= 4, "vacante demo generada")
check(all(q.tipo == "si_no" and q.opciones == ["Sí", "No", "Parcial"] for q in g.preguntas_filtro), "web generada: 100 % Sí/No/Parcial")
check(all(not ABIERTAS.match(q.pregunta) for q in g.preguntas_filtro), "web generada: sin texto libre ni «¿Cuántos…?»")
check(1 <= len(g.preguntas_filtro_whatsapp) <= 3 and all(q.pregunta.count("?") == 1 for q in g.preguntas_filtro_whatsapp), "WhatsApp generado: 2-3 preguntas, una por criterio")
check(any(q.tipo == "numero" for q in g.preguntas_filtro_whatsapp), "WhatsApp conserva la pregunta abierta de experiencia (respuesta libre)")

print("\n--- 5. API: generar vacante y formulario público ---")
with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Grupo CARBE", nombre_comercial="Grupo Carbe", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    r = client.post("/vacantes/generar", json={"titulo": "Auxiliar contable", "requisitos_indispensables": ["Licenciatura en Contaduría", "2 años de experiencia en conciliaciones"], "requisitos_deseables": ["Inglés"],
                                               "proceso": {"pasos": [{"id": "pw", "tipo": "prefiltro_web", "etapa": "Prefiltro"}, {"id": "pwa", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"}]}})  # 2026-10-09: solo lo que está en la ruta
    check(r.status_code == 200, f"POST /vacantes/generar ({r.status_code})")
    j = r.json()
    web_api = j.get("preguntas_filtro") or j.get("preguntasFiltro") or []
    wa_api = j.get("preguntas_filtro_whatsapp") or j.get("preguntasFiltroWhatsapp") or []
    check(web_api and all(q["tipo"] == "si_no" and q["opciones"] == ["Sí", "No", "Parcial"] for q in web_api), "la API entrega el prefiltro web cerrado")
    check(wa_api and all(q["pregunta"].count("?") == 1 for q in wa_api), "la API entrega el prefiltro WhatsApp con una pregunta por criterio")
    db.close()

print(f"\n🎉 Bloque 1 (prefiltros) verificado: {OK} comprobaciones OK.")
