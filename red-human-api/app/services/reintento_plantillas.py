"""Reintento de avisos «Pendiente de plantilla» (Cambio 46, 2026-10-14).

Un aviso de WhatsApp a un evaluador (entrevistador, médico, instructor) que Meta rechazó porque la plantilla todavía no
existe o sigue en revisión (132001…) NO es un fallo: queda en `notificaciones_enviadas` con `estado="pendiente"` y su
`reintento` {telefono, plantilla, parametros, texto}. Este job lo vuelve a intentar (misma cascada: texto libre →
plantilla) cada `META_REINTENTO_PLANTILLA_MIN` minutos, hasta que la plantilla quede activa:

* sale            → la fila y su trazabilidad (`envios_actividad.notificacion_id`) pasan a «Enviado».
* sigue pendiente → se queda «Pendiente de plantilla» (cuenta el intento).
* error definitivo (número inválido, token…) → «Fallido» con su motivo legible.

Pasados `META_REINTENTO_PLANTILLA_DIAS` desde el primer intento se deja de intentar (la cita ya pasó), sin cambiar el
estado. Cada intento se RECLAMA (commit) antes de llamar a Meta: una corrida encimada nunca duplica el aviso.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..config import settings
from ..models import EnvioActividad, Evaluacion, NotificacionEnviada


def _pendientes(db: Session, ahora: datetime):
    limite = ahora - timedelta(days=max(1, int(settings.meta_reintento_plantilla_dias or 7)))
    return (db.query(NotificacionEnviada)
            .filter(NotificacionEnviada.estado == "pendiente", NotificacionEnviada.canal == "whatsapp",
                    NotificacionEnviada.creada_en >= limite)
            .order_by(NotificacionEnviada.id).all())


def _actualizar_trazabilidad(db: Session, fila: NotificacionEnviada, envio: dict, estado: str) -> None:
    try:
        envios = db.query(EnvioActividad).filter(EnvioActividad.notificacion_id == fila.id).all()
    except Exception:  # noqa: BLE001 — tabla del paso NO fatal sin crear
        return
    for e in envios:
        e.estado = estado
        e.detalle = str(envio.get("detalle") or "")[:1000]
        if envio.get("wa_id"):
            e.mensaje_id = str(envio["wa_id"])[:120]
        if estado == "enviado" and e.evaluacion_id:
            ev = db.get(Evaluacion, e.evaluacion_id)
            if ev is not None and not ev.enviada_en:
                ev.enviada_en = datetime.now(timezone.utc)  # primer envío CONFIRMADO a quien realiza la evaluación


async def reintentar(db: Session, ahora: Optional[datetime] = None) -> dict:
    """Una pasada sobre los avisos pendientes de plantilla. Regresa {revisados, enviados, pendientes, fallidos}."""
    from . import whatsapp

    ahora = ahora or datetime.now(timezone.utc)
    cuenta = {"revisados": 0, "enviados": 0, "pendientes": 0, "fallidos": 0}
    for fila in _pendientes(db, ahora):
        datos = dict(fila.reintento or {})
        if not (datos.get("telefono") and datos.get("plantilla")):
            continue
        # reclamar ANTES de llamar a Meta
        datos["intentos"] = int(datos.get("intentos") or 1) + 1
        datos["ultimo"] = ahora.isoformat()
        fila.reintento = datos
        flag_modified(fila, "reintento")
        db.commit()
        cuenta["revisados"] += 1
        try:
            envio = await whatsapp.enviar_plantilla_evaluador(
                datos["telefono"], datos["plantilla"], list(datos.get("parametros") or []), datos.get("texto") or "")
        except Exception as ex:  # noqa: BLE001 — un aviso caído nunca tumba el job
            envio = {"enviado": False, "detalle": f"error: {ex}"}
        if envio.get("enviado"):
            fila.enviado, fila.estado = True, "enviado"
            fila.detalle = f"Enviado al reintentar ({datos['intentos']} intentos): {envio.get('detalle', '')}"[:2000]
            _actualizar_trazabilidad(db, fila, envio, "enviado")
            cuenta["enviados"] += 1
        elif envio.get("estado") == "pendiente":
            fila.detalle = str(envio.get("detalle") or "")
            cuenta["pendientes"] += 1
        else:
            fila.estado = "fallido"
            fila.detalle = str(envio.get("detalle") or "")
            _actualizar_trazabilidad(db, fila, envio, "fallido")
            cuenta["fallidos"] += 1
        db.commit()
    return cuenta


async def job_reintentar() -> None:
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        r = await reintentar(db)
        if r["revisados"]:
            print(f"[whatsapp][HSM] reintento de plantillas pendientes: {r}", flush=True)
    except Exception as ex:  # noqa: BLE001
        db.rollback()
        print(f"[whatsapp][HSM] reintento de plantillas pendientes: {ex}", flush=True)
    finally:
        db.close()
