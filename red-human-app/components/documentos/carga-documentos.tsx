"use client";

/* Cambio 42 — carga de documentos del candidato, UNA pieza para la liga pública (/expediente/[token]) y para la página
   de la solicitud (/aplicar, cuando «Documentos» sigue inmediatamente a «Solicitud web»). Cada archivo se guarda al
   subirlo; la lista solo trae los FALTANTES; los documentos de dos caras (INE, licencia) piden Frente y Reverso por
   separado; al completar los obligatorios se muestra el mensaje final y la actividad queda «Por revisar» para RH. */

import { useState } from "react";
import { Camera, CheckCircle2, Clock, FileWarning, Images, Loader2 } from "lucide-react";
import { Badge, Card } from "@/components/ui";
import { cn } from "@/lib/utils";
import { subirDocumentoPublico, type DocumentoExpedientePublico, type ExpedientePublico } from "@/lib/api";

const ESTADO_INFO: Record<DocumentoExpedientePublico["estado"], { label: string; tone: "good" | "warn" | "bad" | "neutral"; icon: typeof Clock }> = {
  recibido: { label: "Recibido", tone: "good", icon: CheckCircle2 },
  revision: { label: "En revisión", tone: "warn", icon: Clock },
  rechazado: { label: "Rechazado — vuelve a subirlo", tone: "bad", icon: FileWarning },
  pendiente: { label: "Pendiente", tone: "neutral", icon: Clock },
};

/** 2026-10-09: el candidato nunca ve un error crudo («Error 409 al llamar /…»): solo un mensaje claro. */
export function mensajeLimpio(error: string): string {
  const m = /^Error (\d{3}) al llamar/.exec(error || "");
  if (!m) return error;
  const codigo = Number(m[1]);
  if (codigo === 409) return "Esta acción ya no está disponible (es posible que ya se haya completado). Recarga la página; si el problema sigue, contacta a Recursos Humanos.";
  if (codigo === 404) return "No encontramos este documento. Pide a Recursos Humanos una liga nueva.";
  if (codigo >= 500) return "No pudimos completar la acción por un problema temporal. Intenta de nuevo en un momento.";
  return "No pudimos completar la acción. Intenta de nuevo o contacta a Recursos Humanos.";
}

