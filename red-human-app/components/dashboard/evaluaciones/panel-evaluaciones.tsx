"use client";

/* Evaluaciones del candidato — tarjetas compactas (Evaluaciones unificadas — Fase 1, 2026-09-29, especificación
   sección 6). Una evaluación = una tarjeta: tipo y nombre · responsable · estado · conclusión (UNA etiqueta) · cita ·
   condiciones (consentimiento, «Nuevo resultado») · botón de siguiente acción + menú «⋯». El detalle completo
   (cita, comentarios, adjuntos, autor vs. captura, historial) vive en «Ver resultado». Un solo botón «Agregar
   evaluación». Registrar un resultado NUNCA mueve al candidato de etapa ni lo envía a Contratación. */

import { useCallback, useEffect, useState } from "react";
import {
  Ban, BellRing, CalendarClock, CheckCircle2, ClipboardCheck, Copy, Eye, FileText, Link2, Loader2, Lock, Pencil, RefreshCw, RotateCw,
  Send, SkipForward, Sparkles, UserX,
} from "lucide-react";
import { Badge, Button, Card, Eyebrow } from "@/components/ui";
import { MenuAcciones } from "@/components/dashboard/menu-acciones";
import { ModalMarco } from "@/components/dashboard/modulos-rh";
import { LineaNotificar, useNotificarAccion } from "@/components/dashboard/linea-notificar";
import { FormularioResultado, type ModoResultado } from "@/components/dashboard/evaluaciones/formulario-resultado";
import { ModalAgregarEvaluacion, type PresetEvaluacion } from "@/components/dashboard/evaluaciones/agregar-evaluacion";
import {
  CamposCita, Campo, SelectorEvaluador, citaEntrada, citaVacia, evaluadorEntrada, inputEv, useCatalogoEvaluadores, useTeamsConectado,
  validarCita, validarEvaluador, type EstadoCita, type EstadoEvaluador,
} from "@/components/dashboard/evaluaciones/campos-evaluacion";
import {
  avanzarEvaluacionIntegrada, cancelarEvaluacion, enviarEvaluacionProveedor, enviarLigaConsentimientoMedico, fetchEvaluacion, fetchEvaluaciones,
  lineasResultados, marcarEvaluacionNoRealizada, marcarEvaluacionRealizada, modificarEvaluacion, recordatorioEvaluacion, reenviarLigaEvaluacion,
  registrarResultadoEvaluacion, reprogramarEvaluacion, sincronizarEvaluacion, urlAdjuntoEvaluacion,
  type Evaluacion, type EventoEvaluacion, type RespuestaEvaluacion, type Resultado,
} from "@/lib/api";
import type { Candidato } from "@/lib/data";
import { partesLocales, textoCita, textoFechaHora } from "@/lib/fechas";
import { cn } from "@/lib/utils";

const PASOS: Record<string, string> = { asignada: "Asignada", enviada: "Enviada", iniciada: "Iniciada", completada: "Completada", resultado_recibido: "Resultado recibido" };
const VIA: Record<string, string> = { sistema: "en el sistema", liga_evaluador: "vía liga del evaluador", proveedor: "vía proveedor", migracion: "migración" };

type Aviso = { tono: "ok" | "warn" | "error"; texto: string } | null;

function tonoEstado(e: Evaluacion): "good" | "warn" | "bad" | "neutral" | "brand" {
  if (e.estado === "con_resultado") return "brand";
  if (e.estado === "realizada_sin_resultado") return "warn";
  if (e.estado === "no_realizada") return "bad";
  return "neutral";
}
function tonoConclusion(v: string | null): "good" | "warn" | "bad" | "brand" {
  if (v === "avanzar" || v === "favorable" || v === "apto") return "good";
  if (v === "no_avanzar" || v === "desfavorable" || v === "no_apto") return "bad";
  if (v === "con_observaciones" || v === "apto_con_restricciones" || v === "requiere_otra_entrevista") return "warn";
  return "brand";
}

function resumenRespuesta(r: RespuestaEvaluacion, base: string): Aviso {
  const lineas = lineasResultados(r.resultados);
  const fallidos = lineas.filter((l) => !l.ok);
  // las `advertencias` repiten las líneas fallidas: solo se agrega lo que no está en ellas (aviso de Teams)
  const extra = r.avisoTeams ? [r.avisoTeams] : [];
  return {
    tono: fallidos.length || extra.length ? "warn" : "ok",
    texto: [base, lineas.length ? lineas.map((l) => `${l.ok ? "✓" : "✗"} ${l.texto}`).join(" · ") : "", ...extra].filter(Boolean).join(" "),
  };
}

/* ============================================================ Panel ============================================================ */

