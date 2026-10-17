"""Cambio 29 (2026-10-12) — Entrevista Red Human por WhatsApp FIJA para rutas Masivos (operativos). Cero IA generativa.

Flujo (una pregunta por mensaje, siempre en este orden):
  Transición: «Listo, [nombre]. Ahora te haré unas preguntas sobre tu experiencia.» + la primera pregunta.
  A. Situación actual: «¿Trabajas actualmente?» → sí: el primer trabajo del historial es el actual (en presente) ·
     no: «¿Cuánto tiempo llevas sin trabajar?» (historial: fila «Sin empleo actual» + su último empleo) · nunca: bloque C
     directo y el resumen dice «Sin experiencia laboral» (Cambio 52).
  B. Historial laboral: hasta 2 trabajos (2026-10-16, QA) — empresa, puesto, qué hacía, duración y motivo de salida; el
     segundo con OTRAS palabras (`SEGUNDA_VUELTA`). Un despido o una renuncia sin motivo se REPREGUNTA una vez («¿Por qué te
     despidieron?» / «¿Por qué renunciaste?»). Después del primero: «¿Tuviste otro trabajo antes de ese?». Al final:
     «¿En cuántos trabajos has estado en los últimos 3 años?».
  C. Preguntas propias que RH agregó a la vacante.
  D. (2026-10-16, QA) UNA confirmación: «Antes de terminar, te confirmo: puesto, empresa, ubicación, sueldo. ¿Sigues
     interesado?» → sí: «¿Cuándo podrías empezar?» y cierre EXACTO · no: el motivo (Sueldo, Ubicación, Horario u Otro),
     «No interesado» (detiene la ruta para RH; NUNCA descarta) y cierre sin prometer entrevista. Lo que contradiga al
     prefiltro va a Puntos por validar (nunca descarta). Al terminar, `ficha` = resumen A · B · C · D · alertas para RH.
La vacante guarda su ajuste en `Vacante.guiones["entrevista_whatsapp_base"] = {quitadas: [ids], propias: [textos]}`: RH
puede quitar preguntas base o agregar propias (van al bloque C); «Restaurar base» = vacío. La recomendación se calcula
sobre el historial laboral (`evaluar`). Un solo nombre de empresa en toda la conversación: `serial.nombre_empresa_candidato`.
"""

import copy
import re
import unicodedata
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

MAX_TRABAJOS = 2  # 2026-10-16 (QA): máximo 2 trabajos; el segundo con otras palabras («segunda vuelta»)
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
    {"id": "otro", "bloque": "B", "texto": "¿Tuviste otro trabajo antes de ese? (hasta 2 trabajos)"},
    {"id": "cuantos", "bloque": "B", "texto": "¿En cuántos trabajos has estado en los últimos 3 años?"},
    {"id": "conf_final", "bloque": "D", "texto": "Antes de terminar, te confirmo: puesto [puesto], empresa [empresa], ubicación [ubicación], sueldo [sueldo]. ¿Sigues interesado?"},
    {"id": "inicio", "bloque": "D", "texto": "¿Cuándo podrías empezar?"},
]
# Preguntas del bloque D anteriores al 2026-10-16 (conversaciones en curso y ajustes guardados en vacantes)
IDS_D_LEGADO = ("conf_puesto", "conf_sueldo", "conf_traslado")
IDS_BASE = [b["id"] for b in BASE]
QUITABLES = [b["id"] for b in BASE if not b.get("fija")] + list(IDS_D_LEGADO)
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
    """Entrevista por WhatsApp con enfoque OPERATIVO (Cambio 43: el «Enfoque» de la actividad; sin él, ruta Masivos)."""
    from .proceso import enfoques_de_ruta, tiene_proceso

    p = getattr(e, "postulacion", None)
    return (getattr(e, "tipo", "") == "whatsapp" and p is not None and tiene_proceso(p)
            and enfoques_de_ruta(p.proceso).get("entrevista_whatsapp") == "operativo")


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
    return f"Listo{', ' + d['nombre'] if d['nombre'] else ''}. Ahora te haré unas preguntas sobre tu experiencia. {primera}"


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


