"""Prefiltro conversacional (2026-10-08; retro 2026-10-09: TODAS las Cuentas cuya ruta trae «Prefiltro por WhatsApp»).

Reglas del bot (retro 2026-10-09, garantizadas en código): solo preguntas CERRADAS sobre los INDISPENSABLES, de una en
una (lo no indispensable solo se registra si vino del formulario); nunca autopercepciones («¿te consideras
organizado?»); lo que ya respondió en la web se reconfirma con el dato exacto; un número sin unidad («8») pide «¿8 años
o 8 meses?»; «no entiendo» reformula la misma pregunta; un indispensable incumplido = mensaje de rechazo y cierre.
Una postulación que ya iba a medio prefiltro con el flujo anterior (o lo terminó) lo conserva.

UNA entidad (`Postulacion.analisis["prefiltro_conversacional"]`) junta lo que el candidato contestó en el formulario web
y lo que contesta en el chat (WhatsApp o Telegram: la fachada `services/whatsapp.py` decide el canal; aquí no hay nada
propio de Meta). Los criterios salen de `Vacante.preguntas_filtro`:

  * `descarta: true` = INDISPENSABLE: la respuesta contraria cierra la postulación con el motivo exacto.
  * sin `descarta` = solo se REGISTRA (NSS / situación fiscal, experiencia, ubicación…): nunca excluye.
  * `clave: "ubicacion"` (o `solo_con_ubicacion`) = solo se pregunta si la vacante tiene ubicación configurada.

Dinámica del bot: pregunta SOLO lo que falte, una pregunta por mensaje y espera la respuesta. Un saludo, `/start` o
cualquier reconexión repite la pregunta pendiente EXACTA. Una respuesta ambigua en un indispensable pide aclaración
(hasta `MAX_ACLARACIONES`; después decide RH con «Revisar prefiltro»). Un «Parcial» o una respuesta faltante del
formulario web NUNCA aprueba: se pregunta en el chat.

Reconfirmación (2026-10-09): si la vacante trae en «Prefiltro · WhatsApp» preguntas que RECONFIRMAN un indispensable
del web (`reconfirma`), después de un «Sí» del formulario se pide el DATO CONCRETO (cuánto tiempo, cuál opción). Si el dato
contradice el «Sí» es una INCONSISTENCIA: nunca cierra la postulación — queda «Revisar prefiltro» para RH y la
contradicción entra a «Puntos por validar».

Resolución al vuelo (sin esperar a RH):
  * Cumple → prefiltro aprobado; el motor de ruta avanza y manda la liga de la Entrevista Red Human por el canal
    conectado (`motor_ruta._disparar_entrevista`). Con la entrevista por liga NUNCA se pide agendar videollamada.
  * Incumple un indispensable → postulación cerrada (`MOTIVO_CIERRE`) con el motivo exacto y mensaje de cierre.
    RH puede revertirlo con «Continuar por decisión de RH» (`reabrir_por_excepcion`): se conservan las respuestas, se
    registra quién autorizó y la ruta sigue sola (liga de la entrevista incluida).
"""

import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import Postulacion, registrar, ruta_automatica

ENTIDAD = "prefiltro_conversacional"
MOTIVO_CIERRE = "prefiltro_no_aprobado"
ACTOR = "Red Human (prefiltro)"
MAX_ACLARACIONES = 2

# ============================================================ criterios de la vacante


def _norm(texto: str) -> str:
    from ..routers.candidatos import _norm_resp

    return re.sub(r"\s+", " ", re.sub(r"[¿?¡!.,;:()«»\"']", " ", _norm_resp(texto or ""))).strip()


def ubicacion_vacante(v) -> str:
    if v is None or (v.modalidad or "").lower().startswith("remot"):
        return ""
    return (v.ubicacion or "").strip() or ", ".join(x for x in (v.ubicacion_municipio, v.ubicacion_estado) if x)


