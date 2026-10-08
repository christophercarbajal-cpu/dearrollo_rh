"use client";

/* Flujo de psicometría de UN solo modal (2026-10-07, red-human-psicometria.md). AISLADO: solo se pinta en las Cuentas
   con `usePsicometriaSimple()` (hoy «demo-grupak»); el resto de las Cuentas conserva el flujo anterior intacto
   (`ModalActividad` + `VistaAsignarPsicometria`). Principio: el usuario ve el mínimo de pasos.
     - «Agregar actividad a este candidato»: al elegir Psicométrica el MISMO modal muestra la prueba o batería y se
       agrega + asigna + envía con «Enviar ahora», o se agrega «Sin enviar».
     - Catálogo vacío: sin buscador; el administrador ve «Configurar pruebas», los demás el aviso para pedirlo.
     - Solo tres estados: Sin enviar · Enviada · Completada (bloque `psicometria` que deriva la API) y el aviso
       «Sin respuesta en N días». UNA acción por fila: Enviar prueba / Reenviar / Ver resultado.
     - Prueba externa: nombre + PDF de resultados → queda Completada.
     - Correo del candidato EN el mismo modal (precargado de la ficha, editable): «Enviar ahora» exige un correo válido
       y la API lo guarda en la persona ANTES de llamar al proveedor (`asignarPsicometria({correo})`).
   Todo REUTILIZA las APIs que ya existen (actividad ad hoc, «Asignar y enviar», evaluación de registro directo + su
   resultado, envío de ligas): la API no cambia de comportamiento por Cuenta. */

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Copy, FileUp, Loader2, Mail, Send } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import { ModalMarco, inputRH } from "@/components/dashboard/modulos-rh";
import { SelectorPruebas } from "@/components/dashboard/evaluaciones/asignar-psicometria";
import { useEsAdmin } from "@/components/sesion";
import {
  agregarActividadProceso, asignarPsicometria, crearEvaluacion, enviarLigaEvaluacion, esCorreoValido, fetchOpcionesProceso, fetchPruebasPsicometricas,
  nombreEtapa, registrarResultadoEvaluacion, urlArchivo, type Evaluacion, type OpcionesProceso, type PruebaPsicometrica,
} from "@/lib/api";
import type { Candidato, EtapaCandidato, PasoSeguimiento, PsicometriaPaso, SeguimientoProceso as Seg } from "@/lib/data";
import { cn } from "@/lib/utils";

export const TONO_PSICOMETRIA: Record<PsicometriaPaso["status"], "neutral" | "warn" | "good" | "bad"> = {
  sin_enviar: "neutral", enviada: "warn", error_envio: "bad", completada: "good",
};

/** Las mismas tres etiquetas para la tarjeta de la evaluación (pestaña «Evaluación integral»). */
export function estadoPsicometriaSimple(e: Evaluacion): { texto: string; tono: "neutral" | "warn" | "good" } | null {
  if (e.tipo !== "psicometrica" || e.estado === "cancelada") return null;
  if (e.estado === "con_resultado") return { texto: "Completada", tono: "good" };
  const enviada = e.forma === "integrada"
    ? Boolean(e.claveProveedor) || (e.pasoIntegrada ?? "asignada") !== "asignada" || e.estado !== "pendiente"
    : e.forma !== "registro_directo";
  // 2026-10-08: «Enviada» ya no es un estado; se dice a quién se espera
  return enviada ? { texto: "Esperando candidato", tono: "warn" } : { texto: "Sin enviar", tono: "neutral" };
}

/** Catálogo integrado activo (pruebas y baterías). null mientras carga. */
function useCatalogo() {
  const [catalogo, setCatalogo] = useState<PruebaPsicometrica[] | null>(null);
  useEffect(() => {
    fetchPruebasPsicometricas().then((l) => setCatalogo((l ?? []).filter((x) => x.modo === "integrada" && x.activa)));
  }, []);
  return catalogo;
}

