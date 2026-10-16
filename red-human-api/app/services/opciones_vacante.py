"""Sede y turno como preguntas de OPCIÓN (2026-10-16, decisiones del usuario).

* Vocabulario por Cuenta: cómo se llaman sus ubicaciones al hablar con el candidato («sede», «campus», «terminal»…).
  `Cuenta.termino_ubicacion` manda; vacío = se infiere del nombre de las ubicaciones de la vacante («Terminal Monterrey» →
  «terminal»; sin prefijo → «sede»). Así Fletes sigue diciendo «terminal» y UVAQ dice «sede».
* Un requisito de la vacante que pide trabajar en sus sedes («Disponibilidad para trabajar en Morelia, Pátzcuaro o Zamora»)
  se pregunta como la elección de sede («¿En qué sede te gustaría trabajar?») — UNA sola vez: en la solicitud web y el
  chat no la repite. Si es indispensable, «ninguna» descarta.
* Un requisito de turno con turnos capturados en la vacante (`Vacante.guiones["turnos"]`) se pregunta como «¿En qué turno
  te gustaría trabajar?» y se evalúa contra esos turnos (código, horario o «matutino»/«mixto»).
"""

import re
import unicodedata
from typing import List, Optional, Tuple

PREFIJOS = ("terminal", "campus", "sede", "sucursal", "planta", "cedis", "oficina", "tienda", "bodega", "plantel")
TERMINO_DEFAULT = "sede"
NINGUNA = "Ninguna de estas"
NINGUNO = "Ninguno de estos"
ID_TURNO = "turno-elegido"
PREGUNTA_TURNO = "¿En qué turno te gustaría trabajar?"
_DIAS = {"l-v": "lunes a viernes", "l-s": "lunes a sábado", "l-d": "lunes a domingo", "s-d": "fines de semana"}


def _n(t) -> str:
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9: ]", " ", t).split())


# ============================================================ vocabulario

def termino_ubicacion(v) -> str:
    """«sede» | «campus» | «terminal» | …: lo que dice el agente al nombrar las ubicaciones de esta vacante."""
    cuenta = getattr(v, "cuenta", None)
    explicito = _n(getattr(cuenta, "termino_ubicacion", "") or "")
    if explicito:
        return explicito
    from . import ubicaciones as subic

    prefijos = [(_n(u.nombre).split() or [""])[0] for u in subic.de_vacante(None, v)]
    for p in PREFIJOS:
        if prefijos and prefijos.count(p) * 2 >= len(prefijos):
            return p
    return TERMINO_DEFAULT


def nombre_largo(u) -> str:
    """«Terminal Monterrey» se queda igual; «Morelia» → «sede Morelia» (con el término de su Cuenta) — para frases como
    «te esperamos en la sede Morelia» o «La sede Morelia está en…»."""
    nombre = (getattr(u, "nombre", "") or "").strip()
    if not nombre or (_n(nombre).split() or [""])[0] in PREFIJOS:
        return nombre
    termino = ""
    try:
        from sqlalchemy.orm import object_session

        from ..models import Cuenta

        db = object_session(u)
        cuenta = db.get(Cuenta, u.cuenta_id) if db is not None and getattr(u, "cuenta_id", None) else None
        termino = _n(getattr(cuenta, "termino_ubicacion", "") or "")
    except Exception:  # noqa: BLE001
        termino = ""
    return f"{termino or TERMINO_DEFAULT} {nombre}"


def direccion_util(u) -> str:
    """La dirección para decírsela al candidato; «Conocido, …» (sin dirección exacta) no se dice."""
    d = (getattr(u, "direccion", "") or "").strip().rstrip(".")
    return "" if _n(d).startswith("conocido") else d


def pregunta_ubicacion(v) -> str:
    return f"¿En qué {termino_ubicacion(v)} te gustaría trabajar?"


def texto_propuesta(v, nombre_corto: str) -> str:
    return f"Tenemos {termino_ubicacion(v)} en {nombre_corto}, ¿te queda bien?"


# ============================================================ sede

