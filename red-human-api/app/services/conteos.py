"""Conteos de candidatos por etapa — ÚNICA fuente de verdad (2026-09-20, Bloque 4).

`Postulacion.etapa` es la única columna que dice en qué etapa está un candidato. Todo contador
(tarjeta/detalle de la vacante, pipeline global, Kanban) sale de la MISMA base:

    postulaciones ACTIVAS (`Postulacion.activa`) de la Cuenta, cuya persona no está eliminada
    (`Candidato.eliminado_en IS NULL`), incluidas las de Modo Prueba (el Kanban las muestra).

Esa es exactamente la base de `GET /candidatos` sin filtros, así que la suma por etapa de una vacante
== número de tarjetas del Kanban filtrado por esa vacante, y el pipeline global == Kanban completo.
"""

import re
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Query, Session

from ..models import ETAPAS_CANDIDATO, Candidato, Postulacion


# ============================================================ fusión de duplicados (especificación 2026-10-10)
# Una PERSONA aparece UNA sola vez por vacante: postulaciones activas de la misma vacante cuyas fichas comparten
# teléfono (10 dígitos) o correo se FUSIONAN — se muestra la más avanzada (y, a igual etapa, la más antigua) y las demás
# quedan ocultas de tablero y contadores. Es visual/lógica: nada se borra ni se cierra, y la principal lista las
# fusionadas (`fusionadas`). Las de Modo Prueba no se fusionan (ahí repetir teléfono es a propósito).

def _tel(t: str) -> str:
    d = re.sub(r"\D", "", t or "")
    return d[-10:] if len(d) >= 10 else ""


def fusion_duplicados(db: Session, cuenta_id: int, vacante_id: Optional[int] = None) -> Tuple[Set[int], Dict[int, List[str]]]:
    """(ids de postulaciones ocultas, {id principal: [códigos fusionados]}) de la Cuenta (o de una vacante)."""
    q = (
        db.query(Postulacion.id, Postulacion.codigo, Postulacion.vacante_id, Postulacion.etapa, Postulacion.creado_en,
                 Postulacion.candidato_id, Candidato.telefono, Candidato.correo)
        .join(Candidato, Postulacion.candidato_id == Candidato.id)
        .filter(Postulacion.cuenta_id == cuenta_id, Candidato.eliminado_en.is_(None), Postulacion.activa.is_(True),
                Postulacion.vacante_id.isnot(None), Postulacion.es_prueba.is_(False), Candidato.es_prueba.is_(False))
    )
    if vacante_id is not None:
        q = q.filter(Postulacion.vacante_id == vacante_id)
    filas = q.all()
    padre: Dict[int, int] = {f.id: f.id for f in filas}

    def raiz(x: int) -> int:
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    vistos: Dict[tuple, int] = {}
    for f in filas:
        for clave in ((f.vacante_id, "t", _tel(f.telefono)), (f.vacante_id, "c", (f.correo or "").strip().lower())):
            if not clave[2]:
                continue
            if clave in vistos:
                a, b = raiz(vistos[clave]), raiz(f.id)
                if a != b:
                    padre[b] = a
            else:
                vistos[clave] = f.id
    grupos: Dict[int, list] = {}
    for f in filas:
        grupos.setdefault(raiz(f.id), []).append(f)
    indice = {e: i for i, e in enumerate(ETAPAS_CANDIDATO)}
    ocultos: Set[int] = set()
    fusion: Dict[int, List[str]] = {}
    for miembros in grupos.values():
        if len({m.candidato_id for m in miembros}) < 2:
            continue  # misma persona con una sola ficha: no es duplicado
        principal = sorted(miembros, key=lambda m: (-indice.get(m.etapa, 0), m.creado_en or datetime.min, m.id))[0]
        resto = [m for m in miembros if m.id != principal.id]
        ocultos.update(m.id for m in resto)
        fusion[principal.id] = [m.codigo for m in resto]
    return ocultos, fusion


def postulaciones_visibles(db: Session, cuenta_id: int, vacante_id: Optional[int] = None) -> Query:
    """Base común (ver docstring del módulo). Mismo filtro que el Kanban por defecto — sin los duplicados fusionados."""
    q = (
        db.query(Postulacion)
        .join(Candidato, Postulacion.candidato_id == Candidato.id)
        .filter(Postulacion.cuenta_id == cuenta_id, Candidato.eliminado_en.is_(None), Postulacion.activa.is_(True))
    )
    if vacante_id is not None:
        q = q.filter(Postulacion.vacante_id == vacante_id)
    ocultos, _ = fusion_duplicados(db, cuenta_id, vacante_id)
    if ocultos:
        q = q.filter(Postulacion.id.notin_(ocultos))
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
