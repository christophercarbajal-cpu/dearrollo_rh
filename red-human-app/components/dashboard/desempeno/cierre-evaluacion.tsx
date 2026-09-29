"use client";

/* Ejecución y cierre de la evaluación de una persona (Desempeño v2 · Fase 5, 2026-09-27):
   · Notas de avance OPCIONALES con fecha (persona o criterio).
   · «Proponer con Red Human»: resumen, fortalezas y brechas SOLO con lo capturado; el evaluador edita o
     confirma (nada se confirma solo; ya no hay fortalezas automáticas por umbral de 85 %).
   · Conclusión del evaluador (obligatoria para completar). Una buena evaluación se cierra sin brechas.
   · Acciones por brecha CONFIRMADA (responsable, fecha, estado); un curso se asigna en Capacitación.
   · Historial de cambios a criterios/metas (anterior, nuevo, motivo, fecha, usuario). */

import { useEffect, useState } from "react";
import { History, Loader2, MessageSquarePlus, Plus, Sparkles, Trash2 } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import { inputRH } from "@/components/dashboard/modulos-rh";
import { cn } from "@/lib/utils";
import {
  agregarNotaDesempeno, crearAccionDesempeno, editarAccionDesempeno, fetchAccionesEvaluacion, fetchCursos, propuestaIaDesempeno,
  type AccionDesempeno, type BrechaDesempeno, type CambioDesempeno, type CriterioDesempeno, type Curso, type EvaluacionDesempeno,
} from "@/lib/api";
import { textoDia, textoFecha } from "@/lib/fechas";

const FORMATO = { day: "numeric", month: "short", year: "numeric" } as const;
const fecha = (iso?: string | null) => textoFecha(iso, FORMATO);
const ESTADO_ACCION: Record<string, string> = { abierta: "Abierta", en_proceso: "En proceso", completada: "Completada", cancelada: "Cancelada" };

/* ---------------- Notas de avance ---------------- */

export function NotasAvance({ evaluacion, criterios, soloLectura, onActualizada }: {
  evaluacion: EvaluacionDesempeno; criterios: CriterioDesempeno[]; soloLectura: boolean; onActualizada: (e: EvaluacionDesempeno) => void;
}) {
  const [texto, setTexto] = useState("");
  const [criterio, setCriterio] = useState("");
  const [error, setError] = useState("");
  const notas = evaluacion.notas ?? [];

  async function agregar() {
    if (!texto.trim()) return;
    const r = await agregarNotaDesempeno(evaluacion.id, texto, criterio);
    if (!r.ok) return setError(r.error);
    setTexto("");
    setCriterio("");
    onActualizada(r.data);
  }

  return (
    <div>
      <p className="text-xs font-medium text-ink-2">Notas de avance (opcionales)</p>
      {notas.length > 0 && (
        <ul className="mt-2 space-y-1.5">
          {notas.map((n) => (
            <li key={n.id} className="rounded-xl bg-surface-2/60 px-3 py-2 text-[13px] text-ink-2">
              <span className="font-mono text-[11px] text-ink-3">{fecha(n.fecha)} · {n.autor}{n.criterio ? ` · ${n.criterio}` : ""}</span>
              <span className="block">{n.texto}</span>
            </li>
          ))}
        </ul>
      )}
      {!soloLectura && (
        <div className="mt-2 grid gap-2 sm:grid-cols-6">
          <input value={texto} onChange={(e) => setTexto(e.target.value)} placeholder="Ej. Entregó el piloto antes de lo previsto" className={cn(inputRH, "sm:col-span-3")} />
          <select value={criterio} onChange={(e) => setCriterio(e.target.value)} className={cn(inputRH, "sm:col-span-2")} aria-label="Criterio de la nota">
            <option value="">Nota general</option>
            {criterios.map((c) => <option key={c.id} value={c.id}>{c.nombre}</option>)}
          </select>
          <Button size="sm" variant="outline" onClick={agregar} disabled={!texto.trim()}><MessageSquarePlus className="h-4 w-4" /> Agregar</Button>
        </div>
      )}
      {error && <p className="mt-1 text-xs text-bad">{error}</p>}
    </div>
  );
}

