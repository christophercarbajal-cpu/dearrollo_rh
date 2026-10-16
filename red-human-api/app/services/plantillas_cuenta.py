"""Plantillas de Meta POR CUENTA (2026-10-15, UVAQ — decisión del usuario).

Una Cuenta puede restringir las plantillas de Meta que usa (`Cuenta.plantillas_meta`). Vacío = las de siempre (nada
cambia para las demás Cuentas). Con política:

    {"permitidas": ["inicio_entrevista_rh", "aviso_proceso_rh", "alerta_entrevista_asignada", "oferta_laboral_rh"],
     "mapa": {"aviso_evaluador_rh": {"plantilla": "alerta_entrevista_asignada", "params": "evaluador_a_alerta"}, …},
     "aviso_ventana": "aviso_proceso_rh"}

  · Toda plantilla pasa por `aplicar` en el punto ÚNICO de envío (`whatsapp.enviar_plantilla`): se traduce con `mapa`
    (con su transformación de variables) y, si el resultado no está en `permitidas`, NO se llama a Meta: el mensaje queda
    «Pendiente: ventana de 24 h» (`ESTADO_VENTANA`) y su texto sale en texto libre en cuanto esa persona escriba
    (`entregar_pendientes`, desde el webhook). Nunca se crea ni se pide una plantilla nueva.
  · Un texto al candidato fuera de la ventana (aviso de cita con el jefe) usa `aviso_ventana` (sin variables) para abrir
    la conversación, y el texto completo queda pendiente hasta que el candidato responda.

La Cuenta del envío: la que marca `mensajeria.de_cuenta` (envíos al candidato) o `de_cuenta_plantillas` (avisos a
evaluadores, que no cambian de canal).
"""

import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import List, Optional

ESTADO_VENTANA = "ventana"
EVENTO_PENDIENTE = "pendiente_ventana"
TEXTO_VENTANA = "Pendiente: sale en texto libre cuando la persona escriba (ventana de 24 h; esta Cuenta solo usa plantillas aprobadas)"
DIAS_PENDIENTE = 7

_CUENTA_PLANTILLAS: ContextVar[Optional[int]] = ContextVar("cuenta_plantillas", default=None)
_CACHE = {"en": 0.0, "por_cuenta": {}}


@contextmanager
def de_cuenta_plantillas(cuenta_id: Optional[int]):
    """Marca la Cuenta del envío SOLO para la política de plantillas (no cambia el canal como `mensajeria.de_cuenta`)."""
    marca = _CUENTA_PLANTILLAS.set(cuenta_id)
    try:
        yield
    finally:
        _CUENTA_PLANTILLAS.reset(marca)


def cuenta_actual() -> Optional[int]:
    from . import mensajeria

    return _CUENTA_PLANTILLAS.get() or mensajeria._CUENTA.get()


def refrescar() -> None:
    _CACHE["en"] = 0.0


def precargar(cuenta_id: int, pol: dict) -> None:
    """Lo deja en el caché sin esperar al commit (scripts de configuración que validan dentro de su transacción)."""
    politica(cuenta_id)  # carga lo de la base si el caché está vencido
    _CACHE["por_cuenta"][cuenta_id] = dict(pol or {})


def politica(cuenta_id: Optional[int]) -> dict:
    """La política de la Cuenta ({} = sin restricción). Caché de 60 s."""
    if not cuenta_id:
        return {}
    if time.time() - _CACHE["en"] > 60:
        try:
            from ..database import SessionLocal
            from ..models import Cuenta

            with SessionLocal() as db:
                filas = db.query(Cuenta.id, Cuenta.plantillas_meta).all()
            _CACHE["por_cuenta"] = {i: dict(pm) for i, pm in filas if isinstance(pm, dict) and pm.get("permitidas")}
        except Exception:  # noqa: BLE001 — columna aún sin crear: sin restricción
            _CACHE["por_cuenta"] = {}
        _CACHE["en"] = time.time()
    return _CACHE["por_cuenta"].get(cuenta_id, {})


# ------------------------------------------------------------ transformación de variables

def _evaluador_a_alerta(params: List[str]) -> List[str]:
    """aviso_evaluador_rh [evaluador, candidato, puesto, «fecha, hora (zona)», lugar, liga] →
    alerta_entrevista_asignada [entrevistador, candidato, vacante, fecha, hora, liga]."""
    p = list(params) + ["-"] * (6 - len(params))
    fecha, _, hora = str(p[3]).partition(", ")
    return [p[0], p[1], p[2], fecha or "Por confirmar", hora or "Por confirmar", p[5]]


