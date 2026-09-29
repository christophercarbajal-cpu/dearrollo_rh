"""Fase D — despacho central de notificaciones configurables por evento/destinatario/canal
(puntos 22-26 de la reestructuración multi-cuenta).

Punto único de entrada: `disparar()`. Reemplaza los `if c.telefono: enviar_mensaje(...)` /
`enviar_correo(...)` que antes vivían sueltos en cada endpoint — ahora cada endpoint solo dice
QUÉ EVENTO ocurrió; este módulo decide A QUIÉN y POR QUÉ CANAL según la `ReglaNotificacion` de
la Cuenta, resuelve el dato de contacto real (nunca inventa uno, nunca pide captura nueva —
punto 23), arma el texto (que sigue siendo fijo en código, Fase D no incluye un editor de
plantillas) y deja rastro en `NotificacionEnviada`.
"""

import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..models import REGLAS_NOTIFICACION_DEFAULT, ClienteContacto, EntrevistaHumana, Mensaje, NotificacionEnviada, Postulacion, ReglaNotificacion, Usuario
from .correo import enviar_correo
from .whatsapp import enviar_mensaje, enviar_plantilla_documentos, enviar_plantilla_entrevista
from . import plantillas_correo
from ..serial import nombre_empresa_candidato

# RH/entrevistador capturan fecha/hora pensando en hora de México — nunca vienen con offset.
# SQLite descarta el offset de un DateTime(timezone=True) y se queda con los números de reloj
# tal cual, así que hay que convertir a UTC explícitamente antes de guardar (ya nos mordió
# antes en este proyecto, ver candidatos.py::programar_entrevista_humana histórico).
from .. import fechas
from ..fechas import TZ_ORG as TZ_MEXICO  # zona de la organización (app/fechas.py)

_MESES_LARGO = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

RE_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _fecha_hora_legible_mx(dt: datetime) -> str:
    local = dt.astimezone(TZ_MEXICO)
    return fechas.con_zona(f"{local.day} de {_MESES_LARGO[local.month - 1]} a las {local.strftime('%H:%M')}")


def _detalle_modalidad(eh: EntrevistaHumana, c: Postulacion) -> str:
    """Dato específico de la modalidad — se usa en el WhatsApp y en los correos."""
    if eh.modalidad == "Videollamada" and eh.liga:
        return f"Liga de la videollamada: {eh.liga}"
    if eh.modalidad == "Presencial" and eh.ubicacion:
        return f"Ubicación: {eh.ubicacion}"
    if eh.modalidad in ("Llamada", "Teléfono"):
        tel = eh.telefono_contacto or c.telefono
        if tel:
            return f"Te contactaremos al {tel}"
    return ""


def _texto_cita_entrevista_humana(eh: EntrevistaHumana, c: Postulacion) -> str:
    """Fragmento reusado por agendada/recordatorio/modificada, candidato/entrevistador/cliente."""
    cuando = _fecha_hora_legible_mx(eh.fecha) if eh.fecha else "fecha por confirmar"
    texto = f"con {eh.entrevistador or 'nuestro equipo de RH'} el {cuando}, modalidad {eh.modalidad or 'por confirmar'}."
    detalle = _detalle_modalidad(eh, c)
    if detalle:
        texto += f" {detalle}."
    if eh.comentario:
        texto += f" {eh.comentario}"
    return texto


def datos_entrevista_humana(db: Session, eh: EntrevistaHumana, c: Postulacion) -> dict:
    """Datos comunes de las plantillas (correo corporativo y plantilla de Meta) de una Entrevista
    Humana: nombres, vacante, empresa visible para el candidato, fecha/hora en México, conexión y la
    liga al expediente del candidato para el entrevistador (`/entrevista-humana/{token}`: expediente,
    evaluación integral y registro de su evaluación, sin sesión)."""
    v = c.vacante
    fecha, hora = plantillas_correo.fecha_hora_mx(eh.fecha)
    liga_conexion = eh.liga if eh.modalidad == "Videollamada" and eh.liga else ""
    detalle = _detalle_modalidad(eh, c)
    return {
        "entrevistador": eh.entrevistador or "",
        "candidato": c.nombre or "",
        "vacante": v.titulo if v else "",
        "empresa": nombre_empresa_candidato(v) if v else "",
        "fecha": fecha,
        "hora": hora,
        "modalidad": eh.modalidad or "",
        "detalle_conexion": detalle,
        "liga_conexion": liga_conexion,
        "ubicacion": eh.ubicacion or "",
        "telefono_contacto": eh.telefono_contacto or "",
        "telefono_candidato": c.telefono or "",
        "comentario": eh.comentario or "",
        # 2026-09-29 (Evaluaciones unificadas): la liga del evaluador es /evaluacion/{token}
        # (/entrevista-humana/{token} sigue abriendo la misma pantalla para las ligas ya enviadas).
        "liga_expediente": f"{settings.app_url}/evaluacion/{eh.token}",
        "logo_url": "",
    }


def _html(titulo: str, texto: str, empresa: str = "") -> tuple:
    """Cualquier correo sin plantilla propia sale con el layout corporativo (2026-09-19: cero texto plano)."""
    return plantillas_correo.html_aviso(titulo, texto, empresa)


def _aviso_cliente(titulo: str, texto: str, d: dict) -> tuple:
    """Correo al contacto del Cliente con el layout corporativo y la tabla de la entrevista."""
    filas = [("Candidato", d.get("candidato", "")), ("Vacante", d.get("vacante", "")), ("Entrevistador(a)", d.get("entrevistador", "")),
             ("Fecha", d.get("fecha") or "Por confirmar"), ("Hora", d.get("hora") or "Por confirmar"), ("Modalidad", d.get("modalidad") or "Por confirmar")] if d else None
    return plantillas_correo.html_aviso(titulo, texto, d.get("empresa", "") if d else "", filas)


