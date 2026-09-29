"""
Plantillas HTML corporativas de correo (2026-09-18) — Entrevista Humana.

Dos plantillas, limpias y responsivas (tablas + CSS inline, compatibles con Gmail/Outlook/Apple Mail),
con el logotipo de Red Human y, si la Cuenta tiene logo, el de la empresa:
- `html_entrevistador`: aviso de nueva entrevista asignada + CTA «Ver expediente del candidato».
- `html_candidato`: confirmación de la entrevista con una persona + fecha, hora y liga de conexión.

Vista previa en el navegador con datos de prueba: GET /api/emails/preview/entrevistador y
GET /api/emails/preview/candidato (routers/emails_preview.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Optional

from ..config import settings

from .. import fechas
from ..fechas import TZ_ORG as TZ_MEXICO  # zona de la organización (app/fechas.py)
_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

ROJO = "#ee4444"
GRIS = "#58595b"
INK = "#1a1a1a"
INK2 = "#555555"
FONDO = "#f4f5f7"


def fecha_hora_mx(dt: Optional[datetime]) -> tuple[str, str]:
    """('jueves 24 de septiembre de 2026', '10:30 h') en hora de México; vacíos si no hay fecha."""
    if not dt:
        return "", ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(TZ_MEXICO)
    return f"{_DIAS[local.weekday()]} {local.day} de {_MESES[local.month - 1]} de {local.year}", fechas.con_zona(f"{local:%H:%M} h")


def _logo_html(empresa: str, logo_url: str = "") -> str:
    """Encabezado: wordmark Red Human (texto, se ve igual en todos los clientes de correo) y, si hay, el
    logotipo de la empresa que entrevista."""
    marca = (
        f'<span style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:26px;font-weight:600;letter-spacing:-0.5px;">'
        f'<span style="color:{GRIS};">Red</span><span style="color:{ROJO};">Human</span></span>'
        f'<div style="height:4px;width:96px;background:{ROJO};border-radius:2px;margin-top:4px;"></div>'
    )
    if logo_url:
        return (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td align="left" valign="middle">{marca}</td>'
            f'<td align="right" valign="middle"><img src="{escape(logo_url)}" alt="{escape(empresa)}" height="40" style="height:40px;max-width:160px;object-fit:contain;border:0;"></td>'
            f'</tr></table>'
        )
    return marca


def _boton(texto: str, url: str) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:28px auto 8px;"><tr>'
        f'<td align="center" bgcolor="{ROJO}" style="border-radius:12px;">'
        f'<a href="{escape(url)}" target="_blank" style="display:inline-block;padding:14px 28px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
        f'font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:12px;">{escape(texto)}</a>'
        f'</td></tr></table>'
    )


def _fila(etiqueta: str, valor: str, ultima: bool = False) -> str:
    borde = "" if ultima else "border-bottom:1px solid #eceef1;"
    return (
        f'<tr><td style="padding:10px 0;{borde}font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#8a8d91;width:38%;">{escape(etiqueta)}</td>'
        f'<td style="padding:10px 0;{borde}font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:15px;color:{INK};font-weight:600;">{valor}</td></tr>'
    )


def _base(titulo: str, preheader: str, empresa: str, logo_url: str, contenido: str, pie: str) -> str:
    """Esqueleto responsivo: contenedor de 600 px que se vuelve fluido en móvil."""
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<title>{escape(titulo)}</title>
<style>
  @media only screen and (max-width: 620px) {{
    .contenedor {{ width: 100% !important; }}
    .relleno {{ padding: 24px 20px !important; }}
    .titulo {{ font-size: 22px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:{FONDO};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{FONDO};">
<tr><td align="center" style="padding:32px 12px;">
  <table role="presentation" class="contenedor" width="600" cellpadding="0" cellspacing="0" style="width:600px;max-width:100%;background:#ffffff;border-radius:20px;overflow:hidden;box-shadow:0 8px 30px rgba(20,20,25,.08);">
    <tr><td class="relleno" style="padding:28px 40px 12px;">{_logo_html(empresa, logo_url)}</td></tr>
    <tr><td class="relleno" style="padding:12px 40px 8px;">{contenido}</td></tr>
    <tr><td style="padding:20px 40px 28px;border-top:1px solid #eceef1;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:12px;line-height:1.6;color:#8a8d91;">
      {pie}
    </td></tr>
  </table>
  <p style="margin:18px 0 0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:11px;color:#a0a3a8;">Red Human AI · Plataforma de reclutamiento y talento · Este correo se generó automáticamente.</p>
</td></tr>
</table>
</body>
</html>"""


