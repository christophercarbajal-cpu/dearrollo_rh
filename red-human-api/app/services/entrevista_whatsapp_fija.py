"""Cambio 29 (2026-10-12) — Entrevista Red Human por WhatsApp FIJA para rutas Masivos (operativos). Cero IA generativa.

Flujo (una pregunta por mensaje, siempre en este orden):
  Transición: «Listo, [nombre]. Ahora unas preguntas sobre tu experiencia.» + la primera pregunta.
  A. Situación actual: «¿Trabajas actualmente?» → sí: el primer trabajo del historial es el actual (en presente) ·
     no: «¿Cuánto tiempo llevas sin trabajar?».
  B. Historial laboral: hasta 3 trabajos — empresa, puesto, qué hacía, duración y motivo de salida. Un despido o una
     renuncia sin motivo se REPREGUNTA una vez («¿Por qué te despidieron?» / «¿Por qué renunciaste?»). Después de cada
     trabajo: «¿Tuviste otro trabajo antes de ese?».
  C. Preguntas propias que RH agregó a la vacante.
  D. Confirmación de puesto, sueldo, traslado e inicio → cierre EXACTO. Lo que contradiga al prefiltro va a Puntos por
     validar (nunca descarta).
La vacante guarda su ajuste en `Vacante.guiones["entrevista_whatsapp_base"] = {quitadas: [ids], propias: [textos]}`: RH
puede quitar preguntas base o agregar propias (van al bloque C); «Restaurar base» = vacío. La recomendación se calcula
sobre el historial laboral (`evaluar`). Un solo nombre de empresa en toda la conversación: `serial.nombre_empresa_candidato`.
"""

import copy
import re
import unicodedata
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

MAX_TRABAJOS = 3
MAX_PROPIAS = 10
CLAVE_CONFIG = "entrevista_whatsapp_base"

# Preguntas base (id, bloque, texto visible en la configuración). «empresa» no se puede quitar: ancla el historial.
BASE = [
    {"id": "trabaja", "bloque": "A", "texto": "¿Trabajas actualmente?"},
    {"id": "sin_trabajo", "bloque": "A", "texto": "¿Cuánto tiempo llevas sin trabajar?"},
    {"id": "empresa", "bloque": "B", "texto": "¿Cuál fue tu último trabajo? (empresa)", "fija": True},
    {"id": "puesto", "bloque": "B", "texto": "¿Qué puesto tenías?"},
    {"id": "tareas", "bloque": "B", "texto": "¿Qué hacías ahí?"},
    {"id": "duracion", "bloque": "B", "texto": "¿Cuánto tiempo trabajaste ahí?"},
    {"id": "motivo", "bloque": "B", "texto": "¿Por qué saliste? (despido o renuncia se repregunta)"},
    {"id": "otro", "bloque": "B", "texto": "¿Tuviste otro trabajo antes de ese? (hasta 3 trabajos)"},
    {"id": "conf_puesto", "bloque": "D", "texto": "Para confirmar, el puesto es [puesto] en [empresa]. ¿Te interesa?"},
    {"id": "conf_sueldo", "bloque": "D", "texto": "El sueldo es [sueldo]. ¿Estás de acuerdo?"},
    {"id": "conf_traslado", "bloque": "D", "texto": "El trabajo es en [ubicación]. ¿Puedes trasladarte?"},
    {"id": "inicio", "bloque": "D", "texto": "¿Cuándo podrías empezar?"},
]
IDS_BASE = [b["id"] for b in BASE]
QUITABLES = [b["id"] for b in BASE if not b.get("fija")]
BLOQUES = {"A": "Situación actual", "B": "Historial laboral", "C": "Preguntas de RH", "D": "Confirmación y cierre"}


def _n(t: str) -> str:
    return " ".join(unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower().split())


# ============================================================ configuración de la vacante

