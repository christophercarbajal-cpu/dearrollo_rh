"use client";

/* Formulario ÚNICO de resultado (Evaluaciones unificadas — Fase 1, 2026-09-29, especificación sección 5).
   Es EXACTAMENTE el mismo componente en el sistema («Registrar resultado» / «Complementar o corregir») y en la liga
   del evaluador (/evaluacion/[token]); solo cambia a dónde se envía (`onEnviar`).
   - Conclusión: entrevista humana obligatoria (Avanzar / No avanzar / Requiere otra entrevista); médica Apto /
     Apto con restricciones / No apto; resto Favorable / Con observaciones / Desfavorable (opcionales). Las opciones
     vienen de la API (`conclusionesPosibles`) para que nunca se desalineen.
   - Basta un comentario o un adjunto (PDF, imágenes o Word, 10 MB por archivo).
   - «Quién realizó la evaluación» (autor) ≠ quien la registra; precargado con el evaluador si fue asignada.
   - Guardar un resultado NUNCA mueve al candidato de etapa (lo recuerda el texto de ayuda). */

import { useRef, useState } from "react";
import { FileUp, Loader2, Paperclip, Save, X } from "lucide-react";
import { Button } from "@/components/ui";
import type { DatosResultado, Evaluacion, Resultado } from "@/lib/api";
import { cn } from "@/lib/utils";

const MAX_BYTES = 10 * 1024 * 1024;
const ACEPTA = "application/pdf,image/png,image/jpeg,image/webp,.doc,.docx,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const EXT_OK = ["pdf", "png", "jpg", "jpeg", "webp", "doc", "docx"];

export type ModoResultado = DatosResultado["modo"];

