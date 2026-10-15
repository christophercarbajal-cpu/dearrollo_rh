"""Cambio 37 (2026-10-14) — «Preguntas filtro»: UNA sola entidad por vacante.

Reemplaza las tres listas anteriores (Prefiltro · Web, Prefiltro · WhatsApp y «Cómo pregunta Red Human cada requisito»).
Vive en `Vacante.guiones["preguntas_filtro"]` y de ella salen:

* el formulario web (`Vacante.preguntas_filtro`, DERIVADO por `web`): las preguntas que se contestan con Sí/No, número u
  opción; mínimo `MIN_WEB` (si faltan, se completa con preguntas base);
* el prefiltro del chat (`criterios`): todas, una por mensaje — lo que ya contestó en la web no se vuelve a preguntar;
* la pregunta natural de cada indispensable (`guiones["preguntas_requisitos"]`, derivada, compatibilidad).

Cada requisito INDISPENSABLE genera exactamente UNA pregunta: texto en lenguaje natural (nunca el texto base del
requisito envuelto en «¿Cumples con…?» ni «¿Cuál es tu caso?»), tipo (Sí/No, número u opción) y regla de descarte.
Las preguntas base (edad, interés, sueldo, colonia y traslado) llevan la etiqueta «Base» y NO descartan, salvo la edad
o el traslado cuando la vacante tiene un requisito de edad o de traslado (entonces esa pregunta ES la del requisito).
Solo una pregunta con respuesta verificable (Sí/No o dato comparable) puede descartar; una abierta nunca descarta.

Reglas del bot (`clasificar`): se descarta SOLO con una respuesta clara que no cumple (un «no»/«no tengo» explícito o un
dato comparable que no alcanza); una respuesta afirmativa sin el dato, parcial, ambigua o evasiva genera UNA repregunta
concreta y, si sigue sin resolverse, queda «Requiere revisión» (RH decide). «Claro. Solo para tenerlo en cuenta…» es
exclusiva de la pregunta de sueldo.
"""

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

CLAVE = "preguntas_filtro"
CLAVE_PROPUESTOS = "indispensables_propuestos"
TIPOS = ("si_no", "numero", "opcion", "abierta")
TIPOS_WEB = ("si_no", "numero", "opcion")
BASES = ("edad", "interes", "sueldo", "zona", "traslado")
MIN_WEB = 3
MAX_INDISPENSABLES = 5
# Cambio 48: jerarquía ORDINAL (Primaria < Secundaria < Preparatoria < Licenciatura < Posgrado)
NIVELES = ["Primaria", "Secundaria", "Preparatoria", "Licenciatura", "Posgrado"]
ETIQUETA_BASE = "Base"
REGLA_NO_DESCARTA = "No descarta"
# Frases de formulario prohibidas en el texto de una pregunta (Cambio 37)
_RE_PROHIBIDAS = re.compile(r"(cu[aá]l es tu caso|para esta vacante se pide|cumples con|este requisito|para confirmar|"
                            r"primero conf[ií]rmame|para no equivocarme|cu[aá]l de estas opciones)", re.IGNORECASE)
# «Claro. Solo para tenerlo en cuenta, …» SOLO en la pregunta de sueldo
FRASE_SUELDO = "Claro. Solo para tenerlo en cuenta,"
REPREGUNTAS = {
    "experiencia": "¿Cuántos años de experiencia tienes en total, aproximadamente?",
    "escolaridad": "¿Hasta qué grado llegaste en la escuela?",
    "licencia": "¿Qué tipo de licencia de manejo tienes?",
    "transportes": "¿Cuántos transportes tomarías en total para llegar?",
    "edad": "¿Qué edad tienes?",
    "interes": "¿El puesto te interesa? Respóndeme sí o no.",
    "sueldo": f"{FRASE_SUELDO} ¿te interesa el sueldo?",
    "zona": "¿Me dices el nombre de tu colonia y de tu municipio?",
    "traslado": "¿Cuántos minutos te tomaría llegar?",
}


def _n(t: str) -> str:
    return " ".join(unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower().split())


def _texto(x, n: int = 300) -> str:
    return " ".join(str(x or "").split())[:n]


def _norm_req(t: str) -> str:
    from . import ia

    return ia._norm_req(t)


def tiene_frase_prohibida(texto: str) -> bool:
    return bool(_RE_PROHIBIDAS.search(texto or ""))


def _pregunta_valida(q: str) -> bool:
    q = _texto(q)
    return bool(q) and q.count("?") == 1 and q.endswith("?") and not tiene_frase_prohibida(q) and len(q.split()) <= 22


# ============================================================ tipos de requisito

# Rasgos de personalidad (no verificables con un dato): su pregunta es abierta y nunca descarta
_RE_RASGO = re.compile(r"^(ser |persona )?(responsable|puntual|honest[oa]|honrad[oa]|organizad[oa]|ordenad[oa]|trabajador[a]?|"
                       r"comprometid[oa]|paciente|amable|respetuos[oa]|disciplinad[oa]|dinamic[oa]|atent[oa]|tolerante|sociable|"
                       r"servicial|confiable|limpi[oa]|amabilidad|responsabilidad|puntualidad|honestidad|compromiso)\b")


