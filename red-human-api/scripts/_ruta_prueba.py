"""Ayudante de las verificaciones (Cambio 36): fija la ruta INICIAL de una vacante sembrada.

Las vacantes de demostración (`SEMBRAR_DEMO`) ya traen candidatos, y desde el Cambio 36 el proceso de una vacante con
candidatos NO se cambia por la API (`PUT /procesos/vacantes/{codigo}` → 409; se prueba en `verificar_cambios36_38.py`).
Para preparar cada escenario, las verificaciones fijan la ruta como CONFIGURACIÓN de la base con la MISMA función que usa
la API (`proceso.proceso_para_vacante`). Regresa un objeto con `status_code` y `json()` como la respuesta del endpoint.
"""

from types import SimpleNamespace


def fijar_ruta(db, codigo: str, json: dict, headers=None):  # noqa: A002 — mismo nombre que el parámetro de TestClient
    from app.models import Vacante
    from app.services import proceso as sproc

    v = db.query(Vacante).filter(Vacante.codigo == codigo).one()
    v.proceso = sproc.proceso_para_vacante(db, v.cuenta_id, v.proceso or {}, json)
    db.commit()
    db.refresh(v)
    proceso = dict(v.proceso or {})
    return SimpleNamespace(status_code=200, text="", json=lambda: {"proceso": proceso})
