"use client";

/* «Agregar actividad / evaluación» DINÁMICO (2026-10-08, todas las Cuentas). Al elegir el tipo, el formulario muestra SOLO
   los campos que esa ejecución necesita, precargados con lo que ya define la vacante (`GET …/actividades/precarga`):
     - Entrevista humana: entrevistador (interno/externo con su contacto) y cita opcional.
     - Médica: médico/proveedor con su contacto, examen solicitado y cita opcional (el consentimiento se pide solo).
     - Psicométrica: prueba/batería y forma de aplicación (proveedor integrado · liga externa · captura manual).
     - Técnica / Socioeconómica / Otra: instrucciones, forma de aplicación y evaluador.
     - Referencias: cantidad, datos que se piden y responsable de verificar.
   «Registrar evaluación ya realizada» oculta la configuración futura y muestra la captura del resultado (dictamen, score,
   quién la aplicó, comentarios, archivo; en Referencias, los contactos verificados).
   Guardar inserta UNA actividad completamente configurada (`POST …/actividades`): si puede iniciar, se inicia; si no, queda
   «Lista para iniciar» o dice qué la bloquea. «Iniciar» en la ficha ejecuta lo guardado sin reabrir este formulario.
   Validación: `validarActividad` (mismas dependencias condicionales que `actividades.validar_config` en la API). */

import { useEffect, useMemo, useRef, useState } from "react";
import { CalendarClock, ClipboardCheck, Info, Loader2, Sparkles } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import { ModalMarco, inputRH } from "@/components/dashboard/modulos-rh";
import {
  CamposCita, SelectorEvaluador, citaEntrada, citaVacia, evaluadorEntrada, evaluadorVacio, useCatalogoEvaluadores, useTeamsConectado,
  validarCita, validarEvaluador, type EstadoCita, type EstadoEvaluador,
} from "@/components/dashboard/evaluaciones/campos-evaluacion";
import {
  agregarActividadConfigurada, fetchOpcionesProceso, fetchPrecargaActividad, fetchPruebasPsicometricas, nombreEtapa, proveedorVisible,
  type ConfigActividad, type OpcionesProceso, type PrecargaActividad, type PruebaPsicometrica, type RespuestaAgregarActividad,
} from "@/lib/api";
import type { Candidato } from "@/lib/data";
import { cn } from "@/lib/utils";

type Forma = NonNullable<ConfigActividad["forma"]>;
type Contacto = { nombre: string; empresa: string; telefono: string; dictamen: string; comentario: string };

/** Estado completo del formulario (lo que no aplica al tipo elegido simplemente no se valida ni se envía). */
export interface FormActividad {
  tipo: string;
  nombre: string;
  obligatorio: boolean;
  yaRealizada: boolean;
  forma: Forma | "";
  evaluador: EstadoEvaluador;
  conCita: boolean;
  cita: EstadoCita;
  instrucciones: string;
  examen: string;
  pruebaIds: number[];
  ligaExterna: string;
  proveedor: string;
  refCantidad: number;
  refDatos: string[];
  iniciarAlGuardar: boolean;
  // resultado (ya realizada)
  conclusion: string;
  score: string;
  realizadaPor: string;
  comentarios: string;
  contactos: Contacto[];
}

const EVALUACIONES = ["entrevista_humana", "medica", "psicometrica", "socioeconomica", "tecnica", "referencias", "otra"];
const CON_FORMA = ["tecnica", "socioeconomica", "otra"];
const QUIEN: Record<string, string> = {
  entrevista_humana: "Entrevistador", medica: "Médico o proveedor", referencias: "Responsable de verificar", tecnica: "Evaluador",
  socioeconomica: "Evaluador o empresa", otra: "Evaluador",
};
const FORMAS_PSICO: { valor: Forma; texto: string }[] = [
  { valor: "integrada", texto: "Proveedor integrado" }, { valor: "liga_otro_sistema", texto: "Liga externa" }, { valor: "registro_directo", texto: "Captura manual" },
];
const FORMAS_GENERALES: { valor: Forma; texto: string }[] = [
  { valor: "asignada", texto: "Asignar a un evaluador" }, { valor: "liga_otro_sistema", texto: "Liga de otro sistema" }, { valor: "registro_directo", texto: "Captura manual" },
];

