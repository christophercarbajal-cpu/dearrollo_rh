"""Cambio 42 — campana de RH: candidatos DETENIDOS por una acción humana (aprobar documentos, agendar / registrar la
entrevista humana, enviar la oferta, firmar el contrato u «Otra actividad»).

Se avisa al RESPONSABLE de la vacante (el Administrador además ve las vacantes sin responsable). Agrupación:
- 1 candidato en esa vacante con esa acción → «[Nombre] · [Puesto]: [acción]» y el clic abre la ficha en la actividad.
- Más de 1 → «[n] candidatos de [Puesto]: [acción]» y el clic abre el tablero de la vacante filtrado por esa acción.

Todo se DERIVA de la ruta en cada lectura (`proceso.accion_humana`): no hay tabla de notificaciones que sincronizar.
"""

from typing import Dict, List, Tuple

from sqlalchemy.orm import Session, selectinload

from ..models import Evaluacion, Expediente, Postulacion, Usuario, Vacante
from . import conteos
from . import proceso as sproc


def _vacantes_de(db: Session, cuenta_id: int, u: Usuario) -> Dict[int, Vacante]:
    q = db.query(Vacante).filter(Vacante.cuenta_id == cuenta_id, Vacante.estado != "Eliminada")
    admin = getattr(u, "rol", "") == "Administrador"
    vs = [v for v in q.all() if v.responsable_id == u.id or (admin and v.responsable_id is None)]
    return {v.id: v for v in vs}


def agrupar(filas: List[dict]) -> List[dict]:
    """filas = [{vacante, puesto, codigo, nombre, accion}] → grupos por (vacante, acción). Pura (la usa la regresión)."""
    grupos: Dict[Tuple[str, str], List[dict]] = {}
    for f in filas:
        grupos.setdefault((f["vacante"], f["accion"]["clave"]), []).append(f)
    salida = []
    for (vac, clave), fs in grupos.items():
        f0, accion = fs[0], fs[0]["accion"]
        if len(fs) == 1:
            texto = f"{f0['nombre']} · {f0['puesto']}: {accion['texto']}"
            liga = f"/dashboard/candidatos?vacante={vac}&abrir={f0['codigo']}&actividad={accion['paso']}"
        else:
            texto = f"{len(fs)} candidatos de {f0['puesto']}: {accion['texto']}"
            liga = f"/dashboard/candidatos?vacante={vac}&accion={clave}"
        salida.append({"vacante": vac, "puesto": f0["puesto"], "accion": clave, "accionTexto": accion["texto"],
                       "total": len(fs), "texto": texto, "liga": liga, "candidatos": [f["codigo"] for f in fs]})
    salida.sort(key=lambda g: (-g["total"], g["texto"]))
    return salida


def pendientes(db: Session, cuenta_id: int, u: Usuario) -> dict:
    from ..routers.candidatos import nombre_ficha
    from .envios import precarga_tablero

    vacantes = _vacantes_de(db, cuenta_id, u)
    if not vacantes:
        return {"total": 0, "grupos": []}
    ps = (conteos.postulaciones_visibles(db, cuenta_id)
          .filter(Postulacion.vacante_id.in_(list(vacantes)))
          .options(selectinload(Postulacion.candidato),
                   selectinload(Postulacion.expediente).selectinload(Expediente.documentos),
                   selectinload(Postulacion.entrevistas))
          .all())
    ps = [p for p in ps if sproc.tiene_proceso(p)]
    evs: dict = {p.id: [] for p in ps}
    if ps:
        for ev in db.query(Evaluacion).filter(Evaluacion.postulacion_id.in_(list(evs))).order_by(Evaluacion.id):
            evs[ev.postulacion_id].append(ev)
    pre = precarga_tablero(db, ps, evs) if ps else {}
    filas = []
    for p in ps:
        try:
            pasos = sproc.estado_pasos(p, evs[p.id], precarga=pre.get(p.id))
            accion = sproc.accion_humana(p, pasos)
        except Exception:  # noqa: BLE001 — una postulación rota nunca apaga la campana
            continue
        if accion:
            v = vacantes[p.vacante_id]
            nombre = (p.candidato.nombre if p.candidato else "") or ""
            filas.append({"vacante": v.codigo, "puesto": v.titulo, "codigo": p.codigo,
                          "nombre": nombre if nombre and not nombre.startswith("Candidato") else nombre_ficha(p), "accion": accion})
    grupos = agrupar(filas)
    return {"total": len(filas), "grupos": grupos}
