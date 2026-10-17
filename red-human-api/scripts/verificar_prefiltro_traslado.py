"""Prefiltro web de 3 preguntas Sí/No y datos de traslado por WhatsApp (prueba del 2026-10-11 por la tarde, Bloque 1).
Base desechable, SIN red.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_prefiltro_traslado.py

Cubre: el prefiltro web se responde solo con Sí / No (sin «Parcial») y lleva al menos 3 preguntas clave (indispensables,
escolaridad, exigencias, ubicación u horario); con menos de 3 la vacante no se guarda y RH ve el mensaje exacto. El
prefiltro de WhatsApp pregunta, una por mensaje y en orden estricto, escolaridad exacta → colonia/municipio → tiempo de
traslado → medio de transporte; ante una respuesta vaga repregunta UNA sola vez; la escolaridad que no cumple descarta
de inmediato con el mensaje de cierre y el traslado nunca descarta.

Bloque 2: la transición a la entrevista es UN mensaje sin reglas internas ni permiso, concatenado con la primera
pregunta («Listo, [nombre]. Soy Red Human, te haré unas preguntas sobre tu experiencia.\n¿…?»; el aviso de IA va en el
aviso de privacidad); la ficha muestra zona, traslado y transporte; traslado > 90 min = alerta en Puntos por validar y
> 120 min = la entrevista queda «No recomendable» sin descartar.
"""