EVENTOS = ("agendada", "modificada", "recordatorio", "cancelada")

_TEXTOS_ENTREVISTADOR = {
    "agendada": {
        "eyebrow": "Entrevista asignada",
        "titulo": "Hola {nombre}, tienes una nueva entrevista",
        "intro": "Se te asignó la entrevista de <strong style=\"color:{ink};\">{candidato}</strong> para la vacante <strong style=\"color:{ink};\">{vacante}</strong> en {empresa}. Red Human ya hizo el prefiltro y la primera entrevista: en el expediente encontrarás su CV, la evaluación integral y los puntos por validar.",
        "asunto": "Nueva entrevista asignada: {candidato} · {vacante}",
        "cta": "Ver expediente del candidato",
    },
    "modificada": {
        "eyebrow": "Entrevista modificada",
        "titulo": "Hola {nombre}, cambió tu entrevista con {candidato}",
        "intro": "La entrevista de <strong style=\"color:{ink};\">{candidato}</strong> para la vacante <strong style=\"color:{ink};\">{vacante}</strong> en {empresa} fue reprogramada. Estos son los datos vigentes:",
        "asunto": "Entrevista modificada: {candidato} · {vacante}",
        "cta": "Ver expediente del candidato",
    },
    "recordatorio": {
        "eyebrow": "Recordatorio",
        "titulo": "Hola {nombre}, tu entrevista con {candidato} se acerca",
        "intro": "Te recordamos la entrevista de <strong style=\"color:{ink};\">{candidato}</strong> para la vacante <strong style=\"color:{ink};\">{vacante}</strong> en {empresa}. En el expediente tienes su CV, la evaluación integral y los puntos por validar.",
        "asunto": "Recordatorio: entrevista con {candidato} · {vacante}",
        "cta": "Ver expediente del candidato",
    },
    "cancelada": {
        "eyebrow": "Entrevista cancelada",
        "titulo": "Hola {nombre}, se canceló tu entrevista con {candidato}",
        "intro": "La entrevista de <strong style=\"color:{ink};\">{candidato}</strong> para la vacante <strong style=\"color:{ink};\">{vacante}</strong> en {empresa} quedó cancelada. No necesitas hacer nada; si se reprograma te avisamos por este medio.",
        "asunto": "Entrevista cancelada: {candidato} · {vacante}",
        "cta": "",
    },
}

_TEXTOS_CANDIDATO = {
    "agendada": {
        "eyebrow": "Entrevista agendada",
        "titulo": "¡{nombre}, ya tienes fecha para tu entrevista!",
        "intro": "Gracias por tu entrevista con Red Human. Con base en tus resultados, <strong style=\"color:{ink};\">{empresa}</strong> quiere conocerte en persona: te agendamos una entrevista con <strong style=\"color:{ink};\">{entrevistador}</strong>.",
        "asunto": "Tu entrevista para {vacante} quedó agendada",
        "cta": "Unirme a la entrevista",
    },
    "modificada": {
        "eyebrow": "Entrevista modificada",
        "titulo": "{nombre}, tu entrevista cambió de fecha u horario",
        "intro": "<strong style=\"color:{ink};\">{empresa}</strong> reprogramó tu entrevista con <strong style=\"color:{ink};\">{entrevistador}</strong>. Estos son los datos vigentes (los anteriores ya no aplican):",
        "asunto": "Tu entrevista para {vacante} fue modificada",
        "cta": "Unirme a la entrevista",
    },
    "recordatorio": {
        "eyebrow": "Recordatorio",
        "titulo": "{nombre}, tu entrevista se acerca",
        "intro": "Te recordamos tu entrevista con <strong style=\"color:{ink};\">{entrevistador}</strong> de <strong style=\"color:{ink};\">{empresa}</strong>. ¡Mucho éxito!",
        "asunto": "Recordatorio: tu entrevista para {vacante}",
        "cta": "Unirme a la entrevista",
    },
    "cancelada": {
        "eyebrow": "Entrevista cancelada",
        "titulo": "{nombre}, tu entrevista fue cancelada",
        "intro": "<strong style=\"color:{ink};\">{empresa}</strong> canceló la entrevista que tenías programada con <strong style=\"color:{ink};\">{entrevistador}</strong>. Tu proceso sigue abierto: nos pondremos en contacto contigo para definir los siguientes pasos.",
        "asunto": "Tu entrevista para {vacante} fue cancelada",
        "cta": "",
    },
}