const vacio = (tipo = ""): FormActividad => ({
  tipo, nombre: "", obligatorio: false, yaRealizada: false, forma: "", evaluador: evaluadorVacio, conCita: false, cita: citaVacia,
  instrucciones: "", examen: "", pruebaIds: [], ligaExterna: "", proveedor: "", refCantidad: 2, refDatos: ["empresa"],
  iniciarAlGuardar: true, conclusion: "", score: "", realizadaPor: "", comentarios: "", contactos: [],
});

const esUrl = (v: string) => /^https?:\/\/\S+$/i.test(v.trim());
export const esEvaluacion = (tipo: string) => EVALUACIONES.includes(tipo);
/** ¿Este tipo y forma necesitan evaluador? */
export const pideEvaluador = (f: Pick<FormActividad, "tipo" | "forma">) =>
  ["entrevista_humana", "medica", "referencias"].includes(f.tipo) || (CON_FORMA.includes(f.tipo) && f.forma === "asignada");

/** Dependencias condicionales del formulario → { campo: mensaje }. Vacío = se puede guardar. */
export function validarActividad(f: FormActividad, teams: boolean): Partial<Record<keyof FormActividad, string>> {
  const e: Partial<Record<keyof FormActividad, string>> = {};
  if (!f.tipo) e.tipo = "Elige el tipo de actividad.";
  if (f.tipo === "otra" && !f.nombre.trim()) e.nombre = "Escribe el nombre de la actividad.";
  if (!esEvaluacion(f.tipo)) return e;
  if (f.yaRealizada) {
    if (f.tipo === "medica") e.yaRealizada = "La médica requiere el consentimiento del candidato por su liga.";
    if (f.tipo === "entrevista_humana" && !f.conclusion) e.conclusion = "Elige la conclusión de la entrevista.";
    if (f.score.trim() && !(Number(f.score) >= 0 && Number(f.score) <= 100)) e.score = "El score va de 0 a 100.";
    if (!f.conclusion && !f.score.trim() && !f.comentarios.trim() && !f.contactos.some((c) => c.nombre.trim())) {
      e.conclusion = e.conclusion ?? "Captura el resultado: dictamen, score o comentario.";
    }
    return e;
  }
  if (f.tipo === "psicometrica") {
    if (!f.forma) e.forma = "Elige la forma de aplicación.";
    if (f.forma === "integrada" && !f.pruebaIds.length) e.pruebaIds = "Elige la prueba o batería.";
    if (f.forma === "liga_otro_sistema" && !esUrl(f.ligaExterna)) e.ligaExterna = "Captura la liga externa (https://…).";
  }
  if (CON_FORMA.includes(f.tipo)) {
    if (!f.forma) e.forma = "Elige la forma de aplicación.";
    if (f.forma === "liga_otro_sistema" && !esUrl(f.ligaExterna)) e.ligaExterna = "Captura la liga del otro sistema (https://…).";
  }
  if (pideEvaluador(f)) {
    const m = validarEvaluador(f.evaluador);
    if (m) e.evaluador = m;
  }
  if (f.tipo === "medica" && !f.examen.trim()) e.examen = "Indica el examen solicitado.";
  if (f.tipo === "referencias" && (f.refCantidad < 1 || f.refCantidad > 5)) e.refCantidad = "Pide entre 1 y 5 referencias.";
  if (f.conCita && ["entrevista_humana", "medica"].includes(f.tipo)) {
    const m = validarCita(f.cita, teams);
    if (m) e.cita = m;
  }
  return e;
}

