"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchConfiguracion, fetchYo, logout as apiLogout, type UsuarioRH } from "@/lib/api";

/* ============================================================
   Sesión de la persona de RH.

   El servidor es quien manda: la cookie es httpOnly y toda decisión se firma
   allá con el usuario de la sesión. Este contexto solo sirve para pintar la
   interfaz y esconder lo que el rol no puede hacer.

   También carga Modo Prueba una sola vez aquí (en vez de que cada componente que lo necesite
   haga su propio GET /configuracion) — lo usan varios sitios a la vez cuando el modal de un
   candidato está abierto (Lote 4: botón "Continuar de todos modos").

   Punto 27 — Selector de Cuenta:
   Cuando el usuario tiene acceso a más de una Cuenta activa, este contexto expone
   `cuentaActualId` y `cambiarCuenta()`. Al cambiar de Cuenta:
     1. Se persiste el id en localStorage con clave "rh-cuenta-id".
     2. Todas las peticiones a la API incluyen automáticamente X-Cuenta-Id (ver lib/api.ts).
     3. El usuario es llevado al Tablero para evitar ver datos de la Cuenta anterior.
   Si el usuario solo tiene una Cuenta, `cuentaActualId` se resuelve sola y el selector
   no aparece en el shell (regla de negocio confirmada en CONTEXTO_SESION.md).
   ============================================================ */

const CLAVE_STORAGE = "rh-cuenta-id";

interface Contexto {
  usuario: UsuarioRH | null;
  cargando: boolean;
  modoPrueba: boolean;
  /** Id de la Cuenta activa en esta sesión. Null mientras carga o si el usuario no tiene Cuentas. */
  cuentaActualId: number | null;
  refrescar: () => Promise<void>;
  salir: () => Promise<void>;
  /** Cambia la Cuenta activa, la persiste en localStorage y navega al Tablero. */
  cambiarCuenta: (id: number) => void;
}

const SesionCtx = createContext<Contexto>({
  usuario: null,
  cargando: true,
  modoPrueba: false,
  cuentaActualId: null,
  refrescar: async () => {},
  salir: async () => {},
  cambiarCuenta: () => {},
});

/** Lee el id guardado en localStorage y lo valida contra la lista de Cuentas del usuario.
 * Si no está o ya no es válido, cae al primer elemento de la lista. */
function _resolverCuentaId(usuario: UsuarioRH): number | null {
  if (usuario.cuentas.length === 0) return null;
  const guardado = typeof window !== "undefined" ? window.localStorage.getItem(CLAVE_STORAGE) : null;
  const guardadoNum = guardado ? parseInt(guardado, 10) : null;
  const valido = guardadoNum !== null && usuario.cuentas.some((c) => c.id === guardadoNum);
  if (valido) return guardadoNum;
  // Fase 2: sin selección guardada, la Cuenta predeterminada del usuario (si sigue activa).
  const pred = usuario.cuentaPredeterminadaId ?? null;
  if (pred !== null && usuario.cuentas.some((c) => c.id === pred)) return pred;
  return usuario.cuentas[0].id;
}

export function ProveedorSesion({ children }: { children: React.ReactNode }) {
  const [usuario, setUsuario] = useState<UsuarioRH | null>(null);
  const [cargando, setCargando] = useState(true);
  const [modoPrueba, setModoPrueba] = useState(false);
  const [cuentaActualId, setCuentaActualId] = useState<number | null>(null);
  const router = useRouter();

  const refrescar = useCallback(async () => {
    const u = await fetchYo();
    setUsuario(u);
    if (u) {
      const id = _resolverCuentaId(u);
      if (id !== null && typeof window !== "undefined") {
        window.localStorage.setItem(CLAVE_STORAGE, String(id));
      }
      setCuentaActualId(id);
    }
    setCargando(false);
  }, []);

  useEffect(() => {
    refrescar();
  }, [refrescar]);

  // Modo Prueba se relee al cambiar de Cuenta (Punto 9) y cuando Configuración lo alterna
  // (esa sección llama refrescar()).
  useEffect(() => {
    fetchConfiguracion().then((cfg) => {
      if (cfg) setModoPrueba(cfg.modoPrueba);
    });
  }, [cuentaActualId, usuario?.id]);

  const salir = useCallback(async () => {
    await apiLogout();
    setUsuario(null);
    router.push("/login");
  }, [router]);

  const cambiarCuenta = useCallback(
    (id: number) => {
      if (typeof window !== "undefined") {
        window.localStorage.setItem(CLAVE_STORAGE, String(id));
      }
      setCuentaActualId(id);
      // Navegar al Tablero evita ver datos de la Cuenta anterior; además <PorCuenta> (layout)
      // remonta todo el dashboard con la nueva Cuenta, aunque la ruta no cambie.
      router.push("/dashboard");
    },
    [router],
  );

  return (
    <SesionCtx.Provider value={{ usuario, cargando, modoPrueba, cuentaActualId, refrescar, salir, cambiarCuenta }}>
      {children}
    </SesionCtx.Provider>
  );
}

export function useSesion() {
  return useContext(SesionCtx);
}

/** Nombre con el que se firma en pantalla; el servidor usa el suyo propio. */
export function useNombreRH() {
  return useSesion().usuario?.nombre ?? "";
}

/** Administrador y Usuario deciden por igual (ya no hay rol de solo lectura) — se deja el hook
 * para no tocar los call-sites existentes; el servidor es la fuente real de verdad. */
export function usePuedeDecidir() {
  return useSesion().usuario?.puedeDecidir ?? false;
}

/** `true` solo para Administrador — el servidor es quien realmente lo exige (Depends(usuario_admin)). */
export function useEsAdmin() {
  return useSesion().usuario?.rol === "Administrador";
}

/** Modo Prueba (Lote 4) — activo, ciertos bloqueos de estado pueden saltarse con
 * `forzarPrueba` (ver moverEtapaCandidato/autorizarAlta/etc. en lib/api.ts); el flag es inerte
 * si esto es `false`, el servidor nunca lo obedece fuera de Modo Prueba. */
export function useModoPrueba() {
  return useSesion().modoPrueba;
}

/** Id de la Cuenta activa — util para saber cuál está seleccionada sin acceder al usuario completo. */
export function useCuentaActualId() {
  return useSesion().cuentaActualId;
}

/** Flujo de psicometría de UN solo modal (2026-10-07, red-human-psicometria.md): AISLADO a las Cuentas que la API
 * marca con `psicometriaSimple` (hoy solo el slug «demo-grupak», `models.CUENTAS_PSICOMETRIA_SIMPLE`). El resto de
 * las Cuentas conserva el flujo anterior tal cual. */
export const CUENTAS_PSICOMETRIA_SIMPLE = ["demo-grupak"];
export function usePsicometriaSimple() {
  const { usuario, cuentaActualId } = useSesion();
  const cuenta = usuario?.cuentas.find((c) => c.id === cuentaActualId);
  if (!cuenta) return false;
  return Boolean(cuenta.psicometriaSimple) || CUENTAS_PSICOMETRIA_SIMPLE.includes(cuenta.slug ?? "");
}