def _entrevista8_a_alerta(params: List[str]) -> List[str]:
    """alerta_entrevista_asig [entrevistador, candidato, vacante, fecha, hora, contacto, indicaciones, liga] → 6 variables."""
    p = list(params) + ["-"] * (8 - len(params))
    return [p[0], p[1], p[2], p[3], p[4], p[7]]


TRANSFORMACIONES = {
    "evaluador_a_alerta": _evaluador_a_alerta,
    "entrevista8_a_alerta": _entrevista8_a_alerta,
    "solo_nombre": lambda params: [(str(params[0]) if params else "").strip().split(" ")[0] or "-"],
    "sin_variables": lambda params: [],
}


def aplicar(plantilla: str, parametros: Optional[List[str]], cuenta_id: Optional[int] = None) -> dict:
    """{"bloquear": bool, "plantilla", "parametros", "detalle"} según la política de la Cuenta del envío."""
    pol = politica(cuenta_id if cuenta_id is not None else cuenta_actual())
    params = list(parametros or [])
    if not pol:
        return {"bloquear": False, "plantilla": plantilla, "parametros": params}
    destino = (pol.get("mapa") or {}).get(plantilla)
    if destino:
        fn = TRANSFORMACIONES.get(destino.get("params") or "")
        plantilla, params = destino["plantilla"], (fn(params) if fn else params)
    if plantilla not in (pol.get("permitidas") or []):
        return {"bloquear": True, "plantilla": plantilla, "parametros": params,
                "detalle": f"Plantilla «{plantilla}» no aprobada para esta Cuenta. {TEXTO_VENTANA}."}
    return {"bloquear": False, "plantilla": plantilla, "parametros": params}


def aviso_ventana(cuenta_id: Optional[int] = None) -> str:
    return (politica(cuenta_id if cuenta_id is not None else cuenta_actual()).get("aviso_ventana") or "").strip()


# ------------------------------------------------------------ pendientes: salen en texto libre cuando la persona escribe

def guardar_pendiente(telefono: str, texto: str, plantilla: str = "") -> None:
    """Guarda el texto para entregarlo en cuanto esa persona escriba (sesión propia; nunca lanza)."""
    cid = cuenta_actual()
    if not cid or not (texto or "").strip() or not telefono:
        return
    try:
        from ..database import SessionLocal
        from ..models import NotificacionEnviada
        from .telegram import telefono_10

        with SessionLocal() as db:
            db.add(NotificacionEnviada(cuenta_id=cid, evento=EVENTO_PENDIENTE, destinatario_tipo="whatsapp", destino=telefono_10(telefono),
                                       canal="whatsapp", enviado=False, detalle=TEXTO_VENTANA, estado=ESTADO_VENTANA,
                                       reintento={"texto": texto[:4000], "plantilla": plantilla}))
            db.commit()
    except Exception as ex:  # noqa: BLE001
        print(f"[plantillas_cuenta] no se pudo guardar el pendiente: {ex}", flush=True)


async def entregar_pendientes(db, telefono: str) -> int:
    """La persona escribió (ventana abierta): manda en texto libre lo que quedó pendiente. Regresa cuántos salieron."""
    from ..models import NotificacionEnviada
    from .telegram import telefono_10
    from .whatsapp import _texto_libre_meta

    tel = telefono_10(telefono)
    if not tel:
        return 0
    desde = datetime.now(timezone.utc) - timedelta(days=DIAS_PENDIENTE)
    try:
        filas = (db.query(NotificacionEnviada).filter(NotificacionEnviada.estado == ESTADO_VENTANA, NotificacionEnviada.destino == tel,
                                                     NotificacionEnviada.enviado.is_(False), NotificacionEnviada.creada_en >= desde)
                 .order_by(NotificacionEnviada.id).all())
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for f in filas:
        texto = (f.reintento or {}).get("texto") or ""
        r = await _texto_libre_meta(tel, texto) if texto else {"enviado": False}
        f.enviado = bool(r.get("enviado"))
        f.estado = "enviado" if f.enviado else ESTADO_VENTANA
        f.detalle = "Entregado en texto libre al abrirse la ventana de 24 h" if f.enviado else str(r.get("detalle") or "")[:500]
        n += int(f.enviado)
    if filas:
        db.commit()
    return n
