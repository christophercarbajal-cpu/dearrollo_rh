"""Respaldo histórico de la trazabilidad por destinatario (2026-10-08). OPCIONAL.

    .venv/Scripts/python.exe scripts/migrar_envios_actividad.py            # simulación: solo cuenta
    .venv/Scripts/python.exe scripts/migrar_envios_actividad.py --forzar   # escribe

La tabla nueva `envios_actividad` la crea sola el arranque de la API (paso NO fatal, como las demás de módulos RH) y
las columnas nuevas de `evaluaciones` (`referencias`, `referencias_token`, `referencias_capturadas_en`,
`sincronizacion`) las agrega `migraciones.sincronizar` con su default. Este script NO es necesario para que todo
funcione: sin él, una evaluación previa muestra su último envío leído de sus eventos «envio» (`envios.resumen_legado`).
Sirve para que, al primer envío nuevo de una evaluación vieja, su historial de intentos no empiece de cero.

* Copia cada evento «envio» de `eventos_evaluacion` como un lote de `envios_actividad` (enviado / fallido por canal).
  Nunca borra ni modifica los eventos (append-only).
* Idempotente: solo toca evaluaciones que todavía NO tienen filas en `envios_actividad`.
* No manda mensajes, no mueve etapas, no cambia estados.
"""

import secrets
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from app.database import SessionLocal, engine  # noqa: E402
from app.migraciones import crear_tablas_modulos_rh, sincronizar  # noqa: E402
from app.models import EnvioActividad, Evaluacion, EventoEvaluacion, Postulacion  # noqa: E402
from app.services.envios import _normalizar_destinatario  # noqa: E402


def main(forzar: bool) -> None:
    if forzar:
        error = crear_tablas_modulos_rh(engine)
        if error:
            print(f"❌ No se pudo crear la tabla: {error}")
            sys.exit(1)
        sincronizar(engine)
    db = SessionLocal()
    resumen = {"evaluaciones": 0, "con_filas": 0, "lotes": 0, "filas": 0}
    try:
        try:
            con_filas = {x for (x,) in db.query(EnvioActividad.evaluacion_id).filter(EnvioActividad.evaluacion_id.isnot(None)).distinct()}
        except Exception:  # noqa: BLE001 — la tabla aún no existe (simulación sobre una base vieja)
            db.rollback()
            con_filas = set()
        # solo columnas que ya existían: la simulación corre sobre una base SIN migrar y no debe tocar el esquema
        from sqlalchemy.orm import load_only

        campos = (Evaluacion.id, Evaluacion.cuenta_id, Evaluacion.postulacion_id, Evaluacion.tipo, Evaluacion.paso_id, Evaluacion.evaluador_nombre)
        for ev in db.query(Evaluacion).options(load_only(*campos)).order_by(Evaluacion.id).all():
            resumen["evaluaciones"] += 1
            if ev.id in con_filas:
                resumen["con_filas"] += 1
                continue
            p = db.get(Postulacion, ev.postulacion_id)
            if p is None:
                continue
            eventos = (db.query(EventoEvaluacion).filter(EventoEvaluacion.evaluacion_id == ev.id, EventoEvaluacion.accion == "envio")
                       .order_by(EventoEvaluacion.id).all())
            for e in eventos:
                d = e.detalle or {}
                por: dict = {}
                for r in d.get("envios") or []:
                    if isinstance(r, dict):
                        dest = _normalizar_destinatario(r.get("destinatario") or "candidato", ev)
                        if dest:
                            por.setdefault(dest, []).append(r)
                for dest, filas in por.items():
                    resumen["lotes"] += 1
                    lote = secrets.token_hex(8)
                    motivo = d.get("liga") or ("evaluador" if dest != "candidato" else "aviso")
                    for r in filas:
                        resumen["filas"] += 1
                        if forzar:
                            db.add(EnvioActividad(
                                cuenta_id=ev.cuenta_id, postulacion_id=p.id, evaluacion_id=ev.id, paso_id=ev.paso_id or "", lote=lote,
                                destinatario=dest, destinatario_nombre=(ev.evaluador_nombre if dest != "candidato" else p.nombre or "")[:150],
                                motivo=str(motivo)[:20], canal=str(r.get("canal") or "")[:20], destino=str(r.get("destino") or "")[:200],
                                estado="enviado" if r.get("enviado") else "fallido", detalle=str(r.get("detalle") or "")[:1000],
                                actor=(e.actor or "")[:150], creado_en=e.fecha,
                            ))
        if forzar:
            db.commit()
    finally:
        db.close()
    modo = "APLICADO" if forzar else "SIMULACIÓN (nada se escribió; usa --forzar)"
    print(f"{modo}: {resumen['evaluaciones']} evaluaciones revisadas · {resumen['con_filas']} ya tenían trazabilidad · "
          f"{resumen['lotes']} lotes / {resumen['filas']} filas {'creados' if forzar else 'por crear'}")


if __name__ == "__main__":
    main("--forzar" in sys.argv)
