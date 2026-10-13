"""Cambio 29 (2026-10-12) — prefiltro FIJO por WhatsApp para rutas Masivos (operativos). Cero IA generativa.

En una ruta Masivos (`proceso.ruta_es_masiva`) el prefiltro de WhatsApp no se genera: sale de esta base fija, una
pregunta por mensaje y SIEMPRE en este orden:

1. Saludo exacto (`prefiltro_conversacional.saludo`) + la primera pregunta indispensable.
2. Cada indispensable de la vacante, confirmado con un DATO CONCRETO (nunca Sí/No): escolaridad («¿Hasta qué año
   estudiaste?»), experiencia («¿Cuánto tiempo de experiencia…?»), licencia («¿Qué tipo de licencia tienes?»),
   disponibilidad y cualquier otro («¿Cuál es tu caso?»). Si el dato no lo cumple → descarta.
3. «¿Cuántos años tienes?» — se registra; menor de 18 → Puntos por validar (descarta solo si la vacante pide una edad).
4. «El puesto es [puesto]. ¿Te interesa?» · 5. «El sueldo es de [sueldo]. ¿Te interesa?» (se registran).
6. Colonia y municipio · 7. «El trabajo es en [ubicación]. ¿Puedes trasladarte?» · 8. «¿Cuánto tiempo harías de tu
   casa al trabajo?» — descarta SOLO si la vacante tiene un indispensable con límite de traslado.
Sin pregunta de transporte y sin validar habilidades blandas (autopercepciones o «soft skills» de los indispensables).
"""

import re
import unicodedata
from typing import List, Optional, Tuple

EDAD_MINIMA = 18
REPREGUNTAS = {
    "experiencia": "Dime el tiempo exacto, por ejemplo «3 años» o «8 meses».",
    "licencia": "¿De qué tipo es tu licencia? Por ejemplo, «tipo B».",
    "disponibilidad": "Cuéntame en concreto qué días o turnos puedes trabajar.",
    "requisito": "Dame un dato concreto: dónde, cuánto tiempo o de qué forma lo cumples.",
    "edad": "Dime tu edad en número, por ejemplo «25».",
    "interes": "Solo para confirmar: ¿te interesa el puesto?",
    "puede_trasladarse": "Solo para confirmar: ¿puedes trasladarte hasta allá?",
    "traslado": "¿Cuántos minutos más o menos? Por ejemplo, «40 minutos» o «1 hora y media».",
    "sueldo": "Solo para confirmar: ¿te interesa el sueldo?",
    "zona": "¿Me dices el nombre de tu colonia y de tu municipio?",
}
DATOS_OPERATIVOS = ("experiencia", "licencia", "disponibilidad", "requisito", "edad", "interes", "puede_trasladarse")


def _n(t: str) -> str:
    return unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower().strip()


# ============================================================ ¿aplica?

def es_ruta_operativa(pasos_o_proceso) -> bool:
    from .proceso import ruta_es_masiva

    return bool(pasos_o_proceso) and ruta_es_masiva(pasos_o_proceso)


def aplica(v, p=None) -> bool:
    """La postulación (o, sin ella, la vacante) sigue una ruta Masivos."""
    if p is not None and (getattr(p, "proceso", None) or {}).get("pasos"):
        return es_ruta_operativa(p.proceso)
    if v is None:
        return False
    try:
        from sqlalchemy.orm import object_session

        from .guiones import pasos_de_vacante

        return es_ruta_operativa(pasos_de_vacante(object_session(v), v))
    except Exception:  # noqa: BLE001
        return False


# ============================================================ tipos de indispensable

_RE_EDAD_RANGO = re.compile(r"\b(\d{2})\s*(?:a|y|-|–)\s*(\d{2})\s*anos\b")
_RE_EDAD_MIN = re.compile(r"\b(?:mayor(?:es)? de|minimo|al menos|desde|mas de)\s*(\d{2})\s*anos\b")
_RE_EDAD_MAX = re.compile(r"\b(?:menor(?:es)? de|maximo|hasta)\s*(\d{2})\s*anos\b")
_RE_TRASLADO = re.compile(r"\b(traslado|vivir|vive|vivas|cerca|radio|distancia|de la planta|del trabajo)\b")
_RE_DISPONIBILIDAD = re.compile(r"\b(disponibilidad|turnos?|horario|fines? de semana|rolar|nocturn\w*|tiempo completo|horas extra)\b")
_RE_LICENCIA = re.compile(r"\blicencia\b")
_RE_LETRA_LICENCIA = re.compile(r"\btipo\s*([a-e])\b|\b([a-e])\s*$")


