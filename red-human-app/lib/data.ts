/* ============================================================
   Datos de ejemplo — Red Human AI (Fase 1)
   Realistas para México. Sustituir por API real en producción.
   ============================================================ */

export type EstadoPrefiltro = "cumple" | "revision" | "no_cumple" | "pendiente";
export type FuenteCandidato = "Formulario" | "WhatsApp" | "OCC" | "LinkedIn" | "Indeed" | "RH";

/** Pipeline de CINCO columnas (2026-10-01): valores internos; en la interfaz se muestran con `nombreEtapa()`
 * → Prefiltro · Filtro Red Human · Filtro humano · Contratación · Onboarding. «Evaluación» (Evaluación integral)
 * dejó de ser columna: ahora es un resultado acumulado (`resultadoIntegral`). */
export type EtapaCandidato =
  | "Prefiltro"
  | "Entrevista IA"
  | "Entrevista Humana"
  | "Contratación"
  | "Onboarding";

/** 2026-10-01: Evaluación integral como RESULTADO (services/evaluacion_integral.py). */
export type EstadoIntegral = "apto" | "con_observaciones" | "no_apto" | "pendiente";
export interface ValidacionIntegral {
  clave: string;
  nombre: string;
  obligatoria: boolean;
  fuente: "red_human" | "persona";
  tipo?: string;
  estado: "aprobada" | "observaciones" | "no_apto" | "pendiente";
  resultado: string;
  detalle: string;
  score: number | null;
  /** «Revisado por: Red Human» · «Revisado por: [nombre]» · «Pendiente de revisión» */
  revisadoPor: string;
  codigo: string | null;
}
export interface ResultadoIntegral {
  estado: EstadoIntegral;
  texto: string;
  /** Score de lo que ya está calificado; null si nada lo está (un pendiente nunca vale cero). */
  score: number | null;
  scoreParcial: boolean;
  motivo: string;
  completadas: number;
  total: number;
  validaciones: ValidacionIntegral[];
}

/* ---- Proceso configurable (2026-10-06): configuración (plantilla de la Cuenta → vacante → postulación) ---- */
export interface ReglaPaso { tipo: "ninguna" | "calificacion" | "dictamen" | "validacion"; minimo?: number; aceptados?: string[] }
export interface ResponsablePaso { tipo: "red_human" | "rh" | "usuario" | "externo" | "candidato"; usuario_id?: number | null; nombre?: string }
export interface PasoProceso {
  id: string;
  tipo: string;
  nombre: string;
  etapa: EtapaCandidato;
  obligatorio: boolean;
  depende_de: string[];
  responsable: ResponsablePaso;
  regla: ReglaPaso;
  plazo_dias: number | null;
  tipo_entrevista?: string;
  orden?: number;
  heredado?: boolean;
}
export type EtapasProceso = Partial<Record<EtapaCandidato, { avance_automatico: boolean }>>;
export interface ProcesoConfig {
  plantilla_id?: number | null;
  plantilla_nombre?: string;
  plantilla_version?: number;
  version?: number;
  personalizado?: boolean;
  pasos: PasoProceso[];
  etapas: EtapasProceso;
}

