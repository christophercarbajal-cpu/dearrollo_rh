"""
Servicio de avatar para entrevistas — Anam (https://anam.ai).

API necesaria: ANAM_API_KEY  →  https://lab.anam.ai/register → API Keys
Sin clave, la entrevista corre en "modo texto": el mismo agente entrevistador
(OpenAI) conversa por chat, así el flujo completo funciona de punta a punta.

Restricción legal (reforma de derecho de autor 2026): usar únicamente avatares
neutrales del catálogo del proveedor, nunca clones de personas reales sin
consentimiento expreso.
"""

import re
from typing import Optional

import httpx

from ..config import settings

ANAM_SESSION_URL = "https://api.anam.ai/v1/auth/session-token"


def _limpio(valor: str) -> str:
    # Un .env con comillas o espacios ("ANAM_API_KEY = "abc" ") no debe apagar el avatar en silencio.
    return (valor or "").strip().strip('"').strip("'").strip()


def avatar_activo() -> bool:
    # CLAUDE.md: todo personaConfig inline debe traer llmId o Anam rechaza el token como "legacy" —
    # sin ANAM_LLM_ID no se intenta el avatar (antes se intentaba y caía a texto con error).
    return bool(_limpio(settings.anam_api_key) and _limpio(settings.anam_avatar_id) and _limpio(settings.anam_llm_id))


def campos_persona() -> dict:
    """Qué lleva hoy el `personaConfig` (sin valores sensibles) — para revisar compatibilidad con el
    plan contratado sin tener que leer el código en producción."""
    persona = persona_config("Red Human", "prompt", "hola")
    return {"campos": sorted(persona.keys()), "con_voz": "voiceId" in persona, "con_llm_propio": "llmId" in persona}


def estado_avatar() -> dict:
    """Qué variables de Anam ve el proceso (presencia, nunca valores) — para el log de arranque y
    el diagnóstico de RH. Si en producción "cae a texto", esto dice si es configuración o Anam."""
    return {
        "activo": avatar_activo(),
        "ANAM_API_KEY": bool(_limpio(settings.anam_api_key)),
        "ANAM_AVATAR_ID": bool(_limpio(settings.anam_avatar_id)),
        "ANAM_LLM_ID": bool(_limpio(settings.anam_llm_id)),
        "ANAM_VOICE_ID": bool(_limpio(settings.anam_voice_id)),
        "max_sesion_seg": settings.anam_max_sesion_seg,
        "endpoint": ANAM_SESSION_URL,
        # 2026-09-23: huella de la clave (primeros/últimos caracteres) — permite confirmar por teléfono
        # que el servidor trae la clave NUEVA del plan sin exponerla.
        "api_key_huella": _huella(_limpio(settings.anam_api_key)),
        "persona": campos_persona() if _limpio(settings.anam_avatar_id) else {},
    }


def _huella(clave: str) -> str:
    if not clave:
        return ""
    return f"{clave[:4]}…{clave[-4:]} ({len(clave)} caracteres)"


class AvatarError(RuntimeError):
    """Anam rechazó la sesión: el mensaje trae status + cuerpo de la respuesta (antes solo se veía
    "400 Bad Request" en el log y era imposible saber por qué caía a texto).

    2026-09-23 (incidente Expo): además lleva `status` y `request_id` como atributos para poder
    distinguir de un vistazo AUTENTICACIÓN/PLAN (401/402/403/404) de un problema de red o de payload."""

    def __init__(self, mensaje: str, status: Optional[int] = None, request_id: str = "", cuerpo: str = ""):
        super().__init__(mensaje)
        self.status = status
        self.request_id = request_id
        self.cuerpo = cuerpo

    @property
    def es_de_plan(self) -> bool:
        """True si Anam rechazó por credencial/plan (no por red ni por el contenido del personaConfig)."""
        return self.status in (401, 402, 403, 404, 429)


