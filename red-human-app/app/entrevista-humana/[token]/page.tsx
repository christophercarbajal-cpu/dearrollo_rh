"use client";

/* Liga pública del entrevistador (Lote 3 · mejorada 2026-09-19). Primero el EXPEDIENTE completo del
   candidato (CV extraído y archivo, análisis de Luna, Entrevista Red Human, capacitación, documentos)
   para que el entrevistador vea el proceso antes de evaluar; abajo, el formulario de un solo envío
   (Resultado, Recomendación, Comentarios opcionales). Al enviar, la entrevista queda realizada y
   confirmada y se cierra el ciclo (autocierre en el backend). */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { AlertTriangle, Award, Bot, Briefcase, CheckCircle2, ChevronDown, FileText, GraduationCap, Loader2, Sparkles, User } from "lucide-react";
import { Logo, Button, Card, Badge } from "@/components/ui";
import { ThemeToggle } from "@/components/theme-toggle";
import { cn } from "@/lib/utils";
import {
  enviarEvaluacionEntrevistaHumana,
  fetchEntrevistaHumanaPublica,
  urlArchivoEntrevistaHumanaPublica,
  type EntrevistaHumanaPublica,
} from "@/lib/api";
import type { ResultadoEntrevistaHumana, RecomendacionEntrevistaHumana } from "@/lib/data";
import { textoCita } from "@/lib/fechas";

type Fase = "cargando" | "no_disponible" | "formulario" | "enviado";