/* ---- Proceso configurable y seguimiento (2026-10-06): lo calcula la API (services/proceso.py) ---- */
export type EstadoPaso = "pendiente" | "en_curso" | "completada" | "omitida" | "cancelada";
export type ResultadoPaso = "favorable" | "con_observaciones" | "no_favorable";
/** Acción de un paso: reutiliza lo que ya existe («Agregar evaluación», la tarjeta de la evaluación o una pestaña). */
export interface AccionPaso {
  clave: "iniciar_evaluacion" | "consultar_evaluacion" | "consultar" | "abrir";
  texto: string;
  evaluacion?: string;
  pestana?: "whatsapp" | "documentos" | "evaluaciones" | "contratacion";
}
export interface PasoSeguimiento {
  id: string;
  tipo: string;
  nombre: string;
  etapa: EtapaCandidato;
  etapaTexto: string;
  obligatorio: boolean;
  dependeDe: string[];
  reglaTexto: string;
  tipoEntrevista?: string | null;
  responsable: string;
  responsableConfig: { tipo?: string; usuario_id?: number | null; nombre?: string };
  estado: EstadoPaso;
  estadoTexto: string;
  resultado: ResultadoPaso | null;
  resultadoTexto: string;
  detalle: string;
  cumpleRegla: boolean;
  /** Qué falta exactamente («Falta comprobante de domicilio», «Falta completar: Psicométrica»…). */
  espera: string;
  disponible: boolean;
  revisadoPor: string;
  evaluacion: string | null;
  score?: number | null;
  plazoDias: number | null;
  fechaLimite: string | null;
  vencido: boolean;
  decision?: { por: string; motivo: string; fecha: string; autorizado_por?: string } | null;
  heredado: boolean;
  accion: AccionPaso | null;
}
export interface EtapaSeguimiento {
  etapa: EtapaCandidato;
  texto: string;
  actual: boolean;
  avanceAutomatico: boolean;
  sinPasos: boolean;
  lista: boolean;
  faltantes: string[];
  pasos: PasoSeguimiento[];
}
export interface SiguienteAccionProceso {
  tipo: "paso" | "avanzar" | "abrir" | "esperar" | "fin" | "cerrada";
  texto: string;
  detalle?: string;
  paso?: string;
  etapa?: EtapaCandidato;
  accion?: AccionPaso | null;
}
export interface SeguimientoProceso {
  tieneProceso: boolean;
  vacanteTieneProceso?: boolean;
  plantilla?: string;
  personalizado?: boolean;
  version?: number;
  desactualizado?: boolean;
  etapaActual?: EtapaCandidato;
  etapaTexto?: string;
  siguienteEtapa?: EtapaCandidato | null;
  siguienteEtapaTexto?: string | null;
  listaParaAvanzar?: boolean;
  siguienteAccion?: SiguienteAccionProceso;
  alertas?: { paso: string; texto: string; fechaLimite: string | null }[];
  etapas?: EtapaSeguimiento[];
  /** Canal Telegram (2026-10-06): deep links de la postulación y de cada paso (solo si la Cuenta atiende por Telegram). */
  telegram?: { disponible: boolean; liga: string; pasos: Record<string, string> };
}

export interface RespuestaPrefiltro {
  criterio?: string;
  pregunta?: string;
  respuesta?: string;
  cumple?: boolean | null;
}

export type TipoEntrevistador = "interno" | "externo";
/** Extracción del CV (services/ia.py::CVExtraido) — ver Punto 2/3.B. Todo es opcional: el
 * candidato puede no tener CV, o el análisis puede haber fallado (ver estadoAnalisisCv en
 * candidatos/page.tsx). */
export interface CvDatos {
  nombre?: string | null;
  correo?: string | null;
  telefono?: string | null;
  ubicacion?: string | null;
  anios_experiencia?: number | null;
  experiencia_resumen?: string;
  puesto_actual?: string | null;
  ultimo_empleo?: string | null;
  estudios?: string[];
  habilidades?: string[];
  idiomas?: string[];
  /** 3-5 líneas, más completo que experiencia_resumen (Punto 3.B). */
  resumen_profesional?: string;
  /** Solo cuando se extrajo con una vacante de referencia. */
  experiencia_relevante?: string | null;
  conocimientos_relevantes?: string[];
  datos_faltantes?: string[];
  alertas?: string[];
  es_cv?: boolean;
  [key: string]: unknown;
}

