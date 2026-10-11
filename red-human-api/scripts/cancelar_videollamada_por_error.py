"""Cancela una videollamada creada POR ERROR (2026-10-11: «Prueba 5», cita del 12 de octubre).

    # simulación (no cambia nada): muestra qué se cancelaría
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/cancelar_videollamada_por_error.py --candidato "Prueba 5" --fecha 2026-10-12
    # aplicar
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/cancelar_videollamada_por_error.py --candidato "Prueba 5" --fecha 2026-10-12 --forzar

Busca, en las postulaciones cuyo candidato se llama EXACTAMENTE así (palabra completa: «Prueba 5» no toca «Prueba 50»):
  - la videollamada agendada por el chat (`Postulacion.videollamada_agendada_en`) de ese día (hora de la organización),
  - la Entrevista Red Human con avatar programada ese día (`Entrevista.programada_para`, estado «programada»),
  - evaluaciones pendientes con cita en «Videollamada» ese día.
Con `--forzar`: la videollamada pasa a `analisis.videollamadas_anteriores` (marcada `cancelada_por_error`), la entrevista
queda «cancelada» (la sala la trata como cerrada), la evaluación «Cancelada» con motivo; todo queda en historial y
bitácora. NUNCA borra nada y NO avisa al candidato salvo `--avisar`. Idempotente: lo ya cancelado no se toca.
"""

import argparse
import asyncio
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

MOTIVO = "Videollamada creada por error (limpieza 2026-10-11)"
ACTOR = "soporte (limpieza)"


def _mismo_nombre(nombre: str, buscado: str) -> bool:
    patron = r"(?<!\w)" + r"\s+".join(re.escape(x) for x in buscado.split()) + r"(?!\w)"
    return bool(re.search(patron, nombre or "", re.IGNORECASE))


def plan(db, nombre: str, dia: date) -> list:
    from app import fechas
    from app.models import Candidato, Entrevista, Evaluacion, Postulacion

    salida = []
    personas = [c for c in db.query(Candidato).all() if _mismo_nombre(c.nombre, nombre)]
    for c in personas:
        for p in db.query(Postulacion).filter(Postulacion.candidato_id == c.id).all():
            if p.videollamada_agendada_en and fechas.local(p.videollamada_agendada_en).date() == dia:
                salida.append({"tipo": "videollamada_chat", "postulacion": p, "cuando": p.videollamada_agendada_en})
            for e in db.query(Entrevista).filter(Entrevista.postulacion_id == p.id, Entrevista.estado == "programada").all():
                if e.programada_para and fechas.local(e.programada_para).date() == dia and e.tipo != "whatsapp":
                    salida.append({"tipo": "entrevista_avatar", "postulacion": p, "entrevista": e, "cuando": e.programada_para})
            for ev in db.query(Evaluacion).filter(Evaluacion.postulacion_id == p.id, Evaluacion.estado.in_(("pendiente", "no_realizada")),
                                                   Evaluacion.cita_modalidad == "Videollamada").all():
                if ev.cita_fecha_hora and fechas.local(ev.cita_fecha_hora).date() == dia:
                    salida.append({"tipo": "evaluacion", "postulacion": p, "evaluacion": ev, "cuando": ev.cita_fecha_hora})
    return salida


async def aplicar(db, items: list, avisar: bool = False) -> int:
    from sqlalchemy.orm.attributes import flag_modified

    from app import fechas
    from app.models import registrar
    from app.services import evaluaciones as sev

    ahora = datetime.now(timezone.utc)
    hechos = 0
    for it in items:
        p = it["postulacion"]
        if it["tipo"] == "videollamada_chat":
            a = dict(p.analisis or {})
            a["videollamadas_anteriores"] = list(a.get("videollamadas_anteriores") or []) + [{
                "fecha": fechas.iso(p.videollamada_agendada_en), "liga": p.videollamada_liga or "", "noshow": False,
                "cancelada_por_error": True, "cancelada_en": ahora.isoformat(), "motivo": MOTIVO}]
            p.analisis = a
            flag_modified(p, "analisis")
            p.videollamada_agendada_en = None
            p.videollamada_liga = ""
            p.videollamada_aviso_noshow_enviado = False
        elif it["tipo"] == "entrevista_avatar":
            e = it["entrevista"]
            e.estado, e.cierre, e.finalizada_en = "cancelada", "manual", ahora
        elif it["tipo"] == "evaluacion":
            ev = it["evaluacion"]
            sev.cambiar_estado(db, ev, "cancelada", ACTOR, motivo=MOTIVO)
            if avisar:
                await sev.notificar(db, ev, p, "evaluacion_cancelada", ACTOR, audiencias={"candidato"})
        p.historial = list(p.historial or []) + [{"evento": "videollamada_cancelada", "usuario": ACTOR, "fecha": ahora.isoformat(),
                                                  "motivo": MOTIVO, "texto": f"Videollamada del {fechas.local(it['cuando']).strftime('%d/%m %H:%M')} "
                                                                             f"cancelada: {MOTIVO}."}]
        registrar(db, ACTOR, "videollamada_cancelada_por_error", "postulacion", p.codigo,
                  {"tipo": it["tipo"], "cuando": fechas.iso(it["cuando"]), "motivo": MOTIVO, "avisado": avisar})
        hechos += 1
    db.commit()
    return hechos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidato", required=True)
    ap.add_argument("--fecha", required=True, help="AAAA-MM-DD (hora de la organización)")
    ap.add_argument("--forzar", action="store_true", help="aplica la cancelación (sin esto solo simula)")
    ap.add_argument("--avisar", action="store_true", help="avisa al candidato de la cancelación (por defecto NO)")
    args = ap.parse_args()
    from app.database import SessionLocal

    dia = date.fromisoformat(args.fecha)
    db = SessionLocal()
    try:
        items = plan(db, args.candidato, dia)
        from app import fechas

        print(f"{len(items)} cita(s) de «{args.candidato}» el {dia.isoformat()}:")
        for it in items:
            p = it["postulacion"]
            print(f"  - {it['tipo']}: {p.codigo} · {p.nombre} · {p.vacante.titulo if p.vacante else 'sin vacante'} · etapa {p.etapa} · "
                  f"{fechas.local(it['cuando']).strftime('%d/%m/%Y %H:%M')}")
        if not items:
            return 0
        if not args.forzar:
            print("SIMULACIÓN: no se cambió nada. Repite con --forzar para cancelar.")
            return 0
        n = asyncio.run(aplicar(db, items, args.avisar))
        print(f"Listo: {n} cancelada(s). Historial y bitácora actualizados{' · candidato avisado' if args.avisar else ' · sin avisos'}.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