export function PanelEvaluaciones({ c, live, version = 0, onCambio, titulo = "Evaluaciones" }: {
  c: Candidato;
  live: boolean;
  version?: number;
  onCambio?: (c: Candidato) => void;
  titulo?: string;
}) {
  const [lista, setLista] = useState<Evaluacion[] | null>(null);
  const [aviso, setAviso] = useState<Aviso>(null);
  const [ocupado, setOcupado] = useState("");
  const [agregar, setAgregar] = useState<PresetEvaluacion | null>(null);
  const [resultado, setResultado] = useState<{ e: Evaluacion; modo: ModoResultado } | null>(null);
  const [ver, setVer] = useState<string | null>(null);
  const [motivo, setMotivo] = useState<{ e: Evaluacion; accion: "no_realizada" | "cancelar" } | null>(null);
  const [reprogramar, setReprogramar] = useState<Evaluacion | null>(null);
  const [modificar, setModificar] = useState<Evaluacion | null>(null);

  const cargar = useCallback(async () => setLista((await fetchEvaluaciones(c.id)) ?? []), [c.id]);
  useEffect(() => {
    void cargar();
  }, [cargar, version]);

  function listo(r: RespuestaEvaluacion, texto: string) {
    setAviso(resumenRespuesta(r, texto));
    if (r.candidato && onCambio) onCambio(r.candidato);
    void cargar();
  }

  async function accion(e: Evaluacion, fn: () => Promise<Resultado<RespuestaEvaluacion>>, texto: string) {
    setOcupado(e.codigo);
    setAviso(null);
    const r = await fn();
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    listo(r.data, r.data.sincronizacion === "en_curso" ? "El candidato aún no termina sus pruebas." : texto);
  }

  function copiar(texto: string, que: string) {
    void navigator.clipboard?.writeText(texto);
    setAviso({ tono: "ok", texto: `${que} copiada.` });
  }

  function ejecutar(e: Evaluacion, a: string) {
    switch (a) {
      case "registrar_resultado": return setResultado({ e, modo: "registrar" });
      case "complementar": return setResultado({ e, modo: "corregir" });
      case "ver_resultado": return setVer(e.codigo);
      case "marcar_realizada": return accion(e, () => marcarEvaluacionRealizada(e.codigo), `«${e.nombre}»: Realizada · Resultado pendiente.`);
      case "reprogramar": return setReprogramar(e);
      case "modificar": return setModificar(e);
      case "no_realizada": return setMotivo({ e, accion: "no_realizada" });
      case "cancelar": return setMotivo({ e, accion: "cancelar" });
      case "reenviar_liga": return accion(e, () => reenviarLigaEvaluacion(e.codigo), "Liga reenviada.");
      case "enviar_proveedor": return accion(e, () => enviarEvaluacionProveedor(e.codigo), `«${e.nombre}» enviada al proveedor.`);
      case "sincronizar": return accion(e, () => sincronizarEvaluacion(e.codigo), "Resultado recibido del proveedor.");
      case "avanzar_paso": return accion(e, () => avanzarEvaluacionIntegrada(e.codigo), "Paso registrado.");
      case "programar_otra":
        return setAgregar({
          tipo: "entrevista_humana", titulo: "Programar otra entrevista",
          evaluador: e.evaluador ? {
            tipo: e.evaluador.tipo === "interno" ? "interno" : "externo", usuarioId: e.evaluador.usuarioId,
            contacto: e.evaluador.contactoId ?? (e.evaluador.tipo === "externo" ? "nuevo" : ""), nombre: e.evaluador.nombre, correo: e.evaluador.correo, whatsapp: e.evaluador.whatsapp,
          } : undefined,
        });
      case "enviar_consentimiento":
        return (async () => {
          setOcupado(e.codigo);
          const r = await enviarLigaConsentimientoMedico(e.codigo);
          setOcupado("");
          if (!r.ok) return setAviso({ tono: "error", texto: r.error });
          setAviso(resumenRespuesta({ evaluacion: e, resultados: r.data.resultados, advertencias: r.data.advertencias }, "Liga de consentimiento enviada."));
        })();
    }
  }

  const ETIQUETA: Record<string, { texto: string; icono: React.ReactNode }> = {
    registrar_resultado: { texto: "Registrar resultado", icono: <ClipboardCheck className="h-4 w-4" /> },
    marcar_realizada: { texto: "Marcar como realizada", icono: <CheckCircle2 className="h-4 w-4" /> },
    ver_resultado: { texto: "Ver resultado", icono: <Eye className="h-4 w-4" /> },
    complementar: { texto: "Complementar o corregir", icono: <Pencil className="h-4 w-4" /> },
    reprogramar: { texto: "Reprogramar", icono: <CalendarClock className="h-4 w-4" /> },
    modificar: { texto: "Modificar datos e instrucciones", icono: <Pencil className="h-4 w-4" /> },
    no_realizada: { texto: "Marcar como no realizada", icono: <UserX className="h-4 w-4" /> },
    cancelar: { texto: "Cancelar evaluación…", icono: <Ban className="h-4 w-4" /> },
    reenviar_liga: { texto: "Reenviar liga", icono: <Send className="h-4 w-4" /> },
    enviar_consentimiento: { texto: "Enviar liga de consentimiento", icono: <Send className="h-4 w-4" /> },
    programar_otra: { texto: "Programar otra entrevista", icono: <CalendarClock className="h-4 w-4" /> },
    enviar_proveedor: { texto: "Enviar al proveedor", icono: <Send className="h-4 w-4" /> },
    sincronizar: { texto: "Consultar resultado del proveedor", icono: <RefreshCw className="h-4 w-4" /> },
    avanzar_paso: { texto: "Simular siguiente paso del proveedor", icono: <SkipForward className="h-4 w-4" /> },
  };

  function menuDe(e: Evaluacion) {
    const items: { etiqueta: string; icono: React.ReactNode; onClick: () => void; peligrosa?: boolean; disabled?: boolean }[] = [];
    for (const a of e.acciones.menu) {
      if (a === "recordatorio") {
        const alCandidato = Boolean(e.cita) || e.forma === "liga_otro_sistema";
        const alEvaluador = e.forma === "asignada";
        if (alCandidato) items.push({ etiqueta: "Enviar recordatorio al candidato", icono: <BellRing className="h-4 w-4" />, onClick: () => accion(e, () => recordatorioEvaluacion(e.codigo, "candidato"), "Recordatorio enviado al candidato.") });
        if (alEvaluador) items.push({ etiqueta: "Enviar recordatorio al evaluador", icono: <BellRing className="h-4 w-4" />, onClick: () => accion(e, () => recordatorioEvaluacion(e.codigo, "evaluador"), "Recordatorio enviado al evaluador.") });
        if (alCandidato && alEvaluador) items.push({ etiqueta: "Enviar recordatorio a ambos", icono: <BellRing className="h-4 w-4" />, onClick: () => accion(e, () => recordatorioEvaluacion(e.codigo, "ambos"), "Recordatorio enviado a ambos.") });
        continue;
      }
      const et = ETIQUETA[a];
      if (et) items.push({ etiqueta: et.texto, icono: et.icono, onClick: () => ejecutar(e, a), peligrosa: a === "cancelar" });
    }
    if (e.ligaEvaluador && e.estado !== "cancelada") items.push({ etiqueta: "Copiar liga del evaluador", icono: <Link2 className="h-4 w-4" />, onClick: () => copiar(e.ligaEvaluador!, "Liga del evaluador") });
    if (e.ligaConsentimiento) items.push({ etiqueta: "Copiar liga de consentimiento", icono: <Copy className="h-4 w-4" />, onClick: () => copiar(e.ligaConsentimiento!, "Liga de consentimiento") });
    if (e.urlCandidatoProveedor) items.push({ etiqueta: "Copiar liga del candidato (proveedor)", icono: <Copy className="h-4 w-4" />, onClick: () => copiar(e.urlCandidatoProveedor!, "Liga del candidato") });
    return items;
  }

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <Eyebrow>{titulo}</Eyebrow>
          <p className="mt-1 text-[12px] text-ink-3">Entrevista humana, médica, psicométrica, socioeconómica, técnica, referencias u otra. Ningún resultado mueve la etapa.</p>
        </div>
        {live && c.activa !== false && (
          <Button size="sm" onClick={() => setAgregar({})}><ClipboardCheck className="h-4 w-4" /> Agregar evaluación</Button>
        )}
      </div>
      {aviso && (
        <p className={cn("mt-3 rounded-xl border px-3.5 py-2.5 text-[13px] leading-relaxed", aviso.tono === "ok" ? "border-good/30 bg-good-soft/40 text-good" : aviso.tono === "warn" ? "border-warn/30 bg-warn-soft/40 text-warn" : "border-bad/30 bg-bad-soft/40 font-semibold text-bad")}>
          {aviso.texto}
        </p>
      )}
      <div className="mt-3">
        {lista === null ? (
          <Loader2 className="h-5 w-5 animate-spin text-ink-3" />
        ) : lista.length === 0 ? (
          <p className="text-sm text-ink-3">Sin evaluaciones todavía.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {lista.map((e) => (
              <TarjetaEvaluacion
                key={e.codigo}
                e={e}
                live={live}
                ocupado={ocupado === e.codigo}
                onAccion={(a) => ejecutar(e, a)}
                etiquetas={ETIQUETA}
                menu={menuDe(e)}
              />
            ))}
          </ul>
        )}
      </div>

      {agregar && (
        <ModalAgregarEvaluacion c={c} preset={agregar} onClose={() => setAgregar(null)} onListo={(r, texto) => { setAgregar(null); listo(r, texto); }} />
      )}
      {resultado && (
        <ModalResultado
          e={resultado.e}
          modo={resultado.modo}
          onClose={() => setResultado(null)}
          onListo={(texto) => { setResultado(null); setAviso({ tono: "ok", texto }); void cargar(); }}
          onConflicto={async () => {
            const d = await fetchEvaluacion(resultado.e.codigo);
            if (d) setResultado({ e: d.evaluacion, modo: resultado.modo });
          }}
        />
      )}
      {ver && (
        <ModalVerResultado
          codigo={ver}
          live={live}
          onClose={() => { setVer(null); void cargar(); }}
          onComplementar={(e) => { setVer(null); setResultado({ e, modo: "corregir" }); }}
        />
      )}
      {motivo && (
        <ModalMotivo
          e={motivo.e}
          accion={motivo.accion}
          onClose={() => setMotivo(null)}
          onListo={(r, texto) => { setMotivo(null); listo(r, texto); }}
        />
      )}
      {reprogramar && <ModalReprogramar e={reprogramar} onClose={() => setReprogramar(null)} onListo={(r) => { setReprogramar(null); listo(r, "Evaluación reprogramada."); }} />}
      {modificar && <ModalModificar c={c} e={modificar} onClose={() => setModificar(null)} onListo={(r) => { setModificar(null); listo(r, "Cambios guardados."); }} />}
    </Card>
  );
}