export function CargaDocumentos({ token, vista, onCambio }: {
  token: string;
  vista: Pick<ExpedientePublico, "documentos" | "completo" | "mensajeFinal">;
  /** Vista nueva que regresa el servidor tras cada archivo (solo faltantes). */
  onCambio: (v: Partial<ExpedientePublico>) => void;
}) {
  const [subiendo, setSubiendo] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function subir(tipo: string, archivos: File[], cara = "") {
    if (!archivos.length) return;
    setSubiendo(`${tipo}|${cara}`);
    setError("");
    // Cambio 25: cada archivo es su propio registro (las 4 fotos del auto se suben una por una); HEIC → JPG en el servidor
    for (const archivo of archivos) {
      const r = await subirDocumentoPublico(token, tipo, archivo, cara);
      if (!r.ok) {
        setError(mensajeLimpio(r.error));
        break;
      }
      if (r.data.documentos) onCambio(r.data);
    }
    setSubiendo(null);
  }

  if (vista.completo) {
    return (
      <Card className="p-6 text-center">
        <CheckCircle2 className="mx-auto h-8 w-8 text-good" />
        <p className="mt-2 text-sm font-semibold text-ink">{vista.mensajeFinal || "Listo. Ya recibimos tus documentos."}</p>
      </Card>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {error && <div className="rounded-xl border border-bad/25 bg-bad-soft px-3.5 py-2.5 text-[13px] text-bad">{error}</div>}
      {vista.documentos.map((d) => (
        <FilaDocumento key={d.tipo} d={d} subiendo={subiendo} onArchivos={(archivos, cara) => void subir(d.tipo, archivos, cara)} />
      ))}
      <p className="text-center text-[11px] text-ink-3">* obligatorio</p>
    </div>
  );
}

/** Cambio 25: un renglón por documento con «Tomar foto» (cámara del celular) y «Galería o archivo». Si exige varias fotos
 *  (las del auto son 4) muestra cuántas lleva. Cambio 42: con caras (INE, licencia) son DOS casillas: Frente y Reverso. */
function FilaDocumento({ d, subiendo, onArchivos }: {
  d: DocumentoExpedientePublico; subiendo: string | null; onArchivos: (archivos: File[], cara?: string) => void;
}) {
  const cantidad = d.cantidad ?? 1;
  const recibidos = d.recibidos ?? 0;
  const caras = d.caras ?? [];
  const correccion = d.estado === "rechazado" && Boolean(d.correccion);
  const estado = correccion
    ? { label: "Corrección pedida", tone: "bad" as const, icon: FileWarning }
    : d.estado === "pendiente" && cantidad > 1 && recibidos > 0
      ? { label: `${recibidos} de ${cantidad}`, tone: "warn" as const, icon: Clock }
      : ESTADO_INFO[d.estado];
  const Icono = estado.icon;
  const bloqueado = subiendo !== null;
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink">
            {d.tipo}
            {d.obligatorio && <span className="ml-1 text-bad">*</span>}
          </p>
          {cantidad > 1 && !caras.length && (
            <p className="text-[12px] text-ink-3">{d.esFoto ? `${cantidad} fotos` : `${cantidad} archivos`} · llevas {Math.min(recibidos, cantidad)}</p>
          )}
        </div>
        <Badge tone={estado.tone} dot>
          <Icono className="h-3 w-3" /> {estado.label}
        </Badge>
      </div>
      {correccion && <p className="mt-2 rounded-lg bg-bad-soft px-3 py-2 text-[13px] text-bad">Vuelve a subirlo: {d.correccion}</p>}
      {caras.length ? (
        <div className="mt-3 flex flex-col gap-2">
          {caras.map((c) => (
            <div key={c.cara} className="rounded-xl border border-border-soft p-2.5">
              <div className="mb-2 flex items-center justify-between text-[13px]">
                <span className="font-semibold text-ink">{c.cara} <span className="text-bad">*</span></span>
                {c.recibida ? <Badge tone="good" dot>Recibido</Badge> : <Badge tone="neutral">Pendiente</Badge>}
              </div>
              <Botones cargando={subiendo === `${d.tipo}|${c.cara}`} bloqueado={bloqueado} varios={false}
                tenue={c.recibida} onArchivos={(a) => onArchivos(a, c.cara)} />
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-3">
          <Botones cargando={subiendo === `${d.tipo}|`} bloqueado={bloqueado} varios={cantidad > 1}
            tenue={d.estado === "recibido"} onArchivos={(a) => onArchivos(a)} />
        </div>
      )}
    </Card>
  );
}

function Botones({ cargando, bloqueado, varios, tenue, onArchivos }: {
  cargando: boolean; bloqueado: boolean; varios: boolean; tenue: boolean; onArchivos: (archivos: File[]) => void;
}) {
  const elegir = (e: React.ChangeEvent<HTMLInputElement>) => {
    const archivos = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (archivos.length) onArchivos(archivos);
  };
  const boton = "inline-flex min-h-11 flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-xl border px-3 text-sm font-semibold transition totem:min-h-16 totem:text-xl";
  return (
    <div className={cn("flex gap-2", tenue && "opacity-70", bloqueado && "pointer-events-none opacity-60")}>
      <label className={cn(boton, "border-brand/40 bg-brand-soft text-brand hover:brightness-105")}>
        {cargando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Camera className="h-4 w-4" />} Tomar foto
        <input type="file" accept="image/*" capture="environment" className="sr-only" onChange={elegir} disabled={bloqueado} />
      </label>
      <label className={cn(boton, "border-border-soft bg-surface text-ink-2 hover:text-ink")}>
        <Images className="h-4 w-4" /> Galería o archivo
        <input type="file" accept="image/*,application/pdf,.heic,.heif" multiple={varios} className="sr-only" onChange={elegir} disabled={bloqueado} />
      </label>
    </div>
  );
}
