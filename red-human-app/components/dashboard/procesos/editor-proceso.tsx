"use client";

/* Editor ÚNICO del proceso de selección (2026-10-06; catálogo único 2026-10-09). Lo usan Configuración → Procesos de
   selección (plantilla de la Cuenta) y el formulario de vacante (copia personalizable, sin tocar la plantilla). Las
   cinco etapas son fijas; en cada una RH agrega actividades DESDE EL CATÁLOGO, las marca obligatorias u opcionales y
   ajusta SOLO lo propio de cada actividad (CV en la solicitud, guion de la entrevista, batería psicométrica).
   2026-10-09: ya no hay responsable, condición de avance, plazo, «En paralelo / Esperar a…» ni «Avance automático»:
   cada actividad define sola cuándo se cumple y a qué espera, y el candidato avanza solo cuando no quedan
   obligatorias pendientes. Al guardar se mandan los valores del catálogo (la API recalcula las dependencias). */

import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Plus, Trash2, Zap } from "lucide-react";
import { Badge } from "@/components/ui";
import { inputRH } from "@/components/dashboard/modulos-rh";
import { SelectorPruebas } from "@/components/dashboard/evaluaciones/asignar-psicometria";
import { fetchPruebasPsicometricas, type OpcionesProceso, type OpcionTipoPaso, type PruebaPsicometrica } from "@/lib/api";
import type { EtapaCandidato, EtapasProceso, PasoProceso } from "@/lib/data";
import { cn } from "@/lib/utils";

const selectRH = cn(inputRH, "h-10 pr-8");
/** Tipos con algo propio que configurar (el resto se usa tal cual lo define el catálogo). */
// 2026-10-09: las tres actividades de IA (avatar, WhatsApp, llamada) son independientes y cada una lleva su enfoque
const ENTREVISTAS_IA = new Set(["entrevista_agente", "entrevista_whatsapp", "llamada_agente"]);
const CON_DETALLE = new Set(["solicitud_web", "entrevista_humana", "psicometrica", ...ENTREVISTAS_IA]);

function nuevoId(pasos: PasoProceso[], tipo: string): string {
  const base = tipo.replace(/_/g, "-");
  let i = 1;
  let id = base;
  while (pasos.some((p) => p.id === id)) id = `${base}-${++i}`;
  return id;
}

