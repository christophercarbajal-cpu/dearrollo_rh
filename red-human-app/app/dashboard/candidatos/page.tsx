"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  X,
  MapPin,
  Briefcase,
  MessageCircle,
  Video,
  ShieldCheck,
  ThumbsUp,
  ThumbsDown,
  FileText,
  FileCheck2,
  Sparkles,
  UploadCloud,
  Download,
  UserCheck,
  AlertTriangle,
  Send,
  Loader2,
  CheckCircle2,
  Filter,
  Search,
  GraduationCap,
  Award,
  Globe,
  RotateCw,
  Mail,
  Phone,
  CalendarClock,
  FlaskConical,
  User,
  LayoutGrid,
  List,
  ChevronDown,
  Building2,
  Clock,
  ArrowUpDown,
  Copy,
  Pencil,
  XCircle,
  RefreshCw,
  Trash2,
  ArrowRightLeft,
} from "lucide-react";
import { Card, Badge, Button, Avatar, Eyebrow, Progress } from "@/components/ui";
import { PageHeader, EstadoBadge, ScoreRing } from "@/components/dashboard/parts";
import { PerfilProfundoVista } from "@/components/dashboard/perfil-profundo";
import { Aviso, Dropzone, pesoLegible } from "@/components/dashboard/subida";
import {
  type Candidato,
  type EntrevistaHumana,
  type EtapaCandidato,
  type RecomendacionEntrevistaHumana,
  type ResultadoEntrevistaHumana,
  type TipoEntrevistador,
  type Vacante,
} from "@/lib/data";
import type { DocExpediente, NuevoIngreso } from "@/lib/phase2";
import {
  autorizarAlta,
  eliminarCandidato,
  cancelarEntrevistaHumana,
  cancelarExpediente,
  decidirCandidato,
  enviarPrefiltro,
  fetchCandidato,
  fetchCandidatos,
  urlPreviewCorreo,
  urlContratoPdf,
  enviarCartaIntencion,
  urlCartaIntencionPdf,
  fetchClientes,
  fetchEntrevistadores,
  evaluarEntrevistaConLoQueHay,
  fetchExpediente,
  fetchMensajes,
  fetchVacantes,
  guardarCondicionesContratacion,
  fetchRazonesSociales,
  type RazonSocial,
  reiniciarPostulacionPrueba,
  type NotificarAccion,
  marcarEntrevistaHumanaRealizada,
  modificarEntrevistaHumana,
  moverEtapaCandidato,
  avanzarAEntrevistaHumana,
  programarEntrevistaHumana,
  reanalizarCvCandidato,
  recordatorioDocumentosCandidato,
  recordatorioEntrevistaHumana,
  registrarConsentimiento,
  registrarResultadoEntrevistaHumana,
  solicitarDocumentosCandidato,
  subirArchivoCandidato,
  subirCVs,
  subirDocumento,
  urlArchivoCandidato,
  urlCartaIntencion,
  urlDocumento,
  type CargaCV,
  type Cliente,
  type MensajePrefiltro,
  type ModalidadEntrevistaHumana,
  type PerfilProfundo,
  nombreEtapa,
  fetchCliente,
  fetchIntegracionTeams,
  lineasResultados,
  type ContactoCliente,
  type Entrevistador,
  type ResultadoNotificacion,
} from "@/lib/api";
import { usePuedeDecidir, useModoPrueba } from "@/components/sesion";
import { useAnunciarContextoAgente } from "@/components/dashboard/agente/proveedor";
import { ConfirmacionAccion } from "@/components/dashboard/confirmacion-accion";
import { LineaNotificar, useNotificarAccion } from "@/components/dashboard/linea-notificar";
import { MenuAcciones } from "@/components/dashboard/menu-acciones";
import { ModalIniciarOnboarding } from "@/components/dashboard/onboarding/iniciar-onboarding";
import { PanelTareasOnboarding } from "@/components/dashboard/onboarding/tareas-onboarding";
import { ModalAgregarEvaluacion, PanelEvaluaciones } from "@/components/dashboard/evaluaciones/panel-evaluaciones";
import { ClipboardCheck as IconoEvaluacion, PenLine as IconoFirma } from "lucide-react";
import { abrirFirmaEmbebida } from "@/lib/firma-embebida";
import { asegurarExpediente, crearFirmaDocumento, fetchEstadoFirmas, fetchFirmasExpediente, type FirmaDocumento } from "@/lib/api";
import { SwitchModoPrueba } from "@/components/dashboard/switch-modo-prueba";
import { Toast, type ToastMsg } from "@/components/dashboard/toast";
import { INTERVALO_TABLERO_MS, usePolling } from "@/lib/use-polling";
import { cn, etiquetaRecordatorio } from "@/lib/utils";
import { ETIQUETA_ZONA, partesLocales, textoCita, textoFecha, textoFechaHora } from "@/lib/fechas";

const etapas: EtapaCandidato[] = [
  "Prefiltro",
  "Entrevista IA",
  "Evaluación",
  "Entrevista Humana",
  "Contratación",
  "Onboarding",
];
const etapaColor: Record<EtapaCandidato, string> = {
  Prefiltro: "var(--ink-3)",
  "Entrevista IA": "var(--brand)",
  Evaluación: "var(--human)",
  "Entrevista Humana": "var(--brand-2)",
  Contratación: "var(--warn)",
  Onboarding: "var(--good)",
};

/** Zero-touch: la IA ya avanzó sola al candidato hasta aquí; esto es solo el siguiente
 * checkpoint humano al que RH puede mandarlo con un botón explícito (no "cualquier etapa
 * futura" — cada etapa tiene un único destino manual). "Entrevista Humana" abre el modal de
 * agenda (no hace PATCH directo); el resto va por PATCH /candidatos/{codigo}/etapa. Prefiltro,
 * Entrevista IA y Onboarding no tienen destino manual aquí — Prefiltro solo descarta (la IA
 * dispara Entrevista IA sola), Entrevista IA solo descarta, y a Onboarding solo se llega con
 * el botón "Enviar a Onboarding" de la propia etapa Contratación. */
const SIGUIENTE_ETAPA_MANUAL: Partial<Record<EtapaCandidato, EtapaCandidato[]>> = {
  Evaluación: ["Entrevista Humana"],
  "Entrevista Humana": ["Contratación"],
};

type FiltroEstado = "todos" | "en_proceso" | "aptos" | "contratados" | "descartados";

const FILTROS_ESTADO: { key: FiltroEstado; label: string }[] = [
  { key: "todos", label: "Todos" },
  { key: "en_proceso", label: "En proceso" },
  { key: "aptos", label: "Aptos" },
  { key: "contratados", label: "Contratados" },
  { key: "descartados", label: "No cumple" },  // recomendación del prefiltro; descartar sigue siendo decisión de RH
];

const ETAPAS_YA_CONTRATADO: EtapaCandidato[] = ["Contratación", "Onboarding"];

/** 2026-09-22 — «Avanzar a Entrevista Humana» (omitir la Entrevista Red Human). Solo tiene sentido
 * mientras el candidato está en Prefiltro o en la propia Entrevista Red Human. */
const ETAPAS_AVANCE_DIRECTO: EtapaCandidato[] = ["Prefiltro", "Entrevista IA"];
const TEXTO_AVANCE_DIRECTO = "Este candidato avanzará a Entrevista Humana y se omitirá la Entrevista Red Human";

const TIPOS_CONTRATACION = ["Tiempo indeterminado", "Tiempo determinado", "Por obra o proyecto", "Honorarios"];
// 2026-09-20 (B3): formato de la trazabilidad de documentos
function fechaHoraCorta(iso: string): string {
  return textoFechaHora(iso, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) || iso;
}
function canalLegible(canal: string): string {
  return canal
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => ({ whatsapp: "WhatsApp", correo: "Correo", liga: "Liga pública", rh: "RH (tablero)", fisico: "Entrega física" }[x] ?? x))
    .join(" + ") || "—";
}
// 2026-09-20 (B2): «Tiempo determinado» pide duración + unidad; la fecha de término se calcula (aquí solo como
// vista previa; la que vale es la del servidor, `expedienteCondiciones.fechaTermino`).
const UNIDADES_DURACION = ["días", "meses", "años"] as const;
function fechaTerminoLocal(fechaIngreso: string, duracion: number, unidad: string): string {
  if (!fechaIngreso || !duracion || duracion <= 0) return "";
  const [y, m, d] = fechaIngreso.split("-").map(Number);
  if (!y || !m || !d) return "";
  let fin: Date;
  if (unidad === "días") fin = new Date(Date.UTC(y, m - 1, d + duracion));
  else {
    const meses = unidad === "años" ? duracion * 12 : duracion;
    const total = m - 1 + meses;
    const anio = y + Math.floor(total / 12);
    const mes = total % 12;
    const ultimo = new Date(Date.UTC(anio, mes + 1, 0)).getUTCDate();
    fin = new Date(Date.UTC(anio, mes, Math.min(d, ultimo)));
  }
  return fin.toISOString().slice(0, 10);
}
const MODALIDADES_ENTREVISTA_HUMANA: ModalidadEntrevistaHumana[] = ["Presencial", "Videollamada", "Llamada"];

/** Usado tanto por PanelEntrevistaHumana (agenda/resultado) como por PestanaEvaluaciones
 * (vista de solo lectura del mismo resultado) — una sola fuente para el label. */
const RECOMENDACION_LABEL: Record<RecomendacionEntrevistaHumana, string> = {
  avanzar: "Avanzar",
  no_avanzar: "No avanzar",
  segunda_entrevista: "Segunda entrevista",
};

/** Clases completas y estáticas por tono de pestaña — Tailwind necesita ver el nombre de la
 * clase literal en el código para generarla; un template literal tipo `text-${tone}` no
 * funciona (ver toneMap en components/ui.tsx, mismo patrón). */
const TAB_TONE_ACTIVA: Record<string, string> = {
  brand: "bg-bg text-brand border-brand shadow-sm",
  human: "bg-bg text-human border-human shadow-sm",
  good: "bg-bg text-good border-good shadow-sm",
  warn: "bg-bg text-warn border-warn shadow-sm",
};
const TAB_TONE_BADGE: Record<string, string> = {
  brand: "bg-brand/15 text-brand",
  human: "bg-human/15 text-human",
  good: "bg-good/15 text-good",
  warn: "bg-warn/15 text-warn",
};

