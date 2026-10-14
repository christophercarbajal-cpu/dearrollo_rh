"use client";

/* Punto 11 — UN solo formulario de contenido para "Nueva vacante" y "Nueva plantilla".

   Parte 3 (2026-09-12) — estructura final, en este orden:
     1. Datos principales (captura ANTES de generar): Puesto → Área → Seniority → [Cliente, solo en
        Vacantes, vía `slotDatosPrincipales`] → Ubicación → Modalidad → Sueldo (Desde/Hasta/Moneda/
        Periodicidad o A convenir).
     2. Guía opcional para Red Human: Descripción breve → Indispensables → Deseables → Prestaciones.
     3. Un solo botón «Generar vacante con Red Human», ABAJO de lo capturable. 2026-09-16: lo que la IA
        genera (responsabilidades, prefiltros web/WhatsApp, perfil ideal, textos) vive en el acordeón
        «Configuración avanzada», CERRADO por defecto — si no lo abren, la IA genera todo al presionar.
     4. Tras generar, (2) se vuelve «Contenido generado por Red Human (editable)»: Descripción
        completa → Responsabilidades → Indispensables → Deseables → Prestaciones — mismos campos,
        siempre visibles y editables.
     5. Selección: Prefiltro (criterios con eliminatorias) → Entrevista Red Human (2 enfoques).
     Vacantes agrega después Gestión (Responsable → Colaboradores) y Publicación; Plantillas agrega
     encima Nombre/Alcance.

   2026-10-09 (generación según la ruta) — en Vacantes el orden es: Datos principales → Sobre la vacante → Proceso de
   selección (`slotProceso`) → Generar → Revisar («Plantillas de conversación»: SOLO las secciones de las actividades de
   la ruta, editables) → Guardar. «Volver a generar» nunca pisa ediciones de RH sin preguntar; los avisos de
   cumplimiento salen UNA vez (en el bloque de Generar) y desaparecen al capturar el dato.

   Cambio 37 (2026-10-14): UNA sola lista «Preguntas filtro» (reemplaza Prefiltro · Web, Prefiltro · WhatsApp y «Cómo
   pregunta Red Human cada requisito»), debajo de los indispensables; los indispensables que propone Red Human llevan la
   etiqueta «Propuesto por Red Human»; «Falta para publicar» solo aparece tras «Generar con Red Human» o al intentar
   publicar (`intentoPublicar`).

   Dos reglas NO NEGOCIABLES (también garantizadas en el servidor, ia._asegurar_capturado):
   - Red Human no inventa condiciones reales (sueldo, periodicidad, ubicación, modalidad, horario,
     prestaciones): lo que RH no capturó queda vacío/pendiente, nunca rellenado.
   - Red Human respeta lo capturado: indispensable sigue indispensable, deseable sigue deseable;
     solo complementa lo vacío. `contenidoDesdeGenerado` aplica exactamente eso del lado del cliente. */

import { useEffect, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronUp, MessageSquareText, Sparkles, X } from "lucide-react";
import { Button, Eyebrow } from "@/components/ui";
import { Area, CampoSueldo, Field, ListaEditable, Selector } from "@/components/dashboard/campos";
import { ESTADOS_MX, municipiosDe, parsearUbicacion, textoUbicacion } from "@/lib/ubicacion";
import type { EtapaCandidato, PasoProceso, Vacante } from "@/lib/data";
import { PerfilOperativoCampo, rutaMasiva } from "@/components/dashboard/vacantes/perfil-operativo";
import { EntrevistaWhatsappBase } from "@/components/dashboard/vacantes/entrevista-whatsapp-base";
import {
  ETAPAS_PIPELINE,
  nombreEtapa,
  generarVacanteIA,
  ENFOQUES_ENTREVISTA,
  ORDEN_GUIONES,
  TITULOS_GUION,
  type ClaveGuion,
  type GuionConversacion,
  type MetaGuion,
  type ProcesoEntrada,
  type PreguntaRequisito,
  type PreguntaFiltroU,
  TIPOS_PREGUNTA_FILTRO,
  type EntrevistaBaseConfig,
  MONEDAS_SUELDO,
  PERIODICIDADES_SUELDO,
  SENIORITIES,
  type CriterioFiltro,
  type EnfoqueEntrevista,
  type PeriodicidadSueldo,
  type Plantilla,
  type VacanteGenerada,
  type PerfilOperativo,
  type DocumentoSolicitado,
  DOCUMENTOS_SUGERIDOS_VACANTE,
} from "@/lib/api";
import { cn } from "@/lib/utils";

export interface ContenidoVacante {
  /* --- 1. datos principales --- */
  titulo: string;
  area: string;
  seniority: string;
  /** Texto derivado «Municipio, Estado» (lo leen portal/WhatsApp/IA); se conserva libre en vacantes previas. */
  ubicacion: string;
  /* Fase 4: ubicación estructurada — selectores anidados Estado → Municipio/Alcaldía */
  ubicacion_estado: string;
  ubicacion_municipio: string;
  modalidad: string;
  sueldo_desde: string; // texto numérico del input; vacío = sin dato
  sueldo_hasta: string;
  sueldo_moneda: string;
  sueldo_periodicidad: PeriodicidadSueldo | "";
  /* --- 2/4. guía → contenido generado (mismos campos) --- */
  descripcion: string;
  responsabilidades: string[];
  requisitos: string[]; // indispensables (lista); viaja al servidor unida por « · »
  requisitos_deseables: string[];
  beneficios: string[];
  /** Cambio 25: «Documentos y fotos que se piden». Con elementos, la ruta suma «Documentos» después del prefiltro. */
  documentos_solicitados: DocumentoSolicitado[];
  /** Cambio 31: pregunta natural por requisito indispensable (la propone «Generar»; RH la puede ajustar). */
  preguntas_requisitos: PreguntaRequisito[];
  /** Cambio 37: «Preguntas filtro» — la entidad única (una por indispensable + las base). */
  filtro: PreguntaFiltroU[];
  /** Cambio 37: indispensables que propuso Red Human (no los capturó RH). */
  indispensables_propuestos: string[];
  /* --- 5. selección --- */
  /** Preguntas del prefiltro de la POSTULACIÓN WEB (/aplicar). */
  preguntas_filtro: CriterioFiltro[];
  /** Fase 4: preguntas del prefiltro por WHATSAPP — independientes y editables. Vacío = el agente usa las de la web. */
  preguntas_filtro_whatsapp: CriterioFiltro[];
  /** Fase 4 (Punto 6): enfoque de la Entrevista Red Human — solo 2 niveles. */
  enfoque_entrevista: EnfoqueEntrevista;
  /* --- avanzado (los llena la IA; editables pero colapsados) --- */
  resumen: string;
  perfil_ideal: string;
  palabras_clave: string[];
  avisos_cumplimiento: string[];
  texto_whatsapp: string;
  texto_bolsa: string;
  /* --- 2026-10-09: plantillas de conversación de ESTA vacante (solo Vacantes) --- */
  guiones: GuionesForm;
}

