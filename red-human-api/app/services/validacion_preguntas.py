"""Validación de las preguntas generadas por sección (especificación «Ficha de Nueva vacante», 2026-10-17).

* Cada indispensable se CLASIFICA por tipo (`TIPOS_INDISPENSABLE`): Experiencia, Ubicación, Fecha de inicio, Horario,
  Escolaridad, Licencia, Herramienta, Idioma u Otro. El generador propone el tipo; si no es válido, manda `clasificar`.
* Cada tipo tiene una pregunta de RESPALDO fija por sección (`respaldo`): la web (Sí/No) y el WhatsApp (continuación que
  asume que cumple y pide un detalle). Siempre nombran el concepto del indispensable.
* Las preguntas de una sección se generan en batch. Antes de guardar se normalizan y comparan (`problemas`): dos iguales o
  traslapadas, o una que no menciona el concepto de su indispensable, son un problema. `ia.asegurar_secciones` reintenta
  la sección completa hasta `MAX_REINTENTOS` veces y, si sigue fallando, inyecta el respaldo SOLO en las que fallan.
"""

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

TIPOS_INDISPENSABLE = ("Experiencia", "Ubicación", "Fecha de inicio", "Horario", "Escolaridad", "Licencia", "Herramienta",
                       "Idioma", "Otro")
MAX_REINTENTOS = 2
SECCIONES = ("web", "whatsapp")

_IDIOMAS = ("ingles", "frances", "aleman", "italiano", "portugues", "chino", "mandarin", "japones", "coreano", "ruso", "arabe")
_VACIAS = {"para", "como", "con", "del", "las", "los", "una", "uno", "unos", "unas", "que", "por", "sus", "este", "esta",
           "tener", "contar", "cuenta", "cuentas", "saber", "poder", "experiencia", "disponibilidad", "conocimiento",
           "conocimientos", "manejo", "minimo", "minima", "anos", "nivel", "dominio", "requisito", "indispensable", "deseable",
           "buen", "buena", "preferentemente", "todo", "toda", "mismo", "misma", "tienes", "puedes", "sabes"}
# palabras que también cuentan como «menciona el concepto» según el tipo
_SINONIMOS = {
    "Ubicación": ("vive", "vives", "colonia", "traslad", "llegar", "casa", "zona", "cerca"),
    "Fecha de inicio": ("empez", "inici", "incorpor", "comenz", "disponib"),
    "Horario": ("horario", "turno", "jornada", "hora"),
    "Escolaridad": ("estudi", "escuela", "grado", "carrera", "titul", "certificad"),
    "Licencia": ("licencia", "manej", "unidad"),
    "Idioma": _IDIOMAS + ("idioma",),
}


def _n(t: str) -> str:
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", t).split())


def _min(x: str) -> str:
    x = " ".join(str(x or "").split()).strip().rstrip(".")
    return x[:1].lower() + x[1:] if len(x) > 1 and not x[:2].isupper() else x


def clasificar(req: str) -> str:
    """Tipo del indispensable (determinista)."""
    from . import ia

    t = _n(req)
    if any(re.search(rf"\b{i}\b", t) for i in _IDIOMAS) or re.search(r"\b(idioma|bilingue)\b", t):
        return "Idioma"
    if ia.RE_NIVEL_ESTUDIOS.search(req or "") or re.search(r"\b(licenciatura|ingenieria|carrera|titulo|titulad\w*|estudios|bachillerato|"
                                                          r"preparatoria|secundaria|primaria|posgrado|maestria|cedula)\b", t):
        return "Escolaridad"
    if re.search(r"\blicencia\b", t):
        return "Licencia"
    if re.search(r"\b(inicio|iniciar|incorporacion|incorporarse|empezar|comenzar)\b", t):
        return "Fecha de inicio"
    if re.search(r"\b(horario\w*|turnos?|rolar|nocturn\w*|fines? de semana|lunes|sabados?|domingos?|jornada|tiempo completo|"
                 r"medio tiempo)\b", t):
        return "Horario"
    if re.search(r"\b(vivir|radicar|residir|zona|cerca de|ubicacion|traslad\w*|sede|colonia|municipio)\b", t):
        return "Ubicación"
    if "experiencia" in t or ia._RE_ANOS.search(req or ""):
        return "Experiencia"
    if re.search(r"\b(manejo de|uso de|conocimientos? (de|en)|excel|word|office|sap|erp|crm|software|sistema\w*|herramienta\w*|"
                 r"montacargas|maquinaria|equipo|programa\w*|autocad|photoshop|python|java|sql)\b", t):
        return "Herramienta"
    return "Otro"


def tipo_valido(tipo: str) -> Optional[str]:
    k = _n(tipo)
    return next((x for x in TIPOS_INDISPENSABLE if _n(x) == k), None)


def concepto(req: str) -> List[str]:
    """Palabras clave del indispensable (sin relleno): lo que su pregunta tiene que nombrar."""
    palabras = [w for w in _n(req).split() if len(w) >= 4 and w not in _VACIAS and not w.isdigit()]
    return palabras or [w for w in _n(req).split() if len(w) >= 3 and not w.isdigit()]


