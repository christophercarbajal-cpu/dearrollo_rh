"""Multi-ubicación (Cambio 47, 2026-10-14).

* `Ubicacion` = sede donde se trabaja y se cita (terminal, CEDIS…): dirección, liga de Google Maps, indicaciones y
  `horarios_citas` [{dias: [0-6, lunes=0], horas: ["09:00", "12:00"], cupo: 8}].
* Una vacante tiene VARIAS ubicaciones (`VacanteUbicacion`, muchos-a-muchos). Con más de una, el candidato elige con
  `PREGUNTA_TERMINAL` (formulario web o prefiltro por WhatsApp) y la elección queda en `Postulacion.ubicacion_id`: de ahí
  salen el traslado, la dirección de las citas y la autoagenda.
* Cupo: un horario admite `cupo` reservas activas (`ReservaCita`). `reservar` vuelve a contar DESPUÉS de insertar, así
  dos candidatos que eligen el último lugar al mismo tiempo nunca lo sobrepasan.
"""

import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session, object_session
from sqlalchemy.orm.attributes import flag_modified

from ..fechas import TZ_ORG
from ..models import DIAS_SEMANA, Postulacion, ReservaCita, Ubicacion, VacanteUbicacion

PREGUNTA_TERMINAL = "¿En qué terminal te gustaría trabajar?"
ID_PREGUNTA = "ubicacion-terminal"
DIAS_OFRECIDOS = 3  # «los próximos 3 días hábiles con cupo»
DIAS_BUSQUEDA = 45  # hasta dónde se busca cupo
_DIAS_TEXTO = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre",
          "diciembre"]


class ErrorUbicacion(Exception):
    def __init__(self, status: int, mensaje: str):
        super().__init__(mensaje)
        self.status, self.mensaje = status, mensaje


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9: ]", " ", t).split())


# ============================================================ horarios

def _dia(x) -> Optional[int]:
    if isinstance(x, int) and 0 <= x <= 6:
        return x
    t = _norm(str(x))
    if t.isdigit() and 0 <= int(t) <= 6:
        return int(t)
    return next((i for i, d in enumerate(DIAS_SEMANA) if d.startswith(t[:3]) and len(t) >= 2), None)


def _hora(x) -> Optional[str]:
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*", str(x or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
        return None
    return f"{int(m.group(1)):02d}:{int(m.group(2) or 0):02d}"


def normalizar_horarios(entrada) -> List[dict]:
    """[{dias, horas, cupo}] válidos y ordenados. Un renglón inválido → ErrorUbicacion(400)."""
    salida = []
    for i, h in enumerate(entrada or []):
        if not isinstance(h, dict):
            raise ErrorUbicacion(400, f"Horario {i + 1}: formato inválido.")
        dias = sorted({d for d in (_dia(x) for x in h.get("dias") or []) if d is not None})
        horas = sorted({x for x in (_hora(y) for y in h.get("horas") or []) if x})
        try:
            cupo = int(h.get("cupo") or 0)
        except (TypeError, ValueError):
            cupo = 0
        if not dias or not horas:
            raise ErrorUbicacion(400, f"Horario {i + 1}: indica al menos un día y una hora.")
        if cupo < 1:
            raise ErrorUbicacion(400, f"Horario {i + 1}: el cupo debe ser 1 o más lugares por horario.")
        salida.append({"dias": dias, "horas": horas, "cupo": cupo})
    return salida


def texto_horarios(horarios: List[dict]) -> str:
    """«Lunes a viernes, 9:00 y 12:00 · 8 lugares por horario»."""
    partes = []
    for h in horarios or []:
        dias = h.get("dias") or []
        if dias == list(range(dias[0], dias[-1] + 1)) and len(dias) > 2:
            d = f"{_DIAS_TEXTO[dias[0]].capitalize()} a {_DIAS_TEXTO[dias[-1]]}"
        else:
            d = ", ".join(_DIAS_TEXTO[x] for x in dias).capitalize()
        horas = [x.lstrip("0") if not x.startswith("00") else x for x in h.get("horas") or []]
        partes.append(f"{d}, {' y '.join([', '.join(horas[:-1]), horas[-1]] if len(horas) > 1 else horas)} · "
                      f"{h.get('cupo')} lugar{'es' if h.get('cupo') != 1 else ''} por horario")
    return " | ".join(partes)


