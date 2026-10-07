"""Psicométricas.mx — cliente de su API (2026-09-29). Referencia: https://psicometricas.mx/api

* Base https://admin.psicometricas.mx/api/ · todas las llamadas llevan `Token` y `Password` (form-encoded) por HTTPS.
  Llaves SOLO por entorno (PSICOMETRICAS_TOKEN + PSICOMETRICAS_PASSWORD, o PSICOMETRICAS_USUARIO como Password).
  Sin ellas `configurado()` es False y el modo Integrada sigue simulado a mano (nunca rompe el flujo).
* `agregaCandidato` (Candidate, Email, Vacancy, Tests «1,2», Lang Mx) → `clave`. Psicométricas manda al candidato
  su liga por correo; su API NO regresa esa liga (se muestra la clave; `PSICOMETRICAS_URL_CANDIDATO` es opcional).
* `consultaCandidato` (Clave) → estatus / fecha_fin; `consultaResultado` (Clave, Prueba, Pdf) → JSON o PDF binario.
* Su webhook (`termina_prueba` / `termina_practica`) NO trae firma: NUNCA se confía en él solo — se confirma con
  `consultaCandidato` (fecha_fin) antes de guardar nada.
"""

import json
import re
from typing import List, Optional, Union
from urllib.parse import urlparse

import httpx

from ..config import settings

ERRORES = {
    "1001": "Psicométricas.mx rechazó el Token/Password (1001).",
    "1002": "La cuenta de Psicométricas.mx no tiene un paquete activo (1002).",
    "1003": "El paquete de Psicométricas.mx no es compatible con la API (1003).",
    "1004": "Faltan campos obligatorios para Psicométricas.mx (1004).",
}


class PsicometricasError(Exception):
    def __init__(self, mensaje: str, status: Optional[int] = None):
        super().__init__(mensaje)
        self.status = status


def _password() -> str:
    return settings.psicometricas_password or settings.psicometricas_usuario


def configurado() -> bool:
    return bool(settings.psicometricas_token and _password())


def es_psicometricas(proveedor: str) -> bool:
    p = (proveedor or "").lower().replace(" ", "").replace("é", "e")
    return "psicometricas" in p


def _cred() -> dict:
    if not configurado():
        raise PsicometricasError("Psicométricas.mx no está configurado (PSICOMETRICAS_TOKEN / PSICOMETRICAS_PASSWORD).", 503)
    return {"Token": settings.psicometricas_token, "Password": _password()}


def _url(ruta: str) -> str:
    return f"{settings.psicometricas_base_url.rstrip('/')}/{ruta}"


def _revisar(r: httpx.Response) -> Union[dict, list]:
    try:
        datos = r.json()
    except ValueError:
        raise PsicometricasError(f"Psicométricas.mx respondió {r.status_code} sin JSON.", r.status_code)
    codigo = str((datos or {}).get("code") or (datos or {}).get("codigo") or "") if isinstance(datos, dict) else ""
    if r.status_code >= 400 or codigo in ERRORES:
        msg = ERRORES.get(codigo) or (datos.get("msg") if isinstance(datos, dict) else "") or f"HTTP {r.status_code}"
        raise PsicometricasError(str(msg), r.status_code)
    return datos


def tests_de(id_proveedor: str) -> str:
    """«1, 7» → «1,7» (IDs numéricos de sus pruebas: 1 Cleaver, 2 Kostick, 7 Terman, 10 16PF…)."""
    ids = [x.strip() for x in (id_proveedor or "").split(",") if x.strip()]
    if not ids or not all(x.isdigit() for x in ids):
        raise PsicometricasError("El «identificador en el proveedor» debe ser el ID numérico de la prueba en Psicométricas.mx (p. ej. 1 = Cleaver, 7 = Terman; varios: 1,7).", 400)
    return ",".join(ids)


CORREO_VALIDO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Llaves que, si el proveedor llegara a regresarlas, traen la liga/token de acceso del candidato. Su documentación
# solo promete `status`, `clave` y `msg`; se revisa todo el JSON por si su respuesta real trae más.
LLAVES_LIGA = ("url", "liga", "link", "enlace", "url_acceso", "url_candidato", "acceso", "url_prueba", "login")