/* ---------------- Resumen, fortalezas, brechas y conclusión ---------------- */

export interface ValorCierre { resumen: string; fortalezas: string[]; brechas: BrechaDesempeno[]; conclusion: string }

export function CierreEvaluacion({ evaluacion, valor, onCambio, soloLectura }: {
  evaluacion: EvaluacionDesempeno; valor: ValorCierre; onCambio: (v: ValorCierre) => void; soloLectura: boolean;
}) {
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState("");
  const [nuevaFortaleza, setNuevaFortaleza] = useState("");

  async function proponer() {
    setOcupado(true);
    setError("");
    const r = await propuestaIaDesempeno(evaluacion.id);
    setOcupado(false);
    if (!r.ok) return setError(r.error);
    // la propuesta llena los campos, pero NADA queda confirmado: las brechas llegan sin confirmar
    onCambio({
      ...valor,
      resumen: r.data.resumen,
      fortalezas: r.data.fortalezas,
      brechas: [...valor.brechas.filter((b) => b.origen !== "ia" || b.confirmada), ...r.data.brechas.map((b) => ({ ...b, confirmada: false }))],
    });
  }

  const setBrecha = (i: number, b: BrechaDesempeno) => onCambio({ ...valor, brechas: valor.brechas.map((x, k) => (k === i ? b : x)) });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium text-ink-2">Resumen, fortalezas y brechas</p>
        {!soloLectura && (
          <Button size="sm" variant="secondary" onClick={proponer} disabled={ocupado}>
            {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />} Proponer con Red Human
          </Button>
        )}
      </div>
      {error && <p className="text-xs text-bad">{error}</p>}
      <textarea disabled={soloLectura} value={valor.resumen} onChange={(e) => onCambio({ ...valor, resumen: e.target.value })} rows={2}
        placeholder="Resumen del periodo (Red Human puede proponerlo con los resultados capturados)"
        className="w-full rounded-xl border border-border-soft bg-surface px-3 py-2 text-sm outline-none focus:border-brand" />

      <div>
        <p className="text-[11px] font-semibold text-good">Fortalezas (las confirma el evaluador)</p>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {valor.fortalezas.map((f, i) => (
            <span key={i} className="inline-flex items-center gap-1 rounded-lg bg-good-soft/50 px-2 py-1 text-[12px]">
              {f}
              {!soloLectura && <button onClick={() => onCambio({ ...valor, fortalezas: valor.fortalezas.filter((_, k) => k !== i) })} aria-label="Quitar fortaleza" className="text-ink-3 hover:text-bad">×</button>}
            </span>
          ))}
          {!valor.fortalezas.length && <span className="text-[12px] text-ink-3">Sin fortalezas registradas.</span>}
        </div>
        {!soloLectura && (
          <div className="mt-1.5 flex gap-2">
            <input value={nuevaFortaleza} onChange={(e) => setNuevaFortaleza(e.target.value)} placeholder="Agregar fortaleza" className={cn(inputRH, "h-9")} />
            <Button size="sm" variant="ghost" disabled={!nuevaFortaleza.trim()} onClick={() => { onCambio({ ...valor, fortalezas: [...valor.fortalezas, nuevaFortaleza.trim()] }); setNuevaFortaleza(""); }}><Plus className="h-4 w-4" /></Button>
          </div>
        )}
      </div>

      <div>
        <p className="text-[11px] font-semibold text-warn">Brechas — solo las confirmadas cuentan y pueden generar acciones</p>
        <ul className="mt-1.5 flex flex-col gap-2">
          {valor.brechas.map((b, i) => (
            <li key={b.id ?? i} className={cn("rounded-xl border p-3", b.confirmada ? "border-warn/40 bg-warn-soft/20" : "border-dashed border-border-soft")}>
              <div className="flex flex-wrap items-center gap-2">
                <input disabled={soloLectura} value={b.tema} onChange={(e) => setBrecha(i, { ...b, tema: e.target.value })} placeholder="Tema" className={cn(inputRH, "h-9 min-w-0 flex-1")} />
                {b.origen === "ia" && <Badge tone="brand">Propuesta IA</Badge>}
                <label className="inline-flex items-center gap-1.5 text-[12px] text-ink-2">
                  <input type="checkbox" disabled={soloLectura} checked={Boolean(b.confirmada)} onChange={(e) => setBrecha(i, { ...b, confirmada: e.target.checked })} className="h-4 w-4 rounded border-border-soft text-brand" />
                  Confirmada
                </label>
                {!soloLectura && <button onClick={() => onCambio({ ...valor, brechas: valor.brechas.filter((_, k) => k !== i) })} className="text-ink-3 hover:text-bad" aria-label="Quitar brecha"><Trash2 className="h-4 w-4" /></button>}
              </div>
              <input disabled={soloLectura} value={b.descripcion ?? ""} onChange={(e) => setBrecha(i, { ...b, descripcion: e.target.value })} placeholder="Qué falta (con evidencia)" className={cn(inputRH, "mt-2 h-9")} />
            </li>
          ))}
        </ul>
        {!valor.brechas.length && <p className="mt-1 text-[12px] text-ink-3">Sin brechas: una buena evaluación se cierra sin inventarlas.</p>}
        {!soloLectura && (
          <Button size="sm" variant="ghost" className="mt-1.5" onClick={() => onCambio({ ...valor, brechas: [...valor.brechas, { tema: "", descripcion: "", confirmada: true, origen: "manual" }] })}>
            <Plus className="h-4 w-4" /> Agregar brecha
          </Button>
        )}
      </div>

      <label className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-ink-2">Conclusión del evaluador <span className="text-bad">(obligatoria para completar)</span></span>
        <textarea disabled={soloLectura} value={valor.conclusion} onChange={(e) => onCambio({ ...valor, conclusion: e.target.value })} rows={2}
          className="w-full rounded-xl border border-border-soft bg-surface px-3 py-2 text-sm outline-none focus:border-brand" />
      </label>
    </div>
  );
}

