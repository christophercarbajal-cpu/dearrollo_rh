/* Cambio 43 (2026-10-14): textos por plataforma. Red Human los genera en Markdown; la interfaz NUNCA muestra los símbolos
   crudos (#, ##, **, *, _, `). Funciones PURAS (sin React ni navegador) para poder verificarlas en Node:
   - `bloquesMarkdown` → bloques (título, párrafo, viñeta, numerada) con partes en negrita/cursiva para pintarlos.
   - `markdownATexto` → texto limpio (lo que lleva WhatsApp y el portapapeles en texto plano).
   - `markdownAHtml` → HTML simple (el portapapeles en texto enriquecido).
   - `ligaCanal` / `textoParaCopiar` / `htmlParaCopiar` → lo copiado SIEMPRE incluye la liga de postulación con `?canal=`. */

export type ParteMd = { texto: string; negrita?: boolean; cursiva?: boolean };
export type BloqueMd =
  | { tipo: "titulo"; nivel: number; partes: ParteMd[] }
  | { tipo: "parrafo"; partes: ParteMd[] }
  | { tipo: "vineta"; partes: ParteMd[] }
  | { tipo: "numerada"; numero: number; partes: ParteMd[] }
  | { tipo: "separador" };

const RE_TITULO = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RE_VINETA = /^\s*[-*+•]\s+(.*)$/;
const RE_NUMERADA = /^\s*(\d+)[.)]\s+(.*)$/;
const RE_SEPARADOR = /^\s*([-*_])(\s*\1){2,}\s*$/;

/** Partes en línea: **negrita** / __negrita__, *cursiva* / _cursiva_, `código` (sin símbolos en el resultado). */
export function partesEnLinea(linea: string): ParteMd[] {
  const partes: ParteMd[] = [];
  const re = /(\*\*|__)(.+?)\1|(\*|_)(?!\s)(.+?)(?<!\s)\3|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)/g;
  let ultimo = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(linea))) {
    // «_» dentro de una palabra (snake_case) no es cursiva
    if (m[3] === "_" && /\w/.test(linea[m.index - 1] ?? "")) continue;
    if (m.index > ultimo) partes.push({ texto: linea.slice(ultimo, m.index) });
    if (m[2] !== undefined) partes.push({ texto: limpiarResto(m[2]), negrita: true });
    else if (m[4] !== undefined) partes.push({ texto: limpiarResto(m[4]), cursiva: true });
    else if (m[5] !== undefined) partes.push({ texto: m[5] });
    else if (m[6] !== undefined) partes.push({ texto: `${m[6]} (${m[7]})` });
    ultimo = m.index + m[0].length;
  }
  if (ultimo < linea.length) partes.push({ texto: linea.slice(ultimo) });
  return partes.map((p) => ({ ...p, texto: limpiarResto(p.texto) })).filter((p) => p.texto !== "");
}