def parametros_plantilla_entrevista(d: dict) -> List[str]:
    """Los 6 parámetros posicionales de `alerta_entrevista_asignada`, en este orden exacto."""
    return [
        d.get("entrevistador") or "Entrevistador(a)",
        d.get("candidato") or "",
        d.get("vacante") or "",
        d.get("fecha") or "Por confirmar",
        d.get("hora") or "Por confirmar",
        d.get("liga_expediente") or "",
    ]


def _html_correo_candidato(eh: EntrevistaHumana, c: Postulacion) -> str:
    cuando = _fecha_hora_legible_mx(eh.fecha) if eh.fecha else "fecha por confirmar"
    detalle = _detalle_modalidad(eh, c)
    primer_nombre = c.nombre.split(" ")[0] if c.nombre else "candidato(a)"
    return (
        f"<p>¡Hola {primer_nombre}!</p>"
        f"<p>Te confirmamos tu entrevista con <strong>{eh.entrevistador or 'nuestro equipo de RH'}</strong> "
        f"el <strong>{cuando}</strong>, modalidad <strong>{eh.modalidad}</strong>.</p>"
        + (f"<p>{detalle}.</p>" if detalle else "")
        + (f"<p>{eh.comentario}</p>" if eh.comentario else "")
        + "<p>Saludos,<br>Red Human AI</p>"
    )


def _html_correo_entrevistador(eh: EntrevistaHumana, c: Postulacion) -> str:
    cuando = _fecha_hora_legible_mx(eh.fecha) if eh.fecha else "fecha por confirmar"
    detalle = _detalle_modalidad(eh, c)
    return (
        f"<p>Tienes una entrevista programada con <strong>{c.nombre}</strong> "
        f"({c.vacante.titulo if c.vacante else 'vacante sin especificar'}) "
        f"el <strong>{cuando}</strong>, modalidad <strong>{eh.modalidad}</strong>.</p>"
        + (f"<p>{detalle}.</p>" if detalle else "")
        + (f"<p>Teléfono del candidato: {c.telefono}</p>" if c.telefono else "")
        + (f"<p>{eh.comentario}</p>" if eh.comentario else "")
        + "<p>Saludos,<br>Red Human AI</p>"
    )


def _html_correo_evaluacion_entrevistador(eh: EntrevistaHumana, c: Postulacion, liga: str) -> str:
    return (
        f"<p>Gracias por entrevistar a <strong>{c.nombre}</strong> "
        f"({c.vacante.titulo if c.vacante else 'vacante sin especificar'}).</p>"
        "<p>Ayúdanos a registrar tu evaluación — te toma menos de un minuto:</p>"
        f"<p><a href=\"{liga}\">{liga}</a></p>"
        "<p>Saludos,<br>Red Human AI</p>"
    )


def _texto_solicitud_documentos(liga: str) -> str:
    return (
        "¡Felicidades por tu contratación! 🎉 Para avanzar, sube tu INE y tu comprobante de "
        f"domicilio (foto o PDF) desde esta liga: {liga}\n\nEn cuanto los reciba los reviso y "
        "seguimos con el resto de tu expediente."
    )


def texto_recordatorio_documentos(
    nivel: int, primer_nombre: str, puesto: str, pendientes: Optional[List[str]], liga: str = "",
    fecha_limite: Optional[datetime] = None, detalle_rechazos: str = "",
) -> str:
    """Recordatorio de documentos en 3 niveles progresivos (2026-09-17):
    1 ligero (amistoso), 2 intermedio (estándar, con lista y fecha), 3 definitivo (último aviso
    automático: firme, claro sobre la consecuencia, sin amenazas ni presión indebida).
    Sirve para WhatsApp (texto libre) y correo. `pendientes` None → liga genérica de subida."""
    nivel = max(1, min(int(nivel or 1), 3))
    nombre = f" {primer_nombre}" if primer_nombre else ""
    que = f"me falta recibir: {', '.join(pendientes)}" if pendientes else "me faltan tu INE y tu comprobante de domicilio"
    donde = f"\n\nSúbelos aquí: {liga}" if liga else "\n\nMándalos por aquí (foto o PDF)."
    fecha_txt = _fecha_hora_legible_mx(fecha_limite).split(" a las")[0] if fecha_limite else ""
    limite = f" La fecha límite es el {fecha_txt}." if fecha_txt else ""
    rechazos = f"\n\nAlgunos necesitan volver a enviarse:{detalle_rechazos}" if detalle_rechazos else ""
    if nivel == 1:
        # la fecha se menciona como dato, sin presión («tienes hasta…»)
        limite_suave = f" Tienes hasta el {fecha_txt} (fecha límite), así que hay tiempo." if fecha_txt else ""
        return (
            f"Hola{nombre} 👋 ¿Cómo vas? Para seguir con tu expediente de {puesto} {que}.{limite_suave}"
            f"{rechazos}{donde}\n\nSin prisa, en cuanto los tengas a la mano. 🙌"
        )
    if nivel == 2:
        return (
            f"Hola{nombre}, te escribo de nuevo para dar seguimiento a tu expediente de {puesto}: "
            f"todavía {que}.{limite}{rechazos}{donde}\n\nCon eso podemos avanzar con tu ingreso. "
            "Si tienes alguna dificultad para conseguirlos, cuéntame y lo resolvemos. 🙂"
        )
    return (
        f"Hola{nombre}. Este es el último recordatorio automático sobre tu expediente de {puesto}: "
        f"aún {que}.{limite}{rechazos}{donde}\n\nSi no los recibimos, tu proceso de ingreso quedará en "
        "pausa hasta completarlo y una persona de Recursos Humanos se pondrá en contacto contigo. "
        "Si necesitas más tiempo o tienes alguna dificultad, respóndeme por aquí y lo vemos juntos."
    )


def _asunto_recordatorio(nivel: int) -> str:
    return {1: "Recordatorio de documentos", 2: "Seguimiento: documentos pendientes", 3: "Último recordatorio: documentos pendientes"}.get(
        max(1, min(int(nivel or 1), 3)), "Recordatorio de documentos"
    )