/** Valores del catálogo: responsable y condición los define la actividad; sin plazo ni dependencias capturadas. */
function delCatalogo(p: PasoProceso, t?: OpcionTipoPaso): PasoProceso {
  if (!t) return { ...p, depende_de: [], plazo_dias: null };
  return {
    ...p,
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
    nombre: t.texto,
    etapa,
    obligatorio: t.obligatorio ?? true,
    depende_de: [],
    responsable: { tipo: t.responsable },
    regla: { tipo: t.regla },
    plazo_dias: null,
    ...(t.valor === "entrevista_humana" ? { tipo_entrevista: "general" } : ENTREVISTAS_IA.has(t.valor) ? { tipo_entrevista: "profesional" } : {}),
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
  const [abierto, setAbierto] = useState<string | null>(null);
  useEffect(() => {
    fetchPruebasPsicometricas().then((l) => setCatalogo((l ?? []).filter((x) => x.modo === "integrada" && x.activa)));
  }, []);
  const tipos = useMemo(() => Object.fromEntries(opciones.tiposPaso.map((t) => [t.valor, t])), [opciones]);

  const emitir = (lista: PasoProceso[]) => onChange(lista.map((p) => delCatalogo(p, tipos[p.tipo])), etapas);
  const cambiarPaso = (id: string, cambios: Partial<PasoProceso>) => emitir(pasos.map((p) => (p.id === id ? { ...p, ...cambios } : p)));
  const quitarPaso = (id: string) => emitir(pasos.filter((p) => p.id !== id));
  function mover(id: string, delta: number) {
    const i = pasos.findIndex((p) => p.id === id);
    const mismos = pasos.map((p, k) => ({ p, k })).filter((x) => x.p.etapa === pasos[i].etapa);
    const pos = mismos.findIndex((x) => x.k === i);
    const destino = mismos[pos + delta];
    if (!destino) return;
    const copia = [...pasos];
    [copia[i], copia[destino.k]] = [copia[destino.k], copia[i]];
    emitir(copia);
  }
  function agregar(etapa: EtapaCandidato, tipo: string) {
    const t = tipos[tipo];
    if (!t) return;
    const p = pasoNuevo(t, etapa, pasos);
    emitir([...pasos, p]);
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
        const disponibles = opciones.tiposPaso.filter((t) => t.etapas.includes(e.valor));
        return (
          <section key={e.valor} className="rounded-2xl border border-border-soft">
            <header className="flex flex-wrap items-center gap-2 border-b border-border-faint bg-surface-2/60 px-4 py-2.5">
              <h4 className="text-sm font-bold">{e.texto}</h4>
              {deEtapa.length === 0 ? (
                <Badge tone="neutral">Sin actividades · no bloquea</Badge>
              ) : (
                <span className="text-[11px] text-ink-3">{deEtapa.length} actividad{deEtapa.length === 1 ? "" : "es"} · {deEtapa.filter((p) => p.obligatorio).length} obligatoria(s)</span>
              )}
            </header>
            <ul className="divide-y divide-border-faint">
              {deEtapa.map((p) => {
                const t = tipos[p.tipo];
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
                        className="h-9 min-w-0 flex-1 rounded-lg border border-transparent bg-transparent px-2 text-sm font-medium outline-none hover:border-border-soft focus:border-brand"
                        aria-label="Nombre de la actividad"
                      />
                      <span className="text-[11px] text-ink-3">{t?.texto ?? p.tipo}</span>
                      <label className="flex items-center gap-1.5 text-xs text-ink-2">
                        <input type="checkbox" className="h-4 w-4 rounded accent-brand" checked={p.obligatorio} disabled={soloLectura}
                          onChange={(ev) => cambiarPaso(p.id, { obligatorio: ev.target.checked })} />
                        Obligatoria
                      </label>
                      {!soloLectura && (
                        <span className="ml-auto flex items-center">
                          <button type="button" onClick={() => mover(p.id, -1)} className="grid h-8 w-8 place-items-center rounded-lg text-ink-3 hover:bg-surface-2" aria-label="Subir"><ArrowUp className="h-3.5 w-3.5" /></button>
                          <button type="button" onClick={() => mover(p.id, 1)} className="grid h-8 w-8 place-items-center rounded-lg text-ink-3 hover:bg-surface-2" aria-label="Bajar"><ArrowDown className="h-3.5 w-3.5" /></button>
                          <button type="button" onClick={() => quitarPaso(p.id)} className="grid h-8 w-8 place-items-center rounded-lg text-bad hover:bg-bad-soft" aria-label="Quitar actividad"><Trash2 className="h-3.5 w-3.5" /></button>
                        </span>
                      )}
                    </div>
                    {expandido && (
                      <DetalleActividad catalogo={catalogo} p={p} opciones={opciones} soloLectura={soloLectura} onCambio={(c) => cambiarPaso(p.id, c)} />
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

/** Solo lo PROPIO de cada actividad del catálogo. */
function DetalleActividad({ p, catalogo = [], opciones, soloLectura, onCambio }: {
  p: PasoProceso;
  catalogo?: PruebaPsicometrica[];
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
          Enfoque de la Entrevista Red Human
          <select className={selectRH} value={p.tipo_entrevista ?? "profesional"} disabled={soloLectura} onChange={(ev) => onCambio({ tipo_entrevista: ev.target.value })}>
            {opciones.enfoquesEntrevistaAgente.map((o) => <option key={o.valor} value={o.valor}>{o.texto}</option>)}
          </select>
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
