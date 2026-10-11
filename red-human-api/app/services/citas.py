"""Citas presenciales (2026-10-10, Cambio 2).

Aplica a: Entrevista humana, Evaluación médica, Psicometría física y Evaluación técnica (`requiere_cita`).

* Bloqueo de inicio: sin «Programar cita» la actividad queda «Pendiente de agendar» — no se crea la evaluación ni se
  avisa a nadie (`actividades.iniciar` regresa `faltan: ["cita"]`).
* La cita guarda además: «Hasta» (rango de hora, `Evaluacion.cita_hasta`), la liga de mapa AUTOGENERADA con la dirección
  (`cita_mapa`) y hasta 5 adjuntos (imágenes o PDF de máx. 10 MB, `cita_adjuntos`) que se mandan TAL CUAL al candidato:
  nunca pasan por la IA.
* Aviso al candidato con texto fijo (`texto_candidato`): fecha, rango de hora, dirección, liga de mapa y adjuntos.
  Reprogramar avisa a candidato y evaluador.
"""

import os
import secrets
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import quote_plus

from fastapi import HTTPException

from .. import fechas
from . import archivos as fs

TIPOS_CITA_PRESENCIAL = ("entrevista_humana", "medica", "tecnica")
MAX_ADJUNTOS = 5
FORMATOS_ADJUNTO_CITA = {k: v for k, v in fs.FORMATOS.items()}  # PDF e imágenes (png, jpg, jpeg, webp)
TEXTO_PENDIENTE = "Pendiente de agendar"


def requiere_cita(paso: Optional[dict]) -> bool:
    """Entrevista humana, médica, técnica y psicometría FÍSICA se agendan antes de iniciarse."""
    if not paso:
        return False
    tipo = paso.get("tipo")
    if tipo == "psicometrica":
        return paso.get("modalidad") == "fisica"
    if tipo == "tecnica" and ((paso.get("config") or {}).get("forma") == "liga_otro_sistema"):
        return False  # una técnica en línea (liga de otro sistema) no tiene cita
    return tipo in TIPOS_CITA_PRESENCIAL


def tiene_cita(config: Optional[dict]) -> bool:
    c = (config or {}).get("cita")
    return isinstance(c, dict) and bool(c.get("fecha")) and bool(c.get("hora"))


def liga_mapa(direccion: str) -> str:
    """Liga de Google Maps generada con la dirección (nunca se captura a mano)."""
    d = " ".join((direccion or "").split())
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(d)}" if d else ""


def _hora(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    t = fechas.local(dt)
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d} {'a.m.' if t.hour < 12 else 'p.m.'}"


def texto_horario(inicio: Optional[datetime], fin: Optional[datetime] = None) -> str:
    """«9:00 a.m.» o «de 9:00 a.m. a 11:00 a.m.» (hora de la organización)."""
    if inicio is None:
        return ""
    return f"de {_hora(inicio)} a {_hora(fin)}" if fin else _hora(inicio)


def texto_fecha(inicio: Optional[datetime]) -> str:
    if inicio is None:
        return ""
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    t = fechas.local(inicio)
    return f"{dias[t.weekday()]} {t.day} de {meses[t.month - 1]}"


