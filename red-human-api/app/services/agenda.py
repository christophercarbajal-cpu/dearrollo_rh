"""
Zero-Touch fase 1 — seguimiento de videollamadas agendadas por el agente.

`revisar_videollamadas_noshow` corre cada 5 min desde el lifespan de FastAPI (ver app/main.py)
y rescata a los candidatos que agendaron una videollamada (herramienta agendar_videollamada,
ver services/ia.py) pero no llegaron: 15 minutos después de la hora acordada, nadie los movió
de la etapa "Entrevista IA" (ver models.ETAPAS_CANDIDATO). Es un mensaje de texto libre porque
el candidato ya nos escribió antes para llegar hasta aquí, así que la ventana de 24h de Meta
sigue abierta.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Mensaje, NotificacionEnviada, Postulacion, registrar
from .mensajeria import de_cuenta
from .whatsapp import enviar_mensaje

MINUTOS_TOLERANCIA_NOSHOW = 15

MENSAJE_RESCATE = "Hola, vi que no pudiste unirte a la sesión. ¿Te gustaría reagendar? 🙌"

# 2026-09-15 — idempotencia del aviso de no-show. Evento con el que queda en `notificaciones_enviadas`
# y ventana en la que NUNCA se repite para la misma postulación/persona, aunque el job corra en dos
# workers, se reinicie la API o el flag de la postulación se pierda.
EVENTO_REAGENDAR = "reagendar_noshow"
HORAS_SIN_REPETIR_NOSHOW = 24


def _utc(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def aviso_noshow_reciente(db: Session, p: Postulacion, horas: int = HORAS_SIN_REPETIR_NOSHOW) -> bool:
    """True si ya se mandó (o se intentó) el aviso de reagendar a esta persona en las últimas `horas`.
    Mira `notificaciones_enviadas` (evento reagendar_noshow) — la bitácora operativa que RH ve en
    GET /notificaciones/historial — y, por si acaso, el chat de la postulación."""
    corte = datetime.now(timezone.utc) - timedelta(hours=horas)
    reciente = (
        db.query(NotificacionEnviada)
        .filter(
            NotificacionEnviada.candidato_id == p.candidato_id,
            NotificacionEnviada.evento == EVENTO_REAGENDAR,
            NotificacionEnviada.creada_en >= corte,
        )
        .first()
    )
    if reciente:
        return True
    ultimo = (
        db.query(Mensaje)
        .filter(Mensaje.postulacion_id == p.id, Mensaje.rol == "assistant", Mensaje.texto == MENSAJE_RESCATE)
        .order_by(Mensaje.id.desc())
        .first()
    )
    return bool(ultimo and _utc(ultimo.creado_en) >= corte)


def _reclamar_aviso(db: Session, p: Postulacion) -> bool:
    """Marca el flag con un UPDATE condicional y commit ANTES de mandar nada: si otro worker (o una
    corrida anterior) ya lo reclamó, rowcount es 0 y este proceso no manda. Antes el flag se
    escribía después de enviar y se confirmaba al final del lote — dos workers de uvicorn (o un
    error entre el envío y el commit) producían el mensaje doble."""
    filas = (
        db.query(Postulacion)
        .filter(Postulacion.id == p.id, Postulacion.videollamada_aviso_noshow_enviado.is_(False))
        .update({Postulacion.videollamada_aviso_noshow_enviado: True}, synchronize_session=False)
    )
    db.commit()
    return filas == 1


async def revisar_videollamadas_noshow() -> int:
    """Busca citas vencidas sin aviso de rescate y les manda un mensaje. Regresa cuántas procesó.

    "No llegó" se infiere de que RH (o el propio agente) nunca sacó al candidato de la etapa
    'Entrevista' — no hay señal real de asistencia a la videollamada todavía (el mock no la
    tiene). El flag `videollamada_aviso_noshow_enviado` se marca SIEMPRE que se intenta, aun si
    el envío de WhatsApp falla, para no reintentar en bucle cada 5 min contra un número roto.
    """
    corte = datetime.now(timezone.utc) - timedelta(minutes=MINUTOS_TOLERANCIA_NOSHOW)
    procesados = 0
    with SessionLocal() as db:
        postulaciones = (
            db.query(Postulacion)
            .filter(
                Postulacion.activa.is_(True),
                Postulacion.videollamada_agendada_en.isnot(None),
                Postulacion.videollamada_agendada_en < corte,
                Postulacion.videollamada_aviso_noshow_enviado.is_(False),
                Postulacion.etapa == "Entrevista IA",
            )
            .all()
        )
        for p in postulaciones:
            # Idempotencia (2026-09-15): (1) ya hubo aviso en 24 h → se marca y no se repite;
            # (2) reclamar el flag con commit ANTES de enviar — el que no gana el UPDATE no manda.
            if aviso_noshow_reciente(db, p):
                p.videollamada_aviso_noshow_enviado = True
                db.commit()
                print(f"[noshow] {p.codigo}: aviso de reagendar ya enviado en las últimas {HORAS_SIN_REPETIR_NOSHOW} h; no se repite.")
                continue
            if not _reclamar_aviso(db, p):
                continue

            envio = {"enviado": False, "proveedor": "demo", "detalle": "sin teléfono"}
            if p.telefono:
                try:
                    with de_cuenta(p.cuenta_id):
                        envio = await enviar_mensaje(p.telefono, MENSAJE_RESCATE)
                except Exception as e:  # que WhatsApp falle no debe tumbar el job
                    print(f"[noshow-whatsapp-error] {p.codigo}: {e}")
                    envio = {"enviado": False, "proveedor": "error", "detalle": str(e)}

            db.add(Mensaje(
                candidato_id=p.candidato_id, postulacion_id=p.id, rol="assistant", texto=MENSAJE_RESCATE, canal="whatsapp",
                enviado=envio.get("enviado", False), wa_id=envio.get("wa_id", ""),
            ))
            # Queda en la bitácora operativa (misma tabla que el resto de notificaciones): es lo
            # que consulta aviso_noshow_reciente() para no repetirlo.
            db.add(NotificacionEnviada(
                cuenta_id=p.cuenta_id or 0, candidato_id=p.candidato_id, evento=EVENTO_REAGENDAR,
                destinatario_tipo="candidato", canal="whatsapp", destino=p.telefono or "",
                enviado=bool(envio.get("enviado")), detalle=str(envio.get("detalle", "")),
            ))
            # Mensaje saliente: no mueve la conversación del candidato (B1). Si contesta "sí,
            # reagendo", el webhook lo enruta a esta postulación y candidatos._quiere_reagendar
            # abre de nuevo la coordinación de la cita.
            registrar(
                db, "agente-ia", "aviso_noshow_enviado", "postulacion", p.codigo,
                {
                    "cita": p.videollamada_agendada_en.isoformat() if p.videollamada_agendada_en else None,
                    "whatsapp": envio,
                },
            )
            db.commit()
            procesados += 1

        if procesados:
            print(f"[noshow] {procesados} candidato(s) rescatado(s) por inasistencia.")

    return procesados
