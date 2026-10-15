"""
Cambio 56 (2026-10-15): la Psicometría digital cuya invitación lleva MÁS DE 24 h sin completarse recibe UN recordatorio por
WhatsApp (cascada: texto libre → plantilla `META_PLANTILLA_RECORDATORIO_PSICOMETRIA` → «Pendiente de plantilla» que el job de
plantillas reintenta); sin teléfono, o si el WhatsApp falla de forma definitiva, sale por correo. Tope
`PSICOMETRICAS_RECORDATORIOS_MAX` (1). Ventana `PSICOMETRICAS_RECORDATORIO_HORAS` (24; 0 = usa los días de antes).

2026-10-07 — Recordatorio automático de psicometría pendiente (evento «recordatorio_psicometria»).

`revisar_recordatorios_psicometria` corre cada hora (lifespan, app/main.py): para cada psicometría del proveedor
integrado (Psicométricas.mx) con clave, postulación activa y estado visible «Pendiente» (asignada y sin inicio
confirmado) cuyo envío —o último recordatorio— tenga al menos `PSICOMETRICAS_RECORDATORIO_DIAS` días (0 = apagado),
manda al candidato el MISMO aviso (portal + clave + pasos) con el texto de recordatorio, por los canales de la regla
de la Cuenta. Tope: `PSICOMETRICAS_RECORDATORIOS_MAX` automáticos por evaluación.

NUNCA consulta la API del proveedor (el saldo se comparte con producción): decide con lo que Red Human ya sabe
(`enviada_en`, `iniciada_en` — que llega por webhook o «Sincronizar»). Idempotente entre workers: reclama
`recordatorio_psicometria_en` con UPDATE condicional antes de enviar.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import Evaluacion, Postulacion, registrar
from . import evaluaciones as sev


def candidatas(db: Session, ahora: datetime, dias: float, tope: int):
    corte = ahora - timedelta(days=dias)
    filas = (
        db.query(Evaluacion)
        .join(Postulacion, Postulacion.id == Evaluacion.postulacion_id)
        .filter(
            Evaluacion.tipo == "psicometrica", Evaluacion.forma == "integrada", Evaluacion.estado == "pendiente",
            Evaluacion.clave_proveedor != "", Evaluacion.iniciada_en.is_(None), Evaluacion.enviada_en.isnot(None),
            Evaluacion.enviada_en <= corte, Evaluacion.recordatorios_psicometria < tope,
            or_(Evaluacion.recordatorio_psicometria_en.is_(None), Evaluacion.recordatorio_psicometria_en <= corte),
            Postulacion.activa.is_(True),
        )
        .all()
    )
    return [ev for ev in filas if (sev.estado_proveedor(ev) or ("",))[0] == "pendiente" and sev.usa_psicometricas(ev)]


def _reclamar(db: Session, ev: Evaluacion, ahora: datetime) -> bool:
    previo = ev.recordatorio_psicometria_en
    q = db.query(Evaluacion).filter(Evaluacion.id == ev.id)
    q = q.filter(Evaluacion.recordatorio_psicometria_en.is_(None)) if previo is None else q.filter(Evaluacion.recordatorio_psicometria_en == previo)
    filas = q.update({Evaluacion.recordatorio_psicometria_en: ahora,
                      Evaluacion.recordatorios_psicometria: Evaluacion.recordatorios_psicometria + 1}, synchronize_session=False)
    db.commit()
    return filas == 1


def parametros_recordatorio(p: Postulacion) -> list:
    from .prefiltro_conversacional import empresa_de

    nombre = (p.nombre or "").split(" ")[0]
    valores = {"nombre": "" if nombre.startswith("Candidato") else nombre, "empresa": empresa_de(p) or "Red Human",
               "puesto": (p.vacante.titulo if p.vacante else "") or "la vacante"}
    claves = [k.strip().lower() for k in (settings.meta_plantilla_recordatorio_psicometria_params or "").split(",") if k.strip()]
    return [valores[k] or "-" for k in claves if k in valores]


async def enviar_recordatorio(db: Session, ev: Evaluacion, p: Postulacion) -> list:
    """Cambio 56: por WhatsApp con plantilla (cascada); correo solo si no hay teléfono o el WhatsApp falló sin remedio."""
    from . import psicometricas as psi
    from .mensajeria import de_cuenta
    from .reintento_plantillas import registrar_envio
    from .prefiltro_conversacional import empresa_de
    from .whatsapp import enviar_en_cascada

    resultados = []
    if p.telefono:
        liga = sev._url_proveedor(ev) or psi.url_candidato(ev.clave_proveedor) or psi.PORTAL_SUSTENTANTE
        nombre = ((p.nombre or "").split(" ")[0]) if p.nombre and not p.nombre.startswith("Candidato") else ""
        texto = psi.mensaje_candidato(nombre, ev.clave_proveedor, liga, empresa_de(p), p.vacante.titulo if p.vacante else "",
                                      recordatorio=True)
        try:
            with de_cuenta(p.cuenta_id):
                r = await enviar_en_cascada(p.telefono, texto, (settings.meta_plantilla_recordatorio_psicometria or "").strip()
                                            or "recordatorio_psicometria_rh", parametros_recordatorio(p), settings.meta_plantilla_idioma)
        except Exception as ex:  # noqa: BLE001
            r = {"enviado": False, "detalle": str(ex)[:200]}
        try:
            registrar_envio(db, p, "recordatorio_psicometria", r, "recordatorio_psicometria")
        except Exception:  # noqa: BLE001
            pass
        resultados.append({"destinatario": "candidato", "canal": "whatsapp", "destino": p.telefono, "enviado": bool(r.get("enviado")),
                           "detalle": str(r.get("detalle") or r.get("error") or ""), "estado": r.get("estado", "")})
        if r.get("enviado") or r.get("estado") == "pendiente":
            return resultados
    if p.correo:
        resultados += await sev.notificar_psicometria(db, ev, p, "sistema", evento_notificacion="recordatorio_psicometria")
    return resultados


async def revisar_recordatorios_psicometria() -> int:
    horas = int(settings.psicometricas_recordatorio_horas or 0)
    dias = horas / 24 if horas > 0 else int(settings.psicometricas_recordatorio_dias or 0)
    tope = int(settings.psicometricas_recordatorios_max or 0)
    if dias <= 0 or tope <= 0:
        return 0
    ahora = datetime.now(timezone.utc)
    enviados = 0
    with SessionLocal() as db:
        for ev in candidatas(db, ahora, dias, tope):
            p = db.get(Postulacion, ev.postulacion_id)
            if p is None or not _reclamar(db, ev, ahora):
                continue
            db.refresh(ev)
            try:
                resultados = await enviar_recordatorio(db, ev, p)
            except Exception as ex:  # noqa: BLE001 — un envío caído nunca tumba el job
                resultados = [{"destinatario": "candidato", "canal": "", "destino": "", "enviado": False, "detalle": str(ex)[:200]}]
            sev.evento(db, ev, "recordatorio", "sistema", automatico=True, a_quien="candidato")
            registrar(db, "sistema", "recordatorio_psicometria_automatico", "postulacion", p.codigo,
                      {"evaluacion": ev.codigo, "numero": ev.recordatorios_psicometria, "notificaciones": resultados})
            db.commit()
            enviados += 1
    if enviados:
        print(f"[recordatorios] {enviados} recordatorio(s) de psicometría pendiente enviados.", flush=True)
    return enviados