def texto_candidato(evento: str, nombre: str, actividad: str, puesto: str, empresa: str, d: dict) -> str:
    """Aviso de la cita al candidato (texto fijo): fecha, rango de hora, lugar, liga de mapa y adjuntos. Sin
    valoraciones ni promesas."""
    inicio = {"evaluacion_asignada": f"Hola {nombre}, tu {actividad} para {puesto}{f' en {empresa}' if empresa else ''} quedó programada.",
              "evaluacion_reprogramada": f"Hola {nombre}, cambiamos tu {actividad} para {puesto}{f' en {empresa}' if empresa else ''}. Estos son los nuevos datos.",
              "recordatorio_evaluacion": f"Hola {nombre}, te recordamos tu {actividad} para {puesto}{f' en {empresa}' if empresa else ''}."}[evento]
    lineas = [inicio, f"📅 Fecha: {d.get('fecha_texto')}", f"🕘 Hora: {d.get('horario_texto')} ({fechas.ETIQUETA_ZONA})"]
    if d.get("modalidad") == "Presencial":
        if d.get("direccion"):
            lineas.append(f"📍 Lugar: {d['direccion']}")
        if d.get("mapa"):
            lineas.append(f"🗺️ Cómo llegar: {d['mapa']}")
    elif d.get("modalidad") == "Videollamada" and d.get("liga_videollamada"):
        lineas.append(f"💻 Liga: {d['liga_videollamada']}")
    elif d.get("modalidad") == "Teléfono":
        lineas.append("📞 Te llamaremos a este número.")
    if d.get("instrucciones"):
        lineas.append(f"📝 Indicaciones: {d['instrucciones']}")
    n = int(d.get("adjuntos") or 0)
    if n:
        lineas.append(f"📎 Te enviamos {n} archivo{'s' if n > 1 else ''} con información de la cita.")
    lineas.append("Si no puedes asistir, respóndenos por aquí para reprogramar.")
    return "\n".join(lineas)


# ------------------------------------------------------------ adjuntos (sin IA)


async def guardar_adjuntos(archivos, carpeta: str, actor: str, existentes: Optional[list] = None) -> List[dict]:
    """Valida (PDF o imagen, ≤ 10 MB, firma binaria) y guarda; regresa la metadata a guardar. Máximo 5 por cita
    (contando los que ya tenía). Los archivos nunca se mandan a la IA."""
    nuevos = [a for a in (archivos or []) if a is not None and getattr(a, "filename", "")]
    if len(list(existentes or [])) + len(nuevos) > MAX_ADJUNTOS:
        raise HTTPException(400, f"Una cita admite hasta {MAX_ADJUNTOS} adjuntos.")
    salida = []
    for a in nuevos:
        v = fs.validar_bytes(await a.read(), a.filename, f"adjunto «{a.filename}»", FORMATOS_ADJUNTO_CITA)
        ruta = fs.guardar(v, carpeta, f"cita_{secrets.token_hex(5)}")
        salida.append({"id": secrets.token_hex(4), "archivo": ruta, "nombre": v.nombre, "mime": v.mime, "tamano": v.tamano,
                       "subido_por": actor[:150], "subido_en": fechas.iso(datetime.now(timezone.utc))})
    return salida


def leer_adjuntos(lista: Optional[list]) -> List[dict]:
    """[{filename, content, mime}] de los adjuntos que siguen en disco (los que faltan se omiten, nunca truena)."""
    salida = []
    for a in lista or []:
        ruta = (a or {}).get("archivo") or ""
        if not fs.existe(ruta) or not os.path.realpath(ruta).startswith(os.path.realpath(fs.RAIZ_UPLOADS)):
            continue
        with open(ruta, "rb") as f:
            salida.append({"filename": a.get("nombre") or os.path.basename(ruta), "content": f.read(), "mime": a.get("mime") or "application/pdf"})
    return salida


def adjuntos_publicos(lista: Optional[list]) -> List[dict]:
    """Lo que ve RH (sin la ruta en disco)."""
    return [{"id": a.get("id"), "nombre": a.get("nombre"), "mime": a.get("mime"), "tamano": a.get("tamano"),
             "subidoPor": a.get("subido_por"), "subidoEn": a.get("subido_en")} for a in (lista or []) if isinstance(a, dict)]


def sanear_metadata(lista) -> List[dict]:
    """Solo metadata generada por `guardar_adjuntos` (rutas dentro de uploads/); nunca una ruta arbitraria."""
    raiz = os.path.realpath(fs.RAIZ_UPLOADS)
    salida = []
    for a in lista or []:
        if isinstance(a, dict) and a.get("id") and os.path.realpath(str(a.get("archivo") or "")).startswith(raiz):
            salida.append({k: a.get(k) for k in ("id", "archivo", "nombre", "mime", "tamano", "subido_por", "subido_en")})
    return salida[:MAX_ADJUNTOS]
