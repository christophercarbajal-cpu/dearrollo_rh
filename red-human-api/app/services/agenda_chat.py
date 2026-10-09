"""Agenda de la Entrevista Red Human con avatar por chat (retro 2026-10-09). Determinista, sin depender del modelo:

  1. El bot PROPONE una fecha y hora exactas («¿Te confirmo hoy a las 4:00 p.m. (hora de Ciudad de México)?»).
  2. Si el candidato propone otro horario, el bot lo repite como propuesta y vuelve a pedir confirmación.
  3. La cita se registra ÚNICAMENTE con un «sí» explícito del candidato a la propuesta vigente. «Ok», «va» o «puede
     ser» no cuentan: el bot pide el «Sí».

Solo se usa cuando la ruta trae la Entrevista Red Human con avatar (`proceso.mueve_entrevista_ia`); en Masivos la
entrevista sigue por WhatsApp en el mismo chat y no se agenda nada. La propuesta vigente vive en
`Postulacion.analisis["agenda_cita"]`."""

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm.attributes import flag_modified

from ..fechas import ETIQUETA_ZONA, TZ_ORG
from ..models import Postulacion

CLAVE = "agenda_cita"
HORA_INICIO, HORA_FIN = 9, 18  # ventana en la que el bot propone (hora de la organización)
_DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
_DIAS_TEXTO = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre",
          "diciembre"]


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[¿?¡!,;]", " ", t)).strip()


def _ahora() -> datetime:
    return datetime.now(TZ_ORG)


def proponer(ahora: Optional[datetime] = None) -> datetime:
    """Primer horario disponible: la siguiente hora en punto con al menos 1 h de margen, en día hábil y dentro de la
    ventana; si ya no cabe hoy, el siguiente día hábil a las 10:00."""
    ahora = (ahora or _ahora()).astimezone(TZ_ORG)
    dt = (ahora + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    if dt.weekday() < 5 and HORA_INICIO <= dt.hour <= HORA_FIN - 1:
        return dt
    dia = ahora.date() + timedelta(days=1)
    while dia.weekday() >= 5:
        dia += timedelta(days=1)
    return datetime(dia.year, dia.month, dia.day, 10, 0, tzinfo=TZ_ORG)


def texto_horario(dt: datetime, ahora: Optional[datetime] = None) -> str:
    """«hoy a las 4:00 p.m.», «mañana a las 10:00 a.m.», «el lunes 12 de octubre a las 10:00 a.m.»."""
    ahora = (ahora or _ahora()).astimezone(TZ_ORG)
    dt = dt.astimezone(TZ_ORG)
    dias = (dt.date() - ahora.date()).days
    cuando = ("hoy" if dias == 0 else "mañana" if dias == 1 else
              f"el {_DIAS_TEXTO[dt.weekday()]} {dt.day} de {_MESES[dt.month - 1]}")
    h12 = dt.hour % 12 or 12
    return f"{cuando} a las {h12}:{dt.minute:02d} {'a.m.' if dt.hour < 12 else 'p.m.'}"


def pregunta_confirmacion(dt: datetime, ahora: Optional[datetime] = None) -> str:
    return f"¿Te confirmo {texto_horario(dt, ahora)} ({ETIQUETA_ZONA})?"


_RE_HORA = re.compile(r"(?:a las |a la |las |la )?\b(\d{1,2})(?::(\d{2}))?\s*(a ?\.? ?m\.?|p ?\.? ?m\.?|hrs?\b|horas\b|h\b|"
                      r"de la manana|de la tarde|de la noche|del mediodia)?")
_RE_SI = re.compile(r"\b(si|sip|confirmo|confirmado|confirmada|confirmamos)\b")
_RE_NO = re.compile(r"^(no|nop|nel|mejor no|tampoco|ese no|esa no)\b|\bno puedo\b|\bno me queda\b|\botro (dia|horario)\b")


def extraer_horario(texto: str, ahora: Optional[datetime] = None) -> Optional[datetime]:
    """Fecha/hora que propone el candidato («mañana a las 5», «el jueves 10 am», «a las 4:30 pm»). None si no trae una
    hora o un día reconocible. Nunca registra nada: solo propone."""
    ahora = (ahora or _ahora()).astimezone(TZ_ORG)
    t = _norm(texto)
    dia = None
    if "pasado manana" in t:
        dia = ahora.date() + timedelta(days=2)
    elif re.search(r"\bmanana\b", t.replace("de la manana", "")):  # «mañana» (día), no «de la mañana» (hora)
        dia = ahora.date() + timedelta(days=1)
    elif re.search(r"\bhoy\b", t):
        dia = ahora.date()
    else:
        for i, nombre in enumerate(_DIAS):
            if re.search(rf"\b{nombre}\b", t):
                delta = (i - ahora.weekday()) % 7 or 7
                dia = ahora.date() + timedelta(days=delta)
                break
    hora = minuto = None
    for m in _RE_HORA.finditer(t):
        h, mi, marca = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(" ", "").replace(".", "")
        prefijo = m.group(0).startswith(("a las", "a la", "las ", "la "))
        if not (marca or prefijo or m.group(2)):
            continue  # un número suelto («8») no es una hora
        if h > 23 or mi > 59:
            continue
        if marca in ("pm", "delatarde", "delanoche") and h < 12:
            h += 12
        elif marca in ("am", "delamanana") and h == 12:
            h = 0
        elif not marca and 1 <= h <= 7:
            h += 12  # «a las 4» en horario laboral = 4 p.m.
        hora, minuto = h, mi
        break
    if dia is None and hora is None:
        return None
    if hora is None:
        hora, minuto = 10, 0
    if dia is None:
        dia = ahora.date()
        if datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=TZ_ORG) <= ahora + timedelta(minutes=30):
            dia += timedelta(days=1)
    dt = datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=TZ_ORG)
    return dt if dt > ahora else None