/** Lo que viaja a la API (solo los campos del tipo y forma elegidos). */
export function configDe(f: FormActividad, teams: boolean): ConfigActividad {
  const c: ConfigActividad = { iniciar_al_guardar: f.iniciarAlGuardar };
  if (f.tipo === "psicometrica" || CON_FORMA.includes(f.tipo)) c.forma = f.forma || undefined;
  if (pideEvaluador(f)) {
    const ev = evaluadorEntrada(f.evaluador);
    c.evaluador = { tipo: ev.tipo, usuario_id: ev.usuarioId ?? null, contacto_id: ev.contactoId ?? null, nombre: ev.nombre ?? "", correo: ev.correo ?? "", whatsapp: ev.whatsapp ?? "" };
  }
  if (f.conCita && ["entrevista_humana", "medica"].includes(f.tipo)) {
    const ci = citaEntrada(f.cita, teams);
    c.cita = { fecha: ci.fecha, hora: ci.hora, modalidad: ci.modalidad, direccion: ci.direccion ?? "", liga_videollamada: ci.ligaVideollamada ?? "",
      telefono: ci.telefono ?? "", usar_teams: ci.usarTeams ?? true };
  }
  if (f.instrucciones.trim()) c.instrucciones = f.instrucciones.trim();
  if (f.tipo === "medica") c.examen = f.examen.trim();
  if (f.tipo === "psicometrica" && f.forma === "integrada") c.prueba_ids = f.pruebaIds;
  if (f.forma === "liga_otro_sistema") c.liga_externa_candidato = f.ligaExterna.trim();
  if (f.tipo === "psicometrica" && f.forma !== "integrada" && f.proveedor.trim()) c.proveedor = f.proveedor.trim();
  if (f.tipo === "referencias") c.referencias = { cantidad: f.refCantidad, datos: f.refDatos };
  return c;
}

