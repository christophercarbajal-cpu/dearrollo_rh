"use client";

/* Cambio 30 (2026-10-13): vista del candidato de su OFERTA LABORAL (liga que llega por WhatsApp: «[nombre], tenemos una
   oferta para ti. Revísala aquí: [liga]»). Sin sesión. Muestra la carta y dos botones: «Acepto» (la ruta sigue) y
   «No acepto» (queda «Oferta no aceptada» y RH decide). Una respuesta por oferta. */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { Button, Card, Logo } from "@/components/ui";
import { ThemeToggle } from "@/components/theme-toggle";
import { fetchOfertaPublica, responderOfertaPublica, type OfertaPublica } from "@/lib/api";
import { textoDia } from "@/lib/fechas";

const FILAS: { k: keyof OfertaPublica["datos"]; label: string }[] = [
  { k: "puesto", label: "Puesto" }, { k: "empresa", label: "Empresa" }, { k: "sueldo", label: "Sueldo" },
  { k: "ubicacion", label: "Ubicación" }, { k: "modalidad", label: "Modalidad" }, { k: "horario", label: "Horario" },
  { k: "tipo_contratacion", label: "Tipo de contratación" }, { k: "fecha_ingreso", label: "Fecha de ingreso" },
  { k: "jefe_directo", label: "Jefe directo" }, { k: "prestaciones", label: "Prestaciones" },
];

export default function OfertaPage() {
  const { token } = useParams<{ token: string }>();
  const [oferta, setOferta] = useState<OfertaPublica | null | "error">(null);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchOfertaPublica(token).then((r) => setOferta(r ?? "error"));
  }, [token]);

  async function responder(acepta: boolean) {
    setEnviando(true);
    setError("");
    const r = await responderOfertaPublica(token, acepta);
    setEnviando(false);
    if (!r.ok) return setError(/^Error \d{3}/.test(r.error) ? "No pudimos registrar tu respuesta. Intenta de nuevo." : r.error);
    setOferta(r.data);
  }

  return (
    <main className="sala-publica min-h-svh bg-bg">
      <header className="border-b border-border-soft">
        <div className="mx-auto flex max-w-2xl items-center justify-between px-5 py-4">
          <Logo />
          <ThemeToggle />
        </div>
      </header>
      <div className="mx-auto max-w-2xl px-4 py-8">
        {oferta === null ? (
          <Loader2 className="mx-auto h-6 w-6 animate-spin text-ink-3" />
        ) : oferta === "error" ? (
          <Card className="p-6 text-center text-sm text-ink-2">Esta liga de oferta no existe o ya no está disponible.</Card>
        ) : (
          <Card className="p-6">
            <h1 className="font-display text-xl font-bold">Oferta laboral</h1>
            <p className="mt-1 text-sm text-ink-2">
              {oferta.candidato ? `${oferta.candidato.split(" ")[0]}, ` : ""}estas son las condiciones de la oferta que tenemos para ti.
            </p>
            <dl className="mt-5 divide-y divide-border-faint text-sm">
              {FILAS.filter((f) => (oferta.datos[f.k] ?? "").trim()).map((f) => (
                <div key={f.k} className="flex flex-col gap-0.5 py-2 sm:flex-row sm:gap-4">
                  <dt className="w-44 shrink-0 text-ink-3">{f.label}</dt>
                  <dd className="font-medium text-ink">{f.k === "fecha_ingreso" ? textoDia(oferta.datos[f.k] as string) : oferta.datos[f.k]}</dd>
                </div>
              ))}
            </dl>
            {oferta.datos.mensaje && <p className="mt-4 whitespace-pre-line rounded-xl bg-surface-2/60 p-3 text-sm text-ink-2">{oferta.datos.mensaje}</p>}
            {oferta.respuesta ? (
              <p className={`mt-6 flex items-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold ${oferta.respuesta === "acepta" ? "bg-good-soft text-good" : "bg-surface-2 text-ink-2"}`}>
                {oferta.respuesta === "acepta" ? <CheckCircle2 className="h-5 w-5" /> : <XCircle className="h-5 w-5" />}
                {oferta.respuesta === "acepta"
                  ? "Registramos que aceptas la oferta. El equipo de RH te escribirá con el siguiente paso."
                  : "Registramos tu respuesta. El equipo de RH se pondrá en contacto contigo."}
              </p>
            ) : oferta.activa ? (
              <div className="mt-6 flex flex-col gap-3 sm:flex-row">
                <Button className="h-12 flex-1 totem:min-h-16 totem:text-xl" onClick={() => responder(true)} disabled={enviando}>
                  <CheckCircle2 className="h-5 w-5" /> Acepto
                </Button>
                <Button variant="outline" className="h-12 flex-1 totem:min-h-16 totem:text-xl" onClick={() => responder(false)} disabled={enviando}>
                  <XCircle className="h-5 w-5" /> No acepto
                </Button>
              </div>
            ) : (
              <p className="mt-6 text-sm text-ink-3">Este proceso ya no está activo.</p>
            )}
            {error && <p className="mt-3 text-sm font-semibold text-bad">{error}</p>}
          </Card>
        )}
      </div>
    </main>
  );
}
