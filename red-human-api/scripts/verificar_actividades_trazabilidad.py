"""Regresión de la operación de actividades (2026-10-08). Base desechable, SIN claves externas (canales simulados).

    .venv/Scripts/python.exe scripts/verificar_actividades_trazabilidad.py

1. Configuración en un paso: «Iniciar» ejecuta lo configurado; si falta un dato crítico lo pide (`faltan`) sin crear
   nada; repetir «Iniciar» nunca duplica. Actividad adicional = etapa ACTUAL (nunca regresa al candidato).
2. Compuerta: revisa TODAS las actividades previas desde la etapa base de la ruta (las opcionales y omitidas no
   bloquean); rutas anteriores a este cambio conservan la revisión de la etapa actual.
3. Trazabilidad por destinatario: intento / enviado / entregado (acuse de WhatsApp) / fallido; «Enviada» ya no es
   estado: la actividad dice a quién espera; un envío fallido = «Error»; reenvíos granulares sin reiniciar nada.
4. Médica estricta en dos fases (consentimiento → médico; rechazo = cancelada y «No aprobada») y referencias en dos
   fases (el candidato captura → el evaluador dictamina cada contacto).
5. Captura manual: «Registrar resultado» sin duplicados y con prioridad sobre un resultado tardío del proveedor;
   «Reintentar sincronización» solo con falla confirmada; «Persona bajo la lluvia» nunca va por API; la vista de
   psicometría separa configuración incompleta de fallas de red.
6. UI: menú «…» estandarizado, sin duplicados y sin la acción que ya es el botón principal.
"""

import hashlib
import hmac
import itertools
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

_dir = tempfile.mkdtemp(prefix="rh_actividades_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "actividades.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-actividades"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

import app.services.correo as correo_srv  # noqa: E402
import app.services.notificaciones as notif  # noqa: E402
import app.services.whatsapp as wa  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Cuenta, EnvioActividad, Evaluacion, EventoEvaluacion, Postulacion, PruebaPsicometrica, Usuario, UsuarioCuenta, Vacante,
)
from app.services import evaluaciones as sev  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import psicometricas as psi  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import engine  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402
from sqlalchemy import event  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


# ------------------------------------------------------------ canales simulados (nunca red)
ENVIOS = []  # (canal, destino, texto)
CANAL_OK = {"whatsapp": True, "correo": True}
_ids = itertools.count(1)


async def _wa(telefono, texto, *a, **k):
    ENVIOS.append(("whatsapp", telefono, texto))
    if not CANAL_OK["whatsapp"]:
        return {"enviado": False, "proveedor": "meta", "detalle": "131026 número no válido"}
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "wa_id": f"wamid.PRUEBA{next(_ids)}"}


async def _wa_boton(telefono, texto, boton, url, *a, **k):
    return await _wa(telefono, f"{texto} {url}")


async def _correo(destino, asunto, html, *a, **k):
    ENVIOS.append(("correo", destino, asunto))
    if not CANAL_OK["correo"]:
        return {"enviado": False, "proveedor": "resend", "detalle": "RESEND_API_KEY sin configurar"}
    return {"enviado": True, "proveedor": "resend", "detalle": "ok"}


wa.enviar_mensaje = notif.enviar_mensaje = _wa
wa.enviar_con_boton = _wa_boton
correo_srv.enviar_correo = notif.enviar_correo = _correo


