"""Microsoft Teams / Microsoft 365 (Fase 7B) — reunión de Teams + invitación de calendario al
programar una Entrevista Humana en modalidad Videollamada.

Patrón: OAuth 2.0 con permisos DELEGADOS (un usuario de Microsoft 365 conecta su cuenta desde
Configuración → Integraciones; la reunión y las invitaciones salen de SU calendario). La conexión
es POR CUENTA de Red Human (`IntegracionTeams`, una fila por Cuenta).

Variables de entorno (nombres exactos, solo en el servidor): TEAMS_CLIENT_ID, TEAMS_TENANT_ID,
TEAMS_CLIENT_SECRET. Opcional TEAMS_REDIRECT_URI (si falta, se arma con el host real de la API).
Sin las 3 → `teams_configurado()` es False y todo cae al modo de siempre (liga manual), igual que
RESEND_API_KEY o ANAM_API_KEY: nunca rompe el flujo.

Tokens: cifrados en la base con Fernet (AES) usando una clave derivada de TEAMS_CLIENT_SECRET
(SHA-256). Si el secret se rota, las conexiones existentes dejan de poder descifrarse y hay que
reconectar (TeamsError("reconectar")).
"""

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import IntegracionTeams

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read Calendars.ReadWrite"
ZONA_MEXICO = settings.zona_horaria or "America/Mexico_City"
DURACION_REUNION_MIN = 60
MARGEN_REFRESH_SEG = 120
CALLBACK_PATH = "/integraciones/teams/callback"
TIMEOUT = 20


class TeamsError(Exception):
    """Cualquier falla de la integración, con un detalle legible para RH."""


def teams_configurado() -> bool:
    return bool(settings.teams_client_id and settings.teams_tenant_id and settings.teams_client_secret)


def _login_base() -> str:
    return f"https://login.microsoftonline.com/{settings.teams_tenant_id}/oauth2/v2.0"


def redirect_uri(base_url: str = "") -> str:
    """TEAMS_REDIRECT_URI si está; si no, el host real de la API (lo que hay que registrar en Azure)."""
    if settings.teams_redirect_uri:
        return settings.teams_redirect_uri
    return (base_url or settings.app_url).rstrip("/") + CALLBACK_PATH


# ------------------------------------------------------------
# Cifrado de tokens (Fernet con clave derivada del secret)
# ------------------------------------------------------------


def _fernet():
    from cryptography.fernet import Fernet  # dependencia nueva de la Fase 7B (requirements.txt)

    if not settings.teams_client_secret:
        raise TeamsError("Microsoft Teams no está configurado en este servidor.")
    clave = base64.urlsafe_b64encode(hashlib.sha256(settings.teams_client_secret.encode()).digest())
    return Fernet(clave)


def cifrar(texto: str) -> str:
    return _fernet().encrypt((texto or "").encode()).decode()


def descifrar(cifrado: str) -> str:
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt((cifrado or "").encode()).decode()
    except InvalidToken:
        raise TeamsError("reconectar: la conexión se guardó con otro TEAMS_CLIENT_SECRET; vuelve a conectar la cuenta.")


# ------------------------------------------------------------
# state firmado del flujo OAuth (HMAC con el secret; 10 minutos)
# ------------------------------------------------------------


