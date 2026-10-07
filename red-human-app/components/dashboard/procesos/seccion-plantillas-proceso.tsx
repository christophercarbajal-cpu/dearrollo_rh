"use client";

/* Configuración → Procesos de selección (2026-10-06). Plantillas de la Cuenta: pasos por etapa (las cinco son fijas),
   dependencias, responsables, condiciones de avance, plazos y avance automático. Editar una plantilla sube su versión y
   NUNCA cambia a las vacantes que ya la copiaron ni a sus candidatos. 2026-10-06: cada Cuenta trae las tres RUTAS BASE
   editables (Masivos / Corporativos sin y con psicometría). Asignación de un candidato: proceso de la vacante →
   predeterminado de la Cuenta → «Corporativos sin psicometría»; ningún candidato queda sin ruta. */

import { useCallback, useEffect, useState } from "react";
import { GitBranch, Loader2, PenLine, Plus, RotateCcw, Save, Star, Trash2, X } from "lucide-react";
import { Badge, Button, Card } from "@/components/ui";
import { MenuAcciones } from "@/components/dashboard/menu-acciones";
import { AvisoLinea, CampoRH, ModalMarco, inputRH, type AvisoRH } from "@/components/dashboard/modulos-rh";
import { EditorProceso } from "@/components/dashboard/procesos/editor-proceso";
import {
  crearPlantillaProceso, crearPlantillaProcesoDesdeEjemplo, desactivarPlantillaProceso, editarPlantillaProceso, fetchOpcionesProceso,
  fetchPlantillasProceso, nombreEtapa, restaurarRutasBase, type OpcionesProceso, type PlantillaProceso,
} from "@/lib/api";
import type { EtapasProceso, PasoProceso } from "@/lib/data";

export function resumenPasos(pasos: PasoProceso[]): string {
  if (!pasos.length) return "Sin pasos";
  return pasos.map((p) => p.nombre).join(" → ");
}

export function SeccionPlantillasProceso() {
  const [lista, setLista] = useState<PlantillaProceso[] | null>(null);
  const [opciones, setOpciones] = useState<OpcionesProceso | null>(null);
  const [editor, setEditor] = useState<{ plantilla: PlantillaProceso | null } | null>(null);
  const [aviso, setAviso] = useState<AvisoRH>(null);
  const [ocupado, setOcupado] = useState("");

  const recargar = useCallback(async () => setLista((await fetchPlantillasProceso()) ?? []), []);
  useEffect(() => {
    void recargar();
    fetchOpcionesProceso().then((o) => setOpciones(o ?? null));
  }, [recargar]);

  async function desdeEjemplo(clave: string) {
    setOcupado(clave);
    const r = await crearPlantillaProcesoDesdeEjemplo(clave);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setAviso({ tono: "ok", texto: `Proceso «${r.data.nombre}» creado. Ajústalo con «Editar».` });
    void recargar();
  }
  async function restaurar() {
    setOcupado("rutas");
    const r = await restaurarRutasBase();
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setAviso({ tono: "ok", texto: r.data.creadas ? `Se restauraron ${r.data.creadas} ruta(s) base.` : "Las tres rutas base ya existen." });
    void recargar();
  }
  async function predeterminar(p: PlantillaProceso) {
    const r = await editarPlantillaProceso(p.id, { predeterminada: !p.predeterminada });
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    void recargar();
  }
  async function desactivar(p: PlantillaProceso) {
    const r = await desactivarPlantillaProceso(p.id);
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setAviso({ tono: "ok", texto: `«${p.nombre}» desactivado. Las vacantes que lo usan conservan su proceso.` });
    void recargar();
  }

  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand"><GitBranch className="h-4 w-4" /></span>
          <div>
            <h2 className="font-display text-base font-bold">Procesos de selección</h2>
            <p className="mt-0.5 text-sm text-ink-2">
              Pasos del catálogo por etapa, con responsables, condiciones de avance, plazos y ejecución (en paralelo o «Esperar a…»). Cada candidato recibe una copia de su ruta: la de su vacante, la predeterminada de la Cuenta o «Corporativos sin psicometría».
            </p>
          </div>
        </div>
        <Button size="sm" onClick={() => setEditor({ plantilla: null })} disabled={!opciones}><Plus className="h-4 w-4" /> Nuevo proceso</Button>
      </div>

      {aviso && <AvisoLinea aviso={aviso} onCerrar={() => setAviso(null)} />}

      <div className="mt-4">
        {lista === null ? (
          <Loader2 className="h-5 w-5 animate-spin text-ink-3" />
        ) : lista.length === 0 ? (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-ink-3">
              Sin procesos activos: los candidatos reciben la ruta «Corporativos sin psicometría».{" "}
              <button className="font-semibold text-brand hover:underline" onClick={restaurar} disabled={Boolean(ocupado)}>Restaurar las rutas base</button> o empieza con un ejemplo:
            </p>
            <div className="grid gap-2 sm:grid-cols-3">
              {(opciones?.ejemplos ?? []).map((e) => (
                <button key={e.clave} onClick={() => desdeEjemplo(e.clave)} disabled={Boolean(ocupado)}
                  className="rounded-xl border border-border-soft p-3 text-left transition hover:border-brand">
                  <p className="text-sm font-semibold">{ocupado === e.clave ? "Creando…" : e.nombre}</p>
                  <p className="mt-1 text-[11px] text-ink-3">{e.descripcion}</p>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <ul className="divide-y divide-border-faint">
            {lista.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
                    <span className="truncate">{p.nombre}</span>
                    <Badge tone="neutral">v{p.version}</Badge>
                    {p.predeterminada && <Badge tone="brand">Predeterminado</Badge>}
                    {p.rutaBase && <Badge tone="human">Ruta base</Badge>}
                    {p.rutaBase === "corporativos" && !lista.some((x) => x.predeterminada) && <Badge tone="neutral">Respaldo</Badge>}
                  </p>
                  <p className="truncate text-[11px] text-ink-3">{resumenPasos(p.pasos)}</p>
                  <p className="text-[11px] text-ink-3">
                    {p.pasos.filter((x) => x.obligatorio).length} obligatorios ·
                    {" "}Avance automático: {Object.entries(p.etapas).filter(([, c]) => c?.avance_automatico).map(([e]) => nombreEtapa(e)).join(", ") || "ninguno"}
                  </p>
                </div>
                <MenuAcciones
                  acciones={[
                    { etiqueta: "Editar", icono: <PenLine />, onClick: () => setEditor({ plantilla: p }) },
                    { etiqueta: p.predeterminada ? "Quitar como predeterminado" : "Usar como predeterminado", icono: <Star />, onClick: () => predeterminar(p) },
                    { etiqueta: "Desactivar", icono: <Trash2 />, peligrosa: true, onClick: () => desactivar(p) },
                  ]}
                />
              </li>
            ))}
            <li className="flex flex-wrap gap-2 pt-3">
              {(opciones?.rutasBase ?? []).some((r) => !lista.some((x) => x.rutaBase === r.clave)) && (
                <Button size="sm" variant="ghost" onClick={restaurar} disabled={Boolean(ocupado)}>
                  <RotateCcw className="h-3.5 w-3.5" /> Restaurar rutas base
                </Button>
              )}
              {(opciones?.ejemplos ?? []).map((e) => (
                <Button key={e.clave} size="sm" variant="ghost" onClick={() => desdeEjemplo(e.clave)} disabled={Boolean(ocupado)}>
                  <Plus className="h-3.5 w-3.5" /> {e.nombre}
                </Button>
              ))}
            </li>
          </ul>
        )}
      </div>

      {editor && opciones && (
        <EditorPlantilla
          plantilla={editor.plantilla}
          opciones={opciones}
          onClose={() => setEditor(null)}
          onGuardada={(p, nueva) => {
            setEditor(null);
            setAviso({ tono: "ok", texto: nueva ? `Proceso «${p.nombre}» creado.` : `Proceso «${p.nombre}» guardado (v${p.version}). Las vacantes que ya lo usan no cambian.` });
            void recargar();
          }}
        />
      )}
    </Card>
  );
}

