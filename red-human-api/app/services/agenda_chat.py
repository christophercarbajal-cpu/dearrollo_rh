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


# Cambio 24 (2026-10-12): la Entrevista Red Human con avatar ya no se agenda a una hora — el bot pregunta «¿La quieres
# hacer ahora?»: «sí» → manda la liga; «no» → manda la misma liga con 3 días para hacerla.
PREGUNTA_AHORA = "¿La quieres hacer ahora?"
DIAS_PLAZO = 3
_RE_SI_AHORA = re.compile(r"^(si|sip|simon|claro|va|vale|dale|ok|okay|ahorita|ahora|de una|listo|listos|sale|por supuesto|"
                          r"orale|perfecto|ya)\b|\b(ahora|ahorita|de una vez|en este momento|ya mismo)\b")
_RE_NO_AHORA = re.compile(r"^(no|nop|nel|mejor no|ahorita no|ahora no|todavia no|aun no|despues|luego|mas tarde|otro dia|"
                          r"manana)\b|\b(no puedo|ahorita no|ahora no|mas tarde|en otro momento|despues|luego)\b")


def invitacion(p: Postulacion) -> str:
    """Primer mensaje tras el prefiltro (ruta con Entrevista con avatar). Sin valoraciones ni promesas."""
    a = dict(p.analisis or {})
    a[CLAVE] = {"pregunta_ahora": True, "en": datetime.now(timezone.utc).isoformat()}
    p.analisis = a
    flag_modified(p, "analisis")
    nombre = (p.nombre or "").split(" ")[0]
    saludo = f"Gracias, {nombre}. " if nombre and not nombre.startswith("Candidato") else "Gracias. "
    return f"{saludo}El siguiente paso es una entrevista por videollamada con Red Human (unos 10 minutos). {PREGUNTA_AHORA}"


MINUTOS_VIDEO = 5


def invitacion_video(p: Postulacion) -> str:
    """2026-10-15 (Fletes «Entrevista en video»): la ruta va de la solicitud web DIRECTO a la Entrevista con avatar — el
    bot abre el chat por WhatsApp: «Hola, [nombre]. Soy Red Human, el agente virtual de [empresa]. El siguiente paso de tu
    postulación a [puesto] es una entrevista en video de 5 minutos. ¿La quieres hacer ahora?» («sí» → liga; «no» → la
    misma liga con 3 días). Deja vigente la pregunta y la presentación hecha."""
    from .ia import presentacion_bot
    from .prefiltro_conversacional import empresa_de, marcar_presentado, ya_presentado

    a = dict(p.analisis or {})
    a[CLAVE] = {"pregunta_ahora": True, "video": True, "en": datetime.now(timezone.utc).isoformat()}
    p.analisis = a
    flag_modified(p, "analisis")
    nombre = (p.nombre or "").split(" ")[0]
    nombre = "" if not nombre or nombre.startswith("Candidato") else nombre
    presentacion = "" if ya_presentado(p) else f" {presentacion_bot(empresa_de(p))}"
    marcar_presentado(p)
    puesto = p.vacante.titulo if p.vacante else "la vacante"
    return (f"Hola{', ' + nombre if nombre else ''}.{presentacion} El siguiente paso de tu postulación a {puesto} es una entrevista "
            f"en video de {MINUTOS_VIDEO} minutos. {PREGUNTA_AHORA}")


def parametros_video(p: Postulacion) -> list:
    from ..config import settings
    from .prefiltro_conversacional import empresa_de

    nombre = (p.nombre or "").split(" ")[0]
    valores = {"nombre": "" if nombre.startswith("Candidato") else nombre, "empresa": empresa_de(p) or "Red Human",
               "puesto": (p.vacante.titulo if p.vacante else "") or "la vacante"}
    claves = [k.strip().lower() for k in (settings.meta_plantilla_video_params or "").split(",") if k.strip()]
    return [valores[k] or "-" for k in claves if k in valores]