export function ModalAgregarActividad({ c, onClose, onAgregada, tipoInicial = "" }: {
  c: Candidato;
  onClose: () => void;
  onAgregada: (r: RespuestaAgregarActividad, aviso: { tono: "ok" | "warn" | "error"; texto: string }) => void;
  tipoInicial?: string;
}) {
  const { internos, contactos } = useCatalogoEvaluadores(c.clienteIdVacante ?? null);
  const teams = useTeamsConectado();
  const [opciones, setOpciones] = useState<OpcionesProceso | null>(null);
  const [catalogo, setCatalogo] = useState<PruebaPsicometrica[]>([]);
  const [f, setF] = useState<FormActividad>(vacio(tipoInicial));
  const [precarga, setPrecarga] = useState<PrecargaActividad | null>(null);
  const [cargando, setCargando] = useState(false);
  const [intentado, setIntentado] = useState(false);
  const [error, setError] = useState("");
  const [archivos, setArchivos] = useState<File[]>([]);
  const [guardando, setGuardando] = useState(false);
  const candado = useRef(false);

  useEffect(() => {
    fetchOpcionesProceso().then((o) => setOpciones(o ?? null));
    fetchPruebasPsicometricas(false, c.puesto ?? "").then((p) => setCatalogo((p ?? []).filter((x) => x.modo === "integrada" && x.activa)));
  }, [c.puesto]);

  /** Cambiar de tipo reinicia el formulario y lo precarga con lo que define la vacante. */
  useEffect(() => {
    if (!f.tipo) return;
    let vivo = true;
    setCargando(true);
    fetchPrecargaActividad(c.id, f.tipo).then((r) => {
      if (!vivo) return;
      setCargando(false);
      if (!r.ok) return setPrecarga(null);
      const p = r.data;
      setPrecarga(p);
      const k = p.config;
      setF((x) => ({
        ...vacio(x.tipo),
        nombre: x.tipo === "otra" ? "" : "",
        forma: (k.forma as Forma) ?? (x.tipo === "psicometrica" ? (k.prueba_ids?.length ? "integrada" : "") : CON_FORMA.includes(x.tipo) ? "asignada" : ""),
        evaluador: k.evaluador?.tipo === "interno" ? { ...evaluadorVacio, tipo: "interno", usuarioId: k.evaluador.usuario_id ?? null }
          : k.evaluador?.tipo === "externo" ? { ...evaluadorVacio, tipo: "externo", contacto: k.evaluador.contacto_id ?? "nuevo", nombre: k.evaluador.nombre ?? "",
            correo: k.evaluador.correo ?? "", whatsapp: k.evaluador.whatsapp ?? "" }
          : { ...evaluadorVacio, tipo: x.tipo === "medica" || x.tipo === "socioeconomica" ? "externo" : "interno" },
        instrucciones: k.instrucciones ?? "", examen: k.examen ?? "", pruebaIds: k.prueba_ids ?? [], ligaExterna: k.liga_externa_candidato ?? "",
        proveedor: k.proveedor ?? "", refCantidad: k.referencias?.cantidad ?? 2, refDatos: k.referencias?.datos ?? ["empresa"],
        conCita: Boolean(k.cita),
      }));
    });
    return () => { vivo = false; };
  }, [c.id, f.tipo]);

  const errores = useMemo(() => validarActividad(f, teams), [f, teams]);
  const ver = (k: keyof FormActividad) => (intentado ? errores[k] : undefined);
  const tipos = (opciones?.tiposPaso ?? []).filter((t) => !["solicitud_web", "prefiltro_web", "prefiltro_whatsapp", "alta"].includes(t.valor));
  const evaluacion = esEvaluacion(f.tipo);
  const deVacante = (k: keyof ConfigActividad) => precarga?.origen?.[k] === "vacante";
  const set = <K extends keyof FormActividad>(k: K, v: FormActividad[K]) => setF((x) => ({ ...x, [k]: v }));

  async function guardar() {
    setIntentado(true);
    if (candado.current || Object.keys(errores).length) return;
    candado.current = true;
    setGuardando(true);
    setError("");
    const r = await agregarActividadConfigurada(c.id, {
      tipo: f.tipo, nombre: f.nombre.trim() || undefined, obligatorio: f.obligatorio,
      ...(f.yaRealizada
        ? { ya_realizada: true, resultado: {
            conclusion: f.conclusion, score: f.score.trim() ? Number(f.score) : null, realizada_por: f.realizadaPor.trim(), comentarios: f.comentarios.trim(),
            referencias: f.contactos.filter((x) => x.nombre.trim()).map((x) => ({ ...x, contactado: true })) } }
        : { config: evaluacion ? configDe(f, teams) : undefined }),
    }, archivos);
    setGuardando(false);
    candado.current = false;
    if (!r.ok) return setError(r.error);
    const d = r.data;
    const adv = d.advertencias?.length ? ` ${d.advertencias.join(" ")}` : "";
    onAgregada(d, {
      tono: d.bloqueo || d.faltan?.length || adv ? "warn" : "ok",
      texto: `${d.mensaje}${d.bloqueo ? ` ${d.bloqueo}.` : ""}${d.faltan?.includes("correo") ? " Falta el correo del candidato: agrégalo y la prueba se envía sola." : ""}${adv}`,
    });
  }

  return (
    <ModalMarco titulo="Agregar actividad o evaluación" subtitulo={`Solo para este candidato · etapa actual: ${nombreEtapa(c.etapa)}. La plantilla y la vacante no cambian.`} onClose={onClose}>
      <div className="flex max-h-[70vh] flex-col gap-3 overflow-y-auto pr-1">
        <Fila etiqueta="Tipo de actividad" error={ver("tipo")}>
          <select className={cn(inputRH, "h-10")} value={f.tipo} onChange={(e) => setF(vacio(e.target.value))}>
            <option value="">Elegir…</option>
            {tipos.map((t) => <option key={t.valor} value={t.valor}>{t.texto}</option>)}
          </select>
        </Fila>

        {f.tipo && cargando && <Loader2 className="h-4 w-4 animate-spin text-ink-3" />}

        {f.tipo && !cargando && (
          <>
            <Fila etiqueta={f.tipo === "otra" ? "Nombre de la actividad *" : "Nombre (opcional)"} error={ver("nombre")}>
              <input className={inputRH} value={f.nombre} onChange={(e) => set("nombre", e.target.value)} placeholder={precarga?.nombre ?? ""} />
            </Fila>

            {evaluacion && (
              <label className={cn("flex items-center gap-2 rounded-xl border px-3 py-2 text-sm", f.yaRealizada ? "border-brand/40 bg-brand-soft/40" : "border-border-soft")}>
                <input type="checkbox" className="h-4 w-4 accent-brand" checked={f.yaRealizada} disabled={precarga?.puedeRegistrarRealizada === false}
                  onChange={(e) => set("yaRealizada", e.target.checked)} />
                <ClipboardCheck className="h-4 w-4 text-ink-3" /> Registrar evaluación ya realizada
                {precarga?.puedeRegistrarRealizada === false && <span className="text-[11px] text-ink-3">(la médica requiere el consentimiento del candidato)</span>}
              </label>
            )}

            {evaluacion && !f.yaRealizada && (
              <CamposConfiguracion f={f} set={set} ver={ver} catalogo={catalogo} internos={internos} contactos={contactos} teams={teams}
                deVacante={deVacante} precarga={precarga} clienteNombre={c.clienteVacante ?? null} />
            )}

            {evaluacion && f.yaRealizada && (
              <CamposResultado f={f} set={set} ver={ver} conclusiones={precarga?.conclusiones ?? []} onArchivos={setArchivos} />
            )}

            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="h-4 w-4 accent-brand" checked={f.obligatorio} onChange={(e) => set("obligatorio", e.target.checked)} />
              Obligatoria (el candidato no avanza de etapa sin cumplirla)
            </label>
            {evaluacion && !f.yaRealizada && (
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="h-4 w-4 accent-brand" checked={f.iniciarAlGuardar} onChange={(e) => set("iniciarAlGuardar", e.target.checked)} />
                Iniciar al guardar si la ruta ya lo permite (si no, queda «Lista para iniciar»)
              </label>
            )}
          </>
        )}

        {intentado && Object.keys(errores).length > 0 && (
          <p className="rounded-xl border border-warn/40 bg-warn-soft px-3 py-2 text-[12px] text-ink-2">Revisa los campos marcados.</p>
        )}
        {error && <p className="rounded-xl border border-bad/40 bg-bad-soft px-3 py-2 text-sm font-semibold text-bad">{error}</p>}
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onClose} disabled={guardando}>Cancelar</Button>
        <Button size="sm" onClick={guardar} disabled={guardando || !f.tipo}>
          {guardando ? "Guardando…" : f.yaRealizada ? "Guardar resultado" : evaluacion && f.iniciarAlGuardar ? "Guardar e iniciar" : "Agregar actividad"}
        </Button>
      </div>
    </ModalMarco>
  );
}

