"""Base desechable de las verificaciones (`scripts/verificar_*.py`) SIN dejar rastro en %TEMP%.

Cada verificación hace al inicio:

    import _temporal
    _dir = _temporal.directorio("rh_xxx_")

y obtiene un directorio nuevo (la base SQLite, sus `-wal`/`-shm` y `ARCHIVOS_DIR` viven ahí). Al terminar el proceso —pase
o falle: `sys.exit(1)` de un `check`, excepción no atrapada o fin normal— `atexit` cierra el motor de SQLAlchemy (en
Windows un archivo SQLite abierto no se puede borrar) y elimina el directorio completo.

Si el proceso muere sin pasar por `atexit` (lo mata `timeout`, Ctrl+C dos veces, corte de luz), el directorio queda
huérfano: la siguiente verificación que arranque barre los `rh_*` de %TEMP% con más de `HUERFANO_HORAS` horas sin
cambios. El margen evita tocar los de otras verificaciones que corren EN PARALELO (la suite usa `xargs -P`).

¿En memoria (`sqlite:///:memory:`)? No: la app abre sesiones en otros hilos (TestClient, BackgroundTasks, jobs) y cada
conexión a `:memory:` sería una base distinta y vacía; además varias verificaciones necesitan archivos reales (respaldo de
la base en las migraciones, adjuntos en `ARCHIVOS_DIR`). Un directorio temporal con borrado garantizado da lo mismo sin
reescribir la capa de datos.
"""

import atexit
import gc
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

PREFIJO = "rh_"
HUERFANO_HORAS = 2


def _ultimo_cambio(ruta: Path) -> float:
    """El cambio más reciente dentro del directorio (la base se escribe en archivos internos, no en el directorio)."""
    try:
        marcas = [ruta.stat().st_mtime]
        for hijo in ruta.rglob("*"):
            try:
                marcas.append(hijo.stat().st_mtime)
            except OSError:
                pass
        return max(marcas)
    except OSError:
        return time.time()


def barrer_huerfanos(horas: float = HUERFANO_HORAS) -> int:
    """Borra los `rh_*` de %TEMP% sin cambios en `horas` horas (corridas que murieron sin teardown). Nunca lanza."""
    limite = time.time() - horas * 3600
    borrados = 0
    try:
        candidatos = [p for p in Path(tempfile.gettempdir()).iterdir() if p.name.startswith(PREFIJO)]
    except OSError:
        return 0
    for ruta in candidatos:
        try:
            if _ultimo_cambio(ruta) > limite:
                continue
            if ruta.is_dir():
                shutil.rmtree(ruta, ignore_errors=True)
            else:
                ruta.unlink()
            borrados += 0 if ruta.exists() else 1
        except OSError:
            pass
    return borrados


def _cerrar_motores() -> None:
    """Suelta los archivos de la base. Cuando la verificación FALLA a la mitad suele quedar una sesión abierta con su
    conexión prestada; `engine.dispose()` solo cierra las conexiones libres del pool, así que primero se cierran TODAS
    las sesiones y, al final, cualquier conexión `sqlite3` que siga viva en el proceso (que de todos modos termina)."""
    if "sqlalchemy" in sys.modules:
        try:
            from sqlalchemy.orm import close_all_sessions

            close_all_sessions()
        except Exception:  # noqa: BLE001
            pass
    db = sys.modules.get("app.database")
    motor = getattr(db, "engine", None) if db is not None else None
    if motor is not None:
        try:
            motor.dispose()
        except Exception:  # noqa: BLE001
            pass
    gc.collect()
    if "sqlite3" in sys.modules:
        import sqlite3

        for obj in gc.get_objects():
            try:
                if isinstance(obj, sqlite3.Connection):
                    obj.close()
            except Exception:  # noqa: BLE001
                pass


def limpiar(ruta: str) -> bool:
    """Teardown: cierra la base y borra el directorio (reintenta: Windows suelta los candados con un poco de retraso)."""
    _cerrar_motores()
    for espera in (0, 0.2, 0.5, 1.0, 2.0):
        if espera:
            time.sleep(espera)
            gc.collect()
        shutil.rmtree(ruta, ignore_errors=True)
        if not os.path.exists(ruta):
            return True
    print(f"[_temporal] no se pudo borrar {ruta}; la siguiente corrida lo barrerá", file=sys.stderr)
    return False


def directorio(prefijo: str = "rh_prueba_") -> str:
    """Directorio temporal nuevo que se BORRA al terminar el proceso (pase o falle)."""
    if not prefijo.startswith(PREFIJO):
        prefijo = PREFIJO + prefijo
    barrer_huerfanos()
    ruta = tempfile.mkdtemp(prefix=prefijo)
    atexit.register(limpiar, ruta)
    return ruta
