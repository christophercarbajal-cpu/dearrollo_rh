"""Cambio 28 (2026-10-12): «Otra actividad» y «Capacitación» dentro de la ruta.

- «Otra actividad» (tipo `otra` sin forma de evaluación): RH le pone nombre (obligatorio) y, si quiere, un mensaje.
  Al habilitarse, el agente manda ese mensaje UNA vez por el canal del candidato. La actividad queda ahí hasta que RH
  presiona «Marcar como realizada» (`marcar_realizada`). Las «Otra» configuradas como evaluación (con forma: evaluador,
  liga de otro sistema o registro directo) siguen siendo evaluaciones como antes.
- «Capacitación» (tipo `induccion`): RH elige el curso en la ruta (`curso`, código CUR-####). Al habilitarse, el agente lo asigna a
  la postulación y el bot manda la liga. Si aprueba el examen la actividad se cumple y la ruta sigue sola; si reprueba
  queda «No aprobó el curso» — nunca descarta: RH decide (descartar o «Continuar por decisión de RH»).
Todo se reclama antes de enviar y nunca truena (lo llama `proceso.avanzar_seguro`).
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Postulacion, registrar

ACTOR = "Red Human (agente)"
MAX_MENSAJE = 1000
TEXTO_NO_APROBO = "No aprobó el curso"


def es_otra_simple(paso: dict, ev=None) -> bool:
    """«Otra actividad» de RH (nombre + mensaje), sin evaluación detrás."""
    return paso.get("tipo") == "otra" and ev is None and not (paso.get("config") or {}).get("forma")


def estado_otra(paso: dict, decision: dict) -> dict:
    """Estado derivado de una «Otra actividad»: solo la cumple «Marcar como realizada» de RH."""
    base = {"estado": "pendiente", "resultado": None, "cumple": False, "espera": "Falta que RH la marque como realizada",
            "detalle": "", "revisadoPor": "Pendiente de revisión", "evaluacion": None, "terminado_en": None, "otra_simple": True}
    hecha = decision.get("realizada")
    if hecha:
        try:
            terminado = datetime.fromisoformat(hecha.get("fecha"))
        except (TypeError, ValueError):
            terminado = None
        return {**base, "estado": "completada", "resultado": "favorable", "cumple": True, "espera": "",
                "detalle": f"Realizada · marcada por {hecha.get('por', 'RH')}" + (f": {hecha['comentario']}" if hecha.get("comentario") else ""),
                "revisadoPor": f"Revisado por: {hecha.get('por', 'RH')}", "terminado_en": terminado}
    if decision.get("mensaje_enviado"):
        return {**base, "estado": "en_curso", "detalle": "Mensaje enviado al candidato"}
    return base


def marcar_realizada(db: Session, p: Postulacion, paso_id: str, u, comentario: str = "") -> dict:
    """«Marcar como realizada» de una «Otra actividad». No hace commit."""
    from . import proceso as sproc

    paso = sproc._paso(p, paso_id)
    if paso.get("tipo") != "otra":
        raise sproc.ErrorProceso(409, f"«{paso['nombre']}» no se marca a mano: se cumple con su propio registro.")
    decisiones = dict(p.proceso_estado or {})
    d = dict(decisiones.get(paso_id) or {})
    if d.get("realizada"):
        raise sproc.ErrorProceso(409, f"«{paso['nombre']}» ya está marcada como realizada.")
    sello = datetime.now(timezone.utc).isoformat()
    d["realizada"] = {"por": u.nombre, "fecha": sello, "comentario": (comentario or "").strip()[:500]}
    decisiones[paso_id] = d
    p.proceso_estado = decisiones
    p.historial = list(p.historial or []) + [{"evento": "actividad_realizada", "usuario": u.nombre, "fecha": sello, "paso": paso_id,
                                              "texto": f"«{paso['nombre']}» marcada como realizada por {u.nombre}"}]
    registrar(db, u.nombre, "actividad_realizada", "postulacion", p.codigo,
              {"paso": paso_id, "nombre": paso["nombre"], "comentario": d["realizada"]["comentario"], "correo_rh": getattr(u, "correo", "")})
    return d["realizada"]


def _pausado(p: Postulacion) -> bool:
    return bool(((p.analisis or {}).get("motor_ruta") or {}).get("pausado"))


async def _mandar_mensaje(db: Session, p: Postulacion, paso: dict) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from .motor_ruta import _avisar

    decisiones = dict(p.proceso_estado or {})
    d = dict(decisiones.get(paso["id"]) or {})
    d["mensaje_enviado"] = {"fecha": datetime.now(timezone.utc).isoformat(), "entregado": None}
    decisiones[paso["id"]] = d
    p.proceso_estado = decisiones
    flag_modified(p, "proceso_estado")
    db.commit()  # reclamado ANTES de enviar: nunca sale dos veces
    texto = paso["mensaje"]
    entregado, detalle = await _avisar(db, p, texto, paso["nombre"], "", motivo="aviso", paso_id=paso["id"], actor=ACTOR)
    decisiones = dict(p.proceso_estado or {})
    d = dict(decisiones.get(paso["id"]) or {})
    d["mensaje_enviado"] = {**(d.get("mensaje_enviado") or {}), "entregado": bool(entregado), "detalle": detalle}
    decisiones[paso["id"]] = d
    p.proceso_estado = decisiones
    registrar(db, ACTOR, "actividad_mensaje_enviado", "postulacion", p.codigo,
              {"paso": paso["id"], "nombre": paso["nombre"], "entregado": bool(entregado), "detalle": detalle})
    db.commit()


async def _asignar_curso(db: Session, p: Postulacion, paso: dict) -> None:
    from ..models import AsignacionCurso, Curso
    from ..routers.capacitacion import asignar_a_postulacion

    curso = db.query(Curso).filter(Curso.codigo == paso["curso"], Curso.cuenta_id == p.cuenta_id).first()
    if curso is None:
        return
    if db.query(AsignacionCurso).filter(AsignacionCurso.curso_id == curso.id, AsignacionCurso.postulacion_id == p.id).first():
        return
    analisis = dict(p.analisis or {})
    asignados = dict(analisis.get("capacitacion_ruta") or {})
    if asignados.get(paso["id"]):
        return
    asignados[paso["id"]] = {"curso": curso.codigo, "fecha": datetime.now(timezone.utc).isoformat()}
    analisis["capacitacion_ruta"] = asignados
    p.analisis = analisis
    db.commit()  # reclamado antes de asignar y avisar
    await asignar_a_postulacion(db, p, curso, ACTOR)
    db.commit()


async def revisar(db: Session, p: Optional[Postulacion]) -> None:
    """Al habilitarse: manda el mensaje de cada «Otra actividad» y asigna el curso de «Capacitación» (una sola vez)."""
    if p is None or not p.activa or not p.consentimiento or _pausado(p):
        return
    from . import proceso as sproc

    pasos = {x["id"]: x for x in (p.proceso or {}).get("pasos") or []}
    candidatos = [x for x in pasos.values() if (x.get("tipo") == "otra" and x.get("mensaje")) or (x.get("tipo") == "induccion" and x.get("curso") and x.get("modalidad_capacitacion", "plataforma") == "plataforma")]
    if not candidatos:
        return
    if sproc.detenido_por_no_aprobada(p):
        return
    for x in sproc.estado_pasos(p):
        paso = pasos.get(x["id"])
        if paso not in candidatos or not x["disponible"] or x["estado"] in ("completada", "omitida", "cancelada"):
            continue
        if paso["tipo"] == "otra" and x.get("otraSimple") and not ((p.proceso_estado or {}).get(paso["id"]) or {}).get("mensaje_enviado"):
            await _mandar_mensaje(db, p, paso)
        elif paso["tipo"] == "induccion":
            await _asignar_curso(db, p, paso)
