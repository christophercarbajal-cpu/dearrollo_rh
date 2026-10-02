"""Evaluación integral como RESULTADO acumulado (2026-10-01, pipeline de cinco columnas).

La columna «Evaluación integral» se eliminó del Kanban; lo que sigue existiendo es este resultado, visible en la
tarjeta (`postulacion_dict["resultadoIntegral"]`) y en la ficha del candidato. Se calcula al vuelo en cada lectura
(nunca se persiste, igual que `serial._sintesis_global`) y NUNCA mueve la etapa ni decide nada: avanzar o descartar
lo sigue decidiendo una persona de RH (LFPDPPP, human-in-the-loop).

Validaciones que lo componen:
* Análisis de CV y Entrevista Red Human (Filtro Red Human) — las califica Red Human y aportan el SCORE. La Entrevista
  Red Human deja de exigirse si RH la omitió (`actividades_omitidas` / historial `entrevista_ia_omitida`).
* Cada evaluación de la postulación (entrevista humana, médica, psicométrica, …) no cancelada — la califica una
  persona y aporta Apto / Con observaciones / No apto. Solo cuenta como validada cuando RH la REVISÓ («Revisado por:
  [nombre]»); un resultado sin revisar queda «Pendiente de revisión».
* Las evaluaciones que sugiere la vacante y aún no se agregan quedan pendientes.

Reglas:
* OBLIGATORIAS: entrevista humana, médica y los tipos que sugiere la vacante. Si una obligatoria se califica como
  «No apto» (No avanzar / No apto / Desfavorable), el resultado integral es «No apto» aunque el score sea alto.
* Si falta alguna validación → «Pendiente» con el score PARCIAL de lo que ya hay. Un pendiente nunca vale cero:
  sin ninguna calificación el score es None.
* Sin pendientes ni «No apto»: «Apto con observaciones» si alguna trae observaciones; si no, «Apto».
"""

from typing import Iterable, List, Optional

from ..models import TIPO_DESDE_LEGADO, TIPOS_EVALUACION_U, Evaluacion, Postulacion, conclusiones_de

RED_HUMAN = "Red Human"
PENDIENTE_REVISION = "Pendiente de revisión"

ESTADOS_INTEGRAL = {
    "apto": "Apto",
    "con_observaciones": "Apto con observaciones",
    "no_apto": "No apto",
    "pendiente": "Pendiente",
}
TIPOS_OBLIGATORIOS = ("entrevista_humana", "medica")
CONCLUSION_NEGATIVA = ("no_avanzar", "no_apto", "desfavorable")
CONCLUSION_OBSERVACIONES = ("con_observaciones", "apto_con_restricciones")
CONCLUSION_PENDIENTE = ("requiere_otra_entrevista",)


def _revisado_por(nombre: str) -> str:
    return f"Revisado por: {nombre}" if nombre else PENDIENTE_REVISION


def _entrevista_ia_omitida(p: Postulacion) -> bool:
    if any(o.get("actividad") == "Entrevista IA" for o in (p.actividades_omitidas or [])):
        return True
    return any(h.get("evento") == "entrevista_ia_omitida" for h in (p.historial or []))


def _tipos_sugeridos(p: Postulacion) -> List[dict]:
    v = p.vacante
    salida = []
    for s in (v.evaluaciones_sugeridas or []) if v else []:
        tipo = TIPO_DESDE_LEGADO.get(s.get("tipo"), s.get("tipo"))
        if tipo in TIPOS_EVALUACION_U:
            salida.append({"tipo": tipo, "prueba_id": s.get("prueba_id"), "nombre": s.get("nombre") or TIPOS_EVALUACION_U[tipo]})
    return salida


def _validacion_red_human(clave: str, nombre: str, score: Optional[int], detalle: str) -> dict:
    hecha = score is not None
    return {
        "clave": clave, "nombre": nombre, "obligatoria": True, "fuente": "red_human",
        "estado": "aprobada" if hecha else "pendiente",
        "resultado": f"{score}/100" if hecha else "Pendiente",
        "detalle": detalle,
        "score": score,
        "revisadoPor": _revisado_por(RED_HUMAN if hecha else ""),
        "codigo": None,
    }


def _validacion_evaluacion(ev: Evaluacion, obligatoria: bool) -> dict:
    conclusion = ev.conclusion_vigente
    legible = conclusiones_de(ev.tipo).get(conclusion, "")
    if conclusion in CONCLUSION_NEGATIVA:
        estado = "no_apto"  # «calificado como No apto» cuenta aunque RH aún no lo revise
    elif ev.estado != "con_resultado" or not ev.revisada_en or conclusion in CONCLUSION_PENDIENTE:
        estado = "pendiente"
    elif conclusion in CONCLUSION_OBSERVACIONES:
        estado = "observaciones"
    else:
        estado = "aprobada"
    if ev.estado == "con_resultado":
        resultado = legible or "Resultado recibido"
    elif ev.estado == "realizada_sin_resultado":
        resultado = "Realizada · Resultado pendiente"
    elif ev.estado == "no_realizada":
        resultado = "No realizada"
    else:
        resultado = "Pendiente"
    return {
        "clave": ev.codigo, "nombre": ev.nombre_visible, "obligatoria": obligatoria, "fuente": "persona",
        "tipo": ev.tipo, "estado": estado, "resultado": resultado,
        "detalle": f"Realizada por: {ev.realizada_por}" if ev.realizada_por else "",
        "score": None,
        "revisadoPor": _revisado_por(ev.revisada_por if ev.revisada_en else ""),
        "codigo": ev.codigo,
    }


