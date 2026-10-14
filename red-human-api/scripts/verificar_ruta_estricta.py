"""Reglas de ruta, limpieza de la ficha y un solo aviso por cita (2026-10-11, Bloque 4). Base desechable, SIN red.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_ruta_estricta.py

Cubre: el agente revisa la ruta antes de cada paso (nunca agenda videollamada en una ruta masiva, no agenda si lo
anterior no está aprobado, con una «No aprobada» deja de enviar mensajes y nunca retrocede de etapa); la ficha muestra
el MISMO estatus del prefiltro que la ruta, oculta el score de CV en rutas sin CV y no muestra alertas de
escolaridad/documentos de la entrevista; un solo aviso por cita al entrevistador (también frente al recordatorio
automático); y la limpieza de la videollamada creada por error (simulación + aplicar, sin borrar nada).
"""

import asyncio
import itertools
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
_dir = _temporal.directorio("rh_ruta_estricta_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "ruta.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-ruta-estricta"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

import app.services.correo as correo_srv  # noqa: E402
import app.services.notificaciones as notif  # noqa: E402
import app.services.whatsapp as wa  # noqa: E402
from app import fechas  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Entrevista, Evaluacion, NotificacionEnviada, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers import candidatos as rc  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.serial import estado_prefiltro, postulacion_dict  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import recordatorios_entrevista  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

sys.path.insert(0, str(RAIZ / "scripts"))
import cancelar_videollamada_por_error as limpieza  # noqa: E402

OK = 0
ENVIOS = []
_ids = itertools.count(1)


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _wa(telefono, texto, *a, **k):
    ENVIOS.append(("whatsapp", telefono, texto))
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "wa_id": f"wamid.R{next(_ids)}"}


async def _correo(destino, asunto, html, adjuntos=None, *a, **k):
    ENVIOS.append(("correo", destino, asunto))
    return {"enviado": True, "proveedor": "resend", "detalle": "ok"}


wa.enviar_mensaje = notif.enviar_mensaje = rc.enviar_mensaje = _wa
correo_srv.enviar_correo = notif.enviar_correo = _correo