def tipo_requisito(req: str) -> dict:
    """Qué DATO verifica un requisito indispensable y con qué parámetros. dato ∈ escolaridad | experiencia | transportes |
    licencia | disponibilidad | edad | traslado | blanda | requisito."""
    from . import ia
    from . import prefiltro_operativo as pop
    from .prefiltro_conversacional import nivel_estudios

    r = " ".join(str(req or "").split()).strip(" .")
    t = _n(r)
    if ia.RE_NIVEL_ESTUDIOS.search(r):
        return {"dato": "escolaridad", "nivel": nivel_estudios(r)[0]}
    edad = pop.edad_de_requisito(r)
    if edad is not None:
        return {"dato": "edad", "edad_min": edad[0], "edad_max": edad[1]}
    lim = pop.limite_traslado(r)
    if lim is not None:
        return {"dato": "traslado", "limite_min": lim}
    tope = pop.maximo_transportes(r)
    if tope is not None:
        return {"dato": "transportes", "maximo": tope}
    m = ia._RE_ANOS.search(r)
    if m or "experiencia" in t:
        n = float(m.group(1).replace(",", ".")) if m else 0.0
        if m and m.group(2).lower().startswith("mes"):
            n = n / 12
        return {"dato": "experiencia", "minimo": round(n, 2), "tema": ia._tema_experiencia(r)}
    if pop._RE_LICENCIA.search(t):
        varias = pop._RE_LETRAS_LICENCIA.search(t)
        letra = pop._RE_LETRA_LICENCIA.search(t)
        letras = "".join(re.findall(r"[a-e]", re.sub(r"(?:o|y)", " ", varias.group(1)))) if varias else (
            (letra.group(1) or letra.group(2)) if letra else "")
        return {"dato": "licencia", "letra": letras}
    if pop._RE_DISPONIBILIDAD.search(t):
        return {"dato": "disponibilidad"}
    if pop.es_blanda(r) or _RE_RASGO.search(t):
        return {"dato": "blanda"}
    return {"dato": "requisito"}


def _min(x: str) -> str:
    x = x.strip().rstrip(".")
    return x[:1].lower() + x[1:] if len(x) > 1 and not x[:2].isupper() else x


def _anos_txt(n: float) -> str:
    n = int(n) if float(n) == int(n) else n
    return f"{n} año" if n == 1 else f"{n} años"


def pregunta_natural(req: str, info: Optional[dict] = None) -> str:
    """Pregunta en lenguaje natural para un indispensable (sin IA). Nunca «¿Cumples con…?» ni «¿Cuál es tu caso?»."""
    from . import ia

    info = info or tipo_requisito(req)
    d = info["dato"]
    if d == "escolaridad":
        return ia.PREGUNTA_ESCOLARIDAD
    if d == "edad":
        return "¿Cuántos años tienes?"
    if d == "traslado":
        return "¿Cuánto tiempo harías de tu casa al trabajo?"
    if d == "transportes":
        return "¿Cuántos transportes tomarías para llegar al trabajo?"
    if d == "experiencia":
        tema = info.get("tema") or "un puesto similar"
        if tema == "un puesto similar":
            return "¿Cuántos años de experiencia tienes en un puesto similar?"
        return f"¿Cuántos años de experiencia tienes {ia._de_experiencia(tema).removeprefix('de experiencia ')}?"
    if d == "licencia":
        return f"¿Tienes {_min(req)}?" if _n(req).startswith("licencia") else "¿Tienes licencia de manejo vigente?"
    if d == "blanda":
        return f"¿Me cuentas un ejemplo de tu trabajo donde se note {_min(req)}?"
    q = ia.pregunta_si_no_natural(req)
    if tiene_frase_prohibida(q):
        q = f"¿Tienes {_min(req)}?"
    return q


def regla_de(req: str, info: dict, descarta: bool) -> str:
    """El texto de la regla de descarte (lo que ve RH junto a la pregunta)."""
    from .prefiltro_conversacional import NOMBRE_NIVEL

    if not descarta:
        return REGLA_NO_DESCARTA
    d = info["dato"]
    if d == "escolaridad":
        # Cambio 48: regla ORDINAL — «Descarta si menor que Secundaria» (la tabla muestra «Menor que Secundaria»)
        nombre = NOMBRE_NIVEL.get(info.get("nivel"), "") if info.get("nivel") else ""
        return f"Descarta si menor que {nombre}" if nombre else f"Descarta si no cumple: {_min(req)}"
    if d == "experiencia":
        n = float(info.get("minimo") or 0)
        return "Descarta si no tiene experiencia" + (f" o tiene menos de {_anos_txt(n)}" if n > 0 else "")
    if d == "transportes":
        return f"Descarta si toma más de {int(info['maximo'])} transporte{'s' if int(info['maximo']) != 1 else ''}"
    if d == "edad":
        lo, hi = info.get("edad_min"), info.get("edad_max")
        partes = ([f"menos de {lo}"] if lo else []) + ([f"más de {hi}"] if hi else [])
        return f"Descarta si tiene {' o '.join(partes)} años" if partes else "Descarta si no cumple la edad"
    if d == "traslado":
        return f"Descarta si hace más de {int(info['limite_min'])} minutos"
    return "Descarta si responde que no"


_RE_INTERROGATIVA = re.compile(r"^\s*(?:cu[eé]ntame,?\s*)?¿\s*(qu[eé]|cu[aá]l|cu[aá]nt|c[oó]mo|d[oó]nde|cu[aá]ndo|por qu[eé]|qui[eé]n)",
                               re.IGNORECASE)