def calcular(p: Postulacion, evaluaciones: Optional[Iterable[Evaluacion]] = None) -> dict:
    """Resultado integral de UNA postulación. `evaluaciones`: las de esta postulación ya cargadas (el Kanban las
    trae en una sola consulta); si es None se consultan aquí."""
    if evaluaciones is None:
        from sqlalchemy.orm import object_session

        db = object_session(p)
        evaluaciones = (db.query(Evaluacion).filter(Evaluacion.postulacion_id == p.id).order_by(Evaluacion.id).all()
                        if db is not None else [])
    vivas = sorted((e for e in evaluaciones if e.estado != "cancelada"), key=lambda e: e.id or 0)
    a = p.analisis or {}
    validaciones: List[dict] = []

    # --- Red Human: Análisis de CV + Entrevista Red Human (aportan el score) ---
    hay_cv = bool(a.get("requisitos_cumplidos") or a.get("brechas") or a.get("fortalezas_cv"))
    score_cv = p.score if (hay_cv and p.score) else None
    validaciones.append(_validacion_red_human("analisis_cv", "Análisis de CV", score_cv,
                                              "" if score_cv is not None else "Aún no hay CV analizado."))
    ent = next((e for e in reversed(p.entrevistas or []) if e.estado == "evaluada" and e.evaluacion), None)
    match = (ent.evaluacion or {}).get("match_perfil") if ent else None
    if match is not None or not _entrevista_ia_omitida(p):
        detalle = ""
        if ent is None:
            ultima = p.entrevistas[-1] if p.entrevistas else None
            detalle = {"interrumpida": "Se interrumpió; puede reintentarse.", "parcial": "Quedó parcial; puede reintentarse."}.get(
                ultima.estado if ultima else "", "Aún no se realiza.")
        validaciones.append(_validacion_red_human("entrevista_red_human", "Entrevista Red Human",
                                                  int(match) if match is not None else None, detalle))

    # --- Personas: evaluaciones agregadas + las sugeridas por la vacante que faltan ---
    sugeridas = _tipos_sugeridos(p)
    tipos_sugeridos = {s["tipo"] for s in sugeridas}
    for ev in vivas:
        validaciones.append(_validacion_evaluacion(ev, ev.tipo in TIPOS_OBLIGATORIOS or ev.tipo in tipos_sugeridos))
    for s in sugeridas:
        if not any(e.tipo == s["tipo"] and (not s.get("prueba_id") or e.prueba_id == s["prueba_id"]) for e in vivas):
            validaciones.append({
                "clave": f"sugerida:{s['tipo']}:{s.get('prueba_id') or ''}", "nombre": s["nombre"], "obligatoria": True,
                "fuente": "persona", "tipo": s["tipo"], "estado": "pendiente", "resultado": "Sin agregar",
                "detalle": "La vacante la pide; agrégala con «Agregar evaluación».", "score": None,
                "revisadoPor": PENDIENTE_REVISION, "codigo": None,
            })

    # --- Resultado ---
    scores = [v["score"] for v in validaciones if v["score"] is not None]
    score = round(sum(scores) / len(scores)) if scores else None
    pendientes = [v for v in validaciones if v["estado"] == "pendiente"]
    no_aptas = [v for v in validaciones if v["estado"] == "no_apto" and v["obligatoria"]]
    if no_aptas:
        estado = "no_apto"
        motivo = "Requisito obligatorio calificado como No apto: " + ", ".join(f"{v['nombre']} ({v['resultado']})" for v in no_aptas) + "."
    elif pendientes:
        estado = "pendiente"
        motivo = "Faltan validaciones: " + ", ".join(v["nombre"] for v in pendientes) + "."
    elif any(v["estado"] in ("observaciones", "no_apto") for v in validaciones):
        estado = "con_observaciones"
        motivo = "Todas las validaciones están completas; alguna trae observaciones."
    else:
        estado = "apto"
        motivo = "Todas las validaciones están completas y aprobadas."
    return {
        "estado": estado,
        "texto": ESTADOS_INTEGRAL[estado],
        "score": score,
        "scoreParcial": score is not None and estado == "pendiente",
        "motivo": motivo,
        "completadas": sum(1 for v in validaciones if v["estado"] != "pendiente"),
        "total": len(validaciones),
        "validaciones": validaciones,
    }