export interface Candidato {
  id: string;
  nombre: string;
  puesto: string;
  vacanteId: string;
  fuente: FuenteCandidato;
  estado: EstadoPrefiltro;
  etapa: EtapaCandidato;
  score: number; // 0-100 match con el perfil
  experiencia: string;
  ubicacion: string;
  aplicado: string; // fecha relativa
  tono: number;
  evidencia: string;
  /* --- presentes cuando vienen de la API --- */
  telefono?: string;
  correo?: string;
  consentimiento?: boolean;
  prefiltroCompleto?: boolean;
  esPrueba?: boolean;
  /* --- Fase 2: `id` es el código de la POSTULACIÓN (P-####), la tarjeta del Kanban; la
   * persona (C-####) viene en candidatoCodigo/candidato. --- */
  codigo?: string;
  postulacionId?: number;
  candidatoId?: string;
  candidatoCodigo?: string;
  vacanteTitulo?: string;
  origen?: string;
  totalPostulaciones?: number;
  yaAplicoAntes?: boolean;
  activa?: boolean;
  motivoCierre?: string | null;
  cerradaEn?: string | null;
  /** 2026-10-01: motivo que capturó RH al descartar (la tarjeta se queda en su columna con «No cumple»). */
  motivoDescarte?: string;
  resultadoIntegral?: ResultadoIntegral;
  /** Proceso configurable (2026-10-06): vista de seguimiento — solo en el detalle de la ficha. */
  proceso?: SeguimientoProceso;
  tieneProceso?: boolean;
  /** true si el WhatsApp de esta persona está conversando sobre ESTA postulación. */
  enConversacion?: boolean;
  candidato?: {
    id: string;
    codigo: string;
    nombre: string;
    correo: string;
    telefono: string;
    ubicacion: string;
    experiencia: string;
    fuente: FuenteCandidato;
    esPrueba: boolean;
    totalPostulaciones: number;
    postulacionesActivas: number;
    archivos: number;
    creadoEn?: string | null;
  };
  /** Solo en el detalle: las OTRAS postulaciones de la misma persona (más reciente primero). */
  historialPostulaciones?: {
    id: string;
    puesto: string;
    vacanteId: string;
    etapa: EtapaCandidato;
    estado: EstadoPrefiltro;
    score: number;
    activa: boolean;
    motivoCierre: string;
    creado: string;
    creadoEn?: string | null;
    cerradaEn?: string | null;
  }[];
  /* Evaluaciones unificadas (2026-09-29): las evaluaciones (entrevista humana incluida) ya no viajan en la ficha;
   * se piden a GET /evaluaciones/postulaciones/{codigo} (lib/api.fetchEvaluaciones). */
  /* puentes hacia los otros módulos */
  expedienteId?: number | null;
  expedienteProgreso?: number | null;
  /** 2026-09-18: empresa que ve el candidato (Cliente o nombre comercial de la Cuenta) — vista previa de correos. */
  empresaVisible?: string;
  /** 2026-09-17: nivel (1-3) del próximo recordatorio de documentos y cuántos van. */
  recordatorioNivel?: 1 | 2 | 3 | null;
  recordatoriosEnviados?: number | null;
  expedienteEstado?: string | null;
  expedienteCondiciones?: {
    puesto: string;
    sueldo: string;
    tipoContratacion: string;
    ubicacion: string;
    jefeDirecto: string;
    fechaIngreso: string | null;
    /** Fase 5: instrucciones del primer día (van en la bienvenida automática al alta). */
    instruccionesIngreso?: string;
    /** 2026-09-19 (Bloque 3): empresa contratante y estado de captura. */
    empresa?: string;
    /** 2026-09-20 (B2): vigencia de «Tiempo determinado» (duración capturada, término calculado por el servidor). */
    duracionContrato?: number | null;
    duracionUnidad?: string;
    fechaTermino?: string | null;
    guardadasEn?: string | null;
    completas?: boolean;
  } | null;
  entrevistaId?: string | null;
  entrevistaEstado?: string | null;
  entrevistaMatch?: number | null;
  entrevistaRecomendacion?: string | null;
  archivos?: number;
  mensajes?: number;
  /* --- Fase C: actividad, resultado vigente y cliente de la vacante --- */
  /** Fecha ISO de última actividad (cambio de etapa, evaluación, mensaje, etc.). Null si no hay
   * actividad registrada desde el deploy de Fase C (usar aplicado como fallback). */
  ultimaActividadEn?: string | null;
  /** True=Apto, False=No apto, null=sin evaluación todavía.
   * La regla "el más reciente gana" se aplica en el backend (_recalcular_resultado_apto). */
  resultadoApto?: boolean | null;
  /** Nombre del Cliente de la vacante del candidato, si aplica. Null si no tiene vacante o
   * la vacante no tiene Cliente. Permite la columna "Cliente" en la vista lista sin JOIN extra. */
  clienteVacante?: string | null;
  /** Fase 7A: id del Cliente de la vacante (contactos para entrevistador externo / notificar). */
  clienteIdVacante?: number | null;
  /* --- Puntos 3/5: síntesis global (CV + Prefiltro + Entrevista IA + Entrevista Humana),
   * calculada al vuelo en cada lectura del detalle — nunca se persiste, siempre está al día. --- */
  /** Prefiltro = SOLO status de entrada (cumple / no_cumple); no participa en la evaluación integral. */
  prefiltroResumen?: { cumple: number; total: number; incumplidos: string[]; resultado?: "cumple" | "no_cumple" | null } | null;
  /** 2026-09-16: prefiltro dual y control manual */
  respuestasWeb?: { pregunta: string; respuesta: string }[];
  inconsistencias?: { criterio: string; pregunta: string; web: string; whatsapp: string; detectada_en: string; aclarada: boolean; aclaracion: string }[];
  actividadesOmitidas?: { actividad: string; etapa: string; usuario: string; fecha: string; motivo: string; hacia: string }[];
  /** 2026-09-22: notas del historial del expediente (decisiones humanas). Solo se agregan, nunca se borran. */
  historial?: { evento: string; texto: string; usuario: string; fecha: string; desde?: string; hacia?: string; motivo?: string }[];
  /** Capacitación universal: cursos de filtro cursados (resultado en la evaluación del candidato). */
  capacitacion?: { curso: string; titulo: string; calificacion: number; aprobado: boolean; fecha: string; asignacion: string }[];
  /** 2026-09-13: status de la Entrevista Red Human (bloque propio). */
  entrevistaStatus?: {
    codigo: string;
    estado: string;
    cierre: string;
    motivo: string;
    turnosCandidato: number;
    faltante: string[];
    motivoIa: string;
    intentosPrevios: number;
    accionSiguiente: "reintentar" | null;
    turnosUtiles?: number;
  } | null;
  /** true solo cuando la Evaluación Integral (CV + Entrevista Red Human válida) existe. */
  evaluacionIntegral?: boolean;
  afinidadGlobal?: number | null;
  sintesisAfinidad?: string;
  fortalezasPrincipales?: string[];
  puntosPorValidar?: string[];
  recomendacionRedHuman?: "No avanzar" | "Realizar entrevista humana" | "Realizar Entrevista Red Human" | "Reintentar Entrevista Red Human" | "Avanzar a contratación" | null;
  recomendacionMotivo?: string;
  /* solo en el detalle (GET /candidatos/{codigo}) */
  cvDatos?: CvDatos;
  analisis?: {
    origen?: string;
    ia?: boolean;
    requisitos_cumplidos?: string[];
    brechas?: string[];
    /* 2026-09-13: bloque «Análisis de CV» */
    fortalezas_cv?: string[];
    compatibilidad_cv?: string;
    experiencia_relevante_cv?: string;
    prefiltro_resultado?: "cumple" | "no_cumple";
    prefiltro_evidencia?: string;
    alertas?: string[];
    datos_faltantes?: string[];
    respuestas_prefiltro?: RespuestaPrefiltro[];
    [key: string]: unknown;
  };
  listaArchivos?: {
    id: number;
    tipo: string;
    nombre: string;
    mime: string;
    tamano: number;
    estado: "recibido" | "revision" | "rechazado";
    notas: string;
    subidoPor: string;
    subido: string;
  }[];
  vacante?: { id: string; titulo: string; requisitos: string; preguntas: string[] } | null;
  entrevistas?: { id: string; estado: string; tipo: string; token: string; evaluacion: unknown; creada: string }[];
  consentimientoFecha?: string | null;
}

