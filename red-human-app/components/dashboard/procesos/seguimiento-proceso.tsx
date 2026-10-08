"use client";

/* Ficha del candidato → «Resumen» minimalista (UX 2026-10-07). La ETAPA la define la ruta (motor de avance del backend:
   compuerta + avance automático); aquí no hay botones para mover de etapa. Orden fijo:
     1. Recomendación + UN ÚNICO botón principal = la siguiente acción concreta de la ruta (`siguienteAccion` de la API).
     2. Resultados clave (un dato, un lugar).
     3. Avance de la ruta: una línea por actividad con UNA sola etiqueta de estado (`estadoUnificado`). 2026-10-08:
        «Enviada» ya no existe; la etiqueta dice a QUIÉN espera (Esperando candidato / referencias / consentimiento /
        médico·entrevistador·evaluador, Pendiente de revisión) y el detalle muestra cada envío por destinatario
        (Intento · Enviado · Entregado · Fallido). Ninguna fila tiene botones: TODO va al «…» de la actividad con nombres
        estandarizados (la acción propia, Abrir liga, Copiar liga, Reenviar al candidato / al médico / al entrevistador,
        Registrar resultado, Reintentar sincronización, Omitir actividad, Reactivar), armado por la API (`menu`) para que
        Resumen, Ruta y Acción principal lean la MISMA fuente. La única acción visible es el botón principal de arriba.
   2026-10-08 (ruta automática, solo demo-grupak): «Descarte sugerido» (RH confirma el descarte; nunca es automático) y
   «Revisar prefiltro» (RH aprueba) arriba, junto a la recomendación.
     4. Fortalezas (máx. 3) y Puntos por validar (máx. 3).
   Las acciones REUTILIZAN lo que ya existe: «Agregar evaluación» precargada, las tarjetas de evaluación, el chat, los
   documentos o el expediente. Cambiar ruta / agregar actividad / descartar / eliminar viven en el «…» del encabezado.
   2026-10-07 (red-human-psicometria.md): en las Cuentas con flujo simple de psicometría (`usePsicometriaSimple`, hoy solo
   «demo-grupak») las filas psicométricas muestran SOLO Sin enviar / Esperando candidato / Completada; su acción (Enviar prueba /
   Reenviar / Ver resultado) vive en el «…» y un bloqueo de avance por psicometría lo dice claro. */

import { useEffect, useState } from "react";
import {
  AlertTriangle, ArrowRight, CheckCircle2, ChevronDown, ClipboardCheck, Clock, Copy, ExternalLink, FileText, Play, RefreshCw, RotateCcw, Send,
  SkipForward, Sparkles, ThumbsDown, XCircle,
} from "lucide-react";
import { MenuAcciones, type AccionMenu } from "@/components/dashboard/menu-acciones";
import { ModalAgregarActividad } from "@/components/dashboard/procesos/agregar-actividad";
import { Badge, Button, Card, Eyebrow } from "@/components/ui";
import { ModalMarco, inputRH } from "@/components/dashboard/modulos-rh";
import { BadgeIntegral } from "@/components/dashboard/evaluaciones/resultado-integral";
import { usePsicometriaSimple, useSesion } from "@/components/sesion";
import {
  DetalleFilaPsicometria, EstadoFilaPsicometria, ModalActividadSimple, ModalEnviarPrueba, textoAccionPsicometria,
  urlResultadoPsicometria,
} from "@/components/dashboard/evaluaciones/psicometria-simple";
import { ModalAccionTarea } from "@/components/dashboard/onboarding/accion-tarea";
import { PanelTareasOnboarding } from "@/components/dashboard/onboarding/tareas-onboarding";
import {
  cerrarOnboarding, fetchCandidato, fetchTareasOnboarding, type TareaOnboarding,
  actualizarContactoCandidato, agregarActividadProceso, aprobarPrefiltro, esCorreoValido, excepcionRHPaso, fetchEntrevistadores, fetchOpcionesProceso, fetchSeguimiento, iniciarActividad,
  moverEtapaCandidato, nombreEtapa, omitirPasoProceso, ordenEtapa, reactivarPasoProceso, reenviarActividad, registrarResultadoActividad,
  sincronizarEvaluacion, type Entrevistador, type OpcionesProceso, type RespuestaIniciar,
} from "@/lib/api";
import type { AccionPaso, Candidato, EnvioDestinatario, EstadoUnificado, EtapaCandidato, ItemMenuPaso, PasoSeguimiento, SeguimientoProceso as Seg } from "@/lib/data";
import { textoDia, textoFechaHora } from "@/lib/fechas";
import { cn } from "@/lib/utils";

/** Una etiqueta por actividad (vocabulario único, 2026-10-08): dice a QUIÉN espera, nunca «Enviada». */
export const TONO_ESTADO_U: Record<EstadoUnificado, "neutral" | "brand" | "human" | "good" | "warn" | "bad"> = {
  sin_iniciar: "neutral", esperando_candidato: "human", esperando_referencias: "human", esperando_consentimiento: "human",
  esperando_evaluador: "human", pendiente_resultado: "warn", en_curso: "brand", pendiente_revision: "warn", completada: "good",
  aprobada: "good", no_aprobada: "bad", omitida: "neutral", error: "bad", aprobada_excepcion: "warn", falta_correo: "warn",
  lista_para_iniciar: "brand",
};
const TEXTO_DESTINATARIO: Record<string, string> = {
  candidato: "Candidato", medico: "Médico", entrevistador: "Entrevistador", evaluador: "Evaluador", rh: "RH", cliente: "Cliente",
};
const TONO_ENVIO: Record<EnvioDestinatario["estado"], string> = { intento: "text-ink-3", enviado: "text-human", entregado: "text-good", fallido: "text-bad" };

export type PresetPaso = { tipo: string; pasoId: string; titulo: string; usuarioId?: number | null };
type Pestana = NonNullable<AccionPaso["pestana"]>;

const TONO_RECOMENDACION: Record<string, { texto: string; icon: typeof CheckCircle2 }> = {
  "Avanzar a contratación": { texto: "text-good", icon: CheckCircle2 },
  "Realizar entrevista humana": { texto: "text-warn", icon: Sparkles },
  "Realizar Entrevista Red Human": { texto: "text-brand", icon: Sparkles },
  "Reintentar Entrevista Red Human": { texto: "text-warn", icon: RotateCcw },
  "No avanzar": { texto: "text-bad", icon: XCircle },
};

