"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Plus,
  MapPin,
  Users,
  X,
  Check,
  Send,
  Briefcase,
  ShieldAlert,
  Link2,
  RefreshCw,
  ExternalLink,
  Ban,
  RotateCcw,
  Sparkles,
  FileEdit,
  Eye,
  Pencil,
  Trash2,
  AlertTriangle,
  Building2,
  LayoutGrid,
  List,
  Search,
  Filter,
  ChevronDown,
  ChevronRight,
  MoreHorizontal,
} from "lucide-react";
import { Button, Card, Badge, Eyebrow } from "@/components/ui";
import { Area, Selector, ToggleSiNo } from "@/components/dashboard/campos";
import {
  CONTENIDO_VACIO,
  FormularioContenidoVacante,
  contenidoComoPayload,
  contenidoDesdePlantilla,
  contenidoDesdeVacante,
  faltantesDatosPrincipales,
  requisitosLista,
  tieneContenidoManual,
  type ContenidoVacante,
} from "@/components/dashboard/vacantes/formulario-contenido";
import { PageHeader } from "@/components/dashboard/parts";
import { MenuAcciones } from "@/components/dashboard/menu-acciones";
import { Aviso, BotonCopiar } from "@/components/dashboard/subida";
import type { Vacante } from "@/lib/data";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  crearVacante,
  actualizarVacante,
  fetchPruebasPsicometricas,
  TIPOS_EVALUACION,
  type PruebaPsicometrica,
  eliminarVacante,
  fetchVacantes,
  fetchVistaPreviaVacante,
  publicarVacante,
  cerrarVacante,
  regenerarVacante,
  generarVacanteIA,
  fetchClientes,
  fetchCursos,
  fetchEntrevistadores,
  fetchPlantillas,
  guardarVacanteComoPlantilla,
  ENFOQUES_ENTREVISTA,
  nombreEtapa,
  type BloquePlataforma,
  type CriterioFiltro,
  type VacanteGenerada,
  type Cliente,
  type Plantilla,
} from "@/lib/api";
import { usePuedeDecidir } from "@/components/sesion";
import { useAnunciarContextoAgente } from "@/components/dashboard/agente/proveedor";
import { cn } from "@/lib/utils";
import { usePolling } from "@/lib/use-polling";
import { textoFecha } from "@/lib/fechas";

const estadoTone: Record<Vacante["estado"], "good" | "neutral" | "warn" | "bad"> = {
  Publicada: "good",
  Borrador: "neutral",
  "En revisión": "warn",
  Cerrada: "bad",
  Eliminada: "bad",
};

const filtros = ["Todas", "Publicada", "Borrador", "En revisión"] as const;

/** Canales de publicación que RH elige (rejilla «Publicación · Canales»). `api` debe coincidir EXACTO
 * con `models.PLATAFORMAS` del backend (2026-09-26). Google Empleos, Jooble y Talent.com por ahora
 * solo se registran; su integración está en docs/arquitectura_bolsas_empleo.md. */
const PLATAFORMAS = [
  { clave: "portal", nombre: "Portal", api: "Portal", nota: "Landing pública /aplicar" },
  { clave: "whatsapp", nombre: "WhatsApp", api: "WhatsApp", nota: "Menú del agente y estados" },
  { clave: "google", nombre: "Google Empleos", api: "Google Empleos", nota: "Etiqueta estructurada JobPosting" },
  { clave: "jooble", nombre: "Jooble", api: "Jooble", nota: "Feed XML automático" },
  { clave: "talent", nombre: "Talent.com", api: "Talent.com", nota: "Feed XML automático" },
] as const;

/** Textos generados por plataforma (`Vacante.publicaciones`): lo que RH copia y pega. Independiente
 * de los canales de arriba — OCC y LinkedIn ya no son canal, pero su texto se sigue generando. */
const BLOQUES_TEXTO = [
  { clave: "whatsapp", nombre: "WhatsApp" },
  { clave: "occ", nombre: "OCC" },
  { clave: "linkedin", nombre: "LinkedIn" },
  { clave: "portal", nombre: "Portal" },
] as const;