/** Quita acentos y pasa a minúsculas para que "jose" encuentre "José" en la búsqueda por nombre. */
function normalizarTexto(s: string): string {
  return s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

/** Formatea una fecha ISO a formato corto legible (ej. "10 sep, 14:30") */
function fechaCorta(iso: string | null | undefined): string | null {
  return textoFechaHora(iso, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) || null;
}

/** HOTFIX 2026-09-22 — «Todos» muestra TODAS las postulaciones cargadas, incluidas las que el agente
 * marcó «no cumple». `Postulacion.estado` es solo la RECOMENDACIÓN del prefiltro: la postulación sigue
 * ACTIVA y en su etapa hasta que una persona de RH la descarte (LFPDPPP/HITL), y así la cuenta el backend
 * (`services/conteos.py`, B4). Ocultarlas aquí dejaba tarjetas invisibles en Prefiltro mientras el contador
 * de la vacante decía 3, 5, 12… Las cerradas siguen fuera hasta activar «Mostrar cerradas» (eso lo filtra
 * la API, no esta función); el chip «No cumple» sigue disponible para revisarlas aparte. */
function coincideEstado(c: Candidato, filtro: FiltroEstado): boolean {
  const yaContratado = ETAPAS_YA_CONTRATADO.includes(c.etapa);
  switch (filtro) {
    case "en_proceso":
      return !yaContratado && (c.estado === "revision" || c.estado === "pendiente");
    case "aptos":
      return !yaContratado && c.estado === "cumple";
    case "contratados":
      return yaContratado;
    case "descartados":
      return c.estado === "no_cumple";
    case "todos":
    default:
      return true;
  }
}

function CandidatosContenido() {
  const puedeDecidir = usePuedeDecidir();
  const modoPrueba = useModoPrueba();
  const searchParams = useSearchParams();

  const [sel, setSel] = useState<Candidato | null>(null);
  // 2026-09-22: «Avanzar a Entrevista Humana» desde la tarjeta del Kanban (misma confirmación que la ficha)
  const [avanceKanban, setAvanceKanban] = useState<Candidato | null>(null);
  const [avanzando, setAvanzando] = useState(false);
  useAnunciarContextoAgente(
    sel ? { pantalla: "candidato", entidad: { tipo: "candidato", codigo: sel.id } } : { pantalla: "candidatos" },
  );
  // 2026-09-15 (arranque en vivo): el tablero NUNCA arranca con datos de ejemplo — antes se pintaban
  // las tarjetas demo un instante («flasheo») hasta que llegaba la respuesta real.
  const [datos, setDatos] = useState<Candidato[]>([]);
  const [cargando, setCargando] = useState(true);
  const [vacantes, setVacantes] = useState<Vacante[]>([]);
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [usuarios, setUsuarios] = useState<{ id: number; nombre: string }[]>([]);

  // Filtros principales
  const [filtroVacante, setFiltroVacante] = useState<string>("");
  const [filtroEstado, setFiltroEstado] = useState<FiltroEstado>("todos");
  const [busqueda, setBusqueda] = useState("");
  const [live, setLive] = useState(false);
  const [carga, setCarga] = useState(false);

  // Fase C: Vistas, URL params y filtros avanzados
  const [vista, setVista] = useState<"pipeline" | "lista">("pipeline");
  const [columnaResaltada, setColumnaResaltada] = useState<string | null>(null);
  const [filtrosAvanzados, setFiltrosAvanzados] = useState(false);
  const [fCliente, setFCliente] = useState<number | "">("");
  const [fResponsable, setFResponsable] = useState<number | "">("");
  const [fFuente, setFFuente] = useState<string>("");
  const [fConsentimiento, setFConsentimiento] = useState<"todos" | "con" | "sin">("todos");
  const [fApto, setFApto] = useState<"todos" | "apto" | "no_apto" | "sin_evaluar">("todos");
  const [fScoreMin, setFScoreMin] = useState<number | "">("");
  const [fScoreMax, setFScoreMax] = useState<number | "">("");
  const [fDuplicados, setFDuplicados] = useState(false);
  // Fase 2 (B4): las postulaciones cerradas (descartado/contratado/reinicio) no se cargan salvo
  // que RH lo pida explícitamente — es un parámetro de la API, no un filtro local.
  const [mostrarCerradas, setMostrarCerradas] = useState(false);
  const [orden, setOrden] = useState<"actividad" | "fecha" | "score" | "nombre">("actividad");

  // Inicialización desde URL params y localStorage
  useEffect(() => {
    const vParam = searchParams.get("vacante");
    if (vParam) setFiltroVacante(vParam);

    const eParam = searchParams.get("etapa");
    if (eParam && etapas.includes(eParam as EtapaCandidato)) {
      setColumnaResaltada(eParam);
      setTimeout(() => {
        const el = document.getElementById(`columna-etapa-${eParam.replace(/\s/g, "-")}`);
        if (el) el.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
      }, 200);
    }

    const guardada = localStorage.getItem("rh-candidatos-vista");
    if (guardada === "pipeline" || guardada === "lista") {
      setVista(guardada);
    }
  }, [searchParams]);

  const cambiarVista = (nueva: "pipeline" | "lista") => {
    setVista(nueva);
    try {
      localStorage.setItem("rh-candidatos-vista", nueva);
    } catch {}
  };

  const recargar = useCallback(async (abrirCodigo?: string) => {
    const c = await fetchCandidatos({
      ...(filtroVacante ? { vacante: filtroVacante } : {}),
      ...(mostrarCerradas ? { mostrar_cerradas: true } : {}),
    });
    if (c) {
      setDatos(c);
      setLive(true);
      if (abrirCodigo && c.length) {
        const detalle = await fetchCandidato(abrirCodigo);
        if (detalle) setSel(detalle);
      }
    }
    setCargando(false);
  }, [filtroVacante, mostrarCerradas]);

  useEffect(() => {
    recargar();
    fetchVacantes().then((v) => v && setVacantes(v));
    fetchClientes("Activo").then((cl) => setClientes(cl ?? []));
    fetchEntrevistadores().then((u) => setUsuarios(u ?? []));
  }, [recargar]);
  // Fase 4: el Kanban se revalida solo (WhatsApp, IA y otros usuarios mueven tarjetas) — se pausa
  // mientras hay una ficha abierta para no pisar lo que RH está editando.
  usePolling(() => recargar(), INTERVALO_TABLERO_MS, sel === null);

  async function abrir(c: Candidato) {
    setSel(c);
    if (!live) return;
    const detalle = await fetchCandidato(c.id);
    if (detalle) setSel(detalle);
  }

  // Detección de duplicados en el conjunto cargado (por teléfono normalizado a 10 dígitos o correo).
  // 2026-09-16 (Modo Prueba flexible): con Modo Prueba activo repetir teléfono/correo es lo esperado
  // (cada alta es una persona independiente), así que no se marca nada como «Duplicado».
  const duplicadosSet = useMemo(() => {
    const telMap = new Map<string, number>();
    const emailMap = new Map<string, number>();
    if (modoPrueba) return new Set<string>();

    for (const c of datos) {
      const t = c.telefono ? c.telefono.replace(/\D/g, "").slice(-10) : "";
      if (t.length >= 7) telMap.set(t, (telMap.get(t) ?? 0) + 1);
      const m = c.correo ? c.correo.trim().toLowerCase() : "";
      if (m) emailMap.set(m, (emailMap.get(m) ?? 0) + 1);
    }

    const dups = new Set<string>();
    for (const c of datos) {
      const t = c.telefono ? c.telefono.replace(/\D/g, "").slice(-10) : "";
      const m = c.correo ? c.correo.trim().toLowerCase() : "";
      if ((t.length >= 7 && (telMap.get(t) ?? 0) > 1) || (m && (emailMap.get(m) ?? 0) > 1)) {
        dups.add(c.id);
      }
    }
    return dups;
  }, [datos, modoPrueba]);

  const sinConsentimiento = datos.filter((c) => c.consentimiento === false).length;

  // Filtrado y ordenamiento compuesto
  const datosFiltrados = useMemo(() => {
    let res = datos.filter((c) => {
      if (filtroVacante && c.vacanteId !== filtroVacante) return false;
      if (columnaResaltada && c.etapa !== columnaResaltada) return false;  // B4: ?etapa= es un filtro exacto
      if (!coincideEstado(c, filtroEstado)) return false;
      if (
        busqueda.trim() &&
        !normalizarTexto(c.nombre).includes(normalizarTexto(busqueda)) &&
        !normalizarTexto(c.id).includes(normalizarTexto(busqueda))
      ) {
        return false;
      }
      if (fCliente !== "") {
        const v = vacantes.find((vac) => vac.id === c.vacanteId);
        const cliObj = clientes.find((cl) => cl.id === fCliente);
        const cliNombre = cliObj?.nombre;
        if (cliNombre && c.clienteVacante !== cliNombre && v?.cliente !== cliNombre) {
          return false;
        }
      }
      if (fResponsable !== "") {
        const v = vacantes.find((vac) => vac.id === c.vacanteId);
        const uObj = usuarios.find((u) => u.id === fResponsable);
        const uNombre = uObj?.nombre;
        if (uNombre && v?.responsable !== uNombre) {
          return false;
        }
      }
      if (fFuente && c.fuente !== fFuente) return false;
      if (fConsentimiento === "con" && c.consentimiento !== true) return false;
      if (fConsentimiento === "sin" && c.consentimiento !== false) return false;
      if (fApto === "apto" && c.resultadoApto !== true) return false;
      if (fApto === "no_apto" && c.resultadoApto !== false) return false;
      if (fApto === "sin_evaluar" && c.resultadoApto != null) return false;
      if (fScoreMin !== "" && (c.score ?? 0) < Number(fScoreMin)) return false;
      if (fScoreMax !== "" && (c.score ?? 0) > Number(fScoreMax)) return false;
      if (fDuplicados && !duplicadosSet.has(c.id)) return false;
      return true;
    });

    res = [...res].sort((a, b) => {
      if (orden === "actividad") {
        const ta = a.ultimaActividadEn ? new Date(a.ultimaActividadEn).getTime() : a.aplicado ? new Date(a.aplicado).getTime() : 0;
        const tb = b.ultimaActividadEn ? new Date(b.ultimaActividadEn).getTime() : b.aplicado ? new Date(b.aplicado).getTime() : 0;
        return tb - ta;
      }
      if (orden === "fecha") {
        const ta = a.aplicado ? new Date(a.aplicado).getTime() : 0;
        const tb = b.aplicado ? new Date(b.aplicado).getTime() : 0;
        return tb - ta;
      }
      if (orden === "score") {
        return (b.score ?? 0) - (a.score ?? 0);
      }
      if (orden === "nombre") {
        return a.nombre.localeCompare(b.nombre);
      }
      return 0;
    });

    return res;
  }, [
    columnaResaltada,
    datos,
    filtroVacante,
    filtroEstado,
    busqueda,
    fCliente,
    fResponsable,
    fFuente,
    fConsentimiento,
    fApto,
    fScoreMin,
    fScoreMax,
    fDuplicados,
    orden,
    vacantes,
    clientes,
    duplicadosSet,
  ]);

  const vacanteSeleccionada = vacantes.find((v) => v.id === filtroVacante);
  const totalFiltrosAvanzadosActivos =
    (fCliente !== "" ? 1 : 0) +
    (fResponsable !== "" ? 1 : 0) +
    (fFuente ? 1 : 0) +
    (fConsentimiento !== "todos" ? 1 : 0) +
    (fApto !== "todos" ? 1 : 0) +
    (fScoreMin !== "" || fScoreMax !== "" ? 1 : 0) +
    (fDuplicados ? 1 : 0);

  const limpiarTodosLosFiltros = () => {
    setFiltroVacante("");
    setFiltroEstado("todos");
    setBusqueda("");
    setFCliente("");
    setFResponsable("");
    setFFuente("");
    setFConsentimiento("todos");
    setFApto("todos");
    setFScoreMin("");
    setFScoreMax("");
    setFDuplicados(false);
    setColumnaResaltada(null);
  };

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6 sm:py-8">
      <PageHeader title="Candidatos" subtitle="Pipeline de selección · prefiltrado por el agente con evidencia y extracción de CV.">
        {live && (
          <Badge tone="good" dot>
            API en vivo
          </Badge>
        )}
        <Badge tone="brand" dot>
          <Sparkles className="h-3 w-3" /> Agente activo
        </Badge>
        {puedeDecidir && (
          <Button size="sm" onClick={() => setCarga(true)}>
            <UploadCloud className="h-4 w-4" /> Cargar CVs
          </Button>
        )}
      </PageHeader>

      {/* Barra principal de control: selector de vista, vacante, búsqueda, estado y filtros */}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          {/* Toggle de vista (Pipeline vs Lista) */}
          <div className="flex items-center rounded-xl border border-border-soft bg-surface p-1 shadow-sm">
            <button
              id="candidatos-vista-pipeline"
              onClick={() => cambiarVista("pipeline")}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition",
                vista === "pipeline"
                  ? "bg-brand text-white shadow-sm"
                  : "text-ink-3 hover:bg-surface-2 hover:text-ink",
              )}
              title="Vista de Pipeline (Kanban)"
            >
              <LayoutGrid className="h-3.5 w-3.5" /> Pipeline
            </button>
            <button
              id="candidatos-vista-lista"
              onClick={() => cambiarVista("lista")}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition",
                vista === "lista"
                  ? "bg-brand text-white shadow-sm"
                  : "text-ink-3 hover:bg-surface-2 hover:text-ink",
              )}
              title="Vista en Lista detallada"
            >
              <List className="h-3.5 w-3.5" /> Lista
            </button>
          </div>

          {/* Filtro por vacante */}
          <div className="relative">
            <Filter className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
            <select
              id="filtro-vacante"
              value={filtroVacante}
              onChange={(e) => setFiltroVacante(e.target.value)}
              className="h-10 min-w-[240px] appearance-none rounded-xl border border-border-soft bg-surface pl-9 pr-8 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20"
            >
              <option value="">Todas las vacantes</option>
              {vacantes.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.titulo}{v.cliente ? ` · ${v.cliente}` : ""}{v.ubicacion ? ` (${v.ubicacion})` : ""}
                </option>
              ))}
            </select>
          </div>

          {/* Búsqueda por nombre o código */}
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
            <input
              type="text"
              value={busqueda}
              onChange={(e) => setBusqueda(e.target.value)}
              placeholder="Buscar por nombre o código…"
              className="h-10 min-w-[220px] rounded-xl border border-border-soft bg-surface pl-9 pr-8 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20"
            />
            {busqueda && (
              <button
                onClick={() => setBusqueda("")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-3 hover:text-ink"
                aria-label="Limpiar búsqueda"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>

          {/* Barra de filtro por estado */}
          <div className="scroll-x max-w-full items-center gap-1 rounded-xl border border-border-soft bg-surface-2/60 p-1">
            {FILTROS_ESTADO.map((f) => (
              <button
                key={f.key}
                onClick={() => setFiltroEstado(f.key)}
                className={cn(
                  "rounded-lg px-2.5 py-1 text-xs font-semibold transition",
                  filtroEstado === f.key
                    ? "bg-surface text-brand shadow-sm"
                    : "text-ink-3 hover:bg-surface/60 hover:text-ink",
                )}
              >
                {f.label}
              </button>
            ))}
          </div>

          {/* Botón desplegable de filtros avanzados */}
          <button
            onClick={() => setFiltrosAvanzados((prev) => !prev)}
            className={cn(
              "flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-semibold transition",
              filtrosAvanzados || totalFiltrosAvanzadosActivos > 0
                ? "border-brand bg-brand-soft text-brand"
                : "border-border-soft bg-surface text-ink-2 hover:border-brand/40",
            )}
          >
            <Filter className="h-3.5 w-3.5" />
            Filtros
            {totalFiltrosAvanzadosActivos > 0 && (
              <span className="flex h-4 min-w-[16px] items-center justify-center rounded-full bg-brand px-1 text-[10px] text-white">
                {totalFiltrosAvanzadosActivos}
              </span>
            )}
            <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", filtrosAvanzados && "rotate-180")} />
          </button>
        </div>

        {/* Contador y Orden */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 text-xs text-ink-3">
            <ArrowUpDown className="h-3.5 w-3.5" />
            <select
              value={orden}
              onChange={(e) => setOrden(e.target.value as typeof orden)}
              className="rounded-lg border border-border-soft bg-surface px-2 py-1 text-xs font-medium text-ink outline-none focus:border-brand"
            >
              <option value="actividad">Última actividad</option>
              <option value="fecha">Fecha aplicación</option>
              <option value="score">Mayor Score CV</option>
              <option value="nombre">Nombre (A-Z)</option>
            </select>
          </div>
          <Badge tone="brand" dot>
            {datosFiltrados.length} candidato{datosFiltrados.length !== 1 ? "s" : ""}
          </Badge>
        </div>
      </div>

      {/* Panel desplegable de Filtros Avanzados (Fase C) */}
      {filtrosAvanzados && (
        <Card className="mt-3 grid gap-3 border-border-soft bg-surface/90 p-4 sm:grid-cols-2 lg:grid-cols-6">
          {/* Cliente */}
          {clientes.length > 0 && (
            <div>
              <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-3">
                Cliente
              </label>
              <select
                value={fCliente}
                onChange={(e) => setFCliente(e.target.value === "" ? "" : Number(e.target.value))}
                className="w-full rounded-lg border border-border-soft bg-surface p-2 text-xs outline-none focus:border-brand"
              >
                <option value="">Todos los clientes</option>
                {clientes.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.nombre}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Responsable */}
          {usuarios.length > 0 && (
            <div>
              <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-3">
                Responsable
              </label>
              <select
                value={fResponsable}
                onChange={(e) => setFResponsable(e.target.value === "" ? "" : Number(e.target.value))}
                className="w-full rounded-lg border border-border-soft bg-surface p-2 text-xs outline-none focus:border-brand"
              >
                <option value="">Todos los responsables</option>
                {usuarios.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.nombre}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Fuente */}
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-3">
              Fuente
            </label>
            <select
              value={fFuente}
              onChange={(e) => setFFuente(e.target.value)}
              className="w-full rounded-lg border border-border-soft bg-surface p-2 text-xs outline-none focus:border-brand"
            >
              <option value="">Todas las fuentes</option>
              <option value="WhatsApp">WhatsApp</option>
              <option value="OCC">OCC</option>
              <option value="LinkedIn">LinkedIn</option>
              <option value="Portal">Portal</option>
              <option value="Carga CV">Carga CV</option>
            </select>
          </div>

          {/* Apto (Punto 21) */}
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-3">
              Resultado Apto
            </label>
            <select
              value={fApto}
              onChange={(e) => setFApto(e.target.value as typeof fApto)}
              className="w-full rounded-lg border border-border-soft bg-surface p-2 text-xs outline-none focus:border-brand"
            >
              <option value="todos">Todos los resultados</option>
              <option value="apto">Apto (Sí)</option>
              <option value="no_apto">No apto (No)</option>
              <option value="sin_evaluar">Sin evaluar</option>
            </select>
          </div>

          {/* Score CV (Rango) */}
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-3">
              Score CV (%)
            </label>
            <div className="flex items-center gap-1.5">
              <input
                type="number"
                min="0"
                max="100"
                placeholder="Mín"
                value={fScoreMin}
                onChange={(e) => setFScoreMin(e.target.value === "" ? "" : Math.max(0, Math.min(100, Number(e.target.value))))}
                className="w-full rounded-lg border border-border-soft bg-surface p-2 text-xs outline-none focus:border-brand"
              />
              <span className="text-xs text-ink-3">-</span>
              <input
                type="number"
                min="0"
                max="100"
                placeholder="Máx"
                value={fScoreMax}
                onChange={(e) => setFScoreMax(e.target.value === "" ? "" : Math.max(0, Math.min(100, Number(e.target.value))))}
                className="w-full rounded-lg border border-border-soft bg-surface p-2 text-xs outline-none focus:border-brand"
              />
            </div>
          </div>

          {/* Consentimiento */}
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-3">
              Consentimiento
            </label>
            <select
              value={fConsentimiento}
              onChange={(e) => setFConsentimiento(e.target.value as typeof fConsentimiento)}
              className="w-full rounded-lg border border-border-soft bg-surface p-2 text-xs outline-none focus:border-brand"
            >
              <option value="todos">Todos</option>
              <option value="con">Con consentimiento</option>
              <option value="sin">Sin consentimiento</option>
            </select>
          </div>

          {/* Duplicados */}
          <div className="flex flex-col justify-end gap-2 sm:col-span-2 lg:col-span-6">
            <div className="flex flex-wrap items-center justify-between border-t border-border-faint pt-2">
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="f-duplicados"
                  checked={fDuplicados}
                  onChange={(e) => setFDuplicados(e.target.checked)}
                  className="h-4 w-4 rounded border-border-soft text-brand focus:ring-brand"
                />
                <label htmlFor="f-duplicados" className="cursor-pointer text-xs font-medium text-ink-2">
                  Solo duplicados ({duplicadosSet.size})
                </label>
                <input
                  type="checkbox"
                  id="f-cerradas"
                  checked={mostrarCerradas}
                  onChange={(e) => setMostrarCerradas(e.target.checked)}
                  className="ml-4 h-4 w-4 rounded border-border-soft text-brand focus:ring-brand"
                />
                <label
                  htmlFor="f-cerradas"
                  title="Incluye postulaciones descartadas, contratadas o reiniciadas (quedan como historial de la persona)"
                  className="cursor-pointer text-xs font-medium text-ink-2"
                >
                  Mostrar cerradas
                </label>
              </div>
              {totalFiltrosAvanzadosActivos > 0 && (
                <button
                  onClick={limpiarTodosLosFiltros}
                  className="text-xs font-semibold text-brand hover:underline"
                >
                  Limpiar todos los filtros
                </button>
              )}
            </div>
          </div>
        </Card>
      )}

      {/* Avisos */}
      {columnaResaltada && (
        <div className="mt-3 flex items-center justify-between rounded-xl border border-brand/40 bg-brand-soft/40 px-4 py-2 text-xs text-brand">
          <span>
            Filtro por etapa: <strong>{nombreEtapa(columnaResaltada)}</strong>
            {filtroVacante ? <> · vacante <strong>{filtroVacante}</strong></> : null} · {datosFiltrados.length} candidato(s)
          </span>
          <button
            onClick={() => setColumnaResaltada(null)}
            className="flex items-center gap-1 font-semibold hover:underline"
          >
            <X className="h-3.5 w-3.5" /> Quitar filtro de etapa
          </button>
        </div>
      )}

      {live && sinConsentimiento > 0 && (
        <div className="mt-4">
          <Aviso tono="warn">
            {sinConsentimiento} candidato(s) sin consentimiento registrado. Sin él no se puede abrir expediente de
            contratación (LFPDPPP 2025).
          </Aviso>
        </div>
      )}

      {/* Estado de carga: esqueleto neutro (nunca tarjetas de ejemplo) hasta la primera respuesta real */}
      {cargando && (
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6" aria-busy="true">
          {etapas.map((etapa) => (
            <div key={etapa} className="rounded-2xl border border-border-soft bg-surface p-3">
              <div className="h-4 w-24 animate-pulse rounded bg-surface-2" />
              <div className="mt-3 h-20 animate-pulse rounded-xl bg-surface-2/60" />
            </div>
          ))}
        </div>
      )}

      {/* VISTA 1: PIPELINE (Kanban) */}
      {!cargando && vista === "pipeline" && (
        <div className={cn("mt-6 grid gap-4", columnaResaltada ? "grid-cols-1 sm:max-w-md" : "sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6")}>
          {etapas.filter((etapa) => !columnaResaltada || etapa === columnaResaltada).map((etapa) => {
            const cols = datosFiltrados.filter((c) => c.etapa === etapa);
            const esResaltada = columnaResaltada === etapa;

            return (
              <div
                key={etapa}
                id={`columna-etapa-${etapa.replace(/\s/g, "-")}`}
                className={cn(
                  "flex flex-col rounded-2xl border p-3 transition-all duration-300",
                  esResaltada
                    ? "border-brand bg-brand/5 ring-2 ring-brand/30 shadow-md"
                    : "border-border-soft bg-surface-2/40",
                )}
              >
                <div className="mb-3 flex items-center justify-between px-1">
                  <div className="flex items-center gap-2">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: etapaColor[etapa] }} />
                    <span className={cn("text-sm font-semibold", esResaltada && "text-brand")}>{nombreEtapa(etapa)}</span>
                  </div>
                  <span
                    className={cn(
                      "rounded-full px-2 py-0.5 font-mono text-[11px]",
                      esResaltada ? "bg-brand text-white font-bold" : "bg-surface text-ink-3",
                    )}
                  >
                    {cols.length}
                  </span>
                </div>

                <div className="flex flex-col gap-2.5">
                  {cols.map((c) => {
                    const esDup = duplicadosSet.has(c.id);
                    return (
                      /* 2026-09-22: el menú «…» va FUERA del botón de la tarjeta (no se anidan botones);
                         solo aparece donde tiene sentido avanzar directo a Entrevista Humana. */
                      <div key={c.id} className="relative">
                      {puedeDecidir && ETAPAS_AVANCE_DIRECTO.includes(c.etapa) && c.activa !== false && (
                        <div className="absolute right-1.5 top-1.5 z-10">
                          <MenuAcciones
                            etiqueta={`Acciones de ${c.nombre}`}
                            acciones={[{
                              etiqueta: "Avanzar a Entrevista Humana",
                              icono: <CalendarClock />,
                              title: TEXTO_AVANCE_DIRECTO,
                              onClick: () => setAvanceKanban(c),
                            }]}
                          />
                        </div>
                      )}
                      <button
                        onClick={() => abrir(c)}
                        className="card-hover group w-full rounded-xl border border-border-soft bg-surface p-3.5 text-left transition-all hover:border-brand/40 hover:shadow-md"
                      >
                        <div className="flex items-center gap-3">
                          <div className="relative">
                            <ScoreRing score={c.score} />
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-1.5 flex-wrap">
                              <p className="truncate text-sm font-semibold group-hover:text-brand">{c.nombre}</p>
                              {c.esPrueba && (
                                <span className="shrink-0 rounded bg-brand-soft px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-wide text-brand">
                                  Prueba
                                </span>
                              )}
                              {c.yaAplicoAntes && (
                                <span
                                  title={`Este candidato tiene ${c.totalPostulaciones} postulaciones`}
                                  className="shrink-0 rounded bg-blue-500/10 px-1.5 py-0.5 font-mono text-[9px] font-bold text-blue-600"
                                >
                                  🔄 Ya aplicó antes
                                </span>
                              )}
                              {c.activa === false && (
                                <span
                                  title={`Postulación cerrada (${c.motivoCierre || "sin motivo"}) — queda como historial de la persona`}
                                  className="shrink-0 rounded bg-ink-3/10 px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-wide text-ink-3"
                                >
                                  Cerrada
                                </span>
                              )}
                              {esDup && (
                                <span
                                  title="Posible candidato duplicado (coincide teléfono o correo)"
                                  className="shrink-0 rounded bg-warn-soft px-1.5 py-0.5 font-mono text-[9px] font-bold text-warn"
                                >
                                  Duplicado
                                </span>
                              )}
                            </div>
                            <p className="truncate text-xs text-ink-3">
                              {c.puesto || "Sin vacante"}
                              {c.clienteVacante ? ` · ${c.clienteVacante}` : ""}
                            </p>
                            <div className="mt-1 flex items-center gap-1.5">
                              <span className="rounded bg-brand/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-brand">
                                Score CV: {c.score}%
                              </span>
                            </div>
                          </div>
                        </div>

                        {/* Estado y Apto (Fase C) */}
                        <div className="mt-3 flex items-center justify-between">
                          <div className="flex items-center gap-1.5">
                            <EstadoBadge estado={c.estado} />
                            {c.resultadoApto === true && (
                              <span className="rounded-md bg-good-soft px-1.5 py-0.5 text-[10px] font-bold text-good">
                                Apto
                              </span>
                            )}
                            {c.resultadoApto === false && (
                              <span className="rounded-md bg-bad-soft px-1.5 py-0.5 text-[10px] font-bold text-bad">
                                No apto
                              </span>
                            )}
                          </div>
                          <span className="flex items-center gap-1 font-mono text-[10px] text-ink-3">
                            {c.fuente === "WhatsApp" ? (
                              <span className="inline-flex items-center gap-1 font-semibold text-good">
                                <MessageCircle className="h-3 w-3" /> WhatsApp
                              </span>
                            ) : (
                              c.fuente
                            )}
                          </span>
                        </div>

                        {/* Señales */}
                        <div className="mt-2 flex flex-wrap items-center gap-1.5">
                          {(c.archivos ?? 0) > 0 && (
                            <Pastilla icon={FileText} tono="neutral">
                              {c.archivos} CV/doc
                            </Pastilla>
                          )}
                          {(c.mensajes ?? 0) > 0 && (
                            <Pastilla icon={MessageCircle} tono="good">
                              {c.mensajes} msgs
                            </Pastilla>
                          )}
                          {c.entrevistaEstado === "evaluada" && (
                            <Pastilla icon={Video}>match {c.entrevistaMatch ?? "—"}</Pastilla>
                          )}
                          {c.expedienteId != null && (
                            <Pastilla icon={UserCheck} tono="good">
                              expediente {c.expedienteProgreso ?? 0}%
                            </Pastilla>
                          )}
                          {c.consentimiento === false && (
                            <Pastilla icon={AlertTriangle} tono="warn">
                              sin consentimiento
                            </Pastilla>
                          )}
                        </div>

                        {/* Fecha última actividad / aplicación */}
                        {(c.ultimaActividadEn || c.aplicado) && (
                          <div className="mt-2.5 flex items-center gap-1 border-t border-border-faint pt-2 text-[10px] text-ink-3">
                            <Clock className="h-3 w-3" />
                            <span>
                              {c.ultimaActividadEn
                                ? `Actividad ${fechaCorta(c.ultimaActividadEn)}`
                                : `Aplicó ${fechaCorta(c.aplicado)}`}
                            </span>
                          </div>
                        )}
                      </button>
                      </div>
                    );
                  })}
                  {cols.length === 0 && (
                    <div className="rounded-xl border border-dashed border-border-soft py-8 text-center text-xs text-ink-3">
                      Sin candidatos
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* VISTA 2: LISTA (Fase C) */}
      {!cargando && vista === "lista" && (
        <div className="mt-6 overflow-x-auto rounded-xl border border-border-soft bg-surface">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-soft bg-surface-2 text-xs font-semibold uppercase tracking-wide text-ink-3">
                <th className="px-4 py-3 text-left">Candidato</th>
                <th className="px-4 py-3 text-left">Vacante</th>
                <th className="px-4 py-3 text-left">Cliente</th>
                <th className="px-4 py-3 text-left">Etapa</th>
                <th className="px-4 py-3 text-left">Resultado Apto</th>
                <th className="px-4 py-3 text-left">Score CV</th>
                <th className="px-4 py-3 text-left">Fuente</th>
                <th className="px-4 py-3 text-left">Última Actividad</th>
                <th className="px-4 py-3 text-right">Acción</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-faint">
              {datosFiltrados.map((c) => {
                const esDup = duplicadosSet.has(c.id);
                return (
                  <tr
                    key={c.id}
                    onClick={() => abrir(c)}
                    className="cursor-pointer transition hover:bg-brand-soft/30"
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2.5">
                        <Avatar name={c.nombre} tone={c.tono} />
                        <div>
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <p className="font-semibold text-ink">{c.nombre}</p>
                            {c.esPrueba && (
                              <span className="rounded bg-brand-soft px-1 text-[9px] font-bold text-brand">
                                Prueba
                              </span>
                            )}
                            {c.yaAplicoAntes && (
                              <span
                                title={`Este candidato tiene ${c.totalPostulaciones} postulaciones`}
                                className="rounded bg-blue-500/10 px-1 text-[9px] font-bold text-blue-600"
                              >
                                🔄 Ya aplicó antes
                              </span>
                            )}
                            {c.activa === false && (
                              <span
                                title={`Postulación cerrada (${c.motivoCierre || "sin motivo"})`}
                                className="rounded bg-ink-3/10 px-1 text-[9px] font-bold uppercase text-ink-3"
                              >
                                Cerrada
                              </span>
                            )}
                            {esDup && (
                              <span
                                title="Posible candidato duplicado"
                                className="rounded bg-warn-soft px-1 text-[9px] font-bold text-warn"
                              >
                                Duplicado
                              </span>
                            )}
                          </div>
                          <p className="font-mono text-[11px] text-ink-3">{c.id}</p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-ink-2">
                      <p className="font-medium text-ink">{c.puesto || "—"}</p>
                      <p className="font-mono text-[11px] text-ink-3">{c.vacanteId}</p>
                    </td>
                    <td className="px-4 py-3 text-ink-2">
                      {c.clienteVacante || "—"}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold"
                        style={{
                          background: `${etapaColor[c.etapa]}18`,
                          color: etapaColor[c.etapa],
                        }}
                      >
                        <span className="h-1.5 w-1.5 rounded-full" style={{ background: etapaColor[c.etapa] }} />
                        {nombreEtapa(c.etapa)}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {c.resultadoApto === true && (
                        <Badge tone="good" dot>
                          Apto
                        </Badge>
                      )}
                      {c.resultadoApto === false && (
                        <Badge tone="bad" dot>
                          No apto
                        </Badge>
                      )}
                      {c.resultadoApto == null && (
                        <span className="text-xs text-ink-3">Sin evaluar</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-mono text-xs font-semibold text-ink">
                        {c.score != null ? `${c.score}%` : "—"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-ink-2">
                      {c.fuente === "WhatsApp" ? (
                        <span className="inline-flex items-center gap-1 font-semibold text-good">
                          <MessageCircle className="h-3.5 w-3.5" /> WhatsApp
                        </span>
                      ) : (
                        c.fuente || "—"
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-ink-3">
                      {c.ultimaActividadEn ? fechaCorta(c.ultimaActividadEn) : c.aplicado ? fechaCorta(c.aplicado) : "—"}
                    </td>
                    <td className="px-4 py-3 text-right" onClick={(e) => e.stopPropagation()}>
                      <Button size="sm" variant="secondary" onClick={() => abrir(c)}>
                        Ver detalle
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {datosFiltrados.length === 0 && (
            <div className="py-12 text-center text-sm text-ink-3">Sin candidatos con estos filtros.</div>
          )}
        </div>
      )}

      {carga && (
        <CargarCVs
          vacantes={vacantes}
          onClose={() => setCarga(false)}
          onListo={(codigo) => {
            recargar(codigo);
          }}
        />
      )}

      {/* Modal Centrado de Detalle del Candidato */}
      {sel && (
        <ModalCandidato
          c={sel}
          live={live}
          onClose={() => setSel(null)}
          onCambio={(actualizado) => {
            setSel(actualizado);
            recargar();
          }}
          onEliminado={() => {
            // CRUD: la persona ya no existe para el sistema → volver al tablero
            setSel(null);
            recargar();
          }}
        />
      )}

      {/* 2026-09-22: confirmación de «Avanzar a Entrevista Humana» desde la tarjeta del Kanban */}
      {avanceKanban && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={() => !avanzando && setAvanceKanban(null)}>
          <div className="w-full max-w-md rounded-3xl border border-border-soft bg-bg p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-display text-lg font-bold">Avanzar a Entrevista Humana</h3>
            <p className="mt-2 text-sm leading-relaxed text-ink-2">{TEXTO_AVANCE_DIRECTO}.</p>
            <p className="mt-2 text-xs text-ink-3">
              {avanceKanban.nombre} · queda registrado en el historial del expediente; lo ya generado se conserva.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setAvanceKanban(null)} disabled={avanzando}>Cancelar</Button>
              <Button
                size="sm"
                disabled={avanzando}
                onClick={async () => {
                  setAvanzando(true);
                  const r = await avanzarAEntrevistaHumana(avanceKanban.id);
                  setAvanzando(false);
                  if (!r.ok) return;
                  setAvanceKanban(null);
                  if (sel?.id === r.data.id) setSel(r.data);
                  recargar();
                }}
              >
                {avanzando ? "Avanzando…" : "Avanzar"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Candidatos() {
  return (
    <Suspense fallback={<div className="p-8 text-center text-sm text-ink-3">Cargando candidatos…</div>}>
      <CandidatosContenido />
    </Suspense>
  );
}

function Pastilla({
  icon: Icon,
  children,
  tono = "neutral",
}: {
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
  tono?: "neutral" | "good" | "warn";
}) {
  const tonos = {
    neutral: "bg-surface-2 text-ink-3",
    good: "bg-good-soft text-good",
    warn: "bg-warn-soft text-warn",
  };
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-[10px]", tonos[tono])}>
      <Icon className="h-3 w-3" />
      {children}
    </span>
  );
}

type TabCandidato = "resumen" | "evaluaciones" | "documentos" | "whatsapp" | "contratacion";

/** `reintentar` (Lote 4): presente solo en avisos de error de acciones que pueden toparse con
 * un bloqueo de estado forzable — el botón "Continuar de todos modos" solo se pinta si además
 * Modo Prueba está activo (ver useModoPrueba). */
type AvisoEstado = { tono: "ok" | "error" | "warn"; texto: string; reintentar?: () => void } | null;

/* ============================================================
   MODAL CENTRADO: Detalle del Candidato (4 pestañas + Contratación condicional)
   ============================================================ */
/** Texto del aviso tras una acción que notifica: qué salió y por qué canal, o que la regla
 * no tenía nada activo (Punto 12). */
function resumenEnvio(r: { ok: boolean; data?: { resultados?: { enviado: boolean }[] } }, base: string): string {
  const resultados = r.ok ? r.data?.resultados ?? [] : [];
  const enviados = resultados.filter((x) => x.enviado).length;
  if (resultados.length === 0) return `${base} No había ningún destinatario activo — revisa la línea «Notificar» o Configuración → Notificaciones.`;
  return enviados === 0 ? `${base} Ningún envío se completó (revisa los datos de contacto).` : `${base} ${enviados} envío(s) realizados.`;
}

function ModalCandidato({
  c,
  live,
  onClose,
  onCambio,
  onEliminado,
}: {
  c: Candidato;
  live: boolean;
  onClose: () => void;
  onCambio: (c: Candidato) => void;
  onEliminado?: () => void;
}) {
  const puedeDecidir = usePuedeDecidir();
  const modoPrueba = useModoPrueba();
  // CRUD (2026-09-15): eliminar candidato (baja lógica de la persona) con confirmación
  const [confirmarEliminar, setConfirmarEliminar] = useState(false);
  const [eliminando, setEliminando] = useState(false);
  const [errorEliminar, setErrorEliminar] = useState("");
  async function eliminarPersona() {
    setEliminando(true);
    setErrorEliminar("");
    const r = await eliminarCandidato(c.id);
    setEliminando(false);
    if (!r.ok) return setErrorEliminar(r.error);
    setConfirmarEliminar(false);
    onEliminado?.();
  }
  const [tab, setTab] = useState<TabCandidato>(() => (c.etapa === "Contratación" ? "contratacion" : "resumen"));
  // Si el candidato ENTRA a Contratación mientras el modal ya está abierto (p.ej. RH lo mueve
  // de etapa sin cerrar la ficha), salta solo a esa pestaña para que no se pierda entre las
  // demás — sin esto, seguiría en "resumen" hasta que el usuario la buscara a mano.
  const etapaAnterior = useRef(c.etapa);
  useEffect(() => {
    if (c.etapa === "Contratación" && etapaAnterior.current !== "Contratación") {
      setTab("contratacion");
    }
    etapaAnterior.current = c.etapa;
  }, [c.etapa]);
  const [aviso, setAviso] = useState<AvisoEstado>(null);
  const [ocupado, setOcupado] = useState("");
  const [comentario, setComentario] = useState("");
  const [modalEntrevista, setModalEntrevista] = useState(false);

  function resolver<T>(r: { ok: true; data: T } | { ok: false; error: string }, exito: string, reintentar?: () => void) {
    setOcupado("");
    if (!r.ok) {
      setAviso({ tono: "error", texto: r.error, reintentar });
      return null;
    }
    setAviso({ tono: "ok", texto: exito });
    return r.data;
  }

  /** 2026-09-17: Descartar pasa SIEMPRE por confirmación con motivo (HITL, queda en bitácora). Con
   * expediente abierto (Contratación/Onboarding) el backend lo cancela en la misma decisión. */
  const [confirmarDescartar, setConfirmarDescartar] = useState<null | { motivo: string }>(null);
  const [toast, setToast] = useState<ToastMsg>(null);

  function descartar() {
    if (!live) return setAviso({ tono: "warn", texto: "Levanta la API para registrar decisiones en la bitácora." });
    setConfirmarDescartar({ motivo: comentario });
  }

  async function descartarConfirmado() {
    if (!confirmarDescartar) return;
    setOcupado("descartar");
    const r = await decidirCandidato(c.id, "descartar", confirmarDescartar.motivo.trim());
    const data = resolver(r, "Candidato descartado.");
    if (data) {
      setComentario("");
      setConfirmarDescartar(null);
      onCambio(data);
    }
  }

  /** Botón explícito de avance — PATCH /candidatos/{codigo}/etapa con el destino exacto.
   * `forzarPrueba` (Lote 4): si el primer intento falla y Modo Prueba está activo, el aviso de
   * error trae un botón "Continuar de todos modos" que reintenta con el flag en true. */
  async function enviarAEtapa(etapa: EtapaCandidato, forzarPrueba = false) {
    if (!live) return setAviso({ tono: "warn", texto: "Levanta la API para registrar decisiones en la bitácora." });
    setOcupado(etapa);
    const r = await moverEtapaCandidato(c.id, etapa, comentario, forzarPrueba);
    const data = resolver(
      r, `Enviado a ${nombreEtapa(etapa)}.`,
      modoPrueba && !forzarPrueba ? () => enviarAEtapa(etapa, true) : undefined,
    );
    if (data) {
      setComentario("");
      onCambio(data);
    }
  }

  /** MODO PRUEBA (Punto 8): cierra la postulación actual y crea una nueva limpia para la misma
   * vacante, conservando teléfono y wa_id para volver a probar el flujo desde cero. */
  async function reiniciarPrueba() {
    if (!live) return setAviso({ tono: "warn", texto: "Levanta la API para registrar la acción en la bitácora." });
    if (!window.confirm(`¿Reiniciar postulación de prueba para ${c.nombre}? Se cerrará la postulación actual y se creará una limpia para volver a probar desde cero.`)) {
      return;
    }
    setOcupado("reiniciar-prueba");
    const r = await reiniciarPostulacionPrueba(c.id);
    const data = resolver(r, "Postulación reiniciada — la anterior quedó cerrada como historial; esta es la nueva.");
    if (data) onCambio(data);
  }

  // Punto 12: cada acción que notifica pasa por una confirmación ligera con la línea
  // "Notificar: … · Editar"; el ajuste viaja como `notificar` solo para esa acción.
  const [confirmacion, setConfirmacion] = useState<null | "solicitar" | "recordatorio" | "alta">(null);
  // 2026-09-16 (control manual de RH): «Mover a otra etapa» — selector simple + motivo opcional
  const [moverA, setMoverA] = useState<null | { etapa: EtapaCandidato | ""; motivo: string }>(null);
  // Evaluaciones (2026-09-28): «Agregar evaluación o verificación» — nunca mueve la columna del pipeline
  const [agregarEval, setAgregarEval] = useState(false);
  const [versionEval, setVersionEval] = useState(0);
  // 2026-09-22: confirmación de «Avanzar a Entrevista Humana» (omite la Entrevista Red Human)
  const [avanceDirecto, setAvanceDirecto] = useState(false);
  async function confirmarAvanceDirecto() {
    if (!live) return setAviso({ tono: "warn", texto: "Levanta la API para registrar decisiones en la bitácora." });
    setOcupado("avance-directo");
    const r = await avanzarAEntrevistaHumana(c.id);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setAvanceDirecto(false);
    onCambio(r.data);
    setAviso({ tono: "ok", texto: "Candidato en Entrevista Humana. La Entrevista Red Human quedó registrada como omitida manualmente." });
  }
  async function moverManual() {
    if (!moverA?.etapa) return;
    if (!live) return setAviso({ tono: "warn", texto: "Levanta la API para registrar decisiones en la bitácora." });
    setOcupado("mover");
    const r = await moverEtapaCandidato(c.id, moverA.etapa, moverA.motivo, false, true);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    const omitidas = (r.data.actividadesOmitidas ?? []).filter((o) => o.hacia === moverA.etapa).map((o) => nombreEtapa(o.actividad));
    setMoverA(null);
    onCambio(r.data);
    setAviso({ tono: "ok", texto: `Movido a ${nombreEtapa(moverA.etapa)}.${omitidas.length ? ` Omitido manualmente: ${omitidas.join(", ")}.` : ""}` });
  }
  const notificarAltaRef = useRef<NotificarAccion | undefined>(undefined);

  /** Onboarding · Zero-Touch fase 2 — RH detona, la IA da seguimiento por WhatsApp. */
  async function solicitarDocumentos(notificar?: NotificarAccion) {
    if (!live) return setAviso({ tono: "warn", texto: "Levanta la API para enviar mensajes por WhatsApp." });
    setOcupado("solicitar-documentos");
    const r = await solicitarDocumentosCandidato(c.id, notificar);
    const data = resolver(r, resumenEnvio(r, "Solicitud de documentos enviada."));
    if (data) onCambio(data.candidato);
  }

  async function enviarRecordatorioDocumentos(notificar?: NotificarAccion) {
    if (!live) return setAviso({ tono: "warn", texto: "Levanta la API para enviar mensajes por WhatsApp." });
    setOcupado("recordatorio-documentos");
    const r = await recordatorioDocumentosCandidato(c.id, notificar);
    const data = resolver(r, resumenEnvio(r, "Recordatorio enviado."));
    if (data) onCambio(data.candidato);
  }

  /** Botón principal de Onboarding — cierra el ciclo y mueve el registro a Colaboradores.
   * Siempre visible y habilitado mientras esté en Onboarding: si faltan documentos
   * obligatorios, el backend lo rechaza (409) y el motivo se muestra en {aviso}. Con Modo
   * Prueba activo, ese aviso trae un botón "Continuar de todos modos" — salvo que el 409 sea
   * "ya fue dado de alta", que el backend nunca deja saltar (ver contratacion.alta). */
  async function darDeAltaComoColaborador(forzarPrueba = false, notificar?: NotificarAccion) {
    if (!live || !c.expedienteId) return setAviso({ tono: "warn", texto: "Levanta la API para dar de alta al candidato." });
    if (notificar) notificarAltaRef.current = notificar;
    setOcupado("alta");
    const r = await autorizarAlta(c.expedienteId, undefined, forzarPrueba, notificarAltaRef.current);
    if (!r.ok) {
      setOcupado("");
      // 2026-09-15: sin documentos adjuntos el backend responde 400 y NO se puede forzar ni en Modo
      // Prueba — no se ofrece «Continuar de todos modos», solo el aviso rojo con el motivo.
      const sinDocumentos = /no tiene documentos adjuntos/i.test(r.error);
      return setAviso({
        tono: "error", texto: r.error,
        reintentar: modoPrueba && !forzarPrueba && !sinDocumentos ? () => darDeAltaComoColaborador(true) : undefined,
      });
    }
    const actualizado = await fetchCandidato(c.id);
    setOcupado("");
    if (actualizado) onCambio(actualizado);
    // Fase 5: qué salió (bienvenida + instrucciones de ingreso al candidato; aviso al Cliente)
    const envios = r.data.notificaciones ?? [];
    const ok = envios.filter((x) => x.enviado).map((x) => `${x.destinatario} por ${x.canal}`);
    const fallidos = envios.filter((x) => !x.enviado).map((x) => `${x.destinatario} por ${x.canal}${x.detalle ? ` (${x.detalle})` : ""}`);
    setAviso({
      tono: fallidos.length && !ok.length ? "warn" : "ok",
      texto:
        "Alta registrada — el candidato se movió a Colaboradores." +
        (ok.length ? ` Bienvenida enviada: ${ok.join(", ")}.` : "") +
        (fallidos.length ? ` No salió: ${fallidos.join("; ")}.` : ""),
    });
  }

  async function consentir() {
    setOcupado("consentimiento");
    const r = await registrarConsentimiento(c.id, {
      medio: "verbal",
      evidencia: "Consentimiento confirmado por RH durante el contacto con la persona candidata.",
    });
    const data = resolver(r, "Consentimiento registrado en la bitácora.");
    if (data) onCambio(data);
  }

  const siguientesEtapas = SIGUIENTE_ETAPA_MANUAL[c.etapa] ?? [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-0 backdrop-blur-md animate-in fade-in duration-200 sm:p-6">
      <div
        className="relative flex h-[100dvh] w-full max-w-4xl flex-col overflow-hidden border border-border-soft bg-bg shadow-2xl animate-in zoom-in-95 duration-200 sm:h-auto sm:max-h-[92vh] sm:rounded-3xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header Modal */}
        <div className="glass sticky top-0 z-10 flex items-center justify-between gap-2 border-b border-border-soft px-4 py-3 sm:px-6 sm:py-4">
          <div className="flex items-center gap-3.5 min-w-0">
            <Avatar name={c.nombre} tone={c.tono} />
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="font-display truncate text-lg sm:text-xl font-bold text-ink">{c.nombre}</h2>
                <span className="font-mono text-xs text-ink-3" title="Postulación">{c.id}</span>
                {c.candidatoCodigo && (
                  <span className="font-mono text-[10px] text-ink-3" title="Persona (maestro de identidad)">
                    · {c.candidatoCodigo}
                  </span>
                )}
                {c.yaAplicoAntes && (
                  <span
                    title={`Esta persona tiene ${c.totalPostulaciones} postulaciones en diferentes vacantes`}
                    className="rounded bg-blue-500/10 px-2 py-0.5 font-mono text-[10px] font-bold text-blue-600"
                  >
                    🔄 {c.totalPostulaciones} postulaciones
                  </span>
                )}
                {c.activa === false && (
                  <span
                    title={`Cerrada: ${c.motivoCierre || "sin motivo"}. Mover de etapa la reabre.`}
                    className="rounded bg-ink-3/10 px-2 py-0.5 font-mono text-[10px] font-bold uppercase text-ink-3"
                  >
                    Cerrada · {c.motivoCierre || "—"}
                  </span>
                )}
                {c.enConversacion && c.yaAplicoAntes && (
                  <span
                    title="El WhatsApp de esta persona está conversando sobre ESTA postulación"
                    className="rounded bg-emerald-500/10 px-2 py-0.5 font-mono text-[10px] font-bold text-emerald-700"
                  >
                    💬 En chat
                  </span>
                )}
              </div>
              <p className="truncate text-xs sm:text-sm text-ink-2">
                {c.puesto || "Sin vacante asignada"} · <b className="text-ink font-semibold">{c.fuente}</b>
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3 shrink-0">
            <EstadoBadge estado={c.estado} prefijo="Prefiltro: " />
            {live && puedeDecidir && (
              <Button
                size="sm"
                variant="outline"
                className="border-bad/40 text-bad hover:bg-bad-soft"
                onClick={() => setConfirmarEliminar(true)}
                disabled={eliminando}
              >
                <Trash2 className="h-4 w-4" /> Eliminar candidato
              </Button>
            )}
            <button
              onClick={onClose}
              className="grid h-9 w-9 place-items-center rounded-xl text-ink-2 hover:bg-surface-2 transition"
              aria-label="Cerrar modal"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {confirmarEliminar && (
          <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={() => !eliminando && setConfirmarEliminar(false)}>
            <div className="w-full max-w-md rounded-3xl border border-border-soft bg-bg p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-start gap-3">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-bad-soft text-bad">
                  <AlertTriangle className="h-5 w-5" />
                </span>
                <div>
                  <h3 className="font-display text-lg font-bold">¿Estás seguro de que deseas eliminar a este candidato?</h3>
                  <p className="mt-2 text-sm leading-relaxed text-ink-2">
                    <b>{c.nombre}</b> desaparecerá del tablero y de las búsquedas; todas sus postulaciones activas se cerrarán
                    {c.totalPostulaciones && c.totalPostulaciones > 1 ? ` (tiene ${c.totalPostulaciones})` : ""}. Nada se borra físicamente:
                    su historial (entrevistas, expediente, mensajes) se conserva y la acción queda en la bitácora.
                  </p>
                  {errorEliminar && <p className="mt-2 text-sm font-semibold text-bad">{errorEliminar}</p>}
                </div>
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setConfirmarEliminar(false)} disabled={eliminando}>
                  Cancelar
                </Button>
                <Button size="sm" className="bg-bad text-white hover:bg-bad/90" onClick={eliminarPersona} disabled={eliminando}>
                  <Trash2 className="h-4 w-4" /> {eliminando ? "Eliminando…" : "Sí, eliminar candidato"}
                </Button>
              </div>
            </div>
          </div>
        )}

        {/* Barra de Pestañas Principales (4 base + Contratación condicional) */}
        <div className="border-b border-border-soft bg-surface-2/70 pt-3">
          {/* 2026-09-17 (móvil): las pestañas se deslizan con el dedo (scroll-x) en vez de recortarse */}
          <div className="scroll-x gap-2 px-4 sm:px-6">
            {(
              [
                { id: "resumen", label: "Resumen", icon: User, tone: "brand" },
                { id: "evaluaciones", label: "Evaluación integral", icon: Sparkles, tone: "human" },
                { id: "documentos", label: "CV y documentos", icon: FileText, tone: "brand" },
                { id: "whatsapp", label: "WhatsApp", icon: MessageCircle, tone: "good", badge: c.mensajes },
                // 2026-09-17: la pestaña del expediente (checklist de documentos) vive en Contratación Y
                // Onboarding — antes desaparecía al pasar a Onboarding y RH ya no veía qué faltaba.
                ...(c.etapa === "Contratación" || c.etapa === "Onboarding"
                  ? [{ id: "contratacion", label: c.etapa === "Onboarding" ? "Expediente" : "Contratación", icon: Briefcase, tone: "warn" }]
                  : []),
              ] as { id: TabCandidato; label: string; icon: typeof User; tone: string; badge?: number }[]
            ).map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={cn(
                  "flex shrink-0 items-center gap-2 whitespace-nowrap rounded-t-xl px-3 py-2.5 text-sm font-semibold transition border-b-2 sm:px-4",
                  tab === t.id ? TAB_TONE_ACTIVA[t.tone] : "border-transparent text-ink-3 hover:text-ink hover:bg-surface/50",
                )}
              >
                <t.icon className="h-4 w-4" />
                {t.label}
                {Boolean(t.badge) && (
                  <span className={cn("rounded-full px-2 py-0.2 font-mono text-[11px] font-bold", TAB_TONE_BADGE[t.tone])}>
                    {t.badge}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* Cuerpo Scrolleable */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-6 flex flex-col gap-5">
          {aviso && (
            <Aviso tono={aviso.tono} onCerrar={() => setAviso(null)}>
              {aviso.texto}
              {aviso.reintentar && (
                <Button size="sm" variant="outline" className="mt-2" onClick={aviso.reintentar}>
                  <FlaskConical className="h-3.5 w-3.5" /> Continuar de todos modos (modo prueba)
                </Button>
              )}
            </Aviso>
          )}

          {c.consentimiento === false && (
            <Card className="border-warn/30 bg-warn-soft/40 p-4">
              <div className="flex items-start gap-2.5">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-warn" />
                <div className="flex-1">
                  <p className="text-[13px] leading-relaxed text-ink-2">
                    <b className="text-ink">Sin consentimiento registrado.</b> La LFPDPPP exige consentimiento
                    explícito antes de tratar los datos del candidato o abrir su expediente.
                  </p>
                  {live && (
                    <Button size="sm" variant="outline" className="mt-3" onClick={consentir} disabled={Boolean(ocupado)}>
                      Registrar consentimiento
                    </Button>
                  )}
                </div>
              </div>
            </Card>
          )}

          {c.etapa === "Entrevista Humana" && <PanelEntrevistaHumana c={c} live={live} onCambio={onCambio} />}

          {tab === "resumen" && <PestanaResumen c={c} live={live} onCambio={onCambio} setTab={setTab} />}
          {tab === "evaluaciones" && <PestanaEvaluaciones c={c} live={live} onCambio={onCambio} versionEval={versionEval} />}
          {tab === "documentos" && <PestanaDocumentos c={c} live={live} onCambio={onCambio} setAviso={setAviso} />}
          {tab === "whatsapp" && <PestanaWhatsApp c={c} live={live} onCambio={onCambio} />}
          {tab === "contratacion" && (c.etapa === "Contratación" || c.etapa === "Onboarding") && (
            <PanelContratacion c={c} live={live} onCambio={onCambio} setAviso={setAviso} onDocumentos={setConfirmacion} onDescartar={descartar} />
          )}
        </div>

        {/* MODO PRUEBA (Punto 8): independiente de la etapa — reinicia la postulación sin borrar teléfono.
            Solo visible con Modo Prueba activo o sobre una postulación de prueba (el backend lo exige). */}
        {puedeDecidir && (modoPrueba || c.esPrueba) && (
          <div className="border-t border-border-soft bg-surface px-6 py-2">
            <div className="flex justify-end">
              <Button
                variant="ghost"
                size="sm"
                onClick={reiniciarPrueba}
                disabled={Boolean(ocupado)}
                title="Modo Prueba (Punto 8): cierra la postulación actual y crea una nueva limpia para volver a probar desde cero."
                className="text-[11px] text-ink-3 hover:text-brand hover:bg-brand-soft/40 transition"
              >
                <RotateCw className="h-3.5 w-3.5" /> Reiniciar prueba
              </Button>
            </div>
          </div>
        )}

        {/* Etapa Prefiltro: solo Descartar — el paso a Entrevista IA es zero-touch, lo dispara
            la IA sola por WhatsApp al completar el prefiltro (no hay botón manual). */}
        {puedeDecidir && c.etapa === "Prefiltro" && (
          <div className="border-t border-border-soft bg-surface px-4 py-3 sm:px-6 sm:py-4">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={descartar}
                disabled={Boolean(ocupado)}
                className="border-bad/30 text-bad hover:bg-bad-soft"
              >
                <ThumbsDown className="h-4 w-4" /> Descartar candidato
              </Button>
            </div>
          </div>
        )}

        {/* Footer Fijo con HITL y Acciones — Prefiltro, Entrevista Humana y Contratación tienen
            su propio panel de acciones; este footer genérico no aplica ahí. */}
        {puedeDecidir &&
          c.etapa !== "Prefiltro" &&
          c.etapa !== "Contratación" &&
          (c.etapa !== "Entrevista Humana" || c.entrevistaHumana?.realizada) && (
          <div className="border-t border-border-soft bg-surface px-4 py-3 sm:px-6 sm:py-4">
            <div className="flex flex-col gap-3">
              <input
                value={comentario}
                onChange={(e) => setComentario(e.target.value)}
                placeholder="Nota de decisión para auditoría (opcional)…"
                className="h-10 w-full rounded-xl border border-border-soft bg-bg px-3.5 text-xs sm:text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20"
              />

              {/* Regla de UI (2026-09-16): UNA acción principal = la siguiente esperada; todo lo demás en «…»
                  («Mover a otra etapa» abre un selector simple sin bloqueos de secuencia). */}
              <div className="flex items-center gap-2">
                {siguientesEtapas[0] && (
                  <Button
                    size="sm"
                    className="flex-1"
                    onClick={() => (siguientesEtapas[0] === "Entrevista Humana" ? setModalEntrevista(true) : enviarAEtapa(siguientesEtapas[0]))}
                    disabled={Boolean(ocupado)}
                  >
                    <ThumbsUp className="h-4 w-4" /> Enviar a {nombreEtapa(siguientesEtapas[0])}
                  </Button>
                )}
                {c.expedienteId != null && c.etapa !== "Onboarding" && (
                  <a
                    href="/dashboard/onboarding"
                    className="flex items-center gap-1.5 rounded-xl border border-good/30 bg-good-soft px-3 py-2 text-xs font-semibold text-good transition hover:brightness-105"
                  >
                    <UserCheck className="h-4 w-4" /> Expediente ({c.expedienteProgreso ?? 0}%)
                  </a>
                )}
                <MenuAcciones
                  etiqueta="Más acciones"
                  acciones={[
                    ...siguientesEtapas.slice(1).map((etapa) => ({
                      etiqueta: `Enviar a ${nombreEtapa(etapa)}`,
                      icono: <ThumbsUp />,
                      onClick: () => (etapa === "Entrevista Humana" ? setModalEntrevista(true) : enviarAEtapa(etapa)),
                      disabled: Boolean(ocupado),
                    })),
                    ...(ETAPAS_AVANCE_DIRECTO.includes(c.etapa)
                      ? [{
                          etiqueta: "Avanzar a Entrevista Humana",
                          icono: <CalendarClock />,
                          title: TEXTO_AVANCE_DIRECTO,
                          onClick: () => setAvanceDirecto(true),
                          disabled: Boolean(ocupado),
                        }]
                      : []),
                    { etiqueta: "Agregar evaluación o verificación", icono: <IconoEvaluacion />, onClick: () => setAgregarEval(true), disabled: Boolean(ocupado) || c.activa === false },
                    { etiqueta: "Mover a otra etapa…", icono: <ArrowRightLeft />, onClick: () => setMoverA({ etapa: "", motivo: "" }), disabled: Boolean(ocupado) },
                    ...(c.etapa === "Entrevista Humana"
                      ? [{ etiqueta: "Agendar otra Entrevista Humana", icono: <CalendarClock />, onClick: () => setModalEntrevista(true), disabled: Boolean(ocupado) }]
                      : []),
                    ...(c.etapa === "Onboarding"
                      ? [
                          { etiqueta: "Solicitar documentos", icono: <Send />, onClick: () => setConfirmacion("solicitar"), disabled: Boolean(ocupado) },
                          { etiqueta: etiquetaRecordatorio(c.recordatorioNivel, c.recordatoriosEnviados).texto, icono: <RotateCw />, onClick: () => setConfirmacion("recordatorio"), disabled: Boolean(ocupado) },
                        ]
                      : []),
                    { etiqueta: "Descartar candidato…", icono: <ThumbsDown />, peligrosa: true, onClick: descartar, disabled: Boolean(ocupado) },
                  ]}
                />
              </div>

              {/* 2026-09-15 (Fase 1): el error del alta se muestra AQUÍ, pegado al botón — antes solo
                  aparecía arriba del cuerpo scrolleable y RH veía «parpadear» el botón sin explicación. */}
              {c.etapa === "Onboarding" && aviso && aviso.tono !== "ok" && (
                <div
                  role="alert"
                  className="flex items-start gap-2 rounded-xl border border-bad/40 bg-bad-soft px-3 py-2.5 text-xs font-semibold text-bad"
                >
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{aviso.texto}</span>
                </div>
              )}
              {/* Botón principal — siempre visible en Onboarding, sin importar el estado de los documentos */}
              {c.etapa === "Onboarding" && (
                <>
                  {/* 2026-09-18: switch de Modo Prueba junto al alta. ACTIVO → se permite con expediente
                      incompleto (se manda forzar_prueba directo); INACTIVO → el botón queda bloqueado
                      hasta que el expediente esté validado al 100% (el backend lo refuerza). */}
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[12px] text-ink-3">
                      Expediente al <b className={cn("font-mono", (c.expedienteProgreso ?? 0) >= 100 ? "text-good" : "text-warn")}>{c.expedienteProgreso ?? 0}%</b>
                      {(c.expedienteProgreso ?? 0) < 100 && !modoPrueba ? " · el alta exige 100% validado" : ""}
                      {(c.expedienteProgreso ?? 0) < 100 && modoPrueba ? " · Modo Prueba permite el alta incompleta" : ""}
                    </span>
                    <SwitchModoPrueba />
                  </div>
                  <Button
                    className="w-full"
                    onClick={() => setConfirmacion("alta")}
                    disabled={Boolean(ocupado) || c.expedienteEstado === "alta" || ((c.expedienteProgreso ?? 0) < 100 && !modoPrueba)}
                    title={(c.expedienteProgreso ?? 0) < 100 && !modoPrueba ? "Completa y valida el expediente al 100% (o activa Modo Prueba) para dar de alta." : undefined}
                  >
                    <UserCheck className="h-4 w-4" />
                    {c.expedienteEstado === "alta"
                      ? "Alta completada ✓"
                      : ocupado === "alta"
                        ? "Dando de alta…"
                        : (c.expedienteProgreso ?? 0) < 100 && modoPrueba
                          ? `DAR DE ALTA (Modo Prueba · expediente al ${c.expedienteProgreso ?? 0}%)`
                          : "DAR DE ALTA COMO COLABORADOR"}
                  </Button>
                </>
              )}
            </div>
          </div>
        )}
      </div>

      {confirmarDescartar && (
          <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={() => !ocupado && setConfirmarDescartar(null)}>
            <div className="w-full max-w-md rounded-3xl border border-border-soft bg-bg p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
              <h3 className="font-display text-lg font-bold text-bad">Descartar candidato</h3>
              <p className="mt-1 text-sm text-ink-2">
                La postulación de <b className="text-ink">{c.nombre}</b> se cierra como descartada y queda en el historial con tu nombre.
                {c.expedienteId != null ? " Su expediente de contratación se cancela." : ""}
              </p>
              <label className="mt-4 flex flex-col gap-1.5">
                <span className="text-xs font-medium text-ink-2">Motivo{c.expedienteId != null ? " (obligatorio)" : ""}</span>
                <input
                  autoFocus
                  value={confirmarDescartar.motivo}
                  onChange={(e) => setConfirmarDescartar({ motivo: e.target.value })}
                  placeholder="Ej. no cumple el requisito de disponibilidad"
                  className="h-10 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
                />
              </label>
              <div className="mt-5 flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setConfirmarDescartar(null)} disabled={ocupado === "descartar"}>Cancelar</Button>
                <Button
                  size="sm"
                  className="bg-bad text-white hover:bg-bad/90"
                  onClick={descartarConfirmado}
                  disabled={ocupado === "descartar" || (c.expedienteId != null && !confirmarDescartar.motivo.trim())}
                >
                  <ThumbsDown className="h-4 w-4" /> {ocupado === "descartar" ? "Descartando…" : "Sí, descartar"}
                </Button>
              </div>
            </div>
          </div>
        )}
      {avanceDirecto && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={() => ocupado !== "avance-directo" && setAvanceDirecto(false)}>
          <div className="w-full max-w-md rounded-3xl border border-border-soft bg-bg p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-display text-lg font-bold">Avanzar a Entrevista Humana</h3>
            <p className="mt-2 text-sm leading-relaxed text-ink-2">{TEXTO_AVANCE_DIRECTO}.</p>
            <p className="mt-2 text-xs text-ink-3">
              Queda registrado en el historial del expediente ({c.nombre}); lo que ya se generó (chat, análisis de CV, entrevista parcial) se conserva.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setAvanceDirecto(false)} disabled={ocupado === "avance-directo"}>Cancelar</Button>
              <Button size="sm" onClick={confirmarAvanceDirecto} disabled={ocupado === "avance-directo"}>
                {ocupado === "avance-directo" ? "Avanzando…" : "Avanzar"}
              </Button>
            </div>
          </div>
        </div>
      )}
      {agregarEval && (
        <ModalAgregarEvaluacion
          codigo={c.id}
          puesto={c.puesto}
          onClose={() => setAgregarEval(false)}
          onAgregada={(ev) => {
            setAgregarEval(false);
            setVersionEval((x) => x + 1);
            setTab("evaluaciones");
            setAviso({ tono: "ok", texto: `«${ev.nombre}» agregada: ${ev.estadoTexto}. El candidato sigue en ${nombreEtapa(c.etapa)}.` });
          }}
        />
      )}
      {moverA && (
          <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={() => !ocupado && setMoverA(null)}>
            <div className="w-full max-w-md rounded-3xl border border-border-soft bg-bg p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
              <h3 className="font-display text-lg font-bold">Mover a otra etapa</h3>
              <p className="mt-1 text-sm text-ink-2">
                RH decide: el candidato se mueve aunque WhatsApp o el correo fallen. Lo que se salte queda registrado como «Omitida manualmente».
              </p>
              <label className="mt-4 flex flex-col gap-1.5">
                <span className="text-xs font-medium text-ink-2">Etapa destino</span>
                <select
                  value={moverA.etapa}
                  onChange={(e) => setMoverA({ ...moverA, etapa: e.target.value as EtapaCandidato | "" })}
                  className="h-11 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
                >
                  <option value="">Elige…</option>
                  {(["Prefiltro", "Entrevista IA", "Evaluación", "Entrevista Humana", "Contratación", "Onboarding"] as EtapaCandidato[])
                    .filter((e) => e !== c.etapa)
                    .map((e) => (
                      <option key={e} value={e}>{nombreEtapa(e)}</option>
                    ))}
                </select>
              </label>
              <label className="mt-3 flex flex-col gap-1.5">
                <span className="text-xs font-medium text-ink-2">Motivo (opcional)</span>
                <input
                  value={moverA.motivo}
                  onChange={(e) => setMoverA({ ...moverA, motivo: e.target.value })}
                  placeholder="Ej. el candidato ya fue entrevistado por el cliente"
                  className="h-10 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
                />
              </label>
              <div className="mt-5 flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setMoverA(null)} disabled={ocupado === "mover"}>Cancelar</Button>
                <Button size="sm" onClick={moverManual} disabled={!moverA.etapa || ocupado === "mover"}>
                  {ocupado === "mover" ? "Moviendo…" : "Mover"}
                </Button>
              </div>
            </div>
          </div>
        )}
        {confirmacion === "solicitar" && (
          <ConfirmacionAccion
            titulo="Solicitar documentos"
            texto={`Se le pedirá a ${c.nombre.split(" ")[0]} que suba sus documentos con la liga pública del expediente.`}
            evento="solicitud_documentos"
            hayEntrevistador={false}
            hayCliente={Boolean(c.clienteVacante)}
            clienteId={c.clienteIdVacante ?? null}
            etiquetaConfirmar="Enviar"
            onCancelar={() => setConfirmacion(null)}
            onConfirmar={async (n) => {
              setConfirmacion(null);
              await solicitarDocumentos(n);
            }}
          />
        )}
        {confirmacion === "recordatorio" && (
          <ConfirmacionAccion
            titulo={`Enviar recordatorio de documentos · nivel ${etiquetaRecordatorio(c.recordatorioNivel, c.recordatoriosEnviados).nivel} de 3 (${etiquetaRecordatorio(c.recordatorioNivel, c.recordatoriosEnviados).tono.nombre})`}
            texto={`${etiquetaRecordatorio(c.recordatorioNivel, c.recordatoriosEnviados).tono.descripcion} Incluye los documentos que siguen pendientes en el expediente.`}
            evento="recordatorio_documentos"
            hayEntrevistador={false}
            hayCliente={Boolean(c.clienteVacante)}
            clienteId={c.clienteIdVacante ?? null}
            etiquetaConfirmar="Enviar"
            onCancelar={() => setConfirmacion(null)}
            onConfirmar={async (n) => {
              setConfirmacion(null);
              await enviarRecordatorioDocumentos(n);
            }}
          />
        )}
        {confirmacion === "alta" && (
          <ConfirmacionAccion
            titulo="Dar de alta como colaborador"
            texto="RH autoriza el alta: el registro se mueve a Colaboradores y la postulación queda cerrada como contratada."
            evento="contratacion"
            hayEntrevistador={false}
            hayCliente={Boolean(c.clienteVacante)}
            clienteId={c.clienteIdVacante ?? null}
            etiquetaConfirmar="Dar de alta"
            onCancelar={() => setConfirmacion(null)}
            onConfirmar={async (n) => {
              setConfirmacion(null);
              // Modo Prueba activo con expediente incompleto → forzar directo (sin el segundo clic de «Continuar»)
              await darDeAltaComoColaborador(modoPrueba && (c.expedienteProgreso ?? 0) < 100, n);
            }}
          />
        )}
      <Toast msg={toast} onClose={() => setToast(null)} />
      {modalEntrevista && (
        <ModalProgramarEntrevista
          c={c}
          onClose={() => setModalEntrevista(false)}
          onListo={(actualizado, resultados, advertencias) => {
            setModalEntrevista(false);
            // Fase 7A: el resultado por canal ya no es silencioso — se muestra qué salió y qué no (y por qué)
            const lineas = lineasResultados(resultados);
            const fallidos = lineas.filter((l) => !l.ok);
            // 2026-09-18: además un toast amarillo flotante si algún correo/WhatsApp no salió (no se pierde con el scroll)
            const correoFallo = resultados.some((r) => r.canal === "correo" && !r.enviado && r.destino);
            if (advertencias?.length || correoFallo) {
              setToast({
                tono: "warn",
                texto: correoFallo ? "Entrevista asignada, pero el correo falló. Verifica la API Key o el Dominio" : "Entrevista asignada con avisos",
                detalle: advertencias ?? [],
              });
            }
            setAviso({
              tono: fallidos.length ? "warn" : "ok",
              texto: lineas.length
                ? `Entrevista programada. ${lineas.map((l) => `${l.ok ? "✓" : "✗"} ${l.texto}`).join(" · ")}`
                : "Entrevista programada. No había ningún destinatario activo — revisa la línea «Notificar» o Configuración → Notificaciones.",
            });
            onCambio(actualizado);
          }}
        />
      )}
    </div>
  );
}

/* ============================================================
   PESTAÑA 1: Resumen — síntesis y decisión rápida (Punto 3, secciones A-G)

   Distribución (Punto 3): aquí solo va la síntesis para decidir sin entrar a las demás
   pestañas — el análisis detallado sigue viviendo en Evaluaciones, nunca se duplica un
   bloque completo, solo se referencia con "Ver detalle en Evaluaciones →".
   ============================================================ */

type EstadoAnalisisCv = "sin_cv" | "analizando" | "error" | "analizado";

/** Punto 2: sin columna de estado nueva — se deriva de si hay un Archivo tipo=cv y si
 * `cvDatos` trae señales reales de una extracción (nunca "N/D": vacío es vacío). */
function estadoAnalisisCv(c: Candidato, enVuelo: boolean): EstadoAnalisisCv {
  if (enVuelo) return "analizando";
  const tieneCv = (c.listaArchivos ?? []).some((a) => a.tipo === "cv");
  if (!tieneCv) return "sin_cv";
  const cv = c.cvDatos ?? {};
  const tieneExtraccion = Boolean(
    cv.resumen_profesional || cv.experiencia_resumen || cv.puesto_actual || (cv.habilidades && cv.habilidades.length),
  );
  return tieneExtraccion ? "analizado" : "error";
}

const TONOS_RECOMENDACION: Record<string, { card: string; texto: string; icon: typeof CheckCircle2 }> = {
  "Avanzar a contratación": { card: "border-good/30 bg-good-soft/20", texto: "text-good", icon: CheckCircle2 },
  "Realizar entrevista humana": { card: "border-warn/30 bg-warn-soft/20", texto: "text-warn", icon: UserCheck },
  "Realizar Entrevista Red Human": { card: "border-brand/30 bg-brand-soft/20", texto: "text-brand", icon: UserCheck },
  "Reintentar Entrevista Red Human": { card: "border-warn/30 bg-warn-soft/20", texto: "text-warn", icon: RotateCw },
  "No avanzar": { card: "border-bad/30 bg-bad-soft/20", texto: "text-bad", icon: XCircle },
};

/** 2026-09-13: status legible de la Entrevista Red Human (bloque propio en Resumen y Evaluaciones). */
function textoEntrevistaStatus(s: NonNullable<Candidato["entrevistaStatus"]>): { titulo: string; detalle: string; tono: "good" | "warn" | "bad" | "neutral" } {
  switch (s.estado) {
    case "evaluada":
      return { titulo: "Entrevista Red Human realizada y evaluada", detalle: s.faltante.length ? `No se cubrió: ${s.faltante.join(", ")}.` : "", tono: "good" };
    case "interrumpida":
      return {
        titulo: s.motivo === "sin_respuestas" ? "Entrevista Red Human sin respuestas" : "Entrevista Red Human interrumpida",
        detalle: s.motivo === "sin_respuestas" ? "El candidato no contestó. No se generó evaluación ni score. Acción: reintentar." : "Se cortó antes de terminar. No se generó evaluación. Acción: reintentar.",
        tono: "bad",
      };
    case "parcial":
      return { titulo: "Entrevista Red Human parcial", detalle: `${s.motivoIa ? s.motivoIa + " " : ""}Sin score integral.${s.faltante.length ? ` Faltó: ${s.faltante.join(", ")}.` : ""} Acción: reintentar.`, tono: "warn" };
    case "en_curso":
      return { titulo: "Entrevista Red Human en curso", detalle: "", tono: "neutral" };
    case "completada":
      return { titulo: "Entrevista Red Human completada, evaluando…", detalle: "", tono: "neutral" };
    default:
      return { titulo: "Entrevista Red Human programada", detalle: "Aún no se realiza.", tono: "neutral" };
  }
}

function PestanaResumen({
  c,
  live,
  onCambio,
  setTab,
}: {
  c: Candidato;
  live: boolean;
  onCambio: (c: Candidato) => void;
  setTab: (t: TabCandidato) => void;
}) {
  const [reanalizando, setReanalizando] = useState(false);
  const [errorCv, setErrorCv] = useState("");
  const cv = c.cvDatos ?? {};
  const estadoCv = estadoAnalisisCv(c, reanalizando);
  const ultimoCv = [...(c.listaArchivos ?? [])].reverse().find((a) => a.tipo === "cv");

  async function reintentarAnalisis() {
    if (!ultimoCv) return;
    setReanalizando(true);
    setErrorCv("");
    const r = await reanalizarCvCandidato(c.id, ultimoCv.id);
    setReanalizando(false);
    if (!r.ok) {
      setErrorCv(r.error);
      return;
    }
    onCambio(r.data);
  }

  // --- A. Datos principales — aprovecha automáticamente la extracción del CV, nunca "N/D". ---
  const datosPrincipales: { icon: typeof MapPin; v: string }[] = [];
  if (c.ubicacion) datosPrincipales.push({ icon: MapPin, v: c.ubicacion });
  if (c.telefono) datosPrincipales.push({ icon: Phone, v: c.telefono });
  if (c.correo) datosPrincipales.push({ icon: Mail, v: c.correo });
  if (cv.puesto_actual) datosPrincipales.push({ icon: Briefcase, v: cv.puesto_actual });
  if (cv.ultimo_empleo) datosPrincipales.push({ icon: Building2, v: cv.ultimo_empleo });
  if (cv.anios_experiencia != null) datosPrincipales.push({ icon: CalendarClock, v: `${cv.anios_experiencia} años de experiencia` });
  datosPrincipales.push({ icon: Globe, v: `Canal: ${c.fuente}` });

  const tonoRecomendacion = c.recomendacionRedHuman ? TONOS_RECOMENDACION[c.recomendacionRedHuman] : null;

  return (
    <div className="flex flex-col gap-5">
      {/* A. Datos principales */}
      <div className="flex flex-wrap gap-2">
        {datosPrincipales.map((d, i) => (
          <Info key={i} icon={d.icon} v={d.v} />
        ))}
      </div>

      {/* B. Perfil extraído del CV */}
      <div>
        <Eyebrow>Perfil extraído del CV</Eyebrow>
        <Card className="mt-2 p-5">
          {estadoCv === "sin_cv" && <p className="text-sm text-ink-3">Currículum no recibido.</p>}

          {estadoCv === "analizando" && (
            <p className="flex items-center gap-2 text-sm text-ink-3">
              <Loader2 className="h-4 w-4 animate-spin" /> Analizando currículum…
            </p>
          )}

          {estadoCv === "error" && (
            <div>
              <p className="text-sm text-bad">No fue posible analizar el currículum.</p>
              {live && ultimoCv && (
                <Button size="sm" variant="outline" className="mt-3" onClick={reintentarAnalisis} disabled={reanalizando}>
                  <RefreshCw className={cn("h-3.5 w-3.5", reanalizando && "animate-spin")} />
                  {reanalizando ? "Reintentando…" : "Reintentar análisis"}
                </Button>
              )}
              {errorCv && <p className="mt-2 text-xs text-bad">{errorCv}</p>}
            </div>
          )}

          {estadoCv === "analizado" && (
            <div className="flex flex-col gap-4">
              <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-ink-2">
                {cv.resumen_profesional || cv.experiencia_resumen}
              </p>

              {Boolean(cv.experiencia_relevante) && (
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-wider text-ink-3">Experiencia relevante para esta vacante</p>
                  <p className="mt-1 break-words text-sm leading-relaxed text-ink-2">{cv.experiencia_relevante}</p>
                </div>
              )}

              {Boolean(cv.estudios?.length) && (
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-wider text-ink-3">Formación principal</p>
                  <ul className="mt-1.5 space-y-1">
                    {cv.estudios!.slice(0, 3).map((e, i) => (
                      <li key={i} className="flex items-start gap-1.5 text-sm leading-relaxed text-ink-2">
                        <GraduationCap className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-3" /> <span className="break-words">{e}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {Boolean(cv.conocimientos_relevantes?.length) && (
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-wider text-ink-3">Conocimientos relevantes para la vacante</p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {cv.conocimientos_relevantes!.map((h, i) => (
                      <span
                        key={i}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-brand/25 bg-brand-soft px-2.5 py-1 text-xs font-medium text-brand"
                      >
                        <Award className="h-3.5 w-3.5 text-brand" /> {h}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {live && ultimoCv && (
                <button
                  onClick={reintentarAnalisis}
                  disabled={reanalizando}
                  className="self-start text-[11px] font-semibold text-brand hover:underline disabled:opacity-50"
                >
                  {reanalizando ? "Reanalizando…" : "Reanalizar con el CV más reciente"}
                </button>
              )}
            </div>
          )}
        </Card>
      </div>

      {/* C. Prefiltro — SOLO filtro de entrada (Cumple / No cumple), sin score (2026-09-13) */}
      <div>
        <Eyebrow>Prefiltro de entrada</Eyebrow>
        <Card className="mt-2 p-4">
          {!c.prefiltroResumen ? (
            <p className="text-sm text-ink-3">Prefiltro en curso — todavía no hay criterios evaluados.</p>
          ) : c.prefiltroResumen.resultado === "no_cumple" || c.prefiltroResumen.incumplidos.length > 0 ? (
            <div>
              <p className="text-sm font-semibold text-warn">
                Prefiltro: No cumple{c.prefiltroResumen.total ? ` (incumple ${c.prefiltroResumen.incumplidos.length} de ${c.prefiltroResumen.total} criterios)` : ""}
              </p>
              <ul className="mt-2 space-y-1">
                {c.prefiltroResumen.incumplidos.map((x, i) => (
                  <li key={i} className="break-words text-xs leading-relaxed text-ink-2">• {x}</li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-sm font-semibold text-good">
              Prefiltro: Cumple{c.prefiltroResumen.total ? ` (${c.prefiltroResumen.cumple} de ${c.prefiltroResumen.total} criterios)` : ""}
            </p>
          )}
          <p className="mt-1.5 text-[11px] text-ink-3">Filtro básico de entrada; no forma parte de la evaluación integral.</p>
          {(c.inconsistencias?.length ?? 0) > 0 && (
            <div className="mt-3 rounded-xl border border-warn/40 bg-warn-soft/40 p-3">
              <p className="text-xs font-semibold text-warn">Respuestas distintas entre el formulario web y WhatsApp — RH decide (no se descartó automáticamente):</p>
              <ul className="mt-1.5 space-y-1">
                {c.inconsistencias!.map((i, k) => (
                  <li key={k} className="text-xs leading-relaxed text-ink-2">
                    <b>{i.criterio}</b>: web «{i.web}» · WhatsApp «{i.whatsapp}»
                    {i.aclarada ? <span className="text-good"> · aclaró: «{i.aclaracion}»</span> : <span className="text-ink-3"> · pendiente de aclarar</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      </div>

      {(c.capacitacion?.length ?? 0) > 0 && (
        <div>
          <Eyebrow>Capacitación (filtro de la vacante)</Eyebrow>
          <Card className="mt-2 p-4">
            <ul className="space-y-1">
              {c.capacitacion!.map((k) => (
                <li key={k.asignacion} className="text-sm">
                  <span className={k.aprobado ? "font-semibold text-good" : "font-semibold text-bad"}>{k.aprobado ? "Aprobado" : "No aprobado"} · {k.calificacion}%</span>
                  <span className="text-ink-2"> — {k.titulo}</span>
                  <span className="text-[11px] text-ink-3"> · {fechaCorta(k.fecha)}</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}

      {(c.actividadesOmitidas?.length ?? 0) > 0 && (
        <p className="text-[11px] text-ink-3">
          Omitido manualmente: {c.actividadesOmitidas!.map((o) => `${nombreEtapa(o.actividad)} (${o.usuario}, ${fechaCorta(o.fecha)}${o.motivo ? `: ${o.motivo}` : ""})`).join(" · ")}
        </p>
      )}

      {/* 2026-09-22: historial del expediente — decisiones humanas registradas (nunca se borran) */}
      {(c.historial?.length ?? 0) > 0 && (
        <div>
          <Eyebrow>Historial del expediente</Eyebrow>
          <ul className="mt-2 space-y-1.5">
            {c.historial!.map((h, i) => (
              <li key={i} className="flex items-start gap-2 text-[12px] text-ink-2">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand" />
                <span>
                  {h.texto}
                  {h.motivo ? <span className="text-ink-3"> · {h.motivo}</span> : null}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* C2. Status de la Entrevista Red Human (2026-09-13) */}
      {c.entrevistaStatus && (
        <div>
          <Eyebrow>Entrevista Red Human</Eyebrow>
          <Card className="mt-2 p-4">
            {(() => {
              const st = textoEntrevistaStatus(c.entrevistaStatus);
              return (
                <>
                  <p className={cn("text-sm font-semibold", st.tono === "good" ? "text-good" : st.tono === "warn" ? "text-warn" : st.tono === "bad" ? "text-bad" : "text-ink")}>{st.titulo}</p>
                  {st.detalle && <p className="mt-1 text-xs leading-relaxed text-ink-2">{st.detalle}</p>}
                  <p className="mt-1 text-[11px] text-ink-3">
                    {c.entrevistaStatus.turnosCandidato} respuestas{c.entrevistaStatus.intentosPrevios ? ` · ${c.entrevistaStatus.intentosPrevios} intento(s) previo(s)` : ""}
                    {c.entrevistaStatus.accionSiguiente === "reintentar" ? " · Reintentar desde el tablero de Entrevistas (botón «Reintentar»)." : ""}
                  </p>
                </>
              );
            })()}
          </Card>
        </div>
      )}

      {/* D. Evaluación integral (Análisis de CV + Entrevista Red Human) — solo con entrevista válida */}
      {c.afinidadGlobal != null && c.evaluacionIntegral && (
        <div>
          <Eyebrow>Evaluación integral · Afinidad con la vacante</Eyebrow>
          <Card className="mt-2 p-5">
            <div className="flex flex-wrap items-center gap-4">
              <ScoreRing score={c.afinidadGlobal} />
              <p className="font-display text-lg font-bold text-ink">Afinidad: {c.afinidadGlobal}/100</p>
            </div>
            {c.sintesisAfinidad && <p className="mt-3 break-words text-sm leading-relaxed text-ink-2">{c.sintesisAfinidad}</p>}
            <button onClick={() => setTab("evaluaciones")} className="mt-3 text-[11px] font-semibold text-brand hover:underline">
              Ver detalle en Evaluaciones →
            </button>
          </Card>
        </div>
      )}

      {/* E. Fortalezas principales */}
      {Boolean(c.fortalezasPrincipales?.length) && (
        <div>
          <Eyebrow>Fortalezas principales</Eyebrow>
          <Card className="mt-2 border-good/30 bg-good-soft/20 p-4">
            <ul className="space-y-1.5">
              {c.fortalezasPrincipales!.map((f, i) => (
                <li key={i} className="flex items-start gap-1.5 text-sm leading-relaxed text-ink-2">
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-good" /> <span className="break-words">{f}</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}

      {/* F. Puntos por validar — solo lo que requiere intervención humana */}
      {Boolean(c.puntosPorValidar?.length) && (
        <div>
          <Eyebrow>Puntos por validar</Eyebrow>
          <Card className="mt-2 border-warn/30 bg-warn-soft/20 p-4">
            <ul className="space-y-1.5">
              {c.puntosPorValidar!.map((p, i) => (
                <li key={i} className="flex items-start gap-1.5 text-sm leading-relaxed text-ink-2">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn" /> <span className="break-words">{p}</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}

      {/* G. Recomendación de Red Human — destacada */}
      {c.recomendacionRedHuman && tonoRecomendacion && (
        <Card className={cn("p-5", tonoRecomendacion.card)}>
          <div className="flex items-start gap-3">
            <tonoRecomendacion.icon className={cn("mt-0.5 h-6 w-6 shrink-0", tonoRecomendacion.texto)} />
            <div>
              <p className="font-mono text-[10px] font-bold uppercase tracking-wider text-ink-3">Recomendación de Red Human</p>
              <p className={cn("font-display text-lg font-bold", tonoRecomendacion.texto)}>{c.recomendacionRedHuman}</p>
              {c.recomendacionMotivo && (
                <p className="mt-1.5 break-words text-sm leading-relaxed text-ink-2">{c.recomendacionMotivo}</p>
              )}
            </div>
          </div>
        </Card>
      )}

      {/* H. Fase 2 — otras postulaciones de la misma persona (historial, más reciente primero) */}
      {(c.historialPostulaciones?.length ?? 0) > 0 && (
        <div>
          <Eyebrow>Otras postulaciones de esta persona</Eyebrow>
          <Card className="mt-2 divide-y divide-border-soft p-0">
            {c.historialPostulaciones!.map((h) => (
              <div key={h.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
                <div className="min-w-0">
                  <p className="truncate font-semibold text-ink">{h.puesto || "Sin vacante asignada"}</p>
                  <p className="font-mono text-[10px] text-ink-3">
                    {h.id} · {h.creado}
                  </p>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="rounded bg-surface px-1.5 py-0.5 text-[10px] font-semibold text-ink-2">{h.etapa}</span>
                  {h.activa ? (
                    <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 font-mono text-[9px] font-bold text-emerald-700">En curso</span>
                  ) : (
                    <span className="rounded bg-ink-3/10 px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase text-ink-3">
                      Cerrada · {h.motivoCierre || "—"}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </Card>
        </div>
      )}
    </div>
  );
}

/* ============================================================
   PESTAÑA 2: Evaluaciones (Luna, avatar, requisitos/brechas, prefiltro, entrevista humana)
   ============================================================ */
function PestanaEvaluaciones({ c, live, onCambio, versionEval = 0 }: { c: Candidato; live?: boolean; onCambio?: (c: Candidato) => void; versionEval?: number }) {
  const puedeDecidir = usePuedeDecidir();
  const [evaluando, setEvaluando] = useState(false);
  const [errorEval, setErrorEval] = useState("");
  /** 2026-09-17: entrevista interrumpida/parcial CON respuestas → RH puede evaluarla con lo que hay. */
  async function evaluarConLoQueHay() {
    if (!c.entrevistaStatus) return;
    setEvaluando(true);
    setErrorEval("");
    const r = await evaluarEntrevistaConLoQueHay(c.entrevistaStatus.codigo);
    setEvaluando(false);
    if (!r.ok) return setErrorEval(r.error);
    const ficha = await fetchCandidato(c.id);
    if (ficha && onCambio) onCambio(ficha);
  }
  const a = c.analisis ?? {};
  const hayCv = Boolean(a.requisitos_cumplidos?.length || a.brechas?.length || a.fortalezas_cv?.length || c.cvDatos?.resumen_profesional);
  const ultimaEntrevista = c.entrevistas?.[c.entrevistas.length - 1];
  // 2026-09-13: solo una entrevista EVALUADA alimenta la Evaluación Integral (interrumpida/parcial no)
  const evalAvatar = (ultimaEntrevista?.estado === "evaluada" ? ultimaEntrevista?.evaluacion : null) as
    | { resumen?: string; fortalezas?: string[]; riesgos?: string[]; areas_desarrollo?: string[]; perfil?: PerfilProfundo | null; match_perfil?: number; recomendacion?: string; faltante?: string[] }
    | null
    | undefined;
  const historialEh = c.entrevistasHumanas ?? [];

  return (
    <div className="flex flex-col gap-5">
      {/* ===== 1) ANÁLISIS DE CV — disponible desde el inicio (independiente del prefiltro) ===== */}
      <Card className="border-brand/30 bg-brand-soft/20 p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="space-y-1">
            <span className="font-mono text-[10px] font-bold uppercase tracking-wider text-brand">1 · Análisis de CV</span>
            {hayCv ? (
              <>
                <h3 className="font-display text-xl font-bold text-ink">Ajuste del CV: {c.score} / 100</h3>
                <p className="text-xs sm:text-sm text-ink-2 max-w-xl">{c.evidencia || "Ajuste preliminar comparado contra los requisitos de la vacante."}</p>
              </>
            ) : (
              <>
                <h3 className="font-display text-xl font-bold text-ink">Sin CV analizado</h3>
                <p className="text-xs sm:text-sm text-ink-2 max-w-xl">Sube el CV en Documentos para obtener el análisis (experiencia relevante, fortalezas, brechas y compatibilidad).</p>
              </>
            )}
          </div>
          {hayCv && (
            <div className="flex items-center gap-3 self-start sm:self-auto">
              <div className="scale-125">
                <ScoreRing score={c.score} />
              </div>
            </div>
          )}
        </div>
        {hayCv && (
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {(a.experiencia_relevante_cv || c.cvDatos?.experiencia_relevante) && (
              <div className="sm:col-span-2">
                <p className="font-mono text-[11px] font-bold uppercase tracking-wider text-ink-3">Experiencia relevante</p>
                <p className="mt-1 text-xs leading-relaxed text-ink-2">{a.experiencia_relevante_cv || c.cvDatos?.experiencia_relevante}</p>
              </div>
            )}
            {Boolean(a.fortalezas_cv?.length) && (
              <div>
                <p className="font-mono text-[11px] font-bold uppercase tracking-wider text-good">Fortalezas</p>
                <ul className="mt-1 space-y-1">{a.fortalezas_cv!.map((x, i) => <li key={i} className="text-xs leading-relaxed text-ink-2">• {x}</li>)}</ul>
              </div>
            )}
            {Boolean(a.brechas?.length) && (
              <div>
                <p className="font-mono text-[11px] font-bold uppercase tracking-wider text-warn">Brechas / requisitos no acreditados</p>
                <ul className="mt-1 space-y-1">{a.brechas!.map((x, i) => <li key={i} className="text-xs leading-relaxed text-ink-2">• {x}</li>)}</ul>
              </div>
            )}
            {a.compatibilidad_cv && (
              <div className="sm:col-span-2">
                <p className="font-mono text-[11px] font-bold uppercase tracking-wider text-ink-3">Compatibilidad</p>
                <p className="mt-1 text-xs leading-relaxed text-ink-2">{a.compatibilidad_cv}</p>
              </div>
            )}
          </div>
        )}
      </Card>

      {/* ===== 2) STATUS DE LA ENTREVISTA RED HUMAN ===== */}
      <Card className="p-5">
        <span className="font-mono text-[10px] font-bold uppercase tracking-wider text-human">2 · Entrevista Red Human</span>
        {c.entrevistaStatus ? (
          (() => {
            const st = textoEntrevistaStatus(c.entrevistaStatus);
            return (
              <>
                <h3 className={cn("font-display mt-1 text-lg font-bold", st.tono === "good" ? "text-good" : st.tono === "warn" ? "text-warn" : st.tono === "bad" ? "text-bad" : "text-ink")}>{st.titulo}</h3>
                {st.detalle && <p className="mt-1 text-xs leading-relaxed text-ink-2">{st.detalle}</p>}
                <p className="mt-1 text-[11px] text-ink-3">
                  {c.entrevistaStatus.turnosCandidato} respuestas del candidato
                  {c.entrevistaStatus.intentosPrevios ? ` · ${c.entrevistaStatus.intentosPrevios} intento(s) previo(s)` : ""}
                  {c.entrevistaStatus.accionSiguiente === "reintentar" ? " · Acción siguiente: Reintentar Entrevista Red Human (tablero de Entrevistas → «Reintentar»)." : ""}
                </p>
                {puedeDecidir && live && c.entrevistaStatus.accionSiguiente === "reintentar" && (c.entrevistaStatus.turnosUtiles ?? 0) > 0 && (
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <Button size="sm" variant="outline" onClick={evaluarConLoQueHay} disabled={evaluando}>
                      <Sparkles className="h-4 w-4" /> {evaluando ? "Evaluando…" : `Evaluar con lo que hay (${c.entrevistaStatus.turnosUtiles} respuestas)`}
                    </Button>
                    {errorEval && <span className="text-xs text-bad">{errorEval}</span>}
                  </div>
                )}
              </>
            );
          })()
        ) : (
          <p className="mt-1 text-sm text-ink-3">Todavía no hay Entrevista Red Human para esta postulación.</p>
        )}
      </Card>

      {/* Tarjeta de la evaluación del avatar de entrevista — solo texto descriptivo, sin score:
          el número de afinidad es de Luna (arriba); esto es lo que se habló en la entrevista. */}
      {/* ===== 3) EVALUACIÓN INTEGRAL (Análisis de CV + Entrevista Red Human) — solo con entrevista válida ===== */}
      {evalAvatar?.resumen && (
        <Card className="border-human/30 bg-human-soft/20 p-5">
          <span className="font-mono text-[10px] font-bold uppercase tracking-wider text-human">
            3 · Evaluación integral (CV + Entrevista Red Human)
          </span>
          <div className="mt-2 flex flex-wrap items-center gap-4">
            {evalAvatar.match_perfil != null && <ScoreRing score={evalAvatar.match_perfil} />}
            <div>
              {evalAvatar.match_perfil != null && <p className="font-display text-lg font-bold text-ink">Afinidad {evalAvatar.match_perfil}/100</p>}
              {evalAvatar.recomendacion && (
                <p className="text-xs text-ink-2">
                  Recomendación preliminar: <b className="text-ink">{evalAvatar.recomendacion === "avanzar" ? "avanzar" : evalAvatar.recomendacion === "no_avanzar" ? "no avanzar" : "revisión humana"}</b> — la decisión final es de RH.
                </p>
              )}
            </div>
          </div>
          <p className="mt-2 text-sm leading-relaxed text-ink">{evalAvatar.resumen}</p>
          {Boolean(evalAvatar.faltante?.length) && (
            <p className="mt-2 text-xs leading-relaxed text-warn">La entrevista no cubrió: {evalAvatar.faltante!.join(", ")} — validar en la Entrevista Humana.</p>
          )}

          {Boolean(evalAvatar.fortalezas?.length) && (
            <div className="mt-3">
              <p className="font-mono text-[11px] uppercase tracking-wider text-good font-bold">Fortalezas observadas</p>
              <ul className="mt-1.5 space-y-1">
                {evalAvatar.fortalezas!.map((x, i) => (
                  <li key={i} className="text-xs leading-relaxed text-ink-2">• {x}</li>
                ))}
              </ul>
            </div>
          )}

          {Boolean(evalAvatar.riesgos?.length) && (
            <div className="mt-3">
              <p className="font-mono text-[11px] uppercase tracking-wider text-warn font-bold">Puntos por validar</p>
              <ul className="mt-1.5 space-y-1">
                {evalAvatar.riesgos!.map((x, i) => (
                  <li key={i} className="text-xs leading-relaxed text-ink-2">• {x}</li>
                ))}
              </ul>
            </div>
          )}

          {Boolean(evalAvatar.areas_desarrollo?.length) && (
            <div className="mt-3">
              <p className="font-mono text-[11px] uppercase tracking-wider text-brand font-bold">Áreas de desarrollo</p>
              <ul className="mt-1.5 space-y-1">
                {evalAvatar.areas_desarrollo!.map((x, i) => (
                  <li key={i} className="text-xs leading-relaxed text-ink-2">• {x}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Fase 4 (Punto 5): conocimiento profundo con evidencia; solo existe en evaluaciones nuevas. */}
          {evalAvatar.perfil && (
            <div className="mt-4">
              <PerfilProfundoVista perfil={evalAvatar.perfil} />
            </div>
          )}
        </Card>
      )}

      {/* Requisitos Cumplidos vs Brechas */}
      {(a.requisitos_cumplidos?.length || a.brechas?.length) ? (
        <div className="grid gap-3.5 sm:grid-cols-2">
          {Boolean(a.requisitos_cumplidos?.length) && (
            <Card className="border-good/30 bg-good-soft/20 p-4">
              <p className="font-mono text-[11px] uppercase tracking-wider text-good font-bold flex items-center gap-1.5">
                <CheckCircle2 className="h-4 w-4" /> Requisitos Cumplidos ({a.requisitos_cumplidos!.length})
              </p>
              <ul className="mt-2.5 space-y-1.5">
                {a.requisitos_cumplidos!.map((x, i) => (
                  <li key={i} className="text-xs leading-relaxed text-ink-2 flex items-start gap-1.5">
                    <span className="text-good font-bold">•</span>
                    <span>{x}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {Boolean(a.brechas?.length) && (
            <Card className="border-warn/30 bg-warn-soft/20 p-4">
              <p className="font-mono text-[11px] uppercase tracking-wider text-warn font-bold flex items-center gap-1.5">
                <AlertTriangle className="h-4 w-4" /> Brechas o Puntos por Validar ({a.brechas!.length})
              </p>
              <ul className="mt-2.5 space-y-1.5">
                {a.brechas!.map((x, i) => (
                  <li key={i} className="text-xs leading-relaxed text-ink-2 flex items-start gap-1.5">
                    <span className="text-warn font-bold">•</span>
                    <span>{x}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      ) : null}

      {/* Respuestas Estructuradas del Pre-filtro (WhatsApp) */}
      {Boolean(a.respuestas_prefiltro?.length) && (
        <div>
          <Eyebrow>Entrevista Pre-filtro por WhatsApp ({a.respuestas_prefiltro!.length} respuestas)</Eyebrow>
          <Card className="mt-2 border-good/30 bg-good-soft/10 p-5">
            <div className="space-y-3">
              {a.respuestas_prefiltro!.map((r, i) => (
                <div key={i} className="rounded-xl border border-border-soft bg-surface p-3.5 shadow-sm">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-good/15 text-good font-mono text-[11px] font-bold">
                        {i + 1}
                      </span>
                      <p className="font-semibold text-xs sm:text-sm text-ink">{r.criterio || r.pregunta}</p>
                    </div>

                    {r.cumple !== null && r.cumple !== undefined && (
                      <span
                        className={cn(
                          "shrink-0 rounded-full px-2.5 py-0.5 font-mono text-[10px] font-bold uppercase",
                          r.cumple
                            ? "border border-good/30 bg-good-soft text-good"
                            : "border border-bad/30 bg-bad-soft text-bad"
                        )}
                      >
                        {r.cumple ? "Cumple" : "No cumple"}
                      </span>
                    )}
                  </div>

                  <div className="mt-2.5 rounded-lg bg-surface-2/60 p-2.5 pl-3 border-l-2 border-brand/50">
                    <p className="text-xs leading-relaxed text-ink-2 italic">“{r.respuesta}”</p>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}

      {/* Historial de Entrevistas Humanas — puede haber varias rondas (ver EntrevistaHumana);
          agendar, marcar realizada y el recordatorio siguen viviendo exclusivamente en
          PanelEntrevistaHumana (acción activa sobre la ronda más reciente, no se duplica
          aquí). Esto es únicamente el historial de solo lectura, más reciente primero. */}
      {historialEh.length > 0 && (
        <div>
          <Eyebrow>Historial de Entrevistas Humanas ({historialEh.length})</Eyebrow>
          <div className="mt-2 flex flex-col gap-2.5">
            {historialEh.map((eh, i) => (
              <Card key={i} className="border-[color:var(--brand-2)]/30 bg-surface-2/40 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-ink">
                    {eh.entrevistador || "Sin asignar"}
                    {eh.fecha && (
                      <span className="ml-2 font-normal text-ink-3">
                        {textoCita(eh.fecha)}
                      </span>
                    )}
                  </p>
                  {eh.resultado ? (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge tone={eh.resultado === "aprobado" ? "good" : "bad"} dot>
                        {eh.resultado === "aprobado" ? "Aprobado" : "No aprobado"}
                      </Badge>
                      {eh.recomendacion && <Badge tone="brand">{RECOMENDACION_LABEL[eh.recomendacion]}</Badge>}
                    </div>
                  ) : (
                    <Badge tone="neutral">{eh.realizada ? "Esperando evaluación" : "Programada"}</Badge>
                  )}
                </div>
                <p className="mt-1 text-[11px] text-ink-3">
                  {eh.modalidad || "Modalidad sin definir"}
                  {eh.resultado &&
                    ` · Registrado por ${eh.resultadoCapturadoPor === "entrevistador" ? "el entrevistador" : "RH"}`}
                </p>
                {eh.comentario && <p className="mt-2 text-[13px] leading-relaxed text-ink-2">{eh.comentario}</p>}
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* ===== Evaluaciones y verificaciones (2026-09-28): no mueven la columna del pipeline ===== */}
      <PanelEvaluaciones codigo={c.id} puesto={c.puesto} live={Boolean(live) && puedeDecidir} version={versionEval} />
    </div>
  );
}

/* ============================================================
   PESTAÑA 3: CV y documentos (habilidades, estudios/idiomas, alertas, archivos)
   ============================================================ */
function PestanaDocumentos({
  c,
  live,
  onCambio,
  setAviso,
}: {
  c: Candidato;
  live: boolean;
  onCambio: (c: Candidato) => void;
  setAviso: (a: AvisoEstado) => void;
}) {
  const puedeDecidir = usePuedeDecidir();
  const [cargandoCV, setCargandoCV] = useState(false);
  // 2026-09-20 (B3): documentos requeridos del expediente con su trazabilidad (solicitud → recepción)
  const [expediente, setExpediente] = useState<NuevoIngreso | null>(null);
  const cargarExpediente = useCallback(async () => {
    if (!live || !c.expedienteId) return;
    const e = await fetchExpediente(c.expedienteId);
    if (e) setExpediente(e);
  }, [c.expedienteId, live]);
  useEffect(() => {
    void cargarExpediente();
  }, [cargarExpediente]);
  usePolling(cargarExpediente, 20000);

  const cv = (c.cvDatos || {}) as Record<string, unknown>;
  const habilidades = (cv.habilidades as string[]) || [];
  const estudios = (cv.estudios as string[]) || [];
  const idiomas = (cv.idiomas as string[]) || [];
  const a = c.analisis ?? {};
  const alertas = (cv.alertas as string[]) || (a.alertas || []);
  const faltantes = (cv.datos_faltantes as string[]) || (a.datos_faltantes || []);
  const listaArchivos = c.listaArchivos ?? [];

  async function subirCV(archivos: File[]) {
    setCargandoCV(true);
    setAviso(null);
    const r = await subirArchivoCandidato(c.id, archivos[0], "cv");
    setCargandoCV(false);
    if (!r.ok) {
      setAviso({ tono: "error", texto: r.error });
      return;
    }
    setAviso({ tono: "ok", texto: "CV procesado exitosamente: datos y score de afinidad actualizados." });
    if (r.data.candidato) onCambio(r.data.candidato);
  }

  return (
    <div className="flex flex-col gap-5">
      {/* Habilidades detectadas */}
      {habilidades.length > 0 && (
        <div>
          <Eyebrow>Habilidades y Competencias ({habilidades.length})</Eyebrow>
          <div className="mt-2 flex flex-wrap gap-2">
            {habilidades.map((h, i) => (
              <span
                key={i}
                className="inline-flex items-center gap-1.5 rounded-lg border border-brand/25 bg-brand-soft px-3 py-1.5 text-xs font-medium text-brand"
              >
                <Award className="h-3.5 w-3.5 text-brand" /> {h}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Estudios e Idiomas */}
      {(estudios.length > 0 || idiomas.length > 0) && (
        <div className="grid gap-3.5 sm:grid-cols-2">
          {estudios.length > 0 && (
            <Card className="p-4">
              <Eyebrow>Formación Académica ({estudios.length})</Eyebrow>
              <ul className="mt-2 space-y-1.5">
                {estudios.map((e, i) => (
                  <li key={i} className="flex items-center gap-2 text-xs text-ink-2">
                    <GraduationCap className="h-4 w-4 text-ink-3 shrink-0" /> {e}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {idiomas.length > 0 && (
            <Card className="p-4">
              <Eyebrow>Idiomas</Eyebrow>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {idiomas.map((idm, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-border-soft bg-surface-2 px-2.5 py-1 text-xs text-ink-2"
                  >
                    <Globe className="h-3.5 w-3.5 text-ink-3" /> {idm}
                  </span>
                ))}
              </div>
            </Card>
          )}
        </div>
      )}

      {/* Alertas del CV */}
      {(alertas.length > 0 || faltantes.length > 0) && (
        <div className="rounded-2xl border border-warn/30 bg-warn-soft/30 p-4">
          <div className="flex items-center gap-2 text-warn font-semibold text-xs">
            <AlertTriangle className="h-4 w-4" />
            <span>Focos de atención detectados por la IA en el CV</span>
          </div>
          {alertas.length > 0 && (
            <ul className="mt-2 space-y-1">
              {alertas.map((al, i) => (
                <li key={i} className="text-xs text-warn">• {al}</li>
              ))}
            </ul>
          )}
          {faltantes.length > 0 && (
            <ul className="mt-1 space-y-1">
              {faltantes.map((df, i) => (
                <li key={i} className="text-xs text-ink-3">• Dato faltante: {df}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* 2026-09-20 (B3): trazabilidad de los documentos requeridos del expediente */}
      {c.expedienteId != null && (
        <div>
          <Eyebrow>Documentos requeridos · trazabilidad</Eyebrow>
          {!expediente ? (
            <p className="mt-2 text-xs text-ink-3">Cargando expediente…</p>
          ) : (expediente.documentos ?? []).length === 0 ? (
            <p className="mt-2 text-xs text-ink-3">El expediente todavía no tiene documentos requeridos.</p>
          ) : (
            <div className="scroll-x mt-2 rounded-2xl border border-border-soft">
              <table className="w-full min-w-[640px] text-left text-xs">
                <thead className="bg-surface-2 text-[11px] uppercase tracking-wide text-ink-3">
                  <tr>
                    <th className="px-3 py-2 font-semibold">Documento</th>
                    <th className="px-3 py-2 font-semibold">Solicitado</th>
                    <th className="px-3 py-2 font-semibold">Canal</th>
                    <th className="px-3 py-2 font-semibold">Recibido</th>
                    <th className="px-3 py-2 font-semibold">Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {(expediente.documentos ?? []).map((d) => {
                    const estado = d.estadoSimple ?? (d.estado === "recibido" || (d.estado === "revision" && d.tieneArchivo) ? "Recibido" : d.estado === "rechazado" ? "Rechazado" : "Pendiente");
                    const solicitudes = d.solicitudes ?? [];
                    return (
                      <tr key={d.nombre} className="border-t border-border-soft align-top">
                        <td className="px-3 py-2">
                          <p className="font-semibold text-ink">{d.nombre}</p>
                          {d.obligatorio === false && <p className="text-[11px] text-ink-3">Opcional</p>}
                        </td>
                        <td className="px-3 py-2 text-ink-2">
                          {d.solicitadoEn ? (
                            <>
                              <p>{fechaHoraCorta(d.solicitadoEn)}</p>
                              {solicitudes.length > 1 && (
                                <p className="text-[11px] text-ink-3" title={solicitudes.map((s) => `${s.tipo === "recordatorio" ? "Recordatorio" : "Solicitud"} · ${fechaHoraCorta(s.en)} · ${canalLegible(s.canal)}`).join("\n")}>
                                  +{solicitudes.length - 1} recordatorio{solicitudes.length - 1 === 1 ? "" : "s"} · último {fechaHoraCorta(solicitudes[solicitudes.length - 1].en)}
                                </p>
                              )}
                            </>
                          ) : (
                            <span className="text-ink-3">Sin solicitar</span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-ink-2">{d.solicitadoCanal ? canalLegible(d.solicitadoCanal) : "—"}</td>
                        <td className="px-3 py-2 text-ink-2">
                          {d.recibidoEn ? (
                            <>
                              <p>{fechaHoraCorta(d.recibidoEn)}</p>
                              <p className="text-[11px] text-ink-3">por {canalLegible(d.recibidoCanal || "")}{d.archivo ? ` · ${d.archivo}` : ""}</p>
                            </>
                          ) : (
                            <span className="text-ink-3">—</span>
                          )}
                        </td>
                        <td className="px-3 py-2">
                          <span
                            className={cn(
                              "inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold",
                              estado === "Recibido" ? "bg-good-soft text-good" : estado === "Rechazado" ? "bg-bad-soft text-bad" : "bg-warn-soft text-warn",
                            )}
                          >
                            {estado}
                          </span>
                          {estado === "Recibido" && d.estado === "revision" && <p className="mt-0.5 text-[11px] text-ink-3">En revisión de RH</p>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Archivos y Descarga de CV */}
      <div>
        <Eyebrow>Documentos Adjuntos</Eyebrow>
        <div className="mt-2 flex flex-col gap-2.5">
          {listaArchivos.map((a) => (
            <Card key={a.id} className="flex items-center justify-between gap-3 p-3.5">
              <div className="flex items-center gap-3 min-w-0">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-surface-2 text-brand">
                  <FileText className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-ink">{a.nombre}</p>
                  <p className="font-mono text-[11px] text-ink-3">
                    {a.tipo} · {pesoLegible(a.tamano)} · {a.subido}
                  </p>
                </div>
              </div>

              <a
                href={urlArchivoCandidato(c.id, a.id)}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1.5 shrink-0 rounded-xl border border-border-soft bg-surface-2 px-3 py-1.5 text-xs font-semibold text-ink transition hover:bg-surface hover:text-brand"
              >
                <Download className="h-3.5 w-3.5" /> Descargar
              </a>
            </Card>
          ))}

          {live && puedeDecidir && (
            <Dropzone compacto onArchivos={subirCV} cargando={cargandoCV} titulo="Subir nuevo CV o actualización" />
          )}
        </div>
      </div>
    </div>
  );
}

/* ============================================================
   PESTAÑA 2: Chat de WhatsApp (Historial de Pre-filtro con IA)
   ============================================================ */
function PestanaWhatsApp({
  c,
  live,
  onCambio,
}: {
  c: Candidato;
  live: boolean;
  onCambio: (c: Candidato) => void;
}) {
  const [msgs, setMsgs] = useState<MensajePrefiltro[]>([]);
  const [texto, setTexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [cargandoMsgs, setCargandoMsgs] = useState(false);

  const cargar = useCallback(async () => {
    if (!live) return;
    setCargandoMsgs(true);
    const m = await fetchMensajes(c.id);
    if (m) setMsgs(m);
    setCargandoMsgs(false);
  }, [c.id, live]);

  useEffect(() => {
    cargar();
  }, [cargar]);

  async function enviar() {
    const t = texto.trim();
    if (!t || enviando) return;
    setTexto("");
    setEnviando(true);
    const r = await enviarPrefiltro(c.id, t, "whatsapp");
    setEnviando(false);
    if (!r.ok) return;
    const nuevos = await fetchMensajes(c.id);
    if (nuevos) setMsgs(nuevos);
    if (r.data.clasificacion) {
      const actualizado = await fetchCandidato(c.id);
      if (actualizado) onCambio(actualizado);
    }
  }

  if (!live) {
    return (
      <div className="py-12 text-center text-sm text-ink-3">
        <MessageCircle className="mx-auto h-8 w-8 text-ink-3/60 mb-2" />
        Levanta la API en el puerto 8001 para ver el historial y sincronización de WhatsApp en tiempo real.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3.5">
      {/* Header del Chat */}
      <div className="flex items-center justify-between rounded-2xl border border-border-soft bg-surface p-3.5">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-2xl bg-good/15 text-good">
            <MessageCircle className="h-5 w-5" />
          </span>
          <div>
            <p className="text-sm font-semibold text-ink">
              {c.telefono ? `WhatsApp: +${c.telefono}` : "Conversación de Pre-filtro"}
            </p>
            <p className="text-xs text-ink-3">
              {c.prefiltroCompleto ? "Prefiltro completado por el agente" : "Agente de IA (Luna) activo"} · {msgs.length} mensajes
            </p>
          </div>
        </div>

        <button
          onClick={cargar}
          disabled={cargandoMsgs}
          className="flex items-center gap-1.5 rounded-xl border border-border-soft bg-surface-2 px-3 py-1.5 text-xs font-semibold text-ink-2 hover:bg-surface-3 transition disabled:opacity-50"
          title="Actualizar conversación"
        >
          <RotateCw className={cn("h-3.5 w-3.5", cargandoMsgs && "animate-spin")} />
          Actualizar
        </button>
      </div>

      {/* Feed de Conversación de WhatsApp */}
      <div className="flex max-h-[380px] min-h-[260px] flex-col gap-3 overflow-y-auto rounded-2xl border border-border-soft bg-surface-2/40 p-4">
        {msgs.length === 0 && !cargandoMsgs && (
          <div className="py-12 text-center">
            <MessageCircle className="mx-auto h-10 w-10 text-ink-3/40" />
            <p className="mt-2 text-sm font-medium text-ink-3">Aún no hay mensajes en este chat.</p>
            <p className="mt-0.5 text-xs text-ink-3">
              Cuando el candidato escriba a tu bot de WhatsApp, las preguntas y respuestas aparecerán aquí en vivo.
            </p>
          </div>
        )}

        {msgs.map((m, i) => {
          const esIA = m.rol === "assistant";
          return (
            <div
              key={i}
              className={cn(
                "flex flex-col max-w-[85%] rounded-2xl p-3.5 text-xs sm:text-[13px] leading-relaxed shadow-sm",
                esIA
                  ? "self-start rounded-bl-sm border border-border-soft bg-surface text-ink-2"
                  : "self-end rounded-br-sm bg-brand text-brand-ink",
              )}
            >
              <div className="mb-1 flex items-center justify-between gap-3 text-[10px]">
                <span className={cn("font-semibold flex items-center gap-1", esIA ? "text-human" : "text-brand-ink/80")}>
                  {esIA ? <Sparkles className="h-3 w-3" /> : <MessageCircle className="h-3 w-3" />}
                  {esIA ? "Agente Red Human (Luna)" : (c.nombre || "Candidato")}
                </span>
                <span className={cn("font-mono", esIA ? "text-ink-3" : "text-brand-ink/70")}>
                  {m.canal === "whatsapp" ? "WhatsApp" : "Simulador"}
                </span>
              </div>
              <p className="whitespace-pre-wrap">{m.texto}</p>
            </div>
          );
        })}

        {enviando && (
          <div className="self-start rounded-2xl rounded-bl-sm bg-surface p-3 text-ink-3 border border-border-soft shadow-sm">
            <div className="flex items-center gap-2 text-xs">
              <Loader2 className="h-4 w-4 animate-spin text-brand" />
              <span>Luna está procesando la respuesta...</span>
            </div>
          </div>
        )}
      </div>

      {/* Simulador de Chat / Envío Rápido */}
      <div className="flex gap-2">
        <input
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && enviar()}
          placeholder="Escribir mensaje simulado (prueba de pre-filtro)…"
          className="h-11 flex-1 rounded-xl border border-border-soft bg-surface px-3.5 text-xs sm:text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
        />
        <Button size="md" onClick={enviar} disabled={enviando || !texto.trim()}>
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

/* ============================================================
   MODAL DE CARGA MASIVA DE CVS
   ============================================================ */
function CargarCVs({
  vacantes,
  onClose,
  onListo,
}: {
  vacantes: Vacante[];
  onClose: () => void;
  onListo: (codigo?: string) => void;
}) {
  const [vacante, setVacante] = useState(vacantes[0]?.id ?? "");
  const [fuente, setFuente] = useState("RH");
  const [cargando, setCargando] = useState(false);
  const [res, setRes] = useState<CargaCV | null>(null);
  const [error, setError] = useState("");

  async function procesar(archivos: File[]) {
    setCargando(true);
    setError("");
    setRes(null);
    const r = await subirCVs(archivos, { vacante: vacante || undefined, fuente });
    setCargando(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setRes(r.data);
    onListo();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-0 backdrop-blur-md sm:p-4">
      <div className="relative flex h-[100dvh] w-full max-w-2xl flex-col overflow-hidden border border-border-soft bg-bg shadow-2xl sm:h-auto sm:max-h-[90vh] sm:rounded-3xl">
        <div className="glass sticky top-0 z-10 flex items-center justify-between gap-2 border-b border-border-soft px-4 py-3 sm:px-6 sm:py-4">
          <div>
            <Eyebrow>Ingesta de prospectos</Eyebrow>
            <h2 className="font-display text-lg font-bold text-ink">Cargar CVs con Extracción de IA</h2>
          </div>
          <button onClick={onClose} className="grid h-9 w-9 place-items-center rounded-xl text-ink-2 hover:bg-surface-2">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex flex-col gap-5 overflow-y-auto p-6">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Vacante</span>
              <select
                value={vacante}
                onChange={(e) => setVacante(e.target.value)}
                className="h-11 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
              >
                <option value="">Sin vacante (solo extraer datos)</option>
                {vacantes.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.titulo} · {v.ubicacion}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Fuente</span>
              <select
                value={fuente}
                onChange={(e) => setFuente(e.target.value)}
                className="h-11 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
              >
                {["RH", "OCC", "LinkedIn", "Indeed", "Formulario", "WhatsApp"].map((f) => (
                  <option key={f}>{f}</option>
                ))}
              </select>
            </label>
          </div>

          <Aviso tono="info">
            Con vacante seleccionada, el agente Luna extrae los datos del CV y califica automáticamente la afinidad.
          </Aviso>

          <Dropzone
            multiple
            cargando={cargando}
            onArchivos={procesar}
            titulo="Arrastra hasta 20 CVs o haz clic para elegirlos"
          />

          {error && <Aviso tono="error">{error}</Aviso>}

          {res && (
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <Badge tone="good" dot>
                  {res.procesados} procesado(s)
                </Badge>
                {res.fallidos > 0 && (
                  <Badge tone="bad" dot>
                    {res.fallidos} rechazado(s)
                  </Badge>
                )}
              </div>

              {res.resultados.map((r, i) => (
                <Card key={i} className={cn("p-3.5", !r.ok && "border-bad/25 bg-bad-soft/30")}>
                  <div className="flex items-start gap-3">
                    <span
                      className={cn(
                        "mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg",
                        r.ok ? "bg-good-soft text-good" : "bg-bad-soft text-bad",
                      )}
                    >
                      {r.ok ? <CheckCircle2 className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-mono text-xs text-ink-3">{r.archivo}</p>
                      {r.ok && r.candidato ? (
                        <>
                          <button
                            onClick={() => onListo(r.candidato!.id)}
                            className="mt-0.5 text-left text-sm font-semibold hover:text-brand hover:underline"
                          >
                            {r.candidato.nombre}
                            <span className="ml-1.5 font-mono text-xs font-normal text-ink-3">{r.candidato.id}</span>
                          </button>
                          <div className="mt-1.5 flex flex-wrap items-center gap-2">
                            <EstadoBadge estado={r.candidato.estado} />
                            <span className="font-mono text-[11px] text-ink-3">match {r.candidato.score}</span>
                            {r.duplicado && <Badge tone="warn">ya existía</Badge>}
                          </div>
                        </>
                      ) : (
                        <p className="mt-0.5 text-[13px] leading-relaxed text-bad">{r.error}</p>
                      )}
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ============================================================
   Confirmación genérica (checkbox "Entrevista realizada", etc.)
   ============================================================ */
function ModalConfirmar({
  titulo,
  texto,
  onCancelar,
  onConfirmar,
  cargando,
}: {
  titulo: string;
  texto: string;
  onCancelar: () => void;
  onConfirmar: () => void;
  cargando?: boolean;
}) {
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <Card className="w-full max-w-sm p-5">
        <h3 className="font-display text-lg font-bold">{titulo}</h3>
        <p className="mt-1.5 text-[13px] leading-relaxed text-ink-2">{texto}</p>
        <div className="mt-5 flex gap-3">
          <Button variant="outline" className="flex-1" onClick={onCancelar} disabled={cargando}>
            Cancelar
          </Button>
          <Button className="flex-1" onClick={onConfirmar} disabled={cargando}>
            {cargando ? "Confirmando…" : "Confirmar"}
          </Button>
        </div>
      </Card>
    </div>
  );
}

/* ============================================================
   Etapa: Entrevista Humana — datos programados + "Entrevista realizada"
   ============================================================ */
function PanelEntrevistaHumana({
  c,
  live,
  onCambio,
}: {
  c: Candidato;
  live: boolean;
  onCambio: (c: Candidato) => void;
}) {
  const modoPrueba = useModoPrueba();
  const eh = c.entrevistaHumana;
  const [modalResultado, setModalResultado] = useState(false);
  const [modalModificar, setModalModificar] = useState(false);
  const [marcando, setMarcando] = useState(false);
  const [guardando, setGuardando] = useState(false);
  const [aviso, setAviso] = useState<AvisoEstado>(null);
  const [recordando, setRecordando] = useState(false);
  const [avisoRecordatorio, setAvisoRecordatorio] = useState<AvisoEstado>(null);
  const [cancelando, setCancelando] = useState(false);
  // Punto 12: confirmación ligera con la línea "Notificar: … · Editar" antes de cada acción.
  const [confirmacion, setConfirmacion] = useState<null | "realizada" | "recordatorio" | "cancelar">(null);
  const ultimoNotificar = useRef<NotificarAccion | undefined>(undefined);
  const hayCliente = Boolean(c.clienteVacante);

  /** Botón «Cancelar» (Fase D) — no mueve la tarjeta de etapa: RH agenda otra ronda o mueve la
   * etapa a mano según corresponda. */
  async function cancelar(notificar?: NotificarAccion) {
    setCancelando(true);
    setAviso(null);
    const r = await cancelarEntrevistaHumana(c.id, notificar);
    setCancelando(false);
    if (!r.ok) {
      setAviso({ tono: "error", texto: r.error });
      return;
    }
    onCambio(r.data);
  }

  /** Ya no pide resultado (Lote 3, Eje 2): solo confirma que la entrevista ocurrió y dispara el
   * correo con la liga al entrevistador. `forzarPrueba` (Lote 4): si Modo Prueba está activo y
   * el candidato ya no está en la etapa de Entrevista Humana, el aviso de error trae un botón
   * para reintentar saltando ese bloqueo. */
  async function marcarRealizada(forzarPrueba = false, notificar?: NotificarAccion) {
    if (notificar) ultimoNotificar.current = notificar;
    setMarcando(true);
    setAviso(null);
    const r = await marcarEntrevistaHumanaRealizada(c.id, forzarPrueba, ultimoNotificar.current);
    setMarcando(false);
    if (!r.ok) {
      setAviso({
        tono: "error", texto: r.error,
        reintentar: modoPrueba && !forzarPrueba ? () => marcarRealizada(true) : undefined,
      });
      return;
    }
    onCambio(r.data.candidato);
  }

  /** Respaldo manual de RH — captura la primera vez o corrige un resultado ya capturado
   * (por RH o por el entrevistador vía su liga). */
  async function guardarResultado(
    datos: { resultado: ResultadoEntrevistaHumana; recomendacion: RecomendacionEntrevistaHumana; comentario: string; notificar?: NotificarAccion },
    forzarPrueba = false,
  ) {
    setGuardando(true);
    setAviso(null);
    const r = await registrarResultadoEntrevistaHumana(c.id, datos, forzarPrueba);
    setGuardando(false);
    if (!r.ok) {
      setAviso({
        tono: "error", texto: r.error,
        reintentar: modoPrueba && !forzarPrueba ? () => guardarResultado(datos, true) : undefined,
      });
      return;
    }
    setModalResultado(false);
    onCambio(r.data);
  }

  async function enviarRecordatorio(forzarPrueba = false, notificar?: NotificarAccion) {
    if (notificar) ultimoNotificar.current = notificar;
    setRecordando(true);
    setAvisoRecordatorio(null);
    const r = await recordatorioEntrevistaHumana(c.id, forzarPrueba, ultimoNotificar.current);
    setRecordando(false);
    if (!r.ok) {
      setAvisoRecordatorio({
        tono: "error", texto: r.error,
        reintentar: modoPrueba && !forzarPrueba ? () => enviarRecordatorio(true) : undefined,
      });
      return;
    }
    const algunoEnviado = r.data.resultados.some((x) => x.enviado);
    setAvisoRecordatorio({
      tono: algunoEnviado ? "ok" : "warn",
      texto: algunoEnviado
        ? "Recordatorio enviado."
        : "No se envió nada — revisa la línea «Notificar» o Configuración → Notificaciones para este evento.",
    });
    onCambio(r.data.candidato);
  }

  if (!eh) return null;

  const detalleModalidad =
    eh.modalidad === "Videollamada"
      ? eh.liga && `${eh.porTeams ? "Reunión de Teams: " : "Liga: "}${eh.liga}`
      : eh.modalidad === "Presencial"
        ? eh.ubicacion && `Ubicación: ${eh.ubicacion}`
        : eh.modalidad === "Llamada"
          ? (eh.telefonoContacto || c.telefono) && `Teléfono: ${eh.telefonoContacto || c.telefono}`
          : "";

  const IconoModalidad = eh.modalidad === "Presencial" ? MapPin : eh.modalidad === "Llamada" ? Phone : Video;

  return (
    <Card className="border-[color:var(--brand-2)]/30 bg-surface-2/40 p-4">
      <Eyebrow>Entrevista Humana programada</Eyebrow>
      <div className="mt-2.5 grid gap-2 sm:grid-cols-2">
        <Info
          icon={UserCheck}
          v={`Entrevistador(a): ${eh.entrevistador || "sin asignar"}${eh.tipo ? ` (${eh.tipo === "interno" ? "interno" : "externo"})` : ""}`}
        />
        <Info
          icon={CalendarClock}
          v={textoCita(eh.fecha) || "Sin fecha"}
        />
        <Info icon={IconoModalidad} v={`Modalidad: ${eh.modalidad || "sin definir"}`} />
        {detalleModalidad && <Info icon={Mail} v={detalleModalidad} />}
      </div>
      {eh.comentario && <p className="mt-2.5 text-[13px] leading-relaxed text-ink-2">{eh.comentario}</p>}

      {aviso && (
        <div className="mt-3">
          <Aviso tono={aviso.tono}>
            {aviso.texto}
            {aviso.reintentar && (
              <Button size="sm" variant="outline" className="mt-2" onClick={aviso.reintentar}>
                <FlaskConical className="h-3.5 w-3.5" /> Continuar de todos modos (modo prueba)
              </Button>
            )}
          </Aviso>
        </div>
      )}

      {avisoRecordatorio && (
        <div className="mt-3">
          <Aviso tono={avisoRecordatorio.tono}>
            {avisoRecordatorio.texto}
            {avisoRecordatorio.reintentar && (
              <Button size="sm" variant="outline" className="mt-2" onClick={avisoRecordatorio.reintentar}>
                <FlaskConical className="h-3.5 w-3.5" /> Continuar de todos modos (modo prueba)
              </Button>
            )}
          </Aviso>
        </div>
      )}

      <div className="mt-3.5 flex flex-wrap items-center gap-2">
        {eh.cancelada ? (
          <Badge tone="bad" dot>Cancelada</Badge>
        ) : eh.resultado ? (
          <>
            <Badge tone={eh.resultado === "aprobado" ? "good" : "bad"} dot>
              {eh.resultado === "aprobado" ? "Aprobado" : "No aprobado"}
            </Badge>
            {eh.recomendacion && <Badge tone="brand">{RECOMENDACION_LABEL[eh.recomendacion]}</Badge>}
          </>
        ) : live ? (
          <>
            {/* 2026-09-19 (cambios Raúl): UN solo paso — «Entrevista realizada» abre el único modal
                (Resultado + Comentarios opcionales + Guardar). Sin confirmaciones intermedias. */}
            <Button size="sm" onClick={() => setModalResultado(true)} disabled={guardando || marcando}>
              <CheckCircle2 className="h-4 w-4" /> {guardando ? "Guardando…" : eh.realizada ? "Registrar resultado" : "Entrevista realizada"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setModalModificar(true)} title="No se realizó: reprogramar fecha, hora o modalidad">
              <RotateCw className="h-4 w-4" /> No realizada / Reprogramar
            </Button>
            <MenuAcciones
              acciones={[
                { etiqueta: "Reenviar liga de evaluación al entrevistador", icono: <Send />, onClick: () => setConfirmacion("realizada"), disabled: marcando },
                { etiqueta: "Enviar recordatorio", icono: <RotateCw />, onClick: () => setConfirmacion("recordatorio"), disabled: recordando },
                { etiqueta: "Modificar datos", icono: <Pencil />, onClick: () => setModalModificar(true) },
                { etiqueta: "Cancelar entrevista", icono: <XCircle />, peligrosa: true, onClick: () => setConfirmacion("cancelar"), disabled: cancelando },
              ]}
            />
          </>
        ) : null}
      </div>

      {eh.resultado && live && (
        <div className="mt-2.5 flex flex-wrap items-center gap-2.5">
          <span className="text-[11px] text-ink-3">
            Registrado por {eh.resultadoCapturadoPor === "entrevistador" ? "el entrevistador (liga)" : "RH"}.
          </span>
          <button onClick={() => setModalResultado(true)} disabled={guardando} className="text-[11px] font-semibold text-brand hover:underline">
            Corregir resultado
          </button>
        </div>
      )}
      {eh.realizada && !eh.resultado && live && (
        <p className="mt-2 text-[11px] text-ink-3">Esperando la evaluación del entrevistador desde su liga; también puedes registrarla aquí.</p>
      )}

      {confirmacion === "realizada" && (
        <ConfirmacionAccion
          titulo="Reenviar liga de evaluación"
          texto="Se le manda al entrevistador (correo HTML / WhatsApp) la liga con el expediente y el formulario de evaluación."
          evento="entrevista_humana_terminada"
          hayCliente={hayCliente}
          clienteId={c.clienteIdVacante ?? null}
          etiquetaConfirmar="Enviar liga"
          onCancelar={() => setConfirmacion(null)}
          onConfirmar={async (n) => {
            setConfirmacion(null);
            await marcarRealizada(false, n);
          }}
        />
      )}
      {confirmacion === "recordatorio" && (
        <ConfirmacionAccion
          titulo="Enviar recordatorio de la entrevista"
          evento="recordatorio_entrevista"
          hayCliente={hayCliente}
          clienteId={c.clienteIdVacante ?? null}
          etiquetaConfirmar="Enviar"
          onCancelar={() => setConfirmacion(null)}
          onConfirmar={async (n) => {
            setConfirmacion(null);
            await enviarRecordatorio(false, n);
          }}
        />
      )}
      {confirmacion === "cancelar" && (
        <ConfirmacionAccion
          titulo="¿Cancelar esta entrevista?"
          texto="No se mueve la etapa del candidato; después puedes agendar otra ronda."
          evento="entrevista_cancelada"
          hayCliente={hayCliente}
          clienteId={c.clienteIdVacante ?? null}
          etiquetaConfirmar="Cancelar entrevista"
          tono="bad"
          onCancelar={() => setConfirmacion(null)}
          onConfirmar={async (n) => {
            setConfirmacion(null);
            await cancelar(n);
          }}
        />
      )}
      {modalResultado && (
        <ModalCerrarEntrevistaHumana
          hayCliente={hayCliente}
          clienteId={c.clienteIdVacante ?? null}
          inicial={
            eh.resultado
              ? { resultado: eh.resultado, recomendacion: eh.recomendacion, comentario: eh.comentario }
              : undefined
          }
          onCancelar={() => setModalResultado(false)}
          onConfirmar={guardarResultado}
          cargando={guardando}
        />
      )}

      {modalModificar && (
        <ModalModificarEntrevista
          c={c}
          eh={eh}
          onClose={() => setModalModificar(false)}
          onListo={(datos) => {
            setModalModificar(false);
            onCambio(datos);
          }}
        />
      )}
    </Card>
  );
}

/* ============================================================
   Modal "Marcar entrevista realizada" — Resultado + Recomendación obligatorios
   ============================================================ */
function ModalCerrarEntrevistaHumana({
  inicial,
  hayCliente = false,
  clienteId,
  onCancelar,
  onConfirmar,
  cargando,
}: {
  hayCliente?: boolean;
  clienteId?: number | null;
  /** Presente cuando ya había un resultado capturado — el modal pasa a modo "corregir" y
   * precarga los valores actuales. */
  inicial?: {
    resultado: ResultadoEntrevistaHumana | null;
    recomendacion: RecomendacionEntrevistaHumana | null;
    comentario: string;
  };
  onCancelar: () => void;
  onConfirmar: (datos: {
    resultado: ResultadoEntrevistaHumana;
    recomendacion: RecomendacionEntrevistaHumana;
    comentario: string;
    notificar?: NotificarAccion;
  }) => void;
  cargando?: boolean;
}) {
  const notificar = useNotificarAccion("recomendacion_final");
  const [resultado, setResultado] = useState<ResultadoEntrevistaHumana | "">(inicial?.resultado ?? "");
  const [comentario, setComentario] = useState(inicial?.comentario ?? "");
  const [segunda, setSegunda] = useState(inicial?.recomendacion === "segunda_entrevista");

  // 2026-09-19 (cambios Raúl): un solo paso. La recomendación se deriva del resultado
  // (Aprobado → avanzar, Rechazado → no avanzar; «pedir segunda entrevista» es una casilla opcional).
  const recomendacion: RecomendacionEntrevistaHumana | "" = segunda ? "segunda_entrevista" : resultado === "aprobado" ? "avanzar" : resultado === "no_aprobado" ? "no_avanzar" : "";
  const listo = !!resultado;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <Card className="w-full max-w-md p-5">
        <h3 className="font-display text-lg font-bold">{inicial ? "Corregir resultado" : "Entrevista realizada"}</h3>
        <p className="mt-1 text-[13px] leading-relaxed text-ink-2">
          {inicial ? "Vas a sobreescribir el resultado ya registrado." : "Registra el resultado y listo: la entrevista queda confirmada y se habilitan los siguientes pasos."}
        </p>

        <div className="mt-4 flex flex-col gap-4">
          <div>
            <span className="text-sm font-medium text-ink-2">Resultado</span>
            <div className="mt-1.5 grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setResultado("aprobado")}
                className={`h-10 rounded-xl border text-sm font-medium transition ${
                  resultado === "aprobado" ? "border-good/25 bg-good-soft text-good" : "border-border-soft text-ink-2"
                }`}
              >
                Aprobado
              </button>
              <button
                type="button"
                onClick={() => setResultado("no_aprobado")}
                className={`h-10 rounded-xl border text-sm font-medium transition ${
                  resultado === "no_aprobado" ? "border-bad/25 bg-bad-soft text-bad" : "border-border-soft text-ink-2"
                }`}
              >
                Rechazado
              </button>
            </div>
          </div>

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-2">Comentarios <span className="text-ink-3">(opcional)</span></span>
            <textarea
              value={comentario}
              onChange={(e) => setComentario(e.target.value)}
              rows={3}
              placeholder="Lo que quieras dejar registrado de la entrevista…"
              className="rounded-xl border border-border-soft bg-surface px-3.5 py-2.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
            />
          </label>

          <label className="flex cursor-pointer items-center gap-2 text-[13px] text-ink-2">
            <input type="checkbox" checked={segunda} onChange={(e) => setSegunda(e.target.checked)} className="h-4 w-4 accent-[var(--brand)]" />
            Pedir una segunda entrevista
          </label>
        </div>

        <div className="mt-5 flex gap-3">
          <Button variant="outline" className="flex-1" onClick={onCancelar} disabled={cargando}>
            Cancelar
          </Button>
          <Button
            className="flex-1"
            onClick={() =>
              onConfirmar({
                resultado: resultado as ResultadoEntrevistaHumana,
                recomendacion: recomendacion as RecomendacionEntrevistaHumana,
                comentario,
                notificar: notificar.value,
              })
            }
            disabled={cargando || !listo}
          >
            {cargando ? "Guardando…" : "Confirmar"}
          </Button>
        </div>
      </Card>
    </div>
  );
}

/* ============================================================
   Modal "Programar entrevista" (botón «Enviar a Entrevista Humana»)
   ============================================================ */
function ModalProgramarEntrevista({
  c,
  onClose,
  onListo,
}: {
  c: Candidato;
  onClose: () => void;
  onListo: (c: Candidato, resultados: ResultadoNotificacion[], advertencias?: string[]) => void;
}) {
  const notificar = useNotificarAccion("entrevista_agendada");
  const clienteId = c.clienteIdVacante ?? null;
  const [entrevistadores, setEntrevistadores] = useState<Entrevistador[]>([]);
  const [contactos, setContactos] = useState<ContactoCliente[] | null>(clienteId ? null : []);
  const [tipoEntrevistador, setTipoEntrevistador] = useState<TipoEntrevistador>("interno");
  const [entrevistadorUsuarioId, setEntrevistadorUsuarioId] = useState<number | null>(null);
  // Fase 7A: externo = contacto del Cliente (id) u «Otro entrevistador» (OTRO → captura manual)
  const OTRO = "otro";
  const [contactoSel, setContactoSel] = useState<number | typeof OTRO | "">("");
  const [entrevistadorNombre, setEntrevistadorNombre] = useState("");
  const [entrevistadorCorreo, setEntrevistadorCorreo] = useState("");
  const [entrevistadorWhatsapp, setEntrevistadorWhatsapp] = useState("");
  const [fecha, setFecha] = useState("");
  const [hora, setHora] = useState("");
  const [modalidad, setModalidad] = useState<ModalidadEntrevistaHumana>("Videollamada");
  const [liga, setLiga] = useState("");
  const [ubicacion, setUbicacion] = useState("");
  const [telefonoContacto, setTelefonoContacto] = useState("");
  const [comentario, setComentario] = useState("");
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [preview, setPreview] = useState<null | "entrevistador" | "candidato">(null);
  // Fase 7B: con Teams conectado en la Cuenta la videollamada se crea sola; «Usar otra liga» = excepción
  const [teamsConectado, setTeamsConectado] = useState(false);
  const [otraLiga, setOtraLiga] = useState(false);
  const porTeams = modalidad === "Videollamada" && teamsConectado && !otraLiga;

  useEffect(() => {
    fetchEntrevistadores().then((d) => {
      if (d && d.length) {
        setEntrevistadores(d);
        setEntrevistadorUsuarioId(d[0].id);
      }
    });
    fetchIntegracionTeams().then((t) => setTeamsConectado(Boolean(t?.disponible && t?.conectado)));
  }, []);

  useEffect(() => {
    if (!clienteId) return;
    let vivo = true;
    fetchCliente(clienteId).then((cl) => vivo && setContactos(cl?.listaContactos ?? []));
    return () => {
      vivo = false;
    };
  }, [clienteId]);

  // sin Cliente en la vacante (o sin contactos) el externo va directo a «Otro entrevistador»
  useEffect(() => {
    if (contactos !== null && contactos.length === 0 && contactoSel === "") setContactoSel(OTRO);
  }, [contactos, contactoSel]);

  const internoSel = entrevistadores.find((u) => u.id === entrevistadorUsuarioId) ?? null;
  const contactoElegido = typeof contactoSel === "number" ? (contactos ?? []).find((k) => k.id === contactoSel) ?? null : null;
  const esOtro = contactoSel === OTRO;

  async function programar() {
    if (!fecha || !hora) {
      setError("Completa fecha y hora.");
      return;
    }
    if (tipoEntrevistador === "interno" && !entrevistadorUsuarioId) {
      setError("Selecciona quién entrevista.");
      return;
    }
    if (tipoEntrevistador === "externo" && contactoSel === "") {
      setError("Elige un contacto del Cliente o «+ Otro entrevistador».");
      return;
    }
    if (tipoEntrevistador === "externo" && esOtro && (!entrevistadorNombre.trim() || !entrevistadorCorreo.trim())) {
      setError("Indica nombre y correo del entrevistador.");
      return;
    }
    if (modalidad === "Videollamada" && !porTeams && !liga.trim()) {
      setError("Falta la liga de la videollamada.");
      return;
    }
    if (modalidad === "Presencial" && !ubicacion.trim()) {
      setError("Falta la ubicación de la entrevista.");
      return;
    }
    setEnviando(true);
    setError("");
    const r = await programarEntrevistaHumana(c.id, {
      tipoEntrevistador,
      entrevistadorUsuarioId: tipoEntrevistador === "interno" ? entrevistadorUsuarioId : null,
      entrevistadorContactoId: tipoEntrevistador === "externo" && typeof contactoSel === "number" ? contactoSel : null,
      usarTeams: porTeams,
      entrevistadorNombre: tipoEntrevistador === "externo" && esOtro ? entrevistadorNombre : "",
      entrevistadorCorreo: tipoEntrevistador === "externo" && esOtro ? entrevistadorCorreo : "",
      entrevistadorWhatsapp: tipoEntrevistador === "externo" && esOtro ? entrevistadorWhatsapp : "",
      fecha,
      hora,
      modalidad,
      liga: porTeams ? "" : liga,
      ubicacion,
      telefonoContacto,
      comentario,
      notificar: notificar.value,
    });
    setEnviando(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    onListo(r.data.candidato, r.data.resultados, r.data.advertencias);
  }

  const inputCls = "h-11 rounded-xl border border-border-soft bg-surface px-3.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20";

  /** 2026-09-18: nombre del entrevistador según lo capturado (interno del perfil, contacto del Cliente u «Otro»). */
  const nombreEntrevistadorActual =
    tipoEntrevistador === "interno"
      ? entrevistadores.find((e) => e.id === entrevistadorUsuarioId)?.nombre ?? ""
      : typeof contactoSel === "number"
        ? contactos?.find((k) => k.id === contactoSel)?.nombreCompleto ?? ""
        : entrevistadorNombre;
  /** Vista previa con el contexto REAL del candidato y del formulario; cambia en vivo con los inputs. */
  const datosPreview = {
    evento: "agendada" as const,
    candidato: c.nombre,
    entrevistador: nombreEntrevistadorActual,
    vacante: c.puesto || c.vacanteTitulo || "",
    empresa: c.empresaVisible || "",
    fecha,
    hora,
    modalidad,
    liga: modalidad === "Videollamada" ? (porTeams ? "" : liga) : "",
    ubicacion: modalidad === "Presencial" ? ubicacion : "",
    telefono: modalidad === "Llamada" ? telefonoContacto : "",
    telefonoCandidato: c.telefono,
    comentario,
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-0 backdrop-blur-sm sm:p-4">
      {/* 2026-09-18: más ancho, cuerpo con scroll propio y footer fijo — los botones nunca se pierden (web y móvil) */}
      <Card className="flex h-[100dvh] w-full flex-col overflow-hidden rounded-none p-0 sm:h-auto sm:max-h-[85vh] sm:max-w-2xl sm:rounded-2xl">
        <div className="shrink-0 border-b border-border-faint px-5 pt-5 pb-3">
          <h3 className="font-display text-lg font-bold">Programar entrevista humana</h3>
          <p className="mt-1 text-[13px] leading-relaxed text-ink-2">
            Con {c.nombre.split(" ")[0]}. Al guardar, la tarjeta se mueve a Entrevista Humana y se confirma por correo y WhatsApp.
          </p>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-4">
        <div className="mt-4 flex flex-col gap-3">
          <div>
            <span className="text-sm font-medium text-ink-2">Entrevistador</span>
            <div className="mt-1.5 grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setTipoEntrevistador("interno")}
                className={`h-10 rounded-xl border text-sm font-medium transition ${
                  tipoEntrevistador === "interno" ? "border-brand/25 bg-brand-soft text-brand" : "border-border-soft text-ink-2"
                }`}
              >
                Interno
              </button>
              <button
                type="button"
                onClick={() => setTipoEntrevistador("externo")}
                className={`h-10 rounded-xl border text-sm font-medium transition ${
                  tipoEntrevistador === "externo" ? "border-brand/25 bg-brand-soft text-brand" : "border-border-soft text-ink-2"
                }`}
              >
                Externo
              </button>
            </div>
          </div>

          {tipoEntrevistador === "interno" ? (
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Entrevistador interno</span>
              <select value={entrevistadorUsuarioId ?? ""} onChange={(e) => setEntrevistadorUsuarioId(Number(e.target.value))} className={inputCls}>
                {entrevistadores.length === 0 && <option value="">Sin entrevistadores activos</option>}
                {entrevistadores.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.nombre}
                  </option>
                ))}
              </select>
              {/* Fase 7A: correo y WhatsApp vienen del perfil (Configuración → Usuarios); nunca se capturan aquí */}
              {internoSel && (
                <span className="text-[12px] leading-relaxed text-ink-3">
                  Se notificará a {internoSel.correo}
                  {internoSel.telefono ? ` · WhatsApp ${internoSel.telefono}` : " · sin WhatsApp en su perfil (agrégalo en Configuración → Usuarios)"}
                </span>
              )}
            </label>
          ) : (
            <div className="flex flex-col gap-3">
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-ink-2">Entrevistador externo</span>
                <select
                  value={contactoSel}
                  onChange={(e) => setContactoSel(e.target.value === OTRO ? OTRO : e.target.value === "" ? "" : Number(e.target.value))}
                  className={inputCls}
                >
                  {contactos === null ? (
                    <option value="">Cargando contactos del Cliente…</option>
                  ) : (
                    <>
                      {contactos.length > 0 && <option value="">Elige un contacto{c.clienteVacante ? ` de ${c.clienteVacante}` : ""}…</option>}
                      {contactos.map((k) => (
                        <option key={k.id} value={k.id}>
                          {k.nombre} {k.apellidos ?? ""}
                          {k.puesto ? ` — ${k.puesto}` : ""}
                        </option>
                      ))}
                      <option value={OTRO}>+ Otro entrevistador</option>
                    </>
                  )}
                </select>
                {contactoElegido && (
                  <span className="text-[12px] leading-relaxed text-ink-3">
                    Se notificará a {contactoElegido.correo || "(sin correo registrado)"}
                    {contactoElegido.telefono ? ` · WhatsApp ${contactoElegido.telefono}` : ""}
                  </span>
                )}
                {contactos !== null && contactos.length === 0 && (
                  <span className="text-[12px] leading-relaxed text-ink-3">
                    {clienteId ? "El Cliente de la vacante no tiene contactos registrados." : "La vacante no tiene Cliente asociado."} Captura al entrevistador aquí.
                  </span>
                )}
              </label>
              {esOtro && (
                <div className="grid grid-cols-2 gap-3">
                  <label className="flex flex-col gap-1.5">
                    <span className="text-sm font-medium text-ink-2">Nombre</span>
                    <input value={entrevistadorNombre} onChange={(e) => setEntrevistadorNombre(e.target.value)} placeholder="Nombre completo" className={inputCls} />
                  </label>
                  <label className="flex flex-col gap-1.5">
                    <span className="text-sm font-medium text-ink-2">Correo</span>
                    <input type="email" value={entrevistadorCorreo} onChange={(e) => setEntrevistadorCorreo(e.target.value)} placeholder="correo@empresa.com" className={inputCls} />
                  </label>
                  <label className="col-span-2 flex flex-col gap-1.5">
                    <span className="text-sm font-medium text-ink-2">WhatsApp (opcional)</span>
                    <input value={entrevistadorWhatsapp} onChange={(e) => setEntrevistadorWhatsapp(e.target.value)} placeholder="10 dígitos" className={inputCls} />
                  </label>
                </div>
              )}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Fecha</span>
              <input type="date" value={fecha} onChange={(e) => setFecha(e.target.value)} className={inputCls} />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Hora <span className="font-normal text-ink-3">({ETIQUETA_ZONA})</span></span>
              <input type="time" value={hora} onChange={(e) => setHora(e.target.value)} className={inputCls} />
            </label>
          </div>

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-2">Modalidad</span>
            <select value={modalidad} onChange={(e) => setModalidad(e.target.value as ModalidadEntrevistaHumana)} className={inputCls}>
              {MODALIDADES_ENTREVISTA_HUMANA.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>

          {modalidad === "Videollamada" && porTeams && (
            <div className="rounded-xl border border-brand/25 bg-brand-soft/40 px-3.5 py-2.5 text-[13px] leading-relaxed text-ink-2">
              <span className="font-semibold text-ink">Reunión de Microsoft Teams automática.</span> Al programar se crea la reunión, la liga va en el
              correo y WhatsApp de confirmación y se manda la invitación de calendario a candidato y entrevistador.
              <button type="button" onClick={() => setOtraLiga(true)} className="ml-1.5 font-medium text-brand hover:underline">
                Usar otra liga
              </button>
            </div>
          )}
          {modalidad === "Videollamada" && !porTeams && (
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Liga de la videollamada</span>
              <input value={liga} onChange={(e) => setLiga(e.target.value)} placeholder="https://meet.google.com/…" className={inputCls} />
              {teamsConectado && otraLiga && (
                <button type="button" onClick={() => setOtraLiga(false)} className="self-start text-[12px] font-medium text-brand hover:underline">
                  ← Volver a usar Teams
                </button>
              )}
            </label>
          )}
          {modalidad === "Presencial" && (
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Ubicación / instrucciones</span>
              <input value={ubicacion} onChange={(e) => setUbicacion(e.target.value)} placeholder="Dirección o cómo llegar" className={inputCls} />
            </label>
          )}
          {modalidad === "Llamada" && (
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Teléfono de contacto (opcional)</span>
              <input
                value={telefonoContacto}
                onChange={(e) => setTelefonoContacto(e.target.value)}
                placeholder={c.telefono || "Si lo dejas vacío, se usa el teléfono del candidato"}
                className={inputCls}
              />
            </label>
          )}

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-2">Comentario (opcional)</span>
            <textarea
              value={comentario}
              onChange={(e) => setComentario(e.target.value)}
              rows={2}
              className="rounded-xl border border-border-soft bg-surface px-3.5 py-2.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
            />
          </label>
        </div>

        {error && (
          <div className="mt-3">
            <Aviso tono="error">{error}</Aviso>
          </div>
        )}

        <LineaNotificar className="mt-4" value={notificar.value} onChange={notificar.setValue} hayCliente={Boolean(clienteId)} clienteId={clienteId} />
        </div>

        {/* Footer sticky: siempre visible */}
        <div className="shrink-0 border-t border-border-soft bg-surface px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" onClick={() => setPreview("entrevistador")} disabled={enviando} title="Previsualiza el HTML exacto que recibirán el entrevistador y el candidato">
              <Mail className="h-4 w-4" /> Ver cuerpo del correo
            </Button>
            <div className="ml-auto flex gap-2">
              <Button variant="outline" onClick={onClose} disabled={enviando}>
                Cancelar
              </Button>
              <Button onClick={programar} disabled={enviando}>
                {enviando ? "Programando…" : "Programar entrevista"}
              </Button>
            </div>
          </div>
        </div>
      </Card>

      {preview && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-0 backdrop-blur-sm sm:p-4" onClick={() => setPreview(null)}>
          <Card className="flex h-[100dvh] w-full flex-col overflow-hidden rounded-none p-0 sm:h-[90vh] sm:max-w-3xl sm:rounded-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border-soft px-4 py-3">
              <div className="scroll-x gap-1 rounded-xl border border-border-soft bg-surface-2/60 p-1">
                {(["entrevistador", "candidato"] as const).map((k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setPreview(k)}
                    className={cn("rounded-lg px-3 py-1.5 text-xs font-semibold transition", preview === k ? "bg-brand text-white" : "text-ink-2 hover:text-ink")}
                  >
                    {k === "entrevistador" ? "Correo al entrevistador" : "Correo al candidato"}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-2">
                <span className="hidden text-[11px] text-ink-3 sm:inline">
                  Correo real para {c.nombre.split(" ")[0]} · {modalidad}{fecha ? ` · ${fecha}${hora ? ` ${hora}` : ""}` : " · sin fecha aún"}
                </span>
                <a href={urlPreviewCorreo(preview, datosPreview)} target="_blank" rel="noreferrer" className="text-xs font-semibold text-brand hover:underline">Abrir en pestaña</a>
                <button onClick={() => setPreview(null)} className="grid h-8 w-8 place-items-center rounded-lg text-ink-3 hover:bg-surface-2" aria-label="Cerrar"><X className="h-4 w-4" /></button>
              </div>
            </div>
            {/* key = URL: al cambiar fecha/hora/modalidad/liga en el formulario el iframe se vuelve a cargar con los datos precisos */}
            <iframe key={urlPreviewCorreo(preview, datosPreview)} title={`Vista previa · ${preview}`} src={urlPreviewCorreo(preview, datosPreview)} className="min-h-0 w-full flex-1 bg-white" />
          </Card>
        </div>
      )}
    </div>
  );
}


/* ============================================================
   Modal "Modificar" — Fase D, evento "entrevista_modificada"
   ============================================================ */
function ModalModificarEntrevista({
  c,
  eh,
  onClose,
  onListo,
}: {
  c: Candidato;
  eh: EntrevistaHumana;
  onClose: () => void;
  onListo: (c: Candidato) => void;
}) {
  // 2026-09-29: se precarga con la hora de la ORGANIZACIÓN (antes tomaba la hora UTC como local y cada
  // «Modificar» sumaba 6 h a la cita).
  const inicial = partesLocales(eh.fecha);
  const [fecha, setFecha] = useState(inicial.fecha);
  const [hora, setHora] = useState(inicial.hora);
  const [modalidad, setModalidad] = useState<ModalidadEntrevistaHumana>((eh.modalidad || "Videollamada") as ModalidadEntrevistaHumana);
  const [liga, setLiga] = useState(eh.liga || "");
  const [ubicacion, setUbicacion] = useState(eh.ubicacion || "");
  const [telefonoContacto, setTelefonoContacto] = useState(eh.telefonoContacto || "");
  const [comentario, setComentario] = useState(eh.comentario || "");
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);
  const notificar = useNotificarAccion("entrevista_modificada");

  async function guardar() {
    if (!fecha || !hora) {
      setError("Completa fecha y hora.");
      return;
    }
    if (modalidad === "Videollamada" && !eh.porTeams && !liga.trim()) {
      setError("Falta la liga de la videollamada.");
      return;
    }
    if (modalidad === "Presencial" && !ubicacion.trim()) {
      setError("Falta la ubicación de la entrevista.");
      return;
    }
    setEnviando(true);
    setError("");
    const r = await modificarEntrevistaHumana(c.id, { fecha, hora, modalidad, liga, ubicacion, telefonoContacto, comentario, notificar: notificar.value });
    setEnviando(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    onListo(r.data);
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <Card className="w-full max-w-md p-5">
        <h3 className="font-display text-lg font-bold">Modificar entrevista</h3>
        <p className="mt-1 text-[13px] leading-relaxed text-ink-2">
          Con {c.nombre.split(" ")[0]}. Abajo puedes ajustar a quién se avisa solo por esta vez.
        </p>

        <div className="mt-4 flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Fecha</span>
              <input
                type="date"
                value={fecha}
                onChange={(e) => setFecha(e.target.value)}
                className="h-11 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Hora <span className="font-normal text-ink-3">({ETIQUETA_ZONA})</span></span>
              <input
                type="time"
                value={hora}
                onChange={(e) => setHora(e.target.value)}
                className="h-11 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
              />
            </label>
          </div>

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-2">Modalidad</span>
            <select
              value={modalidad}
              onChange={(e) => setModalidad(e.target.value as ModalidadEntrevistaHumana)}
              className="h-11 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
            >
              {MODALIDADES_ENTREVISTA_HUMANA.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>

          {modalidad === "Videollamada" && eh.porTeams && (
            <p className="rounded-xl border border-brand/25 bg-brand-soft/40 px-3.5 py-2.5 text-[13px] leading-relaxed text-ink-2">
              <span className="font-semibold text-ink">Reunión de Microsoft Teams.</span> La liga se conserva y la reunión de calendario se actualiza con la nueva fecha y hora.
            </p>
          )}
          {modalidad === "Videollamada" && !eh.porTeams && (
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Liga de la videollamada</span>
              <input
                value={liga}
                onChange={(e) => setLiga(e.target.value)}
                placeholder="https://meet.google.com/…"
                className="h-11 rounded-xl border border-border-soft bg-surface px-3.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
              />
            </label>
          )}
          {modalidad === "Presencial" && (
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Ubicación / instrucciones</span>
              <input
                value={ubicacion}
                onChange={(e) => setUbicacion(e.target.value)}
                placeholder="Dirección o cómo llegar"
                className="h-11 rounded-xl border border-border-soft bg-surface px-3.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
              />
            </label>
          )}
          {modalidad === "Llamada" && (
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Teléfono de contacto (opcional)</span>
              <input
                value={telefonoContacto}
                onChange={(e) => setTelefonoContacto(e.target.value)}
                placeholder={c.telefono || "Si lo dejas vacío, se usa el teléfono del candidato"}
                className="h-11 rounded-xl border border-border-soft bg-surface px-3.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
              />
            </label>
          )}

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-2">Comentario (opcional)</span>
            <textarea
              value={comentario}
              onChange={(e) => setComentario(e.target.value)}
              rows={2}
              className="rounded-xl border border-border-soft bg-surface px-3.5 py-2.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
            />
          </label>
        </div>

        {error && (
          <div className="mt-3">
            <Aviso tono="error">{error}</Aviso>
          </div>
        )}

        <LineaNotificar className="mt-4" value={notificar.value} onChange={notificar.setValue} hayCliente={Boolean(c.clienteVacante)} clienteId={c.clienteIdVacante ?? null} />

        <div className="mt-5 flex gap-3">
          <Button variant="outline" className="flex-1" onClick={onClose} disabled={enviando}>
            Cerrar
          </Button>
          <Button className="flex-1" onClick={guardar} disabled={enviando}>
            {enviando ? "Guardando…" : "Guardar cambios"}
          </Button>
        </div>
      </Card>
    </div>
  );
}

/* ============================================================
   Etapa: Contratación — condiciones finales + expediente (6 documentos)
   ============================================================ */
function CampoTexto({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-sm font-medium text-ink-2">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-10 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
      />
    </label>
  );
}

function CampoSelect({
  label,
  value,
  onChange,
  opciones,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  opciones: string[];
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-sm font-medium text-ink-2">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-10 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
      >
        <option value="">Selecciona…</option>
        {opciones.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Fila de documento: Pendiente -> Subir documento -> Cargado -> Ver. */
function FilaDocumentoSimple({
  d,
  expedienteId,
  live,
  onActualizado,
}: {
  d: DocExpediente;
  expedienteId?: number;
  live: boolean;
  onActualizado: (e: NuevoIngreso) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [subiendo, setSubiendo] = useState(false);
  const cargado = Boolean(d.tieneArchivo);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !expedienteId) return;
    setSubiendo(true);
    const r = await subirDocumento(expedienteId, d.nombre, file);
    setSubiendo(false);
    if (r.ok) onActualizado(r.data.expediente);
  }

  return (
    <div className="flex items-center justify-between gap-2 rounded-xl border border-border-soft bg-surface px-3.5 py-2.5">
      <span className="min-w-0 truncate text-sm">{d.nombre}</span>
      <div className="flex shrink-0 items-center gap-2">
        {d.estadoOnboarding ? (
          <Badge tone={d.estadoOnboarding === "Aprobado" ? "good" : d.estadoOnboarding === "Rechazado" ? "bad" : d.estadoOnboarding === "Por revisar" ? "warn" : "neutral"}>
            {d.estadoOnboarding}
          </Badge>
        ) : (
          <Badge tone={cargado ? "good" : "neutral"}>{cargado ? "Cargado" : "Pendiente"}</Badge>
        )}
        {cargado && expedienteId ? (
          <a
            href={urlDocumento(expedienteId, d.nombre)}
            target="_blank"
            rel="noreferrer"
            className="text-xs font-semibold text-brand hover:underline"
          >
            Ver
          </a>
        ) : live && expedienteId ? (
          <>
            <input ref={inputRef} type="file" accept="image/*,application/pdf" className="hidden" onChange={onFile} />
            <button
              onClick={() => inputRef.current?.click()}
              disabled={subiendo}
              className="text-xs font-semibold text-brand hover:underline disabled:opacity-50"
            >
              {subiendo ? "Subiendo…" : "Subir documento"}
            </button>
          </>
        ) : null}
      </div>
    </div>
  );
}

function PanelContratacion({
  c,
  live,
  onCambio,
  setAviso,
  onDocumentos,
  onDescartar,
}: {
  c: Candidato;
  live: boolean;
  onCambio: (c: Candidato) => void;
  setAviso: (a: AvisoEstado) => void;
  /** 2026-09-15: abre la confirmación «Solicitar documentos» / «Enviar recordatorio» del modal
   * (plantilla de WhatsApp; el candidato responde mandando el archivo por el mismo chat). */
  onDocumentos?: (que: "solicitar" | "recordatorio") => void;
  /** 2026-09-17: «Descartar candidato…» también desde Contratación/Onboarding (menú «…»). */
  onDescartar?: () => void;
}) {
  const modoPrueba = useModoPrueba();
  const cond = c.expedienteCondiciones;
  const [puesto, setPuesto] = useState(cond?.puesto ?? c.puesto ?? "");
  const [sueldo, setSueldo] = useState(cond?.sueldo ?? "");
  const [tipo, setTipo] = useState(cond?.tipoContratacion ?? "");
  const [fechaIngreso, setFechaIngreso] = useState(cond?.fechaIngreso ? cond.fechaIngreso.slice(0, 10) : "");
  const [ubicacion, setUbicacion] = useState(cond?.ubicacion ?? "");
  const [jefe, setJefe] = useState(cond?.jefeDirecto ?? "");
  const [instrucciones, setInstrucciones] = useState(cond?.instruccionesIngreso ?? "");
  const [empresa, setEmpresa] = useState(cond?.empresa ?? "");
  // B2: Select de razones sociales de la Cuenta (predeterminada = la de la Cuenta); nunca texto libre
  const [razones, setRazones] = useState<RazonSocial[]>([]);
  const [duracion, setDuracion] = useState<string>(cond?.duracionContrato ? String(cond.duracionContrato) : "");
  const [unidad, setUnidad] = useState<string>(cond?.duracionUnidad || "meses");
  const esDeterminado = tipo === "Tiempo determinado";
  const fechaTerminoPreview = esDeterminado ? fechaTerminoLocal(fechaIngreso, Number(duracion), unidad) : "";
  useEffect(() => {
    let vivo = true;
    fetchRazonesSociales().then((lista) => {
      if (!vivo || !lista) return;
      setRazones(lista);
      // precarga la razón social de la Cuenta si el expediente aún no tiene una válida
      setEmpresa((actual) => (actual && lista.some((x) => x.razonSocial === actual) ? actual : lista.find((x) => x.predeterminada)?.razonSocial ?? lista[0]?.razonSocial ?? ""));
    });
    return () => {
      vivo = false;
    };
  }, []);
  const [guardando, setGuardando] = useState(false);
  // 2026-09-19 (Bloque 3): vista previa en la misma pantalla de carta / contrato con 3 acciones
  const [docPreview, setDocPreview] = useState<null | "carta" | "contrato">(null);
  // 2026-09-29: firma electrónica incrustada (Dropbox Sign). Sin llaves en el servidor → vista previa del PDF como antes.
  const [firmaCfg, setFirmaCfg] = useState<{ configurado: boolean; clientId: string | null; testMode: boolean } | null>(null);
  const [firmas, setFirmas] = useState<FirmaDocumento[]>([]);
  const [firmando, setFirmando] = useState<"" | "carta" | "contrato">("");
  const [enviandoDoc, setEnviandoDoc] = useState<"" | "whatsapp" | "correo">("");
  const [resultadoDoc, setResultadoDoc] = useState<{ ok: boolean; texto: string } | null>(null);
  const condicionesListas = Boolean(cond?.completas);
  // Onboarding v2 (Fase 2): «Enviar a Onboarding» exige condiciones + consentimiento de privacidad (salvo Modo Prueba)
  const requisitosOnboarding = condicionesListas && Boolean(c.consentimiento);
  const [iniciarAbierto, setIniciarAbierto] = useState(false);
  const documentosListos = (c.expedienteProgreso ?? 0) >= 100;
  const [expediente, setExpediente] = useState<NuevoIngreso | null>(null);
  const [cancelando, setCancelando] = useState(false);
  const [motivoCancelar, setMotivoCancelar] = useState("");
  const [ocupado, setOcupado] = useState("");

  const firmaRef = useRef("");
  const cargarExpediente = useCallback(async () => {
    if (!c.expedienteId) return;
    const e = await fetchExpediente(c.expedienteId);
    if (!e) return;
    setExpediente(e);
    // 2026-09-18 (tiempo real): si cambió algún documento (el candidato subió desde su liga/WhatsApp),
    // se refresca también la ficha (progreso, alta, avisos) sin recargar la página.
    const firma = JSON.stringify((e.documentos ?? []).map((d) => [d.nombre, d.estado]));
    if (firmaRef.current && firma !== firmaRef.current) {
      const ficha = await fetchCandidato(c.id);
      if (ficha) onCambio(ficha);
    }
    firmaRef.current = firma;
  }, [c.expedienteId, c.id, onCambio]);

  useEffect(() => {
    void cargarExpediente();
  }, [cargarExpediente]);
  usePolling(cargarExpediente, 20000);

  // 2026-09-29 (red de seguridad): postulación en Contratación/Onboarding sin expediente (p. ej. carga masiva) →
  // se abre en ese momento para ESA postulación (el backend exige consentimiento y es idempotente).
  const asegurando = useRef(false);
  useEffect(() => {
    if (!live || c.expedienteId != null || asegurando.current) return;
    if (c.etapa !== "Contratación" && c.etapa !== "Onboarding") return;
    asegurando.current = true;
    asegurarExpediente(c.id).then((r) => {
      if (r.ok) onCambio(r.data);
      else setAviso({ tono: "error", texto: r.error });
    });
  }, [live, c.expedienteId, c.etapa, c.id, onCambio, setAviso]);

  const cargarFirmas = useCallback(async () => {
    if (c.expedienteId == null) return;
    setFirmas((await fetchFirmasExpediente(c.expedienteId)) ?? []);
  }, [c.expedienteId]);
  useEffect(() => {
    fetchEstadoFirmas().then((x) => setFirmaCfg(x ?? { configurado: false, clientId: null, testMode: false }));
    void cargarFirmas();
  }, [cargarFirmas]);

  /** «Generar carta de intención» / «Generar contrato»: con Dropbox Sign configurado crea la solicitud y abre el modal
   * de firma incrustado (RH firma aquí; el candidato, en su liga de expediente). Sin él, vista previa del PDF. */
  async function firmarOVer(doc: "carta" | "contrato") {
    setResultadoDoc(null);
    if (!firmaCfg?.configurado || !firmaCfg.clientId || c.expedienteId == null) return setDocPreview(doc);
    setFirmando(doc);
    const r = await crearFirmaDocumento(c.expedienteId, doc);
    setFirmando("");
    if (!r.ok) {
      setAviso({ tono: "error", texto: r.error });
      return;
    }
    void cargarFirmas();
    if (!r.data.signUrl) {
      setAviso({ tono: "ok", texto: `${r.data.documentoTexto}: tu firma ya está registrada; falta la del candidato (la hace desde su liga de expediente).` });
      return;
    }
    await abrirFirmaEmbebida({
      clientId: firmaCfg.clientId,
      signUrl: r.data.signUrl,
      testMode: firmaCfg.testMode,
      onFirmado: () => {
        setAviso({ tono: "ok", texto: `${r.data.documentoTexto} firmada por ti. El candidato la firma desde su liga de expediente; el PDF final se guarda solo en el expediente.` });
        setTimeout(() => void cargarFirmas(), 1500);
      },
      onError: (m) => setAviso({ tono: "error", texto: `Firma electrónica: ${m}` }),
    });
  }

  async function guardar() {
    if (esDeterminado && (!duracion || Number(duracion) <= 0)) return setAviso({ tono: "error", texto: "Tiempo determinado: captura la duración del contrato (número mayor a cero)." });
    setGuardando(true);
    const r = await guardarCondicionesContratacion(c.id, {
      puesto,
      sueldo,
      tipoContratacion: tipo,
      fechaIngreso: fechaIngreso || undefined,
      ubicacion,
      jefeDirecto: jefe,
      instruccionesIngreso: instrucciones,
      empresa,
      duracionContrato: esDeterminado ? Number(duracion) : null,
      duracionUnidad: esDeterminado ? unidad : "",
    });
    setGuardando(false);
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setAviso({ tono: "ok", texto: "Condiciones guardadas. Ya puedes generar la carta, solicitar documentos y preparar el contrato." });
    onCambio(r.data);
  }

  /** WhatsApp / Correo de la carta: trazabilidad en bitácora, aviso mínimo en pantalla. */
  async function enviarCarta(canal: "whatsapp" | "correo") {
    if (!c.expedienteId) return;
    setEnviandoDoc(canal);
    setResultadoDoc(null);
    const r = await enviarCartaIntencion(c.expedienteId, canal);
    setEnviandoDoc("");
    if (!r.ok) return setResultadoDoc({ ok: false, texto: r.error });
    setResultadoDoc({ ok: r.data.enviado, texto: r.data.enviado ? `Carta enviada por ${canal === "whatsapp" ? "WhatsApp" : "correo"}.` : `No salió por ${canal}: ${r.data.detalle}` });
  }

  /** Onboarding v2 (Fase 2): abre el resumen; «Iniciar Onboarding» es el único gatillo del cambio de etapa. */
  function enviarOnboarding() {
    setIniciarAbierto(true);
  }

  async function confirmarCancelacion() {
    if (!c.expedienteId || !motivoCancelar.trim()) return;
    setOcupado("cancelar");
    const r = await cancelarExpediente(c.expedienteId, motivoCancelar.trim());
    if (!r.ok) {
      setOcupado("");
      return setAviso({ tono: "error", texto: r.error });
    }
    const actualizado = await fetchCandidato(c.id);
    setOcupado("");
    setCancelando(false);
    if (actualizado) onCambio(actualizado);
    setAviso({ tono: "ok", texto: "Contratación cancelada; el candidato regresó a Entrevista Humana." });
  }

  return (
    <Card className="border-warn/25 bg-warn-soft/10 p-4">
      <Eyebrow>Condiciones de contratación</Eyebrow>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <CampoTexto label="Puesto" value={puesto} onChange={setPuesto} />
        <CampoTexto label="Sueldo" value={sueldo} onChange={setSueldo} placeholder="$14,000 mensuales" />
        <CampoSelect label="Tipo de contratación" value={tipo} onChange={setTipo} opciones={TIPOS_CONTRATACION} />
        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-ink-2">Fecha de ingreso</span>
          <input
            type="date"
            value={fechaIngreso}
            onChange={(e) => setFechaIngreso(e.target.value)}
            className="h-10 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
          />
        </label>
        {esDeterminado && (
          <>
            {/* B2: duración (número + unidad) → fecha de término calculada, nunca capturada */}
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Duración del contrato</span>
              <div className="flex gap-2">
                <input
                  type="number"
                  min={1}
                  value={duracion}
                  onChange={(e) => setDuracion(e.target.value)}
                  placeholder="3"
                  className="h-10 w-24 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
                />
                <select
                  value={unidad}
                  onChange={(e) => setUnidad(e.target.value)}
                  className="h-10 flex-1 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
                >
                  {UNIDADES_DURACION.map((u) => (
                    <option key={u} value={u}>{u}</option>
                  ))}
                </select>
              </div>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Fecha de término</span>
              <input
                type="date"
                value={fechaTerminoPreview || (cond?.fechaTermino ? cond.fechaTermino.slice(0, 10) : "")}
                readOnly
                disabled
                title="Se calcula automáticamente: fecha de ingreso + duración"
                className="h-10 rounded-xl border border-border-soft bg-surface-2 px-3 text-sm text-ink-2 outline-none"
              />
              <span className="text-[11px] text-ink-3">Calculada: fecha de ingreso + duración.</span>
            </label>
          </>
        )}
        <CampoTexto label="Ubicación" value={ubicacion} onChange={setUbicacion} />
        <CampoTexto label="Jefe directo" value={jefe} onChange={setJefe} />
        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-ink-2">Empresa contratante</span>
          <select
            value={empresa}
            onChange={(e) => setEmpresa(e.target.value)}
            className="h-10 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
          >
            {razones.length === 0 && <option value={empresa}>{empresa || "Cargando razones sociales…"}</option>}
            {razones.map((r) => (
              <option key={`${r.origen}-${r.clienteId ?? 0}`} value={r.razonSocial}>
                {r.razonSocial}{r.origen === "cuenta" ? " (Cuenta)" : " (Cliente)"}
              </option>
            ))}
          </select>
          <span className="text-[11px] text-ink-3">Solo razones sociales configuradas en la Cuenta (Configuración → Cuenta / Clientes).</span>
        </label>
        {/* Fase 5: se mandan por WhatsApp/correo automáticamente al dar de alta (evento instrucciones_ingreso) */}
        <label className="flex flex-col gap-1.5 sm:col-span-2">
          <span className="text-xs font-medium text-ink-2">Instrucciones de ingreso (primer día)</span>
          <textarea
            value={instrucciones}
            onChange={(e) => setInstrucciones(e.target.value)}
            rows={3}
            placeholder="Ej. Preséntate el lunes a las 9:00 en recepción con INE y comprobante de domicilio; pregunta por Laura de RH."
            className="rounded-xl border border-border-soft bg-surface px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
          />
          <span className="text-[11px] text-ink-3">Al dar de alta, el colaborador recibe automáticamente su bienvenida con estos datos por WhatsApp y correo.</span>
        </label>
      </div>
      {live && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button size="sm" onClick={guardar} disabled={guardando}>
            {guardando ? "Guardando…" : condicionesListas ? "Guardar cambios" : "Guardar condiciones"}
          </Button>
          <span className="text-[12px] text-ink-3">
            {condicionesListas ? `Condiciones guardadas${cond?.guardadasEn ? ` el ${textoFecha(cond.guardadasEn)}` : ""}.` : "Captura puesto, sueldo, tipo y fecha de ingreso y guarda para habilitar los documentos."}
          </span>
        </div>
      )}

      {/* 2026-09-19 (Bloque 3): flujo lineal — con condiciones guardadas aparecen aquí mismo las acciones */}
      {live && condicionesListas && c.expedienteId != null && (
        <div className="mt-4 flex flex-wrap items-center gap-2 rounded-xl border border-border-soft bg-surface p-3">
          <Button size="sm" variant="outline" onClick={() => void firmarOVer("carta")} disabled={firmando !== ""}
            title={firmaCfg?.configurado ? "Se firma aquí mismo (firma electrónica); el candidato firma desde su liga" : "Vista previa del PDF"}>
            <FileText className="h-4 w-4" /> {firmando === "carta" ? "Preparando firma…" : "Generar carta de intención"}
          </Button>
          {/* Onboarding v2: «Solicitar documentos» ya no vive en Contratación — la primera solicitud la hace «Iniciar Onboarding» */}
          {onDocumentos && c.etapa === "Onboarding" && (
            <Button size="sm" variant="outline" onClick={() => onDocumentos("solicitar")} disabled={Boolean(ocupado)}>
              <Send className="h-4 w-4" /> Solicitar documentos
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={() => void firmarOVer("contrato")}
            disabled={(!documentosListos && !modoPrueba) || firmando !== ""}
            title={documentosListos || modoPrueba ? "Borrador del contrato con las condiciones finales (el firmado se carga en el Onboarding)" : `Se habilita cuando el expediente tenga el 100 % de documentos Aprobados (hoy ${c.expedienteProgreso ?? 0} %)`}
          >
            <FileCheck2 className="h-4 w-4" /> {firmando === "contrato" ? "Preparando firma…" : firmaCfg?.configurado ? "Generar contrato" : "Generar contrato (borrador)"}
          </Button>
          {firmaCfg?.configurado && (
            <MenuAcciones
              acciones={[
                { etiqueta: "Ver PDF de la carta (enviar por WhatsApp / correo)", icono: <FileText className="h-4 w-4" />, onClick: () => { setResultadoDoc(null); setDocPreview("carta"); } },
                { etiqueta: "Ver PDF del contrato", icono: <FileCheck2 className="h-4 w-4" />, onClick: () => { setResultadoDoc(null); setDocPreview("contrato"); }, disabled: !documentosListos && !modoPrueba },
              ]}
            />
          )}
          {c.etapa === "Contratación" && (
            <Button
              size="sm"
              className="ml-auto"
              onClick={() => enviarOnboarding()}
              disabled={Boolean(ocupado) || (!requisitosOnboarding && !modoPrueba)}
              title={
                requisitosOnboarding || modoPrueba
                  ? "Revisa el resumen e inicia el Onboarding (única forma de pasar a Onboarding)"
                  : `Falta: ${[!condicionesListas && "condiciones (puesto, sueldo, tipo y fecha de ingreso)", !c.consentimiento && "consentimiento de privacidad"].filter(Boolean).join(" y ")}`
              }
            >
              Enviar a Onboarding
            </Button>
          )}
        </div>
      )}

      {firmas.length > 0 && (
        <div className="mt-3 rounded-xl border border-border-soft bg-surface p-3">
          <p className="flex items-center gap-1.5 text-[12px] font-semibold text-ink-2"><IconoFirma className="h-3.5 w-3.5" /> Firma electrónica</p>
          <ul className="mt-2 space-y-1.5 text-[12px]">
            {firmas.map((f) => (
              <li key={f.id} className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-ink-2">
                  {f.documentoTexto}{f.testMode ? " (prueba)" : ""} · {f.firmantes.map((x) => `${x.rol === "rh" ? "RH" : "Candidato"}: ${x.estado === "firmado" ? "firmó" : "pendiente"}`).join(" · ")}
                </span>
                <Badge tone={f.estado === "descargada" ? "good" : f.estado === "cancelada" ? "neutral" : f.estado === "firmada" ? "brand" : "warn"}>
                  {f.estado === "descargada" ? "Firmada · PDF en el expediente" : f.estado === "firmada" ? "Firmada · descargando PDF" : f.estado === "cancelada" ? "Cancelada" : "En firma"}
                </Badge>
              </li>
            ))}
          </ul>
          {firmas.some((f) => f.estado === "enviada" && f.firmantes.some((x) => x.rol === "candidato" && x.estado !== "firmado")) && (
            <p className="mt-2 text-[11px] text-ink-3">El candidato firma desde su liga de expediente (compártela con «Ver PDF de la carta» → WhatsApp o correo).</p>
          )}
        </div>
      )}

      {live && c.etapa === "Contratación" && !requisitosOnboarding && (
        <p className="mt-3 text-[12px] text-ink-3">
          Para enviar a Onboarding: {[!condicionesListas && "guarda puesto, sueldo, tipo y fecha de ingreso", !c.consentimiento && "registra el consentimiento de privacidad (LFPDPPP)"].filter(Boolean).join(" y ")}.
          {modoPrueba ? " (Modo Prueba activo: puedes enviarlo de todos modos.)" : ""}
        </p>
      )}

      {c.etapa === "Onboarding" && c.expedienteId != null && (
        <div className="mt-5 border-t border-border-faint pt-4">
          <Eyebrow>Tareas de Onboarding</Eyebrow>
          <div className="mt-3">
            <PanelTareasOnboarding expedienteId={c.expedienteId} live={live} onCambio={() => void cargarExpediente()} />
          </div>
        </div>
      )}

      <div className="mt-5 border-t border-border-faint pt-4">
        <div className="flex items-center justify-between gap-3">
          <Eyebrow>Expediente · {c.expedienteProgreso ?? 0}% aprobado</Eyebrow>
          <div className="w-32">
            <Progress value={c.expedienteProgreso ?? 0} tone="good" />
          </div>
        </div>
        <div className="mt-3 flex flex-col gap-2">
          {(expediente?.documentos ?? []).filter((d) => !d.interno).map((d) => (
            <FilaDocumentoSimple
              key={d.nombre}
              d={d}
              expedienteId={c.expedienteId ?? undefined}
              live={live}
              onActualizado={setExpediente}
            />
          ))}
        </div>
      </div>

      {live && (
        <div className="mt-4 flex flex-wrap gap-2 border-t border-border-faint pt-4">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCancelando(true)}
            disabled={Boolean(ocupado)}
            className="border-bad/30 text-bad hover:bg-bad-soft"
          >
            Cancelar contratación
          </Button>
          {c.expedienteId != null && onDocumentos && (
            <Button size="sm" variant="outline" onClick={() => onDocumentos("recordatorio")} disabled={Boolean(ocupado)} title={etiquetaRecordatorio(c.recordatorioNivel, c.recordatoriosEnviados).tono.descripcion}>
              <RotateCw className="h-4 w-4" /> {etiquetaRecordatorio(c.recordatorioNivel, c.recordatoriosEnviados).texto}
            </Button>
          )}
          {onDescartar && (
            <MenuAcciones acciones={[{ etiqueta: "Descartar candidato…", icono: <ThumbsDown />, peligrosa: true, onClick: onDescartar, disabled: Boolean(ocupado) }]} />
          )}
        </div>
      )}

      {iniciarAbierto && c.expedienteId != null && (
        <ModalIniciarOnboarding
          expedienteId={c.expedienteId}
          onClose={() => setIniciarAbierto(false)}
          onIniciado={(r) => {
            setIniciarAbierto(false);
            onCambio(r.candidato);
            setAviso({ tono: "ok", texto: `Onboarding iniciado: ${r.tareas.length} tareas generadas.` });
          }}
        />
      )}

      {docPreview && c.expedienteId != null && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-0 backdrop-blur-sm sm:p-4" onClick={() => !enviandoDoc && setDocPreview(null)}>
          <Card className="flex h-[100dvh] w-full flex-col overflow-hidden rounded-none p-0 sm:h-[90vh] sm:max-w-3xl sm:rounded-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border-soft px-4 py-3">
              <div>
                <p className="text-sm font-semibold text-ink">{docPreview === "carta" ? "Carta de intención" : "Contrato individual de trabajo"}</p>
                <p className="text-[11px] text-ink-3">Generado con las condiciones guardadas · {puesto} · {sueldo} · {tipo}{fechaIngreso ? ` · ingreso ${fechaIngreso}` : ""}</p>
              </div>
              <button onClick={() => setDocPreview(null)} className="grid h-8 w-8 place-items-center rounded-lg text-ink-3 hover:bg-surface-2" aria-label="Cerrar"><X className="h-4 w-4" /></button>
            </div>
            <iframe title={docPreview} src={docPreview === "carta" ? urlCartaIntencionPdf(c.expedienteId) : urlContratoPdf(c.expedienteId)} className="min-h-0 w-full flex-1 bg-surface-2" />
            <div className="flex shrink-0 flex-wrap items-center gap-2 border-t border-border-soft bg-surface px-4 py-3">
              {docPreview === "carta" ? (
                <>
                  <Button size="sm" variant="outline" onClick={() => enviarCarta("whatsapp")} disabled={Boolean(enviandoDoc) || !c.telefono} title={c.telefono ? "Manda la liga de su expediente con la carta por WhatsApp" : "El candidato no tiene WhatsApp"}>
                    <MessageCircle className="h-4 w-4" /> {enviandoDoc === "whatsapp" ? "Enviando…" : "WhatsApp"}
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => enviarCarta("correo")} disabled={Boolean(enviandoDoc) || !c.correo} title={c.correo ? "Correo con el PDF adjunto" : "El candidato no tiene correo"}>
                    <Mail className="h-4 w-4" /> {enviandoDoc === "correo" ? "Enviando…" : "Correo"}
                  </Button>
                </>
              ) : null}
              <a href={docPreview === "carta" ? urlCartaIntencionPdf(c.expedienteId) : urlContratoPdf(c.expedienteId)} download className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-brand px-3 text-sm font-semibold text-white transition hover:brightness-110">
                <Download className="h-4 w-4" /> Descargar
              </a>
              {resultadoDoc && <span className={cn("text-[12px]", resultadoDoc.ok ? "text-good" : "text-bad")}>{resultadoDoc.texto}</span>}
            </div>
          </Card>
        </div>
      )}

      {cancelando && (
        <div className="mt-3 flex flex-col gap-2 rounded-xl border border-bad/30 bg-bad-soft/40 p-3">
          <input
            value={motivoCancelar}
            onChange={(e) => setMotivoCancelar(e.target.value)}
            placeholder="Motivo de la cancelación…"
            className="h-9 rounded-lg border border-border-soft bg-surface px-3 text-xs outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
          />
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setCancelando(false)} disabled={ocupado === "cancelar"}>
              Volver
            </Button>
            <Button size="sm" onClick={confirmarCancelacion} disabled={!motivoCancelar.trim() || ocupado === "cancelar"}>
              {ocupado === "cancelar" ? "Cancelando…" : "Confirmar cancelación"}
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function Info({ icon: Icon, v }: { icon: React.ComponentType<{ className?: string }>; v: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-lg border border-border-soft bg-surface px-2.5 py-1.5 text-xs text-ink-2">
      <Icon className="h-3.5 w-3.5 text-ink-3" /> {v}
    </span>
  );
}
