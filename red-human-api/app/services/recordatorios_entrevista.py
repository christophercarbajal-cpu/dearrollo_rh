"""
2026-09-19 — Recordatorio automático de citas (Evaluaciones unificadas desde 2026-09-29).

`revisar_recordatorios_entrevista` corre cada 10 min (lifespan, app/main.py): para cada evaluación Pendiente con
cita (entrevista humana u otro tipo; postulación activa; sin bloqueo de consentimiento) cuya fecha cae dentro de las
próximas `ConfiguracionSistema.recordatorio_entrevista_horas` (default 24; 0 = apagado) y que aún no tiene
`recordatorio_enviado_en`, dispara `recordatorio_evaluacion` (regla de la Cuenta; default candidato y evaluador por
correo + WhatsApp) con las mismas plantillas que el recordatorio manual.
Idempotente entre workers: reclama `recordatorio_enviado_en` con UPDATE condicional antes de enviar.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .. import fechas
from ..database import SessionLocal
from ..models import Evaluacion, Postulacion, registrar
from . import evaluaciones as sev
from .configuracion import obtener


def _reclamar(db: Session, ev: Evaluacion, ahora: datetime) -> bool:
    filas = (
        db.query(Evaluacion)
        .filter(Evaluacion.id == ev.id, Evaluacion.recordatorio_enviado_en.is_(None))
        .update({Evaluacion.recordatorio_enviado_en: ahora}, synchronize_session=False)
    )
    db.commit()
    return filas == 1


def pendientes_de_recordatorio(db: Session, ahora: datetime, horas: int):
    limite = ahora + timedelta(hours=horas)
    return (
        db.query(Evaluacion)
        .join(Postulacion, Postulacion.id == Evaluacion.postulacion_id)
        .filter(
            Evaluacion.cita_fecha_hora.isnot(None), Evaluacion.cita_fecha_hora > ahora, Evaluacion.cita_fecha_hora <= limite,
            Evaluacion.recordatorio_enviado_en.is_(None), Evaluacion.estado == "pendiente",
            Evaluacion.consentimiento.notin_(["pendiente", "rechazado"]), Postulacion.activa.is_(True),
        )
        .all()
    )


async def revisar_recordatorios_entrevista() -> int:
    ahora = datetime.now(timezone.utc)
    enviados = 0
    with SessionLocal() as db:
        cfg = obtener(db)
        horas = int(cfg.recordatorio_entrevista_horas or 0)
        if horas <= 0:
            return 0
        for ev in pendientes_de_recordatorio(db, ahora, horas):
            p = db.get(Postulacion, ev.postulacion_id)
            if p is None or not _reclamar(db, ev, ahora):
                continue
            db.refresh(ev)
            resultados = await sev.notificar(db, ev, p, "recordatorio_evaluacion", "sistema")
            sev.evento(db, ev, "recordatorio", "sistema", automatico=True)
            registrar(db, "sistema", "recordatorio_entrevista_automatico", "postulacion", p.codigo,
                      {"evaluacion": ev.codigo, "fecha": fechas.iso(ev.cita_fecha_hora), "notificaciones": resultados})
            db.commit()
            enviados += 1
    if enviados:
        print(f"[recordatorios] {enviados} recordatorio(s) de cita enviados.", flush=True)
    return enviados
