"use client";

/* Ficha del candidato → «Resumen» minimalista (UX 2026-10-07). La ETAPA la define la ruta (motor de avance del backend:
   compuerta + avance automático); aquí no hay botones para mover de etapa. Orden fijo:
     1. Recomendación + UN ÚNICO botón principal = la siguiente acción concreta de la ruta (`siguienteAccion` de la API).
     2. Resultados clave (un dato, un lugar).
     3. Avance de la ruta: una línea por actividad con UNA sola etiqueta de estado (`estadoUnificado`); el detalle se
        despliega y ahí viven las acciones de la actividad: ejecutar, sincronizar, liga de Telegram, Omitir (motivo
        obligatorio), Cancelar y Reactivar.
     4. Fortalezas (máx. 3) y Puntos por validar (máx. 3).
   Las acciones REUTILIZAN lo que ya existe: «Agregar evaluación» precargada, las tarjetas de evaluación, el chat, los
   documentos o el expediente. Cambiar ruta / agregar actividad / descartar / eliminar viven en el «…» del encabezado. */

import { useEffect, useState } from "react";
import {
  AlertTriangle, ArrowRight, Ban, CheckCircle2, ChevronDown, Clock, Link2, Play, RefreshCw, RotateCcw, SkipForward, Sparkles, XCircle,
} from "lucide-react";
import { Badge, Button, Card, Eyebrow } from "@/components/ui";
import { ModalMarco, inputRH } from "@/components/dashboard/modulos-rh";
import { BadgeIntegral } from "@/components/dashboard/evaluaciones/resultado-integral";
import { useSesion } from "@/components/sesion";
import {
  agregarActividadProceso, cancelarPasoProceso, fetchOpcionesProceso, fetchSeguimiento, moverEtapaCandidato, nombreEtapa,
  omitirPasoProceso, reactivarPasoProceso, sincronizarEvaluacion, type OpcionesProceso,
} from "@/lib/api";
import type { AccionPaso, Candidato, EstadoUnificado, EtapaCandidato, PasoSeguimiento, SeguimientoProceso as Seg } from "@/lib/data";
import { textoDia } from "@/lib/fechas";
import { cn } from "@/lib/utils";

/** Una etiqueta por actividad (los 7 estados visibles). */
export const TONO_ESTADO_U: Record<EstadoUnificado, "neutral" | "brand" | "human" | "good" | "warn" | "bad"> = {
  sin_iniciar: "neutral", programada: "human", en_curso: "brand", pendiente_aprobacion: "warn",
  completada: "good", omitida: "neutral", no_favorable: "bad",
};

export type PresetPaso = { tipo: string; pasoId: string; titulo: string; usuarioId?: number | null };
type Pestana = NonNullable<AccionPaso["pestana"]>;

const TONO_RECOMENDACION: Record<string, { texto: string; icon: typeof CheckCircle2 }> = {
  "Avanzar a contratación": { texto: "text-good", icon: CheckCircle2 },
  "Realizar entrevista humana": { texto: "text-warn", icon: Sparkles },
  "Realizar Entrevista Red Human": { texto: "text-brand", icon: Sparkles },
  "Reintentar Entrevista Red Human": { texto: "text-warn", icon: RotateCcw },
  "No avanzar": { texto: "text-bad", icon: XCircle },
};

