"""Demo «Agencia del Centro» (2026-10-15): `seed_agencia_centro.py` y las reglas de negocio de sus 4 procesos. Base
desechable; IA, Meta y psicometría SIMULADAS (sin red).

    PYTHONIOENCODING=utf-8 python scripts/verificar_agencia_centro.py

1. Cuenta «Agencia de talento» → «Agencia del Centro» (slug intacto); el bot dice «Soy Red Human, el agente virtual de
   Agencia del Centro.»; los procesos anteriores quedan DESACTIVADOS (no borrados); 4 procesos nuevos; 5 vacantes.
   Simulacro sin escribir; idempotente.
2. Ayudante general (P1): documentos en la MISMA página de la solicitud; un «No» descarta al enviar; aprobados los
   documentos, el candidato pasa a Filtro humano y recibe SOLO la oferta de días para la prueba práctica (sin entrevistas).
3. Almacenista (P2): aprobados los documentos, arranca sola la Entrevista Red Human por WhatsApp (base operativa).
4. Chofer con auto propio (P3): la Entrevista con avatar usa el enfoque «operativo»; psicometría Informativa.
5. Gerentes (P4): la solicitud exige CV; sin documentos al inicio (se piden en Contratación); avatar «Profesional +
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

    # ===================================================== 2. Ayudante general
    print("\n--- 2. Ayudante general (P1) ---")
    va = vs["Ayudante general"]
    preguntas = [c["pregunta"] for c in vacante_dict(va)["criterios"]]
    r = postular(va, "Raúl Rechazo", "5561000001", {**{q: "Sí" for q in preguntas}, "¿Puedes cargar material pesado?": "No"})
    check(r.status_code in (200, 201) and r.json().get("descartado"), "un «No» a «¿Puedes cargar material pesado?» descarta al enviar")
    desde = len(ENVIADOS)
    r = postular(va, "Ana Ayudante", "5561000002")
    check(r.status_code in (200, 201) and not r.json().get("descartado"), "todo «Sí» → la solicitud pasa")
    docs = r.json().get("documentos") or {}
    check(bool(docs.get("token")), f"la respuesta trae la carga de documentos en la MISMA página → {list(docs)}")
    p = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    check(not any("plantilla inicio_entrevista_rh" in t for t in enviados_a("5561000002", desde)),
          "sin prefiltro por WhatsApp: no sale la plantilla de inicio")
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
    r = postular(vb, "Beto Almacén", "5561000003")
    check(r.status_code in (200, 201) and (r.json().get("documentos") or {}).get("token"), "documentos en la página de la solicitud")
    p = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    check(p.etapa == "Prefiltro" and not db.query(Entrevista).filter(Entrevista.postulacion_id == p.id).count(),
          "antes de los documentos no hay entrevista")
    desde = len(ENVIADOS)
    p = aprobar_documentos(p)
    e = db.query(Entrevista).filter(Entrevista.postulacion_id == p.id).first()
    check(p.etapa == "Entrevista IA" and e is not None and e.tipo == "whatsapp",
          f"documentos aprobados → arranca la Entrevista Red Human por WhatsApp ({p.etapa}, {e.tipo if e else None})")
    check(ewf.aplica(e), "con la base operativa fija")
    check(enviados_a("5561000003", desde), f"el candidato recibe el primer mensaje → {enviados_a('5561000003', desde)}")

    # ===================================================== 4. Chofer
    print("\n--- 4. Chofer con auto propio (P3) ---")
    vc = vs["Chofer con auto propio"]
    r = postular(vc, "Carlos Chofer", "5561000004", correo="carlos@example.com")
    p = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    check(sproc.enfoque_entrevista_agente(p, vc) == "operativo", "la entrevista con avatar carga el enfoque «operativo»")
    nombres = [d["nombre"] for d in vc.documentos_solicitados]
    check(nombres[0] == "Licencia de conducir" and len(nombres) == 4 and all(d["foto"] for d in vc.documentos_solicitados[1:]),
          f"licencia (frente y reverso) + 3 fotos del auto → {nombres}")
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
    from app.services import documentos_vacante as sdv

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
    check(contr == ["documentos", "oferta", "carta_contrato"], f"Contratación: Documentos → Oferta laboral → Contrato y firma ({contr})")
    db.close()

print(f"\n{OK} verificaciones OK")