export interface BloquePublicacion {
  titulo: string;
  copy: string;
  page: string;
  etiquetas: string[];
}

export interface Vacante {
  id: string;
  titulo: string;
  area: string;
  empresa: string;
  ubicacion: string;
  modalidad: "Presencial" | "Híbrido" | "Remoto";
  sueldo: string;
  /** Parte 3: sueldo estructurado (el texto de arriba es el derivado que se muestra). */
  sueldoDesde?: number | null;
  sueldoHasta?: number | null;
  sueldoMoneda?: string;
  sueldoPeriodicidad?: string;
  estado: "Publicada" | "Borrador" | "En revisión" | "Cerrada" | "Eliminada";
  candidatos: number;
  nuevos: number;
  publicada: string;
  plataformas: string[];
  /* --- presentes cuando vienen de la API --- */
  slug?: string;
  descripcion?: string;
  requisitos?: string;
  preguntas_filtro?: string[];
  resumen?: string;
  perfilIdeal?: string;
  responsabilidades?: string[];
  requisitosDeseables?: string[];
  beneficios?: string[];
  palabrasClave?: string[];
  seniority?: string;
  avisosCumplimiento?: string[];
  publicaciones?: Record<string, BloquePublicacion>;
  textoWhatsapp?: string;
  textoBolsa?: string;
  criterios?: {
    pregunta: string;
    tipo: string;
    valida: string;
    respuesta_esperada: string;
    descarta: boolean;
    opciones?: string[];
  }[];
  /** Fase 4: prefiltro por WhatsApp independiente (vacío = usa `criterios`) y ubicación estructurada. */
  criteriosWhatsapp?: { pregunta: string; tipo: string; valida: string; respuesta_esperada: string; descarta: boolean; opciones?: string[] }[];
  ubicacionEstado?: string;
  ubicacionMunicipio?: string;
  /** Capacitación universal: curso que se asigna como filtro al quedar apto */
  cursoFiltroId?: string | null;
  /** Evaluaciones (2026-09-28): sugerencias de la vacante + aviso al enviar a Onboarding (nunca bloquea). */
  evaluacionesSugeridas?: { tipo: string; prueba_id: number | null; nombre: string }[];
  avisarEvaluacionesAntesOnboarding?: boolean;
  /** Proceso configurable (2026-10-06): copia personalizable del proceso de selección (vacío = sin proceso). */
  proceso?: ProcesoConfig | Record<string, never>;
  cursoFiltroTitulo?: string | null;
  /** 2026-09-17: Cuenta dueña (portal por Cuenta) y homónimas publicadas en otras Cuentas (detalle). */
  cuentaId?: number | null;
  cuentaSlug?: string;
  homonimasOtrasCuentas?: { codigo: string; cuenta: string; cuentaId: number }[];
  /** CRUD: baja lógica */
  eliminadaEn?: string | null;
  eliminadaPor?: string;
  embudo?: { etapas?: Record<string, number>; estados?: Record<string, number> };
  creada?: string;
  /** Fecha ISO de primera publicación. Null si la vacante nunca se ha publicado o existia
   * antes del deploy de Fase C y aún no ha pasado por publicar(). */
  publicadaEn?: string | null;
  actualizada?: string;
  /* --- Fase B: Cliente/Responsable/Colaboradores/visibilidad --- */
  cliente?: string | null;
  clienteId?: number | null;
  /** Fase 4 (Punto 6): enfoque de la Entrevista IA. */
  enfoqueEntrevista?: "profesional" | "profesional_personal";
  responsable?: string | null;
  colaboradores?: string[];
  mostrarClienteCandidato?: boolean;
  /** nombre que ve el candidato — ya resuelto por el backend (Cliente si aplica y está visible,
   * si no el nombre de la Cuenta). Úsalo en cualquier vista candidato-visible en vez de `empresa`. */
  nombreEmpresa?: string;
  /** solo presente en /vacantes/slug/{slug}, /vacantes/publicas y /vacantes/{codigo}/vista-previa */
  logoUrl?: string;
}