def _oculto(payload: dict) -> dict:
    return {k: ("***" if k in ("Token", "Password") else v) for k, v in payload.items()}


def payload_agrega_candidato(nombre: str, correo: str, vacante: str, tests: str, lang: str = "Mx") -> dict:
    """Cuerpo EXACTO de agregaCandidato según https://psicometricas.mx/api (form-encoded): Candidate, Email, Vacancy,
    Tests («1,7»), Lang (Mx|Es) + Token/Password. Limpia los valores: un correo con espacios/mayúsculas o un nombre
    de relleno («Candidato WhatsApp») hace que el proveedor registre al candidato pero no le entregue su correo."""
    correo = (correo or "").strip().lower()
    if not CORREO_VALIDO.match(correo):
        raise PsicometricasError(f"El correo del candidato no es válido para Psicométricas.mx: «{correo}».", 400)
    nombre = " ".join((nombre or "").split())
    if not nombre or nombre.startswith("Candidato") or nombre == "TMP":
        raise PsicometricasError("El candidato no tiene nombre real en su ficha; captúralo antes de enviar la psicometría.", 400)
    return {"Candidate": nombre, "Email": correo, "Vacancy": " ".join((vacante or "").split())[:150] or "Vacante",
            "Tests": tests_de(tests), "Lang": lang if lang in ("Mx", "Es") else "Mx"}


def es_liga_candidato(url: str) -> bool:
    """Una liga sirve al CANDIDATO solo si es https y NO es del panel/API de administración (admin.psicometricas.mx o
    cualquier host «admin.*» / ruta /api): esa pide login de reclutador y el sustentante no puede entrar."""
    try:
        u = urlparse((url or "").strip())
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host:
        return False
    if host.startswith("admin.") or host == settings_host_api() or u.path.lower().startswith("/api"):
        return False
    return True


def settings_host_api() -> str:
    return (urlparse(settings.psicometricas_base_url or "").hostname or "").lower()


def _buscar_liga(datos) -> str:
    """URL (o token) de acceso del candidato en cualquier nivel de la respuesta; '' si no viene."""
    if isinstance(datos, dict):
        for k, v in datos.items():
            kl = str(k).lower()
            if isinstance(v, str) and v.strip():
                if kl in LLAVES_LIGA and es_liga_candidato(v):
                    return v.strip()
        for v in datos.values():
            liga = _buscar_liga(v)
            if liga:
                return liga
    elif isinstance(datos, list):
        for v in datos:
            liga = _buscar_liga(v)
            if liga:
                return liga
    elif isinstance(datos, str):
        for m in re.findall(r"https://\S+", datos):
            if es_liga_candidato(m.rstrip(".,)")):
                return m.rstrip(".,)")
    return ""


def asignar_candidato(nombre: str, correo: str, vacante: str, tests: str, lang: str = "Mx") -> dict:
    """agregaCandidato → {clave, liga, respuesta}. `liga` = la URL/token de acceso si el proveedor la regresa; si no,
    PSICOMETRICAS_URL_CANDIDATO con la clave; si tampoco, ''. Deja en el log el payload (sin credenciales) y el JSON
    COMPLETO de la respuesta."""
    cuerpo = payload_agrega_candidato(nombre, correo, vacante, tests, lang)
    payload = {**_cred(), **cuerpo}
    print(f"[psicometricas] POST {_url('agregaCandidato')} payload={json.dumps(_oculto(payload), ensure_ascii=False)}", flush=True)
    try:
        r = httpx.post(_url("agregaCandidato"), data=payload, timeout=30)
    except httpx.HTTPError as ex:
        print(f"[psicometricas] sin conexión: {ex}", flush=True)
        raise PsicometricasError(f"No se pudo conectar con Psicométricas.mx: {ex}")
    try:
        crudo = json.dumps(r.json(), ensure_ascii=False)
    except ValueError:
        crudo = (r.text or "")[:2000]
    print(f"[psicometricas] respuesta HTTP {r.status_code}: {crudo}", flush=True)
    datos = _revisar(r)
    clave = str((datos or {}).get("clave") or "") if isinstance(datos, dict) else ""
    if not clave:
        raise PsicometricasError(f"Psicométricas.mx no regresó la clave del candidato: {str(datos)[:200]}")
    liga = _buscar_liga(datos) or (url_candidato(clave) or "")
    return {"clave": clave, "liga": liga, "respuesta": datos}


