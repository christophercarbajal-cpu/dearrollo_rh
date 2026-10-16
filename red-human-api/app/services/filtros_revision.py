"""Filtros que mandan a «Requiere revisión» en vez de descartar (2026-10-15, UVAQ).

Una vacante puede marcar preguntas de su prefiltro (web o WhatsApp) con una regla de REVISIÓN — no excluyente:
    {"pregunta": "<texto exacto>", "tipo": "no" | "minimo" | "maximo", "valor": n, "unidad": "anios" | "pesos",
     "motivo": "Experiencia menor a 6 meses"}
Se guardan en `Vacante.guiones["filtros_revision"]` y se reconocen por el TEXTO de la pregunta (el mismo en la solicitud
web y en el chat). Si la respuesta cae en la regla:
  · solicitud web → `analisis.solicitud_web.revision` (la actividad «Solicitud web» queda «Requiere revisión»);
  · prefiltro por WhatsApp → el prefiltro termina en «revision» (`prefiltro_conversacional._evaluar`).
La ruta se detiene y RH decide («Continuar por decisión de RH» o descartar). Nunca descarta sola. Una respuesta que no se
entiende no dispara la regla (para eso están la repregunta y «Sin respuesta»).
"""

import re
import unicodedata
from typing import List, Optional

CLAVE = "filtros_revision"
TIPOS = ("no", "minimo", "maximo")


def _n(t: str) -> str:
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[¿?¡!.,;:()\"']", " ", t).split())


def reglas(v) -> List[dict]:
    return [r for r in (((getattr(v, "guiones", None) or {}).get(CLAVE)) or [])
            if isinstance(r, dict) and r.get("pregunta") and r.get("tipo") in TIPOS]


def regla_para(v, *preguntas: str) -> Optional[dict]:
    planos = {_n(q) for q in preguntas if q}
    return next((r for r in reglas(v) if _n(r["pregunta"]) in planos), None)


_NUM = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
        "nueve": 9, "diez": 10, "once": 11, "doce": 12, "quince": 15, "veinte": 20, "medio": 0.5}


def _numero(t: str) -> Optional[float]:
    m = re.search(r"(\d+(?:[.,]\d+)?)", t)
    if m:
        return float(m.group(1).replace(",", "."))
    for w in t.split():
        if w in _NUM:
            return float(_NUM[w])
    return None


def anios(texto: str) -> Optional[float]:
    """«8 meses» → 0.67 · «2 años» → 2 · «año y medio» → 1.5 · «3» → 3 (años) · «nada» / «ninguna» → 0."""
    t = _n(texto)
    if not t:
        return None
    if re.search(r"\b(nada|ninguna|ninguno|sin experiencia|nunca|no tengo)\b", t):
        return 0.0
    if re.search(r"\bano y medio\b", t):
        return 1.5
    n = _numero(t)
    if n is None:
        return 1.0 if re.search(r"\b(un ano|1 ano)\b", t) else None
    if re.search(r"\bmes", t) and not re.search(r"\bano", t):
        return round(n / 12, 2)
    if re.search(r"\bsemana", t):
        return round(n / 52, 2)
    return n


def pesos(texto: str) -> Optional[float]:
    """«$32,000» → 32000 · «35 mil» → 35000 · «35k» → 35000."""
    t = _n(texto).replace(" ", "") if re.search(r"\d[\s,.]\d{3}", _n(texto)) else _n(texto)
    m = re.search(r"(\d+(?:[.,]\d+)?)", t.replace(",", "") if re.search(r"\d,\d{3}", t) else t)
    if not m:
        return None
    n = float(m.group(1).replace(",", "."))
    if re.search(r"\b(mil|k)\b|\d(mil|k)\b", t) and n < 1000:
        n *= 1000
    return n


def falla(regla: dict, texto: str) -> Optional[str]:
    """El motivo de revisión si la respuesta cae en la regla; None si no cae (o no se entiende)."""
    if not regla or not str(texto or "").strip():
        return None
    tipo = regla.get("tipo")
    motivo = regla.get("motivo") or f"Revisar: {regla['pregunta']}"
    if tipo == "no":
        from .prefiltro_conversacional import clasificar

        return motivo if clasificar({"criterio": "", "pregunta": "", "chat": ""}, texto) == "no" else None
    medir = pesos if regla.get("unidad") == "pesos" else anios
    n = medir(texto)
    if n is None:
        return None
    valor = float(regla.get("valor") or 0)
    if tipo == "minimo" and n < valor:
        return f"{motivo} (respondió: «{str(texto).strip()[:80]}»)"
    if tipo == "maximo" and n > valor:
        return f"{motivo} (respondió: «{str(texto).strip()[:80]}»)"
    return None


def web(p) -> List[str]:
    """Motivos de revisión en las respuestas de «Unas preguntas» de la solicitud web."""
    respuestas = {_n(r.get("pregunta")): str(r.get("respuesta") or "") for r in (p.analisis or {}).get("respuestas_web") or []
                  if isinstance(r, dict)}
    salida = []
    for r in reglas(p.vacante):
        m = falla(r, respuestas.get(_n(r["pregunta"]), ""))
        if m:
            salida.append(m)
    return salida


def anotar(criterios: List[dict], v) -> List[dict]:
    """Marca los criterios del prefiltro por WhatsApp que traen regla de revisión (`revision_regla`)."""
    if not reglas(v):
        return criterios
    for c in criterios:
        r = regla_para(v, c.get("chat"), c.get("pregunta"), c.get("pregunta_web"))
        if r:
            c["revision_regla"] = r
    return criterios
