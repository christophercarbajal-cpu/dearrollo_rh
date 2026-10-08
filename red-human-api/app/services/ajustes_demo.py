"""Ajustes de la ruta de demostración (2026-10-08) — SOLO las Cuentas de `models.CUENTAS_RUTA_AUTOMATICA` («demo-grupak»).

1. Retira TEMPORALMENTE «Persona bajo la lluvia» de la ruta: sale de la plantilla y de la copia de cada vacante (sube
   su versión). Las postulaciones conservan su copia congelada, pero la actividad queda OMITIDA con el motivo
   «Ajuste de ruta de demostración» (si no estaba ya completada u omitida): la compuerta la deja de exigir y el motor
   las deja avanzar. Nada se borra (las evaluaciones ya registradas siguen ahí).
2. «Ayudante general»: criterios del prefiltro conversacional — Secundaria y Turnos INDISPENSABLES; Ubicación (solo
   si la vacante tiene ubicación), NSS / situación fiscal y Experiencia solo se REGISTRAN. Solo reemplaza los criterios
   si siguen siendo los que sembró el script (nunca pisa lo que RH haya editado).

Idempotente y determinista: corre en cada arranque (paso no fatal) y en `scripts/ajustar_ruta_demo.py`.
`aplicar=False` solo cuenta.
"""

from datetime import datetime, timezone
from typing import Dict

from sqlalchemy.orm import Session

from ..models import CUENTAS_RUTA_AUTOMATICA, Cuenta, PlantillaProceso, Postulacion, Vacante, registrar

ACTOR = "Red Human (ajuste de ruta)"
MOTIVO = "Ajuste de ruta de demostración"
NOMBRES_RETIRADOS = ("persona bajo la lluvia",)
IDS_RETIRADOS = ("persona-lluvia",)

# Criterios del prefiltro de «Ayudante general» (los usa también scripts/seed_demo_grupak.py).
PREGUNTAS_AYUDANTE = [
    {"clave": "secundaria", "pregunta": "¿Terminaste la secundaria?", "valida": "Secundaria terminada", "tipo": "si_no",
     "respuesta_esperada": "Sí", "descarta": True, "opciones": ["Sí", "No", "Parcial"]},
    {"clave": "turnos", "pregunta": "¿Tienes disponibilidad para rolar turnos?", "valida": "Disponibilidad para rolar turnos",
     "tipo": "si_no", "respuesta_esperada": "Sí", "descarta": True, "opciones": ["Sí", "No", "Parcial"]},
    {"clave": "ubicacion", "pregunta": "¿Vives cerca del centro de trabajo o puedes trasladarte sin problema?",
     "valida": "Ubicación / traslado", "tipo": "si_no", "respuesta_esperada": "Sí", "descarta": False, "solo_con_ubicacion": True,
     "opciones": ["Sí", "No", "Parcial"]},
    {"clave": "nss_fiscal", "pregunta": "¿Cuentas con tu Número de Seguridad Social (NSS) y tu constancia de situación fiscal?",
     "valida": "NSS y situación fiscal", "tipo": "si_no", "respuesta_esperada": "Sí", "descarta": False,
     "opciones": ["Sí", "No", "Parcial"]},
    {"clave": "experiencia", "pregunta": "¿Tienes experiencia en almacén o producción?", "valida": "Experiencia en almacén o producción",
     "tipo": "si_no", "respuesta_esperada": "Sí", "descarta": False, "opciones": ["Sí", "No", "Parcial"]},
]
TITULO_AYUDANTE = "Ayudante general"
# lo que sembró la v1-v4 del script (si sigue así, RH no lo ha tocado)
_SEMBRADAS_AYUDANTE = ["¿Cumples con: Secundaria terminada?", "¿Cumples con: Disponibilidad para rolar turnos?"]


def _retirado(paso: dict) -> bool:
    return paso.get("id") in IDS_RETIRADOS or (paso.get("nombre") or "").strip().lower() in NOMBRES_RETIRADOS


