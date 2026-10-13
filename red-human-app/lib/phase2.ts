/* ============================================================
   Datos de ejemplo — Red Human AI · Fase 2
   Onboarding · Capacitación · Desempeño · Clima
   ============================================================ */

/* -------------------- Onboarding (3.11) -------------------- */
/** Onboarding v2 (2026-09-28): «no_aplica» lo marca SOLO RH con motivo (ver `estadoOnboarding`). */
export type EstadoDoc = "recibido" | "revision" | "pendiente" | "rechazado" | "no_aplica";
export type EstadoDocOnboarding = "Pendiente" | "Por revisar" | "Aprobado" | "Rechazado" | "No aplica";

export interface ValidacionDoc {
  tipoDetectado?: string | null;
  coincideTipo?: boolean | null;
  legible?: boolean | null;
  completo?: boolean | null;
  vigente?: boolean | null;
  coincideTitular?: boolean | null;
  motivoRechazo?: string | null;
}

export interface DocExpediente {
  nombre: string;
  estado: EstadoDoc;
  /* presentes cuando vienen de la API */
  obligatorio?: boolean;
  notas?: string;
  archivo?: string;
  tieneArchivo?: boolean;
  mime?: string;
  tamano?: number;
  subido?: string;
  revisadoPor?: string;
  validacion?: ValidacionDoc | null;
  /** 2026-09-20 (B3): trazabilidad — cuándo/por qué canal se pidió y cuándo/por dónde se recibió. */
  solicitadoEn?: string | null;
  solicitadoCanal?: string;
  solicitudes?: { en: string; canal: string; tipo: "solicitud" | "recordatorio"; por: string }[];
  recibidoEn?: string | null;
  recibidoCanal?: string;
  estadoSimple?: "Pendiente" | "Recibido" | "Rechazado" | "No aplica";
  /** Onboarding v2: Pendiente | Por revisar | Aprobado (confirmado por RH) | Rechazado | No aplica */
  estadoOnboarding?: EstadoDocOnboarding;
  motivoNoAplica?: string;
  noAplicaPor?: string;
  /** Fase 2: documento interno de RH (contrato firmado) — fuera del porcentaje y de lo que se pide al candidato. */
  interno?: boolean;
  aprobado?: boolean;
  /** Cambio 25: cada archivo es su propio registro (fotos del auto = 4) y la corrección que pidió RH. */
  id?: number;
  cantidad?: number;
  esFoto?: boolean;
  archivos?: { id: number; nombre: string; mime: string; canal: string; subidoEn: string | null; convertidoDe: string }[];
  recibidos?: number;
  /** Pendiente · Por revisar · Aprobado · Corrección pedida · Rechazado · No aplica */
  estadoRevision?: string;
  correccion?: { motivo: string; por: string; en: string | null } | null;
}

/** Cambio 25: estado del expediente de documentos. */
export type ClaveEstadoDocumentos = "pendiente" | "por_revisar" | "correccion" | "aprobados" | "excepcion";

/** Onboarding v2 (Fase 3): resumen del tablero. «No aplica» y «Cancelada» NO cuentan en el total (se muestran aparte). */
export interface ResumenTableroOnboarding {
  iniciado: boolean;
  documentos: {
    aprobados: string[];
    faltantes: { tipo: string; estado: EstadoDocOnboarding; obligatorio: boolean }[];
    noAplica: { tipo: string; motivo: string; por: string }[];
    total: number;
    pct: number;
  };
  tareas: { realizadas: number; pendientes: number; atrasadas: number; total: number; pct: number; canceladas: { nombre: string; motivo: string; por: string }[] };
  ingreso: { prevista: string | null; real: string | null; confirmado: boolean; confirmadoPor: string; confirmadoEn: string | null };
  alta: boolean;
  puedeAlta: boolean;
  cerrado: boolean;
  cerradoPor: string;
  cerradoEn: string | null;
  puedeCerrar: boolean;
  faltanCierre: string[];
  noIngreso: { en: string | null; por: string; motivo: string } | null;
  puedeNoIngreso: boolean;
}