def esperando_ahora(p: Postulacion) -> bool:
    return bool(((p.analisis or {}).get(CLAVE) or {}).get("pregunta_ahora"))


def decidir_ahora(texto: str) -> dict:
    """Respuesta a «¿La quieres hacer ahora?»: {"accion": "ahora"} | {"accion": "plazo"} | {"accion": "responder"}."""
    t = _norm(texto)
    if _RE_NO_AHORA.search(t):
        return {"accion": "plazo", "dias": DIAS_PLAZO}
    if _RE_SI_AHORA.search(t):
        return {"accion": "ahora"}
    return {"accion": "responder", "texto": f"{PREGUNTA_AHORA} Respóndeme «sí» y te mando la liga, o «no» y te doy {DIAS_PLAZO} días para hacerla."}


def texto_liga(nombre: str, liga: str, ahora: bool, limite: Optional[datetime] = None) -> str:
    quien = f", {nombre}" if nombre and not nombre.startswith("Candidato") else ""
    if ahora:
        return f"Listo{quien}. Entra desde esta liga para hacer tu entrevista con Red Human:\n\n{liga}"
    hasta = f", hasta {texto_horario(limite)} ({ETIQUETA_ZONA})" if limite else ""
    return (f"Sin problema{quien}. Tienes {DIAS_PLAZO} días para hacerla{hasta}. Entra cuando puedas desde esta liga:\n\n{liga}")


# ============================================================ Agendamiento inteligente (especificación 2026-10-17)
# Al pasar a la Entrevista Red Human con avatar el bot ya NO manda la liga de inmediato: invita y pregunta cuándo. «Ahora»
# → la liga; una fecha/hora → queda agendada y la liga va EN LA MISMA conversación; algo vago → pide día y hora UNA vez y,
# si sigue vago, manda la liga («entra cuando puedas»). Sin recordatorios ni cupos (2026-10-17, «Cambios WhatsApp»).
PREGUNTA_CUANDO = "¿Cuándo te queda bien? Si quieres, puede ser ahora mismo."
TEXTO_INVITACION_AGENDA = f"¡Pasaste a la siguiente etapa! Es una entrevista por videollamada de unos 10 minutos. {PREGUNTA_CUANDO}"
CLAVE_AGENDADA = "entrevista_agendada"
_RE_AHORA_MISMO = re.compile(r"\b(ahora mismo|ahorita mismo|ahora|ahorita|de una vez|en este momento|ya mismo|de inmediato|"
                             r"inmediatamente|enseguida|en seguida|ya)\b")
_RE_AHORA_NO = re.compile(r"\b(ahora no|ahorita no|ya no|no puedo ahora|no ahorita|ahora mismo no|ahorita estoy|ahora estoy)\b")
_RE_DIA_MES = re.compile(r"\b(?:el |dia |el dia )?(\d{1,2})(?: de (" + "|".join(_MESES) + r"))?\b")


def invitacion_agenda(p: Postulacion) -> str:
    """Primer mensaje de la etapa (sin liga): «¡Pasaste a la siguiente etapa! … ¿Cuándo te queda bien? Si quieres, puede ser
    ahora mismo.». Deja vigente la pregunta en `analisis["agenda_cita"]["cuando"]`."""
    a = dict(p.analisis or {})
    a[CLAVE] = {"cuando": True, "en": datetime.now(timezone.utc).isoformat()}
    p.analisis = a
    flag_modified(p, "analisis")
    return texto_invitacion_agenda((p.nombre or "").split(" ")[0])


def texto_invitacion_agenda(nombre: str = "") -> str:
    """2026-10-17: «¡Pasaste a la siguiente etapa, [nombre]! Es una entrevista por videollamada de unos 10 minutos. ¿Cuándo
    te queda bien? Si quieres, puede ser ahora mismo.» (sin nombre usable, sin él)."""
    nombre = "" if not nombre or nombre.startswith("Candidato") else nombre
    if not nombre:
        return TEXTO_INVITACION_AGENDA
    return TEXTO_INVITACION_AGENDA.replace("¡Pasaste a la siguiente etapa!", f"¡Pasaste a la siguiente etapa, {nombre}!")


