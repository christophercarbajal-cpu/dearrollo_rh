"use client";

/* Cambio 25 (2026-10-12) — revisión de documentos en la pestaña «Documentos» de la ficha. Un renglón por documento
   pedido (con sus archivos: las 4 fotos del auto se ven por separado) y tres acciones de RH: «Aprobar», «Pedir
   corrección» (elige cuáles; al candidato le llega por WhatsApp la lista y la liga para reemplazarlos) y «Marcar
   excepción» (con motivo). Estado del expediente: Pendiente · Por revisar · Corrección pedida · Documentos aprobados ·
   Excepción. */

import { useState } from "react";
import { Badge } from "@/components/ui";
import { cn } from "@/lib/utils";
import { aprobarDocumentos, excepcionDocumentos, pedirCorreccionDocumentos, urlArchivoDocumento, urlDocumento } from "@/lib/api";
import type { DocExpediente, NuevoIngreso } from "@/lib/phase2";

const TONO_EXPEDIENTE = { pendiente: "neutral", por_revisar: "warn", correccion: "bad", aprobados: "good", excepcion: "brand" } as const;

function tonoDocumento(estado: string): "good" | "warn" | "bad" | "neutral" {
  if (estado === "Aprobado") return "good";
  if (estado === "Por revisar") return "warn";
  if (estado === "Corrección pedida" || estado === "Rechazado") return "bad";
  return "neutral";
}

export function RevisionDocumentos({ expedienteId, expediente, live, onActualizado, onAviso }: {
  expedienteId: number;
  expediente: NuevoIngreso;
  live: boolean;
  onActualizado: (e: NuevoIngreso) => void;
  onAviso: (a: { tono: "ok" | "error"; texto: string }) => void;
}) {
  const id = expedienteId;
  const docs = (expediente.documentos ?? []).filter((d) => !d.interno && d.estado !== "no_aplica");
  const [elegidos, setElegidos] = useState<string[]>([]);
  const [modo, setModo] = useState<"correccion" | "excepcion" | null>(null);
  const [motivo, setMotivo] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const estado = expediente.estadoDocumentos;
  const excepcion = expediente.excepcionDocumentos;
  const entregados = docs.filter((d) => d.estadoRevision === "Por revisar");

  const alternar = (n: string) => setElegidos((x) => (x.includes(n) ? x.filter((y) => y !== n) : [...x, n]));

  async function aprobar() {
    setOcupado(true);
    const r = await aprobarDocumentos(id, elegidos);
    setOcupado(false);
    if (!r.ok) return onAviso({ tono: "error", texto: r.error });
    setElegidos([]);
    onActualizado(r.data.expediente);
    onAviso({ tono: "ok", texto: `Aprobados: ${r.data.aprobados.join(", ")}.` });
  }

  async function confirmar() {
    setOcupado(true);
    if (modo === "correccion") {
      const r = await pedirCorreccionDocumentos(id, elegidos, motivo.trim());
      setOcupado(false);
      if (!r.ok) return onAviso({ tono: "error", texto: r.error });
      onActualizado(r.data.expediente);
      onAviso(r.data.envio.enviado
        ? { tono: "ok", texto: `Corrección pedida (${r.data.correccion.join(", ")}). El candidato recibió la liga para reemplazarlos.` }
        : { tono: "error", texto: `Corrección registrada, pero el aviso no salió: ${r.data.envio.detalle || "sin detalle"}.` });
    } else {
      const r = await excepcionDocumentos(id, motivo.trim());
      setOcupado(false);
      if (!r.ok) return onAviso({ tono: "error", texto: r.error });
      onActualizado(r.data.expediente);
      onAviso({ tono: "ok", texto: "Excepción registrada: la actividad de documentos queda cumplida." });
    }
    setModo(null);
    setMotivo("");
    setElegidos([]);
  }

  async function quitarExcepcion() {
    setOcupado(true);
    const r = await excepcionDocumentos(id, "", true);
    setOcupado(false);
    if (!r.ok) return onAviso({ tono: "error", texto: r.error });
    onActualizado(r.data.expediente);
  }

  const boton2 = "inline-flex min-h-9 items-center rounded-xl border border-border-soft px-3 text-[13px] font-semibold text-ink-2 transition hover:text-ink disabled:opacity-50";
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {estado && <Badge tone={TONO_EXPEDIENTE[estado.clave]} dot>{estado.texto}</Badge>}
        {live && (
          <div className="flex flex-wrap gap-2">
            <button onClick={() => void aprobar()} disabled={ocupado || (elegidos.length === 0 && entregados.length === 0)}
              className="inline-flex min-h-9 items-center rounded-xl bg-brand px-3.5 text-[13px] font-semibold text-brand-ink transition hover:brightness-110 disabled:opacity-50">
              {elegidos.length ? `Aprobar (${elegidos.length})` : "Aprobar"}
            </button>
            <button onClick={() => setModo(modo === "correccion" ? null : "correccion")} disabled={ocupado || elegidos.length === 0} className={boton2}
              title={elegidos.length ? "" : "Marca los documentos que debe reemplazar"}>
              Pedir corrección
            </button>
            {!excepcion && (
              <button onClick={() => setModo(modo === "excepcion" ? null : "excepcion")} disabled={ocupado} className={boton2}>Marcar excepción</button>
            )}
          </div>
        )}
      </div>
      {excepcion && (
        <p className="rounded-lg bg-brand-soft px-3 py-2 text-[13px] text-ink-2">
          Excepción de {excepcion.por}: {excepcion.motivo}
          {live && <button onClick={() => void quitarExcepcion()} disabled={ocupado} className="ml-2 font-semibold text-brand hover:underline">Quitar</button>}
        </p>
      )}
      {modo && (
        <div className="flex flex-col gap-2 rounded-xl border border-border-soft bg-surface-2 p-3">
          <p className="text-[13px] font-semibold text-ink">
            {modo === "correccion" ? `Pedir corrección de: ${elegidos.join(", ")}` : "Marcar excepción (la actividad de documentos queda cumplida)"}
          </p>
          <textarea value={motivo} onChange={(e) => setMotivo(e.target.value)} rows={2}
            placeholder={modo === "correccion" ? "Qué debe corregir (p. ej. «La foto se ve borrosa»). Le llega por WhatsApp." : "Motivo (mínimo 10 caracteres)"}
            className="rounded-xl border border-border-soft bg-surface px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
          <div className="flex justify-end gap-2">
            <button onClick={() => setModo(null)} className={boton2}>Cancelar</button>
            <button onClick={() => void confirmar()} disabled={ocupado || (modo === "excepcion" && motivo.trim().length < 10)}
              className="inline-flex min-h-9 items-center rounded-xl bg-brand px-3.5 text-[13px] font-semibold text-brand-ink disabled:opacity-50">
              {modo === "correccion" ? "Enviar por WhatsApp" : "Marcar excepción"}
            </button>
          </div>
        </div>
      )}
      {docs.map((d) => (
        <FilaRevision key={d.nombre} d={d} expedienteId={id} elegido={elegidos.includes(d.nombre)} onElegir={live ? () => alternar(d.nombre) : undefined} />
      ))}
    </div>
  );
}

