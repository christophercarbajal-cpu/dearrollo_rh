"""Cambio 58 (2026-10-15) — limpia el título temporal de la vacante B de Fletes México.

Para distinguir las dos vacantes de «Operador de 5ª rueda» se le había puesto a la del proceso con avatar el título
«Operador de 5ª rueda · Entrevista en video», y ese sufijo lo veía el candidato (solicitud web, WhatsApp). Desde el
Cambio 58 RH las distingue por el nombre del proceso en la tarjeta, la lista y el detalle; el título vuelve a ser el real.

Solo cambia `Vacante.titulo` (con bitácora). La liga publicada (`slug`) queda igual: desde la primera publicación es
inmutable. No toca candidatos, procesos ni manda mensajes. Idempotente.

Uso (desde red-human-api/, con el .env del ambiente):
    python scripts/limpiar_titulo_fletes.py            # simulación: qué cambiaría
    python scripts/limpiar_titulo_fletes.py --forzar   # aplica
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:  # la consola de Windows (cp1252) no imprime acentos
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from app.database import SessionLocal  # noqa: E402
from app.models import Cuenta, Vacante, registrar  # noqa: E402

ACTOR = "script:limpiar_titulo_fletes"
SLUG_CUENTA = "fletes-mexico"
TITULO_ANTERIOR = "Operador de 5ª rueda · Entrevista en video"
TITULO = "Operador de 5ª rueda"


def limpiar(db, aplicar: bool = False) -> list:
    """Regresa [(codigo, proceso)] de las vacantes renombradas. Sin `aplicar` no escribe nada."""
    cu = db.query(Cuenta).filter(Cuenta.slug == SLUG_CUENTA).first()
    if cu is None:
        return []
    filas = db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == TITULO_ANTERIOR).order_by(Vacante.id).all()
    salida = []
    for v in filas:
        salida.append((v.codigo, (v.proceso or {}).get("plantilla_nombre") or "sin proceso", v.estado))
        if aplicar:
            v.titulo = TITULO
            registrar(db, ACTOR, "vacante_titulo_limpiado", "vacante", v.codigo, {"antes": TITULO_ANTERIOR, "despues": TITULO})
    return salida


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forzar", action="store_true", help="aplica (sin esto es una simulación)")
    a = ap.parse_args()
    db = SessionLocal()
    try:
        if db.query(Cuenta).filter(Cuenta.slug == SLUG_CUENTA).first() is None:
            print(f"No existe la Cuenta «{SLUG_CUENTA}» en esta base: nada que limpiar.")
            return 0
        filas = limpiar(db, aplicar=a.forzar)
        if not filas:
            print(f"Ninguna vacante de Fletes México se llama «{TITULO_ANTERIOR}»: nada que limpiar.")
            return 0
        for codigo, proceso, estado in filas:
            print(f"{codigo} ({estado}, proceso «{proceso}»): «{TITULO_ANTERIOR}» → «{TITULO}»")
        if a.forzar:
            db.commit()
            print("Aplicado.")
        else:
            db.rollback()
            print("Simulación: nada se guardó. Corre con --forzar para aplicar.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
