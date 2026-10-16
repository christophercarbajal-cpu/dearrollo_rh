"""Demo «Agencia del Centro» (2026-10-15): `seed_agencia_centro.py` y las reglas de negocio de sus 4 procesos. Base
desechable; IA, Meta y psicometría SIMULADAS (sin red).

    PYTHONIOENCODING=utf-8 python scripts/verificar_agencia_centro.py

1. Cuenta «Agencia de talento» → «Agencia del Centro» (slug intacto); el bot dice «Soy Red Human, el agente virtual de
   Agencia del Centro.»; los procesos anteriores quedan DESACTIVADOS (no borrados); 4 procesos nuevos; 5 vacantes.
   Simulacro sin escribir; idempotente.
2. Ayudante general (P1), 2026-10-16: la solicitud web NUNCA descarta — un «No» excluyente se registra, sale la
   plantilla `inicio_entrevista_rh` y el Prefiltro por WhatsApp lo CONFIRMA («Me comentaste que no puedes…, ¿es
   correcto?»): «Sí» → descartado ahí con su motivo; «No, sí puedo» → sigue. Todo «Sí» → el chat hace al menos 3
   preguntas; sin terminar el prefiltro no avanza (compuerta → 409); al cumplir, documentos por liga y luego Filtro
   humano con SOLO la oferta de días para la prueba práctica.
3. Almacenista (P2): la Entrevista Red Human por WhatsApp arranca hasta cumplir el prefiltro por WhatsApp; documentos
   en Contratación (nada en la página de la solicitud); psicometría Informativa después de la entrevista.
4. Chofer con auto propio (P3): licencia y fotos del auto en Prefiltro (después del WhatsApp); lo demás en Contratación;
   avatar «operativo»; psicometría Informativa.
5. Gerentes (P4): la solicitud exige CV; prefiltro por WhatsApp; documentos en Contratación; avatar «Profesional +
   personal» con guion propio por vacante; psicometría con mínimos por escala.
"""

import asyncio
import io
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
_dir = _temporal.directorio("rh_agc_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "agc.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-agc"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Entrevista, PlantillaProceso, Postulacion, Vacante  # noqa: E402
from app.routers.webhooks import procesar_entrante  # noqa: E402
from app.serial import nombre_empresa_candidato, vacante_dict  # noqa: E402
from app.services import entrevista_whatsapp_fija as ewf  # noqa: E402
from app.services import ia  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

import seed_agencia_centro as sac  # noqa: E402

OK = 0
ENVIADOS = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _meta_falso(cuerpo):
    ENVIADOS.append(cuerpo)
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "mensaje_id": f"wamid.{len(ENVIADOS)}"}


whatsapp._meta_post = _meta_falso


def enviados_a(tel, desde=0):
    salida = []
    for c in ENVIADOS[desde:]:
        if c.get("to") != "52" + tel:
            continue
        if c.get("type") == "text":
            salida.append(c["text"]["body"])
        elif c.get("type") == "template":
            salida.append(f"[plantilla {c['template']['name']}]")
    return salida


def correr_seed(*args):
    sys.argv = ["seed", *args]
    return sac.main()


