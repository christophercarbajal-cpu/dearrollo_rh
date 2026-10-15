"""Cambio 50 (2026-10-15) — «Prefiltro web» sale del catálogo: la Solicitud web («Unas preguntas») ES el prefiltro.

Quita la actividad de plantillas y vacantes (sube su versión) y resuelve a los candidatos ACTIVOS detenidos en su
«Prefiltro web» congelado: con respuestas web se evalúan (lo que coincidiría con «Descarta si» va a Puntos por validar,
nunca descarta en la migración); sin respuestas la actividad queda «Omitida» con el motivo del Cambio 50. Con --forzar la
ruta de cada uno sigue sola (`proceso.avanzar_seguro`). La API también la corre al arrancar (sin avanzar; el job del
motor avanza). Idempotente (marca en bitácora).

Uso (desde red-human-api/):
    python scripts/migrar_cambio50.py            # simulación: qué cambiaría
    python scripts/migrar_cambio50.py --forzar   # aplica
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from app.database import SessionLocal  # noqa: E402
from app.services import solicitud_web as ssw  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forzar", action="store_true", help="aplica (sin esto es una simulación)")
    a = ap.parse_args()
    db = SessionLocal()
    try:
        r = asyncio.run(ssw.migrar_cambio50(db, aplicar=a.forzar, avanzar=True))
        if not a.forzar:
            db.rollback()
        if r.get("yaAplicada"):
            print("La migración del Cambio 50 ya estaba aplicada.")
            return 0
        print(f"Plantillas: {r['plantillas']}\nVacantes: {r['vacantes']}\nCandidatos detenidos en «Prefiltro web»: {r['postulaciones']}")
        print("Aplicada." if a.forzar else "Simulación: nada se guardó. Corre con --forzar para aplicar.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