/* ---------------- Acciones por brecha confirmada ---------------- */

export function AccionesBrechas({ evaluacion, brechasGuardadas, puedeEditar }: { evaluacion: EvaluacionDesempeno; brechasGuardadas: BrechaDesempeno[]; puedeEditar: boolean }) {
  const [acciones, setAcciones] = useState<AccionDesempeno[]>([]);
  const [cursos, setCursos] = useState<Curso[]>([]);
  const [nueva, setNueva] = useState<{ brechaId: string; tipo: "accion" | "curso"; descripcion: string; responsable: string; fecha: string; curso: string } | null>(null);
  const [error, setError] = useState("");
  const confirmadas = brechasGuardadas.filter((b) => b.confirmada && b.id);

  useEffect(() => {
    fetchAccionesEvaluacion(evaluacion.id).then((a) => setAcciones(a ?? []));
    fetchCursos().then((c) => setCursos((c ?? []).filter((x) => x.estado === "Publicado")));
  }, [evaluacion.id]);

  async function crear() {
    if (!nueva) return;
    setError("");
    const r = await crearAccionDesempeno(evaluacion.id, {
      brechaId: nueva.brechaId, tipo: nueva.tipo, descripcion: nueva.descripcion, responsable: nueva.responsable,
      fechaCompromiso: nueva.fecha, cursoCodigo: nueva.curso,
    });
    if (!r.ok) return setError(r.error);
    setAcciones([...acciones, r.data]);
    setNueva(null);
  }

  async function cambiarEstado(a: AccionDesempeno, estado: AccionDesempeno["estado"]) {
    const r = await editarAccionDesempeno(a.id, { estado });
    if (!r.ok) return setError(r.error);
    setAcciones(acciones.map((x) => (x.id === a.id ? r.data : x)));
  }

  if (!confirmadas.length && !acciones.length) {
    return <p className="text-[12px] text-ink-3">Las acciones nacen de brechas confirmadas y guardadas.</p>;
  }

  return (
    <div>
      <p className="text-xs font-medium text-ink-2">Acciones</p>
      <ul className="mt-2 flex flex-col gap-2">
        {acciones.map((a) => (
          <li key={a.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-border-soft p-3 text-[13px]">
            <span className="min-w-0 flex-1">
              <b>{a.tipo === "curso" ? `Curso: ${a.curso?.titulo ?? ""}` : a.descripcion || "Acción"}</b>
              <span className="block text-[11px] text-ink-3">Brecha: {a.brecha} · {a.responsable}{a.fechaCompromiso ? ` · para el ${textoDia(a.fechaCompromiso, FORMATO)}` : ""}{a.asignacion ? ` · asignado en Capacitación (${a.asignacion})` : ""}</span>
            </span>
            {puedeEditar ? (
              <select value={a.estado} onChange={(e) => cambiarEstado(a, e.target.value as AccionDesempeno["estado"])} className="h-9 rounded-lg border border-border-soft bg-surface px-2 text-[12px]" aria-label="Estado de la acción">
                {Object.entries(ESTADO_ACCION).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            ) : <Badge tone={a.estado === "completada" ? "good" : "neutral"}>{ESTADO_ACCION[a.estado]}</Badge>}
          </li>
        ))}
      </ul>
      {puedeEditar && confirmadas.length > 0 && (
        nueva ? (
          <div className="mt-2 grid gap-2 rounded-xl bg-surface-2/60 p-3 sm:grid-cols-2">
            <select value={nueva.brechaId} onChange={(e) => setNueva({ ...nueva, brechaId: e.target.value })} className={inputRH} aria-label="Brecha">
              {confirmadas.map((b) => <option key={b.id} value={b.id}>{b.tema}</option>)}
            </select>
            <select value={nueva.tipo} onChange={(e) => setNueva({ ...nueva, tipo: e.target.value as "accion" | "curso" })} className={inputRH} aria-label="Tipo de acción">
              <option value="accion">Acción</option>
              <option value="curso">Asignar curso (Capacitación)</option>
            </select>
            {nueva.tipo === "curso" ? (
              <select value={nueva.curso} onChange={(e) => setNueva({ ...nueva, curso: e.target.value })} className={cn(inputRH, "sm:col-span-2")} aria-label="Curso">
                <option value="">Elige un curso finalizado…</option>
                {cursos.map((c) => <option key={c.id} value={c.id}>{c.titulo}</option>)}
              </select>
            ) : (
              <input value={nueva.descripcion} onChange={(e) => setNueva({ ...nueva, descripcion: e.target.value })} placeholder="Qué se hará" className={cn(inputRH, "sm:col-span-2")} />
            )}
            <input value={nueva.responsable} onChange={(e) => setNueva({ ...nueva, responsable: e.target.value })} placeholder="Responsable" className={inputRH} />
            <input type="date" value={nueva.fecha} onChange={(e) => setNueva({ ...nueva, fecha: e.target.value })} className={inputRH} aria-label="Fecha compromiso" />
            <div className="flex gap-2 sm:col-span-2">
              <Button size="sm" onClick={crear} disabled={nueva.tipo === "curso" && !nueva.curso}>Crear acción</Button>
              <Button size="sm" variant="ghost" onClick={() => setNueva(null)}>Cancelar</Button>
            </div>
          </div>
        ) : (
          <Button size="sm" variant="outline" className="mt-2" onClick={() => setNueva({ brechaId: confirmadas[0].id ?? "", tipo: "accion", descripcion: "", responsable: "", fecha: "", curso: "" })}>
            <Plus className="h-4 w-4" /> Agregar acción
          </Button>
        )
      )}
      {error && <p className="mt-1 text-xs text-bad">{error}</p>}
    </div>
  );
}

/* ---------------- Historial de cambios ---------------- */

export function HistorialCambios({ cambios }: { cambios: CambioDesempeno[] }) {
  if (!cambios.length) return null;
  return (
    <details className="rounded-xl border border-border-soft p-3">
      <summary className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-ink-2"><History className="h-3.5 w-3.5" /> Historial de cambios ({cambios.length})</summary>
      <ul className="mt-2 space-y-1.5 text-[12px] text-ink-2">
        {cambios.map((h, i) => (
          <li key={i}>
            <span className="font-mono text-[11px] text-ink-3">{fecha(h.fecha)} · {h.usuario}</span> — «{h.criterio}» {h.campo}: <b>{String(h.anterior ?? "—")}</b> → <b>{String(h.nuevo ?? "—")}</b>
            {h.nivel === "persona" ? " (ajuste individual)" : ""}. Motivo: {h.motivo}
          </li>
        ))}
      </ul>
    </details>
  );
}

/* ---------------- Historial por persona (ficha del colaborador y «Mis evaluaciones») ---------------- */

export function HistorialDesempeno({ evaluaciones, vacio }: { evaluaciones: EvaluacionDesempeno[]; vacio: string }) {
  if (!evaluaciones.length) return <p className="text-sm text-ink-3">{vacio}</p>;
  return (
    <ul className="flex flex-col gap-3">
      {evaluaciones.map((e) => (
        <li key={e.id} className="rounded-2xl border border-border-soft p-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="font-semibold">{e.ciclo}{e.periodo ? ` · ${e.periodo}` : ""}</p>
            <span className="font-mono text-sm font-bold">{e.calificacion === null ? "—" : `${e.calificacion}%`}</span>
          </div>
          <p className="text-[11px] text-ink-3">
            {e.estado === "completada" ? "Completada" : e.estado === "en_proceso" ? "En proceso" : "Pendiente"} · evaluación {e.estadoEvaluacion === "cerrada" ? "cerrada" : e.estadoEvaluacion === "en_curso" ? "en curso" : "en borrador"} · evaluador: {e.evaluador || "—"}
          </p>
          {e.conclusion && <p className="mt-2 text-[13px] text-ink-2"><b>Conclusión:</b> {e.conclusion}</p>}
          {(e.criterios ?? []).length > 0 && (
            <ul className="mt-2 space-y-1 text-[12px] text-ink-2">
              {(e.criterios ?? []).map((c) => {
                const d = (e.cumplimiento ?? []).find((x) => x.criterio_id === c.id);
                return <li key={c.id}>• {c.nombre}: {d?.no_aplica ? "No aplica" : d?.cumplimiento === null || d?.cumplimiento === undefined ? "sin resultado" : `${d.cumplimiento}%`}</li>;
              })}
            </ul>
          )}
          {(e.fortalezas ?? []).length > 0 && <p className="mt-2 text-[12px] text-good">Fortalezas: {(e.fortalezas ?? []).join(" · ")}</p>}
          {e.brechas.filter((b) => b.confirmada).length > 0 && <p className="text-[12px] text-warn">Brechas: {e.brechas.filter((b) => b.confirmada).map((b) => b.tema).join(" · ")}</p>}
          {(e.acciones ?? []).length > 0 && (
            <p className="mt-1 text-[12px] text-ink-2">Acciones: {(e.acciones ?? []).map((a) => `${a.tipo === "curso" ? `curso «${a.curso?.titulo}»` : a.descripcion} (${ESTADO_ACCION[a.estado]})`).join(" · ")}</p>
          )}
        </li>
      ))}
    </ul>
  );
}
