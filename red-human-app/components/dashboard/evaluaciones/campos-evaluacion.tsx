"use client";

/* Campos compartidos de Evaluaciones unificadas (2026-09-29): evaluador (interno = usuario del sistema; externo =
   contacto reutilizable del Cliente o «+ Nuevo evaluador», que queda guardado como contacto) y cita (fecha y hora en
   la zona de la organización, modalidad, dirección / liga en campos propios). Los usan «Agregar evaluación»,
   «Modificar datos» y «Reprogramar» — una sola implementación. */

import { useEffect, useState } from "react";
import {
  MODALIDADES_CITA, fetchCliente, fetchEntrevistadores, fetchIntegracionTeams,
  type CitaEntrada, type ContactoCliente, type Entrevistador, type EvaluadorEntrada, type ModalidadCita,
} from "@/lib/api";
import { ETIQUETA_ZONA } from "@/lib/fechas";
import { cn } from "@/lib/utils";

export const inputEv = "h-11 w-full rounded-xl border border-border-soft bg-surface px-3.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20";

export function Campo({ etiqueta, children, ayuda, className }: { etiqueta: React.ReactNode; children: React.ReactNode; ayuda?: React.ReactNode; className?: string }) {
  return (
    <label className={cn("flex flex-col gap-1.5", className)}>
      <span className="text-sm font-medium text-ink-2">{etiqueta}</span>
      {children}
      {ayuda && <span className="text-[12px] leading-relaxed text-ink-3">{ayuda}</span>}
    </label>
  );
}

export function Opciones<T extends string>({ valor, opciones, onChange, columnas = 2 }: {
  valor: T; opciones: { valor: T; texto: string; ayuda?: string }[]; onChange: (v: T) => void; columnas?: 2 | 3 | 4;
}) {
  return (
    <div className={cn("grid gap-2", columnas === 2 ? "sm:grid-cols-2" : columnas === 3 ? "sm:grid-cols-3" : "grid-cols-2 sm:grid-cols-4")}>
      {opciones.map((o) => (
        <button
          key={o.valor}
          type="button"
          onClick={() => onChange(o.valor)}
          aria-pressed={valor === o.valor}
          className={cn(
            "rounded-xl border px-3 py-2.5 text-left text-sm font-medium transition",
            valor === o.valor ? "border-brand bg-brand-soft text-brand" : "border-border-soft text-ink-2 hover:border-brand/50",
          )}
        >
          {o.texto}
          {o.ayuda && <span className="mt-0.5 block text-[11px] font-normal text-ink-3">{o.ayuda}</span>}
        </button>
      ))}
    </div>
  );
}

/* ---------------- Evaluador ---------------- */

export type EstadoEvaluador = {
  tipo: "interno" | "externo";
  usuarioId: number | null;
  contacto: number | "nuevo" | "";
  nombre: string;
  correo: string;
  whatsapp: string;
};

export const evaluadorVacio: EstadoEvaluador = { tipo: "interno", usuarioId: null, contacto: "", nombre: "", correo: "", whatsapp: "" };

export function evaluadorEntrada(e: EstadoEvaluador): EvaluadorEntrada {
  if (e.tipo === "interno") return { tipo: "interno", usuarioId: e.usuarioId };
  if (typeof e.contacto === "number") return { tipo: "externo", contactoId: e.contacto };
  return { tipo: "externo", nombre: e.nombre.trim(), correo: e.correo.trim(), whatsapp: e.whatsapp.trim() };
}

/** Mensaje de validación del evaluador (o "" si está completo) — la API valida igual. */
export function validarEvaluador(e: EstadoEvaluador): string {
  if (e.tipo === "interno") return e.usuarioId ? "" : "Elige al evaluador interno.";
  if (e.contacto === "") return "Elige un contacto o «+ Nuevo evaluador».";
  if (e.contacto === "nuevo") {
    if (!e.nombre.trim()) return "Captura el nombre del evaluador.";
    if (!e.correo.trim() && !e.whatsapp.trim()) return "Captura correo o WhatsApp para enviarle la solicitud.";
  }
  return "";
}

export function nombreEvaluador(e: EstadoEvaluador, internos: Entrevistador[], contactos: ContactoCliente[] | null): string {
  if (e.tipo === "interno") return internos.find((u) => u.id === e.usuarioId)?.nombre ?? "";
  if (typeof e.contacto === "number") return contactos?.find((k) => k.id === e.contacto)?.nombreCompleto ?? "";
  return e.nombre;
}

