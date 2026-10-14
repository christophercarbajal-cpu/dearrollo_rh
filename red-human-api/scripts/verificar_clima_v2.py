"""Regresión de Clima v2 (2026-09-27). Base desechable y SIN clave de OpenAI (modo demo, nada sale a la IA).

    .venv/Scripts/python.exe scripts/verificar_clima_v2.py

Cubre: generación de encuesta con Red Human (propuesta, no guarda nada) y el motor de cálculos
(participación, % favorable, dimensiones, índice; prueba y externas siempre fuera).
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
_dir = _temporal.directorio("rh_clima2_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "clima2.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-clima2"
os.environ["SEMBRAR_DEMO"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ESCALA_CLIMA, Colaborador, Cuenta, MedicionClima, Usuario, UsuarioCuenta  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Clima v2", nombre_comercial="Empresa Clima", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for i, (area, sede) in enumerate([("Ventas", "CDMX"), ("Ventas", "Puebla"), ("Operaciones", "CDMX"), ("Operaciones", "Puebla")], start=1):
        db.add(Colaborador(codigo=f"COL-{i}", cuenta_id=cuenta.id, nombre=f"Persona {i}", area=area, ubicacion=sede))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    print("\n--- Fase 2a · Crear encuesta con Red Human ---")
    check(client.post("/clima/generar", json={"prompt": "  "}).status_code == 400, "sin prompt → 400")
    r = client.post("/clima/generar", json={"prompt": "¿Cómo se siente mi equipo con su jefe y la carga de trabajo?"})
    check(r.status_code == 200, f"genera una propuesta ({r.status_code})")
    g = r.json()
    check(g["ia"] is False and g["titulo"] and g["dimensiones"] and len(g["preguntas"]) >= 8, "sin clave: propuesta demo con nombre, dimensiones y preguntas")
    check([p["orden"] for p in g["preguntas"]] == list(range(1, len(g["preguntas"]) + 1)), "preguntas con orden 1..n")
    check(all(p["dimension"] in g["dimensiones"] for p in g["preguntas"]), "cada pregunta pertenece a una dimensión de la lista")
    check({p["tipo"] for p in g["preguntas"]} == {"escala", "opcion", "abierta"}, "trae escala 1-5, opción múltiple y abierta")
    check(all(p.get("escala_max") == ESCALA_CLIMA for p in g["preguntas"] if p["tipo"] == "escala"), "la escala es 1-5")
    check(db.query(MedicionClima).count() == 0, "generar NO guarda nada: RH revisa primero")
    r = client.post("/clima/mediciones", json={"titulo": g["titulo"], "dimensiones": g["dimensiones"], "preguntas": g["preguntas"]})
    check(r.status_code == 201 and r.json()["estado"] == "borrador" and r.json()["dimensiones"] == g["dimensiones"],
          "la propuesta se guarda tal cual como borrador")

    print("\n--- Fase 3 · Motor de cálculos ---")
    preguntas = [
        {"texto": "Mi jefe me apoya", "tipo": "escala", "dimension": "Liderazgo"},
        {"texto": "Recibo retroalimentación", "tipo": "escala", "dimension": "Liderazgo"},
        {"texto": "Mi equipo colabora", "tipo": "escala", "dimension": "Equipo"},
        {"texto": "Hay confianza en el equipo", "tipo": "escala", "dimension": "Equipo"},  # nadie la responde
        {"texto": "¿Te quedarías un año más?", "tipo": "opcion", "dimension": "Permanencia", "opciones": ["Sí", "No", "No sé"]},
        {"texto": "¿Qué mejorarías?", "tipo": "abierta", "dimension": "Comentarios"},
        {"texto": "Tengo equilibrio vida-trabajo", "tipo": "escala", "dimension": "Bienestar"},  # dimensión sin respuestas
    ]
    r = client.post("/clima/mediciones", json={"titulo": "Motor", "preguntas": preguntas, "permite_externos": True,
                                               "cierra_en": "2099-12-31T18:00"})
    MED = r.json()["id"]
    vacio = client.get(f"/clima/mediciones/{MED}/resultados").json()["calculo"]
    check(vacio["indice"]["estado"] == "sin_respuestas" and vacio["indice"]["valor"] is None
          and vacio["indice"]["etiqueta"] == "Aún no hay respuestas reales", "sin respuestas reales: «Aún no hay respuestas reales», nunca 0 %")
    client.post(f"/clima/mediciones/{MED}/prueba/responder", json={"respuestas": {"p1": 1, "p2": 1, "p3": 1}})
    client.patch(f"/clima/mediciones/{MED}/estado", json={"estado": "abierta"})
    client.post(f"/clima/mediciones/{MED}/invitar", json={"colaborador_ids": ["COL-1", "COL-2", "COL-3", "COL-4"]})
    db.expire_all()
    tokens = [p.token for p in db.query(MedicionClima).filter_by(codigo=MED).one().participaciones]
    for tok, resp in zip(tokens, [
        {"p1": 5, "p2": 3, "p3": 4, "p5": "Sí", "p6": "Más capacitación"},
        {"p1": 4, "p2": 2, "p3": 2, "p5": "No"},
        {"p1": 2, "p2": 5, "p3": 5, "p6": "Mejor comunicación"},
    ]):
        check(client.post(f"/clima/publica/{tok}/responder", json={"respuestas": resp}).status_code == 201, "un invitado responde con su liga personal")
    externa = db.query(MedicionClima).filter_by(codigo=MED).one().token
    client.post(f"/clima/publica/{externa}/responder", json={"respuestas": {"p1": 1, "p2": 1, "p3": 1}})
    c = client.get(f"/clima/mediciones/{MED}/resultados").json()["calculo"]
    dims = {d["nombre"]: d for d in c["dimensiones"]}
    lid = {q["texto"]: q for q in dims["Liderazgo"]["preguntas"]}
    check(c["participacion"] == {"invitados": 4, "respondieron": 3, "faltan": 1, "porcentaje": 75.0}, f"participación 3/4 = 75 % ({c['participacion']})")
    check(c["resumenParticipacion"].startswith("3 de 4 respondieron · faltan 1 · cierra el 31/12/2099"), f"texto dinámico: {c['resumenParticipacion']}")
    check(c["respuestasConsideradas"] == 3 and c["externas"] == 1 and c["pruebas"] == 1, "solo cuentan las 3 reales; la externa y la de prueba van aparte")
    check(lid["Mi jefe me apoya"]["favorable"] == 66.7 and lid["Recibo retroalimentación"]["favorable"] == 33.3,
          "% favorable = respuestas 4 o 5 / respuestas válidas (2/3 y 1/3)")
    check(dims["Liderazgo"]["favorable"] == 50.0, "dimensión = promedio de los % de sus preguntas (66.7 y 33.3 → 50)")
    check(dims["Equipo"]["favorable"] == 66.7 and dims["Equipo"]["preguntasConResultado"] == 1, "la pregunta sin respuestas se omite del promedio de su dimensión")
    check(dims["Bienestar"]["favorable"] is None and dims["Permanencia"]["favorable"] is None and dims["Comentarios"]["favorable"] is None,
          "dimensiones sin escala con respuestas quedan sin resultado (no 0 %)")
    check(c["indice"] == {"valor": 58.3, "estado": "calculado", "etiqueta": "Índice de clima", "dimensionesConsideradas": 2},
          f"índice = promedio simple de las dimensiones con resultado ((50 + 66.7) / 2 = 58.3): {c['indice']}")
    per = dims["Permanencia"]["preguntas"][0]
    com = dims["Comentarios"]["preguntas"][0]
    check(per["distribucion"] == {"Sí": 1, "No": 1, "No sé": 0} and "favorable" not in per, "opción múltiple: distribución, sin convertir a puntaje")
    check(set(com["comentarios"]) == {"Más capacitación", "Mejor comunicación"} and "favorable" not in com, "abiertas: comentarios tal cual, sin autor ni puntaje")
    cp = client.get(f"/clima/mediciones/{MED}/resultados?prueba=true").json()["calculo"]
    check(cp["fuente"] == "prueba" and cp["respuestasConsideradas"] == 1 and cp["indice"]["valor"] == 0.0,
          "la vista de prueba usa SOLO las respuestas de prueba (1 respuesta, todo desfavorable)")
    r = client.post("/clima/mediciones", json={"titulo": "Solo comentarios", "preguntas": [{"texto": "¿Algo que decir?", "tipo": "abierta"}], "permite_externos": True})
    MED2 = r.json()["id"]
    client.patch(f"/clima/mediciones/{MED2}/estado", json={"estado": "abierta"})
    client.post(f"/clima/mediciones/{MED2}/invitar", json={"colaborador_ids": ["COL-1"]})
    db.expire_all()
    tok2 = db.query(MedicionClima).filter_by(codigo=MED2).one().participaciones[0].token
    client.post(f"/clima/publica/{tok2}/responder", json={"respuestas": {"p1": "Todo bien"}})
    i2 = client.get(f"/clima/mediciones/{MED2}/resultados").json()["calculo"]["indice"]
    check(i2 == {"valor": None, "estado": "pendiente", "etiqueta": "Índice pendiente"}, f"con respuestas pero sin escalas calculables: «Índice pendiente» ({i2})")

    print("\n--- Fase 2b · Analizar resultados con Red Human (a demanda) ---")
    r = client.post("/clima/mediciones", json={"titulo": "Sin respuestas", "preguntas": [{"texto": "X", "tipo": "escala"}]})
    check(client.post(f"/clima/mediciones/{r.json()['id']}/analizar").status_code == 409, "sin respuestas reales no se analiza (409)")
    check(client.get(f"/clima/mediciones/{MED}/resultados").json()["analisis"] is None, "responder NO genera hallazgos: no hay análisis hasta que RH lo pide")
    r = client.post(f"/clima/mediciones/{MED}/analizar")
    a = r.json()
    check(r.status_code == 200, f"«Analizar resultados» responde ({r.status_code})")
    check(a["fecha"] and a["respuestasConsideradas"] == 3 and a["estado"] in ("Favorable", "En observación", "Requiere atención"),
          f"trae fecha, respuestas consideradas (3) y estado ({a['estado']})")
    check(all(a[k] for k in ("fortalezas", "focosAtencion", "puntosPorValidar", "accionesSugeridas")),
          "trae fortalezas, focos de atención, puntos por validar y acciones sugeridas")
    check(a["alcance"] == "preliminar" and a["estadoMedicion"] == "abierta" and a["ia"] is False, "con la medición abierta el análisis es PRELIMINAR (demo sin clave)")
    check(a["indice"] == 58.3, "el análisis usa las mismas cifras del motor (índice 58.3)")
    check(any("Liderazgo" in x or "58.3" in x for x in a["focosAtencion"] + [a["resumen"]]), "el diagnóstico cita las cifras reales del motor")
    client.post(f"/clima/mediciones/{MED}/analizar")
    db.expire_all()
    med = db.query(MedicionClima).filter_by(codigo=MED).one()
    check(len(med.analisis) == 2 and client.get(f"/clima/mediciones/{MED}/resultados").json()["analisis"]["fecha"] == med.analisis[-1]["fecha"],
          "cada análisis queda en el historial; resultados muestra el más reciente")
    check("COL-" not in str(med.analisis) and "Persona" not in str(med.analisis), "el análisis no contiene identidades")

    print("\n--- Fase 4 · Envío, recordatorios, plantillas y cierre automático ---")
    d = client.get("/clima/destinatarios").json()
    check(d["areas"] == ["Operaciones", "Ventas"] and d["sedes"] == ["CDMX", "Puebla"] and len(d["colaboradores"]) == 4,
          "el modal recibe las áreas y sedes REALES del roster")
    d = client.get("/clima/destinatarios?areas=Ventas&sedes=CDMX").json()
    check([c["id"] for c in d["colaboradores"]] == ["COL-1"], "filtro por área y sede")
    r = client.post("/clima/mediciones", json={"titulo": "Envío", "preguntas": [{"texto": "Me siento bien", "tipo": "escala"}]})
    ENV = r.json()["id"]
    check(client.post(f"/clima/mediciones/{ENV}/abrir", json={"areas": ["Ventas"], "cierra_en": "2000-01-01T00:00"}).status_code == 400,
          "no se abre con fecha de cierre en el pasado")
    r = client.post(f"/clima/mediciones/{ENV}/abrir", json={"areas": ["Ventas"], "cierra_en": "2099-06-30T17:00", "anonima": False})
    check(r.status_code == 200 and r.json()["medicion"]["estado"] == "abierta" and not r.json()["medicion"]["anonima"]
          and len(r.json()["invitados"]) == 2, "«Enviar»: abre con modalidad Identificada, cierre y los 2 de Ventas invitados")
    check(client.post(f"/clima/mediciones/{ENV}/abrir", json={"cierra_en": "2099-06-30T17:00"}).status_code == 409, "solo un borrador se envía")
    check(client.patch(f"/clima/mediciones/{ENV}", json={"cierra_en": "2099-07-15T17:00"}).json()["cierraEn"].startswith("2099-07-15"),
          "abierta: la fecha de cierre se puede ampliar")
    check(client.patch(f"/clima/mediciones/{ENV}", json={"cierra_en": "2000-01-01T00:00"}).status_code == 400, "…pero no hacia el pasado")
    db.expire_all()
    env = db.query(MedicionClima).filter_by(codigo=ENV).one()
    client.post(f"/clima/publica/{env.participaciones[0].token}/responder", json={"respuestas": {"p1": 5}})
    db.expire_all()
    env = db.query(MedicionClima).filter_by(codigo=ENV).one()
    guardada = env.respuestas[0]
    check(guardada.colaborador_id is not None, "identificada: la respuesta sí queda ligada al colaborador (lo sabía al responder)")
    r = client.post(f"/clima/mediciones/{ENV}/recordatorio").json()
    check(r["recordados"] == 1, "el recordatorio va SOLO a quien no ha respondido")
    client.post(f"/clima/publica/{env.participaciones[1].token}/responder", json={"respuestas": {"p1": 4}})
    check(client.post(f"/clima/mediciones/{ENV}/recordatorio").status_code == 409, "si todos respondieron no hay a quién recordar")

    base = {"nombre": "Clima base", "dimensiones": ["Liderazgo", "Bienestar"], "preguntas": [
        {"texto": "Mi jefe me escucha", "tipo": "escala", "dimension": "Liderazgo", "orden": 2},
        {"texto": "¿Qué valoras más?", "tipo": "opcion", "dimension": "Bienestar", "opciones": ["Equipo", "Horario"], "orden": 1},
    ]}
    r = client.post("/clima/plantillas", json=base)
    PL = r.json()["id"]
    check(r.status_code == 201 and r.json()["tipos"] == ["escala", "opcion"], "crear plantilla de clima")
    orig = client.get(f"/clima/plantillas/{PL}").json()["cuestionario"]
    check([q["texto"] for q in orig] == ["¿Qué valoras más?", "Mi jefe me escucha"], "la plantilla respeta el orden numérico")
    r = client.post(f"/clima/plantillas/{PL}/usar")
    USO = r.json()["id"]
    check(r.status_code == 201 and r.json()["estado"] == "borrador" and r.json()["cuestionario"] == orig and r.json()["dimensiones"] == ["Liderazgo", "Bienestar"],
          "«Usar plantilla»: borrador con copia exacta de dimensiones, tipos y orden")
    client.patch(f"/clima/mediciones/{USO}", json={"preguntas": [{"texto": "Otra cosa", "tipo": "abierta"}]})
    check(client.get(f"/clima/plantillas/{PL}").json()["cuestionario"] == orig, "editar el borrador NO altera la plantilla original")
    client.patch(f"/clima/plantillas/{PL}", json={"nombre": "Clima base 2027"})
    check(client.get(f"/clima/mediciones/{USO}").json()["cuestionario"][0]["texto"] == "Otra cosa", "editar la plantilla NO altera las mediciones ya creadas")
    csv = "dimension,pregunta,tipo,opciones,orden\nLiderazgo,Mi jefe reconoce mi trabajo,escala,,1\nBienestar,¿Te gusta tu horario?,opción múltiple,Sí | No | A veces,2\nComentarios,¿Qué cambiarías?,abierta,,3\n"
    r = client.post("/clima/plantillas/importar", files={"archivo": ("pulso.csv", csv.encode("utf-8"), "text/csv")})
    check(r.status_code == 201 and r.json()["nombre"] == "pulso" and r.json()["preguntas"] == 3 and r.json()["dimensiones"] == ["Liderazgo", "Bienestar", "Comentarios"],
          "«Subir plantilla» desde CSV: dimensiones, tipos y orden")
    antes = len(client.get("/clima/plantillas").json())
    r = client.post("/clima/plantillas/importar", files={"archivo": ("mala.csv", "pregunta,tipo\nHola,raro\n".encode(), "text/csv")})
    check(r.status_code == 422 and len(client.get("/clima/plantillas").json()) == antes, "un archivo con filas malas no guarda nada y dice qué fila falla")
    check(client.get("/clima/plantillas/formato").status_code == 200, "CSV de formato descargable")
    client.delete(f"/clima/plantillas/{PL}")
    check(all(p["id"] != PL for p in client.get("/clima/plantillas").json()) and client.post(f"/clima/plantillas/{PL}/usar").status_code == 409,
          "eliminar = desactivar: ya no se lista ni se usa")

    from app.services.clima_cierre import cerrar_vencidas  # noqa: E402
    from datetime import datetime, timedelta, timezone  # noqa: E402

    env = db.query(MedicionClima).filter_by(codigo=ENV).one()
    env.cierra_en = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    check(cerrar_vencidas(db) == 1, "el job de cierre encuentra la medición vencida")
    db.expire_all()
    env = db.query(MedicionClima).filter_by(codigo=ENV).one()
    check(env.estado == "cerrada" and env.cerrada_por == "sistema", "Abierta → Cerrada automáticamente al llegar la fecha de cierre")
    check(cerrar_vencidas(db) == 0, "el job es idempotente")

print(f"\n🎉 Clima v2 verificado: {OK} comprobaciones OK.")
