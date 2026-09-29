"""Métricas que cruzan el Módulo 1 (reclutamiento) y el Módulo 2 (contratación).

Es la vista que demuestra que ambos módulos son un solo flujo: de candidato
captado a colaborador dado de alta, con el embudo real de la base.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..deps import cuenta_actual, usuario_actual
from ..database import get_db
from ..models import ETAPAS_CANDIDATO, Candidato, Cuenta, Entrevista, Expediente, Postulacion, Vacante
from ..services import conteos

router = APIRouter(prefix="/metricas", tags=["metricas"], dependencies=[Depends(usuario_actual)])


def _postulaciones(db: Session, cuenta_id: int):
    """Base de todo conteo de este módulo (Fase 2: se cuentan POSTULACIONES, no personas) —
    nunca cuenta postulaciones de Modo Prueba."""
    return db.query(Postulacion).filter(Postulacion.es_prueba.is_(False), Postulacion.cuenta_id == cuenta_id)


@router.get("/pipeline")
def pipeline(db: Session = Depends(get_db), cuenta: Cuenta = Depends(cuenta_actual)):
    """Embudo de punta a punta: captación → prefiltro → entrevista → expediente → alta."""
    # Fase 2: el embudo cuenta POSTULACIONES (una persona con 2 vacantes son 2 en el embudo).
    total_candidatos = _postulaciones(db, cuenta.id).count()
    # 2026-09-20 (B4): "por etapa" sale de `services.conteos.por_etapa` — EXACTAMENTE la misma base que el
    # Kanban y el embudo de cada vacante (activas, personas no eliminadas, incluye Modo Prueba), para que el
    # pipeline global, la tarjeta de la vacante y la lista de candidatos coincidan siempre.
    por_etapa = conteos.por_etapa(db, cuenta.id)
    por_estado = dict(
        _postulaciones(db, cuenta.id).with_entities(Postulacion.estado, func.count(Postulacion.id)).group_by(Postulacion.estado).all()
    )
    # "por fuente" sigue siendo la fuente de la PERSONA (Formulario/WhatsApp/OCC/...), que es lo
    # que el frontend grafica; se cuenta por postulación.
    por_fuente = dict(
        _postulaciones(db, cuenta.id)
        .join(Candidato, Postulacion.candidato_id == Candidato.id)
        .with_entities(Candidato.fuente, func.count(Postulacion.id))
        .group_by(Candidato.fuente)
        .all()
    )
    prefiltrados = _postulaciones(db, cuenta.id).filter(Postulacion.prefiltro_completo.is_(True)).count()
    entrevistas_evaluadas = (
        db.query(Entrevista)
        .join(Candidato, Entrevista.candidato_id == Candidato.id)
        .filter(Entrevista.estado == "evaluada", Candidato.cuenta_id == cuenta.id)
        .count()
    )
    expedientes = (
        db.query(Expediente)
        .join(Candidato, Expediente.candidato_id == Candidato.id)
        .filter(Candidato.cuenta_id == cuenta.id)
        .all()
    )
    altas = [e for e in expedientes if e.estado == "alta"]

    embudo = [
        {"etapa": "Candidatos", "valor": total_candidatos},
        {"etapa": "Prefiltro IA", "valor": prefiltrados},
        {"etapa": "Entrevista", "valor": entrevistas_evaluadas},
        {"etapa": "Expediente", "valor": len(expedientes)},
        {"etapa": "Alta", "valor": len(altas)},
    ]
    base = embudo[0]["valor"] or 1
    for paso in embudo:
        paso["pct"] = round(paso["valor"] / base * 100)

    hace_7d = datetime.now(timezone.utc) - timedelta(days=7)
    return {
        "vacantes": {
            "total": db.query(Vacante).filter(Vacante.cuenta_id == cuenta.id, Vacante.estado != "Eliminada").count(),
            "publicadas": db.query(Vacante).filter(Vacante.estado == "Publicada", Vacante.cuenta_id == cuenta.id).count(),
            "borradores": db.query(Vacante).filter(Vacante.estado == "Borrador", Vacante.cuenta_id == cuenta.id).count(),
        },
        "candidatos": {
            "total": total_candidatos,
            "nuevos_7d": _postulaciones(db, cuenta.id).filter(Postulacion.creado_en >= hace_7d).count(),
            "por_etapa": {e: por_etapa.get(e, 0) for e in ETAPAS_CANDIDATO},
            "por_estado": por_estado,
            "por_fuente": por_fuente,
            "sin_consentimiento": _postulaciones(db, cuenta.id).filter(Postulacion.consentimiento.is_(False)).count(),
        },
        "contratacion": {
            "expedientes": len(expedientes),
            "en_integracion": sum(1 for e in expedientes if e.estado == "integracion"),
            "listos_para_alta": sum(1 for e in expedientes if e.progreso == 100 and e.estado != "alta"),
            "altas": len(altas),
            "documentos_pendientes": sum(len(e.pendientes) for e in expedientes),
            "documentos_por_revisar": sum(len(e.por_revisar) for e in expedientes),
        },
        "embudo": embudo,
        # cuellos de botella accionables para RH, con la liga al módulo que los resuelve
        "acciones": _acciones(db, expedientes, cuenta.id),
    }


def _acciones(db: Session, expedientes, cuenta_id: int) -> list:
    salida = []

    por_decidir = _postulaciones(db, cuenta_id).filter(
        Postulacion.activa.is_(True), Postulacion.prefiltro_completo.is_(True),
        Postulacion.etapa == "Prefiltro", Postulacion.estado != "no_cumple",
    ).count()
    if por_decidir:
        salida.append({
            "modulo": 1,
            "tipo": "decision_pendiente",
            "cantidad": por_decidir,
            "texto": f"{por_decidir} candidato(s) prefiltrados esperan decisión de RH.",
            "ruta": "/dashboard/candidatos",
        })

    sin_consentimiento = _postulaciones(db, cuenta_id).filter(
        Postulacion.activa.is_(True), Postulacion.consentimiento.is_(False), Postulacion.etapa != "Prefiltro"
    ).count()
    if sin_consentimiento:
        salida.append({
            "modulo": 1,
            "tipo": "consentimiento",
            "cantidad": sin_consentimiento,
            "texto": f"{sin_consentimiento} candidato(s) avanzados sin consentimiento registrado (LFPDPPP).",
            "ruta": "/dashboard/candidatos",
        })

    por_revisar = sum(len(e.por_revisar) for e in expedientes)
    if por_revisar:
        salida.append({
            "modulo": 2,
            "tipo": "documentos_revision",
            "cantidad": por_revisar,
            "texto": f"{por_revisar} documento(s) marcados por la IA para revisión humana.",
            "ruta": "/dashboard/onboarding",
        })

    listos = [e for e in expedientes if e.progreso == 100 and e.estado != "alta"]
    if listos:
        salida.append({
            "modulo": 2,
            "tipo": "alta_pendiente",
            "cantidad": len(listos),
            "texto": f"{len(listos)} expediente(s) completos esperan autorización de alta.",
            "ruta": "/dashboard/onboarding",
        })

    return salida


# ------------------------------------------------------------
# Tablero de control principal (2026-09-28): SOLO datos reales, SIEMPRE filtrados por la Cuenta de la sesión.
# ------------------------------------------------------------

DIAS_CORTOS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
MESES_CORTOS = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")


def _utc(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _dia_mx(dt):
    from ..services.notificaciones import TZ_MEXICO

    return _utc(dt).astimezone(TZ_MEXICO).date()


def _modulo(db: Session, fn):
    """Los módulos de RH viven en tablas del paso NO fatal: si no existen, esa sección es None y el resto sigue."""
    try:
        return fn()
    except Exception:  # noqa: BLE001
        db.rollback()
        return None


@router.get("/tablero")
def tablero(db: Session = Depends(get_db), cuenta: Cuenta = Depends(cuenta_actual)):
    """Todo lo que pinta el Tablero de control. Cada consulta filtra por `cuenta.id` (multi-inquilino estricto);
    nada es inventado: sin datos, las series vienen vacías o en cero."""
    from ..models import Colaborador, Evaluacion
    from ..serial import hace
    from ..services.notificaciones import TZ_MEXICO

    ahora = datetime.now(timezone.utc)
    hoy = ahora.astimezone(TZ_MEXICO).date()
    dias = [hoy - timedelta(days=i) for i in range(6, -1, -1)]
    desde_7d = ahora - timedelta(days=8)

    visibles = conteos.postulaciones_visibles(db, cuenta.id)
    activos_pipeline = visibles.count()

    # --- actividad de los últimos 7 días: postulaciones nuevas y entrevistas (IA finalizadas + humanas del día) ---
    nuevas = [_dia_mx(f) for (f,) in _postulaciones(db, cuenta.id).filter(Postulacion.creado_en >= desde_7d).with_entities(Postulacion.creado_en).all()]
    ent_ia = [
        _dia_mx(f) for (f,) in db.query(Entrevista.finalizada_en)
        .join(Postulacion, Entrevista.postulacion_id == Postulacion.id)
        .filter(Postulacion.cuenta_id == cuenta.id, Entrevista.finalizada_en.isnot(None), Entrevista.finalizada_en >= desde_7d).all()
    ]
    # entrevistas humanas con cita en los últimos 7 días (Evaluaciones unificadas; las canceladas no cuentan)
    ent_h = [
        _dia_mx(f) for (f,) in db.query(Evaluacion.cita_fecha_hora)
        .filter(Evaluacion.cuenta_id == cuenta.id, Evaluacion.tipo == "entrevista_humana", Evaluacion.estado != "cancelada",
                Evaluacion.cita_fecha_hora.isnot(None), Evaluacion.cita_fecha_hora >= desde_7d, Evaluacion.cita_fecha_hora <= ahora).all()
    ]
    actividad = [
        {"dia": DIAS_CORTOS[d.weekday()], "fecha": d.isoformat(), "candidatos": sum(1 for x in nuevas if x == d), "entrevistas": sum(1 for x in ent_ia + ent_h if x == d)}
        for d in dias
    ]

    # --- fuentes de las postulaciones activas ---
    fuentes = [
        {"name": f or "Sin fuente", "value": int(n)}
        for f, n in visibles.with_entities(Candidato.fuente, func.count(Postulacion.id)).group_by(Candidato.fuente).order_by(func.count(Postulacion.id).desc()).all()
    ]

    # --- colaboradores y contrataciones ---
    colaboradores_activos = (
        db.query(Colaborador).filter(Colaborador.cuenta_id == cuenta.id, Colaborador.eliminado_en.is_(None), Colaborador.activo.is_(True)).count()
    )
    expedientes_cuenta = (
        db.query(Expediente).join(Candidato, Expediente.candidato_id == Candidato.id).filter(Candidato.cuenta_id == cuenta.id).all()
    )
    altas = [e for e in expedientes_cuenta if e.estado == "alta" and e.alta_fecha]
    altas_30d = sum(1 for e in altas if _utc(e.alta_fecha) >= ahora - timedelta(days=30))
    # tiempo de contratación: días de la postulación al alta, promedio por mes (últimos 6 meses con altas)
    por_mes: dict = {}
    for e in altas:
        p = e.postulacion
        f = _utc(e.alta_fecha)
        if not p or not p.creado_en or f < ahora - timedelta(days=186):
            continue
        por_mes.setdefault((f.year, f.month), []).append(max(0, (f - _utc(p.creado_en)).days))
    tiempo = [{"mes": MESES_CORTOS[m - 1], "dias": round(sum(v) / len(v))} for (_a, m), v in sorted(por_mes.items())]
    dias_todos = [d for v in por_mes.values() for d in v]

    vacantes_publicadas = db.query(Vacante).filter(Vacante.cuenta_id == cuenta.id, Vacante.estado == "Publicada").count()

    # --- recientes ---
    recientes = [
        {
            "id": p.codigo, "nombre": p.nombre, "puesto": p.vacante.titulo if p.vacante else "",
            "ubicacion": (p.candidato.ubicacion if p.candidato else "") or "", "fuente": p.fuente, "estado": p.estado,
            "score": p.score or 0, "etapa": p.etapa, "aplicado": hace(p.creado_en),
        }
        for p in visibles.order_by(Postulacion.creado_en.desc()).limit(5).all()
    ]

    return {
        "cuentaId": cuenta.id,
        "generado": ahora.isoformat(),
        "kpis": {
            "candidatosActivos": activos_pipeline,
            "candidatosNuevos7d": sum(r["candidatos"] for r in actividad),
            "colaboradoresActivos": colaboradores_activos,
            "altas30d": altas_30d,
            "vacantesPublicadas": vacantes_publicadas,
        },
        "actividad": actividad,
        "fuentes": fuentes,
        "tiempoContratacion": {"serie": tiempo, "promedio": round(sum(dias_todos) / len(dias_todos)) if dias_todos else None},
        "recientes": recientes,
        "pendientesRH": _acciones(db, expedientes_cuenta, cuenta.id),
        "onboarding": _modulo(db, lambda: _tablero_onboarding(db, cuenta.id)),
        "evaluaciones": _modulo(db, lambda: _tablero_evaluaciones(db, cuenta.id)),
        "desempeno": _modulo(db, lambda: _tablero_desempeno(db, cuenta.id)),
        "clima": _modulo(db, lambda: _tablero_clima(db, cuenta.id)),
    }


def _tablero_onboarding(db: Session, cuenta_id: int) -> dict:
    """Onboardings ACTIVOS = en etapa Onboarding (postulación viva, o ya dada de alta y sin cerrar), sin «No ingresó».
    Avance = (documentos aprobados + tareas realizadas) / (documentos aplicables + tareas vigentes)."""
    from sqlalchemy import or_

    from ..models import TareaOnboarding
    from ..services import onboarding as onb

    exps = (
        db.query(Expediente)
        .join(Candidato, Expediente.candidato_id == Candidato.id)
        .join(Postulacion, Expediente.postulacion_id == Postulacion.id)
        .filter(
            Candidato.cuenta_id == cuenta_id, Candidato.eliminado_en.is_(None), Postulacion.cuenta_id == cuenta_id,
            Postulacion.etapa == "Onboarding", or_(Postulacion.activa.is_(True), Postulacion.motivo_cierre == "contratado"),
            Expediente.onboarding_cerrado_en.is_(None), Expediente.no_ingreso_en.is_(None),
        )
        .all()
    )
    ids = [e.id for e in exps]
    tareas: dict = {}
    if ids:
        for t in db.query(TareaOnboarding).filter(TareaOnboarding.cuenta_id == cuenta_id, TareaOnboarding.expediente_id.in_(ids)).all():
            tareas.setdefault(t.expediente_id, []).append(t)
    avances, atrasadas, sin_tareas, listos_cierre = [], 0, 0, 0
    for e in exps:
        r = onb.resumen_tablero(e, tareas.get(e.id, []))
        total = r["documentos"]["total"] + r["tareas"]["total"]
        hechos = len(r["documentos"]["aprobados"]) + r["tareas"]["realizadas"]
        avances.append(round(hechos / total * 100) if total else 0)
        atrasadas += r["tareas"]["atrasadas"]
        sin_tareas += 0 if r["iniciado"] else 1
        listos_cierre += 1 if r["puedeCerrar"] else 0
    return {
        "activos": len(exps),
        "tareasAtrasadas": atrasadas,
        "avancePromedio": round(sum(avances) / len(avances)) if avances else None,
        "sinTareas": sin_tareas,
        "listosParaCerrar": listos_cierre,
    }


def _tablero_evaluaciones(db: Session, cuenta_id: int) -> dict:
    """Evaluaciones (modelo unificado, 2026-09-29) de postulaciones ACTIVAS de personas no eliminadas. Los cinco
    estados + la condición de consentimiento pendiente + los resultados nuevos que RH aún no abre."""
    from ..models import Evaluacion

    base = (
        db.query(Evaluacion)
        .join(Postulacion, Evaluacion.postulacion_id == Postulacion.id)
        .join(Candidato, Postulacion.candidato_id == Candidato.id)
        .filter(Evaluacion.cuenta_id == cuenta_id, Postulacion.cuenta_id == cuenta_id, Postulacion.activa.is_(True), Candidato.eliminado_en.is_(None))
    )
    por = {e: int(n) for e, n in base.with_entities(Evaluacion.estado, func.count(Evaluacion.id)).group_by(Evaluacion.estado).all()}
    return {
        "pendientes": por.get("pendiente", 0),
        "enEsperaConsentimiento": base.filter(Evaluacion.estado == "pendiente", Evaluacion.consentimiento == "pendiente").count(),
        "realizadasSinResultado": por.get("realizada_sin_resultado", 0),
        "conResultado": por.get("con_resultado", 0),
        "nuevosResultados": base.filter(Evaluacion.estado == "con_resultado", Evaluacion.resultado_visto_en.is_(None),
                                        Evaluacion.registrada_via.in_(["liga_evaluador", "proveedor"])).count(),
        "noRealizadas": por.get("no_realizada", 0),
        "porEstado": por,
    }


def _tablero_desempeno(db: Session, cuenta_id: int) -> dict:
    """Ciclos de Desempeño ACTIVOS (En curso): avance = personas completadas ÷ incluidas (motor único)."""
    from ..models import CicloDesempeno, normalizar_estado_ciclo
    from ..services import desempeno_calculo as calc

    ciclos = db.query(CicloDesempeno).filter(CicloDesempeno.cuenta_id == cuenta_id).all()
    activos = [c for c in ciclos if normalizar_estado_ciclo(c.estado) == "en_curso"]
    detalle = [{"id": c.codigo, "nombre": c.nombre, "periodo": c.periodo or "", **calc.avance(c)} for c in activos]
    incl = sum(d["incluidas"] for d in detalle)
    comp = sum(d["completadas"] for d in detalle)
    return {
        "activos": len(activos),
        "borradores": sum(1 for c in ciclos if normalizar_estado_ciclo(c.estado) == "borrador"),
        "personasIncluidas": incl,
        "personasCompletadas": comp,
        "avance": round(comp / incl * 100) if incl else None,
        "ciclos": sorted(detalle, key=lambda d: d["porcentaje"])[:5],
    }


def _tablero_clima(db: Session, cuenta_id: int) -> dict:
    from ..models import MedicionClima

    filas = db.query(MedicionClima.estado, func.count(MedicionClima.id)).filter(MedicionClima.cuenta_id == cuenta_id).group_by(MedicionClima.estado).all()
    por = {e: int(n) for e, n in filas}
    return {"abiertas": por.get("abierta", 0), "borradores": por.get("borrador", 0), "cerradas": por.get("cerrada", 0)}
