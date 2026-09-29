"use client";

/* Tablero de control (2026-09-28): SOLO datos reales de `GET /metricas/tablero`, filtrados por la Cuenta de la
   sesión en el servidor. Nada de arrays estáticos ni números inventados: sin datos se ven ceros y estados vacíos.
   Revalida con `usePolling` (regla de tableros). Al cambiar de Cuenta, <PorCuenta> remonta la página. */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, BarChart3, ClipboardCheck, Clock, Gauge, ListChecks, Smile, UserCheck } from "lucide-react";
import { Button, Card } from "@/components/ui";
import { PageHeader, KpiCard, EstadoBadge, ScoreRing } from "@/components/dashboard/parts";
import { ActividadChart, FuentesDonut, TiempoChart } from "@/components/dashboard/charts";
import { EmbudoEtapas } from "@/components/dashboard/embudo-etapas";
import { fetchTablero, nombreEtapa, type TableroControl } from "@/lib/api";
import type { EstadoPrefiltro } from "@/lib/data";
import { usePolling } from "@/lib/use-polling";

const COLORES_FUENTE = ["var(--brand)", "var(--human)", "var(--brand-2)", "#8b8c90", "#c9cacd", "#a5a6aa"];

function Vacio({ texto }: { texto: string }) {
  return <p className="grid h-[180px] place-items-center text-center text-sm text-ink-3">{texto}</p>;
}

function Dato({ etiqueta, valor, tono = "text-ink" }: { etiqueta: string; valor: React.ReactNode; tono?: string }) {
  return (
    <div className="rounded-xl border border-border-soft bg-surface-2/40 px-3 py-2.5">
      <p className="text-[11px] text-ink-3">{etiqueta}</p>
      <p className={`mt-0.5 font-display text-xl font-bold tabular ${tono}`}>{valor}</p>
    </div>
  );
}