import asyncio
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
_dir = _temporal.directorio("rh_pf_traslado_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "pf_traslado.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "RESEND_API_KEY",
          "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-pf-traslado"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.deps import usuario_actual, usuario_admin, usuario_decisor  # noqa: E402
from app.models import Cuenta, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.services import entrevista_operativa as eop  # noqa: E402
from app.services import entrevistas as sent  # noqa: E402
from app.services import guiones as sgui  # noqa: E402
from app.services import ia  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp as swa  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
WA = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _meta_falso(cuerpo):
    WA.append(cuerpo)
    return {"enviado": True, "proveedor": "meta", "detalle": "", "wa_id": f"wamid.pft.{len(WA)}"}


settings.whatsapp_provider = "meta"
settings.meta_whatsapp_token, settings.meta_phone_number_id = "x", "123"
swa._meta_post = _meta_falso

# ================= 1. Generación: prefiltro web ≥ 3 Sí/No y datos de WhatsApp en orden =================
print("\n--- 1. Generación del prefiltro ---")
ficha = ia.FichaGuion(titulo="Cargador", requisitos_indispensables=["Secundaria terminada"],
                      responsabilidades=["Cargar y descargar cajas de hasta 25 kg", "Acomodar mercancía en tarimas"],
                      ubicacion="Tlalnepantla, Estado de México", sueldo="Desde $9,000 MXN mensuales")
web, wa = ia.asegurar_prefiltros([], [], ficha, hay_web=True, hay_whatsapp=True)
check(len(web) >= 3, f"con 1 indispensable el prefiltro web se completa a {len(web)} preguntas clave (exigencias / ubicación)")
check(all(q["opciones"] == ["Sí", "No"] for q in web) and not any("Parcial" in str(q) for q in web), "todas se responden solo Sí / No")
check([q for q in web if q["descarta"]] == [web[0]] and web[0]["valida"] == "Secundaria terminada",
      "solo el indispensable de RH es eliminatorio (lo agregado nunca descarta)")
check(any("cargar y descargar cajas" in q["pregunta"] for q in web) and any(q.get("clave") == "ubicacion" for q in web),
      "salen de las exigencias del trabajo y de la ubicación")
check([q.get("dato") for q in wa[:5]] == ["escolaridad", "zona", "traslado", "transporte", "sueldo"],
      "WhatsApp: escolaridad → colonia/municipio → traslado → transporte → sueldo, en ese orden")
check(wa[4]["pregunta"] == "El sueldo es desde $9,000 MXN mensuales. ¿Te parece bien?" and not wa[4]["descarta"],
      "5) «El sueldo es desde… ¿Te parece bien?» — se registra, nunca descarta")
_, wa_sin = ia.asegurar_prefiltros([], [], ficha.model_copy(update={"sueldo": ""}), hay_web=True, hay_whatsapp=True)
check("sueldo" not in [q.get("dato") for q in wa_sin], "sin sueldo capturado no se pregunta (nunca se inventa)")
mod, _ = ia.asegurar_prefiltros([], [], ia.FichaGuion(titulo="Capturista", requisitos_indispensables=["Saber Excel"],
                                                      modalidad="Híbrido", horario="Lunes a viernes de 9 a 6"), hay_web=True, hay_whatsapp=False)
check(len(mod) == 3 and any(q.get("clave") == "modalidad" for q in mod) and any(q.get("clave") == "horario" for q in mod),
      "la modalidad y el horario también completan las 3 preguntas clave")
check(wa[0]["descarta"] and wa[0]["valida"] == "Secundaria terminada" and not any(q.get("reconfirma") == "Secundaria terminada" for q in wa),
      "la escolaridad exacta reconfirma el nivel indispensable (sin pregunta sí/no repetida)")
check("Tlalnepantla" in wa[2]["pregunta"] and not wa[2]["descarta"], "el traslado usa la ubicación real y nunca descarta")
check(sgui.normalizar_pregunta({"pregunta": "¿Tienes licencia?", "tipo": "si_no", "opciones": ["Sí", "No", "Parcial"]})["opciones"] == ["Sí", "No"],
      "una pregunta guardada con «Parcial» se normaliza a Sí / No")
pocos, _ = ia.asegurar_prefiltros([], [], ia.FichaGuion(titulo="Ayudante", requisitos_indispensables=["Saber leer"]), hay_web=True,
                                 hay_whatsapp=False)
check(len(pocos) < 3, "sin datos suficientes el generador no inventa: quedan menos de 3 (RH debe agregar)")

RUTA = [{"id": "sw", "tipo": "solicitud_web", "etapa": "Prefiltro"},
        {"id": "pw", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro", "obligatorio": True},
        {"id": "ew", "tipo": "entrevista_whatsapp", "etapa": "Entrevista IA", "obligatorio": True},
        # Cambio 29: con la entrevista con avatar (opcional) la ruta NO es Masivos — aquí se prueba el prefiltro y la
        # entrevista configurables; la base fija de Masivos se prueba en verificar_cambios28_29.py
        {"id": "av", "tipo": "entrevista_agente", "etapa": "Entrevista IA", "obligatorio": False},
        {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"}]

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    obtener(db).modo_prueba = False
    cuenta = Cuenta(nombre="PF Traslado", nombre_comercial="PF Traslado", estado="Activa", slug="pf-traslado")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_admin, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    h = {"X-Cuenta-Id": str(cuenta.id)}

    # ================= 2. Mínimo de 3 preguntas al guardar =================
    print("\n--- 2. Bloqueo con menos de 3 preguntas ---")
    base = {"titulo": "Cargador demo", "area": "Operaciones", "ubicacion_estado": "Ciudad de México", "ubicacion_municipio": "Iztapalapa",
            "modalidad": "Presencial", "descripcion": "Carga y descarga.", "requisitos_indispensables": ["Secundaria terminada"],
            "proceso": {"pasos": RUTA}, "generar_si_falta": False}
    dos = [{"pregunta": "¿Terminaste la secundaria?", "tipo": "si_no", "valida": "Secundaria terminada", "respuesta_esperada": "Sí", "descarta": True},
           {"pregunta": "¿Puedes rolar turnos?", "tipo": "si_no", "valida": "Rolar turnos", "respuesta_esperada": "Sí", "descarta": False}]
    r = client.post("/vacantes", headers=h, json={**base, "preguntas_filtro": dos})
    check(r.status_code == 400 and r.json()["detail"] == ia.MENSAJE_MIN_PREFILTRO,
          "con 2 preguntas NO se crea: «Para el prefiltro necesitamos al menos 3 requisitos clave…»")
    tres = dos + [{"pregunta": "¿Puedes cargar cajas de 25 kg?", "tipo": "si_no", "valida": "Cargar 25 kg", "respuesta_esperada": "Sí"}]
    r = client.post("/vacantes", headers=h, json={**base, "preguntas_filtro": tres})
    check(r.status_code == 201, "con 3 preguntas sí se crea")
    cod = r.json()["id"]
    r = client.patch(f"/vacantes/{cod}", headers=h, json={"preguntas_filtro": dos})
    check(r.status_code == 400 and "al menos 3 requisitos clave" in r.json()["detail"], "editar y dejar 2 también se bloquea")

    # ================= 3. Conversación por WhatsApp =================
    print("\n--- 3. Datos por WhatsApp: uno por mensaje, repregunta única ---")
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).first()
    v.cuenta_id, v.titulo, v.ubicacion, v.sueldo = cuenta.id, "Cargador", "Tlalnepantla", "Desde $9,000 MXN mensuales"
    v.enfoque_entrevista, v.guiones = "operativo", {}
    v.preguntas_filtro = web
    v.preguntas_filtro_whatsapp = wa
    v.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": RUTA})
    db.commit()
    tel = iter(range(5542220001, 5542229999))

    def nueva(nombre):
        c = _crear_candidato(db, cuenta.id, nombre, "WhatsApp", False, telefono=str(next(tel)))
        p = crear_postulacion(db, c, db.get(Vacante, v.id), cuenta.id, "whatsapp", consentimiento=True)
        db.commit()
        return p

    def turno(p, texto):
        return asyncio.run(pconv.turno(db, db.query(Postulacion).filter_by(codigo=p.codigo).one(), texto, "whatsapp"))

    def fresca(p):
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=p.codigo).one()

    p = nueva("Ana Torres")
    r = turno(p, "Hola")
    check(r["respuesta"].endswith("¿Cuál es tu último grado de estudios?") and "Parcial" not in r["respuesta"], "1) primero la escolaridad exacta")
    r = turno(p, "pues normal")
    check(r["respuesta"] == pconv.REPREGUNTA_DATO["escolaridad"], "respuesta vaga → repregunta por un dato concreto")
    r = turno(p, "Terminé la secundaria")
    check(r["respuesta"].endswith("¿En qué colonia y municipio vives?"), "2) después colonia y municipio")
    r = turno(p, "cerca")
    check(r["respuesta"] == pconv.REPREGUNTA_DATO["zona"], "«cerca» es vago → pide el nombre de la colonia")
    r = turno(p, "por aquí")
    check("¿Cuánto tiempo haces de tu casa a Tlalnepantla" in r["respuesta"],
          "la repregunta es UNA sola vez: si sigue vaga, sigue con el traslado (3)")
    p = fresca(p)
    check(any("Colonia y municipio" in x for x in p.analisis.get("puntos_validar_prefiltro") or []),
          "lo que siguió vago queda en Puntos por validar")
    r = turno(p, "como 2 horas y media")
    check(r["respuesta"].endswith("¿En qué transporte llegarías?"), "4) al final el medio de transporte; el traslado largo NO descarta")
    r = turno(p, "En camión y metro")
    # 2026-10-16 (QA): sueldo publicado → «El sueldo es… ¿Te interesa?» (nunca la expectativa)
    check("esperas ganar" not in r["respuesta"] and "expectativa" not in r["respuesta"] and r["respuesta"].endswith("¿Te interesa?"),
          "5) sueldo publicado: «El sueldo es… ¿Te interesa?» (sin expectativa)")
    r = turno(p, "Sí")
    p = fresca(p)
    d = p.analisis.get("prefiltro_datos") or {}
    check(p.activa and (p.analisis.get("prefiltro_web") or {}).get("resultado") == "cumple", "con la escolaridad cumplida el prefiltro aprueba")
    check(d.get("traslado_min") == 150 and d.get("nivel") == "Secundaria" and "Metro" in d.get("transporte_medio", "")
          and d.get("zona") == "por aquí", "los datos quedan guardados para la ficha (zona, minutos, transporte, escolaridad)")
    check(d.get("sueldo_expectativa") is None and not d.get("sueldo_sobre_tope"), "sin expectativa registrada (no se pidió)")

    print("\n--- 3b. Transición natural a la entrevista (Bloque 2) ---")
    desde = 0
    if sent.entrevista_whatsapp_activa(p) is None:
        asyncio.run(sent.iniciar_whatsapp(db, p, "prueba"))
        db.commit()
    p = fresca(p)
    e = sent.entrevista_whatsapp_activa(p)
    intro = next((c["text"]["body"] for c in WA[desde:] if c.get("type") == "text" and c.get("to", "").endswith(p.telefono)
                  and c["text"]["body"].startswith("Listo")), "")
    primera = sent.primera_pregunta(e)
    check(intro == f"Listo, Ana. Ahora te haré unas preguntas sobre tu experiencia. {primera}",
          f"UN solo mensaje con la primera pregunta: {intro!r}")
    check(primera == "¿Cuál fue tu último trabajo?", "la primera pregunta del guion operativo es «¿Cuál fue tu último trabajo?»")
    check(not any(x in intro for x in ("Comenzamos", "10 minutos", "revisa", "inteligencia artificial")),
          "sin «¿Comenzamos?», sin «unos 10 minutos», sin «las revisa RH» y sin el aviso de IA")
    check(e.estado == "en_curso" and e.consentimiento, "la entrevista arranca de inmediato (el consentimiento es el del aviso de privacidad)")
    aviso = __import__("app.routers.webhooks", fromlist=["_texto_aviso_privacidad"])._texto_aviso_privacidad("Ana", v)
    check("inteligencia artificial" in aviso, "el aviso de IA vive en el aviso de privacidad")
    s1 = asyncio.run(sent.turno_whatsapp(db, p, e, "Ayudante en una bodega de abarrotes", "whatsapp"))
    check(s1["respuesta"] != primera and "Comenzar" not in s1["respuesta"], "la respuesta cuenta como respuesta a la primera pregunta y sigue")

    print("\n--- 3c. Ficha: zona, traslado, transporte y alertas ---")
    f = client.get(f"/candidatos/{p.codigo}", headers=h).json()
    d = f.get("prefiltroDatos") or {}
    check(d.get("zona") == "por aquí" and d.get("trasladoMin") == 150 and "Metro" in d.get("transporte", "") and d.get("alerta") is None,
          "la ficha trae zona, traslado (~150 min) y transporte — Cambio 19: sin alerta por minutos (solo se registra)")
    check(not d.get("sueldoExpectativa"), "la ficha no trae una expectativa que no se pidió (sueldo publicado)")
    check(not any("traslado" in x.lower() for x in f.get("puntosPorValidar") or []), "Cambio 19: el traslado no genera Puntos por validar")
    for _ in range(40):  # el resto de la entrevista (motor sin IA) hasta la despedida
        e = sent.entrevista_whatsapp_activa(fresca(p))
        if e is None:
            break
        asyncio.run(sent.turno_whatsapp(db, fresca(p), e, "Sí, cargaba cajas en la bodega 2 años", "whatsapp"))
    p = fresca(p)
    ent = next(x for x in p.entrevistas if x.tipo == "whatsapp")
    ev = ent.evaluacion or {}
    op = ev.get("operativa") or {}
    # 2026-10-16: el sueldo ya es la expectativa (9 mil, dentro del rango): no genera punto; el traslado tampoco cuenta
    check(ent.estado == "evaluada" and not any(x.startswith(("Sueldo: no aceptó", "Expectativa de sueldo")) for x in ev.get("riesgos") or [])
          and not any("traslado" in x.lower() for x in ev.get("riesgos") or []) and op.get("recomendacion"),
          "Cambio 19: una expectativa dentro del rango no es Punto por validar; los 150 min de traslado no cuentan")
    check(p.activa and p.motivo_cierre in ("", None), "…y NO se descarta (RH decide)")
    check(not any(hasattr(eop, x) for x in ("aplicar_traslado", "alerta_traslado")), "sin umbrales fijos de traslado en el código")

    print("\n--- 4. Escolaridad que no cumple → descarte inmediato ---")
    q = nueva("Beto Parcial")
    turno(q, "Hola")
    r = turno(q, "Llegué hasta 2do de secundaria")
    q = fresca(q)
    check(not q.activa and q.motivo_cierre == "descartado" and q.estado == "no_cumple", "«2do de secundaria» con secundaria terminada → DESCARTADO")
    check("no continuaremos con tu postulación" in r["respuesta"] and "secundaria" not in r["respuesta"].lower() and "colonia" not in r["respuesta"],
          "mensaje de cierre de inmediato (ya no pregunta colonia ni traslado)")

    print("\n--- 5. Formulario web + WhatsApp ---")
    w = nueva("Carla Web")
    w.analisis = {"respuestas_web": [{"pregunta": web[0]["pregunta"], "respuesta": "Sí"}]}
    db.commit()
    r = turno(w, "Hola")
    check("¿Cuál es tu último grado de estudios?" in r["respuesta"], "con «Sí» en el formulario también pide la escolaridad exacta")
    r = turno(w, "primero de secundaria")
    w = fresca(w)
    check(not w.activa and w.motivo_cierre == "descartado" and (w.analisis.get("inconsistencias") or [{}])[-1].get("fuente") == "reconfirmacion",
          "el dato contradice el «Sí» → descarta y la contradicción queda registrada")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — prefiltro web de 3 preguntas, traslado por WhatsApp y transición natural")
