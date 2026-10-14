"use client";

/* Cambio 33 (2026-10-13): acciones de la ficha en Onboarding que NO alteran la ruta (actividades ad hoc solo de esta
 * persona): «Pasar a colaborador» (aunque falte algo; lo pendiente se queda «Pendiente» en su expediente), «Solicitar
 * documentos pendientes» (pide SOLO los requeridos que aún no están aprobados) y «Asignar tareas». Arriba de los
 * botones: «Documentos pendientes: N» (se oculta en 0). */

import { useState } from "react";
import { FileText, ListChecks, UserCheck } from "lucide-react";
import { Button, Card } from "@/components/ui";
import { ModalMarco, inputRH } from "@/components/dashboard/modulos-rh";
import { asignarTareasOnboarding, pasarAColaborador, solicitarDocumentosPendientes } from "@/lib/api";
import type { Candidato } from "@/lib/data";

export function AccionesOnboarding({ c, live, onListo }: {
  c: Candidato;
  live: boolean;
  /** Refresca la ficha; `mensaje` = aviso para RH. */
  onListo: (mensaje: string, tono?: "ok" | "error") => void;
}) {
  const [ocupado, setOcupado] = useState("");
  const [confirmar, setConfirmar] = useState(false);
  const [comentario, setComentario] = useState("");
  const onb = c.onboarding;
  if (!onb || c.etapa !== "Onboarding" || c.expedienteId == null || c.activa === false) return null;
  const exp = c.expedienteId;
  const pendientes = onb.documentosPendientes ?? 0;

  async function correr(clave: string, fn: () => Promise<{ ok: true; data: unknown } | { ok: false; error: string }>, exito: string) {
    setOcupado(clave);
    const r = await fn();
    setOcupado("");
    if (!r.ok) return onListo(r.error, "error");
    onListo(exito);
  }

  return (
    <Card className="p-4">
      {onb.estado === "listo_colaborador" && (
        <p className="mb-2 font-display text-sm font-bold text-good">{onb.texto}</p>
      )}
      {pendientes > 0 && (
        <p className="mb-2 text-sm font-semibold text-warn" title={onb.documentosPendientesLista.join(", ")}>
          Documentos pendientes: {pendientes}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => setConfirmar(true)} disabled={!live || Boolean(ocupado)}>
          <UserCheck className="h-4 w-4" /> Pasar a colaborador
        </Button>
        {pendientes > 0 && (
          <Button size="sm" variant="outline" disabled={!live || Boolean(ocupado)}
            onClick={() => void correr("docs", () => solicitarDocumentosPendientes(exp), `Se pidieron ${pendientes} documento(s) pendiente(s).`)}>
            <FileText className="h-4 w-4" /> {ocupado === "docs" ? "Solicitando…" : "Solicitar documentos pendientes"}
          </Button>
        )}
        <Button size="sm" variant="outline" disabled={!live || Boolean(ocupado)}
          onClick={() => void correr("tareas", () => asignarTareasOnboarding(exp), "Tareas de onboarding asignadas.")}>
          <ListChecks className="h-4 w-4" /> {ocupado === "tareas" ? "Asignando…" : "Asignar tareas"}
        </Button>
      </div>
      {confirmar && (
        <ModalMarco titulo="Pasar a colaborador" subtitulo={`${c.nombre} pasará a Colaboradores.`} onClose={() => setConfirmar(false)}>
          <div className="flex flex-col gap-3">
            {pendientes > 0 && (
              <p className="rounded-xl border border-warn/40 bg-warn-soft px-3 py-2 text-sm text-ink">
                Tiene {pendientes} documento(s) pendiente(s): se quedan «Pendiente» en su expediente.
              </p>
            )}
            <label className="flex flex-col gap-1 text-xs text-ink-2">
              Comentario (opcional)
              <input className={inputRH} value={comentario} onChange={(e) => setComentario(e.target.value)} />
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setConfirmar(false)} disabled={Boolean(ocupado)}>Cancelar</Button>
              <Button size="sm" disabled={Boolean(ocupado)} onClick={() => {
                setConfirmar(false);
                void correr("colaborador", () => pasarAColaborador(exp, comentario.trim()), `${c.nombre} pasó a Colaboradores.`);
              }}>
                {ocupado === "colaborador" ? "Guardando…" : "Pasar a colaborador"}
              </Button>
            </div>
          </div>
        </ModalMarco>
      )}
    </Card>
  );
}