_RE_LUGAR = re.compile(r"\b(trabajar en|sede|sedes|campus|terminal|terminales|sucursal|plantel|ubicacion|ubicaciones)\b")


def es_requisito_sede(v, *textos) -> bool:
    """¿Este requisito/pregunta es «trabajar en alguna de las sedes» de una vacante con varias ubicaciones?"""
    from . import ubicaciones as subic

    ubics = subic.de_vacante(None, v)
    if len(ubics) < 2:
        return False
    t = f" {_n(' '.join(str(x or '') for x in textos))} "
    if _n(pregunta_ubicacion(v)) in t or _n(subic.PREGUNTA_TERMINAL) in t:
        return True
    nombres = sum(1 for u in ubics if f" {_n(subic.corto(u))} " in t)
    return nombres >= 2 or (nombres >= 1 and bool(_RE_LUGAR.search(t)))


def pregunta_web_sede(v, descarta: bool, base: Optional[dict] = None) -> dict:
    from . import ubicaciones as subic

    opciones = [u.nombre for u in subic.de_vacante(None, v)] + ([NINGUNA] if descarta else [])
    return {"id": subic.ID_PREGUNTA, "pregunta": pregunta_ubicacion(v), "tipo": "opcion",
            "valida": (base or {}).get("valida") or "Ubicación elegida", "respuesta_esperada": "", "descarta": bool(descarta),
            "opciones": opciones, "ubicacion": True, "base": not descarta, "dato": "terminal"}


_RE_VIVE = re.compile(r"\b(vivo|soy de|estoy en|radico|resido|mi casa|cerca de|por el rumbo de)\b")
_RE_NIEGA = re.compile(r"^(no|ninguna?|ninguno|ninguna de estas|ninguno de estos|tampoco|no puedo|no me queda|no me acomoda|"
                       r"ninguna me queda|ninguno me queda|no se puede)\b")


def niega(texto: str) -> bool:
    return bool(_RE_NIEGA.match(_n(texto)))


def vive_en(texto: str) -> bool:
    """«Vivo en Pátzcuaro» habla de dónde vive, no de una elección: el agente PROPONE esa sede."""
    return bool(_RE_VIVE.search(_n(texto)))


def propuesta_por_zona(v, zona: str) -> Optional[dict]:
    """La opción de ubicación que corresponde al municipio donde dice vivir el candidato (o None)."""
    from . import ubicaciones as subic

    if not (zona or "").strip():
        return None
    return subic.elegir_en([subic.opcion(u) for u in subic.de_vacante(None, v)], zona)


# ============================================================ turno

def turnos(v) -> List[Tuple[str, str]]:
    """[(código, horario)] de `Vacante.guiones["turnos"]` ({código: «06:00–14:00 L-V»} o lista de códigos/horarios)."""
    t = ((getattr(v, "guiones", None) or {}).get("turnos")) or {}
    if isinstance(t, dict):
        return [(str(k), str(x)) for k, x in t.items() if str(x or "").strip()]
    return [(str(x), str(x)) for x in t if str(x or "").strip()]


