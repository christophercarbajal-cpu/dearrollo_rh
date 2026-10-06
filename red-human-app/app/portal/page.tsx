"use client";

/* Portal público de vacantes — rediseño 2026-09-17 («Plus»):
   - hero con buscador único (título, área, empresa, palabras clave, ubicación);
   - chips rápidos (modalidad, «Nuevas esta semana», «Con sueldo publicado») + panel de filtros
     avanzados (Estado → Municipio, área, seniority, rango de sueldo, empresa) plegable en móvil;
   - orden (recientes / mayor sueldo / A-Z), filtros sincronizados con la URL (liga compartible);
   - tarjetas con logo, sueldo destacado, badges y CTA; esqueletos y estado vacío.
   Postularse sigue siendo UN paso (/aplicar/[slug]); no se agregan pantallas. */

import { useCallback, useEffect, useMemo, useState } from "react";
import { parsearUbicacion } from "@/lib/ubicacion";
import {
  MapPin,
  Building2,
  Briefcase,
  Banknote,
  Filter,
  X,
  Sparkles,
  ArrowRight,
  Search,
  ShieldCheck,
  Zap,
  Clock,
  ChevronDown,
  ArrowUpDown,
  Bot,
  Link2,
  Check,
} from "lucide-react";
import { Logo, Card, Badge, Button, Eyebrow } from "@/components/ui";
import { ThemeToggle } from "@/components/theme-toggle";
import { fetchCuentaPublica, fetchVacantesPublicas, urlArchivo } from "@/lib/api";
import type { Vacante } from "@/lib/data";
import { cn } from "@/lib/utils";

const MODALIDADES: Vacante["modalidad"][] = ["Presencial", "Híbrido", "Remoto"];
const SENIORITIES = ["Practicante", "Junior", "Semi-senior", "Senior", "Gerencial", "Directivo"];
type Orden = "recientes" | "sueldo" | "alfabetico";
const ORDENES: { id: Orden; nombre: string }[] = [
  { id: "recientes", nombre: "Más recientes" },
  { id: "sueldo", nombre: "Mayor sueldo" },
  { id: "alfabetico", nombre: "A – Z" },
];
const DIAS_NUEVA = 7;

const formatoMXN = new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 0 });

