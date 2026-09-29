"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Bell,
  Briefcase,
  Building2,
  Check,
  Copy,
  ExternalLink,
  FlaskConical,
  Link2,
  Loader2,
  Pencil,
  Plus,
  RotateCw,
  Save,
  Trash2,
  Upload,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import { Card, Badge, Button } from "@/components/ui";
import { PageHeader } from "@/components/dashboard/parts";
import { Aviso } from "@/components/dashboard/subida";
import { useEsAdmin, useSesion } from "@/components/sesion";
import { Field, Modal, Selector, BotonEliminar } from "@/components/dashboard/campos";
import {
  CONTENIDO_VACIO,
  FormularioContenidoVacante,
  contenidoComoPayload,
  contenidoDesdePlantilla,
  type ContenidoVacante,
} from "@/components/dashboard/vacantes/formulario-contenido";
import { invalidarReglasNotificacion } from "@/components/dashboard/linea-notificar";
import { BotonCargaMasiva } from "@/components/dashboard/carga-masiva";
import { MenuAcciones } from "@/components/dashboard/menu-acciones";
import { SeccionPlantillasClima } from "@/components/dashboard/clima/seccion-plantillas-clima";
import { SeccionPlantillasOnboarding } from "@/components/dashboard/onboarding/seccion-plantillas-onboarding";
import { SeccionPruebasPsicometricas } from "@/components/dashboard/evaluaciones/seccion-pruebas-psicometricas";
import {
  actualizarCliente,
  actualizarConfiguracion,
  actualizarCuentaPorId,
  actualizarPlantilla,
  actualizarUsuario,
  agregarContactoCliente,
  agregarUsuarioCuenta,
  conectarTeams,
  crearCliente,
  crearCuenta,
  crearPlantilla,
  crearUsuario,
  duplicarPlantilla,
  editarContactoCliente,
  eliminarCandidatosPrueba,
  eliminarContactoCliente,
  eliminarPlantilla,
  fetchCliente,
  fetchClientes,
  fetchConfiguracion,
  fetchCuenta,
  fetchCuentas,
  eliminarCuenta,
  restaurarCuenta,
  marcarCuentaPredeterminada,
  fetchIntegracionTeams,
  fetchPlantillas,
  fetchReglasNotificacion,
  fetchUsuarios,
  guardarReglasNotificacion,
  quitarUsuarioCuenta,
  probarTeams,
  desconectarTeams,
  subirLogoCuentaPorId,
  EVENTOS_NOTIFICACION,
  NOMBRE_EVENTO_NOTIFICACION,
  type CamposCliente,
  type CamposContacto,
  type CamposCuenta,
  type Cliente,
  type ConfiguracionSistema,
  type ContactoCliente,
  type DatosCuenta,
  type FichaCuenta,
  type IntegracionTeams,
  type Plantilla,
  type ReglaNotificacion,
  type ResumenBorradoPrueba,
  type RolUsuario,
  type UsuarioRH,
  urlArchivo,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { textoFecha } from "@/lib/fechas";

/* ============================================================
   Configuración — 6 secciones (Punto 2), completadas en Puntos 9-13
   1. Cuentas (gestión completa: listado, alta, ficha con usuarios/clientes/portal)
   2. Usuarios y permisos (Cuenta actual)
   3. Clientes y contactos
   4. Plantillas (administradas aquí, mismo formulario que Nueva vacante) + Plantillas de clima (Clima v2)
   5. Notificaciones (configuración predeterminada + botón Guardar)
   6. Modo prueba (switch, ventana configurable, borrado de prueba)
   ============================================================ */

export default function Configuracion() {
  const esAdmin = useEsAdmin();

  if (!esAdmin) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-6 sm:px-6 sm:py-8">
        <PageHeader title="Configuración" subtitle="Solo un administrador puede ver esta sección." />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:px-6 sm:py-8">
      <PageHeader title="Configuración" subtitle="Ajustes globales del sistema, solo para administradores." />

      <SeccionCuentas />
      <SeccionUsuarios />
      <SeccionClientes />
      <SeccionPlantillas />
      <SeccionPlantillasClima />
      <SeccionPlantillasOnboarding />
      <SeccionPruebasPsicometricas />
      <SeccionNotificaciones />
      <SeccionIntegraciones />
      <SeccionModoPrueba />
    </div>
  );
}

/* ================================================================== */
/* Integraciones (Fase 7B) — Microsoft Teams / Microsoft 365           */
/* ================================================================== */