/** Símbolos sueltos que quedan sin par («**» o «#» aislados): nunca se muestran. */
function limpiarResto(t: string): string {
  return t.replace(/\*\*|__/g, "").replace(/(^|\s)#{1,6}(?=\s)/g, "$1").replace(/`/g, "");
}

export function bloquesMarkdown(md: string): BloqueMd[] {
  const bloques: BloqueMd[] = [];
  let parrafo: string[] = [];
  const cerrar = () => {
    if (parrafo.length) bloques.push({ tipo: "parrafo", partes: partesEnLinea(parrafo.join(" ")) });
    parrafo = [];
  };
  for (const crudo of String(md ?? "").replace(/\r\n?/g, "\n").split("\n")) {
    const linea = crudo.trimEnd();
    if (!linea.trim()) {
      cerrar();
      continue;
    }
    let m: RegExpMatchArray | null;
    if (RE_SEPARADOR.test(linea)) {
      cerrar();
      bloques.push({ tipo: "separador" });
    } else if ((m = linea.match(RE_TITULO))) {
      cerrar();
      bloques.push({ tipo: "titulo", nivel: m[1].length, partes: partesEnLinea(m[2]) });
    } else if ((m = linea.match(RE_VINETA))) {
      cerrar();
      bloques.push({ tipo: "vineta", partes: partesEnLinea(m[1]) });
    } else if ((m = linea.match(RE_NUMERADA))) {
      cerrar();
      bloques.push({ tipo: "numerada", numero: Number(m[1]), partes: partesEnLinea(m[2]) });
    } else {
      parrafo.push(linea.trim());
    }
  }
  cerrar();
  return bloques;
}

const textoDe = (partes: ParteMd[]) => partes.map((p) => p.texto).join("");

/** Texto limpio, sin ningún símbolo de Markdown (títulos como renglón propio, viñetas con «•»). */
export function markdownATexto(md: string): string {
  const salida: string[] = [];
  let previo: BloqueMd["tipo"] | null = null;
  for (const b of bloquesMarkdown(md)) {
    const lista = b.tipo === "vineta" || b.tipo === "numerada";
    if (salida.length && !(lista && (previo === "vineta" || previo === "numerada"))) salida.push("");
    if (b.tipo === "separador") salida.push("—");
    else if (b.tipo === "vineta") salida.push(`• ${textoDe(b.partes)}`);
    else if (b.tipo === "numerada") salida.push(`${b.numero}. ${textoDe(b.partes)}`);
    else salida.push(textoDe(b.partes));
    previo = b.tipo;
  }
  return salida.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

const escapar = (t: string) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const htmlPartes = (partes: ParteMd[]) =>
  partes.map((p) => (p.negrita ? `<strong>${escapar(p.texto)}</strong>` : p.cursiva ? `<em>${escapar(p.texto)}</em>` : escapar(p.texto))).join("");

export function markdownAHtml(md: string): string {
  const out: string[] = [];
  let lista: "ul" | "ol" | null = null;
  const cerrarLista = () => {
    if (lista) out.push(`</${lista}>`);
    lista = null;
  };
  for (const b of bloquesMarkdown(md)) {
    if (b.tipo === "vineta" || b.tipo === "numerada") {
      const tipo = b.tipo === "vineta" ? "ul" : "ol";
      if (lista !== tipo) {
        cerrarLista();
        out.push(`<${tipo}>`);
        lista = tipo;
      }
      out.push(`<li>${htmlPartes(b.partes)}</li>`);
      continue;
    }
    cerrarLista();
    if (b.tipo === "titulo") out.push(`<h${Math.min(b.nivel + 1, 6)}>${htmlPartes(b.partes)}</h${Math.min(b.nivel + 1, 6)}>`);
    else if (b.tipo === "parrafo") out.push(`<p>${htmlPartes(b.partes)}</p>`);
    else out.push("<hr>");
  }
  cerrarLista();
  return out.join("");
}

/** Liga de postulación del canal (misma regla que el servidor: `/aplicar/<slug>?canal=<canal>`). */
export function ligaCanal(origen: string, slug: string, canal: string): string {
  if (!slug) return "";
  return `${origen.replace(/\/+$/, "")}/aplicar/${slug}?canal=${encodeURIComponent(canal)}`;
}

/** Lo que se copia en texto plano: texto limpio + la liga del canal. */
export function textoParaCopiar(md: string, liga: string): string {
  const t = markdownATexto(md);
  return liga ? `${t}\n\n👉 Postúlate aquí: ${liga}` : t;
}

/** Lo que se copia en texto enriquecido: HTML + la liga del canal. */
export function htmlParaCopiar(md: string, liga: string): string {
  const h = markdownAHtml(md);
  return liga ? `${h}<p>👉 Postúlate aquí: <a href="${escapar(liga)}">${escapar(liga)}</a></p>` : h;
}

/** «Cajero(a) de sucursal» → «cajero-a-de-sucursal» (misma regla que `models.slugificar`), para la liga provisional de
 *  una vacante nueva antes de guardarla. */
export function slugificar(texto: string): string {
  const plano = String(texto ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  return plano.replace(/[^a-z0-9]+/g, "-").replace(/-{2,}/g, "-").replace(/^-+|-+$/g, "") || "vacante";
}
