"use client";

/* Liga pública para que el candidato suba sus documentos de contratación (Lote 4). Contraparte
   liviana de app/entrevista/[token] y app/entrevista-humana/[token]: sin sesión, sin avatar —
   aquí es una lista de documentos con un Dropzone por cada uno. A diferencia de la liga de
   Entrevista Humana, esta NO es de un solo uso: el candidato puede volver varias veces hasta
   completar todo. */

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { CheckCircle2, Clock, FileWarning, Loader2 } from "lucide-react";
import { Logo, Card, Badge } from "@/components/ui";
import { ThemeToggle } from "@/components/theme-toggle";
import { Dropzone } from "@/components/dashboard/subida";
import { cn } from "@/lib/utils";
import {
  fetchExpedientePublico,
  subirDocumentoPublico,
  type DocumentoExpedientePublico,
  type ExpedientePublico,
  urlCartaIntencionPublica,
  fetchFirmasPublicas,
  signUrlFirmaCandidato,
  firmarDemoCandidato,
} from "@/lib/api";
import { abrirFirmaEmbebida } from "@/lib/firma-embebida";
import { PadFirma } from "@/components/firmas/pad-firma";

type Fase = "cargando" | "no_disponible" | "lista";

const ESTADO_INFO: Record<DocumentoExpedientePublico["estado"], { label: string; tone: "good" | "warn" | "bad" | "neutral"; icon: typeof Clock }> = {
  recibido: { label: "Recibido", tone: "good", icon: CheckCircle2 },
  revision: { label: "En revisión", tone: "warn", icon: Clock },
  rechazado: { label: "Rechazado — vuelve a subirlo", tone: "bad", icon: FileWarning },
  pendiente: { label: "Pendiente", tone: "neutral", icon: Clock },
};

/** 2026-10-09: el candidato nunca ve un error crudo («Error 409 al llamar /…»): solo un mensaje claro. */
function mensajeLimpio(error: string): string {
  const m = /^Error (\d{3}) al llamar/.exec(error || "");
  if (!m) return error;
  const codigo = Number(m[1]);
  if (codigo === 409) return "Esta acción ya no está disponible (es posible que ya se haya completado). Recarga la página; si el problema sigue, contacta a Recursos Humanos.";
  if (codigo === 404) return "No encontramos este documento. Pide a Recursos Humanos una liga nueva.";
  if (codigo >= 500) return "No pudimos completar la acción por un problema temporal. Intenta de nuevo en un momento.";
  return "No pudimos completar la acción. Intenta de nuevo o contacta a Recursos Humanos.";
}

