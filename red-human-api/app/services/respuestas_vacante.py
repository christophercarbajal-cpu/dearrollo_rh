"""Cambio 59 (2026-10-15): el agente contesta preguntas sobre la vacante y atiende el chat DESPUÉS del cierre.

Todo es lógica del agente (cero plantillas nuevas de Meta):

1. Pregunta sobre la vacante («¿En dónde es?», «¿Cuánto pagan?», «¿Qué horario?»): el DATO exacto de la vacante en UNA
   línea («El trabajo es en [ubicación].», «El sueldo es de [sueldo].»). Si la vacante no tiene ese dato: «Eso lo puedes
   platicar con el equipo de RH de [empresa].» (Cambio 51). Una pregunta del candidato NUNCA se evalúa como respuesta:
   durante una actividad (prefiltro, entrevista fija) se contesta y se repite la pregunta pendiente (`con_pendiente`).
2. Después del cierre (la actividad terminó y se mandó su mensaje de cierre: `marcar_cierre`) el agente queda PASIVO, ya
   no pregunta nada (`turno_post_cierre`):
     «Gracias» / «Ok» → «Con gusto, [nombre].» una sola vez por chat; después, silencio.
     Pregunta de la vacante → el dato, sin preguntar nada después.
     «¿Cuándo me contactan?» / «¿Qué sigue?» → la siguiente actividad de la ruta, sin prometer fechas.
     «¿Cómo me fue?» / «¿Pasé?» → «Tus respuestas ya están con el equipo de RH de [empresa]. Ellos te darán el siguiente
     paso.» (nunca resultados ni motivos).
     Cualquier otra cosa → «Si tienes alguna duda sobre la vacante, con gusto te ayudo.»
   El mensaje de cierre nunca se vuelve a enviar; el salto a otra vacante sigue el Cambio 54 (webhook).
"""

import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm.attributes import flag_modified

from ..models import Postulacion

CLAVE = "post_cierre"
TEXTO_COMO_ME_FUE = "Tus respuestas ya están con el equipo de RH{de}. Ellos te darán el siguiente paso."
TEXTO_OTRO = "Si tienes alguna duda sobre la vacante, con gusto te ayudo."


def _n(t: str) -> str:
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[¿?¡!.,;:()\"']", " ", t).split())


# ============================================================ 1. preguntas sobre la vacante

_INTERROGATIVO = re.compile(r"^(y )?(en )?(donde|adonde|cuanto|cuanta|cuantos|cuantas|cual|cuales|que|a que|de que|como|hay|tienen|"
                            r"dan|es|son|seria|pagan|me pueden decir|me puedes decir|me dices|quisiera saber|una pregunta)\b")
TEMAS = (
    ("ubicacion", re.compile(r"\b(donde (es|esta|queda|seria|se ubica|trabajaria|seria el trabajo)|en donde|ubicacion|direccion|"
                             r"que zona|por donde (es|queda)|en que (parte|lugar|colonia|ciudad|municipio|zona|estado)|"
                             r"(es|queda) (lejos|cerca))\b")),
    ("sueldo", re.compile(r"\b(cuanto (pagan|se paga|es el sueldo|es la paga|ganaria|gano|ganaria yo|pagarian|dan|ofrecen|es el pago)|"
                          r"sueldo|salario|cuanto es (al mes|a la semana|a la quincena)|de cuanto es|la paga|el pago)\b")),
    ("horario", re.compile(r"\b(horario|a que hora|que hora|turno|turnos|cuantas horas|jornada|que dias (se trabaja|trabajaria|son)|"
                           r"de que hora)\b")),
    ("modalidad", re.compile(r"\b(home ?office|remoto|desde casa|presencial|hibrido|modalidad)\b")),
    ("prestaciones", re.compile(r"\b(prestacion\w*|beneficio\w*|imss|seguro social|seguro medico|vales|aguinaldo|vacaciones)\b")),
    ("actividades", re.compile(r"\b(de que (es|trata|se trata) (el )?(trabajo|puesto|vacante|empleo)|que (hay que hacer|haria|tendria que hacer|se hace)|"
                               r"cuales son las (actividades|funciones|tareas)|que actividades|que funciones)\b")),
    ("empresa", re.compile(r"\b(que empresa|cual es la empresa|para que empresa|para quien (es|seria)|como se llama la empresa)\b")),
)


