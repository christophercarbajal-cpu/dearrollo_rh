"use client";

/* Onboarding v2 — Fase 3 (2026-09-28). Gestión activa en el tablero de Onboarding:
   - Avance SEPARADO: documentos Aprobados de los aplicables y tareas Realizadas de las vigentes. «No aplica» y
     «Cancelada» no cuentan en el total y se listan aparte con su motivo (adiós al 100 % falso).
   - Botón legado «Generar tareas de Onboarding» para quien ya estaba en Onboarding (no mueve la etapa).
   - «Confirmar ingreso» (fecha real) → habilita «Dar de alta». «Cerrar Onboarding» manual, nunca automático.
   - «No ingresó» (solo antes del alta): cancela tareas, detiene recordatorios y avisa a los responsables. */

import { useState } from "react";
import { AlertTriangle, CalendarCheck, CheckCircle2, Flag, ListChecks, Loader2, UserX, Wand2 } from "lucide-react";
import { Badge, Button, Eyebrow } from "@/components/ui";
import { CampoRH, ModalMarco, inputRH } from "@/components/dashboard/modulos-rh";
import { PanelTareasOnboarding } from "@/components/dashboard/onboarding/tareas-onboarding";
import { cerrarOnboarding, confirmarIngresoOnboarding, generarTareasOnboarding, registrarNoIngreso } from "@/lib/api";
import type { NuevoIngreso, ResumenTableroOnboarding } from "@/lib/phase2";
import { cn } from "@/lib/utils";
import { hoyLocal, textoDia } from "@/lib/fechas";

type Aviso = { tono: "ok" | "error" | "warn" | "info"; texto: string } | null;

function fechaCorta(iso: string | null) {
  // las fechas de ingreso se guardan a medianoche UTC del día capturado: se leen como día, sin zona
  return textoDia(iso, { day: "numeric", month: "short", year: "numeric" }) || "—";
}

function Barra({ pct, tono }: { pct: number; tono: "good" | "brand" }) {
  return (
    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2">
      <div className={cn("h-full rounded-full transition-all", pct === 100 ? "bg-good" : tono === "good" ? "bg-good" : "bg-brand")} style={{ width: `${pct}%` }} />
    </div>
  );
}

/** Dos mini barras para la tarjeta de la lista (documentos aprobados · tareas realizadas). */
export function MiniAvanceOnboarding({ o }: { o: ResumenTableroOnboarding }) {
  return (
    <div className="mt-3 space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="w-16 shrink-0 text-[10px] text-ink-3">Docs</span>
        <Barra pct={o.documentos.pct} tono="brand" />
        <span className="font-mono text-[10px] tabular text-ink-2">{o.documentos.aprobados.length}/{o.documentos.total}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="w-16 shrink-0 text-[10px] text-ink-3">Tareas</span>
        <Barra pct={o.tareas.pct} tono="brand" />
        <span className="font-mono text-[10px] tabular text-ink-2">{o.iniciado ? `${o.tareas.realizadas}/${o.tareas.total}` : "—"}</span>
      </div>
      <div className="flex flex-wrap gap-1.5 pt-0.5">
        {!o.iniciado && <Badge tone="warn">Sin tareas</Badge>}
        {o.tareas.atrasadas > 0 && <Badge tone="bad">{o.tareas.atrasadas} atrasada{o.tareas.atrasadas === 1 ? "" : "s"}</Badge>}
        {o.ingreso.confirmado && !o.alta && <Badge tone="good">Ingreso confirmado</Badge>}
        {o.alta && !o.cerrado && <Badge tone={o.puedeCerrar ? "good" : "neutral"}>{o.puedeCerrar ? "Listo para cerrar" : "Alta hecha"}</Badge>}
        {o.cerrado && <Badge tone="neutral">Cerrado</Badge>}
      </div>
    </div>
  );
}