def normalizar_config(entrada) -> dict:
    entrada = entrada if isinstance(entrada, dict) else {}
    if entrada.get("restaurar"):
        return {"quitadas": [], "propias": []}
    quitadas = [str(x) for x in entrada.get("quitadas") or [] if str(x) in QUITABLES]
    propias = []
    for x in entrada.get("propias") or []:
        t = " ".join(str(x or "").split())[:300]
        if t and _n(t) not in {_n(y) for y in propias}:
            propias.append(t)
    return {"quitadas": list(dict.fromkeys(quitadas)), "propias": propias[:MAX_PROPIAS]}


def config_de(v) -> dict:
    return normalizar_config(((getattr(v, "guiones", None) or {}).get(CLAVE_CONFIG)) or {})


def vista(v) -> dict:
    cfg = config_de(v)
    return {"base": [{**b, "bloqueTexto": BLOQUES[b["bloque"]], "quitada": b["id"] in cfg["quitadas"]} for b in BASE],
            "quitadas": cfg["quitadas"], "propias": cfg["propias"], "esBase": not cfg["quitadas"] and not cfg["propias"]}


def guion_visible() -> dict:
    """La base como «Entrevista Red Human por WhatsApp — guion» de la vacante (lo que RH ve; la conversación la lleva
    `turno`). Masivos nunca genera este guion con IA."""
    return {"enfoque": "operativo", "temas": [BLOQUES[b["bloque"]] for b in BASE],
            "preguntas": [b["texto"] for b in BASE], "fija": True}


# ============================================================ ¿aplica?

def aplica(e) -> bool:
    """Entrevista por WhatsApp de una postulación con ruta Masivos."""
    from .prefiltro_operativo import es_ruta_operativa

    p = getattr(e, "postulacion", None)
    return getattr(e, "tipo", "") == "whatsapp" and p is not None and es_ruta_operativa(p.proceso or {})


# ============================================================ textos

def _datos(p) -> dict:
    from ..routers.candidatos import nombre_ficha
    from ..serial import nombre_empresa_candidato

    v = p.vacante
    nombre = nombre_ficha(p) or ""
    return {"nombre": "" if not nombre or nombre.startswith("Candidato") else nombre.split(" ")[0],
            "titulo": (v.titulo if v else "") or "el puesto", "empresa": (nombre_empresa_candidato(v) if v else "") or "la empresa",
            "sueldo": ((v.sueldo or "") if v else "").strip(), "ubicacion": ((v.ubicacion or "") if v else "").strip()}


def transicion(p, primera: str) -> str:
    d = _datos(p)
    return f"Listo{', ' + d['nombre'] if d['nombre'] else ''}. Ahora unas preguntas sobre tu experiencia. {primera}"


def _sigue_entrevista_humana(p) -> bool:
    pasos = (p.proceso or {}).get("pasos") or []
    i = next((n for n, x in enumerate(pasos) if x.get("tipo") == "entrevista_whatsapp"), -1)
    return any(x.get("tipo") == "entrevista_humana" for x in pasos[i + 1:])


def cierre(p) -> str:
    """Cierre EXACTO (2026-10-13, checklist Grupak): «Con esto terminamos, [nombre]. Gracias por tu tiempo. El equipo de
    Recursos Humanos de [empresa] te contactará para agendar una entrevista.» Nunca dirección ni fecha: la cita la
    programa RH después. Si la ruta no sigue con una entrevista humana, «…te contactará para continuar con tu proceso.»"""
    d = _datos(p)
    siguiente = "para agendar una entrevista" if _sigue_entrevista_humana(p) else "para continuar con tu proceso"
    return (f"Con esto terminamos{', ' + d['nombre'] if d['nombre'] else ''}. Gracias por tu tiempo. El equipo de Recursos "
            f"Humanos de {d['empresa']} te contactará {siguiente}.")


