"""Cambio 28 (2026-10-12): sincronización Proceso ↔ Vacante.

- La vacante pide documentos y su ruta no trae la actividad «Documentos» antes de Contratación → se agrega sola después
  del prefiltro (`sincronizar`, al guardar la vacante; misma regla que `documentos_vacante.con_documentos_vacante`).
- La ruta trae «Documentos» antes de Contratación → «Documentos y fotos que se piden» es obligatorio.
- La ruta trae «Análisis de CV» → el CV es obligatorio en la solicitud web (`con_cv` y `/candidatos/postular`).
- La ruta trae prefiltro (web o WhatsApp) → la vacante exige al menos 1 indispensable (Cambio 37; antes 3).
Lo que falte NO impide guardar: desactiva «Publicar» (`faltantes_publicacion`; `POST /vacantes/{codigo}/publicar` → 409).
"""

from typing import List, Optional

from sqlalchemy.orm import Session

MIN_INDISPENSABLES = 1  # Cambio 37 (antes 3)
TIPOS_PREFILTRO = ("prefiltro_web", "prefiltro_whatsapp")


def _vivos(pasos) -> list:
    return [x for x in pasos or [] if isinstance(x, dict) and not x.get("heredado")]


def tiene_documentos_previos(pasos) -> bool:
    """La ruta trae «Documentos» antes de Contratación (la que recoge lo que pide la vacante)."""
    from .proceso import _indice

    return any(x.get("tipo") == "documentos" and _indice(x.get("etapa") or "Contratación") < _indice("Contratación")
               for x in _vivos(pasos))


def exige_cv(pasos) -> bool:
    """Cambio 30: «Solicitud web con CV» (o el «Análisis de CV» de rutas anteriores) → el CV es obligatorio en la web."""
    return any(x.get("tipo") == "analisis_cv" or (x.get("tipo") == "solicitud_web" and x.get("con_cv")) for x in _vivos(pasos))


def tiene_prefiltro(pasos) -> bool:
    return any(x.get("tipo") in TIPOS_PREFILTRO for x in _vivos(pasos))


def indispensables(v) -> List[str]:
    from ..routers.vacantes import requisitos_lista

    return requisitos_lista(getattr(v, "requisitos", "") or "")


def faltantes_publicacion(db: Optional[Session], v, pasos: Optional[list] = None) -> List[str]:
    """Qué le falta a la vacante para publicarse según su ruta. Vacío = se puede publicar."""
    from . import documentos_vacante as sdv
    from . import guiones as sgui

    if pasos is None:
        pasos = sgui.pasos_de_vacante(db, v)
    faltan: List[str] = []
    if tiene_prefiltro(pasos) and len(indispensables(v)) < MIN_INDISPENSABLES:
        faltan.append(f"La ruta incluye prefiltro: captura al menos {MIN_INDISPENSABLES} requisito indispensable "
                      f"(hoy {len(indispensables(v))}).")
    docs = sdv.de_vacante(v)
    if tiene_documentos_previos([x for x in pasos if not x.get("documentos_vacante")]) and not docs:
        faltan.append("La ruta incluye «Documentos»: indica en «Documentos y fotos que se piden» qué documentos se piden.")
    return faltan


def sincronizar(v) -> bool:
    """Ajusta la ruta GUARDADA de la vacante: agrega «Documentos» tras el prefiltro si la vacante pide documentos y la
    ruta no la trae, y marca la solicitud web «con CV» si la ruta trae Análisis de CV. Regresa si cambió algo."""
    from sqlalchemy.orm.attributes import flag_modified

    from . import documentos_vacante as sdv

    proc = dict(getattr(v, "proceso", None) or {})
    if not proc.get("pasos"):
        return False
    antes = proc["pasos"]
    nuevo = sdv.con_documentos_vacante(proc, v)
    pasos = [dict(x) for x in nuevo.get("pasos") or []]
    if exige_cv(pasos):
        for x in pasos:
            if x.get("tipo") == "solicitud_web" and not x.get("con_cv"):
                x["con_cv"] = True
    if pasos == antes:
        return False
    v.proceso = {**proc, "pasos": pasos}
    flag_modified(v, "proceso")
    return True
