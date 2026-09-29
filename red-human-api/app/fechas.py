"""Regla única de fechas (2026-09-29, bug de las 6 horas).

- La base guarda SIEMPRE UTC. SQLite descarta el offset de un `DateTime(timezone=True)`: se guarda
  "2026-09-30 13:29:00" y el ORM lo regresaba naive. `serial.iso()` lo mandaba sin zona y el navegador
  lo interpretaba como hora LOCAL → una cita de las 7:29 a.m. se veía a la 1:29 p.m.
- `FechaUTC` es el tipo de TODA columna de fecha-hora: al guardar convierte a UTC (una fecha con otra
  zona ya no se guarda con sus números de reloj) y al leer regresa siempre un datetime aware en UTC.
  No cambia el esquema ni los datos guardados (el `impl` es el mismo `DateTime(timezone=True)`).
- `iso()` serializa con «Z»; el frontend formatea con la zona de la organización (`lib/fechas.ts`).
- Hora de reloj capturada por RH (fecha + hora) → `desde_local()` con `TZ_ORG`.
"""

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator

from .config import settings

TZ_ORG = ZoneInfo(settings.zona_horaria or "America/Mexico_City")

# Etiqueta que acompaña TODA hora mostrada (formulario, tarjeta, liga, correo, WhatsApp) — especificación
# «Evaluaciones unificadas», sección 8. Misma tabla en red-human-app/lib/fechas.ts.
_ETIQUETAS_ZONA = {
    "America/Mexico_City": "hora de Ciudad de México",
    "America/Monterrey": "hora de Monterrey",
    "America/Merida": "hora de Mérida",
    "America/Cancun": "hora de Cancún",
    "America/Chihuahua": "hora de Chihuahua",
    "America/Mazatlan": "hora del Pacífico",
    "America/Hermosillo": "hora de Sonora",
    "America/Tijuana": "hora de Tijuana",
}
ETIQUETA_ZONA = _ETIQUETAS_ZONA.get(TZ_ORG.key, f"hora de {TZ_ORG.key}")


def con_zona(texto: str) -> str:
    """«07:29» → «07:29 (hora de Ciudad de México)»."""
    return f"{texto} ({ETIQUETA_ZONA})" if texto else texto


def a_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Naive = ya está en UTC (convención de la base); aware = se convierte."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    """ISO 8601 en UTC con «Z» — el navegador nunca debe adivinar la zona."""
    dt = a_utc(dt)
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def local(dt: Optional[datetime]) -> Optional[datetime]:
    """El mismo instante en la zona de la organización (para textos: correo, WhatsApp, PDF)."""
    dt = a_utc(dt)
    return dt.astimezone(TZ_ORG) if dt else None


def dia(dt: Optional[datetime]):
    """Un DÍA de calendario (fecha de ingreso, fecha límite, término: se guardan a medianoche UTC) → `date`
    sin convertir de zona. Convertirlo a México lo corre al día anterior."""
    dt = a_utc(dt)
    return dt.date() if dt else None


def desde_local(fecha: str, hora: str = "00:00") -> datetime:
    """«2026-09-30» + «07:29» capturados en la zona de la organización → instante en UTC."""
    return datetime.fromisoformat(f"{fecha[:10]}T{hora[:5]}").replace(tzinfo=TZ_ORG).astimezone(timezone.utc)


class FechaUTC(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return a_utc(value) if isinstance(value, datetime) else value

    def process_result_value(self, value, dialect):
        return a_utc(value) if isinstance(value, datetime) else value
