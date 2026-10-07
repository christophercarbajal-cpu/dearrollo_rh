"""Selector de canal de mensajería (2026-10-06): ¿este envío sale por WhatsApp o por Telegram?

La fachada `services/whatsapp.py` lo consulta en CADA envío (automático o manual: prefiltro, recordatorios, solicitudes
de documentos, avisos de evaluaciones, notificaciones), así que ningún flujo de negocio cambia ni decide el canal:

1. Conversación en curso: mientras se procesa un mensaje ENTRANTE, la respuesta a esa misma persona sale por el canal
   por el que escribió (`en_conversacion`). A terceros (RH, Cliente, entrevistador) nunca se les aplica.
2. Canal de la Cuenta (`Cuenta.canal_mensajeria`): `whatsapp` → WhatsApp; `telegram` → Telegram; `ambos` → el canal por
   el que la persona escribió por última vez (si nunca escribió y ya vinculó Telegram, Telegram; si no, WhatsApp).
   La Cuenta la marca quien envía al candidato (`with de_cuenta(p.cuenta_id)` o `cuenta_id=`); sin ella se infiere de
   la persona con ese teléfono (solo candidatos).
3. Sin `TELEGRAM_BOT_TOKEN`, todo sale por WhatsApp aunque la Cuenta haya elegido Telegram.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import List, Optional, Tuple

from . import telegram

_CONVERSACION: ContextVar[Tuple[str, str]] = ContextVar("conversacion_mensajeria", default=("", ""))
_CUENTA: ContextVar[Optional[int]] = ContextVar("cuenta_mensajeria", default=None)


@contextmanager
def de_cuenta(cuenta_id: Optional[int]):
    """Los envíos dentro del bloque son en nombre de esa Cuenta (su `canal_mensajeria` decide el canal). Se usa en los
    envíos al CANDIDATO; a terceros (RH, Cliente, entrevistador) no se les marca Cuenta."""
    marca = _CUENTA.set(cuenta_id)
    try:
        yield
    finally:
        _CUENTA.reset(marca)


@contextmanager
def en_conversacion(canal: str, telefono: str):
    """Marca el procesamiento de un mensaje entrante: lo que se le responda a ESE teléfono sale por `canal`."""
    marca = _CONVERSACION.set((canal, telegram.telefono_10(telefono)))
    try:
        yield
    finally:
        _CONVERSACION.reset(marca)


def canal_conversacion(telefono: str = "") -> str:
    """Canal del mensaje entrante en proceso ('' fuera de un webhook o si el destino es otra persona)."""
    canal, tel = _CONVERSACION.get()
    if not canal:
        return ""
    if telefono and telegram.telefono_10(telefono) != tel:
        return ""
    return canal


def canal_registro(canal: str) -> str:
    """Canal con el que se guarda un `Mensaje`: la lógica del negocio usa «whatsapp» como canal de chat; si la
    conversación real es por Telegram, el historial lo registra como «telegram»."""
    return "telegram" if canal == "whatsapp" and canal_conversacion() == "telegram" else canal


def es_chat(canal: str) -> bool:
    return canal in ("whatsapp", "telegram")


def solo_telegram() -> bool:
    """AMBIENTE_PRUEBA=true + bot configurado: Telegram es el ÚNICO canal con candidatos (desarrollo). Ignora el canal
    de cada Cuenta y cualquier configuración de WhatsApp."""
    from ..config import settings

    return bool(settings.ambiente_prueba) and telegram.activo()


def canal_de_cuenta(db, cuenta_id: Optional[int]) -> str:
    from ..models import Cuenta, normalizar_canal_mensajeria

    if solo_telegram():
        return "telegram"

    if not cuenta_id:
        return "whatsapp"
    c = db.get(Cuenta, cuenta_id)
    return normalizar_canal_mensajeria(c.canal_mensajeria if c else "")


def _cuentas_de_telefono(db, tel: str) -> List[int]:
    """Cuentas donde esa persona es CANDIDATO (la más reciente primero). El canal de la Cuenta es el de la comunicación
    con candidatos: a quien no lo es (RH, Cliente, entrevistador, colaborador sin historial) se le escribe por WhatsApp."""
    from ..models import Candidato

    return [cid for (cid,) in db.query(Candidato.cuenta_id).filter(Candidato.telefono == tel, Candidato.eliminado_en.is_(None))
            .order_by(Candidato.id.desc()).limit(5).all() if cid]


def _ultimo_canal_entrante(db, tel: str) -> str:
    from ..models import Candidato, Mensaje

    fila = (
        db.query(Mensaje.canal)
        .join(Candidato, Mensaje.candidato_id == Candidato.id)
        .filter(Candidato.telefono == tel, Mensaje.rol == "user", Mensaje.canal.in_(("whatsapp", "telegram")))
        .order_by(Mensaje.id.desc())
        .first()
    )
    return fila[0] if fila else ""


def resolver(telefono: str, cuenta_id: Optional[int] = None, db=None) -> str:
    """'whatsapp' | 'telegram' para un envío a `telefono`. Nunca lanza (ante cualquier error, WhatsApp)."""
    if solo_telegram():
        return "telegram"
    conv = canal_conversacion(telefono)
    if conv:
        return conv
    if not telegram.activo():
        return "whatsapp"
    tel = telegram.telefono_10(telefono)
    if not tel:
        return "whatsapp"
    cuenta_id = cuenta_id or _CUENTA.get()
    from ..database import SessionLocal

    propia = db is None
    db = db or SessionLocal()
    try:
        ids = [cuenta_id] if cuenta_id else _cuentas_de_telefono(db, tel)
        canal = canal_de_cuenta(db, ids[0]) if ids else "whatsapp"
        if canal != "ambos":
            return canal
        if not telegram.chat_de_telefono(tel, db):
            return "whatsapp"
        return _ultimo_canal_entrante(db, tel) or "telegram"
    except Exception as e:  # noqa: BLE001 — el selector nunca tumba un envío
        print(f"[mensajeria] no se pudo resolver el canal de {tel}: {e}")
        return "whatsapp"
    finally:
        if propia:
            db.close()


def alcance_canal(cuentas: list, canal: str) -> list:
    """Cuentas que atienden un webhook de `canal`: WhatsApp → las que no eligieron SOLO Telegram; Telegram → las que
    eligieron Telegram o ambos."""
    from ..models import normalizar_canal_mensajeria

    if solo_telegram():  # desarrollo: Telegram atiende a todas las Cuentas; WhatsApp a ninguna
        return list(cuentas) if canal == "telegram" else []
    if canal == "whatsapp" and not telegram.activo():
        return list(cuentas)  # sin bot, una Cuenta «solo Telegram» sigue atendiendo por WhatsApp
    if canal == "telegram":
        return [c for c in cuentas if normalizar_canal_mensajeria(c.canal_mensajeria) in ("telegram", "ambos")]
    return [c for c in cuentas if normalizar_canal_mensajeria(c.canal_mensajeria) != "telegram"]


def canales_publicos(vacante) -> dict:
    """Canal con candidatos de la Cuenta de una vacante, para el portal público (detalle y «Postulación exitosa»):

    - `canalCandidatos`: whatsapp | telegram | ambos (`Cuenta.canal_mensajeria`; sin `TELEGRAM_BOT_TOKEN` o sin
      `TELEGRAM_BOT_USERNAME` siempre «whatsapp», porque no habría a dónde mandar al candidato);
    - `telegramBotUsername` (del `.env`) y `telegramLiga` = https://t.me/<bot>?start=vac_<VAC-####>;
    - `whatsappLiga` = wa.me del número que atiende la Cuenta (el exclusivo Premium o `WHATSAPP_PUBLIC_NUMBER`).
    Nunca expone tokens: solo el usuario público del bot y el número público."""
    from urllib.parse import quote

    from ..config import settings
    from ..models import normalizar_canal_mensajeria

    cuenta = getattr(vacante, "cuenta", None)
    canal = "telegram" if solo_telegram() else normalizar_canal_mensajeria(cuenta.canal_mensajeria if cuenta else "")
    bot = telegram.usuario_bot() if telegram.activo() else ""
    if not bot:
        canal = "whatsapp"
    numero = (cuenta.whatsapp_comunicacion if cuenta and cuenta.whatsapp_exclusivo else "") or settings.whatsapp_public_number
    numero = "".join(ch for ch in (numero or "") if ch.isdigit())
    if len(numero) == 10:
        numero = "52" + numero
    codigo = getattr(vacante, "codigo", "") or ""
    texto = quote(f"Hola, me interesa la vacante {codigo}".strip())
    return {
        "canalCandidatos": canal,
        "telegramBotUsername": bot if canal in ("telegram", "ambos") else "",
        "telegramLiga": telegram.liga_vacante(codigo) if canal in ("telegram", "ambos") else "",
        "whatsappLiga": f"https://wa.me/{numero}?text={texto}" if numero and canal in ("whatsapp", "ambos") else "",
    }
