"use client";

/* Ligas de entrevistador enviadas ANTES de Evaluaciones unificadas (2026-09-29): el token se migró tal cual a
   `evaluaciones.token_evaluador`, así que esta ruta abre la MISMA pantalla que /evaluacion/[token]. */

import { useParams } from "next/navigation";
import { EvaluacionPublicaPagina } from "@/components/evaluacion-publica";

export default function LigaEntrevistadorAnterior() {
  const params = useParams();
  return <EvaluacionPublicaPagina token={String(params?.token ?? "")} />;
}