def menciona(pregunta: str, req: str, tipo: str = "") -> bool:
    """¿La pregunta nombra el concepto de su indispensable? (prefijo de 5 letras o un sinónimo del tipo)."""
    q = _n(pregunta)
    palabras = q.split()
    for w in concepto(req):
        raiz = w[:5] if len(w) > 5 else w
        if any(p.startswith(raiz) for p in palabras):
            return True
    return any(s in q for s in _SINONIMOS.get(tipo or clasificar(req), ()))


def traslapadas(a: str, b: str) -> bool:
    """Iguales normalizadas o casi las mismas palabras clave."""
    from . import ia

    return bool(_n(a)) and bool(_n(b)) and (_n(a) == _n(b) or ia.repite_pregunta(a, b))


def problemas(items: List[Tuple[str, str, str]]) -> Dict[int, str]:
    """{índice: motivo} sobre [(requisito, tipo, pregunta)] de UNA sección."""
    salida: Dict[int, str] = {}
    for i, (req, tipo, q) in enumerate(items):
        if not str(q or "").strip():
            salida[i] = "vacía"
            continue
        if not menciona(q, req, tipo):
            salida[i] = f"no menciona «{', '.join(concepto(req)[:3])}»"
            continue
        for j in range(i):
            if items[j][2] and traslapadas(q, items[j][2]):
                salida[i] = f"repite la pregunta de «{items[j][0]}»"
                break
    return salida


_INFINITIVOS = {"saber": "Sabes", "poder": "Puedes", "tener": "Tienes", "manejar": "Manejas", "hablar": "Hablas",
                "vivir": "Vives", "radicar": "Radicas", "residir": "Resides", "conocer": "Conoces", "dominar": "Dominas"}


def _si_no(req: str) -> str:
    """«Saber leer y escribir» → «¿Sabes leer y escribir?»; «Contar con auto propio» → «¿Cuentas con auto propio?»; lo demás
    «¿Tienes …?»."""
    r = " ".join(str(req or "").split()).strip(" .")
    k = _n(r).split()
    if not k:
        return ""
    if k[0] == "contar" and len(k) > 1 and k[1] == "con":
        return f"¿Cuentas con {_min(' '.join(r.split()[2:]))}?"
    if k[0] in _INFINITIVOS:
        return f"¿{_INFINITIVOS[k[0]]} {_min(' '.join(r.split()[1:]))}?"
    if re.fullmatch(r"[a-z]+(ar|er|ir)", k[0]) and k[0] not in ("hogar", "lugar", "mar", "poder"):
        return f"¿Puedes {_min(r)}?"  # «Cargar material pesado» → «¿Puedes cargar material pesado?»
    return f"¿Tienes {_min(r)}?"


def _lugar(req: str) -> str:
    r = re.sub(r"^(vivir|radicar|residir)\s+(en|cerca de)\s+|^(zona|cerca de|ubicacion en|ubicación en)\s+", "", " ".join(str(req).split()),
               flags=re.IGNORECASE).strip(" .")
    return r or req


def _herramienta(req: str) -> str:
    r = re.sub(r"^(manejo|uso|conocimientos?|dominio)\s+(de|en|del)\s+", "", " ".join(str(req).split()), flags=re.IGNORECASE).strip(" .")
    return _min(r or req)


def _idioma(req: str) -> str:
    t = _n(req)
    return next((i for i in _IDIOMAS if re.search(rf"\b{i}\b", t)), "idioma").replace("ingles", "inglés").replace(
        "frances", "francés").replace("aleman", "alemán").replace("portugues", "portugués").replace("japones", "japonés")


def respaldo(req: str, tipo: str, seccion: str) -> str:
    """La pregunta FIJA de respaldo del tipo para la sección (web = Sí/No; whatsapp = continuación)."""
    from . import ia

    tipo = tipo_valido(tipo) or clasificar(req)
    if seccion == "web":
        if tipo == "Ubicación":
            return f"¿Vives cerca de {_lugar(req)} o puedes trasladarte ahí?"
        if tipo == "Herramienta":
            return f"¿Sabes usar {_herramienta(req)}?"
        if tipo == "Idioma":
            return f"¿Hablas {_idioma(req)} al nivel que pide el puesto ({_min(req)})?"
        return _si_no(req)
    if tipo == "Experiencia":
        return f"¿En qué empresa ganaste tu experiencia en {ia._tema_experiencia(req)}?"
    if tipo == "Ubicación":
        return f"¿Cuánto tiempo harías de tu casa a {_lugar(req)}?"
    if tipo == "Fecha de inicio":
        return "¿Qué día podrías empezar a trabajar?"
    if tipo == "Horario":
        return "¿Qué horario tenías en tu último trabajo?"
    if tipo == "Escolaridad":
        return "¿En qué escuela terminaste tus estudios?"
    if tipo == "Licencia":
        return "¿Qué tipo de unidades has manejado con tu licencia?"
    if tipo == "Herramienta":
        return f"¿Para qué tareas usabas {_herramienta(req)} en tu último trabajo?"
    if tipo == "Idioma":
        return f"¿En qué situaciones de trabajo has usado el {_idioma(req)}?"
    return f"¿Me cuentas un ejemplo concreto de tu experiencia con {_min(req)}?"