def _compatible(pregunta: str, tipo: str, dato: str) -> bool:
    """La redacción corresponde al tipo: un número pide una cantidad («¿Cuántos…?»); un Sí/No no abre con «¿Qué…?»
    (salvo la licencia, cuya respuesta se lee por la letra)."""
    from .entrevista_operativa import une_dos_ideas

    if une_dos_ideas(pregunta):
        return False  # una idea por pregunta (Cambio 19)
    if tipo == "numero":
        return bool(re.search(r"cu[aá]nt", pregunta, re.IGNORECASE))
    if tipo == "si_no" and dato != "licencia":
        return not _RE_INTERROGATIVA.match(pregunta)
    return True


def _item_indispensable(i: int, req: str, *, sin_experiencia: bool, natural: str = "", previa: Optional[dict] = None,
                        propuesto: bool = False) -> dict:
    from . import ia

    info = tipo_requisito(req)
    d = info["dato"]
    natural = ia.sin_relleno(natural)  # Cambio 48: nunca «Cuéntame,» en una pregunta filtro cerrada
    if previa and previa.get("pregunta"):
        previa = {**previa, "pregunta": ia.sin_relleno(previa["pregunta"])}
    tipo = {"escolaridad": "opcion", "experiencia": "numero", "transportes": "numero", "edad": "numero", "traslado": "numero",
            "blanda": "abierta"}.get(d, "si_no")
    pregunta = (natural if (natural and _pregunta_valida(natural) and d not in ("escolaridad", "edad", "traslado")
                            and _compatible(natural, tipo, d)) else pregunta_natural(req, info))
    descarta = tipo != "abierta" and not (sin_experiencia and d == "experiencia")
    if previa:  # lo que RH ajustó (texto y si descarta) se conserva mientras el requisito siga
        if previa.get("tipo") in TIPOS:
            tipo = previa["tipo"] if d not in ("escolaridad", "edad", "traslado", "transportes") else tipo
        if _pregunta_valida(previa.get("pregunta") or "") and d not in ("escolaridad",):
            pregunta = _texto(previa["pregunta"])
        if "descarta" in previa:
            descarta = bool(previa["descarta"]) and tipo != "abierta" and not (sin_experiencia and d == "experiencia")
    item = {"id": f"ind{i + 1}", "requisito": req.strip(), "pregunta": pregunta, "tipo": tipo, "dato": d,
            "descarta": descarta, "regla": regla_de(req, info, descarta), "base": d in ("edad", "traslado"),
            "indispensable": True, "opciones": list(NIVELES) if tipo == "opcion" and d == "escolaridad" else []}
    for k in ("nivel", "minimo", "maximo", "letra", "edad_min", "edad_max", "limite_min"):
        if info.get(k) not in (None, ""):
            item[k] = info[k]
    if tipo == "numero":
        item["unidad"] = {"experiencia": "años", "transportes": "transportes", "edad": "años", "traslado": "minutos"}.get(d, "")
    if propuesto:
        item["propuesto"] = True
    return item


def _texto_sueldo(sueldo: str) -> str:
    from . import ia

    q = ia.pregunta_sueldo(sueldo or "")
    return q.replace("¿Te parece bien?", "¿Te interesa?") if q else ""


def es_profesional(enfoque: str) -> bool:
    """Cambio 43: enfoques Profesional y Profesional + personal (el resto — Operativo o sin dato — conserva las base de siempre)."""
    return (enfoque or "") in ("profesional", "profesional_personal")


def pregunta_expectativa(periodicidad: str = "") -> str:
    """Cambio 43: el sueldo en enfoques profesionales se pregunta como EXPECTATIVA."""
    p = str(periodicidad or "").strip().lower()
    return "¿Cuál es tu expectativa de sueldo " + {"semanal": "semanal", "quincenal": "quincenal", "anual": "anual"}.get(p, "mensual") + "?"