/** «Prueba o batería»: buscador del catálogo; con catálogo vacío, el aviso (y «Configurar pruebas» al administrador). */
function BloquePrueba({ catalogo, seleccion, onChange, deshabilitado }: {
  catalogo: PruebaPsicometrica[] | null;
  seleccion: number[];
  onChange: (ids: number[]) => void;
  deshabilitado?: boolean;
}) {
  const esAdmin = useEsAdmin();
  if (catalogo === null) return <Loader2 className="h-5 w-5 animate-spin text-ink-3" />;
  if (catalogo.length === 0) {
    return (
      <div className="rounded-xl border border-border-soft bg-surface-2/50 px-3.5 py-3">
        <p className="text-sm font-semibold text-ink">Aún no hay pruebas psicométricas configuradas.</p>
        {esAdmin ? (
          <Button size="sm" variant="outline" className="mt-2" href="/dashboard/configuracion#pruebas-psicometricas">Configurar pruebas</Button>
        ) : (
          <p className="mt-1 text-[12px] text-ink-2">Pide a tu administrador que agregue pruebas.</p>
        )}
      </div>
    );
  }
  return <SelectorPruebas catalogo={catalogo} seleccion={seleccion} onChange={onChange} deshabilitado={deshabilitado} />;
}

/** Botón primario que, deshabilitado, se ve GRIS (no rosa) y explica qué falta. */
function BotonPrincipal({ puede, ocupado, pista, onClick, children }: {
  puede: boolean; ocupado: boolean; pista: string; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        size="sm"
        onClick={onClick}
        disabled={!puede || ocupado}
        className={cn(!puede && "border border-border-soft bg-surface-2 text-ink-3 shadow-none disabled:opacity-100")}
      >
        {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} {children}
      </Button>
      {!puede && <span className="text-[11px] text-ink-3">{pista}</span>}
    </div>
  );
}

/** «Correo del candidato»: precargado de la ficha y editable ahí mismo (sin otra pantalla). */
function CampoCorreo({ valor, onChange, original, deshabilitado }: {
  valor: string; onChange: (v: string) => void; original: string; deshabilitado?: boolean;
}) {
  const invalido = valor.trim() !== "" && !esCorreoValido(valor);
  const cambia = valor.trim().toLowerCase() !== original.trim().toLowerCase();
  return (
    <label className="flex flex-col gap-1 text-xs text-ink-2">
      <span className="flex items-center gap-1.5 font-medium"><Mail className="h-3.5 w-3.5" /> Correo del candidato</span>
      <input
        type="email"
        inputMode="email"
        autoComplete="off"
        className={cn(inputRH, invalido && "border-bad focus:border-bad focus:ring-bad/20")}
        value={valor}
        onChange={(e) => onChange(e.target.value)}
        placeholder="correo@ejemplo.com"
        disabled={deshabilitado}
        aria-invalid={invalido}
      />
      {invalido ? (
        <span className="text-[11px] text-bad">Revisa el formato del correo.</span>
      ) : !original.trim() ? (
        <span className="text-[11px] text-ink-3">El candidato no tiene correo: escríbelo aquí; se guardará en su ficha al enviar.</span>
      ) : cambia ? (
        <span className="text-[11px] text-ink-3">Al enviar se actualizará el correo de su ficha.</span>
      ) : null}
    </label>
  );
}

/** Pista del botón «Enviar ahora»: lo primero que falta. */
function pistaEnvio(sinCatalogo: boolean, seleccion: number[], correo: string): string {
  if (sinCatalogo || !seleccion.length) return "Elige una prueba para enviarla";
  if (!correo.trim()) return "Escribe el correo del candidato";
  return "El correo no tiene un formato válido";
}

/** Prueba externa: nombre + PDF de resultados. */
function CamposExterna({ nombre, setNombre, archivo, setArchivo }: {
  nombre: string; setNombre: (v: string) => void; archivo: File | null; setArchivo: (f: File | null) => void;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border-soft p-3.5">
      <label className="flex flex-col gap-1 text-xs text-ink-2">
        Nombre de la prueba
        <input className={inputRH} value={nombre} onChange={(e) => setNombre(e.target.value)} placeholder="Ej. Cleaver (aplicada por otro proveedor)" />
      </label>
      <label className="flex flex-col gap-1 text-xs text-ink-2">
        PDF de resultados
        <span className="flex items-center gap-2">
          <input type="file" accept="application/pdf,.pdf" className="text-sm" onChange={(e) => setArchivo(e.target.files?.[0] ?? null)} />
          {archivo && <FileUp className="h-4 w-4 text-good" />}
        </span>
      </label>
      <p className="text-[11px] text-ink-3">Al guardar queda «Completada» y la fila muestra «Ver resultado».</p>
    </div>
  );
}