def liga_mapa(u: Ubicacion) -> str:
    if (u.mapa or "").strip():
        return u.mapa.strip()
    from .citas import liga_mapa as armar

    from .opciones_vacante import direccion_util

    contexto = ", ".join(x for x in (u.ubicacion_municipio, u.ubicacion_estado) if x)
    return armar(direccion_util(u) or u.nombre, contexto)  # 2026-10-16: «Conocido, …» no es dirección para el mapa


def ubicacion_dict(u: Ubicacion) -> dict:
    return {"id": u.id, "nombre": u.nombre, "direccion": u.direccion or "", "mapa": liga_mapa(u), "mapaCapturado": u.mapa or "",
            "indicaciones": u.indicaciones or "", "estado": u.ubicacion_estado or "", "municipio": u.ubicacion_municipio or "",
            "horarios": list(u.horarios_citas or []), "horariosTexto": texto_horarios(u.horarios_citas or []), "activa": bool(u.activa)}


# ============================================================ vacante ↔ ubicaciones

def de_vacante(db: Optional[Session], v) -> List[Ubicacion]:
    """Ubicaciones ACTIVAS de la vacante, en su orden."""
    if v is None or getattr(v, "id", None) is None:
        return []
    db = db or object_session(v)
    if db is None:
        return []
    try:
        return (db.query(Ubicacion).join(VacanteUbicacion, VacanteUbicacion.ubicacion_id == Ubicacion.id)
                .filter(VacanteUbicacion.vacante_id == v.id, Ubicacion.activa.is_(True))
                .order_by(VacanteUbicacion.orden, VacanteUbicacion.id).all())
    except Exception:  # noqa: BLE001 — tabla del paso NO fatal sin crear
        return []


def asignar(db: Session, v, ids: List[int]) -> List[Ubicacion]:
    """Reemplaza las ubicaciones de la vacante (solo de su misma Cuenta y activas)."""
    ids = list(dict.fromkeys(int(x) for x in ids or []))
    ubics = db.query(Ubicacion).filter(Ubicacion.id.in_(ids or [0]), Ubicacion.cuenta_id == v.cuenta_id).all() if ids else []
    if len(ubics) != len(ids):
        raise ErrorUbicacion(400, "Alguna ubicación no existe en esta Cuenta.")
    if any(not u.activa for u in ubics):
        raise ErrorUbicacion(400, "No puedes asignar una ubicación desactivada.")
    db.query(VacanteUbicacion).filter(VacanteUbicacion.vacante_id == v.id).delete(synchronize_session=False)
    for i, uid in enumerate(ids):
        db.add(VacanteUbicacion(vacante_id=v.id, ubicacion_id=uid, orden=i))
    db.flush()
    por_id = {u.id: u for u in ubics}
    salida = [por_id[i] for i in ids]
    if salida:
        # `Vacante.ubicacion` (texto que leen portal, WhatsApp y publicaciones) resume las sedes
        v.ubicacion = salida[0].nombre if len(salida) == 1 else " · ".join(u.nombre for u in salida)
    return salida


def pide_terminal(db: Optional[Session], v) -> bool:
    return len(de_vacante(db, v)) > 1


def pregunta_web(db: Optional[Session], v) -> Optional[dict]:
    """Pregunta de opción del formulario web («¿En qué [sede/terminal…] te gustaría trabajar?», vocabulario de la Cuenta,
    2026-10-16). Nunca descarta."""
    from .opciones_vacante import pregunta_ubicacion

    ubics = de_vacante(db, v)
    if len(ubics) < 2:
        return None
    return {"id": ID_PREGUNTA, "pregunta": pregunta_ubicacion(v), "tipo": "opcion", "valida": "Ubicación elegida",
            "respuesta_esperada": "", "descarta": False, "opciones": [u.nombre for u in ubics], "ubicacion": True, "base": True}


def criterio_chat(db: Optional[Session], v) -> Optional[dict]:
    """Criterio del prefiltro conversacional. Las opciones se dicen en una frase natural (no lista numerada)."""
    from .opciones_vacante import pregunta_ubicacion

    ubics = de_vacante(db, v)
    if len(ubics) < 2:
        return None
    cortos = [corto(u) for u in ubics]
    lista = ", ".join(cortos[:-1]) + f" y {cortos[-1]}"
    pregunta = pregunta_ubicacion(v)
    return {"id": ID_PREGUNTA, "clave": "terminal", "dato": "terminal", "criterio": "Ubicación elegida",
            "pregunta": pregunta, "chat": f"{pregunta} Tenemos {lista}.", "indispensable": False,
            "esperada": "si", "tipo": "dato", "opciones": [u.nombre for u in ubics], "ubicaciones": [opcion(u) for u in ubics],
            "registra": True,
            "repregunta": f"¿Cuál prefieres: {lista}?"}


