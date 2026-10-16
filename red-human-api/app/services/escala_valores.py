"""Escala de valores (2026-10-15, UVAQ) — modalidad «valores» de la Psicometría digital.

Seis valores calificados del 1 al 5: Dignidad, Espíritu de servicio, Honestidad, Responsabilidad, Compromiso y Trabajo en
equipo. Las reglas dependen del NIVEL del puesto (la actividad de la ruta lo trae en `evaluacion_psicometria.nivel`):

    Operativo  → promedio mínimo 3.5; alerta si algún valor es 1 o 2 (< 3)
    Jefatura   → promedio mínimo 4.0; alerta si algún valor es < 3
    Dirección  → promedio mínimo 4.2; alerta si algún valor es < 3.5

Las reglas NO son excluyentes: si el promedio no alcanza o hay una alerta, la actividad queda «Requiere revisión» (la ruta
se detiene y RH decide con «Continuar por decisión de RH» o descarta). Nunca descarta sola. Un valor sin dato también
manda a revisión (nunca se aprueba a ciegas).
"""

import unicodedata
from typing import Dict, Optional

VALORES = ("Dignidad", "Espíritu de servicio", "Honestidad", "Responsabilidad", "Compromiso", "Trabajo en equipo")
NIVELES = {
    "operativo": {"nombre": "Operativo", "promedio_min": 3.5, "alerta_bajo": 3.0},
    "jefatura": {"nombre": "Jefatura", "promedio_min": 4.0, "alerta_bajo": 3.0},
    "direccion": {"nombre": "Dirección", "promedio_min": 4.2, "alerta_bajo": 3.5},
}
ESCALA_MIN, ESCALA_MAX = 1, 5


def _plano(t: str) -> str:
    return " ".join(unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower().split())


_POR_PLANO = {_plano(v): v for v in VALORES}


def nivel_valido(nivel: str) -> Optional[str]:
    n = _plano(nivel).replace("ó", "o")
    return n if n in NIVELES else None


def puntajes_de(datos) -> Dict[str, float]:
    """{valor: puntaje} de lo que llegue: {"valores": {...}}, {«Dignidad»: 4, …} o la lista del proveedor
    (`psicometricas.puntajes_por_escala`). Solo los seis valores y solo números dentro de la escala 1-5."""
    from . import psicometricas as psi

    if isinstance(datos, dict) and isinstance(datos.get("valores"), dict):
        datos = datos["valores"]
    crudos = psi.puntajes_por_escala(datos) if datos else {}
    salida: Dict[str, float] = {}
    for k, v in (crudos or {}).items():
        nombre = _POR_PLANO.get(_plano(k))
        try:
            n = float(v)
        except (TypeError, ValueError):
            continue
        if nombre and ESCALA_MIN <= n <= ESCALA_MAX:
            salida[nombre] = n
    return salida


def evaluar(datos, nivel: str) -> dict:
    """{nivel, promedio, promedio_min, alertas[], sin_dato[], cumple, requiere_revision, detalle, puntajes{}}."""
    n = nivel_valido(nivel) or "operativo"
    regla = NIVELES[n]
    puntajes = puntajes_de(datos)
    sin_dato = [v for v in VALORES if v not in puntajes]
    promedio = round(sum(puntajes.values()) / len(puntajes), 2) if puntajes else None
    alertas = [f"{v}: {puntajes[v]:g}" for v in VALORES if v in puntajes and puntajes[v] < regla["alerta_bajo"]]
    bajo_promedio = promedio is None or promedio < regla["promedio_min"]
    cumple = not bajo_promedio and not alertas and not sin_dato
    partes = []
    if promedio is not None:
        partes.append(f"Promedio {promedio:g} (mínimo {regla['promedio_min']:g}, nivel {regla['nombre']})")
    if alertas:
        partes.append("Alerta: " + ", ".join(alertas))
    if sin_dato:
        partes.append("Sin dato: " + ", ".join(sin_dato))
    detalle = ("Cumple la escala de valores — " if cumple else "Requiere revisión — ") + "; ".join(partes or ["sin puntajes"])
    return {"modalidad": "valores", "nivel": n, "promedio": promedio, "promedio_min": regla["promedio_min"], "alertas": alertas,
            "sin_dato": sin_dato, "cumple": cumple, "requiere_revision": not cumple, "detalle": detalle,
            "puntajes": {v: puntajes[v] for v in VALORES if v in puntajes}}
