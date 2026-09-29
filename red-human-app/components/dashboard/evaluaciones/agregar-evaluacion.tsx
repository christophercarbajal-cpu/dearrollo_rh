"use client";

/* Pantalla única «Agregar evaluación» (Evaluaciones unificadas — Fase 1, 2026-09-29, especificación sección 3).
   Sustituye «Programar entrevista humana» y «Agregar evaluación o verificación». Solo dos decisiones: QUÉ (tipo) y
   QUIÉN (cómo se realizará / evaluador); todo lo demás viene precargado u opcional y solo se muestran los campos de
   la opción elegida. Orden fijo: Tipo → ¿Cómo se realizará? → Evaluador → Programar cita → Más opciones.
   El botón final corresponde a la acción: «Guardar resultado» (registrar ahora), «Programar entrevista» (entrevista
   humana con cita) o «Agregar evaluación». Crear/guardar NUNCA manda al candidato a Contratación. */

import { useEffect, useMemo, useState } from "react";
import { CalendarClock, ChevronDown, ClipboardCheck, Eye, Loader2 } from "lucide-react";
import { Button } from "@/components/ui";
import { ModalMarco } from "@/components/dashboard/modulos-rh";
import { LineaNotificar, useNotificarAccion } from "@/components/dashboard/linea-notificar";
import { FormularioResultado } from "@/components/dashboard/evaluaciones/formulario-resultado";
import {
  CamposCita, Campo, Opciones, SelectorEvaluador, citaEntrada, citaVacia, evaluadorEntrada, evaluadorVacio, inputEv, nombreEvaluador,
  useCatalogoEvaluadores, useTeamsConectado, validarCita, validarEvaluador, type EstadoCita, type EstadoEvaluador,
} from "@/components/dashboard/evaluaciones/campos-evaluacion";
import {
  TIPOS_EVALUACION, crearEvaluacion, fetchPruebasPsicometricas, registrarResultadoEvaluacion, urlPreviewCorreo,
  type DatosResultado, type FormaEvaluacion, type PruebaPsicometrica, type RespuestaEvaluacion, type TipoEvaluacion,
} from "@/lib/api";
import type { Candidato } from "@/lib/data";
import { cn } from "@/lib/utils";

/** Conclusiones por tipo — solo para «Registrar resultado ahora» (antes de que exista la evaluación). Misma regla
 * que `models.conclusiones_de` en la API; una vez creada, las opciones siempre vienen de la API. */
const CONCLUSIONES: Record<"entrevista_humana" | "medica" | "general", { valor: string; texto: string }[]> = {
  entrevista_humana: [{ valor: "avanzar", texto: "Avanzar" }, { valor: "no_avanzar", texto: "No avanzar" }, { valor: "requiere_otra_entrevista", texto: "Requiere otra entrevista" }],
  medica: [{ valor: "apto", texto: "Apto" }, { valor: "apto_con_restricciones", texto: "Apto con restricciones" }, { valor: "no_apto", texto: "No apto" }],
  general: [{ valor: "favorable", texto: "Favorable" }, { valor: "con_observaciones", texto: "Con observaciones" }, { valor: "desfavorable", texto: "Desfavorable" }],
};
const conclusionesDe = (t: TipoEvaluacion) => CONCLUSIONES[t === "entrevista_humana" || t === "medica" ? t : "general"];

export type PresetEvaluacion = { tipo?: TipoEvaluacion; evaluador?: EstadoEvaluador; titulo?: string };