def _normalizar_evento(evento: str) -> str:
    e = (evento or "agendada").replace("entrevista_", "").replace("_entrevista", "")
    return e if e in EVENTOS else "agendada"


def html_entrevistador(d: dict, evento: str = "agendada") -> tuple[str, str]:
    """(asunto, html) para la persona que entrevista. d: entrevistador, candidato, vacante, empresa,
    fecha, hora, modalidad, detalle_conexion, liga_expediente, telefono_candidato, comentario, logo_url.
    `evento`: agendada | modificada | recordatorio | cancelada (2026-09-18: mismo layout para todos)."""
    evento = _normalizar_evento(evento)
    t = _TEXTOS_ENTREVISTADOR[evento]
    empresa = d.get("empresa") or "tu empresa"
    nombre = (d.get("entrevistador") or "").split(" ")[0] or "Hola"
    ctx = {"nombre": escape(nombre), "candidato": escape(d.get("candidato", "")), "vacante": escape(d.get("vacante", "")), "empresa": escape(empresa), "entrevistador": escape(d.get("entrevistador") or ""), "ink": INK}
    asunto = t["asunto"].format(**ctx).replace("&amp;", "&")
    filas = (
        _fila("Candidato", escape(d.get("candidato", "")))
        + _fila("Vacante", escape(d.get("vacante", "")))
        + _fila("Fecha", escape(d.get("fecha") or "Por confirmar"))
        + _fila("Hora", escape(d.get("hora") or "Por confirmar"))
        + _fila("Modalidad", escape(d.get("modalidad") or "Por confirmar"))
        + (_fila("Conexión / lugar", escape(d["detalle_conexion"])) if d.get("detalle_conexion") else "")
        + _fila("Teléfono del candidato", escape(d.get("telefono_candidato") or "—"), ultima=True)
    )
    cancelada = evento == "cancelada"
    contenido = (
        f'<p style="margin:0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:{ROJO};font-weight:700;">{t["eyebrow"]}</p>'
        f'<h1 class="titulo" style="margin:8px 0 12px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:26px;line-height:1.2;color:{INK};">{t["titulo"].format(**ctx)}</h1>'
        f'<p style="margin:0 0 20px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:{INK2};">{t["intro"].format(**ctx)}</p>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fafafb;border:1px solid #eceef1;border-radius:14px;padding:6px 18px;{"opacity:.6;" if cancelada else ""}">{filas}</table>'
        + (f'<p style="margin:18px 0 0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.6;color:{INK2};"><strong>Nota de RH:</strong> {escape(d["comentario"])}</p>' if d.get("comentario") and not cancelada else "")
        + (_boton(t["cta"], d.get("liga_expediente") or settings.app_url) if t["cta"] else "")
        + (f'<p style="margin:6px 0 0;text-align:center;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:12px;color:#8a8d91;">Desde ahí también registras tu evaluación al terminar.</p>' if t["cta"] else "")
    )
    pie = f"Recibes este aviso porque {escape(empresa)} te asignó como entrevistador(a) en Red Human AI. Si no te corresponde, responde a Recursos Humanos."
    return asunto, _base(asunto, f"{t['eyebrow']} · {d.get('candidato', '')} · {d.get('fecha', '')} {d.get('hora', '')}", empresa, d.get("logo_url", ""), contenido, pie)


