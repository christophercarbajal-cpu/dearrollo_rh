"""Cambio 38 (2026-10-14) — vocabulario ÚNICO de la recomendación.

La recomendación global (evaluación integral, Entrevista Red Human, tarjeta del tablero) se dice SIEMPRE:
Recomendable · Con reservas · No recomendable. «Apto / No apto» queda EXCLUSIVO del resultado de la Evaluación médica y del
Estudio socioeconómico (esos registros viven en `evaluaciones` y nunca se tocan).

`migrar` normaliza lo que quedó GUARDADO con el vocabulario anterior en postulaciones (`analisis`, `historial`) y
entrevistas (`evaluacion`): un valor de texto EXACTO «Apto con observaciones» → «Con reservas», «Apto» → «Recomendable»,
«No apto» → «No recomendable». Solo valores completos (nunca dentro de una frase) y nunca bajo una clave de dictamen
médico/socioeconómico. Una sola vez (marca en bitácora), idempotente y NO fatal.
"""

from typing import Any, Tuple

from sqlalchemy.orm.attributes import flag_modified

EQUIVALENCIAS = {"Apto con observaciones": "Con reservas", "Apto": "Recomendable", "No apto": "No recomendable"}
# claves cuyo valor es un dictamen médico/socioeconómico: «Apto / No apto» ahí es correcto
_CLAVES_DICTAMEN = {"dictamen", "dictamen_medico", "conclusion_medica", "medica", "medico", "socioeconomica", "socioeconomico"}
MARCA = "vocabulario_recomendacion_2026_10_14"


def normalizar(valor: Any, clave: str = "") -> Tuple[Any, int]:
    """(valor normalizado, cuántos cambios)."""
    if isinstance(valor, str):
        nuevo = EQUIVALENCIAS.get(valor.strip())
        return (nuevo, 1) if nuevo and clave.lower() not in _CLAVES_DICTAMEN else (valor, 0)
    if isinstance(valor, dict):
        if str(valor.get("tipo") or "").lower() in ("medica", "socioeconomica"):
            return valor, 0
        salida, n = {}, 0
        for k, x in valor.items():
            salida[k], m = normalizar(x, str(k))
            n += m
        return salida, n
    if isinstance(valor, list):
        salida, n = [], 0
        for x in valor:
            y, m = normalizar(x, clave)
            salida.append(y)
            n += m
        return salida, n
    return valor, 0


def migrar(db, aplicar: bool = True, forzar: bool = False) -> dict:
    """Recorre postulaciones y entrevistas. `aplicar=False` = simulación (solo cuenta). No hace commit."""
    from ..models import Bitacora, Entrevista, Postulacion, registrar

    if aplicar and not forzar and db.query(Bitacora.id).filter(Bitacora.accion == MARCA).first():
        return {"postulaciones": 0, "entrevistas": 0, "valores": 0, "yaAplicada": True}
    total, postulaciones, entrevistas = 0, [], []
    for p in db.query(Postulacion).all():
        cambio = 0
        for campo in ("analisis", "historial"):
            nuevo, n = normalizar(getattr(p, campo))
            if n:
                cambio += n
                if aplicar:
                    setattr(p, campo, nuevo)
                    flag_modified(p, campo)
        if cambio:
            total += cambio
            postulaciones.append(p.codigo)
    for e in db.query(Entrevista).all():
        nuevo, n = normalizar(e.evaluacion)
        if n:
            total += n
            entrevistas.append(e.codigo)
            if aplicar:
                e.evaluacion = nuevo
                flag_modified(e, "evaluacion")
    if aplicar:
        registrar(db, "sistema", MARCA, "postulacion", "", {"postulaciones": postulaciones, "entrevistas": entrevistas, "valores": total})
        db.flush()
    return {"postulaciones": len(postulaciones), "entrevistas": len(entrevistas), "valores": total, "codigos": postulaciones[:50]}


def con_vocabulario_anterior(db) -> int:
    """Cuántos valores guardados siguen con «Apto / Apto con observaciones / No apto» como recomendación global."""
    return migrar(db, aplicar=False)["valores"]
