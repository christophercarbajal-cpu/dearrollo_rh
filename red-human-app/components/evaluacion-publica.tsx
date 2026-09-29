"use client";

/* Liga del evaluador (Evaluaciones unificadas — Fase 1, 2026-09-29). Sin cuenta: el token es la credencial.
   Primero lo necesario para realizar ESTA evaluación (candidato, vacante, CV; en la entrevista humana además el
   análisis de CV, la Entrevista Red Human en solo lectura, capacitación y documentos) y abajo el formulario de
   resultado — el MISMO componente que usa RH en el sistema (`FormularioResultado`).
   - Si RH ya registró un resultado: se ve en solo lectura con «RH ya registró un resultado el [fecha]» y el botón
     «Agregar complemento» (nunca sobrescribe en silencio).
   - Cancelada → «Esta evaluación fue cancelada». Médica sin consentimiento → no acepta resultado.
   La sirven /evaluacion/[token] y /entrevista-humana/[token] (ligas enviadas antes de la unificación). */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Award, Bot, Briefcase, CalendarClock, CheckCircle2, ChevronDown, FileText, GraduationCap, Loader2, Sparkles, User } from "lucide-react";
import { Badge, Button, Card, Logo } from "@/components/ui";
import { ThemeToggle } from "@/components/theme-toggle";
import { FormularioResultado } from "@/components/dashboard/evaluaciones/formulario-resultado";
import {
  enviarResultadoPublico, fetchEvaluacionPublica, urlAdjuntoEvaluacionPublica, urlArchivoEvaluacionPublica, type EvaluacionPublica,
} from "@/lib/api";
import { textoCita, textoFechaHora } from "@/lib/fechas";
import { cn } from "@/lib/utils";

const RECOMENDACION_IA: Record<string, string> = { avanzar: "Avanzar", no_avanzar: "No avanzar", revision: "Revisión" };

type Fase = "cargando" | "no_disponible" | "listo" | "enviado";

