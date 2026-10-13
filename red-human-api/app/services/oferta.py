"""Cambio 30 (2026-10-13) — «Oferta laboral»: une «Propuesta y aceptación» y «Carta de intención».

Estado en `Postulacion.analisis["oferta"]`: {token, datos (carta), generada_en/por, enviada_en/por, entregada,
respuesta: acepta | no_acepta, respondida_en, canal, anteriores[]}. La actividad se cumple cuando el candidato ACEPTA; si
no acepta queda «Oferta no aceptada» (no favorable: la ruta se detiene y RH decide, nunca descarta). Las rutas congeladas
con «Propuesta y aceptación» siguen con su flujo (`services/propuesta.py`); una propuesta aceptada también cumple la
oferta.
"""

from typing import Optional

from ..models import Postulacion

CLAVE = "oferta"
ACTOR = "Red Human (oferta)"


def estado(p: Postulacion) -> dict:
    v = (p.analisis or {}).get(CLAVE)
    return dict(v) if isinstance(v, dict) else {}


def _fecha(texto) -> Optional[object]:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(texto).replace("Z", "+00:00")) if texto else None
    except ValueError:
        return None


def estado_paso(base: dict, p: Postulacion) -> dict:
    """Estado DERIVADO de la actividad «Oferta laboral»."""
    o = estado(p)
    if o.get("respuesta") == "acepta":
        return {**base, "estado": "completada", "resultado": "favorable", "cumple": True, "detalle": "Oferta aceptada",
                "revisadoPor": "Respuesta del candidato" if o.get("canal") != "rh" else "Revisado por: RH",
                "terminado_en": _fecha(o.get("respondida_en"))}
    if o.get("respuesta") == "no_acepta":
        return {**base, "estado": "completada", "resultado": "no_favorable", "cumple": False, "detalle": "Oferta no aceptada",
                "texto_no_aprobada": "Oferta no aceptada", "revisadoPor": "Respuesta del candidato",
                "terminado_en": _fecha(o.get("respondida_en"))}
    prop = (p.analisis or {}).get("propuesta") or {}
    if prop.get("estado") == "rechazada":
        return {**base, "estado": "completada", "resultado": "no_favorable", "cumple": False, "detalle": "Oferta no aceptada",
                "texto_no_aprobada": "Oferta no aceptada", "revisadoPor": "Respuesta del candidato"}
    if prop.get("estado") == "aceptada":  # flujo anterior (chat): también cumple la oferta
        return {**base, "estado": "completada", "resultado": "favorable", "cumple": True, "detalle": "Propuesta aceptada",
                "revisadoPor": "Respuesta del candidato"}
    if o.get("enviada_en") or prop.get("estado") == "enviada":
        return {**base, "estado": "en_curso", "espera": "Esperando la respuesta del candidato a la oferta",
                "cuello": {"clave": "esperando_candidato", "texto": "Esperando respuesta del candidato", "quien": None}}
    if p.etapa not in ("Contratación", "Onboarding"):
        return {**base, "espera": "La oferta se envía al llegar a Contratación"}
    return {**base, "espera": "Falta generar y enviar la oferta laboral",
            "cuello": {"clave": "pendiente_condiciones", "texto": "Pendiente: enviar oferta", "quien": None}}
