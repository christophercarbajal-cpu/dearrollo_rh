"""Motor de cálculos de Clima v2 (2026-09-27) — ÚNICA fuente de los indicadores de una medición.

Lo leen el tablero (`GET /clima/mediciones/{codigo}/resultados`) y el análisis con IA (`POST …/analizar`),
así que la IA ve exactamente las mismas cifras que RH. Se calcula al vuelo en cada lectura (tiempo real).

Reglas (especificación Clima v2):
  * Las respuestas de PRUEBA nunca entran a los resultados reales (y viceversa); las EXTERNAS tampoco
    suman a la participación ni a los resultados internos (se reportan aparte).
  * Participación = colaboradores invitados que ya respondieron / total de invitados × 100.
  * % favorable de una pregunta de escala 1-5 = respuestas 4 o 5 / respuestas válidas de esa pregunta.
  * Dimensión = promedio de los % favorables de sus preguntas de escala CON respuestas (las demás se omiten).
  * Índice de clima = promedio simple de las dimensiones con resultado (mismo peso cada una). Sin ninguna
    dimensión calculable el índice queda «pendiente» — NUNCA 0 %.
  * Opción múltiple y abiertas se muestran tal cual (distribución / comentarios): no se convierten a puntaje.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from .. import fechas
from ..models import DIMENSION_CLIMA_DEFAULT, ESCALA_CLIMA, MedicionClima, RespuestaClima

FUENTES = ("reales", "prueba")


def es_externa(r: RespuestaClima) -> bool:
    return bool(r.es_externa) or r.origen == "externo"  # filas previas a Clima v2 solo tienen `origen`


def respuestas_de(m: MedicionClima, fuente: str = "reales") -> List[RespuestaClima]:
    """reales = internas no-prueba (lo único que alimenta los indicadores); prueba = solo las de prueba."""
    if fuente == "prueba":
        return [r for r in m.respuestas if r.es_prueba]
    return [r for r in m.respuestas if not r.es_prueba and not es_externa(r)]


def _valor_escala(v) -> Optional[int]:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return int(n) if n == int(n) else None


def _redondeo(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x, 1)


def participacion(m: MedicionClima) -> dict:
    invitados = len(m.participaciones)
    respondieron = sum(1 for p in m.participaciones if p.respondio)
    return {
        "invitados": invitados,
        "respondieron": respondieron,
        "faltan": invitados - respondieron,
        "porcentaje": _redondeo(respondieron / invitados * 100) if invitados else None,
    }


def _fecha_cierre(m: MedicionClima) -> str:
    if not m.cierra_en:
        return ""
    return fechas.local(m.cierra_en).strftime("%d/%m/%Y %H:%M h")


def resumen_participacion(m: MedicionClima, p: dict) -> str:
    """Texto del tablero: «X de Y respondieron · faltan Z · cierra el [fecha]»."""
    if not p["invitados"]:
        return "Aún no hay colaboradores invitados."
    partes = [f"{p['respondieron']} de {p['invitados']} respondieron", f"faltan {p['faltan']}"]
    if m.estado == "cerrada":
        partes.append("encuesta cerrada")
    elif m.cierra_en:
        partes.append(f"cierra el {_fecha_cierre(m)}")
    return " · ".join(partes)


def calcular(m: MedicionClima, fuente: str = "reales") -> dict:
    """Indicadores de la medición con las respuestas de `fuente` (reales | prueba)."""
    if fuente not in FUENTES:
        raise ValueError(f"fuente inválida: {fuente}")
    respuestas = respuestas_de(m, fuente)
    preguntas = sorted(m.preguntas or [], key=lambda p: (p.get("orden") or 0))
    orden_dims = list(m.dimensiones or [])
    por_dim: Dict[str, List[dict]] = {}

    for p in preguntas:
        dim = p.get("dimension") or DIMENSION_CLIMA_DEFAULT
        if dim not in orden_dims:
            orden_dims.append(dim)
        valores = [r.respuestas.get(p["id"]) for r in respuestas if r.respuestas and r.respuestas.get(p["id"]) not in (None, "")]
        fila = {"id": p["id"], "texto": p["texto"], "tipo": p["tipo"], "orden": p.get("orden"), "respuestas": 0}
        if p["tipo"] == "escala":
            maximo = int(p.get("escala_max") or ESCALA_CLIMA)
            validos = [n for n in (_valor_escala(v) for v in valores) if n is not None and 1 <= n <= maximo]
            favorables = sum(1 for n in validos if n >= maximo - 1)  # escala 1-5: 4 o 5
            fila.update({
                "respuestas": len(validos),
                "escalaMax": maximo,
                "favorables": favorables,
                "favorable": (favorables / len(validos) * 100) if validos else None,
                "promedio": _redondeo(sum(validos) / len(validos)) if validos else None,
                "distribucion": {str(n): validos.count(n) for n in range(1, maximo + 1)},
            })
        elif p["tipo"] == "opcion":
            textos = [str(v) for v in valores]
            fila.update({"respuestas": len(textos), "distribucion": {o: textos.count(o) for o in p.get("opciones", [])}})
        else:
            fila.update({"respuestas": len(valores), "comentarios": [str(v)[:500] for v in valores]})  # sin autor, siempre
        por_dim.setdefault(dim, []).append(fila)

    dimensiones = []
    for dim in orden_dims:
        filas = por_dim.get(dim, [])
        con_resultado = [f["favorable"] for f in filas if f["tipo"] == "escala" and f.get("favorable") is not None]
        valor = sum(con_resultado) / len(con_resultado) if con_resultado else None
        for f in filas:
            if "favorable" in f:
                f["favorable"] = _redondeo(f["favorable"])
        dimensiones.append({
            "nombre": dim,
            "favorable": valor,
            "preguntasConResultado": len(con_resultado),
            "preguntas": filas,
        })

    calculables = [d["favorable"] for d in dimensiones if d["favorable"] is not None]
    for d in dimensiones:
        d["favorable"] = _redondeo(d["favorable"])
    if not respuestas:
        indice = {"valor": None, "estado": "sin_respuestas",
                  "etiqueta": "Aún no hay respuestas reales" if fuente == "reales" else "Aún no hay respuestas de prueba"}
    elif not calculables:
        indice = {"valor": None, "estado": "pendiente", "etiqueta": "Índice pendiente"}
    else:
        indice = {"valor": _redondeo(sum(calculables) / len(calculables)), "estado": "calculado", "etiqueta": "Índice de clima",
                  "dimensionesConsideradas": len(calculables)}

    part = participacion(m)
    return {
        "fuente": fuente,
        "calculadoEn": datetime.now(timezone.utc).isoformat(),
        "respuestasConsideradas": len(respuestas),
        "externas": sum(1 for r in m.respuestas if not r.es_prueba and es_externa(r)),
        "pruebas": sum(1 for r in m.respuestas if r.es_prueba),
        "participacion": part,
        "resumenParticipacion": resumen_participacion(m, part),
        "indice": indice,
        "dimensiones": dimensiones,
    }