function EditorPlantilla({ plantilla, opciones, onClose, onGuardada }: {
  plantilla: PlantillaProceso | null;
  opciones: OpcionesProceso;
  onClose: () => void;
  onGuardada: (p: PlantillaProceso, nueva: boolean) => void;
}) {
  const [nombre, setNombre] = useState(plantilla?.nombre ?? "");
  const [descripcion, setDescripcion] = useState(plantilla?.descripcion ?? "");
  const [predeterminada, setPredeterminada] = useState(plantilla?.predeterminada ?? false);
  const [pasos, setPasos] = useState<PasoProceso[]>(plantilla?.pasos ?? []);
  const [etapas, setEtapas] = useState<EtapasProceso>(plantilla?.etapas ?? {});
  const [error, setError] = useState("");
  const [guardando, setGuardando] = useState(false);

  async function guardar() {
    if (!nombre.trim()) return setError("Escribe el nombre del proceso.");
    if (!pasos.length) return setError("Agrega al menos un paso.");
    setGuardando(true);
    setError("");
    const datos = { nombre: nombre.trim(), descripcion, pasos, etapas, predeterminada };
    const r = plantilla ? await editarPlantillaProceso(plantilla.id, datos) : await crearPlantillaProceso(datos);
    setGuardando(false);
    if (!r.ok) return setError(r.error);
    onGuardada(r.data, !plantilla);
  }

  return (
    <ModalMarco titulo={plantilla ? `Editar proceso: ${plantilla.nombre}` : "Nuevo proceso de selección"}
      subtitulo="Las cinco etapas son fijas. Una etapa sin pasos obligatorios nunca bloquea el avance." onClose={onClose} ancho="sm:max-w-3xl">
      <div className="flex flex-col gap-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <CampoRH label="Nombre"><input className={inputRH} value={nombre} onChange={(e) => setNombre(e.target.value)} placeholder="Ej. Operativo mínimo" /></CampoRH>
          <CampoRH label="Descripción (opcional)"><input className={inputRH} value={descripcion} onChange={(e) => setDescripcion(e.target.value)} /></CampoRH>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="h-4 w-4 rounded accent-brand" checked={predeterminada} onChange={(e) => setPredeterminada(e.target.checked)} />
          Usar como predeterminado (vacantes nuevas y candidatos de vacantes sin proceso propio)
        </label>
        <EditorProceso pasos={pasos} etapas={etapas} opciones={opciones} onChange={(p, e) => { setPasos(p); setEtapas(e); }} />
        {error && <p className="rounded-xl border border-bad/40 bg-bad-soft px-3 py-2 text-sm font-semibold text-bad">{error}</p>}
        <div className="flex justify-end gap-2 border-t border-border-faint pt-4">
          <Button variant="outline" size="sm" onClick={onClose} disabled={guardando}><X className="h-4 w-4" /> Cancelar</Button>
          <Button size="sm" onClick={guardar} disabled={guardando}><Save className="h-4 w-4" /> {guardando ? "Guardando…" : "Guardar proceso"}</Button>
        </div>
      </div>
    </ModalMarco>
  );
}