def edad_de_requisito(req: str) -> Optional[Tuple[Optional[int], Optional[int]]]:
    """«Mayor de edad» → (18, None); «18 a 45 años» → (18, 45); «Mayor de 21 años» → (21, None). None = no es de edad."""
    t = _n(req)
    if "mayor de edad" in t:
        return EDAD_MINIMA, None
    m = _RE_EDAD_RANGO.search(t)
    if m and not re.search(r"experiencia", t):
        return int(m.group(1)), int(m.group(2))
    lo = _RE_EDAD_MIN.search(t)
    hi = _RE_EDAD_MAX.search(t)
    if (lo or hi) and not re.search(r"experiencia", t):
        return (int(lo.group(1)) if lo else None), (int(hi.group(1)) if hi else None)
    return None


def limite_traslado(req: str) -> Optional[int]:
    """Límite de traslado (minutos) de un indispensable: «Vivir a máximo 40 minutos de la planta» → 40."""
    t = _n(req)
    if not _RE_TRASLADO.search(t):
        return None
    if re.search(r"\bmedia hora\b", t):
        return 30
    m = re.search(r"\b(\d{1,3})\s*(min|minutos|hr|hrs|hora|horas)\b", t)
    if m:
        n = int(m.group(1))
        return n * 60 if m.group(2).startswith("h") else n
    if re.search(r"\buna hora\b", t):
        return 60
    return None


def es_blanda(req: str) -> bool:
    from . import ia
    from .entrevista_operativa import es_soft_skill

    return ia.es_autopercepcion(req) or es_soft_skill(req)


def _criterio(cid: str, dato: str, criterio: str, pregunta: str, indispensable: bool = False, **extra) -> dict:
    return {"id": cid, "clave": dato, "dato": dato, "criterio": criterio.strip(), "pregunta": pregunta, "chat": pregunta,
            "indispensable": indispensable, "esperada": "si", "tipo": "dato", "operativo": True, "base_operativa": True,
            "repregunta": REPREGUNTAS.get(dato), **extra}


def _pregunta_indispensable(i: int, req: str, natural: str = "") -> dict:
    """Un indispensable como pregunta de DATO CONCRETO (nunca Sí/No). Cambio 31: con la pregunta natural de la vacante
    (`natural`), el chat la usa TAL CUAL; el tipo de dato solo decide cómo se evalúa la respuesta."""
    c = _pregunta_indispensable_base(i, req)
    if natural:
        c["pregunta"] = c["chat"] = natural
    return c


def _pregunta_indispensable_base(i: int, req: str) -> dict:
    from . import ia
    from .prefiltro_conversacional import nivel_estudios

    r = " ".join(req.split()).strip(" .")
    t = _n(r)
    cid = f"ind{i + 1}"
    if ia.RE_NIVEL_ESTUDIOS.search(r):
        nivel = nivel_estudios(r)[0]
        return {**_criterio(cid, "escolaridad", r, "¿Hasta qué año estudiaste?", True), "nivel_requerido": nivel,
                "repregunta": None, "operativo": False}
    m = ia._RE_ANOS.search(r)
    if m or "experiencia" in t:
        n = float(m.group(1).replace(",", ".")) if m else 0.0
        if m and m.group(2).lower().startswith("mes"):
            n = n / 12
        tema = ia._tema_experiencia(r)
        return _criterio(cid, "experiencia", r, f"¿Cuánto tiempo {ia._de_experiencia(tema)} tienes?", True, minimo=n)
    if _RE_LICENCIA.search(t):
        letra = _RE_LETRA_LICENCIA.search(t)
        return _criterio(cid, "licencia", r, "¿Qué tipo de licencia tienes?", True,
                         letra=(letra.group(1) or letra.group(2)) if letra else "")
    if _RE_DISPONIBILIDAD.search(t):
        tema = re.sub(r"^disponibilidad\s+(?:para|de)\s+", "", r, flags=re.IGNORECASE)
        tema = tema[:1].lower() + tema[1:] if tema[:2] != tema[:2].upper() else tema
        pregunta = (f"¿Qué disponibilidad tienes para {tema}?" if tema != r else "¿Qué días y horarios puedes trabajar?")
        return _criterio(cid, "disponibilidad", r, pregunta, True)
    req_min = r[:1].lower() + r[1:] if r[:2] != r[:2].upper() else r
    return _criterio(cid, "requisito", r, f"Para esta vacante se pide: {req_min}. ¿Cuál es tu caso?", True)


