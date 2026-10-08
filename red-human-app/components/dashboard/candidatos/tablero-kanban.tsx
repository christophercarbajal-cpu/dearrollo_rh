"use client";

/* Tablero de Candidatos (rediseño 2026-10-07, red-human-kanban-completo.md). Aplica a TODAS las Cuentas.
   Cada tarjeta tiene UN protagonista (el score) y tres niveles de lectura:
     1. De lejos: nombre + score (color por nivel).
     2. Si interesa: puesto · canal, siguiente paso y el porqué del score.
     3. Solo si hay problema: alertas (sin consentimiento, atorado, expediente incompleto, psicometría pendiente).
   Los datos son los campos calculados de la API (`name`, `score_reason`, `source_channel`, `next_step`,
   `stage_entered_at`, `has_consent`, `expediente_pct`, `psychometric_alert`); si alguno falta, su zona se oculta (nunca
   se pinta «null», «undefined» ni texto de relleno). Presentación pura: la etapa la sigue definiendo la ruta. */

import { useState } from "react";
import { AlertTriangle, ArrowRight, Clock } from "lucide-react";
import { nombreEtapa, type MetricaEtapaTablero } from "@/lib/api";
import type { Candidato, EtapaCandidato } from "@/lib/data";
import { cn } from "@/lib/utils";

/** Umbral de «atorado» (días en la etapa). */
export const DIAS_ATORADO = 7;
/** Tarjetas visibles por columna antes de «Ver N más». */
const TARJETAS_POR_COLUMNA = 20;

export const COLOR_ETAPA: Record<EtapaCandidato, string> = {
  Prefiltro: "#8E8880",
  "Entrevista IA": "#D2453D",
  "Entrevista Humana": "#C2410C",
  Contratación: "#B7791F",
  Onboarding: "#2E8A57",
};

const CANAL: Record<string, string> = { whatsapp: "WhatsApp", web: "Web", referido: "Referido", telegram: "Telegram" };
const FILTRO: Record<string, [string, string]> = {
  cumple: ["Cumple", "bg-kb-green-bg text-kb-green"],
  revisar: ["Revisar", "bg-kb-amber-bg text-kb-amber"],
  no_cumple: ["No cumple", "bg-kb-red-bg text-kb-red"],
};
const ALERTA_PSICO: Record<string, string> = { sin_enviar: "Psicométrica sin enviar", sin_respuesta: "Psicométrica sin respuesta" };

/** Score protagonista: null si no hay análisis de CV (un 0 sin CV no es una calificación). */
export function scoreTarjeta(c: Candidato): number | null {
  if (c.score == null) return null;
  return c.score > 0 || c.score_reason ? c.score : null;
}
export function diasEnEtapa(c: Candidato): number | null {
  const desde = c.stage_entered_at ?? c.creadoEn;
  if (!desde) return null;
  const t = new Date(desde).getTime();
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.floor((Date.now() - t) / 864e5));
}
export const estaAtorado = (c: Candidato) => c.activa !== false && (diasEnEtapa(c) ?? 0) > DIAS_ATORADO;
export const sinConsentimiento = (c: Candidato) => c.activa !== false && (c.has_consent ?? c.consentimiento) === false;
export const expedienteIncompleto = (c: Candidato) =>
  c.etapa === "Contratación" && c.expediente_pct != null && c.expediente_pct < 100;

function tonoScore(s: number | null): string {
  if (s == null) return "bg-kb-gray-bg text-kb-gray";
  if (s >= 85) return "bg-kb-green-bg text-kb-green";
  if (s >= 70) return "bg-kb-amber-bg text-kb-amber";
  return "bg-kb-red-bg text-kb-red";
}

/** Orden por defecto: score descendente (sin score al final); alternativo: más días en la etapa primero. */
export type OrdenTablero = "score" | "dias";
export function ordenarTablero(a: Candidato, b: Candidato, orden: OrdenTablero): number {
  if (orden === "dias") return (diasEnEtapa(b) ?? -1) - (diasEnEtapa(a) ?? -1);
  return (scoreTarjeta(b) ?? -1) - (scoreTarjeta(a) ?? -1);
}

/* ---------------------------------------------------------------- Chips de alerta (reemplazan el banner) */

export type AlertaTablero = "consentimiento" | "atorados" | "expediente";
export const ALERTAS_TABLERO: { id: AlertaTablero; prueba: (c: Candidato) => boolean; texto: (n: number) => string; clase: string; icono?: boolean }[] = [
  { id: "consentimiento", prueba: sinConsentimiento, texto: (n) => `${n} sin consentimiento`, clase: "border-[#F3C4BF] bg-kb-red-bg text-kb-red dark:border-kb-red/40", icono: true },
  { id: "atorados", prueba: estaAtorado, texto: (n) => `${n} atorado${n === 1 ? "" : "s"} más de ${DIAS_ATORADO} días`, clase: "border-[#F0D9A8] bg-kb-amber-bg text-kb-amber dark:border-kb-amber/40", icono: true },
  { id: "expediente", prueba: expedienteIncompleto, texto: (n) => `${n} expediente${n === 1 ? "" : "s"} incompleto${n === 1 ? "" : "s"}`, clase: "border-kb-line-2 bg-kb-card font-medium text-kb-ink-2" },
];

