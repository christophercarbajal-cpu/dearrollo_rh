"""Citas y avisos (prueba del 11 de octubre, Bloque 3 — cambios 8, 9 y 10). Base desechable, SIN red.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_citas_avisos.py

Cubre: C8 — si el entrevistador no ha abierto el bot de Telegram, RH ve «[Nombre] todavía no ha abierto el bot…» (nunca
el error técnico), la actividad sigue «Programada» y el aviso aparte queda «No enviado» con la liga y el mensaje completo
para copiar; si tiene correo, el aviso sale por correo. C10 — el candidato y el entrevistador reciben cada uno SU aviso
(la liga del evaluador solo va al entrevistador); el primer aviso dice «quedó programada» y el recordatorio automático no
repite «te recordamos» a quien acaba de recibir ese primer aviso. (C9 es solo de interfaz.)
"""

import asyncio
import itertools
import json
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

_dir = tempfile.mkdtemp(prefix="rh_citas_avisos_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "citas_avisos.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-citas-avisos"
os.environ["SEMBRAR_DEMO"] = "true"

from _ruta_prueba import fijar_ruta  # noqa: E402  (Cambio 36: ruta inicial de vacantes sembradas)
from fastapi.testclient import TestClient  # noqa: E402

import app.models as modelos  # noqa: E402
import app.services.correo as correo_srv  # noqa: E402
import app.services.notificaciones as notif  # noqa: E402
import app.services.whatsapp as wa  # noqa: E402
from app import fechas  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Evaluacion, EventoEvaluacion, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import telegram  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402
from app.services.recordatorios_entrevista import revisar_recordatorios_entrevista  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


ENVIOS = []
_ids = itertools.count(1)
SIN_BOT = {"5511110000", "5511110001"}  # entrevistadores que nunca abrieron el bot de Telegram


async def _wa(telefono, texto, *a, **k):
    ENVIOS.append(("whatsapp", telefono, texto))
    if telegram.telefono_10(telefono) in SIN_BOT:
        return {**telegram._sin_chat(telefono), "proveedor": "telegram"}
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "wa_id": f"wamid.C{next(_ids)}"}


async def _wa_boton(telefono, texto, boton, url, *a, **k):
    return await _wa(telefono, f"{texto} {url}")


async def _correo(destino, asunto, html, adjuntos=None, *a, **k):
    ENVIOS.append(("correo", destino, asunto))
    if not destino:
        return {"enviado": False, "proveedor": "resend", "detalle": "sin correo"}
    return {"enviado": True, "proveedor": "resend", "detalle": "ok"}


wa.enviar_mensaje = notif.enviar_mensaje = _wa
wa.enviar_con_boton = _wa_boton
correo_srv.enviar_correo = notif.enviar_correo = _correo

