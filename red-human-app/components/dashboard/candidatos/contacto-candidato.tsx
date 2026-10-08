"use client";

/* Datos de contacto de la ficha (2026-10-07, TODAS las Cuentas): correo y teléfono de la PERSONA, editables en línea
   en cualquier etapa (también con la postulación cerrada). Guarda con `PATCH /candidatos/{codigo}/contacto`
   (`candidatos.actualizar_contacto`: valida formato, que no sean de otra persona de la Cuenta, y deja bitácora). */

import { useState } from "react";
import { Loader2, Mail, Pencil, Phone } from "lucide-react";
import { Button } from "@/components/ui";
import { actualizarContactoCandidato, esCorreoValido } from "@/lib/api";
import type { Candidato } from "@/lib/data";
import { cn } from "@/lib/utils";

const soloDigitos = (v: string) => v.replace(/\D/g, "");
/** La API guarda 10 dígitos (quita lada 52/521 si viene). */
const telefonoValido = (v: string) => {
  const d = soloDigitos(v);
  return d.length === 0 || d.length >= 10;
};

const campo =
  "h-9 w-full min-w-0 rounded-lg border border-border-soft bg-surface px-2.5 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20";

export function ContactoCandidato({ c, editable, onCambio }: {
  c: Candidato;
  /** RH con permiso de decidir y API disponible. */
  editable: boolean;
  onCambio: (c: Candidato) => void;
}) {
  const [editando, setEditando] = useState(false);
  const [correo, setCorreo] = useState("");
  const [telefono, setTelefono] = useState("");
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState("");

  function abrir() {
    setCorreo(c.correo ?? "");
    setTelefono(c.telefono ?? "");
    setError("");
    setEditando(true);
  }

  const correoMal = correo.trim() !== "" && !esCorreoValido(correo);
  const telefonoMal = !telefonoValido(telefono);
  const cambio = correo.trim().toLowerCase() !== (c.correo ?? "").trim().toLowerCase()
    || soloDigitos(telefono).slice(-10) !== soloDigitos(c.telefono ?? "");

  async function guardar() {
    if (guardando || correoMal || telefonoMal || !cambio) return;
    setGuardando(true);
    setError("");
    const r = await actualizarContactoCandidato(c.id, { correo: correo.trim(), telefono: soloDigitos(telefono) });
    setGuardando(false);
    if (!r.ok) return setError(r.error);
    onCambio(r.data);
    setEditando(false);
  }

  if (!editando) {
    return (
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-2">
        <span className="inline-flex min-w-0 items-center gap-1">
          <Phone className="h-3.5 w-3.5 shrink-0 text-ink-3" />
          {c.telefono ? <span className="tabular-nums">{c.telefono}</span> : <span className="text-ink-3">Sin teléfono</span>}
        </span>
        <span className="inline-flex min-w-0 items-center gap-1">
          <Mail className="h-3.5 w-3.5 shrink-0 text-ink-3" />
          {c.correo ? <span className="truncate">{c.correo}</span> : <span className="text-ink-3">Sin correo</span>}
        </span>
        {editable && (
          <button
            type="button"
            onClick={abrir}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-semibold text-brand hover:bg-brand-soft"
            aria-label="Editar correo y teléfono"
          >
            <Pencil className="h-3 w-3" /> Editar
          </button>
        )}
      </div>
    );
  }

  return (
    <form
      className="mt-2 flex flex-col gap-2 rounded-xl border border-border-soft bg-surface-2/60 p-2.5"
      onSubmit={(e) => {
        e.preventDefault();
        guardar();
      }}
    >
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="flex min-w-0 flex-col gap-1 text-[11px] font-medium text-ink-2">
          Teléfono (10 dígitos)
          <input
            type="tel"
            inputMode="numeric"
            className={cn(campo, telefonoMal && "border-bad")}
            value={telefono}
            onChange={(e) => setTelefono(e.target.value)}
            placeholder="5512345678"
            disabled={guardando}
            aria-invalid={telefonoMal}
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-[11px] font-medium text-ink-2">
          Correo
          <input
            type="email"
            inputMode="email"
            className={cn(campo, correoMal && "border-bad")}
            value={correo}
            onChange={(e) => setCorreo(e.target.value)}
            placeholder="correo@ejemplo.com"
            disabled={guardando}
            aria-invalid={correoMal}
            autoFocus
          />
        </label>
      </div>
      {(correoMal || telefonoMal || error) && (
        <p role="alert" className="text-[11px] font-semibold text-bad">
          {error || (correoMal ? "Revisa el formato del correo." : "El teléfono debe tener 10 dígitos.")}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => setEditando(false)} disabled={guardando}>Cancelar</Button>
        <Button type="submit" size="sm" disabled={guardando || correoMal || telefonoMal || !cambio}>
          {guardando && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Guardar
        </Button>
      </div>
    </form>
  );
}
