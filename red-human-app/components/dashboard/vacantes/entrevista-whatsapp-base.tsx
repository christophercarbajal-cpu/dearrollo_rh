"use client";

/* Cambio 29 (2026-10-12): «Entrevista por WhatsApp» de las rutas Masivos. La entrevista es FIJA (sin IA generativa):
   A. situación actual → B. historial laboral (hasta 2 trabajos, repregunta de despido/renuncia) → C. preguntas propias de
   RH → D. fecha de inicio (2026-10-17: ya no confirma puesto, sueldo ni ubicación) y cierre según la ruta. RH puede quitar
   preguntas base o agregar las suyas (van al bloque C) SOLO para esta vacante; «Restaurar base» regresa a la etiqueta «Base». */

import { useState } from "react";
import { Plus, RotateCcw, X } from "lucide-react";
import { Badge } from "@/components/ui";
import { PREGUNTAS_BASE_ENTREVISTA, type EntrevistaBaseConfig } from "@/lib/api";
import { cn } from "@/lib/utils";

const BLOQUES: Record<string, string> = { A: "A. Situación actual", B: "B. Historial laboral", C: "C. Preguntas de RH", D: "D. Inicio y cierre" };

export function EntrevistaWhatsappBase({ value, onChange }: { value: EntrevistaBaseConfig; onChange: (v: EntrevistaBaseConfig) => void }) {
  const [nueva, setNueva] = useState("");
  const esBase = !value.quitadas.length && !value.propias.length;
  const quitar = (id: string) => onChange({ ...value, quitadas: [...value.quitadas, id] });
  const volver = (id: string) => onChange({ ...value, quitadas: value.quitadas.filter((x) => x !== id) });
  const agregar = () => {
    const t = nueva.trim();
    if (!t || value.propias.some((x) => x.toLowerCase() === t.toLowerCase())) return;
    onChange({ ...value, propias: [...value.propias, t] });
    setNueva("");
  };

  return (
    <div className="rounded-xl border border-border-soft p-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="flex items-center gap-2 text-sm font-semibold text-ink">
          Entrevista por WhatsApp {esBase ? <Badge tone="brand">Base</Badge> : <Badge tone="warn">Ajustada para esta vacante</Badge>}
        </p>
        {!esBase && (
          <button type="button" onClick={() => onChange({ quitadas: [], propias: [] })} className="flex items-center gap-1 text-[12px] font-semibold text-brand hover:underline">
            <RotateCcw className="h-3.5 w-3.5" /> Restaurar base
          </button>
        )}
      </div>
      <p className="mt-0.5 text-xs text-ink-3">Una pregunta por mensaje. Red Human las adapta a lo que responde el candidato. Quita las que no apliquen o agrega las tuyas en el bloque C.</p>
      {(["A", "B", "C", "D"] as const).map((b) => (
        <div key={b} className="mt-2">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-3">{BLOQUES[b]}</p>
          <ul className="divide-y divide-border-faint">
            {b === "C"
              ? value.propias.map((q, i) => (
                  <li key={`${i}-${q}`} className="flex items-start justify-between gap-2 py-1.5 text-[13px]">
                    <span>{q}</span>
                    <button type="button" onClick={() => onChange({ ...value, propias: value.propias.filter((_, k) => k !== i) })}
                      className="text-ink-3 hover:text-bad" aria-label="Quitar pregunta"><X className="h-3.5 w-3.5" /></button>
                  </li>
                ))
              : PREGUNTAS_BASE_ENTREVISTA.filter((q) => q.bloque === b).map((q) => {
                  const quitada = value.quitadas.includes(q.id);
                  return (
                    <li key={q.id} className={cn("flex items-start justify-between gap-2 py-1.5 text-[13px]", quitada && "text-ink-3 line-through")}>
                      <span>{q.texto}</span>
                      {q.fija ? <span className="text-[11px] text-ink-3">fija</span> : quitada ? (
                        <button type="button" onClick={() => volver(q.id)} className="text-[12px] font-semibold text-brand hover:underline">Volver a incluir</button>
                      ) : (
                        <button type="button" onClick={() => quitar(q.id)} className="text-ink-3 hover:text-bad" aria-label={`Quitar «${q.texto}»`}><X className="h-3.5 w-3.5" /></button>
                      )}
                    </li>
                  );
                })}
          </ul>
          {b === "C" && (
            <div className="mt-1.5 flex gap-2">
              <input value={nueva} onChange={(e) => setNueva(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); agregar(); } }}
                placeholder="Ej. ¿Tienes experiencia con montacargas?" maxLength={300}
                className="min-h-9 flex-1 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand" />
              <button type="button" onClick={agregar} disabled={!nueva.trim()} className="flex items-center gap-1 rounded-xl border border-border-soft px-3 text-sm font-semibold text-brand disabled:opacity-50">
                <Plus className="h-3.5 w-3.5" /> Agregar pregunta
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
