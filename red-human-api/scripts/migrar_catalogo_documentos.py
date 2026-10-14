"""Cambio 34 (2026-10-13) — migra la base al catálogo ÚNICO de documentos (ver `app/services/catalogo_documentos.py`).

* «Identificación oficial» (y demás nombres viejos) → nombre del catálogo («INE (frente y reverso)», …).
* Duplicados del mismo expediente («CURP» pendiente + «CURP» aprobada) → UNO solo; el Aprobado y su archivo sobreviven,
  los archivos de los demás quedan como historial y cada fusión queda en bitácora.
* Vacantes, plantillas de vacante y plantillas de Onboarding → catálogo, sin duplicados.

La API también lo corre en cada arranque (idempotente). Este script sirve para ver el plan antes:
    python scripts/migrar_catalogo_documentos.py            # simulación: muestra qué cambiaría
    python scripts/migrar_catalogo_documentos.py --forzar   # aplica
"""

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from app.database import SessionLocal  # noqa: E402
from app.services import catalogo_documentos as scat  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forzar", action="store_true", help="aplica los cambios (sin esto es una simulación)")
    a = ap.parse_args()
    db = SessionLocal()
    try:
        r = scat.migrar(db, aplicar=a.forzar)
        print(f"\n== Catálogo único de documentos ({'APLICAR' if a.forzar else 'SIMULACIÓN'}) ==")
        for x in r["renombrados"]:
            print(f"  · expediente {x['expediente']}: «{x['de']}» → «{x['a']}»")
        for x in r["fusionados"]:
            print(f"  · expediente {x['expediente']}: se fusiona «{x['sale']['tipo']}» ({x['sale']['estado']}) en "
                  f"«{x['queda']['tipo']}» ({x['queda']['estado']}{', con archivo' if x['queda']['archivo'] else ''})")
        print(f"  Vacantes: {', '.join(r['vacantes']) or '—'} · Plantillas de vacante: {len(r['plantillasVacante'])} · "
              f"Plantillas de Onboarding: {len(r['plantillasOnboarding'])}")
        print(f"  Totales: {r['totales']}")
        if a.forzar:
            db.commit()
            print("\nListo: cambios guardados.")
        else:
            db.rollback()
            print("\nSimulación: nada se guardó. Corre con --forzar para aplicar.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