/** Guiones de entrevista/llamada + trazabilidad; los prefiltros viven en preguntas_filtro / preguntas_filtro_whatsapp. */
export interface GuionesForm {
  secciones: Partial<Record<ClaveGuion, GuionConversacion>>;
  meta: Record<string, MetaGuion>;
  /** Secciones de las actividades de la ruta (vacío = aún no se genera). */
  aplican: ClaveGuion[];
  /** Secciones con ediciones de RH (no se regeneran sin preguntar). */
  editadas: ClaveGuion[];
  /** La API dice que los datos de la vacante cambiaron después de generar. */
  desactualizado: boolean;
  /** 2026-10-10 (enfoque operativo): oficio de la Biblioteca ajustado SOLO para esta vacante; null = se detecta por el puesto. */
  perfil_operativo?: PerfilOperativo | null;
  /** Nombre del oficio que detectó la API (cuando no hay perfil propio). */
  perfil_detectado?: string;
  /** Cambio 29: entrevista fija por WhatsApp de Masivos — preguntas base quitadas y propias de RH (vacío = «Base»). */
  entrevista_base?: EntrevistaBaseConfig;
}

export const GUIONES_VACIOS: GuionesForm = { secciones: {}, meta: {}, aplican: [], editadas: [], desactualizado: false, perfil_operativo: null };

export const CONTENIDO_VACIO: ContenidoVacante = {
  titulo: "",
  area: "",
  seniority: "",
  ubicacion: "",
  ubicacion_estado: "",
  ubicacion_municipio: "",
  modalidad: "Presencial",
  sueldo_desde: "",
  sueldo_hasta: "",
  sueldo_moneda: "MXN",
  sueldo_periodicidad: "",
  descripcion: "",
  responsabilidades: [],
  requisitos: [],
  requisitos_deseables: [],
  beneficios: [],
  documentos_solicitados: [],
  preguntas_requisitos: [],
  filtro: [],
  indispensables_propuestos: [],
  preguntas_filtro: [],
  preguntas_filtro_whatsapp: [],
  enfoque_entrevista: "profesional",
  resumen: "",
  perfil_ideal: "",
  palabras_clave: [],
  avisos_cumplimiento: [],
  texto_whatsapp: "",
  texto_bolsa: "",
  guiones: GUIONES_VACIOS,
};

export const MODALIDADES = ["Presencial", "Híbrido", "Remoto"];

const SEPARADOR_REQUISITOS = " · ";

/** `requisitos` legado es texto separado por « · » (o saltos/;) → lista de indispensables. */
export function requisitosLista(texto: string | undefined): string[] {
  return (texto ?? "")
    .split(/\s*·\s*|;|\n/)
    .map((x) => x.replace(/^[\s.\-•]+|[\s.\-•]+$/g, ""))
    .filter(Boolean);
}

const clave = (t: string) =>
  t
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .trim();

/** Capturados primero y literal; después lo generado que no repita ni esté en `excluir`. */
function unirCapturado(capturados: string[], generados: string[] | undefined, excluir: string[] = []): string[] {
  const vistos = new Set(capturados.map(clave).filter(Boolean));
  const prohibidos = new Set(excluir.map(clave).filter(Boolean));
  const salida = capturados.filter((x) => x.trim());
  for (const g of generados ?? []) {
    const k = clave(g);
    if (!k || vistos.has(k) || prohibidos.has(k)) continue;
    vistos.add(k);
    salida.push(g.trim());
  }
  return salida;
}

/** Precarga TODOS los campos compartidos desde una plantilla. */
/** Estado/Municipio guardados o, para registros previos con solo texto libre, lo que se reconozca. */
export function ubicacionEstructurada(estado: string | undefined, municipio: string | undefined, libre: string | undefined) {
  if (estado) return { ubicacion_estado: estado, ubicacion_municipio: municipio ?? "" };
  const p = parsearUbicacion(libre ?? "");
  return { ubicacion_estado: p.estado, ubicacion_municipio: p.municipio };
}

export function contenidoDesdePlantilla(p: Plantilla): ContenidoVacante {
  return {
    titulo: p.titulo,
    area: p.area,
    seniority: p.seniority ?? "",
    ubicacion: p.ubicacion ?? "",
    ...ubicacionEstructurada(p.ubicacionEstado, p.ubicacionMunicipio, p.ubicacion),
    modalidad: p.modalidad || "Presencial",
    sueldo_desde: p.sueldoDesde ? String(p.sueldoDesde) : "",
    sueldo_hasta: p.sueldoHasta ? String(p.sueldoHasta) : "",
    sueldo_moneda: p.sueldoMoneda || "MXN",
    sueldo_periodicidad: p.sueldoPeriodicidad ?? "",
    descripcion: p.descripcion,
    responsabilidades: [...(p.responsabilidades ?? [])],
    requisitos: requisitosLista(p.requisitos),
    requisitos_deseables: [...(p.requisitosDeseables ?? [])],
    beneficios: [...(p.beneficios ?? [])],
    documentos_solicitados: [...(p.documentosSolicitados ?? [])],
    preguntas_requisitos: [],
    filtro: [],
    indispensables_propuestos: [],
    preguntas_filtro: [...(p.preguntasFiltro ?? [])],
    preguntas_filtro_whatsapp: [...(p.preguntasFiltroWhatsapp ?? [])],
    enfoque_entrevista: p.enfoqueEntrevista ?? "profesional",
    resumen: p.resumen,
    perfil_ideal: p.perfilIdeal,
    palabras_clave: [...(p.palabrasClave ?? [])],
    avisos_cumplimiento: [...(p.avisosCumplimiento ?? [])],
    texto_whatsapp: p.textoWhatsapp,
    texto_bolsa: p.textoBolsa,
    guiones: GUIONES_VACIOS, // la plantilla de vacante no lleva guiones: son de cada vacante
  };
}

/** Guiones de una vacante guardada (detalle para RH). */
export function guionesDesdeVacante(v: Vacante): GuionesForm {
  const g = v.guiones;
  if (!g) return GUIONES_VACIOS;
  const secciones: Partial<Record<ClaveGuion, GuionConversacion>> = {};
  for (const s of g.secciones) {
    if (s.clase === "guion" && !s.vacia) secciones[s.clave] = s.contenido as GuionConversacion;
  }
  return {
    secciones,
    meta: g.meta ?? {},
    aplican: g.aplican ?? [],
    editadas: g.secciones.filter((s) => s.editado).map((s) => s.clave),
    desactualizado: Boolean(g.desactualizado),
    perfil_operativo: g.perfilOperativoPropio && g.perfilOperativo ? perfilForm(g.perfilOperativo) : null,
    perfil_detectado: g.perfilOperativo?.nombre ?? "",
    entrevista_base: { quitadas: g.entrevistaWhatsappBase?.quitadas ?? [], propias: g.entrevistaWhatsappBase?.propias ?? [] },
  };
}

/** Lo que RH ajusta del perfil del oficio para ESTA vacante: el oficio, sus 3 datos y la situación (lo esperado se conserva). */
export function perfilForm(p: PerfilOperativo): PerfilOperativo {
  return { oficio: p.oficio, nombre: p.nombre, datos: [...(p.datos ?? [])].slice(0, 3), situacion: { pregunta: p.situacion?.pregunta ?? "" } };
}

/** CRUD (2026-09-15): el formulario compartido en MODO EDICIÓN — se puebla con la vacante existente
 * (todos los campos, incluidos prefiltro web/WhatsApp y ubicación estructurada). */
