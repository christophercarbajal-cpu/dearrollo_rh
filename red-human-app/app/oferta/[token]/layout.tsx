import type { Metadata } from "next";
import { metadatosLiga } from "@/lib/meta-publica";

// Cambio 31: vista previa de la liga = «[Tipo de liga] · [puesto]» + «[empresa]» (sin textos comerciales)
export async function generateMetadata({ params }: { params: Promise<{ token: string }> }): Promise<Metadata> {
  const { token } = await params;
  return metadatosLiga("oferta", token);
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
