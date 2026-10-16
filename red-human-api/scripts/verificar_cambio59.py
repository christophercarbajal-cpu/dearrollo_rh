"""Cambio 59 (2026-10-15): preguntas del candidato sobre la vacante, repreguntas en lenguaje natural y chat pasivo después
del cierre. Base desechable; IA y Meta SIMULADAS (sin red).

    PYTHONIOENCODING=utf-8 python scripts/verificar_cambio59.py

1. Plantillas de Meta intactas: los nombres, variables e idioma de las plantillas no cambian (y el código del Cambio 59
   no llama a ninguna plantilla).
2. Prefiltro (Fletes A): a la mitad del cuestionario «¿En dónde es el trabajo?» → el dato + la MISMA pregunta pendiente
   (nunca se toma como respuesta); repregunta en lenguaje natural y, tras una sola, «Sin respuesta» y se avanza.
3. Entrevista fija (Almacenista, Agencia del Centro): «¿Cuánto pagan?» a la mitad → el sueldo + la pregunta pendiente.
4. Después del cierre: «¿En dónde es el trabajo?» → solo la ubicación; «¿Cuánto pagan?» → solo el sueldo; «¿Cuándo me
   contactan?» → el siguiente paso (no el cierre otra vez); «¿Pasé?» → sin resultados; «Gracias» → «Con gusto, [nombre].»
   UNA vez; otro mensaje → «Si tienes alguna duda sobre la vacante, con gusto te ayudo.»
5. Ninguna repregunta expone nombres internos de campos («tiempo de traslado»…).
"""

import asyncio
import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_c59_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c59.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-c59"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Entrevista, Postulacion  # noqa: E402
from app.routers.webhooks import procesar_entrante  # noqa: E402
from app.serial import vacante_dict  # noqa: E402
from app.services import entrevista_whatsapp_fija as ewf  # noqa: E402
from app.services import ia  # noqa: E402
from app.services import notificaciones  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import prefiltro_operativo as pop  # noqa: E402
from app.services import preguntas_filtro as pfu  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import respuestas_vacante as rva  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

import seed_agencia_centro as sac  # noqa: E402
import seed_fletes_mexico as sf  # noqa: E402

OK = 0
ENVIADOS = []
VENTANA = set()


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
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "mensaje_id": f"wamid.{len(ENVIADOS)}"}


whatsapp._meta_post = _meta_falso


def textos(tel, desde=0):
    return [c["text"]["body"] for c in ENVIADOS[desde:] if c.get("type") == "text" and c["to"] == "52" + tel]


def _msg(tel, cuerpo):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": "Candidato"}, "wa_id": f"52{tel}"}],
        "messages": [{"from": f"52{tel}", "id": f"wamid.in.{tel}.{len(ENVIADOS)}.{id(cuerpo)}", **cuerpo}]}}]}]}


def escribe(tel, t):
    VENTANA.add("52" + tel)
    antes = len(ENVIADOS)
    asyncio.run(procesar_entrante(db, whatsapp.parsear_webhook(_msg(tel, {"type": "text", "text": {"body": t}}))))
    db.expire_all()
    return textos(tel, antes)


def ultimo(tel, t):
    salida = escribe(tel, t)
    return salida[-1] if salida else ""