MASIVA = [{"id": "pwa", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro", "obligatorio": True},
          {"id": "ewa", "tipo": "entrevista_whatsapp", "etapa": "Entrevista IA", "obligatorio": True},
          {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana", "obligatorio": True}]
CORPORATIVA = [{"id": "sw", "tipo": "solicitud_web", "etapa": "Prefiltro"},
               {"id": "cv", "tipo": "analisis_cv", "etapa": "Prefiltro", "obligatorio": True},
               {"id": "ea", "tipo": "entrevista_agente", "etapa": "Entrevista IA", "obligatorio": True},
               {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana", "obligatorio": True}]

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    obtener(db).recordatorio_entrevista_horas = 24
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    admin.telefono = admin.telefono or "5599990000"
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    cuenta = Cuenta(nombre="Ruta Estricta", nombre_comercial="Ruta Estricta", estado="Activa", slug="ruta-estricta")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    vm, vc = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).limit(2).all()
    for v, ruta in ((vm, MASIVA), (vc, CORPORATIVA)):
        v.cuenta_id, v.cliente_id, v.responsable_id = cuenta.id, None, admin.id
        v.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": ruta})
    db.commit()
    tel = iter(range(5543330001, 5543339999))

    def nueva(nombre, v):
        c = _crear_candidato(db, cuenta.id, nombre, "WhatsApp", False, telefono=str(next(tel)))
        p = crear_postulacion(db, c, db.get(Vacante, v.id), cuenta.id, "whatsapp", consentimiento=True)
        db.commit()
        return p

    def chat(p, texto):
        historial = [{"rol": "user", "texto": texto}]
        return asyncio.run(rc.procesar_prefiltro(db, db.query(Postulacion).filter_by(codigo=p.codigo).one(), texto, "whatsapp")) \
            if hasattr(rc, "procesar_prefiltro") else None

    # ================= 1. Nunca agenda videollamada en la ruta masiva =================
    print("\n--- 1. Ruta masiva: nunca se agenda videollamada ---")
    p = nueva("Prueba 5", vm)
    p.prefiltro_completo, p.estado, p.etapa = True, "cumple", "Entrevista IA"
    p.analisis = {**(p.analisis or {}), "prefiltro_web": {"resultado": "cumple", "en": datetime.now(timezone.utc).isoformat()}}
    db.commit()
    puede, motivo = sproc.agente_puede_actuar(p, "entrevista_agente")
    check(not puede and "no incluye" in motivo, "la ruta masiva no incluye la Entrevista con avatar → el agente no puede agendarla")
    ENVIOS.clear()
    for t in ("gracias", "sí", "hoy a las 4"):
        asyncio.run(rc.procesar_prefiltro(db, db.query(Postulacion).filter_by(codigo=p.codigo).one(), t, "whatsapp"))
    db.expire_all()
    p = db.query(Postulacion).filter_by(codigo=p.codigo).one()
    textos = " ".join(x[2] for x in ENVIOS if x[1] == p.telefono)
    check(p.videollamada_agendada_en is None and "videollamada" not in textos.lower() and "¿Te confirmo" not in textos,
          "aunque escriba «sí» u ofrezca horario, no se propone ni registra ninguna videollamada")
    r = asyncio.run(rc._procesar_turno_agenda(db, p, [{"rol": "user", "texto": "sí"}], "whatsapp"))
    db.expire_all()
    check(r.get("detenido") and db.query(Postulacion).filter_by(codigo=p.codigo).one().videollamada_agendada_en is None,
          "ni aunque se llegue a la herramienta de agenda: la guarda de la ruta la detiene")
    envio = asyncio.run(rc._avisar_apto_e_iniciar_agenda(db, p))
    check(envio["enviado"] is False and envio["texto"] == "", "la invitación a agendar tampoco sale")

    # ================= 2. Solo avanza si lo anterior está aprobado =================
    print("\n--- 2. Ruta con avatar: espera a que lo anterior esté aprobado ---")
    q = nueva("Carla Corporativa", vc)
    q.prefiltro_completo, q.estado = True, "cumple"
    db.commit()
    puede, motivo = sproc.agente_puede_actuar(q, "entrevista_agente")
    check(not puede, f"Análisis de CV sin hacer → no se agenda la entrevista ({motivo})")
    q.score = 85
    q.analisis = {**(q.analisis or {}), "requisitos_cumplidos": ["Experiencia"], "ia": True}
    q.etapa = "Entrevista IA"
    db.commit()
    db.expire_all()
    q = db.query(Postulacion).filter_by(codigo=q.codigo).one()
    puede, motivo = sproc.agente_puede_actuar(q, "entrevista_agente")
    check(puede, f"con el Análisis de CV cumplido, ya se puede agendar ({motivo})")

    # ================= 3. «No aprobada» → deja de enviar mensajes =================
    print("\n--- 3. «No aprobada»: el agente deja de enviar mensajes ---")
    # Cambio 22: el Análisis de CV ya NO detiene la ruta (sin mínimo); la «No aprobada» sale de la Entrevista Red Human
    q.score = 20
    db.commit()
    db.expire_all()
    q = db.query(Postulacion).filter_by(codigo=q.codigo).one()
    check(sproc.detenido_por_no_aprobada(q) is None, "Cambio 22: un CV con score bajo ya no detiene la ruta")
    db.add(Entrevista(codigo="ENT-NA1", candidato_id=q.candidato_id, postulacion_id=q.id, token="tok-no-aprobada", tipo="avatar",
                      estado="evaluada", evaluacion={"score_entrevista": 20, "match_perfil": 20, "recomendacion": "no_avanzar"}))
    db.commit()
    db.expire_all()
    q = db.query(Postulacion).filter_by(codigo=q.codigo).one()
    check(sproc.detenido_por_no_aprobada(q) is not None, "la Entrevista Red Human bajo el mínimo detiene la ruta")
    ENVIOS.clear()
    r = asyncio.run(rc.procesar_prefiltro(db, q, "hola, ¿cuándo es mi entrevista?", "whatsapp"))
    check(r.get("detenido") and not [x for x in ENVIOS if x[1] == q.telefono], "no se le responde ni se agenda nada")
    check(not sproc.agente_puede_actuar(q, "entrevista_agente")[0], "y la guarda lo impide en cualquier paso")

    # ================= 4. Ficha: mismo estatus del prefiltro, CV y escolaridad =================
    print("\n--- 4. Ficha ---")
    p = db.query(Postulacion).filter_by(codigo=p.codigo).one()
    p.analisis = {**p.analisis, "respuestas_prefiltro": [
        {"criterio": "Secundaria terminada", "pregunta": "¿Terminaste la secundaria?", "respuesta": "Sí", "cumple": True},
        {"criterio": "Inglés básico (deseable)", "pregunta": "¿Hablas inglés?", "respuesta": "No", "cumple": False}]}
    db.commit()
    d = postulacion_dict(p, detalle=True)
    paso_pf = next(x for x in sproc.estado_pasos(p) if x["id"] == "pwa")
    check(estado_prefiltro(p) == "cumple" and d["prefiltroResumen"]["resultado"] == "cumple" and paso_pf["resultado"] == "favorable",
          "resumen y ruta muestran el MISMO estatus (un deseable en «No» ya no lo vuelve «No cumple»)")
    check(d["pideCv"] is False and postulacion_dict(q)["pideCv"] is True, "rutas sin CV marcan pideCv=false (la ficha oculta el score de CV)")
    e = Entrevista(codigo="ENT-T1", candidato_id=p.candidato_id, postulacion_id=p.id, token="tok-escolar", tipo="whatsapp", estado="evaluada",
                   evaluacion={"riesgos": ["Validar certificado de secundaria", "Alerta: Traslado de más de 90 minutos"], "fortalezas": ["Puntual"],
                               "faltante": ["Comprobante de domicilio"], "match_perfil": 70})
    db.add(e)
    db.commit()
    db.refresh(p)
    d = postulacion_dict(p, detalle=True)
    puntos = " | ".join(d.get("puntosPorValidar") or (d.get("detalle") or {}).get("puntosPorValidar") or [])
    check("certificado" not in puntos.lower() and "comprobante" not in puntos.lower() and "90 minutos" in puntos,
          "sin alertas de escolaridad/documentos en los puntos por validar (las demás se conservan)")

    # ================= 5. Un solo aviso por cita al entrevistador =================
    print("\n--- 5. Un solo aviso por cita al entrevistador ---")
    r_ = nueva("Rita Cita", vc)
    db.query(NotificacionEnviada).delete()
    db.commit()
    manana = fechas.local(datetime.now(timezone.utc) + timedelta(hours=20))
    r = client.post(f"/evaluaciones/postulaciones/{r_.codigo}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "evaluador": {"tipo": "interno", "usuario_id": admin.id},
        "cita": {"fecha": manana.strftime("%Y-%m-%d"), "hora": manana.strftime("%H:%M"), "modalidad": "Presencial", "direccion": "Av. Uno 1"}})
    check(r.status_code == 201, "cita para dentro de 20 h (dentro de la ventana del recordatorio)")
    cod = r.json()["evaluacion"]["codigo"]

    def avisos_entrevistador():
        db.expire_all()
        return db.query(NotificacionEnviada).filter(NotificacionEnviada.destinatario_tipo == "entrevistador", NotificacionEnviada.enviado.is_(True)).count()

    base = avisos_entrevistador()
    check(base >= 1, "el entrevistador recibió su aviso de la cita")
    asyncio.run(recordatorios_entrevista.revisar_recordatorios_entrevista())
    check(avisos_entrevistador() == base, "el recordatorio automático NO se lo repite (su aviso ya salió dentro de la ventana)")
    ev = db.query(Evaluacion).filter_by(codigo=cod).one()
    from app.services import evaluaciones as sev

    asyncio.run(sev.notificar(db, ev, db.get(Postulacion, ev.postulacion_id), "evaluacion_asignada", "prueba"))
    db.commit()
    check(avisos_entrevistador() == base, "volver a disparar la asignación de la MISMA cita no le llega otra vez")
    client.post(f"/evaluaciones/{cod}/reenviar-liga")
    check(avisos_entrevistador() > base, "«Reenviar liga» (acción explícita de RH) sí le llega")
    base = avisos_entrevistador()
    nueva_hora = fechas.local(datetime.now(timezone.utc) + timedelta(days=3))
    client.patch(f"/evaluaciones/{cod}", json={"cita": {"fecha": nueva_hora.strftime("%Y-%m-%d"), "hora": nueva_hora.strftime("%H:%M"),
                                                       "modalidad": "Presencial", "direccion": "Av. Uno 1"}})
    check(avisos_entrevistador() > base, "si la cita cambia, sí recibe UN aviso de la nueva")

    # ================= 6. Limpieza de la videollamada creada por error =================
    print("\n--- 6. Limpieza: videollamada de «Prueba 5» del 12 de octubre ---")
    p = db.query(Postulacion).filter_by(codigo=p.codigo).one()
    cuando = fechas.desde_local("2026-10-12", "16:00")
    p.videollamada_agendada_en, p.videollamada_liga = cuando, "https://app/entrevista/abc"
    ent = Entrevista(codigo="ENT-T2", candidato_id=p.candidato_id, postulacion_id=p.id, token="tok-video", tipo="avatar", estado="programada",
                     programada_para=cuando)
    db.add(ent)
    otro = nueva("Prueba 50", vm)
    otro.videollamada_agendada_en = cuando
    db.commit()
    items = limpieza.plan(db, "Prueba 5", fechas.local(cuando).date())
    check({i["tipo"] for i in items} == {"videollamada_chat", "entrevista_avatar"} and all(i["postulacion"].id == p.id for i in items),
          "la simulación encuentra SOLO la videollamada de «Prueba 5» (no la de «Prueba 50»)")
    ENVIOS.clear()
    n = asyncio.run(limpieza.aplicar(db, items))
    db.expire_all()
    p = db.query(Postulacion).filter_by(codigo=p.codigo).one()
    check(n == 2 and p.videollamada_agendada_en is None and (p.analisis.get("videollamadas_anteriores") or [{}])[-1].get("cancelada_por_error"),
          "se cancela y queda en el historial de videollamadas (nada se borra)")
    check(db.query(Entrevista).filter_by(codigo="ENT-T2").one().estado == "cancelada", "la entrevista de la sala queda «cancelada»")
    r = client.post("/entrevistas/publica/tok-video/consentimiento", json={"acepta": True})
    check(r.status_code == 409, "la liga de la sala ya no se puede usar")
    check(not ENVIOS and db.get(Postulacion, otro.id).videollamada_agendada_en is not None, "sin avisos y sin tocar a otros candidatos")
    check(limpieza.plan(db, "Prueba 5", fechas.local(cuando).date()) == [], "idempotente: correrlo otra vez no encuentra nada")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — ruta estricta, ficha, aviso único y limpieza")