/** Registra una prueba externa sobre el paso: evaluación de registro directo + su PDF → Completada. */
async function guardarExterna(c: Candidato, pasoId: string, nombre: string, archivo: File): Promise<{ ok: true; candidato?: Candidato } | { ok: false; error: string }> {
  const r = await crearEvaluacion(c.id, { tipo: "psicometrica", nombre: nombre.trim(), forma: "registro_directo", pasoId });
  if (!r.ok) return { ok: false, error: r.error };
  const ev = r.data.evaluacion;
  const res = await registrarResultadoEvaluacion(ev.codigo, {
    conclusion: "", comentarios: "", realizadaPor: "", archivos: [archivo], version: ev.resultadoVersion, modo: "registrar",
  });
  if (!res.ok) return { ok: false, error: `La prueba se registró, pero el PDF no se guardó: ${res.error}` };
  return { ok: true, candidato: res.data.candidato ?? r.data.candidato };
}

function textoEnvio(r: { simulado?: boolean; aviso?: string; envioCandidato?: { enviado: boolean; canales?: string[]; detalle?: string } }, nombre: string) {
  if (r.simulado) return `«${nombre}» quedó asignada (modo simulado: ${r.aviso ?? "sin conexión con el proveedor"}).`;
  const env = r.envioCandidato;
  return env?.enviado
    ? `«${nombre}» enviada al candidato por ${(env.canales ?? []).join(" y ")}.`
    : `«${nombre}» se generó, pero no se pudo enviar al candidato${env?.detalle ? ` (${env.detalle})` : ""}. Queda en «Error de envío»: usa «Reintentar envío» o «Copiar liga».`;
}

/* ================================================================ Modal «Agregar actividad a este candidato» */