def criterios_de(v) -> List[dict]:
    """Criterios del prefiltro de la vacante, en el orden configurado."""
    salida: List[dict] = []
    usados = set()
    ubic = ubicacion_vacante(v)
    for i, pv in enumerate((v.preguntas_filtro if v is not None else None) or []):
        if not isinstance(pv, dict) or not str(pv.get("pregunta", "")).strip():
            continue
        clave = str(pv.get("clave") or "").strip() or f"c{i + 1}"
        es_ubicacion = clave == "ubicacion" or bool(pv.get("solo_con_ubicacion"))
        if es_ubicacion and not ubic:
            continue  # la vacante no tiene ubicación configurada: no se pregunta
        cid = clave if clave not in usados else f"{clave}-{i + 1}"
        usados.add(cid)
        pregunta = str(pv["pregunta"]).strip()
        from . import ia

        if ia.es_autopercepcion(pregunta):
            continue  # retro 2026-10-09: una autopercepción no se puede verificar — el bot nunca la pregunta
        chat = str(pv.get("pregunta_chat") or "").strip() or pregunta
        if es_ubicacion:
            chat = f"¿Vives en {ubic} o te queda cerca para trasladarte?"
        salida.append({
            "id": cid, "clave": clave, "criterio": str(pv.get("valida") or pregunta).strip(), "pregunta": pregunta,
            "chat": chat, "indispensable": bool(pv.get("descarta")),
            "esperada": "no" if _norm(pv.get("respuesta_esperada") or "Sí") == "no" else "si",
        })
    # Retro 2026-10-09: preguntas PROPIAS del prefiltro por WhatsApp (no reconfirman nada): entran las cerradas e
    # indispensables que el web no cubre (las abiertas de vacantes anteriores no: el bot solo pregunta cerradas).
    ya = {_norm(c["criterio"]) for c in salida} | {_norm(c["pregunta"]) for c in salida}
    from . import ia

    for i, pw in enumerate((v.preguntas_filtro_whatsapp if v is not None else None) or []):
        if (not isinstance(pw, dict) or pw.get("reconfirma") or not pw.get("descarta") or not str(pw.get("pregunta", "")).strip()
                or (pw.get("tipo") or "si_no") not in ("si_no", "opcion", "numero") or ia.es_autopercepcion(pw["pregunta"])):
            continue
        pregunta = str(pw["pregunta"]).strip()
        criterio = str(pw.get("valida") or pregunta).strip()
        if _norm(criterio) in ya or _norm(pregunta) in ya:
            continue
        ya.add(_norm(criterio))
        cid = f"wa{i + 1}"
        usados.add(cid)
        salida.append({"id": cid, "clave": cid, "criterio": criterio, "pregunta": pregunta,
                       "chat": str(pw.get("pregunta_chat") or "").strip() or pregunta, "indispensable": True,
                       "esperada": "no" if _norm(pw.get("respuesta_esperada") or "Sí") == "no" else "si"})
    # 2026-10-09: reconfirmaciones de WhatsApp (dato concreto) de los indispensables del web
    por_criterio = {_norm(c["criterio"]): c for c in salida if c["indispensable"]}
    for pw in (v.preguntas_filtro_whatsapp if v is not None else None) or []:
        if not isinstance(pw, dict) or not pw.get("reconfirma") or not str(pw.get("pregunta", "")).strip():
            continue
        base = por_criterio.get(_norm(pw["reconfirma"]))
        if base is None or f"rc-{base['id']}" in usados:
            continue
        usados.add(f"rc-{base['id']}")
        opciones = [str(o) for o in pw.get("opciones") or [] if str(o).strip()]
        chat = str(pw["pregunta"]).strip() + ("\n" + "\n".join(f"{i}. {o}" for i, o in enumerate(opciones, 1)) if opciones else "")
        salida.append({
            "id": f"rc-{base['id']}", "clave": "reconfirma", "criterio": base["criterio"], "pregunta": str(pw["pregunta"]).strip(),
            "chat": chat, "indispensable": False, "esperada": "si", "reconfirma": base["id"], "tipo": pw.get("tipo") or "opcion",
            "opciones": opciones, "opciones_validas": [str(o) for o in pw.get("opciones_validas") or []], "minimo": pw.get("minimo"),
        })
    return salida


_NUMEROS = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
            "diez": 10, "medio": 0.5}


def _anos(t: str) -> Optional[float]:
    """Años de experiencia de una respuesta libre («3 años», «menos de un año», «más de 4», «6 meses»)."""
    m = re.search(r"(\d+(?:[.,]\d+)?)|\b(" + "|".join(_NUMEROS) + r")\b", t)
    if not m:
        return None
    n = float(m.group(1).replace(",", ".")) if m.group(1) else float(_NUMEROS[m.group(2)])
    if re.search(r"\bmes(es)?\b", t) and not re.search(r"\bano", t):
        n = n / 12
    if re.search(r"\bmenos de\b", t):
        n = max(0.0, n - 0.5)
    elif re.search(r"\b(mas de|arriba de)\b", t):
        n = n + 0.5
    return n