# ------------------------------------------------------------
# Punto 23 — resolución de destinatarios: SIEMPRE del dato que ya existe, nunca captura nueva.
# ------------------------------------------------------------


def _correo_entrevistador(db: Session, eh: EntrevistaHumana) -> str:
    if eh.tipo == "interno" and eh.usuario_id:
        u = db.query(Usuario).filter(Usuario.id == eh.usuario_id).first()
        return u.correo if u else ""
    return eh.correo_externo


def _whatsapp_entrevistador(db: Session, eh: EntrevistaHumana) -> str:
    if eh.tipo == "interno" and eh.usuario_id:
        u = db.query(Usuario).filter(Usuario.id == eh.usuario_id).first()
        return u.telefono if u else ""
    return eh.whatsapp_externo


# ------------------------------------------------------------
# Punto 22/24/25 — el texto por (evento, audiencia, canal). Regresa None si esa combinación no
# tiene contenido definido (se omite sin error — nadie configura algo que no exista).
# ------------------------------------------------------------


def _mensaje(evento: str, audiencia: str, canal: str, c: Postulacion, eh: Optional[EntrevistaHumana], liga: str, extra: dict):
    v = c.vacante
    puesto = extra.get("puesto") or (v.titulo if v else "la vacante")
    primer_nombre = c.nombre.split(" ")[0] if c.nombre else "candidato(a)"
    cita = _texto_cita_entrevista_humana(eh, c) if eh else ""

    if evento == "entrevista_agendada" and eh:
        # 2026-09-18: correos con las plantillas corporativas (services/plantillas_correo.py). El WhatsApp del
        # entrevistador sale como plantilla de Meta «alerta_entrevista_asignada» (ver disparar); este texto
        # es el respaldo si la plantilla falla.
        d = extra.get("_datos_entrevista") or {}
        if audiencia == "candidato":
            texto = f"¡Hola {primer_nombre}! 📅 Con base en tu entrevista con Red Human, te programamos una entrevista {cita}"
            return texto if canal == "whatsapp" else (plantillas_correo.html_candidato(d) if d else ("Tu entrevista con Red Human AI", _html_correo_candidato(eh, c)))
        if audiencia == "entrevistador":
            texto = f"Tienes una entrevista programada con {c.nombre} ({puesto}) {cita} Expediente: {d.get('liga_expediente', '')}".strip()
            return texto if canal == "whatsapp" else (plantillas_correo.html_entrevistador(d) if d else (f"Entrevista programada con {c.nombre}", _html_correo_entrevistador(eh, c)))
        if audiencia == "cliente":
            texto = f"Se programó una entrevista para el candidato {c.nombre} ({puesto}) {cita}"
            return texto if canal == "whatsapp" else _aviso_cliente(f"Entrevista programada — {puesto}", texto, d)

    # 2026-09-18: los cuatro eventos de la Entrevista Humana (agendada/modificada/recordatorio/cancelada)
    # usan el MISMO layout corporativo (plantillas_correo) para candidato y entrevistador; el Cliente
    # recibe el aviso genérico con el mismo layout. Nada sale en texto plano.
    d = extra.get("_datos_entrevista") or {}
    if evento == "recordatorio_entrevista" and eh:
        if audiencia == "candidato":
            texto = f"¡Hola de nuevo, {primer_nombre}! 👋 Te recordamos tu entrevista {cita}"
            return texto if canal == "whatsapp" else (plantillas_correo.html_candidato(d, "recordatorio") if d else _html("Recordatorio de tu entrevista", texto))
        if audiencia == "entrevistador":
            texto = f"Recordatorio: tienes una entrevista con {c.nombre} ({puesto}) {cita}"
            return texto if canal == "whatsapp" else (plantillas_correo.html_entrevistador(d, "recordatorio") if d else _html("Recordatorio de entrevista", texto))
        if audiencia == "cliente":
            texto = f"Recordatorio: entrevista programada con {c.nombre} ({puesto}) {cita}"
            return texto if canal == "whatsapp" else _aviso_cliente("Recordatorio de entrevista", texto, d)

    if evento == "entrevista_modificada" and eh:
        if audiencia == "candidato":
            texto = f"Hola {primer_nombre}, tu entrevista cambió — ahora es {cita}"
            return texto if canal == "whatsapp" else (plantillas_correo.html_candidato(d, "modificada") if d else _html("Tu entrevista fue modificada", texto))
        if audiencia == "entrevistador":
            texto = f"La entrevista con {c.nombre} ({puesto}) fue modificada — ahora es {cita}"
            return texto if canal == "whatsapp" else (plantillas_correo.html_entrevistador(d, "modificada") if d else _html("Entrevista modificada", texto))
        if audiencia == "cliente":
            texto = f"La entrevista con el candidato {c.nombre} ({puesto}) fue modificada — ahora es {cita}"
            return texto if canal == "whatsapp" else _aviso_cliente("Entrevista modificada", texto, d)

    if evento == "entrevista_cancelada":
        if audiencia == "candidato":
            texto = f"Hola {primer_nombre}, tu entrevista programada fue cancelada. Nos pondremos en contacto para definir los siguientes pasos."
            return texto if canal == "whatsapp" else (plantillas_correo.html_candidato(d, "cancelada") if d else _html("Tu entrevista fue cancelada", texto))
        if audiencia == "entrevistador":
            texto = f"La entrevista con {c.nombre} ({puesto}) fue cancelada."
            return texto if canal == "whatsapp" else (plantillas_correo.html_entrevistador(d, "cancelada") if d else _html("Entrevista cancelada", texto))
        if audiencia == "cliente":
            texto = f"La entrevista con el candidato {c.nombre} ({puesto}) fue cancelada."
            return texto if canal == "whatsapp" else _aviso_cliente("Entrevista cancelada", texto, d)

    if evento == "candidato_apto":
        if audiencia == "candidato":
            texto = f"¡Buenas noticias, {primer_nombre}! 🎉 Avanzas en el proceso para {puesto}."
            return texto if canal == "whatsapp" else _html("¡Avanzas en el proceso!", texto, nombre_empresa_candidato(v) if v else "")
        if audiencia == "cliente":
            texto = f"El candidato {c.nombre} avanza en el proceso para el puesto {puesto}."
            return texto if canal == "whatsapp" else _html(f"Avance de candidato — {puesto}", texto)

    if evento == "entrevista_humana_terminada" and eh:
        if audiencia == "entrevistador":
            liga_eval = f"{settings.app_url}/evaluacion/{eh.token}"
            texto = f"Gracias por entrevistar a {c.nombre} ({puesto}). Ayúdanos a registrar tu evaluación: {liga_eval}"
            return texto if canal == "whatsapp" else (plantillas_correo.html_evaluacion_entrevistador(d) if d else _html(f"Tu evaluación de la entrevista con {c.nombre}", texto))
        if audiencia == "candidato":
            texto = f"¡Gracias, {primer_nombre}! Terminamos tu entrevista para {puesto}. El equipo de RH revisará tus resultados y te contactará pronto."
            return texto if canal == "whatsapp" else (plantillas_correo.html_entrevista_completada(d, "candidato") if d else _html("Terminamos tu entrevista", texto))
        if audiencia == "cliente":
            texto = f"El candidato {c.nombre} completó su entrevista humana para el puesto {puesto}."
            return texto if canal == "whatsapp" else _aviso_cliente(f"Entrevista completada — {puesto}", texto, d)

    if evento == "entrevista_completada" and eh:
        # 2026-09-19: el entrevistador registró su evaluación desde su liga → ciclo cerrado.
        if audiencia == "candidato":
            texto = f"¡Gracias, {primer_nombre}! Tu entrevista para {puesto} quedó registrada. El equipo de RH revisará los resultados y te contactará pronto."
            return texto if canal == "whatsapp" else (plantillas_correo.html_entrevista_completada(d, "candidato") if d else _html("Terminamos tu entrevista", texto))
        if audiencia == "cliente":
            texto = f"La entrevista de {c.nombre} ({puesto}) se completó: resultado {eh.resultado or 'registrado'}."
            return texto if canal == "whatsapp" else (plantillas_correo.html_entrevista_completada(d, "cliente") if d else _html("Entrevista completada", texto))
        if audiencia == "entrevistador":
            texto = f"Gracias, registramos tu evaluación de {c.nombre} ({puesto})."
            return texto if canal == "whatsapp" else _html("Evaluación registrada", texto, d.get("empresa", "") if d else "")

    if evento == "recomendacion_final" and eh:
        resultado_legible = "Aprobado" if eh.resultado == "aprobado" else "No aprobado"
        if audiencia == "cliente":
            texto = f"La recomendación final para el candidato {c.nombre} ({puesto}) ya está disponible: {resultado_legible}."
            return texto if canal == "whatsapp" else _html(f"Recomendación final — {puesto}", texto)
        if audiencia == "candidato":
            texto = f"Hola {primer_nombre}, ya tenemos una recomendación sobre tu proceso para {puesto}. El equipo de RH se pondrá en contacto contigo."
            return texto if canal == "whatsapp" else _html("Novedades de tu proceso", texto)
        if audiencia == "entrevistador":
            texto = f"Se registró la recomendación final para {c.nombre} ({puesto}): {resultado_legible}."
            return texto if canal == "whatsapp" else _html(f"Recomendación registrada — {c.nombre}", texto)

    if evento == "contratacion":
        ingreso = ""
        fecha_ingreso = extra.get("fecha_ingreso")
        if fecha_ingreso:
            ingreso = f" Te esperamos el {fecha_ingreso.day}."
        if audiencia == "candidato":
            texto = f"¡Bienvenido(a) {primer_nombre}! 🎊 Tu expediente quedó completo y tu alta fue autorizada.{ingreso} En los próximos días te comparto tu plan de inducción."
            return texto if canal == "whatsapp" else _html("¡Bienvenido(a) al equipo!", texto)
        if audiencia == "cliente":
            texto = f"El candidato {c.nombre} fue contratado para el puesto {puesto}.{ingreso}"
            return texto if canal == "whatsapp" else _html(f"Contratación confirmada — {puesto}", texto)

    if evento == "instrucciones_ingreso" and audiencia == "candidato":
        # Fase 5: bienvenida + instrucciones de ingreso, automático al dar de alta como colaborador.
        e = c.expediente
        empresa = extra.get("empresa") or (nombre_empresa_candidato(v) if v else "") or "la empresa"
        fecha = extra.get("fecha_ingreso")
        # fecha de ingreso = día de calendario (medianoche UTC): se lee sin convertir de zona
        fecha_txt = f"{fechas.dia(fecha).day} de {_MESES_LARGO[fechas.dia(fecha).month - 1]}" if fecha else "por confirmar"
        datos = [
            ("Puesto", extra.get("puesto") or (e.puesto if e else "") or puesto),
            ("Empresa", empresa),
            ("Fecha de ingreso", fecha_txt),
            ("Lugar", (e.ubicacion if e else "") or (v.ubicacion if v else "") or "por confirmar"),
            ("Jefe(a) directo(a)", (e.jefe_directo if e else "") or "por confirmar"),
            ("Tipo de contratación", (e.tipo_contratacion if e else "") or "por confirmar"),
        ]
        instrucciones = ((e.instrucciones_ingreso if e else "") or "").strip()
        contacto = extra.get("contacto_rh") or ""
        if canal == "whatsapp":
            lineas = "\n".join(f"• {k}: {val}" for k, val in datos)
            texto = (
                f"¡Bienvenido(a) al equipo, {primer_nombre}! 🎉 Tu alta como colaborador(a) en {empresa} ya está autorizada.\n\n"
                f"Estos son tus datos de ingreso:\n{lineas}"
                + (f"\n\nInstrucciones para tu primer día:\n{instrucciones}" if instrucciones else "")
                + (f"\n\nCualquier duda, escríbenos: {contacto}" if contacto else "")
                + "\n\n¡Nos vemos pronto! 🙌"
            )
            return texto
        filas = "".join(f"<tr><td style=\"padding:6px 12px 6px 0;font-weight:bold\">{k}</td><td style=\"padding:6px 0\">{val}</td></tr>" for k, val in datos)
        html = (
            f"<p>¡Bienvenido(a) al equipo, <strong>{primer_nombre}</strong>! 🎉</p>"
            f"<p>Tu alta como colaborador(a) en <strong>{empresa}</strong> ya está autorizada. Estos son tus datos de ingreso:</p>"
            f"<table style=\"border-collapse:collapse\">{filas}</table>"
            + (f"<p><strong>Instrucciones para tu primer día:</strong><br>{instrucciones.replace(chr(10), '<br>')}</p>" if instrucciones else "")
            + (f"<p>Cualquier duda, escríbenos: {contacto}</p>" if contacto else "")
            + "<p>¡Nos vemos pronto!<br>Red Human AI</p>"
        )
        return (f"Bienvenido(a) a {empresa} — instrucciones de ingreso", html)

    if evento in EVENTOS_EVALUACION:
        return _mensaje_evaluacion(evento, audiencia, canal, c, eh, extra)

    if evento == "solicitud_documentos":
        if audiencia == "candidato":
            texto = _texto_solicitud_documentos(liga)
            return texto if canal == "whatsapp" else _html("Solicitud de documentos", texto)

    if evento == "recordatorio_documentos":
        if audiencia == "candidato":
            # 2026-09-17: tono por nivel (1 ligero, 2 intermedio, 3 definitivo) — `extra["nivel"]` lo
            # decide el expediente (Expediente.nivel_recordatorio); sin dato se asume 1.
            nivel = int(extra.get("nivel") or 1)
            texto = texto_recordatorio_documentos(
                nivel, primer_nombre, puesto, extra.get("pendientes"), liga,
                fecha_limite=extra.get("fecha_limite"), detalle_rechazos=extra.get("detalle_rechazos", ""),
            )
            return texto if canal == "whatsapp" else _html(_asunto_recordatorio(nivel), texto)

    return None


