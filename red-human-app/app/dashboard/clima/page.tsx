"use client";

/* Módulo de Clima laboral — Clima v2 (2026-09-27).

   Inicio: «¿Qué quieres saber de tu equipo?» → «Crear encuesta con Red Human» (acción principal),
   «Usar plantilla» y «Crear manualmente». Borrador: editar/reordenar dimensiones y preguntas, «Probar
   encuesta» (respuestas de prueba, nunca mezcladas con las reales, se pueden reiniciar) y «Enviar
   encuesta» (modal: destinatarios por sede/área, fecha y hora de cierre, Anónima/Identificada). Abierta /
   Cerrada: «X de Y respondieron · faltan Z · cierra el …», resultados por Dimensión → Preguntas (motor
   del backend, sin prueba ni externas) y «Analizar resultados con Red Human» a demanda (no hay hallazgos
   automáticos). Flujo estricto de ida: Borrador → Abierta → Cerrada (nunca se reabre).

   REGLA DE ORO: los participantes internos SON los colaboradores del roster; aquí nunca se captura gente.
   Privacidad: en una encuesta anónima no se guarda ni se muestra quién respondió (LFPDPPP). */

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  ArrowLeft, BarChart3, BellRing, CalendarClock, Check, Copy, Eye, EyeOff, FlaskConical, LayoutTemplate, Link2,
  Loader2, MessageSquare, PenLine, RotateCcw, Save, Send, ShieldCheck, Sparkles, Square, Users,
} from "lucide-react";
import { Badge, Button, Card, Eyebrow } from "@/components/ui";
import { PageHeader } from "@/components/dashboard/parts";
import { AvisoLinea, CampoRH, Cargando, KpiRH, ModalMarco, inputRH, type AvisoRH } from "@/components/dashboard/modulos-rh";
import { MenuAcciones, type AccionMenu } from "@/components/dashboard/menu-acciones";
import { EditorCuestionario, cuestionarioParaGuardar } from "@/components/dashboard/clima/editor-cuestionario";
import { FormularioRespuestas, contarContestadas } from "@/components/clima/formulario-respuestas";
import { usePuedeDecidir } from "@/components/sesion";
import { usePolling } from "@/lib/use-polling";
import { cn } from "@/lib/utils";
import {
  abrirMedicionClima, analizarClima, cerrarMedicionClima, crearMedicionClima, editarMedicionClima, fetchDestinatariosClima,
  fetchMedicionClima, fetchMedicionesClima, fetchPlantillasClima, fetchResultadosClima, generarEncuestaClima, recordarClima,
  regenerarLigaClima, reiniciarPruebaClima, responderPruebaClima, usarPlantillaClima,
  type AnalisisClima, type CalculoClima, type DestinatariosClima, type EnvioClima, type MedicionClima, type PlantillaClima,
  type PreguntaClima, type PropuestaClima, type ResultadosClima,
} from "@/lib/api";
import { ETIQUETA_ZONA, conZona, desdeLocal, partesLocales, textoFechaHora } from "@/lib/fechas";

const ESTADO_TONO: Record<string, "neutral" | "good" | "brand"> = { borrador: "neutral", abierta: "good", cerrada: "brand" };
const ESTADO_LABEL: Record<string, string> = { borrador: "Borrador", abierta: "Abierta", cerrada: "Cerrada" };

