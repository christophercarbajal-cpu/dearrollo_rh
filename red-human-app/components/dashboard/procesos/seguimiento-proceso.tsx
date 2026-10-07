"use client";

/* Ficha del candidato → «Resumen»: la RUTA completa del candidato (2026-10-06), de la solicitud al «Alta como
   colaborador». Arriba: la etapa actual y la SIGUIENTE acción principal. Abajo: todos los pasos agrupados por etapa con
   Nombre, Responsable, Estado (+ qué falta / bloqueo), Resultado y la Acción disponible. Lo que está en espera dice
   exactamente qué falta y nunca frena a las demás actividades en paralelo. Las acciones REUTILIZAN lo que ya existe:
   «Agregar evaluación» precargada, las tarjetas de evaluación, el chat, el CV, la liga de documentos o el expediente;
   al terminar, la API recalcula estado, resultado y siguiente acción en esta misma vista. «Agregar actividad» suma un
   paso SOLO a este candidato (la plantilla y la vacante no cambian). */

import { useEffect, useState } from "react";
import { AlertTriangle, ArrowRight, Ban, CheckCircle2, Clock, Link2, Play, Plus, RefreshCw, RotateCcw, SkipForward, Zap } from "lucide-react";
import { Badge, Button, Card, Eyebrow } from "@/components/ui";
import { MenuAcciones } from "@/components/dashboard/menu-acciones";
import { ModalMarco, inputRH } from "@/components/dashboard/modulos-rh";
import { useSesion } from "@/components/sesion";
import {
  agregarActividadProceso, aplicarProcesoVigente, cancelarPasoProceso, fetchOpcionesProceso, fetchSeguimiento, moverEtapaCandidato, nombreEtapa,
  omitirPasoProceso, reactivarPasoProceso, sincronizarEvaluacion, type OpcionesProceso,
} from "@/lib/api";
import type { AccionPaso, Candidato, EstadoPaso, EtapaCandidato, PasoSeguimiento, ResultadoPaso, SeguimientoProceso } from "@/lib/data";
import { textoDia } from "@/lib/fechas";
import { cn } from "@/lib/utils";

const TONO_ESTADO: Record<EstadoPaso, "neutral" | "brand" | "good" | "warn"> = {
  pendiente: "neutral", en_curso: "brand", completada: "good", omitida: "warn", cancelada: "neutral",
};
const TONO_RESULTADO: Record<ResultadoPaso, "good" | "warn" | "bad"> = { favorable: "good", con_observaciones: "warn", no_favorable: "bad" };

export type PresetPaso = { tipo: string; pasoId: string; titulo: string; usuarioId?: number | null };
type Pestana = NonNullable<AccionPaso["pestana"]>;

