"""Regresión de «Generación de contenido según la ruta» (2026-10-09). Base desechable, SIN red ni OpenAI.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_guiones_vacante.py

1. Almacenamiento: plantillas de conversación POR VACANTE (prefiltro web/WhatsApp, entrevista por WhatsApp, con avatar
   y llamada), solo las de su ruta; edición manual marcada con quién; prefiltros solo cerrados; ajustar la ruta de la
   vacante NO toca la plantilla de la Cuenta; los guiones nunca salen en los payloads públicos.
2. Generación: solo las actividades de la ruta; prefiltros cerrados (eliminatorias = indispensables); WhatsApp reconfirma
   los indispensables del web pidiendo un dato concreto y no repite lo demás; entrevistas/llamada solo preguntas
   abiertas; «Volver a generar» nunca pisa ediciones de RH sin confirmación; datos cambiados → desactualizado.
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

    print(f"\n✅ {OK} comprobaciones OK")