def _texto_sueldo(sueldo: str) -> str:
    t = " ".join(str(sueldo or "").split()).strip().rstrip(".")
    if not t or _n(t) in ("a convenir", "por definir"):
        return ""
    return t[0].lower() + t[1:] if t.lower().startswith("desde ") else f"de {t}"


def texto_pregunta(pid: str, est: dict, p) -> str:
    """Texto de la pregunta `pid` (en presente si el trabajo es el actual)."""
    d = _datos(p)
    k = int(est.get("k") or 0)
    actual = bool(est.get("actual")) and k == 0
    if pid.startswith("propia:"):
        return config_de(p.vacante)["propias"][int(pid.split(":")[1])]
    return {
        "trabaja": "¿Trabajas actualmente?",
        "sin_trabajo": "¿Cuánto tiempo llevas sin trabajar?",
        "empresa": ("¿En qué empresa trabajas?" if actual else "¿Cuál fue tu último trabajo?" if k == 0 else "¿En qué empresa fue?"),
        "puesto": "¿Qué puesto tienes?" if actual else "¿Qué puesto tenías?",
        "tareas": "¿Qué haces ahí?" if actual else "¿Qué hacías ahí?",
        "duracion": "¿Cuánto tiempo llevas ahí?" if actual else "¿Cuánto tiempo trabajaste ahí?",
        "motivo": "¿Por qué quieres cambiar de trabajo?" if actual else "¿Por qué saliste?",
        "otro": "¿Tuviste otro trabajo antes de ese?",
        "conf_puesto": f"Para confirmar, el puesto es {d['titulo']} en {d['empresa']}. ¿Te interesa?",
        "conf_sueldo": f"El sueldo es {_texto_sueldo(d['sueldo'])}. ¿Estás de acuerdo?",
        "conf_traslado": f"El trabajo es en {d['ubicacion']}. ¿Puedes trasladarte?",
        "inicio": "¿Cuándo podrías empezar?",
        "rep_despido": "¿Por qué te despidieron?",
        "rep_renuncia": "¿Por qué renunciaste?",
        "rep_experiencia": PREGUNTA_SIN_EXPERIENCIA,
    }[pid]


# ============================================================ máquina de estados

def _estado(e) -> dict:
    return copy.deepcopy((e.guion or {}).get("fija") or {})


def _guardar(e, est: dict) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    g = dict(e.guion or {})
    g["fija"] = est
    e.guion = g
    flag_modified(e, "guion")


def _secuencia_d(p, quitadas: List[str]) -> List[str]:
    d = _datos(p)
    ids = ["conf_puesto"]
    if _texto_sueldo(d["sueldo"]):
        ids.append("conf_sueldo")
    if d["ubicacion"]:
        ids.append("conf_traslado")
    ids.append("inicio")
    return [x for x in ids if x not in quitadas]


def siguiente(est: dict, p) -> Optional[str]:
    """La siguiente pregunta según lo respondido (None = toca el cierre)."""
    cfg = config_de(p.vacante)
    q = cfg["quitadas"]
    r = est.get("respuestas") or {}
    if "trabaja" not in q and "trabaja" not in r:
        return "trabaja"
    if est.get("nunca_trabajo") and "rep_experiencia" not in r:
        return "rep_experiencia"
    if (est.get("actual") is False and not est.get("nunca_trabajo") and "rep_experiencia" not in r
            and "sin_trabajo" not in q and "sin_trabajo" not in r):
        return "sin_trabajo"
    if not est.get("historial_cerrado"):
        k = int(est.get("k") or 0)
        trabajos = est.get("trabajos") or []
        trabajo = trabajos[k] if len(trabajos) > k else {}
        for campo in ("empresa", "puesto", "tareas", "duracion", "motivo"):
            if campo in q or campo in trabajo or (campo == "puesto" and trabajo.get("informal")):
                continue
            return campo
        if trabajo.get("repregunta") and "repregunta_respuesta" not in trabajo:
            return trabajo["repregunta"]
        if "otro" not in q and k + 1 < MAX_TRABAJOS and f"otro{k}" not in r:
            return "otro"
    for i, _ in enumerate(cfg["propias"]):
        if f"propia:{i}" not in r:
            return f"propia:{i}"
    for x in _secuencia_d(p, q):
        if x not in r:
            return x
    return None