def corto(u: Ubicacion) -> str:
    """«Terminal Monterrey» → «Monterrey» (para decirlo en una frase)."""
    n = re.sub(r"^(terminal|cedis|sucursal|planta|sede|oficina|campus|plantel|tienda|bodega)\s+", "", (u.nombre or "").strip(),
               flags=re.IGNORECASE)
    return n or u.nombre


def opcion(u: Ubicacion) -> dict:
    """{id, nombre, claves}: con qué palabras el candidato puede nombrar esta ubicación."""
    claves = {_norm(corto(u)), _norm(u.ubicacion_municipio), _norm(u.ubicacion_estado)}
    if "ciudad de mexico" in claves or "cdmx" in claves:
        claves |= {"cdmx", "df", "ciudad de mexico"}
    return {"id": u.id, "nombre": u.nombre, "claves": sorted(c for c in claves if c)}


def elegir_en(opciones: List[dict], texto: str) -> Optional[dict]:
    """La opción que nombra el candidato (nombre completo, nombre corto, municipio o estado). Ambigua → None."""
    t = f" {_norm(texto)} "
    if not t.strip():
        return None
    exactas = [o for o in opciones if f" {_norm(o['nombre'])} " in t]
    if len(exactas) == 1:
        return exactas[0]
    cand = [o for o in opciones if any(f" {c} " in t for c in o.get("claves") or [])]
    return cand[0] if len(cand) == 1 else None


def elegir(ubics: List[Ubicacion], texto: str) -> Optional[Ubicacion]:
    o = elegir_en([opcion(u) for u in ubics], texto)
    return next((u for u in ubics if o and u.id == o["id"]), None)


def ubicacion_de(db: Optional[Session], p: Postulacion) -> Optional[Ubicacion]:
    """La que eligió el candidato; si la vacante tiene una sola, esa."""
    db = db or object_session(p) or (object_session(p.vacante) if getattr(p, "vacante", None) is not None else None)
    if db is None:
        return None
    if p.ubicacion_id:
        try:
            u = db.get(Ubicacion, p.ubicacion_id)
        except Exception:  # noqa: BLE001
            u = None
        if u is not None:
            return u
    ubics = de_vacante(db, p.vacante)
    return ubics[0] if len(ubics) == 1 else None


def fijar(p: Postulacion, u, fuente: str) -> None:
    """Guarda la elección (`Ubicacion` u opción {id, nombre}) en la ficha: columna + análisis + historial. No hace commit."""
    uid, nombre = (u["id"], u["nombre"]) if isinstance(u, dict) else (u.id, u.nombre)
    p.ubicacion_id = uid
    a = dict(p.analisis or {})
    a["ubicacion"] = {"id": uid, "nombre": nombre, "fuente": fuente, "en": datetime.now(timezone.utc).isoformat()}
    p.analisis = a
    flag_modified(p, "analisis")
    p.historial = list(p.historial or []) + [{"evento": "ubicacion_elegida", "usuario": "candidato", "fecha": a["ubicacion"]["en"],
                                              "texto": f"Eligió trabajar en {nombre} ({fuente})", "ubicacion": nombre}]


def desde_respuestas_web(db: Session, p: Postulacion, respuestas: List[dict]) -> Optional[Ubicacion]:
    """La respuesta del formulario web a la pregunta de ubicación (si la vacante pide elegir)."""
    from .opciones_vacante import pregunta_ubicacion

    ubics = de_vacante(db, p.vacante)
    if len(ubics) < 2:
        return None
    textos = {_norm(PREGUNTA_TERMINAL), _norm(pregunta_ubicacion(p.vacante))}
    r = next((x for x in respuestas or [] if _norm(x.get("pregunta")) in textos), None)
    u = elegir(ubics, (r or {}).get("respuesta", "")) if r else None
    if u is not None:
        fijar(p, u, "formulario web")
    return u


