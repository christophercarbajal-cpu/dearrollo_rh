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
  Copy,
  Pencil,
  XCircle,
  RefreshCw,
  Trash2,
  ClipboardCheck,
  Route,
  Plus,
  Upload,
} from "lucide-react";
import { Card, Badge, Button, Avatar, Eyebrow, Progress } from "@/components/ui";
import { PageHeader, EstadoBadge, ScoreRing } from "@/components/dashboard/parts";
import { PerfilProfundoVista } from "@/components/dashboard/perfil-profundo";
import { Aviso, Dropzone, pesoLegible } from "@/components/dashboard/subida";
import {
  type Candidato,
  type EtapaCandidato,
  type Vacante,
} from "@/lib/data";
import type { DocExpediente, NuevoIngreso } from "@/lib/phase2";
import {
  autorizarAlta,
  eliminarCandidato,
  cancelarExpediente,
  decidirCandidato,
  enviarPrefiltro,
  fetchCandidato,
  fetchCandidatos,
  fetchMetricasTablero,
  type MetricasTablero,
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
  moverEtapaCandidato,
  reactivarPostulacion,
  MOTIVOS_REACTIVABLES,
  aplicarProcesoVigente,
  reanalizarCvCandidato,
  recordatorioDocumentosCandidato,
  registrarConsentimiento,
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
  type PerfilProfundo,
  nombreEtapa,
  ETAPAS_PIPELINE,
  lineasResultados,
} from "@/lib/api";
import { usePuedeDecidir, useModoPrueba, useSesion } from "@/components/sesion";
import {
  ALERTAS_TABLERO, ChipsAlerta, TableroKanban, diasEnEtapa, ordenarTablero, scoreTarjeta,
  type AlertaTablero, type OrdenTablero,
} from "@/components/dashboard/candidatos/tablero-kanban";
import { ContactoCandidato } from "@/components/dashboard/candidatos/contacto-candidato";
import { useAnunciarContextoAgente } from "@/components/dashboard/agente/proveedor";
import { ConfirmacionAccion } from "@/components/dashboard/confirmacion-accion";
import { MenuAcciones } from "@/components/dashboard/menu-acciones";
import { ModalIniciarOnboarding } from "@/components/dashboard/onboarding/iniciar-onboarding";
import { PanelTareasOnboarding } from "@/components/dashboard/onboarding/tareas-onboarding";
import { PanelEvaluaciones } from "@/components/dashboard/evaluaciones/panel-evaluaciones";
import { ModalAgregarEvaluacion, type PresetEvaluacion } from "@/components/dashboard/evaluaciones/agregar-evaluacion";
import { BadgeIntegral, PanelResultadoIntegral } from "@/components/dashboard/evaluaciones/resultado-integral";
import { ModalActividad, SeguimientoProceso } from "@/components/dashboard/procesos/seguimiento-proceso";
import { ModalMarco } from "@/components/dashboard/modulos-rh";
import type { AccionMenu } from "@/components/dashboard/menu-acciones";
import type { SeguimientoProceso as SeguimientoProcesoData } from "@/lib/data";
import { evaluadorVacio } from "@/components/dashboard/evaluaciones/campos-evaluacion";
import { PenLine as IconoFirma } from "lucide-react";
import { abrirFirmaEmbebida } from "@/lib/firma-embebida";
import {
  asegurarExpediente, fetchFirmasExpediente, fetchModoFirma, firmarDemoRH, firmarDocumentos, subirContratoFirmado, urlDocumentosPdf,
  type FirmaDocumento, type ModoFirma,
} from "@/lib/api";
import { PadFirma, type FirmaCapturada } from "@/components/firmas/pad-firma";
import { Toast, type ToastMsg } from "@/components/dashboard/toast";
import { INTERVALO_TABLERO_MS, usePolling } from "@/lib/use-polling";
import { cn, etiquetaRecordatorio } from "@/lib/utils";
import { textoFecha, textoFechaHora } from "@/lib/fechas";

/** 2026-10-01: CINCO columnas — Prefiltro → Filtro Red Human → Filtro humano → Contratación → Onboarding
 * (nombres visibles vía `nombreEtapa`). «Evaluación integral» ya no es columna: es un resultado en tarjeta y ficha. */
const etapas: EtapaCandidato[] = [...ETAPAS_PIPELINE];
const etapaColor: Record<EtapaCandidato, string> = {
  Prefiltro: "var(--ink-3)",
  "Entrevista IA": "var(--brand)",
  "Entrevista Humana": "var(--brand-2)",
  Contratación: "var(--warn)",
  Onboarding: "var(--good)",
};

/* UX 2026-10-07: la etapa la define la RUTA (compuerta + avance automático del backend). La interfaz ya no ofrece
   mover de etapa a mano; la siguiente acción concreta vive en el botón principal del Resumen. */

type FiltroEstado = "todos" | "en_proceso" | "aptos" | "contratados" | "descartados";

const FILTROS_ESTADO: { key: FiltroEstado; label: string }[] = [
  { key: "todos", label: "Todos" },
  { key: "en_proceso", label: "En proceso" },
  { key: "aptos", label: "Aptos" },
  { key: "contratados", label: "Contratados" },
  { key: "descartados", label: "No cumple" },  // recomendación del prefiltro; descartar sigue siendo decisión de RH
];