export default function ExpedientePublico() {
  const params = useParams();
  const token = String(params?.token ?? "");

  const [fase, setFase] = useState<Fase>("cargando");
  const [info, setInfo] = useState<ExpedientePublico | null>(null);
  const [subiendo, setSubiendo] = useState<string | null>(null);
  const [error, setError] = useState("");
  // 2026-09-29: documentos por firmar (Dropbox Sign) — se firman AQUÍ, en un modal incrustado, sin salir de la página
  const [firmas, setFirmas] = useState<Awaited<ReturnType<typeof fetchFirmasPublicas>>>(null);
  const [firmando, setFirmando] = useState<number | null>(null);
  const [avisoFirma, setAvisoFirma] = useState("");
  const [reintentar, setReintentar] = useState<number | null>(null);
  // 2026-10-09: firma de DEMOSTRACIÓN en la plataforma (sin Dropbox Sign)
  const [padDemo, setPadDemo] = useState<{ id: number; error: string; ocupado: boolean } | null>(null);
  const cargarFirmas = useCallback(() => {
    fetchFirmasPublicas(token).then(setFirmas);
  }, [token]);
  useEffect(() => {
    cargarFirmas();
  }, [cargarFirmas]);

  async function firmar(id: number) {
    if (firmas?.firmas.find((x) => x.id === id)?.modo === "demo") {
      setAvisoFirma("");
      return setPadDemo({ id, error: "", ocupado: false });
    }
    setFirmando(id);
    setAvisoFirma("");
    setReintentar(null);
    const r = await signUrlFirmaCandidato(token, id);
    setFirmando(null);
    if (!r.ok) {
      // 2026-10-08: mensaje limpio (el detalle técnico vive solo en los logs del servidor) + reintento lógico
      setReintentar(id);
      return setAvisoFirma(mensajeLimpio(r.error));
    }
    if (!r.data.signUrl) {
      setAvisoFirma(r.data.mensaje || "Ya firmaste este documento. ¡Gracias!");
      return cargarFirmas();
    }
    await abrirFirmaEmbebida({
      clientId: r.data.clientId,
      signUrl: r.data.signUrl,
      testMode: r.data.testMode,
      onFirmado: () => {
        setAvisoFirma("¡Listo! Tu firma quedó registrada. Recursos Humanos recibirá el documento firmado.");
        setTimeout(cargarFirmas, 1500);
      },
      onError: (m) => setAvisoFirma(m),
    });
  }

  const cargar = useCallback(() => {
    fetchExpedientePublico(token).then((i) => {
      if (!i) return setFase("no_disponible");
      setInfo(i);
      setFase("lista");
    });
  }, [token]);

  useEffect(() => {
    cargar();
  }, [cargar]);

  async function subir(tipo: string, archivos: File[]) {
    if (!archivos[0]) return;
    setSubiendo(tipo);
    setError("");
    const r = await subirDocumentoPublico(token, tipo, archivos[0]);
    setSubiendo(null);
    if (!r.ok) {
      setError(mensajeLimpio(r.error));
      return;
    }
    cargar(); // re-sincroniza la lista completa desde el servidor en vez de mezclar formas de respuesta distintas
  }

  return (
    <main className="min-h-svh bg-bg">
      <header className="border-b border-border-soft">
        <div className="mx-auto flex max-w-2xl items-center justify-between px-5 py-4">
          <Logo />
          <ThemeToggle />
        </div>
      </header>

      <div className="mx-auto max-w-2xl px-5 py-8 sm:py-10">
        {fase === "cargando" && (
          <div className="grid place-items-center py-24 text-ink-3">
            <Loader2 className="h-6 w-6 animate-spin" />
          </div>
        )}

        {fase === "no_disponible" && (
          <Card className="p-8 text-center">
            <h1 className="font-display text-xl font-bold">Liga no disponible</h1>
            <p className="mt-2 text-sm text-ink-2">
              Esta liga no es válida o tu expediente ya se cerró. Si crees que es un error, contacta al equipo de RH.
            </p>
          </Card>
        )}

        {fase === "lista" && info && (
          <>
            <div className="text-center">
              <Badge tone="brand" dot>
                Documentos de contratación
              </Badge>
              <h1 className="font-display mt-3 text-2xl font-bold sm:text-3xl">
                {info.candidato ? `Hola, ${info.candidato.split(" ")[0]}` : "Sube tus documentos"}
              </h1>
              <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-ink-2">
                {info.puesto && `Para tu contratación como ${info.puesto}. `}Sube foto o PDF de cada documento —
                puedes volver a esta liga cuantas veces necesites.
              </p>
              {info.cartaDisponible && (
                <a href={urlCartaIntencionPublica(token)} target="_blank" rel="noreferrer" className="mt-4 inline-flex items-center gap-1.5 rounded-xl border border-brand/40 bg-brand-soft px-4 py-2 text-sm font-semibold text-brand transition hover:brightness-105">
                  Descargar mi carta de intención (PDF)
                </a>
              )}
            </div>

            {firmas && firmas.firmas.length > 0 && (
              <Card className="mt-6 p-5">
                <p className="text-sm font-semibold text-ink">Documentos para firmar</p>
                <p className="mt-0.5 text-[12px] text-ink-3">
                  {firmas.firmas.some((f) => f.modo === "demo") ? "Se firman aquí mismo: dibuja tu firma o escribe tu nombre." : "Se firman aquí mismo con firma electrónica."}
                </p>
                <ul className="mt-3 flex flex-col gap-2">
                  {firmas.firmas.map((f) => (
                    <li key={f.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border-soft px-3.5 py-2.5">
                      <span className="text-sm">{f.documento}</span>
                      {f.yoFirme || f.estado === "firmada" || f.estado === "descargada" ? (
                        <Badge tone="good" dot>Firmado</Badge>
                      ) : f.puedoFirmar ?? f.estado === "enviada" ? (
                        <button
                          onClick={() => void firmar(f.id)}
                          disabled={firmando !== null}
                          className="inline-flex min-h-10 items-center rounded-xl bg-brand px-4 text-sm font-semibold text-brand-ink transition hover:brightness-110 disabled:opacity-60 totem:min-h-16 totem:text-xl"
                        >
                          {firmando === f.id ? "Abriendo…" : "Firmar"}
                        </button>
                      ) : (
                        <Badge tone="neutral">No disponible</Badge>
                      )}
                    </li>
                  ))}
                </ul>
                {padDemo && (
                  <div className="mt-4 rounded-2xl border border-border-soft p-4">
                    <PadFirma ocupado={padDemo.ocupado} error={padDemo.error} onCancelar={() => setPadDemo(null)}
                      onFirmar={async (d) => {
                        setPadDemo({ ...padDemo, ocupado: true, error: "" });
                        const r = await firmarDemoCandidato(token, padDemo.id, d);
                        if (!r.ok) return setPadDemo({ ...padDemo, ocupado: false, error: mensajeLimpio(r.error) });
                        setPadDemo(null);
                        setAvisoFirma(r.data.mensaje);
                        cargarFirmas();
                      }} />
                  </div>
                )}
                {avisoFirma && (
                  <p className="mt-3 flex flex-wrap items-center gap-2 text-[13px] text-ink-2">
                    {avisoFirma}
                    {reintentar !== null && (
                      <button onClick={() => void firmar(reintentar)} disabled={firmando !== null}
                        className="text-[13px] font-semibold text-brand hover:underline disabled:opacity-60">Reintentar</button>
                    )}
                  </p>
                )}
              </Card>
            )}

            {info.estado === "alta" ? (
              <Card className="mt-6 p-6 text-center">
                <CheckCircle2 className="mx-auto h-8 w-8 text-good" />
                <p className="mt-2 text-sm text-ink-2">
                  Tu expediente ya quedó completo y tu alta fue autorizada — ya no se pueden subir más documentos
                  desde aquí.
                </p>
              </Card>
            ) : (
              <div className="mt-6 flex flex-col gap-3">
                {error && (
                  <div className="rounded-xl border border-bad/25 bg-bad-soft px-3.5 py-2.5 text-[13px] text-bad">
                    {error}
                  </div>
                )}
                {info.documentos.map((d) => {
                  const estado = ESTADO_INFO[d.estado];
                  const Icono = estado.icon;
                  return (
                    <Card key={d.tipo} className="p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-ink">
                            {d.tipo}
                            {d.obligatorio && <span className="ml-1 text-bad">*</span>}
                          </span>
                        </div>
                        <Badge tone={estado.tone} dot>
                          <Icono className="h-3 w-3" /> {estado.label}
                        </Badge>
                      </div>
                      <div className={cn("mt-3", d.estado === "recibido" && "opacity-70")}>
                        <Dropzone
                          compacto
                          cargando={subiendo === d.tipo}
                          onArchivos={(archivos) => subir(d.tipo, archivos)}
                          titulo={d.estado === "recibido" ? "Subir otra vez / reemplazar" : "Subir foto o PDF"}
                        />
                      </div>
                    </Card>
                  );
                })}
                <p className="text-center text-[11px] text-ink-3">* obligatorio</p>
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}