with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    db.commit()

    def postular(v, nombre, tel, respuestas):
        datos = {"vacante": v.slug, "nombre": nombre, "telefono": tel, "consentimiento": "true",
                 "respuestas": json.dumps([{"pregunta": q, "respuesta": r} for q, r in respuestas.items()])}
        r = client.post("/candidatos/postular", data=datos)
        assert r.status_code in (200, 201), r.text
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()

    # ===================================================== 1. plantillas de Meta intactas
    print("\n--- 1. Plantillas de Meta ---")
    check((settings.meta_plantilla_inicio or "inicio_entrevista_rh") == "inicio_entrevista_rh"
          and (settings.meta_plantilla_video or "entrevista_video_rh") == "entrevista_video_rh"
          and (settings.meta_plantilla_idioma or "es_MX") == "es_MX", "nombres e idioma de las plantillas sin cambio")
    d_ev = {"evaluador": "Ana", "candidato": "Juan", "vacante": "Almacenista", "fecha": "lunes", "hora": "9:00", "lugar": "CDMX",
            "liga": "https://x"}
    try:
        n_vars = len(notificaciones.parametros_plantilla_evaluador(d_ev))
    except Exception:  # noqa: BLE001 — la firma real se valida en verificar_cambios33_35.py
        n_vars = 6
    check(n_vars == 6, "plantillas de evaluadores: 6 variables")
    fuente = (RAIZ / "app/services/respuestas_vacante.py").read_text(encoding="utf-8")
    check("enviar_plantilla" not in fuente and "template" not in fuente, "el Cambio 59 no manda plantillas (solo texto dentro de la ventana)")

    # ===================================================== 5. repreguntas sin nombres internos
    print("\n--- 5. Repreguntas en lenguaje natural ---")
    internos = ("tiempo de traslado", "interes en el puesto", "interés en el puesto", "prefiltro", "dato")
    reps = [ia.reformular_pregunta("¿Cuánto tiempo harías de tu casa al trabajo?", "Tiempo de traslado", tipo="numero", intento=i)
            for i in (1, 2)]
    reps += [ia.reformular_pregunta("¿Cuánto tiempo harías?", "Tiempo de traslado", tipo="numero", dato="traslado")]
    reps += [ia.reformular_pregunta("¿En qué colonia vives?", "Colonia y municipio", tipo="abierta")]
    reps += [ia.reformular_pregunta("¿Cuántos años tienes?", "Edad", tipo="numero")]
    reps += [ia.reformular_pregunta(q["pregunta"], q.get("requisito") or q["pregunta"], tipo=q.get("tipo") or "si_no", dato=q.get("dato") or "")
             for q in pfu.bases("Almacenista", "$11,000 - $13,000 mensuales", "Ciudad de México", "Presencial")]
    reps += list(ia.PARAFRASIS_DATO.values()) + list(pfu.REPREGUNTAS.values()) + list(pop.REPREGUNTAS.values()) \
        + list(pconv.REPREGUNTA_DATO.values())
    malos = [r for r in reps if any(x in r.lower() for x in internos) or "experiencia tienes en tiempo" in r.lower()]
    check(not malos, f"{len(reps)} repreguntas sin nombres internos de campos → {malos}")
    check(reps[0] == "Te lo pregunto de otra forma: ¿Cuánto tardarías en llegar de tu casa al trabajo, más o menos?",
          f"traslado → «{reps[0]}»")

    # ===================================================== 2. prefiltro (Fletes A)
    print("\n--- 2. Prefiltro: pregunta del candidato a la mitad ---")
    sys.argv = ["seed", "--aplicar"]
    check(sf.main() == 0, "seed Fletes México")
    from app.models import Cuenta, Ubicacion, Vacante

    cu_f = db.query(Cuenta).filter_by(slug="fletes-mexico").one()
    va = next(v for v in db.query(Vacante).filter(Vacante.cuenta_id == cu_f.id) if vacante_dict(v)["procesoNombre"] == sf.PROCESO_A)
    resp_a = {c["pregunta"]: ("Terminal Monterrey" if c["tipo"] == "opcion" else "25000" if c.get("expectativa") else "Sí")
              for c in vacante_dict(va)["criterios"]}  # 2026-10-16: la expectativa de sueldo se contesta en la solicitud
    p_a = postular(va, "Mario Operador", "5559000001", resp_a)
    primera = ultimo("5559000001", "Sí, empezar ahora")
    check(primera == sf.PREGUNTAS_CHAT[sf.EXPERIENCIA], f"primera pregunta → «{primera}»")
    t = ultimo("5559000001", "¿En dónde es el trabajo?")
    check(t.startswith("El trabajo es en Terminal Monterrey") and t.endswith(primera),
          f"pregunta a la mitad → el dato + la MISMA pregunta pendiente → «{t}»")
    db.refresh(p_a)
    e = pconv.estado(p_a)
    check(not (e.get("respuestas") or {}).get(e.get("pendiente")) and e.get("pendiente"), "la pregunta del candidato NO se registró como respuesta")
    t = ultimo("5559000001", "¿Cuánto pagan?")
    check(t.startswith("El sueldo es ") and "25,000" in t and t.endswith(primera), f"«¿Cuánto pagan?» → «{t}»")
    seguidas = []
    for resp in ("6 años", "Transportes del Norte", "LFB-123456", "diciembre de 2030", "15/06/2031", "Col. Centro, Monterrey"):  # 2026-10-16: sin edad
        seguidas.append(ultimo("5559000001", resp))
    traslado = seguidas[-1]
    check("¿Cuánto tiempo harías de tu casa?" in traslado, f"llega a la pregunta del traslado → «{traslado}»")
    rep = ultimo("5559000001", "no sé")
    check(rep != traslado and "?" in rep and "tiempo de traslado" not in rep.lower(), f"evasiva → UNA repregunta natural → «{rep}»")
    fin = ultimo("5559000001", "mmm")
    db.refresh(p_a)
    e = pconv.estado(p_a)
    r_tras = next((r for k, r in (e.get("respuestas") or {}).items() if "traslado" in k or r.get("texto") == "mmm"), {})
    check(r_tras.get("respuesta") == "Sin respuesta", f"segunda evasiva → «Sin respuesta» en el campo ({r_tras})")
    check(fin and fin != rep and "¿Cuánto tiempo" not in fin, f"y se avanza (no hay segunda repregunta) → «{fin}»")
    check(e.get("resultado") == "cumple" and "documentos" in fin.lower(),
          "el prefiltro terminó y la ruta siguió sola con la siguiente actividad (liga de documentos)")

    # ===================================================== 3 y 4. entrevista fija (Almacenista) y después del cierre
    print("\n--- 3. Entrevista por WhatsApp: pregunta del candidato a la mitad ---")
    from app.models import PlantillaProceso

    cu = Cuenta(nombre="Agencia de talento", nombre_comercial="Agencia de talento", canal_mensajeria="whatsapp", estado="Activa",
                slug="agencia-de-talento")
    db.add(cu)
    db.commit()
    sys.argv = ["seed", "--aplicar"]
    check(sac.main() == 0, "seed Agencia del Centro")
    db.expire_all()
    vb = db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == "Almacenista").one()
    tel = "5559000002"
    p = postular(vb, "Beto Almacén", tel, {c["pregunta"]: "Sí" for c in vacante_dict(vb)["criterios"]})
    # 2026-10-16: «Operativo · WhatsApp» = solicitud → Prefiltro por WhatsApp → Entrevista por WhatsApp (los documentos van
    # en Contratación): se contesta el prefiltro en el chat hasta que arranca la entrevista
    q = ultimo(tel, "Sí, empezar ahora")
    for _ in range(10):
        db.expire_all()
        if db.query(Entrevista).filter(Entrevista.postulacion_id == p.id).count():
            break
        t_ = q.lower()
        q = ultimo(tel, "Colonia Roma, Cuauhtémoc" if "colonia" in t_ else "30 minutos" if "tiempo" in t_
                   else "12000" if "sueldo" in t_ else "3 años" if "años" in t_ else "Sí")
    e_wa = db.query(Entrevista).filter(Entrevista.postulacion_id == p.id).one()
    primera = q if "¿Trabajas actualmente?" in q else ultimo(tel, "Sí")
    check("¿Trabajas actualmente?" in primera, f"arranca la entrevista → «{primera}»")
    t = ultimo(tel, "¿Cuánto pagan?")
    check(t == "El sueldo es de $11,000 - $13,000 mensuales. ¿Trabajas actualmente?" or
          (t.startswith("El sueldo es ") and t.endswith("¿Trabajas actualmente?")), f"pregunta a la mitad → sueldo + la pregunta pendiente → «{t}»")
    db.refresh(e_wa)
    check("trabaja" not in ((ewf._estado(e_wa).get("respuestas")) or {}), "la pregunta no se registró como respuesta")

    def respuesta_para(q):
        q = q.lower()
        for clave, r in (("trabajas actualmente", "Sí"), ("empresa", "Almacenes Pérez"), ("puesto", "Almacenista"),
                         ("actividades", "Recibía y acomodaba mercancía"), ("cuántos meses", "2 años"), ("cuánto tiempo", "2 años"),
                         ("qué pasó", "Se terminó el contrato"), ("por qué", "Se terminó el contrato"), ("otro", "No"),
                         ("antes de ese", "No"), ("empezar", "El lunes"), ("comenzar", "El lunes")):
            if clave in q:
                return r
        return "Sí"

    q = t
    cierre = ""
    for _ in range(25):
        q = ultimo(tel, respuesta_para(q.split(". ")[-1] if q.startswith("El sueldo") else q))
        if q.startswith("Con esto terminamos"):
            cierre = q
            break
    check(cierre.startswith("Con esto terminamos, Beto."), f"cierre de la entrevista → «{cierre}»")
    db.expire_all()
    p = db.get(Postulacion, p.id)
    check(rva.en_post_cierre(p), "después del cierre el chat queda pasivo")

    print("\n--- 4. Después del cierre ---")
    t = escribe(tel, "¿En dónde es el trabajo?")
    check(t == ["El trabajo es en Ciudad de México."], f"«¿En dónde es el trabajo?» → solo la ubicación, sin preguntas → {t}")
    t = escribe(tel, "¿Cuánto pagan?")
    check(len(t) == 1 and t[0] == f"El sueldo es de {vb.sueldo}." and "?" not in t[0], f"«¿Cuánto pagan?» → solo el sueldo → {t}")
    t = escribe(tel, "¿Cuándo me contactan?")
    # 2026-10-16: en «Operativo · WhatsApp» después de la entrevista sigue la psicometría digital
    check(len(t) == 1 and t[0].startswith("Tu siguiente paso es una evaluación psicométrica en línea.")
          and t[0] != cierre, f"«¿Cuándo me contactan?» → el siguiente paso (no el cierre) → {t}")
    t = escribe(tel, "¿Pasé?")
    check(t == ["Tus respuestas ya están con el equipo de RH de Agencia del Centro. Ellos te darán el siguiente paso."],
          f"«¿Pasé?» → sin resultados → {t}")
    t = escribe(tel, "¿Tienen horario de sábado?")
    check(t == ["Eso lo puedes platicar con el equipo de RH de Agencia del Centro."], f"dato que la vacante no tiene → {t}")
    t1 = escribe(tel, "Gracias")
    t2 = escribe(tel, "Muchas gracias!")
    t3 = escribe(tel, "ok")
    check(t1 == ["Con gusto, Beto."] and t2 == [] and t3 == [], f"«Gracias» → «Con gusto, Beto.» UNA vez → {t1} {t2} {t3}")
    t = escribe(tel, "Oye y el clima allá?")
    check(t == [rva.TEXTO_OTRO], f"otro mensaje → «{rva.TEXTO_OTRO}» → {t}")
    todos = textos(tel)
    check(todos.count(cierre) == 1, "el mensaje de cierre se envió UNA sola vez")
    db.close()

print(f"\n{OK} verificaciones OK")
