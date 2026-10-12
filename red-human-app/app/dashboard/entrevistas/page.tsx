"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Video,
  Mic,
  Calendar,
  Clock,
  ShieldCheck,
  Sparkles,
  MessageCircle,
  Copy,
  Check,
  X,
  Wand2,
  ExternalLink,
  UserPlus,
  Zap,
  AlertTriangle,
  RotateCcw,
} from "lucide-react";
import { Card, Badge, Button, Eyebrow } from "@/components/ui";
import { PageHeader } from "@/components/dashboard/parts";
import { PerfilProfundoVista } from "@/components/dashboard/perfil-profundo";
import { usePuedeDecidir } from "@/components/sesion";
import { cn } from "@/lib/utils";
import {
  fetchEntrevistas,
  fetchCandidatos,
  fetchVacantes,
  agendarEntrevista,
  entrevistaInmediata,
  fetchMetricasEntrevistas,
  reabrirEntrevista,
  NOMBRE_CIERRE,
  nombreEtapa,
  type Entrevista,
  type EvaluacionEntrevista,
  type MetricasEntrevistas,
} from "@/lib/api";
import type { Candidato, Vacante } from "@/lib/data";
import { INTERVALO_TABLERO_MS, usePolling } from "@/lib/use-polling";

const proximasMock = [
  { nombre: "Luis Ángel Torres", puesto: "Desarrollador Full-Stack", tipo: "Video", hora: "Hoy · 12:30", estado: "Confirmada" },
  { nombre: "María Fernanda López", puesto: "Cajero(a) de sucursal", tipo: "Voz", hora: "Hoy · 13:15", estado: "Confirmada" },
  { nombre: "Ana Sofía Herrera", puesto: "Auxiliar de almacén", tipo: "Voz", hora: "Hoy · 15:00", estado: "Por confirmar" },
];

const estadoEntrevista: Record<Entrevista["estado"], { label: string; tone: "good" | "neutral" | "warn" | "bad" }> = {
  programada: { label: "Por realizar", tone: "warn" },
  en_curso: { label: "En curso", tone: "neutral" },
  completada: { label: "Completada", tone: "neutral" },
  evaluada: { label: "Evaluada", tone: "good" },
  // Fase 4: cerró por desconexión/tiempo sin turnos suficientes; RH puede reabrir la misma liga.
  interrumpida: { label: "Interrumpida", tone: "bad" },
  parcial: { label: "Parcial", tone: "warn" },
  // 2026-10-11: videollamada cancelada (p. ej. creada por error) — antes no estaba en el mapa y la vista tronaba
  cancelada: { label: "Cancelada", tone: "neutral" },
};
/** Un estado nuevo o desconocido nunca tumba la vista. */
const estadoDe = (estado: string) => estadoEntrevista[estado as Entrevista["estado"]] ?? { label: estado || "—", tone: "neutral" as const };
const recomendacionDe = (r?: string) => recomendacionUI[r as EvaluacionEntrevista["recomendacion"]] ?? { label: "revisión humana", clase: "text-warn" };
const decimal = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(1) : "—");

const recomendacionUI: Record<EvaluacionEntrevista["recomendacion"], { label: string; clase: string }> = {
  avanzar: { label: "avanzar", clase: "text-good" },
  revision: { label: "revisión humana", clase: "text-warn" },
  no_avanzar: { label: "no avanzar", clase: "text-bad" },
};

const REFRESCO_MS = INTERVALO_TABLERO_MS;