def html_candidato(d: dict, evento: str = "agendada") -> tuple[str, str]:
    """(asunto, html) para el candidato. d: candidato, entrevistador, vacante, empresa, fecha, hora, modalidad,
    liga_conexion, ubicacion, telefono_contacto, comentario, logo_url. `evento` como en html_entrevistador."""
    evento = _normalizar_evento(evento)
    t = _TEXTOS_CANDIDATO[evento]
    cancelada = evento == "cancelada"
    empresa = d.get("empresa") or "la empresa"
    nombre = (d.get("candidato") or "").split(" ")[0] or "Hola"
    ctx = {"nombre": escape(nombre), "candidato": escape(d.get("candidato", "")), "vacante": escape(d.get("vacante", "")), "empresa": escape(empresa), "entrevistador": escape(d.get("entrevistador") or "el equipo de Recursos Humanos"), "ink": INK}
    asunto = t["asunto"].format(**ctx).replace("&amp;", "&")
    modalidad = d.get("modalidad") or "Por confirmar"
    if d.get("liga_conexion"):
        conexion = _fila("Liga de conexión", f'<a href="{escape(d["liga_conexion"])}" style="color:{ROJO};text-decoration:none;word-break:break-all;">{escape(d["liga_conexion"])}</a>', ultima=True)
    elif d.get("ubicacion"):
        conexion = _fila("Lugar", escape(d["ubicacion"]), ultima=True)
    elif d.get("telefono_contacto"):
        conexion = _fila("Te llamaremos al", escape(d["telefono_contacto"]), ultima=True)
    else:
        conexion = _fila("Conexión", "Te compartiremos los detalles antes de la entrevista", ultima=True)
    filas = (
        _fila("Entrevista con", escape(d.get("entrevistador") or "el equipo de Recursos Humanos"))
        + _fila("Vacante", escape(d.get("vacante", "")))
        + _fila("Fecha", escape(d.get("fecha") or "Por confirmar"))
        + _fila("Hora", escape(d.get("hora") or "Por confirmar"))
        + _fila("Modalidad", escape(modalidad))
        + conexion
    )
    boton = _boton(t["cta"], d["liga_conexion"]) if d.get("liga_conexion") and t["cta"] else ""
    contenido = (
        f'<p style="margin:0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:{ROJO};font-weight:700;">{t["eyebrow"]}</p>'
        f'<h1 class="titulo" style="margin:8px 0 12px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:26px;line-height:1.2;color:{INK};">{t["titulo"].format(**ctx)}</h1>'
        f'<p style="margin:0 0 20px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:{INK2};">{t["intro"].format(**ctx)}</p>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fafafb;border:1px solid #eceef1;border-radius:14px;padding:6px 18px;{"opacity:.6;" if cancelada else ""}">{filas}</table>'
        + (f'<p style="margin:18px 0 0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.6;color:{INK2};">{escape(d["comentario"])}</p>' if d.get("comentario") and not cancelada else "")
        + boton
        + ("" if cancelada else f'<p style="margin:16px 0 0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.6;color:{INK2};">Te recomendamos conectarte 5 minutos antes{" y probar tu cámara y micrófono" if modalidad == "Videollamada" else ""}. Si necesitas cambiar la fecha, respóndenos por WhatsApp.</p>')
    )
    pie = f"Tus datos se tratan conforme al Aviso de Privacidad de {escape(empresa)} exclusivamente para este proceso de selección (LFPDPPP)."
    return asunto, _base(asunto, f"{t['eyebrow']} · {d.get('fecha', '')} {d.get('hora', '')} · {d.get('vacante', '')}", empresa, d.get("logo_url", ""), contenido, pie)


def html_aviso(titulo: str, parrafo: str, empresa: str = "", filas: Optional[list[tuple[str, str]]] = None, cta: Optional[tuple[str, str]] = None) -> tuple[str, str]:
    """Aviso genérico con el layout corporativo (para el Cliente y cualquier evento sin plantilla propia):
    título, párrafo, tabla opcional de (etiqueta, valor) y CTA opcional (texto, url)."""
    tabla = ""
    if filas:
        cuerpo = "".join(_fila(k, escape(v), ultima=(i == len(filas) - 1)) for i, (k, v) in enumerate(filas))
        tabla = f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fafafb;border:1px solid #eceef1;border-radius:14px;padding:6px 18px;margin-top:4px;">{cuerpo}</table>'
    contenido = (
        f'<h1 class="titulo" style="margin:4px 0 12px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:24px;line-height:1.2;color:{INK};">{escape(titulo)}</h1>'
        f'<p style="margin:0 0 16px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:{INK2};">{escape(parrafo)}</p>'
        + tabla
        + (_boton(cta[0], cta[1]) if cta else "")
    )
    pie = "Aviso automático de Red Human AI."
    return titulo, _base(titulo, parrafo[:120], empresa, "", contenido, pie)


