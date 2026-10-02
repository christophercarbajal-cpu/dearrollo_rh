"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { ETAPAS_PIPELINE, fetchPipeline, nombreEtapa } from "@/lib/api";
import { usePolling } from "@/lib/use-polling";

/** 2026-09-20 (B4): candidatos ACTIVOS por etapa — misma fuente que el Kanban y los contadores de cada
 * vacante (`Postulacion.etapa`, `services/conteos.por_etapa`). Cada renglón lleva al Kanban filtrado. */
const ETAPAS = ETAPAS_PIPELINE;

export function EmbudoEtapas() {
  const [porEtapa, setPorEtapa] = useState<Record<string, number> | null>(null);
  const cargar = useCallback(async () => {
    const p = await fetchPipeline();
    if (p) setPorEtapa(p.candidatos.por_etapa ?? {});
  }, []);
  usePolling(cargar);

  const total = Object.values(porEtapa ?? {}).reduce((a, b) => a + b, 0);
  const max = Math.max(1, ...ETAPAS.map((e) => porEtapa?.[e] ?? 0));

  return (
    <div className="mt-5 space-y-3">
      {porEtapa === null && <p className="text-xs text-ink-3">Cargando…</p>}
      {porEtapa !== null &&
        ETAPAS.map((etapa) => {
          const n = porEtapa[etapa] ?? 0;
          return (
            <Link
              key={etapa}
              href={`/dashboard/candidatos?etapa=${encodeURIComponent(etapa)}`}
              className="block rounded-lg px-1 py-0.5 transition hover:bg-brand-soft/60"
              title={`Ver candidatos en ${nombreEtapa(etapa)}`}
            >
              <div className="flex items-center justify-between text-sm">
                <span className="text-ink-2">{nombreEtapa(etapa)}</span>
                <span className="font-mono font-semibold tabular">{n.toLocaleString("es-MX")}</span>
              </div>
              <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-surface-2">
                <div className="h-full rounded-full bg-gradient-to-r from-brand to-brand-2" style={{ width: `${Math.round((n / max) * 100)}%` }} />
              </div>
            </Link>
          );
        })}
      {porEtapa !== null && (
        <Link href="/dashboard/candidatos" className="block pt-1 text-right text-[11px] font-semibold text-brand hover:underline">
          {total.toLocaleString("es-MX")} candidatos activos en total →
        </Link>
      )}
    </div>
  );
}
