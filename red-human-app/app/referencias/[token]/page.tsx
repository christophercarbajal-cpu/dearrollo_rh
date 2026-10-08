"use client";

/* Referencias laborales — fase 1 (2026-10-08). Liga EXCLUSIVA del candidato, sin sesión (el token es la credencial): captura
   los datos de contacto de sus referencias (jefes o compañeros anteriores). Al guardarlas, el evaluador asignado recibe su
   liga para dictaminar cada contacto (fase 2). El candidato nunca ve los dictámenes. */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { CheckCircle2, Loader2, Plus, Trash2, Users } from "lucide-react";
import { Button, Card, Logo } from "@/components/ui";
import { ThemeToggle } from "@/components/theme-toggle";
import { capturarReferencias, esFallaDeRed, fetchReferenciasPublicas, type ReferenciaCaptura, type VistaReferencias } from "@/lib/api";

const VACIA: ReferenciaCaptura = { nombre: "", empresa: "", puesto: "", relacion: "", telefono: "", correo: "" };
const campo = "h-11 w-full rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand";

export default function CapturaReferencias() {
  const params = useParams();
  const token = String(params?.token ?? "");
  const [vista, setVista] = useState<VistaReferencias | null>(null);
  const [errorCarga, setErrorCarga] = useState("");
  const [refs, setRefs] = useState<ReferenciaCaptura[]>([{ ...VACIA }, { ...VACIA }]);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState("");
  const [listo, setListo] = useState(false);

  useEffect(() => {
    fetchReferenciasPublicas(token).then((r) => {
      if (!r.ok) return setErrorCarga(esFallaDeRed(r.error) ? "Sin conexión. Revisa tu internet y vuelve a abrir la liga." : "Esta liga no es válida o ya no está disponible.");
      setVista(r.data);
      setRefs(Array.from({ length: Math.max(r.data.minimo, 2) }, () => ({ ...VACIA })));
    });
  }, [token]);

  function cambiar(i: number, k: keyof ReferenciaCaptura, v: string) {
    setRefs((xs) => xs.map((x, j) => (j === i ? { ...x, [k]: v } : x)));
  }

  async function enviar() {
    const llenas = refs.filter((r) => r.nombre.trim() || r.empresa.trim() || r.telefono.trim() || r.correo.trim());
    if (vista && llenas.length < vista.minimo) return setError(`Comparte al menos ${vista.minimo} referencia(s).`);
    setEnviando(true);
    setError("");
    const r = await capturarReferencias(token, llenas);
    setEnviando(false);
    if (!r.ok) return setError(r.error);
    setListo(true);
  }

  return (
    <main className="sala-publica min-h-svh bg-bg">
      <header className="border-b border-border-soft">
        <div className="mx-auto flex max-w-2xl items-center justify-between px-5 py-4">
          <Logo />
          <ThemeToggle />
        </div>
      </header>
      <div className="mx-auto max-w-2xl px-4 py-8 sm:px-5 sm:py-10">
        {!vista && !errorCarga ? (
          <div className="grid place-items-center py-24 text-ink-3"><Loader2 className="h-6 w-6 animate-spin" /></div>
        ) : errorCarga ? (
          <Card className="p-8 text-center text-sm text-ink-2">{errorCarga}</Card>
        ) : vista && (vista.capturadas || listo) ? (
          <Card className="flex flex-col items-center gap-3 p-8 text-center">
            <CheckCircle2 className="h-10 w-10 text-good" />
            <h1 className="font-display text-xl font-bold">¡Gracias! Recibimos tus referencias</h1>
            <p className="text-sm text-ink-2">{vista.empresa} se pondrá en contacto con ellas. No necesitas hacer nada más.</p>
          </Card>
        ) : vista?.cancelada ? (
          <Card className="p-8 text-center text-sm text-ink-2">Esta solicitud fue cancelada; ya no necesitas hacer nada.</Card>
        ) : vista && (
          <Card className="p-5 sm:p-6">
            <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-ink-3"><Users className="h-4 w-4" /> Referencias laborales</p>
            <h1 className="mt-2 font-display text-xl font-bold">{vista.puesto} · {vista.empresa}</h1>
            <p className="mt-2 text-sm leading-relaxed text-ink-2">
              Hola {vista.candidato.split(" ")[0]}. Comparte los datos de personas que puedan dar referencia de tu trabajo (jefes o
              compañeros anteriores). Avísales que podríamos contactarles.
            </p>
            <div className="mt-5 flex flex-col gap-4">
              {refs.map((r, i) => (
                <fieldset key={i} className="rounded-2xl border border-border-soft p-4">
                  <legend className="px-1 text-xs font-semibold text-ink-3">Referencia {i + 1}</legend>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <input className={campo} placeholder="Nombre completo *" value={r.nombre} onChange={(e) => cambiar(i, "nombre", e.target.value)} />
                    <input className={campo} placeholder="Empresa donde trabajaron juntos *" value={r.empresa} onChange={(e) => cambiar(i, "empresa", e.target.value)} />
                    <input className={campo} placeholder="Su puesto" value={r.puesto} onChange={(e) => cambiar(i, "puesto", e.target.value)} />
                    <input className={campo} placeholder="Relación (jefe directo, compañero…)" value={r.relacion} onChange={(e) => cambiar(i, "relacion", e.target.value)} />
                    <input className={campo} inputMode="tel" placeholder="Teléfono (10 dígitos)" value={r.telefono} onChange={(e) => cambiar(i, "telefono", e.target.value)} />
                    <input className={campo} type="email" placeholder="Correo" value={r.correo} onChange={(e) => cambiar(i, "correo", e.target.value)} />
                  </div>
                  {refs.length > vista.minimo && (
                    <button type="button" className="mt-2 inline-flex items-center gap-1 text-[12px] font-semibold text-ink-3 hover:text-bad"
                      onClick={() => setRefs((xs) => xs.filter((_, j) => j !== i))}>
                      <Trash2 className="h-3.5 w-3.5" /> Quitar
                    </button>
                  )}
                </fieldset>
              ))}
              {refs.length < vista.maximo && (
                <button type="button" className="inline-flex items-center gap-1.5 self-start text-sm font-semibold text-brand hover:underline"
                  onClick={() => setRefs((xs) => [...xs, { ...VACIA }])}>
                  <Plus className="h-4 w-4" /> Agregar otra referencia
                </button>
              )}
            </div>
            <p className="mt-3 text-[12px] text-ink-3">* Obligatorio. Cada referencia necesita un teléfono o un correo.</p>
            {error && <p className="mt-3 rounded-xl border border-bad/40 bg-bad-soft px-3 py-2 text-sm font-semibold text-bad">{error}</p>}
            <Button className="mt-5 w-full totem:min-h-16 totem:text-xl" onClick={enviar} disabled={enviando}>
              {enviando ? "Enviando…" : "Enviar referencias"}
            </Button>
          </Card>
        )}
      </div>
    </main>
  );
}
