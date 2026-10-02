"""Pipeline de cinco columnas (2026-10-01) — acomoda a los candidatos actuales.

Columnas nuevas: Prefiltro → Filtro Red Human → Filtro humano → Contratación → Onboarding.
  * «Evaluación integral» (etapa interna «Evaluación») → Filtro Red Human («Entrevista IA»).
  * Etapas previas (Prefiltro / Filtro Red Human / Evaluación) con una entrevista humana ya creada (no cancelada)
    → Filtro humano («Entrevista Humana»). Esta excepción se aplica UNA sola vez (marca en bitácora).
  * Contratación y Onboarding NUNCA retroceden.
No borra nada: expedientes, evaluaciones, chats y entrevistas se conservan; cada movimiento AGREGA una nota
`etapa_migrada` a `Postulacion.historial` y queda en bitácora. Los contadores se recalculan solos
(services/conteos.py lee `Postulacion.etapa`); el script los muestra antes y después por Cuenta.

La lógica vive en app/migraciones.py::migrar_pipeline_cinco_columnas (la API también la corre al arrancar; es
idempotente, así que correr este script antes o después del despliegue da el mismo resultado).

Uso (desde red-human-api/):
    .venv/Scripts/python.exe scripts/migrar_pipeline_cinco_columnas.py           # simulación: muestra el plan, no escribe
    .venv/Scripts/python.exe scripts/migrar_pipeline_cinco_columnas.py --forzar  # aplica la migración
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.migraciones import migrar_pipeline_cinco_columnas, sincronizar  # noqa: E402
from app.models import ETAPAS_CANDIDATO, Cuenta, nombre_etapa  # noqa: E402
from app.services import conteos  # noqa: E402


def _contadores(db) -> dict:
    """{nombre de Cuenta: {etapa: n}} con el MISMO filtro del Kanban y las tarjetas de vacante."""
    return {c.nombre: conteos.por_etapa(db, c.id) for c in db.query(Cuenta).order_by(Cuenta.id).all()}


def _imprimir_contadores(titulo: str, datos: dict) -> None:
    print(f"\n{titulo}")
    for cuenta, por_etapa in datos.items():
        fila = " · ".join(f"{nombre_etapa(e)} {por_etapa.get(e, 0)}" for e in ETAPAS_CANDIDATO)
        retiradas = {e: n for e, n in por_etapa.items() if e not in ETAPAS_CANDIDATO and n}
        extra = f"  (+ etapas retiradas: {retiradas})" if retiradas else ""
        print(f"  {cuenta}: {fila}{extra}")


def main(forzar: bool) -> int:
    Base.metadata.create_all(bind=engine)
    sincronizar(engine)

    with SessionLocal() as db:
        plan = migrar_pipeline_cinco_columnas(db, aplicar=False)
        print("=" * 60)
        print("MIGRACIÓN — Pipeline de cinco columnas")
        print("=" * 60)
        print(f"Evaluación integral → Filtro Red Human:   {plan['evaluacion_a_filtro_red_human']}")
        print(f"Con entrevista humana → Filtro humano:    {plan['a_filtro_humano']}")
        if not plan["excepcion_aplicada"]:
            print("  (la excepción de la entrevista humana ya se aplicó antes o no hay tabla de evaluaciones: no se repite)")
        for m in plan["postulaciones"]:
            de = "Evaluación integral" if m["de"] == "Evaluación" else nombre_etapa(m["de"])
            print(f"  - {m['codigo']}: {de} → {nombre_etapa(m['a'])}")
        _imprimir_contadores("Contadores ANTES:", _contadores(db))
        print("=" * 60)

        if not forzar:
            print("Simulación: no se escribió nada. Corre con --forzar para aplicar.")
            return 0

        resumen = migrar_pipeline_cinco_columnas(db, aplicar=True)
        db.commit()
        print(f"\n✅ Migración aplicada: {len(resumen['postulaciones'])} postulaciones movidas, "
              f"{resumen['candidatos_legado']} filas legado de Candidato corregidas.")
        _imprimir_contadores("Contadores DESPUÉS:", _contadores(db))
        return 0


if __name__ == "__main__":
    sys.exit(main(forzar="--forzar" in sys.argv))