def _horas(horario: str) -> Optional[Tuple[int, int]]:
    m = re.search(r"(\d{1,2})(?::\d{2})?\s*(?:–|-|a)\s*(\d{1,2})(?::\d{2})?", horario or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def texto_turno(horario: str) -> str:
    """«06:00–14:00 L-V» → «de 6:00 a 14:00»."""
    h = _horas(horario)
    return f"de {h[0]}:00 a {h[1]}:00" if h else horario.strip()


def dias_turnos(v) -> str:
    dias = {next((d for k, d in _DIAS.items() if k in _n(x).replace(" ", "-")), "") for _c, x in turnos(v)}
    return dias.pop() if len(dias) == 1 else ""


def lista_turnos(v) -> str:
    textos = [texto_turno(x) for _c, x in turnos(v)]
    if not textos:
        return ""
    cuerpo = textos[0] if len(textos) == 1 else ", ".join(textos[:-1]) + f" o {textos[-1]}"
    dias = dias_turnos(v)
    return f"{cuerpo}{', de ' + dias if dias else ''}"


def es_requisito_turno(v, *textos) -> bool:
    return bool(turnos(v)) and bool(re.search(r"\bturnos?\b", _n(" ".join(str(x or "") for x in textos))))


def pregunta_web_turno(v, descarta: bool, base: Optional[dict] = None) -> dict:
    dias = dias_turnos(v)
    opciones = [f"{texto_turno(x).capitalize()}{' (' + dias + ')' if dias else ''}" for _c, x in turnos(v)]
    return {"id": ID_TURNO, "pregunta": PREGUNTA_TURNO, "tipo": "opcion", "valida": (base or {}).get("valida") or "Turno",
            "respuesta_esperada": "", "descarta": bool(descarta), "opciones": opciones + ([NINGUNO] if descarta else []),
            "base": False, "dato": "turno"}


def criterio_turno(v, base: dict) -> dict:
    """Criterio del chat (dato «turno»): opciones dichas en una frase; un «ninguno» de un indispensable descarta."""
    lista = lista_turnos(v)
    textos = [base.get("pregunta"), base.get("pregunta_web"), PREGUNTA_TURNO]
    return {"id": ID_TURNO, "clave": "turno", "dato": "turno", "criterio": base.get("criterio") or "Turno",
            "pregunta": PREGUNTA_TURNO, "chat": f"{PREGUNTA_TURNO} Tenemos {lista}.", "indispensable": bool(base.get("indispensable")),
            "esperada": "si", "tipo": "dato", "turnos": [[c, x] for c, x in turnos(v)], "textos_web": [x for x in textos if x],
            **({} if base.get("indispensable") else {"registra": True}),
            "repregunta": f"¿Cuál te acomoda mejor: {lista}?"}


def elegir_turno(lista: List[Tuple[str, str]], texto: str) -> Optional[str]:
    """El código del turno que nombra el candidato (código, opción, horario de inicio o «matutino»/«mixto»). Ambiguo → None.
    `lista` = [(código, horario)] (`turnos(v)` o el `turnos` del criterio)."""
    t = _n(texto)
    lista = [tuple(x) for x in lista or []]
    if not t or not lista:
        return None
    por_codigo = [c for c, _x in lista if _n(c) in t or _n(c).replace(" ", "") in t.replace(" ", "")]
    if len(por_codigo) == 1:
        return por_codigo[0]
    numeros = [int(x) for x in re.findall(r"\b(\d{1,2})(?::\d{2})?\b", t)]
    por_hora = [c for c, x in lista if _horas(x) and numeros and numeros[0] == _horas(x)[0]]
    if len(por_hora) == 1:
        return por_hora[0]
    if re.search(r"\b(matutino|manana|temprano)\b", t):
        cand = [c for c, x in lista if _horas(x) and _horas(x)[0] < 12]
    elif re.search(r"\b(mixto|tarde|vespertino)\b", t):
        cand = [c for c, x in lista if _horas(x) and _horas(x)[0] >= 12]
    else:
        cand = []
    return cand[0] if len(cand) == 1 else None


def clasificar_turno(lista: List[Tuple[str, str]], texto: str) -> Tuple[Optional[str], dict]:
    """(«si», {turno}) · («no», {}) con «ninguno» · (None, {}) si no se entiende (repregunta una vez)."""
    if _n(texto) in (_n(NINGUNO), _n(NINGUNA)):
        return "no", {}
    codigo = elegir_turno(lista, texto)
    if codigo is None:
        # la opción tal como la muestra el formulario web («De 6:00 a 14:00 (lunes a viernes)»)
        codigo = next((c for c, x in lista if _n(texto_turno(x)) in _n(texto)), None)
    if codigo:
        return "si", {"turno": codigo}
    if niega(texto):
        return "no", {}
    from .prefiltro_conversacional import clasificar

    if clasificar({"criterio": "", "pregunta": "", "chat": ""}, texto) == "si" and re.search(r"\b(cualquier|cualquiera|todos|el que sea)\b", _n(texto)):
        return "si", {"turno": "cualquiera"}
    return None, {}