# ------------------------------------------------------------
# Datos de prueba para las vistas previas
# ------------------------------------------------------------

MOCK_ENTREVISTA = {
    "entrevistador": "Mariana López",
    "candidato": "Carlos Hernández Ruiz",
    "vacante": "Abogado Fiscalista",
    "empresa": "Grupo CARBE",
    "fecha": "jueves 24 de septiembre de 2026",
    "hora": fechas.con_zona("10:30 h"),
    "modalidad": "Videollamada",
    "detalle_conexion": "Microsoft Teams · https://teams.microsoft.com/l/meetup-join/ejemplo",
    "liga_conexion": "https://teams.microsoft.com/l/meetup-join/ejemplo",
    "liga_expediente": f"{settings.app_url}/entrevista-humana/ejemplo-token",
    "telefono_candidato": "55 1234 5678",
    "telefono_contacto": "",
    "ubicacion": "",
    "comentario": "Enfocar la conversación en experiencia con auditorías del SAT y manejo de equipo.",
    "logo_url": "",
}


def html_evaluacion_entrevistador(d: dict) -> tuple[str, str]:
    """Entrevista realizada → el entrevistador registra su evaluación (CTA a su liga)."""
    nombre = (d.get("entrevistador") or "").split(" ")[0] or "Hola"
    asunto = f"Registra tu evaluación: {d.get('candidato', '')} · {d.get('vacante', '')}"
    filas = [("Candidato", d.get("candidato", "")), ("Vacante", d.get("vacante", "")), ("Fecha", d.get("fecha") or "—"), ("Hora", d.get("hora") or "—")]
    cuerpo = "".join(_fila(k, escape(v), ultima=(i == len(filas) - 1)) for i, (k, v) in enumerate(filas))
    contenido = (
        f'<p style="margin:0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:{ROJO};font-weight:700;">Entrevista realizada</p>'
        f'<h1 class="titulo" style="margin:8px 0 12px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:26px;line-height:1.2;color:{INK};">Gracias por entrevistar a {escape(d.get("candidato", ""))}, {escape(nombre)}</h1>'
        f'<p style="margin:0 0 20px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:{INK2};">Ayúdanos a registrar tu evaluación: te toma menos de un minuto. En la misma liga tienes el expediente completo del candidato por si quieres repasarlo.</p>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fafafb;border:1px solid #eceef1;border-radius:14px;padding:6px 18px;">{cuerpo}</table>'
        + _boton("Registrar mi evaluación", d.get("liga_expediente") or settings.app_url)
    )
    pie = f"Recibes este aviso porque {escape(d.get('empresa') or 'la empresa')} te asignó como entrevistador(a) en Red Human AI."
    return asunto, _base(asunto, f"Evalúa a {d.get('candidato', '')}", d.get("empresa") or "", d.get("logo_url", ""), contenido, pie)


