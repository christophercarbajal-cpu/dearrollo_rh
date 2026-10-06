"use client";

/* Formulario de vacante → «Proceso de selección» (2026-10-06). Asocia una plantilla de la Cuenta y permite
   personalizar sus pasos, dependencias y responsables SOLO para esta vacante (la plantilla no cambia). Los candidatos
   que ya existen conservan la versión con la que entraron. Acordeón cerrado (reglas de UI: lo opcional plegado). */

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, GitBranch } from "lucide-react";
import { Badge } from "@/components/ui";
import { inputRH } from "@/components/dashboard/modulos-rh";
import { EditorProceso } from "@/components/dashboard/procesos/editor-proceso";
import { resumenPasos } from "@/components/dashboard/procesos/seccion-plantillas-proceso";
import {
  fetchOpcionesProceso, fetchPlantillasProceso, fetchProcesoVacante, type OpcionesProceso, type PlantillaProceso, type ProcesoEntrada,
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

  useEffect(() => {
    fetchPlantillasProceso().then((l) => setPlantillas(l ?? []));
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
            Define qué pasos sigue cada candidato de esta vacante. Personalizarlo aquí no cambia la plantilla de la Cuenta
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
                {personalizar ? "Ocultar pasos" : "Personalizar pasos, dependencias y responsables"}
              </button>
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
