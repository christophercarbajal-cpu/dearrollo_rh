"""Cambio 30 (2026-10-13): migra las rutas de plantillas y vacantes al catálogo nuevo.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/migrar_cambio30.py            # simulación (no guarda)
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/migrar_cambio30.py --forzar   # aplica

- «Análisis de CV» → la solicitud pasa a «Solicitud web con CV» y la actividad se quita.
- «Propuesta y aceptación» / «Carta de intención» → UNA «Oferta laboral».
Idempotente (también corre sola al arrancar la API). Las postulaciones conservan su copia congelada.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from app.database import SessionLocal  # noqa: E402
from app.services import proceso as sproc  # noqa: E402

aplicar = "--forzar" in sys.argv
db = SessionLocal()
try:
    r = sproc.migrar_procesos_cambio30(db, aplicar=aplicar)
    if aplicar:
        db.commit()
    else:
        db.rollback()
    print(("APLICADA" if aplicar else "SIMULACIÓN (usa --forzar para aplicar)") + ":")
    print(f"  plantillas: {len(r['plantillas'])} {r['plantillas']}")
    print(f"  vacantes:   {len(r['vacantes'])} {r['vacantes']}")
    if r["errores"]:
        print(f"  errores:    {r['errores']}")
finally:
    db.close()
