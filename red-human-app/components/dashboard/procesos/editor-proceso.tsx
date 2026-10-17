"use client";

/* Editor ÚNICO del proceso de selección (2026-10-06; catálogo único 2026-10-09). Lo usan Configuración → Procesos de
   selección (plantilla de la Cuenta) y el formulario de vacante (copia personalizable, sin tocar la plantilla). Las
   cinco etapas son fijas; en cada una RH agrega actividades DESDE EL CATÁLOGO, las marca obligatorias u opcionales y
   ajusta SOLO lo propio de cada actividad (CV en la solicitud, guion de la entrevista, batería psicométrica).
   2026-10-09: ya no hay responsable, condición de avance, plazo, «En paralelo / Esperar a…» ni «Avance automático»:
   cada actividad define sola cuándo se cumple y a qué espera, y el candidato avanza solo cuando no quedan
   obligatorias pendientes. Al guardar se mandan los valores del catálogo (la API recalcula las dependencias).
   Cambio 28 (2026-10-12): cada etapa ofrece SOLO su catálogo estricto (`opciones.etapas[].actividades`; «Otra actividad»
   en todas), muestra su descripción fija y ya no hay casilla «Obligatoria»: toda actividad de la ruta es obligatoria. */

import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Plus, Trash2, Zap } from "lucide-react";
import { Badge } from "@/components/ui";
import { inputRH } from "@/components/dashboard/modulos-rh";
import { SelectorPruebas } from "@/components/dashboard/evaluaciones/asignar-psicometria";
import { fetchCursos, fetchPruebasPsicometricas, type Curso, type OpcionesProceso, type OpcionTipoPaso, type PruebaPsicometrica } from "@/lib/api";
import type { EtapaCandidato, EtapasProceso, PasoProceso } from "@/lib/data";
import { aplicarOrdenFijo, claveCatalogo, conFlechas, disponibles as disponiblesEn, insertarSugerido } from "@/lib/orden-proceso";
import { ENFOQUE_DEFAULT, enfoquePaso } from "@/components/dashboard/vacantes/perfil-operativo";
import { cn } from "@/lib/utils";

const selectRH = cn(inputRH, "h-10 pr-8");
/** Tipos con algo propio que configurar (el resto se usa tal cual lo define el catálogo). */
// 2026-10-09: las tres actividades de IA (avatar, WhatsApp, llamada) son independientes y cada una lleva su enfoque
const ENTREVISTAS_IA = new Set(["entrevista_agente", "entrevista_whatsapp", "llamada_agente"]);
// Cambio 28: «Otra actividad» (nombre + mensaje) y «Capacitación» (curso) también se configuran aquí
// Cambio 30: la solicitud ya no tiene casilla «exige CV»: se elige «Solicitud web sin CV» o «con CV» del catálogo
const CON_DETALLE = new Set(["entrevista_humana", "medica", "tecnica", "psicometrica", "otra", "induccion", ...ENTREVISTAS_IA]);  // Cambio 47: médica y técnica = «Quién agenda» y «Misma cita»

function nuevoId(pasos: PasoProceso[], tipo: string): string {
  const base = tipo.replace(/_/g, "-");
  let i = 1;
  let id = base;
  while (pasos.some((p) => p.id === id)) id = `${base}-${++i}`;
  return id;
}

/** Valores del catálogo: responsable y condición los define la actividad; sin plazo ni dependencias capturadas. */
function delCatalogo(p: PasoProceso, t?: OpcionTipoPaso): PasoProceso {
  if (!t) return { ...p, obligatorio: true, depende_de: [], plazo_dias: null };
  return {
    ...p,
    obligatorio: true,
    depende_de: [],
    plazo_dias: null,
    responsable: { tipo: t.responsable },
    regla: t.regla === "calificacion" ? { tipo: "calificacion", minimo: 70 } : { tipo: t.regla },
  };
}