# 2026-10-16 (bug del tester): el TTS leía «$55,000 MXN mensuales» como «55 dólares con 0 centavos MXN mensuales». Todo
# texto que va a la voz del avatar (systemPrompt e initialMessage) pasa por `texto_para_voz`: sin «$», sin separadores de
# miles (la coma se lee como decimal), sin centavos en cero y con «pesos mexicanos» en lugar de MXN/MN.
_NUM = r"\d{1,3}(?:[,\s]\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
_MXN = r"(?:MXN|M\.N\.|MN)\b"
_RE_MONTO = re.compile(r"\$\s*(" + _NUM + r")(?:\s*(?:" + _MXN + r"|pesos(?:\s+mexicanos)?\b))?", re.IGNORECASE)
_RE_MONTO_MXN = re.compile(r"(?<![\w$.,])(" + _NUM + r")\s*" + _MXN, re.IGNORECASE)
_RE_RANGO = re.compile(r"(\d+) pesos mexicanos\s*(?:–|—|-|a)\s*(\d+ pesos mexicanos)")
# 2026-10-17 (QA): el TTS DELETREABA las cifras («10000» → «uno, cero, cero, cero, cero»). Ahora todo monto se escribe con
# PALABRAS: lo que mandamos (systemPrompt / initialMessage) se convierte en código y el LLM tiene la obligación estricta de
# hacer lo mismo con lo que genera (Anam habla directo con OpenAI: ahí solo manda esta regla del prompt).
REGLA_VOZ_MONTOS = (
    "REGLA ESTRICTA DE VOZ (todo lo que escribes se lee en voz alta): escribe SIEMPRE las cantidades de dinero y cualquier "
    "cantidad con PALABRAS, nunca con dígitos. Ejemplo: escribe «diez mil pesos mexicanos», NUNCA «10000 pesos mexicanos» "
    "ni «$10,000 MXN»; «doce mil quinientos pesos mexicanos mensuales», NUNCA «12500». Prohibidos los dígitos, el símbolo $, "
    "«MXN», «dólares» y los centavos.")

_UNIDADES = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce", "trece",
             "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidós",
             "veintitrés", "veinticuatro", "veinticinco", "veintiséis", "veintisiete", "veintiocho", "veintinueve"]
_DECENAS = {30: "treinta", 40: "cuarenta", 50: "cincuenta", 60: "sesenta", 70: "setenta", 80: "ochenta", 90: "noventa"}
_CENTENAS = {100: "ciento", 200: "doscientos", 300: "trescientos", 400: "cuatrocientos", 500: "quinientos", 600: "seiscientos",
             700: "setecientos", 800: "ochocientos", 900: "novecientos"}


def _menor_mil(n: int) -> str:
    if n == 100:
        return "cien"
    if n < 30:
        return _UNIDADES[n]
    if n < 100:
        d, u = n // 10 * 10, n % 10
        return _DECENAS[d] + (f" y {_UNIDADES[u]}" if u else "")
    c, resto = n // 100 * 100, n % 100
    return _CENTENAS[c] + (f" {_menor_mil(resto)}" if resto else "")


def numero_a_palabras(n: int) -> str:
    """12500 → «doce mil quinientos»; 1000 → «mil»; 21000 → «veintiún mil»; 1500000 → «un millón quinientos mil»."""
    n = int(n)
    if n < 1000:
        return _menor_mil(n)
    if n < 1_000_000:
        miles, resto = divmod(n, 1000)
        txt = "mil" if miles == 1 else re.sub(r"uno$", "ún", _menor_mil(miles)) + " mil"
        return txt + (f" {_menor_mil(resto)}" if resto else "")
    millones, resto = divmod(n, 1_000_000)
    txt = "un millón" if millones == 1 else re.sub(r"uno$", "ún", numero_a_palabras(millones)) + " millones"
    return txt + (f" {numero_a_palabras(resto)}" if resto else "")


def _cifra(texto: str) -> str:
    """«55,000.00» → «55000» (sin separadores ni centavos: no se leen en voz); `texto_para_voz` lo pasa a palabras."""
    return re.sub(r"[,\s]", "", texto).split(".", 1)[0]


def _a_palabras(m) -> str:
    try:
        return numero_a_palabras(int(m.group(1)))
    except ValueError:
        return m.group(1)


def texto_para_voz(texto: str) -> str:
    """Sanitiza montos para el TTS con PALABRAS: «$55,000 MXN» → «cincuenta y cinco mil pesos mexicanos»; «$8,000 – $10,000
    MXN» → «ocho mil a diez mil pesos mexicanos». Lo demás queda igual."""
    if not texto:
        return texto or ""
    t = _RE_MONTO.sub(lambda m: f"{_cifra(m.group(1))} pesos mexicanos", texto)
    t = _RE_MONTO_MXN.sub(lambda m: f"{_cifra(m.group(1))} pesos mexicanos", t)
    t = _RE_RANGO.sub(lambda m: f"{m.group(1)} a {m.group(2)}", t)
    t = re.sub(r"pesos mexicanos(?:\s+" + _MXN + r")+", "pesos mexicanos", t, flags=re.IGNORECASE)
    # 2026-10-17: las cifras de dinero con PALABRAS («10000 pesos mexicanos» → «diez mil pesos mexicanos»)
    return re.sub(r"\b(\d+)(?=(?: a \d+)? pesos mexicanos)", _a_palabras, t)