/* ============================================================ Tarjeta compacta ============================================================ */

function TarjetaEvaluacion({ e, live, ocupado, onAccion, etiquetas, menu }: {
  e: Evaluacion;
  live: boolean;
  ocupado: boolean;
  onAccion: (a: string) => void;
  etiquetas: Record<string, { texto: string; icono: React.ReactNode }>;
  menu: { etiqueta: string; icono: React.ReactNode; onClick: () => void; peligrosa?: boolean }[];
}) {
  const principal = e.acciones.principal;
  const secundaria = e.acciones.secundaria;
  // «Ver resultado» es de solo lectura: también sin permisos de decisión
  const puedePrincipal = principal && (live || principal === "ver_resultado");
  return (
    <li className={cn("rounded-xl border bg-surface px-3.5 py-3", e.nuevoResultado ? "border-brand/40" : "border-border-soft")}>
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">
            {e.nombre}
            {e.nombre !== e.tipoTexto && <span className="font-normal text-ink-3"> · {e.tipoTexto}</span>}
          </p>
          <p className="truncate text-[12px] text-ink-3">
            {e.responsable}
            {e.cita?.fechaHora ? ` · ${textoCita(e.cita.fechaHora, { dateStyle: "medium", timeStyle: "short" })}` : ""}
            {e.pasoIntegrada && e.forma === "integrada" && e.estado !== "con_resultado" ? ` · Proveedor: ${PASOS[e.pasoIntegrada] ?? e.pasoIntegrada}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={tonoEstado(e)}>{e.estadoTexto}</Badge>
          {e.conclusionTexto && <Badge tone={tonoConclusion(e.conclusion)} dot>{e.conclusionTexto}</Badge>}
          {e.sinConclusion && <Badge tone="neutral">Resultado recibido · Sin conclusión</Badge>}
          {e.consentimientoTexto && <Badge tone={e.consentimiento === "rechazado" ? "bad" : "warn"}>{e.consentimientoTexto}</Badge>}
          {e.nuevoResultado && <Badge tone="human" dot><Sparkles className="h-3 w-3" /> Nuevo resultado</Badge>}
        </div>
      </div>
      {(e.motivoEstado && (e.estado === "no_realizada" || e.estado === "cancelada")) && <p className="mt-1.5 text-[12px] text-ink-3">Motivo: {e.motivoEstado}</p>}
      {e.claveProveedor && <p className="mt-1.5 text-[12px] text-ink-3">Proveedor {e.proveedor} · clave <span className="font-mono">{e.claveProveedor}</span></p>}
      {(puedePrincipal || (live && secundaria) || (live && menu.length > 0)) && (
        <div className="mt-2.5 flex flex-wrap items-center justify-end gap-2">
          {live && secundaria && etiquetas[secundaria] && (
            <Button size="sm" variant="outline" disabled={ocupado} onClick={() => onAccion(secundaria)}>
              {etiquetas[secundaria].icono} {etiquetas[secundaria].texto}
            </Button>
          )}
          {puedePrincipal && etiquetas[principal] && (
            <Button size="sm" variant={principal === "ver_resultado" ? "outline" : "primary"} disabled={ocupado} onClick={() => onAccion(principal)}>
              {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : etiquetas[principal].icono} {etiquetas[principal].texto}
            </Button>
          )}
          {live && menu.length > 0 && <MenuAcciones acciones={menu} />}
        </div>
      )}
    </li>
  );
}

/* ============================================================ Registrar / complementar / corregir ============================================================ */

function ModalResultado({ e, modo, onClose, onListo, onConflicto }: {
  e: Evaluacion;
  modo: ModoResultado;
  onClose: () => void;
  onListo: (texto: string) => void;
  onConflicto: () => void;
}) {
  const [elegido, setElegido] = useState<ModoResultado>(modo);
  const hayResultado = e.estado === "con_resultado";
  return (
    <ModalMarco
      titulo={hayResultado ? `Complementar o corregir · ${e.nombre}` : `Registrar resultado · ${e.nombre}`}
      subtitulo={hayResultado ? "La versión anterior queda en el historial." : "Queda registrado quién lo capturó y cuándo; el autor se guarda aparte."}
      onClose={onClose}
    >
      {hayResultado && (
        <div className="mb-4 grid grid-cols-2 gap-2">
          {(["corregir", "complementar"] as const).map((m) => (
            <button key={m} type="button" onClick={() => setElegido(m)} aria-pressed={elegido === m}
              className={cn("rounded-xl border px-3 py-2.5 text-sm font-medium transition", elegido === m ? "border-brand bg-brand-soft text-brand" : "border-border-soft text-ink-2 hover:border-brand/50")}>
              {m === "corregir" ? "Corregir el resultado" : "Agregar un complemento"}
            </button>
          ))}
        </div>
      )}
      <FormularioResultado
        key={`${elegido}-${e.resultadoVersion}`}
        evaluacion={e}
        modo={hayResultado ? elegido : "registrar"}
        onEnviar={(d) => registrarResultadoEvaluacion(e.codigo, d)}
        onListo={() => onListo(hayResultado ? "Resultado actualizado; la versión anterior quedó en el historial." : `Resultado de «${e.nombre}» guardado. El candidato sigue en su etapa.`)}
        onCancelar={onClose}
        onConflicto={onConflicto}
      />
    </ModalMarco>
  );
}

/* ============================================================ Ver resultado (detalle + historial) ============================================================ */

function Dato({ etiqueta, children }: { etiqueta: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] font-medium uppercase tracking-wide text-ink-3">{etiqueta}</span>
      <span className="text-sm text-ink">{children}</span>
    </div>
  );
}

export function ModalVerResultado({ codigo, live, onClose, onComplementar }: {
  codigo: string;
  live: boolean;
  onClose: () => void;
  onComplementar: (e: Evaluacion) => void;
}) {
  const [d, setD] = useState<{ evaluacion: Evaluacion; eventos: EventoEvaluacion[] } | null | undefined>(undefined);
  useEffect(() => {
    fetchEvaluacion(codigo).then((r) => setD(r));
  }, [codigo]);
  const e = d?.evaluacion;
  return (
    <ModalMarco titulo={e ? `${e.nombre}` : "Evaluación"} subtitulo={e ? `${e.responsable} · ${e.estadoTexto}` : undefined} onClose={onClose}>
      {d === undefined ? (
        <Loader2 className="h-5 w-5 animate-spin text-ink-3" />
      ) : !e ? (
        <p className="text-sm text-bad">No se pudo cargar la evaluación.</p>
      ) : (
        <div className="flex flex-col gap-5">
          {e.cita && (
            <section className="grid gap-3 rounded-xl border border-border-soft bg-surface-2/40 p-4 sm:grid-cols-2">
              <Dato etiqueta="Cita">{textoCita(e.cita.fechaHora, { dateStyle: "full", timeStyle: "short" })}</Dato>
              <Dato etiqueta="Modalidad">{e.cita.modalidad}{e.cita.porTeams ? " (Microsoft Teams)" : ""}</Dato>
              {e.cita.direccion && <Dato etiqueta="Dirección">{e.cita.direccion}</Dato>}
              {e.cita.ligaVideollamada && <Dato etiqueta="Liga de videollamada"><a className="break-all text-brand hover:underline" href={e.cita.ligaVideollamada} target="_blank" rel="noreferrer">{e.cita.ligaVideollamada}</a></Dato>}
              {e.cita.telefono && <Dato etiqueta="Teléfono">{e.cita.telefono}</Dato>}
            </section>
          )}
          {e.instrucciones && <Dato etiqueta="Instrucciones"><span className="whitespace-pre-line">{e.instrucciones}</span></Dato>}
          {e.ligaExternaCandidato && <Dato etiqueta="Liga de otro sistema"><a className="break-all text-brand hover:underline" href={e.ligaExternaCandidato} target="_blank" rel="noreferrer">{e.ligaExternaCandidato}</a></Dato>}
          {e.consentimiento !== "no_requerido" && (
            <Dato etiqueta="Consentimiento">{e.consentimiento === "otorgado" ? `Otorgado${e.consentimientoEn ? ` el ${textoFechaHora(e.consentimientoEn)}` : ""}` : e.consentimientoTexto}</Dato>
          )}

          <section className="rounded-xl border border-border-soft p-4">
            <h3 className="text-sm font-semibold">Resultado</h3>
            {e.estado !== "con_resultado" ? (
              <p className="mt-1 text-sm text-ink-3">Aún sin resultado registrado.</p>
            ) : (
              <div className="mt-2 flex flex-col gap-3">
                <div className="flex flex-wrap gap-1.5">
                  {e.conclusionTexto ? <Badge tone={tonoConclusion(e.conclusion)} dot>{e.conclusionTexto}</Badge> : <Badge tone="neutral">Resultado recibido · Sin conclusión</Badge>}
                </div>
                {e.restringido ? (
                  <p className="flex items-center gap-1.5 text-[12px] text-ink-3"><Lock className="h-3.5 w-3.5" /> Detalle médico restringido: solo ves el estado y la conclusión.</p>
                ) : (
                  <>
                    {e.comentarios && <p className="whitespace-pre-line text-sm leading-relaxed text-ink-2">{e.comentarios}</p>}
                    {e.adjuntos.length > 0 && (
                      <ul className="flex flex-col gap-1">
                        {e.adjuntos.map((a) => (
                          <li key={a.id}>
                            <a href={urlAdjuntoEvaluacion(e.codigo, a.id)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-sm font-semibold text-brand hover:underline">
                              <FileText className="h-4 w-4" /> {a.nombre}
                            </a>
                            <span className="ml-2 text-[11px] text-ink-3">{a.subidoPor}{a.subidoEn ? ` · ${textoFechaHora(a.subidoEn)}` : ""}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                )}
                <div className="grid gap-3 sm:grid-cols-2">
                  <Dato etiqueta="Realizada por">{e.realizadaPor || "No especificado"}{e.realizadaEn ? <span className="text-ink-3"> · {textoFechaHora(e.realizadaEn)}</span> : null}</Dato>
                  <Dato etiqueta="Registrada por">{e.registradaPor || "—"}{e.registradaVia ? <span className="text-ink-3"> ({VIA[e.registradaVia] ?? e.registradaVia})</span> : null}{e.registradaEn ? <span className="text-ink-3"> · {textoFechaHora(e.registradaEn)}</span> : null}</Dato>
                </div>
              </div>
            )}
          </section>

          <section>
            <h3 className="text-sm font-semibold">Historial</h3>
            <ol className="mt-2 flex flex-col gap-2 border-l border-border-soft pl-4">
              {d.eventos.map((ev) => (
                <li key={ev.id} className="text-[13px] leading-relaxed">
                  <span className="font-semibold text-ink">{ev.accionTexto}</span>
                  {ev.estadoNuevoTexto && ev.estadoAnteriorTexto !== ev.estadoNuevoTexto ? <span className="text-ink-2"> · {ev.estadoAnteriorTexto ? `${ev.estadoAnteriorTexto} → ` : ""}{ev.estadoNuevoTexto}</span> : null}
                  <span className="block text-[11px] text-ink-3">{ev.actor || "Sistema"} · {ev.canalTexto} · {textoFechaHora(ev.fecha)}</span>
                  {typeof ev.detalle.motivo === "string" && ev.detalle.motivo && <span className="block text-[12px] text-ink-2">Motivo: {ev.detalle.motivo}</span>}
                  {Object.keys(ev.anteriores).length > 0 && (
                    <span className="block text-[12px] text-ink-3">Antes: {Object.entries(ev.anteriores).filter(([, v]) => v !== null && v !== "").map(([k, v]) => `${k.replace(/_/g, " ")}: ${String(v).slice(0, 120)}`).join(" · ") || "—"}</span>
                  )}
                </li>
              ))}
            </ol>
          </section>

          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>Cerrar</Button>
            {live && e.estado === "con_resultado" && !e.restringido && (
              <Button size="sm" onClick={() => onComplementar(e)}><Pencil className="h-4 w-4" /> Complementar o corregir</Button>
            )}
          </div>
        </div>
      )}
    </ModalMarco>
  );
}

/* ============================================================ No realizada / Cancelar ============================================================ */

function ModalMotivo({ e, accion, onClose, onListo }: {
  e: Evaluacion;
  accion: "no_realizada" | "cancelar";
  onClose: () => void;
  onListo: (r: RespuestaEvaluacion, texto: string) => void;
}) {
  const [motivo, setMotivo] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState("");
  const notificar = useNotificarAccion("evaluacion_cancelada");
  const cancelar = accion === "cancelar";
  return (
    <ModalMarco
      titulo={cancelar ? `¿Cancelar «${e.nombre}»?` : `No realizada · ${e.nombre}`}
      subtitulo={cancelar
        ? `Queda cerrada sin resultado (no se puede reabrir).${e.cita ? " Se avisa al candidato y al evaluador." : e.forma === "asignada" ? " Se avisa al evaluador." : ""} La etapa del candidato no cambia.`
        : "El candidato o el evaluador no se presentó. Después podrás reprogramarla o cancelarla."}
      onClose={onClose}
      ancho="max-w-lg"
    >
      <Campo etiqueta={`Motivo ${cancelar ? "" : "(opcional)"}`}>
        <input value={motivo} onChange={(x) => setMotivo(x.target.value)} className={inputEv} autoFocus placeholder={cancelar ? "Ej. el candidato declinó" : "Ej. el candidato no se presentó"} />
      </Campo>
      {cancelar && (e.cita || e.forma === "asignada") && (
        <LineaNotificar className="mt-3" value={notificar.value} onChange={notificar.setValue} hayEntrevistador={e.forma === "asignada"} />
      )}
      {error && <p className="mt-3 text-sm font-semibold text-bad">{error}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onClose} disabled={ocupado}>Volver</Button>
        <Button
          size="sm"
          className={cancelar ? "bg-bad text-white hover:bg-bad/90" : undefined}
          disabled={ocupado}
          onClick={async () => {
            setOcupado(true);
            const r = cancelar ? await cancelarEvaluacion(e.codigo, motivo.trim(), notificar.value) : await marcarEvaluacionNoRealizada(e.codigo, motivo.trim());
            setOcupado(false);
            if (!r.ok) return setError(r.error);
            onListo(r.data, cancelar ? `«${e.nombre}» cancelada.` : `«${e.nombre}» marcada como no realizada.`);
          }}
        >
          {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : cancelar ? <Ban className="h-4 w-4" /> : <UserX className="h-4 w-4" />}
          {cancelar ? "Sí, cancelar" : "Marcar como no realizada"}
        </Button>
      </div>
    </ModalMarco>
  );
}

/* ============================================================ Reprogramar ============================================================ */

function citaDesde(e: Evaluacion): EstadoCita {
  if (!e.cita?.fechaHora) return citaVacia;
  const p = partesLocales(e.cita.fechaHora);
  return {
    fecha: p.fecha, hora: p.hora, modalidad: (e.cita.modalidad || "Videollamada") as EstadoCita["modalidad"], direccion: e.cita.direccion,
    liga: e.cita.porTeams ? "" : e.cita.ligaVideollamada, telefono: e.cita.telefono, otraLiga: !e.cita.porTeams && Boolean(e.cita.ligaVideollamada),
  };
}

function ModalReprogramar({ e, onClose, onListo }: { e: Evaluacion; onClose: () => void; onListo: (r: RespuestaEvaluacion) => void }) {
  const teams = useTeamsConectado();
  const [cita, setCita] = useState<EstadoCita>(() => citaDesde(e));
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState("");
  const notificar = useNotificarAccion("evaluacion_reprogramada");
  return (
    <ModalMarco titulo={`Reprogramar · ${e.nombre}`} subtitulo="Se avisa al candidato y al evaluador con la nueva fecha." onClose={onClose}>
      <CamposCita valor={cita} onChange={setCita} teams={teams || Boolean(e.cita?.porTeams)} />
      <LineaNotificar className="mt-4" value={notificar.value} onChange={notificar.setValue} hayEntrevistador={e.forma === "asignada"} />
      {error && <p className="mt-3 text-sm font-semibold text-bad">{error}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onClose} disabled={ocupado}>Cancelar</Button>
        <Button size="sm" disabled={ocupado} onClick={async () => {
          const v = validarCita(cita, teams);
          if (v) return setError(v);
          setOcupado(true);
          const r = await reprogramarEvaluacion(e.codigo, citaEntrada(cita, teams), notificar.value);
          setOcupado(false);
          if (!r.ok) return setError(r.error);
          onListo(r.data);
        }}>
          {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCw className="h-4 w-4" />} Reprogramar
        </Button>
      </div>
    </ModalMarco>
  );
}

/* ============================================================ Modificar datos e instrucciones ============================================================ */

function ModalModificar({ c, e, onClose, onListo }: { c: Candidato; e: Evaluacion; onClose: () => void; onListo: (r: RespuestaEvaluacion) => void }) {
  const teams = useTeamsConectado();
  const { internos, contactos } = useCatalogoEvaluadores(c.clienteIdVacante ?? null);
  const [nombre, setNombre] = useState(e.nombrePropio);
  const [instrucciones, setInstrucciones] = useState(e.instrucciones);
  const [liga, setLiga] = useState(e.ligaExternaCandidato);
  const [cambiarEvaluador, setCambiarEvaluador] = useState(false);
  const [evaluador, setEvaluador] = useState<EstadoEvaluador>({
    tipo: e.evaluador?.tipo === "interno" ? "interno" : "externo", usuarioId: e.evaluador?.usuarioId ?? null,
    contacto: e.evaluador?.contactoId ?? "", nombre: "", correo: "", whatsapp: "",
  });
  const [conCita, setConCita] = useState(Boolean(e.cita));
  const [cita, setCita] = useState<EstadoCita>(() => citaDesde(e));
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState("");
  const notificar = useNotificarAccion("evaluacion_reprogramada");
  return (
    <ModalMarco titulo={`Modificar · ${e.nombre}`} subtitulo="Si cambias la fecha u hora de la cita se avisa a ambos como reprogramación." onClose={onClose}>
      <div className="flex flex-col gap-4">
        {e.tipo === "otra" && (
          <Campo etiqueta="Nombre"><input value={nombre} onChange={(x) => setNombre(x.target.value)} className={inputEv} /></Campo>
        )}
        {e.forma === "liga_otro_sistema" && (
          <Campo etiqueta="Liga del otro sistema"><input value={liga} onChange={(x) => setLiga(x.target.value)} className={inputEv} /></Campo>
        )}
        {e.forma === "asignada" && (
          <div>
            <label className="flex cursor-pointer items-center gap-2 text-sm font-medium text-ink-2">
              <input type="checkbox" checked={cambiarEvaluador} onChange={(x) => setCambiarEvaluador(x.target.checked)} /> Cambiar evaluador ({e.evaluador?.nombre})
            </label>
            {cambiarEvaluador && <div className="mt-2"><SelectorEvaluador valor={evaluador} onChange={setEvaluador} internos={internos} contactos={contactos} clienteNombre={c.clienteVacante} /></div>}
          </div>
        )}
        <div>
          <label className="flex cursor-pointer items-center justify-between gap-3">
            <span className="text-sm font-semibold text-ink">Cita</span>
            <input type="checkbox" role="switch" checked={conCita} onChange={(x) => setConCita(x.target.checked)} className="h-5 w-9 cursor-pointer accent-[var(--brand)]" />
          </label>
          {conCita && <div className="mt-3"><CamposCita valor={cita} onChange={setCita} teams={teams} /></div>}
        </div>
        <Campo etiqueta="Instrucciones">
          <textarea value={instrucciones} onChange={(x) => setInstrucciones(x.target.value)} rows={3} className="rounded-xl border border-border-soft bg-surface px-3.5 py-2.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
        </Campo>
        <LineaNotificar value={notificar.value} onChange={notificar.setValue} hayEntrevistador={e.forma === "asignada"} />
        {error && <p className="text-sm font-semibold text-bad">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose} disabled={ocupado}>Cancelar</Button>
          <Button size="sm" disabled={ocupado} onClick={async () => {
            if (cambiarEvaluador) {
              const v = validarEvaluador(evaluador);
              if (v) return setError(v);
            }
            if (conCita) {
              const v = validarCita(cita, teams);
              if (v) return setError(v);
            }
            setOcupado(true);
            const r = await modificarEvaluacion(e.codigo, {
              nombre: e.tipo === "otra" ? nombre : undefined, instrucciones, ligaExternaCandidato: e.forma === "liga_otro_sistema" ? liga : undefined,
              evaluador: cambiarEvaluador ? evaluadorEntrada(evaluador) : null, cita: conCita ? citaEntrada(cita, teams) : null,
              quitarCita: !conCita && Boolean(e.cita), notificar: notificar.value,
            });
            setOcupado(false);
            if (!r.ok) return setError(r.error);
            onListo(r.data);
          }}>
            {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : <Pencil className="h-4 w-4" />} Guardar cambios
          </Button>
        </div>
      </div>
    </ModalMarco>
  );
}
