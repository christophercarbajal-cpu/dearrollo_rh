/* Motor de ordenamiento del editor de procesos (funciones puras, sin React). Cambio 30 (2026-10-13), reglas del
   Cambio 55 (2026-10-15) — las MISMAS que valida el backend (`proceso.normalizar_pasos`, `models.ORDEN_FIJO_ETAPA`):
   - Prefiltro: orden FIJO, sin flechas — Solicitud web (sin / con CV) → Prefiltro por WhatsApp → Documentos.
   - Filtro Red Human: orden FIJO, sin flechas — Llamada Red Human → Entrevista por WhatsApp → Entrevista con avatar →
     Psicometría digital → Capacitación / Inducción (plataforma).
   - Filtro humano y Onboarding: con flechas; lo nuevo va al FINAL.
   - Contratación: con flechas; «Contrato y firma» sin flechas y SIEMPRE al final (lo nuevo se inserta justo antes).
   - Menú «+ Agregar actividad» (`disponibles`): sin lo que ya está en la etapa (salvo «Otra actividad», que es múltiple);
     «Solicitud web sin CV» y «con CV» son excluyentes. «Entrevista humana» también es múltiple (2026-10-15). */

type PasoOrden = { id: string; tipo: string; etapa: string; con_cv?: boolean; modalidad?: string };

/** Entrada del catálogo de un paso guardado (Cambio 30: la solicitud web es «sin CV» o «con CV»; la psicometría física
 *  es su propia entrada). */
export function claveCatalogo(p: Pick<PasoOrden, "tipo" | "con_cv" | "modalidad">): string {
  if (p.tipo === "solicitud_web") return p.con_cv ? "solicitud_con_cv" : "solicitud_sin_cv";
  if (p.tipo === "psicometrica" && p.modalidad === "fisica") return "psicometria_fisica";
  return p.tipo;
}

/** Cambio 55: etapas de orden fijo (sin flechas) y su orden. */
export const ORDEN_FIJO: Record<string, string[]> = {
  Prefiltro: ["solicitud_sin_cv", "solicitud_con_cv", "prefiltro_whatsapp", "documentos"],
  "Entrevista IA": ["llamada_agente", "entrevista_whatsapp", "entrevista_agente", "psicometrica", "induccion"],
};
const SIEMPRE_AL_FINAL = "carta_contrato";
/** Se pueden repetir en una etapa: «Otra actividad» y «Entrevista humana» (p. ej. Fletes: jefe de operadores y sindicato). */
const MULTIPLES = ["otra", "entrevista_humana"];
const SOLICITUDES = ["solicitud_sin_cv", "solicitud_con_cv"];

/** Rango dentro de una etapa fija (lo desconocido o heredado va al final, en su orden). */
function rangoFijo(p: PasoOrden): number {
  const lista = ORDEN_FIJO[p.etapa] ?? [];
  const i = lista.indexOf(claveCatalogo(p));
  return i === -1 ? 9_000 : i;
}

/** Compatibilidad (Cambio 30): posición sugerida de una actividad. */
export function rangoSugerido(p: PasoOrden, orden: string[] = []): number {
  if (p.tipo === SIEMPRE_AL_FINAL) return 10_000;
  if (ORDEN_FIJO[p.etapa]) return rangoFijo(p);
  const i = orden.indexOf(claveCatalogo(p));
  return i === -1 ? 9_000 : i;
}

/** Reglas fijas: Prefiltro y Filtro Red Human en su orden fijo; «Contrato y firma» al final de Contratación. El resto
 *  conserva el orden de RH. */
export function aplicarOrdenFijo<T extends PasoOrden>(pasos: T[], _orden: string[] = []): T[] {
  const porEtapa = new Map<string, T[]>();
  for (const p of pasos) porEtapa.set(p.etapa, [...(porEtapa.get(p.etapa) ?? []), p]);
  const salida: T[] = [];
  for (const [etapa, lista] of porEtapa) {
    if (ORDEN_FIJO[etapa]) {
      salida.push(...lista.map((p, k) => ({ p, k })).sort((a, b) => rangoFijo(a.p) - rangoFijo(b.p) || a.k - b.k).map((x) => x.p));
    } else {
      salida.push(...lista.filter((x) => x.tipo !== SIEMPRE_AL_FINAL), ...lista.filter((x) => x.tipo === SIEMPRE_AL_FINAL));
    }
  }
  return salida;
}

/** Inserta una actividad NUEVA: en una etapa fija, en su lugar; en Contratación, justo antes de «Contrato y firma»; en
 *  las demás, al final de su etapa. */
export function insertarSugerido<T extends PasoOrden>(pasos: T[], nuevo: T, _orden: string[] = []): T[] {
  let i = -1;
  if (ORDEN_FIJO[nuevo.etapa]) {
    const r = rangoFijo(nuevo);
    i = pasos.findIndex((x) => x.etapa === nuevo.etapa && rangoFijo(x) > r);
  } else if (nuevo.tipo !== SIEMPRE_AL_FINAL) {
    i = pasos.findIndex((x) => x.etapa === nuevo.etapa && x.tipo === SIEMPRE_AL_FINAL);
  }
  if (i !== -1) return [...pasos.slice(0, i), nuevo, ...pasos.slice(i)];
  const ultimo = pasos.map((x) => x.etapa).lastIndexOf(nuevo.etapa);
  return ultimo === -1 ? [...pasos, nuevo] : [...pasos.slice(0, ultimo + 1), nuevo, ...pasos.slice(ultimo + 1)];
}

/** ¿La actividad tiene flechas? No en Prefiltro ni Filtro Red Human, ni «Contrato y firma». */
export function conFlechas(p: PasoOrden): boolean {
  return !ORDEN_FIJO[p.etapa] && p.tipo !== SIEMPRE_AL_FINAL;
}

/** Cambio 55: lo que ofrece «+ Agregar actividad» en una etapa (`catalogo` = su catálogo estricto, ya sin «Otra
 *  actividad» en Prefiltro / Filtro Red Human, sin Psicometría digital en Filtro humano y sin Capacitación en
 *  Contratación). Oculta lo que ya está en la etapa (salvo «Otra actividad» y «Entrevista humana», que se repiten) y la otra
 *  Solicitud web si ya hay una. */
export function disponibles(etapa: string, catalogo: string[], pasos: PasoOrden[]): string[] {
  const enEtapa = new Set(pasos.filter((p) => p.etapa === etapa).map((p) => claveCatalogo(p)));
  const haySolicitud = pasos.some((p) => SOLICITUDES.includes(claveCatalogo(p)));
  return catalogo.filter((v) => MULTIPLES.includes(v) || (!enEtapa.has(v) && !(SOLICITUDES.includes(v) && haySolicitud)));
}