# ------------------------------------------------------------
# Evaluaciones unificadas — Fase 1 (2026-09-29). Cuatro eventos para TODAS las evaluaciones (entrevista humana
# incluida). La entrevista humana con cita conserva sus correos corporativos y la plantilla de Meta; el resto de
# los tipos usa el aviso genérico con el mismo layout. Qué audiencia recibe qué lo decide la matriz de la
# especificación (sección 7) en services/evaluaciones.notificar — aquí solo el texto.
# ------------------------------------------------------------
EVENTOS_EVALUACION = ("evaluacion_asignada", "evaluacion_reprogramada", "evaluacion_cancelada", "recordatorio_evaluacion")
# Sin regla guardada del evento nuevo, los avisos al CLIENTE heredan lo que la Cuenta ya tenía configurado para
# las entrevistas (nada de lo que existe se pierde); candidato/evaluador salen de REGLAS_NOTIFICACION_DEFAULT.
EVENTO_LEGADO_EVALUACION = {
    "evaluacion_asignada": "entrevista_agendada",
    "evaluacion_reprogramada": "entrevista_modificada",
    "evaluacion_cancelada": "entrevista_cancelada",
    "recordatorio_evaluacion": "recordatorio_entrevista",
}


def _mensaje_evaluacion(evento: str, audiencia: str, canal: str, c: Postulacion, eh, extra: dict):
    d = extra.get("_datos_evaluacion") or {}
    if d.get("tipo") == "entrevista_humana" and d.get("con_cita") and eh is not None:
        # misma redacción y mismas plantillas corporativas de siempre para la entrevista humana con cita
        return _mensaje(EVENTO_LEGADO_EVALUACION[evento], audiencia, canal, c, eh, "", extra)
    tipo = d.get("tipo_nombre") or "Evaluación"
    puesto = d.get("vacante") or (c.vacante.titulo if c.vacante else "la vacante")
    primer_nombre = c.nombre.split(" ")[0] if c.nombre else "candidato(a)"
    cita = d.get("cita_texto") or ""
    instrucciones = d.get("instrucciones") or ""
    empresa = d.get("empresa") or ""
    filas = [(k, v) for k, v in (
        ("Evaluación", tipo), ("Candidato", c.nombre), ("Vacante", puesto), ("Fecha", d.get("fecha") or ""),
        ("Hora", d.get("hora") or ""), ("Modalidad", d.get("modalidad") or ""), ("Dirección", d.get("direccion") or ""),
        ("Liga de videollamada", d.get("liga_videollamada") or ""), ("Instrucciones", instrucciones),
    ) if v]
    liga_eval = d.get("liga_evaluador") or ""
    liga_ext = d.get("liga_externa") or ""
    cancelada = evento == "evaluacion_cancelada"
    if audiencia == "entrevistador":
        verbo = {"evaluacion_asignada": "Se te asignó", "evaluacion_reprogramada": "Se reprogramó", "evaluacion_cancelada": "Se canceló",
                 "recordatorio_evaluacion": "Recordatorio de"}[evento]
        texto = f"{verbo} la evaluación «{tipo}» de {c.nombre} ({puesto})."
        if cita:
            texto += f" {cita}"
        if instrucciones and not cancelada:
            texto += f" Instrucciones: {instrucciones}"
        if liga_eval and not cancelada:
            texto += f" Consulta el expediente y registra el resultado aquí: {liga_eval}"
        if canal == "whatsapp":
            return texto
        cta = ("Abrir la evaluación", liga_eval) if liga_eval and not cancelada else None
        return plantillas_correo.html_aviso(f"{tipo} — {c.nombre}", texto, empresa, filas, cta)
    if audiencia == "candidato":
        if cancelada:
            texto = f"Hola {primer_nombre}, tu evaluación «{tipo}» para {puesto} fue cancelada. Nos pondremos en contacto contigo."
        else:
            inicio = {"evaluacion_asignada": f"Hola {primer_nombre}, te programamos tu evaluación «{tipo}» para {puesto}.",
                      "evaluacion_reprogramada": f"Hola {primer_nombre}, tu evaluación «{tipo}» para {puesto} cambió.",
                      "recordatorio_evaluacion": f"Hola {primer_nombre}, te recordamos tu evaluación «{tipo}» para {puesto}."}[evento]
            texto = inicio + (f" {cita}" if cita else "")
            if instrucciones:
                texto += f" Instrucciones: {instrucciones}"
            if liga_ext:
                texto += f" Realízala en esta liga: {liga_ext}"
        if canal == "whatsapp":
            return texto
        cta = ("Abrir", liga_ext) if liga_ext and not cancelada else None
        return plantillas_correo.html_aviso(f"Tu evaluación — {puesto}", texto, empresa, [f for f in filas if f[0] != "Candidato"], cta)
    if audiencia == "cliente":
        verbo = {"evaluacion_asignada": "Se programó", "evaluacion_reprogramada": "Se reprogramó", "evaluacion_cancelada": "Se canceló",
                 "recordatorio_evaluacion": "Recordatorio de"}[evento]
        texto = f"{verbo} la evaluación «{tipo}» del candidato {c.nombre} ({puesto}). {cita}".strip()
        return texto if canal == "whatsapp" else plantillas_correo.html_aviso(f"{tipo} — {puesto}", texto, empresa, filas)
    return None