def tema_pregunta(texto: str) -> Optional[str]:
    """Tema de la vacante por el que PREGUNTA el candidato (None si no es una pregunta sobre la vacante). Una respuesta
    («en la colonia Centro», «una hora») nunca cuenta: tiene que ser pregunta («?») o empezar como pregunta."""
    crudo = (texto or "").strip()
    t = _n(crudo)
    if not t:
        return None
    es_pregunta = "?" in crudo or "¿" in crudo or bool(_INTERROGATIVO.match(t))
    if not es_pregunta or t in ("donde", "en donde", "adonde", "a donde", "cual", "que", "como"):
        # «¿En dónde?» a secas responde a la pregunta del bot («¿en qué empresa?»), no pregunta por la vacante
        return None
    for tema, rx in TEMAS:
        if rx.search(t):
            return tema
    return None


def _empresa(p: Postulacion) -> str:
    from .prefiltro_conversacional import empresa_de

    return empresa_de(p)


def texto_sin_dato(p: Postulacion) -> str:
    from .prefiltro_conversacional import texto_negociacion

    return texto_negociacion(p)  # «Eso lo puedes platicar con el equipo de RH de [empresa].»


def _lista(xs) -> str:
    xs = [str(x).strip().rstrip(".") for x in xs or [] if str(x or "").strip()]
    if not xs:
        return ""
    xs = [xs[0]] + [x[:1].lower() + x[1:] for x in xs[1:]]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " y " + xs[-1]


def respuesta(p: Postulacion, tema: str) -> str:
    """El dato EXACTO de la vacante en una línea; sin dato → «Eso lo puedes platicar con el equipo de RH de [empresa].»"""
    v = p.vacante
    if v is None:
        return texto_sin_dato(p)
    if tema == "ubicacion":
        from . import ubicaciones as subic

        u = None
        try:
            u = subic.ubicacion_de(None, p)
        except Exception:  # noqa: BLE001 — sin ubicaciones: el texto de la vacante
            u = None
        direccion = (getattr(u, "direccion", "") or "").strip() if u is not None else ""
        if u is not None and direccion and "demo" not in direccion.lower():
            return f"El trabajo es en {u.nombre}, {direccion.rstrip('.')}."
        lugar = (v.ubicacion or "").strip().rstrip(".")
        return f"El trabajo es en {lugar}." if lugar else texto_sin_dato(p)
    if tema == "sueldo":
        from .entrevista_whatsapp_fija import _texto_sueldo

        s = _texto_sueldo(v.sueldo or "")
        return f"El sueldo es {s}." if s else texto_sin_dato(p)
    if tema == "horario":
        h = str(getattr(v, "horario", "") or "").strip().rstrip(".")
        return f"El horario es {h[:1].lower() + h[1:]}." if h else texto_sin_dato(p)
    if tema == "modalidad":
        m = (v.modalidad or "").strip()
        return f"El trabajo es {m.lower()}." if m else texto_sin_dato(p)
    if tema == "prestaciones":
        b = _lista(v.beneficios)
        return f"Las prestaciones son: {b}." if b else texto_sin_dato(p)
    if tema == "actividades":
        r = _lista(list(v.responsabilidades or [])[:3])
        return f"Las actividades principales son: {r[:1].lower() + r[1:]}." if r else texto_sin_dato(p)
    if tema == "empresa":
        emp = _empresa(p)
        return f"La vacante es en {emp}." if emp else texto_sin_dato(p)
    return texto_sin_dato(p)


