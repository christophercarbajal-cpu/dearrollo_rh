"""Cambios 28 y 29 (2026-10-12). Base desechable; IA, Bot API y Meta SIMULADAS (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambios28_29.py

Bloque 1 (Cambio 28): catálogo ESTRICTO de actividades por etapa («Otra actividad» en todas); «Documentos» y «Carta de
intención» sin «(opcional)»; sin casilla «Obligatoria»; descripción fija por etapa; «Partir de:» al crear un proceso.

Bloque 2 (Cambio 28): «Otra actividad» (nombre + mensaje por WhatsApp, «Marcar como realizada»); «Capacitación» (curso
de la ruta: aprueba → sigue sola, reprueba → «No aprobó el curso», nunca descarta); sincronización Proceso ↔ Vacante
(documentos tras el prefiltro, documentos obligatorios, CV obligatorio, 3 indispensables, «Publicar» bloqueado, «Se pide
en:» por documento).

Bloque 3 (Cambio 29): en rutas Masivos el prefiltro de WhatsApp es una BASE FIJA sin IA — saludo exacto, indispensables
con datos concretos (descartan), edad (menor de 18 → Puntos por validar), puesto, sueldo, colonia, «¿Puedes
trasladarte?» y tiempo de traslado (descarta solo con límite en la vacante); sin transporte ni habilidades blandas.

Bloque 4 (Cambio 29): entrevista por WhatsApp FIJA en Masivos — transición exacta, A situación actual, B historial (hasta 3
trabajos, repregunta de despido/renuncia), C preguntas propias de RH, D confirmación (contradicción con el prefiltro →
Puntos por validar) y cierre exacto; configuración «Base» en la vacante (quitar, agregar, restaurar); tabla «Historial
laboral» en la ficha y recomendación calculada sobre él; un solo nombre de empresa.
"""