export function EvaluacionPublicaPagina({ token }: { token: string }) {
  const [fase, setFase] = useState<Fase>("cargando");
  const [info, setInfo] = useState<EvaluacionPublica | null>(null);
  const [motivo, setMotivo] = useState("");
  const [complementar, setComplementar] = useState(false);
  const [enviadoComo, setEnviadoComo] = useState("");

  const cargar = useCallback(async () => {
    const r = await fetchEvaluacionPublica(token);
    if (!r.ok) {
      setMotivo(r.error);
      return setFase("no_disponible");
    }
    setInfo(r.data);
    setFase("listo");
  }, [token]);
  useEffect(() => {
    void cargar();
  }, [cargar]);

  const ev = info?.evaluacion;
  const exp = info?.expediente;

  return (
    <main className="sala-publica min-h-svh bg-bg">
      <header className="border-b border-border-soft">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-5 py-4">
          <Logo />
          <ThemeToggle />
        </div>
      </header>

      <div className="mx-auto max-w-3xl px-5 py-8 sm:py-10">
        {fase === "cargando" && (
          <div className="grid place-items-center py-24 text-ink-3"><Loader2 className="h-6 w-6 animate-spin" /></div>
        )}

        {fase === "no_disponible" && (
          <Card className="p-8 text-center">
            <h1 className="font-display text-xl font-bold">Liga no disponible</h1>
            <p className="mt-2 text-sm text-ink-2">{motivo || "La liga no es válida."} Si crees que es un error, contacta al equipo de RH.</p>
          </Card>
        )}

        {fase === "enviado" && (
          <Card className="p-8 text-center">
            <span className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-good/10"><CheckCircle2 className="h-7 w-7 text-good" /></span>
            <h1 className="font-display mt-4 text-2xl font-bold">¡Gracias!</h1>
            <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-ink-2">
              {enviadoComo === "complemento" ? "Tu complemento quedó registrado." : "Tu resultado quedó registrado."} El equipo de RH ya fue notificado y es quien decide los siguientes pasos.
            </p>
            <Button variant="outline" size="sm" className="mt-5" onClick={() => { setFase("cargando"); setComplementar(false); void cargar(); }}>Ver la evaluación</Button>
          </Card>
        )}

        {fase === "listo" && info && ev && exp && (
          <>
            <div className="text-center">
              <Badge tone="brand" dot>{info.empresa || "Red Human"} · {ev.nombre}</Badge>
              <h1 className="font-display mt-3 text-2xl font-bold sm:text-3xl">{ev.nombre} de {exp.candidato.nombre}</h1>
              <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-ink-2">
                {exp.vacante.titulo ? `Vacante: ${exp.vacante.titulo}. ` : ""}Revisa la información y registra el resultado al final.
              </p>
            </div>

            {info.cancelada && (
              <Card className="mt-6 border-bad/30 bg-bad-soft/40 p-5 text-center">
                <p className="font-semibold text-bad">Esta evaluación fue cancelada.</p>
                <p className="mt-1 text-sm text-ink-2">Ya no acepta resultados. Si tienes dudas, contacta al equipo de RH.</p>
              </Card>
            )}

            {ev.cita && (
              <Card className="mt-6 p-5">
                <p className="flex items-center gap-2 text-sm font-semibold"><CalendarClock className="h-4 w-4 text-brand" /> Cita</p>
                <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
                  <Dato k="Fecha y hora" v={textoCita(ev.cita.fechaHora, { dateStyle: "long", timeStyle: "short" })} />
                  <Dato k="Modalidad" v={ev.cita.modalidad || "—"} />
                  {ev.cita.direccion && <Dato k="Dirección" v={ev.cita.direccion} />}
                  {ev.cita.ligaVideollamada && (
                    <div><dt className="font-mono text-[10px] uppercase tracking-wider text-ink-3">Liga de videollamada</dt><dd><a href={ev.cita.ligaVideollamada} target="_blank" rel="noreferrer" className="break-all text-brand hover:underline">{ev.cita.ligaVideollamada}</a></dd></div>
                  )}
                  {ev.cita.telefono && <Dato k="Teléfono" v={ev.cita.telefono} />}
                </dl>
              </Card>
            )}
            {ev.instrucciones && (
              <Card className="mt-3 p-5">
                <p className="text-sm font-semibold">Instrucciones</p>
                <p className="mt-1 whitespace-pre-line text-sm leading-relaxed text-ink-2">{ev.instrucciones}</p>
              </Card>
            )}

            {/* ===== EXPEDIENTE (solo lo necesario para esta evaluación) ===== */}
            <div className="mt-6 flex flex-col gap-3">
              <Seccion icono={User} titulo="Candidato y vacante" abierto>
                <dl className="grid gap-2 text-sm sm:grid-cols-2">
                  <Dato k="Nombre" v={exp.candidato.nombre} />
                  <Dato k="Teléfono" v={exp.candidato.telefono || "—"} />
                  <Dato k="Correo" v={exp.candidato.correo || "—"} />
                  <Dato k="Vacante" v={exp.vacante.titulo || "—"} />
                </dl>
                {exp.vacante.requisitos && <p className="mt-3 text-xs leading-relaxed text-ink-3"><b className="text-ink-2">Requisitos:</b> {exp.vacante.requisitos}</p>}
                {exp.vacante.perfilIdeal && <p className="mt-1 text-xs leading-relaxed text-ink-3"><b className="text-ink-2">Perfil ideal:</b> {exp.vacante.perfilIdeal}</p>}
              </Seccion>

              <Seccion icono={FileText} titulo="CV" abierto={ev.tipo === "entrevista_humana"}>
                {exp.archivos.length > 0 && (
                  <div className="mb-3 flex flex-wrap gap-2">
                    {exp.archivos.map((a) => (
                      <a key={a.id} href={urlArchivoEvaluacionPublica(token, a.id)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 rounded-xl border border-border-soft bg-surface px-3 py-1.5 text-xs font-semibold text-brand transition hover:border-brand/40">
                        <FileText className="h-3.5 w-3.5" /> {a.tipo === "cv" ? "Abrir CV" : a.nombre || a.tipo}
                      </a>
                    ))}
                  </div>
                )}
                {exp.cv.resumen ? <p className="text-sm leading-relaxed text-ink-2">{exp.cv.resumen}</p> : <p className="text-sm text-ink-3">Sin datos extraídos del CV.</p>}
                <Lista titulo="Habilidades" items={exp.cv.habilidades} />
                <Lista titulo="Experiencia" items={exp.cv.experiencia.map((x) => (typeof x === "string" ? x : [x.puesto, x.empresa, x.periodo].filter(Boolean).join(" · ")))} />
                <Lista titulo="Estudios" items={exp.cv.estudios} />
                <Lista titulo="Idiomas" items={exp.cv.idiomas} />
              </Seccion>

              {exp.analisis && (
                <Seccion icono={Sparkles} titulo="Análisis del CV">
                  {exp.analisis.resumen && <p className="text-sm leading-relaxed text-ink-2">{exp.analisis.resumen}</p>}
                  <Lista titulo="Requisitos cumplidos" items={exp.analisis.requisitosCumplidos} tono="good" />
                  <Lista titulo="Brechas" items={exp.analisis.brechas} tono="warn" />
                  <Lista titulo="Fortalezas" items={exp.analisis.fortalezas} />
                  <Lista titulo="Alertas" items={exp.analisis.alertas} tono="bad" />
                </Seccion>
              )}

              {ev.tipo === "entrevista_humana" && (
                <Seccion icono={Bot} titulo="Entrevista Red Human (solo lectura)" abierto={Boolean(exp.entrevistaIA)}>
                  {exp.entrevistaIA ? (
                    <>
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        {exp.entrevistaIA.matchPerfil != null && <Badge tone="brand">Afinidad {exp.entrevistaIA.matchPerfil}/100</Badge>}
                        {exp.entrevistaIA.recomendacion && (
                          <Badge tone={exp.entrevistaIA.recomendacion === "avanzar" ? "good" : exp.entrevistaIA.recomendacion === "no_avanzar" ? "bad" : "warn"}>
                            Recomendación de la IA: {RECOMENDACION_IA[exp.entrevistaIA.recomendacion] ?? exp.entrevistaIA.recomendacion}
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
              )}

              {(exp.capacitacion.length > 0 || exp.documentos.length > 0) && (
                <Seccion icono={GraduationCap} titulo="Capacitación y documentos">
                  {exp.capacitacion.map((k, i) => (
                    <p key={i} className="text-sm text-ink-2"><Award className="mr-1 inline h-3.5 w-3.5 text-brand" /> {k.curso}: {k.aprobado ? "Aprobado" : "No aprobado"} ({k.calificacion}%)</p>
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

            {/* ===== RESULTADO: el MISMO formulario que usa RH ===== */}
            {!info.cancelada && (
              <Card className="mt-6 p-6">
                <div className="flex items-center gap-2">
                  <Briefcase className="h-4 w-4 text-brand" />
                  <h2 className="font-display text-lg font-bold">Resultado</h2>
                </div>
                {info.enEsperaConsentimiento ? (
                  <p className="mt-3 rounded-xl border border-warn/30 bg-warn-soft/40 p-4 text-sm text-ink-2">
                    Esta evaluación está en espera del consentimiento del candidato. Podrás registrar el resultado cuando lo otorgue.
                  </p>
                ) : info.yaTieneResultado && !complementar ? (
                  <div className="mt-3 flex flex-col gap-3">
                    <div className="rounded-xl border border-good/30 bg-good-soft/40 p-4 text-sm text-ink-2">
                      <CheckCircle2 className="mr-1 inline h-4 w-4 text-good" />
                      {info.avisoResultadoRh || `Resultado registrado${ev.registradaEn ? ` el ${textoFechaHora(ev.registradaEn, { dateStyle: "medium" })}` : ""}`}.
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {ev.conclusionTexto ? <Badge tone="brand" dot>{ev.conclusionTexto}</Badge> : <Badge tone="neutral">Resultado recibido · Sin conclusión</Badge>}
                    </div>
                    {ev.comentarios && <p className="whitespace-pre-line text-sm leading-relaxed text-ink-2">{ev.comentarios}</p>}
                    {ev.adjuntos.length > 0 && (
                      <ul className="flex flex-col gap-1">
                        {ev.adjuntos.map((a) => (
                          <li key={a.id}><a href={urlAdjuntoEvaluacionPublica(token, a.id)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-sm font-semibold text-brand hover:underline"><FileText className="h-4 w-4" /> {a.nombre}</a></li>
                        ))}
                      </ul>
                    )}
                    <div className="flex justify-end">
                      <Button variant="outline" onClick={() => setComplementar(true)} className="totem:min-h-16 totem:text-xl">Agregar complemento</Button>
                    </div>
                  </div>
                ) : (
                  <div className="mt-4">
                    <FormularioResultado
                      key={`${complementar}-${ev.resultadoVersion}`}
                      evaluacion={ev}
                      modo={info.yaTieneResultado ? "complementar" : "registrar"}
                      etiquetaAutor="Quién realizó la evaluación"
                      onEnviar={(d) => enviarResultadoPublico(token, d)}
                      onListo={() => { setEnviadoComo(info.yaTieneResultado ? "complemento" : "resultado"); setFase("enviado"); }}
                      onCancelar={complementar ? () => setComplementar(false) : undefined}
                      onConflicto={() => void cargar()}
                    />
                  </div>
                )}
              </Card>
            )}
          </>
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
