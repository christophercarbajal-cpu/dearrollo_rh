"""Ajuste de la ruta de demostración (2026-10-08) — SOLO demo-grupak (`services/ajustes_demo.py`).

Retira temporalmente «Persona bajo la lluvia» (plantilla y vacantes; los candidatos que la esperaban quedan con la
actividad OMITIDA por «Ajuste de ruta de demostración») y fija los criterios del prefiltro conversacional de
«Ayudante general». La API ya lo aplica al arrancar; este script sirve para verlo o aplicarlo sin reiniciar.

    .venv/Scripts/python.exe scripts/ajustar_ruta_demo.py            # simulación: solo cuenta
    .venv/Scripts/python.exe scripts/ajustar_ruta_demo.py --forzar   # aplica (idempotente)
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
from app.services import ajustes_demo  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forzar", action="store_true", help="aplica los cambios (sin esto solo simula)")
    args = ap.parse_args()
    db = SessionLocal()
    try:
        r = ajustes_demo.ajustar(db, aplicar=args.forzar)
        if args.forzar:
            db.commit()
        else:
            db.rollback()
        print(("APLICADO" if args.forzar else "SIMULACIÓN (usa --forzar para aplicar)") + ":")
        print(f"  plantillas sin «Persona bajo la lluvia» ........ {r['plantillas']}")
        print(f"  vacantes sin «Persona bajo la lluvia» .......... {r['vacantes']}")
        print(f"  candidatos liberados (actividad omitida) ....... {r['postulaciones_liberadas']}")
        print(f"  «Ayudante general» con criterios nuevos ........ {r['criterios_ayudante']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
