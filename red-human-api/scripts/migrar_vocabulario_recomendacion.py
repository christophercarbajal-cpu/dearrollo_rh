"""Cambio 38 — normaliza el vocabulario de la recomendación guardada (una sola vez; también corre al arrancar la API).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/migrar_vocabulario_recomendacion.py           # simulación
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/migrar_vocabulario_recomendacion.py --forzar  # aplica

«Apto con observaciones» → «Con reservas» · «Apto» → «Recomendable» · «No apto» → «No recomendable» en postulaciones y
entrevistas. Los dictámenes de la Evaluación médica y del Estudio socioeconómico no se tocan.
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.chdir(RAIZ)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from app.database import SessionLocal  # noqa: E402
from app.services import vocabulario  # noqa: E402

if __name__ == "__main__":
    forzar = "--forzar" in sys.argv
    db = SessionLocal()
    try:
        plan = vocabulario.migrar(db, aplicar=False)
        print(f"Con el vocabulario anterior: {plan['valores']} valor(es) en {plan['postulaciones']} postulación(es) y "
              f"{plan['entrevistas']} entrevista(s). {plan['codigos'] or ''}")
        if not forzar:
            print("Simulación: no se guardó nada (usa --forzar para aplicar).")
        else:
            r = vocabulario.migrar(db, aplicar=True, forzar=True)
            db.commit()
            print(f"Listo: {r['valores']} valor(es) normalizados. Quedan: {vocabulario.con_vocabulario_anterior(db)}")
    finally:
        db.close()
