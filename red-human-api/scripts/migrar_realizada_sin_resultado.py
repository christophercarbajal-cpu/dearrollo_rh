"""Cambio 18 (2026-10-12): «Realizada · Resultado pendiente» → «Pendiente».

    .venv/Scripts/python.exe scripts/migrar_realizada_sin_resultado.py            # simulación: solo lista
    .venv/Scripts/python.exe scripts/migrar_realizada_sin_resultado.py --forzar   # escribe

El botón «Marcar como realizada» y su estado se retiraron: una actividad se cierra registrando el resultado. Este script
regresa a «Pendiente» las evaluaciones que se quedaron en «Realizada · Resultado pendiente» (deja el evento en su
historial). Idempotente; no borra nada, no toca resultados, citas ni etapas y no manda mensajes.
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from app.database import SessionLocal  # noqa: E402
from app.services.evaluaciones import migrar_realizadas_sin_resultado  # noqa: E402


def main(forzar: bool) -> None:
    db = SessionLocal()
    try:
        r = migrar_realizadas_sin_resultado(db, aplicar=forzar)
    finally:
        db.close()
    print(f"Evaluaciones en «Realizada · Resultado pendiente»: {r['encontradas']}")
    for c in r["codigos"]:
        print(f"  · {c}")
    if forzar:
        print(f"✅ {r['migradas']} regresaron a «Pendiente».")
    else:
        print("Simulación: no se escribió nada. Corre con --forzar para aplicar.")


if __name__ == "__main__":
    main("--forzar" in sys.argv)