def agregar_candidato(nombre: str, correo: str, vacante: str, tests: str, lang: str = "Mx") -> str:
    return asignar_candidato(nombre, correo, vacante, tests, lang)["clave"]


def consultar_candidato(clave: str) -> List[dict]:
    try:
        r = httpx.get(_url("consultaCandidato"), params={**_cred(), "Clave": clave}, timeout=30)
    except httpx.HTTPError as ex:
        raise PsicometricasError(f"No se pudo conectar con Psicométricas.mx: {ex}")
    datos = _revisar(r)
    filas = datos if isinstance(datos, list) else [datos]
    return [f for f in filas if isinstance(f, dict) and str(f.get("clave") or clave) == clave]


def terminado(filas: List[dict]) -> bool:
    """Todas sus pruebas con `fecha_fin` (campo documentado de consultaCandidato)."""
    return bool(filas) and all(f.get("fecha_fin") for f in filas)


def resultado_json(clave: str) -> Union[dict, list]:
    try:
        r = httpx.get(_url("consultaResultado"), params={**_cred(), "Clave": clave, "Pdf": "false"}, timeout=60)
    except httpx.HTTPError as ex:
        raise PsicometricasError(f"No se pudo conectar con Psicométricas.mx: {ex}")
    return _revisar(r)


def resultado_pdf(clave: str) -> Optional[bytes]:
    try:
        r = httpx.get(_url("consultaResultado"), params={**_cred(), "Clave": clave, "Pdf": "true"}, timeout=60)
    except httpx.HTTPError as ex:
        raise PsicometricasError(f"No se pudo conectar con Psicométricas.mx: {ex}")
    if r.status_code >= 400:
        raise PsicometricasError(f"Psicométricas.mx respondió {r.status_code} al pedir el PDF.", r.status_code)
    return r.content if r.content.startswith(b"%PDF") else None


def url_candidato(clave: str) -> Optional[str]:
    """Liga DIRECTA del sustentante con su clave (PSICOMETRICAS_URL_CANDIDATO con «{clave}»). Una plantilla sin
    «{clave}» (liga genérica) o del panel de administración se IGNORA con aviso en el log: nunca se manda al candidato."""
    plantilla = (settings.psicometricas_url_candidato or "").strip()
    if not plantilla or not clave:
        return None
    if "{clave}" not in plantilla or not es_liga_candidato(plantilla.replace("{clave}", "X")):
        print(f"[psicometricas] ⚠️ PSICOMETRICAS_URL_CANDIDATO ignorada («{plantilla}»): debe ser la liga del "
              "sustentante con {clave} y no la del panel de administración.", flush=True)
        return None
    return plantilla.replace("{clave}", clave)


def mensaje_candidato(nombre: str, correo: str, clave: str, liga: str = "") -> str:
    """Texto que Red Human manda al candidato por su canal (Telegram/WhatsApp). Siempre lleva la CLAVE de acceso; la
    liga solo si es directa del sustentante (la valida `es_liga_candidato`)."""
    saludo = f"Hola {nombre}," if nombre else "Hola,"
    if liga and es_liga_candidato(liga):
        return (f"{saludo} aquí tienes la liga de tu evaluación psicométrica:\n{liga}\n\n"
                f"Si te pide una clave de acceso, escribe: {clave}\n"
                "Hazla desde un lugar tranquilo, con buena conexión; puede pedirte acceso a la cámara para validar tu identidad.")
    return (f"{saludo} ya te registramos en tu evaluación psicométrica.\n\n"
            f"Psicométricas.mx te envió la invitación a {correo or 'tu correo'} con el botón para comenzar "
            "(si no la ves en 5 minutos, revisa Spam o Promociones; el remitente es Psicométricas.mx).\n"
            f"Tu clave de acceso es: {clave}\n\n"
            "Si no te llegó el correo, respóndenos aquí y te ayudamos.")