export function ModalActividadSimple({ c, etapaActual, onClose, onAgregada }: {
  c: Candidato;
  etapaActual: EtapaCandidato;
  onClose: () => void;
  onAgregada: (r: { proceso: Seg; candidato: Candidato; paso: { nombre: string } }, aviso?: { tono: "ok" | "warn" | "error"; texto: string }) => void;
}) {
  const [opciones, setOpciones] = useState<OpcionesProceso | null>(null);
  const catalogo = useCatalogo();
  const [tipo, setTipo] = useState("");
  const [nombre, setNombre] = useState("");
  const etapa = etapaActual; // 2026-10-08: la actividad adicional va en la etapa ACTUAL; nunca regresa al candidato
  const [obligatorio, setObligatorio] = useState(false);
  const [seleccion, setSeleccion] = useState<number[]>([]);
  const [externa, setExterna] = useState(false);
  const [nombreExterna, setNombreExterna] = useState("");
  const [pdf, setPdf] = useState<File | null>(null);
  const [correo, setCorreo] = useState(c.correo ?? "");
  const [error, setError] = useState("");
  const [ocupado, setOcupado] = useState("");
  const candado = useRef(false);
  useEffect(() => {
    fetchOpcionesProceso().then((o) => setOpciones(o ?? null));
  }, []);
  const tipos = (opciones?.tiposPaso ?? []).filter((t) => !["solicitud_web", "prefiltro_web", "prefiltro_whatsapp", "alta"].includes(t.valor));
  const elegido = tipos.find((t) => t.valor === tipo);
  const psico = tipo === "psicometrica";

  function elegir(valor: string) {
    const t = tipos.find((x) => x.valor === valor);
    setTipo(valor);
    setExterna(false);
    setError("");
  }

  async function agregar(nombreFinal?: string) {
    return agregarActividadProceso(c.id, { tipo, nombre: (nombreFinal ?? nombre).trim() || undefined, etapa, obligatorio });
  }

  async function ejecutar(modo: "sin_enviar" | "enviar" | "externa" | "simple") {
    if (candado.current) return;
    if (!tipo) return setError("Elige la actividad del catálogo.");
    candado.current = true;
    setOcupado(modo);
    setError("");
    try {
      const nombrePrueba = modo === "externa" ? nombreExterna : "";
      const r = await agregar(modo === "externa" && !nombre.trim() ? nombrePrueba : undefined);
      if (!r.ok) return setError(r.error);
      const pasoId = r.data.paso.id;
      if (modo === "enviar") {
        const s = await asignarPsicometria(c.id, { pruebaIds: seleccion, pasoId, correo });
        if (!s.ok) {
          return onAgregada(r.data, { tono: "warn", texto: `La actividad quedó en la ruta como «Sin enviar», pero la prueba no se envió: ${s.error}` });
        }
        return onAgregada({ ...r.data, candidato: s.data.candidato ?? r.data.candidato }, { tono: s.data.simulado ? "warn" : "ok", texto: textoEnvio(s.data, s.data.evaluacion.nombre) });
      }
      if (modo === "externa" && pdf) {
        const e = await guardarExterna(c, pasoId, nombrePrueba, pdf);
        if (!e.ok) return onAgregada(r.data, { tono: "warn", texto: `La actividad se agregó, pero ${e.error}` });
        return onAgregada({ ...r.data, candidato: e.candidato ?? r.data.candidato }, { tono: "ok", texto: `«${nombrePrueba.trim()}» quedó Completada con su PDF de resultados.` });
      }
      onAgregada(r.data, modo === "sin_enviar" ? { tono: "ok", texto: `«${r.data.paso.nombre}» se agregó como «Sin enviar». Envíala con «Enviar prueba» en su fila.` } : undefined);
    } finally {
      candado.current = false;
      setOcupado("");
    }
  }

  const sinCatalogo = catalogo !== null && catalogo.length === 0;
  return (
    <ModalMarco
      titulo="Agregar actividad a este candidato"
      subtitulo="Solo para esta postulación. No mueve al candidato de columna; si es obligatoria, sí impide avanzarlo hasta completarla."
      onClose={onClose}
    >
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-xs text-ink-2">
          Actividad del catálogo
          <select className={cn(inputRH, "h-10")} value={tipo} onChange={(e) => elegir(e.target.value)}>
            <option value="">Elegir…</option>
            {tipos.map((t) => <option key={t.valor} value={t.valor}>{t.texto}</option>)}
          </select>
        </label>
        {elegido && (
          <>
            <label className="flex flex-col gap-1 text-xs text-ink-2">
              Nombre (opcional)
              <input className={inputRH} value={nombre} onChange={(e) => setNombre(e.target.value)} placeholder={elegido.texto} />
            </label>
            <p className="text-[12px] text-ink-3">Se agrega en la etapa actual: <b className="text-ink-2">{nombreEtapa(etapa)}</b>.</p>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="h-4 w-4 rounded accent-brand" checked={obligatorio} onChange={(e) => setObligatorio(e.target.checked)} />
              Obligatoria (el candidato no puede avanzar de etapa sin completarla)
            </label>
          </>
        )}
        {psico && !externa && (
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-2">Prueba o batería</span>
            <BloquePrueba catalogo={catalogo} seleccion={seleccion} onChange={setSeleccion} deshabilitado={Boolean(ocupado)} />
          </div>
        )}
        {psico && !externa && <CampoCorreo valor={correo} onChange={setCorreo} original={c.correo ?? ""} deshabilitado={Boolean(ocupado)} />}
        {psico && externa && (
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-2">Prueba externa</span>
            <CamposExterna nombre={nombreExterna} setNombre={setNombreExterna} archivo={pdf} setArchivo={setPdf} />
          </div>
        )}
        {error && <p role="alert" className="rounded-xl border border-bad/40 bg-bad-soft px-3 py-2 text-sm font-semibold text-bad">{error}</p>}

        <div className="mt-1 flex flex-wrap items-start justify-between gap-2">
          {psico ? (
            <Button size="sm" variant="secondary" onClick={() => { setExterna((x) => !x); setError(""); }} disabled={Boolean(ocupado)}>
              {externa ? "Elegir del catálogo" : "Agregar prueba externa"}
            </Button>
          ) : <span />}
          <div className="flex flex-wrap items-start justify-end gap-2">
            <Button variant="outline" size="sm" onClick={onClose} disabled={Boolean(ocupado)}>Cancelar</Button>
            {psico && externa && (
              <BotonPrincipal puede={Boolean(nombreExterna.trim() && pdf)} ocupado={ocupado === "externa"} pista="Escribe el nombre y sube el PDF de resultados" onClick={() => ejecutar("externa")}>
                Guardar prueba externa
              </BotonPrincipal>
            )}
            {psico && !externa && (
              <>
                <Button variant="outline" size="sm" onClick={() => ejecutar("sin_enviar")} disabled={Boolean(ocupado)}>
                  {ocupado === "sin_enviar" ? "Agregando…" : "Agregar sin enviar"}
                </Button>
                <BotonPrincipal puede={!sinCatalogo && seleccion.length > 0 && esCorreoValido(correo)} ocupado={ocupado === "enviar"} pista={pistaEnvio(sinCatalogo, seleccion, correo)} onClick={() => ejecutar("enviar")}>
                  {ocupado === "enviar" ? "Enviando…" : "Enviar ahora"}
                </BotonPrincipal>
              </>
            )}
            {!psico && (
              <Button size="sm" onClick={() => ejecutar("simple")} disabled={Boolean(ocupado) || !tipo}>{ocupado ? "Agregando…" : "Agregar actividad"}</Button>
            )}
          </div>
        </div>
      </div>
    </ModalMarco>
  );
}