def es_si_explicito(texto: str) -> bool:
    t = _norm(texto)
    return bool(_RE_SI.search(t)) and not _RE_NO.search(t)


def es_no(texto: str) -> bool:
    return bool(_RE_NO.search(_norm(texto)))


def propuesta(p: Postulacion) -> Optional[datetime]:
    valor = ((p.analisis or {}).get(CLAVE) or {}).get("propuesta")
    try:
        return datetime.fromisoformat(valor) if valor else None
    except (TypeError, ValueError):
        return None


def fijar_propuesta(p: Postulacion, dt: Optional[datetime]) -> None:
    a = dict(p.analisis or {})
    if dt is None:
        a.pop(CLAVE, None)
    else:
        a[CLAVE] = {"propuesta": dt.astimezone(timezone.utc).isoformat(), "en": datetime.now(timezone.utc).isoformat()}
    p.analisis = a
    flag_modified(p, "analisis")


def invitacion(p: Postulacion) -> str:
    """Primer mensaje tras el prefiltro (ruta con Entrevista con avatar): propone un horario exacto. Sin valoraciones."""
    dt = proponer()
    fijar_propuesta(p, dt)
    nombre = (p.nombre or "").split(" ")[0]
    saludo = f"Gracias, {nombre}. " if nombre and not nombre.startswith("Candidato") else "Gracias. "
    return (f"{saludo}El siguiente paso es una entrevista por videollamada con Red Human (unos 10 minutos). "
            f"{pregunta_confirmacion(dt)}")


def decidir(p: Postulacion, texto: str) -> dict:
    """Qué hacer con la respuesta del candidato. Regresa {"accion": "registrar", "cuando": dt} | {"accion": "responder",
    "texto": str}. Solo «registrar» si hay propuesta vigente y el candidato dijo «sí» de forma explícita."""
    vigente = propuesta(p)
    nuevo = extraer_horario(texto)
    if vigente is not None and es_si_explicito(texto) and (nuevo is None or nuevo == vigente):
        return {"accion": "registrar", "cuando": vigente}
    if nuevo is not None:
        fijar_propuesta(p, nuevo)
        return {"accion": "responder", "texto": f"Va. {pregunta_confirmacion(nuevo)} Respóndeme «Sí» para agendarla."}
    if es_no(texto):
        fijar_propuesta(p, None)
        return {"accion": "responder", "texto": "Sin problema. ¿Qué día y a qué hora te queda mejor? Por ejemplo: «mañana a las 11 a.m.»."}
    if vigente is None or vigente <= _ahora():
        dt = proponer()
        fijar_propuesta(p, dt)
        return {"accion": "responder", "texto": f"Te propongo un horario para tu entrevista con Red Human. {pregunta_confirmacion(dt)}"}
    return {"accion": "responder",
            "texto": f"Para agendarla necesito tu confirmación: {pregunta_confirmacion(vigente)} Respóndeme «Sí», o dime otro día y hora."}
