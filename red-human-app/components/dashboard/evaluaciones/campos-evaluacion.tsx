"use client";

/* Campos compartidos de Evaluaciones unificadas (2026-09-29): evaluador (interno = usuario del sistema; externo =
   contacto reutilizable del Cliente o «+ Nuevo evaluador», que queda guardado como contacto) y cita (fecha y hora en
   la zona de la organización, modalidad, dirección / liga en campos propios). Los usan «Agregar evaluación»,
   «Modificar datos» y «Reprogramar» — una sola implementación.
   2026-10-10 (citas, ajustes tras la prueba): siempre visibles Fecha, Hora, Modalidad y Dirección; «Hasta», «Instrucciones»
   y «Adjuntos» viven en «▸ Más detalles» (con resumen si ya traen algo). Evaluador: UN selector con los contactos del
   Cliente primero («Nombre · Teléfono»), luego los usuarios internos y al final «+ Nuevo evaluador» (solo entonces se
   piden nombre/correo/WhatsApp; los números repetidos no se bloquean). */

import { useEffect, useState } from "react";
import {
  MAX_ADJUNTOS_CITA, MAX_MB_ADJUNTO_CITA, MODALIDADES_CITA, fetchCliente, fetchEntrevistadores, fetchIntegracionTeams, ligaMapa,
  type AdjuntoCita, type CitaEntrada, type ContactoCliente, type Entrevistador, type EvaluadorEntrada, type ModalidadCita, type UltimaCita,
} from "@/lib/api";
import { ETIQUETA_ZONA, desdeLocal } from "@/lib/fechas";
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

  const interno = valor.tipo === "interno" ? internos.find((u) => u.id === valor.usuarioId) ?? null : null;
  const contacto = valor.tipo === "externo" && typeof valor.contacto === "number" ? contactos?.find((k) => k.id === valor.contacto) ?? null : null;
  const actual = valor.tipo === "interno" ? (valor.usuarioId ? `u:${valor.usuarioId}` : "")
    : valor.contacto === "nuevo" ? "nuevo" : typeof valor.contacto === "number" ? `c:${valor.contacto}` : "";
  const etiqueta = (nombre: string, tel?: string | null) => (tel ? `${nombre} · ${tel}` : nombre);
  function elegir(v: string) {
    if (v === "nuevo") return onChange({ ...valor, tipo: "externo", contacto: "nuevo", usuarioId: null });
    if (v.startsWith("c:")) return onChange({ ...valor, tipo: "externo", contacto: Number(v.slice(2)), usuarioId: null });
    if (v.startsWith("u:")) return onChange({ ...valor, tipo: "interno", usuarioId: Number(v.slice(2)), contacto: "" });
    onChange({ ...valor, tipo: "externo", contacto: "", usuarioId: null });
  }
  const ayuda = interno ? `Se notificará a ${interno.correo}${interno.telefono ? ` · WhatsApp ${interno.telefono}` : " · sin WhatsApp en su perfil"}`
    : contacto ? `Se notificará a ${contacto.correo || "(sin correo)"}${contacto.telefono ? ` · WhatsApp ${contacto.telefono}` : ""}`
      : valor.contacto === "nuevo" ? "Quedará guardado como contacto del Cliente para reutilizarlo." : undefined;
  return (
    <div className="flex flex-col gap-3">
      <Campo etiqueta="Evaluador" ayuda={ayuda}>
        <select value={actual} onChange={(e) => elegir(e.target.value)} className={inputEv}>
          <option value="">{contactos === null ? "Cargando evaluadores…" : "Elige al evaluador…"}</option>
          {contactos && contactos.length > 0 && (
            <optgroup label={clienteNombre ? `Contactos de ${clienteNombre}` : "Contactos del Cliente"}>
              {contactos.map((k) => <option key={`c${k.id}`} value={`c:${k.id}`}>{etiqueta(k.nombreCompleto, k.telefono)}</option>)}
            </optgroup>
          )}
          {internos.length > 0 && (
            <optgroup label="Usuarios internos">
              {internos.map((u) => <option key={`u${u.id}`} value={`u:${u.id}`}>{etiqueta(u.nombre, u.telefono)}</option>)}
            </optgroup>
          )}
          <option value="nuevo">+ Nuevo evaluador</option>
        </select>
      </Campo>
      {valor.tipo === "externo" && (
        <>
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

/** `previos` = adjuntos de la última cita de la vacante que se reutilizan (el servidor los copia). */
export type EstadoCita = {
  fecha: string; hora: string; modalidad: ModalidadCita; direccion: string; liga: string; telefono: string; otraLiga: boolean; hasta: string;
  instrucciones: string; previos: { evaluacion: string; adjuntos: AdjuntoCita[] } | null;
};
export const citaVacia: EstadoCita = {
  fecha: "", hora: "", modalidad: "Videollamada", direccion: "", liga: "", telefono: "", otraLiga: false, hasta: "", instrucciones: "", previos: null,
};

/** Precarga con la última cita de la vacante: evaluador, modalidad, dirección, indicaciones y adjuntos — NUNCA fecha ni hora. */
export function citaDesdeUltima(u: UltimaCita, base: EstadoCita = citaVacia): EstadoCita {
  return {
    ...base, fecha: "", hora: "", hasta: "",
    modalidad: (u.modalidad || base.modalidad) as ModalidadCita, direccion: u.direccion || base.direccion,
    instrucciones: u.instrucciones || base.instrucciones, previos: u.adjuntos.length ? { evaluacion: u.evaluacion, adjuntos: u.adjuntos } : null,
  };
}
export function evaluadorDesdeUltima(u: UltimaCita): EstadoEvaluador | null {
  const e = u.evaluador;
  if (!e) return null;
  if (e.tipo === "interno") return e.usuario_id ? { ...evaluadorVacio, tipo: "interno", usuarioId: e.usuario_id } : null;
  return { ...evaluadorVacio, tipo: "externo", contacto: e.contacto_id ?? "nuevo", nombre: e.nombre, correo: e.correo, whatsapp: e.whatsapp };
}

export function useTeamsConectado() {
  const [conectado, setConectado] = useState(false);
  useEffect(() => {
    fetchIntegracionTeams().then((t) => setConectado(Boolean(t?.disponible && t?.conectado)));
  }, []);
  return conectado;
}

/** `actual` = la fecha-hora que ya tenía la cita (editar otros datos de una cita vencida no se bloquea). */
export function validarCita(c: EstadoCita, teams: boolean, actual?: string | null): string {
  if (!c.fecha || !c.hora) return "Completa fecha y hora de la cita.";
  const cuando = desdeLocal(c.fecha, c.hora);
  const igual = actual && cuando && Math.abs(new Date(actual).getTime() - cuando.getTime()) < 60_000;
  if (cuando && cuando.getTime() <= Date.now() && !igual) return "La fecha y hora de la cita ya pasaron. Elige una fecha y hora futuras.";
  if (c.hasta && c.hasta <= c.hora) return "La hora «Hasta» debe ser posterior a la de inicio.";
  if (c.modalidad === "Presencial" && !c.direccion.trim()) return "Falta la dirección de la cita.";
  if (c.modalidad === "Videollamada" && !(teams && !c.otraLiga) && !c.liga.trim()) return "Falta la liga de la videollamada.";
  return "";
}

export function citaEntrada(c: EstadoCita, teams: boolean): CitaEntrada {
  const porTeams = c.modalidad === "Videollamada" && teams && !c.otraLiga;
  return {
    fecha: c.fecha, hora: c.hora, modalidad: c.modalidad, direccion: c.direccion.trim(),
    ligaVideollamada: porTeams ? "" : c.liga.trim(), telefono: c.telefono.trim(), usarTeams: porTeams, hasta: c.hasta || "",
    instrucciones: c.instrucciones.trim(),
    adjuntosPrevios: c.previos && c.previos.adjuntos.length ? { evaluacion: c.previos.evaluacion, ids: c.previos.adjuntos.map((a) => a.id) } : null,
  };
}

const fotoOPdf = (n: { mime?: string; type?: string }) => ((n.mime ?? n.type ?? "").startsWith("image/") ? "foto" : "PDF");
function contarAdjuntos(lista: { mime?: string; type?: string }[]): string {
  const fotos = lista.filter((x) => fotoOPdf(x) === "foto").length;
  const pdfs = lista.length - fotos;
  return [fotos && `${fotos} foto${fotos > 1 ? "s" : ""}`, pdfs && `${pdfs} PDF`].filter(Boolean).join(" · ");
}

/** Resumen del desplegable: «Hasta 11:00 · Indicaciones: Pregunta en caseta… · 1 foto». */
export function resumenMasDetalles(c: EstadoCita, archivos: File[] = [], conInstrucciones = true): string {
  const corto = (t: string) => (t.length > 28 ? `${t.slice(0, 28).trim()}…` : t);
  const adj = contarAdjuntos([...(c.previos?.adjuntos ?? []), ...archivos]);
  return [c.hasta && `Hasta ${c.hasta}`, conInstrucciones && c.instrucciones.trim() && `Indicaciones: ${corto(c.instrucciones.trim())}`, adj]
    .filter(Boolean).join(" · ");
}

/** Formulario de la cita. `adjuntos` (opcional) agrega los archivos para el candidato dentro de «Más detalles»;
 * `conInstrucciones=false` cuando la pantalla ya tiene su propio campo de instrucciones. `contextoMapa` = ubicación de
 * la vacante para la liga de mapa (el campo de dirección no cambia). */
export function CamposCita({ valor, onChange, teams, adjuntos, conInstrucciones = true, contextoMapa = "" }: {
  valor: EstadoCita; onChange: (v: EstadoCita) => void; teams: boolean;
  adjuntos?: { valor: File[]; onChange: (v: File[]) => void }; conInstrucciones?: boolean; contextoMapa?: string;
}) {
  const porTeams = valor.modalidad === "Videollamada" && teams && !valor.otraLiga;
  const [abierto, setAbierto] = useState(false);
  const resumen = resumenMasDetalles(valor, adjuntos?.valor ?? [], conInstrucciones);
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3">
        <Campo etiqueta={<>Fecha <span className="text-bad">*</span></>}>
          <input type="date" value={valor.fecha} onChange={(e) => onChange({ ...valor, fecha: e.target.value })} className={inputEv} />
        </Campo>
        <Campo etiqueta={<>Hora <span className="text-bad">*</span></>}>
          <input type="time" value={valor.hora} onChange={(e) => onChange({ ...valor, hora: e.target.value })} className={inputEv} />
        </Campo>
      </div>
      <p className="-mt-1.5 text-[11px] text-ink-3">Horas en {ETIQUETA_ZONA}.</p>
      {/* grupo de botones: fieldset (un <label> los fusionaría con su etiqueta) */}
      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1.5 text-sm font-medium text-ink-2">Modalidad <span className="text-bad">*</span></legend>
        <Opciones valor={valor.modalidad} onChange={(m) => onChange({ ...valor, modalidad: m })} columnas={3} opciones={MODALIDADES_CITA.map((m) => ({ valor: m, texto: m }))} />
      </fieldset>
      {valor.modalidad === "Presencial" && (
        <Campo
          etiqueta={<>Dirección <span className="text-bad">*</span></>}
          ayuda={valor.direccion.trim() ? (
            <>Liga de mapa (se genera sola{contextoMapa ? ` con ${contextoMapa}` : ""}): <a className="break-all text-brand hover:underline" href={ligaMapa(valor.direccion, contextoMapa)} target="_blank" rel="noreferrer">ver en el mapa</a></>
          ) : "La liga de mapa se genera sola con la dirección."}
        >
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
      <div className="rounded-xl border border-border-soft">
        <button type="button" onClick={() => setAbierto((x) => !x)} aria-expanded={abierto}
          className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-[13px] font-semibold text-ink-2 hover:text-ink">
          <span className={cn("inline-block transition-transform", abierto && "rotate-90")}>▸</span>
          <span className="truncate">{resumen || "Más detalles"}</span>
          {!resumen && <span className="font-normal text-ink-3">(hasta, {conInstrucciones ? "instrucciones, " : ""}adjuntos)</span>}
        </button>
        {abierto && (
          <div className="flex flex-col gap-3 border-t border-border-soft p-3">
            <Campo etiqueta="Hasta (opcional)" ayuda="Con «Hasta» el candidato recibe el rango (p. ej. de 9:00 a 11:00).">
              <input type="time" value={valor.hasta} onChange={(e) => onChange({ ...valor, hasta: e.target.value })} className={cn(inputEv, "sm:w-40")} />
            </Campo>
            {conInstrucciones && (
              <Campo etiqueta="Instrucciones (opcional)" ayuda="Le llega al candidato como «📝 Indicaciones: …».">
                <textarea value={valor.instrucciones} onChange={(e) => onChange({ ...valor, instrucciones: e.target.value })} rows={2}
                  placeholder="Pregunta en caseta por…, trae identificación oficial…"
                  className="rounded-xl border border-border-soft bg-surface px-3.5 py-2.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
              </Campo>
            )}
            {adjuntos && (
              <>
                {valor.previos && valor.previos.adjuntos.length > 0 && (
                  <div className="flex flex-col gap-1">
                    <span className="text-[12px] text-ink-3">Adjuntos de la última cita de esta vacante</span>
                    {valor.previos.adjuntos.map((a) => (
                      <div key={a.id} className="flex items-center justify-between gap-2 rounded-lg bg-surface-2/60 px-2.5 py-1 text-[12px] text-ink-2">
                        <span className="truncate">{a.nombre} · {fotoOPdf(a)}</span>
                        <button type="button" className="font-semibold text-bad hover:underline" onClick={() => onChange({
                          ...valor, previos: { evaluacion: valor.previos!.evaluacion, adjuntos: valor.previos!.adjuntos.filter((x) => x.id !== a.id) },
                        })}>Quitar</button>
                      </div>
                    ))}
                  </div>
                )}
                <AdjuntosCita valor={adjuntos.valor} onChange={adjuntos.onChange} yaTiene={valor.previos?.adjuntos.length ?? 0} />
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------- Adjuntos de la cita (2026-10-10) ---------------- */

const TIPOS_ADJUNTO_CITA = ["application/pdf", "image/png", "image/jpeg", "image/webp"];

/** Valida en el navegador lo mismo que la API: máx. 5, solo imágenes o PDF, ≤ 10 MB cada uno. */
export function validarAdjuntosCita(archivos: File[], yaTiene = 0): string {
  if (archivos.length + yaTiene > MAX_ADJUNTOS_CITA) return `Una cita admite hasta ${MAX_ADJUNTOS_CITA} adjuntos.`;
  const malo = archivos.find((f) => !TIPOS_ADJUNTO_CITA.includes(f.type));
  if (malo) return `«${malo.name}»: solo imágenes (PNG, JPG, WEBP) o PDF.`;
  const grande = archivos.find((f) => f.size > MAX_MB_ADJUNTO_CITA * 1024 * 1024);
  if (grande) return `«${grande.name}» pesa más de ${MAX_MB_ADJUNTO_CITA} MB.`;
  return "";
}

export function AdjuntosCita({ valor, onChange, yaTiene = 0 }: { valor: File[]; onChange: (v: File[]) => void; yaTiene?: number }) {
  const error = validarAdjuntosCita(valor, yaTiene);
  return (
    <Campo
      etiqueta="Adjuntos para el candidato (opcional)"
      ayuda={error ? <span className="font-semibold text-bad">{error}</span> : `Hasta ${MAX_ADJUNTOS_CITA} imágenes o PDF de máx. ${MAX_MB_ADJUNTO_CITA} MB (croquis, indicaciones). Se envían tal cual con el aviso.`}
    >
      <input
        type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/png,image/jpeg,image/webp"
        onChange={(e) => onChange([...valor, ...Array.from(e.target.files ?? [])])}
        className="text-[13px]"
      />
      {valor.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-1">
          {valor.map((f, i) => (
            <li key={`${f.name}-${i}`} className="flex items-center justify-between gap-2 rounded-lg bg-surface-2/60 px-2.5 py-1 text-[12px] text-ink-2">
              <span className="truncate">{f.name} · {(f.size / 1024 / 1024).toFixed(1)} MB</span>
              <button type="button" className="font-semibold text-bad hover:underline" onClick={() => onChange(valor.filter((_, j) => j !== i))}>Quitar</button>
            </li>
          ))}
        </ul>
      )}
    </Campo>
  );
}
