"""Cambio 48 (2026-10-14). Base desechable; Meta e IA SIMULADAS (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambio48.py

1. Preguntas Sí/No DIRECTAS: el prompt prohíbe «Cuéntame,» y «Para esta vacante se pide: …»; aunque la IA (o una vacante
   guardada) las traiga, el código las quita en preguntas cerradas, de número u opción.
2. Escolaridad ORDINAL (Primaria < Secundaria < Preparatoria < Licenciatura < Posgrado): Licenciatura pasa una vacante que
   pide Secundaria; Primaria se descarta. Regla «Descarta si menor que Secundaria» → la tabla muestra «Menor que Secundaria».
3. Mensaje de descarte GENÉRICO por WhatsApp (sin el motivo); el motivo queda solo en el historial de la ficha.
4. Inicio del prefiltro y descarte por la cascada del Cambio 46: texto libre → plantilla → «Pendiente de plantilla» (132001)
   → el job de reintento los manda cuando la plantilla queda activa.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_c48_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c48.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-c48"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, NotificacionEnviada, Postulacion, Usuario, UsuarioCuenta  # noqa: E402
from app.routers.webhooks import procesar_entrante  # noqa: E402
from app.services import ia  # noqa: E402
from app.services import preguntas_filtro as pfu  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import prefiltro_operativo as pop  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import reintento_plantillas  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
ENVIADOS = []
APP = RAIZ.parent / "red-human-app"
# Meta simulada: el texto libre solo pasa a quien ESCRIBIÓ (ventana de 24 h); una plantilla que no está en ACTIVAS → 132001
VENTANA = set()
ACTIVAS = {"inicio_entrevista_rh", "descarte_postulacion_rh"}


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _meta_falso(cuerpo):
    ENVIADOS.append(cuerpo)
    if cuerpo.get("type") == "text" and cuerpo.get("to") not in VENTANA:
        return {"enviado": False, "proveedor": "meta", "detalle": "131047: Re-engagement message", "codigo": 131047}
    if cuerpo.get("type") == "template" and cuerpo["template"]["name"] not in ACTIVAS:
        return {"enviado": False, "proveedor": "meta", "detalle": "132001: Template name does not exist in the translation",
                "codigo": 132001}
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "mensaje_id": f"wamid.{len(ENVIADOS)}"}


whatsapp._meta_post = _meta_falso


def plantillas(desde=0, nombre=""):
    return [c for c in ENVIADOS[desde:] if c.get("type") == "template" and (not nombre or c["template"]["name"] == nombre)]


def textos(desde=0, tel=""):
    return [c["text"]["body"] for c in ENVIADOS[desde:] if c.get("type") == "text" and (not tel or c["to"] == "52" + tel)]


def _msg(tel, cuerpo):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": "Candidato"}, "wa_id": f"52{tel}"}],
        "messages": [{"from": f"52{tel}", "id": f"wamid.in.{tel}.{len(ENVIADOS)}.{id(cuerpo)}", **cuerpo}]}}]}]}


def escribe(tel, t):
    VENTANA.add("52" + tel)  # escribió: se abre su ventana de 24 h
    return asyncio.run(procesar_entrante(db, whatsapp.parsear_webhook(_msg(tel, {"type": "text", "text": {"body": t}}))))


def pulsa(tel, t):
    VENTANA.add("52" + tel)
    return asyncio.run(procesar_entrante(db, whatsapp.parsear_webhook(_msg(tel, {"type": "button", "button": {"text": t, "payload": t}}))))


GENERICO = ("Gracias por tu interés en Almacenes Centro. En esta ocasión no continuaremos con tu postulación. Te tendremos en "
            "cuenta para futuras vacantes.")
RELLENO = re.compile(r"^\s*(cu[eé]ntame|plat[ií]came|dime)\b", re.IGNORECASE)

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="C48 interna", nombre_comercial="Almacenes Centro", estado="Activa", slug="c48-centro")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    MASIVOS = sproc.ruta_base("masivos_sin_documentos")["pasos"]

    # ===================================================== 1. Preguntas Sí/No directas
    print("\n--- 1. Preguntas Sí/No directas, sin «Cuéntame» ni «Para esta vacante se pide» ---")
    reglas = ia._REGLAS
    check("Cuéntame" in reglas and "PROHIBIDO el relleno conversacional" in reglas and "Para esta vacante se pide" in reglas
          and "¿Puedes cargar material pesado?" in reglas, "el prompt de la IA exige preguntas directas y prohíbe «Cuéntame,» y la frase fija")
    desc = ia.PreguntaRequisito.model_fields["pregunta"].description
    check("PROHIBIDO el relleno" in desc and "Cuéntame, ¿" not in desc, "el esquema de la pregunta por requisito ya no pone «Cuéntame,» como ejemplo")
    propuestas = [{"requisito": "Cargar material pesado", "pregunta": "Cuéntame, ¿puedes cargar material pesado?"},
                  {"requisito": "Disponibilidad para rolar turnos", "pregunta": "Platícame, ¿tienes disponibilidad para rolar turnos?"},
                  {"requisito": "Manejo de montacargas", "pregunta": "Para esta vacante se pide: manejo de montacargas. ¿Cuál es tu caso?"}]
    reqs = [x["requisito"] for x in propuestas]
    pr = {x["requisito"]: x["pregunta"] for x in ia.asegurar_preguntas_requisitos(reqs, propuestas)}
    check(pr["Cargar material pesado"] == "¿Puedes cargar material pesado?", f"IA con «Cuéntame,» → directa ({pr['Cargar material pesado']})")
    check(pr["Disponibilidad para rolar turnos"] == "¿Tienes disponibilidad para rolar turnos?", f"«Platícame,» también se quita ({pr['Disponibilidad para rolar turnos']})")
    check("Para esta vacante se pide" not in pr["Manejo de montacargas"] and pr["Manejo de montacargas"].startswith("¿"),
          f"la frase fija de la IA se rechaza y se redacta directa ({pr['Manejo de montacargas']})")
    check(all(not RELLENO.match(ia.pregunta_natural_requisito(r)) for r in
              ("Cargar material pesado", "2 años de experiencia en almacén", "Disponibilidad para rolar turnos", "Manejo de Excel")),
          "sin IA, ninguna pregunta de requisito empieza con «Cuéntame,»")
    lista = pfu.armar(reqs, titulo="Almacenista", naturales={ia._norm_req(x["requisito"]): x["pregunta"] for x in propuestas},
                      previas=[{"requisito": "Cargar material pesado", "indispensable": True, "tipo": "si_no", "descarta": True,
                                "pregunta": "Cuéntame, ¿puedes cargar cajas pesadas?"}])
    cerradas = [q for q in lista if q["tipo"] != "abierta"]
    check(cerradas and all(not RELLENO.match(q["pregunta"]) and not pfu.tiene_frase_prohibida(q["pregunta"]) for q in cerradas),
          f"«Preguntas filtro»: cerradas, de número u opción sin relleno ni frases fijas ({[q['pregunta'] for q in cerradas[:3]]})")
    check(next(q for q in lista if q["requisito"] == "Cargar material pesado")["pregunta"] == "¿Puedes cargar cajas pesadas?",
          "lo que RH ajustó se conserva, sin el «Cuéntame,»")
    base_op = pop._pregunta_indispensable_base(0, "Manejo de montacargas")
    check("Para esta vacante se pide" not in base_op["pregunta"] and "caso" not in base_op["pregunta"],
          f"prefiltro operativo (Masivos): pregunta directa ({base_op['pregunta']})")
    check(pop.clasificar({**base_op}, "sí")[0] == "si", "y un «sí» la responde (es Sí/No directa)")
    r = client.post("/vacantes/generar", json={
        "titulo": "Almacenista", "area": "Operaciones", "seniority": "Junior", "ubicacion_estado": "Ciudad de México",
        "ubicacion_municipio": "Iztapalapa", "modalidad": "Presencial", "sueldo_desde": 9500, "sueldo_periodicidad": "mensual",
        "descripcion": "Surtir pedidos y cargar cajas.", "requisitos_indispensables": ["Secundaria terminada", "Cargar material pesado",
                                                                                       "Disponibilidad para rolar turnos"],
        "proceso": {"pasos": MASIVOS}})
    g = r.json()
    check(r.status_code == 200 and all(not RELLENO.match(q["pregunta"]) and "Para esta vacante se pide" not in q["pregunta"]
                                       for q in g["filtro"] if q["tipo"] != "abierta"),
          f"/vacantes/generar: preguntas filtro directas ({[q['pregunta'] for q in g['filtro'][:3]]})")
    check(next(q for q in g["filtro"] if q["requisito"] == "Cargar material pesado")["pregunta"] == "¿Puedes cargar material pesado?",
          "«Cargar material pesado» → «¿Puedes cargar material pesado?»")

    # ===================================================== 2. Escolaridad ordinal
    print("\n--- 2. Escolaridad ordinal ---")
    esc = next(q for q in g["filtro"] if q.get("dato") == "escolaridad")
    check(esc["regla"] == "Descarta si menor que Secundaria" and esc["nivel"] == 2, f"regla ordinal ({esc['regla']})")
    check(esc["opciones"] == ["Primaria", "Secundaria", "Preparatoria", "Licenciatura", "Posgrado"], f"opciones con jerarquía ({esc['opciones']})")
    vieja = pfu.normalizar_item({"requisito": "Secundaria terminada", "dato": "escolaridad", "tipo": "opcion", "descarta": True,
                                 "regla": "Descarta si no terminó la secundaria", "pregunta": "¿Cuál es tu último grado de estudios?"})
    check(vieja["regla"] == "Descarta si menor que Secundaria", "una vacante guardada con la regla anterior se lee con la ordinal")
    c_esc = {"id": "x", "dato": "escolaridad", "nivel_requerido": 2, "criterio": "Secundaria terminada", "pregunta": esc["pregunta"],
             "tipo": "opcion"}
    for resp, esperado in (("Licenciatura", "si"), ("Posgrado", "si"), ("Tengo una maestría", "si"), ("Preparatoria", "si"),
                           ("Secundaria", "si"), ("Primaria", "no"), ("Solo la primaria", "no")):
        check(pfu.clasificar(c_esc, resp)[0] == esperado, f"pide Secundaria · responde «{resp}» → {esperado}")
        check(pconv.clasificar({"criterio": "Secundaria terminada", "pregunta": "¿Terminaste la secundaria?"}, resp) == esperado,
              f"(ruta anterior Sí/No) «{resp}» → {esperado}")
    check(pconv.cumple_nivel(4, "Posgrado") == "si" and pconv.cumple_nivel(4, "Preparatoria") == "no",
          "pide Licenciatura: Posgrado pasa, Preparatoria no")

    nodo = shutil.which("node")
    fuente = (APP / "lib" / "api.ts").read_text(encoding="utf-8")
    bloque = re.search(r"export const NIVELES_ESTUDIO.*?\n}\n", fuente, re.S)
    check(bloque is not None, "el frontend define la jerarquía y `textoDescartaSi`")
    programa = r"""