export interface NuevoIngreso {
  /** Onboarding v2 (Fase 3): null si las tablas de módulos no están disponibles. */
  onboarding?: ResumenTableroOnboarding | null;
  /** Onboarding v2: obligatorios que aún no están Aprobados (lo que falta para el 100 %). */
  noAprobados?: string[];
  id: string;
  nombre: string;
  puesto: string;
  ubicacion: string;
  ingreso: string;
  progreso: number;
  tono: number;
  documentos: DocExpediente[];
  /** Cambio 25: Pendiente · Por revisar · Corrección pedida · Documentos aprobados · Excepción */
  estadoDocumentos?: { clave: ClaveEstadoDocumentos; texto: string };
  excepcionDocumentos?: { por: string; motivo: string; en: string } | null;
  /* --- presentes cuando vienen de la API --- */
  expedienteId?: number;
  estado?: "integracion" | "completo" | "alta" | string;
  fechaIngreso?: string | null;
  /* Fase 3: recordatorios automáticos de documentos (null = apagados para este expediente) */
  documentosHasta?: string | null;
  ultimoRecordatorioEn?: string | null;
  /** 2026-09-17: recordatorios en 3 niveles (1 ligero, 2 intermedio, 3 definitivo). */
  recordatoriosEnviados?: number;
  nivelRecordatorio?: 1 | 2 | 3;
  tonoRecordatorio?: "ligero" | "intermedio" | "definitivo";
  recordatoriosAgotados?: boolean;
  /* --- condiciones finales de contratación, capturadas en la etapa Contratación --- */
  sueldo?: string;
  tipoContratacion?: string;
  ubicacionTrabajo?: string;
  jefeDirecto?: string;
  /* --- preparación de ingreso (Onboarding, bloque 4) --- */
  contrato?: "Pendiente" | "Firmado";
  altaAdministrativa?: "Pendiente" | "Realizada";
  equipoAccesos?: "Pendiente" | "Listo" | "No aplica";
  pendientes?: string[];
  porRevisar?: string[];
  sinConfirmar?: string[];
  listoParaAlta?: boolean;
  /* puentes hacia el módulo 1 */
  candidatoId?: string;
  telefono?: string;
  correo?: string;
  vacanteId?: string;
  score?: number;
  entrevistaMatch?: number | null;
  entrevistaRecomendacion?: string | null;
  /* --- Bloque 2 (resumen de evaluación) --- */
  evaluacion?: {
    score: number;
    requisitosCumplidos: string[];
    brechas: string[];
    alertas: string[];
    evidencia: string;
  } | null;
  /* trazabilidad HITL */
  seleccionadoPor?: string;
  altaAutorizadaPor?: string;
  altaFecha?: string | null;
  creado?: string;
}

export const nuevosIngresos: NuevoIngreso[] = [
  {
    id: "N-501",
    nombre: "Emiliano Cruz Vega",
    puesto: "Auxiliar de almacén",
    ubicacion: "Monterrey, NL",
    ingreso: "Ingresa el 22 jul",
    progreso: 80,
    tono: 2,
    documentos: [
      { nombre: "INE", estado: "recibido" },
      { nombre: "CURP", estado: "recibido" },
      { nombre: "RFC", estado: "recibido" },
      { nombre: "Comprobante de domicilio", estado: "pendiente" },
      { nombre: "NSS", estado: "revision" },
    ],
  },
  {
    id: "N-502",
    nombre: "Gabriela Ruiz Ponce",
    puesto: "Analista de nómina",
    ubicacion: "Querétaro, QRO",
    ingreso: "Ingresa el 24 jul",
    progreso: 60,
    tono: 1,
    documentos: [
      { nombre: "INE", estado: "recibido" },
      { nombre: "CURP", estado: "recibido" },
      { nombre: "RFC", estado: "revision" },
      { nombre: "Comprobante de domicilio", estado: "pendiente" },
      { nombre: "Título profesional", estado: "pendiente" },
    ],
  },
  {
    id: "N-503",
    nombre: "Luis Ángel Torres",
    puesto: "Desarrollador Full-Stack",
    ubicacion: "Remoto (MX)",
    ingreso: "Ingresa el 1 ago",
    progreso: 100,
    tono: 2,
    documentos: [
      { nombre: "INE", estado: "recibido" },
      { nombre: "CURP", estado: "recibido" },
      { nombre: "RFC", estado: "recibido" },
      { nombre: "Comprobante de domicilio", estado: "recibido" },
      { nombre: "NSS", estado: "recibido" },
    ],
  },
];