export function SeguimientoProceso({ c, live, version, onCambio, onIniciarEvaluacion, onAbrir, onSolicitarDocumentos, setAviso }: {
  c: Candidato;
  live: boolean;
  version: number;
  onCambio: (c: Candidato) => void;
  onIniciarEvaluacion: (p: PresetPaso) => void;
  onAbrir: (pestana: Pestana) => void;
  onSolicitarDocumentos: () => void;
  setAviso: (a: { tono: "ok" | "error" | "warn"; texto: string } | null) => void;
}) {
  const puedeAutorizar = Boolean(useSesion().usuario?.puedeAutorizarOmisiones);
  const [seg, setSeg] = useState<SeguimientoProceso | null>(c.proceso ?? null);
  const [ocupado, setOcupado] = useState("");
  const [decision, setDecision] = useState<{ tipo: "omitir" | "cancelar" | "avanzar"; paso?: PasoSeguimiento; motivo: string } | null>(null);
  const [actividad, setActividad] = useState(false);

  useEffect(() => {
    if (c.proceso) setSeg(c.proceso);
    fetchSeguimiento(c.id).then((s) => s && setSeg(s));
  }, [c, version]);

  if (!seg) return null;
  if (!seg.tieneProceso) {
    return seg.vacanteTieneProceso ? (
      <Card className="p-4 text-sm text-ink-2">
        Esta postulación entró antes de que la vacante tuviera proceso. {live && (
          <button className="font-semibold text-brand hover:underline" onClick={aplicarVigente}>Aplicar el proceso de la vacante</button>
        )}
      </Card>
    ) : null;
  }

  async function aplicarVigente() {
    setOcupado("vigente");
    const r = await aplicarProcesoVigente(c.id);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setSeg(r.data.proceso);
    onCambio(r.data.candidato);
    const h = r.data.aplicado.heredados;
    setAviso({ tono: "ok", texto: `Proceso actualizado a la versión ${r.data.aplicado.version}.${h.length ? ` Se conservan con su actividad: ${h.join(", ")}.` : ""}` });
  }

  function ejecutar(paso: PasoSeguimiento | undefined, accion: AccionPaso | null | undefined) {
    if (!accion) return;
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

  async function avanzar(etapa: EtapaCandidato, omitir = false, motivo = "") {
    setOcupado("avanzar");
    const r = await moverEtapaCandidato(c.id, etapa, motivo, false, false, omitir);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setDecision(null);
    onCambio(r.data);
    setAviso({ tono: "ok", texto: `Avanzó a ${nombreEtapa(etapa)}.${omitir ? " Los pasos obligatorios pendientes quedaron «Omitida» con tu autorización." : ""}` });
  }

  async function confirmarDecision() {
    if (!decision) return;
    if (decision.tipo === "avanzar") return avanzar(seg!.siguienteEtapa as EtapaCandidato, true, decision.motivo);
    if (!decision.paso) return;
    setOcupado("decision");
    const fn = decision.tipo === "omitir" ? omitirPasoProceso : cancelarPasoProceso;
    const r = await fn(c.id, decision.paso.id, decision.motivo);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setSeg(r.data.proceso);
    onCambio(r.data.candidato);
    setDecision(null);
    setAviso({ tono: "ok", texto: `«${decision.paso.nombre}» quedó ${decision.tipo === "omitir" ? "omitido" : "cancelado"}.` });
  }

  /** Telegram (2026-10-06): RH comparte la liga; el candidato entra al chat (o directo a ese paso) desde su número. */
  async function copiarLigaTelegram(liga: string, paso?: PasoSeguimiento) {
    try {
      await navigator.clipboard.writeText(liga);
      setAviso({ tono: "ok", texto: `Liga de Telegram copiada${paso ? ` (paso «${paso.nombre}»)` : ""}. Solo funciona desde el Telegram con el número del candidato.` });
    } catch {
      setAviso({ tono: "warn", texto: `Copia la liga: ${liga}` });
    }
  }

  /** Psicométricas.mx sin webhook (desarrollo): consulta su API, trae JSON + PDF y el paso queda Completado aquí mismo. */
  async function sincronizar(paso: PasoSeguimiento) {
    if (!paso.evaluacion) return;
    setOcupado(`sync-${paso.id}`);
    const r = await sincronizarEvaluacion(paso.evaluacion);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    const s = await fetchSeguimiento(c.id);
    if (s) setSeg(s);
    const estado = r.data.sincronizacion;
    setAviso(estado === "resultado_recibido"
      ? { tono: "ok", texto: `Resultado de «${paso.nombre}» recibido (JSON y PDF). El paso quedó Completado.` }
      : { tono: "warn", texto: `Psicométricas.mx todavía no reporta «${paso.nombre}» como terminada. Vuelve a sincronizar cuando el candidato la conteste.` });
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
  const obligatorioPendiente = (seg.etapas ?? []).find((e) => e.actual)?.faltantes.length;
  const requiereAutorizacion = decision && (decision.tipo === "avanzar" || decision.paso?.obligatorio);
  const tg = seg.telegram?.disponible ? seg.telegram : null;
  const accionesCabecera = [
    ...(seg.siguienteEtapa && obligatorioPendiente ? [{
      etiqueta: `Avanzar a ${seg.siguienteEtapaTexto} omitiendo obligatorios…`, icono: <SkipForward />,
      onClick: () => setDecision({ tipo: "avanzar", motivo: "" }),
    }] : []),
    ...(tg?.liga ? [{ etiqueta: "Copiar liga de Telegram", icono: <Link2 />, onClick: () => copiarLigaTelegram(tg.liga) }] : []),
    ...(c.activa !== false ? [{ etiqueta: "Agregar actividad a este candidato…", icono: <Plus />, onClick: () => setActividad(true) }] : []),
  ];
  const ORIGEN: Record<string, string> = { vacante: "ruta de la vacante", cuenta: "ruta predeterminada de la Cuenta", base: "ruta de respaldo" };

  return (
    <div className="flex flex-col gap-4">
      {/* Arriba: etapa actual + siguiente acción principal */}
      <Card className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <Eyebrow>Ruta del candidato</Eyebrow>
            <p className="mt-1 font-display text-lg font-bold">
              {seg.etapaTexto}
              {seg.siguienteEtapaTexto && <span className="text-sm font-normal text-ink-3"> · después: {seg.siguienteEtapaTexto}</span>}
            </p>
            <p className="text-[11px] text-ink-3">
              {seg.plantilla || "Proceso propio"}{seg.personalizado ? " (personalizado en la vacante)" : ""} · {ORIGEN[seg.origen ?? "vacante"]} · versión {seg.version}
            </p>
          </div>
          {live && sig && (
            <div className="flex items-center gap-2">
              {sig.tipo === "avanzar" && sig.etapa ? (
                <Button size="sm" onClick={() => avanzar(sig.etapa!)} disabled={Boolean(ocupado) || c.activa === false}>
                  <ArrowRight className="h-4 w-4" /> {sig.texto}
                </Button>
              ) : (sig.tipo === "paso" || sig.tipo === "abrir") && sig.accion ? (
                <Button size="sm" onClick={() => ejecutar(pasoSig, sig.accion)} disabled={Boolean(ocupado)}>
                  <Play className="h-4 w-4" /> {sig.texto}
                </Button>
              ) : null}
              {accionesCabecera.length > 0 && <MenuAcciones acciones={accionesCabecera} />}
            </div>
          )}
        </div>
        {sig && (sig.tipo === "esperar" || sig.tipo === "fin" || sig.tipo === "cerrada" || sig.detalle) && (
          <p className={cn("mt-3 flex items-start gap-2 rounded-xl px-3 py-2 text-[13px]",
            sig.tipo === "esperar" ? "bg-warn-soft/50 text-ink-2" : "bg-surface-2 text-ink-2")}>
            {sig.tipo === "fin" ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-good" /> : <Clock className="mt-0.5 h-4 w-4 shrink-0 text-warn" />}
            <span>{sig.tipo !== "paso" && sig.tipo !== "avanzar" && sig.tipo !== "abrir" ? <b>{sig.texto}. </b> : null}{sig.detalle}</span>
          </p>
        )}
        {(seg.alertas ?? []).length > 0 && (
          <ul className="mt-3 flex flex-col gap-1">
            {seg.alertas!.map((a) => (
              <li key={a.paso} className="flex items-center gap-2 text-[12px] font-semibold text-bad">
                <AlertTriangle className="h-3.5 w-3.5" /> {a.texto}{a.fechaLimite ? ` (límite ${textoDia(a.fechaLimite)})` : ""} · solo alerta, no descarta
              </li>
            ))}
          </ul>
        )}
        {seg.desactualizado && (
          <p className="mt-3 text-[12px] text-ink-3">
            {seg.origen === "vacante" ? "La vacante tiene una versión más nueva del proceso" : "La vacante ahora tiene su propio proceso"}; este candidato conserva su ruta.{" "}
            {live && <button className="font-semibold text-brand hover:underline" onClick={aplicarVigente} disabled={Boolean(ocupado)}>Aplicar versión vigente</button>}
          </p>
        )}
      </Card>

      {/* Todos los pasos, agrupados por etapa */}
      {(seg.etapas ?? []).map((e) => (
        <section key={e.etapa} className={cn("rounded-2xl border", e.actual ? "border-brand/40" : "border-border-soft")}>
          <header className="flex flex-wrap items-center gap-2 border-b border-border-faint px-4 py-2.5">
            <h4 className="text-sm font-bold">{e.texto}</h4>
            {e.actual && <Badge tone="brand">Etapa actual</Badge>}
            {e.avanceAutomatico && <Badge tone="human"><Zap className="mr-1 inline h-3 w-3" />Avance automático</Badge>}
            {e.sinPasos ? <span className="text-[11px] text-ink-3">Sin pasos · no bloquea</span>
              : e.lista ? <Badge tone="good">Obligatorios cumplidos</Badge>
              : <span className="text-[11px] text-warn">Faltan {e.faltantes.length} obligatorio(s)</span>}
          </header>
          {e.pasos.length > 0 && (
            <div className="divide-y divide-border-faint">
              <div className="hidden px-4 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-ink-3 sm:grid sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1.2fr)_auto] sm:gap-3">
                <span>Paso</span><span>Responsable</span><span>Estado</span><span>Resultado</span><span>Acción</span>
              </div>
              {e.pasos.map((p) => (
                <div key={p.id} className="grid gap-1.5 px-4 py-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1.2fr)_auto] sm:items-start sm:gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold">
                      {p.nombre}
                      {p.obligatorio && <span className="ml-1.5 font-mono text-[9px] font-bold uppercase tracking-wide text-ink-3">obligatorio</span>}
                      {p.heredado && <span className="ml-1.5 font-mono text-[9px] font-bold uppercase tracking-wide text-ink-3">fuera del proceso vigente</span>}
                      {p.adhoc && <span className="ml-1.5 font-mono text-[9px] font-bold uppercase tracking-wide text-human">solo este candidato</span>}
                    </p>
                    <p className="text-[11px] text-ink-3">
                      {p.reglaTexto}
                      {p.dependeDe.length > 0 ? ` · espera a ${p.dependeDe.map((d) => pasos.find((x) => x.id === d)?.nombre ?? d).join(", ")}` : " · en paralelo"}
                      {p.plazoDias != null && ` · plazo ${p.plazoDias} día(s)`}
                    </p>
                  </div>
                  <p className="text-[13px] text-ink-2"><span className="text-[11px] text-ink-3 sm:hidden">Responsable: </span>{p.responsable}</p>
                  <div className="flex flex-col gap-1">
                    <span className="flex flex-wrap items-center gap-1">
                      <Badge tone={TONO_ESTADO[p.estado]}>{p.estadoTexto}</Badge>
                      {p.vencido && <Badge tone="bad">Plazo vencido</Badge>}
                    </span>
                    {p.espera && <span className="text-[11px] font-medium text-warn">{p.espera}</span>}
                    {p.decision && <span className="text-[11px] text-ink-3">{p.detalle}</span>}
                  </div>
                  <div className="flex flex-col gap-1">
                    {p.resultado ? <Badge tone={TONO_RESULTADO[p.resultado]}>{p.resultadoTexto}</Badge>
                      : <span className="text-[12px] text-ink-3">{p.detalle && !p.decision ? p.detalle : "—"}</span>}
                    {p.resultado && p.detalle && <span className="text-[11px] text-ink-3">{p.detalle}</span>}
                    {p.revisadoPor && p.estado === "completada" && (
                      <span className={cn("text-[11px]", p.revisadoPor.startsWith("Revisado") ? "text-ink-2" : "text-warn")}>{p.revisadoPor}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-1 sm:justify-end">
                    {live && p.sincronizable && (
                      <Button size="sm" variant="outline" onClick={() => sincronizar(p)} disabled={Boolean(ocupado)}>
                        <RefreshCw className={cn("h-3.5 w-3.5", ocupado === `sync-${p.id}` && "animate-spin")} /> Sincronizar resultado
                      </Button>
                    )}
                    {live && p.accion && (
                      <Button size="sm" variant={p.accion.clave === "iniciar_evaluacion" || p.accion.clave === "abrir" ? "primary" : "outline"}
                        onClick={() => ejecutar(p, p.accion)} disabled={Boolean(ocupado) || c.activa === false}>
                        {p.accion.texto}
                      </Button>
                    )}
                    {live && (p.estado === "pendiente" || p.estado === "en_curso") && !p.heredado && (
                      <MenuAcciones acciones={[
                        ...(tg?.pasos[p.id] ? [{ etiqueta: "Copiar liga de Telegram de este paso", icono: <Link2 />, onClick: () => copiarLigaTelegram(tg.pasos[p.id], p) }] : []),
                        { etiqueta: "Omitir paso…", icono: <SkipForward />, onClick: () => setDecision({ tipo: "omitir", paso: p, motivo: "" }) },
                        { etiqueta: "Cancelar paso…", icono: <Ban />, peligrosa: true, onClick: () => setDecision({ tipo: "cancelar", paso: p, motivo: "" }) },
                      ]} />
                    )}
                    {live && (p.estado === "omitida" || p.estado === "cancelada") && (
                      <Button size="sm" variant="ghost" onClick={() => reactivar(p)}><RotateCcw className="h-3.5 w-3.5" /> Reactivar</Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      ))}

      {actividad && (
        <ModalActividad
          c={c}
          etapaActual={c.etapa}
          onClose={() => setActividad(false)}
          onAgregada={(r) => {
            setActividad(false);
            setSeg(r.proceso);
            onCambio(r.candidato);
            setAviso({ tono: "ok", texto: `«${r.paso.nombre}» se agregó solo a este candidato. La plantilla y la vacante no cambian.` });
          }}
        />
      )}

      {decision && (
        <ModalMarco
          titulo={decision.tipo === "avanzar" ? `Avanzar a ${seg.siguienteEtapaTexto} omitiendo obligatorios`
            : `${decision.tipo === "omitir" ? "Omitir" : "Cancelar"} «${decision.paso?.nombre}»`}
          subtitulo={decision.tipo === "avanzar"
            ? `Quedarán «Omitida»: ${(seg.etapas ?? []).find((x) => x.actual)?.faltantes.join("; ")}.`
            : decision.paso?.obligatorio ? "Es un paso obligatorio: requiere justificación y autorización." : "Es un paso opcional."}
          onClose={() => setDecision(null)}
        >
          <div className="flex flex-col gap-3">
            <textarea
              className={cn(inputRH, "h-24 py-2")}
              value={decision.motivo}
              onChange={(ev) => setDecision({ ...decision, motivo: ev.target.value })}
              placeholder="Justificación (queda en el historial y la bitácora con tu nombre)"
            />
            {requiereAutorizacion && !puedeAutorizar && (
              <p className="rounded-xl border border-warn/40 bg-warn-soft px-3 py-2 text-[12px] text-ink-2">
                No tienes el permiso «Autorizar omisiones». Pídelo a un Administrador (Configuración → Usuarios).
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setDecision(null)}>Cancelar</Button>
              <Button size="sm" onClick={confirmarDecision}
                disabled={Boolean(ocupado) || Boolean(requiereAutorizacion && (!puedeAutorizar || decision.motivo.trim().length < 10))}>
                {ocupado ? "Guardando…" : decision.tipo === "avanzar" ? "Autorizar y avanzar" : decision.tipo === "omitir" ? "Omitir paso" : "Cancelar paso"}
              </Button>
            </div>
          </div>
        </ModalMarco>
      )}
    </div>
  );
}

/** «Agregar actividad a este candidato»: un paso del catálogo SOLO para esta postulación (entrevista, prueba,
 * documentos…). Por defecto no es obligatorio: no frena el avance salvo que RH lo marque. */
function ModalActividad({ c, etapaActual, onClose, onAgregada }: {
  c: Candidato;
  etapaActual: EtapaCandidato;
  onClose: () => void;
  onAgregada: (r: { proceso: SeguimientoProceso; candidato: Candidato; paso: { nombre: string } }) => void;
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