def con_pendiente(p: Postulacion, tema: str, pregunta_pendiente: str) -> str:
    """Durante una actividad: el dato y, en seguida, la MISMA pregunta pendiente (retoma el hilo)."""
    return f"{respuesta(p, tema)} {pregunta_pendiente}".strip()


# ============================================================ 2. después del cierre

_RE_QUE_SIGUE = re.compile(r"\b(cuando me (contactan|contactaran|llaman|llamaran|escriben|escribiran|avisan|avisaran|dicen|marcan)|"
                           r"que sigue|que procede|cual es el siguiente paso|siguiente paso|y ahora que|ahora que sigue|"
                           r"que tengo que hacer( ahora)?|cuando (empiezo|inicio|entro|me dicen)|hasta cuando|cuanto (tardan|se tardan|tiempo tardan)|"
                           r"me van a (llamar|contactar|escribir|avisar))\b")
_RE_COMO_ME_FUE = re.compile(r"\b(como me fue|pase|pasé|quede|me quede|aprobe|aprobé|me aceptaron|me van a contratar|me contrataron|"
                             r"como sali|sali bien|cual fue (el|mi) resultado|(mi|el) resultado|tengo el trabajo|me dieron el (trabajo|puesto)|"
                             r"fui seleccionad[oa])\b")
_RE_ACUSE = re.compile(r"^(ok|okay|oki|okis|va|sale|vale|listo|perfecto|entendido|enterado|de acuerdo|esta bien|bien|"
                       r"muy bien|excelente|genial)( (gracias|muchas gracias|mil gracias))?$")


def es_que_sigue(texto: str) -> bool:
    return bool(_RE_QUE_SIGUE.search(_n(texto)))


def es_como_me_fue(texto: str) -> bool:
    t = _n(texto)
    return bool(_RE_COMO_ME_FUE.search(t)) and ("?" in (texto or "") or "¿" in (texto or "") or len(t.split()) <= 5)


def es_acuse(texto: str) -> bool:
    """«Gracias», «Ok», «👍», «perfecto, gracias»: un acuse sin pregunta."""
    from ..routers.candidatos import es_agradecimiento

    t = _n(texto)
    return es_agradecimiento(texto) or bool(_RE_ACUSE.match(t))


def marcar_cierre(p: Postulacion, actividad: str, texto: str) -> None:
    """La actividad terminó y se mandó su mensaje de cierre: el chat queda PASIVO hasta que empiece otra actividad."""
    a = dict(p.analisis or {})
    previo = dict(a.get(CLAVE) or {})
    a[CLAVE] = {"en": datetime.now(timezone.utc).isoformat(), "actividad": actividad, "cierre": (texto or "")[:500],
                "con_gusto": bool(previo.get("con_gusto"))}
    p.analisis = a
    flag_modified(p, "analisis")


def en_post_cierre(p: Postulacion) -> bool:
    return bool((p.analisis or {}).get(CLAVE))


def _ya_dijo_con_gusto(p: Postulacion) -> bool:
    return bool(((p.analisis or {}).get(CLAVE) or {}).get("con_gusto"))


def _marcar_con_gusto(p: Postulacion) -> None:
    a = dict(p.analisis or {})
    a[CLAVE] = {**(a.get(CLAVE) or {}), "con_gusto": True}
    p.analisis = a
    flag_modified(p, "analisis")


_FRASE_ACTIVIDAD = {
    "entrevista_humana": "una entrevista con el equipo de RH{de}. Te escribirán por este medio para agendarla.",
    "medica": "una evaluación médica. El equipo de RH{de} te escribirá por este medio para agendarla.",
    "tecnica": "una evaluación práctica. El equipo de RH{de} te escribirá por este medio para agendarla.",
    "socioeconomica": "un estudio socioeconómico. El equipo de RH{de} te escribirá por este medio con los detalles.",
    "referencias": "la revisión de tus referencias laborales. El equipo de RH{de} te escribirá por este medio con los detalles.",
    "documentos": "enviar tus documentos. El equipo de RH{de} te escribirá por este medio con los detalles.",
    "psicometrica": "una evaluación psicométrica en línea. Te escribirán por este medio con la liga.",
    "entrevista_agente": "una entrevista en video con Red Human. Te escribirán por este medio con la liga.",
    "entrevista_whatsapp": "una entrevista por este medio con Red Human.",
    "oferta": "revisar la oferta de trabajo. El equipo de RH{de} te escribirá por este medio.",
    "carta_contrato": "la firma de tu contrato. El equipo de RH{de} te escribirá por este medio con los detalles.",
}


