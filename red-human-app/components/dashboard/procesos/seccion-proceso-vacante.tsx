"use client";

/* Formulario de vacante → «Proceso de selección» (2026-10-06). Asocia una plantilla de la Cuenta y permite
   personalizar sus pasos, dependencias y responsables SOLO para esta vacante (la plantilla no cambia). Los candidatos
   que ya existen conservan la versión con la que entraron. Acordeón cerrado (reglas de UI: lo opcional plegado).
   2026-10-07: «Batería psicométrica» a la vista — la vacante hereda la de la ruta y puede elegir otra del catálogo sin
   alterar la ruta (cambia solo la copia de la vacante). */

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, GitBranch } from "lucide-react";
import { Badge } from "@/components/ui";
import { inputRH } from "@/components/dashboard/modulos-rh";
import { EditorProceso } from "@/components/dashboard/procesos/editor-proceso";
import { SelectorPruebas } from "@/components/dashboard/evaluaciones/asignar-psicometria";
import { resumenPasos } from "@/components/dashboard/procesos/seccion-plantillas-proceso";
import {
  fetchOpcionesProceso, fetchPlantillasProceso, fetchProcesoVacante, fetchPruebasPsicometricas, type OpcionesProceso, type PlantillaProceso,
  type ProcesoEntrada, type PruebaPsicometrica,
} from "@/lib/api";
import type { EtapasProceso, PasoProceso, ProcesoConfig, Vacante } from "@/lib/data";
import { cn } from "@/lib/utils";

export interface EstadoProcesoVacante {
  tocado: boolean;
  plantillaId: number | null;
  pasos: PasoProceso[];
  etapas: EtapasProceso;
}

export function procesoInicial(v?: Vacante | null): EstadoProcesoVacante {
  const p = (v?.proceso ?? {}) as Partial<ProcesoConfig>;
  return { tocado: false, plantillaId: p.plantilla_id ?? null, pasos: p.pasos ?? [], etapas: p.etapas ?? {} };
}

/** Lo que se manda a POST/PATCH /vacantes (`proceso`). Sin tocar = no se manda (alta: la predeterminada). */
export function entradaProceso(e: EstadoProcesoVacante): ProcesoEntrada | undefined {
  if (!e.tocado) return undefined;
  if (!e.pasos.length) return { quitar: true };
  return { plantilla_id: e.plantillaId, pasos: e.pasos, etapas: e.etapas };
}