export function SeguimientoProceso({ c, live, version, onCambio, onIniciarEvaluacion, onAbrir, onSolicitarDocumentos, onAlta, onSeg, setAviso }: {
  c: Candidato;
  live: boolean;
  version: number;
  onCambio: (c: Candidato) => void;
  onIniciarEvaluacion: (p: PresetPaso) => void;
  onAbrir: (pestana: Pestana) => void;
  onSolicitarDocumentos: () => void;
  /** «Alta como colaborador»: abre la confirmación del alta (la misma de siempre). */
  onAlta?: () => void;
  /** El encabezado de la ficha usa el seguimiento para su menú «…» (cambiar ruta, liga de Telegram). */
  onSeg?: (s: Seg) => void;
  setAviso: (a: { tono: "ok" | "error" | "warn"; texto: string } | null) => void;
}) {
  const puedeAutorizar = Boolean(useSesion().usuario?.puedeAutorizarOmisiones);
  const [seg, setSegLocal] = useState<Seg | null>(c.proceso ?? null);
  const [ocupado, setOcupado] = useState("");
  const [abierto, setAbierto] = useState<string | null>(null);
  const [decision, setDecision] = useState<{ tipo: "omitir" | "cancelar"; paso: PasoSeguimiento; motivo: string } | null>(null);

  function setSeg(s: Seg) {
    setSegLocal(s);
    onSeg?.(s);
  }

  useEffect(() => {
    if (c.proceso) setSeg(c.proceso);
    fetchSeguimiento(c.id).then((s) => s && setSeg(s));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c, version]);

  if (!seg || !seg.tieneProceso) return <ResumenSinRuta c={c} />;

  function ejecutar(paso: PasoSeguimiento | undefined, accion: AccionPaso | null | undefined) {
    if (!accion) return;
    if (paso?.tipo === "alta" && onAlta) return onAlta();
    if (accion.clave === "iniciar_evaluacion" && paso) {
      const r = paso.responsableConfig;
      return onIniciarEvaluacion({ tipo: paso.tipo, pasoId: paso.id, titulo: `Iniciar: ${paso.nombre}`, usuarioId: r?.tipo === "usuario" ? r.usuario_id : null });
    }
    if (accion.clave === "consultar_evaluacion") return onAbrir("evaluaciones");
    if (accion.clave === "solicitar_documentos") return onSolicitarDocumentos();
    if (accion.clave === "validar_documentos") return onAbrir("documentos");
    if (paso?.tipo === "documentos" && accion.clave === "abrir" && c.etapa === "Contratación") return onSolicitarDocumentos();
    if (accion.pestana) onAbrir(accion.pestana);
  }

  /** Solo cuando la ruta lo permite (todos los obligatorios de la etapa cumplidos y su avance automático apagado). */
  async function continuar(etapa: EtapaCandidato) {
    setOcupado("avanzar");
    const r = await moverEtapaCandidato(c.id, etapa);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    onCambio(r.data);
    setAviso({ tono: "ok", texto: `Continúa en ${nombreEtapa(etapa)}.` });
  }

  async function confirmarDecision() {
    if (!decision) return;
    setOcupado("decision");
    const fn = decision.tipo === "omitir" ? omitirPasoProceso : cancelarPasoProceso;
    const r = await fn(c.id, decision.paso.id, decision.motivo);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setSeg(r.data.proceso);
    onCambio(r.data.candidato);
    const movida = r.data.candidato.etapa !== c.etapa;
    setDecision(null);
    setAviso({ tono: "ok", texto: `«${decision.paso.nombre}» quedó ${decision.tipo === "omitir" ? "omitida" : "cancelada"}.${movida ? ` La ruta continúa en ${nombreEtapa(r.data.candidato.etapa)}.` : ""}` });
  }

  async function copiarLigaTelegram(liga: string, paso: PasoSeguimiento) {
    try {
      await navigator.clipboard.writeText(liga);
      setAviso({ tono: "ok", texto: `Liga de Telegram de «${paso.nombre}» copiada. Solo funciona desde el Telegram con el número del candidato.` });
    } catch {
      setAviso({ tono: "warn", texto: `Copia la liga: ${liga}` });
    }
  }

  /** Psicométricas.mx sin webhook (desarrollo): consulta su API, trae JSON + PDF y la actividad queda Completada. */
  async function sincronizar(paso: PasoSeguimiento) {
    if (!paso.evaluacion) return;
    setOcupado(`sync-${paso.id}`);
    const r = await sincronizarEvaluacion(paso.evaluacion);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    const s = await fetchSeguimiento(c.id);
    if (s) setSeg(s);
    setAviso(r.data.sincronizacion === "resultado_recibido"
      ? { tono: "ok", texto: `Resultado de «${paso.nombre}» recibido (JSON y PDF).` }
      : { tono: "warn", texto: `Psicométricas.mx todavía no reporta «${paso.nombre}» como terminada.` });
  }

  async function reactivar(paso: PasoSeguimiento) {
    const r = await reactivarPasoProceso(c.id, paso.id);
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setSeg(r.data.proceso);
    onCambio(r.data.candidato);
  }

  const sig = seg.siguienteAccion;
  const pasos = (seg.etapas ?? []).flatMap((e) => e.pasos);
  const pasoSig = sig?.paso ? pasos.find((p) => p.id === sig.paso) : undefined;
  const tg = seg.telegram?.disponible ? seg.telegram : null;
  const rec = c.recomendacionRedHuman ? TONO_RECOMENDACION[c.recomendacionRedHuman] : null;
  const RecIcon = rec?.icon ?? Sparkles;

  // UN botón principal: la siguiente acción concreta que define la ruta.
  let principal: { texto: string; onClick: () => void; icono: React.ReactNode } | null = null;
  if (live && sig && c.activa !== false) {
    if ((sig.tipo === "paso" || sig.tipo === "abrir") && sig.accion) {
      principal = { texto: pasoSig?.tipo === "alta" ? "Dar de alta como colaborador" : sig.texto, onClick: () => ejecutar(pasoSig, sig.accion), icono: <Play className="h-4 w-4" /> };
    } else if (sig.tipo === "avanzar" && sig.etapa) {
      principal = { texto: `Continuar a ${nombreEtapa(sig.etapa)}`, onClick: () => continuar(sig.etapa!), icono: <ArrowRight className="h-4 w-4" /> };
    } else if (sig.tipo === "esperar" && sig.accion && pasoSig?.estadoUnificado === "pendiente_aprobacion") {
      principal = { texto: `Aprobar: ${pasoSig.nombre}`, onClick: () => ejecutar(pasoSig, sig.accion), icono: <CheckCircle2 className="h-4 w-4" /> };
    }
  }
  const textoEspera = !principal && sig ? `${sig.texto}${sig.detalle ? ` — ${sig.detalle}` : ""}` : "";

  return (
    <div className="flex flex-col gap-3">
      {/* 1. Recomendación + botón principal */}
      <Card className="p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-start gap-2.5">
            <RecIcon className={cn("mt-0.5 h-5 w-5 shrink-0", rec?.texto ?? "text-ink-3")} />
            <div className="min-w-0">
              <p className="font-mono text-[10px] font-bold uppercase tracking-wider text-ink-3">Recomendación</p>
              <p className={cn("font-display text-base font-bold leading-tight", rec?.texto ?? "text-ink")}>
                {c.recomendacionRedHuman || c.resultadoIntegral?.texto || "Sin recomendación todavía"}
              </p>
              {(c.recomendacionMotivo || (!c.recomendacionRedHuman && c.resultadoIntegral?.motivo)) && (
                <p className="mt-0.5 line-clamp-2 text-[12px] leading-snug text-ink-2">{c.recomendacionMotivo || c.resultadoIntegral?.motivo}</p>
              )}
            </div>
          </div>
          {principal && (
            <Button size="sm" className="shrink-0" onClick={principal.onClick} disabled={Boolean(ocupado)}>
              {principal.icono} {principal.texto}
            </Button>
          )}
        </div>
        {textoEspera && (
          <p className={cn("mt-3 flex items-start gap-2 rounded-lg px-2.5 py-1.5 text-[12px]", sig?.tipo === "fin" ? "bg-good-soft/50" : "bg-surface-2", "text-ink-2")}>
            {sig?.tipo === "fin" ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-good" /> : <Clock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" />}
            <span>{textoEspera}</span>
          </p>
        )}
        {(seg.alertas ?? []).length > 0 && (
          <p className="mt-2 flex items-center gap-1.5 text-[11px] font-semibold text-bad">
            <AlertTriangle className="h-3.5 w-3.5" /> {seg.alertas!.map((a) => `${a.texto}${a.fechaLimite ? ` (${textoDia(a.fechaLimite)})` : ""}`).join(" · ")}
          </p>
        )}
      </Card>

      {/* 2. Resultados clave */}
      <ResultadosClave c={c} />

      {/* 3. Avance de la ruta */}
      <Card className="p-0">
        <div className="flex items-baseline justify-between gap-2 px-4 pt-3 pb-1.5">
          <Eyebrow>Avance de la ruta</Eyebrow>
          <span className="truncate text-[11px] text-ink-3">{seg.plantilla || "Ruta propia"} · v{seg.version}</span>
        </div>
        <ul className="pb-1">
          {(seg.etapas ?? []).filter((e) => e.pasos.length > 0).map((e) => (
            <li key={e.etapa}>
              <p className={cn("px-4 pt-2 pb-0.5 text-[10px] font-bold uppercase tracking-wide", e.actual ? "text-brand" : "text-ink-3")}>
                {e.texto}{e.actual ? " · etapa actual" : ""}
              </p>
              <ul>
                {e.pasos.map((p) => {
                  const ab = abierto === p.id;
                  const estadoU = (p.estadoUnificado ?? "sin_iniciar") as EstadoUnificado;
                  return (
                    <li key={p.id} className="border-t border-border-faint first:border-t-0">
                      <button
                        className="flex w-full items-center gap-2 px-4 py-1.5 text-left hover:bg-surface-2/60"
                        onClick={() => setAbierto(ab ? null : p.id)}
                        aria-expanded={ab}
                      >
                        <ChevronDown className={cn("h-3.5 w-3.5 shrink-0 text-ink-3 transition-transform", !ab && "-rotate-90")} />
                        <span className="min-w-0 flex-1 truncate text-[13px]">
                          {p.nombre}
                          {p.obligatorio && <span className="ml-1 text-bad" title="Obligatoria">*</span>}
                        </span>
                        <Badge tone={TONO_ESTADO_U[estadoU]}>{p.estadoUnificadoTexto ?? p.estadoTexto}</Badge>
                      </button>
                      {ab && (
                        <div className="flex flex-col gap-2 bg-surface-2/40 px-4 py-2.5 pl-10 text-[12px] text-ink-2">
                          <p>
                            <b className="text-ink">Responsable:</b> {p.responsable || "—"} · {p.reglaTexto}
                            {p.dependeDe.length > 0 ? ` · espera a ${p.dependeDe.map((d) => pasos.find((x) => x.id === d)?.nombre ?? d).join(", ")}` : " · en paralelo"}
                            {p.plazoDias != null && ` · plazo ${p.plazoDias} día(s)`}
                            {p.adhoc && " · solo este candidato"}
                            {p.heredado && " · fuera de la ruta vigente"}
                          </p>
                          {p.espera && <p className="font-medium text-warn">{p.espera}</p>}
                          {(p.resultadoTexto || p.detalle) && (
                            <p>{p.resultadoTexto ? <b className="text-ink">{p.resultadoTexto}. </b> : null}{p.detalle}{p.revisadoPor && p.estado === "completada" ? ` · ${p.revisadoPor}` : ""}</p>
                          )}
                          {p.vencido && <p className="font-semibold text-bad">Plazo vencido (solo alerta)</p>}
                          {live && (
                            <div className="flex flex-wrap items-center gap-1.5">
                              {p.accion && (
                                <Button size="sm" variant="outline" onClick={() => ejecutar(p, p.accion)} disabled={Boolean(ocupado) || c.activa === false}>
                                  {p.tipo === "alta" ? "Dar de alta" : p.accion.texto}
                                </Button>
                              )}
                              {p.sincronizable && (
                                <Button size="sm" variant="outline" onClick={() => sincronizar(p)} disabled={Boolean(ocupado)}>
                                  <RefreshCw className={cn("h-3.5 w-3.5", ocupado === `sync-${p.id}` && "animate-spin")} /> Sincronizar resultado
                                </Button>
                              )}
                              {tg?.pasos[p.id] && (p.estado === "pendiente" || p.estado === "en_curso") && (
                                <Button size="sm" variant="ghost" onClick={() => copiarLigaTelegram(tg.pasos[p.id], p)}><Link2 className="h-3.5 w-3.5" /> Liga de Telegram</Button>
                              )}
                              {(p.estado === "pendiente" || p.estado === "en_curso") && !p.heredado && (
                                <>
                                  <Button size="sm" variant="ghost" onClick={() => setDecision({ tipo: "omitir", paso: p, motivo: "" })}>
                                    <SkipForward className="h-3.5 w-3.5" /> Omitir actividad
                                  </Button>
                                  <Button size="sm" variant="ghost" className="text-bad hover:bg-bad-soft" onClick={() => setDecision({ tipo: "cancelar", paso: p, motivo: "" })}>
                                    <Ban className="h-3.5 w-3.5" /> Cancelar
                                  </Button>
                                </>
                              )}
                              {(p.estado === "omitida" || p.estado === "cancelada") && (
                                <Button size="sm" variant="ghost" onClick={() => reactivar(p)}><RotateCcw className="h-3.5 w-3.5" /> Reactivar</Button>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
        </ul>
      </Card>

      {/* 4. Fortalezas y puntos por validar (máx. 3 cada uno) */}
      <FortalezasYPuntos c={c} />

      {decision && (
        <ModalMarco
          titulo={`${decision.tipo === "omitir" ? "Omitir" : "Cancelar"} «${decision.paso.nombre}»`}
          subtitulo={decision.paso.obligatorio ? "Actividad obligatoria: requiere motivo y el permiso «Autorizar omisiones»." : "Escribe el motivo; queda en el historial con tu nombre."}
          onClose={() => setDecision(null)}
        >
          <div className="flex flex-col gap-3">
            <textarea
              className={cn(inputRH, "h-24 py-2")}
              value={decision.motivo}
              onChange={(ev) => setDecision({ ...decision, motivo: ev.target.value })}
              placeholder="Motivo (obligatorio, mínimo 10 caracteres)"
            />
            {decision.paso.obligatorio && !puedeAutorizar && (
              <p className="rounded-xl border border-warn/40 bg-warn-soft px-3 py-2 text-[12px] text-ink-2">
                No tienes el permiso «Autorizar omisiones». Pídelo a un Administrador (Configuración → Usuarios).
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setDecision(null)}>Cerrar</Button>
              <Button size="sm" onClick={confirmarDecision}
                disabled={Boolean(ocupado) || decision.motivo.trim().length < 10 || (decision.paso.obligatorio && !puedeAutorizar)}>
                {ocupado ? "Guardando…" : decision.tipo === "omitir" ? "Omitir actividad" : "Cancelar actividad"}
              </Button>
            </div>
          </div>
        </ModalMarco>
      )}
    </div>
  );
}

/** Resultados clave: cada dato UNA vez (prefiltro, CV, entrevista, evaluación integral, expediente). */
function ResultadosClave({ c }: { c: Candidato }) {
  const pf = c.prefiltroResumen;
  const prefiltro = !pf ? "En curso" : pf.resultado === "no_cumple" || pf.incumplidos.length > 0 ? "No cumple" : "Cumple";
  const items: { k: string; v: React.ReactNode; tono?: string }[] = [
    { k: "Prefiltro", v: prefiltro, tono: prefiltro === "Cumple" ? "text-good" : prefiltro === "No cumple" ? "text-bad" : "text-ink-3" },
    ...(c.score != null ? [{ k: "CV", v: `${c.score}/100` }] : []),
    ...(c.afinidadGlobal != null ? [{ k: "Entrevista Red Human", v: `${c.afinidadGlobal}/100` }] : []),
    ...(c.expedienteId != null ? [{ k: "Expediente", v: `${c.expedienteProgreso ?? 0}%` }] : []),
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-xl border border-border-soft bg-surface px-4 py-2.5">
      {items.map((i) => (
        <span key={i.k} className="text-[12px] text-ink-3">
          {i.k}: <b className={cn("font-semibold", i.tono ?? "text-ink")}>{i.v}</b>
        </span>
      ))}
      {c.resultadoIntegral && <BadgeIntegral r={c.resultadoIntegral} compacto />}
    </div>
  );
}

function FortalezasYPuntos({ c }: { c: Candidato }) {
  const f = (c.fortalezasPrincipales ?? []).slice(0, 3);
  const p = (c.puntosPorValidar ?? []).slice(0, 3);
  if (!f.length && !p.length) return null;
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {f.length > 0 && (
        <Card className="p-3.5">
          <Eyebrow>Fortalezas</Eyebrow>
          <ul className="mt-1.5 space-y-1">
            {f.map((x, i) => (
              <li key={i} className="flex items-start gap-1.5 text-[12px] leading-snug text-ink-2">
                <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-good" /> <span className="break-words">{x}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {p.length > 0 && (
        <Card className="p-3.5">
          <Eyebrow>Puntos por validar</Eyebrow>
          <ul className="mt-1.5 space-y-1">
            {p.map((x, i) => (
              <li key={i} className="flex items-start gap-1.5 text-[12px] leading-snug text-ink-2">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" /> <span className="break-words">{x}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

/** Sin ruta (no debería pasar: toda postulación tiene una): solo resultados, fortalezas y puntos. */
function ResumenSinRuta({ c }: { c: Candidato }) {
  return (
    <div className="flex flex-col gap-3">
      <ResultadosClave c={c} />
      <FortalezasYPuntos c={c} />
    </div>
  );
}

/** «Agregar actividad a este candidato»: un paso del catálogo SOLO para esta postulación (entrevista, prueba,
 * documentos…). Por defecto no es obligatorio: no frena el avance salvo que RH lo marque. */
export function ModalActividad({ c, etapaActual, onClose, onAgregada }: {
  c: Candidato;
  etapaActual: EtapaCandidato;
  onClose: () => void;
  onAgregada: (r: { proceso: Seg; candidato: Candidato; paso: { nombre: string } }) => void;
}) {
  const [opciones, setOpciones] = useState<OpcionesProceso | null>(null);
  const [tipo, setTipo] = useState("");
  const [nombre, setNombre] = useState("");
  const [etapa, setEtapa] = useState<EtapaCandidato>(etapaActual);
  const [obligatorio, setObligatorio] = useState(false);
  const [error, setError] = useState("");
  const [guardando, setGuardando] = useState(false);
  useEffect(() => {
    fetchOpcionesProceso().then((o) => setOpciones(o ?? null));
  }, []);
  const tipos = (opciones?.tiposPaso ?? []).filter((t) => !["solicitud_web", "prefiltro_web", "prefiltro_whatsapp", "alta"].includes(t.valor));
  const elegido = tipos.find((t) => t.valor === tipo);
  const etapas = (opciones?.etapas ?? []).filter((e) => elegido?.etapas.includes(e.valor));

  function elegir(valor: string) {
    const t = tipos.find((x) => x.valor === valor);
    setTipo(valor);
    setNombre(t?.texto ?? "");
    if (t) setEtapa(t.etapas.includes(etapaActual) ? etapaActual : t.etapas[t.etapas.length - 1]);
  }

  async function guardar() {
    if (!tipo) return setError("Elige la actividad del catálogo.");
    setGuardando(true);
    setError("");
    const r = await agregarActividadProceso(c.id, { tipo, nombre: nombre.trim() || undefined, etapa, obligatorio });
    setGuardando(false);
    if (!r.ok) return setError(r.error);
    onAgregada(r.data);
  }

  return (
    <ModalMarco titulo="Agregar actividad a este candidato" subtitulo="Solo para esta postulación: la plantilla y la vacante no cambian." onClose={onClose}>
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-xs text-ink-2">
          Actividad del catálogo
          <select className={cn(inputRH, "h-10")} value={tipo} onChange={(e) => elegir(e.target.value)}>
            <option value="">Elegir…</option>
            {tipos.map((t) => <option key={t.valor} value={t.valor}>{t.texto}</option>)}
          </select>
        </label>
        {elegido && (
          <>
            <label className="flex flex-col gap-1 text-xs text-ink-2">
              Nombre
              <input className={inputRH} value={nombre} onChange={(e) => setNombre(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-ink-2">
              Etapa
              <select className={cn(inputRH, "h-10")} value={etapa} onChange={(e) => setEtapa(e.target.value as EtapaCandidato)}>
                {etapas.map((e) => <option key={e.valor} value={e.valor}>{e.texto}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="h-4 w-4 rounded accent-brand" checked={obligatorio} onChange={(e) => setObligatorio(e.target.checked)} />
              Obligatoria (el candidato no avanza de etapa sin cumplirla)
            </label>
          </>
        )}
        {error && <p className="rounded-xl border border-bad/40 bg-bad-soft px-3 py-2 text-sm font-semibold text-bad">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose} disabled={guardando}>Cancelar</Button>
          <Button size="sm" onClick={guardar} disabled={guardando || !tipo}>{guardando ? "Agregando…" : "Agregar actividad"}</Button>
        </div>
      </div>
    </ModalMarco>
  );
}
