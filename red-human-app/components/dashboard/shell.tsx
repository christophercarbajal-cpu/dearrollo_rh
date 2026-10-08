"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Bell,
  BookOpen,
  Briefcase,
  Building2,
  ChevronDown,
  ClipboardCheck,
  ClipboardList,
  ExternalLink,
  Globe,
  GraduationCap,
  HeartPulse,
  LayoutDashboard,
  LogOut,
  Menu,
  Plus,
  Settings,
  Sparkles,
  Target,
  UserSquare2,
  Users,
  Video,
  X,
} from "lucide-react";
import { Logo, Avatar, Button } from "@/components/ui";
import { ThemeToggle } from "@/components/theme-toggle";
import { cn } from "@/lib/utils";
import { useSesion } from "@/components/sesion";
import { fetchActividadAgente } from "@/lib/api";
import { usePolling } from "@/lib/use-polling";
import { BarraAgente } from "@/components/dashboard/agente/barra";
import { PanelAgente } from "@/components/dashboard/agente/panel";

type NavItem = {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string;
};

// 2026-09-17: sin contadores quemados («24», «1.8k», «12», «3») — eran datos de maqueta en producción.
const navOperacion: NavItem[] = [
  { href: "/dashboard", label: "Tablero de control", icon: LayoutDashboard },
  // { href: "/dashboard/requisiciones", label: "Requisiciones", icon: ClipboardList }, // Oculto temporalmente
  { href: "/dashboard/vacantes", label: "Vacantes", icon: Briefcase },
  { href: "/dashboard/candidatos", label: "Candidatos", icon: Users },
  { href: "/dashboard/entrevistas", label: "Entrevistas", icon: Video },
];

const navColaborador: NavItem[] = [
  { href: "/dashboard/colaboradores", label: "Colaboradores", icon: UserSquare2 },
  { href: "/dashboard/onboarding", label: "Onboarding", icon: ClipboardCheck },
  { href: "/dashboard/capacitacion", label: "Capacitación", icon: GraduationCap },
  { href: "/dashboard/desempeno", label: "Desempeño", icon: Target },
  { href: "/dashboard/clima", label: "Clima", icon: HeartPulse },
  { href: "/dashboard/conocimiento", label: "Base de conocimiento", icon: BookOpen },
];

const navAdmin: NavItem[] = [{ href: "/dashboard/configuracion", label: "Configuración", icon: Settings }];