export default function EvaluacionEntrevistaHumana() {
  const params = useParams();
  const token = String(params?.token ?? "");
  const [fase, setFase] = useState<Fase>("cargando");
  const [info, setInfo] = useState<EntrevistaHumanaPublica | null>(null);
  const [resultado, setResultado] = useState<ResultadoEntrevistaHumana | "">("");
  const [recomendacion, setRecomendacion] = useState<RecomendacionEntrevistaHumana | "">("");
  const [comentario, setComentario] = useState("");
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);

  useEffect(() => {
    fetchEntrevistaHumanaPublica(token).then((i) => {
      if (!i) return setFase("no_disponible");
      setInfo(i);
      setFase("formulario");
    });
  }, [token]);

  // 2026-09-19: la recomendación se propone sola desde el resultado (editable)
  useEffect(() => {
    if (resultado === "aprobado" && !recomendacion) setRecomendacion("avanzar");
    if (resultado === "no_aprobado" && (!recomendacion || recomendacion === "avanzar")) setRecomendacion("no_avanzar");
  }, [resultado]); // eslint-disable-line react-hooks/exhaustive-deps

  const listo = !!resultado && !!recomendacion;

  async function enviar() {
    if (!listo) return;
    setEnviando(true);
    setError("");
    const r = await enviarEvaluacionEntrevistaHumana(token, {
      resultado: resultado as ResultadoEntrevistaHumana,
      recomendacion: recomendacion as RecomendacionEntrevistaHumana,
      comentario,
    });
    setEnviando(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setFase("enviado");
  }

  const exp = info?.expediente;

  return (
    <main className="min-h-svh bg-bg">
      <header className="border-b border-border-soft">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-5 py-4">
          <Logo />
          <ThemeToggle />
        </div>
      </header>

      <div className="mx-auto max-w-3xl px-5 py-8 sm:py-10">
        {fase === "cargando" && (
          <div className="grid place-items-center py-24 text-ink-3">
            <Loader2 className="h-6 w-6 animate-spin" />
          </div>
        )}

        {fase === "no_disponible" && (
          <Card className="p-8 text-center">
            <h1 className="font-display text-xl font-bold">Liga no disponible</h1>
            <p className="mt-2 text-sm text-ink-2">La liga no es válida. Si crees que es un error, contacta al equipo de RH.</p>
          </Card>
        )}

        {fase === "formulario" && info && (
          <>
            <div className="text-center">
              <Badge tone="brand" dot>{info.expediente?.vacante.empresa || "Red Human"} · Entrevista humana</Badge>
              <h1 className="font-display mt-3 text-2xl font-bold sm:text-3xl">Expediente de {info.candidato}</h1>
              <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-ink-2">
                {info.puesto && `Vacante: ${info.puesto}. `}
                {info.fecha ? `Entrevista el ${textoCita(info.fecha, { dateStyle: "long", timeStyle: "short" })}. ` : ""}
                Revisa el proceso y registra tu evaluación al final.
              </p>
            </div>

            {/* ===== EXPEDIENTE ===== */}
            {exp && (
              <div className="mt-6 flex flex-col gap-3">
                <Seccion icono={User} titulo="Candidato y vacante" abierto>
                  <dl className="grid gap-2 text-sm sm:grid-cols-2">
                    <Dato k="Nombre" v={exp.candidato.nombre} />
                    <Dato k="Teléfono" v={exp.candidato.telefono || "—"} />
                    <Dato k="Correo" v={exp.candidato.correo || "—"} />
                    <Dato k="Etapa" v={exp.etapa || "—"} />
                    <Dato k="Vacante" v={exp.vacante.titulo} />
                    <Dato k="Afinidad de CV (Luna)" v={exp.score != null ? `${exp.score}/100` : "Sin CV analizado"} />
                  </dl>
                  {exp.vacante.requisitos && <p className="mt-3 text-xs leading-relaxed text-ink-3"><b className="text-ink-2">Requisitos:</b> {exp.vacante.requisitos}</p>}
                  {exp.vacante.perfilIdeal && <p className="mt-1 text-xs leading-relaxed text-ink-3"><b className="text-ink-2">Perfil ideal:</b> {exp.vacante.perfilIdeal}</p>}
                </Seccion>

                <Seccion icono={FileText} titulo="CV" abierto>
                  {exp.archivos.length > 0 && (
                    <div className="mb-3 flex flex-wrap gap-2">
                      {exp.archivos.map((a) => (
                        <a key={a.id} href={urlArchivoEntrevistaHumanaPublica(token, a.id)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 rounded-xl border border-border-soft bg-surface px-3 py-1.5 text-xs font-semibold text-brand transition hover:border-brand/40">
                          <FileText className="h-3.5 w-3.5" /> {a.tipo === "cv" ? "Abrir CV" : a.nombre || a.tipo}
                        </a>
                      ))}
                    </div>
                  )}
                  {exp.cv.resumen ? <p className="text-sm leading-relaxed text-ink-2">{exp.cv.resumen}</p> : <p className="text-sm text-ink-3">Sin datos extraídos del CV.</p>}
                  <Lista titulo="Habilidades" items={exp.cv.habilidades} />
                  <Lista titulo="Experiencia" items={exp.cv.experiencia.map((e) => (typeof e === "string" ? e : [e.puesto, e.empresa, e.periodo].filter(Boolean).join(" · ")))} />
                  <Lista titulo="Estudios" items={exp.cv.estudios} />
                  <Lista titulo="Idiomas" items={exp.cv.idiomas} />
                </Seccion>

                <Seccion icono={Sparkles} titulo="Análisis del CV (Luna)">
                  {exp.analisis.resumen && <p className="text-sm leading-relaxed text-ink-2">{exp.analisis.resumen}</p>}
                  <Lista titulo="Requisitos cumplidos" items={exp.analisis.requisitosCumplidos} tono="good" />
                  <Lista titulo="Brechas" items={exp.analisis.brechas} tono="warn" />
                  <Lista titulo="Fortalezas" items={exp.analisis.fortalezas} />
                  <Lista titulo="Alertas" items={exp.analisis.alertas} tono="bad" />
                  {!exp.analisis.resumen && !exp.analisis.requisitosCumplidos.length && !exp.analisis.brechas.length && <p className="text-sm text-ink-3">Sin análisis todavía.</p>}
                </Seccion>

                <Seccion icono={Bot} titulo="Entrevista Red Human (IA)" abierto={Boolean(exp.entrevistaIA)}>
                  {exp.entrevistaIA ? (
                    <>
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        {exp.entrevistaIA.matchPerfil != null && <Badge tone="brand">Afinidad {exp.entrevistaIA.matchPerfil}/100</Badge>}
                        {exp.entrevistaIA.recomendacion && (
                          <Badge tone={exp.entrevistaIA.recomendacion === "avanzar" ? "good" : exp.entrevistaIA.recomendacion === "no_avanzar" ? "bad" : "warn"}>
                            Recomendación IA: {exp.entrevistaIA.recomendacion.replace("_", " ")}
                          </Badge>
                        )}
                      </div>
                      {exp.entrevistaIA.resumen && <p className="text-sm leading-relaxed text-ink-2">{exp.entrevistaIA.resumen}</p>}
                      <Lista titulo="Fortalezas observadas" items={exp.entrevistaIA.fortalezas} tono="good" />
                      <Lista titulo="Puntos por validar en tu entrevista" items={exp.entrevistaIA.riesgos} tono="warn" />
                      <Lista titulo="No se cubrió" items={exp.entrevistaIA.faltante} tono="bad" />
                    </>
                  ) : (
                    <p className="text-sm text-ink-3">Sin Entrevista Red Human evaluada.</p>
                  )}
                </Seccion>

                {(exp.capacitacion.length > 0 || exp.documentos.length > 0) && (
                  <Seccion icono={GraduationCap} titulo="Capacitación y documentos">
                    {exp.capacitacion.map((k, i) => (
                      <p key={i} className="text-sm text-ink-2">
                        <Award className="mr-1 inline h-3.5 w-3.5 text-brand" /> {k.curso}: {k.aprobado ? "Aprobado" : "No aprobado"} ({k.calificacion}%)
                      </p>
                    ))}
                    {exp.documentos.length > 0 && (
                      <ul className="mt-2 grid gap-1 text-xs text-ink-2 sm:grid-cols-2">
                        {exp.documentos.map((d) => (
                          <li key={d.tipo} className="flex items-center gap-1.5">
                            {d.estado === "recibido" ? <CheckCircle2 className="h-3.5 w-3.5 text-good" /> : <AlertTriangle className="h-3.5 w-3.5 text-warn" />} {d.tipo} · {d.estado}
                          </li>
                        ))}
                      </ul>
                    )}
                  </Seccion>
                )}
              </div>
            )}

            {/* ===== EVALUACIÓN ===== */}
            <Card className="mt-6 p-6">
              <div className="flex items-center gap-2">
                <Briefcase className="h-4 w-4 text-brand" />
                <h2 className="font-display text-lg font-bold">Tu evaluación</h2>
              </div>
              {info.yaEvaluada ? (
                <div className="mt-3 rounded-xl border border-good/30 bg-good-soft/40 p-4 text-sm text-ink-2">
                  <CheckCircle2 className="mr-1 inline h-4 w-4 text-good" /> Esta entrevista ya fue evaluada
                  {info.resultado ? ` (${info.resultado === "aprobado" ? "Aprobado" : "No aprobado"})` : ""}. Si necesitas corregirla, contacta al equipo de RH.
                </div>
              ) : (
                <div className="mt-4 flex flex-col gap-4">
                  <div>
                    <span className="text-sm font-medium text-ink-2">Resultado</span>
                    <div className="mt-1.5 grid grid-cols-2 gap-2">
                      <button type="button" onClick={() => setResultado("aprobado")} className={cn("h-11 rounded-xl border text-sm font-medium transition", resultado === "aprobado" ? "border-good/25 bg-good-soft text-good" : "border-border-soft text-ink-2")}>
                        Aprobado
                      </button>
                      <button type="button" onClick={() => setResultado("no_aprobado")} className={cn("h-11 rounded-xl border text-sm font-medium transition", resultado === "no_aprobado" ? "border-bad/25 bg-bad-soft text-bad" : "border-border-soft text-ink-2")}>
                        Rechazado
                      </button>
                    </div>
                  </div>

                  <label className="flex flex-col gap-1.5">
                    <span className="text-sm font-medium text-ink-2">Recomendación</span>
                    <select value={recomendacion} onChange={(e) => setRecomendacion(e.target.value as RecomendacionEntrevistaHumana)} className="h-11 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20">
                      <option value="" disabled>Selecciona una opción</option>
                      <option value="avanzar">Avanzar</option>
                      <option value="no_avanzar">No avanzar</option>
                      <option value="segunda_entrevista">Segunda entrevista</option>
                    </select>
                  </label>

                  <label className="flex flex-col gap-1.5">
                    <span className="text-sm font-medium text-ink-2">Comentarios <span className="text-ink-3">(opcional)</span></span>
                    <textarea value={comentario} onChange={(e) => setComentario(e.target.value)} rows={4} className="rounded-xl border border-border-soft bg-surface px-3.5 py-2.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
                  </label>

                  {error && <p className="text-sm text-bad">{error}</p>}

                  <Button className="w-full" disabled={!listo || enviando} onClick={enviar}>
                    {enviando ? "Guardando…" : "Guardar evaluación"}
                  </Button>
                </div>
              )}
            </Card>
          </>
        )}

        {fase === "enviado" && (
          <Card className="p-8 text-center">
            <span className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-good/10">
              <CheckCircle2 className="h-7 w-7 text-good" />
            </span>
            <h1 className="font-display mt-4 text-2xl font-bold">¡Gracias por tu evaluación!</h1>
            <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-ink-2">
              La entrevista quedó confirmada como realizada y tu evaluación registrada. El equipo de RH ya fue notificado y tomará la decisión final.
            </p>
          </Card>
        )}
      </div>
    </main>
  );
}

function Seccion({ icono: Icono, titulo, abierto = false, children }: { icono: typeof User; titulo: string; abierto?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(abierto);
  return (
    <Card className="overflow-hidden">
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-center justify-between gap-3 px-5 py-3.5 text-left">
        <span className="flex items-center gap-2 text-sm font-semibold text-ink"><Icono className="h-4 w-4 text-brand" /> {titulo}</span>
        <ChevronDown className={cn("h-4 w-4 text-ink-3 transition-transform", open && "rotate-180")} />
      </button>
      {open && <div className="border-t border-border-faint px-5 py-4">{children}</div>}
    </Card>
  );
}

function Dato({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <dt className="font-mono text-[10px] uppercase tracking-wider text-ink-3">{k}</dt>
      <dd className="text-ink">{v}</dd>
    </div>
  );
}

function Lista({ titulo, items, tono }: { titulo: string; items: string[]; tono?: "good" | "warn" | "bad" }) {
  if (!items?.length) return null;
  return (
    <div className="mt-3">
      <p className={cn("font-mono text-[10px] font-bold uppercase tracking-wider", tono === "good" ? "text-good" : tono === "warn" ? "text-warn" : tono === "bad" ? "text-bad" : "text-ink-3")}>{titulo}</p>
      <ul className="mt-1 space-y-0.5 text-sm text-ink-2">
        {items.slice(0, 12).map((x, i) => <li key={i}>• {x}</li>)}
      </ul>
    </div>
  );
}