_RE_SOLO_NUMERO = re.compile(r"(?:como |unos |unas |mas o menos |aprox\w* )?(\d+(?:[.,]\d+)?|" + "|".join(_NUMEROS) + r")")


def numero_sin_unidad(c: dict, texto: str) -> Optional[str]:
    """«8» en una pregunta de tiempo: ¿años o meses? Regresa el número (texto) si hay que aclarar la unidad."""
    if c.get("tipo") != "numero":
        return None
    m = _RE_SOLO_NUMERO.fullmatch(_norm(texto))
    return m.group(1) if m else None


def clasificar_dato(c: dict, texto: str) -> Optional[str]:
    """Reconfirmación con dato concreto: «si» si el dato cumple, «no» si lo contradice, None si no se entiende."""
    t = _norm(texto)
    if not t:
        return None
    opciones = c.get("opciones") or []
    validas = {_norm(o) for o in c.get("opciones_validas") or []}
    elegida = None
    if re.fullmatch(r"\d", t) and 1 <= int(t) <= len(opciones) and c.get("tipo") != "numero":
        elegida = opciones[int(t) - 1]
    if elegida is None:
        elegida = next((o for o in opciones if _norm(o) == t), None) or next((o for o in sorted(opciones, key=len, reverse=True)
                                                                              if _norm(o) and _norm(o) in t), None)
    if elegida is not None and validas:
        return "si" if _norm(elegida) in validas else "no"
    if c.get("tipo") == "numero" and c.get("minimo") not in (None, ""):
        n = _anos(t)
        if n is not None:
            return "si" if n >= float(c["minimo"]) else "no"
        return None
    if validas:
        if _RE_NO.match(t):
            return "no" if not any(x.startswith("no") for x in validas) else "si"
        if _RE_SI.match(t) and any(x.startswith("si") for x in validas):
            return "si"
    return None


def aplica(p: Optional[Postulacion]) -> bool:
    if p is None or p.vacante is None or not criterios_de(p.vacante):
        return False
    if ruta_automatica(p.cuenta):
        return True
    # Retro 2026-10-09: todas las Cuentas, si la ruta trae «Prefiltro por WhatsApp» — salvo una postulación que ya iba (o
    # terminó) con el prefiltro anterior: lo conserva.
    pasos = (p.proceso or {}).get("pasos") or []
    if not any(x.get("tipo") == "prefiltro_whatsapp" and not x.get("heredado") for x in pasos):
        return False
    if estado(p):
        return True
    return not (p.prefiltro_completo or (p.analisis or {}).get("respuestas_prefiltro"))


# ============================================================ clasificación de respuestas

_RE_AMBIGUA = re.compile(r"\b(depende|tal vez|quiza|quizas|a veces|mas o menos|no se|no estoy segur\w*|creo que|parcial\w*|"
                         r"algunos?|algunas?|casi|todavia no se|lo veo|lo pienso|puede ser|en ocasiones)\b")
_RE_SI_SIN_PROBLEMA = re.compile(r"\b(no hay problema|sin problema|no tengo problema|ningun problema|no me molesta|no importa)\b")
_RE_NO = re.compile(r"^(no|nop|nel|negativo|nunca|para nada|tampoco|aun no|todavia no)\b")
_RE_SI = re.compile(r"^(si|sii+|sip|simon|claro|por supuesto|afirmativo|correcto|asi es|exacto|yes|ok|okey|va|vale|dale|desde luego|"
                    r"con gusto|cuento con|tengo|puedo|termine|acabe|vivo|estoy)\b")
_RE_NO_ENTIENDE = re.compile(r"(no (le )?entiendo( la pregunta)?|no entendi( la pregunta)?|no le entendi|no comprendo|que|como|mande|"
                             r"perdon|a que te refieres|que quieres decir|que significa( eso)?|no se a que te refieres|explicame|me explicas)")