/** Normaliza para buscar/comparar: sin acentos, minúsculas, espacios simples. */
function norm(s: string | undefined | null) {
  return (s ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();
}

/** Número más alto del sueldo (estructurado si existe; si no, del texto). null = «A convenir». */
function sueldoMaximo(v: Vacante): number | null {
  if (v.sueldoHasta || v.sueldoDesde) return Math.max(v.sueldoHasta ?? 0, v.sueldoDesde ?? 0) || null;
  if (!v.sueldo) return null;
  const numeros = (v.sueldo.match(/[\d,]+/g) ?? []).map((n) => parseInt(n.replace(/,/g, ""), 10)).filter((n) => Number.isFinite(n) && n > 0);
  return numeros.length ? Math.max(...numeros) : null;
}

function esNueva(v: Vacante) {
  if (!v.publicadaEn) return false;
  const t = new Date(v.publicadaEn).getTime();
  return Number.isFinite(t) && Date.now() - t < DIAS_NUEVA * 86_400_000;
}

function haceTexto(iso?: string | null) {
  if (!iso) return "";
  const dias = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (dias <= 0) return "Publicada hoy";
  if (dias === 1) return "Publicada ayer";
  if (dias < 30) return `Hace ${dias} días`;
  const meses = Math.floor(dias / 30);
  return `Hace ${meses} mes${meses > 1 ? "es" : ""}`;
}

function tieneEntrevistaRedHuman(v: Vacante) {
  return Boolean(v.enfoqueEntrevista);
}

interface Filtros {
  q: string;
  estado: string;
  municipio: string;
  area: string;
  empresa: string;
  seniority: string;
  modalidades: string[];
  sueldoMin: number;
  nuevas: boolean;
  conSueldo: boolean;
  orden: Orden;
}

const FILTROS_VACIOS: Filtros = {
  q: "", estado: "", municipio: "", area: "", empresa: "", seniority: "", modalidades: [], sueldoMin: 0, nuevas: false, conSueldo: false, orden: "recientes",
};

function filtrosDesdeUrl(params: URLSearchParams): Filtros {
  const orden = params.get("orden") as Orden | null;
  return {
    q: params.get("q") ?? "",
    estado: params.get("estado") ?? "",
    municipio: params.get("municipio") ?? "",
    area: params.get("area") ?? "",
    empresa: params.get("empresa") ?? "",
    seniority: params.get("seniority") ?? "",
    modalidades: (params.get("modalidad") ?? "").split(",").filter(Boolean),
    sueldoMin: Number(params.get("sueldo") ?? 0) || 0,
    nuevas: params.get("nuevas") === "1",
    conSueldo: params.get("consueldo") === "1",
    orden: orden && ORDENES.some((o) => o.id === orden) ? orden : "recientes",
  };
}

function filtrosAUrl(f: Filtros, cuenta: string) {
  const p = new URLSearchParams();
  if (cuenta) p.set("cuenta", cuenta);
  if (f.q) p.set("q", f.q);
  if (f.estado) p.set("estado", f.estado);
  if (f.municipio) p.set("municipio", f.municipio);
  if (f.area) p.set("area", f.area);
  if (f.empresa) p.set("empresa", f.empresa);
  if (f.seniority) p.set("seniority", f.seniority);
  if (f.modalidades.length) p.set("modalidad", f.modalidades.join(","));
  if (f.sueldoMin > 0) p.set("sueldo", String(f.sueldoMin));
  if (f.nuevas) p.set("nuevas", "1");
  if (f.conSueldo) p.set("consueldo", "1");
  if (f.orden !== "recientes") p.set("orden", f.orden);
  const s = p.toString();
  return s ? `?${s}` : "";
}

export default function Portal() {
  const [vacantes, setVacantes] = useState<Vacante[]>([]);
  const [cargando, setCargando] = useState(true);
  // Portal por Cuenta (/portal?cuenta=<slug>) — sin parámetro, bolsa global.
  const [cuentaParam, setCuentaParam] = useState("");
  const [cuentaPortal, setCuentaPortal] = useState<{ nombre: string; logoUrl: string } | null>(null);
  const [portalNoEncontrado, setPortalNoEncontrado] = useState(false);
  const [f, setF] = useState<Filtros>(FILTROS_VACIOS);
  const [panelAbierto, setPanelAbierto] = useState(false);
  const [ligaCopiada, setLigaCopiada] = useState(false);

  useEffect(() => {
    const params = typeof window === "undefined" ? new URLSearchParams() : new URLSearchParams(window.location.search);
    const cuenta = params.get("cuenta") ?? params.get("cuenta_id") ?? "";
    setCuentaParam(cuenta);
    setF(filtrosDesdeUrl(params));
    void (async () => {
      if (cuenta) {
        const cu = await fetchCuentaPublica(cuenta);
        if (!cu) {
          setPortalNoEncontrado(true);
          setCargando(false);
          return;
        }
        setCuentaPortal({ nombre: cu.nombre, logoUrl: cu.logoUrl });
      }
      const v = await fetchVacantesPublicas(cuenta);
      setVacantes(v ?? []);
      setCargando(false);
    })();
  }, []);

  /** Los filtros viven en la URL (liga compartible, «atrás» del navegador respeta el estado). */
  useEffect(() => {
    if (cargando || typeof window === "undefined") return;
    const destino = `${window.location.pathname}${filtrosAUrl(f, cuentaParam)}`;
    if (destino !== `${window.location.pathname}${window.location.search}`) window.history.replaceState(null, "", destino);
  }, [f, cuentaParam, cargando]);

  const set = useCallback(<K extends keyof Filtros>(k: K, v: Filtros[K]) => setF((prev) => ({ ...prev, [k]: v })), []);

  const ubicacionDe = useCallback((v: Vacante) => {
    if (v.ubicacionEstado) return { estado: v.ubicacionEstado, municipio: v.ubicacionMunicipio ?? "" };
    return parsearUbicacion(v.ubicacion ?? "");
  }, []);

  const estados = useMemo(
    () => Array.from(new Set(vacantes.map((v) => ubicacionDe(v).estado).filter(Boolean))).sort((a, b) => a.localeCompare(b, "es")),
    [vacantes, ubicacionDe],
  );
  const municipios = useMemo(
    () => Array.from(new Set(vacantes.filter((v) => ubicacionDe(v).estado === f.estado).map((v) => ubicacionDe(v).municipio).filter(Boolean))).sort((a, b) => a.localeCompare(b, "es")),
    [vacantes, f.estado, ubicacionDe],
  );
  /** Listas deduplicadas sin importar mayúsculas/acentos; se muestra la primera forma capturada. */
  const dedupe = (valores: (string | undefined)[]) => {
    const vistas = new Map<string, string>();
    for (const x of valores) {
      const a = (x ?? "").trim();
      if (!a) continue;
      const clave = norm(a);
      if (!vistas.has(clave)) vistas.set(clave, a);
    }
    return Array.from(vistas.values()).sort((a, b) => a.localeCompare(b, "es"));
  };
  const areas = useMemo(() => dedupe(vacantes.map((v) => v.area)), [vacantes]);
  const empresas = useMemo(() => dedupe(vacantes.map((v) => v.nombreEmpresa ?? v.empresa)), [vacantes]);
  const seniorities = useMemo(() => SENIORITIES.filter((s) => vacantes.some((v) => norm(v.seniority) === norm(s))), [vacantes]);

  const sueldoTope = useMemo(() => {
    const valores = vacantes.map(sueldoMaximo).filter((n): n is number => n !== null);
    if (!valores.length) return 60000;
    return Math.max(60000, Math.ceil(Math.max(...valores) / 5000) * 5000);
  }, [vacantes]);

  const filtradas = useMemo(() => {
    const q = norm(f.q);
    const lista = vacantes.filter((v) => {
      if (q) {
        const pajar = norm([v.titulo, v.area, v.nombreEmpresa ?? v.empresa, v.ubicacion, v.resumen, ...(v.palabrasClave ?? [])].join(" "));
        if (!q.split(" ").every((palabra) => pajar.includes(palabra))) return false;
      }
      if (f.estado || f.municipio) {
        const u = ubicacionDe(v);
        if (f.estado && u.estado !== f.estado) return false;
        if (f.municipio && u.municipio !== f.municipio) return false;
      }
      if (f.area && norm(v.area) !== norm(f.area)) return false;
      if (f.empresa && norm(v.nombreEmpresa ?? v.empresa) !== norm(f.empresa)) return false;
      if (f.seniority && norm(v.seniority) !== norm(f.seniority)) return false;
      if (f.modalidades.length && !f.modalidades.includes(v.modalidad)) return false;
      if (f.nuevas && !esNueva(v)) return false;
      const max = sueldoMaximo(v);
      if (f.conSueldo && max === null) return false;
      if (f.sueldoMin > 0 && max !== null && max < f.sueldoMin) return false;
      return true;
    });
    const orden = f.orden;
    return [...lista].sort((a, b) => {
      if (orden === "sueldo") return (sueldoMaximo(b) ?? -1) - (sueldoMaximo(a) ?? -1);
      if (orden === "alfabetico") return a.titulo.localeCompare(b.titulo, "es");
      return new Date(b.publicadaEn ?? 0).getTime() - new Date(a.publicadaEn ?? 0).getTime();
    });
  }, [vacantes, f, ubicacionDe]);

  const nActivos =
    (f.estado ? 1 : 0) + (f.municipio ? 1 : 0) + (f.area ? 1 : 0) + (f.empresa ? 1 : 0) + (f.seniority ? 1 : 0) +
    f.modalidades.length + (f.sueldoMin > 0 ? 1 : 0) + (f.nuevas ? 1 : 0) + (f.conSueldo ? 1 : 0) + (f.q ? 1 : 0);
  const limpiar = () => setF({ ...FILTROS_VACIOS, orden: f.orden });
  const toggleModalidad = (m: string) => set("modalidades", f.modalidades.includes(m) ? f.modalidades.filter((x) => x !== m) : [...f.modalidades, m]);

  async function copiarLiga() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setLigaCopiada(true);
      setTimeout(() => setLigaCopiada(false), 1800);
    } catch {}
  }

  const nuevasCount = vacantes.filter(esNueva).length;

  return (
    <main className="min-h-svh bg-bg">
      <header className="glass sticky top-0 z-30 border-b border-border-soft">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-3.5">
          <div className="flex items-center gap-3">
            <Logo />
            {cuentaPortal && (
              <span className="hidden items-center gap-2 border-l border-border-soft pl-3 text-sm font-semibold text-ink sm:flex">
                {cuentaPortal.logoUrl && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={urlArchivo(cuentaPortal.logoUrl)} alt="" className="h-6 w-auto max-w-[96px] object-contain" />
                )}
                {cuentaPortal.nombre}
              </span>
            )}
          </div>
          <ThemeToggle />
        </div>
      </header>

      {/* ---------- HERO + BUSCADOR ---------- */}
      <section className="relative overflow-hidden border-b border-border-soft">
        <div className="grid-bg pointer-events-none absolute inset-0" />
        <div className="pointer-events-none absolute inset-x-0 top-0 h-full bg-[radial-gradient(ellipse_60%_55%_at_50%_0%,rgba(238,68,68,0.14),transparent_70%)]" />

        <div className="relative mx-auto max-w-4xl px-5 pb-12 pt-14 text-center sm:pb-16 sm:pt-20">
          <Badge tone="brand" dot className="mx-auto">
            <Sparkles className="h-3 w-3" /> Bolsa de trabajo · {cuentaPortal ? cuentaPortal.nombre : "Red Human AI"}
          </Badge>
          {cuentaPortal?.logoUrl && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={urlArchivo(cuentaPortal.logoUrl)} alt={cuentaPortal.nombre} className="mx-auto mt-5 h-14 w-auto max-w-[220px] object-contain" />
          )}
          {portalNoEncontrado && (
            <p className="mx-auto mt-5 max-w-md rounded-xl border border-warn/30 bg-warn-soft/40 px-4 py-2 text-sm text-warn">
              No encontramos la bolsa de trabajo «{cuentaParam}». Revisa la liga o consulta la bolsa general.
            </p>
          )}

          <h1 className="font-display mt-5 text-4xl font-extrabold leading-[1.05] tracking-tight sm:text-5xl lg:text-6xl">
            Encuentra tu <span className="brand-gradient-text">próxima oportunidad</span>
          </h1>
          <p className="mx-auto mt-4 max-w-xl text-base leading-relaxed text-ink-2 sm:text-lg">
            Postúlate en minutos: Red Human revisa tu perfil y el equipo de Recursos Humanos te contacta por WhatsApp.
          </p>

          {/* Buscador único */}
          <label className="glass mx-auto mt-8 flex max-w-2xl items-center gap-3 rounded-2xl border border-border-soft px-4 py-2.5 shadow-lg transition focus-within:border-brand focus-within:ring-2 focus-within:ring-brand/20">
            <Search className="h-5 w-5 shrink-0 text-brand" />
            <input
              value={f.q}
              onChange={(e) => set("q", e.target.value)}
              placeholder="Puesto, área, empresa, ciudad o palabra clave…"
              className="h-10 w-full bg-transparent text-[15px] text-ink outline-none placeholder:text-ink-3"
              aria-label="Buscar vacantes"
            />
            {f.q && (
              <button onClick={() => set("q", "")} className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-ink-3 transition hover:bg-surface-2 hover:text-ink" aria-label="Borrar búsqueda">
                <X className="h-4 w-4" />
              </button>
            )}
          </label>

          {/* Chips rápidos */}
          <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
            {MODALIDADES.map((m) => (
              <Chip key={m} activo={f.modalidades.includes(m)} onClick={() => toggleModalidad(m)}>
                <Briefcase className="h-3 w-3" /> {m}
              </Chip>
            ))}
            <Chip activo={f.nuevas} onClick={() => set("nuevas", !f.nuevas)}>
              <Zap className="h-3 w-3" /> Nuevas esta semana{nuevasCount ? ` · ${nuevasCount}` : ""}
            </Chip>
            <Chip activo={f.conSueldo} onClick={() => set("conSueldo", !f.conSueldo)}>
              <Banknote className="h-3 w-3" /> Con sueldo publicado
            </Chip>
          </div>

          <div className="mt-7 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-xs sm:text-sm">
            <span className="inline-flex items-center gap-1.5 text-ink-2">
              <Briefcase className="h-4 w-4 text-brand" />
              <b className="text-ink">{vacantes.length}</b> vacante{vacantes.length !== 1 ? "s" : ""} abierta{vacantes.length !== 1 ? "s" : ""}
            </span>
            <span className="inline-flex items-center gap-1.5 text-ink-2">
              <Zap className="h-4 w-4 text-brand" /> Respuesta en menos de 24 h
            </span>
            <span className="inline-flex items-center gap-1.5 text-ink-2">
              <ShieldCheck className="h-4 w-4 text-good" /> Datos protegidos (LFPDPPP)
            </span>
          </div>
        </div>
      </section>

      <div className="mx-auto max-w-6xl px-5 py-8 sm:py-12">
        {/* Barra de resultados: conteo, orden, filtros avanzados (móvil), copiar liga */}
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-ink-2">
            <b className="font-display text-lg text-ink">{cargando ? "…" : filtradas.length}</b> vacante{filtradas.length !== 1 ? "s" : ""}
            {nActivos > 0 && (
              <button onClick={limpiar} className="ml-2 inline-flex items-center gap-1 rounded-full border border-border-soft bg-surface px-2 py-0.5 text-[11px] font-semibold text-ink-3 transition hover:border-brand/40 hover:text-brand">
                <X className="h-3 w-3" /> Limpiar {nActivos} filtro{nActivos !== 1 ? "s" : ""}
              </button>
            )}
          </p>
          <div className="flex items-center gap-2">
            <label className="flex h-9 items-center gap-1.5 rounded-xl border border-border-soft bg-surface px-3 text-xs font-medium text-ink-2">
              <ArrowUpDown className="h-3.5 w-3.5 text-brand" />
              <select value={f.orden} onChange={(e) => set("orden", e.target.value as Orden)} className="bg-transparent text-xs font-semibold text-ink outline-none" aria-label="Ordenar">
                {ORDENES.map((o) => (
                  <option key={o.id} value={o.id}>{o.nombre}</option>
                ))}
              </select>
            </label>
            <button
              onClick={copiarLiga}
              title="Copiar liga con estos filtros"
              className="grid h-9 w-9 place-items-center rounded-xl border border-border-soft bg-surface text-ink-3 transition hover:border-brand/40 hover:text-brand"
              aria-label="Copiar liga"
            >
              {ligaCopiada ? <Check className="h-4 w-4 text-good" /> : <Link2 className="h-4 w-4" />}
            </button>
            <button
              onClick={() => setPanelAbierto((a) => !a)}
              className={cn(
                "flex h-9 items-center gap-1.5 rounded-xl border px-3 text-xs font-semibold transition lg:hidden",
                panelAbierto ? "border-brand/40 bg-brand-soft text-brand" : "border-border-soft bg-surface text-ink-2",
              )}
              aria-expanded={panelAbierto}
            >
              <Filter className="h-3.5 w-3.5" /> Filtros{nActivos ? ` (${nActivos})` : ""}
              <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", panelAbierto && "rotate-180")} />
            </button>
          </div>
        </div>

        <div className="grid gap-6 lg:grid-cols-[272px_1fr]">
          {/* Panel de filtros avanzados (siempre visible en escritorio; plegable en móvil) */}
          <aside className={cn("flex-col gap-4 lg:sticky lg:top-24 lg:flex lg:self-start", panelAbierto ? "flex" : "hidden")}>
            <Card className="border-border-soft p-5 shadow-sm">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-sm font-semibold text-ink">
                  <Filter className="h-4 w-4 text-brand" /> Filtros avanzados
                </span>
                {nActivos > 0 && (
                  <button onClick={limpiar} className="flex items-center gap-1 text-xs font-medium text-ink-3 transition hover:text-brand">
                    <X className="h-3 w-3" /> Limpiar
                  </button>
                )}
              </div>

              <Campo etiqueta="Estado">
                <select value={f.estado} onChange={(e) => setF({ ...f, estado: e.target.value, municipio: "" })} className={claseSelect}>
                  <option value="">Todo México</option>
                  {estados.map((u) => <option key={u} value={u}>{u}</option>)}
                </select>
              </Campo>
              {f.estado && municipios.length > 0 && (
                <Campo etiqueta={f.estado === "Ciudad de México" ? "Alcaldía" : "Municipio"}>
                  <select value={f.municipio} onChange={(e) => set("municipio", e.target.value)} className={claseSelect}>
                    <option value="">Todos</option>
                    {municipios.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </Campo>
              )}
              <Campo etiqueta="Área / Departamento">
                <select value={f.area} onChange={(e) => set("area", e.target.value)} className={claseSelect}>
                  <option value="">Todas</option>
                  {areas.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </Campo>
              {empresas.length > 1 && (
                <Campo etiqueta="Empresa">
                  <select value={f.empresa} onChange={(e) => set("empresa", e.target.value)} className={claseSelect}>
                    <option value="">Todas</option>
                    {empresas.map((a) => <option key={a} value={a}>{a}</option>)}
                  </select>
                </Campo>
              )}
              {seniorities.length > 0 && (
                <Campo etiqueta="Nivel de experiencia">
                  <div className="flex flex-wrap gap-1.5">
                    {seniorities.map((s) => (
                      <Chip key={s} activo={norm(f.seniority) === norm(s)} onClick={() => set("seniority", norm(f.seniority) === norm(s) ? "" : s)} compacto>
                        {s}
                      </Chip>
                    ))}
                  </div>
                </Campo>
              )}
              <Campo etiqueta="Modalidad">
                <div className="flex flex-wrap gap-1.5">
                  {MODALIDADES.map((m) => (
                    <Chip key={m} activo={f.modalidades.includes(m)} onClick={() => toggleModalidad(m)} compacto>{m}</Chip>
                  ))}
                </div>
              </Campo>
              <Campo
                etiqueta="Sueldo mínimo"
                extra={<span className="font-mono text-xs font-semibold text-brand">{f.sueldoMin > 0 ? formatoMXN.format(f.sueldoMin) : "Cualquiera"}</span>}
              >
                <input
                  type="range"
                  min={0}
                  max={sueldoTope}
                  step={1000}
                  value={f.sueldoMin}
                  onChange={(e) => set("sueldoMin", Number(e.target.value))}
                  className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-surface-2 accent-[var(--brand)]"
                  aria-label="Sueldo mínimo"
                />
                <div className="flex items-center justify-between font-mono text-[10px] text-ink-3">
                  <span>$0</span>
                  <span>{formatoMXN.format(sueldoTope)}</span>
                </div>
                <label className="mt-1 flex cursor-pointer items-center gap-2 text-[12px] text-ink-2">
                  <input type="checkbox" checked={f.conSueldo} onChange={(e) => set("conSueldo", e.target.checked)} className="accent-[var(--brand)]" />
                  Solo con sueldo publicado
                </label>
              </Campo>
              <Campo etiqueta="Publicación">
                <label className="flex cursor-pointer items-center gap-2 text-[13px] text-ink-2">
                  <input type="checkbox" checked={f.nuevas} onChange={(e) => set("nuevas", e.target.checked)} className="accent-[var(--brand)]" />
                  Solo nuevas (últimos {DIAS_NUEVA} días)
                </label>
              </Campo>
            </Card>
          </aside>

          {/* Resultados */}
          <div>
            {cargando ? (
              <div className="grid gap-5 sm:grid-cols-2">
                {[0, 1, 2, 3].map((i) => (
                  <div key={i} className="shimmer h-60 rounded-2xl border border-border-soft bg-surface-2/60" />
                ))}
              </div>
            ) : filtradas.length === 0 ? (
              <Card className="flex flex-col items-center gap-3 p-16 text-center">
                <span className="grid h-14 w-14 place-items-center rounded-2xl bg-brand-soft text-brand">
                  <Search className="h-6 w-6" />
                </span>
                <div>
                  <p className="font-display text-base font-bold text-ink">
                    {vacantes.length === 0 ? "Por ahora no hay vacantes abiertas." : "No hay vacantes con estos filtros."}
                  </p>
                  <p className="mt-1 text-xs text-ink-3">
                    {vacantes.length === 0 ? "Vuelve pronto: publicamos nuevas oportunidades con frecuencia." : "Prueba con otra palabra o quita alguno de los filtros."}
                  </p>
                </div>
                {nActivos > 0 && (
                  <Button variant="outline" size="sm" onClick={limpiar}>
                    Quitar filtros
                  </Button>
                )}
              </Card>
            ) : (
              <div className="grid gap-5 sm:grid-cols-2">
                {filtradas.map((v) => (
                  <VacanteCard key={v.id} v={v} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      <footer className="border-t border-border-soft py-8 text-center text-[11px] text-ink-3">
        Bolsa de trabajo operada con Red Human AI · Tus datos se tratan conforme al Aviso de Privacidad (LFPDPPP).
      </footer>
    </main>
  );
}

const claseSelect = "h-11 w-full rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20";

function Campo({ etiqueta, extra, children }: { etiqueta: string; extra?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="mt-5 flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <Eyebrow className="text-[10px] text-ink-3">{etiqueta}</Eyebrow>
        {extra}
      </div>
      {children}
    </div>
  );
}

function Chip({ activo, onClick, children, compacto }: { activo: boolean; onClick: () => void; children: React.ReactNode; compacto?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={activo}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border font-semibold transition",
        compacto ? "px-2.5 py-1 text-[11px]" : "px-3.5 py-1.5 text-xs",
        activo ? "border-brand bg-brand text-white shadow-sm" : "border-border-soft bg-surface text-ink-2 hover:border-brand/40 hover:text-brand",
      )}
    >
      {children}
    </button>
  );
}

function VacanteCard({ v }: { v: Vacante }) {
  const empresa = v.nombreEmpresa ?? v.empresa;
  const nueva = esNueva(v);
  const sueldoTexto = v.sueldo && !/convenir/i.test(v.sueldo) ? v.sueldo : "";
  const chips = [v.seniority, ...(v.palabrasClave ?? [])].filter(Boolean).slice(0, 3) as string[];
  return (
    <Card className="group relative flex flex-col gap-4 overflow-hidden rounded-2xl border border-border-soft p-5 shadow-md transition-all duration-300 hover:-translate-y-1 hover:border-brand/40 hover:shadow-xl">
      <div className="pointer-events-none absolute -right-10 -top-10 h-32 w-32 rounded-full bg-brand/10 opacity-0 blur-2xl transition-opacity duration-300 group-hover:opacity-100" />

      <div className="relative flex items-start gap-3">
        <span className="grid h-12 w-12 shrink-0 place-items-center overflow-hidden rounded-xl border border-border-soft bg-surface-2">
          {v.logoUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={urlArchivo(v.logoUrl)} alt={empresa} className="h-full w-full object-contain p-1" />
          ) : (
            <Building2 className="h-5 w-5 text-ink-3" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate font-mono text-[11px] font-semibold uppercase tracking-wider text-brand">{v.area || "General"}</p>
            {nueva && <Badge tone="good" dot className="shrink-0">Nueva</Badge>}
          </div>
          <h3 className="font-display mt-0.5 line-clamp-2 text-lg font-bold leading-tight text-ink">{v.titulo}</h3>
          <p className="mt-0.5 truncate text-[12px] text-ink-3">{empresa}</p>
        </div>
      </div>

      {sueldoTexto ? (
        <p className="flex items-center gap-1.5 text-[15px] font-bold text-good">
          <Banknote className="h-4 w-4" /> {sueldoTexto}
        </p>
      ) : (
        <p className="flex items-center gap-1.5 text-[13px] font-semibold text-ink-3">
          <Banknote className="h-4 w-4" /> Sueldo a convenir
        </p>
      )}

      {v.resumen && <p className="line-clamp-2 text-[13px] leading-relaxed text-ink-2">{v.resumen}</p>}

      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone="neutral">
          <MapPin className="h-3 w-3" /> {v.ubicacion || "México"}
        </Badge>
        <Badge tone="brand">
          <Briefcase className="h-3 w-3" /> {v.modalidad}
        </Badge>
        {chips.map((c) => (
          <Badge key={c} tone="neutral">{c}</Badge>
        ))}
        {tieneEntrevistaRedHuman(v) && (
          <Badge tone="human">
            <Bot className="h-3 w-3" /> Entrevista Red Human
          </Badge>
        )}
      </div>

      <div className="mt-auto flex items-center justify-between gap-3 pt-1">
        <span className="inline-flex items-center gap-1 text-[11px] text-ink-3">
          <Clock className="h-3 w-3" /> {haceTexto(v.publicadaEn) || "Publicada"}
        </span>
        {/* Postularme: la misma ruta de un paso (/aplicar/[slug]) */}
        <Button href={`/aplicar/${v.slug}`} size="sm" className="transition-transform duration-300 group-hover:scale-[1.03]">
          Postularme <ArrowRight className="h-4 w-4 transition-transform duration-300 group-hover:translate-x-0.5" />
        </Button>
      </div>
    </Card>
  );
}
