"""Verificación de la Fase 7B (2026-09-12) — Microsoft Teams en Entrevista Humana — SIN credenciales
reales: primero el modo seguro (sin TEAMS_*), luego credenciales ficticias con Microsoft (login +
Graph) SIMULADO monkeypatcheando `httpx.AsyncClient` dentro de services/teams.py.

Uso (desde red-human-api/):
    .venv/Scripts/python.exe scripts/verificar_teams.py
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_teams_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "teams.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY",
          "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET", "TEAMS_REDIRECT_URI"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-teams"
os.environ["SEMBRAR_DEMO"] = "true"  # los scripts de verificación sí usan los datos de ejemplo
os.environ["APP_URL"] = "http://localhost:3000"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cuenta, Evaluacion, IntegracionTeams, NotificacionEnviada, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import teams  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


# ------------------------------------------------------------
# Microsoft simulado: registra cada llamada y responde según la ruta
# ------------------------------------------------------------
LLAMADAS = []
MODO = {"graph_falla": False, "token_falla": False}


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class ClienteFalso:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None, **kw):
        LLAMADAS.append(("POST", url, data))
        if MODO["token_falla"]:
            return _Resp(400, {"error": "invalid_grant", "error_description": "AADSTS70008: refresh token expirado"})
        return {"__token__": True} and _Resp(200, {
            "access_token": f"AT-{data.get('grant_type')}-{len(LLAMADAS)}", "refresh_token": f"RT-{len(LLAMADAS)}", "expires_in": 3600, "scope": teams.SCOPES,
        })

    async def request(self, metodo, url, headers=None, json=None, **kw):
        LLAMADAS.append((metodo, url, json))
        if MODO["graph_falla"]:
            return _Resp(500, {"error": {"code": "ServiceUnavailable", "message": "Graph caído (simulado)"}})
        if url.endswith("/me"):
            return _Resp(200, {"userPrincipalName": "rh@empresa.mx", "displayName": "RH Empresa"})
        if metodo == "POST" and url.endswith("/me/events"):
            return _Resp(201, {"id": "EVT-123", "webLink": "https://outlook.office.com/evt", "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/simulada"}})
        if metodo in ("PATCH", "DELETE") and "/me/events/" in url:
            return _Resp(204)
        return _Resp(404, {"error": {"code": "NotFound", "message": url}})


teams.httpx.AsyncClient = ClienteFalso  # type: ignore[attr-defined]

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta Teams", nombre_comercial="Reclutadora Teams", estado="Activa")
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

    persona = db.query(Candidato).filter_by(codigo="C-8801").one()
    persona.correo = "cand@correo.mx"
    p = persona.postulaciones_activas[-1]
    p.consentimiento = True
    db.commit()
    P = p.codigo
    # Evaluaciones unificadas (2026-09-29): la entrevista se agenda con «Agregar evaluación» (cita opcional)
    CITA = {"fecha": "2026-10-01", "hora": "10:00", "modalidad": "Videollamada"}

    def agendar(cita_extra=None, **extra):
        return client.post(f"/evaluaciones/postulaciones/{P}", json={
            "tipo": "entrevista_humana", "forma": "asignada", "evaluador": {"tipo": "interno", "usuario_id": admin.id},
            "cita": {**CITA, **(cita_extra or {})}, **extra,
        })

    # ---------- 1. Modo seguro: sin TEAMS_* ----------
    r = client.get("/integraciones/teams")
    check(r.status_code == 200 and r.json()["disponible"] is False and r.json()["conectado"] is False and r.json()["redirectUri"].endswith("/integraciones/teams/callback"),
          "sin credenciales: GET /integraciones/teams → disponible=false, conectado=false, redirectUri visible")
    r = client.post("/integraciones/teams/conectar")
    check(r.status_code == 409, "sin credenciales: conectar → 409 (no rompe)")
    r = client.get("/integraciones/teams/callback", params={"code": "x", "state": "y"}, follow_redirects=False)
    check(r.status_code == 302 and "teams=error" in r.headers["location"], "sin credenciales: callback redirige con teams=error")
    r = agendar()
    check(r.status_code == 400 and "liga" in r.json()["detail"], "sin Teams: Videollamada exige la liga manual (comportamiento de siempre)")
    r = agendar({"liga_videollamada": "https://meet.google.com/abc"})
    check(r.status_code == 201 and r.json()["evaluacion"]["cita"]["ligaVideollamada"] == "https://meet.google.com/abc" and r.json()["evaluacion"]["cita"]["porTeams"] is False,
          "sin Teams: liga manual se guarda tal cual (porTeams=false)")

    # ---------- 2. Credenciales ficticias (nombres EXACTOS de las variables) ----------
    settings.teams_client_id = "cid-123"
    settings.teams_tenant_id = "tenant-abc"
    settings.teams_client_secret = "secreto-super-largo-1"
    check(teams.teams_configurado(), "TEAMS_CLIENT_ID / TEAMS_TENANT_ID / TEAMS_CLIENT_SECRET → disponible")
    r = client.get("/integraciones/teams")
    check(r.json()["disponible"] is True and r.json()["conectado"] is False, "GET estado: disponible pero no conectado")
    r = client.post("/integraciones/teams/conectar")
    check(r.status_code == 200 and "login.microsoftonline.com/tenant-abc/oauth2/v2.0/authorize" in r.json()["url"] and "client_id=cid-123" in r.json()["url"]
          and "Calendars.ReadWrite" in r.json()["url"], "conectar → URL de autorización de Microsoft con tenant, client_id y scopes")
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(r.json()["url"]).query)
    state = q["state"][0]
    check(q["redirect_uri"][0].endswith("/integraciones/teams/callback"), "redirect_uri = host de la API + /integraciones/teams/callback")

    r = client.get("/integraciones/teams/callback", params={"code": "c1", "state": state + "x"}, follow_redirects=False)
    check("teams=error" in r.headers["location"] and "state" in r.headers["location"], "callback con state manipulado → error (firma HMAC)")
    r = client.get("/integraciones/teams/callback", params={"error": "access_denied", "error_description": "cancelado", "state": state}, follow_redirects=False)
    check("teams=error" in r.headers["location"], "callback con error de Microsoft → error legible")
    r = client.get("/integraciones/teams/callback", params={"code": "c1", "state": state}, follow_redirects=False)
    check(r.status_code == 302 and "teams=ok" in r.headers["location"] and "rh%40empresa.mx" in r.headers["location"], "callback válido → intercambia código, consulta /me y redirige con teams=ok")
    integ = db.query(IntegracionTeams).filter_by(cuenta_id=cuenta.id).one()
    check(integ.usuario_m365 == "rh@empresa.mx" and integ.nombre_m365 == "RH Empresa" and integ.conectado_por == admin.nombre, "fila por Cuenta con usuario M365 y quién conectó")
    check("AT-" not in integ.access_token_cifrado and "RT-" not in integ.refresh_token_cifrado and integ.access_token_cifrado.startswith("gAAAA"),
          "tokens NO legibles en la base (Fernet)")
    check(teams.descifrar(integ.access_token_cifrado).startswith("AT-authorization_code"), "los tokens se descifran con el secret")
    r = client.get("/integraciones/teams")
    j = r.json()
    check(j["conectado"] and j["usuarioM365"] == "rh@empresa.mx" and "token" not in json.dumps(j).lower(), "GET estado: conectado, sin exponer tokens")
    r = client.post("/integraciones/teams/probar")
    check(r.status_code == 200 and r.json()["ok"] and r.json()["usuarioM365"] == "rh@empresa.mx", "probar conexión → /me")

    # ---------- 3. Programar Videollamada con Teams ----------
    base = db.query(NotificacionEnviada).count()
    LLAMADAS.clear()
    r = agendar(notificar={"candidato_correo": True, "candidato_whatsapp": True, "entrevistador_correo": True, "entrevistador_whatsapp": False})
    check(r.status_code == 201, "programar Videollamada con Teams conectado, sin liga → 201")
    eh = r.json()["evaluacion"]["cita"]
    ev_db = db.query(Evaluacion).filter_by(codigo=r.json()["evaluacion"]["codigo"]).one()
    check(eh["ligaVideollamada"] == "https://teams.microsoft.com/l/meetup-join/simulada" and eh["porTeams"] and ev_db.teams_evento_id == "EVT-123",
          "liga de Teams guardada + teams_evento_id")
    evento = next(x for x in LLAMADAS if x[0] == "POST" and x[1].endswith("/me/events"))[2]
    correos = sorted(a["emailAddress"]["address"] for a in evento["attendees"])
    check(correos == sorted(["cand@correo.mx", admin.correo]) and evento["isOnlineMeeting"] and evento["onlineMeetingProvider"] == "teamsForBusiness",
          "evento de calendario con reunión de Teams e invitación a candidato y entrevistador")
    check(evento["start"]["dateTime"] == "2026-10-01T16:00:00" and evento["end"]["dateTime"] == "2026-10-01T17:00:00" and "Entrevista —" in evento["subject"],
          "hora de México → UTC (10:00 CDMX = 16:00Z), 60 min, asunto con puesto y candidato")
    env = db.query(NotificacionEnviada).filter(NotificacionEnviada.id > base).all()
    check(len(env) == 3, "confirmaciones enviadas tras crear la reunión")
    texto_wa = next(x for x in env if x.canal == "whatsapp")
    from app.models import Mensaje
    m = db.query(Mensaje).order_by(Mensaje.id.desc()).first()
    check("teams.microsoft.com/l/meetup-join/simulada" in m.texto, "la liga de Teams va en el WhatsApp de confirmación")
    from app.services.notificaciones import _mensaje
    from app.services.evaluaciones import CitaComoEntrevista
    asunto, html = _mensaje("entrevista_agendada", "candidato", "correo", p, CitaComoEntrevista(ev_db), "", {})
    check("teams.microsoft.com/l/meetup-join/simulada" in html, "la liga de Teams va en el correo de confirmación")

    # «Usar otra liga»
    r = agendar({"usar_teams": False, "liga_videollamada": "https://zoom.us/j/1"})
    check(r.status_code == 201 and r.json()["evaluacion"]["cita"]["ligaVideollamada"] == "https://zoom.us/j/1" and not r.json()["evaluacion"]["cita"]["porTeams"],
          "usar_teams=false → liga manual, sin llamar a Graph")
    r = agendar({"usar_teams": False})
    check(r.status_code == 400, "usar_teams=false sin liga → 400")
    n = len(LLAMADAS)
    r = agendar({"modalidad": "Presencial", "direccion": "Oficina"})
    check(r.status_code == 201 and len(LLAMADAS) == n, "Presencial no toca Teams")

    # ---------- 4. Falla de Graph → 502 y nada guardado ----------
    n_eh = db.query(Evaluacion).count()
    base = db.query(NotificacionEnviada).count()
    MODO["graph_falla"] = True
    r = agendar()
    MODO["graph_falla"] = False
    db.expire_all()
    check(r.status_code == 502 and "No se pudo crear la reunión de Teams" in r.json()["detail"] and "Usar otra liga" in r.json()["detail"], "Graph falla → 502 con motivo y sugerencia")
    check(db.query(Evaluacion).count() == n_eh and db.query(NotificacionEnviada).count() == base, "…y NO se guardó la entrevista ni se notificó nada")
    check(db.query(IntegracionTeams).filter_by(cuenta_id=cuenta.id).one().ultimo_error.startswith("ServiceUnavailable"), "el error queda en ultimo_error (Integraciones lo muestra)")

    # ---------- 5. Token vencido → refresh ----------
    integ = db.query(IntegracionTeams).filter_by(cuenta_id=cuenta.id).one()
    integ.expira_en = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.commit()
    LLAMADAS.clear()
    r = client.post("/integraciones/teams/probar")
    check(r.status_code == 200 and any(x[0] == "POST" and x[1].endswith("/token") and x[2]["grant_type"] == "refresh_token" for x in LLAMADAS), "token vencido → refresh automático antes de Graph")
    db.expire_all()
    integ = db.query(IntegracionTeams).filter_by(cuenta_id=cuenta.id).one()
    check(teams.descifrar(integ.access_token_cifrado).startswith("AT-refresh_token"), "el token renovado se guarda cifrado")
    integ.expira_en = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.commit()
    MODO["token_falla"] = True
    r = client.post("/integraciones/teams/probar")
    MODO["token_falla"] = False
    check(r.status_code == 502 and "reconectar" in r.json()["detail"], "refresh rechazado → 502 «reconectar»")
    integ = db.query(IntegracionTeams).filter_by(cuenta_id=cuenta.id).one()
    integ.expira_en = datetime.now(timezone.utc) + timedelta(hours=1)
    db.commit()

    # ---------- 6. Modificar / cancelar (best-effort) ----------
    r = agendar()
    check(r.status_code == 201 and r.json()["evaluacion"]["cita"]["porTeams"], "nueva evaluación por Teams")
    COD = r.json()["evaluacion"]["codigo"]
    LLAMADAS.clear()
    r = client.patch(f"/evaluaciones/{COD}", json={"cita": {"fecha": "2026-10-02", "hora": "12:00", "modalidad": "Videollamada"}})
    check(r.status_code == 200 and any(x[0] == "PATCH" and "/me/events/EVT-123" in x[1] for x in LLAMADAS) and r.json()["evaluacion"]["cita"]["ligaVideollamada"].endswith("simulada"),
          "modificar: actualiza la reunión de Teams y conserva la liga (no se vuelve a pedir)")
    MODO["graph_falla"] = True
    r = client.patch(f"/evaluaciones/{COD}", json={"cita": {"fecha": "2026-10-03", "hora": "12:00", "modalidad": "Videollamada"}})
    MODO["graph_falla"] = False
    check(r.status_code == 200 and r.json().get("avisoTeams"), "modificar con Graph caído → la acción de RH NO se bloquea, regresa avisoTeams")
    LLAMADAS.clear()
    r = client.post(f"/evaluaciones/{COD}/cancelar", json={})
    check(r.status_code == 200 and any(x[0] == "DELETE" and "/me/events/EVT-123" in x[1] for x in LLAMADAS), "cancelar: borra la reunión (Graph manda la cancelación)")

    # ---------- 7. Desconectar y secret rotado ----------
    r = client.delete("/integraciones/teams")
    check(r.status_code == 200 and db.query(IntegracionTeams).filter_by(cuenta_id=cuenta.id).count() == 0, "desconectar borra la conexión")
    r = agendar()
    check(r.status_code == 400, "desconectado → vuelve a pedir la liga manual")
    r = client.get("/integraciones/teams/callback", params={"code": "c2", "state": teams.firmar_state(cuenta.id, admin.id, admin.nombre)}, follow_redirects=False)
    check("teams=ok" in r.headers["location"], "reconectar")
    settings.teams_client_secret = "secreto-rotado-2"
    r = client.post("/integraciones/teams/probar")
    check(r.status_code == 502 and "reconectar" in r.json()["detail"], "secret rotado → los tokens no se descifran → «reconectar» (no truena)")
    settings.teams_client_secret = ""
    settings.teams_client_id = ""
    settings.teams_tenant_id = ""

    db.close()

print(f"\n🎉 Microsoft Teams (Fase 7B) verificado: {OK} comprobaciones OK.")
