"""Citas presenciales (2026-10-10, Cambio 2).

Aplica a: Entrevista humana, Evaluación médica, Psicometría física y Evaluación técnica (`requiere_cita`).

* Bloqueo de inicio: sin «Programar cita» la actividad queda «Pendiente de agendar» — no se crea la evaluación ni se
  avisa a nadie (`actividades.iniciar` regresa `faltan: ["cita"]`).
* La cita guarda además: «Hasta» (rango de hora, `Evaluacion.cita_hasta`), la liga de mapa AUTOGENERADA con la dirección
  (`cita_mapa`) y hasta 5 adjuntos (imágenes o PDF de máx. 10 MB, `cita_adjuntos`) que se mandan TAL CUAL al candidato:
  nunca pasan por la IA.
* Aviso al candidato con texto fijo (`texto_candidato`): fecha, rango de hora, dirección, liga de mapa y adjuntos.
  Reprogramar avisa a candidato y evaluador.

Ajustes tras la prueba del 2026-10-10 («Citas: ajustes y mejoras»):
* Indicaciones («📝 Indicaciones: …», `Evaluacion.instrucciones`) y «👤 Pregunta por: [evaluador]» en el aviso.
* Editar una cita = UN solo aviso («Tu cita cambió» + los datos nuevos) y los adjuntos NO se reenvían.
* Nunca una cita en el pasado; «Hasta» estrictamente posterior a la hora.
* Fotos (JPG/PNG) salen como imagen con vista previa; PDF como documento.
* Programar otra cita para la MISMA actividad cancela la anterior y avisa al candidato.
* La liga de mapa concatena la ubicación de la vacante (municipio, estado) cuando la dirección no la trae; lo que RH
  escribió no cambia (`direccion_para_mapa`).
* `ultima_cita_vacante`: precarga del formulario con la última cita de la vacante (sin fecha ni hora).
"""

import os
import secrets
import unicodedata
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


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return " ".join("".join(ch for ch in s if not unicodedata.combining(ch)).lower().split())


def direccion_para_mapa(direccion: str, contexto: str = "") -> str:
    """Dirección que se busca en el mapa: la que escribió RH + la ubicación de la vacante (municipio, estado) que la
    dirección no mencione. Lo que RH escribió nunca cambia; solo la búsqueda."""
    d = " ".join((direccion or "").split())
    if not d:
        return ""
    faltan = [x.strip() for x in (contexto or "").split(",") if x.strip() and _norm(x) not in _norm(d)]
    return f"{d}, {', '.join(faltan)}" if faltan else d


def contexto_vacante(v) -> str:
    """Ubicación de la vacante para afinar el mapa («Zapopan, Jalisco»)."""
    if v is None:
        return ""
    from ..models import texto_ubicacion

    return texto_ubicacion(getattr(v, "ubicacion_estado", "") or "", getattr(v, "ubicacion_municipio", "") or "", getattr(v, "ubicacion", "") or "")


def liga_mapa(direccion: str, contexto: str = "") -> str:
    """Liga de Google Maps generada con la dirección (nunca se captura a mano)."""
    d = direccion_para_mapa(direccion, contexto)
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(d)}" if d else ""


def en_pasado(cuando: Optional[datetime]) -> bool:
    return cuando is not None and cuando <= datetime.now(timezone.utc)


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
              "evaluacion_reprogramada": f"Hola {nombre}, tu cita cambió. Estos son los nuevos datos de tu {actividad} para {puesto}{f' en {empresa}' if empresa else ''}:",
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
    if d.get("evaluador"):
        lineas.append(f"👤 Pregunta por: {d['evaluador']}")
    if d.get("instrucciones"):
        lineas.append(f"📝 Indicaciones: {d['instrucciones']}")
    n = int(d.get("adjuntos") or 0)
    if n and evento == "evaluacion_asignada":  # al editar la cita los adjuntos no se reenvían
        lineas.append(f"📎 Te enviamos {n} archivo{'s' if n > 1 else ''} con información de la cita.")
    lineas.append("Si no puedes asistir, respóndenos por aquí para reprogramar.")
    return "\n".join(lineas)


def texto_cancelacion_reemplazo(nombre: str, actividad: str, puesto: str, d: dict) -> str:
    """La cita anterior se canceló porque RH programó otra para la misma actividad (los datos nuevos llegan aparte)."""
    cuando = " ".join(x for x in (d.get("fecha_texto"), f"a las {d['horario_texto']}" if d.get("horario_texto") else "") if x)
    return (f"Hola {nombre}, cancelamos tu cita de {actividad} para {puesto}{f' del {cuando}' if cuando else ''}. "
            "En seguida te enviamos los datos de tu nueva cita.")


def ultima_cita_vacante(db, p, tipo: str = "") -> Optional[dict]:
    """Precarga del formulario de cita: la ÚLTIMA cita agendada en esta vacante (mismo tipo primero). Regresa evaluador,
    modalidad, dirección, indicaciones y adjuntos; nunca fecha ni hora."""
    from ..models import Evaluacion

    if not p.vacante_id:
        return None
    base = db.query(Evaluacion).filter(Evaluacion.cuenta_id == p.cuenta_id, Evaluacion.vacante_id == p.vacante_id,
                                       Evaluacion.cita_fecha_hora.isnot(None))
    ev = None
    if tipo:
        ev = base.filter(Evaluacion.tipo == tipo).order_by(Evaluacion.id.desc()).first()
    ev = ev or base.order_by(Evaluacion.id.desc()).first()
    if ev is None:
        return None
    instrucciones = "\n".join(x for x in (ev.instrucciones or "").splitlines() if not x.startswith("Examen solicitado:")).strip()
    evaluador = None
    if ev.forma == "asignada" and ev.evaluador_tipo in ("interno", "externo"):
        evaluador = {"tipo": ev.evaluador_tipo, "usuario_id": ev.evaluador_usuario_id, "contacto_id": ev.evaluador_contacto_id,
                     "nombre": ev.evaluador_nombre or "", "correo": ev.evaluador_correo or "", "whatsapp": ev.evaluador_whatsapp or ""}
    return {"evaluacion": ev.codigo, "tipo": ev.tipo, "evaluador": evaluador, "modalidad": ev.cita_modalidad or "",
            "direccion": ev.cita_direccion or "", "instrucciones": instrucciones, "adjuntos": adjuntos_publicos(ev.cita_adjuntos)}


def adjuntos_de_referencia(db, p, ref) -> List[dict]:
    """Adjuntos de una cita ANTERIOR de la misma vacante que RH decidió reutilizar ({evaluacion, ids}). Se valida que la
    evaluación sea de la misma Cuenta y vacante; se copia la metadata con ids nuevos (los archivos en disco se comparten:
    quitar un adjunto nunca borra el archivo)."""
    from ..models import Evaluacion

    if not isinstance(ref, dict) or not ref.get("evaluacion") or not p.vacante_id:
        return []
    ids = {str(i) for i in (ref.get("ids") or [])}
    ev = db.query(Evaluacion).filter(Evaluacion.codigo == str(ref["evaluacion"]), Evaluacion.cuenta_id == p.cuenta_id,
                                     Evaluacion.vacante_id == p.vacante_id).first()
    if ev is None or not ids:
        return []
    salida = []
    for a in sanear_metadata(ev.cita_adjuntos):
        if a["id"] in ids and fs.existe(a["archivo"]):
            salida.append({**a, "id": secrets.token_hex(4)})
    return salida[:MAX_ADJUNTOS]


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