def _sin_retirados(pasos: list) -> list:
    quitar = {x["id"] for x in pasos if _retirado(x)}
    return [{**x, "depende_de": [d for d in x.get("depende_de") or [] if d not in quitar]} for x in pasos if x["id"] not in quitar]


def ajustar(db: Session, aplicar: bool = True) -> Dict[str, int]:
    """No hace commit."""
    from . import proceso as sproc

    cuentas = db.query(Cuenta).filter(Cuenta.slug.in_(CUENTAS_RUTA_AUTOMATICA)).all()
    salida = {"plantillas": 0, "vacantes": 0, "postulaciones_liberadas": 0, "criterios_ayudante": 0}
    sello = datetime.now(timezone.utc).isoformat()
    for cu in cuentas:
        for pl in db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cu.id).all():
            if any(_retirado(x) for x in pl.pasos or []):
                salida["plantillas"] += 1
                if aplicar:
                    pl.pasos = _sin_retirados(list(pl.pasos))
                    pl.version = int(pl.version or 1) + 1
                    pl.actualizada_por = ACTOR
                    registrar(db, ACTOR, "ruta_actividad_retirada", "plantilla_proceso", str(pl.id), {"motivo": MOTIVO, "actividad": "Persona bajo la lluvia"})
        for v in db.query(Vacante).filter(Vacante.cuenta_id == cu.id).all():
            proc = dict(v.proceso or {})
            if proc.get("pasos") and any(_retirado(x) for x in proc["pasos"]):
                salida["vacantes"] += 1
                if aplicar:
                    proc["pasos"] = _sin_retirados(list(proc["pasos"]))
                    proc["version"] = int(proc.get("version") or 1) + 1
                    proc["actualizado_en"] = sello
                    v.proceso = proc
                    registrar(db, ACTOR, "ruta_actividad_retirada", "vacante", v.codigo, {"motivo": MOTIVO, "actividad": "Persona bajo la lluvia"})
            if v.titulo == TITULO_AYUDANTE and [q.get("pregunta") for q in v.preguntas_filtro or [] if isinstance(q, dict)] == _SEMBRADAS_AYUDANTE:
                salida["criterios_ayudante"] += 1
                if aplicar:
                    v.preguntas_filtro = [dict(q) for q in PREGUNTAS_AYUDANTE]
                    registrar(db, ACTOR, "prefiltro_criterios_actualizados", "vacante", v.codigo, {"motivo": MOTIVO})
        for p in db.query(Postulacion).filter(Postulacion.cuenta_id == cu.id).all():
            if not sproc.tiene_proceso(p):
                continue
            ids = [x["id"] for x in p.proceso["pasos"] if _retirado(x)]
            if not ids:
                continue
            estados = {x["id"]: x["estado"] for x in sproc.estado_pasos(p, solo_evaluables=True)}
            decisiones = dict(p.proceso_estado or {})
            for pid in ids:
                if estados.get(pid) in ("completada", "omitida", "cancelada") or (decisiones.get(pid) or {}).get("omitida"):
                    continue
                salida["postulaciones_liberadas"] += 1
                if not aplicar:
                    continue
                d = dict(decisiones.get(pid) or {})
                d["omitida"] = {"por": ACTOR, "motivo": MOTIVO, "fecha": sello, "obligatorio": True, "autorizado_por": ACTOR}
                decisiones[pid] = d
                p.proceso_estado = dict(decisiones)
                p.historial = list(p.historial or []) + [{
                    "evento": "paso_omitida", "usuario": ACTOR, "fecha": sello, "paso": pid, "motivo": MOTIVO,
                    "texto": f"Omitido el paso «Persona bajo la lluvia» por {ACTOR}: {MOTIVO}",
                }]
                registrar(db, ACTOR, "proceso_paso_omitida", "postulacion", p.codigo, {"paso": pid, "motivo": MOTIVO})
    return salida
