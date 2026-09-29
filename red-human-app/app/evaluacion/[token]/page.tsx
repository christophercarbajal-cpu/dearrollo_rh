"use client";

/* Liga del evaluador — Evaluaciones unificadas (2026-09-29). Ver components/evaluacion-publica.tsx. */

import { useParams } from "next/navigation";
import { EvaluacionPublicaPagina } from "@/components/evaluacion-publica";

export default function LigaEvaluador() {
  const params = useParams();
  return <EvaluacionPublicaPagina token={String(params?.token ?? "")} />;
}
