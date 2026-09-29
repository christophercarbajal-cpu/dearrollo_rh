"""Diagnóstico de citas corridas por el bug de las 6 horas (2026-09-29). SOLO LECTURA: no escribe nada.

Qué pasaba: el modal «Modificar entrevista» precargaba la hora UTC como si fuera hora de México (la API la
mandaba sin «Z»). Si RH guardaba sin corregir la hora, la cita quedaba desfasada: nueva = anterior + 6 h
(o + N días + 6 h si solo cambió el día). Una hora que RH tecleó a mano es correcta y no se toca.

Cómo detecta: recorre la bitácora (`entrevista_humana_programada` / `_modificada`, cada una trae la fecha
guardada) por postulación y compara cada modificación con la fecha anterior. Si la diferencia, módulo 24 h,
es exactamente el desfase de la zona en ese momento (6 h en México) la marca como PROBABLE. Imprime la hora
guardada, la hora anterior y la sugerida — la corrección la decide y la hace RH a mano.

Uso (desde red-human-api/, con el .env del entorno):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/diagnostico_citas_desfasadas.py [--todas]
    --todas  también lista las modificaciones que NO parecen desfasadas.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import fechas  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Bitacora, EntrevistaHumana, Evaluacion, Postulacion  # noqa: E402

ACCIONES = ("entrevista_humana_programada", "entrevista_humana_modificada")


def _leer(texto) -> datetime | None:
    try:
        return fechas.a_utc(datetime.fromisoformat(str(texto).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _ver(dt: datetime | None) -> str:
    return fechas.local(dt).strftime("%d/%m/%Y %H:%M") if dt else "—"


def main() -> None:
    todas = "--todas" in sys.argv
    db = SessionLocal()
    try:
        eventos = db.execute(
            select(Bitacora).where(Bitacora.accion.in_(ACCIONES)).order_by(Bitacora.entidad_id, Bitacora.id)
        ).scalars().all()
        anterior: dict[str, datetime | None] = {}
        probables, revisadas = [], 0
        for ev in eventos:
            nueva = _leer((ev.detalle or {}).get("fecha"))
            previa = anterior.get(ev.entidad_id)
            anterior[ev.entidad_id] = nueva
            if ev.accion != "entrevista_humana_modificada" or not nueva or not previa:
                continue
            revisadas += 1
            # desfase de la zona en la fecha anterior (México: -6 h todo el año desde 2022)
            desfase = -fechas.local(previa).utcoffset()
            corrimiento = (nueva - previa) % timedelta(days=1)
            fila = {
                "postulacion": ev.entidad_id, "bitacora_id": ev.id, "por": ev.actor, "cuando": _ver(ev.ts),
                "anterior": _ver(previa), "guardada": _ver(nueva), "sugerida": _ver(nueva - desfase),
                "probable": corrimiento == desfase,
            }
            if fila["probable"]:
                probables.append(fila)
            elif todas:
                print(f"  ok  {fila['postulacion']}  {fila['anterior']} → {fila['guardada']}  ({fila['por']}, {fila['cuando']})")

        print(f"\nModificaciones revisadas: {revisadas} · con desfase probable: {len(probables)}")
        print(f"Zona de la organización: {fechas.TZ_ORG.key}\n")
        for f in probables:
            p = db.execute(select(Postulacion).where(Postulacion.codigo == f["postulacion"])).scalar_one_or_none()
            actual = None
            if p:
                ehs = db.execute(
                    select(EntrevistaHumana).where(EntrevistaHumana.postulacion_id == p.id).order_by(EntrevistaHumana.id.desc())
                ).scalars().first()
                actual = ehs
            vigente = ""
            if actual is not None:
                # Evaluaciones unificadas (2026-09-29): la cita editable vive en `evaluaciones` (copiada tal cual)
                ev = db.execute(select(Evaluacion).where(Evaluacion.origen_tabla == "entrevistas_humanas",
                                                         Evaluacion.origen_id == actual.id)).scalars().first()
                if ev is not None:
                    estado = ev.estado
                    fecha_vigente = ev.cita_fecha_hora
                    donde = f"evaluación {ev.codigo}"
                else:
                    estado = "cancelada" if actual.cancelada else ("realizada" if actual.realizada else "vigente")
                    fecha_vigente = actual.fecha
                    donde = f"entrevista #{actual.id}"
                sigue = "SÍ" if fecha_vigente and _ver(fecha_vigente) == f["guardada"] else "no (se cambió después)"
                vigente = f" · {donde} {estado} · ¿la base conserva la hora desfasada? {sigue}"
            print(
                f"⚠ {f['postulacion']} (bitácora #{f['bitacora_id']}, {f['por']}, {f['cuando']})\n"
                f"    antes: {f['anterior']}  →  guardada: {f['guardada']}  →  probable real: {f['sugerida']}{vigente}"
            )
        if probables:
            print("\nNada se corrigió. Confirma con el entrevistador/candidato y usa «Modificar» ya con la versión corregida.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
