"use client";

/* Referencias laborales — fase 2 (2026-10-08): el evaluador (por su liga) o RH (desde el sistema) dictamina CADA contacto
   que capturó el candidato: si se le pudo contactar, el dictamen (Favorable / Con observaciones / Desfavorable) y un
   comentario. Cada dictamen queda en el historial de la evaluación; el resultado general sigue en el formulario único. */

import { useState } from "react";
import { CheckCircle2, Phone, Mail } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import type { Referencia, Resultado } from "@/lib/api";
import { cn } from "@/lib/utils";

const DICTAMENES = [
  { valor: "favorable", texto: "Favorable" },
  { valor: "con_observaciones", texto: "Con observaciones" },
  { valor: "desfavorable", texto: "Desfavorable" },
];
const TONO: Record<string, "good" | "warn" | "bad"> = { favorable: "good", con_observaciones: "warn", desfavorable: "bad" };

type Dictaminar = (rid: string, datos: { contactado: boolean; dictamen: string; comentario: string }) => Promise<Resultado<{ referencia: Referencia }>>;

export function ReferenciasDictamen({ referencias, onDictaminar, soloLectura = false }: {
  referencias: Referencia[];
  onDictaminar: Dictaminar;
  soloLectura?: boolean;
}) {
  const [lista, setLista] = useState(referencias);
  if (!lista.length) return <p className="text-sm text-ink-3">El candidato aún no comparte sus referencias.</p>;
  return (
    <ul className="flex flex-col gap-2.5">
      {lista.map((r) => (
        <FilaReferencia key={r.id} r={r} soloLectura={soloLectura} onDictaminar={onDictaminar}
          onGuardada={(nueva) => setLista((xs) => xs.map((x) => (x.id === nueva.id ? nueva : x)))} />
      ))}
    </ul>
  );
}

function FilaReferencia({ r, soloLectura, onDictaminar, onGuardada }: {
  r: Referencia;
  soloLectura: boolean;
  onDictaminar: Dictaminar;
  onGuardada: (r: Referencia) => void;
}) {
  const [editando, setEditando] = useState(!r.dictaminadoEn && !soloLectura);
  const [contactado, setContactado] = useState(r.contactado !== false);
  const [dictamen, setDictamen] = useState(r.dictamen ?? "");
  const [comentario, setComentario] = useState(r.comentario);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState("");

  async function guardar() {
    setGuardando(true);
    setError("");
    const res = await onDictaminar(r.id, { contactado, dictamen: contactado ? dictamen : "", comentario });
    setGuardando(false);
    if (!res.ok) return setError(res.error);
    onGuardada(res.data.referencia);
    setEditando(false);
  }

  return (
    <li className="rounded-xl border border-border-soft bg-surface p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink">{r.nombre}</p>
          <p className="text-[12px] text-ink-2">{[r.puesto, r.empresa, r.relacion].filter(Boolean).join(" · ")}</p>
          <p className="mt-0.5 flex flex-wrap gap-x-3 text-[12px] text-ink-3">
            {r.telefono && <a href={`tel:${r.telefono}`} className="inline-flex items-center gap-1 hover:text-brand"><Phone className="h-3 w-3" /> {r.telefono}</a>}
            {r.correo && <a href={`mailto:${r.correo}`} className="inline-flex items-center gap-1 hover:text-brand"><Mail className="h-3 w-3" /> {r.correo}</a>}
          </p>
        </div>
        {r.dictaminadoEn && !editando && (
          r.contactado === false
            ? <Badge tone="neutral">No contactada</Badge>
            : <Badge tone={TONO[r.dictamen ?? ""] ?? "neutral"}>{r.dictamenTexto || "Sin dictamen"}</Badge>
        )}
      </div>
      {r.dictaminadoEn && !editando && (
        <div className="mt-1.5 text-[12px] text-ink-2">
          {r.comentario && <p className="whitespace-pre-line">{r.comentario}</p>}
          <p className="text-ink-3">Dictaminó: {r.dictaminadoPor}</p>
          {!soloLectura && <button type="button" className="mt-1 text-[12px] font-semibold text-brand hover:underline" onClick={() => setEditando(true)}>Corregir</button>}
        </div>
      )}
      {editando && (
        <div className="mt-2 flex flex-col gap-2">
          <label className="flex items-center gap-2 text-[13px]">
            <input type="checkbox" className="h-4 w-4 accent-brand" checked={contactado} onChange={(e) => setContactado(e.target.checked)} />
            Se pudo contactar
          </label>
          {contactado && (
            <div className="flex flex-wrap gap-1.5">
              {DICTAMENES.map((d) => (
                <button key={d.valor} type="button" onClick={() => setDictamen(d.valor)}
                  className={cn("rounded-lg border px-2.5 py-1 text-[12px] font-semibold",
                    dictamen === d.valor ? "border-brand bg-brand-soft text-brand" : "border-border-soft text-ink-2 hover:border-brand/40")}>
                  {d.texto}
                </button>
              ))}
            </div>
          )}
          <textarea className="min-h-16 rounded-xl border border-border-soft bg-surface px-3 py-2 text-sm" value={comentario}
            onChange={(e) => setComentario(e.target.value)}
            placeholder={contactado ? "Qué comentó la referencia (opcional)" : "¿Qué pasó? (obligatorio: no contestó, número equivocado…)"} />
          {error && <p className="text-[12px] font-semibold text-bad">{error}</p>}
          <div className="flex justify-end">
            <Button size="sm" onClick={guardar} disabled={guardando || (contactado ? !dictamen : !comentario.trim())}>
              <CheckCircle2 className="h-4 w-4" /> {guardando ? "Guardando…" : "Guardar dictamen"}
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}