def html_entrevista_completada(d: dict, audiencia: str) -> tuple[str, str]:
    """Cierre del ciclo: la entrevista humana se completó y ya tiene evaluación (candidato / RH / cliente)."""
    resultado = d.get("resultado") or ""
    rec = d.get("recomendacion") or ""
    etiqueta_res = {"aprobado": "Aprobado", "no_aprobado": "No aprobado"}.get(resultado, "Registrado")
    etiqueta_rec = {"avanzar": "Avanzar", "no_avanzar": "No avanzar", "segunda_entrevista": "Segunda entrevista"}.get(rec, "")
    if audiencia == "candidato":
        nombre = (d.get("candidato") or "").split(" ")[0] or "Hola"
        asunto = f"Terminamos tu entrevista para {d.get('vacante', '')}"
        titulo = f"¡Gracias, {escape(nombre)}!"
        parrafo = f"Tu entrevista con {escape(d.get('entrevistador') or 'el equipo')} de {escape(d.get('empresa') or 'la empresa')} quedó registrada. El equipo de Recursos Humanos revisará los resultados y te contactará pronto con el siguiente paso."
        filas = [("Vacante", d.get("vacante", "")), ("Entrevista con", d.get("entrevistador") or "Recursos Humanos"), ("Fecha", d.get("fecha") or "—")]
        eyebrow = "Entrevista completada"
    else:
        asunto = f"Entrevista completada: {d.get('candidato', '')} · {etiqueta_res}"
        titulo = f"Entrevista con {escape(d.get('candidato', ''))} completada"
        parrafo = f"{escape(d.get('entrevistador') or 'El entrevistador')} registró su evaluación de la entrevista para {escape(d.get('vacante', ''))}. La decisión final sigue siendo de Recursos Humanos."
        filas = [("Candidato", d.get("candidato", "")), ("Vacante", d.get("vacante", "")), ("Entrevistador(a)", d.get("entrevistador", "")), ("Resultado", etiqueta_res)]
        if etiqueta_rec:
            filas.append(("Recomendación", etiqueta_rec))
        if d.get("comentario"):
            filas.append(("Comentario", d["comentario"][:300]))
        eyebrow = "Ciclo cerrado"
    cuerpo = "".join(_fila(k, escape(v), ultima=(i == len(filas) - 1)) for i, (k, v) in enumerate(filas))
    contenido = (
        f'<p style="margin:0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:{ROJO};font-weight:700;">{eyebrow}</p>'
        f'<h1 class="titulo" style="margin:8px 0 12px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:26px;line-height:1.2;color:{INK};">{titulo}</h1>'
        f'<p style="margin:0 0 20px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:{INK2};">{parrafo}</p>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fafafb;border:1px solid #eceef1;border-radius:14px;padding:6px 18px;">{cuerpo}</table>'
        + (_boton("Ver al candidato en Red Human", d["liga_dashboard"]) if audiencia == "rh" and d.get("liga_dashboard") else "")
    )
    pie = "Aviso automático de Red Human AI." if audiencia != "candidato" else f"Tus datos se tratan conforme al Aviso de Privacidad de {escape(d.get('empresa') or 'la empresa')} (LFPDPPP)."
    return asunto, _base(asunto, parrafo[:120], d.get("empresa") or "", d.get("logo_url", ""), contenido, pie)


def html_vacante_publicada(d: dict) -> tuple[str, str]:
    """Nueva vacante publicada: descripción con el diseño corporativo + CTA a la vacante en el portal.
    d: titulo, empresa, area, ubicacion, modalidad, sueldo, seniority, resumen, descripcion, requisitos (lista),
    beneficios (lista), liga, publicada_por."""
    asunto = f"Vacante publicada: {d.get('titulo', '')}"
    filas = [x for x in [("Área", d.get("area")), ("Ubicación", d.get("ubicacion")), ("Modalidad", d.get("modalidad")), ("Sueldo", d.get("sueldo")), ("Nivel", d.get("seniority"))] if x[1]]
    cuerpo = "".join(_fila(k, escape(str(v)), ultima=(i == len(filas) - 1)) for i, (k, v) in enumerate(filas))
    lista = lambda items: "".join(f'<li style="margin:4px 0;">{escape(str(x))}</li>' for x in (items or [])[:12])  # noqa: E731
    contenido = (
        f'<p style="margin:0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:{ROJO};font-weight:700;">Nueva vacante</p>'
        f'<h1 class="titulo" style="margin:8px 0 12px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:26px;line-height:1.2;color:{INK};">{escape(d.get("titulo", ""))}</h1>'
        f'<p style="margin:0 0 16px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:{INK2};">{escape(d.get("resumen") or d.get("descripcion") or "")}</p>'
        + (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fafafb;border:1px solid #eceef1;border-radius:14px;padding:6px 18px;">{cuerpo}</table>' if filas else "")
        + (f'<h2 style="margin:20px 0 6px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:14px;color:{INK};">Requisitos</h2><ul style="margin:0;padding-left:20px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.5;color:{INK2};">{lista(d.get("requisitos"))}</ul>' if d.get("requisitos") else "")
        + (f'<h2 style="margin:16px 0 6px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:14px;color:{INK};">Prestaciones</h2><ul style="margin:0;padding-left:20px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.5;color:{INK2};">{lista(d.get("beneficios"))}</ul>' if d.get("beneficios") else "")
        + (_boton("Ver la vacante", d["liga"]) if d.get("liga") else "")
    )
    pie = f"Publicada por {escape(d.get('publicada_por') or 'Recursos Humanos')} desde Red Human AI para {escape(d.get('empresa') or 'la empresa')}."
    return asunto, _base(asunto, d.get("resumen") or d.get("titulo") or "", d.get("empresa") or "", d.get("logo_url", ""), contenido, pie)