def esperando_cuando(p: Postulacion) -> bool:
    return bool(((p.analisis or {}).get(CLAVE) or {}).get("cuando"))


def _dia_del_mes(t: str, ahora: datetime):
    """«el martes 20», «el 20», «20 de octubre» → la fecha (el mes actual o el siguiente si ese día ya pasó)."""
    for m in _RE_DIA_MES.finditer(t):
        n = int(m.group(1))
        antes = t[:m.start()].rstrip()
        despues = t[m.end():].lstrip()
        if not 1 <= n <= 31:
            continue
        # un número que es la HORA («a las 6», «6 pm», «6:30») no es el día
        if re.search(r"(a las|a la|las|la)$", antes) or re.match(r"(:|am\b|pm\b|a\.?m|p\.?m|hrs?\b|horas\b|de la (manana|tarde|noche))", despues):
            continue
        dia_semana = any(antes.endswith(d) for d in _DIAS)
        if not (m.group(2) or dia_semana or m.group(0).startswith(("el ", "dia "))):
            continue
        mes = _MESES.index(m.group(2)) + 1 if m.group(2) else ahora.month
        anio = ahora.year
        try:
            fecha = datetime(anio, mes, n, tzinfo=TZ_ORG).date()
        except ValueError:
            continue
        if fecha < ahora.date():
            if m.group(2):
                fecha = fecha.replace(year=anio + 1)
            else:
                mes2, anio2 = (1, anio + 1) if mes == 12 else (mes + 1, anio)
                try:
                    fecha = datetime(anio2, mes2, n, tzinfo=TZ_ORG).date()
                except ValueError:
                    continue
        return fecha
    return None


def partes_horario(texto: str, ahora: Optional[datetime] = None) -> dict:
    """{"dia": date | None, "hora": (h, m) | None} de lo que escribió el candidato (sin inventar la parte que falta)."""
    ahora = (ahora or _ahora()).astimezone(TZ_ORG)
    t = _norm(texto)
    dia = _dia_del_mes(t, ahora)
    if dia is None:
        if "pasado manana" in t:
            dia = ahora.date() + timedelta(days=2)
        elif re.search(r"\bmanana\b", t.replace("de la manana", "")):
            dia = ahora.date() + timedelta(days=1)
        elif re.search(r"\bhoy\b", t):
            dia = ahora.date()
        else:
            for i, nombre in enumerate(_DIAS):
                if re.search(rf"\b{nombre}\b", t):
                    dia = ahora.date() + timedelta(days=(i - ahora.weekday()) % 7 or 7)
                    break
    hora = None
    for m in _RE_HORA.finditer(t):
        h, mi, marca = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(" ", "").replace(".", "")
        prefijo = m.group(0).startswith(("a las", "a la", "las ", "la "))
        if not (marca or prefijo or m.group(2)) or h > 23 or mi > 59:
            continue
        if marca in ("pm", "delatarde", "delanoche") and h < 12:
            h += 12
        elif marca in ("am", "delamanana") and h == 12:
            h = 0
        elif not marca and 1 <= h <= 7:
            h += 12
        hora = (h, mi)
        break
    return {"dia": dia, "hora": hora}


def texto_cita(dt: datetime, ahora: Optional[datetime] = None) -> str:
    """«hoy a las 6:00 p.m.», «mañana a las 10:00 a.m.», «el martes 20 a las 6:00 p.m.»."""
    ahora = (ahora or _ahora()).astimezone(TZ_ORG)
    dt = dt.astimezone(TZ_ORG)
    dias = (dt.date() - ahora.date()).days
    cuando = "hoy" if dias == 0 else "mañana" if dias == 1 else f"el {_DIAS_TEXTO[dt.weekday()]} {dt.day}"
    h12 = dt.hour % 12 or 12
    return f"{cuando} a las {h12}:{dt.minute:02d} {'a.m.' if dt.hour < 12 else 'p.m.'}"


