"use client";

/* Editor ÚNICO del proceso de selección (2026-10-06). Lo usan Configuración → Procesos de selección (plantilla de la
   Cuenta) y el formulario de vacante (copia personalizable, sin tocar la plantilla). Las cinco etapas son fijas; en
   cada una RH agrega pasos DESDE EL CATÁLOGO con su responsable, condición de avance (regla), plazo y su ejecución:
   «En paralelo» o «Esperar a…» (dependencias EXPLÍCITAS). El orden visual NO crea dependencias. El interruptor «Avance automático» de la etapa mueve al candidato cuando todos los
   obligatorios cumplen su condición. La validación final (ciclos, etapas permitidas) la hace la API. */

import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Plus, Trash2, Zap } from "lucide-react";
import { Badge } from "@/components/ui";
import { inputRH } from "@/components/dashboard/modulos-rh";
import { fetchEntrevistadores, type Entrevistador, type OpcionesProceso, type OpcionTipoPaso } from "@/lib/api";
import type { EtapaCandidato, EtapasProceso, PasoProceso, ReglaPaso, ResponsablePaso } from "@/lib/data";
import { cn } from "@/lib/utils";

const selectRH = cn(inputRH, "h-10 pr-8");

function nuevoId(pasos: PasoProceso[], tipo: string): string {
  const base = tipo.replace(/_/g, "-");
  let i = 1;
  let id = base;
  while (pasos.some((p) => p.id === id)) id = `${base}-${++i}`;
  return id;
}

export function pasoNuevo(t: OpcionTipoPaso, etapa: EtapaCandidato, pasos: PasoProceso[]): PasoProceso {
  return {
    id: nuevoId(pasos, t.valor),
    tipo: t.valor,
    nombre: t.texto,
    etapa,
    obligatorio: true,
    depende_de: [],
    responsable: { tipo: t.responsable },
    regla: t.regla === "calificacion" ? { tipo: "calificacion", minimo: 70 } : { tipo: t.regla },
    plazo_dias: null,
    ...(t.valor === "entrevista_humana" ? { tipo_entrevista: "general" } : t.valor === "entrevista_agente" ? { tipo_entrevista: "profesional" } : {}),
  };
}