type Setter = <K extends keyof FormActividad>(k: K, v: FormActividad[K]) => void;
type Ver = (k: keyof FormActividad) => string | undefined;

function Fila({ etiqueta, error, children, deVacante }: { etiqueta: React.ReactNode; error?: string; children: React.ReactNode; deVacante?: boolean }) {
  return (
    <div className="flex flex-col gap-1 text-xs text-ink-2">
      <span className="flex items-center gap-1.5">
        {etiqueta}
        {deVacante && <Badge tone="brand"><Sparkles className="h-3 w-3" /> De la vacante</Badge>}
      </span>
      {children}
      {error && <span className="text-[11px] font-semibold text-bad">{error}</span>}
    </div>
  );
}

function Segmentos({ valor, opciones, onChange }: { valor: string; opciones: { valor: string; texto: string }[]; onChange: (v: string) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {opciones.map((o) => (
        <button key={o.valor} type="button" onClick={() => onChange(o.valor)}
          className={cn("rounded-lg border px-2.5 py-1.5 text-[12px] font-semibold",
            valor === o.valor ? "border-brand bg-brand-soft text-brand" : "border-border-soft text-ink-2 hover:border-brand/40")}>
          {o.texto}
        </button>
      ))}
    </div>
  );
}

function CamposConfiguracion({ f, set, ver, catalogo, internos, contactos, teams, deVacante, precarga, clienteNombre }: {
  f: FormActividad; set: Setter; ver: Ver; catalogo: PruebaPsicometrica[];
  internos: Parameters<typeof SelectorEvaluador>[0]["internos"]; contactos: Parameters<typeof SelectorEvaluador>[0]["contactos"];
  teams: boolean; deVacante: (k: keyof ConfigActividad) => boolean; precarga: PrecargaActividad | null; clienteNombre: string | null;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-border-soft p-3">
      {f.tipo === "medica" && (
        <p className="flex items-start gap-1.5 rounded-lg bg-human-soft/50 px-2.5 py-1.5 text-[12px] text-ink-2">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-human" />
          El consentimiento expreso se le pedirá al candidato automáticamente; el médico recibe su liga solo cuando acepte.
        </p>
      )}

      {f.tipo === "psicometrica" && (
        <>
          <Fila etiqueta="Forma de aplicación" error={ver("forma")} deVacante={deVacante("forma")}>
            <Segmentos valor={f.forma} opciones={FORMAS_PSICO} onChange={(v) => set("forma", v as Forma)} />
          </Fila>
          {f.forma === "integrada" && (
            <Fila etiqueta={`Prueba o batería · proveedor: ${proveedorVisible("Psicométricas.mx")}`} error={ver("pruebaIds")} deVacante={deVacante("prueba_ids")}>
              {catalogo.length === 0 ? (
                <span className="text-[12px] text-warn">No hay pruebas conectadas al proveedor en el catálogo (Configuración → Pruebas psicométricas).</span>
              ) : (
                <div className="flex max-h-40 flex-col gap-1 overflow-y-auto rounded-xl border border-border-soft p-2">
                  {catalogo.map((p) => (
                    <label key={p.id} className="flex items-center gap-2 text-[13px] text-ink">
                      <input type="checkbox" className="h-4 w-4 accent-brand" checked={f.pruebaIds.includes(p.id)}
                        onChange={(e) => set("pruebaIds", e.target.checked ? [...f.pruebaIds, p.id] : f.pruebaIds.filter((x) => x !== p.id))} />
                      {p.nombre} {p.sugerida && <Badge tone="good">Sugerida</Badge>}
                    </label>
                  ))}
                </div>
              )}
              {!precarga?.candidatoTieneCorreo && (
                <span className="text-[11px] text-ink-3">El candidato no tiene correo: al guardar quedará «Falta correo» y la prueba se envía sola al agregarlo.</span>
              )}
            </Fila>
          )}
          {f.forma === "liga_otro_sistema" && (
            <>
              <Fila etiqueta="Liga de la prueba (la recibe el candidato)" error={ver("ligaExterna")}>
                <input className={inputRH} value={f.ligaExterna} onChange={(e) => set("ligaExterna", e.target.value)} placeholder="https://…" />
              </Fila>
              <Fila etiqueta="Proveedor (opcional)">
                <input className={inputRH} value={f.proveedor} onChange={(e) => set("proveedor", e.target.value)} />
              </Fila>
            </>
          )}
          {f.forma === "registro_directo" && (
            <p className="text-[12px] text-ink-3">Se aplica fuera del sistema y el resultado se captura con «Registrar resultado» (o marca «ya realizada» arriba).</p>
          )}
        </>
      )}

      {CON_FORMA.includes(f.tipo) && (
        <>
          <Fila etiqueta="Instrucciones (opcional)" deVacante={deVacante("instrucciones")}>
            <textarea className={cn(inputRH, "h-16 py-2")} value={f.instrucciones} onChange={(e) => set("instrucciones", e.target.value)} />
          </Fila>
          <Fila etiqueta="Forma de aplicación" error={ver("forma")} deVacante={deVacante("forma")}>
            <Segmentos valor={f.forma} opciones={FORMAS_GENERALES} onChange={(v) => set("forma", v as Forma)} />
          </Fila>
          {f.forma === "liga_otro_sistema" && (
            <Fila etiqueta="Liga del otro sistema" error={ver("ligaExterna")}>
              <input className={inputRH} value={f.ligaExterna} onChange={(e) => set("ligaExterna", e.target.value)} placeholder="https://…" />
            </Fila>
          )}
        </>
      )}

      {f.tipo === "medica" && (
        <Fila etiqueta="Examen solicitado" error={ver("examen")} deVacante={deVacante("examen")}>
          <input className={inputRH} value={f.examen} onChange={(e) => set("examen", e.target.value)} placeholder="Examen médico general, antidoping…" />
        </Fila>
      )}

      {f.tipo === "referencias" && (
        <>
          <Fila etiqueta="Referencias que se piden" error={ver("refCantidad")} deVacante={deVacante("referencias")}>
            <input type="number" min={1} max={5} className={cn(inputRH, "w-24")} value={f.refCantidad} onChange={(e) => set("refCantidad", Number(e.target.value))} />
          </Fila>
          <Fila etiqueta="Datos que debe capturar el candidato">
            <div className="flex flex-wrap gap-3">
              {(precarga?.datosReferencia ?? []).map((d) => (
                <label key={d.valor} className="flex items-center gap-1.5 text-[13px] text-ink">
                  <input type="checkbox" className="h-4 w-4 accent-brand" checked={f.refDatos.includes(d.valor)}
                    onChange={(e) => set("refDatos", e.target.checked ? [...f.refDatos, d.valor] : f.refDatos.filter((x) => x !== d.valor))} />
                  {d.texto}
                </label>
              ))}
            </div>
          </Fila>
        </>
      )}

      {pideEvaluador(f) && (
        <Fila etiqueta={QUIEN[f.tipo] ?? "Evaluador"} error={ver("evaluador")} deVacante={deVacante("evaluador")}>
          <SelectorEvaluador valor={f.evaluador} onChange={(v) => set("evaluador", v)} internos={internos} contactos={contactos} clienteNombre={clienteNombre} />
        </Fila>
      )}

      {["entrevista_humana", "medica"].includes(f.tipo) && (
        <>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" className="h-4 w-4 accent-brand" checked={f.conCita} onChange={(e) => set("conCita", e.target.checked)} />
            <CalendarClock className="h-4 w-4 text-ink-3" /> Programar cita
          </label>
          {f.conCita && (
            <Fila etiqueta="Cita" error={ver("cita")}>
              <CamposCita valor={f.cita} onChange={(v) => set("cita", v)} teams={teams} />
            </Fila>
          )}
        </>
      )}
    </div>
  );
}