_RE_SUPERIOR = re.compile(r"\b(prepa|preparatoria|bachiller\w*|licenciatura|universidad|carrera|ingenier\w*|tecnico|cbtis|conalep|cetis|cecyt\w*)\b")
_RE_INCOMPLETA = re.compile(r"\b(trunca|no (la )?termine|sin terminar|inconclus\w*|me falto|incomplet\w*|la deje|no la acabe)\b")
SALUDOS = {"hola", "holi", "ola", "buenas", "buen dia", "buenos dias", "buenas tardes", "buenas noches", "hey", "hi", "hello",
           "start", "/start", "continuar", "seguir", "retomar", "sigo aqui", "ya regrese", "inicio", "empezar", "menu"}


def es_saludo(texto: str) -> bool:
    t = _norm(texto).lstrip("/")
    return t in SALUDOS or t.startswith("start ")


def clasificar(c: dict, texto: str) -> Optional[str]:
    """«si» / «no» / None (ambigua). Determinista primero; la IA solo desempata lo que el léxico no resuelve."""
    t = _norm(texto)
    if not t:
        return None
    base = _norm(c["criterio"] + " " + c["pregunta"])
    if "secundaria" in base:
        if _RE_SUPERIOR.search(t):
            return "si"  # bachillerato o más (aunque esté trunco): la secundaria está terminada
        if "secundaria" in t:
            return "no" if (_RE_INCOMPLETA.search(t) or _RE_NO.match(t)) else "si"
        if "primaria" in t:
            return "no"
    if _RE_AMBIGUA.search(t):
        return None
    if _RE_SI_SIN_PROBLEMA.search(t):
        return "si"
    if _RE_NO.match(t):
        return "no"
    if _RE_SI.match(t):
        return "si"
    from . import ia

    if ia.ia_activa():
        return ia.clasificar_respuesta_prefiltro(c["chat"], texto)
    return None


# ============================================================ entidad única (web + chat)


def estado(p: Postulacion) -> dict:
    return dict((p.analisis or {}).get(ENTIDAD) or {})


def _guardar(p: Postulacion, e: dict) -> None:
    a = dict(p.analisis or {})
    a[ENTIDAD] = e
    p.analisis = a
    flag_modified(p, "analisis")


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sembrar_web(p: Postulacion, e: dict, criterios: List[dict]) -> dict:
    """Las respuestas del formulario web entran a la MISMA entidad. «Sí»/«No» claros cuentan; «Parcial» o vacío quedan
    pendientes y el bot los pregunta."""
    respuestas = dict(e.get("respuestas") or {})
    web = {_norm(r.get("pregunta", "")): str(r.get("respuesta", "")).strip()
           for r in (p.analisis or {}).get("respuestas_web") or [] if isinstance(r, dict)}
    for c in criterios:
        if c["id"] in respuestas:
            continue
        valor = web.get(_norm(c["pregunta"])) or web.get(_norm(c["criterio"]))
        if not valor:
            continue
        v = _norm(valor)
        sentido = "si" if v.startswith("si") else "no" if v == "no" else None
        if sentido is None:
            continue  # «Parcial»: no aprueba ni descarta — se aclara en el chat
        respuestas[c["id"]] = {"respuesta": valor, "valor": sentido, "fuente": "web", "en": _ahora()}
    e["respuestas"] = respuestas
    e["web_importada"] = True
    return e


def _evaluar(criterios: List[dict], e: dict) -> Tuple[Optional[str], Optional[dict], Optional[dict]]:
    """(resultado, criterio que incumple, siguiente criterio por preguntar). resultado ∈ cumple | no_cumple | None."""
    respuestas = e.get("respuestas") or {}
    for c in criterios:
        r = respuestas.get(c["id"])
        if r and c["indispensable"] and r.get("valor") and r["valor"] != c["esperada"]:
            return "no_cumple", c, None
    for c in criterios:
        r = respuestas.get(c["id"])
        if c.get("reconfirma") and r and r.get("valor") == "no":
            return "inconsistencia", c, None  # el dato concreto contradice el «Sí» del formulario: nunca descarta

    def falta(c: dict) -> bool:
        if c["id"] in respuestas:
            return False
        if c.get("reconfirma"):
            # solo se reconfirma un «Sí» dado en el FORMULARIO (lo que ya contestó en el chat no se vuelve a preguntar)
            base = respuestas.get(c["reconfirma"]) or {}
            return base.get("valor") == "si" and base.get("fuente") == "web"
        return c["indispensable"]  # retro 2026-10-09: el chat solo pregunta indispensables (lo demás se registra)

    siguiente = next((c for c in criterios if falta(c)), None)
    return (None, None, siguiente) if siguiente else ("cumple", None, None)