export function contenidoDesdeVacante(v: Vacante): ContenidoVacante {
  return {
    titulo: v.titulo,
    area: v.area ?? "",
    seniority: v.seniority ?? "",
    ubicacion: v.ubicacion ?? "",
    ...ubicacionEstructurada(v.ubicacionEstado, v.ubicacionMunicipio, v.ubicacion),
    modalidad: v.modalidad || "Presencial",
    sueldo_desde: v.sueldoDesde ? String(v.sueldoDesde) : "",
    sueldo_hasta: v.sueldoHasta ? String(v.sueldoHasta) : "",
    sueldo_moneda: v.sueldoMoneda || "MXN",
    sueldo_periodicidad: (v.sueldoPeriodicidad as PeriodicidadSueldo | undefined) ?? "",
    descripcion: v.descripcion ?? "",
    responsabilidades: [...(v.responsabilidades ?? [])],
    requisitos: requisitosLista(v.requisitos),
    requisitos_deseables: [...(v.requisitosDeseables ?? [])],
    beneficios: [...(v.beneficios ?? [])],
    documentos_solicitados: [...(v.documentosSolicitados ?? [])],
    preguntas_requisitos: [...(v.preguntasRequisitos ?? [])],
    filtro: [...(v.filtro ?? [])],
    indispensables_propuestos: [...(v.indispensablesPropuestos ?? [])],
    preguntas_filtro: [...((v.criterios ?? []) as CriterioFiltro[])],
    preguntas_filtro_whatsapp: [...((v.criteriosWhatsapp ?? []) as CriterioFiltro[])],
    enfoque_entrevista: v.enfoqueEntrevista ?? "profesional",
    resumen: v.resumen ?? "",
    perfil_ideal: v.perfilIdeal ?? "",
    palabras_clave: [...(v.palabrasClave ?? [])],
    avisos_cumplimiento: [...(v.avisosCumplimiento ?? [])],
    texto_whatsapp: v.textoWhatsapp ?? "",
    texto_bolsa: v.textoBolsa ?? "",
    guiones: guionesDesdeVacante(v),
  };
}

/** Vuelca lo que generó Red Human sobre el contenido actual RESPETANDO lo capturado: nunca toca
 * sueldo, seniority ni prestaciones; indispensables/deseables capturados quedan literal, en su
 * categoría y primero; la descripción breve se expande (decisión 3); lo demás solo rellena vacíos. */
export function contenidoDesdeGenerado(base: ContenidoVacante, g: VacanteGenerada): ContenidoVacante {
  return {
    ...base,
    descripcion: g.descripcion || base.descripcion,
    responsabilidades: base.responsabilidades.length ? base.responsabilidades : g.responsabilidades ?? [],
    requisitos: unirCapturado(base.requisitos, g.requisitos_indispensables, base.requisitos_deseables),
    requisitos_deseables: unirCapturado(base.requisitos_deseables, g.requisitos_deseables, base.requisitos),
    beneficios: base.beneficios, // regla 4: solo lo capturado por RH
    // Cambio 25: lo capturado manda; si no hay, los documentos que menciona la descripción
    documentos_solicitados: base.documentos_solicitados.length ? base.documentos_solicitados : g.documentos_solicitados ?? [],
    preguntas_requisitos: g.preguntas_requisitos ?? base.preguntas_requisitos,
    filtro: g.filtro ?? base.filtro,
    indispensables_propuestos: g.indispensables_propuestos ?? base.indispensables_propuestos,
    preguntas_filtro: base.preguntas_filtro.length ? base.preguntas_filtro : g.preguntas_filtro ?? [],
    preguntas_filtro_whatsapp: base.preguntas_filtro_whatsapp.length ? base.preguntas_filtro_whatsapp : g.preguntas_filtro_whatsapp ?? [],
    resumen: base.resumen || g.resumen,
    perfil_ideal: base.perfil_ideal || g.perfil_ideal,
    palabras_clave: base.palabras_clave.length ? base.palabras_clave : g.palabras_clave ?? [],
    avisos_cumplimiento: g.avisos_cumplimiento ?? [],
    texto_whatsapp: base.texto_whatsapp || (g.texto_whatsapp ?? ""),
    texto_bolsa: base.texto_bolsa || (g.portal?.page ?? ""),
  };
}

/** 2026-10-09: tras «Generar», los prefiltros y guiones son EXACTAMENTE los de la ruta (la API regresa tal cual lo que
 * se pidió conservar). Lo que no está en la ruta queda vacío. */
export function aplicarGuionesGenerados(base: ContenidoVacante, g: VacanteGenerada): ContenidoVacante {
  const gg = g.guiones;
  if (!gg) return base;
  const conservadas = new Set(gg.conservadas ?? []);
  const secciones: Partial<Record<ClaveGuion, GuionConversacion>> = {};
  for (const clave of gg.aplican) {
    if (clave === "prefiltro_web" || clave === "prefiltro_whatsapp") continue;
    const nueva = gg.secciones[clave] ?? (conservadas.has(clave) ? base.guiones.secciones[clave] : undefined);
    if (nueva) secciones[clave] = nueva;
  }
  return {
    ...base,
    preguntas_filtro: g.preguntas_filtro ?? [],
    preguntas_filtro_whatsapp: g.preguntas_filtro_whatsapp ?? [],
    guiones: {
      secciones,
      meta: { ...base.guiones.meta, ...gg.meta },
      aplican: gg.aplican,
      editadas: base.guiones.editadas.filter((k) => conservadas.has(k)),
      desactualizado: false,
      perfil_operativo: base.guiones.perfil_operativo ?? null,
      perfil_detectado: base.guiones.perfil_detectado,
      entrevista_base: base.guiones.entrevista_base,
    },
  };
}

/** Avisos de cumplimiento UNA sola vez y solo los vigentes: un dato ya capturado (sueldo, ubicación, prestaciones) borra
 * su aviso. Misma regla que el servidor (`ia.avisos_vigentes`). */
export function avisosVigentes(c: ContenidoVacante): string[] {
  const capturado: Record<string, boolean> = {
    sueldo: Boolean(c.sueldo_periodicidad && c.sueldo_periodicidad !== "a_convenir" && c.sueldo_desde),
    ubicacion: Boolean(c.ubicacion_estado || c.ubicacion.trim()),
    prestaciones: c.beneficios.some((b) => b.trim()),
  };
  const temas: Record<string, string[]> = { sueldo: ["sueldo", "salario", "a convenir"], ubicacion: ["ubicacion"], prestaciones: ["prestacion", "beneficio"] };
  const pendiente = ["no captur", "pendiente", "confirmar", "falta", "sin dato", "no se invent", "no especific"];
  const vistos = new Set<string>();
  const usados = new Set<string>();
  const salida: string[] = [];
  for (const a of c.avisos_cumplimiento) {
    const t = a.replace(/\s+/g, " ").trim();
    const k = clave(t);
    if (!k || vistos.has(k)) continue;
    const tema = pendiente.some((p) => k.includes(p)) ? Object.keys(temas).find((x) => temas[x].some((p) => k.includes(p))) : undefined;
    if (tema && (capturado[tema] || usados.has(tema))) continue;
    if (k.startsWith("modo demo") && salida.some((x) => clave(x).startsWith("modo demo"))) continue;
    vistos.add(k);
    if (tema) usados.add(tema);
    salida.push(t);
  }
  return salida;
}

/** true si ya hay contenido (capturado o generado) — la API no debe volver a generarlo al guardar. */
export function tieneContenidoManual(c: ContenidoVacante): boolean {
  return Boolean(c.responsabilidades.length || c.preguntas_filtro.length || c.filtro.length);
}

