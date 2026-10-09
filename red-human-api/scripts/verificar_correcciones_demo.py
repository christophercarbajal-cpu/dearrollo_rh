"""Verificación de las 4 correcciones dictadas por el cliente tras la demo (2026-09-15) — modo demo
(sin OpenAI, sin Meta), base SQLite desechable.

1. Memoria de 5 días en WhatsApp: en etapas avanzadas la postulación NO expira a los 60 min de Modo
   Prueba; "sí quiero reagendar" al día 3 cae en la postulación en curso y reabre la agenda.
2. Aviso de no-show idempotente: dos corridas del job (o dos workers) = UN solo «¿Quieres reagendar?».
3. Alta como colaborador bloqueada (400) sin documentos adjuntos, incluso con forzar_prueba.
4. Solicitud de documentos por plantilla + documento recibido por WhatsApp adjuntado al expediente.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_correcciones_demo.py
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_demo_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "demo.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-demo"
os.environ["SEMBRAR_DEMO"] = "true"  # los scripts de verificación sí usan los datos de ejemplo

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cuenta, Documento, Mensaje, NotificacionEnviada, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import agenda, whatsapp  # noqa: E402
from app.services import notificaciones as notif  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0


import itertools as _it
_WAMID = _it.count(1)  # wamid único por mensaje simulado (el webhook deduplica reintentos)


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def webhook_meta(client, telefono: str, texto: str = "", tipo: str = "text", media: dict = None, wamid: str = "wamid.1"):
    m = {"from": telefono, "id": f"{wamid}.{next(_WAMID)}", "type": tipo}
    if tipo == "text":
        m["text"] = {"body": texto}
    else:
        m[tipo] = {**(media or {}), **({"caption": texto} if texto else {})}
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"display_phone_number": "5215500000000"},
        "contacts": [{"profile": {"name": "Karla Demo"}, "wa_id": telefono}],
        "messages": [m],
    }}]}]}
    # 2026-10-08: con WHATSAPP_PROVIDER=meta la firma X-Hub-Signature-256 es obligatoria (como la manda Meta)
    import hashlib
    import hmac
    import json as _json

    from app.config import settings as _s

    _s.meta_app_secret = _s.meta_app_secret or "secreto-correcciones-demo"
    cuerpo = _json.dumps(payload).encode()
    firma = "sha256=" + hmac.new(_s.meta_app_secret.encode(), cuerpo, hashlib.sha256).hexdigest()
    return client.post("/webhooks/whatsapp", content=cuerpo, headers={"Content-Type": "application/json", "X-Hub-Signature-256": firma})


# --- WhatsApp saliente simulado: guarda lo que se manda ---
ENVIADOS = []


async def _fake_enviar_mensaje(telefono, texto):
    ENVIADOS.append({"tipo": "texto", "telefono": telefono, "texto": texto})
    return {"enviado": True, "proveedor": "meta", "detalle": 200, "wa_id": f"wamid.out.{len(ENVIADOS)}"}


async def _fake_enviar_lista(telefono, *a, **k):
    ENVIADOS.append({"tipo": "lista", "telefono": telefono})
    return {"enviado": True, "proveedor": "meta"}


PLANTILLAS = []


async def _fake_enviar_plantilla(telefono, plantilla, parametros=None, idioma=None):
    PLANTILLAS.append({"telefono": telefono, "plantilla": plantilla, "parametros": parametros, "idioma": idioma})
    if plantilla == "rota":
        return {"enviado": False, "proveedor": "meta", "detalle": "132001: Template name does not exist"}
    return {"enviado": True, "proveedor": "meta", "detalle": 200, "wa_id": "wamid.tpl"}


PDF_MIN = b"%PDF-1.4\n" + b"%" * 600 + b"\n%%EOF\n"


async def _fake_descargar_media(media_id):
    if media_id == "media-mala":
        return {"ok": False, "detalle": "Graph 400: media not found"}
    return {"ok": True, "contenido": PDF_MIN, "mime": "application/pdf", "extension": "pdf", "filename": f"whatsapp_{media_id}.pdf", "tamano": len(PDF_MIN)}


import app.routers.webhooks as rw  # noqa: E402
import app.routers.candidatos as rc  # noqa: E402

rw.enviar_mensaje = _fake_enviar_mensaje
rw.enviar_lista_interactiva = _fake_enviar_lista
rw.descargar_media = _fake_descargar_media
rc.enviar_mensaje = _fake_enviar_mensaje
agenda.enviar_mensaje = _fake_enviar_mensaje
notif.enviar_mensaje = _fake_enviar_mensaje
whatsapp.enviar_plantilla = _fake_enviar_plantilla
whatsapp.enviar_mensaje = _fake_enviar_mensaje  # fallback de enviar_plantilla_documentos
whatsapp.settings.whatsapp_provider = "meta"  # para que enviar_plantilla_documentos use la plantilla
whatsapp.settings.meta_plantilla_documentos = "solicitud_documentos_rh"
whatsapp.settings.meta_plantilla_documentos_params = "nombre,documentos,liga"

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta Demo", nombre_comercial="Demo RH", estado="Activa", whatsapp_comunicacion="5500000000")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta.id
        for p in c.postulaciones:
            p.cuenta_id = cuenta.id
    cfg = obtener(db)
    cfg.modo_prueba = True
    cfg.modo_prueba_ventana_min = 60
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    r = client.post("/vacantes", json={"titulo": "Cajero Demo", "descripcion": "x", "generar_si_falta": False, "publicar": True})
    VAC = r.json()["id"]
    client.post(f"/vacantes/{VAC}/publicar")
    TEL = "5215512345678"

    # ================= 1. Memoria de 5 días =================
    print("\n--- 1. Memoria de contexto en WhatsApp (Modo Prueba, ventana 60 min) ---")
    r = client.post("/candidatos", json={"nombre": "Karla Demo", "telefono": "5512345678", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    p = db.query(Postulacion).filter_by(codigo=P).one()
    # Postulación apta con videollamada agendada hace 3 días, sin actividad desde entonces.
    p.etapa = "Entrevista IA"
    p.estado = "cumple"
    p.prefiltro_completo = True
    p.videollamada_agendada_en = datetime.now(timezone.utc) - timedelta(days=3)
    p.videollamada_liga = "https://demo/entrevista/abc"
    p.ultima_actividad_en = datetime.now(timezone.utc) - timedelta(days=3)
    p.candidato.postulacion_conversacion_id = p.id
    p.candidato.wa_id = TEL
    db.commit()
    hace_3d = datetime.now(timezone.utc) - timedelta(days=3)
    db.add(Mensaje(candidato_id=p.candidato_id, postulacion_id=p.id, rol="assistant", texto="Cita confirmada", canal="whatsapp", enviado=True, creado_en=hace_3d))
    db.commit()

    ENVIADOS.clear()
    r = webhook_meta(client, TEL, "Hola, sí quiero reagendar mi entrevista")
    db.expire_all()
    p = db.query(Postulacion).filter_by(codigo=P).one()
    check(r.status_code == 200 and r.json().get("postulacion") == P, f"día 3: el mensaje se enruta a la postulación en curso ({r.json().get('accion')}), no al menú")
    check(p.activa and p.motivo_cierre == "", "día 3: la postulación sigue activa (no expiró como prueba_expirada)")
    check(not any(e["tipo"] == "lista" for e in ENVIADOS), "día 3: NO se mandó el menú de vacantes")
    check(p.videollamada_agendada_en is None and (p.analisis or {}).get("videollamadas_anteriores"), "reagendar: la cita anterior se soltó y quedó en analisis.videollamadas_anteriores")
    check(p.espera_respuesta, "reagendar: la postulación vuelve a esperar respuesta (agenda abierta)")
    check(ENVIADOS and ENVIADOS[-1]["tipo"] == "texto" and "agenda" in ENVIADOS[-1]["texto"].lower(), f"reagendar: el agente de agenda respondió por WhatsApp («{ENVIADOS[-1]['texto'][:60] if ENVIADOS else ''}»)")

    # Prefiltro sí sigue expirando con la ventana corta
    r = client.post("/candidatos", json={"nombre": "Pepe Prueba", "telefono": "5598765432", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P2 = r.json()["id"]
    p2 = db.query(Postulacion).filter_by(codigo=P2).one()
    p2.candidato.postulacion_conversacion_id = p2.id
    p2.candidato.wa_id = "5215598765432"
    p2.ultima_actividad_en = datetime.now(timezone.utc) - timedelta(hours=2)
    p2.creado_en = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()
    r = webhook_meta(client, "5215598765432", "hola")
    db.expire_all()
    p2 = db.query(Postulacion).filter_by(codigo=P2).one()
    check(not p2.activa and p2.motivo_cierre == "prueba_expirada", "Prefiltro frío (2 h) sigue expirando con la ventana corta de Modo Prueba")

    # Etapa avanzada con 6 días sin actividad → sin_interes
    r = client.post("/candidatos", json={"nombre": "Lalo Lejano", "telefono": "5511112222", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P3 = r.json()["id"]
    p3 = db.query(Postulacion).filter_by(codigo=P3).one()
    p3.etapa = "Entrevista IA"  # etapa avanzada (Filtro Red Human)
    p3.candidato.postulacion_conversacion_id = p3.id
    p3.candidato.wa_id = "5215511112222"
    p3.ultima_actividad_en = datetime.now(timezone.utc) - timedelta(days=6)
    p3.creado_en = datetime.now(timezone.utc) - timedelta(days=6)
    db.commit()
    r = webhook_meta(client, "5215511112222", "hola")
    db.expire_all()
    p3 = db.query(Postulacion).filter_by(codigo=P3).one()
    check(not p3.activa and p3.motivo_cierre == "sin_interes", "etapa avanzada con 6 días sin actividad → cerrada por sin_interes (pasados los 5 días)")

    # Modo normal: nunca expira
    cfg = obtener(db)
    cfg.modo_prueba = False
    db.commit()
    r = client.post("/candidatos", json={"nombre": "Nora Normal", "telefono": "5533334444", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P4 = r.json()["id"]
    p4 = db.query(Postulacion).filter_by(codigo=P4).one()
    p4.etapa = "Contratación"
    p4.candidato.postulacion_conversacion_id = p4.id
    p4.candidato.wa_id = "5215533334444"
    p4.ultima_actividad_en = datetime.now(timezone.utc) - timedelta(days=10)
    p4.creado_en = datetime.now(timezone.utc) - timedelta(days=10)
    db.commit()
    r = webhook_meta(client, "5215533334444", "hola")
    db.expire_all()
    p4 = db.query(Postulacion).filter_by(codigo=P4).one()
    check(p4.activa and r.json().get("postulacion") == P4, "Modo Prueba apagado: 10 días sin actividad y el mensaje sigue cayendo en la postulación (nunca se cierra sola)")
    cfg = obtener(db)
    cfg.modo_prueba = True
    db.commit()

    # ================= 2. No-show idempotente =================
    print("\n--- 2. Aviso de no-show sin duplicados ---")
    r = client.post("/candidatos", json={"nombre": "Nino Noshow", "telefono": "5555556666", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P5 = r.json()["id"]
    p5 = db.query(Postulacion).filter_by(codigo=P5).one()
    p5.etapa = "Entrevista IA"
    p5.estado = "cumple"
    p5.prefiltro_completo = True
    p5.videollamada_agendada_en = datetime.now(timezone.utc) - timedelta(hours=1)
    p5.candidato.wa_id = "5215555556666"
    db.commit()
    ENVIADOS.clear()
    n1 = asyncio.run(agenda.revisar_videollamadas_noshow())
    n2 = asyncio.run(agenda.revisar_videollamadas_noshow())
    avisos = [e for e in ENVIADOS if e["texto"] == agenda.MENSAJE_RESCATE and e["telefono"] == "5555556666"]
    check(n1 == 1 and n2 == 0 and len(avisos) == 1, f"dos corridas del job → UN solo aviso de reagendar (corridas: {n1}, {n2})")
    reg = db.query(NotificacionEnviada).filter_by(evento=agenda.EVENTO_REAGENDAR, candidato_id=p5.candidato_id).count()
    check(reg == 1, "el aviso quedó registrado UNA vez en notificaciones_enviadas (evento reagendar_noshow)")
    # Simula que el flag se perdió (otro worker / reinicio) dentro de las 24 h → sigue sin repetirse
    db.expire_all()
    p5 = db.query(Postulacion).filter_by(codigo=P5).one()
    p5.videollamada_aviso_noshow_enviado = False
    db.commit()
    n3 = asyncio.run(agenda.revisar_videollamadas_noshow())
    avisos = [e for e in ENVIADOS if e["texto"] == agenda.MENSAJE_RESCATE and e["telefono"] == "5555556666"]
    check(n3 == 0 and len(avisos) == 1, "flag perdido dentro de las 24 h → notificaciones_enviadas evita el segundo envío")
    # "sí" tras el no-show reabre la agenda en la misma postulación
    db.expire_all()
    p5 = db.query(Postulacion).filter_by(codigo=P5).one()
    p5.candidato.postulacion_conversacion_id = p5.id
    db.commit()
    r = webhook_meta(client, "5215555556666", "Sí")
    db.expire_all()
    p5 = db.query(Postulacion).filter_by(codigo=P5).one()
    check(r.json().get("postulacion") == P5 and p5.videollamada_agendada_en is None and not p5.videollamada_aviso_noshow_enviado, "«Sí» al aviso de no-show → se reabre la agenda en la misma postulación")

    # ================= 3. Alta sin documentos =================
    print("\n--- 3. Alta como colaborador sin documentos ---")
    r = client.post("/candidatos", json={"nombre": "Alta Sindocs", "telefono": "5577778888", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P6 = r.json()["id"]
    r = client.patch(f"/candidatos/{P6}/etapa?forzar_prueba=true", json={"etapa": "Contratación"})
    check(r.status_code == 200 and r.json().get("expedienteId"), "mover a Contratación abre el expediente")
    EXP = r.json()["expedienteId"]
    # 2026-09-18 (Modo Prueba TOTAL): con Modo Prueba activo el alta ya no exige documentos; el bloqueo se
    # verifica con Modo Prueba apagado.
    cfg_demo = obtener(db)
    prueba_previa = cfg_demo.modo_prueba
    cfg_demo.modo_prueba = False
    db.commit()
    r = client.post(f"/contratacion/expedientes/{EXP}/alta?forzar_prueba=true", json={})
    check(r.status_code == 400 and "no tiene documentos adjuntos" in r.json()["detail"], f"alta sin documentos → 400 con Modo Prueba apagado: «{r.json().get('detail')}»")
    r = client.post(f"/contratacion/expedientes/{EXP}/alta", json={})
    check(r.status_code == 400, "alta sin documentos → 400 también sin forzar (Modo Prueba apagado)")
    cfg_demo.modo_prueba = prueba_previa
    db.commit()

    # ================= 4. Plantilla + documento por WhatsApp =================
    print("\n--- 4. Solicitud de documentos por plantilla y carga por WhatsApp ---")
    PLANTILLAS.clear()
    ENVIADOS.clear()
    r = client.post(f"/candidatos/{P6}/solicitar-documentos", json={"notificar": {"candidato_whatsapp": True, "candidato_correo": False}})
    check(r.status_code == 200, "Solicitar documentos desde Contratación → 200 (antes solo Onboarding)")
    res_wa = [x for x in r.json()["resultados"] if x["canal"] == "whatsapp"]
    check(res_wa and res_wa[0]["enviado"] and res_wa[0].get("plantilla") == "solicitud_documentos_rh", "WhatsApp al candidato salió con la plantilla solicitud_documentos_rh")
    check(PLANTILLAS and PLANTILLAS[-1]["parametros"][0] == "Alta" and "Identificación oficial" in PLANTILLAS[-1]["parametros"][1] and "/expediente/" in PLANTILLAS[-1]["parametros"][2],
          f"parámetros de la plantilla = nombre, documentos pendientes, liga: {PLANTILLAS[-1]['parametros']}")
    r = client.post(f"/contratacion/expedientes/{EXP}/recordatorio", json={"notificar": {"candidato_whatsapp": True, "candidato_correo": False}})
    check(r.status_code == 200 and any(x.get("plantilla") for x in r.json()["notificaciones"]), "«Enviar recordatorio» del expediente también usa la plantilla")
    # plantilla rota → cae a texto libre, nunca se pierde el aviso
    whatsapp.settings.meta_plantilla_documentos = "rota"
    r = client.post(f"/candidatos/{P6}/recordatorio-documentos", json={"notificar": {"candidato_whatsapp": True, "candidato_correo": False}})
    res_wa = [x for x in r.json()["resultados"] if x["canal"] == "whatsapp"]
    check(res_wa and res_wa[0]["enviado"] and "rota" in res_wa[0].get("motivo_fallback", ""), "plantilla rechazada por Meta → cae al texto libre con motivo_fallback")
    whatsapp.settings.meta_plantilla_documentos = "solicitud_documentos_rh"

    # el candidato manda su INE como documento por WhatsApp
    p6 = db.query(Postulacion).filter_by(codigo=P6).one()
    p6.candidato.wa_id = "5215577778888"
    p6.candidato.postulacion_conversacion_id = p6.id
    db.commit()
    ENVIADOS.clear()
    r = webhook_meta(client, "5215577778888", "mi INE", tipo="document", media={"id": "media-1", "mime_type": "application/pdf", "filename": "ine_karla.pdf"}, wamid="wamid.doc1")
    check(r.status_code == 200 and r.json().get("accion") == "documento_whatsapp" and r.json().get("documento") == "Identificación oficial", f"documento por WhatsApp con pie «mi INE» → adjuntado como Identificación oficial ({r.json().get('estado')})")
    db.expire_all()
    doc = db.query(Documento).filter_by(expediente_id=EXP, tipo="Identificación oficial").one()
    check(bool(doc.archivo) and os.path.isfile(doc.archivo) and doc.mime == "application/pdf" and doc.subido_en is not None, "el archivo quedó guardado en disco y ligado al Documento del expediente")
    check(ENVIADOS and "Recibí tu Identificación oficial" in ENVIADOS[-1]["texto"], "el candidato recibe confirmación con lo que falta")
    # imagen sin pie → primer obligatorio pendiente (CURP)
    r = webhook_meta(client, "5215577778888", "", tipo="image", media={"id": "media-2", "mime_type": "image/jpeg"}, wamid="wamid.img1")
    check(r.status_code == 200 and r.json().get("documento") == "CURP", "imagen sin pie de foto → primer obligatorio pendiente (CURP)")
    # comprobante de luz → Comprobante de domicilio por sinónimo
    r = webhook_meta(client, "5215577778888", "recibo de luz", tipo="image", media={"id": "media-3", "mime_type": "image/jpeg"}, wamid="wamid.img2")
    check(r.json().get("documento") == "Comprobante de domicilio", "«recibo de luz» → Comprobante de domicilio (sinónimos)")
    # descarga fallida → aviso al candidato, sin 500
    r = webhook_meta(client, "5215577778888", "", tipo="document", media={"id": "media-mala", "mime_type": "application/pdf"}, wamid="wamid.doc2")
    check(r.status_code == 200 and r.json().get("error") and "no pude descargarlo" in ENVIADOS[-1]["texto"], "descarga fallida de Meta → se le avisa al candidato, sin 500")
    # ahora sí hay documentos: el alta ya no se bloquea por «sin documentos» (sigue el gate de progreso).
    # 2026-09-18: el gate solo aplica con Modo Prueba apagado (Modo Prueba TOTAL lo omite).
    cfg_demo.modo_prueba = False
    db.commit()
    r = client.post(f"/contratacion/expedientes/{EXP}/alta", json={})
    check(r.status_code == 409 and "no tiene documentos adjuntos" not in r.json()["detail"], "con documentos adjuntos el alta pasa el nuevo gate (y sigue el de completitud/revisión: 409)")
    cfg_demo.modo_prueba = True
    db.commit()
    # adjunto fuera de Contratación/Onboarding → no se toca ningún expediente
    r = webhook_meta(client, TEL, "", tipo="image", media={"id": "media-9", "mime_type": "image/jpeg"}, wamid="wamid.img9")
    check(r.status_code == 200 and r.json().get("accion") == "adjunto_ignorado", "imagen en etapa Entrevista IA → se ignora con aviso (sin expediente)")

    # webhook con texto normal sigue funcionando (regresión)
    r = webhook_meta(client, "5215577778888", "¿ya recibieron todo?")
    check(r.status_code == 200 and r.json().get("postulacion") == P6, "texto normal en Contratación sigue enrutándose a la postulación")

print(f"\n🎉 Correcciones post-demo verificadas: {OK} comprobaciones OK.")