export default function Entrevistas() {
  const [entrevistas, setEntrevistas] = useState<Entrevista[]>([]);
  const [metricas, setMetricas] = useState<MetricasEntrevistas | null>(null);
  const [apiViva, setApiViva] = useState(false);
  const [open, setOpen] = useState(false);
  const [seleccionada, setSeleccionada] = useState<string | null>(null);
  const [reabriendo, setReabriendo] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const puedeDecidir = usePuedeDecidir();

  const recargar = useCallback(() => {
    fetchEntrevistas().then((e) => {
      if (e) {
        setEntrevistas(e);
        setApiViva(true);
      }
    });
    fetchMetricasEntrevistas().then((m) => m && setMetricas(m));
  }, []);

  useEffect(() => {
    recargar();
  }, [recargar]);
  usePolling(recargar, REFRESCO_MS); // Fase 4: mismo hook que el resto de los tableros

  const evaluada =
    (seleccionada && entrevistas.find((e) => e.id === seleccionada && e.evaluacion)) ||
    entrevistas.find((e) => e.estado === "evaluada" && e.evaluacion);

  /** Reapertura explícita (Fase 4): archiva el intento anterior en el servidor y vuelve a `programada`. */
  async function reabrir(e: Entrevista) {
    if (!window.confirm(`¿Reabrir la entrevista de ${e.nombre}? El intento anterior se archiva y la misma liga vuelve a servir.`)) return;
    setReabriendo(e.id);
    const r = await reabrirEntrevista(e.id, "Reapertura desde el tablero de Entrevistas");
    setReabriendo(null);
    setAviso(r.ok ? `Entrevista de ${e.nombre} reabierta; comparte de nuevo la liga.` : "No se pudo reabrir la entrevista.");
    setTimeout(() => setAviso(null), 4000);
    if (r.ok) recargar();
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
      <PageHeader title="Entrevistas" subtitle="Motor de entrevistas por voz y video con evidencia y consentimiento.">
        {apiViva && (
          <Badge tone="good" dot>
            En vivo · se actualiza solo
          </Badge>
        )}
        <Button size="sm" onClick={() => setOpen(true)}>
          <Zap className="h-4 w-4" /> Nueva entrevista
        </Button>
      </PageHeader>
      {aviso && (
        <p className="mt-3 rounded-xl border border-border-soft bg-surface-2 px-4 py-2 text-sm text-ink-2">{aviso}</p>
      )}

      {/* Métricas */}
      {metricas && (
        <div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[
            { l: "Entrevistas", v: metricas.total, sub: `${metricas.pendientes} por realizar` },
            { l: "Evaluadas", v: metricas.evaluadas, sub: `${metricas.recomendaciones.avanzar} para avanzar` },
            { l: "Match promedio", v: `${metricas.match_promedio}%`, sub: "contra el perfil" },
            {
              l: "Revisión humana",
              v: metricas.recomendaciones.revision,
              sub: `${metricas.recomendaciones.no_avanzar} no avanzar`,
            },
          ].map((m) => (
            <Card key={m.l} className="p-4">
              <p className="text-xs font-medium text-ink-3">{m.l}</p>
              <p className="font-display mt-1 text-2xl font-bold text-brand tabular">{m.v}</p>
              <p className="mt-0.5 text-[11px] text-ink-3">{m.sub}</p>
            </Card>
          ))}
        </div>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-[1.5fr_1fr]">
        {/* Tu entrevistadora */}
        <Card className="overflow-hidden">
          <div className="relative aspect-[723/295] overflow-hidden bg-[#151517]">
            {metricas?.avatar_activo !== false ? (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img src="/avatar-alma.png" alt="Red Human, entrevistadora en video" className="h-full w-full object-cover" />
            ) : (
              <div className="grid h-full place-items-center">
                <div className="grid h-24 w-24 place-items-center rounded-full bg-gradient-to-br from-brand to-brand-2">
                  <Sparkles className="h-10 w-10 text-white" />
                </div>
              </div>
            )}
            <div className="absolute left-4 top-4 flex items-center gap-2">
              <span className="flex items-center gap-1.5 rounded-full bg-black/50 px-2.5 py-1 font-mono text-[11px] text-white/90 backdrop-blur">
                <Sparkles className="h-3 w-3" /> Red Human · entrevistadora en video
              </span>
              {metricas && (
                <span
                  className={cn(
                    "rounded-full px-2.5 py-1 font-mono text-[11px] backdrop-blur",
                    metricas.avatar_activo ? "bg-good/20 text-good" : "bg-warn/20 text-warn",
                  )}
                >
                  {metricas.avatar_activo ? "● Avatar en video activo" : "● Modo texto (sin ANAM_API_KEY)"}
                </span>
              )}
            </div>
            <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent p-4 pt-10">
              <p className="text-[13px] text-white/85">
                Entrevista en video con voz en español, guion a la medida de cada vacante y evaluación con evidencia al
                terminar.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3 border-b border-border-faint bg-human-soft/50 px-5 py-3">
            <ShieldCheck className="h-4 w-4 shrink-0 text-human" />
            <p className="text-[13px] text-ink-2">
              Cada candidato autoriza la grabación y sabe que conversa con una IA. Consentimiento registrado en bitácora.
            </p>
          </div>

          <div className="grid gap-3 p-5 sm:grid-cols-2">
            <button
              onClick={() => setOpen(true)}
              className="group flex items-start gap-3 rounded-xl border border-border-soft p-4 text-left transition hover:border-brand/50 hover:bg-surface-2/50"
            >
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand">
                <UserPlus className="h-5 w-5" />
              </span>
              <span>
                <span className="block text-sm font-semibold">Entrevista inmediata</span>
                <span className="mt-0.5 block text-xs leading-relaxed text-ink-3">
                  Captura un prospecto nuevo y genera su liga al instante.
                </span>
              </span>
            </button>
            <button
              onClick={() => setOpen(true)}
              className="group flex items-start gap-3 rounded-xl border border-border-soft p-4 text-left transition hover:border-brand/50 hover:bg-surface-2/50"
            >
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-human-soft text-human">
                <Calendar className="h-5 w-5" />
              </span>
              <span>
                <span className="block text-sm font-semibold">Agendar candidato</span>
                <span className="mt-0.5 block text-xs leading-relaxed text-ink-3">
                  Elige a alguien del pipeline y mándale la liga por WhatsApp.
                </span>
              </span>
            </button>
          </div>
        </Card>

        {/* Columna derecha */}
        <div className="flex flex-col gap-4">
          <Card className="p-5">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-brand" />
              <h3 className="font-display text-lg font-bold">Evaluación del agente</h3>
              {evaluada && (
                <span className="ml-auto font-mono text-[11px] text-ink-3">
                  {evaluada.id} · {evaluada.nombre.split(" ")[0]}
                </span>
              )}
            </div>
            {evaluada && evaluada.evaluacion ? (
              <>
                <p className="mt-2 text-sm leading-relaxed text-ink-2">{evaluada.evaluacion.resumen}</p>
                <p className="mt-2 text-sm text-ink-2">
                  Recomendación preliminar:{" "}
                  <b className={recomendacionDe(evaluada.evaluacion.recomendacion).clase}>
                    {recomendacionDe(evaluada.evaluacion.recomendacion).label}
                  </b>{" "}
                  — la decisión final es de RH.
                </p>
                <div className="mt-4 grid grid-cols-3 gap-2 text-center">
                  {[
                    { l: "Experiencia", v: decimal(evaluada.evaluacion.calif_experiencia) },
                    { l: "Comunicación", v: decimal(evaluada.evaluacion.calif_comunicacion) },
                    { l: "Match perfil", v: evaluada.evaluacion.match_perfil != null ? `${evaluada.evaluacion.match_perfil}%` : "—" },
                  ].map((m) => (
                    <div key={m.l} className="rounded-xl bg-surface-2 py-3">
                      <div className="font-display text-lg font-bold text-brand">{m.v}</div>
                      <div className="text-[10px] text-ink-3">{m.l}</div>
                    </div>
                  ))}
                </div>
                {evaluada.evaluacion.fortalezas.length > 0 && (
                  <div className="mt-4 space-y-1.5">
                    {(evaluada.evaluacion.fortalezas ?? []).slice(0, 3).map((f, i) => (
                      <p key={i} className="flex gap-2 text-xs leading-relaxed text-ink-2">
                        <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-good" /> {f}
                      </p>
                    ))}
                  </div>
                )}
                {evaluada.evaluacion.riesgos.length > 0 && (
                  <div className="mt-3 space-y-1.5">
                    {(evaluada.evaluacion.riesgos ?? []).slice(0, 3).map((f, i) => (
                      <p key={i} className="flex gap-2 text-xs leading-relaxed text-ink-2">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" /> {f}
                      </p>
                    ))}
                  </div>
                )}
                {evaluada.evaluacion.evidencia && (
                  <p className="mt-3 rounded-lg bg-surface-2 px-3 py-2 text-xs italic leading-relaxed text-ink-2">
                    “{evaluada.evaluacion.evidencia}”
                  </p>
                )}
                {evaluada.evaluacion.perfil && (
                  <div className="mt-4">
                    <PerfilProfundoVista perfil={evaluada.evaluacion.perfil} compacto />
                  </div>
                )}
                <p className="mt-3 font-mono text-[11px] text-ink-3">
                  Cierre: {NOMBRE_CIERRE[evaluada.cierre ?? ""]}
                  {evaluada.turnosCandidato != null ? ` · ${evaluada.turnosCandidato} respuestas` : ""}
                  {evaluada.intentosPrevios ? ` · ${evaluada.intentosPrevios} intento(s) previo(s)` : ""}
                </p>
              </>
            ) : (
              <p className="mt-2 text-sm leading-relaxed text-ink-2">
                Aquí aparecerá la evaluación de la última entrevista: resumen, calificaciones y una recomendación
                preliminar con evidencia. {apiViva ? "Agenda tu primera entrevista para verla en acción." : "Levanta la API para conectar."}
              </p>
            )}
          </Card>

          <Card className="overflow-hidden">
            <div className="flex items-center justify-between border-b border-border-faint px-5 py-3.5">
              <h3 className="font-display text-lg font-bold">{apiViva ? "Entrevistas" : "Próximas entrevistas"}</h3>
              {apiViva && <span className="font-mono text-[11px] text-ink-3">{entrevistas.length}</span>}
            </div>
            <div className="max-h-[24rem] divide-y divide-border-faint overflow-y-auto">
              {apiViva && entrevistas.length > 0 ? (
                entrevistas.map((e) => (
                  <FilaEntrevista
                    key={e.id}
                    e={e}
                    activa={evaluada?.id === e.id}
                    onVer={() => setSeleccionada(e.id)}
                    onReabrir={puedeDecidir && (e.estado === "interrumpida" || e.estado === "parcial") ? () => reabrir(e) : undefined}
                    reabriendo={reabriendo === e.id}
                  />
                ))
              ) : apiViva ? (
                <p className="px-5 py-6 text-sm text-ink-3">
                  Aún no hay entrevistas. Usa “Nueva entrevista” para generar la primera liga.
                </p>
              ) : (
                proximasMock.map((p) => (
                  <div key={p.nombre} className="flex items-center gap-3 px-5 py-3.5">
                    <span className={cn("grid h-9 w-9 place-items-center rounded-xl", p.tipo === "Video" ? "bg-brand-soft text-brand" : "bg-human-soft text-human")}>
                      {p.tipo === "Video" ? <Video className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold">{p.nombre}</p>
                      <p className="truncate text-xs text-ink-3">{p.puesto}</p>
                    </div>
                    <p className="flex items-center gap-1 font-mono text-[11px] text-ink-2">
                      <Clock className="h-3 w-3" /> {p.hora}
                    </p>
                  </div>
                ))
              )}
            </div>
          </Card>
        </div>
      </div>

      {open && (
        <NuevaEntrevistaModal
          onClose={() => setOpen(false)}
          onCreada={() => {
            recargar();
          }}
        />
      )}

    </div>
  );
}

function FilaEntrevista({
  e,
  activa,
  onVer,
  onReabrir,
  reabriendo = false,
}: {
  e: Entrevista;
  activa: boolean;
  onVer: () => void;
  onReabrir?: () => void;
  reabriendo?: boolean;
}) {
  const [copiada, setCopiada] = useState(false);
  const est = estadoDe(e.estado);
  const liga = typeof window === "undefined" ? `/entrevista/${e.token}` : `${window.location.origin}/entrevista/${e.token}`;

  function copiar() {
    navigator.clipboard.writeText(liga).then(() => {
      setCopiada(true);
      setTimeout(() => setCopiada(false), 1600);
    });
  }

  return (
    <div
      onClick={onVer}
      className={cn(
        "flex cursor-pointer items-center gap-3 px-5 py-3.5 transition hover:bg-surface-2/50",
        activa && "bg-brand-soft/30",
      )}
    >
      <span className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-xl", e.tipo === "avatar" ? "bg-brand-soft text-brand" : "bg-human-soft text-human")}>
        {e.tipo === "avatar" ? <Video className="h-4 w-4" /> : <MessageCircle className="h-4 w-4" />}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold">{e.nombre}</p>
        <p className="truncate text-xs text-ink-3">
          {e.puesto || "Sin vacante"} · {e.creada}
          {e.evaluacion?.match_perfil != null ? ` · match ${e.evaluacion.match_perfil}%` : ""}
        </p>
      </div>
      <Badge tone={est.tone}>{est.label}</Badge>
      {(e.estado === "interrumpida" || e.estado === "parcial") && onReabrir ? (
        <button
          onClick={(ev) => {
            ev.stopPropagation();
            onReabrir();
          }}
          disabled={reabriendo}
          title={`Reintentar Entrevista Red Human (${e.motivo === "sin_respuestas" ? "sin respuestas" : e.motivo === "parcial" ? "parcial" : NOMBRE_CIERRE[e.cierre ?? ""]})`}
          className="flex h-8 shrink-0 items-center gap-1 rounded-lg px-2 text-xs font-medium text-brand transition hover:bg-brand-soft disabled:opacity-50"
        >
          <RotateCcw className={cn("h-3.5 w-3.5", reabriendo && "animate-spin")} /> Reintentar
        </button>
      ) : e.estado === "programada" || e.estado === "en_curso" ? (
        <button
          onClick={(ev) => {
            ev.stopPropagation();
            copiar();
          }}
          title="Copiar liga para el candidato"
          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-ink-3 transition hover:bg-surface-2 hover:text-brand"
        >
          {copiada ? <Check className="h-4 w-4 text-good" /> : <Copy className="h-4 w-4" />}
        </button>
      ) : (
        <a
          href={liga}
          target="_blank"
          rel="noreferrer"
          onClick={(ev) => ev.stopPropagation()}
          title="Abrir sala"
          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-ink-3 transition hover:bg-surface-2 hover:text-brand"
        >
          <ExternalLink className="h-4 w-4" />
        </a>
      )}
    </div>
  );
}

/* ---------------- Modal: nueva entrevista (prospecto o candidato) ---------------- */
function NuevaEntrevistaModal({ onClose, onCreada }: { onClose: () => void; onCreada: () => void }) {
  const [modo, setModo] = useState<"prospecto" | "candidato">("prospecto");
  const [candidatos, setCandidatos] = useState<Candidato[]>([]);
  const [vacantes, setVacantes] = useState<Vacante[]>([]);
  const [codigo, setCodigo] = useState("");
  const [nombre, setNombre] = useState("");
  const [telefono, setTelefono] = useState("");
  const [vacante, setVacante] = useState("");
  const [avisar, setAvisar] = useState(false);
  const [state, setState] = useState<"form" | "loading" | "done" | "sin_api">("form");
  const [resultado, setResultado] = useState<(Entrevista & { liga: string; ia: boolean }) | null>(null);
  const [copiada, setCopiada] = useState(false);

  useEffect(() => {
    fetchCandidatos().then((c) => {
      if (!c) return setState("sin_api");
      setCandidatos(c);
      if (c.length) setCodigo(c[0].id);
    });
    fetchVacantes().then((v) => {
      if (v) {
        setVacantes(v);
        const pub = v.find((x) => x.estado === "Publicada");
        if (pub) setVacante(pub.id);
      }
    });
  }, []);

  async function crear() {
    setState("loading");
    const r =
      modo === "prospecto"
        ? await entrevistaInmediata({
            nombre,
            telefono,
            vacante: vacante || null,
            avisar_whatsapp: avisar && !!telefono,
          })
        : await agendarEntrevista(codigo, avisar);
    if (!r.ok) return setState("sin_api");
    setResultado(r.data);
    setState("done");
    onCreada();
  }

  function copiar() {
    if (!resultado) return;
    navigator.clipboard.writeText(resultado.liga).then(() => {
      setCopiada(true);
      setTimeout(() => setCopiada(false), 1600);
    });
  }

  const puedeCrear = modo === "prospecto" ? nombre.trim().length > 2 : !!codigo;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div className="relative flex h-full w-full max-w-xl flex-col overflow-y-auto border-l border-border-soft bg-bg shadow-2xl">
        <div className="glass sticky top-0 z-10 flex items-center justify-between border-b border-border-soft px-6 py-4">
          <div>
            <Eyebrow>Motor de entrevistas</Eyebrow>
            <h2 className="font-display text-xl font-bold">Nueva entrevista con IA</h2>
          </div>
          <button onClick={onClose} className="grid h-9 w-9 place-items-center rounded-lg text-ink-3 transition hover:bg-surface-2 hover:text-ink">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 p-6">
          {state === "sin_api" && (
            <p className="text-sm leading-relaxed text-ink-2">
              La API no está corriendo. Levántala con <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs">uvicorn app.main:app --reload</code> en{" "}
              <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs">red-human-api</code> y vuelve a intentar.
            </p>
          )}

          {(state === "form" || state === "loading") && (
            <div className="space-y-5">
              {/* Selector de modo */}
              <div className="grid grid-cols-2 gap-2 rounded-xl bg-surface-2 p-1">
                {(
                  [
                    { id: "prospecto", label: "Nuevo prospecto", icon: UserPlus },
                    { id: "candidato", label: "Candidato existente", icon: Calendar },
                  ] as const
                ).map((t) => (
                  <button
                    key={t.id}
                    onClick={() => setModo(t.id)}
                    className={cn(
                      "flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition",
                      modo === t.id ? "bg-bg text-brand shadow" : "text-ink-3 hover:text-ink",
                    )}
                  >
                    <t.icon className="h-4 w-4" /> {t.label}
                  </button>
                ))}
              </div>

              {modo === "prospecto" ? (
                <>
                  <div>
                    <label className="text-sm font-semibold">Nombre del prospecto</label>
                    <input
                      value={nombre}
                      onChange={(e) => setNombre(e.target.value)}
                      placeholder="Nombre y apellidos"
                      className="mt-1.5 h-11 w-full rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none transition focus:border-brand"
                    />
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div>
                      <label className="text-sm font-semibold">WhatsApp (opcional)</label>
                      <input
                        value={telefono}
                        onChange={(e) => setTelefono(e.target.value)}
                        placeholder="10 dígitos"
                        className="mt-1.5 h-11 w-full rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none transition focus:border-brand"
                      />
                    </div>
                    <div>
                      <label className="text-sm font-semibold">Vacante</label>
                      <select
                        value={vacante}
                        onChange={(e) => setVacante(e.target.value)}
                        className="mt-1.5 h-11 w-full rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none transition focus:border-brand"
                      >
                        <option value="">Entrevista general</option>
                        {vacantes.map((v) => (
                          <option key={v.id} value={v.id}>
                            {v.titulo}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                </>
              ) : (
                <div>
                  <label className="text-sm font-semibold">Candidato</label>
                  <select
                    value={codigo}
                    onChange={(e) => setCodigo(e.target.value)}
                    className="mt-1.5 h-11 w-full rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none transition focus:border-brand"
                  >
                    {candidatos.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.nombre} — {c.puesto || "sin vacante"} ({nombreEtapa(c.etapa)})
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <label
                className={cn(
                  "flex cursor-pointer items-center gap-3 rounded-xl border border-border-soft p-4 transition hover:border-brand/40",
                  modo === "prospecto" && !telefono && "opacity-50",
                )}
              >
                <input
                  type="checkbox"
                  checked={avisar}
                  disabled={modo === "prospecto" && !telefono}
                  onChange={(e) => setAvisar(e.target.checked)}
                  className="h-4 w-4"
                />
                <span className="text-sm text-ink-2">Enviar la liga por WhatsApp</span>
              </label>

              <p className="text-xs leading-relaxed text-ink-3">
                La IA genera un guion de entrevista a la medida de la vacante y del perfil, y una liga que puedes
                compartir por cualquier medio. El candidato entra, da su consentimiento y conversa con Red Human.
              </p>

              <Button className="w-full" onClick={crear} disabled={!puedeCrear || state === "loading"}>
                <Wand2 className={cn("h-4 w-4", state === "loading" && "animate-pulse")} />
                {state === "loading" ? "Generando guion con IA…" : "Generar liga y guion"}
              </Button>
            </div>
          )}

          {state === "done" && resultado && (
            <div className="space-y-5">
              <div className="flex items-center gap-2">
                <Badge tone="good" dot>
                  {resultado.ia ? "Guion generado con IA" : "Guion demo"}
                </Badge>
                <Badge tone={resultado.tipo === "avatar" ? "good" : "neutral"}>
                  {resultado.tipo === "avatar" ? "Avatar de video" : "Chat de texto"}
                </Badge>
              </div>

              <div>
                <label className="text-sm font-semibold">Liga para {resultado.nombre}</label>
                <div className="mt-1.5 flex items-center gap-2">
                  <input
                    readOnly
                    value={resultado.liga}
                    className="h-11 flex-1 rounded-xl border border-border-soft bg-surface-2 px-3 font-mono text-xs text-ink-2 outline-none"
                  />
                  <Button variant="outline" size="sm" onClick={copiar}>
                    {copiada ? <Check className="h-4 w-4 text-good" /> : <Copy className="h-4 w-4" />}
                  </Button>
                  <Button variant="outline" size="sm" href={resultado.liga}>
                    <ExternalLink className="h-4 w-4" />
                  </Button>
                </div>
                <p className="mt-1.5 text-xs text-ink-3">
                  Compártela por WhatsApp, correo o QR. Cuando termine, la evaluación aparece sola en este tablero.
                </p>
              </div>

              {resultado.guion?.preguntas && (
                <div>
                  <Eyebrow>Guion de la entrevista</Eyebrow>
                  <ol className="mt-2 space-y-2">
                    {resultado.guion.preguntas.map((p, i) => (
                      <li key={i} className="flex gap-2.5 text-sm leading-relaxed text-ink-2">
                        <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-md bg-brand-soft font-mono text-[10px] font-bold text-brand">
                          {i + 1}
                        </span>
                        {p}
                      </li>
                    ))}
                  </ol>
                </div>
              )}

              <Button variant="secondary" className="w-full" onClick={onClose}>
                Listo
              </Button>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2.5 border-t border-border-faint bg-human-soft/40 px-6 py-3">
          <ShieldCheck className="h-4 w-4 shrink-0 text-human" />
          <p className="text-[12px] text-ink-3">
            La IA entrevista y recomienda; avanzar o descartar siempre lo decide una persona de RH (LFPDPPP).
          </p>
        </div>
      </div>
    </div>
  );
}
