"""Webhooks de proveedores externos (2026-09-29): Dropbox Sign y Psicométricas.mx.

Rutas EXACTAS (registrarlas así en cada proveedor):
  * POST /api/webhooks/dropbox        — callback de la API app de Dropbox Sign.
  * POST /api/webhooks/psicometricas  — «Registros de API» de Psicométricas.mx (…?secreto=PSICOMETRICAS_WEBHOOK_SECRET).
Ambas contestan de inmediato y procesan en segundo plano (mismo patrón que el webhook de WhatsApp), porque los
proveedores reintentan si tardas. Nada se guarda sin verificar:
  * Dropbox Sign: HMAC-SHA256 del evento con la API key; sin firma válida → 401 (y no se procesa).
  * Psicométricas.mx: su aviso no trae firma → secreto propio en la URL (si está configurado) y, sobre todo, cada
    aviso se CONFIRMA consultando su API antes de guardar el resultado.
"""

import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse

from ..config import settings
from ..database import SessionLocal
from ..services import dropbox_sign as dsign

log = logging.getLogger("redhuman.webhooks")
router = APIRouter(tags=["webhooks-proveedores"])

RESPUESTA_DROPBOX = "Hello API Event Received"  # texto EXACTO que exige Dropbox Sign


async def _payload(request: Request) -> dict:
    """JSON directo o multipart/form-urlencoded (Dropbox manda el evento en el campo `json`)."""
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype or "application/x-www-form-urlencoded" in ctype:
        form = await request.form()
        if form.get("json"):
            return json.loads(str(form.get("json")))
        return {k: str(v) for k, v in form.items()}
    cuerpo = (await request.body()).decode("utf-8", "ignore").strip()
    return json.loads(cuerpo) if cuerpo else {}


# ---------------- Dropbox Sign ----------------

def _procesar_dropbox(evento: dict) -> None:
    from .firmas import procesar_evento_firma

    db = SessionLocal()
    try:
        r = procesar_evento_firma(db, evento)
        log.info("[DROPBOX SIGN] %s → %s", (evento.get("event") or {}).get("event_type"), r)
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("[DROPBOX SIGN] no se pudo procesar el evento")
    finally:
        db.close()


@router.post("/api/webhooks/dropbox", response_class=PlainTextResponse)
@router.post("/webhooks/dropbox", response_class=PlainTextResponse, include_in_schema=False)
async def webhook_dropbox(request: Request, background: BackgroundTasks):
    try:
        evento = await _payload(request)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(400, "Evento inválido.")
    if not dsign.configurado():
        raise HTTPException(503, "Dropbox Sign no está configurado en este servidor.")
    if not dsign.evento_valido(evento):
        log.warning("[DROPBOX SIGN] evento con firma inválida rechazado")
        raise HTTPException(401, "Firma del evento inválida.")
    tipo = (evento.get("event") or {}).get("event_type")
    if tipo != "callback_test":
        background.add_task(_procesar_dropbox, evento)
    return RESPUESTA_DROPBOX


# ---------------- Psicométricas.mx ----------------

def _procesar_psicometricas(clave: str, tipo: str, datos: dict) -> None:
    from ..models import Evaluacion, registrar
    from ..services import evaluaciones as sev
    from ..services import psicometricas as psi

    db = SessionLocal()
    try:
        # Evaluaciones unificadas (2026-09-29): la clave del proveedor vive en `evaluaciones` (migrada tal cual).
        evs = db.query(Evaluacion).filter(Evaluacion.clave_proveedor == clave).all()
        for ev in evs:
            if tipo == "termina_practica":
                sev.evento(db, ev, "envio", "Psicométricas.mx", "proveedor",
                           nota=f"El candidato terminó la práctica de {datos.get('nombre_prueba') or 'la prueba'}.")
                continue
            try:
                r = sev.sincronizar_psicometricas(db, ev)
            except psi.PsicometricasError as ex:
                r = f"error: {ex}"
            registrar(db, "psicometricas", "evaluacion_webhook", "evaluaciones", ev.codigo, {"tipo": tipo, "resultado": r})
        db.commit()
        log.info("[PSICOMETRICAS] %s %s → %d evaluación(es)", tipo, clave, len(evs))
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("[PSICOMETRICAS] no se pudo procesar el aviso")
    finally:
        db.close()


@router.post("/api/webhooks/psicometricas")
@router.post("/webhooks/psicometricas", include_in_schema=False)
async def webhook_psicometricas(request: Request, background: BackgroundTasks, secreto: str = ""):
    if settings.psicometricas_webhook_secret and not hmac.compare_digest(secreto or "", settings.psicometricas_webhook_secret):
        raise HTTPException(401, "Secreto inválido.")
    try:
        datos = await _payload(request)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(400, "Aviso inválido.")
    clave = str(datos.get("clave") or "").strip()
    tipo = str(datos.get("type") or "").strip()
    estado = str(datos.get("estado") or datos.get("status") or "").strip().lower()
    if estado == "completada":  # por si la cuenta reporta el estado en texto
        tipo = "termina_prueba"
    if not clave or tipo not in ("termina_prueba", "termina_practica"):
        return {"ok": True, "ignorado": True}
    background.add_task(_procesar_psicometricas, clave, tipo, datos)
    return {"ok": True}