export function SeccionProcesoVacante({ value, onChange, codigoVacante }: {
  value: EstadoProcesoVacante;
  onChange: (e: EstadoProcesoVacante) => void;
  codigoVacante?: string;
}) {
  const [abierto, setAbierto] = useState(false);
  const [personalizar, setPersonalizar] = useState(false);
  const [plantillas, setPlantillas] = useState<PlantillaProceso[]>([]);
  const [opciones, setOpciones] = useState<OpcionesProceso | null>(null);
  const [activos, setActivos] = useState<number | null>(null);
  const [catalogo, setCatalogo] = useState<PruebaPsicometrica[]>([]);
  const [cambiarBateria, setCambiarBateria] = useState(false);

  useEffect(() => {
    fetchPlantillasProceso().then((l) => setPlantillas(l ?? []));
    fetchPruebasPsicometricas().then((l) => setCatalogo((l ?? []).filter((x) => x.modo === "integrada" && x.activa)));
    fetchOpcionesProceso().then((o) => setOpciones(o ?? null));
    if (codigoVacante) fetchProcesoVacante(codigoVacante).then((r) => setActivos(r?.candidatosActivos ?? null));
  }, [codigoVacante]);

  const predeterminada = plantillas.find((p) => p.predeterminada);
  // Alta sin tocar: se mostrará la predeterminada (la API la copia sola)
  const efectivo = !value.tocado && !codigoVacante && predeterminada
    ? { plantillaId: predeterminada.id, pasos: predeterminada.pasos, etapas: predeterminada.etapas }
    : value;
  const plantilla = plantillas.find((p) => p.id === efectivo.plantillaId);
  const personalizado = Boolean(plantilla) && JSON.stringify(plantilla?.pasos) !== JSON.stringify(efectivo.pasos);

  function elegir(id: string) {
    if (id === "") return onChange({ tocado: true, plantillaId: null, pasos: [], etapas: {} });
    if (id === "manual") {
      setPersonalizar(true);
      return onChange({ tocado: true, plantillaId: null, pasos: value.pasos, etapas: value.etapas });
    }
    const p = plantillas.find((x) => x.id === Number(id));
    if (p) onChange({ tocado: true, plantillaId: p.id, pasos: p.pasos, etapas: p.etapas });
  }

  const pasoPsico = efectivo.pasos.find((x) => x.tipo === "psicometrica");
  const pasoPsicoRuta = plantilla?.pasos.find((x) => x.tipo === "psicometrica");
  const bateriaPropia = Boolean(pasoPsico && pasoPsicoRuta) && JSON.stringify(pasoPsico?.pruebas ?? []) !== JSON.stringify(pasoPsicoRuta?.pruebas ?? []);
  const nombresBateria = (ids: number[]) => ids.map((id) => catalogo.find((x) => x.id === id)?.nombre ?? `#${id} (inactiva)`).join(" + ");
  function elegirBateria(ids: number[]) {
    if (!pasoPsico) return;
    onChange({ tocado: true, plantillaId: efectivo.plantillaId, etapas: efectivo.etapas,
      pasos: efectivo.pasos.map((x) => (x.id === pasoPsico.id ? { ...x, pruebas: ids } : x)) });
  }

  const resumen = efectivo.pasos.length
    ? `${plantilla?.nombre ?? "Proceso propio"}${personalizado ? " (personalizado)" : ""} · ${efectivo.pasos.length} pasos`
    : "Sin proceso: flujo de siempre";

  return (
    <section className="rounded-2xl border border-border-soft">
      <button type="button" onClick={() => setAbierto((x) => !x)} className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left">
        <span className="flex items-center gap-2">
          <GitBranch className="h-4 w-4 text-brand" />
          <span className="text-sm font-semibold">Proceso de selección</span>
          <span className="text-[12px] text-ink-3">{resumen}</span>
        </span>
        {abierto ? <ChevronDown className="h-4 w-4 text-ink-3" /> : <ChevronRight className="h-4 w-4 text-ink-3" />}
      </button>
      {abierto && (
        <div className="flex flex-col gap-3 border-t border-border-faint p-4">
          <p className="text-[12px] text-ink-3">
            Define qué actividades sigue cada candidato de esta vacante; Red Human genera los prefiltros y guiones SOLO de
            estas actividades. Ajustar la ruta aquí cambia únicamente esta vacante: la plantilla general de la Cuenta no se toca
            {codigoVacante && activos ? `, y los ${activos} candidato(s) activo(s) conservan la versión con la que entraron` : ""}.
          </p>
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-2">Plantilla</span>
            <select
              className={cn(inputRH, "pr-8")}
              value={efectivo.pasos.length ? (efectivo.plantillaId ?? "manual") : ""}
              onChange={(e) => elegir(e.target.value)}
            >
              <option value="">Sin proceso (flujo de siempre)</option>
              {plantillas.map((p) => <option key={p.id} value={p.id}>{p.nombre}{p.predeterminada ? " · predeterminado" : ""}</option>)}
              <option value="manual">Armar uno propio para esta vacante</option>
            </select>
          </label>
          {efectivo.pasos.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              {personalizado && <Badge tone="warn">Personalizado para esta vacante</Badge>}
              <span className="text-[12px] text-ink-3">{resumenPasos(efectivo.pasos)}</span>
              <button type="button" onClick={() => setPersonalizar((x) => !x)} className="text-[12px] font-semibold text-brand hover:underline">
                {personalizar ? "Ocultar pasos" : "Ajustar ruta de esta vacante"}
              </button>
            </div>
          )}
          {pasoPsico && (
            <div className="rounded-xl border border-border-soft p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-xs font-medium text-ink-2">Batería psicométrica{bateriaPropia ? " (elegida para esta vacante)" : plantilla ? " (de la ruta)" : ""}</p>
                  <p className="truncate text-sm text-ink">{(pasoPsico.pruebas ?? []).length ? nombresBateria(pasoPsico.pruebas ?? []) : "Sin batería: RH la elige al asignar"}</p>
                </div>
                <div className="flex items-center gap-3">
                  {bateriaPropia && (
                    <button type="button" onClick={() => elegirBateria(pasoPsicoRuta?.pruebas ?? [])} className="text-[12px] font-semibold text-ink-2 hover:underline">
                      Usar la de la ruta
                    </button>
                  )}
                  <button type="button" onClick={() => setCambiarBateria((x) => !x)} className="text-[12px] font-semibold text-brand hover:underline">
                    {cambiarBateria ? "Listo" : "Cambiar selección"}
                  </button>
                </div>
              </div>
              {cambiarBateria && (
                <div className="mt-2">
                  <SelectorPruebas catalogo={catalogo} seleccion={pasoPsico.pruebas ?? []} onChange={elegirBateria} />
                  <p className="mt-1 text-[11px] text-ink-3">Cambia solo esta vacante; la ruta general no se altera.</p>
                </div>
              )}
            </div>
          )}
          {(personalizar || (efectivo.plantillaId == null && value.tocado)) && opciones && (
            <EditorProceso
              pasos={efectivo.pasos}
              etapas={efectivo.etapas}
              opciones={opciones}
              onChange={(pasos, etapas) => onChange({ tocado: true, plantillaId: efectivo.plantillaId, pasos, etapas })}
            />
          )}
        </div>
      )}
    </section>
  );
}