with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    db.commit()

    # ===================================================== 1. Cuenta, procesos y vacantes
    print("\n--- 1. Cuenta, procesos y vacantes ---")
    cu = Cuenta(nombre="Agencia de talento", nombre_comercial="Agencia de talento", razon_social="Agencia de talento",
                canal_mensajeria="whatsapp", estado="Activa", slug="agencia-de-talento")
    db.add(cu)
    db.flush()
    sproc.asegurar_rutas_base(db, cu.id, "prueba")
    db.add(PlantillaProceso(cuenta_id=cu.id, nombre="Operativos", descripcion="", pasos=sproc.ruta_base("masivos_sin_documentos")["pasos"],
                            etapas=sproc.normalizar_etapas({}), version=1, predeterminada=False, activa=True))
    db.commit()
    cuenta_id = cu.id

    check(correr_seed() == 0, "simulacro sin errores")
    db.expire_all()
    check(db.get(Cuenta, cuenta_id).nombre_comercial == "Agencia de talento"
          and db.query(Vacante).filter(Vacante.cuenta_id == cuenta_id).count() == 0, "el simulacro no escribe nada")
    check(correr_seed("--aplicar") == 0, "seed_agencia_centro.py --aplicar")
    db.expire_all()
    cu = db.get(Cuenta, cuenta_id)
    check(cu.nombre == cu.nombre_comercial == "Agencia del Centro" and cu.slug == "agencia-de-talento",
          f"Cuenta renombrada a «{cu.nombre_comercial}» (slug {cu.slug} intacto)")
    planes = db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cuenta_id).all()
    activos = sorted(p.nombre for p in planes if p.activa)
    viejos = [p for p in planes if p.nombre in ("Operativos", "Masivos sin documentos iniciales", "Masivos con documentos iniciales")]
    check(len(viejos) == 3 and not any(p.activa for p in viejos), "«Operativos» y los dos «Masivos» quedan desactivados (no borrados)")
    check(set(activos) >= {sac.P1, sac.P2, sac.P3, sac.P4}, f"los 4 procesos nuevos activos → {activos}")
    pred = [p.nombre for p in planes if p.activa and p.predeterminada]
    check(pred == [sac.P2], f"una sola predeterminada: «{sac.P2}» → {pred}")
    vs = {v.titulo: v for v in db.query(Vacante).filter(Vacante.cuenta_id == cuenta_id, Vacante.estado != "Eliminada")}
    check(sorted(vs) == sorted(d["titulo"] for d in sac.VACANTES) and all(v.estado == "Publicada" for v in vs.values()),
          f"5 vacantes publicadas → {sorted(vs)}")
    check(all(v.ubicacion == "Ciudad de México" and v.sueldo_periodicidad == "mensual" for v in vs.values()),
          "todas en Ciudad de México, sueldo mensual")
    check(all(ia.presentacion_bot(nombre_empresa_candidato(v)) == sac.PRESENTACION for v in vs.values()),
          f"presentación: «{sac.PRESENTACION}»")
    check(correr_seed("--aplicar") == 0, "segunda corrida")
    db.expire_all()
    check(db.query(Vacante).filter(Vacante.cuenta_id == cuenta_id).count() == 5
          and db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cuenta_id, PlantillaProceso.nombre == sac.P4).count() == 1,
          "idempotente: no duplica vacantes ni procesos")

    def postular(v, nombre, tel, respuestas=None, cv=None, correo=""):
        respuestas = respuestas or {c["pregunta"]: "Sí" for c in vacante_dict(v)["criterios"]}
        datos = {"vacante": v.slug, "nombre": nombre, "telefono": tel, "correo": correo, "consentimiento": "true",
                 "respuestas": json.dumps([{"pregunta": q, "respuesta": r} for q, r in respuestas.items()])}
        archivos = {"cv": ("cv.pdf", io.BytesIO(b"%PDF-1.4 CV de prueba"), "application/pdf")} if cv else None
        r = client.post("/candidatos/postular", data=datos, files=archivos)
        db.expire_all()
        return r

    def aprobar_documentos(p):
        e = p.expediente
        for d in e.documentos:
            d.archivo = d.archivo or "demo.pdf"
            d.estado, d.revisado_por = "recibido", "RH de prueba"
        db.commit()
        asyncio.run(sproc.avanzar_seguro(db, p))
        db.commit()
        db.expire_all()
        return db.get(Postulacion, p.id)

    from app.deps import cuenta_actual, usuario_actual, usuario_decisor
    from app.models import Usuario

    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").order_by(Usuario.id).first()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: db.get(Cuenta, cuenta_id)
    N_WA = [0]

    def escribe(tel, texto):
        """Mensaje entrante del candidato por WhatsApp; regresa lo que el bot le contestó."""
        N_WA[0] += 1
        desde_ = len(ENVIADOS)
        msg = whatsapp.parsear_webhook({"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
            "contacts": [{"profile": {"name": "Candidato"}, "wa_id": f"52{tel}"}],
            "messages": [{"from": f"52{tel}", "id": f"wamid.in.agc.{N_WA[0]}", "type": "text", "text": {"body": texto}}]}}]}]})
        asyncio.run(procesar_entrante(db, msg))
        db.expire_all()
        return enviados_a(tel, desde_)

    def contestar(tel, p, maximo=12):
        """Contesta el prefiltro por WhatsApp (todo cumple) hasta que se resuelve. Regresa las preguntas que hizo el bot."""
        preguntas_bot = []
        salida = escribe(tel, "Sí, empezar ahora")
        for _ in range(maximo):
            db.expire_all()
            p = db.get(Postulacion, p.id)
            if ((p.analisis or {}).get("prefiltro_conversacional") or {}).get("resultado") or not salida:
                break
            q = salida[-1]
            preguntas_bot.append(q)
            t = q.lower()
            r_ = ("Sí" if "correcto" in t else "Colonia Roma, Cuauhtémoc" if "colonia" in t else "30 minutos" if "tiempo" in t
                  else "10000" if "sueldo" in t else "3 años" if "años" in t else "Sí")
            salida = escribe(tel, r_)
        return preguntas_bot

    def resultado_prefiltro(p):
        return ((p.analisis or {}).get("prefiltro_conversacional") or {}).get("resultado")

    # ===================================================== 2. Ayudante general
    print("\n--- 2. Ayudante general (P1) ---")
    va = vs["Ayudante general"]
    preguntas = [c["pregunta"] for c in vacante_dict(va)["criterios"]]
    check(len([c for c in vacante_dict(va)["criterios"] if c.get("descarta")]) >= 3, "la solicitud trae 3 preguntas de la vacante")
    desde = len(ENVIADOS)
    r = postular(va, "Raúl Rechazo", "5561000001", {**{q: "Sí" for q in preguntas}, "¿Puedes cargar material pesado?": "No"})
    p = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    check(r.status_code in (200, 201) and not r.json().get("descartado") and p.activa,
          "un «No» excluyente en la web NO descarta: la solicitud termina y la postulación sigue activa")
    check(any(h.get("evento") == "respuesta_excluyente_web" for h in p.historial or []), "la respuesta excluyente queda registrada")
    check("[plantilla inicio_entrevista_rh]" in enviados_a("5561000001", desde),
          f"sale la plantilla inicio_entrevista_rh → {enviados_a('5561000001', desde)}")
    salida = escribe("5561000001", "Sí, empezar ahora")
    check(salida and "Me comentaste que no puedes cargar material pesado, ¿es correcto?" in salida[-1],
          f"el WhatsApp CONFIRMA la respuesta excluyente de la web → {salida}")
    salida = escribe("5561000001", "Sí, es correcto")
    p = db.get(Postulacion, p.id)
    desc = next((h for h in p.historial or [] if h.get("evento") == "descartado"), {})
    check(not p.activa and p.motivo_cierre == "descartado" and "cargar material pesado" in desc.get("motivo", "")
          and "confirmó" in desc.get("motivo", ""), f"confirmado → descartado ahí con su motivo («{desc.get('motivo')}»)")
    check(salida and "no continuaremos" in salida[-1] and "cargar" not in salida[-1], "el candidato recibe el mensaje genérico")

    r = postular(va, "Mario Corrige", "5561000009", {**{q: "Sí" for q in preguntas}, "¿Puedes cargar material pesado?": "No"})
    p = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    escribe("5561000009", "Sí, empezar ahora")
    salida = escribe("5561000009", "No, sí puedo")
    p = db.get(Postulacion, p.id)
    check(p.activa and salida and "cargar material" not in salida[-1] and not resultado_prefiltro(p),
          f"corrige («No, sí puedo») → el prefiltro sigue con la siguiente pregunta → {salida}")

    desde = len(ENVIADOS)
    r = postular(va, "Ana Ayudante", "5561000002")
    check(r.status_code in (200, 201) and not r.json().get("descartado"), "todo «Sí» → la solicitud pasa")
    check(not r.json().get("documentos"), "los documentos ya NO se piden en la página de la solicitud (van después del WhatsApp)")
    p = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    check("[plantilla inicio_entrevista_rh]" in enviados_a("5561000002", desde), "con Prefiltro por WhatsApp sale la plantilla de inicio")
    # Tarea 4: sin terminar el prefiltro por WhatsApp nada avanza
    asyncio.run(sproc.avanzar_seguro(db, p))
    db.expire_all()
    p = db.get(Postulacion, p.id)
    falt = [x["tipo"] for x in sproc.faltantes(sproc.estado_pasos(p), p.etapa, "Entrevista Humana")]
    check(p.etapa == "Prefiltro" and "prefiltro_whatsapp" in falt, f"sin terminar el prefiltro sigue en Prefiltro (faltan {falt})")
    r = client.patch(f"/candidatos/{p.codigo}/etapa", json={"etapa": "Entrevista Humana", "manual": True})
    check(r.status_code == 409, f"mover a Filtro humano sin terminar el prefiltro → 409 ({r.status_code})")
    hechas = contestar("5561000002", p)
    p = db.get(Postulacion, p.id)
    check(resultado_prefiltro(p) == "cumple" and len(hechas) >= 3, f"el chat hace al menos 3 preguntas y cumple → {hechas}")
    check(p.etapa == "Prefiltro" and p.expediente is not None, "al cumplir, se piden los documentos (sigue en Prefiltro)")
    desde = len(ENVIADOS)
    p = aprobar_documentos(p)
    check(p.etapa == "Entrevista Humana", f"documentos aprobados → Filtro humano ({p.etapa})")
    oferta = next((t for t in enviados_a("5561000002", desde) if "¿Qué día puedes venir?" in t), "")
    check("evaluación técnica o práctica" in oferta.lower() and "Ciudad de México" in oferta,
          f"autoagenda de la prueba práctica en Ciudad de México → «{oferta}» {p.analisis.get('autoagenda') if not oferta else ''}")
    check(not db.query(Entrevista).filter(Entrevista.postulacion_id == p.id).count(), "sin entrevistas previas")
    medica = next(x for x in sproc.estado_pasos(p) if x["tipo"] == "medica")
    check(not medica["disponible"] or medica["estado"] == "pendiente", "la médica espera a la prueba práctica")

    # ===================================================== 3. Almacenista
    print("\n--- 3. Almacenista (P2) ---")
    vb = vs["Almacenista"]
    desde = len(ENVIADOS)
    r = postular(vb, "Beto Almacén", "5561000003")
    check(r.status_code in (200, 201) and not r.json().get("documentos"), "sin documentos en la página de la solicitud")
    p = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    check("[plantilla inicio_entrevista_rh]" in enviados_a("5561000003", desde), "sale la plantilla inicio_entrevista_rh")
    check(p.etapa == "Prefiltro" and not db.query(Entrevista).filter(Entrevista.postulacion_id == p.id).count(),
          "antes del prefiltro por WhatsApp no hay entrevista")
    ok_e, motivo_e = sproc.agente_puede_actuar(p, "entrevista_whatsapp")
    check(not ok_e, f"el agente no puede iniciar la entrevista sin terminar el prefiltro ({motivo_e})")
    hechas = contestar("5561000003", p)
    p = db.get(Postulacion, p.id)
    e = db.query(Entrevista).filter(Entrevista.postulacion_id == p.id).first()
    check(resultado_prefiltro(p) == "cumple" and p.etapa == "Entrevista IA" and e is not None and e.tipo == "whatsapp",
          f"prefiltro cumplido → arranca la Entrevista Red Human por WhatsApp ({p.etapa}, {e.tipo if e else None}, "
          f"{resultado_prefiltro(p)}, {hechas})")
    check(ewf.aplica(e), "con la base operativa fija")
    psi = next(x for x in p.proceso["pasos"] if x["tipo"] == "psicometrica")
    check(psi["evaluacion_psicometria"] == {"modalidad": "informativa"} and psi["depende_de"] == ["entrevista-whatsapp"],
          "psicometría Informativa, después de la entrevista por WhatsApp")
    from app.services import documentos_vacante as sdv

    check(not sdv.de_vacante(vb, vb.proceso["pasos"], tempranos=True) and len(sdv.de_vacante(vb, vb.proceso["pasos"], tempranos=False)) == 4,
          "documentos en Contratación")

    # ===================================================== 4. Chofer
    print("\n--- 4. Chofer con auto propio (P3) ---")
    vc = vs["Chofer con auto propio"]
    r = postular(vc, "Carlos Chofer", "5561000004", correo="carlos@example.com")
    p = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    check(sproc.enfoque_entrevista_agente(p, vc) == "operativo", "la entrevista con avatar carga el enfoque «operativo»")
    tempranos = [d["nombre"] for d in sdv.de_vacante(vc, vc.proceso["pasos"], tempranos=True)]
    finales = [d["nombre"] for d in sdv.de_vacante(vc, vc.proceso["pasos"], tempranos=False)]
    check(tempranos[:1] == ["Licencia de conducir"] and len(tempranos) == 4 and len(finales) == 3,
          f"licencia + 3 fotos del auto en Prefiltro; lo demás en Contratación → {tempranos} / {finales}")
    pre = [sproc.clave_catalogo(x) for x in vc.proceso["pasos"] if x["etapa"] == "Prefiltro"]
    check(pre == ["solicitud_sin_cv", "prefiltro_whatsapp", "documentos"], f"Prefiltro del chofer: solicitud → WhatsApp → documentos ({pre})")
    plantilla_p3 = db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cuenta_id, PlantillaProceso.nombre == sac.P3).one()
    check(not any(x["tipo"] == "documentos" and x["etapa"] == "Prefiltro" for x in plantilla_p3.pasos),
          "la plantilla «Operativo · Avatar» no cambia (solo la copia de la vacante)")
    psi = next(x for x in p.proceso["pasos"] if x["tipo"] == "psicometrica")
    check(psi["evaluacion_psicometria"] == {"modalidad": "informativa"} and psi["depende_de"] == ["entrevista-avatar"],
          "psicometría Informativa, después del avatar")

    # ===================================================== 5. Gerentes
    print("\n--- 5. Gerentes (P4) ---")
    vg, vv = vs["Gerente de Recursos Humanos"], vs["Gerente de Ventas"]
    r = postular(vg, "Gina Gerente", "5561000005", correo="gina@example.com")
    check(r.status_code == 400, f"sin CV → 400 ({r.status_code})")
    r = postular(vg, "Gina Gerente", "5561000005", cv=True, correo="gina@example.com")
    check(r.status_code in (200, 201) and not r.json().get("documentos"), "con CV pasa y NO pide documentos en la solicitud")
    p = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    check(sproc.enfoque_entrevista_agente(p, vg) == "profesional_personal", "avatar «Profesional + personal»")
    check([sproc.clave_catalogo(x) for x in vg.proceso["pasos"] if x["etapa"] == "Prefiltro"] == ["solicitud_con_cv", "prefiltro_whatsapp"],
          "Prefiltro: solicitud con CV → Prefiltro por WhatsApp")
    for v in (vg, vv):
        finales = sdv.de_vacante(v, v.proceso["pasos"], tempranos=False)
        check(not sdv.de_vacante(v, v.proceso["pasos"], tempranos=True) and len(finales) == 6
              and sdv.etapa_doc(finales[0], v.proceso["pasos"]) == "Contratación",
              f"{v.titulo}: los 6 documentos se piden en Contratación")
    g_rh = ((vg.guiones or {}).get("secciones") or {}).get("entrevista_avatar") or {}
    g_ve = ((vv.guiones or {}).get("secciones") or {}).get("entrevista_avatar") or {}
    g_ch = ((vc.guiones or {}).get("secciones") or {}).get("entrevista_avatar") or {}
    check(g_rh.get("preguntas") and g_rh.get("preguntas") != g_ve.get("preguntas"), "cada gerente tiene su propio guion")
    check(not g_rh.get("base_fija") and g_ch.get("base_fija"), "gerentes = guion profesional; chofer = base operativa fija")
    psi = next(x for x in vg.proceso["pasos"] if x["tipo"] == "psicometrica")
    check(psi["evaluacion_psicometria"] == {"modalidad": "minimos", "minimos": sac.MINIMOS_ADMINISTRATIVO} and len(psi["pruebas"]) == 3,
          "psicometría con mínimos por escala (Cleaver + Terman + Moss)")
    contr = [sproc.clave_catalogo(x) for x in vg.proceso["pasos"] if x["etapa"] == "Contratación"]
    check(contr == ["oferta", "documentos", "carta_contrato"], f"Contratación: Oferta laboral → Documentos → Contrato y firma ({contr})")
    db.close()

print(f"\n{OK} verificaciones OK")