function NavList({ items, onNavigate }: { items: NavItem[]; onNavigate?: () => void }) {
  const path = usePathname();
  return (
    <nav className="flex flex-col gap-1">
      {items.map((item) => {
        const active = item.href === "/dashboard" ? path === item.href : path.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            className={cn(
              "group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition",
              active ? "bg-brand-soft text-brand" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
            )}
          >
            {active && <span className="absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-brand" />}
            <item.icon className={cn("h-[18px] w-[18px]", active ? "text-brand" : "text-ink-3 group-hover:text-ink-2")} />
            <span className="flex-1">{item.label}</span>
            {item.badge && (
              <span className={cn("rounded-full px-2 py-0.5 font-mono text-[10px] font-semibold", active ? "bg-brand/15 text-brand" : "bg-surface-2 text-ink-3")}>
                {item.badge}
              </span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}

/* ---------------- Selector de Cuenta (Punto 27) ---------------- */

/** Solo se renderiza cuando el usuario tiene acceso a más de una Cuenta activa.
 * Muestra la Cuenta seleccionada y un dropdown con las opciones disponibles.
 * Al seleccionar una Cuenta diferente, persiste en localStorage y navega al Tablero. */
function SelectorCuenta({ variant = "sidebar" }: { variant?: "sidebar" | "topbar" }) {
  const { usuario, cuentaActualId, cambiarCuenta } = useSesion();
  const [abierto, setAbierto] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Cerrar el dropdown al hacer clic fuera
  useEffect(() => {
    if (!abierto) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setAbierto(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [abierto]);

  // Regla Fase A: si solo tiene una Cuenta no se muestra ningún selector
  if (!usuario || usuario.cuentas.length <= 1) return null;

  const cuentaActual = usuario.cuentas.find((c) => c.id === cuentaActualId);

  if (variant === "topbar") {
    return (
      <div ref={ref} className="relative">
        <button
          id="selector-cuenta-topbar"
          onClick={() => setAbierto((v) => !v)}
          aria-haspopup="listbox"
          aria-expanded={abierto}
          className="flex items-center gap-1.5 rounded-lg border border-border-soft bg-surface-2 px-2.5 py-1.5 text-[12px] font-medium text-ink-2 transition hover:border-brand/40 hover:bg-brand-soft hover:text-brand"
        >
          <Building2 className="h-3.5 w-3.5" />
          <span className="max-w-[120px] truncate">{cuentaActual?.nombre ?? cuentaActual?.nombreComercial ?? "Cuenta"}</span>
          <ChevronDown className={cn("h-3 w-3 transition-transform", abierto && "rotate-180")} />
        </button>
        {abierto && (
          <div
            role="listbox"
            className="absolute right-0 top-full z-50 mt-1 min-w-[180px] overflow-hidden rounded-xl border border-border-soft bg-surface shadow-lg"
          >
            {usuario.cuentas.map((c) => (
              <button
                key={c.id}
                role="option"
                aria-selected={c.id === cuentaActualId}
                onClick={() => { cambiarCuenta(c.id); setAbierto(false); }}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm transition hover:bg-surface-2",
                  c.id === cuentaActualId ? "font-semibold text-brand" : "text-ink-2",
                )}
              >
                {c.id === cuentaActualId && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand" />}
                <span className={c.id === cuentaActualId ? "" : "pl-3.5"}>{c.nombre || c.nombreComercial}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

  // Variant sidebar — se inserta justo debajo del nombre de usuario en TarjetaUsuario
  return (
    <div ref={ref} className="relative mt-1">
      <button
        id="selector-cuenta-sidebar"
        onClick={() => setAbierto((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={abierto}
        className="flex w-full items-center gap-1.5 rounded-lg px-1 py-0.5 text-[11px] text-ink-3 transition hover:text-brand"
      >
        <Building2 className="h-3 w-3 shrink-0" />
        <span className="flex-1 truncate text-left">{cuentaActual?.nombre ?? cuentaActual?.nombreComercial ?? "Cuenta"}</span>
        <ChevronDown className={cn("h-3 w-3 shrink-0 transition-transform", abierto && "rotate-180")} />
      </button>
      {abierto && (
        <div
          role="listbox"
          className="absolute bottom-full left-0 z-50 mb-1 min-w-full overflow-hidden rounded-xl border border-border-soft bg-surface shadow-lg"
        >
          {usuario.cuentas.map((c) => (
            <button
              key={c.id}
              role="option"
              aria-selected={c.id === cuentaActualId}
              onClick={() => { cambiarCuenta(c.id); setAbierto(false); }}
              className={cn(
                "flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] transition hover:bg-surface-2",
                c.id === cuentaActualId ? "font-semibold text-brand" : "text-ink-2",
              )}
            >
              {c.id === cuentaActualId && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand" />}
              <span className={c.id === cuentaActualId ? "" : "pl-3.5"}>{c.nombre || c.nombreComercial}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------- Sidebar content ---------------- */

/** 2026-09-15: dato REAL (antes «3» quemado): postulaciones en Prefiltro con sesión de WhatsApp activa
 * (mensaje en las últimas 24 h). Se revalida con el mismo hook que los tableros. */
function ContadorAgente() {
  const [act, setAct] = useState<{ prefiltrando: number; enPrefiltro: number } | null>(null);
  const { cuentaActualId } = useSesion();
  const cargar = useCallback(async () => {
    const a = await fetchActividadAgente();
    if (a) setAct(a);
  }, []);
  useEffect(() => {
    cargar();
  }, [cargar, cuentaActualId]);
  usePolling(cargar, 30000);
  if (!act) return <>Conectando con el agente de WhatsApp…</>;
  if (act.prefiltrando === 0) {
    return act.enPrefiltro > 0
      ? <>{act.enPrefiltro} candidato{act.enPrefiltro !== 1 ? "s" : ""} en Prefiltro, sin conversación activa por WhatsApp ahora mismo.</>
      : <>Sin conversaciones de prefiltro activas por WhatsApp en este momento.</>;
  }
  return <>Prefiltrando {act.prefiltrando} candidato{act.prefiltrando !== 1 ? "s" : ""} en este momento por WhatsApp.</>;
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { usuario, cuentaActualId } = useSesion();
  return (
    <div className="flex min-h-full flex-col gap-5 p-4 lg:gap-6">
      <div className="px-2 pt-1">
        <Link href="/" onClick={onNavigate}>
          <Logo />
        </Link>
      </div>

      <Button href="/dashboard/vacantes" size="sm" className="w-full">
        <Plus className="h-4 w-4" /> Nueva vacante
      </Button>

      {/* Acceso directo a la bolsa de trabajo pública DE LA CUENTA ACTUAL (?cuenta=<id>): pestaña nueva para no perder el panel. */}
      <Link
        href={cuentaActualId ? `/portal?cuenta=${cuentaActualId}` : "/portal"}
        target="_blank"
        rel="noopener noreferrer"
        className="group flex items-center gap-2.5 rounded-xl border border-border-soft bg-surface-2/60 px-3 py-2.5 text-sm font-medium text-ink-2 transition hover:border-brand/40 hover:bg-brand-soft hover:text-brand"
      >
        <Globe className="h-[18px] w-[18px] text-ink-3 transition group-hover:text-brand" />
        <span className="flex-1">Ver Bolsa de Trabajo</span>
        <ExternalLink className="h-3.5 w-3.5 text-ink-3 transition group-hover:text-brand" />
      </Link>

      {/* 2026-10-07: el menú ya no se recorta dentro de su propio scroll (la tarjeta «Agente activo» tapaba «Base de
          conocimiento» en pantallas bajas): crece con su contenido y la barra lateral completa hace scroll. */}
      <div className="flex-1 space-y-5">
        <div>
          <p className="px-3 pb-2 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-3">Reclutamiento</p>
          <NavList items={navOperacion} onNavigate={onNavigate} />
        </div>
        <div>
          <p className="px-3 pb-2 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-3">Colaborador</p>
          <NavList items={navColaborador} onNavigate={onNavigate} />
        </div>
        {usuario?.rol === "Administrador" && (
          <div>
            <p className="px-3 pb-2 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-3">Administración</p>
            <NavList items={navAdmin} onNavigate={onNavigate} />
          </div>
        )}
      </div>

      {/* Agente card */}
      <div className="relative shrink-0 overflow-hidden rounded-2xl border border-border-soft bg-gradient-to-br from-brand-soft to-transparent p-4">
        <div className="flex items-center gap-2">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand text-brand-ink">
            <Sparkles className="h-4 w-4" />
          </span>
          <span className="text-sm font-semibold">Agente activo</span>
          <span className="ml-auto h-2 w-2 rounded-full bg-good pulse-ring" />
        </div>
        <p className="mt-2 text-xs leading-relaxed text-ink-2">
          <ContadorAgente />
        </p>
      </div>

      <TarjetaUsuario />
    </div>
  );
}

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const path = usePathname();

  // 2026-09-17 (móvil): el cajón se cierra al navegar, con Escape y al pasar a escritorio; mientras
  // está abierto se bloquea el scroll del fondo (antes el contenido seguía desplazándose detrás).
  useEffect(() => {
    setOpen(false);
  }, [path]);
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const mq = window.matchMedia("(min-width: 1024px)");
    const alCambiar = () => mq.matches && setOpen(false);
    document.addEventListener("keydown", esc);
    mq.addEventListener("change", alCambiar);
    const overflowPrevio = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", esc);
      mq.removeEventListener("change", alCambiar);
      document.body.style.overflow = overflowPrevio;
    };
  }, [open]);

  return (
    <div className="min-h-svh bg-bg">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-64 overflow-y-auto border-r border-border-soft bg-surface lg:block">
        <SidebarContent />
      </aside>

      {/* Mobile drawer (hamburguesa) — cajón deslizable con fondo oscurecido; todo el contenido hace scroll
          dentro del cajón para pantallas bajas. */}
      <div className={cn("fixed inset-0 z-50 lg:hidden", open ? "pointer-events-auto" : "pointer-events-none")} aria-hidden={!open}>
        <div
          className={cn("absolute inset-0 bg-black/50 backdrop-blur-sm transition-opacity duration-200", open ? "opacity-100" : "opacity-0")}
          onClick={() => setOpen(false)}
        />
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Menú de navegación"
          className={cn(
            "absolute inset-y-0 left-0 flex w-[min(20rem,88vw)] flex-col overflow-y-auto border-r border-border-soft bg-surface shadow-2xl transition-transform duration-200 ease-out",
            "pb-[env(safe-area-inset-bottom)]",
            open ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <button
            onClick={() => setOpen(false)}
            className="absolute right-3 top-3 z-10 grid h-10 w-10 place-items-center rounded-xl text-ink-2 hover:bg-surface-2"
            aria-label="Cerrar menú"
          >
            <X className="h-5 w-5" />
          </button>
          {open && <SidebarContent onNavigate={() => setOpen(false)} />}
        </div>
      </div>

      {/* Topbar */}
      <header className="glass fixed inset-x-0 top-0 z-30 border-b border-border-soft lg:left-64">
        <div className="flex h-16 items-center gap-2 px-3 sm:gap-3 sm:px-6">
          <button
            onClick={() => setOpen(true)}
            className="grid h-10 w-10 shrink-0 place-items-center rounded-xl text-ink-2 hover:bg-surface-2 lg:hidden"
            aria-label="Abrir menú"
            aria-expanded={open}
          >
            <Menu className="h-5 w-5" />
          </button>
          <Link href="/dashboard" className="shrink-0 lg:hidden" aria-label="Tablero de control">
            <Logo size="sm" />
          </Link>

          {/* Fase F, punto 29: barra permanente "Pregunta a Red Human" — sustituye el buscador
              decorativo, no es un módulo aparte. */}
          <BarraAgente />

          <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
            {/* Selector de Cuenta en topbar (solo si el usuario tiene más de una) */}
            <SelectorCuenta variant="topbar" />
            <ThemeToggle />
            <button className="relative hidden h-10 w-10 place-items-center rounded-xl text-ink-2 hover:bg-surface-2 sm:grid" aria-label="Notificaciones">
              <Bell className="h-5 w-5" />
            </button>
            <UsuarioBarra />
          </div>
        </div>
      </header>

      <main className="pt-16 lg:pl-64">{children}</main>

      <PanelAgente />
    </div>
  );
}


/* ---------------- Sesión ---------------- */

function TarjetaUsuario() {
  const { usuario, cargando, salir } = useSesion();
  if (cargando) return <div className="h-[68px] animate-pulse rounded-2xl bg-surface-2" />;
  if (!usuario) return null;
  return (
    <div className="flex flex-col gap-1 rounded-2xl border border-border-soft bg-surface-2 p-3">
      <div className="flex items-center gap-3">
        <Avatar name={usuario.nombre} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">{usuario.nombre}</p>
          <p className="truncate text-xs text-ink-3">{usuario.rol}</p>
        </div>
        <button
          onClick={salir}
          className="grid h-8 w-8 place-items-center rounded-lg text-ink-3 transition hover:bg-surface hover:text-brand"
          aria-label="Cerrar sesión"
          title="Cerrar sesión"
        >
          <LogOut className="h-4 w-4" />
        </button>
      </div>
      {/* Selector de Cuenta en sidebar (solo si el usuario tiene más de una) */}
      <SelectorCuenta variant="sidebar" />
    </div>
  );
}

function UsuarioBarra() {
  const { usuario, salir } = useSesion();
  if (!usuario) return null;
  return (
    <div className="hidden items-center gap-2.5 rounded-xl border border-border-soft bg-surface py-1.5 pl-1.5 pr-2 sm:flex">
      <Avatar name={usuario.nombre} />
      <div className="leading-tight">
        <p className="text-[13px] font-semibold">{usuario.nombre}</p>
        <p className="font-mono text-[10px] text-ink-3">{usuario.rol}</p>
      </div>
      <button
        onClick={salir}
        className="ml-1 grid h-8 w-8 place-items-center rounded-lg text-ink-3 transition hover:bg-surface-2 hover:text-brand"
        aria-label="Cerrar sesión"
        title="Cerrar sesión"
      >
        <LogOut className="h-4 w-4" />
      </button>
    </div>
  );
}