def bases(titulo: str, sueldo: str, ubicacion: str, modalidad: str = "", ya: Optional[set] = None, enfoque: str = "",
          periodicidad: str = "") -> List[dict]:
    """Las preguntas base, en orden: edad, interés, sueldo (solo con sueldo capturado), colonia y traslado. No descartan.
    Cambio 43 — enfoque Operativo: colonia, traslado y sueldo AFIRMATIVO («El sueldo es… ¿Te interesa?»). Enfoques
    Profesional (+ personal): SIN colonia, traslado solo si un indispensable lo pide (esa pregunta ya es la del requisito) y
    el sueldo como EXPECTATIVA («¿Cuál es tu expectativa de sueldo mensual?»)."""
    ya = ya or set()
    if es_profesional(enfoque):
        titulo = (titulo or "").strip() or "esta vacante"
        salida = []
        if "edad" not in ya:
            salida.append({"id": "base-edad", "requisito": "Edad", "pregunta": "¿Cuántos años tienes?", "tipo": "numero",
                           "unidad": "años", "dato": "edad"})
        salida.append({"id": "base-interes", "requisito": f"Interés en el puesto: {titulo}",
                       "pregunta": f"El puesto es {titulo}. ¿Te interesa?", "tipo": "si_no", "dato": "interes"})
        salida.append({"id": "base-sueldo", "requisito": "Expectativa de sueldo", "pregunta": pregunta_expectativa(periodicidad),
                       "tipo": "numero", "unidad": "pesos", "dato": "sueldo", "expectativa": True})
        return [{**q, "base": True, "descarta": False, "regla": REGLA_NO_DESCARTA, "opciones": []} for q in salida]
    titulo = (titulo or "").strip() or "esta vacante"
    salida = []
    if "edad" not in ya:
        salida.append({"id": "base-edad", "requisito": "Edad", "pregunta": "¿Cuántos años tienes?", "tipo": "numero", "unidad": "años",
                       "dato": "edad"})
    salida.append({"id": "base-interes", "requisito": f"Interés en el puesto: {titulo}", "pregunta": f"El puesto es {titulo}. ¿Te interesa?",
                   "tipo": "si_no", "dato": "interes"})
    q_sueldo = _texto_sueldo(sueldo)
    if q_sueldo:
        salida.append({"id": "base-sueldo", "requisito": f"Sueldo: {sueldo.strip()}", "pregunta": q_sueldo, "tipo": "si_no", "dato": "sueldo"})
    salida.append({"id": "base-zona", "requisito": "Colonia y municipio", "pregunta": "¿En qué colonia y municipio vives?", "tipo": "abierta",
                   "dato": "zona"})
    remoto = (modalidad or "").lower().startswith("remot")
    if "traslado" not in ya and not remoto:
        salida.append({"id": "base-traslado", "requisito": "Tiempo de traslado", "pregunta": "¿Cuánto tiempo harías de tu casa al trabajo?",
                       "tipo": "numero", "unidad": "minutos", "dato": "traslado"})
    return [{**q, "base": True, "descarta": False, "regla": REGLA_NO_DESCARTA, "opciones": []} for q in salida]


def armar(indispensables: List[str], *, titulo: str = "", sueldo: str = "", ubicacion: str = "", modalidad: str = "",
          seniority: str = "", naturales: Optional[Dict[str, str]] = None, previas: Optional[List[dict]] = None,
          propuestos: Optional[List[str]] = None, enfoque: str = "", periodicidad: str = "") -> List[dict]:
    """La lista completa: una pregunta por indispensable (en su orden) y después las base. `naturales` = {requisito
    normalizado: pregunta propuesta por la IA}; `previas` = la lista guardada (se conservan los ajustes de RH)."""
    naturales = naturales or {}
    por_req = {_norm_req(q.get("requisito") or ""): q for q in previas or [] if isinstance(q, dict) and q.get("indispensable")}
    prop = {_norm_req(x) for x in propuestos or []}
    sin_exp = (seniority or "").strip().lower() == "sin experiencia"
    salida, ya = [], set()
    reqs = [r for r in indispensables or [] if str(r or "").strip()]
    for i, req in enumerate(reqs):
        it = _item_indispensable(i, req, sin_experiencia=sin_exp, natural=naturales.get(_norm_req(req), ""),
                                 previa=por_req.get(_norm_req(req)), propuesto=_norm_req(req) in prop)
        if it["dato"] in ("edad", "traslado"):
            ya.add(it["dato"])
        salida.append(it)
    salida += bases(titulo, sueldo, ubicacion, modalidad, ya, enfoque=enfoque, periodicidad=periodicidad)
    return [normalizar_item(q) for q in salida]


def normalizar_item(q: dict) -> dict:
    """Garantía en código: una abierta nunca descarta; la regla coincide con `descarta`; tipo válido."""
    q = dict(q)
    q["tipo"] = q.get("tipo") if q.get("tipo") in TIPOS else "si_no"
    q["pregunta"] = _texto(q.get("pregunta"))
    if q["tipo"] != "abierta":
        from . import ia

        q["pregunta"] = ia.sin_relleno(q["pregunta"])  # Cambio 48: cerradas, de número u opción sin «Cuéntame,»
    if q["tipo"] == "abierta":
        q["descarta"] = False
    q["descarta"] = bool(q.get("descarta"))
    if not q["descarta"]:
        q["regla"] = REGLA_NO_DESCARTA
    elif not q.get("regla") or q["regla"] == REGLA_NO_DESCARTA or q.get("dato") == "escolaridad":
        # Cambio 48: la escolaridad siempre con su regla ordinal (también las guardadas como «no terminó la …»)
        q["regla"] = regla_de(q.get("requisito") or "", tipo_requisito(q.get("requisito") or ""), True)
    q["base"] = bool(q.get("base"))
    q["etiqueta"] = ETIQUETA_BASE if q["base"] else ("Propuesto por Red Human" if q.get("propuesto") else "Requisito")
    if q["tipo"] == "si_no":
        q["opciones"] = ["Sí", "No"]
    return q


def normalizar_lista(entrada, indispensables: List[str], **kw) -> List[dict]:
    """Lo que manda el formulario (RH edita texto, tipo y si descarta) contra los indispensables VIGENTES: siempre una
    pregunta por indispensable y las base. Nunca acepta frases de formulario prohibidas (se reescriben)."""
    previas = [q for q in entrada or [] if isinstance(q, dict)]
    return armar(indispensables, previas=previas, **kw)


# ============================================================ vacante


def de_vacante(v) -> List[dict]:
    """La lista guardada en la vacante (vacía = vacante anterior al Cambio 37 sin migrar)."""
    return [q for q in (((getattr(v, "guiones", None) or {}).get(CLAVE)) or []) if isinstance(q, dict) and q.get("pregunta")]