export function ModalAgregarEvaluacion({ c, preset, onClose, onListo }: {
  c: Candidato;
  preset?: PresetEvaluacion;
  onClose: () => void;
  onListo: (r: RespuestaEvaluacion, texto: string) => void;
}) {
  const clienteId = c.clienteIdVacante ?? null;
  const { internos, contactos } = useCatalogoEvaluadores(clienteId);
  const teams = useTeamsConectado();
  const [tipo, setTipo] = useState<TipoEvaluacion>(preset?.tipo ?? "entrevista_humana");
  const [nombre, setNombre] = useState("");
  const [forma, setForma] = useState<FormaEvaluacion>("asignada");
  const [evaluador, setEvaluador] = useState<EstadoEvaluador>(preset?.evaluador ?? evaluadorVacio);
  const [conCita, setConCita] = useState((preset?.tipo ?? "entrevista_humana") === "entrevista_humana");
  const [cita, setCita] = useState<EstadoCita>(citaVacia);
  const [masOpciones, setMasOpciones] = useState(false);
  const [instrucciones, setInstrucciones] = useState("");
  const [ligaExterna, setLigaExterna] = useState("");
  const [pruebas, setPruebas] = useState<PruebaPsicometrica[] | null>(null);
  const [pruebaId, setPruebaId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [preview, setPreview] = useState<null | "entrevistador" | "candidato">(null);
  // «Registrar resultado ahora»: si el resultado falla después de crear, el reintento no crea otra evaluación
  const [creada, setCreada] = useState<RespuestaEvaluacion | null>(null);
  const notificar = useNotificarAccion("evaluacion_asignada");

  useEffect(() => {
    fetchPruebasPsicometricas(false, c.puesto ?? "").then((p) => setPruebas((p ?? []).filter((x) => x.modo === "integrada" && x.activa)));
  }, [c.puesto]);

  const hayIntegradas = (pruebas?.length ?? 0) > 0 && tipo !== "entrevista_humana";
  const formas = useMemo(
    () => [
      { valor: "asignada" as const, texto: "Asignar a una persona", ayuda: "Recibe su liga para registrar el resultado" },
      { valor: "registro_directo" as const, texto: "Registrar resultado ahora", ayuda: "Ya se hizo; lo capturas tú" },
      { valor: "liga_otro_sistema" as const, texto: "Enviar liga de otro sistema", ayuda: "El candidato la realiza fuera" },
      ...(hayIntegradas ? [{ valor: "integrada" as const, texto: "Usar proveedor integrado", ayuda: "Psicométricas.mx u otro conectado" }] : []),
    ],
    [hayIntegradas],
  );

  function elegirTipo(t: TipoEvaluacion) {
    setTipo(t);
    setError("");
    // predeterminados por tipo (especificación): la cita solo viene activada en la entrevista humana
    if (forma !== "registro_directo") setConCita(t === "entrevista_humana");
    if (forma === "integrada" && t === "entrevista_humana") setForma("asignada");
  }
  function elegirForma(f: FormaEvaluacion) {
    setForma(f);
    setError("");
    if (f === "registro_directo") setConCita(false);
    else if (tipo === "entrevista_humana" && f === "asignada") setConCita(true);
  }

  const medicaDirecta = tipo === "medica" && forma === "registro_directo";
  const citaVisible = forma !== "registro_directo";
  const tipoTexto = TIPOS_EVALUACION.find((t) => t.valor === tipo)?.texto ?? "";

  function validar(): string {
    if (tipo === "otra" && !nombre.trim()) return "Con «Otra» captura el nombre de la evaluación.";
    if (forma === "asignada") {
      const e = validarEvaluador(evaluador);
      if (e) return e;
    }
    if (forma === "liga_otro_sistema" && !/^https?:\/\//i.test(ligaExterna.trim())) return "Captura la liga del otro sistema que recibirá el candidato (https://…).";
    if (forma === "integrada" && !pruebaId) return "Elige la prueba del proveedor integrado.";
    if (citaVisible && conCita) {
      const e = validarCita(cita, teams);
      if (e) return e;
    }
    return "";
  }

  async function crear(): Promise<RespuestaEvaluacion | { error: string }> {
    if (creada) return creada;
    const r = await crearEvaluacion(c.id, {
      tipo, nombre: nombre.trim(), forma,
      evaluador: forma === "asignada" ? evaluadorEntrada(evaluador) : null,
      instrucciones: instrucciones.trim(), ligaExternaCandidato: forma === "liga_otro_sistema" ? ligaExterna.trim() : "",
      pruebaId: forma === "integrada" ? pruebaId : null,
      cita: citaVisible && conCita ? citaEntrada(cita, teams) : null,
      notificar: forma === "registro_directo" ? undefined : notificar.value,
    });
    if (!r.ok) return { error: r.error };
    setCreada(r.data);
    return r.data;
  }

  async function agregar() {
    const e = validar();
    if (e) return setError(e);
    setEnviando(true);
    setError("");
    const r = await crear();
    setEnviando(false);
    if ("error" in r) return setError(r.error);
    const ev = r.evaluacion;
    const texto = ev.consentimiento === "pendiente"
      ? `«${ev.nombre}» agregada. En espera de consentimiento: mándale al candidato la liga desde «⋯» de la tarjeta. La liga del evaluador sale cuando lo otorgue.`
      : `«${ev.nombre}» ${tipo === "entrevista_humana" && ev.cita ? "programada" : "agregada"}.`;
    onListo(r, texto);
  }

  /** «Registrar resultado ahora»: crea la evaluación (sin evaluador ni avisos) y guarda el resultado con el formulario único. */
  async function registrarAhora(d: DatosResultado) {
    const e = validar();
    if (e) return { ok: false as const, error: e };
    const r = await crear();
    if ("error" in r) return { ok: false as const, error: r.error };
    const res = await registrarResultadoEvaluacion(r.evaluacion.codigo, { ...d, version: r.evaluacion.resultadoVersion, modo: "registrar" });
    if (res.ok) onListo({ ...r, evaluacion: res.data.evaluacion, candidato: res.data.candidato ?? r.candidato }, `Resultado de «${res.data.evaluacion.nombre}» guardado.`);
    return res;
  }

  const botonFinal = tipo === "entrevista_humana" && conCita && citaVisible ? "Programar entrevista" : "Agregar evaluación";
  const datosPreview = {
    evento: "agendada" as const, candidato: c.nombre, entrevistador: nombreEvaluador(evaluador, internos, contactos), vacante: c.puesto || "",
    empresa: c.empresaVisible || "", fecha: cita.fecha, hora: cita.hora, modalidad: cita.modalidad === "Teléfono" ? "Llamada" : cita.modalidad,
    liga: cita.modalidad === "Videollamada" ? cita.liga : "", ubicacion: cita.modalidad === "Presencial" ? cita.direccion : "",
    telefono: cita.modalidad === "Teléfono" ? cita.telefono : "", telefonoCandidato: c.telefono, comentario: instrucciones,
  };

  return (
    <ModalMarco titulo={preset?.titulo ?? "Agregar evaluación"} subtitulo={`${c.nombre} · ${c.puesto || "sin vacante"}. Ningún resultado mueve al candidato de etapa.`} onClose={onClose}>
      <div className="flex flex-col gap-5">
        <section>
          <h3 className="text-sm font-semibold text-ink">1 · Tipo</h3>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {TIPOS_EVALUACION.map((t) => (
              <button
                key={t.valor}
                type="button"
                onClick={() => elegirTipo(t.valor)}
                aria-pressed={tipo === t.valor}
                disabled={Boolean(creada)}
                className={cn("rounded-xl border px-3 py-2.5 text-left text-sm font-medium transition",
                  tipo === t.valor ? "border-brand bg-brand-soft text-brand" : "border-border-soft text-ink-2 hover:border-brand/50")}
              >
                {t.texto}
              </button>
            ))}
          </div>
          {tipo === "otra" && (
            <Campo etiqueta={<>Nombre <span className="text-bad">*</span></>} className="mt-3">
              <input value={nombre} onChange={(e) => setNombre(e.target.value)} placeholder="Ej. Prueba de manejo" className={inputEv} />
            </Campo>
          )}
          {tipo === "medica" && (
            <p className="mt-3 rounded-xl border border-warn/30 bg-warn-soft/50 px-3.5 py-2.5 text-[12px] leading-relaxed text-warn">
              La evaluación médica requiere el consentimiento expreso y por escrito del candidato. Mientras no lo otorgue no se envía la liga al evaluador ni se puede guardar un resultado.
            </p>
          )}
        </section>

        <section>
          <h3 className="text-sm font-semibold text-ink">2 · ¿Cómo se realizará?</h3>
          <div className="mt-2">
            <Opciones valor={forma} onChange={elegirForma} opciones={formas} />
          </div>
          {forma === "liga_otro_sistema" && (
            <Campo etiqueta={<>Liga que recibirá el candidato <span className="text-bad">*</span></>} className="mt-3">
              <input value={ligaExterna} onChange={(e) => setLigaExterna(e.target.value)} placeholder="https://…" className={inputEv} />
            </Campo>
          )}
          {forma === "integrada" && (
            <Campo etiqueta={<>Prueba del proveedor <span className="text-bad">*</span></>} className="mt-3">
              <select value={pruebaId ?? ""} onChange={(e) => setPruebaId(e.target.value ? Number(e.target.value) : null)} className={inputEv}>
                <option value="">Elige una prueba…</option>
                {(pruebas ?? []).map((p) => <option key={p.id} value={p.id}>{p.nombre} · {p.proveedor}{p.sugerida ? " · sugerida para el puesto" : ""}</option>)}
              </select>
            </Campo>
          )}
        </section>

        {forma === "asignada" && (
          <section>
            <h3 className="text-sm font-semibold text-ink">3 · Evaluador</h3>
            <div className="mt-2">
              <SelectorEvaluador valor={evaluador} onChange={setEvaluador} internos={internos} contactos={contactos} clienteNombre={c.clienteVacante} />
            </div>
          </section>
        )}

        {citaVisible && (
          <section>
            <label className="flex cursor-pointer items-center justify-between gap-3">
              <span className="text-sm font-semibold text-ink">Programar cita</span>
              <input type="checkbox" role="switch" checked={conCita} onChange={(e) => setConCita(e.target.checked)} className="h-5 w-9 cursor-pointer accent-[var(--brand)]" />
            </label>
            {conCita && <div className="mt-3"><CamposCita valor={cita} onChange={setCita} teams={teams} /></div>}
            {!conCita && <p className="mt-1 text-[12px] text-ink-3">Sin cita. {forma === "asignada" ? "Solo se notifica al evaluador." : ""}</p>}
          </section>
        )}

        {forma !== "registro_directo" && (
          <section>
            <button type="button" onClick={() => setMasOpciones(!masOpciones)} className="flex items-center gap-1.5 text-sm font-semibold text-ink-2 hover:text-ink">
              <ChevronDown className={cn("h-4 w-4 transition", masOpciones && "rotate-180")} /> Más opciones
            </button>
            {masOpciones && (
              <Campo etiqueta="Instrucciones (opcional)" ayuda="Las ve el evaluador (y el candidato si hay cita): cómo llegar, qué llevar…" className="mt-2">
                <textarea value={instrucciones} onChange={(e) => setInstrucciones(e.target.value)} rows={3} className="rounded-xl border border-border-soft bg-surface px-3.5 py-2.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
              </Campo>
            )}
          </section>
        )}

        {forma === "registro_directo" && !medicaDirecta && (
          <section className="rounded-2xl border border-border-soft bg-surface-2/40 p-4">
            <h3 className="text-sm font-semibold text-ink">Resultado</h3>
            <div className="mt-3">
              <FormularioResultado
                evaluacion={{
                  tipo, conclusionesPosibles: conclusionesDe(tipo), conclusionObligatoria: tipo === "entrevista_humana", conclusion: null,
                  resultadoVersion: 0, forma, evaluador: null, realizadaPor: "", comentarios: "",
                }}
                modo="registrar"
                onEnviar={registrarAhora}
                onListo={() => undefined}
                onCancelar={onClose}
              />
            </div>
          </section>
        )}

        {forma !== "registro_directo" && (
          <LineaNotificar value={notificar.value} onChange={notificar.setValue} hayEntrevistador={forma === "asignada"} hayCliente={Boolean(c.clienteVacante)} clienteId={clienteId} />
        )}

        {tipo === "entrevista_humana" && forma === "asignada" && conCita && (
          <div className="flex flex-wrap items-center gap-3 text-[12px]">
            <span className="text-ink-3">Ver cuerpo del correo:</span>
            <button type="button" className="inline-flex items-center gap-1 font-semibold text-brand hover:underline" onClick={() => setPreview("entrevistador")}><Eye className="h-3.5 w-3.5" /> al evaluador</button>
            <button type="button" className="inline-flex items-center gap-1 font-semibold text-brand hover:underline" onClick={() => setPreview("candidato")}><Eye className="h-3.5 w-3.5" /> al candidato</button>
          </div>
        )}

        {error && <p role="alert" className="text-sm font-semibold text-bad">{error}</p>}
        {(forma !== "registro_directo" || medicaDirecta) && (
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" size="sm" onClick={onClose} disabled={enviando}>Cancelar</Button>
            <Button onClick={agregar} disabled={enviando}>
              {enviando ? <Loader2 className="h-4 w-4 animate-spin" /> : botonFinal === "Programar entrevista" ? <CalendarClock className="h-4 w-4" /> : <ClipboardCheck className="h-4 w-4" />}
              {botonFinal}
            </Button>
          </div>
        )}
        {medicaDirecta && <p className="text-[12px] text-ink-3">Se agrega en espera de consentimiento; cuando el candidato lo otorgue podrás registrar el resultado con «Registrar resultado».</p>}
      </div>

      {preview && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-0 sm:p-4" onClick={() => setPreview(null)}>
          <div className="flex h-[100dvh] w-full flex-col overflow-hidden bg-bg sm:h-[85vh] sm:max-w-2xl sm:rounded-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-border-soft px-4 py-3">
              <span className="text-sm font-semibold">Correo {preview === "entrevistador" ? "al evaluador" : "al candidato"} · {tipoTexto}</span>
              <Button size="sm" variant="outline" onClick={() => setPreview(null)}>Cerrar</Button>
            </div>
            <iframe key={urlPreviewCorreo(preview, datosPreview)} src={urlPreviewCorreo(preview, datosPreview)} title="Vista previa del correo" className="min-h-0 flex-1 bg-white" />
          </div>
        </div>
      )}
    </ModalMarco>
  );
}