# «Nunca he trabajado» (2026-10-13): antes de cerrar el historial se repregunta UNA vez por trabajos informales
PREGUNTA_SIN_EXPERIENCIA = "¿Has hecho algún trabajo, aunque sea por tu cuenta o ayudando a alguien?"
_RE_NUNCA_TRABAJO = re.compile(r"\b(nunca (he )?trabaj\w*|no he trabajado( nunca)?|nunca he tenido (un )?(trabajo|empleo)|no tengo experiencia"
                               r"|sin experiencia|(es|seria|este seria|este es) mi primer (trabajo|empleo))\b")
_RE_SIN_TRABAJO = re.compile(r"^(no|nunca|ninguno|ninguna|no he trabajado|nunca he trabajado|es mi primer trabajo|sin experiencia)\b")
_RE_DESPIDO = re.compile(r"\b(me corrieron|me despidieron|despid\w*|me sacaron|me liquidaron|me dieron las gracias|me echaron|corrieron)\b")
_RE_RENUNCIA = re.compile(r"\b(renunci\w*|me sali|me fui|deje el trabajo|lo deje)\b")


def repregunta_motivo(texto: str) -> Optional[str]:
    """Despido → «¿Por qué te despidieron?»; renuncia sin motivo → «¿Por qué renunciaste?» (una sola vez por trabajo)."""
    t = _n(texto)
    if _RE_DESPIDO.search(t) and not re.search(r"\b(porque|por que|ya que|pues)\b", t):
        return "rep_despido"
    if _RE_RENUNCIA.search(t) and len(t.split()) <= 4 and not re.search(r"\b(porque|por que|ya que|pues|para)\b", t):
        return "rep_renuncia"
    return None


def registrar_respuesta(est: dict, pid: str, texto: str, p) -> None:
    from .prefiltro_conversacional import clasificar

    texto = texto.strip()[:600]
    r = est.setdefault("respuestas", {})
    trabajos = est.setdefault("trabajos", [])
    k = int(est.get("k") or 0)
    si_no = clasificar({"criterio": "", "pregunta": "", "chat": ""}, texto)
    if pid == "trabaja":
        r["trabaja"] = texto
        est["actual"] = si_no == "si"
        if _RE_NUNCA_TRABAJO.search(_n(texto)):
            est["actual"] = False
            est["nunca_trabajo"] = True
        return
    if pid == "sin_trabajo":
        r["sin_trabajo"] = texto
        if _RE_NUNCA_TRABAJO.search(_n(texto)):
            est["nunca_trabajo"] = True
        return
    if pid == "rep_experiencia":
        r["rep_experiencia"] = texto
        est.pop("nunca_trabajo", None)
        t = _n(texto)
        if si_no == "no" or _RE_NUNCA_TRABAJO.search(t) or _RE_SIN_TRABAJO.match(t) and len(t.split()) <= 3:
            est["historial_cerrado"] = True
            est["sin_experiencia"] = True
        else:
            # trabajo informal: lo que contó es el «dónde»; sigue con qué hacía, cuánto tiempo y por qué dejó de hacerlo
            trabajos.clear()
            trabajos.append({"actual": False, "informal": True, "empresa": texto, "puesto": "Por su cuenta"})
            est["k"] = 0
        return
    if pid in ("empresa", "puesto", "tareas", "duracion", "motivo"):
        while len(trabajos) <= k:
            trabajos.append({"actual": bool(est.get("actual")) and len(trabajos) == 0})
        if pid == "empresa" and k == 0 and not est.get("actual") and (_RE_SIN_TRABAJO.match(_n(texto)) or _RE_NUNCA_TRABAJO.search(_n(texto))):
            trabajos.pop()  # dice que nunca ha trabajado: antes de cerrar el historial, UNA repregunta por trabajo informal
            if "rep_experiencia" in r:
                est["historial_cerrado"] = True
                est["sin_experiencia"] = True
            else:
                est["nunca_trabajo"] = True
            return
        trabajos[k][pid] = texto
        if pid == "motivo":
            rep = repregunta_motivo(texto)
            if rep:
                trabajos[k]["repregunta"] = rep
        return
    if pid in ("rep_despido", "rep_renuncia"):
        trabajos[k]["repregunta_respuesta"] = texto
        trabajos[k]["motivo"] = f"{trabajos[k].get('motivo', '')} — {texto}".strip(" —")
        return
    if pid == "otro":
        r[f"otro{k}"] = texto
        if si_no == "si" and k + 1 < MAX_TRABAJOS:
            est["k"] = k + 1
        else:
            est["historial_cerrado"] = True
        return
    r[pid] = texto
    if pid in ("conf_puesto", "conf_sueldo", "conf_traslado"):
        est.setdefault("confirmaciones", {})[pid] = si_no