def _texto_sueldo(sueldo: str) -> str:
    from . import ia

    q = ia.pregunta_sueldo(sueldo or "")
    return q.replace("¿Te parece bien?", "¿Te interesa?") if q else ""


def base(titulo: str, sueldo: str, ubicacion: str, indispensables: List[str], naturales: Optional[dict] = None) -> List[dict]:
    """La base FIJA del prefiltro operativo (criterios del motor conversacional), en su orden."""
    salida: List[dict] = []
    edad: Optional[Tuple[Optional[int], Optional[int]]] = None
    limite: Optional[int] = None
    limite_req = ""
    edad_req = ""
    for i, req in enumerate(r for r in indispensables or [] if str(r or "").strip()):
        if es_blanda(req):
            continue  # Cambio 29: nunca se validan habilidades blandas en el prefiltro
        e = edad_de_requisito(req)
        if e is not None:
            edad, edad_req = e, req
            continue
        lim = limite_traslado(req)
        if lim is not None:
            limite, limite_req = lim, req
            continue
        from . import ia

        salida.append(_pregunta_indispensable(i, req, (naturales or {}).get(ia._norm_req(req), "")))
    # Edad: se registra; descarta solo si la vacante pide una edad (indispensable)
    salida.append(_criterio("dato-edad", "edad", edad_req or "Edad", "¿Cuántos años tienes?", edad is not None,
                            edad_min=(edad or (None, None))[0], edad_max=(edad or (None, None))[1]))
    titulo = (titulo or "").strip() or "esta vacante"
    salida.append(_criterio("dato-interes", "interes", f"Interés en el puesto: {titulo}", f"El puesto es {titulo}. ¿Te interesa?"))
    q_sueldo = _texto_sueldo(sueldo)
    if q_sueldo:
        salida.append({**_criterio("dato-sueldo", "sueldo", f"Sueldo: {sueldo.strip()}", q_sueldo), "operativo": False})
    salida.append({**_criterio("dato-zona", "zona", "Colonia y municipio", "¿En qué colonia y municipio vives?"), "operativo": False})
    lugar = (ubicacion or "").strip()
    if lugar:
        salida.append(_criterio("dato-puede-trasladarse", "puede_trasladarse", f"Trasladarse a {lugar}",
                                f"El trabajo es en {lugar}. ¿Puedes trasladarte?"))
    traslado = _criterio("dato-traslado", "traslado", limite_req or "Tiempo de traslado", "¿Cuánto tiempo harías de tu casa al trabajo?",
                         limite is not None)
    if limite is not None:
        traslado["limite_min"] = limite
    else:
        traslado["operativo"] = False  # sin límite en la vacante: el tiempo solo se registra
    salida.append(traslado)
    return salida


def criterios(v) -> List[dict]:
    from .prefiltro_conversacional import ubicacion_vacante
    from ..routers.vacantes import requisitos_lista

    from .guiones import preguntas_requisitos

    return base(getattr(v, "titulo", "") or "", getattr(v, "sueldo", "") or "", ubicacion_vacante(v),
                requisitos_lista(getattr(v, "requisitos", "") or ""), preguntas_requisitos(v))


def preguntas_guardables(ficha) -> List[dict]:
    """La misma base como «Prefiltro · WhatsApp» de la vacante (lo que RH ve en «Plantillas de conversación»)."""
    from . import ia

    salida = []
    for c in base(ficha.titulo, ficha.sueldo, ficha.ubicacion, list(ficha.requisitos_indispensables)):
        salida.append({"pregunta": c["pregunta"], "tipo": "dato", "dato": c["dato"], "valida": c["criterio"],
                       "respuesta_esperada": "", "descarta": bool(c["indispensable"]), "opciones": [], "base_operativa": True,
                       "regla": ia.regla_descarta(c["criterio"]) if c["indispensable"] else ia.REGLA_REGISTRA})
    return salida


# ============================================================ clasificación y registro

_RE_NIEGA = re.compile(r"^(no|nop|nel|ninguna?|nada|nunca|tampoco|no tengo|no cuento|no puedo|no se|sin)\b")


