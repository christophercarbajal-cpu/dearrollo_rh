"""Vistas previas de los correos corporativos (2026-09-18) — para revisar el diseño en el navegador y, desde el
modal de Entrevista Humana, ver el correo EXACTO con los datos capturados (query params). Sin sesión, sin enviar.

    GET /api/emails/preview/entrevistador
    GET /api/emails/preview/candidato

Query params (todos opcionales; sin ninguno se usan datos de prueba):
    evento=agendada|modificada|recordatorio|cancelada · candidato · entrevistador · vacante · empresa ·
    fecha (ISO «2026-09-24» o «2026-09-24T10:30») · hora («10:30») · modalidad · liga · ubicacion · telefono ·
    telefono_candidato · comentario · liga_expediente · json=1 (regresa asunto/datos/parámetros de Meta)."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import settings
from ..services import plantillas_correo as pc

router = APIRouter(prefix="/api/emails/preview", tags=["emails-preview"])


def _fecha_hora(fecha: str, hora: str) -> tuple[str, str]:
    """«2026-09-24» + «10:30» (o ISO completo) → («jueves 24 de septiembre de 2026», «10:30 h») en hora de México."""
    f = (fecha or "").strip()
    h = (hora or "").strip()
    if not f:
        return "", (h + " h" if h and not h.endswith("h") else h)
    try:
        if "T" in f:
            dt = datetime.fromisoformat(f)
        else:
            dt = datetime.fromisoformat(f + "T" + (h or "00:00"))
        from ..fechas import TZ_ORG

        dt = dt.replace(tzinfo=TZ_ORG)
        texto_fecha, texto_hora = pc.fecha_hora_mx(dt)
        return texto_fecha, (texto_hora if h or "T" in f else "")
    except ValueError:
        return f, (h + " h" if h and not h.endswith("h") else h)


def _datos(
    evento: str, candidato: Optional[str], entrevistador: Optional[str], vacante: Optional[str], empresa: Optional[str],
    fecha: Optional[str], hora: Optional[str], modalidad: Optional[str], liga: Optional[str], ubicacion: Optional[str],
    telefono: Optional[str], telefono_candidato: Optional[str], comentario: Optional[str], liga_expediente: Optional[str],
) -> dict:
    # `modalidad` sola no cuenta como dato real: solo cambia la variante del ejemplo
    reales = any(x is not None for x in (candidato, entrevistador, vacante, empresa, fecha, hora, liga, ubicacion, telefono, comentario))
    if not reales:
        d = dict(pc.MOCK_ENTREVISTA)
        m = (modalidad or d["modalidad"])
        if m == "Presencial":
            d.update({"modalidad": "Presencial", "liga_conexion": "", "detalle_conexion": "Ubicación: Av. Reforma 222, piso 8, CDMX", "ubicacion": "Av. Reforma 222, piso 8, CDMX"})
        elif m == "Llamada":
            d.update({"modalidad": "Llamada", "liga_conexion": "", "detalle_conexion": "Te contactaremos al 55 1234 5678", "telefono_contacto": "55 1234 5678"})
        return d
    fecha_txt, hora_txt = _fecha_hora(fecha or "", hora or "")
    m = modalidad or "Videollamada"
    liga_c = (liga or "").strip() if m == "Videollamada" else ""
    if m == "Videollamada":
        detalle = f"Liga de la videollamada: {liga_c}" if liga_c else "Liga por confirmar (se genera al programar)"
    elif m == "Presencial":
        detalle = f"Ubicación: {ubicacion}" if ubicacion else "Ubicación por confirmar"
    else:
        detalle = f"Te contactaremos al {telefono or telefono_candidato or 'tu teléfono'}"
    return {
        "entrevistador": entrevistador or "",
        "candidato": candidato or "",
        "vacante": vacante or "",
        "empresa": empresa or "",
        "fecha": fecha_txt,
        "hora": hora_txt,
        "modalidad": m,
        "detalle_conexion": detalle,
        "liga_conexion": liga_c,
        "ubicacion": ubicacion or "",
        "telefono_contacto": telefono or "",
        "telefono_candidato": telefono_candidato or "",
        "comentario": comentario or "",
        "liga_expediente": liga_expediente or f"{settings.app_url}/entrevista-humana/…",
        "logo_url": "",
    }


def _params_unused():
    return dict(
        evento=Query("agendada"), candidato=Query(None), entrevistador=Query(None), vacante=Query(None), empresa=Query(None),
        fecha=Query(None), hora=Query(None), modalidad=Query(None), liga=Query(None), ubicacion=Query(None), telefono=Query(None),
        telefono_candidato=Query(None), comentario=Query(None), liga_expediente=Query(None), json=Query(0),
    )


@router.get("/entrevistador", response_class=HTMLResponse)
def preview_entrevistador(
    evento: str = Query("agendada"), candidato: Optional[str] = Query(None), entrevistador: Optional[str] = Query(None),
    vacante: Optional[str] = Query(None), empresa: Optional[str] = Query(None), fecha: Optional[str] = Query(None),
    hora: Optional[str] = Query(None), modalidad: Optional[str] = Query(None), liga: Optional[str] = Query(None),
    ubicacion: Optional[str] = Query(None), telefono: Optional[str] = Query(None), telefono_candidato: Optional[str] = Query(None),
    comentario: Optional[str] = Query(None), liga_expediente: Optional[str] = Query(None), json: int = Query(0),
):
    d = _datos(evento, candidato, entrevistador, vacante, empresa, fecha, hora, modalidad, liga, ubicacion, telefono, telefono_candidato, comentario, liga_expediente)
    asunto, html = pc.html_entrevistador(d, evento)
    if json:
        return JSONResponse({"asunto": asunto, "evento": evento, "datos": d, "parametros_meta": [d["entrevistador"], d["candidato"], d["vacante"], d["fecha"], d["hora"], d["liga_expediente"]]})
    return HTMLResponse(html)


@router.get("/candidato", response_class=HTMLResponse)
def preview_candidato(
    evento: str = Query("agendada"), candidato: Optional[str] = Query(None), entrevistador: Optional[str] = Query(None),
    vacante: Optional[str] = Query(None), empresa: Optional[str] = Query(None), fecha: Optional[str] = Query(None),
    hora: Optional[str] = Query(None), modalidad: Optional[str] = Query(None), liga: Optional[str] = Query(None),
    ubicacion: Optional[str] = Query(None), telefono: Optional[str] = Query(None), telefono_candidato: Optional[str] = Query(None),
    comentario: Optional[str] = Query(None), liga_expediente: Optional[str] = Query(None), json: int = Query(0),
):
    d = _datos(evento, candidato, entrevistador, vacante, empresa, fecha, hora, modalidad, liga, ubicacion, telefono, telefono_candidato, comentario, liga_expediente)
    asunto, html = pc.html_candidato(d, evento)
    if json:
        return JSONResponse({"asunto": asunto, "evento": evento, "datos": d})
    return HTMLResponse(html)