export function GestionOnboarding({ n, live, setAviso, onRecargar }: {
  n: NuevoIngreso;
  live: boolean;
  setAviso: (a: Aviso) => void;
  onRecargar: () => void;
}) {
  const o = n.onboarding;
  const [ocupado, setOcupado] = useState("");
  const [fechaReal, setFechaReal] = useState(hoyLocal());
  const [confirmando, setConfirmando] = useState(false);
  if (!o || n.expedienteId == null) return null;
  const expedienteId = n.expedienteId;

  async function generar() {
    setOcupado("generar");
    const r = await generarTareasOnboarding(expedienteId);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setAviso({ tono: "ok", texto: `${r.data.tareas.length} tareas generadas (${r.data.plantilla || "configuración predeterminada"}). La etapa no cambió.` });
    onRecargar();
  }

  async function confirmar() {
    setOcupado("confirmar");
    const r = await confirmarIngresoOnboarding(expedienteId, fechaReal);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setConfirmando(false);
    setAviso({ tono: "ok", texto: `Ingreso confirmado el ${fechaCorta(r.data.ingreso.real)}.${r.data.plazosRecalculados ? ` ${r.data.plazosRecalculados} plazo(s) recalculado(s) con la fecha real.` : ""} Ya puedes dar de alta.` });
    onRecargar();
  }

  return (
    <div className="border-b border-border-faint p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Eyebrow>Avance del Onboarding</Eyebrow>
        {o.tareas.atrasadas > 0 && (
          <Badge tone="bad" dot>{o.tareas.atrasadas} tarea{o.tareas.atrasadas === 1 ? "" : "s"} atrasada{o.tareas.atrasadas === 1 ? "" : "s"}</Badge>
        )}
      </div>

      {!o.iniciado && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-warn/30 bg-warn-soft px-3.5 py-3">
          <p className="text-[13px] text-ink-2">
            Esta persona entró a Onboarding antes de las tareas. Genera sus tareas desde la plantilla que aplica; la etapa no cambia.
          </p>
          {live && (
            <Button size="sm" onClick={generar} disabled={Boolean(ocupado)}>
              {ocupado === "generar" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />} Generar tareas de Onboarding
            </Button>
          )}
        </div>
      )}

      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        {/* Documentos */}
        <div className="rounded-xl border border-border-soft p-3.5">
          <p className="flex items-center justify-between text-sm font-semibold">
            <span>Documentos aprobados</span>
            <span className="font-mono text-xs tabular">{o.documentos.aprobados.length} de {o.documentos.total} · {o.documentos.pct}%</span>
          </p>
          <div className="mt-2 flex"><Barra pct={o.documentos.pct} tono="good" /></div>
          {o.documentos.faltantes.length > 0 && (
            <ul className="mt-2.5 space-y-1 text-[12px]">
              {o.documentos.faltantes.map((d) => (
                <li key={d.tipo} className="flex items-center justify-between gap-2">
                  <span className="truncate text-ink-2">{d.tipo}{d.obligatorio ? "" : " (opcional)"}</span>
                  <Badge tone={d.estado === "Rechazado" ? "bad" : d.estado === "Por revisar" ? "warn" : "neutral"}>{d.estado}</Badge>
                </li>
              ))}
            </ul>
          )}
          {o.documentos.noAplica.length > 0 && (
            <div className="mt-3 border-t border-border-faint pt-2">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-ink-3">No aplica (fuera del total)</p>
              <ul className="mt-1 space-y-0.5 text-[11px] text-ink-3">
                {o.documentos.noAplica.map((d) => <li key={d.tipo}><b className="font-medium text-ink-2">{d.tipo}</b> — {d.motivo}{d.por ? ` (${d.por})` : ""}</li>)}
              </ul>
            </div>
          )}
        </div>

        {/* Tareas */}
        <div className="rounded-xl border border-border-soft p-3.5">
          <p className="flex items-center justify-between text-sm font-semibold">
            <span>Tareas realizadas</span>
            <span className="font-mono text-xs tabular">{o.tareas.realizadas} de {o.tareas.total} · {o.tareas.pct}%</span>
          </p>
          <div className="mt-2 flex"><Barra pct={o.tareas.pct} tono="good" /></div>
          <p className="mt-2 text-[12px] text-ink-3">
            {o.tareas.pendientes} pendiente{o.tareas.pendientes === 1 ? "" : "s"}{o.tareas.atrasadas ? ` · ${o.tareas.atrasadas} atrasada${o.tareas.atrasadas === 1 ? "" : "s"}` : ""}
          </p>
          {o.tareas.canceladas.length > 0 && (
            <div className="mt-3 border-t border-border-faint pt-2">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-ink-3">Canceladas (fuera del total)</p>
              <ul className="mt-1 space-y-0.5 text-[11px] text-ink-3">
                {o.tareas.canceladas.map((t) => <li key={t.nombre}><b className="font-medium text-ink-2">{t.nombre}</b> — {t.motivo}{t.por ? ` (${t.por})` : ""}</li>)}
              </ul>
            </div>
          )}
        </div>
      </div>

      {o.iniciado && (
        <div className="mt-4">
          <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold"><ListChecks className="h-4 w-4 text-ink-3" /> Tareas</p>
          <PanelTareasOnboarding
            key={`${o.tareas.realizadas}-${o.tareas.total}-${o.tareas.canceladas.length}-${o.ingreso.real ?? ""}`}
            expedienteId={expedienteId}
            live={live && !o.cerrado && !o.noIngreso}
            onCambio={onRecargar}
          />
        </div>
      )}

      {/* Ingreso */}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border-soft bg-surface-2/40 px-3.5 py-3">
        <div className="text-[13px] text-ink-2">
          <p>Ingreso previsto: <b className="text-ink">{fechaCorta(o.ingreso.prevista)}</b></p>
          {o.ingreso.confirmado ? (
            <p className="flex items-center gap-1 text-good">
              <CheckCircle2 className="h-3.5 w-3.5" /> Llegó el {fechaCorta(o.ingreso.real)} · confirmado por {o.ingreso.confirmadoPor}
            </p>
          ) : (
            <p className="text-[12px] text-ink-3">«Dar de alta» se habilita al confirmar el ingreso real.</p>
          )}
        </div>
        {live && o.iniciado && !o.ingreso.confirmado && !o.alta && !o.noIngreso && (
          <Button size="sm" onClick={() => setConfirmando(true)} disabled={Boolean(ocupado)}>
            <CalendarCheck className="h-4 w-4" /> Confirmar ingreso
          </Button>
        )}
      </div>

      {confirmando && (
        <ModalMarco titulo="Confirmar ingreso" subtitulo="Registra el día en que la persona llegó. Los plazos pendientes se recalculan con esta fecha." onClose={() => setConfirmando(false)}>
          <CampoRH label="Fecha real de ingreso">
            <input type="date" value={fechaReal} max={hoyLocal()} onChange={(e) => setFechaReal(e.target.value)} className={inputRH} />
          </CampoRH>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setConfirmando(false)}>Cancelar</Button>
            <Button size="sm" onClick={confirmar} disabled={!fechaReal || Boolean(ocupado)}>
              {ocupado === "confirmar" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CalendarCheck className="h-4 w-4" />} Confirmar ingreso
            </Button>
          </div>
        </ModalMarco>
      )}
    </div>
  );
}

