"""Cambio 34 (2026-10-13) — migración al catálogo ÚNICO de documentos (`models.CATALOGO_DOCUMENTOS`).

* Expedientes: cada documento toma el nombre del catálogo («Identificación oficial» → «INE (frente y reverso)»,
  «Constancia de Situación Fiscal / RFC» → «Constancia de Situación Fiscal (RFC)»…) y los duplicados del MISMO expediente
  (misma `clave_documento`) se FUSIONAN en uno: sobrevive el de mayor prioridad — Aprobado con archivo > Aprobado >
  con archivo > Por revisar > Pendiente — y conserva su estado y su archivo. De los demás se suman obligatorio y la
  trazabilidad de solicitudes; sus archivos pasan al sobreviviente como historial (no vigentes) — nunca se borra un
  archivo del disco — y la fila se elimina dejando su foto completa en bitácora (`documento_fusionado`).
* Vacantes y plantillas de vacante (`documentos_solicitados`) y plantillas de Onboarding (`documentos`): se reescriben
  con el catálogo y sin duplicados.
Idempotente: correrlo otra vez no cambia nada. Corre en cada arranque (paso no fatal) y a mano con
`scripts/migrar_catalogo_documentos.py` (simulación por defecto).
"""

from typing import Dict, List

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import (ArchivoDocumento, Documento, Expediente, Plantilla, PlantillaOnboarding, Vacante, clave_documento,
                      nombre_documento, normalizar_documentos_vacante, registrar)

ACTOR = "Red Human (catálogo de documentos)"


def prioridad(d: Documento) -> tuple:
    """Mayor = sobrevive. El estado «Aprobado» y su archivo tienen prioridad (decisión del usuario)."""
    return (bool(d.aprobado) and bool(d.archivo), bool(d.aprobado), bool(d.archivo), d.estado in ("recibido", "revision"),
            d.estado != "no_aplica", bool(d.obligatorio), -(d.id or 0))


def _foto(d: Documento) -> dict:
    return {"id": d.id, "tipo": d.tipo, "estado": d.estado, "archivo": d.archivo, "nombre_archivo": d.nombre_archivo,
            "revisado_por": d.revisado_por, "obligatorio": d.obligatorio}


def _archivos_de(db: Session, doc_id: int) -> List[ArchivoDocumento]:
    try:
        return db.query(ArchivoDocumento).filter(ArchivoDocumento.documento_id == doc_id).all()
    except Exception:  # noqa: BLE001 — tabla de archivos no disponible
        return []


def fusionar_expediente(db: Session, e: Expediente, aplicar: bool) -> Dict[str, list]:
    """Renombra al catálogo y fusiona duplicados de UN expediente. Regresa {renombrados, fusionados}."""
    renombrados, fusionados = [], []
    grupos: Dict[str, List[Documento]] = {}
    for d in e.documentos:
        if d.interno:
            continue
        grupos.setdefault(clave_documento(d.tipo), []).append(d)
    for clave, docs in grupos.items():
        docs = sorted(docs, key=prioridad, reverse=True)
        ganador, perdedores = docs[0], docs[1:]
        nombre = nombre_documento(ganador.tipo)
        if ganador.tipo != nombre:
            renombrados.append({"expediente": e.id, "de": ganador.tipo, "a": nombre})
        for x in perdedores:
            fusionados.append({"expediente": e.id, "queda": {**_foto(ganador), "tipo": nombre}, "sale": _foto(x)})
        if not aplicar:
            continue
        ganador.tipo = nombre
        for x in perdedores:
            ganador.obligatorio = bool(ganador.obligatorio or x.obligatorio)
            if x.solicitado_en and (not ganador.solicitado_en or x.solicitado_en < ganador.solicitado_en):
                ganador.solicitado_en, ganador.solicitado_canal = x.solicitado_en, ganador.solicitado_canal or x.solicitado_canal
            if x.solicitudes:
                ganador.solicitudes = list(ganador.solicitudes or []) + list(x.solicitudes)
                flag_modified(ganador, "solicitudes")
            ganador.cantidad = max(int(ganador.cantidad or 1), int(x.cantidad or 1))
            for a in _archivos_de(db, x.id):  # historial: el archivo nunca se borra del disco
                a.documento_id = ganador.id
                a.vigente = a.vigente and not (ganador.aprobado or ganador.archivo)
            registrar(db, ACTOR, "documento_fusionado", "expediente", str(e.id), {"queda": _foto(ganador), "sale": _foto(x)})
            e.documentos.remove(x)
            db.delete(x)
    return {"renombrados": renombrados, "fusionados": fusionados}


