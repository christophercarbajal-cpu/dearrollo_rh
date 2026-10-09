"""Regresión de «Generación de contenido según la ruta» (2026-10-09). Base desechable, SIN red ni OpenAI.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_guiones_vacante.py

1. Almacenamiento: plantillas de conversación POR VACANTE (prefiltro web/WhatsApp, entrevista por WhatsApp, con avatar
   y llamada), solo las de su ruta; edición manual marcada con quién; prefiltros solo cerrados; ajustar la ruta de la
   vacante NO toca la plantilla de la Cuenta; los guiones nunca salen en los payloads públicos.
2. Generación: solo las actividades de la ruta; prefiltros cerrados (eliminatorias = indispensables); WhatsApp reconfirma
   los indispensables del web pidiendo un dato concreto y no repite lo demás; entrevistas/llamada solo preguntas
   abiertas; «Volver a generar» nunca pisa ediciones de RH sin confirmación; datos cambiados → desactualizado.
3. Ejecución: la Entrevista por WhatsApp se conduce en el chat con el guion de la VACANTE (incluida la edición de RH
   hecha después de mandar la invitación) y se evalúa igual que la sala; la Llamada usa la sala en modo llamada; sin guion
   se genera Just-In-Time antes del primer mensaje; una contradicción con respuestas previas es «Inconsistencia» en
   Puntos por validar y nunca descarta.
4. Publicación y avisos: si la ruta no pide CV, ningún texto de publicación dice «envía tu CV» y el formulario público lo
   deja opcional (`pideCv`); los avisos de cumplimiento salen una sola vez y desaparecen al capturar el dato.
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

_dir = tempfile.mkdtemp(prefix="rh_guiones_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "guiones.db").replace("\\", "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET",
          "DROPBOX_SIGN_API_KEY", "DROPBOX_SIGN_CLIENT_ID"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-guiones"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import usuario_actual, usuario_admin, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, PlantillaProceso, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def seccion(det: dict, clave: str) -> dict:
    return next(s for s in det["guiones"]["secciones"] if s["clave"] == clave)


FICHA = {
    "titulo": "Montacarguista", "area": "Almacén", "seniority": "Junior", "ubicacion_estado": "Jalisco",
    "ubicacion_municipio": "Zapopan", "modalidad": "Presencial", "sueldo_desde": 12000, "sueldo_hasta": 14000,
    "sueldo_periodicidad": "mensual", "descripcion": "Operar montacargas en el CEDIS.",
    "requisitos_indispensables": ["2 años de experiencia operando montacargas", "Licencia de montacargas vigente"],
    "requisitos_deseables": ["Manejo de inventarios"], "beneficios": ["Vales de despensa"],
    "responsabilidades": ["Cargar y descargar tráileres", "Acomodar tarimas en rack"],
}

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    obtener(db).modo_prueba = False
    cuenta = Cuenta(nombre="Logística SA", nombre_comercial="Logística", razon_social="Logística SA de CV", estado="Activa", slug="logistica-sa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor, usuario_admin):
        app.dependency_overrides[dep] = lambda: admin
    H = {"X-Cuenta-Id": str(cuenta.id)}
    sproc.asegurar_rutas_base(db, cuenta.id)
    db.commit()

    # ================= 1. Almacenamiento por vacante =================
    print("\n--- 1. Plantillas de conversación por vacante ---")
    ops = {t["valor"]: {**t, "nombre": t["texto"]} for t in client.get("/procesos/opciones", headers=H).json()["tiposPaso"]}
    check({"entrevista_agente", "entrevista_whatsapp", "llamada_agente"} <= set(ops)
          and ops["entrevista_agente"]["nombre"] == "Entrevista Red Human con avatar"
          and ops["entrevista_whatsapp"]["nombre"] == "Entrevista Red Human por WhatsApp" and ops["llamada_agente"]["nombre"] == "Llamada Red Human"
          and all(ops[t]["etapa"] == "Entrevista IA" for t in ("entrevista_agente", "entrevista_whatsapp", "llamada_agente")),
          "catálogo: tres actividades de IA independientes en Filtro Red Human (avatar, WhatsApp, llamada)")
    corp = next(p for p in client.get("/procesos/plantillas", headers=H).json() if p.get("rutaBase") == "corporativo")
    pasos = corp["pasos"] + [{"id": "entrevista-wa", "tipo": "entrevista_whatsapp", "etapa": "Entrevista IA"},
                             {"id": "llamada", "tipo": "llamada_agente", "etapa": "Entrevista IA"}]
    guion_wa = {"enfoque": "Validar experiencia real", "temas": ["Experiencia"], "preguntas": ["Cuéntame de tu último trabajo con montacargas."]}
    web = [{"pregunta": "¿Tienes al menos 2 años operando montacargas?", "tipo": "si_no", "valida": "2 años de experiencia operando montacargas",
            "respuesta_esperada": "Sí", "descarta": True}]
    r = client.post("/vacantes", headers=H, json={**FICHA, "generar_si_falta": False, "preguntas_filtro": web,
                                                 "proceso": {"plantilla_id": corp["id"], "pasos": pasos, "etapas": corp["etapas"]},
                                                 "guiones": {"secciones": {"entrevista_whatsapp": guion_wa}}})
    check(r.status_code == 201, f"alta de vacante con guiones ({r.status_code} {r.text[:200]})")
    VAC = r.json()["id"]
    det = client.get(f"/vacantes/{VAC}", headers=H).json()
    aplican = det["guiones"]["aplican"]
    check(aplican == ["prefiltro_web", "entrevista_whatsapp", "entrevista_avatar", "llamada"],
          f"solo aplican las secciones de las actividades de la ruta (sin Prefiltro · WhatsApp): {aplican}")
    check([s["titulo"] for s in det["guiones"]["secciones"]] == [
        "Prefiltro · Web", "Prefiltro · WhatsApp", "Entrevista Red Human por WhatsApp — guion", "Entrevista con avatar — guion", "Llamada — guion"],
        "las cinco secciones con su nombre")
    s_wa = seccion(det, "entrevista_whatsapp")
    check(s_wa["contenido"]["preguntas"] == guion_wa["preguntas"] and s_wa["editado"] and s_wa["editadoPor"] == admin.nombre,
          "guion escrito a mano: se guarda en la vacante y queda marcado como edición de RH")
    v = db.query(Vacante).filter(Vacante.codigo == VAC).one()
    check(v.preguntas_filtro[0]["pregunta"] == web[0]["pregunta"], "Prefiltro · Web vive en `preguntas_filtro` (una sola fuente para portal y agente)")

    r = client.patch(f"/vacantes/{VAC}", headers=H, json={"preguntas_filtro_whatsapp": [{"pregunta": "Describe tu experiencia", "tipo": "texto_corto"}]})
    check(r.status_code == 400 and "cerradas" in r.json()["detail"], "prefiltro con pregunta abierta → 400 (solo preguntas cerradas)")
    r = client.patch(f"/vacantes/{VAC}", headers=H, json={"guiones": {"secciones": {"llamada": {"preguntas": ["¿Tienes licencia de montacargas?"]}}}})
    llamada = seccion(r.json(), "llamada")["contenido"]
    check(r.status_code == 200 and llamada["preguntas"][0].startswith("Cuéntame") and "?" not in llamada["preguntas"][0][:3],
          "guion de llamada: una pregunta cerrada se reescribe como abierta (nunca «sí/no» en una entrevista)")

    # «Ajustar ruta de esta vacante» no toca la plantilla general
    antes = db.get(PlantillaProceso, corp["id"])
    version_antes, pasos_antes = antes.version, list(antes.pasos)
    r = client.patch(f"/vacantes/{VAC}", headers=H, json={"proceso": {"plantilla_id": corp["id"], "pasos": pasos[:-1], "etapas": corp["etapas"]}})
    db.expire_all()
    pl = db.get(PlantillaProceso, corp["id"])
    check(r.status_code == 200 and pl.version == version_antes and pl.pasos == pasos_antes,
          "ajustar la ruta de la vacante NO altera la plantilla general de la Cuenta")
    check("llamada" not in r.json()["guiones"]["aplican"], "…y las secciones aplicables siguen a la ruta de la vacante (sin Llamada)")

    publico = client.get(f"/vacantes/slug/{r.json()['slug']}").json() if r.json().get("slug") else {}
    pub = client.get("/vacantes/publicas").json()
    pub = pub if isinstance(pub, list) else pub.get("vacantes", [])
    check("guiones" not in publico and all("guiones" not in x for x in pub if isinstance(x, dict)),
          "los guiones nunca salen en los payloads públicos")

    # ================= 2. Generación según la ruta =================
    print("\n--- 2. Generación según la ruta ---")
    import re as _re

    ABIERTA_NO = _re.compile(r"^\s*¿?\s*(tienes|cuentas|has|puedes|estás|estas|vives|eres|cumples)\b", _re.IGNORECASE)
    pasos_corp = corp["pasos"] + [{"id": "prefiltro-wa", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"},
                                  {"id": "entrevista-wa", "tipo": "entrevista_whatsapp", "etapa": "Entrevista IA"}]
    r = client.post("/vacantes/generar", headers=H, json={**FICHA, "proceso": {"pasos": pasos_corp}})
    g = r.json()
    check(r.status_code == 200 and g["guiones"]["aplican"] == ["prefiltro_web", "prefiltro_whatsapp", "entrevista_whatsapp", "entrevista_avatar"],
          f"generar: solo las secciones de la ruta (sin Llamada): {g['guiones'].get('aplican')}")
    check(set(g["guiones"]["secciones"]) == {"entrevista_whatsapp", "entrevista_avatar"} and "llamada" not in g["guiones"]["meta"],
          "no se genera nada para actividades que no están en la ruta")
    web_g, wa_g = g["preguntas_filtro"], g["preguntas_filtro_whatsapp"]
    check(web_g and all(q["tipo"] in ("si_no", "numero", "opcion") and q.get("opciones") for q in web_g + wa_g),
          "prefiltros web y WhatsApp: solo preguntas cerradas")
    indisp = [q for q in web_g if q["descarta"]]
    check(sorted(q["valida"] for q in indisp) == sorted(FICHA["requisitos_indispensables"]),
          "eliminatorias del web = exactamente los requisitos indispensables")
    check(len(wa_g) == len(indisp) and all(q.get("reconfirma") and q["tipo"] in ("numero", "opcion") for q in wa_g)
          and not any("inventarios" in q["valida"].lower() for q in wa_g),
          "WhatsApp: reconfirma CADA indispensable con un dato concreto y no repite lo no indispensable")
    exp = next(q for q in wa_g if "montacargas" in q["valida"] and "años" in q["valida"])
    check(exp["tipo"] == "numero" and "cuánto tiempo" in exp["pregunta"].lower() and exp.get("minimo") == 2,
          f"web «¿tienes al menos 2 años…?» → WhatsApp «¿cuánto tiempo…?» con rangos: {exp['pregunta']}")
    textos_prefiltro = {q["pregunta"] for q in web_g + wa_g}
    guiones_g = g["guiones"]["secciones"]
    check(all(not ABIERTA_NO.match(q) and q not in textos_prefiltro for c in guiones_g.values() for q in c["preguntas"]),
          "entrevistas: solo preguntas abiertas y ninguna pregunta de prefiltro")
    check(all(m.get("generado_hash") and not m.get("editado") for m in g["guiones"]["meta"].values()), "meta de generación por sección")

    r = client.post("/vacantes/generar", headers=H, json={**FICHA, "proceso": {"pasos": [
        {"id": "pw", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"}, {"id": "cond", "tipo": "condiciones", "etapa": "Contratación"}]}})
    solo_wa = r.json()
    check(solo_wa["guiones"]["aplican"] == ["prefiltro_whatsapp"] and solo_wa["preguntas_filtro"] == []
          and all(q["descarta"] and not q.get("reconfirma") for q in solo_wa["preguntas_filtro_whatsapp"]),
          "ruta solo con prefiltro por WhatsApp: es el primer filtro (eliminatorias cerradas), sin web")

    editado = {"enfoque": "Mío", "temas": ["Montacargas"], "preguntas": ["Cuéntame de tu turno más pesado."]}
    r = client.post("/vacantes/generar", headers=H, json={**FICHA, "proceso": {"pasos": pasos_corp}, "conservar": ["entrevista_whatsapp"],
                                                         "guiones_actuales": {"secciones": {"entrevista_whatsapp": editado}}})
    check("entrevista_whatsapp" in r.json()["guiones"]["conservadas"] and "entrevista_whatsapp" not in r.json()["guiones"]["secciones"],
          "«Volver a generar» conservando una edición: esa sección no se toca")

    # alta con lo generado: lo intacto NO cuenta como edición; lo cambiado sí
    secciones_alta = dict(guiones_g)
    secciones_alta["entrevista_avatar"] = {**guiones_g["entrevista_avatar"], "preguntas": guiones_g["entrevista_avatar"]["preguntas"] + ["Cuéntame qué te gustaría aprender aquí."]}
    r = client.post("/vacantes", headers=H, json={**FICHA, "generar_si_falta": False, "proceso": {"pasos": pasos_corp},
                                                 "preguntas_filtro": web_g, "preguntas_filtro_whatsapp": wa_g,
                                                 "guiones": {"secciones": secciones_alta, "meta": g["guiones"]["meta"]}})
    VAC2 = r.json()["id"]
    det2 = r.json()
    check(not seccion(det2, "entrevista_whatsapp")["editado"] and not seccion(det2, "prefiltro_web")["editado"]
          and seccion(det2, "entrevista_avatar")["editado"], "al guardar: lo generado intacto no es edición; lo que RH cambió sí")
    r = client.post(f"/vacantes/{VAC2}/guiones/generar", headers=H, json={})
    check(r.status_code == 409 and "Entrevista con avatar" in r.json()["detail"],
          "volver a generar con ediciones manuales SIN confirmar → 409 (nunca se pisan solas)")
    r = client.post(f"/vacantes/{VAC2}/guiones/generar", headers=H, json={"sobrescribir_editadas": False})
    check(r.status_code == 200 and r.json()["conservadas"] == ["entrevista_avatar"]
          and seccion(r.json(), "entrevista_avatar")["contenido"]["preguntas"][-1] == "Cuéntame qué te gustaría aprender aquí.",
          "«Conservar mis ediciones»: se regenera lo demás y la edición queda")
    r = client.post(f"/vacantes/{VAC2}/guiones/generar", headers=H, json={"sobrescribir_editadas": True})
    check(r.status_code == 200 and not seccion(r.json(), "entrevista_avatar")["editado"]
          and "Cuéntame qué te gustaría aprender aquí." not in seccion(r.json(), "entrevista_avatar")["contenido"]["preguntas"],
          "«Sobrescribir»: la sección vuelve a lo generado")
    check(not r.json()["guiones"]["desactualizado"], "recién generada no está desactualizada")
    r = client.patch(f"/vacantes/{VAC2}", headers=H, json={"titulo": "Montacarguista de patio"})
    check(r.json()["guiones"]["desactualizado"], "cambiar datos de la vacante después de generar → «desactualizado» (sugerir volver a generar)")

    # ================= 3. Ejecución con el candidato =================
    print("\n--- 3. Ejecución con el candidato ---")
    import asyncio

    from app.models import Bitacora, Entrevista, Postulacion
    from app.routers.candidatos import _crear_candidato, crear_postulacion, procesar_prefiltro
    from app.services import ia as sia
    from app.services import prefiltro_conversacional as pconv
    from app.services.entrevistas import crear_entrevista_para_candidato

    ruta_wa = [{"id": "pw", "tipo": "prefiltro_web", "etapa": "Prefiltro"},
               {"id": "entrevista-wa", "tipo": "entrevista_whatsapp", "etapa": "Entrevista IA"},
               {"id": "llamada", "tipo": "llamada_agente", "etapa": "Entrevista IA", "obligatorio": False},
               {"id": "cond", "tipo": "condiciones", "etapa": "Contratación"}]
    r = client.post("/vacantes", headers=H, json={**FICHA, "titulo": "Montacarguista WA", "generar_si_falta": False,
                                                 "proceso": {"pasos": ruta_wa}, "preguntas_filtro": web,
                                                 "guiones": {"secciones": {"entrevista_whatsapp": {
                                                     "enfoque": "Experiencia en patio", "temas": ["Patio", "Seguridad"],
                                                     "preguntas": ["Cuéntame de tu último turno en patio.", "Cuéntame cómo cuidas la seguridad al cargar."]}}}})
    VAC3 = r.json()["id"]
    v3 = db.query(Vacante).filter(Vacante.codigo == VAC3).one()
    c3 = _crear_candidato(db, cuenta.id, "Rosa Patio", "web", False, telefono="5512340001", correo="rosa@ejemplo.mx")
    p3 = crear_postulacion(db, c3, v3, cuenta.id, "web")
    p3.consentimiento = True
    p3.prefiltro_completo = True
    p3.estado = "cumple"
    p3.etapa = "Entrevista IA"
    p3.analisis = {"respuestas_web": [{"pregunta": web[0]["pregunta"], "respuesta": "Sí"}]}
    db.commit()
    P3 = p3.codigo
    r = client.post(f"/procesos/postulaciones/{P3}/pasos/entrevista-wa/iniciar", headers=H, json={})
    db.expire_all()
    p3 = db.query(Postulacion).filter_by(codigo=P3).one()
    e3 = next((e for e in p3.entrevistas if e.tipo == "whatsapp"), None)
    check(r.status_code == 200 and e3 is not None and e3.estado == "programada" and e3.guion.get("seccion") == "entrevista_whatsapp",
          f"«Iniciar» la Entrevista por WhatsApp crea la entrevista con el guion de la vacante ({r.status_code})")
    intro = sia.mensaje_inicial_entrevista_whatsapp(v3.titulo)  # sin proveedor en la prueba: el envío queda como intento
    check("inteligencia artificial" in intro and intro.rstrip().endswith("¿Comenzamos?") and p3.espera_respuesta,
          "la presentación avisa que la conduce la IA y pide «¿Comenzamos?»; la postulación espera respuesta en el chat")
    # RH edita el guion DESPUÉS de mandar la invitación: el bot usa la versión vigente al comenzar
    r = client.patch(f"/vacantes/{VAC3}", headers=H, json={"guiones": {"secciones": {"entrevista_whatsapp": {
        "enfoque": "Experiencia en patio", "temas": ["Patio", "Seguridad"],
        "preguntas": ["Cuéntame de tu último turno en patio (editada).", "Cuéntame cómo cuidas la seguridad al cargar."]}}}})
    check(r.status_code == 200, "RH edita el guion de WhatsApp de la vacante")
    r1 = asyncio.run(procesar_prefiltro(db, p3, "más tarde", "whatsapp"))
    check(r1.get("esperando") and db.get(Entrevista, e3.id).estado == "programada", "«más tarde» → no empieza ni registra consentimiento")
    db.expire_all()  # cada petición real abre su sesión; aquí se refresca la caché de la prueba
    r1 = asyncio.run(procesar_prefiltro(db, db.query(Postulacion).filter_by(codigo=P3).one(), "Sí, comencemos", "whatsapp"))
    check(r1["respuesta"] == "Cuéntame de tu último turno en patio (editada).",
          f"el bot hace la PRIMERA pregunta del guion de la vacante con la edición de RH: {r1['respuesta'][:80]}")
    e3 = db.get(Entrevista, e3.id)
    check(e3.consentimiento and e3.estado == "en_curso", "el «Sí» queda como consentimiento de la entrevista")
    p3 = db.query(Postulacion).filter_by(codigo=P3).one()
    r2 = asyncio.run(procesar_prefiltro(db, p3, "No tengo experiencia operando montacargas; trabajé en almacén acomodando cajas.", "whatsapp"))
    check(r2["respuesta"] == "Cuéntame cómo cuidas la seguridad al cargar.", "segunda pregunta del guion, en orden")
    p3 = db.query(Postulacion).filter_by(codigo=P3).one()
    r3 = asyncio.run(procesar_prefiltro(db, p3, "Reviso el mástil, uso cinturón, nunca levanto con gente cerca y respeto la capacidad.", "whatsapp"))
    db.expire_all()
    e3 = db.get(Entrevista, e3.id)
    check(r3.get("terminada") and e3.estado == "evaluada" and e3.evaluacion.get("score_entrevista") is not None,
          "al despedirse se cierra y se evalúa como cualquier entrevista de IA (resumen, fortalezas, puntos por validar, recomendación)")
    paso_wa = next(x for et in client.get(f"/procesos/postulaciones/{P3}", headers=H).json()["etapas"] for x in et["pasos"] if x["id"] == "entrevista-wa")
    check(paso_wa["estado"] == "completada", "la actividad «Entrevista Red Human por WhatsApp» queda completada con su evaluación")
    riesgos = e3.evaluacion.get("riesgos") or []
    check(riesgos and riesgos[0].startswith("Inconsistencia:") and e3.evaluacion.get("recomendacion") != "no_avanzar",
          "contradicción con el formulario web → «Inconsistencia» en Puntos por validar, sin recomendar descarte")
    ficha = client.get(f"/candidatos/{P3}", headers=H).json()
    p3 = db.query(Postulacion).filter_by(codigo=P3).one()
    check(p3.activa and any(x.startswith("Inconsistencia:") for x in ficha.get("puntosPorValidar") or (ficha.get("detalle") or {}).get("puntosPorValidar") or []),
          "la ficha la muestra en Puntos por validar y la postulación sigue activa")

    # Llamada Red Human + JIT
    v3.guiones = {}
    db.commit()
    p3 = db.query(Postulacion).filter_by(codigo=P3).one()
    e_ll, _ = crear_entrevista_para_candidato(db, p3, "RH", paso_tipo="llamada_agente")
    db.commit()
    db.refresh(v3)
    check(e_ll.tipo == "llamada" and e_ll.guion.get("seccion") == "llamada" and (v3.guiones.get("secciones") or {}).get("llamada"),
          "sin guion de llamada en la vacante → se genera Just-In-Time, se guarda en la vacante y se usa")
    check(db.query(Bitacora).filter(Bitacora.accion == "guion_generado_jit", Bitacora.entidad_id == VAC3).count() >= 1,
          "el JIT queda en bitácora")
    pub = client.get(f"/entrevistas/publica/{e_ll.token}").json()
    client.post(f"/entrevistas/publica/{e_ll.token}/consentimiento", json={"acepta": True})
    ses = client.post(f"/entrevistas/publica/{e_ll.token}/sesion", json={}).json()
    db.refresh(e_ll)
    check(pub["tipo"] == "llamada" and ses.get("llamada") is True and e_ll.tipo == "llamada",
          "la sala abre la Llamada Red Human en modo llamada (sin perder su tipo aunque caiga a texto)")
    e_tok = db.query(Entrevista).filter(Entrevista.id == e3.id).one()
    check(client.post(f"/entrevistas/publica/{e_tok.token}/sesion", json={}).status_code == 409,
          "una entrevista por WhatsApp no se abre en la sala")

    # JIT del prefiltro: vacante sin preguntas cuya ruta tiene prefiltro por WhatsApp
    r = client.post("/vacantes", headers=H, json={**FICHA, "titulo": "Ayudante sin guion", "generar_si_falta": False,
                                                 "proceso": {"pasos": [{"id": "pwa", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"}]}})
    v5 = db.query(Vacante).filter(Vacante.codigo == r.json()["id"]).one()
    c5 = _crear_candidato(db, cuenta.id, "Leo Antiguo", "whatsapp", False, telefono="5512340005")
    p5 = crear_postulacion(db, c5, v5, cuenta.id, "whatsapp")
    p5.consentimiento = True
    db.commit()
    check(not v5.preguntas_filtro_whatsapp, "candidato antiguo: su vacante no tiene guion de prefiltro")
    asyncio.run(procesar_prefiltro(db, p5, "Hola", "whatsapp"))
    db.refresh(v5)
    check(len(v5.preguntas_filtro_whatsapp) >= 1 and all(q["tipo"] in ("si_no", "numero", "opcion") for q in v5.preguntas_filtro_whatsapp),
          "el bot genera el guion de prefiltro JIT antes del primer mensaje (nunca arranca sin guion)")

    # Prefiltro conversacional (ruta automática): reconfirmación con dato concreto; contradicción → Inconsistencia, sin cierre
    demo = Cuenta(nombre="Demo GrupPak", nombre_comercial="GrupPak", razon_social="GrupPak SA de CV", estado="Activa", slug="demo-grupak")
    db.add(demo)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=demo.id))
    db.commit()
    rec = sia.pregunta_reconfirmacion("2 años de experiencia operando montacargas")
    v6 = Vacante(codigo="VAC-RC", titulo="Montacarguista RC", cuenta_id=demo.id, estado="Publicada", modalidad="Presencial",
                 preguntas_filtro=[{**web[0]}], preguntas_filtro_whatsapp=[rec])
    db.add(v6)
    db.flush()
    c6 = _crear_candidato(db, demo.id, "Iván Reconfirma", "web", False, telefono="5512340006")
    p6 = crear_postulacion(db, c6, v6, demo.id, "web")
    p6.consentimiento = True
    p6.analisis = {"respuestas_web": [{"pregunta": web[0]["pregunta"], "respuesta": "Sí"}]}
    db.commit()
    check(pconv.aplica(p6) and any(c.get("reconfirma") for c in pconv.criterios_de(v6)),
          "el prefiltro conversacional suma la reconfirmación de WhatsApp al indispensable del web")
    r = asyncio.run(pconv.turno(db, p6, "Hola", "whatsapp"))
    check("cuánto tiempo" in r["respuesta"].lower() and "2 a 4 años" in r["respuesta"],
          "tras el «Sí» del formulario, el bot pide el DATO CONCRETO (cuánto tiempo, con rangos)")
    r = asyncio.run(pconv.turno(db, db.query(Postulacion).filter_by(codigo=p6.codigo).one(), "1 año", "whatsapp"))
    p6 = db.query(Postulacion).filter_by(codigo=p6.codigo).one()
    check(p6.activa and p6.estado == "revision" and (p6.analisis.get("inconsistencias") or [{}])[-1].get("fuente") == "reconfirmacion",
          "«1 año» contradice el «Sí» de 2 años → Inconsistencia y «Revisar prefiltro»; la postulación NO se cierra")
    ficha6 = client.get(f"/candidatos/{p6.codigo}", headers={"X-Cuenta-Id": str(demo.id)}).json()
    textos = ficha6.get("puntosPorValidar") or (ficha6.get("detalle") or {}).get("puntosPorValidar") or []
    check(any(t.startswith("Inconsistencia:") for t in textos), f"y aparece en Puntos por validar: {textos[:1]}")

    # ================= 4. Publicación sin CV y avisos únicos =================
    print("\n--- 4. Publicación según la ruta y avisos ---")
    masivos = next(p for p in client.get("/procesos/plantillas", headers=H).json() if p.get("rutaBase") == "masivos_sin_documentos")
    r = client.post("/vacantes/generar", headers=H, json={**FICHA, "proceso": {"plantilla_id": masivos["id"]}})
    textos = " ".join([r.json()["texto_whatsapp"]] + [r.json()[k]["copy"] + r.json()[k]["page"] for k in ("occ", "linkedin", "portal")])
    check(r.status_code == 200 and "CV" not in textos and "currículum" not in textos.lower(),
          "ruta Masivos (solicitud sin CV): ningún texto de publicación pide el CV")
    r = client.post("/vacantes/generar", headers=H, json={**FICHA, "proceso": {"plantilla_id": corp["id"]}})
    check("CV" in r.json()["occ"]["page"], "ruta Corporativos (con Análisis de CV): la publicación sí lo pide")
    r = client.post("/vacantes", headers=H, json={**FICHA, "titulo": "Ayudante masivo", "generar_si_falta": False,
                                                 "proceso": {"plantilla_id": masivos["id"]},
                                                 "avisos_cumplimiento": ["Sueldo no capturado («A convenir»): RH debe confirmarlo antes de publicar.",
                                                                         "Falta confirmar el sueldo del puesto.",
                                                                         "Se reescribió un requisito de edad como competencia.",
                                                                         "Se reescribió un requisito de edad como competencia."]})
    det = r.json()
    slug = det["slug"]
    check(det["avisosCumplimiento"] == ["Se reescribió un requisito de edad como competencia."],
          f"avisos: sin duplicados y sin el del sueldo ya capturado: {det['avisosCumplimiento']}")
    client.post(f"/vacantes/{det['id']}/publicar", headers=H, json={"plataformas": ["Portal"]})
    pub_slug = client.get(f"/vacantes/slug/{slug}").json()
    check(pub_slug.get("pideCv") is False and client.get(f"/vacantes/{VAC}", headers=H).json()["pideCv"] is True,
          "el formulario público sabe si la ruta pide CV (Masivos no; Corporativos sí)")

    print(f"\n✅ {OK} comprobaciones OK")