def persona_config(nombre_persona: str, system_prompt: str, mensaje_inicial: str, extras: Optional[dict] = None) -> dict:
    """personaConfig que se manda a Anam. `extras` (Fase 4, Punto 4 — tras el spike) permite
    agregar `tools`/`voiceDetectionOptions` sin tocar este núcleo."""
    persona = {
        "name": nombre_persona,
        "avatarId": _limpio(settings.anam_avatar_id),
        "systemPrompt": texto_para_voz(system_prompt) + "\n\n" + REGLA_VOZ_MONTOS,
        "languageCode": "es",
        "maxSessionLengthSeconds": settings.anam_max_sesion_seg,
        "initialMessage": texto_para_voz(mensaje_inicial),
    }
    if _limpio(settings.anam_voice_id):
        persona["voiceId"] = _limpio(settings.anam_voice_id)
    if _limpio(settings.anam_llm_id):
        persona["llmId"] = _limpio(settings.anam_llm_id)
    if extras:
        persona.update(extras)
    return persona


async def crear_sesion_avatar(
    nombre_persona: str, system_prompt: str, mensaje_inicial: str, extras: Optional[dict] = None
) -> Optional[dict]:
    """Crea una sesión de avatar en Anam y regresa el sessionToken para el navegador.

    Regresa None en modo demo (sin clave) — el frontend cae a entrevista por texto.
    """
    if not avatar_activo():
        return None

    persona = persona_config(nombre_persona, system_prompt, mensaje_inicial, extras)

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            ANAM_SESSION_URL,
            headers={"Authorization": f"Bearer {_limpio(settings.anam_api_key)}"},
            json={"personaConfig": persona},
        )
        # Anam manda un identificador de petición en la respuesta: es lo primero que pide su soporte.
        pedido = r.headers.get("x-request-id") or r.headers.get("x-amzn-requestid") or ""
        if r.status_code >= 400:
            pista = {
                401: "credencial inválida o revocada (ANAM_API_KEY)",
                402: "plan sin saldo o pago pendiente",
                403: "la clave no tiene permiso para este avatar/plan",
                404: "recurso inexistente (avatarId, voiceId o llmId ya no existen en esta cuenta)",
                429: "límite de sesiones del plan alcanzado",
            }.get(r.status_code, "rechazo de Anam")
            raise AvatarError(
                f"Anam {r.status_code} en session-token ({pista}): {r.text[:400]}"
                + (f" [request-id {pedido}]" if pedido else ""),
                status=r.status_code, request_id=pedido, cuerpo=r.text[:400],
            )
        datos = r.json()
        token = datos.get("sessionToken")
        if not token:
            raise AvatarError(f"Anam no regresó sessionToken: {str(datos)[:300]}", status=r.status_code, request_id=pedido)
        return {"session_token": token, "proveedor": "anam", "request_id": pedido}


async def probar_avatar() -> dict:
    """Diagnóstico para RH/soporte: intenta crear un session token real con un prompt mínimo y
    regresa qué pasó, sin exponer claves. No abre ninguna entrevista."""
    estado = estado_avatar()
    if not estado["activo"]:
        return {**estado, "ok": False, "detalle": "Faltan variables de Anam en el servidor (ver banderas)."}
    try:
        ses = await crear_sesion_avatar("Red Human", "Eres Red Human. Responde en una frase.", "Hola, soy Red Human.")
        return {
            **estado, "ok": bool(ses),
            "detalle": "Anam emitió un session token: la credencial y el plan están bien; si la sala sigue cayendo a texto, el problema es de RED (WebRTC) en el dispositivo." if ses else "Sin respuesta.",
            "request_id": (ses or {}).get("request_id", ""),
        }
    except AvatarError as ex:
        return {
            **estado, "ok": False, "status": ex.status, "request_id": ex.request_id,
            "causa": "autenticacion_o_plan" if ex.es_de_plan else "payload_o_red",
            "detalle": str(ex)[:500],
        }
    except Exception as ex:  # noqa: BLE001 — se reporta tal cual a RH
        return {**estado, "ok": False, "causa": "red_servidor", "detalle": str(ex)[:500]}