def cierre_no_interesado(p) -> str:
    """2026-10-16 (QA): cierre cuando ya no le interesa — sin prometer entrevista; RH decide con su motivo a la vista."""
    d = _datos(p)
    return (f"Con esto terminamos{', ' + d['nombre'] if d['nombre'] else ''}. Gracias por tu tiempo y por tu sinceridad. "
            f"Compartiré tus comentarios con el equipo de Recursos Humanos de {d['empresa']}.")


def _texto_sueldo(sueldo: str) -> str:
    t = " ".join(str(sueldo or "").split()).strip().rstrip(".")
    if not t or _n(t) in ("a convenir", "por definir"):
        return ""
    return t[0].lower() + t[1:] if t.lower().startswith("desde ") else f"de {t}"


# 2026-10-16 (QA): el segundo trabajo se pregunta con OTRAS palabras (nunca se repiten textualmente las del primero)
SEGUNDA_VUELTA = {
    "empresa": "¿Y antes de ese, en qué empresa estuviste?",
    "puesto": "¿Qué cargo tenías en ese lugar?",
    "tareas": "¿A qué te dedicabas en ese empleo?",
    "duracion": "¿Cuánto duraste en ese empleo?",
    "motivo": "¿Qué te hizo dejar ese empleo?",
}
PREGUNTA_CUANTOS = "¿En cuántos trabajos has estado en los últimos 3 años?"
PREGUNTA_MOTIVO_NO = "Entiendo. ¿Qué es lo que no te convence: el sueldo, la ubicación, el horario u otra cosa?"
MOTIVOS_NO_INTERES = ("Sueldo", "Ubicación", "Horario", "Otro")
_RE_MOTIVO = (
    ("Sueldo", re.compile(r"\b(sueldo|salario|pago|paga|pagan|dinero|lana|varo|poco|bajo|mas dinero|gano mas|ganar mas|prestaciones)\b")),
    ("Ubicación", re.compile(r"\b(lejos|ubicacion|distancia|traslado|transporte|zona|queda lejos|me queda|camion|pasaje|ciudad|mudar\w*)\b")),
    ("Horario", re.compile(r"\b(horario|turno|turnos|hora|horas|noche|nocturno|fin de semana|fines de semana|sabado|domingo|rolar|dias)\b")),
)


def motivo_no_interes(texto: str) -> Optional[str]:
    """Clasifica por qué ya no le interesa (Sueldo, Ubicación, Horario u Otro). None = la respuesta no dice el motivo."""
    t = _n(texto)
    for motivo, rx in _RE_MOTIVO:
        if rx.search(t):
            return motivo
    if re.search(r"\b(otr[oa]|otra cosa|personal|familia|ya encontre|ya tengo|no me interesa)\b", t) or len(t.split()) >= 4:
        return "Otro"
    return None


def texto_confirmacion_final(p) -> str:
    """Bloque D en UN solo mensaje (2026-10-16, QA): «Antes de terminar, te confirmo: puesto [x], empresa [y], ubicación
    [z], sueldo [s]. ¿Sigues interesado?» — sin los datos que la vacante no tiene (el sueldo solo si está publicado)."""
    from .prefiltro_conversacional import sueldo_publicado

    d = _datos(p)
    partes = [f"puesto {d['titulo']}", f"empresa {d['empresa']}"]
    if d["ubicacion"]:
        partes.append(f"ubicación {d['ubicacion']}")
    v = p.vacante
    if v is not None and sueldo_publicado(v) and _texto_sueldo(d["sueldo"]):
        from .oferta import sueldo_exacto

        partes.append(f"sueldo {sueldo_exacto(d['sueldo']) or d['sueldo']}")
    return f"Antes de terminar, te confirmo: {', '.join(partes)}. ¿Sigues interesado?"


def texto_pregunta(pid: str, est: dict, p) -> str:
    """Texto de la pregunta `pid` (en presente si el trabajo es el actual)."""
    d = _datos(p)
    k = int(est.get("k") or 0)
    actual = bool(est.get("actual")) and k == 0
    if pid.startswith("propia:"):
        return config_de(p.vacante)["propias"][int(pid.split(":")[1])]
    if k >= 1 and pid in SEGUNDA_VUELTA:
        return SEGUNDA_VUELTA[pid]
    if pid == "conf_final":
        return texto_confirmacion_final(p)
    if pid == "motivo_no":
        return PREGUNTA_MOTIVO_NO
    if pid == "cuantos":
        return PREGUNTA_CUANTOS
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