function CamposResultado({ f, set, ver, conclusiones, onArchivos }: {
  f: FormActividad; set: Setter; ver: Ver; conclusiones: { valor: string; texto: string }[]; onArchivos: (a: File[]) => void;
}) {
  const cambiar = (i: number, k: keyof Contacto, v: string) => set("contactos", f.contactos.map((x, j) => (j === i ? { ...x, [k]: v } : x)));
  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-brand/30 p-3">
      <Fila etiqueta={f.tipo === "entrevista_humana" ? "Conclusión *" : "Dictamen"} error={ver("conclusion") ?? ver("yaRealizada")}>
        <select className={cn(inputRH, "h-10")} value={f.conclusion} onChange={(e) => set("conclusion", e.target.value)}>
          <option value="">{f.tipo === "entrevista_humana" ? "Elegir…" : "Sin dictamen"}</option>
          {conclusiones.map((o) => <option key={o.valor} value={o.valor}>{o.texto}</option>)}
        </select>
      </Fila>
      <div className="grid gap-3 sm:grid-cols-2">
        <Fila etiqueta="Score (0-100, opcional)" error={ver("score")}>
          <input className={inputRH} inputMode="decimal" value={f.score} onChange={(e) => set("score", e.target.value)} />
        </Fila>
        <Fila etiqueta="¿Quién la aplicó?">
          <input className={inputRH} value={f.realizadaPor} onChange={(e) => set("realizadaPor", e.target.value)} />
        </Fila>
      </div>
      <Fila etiqueta="Comentarios">
        <textarea className={cn(inputRH, "h-16 py-2")} value={f.comentarios} onChange={(e) => set("comentarios", e.target.value)} />
      </Fila>
      {f.tipo === "referencias" && (
        <div className="flex flex-col gap-2">
          <span className="text-xs text-ink-2">Contactos verificados</span>
          {f.contactos.map((x, i) => (
            <div key={i} className="grid gap-1.5 sm:grid-cols-2">
              <input className={inputRH} placeholder="Nombre *" value={x.nombre} onChange={(e) => cambiar(i, "nombre", e.target.value)} />
              <input className={inputRH} placeholder="Empresa" value={x.empresa} onChange={(e) => cambiar(i, "empresa", e.target.value)} />
              <input className={inputRH} placeholder="Teléfono" value={x.telefono} onChange={(e) => cambiar(i, "telefono", e.target.value)} />
              <select className={cn(inputRH, "h-10")} value={x.dictamen} onChange={(e) => cambiar(i, "dictamen", e.target.value)}>
                <option value="">Dictamen…</option>
                <option value="favorable">Favorable</option>
                <option value="con_observaciones">Con observaciones</option>
                <option value="desfavorable">Desfavorable</option>
              </select>
            </div>
          ))}
          <button type="button" className="self-start text-[12px] font-semibold text-brand hover:underline"
            onClick={() => set("contactos", [...f.contactos, { nombre: "", empresa: "", telefono: "", dictamen: "", comentario: "" }])}>
            + Agregar contacto verificado
          </button>
        </div>
      )}
      <Fila etiqueta="Archivo (PDF, imagen o Word, opcional)">
        <input type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.webp,.doc,.docx" onChange={(e) => onArchivos(Array.from(e.target.files ?? []))} />
      </Fila>
    </div>
  );
}