/** Datos principales obligatorios (Parte 3): se exigen para generar y para publicar. */
export function faltantesDatosPrincipales(c: ContenidoVacante): string[] {
  const faltan: string[] = [];
  if (!c.titulo.trim()) faltan.push("Puesto");
  if (!c.area.trim()) faltan.push("Área");
  if (!c.seniority) faltan.push("Seniority");
  if (!c.ubicacion_estado && !c.ubicacion.trim()) faltan.push("Ubicación (Estado y Municipio)");
  if (!c.modalidad) faltan.push("Modalidad");
  if (!c.sueldo_periodicidad) faltan.push("Periodicidad del sueldo (o «A convenir»)");
  else if (c.sueldo_periodicidad !== "a_convenir" && !c.sueldo_desde) faltan.push("Sueldo desde (o «A convenir»)");
  return faltan;
}

/** Cuerpo snake_case para POST /plantillas, PATCH /plantillas/{id}, POST /vacantes y /vacantes/generar. */
export function contenidoComoPayload(c: ContenidoVacante) {
  const desde = c.sueldo_desde ? Number(c.sueldo_desde) : null;
  const hasta = c.sueldo_hasta ? Number(c.sueldo_hasta) : null;
  return {
    titulo: c.titulo.trim(),
    area: c.area,
    seniority: c.seniority,
    ubicacion: textoUbicacion(c.ubicacion_estado, c.ubicacion_municipio, c.ubicacion),
    ubicacion_estado: c.ubicacion_estado,
    ubicacion_municipio: c.ubicacion_municipio,
    modalidad: c.modalidad,
    sueldo_desde: c.sueldo_periodicidad === "a_convenir" ? null : desde,
    sueldo_hasta: c.sueldo_periodicidad === "a_convenir" ? null : hasta,
    sueldo_moneda: c.sueldo_moneda || "MXN",
    sueldo_periodicidad: c.sueldo_periodicidad,
    requisitos: c.requisitos.join(SEPARADOR_REQUISITOS),
    requisitos_indispensables: c.requisitos,
    descripcion: c.descripcion,
    responsabilidades: c.responsabilidades,
    requisitos_deseables: c.requisitos_deseables,
    beneficios: c.beneficios,
    documentos_solicitados: c.documentos_solicitados,
    // Cambio 37: la entidad única «Preguntas filtro» (el servidor deriva el formulario web y la pregunta de cada requisito)
    filtro: c.filtro,
    indispensables_propuestos: c.indispensables_propuestos.filter((x) => c.requisitos.some((r) => clave(r) === clave(x))),
    enfoque_entrevista: c.enfoque_entrevista,
    resumen: c.resumen,
    perfil_ideal: c.perfil_ideal,
    palabras_clave: c.palabras_clave,
    avisos_cumplimiento: avisosVigentes(c),
    texto_whatsapp: c.texto_whatsapp,
    texto_bolsa: c.texto_bolsa,
    guiones: {
      secciones: c.guiones.secciones,
      meta: c.guiones.meta,
      ...(c.enfoque_entrevista === "operativo" ? { perfil_operativo: c.guiones.perfil_operativo ?? null } : {}),
      ...(c.guiones.entrevista_base ? { entrevista_whatsapp_base: c.guiones.entrevista_base } : {}),
    },
  };
}

export function Seccion({ titulo, children, ayuda }: { titulo: string; children: React.ReactNode; ayuda?: string }) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        <Eyebrow>{titulo}</Eyebrow>
        {ayuda && <p className="mt-0.5 text-xs text-ink-3">{ayuda}</p>}
      </div>
      {children}
    </section>
  );
}

