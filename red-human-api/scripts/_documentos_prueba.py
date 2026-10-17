"""Ayuda para las verificaciones (2026-10-17, «Documentos alineados a la vacante»).

El expediente del candidato pide SOLO lo que seleccionó su vacante (snapshot de la postulación); la configuración base de
la Cuenta ya no se suma. Las verificaciones que prueban contrato, alta, recordatorios, trazabilidad… con los documentos
de siempre (INE, CURP, RFC, NSS, comprobante de domicilio, CLABE) hacen que SU vacante los pida explícitamente.
"""

from typing import Iterable, List, Optional

from app.models import DOCUMENTOS_BASE, Postulacion, Vacante, normalizar_documentos_vacante


def documentos_base_si_la_vacante_no_pide() -> None:
    """SOLO para verificaciones de otros módulos (contrato, alta, recordatorios, trazabilidad, tablero…) escritas cuando el
    expediente traía siempre la base: si la vacante NO seleccionó documentos, el snapshot de la postulación toma los
    documentos base (con la CLABE opcional, como antes). Debe llamarse ANTES de arrancar la app. La regla real (solo lo
    que pide la vacante) la prueba `verificar_documentos_snapshot.py`."""
    from app.models import documentos_base_requeridos
    import app.services.documentos_vacante as sdv

    original = sdv._calcular_snapshot

    def calcular(p, v=None):
        docs = original(p, v)
        if docs or (v if v is not None else p.vacante) is None:
            return docs
        return [{"nombre": d["tipo"], "cantidad": 1, "foto": False, "etapa": "Contratación", "obligatorio": d["obligatorio"]}
                for d in documentos_base_requeridos()]

    sdv._calcular_snapshot = calcular
    # las rutas de prueba armadas a mano no siempre traen la actividad de documentos: el expediente se comporta como antes
    sdv.ruta_con_documentos = lambda p: p is not None


def pedir_documentos(db, vacantes: Optional[Iterable[Vacante]] = None, nombres: Optional[List[str]] = None, cuenta_id: Optional[int] = None) -> None:
    """La(s) vacante(s) piden `nombres` (default: los documentos base) y las postulaciones que ya existen re-fijan su
    snapshot. Sin `vacantes`, todas las de la Cuenta (o todas, sin Cuenta)."""
    from app.services.documentos_vacante import fijar_snapshot

    nombres = list(nombres or DOCUMENTOS_BASE)
    if vacantes is None:
        q = db.query(Vacante)
        if cuenta_id is not None:
            q = q.filter(Vacante.cuenta_id == cuenta_id)
        vacantes = q.all()
    vacantes = list(vacantes)
    for v in vacantes:
        v.documentos_solicitados = normalizar_documentos_vacante([{"nombre": n} for n in nombres])
    db.flush()
    ids = [v.id for v in vacantes]
    if ids:
        for p in db.query(Postulacion).filter(Postulacion.vacante_id.in_(ids)).all():
            fijar_snapshot(p, forzar=True)
    db.commit()
