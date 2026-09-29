"use client";

/* Consentimiento EXPRESO y POR ESCRITO por medio electrónico para el estudio médico (2026-09-28, LFPDPPP: los datos
   de salud son sensibles). Sin sesión: la liga es la credencial. La persona lee el texto exacto, escribe su nombre
   completo como firma y marca «Acepto». El servidor guarda la copia del texto, la aceptación y la evidencia. */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { CheckCircle2, Loader2, ShieldCheck } from "lucide-react";
import { Button, Card, Logo } from "@/components/ui";
import { ThemeToggle } from "@/components/theme-toggle";
import { aceptarConsentimientoPublico, fetchConsentimientoPublico, rechazarConsentimientoPublico } from "@/lib/api";

type Datos = Awaited<ReturnType<typeof fetchConsentimientoPublico>>;

export default function ConsentimientoMedico() {
  const params = useParams();
  const token = String(params?.token ?? "");
  const [datos, setDatos] = useState<Datos | undefined>(undefined);
  const [nombre, setNombre] = useState("");
  const [acepto, setAcepto] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState("");
  const [listo, setListo] = useState(false);
  const [rechazado, setRechazado] = useState(false);

  useEffect(() => {
    fetchConsentimientoPublico(token).then(setDatos);
  }, [token]);

  async function aceptar() {
    setEnviando(true);
    setError("");
    const r = await aceptarConsentimientoPublico(token, nombre.trim());
    setEnviando(false);
    if (!r.ok) return setError(r.error);
    setListo(true);
  }

  /** Evaluaciones unificadas (2026-09-29): «No acepto» queda registrado; la evaluación médica no avanza sin él. */
  async function rechazar() {
    if (!window.confirm("¿Confirmas que NO otorgas tu consentimiento para la evaluación médica?")) return;
    setEnviando(true);
    setError("");
    const r = await rechazarConsentimientoPublico(token);
    setEnviando(false);
    if (!r.ok) return setError(r.error);
    setRechazado(true);
  }

  return (
    <main className="sala-publica min-h-svh bg-bg">
      <header className="border-b border-border-soft">
        <div className="mx-auto flex max-w-2xl items-center justify-between px-5 py-4">
          <Logo />
          <ThemeToggle />
        </div>
      </header>
      <div className="mx-auto max-w-2xl px-5 py-8 sm:py-10">
        {datos === undefined ? (
          <div className="grid place-items-center py-24 text-ink-3"><Loader2 className="h-6 w-6 animate-spin" /></div>
        ) : datos === null ? (
          <Card className="p-8 text-center text-sm text-ink-2">Esta liga no es válida o ya no está disponible.</Card>
        ) : datos.aceptado || listo ? (
          <Card className="flex flex-col items-center gap-3 p-8 text-center">
            <CheckCircle2 className="h-10 w-10 text-good" />
            <h1 className="font-display text-xl font-bold">Consentimiento registrado</h1>
            <p className="text-sm text-ink-2">Gracias. {datos.empresa} continuará con tu proceso. Puedes revocar tu consentimiento en cualquier momento.</p>
          </Card>
        ) : datos.rechazado || rechazado ? (
          <Card className="p-8 text-center text-sm text-ink-2">Registramos que no otorgas tu consentimiento. Recursos Humanos se pondrá en contacto contigo si hace falta.</Card>
        ) : datos.cancelada ? (
          <Card className="p-8 text-center text-sm text-ink-2">Esta solicitud fue cancelada; ya no necesitas hacer nada.</Card>
        ) : (
          <Card className="p-6">
            <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-ink-3"><ShieldCheck className="h-4 w-4" /> Consentimiento para evaluación médica</p>
            <h1 className="mt-2 font-display text-xl font-bold">{datos.puesto} · {datos.empresa}</h1>
            <p className="mt-4 whitespace-pre-line rounded-xl border border-border-soft bg-surface-2/50 p-4 text-[14px] leading-relaxed text-ink-2">{datos.texto}</p>
            <label className="mt-5 flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Escribe tu nombre completo como firma</span>
              <input
                value={nombre}
                onChange={(e) => setNombre(e.target.value)}
                autoComplete="name"
                className="h-12 rounded-xl border border-border-soft bg-surface px-3 text-base outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
              />
            </label>
            <label className="mt-4 flex items-start gap-2 text-sm text-ink-2">
              <input type="checkbox" checked={acepto} onChange={(e) => setAcepto(e.target.checked)} className="mt-0.5 h-5 w-5" />
              Acepto: leí el texto anterior y otorgo mi consentimiento expreso y por escrito para el estudio médico.
            </label>
            {error && <p className="mt-3 text-sm font-semibold text-bad">{error}</p>}
            <Button size="lg" className="mt-5 w-full totem:min-h-16 totem:text-xl" onClick={aceptar} disabled={enviando || !acepto || nombre.trim().length < 5}>
              {enviando ? <Loader2 className="h-5 w-5 animate-spin" /> : <CheckCircle2 className="h-5 w-5" />} Otorgar consentimiento
            </Button>
            <Button variant="outline" size="lg" className="mt-3 w-full totem:min-h-16 totem:text-xl" onClick={rechazar} disabled={enviando}>
              No acepto
            </Button>
            <p className="mt-3 text-[11px] text-ink-3">Si no estás de acuerdo, elige «No acepto»: la evaluación médica no se realiza sin tu consentimiento.</p>
          </Card>
        )}
      </div>
    </main>
  );
}
