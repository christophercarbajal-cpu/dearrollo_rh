"""Cambio 55 (2026-10-15) — procesos existentes al catálogo nuevo del editor.

  1. «Otra actividad» en Prefiltro o Filtro Red Human → al INICIO de Filtro humano.
  2. «Psicometría digital» en Filtro humano → Filtro Red Human (en su lugar fijo).
  3. «Capacitación / Inducción» en Contratación → al FINAL de Filtro humano.

Aplica a las plantillas de proceso y a las rutas de las vacantes (sube su versión). Las postulaciones conservan su copia
congelada. La API también la corre al arrancar. Idempotente (marca en bitácora).

Uso (desde red-human-api/):
    python scripts/migrar_cambio55.py            # simulación: qué cambiaría
    python scripts/migrar_cambio55.py --forzar   # aplica
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from app.database import SessionLocal  # noqa: E402
from app.services import proceso as sproc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forzar", action="store_true", help="aplica (sin esto es una simulación)")
    a = ap.parse_args()
    db = SessionLocal()
    try:
        r = sproc.migrar_cambio55(db, aplicar=a.forzar)
        if r.get("yaAplicada"):
            print("La migración del Cambio 55 ya estaba aplicada.")
            return 0
        print(f"Plantillas: {r['plantillas']}\nVacantes: {r['vacantes']}")
        if any(isinstance(x, str) and x.startswith("sin tablas") for x in r["errores"]):
            db.rollback()
            print(f"No se aplicó: la base no tiene el esquema al día ({r['errores'][0][:160]}…). Arranca la API una vez "
                  "(sincroniza el esquema y corre esta migración sola) o vuelve a correr este script después.")
            return 1
        if r["errores"]:
            print(f"Sin migrar (revisar a mano): {r['errores']}")
        if a.forzar:
            db.commit()
            print("Aplicada.")
        else:
            db.rollback()
            print("Simulación: nada se guardó. Corre con --forzar para aplicar.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