def _si_no(texto: str) -> Optional[str]:
    from .prefiltro_conversacional import clasificar

    return clasificar({"criterio": "", "pregunta": "", "chat": ""}, texto)


def _edad(texto: str) -> Optional[int]:
    m = re.search(r"\b(\d{2})\b", texto or "")
    if not m:
        return None
    n = int(m.group(1))
    return n if 14 <= n <= 90 else None


def clasificar(c: dict, texto: str) -> Tuple[Optional[str], dict]:
    """(valor, extra) de un dato del prefiltro operativo. valor None = vaga (se repregunta una vez)."""
    from .prefiltro_conversacional import _anos, limpiar, minutos_de

    t = limpiar(texto)
    if not t:
        return None, {}
    dato = c.get("dato")
    niega = bool(_RE_NIEGA.match(t))
    if dato == "experiencia":
        if niega or re.search(r"\b(ninguna|sin experiencia|nunca)\b", t):
            return "no", {"anos": 0}
        n = _anos(t)
        if n is None:
            return None, {}
        return ("si" if n >= float(c.get("minimo") or 0) else "no"), {"anos": round(n, 2)}
    if dato == "licencia":
        if niega:
            return "no", {}
        letra = re.search(r"\b(?:tipo\s*)?([a-e])\b", t)
        if letra is None and len(t.split()) <= 2 and _si_no(texto) == "si":
            return None, {}  # «sí» a secas no es un dato concreto
        extra = {"tipo": letra.group(1).upper() if letra else ""}
        if c.get("letra") and letra and letra.group(1) != c["letra"]:
            extra["otra_letra"] = True  # otra letra: no se descarta, va a Puntos por validar
        return "si", extra
    if dato in ("disponibilidad", "requisito"):
        if niega:
            return "no", {}
        if _si_no(texto) == "si" and len(t.split()) <= 2:
            return None, {}  # «sí» a secas: se pide el dato concreto una vez
        return "si", {"concreto": texto.strip()[:200]}
    if dato == "edad":
        n = _edad(t)
        if n is None:
            return None, {}
        lo, hi = c.get("edad_min"), c.get("edad_max")
        ok = (lo is None or n >= lo) and (hi is None or n <= hi)
        return ("si" if ok or not c.get("indispensable") else "no"), {"edad": n}
    if dato in ("interes", "puede_trasladarse"):
        v = _si_no(texto)
        return (None, {}) if v is None else (v, {"acepta": v == "si"})
    if dato == "traslado":
        mins = minutos_de(texto)
        if mins is None:
            return None, {}
        return ("si" if mins <= int(c.get("limite_min") or 10 ** 6) else "no"), {"minutos": mins}
    return "si", {}


def guardar(a: dict, d: dict, c: dict, texto: str, extra: dict) -> None:
    """Datos del prefiltro operativo en `analisis.prefiltro_datos` y lo que va a Puntos por validar (nunca descarta)."""
    puntos = list(a.get("puntos_validar_prefiltro") or [])
    dato = c["dato"]
    if dato == "edad" and extra.get("edad") is not None:
        d["edad"] = extra["edad"]
        if extra["edad"] < EDAD_MINIMA and not c.get("indispensable"):
            puntos.append(f"Menor de {EDAD_MINIMA} años: {extra['edad']}")
    elif dato == "interes":
        d["interes_puesto"] = extra.get("acepta")
        if extra.get("acepta") is False:
            puntos.append("No le interesa el puesto")
    elif dato == "puede_trasladarse":
        d["puede_trasladarse"] = extra.get("acepta")
        if extra.get("acepta") is False:
            puntos.append(f"No puede trasladarse: {c['criterio'].removeprefix('Trasladarse a ')}")
    elif dato == "licencia" and extra.get("otra_letra"):
        puntos.append(f"Licencia tipo {extra.get('tipo')}; la vacante pide: {c['criterio']}")
    elif dato == "experiencia" and extra.get("anos") is not None:
        d.setdefault("experiencia", {})
        d["experiencia"] = {**(d.get("experiencia") if isinstance(d.get("experiencia"), dict) else {}), c["criterio"]: extra["anos"]}
    if dato == "traslado" and extra.get("minutos") is not None:
        d["traslado_min"] = extra["minutos"]
    a["puntos_validar_prefiltro"] = puntos