def _criterios_resueltos(criterios: List[dict], e: dict) -> List[dict]:
    respuestas = e.get("respuestas") or {}
    salida = []
    for c in criterios:
        r = respuestas.get(c["id"]) or {}
        if not r:
            veredicto = "sin_respuesta"
        elif not c["indispensable"]:
            veredicto = "registrado"
        else:
            veredicto = "cumple" if r.get("valor") == c["esperada"] else "no_cumple"
        if c.get("reconfirma") and r:
            veredicto = "cumple" if r.get("valor") == "si" else "inconsistencia"
        salida.append({"criterio": c["criterio"], "pregunta": c["pregunta"] if c.get("reconfirma") else c["chat"],
                       "respuesta": r.get("respuesta", ""), "valor": r.get("valor"), "excluyente": c["indispensable"],
                       "veredicto": veredicto, "fuente": r.get("fuente", ""), **({"reconfirma": True} if c.get("reconfirma") else {})})
    return salida


def resumen(p: Postulacion) -> Optional[dict]:
    """Para la ficha y el paso del prefiltro: qué se respondió, por dónde, y qué falta."""
    if not aplica(p):
        return None
    criterios = criterios_de(p.vacante)
    e = estado(p)
    if not e.get("web_importada"):
        e = _sembrar_web(p, dict(e), criterios)
    resultado, _incumple, siguiente = _evaluar(criterios, e)
    return {"resultado": e.get("resultado") or None, "pendiente": (siguiente or {}).get("criterio") if not e.get("resultado") else None,
            "preguntaPendiente": (siguiente or {}).get("chat") if not e.get("resultado") else None,
            "criterios": _criterios_resueltos(criterios, e), "iniciado": bool(e.get("pendiente") or e.get("respuestas"))}


def pregunta_pendiente(p: Postulacion) -> Optional[str]:
    """Texto EXACTO de la pregunta en curso (para retomar desde un deep link o una reconexión)."""
    if not aplica(p):
        return None
    e = estado(p)
    if e.get("resultado"):
        return None
    c = next((x for x in criterios_de(p.vacante) if x["id"] == e.get("pendiente")), None)
    return c["chat"] if c else None


# ============================================================ resolución


def _requisito(c: dict) -> str:
    t = c["criterio"].strip().rstrip(".")
    return t[:1].lower() + t[1:] if t and not t[:2].isupper() else t


def _resolver(db: Session, p: Postulacion, criterios: List[dict], e: dict, resultado: str, motivo: str) -> None:
    """Escribe la decisión en la entidad, en `analisis.prefiltro_web` (lo que lee el paso de la ruta) y en el tablero."""
    e["resultado"] = resultado
    e["motivo"] = motivo
    e["en"] = _ahora()
    e["pendiente"] = None
    _guardar(p, e)
    a = dict(p.analisis or {})
    resueltos = _criterios_resueltos(criterios, e)
    a["prefiltro_web"] = {"resultado": resultado, "motivo": motivo, "criterios": resueltos, "en": e["en"], "decidido_por": "Red Human",
                          "conversacional": True}
    a["respuestas_prefiltro"] = [{"criterio": x["criterio"], "pregunta": x["pregunta"], "respuesta": x["respuesta"],
                                  "cumple": None if x["veredicto"] in ("registrado", "sin_respuesta") else x["veredicto"] == "cumple"}
                                 for x in resueltos if x["respuesta"]]
    a["prefiltro_resultado"] = resultado
    p.analisis = a
    flag_modified(p, "analisis")
    p.estado = resultado
    p.prefiltro_completo = True  # «revision» también: el chat ya no pregunta, decide RH («Revisar prefiltro»)
    registrar(db, ACTOR, "prefiltro_conversacional_resuelto", "postulacion", p.codigo,
              {"resultado": resultado, "motivo": motivo, "criterios": resueltos})