def propuestos_de(v) -> List[str]:
    return [str(x) for x in (((getattr(v, "guiones", None) or {}).get(CLAVE_PROPUESTOS)) or []) if str(x or "").strip()]


def kw_de_vacante(v) -> dict:
    from .prefiltro_conversacional import ubicacion_vacante

    return {"titulo": getattr(v, "titulo", "") or "", "sueldo": "" if (getattr(v, "sueldo", "") or "") == "A convenir" else (v.sueldo or ""),
            "ubicacion": ubicacion_vacante(v), "modalidad": getattr(v, "modalidad", "") or "", "seniority": getattr(v, "seniority", "") or "",
            "enfoque": getattr(v, "enfoque_entrevista", "") or "", "periodicidad": getattr(v, "sueldo_periodicidad", "") or ""}


def web(lista: List[dict]) -> List[dict]:
    """Formulario web (columna `Vacante.preguntas_filtro`): las preguntas cerradas o numéricas de los indispensables y,
    si no llegan a `MIN_WEB`, las base que hagan falta (interés, sueldo, edad, traslado). Formato que lee `/aplicar`."""
    indisp = [q for q in lista if q.get("indispensable") and q.get("tipo") in TIPOS_WEB]
    salida = list(indisp)
    if len(salida) < MIN_WEB:
        orden = ("interes", "sueldo", "edad", "traslado")
        extra = sorted([q for q in lista if q.get("base") and not q.get("indispensable") and q.get("tipo") in TIPOS_WEB],
                       key=lambda q: orden.index(q["dato"]) if q.get("dato") in orden else 9)
        salida += extra[:MIN_WEB - len(salida)]
    out = []
    for q in salida:
        out.append({"id": q["id"], "pregunta": q["pregunta"], "tipo": q["tipo"], "valida": q.get("requisito") or q["pregunta"],
                    "respuesta_esperada": "Sí" if q["tipo"] == "si_no" else "", "descarta": bool(q.get("descarta")),
                    "opciones": list(q.get("opciones") or (["Sí", "No"] if q["tipo"] == "si_no" else [])),
                    "unidad": q.get("unidad", ""), "base": bool(q.get("base")), "unificado": True,
                    **({"minimo": q["minimo"]} if q.get("minimo") not in (None, "") else {})})
    return out


def guardar(v, lista: List[dict], propuestos: Optional[List[str]] = None) -> List[dict]:
    """Escribe la entidad en la vacante y DERIVA lo que leen los demás: el formulario web (`preguntas_filtro`), la pregunta
    natural de cada indispensable (`guiones.preguntas_requisitos`) y vacía el prefiltro de WhatsApp separado."""
    from sqlalchemy.orm.attributes import flag_modified

    g = dict(getattr(v, "guiones", None) or {})
    g[CLAVE] = lista
    if propuestos is not None:
        g[CLAVE_PROPUESTOS] = [p for p in propuestos if str(p or "").strip()]
    g["preguntas_requisitos"] = [{"requisito": q["requisito"], "pregunta": q["pregunta"]} for q in lista if q.get("indispensable")]
    v.guiones = g
    v.preguntas_filtro = web(lista)
    v.preguntas_filtro_whatsapp = []
    for campo in ("guiones", "preguntas_filtro", "preguntas_filtro_whatsapp"):
        try:
            flag_modified(v, campo)
        except Exception:  # noqa: BLE001 — objeto aún sin sesión
            pass
    return lista


def sincronizar(v, entrada=None, propuestos: Optional[List[str]] = None) -> List[dict]:
    """Recalcula la entidad contra los indispensables vigentes de la vacante (conservando lo que RH ajustó) y la guarda."""
    from ..routers.vacantes import requisitos_lista

    previas = list(entrada) if entrada is not None else de_vacante(v)
    prop = propuestos if propuestos is not None else propuestos_de(v)
    lista = armar(requisitos_lista(getattr(v, "requisitos", "") or ""), previas=previas, propuestos=prop,
                  naturales={_norm_req(x.get("requisito") or ""): x.get("pregunta") or ""
                             for x in ((getattr(v, "guiones", None) or {}).get("preguntas_requisitos") or []) if isinstance(x, dict)},
                  **kw_de_vacante(v))
    return guardar(v, lista, prop)


MARCA_MIGRACION = "preguntas_filtro_unificadas_2026_10_14"


def migrar(db, aplicar: bool = True) -> dict:
    """UNA vez (marca en bitácora): toda vacante no eliminada recibe su entidad «Preguntas filtro» a partir de sus
    indispensables y de la pregunta natural que ya tenía; el formulario web se deriva. No toca postulaciones (cada una ya
    lleva sus respuestas). Idempotente y no fatal. No hace commit."""
    from ..models import Bitacora, Vacante, registrar

    try:
        if aplicar and db.query(Bitacora.id).filter(Bitacora.accion == MARCA_MIGRACION).first():
            return {"vacantes": 0, "yaAplicada": True}
        vacantes = db.query(Vacante).filter(Vacante.estado != "Eliminada").all()
    except Exception:  # noqa: BLE001
        return {"vacantes": 0}
    cambiadas = []
    for v in vacantes:
        if de_vacante(v):
            continue
        cambiadas.append(v.codigo)
        if aplicar:
            sincronizar(v, entrada=[])
    if aplicar:
        registrar(db, "sistema", MARCA_MIGRACION, "vacante", "", {"vacantes": cambiadas})
        db.flush()
    return {"vacantes": len(cambiadas), "codigos": cambiadas}