const ETAPAS_YA_CONTRATADO: EtapaCandidato[] = ["Contratación", "Onboarding"];

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
  // UX 2026-10-07: el tablero no mueve candidatos (sin arrastre); un intento solo muestra este aviso.
  const [avisoTablero, setAvisoTablero] = useState<ToastMsg>(null);
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
  // Rediseño del tablero (2026-10-07): orden por defecto = score descendente; chips de alerta que filtran el tablero
  // (segundo clic los desactiva) y métricas por columna (conversión y días promedio) de la API.
  const [orden, setOrden] = useState<OrdenTablero>("score");
  const [alerta, setAlerta] = useState<AlertaTablero | null>(null);
  const [metricas, setMetricas] = useState<MetricasTablero | null>(null);
  // Vista por defecto (2026-10-07): sin ?vacante= el tablero abre en la vacante con MÁS candidatos activos
  // (`Vacante.candidatos` = postulaciones activas, services/conteos). Se decide UNA vez al montar; si RH elige
  // «Vacante: Todas» después, se respeta. El tablero no carga hasta decidirlo (evita una carga doble).
  const [vacanteInicialLista, setVacanteInicialLista] = useState(false);
  const vacanteInicialDecidida = useRef(false);

  // Inicialización desde URL params y localStorage
  useEffect(() => {
    const vParam = searchParams.get("vacante");
    if (vParam) setFiltroVacante(vParam);
    if (!vacanteInicialDecidida.current) {
      vacanteInicialDecidida.current = true;
      if (vParam) {
        setVacanteInicialLista(true);
      } else {
        fetchVacantes()
          .then((v) => {
            if (!v) return;
            setVacantes(v);
            const mayor = v
              .filter((x) => x.estado !== "Eliminada" && x.estado !== "Cerrada" && (x.candidatos ?? 0) > 0)
              .reduce<Vacante | null>((m, x) => (!m || x.candidatos > m.candidatos ? x : m), null);
            if (mayor) setFiltroVacante(mayor.id);
          })
          .finally(() => setVacanteInicialLista(true));
      }
    }

    // 2026-10-01: una liga vieja con ?etapa=Evaluación (columna retirada) abre Filtro Red Human
    const eCrudo = searchParams.get("etapa");
    const eParam = eCrudo === "Evaluación" ? "Entrevista IA" : eCrudo;
    if (eParam && etapas.includes(eParam as EtapaCandidato)) {
      setColumnaResaltada(eParam);
      setTimeout(() => {
        const el = document.getElementById(`columna-etapa-${eParam.replace(/\s/g, "-")}`);
        // Solo desplazamiento HORIZONTAL dentro del tablero: scrollIntoView también movía la ventana y el header fijo
        // de la app tapaba el título «Candidatos».
        const contenedor = el?.closest(".overflow-x-auto");
        if (el && contenedor) {
          contenedor.scrollTo({ left: el.offsetLeft - (contenedor.clientWidth - el.clientWidth) / 2, behavior: "smooth" });
        }
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
    const [c, m] = await Promise.all([
      fetchCandidatos({
        ...(filtroVacante ? { vacante: filtroVacante } : {}),
        ...(mostrarCerradas ? { mostrar_cerradas: true } : {}),
      }),
      fetchMetricasTablero(filtroVacante || undefined),
    ]);
    if (m) setMetricas(m);
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
    if (!vacanteInicialLista) return;
    recargar();
    fetchVacantes().then((v) => v && setVacantes(v));
    fetchClientes("Activo").then((cl) => setClientes(cl ?? []));
    fetchEntrevistadores().then((u) => setUsuarios(u ?? []));
  }, [recargar, vacanteInicialLista]);
  // Fase 4: el Kanban se revalida solo (WhatsApp, IA y otros usuarios mueven tarjetas) — se pausa
  // mientras hay una ficha abierta para no pisar lo que RH está editando.
  usePolling(() => recargar(), INTERVALO_TABLERO_MS, sel === null && vacanteInicialLista);

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

  // Filtrado compuesto. `datosBase` = todo menos el chip de alerta (es el universo que cuentan los chips).
  const datosBase = useMemo(() => {
    return datos.filter((c) => {
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
    vacantes,
    clientes,
    duplicadosSet,
  ]);
  const datosFiltrados = useMemo(() => {
    const prueba = ALERTAS_TABLERO.find((a) => a.id === alerta)?.prueba;
    return (prueba ? datosBase.filter(prueba) : datosBase).sort((a, b) => ordenarTablero(a, b, orden));
  }, [datosBase, alerta, orden]);
  const filtrando = Boolean(
    busqueda.trim() || alerta || filtroEstado !== "todos" || columnaResaltada || mostrarCerradas ||
      fCliente !== "" || fResponsable !== "" || fFuente || fConsentimiento !== "todos" || fApto !== "todos" ||
      fScoreMin !== "" || fScoreMax !== "" || fDuplicados,
  );

  const vacanteSeleccionada = vacantes.find((v) => v.id === filtroVacante);
  const totalFiltrosAvanzadosActivos =
    (fCliente !== "" ? 1 : 0) +
    (fResponsable !== "" ? 1 : 0) +
    (fFuente ? 1 : 0) +
    (fConsentimiento !== "todos" ? 1 : 0) +
    (fApto !== "todos" ? 1 : 0) +
    (fScoreMin !== "" || fScoreMax !== "" ? 1 : 0) +
    (fDuplicados ? 1 : 0) +
    (filtroEstado !== "todos" ? 1 : 0);

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
    setAlerta(null);
  };

  return (
    <div className="mx-auto max-w-[1400px] px-4 pb-6 pt-8 sm:px-6 sm:pb-8 sm:pt-10">
      {/* Rediseño 2026-10-07: título + subtítulo con el total en proceso y las vacantes abiertas; buscador, Vacante y
          Ordenar; los chips de alerta reemplazan el banner de consentimiento. */}
      <PageHeader
        title="Candidatos"
        subtitle={metricas
          ? `${metricas.en_proceso} en proceso · ${metricas.vacantes_abiertas} vacante${metricas.vacantes_abiertas === 1 ? "" : "s"} abierta${metricas.vacantes_abiertas === 1 ? "" : "s"}`
          : "Pipeline de selección"}
      >
        {puedeDecidir && (
          <Button size="sm" onClick={() => setCarga(true)}>
            <UploadCloud className="h-4 w-4" /> Cargar CVs
          </Button>
        )}
      </PageHeader>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {/* Búsqueda por nombre (también acepta el código) */}
          <label className="flex h-10 min-w-[240px] flex-1 items-center gap-2 rounded-[10px] border border-kb-line-2 bg-kb-card px-3 text-sm text-kb-ink-2 sm:flex-none">
            <Search className="h-4 w-4 shrink-0" />
            <input
              type="text"
              value={busqueda}
              onChange={(e) => setBusqueda(e.target.value)}
              placeholder="Buscar por nombre"
              aria-label="Buscar por nombre"
              className="min-w-0 flex-1 bg-transparent text-kb-ink outline-none"
            />
            {busqueda && (
              <button onClick={() => setBusqueda("")} className="text-kb-ink-3 hover:text-kb-ink" aria-label="Limpiar búsqueda">
                <X className="h-4 w-4" />
              </button>
            )}
          </label>

          <select
            id="filtro-vacante"
            aria-label="Vacante"
            value={filtroVacante}
            onChange={(e) => setFiltroVacante(e.target.value)}
            className="h-10 max-w-[280px] rounded-[10px] border border-kb-line-2 bg-kb-card px-3 text-sm text-kb-ink outline-none focus:border-brand"
          >
            <option value="">Vacante: Todas</option>
            {vacantes.map((v) => (
              <option key={v.id} value={v.id}>
                {v.titulo}{v.cliente ? ` · ${v.cliente}` : ""}{v.ubicacion ? ` (${v.ubicacion})` : ""}
              </option>
            ))}
          </select>

          <select
            aria-label="Ordenar"
            value={orden}
            onChange={(e) => setOrden(e.target.value as OrdenTablero)}
            className="h-10 rounded-[10px] border border-kb-line-2 bg-kb-card px-3 text-sm text-kb-ink outline-none focus:border-brand"
          >
            <option value="score">Ordenar: Score</option>
            <option value="dias">Ordenar: Días en etapa</option>
          </select>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Toggle de vista (Pipeline vs Lista) */}
          <div className="flex items-center rounded-[10px] border border-kb-line-2 bg-kb-card p-1">
            <button
              id="candidatos-vista-pipeline"
              onClick={() => cambiarVista("pipeline")}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition",
                vista === "pipeline" ? "bg-surface-2 text-ink" : "text-ink-3 hover:text-ink",
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
                vista === "lista" ? "bg-surface-2 text-ink" : "text-ink-3 hover:text-ink",
              )}
              title="Vista en Lista detallada"
            >
              <List className="h-3.5 w-3.5" /> Lista
            </button>
          </div>

          {/* Botón desplegable de filtros avanzados */}
          <button
            onClick={() => setFiltrosAvanzados((prev) => !prev)}
            className={cn(
              "flex h-10 items-center gap-1.5 rounded-[10px] border px-3 text-xs font-semibold transition",
              filtrosAvanzados || totalFiltrosAvanzadosActivos > 0
                ? "border-ink-3 bg-surface-2 text-ink"
                : "border-kb-line-2 bg-kb-card text-ink-2 hover:text-ink",
            )}
          >
            <Filter className="h-3.5 w-3.5" />
            Filtros
            {totalFiltrosAvanzadosActivos > 0 && (
              <span className="flex h-4 min-w-[16px] items-center justify-center rounded-full bg-ink px-1 text-[10px] text-bg">
                {totalFiltrosAvanzadosActivos}
              </span>
            )}
            <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", filtrosAvanzados && "rotate-180")} />
          </button>
        </div>
      </div>

      {live && <ChipsAlerta datos={datosBase} activa={alerta} onCambiar={setAlerta} />}

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

          {/* Estado del prefiltro (antes barra de chips; «No cumple» sirve para revisarlas aparte) */}
          <div>
            <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-ink-3">
              Estado
            </label>
            <select
              value={filtroEstado}
              onChange={(e) => setFiltroEstado(e.target.value as FiltroEstado)}
              className="w-full rounded-lg border border-border-soft bg-surface p-2 text-xs outline-none focus:border-brand"
            >
              {FILTROS_ESTADO.map((f) => (
                <option key={f.key} value={f.key}>{f.label}</option>
              ))}
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
              Score (%)
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

      {/* VISTA 1: PIPELINE (Kanban) — rediseño 2026-10-07 (components/dashboard/candidatos/tablero-kanban.tsx).
          Sin arrastre: la etapa la define la ruta; un intento solo muestra el aviso. */}
      {!cargando && vista === "pipeline" && (
        <TableroKanban
          etapas={etapas.filter((etapa) => !columnaResaltada || etapa === columnaResaltada)}
          datos={datosFiltrados}
          metricas={metricas?.stats ?? []}
          orden={orden}
          filtrando={filtrando}
          columnaResaltada={columnaResaltada}
          onAbrir={abrir}
          onIntentoArrastre={() => setAvisoTablero({ tono: "warn", texto: "La etapa la define la ruta. Abre la ficha para continuar." })}
        />
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
                <th className="px-4 py-3 text-left">Evaluación integral</th>
                <th className="px-4 py-3 text-left">Score</th>
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
                      {c.resultadoIntegral ? <BadgeIntegral r={c.resultadoIntegral} compacto /> : <span className="text-xs text-ink-3">—</span>}
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-mono text-xs font-semibold text-ink">
                        {scoreTarjeta(c) != null ? `${scoreTarjeta(c)}%` : "—"}
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
                      {c.ultimaActividadEn ? fechaCorta(c.ultimaActividadEn) : fechaCorta(c.creadoEn) ?? "—"}
                      {diasEnEtapa(c) != null && <span className="block text-[11px]">{diasEnEtapa(c)} d en la etapa</span>}
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

      <Toast msg={avisoTablero} onClose={() => setAvisoTablero(null)} />
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

type TabCandidato = "seguimiento" | "resumen" | "evaluaciones" | "documentos" | "whatsapp" | "contratacion" | "historial";

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

/** ¿El «dato faltante» que reportó la IA en el CV ya está en la ficha? (correo / teléfono capturados aparte). */
function datoYaCapturado(dato: string, c: Candidato): boolean {
  const t = dato.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const correo = /correo|e-?mail|mail/.test(t);
  const telefono = /telefono|celular|whatsapp|movil|numero de contacto/.test(t);
  if (/contacto/.test(t) && !correo && !telefono) return Boolean(c.correo && c.telefono);
  return (correo && Boolean(c.correo)) || (telefono && Boolean(c.telefono));
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
  const [motivoEliminar, setMotivoEliminar] = useState("");
  async function eliminarPersona() {
    setEliminando(true);
    setErrorEliminar("");
    const r = await eliminarCandidato(c.id, motivoEliminar.trim());
    setEliminando(false);
    if (!r.ok) return setErrorEliminar(r.error);
    setConfirmarEliminar(false);
    onEliminado?.();
  }
  // Proceso configurable (2026-10-06): con proceso la ficha abre en «Seguimiento» (etapa actual + siguiente acción)
  const conProceso = Boolean(c.tieneProceso || c.proceso?.tieneProceso);
  // 2026-10-06: toda postulación tiene ruta y «Resumen» la muestra completa (de la solicitud al Alta).
  const [tab, setTab] = useState<TabCandidato>(() => (c.etapa === "Contratación" && !conProceso ? "contratacion" : "resumen"));
  // Si el candidato ENTRA a Contratación mientras el modal ya está abierto (p.ej. RH lo mueve
  // de etapa sin cerrar la ficha), salta solo a esa pestaña para que no se pierda entre las
  // demás — sin esto, seguiría en "resumen" hasta que el usuario la buscara a mano.
  const etapaAnterior = useRef(c.etapa);
  useEffect(() => {
    if (c.etapa === "Contratación" && etapaAnterior.current !== "Contratación") {
      setTab((t) => (t === "seguimiento" ? t : "contratacion"));
    }
    etapaAnterior.current = c.etapa;
  }, [c.etapa]);
  const [aviso, setAviso] = useState<AvisoEstado>(null);
  const [ocupado, setOcupado] = useState("");

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
    setConfirmarDescartar({ motivo: "" });
  }

  async function descartarConfirmado() {
    if (!confirmarDescartar) return;
    setOcupado("descartar");
    const r = await decidirCandidato(c.id, "descartar", confirmarDescartar.motivo.trim());
    const data = resolver(r, "Candidato descartado.");
    if (data) {
      setConfirmarDescartar(null);
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
  // UX 2026-10-07: menú «…» del encabezado — cambiar ruta, agregar actividad, opciones de prueba, descartar, eliminar.
  const [seg, setSeg] = useState<SeguimientoProcesoData | null>(c.proceso ?? null);
  const [actividad, setActividad] = useState(false);
  const [cambiarRuta, setCambiarRuta] = useState(false);
  const [reabrir, setReabrir] = useState<null | { motivo: string }>(null);
  // Evaluaciones unificadas: botón ÚNICO «Agregar evaluación» (2026-10-01). Crear una entrevista humana mueve a
  // Filtro humano desde Prefiltro / Filtro Red Human; cualquier otra evaluación no cambia la columna.
  const [agregarEval, setAgregarEval] = useState<PresetEvaluacion | null>(null);
  const [versionEval, setVersionEval] = useState(0);
  async function aplicarRutaVigente() {
    setOcupado("ruta");
    const r = await aplicarProcesoVigente(c.id);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setCambiarRuta(false);
    setSeg(r.data.proceso);
    setVersionEval((x) => x + 1);
    onCambio(r.data.candidato);
    const h = r.data.aplicado.heredados;
    setAviso({ tono: "ok", texto: `Ruta actualizada a la versión ${r.data.aplicado.version}.${h.length ? ` Se conservan con su actividad: ${h.join(", ")}.` : ""}` });
  }

  /** «Reactivar» (especificación 2026-10-10): un descartado o sin respuesta vuelve a SU etapa y retoma la ruta; queda en
   *  el historial con tu nombre y el motivo. Sustituye a «Reabrir postulación». */
  async function reabrirPostulacion() {
    if (!reabrir) return;
    setOcupado("reabrir");
    const r = await reactivarPostulacion(c.id, reabrir.motivo.trim());
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setReabrir(null);
    onCambio(r.data);
    setAviso({ tono: "ok", texto: "Candidato reactivado: retoma su ruta." });
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

  // 2026-10-08: menú «…» simplificado. «Cambiar ruta» solo aparece cuando hay una versión más nueva de la ruta (aplica la
  // vigente conservando lo hecho); la «Liga de Telegram» genérica se retiró — cada actividad trae su liga REAL en
  // «Avance de la ruta»; «Reiniciar postulación» solo existe con Modo Prueba activo.
  const accionesFicha: AccionMenu[] = !(live && puedeDecidir) ? [] : [
    ...(seg?.desactualizado
      ? [{ etiqueta: "Cambiar ruta…", icono: <Route />, onClick: () => setCambiarRuta(true), disabled: Boolean(ocupado),
           title: "Hay una versión más nueva de la ruta" }] : []),
    ...(c.activa !== false ? [{ etiqueta: "Agregar actividad a este candidato…", icono: <Plus />, onClick: () => setActividad(true) }] : []),
    ...(c.activa === false && MOTIVOS_REACTIVABLES.includes(c.motivoCierre ?? "")
      ? [{ etiqueta: "Reactivar…", icono: <RotateCw />, onClick: () => setReabrir({ motivo: "" }), disabled: Boolean(ocupado) }] : []),
    ...(modoPrueba
      ? [{ etiqueta: "Prueba · Reiniciar postulación", icono: <FlaskConical />, onClick: () => void reiniciarPrueba(), disabled: Boolean(ocupado),
           title: "Cierra la postulación actual y crea una limpia para volver a probar desde cero (solo Modo Prueba)." }] : []),
    ...(c.activa !== false ? [{ etiqueta: "Descartar candidato…", icono: <ThumbsDown />, peligrosa: true, onClick: descartar, disabled: Boolean(ocupado) }] : []),
    { etiqueta: "Eliminar candidato…", icono: <Trash2 />, peligrosa: true, onClick: () => setConfirmarEliminar(true), disabled: eliminando },
  ];

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
              {/* 2026-10-07: correo y teléfono editables en cualquier etapa (todas las Cuentas) */}
              <ContactoCandidato c={c} editable={live && puedeDecidir} onCambio={onCambio} />
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1">
            {accionesFicha.length > 0 && <MenuAcciones etiqueta="Acciones del candidato" acciones={accionesFicha} />}
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
                  <h3 className="font-display text-lg font-bold">¿Eliminar a este candidato?</h3>
                  <p className="mt-2 text-sm leading-relaxed text-ink-2">
                    <b>{c.nombre}</b> desaparecerá del tablero y de las búsquedas; todas sus postulaciones activas se cerrarán
                    {c.totalPostulaciones && c.totalPostulaciones > 1 ? ` (tiene ${c.totalPostulaciones})` : ""}. Nada se borra físicamente:
                    su historial (entrevistas, expediente, mensajes) se conserva y la acción queda en la bitácora.
                  </p>
                  <label className="mt-3 flex flex-col gap-1">
                    <span className="text-xs font-medium text-ink-2">Motivo (obligatorio)</span>
                    <input
                      value={motivoEliminar}
                      onChange={(e) => setMotivoEliminar(e.target.value)}
                      placeholder="Ej. registro duplicado"
                      className="h-10 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
                    />
                  </label>
                  {errorEliminar && <p className="mt-2 text-sm font-semibold text-bad">{errorEliminar}</p>}
                </div>
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setConfirmarEliminar(false)} disabled={eliminando}>
                  Cancelar
                </Button>
                <Button size="sm" className="bg-bad text-white hover:bg-bad/90" onClick={eliminarPersona} disabled={eliminando || motivoEliminar.trim().length < 5}>
                  <Trash2 className="h-4 w-4" /> {eliminando ? "Eliminando…" : "Sí, eliminar candidato"}
                </Button>
              </div>
            </div>
          </div>
        )}

        {/* Pestañas: Contratación (ruta + acción principal; antes «Resumen») · Evaluación integral · Documentos · WhatsApp ·
            [Expediente] · Historial — 2026-10-09 */}
        <div className="border-b border-border-soft bg-surface-2/70 pt-2">
          <div className="scroll-x gap-1 px-3 sm:px-5">
            {(
              [
                { id: "resumen", label: "Contratación", icon: User, tone: "brand" },
                { id: "evaluaciones", label: "Evaluación integral", icon: Sparkles, tone: "human" },
                { id: "documentos", label: "Documentos", icon: FileText, tone: "brand" },
                // 2026-10-09: la conversación es de ESTA postulación — la pestaña lleva el nombre del puesto
                { id: "whatsapp", label: c.puesto || "Conversación", icon: MessageCircle, tone: "good", badge: c.mensajes },
                // la pestaña del expediente vive en Contratación Y Onboarding
                ...(c.etapa === "Contratación" || c.etapa === "Onboarding"
                  ? [{ id: "contratacion", label: "Expediente", icon: Briefcase, tone: "warn" }]
                  : []),
                { id: "historial", label: "Historial", icon: Clock, tone: "brand" },
              ] as { id: TabCandidato; label: string; icon: typeof User; tone: string; badge?: number }[]
            ).map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={cn(
                  "flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-t-lg border-b-2 px-3 py-2 text-[13px] font-semibold transition",
                  tab === t.id ? TAB_TONE_ACTIVA[t.tone] : "border-transparent text-ink-3 hover:bg-surface/50 hover:text-ink",
                )}
              >
                <t.icon className="h-3.5 w-3.5" />
                {t.label}
                {Boolean(t.badge) && (
                  <span className={cn("rounded-full px-1.5 font-mono text-[10px] font-bold", TAB_TONE_BADGE[t.tone])}>{t.badge}</span>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* Cuerpo — sin barra flotante inferior: la acción principal vive en el Resumen; lo secundario en «…» */}
        <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-3 sm:p-5">
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
            <div className="flex flex-wrap items-center gap-2 rounded-xl border border-warn/30 bg-warn-soft/40 px-3 py-2 text-[12px] text-ink-2">
              <ShieldCheck className="h-4 w-4 shrink-0 text-warn" />
              <span className="flex-1"><b className="text-ink">Sin consentimiento registrado.</b> La LFPDPPP lo exige antes de tratar sus datos o abrir su expediente.</span>
              {live && (
                <Button size="sm" variant="outline" onClick={consentir} disabled={Boolean(ocupado)}>Registrar consentimiento</Button>
              )}
            </div>
          )}

          {(tab === "resumen" || tab === "seguimiento") && (
            <SeguimientoProceso
              c={c}
              live={live && puedeDecidir}
              version={versionEval}
              onCambio={onCambio}
              setAviso={setAviso}
              onSeg={setSeg}
              onAbrir={(p) => setTab(p)}
              onSolicitarDocumentos={() => setConfirmacion("solicitar")}
              onAlta={() => setConfirmacion("alta")}
              onDescartar={(motivo) => (live ? setConfirmarDescartar({ motivo }) : descartar())}
              onIniciarEvaluacion={(p) => setAgregarEval({
                tipo: p.tipo as PresetEvaluacion["tipo"], pasoId: p.pasoId, titulo: p.titulo,
                evaluador: p.usuarioId ? { ...evaluadorVacio, usuarioId: p.usuarioId } : undefined,
              })}
            />
          )}
          {tab === "evaluaciones" && (
            <>
              <PanelResultadoIntegral r={c.resultadoIntegral} />
              <PestanaResumen c={c} live={live} onCambio={onCambio} setTab={setTab} seccion="evaluacion" />
              <PestanaEvaluaciones c={c} live={live} onCambio={onCambio} versionEval={versionEval} />
            </>
          )}
          {tab === "documentos" && (
            <>
              <PestanaResumen c={c} live={live} onCambio={onCambio} setTab={setTab} seccion="perfil" />
              <PestanaDocumentos c={c} live={live} onCambio={onCambio} setAviso={setAviso} />
            </>
          )}
          {tab === "whatsapp" && <PestanaWhatsApp c={c} live={live} onCambio={onCambio} />}
          {tab === "contratacion" && (c.etapa === "Contratación" || c.etapa === "Onboarding") && (
            <PanelContratacion c={c} live={live} onCambio={onCambio} setAviso={setAviso} onDocumentos={setConfirmacion} />
          )}
          {tab === "historial" && <PestanaResumen c={c} live={live} onCambio={onCambio} setTab={setTab} seccion="historial" />}
        </div>
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
                <span className="text-xs font-medium text-ink-2">Motivo (obligatorio)</span>
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
                  disabled={ocupado === "descartar" || confirmarDescartar.motivo.trim().length < 5}
                >
                  <ThumbsDown className="h-4 w-4" /> {ocupado === "descartar" ? "Descartando…" : "Sí, descartar"}
                </Button>
              </div>
            </div>
          </div>
        )}
      {agregarEval && (
        <ModalAgregarEvaluacion
          c={c}
          preset={agregarEval}
          onClose={() => setAgregarEval(null)}
          onListo={(r, texto) => {
            setAgregarEval(null);
            setVersionEval((x) => x + 1);
            setTab((t) => (t === "seguimiento" ? t : "resumen"));
            // un correo/WhatsApp que no salió nunca es silencioso (toast amarillo + detalle)
            const lineas = lineasResultados(r.resultados);
            const correoFallo = (r.resultados ?? []).some((x) => x.canal === "correo" && !x.enviado && x.destino);
            if (r.advertencias?.length || correoFallo) {
              setToast({ tono: "warn", texto: correoFallo ? "Evaluación asignada, pero el correo falló. Verifica la API Key o el Dominio" : "Evaluación asignada con avisos", detalle: r.advertencias ?? [] });
            }
            setAviso({ tono: lineas.some((l) => !l.ok) ? "warn" : "ok", texto: [texto, ...lineas.map((l) => `${l.ok ? "✓" : "✗"} ${l.texto}`)].join(" · ") });
            if (r.candidato) onCambio(r.candidato);
          }}
        />
      )}
      {actividad && (
        <ModalActividad
          c={c}
          etapaActual={c.etapa}
          onClose={() => setActividad(false)}
          onAgregada={(r, aviso) => {
            setActividad(false);
            setSeg(r.proceso);
            setVersionEval((x) => x + 1);
            onCambio(r.candidato);
            setAviso(aviso ?? { tono: "ok", texto: `«${r.paso.nombre}» se agregó solo a este candidato. La plantilla y la vacante no cambian.` });
          }}
        />
      )}
      {cambiarRuta && (
        <ModalMarco titulo="Cambiar ruta" subtitulo="Aplica la versión vigente de la ruta de la vacante a este candidato." onClose={() => setCambiarRuta(false)}>
          <p className="text-sm text-ink-2">
            Lo que ya tiene actividad se conserva aunque no exista en la versión nueva (queda como «fuera de la ruta vigente», no obligatorio).
            Su etapa actual no cambia.
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setCambiarRuta(false)}>Cancelar</Button>
            <Button size="sm" onClick={aplicarRutaVigente} disabled={ocupado === "ruta"}>{ocupado === "ruta" ? "Aplicando…" : "Aplicar versión vigente"}</Button>
          </div>
        </ModalMarco>
      )}
      {reabrir && (
        <ModalMarco titulo="Reactivar candidato" subtitulo={`Vuelve a quedar activo en ${nombreEtapa(c.etapa)} y retoma su ruta; queda en el historial con tu nombre.`} onClose={() => setReabrir(null)}>
          <input
            autoFocus
            value={reabrir.motivo}
            onChange={(e) => setReabrir({ motivo: e.target.value })}
            placeholder="Motivo (obligatorio, al menos 10 caracteres)"
            className="h-10 w-full rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
          />
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setReabrir(null)}>Cancelar</Button>
            <Button size="sm" onClick={reabrirPostulacion} disabled={ocupado === "reabrir" || reabrir.motivo.trim().length < 10}>
              {ocupado === "reabrir" ? "Reactivando…" : "Reactivar"}
            </Button>
          </div>
        </ModalMarco>
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
            texto={`RH autoriza el alta: el registro se mueve a Colaboradores y la postulación queda cerrada como contratada. Expediente al ${c.expedienteProgreso ?? 0}%${(c.expedienteProgreso ?? 0) < 100 ? (modoPrueba ? " · Modo Prueba permite el alta incompleta." : " · el alta exige el expediente al 100 % validado.") : "."}`}
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

/** UX 2026-10-07: el contenido que vivía en «Resumen» se reparte SIN duplicar — «perfil» (CV extraído) va en
 * Documentos, «evaluacion» (prefiltro, capacitación, afinidad) en Evaluación integral e «historial» (contacto,
 * omisiones, historial y otras postulaciones) en Historial. Recomendación, fortalezas y puntos por validar viven en el
 * Resumen; el status de la Entrevista Red Human ya está en Evaluación integral. */
function PestanaResumen({
  c,
  live,
  onCambio,
  setTab,
  seccion,
}: {
  c: Candidato;
  live: boolean;
  onCambio: (c: Candidato) => void;
  setTab: (t: TabCandidato) => void;
  seccion: "perfil" | "evaluacion" | "historial";
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

  return (
    <div className="flex flex-col gap-4">
      {/* A. Datos principales */}
      {seccion === "historial" && <div className="flex flex-wrap gap-2">
        {datosPrincipales.map((d, i) => (
          <Info key={i} icon={d.icon} v={d.v} />
        ))}
      </div>}

      {/* B. Perfil extraído del CV */}
      {seccion === "perfil" && <div>
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
      </div>}

      {/* C. Prefiltro — SOLO filtro de entrada (Cumple / No cumple), sin score (2026-09-13) */}
      {seccion === "evaluacion" && <div>
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
      </div>}

      {seccion === "evaluacion" && (c.capacitacion?.length ?? 0) > 0 && (
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

      {seccion === "historial" && (c.actividadesOmitidas?.length ?? 0) > 0 && (
        <p className="text-[11px] text-ink-3">
          Omitido manualmente: {c.actividadesOmitidas!.map((o) => `${nombreEtapa(o.actividad)} (${o.usuario}, ${fechaCorta(o.fecha)}${o.motivo ? `: ${o.motivo}` : ""})`).join(" · ")}
        </p>
      )}

      {/* 2026-09-22: historial del expediente — decisiones humanas registradas (nunca se borran) */}
      {seccion === "historial" && (c.historial?.length ?? 0) > 0 && (
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

      {/* D. Evaluación integral (Análisis de CV + Entrevista Red Human) — solo con entrevista válida */}
      {seccion === "evaluacion" && c.afinidadGlobal != null && c.evaluacionIntegral && (
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

      {/* H. Fase 2 — otras postulaciones de la misma persona (historial, más reciente primero) */}
      {seccion === "historial" && (c.historialPostulaciones?.length ?? 0) > 0 && (
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

      {/* ===== Evaluaciones (unificadas, 2026-09-29): las mismas tarjetas que en «Resumen»; «Ver resultado» abre el detalle ===== */}
      <PanelEvaluaciones c={c} live={Boolean(live) && puedeDecidir} version={versionEval} onCambio={onCambio} titulo="Evaluaciones · resultados" />
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
  // 2026-10-08: un «dato faltante» del CV que la ficha YA tiene (correo, teléfono) no es un foco de atención.
  const faltantes = ((cv.datos_faltantes as string[]) || (a.datos_faltantes || [])).filter((df) => !datoYaCapturado(df, c));
  const listaArchivos = c.listaArchivos ?? [];

  const [tipoNuevo, setTipoNuevo] = useState<string | null>(null);
  const inputArchivo = useRef<HTMLInputElement>(null);
  async function subirDocumento(archivo: File | undefined, tipo: string) {
    if (!archivo) return;
    setCargandoCV(true);
    setAviso(null);
    const r = await subirArchivoCandidato(c.id, archivo, tipo);
    setCargandoCV(false);
    setTipoNuevo(null);
    if (!r.ok) {
      setAviso({ tono: "error", texto: r.error });
      return;
    }
    setAviso({ tono: "ok", texto: tipo === "cv" ? "CV procesado: datos y score de afinidad actualizados." : "Documento agregado." });
    if (r.data.candidato) onCambio(r.data.candidato);
  }

  return (
    <div className="flex flex-col gap-4">
      {(habilidades.length > 0 || idiomas.length > 0) && (
        <div className="flex flex-wrap items-center gap-1.5">
          {habilidades.map((h, i) => (
            <span key={`h${i}`} className="rounded-md border border-brand/25 bg-brand-soft px-2 py-0.5 text-[11px] font-medium text-brand">{h}</span>
          ))}
          {idiomas.map((idm, i) => (
            <span key={`i${i}`} className="inline-flex items-center gap-1 rounded-md border border-border-soft bg-surface-2 px-2 py-0.5 text-[11px] text-ink-2">
              <Globe className="h-3 w-3 text-ink-3" /> {idm}
            </span>
          ))}
        </div>
      )}

      {/* Alertas del CV */}
      {(alertas.length > 0 || faltantes.length > 0) && (
        <div className="rounded-xl border border-warn/30 bg-warn-soft/30 px-3 py-2.5">
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

      {/* 2026-10-06 (ruta Masivos): documentos pedidos ANTES de Contratación — RH los valida aquí mismo (misma fila
          y mismas acciones del expediente); el paso «Validar documentos» del seguimiento se actualiza solo. */}
      {c.expedienteId != null && c.etapa !== "Contratación" && c.etapa !== "Onboarding" && expediente && (
        <div>
          <Eyebrow>Validar documentos</Eyebrow>
          <div className="mt-2 flex flex-col gap-2">
            {(expediente.documentos ?? []).filter((d) => !d.interno).map((d) => (
              <FilaDocumentoSimple key={d.nombre} d={d} expedienteId={c.expedienteId ?? undefined} live={live && puedeDecidir} onActualizado={setExpediente} />
            ))}
          </div>
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

      {/* Archivos de la persona: una línea cada uno + «Agregar documento» */}
      <div>
        <div className="flex items-center justify-between gap-2">
          <Eyebrow>Archivos ({listaArchivos.length})</Eyebrow>
          {live && puedeDecidir && (
            tipoNuevo === null ? (
              <Button size="sm" variant="outline" onClick={() => setTipoNuevo("cv")} disabled={cargandoCV}>
                <Plus className="h-3.5 w-3.5" /> Agregar documento
              </Button>
            ) : (
              <div className="flex items-center gap-1.5">
                <select
                  value={tipoNuevo}
                  onChange={(e) => setTipoNuevo(e.target.value)}
                  className="h-8 rounded-lg border border-border-soft bg-surface px-2 text-xs outline-none focus:border-brand"
                  aria-label="Tipo de documento"
                >
                  <option value="cv">CV</option>
                  <option value="identificacion">Identificación</option>
                  <option value="certificado">Certificado</option>
                  <option value="carta">Carta</option>
                  <option value="otro">Otro</option>
                </select>
                <Button size="sm" onClick={() => inputArchivo.current?.click()} disabled={cargandoCV}>
                  {cargandoCV ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />} Elegir archivo
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setTipoNuevo(null)} disabled={cargandoCV} aria-label="Cancelar">
                  <X className="h-3.5 w-3.5" />
                </Button>
                <input
                  ref={inputArchivo}
                  type="file"
                  className="hidden"
                  accept=".pdf,.doc,.docx,.jpg,.jpeg,.png,.webp"
                  onChange={(e) => {
                    void subirDocumento(e.target.files?.[0], tipoNuevo);
                    e.target.value = "";
                  }}
                />
              </div>
            )
          )}
        </div>
        {listaArchivos.length === 0 ? (
          <p className="mt-1.5 text-xs text-ink-3">Sin archivos.</p>
        ) : (
          <ul className="mt-1.5 divide-y divide-border-faint rounded-xl border border-border-soft bg-surface">
            {listaArchivos.map((a) => (
              <li key={a.id} className="flex items-center gap-2 px-3 py-1.5">
                <FileText className="h-3.5 w-3.5 shrink-0 text-brand" />
                <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{a.nombre}</span>
                <span className="hidden shrink-0 font-mono text-[10px] text-ink-3 sm:inline">{a.tipo} · {pesoLegible(a.tamano)} · {a.subido}</span>
                <a
                  href={urlArchivoCandidato(c.id, a.id)}
                  target="_blank"
                  rel="noreferrer"
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-ink-2 transition hover:bg-surface-2 hover:text-brand"
                  title="Descargar"
                  aria-label={`Descargar ${a.nombre}`}
                >
                  <Download className="h-3.5 w-3.5" />
                </a>
              </li>
            ))}
          </ul>
        )}
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
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2 text-xs text-ink-3">
        {/* 2026-10-09: encabezado del chat = el puesto de esta postulación (no el genérico «WhatsApp») */}
        <span className="min-w-0 truncate">
          <b className="font-semibold text-ink">{c.puesto || "Sin vacante asignada"}</b> · {c.telefono ? `+${c.telefono}` : "Sin teléfono"}
        </span>
        <button
          onClick={cargar}
          disabled={cargandoMsgs}
          className="flex items-center gap-1 rounded-lg px-2 py-1 font-semibold text-ink-2 transition hover:bg-surface-2 disabled:opacity-50"
          title="Actualizar conversación"
        >
          <RotateCw className={cn("h-3.5 w-3.5", cargandoMsgs && "animate-spin")} /> Actualizar
        </button>
      </div>

      {/* Feed de Conversación de WhatsApp */}
      <div className="flex max-h-[60vh] min-h-[300px] flex-col gap-2 overflow-y-auto rounded-xl border border-border-soft bg-surface-2/40 p-3">
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
                  {m.canal === "whatsapp" ? "WhatsApp" : m.canal === "telegram" ? "Telegram" : "Simulador"}
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
  // 2026-10-09: «Firmar documentos» (carta + contrato en un solo acto) con el modo de la Cuenta: electrónica, papel o demo
  const [firmaCfg, setFirmaCfg] = useState<{ modo: ModoFirma; clientId: string | null; testMode: boolean } | null>(null);
  const [firmas, setFirmas] = useState<FirmaDocumento[]>([]);
  const [firmando, setFirmando] = useState(false);
  const [padDemo, setPadDemo] = useState<{ id: number; error: string; ocupado: boolean } | null>(null);
  const { usuario } = useSesion();
  const archivoContrato = useRef<HTMLInputElement>(null);
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
    fetchModoFirma().then((x) => setFirmaCfg(x ? { modo: x.modo, clientId: x.clientId, testMode: x.testMode } : { modo: "papel", clientId: null, testMode: false }));
    void cargarFirmas();
  }, [cargarFirmas]);

  /** 2026-10-09: estado de «Firmar documentos» (la solicitud unificada o un contrato ya firmado por cualquier vía). */
  const firmaDocs = firmas.find((f) => (f.documento === "documentos" || f.documento === "contrato") && f.estado !== "cancelada" && f.estado !== "error");
  function textoFirmado(): string | null {
    if (c.contratoFirmado || (firmaDocs && firmaDocs.estado !== "enviada")) return "Documentos firmados";
    if (!firmaDocs) return null;
    const rh = firmaDocs.firmantes.find((x) => x.rol === "rh");
    return rh?.estado === "firmado" ? "Documentos: falta la firma del candidato" : null;
  }

  async function refrescarFicha(mensaje: string) {
    setAviso({ tono: "ok", texto: mensaje });
    void cargarFirmas();
    const ficha = await fetchCandidato(c.id);
    if (ficha) onCambio(ficha);
  }

  /** Papel (o firma física en cualquier modo): el PDF firmado se sube y es el MISMO contrato que ve Onboarding. */
  async function adjuntarContrato(f: File | undefined) {
    if (archivoContrato.current) archivoContrato.current.value = "";
    if (!f || c.expedienteId == null) return;
    const r = await subirContratoFirmado(c.expedienteId, f);
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    await refrescarFicha("Documentos firmados guardados en el expediente. Si no falta nada obligatorio, el candidato pasa solo a Onboarding.");
  }

  /** Botón ÚNICO «Firmar documentos». Electrónica: modal incrustado de Dropbox Sign (el candidato firma en su liga).
   * Demo: RH dibuja o escribe su firma aquí. Papel: abre el PDF unido para imprimir; luego «Adjuntar documentos firmados». */
  async function firmarDocs() {
    if (c.expedienteId == null) return;
    setResultadoDoc(null);
    setFirmando(true);
    const r = await firmarDocumentos(c.expedienteId);
    setFirmando(false);
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    void cargarFirmas();
    if (r.data.modo === "papel") {
      window.open(urlDocumentosPdf(c.expedienteId), "_blank", "noopener");
      return setAviso({ tono: "ok", texto: "Imprime la carta y el contrato, fírmenlos y súbelos con «Adjuntar documentos firmados» (menú «…»)." });
    }
    const rh = (r.data.firmantes ?? []).find((x) => x.rol === "rh");
    if (rh?.estado === "firmado") {
      return setAviso({ tono: "ok", texto: "Tu firma ya está registrada; falta la del candidato (la hace desde su liga de expediente)." });
    }
    if (r.data.modo === "demo" && r.data.id != null) return setPadDemo({ id: r.data.id, error: "", ocupado: false });
    if (!r.data.signUrl || !firmaCfg?.clientId) return;
    await abrirFirmaEmbebida({
      clientId: firmaCfg.clientId,
      signUrl: r.data.signUrl,
      testMode: firmaCfg.testMode,
      onFirmado: () => {
        setAviso({ tono: "ok", texto: "Firmaste los documentos. El candidato firma desde su liga; al completarse pasa solo a Onboarding." });
        setTimeout(() => void cargarFirmas(), 1500);
      },
      onError: (m) => setAviso({ tono: "error", texto: `Firma electrónica: ${m}` }),
    });
  }

  async function firmarComoRH(datos: FirmaCapturada) {
    if (!padDemo) return;
    setPadDemo({ ...padDemo, ocupado: true, error: "" });
    const r = await firmarDemoRH(padDemo.id, datos);
    if (!r.ok) return setPadDemo({ ...padDemo, ocupado: false, error: r.error });
    setPadDemo(null);
    await refrescarFicha(r.data.pasoAOnboarding
      ? "Documentos firmados por ambos: el candidato pasó a Onboarding."
      : r.data.completa ? "Documentos firmados por ambos." : "Firmaste los documentos. Falta la firma del candidato (la hace desde su liga de expediente).");
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
          {c.etapa === "Contratación" && (
            <Button size="sm" onClick={() => void firmarDocs()} disabled={firmando || Boolean(textoFirmado())}
              title={firmaCfg?.modo === "papel" ? "Imprime carta y contrato; luego sube el PDF firmado"
                : firmaCfg?.modo === "demo" ? "Firma de demostración en la plataforma (sin validez legal)"
                : "Firma electrónica: tú firmas aquí y el candidato desde su liga"}>
              <FileCheck2 className="h-4 w-4" /> {firmando ? "Preparando firma…" : textoFirmado() ?? "Firmar documentos"}
            </Button>
          )}
          {/* Onboarding v2: «Solicitar documentos» ya no vive en Contratación — la primera solicitud la hace «Iniciar Onboarding» */}
          {onDocumentos && c.etapa === "Onboarding" && (
            <Button size="sm" variant="outline" onClick={() => onDocumentos("solicitar")} disabled={Boolean(ocupado)}>
              <Send className="h-4 w-4" /> Solicitar documentos
            </Button>
          )}
          <input ref={archivoContrato} type="file" accept="application/pdf" className="hidden" onChange={(e) => void adjuntarContrato(e.target.files?.[0])} />
          <MenuAcciones
            acciones={[
              { etiqueta: "Ver PDF de la carta (enviar por WhatsApp / correo)", icono: <FileText className="h-4 w-4" />, onClick: () => { setResultadoDoc(null); setDocPreview("carta"); } },
              { etiqueta: "Ver PDF del contrato", icono: <FileCheck2 className="h-4 w-4" />, onClick: () => { setResultadoDoc(null); setDocPreview("contrato"); } },
              ...(c.expedienteId != null
                ? [{ etiqueta: "Imprimir carta y contrato (un solo PDF)", icono: <FileText className="h-4 w-4" />, onClick: () => window.open(urlDocumentosPdf(c.expedienteId!), "_blank", "noopener") }]
                : []),
              { etiqueta: "Adjuntar documentos firmados (papel / PDF)", icono: <FileCheck2 className="h-4 w-4" />, onClick: () => archivoContrato.current?.click() },
              ...(c.etapa === "Contratación"
                ? [{ etiqueta: "Iniciar Onboarding manualmente…", icono: <Send className="h-4 w-4" />, onClick: () => enviarOnboarding(),
                  disabled: Boolean(ocupado) || (!requisitosOnboarding && !modoPrueba) }]
                : []),
            ]}
          />
        </div>
      )}

      {firmas.length > 0 && (
        <div className="mt-3 rounded-xl border border-border-soft bg-surface p-3">
          <p className="flex items-center gap-1.5 text-[12px] font-semibold text-ink-2"><IconoFirma className="h-3.5 w-3.5" /> Firma de documentos</p>
          <ul className="mt-2 space-y-1.5 text-[12px]">
            {firmas.map((f) => (
              <li key={f.id} className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-ink-2">
                  {f.documentoTexto}{f.modo === "demo" ? " (demostración)" : f.testMode ? " (prueba)" : ""} · {f.firmantes.map((x) => `${x.rol === "rh" ? "RH" : "Candidato"}: ${x.estado === "firmado" ? "firmó" : "pendiente"}`).join(" · ")}
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
      {padDemo && (
        <ModalMarco titulo="Firmar documentos" subtitulo="Carta de intención y contrato · firma de demostración" onClose={() => setPadDemo(null)} ancho="max-w-lg">
          <PadFirma nombreSugerido={usuario?.nombre ?? ""} ocupado={padDemo.ocupado} error={padDemo.error}
            onCancelar={() => setPadDemo(null)} onFirmar={(d) => void firmarComoRH(d)} />
        </ModalMarco>
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