def _cerrar_historial_si_toca(est: dict, p) -> None:
    """Sin la pregunta «¿Tuviste otro trabajo…?» (o tras el 3.º) el historial se cierra al completar el trabajo."""
    q = config_de(p.vacante)["quitadas"]
    k = int(est.get("k") or 0)
    trabajos = est.get("trabajos") or []
    if est.get("historial_cerrado") or len(trabajos) <= k:
        return
    t = trabajos[k]
    completo = all(c in q or c in t for c in ("empresa", "puesto", "tareas", "duracion", "motivo"))
    if completo and (not t.get("repregunta") or "repregunta_respuesta" in t) and ("otro" in q or k + 1 >= MAX_TRABAJOS):
        est["historial_cerrado"] = True


def primera(e) -> str:
    """Primera pregunta de la entrevista (va en el mensaje de transición)."""
    p = e.postulacion
    est = _estado(e)
    pid = siguiente(est, p)
    est["pendiente"] = pid
    _guardar(e, est)
    return texto_pregunta(pid, est, p) if pid else ""


def turno(e, texto: str) -> Tuple[str, bool]:
    """Registra la respuesta a la pregunta pendiente y regresa (siguiente mensaje, terminada)."""
    p = e.postulacion
    est = _estado(e)
    pid = est.get("pendiente") or siguiente(est, p)
    if pid:
        registrar_respuesta(est, pid, texto, p)
        _cerrar_historial_si_toca(est, p)
    nxt = siguiente(est, p)
    est["pendiente"] = nxt
    if nxt is None:
        est["terminada_en"] = datetime.now(timezone.utc).isoformat()
        _guardar(e, est)
        return cierre(p), True
    _guardar(e, est)
    return texto_pregunta(nxt, est, p), False


# ============================================================ evaluación sobre el historial

def historial(e) -> List[dict]:
    """Historial laboral para la ficha: Empresa · Puesto · Tareas · Duración · Motivo."""
    salida = []
    for t in (_estado(e).get("trabajos") or []):
        salida.append({"empresa": t.get("empresa", ""), "puesto": t.get("puesto", ""), "tareas": t.get("tareas", ""),
                       "duracion": t.get("duracion", ""), "motivo": t.get("motivo", ""), "actual": bool(t.get("actual"))})
    return salida


_RE_MESES = re.compile(r"(\d+(?:[.,]\d+)?)\s*(anos?|años?|mes(?:es)?)")