function fechaLocal(iso: string | null | undefined) {
  return textoFechaHora(iso, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** «X de Y respondieron · faltan Z · cierra el …» con la hora LOCAL de quien mira. */
function textoParticipacion(m: MedicionClima, c: CalculoClima) {
  const p = c.participacion;
  if (!p.invitados) return "Aún no hay colaboradores invitados.";
  const partes = [`${p.respondieron} de ${p.invitados} respondieron`, `faltan ${p.faltan}`];
  if (m.estado === "cerrada") partes.push(`cerró el ${conZona(fechaLocal(m.cerradaEn ?? m.cierraEn))}`);
  else if (m.cierraEn) partes.push(`cierra el ${conZona(fechaLocal(m.cierraEn))}`);
  return partes.join(" · ");
}

function resumenEnvios(envios: EnvioClima[]) {
  const nuevos = envios.filter((e) => !e.yaRespondio);
  const fallidos = nuevos.filter((e) => !(e.correo?.enviado || e.whatsapp?.enviado));
  return {
    texto: `Liga personal enviada a ${nuevos.length - fallidos.length} de ${nuevos.length} colaborador(es).` +
      (fallidos.length ? ` No salió para: ${fallidos.map((f) => f.nombre).join(", ")} (revisa su correo o WhatsApp en el roster).` : ""),
    tono: (fallidos.length ? "warn" : "ok") as "warn" | "ok",
  };
}

export default function Clima() {
  return (
    <Suspense fallback={<div className="mx-auto max-w-7xl px-4 py-16"><Cargando /></div>}>
      <ClimaInner />
    </Suspense>
  );
}

type Vista = { tipo: "inicio" } | { tipo: "nueva"; propuesta: PropuestaClima | null } | { tipo: "medicion"; codigo: string };

function ClimaInner() {
  const puedeDecidir = usePuedeDecidir();
  const params = useSearchParams();
  const [vista, setVista] = useState<Vista>(() => {
    const cod = params.get("medicion");
    return cod ? { tipo: "medicion", codigo: cod } : { tipo: "inicio" };
  });

  if (vista.tipo === "nueva") {
    return (
      <EditorBorrador
        propuesta={vista.propuesta}
        onVolver={() => setVista({ tipo: "inicio" })}
        onGuardada={(m) => setVista({ tipo: "medicion", codigo: m.id })}
      />
    );
  }
  if (vista.tipo === "medicion") {
    return <Medicion codigo={vista.codigo} puedeDecidir={puedeDecidir} onVolver={() => setVista({ tipo: "inicio" })} />;
  }
  return (
    <Inicio
      puedeDecidir={puedeDecidir}
      onAbrir={(codigo) => setVista({ tipo: "medicion", codigo })}
      onNueva={(propuesta) => setVista({ tipo: "nueva", propuesta })}
    />
  );
}

/* ============================================================
   Inicio
   ============================================================ */

function Inicio({ puedeDecidir, onAbrir, onNueva }: {
  puedeDecidir: boolean; onAbrir: (codigo: string) => void; onNueva: (p: PropuestaClima | null) => void;
}) {
  const [mediciones, setMediciones] = useState<MedicionClima[] | null>(null);
  const [prompt, setPrompt] = useState("");
  const [generando, setGenerando] = useState(false);
  const [plantillas, setPlantillas] = useState(false);
  const [aviso, setAviso] = useState<AvisoRH>(null);

  const recargar = useCallback(async () => setMediciones((await fetchMedicionesClima()) ?? []), []);
  useEffect(() => {
    void recargar();
  }, [recargar]);
  usePolling(recargar);

  async function crearConRedHuman() {
    if (!prompt.trim()) return setAviso({ tono: "warn", texto: "Escribe qué quieres saber de tu equipo." });
    setGenerando(true);
    setAviso(null);
    const r = await generarEncuestaClima(prompt);
    setGenerando(false);
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    onNueva(r.data);
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
      <PageHeader title="Clima laboral" subtitle="Encuestas anónimas o identificadas para tus colaboradores, con resultados por dimensión." />

      {puedeDecidir && (
        <Card className="mt-6 p-5 sm:p-7">
          <label htmlFor="prompt-clima" className="font-display text-xl font-bold sm:text-2xl">¿Qué quieres saber de tu equipo?</label>
          <textarea
            id="prompt-clima"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            placeholder="Ej. Quiero saber cómo se sienten con su jefe directo, la carga de trabajo y si ven oportunidades de crecer."
            className="mt-3 w-full rounded-2xl border border-border-soft bg-surface px-4 py-3 text-base outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20"
          />
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button onClick={crearConRedHuman} disabled={generando}>
              {generando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
              {generando ? "Red Human está diseñando tu encuesta…" : "Crear encuesta con Red Human"}
            </Button>
            <Button variant="outline" onClick={() => setPlantillas(true)} disabled={generando}><LayoutTemplate className="h-4 w-4" /> Usar plantilla</Button>
            <Button variant="ghost" onClick={() => onNueva(null)} disabled={generando}><PenLine className="h-4 w-4" /> Crear manualmente</Button>
          </div>
          {aviso && <AvisoLinea aviso={aviso} onCerrar={() => setAviso(null)} />}
        </Card>
      )}

      <h2 className="font-display mt-8 text-lg font-bold">Tus encuestas</h2>
      {mediciones === null ? (
        <Cargando />
      ) : mediciones.length === 0 ? (
        <p className="mt-3 text-sm text-ink-3">Todavía no hay encuestas de clima.</p>
      ) : (
        <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {mediciones.map((m) => (
            <button
              key={m.id}
              onClick={() => onAbrir(m.id)}
              className="card-hover group flex flex-col rounded-2xl border border-border-soft bg-surface p-5 text-left transition-all hover:border-brand/40 hover:shadow-md"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate font-display text-lg font-bold group-hover:text-brand">{m.titulo}</p>
                  <p className="font-mono text-[11px] text-ink-3">{m.id} · {m.preguntas} preguntas · {m.dimensiones.length} dimensiones</p>
                </div>
                <Badge tone={ESTADO_TONO[m.estado] ?? "neutral"} dot>{ESTADO_LABEL[m.estado] ?? m.estado}</Badge>
              </div>
              <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                <span className={cn("inline-flex items-center gap-1 rounded-lg px-2 py-1 font-semibold", m.anonima ? "bg-human-soft text-human" : "bg-surface-2 text-ink-2")}>
                  {m.anonima ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />} {m.anonima ? "Anónima" : "Identificada"}
                </span>
              </div>
              <div className="mt-auto flex items-baseline justify-between pt-4 text-xs text-ink-3">
                <span>{m.estado === "borrador" ? `${m.respuestasPrueba} respuesta(s) de prueba` : `${m.respondieron} de ${m.invitados} respondieron`}</span>
                <span className="text-[11px]">{m.estado === "abierta" && m.cierraEn ? `cierra ${fechaLocal(m.cierraEn)}` : m.creado}</span>
              </div>
            </button>
          ))}
        </div>
      )}

      {plantillas && (
        <ModalUsarPlantilla
          onClose={() => setPlantillas(false)}
          onCreada={(m) => { setPlantillas(false); onAbrir(m.id); }}
        />
      )}
    </div>
  );
}

function ModalUsarPlantilla({ onClose, onCreada }: { onClose: () => void; onCreada: (m: MedicionClima) => void }) {
  const [lista, setLista] = useState<PlantillaClima[] | null>(null);
  const [ocupado, setOcupado] = useState<number | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    fetchPlantillasClima().then((p) => setLista(p ?? []));
  }, []);

  async function usar(id: number) {
    setOcupado(id);
    setError("");
    const r = await usarPlantillaClima(id);
    setOcupado(null);
    if (!r.ok) return setError(r.error);
    onCreada(r.data);
  }

  return (
    <ModalMarco titulo="Usar plantilla" subtitulo="Se crea un borrador con una copia de sus dimensiones y preguntas; la plantilla no cambia." onClose={onClose}>
      {lista === null ? (
        <Cargando />
      ) : lista.length === 0 ? (
        <p className="py-6 text-center text-sm text-ink-3">No hay plantillas de clima. Créalas o súbelas en Configuración → Plantillas de clima.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {lista.map((p) => (
            <li key={p.id} className="flex items-center justify-between gap-3 rounded-2xl border border-border-soft p-4">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">{p.nombre}</p>
                <p className="truncate text-[11px] text-ink-3">{p.preguntas} preguntas · {p.dimensiones.join(", ")}</p>
              </div>
              <Button size="sm" onClick={() => usar(p.id)} disabled={ocupado !== null}>
                {ocupado === p.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <LayoutTemplate className="h-4 w-4" />} Usar
              </Button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="mt-3 text-sm font-semibold text-bad">{error}</p>}
    </ModalMarco>
  );
}

/* ============================================================
   Borrador: crear / editar / probar
   ============================================================ */

function EditorBorrador({ medicion, propuesta, onVolver, onGuardada, onEnviar, onProbar, pruebas }: {
  medicion?: MedicionClima;
  propuesta?: PropuestaClima | null;
  onVolver: () => void;
  onGuardada: (m: MedicionClima) => void;
  onEnviar?: () => void;
  onProbar?: () => void;
  pruebas?: React.ReactNode;
}) {
  const [titulo, setTitulo] = useState(medicion?.titulo ?? propuesta?.titulo ?? "");
  const [descripcion, setDescripcion] = useState(medicion?.descripcion ?? propuesta?.descripcion ?? "");
  const [dimensiones, setDimensiones] = useState<string[]>(medicion?.dimensiones ?? propuesta?.dimensiones ?? ["General"]);
  const [preguntas, setPreguntas] = useState<PreguntaClima[]>(
    medicion?.cuestionario ?? propuesta?.preguntas ?? [{ id: "p1", texto: "", tipo: "escala", dimension: "General", escala_max: 5 }],
  );
  const [sucio, setSucio] = useState(!medicion);
  const [ocupado, setOcupado] = useState(false);
  const [aviso, setAviso] = useState<AvisoRH>(null);

  async function guardar(): Promise<MedicionClima | null> {
    const limpias = cuestionarioParaGuardar(preguntas);
    if (!titulo.trim()) {
      setAviso({ tono: "warn", texto: "Ponle nombre a la encuesta." });
      return null;
    }
    if (!limpias.length) {
      setAviso({ tono: "warn", texto: "Captura al menos una pregunta." });
      return null;
    }
    setOcupado(true);
    const r = medicion
      ? await editarMedicionClima(medicion.id, { titulo, descripcion, dimensiones, preguntas: limpias })
      : await crearMedicionClima({ titulo, descripcion, dimensiones, preguntas: limpias });
    setOcupado(false);
    if (!r.ok) {
      setAviso({ tono: "error", texto: r.error });
      return null;
    }
    setSucio(false);
    setAviso({ tono: "ok", texto: "Borrador guardado." });
    onGuardada(r.data);
    return r.data;
  }

  /** Enviar y Probar trabajan sobre lo guardado: si hay cambios, se guardan primero. */
  async function guardarY(accion?: () => void) {
    if (sucio && !(await guardar())) return;
    accion?.();
  }

  const secundarias: AccionMenu[] = medicion
    ? [
        { etiqueta: "Probar encuesta", icono: <FlaskConical className="h-4 w-4" />, onClick: () => void guardarY(onProbar) },
        { etiqueta: "Guardar cambios", icono: <Save className="h-4 w-4" />, onClick: () => void guardar(), disabled: !sucio },
      ]
    : [];

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-8">
      <button onClick={onVolver} className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-ink-2 transition hover:text-brand">
        <ArrowLeft className="h-4 w-4" /> Clima laboral
      </button>
      <PageHeader title={medicion ? medicion.titulo : "Nueva encuesta"} subtitle={medicion ? `${medicion.id} · Borrador` : "Borrador sin guardar"}>
        <Badge tone="neutral" dot>Borrador</Badge>
        {medicion ? (
          <>
            <Button size="sm" onClick={() => void guardarY(onEnviar)} disabled={ocupado}><Send className="h-4 w-4" /> Enviar encuesta</Button>
            <MenuAcciones acciones={secundarias} />
          </>
        ) : (
          <Button size="sm" onClick={() => void guardar()} disabled={ocupado}>
            {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Guardar borrador
          </Button>
        )}
      </PageHeader>

      {propuesta && !medicion && (
        <AvisoLinea
          aviso={{ tono: "ok", texto: propuesta.ia ? "Propuesta de Red Human: revísala, ajústala y guárdala como borrador." : "Cuestionario base (sin IA disponible): ajústalo y guárdalo como borrador." }}
          onCerrar={() => undefined}
        />
      )}
      {aviso && <AvisoLinea aviso={aviso} onCerrar={() => setAviso(null)} />}

      {/* Índice oculto en borrador: solo hay respuestas de prueba */}
      <Card className="mt-4 flex items-center gap-3 border-dashed p-4 text-sm text-ink-3">
        <BarChart3 className="h-5 w-5 shrink-0" /> Índice de clima: <b className="text-ink-2">Aún no hay respuestas reales</b>
      </Card>

      <Card className="mt-4 p-5">
        <div className="grid gap-3 sm:grid-cols-2">
          <CampoRH label="Nombre de la encuesta"><input value={titulo} onChange={(e) => { setTitulo(e.target.value); setSucio(true); }} className={inputRH} /></CampoRH>
          <CampoRH label="Descripción para quien responde (opcional)"><input value={descripcion} onChange={(e) => { setDescripcion(e.target.value); setSucio(true); }} className={inputRH} /></CampoRH>
        </div>
        <div className="mt-5">
          <EditorCuestionario
            dimensiones={dimensiones}
            preguntas={preguntas}
            onCambio={(d, p) => { setDimensiones(d); setPreguntas(p); setSucio(true); }}
          />
        </div>
        {medicion && sucio && (
          <div className="mt-4 flex justify-end">
            <Button size="sm" variant="secondary" onClick={() => void guardar()} disabled={ocupado}>
              {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Guardar cambios
            </Button>
          </div>
        )}
      </Card>

      {pruebas}
    </div>
  );
}

function ModalProbar({ medicion, onClose, onGuardada }: { medicion: MedicionClima; onClose: () => void; onGuardada: () => void }) {
  const [respuestas, setRespuestas] = useState<Record<string, unknown>>({});
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState("");

  async function enviar() {
    if (!contarContestadas(respuestas)) return setError("Contesta al menos una pregunta.");
    setOcupado(true);
    const r = await responderPruebaClima(medicion.id, respuestas);
    setOcupado(false);
    if (!r.ok) return setError(r.error);
    onGuardada();
  }

  return (
    <ModalMarco titulo="Probar encuesta" subtitulo="Así la verá tu equipo. Esta respuesta es de PRUEBA: nunca se mezcla con las reales." onClose={onClose} ancho="max-w-3xl">
      <FormularioRespuestas preguntas={medicion.cuestionario ?? []} respuestas={respuestas} onCambio={setRespuestas} />
      {error && <p className="mt-3 text-sm font-semibold text-bad">{error}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onClose} disabled={ocupado}>Cancelar</Button>
        <Button size="sm" onClick={enviar} disabled={ocupado}>
          {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />} Guardar respuesta de prueba
        </Button>
      </div>
    </ModalMarco>
  );
}

/* ============================================================
   Medición guardada: borrador o activa/cerrada
   ============================================================ */

function Medicion({ codigo, puedeDecidir, onVolver }: { codigo: string; puedeDecidir: boolean; onVolver: () => void }) {
  const [medicion, setMedicion] = useState<MedicionClima | null>(null);
  const [datos, setDatos] = useState<ResultadosClima | null>(null);
  const [probar, setProbar] = useState(false);
  const [enviar, setEnviar] = useState(false);
  const [aviso, setAviso] = useState<AvisoRH>(null);
  const [version, setVersion] = useState(0);

  const recargar = useCallback(async () => {
    const m = await fetchMedicionClima(codigo);
    if (!m) return;
    setMedicion(m);
    setDatos(await fetchResultadosClima(codigo, m.estado === "borrador"));
  }, [codigo]);
  useEffect(() => {
    void recargar();
  }, [recargar]);
  usePolling(recargar, undefined, medicion?.estado !== "borrador");

  if (!medicion || !datos) return <div className="mx-auto max-w-7xl px-4 py-16"><Cargando /></div>;

  if (medicion.estado === "borrador") {
    return (
      <>
        <EditorBorrador
          key={version}
          medicion={medicion}
          onVolver={onVolver}
          onGuardada={(m) => { setMedicion({ ...medicion, ...m }); void recargar(); }}
          onProbar={() => setProbar(true)}
          onEnviar={() => setEnviar(true)}
          pruebas={
            <ResultadosPrueba
              medicion={medicion}
              calculo={datos.calculo}
              onReiniciar={async () => {
                const r = await reiniciarPruebaClima(codigo);
                setAviso(r.ok ? { tono: "ok", texto: `Se borraron ${r.data.borradas} respuesta(s) de prueba.` } : { tono: "error", texto: r.error });
                void recargar();
              }}
              aviso={aviso}
              onCerrarAviso={() => setAviso(null)}
            />
          }
        />
        {probar && (
          <ModalProbar
            medicion={medicion}
            onClose={() => setProbar(false)}
            onGuardada={() => { setProbar(false); setAviso({ tono: "ok", texto: "Respuesta de prueba guardada: ya aparece en los resultados de prueba." }); void recargar(); }}
          />
        )}
        {enviar && (
          <ModalEnvio
            medicion={medicion}
            onClose={() => setEnviar(false)}
            onEnviada={(texto, tono) => { setEnviar(false); setAviso({ tono, texto }); setVersion((v) => v + 1); void recargar(); }}
          />
        )}
      </>
    );
  }

  return <DetalleActiva medicion={medicion} datos={datos} puedeDecidir={puedeDecidir} onVolver={onVolver} onRecargar={recargar} avisoInicial={aviso} />;
}

function ResultadosPrueba({ medicion, calculo, onReiniciar, aviso, onCerrarAviso }: {
  medicion: MedicionClima; calculo: CalculoClima; onReiniciar: () => void; aviso: AvisoRH; onCerrarAviso: () => void;
}) {
  return (
    <Card className="mt-4 border-warn/30 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Eyebrow><span className="inline-flex items-center gap-1.5"><FlaskConical className="h-3.5 w-3.5" /> Resultados de prueba</span></Eyebrow>
          <p className="mt-1 text-sm text-ink-2">{calculo.respuestasConsideradas} respuesta(s) de prueba · no cuentan en los resultados reales.</p>
        </div>
        {medicion.respuestasPrueba > 0 && (
          <Button size="sm" variant="outline" onClick={onReiniciar}><RotateCcw className="h-4 w-4" /> Reiniciar respuestas de prueba</Button>
        )}
      </div>
      {aviso && <AvisoLinea aviso={aviso} onCerrar={onCerrarAviso} />}
      {calculo.respuestasConsideradas > 0 && (
        <div className="mt-4">
          {calculo.indice.valor !== null && (
            <p className="mb-3 text-sm text-ink-2">Índice de prueba: <b className="font-mono">{calculo.indice.valor}%</b></p>
          )}
          <ResultadosPorDimension calculo={calculo} anonima />
        </div>
      )}
    </Card>
  );
}

/* ---------- Modal de envío ---------- */

// <input type="datetime-local"> en la zona de la ORGANIZACIÓN (lib/fechas.ts), no en la del navegador
function cierrePorDefecto() {
  return `${partesLocales(new Date(Date.now() + 7 * 86_400_000)).fecha}T18:00`;
}

function instanteLocal(valor: string) {
  return valor ? desdeLocal(valor.slice(0, 10), valor.slice(11, 16)) : null;
}

function ModalEnvio({ medicion, onClose, onEnviada }: {
  medicion: MedicionClima; onClose: () => void; onEnviada: (texto: string, tono: "ok" | "warn") => void;
}) {
  const [opciones, setOpciones] = useState<DestinatariosClima | null>(null);
  const [areas, setAreas] = useState<string[]>([]);
  const [sedes, setSedes] = useState<string[]>([]);
  const [filtrados, setFiltrados] = useState<DestinatariosClima["colaboradores"] | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [cierre, setCierre] = useState(cierrePorDefecto());
  const [anonima, setAnonima] = useState(medicion.anonima);
  const [externos, setExternos] = useState(medicion.permiteExternos);
  const [mensaje, setMensaje] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchDestinatariosClima().then((d) => setOpciones(d));
  }, []);
  useEffect(() => {
    fetchDestinatariosClima({ areas, sedes }).then((d) => {
      const lista = d?.colaboradores ?? [];
      setFiltrados(lista);
      setSel(lista.map((c) => c.id));
    });
  }, [areas, sedes]);

  const alternar = (lista: string[], set: (x: string[]) => void, v: string) => set(lista.includes(v) ? lista.filter((x) => x !== v) : [...lista, v]);

  async function enviar() {
    if (!sel.length) return setError("Elige al menos un destinatario.");
    const fecha = instanteLocal(cierre);
    if (!fecha || fecha <= new Date()) return setError("Elige una fecha y hora de cierre en el futuro.");
    setOcupado(true);
    setError("");
    const r = await abrirMedicionClima(medicion.id, {
      colaboradorIds: sel, areas, sedes, cierraEn: fecha.toISOString(), anonima, permiteExternos: externos, mensaje,
    });
    setOcupado(false);
    if (!r.ok) return setError(r.error);
    const res = resumenEnvios(r.data.invitados);
    onEnviada(`Encuesta abierta. ${res.texto}`, res.tono);
  }

  return (
    <ModalMarco titulo="Enviar encuesta" subtitulo="Cada destinatario recibe su liga personal por correo y/o WhatsApp. Al enviar, la encuesta queda Abierta." onClose={onClose} ancho="max-w-3xl">
      {opciones === null ? <Cargando /> : (
        <>
          <p className="text-xs font-medium text-ink-2">Destinatarios</p>
          {(opciones.sedes.length > 0 || opciones.areas.length > 0) && (
            <div className="mt-2 flex flex-col gap-2">
              {opciones.sedes.length > 0 && (
                <div className="scroll-x flex gap-1.5">
                  <span className="shrink-0 self-center text-[11px] text-ink-3">Sede:</span>
                  {opciones.sedes.map((s) => (
                    <button key={s} onClick={() => alternar(sedes, setSedes, s)} className={cn("shrink-0 rounded-full border px-3 py-1 text-xs", sedes.includes(s) ? "border-brand bg-brand-soft text-ink" : "border-border-soft text-ink-2")}>{s}</button>
                  ))}
                </div>
              )}
              {opciones.areas.length > 0 && (
                <div className="scroll-x flex gap-1.5">
                  <span className="shrink-0 self-center text-[11px] text-ink-3">Área:</span>
                  {opciones.areas.map((a) => (
                    <button key={a} onClick={() => alternar(areas, setAreas, a)} className={cn("shrink-0 rounded-full border px-3 py-1 text-xs", areas.includes(a) ? "border-brand bg-brand-soft text-ink" : "border-border-soft text-ink-2")}>{a}</button>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="mt-2 flex items-center justify-between text-xs text-ink-3">
            <span>{sel.length} de {filtrados?.length ?? 0} seleccionado(s)</span>
            {filtrados && filtrados.length > 0 && (
              <button className="font-semibold text-brand hover:underline" onClick={() => setSel(sel.length === filtrados.length ? [] : filtrados.map((c) => c.id))}>
                {sel.length === filtrados.length ? "Quitar todos" : "Seleccionar todos"}
              </button>
            )}
          </div>
          <div className="mt-2 max-h-[30vh] overflow-y-auto rounded-2xl border border-border-soft">
            {filtrados === null ? <Cargando /> : filtrados.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-ink-3">No hay colaboradores activos con esos filtros.</p>
            ) : (
              <ul className="divide-y divide-border-faint">
                {filtrados.map((c) => (
                  <li key={c.id}>
                    <label className="flex cursor-pointer items-center gap-3 px-4 py-2.5 hover:bg-surface-2/60">
                      <input type="checkbox" checked={sel.includes(c.id)} onChange={() => alternar(sel, setSel, c.id)} className="h-4 w-4 rounded border-border-soft text-brand" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold">{c.nombre}</p>
                        <p className="truncate text-[11px] text-ink-3">{[c.area, c.sede, c.puesto].filter(Boolean).join(" · ") || "Sin área"}</p>
                      </div>
                      {!c.tieneCorreo && !c.tieneWhatsapp && <span className="shrink-0 text-[11px] font-semibold text-warn">Sin contacto</span>}
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <CampoRH label={`Cierra el (${ETIQUETA_ZONA})`} ayuda="Podrás ampliarla mientras esté abierta.">
              <input type="datetime-local" value={cierre} onChange={(e) => setCierre(e.target.value)} className={inputRH} />
            </CampoRH>
            <CampoRH label="Nota para el mensaje (opcional)">
              <input value={mensaje} onChange={(e) => setMensaje(e.target.value)} placeholder="Ej. Nos ayuda mucho tu opinión." className={inputRH} />
            </CampoRH>
          </div>

          <p className="mt-4 text-xs font-medium text-ink-2">Modalidad</p>
          <div className="mt-2 inline-flex rounded-xl border border-border-soft p-1" role="radiogroup" aria-label="Modalidad">
            {[{ v: true, t: "Anónima", i: <EyeOff className="h-4 w-4" /> }, { v: false, t: "Identificada", i: <Eye className="h-4 w-4" /> }].map((o) => (
              <button
                key={o.t}
                role="radio"
                aria-checked={anonima === o.v}
                onClick={() => setAnonima(o.v)}
                className={cn("inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-semibold transition", anonima === o.v ? "bg-brand text-white" : "text-ink-2 hover:bg-surface-2")}
              >
                {o.i} {o.t}
              </button>
            ))}
          </div>
          <p className="mt-1.5 text-[12px] text-ink-3">
            {anonima ? "No se guarda quién respondió; solo sabemos quién ya contestó para no repetir recordatorios." : "Cada respuesta queda ligada a la persona; se le avisa antes de contestar."} La modalidad queda fija al enviar.
          </p>
          <label className="mt-3 flex cursor-pointer items-start gap-3 text-[13px] text-ink-2">
            <input type="checkbox" checked={externos} onChange={(e) => setExternos(e.target.checked)} className="mt-0.5 h-4 w-4 rounded border-border-soft text-brand" />
            Aceptar respuestas externas por la liga compartida (no suman a la participación).
          </label>
        </>
      )}
      {error && <p className="mt-3 text-sm font-semibold text-bad">{error}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onClose} disabled={ocupado}>Cancelar</Button>
        <Button size="sm" onClick={enviar} disabled={ocupado || !sel.length}>
          {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} Enviar a {sel.length}
        </Button>
      </div>
    </ModalMarco>
  );
}

/* ---------- Abierta / Cerrada ---------- */

function DetalleActiva({ medicion, datos, puedeDecidir, onVolver, onRecargar, avisoInicial }: {
  medicion: MedicionClima; datos: ResultadosClima; puedeDecidir: boolean; onVolver: () => void; onRecargar: () => Promise<void>; avisoInicial: AvisoRH;
}) {
  const [aviso, setAviso] = useState<AvisoRH>(avisoInicial);
  const [ocupado, setOcupado] = useState("");
  const [modal, setModal] = useState<"" | "fecha" | "cerrar">("");
  const [analisis, setAnalisis] = useState<AnalisisClima | null>(datos.analisis);
  const c = datos.calculo;
  const abierta = medicion.estado === "abierta";

  useEffect(() => {
    setAnalisis(datos.analisis);
  }, [datos.analisis]);

  async function analizar() {
    setOcupado("analizar");
    const r = await analizarClima(medicion.id);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setAnalisis(r.data);
  }

  async function copiar(texto: string) {
    try {
      await navigator.clipboard.writeText(texto);
      setAviso({ tono: "ok", texto: "Liga externa copiada." });
    } catch {
      setAviso({ tono: "warn", texto: `Copia la liga a mano: ${texto}` });
    }
  }

  const secundarias: AccionMenu[] = [];
  if (puedeDecidir && abierta) {
    secundarias.push(
      {
        etiqueta: "Recordar a quien no ha respondido", icono: <BellRing className="h-4 w-4" />, disabled: !c.participacion.faltan,
        onClick: async () => {
          const r = await recordarClima(medicion.id);
          if (!r.ok) return setAviso({ tono: "error", texto: r.error });
          setAviso({ tono: "ok", texto: `Recordatorio enviado a ${r.data.recordados} persona(s).` });
        },
      },
      { etiqueta: "Cambiar fecha de cierre", icono: <CalendarClock className="h-4 w-4" />, onClick: () => setModal("fecha") },
    );
    if (medicion.permiteExternos) {
      secundarias.push(
        { etiqueta: "Copiar liga externa", icono: <Copy className="h-4 w-4" />, onClick: () => void copiar(medicion.liga) },
        {
          etiqueta: "Generar liga externa nueva", icono: <Link2 className="h-4 w-4" />,
          onClick: async () => {
            const r = await regenerarLigaClima(medicion.id);
            if (!r.ok) return setAviso({ tono: "error", texto: r.error });
            setAviso({ tono: "warn", texto: "Liga externa nueva generada: la anterior dejó de funcionar. Las ligas personales no cambian." });
            void onRecargar();
          },
        },
      );
    }
    secundarias.push({ etiqueta: "Cerrar encuesta ahora", icono: <Square className="h-4 w-4" />, peligrosa: true, onClick: () => setModal("cerrar") });
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
      <button onClick={onVolver} className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-ink-2 transition hover:text-brand">
        <ArrowLeft className="h-4 w-4" /> Clima laboral
      </button>
      <PageHeader title={medicion.titulo} subtitle={textoParticipacion(medicion, c)}>
        <Badge tone={ESTADO_TONO[medicion.estado]} dot>{ESTADO_LABEL[medicion.estado]}</Badge>
        <Badge tone={medicion.anonima ? "good" : "neutral"}>{medicion.anonima ? "Anónima" : "Identificada"}</Badge>
        {puedeDecidir && (
          <Button size="sm" onClick={analizar} disabled={ocupado === "analizar" || !c.respuestasConsideradas}>
            {ocupado === "analizar" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />} Analizar resultados con Red Human
          </Button>
        )}
        {secundarias.length > 0 && <MenuAcciones acciones={secundarias} />}
      </PageHeader>

      {aviso && <AvisoLinea aviso={aviso} onCerrar={() => setAviso(null)} />}

      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiRH
          etiqueta="Índice de clima"
          valor={c.indice.valor === null ? "—" : `${c.indice.valor}%`}
          pie={c.indice.valor === null ? c.indice.etiqueta : `promedio de ${c.indice.dimensionesConsideradas} dimensión(es)`}
          icono={<BarChart3 className="h-4 w-4" />}
        />
        <KpiRH
          etiqueta="Participación"
          valor={c.participacion.porcentaje === null ? "—" : `${Math.round(c.participacion.porcentaje)}%`}
          pie={`${c.participacion.respondieron} de ${c.participacion.invitados} invitados`}
          icono={<Users className="h-4 w-4" />}
        />
        <KpiRH etiqueta="Respuestas reales" valor={String(c.respuestasConsideradas)} pie="sin prueba ni externas" icono={<MessageSquare className="h-4 w-4" />} />
        <KpiRH etiqueta="Externas" valor={String(c.externas)} pie="no suman a participación" icono={<Link2 className="h-4 w-4" />} />
      </div>

      {analisis && <TarjetaAnalisis a={analisis} />}

      <div className="mt-4">
        {c.respuestasConsideradas === 0 ? (
          <Card className="p-8 text-center text-sm text-ink-3">Aún no hay respuestas reales. Los resultados aparecerán aquí en cuanto el equipo conteste.</Card>
        ) : (
          <ResultadosPorDimension calculo={c} anonima={medicion.anonima} />
        )}
      </div>

      {modal === "fecha" && (
        <ModalFechaCierre
          medicion={medicion}
          onClose={() => setModal("")}
          onListo={() => { setModal(""); setAviso({ tono: "ok", texto: "Fecha de cierre actualizada." }); void onRecargar(); }}
        />
      )}
      {modal === "cerrar" && (
        <ModalMarco titulo="Cerrar encuesta" subtitulo="Dejará de recibir respuestas y los resultados quedan congelados. Una encuesta cerrada no se vuelve a abrir." onClose={() => setModal("")}>
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setModal("")}>Cancelar</Button>
            <Button
              size="sm"
              onClick={async () => {
                const r = await cerrarMedicionClima(medicion.id);
                setModal("");
                if (!r.ok) return setAviso({ tono: "error", texto: r.error });
                setAviso({ tono: "ok", texto: "Encuesta cerrada." });
                void onRecargar();
              }}
            >
              <Square className="h-4 w-4" /> Cerrar encuesta
            </Button>
          </div>
        </ModalMarco>
      )}
    </div>
  );
}

function ModalFechaCierre({ medicion, onClose, onListo }: { medicion: MedicionClima; onClose: () => void; onListo: () => void }) {
  const inicial = useMemo(() => {
    if (!medicion.cierraEn) return cierrePorDefecto();
    const p = partesLocales(medicion.cierraEn);
    return `${p.fecha}T${p.hora}`;
  }, [medicion.cierraEn]);
  const [valor, setValor] = useState(inicial);
  const [error, setError] = useState("");

  async function guardar() {
    const fecha = instanteLocal(valor);
    if (!fecha || fecha <= new Date()) return setError("La nueva fecha de cierre debe estar en el futuro.");
    const r = await editarMedicionClima(medicion.id, { cierraEn: fecha.toISOString() });
    if (!r.ok) return setError(r.error);
    onListo();
  }

  return (
    <ModalMarco titulo="Cambiar fecha de cierre" onClose={onClose}>
      <CampoRH label={`Cierra el (${ETIQUETA_ZONA})`}><input type="datetime-local" value={valor} onChange={(e) => setValor(e.target.value)} className={inputRH} /></CampoRH>
      {error && <p className="mt-3 text-sm font-semibold text-bad">{error}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onClose}>Cancelar</Button>
        <Button size="sm" onClick={guardar}><Check className="h-4 w-4" /> Guardar</Button>
      </div>
    </ModalMarco>
  );
}

function TarjetaAnalisis({ a }: { a: AnalisisClima }) {
  const tono = a.estado === "Favorable" ? "good" : a.estado === "Requiere atención" ? "warn" : "neutral";
  const bloques: [string, string[], string][] = [
    ["Fortalezas", a.fortalezas, "text-good"],
    ["Focos de atención", a.focosAtencion, "text-warn"],
    ["Puntos por validar", a.puntosPorValidar, "text-ink-2"],
    ["Acciones sugeridas", a.accionesSugeridas, "text-brand"],
  ];
  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Eyebrow><span className="inline-flex items-center gap-1.5"><Sparkles className="h-3.5 w-3.5" /> Análisis de Red Human</span></Eyebrow>
        <Badge tone={tono as "good" | "warn" | "neutral"} dot>{a.estado}</Badge>
        {a.alcance === "preliminar" && <Badge tone="neutral">Preliminar · encuesta abierta</Badge>}
      </div>
      <p className="mt-1 text-[11px] text-ink-3">
        {fechaLocal(a.fecha)} · {a.respuestasConsideradas} respuesta(s) consideradas · pedido por {a.solicitadoPor}{a.ia ? "" : " · sin IA disponible (cálculo directo)"}
      </p>
      <p className="mt-3 text-sm leading-relaxed text-ink-2">{a.resumen}</p>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        {bloques.map(([t, items, color]) => (
          <div key={t}>
            <p className={cn("text-[13px] font-semibold", color)}>{t}</p>
            <ul className="mt-1.5 space-y-1.5">
              {items.map((x, i) => <li key={i} className="text-[13px] leading-relaxed text-ink-2">• {x}</li>)}
            </ul>
          </div>
        ))}
      </div>
      <p className="mt-4 flex items-start gap-1.5 text-[11px] text-ink-3">
        <ShieldCheck className="mt-0.5 h-3 w-3 shrink-0 text-human" /> Recomendación de Red Human: las decisiones las toma una persona de RH.
      </p>
    </Card>
  );
}

/* ---------- Resultados: Dimensiones → Preguntas ---------- */

function ResultadosPorDimension({ calculo, anonima }: { calculo: CalculoClima; anonima: boolean }) {
  return (
    <div className="flex flex-col gap-4">
      {calculo.dimensiones.map((d) => (
        <Card key={d.nombre} className="p-5">
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="font-display text-lg font-bold">{d.nombre}</h3>
            <span className="shrink-0 font-mono text-sm font-bold tabular">
              {d.favorable === null ? <span className="text-ink-3">Sin resultado</span> : `${d.favorable}% favorable`}
            </span>
          </div>
          <div className="mt-4 space-y-4">
            {d.preguntas.map((p) => (
              <div key={p.id}>
                <div className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="min-w-0 text-ink-2">{p.texto}</span>
                  <span className="shrink-0 text-[11px] text-ink-3">{p.respuestas} resp.</span>
                </div>
                {p.tipo === "escala" && (
                  p.favorable === null || p.favorable === undefined ? (
                    <p className="mt-1 text-[11px] text-ink-3">Sin respuestas todavía (no cuenta en la dimensión).</p>
                  ) : (
                    <>
                      <div className="mt-1.5 flex items-center gap-2">
                        <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface-2">
                          <div className={cn("h-full rounded-full", p.favorable >= 80 ? "bg-good" : p.favorable >= 60 ? "bg-brand" : "bg-warn")} style={{ width: `${p.favorable}%` }} />
                        </div>
                        <span className="w-24 shrink-0 text-right font-mono text-xs font-bold tabular">{p.favorable}% fav.</span>
                      </div>
                      <p className="mt-1 text-[10px] text-ink-3">
                        {Object.entries(p.distribucion ?? {}).map(([k, n]) => `${k}: ${n}`).join(" · ")}
                      </p>
                    </>
                  )
                )}
                {p.tipo === "opcion" && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {Object.entries(p.distribucion ?? {}).map(([op, n]) => (
                      <span key={op} className="rounded-lg bg-surface-2 px-2.5 py-1 text-xs text-ink-2">{op}: <b className="font-mono tabular">{n}</b></span>
                    ))}
                  </div>
                )}
                {p.tipo === "abierta" && (
                  (p.comentarios ?? []).length === 0 ? (
                    <p className="mt-1 text-[11px] text-ink-3">Sin comentarios todavía.</p>
                  ) : (
                    <ul className="mt-2 max-h-64 space-y-2 overflow-y-auto">
                      {(p.comentarios ?? []).map((t, i) => (
                        <li key={i} className="rounded-xl bg-surface-2 px-3 py-2 text-[13px] leading-relaxed text-ink-2">«{t}»</li>
                      ))}
                    </ul>
                  )
                )}
              </div>
            ))}
          </div>
          {d.preguntas.some((p) => p.tipo === "abierta") && (
            <p className="mt-3 flex items-start gap-1.5 text-[11px] text-ink-3">
              <ShieldCheck className="mt-0.5 h-3 w-3 shrink-0 text-human" />
              {anonima ? "Comentarios anónimos: no se guarda quién los escribió." : "Encuesta identificada."} Los comentarios no se convierten en puntaje.
            </p>
          )}
        </Card>
      ))}
    </div>
  );
}