# ============================================================ criterios del bot


def aplica(v) -> bool:
    """La vacante usa la entidad única. Si alguien volvió a escribir las listas viejas por separado (un cliente anterior
    de la API o un ajuste directo) — prefiltro de WhatsApp propio o un formulario web distinto del derivado — la vacante
    sigue con el prefiltro anterior (misma regla que `PATCH /vacantes` con `preguntas_filtro` sin `filtro`)."""
    lista = de_vacante(v)
    if not lista:
        return False
    if any(isinstance(x, dict) for x in (getattr(v, "preguntas_filtro_whatsapp", None) or [])):
        return False
    actual = [_texto(q.get("pregunta")) for q in (getattr(v, "preguntas_filtro", None) or []) if isinstance(q, dict)]
    return actual == [q["pregunta"] for q in web(lista)]


def criterios(v, corporativa: bool = False) -> List[dict]:
    """Criterios del prefiltro conversacional desde la entidad única, en su orden (indispensables primero, después las
    base). Ruta corporativa (Cambios 24 y 31): sin colonia, el sueldo es la EXPECTATIVA y el traslado solo si es requisito."""
    from .prefiltro_conversacional import dato_expectativa

    salida = []
    for q in de_vacante(v):
        q = normalizar_item(q)
        if q.get("expectativa") and q.get("base") and not q.get("indispensable"):
            # Cambio 43: enfoques profesionales — el sueldo es la EXPECTATIVA (con la pregunta guardada)
            salida.append({**dato_expectativa(v), "pregunta": q["pregunta"], "chat": q["pregunta"], "unificado": True, "base": True,
                           "tipo": "numero"})
            continue
        if corporativa and q.get("base") and not q.get("indispensable"):
            if q["dato"] == "zona" or q["dato"] == "traslado":
                continue
            if q["dato"] == "sueldo":
                salida.append({**dato_expectativa(v), "unificado": True, "base": True, "tipo": "numero"})
                continue
        c = {"id": q["id"], "clave": q.get("dato") or q["id"], "dato": q.get("dato") or "requisito",
             "criterio": q.get("requisito") or q["pregunta"], "pregunta": q["pregunta"], "chat": q["pregunta"],
             "indispensable": bool(q.get("descarta")), "esperada": "si", "tipo": q["tipo"], "unificado": True,
             "base": bool(q.get("base")), "regla": q.get("regla") or ""}
        for k in ("nivel", "minimo", "maximo", "letra", "edad_min", "edad_max", "limite_min", "unidad", "opciones"):
            if q.get(k) not in (None, "", []):
                c[k] = q[k]
        if q.get("dato") == "escolaridad" and q.get("nivel") is not None:
            c["nivel_requerido"] = q["nivel"]
        salida.append(c)
    if corporativa and not any(c.get("dato") == "sueldo" for c in salida) and (getattr(v, "sueldo", "") or ""):
        salida.append({**dato_expectativa(v), "unificado": True, "base": True, "tipo": "numero"})
    return salida


# ============================================================ clasificación de respuestas

_RE_NEGATIVA = re.compile(r"^(no|nop|nel|negativo|nunca|ninguna?|nada|para nada|tampoco|aun no|todavia no|no tengo|no cuento|"
                          r"no puedo|sin)\b")
_RE_HEDGE = re.compile(r"\b(casi|mas o menos|aproximadamente|algo de|poco|poquito|un poco|algunos?|algunas?|creo|tal vez|quiza\w*|"
                       r"depende|no se|no estoy segur\w*|a veces|parcial\w*)\b")
_RE_EVASIVA = re.compile(r"^(no se|nose|depende|luego te digo|despues te digo|ahorita no se|lo que sea|como sea|cualquiera|"
                         r"\?+|ok|okay|mmm+|eh|ah)$")