def _similar(trabajo: dict, v) -> bool:
    """¿El trabajo es similar al puesto? Mismo oficio de la Biblioteca o comparte palabras con el puesto."""
    from . import entrevista_operativa as eop

    texto = " ".join(str(trabajo.get(k) or "") for k in ("puesto", "tareas"))
    if not texto.strip():
        return False
    titulo = getattr(v, "titulo", "") or ""
    oficio = eop.detectar_oficio(titulo, list(getattr(v, "responsabilidades", None) or []))
    if oficio and eop.detectar_oficio(texto, []) == oficio:
        return True
    vacias = {"de", "del", "la", "el", "en", "y", "a", "general", "auxiliar", "ayudante"}
    claves = {w for w in _n(titulo).split() if len(w) > 3 and w not in vacias}
    raices = {w[:5] for w in claves}
    return any(w[:5] in raices for w in _n(texto).split() if len(w) > 3)


def contradicciones(est: dict, p) -> List[str]:
    """Confirmaciones de D que contradicen al prefiltro → «Inconsistencia: …» (Puntos por validar)."""
    d = ((p.analisis or {}).get("prefiltro_datos")) or {}
    conf = est.get("confirmaciones") or {}
    pares = (("conf_puesto", "interes_puesto", "le interesa el puesto"), ("conf_sueldo", "sueldo_acepta", "está de acuerdo con el sueldo"),
             ("conf_traslado", "puede_trasladarse", "puede trasladarse"))
    salida = []
    for c, k, texto in pares:
        ahora, antes = conf.get(c), d.get(k)
        if ahora in ("si", "no") and isinstance(antes, bool) and (ahora == "si") != antes:
            salida.append(f"Inconsistencia: en el prefiltro dijo que {'sí' if antes else 'no'} {texto} y en la entrevista que "
                          f"{'sí' if ahora == 'si' else 'no'}")
        elif ahora == "no" and antes is None:
            salida.append(f"En la entrevista dijo que no {texto}")
    return salida


def evaluar(e, p, v) -> dict:
    """Recomendación operativa calculada SOBRE EL HISTORIAL LABORAL (mismo formato que `entrevista_operativa.resultado`):
    experiencia en un puesto similar con datos concretos (empresa, puesto y duración) y ningún Punto por validar →
    Recomendable; sin experiencia y con Puntos por validar → No recomendable; lo demás → Con reservas. Nunca descarta."""
    from . import entrevista_operativa as eop

    est = _estado(e)
    trabajos = est.get("trabajos") or []
    similares = [t for t in trabajos if _similar(t, v) and t.get("empresa") and t.get("duracion")]
    if similares:
        t = similares[0]
        a = eop._criterio("cumple", f"{t.get('puesto') or 'Puesto similar'} en {t.get('empresa')} ({t.get('duracion')})")
    elif trabajos:
        t = trabajos[0]
        a = eop._criterio("parcial", f"Experiencia en {t.get('puesto') or t.get('empresa') or 'otro puesto'}; no es similar o faltan datos")
    else:
        a = eop._criterio("no_cumple", "Sin historial laboral")
    criterios = {"A": a, "B": eop._criterio("sin_dato", "Entrevista fija: sin situación del oficio"),
                 "C": eop._criterio("cumple", "Confirmados en el prefiltro"), "D": eop._criterio("cumple" if est.get("respuestas", {}).get("inicio") else "sin_dato",
                                                                                                est.get("respuestas", {}).get("inicio") or "")}
    extra = contradicciones(est, p)
    for t in trabajos:
        if t.get("motivo") and eop.es_salida_conflictiva(t["motivo"]):
            extra.append(f"{t['motivo']} ({t.get('empresa') or 'trabajo anterior'})")
    prefiltro = eop.contexto_prefiltro(p)
    puntos = eop.puntos_de(criterios, [], prefiltro, extra)
    puntos += [x for x in ((p.analisis or {}).get("puntos_validar_prefiltro") or []) if x not in puntos]
    reqs = eop.requisitos_de(criterios, [], {}, prefiltro, puntos)
    op = eop.resultado(criterios, puntos, None, reqs)
    op["historial_laboral"] = historial(e)
    op["fija"] = True
    return op
