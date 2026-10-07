"""Rutas de proceso para los candidatos históricos (2026-10-06).

Regla: NINGUNA postulación (activa o cerrada) queda sin ruta. A cada postulación sin `proceso` se le asigna una COPIA
estática de su ruta con la misma cascada que usa una postulación nueva:
  1) el proceso de su vacante, 2) el proceso predeterminado de su Cuenta, 3) «Corporativos sin psicometría».
Antes se siembran en cada Cuenta las tres rutas base editables (Masivos / Corporativos sin y con psicometría) que falten.

Integridad (documento de reglas):
  * El estado de cada paso NO se escribe: se DERIVA de los registros reales (prefiltro, análisis de CV, Entrevista Red
    Human, evaluaciones, expediente y documentos, tareas de Onboarding, cursos). Nada queda «Completado» solo porque el
    candidato esté en una etapa avanzada.
  * NO cambia la etapa, NO toca resultados, documentos, evaluaciones ni historial (solo AGREGA una nota
    `ruta_asignada`), NO reenvía solicitudes ni mensajes y NO dispara el avance automático.

La lógica vive en app/services/proceso.py::asignar_rutas_faltantes (la API también la corre al arrancar; es idempotente,
así que correr este script antes o después del despliegue da el mismo resultado).

Uso (desde red-human-api/):
    .venv/Scripts/python.exe scripts/migrar_rutas_candidatos.py           # simulación: muestra el plan, no escribe
    .venv/Scripts/python.exe scripts/migrar_rutas_candidatos.py --forzar  # aplica la migración
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.migraciones import crear_tablas_modulos_rh, sincronizar  # noqa: E402
from app.models import Cuenta, Postulacion  # noqa: E402
from app.services import modulos_rh  # noqa: E402
from app.services import proceso as sproc  # noqa: E402

ORIGENES = {"vacante": "proceso de la vacante", "cuenta": "proceso predeterminado de la Cuenta",
            "base": "«Corporativos sin psicometría» (respaldo)"}


def _imprimir(titulo: str, r: dict, cuentas: dict) -> None:
    print(f"\n{titulo}")
    print(f"  Postulaciones revisadas:  {r['revisadas']}")
    print(f"  Sin ruta:                 {r['sin_ruta']}")
    for origen, n in r["por_origen"].items():
        print(f"    - {n} con {ORIGENES.get(origen, origen)}")
    for cid, n in r["por_cuenta"].items():
        nombre = cuentas.get(int(cid), f"Cuenta {cid}") if str(cid).isdigit() else "(sin Cuenta)"
        print(f"    · {nombre}: {n}")


def main(forzar: bool) -> int:
    Base.metadata.create_all(bind=engine)
    error = crear_tablas_modulos_rh(engine)
    modulos_rh.marcar_disponible(error is None, error or "")
    sincronizar(engine)

    with SessionLocal() as db:
        cuentas = {c.id: c.nombre for c in db.query(Cuenta).all()}
        etapas_antes = {p.id: p.etapa for p in db.query(Postulacion).all()}
        print("=" * 64)
        print("MIGRACIÓN — Rutas de proceso para candidatos históricos")
        print("=" * 64)
        if error:
            print(f"⚠️  plantillas_proceso no disponible ({error}): se usará la ruta base en código.")
        plan = sproc.asignar_rutas_faltantes(db, aplicar=False)
        _imprimir("Plan:", plan, cuentas)
        print("=" * 64)

        if not forzar:
            db.rollback()
            print("Simulación: no se escribió nada. Corre con --forzar para aplicar.")
            return 0

        with db.begin_nested():
            creadas = sproc.asegurar_rutas_base_todas(db)
        r = sproc.asignar_rutas_faltantes(db, aplicar=True)
        # Garantías: nadie cambió de etapa y nadie quedó sin ruta (si algo falla, no se guarda nada).
        movidas = [p.codigo for p in db.query(Postulacion).all() if etapas_antes.get(p.id, p.etapa) != p.etapa]
        if movidas:
            db.rollback()
            print(f"❌ Abortado: {len(movidas)} postulación(es) cambiarían de etapa ({movidas[:10]}). No se guardó nada.")
            return 1
        restantes = sum(1 for p in db.query(Postulacion).all() if not sproc.tiene_proceso(p))
        if restantes:
            db.rollback()
            print(f"❌ Abortado: {restantes} postulación(es) seguirían sin ruta. No se guardó nada.")
            return 1
        db.commit()
        print(f"\n✅ Rutas base sembradas: {creadas} · rutas asignadas: {r['asignadas']}")
        p = r["pasos"]
        print(f"   Estado DERIVADO de los pasos asignados: {p.get('completada', 0)} completados · "
              f"{p.get('en_curso', 0)} en curso · {p.get('pendiente', 0)} pendientes"
              f"{' · ' + str(p.get('omitida', 0)) + ' omitidos' if p.get('omitida') else ''}")
        print("   Etapas, resultados, documentos e historial: sin cambios. Postulaciones sin ruta: 0.")
        return 0


if __name__ == "__main__":
    sys.exit(main(forzar="--forzar" in sys.argv))