def _regla(db: Session, cuenta_id: int, evento: str) -> Optional[ReglaNotificacion]:
    return db.query(ReglaNotificacion).filter(
        ReglaNotificacion.cuenta_id == cuenta_id, ReglaNotificacion.evento == evento
    ).first()


# Eventos que al candidato le llegan por WhatsApp con la plantilla META_PLANTILLA_DOCUMENTOS
# (2026-09-15): así «Solicitar documentos» y «Enviar recordatorio» salen aunque la ventana de 24 h
# esté cerrada, y el candidato contesta mandando el archivo por el mismo chat.
EVENTOS_PLANTILLA_DOCUMENTOS = ("solicitud_documentos", "recordatorio_documentos")


def _valores_plantilla_documentos(c: Postulacion, liga: str, extra: dict) -> dict:
    v = c.vacante
    pendientes = extra.get("pendientes")
    if pendientes is None and c.expediente:
        pendientes = c.expediente.pendientes
    return {
        "nombre": (c.nombre.split(" ")[0] if c.nombre else "") or "candidato(a)",
        "documentos": ", ".join(pendientes) if pendientes else "INE y comprobante de domicilio",
        "liga": liga or "",
        "empresa": nombre_empresa_candidato(v) if v else "",
        "vacante": extra.get("puesto") or (v.titulo if v else ""),
        "nivel": int(extra.get("nivel") or 1),  # 2026-09-17: elige la plantilla por nivel (no es variable de Meta)
    }