import asyncio
import json
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
_dir = _temporal.directorio("rh_c28_29_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c28.db").replace(chr(92), "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "RESEND_API_KEY",
          "TELEGRAM_WEBHOOK_SECRET", "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:PRUEBA-token-falso"
os.environ["TELEGRAM_BOT_USERNAME"] = "RedHumanPruebaBot"
os.environ["ADMIN_PASSWORD"] = "prueba-c28"
os.environ["SEMBRAR_DEMO"] = "true"

from _ruta_prueba import fijar_ruta  # noqa: E402  (Cambio 36: ruta inicial de vacantes sembradas)
from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ACTIVIDADES_POR_ETAPA, AsignacionCurso, Bitacora, Cuenta, Curso, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
APP_DIR = RAIZ.parent / "red-human-app"


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def leer(rel):
    return (APP_DIR / rel).read_text(encoding="utf-8")


with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="C28", nombre_comercial="Operaciones MX", estado="Activa", slug="c28")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ================= Bloque 1 · Cambio 28: estructura del editor y plantillas =================
    print("\n--- Bloque 1 · catálogo estricto por etapa ---")
    op = client.get("/procesos/opciones").json()
    etapas = {e["valor"]: e for e in op["etapas"]}
    # Cambio 55: «Otra actividad» ya no se ofrece en Prefiltro ni Filtro Red Human
    check(all(("otra" in e["actividades"]) == (e["valor"] not in ("Prefiltro", "Entrevista IA")) for e in op["etapas"]),
          "«Otra actividad» se ofrece en todas las etapas salvo Prefiltro y Filtro Red Human")
    check(etapas["Prefiltro"]["actividades"] == list(ACTIVIDADES_POR_ETAPA["Prefiltro"])
          and "entrevista_humana" not in etapas["Prefiltro"]["actividades"]
          and "condiciones" not in etapas["Entrevista IA"]["actividades"]
          and "alta" in etapas["Onboarding"]["actividades"] and "alta" not in etapas["Contratación"]["actividades"],
          "cada etapa ofrece solo su catálogo")
    check([e["descripcion"] for e in op["etapas"]] == ["Solicitud y primeras preguntas.", "Lo hace la IA de Red Human.",
                                                       "Lo hace tu equipo.", "Oferta, documentos y contrato.",
                                                       "Primeros días del nuevo empleado."],
          "cada etapa trae su descripción fija (textos exactos del requerimiento)")
    nombres = {t["valor"]: t["texto"] for t in op["tiposPaso"]}
    check(nombres["documentos"] == "Documentos" and nombres["oferta"] == "Oferta laboral"
          and not any("(opcional)" in t for t in nombres.values()), "«Documentos» y sin «(opcional)» en el catálogo")
    check(all(t.get("obligatorio", True) for t in op["tiposPaso"]), "toda actividad nace obligatoria")

    r = client.post("/procesos/plantillas", json={"nombre": "Mal armado", "pasos": [
        {"tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"}, {"tipo": "condiciones", "etapa": "Prefiltro"}]})
    check(r.status_code == 400 and "no puede ir en" in r.json()["detail"], f"una actividad fuera de su etapa → 400 ({r.status_code})")
    r = client.post("/procesos/plantillas", json={"nombre": "Con otra", "pasos": [
        {"tipo": "otra", "etapa": "Entrevista Humana", "nombre": "Llamar al candidato"}, {"tipo": "otra", "etapa": "Onboarding", "nombre": "Bienvenida"}]})
    check(r.status_code == 201, f"«Otra actividad» en Filtro humano y Onboarding ({r.status_code})")
    viejo = [{"id": "cv", "tipo": "analisis_cv", "etapa": "Entrevista IA"}]
    check(sproc.normalizar_pasos(viejo, previos=viejo)[0]["etapa"] == "Entrevista IA",
          "una actividad ya guardada fuera del catálogo nuevo se conserva (rutas previas)")
    check(len(sproc.normalizar_pasos(viejo)) == 1, "las copias congeladas se siguen normalizando sin el catálogo estricto")

    print("\n--- Bloque 1 · «Partir de:» ---")
    partir = {o["clave"]: o for o in op["partirDe"]}
    check([o["nombre"] for o in op["partirDe"]] == ["Masivos sin documentos", "Masivos con documentos", "Corporativos", "En blanco"],
          "«Partir de:» ofrece Masivos sin documentos, Masivos con documentos, Corporativos y En blanco")
    check(not partir["blanco"]["pasos"] and any(p["tipo"] == "solicitud_web" and p.get("con_cv") for p in partir["corporativo"]["pasos"])
          and any(p["id"] == "documentos-iniciales" for p in partir["masivos_con_documentos"]["pasos"]),
          "cada opción carga sus actividades")
    r = client.post("/procesos/plantillas", json={"nombre": "Masivos propio", "pasos": partir["masivos_sin_documentos"]["pasos"]})
    check(r.status_code == 201, f"un proceso creado a partir de una ruta base se guarda ({r.status_code})")

    ed = leer("components/dashboard/procesos/editor-proceso.tsx")
    pl = leer("components/dashboard/procesos/seccion-plantillas-proceso.tsx")
    check("Obligatoria" not in ed.split("*/", 1)[1] and "e.descripcion" in ed and "e.actividades" in ed,
          "el editor no tiene casilla «Obligatoria», muestra la descripción y usa el catálogo de la etapa")
    check("Partir de:" in pl and "opciones.partirDe" in pl, "el modal de nuevo proceso empieza con «Partir de:»")

    # ================= Bloque 2 · Cambio 28: actividades especiales y sincronización =================
    print("\n--- Bloque 2 · «Otra actividad» ---")
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).first()
    v.cuenta_id = cuenta.id
    v.requisitos = "Secundaria terminada · Disponibilidad de turnos · Experiencia en almacén"
    v.descripcion = v.descripcion or "Almacenista para centro de distribución."
    db.commit()

    def postular(nombre, tel, pasos, etapa="Prefiltro"):
        c = _crear_candidato(db, cuenta.id, nombre, "Portal", False, telefono=tel)
        p = crear_postulacion(db, c, v, cuenta.id, "web", consentimiento=True)
        p.proceso = {**sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": pasos}), "etapa_base": "Prefiltro"}
        p.etapa = etapa
        db.commit()
        return p

    def paso_de(p, pid):
        db.refresh(p)
        return next(x for x in sproc.estado_pasos(p) if x["id"] == pid)

    r = client.post("/procesos/plantillas", json={"nombre": "Otra sin nombre", "pasos": [{"tipo": "otra", "etapa": "Entrevista Humana"}]})
    check(r.status_code == 400 and "nombre" in r.json()["detail"], "«Otra actividad» sin nombre no se guarda en la ruta")
    p1 = postular("Otra Uno", "5512880001", [{"id": "sw", "tipo": "solicitud_web", "etapa": "Prefiltro"},
                                              {"id": "llamar", "tipo": "otra", "etapa": "Entrevista Humana", "nombre": "Llamada de bienvenida",
                                               "mensaje": "Te llamaremos hoy para darte la bienvenida."}], etapa="Entrevista Humana")
    check(next(x for x in p1.proceso["pasos"] if x["id"] == "llamar")["mensaje"].startswith("Te llamaremos"), "el mensaje viaja en la actividad")
    asyncio.run(sproc.avanzar_seguro(db, p1))
    x = paso_de(p1, "llamar")
    check(bool((p1.proceso_estado or {}).get("llamar", {}).get("mensaje_enviado")), "al habilitarse, el agente manda el mensaje (una vez)")
    check(x["otraSimple"] and x["estado"] != "completada" and x["accion"]["clave"] == "marcar_realizada",
          "se queda ahí hasta «Marcar como realizada»")
    asyncio.run(sproc.avanzar_seguro(db, p1))
    check(db.query(Bitacora).filter(Bitacora.accion == "actividad_mensaje_enviado", Bitacora.entidad_id == p1.codigo).count() == 1,
          "el mensaje no se repite")
    r = client.post(f"/procesos/postulaciones/{p1.codigo}/pasos/llamar/realizada", json={"comentario": "Se le llamó"})
    check(r.status_code == 200 and paso_de(p1, "llamar")["estado"] == "completada", f"«Marcar como realizada» la cumple ({r.status_code})")
    r = client.post(f"/procesos/postulaciones/{p1.codigo}/pasos/llamar/realizada", json={})
    check(r.status_code == 409, "no se marca dos veces")
    r = client.post(f"/procesos/postulaciones/{p1.codigo}/pasos/sw/realizada", json={})
    check(r.status_code == 409, "solo «Otra actividad» se marca a mano")
    r = client.post(f"/procesos/postulaciones/{p1.codigo}/actividades",
                    data={"datos": json.dumps({"tipo": "otra", "nombre": "Recoger uniforme", "mensaje": "Pasa por tu uniforme."})})
    check(r.status_code in (200, 201) and r.json()["paso"]["mensaje"] == "Pasa por tu uniforme.",
          f"«Agregar actividad» → «Otra»: nombre + mensaje, sin evaluador ({r.status_code} {r.text[:120]})")

    print("\n--- Bloque 2 · «Capacitación» ---")
    curso = Curso(codigo="CUR-C28", titulo="Seguridad en almacén", estado="Publicado", cuenta_id=cuenta.id, calificacion_minima=70)
    db.add(curso)
    db.commit()
    p2 = postular("Curso Dos", "5512880002", [{"id": "cap", "tipo": "induccion", "etapa": "Onboarding", "curso": "CUR-C28"},
                                              {"id": "alta", "tipo": "alta", "etapa": "Onboarding"}], etapa="Onboarding")
    check(p2.proceso["pasos"][0]["curso"] == "CUR-C28", "RH elige el curso en la actividad")
    asyncio.run(sproc.avanzar_seguro(db, p2))
    a = db.query(AsignacionCurso).filter(AsignacionCurso.postulacion_id == p2.id).first()
    check(a is not None and a.curso_id == curso.id, "al habilitarse se asigna el curso (el bot manda la liga)")
    asyncio.run(sproc.avanzar_seguro(db, p2))
    check(db.query(AsignacionCurso).filter(AsignacionCurso.postulacion_id == p2.id).count() == 1, "no se asigna dos veces")
    a.estado, a.aprobado, a.calificacion = "completado", False, 40
    db.commit()
    x = paso_de(p2, "cap")
    check(x["estadoUnificadoTexto"] == "No aprobó el curso" and x["noAproboCurso"] and not x["cumpleRegla"],
          "reprueba → «No aprobó el curso»")
    check(p2.activa and not p2.motivo_cierre, "reprobar el curso NUNCA descarta: RH decide")
    a.aprobado, a.calificacion = True, 90
    db.commit()
    x = paso_de(p2, "cap")
    check(x["estado"] == "completada" and x["cumpleRegla"], "aprueba → la actividad se cumple y la ruta sigue sola")

    print("\n--- Bloque 2 · sincronización Proceso ↔ Vacante ---")
    # Cambio 36 («El proceso manda»): la vacante NUNCA modifica el proceso (ya no se agrega «Documentos») y, con
    # candidatos, la API ya no cambia su proceso; la ruta se fija como configuración para probar lo demás
    masivos = sproc.ruta_base("masivos_sin_documentos")["pasos"]
    r = client.patch(f"/vacantes/{v.codigo}", json={"proceso": {"pasos": masivos}})
    check(r.status_code == 409, f"con candidatos el proceso de la vacante no se cambia por la API ({r.status_code})")
    fijar_ruta(db, v.codigo, json={"pasos": masivos})
    r = client.patch(f"/vacantes/{v.codigo}", json={"documentos_solicitados": [{"nombre": "INE"}, {"nombre": "Licencia de conducir"}]})
    db.expire_all()
    v = db.get(Vacante, v.id)
    ids = [x["id"] for x in v.proceso["pasos"]]
    check(r.status_code == 200 and "documentos-vacante" not in ids and not any(x.get("documentos_vacante") for x in v.proceso["pasos"]),
          f"la vacante pide documentos y su ruta no trae «Documentos» → la ruta NO cambia ({r.status_code})")
    con_docs = sproc.ruta_base("masivos_con_documentos")["pasos"]
    fijar_ruta(db, v.codigo, json={"pasos": con_docs})
    r = client.patch(f"/vacantes/{v.codigo}", json={"documentos_solicitados": []})
    db.expire_all()
    v = db.get(Vacante, v.id)
    check(r.status_code == 200 and any("Documentos" in f for f in r.json()["faltantesPublicacion"]),
          f"ruta con «Documentos» y sin documentos en la vacante → falta para publicar ({r.json().get('faltantesPublicacion')})")
    v.estado = "En revisión"
    db.commit()
    r = client.post(f"/vacantes/{v.codigo}/publicar", json={"plataformas": ["Portal"]})
    check(r.status_code == 409 and "Documentos" in r.json()["detail"], f"«Publicar» se bloquea con la alerta ({r.status_code})")
    # Cambio 37: con prefiltro basta 1 indispensable (antes 3)
    r = client.patch(f"/vacantes/{v.codigo}", json={"documentos_solicitados": [{"nombre": "INE"}], "requisitos": ""})
    check(any("1 requisito indispensable" in f for f in r.json()["faltantesPublicacion"]),
          "con prefiltro, la vacante exige al menos 1 indispensable (Cambio 37)")
    r = client.patch(f"/vacantes/{v.codigo}", json={"requisitos": "Secundaria terminada"})
    check(r.json()["faltantesPublicacion"] == [], "completos los datos (1 indispensable), ya no falta nada")
    r = client.post(f"/vacantes/{v.codigo}/publicar", json={"plataformas": ["Portal"]})
    check(r.status_code == 200 and r.json()["estado"] == "Publicada", f"y se publica ({r.status_code} {r.text[:200]})")

    # Cambio 30: el «Análisis de CV» ya es parte de la «Solicitud web con CV»; una ruta anterior que aún trae la actividad
    # aparte (legado) sigue forzando el CV en la solicitud
    corp = sproc.ruta_base("corporativo")["pasos"]
    corp = [{**x, "con_cv": False} if x["tipo"] == "solicitud_web" else x for x in corp] + [
        {"id": "analisis_cv", "tipo": "analisis_cv", "etapa": "Prefiltro"}]
    fijar_ruta(db, v.codigo, json={"pasos": corp})  # Cambio 36: con candidatos, la ruta se fija como configuración
    r = client.patch(f"/vacantes/{v.codigo}", json={"descripcion": "Operar montacargas."})  # guardar sincroniza la ruta
    db.expire_all()
    v = db.get(Vacante, v.id)
    check(next(x for x in v.proceso["pasos"] if x["tipo"] == "solicitud_web")["con_cv"] is True and r.json()["exigeCv"],
          "ruta con Análisis de CV → la solicitud web exige CV")
    app.dependency_overrides.pop(cuenta_actual, None)
    r = client.post("/candidatos/postular", data={"vacante": v.codigo, "nombre": "Sin CV", "telefono": "5512880003", "consentimiento": "true"})
    check(r.status_code == 400 and "CV" in r.json()["detail"], f"sin CV la postulación web se rechaza ({r.status_code})")
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    print("\n--- Bloque 2 · «Se pide en:» ---")
    con_docs = sproc.ruta_base("masivos_con_documentos")["pasos"]
    fijar_ruta(db, v.codigo, json={"pasos": con_docs})  # Cambio 36: con candidatos, la ruta se fija como configuración
    r = client.patch(f"/vacantes/{v.codigo}", json={"documentos_solicitados": [
        {"nombre": "INE"}, {"nombre": "Comprobante de domicilio", "etapa": "Contratación"}]})
    db.expire_all()
    v = db.get(Vacante, v.id)
    check(r.json()["etapasDocumentos"] == ["Prefiltro", "Contratación"], "con «Documentos» en varias etapas se ofrece «Se pide en:»")
    check(r.json()["documentosSolicitados"][1]["etapa"] == "Contratación", "cada documento guarda su etapa")
    temprano = next(x for x in v.proceso["pasos"] if x["tipo"] == "documentos" and x["etapa"] == "Prefiltro")
    check(temprano.get("documentos") == ["INE (frente y reverso)"], "lo de Contratación no se pide en el Prefiltro")
    p3 = postular("Docs Tres", "5512880004", v.proceso["pasos"])
    r = client.patch(f"/candidatos/{p3.codigo}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True,
                                                              "comentario": "Prueba de documentos por etapa"})
    db.refresh(p3)
    check(r.status_code == 200 and p3.expediente is not None and any(d.tipo == "Comprobante de domicilio" for d in p3.expediente.documentos),
          f"al llegar a Contratación el documento entra al expediente ({r.status_code} {r.text[:150]})")

    form = leer("components/dashboard/vacantes/formulario-contenido.tsx")
    pag = leer("app/dashboard/vacantes/page.tsx")
    check("Se pide en:" in form and "avisosPublicacion" in form, "el formulario muestra «Se pide en:» y lo que falta para publicar")
    check("v.faltantesPublicacion?.length" in pag, "el botón «Publicar» se desactiva con alerta")

    # ================= Bloque 3 · Cambio 29: prefiltro operativo FIJO =================
    print("\n--- Bloque 3 · prefiltro operativo fijo (rutas Masivos) ---")
    from app.services import ia, prefiltro_operativo as pop
    from app.services import prefiltro_conversacional as pconv

    REQS = "Secundaria terminada · 1 año de experiencia en almacén · Disponibilidad para rolar turnos · Trabajo en equipo"
    vo = Vacante(codigo="VAC-OP29", titulo="Almacenista", estado="Publicada", cuenta_id=cuenta.id, requisitos=REQS,
                 descripcion="Surtir pedidos en el CEDIS.", sueldo="Desde $9,500 MXN mensuales", ubicacion="Zapopan, Jalisco",
                 ubicacion_estado="Jalisco", ubicacion_municipio="Zapopan", enfoque_entrevista="operativo",
                 preguntas_filtro_whatsapp=[{"pregunta": "¿Te consideras una persona organizada?", "tipo": "si_no", "descarta": True}])
    db.add(vo)
    db.flush()
    vo.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": sproc.ruta_base("masivos_sin_documentos")["pasos"]})
    db.commit()
    crit = pconv.criterios_de(vo)
    # 2026-10-16: sin «¿Cuántos años tienes?» (la vacante no pide edad) ni «¿Te interesa?»; el sueldo es la expectativa
    # 2026-10-16 (bug del tester): la vacante publica su sueldo ($9,500) → la expectativa ya no se pregunta
    # 2026-10-16 (QA): sueldo publicado → «El sueldo es… ¿Te interesa?» (la expectativa ya no existe)
    # 2026-10-17 (Ficha de Nueva vacante): ya no se pregunta «El sueldo es… ¿Te interesa?»
    check(pop.aplica(vo) and [c["dato"] for c in crit] == ["escolaridad", "experiencia", "disponibilidad", "zona", "traslado"],
          f"ruta Masivos: base fija en orden (indispensables → colonia → traslado; sin sueldo) {[c['dato'] for c in crit]}")
    check(not any(c["dato"] == "transporte" for c in crit) and not any("organizad" in c["chat"] or "equipo" in c["chat"].lower() for c in crit),
          "sin pregunta de transporte ni habilidades blandas (lo generado antes por IA se ignora)")
    check([c["indispensable"] for c in crit[:3]] == [True, True, True] and not crit[3]["indispensable"] and not crit[-1]["indispensable"],
          "los indispensables descartan; edad y traslado (sin límite) solo se registran")
    # Cambio 48: preguntas DIRECTAS (dato concreto o Sí/No directa), nunca relleno ni el requisito pegado en una frase fija
    check(not any(c["chat"].startswith(("Cuéntame", "¿Cumples")) or "Para esta vacante se pide" in c["chat"] for c in crit[:3])
          and crit[0]["chat"] == "¿Cuál es tu último grado de estudios?" and crit[1]["chat"].startswith("¿Cuánt"),
          f"los indispensables se preguntan directo ({[c['chat'] for c in crit[:3]]})")
    # Cambio 51: el puesto lleva ciudad y estado; ya no se pregunta «¿Puedes trasladarte?»
    # 2026-10-16: sin «El puesto es… ¿Te interesa?» ni el sueldo afirmativo: el sueldo es la expectativa
    # 2026-10-17 (Ficha de Nueva vacante): ya no se pregunta el sueldo — de los indispensables a la colonia
    check(crit[3]["chat"] == "¿En qué colonia y municipio vives?" and crit[4]["chat"] == "¿Cuánto tiempo harías de tu casa al trabajo?",
          f"textos fijos de colonia y traslado {[c['chat'] for c in crit[3:]]}")
    ficha = ia.FichaGuion(titulo="Almacenista", requisitos_indispensables=REQS.split(" · "), sueldo="Desde $9,500 MXN mensuales",
                          ubicacion="Zapopan, Jalisco", enfoque_entrevista="operativo", ruta_masiva=True)
    wa = ia.generar_guiones(ficha, ["prefiltro_whatsapp"], hay_whatsapp=True)[0]["prefiltro_whatsapp"]
    check([q["dato"] for q in wa] == [c["dato"] for c in crit] and all(q.get("base_operativa") for q in wa),
          "la generación de Masivos NO usa IA: guarda la misma base fija")

    def chat(p, texto):
        r = client.post(f"/candidatos/{p.codigo}/prefiltro", json={"texto": texto})
        assert r.status_code == 200, r.text
        return r.json().get("respuesta") or ""

    def postular_op(nombre, tel):
        c = _crear_candidato(db, cuenta.id, nombre, "Portal", False, telefono=tel)
        p = crear_postulacion(db, c, vo, cuenta.id, "web", consentimiento=True)
        db.commit()
        return p

    q1 = postular_op("Laura Operativa", "5512990001")
    r = chat(q1, "Hola")
    check(r.startswith("Hola, Laura. Soy Red Human, el agente virtual de Operaciones MX. ")
          and r.endswith("¿Cuál es tu último grado de estudios?"), f"saludo exacto + primera pregunta indispensable → «{r[:90]}…»")
    r = chat(q1, "Terminé la secundaria")
    check("llevas en almacén" in r, f"siguiente indispensable con dato concreto (pregunta natural, Cambio 31) → «{r}»")
    r = chat(q1, "sí")
    # Cambio 37: la repregunta pide el dato directo («Claro. Solo para tenerlo en cuenta…» es solo del sueldo)
    check("cuánto tiempo" in r.lower() and not r.startswith("Claro. Solo para tenerlo en cuenta"),
          "un «sí» a secas no es un dato: se pide el dato concreto")
    r = chat(q1, "2 años")
    check("rolar turnos" in r, f"→ «{r}»")
    r = chat(q1, "Puedo mañana y tarde")
    # 2026-10-16: la vacante no pide edad → no se pregunta · 2026-10-17: ni el sueldo («El sueldo es… ¿Te interesa?»)
    check(r.endswith("¿En qué colonia y municipio vives?") and "años tienes" not in r and "El sueldo es" not in r,
          f"colonia y municipio justo después de los indispensables (sin edad ni sueldo) → «{r}»")
    r = chat(q1, "Colonia Centro, Zapopan")
    # Cambio 51: ya no se pregunta «¿Puedes trasladarte?» (la ciudad va en la pregunta del puesto)
    check(r.endswith("¿Cuánto tiempo harías de tu casa al trabajo?"), f"tiempo de traslado → «{r}»")
    r = chat(q1, "2 horas")
    db.refresh(q1)
    datos = (q1.analisis or {}).get("prefiltro_datos") or {}
    puntos = (q1.analisis or {}).get("puntos_validar_prefiltro") or []
    check(q1.activa and pconv.estado(q1).get("resultado") == "cumple" and datos.get("traslado_min") == 120 and "edad" not in datos
          and datos.get("sueldo_expectativa") is None,
          "sin límite de traslado en la vacante, 2 horas solo se registra y el prefiltro se cumple (sin edad)")
    check(not any("Menor de 18" in x for x in puntos), "sin edad en la vacante no hay punto de edad")
    textos = [m.texto for m in q1.mensajes if m.rol == "assistant"]
    check(not any("transporte" in t.lower() for t in textos), "nunca pregunta el transporte")

    q2 = postular_op("Pedro Novato", "5512990002")
    chat(q2, "Hola")
    chat(q2, "Terminé la secundaria")
    r = chat(q2, "6 meses")
    db.refresh(q2)
    check(not q2.activa and q2.motivo_cierre == "descartado" and "no continuaremos con tu postulación" in r
          and "experiencia" not in r and "1 año de experiencia en almacén" in q2.analisis["prefiltro_web"]["motivo"],
          f"el dato no cumple el indispensable → descarta con el cierre genérico (Cambio 48; el motivo solo en la ficha) → «{r[:100]}»")

    vo.requisitos = REQS + " · Vivir a máximo 60 minutos de la planta · Mayor de edad"
    db.commit()
    crit = pconv.criterios_de(vo)
    trasl = next(c for c in crit if c["dato"] == "traslado")
    edad = next(c for c in crit if c["dato"] == "edad")
    check(trasl["indispensable"] and trasl["limite_min"] == 60 and edad["indispensable"] and edad["edad_min"] == 18,
          "con límite de traslado (y edad) en la vacante, esos datos sí descartan")
    q3 = postular_op("Rosa Lejana", "5512990003")
    for t in ("Hola", "Terminé la secundaria", "3 años", "Fines de semana y entre semana", "30", "Sí", "Las Águilas, Zapopan"):  # «Sí» al sueldo
        chat(q3, t)
    r = chat(q3, "hora y media")
    db.refresh(q3)
    check(not q3.activa and "60 minutos" not in r and "60 minutos" in q3.analisis["prefiltro_web"]["motivo"],
          "traslado mayor al límite de la vacante → descarta (Cambio 48: el límite solo en el motivo de la ficha)")

    # ================= Bloque 4 · Cambio 29: entrevista operativa fija y ficha =================
    print("\n--- Bloque 4 · configuración «Entrevista por WhatsApp» en la vacante ---")
    from app.services import entrevista_whatsapp_fija as ewf
    from app.serial import nombre_empresa_candidato, postulacion_dict

    vo.requisitos = REQS
    db.commit()
    r = client.get(f"/vacantes/{vo.codigo}")
    base = r.json()["guiones"]["entrevistaWhatsappBase"]
    check(base["esBase"] and [b["id"] for b in base["base"]] == ewf.IDS_BASE, "la vacante muestra la entrevista «Base»")
    r = client.patch(f"/vacantes/{vo.codigo}", json={"guiones": {"entrevista_whatsapp_base": {
        "quitadas": ["conf_traslado", "empresa"], "propias": ["¿Has manejado montacargas?"]}}})
    eb = r.json()["guiones"]["entrevistaWhatsappBase"]
    check(r.status_code == 200 and not eb["esBase"] and eb["quitadas"] == ["conf_traslado"] and eb["propias"] == ["¿Has manejado montacargas?"],
          "RH quita preguntas base (la de empresa es fija) y agrega propias (bloque C)")
    r = client.patch(f"/vacantes/{vo.codigo}", json={"guiones": {"entrevista_whatsapp_base": {"restaurar": True}}})
    check(r.json()["guiones"]["entrevistaWhatsappBase"]["esBase"], "«Restaurar base»")
    client.patch(f"/vacantes/{vo.codigo}", json={"guiones": {"entrevista_whatsapp_base": {"quitadas": [], "propias": ["¿Has manejado montacargas?"]}}})
    db.expire_all()
    vo = db.get(Vacante, vo.id)
    g = ia.generar_guiones(ia.FichaGuion(titulo="Almacenista", enfoque_entrevista="operativo", ruta_masiva=True), ["entrevista_whatsapp"])[0]
    check(g["entrevista_whatsapp"].get("fija") and g["entrevista_whatsapp"]["preguntas"][0] == "¿Trabajas actualmente?",
          "Masivos no genera el guion de la entrevista con IA: es la base fija")

    print("\n--- Bloque 4 · entrevista fija por WhatsApp ---")
    from _respuestas_prefiltro import respuesta_pendiente

    q4 = postular_op("Mario Fijo", "5512990004")
    chat(q4, "Hola")
    for _ in range(15):
        db.refresh(q4)
        if pconv.estado(q4).get("resultado"):
            break
        chat(q4, respuesta_pendiente(q4))
    db.refresh(q4)
    e4 = next(x for x in q4.entrevistas if x.tipo == "whatsapp")
    transicion = (e4.transcript or [{}])[0].get("texto", "")
    check(q4.etapa == "Entrevista IA" and transicion == "Listo, Mario. Ahora te haré unas preguntas sobre tu experiencia. ¿Trabajas actualmente?",
          f"transición exacta + A. situación actual → «{transicion[:90]}»")
    pasos = [("No", "¿Cuánto tiempo llevas sin trabajar?"), ("2 meses", "¿Cuál fue tu último trabajo?"),
             ("Bodega del Centro", "¿Qué puesto tenías?"), ("Almacenista", "¿Qué hacías ahí?"),
             ("Surtía pedidos y acomodaba mercancía", "¿Cuánto tiempo trabajaste ahí?"), ("2 años", "¿Por qué saliste?"),
             ("Me despidieron", "¿Por qué te despidieron?"), ("Por faltas", "¿Tuviste otro trabajo antes de ese?"),
             # 2026-10-16 (QA): máximo 2 trabajos; el segundo con OTRAS palabras; después «¿En cuántos trabajos…?»
             ("Sí", "¿Y antes de ese, en qué empresa estuviste?"), ("Tiendas Oxxo", "¿Qué cargo tenías en ese lugar?"),
             ("Cajero", "¿A qué te dedicabas en ese empleo?"), ("Cobraba", "¿Cuánto duraste en ese empleo?"),
             ("1 año", "¿Qué te hizo dejar ese empleo?"), ("Renuncié", "¿Por qué renunciaste?"),
             ("Me cambié de ciudad", "¿En cuántos trabajos has estado en los últimos 3 años?"), ("2", "¿Has manejado montacargas?")]
    ok = True
    for resp, esperada in pasos:
        r = chat(q4, resp)
        if r != esperada:
            ok = False
            print(f"   respuesta «{resp}» → «{r}» (se esperaba «{esperada}»)")
            break
    check(ok, "A → B (hasta 2 trabajos, repregunta de despido y renuncia, cuántos trabajos) → C (pregunta propia de RH), una por mensaje")
    empresa = nombre_empresa_candidato(vo)
    r = chat(q4, "Un poco")
    # 2026-10-16 (QA): D = UNA confirmación («Antes de terminar… ¿Sigues interesado?») y después el inicio
    check(r.startswith("Antes de terminar, te confirmo: puesto ") and r.endswith("¿Sigues interesado?"), f"D. confirmación única → «{r}»")
    r = chat(q4, "Sí")
    check(r == "¿Cuándo podrías empezar?", f"D. sigue interesado → inicio → «{r}»")
    r = chat(q4, "El lunes")
    check(r == f"Con esto terminamos, Mario. Gracias por tu tiempo. El equipo de Recursos Humanos de {empresa} te contactará para "
              "agendar una entrevista.", f"cierre exacto → «{r}»")
    db.refresh(q4)
    textos = [m["texto"] for m in (next(x for x in q4.entrevistas if x.tipo == "whatsapp").transcript or []) if m.get("rol") == "assistant"]
    check(all(cuenta.nombre not in t for t in textos) and sum(empresa in t for t in textos) >= 1,
          "un solo nombre de empresa en toda la conversación (nunca el nombre interno de la Cuenta)")
    e = next(x for x in q4.entrevistas if x.tipo == "whatsapp")
    op = (e.evaluacion or {}).get("operativa") or {}
    check(e.estado == "evaluada" and op.get("fija"), "la entrevista se cierra y se evalúa")
    puntos = " ".join(op.get("puntos") or [])
    check("despid" in puntos.lower(), f"salida por despido → Puntos por validar: {op.get('puntos')}")
    check(op["requisitos"]["experiencia"]["estado"] == "cumple" and op["recomendacion"] == "con_reservas",
          "recomendación calculada sobre el historial (experiencia similar + puntos → «Con reservas»)")
    d = postulacion_dict(q4, detalle=True)
    hist = d["historialLaboral"]
    # Cambio 53: no trabaja → primero la fila «Sin empleo actual» (tiempo sin trabajar) y debajo su último empleo
    check(len(hist) == 3 and hist[0]["empresa"] == "Sin empleo actual" and hist[0]["duracion"] == "2 meses"
          and hist[1]["empresa"] == "Bodega del Centro" and hist[1]["puesto"] == "Almacenista"
          and hist[1]["duracion"] == "2 años" and "Por faltas" in hist[1]["motivo"] and hist[2]["empresa"] == "Tiendas Oxxo",
          "la ficha trae la tabla «Historial laboral» (empresa, puesto, tareas, duración, motivo)")

    q5 = postular_op("Sofía Actual", "5512990005")
    chat(q5, "Hola")
    for _ in range(15):
        db.refresh(q5)
        if pconv.estado(q5).get("resultado"):
            break
        chat(q5, respuesta_pendiente(q5))
    r = chat(q5, "Sí, trabajo")
    check(r == "¿En qué empresa trabajas?", "si trabaja actualmente, el historial arranca en presente")
    for t in ("Soriana", "Almacenista", "Recibo mercancía", "6 meses"):
        r = chat(q5, t)
    check(r == "¿Por qué quieres cambiar de trabajo?", "motivo en presente para el trabajo actual")

    seg = leer("components/dashboard/procesos/seguimiento-proceso.tsx")
    form = leer("components/dashboard/vacantes/entrevista-whatsapp-base.tsx")
    check("Historial laboral" in seg and all(h in seg for h in ("Empresa", "Puesto", "Tareas", "Duración", "Motivo")),
          "la ficha muestra la tabla «Historial laboral»")
    check("Base" in form and "Agregar pregunta" in form and "Restaurar base" in form, "la vacante muestra «Base», «+ Agregar pregunta» y «Restaurar base»")

    app.dependency_overrides.clear()
    db.close()

print(f"\n🎉 Cambios 28 y 29: {OK} comprobaciones OK")
