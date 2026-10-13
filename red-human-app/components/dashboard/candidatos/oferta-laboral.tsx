"use client";

/* Cambio 30 (2026-10-13): «Oferta laboral». La carta se genera con los datos de la vacante (lo del expediente manda);
   RH la revisa y presiona «Enviar oferta» → WhatsApp «[nombre], tenemos una oferta para ti. Revísala aquí: [liga]». El
   candidato la abre en /oferta/[token] y responde «Acepto» (la ruta sigue) o «No acepto» («Oferta no aceptada»: RH
   decide, nunca descarta). Si la ruta NO trae la actividad, en Contratación aparece el botón «Generar carta oferta»: no
   bloquea nada, solo registra «Oferta aceptada / no aceptada» con su fecha. */

import { useEffect, useState } from "react";
import { FileSignature, Loader2, Send } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import { CampoRH, ModalMarco, inputRH } from "@/components/dashboard/modulos-rh";
import { enviarOferta, fetchOferta } from "@/lib/api";
import type { CartaOferta, OfertaResumen } from "@/lib/data";
import { textoFechaHora } from "@/lib/fechas";

const CAMPOS: { k: keyof CartaOferta; label: string; largo?: boolean; tipo?: string }[] = [
  { k: "puesto", label: "Puesto *" }, { k: "empresa", label: "Empresa" }, { k: "sueldo", label: "Sueldo *" },
  { k: "ubicacion", label: "Ubicación" }, { k: "modalidad", label: "Modalidad" }, { k: "horario", label: "Horario" },
  { k: "tipo_contratacion", label: "Tipo de contratación" }, { k: "fecha_ingreso", label: "Fecha de ingreso", tipo: "date" },
  { k: "jefe_directo", label: "Jefe directo" }, { k: "prestaciones", label: "Prestaciones", largo: true },
  { k: "mensaje", label: "Mensaje para el candidato (opcional)", largo: true },
];

export function ResumenOferta({ oferta }: { oferta?: OfertaResumen | null }) {
  if (!oferta) return null;
  const tono = oferta.estado === "aceptada" ? "good" : oferta.estado === "no_aceptada" ? "bad" : "neutral";
  const texto = { generada: "Oferta generada", enviada: "Oferta enviada", aceptada: "Oferta aceptada", no_aceptada: "Oferta no aceptada" }[oferta.estado];
  const fecha = oferta.respondidaEn ?? oferta.enviadaEn;
  return (
    <span className="inline-flex items-center gap-2 text-[12px] text-ink-3">
      <Badge tone={tono}>{texto}</Badge>
      {fecha && textoFechaHora(fecha)}
    </span>
  );
}

export function ModalOferta({ codigo, onClose, onEnviada }: {
  codigo: string;
  onClose: () => void;
  onEnviada: (o: OfertaResumen, aviso: string) => void;
}) {
  const [datos, setDatos] = useState<Partial<CartaOferta> | null>(null);
  const [enRuta, setEnRuta] = useState(true);
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);
  useEffect(() => {
    fetchOferta(codigo).then((r) => {
      if (!r) return setError("No se pudo cargar la oferta.");
      setDatos(r.datos);
      setEnRuta(r.enRuta);
    });
  }, [codigo]);

  async function enviar() {
    if (!datos?.puesto?.trim() || !datos?.sueldo?.trim()) return setError("La carta necesita al menos el puesto y el sueldo.");
    setEnviando(true);
    setError("");
    const r = await enviarOferta(codigo, datos);
    setEnviando(false);
    if (!r.ok) return setError(r.error);
    onEnviada(r.data.oferta, r.data.enviada ? "Oferta enviada al candidato." : `La oferta quedó lista pero no se entregó (${r.data.detalle}). Copia la liga: ${r.data.liga}`);
  }

  return (
    <ModalMarco titulo={enRuta ? "Oferta laboral" : "Generar carta oferta"}
      subtitulo="Se genera con los datos de la vacante. Revísala y envíala: el candidato la abre desde WhatsApp y responde «Acepto» o «No acepto»."
      onClose={onClose} ancho="sm:max-w-2xl">
      {!datos ? <Loader2 className="h-5 w-5 animate-spin text-ink-3" /> : (
        <div className="flex flex-col gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            {CAMPOS.map((c) => (
              <div key={c.k} className={c.largo ? "sm:col-span-2" : undefined}>
                <CampoRH label={c.label}>
                  {c.largo ? (
                    <textarea className={`${inputRH} h-auto min-h-[64px] py-2`} value={datos[c.k] ?? ""} onChange={(e) => setDatos({ ...datos, [c.k]: e.target.value })} />
                  ) : (
                    <input type={c.tipo ?? "text"} className={inputRH} value={datos[c.k] ?? ""} onChange={(e) => setDatos({ ...datos, [c.k]: e.target.value })} />
                  )}
                </CampoRH>
              </div>
            ))}
          </div>
          {!enRuta && (
            <p className="rounded-xl bg-surface-2/60 px-3 py-2 text-[12px] text-ink-2">
              La ruta de este candidato no tiene «Oferta laboral»: la carta no detiene nada, solo registra si la acepta y cuándo.
            </p>
          )}
          {error && <p className="rounded-xl border border-bad/40 bg-bad-soft px-3 py-2 text-sm font-semibold text-bad">{error}</p>}
          <div className="flex justify-end gap-2 border-t border-border-faint pt-3">
            <Button variant="outline" size="sm" onClick={onClose} disabled={enviando}>Cancelar</Button>
            <Button size="sm" onClick={enviar} disabled={enviando}><Send className="h-4 w-4" /> {enviando ? "Enviando…" : "Enviar oferta"}</Button>
          </div>
        </div>
      )}
    </ModalMarco>
  );
}

/** Botón «Generar carta oferta» (rutas SIN la actividad «Oferta laboral», en Contratación). */
export function BotonCartaOferta({ codigo, oferta, onCambio }: { codigo: string; oferta?: OfertaResumen | null; onCambio: (aviso: string) => void }) {
  const [abierto, setAbierto] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-dashed border-brand/40 bg-brand-soft/20 px-4 py-2.5">
      <Button size="sm" variant="outline" onClick={() => setAbierto(true)}>
        <FileSignature className="h-4 w-4" /> {oferta ? "Reenviar carta oferta" : "Generar carta oferta"}
      </Button>
      <ResumenOferta oferta={oferta} />
      {abierto && <ModalOferta codigo={codigo} onClose={() => setAbierto(false)} onEnviada={(_o, aviso) => { setAbierto(false); onCambio(aviso); }} />}
    </div>
  );
}