export function pasoNuevo(t: OpcionTipoPaso, etapa: EtapaCandidato, pasos: PasoProceso[]): PasoProceso {
  return delCatalogo({
    id: nuevoId(pasos, t.valor),
    tipo: t.valor,
    nombre: t.valor === "otra" ? "" : t.texto, // Cambio 28: «Otra actividad» exige que RH le ponga nombre
    etapa,
    obligatorio: true,
    depende_de: [],
    responsable: { tipo: t.responsable },
    regla: { tipo: t.regla },
    plazo_dias: null,
    ...(t.valor === "entrevista_humana" ? { tipo_entrevista: "general" }
      // Cambio 43: «Enfoque» por actividad — WhatsApp = Operativo; avatar y llamada = Profesional
      : ENTREVISTAS_IA.has(t.valor) ? { tipo_entrevista: "profesional", enfoque: ENFOQUE_DEFAULT[t.valor] ?? "profesional" } : {}),
  }, t);
}

export function EditorProceso({ pasos, etapas, opciones, onChange, soloLectura = false }: {
  pasos: PasoProceso[];
  etapas: EtapasProceso;
  opciones: OpcionesProceso;
  onChange: (pasos: PasoProceso[], etapas: EtapasProceso) => void;
  soloLectura?: boolean;
}) {
  const [catalogo, setCatalogo] = useState<PruebaPsicometrica[]>([]);
  const [cursos, setCursos] = useState<Curso[]>([]);
  const [abierto, setAbierto] = useState<string | null>(null);
  useEffect(() => {
    fetchPruebasPsicometricas().then((l) => setCatalogo((l ?? []).filter((x) => x.modo === "integrada" && x.activa)));
    fetchCursos().then((l) => setCursos((l ?? []).filter((x) => x.estado === "Publicado")));
  }, []);
  const tipos = useMemo(() => Object.fromEntries(opciones.tiposPaso.map((t) => [t.valor, t])), [opciones]);
  const orden = useMemo(() => opciones.tiposPaso.map((t) => t.valor), [opciones]);

  const emitir = (lista: PasoProceso[]) =>
    onChange(aplicarOrdenFijo(lista, orden).map((p) => delCatalogo(p, tipos[claveCatalogo(p)])), etapas);
  const cambiarPaso = (id: string, cambios: Partial<PasoProceso>) => emitir(pasos.map((p) => (p.id === id ? { ...p, ...cambios } : p)));
  const quitarPaso = (id: string) => emitir(pasos.filter((p) => p.id !== id));
  function mover(id: string, delta: number) {
    const i = pasos.findIndex((p) => p.id === id);
    const mismos = pasos.map((p, k) => ({ p, k })).filter((x) => x.p.etapa === pasos[i].etapa);
    const pos = mismos.findIndex((x) => x.k === i);
    const destino = mismos[pos + delta];
    if (!destino || !conFlechas(pasos[i]) || !conFlechas(destino.p)) return; // Prefiltro y «Contrato y firma» son fijos
    const copia = [...pasos];
    [copia[i], copia[destino.k]] = [copia[destino.k], copia[i]];
    emitir(copia);
  }
  function agregar(etapa: EtapaCandidato, tipo: string) {
    const t = tipos[tipo];
    if (!t) return;
    const p = pasoNuevo(t, etapa, pasos);
    const esSolicitud = (x: PasoProceso) => ["solicitud_web", "solicitud_sin_cv", "solicitud_con_cv"].includes(x.tipo);
    if (esSolicitud(p) && pasos.some(esSolicitud)) {
      // Cambio 30: «Solicitud web sin CV» y «con CV» se REEMPLAZAN entre sí (un proceso lleva una sola)
      emitir(pasos.map((x) => (esSolicitud(x) ? { ...p, id: x.id } : x)));
      return;
    }
    emitir(insertarSugerido(pasos, p, orden)); // Cambio 30: se acomoda sola en su posición sugerida
    if (CON_DETALLE.has(tipo)) setAbierto(p.id);
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="flex items-start gap-2 rounded-xl bg-surface-2/60 px-3 py-2 text-[12px] text-ink-2">
        <Zap className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand" />
        El candidato avanza a la siguiente etapa cuando no le quedan actividades obligatorias pendientes. Pasa a
        Onboarding al completarse Contrato y firma.
      </p>
      {opciones.etapas.map((e) => {
        const deEtapa = pasos.filter((p) => p.etapa === e.valor);
        // Cambio 55: sin lo que ya está en la etapa (salvo «Otra actividad») y una sola Solicitud web (sin o con CV)
        const catalogoEtapa = e.actividades ?? opciones.tiposPaso.filter((t) => t.etapas.includes(e.valor)).map((t) => t.valor);
        const disponibles = disponiblesEn(e.valor, catalogoEtapa, pasos)
          .map((v) => tipos[v]).filter((t): t is OpcionTipoPaso => Boolean(t));
        return (
          <section key={e.valor} className="rounded-2xl border border-border-soft">
            <header className="flex flex-wrap items-center gap-x-2 gap-y-0.5 border-b border-border-faint bg-surface-2/60 px-4 py-2.5">
              <div className="min-w-0">
                <h4 className="text-sm font-bold">{e.texto}</h4>
                {e.descripcion && <p className="text-[11px] text-ink-3">{e.descripcion}</p>}
              </div>
              {deEtapa.length === 0 ? (
                <Badge tone="neutral">Sin actividades · no bloquea</Badge>
              ) : (
                <span className="ml-auto text-[11px] text-ink-3">{deEtapa.length} actividad{deEtapa.length === 1 ? "" : "es"}</span>
              )}
            </header>
            <ul className="divide-y divide-border-faint">
              {deEtapa.map((p) => {
                const t = tipos[claveCatalogo(p)];
                const conDetalle = CON_DETALLE.has(p.tipo);
                const expandido = conDetalle && abierto === p.id;
                return (
                  <li key={p.id} className="px-4 py-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      {conDetalle ? (
                        <button type="button" onClick={() => setAbierto(expandido ? null : p.id)} className="text-ink-3 hover:text-ink" aria-label="Configurar actividad">
                          {expandido ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </button>
                      ) : <span className="w-4" />}
                      <input
                        value={p.nombre}
                        disabled={soloLectura}
                        onChange={(ev) => cambiarPaso(p.id, { nombre: ev.target.value })}
                        placeholder={p.tipo === "otra" ? "Nombre de la actividad *" : undefined}
                        className={cn("h-9 min-w-0 flex-1 rounded-lg border bg-transparent px-2 text-sm font-medium outline-none hover:border-border-soft focus:border-brand",
                          p.tipo === "otra" && !p.nombre.trim() ? "border-bad/60" : "border-transparent")}
                        aria-label="Nombre de la actividad"
                      />
                      <span className="text-[11px] text-ink-3">{t?.texto ?? p.tipo}</span>
                      {!soloLectura && (
                        <span className="ml-auto flex items-center">
                          {conFlechas(p) && (
                            <>
                              <button type="button" onClick={() => mover(p.id, -1)} className="grid h-8 w-8 place-items-center rounded-lg text-ink-3 hover:bg-surface-2" aria-label="Subir"><ArrowUp className="h-3.5 w-3.5" /></button>
                              <button type="button" onClick={() => mover(p.id, 1)} className="grid h-8 w-8 place-items-center rounded-lg text-ink-3 hover:bg-surface-2" aria-label="Bajar"><ArrowDown className="h-3.5 w-3.5" /></button>
                            </>
                          )}
                          <button type="button" onClick={() => quitarPaso(p.id)} className="grid h-8 w-8 place-items-center rounded-lg text-bad hover:bg-bad-soft" aria-label="Quitar actividad"><Trash2 className="h-3.5 w-3.5" /></button>
                        </span>
                      )}
                    </div>
                    {expandido && (
                      <DetalleActividad pasos={pasos} catalogo={catalogo} cursos={cursos} p={p} opciones={opciones} soloLectura={soloLectura} onCambio={(c) => cambiarPaso(p.id, c)} />
                    )}
                  </li>
                );
              })}
            </ul>
            {!soloLectura && (
              <div className="px-4 py-2.5">
                <select
                  value=""
                  onChange={(ev) => agregar(e.valor, ev.target.value)}
                  className="h-9 rounded-lg border border-dashed border-border-soft bg-transparent px-2 text-xs font-semibold text-brand outline-none"
                  aria-label={`Agregar actividad en ${e.texto}`}
                >
                  <option value="">+ Agregar actividad del catálogo en {e.texto}…</option>
                  {disponibles.map((t) => <option key={t.valor} value={t.valor}>{t.texto}</option>)}
                </select>
              </div>
            )}
          </section>
        );
      })}
      {pasos.length === 0 && (
        <p className="flex items-center gap-2 text-xs text-ink-3"><Plus className="h-3.5 w-3.5" /> Agrega al menos una actividad. Las etapas sin actividades nunca bloquean el avance.</p>
      )}
    </div>
  );
}