/* -------------------- Capacitación (3.14) --------------------
   Todo lo de este módulo ya es real: `Curso`/`cursos` viven en lib/api.ts
   (fetchCursos()/fetchCurso()), y los 4 KPIs del dashboard vienen de
   fetchCapacitacionKpis() (GET /capacitacion/kpis) — ver
   app/dashboard/capacitacion/page.tsx. Nada mockeado queda en este archivo. */

/* -------------------- Desempeño (3.15) -------------------- */
export const competencias = [
  { competencia: "Conocimiento técnico", valor: 82, meta: 80 },
  { competencia: "Comunicación", valor: 74, meta: 75 },
  { competencia: "Trabajo en equipo", valor: 88, meta: 80 },
  { competencia: "Orientación a resultados", valor: 79, meta: 85 },
  { competencia: "Adaptabilidad", valor: 71, meta: 70 },
  { competencia: "Servicio al cliente", valor: 85, meta: 80 },
];

export const kpisDesempeno = [
  { kpi: "Ventas vs meta", actual: 112, meta: 100, unidad: "%", tone: "good" },
  { kpi: "Satisfacción cliente (CSAT)", actual: 4.6, meta: 4.5, unidad: "/5", tone: "good" },
  { kpi: "Tiempo de atención", actual: 3.8, meta: 3.5, unidad: "min", tone: "warn" },
  { kpi: "Ausentismo", actual: 2.1, meta: 3.0, unidad: "%", tone: "good" },
];

export const equipoDesempeno = [
  { nombre: "Jorge Alberto Ramírez", puesto: "Ejecutivo de ventas", score: 92, tendencia: "up", tono: 0 },
  { nombre: "Gabriela Ruiz Ponce", puesto: "Analista de nómina", score: 87, tendencia: "up", tono: 1 },
  { nombre: "María Fernanda López", puesto: "Cajera de sucursal", score: 81, tendencia: "flat", tono: 1 },
  { nombre: "Emiliano Cruz Vega", puesto: "Auxiliar de almacén", score: 74, tendencia: "up", tono: 2 },
  { nombre: "Ana Sofía Herrera", puesto: "Auxiliar de almacén", score: 63, tendencia: "down", tono: 3 },
];

export const brechas = [
  { competencia: "Orientación a resultados", brecha: 6, accion: "Curso: técnicas de cierre de ventas" },
  { competencia: "Comunicación", brecha: 1, accion: "Taller de comunicación efectiva" },
];

/* -------------------- Clima (3.16) -------------------- */
export const climaScore = 78;

export const climaTrend = [
  { mes: "Feb", score: 71 },
  { mes: "Mar", score: 73 },
  { mes: "Abr", score: 70 },
  { mes: "May", score: 74 },
  { mes: "Jun", score: 76 },
  { mes: "Jul", score: 78 },
];

export const climaDimensiones = [
  { dim: "Ambiente de trabajo", valor: 84 },
  { dim: "Liderazgo", valor: 72 },
  { dim: "Reconocimiento", valor: 68 },
  { dim: "Balance vida-trabajo", valor: 75 },
  { dim: "Crecimiento", valor: 70 },
  { dim: "Compensación", valor: 66 },
];

export type NivelSenal = "alto" | "medio" | "bajo";

export interface SenalClima {
  id: string;
  colaborador: string;
  puesto: string;
  nivel: NivelSenal;
  senal: string;
  detectado: string;
  tono: number;
}

export const climaSenales: SenalClima[] = [
  { id: "S-01", colaborador: "Ana Sofía Herrera", puesto: "Auxiliar de almacén", nivel: "alto", senal: "Menciones repetidas de carga de trabajo y desmotivación en las últimas 2 conversaciones.", detectado: "hace 1 día", tono: 3 },
  { id: "S-02", colaborador: "Roberto Nava", puesto: "Ejecutivo de ventas", nivel: "medio", senal: "Baja en el tono positivo respecto al mes anterior; comentarios sobre reconocimiento.", detectado: "hace 3 días", tono: 0 },
  { id: "S-03", colaborador: "Diana Karen Méndez", puesto: "Cajera de sucursal", nivel: "bajo", senal: "Cambio leve en disponibilidad; sin señales de riesgo por ahora.", detectado: "hace 5 días", tono: 1 },
];

export const climaKpis = [
  { label: "Índice de clima", value: "78", tone: "good" },
  { label: "Participación en pulsos", value: "86%", tone: "brand" },
  { label: "Señales activas", value: "3", tone: "warn" },
  { label: "Rotación (mes)", value: "2.1%", tone: "good" },
];