export const vacantes: Vacante[] = [
  {
    id: "VAC-1042",
    titulo: "Cajero(a) de sucursal",
    area: "Operaciones",
    empresa: "Grupo Carbe",
    ubicacion: "Guadalajara, JAL",
    modalidad: "Presencial",
    sueldo: "$9,500 – 11,000",
    estado: "Publicada",
    candidatos: 184,
    nuevos: 12,
    publicada: "hace 3 días",
    plataformas: ["WhatsApp", "OCC", "Portal"],
  },
  {
    id: "VAC-1041",
    titulo: "Ejecutivo(a) de ventas telefónicas",
    area: "Comercial",
    empresa: "Grupo Carbe",
    ubicacion: "CDMX",
    modalidad: "Híbrido",
    sueldo: "$12,000 + comisiones",
    estado: "Publicada",
    candidatos: 246,
    nuevos: 28,
    publicada: "hace 5 días",
    plataformas: ["WhatsApp", "LinkedIn", "Indeed"],
  },
  {
    id: "VAC-1040",
    titulo: "Auxiliar de almacén",
    area: "Logística",
    empresa: "Distribuidora Norte",
    ubicacion: "Monterrey, NL",
    modalidad: "Presencial",
    sueldo: "$8,800 – 10,200",
    estado: "Publicada",
    candidatos: 132,
    nuevos: 7,
    publicada: "hace 1 semana",
    plataformas: ["WhatsApp", "OCC"],
  },
  {
    id: "VAC-1039",
    titulo: "Analista de nómina",
    area: "Recursos Humanos",
    empresa: "Grupo Carbe",
    ubicacion: "Querétaro, QRO",
    modalidad: "Híbrido",
    sueldo: "$18,000 – 22,000",
    estado: "En revisión",
    candidatos: 41,
    nuevos: 4,
    publicada: "borrador",
    plataformas: ["Portal"],
  },
  {
    id: "VAC-1038",
    titulo: "Desarrollador(a) Full-Stack",
    area: "Tecnología",
    empresa: "Grupo Carbe",
    ubicacion: "Remoto (MX)",
    modalidad: "Remoto",
    sueldo: "$45,000 – 60,000",
    estado: "Publicada",
    candidatos: 89,
    nuevos: 9,
    publicada: "hace 2 días",
    plataformas: ["LinkedIn", "Portal"],
  },
  {
    id: "VAC-1037",
    titulo: "Supervisor(a) de piso",
    area: "Operaciones",
    empresa: "Retail Bajío",
    ubicacion: "León, GTO",
    modalidad: "Presencial",
    sueldo: "$16,500 – 19,000",
    estado: "Borrador",
    candidatos: 0,
    nuevos: 0,
    publicada: "borrador",
    plataformas: [],
  },
];