def texto_siguiente_paso(p: Postulacion) -> str:
    """«Tu siguiente paso es [actividad]…» — la siguiente actividad de la ruta, sin fechas ni promesas."""
    from . import proceso as sproc

    emp = _empresa(p)
    de = f" de {emp}" if emp else ""
    sig = None
    try:
        sig = sproc.siguiente_actividad(p)
    except Exception:  # noqa: BLE001 — sin ruta: respuesta general
        sig = None
    if sig is not None:
        paso = next((x for x in ((p.proceso or {}).get("pasos") or []) if x.get("id") == sig["id"]), {})
        frase = _FRASE_ACTIVIDAD.get(paso.get("tipo"))
        if frase:
            return f"Tu siguiente paso es {frase.format(de=de)}"
        nombre = (sig.get("nombre") or "").strip()
        if nombre:
            return f"Tu siguiente paso es: {nombre[:1].lower() + nombre[1:]}. El equipo de RH{de} te escribirá por este medio."
    return f"El equipo de RH{de} revisará tu información y te escribirá por este medio."


def respuesta_post_cierre(p: Postulacion, texto: str) -> Optional[str]:
    """El mensaje del agente en el chat pasivo; "" = silencio (ya se dijo «Con gusto»); None = no aplica."""
    tema = tema_pregunta(texto)
    if tema:
        return respuesta(p, tema)  # el dato, sin ninguna pregunta después
    if es_como_me_fue(texto):
        emp = _empresa(p)
        return TEXTO_COMO_ME_FUE.format(de=f" de {emp}" if emp else "")
    if es_que_sigue(texto):
        return texto_siguiente_paso(p)
    if es_acuse(texto):
        if _ya_dijo_con_gusto(p):
            return ""
        from ..routers.candidatos import nombre_ficha

        nombre = nombre_ficha(p) or ""
        nombre = "" if not nombre or nombre.startswith("Candidato") else nombre.split(" ")[0]
        return f"Con gusto{', ' + nombre if nombre else ''}."
    return TEXTO_OTRO


async def turno_post_cierre(db, p: Postulacion, texto: str, canal: str) -> dict:
    """Un turno del chat pasivo. Nunca repite el cierre ni el mismo mensaje dos veces seguidas."""
    from ..routers.candidatos import _actualizar_ultima_actividad, _enviar_whatsapp, _ultimo_del_bot, guardar_mensaje

    mensaje = respuesta_post_cierre(p, texto)
    cierre = ((p.analisis or {}).get(CLAVE) or {}).get("cierre") or ""
    if not mensaje or mensaje.strip() == _ultimo_del_bot(p).strip() or (cierre and mensaje.strip() == cierre.strip()):
        db.commit()
        return {"respuesta": "", "clasificacion": None, "ia": False, "silencio": True, "post_cierre": True,
                "whatsapp": {"enviado": False, "detalle": "Sin respuesta (chat pasivo después del cierre)"}}
    if mensaje.startswith("Con gusto"):
        _marcar_con_gusto(p)
    envio = await _enviar_whatsapp(p, mensaje, canal)
    guardar_mensaje(db, p, "assistant", mensaje, canal, envio)
    _actualizar_ultima_actividad(p)
    db.commit()
    return {"respuesta": mensaje, "clasificacion": None, "ia": False, "whatsapp": envio, "post_cierre": True,
            **({"con_gusto": True} if mensaje.startswith("Con gusto") else {})}