export function SeguimientoProceso({ c, live, version, onCambio, onIniciarEvaluacion, onAbrir, onSolicitarDocumentos, onAlta, onDescartar, onSeg, setAviso }: {
  c: Candidato;
  live: boolean;
  version: number;
  onCambio: (c: Candidato) => void;
  onIniciarEvaluacion: (p: PresetPaso) => void;
  onAbrir: (pestana: Pestana) => void;
  onSolicitarDocumentos: () => void;
  /** «Alta como colaborador»: abre la confirmación del alta (la misma de siempre). */
  onAlta?: () => void;
  /** «Confirmar descarte» (ruta automática): abre el descarte de siempre con el motivo sugerido precargado. */
  onDescartar?: (motivo: string) => void;
  /** El encabezado de la ficha usa el seguimiento para su menú «…» (cambiar ruta, liga de Telegram). */
  onSeg?: (s: Seg) => void;
  setAviso: (a: { tono: "ok" | "error" | "warn"; texto: string } | null) => void;
}) {
  const puedeAutorizar = Boolean(useSesion().usuario?.puedeAutorizarOmisiones);
  const simple = usePsicometriaSimple();
  const [enviarPrueba, setEnviarPrueba] = useState<PasoSeguimiento | null>(null);
  const [bloqueoPsico, setBloqueoPsico] = useState<PasoSeguimiento | null>(null);
  const [seg, setSegLocal] = useState<Seg | null>(c.proceso ?? null);
  const [ocupado, setOcupado] = useState("");
  const [abierto, setAbierto] = useState<string | null>(null);
  // «Omitir actividad» unifica Omitir y Cancelar (2026-10-08): motivo obligatorio; recalcula el avance sin borrar nada.
  const [decision, setDecision] = useState<{ tipo: "omitir"; paso: PasoSeguimiento; motivo: string } | null>(null);
  // «Iniciar» en un paso: si la API dice que falta un dato crítico, se pide SOLO ese dato
  const [faltan, setFaltan] = useState<{ paso: PasoSeguimiento; faltan: NonNullable<RespuestaIniciar["faltan"]>; mensaje: string } | null>(null);
  const [registrar, setRegistrar] = useState<PasoSeguimiento | null>(null);
  // 2026-10-08: «Agregar correo» (psicometría detenida por falta de correo) y «Continuar por decisión de RH»
  const [pedirCorreo, setPedirCorreo] = useState<PasoSeguimiento | null>(null);
  const [excepcion, setExcepcion] = useState<{ paso: string; nombre: string; detalle: string; motivo: string } | null>(null);
  // 2026-10-08: tareas de Onboarding resueltas DESDE LA FICHA (botón principal o fila)
  const [tareaAbierta, setTareaAbierta] = useState<TareaOnboarding | null>(null);
  const [versionTareas, setVersionTareas] = useState(0);

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
    if (simple && paso?.tipo === "psicometrica" && paso.psicometria && accion.clave === "iniciar_evaluacion") return setEnviarPrueba(paso);
    if (accion.clave === "iniciar_evaluacion" && paso) return void iniciar(paso);
    if (accion.clave === "agregar_correo" && paso) return setPedirCorreo(paso);
    if (accion.clave === "registrar_resultado" && paso) return setRegistrar(paso);
    if (accion.clave === "reenviar" && paso) return void reenviar(paso, accion.a ?? "candidato");
    if (accion.clave === "consultar_evaluacion") return onAbrir("evaluaciones");
    if (accion.clave === "solicitar_documentos") return onSolicitarDocumentos();
    if (accion.clave === "validar_documentos") return onAbrir("documentos");
    if (paso?.tipo === "documentos" && accion.clave === "abrir" && c.etapa === "Contratación") return onSolicitarDocumentos();
    if (accion.pestana) onAbrir(accion.pestana);
  }

  /** Tras resolver una tarea / el alta / el cierre: ruta, ficha y tareas se vuelven a leer (una sola fuente). */
  async function refrescarTodo(mensaje = "") {
    const s = await fetchSeguimiento(c.id);
    if (s) setSeg(s);
    const ficha = await fetchCandidato(c.id);
    if (ficha) onCambio(ficha);
    setVersionTareas((v) => v + 1);
    if (mensaje) setAviso({ tono: "ok", texto: mensaje });
  }

  async function abrirTarea(id: number) {
    if (c.expedienteId == null) return;
    const t = (await fetchTareasOnboarding(c.expedienteId))?.find((x) => x.id === id);
    if (t?.accion) setTareaAbierta(t);
    else void refrescarTodo();
  }

  async function cerrarOnb() {
    if (c.expedienteId == null) return;
    setOcupado("cerrar-onboarding");
    const r = await cerrarOnboarding(c.expedienteId);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    await refrescarTodo("Onboarding cerrado: el proceso de esta persona terminó.");
  }

  /** «Iniciar» (2026-10-08): ejecuta lo configurado en la ruta en UN paso; si falta un dato crítico se pide solo ese. */
  async function iniciar(paso: PasoSeguimiento, datos: Parameters<typeof iniciarActividad>[2] = {}) {
    setOcupado(`ini-${paso.id}`);
    const r = await iniciarActividad(c.id, paso.id, datos);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setSeg(r.data.proceso);
    onCambio(r.data.candidato);
    if (!r.data.iniciada && r.data.faltan?.length) {
      if (r.data.faltan.includes("pruebas")) {
        // la batería no está configurada: se elige en la vista de psicometría de siempre (selección + envío)
        const rc = paso.responsableConfig;
        return onIniciarEvaluacion({ tipo: paso.tipo, pasoId: paso.id, titulo: `Iniciar: ${paso.nombre}`, usuarioId: rc?.tipo === "usuario" ? rc.usuario_id : null });
      }
      // falta el correo → edición rápida del contacto; al guardarlo la prueba se envía sola (sin duplicar)
      if (r.data.faltan.length === 1 && r.data.faltan[0] === "correo") return setPedirCorreo(paso);
      return setFaltan({ paso, faltan: r.data.faltan, mensaje: r.data.mensaje });
    }
    setFaltan(null);
    const adv = r.data.advertencias?.length ? ` ${r.data.advertencias.join(" ")}` : "";
    setAviso({ tono: r.data.yaExistia || adv ? "warn" : "ok", texto: `${r.data.mensaje}${adv}` });
  }

  /** Reenvío granular: SU liga a ese destinatario; nunca cambia el estado de la actividad. */
  async function reenviar(paso: PasoSeguimiento, a: string) {
    setOcupado(`env-${paso.id}`);
    const r = await reenviarActividad(c.id, paso.id, a);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setSeg(r.data.proceso);
    onCambio(r.data.candidato);
    const quien = (TEXTO_DESTINATARIO[a] ?? a).toLowerCase();
    const ok = (r.data.resultados ?? []).filter((x) => x.enviado).map((x) => x.canal).filter(Boolean);
    setAviso(r.data.enviado
      ? { tono: "ok", texto: `Enviado al ${quien}${ok.length ? ` por ${ok.join(" y ")}` : ""}.` }
      : { tono: "warn", texto: `No salió el envío al ${quien}: ${(r.data.resultados ?? []).map((x) => x.detalle).filter(Boolean).join("; ") || "sin canal disponible"}. Copia la liga y compártela tú.` });
  }

  /** Submenú «…» de la actividad, tal como lo arma la API (sin duplicados ni la acción principal de la ficha). */
  function accionesDe(p: PasoSeguimiento): AccionMenu[] {
    const items: ItemMenuPaso[] = p.menu ?? [];
    const lista: AccionMenu[] = [];
    const ps = simple ? p.psicometria : null;
    for (const m of items) {
      if (m.clave === "accion" && m.accion) {
        const texto = ps && m.accion.clave === "iniciar_evaluacion" ? "Enviar prueba" : m.texto;
        lista.push({ etiqueta: texto, icono: <Play />, onClick: () => ejecutar(p, m.accion), disabled: Boolean(ocupado) || c.activa === false });
      } else if (m.clave === "abrir_liga" && p.liga) {
        lista.push({ etiqueta: m.texto, icono: <ExternalLink />, onClick: () => window.open(p.liga!.url, "_blank", "noopener,noreferrer") });
      } else if (m.clave === "copiar_liga" && p.liga) {
        lista.push({ etiqueta: m.texto, icono: <Copy />, onClick: () => void copiarLiga(p.liga!, p) });
      } else if (m.clave.startsWith("reenviar_") && m.a) {
        lista.push({ etiqueta: m.texto, icono: <Send />, onClick: () => void reenviar(p, m.a!), disabled: Boolean(ocupado) || c.activa === false });
      } else if (m.clave === "registrar_resultado") {
        lista.push({ etiqueta: m.texto, icono: <ClipboardCheck />, onClick: () => setRegistrar(p), disabled: c.activa === false });
      } else if (m.clave === "reintentar_sincronizacion") {
        lista.push({ etiqueta: m.texto, icono: <RefreshCw />, onClick: () => void sincronizar(p), disabled: Boolean(ocupado) });
      } else if (m.clave === "omitir") {
        lista.push({ etiqueta: m.texto, icono: <SkipForward />, onClick: () => setDecision({ tipo: "omitir", paso: p, motivo: "" }), disabled: c.activa === false });
      } else if (m.clave === "reactivar") {
        lista.push({ etiqueta: m.texto, icono: <RotateCcw />, onClick: () => void reactivar(p) });
      } else if (m.clave === "continuar_excepcion") {
        lista.push({ etiqueta: m.texto, icono: <CheckCircle2 />, disabled: c.activa === false,
          onClick: () => setExcepcion({ paso: p.id, nombre: p.nombre, detalle: p.detalle, motivo: "" }) });
      }
    }
    const url = ps?.status === "completada" ? urlResultadoPsicometria(ps) : null;
    if (url) lista.unshift({ etiqueta: "Ver resultado", icono: <FileText />, onClick: () => window.open(url, "_blank", "noopener,noreferrer") });
    return lista;
  }

  async function confirmarExcepcion() {
    if (!excepcion) return;
    setOcupado("excepcion");
    const r = await excepcionRHPaso(c.id, excepcion.paso, excepcion.motivo);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setSeg(r.data.proceso);
    onCambio(r.data.candidato);
    const movida = r.data.candidato.etapa !== c.etapa;
    setExcepcion(null);
    setAviso({ tono: "ok", texto: `«${excepcion.nombre}» continúa por decisión de RH (su resultado se conserva).${movida ? ` La ruta avanzó a ${nombreEtapa(r.data.candidato.etapa)}.` : ""}` });
  }

  /** Solo cuando la ruta lo permite (todos los obligatorios de la etapa cumplidos y su avance automático apagado). */
  async function continuar(etapa: EtapaCandidato) {
    setOcupado("avanzar");
    const r = await moverEtapaCandidato(c.id, etapa);
    setOcupado("");
    if (!r.ok) {
      // flujo simple: un bloqueo por psicometría obligatoria se dice claro y con la acción que lo resuelve
      const psico = simple ? pasos.find((x) => x.tipo === "psicometrica" && x.obligatorio && x.psicometria && x.psicometria.status !== "completada"
        && !x.heredado && x.estado !== "omitida" && x.estado !== "cancelada" && ordenEtapa(x.etapa) < ordenEtapa(etapa)) : undefined;
      if (psico) {
        setBloqueoPsico(psico);
        return setAviso(null);
      }
      return setAviso({ tono: "error", texto: r.error });
    }
    setBloqueoPsico(null);
    onCambio(r.data);
    setAviso({ tono: "ok", texto: `Continúa en ${nombreEtapa(etapa)}.` });
  }

  async function confirmarDecision() {
    if (!decision) return;
    setOcupado("decision");
    const r = await omitirPasoProceso(c.id, decision.paso.id, decision.motivo);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setSeg(r.data.proceso);
    onCambio(r.data.candidato);
    const movida = r.data.candidato.etapa !== c.etapa;
    setDecision(null);
    setAviso({ tono: "ok", texto: `«${decision.paso.nombre}» quedó omitida.${movida ? ` La ruta continúa en ${nombreEtapa(r.data.candidato.etapa)}.` : ""}` });
  }

  /** «Copiar liga»: SOLO copia la URL real de la actividad; nunca la marca como enviada. */
  async function copiarLiga(liga: NonNullable<PasoSeguimiento["liga"]>, paso: PasoSeguimiento) {
    const texto = liga.clave ? `${liga.url}\nClave de acceso: ${liga.clave}` : liga.url;
    try {
      await navigator.clipboard.writeText(texto);
      setAviso({ tono: "ok", texto: `Liga de «${paso.nombre}» copiada.` });
    } catch {
      setAviso({ tono: "warn", texto: `Copia la liga: ${liga.url}` });
    }
  }

  async function aprobarPrefiltroRH() {
    setOcupado("prefiltro");
    const r = await aprobarPrefiltro(c.id);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setSeg(r.data.proceso);
    onCambio(r.data.candidato);
    setAviso({ tono: "ok", texto: "Prefiltro aprobado: la ruta continúa sola." });
  }

  /** «Reintentar sincronización»: solo existe tras una falla CONFIRMADA de recuperación (el resultado llega solo). */
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
      : { tono: "warn", texto: `La plataforma de evaluación todavía no entrega el resultado de «${paso.nombre}»; se reintentará automáticamente.` });
  }

  /** Flujo simple: la acción que resuelve un bloqueo por psicometría (enviar o reenviar al candidato). */
  async function accionPsicometria(paso: PasoSeguimiento) {
    const ps = paso.psicometria;
    if (!ps) return;
    if (ps.status === "sin_enviar") return setEnviarPrueba(paso);
    if (ps.status === "enviada" || ps.status === "error_envio") return void reenviar(paso, "candidato");
    const url = urlResultadoPsicometria(ps);
    if (url) window.open(url, "_blank", "noopener,noreferrer");
    else onAbrir("evaluaciones");
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
  // un solo evaluador de condiciones: la API manda `bloqueo` (todas las Cuentas) y `descarteSugerido` (ruta automática)
  const descarte = seg.bloqueo ?? seg.descarteSugerido ?? null;
  // 2026-10-08: una postulación que el prefiltro conversacional cerró sigue ofreciendo «Continuar por decisión de RH»
  const cerradaPrefiltro = Boolean(descarte?.cerradaPorPrefiltro);
  const vigente = c.activa !== false || cerradaPrefiltro;
  const pasoPrefiltro = pasos.find((p) => p.revisarPrefiltro && p.estado !== "omitida" && p.estado !== "cancelada");
  const recRuta = seg.recomendacion ?? null;
  const rec = recRuta
    ? { texto: recRuta.tono === "bad" ? "text-bad" : recRuta.tono === "warn" ? "text-warn" : "text-good", icon: recRuta.tono === "good" ? CheckCircle2 : AlertTriangle }
    : c.recomendacionRedHuman ? TONO_RECOMENDACION[c.recomendacionRedHuman] : null;
  const RecIcon = rec?.icon ?? Sparkles;

  // UN botón principal: la siguiente acción concreta que define la ruta.
  let principal: { texto: string; onClick: () => void; icono: React.ReactNode } | null = null;
  if (live && sig?.tipo === "cerrar_onboarding" && c.expedienteId != null) {
    // 2026-10-08: con el alta hecha (postulación ya cerrada como «contratado») el último paso es cerrar el Onboarding
    principal = { texto: "Cerrar Onboarding", onClick: () => void cerrarOnb(), icono: <CheckCircle2 className="h-4 w-4" /> };
  } else if (live && vigente && descarte) {
    principal = null; // el bloqueo trae SUS dos decisiones (abajo): Confirmar descarte · Continuar por decisión de RH
  } else if (live && c.activa !== false && pasoPrefiltro) {
    principal = { texto: "Aprobar prefiltro", onClick: () => void aprobarPrefiltroRH(), icono: <CheckCircle2 className="h-4 w-4" /> };
  } else if (live && sig && c.activa !== false) {
    if (sig.tipo === "tarea" && sig.tarea != null) {
      // Onboarding: la tarea pendiente se resuelve aquí mismo (Confirmar ingreso, Registrar alta IMSS…)
      principal = { texto: sig.texto, onClick: () => void abrirTarea(sig.tarea!), icono: <Play className="h-4 w-4" /> };
    } else if (sig.tipo === "alta" && onAlta) {
      principal = { texto: "Dar de alta como colaborador", onClick: onAlta, icono: <CheckCircle2 className="h-4 w-4" /> };
    } else if ((sig.tipo === "paso" || sig.tipo === "abrir") && sig.accion) {
      const enviarPsico = simple && pasoSig?.tipo === "psicometrica" && sig.accion.clave === "iniciar_evaluacion";
      principal = { texto: pasoSig?.tipo === "alta" ? "Dar de alta como colaborador" : enviarPsico ? "Enviar prueba" : sig.texto, onClick: () => ejecutar(pasoSig, sig.accion), icono: <Play className="h-4 w-4" /> };
    } else if (sig.tipo === "avanzar" && sig.etapa) {
      principal = { texto: `Continuar a ${nombreEtapa(sig.etapa)}`, onClick: () => continuar(sig.etapa!), icono: <ArrowRight className="h-4 w-4" /> };
    } else if (sig.tipo === "esperar" && sig.accion && pasoSig?.pendienteAprobacion) {
      principal = { texto: `Aprobar: ${pasoSig.nombre}`, onClick: () => ejecutar(pasoSig, sig.accion), icono: <CheckCircle2 className="h-4 w-4" /> };
    }
  }
  // flujo simple: si la ruta espera una psicométrica obligatoria ya enviada, se dice claro por qué no avanza
  const pasoBloqueo = bloqueoPsico ?? (simple && !principal && pasoSig?.obligatorio && pasoSig.psicometria?.status === "enviada"
    && pasoSig.estado !== "omitida" && pasoSig.estado !== "cancelada" ? pasoSig : null);
  const textoEspera = !principal && !pasoBloqueo && sig ? `${sig.texto}${sig.detalle ? ` — ${sig.detalle}` : ""}` : "";
  /** Actividad automática sin error: solo se muestra su estado (sin botón). */
  const soloEstado = (p: PasoSeguimiento) => Boolean(p.automatica) && !p.error;

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
                {recRuta?.texto || c.recomendacionRedHuman || c.resultadoIntegral?.texto || "Sin recomendación todavía"}
              </p>
              {(recRuta?.motivo || c.recomendacionMotivo || (!c.recomendacionRedHuman && c.resultadoIntegral?.motivo)) && (
                <p className="mt-0.5 line-clamp-2 text-[12px] leading-snug text-ink-2">{recRuta?.motivo || c.recomendacionMotivo || c.resultadoIntegral?.motivo}</p>
              )}
            </div>
          </div>
          {principal && (
            <Button size="sm" className="shrink-0" onClick={principal.onClick} disabled={Boolean(ocupado)}>
              {principal.icono} {principal.texto}
            </Button>
          )}
        </div>
        {descarte && vigente && (
          <div role="alert" className="mt-3 rounded-lg border border-bad/40 bg-bad-soft/50 px-3 py-2">
            <p className="flex items-center gap-2 text-[13px] font-semibold text-bad">
              <XCircle className="h-4 w-4 shrink-0" /> {cerradaPrefiltro ? "Prefiltro no aprobado" : seg.descarteSugerido ? "Descarte sugerido" : "No aprobada"}: {descarte.motivo}
            </p>
            <p className="mt-0.5 pl-6 text-[12px] text-ink-2">
              {cerradaPrefiltro
                ? "La postulación se cerró y se le avisó al candidato. Si RH decide seguir, continúa con un motivo: se reabre, se conservan sus respuestas y la liga de la entrevista sale sola."
                : "La ruta se detuvo. Confirma el descarte o, si RH decide seguir, continúa con un motivo (el resultado se conserva)."}
            </p>
            {live && (
              <div className="mt-2 flex flex-wrap justify-end gap-2">
                {onDescartar && !cerradaPrefiltro && (
                  <Button size="sm" variant="outline" onClick={() => onDescartar(descarte.motivo)} disabled={Boolean(ocupado)}>
                    <ThumbsDown className="h-4 w-4" /> Confirmar descarte
                  </Button>
                )}
                <Button size="sm" onClick={() => setExcepcion({ paso: descarte.paso, nombre: descarte.nombre, detalle: descarte.motivo, motivo: "" })}
                  disabled={Boolean(ocupado)}>
                  <CheckCircle2 className="h-4 w-4" /> Continuar por decisión de RH
                </Button>
              </div>
            )}
          </div>
        )}
        {pasoPrefiltro && !descarte && (
          <p className="mt-3 flex items-start gap-2 rounded-lg border border-warn/40 bg-warn-soft/50 px-3 py-2 text-[12px] text-ink-2">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" />
            <span><b className="text-ink">Revisar prefiltro.</b> {pasoPrefiltro.espera.replace(/^Revisar prefiltro:\s*/, "")} Apruébalo o descarta al candidato desde «…».</span>
          </p>
        )}
        {textoEspera && !descarte && !pasoPrefiltro && (
          <p className={cn("mt-3 flex items-start gap-2 rounded-lg px-2.5 py-1.5 text-[12px]", sig?.tipo === "fin" ? "bg-good-soft/50" : "bg-surface-2", "text-ink-2")}>
            {sig?.tipo === "fin" ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-good" /> : <Clock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" />}
            <span>{textoEspera}</span>
          </p>
        )}
        {pasoBloqueo?.psicometria && (
          <div role="alert" className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warn/40 bg-warn-soft/50 px-3 py-2">
            <p className="flex items-center gap-2 text-[13px] font-semibold text-warn">
              <AlertTriangle className="h-4 w-4 shrink-0" /> No puede avanzar: falta completar la psicométrica.
            </p>
            {live && pasoBloqueo.psicometria.status !== "completada" && (
              <Button size="sm" variant="outline" onClick={() => accionPsicometria(pasoBloqueo)} disabled={Boolean(ocupado)}>
                {textoAccionPsicometria(pasoBloqueo.psicometria)}
              </Button>
            )}
          </div>
        )}
        {(seg.alertas ?? []).length > 0 && (
          <p className="mt-2 flex items-center gap-1.5 text-[11px] font-semibold text-bad">
            <AlertTriangle className="h-3.5 w-3.5" /> {seg.alertas!.map((a) => `${a.texto}${a.fechaLimite ? ` (${textoDia(a.fechaLimite)})` : ""}`).join(" · ")}
          </p>
        )}
      </Card>

      {/* 2. Resultados clave */}
      <ResultadosClave c={c} />

      {/* 2026-10-08: tareas de Onboarding en la ficha, cada una con su acción directa */}
      {c.etapa === "Onboarding" && c.expedienteId != null && (
        <Card className="p-4">
          <Eyebrow>Tareas de Onboarding</Eyebrow>
          <div className="mt-3">
            <PanelTareasOnboarding expedienteId={c.expedienteId} live={live} recargar={versionTareas} onCambio={() => void refrescarTodo()} />
          </div>
        </Card>
      )}
      {tareaAbierta && c.expedienteId != null && (
        <ModalAccionTarea expedienteId={c.expedienteId} tarea={tareaAbierta} onClose={() => setTareaAbierta(null)}
          onHecho={(m) => { setTareaAbierta(null); void refrescarTodo(m); }} />
      )}

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
                  const ps = simple && p.psicometria && p.estado !== "omitida" && p.estado !== "cancelada" && !p.heredado ? p.psicometria : null;
                  const menu = live && !soloEstado(p) ? accionesDe(p) : [];
                  return (
                    <li key={p.id} className="border-t border-border-faint first:border-t-0">
                      <div className="flex items-center gap-2 pr-2 hover:bg-surface-2/60">
                        <button
                          className="flex min-w-0 flex-1 items-center gap-2 py-1.5 pl-4 text-left"
                          onClick={() => setAbierto(ab ? null : p.id)}
                          aria-expanded={ab}
                        >
                          <ChevronDown className={cn("h-3.5 w-3.5 shrink-0 text-ink-3 transition-transform", !ab && "-rotate-90")} />
                          <span className="min-w-0 flex-1 truncate text-[13px]">
                            {p.nombre}
                            {p.condicion && p.condicion !== "opcional" && (
                              <span className="ml-1 text-bad" title={p.condicionTexto}>*</span>
                            )}
                          </span>
                          {ps && ps.status === "error_envio" ? <EstadoFilaPsicometria ps={ps} />
                            : <Badge tone={TONO_ESTADO_U[estadoU] ?? "neutral"}>{p.estadoUnificadoTexto ?? p.estadoTexto}</Badge>}
                        </button>
                        {menu.length > 0 ? (
                          <MenuAcciones acciones={menu} etiqueta={`Acciones de «${p.nombre}»`} className="shrink-0" />
                        ) : <span className="w-8 shrink-0" aria-hidden />}
                      </div>
                      {ps && <div className="px-4 pb-1"><DetalleFilaPsicometria ps={ps} /></div>}
                      {ab && (
                        <div className="flex flex-col gap-2 bg-surface-2/40 px-4 py-2.5 pl-10 text-[12px] text-ink-2">
                          <p>
                            <b className="text-ink">{p.condicionTexto ?? (p.obligatorio ? "Obligatoria" : "Opcional")}</b> · Responsable: {p.responsable || "—"} · {p.reglaTexto}
                            {p.dependeDe.length > 0 ? ` · espera a ${p.dependeDe.map((d) => pasos.find((x) => x.id === d)?.nombre ?? d).join(", ")}` : " · en paralelo"}
                            {p.plazoDias != null && ` · plazo ${p.plazoDias} día(s)`}
                            {p.adhoc && " · solo este candidato"}
                            {p.heredado && " · fuera de la ruta vigente"}
                          </p>
                          {p.espera && <p className={cn("font-medium", p.error ? "text-bad" : "text-warn")}>{p.espera}</p>}
                          {(p.resultadoTexto || p.detalle) && (
                            <p>{p.resultadoTexto ? <b className="text-ink">{p.resultadoTexto}. </b> : null}{p.detalle}{p.revisadoPor && p.estado === "completada" ? ` · ${p.revisadoPor}` : ""}</p>
                          )}
                          {p.vencido && <p className="font-semibold text-bad">Plazo vencido (solo alerta)</p>}
                          {p.liga?.url && <p className="truncate"><b className="text-ink">{p.liga.texto}:</b> {p.liga.url}{p.liga.clave ? ` · clave ${p.liga.clave}` : ""}</p>}
                          <EnviosPorDestinatario envios={p.envios} />
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

      {enviarPrueba && (
        <ModalEnviarPrueba
          c={c}
          paso={enviarPrueba}
          onClose={() => setEnviarPrueba(null)}
          onListo={async (aviso, candidato) => {
            setEnviarPrueba(null);
            setBloqueoPsico(null);
            setAviso(aviso);
            const s = await fetchSeguimiento(c.id);
            if (s) setSeg(s);
            if (candidato) onCambio(candidato);
          }}
        />
      )}

      {faltan && (
        <ModalDatosFaltantes
          c={c}
          paso={faltan.paso}
          faltan={faltan.faltan}
          mensaje={faltan.mensaje}
          ocupado={Boolean(ocupado)}
          onClose={() => setFaltan(null)}
          onEnviar={(datos) => void iniciar(faltan.paso, datos)}
        />
      )}

      {pedirCorreo && (
        <ModalAgregarCorreo
          c={c}
          paso={pedirCorreo}
          onClose={() => setPedirCorreo(null)}
          onListo={async (candidato, reanudadas) => {
            setPedirCorreo(null);
            onCambio(candidato);
            const s = await fetchSeguimiento(c.id);
            if (s) setSeg(s);
            const ok = reanudadas.filter((x) => x.ok);
            const mal = reanudadas.filter((x) => !x.ok);
            setAviso(mal.length
              ? { tono: "warn", texto: `Correo guardado, pero no se pudo retomar: ${mal.map((x) => `${x.nombre} (${x.mensaje})`).join("; ")}` }
              : { tono: "ok", texto: `Correo guardado.${ok.length ? ` Se retomó el envío de ${ok.map((x) => `«${x.nombre}»`).join(", ")}.` : ""}` });
          }}
        />
      )}

      {excepcion && (
        <ModalMarco
          titulo={`Continuar por decisión de RH: ${excepcion.nombre}`}
          subtitulo="El resultado y el score se conservan tal cual; la actividad deja de detener la ruta. Queda en el historial con tu nombre."
          onClose={() => setExcepcion(null)}
        >
          <div className="flex flex-col gap-3">
            {excepcion.detalle && <p className="rounded-lg bg-surface-2 px-3 py-2 text-[12px] text-ink-2">{excepcion.detalle}</p>}
            <textarea
              className={cn(inputRH, "h-24 py-2")}
              value={excepcion.motivo}
              onChange={(ev) => setExcepcion({ ...excepcion, motivo: ev.target.value })}
              placeholder="¿Por qué continúa? (obligatorio, mínimo 10 caracteres)"
            />
            {!puedeAutorizar && (
              <p className="rounded-xl border border-warn/40 bg-warn-soft px-3 py-2 text-[12px] text-ink-2">
                En una actividad obligatoria se requiere el permiso «Autorizar omisiones» (Configuración → Usuarios).
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setExcepcion(null)}>Cancelar</Button>
              <Button size="sm" onClick={confirmarExcepcion} disabled={Boolean(ocupado) || excepcion.motivo.trim().length < 10}>
                {ocupado === "excepcion" ? "Guardando…" : "Continuar"}
              </Button>
            </div>
          </div>
        </ModalMarco>
      )}

      {registrar && (
        <ModalRegistrarResultado
          c={c}
          paso={registrar}
          onClose={() => setRegistrar(null)}
          onListo={(r) => {
            setRegistrar(null);
            setSeg(r.proceso);
            onCambio(r.candidato);
            setAviso({ tono: "ok", texto: `Resultado de «${registrar.nombre}» registrado.` });
          }}
        />
      )}

      {decision && (
        <ModalMarco
          titulo={`Omitir «${decision.paso.nombre}»`}
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
                {ocupado ? "Guardando…" : "Omitir actividad"}
              </Button>
            </div>
          </div>
        </ModalMarco>
      )}
    </div>
  );
}

/** Último envío a cada destinatario de la actividad: Intento · Enviado · Entregado · Fallido (con canal y fecha). */
function EnviosPorDestinatario({ envios }: { envios?: Record<string, EnvioDestinatario> }) {
  const filas = Object.entries(envios ?? {});
  if (!filas.length) return null;
  return (
    <ul className="space-y-0.5">
      {filas.map(([dest, e]) => (
        <li key={dest} className="flex flex-wrap items-baseline gap-x-1.5">
          <b className="text-ink">{TEXTO_DESTINATARIO[dest] ?? dest}{e.nombre && dest !== "candidato" ? ` (${e.nombre})` : ""}:</b>
          <span className={cn("font-semibold", TONO_ENVIO[e.estado])}>{e.estadoTexto}</span>
          {e.fecha && <span className="text-ink-3">· {textoFechaHora(e.fecha)}</span>}
          {e.canales.length > 0 && (
            <span className="text-ink-3">· {e.canales.map((x) => `${x.canal || "sin canal"} ${x.estado === "fallido" ? "✗" : "✓"}`).join(", ")}</span>
          )}
          {e.intentos > 1 && <span className="text-ink-3">· {e.intentos} envíos</span>}
          {e.estado === "fallido" && e.canales.some((x) => x.detalle) && (
            <span className="w-full text-bad">{e.canales.map((x) => x.detalle).filter(Boolean).join("; ")}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** «Iniciar» pidió un dato crítico: se captura SOLO ese (evaluador / correo) y se reintenta sin duplicar nada. */
function ModalDatosFaltantes({ c, paso, faltan, mensaje, ocupado, onClose, onEnviar }: {
  c: Candidato;
  paso: PasoSeguimiento;
  faltan: NonNullable<RespuestaIniciar["faltan"]>;
  mensaje: string;
  ocupado: boolean;
  onClose: () => void;
  onEnviar: (datos: Parameters<typeof iniciarActividad>[2]) => void;
}) {
  const [usuarios, setUsuarios] = useState<Entrevistador[]>([]);
  const [tipo, setTipo] = useState<"interno" | "externo">(paso.tipo === "entrevista_humana" ? "interno" : "externo");
  const [usuarioId, setUsuarioId] = useState<number | "">("");
  const [externo, setExterno] = useState({ nombre: "", correo: "", whatsapp: "" });
  const [correo, setCorreo] = useState(c.correo ?? "");
  const [error, setError] = useState("");
  const pideEvaluador = faltan.includes("evaluador");
  const pideCorreo = faltan.includes("correo");
  const quien = paso.tipo === "medica" ? "Médico" : paso.tipo === "entrevista_humana" ? "Entrevistador" : "Quién la aplica";
  useEffect(() => {
    if (pideEvaluador) fetchEntrevistadores().then((x) => setUsuarios(x ?? []));
  }, [pideEvaluador]);

  function enviar() {
    setError("");
    const datos: Parameters<typeof iniciarActividad>[2] = {};
    if (pideEvaluador) {
      if (tipo === "interno") {
        if (!usuarioId) return setError(`Elige al ${quien.toLowerCase()}.`);
        datos.evaluador = { tipo: "interno", usuario_id: Number(usuarioId) };
      } else {
        if (externo.nombre.trim().length < 3) return setError("Escribe el nombre.");
        if (!externo.correo.trim() && !externo.whatsapp.trim()) return setError("Escribe un correo o un WhatsApp para enviarle su liga.");
        datos.evaluador = { tipo: "externo", nombre: externo.nombre.trim(), correo: externo.correo.trim(), whatsapp: externo.whatsapp.trim() };
      }
    }
    if (pideCorreo) {
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(correo.trim())) return setError("Escribe un correo válido del candidato.");
      datos.correo = correo.trim();
    }
    onEnviar(datos);
  }

  return (
    <ModalMarco titulo="Falta un dato para iniciar" subtitulo="Solo se pide lo que falta; lo demás ya está configurado en la actividad." onClose={onClose}>
      <div className="flex flex-col gap-3">
        {pideEvaluador && (
          <>
            <div className="flex gap-2 text-sm">
              {(["interno", "externo"] as const).map((t) => (
                <label key={t} className="flex items-center gap-1.5">
                  <input type="radio" className="accent-brand" checked={tipo === t} onChange={() => setTipo(t)} />
                  {t === "interno" ? "Usuario de la Cuenta" : "Externo"}
                </label>
              ))}
            </div>
            {tipo === "interno" ? (
              <label className="flex flex-col gap-1 text-xs text-ink-2">
                {quien}
                <select className={cn(inputRH, "h-10")} value={usuarioId} onChange={(e) => setUsuarioId(e.target.value ? Number(e.target.value) : "")}>
                  <option value="">Elegir…</option>
                  {usuarios.map((u) => <option key={u.id} value={u.id}>{u.nombre}</option>)}
                </select>
              </label>
            ) : (
              <div className="grid gap-2 sm:grid-cols-3">
                <input className={inputRH} placeholder="Nombre" value={externo.nombre} onChange={(e) => setExterno({ ...externo, nombre: e.target.value })} />
                <input className={inputRH} placeholder="Correo" value={externo.correo} onChange={(e) => setExterno({ ...externo, correo: e.target.value })} />
                <input className={inputRH} placeholder="WhatsApp (10 dígitos)" value={externo.whatsapp} onChange={(e) => setExterno({ ...externo, whatsapp: e.target.value })} />
              </div>
            )}
          </>
        )}
        {pideCorreo && (
          <label className="flex flex-col gap-1 text-xs text-ink-2">
            Correo del candidato (ahí recibe su prueba)
            <input className={inputRH} type="email" value={correo} onChange={(e) => setCorreo(e.target.value)} />
          </label>
        )}
        {error && <p className="rounded-xl border border-bad/40 bg-bad-soft px-3 py-2 text-sm font-semibold text-bad">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose} disabled={ocupado}>Cancelar</Button>
          <Button size="sm" onClick={enviar} disabled={ocupado}>{ocupado ? "Iniciando…" : "Iniciar"}</Button>
        </div>
      </div>
    </ModalMarco>
  );
}

/** «Agregar correo» (2026-10-08): edición rápida del contacto. Al guardarlo, la API retoma SOLA el envío de la
 * psicometría que se detuvo por falta de correo (sin duplicar). */
function ModalAgregarCorreo({ c, paso, onClose, onListo }: {
  c: Candidato;
  paso: PasoSeguimiento;
  onClose: () => void;
  onListo: (c: Candidato, reanudadas: { paso: string; nombre: string; ok: boolean; mensaje: string }[]) => void;
}) {
  const [correo, setCorreo] = useState(c.correo ?? "");
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState("");

  async function guardar() {
    if (!esCorreoValido(correo.trim())) return setError("Escribe un correo válido.");
    setGuardando(true);
    setError("");
    const r = await actualizarContactoCandidato(c.id, { correo: correo.trim(), reanudar_paso: paso.id });
    setGuardando(false);
    if (!r.ok) return setError(r.error);
    onListo(r.data, r.data.psicometriaReanudada ?? []);
  }

  return (
    <ModalMarco titulo="Agregar correo del candidato" subtitulo={`«${paso.nombre}» necesita un correo; al guardarlo la prueba se envía sola.`} onClose={onClose}>
      <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); void guardar(); }}>
        <label className="flex flex-col gap-1 text-xs text-ink-2">
          Correo
          <input className={inputRH} type="email" inputMode="email" autoFocus value={correo} onChange={(e) => setCorreo(e.target.value)} placeholder="correo@ejemplo.com" />
        </label>
        {error && <p className="rounded-xl border border-bad/40 bg-bad-soft px-3 py-2 text-sm font-semibold text-bad">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={guardando}>Cancelar</Button>
          <Button type="submit" size="sm" disabled={guardando}>{guardando ? "Guardando…" : "Guardar y enviar prueba"}</Button>
        </div>
      </form>
    </ModalMarco>
  );
}

type ContactoVerificado = { nombre: string; empresa: string; telefono: string; dictamen: string; comentario: string };

/** «Registrar resultado» de algo hecho FUERA del sistema: dictamen, quién la aplicó y comentarios (quién captura = tu
 * sesión). Se guarda en la evaluación de la actividad, sin duplicados; un resultado tardío del proveedor no la pisa. */
function ModalRegistrarResultado({ c, paso, onClose, onListo }: {
  c: Candidato;
  paso: PasoSeguimiento;
  onClose: () => void;
  onListo: (r: { proceso: Seg; candidato: Candidato }) => void;
}) {
  const { usuario } = useSesion();
  const [opciones, setOpciones] = useState<{ valor: string; texto: string }[]>([]);
  const [conclusion, setConclusion] = useState("");
  const [realizadaPor, setRealizadaPor] = useState("");
  const [comentarios, setComentarios] = useState("");
  const [archivos, setArchivos] = useState<File[]>([]);
  const [error, setError] = useState("");
  const [guardando, setGuardando] = useState(false);
  const esReferencias = paso.tipo === "referencias";
  const [contactos, setContactos] = useState<ContactoVerificado[]>([]);
  useEffect(() => {
    fetchOpcionesProceso().then((o) => setOpciones(o?.tiposPaso.find((t) => t.valor === paso.tipo)?.dictamenes ?? []));
  }, [paso.tipo]);
  const cambiarContacto = (i: number, k: keyof ContactoVerificado, v: string) =>
    setContactos((xs) => xs.map((x, j) => (j === i ? { ...x, [k]: v } : x)));

  async function guardar() {
    if (paso.tipo === "entrevista_humana" && !conclusion) return setError("Elige la conclusión de la entrevista.");
    const llenos = contactos.filter((x) => x.nombre.trim());
    if (!conclusion && !comentarios.trim() && !archivos.length && !llenos.length) return setError("Elige el dictamen o agrega un comentario, un archivo o los contactos verificados.");
    setGuardando(true);
    setError("");
    const r = await registrarResultadoActividad(c.id, paso.id, {
      conclusion, comentarios, realizadaPor, archivos,
      referencias: llenos.map((x) => ({ nombre: x.nombre.trim(), empresa: x.empresa, telefono: x.telefono, dictamen: x.dictamen, comentario: x.comentario, contactado: true })),
    });
    setGuardando(false);
    if (!r.ok) return setError(r.error);
    onListo(r.data);
  }

  return (
    <ModalMarco titulo={`Registrar resultado: ${paso.nombre}`} subtitulo="Para lo que se hizo fuera del sistema (presencial, por teléfono, en papel)." onClose={onClose}>
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-xs text-ink-2">
          Dictamen
          <select className={cn(inputRH, "h-10")} value={conclusion} onChange={(e) => setConclusion(e.target.value)}>
            <option value="">{paso.tipo === "entrevista_humana" ? "Elegir…" : "Sin dictamen (solo comentario o archivo)"}</option>
            {opciones.map((o) => <option key={o.valor} value={o.valor}>{o.texto}</option>)}
          </select>
        </label>
        {esReferencias && (
          <div className="flex flex-col gap-2 rounded-xl border border-border-soft p-3">
            <p className="text-xs font-semibold text-ink-2">Contactos verificados fuera del sistema (opcional)</p>
            {contactos.map((x, i) => (
              <div key={i} className="grid gap-1.5 sm:grid-cols-2">
                <input className={inputRH} placeholder="Nombre *" value={x.nombre} onChange={(e) => cambiarContacto(i, "nombre", e.target.value)} />
                <input className={inputRH} placeholder="Empresa" value={x.empresa} onChange={(e) => cambiarContacto(i, "empresa", e.target.value)} />
                <input className={inputRH} placeholder="Teléfono" value={x.telefono} onChange={(e) => cambiarContacto(i, "telefono", e.target.value)} />
                <select className={cn(inputRH, "h-10")} value={x.dictamen} onChange={(e) => cambiarContacto(i, "dictamen", e.target.value)}>
                  <option value="">Dictamen…</option>
                  <option value="favorable">Favorable</option>
                  <option value="con_observaciones">Con observaciones</option>
                  <option value="desfavorable">Desfavorable</option>
                </select>
                <input className={cn(inputRH, "sm:col-span-2")} placeholder="Qué comentó" value={x.comentario} onChange={(e) => cambiarContacto(i, "comentario", e.target.value)} />
              </div>
            ))}
            <button type="button" className="self-start text-[12px] font-semibold text-brand hover:underline"
              onClick={() => setContactos((xs) => [...xs, { nombre: "", empresa: "", telefono: "", dictamen: "", comentario: "" }])}>
              + Agregar contacto verificado
            </button>
          </div>
        )}
        <label className="flex flex-col gap-1 text-xs text-ink-2">
          ¿Quién la aplicó?
          <input className={inputRH} value={realizadaPor} onChange={(e) => setRealizadaPor(e.target.value)} placeholder="Nombre de quien la realizó (opcional)" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-2">
          Comentarios
          <textarea className={cn(inputRH, "h-20 py-2")} value={comentarios} onChange={(e) => setComentarios(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-2">
          Archivo (PDF, imagen o Word)
          <input type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.webp,.doc,.docx" onChange={(e) => setArchivos(Array.from(e.target.files ?? []))} />
        </label>
        <p className="text-[12px] text-ink-3">Captura: <b className="text-ink-2">{usuario?.nombre ?? "tu usuario"}</b> · queda en el historial con fecha y hora.</p>
        {error && <p className="rounded-xl border border-bad/40 bg-bad-soft px-3 py-2 text-sm font-semibold text-bad">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose} disabled={guardando}>Cancelar</Button>
          <Button size="sm" onClick={guardar} disabled={guardando}>{guardando ? "Guardando…" : "Registrar resultado"}</Button>
        </div>
      </div>
    </ModalMarco>
  );
}

/** Resultados clave: cada dato UNA vez (prefiltro, CV, entrevista, evaluación integral, expediente). */
function ResultadosClave({ c }: { c: Candidato }) {
  const pf = c.prefiltroResumen;
  const prefiltro = !pf ? "En curso" : pf.resultado === "no_cumple" || pf.incumplidos.length > 0 ? "No cumple" : "Cumple";
  const items: { k: string; v: React.ReactNode; tono?: string }[] = [
    { k: "Prefiltro", v: prefiltro, tono: prefiltro === "Cumple" ? "text-good" : prefiltro === "No cumple" ? "text-bad" : "text-ink-3" },
    ...(c.score != null ? [{ k: "CV", v: `${c.score}/100` }] : []),
    // 2026-10-08: el score de la entrevista es el SUYO (antes se mostraba la afinidad integral CV + entrevista)
    ...(c.scoreEntrevista != null ? [{ k: "Entrevista Red Human", v: `${c.scoreEntrevista}/100` }] : []),
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

type PropsActividad = {
  c: Candidato;
  etapaActual: EtapaCandidato;
  onClose: () => void;
  /** `aviso`: resultado del envío (flujo simple de psicometría); sin él, el aviso de siempre. */
  onAgregada: (r: { proceso: Seg; candidato: Candidato; paso: { nombre: string } }, aviso?: { tono: "ok" | "warn" | "error"; texto: string }) => void;
};

/** «Agregar actividad a este candidato» (2026-10-08, TODAS las Cuentas): formulario DINÁMICO que configura la actividad
 * completa (o la registra ya realizada) en un solo paso. «Iniciar» después ejecuta lo guardado. */
export function ModalActividad({ c, onClose, onAgregada }: PropsActividad) {
  return <ModalAgregarActividad c={c} onClose={onClose} onAgregada={(r, aviso) => onAgregada(r, aviso)} />;
}

/** Modal anterior (solo tipo + nombre + obligatoria). Se conserva como respaldo; ya no se monta. */
export function ModalActividadAnterior(props: PropsActividad) {
  const simple = usePsicometriaSimple();
  return simple ? <ModalActividadSimple {...props} /> : <ModalActividadClasica {...props} />;
}

/** Modal de siempre: un paso del catálogo SOLO para esta postulación (entrevista, prueba, documentos…). Por defecto no
 * es obligatorio: no frena el avance salvo que RH lo marque. */
function ModalActividadClasica({ c, etapaActual, onClose, onAgregada }: PropsActividad) {
  const [opciones, setOpciones] = useState<OpcionesProceso | null>(null);
  const [tipo, setTipo] = useState("");
  const [nombre, setNombre] = useState("");
  const etapa = etapaActual; // 2026-10-08: la actividad adicional va en la etapa ACTUAL; nunca regresa al candidato
  const [obligatorio, setObligatorio] = useState(false);
  const [error, setError] = useState("");
  const [guardando, setGuardando] = useState(false);
  useEffect(() => {
    fetchOpcionesProceso().then((o) => setOpciones(o ?? null));
  }, []);
  const tipos = (opciones?.tiposPaso ?? []).filter((t) => !["solicitud_web", "prefiltro_web", "prefiltro_whatsapp", "alta"].includes(t.valor));
  const elegido = tipos.find((t) => t.valor === tipo);

  function elegir(valor: string) {
    const t = tipos.find((x) => x.valor === valor);
    setTipo(valor);
    setNombre(t?.texto ?? "");
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
            <p className="text-[12px] text-ink-3">Se agrega en la etapa actual: <b className="text-ink-2">{nombreEtapa(etapa)}</b>.</p>
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
