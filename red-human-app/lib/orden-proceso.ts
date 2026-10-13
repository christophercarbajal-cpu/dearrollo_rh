/* Cambio 30 (2026-10-13): motor de ordenamiento del editor de procesos (funciones puras, sin React).
   - Al agregar, la actividad se acomoda SOLA en su posición sugerida: el orden del catálogo (el de `/procesos/opciones`).
   - Prefiltro es FIJO (siempre en orden de catálogo, sin flechas).
   - «Contrato y firma» va SIEMPRE al final de Contratación (sin flechas; nada se puede mover después de ella).
   - En las demás etapas RH reordena libremente con las flechas. */

type PasoOrden = { id: string; tipo: string; etapa: string; con_cv?: boolean; modalidad?: string };

/** Entrada del catálogo de un paso guardado (Cambio 30: la solicitud web es «sin CV» o «con CV»; la psicometría física
 *  es su propia entrada). */
export function claveCatalogo(p: Pick<PasoOrden, "tipo" | "con_cv" | "modalidad">): string {
  if (p.tipo === "solicitud_web") return p.con_cv ? "solicitud_con_cv" : "solicitud_sin_cv";
  if (p.tipo === "psicometrica" && p.modalidad === "fisica") return "psicometria_fisica";
  return p.tipo;
}

const ETAPA_FIJA = "Prefiltro";
const SIEMPRE_AL_FINAL = "carta_contrato";

/** Posición sugerida de una actividad (orden del catálogo; lo heredado o desconocido va al final). */
export function rangoSugerido(p: PasoOrden, orden: string[]): number {
  const i = orden.indexOf(claveCatalogo(p));
  if (p.tipo === SIEMPRE_AL_FINAL) return 10_000;
  return i === -1 ? 9_000 : i;
}

/** Reglas fijas: Prefiltro en orden de catálogo y «Contrato y firma» al final de Contratación. El resto conserva el orden de RH. */
export function aplicarOrdenFijo<T extends PasoOrden>(pasos: T[], orden: string[]): T[] {
  const porEtapa = new Map<string, T[]>();
  for (const p of pasos) porEtapa.set(p.etapa, [...(porEtapa.get(p.etapa) ?? []), p]);
  const salida: T[] = [];
  for (const [etapa, lista] of porEtapa) {
    if (etapa === ETAPA_FIJA) {
      salida.push(...[...lista].sort((a, b) => rangoSugerido(a, orden) - rangoSugerido(b, orden)));
    } else {
      salida.push(...lista.filter((x) => x.tipo !== SIEMPRE_AL_FINAL), ...lista.filter((x) => x.tipo === SIEMPRE_AL_FINAL));
    }
  }
  return salida;
}

/** Inserta una actividad NUEVA en su posición sugerida dentro de su etapa (antes de la primera con rango mayor). */
export function insertarSugerido<T extends PasoOrden>(pasos: T[], nuevo: T, orden: string[]): T[] {
  const r = rangoSugerido(nuevo, orden);
  const i = pasos.findIndex((x) => x.etapa === nuevo.etapa && rangoSugerido(x, orden) > r);
  if (i !== -1) return [...pasos.slice(0, i), nuevo, ...pasos.slice(i)];
  const ultimo = pasos.map((x) => x.etapa).lastIndexOf(nuevo.etapa);
  return ultimo === -1 ? [...pasos, nuevo] : [...pasos.slice(0, ultimo + 1), nuevo, ...pasos.slice(ultimo + 1)];
}

/** ¿La actividad tiene flechas? No en Prefiltro ni «Contrato y firma». */
export function conFlechas(p: PasoOrden): boolean {
  return p.etapa !== ETAPA_FIJA && p.tipo !== SIEMPRE_AL_FINAL;
}