export function useCatalogoEvaluadores(clienteId: number | null) {
  const [internos, setInternos] = useState<Entrevistador[]>([]);
  const [contactos, setContactos] = useState<ContactoCliente[] | null>(clienteId ? null : []);
  useEffect(() => {
    fetchEntrevistadores().then((d) => setInternos(d ?? []));
  }, []);
  useEffect(() => {
    if (!clienteId) return;
    let vivo = true;
    fetchCliente(clienteId).then((cl) => vivo && setContactos(cl?.listaContactos ?? []));
    return () => {
      vivo = false;
    };
  }, [clienteId]);
  return { internos, contactos };
}

export function SelectorEvaluador({ valor, onChange, internos, contactos, clienteNombre }: {
  valor: EstadoEvaluador;
  onChange: (v: EstadoEvaluador) => void;
  internos: Entrevistador[];
  contactos: ContactoCliente[] | null;
  clienteNombre?: string | null;
}) {
  // valores por defecto razonables en cuanto llegan los catálogos
  useEffect(() => {
    if (valor.tipo === "interno" && !valor.usuarioId && internos.length) onChange({ ...valor, usuarioId: internos[0].id });
  }, [valor, internos, onChange]);
  useEffect(() => {
    if (valor.tipo === "externo" && valor.contacto === "" && contactos && contactos.length === 0) onChange({ ...valor, contacto: "nuevo" });
  }, [valor, contactos, onChange]);

  const interno = internos.find((u) => u.id === valor.usuarioId) ?? null;
  const contacto = typeof valor.contacto === "number" ? contactos?.find((k) => k.id === valor.contacto) ?? null : null;
  return (
    <div className="flex flex-col gap-3">
      <Opciones
        valor={valor.tipo}
        onChange={(t) => onChange({ ...valor, tipo: t })}
        opciones={[{ valor: "interno", texto: "Interno", ayuda: "Usuario del sistema" }, { valor: "externo", texto: "Externo", ayuda: "Entra con su liga, sin cuenta" }]}
      />
      {valor.tipo === "interno" ? (
        <Campo
          etiqueta="Evaluador interno"
          ayuda={interno ? `Se notificará a ${interno.correo}${interno.telefono ? ` · WhatsApp ${interno.telefono}` : " · sin WhatsApp en su perfil"}` : undefined}
        >
          <select value={valor.usuarioId ?? ""} onChange={(e) => onChange({ ...valor, usuarioId: Number(e.target.value) || null })} className={inputEv}>
            {internos.length === 0 && <option value="">Sin usuarios activos</option>}
            {internos.map((u) => <option key={u.id} value={u.id}>{u.nombre}</option>)}
          </select>
        </Campo>
      ) : (
        <>
          <Campo
            etiqueta="Evaluador externo"
            ayuda={contacto ? `Se notificará a ${contacto.correo || "(sin correo)"}${contacto.telefono ? ` · WhatsApp ${contacto.telefono}` : ""}` : contactos && contactos.length === 0 ? "Captura al evaluador; quedará guardado como contacto del Cliente para reutilizarlo." : undefined}
          >
            <select
              value={valor.contacto}
              onChange={(e) => onChange({ ...valor, contacto: e.target.value === "nuevo" ? "nuevo" : e.target.value === "" ? "" : Number(e.target.value) })}
              className={inputEv}
            >
              {contactos === null ? (
                <option value="">Cargando contactos…</option>
              ) : (
                <>
                  {contactos.length > 0 && <option value="">Elige un contacto{clienteNombre ? ` de ${clienteNombre}` : ""}…</option>}
                  {contactos.map((k) => <option key={k.id} value={k.id}>{k.nombreCompleto}{k.puesto ? ` — ${k.puesto}` : ""}</option>)}
                  <option value="nuevo">+ Nuevo evaluador</option>
                </>
              )}
            </select>
          </Campo>
          {valor.contacto === "nuevo" && (
            <div className="grid gap-3 sm:grid-cols-3">
              <Campo etiqueta={<>Nombre <span className="text-bad">*</span></>} className="sm:col-span-3">
                <input value={valor.nombre} onChange={(e) => onChange({ ...valor, nombre: e.target.value })} placeholder="Nombre completo" className={inputEv} />
              </Campo>
              <Campo etiqueta="Correo" className="sm:col-span-2">
                <input type="email" value={valor.correo} onChange={(e) => onChange({ ...valor, correo: e.target.value })} placeholder="correo@empresa.com" className={inputEv} />
              </Campo>
              <Campo etiqueta="WhatsApp">
                <input value={valor.whatsapp} onChange={(e) => onChange({ ...valor, whatsapp: e.target.value })} placeholder="10 dígitos" className={inputEv} />
              </Campo>
              <p className="text-[12px] text-ink-3 sm:col-span-3">Al menos uno: correo o WhatsApp.</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ---------------- Cita ---------------- */

export type EstadoCita = { fecha: string; hora: string; modalidad: ModalidadCita; direccion: string; liga: string; telefono: string; otraLiga: boolean };
export const citaVacia: EstadoCita = { fecha: "", hora: "", modalidad: "Videollamada", direccion: "", liga: "", telefono: "", otraLiga: false };

export function useTeamsConectado() {
  const [conectado, setConectado] = useState(false);
  useEffect(() => {
    fetchIntegracionTeams().then((t) => setConectado(Boolean(t?.disponible && t?.conectado)));
  }, []);
  return conectado;
}

export function validarCita(c: EstadoCita, teams: boolean): string {
  if (!c.fecha || !c.hora) return "Completa fecha y hora de la cita.";
  if (c.modalidad === "Presencial" && !c.direccion.trim()) return "Falta la dirección de la cita.";
  if (c.modalidad === "Videollamada" && !(teams && !c.otraLiga) && !c.liga.trim()) return "Falta la liga de la videollamada.";
  return "";
}

export function citaEntrada(c: EstadoCita, teams: boolean): CitaEntrada {
  const porTeams = c.modalidad === "Videollamada" && teams && !c.otraLiga;
  return {
    fecha: c.fecha, hora: c.hora, modalidad: c.modalidad, direccion: c.direccion.trim(),
    ligaVideollamada: porTeams ? "" : c.liga.trim(), telefono: c.telefono.trim(), usarTeams: porTeams,
  };
}

export function CamposCita({ valor, onChange, teams }: { valor: EstadoCita; onChange: (v: EstadoCita) => void; teams: boolean }) {
  const porTeams = valor.modalidad === "Videollamada" && teams && !valor.otraLiga;
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3">
        <Campo etiqueta={<>Fecha <span className="text-bad">*</span></>}>
          <input type="date" value={valor.fecha} onChange={(e) => onChange({ ...valor, fecha: e.target.value })} className={inputEv} />
        </Campo>
        <Campo etiqueta={<>Hora <span className="text-bad">*</span> <span className="font-normal text-ink-3">({ETIQUETA_ZONA})</span></>}>
          <input type="time" value={valor.hora} onChange={(e) => onChange({ ...valor, hora: e.target.value })} className={inputEv} />
        </Campo>
      </div>
      {/* grupo de botones: fieldset (un <label> los fusionaría con su etiqueta) */}
      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1.5 text-sm font-medium text-ink-2">Modalidad <span className="text-bad">*</span></legend>
        <Opciones valor={valor.modalidad} onChange={(m) => onChange({ ...valor, modalidad: m })} columnas={3} opciones={MODALIDADES_CITA.map((m) => ({ valor: m, texto: m }))} />
      </fieldset>
      {valor.modalidad === "Presencial" && (
        <Campo etiqueta={<>Dirección <span className="text-bad">*</span></>}>
          <input value={valor.direccion} onChange={(e) => onChange({ ...valor, direccion: e.target.value })} placeholder="Calle, número, colonia, ciudad" className={inputEv} />
        </Campo>
      )}
      {valor.modalidad === "Videollamada" && (porTeams ? (
        <p className="rounded-xl border border-brand/25 bg-brand-soft/40 px-3.5 py-2.5 text-[13px] leading-relaxed text-ink-2">
          <b className="text-ink">Reunión de Microsoft Teams automática.</b> Al guardar se crea la reunión y la invitación de calendario.{" "}
          <button type="button" className="font-semibold text-brand hover:underline" onClick={() => onChange({ ...valor, otraLiga: true })}>Usar otra liga</button>
        </p>
      ) : (
        <Campo
          etiqueta={<>Liga de videollamada <span className="text-bad">*</span></>}
          ayuda={teams ? <button type="button" className="font-semibold text-brand hover:underline" onClick={() => onChange({ ...valor, otraLiga: false, liga: "" })}>Usar Teams automático</button> : undefined}
        >
          <input value={valor.liga} onChange={(e) => onChange({ ...valor, liga: e.target.value })} placeholder="https://…" className={inputEv} />
        </Campo>
      ))}
      {valor.modalidad === "Teléfono" && (
        <Campo etiqueta="Teléfono de contacto (opcional)" ayuda="Si lo dejas vacío se usa el teléfono del candidato.">
          <input value={valor.telefono} onChange={(e) => onChange({ ...valor, telefono: e.target.value })} placeholder="10 dígitos" className={inputEv} />
        </Campo>
      )}
    </div>
  );
}