LIGA_VIDEO = "https://meet.ejemplo.mx/abc-defg"
CITA = {"fecha": "2026-12-15", "hora": "10:00", "modalidad": "Videollamada", "liga_videollamada": LIGA_VIDEO}

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    cuenta = Cuenta(nombre="Citas Avisos SA", nombre_comercial="Citas Avisos", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    obtener(db).modo_prueba = False
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).first()
    v.cuenta_id, v.cliente_id, v.proceso, v.responsable_id, v.titulo = cuenta.id, None, {}, admin.id, "Almacenista"
    db.commit()
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    PASOS = [{"id": "solicitud", "tipo": "solicitud_web", "etapa": "Prefiltro"},
             {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"}]
    assert fijar_ruta(db, v.codigo, json={"pasos": PASOS}).status_code == 200
    tel = iter(range(5588000000, 5588999999))

    def nueva(nombre):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": str(next(tel)), "correo": f"{nombre.split()[0].lower()}@avisos.mx",
                                             "vacante": v.codigo, "consentimiento": True, "fuente": "RH"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def post(codigo):
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=codigo).one()

    def agregar(codigo, evaluador, cita):
        """«Iniciar» la entrevista humana de la ruta con su entrevistador y su cita."""
        r = client.post(f"/procesos/postulaciones/{codigo}/pasos/eh/iniciar", json={"evaluador": evaluador, "cita": cita})
        r.json().setdefault("paso", {"id": "eh"})
        return r

    def paso_de(codigo, pid):
        s = client.get(f"/procesos/postulaciones/{codigo}").json()
        return next(x for e in s["etapas"] for x in e["pasos"] if x["id"] == pid)

    def al(t, canal="whatsapp"):
        return [x for c, d, x in ENVIOS if c == canal and telegram.telefono_10(d or "") == telegram.telefono_10(t)] if canal == "whatsapp" \
            else [x for c, d, x in ENVIOS if c == canal and d == t]

    # ================= C8. El entrevistador no ha abierto el bot =================
    print("\n--- C8. «[Nombre] todavía no ha abierto el bot…», actividad Programada y aviso No enviado ---")
    P = nueva("Ana Avisos")
    tel_p = post(P).telefono
    ENVIOS.clear()
    LAURA = {"tipo": "externo", "nombre": "Laura Externa", "whatsapp": "5511110000"}
    r = agregar(P, LAURA, CITA)
    check(r.status_code == 200 and r.json()["iniciada"], f"entrevista humana con cita: se inicia ({r.status_code} {r.text[:120]})")
    pid = "eh"
    ev = db.query(Evaluacion).filter(Evaluacion.postulacion_id == post(P).id, Evaluacion.paso_id == pid).one()
    res = r.json().get("notificaciones") or r.json().get("envios") or []
    x = paso_de(P, pid)
    check(x["estadoUnificado"] != "error" and x["estadoUnificadoTexto"].startswith("Programada") and not x.get("error"),
          f"la ACTIVIDAD sigue «{x['estadoUnificadoTexto']}» (no «Error»)")
    aviso = x.get("aviso") or {}
    check(aviso.get("texto") == "No enviado" and aviso.get("aQuien") == "al entrevistador", "el AVISO va aparte: «No enviado»")
    check(aviso.get("motivo", "").startswith("Laura Externa todavía no ha abierto el bot") and "Telegram no permite" not in aviso.get("motivo", ""),
          f"mensaje claro en lugar del error técnico: {aviso.get('motivo', '')[:90]}")
    check("/evaluacion/" in aviso.get("liga", ""), "«Copiar liga» = la liga del entrevistador")
    check("Ana Avisos" in aviso.get("mensaje", "") and aviso.get("liga", "") in aviso.get("mensaje", "") and LIGA_VIDEO in aviso.get("mensaje", ""),
          "«Copiar mensaje completo» = el mismo aviso que se le habría mandado (candidato, cita y su liga)")
    eventos = db.query(EventoEvaluacion).filter(EventoEvaluacion.evaluacion_id == ev.id, EventoEvaluacion.accion == "envio").all()
    detalles = [y.get("detalle", "") for e in eventos for y in (e.detalle or {}).get("envios") or [] if y.get("destinatario") == "entrevistador"]
    check(any("todavía no ha abierto el bot" in d for d in detalles) and not any("no ha iniciado conversación" in d for d in detalles),
          "el historial y las advertencias a RH guardan el texto claro (nunca el técnico)")

    # ================= C10. Cada quien su aviso; «quedó programada» =================
    print("\n--- C10. Avisos separados y «quedó programada» ---")
    a_cand = al(tel_p)
    check(len(a_cand) == 1 and "quedó programada" in a_cand[0] and "te recordamos" not in a_cand[0].lower(),
          f"el candidato recibe SU aviso: «…quedó programada…» ({a_cand[0][:80] if a_cand else '—'})")
    check("/evaluacion/" not in a_cand[0] and LIGA_VIDEO in a_cand[0], "…con la liga de la videollamada y SIN la liga del entrevistador")
    a_ent = al("5511110000")
    check(len(a_ent) == 1 and "/evaluacion/" in a_ent[0] and "Ana Avisos" in a_ent[0], "el entrevistador tiene SU aviso con su liga")

    # ================= C8. Con correo: el aviso sale por correo =================
    print("\n--- C8. Respaldo por correo cuando la mensajería no le llega ---")
    base = dict(modelos.REGLAS_NOTIFICACION_DEFAULT["evaluacion_asignada"])
    modelos.REGLAS_NOTIFICACION_DEFAULT["evaluacion_asignada"] = {**base, "entrevistador_correo": False}  # la regla solo pide mensajería
    notif.REGLAS_NOTIFICACION_DEFAULT = modelos.REGLAS_NOTIFICACION_DEFAULT
    Q = nueva("Beto Avisos")
    ENVIOS.clear()
    MARIO = {"tipo": "externo", "nombre": "Mario Externo", "whatsapp": "5511110001", "correo": "mario@externo.mx"}
    r = agregar(Q, MARIO, {**CITA, "hora": "11:00"})
    check(r.status_code == 200, "segunda cita (entrevistador con correo)")
    check(al("5511110001") and al("mario@externo.mx", "correo"), "Telegram no le llegó → el MISMO aviso le sale por correo")
    x = paso_de(Q, "eh")
    check(not x.get("aviso") and x["estadoUnificadoTexto"].startswith("Programada"), "con el correo entregado el aviso ya no queda «No enviado»")
    modelos.REGLAS_NOTIFICACION_DEFAULT["evaluacion_asignada"] = base

    # ================= C10. Recordatorio automático sin duplicar el primer aviso =================
    print("\n--- C10. El recordatorio no repite «te recordamos» tras el primer aviso ---")
    R = nueva("Carla Avisos")
    tel_r = post(R).telefono
    pronto = fechas.local(datetime.now(timezone.utc) + timedelta(hours=20))
    ENVIOS.clear()
    r = agregar(R, {"tipo": "interno", "usuario_id": admin.id},
                {**CITA, "fecha": pronto.strftime("%Y-%m-%d"), "hora": pronto.strftime("%H:%M")})
    check(r.status_code == 200 and r.json()["iniciada"], "cita para dentro de ~20 h (cae en la ventana del recordatorio)")
    ev_r = db.query(Evaluacion).filter(Evaluacion.postulacion_id == post(R).id, Evaluacion.paso_id == "eh").one()
    check(len(al(tel_r)) == 1 and "quedó programada" in al(tel_r)[0], "primer aviso: «quedó programada»")
    ENVIOS.clear()
    asyncio.run(revisar_recordatorios_entrevista())
    check(not [t for t in al(tel_r) if "recordamos" in t.lower()], "el job NO le manda «te recordamos» minutos después del primer aviso")
    # el primer aviso salió hace días (fuera de la ventana): ahí sí toca el recordatorio
    db.expire_all()
    for e in db.query(EventoEvaluacion).filter(EventoEvaluacion.evaluacion_id == ev_r.id, EventoEvaluacion.accion == "envio").all():
        e.fecha = datetime.now(timezone.utc) - timedelta(days=3)
    db.get(Evaluacion, ev_r.id).recordatorio_enviado_en = None
    db.commit()
    ENVIOS.clear()
    asyncio.run(revisar_recordatorios_entrevista())
    check(any("recordamos" in t.lower() for t in al(tel_r)), "con el primer aviso de hace días, el recordatorio sí sale")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — citas y avisos (C8, C10)")
