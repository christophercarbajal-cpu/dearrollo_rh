"use client";

/* 2026-10-16 (Bloque 2) — Vista previa de las «Preguntas filtro» en la ficha de la vacante.

   Una pregunta por requisito indispensable (Red Human propone siempre 3; RH puede llegar a 5). Cada una dice de dónde
   salió («De tu descripción», «Propuesto por Red Human», «Agregado por ti») y se puede Editar o Borrar; «Regenerar» vuelve
   a proponer lo que no agregó RH. Se ve DOS veces, en el orden de la ruta:
     · Solicitud web: SOLO la versión Sí/No de cada requisito (la web registra, nunca descarta).
     · Prefiltro por WhatsApp: la pregunta conversacional del chat (+ las preguntas base, que se quitan con la «X»).
   Borrar o agregar una pregunta cambia la lista de requisitos indispensables (son la misma cosa). */

import { useState } from "react";
import { Pencil, Plus, RefreshCw, RotateCcw, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui";
import {
  ETIQUETA_ORIGEN,
  MAX_PREGUNTAS_FILTRO,
  textoDescartaSi,
  type OrigenPregunta,
  type PreguntaFiltroU,
} from "@/lib/api";
import { cn } from "@/lib/utils";

export interface CambioPreguntas {
  filtro: PreguntaFiltroU[];
  requisitos: string[];
  indispensables_propuestos: string[];
}

const clave = (t: string) =>
  t
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .trim();

const ESTILO_ORIGEN: Record<OrigenPregunta, string> = {
  descripcion: "bg-bg-2 text-ink-2",
  red_human: "bg-brand-soft text-brand",
  rh: "bg-good-soft text-good",
};

export function origenDe(q: PreguntaFiltroU, propuestos: string[]): OrigenPregunta {
  if (q.origen) return q.origen;
  return propuestos.some((p) => clave(p) === clave(q.requisito)) ? "red_human" : "rh";
}

/** La pregunta que ve el candidato en la solicitud web (Sí/No). */
export function preguntaWeb(q: PreguntaFiltroU): string {
  return q.pregunta_web || q.pregunta;
}

function minusculaInicial(t: string) {
  const x = t.trim().replace(/\.$/, "");
  return x.length > 1 && x.slice(0, 2) !== x.slice(0, 2).toUpperCase() ? x : x.charAt(0).toLowerCase() + x.slice(1);
}

/** Pregunta nueva «Agregada por ti»: Sí/No directa (el servidor la ajusta al tipo de requisito al guardar). */
export function nuevaPregunta(requisito: string, pregunta: string): PreguntaFiltroU {
  const req = requisito.trim();
  const texto = pregunta.trim() || `¿Tienes ${minusculaInicial(req)}?`;
  return {
    id: `nuevo-${Date.now()}`,
    requisito: req,
    pregunta: texto.startsWith("¿") ? texto : `¿${texto.replace(/\?$/, "")}?`,
    tipo: "si_no",
    dato: "requisito",
    descarta: true,
    regla: "Descarta si responde que no",
    base: false,
    indispensable: true,
    origen: "rh",
    etiqueta: ETIQUETA_ORIGEN.rh,
    opciones: ["Sí", "No"],
  };
}

function Origen({ origen }: { origen: OrigenPregunta }) {
  return (
    <span className={cn("w-fit rounded-full px-2 py-0.5 text-[10px] font-medium", ESTILO_ORIGEN[origen])}>
      {ETIQUETA_ORIGEN[origen]}
    </span>
  );
}

export function VistaPreviaPreguntas({
  canal,
  requisitos,
  filtro,
  propuestos,
  onChange,
  onRegenerar,
  regenerando = false,
  error = "",
}: {
  canal: "web" | "whatsapp";
  requisitos: string[];
  filtro: PreguntaFiltroU[];
  propuestos: string[];
  onChange: (c: CambioPreguntas) => void;
  onRegenerar?: () => void;
  regenerando?: boolean;
  error?: string;
}) {
  const [editando, setEditando] = useState<string | null>(null);
  const [borrador, setBorrador] = useState("");
  const [agregando, setAgregando] = useState(false);
  const [nuevoReq, setNuevoReq] = useState("");
  const [nuevaQ, setNuevaQ] = useState("");

  const delRequisito = (q: PreguntaFiltroU) => requisitos.some((r) => clave(r) === clave(q.requisito));
  const preguntas = filtro.filter((q) => q.indispensable && delRequisito(q));
  const sinPregunta = requisitos.filter((r) => r.trim() && !filtro.some((q) => q.indispensable && clave(q.requisito) === clave(r)));
  const total = preguntas.length + sinPregunta.length;
  const bases = filtro.filter((q) => q.base && !q.indispensable);
  const basesVisibles = bases.filter((q) => !q.quitada && (canal === "whatsapp" || q.tipo !== "abierta"));
  const quitadas = bases.filter((q) => q.quitada);

  const emitir = (nuevoFiltro: PreguntaFiltroU[], nuevosReq = requisitos, nuevosProp = propuestos) =>
    onChange({ filtro: nuevoFiltro, requisitos: nuevosReq, indispensables_propuestos: nuevosProp });

  const textoDe = (q: PreguntaFiltroU) => (canal === "web" ? preguntaWeb(q) : q.pregunta);
  const guardarEdicion = (q: PreguntaFiltroU) => {
    const texto = borrador.trim();
    if (texto) {
      // en la web se edita la versión Sí/No (si la pregunta del chat es otra); en el chat, la conversacional
      const campo: "pregunta" | "pregunta_web" = canal === "web" && (q.pregunta_web || q.tipo !== "si_no") ? "pregunta_web" : "pregunta";
      emitir(filtro.map((x) => (x.id === q.id ? { ...x, [campo]: texto } : x)));
    }
    setEditando(null);
  };
  const borrar = (requisito: string) =>
    emitir(
      filtro.filter((x) => !(x.indispensable && clave(x.requisito) === clave(requisito))),
      requisitos.filter((r) => clave(r) !== clave(requisito)),
      propuestos.filter((p) => clave(p) !== clave(requisito)),
    );
  const agregar = () => {
    const req = nuevoReq.trim();
    if (!req || total >= MAX_PREGUNTAS_FILTRO || requisitos.some((r) => clave(r) === clave(req))) return;
    emitir([...filtro, nuevaPregunta(req, nuevaQ)], [...requisitos, req]);
    setNuevoReq("");
    setNuevaQ("");
    setAgregando(false);
  };
  const alternarBase = (q: PreguntaFiltroU, quitar: boolean) => emitir(filtro.map((x) => (x.id === q.id ? { ...x, quitada: quitar } : x)));

  return (
    <div className="flex flex-col gap-3" data-vista-previa={canal}>
      <p className="text-[12px] text-ink-3">
        {canal === "web"
          ? "Así las ve el candidato en la solicitud: solo Sí / No. La solicitud registra las respuestas; nunca descarta."
          : "Así pregunta Red Human en el chat, en modo conversación y de una en una."}
      </p>
      <ol className="flex flex-col gap-2">
        {preguntas.map((q, i) => (
          <li key={q.id} className="rounded-xl border border-border-soft bg-surface px-3 py-2.5">
            <div className="flex items-start gap-2">
              <span className="mt-0.5 text-xs font-semibold text-ink-3">{i + 1}.</span>
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                {editando === q.id ? (
                  <input autoFocus value={borrador} onChange={(e) => setBorrador(e.target.value)} aria-label={`Editar pregunta de ${q.requisito}`}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); guardarEdicion(q); } if (e.key === "Escape") setEditando(null); }}
                    onBlur={() => guardarEdicion(q)}
                    className="min-h-9 w-full rounded-lg border border-brand bg-surface px-2.5 text-sm outline-none" />
                ) : (
                  <p className="text-sm text-ink">{textoDe(q)}</p>
                )}
                {canal === "web" && <p className="text-[11px] text-ink-3">Respuestas: Sí · No</p>}
                <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-ink-3">
                  <Origen origen={origenDe(q, propuestos)} />
                  <span>Requisito: {q.requisito}</span>
                  {canal === "whatsapp" && <span>· {textoDescartaSi(q) === "No descarta" ? "No descarta" : `Descarta si: ${textoDescartaSi(q)}`}</span>}
                </div>
              </div>
              <div className="flex shrink-0 gap-1">
                <button type="button" onClick={() => { setEditando(q.id); setBorrador(textoDe(q)); }} aria-label={`Editar ${q.requisito}`}
                  className="rounded-lg p-1.5 text-ink-3 hover:bg-bg-2 hover:text-ink"><Pencil className="h-3.5 w-3.5" /></button>
                <button type="button" onClick={() => borrar(q.requisito)} aria-label={`Borrar ${q.requisito}`}
                  className="rounded-lg p-1.5 text-ink-3 hover:bg-bad-soft hover:text-bad"><Trash2 className="h-3.5 w-3.5" /></button>
              </div>
            </div>
          </li>
        ))}
        {sinPregunta.map((r) => (
          <li key={`nuevo-${r}`} className="flex items-center justify-between gap-2 rounded-xl border border-dashed border-border-soft px-3 py-2 text-xs text-ink-3">
            <span><b className="text-ink-2">{r}</b> · Red Human arma su pregunta al guardar.</span>
            <button type="button" onClick={() => borrar(r)} aria-label={`Borrar ${r}`} className="rounded-lg p-1 hover:text-bad"><Trash2 className="h-3.5 w-3.5" /></button>
          </li>
        ))}
        {!total && <li className="text-xs text-ink-3">Sin preguntas todavía. Usa «Regenerar» o agrega una.</li>}
      </ol>

      {agregando && (
        <div className="flex flex-col gap-2 rounded-xl border border-border-soft p-3">
          <input value={nuevoReq} onChange={(e) => setNuevoReq(e.target.value)} placeholder="Requisito (p. ej. Licencia de manejo tipo B)"
            className="min-h-9 rounded-lg border border-border-soft bg-surface px-2.5 text-sm outline-none focus:border-brand" aria-label="Requisito nuevo" />
          <input value={nuevaQ} onChange={(e) => setNuevaQ(e.target.value)} placeholder="Pregunta (opcional; si la dejas vacía, Red Human la arma)"
            className="min-h-9 rounded-lg border border-border-soft bg-surface px-2.5 text-sm outline-none focus:border-brand" aria-label="Pregunta nueva" />
          <div className="flex gap-2">
            <Button type="button" size="sm" onClick={agregar} disabled={!nuevoReq.trim()}>Agregar</Button>
            <Button type="button" size="sm" variant="outline" onClick={() => setAgregando(false)}>Cancelar</Button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => setAgregando(true)} disabled={total >= MAX_PREGUNTAS_FILTRO || agregando}>
          <Plus className="h-3.5 w-3.5" /> Agregar pregunta
        </Button>
        {onRegenerar && (
          <Button type="button" size="sm" variant="outline" onClick={onRegenerar} disabled={regenerando}>
            <RefreshCw className="h-3.5 w-3.5" /> {regenerando ? "Regenerando…" : "Regenerar"}
          </Button>
        )}
        <span className="text-[11px] text-ink-3">{total} de {MAX_PREGUNTAS_FILTRO} como máximo</span>
      </div>
      {error && <p className="text-xs text-bad">{error}</p>}

      {(basesVisibles.length > 0 || quitadas.length > 0) && (
        <div className="flex flex-col gap-1.5 border-t border-border-faint pt-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-3">Preguntas base · solo se registran</p>
          {basesVisibles.map((q) => (
            <div key={q.id} className="flex items-center justify-between gap-2 rounded-lg bg-surface-2 px-3 py-1.5 text-sm text-ink-2">
              <span>{q.pregunta} <span className="ml-1 rounded-full bg-bg-2 px-2 py-0.5 text-[10px] text-ink-2">Base</span></span>
              <button type="button" onClick={() => alternarBase(q, true)} aria-label={`Quitar ${q.requisito}`}
                className="rounded-lg p-1 text-ink-3 hover:bg-bad-soft hover:text-bad"><X className="h-3.5 w-3.5" /></button>
            </div>
          ))}
          {quitadas.map((q) => (
            <div key={q.id} className="flex items-center justify-between gap-2 px-3 py-1 text-xs text-ink-3">
              <span className="line-through">{q.pregunta}</span>
              <button type="button" onClick={() => alternarBase(q, false)} className="inline-flex items-center gap-1 hover:text-ink">
                <RotateCcw className="h-3 w-3" /> Restaurar
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