function FilaRevision({ d, expedienteId, elegido, onElegir }: {
  d: DocExpediente; expedienteId: number; elegido: boolean; onElegir?: () => void;
}) {
  const estado = d.estadoRevision ?? d.estadoOnboarding ?? "Pendiente";
  const cantidad = d.cantidad ?? 1;
  const archivos = d.archivos ?? [];
  return (
    <div className={cn("rounded-xl border bg-surface px-3.5 py-2.5", elegido ? "border-brand" : "border-border-soft")}>
      <div className="flex items-center justify-between gap-2">
        <label className="flex min-w-0 items-center gap-2 text-sm">
          {onElegir && <input type="checkbox" checked={elegido} onChange={onElegir} className="h-4 w-4 accent-[var(--brand)]" />}
          <span className="truncate font-medium">{d.nombre}</span>
          {cantidad > 1 && <span className="shrink-0 text-[12px] text-ink-3">{Math.min(d.recibidos ?? 0, cantidad)}/{cantidad}</span>}
        </label>
        <Badge tone={tonoDocumento(estado)}>{estado}</Badge>
      </div>
      {d.correccion && estado === "Corrección pedida" && (
        <p className="mt-1 text-[12px] text-bad">Corrección pedida por {d.correccion.por}{d.correccion.motivo ? `: ${d.correccion.motivo}` : ""}</p>
      )}
      {(archivos.length > 0 || d.tieneArchivo) && (
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
          {archivos.length > 0
            ? archivos.map((a, i) => (
                <a key={a.id} href={urlArchivoDocumento(expedienteId, a.id)} target="_blank" rel="noreferrer" className="text-xs font-semibold text-brand hover:underline">
                  {cantidad > 1 ? `Foto ${i + 1}` : "Ver"}{a.convertidoDe ? " (de HEIC)" : ""}
                </a>
              ))
            : (
              <a href={urlDocumento(expedienteId, d.nombre)} target="_blank" rel="noreferrer" className="text-xs font-semibold text-brand hover:underline">Ver</a>
            )}
        </div>
      )}
    </div>
  );
}