def _normalizar_lista_vacante(obj, campo: str, aplicar: bool) -> bool:
    actual = list(getattr(obj, campo) or [])
    nuevo = normalizar_documentos_vacante(actual)
    if nuevo == actual:
        return False
    if aplicar:
        setattr(obj, campo, nuevo)
        flag_modified(obj, campo)
    return True


def _normalizar_plantilla_onboarding(pl: PlantillaOnboarding, aplicar: bool) -> bool:
    from .onboarding import normalizar_documentos

    actual = list(pl.documentos or [])
    nuevo = normalizar_documentos(actual)
    if nuevo == actual:
        return False
    if aplicar:
        pl.documentos = nuevo
        flag_modified(pl, "documentos")
    return True


def migrar(db: Session, aplicar: bool = False) -> dict:
    """Simulación (`aplicar=False`) o aplicación de toda la base. Regresa el detalle y los contadores."""
    renombrados, fusionados = [], []
    for e in db.query(Expediente).order_by(Expediente.id).all():
        r = fusionar_expediente(db, e, aplicar)
        renombrados += r["renombrados"]
        fusionados += r["fusionados"]
    vacantes = [v.codigo for v in db.query(Vacante).all() if _normalizar_lista_vacante(v, "documentos_solicitados", aplicar)]
    plantillas = [pl.id for pl in db.query(Plantilla).all() if _normalizar_lista_vacante(pl, "documentos_solicitados", aplicar)]
    try:
        onboarding = [pl.id for pl in db.query(PlantillaOnboarding).all() if _normalizar_plantilla_onboarding(pl, aplicar)]
    except Exception:  # noqa: BLE001 — tablas de módulos no disponibles
        db.rollback()
        onboarding = []
    if aplicar:
        db.flush()
    return {"renombrados": renombrados, "fusionados": fusionados, "vacantes": vacantes, "plantillasVacante": plantillas,
            "plantillasOnboarding": onboarding,
            "totales": {"renombrados": len(renombrados), "fusionados": len(fusionados), "vacantes": len(vacantes),
                        "plantillasVacante": len(plantillas), "plantillasOnboarding": len(onboarding)}}


# 2026-10-16 (bug del tester): la CLABE de la configuración BASE ya no bloquea (`models.DOCUMENTOS_BASE_OPCIONALES`). Los
# expedientes abiertos antes la traen como obligatoria: se corrige UNA vez (marca en bitácora) solo donde ni la plantilla
# de Onboarding de la Cuenta ni la vacante la piden como obligatoria. Nunca toca una ya Aprobada ni borra nada. NO fatal.
MARCA_CLABE = "clabe_opcional_2026_10_16"


def clabe_opcional(db: Session, aplicar: bool = True, forzar: bool = False) -> dict:
    """Regresa {"documentos": [ids], "yaAplicada": bool}. No hace commit."""
    from ..models import Bitacora, Postulacion
    from .onboarding import documentos_requeridos

    if aplicar and not forzar and db.query(Bitacora.id).filter(Bitacora.accion == MARCA_CLABE).first():
        return {"documentos": [], "yaAplicada": True}
    cambiados = []
    for d in db.query(Documento).filter(Documento.obligatorio.is_(True)).all():
        if d.interno or d.aprobado or clave_documento(d.tipo) != "cuenta_bancaria":
            continue
        e = db.get(Expediente, d.expediente_id)
        p = db.get(Postulacion, e.postulacion_id) if e is not None and e.postulacion_id else None
        if p is None:
            continue
        try:
            req = documentos_requeridos(db, p)
        except Exception:  # noqa: BLE001 — sin tablas de Onboarding: la base (opcional)
            req = []
        if any(clave_documento(r["tipo"]) == "cuenta_bancaria" and r.get("obligatorio") for r in req):
            continue  # la pide explícitamente la plantilla de la Cuenta o la vacante
        cambiados.append(d.id)
        if aplicar:
            d.obligatorio = False
            registrar(db, ACTOR, "documento_opcional", "postulacion", p.codigo,
                      {"documento": d.id, "tipo": d.tipo, "motivo": "CLABE de la configuración base: ya no bloquea"})
    if aplicar:
        registrar(db, ACTOR, MARCA_CLABE, "sistema", "-", {"documentos": cambiados})
        db.flush()
    return {"documentos": cambiados, "yaAplicada": False}