/* ================================================================ «Enviar prueba» desde la fila (mismo bloque) */

export function ModalEnviarPrueba({ c, paso, onClose, onListo }: {
  c: Candidato;
  paso: PasoSeguimiento;
  onClose: () => void;
  onListo: (aviso: { tono: "ok" | "warn" | "error"; texto: string }, candidato?: Candidato) => void;
}) {
  const catalogo = useCatalogo();
  const [seleccion, setSeleccion] = useState<number[]>([]);
  const [externa, setExterna] = useState(false);
  const [nombreExterna, setNombreExterna] = useState("");
  const [pdf, setPdf] = useState<File | null>(null);
  const [correo, setCorreo] = useState(c.correo ?? "");
  const [error, setError] = useState("");
  const [ocupado, setOcupado] = useState("");
  const candado = useRef(false);
  // la batería de la ruta/vacante llega preseleccionada (solo las que siguen activas en el catálogo)
  useEffect(() => {
    if (catalogo) setSeleccion((paso.pruebas ?? []).map(Number).filter((id) => catalogo.some((x) => x.id === id)));
  }, [catalogo, paso.pruebas]);

  async function enviar() {
    if (candado.current) return;
    candado.current = true;
    setOcupado("enviar");
    setError("");
    const r = await asignarPsicometria(c.id, { pruebaIds: seleccion, pasoId: paso.id, correo });
    candado.current = false;
    setOcupado("");
    if (!r.ok) return setError(r.error);
    onListo({ tono: r.data.simulado ? "warn" : "ok", texto: textoEnvio(r.data, r.data.evaluacion.nombre) }, r.data.candidato);
  }

  async function externaGuardar() {
    if (candado.current || !pdf) return;
    candado.current = true;
    setOcupado("externa");
    setError("");
    const r = await guardarExterna(c, paso.id, nombreExterna, pdf);
    candado.current = false;
    setOcupado("");
    if (!r.ok) return setError(r.error);
    onListo({ tono: "ok", texto: `«${nombreExterna.trim()}» quedó Completada con su PDF de resultados.` }, r.candidato);
  }

  const sinCatalogo = catalogo !== null && catalogo.length === 0;
  return (
    <ModalMarco titulo={`Enviar prueba: ${paso.nombre}`} subtitulo="Elige la prueba o batería y envíala al candidato." onClose={onClose}>
      <div className="flex flex-col gap-3">
        {!externa ? (
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-2">Prueba o batería</span>
            <BloquePrueba catalogo={catalogo} seleccion={seleccion} onChange={setSeleccion} deshabilitado={Boolean(ocupado)} />
            <div className="mt-1.5">
              <CampoCorreo valor={correo} onChange={setCorreo} original={c.correo ?? ""} deshabilitado={Boolean(ocupado)} />
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-2">Prueba externa</span>
            <CamposExterna nombre={nombreExterna} setNombre={setNombreExterna} archivo={pdf} setArchivo={setPdf} />
          </div>
        )}
        {error && <p role="alert" className="rounded-xl border border-bad/40 bg-bad-soft px-3 py-2 text-sm font-semibold text-bad">{error}</p>}
        <div className="mt-1 flex flex-wrap items-start justify-between gap-2">
          <Button size="sm" variant="secondary" onClick={() => { setExterna((x) => !x); setError(""); }} disabled={Boolean(ocupado)}>
            {externa ? "Elegir del catálogo" : "Agregar prueba externa"}
          </Button>
          <div className="flex flex-wrap items-start justify-end gap-2">
            <Button variant="outline" size="sm" onClick={onClose} disabled={Boolean(ocupado)}>Cancelar</Button>
            {externa ? (
              <BotonPrincipal puede={Boolean(nombreExterna.trim() && pdf)} ocupado={ocupado === "externa"} pista="Escribe el nombre y sube el PDF de resultados" onClick={externaGuardar}>
                Guardar prueba externa
              </BotonPrincipal>
            ) : (
              <BotonPrincipal puede={!sinCatalogo && seleccion.length > 0 && esCorreoValido(correo)} ocupado={ocupado === "enviar"} pista={pistaEnvio(sinCatalogo, seleccion, correo)} onClick={enviar}>
                {ocupado === "enviar" ? "Enviando…" : "Enviar ahora"}
              </BotonPrincipal>
            )}
          </div>
        </div>
      </div>
    </ModalMarco>
  );
}

