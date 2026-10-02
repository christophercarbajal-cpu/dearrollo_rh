"use client";

/* Evaluación integral como RESULTADO acumulado (2026-10-01, pipeline de cinco columnas). Ya no es columna del
   Kanban: se ve en la tarjeta (`BadgeIntegral`) y en la ficha (`PanelResultadoIntegral`). Lo calcula la API
   (`services/evaluacion_integral.py`): un requisito obligatorio «No apto» manda sobre cualquier score; si faltan
   validaciones el resultado es «Pendiente» con el score PARCIAL (nunca cero); cada validación dice quién la revisó
   («Revisado por: Red Human», «Revisado por: [nombre]» o «Pendiente de revisión»). Solo informa: RH decide. */

import { Badge, Card, Eyebrow } from "@/components/ui";
import type { EstadoIntegral, ResultadoIntegral, ValidacionIntegral } from "@/lib/data";
import { cn } from "@/lib/utils";

const TONO: Record<EstadoIntegral, "good" | "warn" | "bad" | "neutral"> = {
  apto: "good",
  con_observaciones: "warn",
  no_apto: "bad",
  pendiente: "neutral",
};
const TONO_VALIDACION: Record<ValidacionIntegral["estado"], "good" | "warn" | "bad" | "neutral"> = {
  aprobada: "good",
  observaciones: "warn",
  no_apto: "bad",
  pendiente: "neutral",
};

function textoScore(r: ResultadoIntegral): string {
  if (r.score == null) return "sin score aún";
  return r.scoreParcial ? `score parcial ${r.score}` : `score ${r.score}`;
}

/** Tarjeta del Kanban / lista: «Evaluación integral: Pendiente · score parcial 78». */
export function BadgeIntegral({ r, compacto = false }: { r?: ResultadoIntegral; compacto?: boolean }) {
  if (!r) return null;
  return (
    <span title={`Evaluación integral: ${r.texto} — ${r.motivo}`} className="inline-flex">
      <Badge tone={TONO[r.estado]} dot>
        {compacto ? r.texto : `Integral: ${r.texto}`}
        <span className="ml-1 font-mono text-[10px] opacity-80">· {textoScore(r)}</span>
      </Badge>
    </span>
  );
}

/** Ficha del candidato: resultado + cada validación con quién la revisó. */
export function PanelResultadoIntegral({ r }: { r?: ResultadoIntegral }) {
  if (!r) return null;
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <Eyebrow>Evaluación integral</Eyebrow>
          <p className="mt-1 text-[12px] text-ink-3">{r.motivo}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={TONO[r.estado]} dot>{r.texto}</Badge>
          <span className="font-mono text-xs font-semibold text-ink-2">
            {r.score == null ? "Score: —" : `${r.scoreParcial ? "Score parcial" : "Score"}: ${r.score}/100`}
          </span>
        </div>
      </div>
      <p className="mt-2 text-[11px] text-ink-3">
        {r.completadas} de {r.total} validaciones completas. Solo informa: avanzar o descartar lo decide RH.
      </p>
      <ul className="mt-3 flex flex-col gap-1.5">
        {r.validaciones.map((v) => (
          <li key={v.clave} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border-soft px-3 py-2">
            <div className="min-w-0">
              <p className="text-sm font-medium text-ink">
                {v.nombre}
                {v.obligatoria && v.fuente === "persona" && (
                  <span className="ml-1.5 font-mono text-[9px] font-bold uppercase tracking-wide text-ink-3">obligatoria</span>
                )}
              </p>
              <p className={cn("text-[11px]", v.revisadoPor.startsWith("Revisado") ? "text-ink-2" : "text-warn")}>
                {v.revisadoPor}
                {v.detalle ? ` · ${v.detalle}` : ""}
              </p>
            </div>
            <Badge tone={TONO_VALIDACION[v.estado]}>{v.resultado}</Badge>
          </li>
        ))}
      </ul>
    </Card>
  );
}