/** Fase 4: selectores anidados Estado → Municipio/Alcaldía (catálogo INEGI en lib/estados-municipios.json). */
function SelectorUbicacion({
  estado,
  municipio,
  libre,
  onChange,
}: {
  estado: string;
  municipio: string;
  libre: string;
  onChange: (estado: string, municipio: string) => void;
}) {
  const municipios = municipiosDe(estado);
  const claseSelect = "h-11 w-full rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20";
  return (
    <div className="grid gap-4 sm:col-span-2 sm:grid-cols-2">
      <label className="flex flex-col gap-1.5">
        <span className="text-sm font-medium text-ink-2">Estado *</span>
        <select value={estado} onChange={(e) => onChange(e.target.value, "")} className={claseSelect}>
          <option value="">Elige un estado…</option>
          {ESTADOS_MX.map((e) => (
            <option key={e} value={e}>{e}</option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1.5">
        <span className="text-sm font-medium text-ink-2">{estado === "Ciudad de México" ? "Alcaldía *" : "Municipio *"}</span>
        <select value={municipio} onChange={(e) => onChange(estado, e.target.value)} disabled={!estado} className={claseSelect}>
          <option value="">{estado ? "Elige…" : "Primero elige el estado"}</option>
          {municipios.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
        {!estado && libre.trim() && (
          <span className="text-[11px] text-ink-3">Ubicación actual (texto libre): «{libre}». Elige Estado y Municipio para estructurarla.</span>
        )}
      </label>
    </div>
  );
}

/** Guion de entrevista o llamada: enfoque + preguntas ABIERTAS (los temas se derivan de las preguntas). */
function GuionEditor({ valor, onChange }: { valor: GuionConversacion | undefined; onChange: (g: GuionConversacion) => void }) {
  const g = valor ?? { enfoque: "", temas: [], preguntas: [] };
  const rev = g.operativo?.revision;
  return (
    <div className="flex flex-col gap-3">
      {/* 2026-10-11: revisión automática del guion operativo; si no pasó tras regenerar, RH ve qué falló */}
      {rev && (rev.ok ? (
        <p className="rounded-lg bg-good-soft px-3 py-2 text-[12px] text-good">
          Revisión automática aprobada{rev.intentos > 1 ? ` (en el intento ${rev.intentos})` : ""}. Las preguntas marcadas
          «[Solo si responde sí/no]» se hacen solo según la respuesta anterior.
        </p>
      ) : (
        <div className="rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-[12px] text-ink-2">
          <p className="font-semibold text-warn">
            La revisión automática no pasó{rev.intentos > 1 ? ` después de ${rev.intentos} intentos` : ""}. Corrige esto antes de usarlo:
          </p>
          <ul className="mt-1 list-disc pl-4">
            {rev.problemas.map((p) => <li key={p}>{p}</li>)}
          </ul>
        </div>
      ))}
      <Area label="Enfoque" value={g.enfoque} onChange={(enfoque) => onChange({ ...g, enfoque })} rows={2} />
      <ListaEditable
        label="Preguntas (abiertas, en orden)"
        items={g.preguntas}
        onChange={(preguntas) => onChange({ ...g, preguntas, temas: preguntas.map((q) => q.replace(/^[¿\s]+|[?.\s]+$/g, "").slice(0, 120)) })}
        placeholder="Ej. Cuéntame de tu último trabajo en almacén."
      />
    </div>
  );
}

/** «Revisar»: las plantillas de conversación SOLO de las actividades de la ruta, editables. Al editar una sección queda
 * marcada como edición de RH (no se regenera sin preguntar). */
function PlantillasConversacion({ value, onChange }: { value: ContenidoVacante; onChange: (c: ContenidoVacante) => void }) {
  const g = value.guiones;
  const marcar = (clave: ClaveGuion, cambios: Partial<ContenidoVacante>, secciones?: GuionesForm["secciones"]) =>
    onChange({
      ...value,
      ...cambios,
      guiones: { ...g, secciones: secciones ?? g.secciones, editadas: g.editadas.includes(clave) ? g.editadas : [...g.editadas, clave] },
    });
  // Cambio 37: los prefiltros viven en «Preguntas filtro» (debajo de los indispensables); aquí solo los guiones
  const orden = ORDEN_GUIONES.filter((k) => g.aplican.includes(k) && !k.startsWith("prefiltro"));
  return (
    <section className="rounded-xl border border-border-soft">
      <div className="flex items-center gap-2 border-b border-border-faint px-4 py-3">
        <MessageSquareText className="h-4 w-4 text-brand" />
        <span className="text-sm font-semibold text-ink">Revisar · Plantillas de conversación</span>
        <span className="text-xs text-ink-3">solo las actividades de la ruta de esta vacante</span>
      </div>
      <div className="flex flex-col gap-5 px-4 py-4">
        {orden.length === 0 && (
          <p className="text-sm text-ink-3">La ruta de esta vacante no tiene entrevistas de Red Human ni llamada: no hay guiones que revisar.</p>
        )}
        {orden.map((k) => {
          const editada = g.editadas.includes(k);
          return (
            <div key={k} className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Eyebrow>{TITULOS_GUION[k]}</Eyebrow>
                {editada && <span className="rounded-full bg-warn-soft px-2 py-0.5 text-[11px] font-medium text-warn">Editado por RH</span>}
              </div>
              <p className="text-[11px] text-ink-3">Solo preguntas abiertas. Red Human las hace en este orden con el candidato.</p>
              <GuionEditor valor={g.secciones[k]} onChange={(guion) => marcar(k, {}, { ...g.secciones, [k]: guion })} />
            </div>
          );
        })}
      </div>
    </section>
  );
}

/** Huella de lo que alimenta los guiones: si cambia después de generar, se sugiere volver a generar. */
function firmaGeneracion(c: ContenidoVacante, ruta?: ProcesoEntrada) {
  const pasos = ruta && "pasos" in ruta ? (ruta.pasos ?? []).map((x) => x.tipo) : ruta ?? null;
  return JSON.stringify([c.titulo, c.responsabilidades, c.requisitos, c.requisitos_deseables, c.ubicacion_estado, c.ubicacion_municipio,
    c.modalidad, c.sueldo_desde, c.sueldo_hasta, c.sueldo_periodicidad, c.enfoque_entrevista, pasos]);
}

export function FormularioContenidoVacante({
  value,
  onChange,
  onGenerado,
  conIA = true,
  clienteId,
  mostrarCliente = true,
  slotDatosPrincipales,
  faltaCliente = false,
  conGuiones = false,
  slotProceso,
  rutaGeneracion,
  intentoPublicar = 0,
}: {
  value: ContenidoVacante;
  onChange: (c: ContenidoVacante) => void;
  /** Vacantes lo usa para conservar los bloques de publicación (occ/linkedin/portal) del generador. */
  onGenerado?: (g: VacanteGenerada) => void;
  conIA?: boolean;
  /** Fase 4 (Punto 1): el nombre de empresa que usa la IA lo resuelve el servidor con la regla
   * Cliente visible / Cuenta; aquí solo viaja el contexto (nada de texto libre). */
  clienteId?: number | null;
  mostrarCliente?: boolean;
  /** Vacantes inyecta aquí el selector de Cliente (+ Mostrar cliente) dentro de Datos principales. */
  slotDatosPrincipales?: React.ReactNode;
  /** true cuando la Cuenta tiene Clientes y RH todavía no eligió (Cliente o «recluta directo»). */
  faltaCliente?: boolean;
  /** 2026-10-09 (solo Vacantes): plantillas de conversación por actividad de la ruta, en «Revisar». */
  conGuiones?: boolean;
  /** «Proceso de selección» va ANTES de Generar: lo que se genere depende de la ruta. */
  slotProceso?: React.ReactNode;
  /** La ruta elegida (sin ella, la API usa la predeterminada de la Cuenta). */
  rutaGeneracion?: ProcesoEntrada;
  /** Cambio 37: sube cada vez que RH intenta publicar — entonces se muestra «Falta para publicar». */
  intentoPublicar?: number;
}) {
  const set = <K extends keyof ContenidoVacante>(k: K) => (v: ContenidoVacante[K]) => onChange({ ...value, [k]: v });
  const [generando, setGenerando] = useState(false);
  const [errorIA, setErrorIA] = useState("");
  const [avanzado, setAvanzado] = useState(false);
  const [empresaIA, setEmpresaIA] = useState("");
  const [generado, setGenerado] = useState(() => tieneContenidoManual(value));
  const [firmaGen, setFirmaGen] = useState<string | null>(null);
  const [confirmar, setConfirmar] = useState<ClaveGuion[] | null>(null);
  const avisos = avisosVigentes(value);
  // Cambio 27 (2026-10-12): la plantilla de ruta define el enfoque — Masivos = Operativo, sin importar el seniority
  const enfoquePorRuta = conGuiones && rutaMasiva(rutaGeneracion);
  useEffect(() => {
    if (enfoquePorRuta && value.enfoque_entrevista !== "operativo") onChange({ ...value, enfoque_entrevista: "operativo" });
  }, [enfoquePorRuta, value, onChange]);
  // Cambio 28: lo que la ruta exige a la vacante (documentos, 3 indispensables, CV); sin esto «Publicar» queda desactivado
  const reqRuta = requisitosDeRuta(rutaGeneracion && "pasos" in rutaGeneracion ? rutaGeneracion.pasos ?? [] : []);
  const avisosRuta = avisosPublicacion(value, reqRuta);
  // Cambio 37: «Falta para publicar» DIFERIDO — solo tras «Generar con Red Human» o al intentar publicar
  const [mostrarFaltantes, setMostrarFaltantes] = useState(false);
  useEffect(() => {
    if (intentoPublicar > 0) setMostrarFaltantes(true);
  }, [intentoPublicar]);
  const desactualizado = conGuiones && generado && (firmaGen !== null ? firmaGen !== firmaGeneracion(value, rutaGeneracion) : value.guiones.desactualizado);

  async function generar(decision?: "conservar" | "sobrescribir") {
    // «Volver a generar»: las secciones editadas por RH nunca se pisan sin preguntar
    const editadas = conGuiones ? value.guiones.editadas : [];
    if (generado && editadas.length && !decision) {
      setConfirmar(editadas);
      return;
    }
    setConfirmar(null);
    const faltan = faltantesDatosPrincipales(value);
    if (faltaCliente) faltan.splice(3, 0, "Cliente (o «La Cuenta recluta directo»)");
    if (faltan.length) {
      setErrorIA(`Para generar, captura primero: ${faltan.join(", ")}.`);
      return;
    }
    setGenerando(true);
    setErrorIA("");
    const p = contenidoComoPayload(value);
    const r = await generarVacanteIA({
      titulo: p.titulo,
      area: p.area,
      seniority: p.seniority,
      ubicacion: p.ubicacion,
      modalidad: p.modalidad,
      sueldo_desde: p.sueldo_desde,
      sueldo_hasta: p.sueldo_hasta,
      sueldo_moneda: p.sueldo_moneda,
      sueldo_periodicidad: p.sueldo_periodicidad,
      descripcion: p.descripcion,
      requisitos_indispensables: p.requisitos_indispensables,
      requisitos_deseables: p.requisitos_deseables,
      beneficios: p.beneficios,
      documentos_solicitados: p.documentos_solicitados,
      cliente_id: clienteId ?? null,
      mostrar_cliente_candidato: mostrarCliente,
      responsabilidades: p.responsabilidades,
      enfoque_entrevista: value.enfoque_entrevista,
      ...(value.enfoque_entrevista === "operativo" && value.guiones.perfil_operativo ? { perfil_operativo: value.guiones.perfil_operativo } : {}),
      ...(conGuiones
        ? {
            proceso: rutaGeneracion,
            guiones_actuales: { secciones: value.guiones.secciones },
            filtro: value.filtro,
            conservar: decision === "conservar" ? editadas : [],
          }
        : {}),
    });
    setGenerando(false);
    if (!r.ok) {
      setErrorIA(r.error);
      return;
    }
    const base = contenidoDesdeGenerado(value, r.data);
    onChange(conGuiones ? aplicarGuionesGenerados(base, r.data) : base);
    setFirmaGen(firmaGeneracion(value, rutaGeneracion));
    onGenerado?.(r.data);
    setGenerado(true);
    setMostrarFaltantes(true);
    if (r.data.empresa) setEmpresaIA(r.data.empresa);
  }

  const sueldo = { desde: value.sueldo_desde, hasta: value.sueldo_hasta, moneda: value.sueldo_moneda, periodicidad: value.sueldo_periodicidad };

  const nPrefiltro = value.filtro.length;
  const resumenAvanzado = generado
    ? [
        value.responsabilidades.length ? `${value.responsabilidades.length} responsabilidades` : "",
        nPrefiltro ? `${nPrefiltro} preguntas filtro` : "",
        value.perfil_ideal ? "perfil ideal" : "",
        value.texto_whatsapp || value.texto_bolsa ? "textos de publicación" : "",
      ].filter(Boolean).join(" · ")
    : "Red Human genera todo esto al presionar el botón; ábrelo solo si quieres ajustarlo a mano.";

  return (
    <div className="flex flex-col gap-6">
      {/* 1. Datos principales — condiciones reales que la IA nunca inventa (Puesto, Cliente, ubicación,
          modalidad, sueldo). Se mantienen arriba porque sin ellas no se puede generar ni publicar. */}
      <Seccion titulo="Datos principales">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Puesto *" value={value.titulo} onChange={set("titulo")} placeholder="Ej. Cajero(a) de sucursal" full />
          <Field label="Área *" value={value.area} onChange={set("area")} placeholder="Operaciones, Ventas…" />
          <Selector label="Seniority *" value={value.seniority} onChange={set("seniority")} opciones={[{ valor: "", texto: "Elige un nivel…" }, ...SENIORITIES.map((s) => ({ valor: s, texto: s }))]} />
          {slotDatosPrincipales}
          <SelectorUbicacion
            estado={value.ubicacion_estado}
            municipio={value.ubicacion_municipio}
            libre={value.ubicacion}
            onChange={(estado, municipio) =>
              onChange({ ...value, ubicacion_estado: estado, ubicacion_municipio: municipio, ubicacion: textoUbicacion(estado, municipio, value.ubicacion) })
            }
          />
          <Selector label="Modalidad *" value={value.modalidad} onChange={set("modalidad")} opciones={MODALIDADES} />
          <CampoSueldo
            value={sueldo}
            onChange={(v) =>
              onChange({ ...value, sueldo_desde: v.desde, sueldo_hasta: v.hasta, sueldo_moneda: v.moneda, sueldo_periodicidad: v.periodicidad as PeriodicidadSueldo | "" })
            }
            periodicidades={PERIODICIDADES_SUELDO}
            monedas={MONEDAS_SUELDO}
          />
        </div>
      </Seccion>

      {/* 2. Lo que RH captura (regla 2026-09-16): descripción breve, indispensables, deseables, prestaciones y
          enfoque de entrevista. Lo capturado se respeta literal; Red Human solo complementa lo vacío. */}
      <Seccion titulo="Sobre la vacante">
        <Area
          label="Descripción breve"
          value={value.descripcion}
          onChange={set("descripcion")}
          rows={generado ? 4 : 3}
          placeholder="Una o dos líneas: qué hace el puesto y para quién. Red Human la expande."
        />
        <ListaEditable label={reqRuta.minIndispensables ? `Requisitos indispensables * (mínimo ${reqRuta.minIndispensables})` : "Requisitos indispensables"}
          items={value.requisitos} onChange={set("requisitos")} placeholder="Ej. Carrera técnica concluida"
          ayuda="Máximo 5. Solo condiciones que se puedan verificar (no tareas del puesto)." />
        <PropuestosRedHuman requisitos={value.requisitos} propuestos={value.indispensables_propuestos} />
        <ListaEditable label="Requisitos deseables" items={value.requisitos_deseables} onChange={set("requisitos_deseables")} placeholder="Ej. Inglés básico" />
        <ListaEditable label="Prestaciones" items={value.beneficios} onChange={set("beneficios")} placeholder="Ej. Vales de despensa" ayuda="Solo se publican las que captures aquí." />
        {conGuiones && <EditorPreguntasFiltro requisitos={value.requisitos} items={value.filtro} onChange={set("filtro")} />}
        <CampoDocumentos valor={value.documentos_solicitados} onChange={set("documentos_solicitados")}
          obligatorio={reqRuta.documentosObligatorios} etapas={reqRuta.etapasDocumentos} />
        {mostrarFaltantes && avisosRuta.length > 0 && (
          <div className="rounded-xl border border-warn/40 bg-warn-soft px-3.5 py-2.5 text-xs text-ink-2" role="alert">
            <p className="font-semibold text-ink">Falta para publicar (lo pide la ruta):</p>
            <ul className="mt-1 list-disc pl-4">{avisosRuta.map((a) => <li key={a}>{a}</li>)}</ul>
          </div>
        )}
        {enfoquePorRuta ? (
          <div className="rounded-xl border border-border-soft bg-surface-2/50 px-3.5 py-2.5">
            <p className="text-sm font-semibold text-ink">Enfoque de entrevista: Operativo</p>
            <p className="mt-0.5 text-xs text-ink-3">Lo define la ruta Masivos, sin importar el seniority.</p>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <Selector
              label="Enfoque de entrevista"
              value={value.enfoque_entrevista}
              onChange={(v) => set("enfoque_entrevista")(v as EnfoqueEntrevista)}
              opciones={ENFOQUES_ENTREVISTA.map((e) => ({ valor: e.valor, texto: e.texto }))}
            />
            <p className="self-end pb-2 text-xs leading-relaxed text-ink-3">
              {ENFOQUES_ENTREVISTA.find((e) => e.valor === value.enfoque_entrevista)?.detalle}
            </p>
          </div>
        )}
        {conGuiones && enfoquePorRuta && (
          <EntrevistaWhatsappBase
            value={value.guiones.entrevista_base ?? { quitadas: [], propias: [] }}
            onChange={(cfg) => onChange({ ...value, guiones: { ...value.guiones, entrevista_base: cfg } })}
          />
        )}
        {conGuiones && value.enfoque_entrevista === "operativo" && !enfoquePorRuta && (
          <PerfilOperativoCampo
            value={value.guiones.perfil_operativo ?? null}
            detectado={value.guiones.perfil_detectado ?? ""}
            onChange={(p) => onChange({ ...value, guiones: { ...value.guiones, perfil_operativo: p } })}
          />
        )}
      </Seccion>

      {/* 2026-10-09: «Proceso de selección» ANTES de generar — se genera solo lo de las actividades de la ruta */}
      {slotProceso}

      {/* 3. Única acción principal */}
      {conIA && (
        <div className="rounded-xl border border-dashed border-brand/40 bg-brand-soft/30 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-ink-2">
              {generado ? "Vuelve a generar si cambiaste algo arriba." : "Red Human completa descripción, responsabilidades, prefiltros, entrevista y textos de publicación."}
            </p>
            <Button type="button" onClick={() => generar()} disabled={generando}>
              <Sparkles className="h-4 w-4" /> {generando ? "Generando…" : generado ? "Volver a generar" : "Generar vacante con Red Human"}
            </Button>
          </div>
          {errorIA && <p className="mt-2 text-xs text-bad">{errorIA}</p>}
          {empresaIA && !errorIA && (
            <p className="mt-2 text-xs text-ink-3">
              Contenido generado a nombre de <b className="text-ink">{empresaIA}</b>.
            </p>
          )}
          {confirmar && (
            <div className="mt-3 rounded-lg border border-warn/40 bg-warn-soft/50 p-3 text-sm text-ink-2">
              <p className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn" />
                <span>Editaste a mano: {confirmar.map((k) => `«${TITULOS_GUION[k]}»`).join(", ")}. ¿Qué hacemos con esas ediciones?</span>
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button type="button" size="sm" onClick={() => generar("conservar")}>Conservar mis ediciones</Button>
                <Button type="button" size="sm" variant="outline" onClick={() => generar("sobrescribir")}>Sobrescribirlas</Button>
                <Button type="button" size="sm" variant="outline" onClick={() => setConfirmar(null)}>Cancelar</Button>
              </div>
            </div>
          )}
          {desactualizado && !confirmar && (
            <p className="mt-2 flex items-center gap-1.5 text-xs text-warn">
              <AlertTriangle className="h-3.5 w-3.5" /> Cambiaste datos de la vacante o su ruta después de generar: vuelve a generar para actualizar los guiones.
            </p>
          )}
          {/* Avisos de cumplimiento: UNA sola vez, aquí; desaparecen al capturar el dato */}
          {generado && avisos.length > 0 && (
            <ul className="mt-3 space-y-1 rounded-lg bg-warn-soft/40 p-3 text-xs leading-relaxed text-ink-2">
              {avisos.map((a, i) => (
                <li key={i}>· {a}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* 4. Revisar — plantillas de conversación de las actividades de la ruta (Vacantes) */}
      {conGuiones && generado && <PlantillasConversacion value={value} onChange={onChange} />}

      {/* 4. Configuración avanzada — acordeón CERRADO por defecto: todo lo que la IA genera sola */}
      <section className="rounded-xl border border-border-soft">
        <button
          type="button"
          onClick={() => setAvanzado((a) => !a)}
          aria-expanded={avanzado}
          className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        >
          <span>
            <span className="text-sm font-semibold text-ink">Configuración avanzada</span>
            <span className="block text-xs text-ink-3">{resumenAvanzado}</span>
          </span>
          {avanzado ? <ChevronUp className="h-4 w-4 shrink-0 text-ink-3" /> : <ChevronDown className="h-4 w-4 shrink-0 text-ink-3" />}
        </button>
        <div className={cn("flex flex-col gap-6 border-t border-border-faint px-4 py-4", !avanzado && "hidden")}>
          <Seccion titulo="Contenido generado">
            <ListaEditable label="Responsabilidades principales" items={value.responsabilidades} onChange={set("responsabilidades")} placeholder="Una responsabilidad por renglón" />
            <Area label="Resumen (portal)" value={value.resumen} onChange={set("resumen")} rows={2} />
            <Area label="Perfil ideal" value={value.perfil_ideal} onChange={set("perfil_ideal")} rows={3} />
            <ListaEditable label="Palabras clave" items={value.palabras_clave} onChange={set("palabras_clave")} />
          </Seccion>
          <Seccion titulo="Textos de publicación">
            <Area label="Texto para WhatsApp" value={value.texto_whatsapp} onChange={set("texto_whatsapp")} rows={3} />
            <Area label="Texto para bolsa de trabajo" value={value.texto_bolsa} onChange={set("texto_bolsa")} rows={4} />
          </Seccion>
        </div>
      </section>
    </div>
  );
}

/** Cambio 37: los indispensables que NO capturó RH los propone Red Human — se marcan para que RH los confirme o quite. */
function PropuestosRedHuman({ requisitos, propuestos }: { requisitos: string[]; propuestos: string[] }) {
  const vigentes = propuestos.filter((p) => requisitos.some((r) => clave(r) === clave(p)));
  if (!vigentes.length) return null;
  return (
    <div className="-mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-3">
      {vigentes.map((p) => (
        <span key={p} className="inline-flex items-center gap-1 rounded-full border border-brand/30 bg-brand-soft/60 px-2 py-0.5 text-brand">
          <Sparkles className="h-3 w-3" /> {p} · Propuesto por Red Human
        </span>
      ))}
    </div>
  );
}

/** Cambio 37 (2026-10-14): «Preguntas filtro» — la ÚNICA lista de preguntas de la vacante (formulario web y chat).
 *  Una por requisito indispensable (texto natural, tipo y regla de descarte) y las «Base» (edad, interés, sueldo,
 *  colonia, traslado), que no descartan. Una pregunta abierta nunca descarta. Lo que cambie en los indispensables se
 *  refleja al guardar (el servidor arma la pregunta de un requisito nuevo y quita la de uno que ya no está). */
function EditorPreguntasFiltro({ requisitos, items, onChange }: { requisitos: string[]; items: PreguntaFiltroU[]; onChange: (x: PreguntaFiltroU[]) => void }) {
  const vigentes = items.filter((q) => !q.indispensable || requisitos.some((r) => clave(r) === clave(q.requisito)));
  const sinPregunta = requisitos.filter((r) => r.trim() && !items.some((q) => q.indispensable && clave(q.requisito) === clave(r)));
  const set = (id: string, cambios: Partial<PreguntaFiltroU>) =>
    onChange(items.map((q) => {
      if (q.id !== id) return q;
      const x = { ...q, ...cambios };
      if (x.tipo === "abierta" || x.base && !x.indispensable) x.descarta = false;
      return x;
    }));
  if (!vigentes.length && !sinPregunta.length) return null;
  return (
    <div>
      <p className="text-sm font-semibold text-ink">Preguntas filtro</p>
      <p className="mt-0.5 text-xs text-ink-3">
        Una sola lista para el formulario web y el chat. Cada requisito indispensable tiene su pregunta; las «Base» solo se
        registran. Solo una respuesta verificable (Sí / No o un dato comparable) puede descartar.
      </p>
      <ul className="mt-2 flex flex-col gap-2">
        {vigentes.map((q) => (
          <li key={q.id} className="rounded-xl border border-border-soft bg-surface-2/50 p-3">
            <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
              <span className={cn("rounded-full px-2 py-0.5 font-medium",
                q.base && !q.indispensable ? "bg-surface-3 text-ink-2" : q.propuesto ? "bg-brand-soft text-brand" : "bg-good-soft text-good")}>
                {q.etiqueta ?? (q.base ? "Base" : "Requisito")}
              </span>
              <span className="text-ink-3">{q.requisito}</span>
            </div>
            <input value={q.pregunta} onChange={(e) => set(q.id, { pregunta: e.target.value })} aria-label={`Pregunta de ${q.requisito}`}
              className="mt-2 min-h-10 w-full rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand" />
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-2">
              <select value={q.tipo} disabled={q.base} onChange={(e) => set(q.id, { tipo: e.target.value as PreguntaFiltroU["tipo"] })}
                className="h-9 rounded-lg border border-border-soft bg-surface px-2 disabled:opacity-60">
                {TIPOS_PREGUNTA_FILTRO.map((t) => <option key={t.valor} value={t.valor}>{t.texto}</option>)}
              </select>
              <label className={cn("flex items-center gap-1.5", (q.tipo === "abierta" || (q.base && !q.indispensable)) && "opacity-50")}>
                <input type="checkbox" checked={q.descarta} disabled={q.tipo === "abierta" || (q.base && !q.indispensable)}
                  onChange={(e) => set(q.id, { descarta: e.target.checked })} className="h-3.5 w-3.5 rounded border-border-soft text-brand" />
                Descarta
              </label>
              <span className="text-ink-3">{q.descarta ? q.regla : "No descarta"}</span>
            </div>
          </li>
        ))}
        {sinPregunta.map((r) => (
          <li key={`nuevo-${r}`} className="rounded-xl border border-dashed border-border-soft px-3 py-2 text-xs text-ink-3">
            <b className="text-ink-2">{r}</b>: Red Human arma su pregunta al guardar.
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Cambio 25 (2026-10-12): «Documentos y fotos que se piden». Sugeridos en chips (INE, licencia, fotos del auto —4—…)
 *  y campo libre. Si la lista tiene elementos, la ruta del candidato suma la actividad «Documentos» después del
 *  prefiltro de WhatsApp y el agente le manda la liga para subirlos. Vacía al generar → Red Human la llena con lo que
 *  mencione la descripción. */
/** Cambio 28: lo que la ruta exige a la vacante. «Documentos» antes de Contratación = el campo es obligatorio; con
 *  «Documentos» en varias etapas cada documento elige «Se pide en:»; con prefiltro, mínimo 3 indispensables. */
export function requisitosDeRuta(pasos: PasoProceso[]) {
  const orden = (e: string) => ETAPAS_PIPELINE.indexOf(e as EtapaCandidato);
  const docs = pasos.filter((x) => x.tipo === "documentos" && !x.heredado);
  const etapasDocumentos = [...new Set(docs.map((x) => x.etapa))].sort((a, b) => orden(a) - orden(b));
  return {
    documentosObligatorios: docs.some((x) => x.id !== "documentos-vacante" && orden(x.etapa) < orden("Contratación")),
    etapasDocumentos,
    // Cambio 37: con prefiltro basta 1 indispensable (antes 3)
    minIndispensables: pasos.some((x) => x.tipo === "prefiltro_web" || x.tipo === "prefiltro_whatsapp") ? 1 : 0,
    exigeCv: pasos.some((x) => x.tipo === "analisis_cv"),
  };
}

export function avisosPublicacion(c: Pick<ContenidoVacante, "requisitos" | "documentos_solicitados">, r: ReturnType<typeof requisitosDeRuta>): string[] {
  const avisos: string[] = [];
  const n = c.requisitos.filter((x) => x.trim()).length;
  if (r.minIndispensables && n < r.minIndispensables) avisos.push(`Captura al menos ${r.minIndispensables} requisito indispensable (hoy ${n}): la ruta incluye prefiltro.`);
  if (r.documentosObligatorios && !c.documentos_solicitados.length) avisos.push("Indica qué documentos se piden: la ruta incluye «Documentos».");
  return avisos;
}

function CampoDocumentos({ valor, onChange, obligatorio = false, etapas = [] }: {
  valor: DocumentoSolicitado[]; onChange: (v: DocumentoSolicitado[]) => void; obligatorio?: boolean; etapas?: string[];
}) {
  const [otro, setOtro] = useState("");
  const tiene = (n: string) => valor.some((d) => clave(d.nombre) === clave(n));
  const alternar = (d: DocumentoSolicitado) => onChange(tiene(d.nombre) ? valor.filter((x) => clave(x.nombre) !== clave(d.nombre)) : [...valor, d]);
  const agregar = () => {
    const nombre = otro.trim();
    if (nombre && !tiene(nombre)) onChange([...valor, { id: "otro", nombre, cantidad: 1, foto: false }]);
    setOtro("");
  };
  const otros = valor.filter((d) => !DOCUMENTOS_SUGERIDOS_VACANTE.some((x) => clave(x.nombre) === clave(d.nombre)));
  return (
    <div>
      <p className="text-sm font-semibold text-ink">Documentos y fotos que se piden{obligatorio ? " *" : ""}</p>
      <p className="mt-0.5 text-xs text-ink-3">
        {obligatorio ? "Obligatorio: la ruta incluye «Documentos»." : "Opcional."} Si eliges alguno, al pasar el prefiltro de WhatsApp el
        candidato recibe una liga para subirlos (foto o PDF); si la ruta no trae «Documentos», se agrega sola después del prefiltro.
      </p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {DOCUMENTOS_SUGERIDOS_VACANTE.map((d) => (
          <button key={d.nombre} type="button" onClick={() => alternar(d)}
            className={cn("rounded-full border px-3 py-1 text-[12px] font-medium transition",
              tiene(d.nombre) ? "border-brand bg-brand-soft text-brand" : "border-border-soft text-ink-2 hover:text-ink")}>
            {d.nombre}{d.cantidad > 1 ? ` (${d.cantidad})` : ""}
          </button>
        ))}
        {otros.map((d) => (
          <span key={d.nombre} className="inline-flex items-center gap-1 rounded-full border border-brand bg-brand-soft px-3 py-1 text-[12px] font-medium text-brand">
            {d.nombre}
            <button type="button" onClick={() => alternar(d)} aria-label={`Quitar ${d.nombre}`}><X className="h-3 w-3" /></button>
          </span>
        ))}
      </div>
      <div className="mt-2 flex gap-2">
        <input value={otro} onChange={(e) => setOtro(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); agregar(); } }}
          placeholder="Otro documento (p. ej. Carta de no antecedentes penales)"
          className="min-h-10 flex-1 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
        <button type="button" onClick={agregar} disabled={!otro.trim()} className="rounded-xl border border-border-soft px-3 text-sm font-semibold text-ink-2 disabled:opacity-50">
          Agregar
        </button>
      </div>
      {etapas.length > 1 && valor.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1.5">
          {valor.map((d) => (
            <li key={d.nombre} className="flex flex-wrap items-center gap-2 text-xs text-ink-2">
              <span className="min-w-[8rem] font-medium text-ink">{d.nombre}</span>
              <label className="flex items-center gap-1.5">
                Se pide en:
                <select value={d.etapa ?? etapas[0]} className="h-8 rounded-lg border border-border-soft bg-surface px-2"
                  onChange={(e) => onChange(valor.map((x) => (x.nombre === d.nombre ? { ...x, etapa: e.target.value as EtapaCandidato } : x)))}>
                  {etapas.map((e) => <option key={e} value={e}>{nombreEtapa(e)}</option>)}
                </select>
              </label>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