const ts = require(process.argv[1] + '/node_modules/typescript');
const js = ts.transpileModule(process.argv[2], { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const m = { exports: {} }; new Function('module', 'exports', js)(m, m.exports);
const q = JSON.parse(process.argv[3]);
console.log(JSON.stringify(q.map(m.exports.textoDescartaSi)));
"""
    casos = [{"descarta": True, "dato": "escolaridad", "nivel": 2, "regla": esc["regla"]},
             {"descarta": True, "dato": "requisito", "regla": "Descarta si responde que no"},
             {"descarta": False, "dato": "zona", "regla": "No descarta"}]
    snippet = bloque.group(0).replace("Pick<PreguntaFiltroU, \"descarta\" | \"regla\" | \"dato\" | \"nivel\">",
                                      "{descarta: boolean; regla: string; dato?: string; nivel?: number}")
    salida = subprocess.run([nodo, "-e", programa, str(APP), snippet, json.dumps(casos)], capture_output=True, text=True, encoding="utf-8")
    check(salida.returncode == 0, f"`textoDescartaSi` corre en Node ({salida.stderr[:200]})")
    vistos = json.loads(salida.stdout.strip().splitlines()[-1])
    check(vistos == ["Menor que Secundaria", "Responde que no", "No descarta"], f"columna «Descarta si» → {vistos}")
    form = (APP / "components" / "dashboard" / "vacantes" / "formulario-contenido.tsx").read_text(encoding="utf-8")
    check(form.count("textoDescartaSi(q)") == 2 and 'q.regla.replace(/^Descarta si /' not in form,
          "la tabla «Requisito · Pregunta · Descarta si» usa `textoDescartaSi`")

    # vacante real: Licenciatura pasa, Primaria se descarta (chat)
    r = client.post("/vacantes", json={
        "titulo": "Almacenista", "area": "Operaciones", "seniority": "Junior", "ubicacion_estado": "Ciudad de México",
        "ubicacion_municipio": "Iztapalapa", "modalidad": "Presencial", "sueldo_desde": 9500, "sueldo_periodicidad": "mensual",
        "requisitos_indispensables": ["Secundaria terminada", "Cargar material pesado", "Disponibilidad para rolar turnos"],
        "descripcion": "Surtir pedidos.", "responsabilidades": ["Surtir pedidos"], "proceso": {"pasos": MASIVOS},
        "publicar": True, "plataformas": ["Portal"]})
    check(r.status_code == 201, f"alta de la vacante ({r.status_code}: {r.text[:200]})")
    vac = r.json()
    app.dependency_overrides.pop(cuenta_actual, None)

    def postular(nombre, tel, respuestas=None):
        datos = {"vacante": vac["slug"], "nombre": nombre, "telefono": tel, "consentimiento": "true"}
        if respuestas:
            datos["respuestas"] = json.dumps(respuestas)
        r = client.post("/candidatos/postular", data=datos)
        assert r.status_code == 201, r.text
        return db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()

    p_lic = postular("Laura Gómez", "5548000001")
    pulsa("5548000001", "Sí, empezar ahora")
    r = escribe("5548000001", "Licenciatura")
    db.refresh(p_lic)
    check(p_lic.activa and r.get("clasificacion_final") != "no_cumple" and "material" in r["respuesta"].lower(),
          f"pide Secundaria · «Licenciatura» pasa y sigue con el siguiente requisito ({r['respuesta']})")

    # ===================================================== 3. Mensaje de descarte genérico
    print("\n--- 3. Descarte: mensaje genérico, motivo solo en el historial ---")
    p_pri = postular("Pedro Ruiz", "5548000002")
    pulsa("5548000002", "Sí, empezar ahora")
    desde = len(ENVIADOS)
    r = escribe("5548000002", "Primaria")
    db.refresh(p_pri)
    check(not p_pri.activa and p_pri.motivo_cierre == "descartado", "pide Secundaria · «Primaria» → descartado")
    check(r["respuesta"] == GENERICO, f"respuesta = mensaje genérico exacto ({r['respuesta']})")
    salidos = textos(desde, "5548000002")
    check(salidos == [GENERICO], f"lo que salió por WhatsApp es el genérico (texto libre: su ventana está abierta) ({salidos})")
    check(not any(re.search(r"secundaria|requisito|necesitamos|primaria", t, re.I) for t in salidos), "sin el motivo del rechazo")
    hist = next(h for h in p_pri.historial if h.get("evento") == "descartado")
    check("Secundaria terminada" in hist["motivo"] and "Primaria" in hist["motivo"],
          f"el historial de la ficha guarda el motivo detallado ({hist['motivo']})")
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    ficha = client.get(f"/candidatos/{p_pri.codigo}").json()
    check("Secundaria terminada" in json.dumps(ficha.get("historial") or [], ensure_ascii=False), "la ficha (solo RH) muestra el motivo")
    app.dependency_overrides.pop(cuenta_actual, None)
    nf = db.query(NotificacionEnviada).filter_by(evento="descarte_prefiltro", destino=p_pri.telefono).one()
    check(nf.enviado and nf.estado == "enviado", "el descarte queda en `notificaciones_enviadas` (enviado)")

    # ===================================================== 4. Cascada del Cambio 46
    print("\n--- 4. Inicio y descarte por la cascada (texto libre → plantilla → «Pendiente de plantilla») ---")
    desde = len(ENVIADOS)
    p_ini = postular("Ana Soto", "5548000003")
    db.refresh(p_ini)
    tipos = [(c["type"], c.get("template", {}).get("name")) for c in ENVIADOS[desde:] if c.get("to") == "525548000003"]
    check(tipos == [("text", None), ("template", "inicio_entrevista_rh")],
          f"inicio: primero texto libre (rechazado, sin ventana) y luego la plantilla ({tipos})")
    check((p_ini.analisis.get("plantilla_inicio") or {}).get("entregada"), "la plantilla de inicio quedó entregada")

    ACTIVAS.discard("inicio_entrevista_rh")
    p_pend = postular("Beto Lara", "5548000004")
    db.refresh(p_pend)
    pi = p_pend.analisis.get("plantilla_inicio") or {}
    nf = db.query(NotificacionEnviada).filter_by(evento="inicio_prefiltro", destino=p_pend.telefono).one()
    check(pi.get("pendiente_plantilla") and not pi.get("entregada") and nf.estado == "pendiente" and not nf.enviado,
          f"132001 en la plantilla de inicio → «Pendiente de plantilla», nunca «Fallido» ({nf.estado})")
    check((nf.reintento or {}).get("plantilla") == "inicio_entrevista_rh" and (nf.reintento or {}).get("postulacion_id") == p_pend.id,
          "con lo necesario para reintentar")
    res = asyncio.run(reintento_plantillas.reintentar(db))
    db.refresh(nf)
    check(res["pendientes"] >= 1 and nf.estado == "pendiente", "el job reintenta: la plantilla sigue sin activarse → sigue pendiente")
    ACTIVAS.add("inicio_entrevista_rh")
    desde = len(ENVIADOS)
    asyncio.run(reintento_plantillas.reintentar(db))
    db.refresh(nf)
    db.refresh(p_pend)
    check(nf.estado == "enviado" and nf.enviado and plantillas(desde, "inicio_entrevista_rh"),
          "la plantilla queda activa → el job la manda y la marca «Enviado»")
    check((p_pend.analisis.get("plantilla_inicio") or {}).get("entregada") and not (p_pend.analisis.get("plantilla_inicio") or {}).get("pendiente_plantilla"),
          "y la postulación la registra como entregada (conversación ligada)")

    # descarte desde el formulario web (sin ventana de 24 h): plantilla de descarte en cascada
    ACTIVAS.discard("descarte_postulacion_rh")
    desde = len(ENVIADOS)
    p_web = postular("Carla Méndez", "5548000005", [{"pregunta": esc["pregunta"], "respuesta": "Primaria"}])
    db.refresh(p_web)
    check(not p_web.activa and p_web.motivo_cierre == "descartado", "formulario web «Primaria» → descartado")
    al_numero = [c for c in ENVIADOS[desde:] if c.get("to") == "525548000005"]
    libres = [c["text"]["body"] for c in al_numero if c["type"] == "text"]
    check(GENERICO in libres, "descarte: primero el texto libre genérico (rechazado: el candidato no ha escrito)")
    desc_tpl = [c for c in al_numero if c["type"] == "template" and c["template"]["name"] == "descarte_postulacion_rh"]
    check(desc_tpl and [x["text"] for x in desc_tpl[0]["template"]["components"][0]["parameters"]] == ["Almacenes Centro"],
          "luego la plantilla de descarte con la empresa (sin el motivo)")
    nf = db.query(NotificacionEnviada).filter_by(evento="descarte_prefiltro", destino=p_web.telefono).one()
    check(nf.estado == "pendiente" and (nf.reintento or {}).get("plantilla") == "descarte_postulacion_rh",
          "plantilla de descarte en revisión (132001) → «Pendiente de plantilla»")
    check("Secundaria terminada" in next(h for h in p_web.historial if h.get("evento") == "descartado")["motivo"],
          "el motivo del descarte web queda en el historial")
    ACTIVAS.add("descarte_postulacion_rh")
    asyncio.run(reintento_plantillas.reintentar(db))
    db.refresh(nf)
    check(nf.estado == "enviado", "la plantilla de descarte se activa → el job la manda («Enviado»)")

    # un inicio pendiente ya no se manda si el candidato escribió mientras tanto
    ACTIVAS.discard("inicio_entrevista_rh")
    p_esc = postular("Diego Paz", "5548000006")
    nf = db.query(NotificacionEnviada).filter_by(evento="inicio_prefiltro", destino=p_esc.telefono).one()
    check(nf.estado == "pendiente", "inicio pendiente")
    escribe("5548000006", "Hola")
    ACTIVAS.add("inicio_entrevista_rh")
    desde = len(ENVIADOS)
    asyncio.run(reintento_plantillas.reintentar(db))
    db.refresh(nf)
    check(nf.estado == "omitido" and not plantillas(desde, "inicio_entrevista_rh"),
          "el candidato escribió mientras tanto → el reintento ya no manda la plantilla de inicio")

    db.close()

print(f"\n🎉 Cambio 48: {OK} comprobaciones OK")