def registrar_inconsistencia(db: Session, p: Postulacion, criterios: List[dict], e: dict, c: dict) -> None:
    """El dato concreto contradice lo que contestó en el formulario: «Inconsistencia» para RH (Puntos por validar) y
    «Revisar prefiltro» — la postulación sigue activa (nunca se descarta por una contradicción). No hace commit."""
    respuestas = e.get("respuestas") or {}
    web = (respuestas.get(c["reconfirma"]) or {}).get("respuesta", "")
    chat = (respuestas.get(c["id"]) or {}).get("respuesta", "")
    a = dict(p.analisis or {})
    a["inconsistencias"] = list(a.get("inconsistencias") or []) + [{
        "criterio": c["criterio"], "web": web, "whatsapp": chat, "aclarada": False, "fuente": "reconfirmacion", "en": _ahora()}]
    p.analisis = a
    flag_modified(p, "analisis")
    _resolver(db, p, criterios, e, "revision", f"Inconsistencia en «{c['criterio']}»: el formulario dice «{web}» y el chat «{chat}»")
    registrar(db, ACTOR, "prefiltro_inconsistencia_detectada", "postulacion", p.codigo,
              {"criterio": c["criterio"], "web": web, "chat": chat})


def _texto_cierre(c: dict) -> str:
    return (f"Gracias por tu interés. Para esta vacante necesitamos {_requisito(c)}, por lo que en esta ocasión no continuaremos "
            "con tu postulación.")


def cerrar_no_aprobado(db: Session, p: Postulacion, criterios: List[dict], e: dict, c: dict) -> str:
    """Incumple un indispensable: cierra la postulación (No aprobado) con el motivo EXACTO. No hace commit. Regresa el
    texto del mensaje de cierre (lo envía quien llama, por el canal de la conversación)."""
    motivo = f"No cumple el requisito indispensable: {c['criterio']}"
    _resolver(db, p, criterios, e, "no_cumple", motivo)
    sello = datetime.now(timezone.utc)
    p.historial = list(p.historial or []) + [{
        "evento": "prefiltro_no_aprobado", "usuario": ACTOR, "fecha": sello.isoformat(), "motivo": motivo,
        "texto": f"Prefiltro no aprobado — {motivo}. Postulación cerrada automáticamente (RH puede continuar por decisión de RH).",
    }]
    p.cerrar(MOTIVO_CIERRE)
    registrar(db, ACTOR, "postulacion_cerrada_prefiltro", "postulacion", p.codigo, {"motivo": motivo, "criterio": c["criterio"]})
    return _texto_cierre(c)


def reabrir_por_excepcion(db: Session, p: Postulacion, u) -> bool:
    """«Continuar por decisión de RH» sobre un prefiltro reprobado que cerró la postulación: la reabre conservando las
    respuestas originales (la entidad y el resultado No aprobado no se tocan). No hace commit."""
    if p.activa or p.motivo_cierre != MOTIVO_CIERRE:
        return False
    p.activa = True
    p.motivo_cierre = ""
    p.cerrada_en = None
    p.estado = "cumple"
    sello = datetime.now(timezone.utc)
    p.historial = list(p.historial or []) + [{
        "evento": "reabierta_por_excepcion", "usuario": u.nombre, "fecha": sello.isoformat(),
        "texto": f"Postulación reabierta por decisión de RH ({u.nombre}); las respuestas del prefiltro se conservan.",
    }]
    registrar(db, u.nombre, "postulacion_reabierta_excepcion_prefiltro", "postulacion", p.codigo, {"correo_rh": getattr(u, "correo", "")})
    return True


# ============================================================ desde el formulario web (motor de ruta)


async def desde_web(db: Session, p: Postulacion) -> Optional[str]:
    """Al recibir la postulación web (o en el barrido del motor): importa las respuestas del formulario y resuelve lo que
    ya se pueda. Pendientes → nada (el bot pregunta cuando el candidato escriba); sin teléfono para preguntarle → RH
    revisa. Hace commit si decide algo. Regresa el resultado o None."""
    e = estado(p)
    if e.get("resultado") or not (p.analisis or {}).get("respuestas_web"):
        return None
    criterios = criterios_de(p.vacante)
    if not e.get("web_importada"):
        e = _sembrar_web(p, e, criterios)
        _guardar(p, e)
    resultado, incumple, siguiente = _evaluar(criterios, e)
    if resultado == "no_cumple":
        texto = cerrar_no_aprobado(db, p, criterios, e, incumple)
        db.commit()
        from .motor_ruta import _avisar

        await _avisar(db, p, texto, f"Tu postulación a {p.vacante.titulo}", "", motivo="prefiltro")
        db.commit()
        return "no_cumple"
    if resultado == "cumple":
        _resolver(db, p, criterios, e, "cumple", "Cumple los requisitos indispensables de la vacante")
        db.commit()
        return "cumple"
    if not p.telefono:
        _resolver(db, p, criterios, e, "revision", f"Falta responder: {siguiente['criterio']} (el candidato no dejó teléfono para preguntarle)")
        db.commit()
        return "revision"
    db.commit()
    return None


