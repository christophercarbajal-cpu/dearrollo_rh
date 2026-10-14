"use client";

/* Cambio 43 (2026-10-14): «Textos por plataforma». El Markdown que genera Red Human se PINTA (títulos, negritas, listas) —
   nunca se ven «#», «##» ni «**»—; WhatsApp va siempre sin Markdown. Cada texto trae «Copiar»: el portapapeles lleva el
   texto limpio (y HTML para pegar con formato) MÁS la liga de postulación con su canal (`?canal=occ`, `?canal=whatsapp`…). */

import { useState } from "react";
import { Check, Copy, Pencil } from "lucide-react";
import { cn } from "@/lib/utils";
import { bloquesMarkdown, htmlParaCopiar, markdownATexto, textoParaCopiar, type ParteMd } from "@/lib/markdown-texto";

function Partes({ partes }: { partes: ParteMd[] }) {
  return (
    <>
      {partes.map((p, i) =>
        p.negrita ? <strong key={i} className="font-semibold text-ink">{p.texto}</strong>
          : p.cursiva ? <em key={i}>{p.texto}</em>
            : <span key={i}>{p.texto}</span>,
      )}
    </>
  );
}

/** Markdown pintado (sin símbolos crudos). */
export function MarkdownVista({ texto, className }: { texto: string; className?: string }) {
  const bloques = bloquesMarkdown(texto);
  if (!bloques.length) return <p className="text-sm text-ink-3">Sin texto.</p>;
  return (
    <div className={cn("flex flex-col gap-2 text-[13px] leading-relaxed text-ink-2", className)}>
      {bloques.map((b, i) => {
        if (b.tipo === "titulo") {
          return <p key={i} className={cn("font-semibold text-ink", b.nivel <= 1 ? "text-base" : b.nivel === 2 ? "text-sm" : "text-[13px]")}><Partes partes={b.partes} /></p>;
        }
        if (b.tipo === "vineta") return <p key={i} className="flex gap-2 pl-1"><span aria-hidden>•</span><span><Partes partes={b.partes} /></span></p>;
        if (b.tipo === "numerada") return <p key={i} className="flex gap-2 pl-1"><span>{b.numero}.</span><span><Partes partes={b.partes} /></span></p>;
        if (b.tipo === "separador") return <hr key={i} className="border-border-faint" />;
        return <p key={i}><Partes partes={b.partes} /></p>;
      })}
    </div>
  );
}

/** «Copiar» con la liga del canal: texto plano limpio + HTML (texto enriquecido) cuando el navegador lo permite. */
export function BotonCopiarTexto({ texto, liga, etiqueta = "Copiar" }: { texto: string; liga: string; etiqueta?: string }) {
  const [copiado, setCopiado] = useState(false);
  async function copiar() {
    const plano = textoParaCopiar(texto, liga);
    try {
      if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
        await navigator.clipboard.write([new ClipboardItem({
          "text/plain": new Blob([plano], { type: "text/plain" }),
          "text/html": new Blob([htmlParaCopiar(texto, liga)], { type: "text/html" }),
        })]);
      } else {
        await navigator.clipboard.writeText(plano);
      }
    } catch {
      try {
        await navigator.clipboard.writeText(plano);
      } catch {
        return; // el navegador bloqueó el portapapeles (contexto no seguro)
      }
    }
    setCopiado(true);
    setTimeout(() => setCopiado(false), 1800);
  }
  return (
    <button type="button" onClick={copiar} disabled={!texto.trim()}
      className="inline-flex items-center gap-1 text-xs font-medium text-brand transition hover:underline disabled:opacity-40">
      {copiado ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      {copiado ? "Copiado" : etiqueta}
    </button>
  );
}

/** Un texto por plataforma: vista pintada + «Editar» (Markdown) + «Copiar» con la liga del canal. */
export function TextoPlataforma({ etiqueta, canal, texto, liga, onChange, sinMarkdown = false, provisional = false }: {
  etiqueta: string;
  canal: string;
  texto: string;
  liga: string;
  onChange?: (t: string) => void;
  /** WhatsApp: siempre sin Markdown (se limpia al mostrar, al editar y al copiar). */
  sinMarkdown?: boolean;
  /** La vacante aún no se guarda: la liga se calculó con el puesto y queda fija al publicar. */
  provisional?: boolean;
}) {
  const [editar, setEditar] = useState(false);
  const limpio = sinMarkdown ? markdownATexto(texto) : texto;
  return (
    <div className="flex flex-col gap-1.5" data-canal={canal}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-ink-2">{etiqueta}</span>
        <span className="flex items-center gap-3">
          {onChange && (
            <button type="button" onClick={() => setEditar((x) => !x)}
              className="inline-flex items-center gap-1 text-xs font-medium text-ink-3 transition hover:text-ink">
              <Pencil className="h-3.5 w-3.5" /> {editar ? "Ver" : "Editar"}
            </button>
          )}
          <BotonCopiarTexto texto={limpio} liga={liga} />
        </span>
      </div>
      {editar && onChange ? (
        <textarea value={limpio} rows={sinMarkdown ? 4 : 8} onChange={(e) => onChange(sinMarkdown ? e.target.value : e.target.value)}
          className="w-full rounded-xl border border-border-soft bg-surface px-3 py-2 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20" />
      ) : sinMarkdown ? (
        <p className="whitespace-pre-wrap rounded-xl bg-surface-2 p-3 text-[13px] leading-relaxed text-ink-2">{limpio || "Sin texto."}</p>
      ) : (
        <MarkdownVista texto={texto} className="max-h-80 overflow-y-auto rounded-xl bg-surface-2 p-3" />
      )}
      {liga && (
        <p className="truncate text-[11px] text-ink-3" title={liga}>
          Liga al copiar: {liga}{provisional ? " (provisional: queda fija al publicar)" : ""}
        </p>
      )}
    </div>
  );
}

export type PestanaTexto = Omit<Parameters<typeof TextoPlataforma>[0], "canal"> & { canal: string };

/** Cambio 45 (2026-10-14): «Textos por plataforma» en PESTAÑAS horizontales (deslizables si no caben). La primera
 * (WhatsApp) queda activa al cargar y debajo se pinta SOLO el texto de la pestaña elegida, con su «Editar» y su «Copiar»
 * (liga `?canal=`). */
export function PestanasTextoPlataforma({ pestanas }: { pestanas: PestanaTexto[] }) {
  const [activa, setActiva] = useState<string>(pestanas[0]?.canal ?? "");
  const actual = pestanas.find((p) => p.canal === activa) ?? pestanas[0];
  if (!actual) return null;
  return (
    <div className="flex flex-col gap-3" data-pestanas-plataforma>
      <div role="tablist" aria-label="Textos por plataforma" className="scroll-x gap-1 rounded-xl border border-border-faint bg-surface-2/50 p-1">
        {pestanas.map((p) => (
          <button key={p.canal} type="button" role="tab" aria-selected={p.canal === actual.canal} data-pestana={p.canal}
            onClick={() => setActiva(p.canal)}
            className={cn("rounded-lg px-3 py-1.5 text-[13px] font-medium transition",
              p.canal === actual.canal ? "bg-surface text-ink shadow-sm" : "text-ink-3 hover:text-ink")}>
            {p.etiqueta}
          </button>
        ))}
      </div>
      {/* key = canal: al cambiar de pestaña el modo «Editar» de la anterior no se arrastra */}
      <div role="tabpanel">
        <TextoPlataforma key={actual.canal} {...actual} />
      </div>
    </div>
  );
}
