"use client";

/* Onboarding v2 — tareas de la persona EN LA FICHA (2026-09-28; acciones directas 2026-10-08).
   Cada fila: Tarea · Responsable (se asigna aquí si está vacío; avisa por correo SOLO a esa persona) · Plazo · Estado ·
   Acción. La acción se ejecuta aquí mismo (`ModalAccionTarea`): «Confirmar ingreso», «Registrar alta IMSS / nómina»
   (registro manual, sin integración), «Adjuntar contrato firmado» (el MISMO contrato de Contratación). Si el contrato
   se firmó u omitió en Contratación, la tarea ya llega Realizada u «Omitida». Las tareas son la fuente de verdad. */

import { useCallback, useEffect, useRef, useState } from "react";
import { Ban, FileSignature, Loader2, Play, RotateCcw, UserPlus } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import { MenuAcciones } from "@/components/dashboard/menu-acciones";
import { CampoRH, ModalMarco, inputRH } from "@/components/dashboard/modulos-rh";
import { ModalAccionTarea } from "@/components/dashboard/onboarding/accion-tarea";
import {
  cambiarTareaOnboarding, fetchEntrevistadores, fetchTareasOnboarding, subirContratoFirmado, urlContratoFirmado,
  type Entrevistador, type TareaOnboarding,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { textoDia, textoFecha } from "@/lib/fechas";

const FORMATO = { day: "numeric", month: "short", year: "numeric" } as const;

function estadoTarea(t: TareaOnboarding): { texto: string; tono: "good" | "neutral" | "bad" | "warn" } {
  if (t.estado === "realizada") return { texto: "Realizada", tono: "good" };
  if (t.omitida) return { texto: "Omitida", tono: "neutral" };
  if (t.estado === "cancelada") return { texto: "Cancelada", tono: "neutral" };
  return t.atrasada ? { texto: "Atrasada", tono: "bad" } : { texto: "Pendiente", tono: "warn" };
}

export function PanelTareasOnboarding({ expedienteId, live, onCambio, recargar = 0 }: {
  expedienteId: number;
  live: boolean;
  onCambio?: () => void;
  /** Cambia el número para volver a leer las tareas (p. ej. después de la acción principal de la ruta). */
  recargar?: number;
}) {
  const [tareas, setTareas] = useState<TareaOnboarding[] | null>(null);
  const [usuarios, setUsuarios] = useState<Entrevistador[]>([]);
  const [error, setError] = useState("");
  const [aviso, setAviso] = useState("");
  const [ocupado, setOcupado] = useState<number | null>(null);
  const [cancelar, setCancelar] = useState<TareaOnboarding | null>(null);
  const [accion, setAccion] = useState<TareaOnboarding | null>(null);
  const [motivo, setMotivo] = useState("");
  const archivo = useRef<HTMLInputElement>(null);

  const cargar = useCallback(async () => setTareas((await fetchTareasOnboarding(expedienteId)) ?? []), [expedienteId]);
  useEffect(() => {
    void cargar();
  }, [cargar, recargar]);
  useEffect(() => {
    if (live) fetchEntrevistadores().then((x) => setUsuarios(x ?? []));
  }, [live]);

  function listo(mensaje = "") {
    setAviso(mensaje);
    void cargar();
    onCambio?.();
  }

  async function cambiar(t: TareaOnboarding, estado: TareaOnboarding["estado"], motivoTexto = "") {
    setOcupado(t.id);
    setError("");
    const r = await cambiarTareaOnboarding(t.id, { estado, motivo: motivoTexto });
    setOcupado(null);
    if (!r.ok) return setError(r.error);
    listo();
  }

  async function asignar(t: TareaOnboarding, responsable: string) {
    if (!responsable) return;
    setOcupado(t.id);
    setError("");
    const r = await cambiarTareaOnboarding(t.id, { responsable });
    setOcupado(null);
    if (!r.ok) return setError(r.error);
    const a = r.data.avisoResponsable;
    listo(a ? (a.enviado ? `Se le avisó por correo a ${a.destinatario}.` : `${a.destinatario}: el aviso no salió (${a.detalle}).`) : "Responsable asignado.");
  }

  async function reemplazarContrato(f: File | undefined) {
    if (archivo.current) archivo.current.value = "";
    if (!f) return;
    setError("");
    const r = await subirContratoFirmado(expedienteId, f);
    if (!r.ok) return setError(r.error);
    listo("Contrato firmado reemplazado.");
  }

  if (tareas === null) return <Loader2 className="h-5 w-5 animate-spin text-ink-3" />;
  if (tareas.length === 0) return <p className="text-sm text-ink-3">El Onboarding de esta persona aún no tiene tareas.</p>;

  return (
    <div>
      <input ref={archivo} type="file" accept="application/pdf" className="hidden" onChange={(e) => void reemplazarContrato(e.target.files?.[0])} />
      <div className="hidden grid-cols-[minmax(0,1.6fr)_minmax(0,1.2fr)_minmax(0,0.9fr)_auto_auto] gap-3 px-3.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-3 sm:grid">
        <span>Tarea</span><span>Responsable</span><span>Plazo</span><span>Estado</span><span className="text-right">Acción</span>
      </div>
      <ul className="flex flex-col gap-2">
        {tareas.map((t) => {
          const esContrato = t.clave === "contrato_firmado";
          const est = estadoTarea(t);
          return (
            <li key={t.id} className={cn("grid items-center gap-x-3 gap-y-1.5 rounded-xl border bg-surface px-3.5 py-2.5 sm:grid-cols-[minmax(0,1.6fr)_minmax(0,1.2fr)_minmax(0,0.9fr)_auto_auto]",
              t.atrasada ? "border-bad/40" : "border-border-soft")}>
              <div className="min-w-0">
                <p className={cn("truncate text-sm font-medium", t.estado === "cancelada" && "text-ink-3 line-through")}>
                  {t.nombre} {t.obligatoria && <span className="text-[10px] font-normal text-ink-3">· obligatoria</span>}
                </p>
                <p className="truncate text-[11px] text-ink-3">
                  {t.estado === "realizada" && t.realizadaPor ? `Realizada por ${t.realizadaPor} el ${textoFecha(t.realizadaEn, FORMATO)}` : ""}
                  {t.estado === "cancelada" ? `${t.omitida ? "" : "Cancelada · "}${t.motivoCancelacion}` : ""}
                  {t.estado === "pendiente" && t.clave === "alta_imss_nomina" ? "Registro manual (sin integración con IMSS ni nómina)" : ""}
                </p>
              </div>
              <div className="min-w-0 text-[12px]">
                {t.responsable ? (
                  <span className="truncate text-ink-2">{t.responsable}</span>
                ) : live && t.estado === "pendiente" ? (
                  <select aria-label={`Asignar responsable de ${t.nombre}`} defaultValue="" disabled={ocupado === t.id}
                    onChange={(e) => void asignar(t, e.target.value)}
                    className="h-8 w-full rounded-lg border border-border-soft bg-surface px-2 text-[12px] outline-none focus:border-brand">
                    <option value="" disabled>Asignar responsable…</option>
                    {usuarios.map((u) => <option key={u.id} value={u.nombre}>{u.nombre}</option>)}
                  </select>
                ) : (
                  <span className="text-ink-3">Sin responsable</span>
                )}
              </div>
              <span className={cn("text-[12px]", t.atrasada ? "font-semibold text-bad" : "text-ink-2")}>
                {t.fechaLimite ? textoDia(t.fechaLimite, FORMATO) : "Sin plazo"}
              </span>
              <Badge tone={est.tono}>{est.texto}</Badge>
              <div className="flex items-center justify-end gap-1.5">
                {live && t.accion && (
                  <Button size="sm" variant="outline" onClick={() => setAccion(t)} disabled={ocupado === t.id}>
                    {ocupado === t.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} {t.accion.texto}
                  </Button>
                )}
                {esContrato && t.estado === "realizada" && (
                  <a href={urlContratoFirmado(expedienteId)} target="_blank" rel="noreferrer" className="text-xs font-semibold text-brand hover:underline">Ver contrato</a>
                )}
                {live && (
                  <MenuAcciones
                    acciones={[
                      ...(t.responsable && t.estado === "pendiente"
                        ? usuarios.filter((u) => u.nombre !== t.responsable).slice(0, 8).map((u) => ({
                          etiqueta: `Reasignar a ${u.nombre}`, icono: <UserPlus className="h-4 w-4" />, onClick: () => void asignar(t, u.nombre),
                        }))
                        : []),
                      ...(esContrato && t.estado === "realizada"
                        ? [{ etiqueta: "Reemplazar contrato firmado", icono: <FileSignature className="h-4 w-4" />, onClick: () => archivo.current?.click() }]
                        : []),
                      ...(t.estado !== "pendiente" && !t.cierreConAccion && !t.omitida
                        ? [{ etiqueta: "Reabrir", icono: <RotateCcw className="h-4 w-4" />, onClick: () => void cambiar(t, "pendiente") }]
                        : []),
                      ...(!t.fija && t.estado === "pendiente"
                        ? [{ etiqueta: "Cancelar tarea…", icono: <Ban className="h-4 w-4" />, peligrosa: true, onClick: () => { setMotivo(""); setCancelar(t); } }]
                        : []),
                    ]}
                  />
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {aviso && <p className="mt-2 text-[12px] text-ink-2">{aviso}</p>}
      {error && <p className="mt-2 text-sm font-semibold text-bad">{error}</p>}
      {accion && (
        <ModalAccionTarea expedienteId={expedienteId} tarea={accion} onClose={() => setAccion(null)}
          onHecho={(m) => { setAccion(null); listo(m); }} />
      )}
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