def envios_a(destino):
    return [x for x in ENVIOS if x[1] == destino]


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    admin.telefono = admin.telefono or "5599990000"
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    cuenta = Cuenta(nombre="Actividades SA", nombre_comercial="Actividades", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    obtener(db).modo_prueba = False
    vacs = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).all()[:2]
    for v in vacs:
        v.cuenta_id, v.cliente_id, v.proceso, v.responsable_id = cuenta.id, None, {}, admin.id
    db.commit()
    V, V2 = vacs[0].codigo, vacs[1].codigo
    SIN_AUTO = {e: {"avance_automatico": False} for e in ("Prefiltro", "Entrevista IA", "Entrevista Humana", "Contratación", "Onboarding")}
    PASOS = [
        {"id": "solicitud", "tipo": "solicitud_web", "etapa": "Prefiltro"},
        {"id": "cv", "tipo": "analisis_cv", "etapa": "Prefiltro", "obligatorio": True},
        {"id": "medica", "tipo": "medica", "etapa": "Entrevista Humana"},
        {"id": "refs", "tipo": "referencias", "etapa": "Entrevista Humana", "obligatorio": False},
        {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana", "responsable": {"tipo": "usuario"}},
        {"id": "tec", "tipo": "tecnica", "etapa": "Entrevista Humana", "obligatorio": False},
        {"id": "psico", "tipo": "psicometrica", "etapa": "Entrevista Humana", "obligatorio": False},
        {"id": "cond", "tipo": "condiciones", "etapa": "Contratación"},
    ]
    for vac in (V, V2):
        r = client.put(f"/procesos/vacantes/{vac}", json={"pasos": PASOS, "etapas": SIN_AUTO})
        assert r.status_code == 200, r.text
    tel = iter(range(5584000000, 5584999999))

    def nueva(nombre, vac=V):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": str(next(tel)), "correo": f"{nombre.split()[0].lower()}@act.mx",
                                             "vacante": vac, "consentimiento": True, "fuente": "RH"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def seg(codigo):
        return client.get(f"/procesos/postulaciones/{codigo}").json()

    def paso(s, pid):
        return next(x for e in s["etapas"] for x in e["pasos"] if x["id"] == pid)

    def post(codigo):
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=codigo).one()

    def evs(codigo, paso_id):
        return db.query(Evaluacion).filter(Evaluacion.postulacion_id == post(codigo).id, Evaluacion.paso_id == paso_id).all()

    MEDICO = {"tipo": "externo", "nombre": "Dra. Salud", "correo": "dra@clinica.mx", "whatsapp": "5511112222"}

    # ================= 1. Configuración en un paso =================
    print("\n--- 1. «Iniciar» en un paso, sin duplicados ---")
    P = nueva("Ana Actividades")
    r = client.post(f"/procesos/postulaciones/{P}/pasos/eh/iniciar", json={})
    check(r.status_code == 200 and r.json()["iniciada"] is False and r.json()["faltan"] == ["evaluador"] and not evs(P, "eh"),
          "entrevista humana sin entrevistador en la ruta → pide SOLO el entrevistador y no crea nada")
    r = client.post(f"/procesos/postulaciones/{P}/pasos/eh/iniciar", json={"evaluador": {"tipo": "interno", "usuario_id": admin.id}})
    check(r.status_code == 200 and r.json()["iniciada"] and len(evs(P, "eh")) == 1, "con el dato que faltaba se inicia (una evaluación ligada a la actividad)")
    r = client.post(f"/procesos/postulaciones/{P}/pasos/eh/iniciar", json={"evaluador": {"tipo": "interno", "usuario_id": admin.id}})
    check(r.status_code == 200 and r.json().get("yaExistia") and len(evs(P, "eh")) == 1, "«Iniciar» otra vez nunca duplica: regresa la existente")
    r = client.post(f"/procesos/postulaciones/{P}/pasos/tec/iniciar", json={})
    ev_tec = evs(P, "tec")
    check(r.status_code == 200 and r.json()["iniciada"] and len(ev_tec) == 1 and ev_tec[0].evaluador_usuario_id == admin.id,
          "técnica con responsable RH → se asigna al responsable de la vacante sin pedir nada")
    etapa_antes = post(P).etapa
    r = client.post(f"/procesos/postulaciones/{P}/pasos", json={"tipo": "socioeconomica", "etapa": "Prefiltro"})
    nuevo = r.json()["paso"]
    check(r.status_code == 201 and nuevo["etapa"] == etapa_antes and post(P).etapa == etapa_antes,
          f"actividad adicional pedida en una etapa anterior → va en la ACTUAL ({etapa_antes}) y no regresa al candidato")
    r = client.post(f"/procesos/postulaciones/{P}/pasos", json={"tipo": "condiciones"})
    check(r.status_code == 201 and r.json()["paso"]["etapa"] == etapa_antes, "un tipo de otra etapa también se agrega en la etapa actual (solo este candidato)")

    # ================= 2. Compuerta: todas las previas =================
    print("\n--- 2. Compuerta ---")
    PG = nueva("Gema Compuerta")
    pg = post(PG)
    check(pg.proceso.get("etapa_base") == "Prefiltro", "la ruta congelada guarda su etapa base")
    pg.etapa = "Entrevista Humana"  # simula que llegó ahí sin pasar por la compuerta
    db.commit()
    falta = [x["id"] for x in sproc.faltantes(sproc.estado_pasos(post(PG)), sproc.desde_compuerta(post(PG)), "Contratación")]
    check("cv" in falta, "hacia Contratación también revisa las actividades OBLIGATORIAS de etapas anteriores (Análisis de CV)")
    check(not {"refs", "tec", "psico"} & set(falta) and "medica" in falta, "las opcionales no bloquean; las obligatorias en paralelo sí")
    proc = dict(post(PG).proceso)
    proc.pop("etapa_base")
    pg = post(PG)
    pg.proceso = proc
    db.commit()
    falta = [x["id"] for x in sproc.faltantes(sproc.estado_pasos(post(PG)), sproc.desde_compuerta(post(PG)), "Contratación")]
    check("cv" not in falta, "ruta anterior a este cambio (sin etapa base): solo revisa su etapa actual (retrocompatible)")
    s = seg(PG)
    check(paso(s, "cv")["condicion"] == "aprobacion" and paso(s, "tec")["condicion"] == "opcional"
          and paso(s, "solicitud")["condicionTexto"] == "Requiere completarse",
          "cada actividad dice su condición: Requiere completarse / Requiere aprobación / Opcional")

    # ================= 3 + 4. Médica en dos fases + trazabilidad =================
    print("\n--- 3. Médica estricta en dos fases + trazabilidad por destinatario ---")
    PM = nueva("Mara Médica")
    tel_m = post(PM).telefono
    r = client.post(f"/procesos/postulaciones/{PM}/pasos/medica/iniciar", json={})
    check(r.json()["faltan"] == ["evaluador"], "médica sin médico en la ruta → pide SOLO al médico")
    ENVIOS.clear()
    r = client.post(f"/procesos/postulaciones/{PM}/pasos/medica/iniciar", json={"evaluador": MEDICO})
    check(r.status_code == 200 and r.json()["iniciada"], "médica iniciada con el médico capturado")
    check(any("consentimiento" in x[2] for x in envios_a(tel_m)) and not envios_a("5511112222") and not envios_a("dra@clinica.mx"),
          "fase 1: la solicitud de consentimiento sale SOLA al candidato; al médico no le sale nada")
    x = paso(seg(PM), "medica")
    check(x["estadoUnificado"] == "esperando_consentimiento" and x["esperando"]["quien"] == "candidato",
          "estado = «Esperando consentimiento» (nunca «Enviada»)")
    check(x["envios"]["candidato"]["estado"] == "enviado" and "medico" not in x["envios"], "envío al candidato «Enviado»; ninguno al médico")
    check([m["clave"] for m in x["menu"] if m["clave"].startswith("reenviar")] == ["reenviar_candidato"] and "registrar_resultado" not in [m["clave"] for m in x["menu"]],
          "mientras no hay consentimiento: solo «Reenviar al candidato» y no se puede registrar resultado")
    # acuse de entrega de WhatsApp → «Entregado»
    wamid = db.query(EnvioActividad).filter(EnvioActividad.postulacion_id == post(PM).id, EnvioActividad.canal == "whatsapp").first().mensaje_id
    acuse = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"statuses": [{"id": wamid, "status": "delivered"}]}}]}]}
    cuerpo = json.dumps(acuse).encode()
    JSON_H = {"Content-Type": "application/json"}
    r = client.post("/webhooks/whatsapp", content=cuerpo, headers=JSON_H)
    check(r.status_code == 200 and paso(seg(PM), "medica")["envios"]["candidato"]["estado"] == "enviado",
          "sin META_APP_SECRET un acuse SIN firma no altera nada (no se puede verificar)")
    settings.meta_app_secret = "secreto-de-prueba"
    firma = "sha256=" + hmac.new(b"secreto-de-prueba", cuerpo, hashlib.sha256).hexdigest()
    check(client.post("/webhooks/whatsapp", content=cuerpo, headers=JSON_H).status_code == 403, "con META_APP_SECRET: webhook SIN firma → 403")
    check(client.post("/webhooks/whatsapp", content=cuerpo, headers={**JSON_H, "X-Hub-Signature-256": "sha256=" + "0" * 64}).status_code == 403,
          "firma inválida → 403 (nada se procesa)")
    alterado = json.dumps({**acuse, "x": 1}).encode()
    check(client.post("/webhooks/whatsapp", content=alterado, headers={**JSON_H, "X-Hub-Signature-256": firma}).status_code == 403,
          "firma de OTRO cuerpo (payload alterado) → 403")
    check(paso(seg(PM), "medica")["envios"]["candidato"]["estado"] == "enviado", "un acuse rechazado nunca cambia la trazabilidad")
    r = client.post("/webhooks/whatsapp", content=cuerpo, headers={**JSON_H, "X-Hub-Signature-256": firma})
    check(r.status_code == 200 and paso(seg(PM), "medica")["envios"]["candidato"]["estado"] == "entregado",
          "acuse «delivered» con firma válida → el envío al candidato queda «Entregado»")
    prov = settings.whatsapp_provider
    settings.meta_app_secret, settings.whatsapp_provider = "", "meta"
    check(client.post("/webhooks/whatsapp", content=cuerpo, headers={**JSON_H, "X-Hub-Signature-256": firma}).status_code == 503,
          "WHATSAPP_PROVIDER=meta sin META_APP_SECRET → 503 (nunca se procesa un webhook de Meta sin verificar)")
    settings.whatsapp_provider = prov
    lotes_antes = db.query(EnvioActividad).filter(EnvioActividad.postulacion_id == post(PM).id).count()
    r = client.post(f"/procesos/postulaciones/{PM}/pasos/medica/reenviar", json={"a": "candidato"})
    x = paso(r.json()["proceso"] and seg(PM), "medica")
    check(r.status_code == 200 and r.json()["enviado"] and x["envios"]["candidato"]["intentos"] == 2
          and x["estadoUnificado"] == "esperando_consentimiento" and db.query(EnvioActividad).filter(EnvioActividad.postulacion_id == post(PM).id).count() > lotes_antes,
          "«Reenviar al candidato» agrega un envío nuevo SIN reiniciar ni cambiar el estado de la actividad")
    r = client.post(f"/procesos/postulaciones/{PM}/pasos/medica/reenviar", json={"a": "medico"})
    check(r.status_code == 409, "«Reenviar al médico» no existe antes del consentimiento")
    ev_m = evs(PM, "medica")[0]
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/publica/consentimiento/{ev_m.consentimiento_token}/aceptar", json={"nombre": "Mara Médica López", "acepto": True})
    check(r.status_code == 200 and (envios_a("5511112222") or envios_a("dra@clinica.mx")), "fase 2: al aceptar, la asignación sale SOLA al médico")
    x = paso(seg(PM), "medica")
    check(x["estadoUnificado"] == "esperando_evaluador" and x["estadoUnificadoTexto"] == "Esperando médico" and x["envios"]["medico"]["estado"] == "enviado",
          "estado = «Esperando médico» con su envío trazado")
    check({"reenviar_medico", "registrar_resultado"} <= {m["clave"] for m in x["menu"]}
          and next(m for m in x["menu"] if m["clave"] == "reenviar_medico")["texto"] == "Reenviar al médico",
          "ya con consentimiento: «Reenviar al médico» y «Registrar resultado» en el «…»")
    # rechazo → cancelada + No aprobada
    PR = nueva("Rita Rechazo")
    client.post(f"/procesos/postulaciones/{PR}/pasos/medica/iniciar", json={"evaluador": MEDICO})
    ev_r = evs(PR, "medica")[0]
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/publica/consentimiento/{ev_r.consentimiento_token}/rechazar")
    db.expire_all()
    check(r.status_code == 200 and db.get(Evaluacion, ev_r.id).estado == "cancelada" and not envios_a("dra@clinica.mx"),
          "si el candidato RECHAZA: la evaluación médica se cancela sola y al médico nunca le sale nada")
    x = paso(seg(PR), "medica")
    check(x["estadoUnificado"] == "no_aprobada" and "rechazó" in x["detalle"], "la actividad médica queda «No aprobada» (RH decide: descartar u omitir)")
    # envío fallido → Error
    PF = nueva("Fabi Falla")
    CANAL_OK.update(whatsapp=False, correo=False)
    client.post(f"/procesos/postulaciones/{PF}/pasos/medica/iniciar", json={"evaluador": MEDICO})
    CANAL_OK.update(whatsapp=True, correo=True)
    x = paso(seg(PF), "medica")
    check(x["estadoUnificado"] == "error" and "consentimiento" in (x["error"] or "") and x["envios"]["candidato"]["estado"] == "fallido",
          "si el envío al candidato falla por todos sus canales → «Error» con el motivo (nunca silencioso)")
    client.post(f"/procesos/postulaciones/{PF}/pasos/medica/reenviar", json={"a": "candidato"})
    check(paso(seg(PF), "medica")["estadoUnificado"] == "esperando_consentimiento", "reenviar con éxito saca a la actividad del «Error»")

    # ================= 4b. Referencias en dos fases =================
    print("\n--- 4. Referencias laborales en dos fases ---")
    PRF = nueva("Rosa Referencias")
    tel_r = post(PRF).telefono
    ENVIOS.clear()
    r = client.post(f"/procesos/postulaciones/{PRF}/pasos/refs/iniciar", json={})
    ev_rf = evs(PRF, "refs")[0]
    check(r.json()["iniciada"] and ev_rf.referencias_token and any("referencias" in x[2] for x in envios_a(tel_r)),
          "fase 1: el candidato recibe su liga EXCLUSIVA para capturar sus referencias")
    check(len(ENVIOS) == len(envios_a(tel_r)) + len(envios_a(post(PRF).correo)), "al evaluador no le sale nada mientras el candidato no capture")
    x = paso(seg(PRF), "refs")
    check(x["estadoUnificado"] == "esperando_referencias" and x["liga"]["url"].endswith(f"/referencias/{ev_rf.referencias_token}"),
          "estado = «Esperando referencias» y la liga real es la de captura")
    vista = client.get(f"/evaluaciones/publica/referencias/{ev_rf.referencias_token}").json()
    check(vista["capturadas"] is False and vista["minimo"] >= 1, "la liga pública muestra el formulario de captura")
    check(client.post(f"/evaluaciones/publica/referencias/{ev_rf.referencias_token}", json={"referencias": [{"nombre": "Jefe Uno", "empresa": ""}]}).status_code == 400,
          "cada referencia exige nombre, empresa y un teléfono o correo")
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/publica/referencias/{ev_rf.referencias_token}", json={"referencias": [
        {"nombre": "Jefe Uno", "empresa": "Acme", "puesto": "Gerente", "relacion": "Jefe directo", "telefono": "55 1234 5678"},
        {"nombre": "Compa Dos", "empresa": "Beta", "correo": "dos@beta.mx"},
    ]})
    check(r.status_code == 200 and r.json()["total"] == 2 and (envios_a(admin.correo) or envios_a(admin.telefono)),
          "fase 2: al capturarlas, el evaluador recibe su liga en ese momento")
    check(client.post(f"/evaluaciones/publica/referencias/{ev_rf.referencias_token}", json={"referencias": [{"nombre": "Otro", "empresa": "X", "correo": "o@x.mx"}]}).status_code == 409,
          "la captura no se repite")
    x = paso(seg(PRF), "refs")
    check(x["estadoUnificado"] == "esperando_evaluador" and x["envios"]["evaluador"]["estado"] == "enviado", "estado = «Esperando evaluador» con su envío trazado")
    db.expire_all()
    ev_rf = db.get(Evaluacion, ev_rf.id)
    pub = client.get(f"/evaluaciones/publica/{ev_rf.token_evaluador}").json()
    refs = pub["evaluacion"]["referencias"]
    check(len(refs) == 2 and refs[0]["telefono"] == "5512345678", "la liga del evaluador trae ESOS contactos (teléfono a 10 dígitos)")
    r = client.post(f"/evaluaciones/publica/{ev_rf.token_evaluador}/referencias/{refs[0]['id']}", json={"contactado": True, "dictamen": "favorable", "comentario": "Lo recomienda"})
    r2 = client.post(f"/evaluaciones/{ev_rf.codigo}/referencias/{refs[1]['id']}", json={"contactado": False, "comentario": "No contestó en 3 intentos"})
    check(r.status_code == 200 and r.json()["referencia"]["dictamen"] == "favorable" and r2.status_code == 200 and r2.json()["referencia"]["contactado"] is False,
          "dictamen POR CONTACTO desde la liga del evaluador y desde el sistema (mismo registro)")
    check(db.query(EventoEvaluacion).filter(EventoEvaluacion.evaluacion_id == ev_rf.id, EventoEvaluacion.accion == "referencia_dictaminada").count() == 2,
          "cada dictamen queda en el historial")

    # ================= 5. Captura manual y proveedor =================
    print("\n--- 5. Captura manual, proveedor y pruebas sin API ---")
    PT = nueva("Tito Técnica")
    r = client.post(f"/procesos/postulaciones/{PT}/pasos/tec/resultado", data={"conclusion": "favorable", "comentarios": "Caso práctico en oficina",
                                                                               "realizada_por": "Ing. Pérez"})
    t = evs(PT, "tec")
    check(r.status_code == 200 and len(t) == 1 and t[0].forma == "registro_directo" and t[0].estado == "con_resultado"
          and t[0].realizada_por == "Ing. Pérez" and t[0].registrada_por == admin.nombre,
          "«Registrar resultado» sin haberla iniciado: una sola evaluación ligada a la actividad con dictamen, quién la aplicó y quién capturó")
    check(client.post(f"/procesos/postulaciones/{PT}/pasos/tec/resultado", data={"conclusion": "favorable"}).status_code == 409 and len(evs(PT, "tec")) == 1,
          "no se registra dos veces ni se duplica")
    r = client.post(f"/procesos/postulaciones/{P}/pasos/tec/resultado", data={"conclusion": "con_observaciones", "realizada_por": "Ing. Ruiz"})
    check(r.status_code == 200 and len(evs(P, "tec")) == 1 and evs(P, "tec")[0].conclusion == "con_observaciones",
          "con la actividad ya iniciada, el resultado manual se guarda en ESA misma evaluación")
    # psicometría con captura manual y aviso tardío del proveedor
    PP = nueva("Pepe Psico")
    ev_p = sev.nueva(post(PP), cuenta.id, admin.nombre, admin.id, tipo="psicometrica", forma="integrada", paso_id="psico",
                     proveedor="Psicométricas.mx", id_proveedor="1", nombre="Cleaver", paso_integrada="enviada")
    db.add(ev_p)
    db.flush()
    sev.asignar_codigo(ev_p)
    ev_p.clave_proveedor = "1-PRUEBA-0001"
    db.commit()
    r = client.post(f"/procesos/postulaciones/{PP}/pasos/psico/resultado", data={"conclusion": "favorable", "comentarios": "Aplicada en papel",
                                                                                 "realizada_por": "Psic. Luna"})
    check(r.status_code == 200, "psicometría aplicada en papel: RH registra el resultado en la misma evaluación")
    llamadas = []
    orig = psi.consultar_candidato
    psi.consultar_candidato = lambda clave: (llamadas.append(clave), [])[1]
    db.expire_all()
    ev_p = db.get(Evaluacion, ev_p.id)
    rr = sev.recuperar_resultado(db, ev_p, origen="webhook")
    db.commit()
    psi.consultar_candidato = orig
    db.expire_all()
    ev_p = db.get(Evaluacion, ev_p.id)
    check(rr == "ya_estaba" and ev_p.conclusion == "favorable" and ev_p.registrada_por == admin.nombre and not llamadas,
          "un resultado tardío del proveedor NO sobrescribe la captura manual (ni consume saldo de su API)")
    check(db.query(EventoEvaluacion).filter(EventoEvaluacion.evaluacion_id == ev_p.id, EventoEvaluacion.accion == "resultado_proveedor_posterior").count() == 1,
          "…y queda constancia en el historial (no es silencioso)")
    # pruebas sin API
    r = client.post("/evaluaciones/pruebas", json={"nombre": "Persona bajo la lluvia", "modo": "integrada", "proveedor": "Psicométricas.mx", "id_proveedor": "1"})
    check(r.status_code == 201 and r.json()["modo"] == "manual", "«Persona bajo la lluvia» no existe en la API del proveedor → se guarda para evaluador / registro manual")
    lluvia = db.query(PruebaPsicometrica).filter(PruebaPsicometrica.id == r.json()["id"]).one()
    lluvia.modo = "integrada"  # una captura vieja que la marcó como integrada
    db.commit()
    vista = client.get(f"/evaluaciones/postulaciones/{PP}/psicometria").json()
    check(all(c["id"] != lluvia.id for c in vista["catalogo"]), "aunque una captura vieja la tenga como integrada, nunca aparece para enviar por API")
    r = client.post(f"/evaluaciones/postulaciones/{nueva('Lalo Lluvia')}/psicometria", json={"prueba_ids": [lluvia.id]})
    check(r.status_code == 400 and "Persona bajo la lluvia" in r.json()["detail"] and "evaluador" in r.json()["detail"],
          "asignarla por API → 400 claro: se aplica con evaluador o registro manual")
    check(vista["configuracion"]["completa"] is False and any(f["clave"] in ("catalogo", "bateria") for f in vista["configuracion"]["faltantes"]),
          "vista de psicometría: la configuración incompleta llega como lista de faltantes (no como error de carga)")

    # ================= 6. Menú estandarizado =================
    print("\n--- 6. Una acción principal + «…» estandarizado ---")
    s = seg(PM)
    nombres_validos = {"Abrir liga", "Copiar liga", "Registrar resultado", "Reintentar sincronización", "Omitir actividad", "Reactivar"}
    todos = [m for e in s["etapas"] for x in e["pasos"] for m in x.get("menu", [])]
    check(all(m["texto"] in nombres_validos or m["clave"] in ("accion",) or m["clave"].startswith("reenviar_") for m in todos),
          "el «…» solo usa nombres estandarizados")
    check(all(len({m["clave"] for m in x["menu"]}) == len(x["menu"]) for e in s["etapas"] for x in e["pasos"]), "sin acciones duplicadas en un mismo «…»")
    sig = s["siguienteAccion"]
    if sig.get("paso") and sig["tipo"] == "paso":
        check(all(m["clave"] != "accion" for m in paso(s, sig["paso"])["menu"]), "la acción que ya es el botón principal no se repite en el «…» de su actividad")
    x = paso(s, "medica")
    check(x["liga"] and {"abrir_liga", "copiar_liga"} <= {m["clave"] for m in x["menu"]}, "Abrir liga / Copiar liga viven en el «…» (copiar no marca nada)")
    check(not any(x.get("estadoUnificado") == "enviada" for e in s["etapas"] for x in e["pasos"]), "«Enviada» ya no existe como estado")

    # ================= 7. Tablero sin N+1 =================
    print("\n--- 7. Tablero: envíos y demás precargados en bloque ---")
    CONSULTAS = []

    def _contar(conn, cursor, sql, *a):
        CONSULTAS.append(sql.lower())

    def medir():
        CONSULTAS.clear()
        event.listen(engine, "before_cursor_execute", _contar)
        try:
            r = client.get("/candidatos")
        finally:
            event.remove(engine, "before_cursor_execute", _contar)
        tablas = ("envios_actividad", "eventos_evaluacion", "tareas_onboarding", "from usuarios")
        return len(r.json()), {t: sum(1 for q in CONSULTAS if t in q) for t in tablas}

    n1, c1 = medir()
    for i in range(6):
        nueva(f"Nora{i} Masiva")
    n2, c2 = medir()
    check(n2 >= n1 + 6, f"el tablero creció ({n1} → {n2} tarjetas)")
    check(c1 == c2 and all(v <= 2 for v in c2.values()),
          f"consultas de envíos / eventos / tareas / usuarios CONSTANTES sin importar las tarjetas ({c1} → {c2})")
    db.close()

print(f"\n🎉 Actividades: configuración en un paso, trazabilidad por destinatario, flujos de dos fases y captura manual — {OK} verificaciones OK")