def ya_dicho(p) -> Dict[str, bool]:
    """Cambio 52 (regla de oro): qué ya se le dijo / respondió antes (solicitud web o prefiltro) — puesto, sueldo y
    ubicación no se vuelven a confirmar en el bloque D."""
    d = ((p.analisis or {}).get("prefiltro_datos")) or {}
    # 2026-10-16: ya no existe «El puesto es… ¿Te interesa?» — haber contestado la solicitud o el prefiltro de ESTA
    # vacante (cuya página dice puesto y ubicación) cuenta como puesto y ubicación ya dichos
    contesto = d.get("interes_puesto") is not None or any(k != "en" for k in d) or bool((p.analisis or {}).get("respuestas_web"))
    return {"conf_puesto": contesto,
            # 2026-10-16 (bug del tester): la página también publica el sueldo; ya no se pide la expectativa, así que
            # contestar la solicitud o el prefiltro cuenta como sueldo dicho (no se vuelve a plantear)
            "conf_sueldo": contesto or d.get("sueldo_acepta") is not None or bool(d.get("sueldo")),
            # Cambio 51: la pregunta del puesto ya dice la ciudad y el estado («El puesto es … en [ciudad, estado]»)
            "conf_traslado": contesto
            or any(d.get(k) not in (None, "") for k in ("traslado", "traslado_min", "puede_trasladarse", "reubicacion"))}


def _secuencia_d(p, quitadas: List[str], est: Optional[dict] = None) -> List[str]:
    """Bloque D (2026-10-16, QA): UNA confirmación con puesto, empresa, ubicación y sueldo («¿Sigues interesado?») → si ya
    no le interesa, el motivo (Sueldo, Ubicación, Horario u Otro) y el cierre; si sigue interesado, la fecha de inicio."""
    est = est or {}
    ids = [] if "conf_final" in quitadas else ["conf_final"]
    ni = est.get("no_interesado")
    if ni:
        return ids + ([] if ni.get("motivo") else ["motivo_no"])
    return ids + ([] if "inicio" in quitadas else ["inicio"])


def _respondidas_antes(p) -> set:
    """Textos (normalizados) de lo que el candidato YA contestó en la solicitud web o en el prefiltro."""
    a = p.analisis or {}
    salida = {_n(r.get("pregunta")) for r in a.get("respuestas_web") or [] if isinstance(r, dict) and str(r.get("respuesta") or "").strip()}
    salida |= {_n(r.get("pregunta")) for r in a.get("respuestas_prefiltro") or [] if isinstance(r, dict) and r.get("respuesta")}
    pw = (a.get("prefiltro_web") or {}).get("criterios") or []
    salida |= {_n(c.get("pregunta")) for c in pw if isinstance(c, dict) and c.get("respuesta")}
    return {x for x in salida if x}


def propia_ya_respondida(p, texto: str) -> bool:
    """Cambio 52: una pregunta propia de RH (bloque C) que ya contestó antes no se repite."""
    return _n(texto) in _respondidas_antes(p)


