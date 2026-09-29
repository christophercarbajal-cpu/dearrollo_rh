"""Verificación del bug de las 6 horas (2026-09-29).

1) `FechaUTC`: la base guarda UTC; al leer SIEMPRE regresa aware en UTC (SQLite pierde el offset); una fecha
   con otra zona se guarda convertida (no con sus números de reloj).
2) `iso()` manda «Z»; la API de candidatos expone la cita de las 7:29 (México) como 13:29Z.
3) Modificar (PATCH /evaluaciones/{codigo}) con la misma fecha/hora NO corre la cita (antes cada «Modificar» sumaba 6 h).
4) El correo muestra la hora de la organización.
5) `scripts/diagnostico_citas_desfasadas.py` detecta un desfase de +6 h en la bitácora y no escribe nada.
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_zona_horaria.py
"""

import contextlib
import io
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_tz_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "tz.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-tz"
os.environ["SEMBRAR_DEMO"] = "true"
os.environ.pop("ZONA_HORARIA", None)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import fechas  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Bitacora, Candidato, Cuenta, EntrevistaHumana, Evaluacion, Usuario, UsuarioCuenta, Vacante, registrar  # noqa: E402
from app.services import plantillas_correo  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta TZ", nombre_comercial="TZ RH", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta.id
        for p in c.postulaciones:
            p.cuenta_id = cuenta.id
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    check(fechas.TZ_ORG.key == "America/Mexico_City", "zona de la organización por defecto: America/Mexico_City")

    # ---------- 1. programar 7:29 (México) — Evaluaciones unificadas ----------
    persona = db.query(Candidato).filter_by(codigo="C-8801").one()
    p = persona.postulaciones_activas[-1]
    p.consentimiento = True
    db.commit()
    P = p.codigo
    cita = {"fecha": "2026-09-30", "hora": "07:29", "modalidad": "Teléfono"}
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={
        "tipo": "entrevista_humana", "forma": "asignada",
        "evaluador": {"tipo": "externo", "nombre": "Ana Externa", "correo": "ana@externa.mx"}, "cita": cita,
    })
    check(r.status_code == 201, f"programar entrevista 2026-09-30 07:29 → 201 ({r.status_code})")
    codigo = r.json()["evaluacion"]["codigo"]
    db.expire_all()
    ev = db.query(Evaluacion).filter_by(codigo=codigo).one()
    crudo = db.execute(text("select cita_fecha_hora from evaluaciones where id = :i"), {"i": ev.id}).scalar()
    check(str(crudo).startswith("2026-09-30 13:29:00"), f"la base guarda UTC: {crudo}")
    check(ev.cita_fecha_hora.tzinfo is not None and ev.cita_fecha_hora == datetime(2026, 9, 30, 13, 29, tzinfo=timezone.utc),
          "FechaUTC: al leer regresa aware en UTC (SQLite pierde el offset)")
    check(fechas.local(ev.cita_fecha_hora).strftime("%H:%M") == "07:29" and ev.cita_zona_horaria == "America/Mexico_City",
          "en la zona de la organización es 07:29 (y se guarda la zona)")

    # ---------- 2. la API manda «Z» ----------
    lista = client.get(f"/evaluaciones/postulaciones/{P}").json()
    fechas_api = [x["cita"]["fechaHora"] for x in lista if x.get("cita")]
    check(fechas_api and fechas_api[0] == "2026-09-30T13:29:00Z", f"GET /evaluaciones/postulaciones/{{codigo}} expone la cita como 13:29Z ({fechas_api[:1]})")
    check(fechas.iso(datetime(2026, 9, 30, 13, 29)) == "2026-09-30T13:29:00Z", "iso(): naive se toma como UTC y sale con «Z»")
    check(fechas.iso(datetime(2026, 9, 30, 7, 29, tzinfo=ZoneInfo("America/Mexico_City"))) == "2026-09-30T13:29:00Z", "iso(): aware se convierte a UTC")

    # ---------- 3. modificar con la misma hora no corre la cita ----------
    r = client.patch(f"/evaluaciones/{codigo}", json={"cita": cita})
    check(r.status_code == 200, f"modificar con la misma fecha/hora → 200 ({r.status_code})")
    db.expire_all()
    ev = db.get(Evaluacion, ev.id)
    check(ev.cita_fecha_hora == datetime(2026, 9, 30, 13, 29, tzinfo=timezone.utc), "modificar sin cambios deja la cita en 07:29 (antes +6 h)")

    # ---------- 1b. bind: una fecha aware con otra zona se guarda en UTC ----------
    ev.cita_fecha_hora = datetime(2026, 10, 2, 9, 0, tzinfo=ZoneInfo("America/Mexico_City"))
    db.commit()
    crudo = db.execute(text("select cita_fecha_hora from evaluaciones where id = :i"), {"i": ev.id}).scalar()
    check(str(crudo).startswith("2026-10-02 15:00:00"), f"bind: 09:00 México se guarda como 15:00 UTC ({crudo})")
    ev.cita_fecha_hora = datetime(2026, 9, 30, 13, 29, tzinfo=timezone.utc)
    db.commit()

    # ---------- 4. correo en hora de la organización ----------
    texto_fecha, texto_hora = plantillas_correo.fecha_hora_mx(ev.cita_fecha_hora)
    check("07:29" in texto_hora and texto_hora.endswith("(hora de Ciudad de México)"), f"correo: la hora sale en México con su zona ({texto_fecha} {texto_hora})")
    from app.services import notificaciones as notif  # noqa: E402

    check(notif._fecha_hora_legible_mx(ev.cita_fecha_hora).endswith("07:29 (hora de Ciudad de México)"), "WhatsApp: la hora lleva la etiqueta de zona")

    # ---------- 5. diagnóstico de solo lectura (citas históricas del modal viejo) ----------
    # dato legado tal como lo dejó el modelo anterior: la entrevista + su bitácora; la modificación guardó la hora UTC
    # como si fuera de México (+6 h)
    base = datetime(2026, 9, 30, 13, 29, tzinfo=timezone.utc)
    eh = EntrevistaHumana(candidato_id=p.candidato_id, postulacion_id=p.id, tipo="externo", entrevistador="Ana", fecha=base + timedelta(hours=6),
                          modalidad="Llamada", token="tok-tz-legado")
    db.add(eh)
    db.flush()
    registrar(db, "RH Prueba", "entrevista_humana_programada", "postulacion", P, {"fecha": base.isoformat(), "modalidad": "Llamada"})
    registrar(db, "RH Prueba", "entrevista_humana_modificada", "postulacion", P,
              {"fecha": (base + timedelta(hours=6)).isoformat(), "modalidad": "Llamada"})
    db.commit()
    antes = (db.query(Bitacora).count(), db.execute(text("select fecha from entrevistas_humanas where id = :i"), {"i": eh.id}).scalar())
    import runpy  # noqa: E402

    salida = io.StringIO()
    argv = sys.argv
    sys.argv = ["diagnostico_citas_desfasadas.py"]
    with contextlib.redirect_stdout(salida):
        runpy.run_path(str(RAIZ / "scripts" / "diagnostico_citas_desfasadas.py"), run_name="__main__")
    sys.argv = argv
    out = salida.getvalue()
    check("con desfase probable: 1" in out and P in out and "probable real: 30/09/2026 07:29" in out,
          "diagnóstico: detecta la modificación desfasada y sugiere 07:29")
    despues = (db.query(Bitacora).count(), db.execute(text("select fecha from entrevistas_humanas where id = :i"), {"i": eh.id}).scalar())
    check(antes == despues, "diagnóstico: solo lectura (ni bitácora ni citas cambian)")
    db.close()

print(f"\n{OK} verificaciones OK")