async def _enviar_y_registrar(
    db: Session, p: Postulacion, evento: str, destinatario_tipo: str, canal: str, destino: str, contenido,
    plantilla_valores: Optional[dict] = None,
    plantilla_entrevista: Optional[List[str]] = None,
) -> dict:
    """Regresa {destinatario, canal, destino, enviado, proveedor, detalle} — Fase 7A: el detalle de
    por qué NO salió un envío (sin correo, RESEND_API_KEY sin configurar, Meta rechazó…) ya no se
    queda solo en NotificacionEnviada: llega hasta la respuesta para que RH lo vea."""
    cuenta_id, candidato_id = p.cuenta_id, p.candidato_id
    base = {"destinatario": destinatario_tipo, "canal": canal, "destino": destino or ""}
    if not destino or not contenido:
        detalle = "sin dato de contacto" if not destino else "sin contenido definido para esta combinación"
        db.add(NotificacionEnviada(
            cuenta_id=cuenta_id, candidato_id=candidato_id, evento=evento, destinatario_tipo=destinatario_tipo,
            canal=canal, destino=destino or "", enviado=False, detalle=detalle,
        ))
        return {**base, "enviado": False, "detalle": detalle}
    try:
        if canal == "whatsapp" and plantilla_valores is not None:
            envio = await enviar_plantilla_documentos(destino, plantilla_valores, contenido, nivel=int(plantilla_valores.get("nivel") or 1))
        elif canal == "whatsapp" and plantilla_entrevista is not None and settings.whatsapp_provider == "meta" and (settings.meta_plantilla_entrevista or "").strip():
            # plantilla de Meta «alerta_entrevista_asignada» (6 parámetros); si Meta la rechaza cae a texto
            envio = await enviar_plantilla_entrevista(destino, plantilla_entrevista, contenido)
        elif canal == "whatsapp":
            envio = await enviar_mensaje(destino, contenido)
        else:
            asunto, html = contenido
            envio = await enviar_correo(destino, asunto, html)
    except Exception as ex:  # que un proveedor falle no debe tumbar el flujo que disparó el evento
        envio = {"enviado": False, "proveedor": "error", "detalle": str(ex)}
    db.add(NotificacionEnviada(
        cuenta_id=cuenta_id, candidato_id=candidato_id, evento=evento, destinatario_tipo=destinatario_tipo,
        canal=canal, destino=destino, enviado=bool(envio.get("enviado")), detalle=str(envio.get("detalle", "")),
    ))
    if destinatario_tipo == "candidato" and canal == "whatsapp":
        # Mensaje saliente: queda en el historial de ESTA postulación pero NO mueve la
        # conversación del candidato (B1, ver candidatos.fijar_conversacion).
        db.add(Mensaje(
            candidato_id=candidato_id, postulacion_id=p.id, rol="assistant", texto=contenido, canal="whatsapp",
            enviado=bool(envio.get("enviado")), wa_id=envio.get("wa_id", ""),
        ))
    return {**base, **envio, "detalle": str(envio.get("detalle", ""))}