/* ================================================================ Fila de la ruta: estado + UNA acción */

export function EstadoFilaPsicometria({ ps }: { ps: PsicometriaPaso }) {
  return <Badge tone={TONO_PSICOMETRIA[ps.status]}>{ps.statusTexto}</Badge>;
}

/** «Copiar liga»: la URL REAL de la prueba (+ clave). Solo copia: nunca marca la actividad como enviada. */
function CopiarLiga({ ps }: { ps: PsicometriaPaso }) {
  const [copiada, setCopiada] = useState(false);
  if (!ps.liga) return null;
  const texto = ps.clave ? `${ps.liga}\nClave de acceso: ${ps.clave}` : ps.liga;
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(texto);
          setCopiada(true);
          setTimeout(() => setCopiada(false), 2000);
        } catch {
          window.prompt("Copia la liga:", texto);
        }
      }}
      className="inline-flex items-center gap-1 font-semibold text-brand hover:underline"
      title={ps.clave ? `Copia la liga y la clave ${ps.clave}` : "Copia la liga de la prueba"}
    >
      <Copy className="h-3 w-3" /> {copiada ? "Copiada" : "Copiar liga"}
    </button>
  );
}

export function DetalleFilaPsicometria({ ps }: { ps: PsicometriaPaso }) {
  if (ps.status === "error_envio") {
    return (
      <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 pl-[22px] text-[11px] font-semibold text-bad">
        <span className="inline-flex items-center gap-1"><AlertTriangle className="h-3 w-3" /> La prueba se generó, pero no le llegó al candidato.</span>
        <CopiarLiga ps={ps} />
      </p>
    );
  }
  if (ps.status === "enviada" && ps.liga && ps.dias_sin_respuesta == null) {
    return <p className="pl-[22px] text-[11px]"><CopiarLiga ps={ps} /></p>;
  }
  if (ps.status === "completada" && ps.result_summary) {
    return <p className="truncate pl-[22px] text-[11px] text-ink-2" title={ps.result_summary}>{ps.result_summary}</p>;
  }
  if (ps.status === "enviada" && ps.dias_sin_respuesta != null) {
    return (
      <p className="flex flex-wrap items-center gap-x-2 pl-[22px] text-[11px] font-semibold text-warn">
        <span className="inline-flex items-center gap-1"><AlertTriangle className="h-3 w-3" /> Sin respuesta en {ps.dias_sin_respuesta} días</span>
        <CopiarLiga ps={ps} />
      </p>
    );
  }
  return null;
}

/** Texto de la única acción de la fila según el estado (null = sin acción). */
export function textoAccionPsicometria(ps: PsicometriaPaso): string {
  return ps.status === "sin_enviar" ? "Enviar prueba" : ps.status === "enviada" ? "Reenviar" : ps.status === "error_envio" ? "Reintentar envío" : "Ver resultado";
}

/** «Reenviar»: manda de nuevo la MISMA liga al candidato (portal del proveedor o liga del otro sistema). */
export async function reenviarPsicometria(ps: PsicometriaPaso): Promise<{ tono: "ok" | "warn" | "error"; texto: string }> {
  if (!ps.evaluacion) return { tono: "error", texto: "La prueba ya no tiene una asignación vigente." };
  if (!ps.reenvio) {
    return { tono: "warn", texto: ps.simulado
      ? "Modo simulado: este servidor no está conectado a la plataforma de evaluación, así que no hay liga que reenviar."
      : "Esta prueba no tiene una liga para el candidato que se pueda reenviar." };
  }
  const r = await enviarLigaEvaluacion(ps.evaluacion, ps.reenvio);
  if (!r.ok) return { tono: "error", texto: r.error };
  const ok = (r.data.resultados ?? []).filter((x) => x.enviado).map((x) => x.canal);
  return ok.length
    ? { tono: "ok", texto: `Prueba reenviada al candidato por ${ok.join(" y ")}.` }
    : { tono: "warn", texto: "No se pudo enviar por ningún canal: revisa el teléfono y el correo del candidato, o usa «Copiar liga»." };
}

/** «Ver resultado»: el PDF de resultados (externa o del proveedor); sin archivo, la evaluación en su pestaña. */
export function urlResultadoPsicometria(ps: PsicometriaPaso): string | null {
  return ps.result_file_url ? urlArchivo(ps.result_file_url) : null;
}