def siguiente(est: dict, p) -> Optional[str]:
    """La siguiente pregunta según lo respondido (None = toca el cierre)."""
    cfg = config_de(p.vacante)
    q = cfg["quitadas"]
    r = est.get("respuestas") or {}
    if "trabaja" not in q and "trabaja" not in r:
        return "trabaja"
    if est.get("nunca_trabajo") and "rep_experiencia" not in r:
        return "rep_experiencia"  # conversación anterior al Cambio 52 que ya tenía la repregunta pendiente
    if (est.get("actual") is False and not est.get("sin_experiencia") and not est.get("nunca_trabajo")
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
    if "cuantos" not in q and "cuantos" not in r and not est.get("sin_experiencia"):
        return "cuantos"  # 2026-10-16 (QA)
    for i, texto in enumerate(cfg["propias"]):
        if f"propia:{i}" not in r and not propia_ya_respondida(p, texto):
            return f"propia:{i}"
    for x in _secuencia_d(p, q, est):
        if x not in r:
            return x
    return None


# Cambio 52 (2026-10-15): «Nunca he trabajado» → bloque C directo (sin historial) y el resumen dice «Sin experiencia
# laboral». (La repregunta por trabajos informales del 2026-10-13 ya no se hace; se conserva el texto para conversaciones
# que ya la tenían pendiente.)
PREGUNTA_SIN_EXPERIENCIA = "¿Has hecho algún trabajo, aunque sea por tu cuenta o ayudando a alguien?"
SIN_EXPERIENCIA = "Sin experiencia laboral"
SIN_EMPLEO_ACTUAL = "Sin empleo actual"
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
        if _RE_NUNCA_TRABAJO.search(_n(texto)) or _n(texto) in ("nunca", "nunca he trabajado"):
            _sin_experiencia(est)
        return
    if pid == "sin_trabajo":
        r["sin_trabajo"] = texto
        if _RE_NUNCA_TRABAJO.search(_n(texto)) or _n(texto) == "nunca":
            _sin_experiencia(est)
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
            trabajos.pop()  # Cambio 52: dice que nunca ha trabajado → bloque C directo
            _sin_experiencia(est)
            return
        # Cambio 53: si la respuesta no es un dato («¿En dónde?», «no sé»), la celda queda VACÍA (nunca se inventa)
        trabajos[k][pid] = "" if no_es_dato(texto) else texto
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
    if pid in IDS_D_LEGADO:
        est.setdefault("confirmaciones", {})[pid] = si_no
    if pid == "conf_final":
        # «no» = ya no le interesa: se marca «No interesado» (NUNCA se descarta en automático; RH decide)
        motivo = motivo_no_interes(texto) if si_no == "no" or _RE_NO_INTERESA.search(_n(texto)) else None
        if si_no == "no" or _RE_NO_INTERESA.search(_n(texto)):
            est["no_interesado"] = {"motivo": motivo or "", "texto": texto}
            est.setdefault("confirmaciones", {})["conf_final"] = "no"
        else:
            est.setdefault("confirmaciones", {})["conf_final"] = "si" if si_no == "si" else "sin_dato"
    if pid == "motivo_no":
        ni = est.setdefault("no_interesado", {"texto": ""})
        ni["motivo"] = "Otro" if texto == SIN_RESPUESTA else (motivo_no_interes(texto) or "Otro")
        ni["detalle"] = texto


_RE_NO_INTERESA = re.compile(r"\b(ya no me interesa|no me interesa|no estoy interesad\w*|ya no|mejor no|paso)\b")


def _sin_experiencia(est: dict) -> None:
    est["actual"] = False
    est["sin_experiencia"] = True
    est["historial_cerrado"] = True
    est.pop("nunca_trabajo", None)


SIN_RESPUESTA = "Sin respuesta"  # Cambio 59


def no_es_dato(texto: str) -> bool:
    """Cambio 53: la respuesta no aporta un dato para el historial (una pregunta de vuelta, «no sé», vacía)."""
    from .preguntas_filtro import es_evasiva

    crudo = (texto or "").strip()
    t = _n(crudo)
    return (not t or (("?" in crudo or "¿" in crudo) and len(t.split()) <= 6) or es_evasiva(crudo)
            or t in ("en donde", "donde", "que", "como", "cual", "mande", "perdon"))


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


# Cambio 51: «no entiendo» → la MISMA pregunta con OTRAS palabras (nunca repetida)
ALTERNATIVAS = {
    "trabaja": "¿Ahorita tienes un empleo?",
    "sin_trabajo": "¿Desde cuándo no tienes empleo?",
    "empresa": "¿Cómo se llama la empresa donde trabajaste?",
    "puesto": "¿Cuál era tu puesto o tu función ahí?",
    "tareas": "¿Cuáles eran tus actividades en ese trabajo?",
    "duracion": "¿Cuántos meses o años estuviste en ese trabajo?",
    "motivo": "¿Qué pasó para que dejaras ese trabajo?",
    "otro": "Antes de ese empleo, ¿trabajaste en otro lugar?",
    "inicio": "¿En qué fecha podrías comenzar a trabajar?",
    "cuantos": "En los últimos 3 años, ¿cuántos empleos has tenido?",
    "conf_final": "¿Sigues interesado en el puesto con esas condiciones? Respóndeme sí o no.",
    "motivo_no": "¿Es por el sueldo, por la ubicación, por el horario o por otra razón?",
}


def turno(e, texto: str) -> Tuple[str, bool]:
    """Registra la respuesta a la pregunta pendiente y regresa (siguiente mensaje, terminada)."""
    from .prefiltro_conversacional import es_negociacion, no_entiende, texto_negociacion

    p = e.postulacion
    est = _estado(e)
    pid = est.get("pendiente") or siguiente(est, p)
    if pid and es_negociacion(texto):
        # Cambio 51: nunca prometer nada fuera de la vacante; se retoma la MISMA pregunta
        return f"{texto_negociacion(p)} {texto_pregunta(pid, est, p)}", False
    from . import respuestas_vacante as rva

    tema = rva.tema_pregunta(texto) if pid else None
    if tema:
        # Cambio 59: una pregunta sobre la vacante NUNCA se registra como respuesta — el dato y la MISMA pregunta pendiente
        return rva.con_pendiente(p, tema, texto_pregunta(pid, est, p)), False
    no_contesta = bool(pid) and (no_entiende(texto) or no_es_dato(texto))
    if pid == "conf_final" and not no_contesta:
        from .prefiltro_conversacional import clasificar

        # «¿Sigues interesado?» necesita un sí o un no: una respuesta ambigua se repregunta UNA vez
        no_contesta = (clasificar({"criterio": "", "pregunta": "", "chat": ""}, texto) not in ("si", "no")
                       and not _RE_NO_INTERESA.search(_n(texto)) and motivo_no_interes(texto) is None)
    if no_contesta and (est.get("refraseada") or {}).get(pid):
        # Cambio 59: ya se repreguntó UNA vez y sigue sin contestar → «Sin respuesta» y se avanza
        texto = SIN_RESPUESTA
    elif no_contesta:
        est.setdefault("refraseada", {})[pid] = True
        _guardar(e, est)
        alt = ALTERNATIVAS.get(pid) or texto_pregunta(pid, est, p)
        if pid in ("empresa", "puesto", "tareas", "duracion", "motivo") and bool(est.get("actual")) and int(est.get("k") or 0) == 0:
            alt = (alt.replace("trabajaste", "trabajas").replace("estuviste", "llevas").replace("era tu", "es tu")
                   .replace("eran tus", "son tus").replace("dejaras ese", "quieras dejar ese"))
        return f"Te lo pregunto de otra forma: {alt}", False
    if pid:
        registrar_respuesta(est, pid, texto, p)
        _cerrar_historial_si_toca(est, p)
    nxt = siguiente(est, p)
    est["pendiente"] = nxt
    if nxt is None:
        est["terminada_en"] = datetime.now(timezone.utc).isoformat()
        _guardar(e, est)
        return (cierre_no_interesado(p) if est.get("no_interesado") else cierre(p)), True
    _guardar(e, est)
    return texto_pregunta(nxt, est, p), False


# ============================================================ evaluación sobre el historial

def historial(e) -> List[dict]:
    """Historial laboral para la ficha: Empresa · Puesto · Tareas · Duración · Motivo."""
    est = _estado(e)
    salida = []
    if est.get("sin_experiencia"):
        # Cambio 52: «Nunca he trabajado» → una sola fila
        return [{"empresa": SIN_EXPERIENCIA, "puesto": "", "tareas": "", "duracion": "", "motivo": "", "actual": False}]
    if est.get("actual") is False and "trabaja" in (est.get("respuestas") or {}):
        # Cambio 53: no trabaja actualmente → fila «Sin empleo actual» (con el tiempo sin trabajar) y debajo su último empleo
        sin = (est.get("respuestas") or {}).get("sin_trabajo", "")
        salida.append({"empresa": SIN_EMPLEO_ACTUAL, "puesto": "", "tareas": "", "duracion": "" if no_es_dato(sin) else sin,
                       "motivo": "", "actual": True})
    for t in (est.get("trabajos") or []):
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
    ni = est.get("no_interesado") or {}
    if ni.get("motivo") == "Sueldo" and d.get("sueldo_acepta") is True:
        salida.append("Inconsistencia: en el prefiltro aceptó el sueldo y en la entrevista dijo que no le interesa por el sueldo")
    if ni.get("motivo") == "Ubicación" and (d.get("puede_trasladarse") is True or d.get("ubicacion_elegida")):
        salida.append("Inconsistencia: en el prefiltro aceptó la ubicación y en la entrevista dijo que no le interesa por la ubicación")
    return salida


def evaluar(e, p, v) -> dict:
    """Recomendación operativa calculada SOBRE EL HISTORIAL LABORAL (mismo formato que `entrevista_operativa.resultado`):
    experiencia en un puesto similar con datos concretos (empresa, puesto y duración) y ningún Punto por validar →
    Recomendable; sin experiencia y con Puntos por validar → No recomendable; lo demás → Con reservas. Nunca descarta."""
    from . import entrevista_operativa as eop

    est = _estado(e)
    trabajos = [t for t in est.get("trabajos") or [] if t.get("empresa")]
    similares = [t for t in trabajos if _similar(t, v) and t.get("empresa") and t.get("duracion")]
    if similares:
        t = similares[0]
        a = eop._criterio("cumple", f"{t.get('puesto') or 'Puesto similar'} en {t.get('empresa')} ({t.get('duracion')})")
    elif trabajos:
        t = trabajos[0]
        a = eop._criterio("parcial", f"Experiencia en {t.get('puesto') or t.get('empresa') or 'otro puesto'}; no es similar o faltan datos")
    else:
        a = eop._criterio("no_cumple", SIN_EXPERIENCIA if est.get("sin_experiencia") else "Sin historial laboral")
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
    ni = est.get("no_interesado")
    if ni:
        punto = f"No interesado (motivo: {ni.get('motivo') or 'sin dato'})"
        if punto not in puntos:
            puntos.insert(0, punto)
    reqs = eop.requisitos_de(criterios, [], {}, prefiltro, puntos)
    op = eop.resultado(criterios, puntos, None, reqs)
    if ni:
        # 2026-10-16 (QA): «No interesado» detiene la ruta para RH (como «No avanzar») pero NUNCA descarta
        op["recomendacion"] = "no_recomendable"
        op["recomendacion_texto"] = "No interesado"
        op["no_interesado"] = {"motivo": ni.get("motivo") or "", "texto": ni.get("detalle") or ni.get("texto") or ""}
    op["historial_laboral"] = historial(e)
    op["ficha"] = ficha(e)
    op["fija"] = True
    return op


def ficha(e) -> dict:
    """«Ficha de candidato» (2026-10-16, QA): resumen estructurado de la entrevista para RH — A situación actual, B historial
    (máx. 2 trabajos + cuántos trabajos en 3 años), C preguntas de RH, D confirmación final, y alertas de contradicciones."""
    est = _estado(e)
    p = e.postulacion
    r = est.get("respuestas") or {}
    trabaja = r.get("trabaja", "")
    a = {"trabajaActualmente": ("Sí" if est.get("actual") else "No") if "trabaja" in r else SIN_RESPUESTA,
         "respuesta": trabaja, "tiempoSinTrabajar": r.get("sin_trabajo", "") if est.get("actual") is False else "",
         "sinExperiencia": bool(est.get("sin_experiencia"))}
    b = {"trabajos": [t for t in historial(e) if t.get("empresa") not in (SIN_EMPLEO_ACTUAL,)],
         "trabajosUltimos3Anios": r.get("cuantos", "")}
    propias = config_de(p.vacante)["propias"] if p is not None else []
    c = [{"pregunta": t, "respuesta": r.get(f"propia:{i}", "")} for i, t in enumerate(propias) if f"propia:{i}" in r]
    conf = (est.get("confirmaciones") or {}).get("conf_final")
    ni = est.get("no_interesado") or {}
    d = {"confirmacion": texto_confirmacion_final(p) if p is not None else "",
         "respuesta": r.get("conf_final", ""),
         "interesado": None if conf in (None, "sin_dato") and not ni else not ni,
         "motivoNoInteres": ni.get("motivo") or "", "detalleNoInteres": ni.get("detalle") or "",
         "fechaInicio": r.get("inicio", "")}
    alertas = contradicciones(est, p) if p is not None else []
    if ni:
        alertas.insert(0, f"No interesado (motivo: {ni.get('motivo') or 'sin dato'})")
    for t in est.get("trabajos") or []:
        from . import entrevista_operativa as eop

        if t.get("motivo") and eop.es_salida_conflictiva(t["motivo"]):
            alertas.append(f"{t['motivo']} ({t.get('empresa') or 'trabajo anterior'})")
    return {"A": a, "B": b, "C": c, "D": d, "alertas": alertas, "terminada": bool(est.get("terminada_en"))}