def advertencias_de(resultados: List[dict]) -> List[str]:
    """2026-09-18: avisos legibles para RH cuando un canal NO salió (se regresan en el JSON de la acción y el
    frontend los muestra como toast amarillo). Un correo fallido deja de ser silencioso."""
    avisos: List[str] = []
    for r in resultados or []:
        if r.get("enviado"):
            continue
        detalle = str(r.get("detalle") or "")
        if r.get("canal") == "correo" and r.get("destino"):
            avisos.append(f"Evaluación asignada, pero el correo falló. Verifica la API Key o el Dominio ({r.get('destinatario')}: {detalle[:120]})")
        elif r.get("canal") == "whatsapp" and r.get("destino"):
            avisos.append(f"El WhatsApp a {r.get('destinatario')} no salió: {detalle[:120]}")
    return avisos


FLAGS_NOTIFICACION = (
    "candidato_correo", "candidato_whatsapp",
    "entrevistador_correo", "entrevistador_whatsapp",
    "cliente_correo", "cliente_whatsapp",
)


class NotificarIn(BaseModel):
    """Cuerpo opcional `notificar` de las acciones manuales (Punto 12): lo que RH ajustó en la
    línea "Notificar: … · Editar" solo para esta acción. None = usar la regla predeterminada."""
    candidato_correo: Optional[bool] = None
    candidato_whatsapp: Optional[bool] = None
    entrevistador_correo: Optional[bool] = None
    entrevistador_whatsapp: Optional[bool] = None
    cliente_correo: Optional[bool] = None
    cliente_whatsapp: Optional[bool] = None
    # Fase 7A: contactos del Cliente elegidos por RH para ESTA acción (ids de ClienteContacto).
    # None = todos los contactos del Cliente (comportamiento de Fase D).
    cliente_contactos_ids: Optional[List[int]] = None


def override_de(datos: Optional[NotificarIn]) -> Optional[dict]:
    """Dict con los flags explícitos (los None se omiten) o None si no se ajustó nada."""
    if datos is None:
        return None
    valores = {k: v for k, v in datos.model_dump().items() if v is not None}
    return valores or None


class _ReglaEfectiva:
    """Regla predeterminada de la Cuenta + override de UNA acción (Punto 12). Los flags que el
    override no menciona (None) conservan el valor de la regla guardada; la regla en sí nunca se
    modifica desde aquí."""

    def __init__(self, regla: Optional[ReglaNotificacion], override: Optional[dict]):
        for flag in FLAGS_NOTIFICACION:
            valor = (override or {}).get(flag)
            if valor is None:
                valor = bool(getattr(regla, flag)) if regla else False
            setattr(self, flag, bool(valor))
        ids = (override or {}).get("cliente_contactos_ids")
        self.cliente_contactos_ids: Optional[List[int]] = [int(x) for x in ids] if ids is not None else None

    def alguno(self) -> bool:
        return any(getattr(self, f) for f in FLAGS_NOTIFICACION)