# ============================================================ turno del chat


async def turno(db: Session, p: Postulacion, texto: str, canal: str, reconexion: bool = False) -> Optional[dict]:
    """Un turno del bot (el mensaje del candidato ya está guardado). None = el prefiltro ya está resuelto y el turno lo
    atiende el flujo de siempre (seguimiento, documentos…)."""
    from ..routers.candidatos import _actualizar_ultima_actividad, _enviar_whatsapp, guardar_mensaje, nombre_ficha
    from .mensajeria import canal_registro

    e = estado(p)
    if e.get("resultado"):
        return None
    criterios = criterios_de(p.vacante)
    if not e.get("web_importada"):
        e = _sembrar_web(p, e, criterios)

    async def responder(mensaje: str, **extra) -> dict:
        _guardar(p, e)
        envio = await _enviar_whatsapp(p, mensaje, canal)
        guardar_mensaje(db, p, "assistant", mensaje, canal, envio)
        _actualizar_ultima_actividad(p)
        db.commit()
        return {"respuesta": mensaje, "clasificacion": None, "ia": False, "whatsapp": envio, "prefiltro": e.get("resultado"), **extra}

    vacante = p.vacante.titulo if p.vacante else "la vacante"
    pendiente = next((c for c in criterios if c["id"] == e.get("pendiente")), None)
    aclaraciones = dict(e.get("aclaraciones") or {})
    if pendiente is not None and (reconexion or es_saludo(texto)):
        # reconexión: la pregunta pendiente EXACTA, sin contarla como respuesta
        return await responder(f"Retomemos tu postulación a *{vacante}* 👇\n\n{pendiente['chat']}", retomada=True)
    if pendiente is not None and _RE_NO_ENTIENDE.fullmatch(_norm(texto)):
        # retro 2026-10-09: «no entiendo» → la MISMA pregunta con otras palabras (cuenta como aclaración)
        n = aclaraciones.get(pendiente["id"], 0) + 1
        if n <= MAX_ACLARACIONES:
            aclaraciones[pendiente["id"]] = n
            e["aclaraciones"] = aclaraciones
            from . import ia

            registrar(db, ACTOR, "prefiltro_pregunta_reformulada", "postulacion", p.codigo, {"criterio": pendiente["criterio"], "intento": n})
            return await responder(ia.reformular_pregunta(pendiente["pregunta"], pendiente["criterio"], pendiente.get("opciones")),
                                   aclaracion=True)
    unidad = dict(e.get("unidad_pendiente") or {})
    if pendiente is not None and pendiente["id"] in unidad:
        # respuesta a «¿8 años o 8 meses?»: se completa el dato con la unidad elegida
        t = _norm(texto)
        es_mes = bool(re.search(r"\bmes", t))
        if es_mes or re.search(r"\bano", t):
            texto = f"{unidad.pop(pendiente['id'])} {'meses' if es_mes else 'años'}"
            e["unidad_pendiente"] = unidad
    elif pendiente is not None and pendiente.get("reconfirma") and numero_sin_unidad(pendiente, texto):
        n_txt = numero_sin_unidad(pendiente, texto)
        unidad[pendiente["id"]] = n_txt
        e["unidad_pendiente"] = unidad
        return await responder(f"Para no equivocarme: ¿son {n_txt} años o {n_txt} meses?", aclaracion=True)
    if pendiente is not None:
        valor = clasificar_dato(pendiente, texto) if pendiente.get("reconfirma") else clasificar(pendiente, texto)
        if valor is None and (pendiente["indispensable"] or pendiente.get("reconfirma")):
            n = aclaraciones.get(pendiente["id"], 0) + 1
            if n > MAX_ACLARACIONES:
                e["respuestas"] = {**(e.get("respuestas") or {}),
                                   pendiente["id"]: {"respuesta": texto.strip()[:300], "valor": None, "fuente": canal_registro(canal), "en": _ahora()}}
                _resolver(db, p, criterios, e, "revision", f"Respuesta no concluyente en: {pendiente['criterio']}")
                return await responder(f"Gracias, {nombre_ficha(p).split(' ')[0]}. Una persona del equipo de RH revisará tu "
                                       "respuesta y te escribirá por este medio. 🙌")
            aclaraciones[pendiente["id"]] = n
            e["aclaraciones"] = aclaraciones
            registrar(db, ACTOR, "prefiltro_aclaracion_solicitada", "postulacion", p.codigo,
                      {"criterio": pendiente["criterio"], "respuesta": texto[:200], "intento": n})
            pide = ("Dime el tiempo exacto, por ejemplo «3 años» o «8 meses»." if pendiente.get("tipo") == "numero"
                    else "Elige una de las opciones, por favor." if pendiente.get("opciones") else "Respóndeme *Sí* o *No*, por favor.")
            return await responder(f"Para no equivocarme: {pendiente['chat']}\n\n{pide} 🙂", aclaracion=True)
        e["respuestas"] = {**(e.get("respuestas") or {}),
                           pendiente["id"]: {"respuesta": texto.strip()[:300], "valor": valor, "fuente": canal_registro(canal), "en": _ahora()}}
        e["pendiente"] = None

    resultado, incumple, siguiente = _evaluar(criterios, e)
    if resultado == "no_cumple":
        mensaje = cerrar_no_aprobado(db, p, criterios, e, incumple)
        return await responder(mensaje, clasificacion_final="no_cumple")
    if resultado == "inconsistencia":
        registrar_inconsistencia(db, p, criterios, e, incumple)
        return await responder(f"Gracias, {nombre_ficha(p).split(' ')[0]}. Una persona del equipo de RH revisará tus respuestas y "
                               "te escribirá por este medio. 🙌", clasificacion_final="revision")
    if siguiente is not None:
        primera = not e.get("preguntadas")
        e["pendiente"] = siguiente["id"]
        e["preguntadas"] = list(e.get("preguntadas") or []) + [siguiente["id"]]
        if primera:
            resp = e.get("respuestas") or {}
            faltan = sum(1 for c in criterios if c["id"] not in resp and (not c.get("reconfirma") or (
                (resp.get(c["reconfirma"]) or {}).get("valor") == "si" and (resp.get(c["reconfirma"]) or {}).get("fuente") == "web")))
            web = any((r or {}).get("fuente") == "web" for r in (e.get("respuestas") or {}).values())
            intro = (f"Ya tengo las respuestas de tu formulario; solo me falta confirmar {faltan} dato{'s' if faltan != 1 else ''}."
                     if web else f"Para continuar con tu postulación a *{vacante}* te haré {faltan} "
                                 f"{'preguntas breves' if faltan != 1 else 'pregunta breve'}, una a la vez.")
            return await responder(f"{intro}\n\n{siguiente['chat']}")
        return await responder(f"Gracias 🙌\n\n{siguiente['chat']}")

    # Cumple: se aprueba al instante y la ruta sigue sola (la liga de la entrevista sale por el canal conectado)
    _resolver(db, p, criterios, e, "cumple", "Cumple los requisitos indispensables de la vacante")
    _actualizar_ultima_actividad(p)
    db.commit()
    from . import proceso as sproc

    antes = {m.id for m in p.mensajes or [] if m.rol == "assistant"}
    await sproc.avanzar_seguro(db, p)
    db.refresh(p)
    ultimo = next((m for m in reversed(p.mensajes) if m.rol == "assistant"), None)
    if ultimo is not None and ultimo.id not in antes:
        # la ruta ya mandó el siguiente paso (Entrevista por WhatsApp en este chat, liga o propuesta de cita)
        return {"respuesta": ultimo.texto, "clasificacion": {"estado": "cumple", "evidencia": ""}, "ia": False,
                "whatsapp": {"enviado": bool(ultimo.enviado)}, "prefiltro": "cumple"}
    # retro 2026-10-09: sin valoraciones ni promesas de avance («Tu perfil es compatible» está prohibido)
    mensaje = (f"Gracias, {nombre_ficha(p).split(' ')[0]}. Registré tus respuestas; una persona del equipo de RH te escribirá "
               "por este medio. 🙌")
    return await responder(mensaje, clasificacion={"estado": "cumple", "evidencia": ""})
