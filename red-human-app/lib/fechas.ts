/**
 * Regla única de fechas del frontend (2026-09-29, bug de las 6 horas).
 *
 * - La API manda instantes en UTC con zona («…Z» / «+00:00»); se MUESTRAN y se CAPTURAN en la zona de la
 *   organización (`ZONA_ORG`, America/Mexico_City por defecto), nunca en la del navegador.
 * - Un ISO sin zona que llegue de la API se toma como UTC (así guarda la base), nunca como hora local:
 *   eso era lo que convertía una cita de las 7:29 a.m. en la 1:29 p.m.
 * - Los DÍAS de calendario (fecha de ingreso, fecha límite, término de contrato) se guardan a medianoche UTC:
 *   `dia()` los formatea sin convertir de zona (convertirlos los corría al día anterior).
 * - Contraparte del backend: `red-human-api/app/fechas.py`.
 */

export const ZONA_ORG = process.env.NEXT_PUBLIC_ZONA_HORARIA || "America/Mexico_City";

// Etiqueta que acompaña TODA hora mostrada (especificación «Evaluaciones unificadas», sección 8).
// Misma tabla en red-human-api/app/fechas.py.
const ETIQUETAS_ZONA: Record<string, string> = {
  "America/Mexico_City": "hora de Ciudad de México",
  "America/Monterrey": "hora de Monterrey",
  "America/Merida": "hora de Mérida",
  "America/Cancun": "hora de Cancún",
  "America/Chihuahua": "hora de Chihuahua",
  "America/Mazatlan": "hora del Pacífico",
  "America/Hermosillo": "hora de Sonora",
  "America/Tijuana": "hora de Tijuana",
};
export const ETIQUETA_ZONA = ETIQUETAS_ZONA[ZONA_ORG] ?? `hora de ${ZONA_ORG}`;

/** «7:29 a.m.» → «7:29 a.m. (hora de Ciudad de México)». */
export function conZona(texto: string): string {
  return texto ? `${texto} (${ETIQUETA_ZONA})` : texto;
}

type Entrada = string | Date | null | undefined;

const SIN_ZONA = /T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;

/** ISO de la API → Date (sin zona = UTC). null si no es una fecha válida. */
export function aFecha(valor: Entrada): Date | null {
  if (!valor) return null;
  if (valor instanceof Date) return Number.isNaN(valor.getTime()) ? null : valor;
  const texto = SIN_ZONA.test(valor) ? `${valor}Z` : valor;
  const d = new Date(texto);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Fecha y hora en la zona de la organización. Default: «30 sep 2026, 7:29». */
export function textoFechaHora(valor: Entrada, opciones: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" }): string {
  const d = aFecha(valor);
  return d ? d.toLocaleString("es-MX", { ...opciones, timeZone: ZONA_ORG }) : "";
}

/** Fecha y hora de una CITA con la etiqueta de zona — así se muestra en tarjeta, ficha y liga. */
export function textoCita(valor: Entrada, opciones?: Intl.DateTimeFormatOptions): string {
  return conZona(textoFechaHora(valor, opciones));
}

/** Solo la fecha de un INSTANTE (creado, enviado, eliminado…) en la zona de la organización. */
export function textoFecha(valor: Entrada, opciones: Intl.DateTimeFormatOptions = {}): string {
  const d = aFecha(valor);
  return d ? d.toLocaleDateString("es-MX", { ...opciones, timeZone: ZONA_ORG }) : "";
}

/** Un DÍA de calendario (guardado a medianoche UTC): se formatea sin cambiar de zona. */
export function textoDia(valor: Entrada, opciones: Intl.DateTimeFormatOptions = {}): string {
  if (!valor) return "";
  const texto = valor instanceof Date ? valor.toISOString() : valor;
  const d = new Date(`${texto.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("es-MX", { ...opciones, timeZone: "UTC" });
}

/** Partes de reloj en la zona de la organización → para precargar <input type="date"/"time"/"datetime-local">. */
export function partesLocales(valor: Entrada): { fecha: string; hora: string } {
  const d = aFecha(valor);
  if (!d) return { fecha: "", hora: "" };
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: ZONA_ORG, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(d).map((x) => [x.type, x.value]),
  );
  return { fecha: `${p.year}-${p.month}-${p.day}`, hora: `${p.hour}:${p.minute}` };
}

/** Hoy (YYYY-MM-DD) en la zona de la organización. */
export function hoyLocal(): string {
  return partesLocales(new Date()).fecha;
}

/** «2026-09-30» + «07:29» capturados en la zona de la organización → instante (Date). */
export function desdeLocal(fechaTexto: string, horaTexto = "00:00"): Date | null {
  const [y, m, d] = fechaTexto.slice(0, 10).split("-").map(Number);
  const [h, min] = horaTexto.slice(0, 5).split(":").map(Number);
  if ([y, m, d, h, min].some((n) => Number.isNaN(n))) return null;
  const reloj = Date.UTC(y, m - 1, d, h, min);
  // desfase de la zona en ese momento (dos pasadas por si el cambio de horario cae en medio)
  let t = reloj;
  for (let i = 0; i < 2; i++) {
    const p = partesLocales(new Date(t));
    const [py, pm, pd] = p.fecha.split("-").map(Number);
    const [ph, pmin] = p.hora.split(":").map(Number);
    t += reloj - Date.UTC(py, pm - 1, pd, ph, pmin);
  }
  return new Date(t);
}