export default function Tablero() {
  const [t, setT] = useState<TableroControl | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState(false);

  const cargar = useCallback(async () => {
    const r = await fetchTablero();
    if (r) {
      setT(r);
      setError(false);
    } else setError(true);
    setCargando(false);
  }, []);
  useEffect(() => {
    void cargar();
  }, [cargar]);
  usePolling(cargar);

  const serieNuevos = t?.actividad.map((d) => d.candidatos) ?? [];
  const serieEntrevistas = t?.actividad.map((d) => d.entrevistas) ?? [];

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
      <PageHeader title="Tablero de control" subtitle="Vista general de reclutamiento y operación de tu Cuenta">
        <Button href="/dashboard/vacantes" size="sm">
          Nueva vacante <ArrowRight className="h-4 w-4" />
        </Button>
      </PageHeader>

      {error && !t && (
        <Card className="mt-6 p-6 text-sm text-ink-2">No se pudo cargar el tablero. Revisa la conexión con la API e intenta de nuevo.</Card>
      )}

      {/* KPIs */}
      <div className="mt-7 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {cargando && !t ? (
          [0, 1, 2, 3].map((i) => <div key={i} className="h-32 animate-pulse rounded-2xl border border-border-soft bg-surface-2/60" />)
        ) : t ? (
          <>
            <KpiCard label="Candidatos activos en el pipeline" value={t.kpis.candidatosActivos.toLocaleString("es-MX")}
              detalle={`${t.kpis.candidatosNuevos7d} nuevos en 7 días`} spark={serieNuevos} tone="brand" />
            <KpiCard label="Colaboradores activos" value={t.kpis.colaboradoresActivos.toLocaleString("es-MX")}
              detalle={`${t.kpis.altas30d} alta${t.kpis.altas30d === 1 ? "" : "s"} en los últimos 30 días`} tone="good" />
            <KpiCard label="Vacantes publicadas" value={String(t.kpis.vacantesPublicadas)} detalle="Visibles en portal y WhatsApp" tone="human" />
            <KpiCard label="Entrevistas (7 días)" value={String(serieEntrevistas.reduce((a, b) => a + b, 0))}
              detalle="Red Human finalizadas + humanas" spark={serieEntrevistas} tone="human" />
          </>
        ) : null}
      </div>

      {t && (
        <>
          {/* Onboarding · Evaluaciones · Desempeño y Clima */}
          <div className="mt-4 grid gap-4 lg:grid-cols-3">
            <Card className="p-5">
              <div className="flex items-center justify-between">
                <h3 className="flex items-center gap-2 font-display text-lg font-bold"><UserCheck className="h-5 w-5 text-good" /> Onboarding</h3>
                <Link href="/dashboard/onboarding" className="text-sm font-medium text-brand hover:underline">Abrir</Link>
              </div>
              {t.onboarding ? (
                <>
                  <div className="mt-4 grid grid-cols-3 gap-2">
                    <Dato etiqueta="Activos" valor={t.onboarding.activos} />
                    <Dato etiqueta="Tareas atrasadas" valor={t.onboarding.tareasAtrasadas} tono={t.onboarding.tareasAtrasadas ? "text-bad" : "text-ink"} />
                    <Dato etiqueta="Avance promedio" valor={t.onboarding.avancePromedio == null ? "—" : `${t.onboarding.avancePromedio}%`} />
                  </div>
                  <p className="mt-3 text-[12px] text-ink-3">
                    {t.onboarding.activos === 0
                      ? "Sin procesos de Onboarding activos."
                      : [t.onboarding.sinTareas ? `${t.onboarding.sinTareas} sin tareas generadas` : "", t.onboarding.listosParaCerrar ? `${t.onboarding.listosParaCerrar} listo(s) para cerrar` : ""].filter(Boolean).join(" · ") || "Documentos aprobados y tareas realizadas sobre lo aplicable."}
                  </p>
                </>
              ) : <p className="mt-4 text-sm text-ink-3">Módulo no disponible en este servidor.</p>}
            </Card>

            <Card className="p-5">
              <h3 className="flex items-center gap-2 font-display text-lg font-bold"><ClipboardCheck className="h-5 w-5 text-brand" /> Evaluaciones</h3>
              {t.evaluaciones ? (
                <>
                  <div className="mt-4 grid grid-cols-2 gap-2">
                    <Dato etiqueta="Pendientes" valor={t.evaluaciones.pendientes} />
                    <Dato etiqueta="Resultado pendiente" valor={t.evaluaciones.realizadasSinResultado} tono={t.evaluaciones.realizadasSinResultado ? "text-warn" : "text-ink"} />
                  </div>
                  <p className="mt-3 text-[12px] text-ink-3">
                    {t.evaluaciones.nuevosResultados ? `${t.evaluaciones.nuevosResultados} resultado(s) nuevo(s) sin abrir · ` : ""}
                    {t.evaluaciones.enEsperaConsentimiento ? `${t.evaluaciones.enEsperaConsentimiento} en espera de consentimiento · ` : ""}
                    {t.evaluaciones.conResultado} con resultado{t.evaluaciones.noRealizadas ? ` · ${t.evaluaciones.noRealizadas} no realizada(s)` : ""} (postulaciones activas)
                  </p>
                </>
              ) : <p className="mt-4 text-sm text-ink-3">Módulo no disponible en este servidor.</p>}
            </Card>

            <Card className="p-5">
              <div className="flex items-center justify-between">
                <h3 className="flex items-center gap-2 font-display text-lg font-bold"><Gauge className="h-5 w-5 text-human" /> Desempeño y Clima</h3>
                <Link href="/dashboard/desempeno" className="text-sm font-medium text-brand hover:underline">Abrir</Link>
              </div>
              {t.desempeno ? (
                <>
                  <div className="mt-4 grid grid-cols-2 gap-2">
                    <Dato etiqueta="Ciclos activos" valor={t.desempeno.activos} />
                    <Dato etiqueta="Avance" valor={t.desempeno.avance == null ? "—" : `${t.desempeno.avance}%`} />
                  </div>
                  <p className="mt-3 text-[12px] text-ink-3">
                    {t.desempeno.activos
                      ? `${t.desempeno.personasCompletadas} de ${t.desempeno.personasIncluidas} evaluaciones completadas`
                      : "Sin evaluaciones de desempeño en curso"}
                    {t.desempeno.borradores ? ` · ${t.desempeno.borradores} en borrador` : ""}
                  </p>
                  {t.desempeno.ciclos.length > 0 && (
                    <ul className="mt-2 space-y-1 text-[12px]">
                      {t.desempeno.ciclos.map((c) => (
                        <li key={c.id} className="flex justify-between gap-2"><span className="truncate text-ink-2">{c.nombre}</span><span className="tabular text-ink-3">{c.completadas}/{c.incluidas} · {c.porcentaje}%</span></li>
                      ))}
                    </ul>
                  )}
                </>
              ) : <p className="mt-4 text-sm text-ink-3">Módulo de Desempeño no disponible.</p>}
              {t.clima && (
                <p className="mt-3 flex items-center gap-1.5 border-t border-border-faint pt-2 text-[12px] text-ink-3">
                  <Smile className="h-3.5 w-3.5" /> Clima: {t.clima.abiertas} encuesta{t.clima.abiertas === 1 ? "" : "s"} abierta{t.clima.abiertas === 1 ? "" : "s"}
                  {t.clima.borradores ? ` · ${t.clima.borradores} en borrador` : ""}
                </p>
              )}
            </Card>
          </div>

          {/* Actividad y fuentes */}
          <div className="mt-4 grid gap-4 lg:grid-cols-3">
            <Card className="p-5 lg:col-span-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="font-display text-lg font-bold">Actividad</h3>
                  <p className="text-sm text-ink-3">Postulaciones nuevas y entrevistas · últimos 7 días</p>
                </div>
                <div className="flex items-center gap-4 text-xs">
                  <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-brand" /> Candidatos</span>
                  <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-human" /> Entrevistas</span>
                </div>
              </div>
              <div className="mt-4">
                {serieNuevos.some(Boolean) || serieEntrevistas.some(Boolean)
                  ? <ActividadChart data={t.actividad} />
                  : <Vacio texto="Sin postulaciones ni entrevistas en los últimos 7 días." />}
              </div>
            </Card>

            <Card className="p-5">
              <h3 className="font-display text-lg font-bold">Fuente de candidatos</h3>
              <p className="text-sm text-ink-3">Candidatos activos por origen</p>
              <div className="mt-6">
                {t.fuentes.length
                  ? <FuentesDonut data={t.fuentes.map((f, i) => ({ ...f, color: COLORES_FUENTE[i % COLORES_FUENTE.length] }))} />
                  : <Vacio texto="Aún no hay candidatos activos." />}
              </div>
            </Card>
          </div>

          {/* Embudo · tiempo de contratación · pendientes de RH */}
          <div className="mt-4 grid gap-4 lg:grid-cols-3">
            <Card className="p-5">
              <h3 className="font-display text-lg font-bold">Embudo de selección</h3>
              <p className="text-sm text-ink-3">Candidatos activos por etapa · clic para abrir el Kanban filtrado</p>
              <EmbudoEtapas />
            </Card>

            <Card className="p-5">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-display text-lg font-bold">Tiempo de contratación</h3>
                  <p className="text-sm text-ink-3">Días de la postulación al alta · por mes</p>
                </div>
                {t.tiempoContratacion.promedio != null && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2.5 py-1 text-xs font-semibold text-ink-2">
                    <Clock className="h-3.5 w-3.5" /> {t.tiempoContratacion.promedio} días
                  </span>
                )}
              </div>
              <div className="mt-4">
                {t.tiempoContratacion.serie.length
                  ? <TiempoChart data={t.tiempoContratacion.serie} />
                  : <Vacio texto="Todavía no hay altas en los últimos 6 meses." />}
              </div>
            </Card>

            <Card className="p-5">
              <h3 className="flex items-center gap-2 font-display text-lg font-bold"><ListChecks className="h-5 w-5 text-brand" /> Pendientes para RH</h3>
              <p className="text-sm text-ink-3">Calculados con los datos de tu Cuenta</p>
              {t.pendientesRH.length ? (
                <ul className="mt-4 space-y-2">
                  {t.pendientesRH.map((a) => (
                    <li key={a.tipo}>
                      <Link href={a.ruta} className="flex items-start gap-2 rounded-xl border border-border-soft p-2.5 text-[13px] text-ink-2 transition hover:border-brand/50">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn" /> {a.texto}
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-6 flex items-center gap-2 text-sm text-ink-3"><BarChart3 className="h-4 w-4" /> Sin pendientes por ahora.</p>
              )}
            </Card>
          </div>

          {/* Candidatos recientes */}
          <Card className="mt-4">
            <div className="flex items-center justify-between border-b border-border-faint p-5">
              <div>
                <h3 className="font-display text-lg font-bold">Candidatos recientes</h3>
                <p className="text-sm text-ink-3">Últimas postulaciones activas</p>
              </div>
              <Link href="/dashboard/candidatos" className="text-sm font-medium text-brand hover:underline">Ver todos</Link>
            </div>
            {t.recientes.length ? (
              <div className="divide-y divide-border-faint">
                {t.recientes.map((c) => (
                  <div key={c.id} className="flex items-center gap-4 px-5 py-3.5 transition hover:bg-surface-2/50">
                    <ScoreRing score={c.score} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold">{c.nombre}</p>
                      <p className="truncate text-xs text-ink-3">{[c.puesto, c.ubicacion, nombreEtapa(c.etapa)].filter(Boolean).join(" · ")}</p>
                    </div>
                    <span className="hidden font-mono text-[11px] text-ink-3 sm:block">{c.fuente}</span>
                    <EstadoBadge estado={c.estado as EstadoPrefiltro} />
                    <span className="hidden w-20 text-right font-mono text-[11px] text-ink-3 md:block">{c.aplicado}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="p-5 text-sm text-ink-3">Aún no hay postulaciones activas en esta Cuenta.</p>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
