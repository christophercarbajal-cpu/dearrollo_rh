"""Psicométricas.mx — cliente de su API (2026-09-29). Referencia: https://psicometricas.mx/api

* Base https://admin.psicometricas.mx/api/ · todas las llamadas llevan `Token` y `Password` (form-encoded) por HTTPS.
  Llaves SOLO por entorno (PSICOMETRICAS_TOKEN + PSICOMETRICAS_PASSWORD, o PSICOMETRICAS_USUARIO como Password).
  Sin ellas `configurado()` es False y el modo Integrada sigue simulado a mano (nunca rompe el flujo).
* `agregaCandidato` (Candidate, Email, Vacancy, Tests «1,2», Lang Mx) → `clave`. Su API NO regresa liga: el
  sustentante entra al portal oficial `PSICOMETRICAS_URL_CANDIDATO` (default https://evaluacion.psicometrica.mx/) y
  escribe su clave. Red Human NO depende del correo del proveedor: notifica él mismo (correo + Telegram/WhatsApp).
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


# Catálogo OFICIAL de pruebas que acepta `Tests` en agregaCandidato (https://psicometricas.mx/api, consultado 2026-10-08).
# Un ID fuera de esta lista hace que el proveedor rechace el alta (incidente «Batería Gerente»: incluía Herrmann, que la
# plataforma NO ofrece). Se valida ANTES de llamar para no gastar el saldo compartido con producción.
PRUEBAS_PROVEEDOR = {
    "1": "Cleaver", "2": "Kostick", "3": "IPV", "4": "LIFO", "5": "Zavic", "7": "Terman", "9": "Inglés", "10": "16PF",
    "11": "Barsit", "15": "Moss", "16": "Wonderlic",
}
TESTS_MAX = 100  # longitud máxima documentada del campo `Tests`
# Texto para RH cuando el ALTA en el proveedor falla (el detalle técnico va al log y a la bitácora).
MENSAJE_FALLA_ALTA = "No se pudo generar la prueba. Intenta nuevamente."
# Nombre visible del proveedor en la interfaz y en los mensajes a RH/candidato: la plataforma es de Red Human.
NOMBRE_VISIBLE = "Red Human"


def disponibles_texto() -> str:
    return ", ".join(f"{k} {v}" for k, v in PRUEBAS_PROVEEDOR.items())


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
    if 300 <= r.status_code < 400:
        # p. ej. 301: la URL base cambió o le falta/sobra la diagonal. No se sigue la redirección (un POST redirigido
        # llega como GET y sin datos): se reporta con su destino para corregir PSICOMETRICAS_BASE_URL.
        raise PsicometricasError(f"El proveedor respondió una redirección {r.status_code} hacia «{r.headers.get('location', '')}»: "
                                 "revisa PSICOMETRICAS_BASE_URL.", r.status_code)
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
    """«1, 7, 1» → «1,7»: IDs numéricos SIN repetir, todos del catálogo oficial (`PRUEBAS_PROVEEDOR`) y dentro del largo
    documentado. Cualquier otro valor → 400 SIN llamar al proveedor."""
    ids = list(dict.fromkeys(x.strip().lstrip("0") or "0" for x in (id_proveedor or "").split(",") if x.strip()))
    if not ids or not all(x.isdigit() for x in ids):
        raise PsicometricasError("El «identificador en el proveedor» debe ser el ID numérico de la prueba (p. ej. 1 = Cleaver, "
                                 "7 = Terman; varios: 1,7).", 400)
    invalidos = [x for x in ids if x not in PRUEBAS_PROVEEDOR]
    if invalidos:
        raise PsicometricasError(f"El identificador {', '.join(invalidos)} no corresponde a ninguna prueba disponible en la "
                                 f"plataforma de evaluación. Disponibles: {disponibles_texto()}.", 400)
    tests = ",".join(ids)
    if len(tests) > TESTS_MAX:
        raise PsicometricasError(f"Demasiadas pruebas en una sola asignación (máximo {TESTS_MAX} caracteres).", 400)
    return tests


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
        raise PsicometricasError(f"El correo del candidato no es válido: «{correo}».", 400)
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
        raise PsicometricasError(f"No se pudo conectar con la plataforma de evaluación: {ex}")
    try:
        crudo = json.dumps(r.json(), ensure_ascii=False)
    except ValueError:
        crudo = (r.text or "")[:2000]
    print(f"[psicometricas] respuesta HTTP {r.status_code}: {crudo}", flush=True)
    datos = _revisar(r)
    clave = str((datos or {}).get("clave") or "") if isinstance(datos, dict) else ""
    if not clave:
        raise PsicometricasError(f"La plataforma de evaluación no regresó la clave del candidato: {str(datos)[:200]}")
    liga = _buscar_liga(datos) or (url_candidato(clave) or "")
    return {"clave": clave, "liga": liga, "respuesta": datos}


def agregar_candidato(nombre: str, correo: str, vacante: str, tests: str, lang: str = "Mx") -> str:
    return asignar_candidato(nombre, correo, vacante, tests, lang)["clave"]


def consultar_candidato(clave: str) -> List[dict]:
    try:
        r = httpx.get(_url("consultaCandidato"), params={**_cred(), "Clave": clave}, timeout=30)
    except httpx.HTTPError as ex:
        raise PsicometricasError(f"No se pudo conectar con la plataforma de evaluación: {ex}")
    datos = _revisar(r)
    filas = datos if isinstance(datos, list) else [datos]
    return [f for f in filas if isinstance(f, dict) and str(f.get("clave") or clave) == clave]


def terminado(filas: List[dict]) -> bool:
    """Todas sus pruebas con `fecha_fin` (campo documentado de consultaCandidato)."""
    return bool(filas) and all(f.get("fecha_fin") for f in filas)


CAMPOS_INICIO = ("fecha_inicio", "fecha_ini", "inicio", "fecha_inicial")


def iniciado(filas: List[dict]) -> bool:
    """¿El sustentante ya empezó? Alguna prueba con fecha de inicio (o de fin, si terminó una de varias)."""
    for f in filas or []:
        if f.get("fecha_fin") or any(f.get(c) for c in CAMPOS_INICIO):
            return True
    return False


def resultado_json(clave: str) -> Union[dict, list]:
    try:
        r = httpx.get(_url("consultaResultado"), params={**_cred(), "Clave": clave, "Pdf": "false"}, timeout=60)
    except httpx.HTTPError as ex:
        raise PsicometricasError(f"No se pudo conectar con la plataforma de evaluación: {ex}")
    return _revisar(r)


def resultado_pdf(clave: str) -> Optional[bytes]:
    try:
        r = httpx.get(_url("consultaResultado"), params={**_cred(), "Clave": clave, "Pdf": "true"}, timeout=60)
    except httpx.HTTPError as ex:
        raise PsicometricasError(f"No se pudo conectar con la plataforma de evaluación: {ex}")
    if r.status_code >= 400:
        raise PsicometricasError(f"Psicométricas.mx respondió {r.status_code} al pedir el PDF.", r.status_code)
    return r.content if r.content.startswith(b"%PDF") else None


PORTAL_SUSTENTANTE = "https://evaluacion.psicometrica.mx/"


def url_candidato(clave: str) -> Optional[str]:
    """Portal del sustentante para esa clave: PSICOMETRICAS_URL_CANDIDATO (default el portal oficial) con «{clave}»
    sustituida si la trae. Una URL del panel de administración se IGNORA con aviso y se usa el portal oficial."""
    if not clave:
        return None
    plantilla = (settings.psicometricas_url_candidato or "").strip() or PORTAL_SUSTENTANTE
    if not es_liga_candidato(plantilla.replace("{clave}", "X")):
        print(f"[psicometricas] ⚠️ PSICOMETRICAS_URL_CANDIDATO ignorada («{plantilla}»): es del panel de administración "
              f"o no es https. Se usa el portal oficial {PORTAL_SUSTENTANTE}.", flush=True)
        plantilla = PORTAL_SUSTENTANTE
    return plantilla.replace("{clave}", clave)


def instrucciones(clave: str) -> List[str]:
    """Pasos del portal (https://evaluacion.psicometrica.mx/login: campo «Clave», aviso de privacidad, «Entrar»)."""
    return [
        "Abre la página de evaluación desde tu celular o computadora.",
        f"En el campo «Clave» escribe tu clave de acceso: {clave}",
        "Marca la casilla del aviso de privacidad y presiona «Entrar».",
        "Responde tus pruebas en un lugar tranquilo y con buena conexión; puede pedirte acceso a la cámara para validar tu identidad.",
    ]


def mensaje_candidato(nombre: str, clave: str, liga: str, empresa: str = "", vacante: str = "", recordatorio: bool = False) -> str:
    """Texto que Red Human manda por Telegram/WhatsApp: SIEMPRE la URL del portal, la clave y los pasos."""
    saludo = f"Hola {nombre}," if nombre else "Hola,"
    contexto = f" para la vacante {vacante}" if vacante else ""
    contexto += f" en {empresa}" if empresa else ""
    pasos = "\n".join(f"{i}. {t}" for i, t in enumerate(instrucciones(clave), 1))
    inicio = (f"{saludo} te recordamos que tienes pendiente tu evaluación psicométrica{contexto}."
              if recordatorio else f"{saludo} se te asignó una evaluación psicométrica{contexto}.")
    return (f"{inicio}\n\n"
            f"Aquí tienes la liga de tu evaluación:\n{liga}\n\n"
            f"Tu clave de acceso: {clave}\n\n"
            f"Cómo empezar:\n{pasos}\n\n"
            "Si tienes algún problema para entrar, respóndenos por aquí.")