# ============================================================ cupos y reservas

def horas_del_dia(u: Ubicacion, dia: date) -> List[Tuple[str, int]]:
    """[("09:00", cupo), …] del día de la semana (si dos renglones dan la misma hora, gana el cupo mayor)."""
    horas = {}
    for h in u.horarios_citas or []:
        if dia.weekday() in (h.get("dias") or []):
            for x in h.get("horas") or []:
                horas[x] = max(horas.get(x, 0), int(h.get("cupo") or 0))
    return sorted(horas.items())


def _inicio(dia: date, hora: str) -> datetime:
    hh, mm = (int(x) for x in hora.split(":"))
    return datetime.combine(dia, time(hh, mm), tzinfo=TZ_ORG).astimezone(timezone.utc)


def ocupados(db: Session, u: Ubicacion, inicio: datetime) -> int:
    return (db.query(ReservaCita).filter(ReservaCita.ubicacion_id == u.id, ReservaCita.inicio == inicio,
                                         ReservaCita.estado == "activa").count())


def horarios_libres(db: Session, u: Ubicacion, dia: date, ahora: Optional[datetime] = None) -> List[dict]:
    """Horarios del día con lugar y a futuro: [{hora, inicio (UTC), libres}]. Un horario lleno no aparece."""
    ahora = ahora or datetime.now(timezone.utc)
    salida = []
    for hora, cupo in horas_del_dia(u, dia):
        inicio = _inicio(dia, hora)
        if inicio <= ahora + timedelta(hours=1):
            continue
        libres = cupo - ocupados(db, u, inicio)
        if libres > 0:
            salida.append({"hora": hora, "inicio": inicio, "libres": libres})
    return salida


def dias_disponibles(db: Session, u: Ubicacion, n: int = DIAS_OFRECIDOS, ahora: Optional[datetime] = None) -> List[date]:
    """Los próximos `n` días HÁBILES (lunes a viernes) a partir de mañana con al menos un horario con lugar."""
    ahora = ahora or datetime.now(timezone.utc)
    dia = ahora.astimezone(TZ_ORG).date()
    salida = []
    for _ in range(DIAS_BUSQUEDA):
        dia += timedelta(days=1)
        if dia.weekday() >= 5:
            continue
        if horarios_libres(db, u, dia, ahora):
            salida.append(dia)
            if len(salida) >= n:
                break
    return salida


def reservar(db: Session, p: Postulacion, u: Ubicacion, inicio: datetime, pasos: List[str]) -> Optional[ReservaCita]:
    """Ocupa UN lugar (resta 1 al cupo). None si el horario ya se llenó (se vuelve a contar después de insertar)."""
    cupo = dict(horas_del_dia(u, inicio.astimezone(TZ_ORG).date())).get(inicio.astimezone(TZ_ORG).strftime("%H:%M"), 0)
    if cupo <= 0 or ocupados(db, u, inicio) >= cupo:
        return None
    r = ReservaCita(cuenta_id=p.cuenta_id, ubicacion_id=u.id, postulacion_id=p.id, inicio=inicio, pasos=list(pasos), estado="activa")
    db.add(r)
    db.flush()
    if ocupados(db, u, inicio) > cupo:  # otro candidato tomó el último lugar al mismo tiempo
        db.delete(r)
        db.flush()
        return None
    return r


def reserva_activa(db: Session, p: Postulacion) -> Optional[ReservaCita]:
    try:
        return (db.query(ReservaCita).filter(ReservaCita.postulacion_id == p.id, ReservaCita.estado == "activa",
                                             ReservaCita.inicio > datetime.now(timezone.utc))
                .order_by(ReservaCita.inicio).first())
    except Exception:  # noqa: BLE001
        return None


def cancelar(r: ReservaCita, motivo: str) -> None:
    """Libera el lugar (vuelve a sumar 1 al cupo)."""
    r.estado = "cancelada"
    r.motivo_cancelacion = (motivo or "")[:300]


# ============================================================ textos

def texto_dia(d: date) -> str:
    return f"{_DIAS_TEXTO[d.weekday()]} {d.day} de {_MESES[d.month - 1]}"


def texto_hora(hora: str) -> str:
    hh, mm = (int(x) for x in hora.split(":"))
    return f"{hh % 12 or 12}:{mm:02d} {'a.m.' if hh < 12 else 'p.m.'}"