export function EditorProceso({ pasos, etapas, opciones, onChange, soloLectura = false }: {
  pasos: PasoProceso[];
  etapas: EtapasProceso;
  opciones: OpcionesProceso;
  onChange: (pasos: PasoProceso[], etapas: EtapasProceso) => void;
  soloLectura?: boolean;
}) {
  const [usuarios, setUsuarios] = useState<Entrevistador[]>([]);
  const [abierto, setAbierto] = useState<string | null>(null);
  useEffect(() => {
    fetchEntrevistadores().then((u) => setUsuarios(u ?? []));
  }, []);
  const tipos = useMemo(() => Object.fromEntries(opciones.tiposPaso.map((t) => [t.valor, t])), [opciones]);
  const ordenEtapa = (e: string) => opciones.etapas.findIndex((x) => x.valor === e);

  const cambiarPaso = (id: string, cambios: Partial<PasoProceso>) => onChange(pasos.map((p) => (p.id === id ? { ...p, ...cambios } : p)), etapas);
  const quitarPaso = (id: string) =>
    onChange(pasos.filter((p) => p.id !== id).map((p) => ({ ...p, depende_de: p.depende_de.filter((d) => d !== id) })), etapas);
  function mover(id: string, delta: number) {
    const i = pasos.findIndex((p) => p.id === id);
    const mismos = pasos.map((p, k) => ({ p, k })).filter((x) => x.p.etapa === pasos[i].etapa);
    const pos = mismos.findIndex((x) => x.k === i);
    const destino = mismos[pos + delta];
    if (!destino) return;
    const copia = [...pasos];
    [copia[i], copia[destino.k]] = [copia[destino.k], copia[i]];
    onChange(copia, etapas);
  }
  function agregar(etapa: EtapaCandidato, tipo: string) {
    const t = tipos[tipo];
    if (!t) return;
    const p = pasoNuevo(t, etapa, pasos);
    onChange([...pasos, p], etapas);
    setAbierto(p.id);
  }

  return (
    <div className="flex flex-col gap-3">
      {opciones.etapas.map((e) => {
        const deEtapa = pasos.filter((p) => p.etapa === e.valor);
        const auto = Boolean(etapas[e.valor]?.avance_automatico);
        const disponibles = opciones.tiposPaso.filter((t) => t.etapas.includes(e.valor));
        return (
          <section key={e.valor} className="rounded-2xl border border-border-soft">
            <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border-faint bg-surface-2/60 px-4 py-2.5">
              <div className="flex items-center gap-2">
                <h4 className="text-sm font-bold">{e.texto}</h4>
                {deEtapa.length === 0 ? (
                  <Badge tone="neutral">Sin pasos · no bloquea</Badge>
                ) : (
                  <span className="text-[11px] text-ink-3">{deEtapa.length} paso{deEtapa.length === 1 ? "" : "s"} · {deEtapa.filter((p) => p.obligatorio).length} obligatorio(s)</span>
                )}
              </div>
              <label
                className={cn("flex items-center gap-2 text-xs", e.permiteAvanceAutomatico ? "text-ink-2" : "text-ink-3")}
                title={e.permiteAvanceAutomatico
                  ? "Al cumplirse todos los pasos obligatorios de esta etapa, el candidato pasa solo a la siguiente etapa con pasos."
                  : e.valor === "Contratación" ? "A Onboarding solo se entra con «Iniciar Onboarding»." : "Última etapa."}
              >
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded accent-brand"
                  checked={auto && e.permiteAvanceAutomatico}
                  disabled={soloLectura || !e.permiteAvanceAutomatico}
                  onChange={(ev) => onChange(pasos, { ...etapas, [e.valor]: { avance_automatico: ev.target.checked } })}
                />
                <Zap className="h-3.5 w-3.5" /> Avance automático
              </label>
            </header>
            <ul className="divide-y divide-border-faint">
              {deEtapa.map((p) => {
                const t = tipos[p.tipo];
                const expandido = abierto === p.id;
                const previos = pasos.filter((x) => x.id !== p.id && ordenEtapa(x.etapa) <= ordenEtapa(p.etapa));
                return (
                  <li key={p.id} className="px-4 py-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <button type="button" onClick={() => setAbierto(expandido ? null : p.id)} className="text-ink-3 hover:text-ink" aria-label="Detalles del paso">
                        {expandido ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                      </button>
                      <input
                        value={p.nombre}
                        disabled={soloLectura}
                        onChange={(ev) => cambiarPaso(p.id, { nombre: ev.target.value })}
                        className="h-9 min-w-0 flex-1 rounded-lg border border-transparent bg-transparent px-2 text-sm font-medium outline-none hover:border-border-soft focus:border-brand"
                        aria-label="Nombre del paso"
                      />
                      <span className="text-[11px] text-ink-3">{t?.texto ?? p.tipo}</span>
                      <label className="flex items-center gap-1.5 text-xs text-ink-2">
                        <input type="checkbox" className="h-4 w-4 rounded accent-brand" checked={p.obligatorio} disabled={soloLectura}
                          onChange={(ev) => cambiarPaso(p.id, { obligatorio: ev.target.checked })} />
                        Obligatorio
                      </label>
                      <span className="text-[11px] text-ink-3">
                        {p.depende_de.length > 0
                          ? `Espera a: ${p.depende_de.map((d) => pasos.find((x) => x.id === d)?.nombre ?? d).join(", ")}`
                          : "En paralelo"}
                      </span>
                      {!soloLectura && (
                        <span className="ml-auto flex items-center">
                          <button type="button" onClick={() => mover(p.id, -1)} className="grid h-8 w-8 place-items-center rounded-lg text-ink-3 hover:bg-surface-2" aria-label="Subir"><ArrowUp className="h-3.5 w-3.5" /></button>
                          <button type="button" onClick={() => mover(p.id, 1)} className="grid h-8 w-8 place-items-center rounded-lg text-ink-3 hover:bg-surface-2" aria-label="Bajar"><ArrowDown className="h-3.5 w-3.5" /></button>
                          <button type="button" onClick={() => quitarPaso(p.id)} className="grid h-8 w-8 place-items-center rounded-lg text-bad hover:bg-bad-soft" aria-label="Quitar paso"><Trash2 className="h-3.5 w-3.5" /></button>
                        </span>
                      )}
                    </div>
                    {expandido && (
                      <DetallePaso p={p} t={t} previos={previos} usuarios={usuarios} opciones={opciones} soloLectura={soloLectura}
                        onCambio={(c) => cambiarPaso(p.id, c)} />
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
                  aria-label={`Agregar paso en ${e.texto}`}
                >
                  <option value="">+ Agregar paso del catálogo en {e.texto}…</option>
                  {disponibles.map((t) => <option key={t.valor} value={t.valor}>{t.texto}</option>)}
                </select>
              </div>
            )}
          </section>
        );
      })}
      {pasos.length === 0 && (
        <p className="flex items-center gap-2 text-xs text-ink-3"><Plus className="h-3.5 w-3.5" /> Agrega al menos un paso. Las etapas sin pasos nunca bloquean el avance.</p>
      )}
    </div>
  );
}

function DetallePaso({ p, t, previos, usuarios, opciones, soloLectura, onCambio }: {
  p: PasoProceso;
  t?: OpcionTipoPaso;
  previos: PasoProceso[];
  usuarios: Entrevistador[];
  opciones: OpcionesProceso;
  soloLectura: boolean;
  onCambio: (c: Partial<PasoProceso>) => void;
}) {
  const r = p.responsable;
  const regla = p.regla;
  const setResp = (c: Partial<ResponsablePaso>) => onCambio({ responsable: { ...r, ...c } });
  const setRegla = (c: Partial<ReglaPaso>) => onCambio({ regla: { ...regla, ...c } });
  const dictamenes = t?.dictamenes ?? [];
  return (
    <div className="mt-2 grid gap-3 rounded-xl bg-surface-2/50 p-3 sm:grid-cols-2">
      <label className="flex flex-col gap-1 text-xs text-ink-2">
        Responsable
        <select className={selectRH} value={r.tipo} disabled={soloLectura} onChange={(ev) => setResp({ tipo: ev.target.value as ResponsablePaso["tipo"], usuario_id: null, nombre: "" })}>
          {opciones.responsables.map((o) => <option key={o.valor} value={o.valor}>{o.texto}</option>)}
        </select>
      </label>
      {r.tipo === "usuario" && (
        <label className="flex flex-col gap-1 text-xs text-ink-2">
          Usuario
          <select className={selectRH} value={r.usuario_id ?? ""} disabled={soloLectura}
            onChange={(ev) => {
              const u = usuarios.find((x) => x.id === Number(ev.target.value));
              setResp({ usuario_id: u?.id ?? null, nombre: u?.nombre ?? "" });
            }}>
            <option value="">Elegir al aplicar el proceso</option>
            {usuarios.map((u) => <option key={u.id} value={u.id}>{u.nombre}</option>)}
          </select>
        </label>
      )}
      {r.tipo === "externo" && (
        <label className="flex flex-col gap-1 text-xs text-ink-2">
          Nombre del externo
          <input className={inputRH} value={r.nombre ?? ""} disabled={soloLectura} onChange={(ev) => setResp({ nombre: ev.target.value })} placeholder="Ej. Clínica del Valle" />
        </label>
      )}
      <label className="flex flex-col gap-1 text-xs text-ink-2">
        Condición de avance
        <select className={selectRH} value={regla.tipo} disabled={soloLectura}
          onChange={(ev) => {
            const tipo = ev.target.value as ReglaPaso["tipo"];
            onCambio({ regla: tipo === "calificacion" ? { tipo, minimo: regla.minimo ?? 70 } : tipo === "dictamen" ? { tipo, aceptados: regla.aceptados } : { tipo } });
          }}>
          {opciones.reglas.map((o) => <option key={o.valor} value={o.valor}>{o.texto}</option>)}
        </select>
      </label>
      {regla.tipo === "calificacion" && (
        <label className="flex flex-col gap-1 text-xs text-ink-2">
          Calificación mínima (0-100)
          <input type="number" min={0} max={100} className={inputRH} value={regla.minimo ?? 70} disabled={soloLectura}
            onChange={(ev) => setRegla({ minimo: Number(ev.target.value) })} />
        </label>
      )}
      {regla.tipo === "dictamen" && dictamenes.length > 0 && (
        <fieldset className="flex flex-col gap-1 text-xs text-ink-2 sm:col-span-2">
          <legend className="mb-1">Dictámenes que permiten avanzar</legend>
          <div className="flex flex-wrap gap-3">
            {dictamenes.map((d) => {
              const aceptados = regla.aceptados ?? [];
              return (
                <label key={d.valor} className="flex items-center gap-1.5">
                  <input type="checkbox" className="h-4 w-4 rounded accent-brand" disabled={soloLectura}
                    checked={aceptados.length === 0 ? d.valor !== "desfavorable" && d.valor !== "no_apto" && d.valor !== "no_avanzar" && d.valor !== "requiere_otra_entrevista" : aceptados.includes(d.valor)}
                    onChange={(ev) => {
                      const base = aceptados.length ? aceptados : dictamenes.map((x) => x.valor).filter((v) => !["desfavorable", "no_apto", "no_avanzar", "requiere_otra_entrevista"].includes(v));
                      setRegla({ aceptados: ev.target.checked ? [...new Set([...base, d.valor])] : base.filter((v) => v !== d.valor) });
                    }} />
                  {d.texto}
                </label>
              );
            })}
          </div>
        </fieldset>
      )}
      <label className="flex flex-col gap-1 text-xs text-ink-2">
        Plazo (días, opcional)
        <input type="number" min={0} max={365} className={inputRH} value={p.plazo_dias ?? ""} disabled={soloLectura} placeholder="Sin plazo"
          onChange={(ev) => onCambio({ plazo_dias: ev.target.value === "" ? null : Number(ev.target.value) })} />
        <span className="text-[11px] text-ink-3">Vencerlo solo genera una alerta; nunca descarta.</span>
      </label>
      {p.tipo === "entrevista_humana" && (
        <label className="flex flex-col gap-1 text-xs text-ink-2">
          Tipo de entrevista (genera su guion)
          <select className={selectRH} value={p.tipo_entrevista ?? "general"} disabled={soloLectura} onChange={(ev) => onCambio({ tipo_entrevista: ev.target.value })}>
            {opciones.tiposEntrevistaHumana.map((o) => <option key={o.valor} value={o.valor}>{o.texto}</option>)}
          </select>
        </label>
      )}
      {p.tipo === "entrevista_agente" && (
        <label className="flex flex-col gap-1 text-xs text-ink-2">
          Enfoque de la Entrevista Red Human
          <select className={selectRH} value={p.tipo_entrevista ?? "profesional"} disabled={soloLectura} onChange={(ev) => onCambio({ tipo_entrevista: ev.target.value })}>
            {opciones.enfoquesEntrevistaAgente.map((o) => <option key={o.valor} value={o.valor}>{o.texto}</option>)}
          </select>
        </label>
      )}
      {p.tipo === "solicitud_web" && (
        <label className="flex items-center gap-2 text-xs text-ink-2 sm:col-span-2">
          <input type="checkbox" className="h-4 w-4 rounded accent-brand" checked={Boolean(p.con_cv)} disabled={soloLectura}
            onChange={(ev) => onCambio({ con_cv: ev.target.checked })} />
          La solicitud exige CV
        </label>
      )}
      <EjecucionPaso p={p} previos={previos} soloLectura={soloLectura} onCambio={onCambio} />
    </div>
  );
}

/** «En paralelo» (sin dependencias) o «Esperar a…» (dependencias explícitas). El orden de la lista nunca las crea. */
function EjecucionPaso({ p, previos, soloLectura, onCambio }: {
  p: PasoProceso;
  previos: PasoProceso[];
  soloLectura: boolean;
  onCambio: (c: Partial<PasoProceso>) => void;
}) {
  const [esperar, setEsperar] = useState(p.depende_de.length > 0);
  useEffect(() => setEsperar(p.depende_de.length > 0), [p.id, p.depende_de.length]);
  return (
    <fieldset className="flex flex-col gap-2 text-xs text-ink-2 sm:col-span-2">
      <legend className="mb-1">Ejecución</legend>
      <div className="flex flex-wrap gap-4">
        <label className="flex items-center gap-1.5">
          <input type="radio" className="h-4 w-4 accent-brand" checked={!esperar} disabled={soloLectura}
            onChange={() => { setEsperar(false); onCambio({ depende_de: [] }); }} />
          En paralelo <span className="text-ink-3">(arranca en cuanto el candidato llega a la etapa)</span>
        </label>
        <label className="flex items-center gap-1.5">
          <input type="radio" className="h-4 w-4 accent-brand" checked={esperar} disabled={soloLectura || previos.length === 0}
            onChange={() => setEsperar(true)} />
          Esperar a…
        </label>
      </div>
      {esperar && (previos.length === 0 ? (
        <span className="text-[11px] text-ink-3">No hay pasos previos.</span>
      ) : (
        <div className="flex flex-wrap gap-2">
          {previos.map((x) => {
            const marcado = p.depende_de.includes(x.id);
            return (
              <button key={x.id} type="button" disabled={soloLectura}
                onClick={() => onCambio({ depende_de: marcado ? p.depende_de.filter((d) => d !== x.id) : [...p.depende_de, x.id] })}
                className={cn("rounded-full border px-2.5 py-1 text-[11px] font-semibold transition",
                  marcado ? "border-brand bg-brand-soft text-brand" : "border-border-soft text-ink-3 hover:text-ink")}>
                {x.nombre}
              </button>
            );
          })}
        </div>
      ))}
    </fieldset>
  );
}
