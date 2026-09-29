"use client";

/* Onboarding v2 — Fase 2 (2026-09-28). Tareas de la persona en la ficha (Onboarding): Pendiente / Realizada /
   Cancelada (con motivo). Las tres fijas no se cancelan; «Contrato firmado» solo se cierra al cargar el PDF
   firmado («Cargar contrato firmado», queda quién y cuándo). Las tareas son la fuente de verdad. */

import { useCallback, useEffect, useRef, useState } from "react";
import { Ban, Check, FileSignature, Loader2, RotateCcw, Upload } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import { MenuAcciones } from "@/components/dashboard/menu-acciones";
import { CampoRH, ModalMarco, inputRH } from "@/components/dashboard/modulos-rh";
import { cambiarTareaOnboarding, fetchTareasOnboarding, subirContratoFirmado, urlContratoFirmado, type TareaOnboarding } from "@/lib/api";
import { cn } from "@/lib/utils";
import { textoDia, textoFecha } from "@/lib/fechas";

const FORMATO = { day: "numeric", month: "short", year: "numeric" } as const;

export function PanelTareasOnboarding({ expedienteId, live, onCambio }: { expedienteId: number; live: boolean; onCambio?: () => void }) {
  const [tareas, setTareas] = useState<TareaOnboarding[] | null>(null);
  const [error, setError] = useState("");
  const [ocupado, setOcupado] = useState<number | null>(null);
  const [cancelar, setCancelar] = useState<TareaOnboarding | null>(null);
  const [motivo, setMotivo] = useState("");
  const archivo = useRef<HTMLInputElement>(null);

  const cargar = useCallback(async () => setTareas((await fetchTareasOnboarding(expedienteId)) ?? []), [expedienteId]);
  useEffect(() => {
    void cargar();
  }, [cargar]);

  async function cambiar(t: TareaOnboarding, estado: TareaOnboarding["estado"], motivoTexto = "") {
    setOcupado(t.id);
    setError("");
    const r = await cambiarTareaOnboarding(t.id, { estado, motivo: motivoTexto });
    setOcupado(null);
    if (!r.ok) return setError(r.error);
    void cargar();
    onCambio?.();
  }

  async function subirContrato(f: File | undefined, t: TareaOnboarding) {
    if (archivo.current) archivo.current.value = "";
    if (!f) return;
    setOcupado(t.id);
    setError("");
    const r = await subirContratoFirmado(expedienteId, f);
    setOcupado(null);
    if (!r.ok) return setError(r.error);
    void cargar();
    onCambio?.();
  }

  if (tareas === null) return <Loader2 className="h-5 w-5 animate-spin text-ink-3" />;
  if (tareas.length === 0) return <p className="text-sm text-ink-3">El Onboarding de esta persona aún no tiene tareas.</p>;

  return (
    <div>
      <ul className="flex flex-col gap-2">
        {tareas.map((t) => {
          const esContrato = t.clave === "contrato_firmado";
          return (
            <li key={t.id} className={cn("flex flex-wrap items-center gap-3 rounded-xl border bg-surface px-3.5 py-2.5", t.atrasada ? "border-bad/40" : "border-border-soft")}>
              <div className="min-w-0 flex-1">
                <p className={cn("truncate text-sm font-medium", t.estado === "cancelada" && "text-ink-3 line-through")}>
                  {t.nombre} {t.fija && <span className="text-[10px] font-normal text-ink-3">· obligatoria</span>}
                </p>
                <p className="truncate text-[11px] text-ink-3">
                  {t.responsable || "Sin responsable"}
                  {t.fechaLimite ? ` · vence ${textoDia(t.fechaLimite, FORMATO)}` : ""}
                  {t.estado === "realizada" && t.realizadaPor ? ` · realizada por ${t.realizadaPor} el ${textoFecha(t.realizadaEn, FORMATO)}` : ""}
                  {t.estado === "cancelada" ? ` · cancelada por ${t.canceladaPor}: ${t.motivoCancelacion}` : ""}
                </p>
              </div>
              <Badge tone={t.estado === "realizada" ? "good" : t.estado === "cancelada" ? "neutral" : t.atrasada ? "bad" : "warn"}>
                {t.estado === "realizada" ? "Realizada" : t.estado === "cancelada" ? "Cancelada" : t.atrasada ? "Atrasada" : "Pendiente"}
              </Badge>
              {live && esContrato && (
                <>
                  <input ref={archivo} type="file" accept="application/pdf" className="hidden" onChange={(e) => void subirContrato(e.target.files?.[0], t)} />
                  {t.estado === "realizada" ? (
                    <a href={urlContratoFirmado(expedienteId)} target="_blank" rel="noreferrer" className="text-xs font-semibold text-brand hover:underline">Ver contrato</a>
                  ) : (
                    <Button size="sm" variant="outline" onClick={() => archivo.current?.click()} disabled={ocupado === t.id} title="Sube el PDF final firmado">
                      {ocupado === t.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />} Cargar contrato firmado
                    </Button>
                  )}
                </>
              )}
              {live && !esContrato && t.estado === "pendiente" && t.cierreConAccion && (
                <span className="text-[11px] text-ink-3" title={t.cierreConAccion}>Se confirma en el tablero de Onboarding</span>
              )}
              {live && !esContrato && t.estado === "pendiente" && !t.cierreConAccion && (
                <Button size="sm" variant="outline" onClick={() => void cambiar(t, "realizada")} disabled={ocupado === t.id}>
                  {ocupado === t.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Realizada
                </Button>
              )}
              {live && (
                <MenuAcciones
                  acciones={[
                    ...(esContrato && t.estado === "realizada"
                      ? [{ etiqueta: "Reemplazar contrato firmado", icono: <FileSignature className="h-4 w-4" />, onClick: () => archivo.current?.click() }]
                      : []),
                    ...(t.estado !== "pendiente" && !t.cierreConAccion
                      ? [{ etiqueta: "Reabrir", icono: <RotateCcw className="h-4 w-4" />, onClick: () => void cambiar(t, "pendiente") }]
                      : []),
                    ...(!t.fija && t.estado === "pendiente"
                      ? [{ etiqueta: "Cancelar tarea…", icono: <Ban className="h-4 w-4" />, peligrosa: true, onClick: () => { setMotivo(""); setCancelar(t); } }]
                      : []),
                  ]}
                />
              )}
            </li>
          );
        })}
      </ul>
      {error && <p className="mt-2 text-sm font-semibold text-bad">{error}</p>}
      {cancelar && (
        <ModalMarco titulo="Cancelar tarea" subtitulo={`«${cancelar.nombre}» deja de contar como pendiente. El motivo queda registrado.`} onClose={() => setCancelar(null)}>
          <CampoRH label="Motivo">
            <input value={motivo} onChange={(e) => setMotivo(e.target.value)} className={inputRH} placeholder="p. ej. Usará su propio equipo" autoFocus />
          </CampoRH>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setCancelar(null)}>Volver</Button>
            <Button
              size="sm"
              disabled={!motivo.trim()}
              onClick={async () => {
                const t = cancelar;
                setCancelar(null);
                await cambiar(t, "cancelada", motivo.trim());
              }}
            >
              <Ban className="h-4 w-4" /> Cancelar tarea
            </Button>
          </div>
        </ModalMarco>
      )}
    </div>
  );
}