def texto_estado_agendada(dt: datetime) -> str:
    """Estado del pipeline: «Entrevista agendada · martes 20 6:00 p.m.»."""
    dt = dt.astimezone(TZ_ORG)
    h12 = dt.hour % 12 or 12
    return f"Entrevista agendada · {_DIAS_TEXTO[dt.weekday()]} {dt.day} {h12}:{dt.minute:02d} {'a.m.' if dt.hour < 12 else 'p.m.'}"


def _con_dia_pendiente(p: Postulacion, partes: dict) -> dict:
    """Si antes dijo solo el día («el jueves») y ahora solo la hora («a las 5»), se juntan."""
    previo = ((p.analisis or {}).get(CLAVE) or {}).get("dia")
    if partes["dia"] is None and partes["hora"] is not None and previo:
        try:
            return {**partes, "dia": datetime.fromisoformat(previo).date()}
        except (TypeError, ValueError):
            pass
    return partes


def _guardar_dia(p: Postulacion, dia) -> None:
    a = dict(p.analisis or {})
    estado = dict(a.get(CLAVE) or {})
    estado["dia"] = dia.isoformat() if dia else None
    a[CLAVE] = estado
    p.analisis = a
    flag_modified(p, "analisis")


def interpretar_cuando(p: Postulacion, texto: str, ahora: Optional[datetime] = None) -> dict:
    """Respuesta a «¿Cuándo te queda bien?»: {"accion": "ahora"} | {"accion": "registrar", "cuando": dt} | {"accion":
    "responder", "texto": str}. Primero la lectura determinista; si no se entiende, el modelo (`ia.interpretar_agenda`)."""
    ahora = (ahora or _ahora()).astimezone(TZ_ORG)
    t = _norm(texto)
    partes = _con_dia_pendiente(p, partes_horario(texto, ahora))
    if _RE_AHORA_MISMO.search(t) and not _RE_AHORA_NO.search(t) and partes["dia"] is None and partes["hora"] is None:
        return {"accion": "ahora"}
    if partes["dia"] is None and partes["hora"] is None:
        from . import ia

        lectura = ia.interpretar_agenda(texto, ahora)
        if lectura.get("accion") == "ahora":
            return {"accion": "ahora"}
        if lectura.get("cuando"):
            partes = {"dia": lectura["cuando"].date(), "hora": (lectura["cuando"].hour, lectura["cuando"].minute)}
    if partes["hora"] is not None:
        dia = partes["dia"] or ahora.date()
        dt = datetime(dia.year, dia.month, dia.day, partes["hora"][0], partes["hora"][1], tzinfo=TZ_ORG)
        if partes["dia"] is None and dt <= ahora + timedelta(minutes=10):
            dt += timedelta(days=1)  # «a las 9» cuando ya pasaron las 9 → mañana
        if dt <= ahora:
            return {"accion": "responder", "texto": "Esa hora ya pasó. ¿Qué otro día y hora te queda bien? Si quieres, puede ser ahora mismo."}
        return {"accion": "registrar", "cuando": dt}
    # 2026-10-17: lo vago se aclara UNA sola vez; si sigue vago, se manda la liga («entra cuando puedas»)
    if ((p.analisis or {}).get(CLAVE) or {}).get("aclarado"):
        return {"accion": "liga_vaga"}
    _marcar_aclarado(p)
    if partes["dia"] is not None:
        _guardar_dia(p, partes["dia"])
        dia_dt = datetime(partes["dia"].year, partes["dia"].month, partes["dia"].day, tzinfo=TZ_ORG)
        cuando = texto_cita(dia_dt.replace(hour=12), ahora).split(" a las ")[0]
        return {"accion": "responder", "texto": f"Va. ¿A qué hora te queda bien {cuando}?"}
    return {"accion": "responder",
            "texto": "¿Qué día y a qué hora te queda bien? Por ejemplo: «mañana a las 11 a.m.». Si quieres, puede ser ahora mismo."}


