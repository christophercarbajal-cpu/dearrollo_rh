"use client";

/* Firma de DEMOSTRACIÓN en la plataforma (2026-10-09): el firmante dibuja su firma (lienzo táctil o mouse) o escribe su
   nombre completo. La misma pieza la usan RH (ficha → «Firmar documentos») y el candidato (su liga de expediente). El
   PDF final lleva marca de agua «DEMOSTRACIÓN · SIN VALIDEZ LEGAL» y NUNCA se llama a Dropbox Sign. */

import { useEffect, useRef, useState } from "react";
import { Eraser, Loader2, PenLine, Type } from "lucide-react";
import { cn } from "@/lib/utils";

export type FirmaCapturada = { imagen?: string; texto?: string };

export function PadFirma({ nombreSugerido = "", ocupado = false, onFirmar, onCancelar, error = "" }: {
  nombreSugerido?: string;
  ocupado?: boolean;
  onFirmar: (f: FirmaCapturada) => void;
  onCancelar: () => void;
  error?: string;
}) {
  const [modo, setModo] = useState<"dibujar" | "escribir">("dibujar");
  const [texto, setTexto] = useState(nombreSugerido);
  const [trazos, setTrazos] = useState(0);
  const lienzo = useRef<HTMLCanvasElement>(null);
  const dibujando = useRef(false);

  useEffect(() => {
    const cv = lienzo.current;
    if (!cv) return;
    const escala = window.devicePixelRatio || 1;
    cv.width = cv.clientWidth * escala;
    cv.height = cv.clientHeight * escala;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.scale(escala, escala);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, cv.clientWidth, cv.clientHeight);
    ctx.lineWidth = 2.2;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#141450";
  }, [modo]);

  function punto(e: React.PointerEvent<HTMLCanvasElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }
  function inicio(e: React.PointerEvent<HTMLCanvasElement>) {
    const ctx = e.currentTarget.getContext("2d");
    if (!ctx) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    dibujando.current = true;
    const p = punto(e);
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
  }
  function mover(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!dibujando.current) return;
    const ctx = e.currentTarget.getContext("2d");
    if (!ctx) return;
    const p = punto(e);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
  }
  function fin() {
    if (dibujando.current) setTrazos((n) => n + 1);
    dibujando.current = false;
  }
  function borrar() {
    const cv = lienzo.current;
    const ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, cv.width, cv.height);
    setTrazos(0);
  }

  const listo = modo === "dibujar" ? trazos > 0 : texto.trim().length >= 3;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-1 rounded-xl bg-surface-2 p-1 text-[13px] font-semibold">
        {([["dibujar", "Dibujar", PenLine], ["escribir", "Escribir mi nombre", Type]] as const).map(([v, t, Icono]) => (
          <button key={v} type="button" onClick={() => setModo(v)}
            className={cn("flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-2", modo === v ? "bg-surface shadow-sm" : "text-ink-3")}>
            <Icono className="h-4 w-4" /> {t}
          </button>
        ))}
      </div>
      {modo === "dibujar" ? (
        <div>
          <canvas ref={lienzo} aria-label="Lienzo de firma" onPointerDown={inicio} onPointerMove={mover} onPointerUp={fin} onPointerLeave={fin}
            className="h-40 w-full touch-none rounded-xl border border-border-soft bg-white" />
          <div className="mt-1 flex items-center justify-between text-[11px] text-ink-3">
            <span>Firma con tu dedo o con el mouse.</span>
            <button type="button" onClick={borrar} className="flex items-center gap-1 font-semibold text-brand hover:underline"><Eraser className="h-3.5 w-3.5" /> Borrar</button>
          </div>
        </div>
      ) : (
        <div>
          <input value={texto} onChange={(e) => setTexto(e.target.value)} placeholder="Tu nombre completo" maxLength={80}
            className="h-11 w-full rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand" />
          {texto.trim() && <p className="mt-2 rounded-xl border border-border-soft bg-white px-3 py-3 font-serif text-2xl italic text-[#141450]">{texto}</p>}
        </div>
      )}
      <p className="text-[11px] text-ink-3">Firma de demostración: el documento final lleva la marca «DEMOSTRACIÓN · SIN VALIDEZ LEGAL».</p>
      {error && <p className="text-[13px] font-semibold text-bad">{error}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancelar} className="h-10 rounded-xl px-4 text-sm font-semibold text-ink-2 hover:bg-surface-2">Cancelar</button>
        <button type="button" disabled={!listo || ocupado}
          onClick={() => onFirmar(modo === "dibujar" ? { imagen: lienzo.current?.toDataURL("image/png") } : { texto: texto.trim() })}
          className="flex h-10 items-center gap-1.5 rounded-xl bg-brand px-4 text-sm font-semibold text-white disabled:opacity-50">
          {ocupado && <Loader2 className="h-4 w-4 animate-spin" />} Firmar
        </button>
      </div>
    </div>
  );
}
