"""Cambios 45 y 46 (2026-10-14). Base desechable; Meta, IA y correo SIMULADOS (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambios45_46.py

Cambio 45: «Textos por plataforma» en pestañas horizontales (deslizables), WhatsApp activa al cargar, solo el texto de la
pestaña elegida, con «Editar», «Copiar» y la liga `?canal=`.
Cambio 46: aviso a evaluadores en cascada (texto libre → plantilla con los nombres EXACTOS de Meta, es_MX); plantilla
inexistente o en revisión (132001) = «Pendiente de plantilla» (nunca «Fallido») y se reintenta sola; «Fallido» solo
para errores definitivos, con su motivo.
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_c45_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c45.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
for k in ("META_PLANTILLA_EVALUADOR", "META_PLANTILLA_INSTRUCTOR", "META_PLANTILLA_MEDICO", "META_PLANTILLA_IDIOMA"):
    os.environ.pop(k, None)
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-c45"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_admin, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ESTADOS_ENVIO, Cuenta, EnvioActividad, Evaluacion, NotificacionEnviada, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.services import notificaciones, reintento_plantillas  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
ENVIADOS = []
APP = RAIZ.parent / "red-human-app"


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


# Meta simulada: `texto` = el evaluador está (o no) dentro de la ventana de 24 h; `plantilla` = ok | pendiente | invalido
MODO = {"texto": False, "plantilla": "ok"}


async def _meta_falso(cuerpo):
    ENVIADOS.append(cuerpo)
    if cuerpo.get("type") == "text" and cuerpo.get("to") != "525511450001" and not MODO["texto"]:
        return {"enviado": False, "proveedor": "meta", "detalle": "131047: Re-engagement message", "codigo": 131047}
    if cuerpo.get("type") == "template":
        if MODO["plantilla"] == "pendiente":
            return {"enviado": False, "proveedor": "meta", "detalle": "132001: Template name does not exist in the translation",
                    "codigo": 132001}
        if MODO["plantilla"] == "invalido":
            return {"enviado": False, "proveedor": "meta", "detalle": "131026: Message undeliverable", "codigo": 131026}
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "wa_id": f"wamid.{len(ENVIADOS)}"}


whatsapp._meta_post = _meta_falso
PARAMS = ["Mariana", "Luisa Pérez", "Almacenista", "jueves 15 de octubre, 10:30", "Av. Patria 100", "https://app/evaluacion/x"]


PARAMS_MEDICO = ["Mariana", "Luisa Pérez", "Almacenista", "jueves 15 de octubre", "10:30 (hora de Ciudad de México)", "Av. Patria 100",
                 "https://app/evaluacion/x"]


def enviar(plantilla="aviso_evaluador_rh"):
    params = PARAMS_MEDICO if plantilla == "aviso_medico" else PARAMS
    return asyncio.run(whatsapp.enviar_plantilla_evaluador("3399998888", plantilla, params, "Hola Mariana, tienes una entrevista."))


# ================= Cambio 46 · mapeo de plantillas =================
print("\n--- Cambio 46 · nombres EXACTOS de las plantillas en Meta (es_MX) ---")
check([notificaciones.plantilla_evaluador(e) for e in ("evaluacion_asignada", "evaluacion_reprogramada", "evaluacion_cancelada")]
      == ["aviso_evaluador_rh", "cita_cambio_evaluador_rh", "cita_cancelada_evaluador_rh"],
      "entrevista → aviso_evaluador_rh · cambio de cita → cita_cambio_evaluador_rh · cancelación → cita_cancelada_evaluador_rh")
check(notificaciones.plantilla_evaluador("evaluacion_asignada", {"capacitacion": True}) == "aviso_instructor"
      and notificaciones.plantilla_evaluador("recordatorio_evaluacion", {"capacitacion": True}) == "aviso_instructor",
      "instructor (capacitación presencial) → aviso_instructor")
check(notificaciones.plantilla_evaluador("evaluacion_asignada", {"tipo": "medica"}) == "aviso_medico",
      "médico → aviso_medico")
check(settings.meta_plantilla_evaluador_idioma == "es_MX", "idioma de las plantillas de evaluador: es_MX")

# ================= Cambio 46 · cascada =================
print("\n--- Cambio 46 · intento 1 texto libre, intento 2 plantilla ---")
MODO.update(texto=True, plantilla="ok")
desde = len(ENVIADOS)
r = enviar()
check(r.get("enviado") and r.get("formato") == "texto" and [c["type"] for c in ENVIADOS[desde:]] == ["text"],
      "dentro de la ventana de 24 h sale el texto libre y NO la plantilla")
MODO.update(texto=False, plantilla="ok")
desde = len(ENVIADOS)
r = enviar()
t = ENVIADOS[desde:]
check(r.get("enviado") and [c["type"] for c in t] == ["text", "template"] and t[1]["template"]["name"] == "aviso_evaluador_rh"
      and t[1]["template"]["language"]["code"] == "es_MX" and len(t[1]["template"]["components"][0]["parameters"]) == 6,
      "Meta rechaza el texto libre → sale la plantilla «aviso_evaluador_rh» (es_MX, 6 variables)")

print("\n--- Cambio 46 · plantilla en revisión = «Pendiente de plantilla», nunca «Fallido» ---")
MODO.update(texto=False, plantilla="pendiente")
r = enviar("aviso_medico")
check(not r.get("enviado") and r.get("estado") == "pendiente" and r.get("pendiente_plantilla")
      and r["reintento"]["plantilla"] == "aviso_medico" and r["reintento"]["parametros"] == PARAMS_MEDICO,
      "132001 → estado «pendiente» con lo necesario para reintentar")
check(whatsapp.es_plantilla_pendiente({"enviado": False, "detalle": "(#132001) Template name does not exist"})
      and whatsapp.es_plantilla_pendiente({"enviado": False, "detalle": "Template is pending review", "codigo": 100})
      and not whatsapp.es_plantilla_pendiente({"enviado": False, "detalle": "131026: Message undeliverable", "codigo": 131026}),
      "plantilla inexistente / en revisión se reconoce; un número inválido NO")
MODO.update(texto=False, plantilla="invalido")
r = enviar()
check(not r.get("enviado") and r.get("estado") != "pendiente" and r.get("error", "").startswith("Número inválido"),
      "error definitivo (131026) → fallido con el motivo legible")
check(ESTADOS_ENVIO.get("pendiente") == "Pendiente de plantilla", "estado visible «Pendiente de plantilla»")

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cu = Cuenta(nombre="C45", nombre_comercial="Operaciones C45", estado="Activa", slug="c45")
    db.add(cu)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cu.id))
    v = Vacante(codigo="VAC-C45", titulo="Almacenista", estado="Publicada", cuenta_id=cu.id, requisitos="Experiencia en almacén")
    db.add(v)
    db.flush()
    v.proceso = sproc.proceso_para_vacante(db, cu.id, {}, {"pasos": [
        {"id": "eh", "tipo": "entrevista_humana", "nombre": "Entrevista humana", "etapa": "Entrevista Humana"}], "etapas": {}})
    db.commit()
    for dep in (usuario_actual, usuario_decisor, usuario_admin):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cu

    print("\n--- Cambio 46 · de punta a punta: cita con la plantilla en revisión ---")
    p = crear_postulacion(db, _crear_candidato(db, cu.id, "Luisa Pérez", "Formulario", False, correo="luisa@c45.mx",
                                               telefono="5511450001"), v, cu.id, "formulario", consentimiento=True)
    p.etapa = "Entrevista Humana"
    db.commit()
    MODO.update(texto=False, plantilla="pendiente")
    fecha = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")
    r = client.post(f"/evaluaciones/postulaciones/{p.codigo}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "paso_id": "eh",
        "evaluador": {"tipo": "externo", "nombre": "Mariana Entrevista", "whatsapp": "3399998888"},
        "cita": {"fecha": fecha, "hora": "10:30", "modalidad": "Presencial", "direccion": "Av. Patria 100, Zapopan"},
        "notificar": {"cliente_correo": False, "cliente_whatsapp": False, "entrevistador_correo": False}})
    check(r.status_code == 201, f"entrevista humana con cita ({r.status_code} {r.text[:160]})")
    ev_cod = r.json()["evaluacion"]["codigo"]
    res = [x for x in r.json().get("resultados") or [] if x.get("destinatario") == "entrevistador" and x.get("canal") == "whatsapp"]
    check(res and res[0].get("estado") == "pendiente" and not res[0].get("enviado"),
          f"el aviso al entrevistador queda «pendiente» ({[(x.get('estado'), x.get('detalle')) for x in res]})")
    check(any("pendiente de plantilla" in a for a in r.json().get("advertencias") or []) or not r.json().get("advertencias"),
          "la advertencia a RH dice «pendiente de plantilla», no «falló»")
    fila = db.query(NotificacionEnviada).filter_by(destino="3399998888").order_by(NotificacionEnviada.id.desc()).first()
    check(fila is not None and fila.estado == "pendiente" and fila.reintento and fila.reintento["plantilla"] == "aviso_evaluador_rh",
          "en la BD: notificación «pendiente» con su reintento (plantilla aviso_evaluador_rh)")
    s = client.get(f"/procesos/postulaciones/{p.codigo}").json()
    paso = next(x for e in s["etapas"] for x in e["pasos"] if x["id"] == "eh")
    env = (paso.get("envios") or {}).get("entrevistador") or {}
    check(env.get("estado") == "pendiente" and env.get("estadoTexto") == "Pendiente de plantilla" and not env.get("error"),
          f"seguimiento: «Pendiente de plantilla», nunca «Fallido» ({env.get('estadoTexto')})")
    check((paso.get("aviso") or {}).get("texto") == "Pendiente de plantilla" and paso.get("estadoUnificado") != "error",
          f"la actividad no se marca como Error; el aviso dice «Pendiente de plantilla» ({paso.get('estadoUnificadoTexto')})")

    print("\n--- Cambio 46 · reintento automático ---")
    r1 = asyncio.run(reintento_plantillas.reintentar(db))
    db.refresh(fila)
    check(r1["pendientes"] >= 1 and fila.estado == "pendiente" and fila.reintento["intentos"] == 2,
          "la plantilla sigue en revisión → se queda pendiente y cuenta el intento")
    MODO.update(texto=False, plantilla="ok")
    desde = len(ENVIADOS)
    r2 = asyncio.run(reintento_plantillas.reintentar(db))
    db.refresh(fila)
    salio = [c for c in ENVIADOS[desde:] if c.get("type") == "template" and c.get("to") == "523399998888"]
    check(r2["enviados"] >= 1 and fila.estado == "enviado" and fila.enviado and salio
          and salio[0]["template"]["name"] == "aviso_evaluador_rh",
          "Meta aprobó la plantilla → el job la manda y la notificación queda «Enviado»")
    tr = db.query(EnvioActividad).filter_by(notificacion_id=fila.id).all()
    check(tr and all(x.estado == "enviado" for x in tr), "la trazabilidad de la actividad también pasa a «Enviado»")
    ev = db.query(Evaluacion).filter_by(codigo=ev_cod).one()
    check(ev.enviada_en is not None, "la evaluación registra el primer envío confirmado")
    desde = len(ENVIADOS)
    asyncio.run(reintento_plantillas.reintentar(db))
    check(not ENVIADOS[desde:], "ya enviada: el job no la vuelve a mandar")

    print("\n--- Cambio 46 · error definitivo en el reintento = «Fallido» con motivo ---")
    MODO.update(texto=False, plantilla="pendiente")
    r = client.post(f"/evaluaciones/{ev_cod}/ligas/evaluador/enviar")
    fila2 = db.query(NotificacionEnviada).filter_by(destino="3399998888", estado="pendiente").order_by(NotificacionEnviada.id.desc()).first()
    check(fila2 is not None, f"«Reenviar» con la plantilla en revisión → otra notificación pendiente ({r.status_code})")
    MODO.update(texto=False, plantilla="invalido")
    asyncio.run(reintento_plantillas.reintentar(db))
    db.refresh(fila2)
    check(fila2.estado == "fallido" and "131026" in fila2.detalle, "número inválido al reintentar → «Fallido»")
    s = client.get(f"/procesos/postulaciones/{p.codigo}").json()
    paso = next(x for e in s["etapas"] for x in e["pasos"] if x["id"] == "eh")
    env = (paso.get("envios") or {}).get("entrevistador") or {}
    check(env.get("estado") == "fallido" and env.get("error", "").startswith("Número inválido"),
          f"la UI muestra «Fallido» con el motivo ({env.get('estadoTexto')}: {env.get('error')})")
    vieja = datetime.now(timezone.utc) - timedelta(days=settings.meta_reintento_plantilla_dias + 1)
    fila2.estado, fila2.creada_en = "pendiente", vieja
    db.commit()
    desde = len(ENVIADOS)
    asyncio.run(reintento_plantillas.reintentar(db))
    check(not ENVIADOS[desde:], f"pasados {settings.meta_reintento_plantilla_dias} días ya no se reintenta")

    app.dependency_overrides.clear()
    db.close()

main_py = (RAIZ / "app/main.py").read_text(encoding="utf-8")
check('id="reintento_plantillas"' in main_py and "job_reintentar" in main_py, "el job de reintento está programado en el arranque")

# ================= Cambio 45 · pestañas =================
print("\n--- Cambio 45 · «Textos por plataforma» en pestañas ---")
tp = (APP / "components/dashboard/vacantes/texto-plataforma.tsx").read_text(encoding="utf-8")
form = (APP / "components/dashboard/vacantes/formulario-contenido.tsx").read_text(encoding="utf-8")
det = (APP / "app/dashboard/vacantes/page.tsx").read_text(encoding="utf-8")
check("export function PestanasTextoPlataforma" in tp and 'role="tablist"' in tp and "scroll-x" in tp,
      "componente de pestañas horizontales con desplazamiento lateral (.scroll-x)")
check("useState<string>(pestanas[0]?.canal" in tp and "<TextoPlataforma key={actual.canal} {...actual} />" in tp,
      "la primera pestaña queda activa y debajo se pinta SOLO el texto elegido")
i = form.index("<PestanasTextoPlataforma pestanas={[")
bloque = form[i:i + 900]
check(bloque.index('etiqueta: "WhatsApp"') < bloque.index('"portal", "occ", "linkedin"') and "<TextoPlataforma " not in form,
      "el formulario usa las pestañas con WhatsApp primero (ya no la lista vertical)")
check('liga: ligaDe("whatsapp")' in bloque and "liga: ligaDe(k)" in bloque and "sinMarkdown: true" in bloque
      and "onChange: (t: string) => setPub(k, t)" in bloque,
      "se conservan formato limpio, «Editar», «Copiar» y la liga ?canal=")
check("<BotonCopiarTexto texto={limpio} liga={liga} />" in tp and '{editar ? "Ver" : "Editar"}' in tp and "Liga al copiar:" in tp,
      "«Copiar» con liga, «Editar» y «Liga al copiar» intactos")
check('role="tablist" className="scroll-x' in det, "el detalle de la vacante también desliza sus pestañas")

print(f"\n🎉 Cambios 45-46: {OK} comprobaciones OK")