/** Cambio 56: escalas que el proveedor devuelve para las pruebas elegidas (por su id en el proveedor, «1,7» = varias). */
export function escalasDe(pruebas: number[], catalogo: PruebaPsicometrica[], escalas: Record<string, string[]> = {}): string[] {
  const salida: string[] = [];
  for (const id of pruebas) {
    const prueba = catalogo.find((x) => x.id === id);
    for (const ip of String(prueba?.idProveedor ?? "").split(",").map((x) => x.trim()).filter(Boolean)) {
      for (const e of escalas[ip] ?? []) if (!salida.includes(e)) salida.push(e);
    }
  }
  return salida;
}

/** Cambio 56: modalidad de evaluación de la Psicometría digital. «Con mínimos por escala» solo si las pruebas elegidas
 *  devuelven puntajes por escala en la API del proveedor. */
function EvaluacionPsicometria({ p, catalogo, opciones, soloLectura, onCambio }: {
  p: PasoProceso; catalogo: PruebaPsicometrica[]; opciones: OpcionesProceso; soloLectura: boolean; onCambio: (c: Partial<PasoProceso>) => void;
}) {
  const escalas = escalasDe(p.pruebas ?? [], catalogo, opciones.escalasPsicometria);
  const conf = p.evaluacion_psicometria ?? { modalidad: "informativa" as const };
  const modalidad = escalas.length ? conf.modalidad : "informativa";
  const minimos = conf.minimos ?? {};
  return (
    <div className="flex flex-col gap-2 text-xs text-ink-2 sm:col-span-2">
      <label className="flex flex-col gap-1">
        Modalidad
        <select className={selectRH} value={modalidad} disabled={soloLectura}
          onChange={(ev) => onCambio({ evaluacion_psicometria: { modalidad: ev.target.value as "informativa" | "minimos", minimos } })}>
          <option value="informativa">Informativa</option>
          <option value="minimos" disabled={!escalas.length}>Con mínimos por escala</option>
        </select>
        <span className="text-[11px] text-ink-3">
          {modalidad === "minimos"
            ? "Al terminar la prueba se comparan sus puntajes: si cumple todos los mínimos avanza solo; si no, queda «No cumple parámetro» y RH decide."
            : escalas.length ? "Al terminar la prueba se guardan los resultados y el candidato avanza solo."
              : "Estas pruebas no devuelven puntajes por escala: solo pueden ser informativas (al terminar, el candidato avanza solo)."}
        </span>
      </label>
      {modalidad === "minimos" && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {escalas.map((e) => (
            <label key={e} className="flex flex-col gap-1">
              {e}
              <input type="number" min={0} className={inputRH} disabled={soloLectura} value={minimos[e] ?? ""} placeholder="Sin mínimo"
                onChange={(ev) => {
                  const siguiente = { ...minimos };
                  if (ev.target.value === "") delete siguiente[e];
                  else siguiente[e] = Number(ev.target.value);
                  onCambio({ evaluacion_psicometria: { modalidad: "minimos", minimos: siguiente } });
                }} />
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

/** Cambio 47: actividades que se hacen en persona con cita (misma regla que `proceso.es_presencial` del backend). */
function esPresencial(p: PasoProceso): boolean {
  if (p.tipo === "entrevista_humana" || p.tipo === "medica" || p.tipo === "tecnica") return true;
  if (p.tipo === "psicometrica") return p.modalidad === "fisica";
  return p.tipo === "induccion" && (p.etapa === "Entrevista Humana" || p.modalidad_capacitacion === "presencial");
}

/** Solo lo PROPIO de cada actividad del catálogo. */
function DetalleActividad({ p, pasos = [], catalogo = [], cursos = [], opciones, soloLectura, onCambio }: {
  p: PasoProceso;
  pasos?: PasoProceso[];
  catalogo?: PruebaPsicometrica[];
  cursos?: Curso[];
  opciones: OpcionesProceso;
  soloLectura: boolean;
  onCambio: (c: Partial<PasoProceso>) => void;
}) {
  return (
    <div className="mt-2 grid gap-3 rounded-xl bg-surface-2/50 p-3 sm:grid-cols-2">
      {p.tipo === "entrevista_humana" && (
        <label className="flex flex-col gap-1 text-xs text-ink-2">
          Tipo de entrevista (genera su guion)
          <select className={selectRH} value={p.tipo_entrevista ?? "general"} disabled={soloLectura} onChange={(ev) => onCambio({ tipo_entrevista: ev.target.value })}>
            {opciones.tiposEntrevistaHumana.map((o) => <option key={o.valor} value={o.valor}>{o.texto}</option>)}
          </select>
        </label>
      )}
      {ENTREVISTAS_IA.has(p.tipo) && (
        <label className="flex flex-col gap-1 text-xs text-ink-2">
          Enfoque
          <select className={selectRH} value={enfoquePaso(p, pasos)} disabled={soloLectura} onChange={(ev) => onCambio({ enfoque: ev.target.value })}>
            {opciones.enfoquesEntrevistaAgente.map((o) => <option key={o.valor} value={o.valor}>{o.texto}</option>)}
          </select>
          <span className="text-[11px] text-ink-3">
            {enfoquePaso(p, pasos) === "operativo" ? "Entrevista base fija: situación actual e historial de hasta 2 trabajos."
              : "Red Human genera un guion de hasta 10 preguntas técnicas y de casos reales."}
          </span>
        </label>
      )}
      {p.tipo === "psicometrica" && p.modalidad !== "fisica" && (
        <div className="flex flex-col gap-1 text-xs text-ink-2 sm:col-span-2">
          Batería predeterminada
          <span className="text-[11px] text-ink-3">
            {(p.pruebas ?? []).length
              ? (p.pruebas ?? []).map((id) => catalogo.find((x) => x.id === id)?.nombre ?? `#${id} (inactiva)`).join(" + ")
              : "Sin batería: RH la elige al asignar."} Al abrir la actividad del candidato se precarga y RH solo da «Asignar y enviar».
          </span>
          {!soloLectura && <SelectorPruebas catalogo={catalogo} seleccion={p.pruebas ?? []} onChange={(ids) => onCambio({ pruebas: ids })} />}
        </div>
      )}
      {p.tipo === "psicometrica" && p.modalidad !== "fisica" && (
        <EvaluacionPsicometria p={p} catalogo={catalogo} opciones={opciones} soloLectura={soloLectura} onCambio={onCambio} />
      )}
      {p.tipo === "otra" && (
        <label className="flex flex-col gap-1 text-xs text-ink-2 sm:col-span-2">
          Mensaje al candidato por WhatsApp (opcional)
          <textarea className={cn(inputRH, "min-h-[72px] py-2")} value={p.mensaje ?? ""} disabled={soloLectura} maxLength={1000}
            onChange={(ev) => onCambio({ mensaje: ev.target.value })} placeholder="Se envía al habilitarse la actividad." />
          <span className="text-[11px] text-ink-3">La actividad queda pendiente hasta que RH presione «Marcar como realizada».</span>
        </label>
      )}
      {p.tipo === "induccion" && (
        <div className="flex flex-col gap-1 text-xs text-ink-2 sm:col-span-2">
          Modalidad
          {p.etapa === "Contratación" || p.etapa === "Onboarding" ? (
            <select className={selectRH} value={p.modalidad_capacitacion ?? "plataforma"} disabled={soloLectura}
              onChange={(ev) => onCambio({ modalidad_capacitacion: ev.target.value as "plataforma" | "presencial" })}>
              <option value="plataforma">En plataforma</option>
              <option value="presencial">Presencial</option>
            </select>
          ) : (
            // Cambio 30: Filtro RH = solo «En plataforma»; Filtro humano = solo «Presencial» (sin selector)
            <span className="text-sm font-medium text-ink">{p.etapa === "Entrevista Humana" ? "Presencial" : "En plataforma"}</span>
          )}
          {(p.etapa === "Entrevista Humana" || (p.etapa !== "Entrevista IA" && p.modalidad_capacitacion === "presencial")) && (
            <span className="text-[11px] text-ink-3">Se programa con fecha, hora e instructor (como la entrevista humana); el candidato y el instructor reciben su aviso.</span>
          )}
        </div>
      )}
      {p.tipo === "induccion" && (p.etapa === "Entrevista IA" || (p.etapa !== "Entrevista Humana" && p.modalidad_capacitacion !== "presencial")) && (
        <label className="flex flex-col gap-1 text-xs text-ink-2 sm:col-span-2">
          Curso
          <select className={selectRH} value={p.curso ?? ""} disabled={soloLectura} onChange={(ev) => onCambio({ curso: ev.target.value || undefined })}>
            <option value="">Elige un curso publicado…</option>
            {cursos.map((c) => <option key={c.id} value={c.id}>{c.titulo}</option>)}
          </select>
          <span className="text-[11px] text-ink-3">El bot manda la liga al habilitarse. Si aprueba el examen avanza sola; si reprueba queda «No aprobó el curso» y RH decide.</span>
        </label>
      )}
      {esPresencial(p) && (
        <>
          {/* Cambio 47: quién agenda (RH o el candidato por WhatsApp, con los horarios y cupos de su ubicación) */}
          <label className="flex flex-col gap-1 text-xs text-ink-2">
            Quién agenda
            <select className={selectRH} value={p.tipo_agenda ?? "rh"} disabled={soloLectura}
              onChange={(ev) => onCambio({ tipo_agenda: ev.target.value as "rh" | "candidato" })}>
              <option value="rh">La agenda RH</option>
              <option value="candidato">La agenda el candidato</option>
            </select>
            <span className="text-[11px] text-ink-3">
              {p.tipo_agenda === "candidato"
                ? "El bot le ofrece al candidato los próximos 3 días hábiles con cupo en su ubicación y le recuerda la cita 24 h y 2 h antes."
                : "RH programa la cita desde la ficha del candidato."}
            </span>
          </label>
          <label className="flex items-start gap-2 text-xs text-ink-2">
            <input type="checkbox" className="mt-0.5 h-4 w-4 rounded accent-brand" checked={Boolean(p.misma_cita)} disabled={soloLectura}
              onChange={(ev) => onCambio({ misma_cita: ev.target.checked })} />
            <span>
              Misma cita
              <span className="block text-[11px] text-ink-3">
                Se agenda una sola vez junto con las actividades presenciales consecutivas marcadas igual; cada evaluador captura su
                resultado y el candidato avanza hasta tener todos.
              </span>
            </span>
          </label>
        </>
      )}
      {p.tipo === "solicitud_web" && (
        <label className="flex items-center gap-2 text-xs text-ink-2 sm:col-span-2">
          <input type="checkbox" className="h-4 w-4 rounded accent-brand" checked={Boolean(p.con_cv)} disabled={soloLectura}
            onChange={(ev) => onCambio({ con_cv: ev.target.checked })} />
          La solicitud exige CV (si no, se cumple solo con la solicitud)
        </label>
      )}
    </div>
  );
}
