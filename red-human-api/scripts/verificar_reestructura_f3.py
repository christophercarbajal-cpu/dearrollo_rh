"""Verificación REESTRUCTURA · FASE 3 (2026-09-16) — módulo universal de Capacitación: generar (tema, contexto,
adjuntos, duración) → objetivo + módulos + evaluación integrada; publicar; asignación universal (colaborador,
candidato como filtro de la vacante, externo por liga abierta); cursar módulo por módulo y evaluación una
pregunta a la vez; resultado Aprobado/No aprobado + % guardado donde corresponde; tablero único con filtros.
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_reestructura_f3.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_rf3_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "rf3.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["SEMBRAR_DEMO"] = "false"
os.environ["ADMIN_PASSWORD"] = "prueba-rf3"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Colaborador, Cuenta, Postulacion, Usuario, UsuarioCuenta  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402
import app.routers.capacitacion as rcap  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


ENVIOS = []


async def _fake_wa(telefono, texto):
    ENVIOS.append({"canal": "whatsapp", "destino": telefono, "texto": texto})
    return {"enviado": True, "proveedor": "meta"}


async def _fake_correo(destino, asunto, html):
    ENVIOS.append({"canal": "correo", "destino": destino, "texto": html})
    return {"enviado": True, "proveedor": "resend"}


rcap.enviar_mensaje = _fake_wa
rcap.enviar_texto_sin_plantilla = _fake_wa  # 2026-09-18: colaboradores reciben texto libre sin plantilla
rcap.enviar_correo = _fake_correo

PDF_MIN = b"%PDF-1.4\n" + b"%" * 600 + b"\n%%EOF\n"

# Cambio 28: con prefiltro en la ruta, publicar exige al menos 3 indispensables
REQ3 = ["Mayor de edad", "Disponibilidad de horario", "Saber leer y escribir"]
with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta RF3", nombre_comercial="RF3 Retail", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    col = Colaborador(codigo="COL-9001", nombre="Marta Ruiz", correo="marta@rf3.mx", telefono="5511110001", puesto="Cajera", cuenta_id=cuenta.id)
    db.add(col)
    cfg = obtener(db)
    cfg.modo_prueba = True
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    print("\n--- Generar: solo tema, contexto, adjuntos y duración ---")
    r = client.post("/capacitacion/generar", data={"tema": "Atención al cliente en sucursal", "duracion_horas": "2", "contexto": "Para cajeros nuevos, tono cercano"},
                    files=[("archivos", ("manual.txt", b"Saludar al cliente. Escuchar. Resolver rapido.", "text/plain")), ("archivos", ("guia.pdf", PDF_MIN, "application/pdf"))])
    check(r.status_code == 201, f"POST /capacitacion/generar → {r.status_code}")
    c = r.json()
    CUR = c["id"]
    check(c["estado"] == "Borrador" and c["objetivo"] and c["categoria"], "curso en Borrador con objetivo y categoría generados")
    check(len(c["listaModulos"]) >= 2 and all(m["contenido"] for m in c["listaModulos"]), f"{len(c['listaModulos'])} módulos con contenido")
    check(len(c["evaluacion"]) >= 3 and all(q["tipo"] in ("opcion", "vf") and 0 <= q["correcta"] < len(q["opciones"]) for q in c["evaluacion"]), f"evaluación integrada con {len(c['evaluacion'])} preguntas válidas (opción múltiple / V-F)")
    check(not any(m["titulo"].lower().startswith("evaluaci") for m in c["listaModulos"]), "la evaluación NO es un módulo aparte")
    check(c["adjuntos"] == ["manual.txt", "guia.pdf"] and c["contexto"].startswith("Para cajeros"), "adjuntos y contexto guardados")
    r = client.post("/capacitacion/generar", data={"tema": "", "duracion_horas": "2"})
    check(r.status_code in (400, 422), f"sin tema → {r.status_code} (rechazado)")

    print("\n--- Editar solo si RH quiere; publicar ---")
    r = client.patch(f"/capacitacion/{CUR}", json={"calificacion_minima": 80, "objetivo": "Objetivo editado por RH"})
    check(r.status_code == 200 and r.json()["calificacionMinima"] == 80 and r.json()["objetivo"] == "Objetivo editado por RH", "PATCH objetivo y mínimo aprobatorio")
    r = client.patch(f"/capacitacion/{CUR}", json={"evaluacion": [{"pregunta": "¿Se saluda al cliente?", "tipo": "vf", "opciones": [], "correcta": 0},
                                                                   {"pregunta": "¿Qué hacer ante una queja?", "tipo": "opcion", "opciones": ["Escuchar y resolver", "Ignorar", "Discutir"], "correcta": 0, "explicacion": "Escuchar primero."},
                                                                   {"pregunta": "El cliente siempre debe esperar.", "tipo": "vf", "opciones": [], "correcta": 1},
                                                                   {"pregunta": "¿Cuál es la prioridad en caja?", "tipo": "opcion", "opciones": ["Exactitud", "Rapidez sin exactitud", "Platicar"], "correcta": 0}]})
    check(r.status_code == 200 and r.json()["preguntas"] == 4 and r.json()["evaluacion"][0]["opciones"] == ["Verdadero", "Falso"], "PATCH evaluación: V/F normaliza opciones; 4 preguntas")
    r = client.patch(f"/capacitacion/{CUR}", json={"evaluacion": [{"pregunta": "Mala", "tipo": "opcion", "opciones": ["solo una"], "correcta": 0}]})
    check(r.status_code == 400, "pregunta inválida → 400")
    r = client.post(f"/capacitacion/{CUR}/asignar", json={"colaborador_ids": ["COL-9001"]})
    check(r.status_code == 409, "asignar un Borrador → 409")
    r = client.patch(f"/capacitacion/{CUR}/publicar")
    check(r.status_code == 200 and r.json()["estado"] == "Publicado", "publicar")

    print("\n--- Asignación universal ---")
    r = client.post("/vacantes", json={"titulo": "Cajero RF3", "descripcion": "x", "generar_si_falta": False, "requisitos_indispensables": REQ3, "publicar": True})
    VAC = r.json()["id"]
    r = client.post("/candidatos", json={"nombre": "Ana Cand", "telefono": "5522220002", "correo": "ana@rf3.mx", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    ENVIOS.clear()
    r = client.post(f"/capacitacion/{CUR}/asignar", json={"colaborador_ids": ["COL-9001"], "postulacion_ids": [P], "externos": [{"nombre": "Pedro Proveedor", "correo": "pedro@prov.mx", "organizacion": "Proveedor X"}, {}]})
    check(r.status_code == 201 and len(r.json()["asignaciones"]) == 4, "un mismo curso → colaborador + candidato + externo con datos + liga abierta (4 asignaciones)")
    asigs = {a["tipo"] + ("-abierta" if a["tipo"] == "externo" and not a["persona"].strip("()").startswith("Pedro") and "externo sin registrar" in a["persona"] else ""): a for a in r.json()["asignaciones"]}
    check({a["tipo"] for a in r.json()["asignaciones"]} == {"colaborador", "candidato", "externo"}, "los tres tipos de persona")
    check(sum(1 for e in ENVIOS if e["canal"] == "whatsapp") >= 2 and sum(1 for e in ENVIOS if e["canal"] == "correo") >= 3, f"se mandó la liga por WhatsApp/correo a quien tiene datos ({len(ENVIOS)} envíos)")
    liga_abierta = next(a for a in r.json()["asignaciones"] if a["tipo"] == "externo" and not a["persona"] or a["persona"].startswith("("))
    tok_col = next(a for a in r.json()["asignaciones"] if a["tipo"] == "colaborador")["token"]
    tok_cand = next(a for a in r.json()["asignaciones"] if a["tipo"] == "candidato")["token"]
    tok_ext = liga_abierta["token"]
    r = client.post(f"/capacitacion/{CUR}/asignar", json={"colaborador_ids": ["COL-9001"]})
    check(r.status_code == 201 and r.json()["asignaciones"][0]["token"] == tok_col, "reasignar al mismo colaborador reutiliza la asignación (idempotente)")

    print("\n--- Cursar: módulo por módulo y evaluación una pregunta a la vez ---")
    r = client.get(f"/capacitacion/publica/{tok_col}")
    d = r.json()
    check(r.status_code == 200 and d["persona"] == "Marta Ruiz" and d["totalPreguntas"] == 4 and d["pregunta"] is None and not d["requiereRegistro"], "sala pública del colaborador: módulos visibles, evaluación aún no")
    check(all("correcta" not in m for m in d["modulos"]) and "evaluacion" not in d, "la sala NUNCA expone las respuestas correctas")
    r = client.post(f"/capacitacion/publica/{tok_col}/responder", json={"indice": 0, "respuesta": 0})
    check(r.status_code == 409, "responder antes de terminar los módulos → 409")
    r = client.post(f"/capacitacion/publica/{tok_col}/avanzar", json={"modulo": 2})
    check(r.status_code == 409, "saltarse un módulo → 409 (en orden)")
    total = d["totalModulos"]
    for i in range(1, total + 1):
        r = client.post(f"/capacitacion/publica/{tok_col}/avanzar", json={"modulo": i})
        check(r.status_code == 200 and r.json()["modulosCompletados"] == i, f"módulo {i}/{total} completado")
    d = r.json()
    check(d["estado"] == "en_curso" and d["pregunta"] and d["pregunta"]["indice"] == 0 and "opciones" in d["pregunta"], "al terminar los módulos aparece la pregunta 1 (una por pantalla)")
    r = client.post(f"/capacitacion/publica/{tok_col}/responder", json={"indice": 1, "respuesta": 0})
    check(r.status_code == 409, "contestar fuera de orden → 409")
    respuestas = [0, 0, 1, 0]  # todas correctas
    for i, resp in enumerate(respuestas):
        r = client.post(f"/capacitacion/publica/{tok_col}/responder", json={"indice": i, "respuesta": resp})
        check(r.status_code == 200 and r.json()["correcta"] is True, f"pregunta {i + 1} respondida (correcta)")
    d = r.json()
    check(d["terminado"] and d["resultado"]["calificacion"] == 100 and d["resultado"]["aprobado"] is True and d["estado"] == "completado", "resultado: Aprobado 100% (mínimo 80)")
    check(len(d["resultado"]["detalle"]) == 4, "detalle por pregunta para la persona")
    r = client.get("/colaboradores/COL-9001")
    check(r.status_code == 200, "el colaborador sigue accesible")
    r = client.get("/capacitacion/asignaciones?tipo=colaborador")
    check(any(a["colaboradorId"] == "COL-9001" and a["aprobado"] is True and a["calificacion"] == 100 for a in r.json()), "historial del colaborador: aprobado 100% en el tablero")

    # candidato: reprueba → resultado en su evaluación (ficha)
    for i in range(1, total + 1):
        client.post(f"/capacitacion/publica/{tok_cand}/avanzar", json={"modulo": i})
    for i, resp in enumerate([1, 1, 0, 1]):
        r = client.post(f"/capacitacion/publica/{tok_cand}/responder", json={"indice": i, "respuesta": resp})
    check(r.json()["terminado"] and r.json()["resultado"]["calificacion"] == 0 and r.json()["resultado"]["aprobado"] is False, "candidato: No aprobado 0%")
    r = client.get(f"/candidatos/{P}")
    check(r.json()["capacitacion"] and r.json()["capacitacion"][0]["aprobado"] is False and r.json()["capacitacion"][0]["curso"] == CUR, "el resultado queda en la evaluación del candidato (ficha)")

    # externo con liga abierta: registro con nombre + correo/WA
    r = client.get(f"/capacitacion/publica/{tok_ext}")
    check(r.json()["requiereRegistro"] is True, "liga abierta: pide registro")
    r = client.post(f"/capacitacion/publica/{tok_ext}/avanzar", json={"modulo": 1})
    check(r.status_code == 409, "sin registrarse no se puede empezar")
    r = client.post(f"/capacitacion/publica/{tok_ext}/registro", json={"nombre": "Lucía Externa", "correo": ""})
    check(r.status_code == 400, "registro sin correo ni WhatsApp → 400")
    r = client.post(f"/capacitacion/publica/{tok_ext}/registro", json={"nombre": "Lucía Externa", "telefono": "5533330003"})
    check(r.status_code == 200 and r.json()["persona"] == "Lucía Externa" and not r.json()["requiereRegistro"], "registro con nombre + WhatsApp")
    for i in range(1, total + 1):
        client.post(f"/capacitacion/publica/{tok_ext}/avanzar", json={"modulo": i})
    for i, resp in enumerate([0, 0, 1, 1]):
        r = client.post(f"/capacitacion/publica/{tok_ext}/responder", json={"indice": i, "respuesta": resp})
    check(r.json()["resultado"]["calificacion"] == 75 and r.json()["resultado"]["aprobado"] is False, "externo: 75% < mínimo 80 → No aprobado")
    r = client.get("/capacitacion/asignaciones?tipo=externo")
    check(any(a["persona"] == "Lucía Externa" and a["calificacion"] == 75 and a["telefono"] == "5533330003" for a in r.json()), "resultado del externo guardado en su asignación")

    print("\n--- Curso como filtro de la vacante + tablero ---")
    r = client.patch(f"/vacantes/{VAC}", json={"curso_filtro": CUR})
    check(r.status_code == 200 and r.json()["cursoFiltroId"] == CUR, "vacante con curso de filtro")
    r = client.post("/candidatos", json={"nombre": "Beto Filtro", "telefono": "5544440004", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P2 = r.json()["id"]
    ENVIOS.clear()
    r = client.patch(f"/candidatos/{P2}/etapa?forzar_prueba=true", json={"etapa": "Entrevista IA"})
    check(r.status_code == 200, "candidato queda apto (Entrevista IA)")
    r = client.get(f"/capacitacion/asignaciones?tipo=candidato&curso={CUR}")
    check(any(a["postulacionId"] == P2 for a in r.json()), "al quedar apto se le asigna el curso de filtro automáticamente")
    check(any("Te asignamos el curso" in e["texto"] and "proceso de selección" in e["texto"] for e in ENVIOS), "…y recibe la liga por WhatsApp")
    r = client.get("/capacitacion/asignaciones?estado=completado")
    check(len(r.json()) == 3, "tablero: filtro estado=completado → 3")
    r = client.get("/capacitacion/asignaciones?aprobado=true")
    check(len(r.json()) == 1 and r.json()[0]["tipo"] == "colaborador", "tablero: filtro aprobado=true → 1")
    r = client.get("/capacitacion/kpis")
    check(r.json()["porTipo"] == {"colaborador": 1, "candidato": 2, "externo": 2} and r.json()["tasaAprobacion"] == 33, f"KPIs por tipo y aprobación: {r.json()}")
    r = client.delete(f"/capacitacion/{CUR}")
    check(r.status_code == 200 and not any(x["id"] == CUR for x in client.get("/capacitacion").json()), "archivar: deja de listarse; resultados conservados")
    check(client.get(f"/capacitacion/publica/{tok_col}").status_code == 404, "liga de un curso archivado → 404")

print(f"\n🎉 Reestructura F3 verificada: {OK} comprobaciones OK.")