_NUM_PALABRA = {"cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
                "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "quince": 15, "veinte": 20, "medio": 0.5}
_RE_SOLO_NUMERO = re.compile(r"^(?:como |unos |unas |mas o menos |aprox\w* |son |llevo |tengo |los mismos |las mismas )?"
                             r"(\d+(?:[.,]\d+)?|" + "|".join(_NUM_PALABRA) + r")$")
_RE_VEHICULO = re.compile(r"\b(caminando|a pie|camino|caminar|bici\w*|carro|auto|coche|moto\w*|camioneta|vehiculo|uber|didi|taxi)\b")
_RE_PUBLICO = re.compile(r"\b(camion\w*|micro\w*|combi\w*|pesero\w*|metro\w*|tren\w*|ruta\w*|colectivo\w*|autobus\w*|transbord\w*)\b")


def _limpio(texto: str) -> str:
    from .prefiltro_conversacional import limpiar

    return limpiar(texto)


def numero_sin_unidad(texto: str) -> Optional[str]:
    """«uno», «8», «los mismos 10» (sin «años»/«meses») → el número tal como lo escribió; None si trae unidad o no es número."""
    t = _limpio(texto)
    m = _RE_SOLO_NUMERO.fullmatch(t)
    return m.group(1) if m else None


def pregunta_unidad(n_txt: str) -> str:
    """«¿Un año o un mes?» (uno) / «¿8 años o 8 meses?»."""
    if _n(n_txt) in ("1", "uno", "un", "una"):
        return "¿Un año o un mes?"
    return f"¿{n_txt} años o {n_txt} meses?"


def anos_de(texto: str) -> Optional[float]:
    from .prefiltro_conversacional import _anos

    t = _limpio(texto)
    for palabra, n in sorted(_NUM_PALABRA.items(), key=lambda x: -len(x[0])):
        t = re.sub(rf"\b{palabra}\b", str(n), t)
    return _anos(t)


def transportes_de(texto: str) -> Optional[int]:
    """Cambio 37: «en carro», «moto», «caminando» = 0 transportes; «uno» = 1; «dos camiones» = 2."""
    t = _n(_limpio(texto))
    m = re.search(r"\b(\d+)\b", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(" + "|".join(k for k in _NUM_PALABRA if k != "medio") + r")\b", t)
    if m and not (_RE_VEHICULO.search(t) and m.group(1) in ("un", "una") and not _RE_PUBLICO.search(t)):
        return int(_NUM_PALABRA[m.group(1)])
    if _RE_VEHICULO.search(t) and not _RE_PUBLICO.search(t):
        return 0
    return None


def es_evasiva(texto: str) -> bool:
    t = _limpio(texto)
    return not t or bool(_RE_EVASIVA.fullmatch(t))


def clasificar(c: dict, texto: str) -> Tuple[Optional[str], dict]:
    """(valor, extra). valor «si» = cumple / se registra; «no» = respuesta CLARA que no cumple (descarta si la pregunta
    descarta); None = afirmativa sin dato, parcial, ambigua o evasiva → UNA repregunta y después «Requiere revisión».
    extra["unidad"] = número sin unidad en una pregunta de tiempo («¿Un año o un mes?»)."""
    from . import prefiltro_conversacional as pc
    from . import prefiltro_operativo as pop

    t = _limpio(texto)
    if not t:
        return None, {}
    dato = c.get("dato") or "requisito"
    negativa = bool(_RE_NEGATIVA.match(t)) and not pc._RE_SI_SIN_PROBLEMA.search(t)
    hedge = bool(_RE_HEDGE.search(t))
    if dato == "experiencia":
        if negativa or re.search(r"\b(sin experiencia|nunca he|no he trabajado)\b", t):
            return "no", {"anos": 0}
        n_txt = numero_sin_unidad(texto)
        if n_txt is not None:
            return None, {"unidad": n_txt}
        n = anos_de(texto)
        if n is None:
            return None, {}
        minimo = float(c.get("minimo") or 0)
        if re.search(r"\bcasi\b", t) and n <= minimo:
            return None, {"anos": round(n, 2), "parcial": True}  # «casi 3 años» con 3 pedidos: parcial, se confirma
        if n >= minimo and (n > 0 or not hedge):
            return "si", {"anos": round(n, 2)}
        if hedge:
            return None, {"anos": round(n, 2), "parcial": True}
        return "no", {"anos": round(n, 2)}
    if dato == "escolaridad":
        if re.search(r"\b(no (la |el )?(termine|acabe|tengo)|no estudie|sin estudios)\b", t):
            return "no", {"nivel": "", "terminado": False}
        nivel, terminado = pc.nivel_estudios(texto)
        if nivel is None:
            return None, {}
        extra = {"nivel": pc.NOMBRE_NIVEL.get(nivel, ""), "terminado": terminado}
        return pc.cumple_nivel(c.get("nivel_requerido"), texto) if c.get("nivel_requerido") is not None else "si", extra
    if dato == "transportes":
        n = transportes_de(texto)
        if n is None:
            return None, {}
        tope = c.get("maximo")
        return ("si" if tope is None or n <= float(tope) else "no"), {"transportes": n}
    if dato == "edad":
        n = pop._edad(t)
        if n is None:
            return None, {}
        lo, hi = c.get("edad_min"), c.get("edad_max")
        ok = (lo is None or n >= lo) and (hi is None or n <= hi)
        return ("si" if ok or not c.get("indispensable") else "no"), {"edad": n}
    if dato == "traslado":
        if re.fullmatch(r"(poco|muy poco|no mucho|un rato|rapido|cerca|lejos|mucho|depende|no se|normal|bastante)", t):
            return None, {}
        mins = pc.minutos_de(texto)
        if mins is None:
            return None, {}
        lim = c.get("limite_min")
        return ("si" if lim is None or mins <= int(lim) else "no"), {"minutos": mins}
    if dato == "zona":
        if pc._RE_ZONA_VAGA.match(t) or len(t) < 3 or es_evasiva(texto):
            return None, {}
        return "si", {"zona": texto.strip()[:120]}
    if dato == "sueldo" and c.get("expectativa"):
        monto = pc.monto_de(texto)
        return (None, {}) if monto is None else ("si", {"monto": monto})
    if c.get("tipo") == "abierta":
        return (None, {}) if es_evasiva(texto) or len(t) < 2 else ("si", {"respuesta": texto.strip()[:300]})
    # Sí / No (licencia, disponibilidad, requisito, interés, sueldo, registro)
    if dato == "licencia":
        if negativa:
            return "no", {}
        letra = re.search(r"\b(?:tipo\s*)?([a-e])\b", t)
        v = pc.clasificar({"criterio": c.get("criterio", ""), "pregunta": c.get("pregunta", ""), "chat": c.get("chat", "")}, texto)
        if letra is None and v != "si":
            return None, {}
        extra = {"tipo": letra.group(1).upper() if letra else ""}
        if c.get("letra") and letra and letra.group(1) not in c["letra"]:
            extra["otra_letra"] = True  # otra letra: no se descarta, va a Puntos por validar
        return "si", extra
    if hedge and not negativa:
        return None, {"parcial": True}
    v = pc.clasificar({"criterio": c.get("criterio", ""), "pregunta": c.get("pregunta", ""), "chat": c.get("chat", "")}, texto)
    if v == "no" and not negativa:
        v = None  # un «no» que no es explícito (desempate de la IA) nunca descarta: se confirma
    return v, ({"acepta": v == "si"} if v is not None else {})


def repregunta(c: dict) -> str:
    """UNA repregunta concreta. «Claro. Solo para tenerlo en cuenta…» SOLO en sueldo."""
    if c.get("dato") == "sueldo":
        return c.get("repregunta") if c.get("expectativa") and c.get("repregunta") else REPREGUNTAS["sueldo"]
    if c.get("dato") in REPREGUNTAS:
        return REPREGUNTAS[c["dato"]]
    q = (c.get("chat") or c.get("pregunta") or "").strip()
    return f"¿Me confirmas con un sí o un no? {q}" if c.get("tipo") == "si_no" else q


def registrar_datos(p, c: dict, texto: str, extra: dict) -> None:
    """`analisis.prefiltro_datos` (lo que muestra la ficha) y Puntos por validar de lo que se registra (nunca descarta)."""
    from sqlalchemy.orm.attributes import flag_modified

    from . import prefiltro_conversacional as pc

    a = dict(p.analisis or {})
    d = dict(a.get("prefiltro_datos") or {})
    puntos = list(a.get("puntos_validar_prefiltro") or [])
    dato = c.get("dato")
    if dato == "zona":
        d["zona"] = texto.strip()[:200]
    elif dato == "traslado" and extra.get("minutos") is not None:
        d["traslado"] = texto.strip()[:200]
        d["traslado_min"] = extra["minutos"]
    elif dato == "edad" and extra.get("edad") is not None:
        d["edad"] = extra["edad"]
        if extra["edad"] < 18 and not c.get("indispensable"):
            puntos.append(f"Menor de 18 años: {extra['edad']}")
    elif dato == "interes":
        d["interes_puesto"] = extra.get("acepta")
        if extra.get("acepta") is False:
            puntos.append("No le interesa el puesto")
    elif dato == "sueldo" and c.get("expectativa"):
        d["sueldo"] = texto.strip()[:200]
        if extra.get("flexible"):
            d["sueldo"], d["sueldo_expectativa"] = "Flexible", None
        else:
            d["sueldo_expectativa"] = extra.get("monto")
            d["sueldo_ofrecido"] = str(c.get("criterio") or "").removeprefix("Sueldo:").strip()
            tope = c.get("tope")
            if extra.get("monto") and tope and extra["monto"] > tope * (1 + pc.MARGEN_EXPECTATIVA):
                d["sueldo_sobre_tope"] = True
                puntos.append(f"Expectativa de sueldo: ${extra['monto']:,} (más de 20 % arriba del sueldo de la vacante: "
                              f"{d['sueldo_ofrecido'] or f'${tope:,}'})")
    elif dato == "sueldo":
        d["sueldo"] = texto.strip()[:200]
        d["sueldo_acepta"] = extra.get("acepta")
        d["sueldo_ofrecido"] = str(c.get("criterio") or "").removeprefix("Sueldo: ").strip()
    elif dato == "transportes" and extra.get("transportes") is not None:
        d["transportes"] = extra["transportes"]
    elif dato == "escolaridad":
        d["escolaridad"] = texto.strip()[:200]
        d["nivel"] = extra.get("nivel") or ""
        d["terminado"] = extra.get("terminado")
    elif dato == "experiencia" and extra.get("anos") is not None:
        d["experiencia"] = {**(d.get("experiencia") if isinstance(d.get("experiencia"), dict) else {}), c["criterio"]: extra["anos"]}
    elif dato == "licencia" and extra.get("otra_letra"):
        puntos.append(f"Licencia tipo {extra.get('tipo')}; la vacante pide: {c['criterio']}")
    d["en"] = pc._ahora()
    a["prefiltro_datos"] = d
    a["puntos_validar_prefiltro"] = puntos
    p.analisis = a
    flag_modified(p, "analisis")


def valor_web(c: dict, respuesta: str) -> Tuple[Optional[str], dict]:
    """Respuesta del formulario web a una pregunta de la entidad (Sí/No, número u opción)."""
    r = (respuesta or "").strip()
    if not r:
        return None, {}
    if c.get("tipo") == "numero" and re.fullmatch(r"\d+(?:[.,]\d+)?", r):
        unidad = c.get("unidad") or ""
        if c.get("dato") == "experiencia":
            r = f"{r} años"
        elif c.get("dato") == "traslado":
            r = f"{r} minutos"
        elif unidad and c.get("dato") not in ("edad", "transportes"):
            r = f"{r} {unidad}"
    return clasificar(c, r)