export default function Vacantes() {
  const puedeDecidir = usePuedeDecidir();
  const router = useRouter();
  const [filtro, setFiltro] = useState<(typeof filtros)[number]>("Todas");
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState<Vacante | null>(null);
  useAnunciarContextoAgente(
    sel ? { pantalla: "vacante", entidad: { tipo: "vacante", codigo: sel.id } } : { pantalla: "vacantes" },
  );
  const [verPrevia, setVerPrevia] = useState<string | null>(null);
  // 2026-09-15 (arranque en vivo): la lista arranca VACÍA y solo muestra lo que regresa la API — antes
  // se pintaban 6 vacantes de ejemplo hasta (y si) la API regresaba algo.
  const [datos, setDatos] = useState<Vacante[]>([]);
  const [cargando, setCargando] = useState(true);
  const [live, setLive] = useState(false);
  const [cambiandoEstatus, setCambiandoEstatus] = useState("");
  // --- Fase C: vista, buscador y filtros avanzados ---
  const [vista, setVista] = useState<"tarjetas" | "lista">("tarjetas");
  const [buscador, setBuscador] = useState("");
  const [filtrosAbiertos, setFiltrosAbiertos] = useState(false);
  const [fCliente, setFCliente] = useState<number | "">("" );
  const [fResponsable, setFResponsable] = useState<number | "">("" );
  const [fArea, setFArea] = useState("");
  const [fUbicacion, setFUbicacion] = useState("");
  const [clientes, setClientes] = useState<import("@/lib/api").Cliente[]>([]);
  const [usuarios, setUsuarios] = useState<{ id: number; nombre: string }[]>([]);
  // Menú de acciones flotante por tarjeta/fila
  const [menuAbierto, setMenuAbierto] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const recargar = useCallback(
    async (seleccionar?: string) => {
      const v = await fetchVacantes();
      if (v) {
        setDatos(v);
        setLive(true);
        if (seleccionar) setSel(v.find((x) => x.id === seleccionar) ?? null);
      }
      setCargando(false);
    },
    [],
  );

  useEffect(() => {
    recargar();
    // Fase C: restaurar vista preferida desde localStorage
    const guardada = localStorage.getItem("rh-vacantes-vista");
    if (guardada === "lista" || guardada === "tarjetas") setVista(guardada);
    // Cargar listas para los selectores de filtros avanzados
    fetchClientes("Activo").then((c) => setClientes(c ?? []));
    fetchEntrevistadores().then((u) => setUsuarios(u ?? []));
  }, [recargar]);
  // 2026-09-13 / Fase 4: los contadores del embudo (postulaciones ACTIVAS por etapa) reflejan los
  // movimientos hechos en Candidatos — revalidación con el mismo hook que el resto de los tableros.
  usePolling(recargar);

  // Cerrar menú flotante al hacer click fuera
  useEffect(() => {
    function clickFuera(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuAbierto(null);
    }
    document.addEventListener("mousedown", clickFuera);
    return () => document.removeEventListener("mousedown", clickFuera);
  }, []);

  /** Switch de estatus: Publicada -> Cerrada le quita la vacante del portal público al instante; Cerrada -> Publicada la reabre. */
  async function alternarEstatus(v: Vacante) {
    setCambiandoEstatus(v.id);
    const r =
      v.estado === "Publicada"
        ? await cerrarVacante(v.id)
        : await publicarVacante(v.id, v.plataformas.length ? v.plataformas : ["WhatsApp", "Portal"]);
    setCambiandoEstatus("");
    if (r.ok) recargar(sel?.id === v.id ? v.id : undefined);
  }

  function cambiarVista(v: "tarjetas" | "lista") {
    setVista(v);
    localStorage.setItem("rh-vacantes-vista", v);
  }

  /** Navega a Candidatos filtrando por vacante + etapa (Punto 17). */
  // B4: clic en un contador → vista de Candidatos con el filtro EXACTO (vacante y/o etapa)
  function navegarAEtapa(vacanteId: string, etapa?: string) {
    const params = new URLSearchParams({ vacante: vacanteId, ...(etapa ? { etapa } : {}) });
    router.push(`/dashboard/candidatos?${params.toString()}`);
  }

  function limpiarFiltros() {
    setFCliente("");
    setFResponsable("");
    setFArea("");
    setFUbicacion("");
    setBuscador("");
  }

  const filtrosActivosCount = [fCliente, fResponsable, fArea, fUbicacion].filter(Boolean).length;

  // Formatear fecha corta
  function fechaCorta(iso: string | null | undefined): string | null {
    if (!iso) return null;
    try {
      return textoFecha(iso, { day: "numeric", month: "short" }) || null;
    } catch {
      return null;
    }
  }

  // Filtrado combinado (estatus + buscador + filtros avanzados — todos client-side)
  const lista = useMemo(() => {
    let r = filtro === "Todas" ? datos : datos.filter((v) => v.estado === filtro);
    if (buscador.trim())
      r = r.filter((v) => v.titulo.toLowerCase().includes(buscador.toLowerCase().trim()));
    if (fArea.trim()) r = r.filter((v) => v.area?.toLowerCase().includes(fArea.toLowerCase().trim()));
    if (fUbicacion.trim()) r = r.filter((v) => v.ubicacion?.toLowerCase().includes(fUbicacion.toLowerCase().trim()));
    if (fCliente) r = r.filter((v) => {
      // cliente es un string de nombre — buscamos las vacantes que tengan algún candidato del cliente seleccionado
      // como no tenemos cliente_id en el frontend, filtramos por nombre de cliente
      const nombreCliente = clientes.find((c) => c.id === fCliente)?.nombre;
      return nombreCliente ? v.cliente === nombreCliente : true;
    });
    if (fResponsable) r = r.filter((v) => {
      const nombreResp = usuarios.find((u) => u.id === fResponsable)?.nombre;
      return nombreResp ? v.responsable === nombreResp : true;
    });
    return r;
  }, [datos, filtro, buscador, fArea, fUbicacion, fCliente, fResponsable, clientes, usuarios]);

  // Columna Cliente: solo si alguna vacante del listado actual tiene cliente != null
  const mostrarCliente = useMemo(() => lista.some((v) => v.cliente), [lista]);

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
      <PageHeader title="Vacantes" subtitle="Publica, distribuye y da seguimiento a tus vacantes.">
        {live && (
          <Badge tone="good" dot>
            API en vivo
          </Badge>
        )}
        {/* Fase C: selector de vista */}
        <div className="flex items-center gap-1 rounded-lg border border-border-soft bg-surface-2 p-1">
          <button
            id="vacantes-vista-tarjetas"
            onClick={() => cambiarVista("tarjetas")}
            className={cn(
              "grid place-items-center rounded-md p-1.5 transition",
              vista === "tarjetas" ? "bg-surface text-ink shadow-sm" : "text-ink-3 hover:text-ink",
            )}
            title="Vista tarjetas"
          >
            <LayoutGrid className="h-4 w-4" />
          </button>
          <button
            id="vacantes-vista-lista"
            onClick={() => cambiarVista("lista")}
            className={cn(
              "grid place-items-center rounded-md p-1.5 transition",
              vista === "lista" ? "bg-surface text-ink shadow-sm" : "text-ink-3 hover:text-ink",
            )}
            title="Vista lista"
          >
            <List className="h-4 w-4" />
          </button>
        </div>
        {puedeDecidir && (
          <Button size="sm" onClick={() => setOpen(true)}>
            <Plus className="h-4 w-4" /> Nueva vacante
          </Button>
        )}
      </PageHeader>

      {/* Filtros rápidos de estatus + buscador + botón Filtros */}
      <div className="mt-6 flex flex-wrap items-center gap-2">
        {filtros.map((f) => (
          <button
            key={f}
            onClick={() => setFiltro(f)}
            className={cn(
              "rounded-full border px-3.5 py-1.5 text-sm font-medium transition",
              filtro === f
                ? "border-brand bg-brand-soft text-brand"
                : "border-border-soft text-ink-2 hover:border-brand/40 hover:text-ink",
            )}
          >
            {f}
            {f !== "Todas" && (
              <span className="ml-1.5 font-mono text-xs opacity-70">
                {datos.filter((v) => v.estado === f).length}
              </span>
            )}
          </button>
        ))}
        {/* Buscador */}
        <div className="relative ml-auto flex items-center">
          <Search className="absolute left-3 h-4 w-4 text-ink-3" />
          <input
            id="vacantes-buscador"
            type="text"
            placeholder="Buscar por título…"
            value={buscador}
            onChange={(e) => setBuscador(e.target.value)}
            className="h-9 rounded-full border border-border-soft bg-surface pl-9 pr-3 text-sm text-ink placeholder:text-ink-3 focus:border-brand focus:outline-none"
          />
          {buscador && (
            <button onClick={() => setBuscador("")} className="absolute right-3 text-ink-3 hover:text-ink">
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        {/* Botón Filtros avanzados */}
        <button
          id="vacantes-btn-filtros"
          onClick={() => setFiltrosAbiertos((prev) => !prev)}
          className={cn(
            "flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-medium transition",
            filtrosAbiertos || filtrosActivosCount > 0
              ? "border-brand bg-brand-soft text-brand"
              : "border-border-soft text-ink-2 hover:border-brand/40 hover:text-ink",
          )}
        >
          <Filter className="h-3.5 w-3.5" />
          Filtros{filtrosActivosCount > 0 ? ` · ${filtrosActivosCount}` : ""}
          <ChevronDown className={cn("h-3.5 w-3.5 transition", filtrosAbiertos && "rotate-180")} />
        </button>
        {(filtrosActivosCount > 0 || buscador) && (
          <button
            onClick={limpiarFiltros}
            className="text-xs text-ink-3 underline hover:text-ink"
          >
            Limpiar
          </button>
        )}
      </div>

      {/* Panel de filtros avanzados */}
      {filtrosAbiertos && (
        <div className="mt-3 flex flex-wrap items-end gap-3 rounded-xl border border-border-soft bg-surface-2 p-4">
          {clientes.length > 0 && (
            <label className="flex flex-col gap-1.5 text-xs text-ink-2">
              Cliente
              <select
                value={fCliente}
                onChange={(e) => setFCliente(e.target.value ? Number(e.target.value) : "")}
                className="rounded-lg border border-border-soft bg-surface px-3 py-2 text-sm text-ink focus:border-brand focus:outline-none"
              >
                <option value="">Todos</option>
                {clientes.map((c) => (
                  <option key={c.id} value={c.id}>{c.nombre}</option>
                ))}
              </select>
            </label>
          )}
          {usuarios.length > 0 && (
            <label className="flex flex-col gap-1.5 text-xs text-ink-2">
              Responsable
              <select
                value={fResponsable}
                onChange={(e) => setFResponsable(e.target.value ? Number(e.target.value) : "")}
                className="rounded-lg border border-border-soft bg-surface px-3 py-2 text-sm text-ink focus:border-brand focus:outline-none"
              >
                <option value="">Todos</option>
                {usuarios.map((u) => (
                  <option key={u.id} value={u.id}>{u.nombre}</option>
                ))}
              </select>
            </label>
          )}
          <label className="flex flex-col gap-1.5 text-xs text-ink-2">
            Área
            <input
              type="text"
              value={fArea}
              onChange={(e) => setFArea(e.target.value)}
              placeholder="Ej: Operaciones"
              className="rounded-lg border border-border-soft bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-3 focus:border-brand focus:outline-none"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-xs text-ink-2">
            Ubicación
            <input
              type="text"
              value={fUbicacion}
              onChange={(e) => setFUbicacion(e.target.value)}
              placeholder="Ej: Guadalajara"
              className="rounded-lg border border-border-soft bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-3 focus:border-brand focus:outline-none"
            />
          </label>
          <button
            onClick={limpiarFiltros}
            className="rounded-lg border border-border-soft px-3 py-2 text-sm text-ink-2 hover:border-bad/40 hover:bg-bad-soft hover:text-bad"
          >
            Limpiar filtros
          </button>
        </div>
      )}

      {/* Vista Tarjetas */}
      {cargando && (
        <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-44 animate-pulse rounded-2xl border border-border-soft bg-surface-2/60" />
          ))}
        </div>
      )}
      {!cargando && vista === "tarjetas" && lista.length === 0 && (
        <Card className="mt-5 flex flex-col items-center gap-2 p-12 text-center">
          <Briefcase className="h-8 w-8 text-ink-3" />
          <p className="text-sm font-medium text-ink-2">{datos.length === 0 ? "Aún no hay vacantes." : "Sin vacantes con estos filtros."}</p>
          {datos.length === 0 && <p className="max-w-sm text-xs text-ink-3">Crea la primera con «Nueva vacante»: se genera con Red Human a partir de los datos que captures.</p>}
        </Card>
      )}
      {!cargando && vista === "tarjetas" && (
        <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {lista.map((v) => (
            <Card key={v.id} hover className="flex cursor-pointer flex-col p-5" onClick={() => setSel(v)}>
              <div className="flex items-start justify-between">
                <span className="grid h-11 w-11 place-items-center rounded-xl bg-brand-soft text-brand">
                  <Briefcase className="h-5 w-5" />
                </span>
                <div className="flex items-center gap-2">
                  <Badge tone={estadoTone[v.estado]} dot>{v.estado}</Badge>
                  {/* Menú de acciones (Fase C: mover de botón principal a ⋯) */}
                  {puedeDecidir && (v.estado === "Publicada" || v.estado === "Cerrada") && (
                    <div className="relative" ref={menuAbierto === v.id ? menuRef : undefined}>
                      <button
                        id={`vacante-menu-${v.id}`}
                        onClick={(e) => { e.stopPropagation(); setMenuAbierto(menuAbierto === v.id ? null : v.id); }}
                        className="grid place-items-center rounded-lg p-1.5 text-ink-3 transition hover:bg-surface-2 hover:text-ink"
                      >
                        <MoreHorizontal className="h-4 w-4" />
                      </button>
                      {menuAbierto === v.id && (
                        <div className="absolute right-0 top-full z-20 mt-1 min-w-[160px] rounded-xl border border-border-soft bg-surface p-1 shadow-lg">
                          <button
                            onClick={(e) => { e.stopPropagation(); setMenuAbierto(null); alternarEstatus(v); }}
                            disabled={cambiandoEstatus === v.id}
                            className={cn(
                              "flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-medium transition hover:bg-surface-2 disabled:opacity-50",
                              v.estado === "Publicada" ? "text-bad" : "text-good",
                            )}
                          >
                            {v.estado === "Publicada" ? <Ban className="h-3.5 w-3.5" /> : <RotateCcw className="h-3.5 w-3.5" />}
                            {cambiandoEstatus === v.id ? "…" : v.estado === "Publicada" ? "Cerrar vacante" : "Reabrir vacante"}
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>

              <h3 className="font-display mt-4 text-lg font-bold leading-snug">{v.titulo}</h3>
              <p className="mt-1 text-sm text-ink-3">
                {v.area} · {v.cliente ?? v.empresa}
              </p>

              <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-sm text-ink-2">
                <span className="flex items-center gap-1.5">
                  <MapPin className="h-4 w-4 text-ink-3" /> {v.ubicacion}
                </span>
                <span className="font-mono text-brand">{v.sueldo}</span>
              </div>

              {v.plataformas.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {v.plataformas.map((p) => (
                    <span key={p} className="rounded-md bg-surface-2 px-2 py-0.5 font-mono text-[10px] text-ink-3">{p}</span>
                  ))}
                </div>
              )}

              {(v.avisosCumplimiento?.length ?? 0) > 0 && (
                <p className="mt-3 flex items-center gap-1.5 text-[11px] text-warn">
                  <ShieldAlert className="h-3.5 w-3.5" />
                  {v.avisosCumplimiento!.length} aviso(s) de cumplimiento por confirmar
                </p>
              )}

              {/* Fase C: mini-embudo clicable (Punto 16 + 17) */}
              {v.embudo?.etapas && Object.keys(v.embudo.etapas).length > 0 && (
                <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1">
                  {Object.entries(v.embudo.etapas)
                    .filter(([, n]) => n > 0)
                    .map(([etapa, n]) => (
                      <button
                        key={etapa}
                        id={`vacante-embudo-${v.id}-${etapa.replace(/\s/g, "-")}`}
                        onClick={(e) => { e.stopPropagation(); navegarAEtapa(v.id, etapa); }}
                        className="flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium text-ink-3 transition hover:bg-brand-soft hover:text-brand"
                      >
                        <span className="h-1.5 w-1.5 rounded-full bg-brand" />
                        {nombreEtapa(etapa)} <span className="font-semibold tabular">{n}</span>
                      </button>
                    ))}
                </div>
              )}

              {/* Fase C: pie con candidatos + fechas */}
              <div className="mt-auto flex items-end justify-between border-t border-border-faint pt-4">
                <div className="flex items-center gap-2 text-sm">
                  {/* B4: el total = activos de la vacante (misma fuente que el embudo y el Kanban); clic → Kanban filtrado */}
                  <button
                    onClick={(e) => { e.stopPropagation(); navegarAEtapa(v.id); }}
                    className="flex items-center gap-2 rounded-md px-1 py-0.5 transition hover:bg-brand-soft hover:text-brand"
                    title="Ver todos los candidatos activos de esta vacante"
                  >
                    <Users className="h-4 w-4 text-ink-3" />
                    <span className="font-semibold tabular">{v.candidatos}</span>
                    <span className="text-ink-3">candidatos</span>
                  </button>
                  {v.nuevos > 0 && (
                    <span className="rounded-full bg-human-soft px-2 py-0.5 text-[11px] font-semibold text-human">
                      {v.nuevos} nuevos
                    </span>
                  )}
                </div>
                <div className="text-right text-[11px] text-ink-3">
                  {fechaCorta(v.creada) && <span>Creada {fechaCorta(v.creada)}</span>}
                  {fechaCorta(v.publicadaEn) && (
                    <><br /><span className="text-good">Publicada {fechaCorta(v.publicadaEn)}</span></>
                  )}
                </div>
              </div>
            </Card>
          ))}

          {/* Add card */}
          <button
            onClick={() => setOpen(true)}
            className="group grid min-h-[220px] place-items-center rounded-2xl border border-dashed border-border-soft text-ink-3 transition hover:border-brand hover:text-brand"
          >
            <span className="flex flex-col items-center gap-2">
              <span className="grid h-12 w-12 place-items-center rounded-2xl bg-surface-2 transition group-hover:bg-brand-soft">
                <Plus className="h-6 w-6" />
              </span>
              <span className="text-sm font-medium">Crear vacante</span>
            </span>
          </button>
        </div>
      )}

      {/* Fase C: Vista Lista */}
      {!cargando && vista === "lista" && (
        <div className="mt-5 overflow-x-auto rounded-xl border border-border-soft">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-soft bg-surface-2 text-xs font-semibold uppercase tracking-wide text-ink-3">
                <th className="px-4 py-3 text-left">Vacante</th>
                {mostrarCliente && <th className="px-4 py-3 text-left">Cliente</th>}
                <th className="px-4 py-3 text-left">Área</th>
                <th className="px-4 py-3 text-left">Estatus</th>
                <th className="px-4 py-3 text-left">Candidatos / Etapas</th>
                <th className="px-4 py-3 text-left">Responsable</th>
                <th className="px-4 py-3 text-left">Ubicación</th>
                <th className="px-4 py-3 text-left">Creada</th>
                {puedeDecidir && <th className="px-4 py-3" />}
              </tr>
            </thead>
            <tbody className="divide-y divide-border-faint">
              {lista.map((v) => (
                <tr
                  key={v.id}
                  onClick={() => setSel(v)}
                  className="cursor-pointer transition hover:bg-brand-soft/30"
                >
                  <td className="px-4 py-3">
                    <p className="font-semibold text-ink">{v.titulo}</p>
                    <p className="font-mono text-[11px] text-ink-3">{v.id}</p>
                  </td>
                  {mostrarCliente && <td className="px-4 py-3 text-ink-2">{v.cliente ?? "—"}</td>}
                  <td className="px-4 py-3 text-ink-2">{v.area || "—"}</td>
                  <td className="px-4 py-3">
                    <Badge tone={estadoTone[v.estado]} dot>{v.estado}</Badge>
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={(e) => { e.stopPropagation(); navegarAEtapa(v.id); }}
                      className="flex items-center gap-1 rounded-md px-1 text-ink-2 transition hover:bg-brand-soft hover:text-brand"
                      title="Ver todos los candidatos activos de esta vacante"
                    >
                      <Users className="h-3.5 w-3.5 text-ink-3" />
                      <span className="font-semibold">{v.candidatos}</span>
                    </button>
                    {v.embudo?.etapas && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {Object.entries(v.embudo.etapas)
                          .filter(([, n]) => n > 0)
                          .map(([etapa, n]) => (
                            <button
                              key={etapa}
                              onClick={(e) => { e.stopPropagation(); navegarAEtapa(v.id, etapa); }}
                              className="rounded-md bg-brand-soft px-1.5 py-0.5 text-[10px] font-medium text-brand hover:bg-brand hover:text-white"
                            >
                              {nombreEtapa(etapa)} {n}
                            </button>
                          ))}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-ink-2">{v.responsable ?? "—"}</td>
                  <td className="px-4 py-3 text-ink-2">{v.ubicacion || "—"}</td>
                  <td className="px-4 py-3 text-[12px] text-ink-3">
                    {fechaCorta(v.creada) ?? "—"}
                    {fechaCorta(v.publicadaEn) && (
                      <div className="text-good">{fechaCorta(v.publicadaEn)}</div>
                    )}
                  </td>
                  {puedeDecidir && (
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      {(v.estado === "Publicada" || v.estado === "Cerrada") && (
                        <button
                          onClick={() => alternarEstatus(v)}
                          disabled={cambiandoEstatus === v.id}
                          className={cn(
                            "rounded-lg border px-3 py-1.5 text-xs font-medium transition disabled:opacity-50",
                            v.estado === "Publicada"
                              ? "border-bad/30 text-bad hover:bg-bad-soft"
                              : "border-good/30 text-good hover:bg-good-soft",
                          )}
                        >
                          {cambiandoEstatus === v.id ? "…" : v.estado === "Publicada" ? "Cerrar" : "Reabrir"}
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {lista.length === 0 && (
            <div className="py-12 text-center text-sm text-ink-3">Sin vacantes con estos filtros.</div>
          )}
        </div>
      )}

      {open && (
        <CrearVacante
          onClose={() => setOpen(false)}
          onGuardado={(codigo) => {
            recargar(codigo);
            setOpen(false);
          }}
        />
      )}

      {sel && (
        <DetalleVacante
          v={sel}
          live={live}
          onClose={() => setSel(null)}
          onCambio={recargar}
          onVerPrevia={() => setVerPrevia(sel.id)}
          onEliminada={() => {
            // CRUD: al eliminar se vuelve a la lista (la eliminada ya no aparece en el tablero)
            setSel(null);
            recargar();
          }}
        />
      )}

      {verPrevia && <VistaPreviaVacante codigo={verPrevia} onClose={() => setVerPrevia(null)} />}

    </div>
  );
}

/* ============================================================
   CRUD (2026-09-15): editar vacante — el MISMO formulario de «Nueva vacante» en modo edición
   ============================================================ */
function EditarVacante({ v, onClose, onGuardada }: { v: Vacante; onClose: () => void; onGuardada: () => void }) {
  const [contenido, setContenido] = useState<ContenidoVacante>(() => contenidoDesdeVacante(v));
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState("");

  async function guardar() {
    const faltan = faltantesDatosPrincipales(contenido);
    if (faltan.length) return setError(`Faltan datos principales: ${faltan.join(", ")}.`);
    setGuardando(true);
    setError("");
    // PATCH /vacantes/{codigo}: mismo payload que el alta; el servidor deriva sueldo/ubicación y respeta el resto.
    const r = await actualizarVacante(v.id, contenidoComoPayload(contenido));
    setGuardando(false);
    if (!r.ok) return setError(r.error);
    onGuardada();
  }

  return (
    <Panel titulo={`Editar: ${v.titulo}`} eyebrow={v.id} onClose={onClose} ancho="max-w-3xl">
      <div className="flex flex-col gap-6 p-6">
        <Aviso tono="info">
          Corrige cualquier campo (puesto, sueldo, ubicación, preguntas de prefiltro, entrevista…). Los cambios aplican de inmediato en
          portal, WhatsApp y la IA; lo ya publicado por plataforma se conserva hasta que regeneres.
        </Aviso>
        <FormularioContenidoVacante
          value={contenido}
          onChange={setContenido}
          clienteId={v.clienteId ?? null}
          mostrarCliente={v.mostrarClienteCandidato ?? true}
        />
        {error && <Aviso tono="error" onCerrar={() => setError("")}>{error}</Aviso>}
        <div className="flex items-center gap-3 border-t border-border-faint pt-5">
          <Button variant="outline" className="flex-1" onClick={onClose} disabled={guardando}>
            Cancelar
          </Button>
          <Button className="flex-1" onClick={guardar} disabled={guardando}>
            {guardando ? "Guardando…" : "Guardar cambios"}
          </Button>
        </div>
      </div>
    </Panel>
  );
}

/* Modal de confirmación de baja lógica: advierte lo que pasa con las postulaciones activas. */
function ConfirmarEliminarVacante({
  v,
  ocupado,
  onCancelar,
  onConfirmar,
}: {
  v: Vacante;
  ocupado: boolean;
  onCancelar: () => void;
  onConfirmar: () => void;
}) {
  const activas = Object.values(v.embudo?.etapas ?? {}).reduce((a, b) => a + b, 0);
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onCancelar}>
      <Card className="w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-bad-soft text-bad">
            <AlertTriangle className="h-5 w-5" />
          </span>
          <div>
            <h2 className="font-display text-lg font-bold">¿Eliminar la vacante «{v.titulo}»?</h2>
            <p className="mt-2 text-sm leading-relaxed text-ink-2">
              Dejará de aparecer en los tableros, en el portal público y en el menú de WhatsApp.
              {activas > 0 && (
                <>
                  {" "}
                  <b className="text-bad">{activas} postulación{activas !== 1 ? "es" : ""} activa{activas !== 1 ? "s" : ""}</b> se cerrarán con motivo «vacante eliminada».
                </>
              )}{" "}
              Nada se borra: el historial (candidatos, entrevistas, expedientes) se conserva y la vacante se puede restaurar.
            </p>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onCancelar} disabled={ocupado}>
            Cancelar
          </Button>
          <Button size="sm" className="bg-bad text-white hover:bg-bad/90" onClick={onConfirmar} disabled={ocupado}>
            <Trash2 className="h-4 w-4" /> {ocupado ? "Eliminando…" : "Sí, eliminar vacante"}
          </Button>
        </div>
      </Card>
    </div>
  );
}

/* ============================================================
   Modal: crear vacante con IA
   ============================================================ */
function CrearVacante({ onClose, onGuardado }: { onClose: () => void; onGuardado: (codigo: string) => void }) {
  const [paso, setPaso] = useState<"elegir" | "plantilla" | "formulario">("elegir");
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [usuarios, setUsuarios] = useState<{ id: number; nombre: string }[]>([]);
  const [plantillas, setPlantillas] = useState<Plantilla[]>([]);
  const [clienteParaPlantilla, setClienteParaPlantilla] = useState<number | "">("");
  const [plantillaBase, setPlantillaBase] = useState<Plantilla | null>(null);

  useEffect(() => {
    fetchClientes("Activo").then((c) => setClientes(c ?? []));
    fetchEntrevistadores().then((u) => setUsuarios(u ?? []));
  }, []);

  useEffect(() => {
    if (paso !== "plantilla") return;
    fetchPlantillas(clienteParaPlantilla || undefined).then((p) => setPlantillas(p ?? []));
  }, [paso, clienteParaPlantilla]);

  // Punto 11: UN solo formulario de contenido, compartido con Configuración → Plantillas.
  const [contenido, setContenido] = useState<ContenidoVacante>(CONTENIDO_VACIO);

  function elegirPlantilla(p: Plantilla) {
    setPlantillaBase(p);
    setContenido(contenidoDesdePlantilla(p)); // precarga TODOS los campos (antes solo 5)
    if (p.clienteId) setClienteId(p.clienteId);
    setPaso("formulario");
  }

  // Parte 3 (decisión 1): con Clientes en la Cuenta, RH debe ELEGIR (un Cliente o «recluta directo»);
  // "" = todavía no eligió, 0 = la Cuenta recluta directo (sin Cliente).
  const [clienteId, setClienteId] = useState<number | "">("");
  const [responsableId, setResponsableId] = useState<number | "">("");
  const [colaboradoresIds, setColaboradoresIds] = useState<number[]>([]);
  const [mostrarCliente, setMostrarCliente] = useState(true);

  // Bloques de publicación por plataforma (occ/linkedin/portal) que solo produce el generador:
  // se conservan aparte del contenido editable para mandarlos en `publicaciones`.
  const [gen, setGen] = useState<VacanteGenerada | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState("");
  const [destinos, setDestinos] = useState<string[]>(["WhatsApp", "Portal"]);

  const faltaCliente = clientes.length > 0 && clienteId === "";

  async function guardar(publicar: boolean) {
    if (!contenido.titulo.trim()) {
      setError("El nombre del puesto es obligatorio.");
      return;
    }
    // Publicar exige los datos principales completos; un borrador puede quedar incompleto.
    if (publicar) {
      const faltan = faltantesDatosPrincipales(contenido);
      if (faltaCliente) faltan.splice(3, 0, "Cliente (o «La Cuenta recluta directo»)");
      if (faltan.length) {
        setError(`Para publicar, completa los datos principales: ${faltan.join(", ")}.`);
        return;
      }
    }
    setGuardando(true);
    setError("");
    const manual = tieneContenidoManual(contenido);
    const r = await crearVacante({
      ...contenidoComoPayload(contenido),
      publicaciones: gen
        ? {
            whatsapp: { titulo: contenido.titulo, copy: contenido.texto_whatsapp, page: contenido.texto_whatsapp, etiquetas: [] },
            occ: gen.occ,
            linkedin: gen.linkedin,
            portal: gen.portal,
          }
        : {},
      publicar,
      plataformas: destinos, // se guarda también en borrador; el estado decide si se publica
      // sin contenido capturado ni IA ni plantilla, la API genera el contenido al guardar
      generar_si_falta: !gen && !plantillaBase && !manual,
      cliente_id: clienteId || null,
      responsable_id: responsableId || null,
      colaboradores_ids: colaboradoresIds,
      mostrar_cliente_candidato: mostrarCliente,
      plantilla_id: plantillaBase?.id ?? null,
    });
    setGuardando(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    onGuardado(r.data.id);
  }

  if (paso === "elegir") {
    return (
      <Panel titulo="Nueva vacante" eyebrow="Distribuidor de vacantes" onClose={onClose} ancho="max-w-2xl">
        <div className="flex flex-col gap-4 p-6">
          <p className="text-sm text-ink-2">¿Cómo quieres empezar? Ninguna opción es obligatoria.</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <button
              onClick={() => {
                setPlantillaBase(null);
                setPaso("formulario");
              }}
              className="group flex flex-col items-start gap-3 rounded-2xl border border-border-soft bg-surface p-5 text-left transition hover:border-brand"
            >
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-brand-soft text-brand">
                <FileEdit className="h-5 w-5" />
              </span>
              <div>
                <p className="font-display text-base font-bold">Crear desde cero</p>
                <p className="mt-1 text-sm text-ink-3">Empieza con un formulario en blanco.</p>
              </div>
            </button>
            <button
              onClick={() => setPaso("plantilla")}
              className="group flex flex-col items-start gap-3 rounded-2xl border border-border-soft bg-surface p-5 text-left transition hover:border-brand"
            >
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-human-soft text-human">
                <Sparkles className="h-5 w-5" />
              </span>
              <div>
                <p className="font-display text-base font-bold">Usar plantilla</p>
                <p className="mt-1 text-sm text-ink-3">Precarga puesto, descripción, responsabilidades, requisitos, condiciones y criterios.</p>
              </div>
            </button>
          </div>
        </div>
      </Panel>
    );
  }

  if (paso === "plantilla") {
    return (
      <Panel titulo="Usar plantilla" eyebrow="Nueva vacante" onClose={onClose} ancho="max-w-2xl">
        <div className="flex flex-col gap-4 p-6">
          <button onClick={() => setPaso("elegir")} className="self-start text-xs font-medium text-ink-3 hover:text-brand">
            ← Volver
          </button>
          {clientes.length > 0 && (
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Cliente (opcional)</span>
              <select
                value={clienteParaPlantilla}
                onChange={(e) => setClienteParaPlantilla(e.target.value ? Number(e.target.value) : "")}
                className="h-11 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20"
              >
                <option value="">Sin Cliente (solo plantillas generales)</option>
                {clientes.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.nombre}
                  </option>
                ))}
              </select>
            </label>
          )}
          {plantillas.length === 0 ? (
            <Aviso tono="info">
              No hay plantillas {clienteParaPlantilla ? "para este Cliente ni generales" : "generales"} todavía. Se
              administran en Configuración → Plantillas; también puedes crear la vacante desde cero.
            </Aviso>
          ) : (
            <div className="flex flex-col gap-2.5">
              {plantillas.map((p) => (
                <button
                  key={p.id}
                  onClick={() => elegirPlantilla(p)}
                  className="flex items-center justify-between gap-3 rounded-xl border border-border-soft bg-surface p-4 text-left transition hover:border-brand"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-semibold">{p.nombre}</p>
                    <p className="truncate text-xs text-ink-3">{p.titulo || "Sin título precargado"}</p>
                  </div>
                  {p.clienteNombre ? <Badge tone="brand">{p.clienteNombre}</Badge> : <Badge tone="neutral">General</Badge>}
                </button>
              ))}
            </div>
          )}
        </div>
      </Panel>
    );
  }

  return (
    <Panel titulo="Nueva vacante" eyebrow="Distribuidor de vacantes" onClose={onClose} ancho="max-w-3xl">
      <div className="flex flex-col gap-6 p-6">
        {plantillaBase && (
          <Aviso tono="ok">
            Formulario precargado desde la plantilla «{plantillaBase.nombre}». Puedes editar cualquier campo.
          </Aviso>
        )}

        {/* Parte 3: Datos principales → Guía → Generar → Contenido → Selección viven en el formulario
            compartido; el Cliente (Fase B, punto 8) se inyecta dentro de Datos principales. Fase 4
            (Punto 1): sin "Empresa" en texto libre — el nombre lo resuelve el servidor. */}
        <FormularioContenidoVacante
          value={contenido}
          onChange={setContenido}
          onGenerado={setGen}
          clienteId={clienteId || null}
          mostrarCliente={mostrarCliente}
          faltaCliente={faltaCliente}
          slotDatosPrincipales={
            clientes.length > 0 ? (
              <>
                <label className="flex flex-col gap-1.5">
                  <span className="text-sm font-medium text-ink-2">Cliente *</span>
                  <select
                    value={clienteId}
                    onChange={(e) => setClienteId(e.target.value === "" ? "" : Number(e.target.value))}
                    className="h-11 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20"
                  >
                    <option value="">Elige…</option>
                    <option value="0">La Cuenta recluta directo (sin Cliente)</option>
                    {clientes.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.nombre}
                      </option>
                    ))}
                  </select>
                </label>
                {clienteId !== "" && clienteId !== 0 && (
                  <ToggleSiNo
                    label="Mostrar cliente al candidato"
                    ayuda="Si está en 'No', el candidato ve el nombre de tu Cuenta en vez del Cliente — internamente el equipo siempre ve la relación real."
                    valor={mostrarCliente}
                    onChange={setMostrarCliente}
                  />
                )}
              </>
            ) : null
          }
        />

        {/* Gestión — DESPUÉS de la Entrevista Red Human (Parte 3, punto 9) */}
        <div className="border-t border-border-faint pt-5">
          <Eyebrow>Gestión</Eyebrow>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-2">Responsable (opcional)</span>
            <select
              value={responsableId}
              onChange={(e) => setResponsableId(e.target.value ? Number(e.target.value) : "")}
              className="h-11 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20"
            >
              <option value="">Quien crea la vacante</option>
              {usuarios.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.nombre}
                </option>
              ))}
            </select>
          </label>
        </div>

        {usuarios.length > 0 && (
          <div>
            <Eyebrow>Colaboradores (opcional)</Eyebrow>
            <div className="mt-2.5 flex flex-wrap gap-2">
              {usuarios.map((u) => {
                const activo = colaboradoresIds.includes(u.id);
                return (
                  <button
                    key={u.id}
                    onClick={() =>
                      setColaboradoresIds((ids) => (activo ? ids.filter((x) => x !== u.id) : [...ids, u.id]))
                    }
                    className={cn(
                      "rounded-full border px-3 py-1.5 text-[13px] font-medium transition",
                      activo ? "border-brand bg-brand-soft text-brand" : "border-border-soft text-ink-2 hover:border-brand/40",
                    )}
                  >
                    {u.nombre}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {error && <Aviso tono="error">{error}</Aviso>}

        {gen && <ResultadoGeneracion gen={gen} />}

        {/* Publicación — al final del flujo (Parte 3, punto 10) */}
        <div className="border-t border-border-faint pt-5">
          <Eyebrow>Publicación · Canales</Eyebrow>
          <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
            {PLATAFORMAS.map((p) => {
              const activo = destinos.includes(p.api);
              return (
                <button
                  key={p.clave}
                  onClick={() => setDestinos((d) => (activo ? d.filter((x) => x !== p.api) : [...d, p.api]))}
                  className={cn(
                    "flex items-center gap-3 rounded-xl border p-3 text-left transition",
                    activo ? "border-brand bg-brand-soft/50" : "border-border-soft bg-surface hover:border-brand/40",
                  )}
                >
                  <span
                    className={cn(
                      "grid h-5 w-5 shrink-0 place-items-center rounded-md border",
                      activo ? "border-brand bg-brand text-brand-ink" : "border-border-soft",
                    )}
                  >
                    {activo && <Check className="h-3.5 w-3.5" />}
                  </span>
                  <div className="min-w-0">
                    <p className="text-sm font-semibold">{p.nombre}</p>
                    <p className="truncate text-xs text-ink-3">{p.nota}</p>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="flex items-center gap-3 border-t border-border-faint pt-5">
          <Button variant="outline" className="flex-1" onClick={() => guardar(false)} disabled={guardando}>
            Guardar borrador
          </Button>
          <Button className="flex-1" onClick={() => guardar(true)} disabled={guardando || destinos.length === 0}>
            {guardando ? "Publicando…" : "Publicar vacante"}
          </Button>
        </div>
      </div>
    </Panel>
  );
}

/* ---------------- Resultado del generador ---------------- */
function ResultadoGeneracion({ gen }: { gen: VacanteGenerada }) {
  const bloques: Record<string, BloquePlataforma> = useMemo(
    () => ({
      whatsapp: { titulo: "WhatsApp", copy: gen.texto_whatsapp, page: gen.texto_whatsapp, etiquetas: [] },
      occ: gen.occ,
      linkedin: gen.linkedin,
      portal: gen.portal,
    }),
    [gen],
  );

  return (
    <div className="flex flex-col gap-4">
      <Aviso tono={gen.ia ? "ok" : "warn"}>
        {gen.ia
          ? "Publicación generada con IA y adaptada al formato de cada plataforma."
          : "Publicación generada con plantilla (modo demo — agrega OPENAI_API_KEY en la API para IA real)."}
      </Aviso>

      {gen.avisos_cumplimiento.length > 0 && (
        <Card className="border-warn/30 bg-warn-soft/40 p-4">
          <span className="flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wider text-warn">
            <ShieldAlert className="h-3.5 w-3.5" /> Cumplimiento · revisa antes de publicar
          </span>
          <ul className="mt-2.5 space-y-1.5">
            {gen.avisos_cumplimiento.map((a, i) => (
              <li key={i} className="text-[13px] leading-relaxed text-ink-2">
                · {a}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <ContenidoBase gen={gen} />
      <PestanasPlataforma bloques={bloques} />
      <Criterios criterios={gen.preguntas_filtro} />
    </div>
  );
}

function ContenidoBase({ gen }: { gen: VacanteGenerada }) {
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-[11px] uppercase tracking-wider text-ink-3">Contenido base</span>
        <div className="flex items-center gap-2">
          <Badge tone="brand">{gen.seniority}</Badge>
          <Badge tone="neutral">{gen.rango_salarial_sugerido}</Badge>
        </div>
      </div>

      <p className="mt-3 text-[15px] font-medium leading-relaxed">{gen.resumen}</p>
      <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-ink-2">{gen.descripcion}</p>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <ListaCorta titulo="Responsabilidades" items={gen.responsabilidades} />
        <ListaCorta titulo="Indispensables" items={gen.requisitos_indispensables} />
        <ListaCorta titulo="Deseables" items={gen.requisitos_deseables} />
        <ListaCorta titulo="Ofrecemos" items={gen.beneficios} />
      </div>

      {gen.palabras_clave.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-1.5 border-t border-border-faint pt-3">
          {gen.palabras_clave.map((p) => (
            <span key={p} className="rounded-md bg-surface-2 px-2 py-0.5 font-mono text-[10px] text-ink-3">
              {p}
            </span>
          ))}
        </div>
      )}
    </Card>
  );
}

/** Copy y page de cada plataforma, en pestañas — es lo que RH pega en OCC / LinkedIn. */
function PestanasPlataforma({ bloques, liga }: { bloques: Record<string, BloquePlataforma>; liga?: string }) {
  const disponibles = BLOQUES_TEXTO.filter((p) => bloques[p.clave]?.page || bloques[p.clave]?.copy);
  const [activa, setActiva] = useState(disponibles[0]?.clave ?? "occ");
  const bloque = bloques[activa];

  if (!disponibles.length) return null;

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap gap-1 border-b border-border-faint bg-surface-2/50 p-1.5">
        {disponibles.map((p) => (
          <button
            key={p.clave}
            onClick={() => setActiva(p.clave)}
            className={cn(
              "rounded-lg px-3 py-1.5 text-[13px] font-medium transition",
              activa === p.clave ? "bg-surface text-ink shadow-sm" : "text-ink-3 hover:text-ink",
            )}
          >
            {p.nombre}
          </button>
        ))}
      </div>

      {bloque && (
        <div className="flex flex-col gap-4 p-4">
          <div>
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-[11px] uppercase tracking-wider text-ink-3">
                Título ({bloque.titulo.length} car.)
              </span>
              <BotonCopiar texto={bloque.titulo} />
            </div>
            <p className="mt-1.5 text-sm font-semibold">{bloque.titulo}</p>
          </div>

          <div>
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-[11px] uppercase tracking-wider text-ink-3">
                Copy · difusión ({bloque.copy.length} car.)
              </span>
              <BotonCopiar texto={liga ? `${bloque.copy}\n\n👉 Postúlate aquí: ${liga}` : bloque.copy} />
            </div>
            <pre className="mt-1.5 whitespace-pre-wrap break-words rounded-xl bg-surface-2 p-3 font-sans text-[13px] leading-relaxed text-ink-2">
              {bloque.copy}
            </pre>
          </div>

          <div>
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-[11px] uppercase tracking-wider text-ink-3">
                Page · publicación completa
              </span>
              <BotonCopiar texto={bloque.page} />
            </div>
            <pre className="mt-1.5 max-h-72 overflow-y-auto whitespace-pre-wrap break-words rounded-xl bg-surface-2 p-3 font-sans text-[13px] leading-relaxed text-ink-2">
              {bloque.page}
            </pre>
          </div>

          {bloque.etiquetas?.length > 0 && (
            <div>
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-[11px] uppercase tracking-wider text-ink-3">Etiquetas</span>
                <BotonCopiar texto={bloque.etiquetas.join(", ")} />
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {bloque.etiquetas.map((e) => (
                  <span key={e} className="rounded-md bg-brand-soft px-2 py-0.5 font-mono text-[10px] text-brand">
                    {e}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function Criterios({ criterios, titulo = "Criterios de prefiltro · postulación web" }: { criterios: CriterioFiltro[]; titulo?: string }) {
  if (!criterios?.length) return null;
  return (
    <Card className="p-4">
      <span className="font-mono text-[11px] uppercase tracking-wider text-ink-3">
        {titulo}
      </span>
      <ul className="mt-2.5 space-y-2">
        {criterios.map((q, i) => (
          <li key={i} className="flex items-start gap-2.5">
            <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-md bg-brand-soft font-mono text-[10px] font-bold text-brand">
              {i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm text-ink-2">{q.pregunta}</p>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-[10px] text-ink-3">
                <span>espera: {q.respuesta_esperada}</span>
                {q.descarta ? (
                  <span className="rounded bg-bad-soft px-1.5 py-0.5 text-bad">descarta</span>
                ) : (
                  <span className="rounded bg-surface-2 px-1.5 py-0.5">no descarta</span>
                )}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/* ============================================================
   Cliente / Responsable / Colaboradores — únicos campos editables del detalle (Fase B)
   ============================================================ */
function RelacionesVacante({ v, onCambio }: { v: Vacante; onCambio: () => void }) {
  const puedeDecidir = usePuedeDecidir();
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [usuarios, setUsuarios] = useState<{ id: number; nombre: string }[]>([]);
  const [guardando, setGuardando] = useState(false);

  useEffect(() => {
    fetchClientes("Activo").then((c) => setClientes(c ?? []));
    fetchEntrevistadores().then((u) => setUsuarios(u ?? []));
  }, []);

  async function guardar(cambios: Record<string, unknown>) {
    setGuardando(true);
    await actualizarVacante(v.id, cambios);
    setGuardando(false);
    onCambio();
  }

  if (!puedeDecidir) {
    return (
      <Card className="p-4 text-sm text-ink-2">
        <p>Cliente: {v.cliente ?? "— la Cuenta recluta directo —"}</p>
        <p className="mt-1">Responsable: {v.responsable ?? "—"}</p>
        {(v.colaboradores?.length ?? 0) > 0 && <p className="mt-1">Colaboradores: {v.colaboradores!.join(", ")}</p>}
      </Card>
    );
  }

  return (
    <Card className="flex flex-col gap-4 p-4">
      <Eyebrow>Cliente y responsables</Eyebrow>
      <div className="grid gap-4 sm:grid-cols-2">
        {clientes.length > 0 && (
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-2">Cliente</span>
            <select
              value={v.cliente ? clientes.find((c) => c.nombre === v.cliente)?.id ?? "" : ""}
              onChange={(e) => guardar({ cliente_id: e.target.value ? Number(e.target.value) : null })}
              disabled={guardando}
              className="h-11 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20"
            >
              <option value="">Sin Cliente — la Cuenta recluta directo</option>
              {clientes.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.nombre}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-ink-2">Responsable</span>
          <select
            value={usuarios.find((u) => u.nombre === v.responsable)?.id ?? ""}
            onChange={(e) => guardar({ responsable_id: e.target.value ? Number(e.target.value) : null })}
            disabled={guardando}
            className="h-11 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20"
          >
            <option value="">Sin asignar</option>
            {usuarios.map((u) => (
              <option key={u.id} value={u.id}>
                {u.nombre}
              </option>
            ))}
          </select>
        </label>
      </div>

      {usuarios.length > 0 && (
        <div>
          <span className="text-sm font-medium text-ink-2">Colaboradores</span>
          <div className="mt-2 flex flex-wrap gap-2">
            {usuarios.map((u) => {
              const activo = (v.colaboradores ?? []).includes(u.nombre);
              return (
                <button
                  key={u.id}
                  disabled={guardando}
                  onClick={() => {
                    const nombresActuales = v.colaboradores ?? [];
                    const idsActuales = usuarios.filter((x) => nombresActuales.includes(x.nombre)).map((x) => x.id);
                    const nuevos = activo ? idsActuales.filter((x) => x !== u.id) : [...idsActuales, u.id];
                    guardar({ colaboradores_ids: nuevos });
                  }}
                  className={cn(
                    "rounded-full border px-3 py-1.5 text-[13px] font-medium transition disabled:opacity-50",
                    activo ? "border-brand bg-brand-soft text-brand" : "border-border-soft text-ink-2 hover:border-brand/40",
                  )}
                >
                  {u.nombre}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {v.cliente && (
        <ToggleSiNo
          label="Mostrar cliente al candidato"
          ayuda="Si está en 'No', el candidato ve el nombre de tu Cuenta en vez del Cliente."
          valor={v.mostrarClienteCandidato ?? true}
          onChange={(valor) => guardar({ mostrar_cliente_candidato: valor })}
        />
      )}

      {/* Fase 4 (Punto 6): enfoque de la Entrevista IA por vacante; aplica a las entrevistas que se
          agenden después (el guion se genera al agendar). */}
      <div className="grid gap-4 border-t border-border-faint pt-4 sm:grid-cols-2">
        <Selector
          label="Enfoque de la Entrevista Red Human"
          value={v.enfoqueEntrevista ?? "profesional"}
          onChange={(valor) => guardar({ enfoque_entrevista: valor })}
          opciones={ENFOQUES_ENTREVISTA.map((e) => ({ valor: e.valor, texto: e.texto }))}
        />
        <p className="self-end pb-2 text-xs leading-relaxed text-ink-3">
          {ENFOQUES_ENTREVISTA.find((e) => e.valor === (v.enfoqueEntrevista ?? "profesional"))?.detalle}
        </p>
      </div>
    </Card>
  );
}

/* ============================================================
   Drawer: detalle de una vacante ya guardada
   ============================================================ */
/* Capacitación universal (2026-09-16): curso publicado que se asigna al candidato como filtro al quedar apto. */
function CursoFiltro({ v, onCambio }: { v: Vacante; onCambio: () => void }) {
  const [cursos, setCursos] = useState<{ id: string; titulo: string; estado: string }[]>([]);
  const [guardando, setGuardando] = useState(false);
  useEffect(() => {
    fetchCursos().then((c) => setCursos((c ?? []).filter((x) => x.estado === "Publicado")));
  }, []);
  async function cambiar(codigo: string) {
    setGuardando(true);
    await actualizarVacante(v.id, { curso_filtro: codigo });
    setGuardando(false);
    onCambio();
  }
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[11px] uppercase tracking-wide text-ink-3">Curso de filtro para candidatos</span>
      <select
        value={v.cursoFiltroId ?? ""}
        onChange={(e) => cambiar(e.target.value)}
        disabled={guardando}
        className="h-10 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand"
      >
        <option value="">Sin curso</option>
        {cursos.map((c) => (
          <option key={c.id} value={c.id}>{c.titulo}</option>
        ))}
      </select>
      <span className="text-[11px] text-ink-3">Se asigna solo cuando el candidato queda apto; su resultado aparece en su ficha.</span>
    </label>
  );
}

/* Evaluaciones (2026-09-28): la vacante solo SUGIERE evaluaciones y, si RH quiere, avisa al enviar a Onboarding
   cuando falte alguna o no esté revisada. Nunca asigna ni bloquea por sí sola. */
function EvaluacionesVacante({ v, editable, onCambio }: { v: Vacante; editable: boolean; onCambio: () => void }) {
  const [sugeridas, setSugeridas] = useState<{ tipo: string; prueba_id: number | null; nombre: string }[]>(v.evaluacionesSugeridas ?? []);
  const [avisar, setAvisar] = useState(Boolean(v.avisarEvaluacionesAntesOnboarding));
  const [pruebas, setPruebas] = useState<PruebaPsicometrica[]>([]);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (editable) fetchPruebasPsicometricas(false, v.titulo).then((p) => setPruebas(p ?? []));
  }, [editable, v.titulo]);
  async function guardar() {
    setGuardando(true);
    setError("");
    const r = await actualizarVacante(v.id, { evaluaciones_sugeridas: sugeridas, avisar_evaluaciones_antes_onboarding: avisar });
    setGuardando(false);
    if (!r.ok) return setError(r.error);
    onCambio();
  }
  if (!editable) {
    return (
      <p className="text-sm text-ink-2">
        {(v.evaluacionesSugeridas ?? []).map((x) => x.nombre).join(" · ") || "Sin evaluaciones sugeridas."}
        {v.avisarEvaluacionesAntesOnboarding ? " · Avisa antes de Onboarding" : ""}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {sugeridas.map((x, i) => (
        <div key={i} className="flex flex-wrap gap-2">
          <select
            value={x.tipo}
            onChange={(e) => setSugeridas(sugeridas.map((y, j) => (j === i ? { tipo: e.target.value, prueba_id: null, nombre: "" } : y)))}
            className="h-10 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand"
          >
            {TIPOS_EVALUACION.map((t) => <option key={t.valor} value={t.valor}>{t.texto}</option>)}
          </select>
          {x.tipo === "psicometrica" && (
            <select
              value={x.prueba_id ?? ""}
              onChange={(e) => setSugeridas(sugeridas.map((y, j) => (j === i ? { ...y, prueba_id: e.target.value ? Number(e.target.value) : null, nombre: "" } : y)))}
              className="h-10 min-w-0 flex-1 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand"
            >
              <option value="">Cualquier psicométrica</option>
              {pruebas.map((p) => <option key={p.id} value={p.id}>{p.nombre}{p.sugerida ? " · sugerida para el puesto" : ""}</option>)}
            </select>
          )}
          <Button size="sm" variant="ghost" onClick={() => setSugeridas(sugeridas.filter((_, j) => j !== i))} aria-label="Quitar"><X className="h-4 w-4" /></Button>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="outline" onClick={() => setSugeridas([...sugeridas, { tipo: "psicometrica", prueba_id: null, nombre: "" }])}>
          <Plus className="h-4 w-4" /> Sugerir evaluación
        </Button>
        <label className="flex items-center gap-2 text-sm text-ink-2">
          <input type="checkbox" checked={avisar} onChange={(e) => setAvisar(e.target.checked)} /> Avisar antes de Onboarding
        </label>
        <Button size="sm" onClick={guardar} disabled={guardando}>{guardando ? "Guardando…" : "Guardar"}</Button>
      </div>
      <p className="text-[11px] text-ink-3">Solo sugiere: RH las asigna desde la ficha del candidato. El aviso aparece al «Enviar a Onboarding» y nunca bloquea.</p>
      {error && <p className="text-sm font-semibold text-bad">{error}</p>}
    </div>
  );
}

/* Sección plegable CERRADA por defecto (regla de UI 2026-09-16: nada de "efecto libro"). */
function Plegable({ titulo, resumen, children }: { titulo: string; resumen?: string; children: React.ReactNode }) {
  const [abierto, setAbierto] = useState(false);
  return (
    <section className="rounded-xl border border-border-soft">
      <button type="button" onClick={() => setAbierto((a) => !a)} aria-expanded={abierto} className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left">
        <span className="min-w-0">
          <span className="text-sm font-semibold text-ink">{titulo}</span>
          {resumen && <span className="block truncate text-xs text-ink-3">{resumen}</span>}
        </span>
        {abierto ? <ChevronDown className="h-4 w-4 shrink-0 text-ink-3" /> : <ChevronRight className="h-4 w-4 shrink-0 text-ink-3" />}
      </button>
      {abierto && <div className="flex flex-col gap-4 border-t border-border-faint px-4 py-4">{children}</div>}
    </section>
  );
}

function ListaResumen({ titulo, items }: { titulo: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="text-[11px] uppercase tracking-wide text-ink-3">{titulo}</p>
      <ul className="mt-1 space-y-1">
        {items.map((x, i) => (
          <li key={i} className="text-sm leading-relaxed text-ink-2">· {x}</li>
        ))}
      </ul>
    </div>
  );
}

function DetalleVacante({
  v,
  live,
  onClose,
  onCambio,
  onVerPrevia,
  onEliminada,
}: {
  v: Vacante;
  live: boolean;
  onClose: () => void;
  onCambio: (codigo?: string) => void;
  onVerPrevia: () => void;
  onEliminada?: () => void;
}) {
  const [ocupado, setOcupado] = useState("");
  const [aviso, setAviso] = useState<{ tono: "ok" | "error"; texto: string } | null>(null);
  const puedeDecidir = usePuedeDecidir();
  // CRUD (2026-09-15): editar (formulario compartido en modo edición) y eliminar (baja lógica con confirmación)
  const [editando, setEditando] = useState(false);
  const [confirmarEliminar, setConfirmarEliminar] = useState(false);

  async function eliminar() {
    setOcupado("eliminar");
    const r = await eliminarVacante(v.id);
    setOcupado("");
    setConfirmarEliminar(false);
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    onEliminada?.();
  }
  // solo canales del catálogo actual: valores viejos (OCC, LinkedIn) no se preseleccionan
  const [destinos, setDestinos] = useState<string[]>(() => {
    const vigentes = v.plataformas.filter((p) => PLATAFORMAS.some((c) => c.api === p));
    return vigentes.length ? vigentes : ["Portal", "WhatsApp"];
  });

  const liga = v.slug && typeof window !== "undefined" ? `${window.location.origin}/aplicar/${v.slug}` : "";
  const bloques = (v.publicaciones ?? {}) as Record<string, BloquePlataforma>;
  const tieneContenido = Object.keys(bloques).length > 0;

  async function publicar() {
    setOcupado("publicar");
    const r = await publicarVacante(v.id, destinos);
    setOcupado("");
    setAviso(r.ok ? { tono: "ok", texto: `Publicada en ${destinos.join(", ")}.` } : { tono: "error", texto: r.error });
    if (r.ok) onCambio(v.id);
  }

  async function regenerar() {
    setOcupado("regenerar");
    const r = await regenerarVacante(v.id, "");
    setOcupado("");
    setAviso(
      r.ok
        ? { tono: "ok", texto: "Contenido regenerado para las cuatro plataformas." }
        : { tono: "error", texto: r.error },
    );
    if (r.ok) onCambio(v.id);
  }

  /** Switch de estatus: al cerrar, la vacante desaparece de /vacantes/publicas al instante (sin borrar su historial). */
  async function alternarEstatus() {
    setOcupado("estatus");
    const r = v.estado === "Publicada" ? await cerrarVacante(v.id) : await publicarVacante(v.id, destinos);
    setOcupado("");
    setAviso(
      r.ok
        ? {
            tono: "ok",
            texto:
              v.estado === "Publicada"
                ? "Vacante cerrada: ya no aparece en el portal público ni recibe nuevas postulaciones."
                : "Vacante reabierta: vuelve a aparecer en el portal público.",
          }
        : { tono: "error", texto: r.error },
    );
    if (r.ok) onCambio(v.id);
  }

  const [mostrarGuardarPlantilla, setMostrarGuardarPlantilla] = useState(false);
  const [nombrePlantilla, setNombrePlantilla] = useState("");
  const [alcancePlantilla, setAlcancePlantilla] = useState<"general" | "cliente">("general");
  const [guardandoPlantilla, setGuardandoPlantilla] = useState(false);

  /** «Guardar como plantilla» (Punto 11): el servidor copia los campos compartidos de esta
   * vacante — General de la Cuenta o del Cliente de la vacante, a elección. */
  async function guardarComoPlantilla() {
    if (!nombrePlantilla.trim()) return;
    setGuardandoPlantilla(true);
    const r = await guardarVacanteComoPlantilla(v.id, nombrePlantilla.trim(), alcancePlantilla === "cliente" ? v.clienteId ?? null : null);
    setGuardandoPlantilla(false);
    if (!r.ok) {
      setAviso({ tono: "error", texto: r.error });
      return;
    }
    setAviso({ tono: "ok", texto: `Plantilla "${r.data.nombre}" creada — se administra en Configuración → Plantillas y ya se sugiere en nuevas vacantes.` });
    setMostrarGuardarPlantilla(false);
    setNombrePlantilla("");
  }

  const embudo = v.embudo?.etapas ?? {};

  // Regla de UI (2026-09-16): resumen compacto arriba, UNA acción principal (Publicar / Cerrar-Reabrir),
  // secundarias en «…», y todo el contenido en secciones CERRADAS (Requisitos, Prefiltros, Entrevista,
  // Publicaciones, Gestión) — nada de "efecto libro".
  const puedeActuar = live && puedeDecidir && v.estado !== "Eliminada";
  const accionesMenu = [
    ...(live ? [{ etiqueta: "Vista previa (como la ve el candidato)", icono: <Eye />, onClick: onVerPrevia }] : []),
    ...(puedeActuar ? [{ etiqueta: "Editar vacante", icono: <Pencil />, onClick: () => setEditando(true), disabled: Boolean(ocupado) }] : []),
    ...(puedeActuar && v.estado !== "Cerrada" ? [{ etiqueta: "Regenerar con IA", icono: <RefreshCw />, onClick: regenerar, disabled: Boolean(ocupado) }] : []),
    ...(puedeActuar && tieneContenido ? [{ etiqueta: "Guardar como plantilla", icono: <FileEdit />, onClick: () => setMostrarGuardarPlantilla(true) }] : []),
    ...(puedeActuar && v.estado === "Publicada" ? [{ etiqueta: "Cerrar vacante", icono: <Ban />, onClick: alternarEstatus, disabled: Boolean(ocupado) }] : []),
    ...(puedeActuar && v.estado === "Cerrada" ? [{ etiqueta: "Reabrir vacante", icono: <RotateCcw />, onClick: alternarEstatus, disabled: Boolean(ocupado) }] : []),
    ...(puedeActuar ? [{ etiqueta: "Eliminar vacante", icono: <Trash2 />, peligrosa: true, onClick: () => setConfirmarEliminar(true), disabled: Boolean(ocupado) }] : []),
  ];
  const nPrefiltro = (v.criterios?.length ?? 0) + (v.criteriosWhatsapp?.length ?? 0);

  return (
    <Panel titulo={v.titulo} eyebrow={v.id} onClose={onClose} ancho="max-w-3xl">
      <div className="flex flex-col gap-4 p-6">
        {/* Resumen compacto */}
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={estadoTone[v.estado]} dot>
            {v.estado}
          </Badge>
          {v.seniority && <Badge tone="brand">{v.seniority}</Badge>}
          <Badge tone="neutral">{v.modalidad}</Badge>
          <span className="text-sm text-ink-2">
            {v.area} · {v.ubicacion} · <span className="font-mono text-brand">{v.sueldo}</span>
          </span>
          <MenuAcciones className="ml-auto" etiqueta="Más acciones de la vacante" acciones={accionesMenu} />
        </div>

        {v.estado === "Eliminada" && (
          <Aviso tono="warn">
            Esta vacante fue eliminada{v.eliminadaPor ? ` por ${v.eliminadaPor}` : ""}{v.eliminadaEn ? ` el ${textoFecha(v.eliminadaEn)}` : ""}.
            No aparece en tableros, portal ni WhatsApp; su historial se conserva.
          </Aviso>
        )}
        {Boolean(v.homonimasOtrasCuentas?.length) && (
          <Aviso tono="info">
            Hay otra vacante publicada con este mismo título en {v.homonimasOtrasCuentas!.map((h) => `${h.cuenta} (${h.codigo})`).join(", ")}.
            El portal general muestra las vacantes de todas las Cuentas; esa sigue visible aunque esta se elimine.
          </Aviso>
        )}

        {/* Embudo de esta vacante — conecta con el pipeline de candidatos (B4: clic → Kanban filtrado por vacante + etapa) */}
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          {["Prefiltro", "Entrevista IA", "Evaluación", "Entrevista Humana", "Contratación", "Onboarding"].map((e) => (
            <Link
              key={e}
              href={`/dashboard/candidatos?${new URLSearchParams({ vacante: v.id, etapa: e }).toString()}`}
              className="rounded-xl border border-border-soft bg-surface p-3 text-center transition hover:border-brand hover:bg-brand-soft/40"
              title={`Ver candidatos de esta vacante en ${nombreEtapa(e)}`}
            >
              <p className="font-display text-xl font-bold tabular">{embudo[e] ?? 0}</p>
              <p className="mt-0.5 text-[11px] text-ink-3">{nombreEtapa(e)}</p>
            </Link>
          ))}
        </div>
        <p className="-mt-1 text-[11px] text-ink-3">
          {Object.values(embudo).reduce((a, b) => a + b, 0)} candidatos activos en total · misma cuenta que el tablero de Candidatos.
        </p>

        {liga && (
          <div className="flex items-center gap-2 rounded-xl border border-border-soft bg-surface-2 px-3.5 py-2.5">
            <Link2 className="h-4 w-4 shrink-0 text-ink-3" />
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-2">{liga}</span>
            <BotonCopiar texto={liga} etiqueta="Copiar liga" />
            <a href={`/aplicar/${v.slug}`} target="_blank" rel="noreferrer" className="text-ink-3 transition hover:text-brand" aria-label="Abrir página de postulación">
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </div>
        )}

        {aviso && <Aviso tono={aviso.tono} onCerrar={() => setAviso(null)}>{aviso.texto}</Aviso>}

        {/* Acción principal: publicar (con destinos) mientras no esté publicada; publicada = indicador */}
        {puedeActuar && v.estado !== "Publicada" && v.estado !== "Cerrada" && (
          <div className="flex flex-col gap-3 rounded-xl border border-dashed border-brand/40 bg-brand-soft/30 p-4">
            <div className="flex flex-wrap gap-2">
              {PLATAFORMAS.map((p) => {
                const activo = destinos.includes(p.api);
                return (
                  <button
                    key={p.clave}
                    onClick={() => setDestinos((d) => (activo ? d.filter((x) => x !== p.api) : [...d, p.api]))}
                    className={cn(
                      "rounded-full border px-3 py-1.5 text-[13px] font-medium transition",
                      activo ? "border-brand bg-brand-soft text-brand" : "border-border-soft text-ink-2 hover:border-brand/40",
                    )}
                  >
                    {p.nombre}
                  </button>
                );
              })}
            </div>
            <Button className="w-full" onClick={publicar} disabled={Boolean(ocupado) || !destinos.length || !tieneContenido}>
              <Send className="h-4 w-4" />
              {ocupado === "publicar" ? "Publicando…" : "Publicar"}
            </Button>
            {!tieneContenido && <p className="text-xs text-ink-3">Sin contenido todavía: usa «Regenerar con IA» en el menú «…».</p>}
          </div>
        )}
        {puedeActuar && v.estado === "Publicada" && (
          <div className="flex items-center justify-center gap-2 rounded-xl border border-good/30 bg-good-soft px-4 py-2.5 text-sm font-semibold text-good">
            <Check className="h-4 w-4" /> Publicada · {ocupado === "estatus" ? "actualizando…" : "el candidato ya puede postularse"}
          </div>
        )}

        {mostrarGuardarPlantilla && (
          <div className="flex flex-wrap gap-2 rounded-xl border border-border-soft p-4">
            <input
              value={nombrePlantilla}
              onChange={(e) => setNombrePlantilla(e.target.value)}
              placeholder="Nombre de la plantilla"
              className="h-10 min-w-[200px] flex-1 rounded-xl border border-border-soft bg-surface px-3.5 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20"
            />
            {v.clienteId && (
              <select
                value={alcancePlantilla}
                onChange={(e) => setAlcancePlantilla(e.target.value as "general" | "cliente")}
                className="h-10 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand"
              >
                <option value="general">General de la Cuenta</option>
                <option value="cliente">Solo para {v.cliente || "este Cliente"}</option>
              </select>
            )}
            <Button size="sm" onClick={guardarComoPlantilla} disabled={guardandoPlantilla || !nombrePlantilla.trim()}>
              {guardandoPlantilla ? "Guardando…" : "Guardar"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setMostrarGuardarPlantilla(false)}>
              Cancelar
            </Button>
          </div>
        )}

        {(v.avisosCumplimiento?.length ?? 0) > 0 && (
          <Card className="border-warn/30 bg-warn-soft/40 p-4">
            <span className="flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wider text-warn">
              <ShieldAlert className="h-3.5 w-3.5" /> Cumplimiento
            </span>
            <ul className="mt-2 space-y-1.5">
              {v.avisosCumplimiento!.map((a, i) => (
                <li key={i} className="text-[13px] leading-relaxed text-ink-2">· {a}</li>
              ))}
            </ul>
          </Card>
        )}

        {/* Secciones CERRADAS por defecto */}
        <Plegable titulo="Requisitos" resumen={`${requisitosLista(v.requisitos).length} indispensables · ${v.requisitosDeseables?.length ?? 0} deseables · ${v.beneficios?.length ?? 0} prestaciones`}>
          {v.descripcion && <p className="text-sm leading-relaxed text-ink-2">{v.descripcion}</p>}
          <ListaResumen titulo="Responsabilidades" items={v.responsabilidades ?? []} />
          <ListaResumen titulo="Indispensables" items={requisitosLista(v.requisitos)} />
          <ListaResumen titulo="Deseables" items={v.requisitosDeseables ?? []} />
          <ListaResumen titulo="Prestaciones" items={v.beneficios ?? []} />
        </Plegable>

        <Plegable titulo="Prefiltros" resumen={`${nPrefiltro ? `${v.criterios?.length ?? 0} web · ${v.criteriosWhatsapp?.length ?? 0} WhatsApp` : "Sin preguntas todavía"}${v.cursoFiltroTitulo ? ` · curso: ${v.cursoFiltroTitulo}` : ""}`}>
          {live && puedeDecidir && <CursoFiltro v={v} onCambio={() => onCambio(v.id)} />}
          {(v.criterios?.length ?? 0) > 0 && <Criterios criterios={v.criterios as CriterioFiltro[]} />}
          {(v.criteriosWhatsapp?.length ?? 0) > 0 ? (
            <Criterios criterios={v.criteriosWhatsapp as CriterioFiltro[]} titulo="Criterios de prefiltro · WhatsApp (puntos críticos)" />
          ) : (v.criterios?.length ?? 0) > 0 ? (
            <p className="text-[12px] text-ink-3">WhatsApp: el agente confirma con las mismas preguntas de la web (no hay puntos críticos propios).</p>
          ) : null}
        </Plegable>

        <Plegable titulo="Entrevista" resumen={ENFOQUES_ENTREVISTA.find((e) => e.valor === (v.enfoqueEntrevista ?? "profesional"))?.texto ?? "Profesional"}>
          <p className="text-sm text-ink-2">{ENFOQUES_ENTREVISTA.find((e) => e.valor === (v.enfoqueEntrevista ?? "profesional"))?.detalle}</p>
          {v.perfilIdeal && (
            <div>
              <p className="text-[11px] uppercase tracking-wide text-ink-3">Perfil ideal</p>
              <p className="mt-1 text-sm leading-relaxed text-ink-2">{v.perfilIdeal}</p>
            </div>
          )}
        </Plegable>

        <Plegable
          titulo="Evaluaciones y verificaciones"
          resumen={`${(v.evaluacionesSugeridas ?? []).length ? `${(v.evaluacionesSugeridas ?? []).length} sugerida(s)` : "Sin sugerencias"}${v.avisarEvaluacionesAntesOnboarding ? " · avisa antes de Onboarding" : ""}`}
        >
          <EvaluacionesVacante v={v} editable={live && puedeDecidir} onCambio={() => onCambio(v.id)} />
        </Plegable>

        <Plegable titulo="Publicaciones" resumen={tieneContenido ? `${Object.keys(bloques).length} plataforma(s)${v.plataformas.length ? ` · distribuida en ${v.plataformas.join(", ")}` : ""}` : "Sin contenido generado"}>
          {tieneContenido ? (
            <PestanasPlataforma bloques={bloques} liga={liga} />
          ) : (
            <p className="text-sm text-ink-3">Esta vacante todavía no tiene publicación por plataforma. Genérala con «Regenerar con IA».</p>
          )}
        </Plegable>

        {live && (
          <Plegable titulo="Gestión" resumen={[v.cliente ? `Cliente: ${v.cliente}` : "Recluta directo", v.responsable ? `Responsable: ${v.responsable}` : ""].filter(Boolean).join(" · ")}>
            <RelacionesVacante v={v} onCambio={() => onCambio(v.id)} />
          </Plegable>
        )}
      </div>
      {editando && (
        <EditarVacante
          v={v}
          onClose={() => setEditando(false)}
          onGuardada={() => {
            setEditando(false);
            setAviso({ tono: "ok", texto: "Vacante actualizada." });
            onCambio(v.id);
          }}
        />
      )}
      {confirmarEliminar && (
        <ConfirmarEliminarVacante
          v={v}
          ocupado={ocupado === "eliminar"}
          onCancelar={() => setConfirmarEliminar(false)}
          onConfirmar={eliminar}
        />
      )}
    </Panel>
  );
}

/* ============================================================
   Piezas compartidas
   ============================================================ */
function Panel({
  titulo,
  eyebrow,
  onClose,
  children,
  ancho = "max-w-xl",
}: {
  titulo: string;
  eyebrow: string;
  onClose: () => void;
  children: React.ReactNode;
  ancho?: string;
}) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div
        className={cn(
          "relative flex h-full w-full flex-col overflow-y-auto border-l border-border-soft bg-bg shadow-2xl",
          ancho,
        )}
      >
        <div className="glass sticky top-0 z-10 flex items-center justify-between gap-2 border-b border-border-soft px-4 py-3 sm:px-6 sm:py-4">
          <div className="min-w-0">
            <Eyebrow>{eyebrow}</Eyebrow>
            <h2 className="font-display truncate text-xl font-bold">{titulo}</h2>
          </div>
          <button
            onClick={onClose}
            className="grid h-9 w-9 shrink-0 place-items-center rounded-xl text-ink-2 hover:bg-surface-2"
            aria-label="Cerrar"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** Vista previa (Fase B, punto 12) — mismo layout conceptual que /aplicar/[slug]: título,
 * nombre de empresa ya resuelto por el backend (Cliente o Cuenta, según el flag), ubicación,
 * sueldo/modalidad, resumen/descripción/responsabilidades/beneficios. Logo de la Cuenta si
 * existe. Funciona con la vacante en Borrador — nunca es obligatoria para publicar. */
function VistaPreviaVacante({ codigo, onClose }: { codigo: string; onClose: () => void }) {
  const [v, setV] = useState<Vacante | null>(null);
  const [cargando, setCargando] = useState(true);

  useEffect(() => {
    fetchVistaPreviaVacante(codigo).then((data) => {
      setV(data);
      setCargando(false);
    });
  }, [codigo]);

  return (
    <Panel titulo="Vista previa" eyebrow="Cómo la ve el candidato" onClose={onClose} ancho="max-w-2xl">
      <div className="flex flex-col gap-5 p-6">
        {cargando && <p className="text-sm text-ink-3">Cargando…</p>}
        {!cargando && !v && <Aviso tono="error">No se pudo cargar la vista previa.</Aviso>}
        {v && (
          <Card className="p-6">
            {v.logoUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={v.logoUrl} alt={v.nombreEmpresa ?? ""} className="h-10 w-auto object-contain" />
            ) : (
              <span className="font-display text-lg font-bold text-brand">Red Human AI</span>
            )}
            <h2 className="font-display mt-4 text-2xl font-bold">{v.titulo}</h2>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-ink-2">
              <span className="flex items-center gap-1.5">
                <Building2 className="h-4 w-4 text-ink-3" /> {v.nombreEmpresa ?? v.empresa}
              </span>
              <span className="flex items-center gap-1.5">
                <MapPin className="h-4 w-4 text-ink-3" /> {v.ubicacion}
              </span>
              <span>{v.modalidad}</span>
              <span className="font-mono text-brand">{v.sueldo}</span>
            </div>
            {v.resumen && <p className="mt-4 text-[15px] font-medium leading-relaxed">{v.resumen}</p>}
            {v.descripcion && (
              <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-ink-2">{v.descripcion}</p>
            )}
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <ListaCorta titulo="Responsabilidades" items={v.responsabilidades ?? []} />
              <ListaCorta titulo="Ofrecemos" items={v.beneficios ?? []} />
            </div>
          </Card>
        )}
      </div>
    </Panel>
  );
}

function ListaCorta({ titulo, items }: { titulo: string; items: string[] }) {
  if (!items?.length) return null;
  return (
    <div>
      <p className="font-mono text-[10px] uppercase tracking-wider text-ink-3">{titulo}</p>
      <ul className="mt-1.5 space-y-1">
        {items.map((x, i) => (
          <li key={i} className="flex gap-1.5 text-[13px] leading-relaxed text-ink-2">
            <span className="text-brand">·</span>
            {x}
          </li>
        ))}
      </ul>
    </div>
  );
}

