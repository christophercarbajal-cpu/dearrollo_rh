/* Landing pública de postulación — Server Component (2026-09-27, Google Empleos).

   En el servidor pide el JSON-LD `JobPosting` de la vacante (GET /vacantes/slug/{slug}/jobposting) y lo
   inyecta como <script type="application/ld+json"> en el HTML, que es lo que lee Google Empleos. La API
   responde 404 si la vacante no está Publicada o RH no marcó «Google Empleos»: en ese caso (o si la API
   no contesta) no se inyecta nada y la página sigue funcionando igual. El formulario (un solo paso) vive
   intacto en `formulario-aplicar.tsx`. Ver docs/arquitectura_bolsas_empleo.md (3.3-5). */

import { Suspense } from "react";
import FormularioAplicar from "./formulario-aplicar";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
// Cada cuánto se vuelve a pedir el marcado: apagar «Google Empleos» o cerrar la vacante se refleja
// en la página a más tardar en este tiempo.
const REVALIDAR_SEGUNDOS = 300;

async function jobPosting(slug: string): Promise<Record<string, unknown> | null> {
  try {
    const r = await fetch(`${API}/vacantes/slug/${encodeURIComponent(slug)}/jobposting`, {
      next: { revalidate: REVALIDAR_SEGUNDOS },
    });
    if (!r.ok) return null;
    const datos = await r.json();
    return datos && datos["@type"] === "JobPosting" ? datos : null;
  } catch {
    return null; // la API caída nunca tumba la landing
  }
}

/** Método recomendado por Next.js para JSON-LD: JSON serializado con `<` escapado, para que ningún
 * texto de la vacante pueda cerrar el <script> (evita XSS). */
function jsonLdSeguro(datos: Record<string, unknown>): string {
  return JSON.stringify(datos).replace(/</g, "\\u003c");
}

export default async function Aplicar({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const marcado = await jobPosting(slug);
  return (
    <>
      {marcado && <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdSeguro(marcado) }} />}
      {/* Cambio 36: el formulario lee ?canal= (useSearchParams) */}
      <Suspense>
        <FormularioAplicar />
      </Suspense>
    </>
  );
}