export const knowledgeBase = [
  { id: "KB-01", titulo: "Solicitud de vacaciones", categoria: "Prestaciones", vigencia: "v3 · vigente", accesos: 1284 },
  { id: "KB-02", titulo: "Proceso de nómina y fechas de pago", categoria: "Nómina", vigencia: "v5 · vigente", accesos: 2140 },
  { id: "KB-03", titulo: "Permisos y ausencias justificadas", categoria: "Políticas", vigencia: "v2 · vigente", accesos: 863 },
  { id: "KB-04", titulo: "Prestaciones superiores a la ley", categoria: "Prestaciones", vigencia: "v4 · vigente", accesos: 1547 },
  { id: "KB-05", titulo: "Reglamento interior de trabajo", categoria: "Políticas", vigencia: "v1 · vigente", accesos: 692 },
  { id: "KB-06", titulo: "Alta en el IMSS y seguro de gastos", categoria: "Prestaciones", vigencia: "v2 · vigente", accesos: 1103 },
];

export const chatSugerencias = [
  "¿Cuántos días de vacaciones me tocan este año?",
  "¿Cuándo es el próximo día de pago?",
  "¿Cómo solicito un permiso por asuntos personales?",
  "¿Qué prestaciones superiores a la ley tengo?",
];

export const conversacionDemo = [
  {
    rol: "user" as const,
    texto: "Hola, quiero saber cuántos días de vacaciones me corresponden. Entré a la empresa en marzo de 2023.",
  },
  {
    rol: "assistant" as const,
    texto:
      "¡Hola! Con gusto te ayudo. Ingresaste en marzo de 2023, así que llevas 2 años cumplidos. Conforme a la reforma de la Ley Federal del Trabajo, en tu segundo año cumplido te corresponden 14 días de vacaciones, y para tu tercer año subirá a 16 días.",
    fuente: "KB-01 · Solicitud de vacaciones (v3)",
  },
  {
    rol: "user" as const,
    texto: "Perfecto. ¿Y cómo las solicito?",
  },
  {
    rol: "assistant" as const,
    texto:
      "Puedes solicitarlas directamente por aquí: dime las fechas que quieres y genero tu solicitud con folio para que tu jefe(a) la autorice. También puedo revisar tu saldo actual de días disponibles antes de continuar. ¿Qué fechas tienes en mente?",
  },
];