def _marcar_aclarado(p: Postulacion) -> None:
    a = dict(p.analisis or {})
    a[CLAVE] = {**(a.get(CLAVE) or {}), "aclarado": True}
    p.analisis = a
    flag_modified(p, "analisis")


def texto_liga_vaga(liga: str) -> str:
    """2026-10-17: siguió sin decir día y hora tras la aclaración → la liga para entrar cuando pueda."""
    return f"Aquí está tu link; entra cuando puedas: {liga}"


def texto_liga_ahora(liga: str) -> str:
    return f"Aquí está tu link para entrar ahora mismo: {liga}"


def marcar_link_enviado(p: Postulacion, token: str) -> None:
    """Pipeline «Link enviado» (ahora mismo o tras una respuesta vaga). Sin recordatorios."""
    a = dict(p.analisis or {})
    a.pop(CLAVE, None)
    a[CLAVE_AGENDADA] = {"link_enviado": True, "entrevista": token}
    p.analisis = a
    flag_modified(p, "analisis")


def link_enviado(p: Postulacion) -> bool:
    return bool(((p.analisis or {}).get(CLAVE_AGENDADA) or {}).get("link_enviado"))


def texto_agendada(nombre: str, dt: datetime, liga: str) -> str:
    """2026-10-17: «Listo, quedó el martes 20 a las 6:00 p.m. Aquí está tu link…» — la liga va EN ESTA conversación y no hay
    recordatorios."""
    return f"Listo, quedó {texto_cita(dt)} ({ETIQUETA_ZONA}). Aquí está tu link para entrar a esa hora: {liga}"


def marcar_agendada(p: Postulacion, dt: datetime, token: str) -> None:
    a = dict(p.analisis or {})
    a.pop(CLAVE, None)
    a[CLAVE_AGENDADA] = {"cuando": dt.astimezone(timezone.utc).isoformat(), "entrevista": token}
    p.analisis = a
    flag_modified(p, "analisis")


def agendada(p: Postulacion) -> Optional[datetime]:
    valor = ((p.analisis or {}).get(CLAVE_AGENDADA) or {}).get("cuando")
    try:
        return datetime.fromisoformat(valor) if valor else None
    except (TypeError, ValueError):
        return None


def decidir(p: Postulacion, texto: str) -> dict:
    """Qué hacer con la respuesta del candidato. Regresa {"accion": "registrar", "cuando": dt} | {"accion": "ahora" |
    "plazo"} | {"accion": "responder", "texto": str}. Con la pregunta «¿La quieres hacer ahora?» vigente decide
    `decidir_ahora`; las propuestas de horario anteriores al Cambio 24 siguen funcionando.
    2026-10-17: con «¿Cuándo te queda bien?» vigente decide `interpretar_cuando` (la cita queda con lo que diga, sin «Sí»)."""
    estado = (p.analisis or {}).get(CLAVE) or {}
    if estado.get("cuando"):
        d = interpretar_cuando(p, texto)
        return {**d, "agenda_nueva": True}
    if estado.get("pregunta_ahora") and estado.get("liga_enviada"):
        # 2026-10-16: la liga ya salió en el mensaje único del motor — «no puedo ahora» → la liga con 3 días; cualquier
        # otra cosa («ok, gracias», «sí») → se le recuerda la liga (nunca se vuelve a preguntar ni a proponer horario)
        d = decidir_ahora(texto)
        return d if d["accion"] == "plazo" else {"accion": "ahora"}
    if estado.get("pregunta_ahora"):
        return decidir_ahora(texto)
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
        invitacion(p)  # Cambio 24: sin propuesta vigente se pregunta «¿La quieres hacer ahora?»
        return {"accion": "responder", "texto": f"Tu siguiente paso es la entrevista con Red Human. {PREGUNTA_AHORA}"}
    return {"accion": "responder",
            "texto": f"Para agendarla necesito tu confirmación: {pregunta_confirmacion(vigente)} Respóndeme «Sí», o dime otro día y hora."}
