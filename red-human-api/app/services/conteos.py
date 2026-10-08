"""Conteos de candidatos por etapa — ÚNICA fuente de verdad (2026-09-20, Bloque 4).

`Postulacion.etapa` es la única columna que dice en qué etapa está un candidato. Todo contador
(tarjeta/detalle de la vacante, pipeline global, Kanban) sale de la MISMA base:

    postulaciones ACTIVAS (`Postulacion.activa`) de la Cuenta, cuya persona no está eliminada
    (`Candidato.eliminado_en IS NULL`), incluidas las de Modo Prueba (el Kanban las muestra).

Esa es exactamente la base de `GET /candidatos` sin filtros, así que la suma por etapa de una vacante
== número de tarjetas del Kanban filtrado por esa vacante, y el pipeline global == Kanban completo.
"""

from datetime import datetime
from typing import Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Query, Session

from ..models import Candidato, Postulacion


def postulaciones_visibles(db: Session, cuenta_id: int, vacante_id: Optional[int] = None) -> Query:
    """Base común (ver docstring del módulo). Mismo filtro que el Kanban por defecto."""
    q = (
        db.query(Postulacion)
        .join(Candidato, Postulacion.candidato_id == Candidato.id)
        .filter(Postulacion.cuenta_id == cuenta_id, Candidato.eliminado_en.is_(None), Postulacion.activa.is_(True))
    )
    if vacante_id is not None:
        q = q.filter(Postulacion.vacante_id == vacante_id)
    return q


def por_etapa(db: Session, cuenta_id: int, vacante_id: Optional[int] = None) -> Dict[str, int]:
    """{etapa: n} de postulaciones activas — Cuenta completa o una vacante."""
    filas = (
        postulaciones_visibles(db, cuenta_id, vacante_id)
        .with_entities(Postulacion.etapa, func.count(Postulacion.id))
        .group_by(Postulacion.etapa)
        .all()
    )
    return {etapa: int(n) for etapa, n in filas}


def por_estado(db: Session, cuenta_id: int, vacante_id: Optional[int] = None) -> Dict[str, int]:
    """{estado del agente: n} sobre la misma base (para la tarjeta de la vacante)."""
    filas = (
        postulaciones_visibles(db, cuenta_id, vacante_id)
        .with_entities(Postulacion.estado, func.count(Postulacion.id))
        .group_by(Postulacion.estado)
        .all()
    )
    return {estado: int(n) for estado, n in filas}


def total(db: Session, cuenta_id: int, vacante_id: Optional[int] = None) -> int:
    return postulaciones_visibles(db, cuenta_id, vacante_id).count()


def resumen_por_vacante(db: Session, cuenta_id: int, desde_nuevos: datetime) -> Dict[int, dict]:
    """Hotfix concurrencia 2026-09-24: los contadores de TODAS las vacantes de la Cuenta en tres
    consultas agrupadas (antes eran ~5 por vacante en el listado). MISMA base `postulaciones_visibles`,
    así cada número sigue siendo idéntico a `por_etapa`/`por_estado`/`total` de esa vacante.
    Regresa {vacante_id: {"etapas", "estados", "total", "nuevos"}}; una vacante sin postulaciones no aparece."""
    base = postulaciones_visibles(db, cuenta_id).filter(Postulacion.vacante_id.isnot(None))
    res: Dict[int, dict] = {}

    def fila(vid: int) -> dict:
        return res.setdefault(vid, {"etapas": {}, "estados": {}, "total": 0, "nuevos": 0})

    for vid, etapa, n in (
        base.with_entities(Postulacion.vacante_id, Postulacion.etapa, func.count(Postulacion.id))
        .group_by(Postulacion.vacante_id, Postulacion.etapa)
        .all()
    ):
        f = fila(vid)
        f["etapas"][etapa] = int(n)
        f["total"] += int(n)
    for vid, estado, n in (
        base.with_entities(Postulacion.vacante_id, Postulacion.estado, func.count(Postulacion.id))
        .group_by(Postulacion.vacante_id, Postulacion.estado)
        .all()
    ):
        fila(vid)["estados"][estado] = int(n)
    for vid, n in (
        base.filter(Postulacion.creado_en >= desde_nuevos)
        .with_entities(Postulacion.vacante_id, func.count(Postulacion.id))
        .group_by(Postulacion.vacante_id)
        .all()
    ):
        fila(vid)["nuevos"] = int(n)
    return res


def metricas_tablero(db: Session, cuenta_id: int, vacante_id: Optional[int] = None) -> list:
    """Encabezado de cada columna del tablero de Candidatos (rediseño 2026-10-07, red-human-kanban-completo.md §3):

    - `total`: postulaciones ACTIVAS en la etapa (misma base que el Kanban y el resto de contadores).
    - `conversion_pct`: % de las postulaciones que llegaron a la etapa ANTERIOR y también llegaron a esta (null en
      Prefiltro). «Llegó» = su etapa (actual o en la que se cerró) es esta o una posterior: desde el pipeline de cinco
      columnas un descarte se queda en su columna, así que la etapa guardada es la más lejana alcanzada. Incluye cerradas.
    - `avg_days`: promedio de días que llevan en la etapa las ACTIVAS (desde `etapa_desde`; registros viejos sin ella
      cuentan desde su creación). null sin postulaciones.
    Solo informa: nunca escribe nada."""
    from datetime import timezone

    from ..models import ETAPAS_CANDIDATO

    activos = por_etapa(db, cuenta_id, vacante_id)
    todas = (
        db.query(Postulacion.etapa, Postulacion.activa, Postulacion.etapa_desde, Postulacion.creado_en)
        .join(Candidato, Postulacion.candidato_id == Candidato.id)
        .filter(Postulacion.cuenta_id == cuenta_id, Candidato.eliminado_en.is_(None))
    )
    if vacante_id is not None:
        todas = todas.filter(Postulacion.vacante_id == vacante_id)
    indice = {e: i for i, e in enumerate(ETAPAS_CANDIDATO)}
    alcanzaron = [0] * len(ETAPAS_CANDIDATO)
    dias: Dict[str, list] = {e: [] for e in ETAPAS_CANDIDATO}
    ahora = datetime.now(timezone.utc)
    for etapa, activa, desde, creado in todas.all():
        i = indice.get(etapa)
        if i is None:
            continue
        for j in range(i + 1):
            alcanzaron[j] += 1
        if activa:
            base = desde or creado
            if base is not None:
                if base.tzinfo is None:
                    base = base.replace(tzinfo=timezone.utc)
                dias[etapa].append(max((ahora - base).total_seconds(), 0) / 86400)
    salida = []
    for i, etapa in enumerate(ETAPAS_CANDIDATO):
        previo = alcanzaron[i - 1] if i else 0
        salida.append({
            "etapa": etapa,
            "total": activos.get(etapa, 0),
            "conversion_pct": round(100 * alcanzaron[i] / previo) if i and previo else None,
            "avg_days": round(sum(dias[etapa]) / len(dias[etapa]), 1) if dias[etapa] else None,
        })
    return salida