def firmar_state(cuenta_id: int, usuario_id: int, usuario_nombre: str) -> str:
    cuerpo = json.dumps({"c": cuenta_id, "u": usuario_id, "n": usuario_nombre[:80], "t": int(time.time())}, separators=(",", ":"))
    b = base64.urlsafe_b64encode(cuerpo.encode()).decode().rstrip("=")
    firma = hmac.new(settings.teams_client_secret.encode(), b.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{b}.{firma}"


def leer_state(state: str) -> dict:
    try:
        b, firma = state.split(".", 1)
    except ValueError:
        raise TeamsError("state inválido")
    esperada = hmac.new(settings.teams_client_secret.encode(), b.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(esperada, firma):
        raise TeamsError("state inválido (firma)")
    datos = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode())
    if time.time() - int(datos.get("t", 0)) > 600:
        raise TeamsError("la autorización tardó más de 10 minutos; vuelve a intentar")
    return datos


# ------------------------------------------------------------
# OAuth: URL de autorización, intercambio de código, refresh
# ------------------------------------------------------------


def url_autorizacion(state: str, uri: str) -> str:
    q = {
        "client_id": settings.teams_client_id,
        "response_type": "code",
        "redirect_uri": uri,
        "response_mode": "query",
        "scope": SCOPES,
        "state": state,
        "prompt": "select_account",
    }
    return f"{_login_base()}/authorize?{urlencode(q)}"


def _error_de(r: httpx.Response) -> str:
    try:
        j = r.json() or {}
    except Exception:
        return f"HTTP {r.status_code}: {r.text[:200]}"
    err = j.get("error")
    if isinstance(err, dict):
        return f"{err.get('code', r.status_code)}: {err.get('message', '')[:200]}"
    return f"{err or r.status_code}: {j.get('error_description', r.text[:200])[:200]}"


async def _post_token(datos: dict) -> dict:
    cuerpo = {"client_id": settings.teams_client_id, "client_secret": settings.teams_client_secret, "scope": SCOPES, **datos}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
            r = await cli.post(f"{_login_base()}/token", data=cuerpo)
    except Exception as e:
        raise TeamsError(f"error de red con Microsoft: {e}")
    if r.status_code >= 300:
        raise TeamsError(_error_de(r))
    return r.json()


async def intercambiar_codigo(code: str, uri: str) -> dict:
    return await _post_token({"grant_type": "authorization_code", "code": code, "redirect_uri": uri})


async def refrescar(refresh_token: str) -> dict:
    return await _post_token({"grant_type": "refresh_token", "refresh_token": refresh_token})


def guardar_tokens(integ: IntegracionTeams, tokens: dict) -> None:
    integ.access_token_cifrado = cifrar(tokens["access_token"])
    if tokens.get("refresh_token"):
        integ.refresh_token_cifrado = cifrar(tokens["refresh_token"])
    integ.expira_en = datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    integ.scopes = tokens.get("scope", SCOPES)
    integ.ultimo_error = ""


async def token_vigente(db: Session, integ: IntegracionTeams) -> str:
    """Access token listo para usar; refresca (y guarda) si está por vencer."""
    expira = integ.expira_en
    if expira is not None and expira.tzinfo is None:
        expira = expira.replace(tzinfo=timezone.utc)  # SQLite pierde el tzinfo
    if expira is None or (expira - datetime.now(timezone.utc)).total_seconds() < MARGEN_REFRESH_SEG:
        refresh = descifrar(integ.refresh_token_cifrado)
        if not refresh:
            raise TeamsError("reconectar: la conexión no tiene refresh token.")
        try:
            tokens = await refrescar(refresh)
        except TeamsError as e:
            integ.ultimo_error = str(e)[:300]
            db.flush()
            raise TeamsError(f"reconectar: no se pudo renovar el acceso ({e}).")
        guardar_tokens(integ, tokens)
        db.flush()
    return descifrar(integ.access_token_cifrado)


# ------------------------------------------------------------
# Graph
# ------------------------------------------------------------


async def _graph(metodo: str, ruta: str, token: str, json_body: Optional[dict] = None) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
            r = await cli.request(
                metodo, f"{GRAPH}{ruta}", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=json_body,
            )
    except Exception as e:
        raise TeamsError(f"error de red con Microsoft Graph: {e}")
    if r.status_code >= 300:
        raise TeamsError(_error_de(r))
    if r.status_code == 204 or not r.content:
        return None
    return r.json()


async def perfil(token: str) -> dict:
    """GET /me → {upn, nombre}."""
    me = await _graph("GET", "/me", token) or {}
    return {"upn": me.get("userPrincipalName") or me.get("mail") or "", "nombre": me.get("displayName") or ""}


def _fecha_graph(dt: datetime) -> dict:
    """Graph acepta la hora local con su zona; guardamos UTC y la mandamos como UTC explícito."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return {"dateTime": dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"}


def _asistentes(asistentes: List[dict]) -> list:
    salida = []
    for a in asistentes:
        correo = (a.get("correo") or "").strip()
        if not correo:
            continue  # sin correo no hay a quién invitar (el candidato puede no tener)
        salida.append({"emailAddress": {"address": correo, "name": a.get("nombre") or correo}, "type": "required"})
    return salida


async def crear_reunion(
    db: Session, integ: IntegracionTeams, *, asunto: str, inicio: datetime, asistentes: List[dict], cuerpo: str = "",
    duracion_min: int = DURACION_REUNION_MIN,
) -> dict:
    """Crea el evento en el calendario del usuario conectado con reunión de Teams. Graph manda la
    invitación de calendario a cada asistente. Regresa {evento_id, liga, web_link}."""
    token = await token_vigente(db, integ)
    fin = inicio + timedelta(minutes=duracion_min)
    evento = {
        "subject": asunto,
        "body": {"contentType": "text", "content": cuerpo or asunto},
        "start": _fecha_graph(inicio),
        "end": _fecha_graph(fin),
        "attendees": _asistentes(asistentes),
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness",
        "allowNewTimeProposals": False,
    }
    creado = await _graph("POST", "/me/events", token, evento) or {}
    liga = ((creado.get("onlineMeeting") or {}).get("joinUrl")) or creado.get("onlineMeetingUrl") or ""
    if not liga:
        raise TeamsError("Microsoft creó el evento pero no regresó la liga de Teams (¿la cuenta conectada tiene licencia de Teams?).")
    return {"evento_id": creado.get("id", ""), "liga": liga, "web_link": creado.get("webLink", "")}


async def actualizar_reunion(db: Session, integ: IntegracionTeams, evento_id: str, *, inicio: datetime, asunto: Optional[str] = None,
                             duracion_min: int = DURACION_REUNION_MIN) -> None:
    token = await token_vigente(db, integ)
    cambios = {"start": _fecha_graph(inicio), "end": _fecha_graph(inicio + timedelta(minutes=duracion_min))}
    if asunto:
        cambios["subject"] = asunto
    await _graph("PATCH", f"/me/events/{evento_id}", token, cambios)


async def cancelar_reunion(db: Session, integ: IntegracionTeams, evento_id: str) -> None:
    token = await token_vigente(db, integ)
    await _graph("DELETE", f"/me/events/{evento_id}", token)


def integracion_de(db: Session, cuenta_id: Optional[int]) -> Optional[IntegracionTeams]:
    """Conexión de la Cuenta lista para usarse: configurado en el servidor Y fila conectada."""
    if not cuenta_id or not teams_configurado():
        return None
    return db.query(IntegracionTeams).filter(IntegracionTeams.cuenta_id == cuenta_id).first()