export function FormularioResultado({
  evaluacion,
  modo,
  onEnviar,
  onListo,
  onCancelar,
  onConflicto,
  etiquetaAutor = "Quién realizó la evaluación",
  compacto = false,
}: {
  evaluacion: Pick<Evaluacion, "tipo" | "conclusionesPosibles" | "conclusionObligatoria" | "conclusion" | "resultadoVersion" | "forma" | "evaluador" | "realizadaPor" | "comentarios">;
  /** registrar = primer resultado · corregir = RH reemplaza (lo anterior queda en historial) · complementar = agrega. */
  modo: ModoResultado;
  onEnviar: (datos: DatosResultado) => Promise<Resultado<unknown>>;
  onListo: () => void;
  onCancelar?: () => void;
  /** «Este resultado cambió mientras lo editabas» → el padre recarga el formulario. */
  onConflicto?: () => void;
  etiquetaAutor?: string;
  compacto?: boolean;
}) {
  const complementar = modo === "complementar";
  const autorInicial = modo === "corregir" ? evaluacion.realizadaPor : evaluacion.realizadaPor || (evaluacion.forma === "asignada" ? evaluacion.evaluador?.nombre ?? "" : "");
  const [conclusion, setConclusion] = useState(modo === "corregir" ? evaluacion.conclusion ?? "" : "");
  const [comentarios, setComentarios] = useState(modo === "corregir" ? evaluacion.comentarios ?? "" : "");
  const [autor, setAutor] = useState(autorInicial);
  const [archivos, setArchivos] = useState<File[]>([]);
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);
  const ref = useRef<HTMLInputElement>(null);

  // En un complemento la conclusión solo se ofrece si aún no había una (nunca se sobrescribe).
  const pideConclusion = !complementar || !evaluacion.conclusion;
  const conclusionObligatoria = evaluacion.conclusionObligatoria && !complementar;

  function agregarArchivos(lista: FileList | null) {
    const nuevos: File[] = [];
    for (const f of Array.from(lista ?? [])) {
      const ext = f.name.split(".").pop()?.toLowerCase() ?? "";
      if (!EXT_OK.includes(ext)) return setError(`«${f.name}»: solo PDF, imágenes (JPG, PNG, WEBP) o Word.`);
      if (f.size > MAX_BYTES) return setError(`«${f.name}» pesa más de 10 MB.`);
      nuevos.push(f);
    }
    setError("");
    setArchivos((a) => [...a, ...nuevos]);
  }

  async function guardar() {
    if (conclusionObligatoria && !conclusion) return setError("Elige la conclusión de la entrevista.");
    if (!conclusion && !comentarios.trim() && archivos.length === 0 && !(modo === "corregir")) {
      return setError(complementar ? "El complemento necesita un comentario o un adjunto." : "Agrega al menos un comentario o un adjunto.");
    }
    setEnviando(true);
    setError("");
    const r = await onEnviar({ conclusion, comentarios: comentarios.trim(), realizadaPor: autor.trim(), archivos, version: evaluacion.resultadoVersion, modo });
    setEnviando(false);
    if (!r.ok) {
      setError(r.error);
      if (/cambió mientras lo editabas/i.test(r.error)) onConflicto?.();
      return;
    }
    onListo();
  }

  return (
    <div className="flex flex-col gap-4">
      {pideConclusion && evaluacion.conclusionesPosibles.length > 0 && (
        <fieldset>
          <legend className="text-sm font-medium text-ink-2">
            Conclusión {conclusionObligatoria ? <span className="text-bad">*</span> : <span className="font-normal text-ink-3">(opcional)</span>}
          </legend>
          <div className={cn("mt-1.5 grid gap-2", evaluacion.conclusionesPosibles.length === 3 ? "sm:grid-cols-3" : "sm:grid-cols-2")}>
            {evaluacion.conclusionesPosibles.map((o) => (
              <button
                key={o.valor}
                type="button"
                onClick={() => setConclusion(conclusion === o.valor && !conclusionObligatoria ? "" : o.valor)}
                aria-pressed={conclusion === o.valor}
                className={cn(
                  "min-h-11 rounded-xl border px-3 py-2 text-sm font-semibold transition totem:min-h-16 totem:text-xl",
                  conclusion === o.valor ? "border-brand bg-brand-soft text-brand" : "border-border-soft text-ink-2 hover:border-brand/50",
                )}
              >
                {o.texto}
              </button>
            ))}
          </div>
        </fieldset>
      )}

      <label className="flex flex-col gap-1.5">
        <span className="text-sm font-medium text-ink-2">
          {complementar ? "Complemento" : "Comentarios"} <span className="font-normal text-ink-3">(opcional)</span>
        </span>
        <textarea
          value={comentarios}
          onChange={(e) => setComentarios(e.target.value)}
          rows={compacto ? 3 : 5}
          placeholder={complementar ? "Lo que quieras agregar al resultado ya registrado…" : "Observaciones, hallazgos, lo que haya que validar…"}
          className="rounded-xl border border-border-soft bg-surface px-3.5 py-2.5 text-sm leading-relaxed outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
        />
      </label>

      <div>
        <span className="text-sm font-medium text-ink-2">Adjuntos <span className="font-normal text-ink-3">(opcional · PDF, imágenes o Word · 10 MB c/u)</span></span>
        <input ref={ref} type="file" multiple accept={ACEPTA} className="hidden" onChange={(e) => { agregarArchivos(e.target.files); e.target.value = ""; }} />
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => ref.current?.click()}>
            <FileUp className="h-4 w-4" /> Adjuntar archivos
          </Button>
          {archivos.map((f, i) => (
            <span key={`${f.name}-${i}`} className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-border-soft bg-surface-2 px-2.5 py-1 text-[12px] text-ink-2">
              <Paperclip className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{f.name}</span>
              <button type="button" aria-label={`Quitar ${f.name}`} onClick={() => setArchivos((a) => a.filter((_, j) => j !== i))} className="text-ink-3 hover:text-bad">
                <X className="h-3.5 w-3.5" />
              </button>
            </span>
          ))}
        </div>
      </div>

      {!complementar && (
        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-ink-2">{etiquetaAutor} <span className="font-normal text-ink-3">(opcional)</span></span>
          <input
            value={autor}
            onChange={(e) => setAutor(e.target.value)}
            placeholder="No especificado"
            className="h-11 rounded-xl border border-border-soft bg-surface px-3.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
          />
          <span className="text-[11px] text-ink-3">El autor real del estudio o la entrevista. Quien registra el resultado queda guardado aparte.</span>
        </label>
      )}

      <p className="text-[11px] leading-relaxed text-ink-3">
        Guardar el resultado no aprueba ni mueve al candidato de etapa: esa decisión la sigue tomando RH.
      </p>
      {error && <p role="alert" className="text-sm font-semibold text-bad">{error}</p>}
      <div className="flex flex-wrap justify-end gap-2">
        {onCancelar && <Button type="button" variant="outline" size="sm" onClick={onCancelar} disabled={enviando}>Cancelar</Button>}
        <Button type="button" onClick={guardar} disabled={enviando} className="totem:min-h-16 totem:text-xl">
          {enviando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          {complementar ? "Agregar complemento" : modo === "corregir" ? "Guardar corrección" : "Guardar resultado"}
        </Button>
      </div>
    </div>
  );
}
