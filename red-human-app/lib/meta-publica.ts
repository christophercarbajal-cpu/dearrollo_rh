/* Cambio 31 (2026-10-13): metadatos Open Graph de las ligas PÚBLICAS del candidato (entrevista, documentos, oferta).
   Título «[Tipo de liga] · [puesto]» y descripción «[empresa]» — sin textos comerciales de la plataforma. Se arman en
   el SERVIDOR (generateMetadata de cada layout) con `GET /publico/meta/{tipo}/{token}`; si la API no responde, queda
   solo el tipo de liga. */

import type { Metadata } from "next";

export type TipoLiga = "entrevista" | "documentos" | "oferta";
const ETIQUETA: Record<TipoLiga, string> = { entrevista: "Entrevista", documentos: "Documentos", oferta: "Oferta laboral" };
const API = process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function metadatosLiga(tipo: TipoLiga, token: string): Promise<Metadata> {
  let titulo = ETIQUETA[tipo];
  let descripcion = "";
  try {
    const r = await fetch(`${API}/publico/meta/${tipo}/${encodeURIComponent(token)}`, { cache: "no-store", signal: AbortSignal.timeout(3000) });
    if (r.ok) {
      const d = (await r.json()) as { titulo?: string; descripcion?: string };
      titulo = d.titulo || titulo;
      descripcion = d.descripcion || "";
    }
  } catch {
    /* sin API: queda el tipo de liga */
  }
  return {
    title: titulo,
    description: descripcion || titulo,
    openGraph: { title: titulo, description: descripcion || titulo, type: "website" },
    twitter: { card: "summary", title: titulo, description: descripcion || titulo },
  };
}
