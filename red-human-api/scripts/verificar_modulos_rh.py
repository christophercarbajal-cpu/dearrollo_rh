"""Verificación BLOQUE 3 (2026-09-22): andamiaje de Desempeño, Clima y permisos de la Base de Conocimiento.

REGLA DE ORO: `colaboradores` es la base maestra. Ningún módulo captura personas ni crea otra tabla de
usuarios internos — se comprueba aquí (modelos y endpoints) además del flujo de cada módulo.
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_modulos_rh.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_mod_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "mod.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-mod"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import clima as rclima  # noqa: E402
from app.models import (  # noqa: E402
    TABLAS_MODULOS_RH,
    Colaborador,
    Cuenta,
    DocumentoConocimiento,
    MedicionClima,
    RespuestaClima,
    Usuario,
    UsuarioCuenta,
    puede_ver_conocimiento,
)

OK = 0
ENVIOS = []


async def _fake_correo(destino, asunto, html, adjuntos=None):
    ENVIOS.append(("correo", destino, asunto))
    return {"enviado": True, "proveedor": "resend", "detalle": 200}


async def _fake_wa(tel, texto, *a, **k):
    ENVIOS.append(("whatsapp", tel, texto))
    return {"enviado": True, "proveedor": "meta", "detalle": 200}


rclima.enviar_correo = _fake_correo
rclima.enviar_texto_sin_plantilla = _fake_wa


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


print("\n--- 0. Regla de oro: ninguna tabla nueva de personas ---")
nuevas = set(TABLAS_MODULOS_RH)
check(nuevas == {"ciclos_desempeno", "evaluaciones_desempeno", "mediciones_clima", "respuestas_clima", "participaciones_clima", "plantillas_clima", "plantillas_desempeno", "acciones_desempeno",
                   "plantillas_onboarding", "tareas_onboarding", "pruebas_psicometricas", "evaluaciones_candidato", "firmas_documentos",
                   "evaluaciones", "eventos_evaluacion"},
      "solo 15 tablas nuevas (Clima v2, Desempeño v2, Onboarding v2, Evaluaciones, Firmas y Evaluaciones unificadas), ninguna de personas")
for nombre in sorted(nuevas):
    columnas = set(Base.metadata.tables[nombre].columns.keys())
    # una tabla de personas tendría datos de contacto propios; `nombre`/`titulo` describen al ciclo o a
    # la medición, no a un individuo. `externo_*` de clima es un dato DE LA RESPUESTA (no un padrón).
    contacto = {c for c in columnas if c in ("correo", "telefono", "wa_id", "curp", "rfc")}
    check(not contacto, f"{nombre}: sin datos de contacto propios (no es un padrón de personas)")
    if "colaborador_id" in columnas:
        check(True, f"{nombre}: la persona se referencia con colaborador_id (roster maestro)")
fks = {str(fk.column) for t in (Base.metadata.tables[n] for n in nuevas) for fk in t.foreign_keys}
check(any("colaboradores.id" in f for f in fks), "Desempeño y Clima apuntan a colaboradores.id (base maestra)")
check(not any("cuentas.id" in f for f in fks), "cuenta_id sin FK a cuentas (hotfix 2026-09-18)")

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta RH", nombre_comercial="RH Demo", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.add(Colaborador(codigo="COL-1", cuenta_id=cuenta.id, nombre="Ana Ventas", correo="ana@x.mx", telefono="5511110001", puesto="Cajera", area="Ventas"))
    db.add(Colaborador(codigo="COL-2", cuenta_id=cuenta.id, nombre="Beto Almacén", correo="beto@x.mx", telefono="5511110002", puesto="Almacenista", area="Operaciones"))
    db.add(Colaborador(codigo="COL-3", cuenta_id=cuenta.id, nombre="Caro Baja", correo="caro@x.mx", telefono="5511110003", puesto="Cajera", area="Ventas", activo=False))
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    print("\n--- 1. Desempeño: crear (con IA) → participantes → evaluar → resultados ---")
    r = client.post("/desempeno/ciclos/generar", json={"puesto": "Cajera", "periodo": "2026-S2"})
    check(r.status_code == 200 and r.json()["objetivos"] and r.json()["kpis"], "«Crear evaluación con IA» propone objetivos y KPIs editables")
    propuesta = r.json()
    check(all("peso" in o for o in propuesta["objetivos"]), "los objetivos traen peso para ponderar")
    r = client.post("/desempeno/ciclos", json={"nombre": "Desempeño 2026-S2", "periodo": "2026-S2", "puesto_objetivo": "Cajera",
                                               "objetivos": [{"titulo": "Cumplir responsabilidades", "peso": 60}],
                                               "kpis": [{"nombre": "Calidad", "meta": "95%", "peso": 40}], "generado_con_ia": propuesta["generadoConIa"]})
    check(r.status_code == 201 and r.json()["id"].startswith("DES-"), f"ciclo creado ({r.status_code})")
    CICLO = r.json()["id"]
    check(client.post("/desempeno/ciclos", json={"nombre": "Sin nada"}).status_code == 400, "un ciclo sin objetivos ni KPIs se rechaza")
    r = client.post(f"/desempeno/ciclos/{CICLO}/participantes", json={"colaborador_ids": ["COL-1", "COL-2", "COL-3", "NO-EXISTE"]})
    check(r.status_code == 201, f"participantes agregados ({r.status_code})")
    check(len(r.json()["evaluaciones"]) == 2 and "COL-3" in r.json()["noEncontrados"] and "NO-EXISTE" in r.json()["noEncontrados"],
          "solo entran colaboradores ACTIVOS del roster (el inactivo y el inexistente quedan fuera)")
    check(r.json()["ciclo"]["estado"] == "borrador", "agregar participantes NO inicia la evaluación (Desempeño v2)")
    EV1, EV2 = [e["id"] for e in r.json()["evaluaciones"]]
    r2 = client.post(f"/desempeno/ciclos/{CICLO}/participantes", json={"colaborador_ids": ["COL-1"]})
    check(len(r2.json()["evaluaciones"]) == 1 and r2.json()["evaluaciones"][0]["id"] == EV1, "agregar dos veces a la misma persona NO duplica su evaluación")
    ev = client.get(f"/desempeno/evaluaciones/{EV1}").json()
    check(ev["colaborador"] and ev["puesto"] == "Cajera" and ev["area"] == "Ventas", "la ficha toma nombre, puesto y área del roster (no se recapturan)")
    check(client.post(f"/desempeno/ciclos/{CICLO}/iniciar").json()["estado"] == "en_curso", "RH inicia la evaluación (Borrador → En curso)")
    r = client.patch(f"/desempeno/evaluaciones/{EV1}", json={
        "resultados": [{"tipo": "objetivo", "nombre": "Cumplir responsabilidades", "meta": "100%", "real": "90%", "logro": 90, "peso": 60},
                       {"tipo": "kpi", "nombre": "Calidad", "meta": "95%", "real": "70%", "logro": 70, "peso": 40}],
        "brechas": [{"tema": "Atención a cliente", "brecha": "Quejas por trato", "accion_sugerida": "Curso de servicio"}],
        "comentarios": "Buen periodo, con foco en calidad.", "conclusion": "Buen periodo.", "fortalezas": ["Cumplir responsabilidades"], "completar": True,
    })
    check(r.status_code == 200 and r.json()["estado"] == "completada", "evaluación capturada y completada")
    check(r.json()["calificacion"] == 82.0, f"calificación ponderada (90×60 + 70×40)/100 = 82 → {r.json()['calificacion']}")
    client.patch(f"/desempeno/evaluaciones/{EV2}", json={"resultados": [{"tipo": "objetivo", "nombre": "Cumplir responsabilidades", "logro": 60},
                                                                     {"tipo": "kpi", "nombre": "Calidad", "logro": 60}],
                                                         "brechas": [{"tema": "Atención a cliente", "brecha": "Tiempos de respuesta", "accion_sugerida": "Curso de servicio"}],
                                                         "conclusion": "Periodo regular.", "completar": True})
    r = client.get(f"/desempeno/ciclos/{CICLO}/resultados").json()
    check(r["total"] == 2 and r["completadas"] == 2 and r["avance"] == 100, "avance del ciclo")
    check(r["promedio"] == 71.0 and r["ranking"][0]["id"] == EV1, f"promedio y ranking ({r['promedio']})")
    check(r["brechas"][0]["tema"] == "Atención a cliente" and r["brechas"][0]["personas"] == 2, "las brechas se agrupan por tema (insumo de Capacitación)")

    print("\n--- 2. Clima: medición anónima, liga pública y resultados ---")
    r = client.post("/clima/mediciones", json={
        "titulo": "Clima 2026", "anonima": True, "permite_externos": False,
        "preguntas": [{"texto": "¿Qué tan a gusto te sientes en tu equipo?", "tipo": "escala", "escala_max": 5},
                      {"texto": "¿Qué mejorarías?", "tipo": "abierta"},
                      {"texto": "¿Cómo calificas la comunicación?", "tipo": "opcion", "opciones": ["Buena", "Regular", "Mala"]}],
    })
    check(r.status_code == 201 and r.json()["id"].startswith("CLI-") and "/clima/" in r.json()["liga"], f"medición creada con liga pública ({r.status_code})")
    MED = r.json()["id"]
    TOKEN = client.get(f"/clima/mediciones/{MED}").json()["liga"].rsplit("/", 1)[-1]
    check(client.post("/clima/mediciones", json={"titulo": "Mala", "preguntas": [{"texto": "X", "tipo": "opcion", "opciones": ["Una"]}]}).status_code == 400, "una pregunta de opción con menos de 2 opciones se rechaza")
    r = client.post(f"/clima/mediciones/{MED}/responder", json={"respuestas": {"p1": 4}})
    check(r.status_code == 409, "cerrada/borrador no recibe respuestas")
    check(client.patch(f"/clima/mediciones/{MED}/estado", json={"estado": "abierta"}).status_code == 200, "RH la abre")
    check(client.patch(f"/clima/mediciones/{MED}/estado", json={"estado": "borrador"}).status_code == 409, "flujo de ida: una abierta no regresa a borrador")
    check(client.patch(f"/clima/mediciones/{MED}", json={"titulo": "Otro"}).status_code == 409, "abierta: solo se puede mover la fecha de cierre")
    r = client.post(f"/clima/mediciones/{MED}/responder", json={"respuestas": {"p1": 5}, "colaborador_id": "COL-1"})
    check(r.status_code == 409, "RH no captura la respuesta de alguien NO invitado (control de participación)")
    client.post(f"/clima/mediciones/{MED}/invitar", json={"colaborador_ids": ["COL-1"]})
    r = client.post(f"/clima/mediciones/{MED}/responder", json={"respuestas": {"p1": 5, "p2": "Más capacitación", "p3": "Buena"}, "colaborador_id": "COL-1"})
    check(r.status_code == 201 and r.json()["anonima"], "un colaborador invitado responde")
    check(client.post(f"/clima/mediciones/{MED}/responder", json={"respuestas": {"p1": 1}, "colaborador_id": "COL-1"}).status_code == 409,
          "no se aceptan respuestas duplicadas del mismo colaborador")
    db.expire_all()
    guardada = db.query(RespuestaClima).order_by(RespuestaClima.id.desc()).first()
    check(guardada.colaborador_id is None and not guardada.externo_nombre, "ANÓNIMA: no se guarda quién respondió, ni aunque el cliente mande el código")
    check((guardada.enviado_en.hour, guardada.enviado_en.minute, guardada.enviado_en.second) == (0, 0, 0),
          "ANÓNIMA: la respuesta guarda solo el día (no la hora) para no cruzarla con la participación")
    columnas_part = set(Base.metadata.tables["participaciones_clima"].columns.keys())
    check(not {c for c in columnas_part if "respuesta" in c or "respondido_en" in c},
          "la participación no apunta a la respuesta ni guarda cuándo se respondió")
    pub = client.get(f"/clima/publica/{TOKEN}").json()
    check(pub["abierta"] and len(pub["preguntas"]) == 3 and "ANÓNIMAS" in pub["aviso"] and pub["tipoLiga"] == "externa",
          "la liga compartida es la EXTERNA y muestra el cuestionario y el aviso de anonimato")
    r = client.post(f"/clima/publica/{TOKEN}/responder", json={"respuestas": {"p1": 3}, "externo_nombre": "Externo X"})
    check(r.status_code == 403, "sin «permite externos», la liga compartida no acepta respuestas")
    r = client.post(f"/clima/publica/{TOKEN}/responder", json={"respuestas": {"p1": 3, "p3": "Regular"}, "colaborador_id": "COL-2"})
    check(r.status_code == 403, "un colaborador no invitado no entra por la liga compartida escribiendo un código")
    check(client.post(f"/clima/mediciones/{MED}/prueba/responder", json={"respuestas": {"p1": 1, "p2": "prueba"}}).status_code == 201,
          "«Probar encuesta» guarda una respuesta de prueba")
    res = client.get(f"/clima/mediciones/{MED}/resultados").json()
    check(res["totalRespuestas"] == 1 and res["pruebas"] == 1 and res["invitados"] == 1 and res["participacion"] == 100,
          f"la prueba NO se mezcla con lo real; participación sobre invitados ({res['participacion']}%)")
    p1 = next(p for p in res["porPregunta"] if p["id"] == "p1")
    check(p1["promedio"] == 5.0 and p1["escalaMax"] == 5, f"promedio de la escala sin la prueba ({p1['promedio']})")
    p2 = next(p for p in res["porPregunta"] if p["id"] == "p2")
    check(p2["textos"] == ["Más capacitación"] and "autor" not in str(p2), "las respuestas abiertas salen sin autor")
    check(client.delete(f"/clima/mediciones/{MED}/prueba").json()["borradas"] == 1, "reiniciar la prueba borra solo las de prueba")
    # identificada + externos
    r = client.post("/clima/mediciones", json={"titulo": "Pulso proveedores", "anonima": False, "permite_externos": True,
                                               "preguntas": [{"texto": "¿Cómo nos calificas?", "tipo": "escala"}]})
    MED2 = r.json()["id"]
    TOKEN2 = r.json()["liga"].rsplit("/", 1)[-1]
    client.patch(f"/clima/mediciones/{MED2}/estado", json={"estado": "abierta"})
    check(client.post(f"/clima/mediciones/{MED2}/responder", json={"respuestas": {"p1": 5}}).status_code == 400, "identificada sin colaborador → 400")
    r = client.post(f"/clima/publica/{TOKEN2}/responder", json={"respuestas": {"p1": 4}, "externo_nombre": "Proveedor X", "externo_correo": "prov@x.mx"})
    check(r.status_code == 201, "con «permite externos» la liga acepta a alguien de fuera")
    db.expire_all()
    ext = db.query(RespuestaClima).order_by(RespuestaClima.id.desc()).first()
    check(ext.origen == "externo" and ext.es_externa and ext.colaborador_id is None and ext.externo_nombre == "Proveedor X",
          "el externo queda marcado como externo, como dato de la respuesta; NUNCA se crea un colaborador")
    r2 = client.get(f"/clima/mediciones/{MED2}/resultados").json()
    check(r2["totalRespuestas"] == 0 and r2["externos"] == 1 and r2["participacion"] is None, "las externas no suman a la participación ni a los resultados internos")
    check(db.query(Colaborador).filter(Colaborador.cuenta_id == cuenta.id).count() == 3, "el roster sigue con las 3 personas de siempre")

    print("\n--- 3. Base de Conocimiento: permisos por área/puesto del roster ---")
    r = client.post("/conocimiento/documentos", data={"titulo": "Reglamento interno", "tipo": "reglamento",
                                                      "texto": "El horario de comida es de 14:00 a 15:00 horas para todo el personal."})
    check(r.status_code == 201, f"documento de texto indexado ({r.status_code})")
    DOC = (r.json()["documentos"][0]["id"] if isinstance(r.json(), dict) and r.json().get("documentos") else r.json()[0]["id"])
    r = client.post("/conocimiento/documentos", data={"titulo": "Manual de caja", "tipo": "manual",
                                                      "texto": "El corte de caja se hace al cierre del turno y lo firma el supervisor."})
    DOC2 = (r.json()["documentos"][0]["id"] if isinstance(r.json(), dict) and r.json().get("documentos") else r.json()[0]["id"])
    opciones = client.get("/conocimiento/areas").json()
    check(opciones["areas"] == ["Operaciones", "Ventas"] and "Cajera" in opciones["puestos"], "las opciones de permisos salen del roster real")
    r = client.patch(f"/conocimiento/documentos/{DOC2}/permisos", json={"areas": ["Ventas"], "puestos": []})
    check(r.status_code == 200 and r.json()["areas"] == ["Ventas"], "el manual queda restringido al área de Ventas")
    db.expire_all()
    doc2 = db.get(DocumentoConocimiento, DOC2)
    ana = db.query(Colaborador).filter_by(codigo="COL-1").one()
    beto = db.query(Colaborador).filter_by(codigo="COL-2").one()
    check(puede_ver_conocimiento(doc2, ana) and not puede_ver_conocimiento(doc2, beto), "Ana (Ventas) lo ve; Beto (Operaciones) no")
    check(puede_ver_conocimiento(doc2, None), "RH (sin colaborador) ve todo")
    r = client.get("/conocimiento/buscar?q=corte de caja").json()
    check(any("caja" in x["texto"].lower() for x in r["resultados"]), "RH encuentra el manual")
    r = client.post("/conocimiento/preguntar", json={"pregunta": "¿Cómo se hace el corte de caja?", "colaborador_id": "COL-2"}).json()
    check(all(f["documentoId"] != DOC2 for f in r["fragmentos"]), "preguntando como Beto, el manual restringido NO entra como evidencia")
    r = client.post("/conocimiento/preguntar", json={"pregunta": "¿Cómo se hace el corte de caja?", "colaborador_id": "COL-1"}).json()
    check(any(f["documentoId"] == DOC2 for f in r["fragmentos"]), "preguntando como Ana (Ventas), sí entra")
    client.patch(f"/conocimiento/documentos/{DOC}/permisos", json={"publicado": False})
    r = client.post("/conocimiento/preguntar", json={"pregunta": "¿A qué hora es la comida?"}).json()
    check(all(f["documentoId"] != DOC for f in r["fragmentos"]), "un documento NO publicado nunca alimenta la respuesta de la IA")
    check(client.post("/conocimiento/preguntar", json={"pregunta": "x", "colaborador_id": "COL-999"}).status_code == 404, "un colaborador inexistente no se inventa: 404")
    print("\n--- 4. Lo que consumen las pantallas: fortalezas, invitaciones y borrador con IA ---")
    res = client.get(f"/desempeno/ciclos/{CICLO}/resultados").json()
    check([f["tema"] for f in res["fortalezas"]] == ["Cumplir responsabilidades"],
          f"el dashboard recibe las FORTALEZAS CONFIRMADAS por el evaluador (ya no por umbral de 85 %): {[f['tema'] for f in res['fortalezas']]}")
    check(res["fortalezas"][0]["personas"] == 1, "cada fortaleza trae cuántas personas la tienen")

    ENVIOS.clear()
    r = client.post(f"/clima/mediciones/{MED}/invitar", json={"colaborador_ids": ["COL-1", "COL-2", "COL-3"], "mensaje": "Nos ayuda mucho."})
    check(r.status_code == 200, f"«Invitar colaboradores» manda la liga ({r.status_code}: {r.text[:160]})")
    check(len(r.json()["invitados"]) == 2 and "COL-3" in r.json()["noEncontrados"], "solo se invita a colaboradores ACTIVOS del roster")
    check(any(x.get("yaRespondio") for x in r.json()["invitados"]), "a quien ya respondió no se le vuelve a escribir")
    check(any(e[0] == "correo" for e in ENVIOS) and any(e[0] == "whatsapp" for e in ENVIOS), "sale por correo y WhatsApp con lo que cada quien tenga en el roster")
    db.expire_all()
    med = db.query(MedicionClima).filter_by(codigo=MED).one()
    beto_part = next(p for p in med.participaciones if p.colaborador.codigo == "COL-2")
    check(all(beto_part.token in e[2] for e in ENVIOS if e[0] == "whatsapp"), "el mensaje lleva la liga PERSONAL del invitado")
    check(client.get(f"/clima/publica/{beto_part.token}").json()["tipoLiga"] == "personal", "la liga personal se reconoce como personal")
    check(client.post(f"/clima/publica/{beto_part.token}/responder", json={"respuestas": {"p1": 3}}).status_code == 201, "el invitado responde con su liga, sin sesión")
    check(client.post(f"/clima/publica/{beto_part.token}/responder", json={"respuestas": {"p1": 3}}).status_code == 409, "su liga personal no acepta una segunda respuesta")
    res = client.get(f"/clima/mediciones/{MED}/resultados").json()
    check(res["invitados"] == 2 and res["respondieron"] == 2 and res["participacion"] == 100 and res["totalRespuestas"] == 2,
          f"participación = respondieron / invitados ({res['respondieron']}/{res['invitados']})")
    check(client.post(f"/clima/mediciones/{MED}/invitar", json={"colaborador_ids": []}).status_code == 400, "invitar sin nadie seleccionado → 400")
    client.patch(f"/clima/mediciones/{MED2}/estado", json={"estado": "cerrada"})
    check(client.post(f"/clima/mediciones/{MED2}/invitar", json={"colaborador_ids": ["COL-1"]}).status_code == 409, "no se invita a una medición cerrada")
    check(client.patch(f"/clima/mediciones/{MED2}/estado", json={"estado": "abierta"}).status_code == 409, "una medición cerrada nunca se reabre")
    db.expire_all()
    med = db.query(MedicionClima).filter_by(codigo=MED).one()
    check(db.query(RespuestaClima).filter(RespuestaClima.medicion_id == med.id).count() == 2, "invitar NO crea respuestas (la participación vive en su propia tabla)")

    r = client.post("/conocimiento/generar", json={"tema": "Política de home office", "tipo": "politica", "notas": "Aplica a administrativos"})
    check(r.status_code == 200 and r.json()["texto"], f"«Generar con Red Human» regresa un borrador editable ({r.status_code})")
    check(bool(r.json()["avisos"]), "el borrador dice qué falta confirmar antes de publicar")
    check(db.query(DocumentoConocimiento).filter(DocumentoConocimiento.titulo.ilike("%home office%")).count() == 0, "generar NO guarda ni publica nada: RH revisa primero")
    check(client.post("/conocimiento/generar", json={"tema": "  "}).status_code == 400, "sin tema → 400")
    db.close()

print(f"\n🎉 Bloque 3 (Desempeño · Clima · Conocimiento) verificado: {OK} comprobaciones OK.")