async def disparar(
    db: Session,
    evento: str,
    c: Postulacion,
    actor: str,
    *,
    eh: Optional[EntrevistaHumana] = None,
    liga: str = "",
    extra: Optional[dict] = None,
    override: Optional[dict] = None,
) -> List[dict]:
    """Punto único de entrada del sistema de notificaciones (Fase D). Nunca truena: si falta la
    Cuenta, la regla, o el dato de contacto, simplemente no manda ese envío en particular.

    `override` (Punto 12): flags que RH ajustó SOLO para esta acción desde la línea
    "Notificar: … · Editar"; sustituyen a la regla guardada únicamente en esta llamada. Los
    eventos automáticos (transición a apto, no-show, liga externa) nunca mandan override."""
    extra = extra or {}
    if not c.cuenta_id:
        return []
    regla_guardada = _regla(db, c.cuenta_id, evento)
    if not regla_guardada and evento in EVENTO_LEGADO_EVALUACION:
        # Evaluaciones unificadas: el Cliente conserva lo que la Cuenta configuró para las entrevistas
        legado = _regla(db, c.cuenta_id, EVENTO_LEGADO_EVALUACION[evento])
        if legado is not None:
            base = dict(REGLAS_NOTIFICACION_DEFAULT.get(evento) or {})
            base.update({"cliente_correo": bool(legado.cliente_correo), "cliente_whatsapp": bool(legado.cliente_whatsapp)})
            override = {**base, **{k: v for k, v in (override or {}).items() if v is not None}}
    if not regla_guardada:
        # Fase 3 (2026-09-15): sin regla guardada (nadie abrió Configuración → Notificaciones todavía)
        # se aplica la regla con la que NACERÍA (REGLAS_NOTIFICACION_DEFAULT) — antes un evento
        # automático (recordatorio, no-show) no mandaba nada en una Cuenta nueva.
        # 2026-09-18: los defaults son la BASE aunque la acción traiga un override parcial (p. ej. solo
        # «cliente» apagado): antes ese override parcial dejaba en False todo lo que no mencionaba y la
        # entrevista se agendaba sin avisar a nadie.
        defaults = REGLAS_NOTIFICACION_DEFAULT.get(evento) or {}
        override = {**defaults, **{k: v for k, v in (override or {}).items() if v is not None}}
        if not override:
            return []
    regla = _ReglaEfectiva(regla_guardada, override)
    if not regla.alguno():
        return []

    resultados: List[dict] = []
    # 2026-09-18: datos compartidos por las plantillas de la Entrevista Humana (correo + Meta)
    if eh and evento in ("entrevista_agendada", "entrevista_modificada", "recordatorio_entrevista", "entrevista_cancelada", "entrevista_humana_terminada", "recomendacion_final", "entrevista_completada", *EVENTOS_EVALUACION):
        d_eh = datos_entrevista_humana(db, eh, c)
        d_eh.update({"resultado": eh.resultado or "", "recomendacion": eh.recomendacion or "", "comentario_evaluacion": eh.comentario or ""})
        extra = {**extra, "_datos_entrevista": d_eh}

    # --- Candidato: correo/teléfono ya en su ficha (punto 23) ---
    if regla.candidato_whatsapp:
        texto = _mensaje(evento, "candidato", "whatsapp", c, eh, liga, extra)
        valores = _valores_plantilla_documentos(c, liga, extra) if evento in EVENTOS_PLANTILLA_DOCUMENTOS else None
        resultados.append(await _enviar_y_registrar(db, c, evento, "candidato", "whatsapp", c.telefono, texto, plantilla_valores=valores))
    if regla.candidato_correo:
        contenido = _mensaje(evento, "candidato", "correo", c, eh, liga, extra)
        resultados.append(await _enviar_y_registrar(db, c, evento, "candidato", "correo", c.correo, contenido))

    # --- Entrevistador: Usuario si es interno, datos ya registrados en la EntrevistaHumana si
    # es externo (punto 23) — sin ronda vigente no hay a quién resolver, se omite. ---
    if eh and (regla.entrevistador_whatsapp or regla.entrevistador_correo):
        if regla.entrevistador_whatsapp:
            texto = _mensaje(evento, "entrevistador", "whatsapp", c, eh, liga, extra)
            d_ev = extra.get("_datos_evaluacion") or {}
            con_plantilla = evento == "entrevista_agendada" or (evento == "evaluacion_asignada" and d_ev.get("tipo") == "entrevista_humana" and d_ev.get("con_cita"))
            params = parametros_plantilla_entrevista(extra["_datos_entrevista"]) if con_plantilla and extra.get("_datos_entrevista") else None
            resultados.append(await _enviar_y_registrar(
                db, c, evento, "entrevistador", "whatsapp", _whatsapp_entrevistador(db, eh), texto, plantilla_entrevista=params,
            ))
        if regla.entrevistador_correo:
            contenido = _mensaje(evento, "entrevistador", "correo", c, eh, liga, extra)
            resultados.append(await _enviar_y_registrar(
                db, c, evento, "entrevistador", "correo", _correo_entrevistador(db, eh), contenido
            ))

    # --- Cliente: TODOS los contactos ya registrados en ClienteContacto (punto 23) — si la
    # vacante no tiene Cliente, se ignora en silencio sin importar la regla (punto 26). ---
    cliente_id = c.vacante.cliente_id if c.vacante else None
    if cliente_id and (regla.cliente_whatsapp or regla.cliente_correo):
        q = db.query(ClienteContacto).filter(ClienteContacto.cliente_id == cliente_id)
        if regla.cliente_contactos_ids is not None:
            # Fase 7A: solo los contactos que RH marcó para esta acción (nunca se capturan datos nuevos)
            q = q.filter(ClienteContacto.id.in_(regla.cliente_contactos_ids))
        contactos = q.all()
        for contacto in contactos:
            if regla.cliente_whatsapp:
                texto = _mensaje(evento, "cliente", "whatsapp", c, eh, liga, extra)
                resultados.append(await _enviar_y_registrar(
                    db, c, evento, "cliente", "whatsapp", contacto.telefono, texto
                ))
            if regla.cliente_correo:
                contenido = _mensaje(evento, "cliente", "correo", c, eh, liga, extra)
                resultados.append(await _enviar_y_registrar(
                    db, c, evento, "cliente", "correo", contacto.correo, contenido
                ))

    db.flush()
    return resultados


# ------------------------------------------------------------
# 2026-09-19 — Vacante publicada (evento sin postulación): descripción HTML al Cliente y al responsable
# ------------------------------------------------------------


def datos_vacante(v) -> dict:
    from ..routers.vacantes import requisitos_lista  # import local: routers ↔ services

    return {
        "titulo": v.titulo, "empresa": nombre_empresa_candidato(v), "area": v.area or "", "ubicacion": v.ubicacion or "",
        "modalidad": v.modalidad or "", "sueldo": v.sueldo or "", "seniority": v.seniority or "",
        "resumen": v.resumen or "", "descripcion": v.descripcion or "",
        "requisitos": requisitos_lista(v.requisitos or ""), "beneficios": list(v.beneficios or []),
        "liga": f"{settings.app_url}/aplicar/{v.slug}" if v.slug else f"{settings.app_url}/portal",
        "publicada_por": "", "logo_url": "",
    }


async def notificar_vacante_publicada(db: Session, v, actor: str) -> List[dict]:
    """Al publicar una vacante: correo HTML con la descripción a los contactos del Cliente (regla
    `vacante_publicada`, default cliente_correo=True) y al responsable de la vacante. Nunca bloquea la
    publicación: cada envío regresa su resultado."""
    regla_guardada = _regla(db, v.cuenta_id, "vacante_publicada") if v.cuenta_id else None
    cliente_correo = bool(regla_guardada.cliente_correo) if regla_guardada else bool(REGLAS_NOTIFICACION_DEFAULT.get("vacante_publicada", {}).get("cliente_correo"))
    d = datos_vacante(v)
    d["publicada_por"] = actor
    asunto, html = plantillas_correo.html_vacante_publicada(d)
    destinos: List[tuple] = []
    if cliente_correo and v.cliente_id:
        for k in db.query(ClienteContacto).filter(ClienteContacto.cliente_id == v.cliente_id).all():
            if k.correo:
                destinos.append(("cliente", k.correo))
    if v.responsable and v.responsable.correo:
        destinos.append(("responsable", v.responsable.correo))
    resultados: List[dict] = []
    for tipo, correo in destinos:
        try:
            envio = await enviar_correo(correo, asunto, html)
        except Exception as ex:  # noqa: BLE001
            envio = {"enviado": False, "proveedor": "error", "detalle": str(ex)[:200]}
        db.add(NotificacionEnviada(
            cuenta_id=v.cuenta_id, candidato_id=None, evento="vacante_publicada", destinatario_tipo=tipo, canal="correo",
            destino=correo, enviado=bool(envio.get("enviado")), detalle=str(envio.get("detalle", "")),
        ))
        resultados.append({"destinatario": tipo, "canal": "correo", "destino": correo, **envio, "detalle": str(envio.get("detalle", ""))})
    return resultados