export function ChipsAlerta({ datos, activa, onCambiar }: {
  /** Universo de las alertas: el tablero con vacante/búsqueda aplicadas, sin el filtro de alerta. */
  datos: Candidato[];
  activa: AlertaTablero | null;
  onCambiar: (a: AlertaTablero | null) => void;
}) {
  const chips = ALERTAS_TABLERO.map((a) => ({ ...a, n: datos.filter(a.prueba).length })).filter((a) => a.n > 0 || activa === a.id);
  if (!chips.length) return null;
  return (
    <div className="mt-4 flex flex-wrap gap-2">
      {chips.map((a) => (
        <button
          key={a.id}
          type="button"
          aria-pressed={activa === a.id}
          onClick={() => onCambiar(activa === a.id ? null : a.id)}
          className={cn(
            "flex h-9 items-center gap-1.5 rounded-full border px-3.5 text-[13px] font-semibold tabular-nums transition",
            a.clase,
            activa === a.id && "outline outline-2 outline-offset-1 outline-current",
          )}
        >
          {a.icono && (a.id === "atorados" ? <Clock className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />)}
          {a.texto(a.n)}
        </button>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- Tarjeta */

function Chip({ clase, children, title }: { clase: string; children: React.ReactNode; title?: string }) {
  return (
    <span title={title} className={cn("inline-flex h-6 items-center rounded-full px-[9px] text-xs font-semibold", clase)}>
      {children}
    </span>
  );
}

export function TarjetaCandidato({ c, onAbrir, onIntentoArrastre }: {
  c: Candidato;
  onAbrir: (c: Candidato) => void;
  onIntentoArrastre?: () => void;
}) {
  const score = scoreTarjeta(c);
  const dias = diasEnEtapa(c);
  const atorado = estaAtorado(c);
  const nombre = c.name || c.nombre;
  const puesto = c.role || c.puesto;
  const canal = c.source_channel ? CANAL[c.source_channel] : "";
  const subtitulo = [puesto, canal].filter(Boolean).join(" · ");
  const razon = c.score_reason;
  const porque = razon
    ? [razon.fortaleza ? `Fuerte: ${razon.fortaleza}` : "", razon.faltante ? `Falta: ${razon.faltante}` : ""].filter(Boolean).join(" · ")
    : "";
  const pct = c.etapa === "Contratación" && c.expediente_pct != null ? Math.max(0, Math.min(100, c.expediente_pct)) : null;
  const filtro = c.filter_status ? FILTRO[c.filter_status] : null;
  const cerrada = c.activa === false;
  const motivo = c.motivoDescarte || c.motivoCierre || "";

  return (
    <button
      type="button"
      onClick={() => onAbrir(c)}
      draggable={Boolean(onIntentoArrastre)}
      onDragStart={(e) => {
        e.preventDefault();
        onIntentoArrastre?.();
      }}
      className={cn(
        "flex w-full flex-col gap-2.5 rounded-xl border border-kb-line bg-kb-card p-3.5 text-left text-kb-ink shadow-[0_1px_2px_rgba(28,27,25,0.04)] tabular-nums transition hover:border-[#D6D1CA] focus-visible:outline-2 focus-visible:outline-brand dark:hover:border-kb-line-2",
        cerrada && "opacity-75",
      )}
    >
      <div className="flex items-start gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-[3px]">
          <p className="line-clamp-2 text-[15px] font-semibold leading-[1.3]">{nombre}</p>
          {subtitulo && <p className="text-[13px] text-kb-ink-2">{subtitulo}</p>}
        </div>
        <span
          aria-label={score == null ? "Sin score" : `Score ${score}`}
          className={cn("grid h-[46px] w-[46px] shrink-0 place-items-center rounded-xl text-[19px] font-bold", tonoScore(score))}
        >
          {score == null ? "—" : score}
        </span>
      </div>

      {c.next_step && (
        <p className="flex items-start gap-1.5 text-[13px] font-medium">
          <ArrowRight className="mt-[3px] h-3.5 w-3.5 shrink-0 text-kb-ink-3" />
          <span className="min-w-0 break-words">{c.next_step}</span>
        </p>
      )}

      {porque && <p className="rounded-lg bg-kb-bg px-2.5 py-2 text-[12.5px] leading-[1.45] text-kb-ink-2">{porque}</p>}

      {pct != null && (
        <div>
          <div className="flex justify-between text-xs text-kb-ink-2">
            <span>Expediente</span>
            <b className="text-kb-ink">{pct}%</b>
          </div>
          <div className="mt-[5px] h-1.5 overflow-hidden rounded-full bg-kb-bar">
            <span className="block h-full rounded-full" style={{ width: `${pct}%`, background: pct >= 100 ? "var(--kb-bar-ok)" : "var(--kb-bar-warn)" }} />
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1.5">
        {filtro && <Chip clase={filtro[1]} title={c.filter_status === "no_cumple" && motivo ? motivo : undefined}>{filtro[0]}</Chip>}
        {cerrada && c.motivoCierre !== "descartado" && <Chip clase="bg-kb-gray-bg text-kb-gray" title={motivo}>Cerrada</Chip>}
        {sinConsentimiento(c) && <Chip clase="bg-kb-red-bg text-kb-red">Sin consentimiento</Chip>}
        {c.psychometric_alert && ALERTA_PSICO[c.psychometric_alert] && (
          <Chip clase="bg-kb-amber-bg text-kb-amber">{ALERTA_PSICO[c.psychometric_alert]}</Chip>
        )}
        {dias != null && (
          <span className={cn("ml-auto inline-flex items-center gap-1 text-xs font-medium text-kb-ink-3", atorado && "font-semibold text-kb-amber")}>
            <Clock className="h-3 w-3" />
            {dias} d{atorado ? " · atorado" : ""}
          </span>
        )}
      </div>
    </button>
  );
}

/* ---------------------------------------------------------------- Columna y tablero */

function Columna({ etapa, tarjetas, metrica, filtrando, resaltada, onAbrir, onIntentoArrastre }: {
  etapa: EtapaCandidato;
  tarjetas: Candidato[];
  metrica?: MetricaEtapaTablero;
  filtrando: boolean;
  resaltada: boolean;
  onAbrir: (c: Candidato) => void;
  onIntentoArrastre?: () => void;
}) {
  const [limite, setLimite] = useState(TARJETAS_POR_COLUMNA);
  const visibles = tarjetas.slice(0, limite);
  const total = filtrando ? tarjetas.length : metrica?.total ?? tarjetas.length;
  const resto = Math.max(tarjetas.length - visibles.length, 0);
  const meta = [
    metrica ? (metrica.conversion_pct != null ? `${metrica.conversion_pct}% pasa` : etapa === "Prefiltro" ? "Entrada" : null) : null,
    metrica?.avg_days != null ? `${metrica.avg_days} d promedio` : null,
  ].filter(Boolean).join(" · ");

  return (
    <section id={`columna-etapa-${etapa.replace(/\s/g, "-")}`} className="flex w-[268px] flex-none flex-col gap-2.5">
      <header className={cn("border-b-2 px-1 pb-1.5", resaltada ? "border-brand" : "border-kb-line-2")}>
        <div className="flex items-center gap-2 whitespace-nowrap">
          <span className="h-[9px] w-[9px] shrink-0 rounded-full" style={{ background: COLOR_ETAPA[etapa] }} />
          <span className="text-[15px] font-semibold text-kb-ink">{nombreEtapa(etapa)}</span>
          <span className="ml-auto text-[13px] font-semibold tabular-nums text-kb-ink-2">{total}</span>
        </div>
        {meta && <p className="mt-1 text-xs tabular-nums text-kb-ink-3">{meta}</p>}
      </header>
      {visibles.map((c) => (
        <TarjetaCandidato key={c.id} c={c} onAbrir={onAbrir} onIntentoArrastre={onIntentoArrastre} />
      ))}
      {tarjetas.length === 0 && <p className="px-1 py-3 text-[13px] text-kb-ink-3">Sin candidatos</p>}
      {resto > 0 && (
        <button
          type="button"
          onClick={() => setLimite((l) => l + TARJETAS_POR_COLUMNA)}
          className="h-10 rounded-[10px] border border-dashed border-[#D6D1CA] text-[13px] text-kb-ink-2 transition hover:bg-kb-card dark:border-kb-line-2"
        >
          Ver {resto} más
        </button>
      )}
    </section>
  );
}

export function TableroKanban({ etapas, datos, metricas, orden, filtrando, columnaResaltada, onAbrir, onIntentoArrastre }: {
  etapas: EtapaCandidato[];
  /** Tarjetas YA filtradas (vacante, búsqueda, alerta, filtros avanzados). */
  datos: Candidato[];
  metricas: MetricaEtapaTablero[];
  orden: OrdenTablero;
  /** Con un filtro activo el conteo de la columna es el de las tarjetas visibles, no el total de la etapa. */
  filtrando: boolean;
  columnaResaltada?: string | null;
  onAbrir: (c: Candidato) => void;
  onIntentoArrastre?: () => void;
}) {
  return (
    <div className="mt-6 overflow-x-auto pb-2">
      <div className="flex min-w-min items-start gap-4">
        {etapas.map((etapa) => (
          <Columna
            key={etapa}
            etapa={etapa}
            tarjetas={datos.filter((c) => c.etapa === etapa).sort((a, b) => ordenarTablero(a, b, orden))}
            metrica={metricas.find((m) => m.etapa === etapa)}
            filtrando={filtrando}
            resaltada={columnaResaltada === etapa}
            onAbrir={onAbrir}
            onIntentoArrastre={onIntentoArrastre}
          />
        ))}
      </div>
    </div>
  );
}