/** «Cerrar Onboarding» (después del alta; manual, nunca automático). */
export function BotonCerrarOnboarding({ n, modoPrueba, setAviso, onRecargar }: {
  n: NuevoIngreso; modoPrueba: boolean; setAviso: (a: Aviso) => void; onRecargar: () => void;
}) {
  const o = n.onboarding;
  const [ocupado, setOcupado] = useState(false);
  if (!o || !o.alta || n.expedienteId == null) return null;
  const expedienteId = n.expedienteId;
  if (o.cerrado) {
    return <p className="mt-3 flex items-center gap-1.5 text-sm text-good"><Flag className="h-4 w-4" /> Onboarding cerrado por {o.cerradoPor}.</p>;
  }
  const habilitado = o.puedeCerrar || modoPrueba;
  return (
    <div className="mt-3">
      <Button
        size="lg"
        variant={habilitado ? "primary" : "outline"}
        className="w-full"
        disabled={!habilitado || ocupado}
        onClick={async () => {
          setOcupado(true);
          const r = await cerrarOnboarding(expedienteId);
          setOcupado(false);
          if (!r.ok) return setAviso({ tono: "error", texto: r.error });
          setAviso({ tono: "ok", texto: "Onboarding cerrado. Sale del tablero (consúltalo con «Mostrar cerrados»)." });
          onRecargar();
        }}
      >
        {ocupado ? <Loader2 className="h-5 w-5 animate-spin" /> : <Flag className="h-5 w-5" />} Cerrar Onboarding
      </Button>
      {!o.puedeCerrar && o.faltanCierre.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[12px] text-ink-3">
          {o.faltanCierre.slice(0, 6).map((f) => <li key={f} className="flex items-center gap-1"><AlertTriangle className="h-3 w-3 text-warn" /> {f}</li>)}
          {o.faltanCierre.length > 6 && <li>… y {o.faltanCierre.length - 6} más</li>}
        </ul>
      )}
    </div>
  );
}

/** «No ingresó» (solo antes del alta): modal con motivo. */
export function ModalNoIngreso({ n, onClose, setAviso, onRecargar }: {
  n: NuevoIngreso; onClose: () => void; setAviso: (a: Aviso) => void; onRecargar: () => void;
}) {
  const [motivo, setMotivo] = useState("");
  const [ocupado, setOcupado] = useState(false);
  return (
    <ModalMarco
      titulo="No ingresó"
      subtitulo="Se cancelan las tareas abiertas, se detienen los recordatorios, se avisa a los responsables y queda en el historial del candidato."
      onClose={onClose}
    >
      <CampoRH label="Motivo">
        <input value={motivo} onChange={(e) => setMotivo(e.target.value)} className={inputRH} placeholder="p. ej. Aceptó otra oferta" autoFocus />
      </CampoRH>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onClose} disabled={ocupado}>Volver</Button>
        <Button
          size="sm"
          disabled={!motivo.trim() || ocupado || n.expedienteId == null}
          onClick={async () => {
            setOcupado(true);
            const r = await registrarNoIngreso(n.expedienteId!, motivo.trim());
            setOcupado(false);
            if (!r.ok) return setAviso({ tono: "error", texto: r.error });
            const fallidos = r.data.avisosResponsables.filter((a) => !a.enviado).length;
            setAviso({
              tono: fallidos ? "warn" : "ok",
              texto: `Registrado: ${n.nombre} no ingresó. Tareas canceladas y recordatorios detenidos.` +
                (r.data.avisosResponsables.length ? ` Avisos a responsables: ${r.data.avisosResponsables.length - fallidos} enviado(s)${fallidos ? `, ${fallidos} sin enviar` : ""}.` : ""),
            });
            onClose();
            onRecargar();
          }}
        >
          {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserX className="h-4 w-4" />} Registrar «No ingresó»
        </Button>
      </div>
    </ModalMarco>
  );
}
