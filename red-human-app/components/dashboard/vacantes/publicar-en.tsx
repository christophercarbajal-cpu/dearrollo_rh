"use client";

/* Cambio 36 (2026-10-14) — panel «Publicar en» del motor de publicación multicanal.

   Muestra los canales CONECTADOS de la Cuenta (Portal, WhatsApp, Google Empleos y los externos con conector; en Modo
   Demo todos, simulados) con su casilla MARCADA por defecto. Después de publicar, cada canal muestra su estado:
   «Publicada» con su liga (lleva ?canal=<canal>) o «Error: motivo». Presentación pura: la acción la hace quien lo usa. */

import { useEffect, useState } from "react";
import { ExternalLink } from "lucide-react";
import { fetchCanalesPublicacion, type CanalPublicacion } from "@/lib/api";
import { cn } from "@/lib/utils";

export function usarCanales(): [CanalPublicacion[], string[], (x: string[]) => void] {
  const [canales, setCanales] = useState<CanalPublicacion[]>([]);
  const [marcados, setMarcados] = useState<string[]>([]);
  useEffect(() => {
    fetchCanalesPublicacion().then((l) => {
      setCanales(l ?? []);
      setMarcados((l ?? []).map((c) => c.canal));
    });
  }, []);
  return [canales, marcados, setMarcados];
}

const TONO: Record<string, string> = {
  publicada: "text-good", error: "text-bad", pausada: "text-warn", cerrada: "text-ink-3", pendiente: "text-ink-3",
};

export function PanelPublicarEn({ canales, marcados, onChange, distribucion, deshabilitado = false }: {
  canales: CanalPublicacion[];
  marcados: string[];
  onChange: (x: string[]) => void;
  /** Estado por canal de la vacante (después de publicar). */
  distribucion?: Record<string, CanalPublicacion>;
  deshabilitado?: boolean;
}) {
  const alternar = (c: string) => onChange(marcados.includes(c) ? marcados.filter((x) => x !== c) : [...marcados, c]);
  return (
    <section className="rounded-2xl border border-border-soft p-4" aria-label="Publicar en">
      <p className="text-sm font-semibold text-ink">Publicar en</p>
      <p className="mt-0.5 text-xs text-ink-3">Canales conectados. «Publicar» la manda a todos los marcados; cada liga lleva su canal.</p>
      {!canales.length && <p className="mt-2 text-xs text-ink-3">Cargando canales…</p>}
      <ul className="mt-3 flex flex-col gap-2">
        {canales.map((c) => {
          const e = distribucion?.[c.canal];
          return (
            <li key={c.canal} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-border-faint px-3 py-2">
              <label className="flex min-w-0 flex-1 items-center gap-2 text-sm">
                <input type="checkbox" checked={marcados.includes(c.canal)} disabled={deshabilitado} onChange={() => alternar(c.canal)}
                  className="h-4 w-4 rounded border-border-soft text-brand" />
                <span className="font-medium text-ink">{c.nombre}</span>
                {c.simulado && <span className="rounded-full bg-surface-3 px-2 py-0.5 text-[10px] text-ink-3">Demo</span>}
              </label>
              {e && e.estado !== "pendiente" && (
                <span className={cn("text-xs font-medium", TONO[e.estado])} data-estado-canal={e.estado}>{e.estadoTexto}</span>
              )}
              {e?.estado === "publicada" && e.liga && (
                <a href={e.liga} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-brand hover:underline">
                  Liga <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
