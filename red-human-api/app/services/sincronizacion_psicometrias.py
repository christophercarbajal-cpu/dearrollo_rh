"""Recuperación automática de resultados del proveedor psicométrico (2026-10-08).

El resultado llega SOLO por el webhook del proveedor (`routers/webhooks_proveedores.py`). Si ese aviso llega y la
descarga falla (o su API aún no lo entrega), la evaluación queda con `sincronizacion.estado = fallida` y este job la
reintenta con espera creciente (1 h, 3 h, 12 h, 24 h; máximo `MAX_REINTENTOS_SINCRONIZACION`). NUNCA consulta
evaluaciones sin una falla confirmada: el saldo de la API (100 peticiones) se comparte con producción.
"""

from datetime import datetime, timezone

from ..database import SessionLocal
from ..models import Evaluacion, registrar
from . import evaluaciones as sev
from . import proceso as sproc
from .modulos_rh import disponible


def _vence(texto) -> bool:
    if not texto:
        return False
    try:
        cuando = datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    except ValueError:
        return True
    if cuando.tzinfo is None:
        cuando = cuando.replace(tzinfo=timezone.utc)
    return cuando <= datetime.now(timezone.utc)


async def reintentar() -> dict:
    if not disponible():
        return {"revisadas": 0}
    db = SessionLocal()
    resumen = {"revisadas": 0, "recuperadas": 0, "fallidas": 0}
    try:
        candidatas = (db.query(Evaluacion)
                      .filter(Evaluacion.clave_proveedor != "", Evaluacion.estado.in_(("pendiente", "realizada_sin_resultado")))
                      .all())
        for ev in candidatas:
            s = ev.sincronizacion or {}
            if s.get("estado") != "fallida" or not _vence(s.get("siguiente_en")):
                continue
            resumen["revisadas"] += 1
            r = sev.recuperar_resultado(db, ev, "Red Human (reintento automático)", "reintento")
            if r == "resultado_recibido":
                resumen["recuperadas"] += 1
            elif r == "en_curso":
                # el aviso de término ya llegó antes: seguir sin resultado cuenta como falla de recuperación
                sev.marcar_sincronizacion(db, ev, False, "Su API todavía no entrega el resultado.", "Red Human (reintento automático)")
                resumen["fallidas"] += 1
            elif r.startswith("error"):
                resumen["fallidas"] += 1
            registrar(db, "sistema", "evaluacion_sincronizacion_reintento", "evaluaciones", ev.codigo, {"resultado": r})
            db.commit()
            if r == "resultado_recibido":
                try:
                    await sproc.avanzar_seguro(db, sev.postulacion_de(db, ev))
                except sev.ErrorEvaluacion:
                    pass
    except Exception as ex:  # noqa: BLE001
        db.rollback()
        print(f"[sincronizacion_psicometrias] {ex}", flush=True)
    finally:
        db.close()
    return resumen
