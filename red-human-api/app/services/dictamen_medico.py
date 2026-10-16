"""Dictamen del examen médico con reglas de la RUTA (2026-10-15, UVAQ — decisión del usuario).

La actividad «Evaluación médica» de la ruta puede traer:
  · `no_apto_descarta`: «No apto» CIERRA la postulación sola como «Descartado» (mismo cierre que el descarte de RH;
    motivo en el historial; el candidato recibe el mensaje genérico, nunca el dictamen).
  · `restricciones_revision`: «Apto con restricciones» → «Requiere revisión» (lo deriva `proceso._paso_evaluacion`).
«Apto» cumple la actividad y la ruta sigue sola hacia Contratación.

Sin esas banderas la regla de siempre: «No apto» detiene la ruta y RH decide (human-in-the-loop).
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Postulacion, registrar

ACTOR = "Red Human (dictamen médico)"
MOTIVO = "No apto en el examen médico"


async def revisar(db: Session, p: Postulacion) -> bool:
    """Si la ruta pide descartar con «No apto» y el dictamen vigente lo es, descarta. Regresa True si descartó."""
    if p is None or not p.activa:
        return False
    pasos = [x for x in ((p.proceso or {}).get("pasos") or []) if x.get("tipo") == "medica" and x.get("no_apto_descarta")]
    if not pasos:
        return False
    ids = {x["id"] for x in pasos}
    from ..models import Evaluacion

    evs = db.query(Evaluacion).filter(Evaluacion.postulacion_id == p.id, Evaluacion.tipo == "medica",
                                      Evaluacion.estado == "con_resultado").all()
    ev = next((e for e in evs if (e.paso_id in ids or not e.paso_id) and e.conclusion_vigente == "no_apto"), None)
    if ev is None:
        return False
    from . import prefiltro_conversacional as pconv

    sello = datetime.now(timezone.utc)
    p.historial = list(p.historial or []) + [{
        "evento": "descartado", "usuario": ACTOR, "fecha": sello.isoformat(), "etapa": p.etapa, "motivo": MOTIVO,
        "origen": "examen_medico", "evaluacion": ev.codigo,
        "texto": f"Descartado por Red Human: {MOTIVO} ({ev.codigo}). RH puede reactivarlo desde «…» → «Reactivar».",
    }]
    p.estado = "no_cumple"
    p.cerrar("descartado")
    registrar(db, ACTOR, "postulacion_descartada_examen_medico", "postulacion", p.codigo, {"evaluacion": ev.codigo})
    db.commit()
    try:
        await pconv.avisar_descarte(db, p, pconv.texto_descarte(pconv.empresa_de(p)))
    except Exception as ex:  # noqa: BLE001 — el aviso nunca deshace el descarte
        print(f"[dictamen_medico] aviso de descarte falló para {p.codigo}: {ex}", flush=True)
    return True