function SeccionIntegraciones() {
  const [teams, setTeams] = useState<IntegracionTeams | null>(null);
  const [ocupado, setOcupado] = useState("");
  const [msg, setMsg] = useState<{ tono: "ok" | "error" | "info"; texto: string } | null>(null);
  const [copiada, setCopiada] = useState(false);

  const recargar = useCallback(() => fetchIntegracionTeams().then((t) => setTeams(t)), []);
  useEffect(() => {
    recargar();
    // Retorno del flujo OAuth (GET /integraciones/teams/callback redirige aquí con ?teams=ok|error)
    try {
      const q = new URLSearchParams(window.location.search);
      const r = q.get("teams");
      if (r === "ok") setMsg({ tono: "ok", texto: `Microsoft 365 conectado${q.get("usuario") ? ` como ${q.get("usuario")}` : ""}.` });
      if (r === "error") setMsg({ tono: "error", texto: `No se pudo conectar Microsoft 365: ${q.get("motivo") ?? "error desconocido"}.` });
      if (r) window.history.replaceState({}, "", window.location.pathname);
    } catch {}
  }, [recargar]);

  async function conectar() {
    setOcupado("conectar");
    setMsg(null);
    const r = await conectarTeams();
    if (!r.ok) {
      setOcupado("");
      return setMsg({ tono: "error", texto: r.error });
    }
    window.location.href = r.data.url; // Microsoft → callback de la API → de vuelta aquí con ?teams=…
  }

  async function probar() {
    setOcupado("probar");
    setMsg(null);
    const r = await probarTeams();
    setOcupado("");
    setMsg(r.ok ? { tono: "ok", texto: `Conexión correcta: ${r.data.nombreM365 || r.data.usuarioM365}.` } : { tono: "error", texto: r.error });
    recargar();
  }

  async function desconectar() {
    if (!window.confirm("¿Desconectar Microsoft 365 de esta Cuenta? Las videollamadas volverán a pedir la liga a mano.")) return;
    setOcupado("desconectar");
    const r = await desconectarTeams();
    setOcupado("");
    setMsg(r.ok ? { tono: "info", texto: "Microsoft 365 desconectado." } : { tono: "error", texto: r.error });
    recargar();
  }

  function copiar() {
    if (!teams) return;
    navigator.clipboard.writeText(teams.redirectUri).then(() => {
      setCopiada(true);
      setTimeout(() => setCopiada(false), 1600);
    });
  }

  return (
    <Card className="mb-6 p-5">
      <CabSeccion icono={Link2} titulo="Integraciones" subtitulo="Conexiones externas de esta Cuenta. Cada Cuenta de Red Human conecta la suya." />
      {msg && <div className="mb-3"><Aviso tono={msg.tono}>{msg.texto}</Aviso></div>}
      <div className="rounded-xl border border-border-soft p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold">Microsoft Teams / Microsoft 365</p>
            <p className="mt-0.5 text-[13px] leading-relaxed text-ink-2">
              Con la cuenta conectada, al programar una Entrevista Humana en Videollamada Red Human crea la reunión de Teams,
              guarda la liga, la incluye en el correo y WhatsApp de confirmación y manda la invitación de calendario a
              candidato y entrevistador.
            </p>
            {!teams ? (
              <p className="mt-2 text-[12px] text-ink-3">Cargando…</p>
            ) : !teams.disponible ? (
              <p className="mt-2 text-[12px] text-ink-3">
                No disponible en este servidor: faltan <code className="font-mono">TEAMS_CLIENT_ID</code>, <code className="font-mono">TEAMS_TENANT_ID</code> y{" "}
                <code className="font-mono">TEAMS_CLIENT_SECRET</code> en el <code className="font-mono">.env</code> de la API. Mientras tanto la videollamada pide la liga a mano.
              </p>
            ) : teams.conectado ? (
              <p className="mt-2 text-[12px] text-ink-2">
                <Badge tone="good" dot>Conectada</Badge> como <b>{teams.nombreM365 || teams.usuarioM365}</b> ({teams.usuarioM365})
                {teams.conectadoEn ? ` · ${fechaCorta(teams.conectadoEn)}` : ""}{teams.conectadoPor ? ` · por ${teams.conectadoPor}` : ""}
                {teams.ultimoError && <span className="mt-1 block text-bad">Último error: {teams.ultimoError}</span>}
              </p>
            ) : (
              <p className="mt-2 text-[12px] text-ink-3"><Badge tone="neutral">No conectada</Badge> Conecta un usuario de Microsoft 365 de tu organización (la reunión sale de su calendario).</p>
            )}
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            {teams?.disponible && (
              <Button size="sm" onClick={conectar} disabled={Boolean(ocupado)}>
                {ocupado === "conectar" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
                {teams.conectado ? "Reconectar" : "Conectar cuenta"}
              </Button>
            )}
            {teams?.conectado && (
              <>
                <Button variant="outline" size="sm" onClick={probar} disabled={Boolean(ocupado)}>
                  {ocupado === "probar" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Probar conexión
                </Button>
                <Button variant="outline" size="sm" onClick={desconectar} disabled={Boolean(ocupado)} className="border-bad/30 text-bad hover:bg-bad-soft">
                  <X className="h-4 w-4" /> Desconectar
                </Button>
              </>
            )}
          </div>
        </div>
        {teams?.disponible && (
          <div className="mt-3 rounded-lg bg-surface-2 px-3 py-2 text-[12px] text-ink-3">
            Redirect URI a registrar en el App Registration de Azure (permisos delegados {teams.scopes}):{" "}
            <code className="font-mono text-ink-2">{teams.redirectUri}</code>
            <button type="button" onClick={copiar} className="ml-2 inline-flex items-center gap-1 text-brand hover:underline">
              {copiada ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />} {copiada ? "Copiada" : "Copiar"}
            </button>
          </div>
        )}
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Cabecera de sección reutilizable                                      */
/* ------------------------------------------------------------------ */

function CabSeccion({
  icono: Icono,
  titulo,
  subtitulo,
}: {
  icono: React.ComponentType<{ className?: string }>;
  titulo: string;
  subtitulo?: string;
}) {
  return (
    <div className="mb-4 flex items-center gap-2">
      <Icono className="h-[18px] w-[18px] shrink-0 text-brand" />
      <div>
        <h2 className="font-display text-base font-bold leading-none">{titulo}</h2>
        {subtitulo && <p className="mt-1 text-[13px] leading-relaxed text-ink-2">{subtitulo}</p>}
      </div>
    </div>
  );
}

function Entrada({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  className,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  className?: string;
}) {
  return (
    <div className={className}>
      <label className="mb-1 block text-[12px] font-medium text-ink-2">{label}</label>
      <input
        value={value}
        type={type}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-10 w-full rounded-xl border border-border-soft bg-surface px-3.5 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20"
      />
    </div>
  );
}

function fechaCorta(iso: string) {
  return textoFecha(iso, { day: "2-digit", month: "short", year: "numeric" }) || "—";
}

/* ================================================================== */
/* 1. Cuentas (Punto 9)                                                */
/* ================================================================== */

type FormCuentaState = { nombre: string; nombreComercial: string; razonSocial: string; contactoNombre: string; correo: string; whatsapp: string; whatsappExclusivo: boolean; estado: "Activa" | "Inactiva" };

const cuentaAForm = (c?: DatosCuenta | null): FormCuentaState => ({
  nombre: c?.nombre ?? "",
  nombreComercial: c?.nombreComercial ?? "",
  razonSocial: c?.razonSocial ?? "",
  contactoNombre: c?.contactoNombre ?? "",
  correo: c?.correoComunicacion ?? "",
  whatsapp: c?.whatsappComunicacion ?? "",
  whatsappExclusivo: Boolean(c?.whatsappExclusivo),
  estado: c?.estado === "Inactiva" ? "Inactiva" : "Activa",
});

const formACampos = (f: FormCuentaState): CamposCuenta & { nombre: string } => ({
  nombre: f.nombre,
  nombre_comercial: f.nombreComercial,
  razon_social: f.razonSocial,
  contacto_nombre: f.contactoNombre,
  correo_comunicacion: f.correo,
  whatsapp_comunicacion: f.whatsapp,
  whatsapp_exclusivo: f.whatsappExclusivo,
  estado: f.estado,
});

function CamposCuentaForm({ f, set }: { f: FormCuentaState; set: (k: keyof FormCuentaState, v: string | boolean) => void }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Entrada label="Nombre de la cuenta" value={f.nombre} onChange={(v) => set("nombre", v)} placeholder="Ej. Grupo Carbe (identificador interno)" className="sm:col-span-2" />
      <Entrada label="Nombre comercial" value={f.nombreComercial} onChange={(v) => set("nombreComercial", v)} placeholder="Lo que ven los candidatos en el portal" />
      <Entrada label="Razón social (opcional)" value={f.razonSocial} onChange={(v) => set("razonSocial", v)} placeholder="Ej. Carbe S.A. de C.V." />
      <Entrada label="Correo" value={f.correo} onChange={(v) => set("correo", v)} placeholder="rh@empresa.com" type="email" />
      <Entrada label="Teléfono / WhatsApp" value={f.whatsapp} onChange={(v) => set("whatsapp", v)} placeholder="52 55 1234 5678" />
      <Entrada label="Nombre de contacto" value={f.contactoNombre} onChange={(v) => set("contactoNombre", v)} placeholder="Ej. Ana García" />
      <Selector label="Estatus" value={f.estado} onChange={(v) => set("estado", v)} opciones={["Activa", "Inactiva"]} />
      {/* 2026-09-17 (WhatsApp multi-tenant): por defecto el número maestro de WhatsApp atiende a TODAS las
          Cuentas (el candidato elige la vacante y su postulación queda en la Cuenta correcta). Premium:
          un número propio conectado en Meta se reserva para esta Cuenta. */}
      <label className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-border-soft bg-surface-2/50 p-3 sm:col-span-2">
        <input
          type="checkbox"
          checked={f.whatsappExclusivo}
          onChange={(e) => set("whatsappExclusivo", e.target.checked)}
          className="mt-0.5 h-4 w-4 accent-[var(--brand)]"
        />
        <span className="text-[13px] leading-relaxed text-ink-2">
          <b className="text-ink">Número de WhatsApp exclusivo de esta Cuenta</b> (Premium). Los mensajes que lleguen al número capturado
          arriba verán solo las vacantes de esta Cuenta. Desmarcado, el número maestro es compartido: el candidato ve las vacantes de
          todas las Cuentas activas y su postulación queda en la Cuenta de la vacante que elija.
        </span>
      </label>
    </div>
  );
}

function SeccionCuentas() {
  const { cuentaActualId, refrescar } = useSesion();
  const [cuentas, setCuentas] = useState<DatosCuenta[]>([]);
  const [cargando, setCargando] = useState(true);
  const [nueva, setNueva] = useState(false);
  const [fichaId, setFichaId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [verEliminadas, setVerEliminadas] = useState(false);
  const [ocupada, setOcupada] = useState<number | null>(null);

  const recargar = useCallback(async () => {
    const c = await fetchCuentas(verEliminadas);
    setCuentas(c ?? []);
    setCargando(false);
  }, [verEliminadas]);
  useEffect(() => {
    recargar();
  }, [recargar]);

  // Fase 2: baja lógica / restaurar / predeterminada — el backend valida (Cuenta actual, última activa…)
  async function eliminar(c: DatosCuenta) {
    if (!window.confirm(`¿Eliminar la Cuenta «${c.nombre}»? No se borra nada: deja de aparecer y se puede restaurar después.`)) return;
    setOcupada(c.id);
    setError("");
    const r = await eliminarCuenta(c.id);
    setOcupada(null);
    if (!r.ok) return setError(r.error);
    setOk(`Cuenta «${c.nombre}» eliminada (se puede restaurar desde «Ver eliminadas»).`);
    await recargar();
    await refrescar();
  }
  async function restaurar(c: DatosCuenta) {
    setOcupada(c.id);
    setError("");
    const r = await restaurarCuenta(c.id);
    setOcupada(null);
    if (!r.ok) return setError(r.error);
    setOk(`Cuenta «${c.nombre}» restaurada.`);
    await recargar();
    await refrescar();
  }
  async function predeterminada(c: DatosCuenta) {
    setOcupada(c.id);
    setError("");
    const r = await marcarCuentaPredeterminada(c.id);
    setOcupada(null);
    if (!r.ok) return setError(r.error);
    setOk(`«${c.nombre}» es ahora tu Cuenta predeterminada: con ella arranca tu sesión.`);
    await recargar();
    await refrescar();
  }

  return (
    <Card className="mt-6 p-5">
      <div className="flex items-start justify-between gap-4">
        <CabSeccion
          icono={Building2}
          titulo="Cuentas"
          subtitulo="Empresas reclutadoras que usan Red Human. Solo ves las Cuentas a las que tienes acceso; los datos de una nunca se mezclan con otra."
        />
        <Button size="sm" onClick={() => setNueva(true)}>
          <Plus className="h-4 w-4" /> Nueva cuenta
        </Button>
      </div>

      {error && <div className="mb-3"><Aviso tono="error" onCerrar={() => setError("")}>{error}</Aviso></div>}
      {ok && <div className="mb-3"><Aviso tono="ok" onCerrar={() => setOk("")}>{ok}</Aviso></div>}

      {cargando ? (
        <Loader2 className="h-5 w-5 animate-spin text-ink-3" />
      ) : cuentas.length === 0 ? (
        <p className="text-sm text-ink-3">No tienes ninguna Cuenta asignada.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-border-faint">
          {cuentas.map((c) => (
            <li key={c.id} className="flex items-center gap-3 py-2.5">
              <button type="button" onClick={() => setFichaId(c.id)} className="min-w-0 flex-1 text-left">
                <p className="truncate text-sm font-medium text-ink hover:text-brand">
                  {c.nombre}
                  {c.id === cuentaActualId && <Badge tone="brand" className="ml-2">Actual</Badge>}
                  {c.esPredeterminada && <Badge tone="good" className="ml-2">Predeterminada</Badge>}
                </p>
                <p className="truncate text-[12px] text-ink-3">
                  {c.nombreComercial} · {c.usuarios} usuario(s) · {c.clientes} cliente(s)
                  {c.estado === "Eliminada" && c.eliminadaEn ? ` · eliminada el ${fechaCorta(c.eliminadaEn)}` : ""}
                </p>
                {c.portalUrl && c.estado === "Activa" && (
                  <p className="truncate text-[12px] text-ink-3" title="Bolsa de trabajo pública de esta Cuenta">
                    Portal: <span className="font-mono text-brand">{c.portalUrl}</span>
                  </p>
                )}
              </button>
              <Badge tone={c.estado === "Activa" ? "good" : c.estado === "Eliminada" ? "bad" : "neutral"} dot>{c.estado}</Badge>
              {/* Regla de UI: las acciones secundarias viven en «…»; solo quedan visibles los indicadores */}
              <MenuAcciones
                etiqueta={`Acciones de ${c.nombre}`}
                acciones={
                  c.estado === "Eliminada"
                    ? [{ etiqueta: "Restaurar", icono: <RotateCw />, onClick: () => restaurar(c), disabled: ocupada === c.id }]
                    : [
                        { etiqueta: "Editar", icono: <Pencil />, onClick: () => setFichaId(c.id) },
                        ...(c.estado === "Activa" && !c.esPredeterminada
                          ? [{ etiqueta: "Predeterminar", icono: <Check />, onClick: () => predeterminada(c), disabled: ocupada === c.id, title: "Con esta Cuenta arrancará tu sesión" }]
                          : []),
                        {
                          etiqueta: "Eliminar",
                          icono: <Trash2 />,
                          peligrosa: true,
                          onClick: () => eliminar(c),
                          disabled: ocupada === c.id || c.id === cuentaActualId,
                          title: c.id === cuentaActualId ? "No puedes eliminar la Cuenta con la que estás operando" : "Eliminar (baja lógica)",
                        },
                      ]
                }
              />
            </li>
          ))}
        </ul>
      )}
      <button type="button" onClick={() => setVerEliminadas((v) => !v)} className="mt-3 text-[12px] text-ink-3 hover:text-ink hover:underline">
        {verEliminadas ? "Ocultar cuentas eliminadas" : "Ver cuentas eliminadas"}
      </button>

      {nueva && (
        <FormNuevaCuenta
          onClose={() => setNueva(false)}
          onCreada={(ficha) => {
            setNueva(false);
            recargar();
            setFichaId(ficha.id);
          }}
        />
      )}
      {fichaId !== null && (
        <FichaCuentaModal
          cuentaId={fichaId}
          onClose={() => {
            setFichaId(null);
            recargar();
          }}
        />
      )}
    </Card>
  );
}

function FormNuevaCuenta({ onClose, onCreada }: { onClose: () => void; onCreada: (f: FichaCuenta) => void }) {
  const [f, setF] = useState<FormCuentaState>(cuentaAForm());
  const [logo, setLogo] = useState<File | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState("");
  const set = (k: keyof FormCuentaState, v: string | boolean) => setF((p) => ({ ...p, [k]: v }));

  async function guardar() {
    setGuardando(true);
    setError("");
    const r = await crearCuenta(formACampos(f));
    if (!r.ok) {
      setGuardando(false);
      setError(r.error);
      return;
    }
    let ficha = r.data;
    if (logo) {
      const rl = await subirLogoCuentaPorId(ficha.id, logo);
      if (rl.ok) ficha = rl.data;
    }
    setGuardando(false);
    onCreada(ficha);
  }

  return (
    <Modal
      titulo="Nueva cuenta"
      subtitulo="Quien la crea queda vinculado automáticamente; después agrega a los demás usuarios desde su ficha."
      onClose={onClose}
      pie={
        <>
          <Button variant="outline" size="sm" onClick={onClose} disabled={guardando}>Cancelar</Button>
          <Button size="sm" onClick={guardar} disabled={guardando || !f.nombre.trim()}>
            {guardando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Guardar cuenta
          </Button>
        </>
      }
    >
      {error && <div className="mb-3"><Aviso tono="error">{error}</Aviso></div>}
      <CamposCuentaForm f={f} set={set} />
      <div className="mt-3">
        <label className="mb-1 block text-[12px] font-medium text-ink-2">Logo (opcional)</label>
        <input type="file" accept=".png,.jpg,.jpeg,.svg,.webp" onChange={(e) => setLogo(e.target.files?.[0] ?? null)} className="text-sm" />
      </div>
    </Modal>
  );
}

type TabFicha = "datos" | "usuarios" | "clientes" | "portal";

function FichaCuentaModal({ cuentaId, onClose }: { cuentaId: number; onClose: () => void }) {
  const { cuentaActualId, cambiarCuenta } = useSesion();
  const [ficha, setFicha] = useState<FichaCuenta | null>(null);
  const [tab, setTab] = useState<TabFicha>("datos");
  const [error, setError] = useState("");

  useEffect(() => {
    fetchCuenta(cuentaId).then((f) => {
      if (f) setFicha(f);
      else setError("No se pudo cargar la cuenta.");
    });
  }, [cuentaId]);

  const esActual = cuentaId === cuentaActualId;
  const TABS: { id: TabFicha; texto: string }[] = [
    { id: "datos", texto: "Datos generales" },
    { id: "usuarios", texto: `Usuarios (${ficha?.usuariosDetalle.length ?? 0})` },
    { id: "clientes", texto: `Clientes (${ficha?.clientesDetalle.length ?? 0})` },
    { id: "portal", texto: "Portal / Bolsa de trabajo" },
  ];

  return (
    <Modal titulo={ficha ? ficha.nombre : "Cuenta"} subtitulo={ficha ? `${ficha.nombreComercial}${esActual ? " · Cuenta actual" : ""}` : undefined} onClose={onClose} ancho="max-w-3xl">
      {error && <div className="mb-3"><Aviso tono="error">{error}</Aviso></div>}
      {!ficha ? (
        <Loader2 className="h-5 w-5 animate-spin text-ink-3" />
      ) : (
        <>
          <div className="scroll-x mb-4 gap-1 border-b border-border-soft">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className={cn(
                  "-mb-px shrink-0 whitespace-nowrap border-b-2 px-3 py-2 text-[13px] font-semibold transition",
                  tab === t.id ? "border-brand text-brand" : "border-transparent text-ink-3 hover:text-ink",
                )}
              >
                {t.texto}
              </button>
            ))}
          </div>
          {tab === "datos" && <TabDatosCuenta ficha={ficha} onCambio={setFicha} />}
          {tab === "usuarios" && <TabUsuariosCuenta ficha={ficha} onCambio={setFicha} />}
          {tab === "clientes" && (
            <div>
              {ficha.clientesDetalle.length === 0 ? (
                <p className="text-sm text-ink-3">Esta Cuenta no tiene Clientes todavía.</p>
              ) : (
                <ul className="divide-y divide-border-faint">
                  {ficha.clientesDetalle.map((c) => (
                    <li key={c.id} className="flex items-center gap-3 py-2">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{c.nombre}</p>
                        <p className="text-[12px] text-ink-3">{c.nombreComercial || "—"} · {c.contactos} contacto(s)</p>
                      </div>
                      <Badge tone={c.estado === "Activo" ? "good" : "neutral"} dot>{c.estado}</Badge>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-3 text-[12px] text-ink-3">
                {esActual ? (
                  <>Los Clientes se administran en la sección «Clientes y contactos» de esta misma pantalla.</>
                ) : (
                  <>
                    Para administrar sus Clientes cambia a esta Cuenta:{" "}
                    <button type="button" className="font-semibold text-brand hover:underline" onClick={() => cambiarCuenta(cuentaId)}>
                      Cambiar a esta cuenta →
                    </button>
                  </>
                )}
              </p>
            </div>
          )}
          {tab === "portal" && <TabPortalCuenta ficha={ficha} onCambio={setFicha} />}
        </>
      )}
    </Modal>
  );
}

function TabDatosCuenta({ ficha, onCambio }: { ficha: FichaCuenta; onCambio: (f: FichaCuenta) => void }) {
  const [f, setF] = useState<FormCuentaState>(cuentaAForm(ficha));
  const [guardando, setGuardando] = useState(false);
  const [msg, setMsg] = useState<{ tono: "ok" | "error"; texto: string } | null>(null);
  const set = (k: keyof FormCuentaState, v: string | boolean) => setF((p) => ({ ...p, [k]: v }));

  async function guardar() {
    setGuardando(true);
    setMsg(null);
    const r = await actualizarCuentaPorId(ficha.id, formACampos(f));
    setGuardando(false);
    if (!r.ok) return setMsg({ tono: "error", texto: r.error });
    onCambio(r.data);
    setMsg({ tono: "ok", texto: "Cambios guardados correctamente." });
  }

  return (
    <div>
      {msg && <div className="mb-3"><Aviso tono={msg.tono}>{msg.texto}</Aviso></div>}
      <CamposCuentaForm f={f} set={set} />
      <div className="mt-4 flex justify-end">
        <Button size="sm" onClick={guardar} disabled={guardando}>
          {guardando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Guardar cuenta
        </Button>
      </div>
    </div>
  );
}

function TabPortalCuenta({ ficha, onCambio }: { ficha: FichaCuenta; onCambio: (f: FichaCuenta) => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [subiendo, setSubiendo] = useState(false);
  const [error, setError] = useState("");

  async function onLogo(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setSubiendo(true);
    setError("");
    const r = await subirLogoCuentaPorId(ficha.id, f);
    setSubiendo(false);
    if (!r.ok) return setError(r.error);
    onCambio(r.data);
  }

  return (
    <div>
      {error && <div className="mb-3"><Aviso tono="error">{error}</Aviso></div>}
      <div className="flex items-center gap-4">
        <div className="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-border-soft bg-surface-2">
          {ficha.logo ? <img src={urlArchivo("/" + ficha.logo)} alt="Logo" className="h-full w-full object-contain" /> : <Building2 className="h-7 w-7 text-ink-3" />}
        </div>
        <div>
          <p className="text-[13px] font-medium">Logo de la Cuenta</p>
          <p className="text-[12px] text-ink-3">PNG, JPG, SVG o WebP. Se muestra en el portal de postulación y en los correos.</p>
          <button type="button" onClick={() => fileRef.current?.click()} disabled={subiendo} className="mt-1.5 flex items-center gap-1.5 text-[12px] font-medium text-brand hover:underline disabled:opacity-50">
            {subiendo ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
            {subiendo ? "Subiendo…" : "Cambiar logo"}
          </button>
          <input ref={fileRef} type="file" accept=".png,.jpg,.jpeg,.svg,.webp" className="hidden" onChange={onLogo} />
        </div>
      </div>
      <div className="mt-4 rounded-xl border border-border-soft bg-surface-2/60 p-4 text-[13px]">
        <p><span className="font-semibold">Nombre visible para candidatos:</span> {ficha.portal.nombreComercial}</p>
        <p className="mt-1">
          <span className="font-semibold">Bolsa de trabajo pública:</span>{" "}
          <a href={ficha.portal.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-brand hover:underline">
            {ficha.portal.url} <ExternalLink className="h-3 w-3" />
          </a>
        </p>
        <p className="mt-2 text-[12px] text-ink-3">El portal muestra las vacantes publicadas de la Cuenta activa con este logo y nombre comercial (Fase B).</p>
      </div>
    </div>
  );
}

function TabUsuariosCuenta({ ficha, onCambio }: { ficha: FichaCuenta; onCambio: (f: FichaCuenta) => void }) {
  const { usuario } = useSesion();
  const [mostrarForm, setMostrarForm] = useState(false);
  const [f, setF] = useState({ nombre: "", correo: "", rol: "Usuario" as RolUsuario, puesto: "", telefono: "" });
  const [guardando, setGuardando] = useState(false);
  const [msg, setMsg] = useState<{ tono: "ok" | "error" | "info"; texto: string } | null>(null);
  const [passTemporal, setPassTemporal] = useState<string | null>(null);
  const [quitando, setQuitando] = useState<number | null>(null);

  async function guardar() {
    setGuardando(true);
    setMsg(null);
    setPassTemporal(null);
    const r = await agregarUsuarioCuenta(ficha.id, f);
    setGuardando(false);
    if (!r.ok) return setMsg({ tono: "error", texto: r.error });
    onCambio(r.data.cuenta);
    setMostrarForm(false);
    setF({ nombre: "", correo: "", rol: "Usuario", puesto: "", telefono: "" });
    if (r.data.nuevo) {
      setPassTemporal(r.data.passwordTemporal);
      setMsg({ tono: "ok", texto: `Usuario ${r.data.usuario.nombre} creado y vinculado a esta Cuenta.` });
    } else {
      setMsg({ tono: "info", texto: `${r.data.usuario.nombre} ya existía: se le dio acceso a esta Cuenta (su rol y contraseña no cambian).` });
    }
  }

  async function quitar(u: { id: number; nombre: string }) {
    if (!window.confirm(`¿Quitar a ${u.nombre} de esta Cuenta? El usuario no se borra, solo pierde el acceso a esta Cuenta.`)) return;
    setQuitando(u.id);
    setMsg(null);
    const r = await quitarUsuarioCuenta(ficha.id, u.id);
    setQuitando(null);
    if (!r.ok) return setMsg({ tono: "error", texto: r.error });
    onCambio(r.data);
  }

  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-[13px] text-ink-2">Personas con acceso a esta Cuenta.</p>
        <Button size="sm" onClick={() => { setMostrarForm(true); setMsg(null); }}>
          <UserPlus className="h-4 w-4" /> Agregar usuario
        </Button>
      </div>
      {msg && <div className="mb-3"><Aviso tono={msg.tono}>{msg.texto}</Aviso></div>}
      {passTemporal && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-warn/40 bg-warn-soft px-3.5 py-2.5 text-[13px]">
          <span>Contraseña temporal (se muestra una sola vez; deberá cambiarla al entrar):</span>
          <code className="rounded bg-surface px-2 py-0.5 font-mono">{passTemporal}</code>
          <button type="button" className="inline-flex items-center gap-1 text-brand hover:underline" onClick={() => navigator.clipboard?.writeText(passTemporal)}>
            <Copy className="h-3 w-3" /> Copiar
          </button>
        </div>
      )}
      {mostrarForm && (
        <div className="mb-4 rounded-xl border border-border-soft bg-surface-2 p-4">
          <p className="mb-3 text-sm font-semibold">Nuevo usuario en {ficha.nombre}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Entrada label="Nombre" value={f.nombre} onChange={(v) => setF((p) => ({ ...p, nombre: v }))} placeholder="Ej. María López" />
            <Entrada label="Correo" value={f.correo} onChange={(v) => setF((p) => ({ ...p, correo: v }))} placeholder="correo@empresa.com" type="email" />
            <Selector label="Rol" value={f.rol} onChange={(v) => setF((p) => ({ ...p, rol: v as RolUsuario }))} opciones={["Usuario", "Administrador"]} />
            <Entrada label="Puesto (opcional)" value={f.puesto} onChange={(v) => setF((p) => ({ ...p, puesto: v }))} placeholder="Ej. Reclutadora" />
            <Entrada label="WhatsApp (opcional)" value={f.telefono} onChange={(v) => setF((p) => ({ ...p, telefono: v }))} placeholder="10 dígitos — para avisos como entrevistador" />
          </div>
          <p className="mt-2 text-[12px] text-ink-3">Si el correo ya pertenece a un usuario del sistema, solo se le dará acceso a esta Cuenta.</p>
          <div className="mt-3 flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setMostrarForm(false)} disabled={guardando}><X className="h-4 w-4" /> Cancelar</Button>
            <Button size="sm" onClick={guardar} disabled={guardando || !f.correo.trim()}>
              {guardando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Guardar usuario
            </Button>
          </div>
        </div>
      )}
      <ul className="divide-y divide-border-faint">
        {ficha.usuariosDetalle.map((u) => (
          <li key={u.id} className="flex items-center gap-3 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{u.nombre}{u.id === usuario?.id && <span className="ml-1.5 text-[11px] text-ink-3">(tú)</span>}</p>
              <p className="truncate text-[12px] text-ink-3">{u.correo}{u.puesto ? ` · ${u.puesto}` : ""}{u.telefono ? ` · WhatsApp ${u.telefono}` : ""}</p>
            </div>
            <Badge tone={u.rol === "Administrador" ? "brand" : "neutral"}>{u.rol}</Badge>
            <Badge tone={u.activo ? "good" : "neutral"} dot>{u.activo ? "Activo" : "Inactivo"}</Badge>
            {u.id !== usuario?.id && (
              quitando === u.id ? <Loader2 className="h-4 w-4 animate-spin text-ink-3" /> : <BotonEliminar onClick={() => quitar(u)} title="Quitar de esta Cuenta" />
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ================================================================== */
/* 2. Usuarios y permisos (Cuenta actual) — sin cambios funcionales     */
/* ================================================================== */

function SeccionUsuarios() {
  const [usuarios, setUsuarios] = useState<UsuarioRH[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState("");
  const [mostrarForm, setMostrarForm] = useState(false);
  const [editando, setEditando] = useState<UsuarioRH | null>(null);

  useEffect(() => {
    fetchUsuarios().then((u) => {
      setUsuarios(u ?? []);
      setCargando(false);
    });
  }, []);

  return (
    <Card className="mt-4 p-5">
      <div className="flex items-start justify-between gap-4">
        <CabSeccion
          icono={Users}
          titulo="Usuarios y permisos"
          subtitulo="Personas de RH con acceso a la Cuenta actual. Para dar acceso a otra Cuenta usa su ficha en «Cuentas»."
        />
        <div className="flex shrink-0 items-center gap-2">
          <BotonCargaMasiva tipo="usuarios" onTerminado={() => fetchUsuarios().then((u) => setUsuarios(u ?? []))} />
          <Button size="sm" onClick={() => { setMostrarForm(true); setEditando(null); }}>
            <Plus className="h-4 w-4" /> Agregar
          </Button>
        </div>
      </div>

      {error && <div className="mb-3"><Aviso tono="error">{error}</Aviso></div>}

      {mostrarForm && (
        <FormUsuario
          usuario={editando}
          onGuardado={(u) => {
            setUsuarios((prev) =>
              editando ? prev.map((x) => (x.id === u.id ? u : x)) : [...prev, u].sort((a, b) => a.nombre.localeCompare(b.nombre)),
            );
            setMostrarForm(false);
            setEditando(null);
          }}
          onCancelar={() => { setMostrarForm(false); setEditando(null); }}
          onError={setError}
        />
      )}

      {cargando ? (
        <Loader2 className="h-5 w-5 animate-spin text-ink-3" />
      ) : usuarios.length === 0 ? (
        <p className="text-sm text-ink-3">No hay usuarios en esta Cuenta.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-border-faint">
          {usuarios.map((u) => (
            <li key={u.id} className="flex items-center gap-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{u.nombre}</p>
                <p className="truncate text-[12px] text-ink-3">{u.correo}{u.puesto ? ` · ${u.puesto}` : ""}{u.telefono ? ` · WhatsApp ${u.telefono}` : ""}</p>
              </div>
              <Badge tone={u.rol === "Administrador" ? "brand" : "neutral"}>{u.rol}</Badge>
              <Badge tone={u.activo ? "good" : "neutral"} dot>{u.activo ? "Activo" : "Inactivo"}</Badge>
              <MenuAcciones
                etiqueta={`Acciones de ${u.nombre}`}
                acciones={[{ etiqueta: "Editar", icono: <Pencil />, onClick: () => { setEditando(u); setMostrarForm(true); setError(""); } }]}
              />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function FormUsuario({
  usuario,
  onGuardado,
  onCancelar,
  onError,
}: {
  usuario: UsuarioRH | null;
  onGuardado: (u: UsuarioRH) => void;
  onCancelar: () => void;
  onError: (e: string) => void;
}) {
  const [nombre, setNombre] = useState(usuario?.nombre ?? "");
  const [correo, setCorreo] = useState(usuario?.correo ?? "");
  const [puesto, setPuesto] = useState(usuario?.puesto ?? "");
  const [telefono, setTelefono] = useState(usuario?.telefono ?? "");
  const [rol, setRol] = useState<RolUsuario>(usuario?.rol ?? "Usuario");
  const [activo, setActivo] = useState(usuario?.activo ?? true);
  // Evaluaciones (2026-09-28): permiso para ver informes médicos completos (el Administrador siempre lo tiene)
  const [accesoMedico, setAccesoMedico] = useState(Boolean(usuario?.accesoInformesMedicos));
  const [password, setPassword] = useState("");
  const [guardando, setGuardando] = useState(false);

  async function guardar() {
    setGuardando(true);
    onError("");
    let r;
    if (usuario) {
      const cambios: Parameters<typeof actualizarUsuario>[1] = { nombre, puesto, telefono, rol, activo, acceso_informes_medicos: accesoMedico };
      if (password) cambios.password = password;
      r = await actualizarUsuario(usuario.id, cambios);
    } else {
      if (!password) { onError("La contraseña es obligatoria al crear un usuario."); setGuardando(false); return; }
      r = await crearUsuario({ correo, nombre, puesto, telefono, rol, password });
    }
    setGuardando(false);
    if (!r.ok) { onError(r.error); return; }
    onGuardado(r.data);
  }

  return (
    <div className="mb-4 rounded-xl border border-border-soft bg-surface-2 p-4">
      <p className="mb-3 text-sm font-semibold">{usuario ? `Editar: ${usuario.nombre}` : "Nuevo usuario"}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Entrada label="Nombre completo" value={nombre} onChange={setNombre} placeholder="Ej. María López" />
        {!usuario && <Entrada label="Correo" value={correo} onChange={setCorreo} placeholder="correo@empresa.com" type="email" />}
        <Entrada label="Puesto" value={puesto} onChange={setPuesto} placeholder="Ej. Reclutadora" />
        {/* Fase 7A: WhatsApp del perfil — lo usa la notificación cuando esta persona es Entrevistador */}
        <Entrada label="WhatsApp (opcional)" value={telefono} onChange={setTelefono} placeholder="10 dígitos — para avisos como entrevistador" />
        <Selector label="Rol" value={rol} onChange={(v) => setRol(v as RolUsuario)} opciones={["Usuario", "Administrador"]} />
        <Entrada label={usuario ? "Nueva contraseña (opcional)" : "Contraseña"} value={password} onChange={setPassword} type="password" placeholder="Mínimo 8 caracteres" />
        {usuario && (
          <div className="flex items-center gap-2 pt-5">
            <input type="checkbox" id={`activo-${usuario.id}`} checked={activo} onChange={(e) => setActivo(e.target.checked)} className="h-4 w-4 rounded accent-brand" />
            <label htmlFor={`activo-${usuario.id}`} className="text-sm">Usuario activo</label>
          </div>
        )}
        {usuario && (
          <div className="flex items-start gap-2 sm:col-span-2">
            <input type="checkbox" id={`medico-${usuario.id}`} checked={rol === "Administrador" || accesoMedico} disabled={rol === "Administrador"}
              onChange={(e) => setAccesoMedico(e.target.checked)} className="mt-0.5 h-4 w-4 rounded accent-brand" />
            <label htmlFor={`medico-${usuario.id}`} className="text-sm">
              Puede ver informes médicos completos
              <span className="block text-[11px] text-ink-3">Dato sensible (LFPDPPP). Sin este permiso solo ve el estado y el dictamen. El Administrador siempre lo tiene.</span>
            </label>
          </div>
        )}
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onCancelar} disabled={guardando}><X className="h-4 w-4" /> Cancelar</Button>
        <Button size="sm" onClick={guardar} disabled={guardando}>
          {guardando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          {guardando ? "Guardando…" : "Guardar"}
        </Button>
      </div>
    </div>
  );
}

/* ================================================================== */
/* 3. Clientes y contactos (Punto 10)                                  */
/* ================================================================== */

type FormClienteState = { nombre: string; razonSocial: string; nombreComercial: string; estado: "Activo" | "Inactivo" };
const clienteAForm = (c?: Cliente | null): FormClienteState => ({
  nombre: c?.nombre ?? "",
  razonSocial: c?.razonSocial ?? "",
  nombreComercial: c?.nombreComercial ?? "",
  estado: c?.estado ?? "Activo",
});
const clienteACampos = (f: FormClienteState): CamposCliente & { nombre: string } => ({
  nombre: f.nombre,
  razon_social: f.razonSocial,
  nombre_comercial: f.nombreComercial,
  estado: f.estado,
});

function CamposClienteForm({ f, set }: { f: FormClienteState; set: (k: keyof FormClienteState, v: string) => void }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Entrada label="Nombre del cliente" value={f.nombre} onChange={(v) => set("nombre", v)} placeholder="Ej. Distribuidora Norte" className="sm:col-span-2" />
      <Entrada label="Razón social" value={f.razonSocial} onChange={(v) => set("razonSocial", v)} placeholder="Ej. Distribuidora Norte S.A. de C.V." />
      <Entrada label="Nombre comercial" value={f.nombreComercial} onChange={(v) => set("nombreComercial", v)} placeholder="Lo que ven los candidatos (si aplica)" />
      <Selector label="Estatus" value={f.estado} onChange={(v) => set("estado", v)} opciones={["Activo", "Inactivo"]} />
    </div>
  );
}

function SeccionClientes() {
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [cargando, setCargando] = useState(true);
  const [nuevo, setNuevo] = useState(false);
  const [fichaId, setFichaId] = useState<number | null>(null);

  const recargar = useCallback(async () => {
    const c = await fetchClientes();
    setClientes(c ?? []);
    setCargando(false);
  }, []);
  useEffect(() => {
    recargar();
  }, [recargar]);

  return (
    <Card className="mt-4 p-5">
      <div className="flex items-start justify-between gap-4">
        <CabSeccion
          icono={Briefcase}
          titulo="Clientes y contactos"
          subtitulo="Empresas para las que recluta tu Cuenta y sus personas de contacto (reciben las notificaciones de Cliente). Se eligen al crear una vacante; si no hay ninguno, ese selector no aparece."
        />
        <div className="flex shrink-0 items-center gap-2">
          <BotonCargaMasiva tipo="clientes" onTerminado={recargar} />
          <Button size="sm" onClick={() => setNuevo(true)}>
            <Plus className="h-4 w-4" /> Nuevo cliente
          </Button>
        </div>
      </div>

      {cargando ? (
        <Loader2 className="h-5 w-5 animate-spin text-ink-3" />
      ) : clientes.length === 0 ? (
        <p className="text-sm text-ink-3">Aún no hay Clientes. Sin Clientes, la Cuenta recluta directo para sí misma.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-sm">
            <thead>
              <tr className="border-b border-border-faint text-left text-[11px] uppercase tracking-wide text-ink-3">
                <th className="py-2 pr-3 font-medium">Cliente</th>
                <th className="py-2 pr-3 font-medium">Nombre comercial</th>
                <th className="py-2 pr-3 text-center font-medium">Contactos</th>
                <th className="py-2 font-medium">Estatus</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {clientes.map((c) => (
                <tr key={c.id} className="border-b border-border-faint last:border-0">
                  <td className="py-2.5 pr-3">
                    <button type="button" onClick={() => setFichaId(c.id)} className="font-medium text-ink hover:text-brand hover:underline">{c.nombre}</button>
                  </td>
                  <td className="py-2.5 pr-3 text-ink-2">{c.nombreComercial || "—"}</td>
                  <td className="py-2.5 pr-3 text-center text-ink-2">{c.contactos}</td>
                  <td className="py-2.5"><Badge tone={c.estado === "Activo" ? "good" : "neutral"} dot>{c.estado}</Badge></td>
                  <td className="py-2.5 text-right">
                    <MenuAcciones etiqueta={`Acciones de ${c.nombre}`} acciones={[{ etiqueta: "Editar", icono: <Pencil />, onClick: () => setFichaId(c.id) }]} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {nuevo && (
        <FormNuevoCliente
          onClose={() => setNuevo(false)}
          onCreado={(c) => {
            setNuevo(false);
            recargar();
            setFichaId(c.id);
          }}
        />
      )}
      {fichaId !== null && (
        <FichaClienteModal
          clienteId={fichaId}
          onClose={() => {
            setFichaId(null);
            recargar();
          }}
        />
      )}
    </Card>
  );
}

function FormNuevoCliente({ onClose, onCreado }: { onClose: () => void; onCreado: (c: Cliente) => void }) {
  const [f, setF] = useState<FormClienteState>(clienteAForm());
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState("");
  const set = (k: keyof FormClienteState, v: string) => setF((p) => ({ ...p, [k]: v }));

  async function guardar() {
    setGuardando(true);
    setError("");
    const r = await crearCliente(clienteACampos(f));
    setGuardando(false);
    if (!r.ok) return setError(r.error);
    onCreado(r.data);
  }

  return (
    <Modal
      titulo="Nuevo cliente"
      onClose={onClose}
      ancho="max-w-xl"
      pie={
        <>
          <Button variant="outline" size="sm" onClick={onClose} disabled={guardando}>Cancelar</Button>
          <Button size="sm" onClick={guardar} disabled={guardando || !f.nombre.trim()}>
            {guardando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Guardar cliente
          </Button>
        </>
      }
    >
      {error && <div className="mb-3"><Aviso tono="error">{error}</Aviso></div>}
      <CamposClienteForm f={f} set={set} />
    </Modal>
  );
}

const CONTACTO_VACIO: CamposContacto = { nombre: "", apellidos: "", puesto: "", correo: "", telefono: "" };

function FichaClienteModal({ clienteId, onClose }: { clienteId: number; onClose: () => void }) {
  const [cliente, setCliente] = useState<Cliente | null>(null);
  const [editando, setEditando] = useState(false);
  const [f, setF] = useState<FormClienteState>(clienteAForm());
  const [formContacto, setFormContacto] = useState<{ id: number | null; datos: CamposContacto } | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [msg, setMsg] = useState<{ tono: "ok" | "error"; texto: string } | null>(null);

  useEffect(() => {
    fetchCliente(clienteId).then((c) => {
      if (c) {
        setCliente(c);
        setF(clienteAForm(c));
      } else setMsg({ tono: "error", texto: "No se pudo cargar el cliente." });
    });
  }, [clienteId]);

  async function guardarCliente() {
    setOcupado(true);
    setMsg(null);
    const r = await actualizarCliente(clienteId, clienteACampos(f));
    setOcupado(false);
    if (!r.ok) return setMsg({ tono: "error", texto: r.error });
    setCliente(r.data);
    setEditando(false);
    setMsg({ tono: "ok", texto: "Cliente actualizado." });
  }

  async function guardarContacto() {
    if (!formContacto) return;
    setOcupado(true);
    setMsg(null);
    const r = formContacto.id
      ? await editarContactoCliente(clienteId, formContacto.id, formContacto.datos)
      : await agregarContactoCliente(clienteId, formContacto.datos);
    setOcupado(false);
    if (!r.ok) return setMsg({ tono: "error", texto: r.error });
    setCliente(r.data);
    setFormContacto(null);
  }

  async function eliminarContacto(k: ContactoCliente) {
    if (!window.confirm(`¿Eliminar el contacto ${k.nombreCompleto}? Dejará de recibir notificaciones de este Cliente.`)) return;
    setOcupado(true);
    const r = await eliminarContactoCliente(clienteId, k.id);
    setOcupado(false);
    if (!r.ok) return setMsg({ tono: "error", texto: r.error });
    setCliente(r.data);
  }

  const setC = (k: keyof CamposContacto, v: string) => setFormContacto((p) => (p ? { ...p, datos: { ...p.datos, [k]: v } } : p));

  return (
    <Modal titulo={cliente?.nombre ?? "Cliente"} subtitulo={cliente ? `${cliente.razonSocial || "Sin razón social"} · ${cliente.estado}` : undefined} onClose={onClose} ancho="max-w-2xl">
      {msg && <div className="mb-3"><Aviso tono={msg.tono} onCerrar={() => setMsg(null)}>{msg.texto}</Aviso></div>}
      {!cliente ? (
        <Loader2 className="h-5 w-5 animate-spin text-ink-3" />
      ) : (
        <div className="flex flex-col gap-6">
          {/* Datos generales */}
          <section>
            <div className="mb-2 flex items-center justify-between">
              <h4 className="text-sm font-semibold">Datos generales</h4>
              {!editando && (
                <Button variant="outline" size="sm" onClick={() => setEditando(true)}><Pencil className="h-3.5 w-3.5" /> Editar cliente</Button>
              )}
            </div>
            {editando ? (
              <div className="rounded-xl border border-border-soft bg-surface-2 p-4">
                <CamposClienteForm f={f} set={(k, v) => setF((p) => ({ ...p, [k]: v }))} />
                <div className="mt-3 flex justify-end gap-2">
                  <Button variant="outline" size="sm" onClick={() => { setEditando(false); setF(clienteAForm(cliente)); }} disabled={ocupado}>Cancelar</Button>
                  <Button size="sm" onClick={guardarCliente} disabled={ocupado || !f.nombre.trim()}>
                    {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Guardar cliente
                  </Button>
                </div>
              </div>
            ) : (
              <dl className="grid gap-x-6 gap-y-1.5 text-[13px] sm:grid-cols-2">
                <div><dt className="text-ink-3">Nombre</dt><dd className="font-medium">{cliente.nombre}</dd></div>
                <div><dt className="text-ink-3">Razón social</dt><dd className="font-medium">{cliente.razonSocial || "—"}</dd></div>
                <div><dt className="text-ink-3">Nombre comercial</dt><dd className="font-medium">{cliente.nombreComercial || "—"}</dd></div>
                <div><dt className="text-ink-3">Estatus</dt><dd><Badge tone={cliente.estado === "Activo" ? "good" : "neutral"} dot>{cliente.estado}</Badge></dd></div>
              </dl>
            )}
          </section>

          {/* Contactos */}
          <section>
            <div className="mb-2 flex items-center justify-between">
              <h4 className="text-sm font-semibold">Contactos</h4>
              <Button size="sm" onClick={() => setFormContacto({ id: null, datos: CONTACTO_VACIO })} disabled={Boolean(formContacto)}>
                <Plus className="h-4 w-4" /> Agregar contacto
              </Button>
            </div>
            {formContacto && (
              <div className="mb-3 rounded-xl border border-border-soft bg-surface-2 p-4">
                <p className="mb-3 text-sm font-semibold">{formContacto.id ? "Editar contacto" : "Nuevo contacto"}</p>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Entrada label="Nombre" value={formContacto.datos.nombre} onChange={(v) => setC("nombre", v)} />
                  <Entrada label="Apellidos" value={formContacto.datos.apellidos ?? ""} onChange={(v) => setC("apellidos", v)} />
                  <Entrada label="Puesto" value={formContacto.datos.puesto ?? ""} onChange={(v) => setC("puesto", v)} placeholder="Ej. Gerente de sucursal" />
                  <Entrada label="Correo" value={formContacto.datos.correo ?? ""} onChange={(v) => setC("correo", v)} type="email" />
                  <Entrada label="WhatsApp / teléfono" value={formContacto.datos.telefono ?? ""} onChange={(v) => setC("telefono", v)} placeholder="10 dígitos" />
                </div>
                <div className="mt-3 flex justify-end gap-2">
                  <Button variant="outline" size="sm" onClick={() => setFormContacto(null)} disabled={ocupado}>Cancelar</Button>
                  <Button size="sm" onClick={guardarContacto} disabled={ocupado || !formContacto.datos.nombre.trim()}>
                    {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Guardar contacto
                  </Button>
                </div>
              </div>
            )}
            {(cliente.listaContactos ?? []).length === 0 ? (
              <p className="text-[13px] text-ink-3">Sin contactos. Las notificaciones de Cliente no tendrán a quién llegar hasta que agregues uno.</p>
            ) : (
              <ul className="divide-y divide-border-faint">
                {(cliente.listaContactos ?? []).map((k) => (
                  <li key={k.id} className="flex items-center gap-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{k.nombreCompleto}{k.puesto && <span className="ml-1.5 text-[12px] font-normal text-ink-3">· {k.puesto}</span>}</p>
                      <p className="truncate text-[12px] text-ink-3">{[k.correo, k.telefono].filter(Boolean).join(" · ") || "Sin datos de contacto"}</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => setFormContacto({ id: k.id, datos: { nombre: k.nombre, apellidos: k.apellidos, puesto: k.puesto, correo: k.correo, telefono: k.telefono } })}
                      className="grid h-7 w-7 place-items-center rounded-lg text-ink-3 hover:bg-surface-2 hover:text-brand"
                      aria-label={`Editar ${k.nombreCompleto}`}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <BotonEliminar onClick={() => eliminarContacto(k)} />
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </Modal>
  );
}

/* ================================================================== */
/* 4. Plantillas (Punto 11) — administradas aquí, mismo formulario que Nueva vacante */
/* ================================================================== */

function SeccionPlantillas() {
  const [plantillas, setPlantillas] = useState<Plantilla[]>([]);
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [cargando, setCargando] = useState(true);
  const [editor, setEditor] = useState<{ plantilla: Plantilla | null } | null>(null);
  const [msg, setMsg] = useState<{ tono: "ok" | "error"; texto: string } | null>(null);
  const [ocupado, setOcupado] = useState<number | null>(null);

  const recargar = useCallback(async () => {
    const [p, c] = await Promise.all([fetchPlantillas(), fetchClientes("Activo")]);
    setPlantillas(p ?? []);
    setClientes(c ?? []);
    setCargando(false);
  }, []);
  useEffect(() => {
    recargar();
  }, [recargar]);

  async function duplicar(p: Plantilla) {
    setOcupado(p.id);
    setMsg(null);
    const r = await duplicarPlantilla(p.id);
    setOcupado(null);
    if (!r.ok) return setMsg({ tono: "error", texto: r.error });
    await recargar();
    setMsg({ tono: "ok", texto: `Plantilla duplicada como «${r.data.nombre}».` });
  }

  async function eliminar(p: Plantilla) {
    if (!window.confirm(`¿Eliminar la plantilla «${p.nombre}»? Las vacantes ya creadas con ella no se tocan.`)) return;
    setOcupado(p.id);
    setMsg(null);
    const r = await eliminarPlantilla(p.id);
    setOcupado(null);
    if (!r.ok) return setMsg({ tono: "error", texto: r.error });
    await recargar();
  }

  return (
    <Card className="mt-4 p-5">
      <div className="flex items-start justify-between gap-4">
        <CabSeccion
          icono={Briefcase}
          titulo="Plantillas"
          subtitulo="Contenido reutilizable para crear vacantes en un clic. Mismo formulario que «Nueva vacante»; desde una vacante existente también puedes «Guardar como plantilla»."
        />
        <div className="flex shrink-0 items-center gap-2">
          <BotonCargaMasiva tipo="plantillas" onTerminado={recargar} />
          <Button size="sm" onClick={() => setEditor({ plantilla: null })}>
            <Plus className="h-4 w-4" /> Nueva plantilla
          </Button>
        </div>
      </div>

      {msg && <div className="mb-3"><Aviso tono={msg.tono} onCerrar={() => setMsg(null)}>{msg.texto}</Aviso></div>}

      {cargando ? (
        <Loader2 className="h-5 w-5 animate-spin text-ink-3" />
      ) : plantillas.length === 0 ? (
        <p className="text-sm text-ink-3">Aún no hay plantillas en esta Cuenta.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-sm">
            <thead>
              <tr className="border-b border-border-faint text-left text-[11px] uppercase tracking-wide text-ink-3">
                <th className="py-2 pr-3 font-medium">Nombre de plantilla</th>
                <th className="py-2 pr-3 font-medium">Puesto</th>
                <th className="py-2 pr-3 font-medium">Última actualización</th>
                <th className="py-2 text-right font-medium">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {plantillas.map((p) => (
                <tr key={p.id} className="border-b border-border-faint last:border-0">
                  <td className="py-2.5 pr-3">
                    <button type="button" onClick={() => setEditor({ plantilla: p })} className="font-medium text-ink hover:text-brand hover:underline">{p.nombre}</button>
                    <p className="text-[11px] text-ink-3">{p.clienteNombre ? `Cliente: ${p.clienteNombre}` : "General de la Cuenta"}</p>
                  </td>
                  <td className="py-2.5 pr-3 text-ink-2">{p.titulo || "—"}</td>
                  <td className="py-2.5 pr-3 text-ink-2">{fechaCorta(p.actualizada)}</td>
                  <td className="py-2.5 text-right">
                    <div className="inline-flex items-center gap-1">
                      <MenuAcciones
                        etiqueta={`Acciones de ${p.nombre}`}
                        acciones={[
                          { etiqueta: "Editar", icono: <Pencil />, onClick: () => setEditor({ plantilla: p }) },
                          { etiqueta: "Duplicar", icono: <Copy />, onClick: () => duplicar(p), disabled: ocupado === p.id },
                          { etiqueta: "Eliminar", icono: <Trash2 />, peligrosa: true, onClick: () => eliminar(p), disabled: ocupado === p.id },
                        ]}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editor && (
        <EditorPlantilla
          plantilla={editor.plantilla}
          clientes={clientes}
          onClose={() => setEditor(null)}
          onGuardada={(nombre) => {
            setEditor(null);
            recargar();
            setMsg({ tono: "ok", texto: `Plantilla «${nombre}» guardada.` });
          }}
        />
      )}
    </Card>
  );
}

function EditorPlantilla({
  plantilla,
  clientes,
  onClose,
  onGuardada,
}: {
  plantilla: Plantilla | null;
  clientes: Cliente[];
  onClose: () => void;
  onGuardada: (nombre: string) => void;
}) {
  const [nombre, setNombre] = useState(plantilla?.nombre ?? "");
  const [clienteId, setClienteId] = useState<string>(plantilla?.clienteId ? String(plantilla.clienteId) : "");
  const [contenido, setContenido] = useState<ContenidoVacante>(plantilla ? contenidoDesdePlantilla(plantilla) : CONTENIDO_VACIO);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState("");

  async function guardar() {
    if (!nombre.trim()) return setError("El nombre de la plantilla es obligatorio.");
    if (!contenido.titulo.trim()) return setError("El nombre del puesto es obligatorio.");
    setGuardando(true);
    setError("");
    const payload = { nombre: nombre.trim(), cliente_id: clienteId ? Number(clienteId) : null, ...contenidoComoPayload(contenido) };
    const r = plantilla ? await actualizarPlantilla(plantilla.id, payload) : await crearPlantilla(payload);
    setGuardando(false);
    if (!r.ok) return setError(r.error);
    onGuardada(r.data.nombre);
  }

  return (
    <Modal
      titulo={plantilla ? `Editar plantilla: ${plantilla.nombre}` : "Nueva plantilla"}
      subtitulo="Los mismos campos que «Nueva vacante». Al usar la plantilla, todo esto se precarga."
      onClose={onClose}
      ancho="max-w-3xl"
      pie={
        <>
          {error && <span className="mr-auto self-center text-[13px] text-bad">{error}</span>}
          <Button variant="outline" size="sm" onClick={onClose} disabled={guardando}>Cancelar</Button>
          <Button size="sm" onClick={guardar} disabled={guardando}>
            {guardando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} {plantilla ? "Guardar cambios" : "Guardar plantilla"}
          </Button>
        </>
      }
    >
      <div className="mb-6 grid gap-4 sm:grid-cols-2">
        <Field label="Nombre de plantilla" value={nombre} onChange={setNombre} placeholder="Ej. Cajero base sucursal" />
        {clientes.length > 0 ? (
          <Selector
            label="Alcance"
            value={clienteId}
            onChange={setClienteId}
            opciones={[{ valor: "", texto: "General de la Cuenta" }, ...clientes.map((c) => ({ valor: String(c.id), texto: `Cliente: ${c.nombre}` }))]}
          />
        ) : (
          <div className="flex items-end pb-2 text-[12px] text-ink-3">General de la Cuenta (no hay Clientes activos).</div>
        )}
      </div>
      {/* Fase 4 (Punto 1): el generador usa el Cliente del alcance para resolver el nombre de empresa. */}
      <FormularioContenidoVacante value={contenido} onChange={setContenido} clienteId={clienteId ? Number(clienteId) : null} />
    </Modal>
  );
}

/* ================================================================== */
/* 5. Notificaciones (Punto 12) — configuración predeterminada + Guardar */
/* ================================================================== */

const CAMPOS_REGLA: (keyof Omit<ReglaNotificacion, "evento">)[] = [
  "candidatoCorreo", "candidatoWhatsapp", "entrevistadorCorreo", "entrevistadorWhatsapp", "clienteCorreo", "clienteWhatsapp",
];

function SeccionNotificaciones() {
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [guardadas, setGuardadas] = useState<ReglaNotificacion[]>([]);
  const [reglas, setReglas] = useState<ReglaNotificacion[]>([]);
  const [cargando, setCargando] = useState(true);
  const [guardando, setGuardando] = useState(false);
  const [msg, setMsg] = useState<{ tono: "ok" | "error"; texto: string } | null>(null);

  useEffect(() => {
    Promise.all([fetchClientes(), fetchReglasNotificacion()]).then(([c, r]) => {
      setClientes(c ?? []);
      setGuardadas(r ?? []);
      setReglas(r ?? []);
      setCargando(false);
    });
  }, []);

  const hayClienteActivo = clientes.some((c) => c.estado === "Activo");
  const hayCambios = reglas.some((r) => {
    const g = guardadas.find((x) => x.evento === r.evento);
    return !g || CAMPOS_REGLA.some((k) => g[k] !== r[k]);
  });

  function alternar(evento: ReglaNotificacion["evento"], campo: keyof Omit<ReglaNotificacion, "evento">) {
    setReglas((prev) => prev.map((r) => (r.evento === evento ? { ...r, [campo]: !r[campo] } : r)));
  }

  async function guardar() {
    setGuardando(true);
    setMsg(null);
    const r = await guardarReglasNotificacion(reglas);
    setGuardando(false);
    if (!r.ok) return setMsg({ tono: "error", texto: r.error });
    setGuardadas(r.data);
    setReglas(r.data);
    invalidarReglasNotificacion();
    setMsg({ tono: "ok", texto: "Configuración de notificaciones guardada." });
  }

  return (
    <Card className="mt-4 p-5">
      <CabSeccion
        icono={Bell}
        titulo="Notificaciones"
        subtitulo="Comportamiento predeterminado: para cada evento, quién se entera y por qué canal. Al ejecutar una acción (p. ej. agendar entrevista) puedes ajustarlo solo para esa vez sin cambiar esto."
      />

      {msg && <div className="mb-3"><Aviso tono={msg.tono} onCerrar={() => setMsg(null)}>{msg.texto}</Aviso></div>}

      {cargando ? (
        <Loader2 className="h-5 w-5 animate-spin text-ink-3" />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] border-collapse text-sm">
              <thead>
                <tr className="border-b border-border-faint text-left text-[11px] uppercase tracking-wide text-ink-3">
                  <th className="py-2 pr-3 font-medium">Evento</th>
                  <th className="px-2 py-2 text-center font-medium" colSpan={2}>Candidato</th>
                  <th className="px-2 py-2 text-center font-medium" colSpan={2}>Entrevistador</th>
                  {hayClienteActivo && <th className="px-2 py-2 text-center font-medium" colSpan={2}>Cliente</th>}
                </tr>
                <tr className="border-b border-border-faint text-center text-[11px] text-ink-3">
                  <th />
                  <th className="px-2 pb-1.5 font-normal">Correo</th>
                  <th className="px-2 pb-1.5 font-normal">WhatsApp</th>
                  <th className="px-2 pb-1.5 font-normal">Correo</th>
                  <th className="px-2 pb-1.5 font-normal">WhatsApp</th>
                  {hayClienteActivo && (
                    <>
                      <th className="px-2 pb-1.5 font-normal">Correo</th>
                      <th className="px-2 pb-1.5 font-normal">WhatsApp</th>
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {EVENTOS_NOTIFICACION.map((evento) => {
                  const regla = reglas.find((r) => r.evento === evento);
                  if (!regla) return null;
                  const columnas = hayClienteActivo ? CAMPOS_REGLA : CAMPOS_REGLA.slice(0, 4);
                  return (
                    <tr key={evento} className="border-b border-border-faint last:border-0">
                      <td className="py-2 pr-3 text-[13px]">{NOMBRE_EVENTO_NOTIFICACION[evento]}</td>
                      {columnas.map((campo) => (
                        <td key={campo} className="px-2 py-2 text-center">
                          <input
                            type="checkbox"
                            checked={regla[campo]}
                            onChange={() => alternar(evento, campo)}
                            className="h-4 w-4 rounded border-border-soft accent-brand"
                            aria-label={`${NOMBRE_EVENTO_NOTIFICACION[evento]} — ${campo}`}
                          />
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-4 flex items-center justify-end gap-3">
            {hayCambios && <span className="text-[12px] text-warn">Cambios sin guardar</span>}
            <Button size="sm" onClick={guardar} disabled={guardando || !hayCambios}>
              {guardando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Guardar configuración de notificaciones
            </Button>
          </div>
          <p className="mt-2 text-[12px] leading-relaxed text-ink-3">
            Los recordatorios automáticos usan directamente esta configuración, sin pedir confirmación.
          </p>
        </>
      )}
    </Card>
  );
}

/* ================================================================== */
/* 6. Modo prueba (Punto 13)                                            */
/* ================================================================== */

function SeccionModoPrueba() {
  const { refrescar } = useSesion();
  const [cfg, setCfg] = useState<ConfiguracionSistema | null>(null);
  const [ventana, setVentana] = useState("60");
  const [cargando, setCargando] = useState(true);
  const [guardando, setGuardando] = useState(false);
  const [borrando, setBorrando] = useState(false);
  const [confirmando, setConfirmando] = useState(false);
  const [resumen, setResumen] = useState<ResumenBorradoPrueba | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchConfiguracion().then((d) => {
      setCfg(d);
      if (d) setVentana(String(d.modoPruebaVentanaMin));
      setCargando(false);
    });
  }, []);

  async function alternar() {
    if (!cfg) return;
    setGuardando(true);
    setError("");
    const r = await actualizarConfiguracion({ modoPrueba: !cfg.modoPrueba });
    setGuardando(false);
    if (!r.ok) { setError(r.error); return; }
    setCfg(r.data);
    refrescar(); // el shell (badge "Modo prueba", botón Reiniciar prueba) lee modoPrueba de la sesión
  }

  async function guardarVentana() {
    const n = parseInt(ventana, 10);
    if (isNaN(n) || n < 5 || n > 1440) return setError("La ventana debe estar entre 5 y 1440 minutos.");
    setGuardando(true);
    setError("");
    const r = await actualizarConfiguracion({ modoPruebaVentanaMin: n });
    setGuardando(false);
    if (!r.ok) { setError(r.error); return; }
    setCfg(r.data);
  }

  // Fase 3: parámetros del cron de recordatorios de documentos
  const [recDias, setRecDias] = useState("");
  const [recHora, setRecHora] = useState("");
  const [recEntrevista, setRecEntrevista] = useState("");  // 2026-09-19: horas antes de la Entrevista Humana
  useEffect(() => {
    if (cfg) {
      setRecDias(String(cfg.recordatorioDocumentosDias ?? 2));
      setRecHora(String(cfg.recordatorioDocumentosHora ?? 10));
      setRecEntrevista(String(cfg.recordatorioEntrevistaHoras ?? 24));
    }
  }, [cfg]);
  const recCambio = cfg ? recDias !== String(cfg.recordatorioDocumentosDias) || recHora !== String(cfg.recordatorioDocumentosHora) || recEntrevista !== String(cfg.recordatorioEntrevistaHoras ?? 24) : false;
  async function guardarRecordatorios() {
    const d = parseInt(recDias, 10);
    const h = parseInt(recHora, 10);
    const e = parseInt(recEntrevista, 10);
    if (isNaN(d) || d < 1 || d > 30) return setError("Los recordatorios deben ser cada 1 a 30 días.");
    if (isNaN(h) || h < 0 || h > 23) return setError("La hora debe estar entre 0 y 23.");
    if (isNaN(e) || e < 0 || e > 168) return setError("El recordatorio de entrevista debe ser entre 0 (apagado) y 168 horas.");
    setGuardando(true);
    setError("");
    const r = await actualizarConfiguracion({ recordatorioDocumentosDias: d, recordatorioDocumentosHora: h, recordatorioEntrevistaHoras: e });
    setGuardando(false);
    if (!r.ok) { setError(r.error); return; }
    setCfg(r.data);
  }

  async function confirmarBorrado() {
    setBorrando(true);
    setError("");
    const r = await eliminarCandidatosPrueba();
    setBorrando(false);
    if (!r.ok) { setError(r.error); return; }
    setResumen(r.data);
    setConfirmando(false);
    setCfg((prev) => (prev ? { ...prev, candidatosPrueba: 0, postulacionesPrueba: 0 } : prev));
  }

  const ventanaCambio = cfg ? ventana !== String(cfg.modoPruebaVentanaMin) : false;

  return (
    <Card className="mt-4 p-5">
      <CabSeccion
        icono={FlaskConical}
        titulo="Modo prueba"
        subtitulo="Prueba el flujo de postulación sin contaminar los datos reales de RH."
      />

      {error && <div className="mb-4"><Aviso tono="error" onCerrar={() => setError("")}>{error}</Aviso></div>}

      {/* Toggle modo prueba */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium">Estado del modo prueba</p>
            {cfg?.modoPrueba && <Badge tone="brand" dot>Activo</Badge>}
          </div>
          <p className="mt-1 max-w-md text-[13px] leading-relaxed text-ink-2">
            Con Modo Prueba activo, las postulaciones que entren (web o WhatsApp) se marcan como prueba y nunca
            aparecen en los listados ni reportes de RH; el dedup por teléfono/correo se desactiva para poder
            repetir el flujo con el mismo número.
          </p>
        </div>
        {cargando ? (
          <Loader2 className="h-5 w-5 shrink-0 animate-spin text-ink-3" />
        ) : (
          <button
            type="button"
            onClick={alternar}
            disabled={guardando}
            aria-pressed={cfg?.modoPrueba}
            aria-label="Alternar Modo Prueba"
            className={cn("h-7 w-12 shrink-0 rounded-full border transition", cfg?.modoPrueba ? "border-brand bg-brand" : "border-border-soft bg-surface-2")}
          >
            <span className={cn("block h-5 w-5 rounded-full bg-white shadow transition-transform", cfg?.modoPrueba ? "translate-x-6" : "translate-x-1")} />
          </button>
        )}
      </div>

      {/* Ventana de nueva sesión */}
      <div className="mt-5 rounded-xl border border-border-soft p-4">
        <p className="text-sm font-semibold">Tiempo automático de nueva sesión</p>
        <p className="mt-1 max-w-lg text-[13px] leading-relaxed text-ink-2">
          Con Modo Prueba activo, si una conversación de WhatsApp de prueba lleva más de este tiempo sin actividad,
          el siguiente mensaje del mismo teléfono cierra la postulación anterior y arranca una nueva desde cero.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div>
            <label className="mb-1 block text-[12px] font-medium text-ink-2">Minutos de inactividad</label>
            <input
              type="number"
              min={5}
              max={1440}
              value={ventana}
              onChange={(e) => setVentana(e.target.value)}
              className="h-10 w-32 rounded-xl border border-border-soft bg-surface px-3.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
            />
          </div>
          <Button size="sm" onClick={guardarVentana} disabled={guardando || !ventanaCambio}>
            {guardando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Guardar
          </Button>
          {cfg && !ventanaCambio && <span className="pb-2.5 text-[12px] text-ink-3"><Check className="mr-1 inline h-3.5 w-3.5" />{cfg.modoPruebaVentanaMin} min</span>}
        </div>
      </div>

      {/* Fase 3: recordatorios automáticos de documentos */}
      <div className="mt-4 rounded-xl border border-border-soft p-4">
        <p className="text-sm font-semibold">Recordatorios automáticos de documentos</p>
        <p className="mt-1 text-[12px] leading-relaxed text-ink-3">
          Para cada expediente con fecha «recordar hasta» (Onboarding), el sistema manda por sí solo el recordatorio de
          documentos pendientes con la regla de notificación de la Cuenta: cada N días, a partir de la hora indicada
          (hora de México), y nunca después de la fecha límite.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div>
            <label className="mb-1 block text-[12px] font-medium text-ink-2">Cada (días)</label>
            <input type="number" min={1} max={30} value={recDias} onChange={(e) => setRecDias(e.target.value)}
              className="h-10 w-24 rounded-xl border border-border-soft bg-surface px-3.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
          </div>
          <div>
            <label className="mb-1 block text-[12px] font-medium text-ink-2">A partir de las (hora)</label>
            <input type="number" min={0} max={23} value={recHora} onChange={(e) => setRecHora(e.target.value)}
              className="h-10 w-24 rounded-xl border border-border-soft bg-surface px-3.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
          </div>
          <div>
            <label className="mb-1 block text-[12px] font-medium text-ink-2" title="Recordatorio automático de la Entrevista Humana a candidato y entrevistador (0 = apagado)">Entrevista: horas antes</label>
            <input type="number" min={0} max={168} value={recEntrevista} onChange={(e) => setRecEntrevista(e.target.value)}
              className="h-10 w-24 rounded-xl border border-border-soft bg-surface px-3.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
          </div>
          <Button size="sm" onClick={guardarRecordatorios} disabled={guardando || !recCambio}>
            {guardando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Guardar
          </Button>
          {cfg && !recCambio && <span className="pb-2.5 text-[12px] text-ink-3"><Check className="mr-1 inline h-3.5 w-3.5" />documentos cada {cfg.recordatorioDocumentosDias} día(s) desde las {cfg.recordatorioDocumentosHora}:00 · entrevista {cfg.recordatorioEntrevistaHoras ?? 24} h antes</span>}
        </div>
      </div>

      {/* Reiniciar prueba (Punto 8 / Fase 2) */}
      <div className="mt-4 flex items-start gap-3 rounded-xl border border-border-soft p-4">
        <RotateCw className="mt-0.5 h-5 w-5 shrink-0 text-brand" />
        <div>
          <p className="text-sm font-semibold">Reiniciar prueba (antes «Liberar número»)</p>
          <p className="mt-1 text-[13px] leading-relaxed text-ink-2">
            Desde la ficha de cualquier postulación de prueba (Candidatos → tarjeta → «Reiniciar prueba») se cierra la
            postulación actual y se crea una nueva limpia para la misma persona y vacante — el teléfono y el WhatsApp
            no se tocan, así el mismo número vuelve a empezar el flujo desde cero. Solo está disponible con Modo Prueba
            activo o sobre postulaciones marcadas como prueba.
          </p>
        </div>
      </div>

      {/* Eliminar postulaciones de prueba */}
      <div className="mt-4 flex items-start gap-3 rounded-xl border border-bad/25 p-4">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-bad" />
        <div className="flex-1">
          <p className="text-sm font-semibold">Eliminar postulaciones de prueba</p>
          <p className="mt-1 text-[13px] leading-relaxed text-ink-2">
            Borra permanentemente TODAS las personas marcadas como prueba de esta Cuenta
            {cfg && ` (${cfg.candidatosPrueba} persona(s), ${cfg.postulacionesPrueba} postulación(es))`}, junto con sus
            postulaciones, mensajes, entrevistas, expedientes, documentos y notificaciones. Los candidatos y
            postulaciones reales no se tocan. Esta acción no se puede deshacer.
          </p>
          {resumen && (
            <div className="mt-2">
              <Aviso tono="ok">
                Se borraron {resumen.candidatos} persona(s), {resumen.postulaciones} postulación(es), {resumen.mensajes} mensaje(s),{" "}
                {resumen.entrevistas} entrevista(s), {resumen.expedientes} expediente(s), {resumen.documentos} documento(s) y{" "}
                {resumen.notificaciones} notificación(es).
                {resumen.colaboradoresConservados > 0 && (
                  <> {resumen.colaboradoresConservados} colaborador(es) dados de alta desde una prueba se conservaron: revísalos en Colaboradores.</>
                )}
              </Aviso>
            </div>
          )}
          <Button
            variant="outline"
            size="sm"
            className="mt-3 border-bad/30 text-bad"
            onClick={() => setConfirmando(true)}
            disabled={!cfg || cfg.candidatosPrueba === 0}
          >
            <Trash2 className="h-4 w-4" /> Eliminar postulaciones de prueba
          </Button>
        </div>
      </div>

      {confirmando && (
        <ModalConfirmarBorrado
          cantidad={cfg?.postulacionesPrueba ?? 0}
          onCancelar={() => setConfirmando(false)}
          onConfirmar={confirmarBorrado}
          cargando={borrando}
        />
      )}
    </Card>
  );
}

function ModalConfirmarBorrado({
  cantidad, onCancelar, onConfirmar, cargando,
}: { cantidad: number; onCancelar: () => void; onConfirmar: () => void; cargando: boolean }) {
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <Card className="w-full max-w-sm p-5">
        <h3 className="font-display text-lg font-bold">¿Eliminar {cantidad} postulación(es) de prueba?</h3>
        <p className="mt-1.5 text-[13px] leading-relaxed text-ink-2">
          Esta acción es permanente: se borran las personas de prueba y todo lo que cuelga de ellas
          (postulaciones, mensajes, entrevistas, expedientes, documentos, notificaciones). Los registros
          reales no se tocan. No se puede deshacer.
        </p>
        <div className="mt-5 flex gap-3">
          <Button variant="outline" className="flex-1" onClick={onCancelar} disabled={cargando}>Cancelar</Button>
          <Button className="flex-1" onClick={onConfirmar} disabled={cargando}>
            {cargando ? "Borrando…" : "Sí, eliminar todo"}
          </Button>
        </div>
      </Card>
    </div>
  );
}
