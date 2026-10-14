"""Diagnóstico (SOLO LECTURA) de «el candidato no aparece en la lista de su vacante» — Cambio 37, caso VAC-1171.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/diagnostico_postulacion.py --telefono "+52 55 1046 8412" --vacante VAC-1171
    (también acepta --vacante abogado-corporativo-135, el slug de la liga /aplicar/<slug>)

Muestra la vacante a la que apunta la liga, TODAS las filas de esa persona (por teléfono, en todas las Cuentas), sus
postulaciones (vacante, Cuenta, activa/motivo de cierre, etapa, estado, Modo Prueba), a cuál apunta la conversación de
WhatsApp, la plantilla de inicio y los últimos mensajes; y explica por qué la tarjeta no sale en la lista de la vacante.
No escribe nada.

Causas que reconoce:
  · DESCARTADA por el prefiltro o por RH → está cerrada: la lista solo muestra activas (se ve con «Mostrar cerradas» o con
    la liga «[n] descartados» del Resumen de la vacante — Cambio 37).
  · Postulación en OTRA vacante/Cuenta → la respuesta a la plantilla cayó en otro proceso del mismo número (antes del
    Cambio 37 la plantilla no ligaba la conversación a su vacante).
  · Prueba reemplazada / reinicio de prueba / persona eliminada / duplicado fusionado.
"""

import argparse
import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.chdir(RAIZ)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from app.database import SessionLocal  # noqa: E402
from app.models import Candidato, Mensaje, Postulacion, Vacante  # noqa: E402


def tel10(t: str) -> str:
    d = re.sub(r"\D", "", t or "")
    if d.startswith("521") and len(d) == 13:
        d = d[3:]
    elif d.startswith("52") and len(d) == 12:
        d = d[2:]
    return d[-10:]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--telefono", required=True)
    ap.add_argument("--vacante", default="", help="VAC-#### o el slug de /aplicar/<slug>")
    args = ap.parse_args()
    tel = tel10(args.telefono)
    db = SessionLocal()
    try:
        v = None
        if args.vacante:
            v = db.query(Vacante).filter((Vacante.codigo == args.vacante) | (Vacante.slug == args.vacante)).first()
            if v is None:
                print(f"⚠️  No existe la vacante «{args.vacante}» (ni por código ni por slug).")
            else:
                print(f"Vacante: {v.codigo} «{v.titulo}» · slug={v.slug} · estado={v.estado} · cuenta_id={v.cuenta_id}")
                otras = db.query(Vacante).filter(Vacante.slug == v.slug, Vacante.id != v.id).all()
                if otras:
                    print(f"⚠️  Otras vacantes con el MISMO slug: {[x.codigo for x in otras]} — la liga puede caer en la primera.")
        personas = (db.query(Candidato).filter((Candidato.telefono == tel) | (Candidato.wa_id.like(f"%{tel}")))
                    .order_by(Candidato.id).all())
        print(f"\nPersonas con el teléfono {tel}: {len(personas)}")
        hallazgos = []
        for c in personas:
            conv = c.postulacion_conversacion_id
            print(f"  · {c.codigo} «{c.nombre}» cuenta_id={c.cuenta_id} prueba={c.es_prueba} eliminado={bool(c.eliminado_en)} "
                  f"conversación→{conv}")
            for p in c.postulaciones:
                vac = p.vacante
                pi = (p.analisis or {}).get("plantilla_inicio") or {}
                marca = " ← conversación" if conv == p.id else ""
                print(f"      {p.codigo} vacante={vac.codigo if vac else '—'} cuenta_id={p.cuenta_id} activa={p.activa} "
                      f"cierre={p.motivo_cierre or '—'} etapa={p.etapa} estado={p.estado} prueba={p.es_prueba} "
                      f"plantilla={'enviada ' + str(pi.get('enviada_en'))[:16] if pi.get('enviada_en') else 'no'}"
                      f"{' respondida' if pi.get('respondida_en') else ''}{marca}")
                if v is not None and p.vacante_id == v.id:
                    if not p.activa:
                        hallazgos.append(f"{p.codigo}: está CERRADA ({p.motivo_cierre}) → la lista de la vacante solo muestra activas. "
                                         "Se ve con «Mostrar cerradas» o con la liga «[n] descartados» del Resumen.")
                    if p.cuenta_id != v.cuenta_id:
                        hallazgos.append(f"{p.codigo}: su cuenta_id ({p.cuenta_id}) ≠ la de la vacante ({v.cuenta_id}).")
                    if c.eliminado_en:
                        hallazgos.append(f"{p.codigo}: la persona {c.codigo} está eliminada (no sale en tableros).")
                elif v is not None and p.activa is False and p.motivo_cierre in ("descartado", "prefiltro_no_aprobado"):
                    hallazgos.append(f"{p.codigo}: el descarte fue en OTRA vacante ({vac.codigo if vac else 'sin vacante'}), no en {v.codigo}.")
                ultimos = (db.query(Mensaje).filter(Mensaje.postulacion_id == p.id).order_by(Mensaje.id.desc()).limit(4).all())
                for m in reversed(ultimos):
                    print(f"         [{m.rol}] {m.texto[:110]!r}")
        if not personas:
            hallazgos.append("No hay ninguna persona con ese teléfono en ESTA base (¿es la base de producción?).")
        elif v is not None and not any(p.vacante_id == v.id for c in personas for p in c.postulaciones):
            hallazgos.append(f"Ninguna postulación de ese teléfono está en {v.codigo}: la postulación web se registró en otra vacante "
                             "o con otro teléfono (revisa el formulario y la bitácora «postulacion_recibida»).")
        print("\nDiagnóstico:")
        for h in hallazgos or ["Sin hallazgos: la postulación está activa en su vacante y su Cuenta (revisa los filtros del tablero)."]:
            print(f"  - {h}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
