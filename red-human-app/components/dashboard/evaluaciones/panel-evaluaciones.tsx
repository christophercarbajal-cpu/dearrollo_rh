"use client";

/* Evaluaciones del candidato — tarjetas compactas (Evaluaciones unificadas — Fase 1, 2026-09-29, especificación
   sección 6). Una evaluación = una tarjeta: tipo y nombre · responsable · seguimiento · conclusión (UNA etiqueta) ·
   cita · condiciones (consentimiento, «Nuevo resultado») · botón de siguiente acción + menú «⋯». Un solo botón
   «Agregar evaluación». Registrar o revisar un resultado NUNCA mueve al candidato de etapa ni lo envía a Contratación;
   solo CREAR una entrevista humana lo pasa a «Filtro humano» (2026-10-01, pipeline de 5 columnas).
   2026-10-01: cada liga externa (consentimiento, evaluador/médico, otro sistema, proveedor) se ve en la tarjeta con
   Abrir / Copiar / Enviar o reenviar y el estado de su último envío APARTE (un envío fallido no bloquea nada); con
   resultado, la tarjeta muestra resultado, observaciones y reporte (abrir/descargar) y «Marcar como revisada». */

import { useCallback, useEffect, useState } from "react";
import {
  Ban, BadgeCheck, BellRing, CalendarClock, CheckCircle2, ClipboardCheck, Copy, Download, ExternalLink, Eye, FileText, Loader2, Lock, Pencil,
  PlayCircle, RefreshCw, RotateCw, Send, SkipForward, Sparkles, UserX,
} from "lucide-react";
import { Badge, Button, Card, Eyebrow } from "@/components/ui";
import { MenuAcciones } from "@/components/dashboard/menu-acciones";
import { ModalMarco } from "@/components/dashboard/modulos-rh";
import { usePsicometriaSimple } from "@/components/sesion";
import { estadoPsicometriaSimple } from "@/components/dashboard/evaluaciones/psicometria-simple";
import { ReferenciasDictamen } from "@/components/dashboard/evaluaciones/referencias-dictamen";
import { LineaNotificar, useNotificarAccion } from "@/components/dashboard/linea-notificar";
import { FormularioResultado, type ModoResultado } from "@/components/dashboard/evaluaciones/formulario-resultado";
import { ModalAgregarEvaluacion, type PresetEvaluacion } from "@/components/dashboard/evaluaciones/agregar-evaluacion";
import {
  CamposCita, Campo, SelectorEvaluador, citaEntrada, citaVacia, evaluadorEntrada, inputEv, useCatalogoEvaluadores, useTeamsConectado,
  validarCita, validarEvaluador, type EstadoCita, type EstadoEvaluador,
} from "@/components/dashboard/evaluaciones/campos-evaluacion";
import {
  avanzarEvaluacionIntegrada, cancelarEvaluacion, confirmarInicioEvaluacion, dictaminarReferencia, enviarEvaluacionProveedor, enviarLigaEvaluacion, fetchEvaluacion,
  fetchEvaluaciones, lineasResultados, marcarEvaluacionNoRealizada, marcarEvaluacionRealizada, modificarEvaluacion, recordatorioEvaluacion,
  registrarResultadoEvaluacion, reprogramarEvaluacion, revisarEvaluacion, sincronizarEvaluacion, urlAdjuntoEvaluacion,
  type Evaluacion, type EventoEvaluacion, type LigaEvaluacion, type RespuestaEvaluacion, type Resultado,
  proveedorVisible,
} from "@/lib/api";
import type { Candidato } from "@/lib/data";
import { partesLocales, textoCita, textoFechaHora } from "@/lib/fechas";
import { cn } from "@/lib/utils";

const DESTINATARIO: Record<string, string> = { candidato: "Candidato", medico: "Médico", entrevistador: "Entrevistador", evaluador: "Evaluador" };
const PASOS: Record<string, string> = { asignada: "Asignada", enviada: "Liga generada", iniciada: "Iniciada", completada: "Completada", resultado_recibido: "Resultado recibido" };
const VIA: Record<string, string> = { sistema: "en el sistema", liga_evaluador: "vía liga del evaluador", proveedor: "vía proveedor", migracion: "migración" };

type Aviso = { tono: "ok" | "warn" | "error"; texto: string } | null;

function tonoEstado(e: Evaluacion): "good" | "warn" | "bad" | "neutral" | "brand" {
  if (e.seguimiento === "revisada") return "good";
  if (e.estado === "con_resultado") return "brand";
  if (e.estado === "realizada_sin_resultado" || e.seguimiento === "en_curso" || e.seguimiento === "en_proceso") return "warn";
  if (e.estado === "no_realizada") return "bad";
  return "neutral";
}
const CANAL: Record<string, string> = { whatsapp: "WhatsApp", correo: "Correo" };
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
  const [revisar, setRevisar] = useState<Evaluacion | null>(null);

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

  /** Psicométricas.mx: además del alta en el proveedor, Red Human manda la liga (o la clave) al candidato por su canal. */
  async function enviarProveedor(e: Evaluacion) {
    setOcupado(e.codigo);
    setAviso(null);
    const r = await enviarEvaluacionProveedor(e.codigo);
    setOcupado("");
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    const env = r.data.envioCandidato;
    const NOMBRE_CANAL: Record<string, string> = { telegram: "Telegram", whatsapp: "WhatsApp", correo: "correo" };
    const canales = (env?.canales ?? []).map((c) => NOMBRE_CANAL[c] ?? c).join(" y ");
    const texto = !env
      ? `«${e.nombre}» enviada al proveedor.`
      : env.enviado
        ? `«${e.nombre}» asignada. Le mandamos al candidato la liga de evaluación y su clave por ${canales}.${env.detalle ? ` No salió: ${env.detalle}.` : ""}`
        : `«${e.nombre}» asignada, pero no pudimos avisarle al candidato: ${env.detalle || "sin detalle"}. Usa «Reenviar» o copia la liga y la clave.`;
    listo(r.data, texto);
  }

  function copiar(texto: string, que: string) {
    void navigator.clipboard?.writeText(texto);
    setAviso({ tono: "ok", texto: `${que}: copiada.` });
  }

  /** «Enviar o reenviar» una liga: la liga ya existe; si el envío falla se informa y Abrir/Copiar siguen disponibles. */
  function enviarLiga(e: Evaluacion, l: LigaEvaluacion) {
    void accion(e, () => enviarLigaEvaluacion(e.codigo, l.clave), `${l.titulo}: envío registrado.`);
  }

  function ejecutar(e: Evaluacion, a: string) {
    switch (a) {
      case "registrar_resultado": return setResultado({ e, modo: "registrar" });
      case "complementar": return setResultado({ e, modo: "corregir" });
      case "ver_resultado": return setVer(e.codigo);
      case "marcar_revisada":
      case "cambiar_revision": return setRevisar(e);
      case "confirmar_inicio": return accion(e, () => confirmarInicioEvaluacion(e.codigo), `«${e.nombre}»: inicio confirmado.`);
      case "marcar_realizada": return accion(e, () => marcarEvaluacionRealizada(e.codigo), `«${e.nombre}»: Realizada · Resultado pendiente.`);
      case "reprogramar": return setReprogramar(e);
      case "modificar": return setModificar(e);
      case "no_realizada": return setMotivo({ e, accion: "no_realizada" });
      case "cancelar": return setMotivo({ e, accion: "cancelar" });
      case "enviar_proveedor": return void enviarProveedor(e);
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
    }
  }

  const ETIQUETA: Record<string, { texto: string; icono: React.ReactNode }> = {
    // la captura manual existe aunque la evaluación esté asignada a un externo: ambos escriben el MISMO registro
    registrar_resultado: { texto: "Registrar resultado / Adjuntar reporte", icono: <ClipboardCheck className="h-4 w-4" /> },
    marcar_realizada: { texto: "Marcar como realizada", icono: <CheckCircle2 className="h-4 w-4" /> },
    ver_resultado: { texto: "Ver resultado", icono: <Eye className="h-4 w-4" /> },
    complementar: { texto: "Complementar o corregir", icono: <Pencil className="h-4 w-4" /> },
    marcar_revisada: { texto: "Marcar como revisada", icono: <BadgeCheck className="h-4 w-4" /> },
    cambiar_revision: { texto: "Cambiar revisión de RH", icono: <BadgeCheck className="h-4 w-4" /> },
    confirmar_inicio: { texto: "Confirmar inicio", icono: <PlayCircle className="h-4 w-4" /> },
    reprogramar: { texto: "Reprogramar", icono: <CalendarClock className="h-4 w-4" /> },
    modificar: { texto: "Modificar datos e instrucciones", icono: <Pencil className="h-4 w-4" /> },
    no_realizada: { texto: "Marcar como no realizada", icono: <UserX className="h-4 w-4" /> },
    cancelar: { texto: "Cancelar evaluación…", icono: <Ban className="h-4 w-4" /> },
    programar_otra: { texto: "Programar otra entrevista", icono: <CalendarClock className="h-4 w-4" /> },
    enviar_proveedor: { texto: "Enviar al proveedor", icono: <Send className="h-4 w-4" /> },
    sincronizar: { texto: "Reintentar sincronización", icono: <RefreshCw className="h-4 w-4" /> },
    avanzar_paso: { texto: "Simular siguiente paso del proveedor", icono: <SkipForward className="h-4 w-4" /> },
  };
  const etiquetasDe = (e: Evaluacion): Record<string, { texto: string; icono: React.ReactNode }> => ({
    ...ETIQUETA,
    // «En curso» (psicométrica) / «En proceso» (socioeconómica) solo con esta confirmación
    confirmar_inicio: { ...ETIQUETA.confirmar_inicio, texto: e.tipo === "socioeconomica" ? "Marcar en proceso" : "Confirmar inicio (En curso)" },
  });

  function menuDe(e: Evaluacion) {
    const items: { etiqueta: string; icono: React.ReactNode; onClick: () => void; peligrosa?: boolean; disabled?: boolean }[] = [];
    for (const a of e.acciones.menu) {
      if (a === "recordatorio") {
        const alCandidato = Boolean(e.cita) || e.forma === "liga_otro_sistema" || (e.forma === "integrada" && Boolean(e.claveProveedor));
        const alEvaluador = e.forma === "asignada";
        if (alCandidato) items.push({ etiqueta: "Enviar recordatorio al candidato", icono: <BellRing className="h-4 w-4" />, onClick: () => accion(e, () => recordatorioEvaluacion(e.codigo, "candidato"), "Recordatorio enviado al candidato.") });
        if (alEvaluador) items.push({ etiqueta: "Enviar recordatorio al evaluador", icono: <BellRing className="h-4 w-4" />, onClick: () => accion(e, () => recordatorioEvaluacion(e.codigo, "evaluador"), "Recordatorio enviado al evaluador.") });
        if (alCandidato && alEvaluador) items.push({ etiqueta: "Enviar recordatorio a ambos", icono: <BellRing className="h-4 w-4" />, onClick: () => accion(e, () => recordatorioEvaluacion(e.codigo, "ambos"), "Recordatorio enviado a ambos.") });
        continue;
      }
      const et = etiquetasDe(e)[a];
      if (et) items.push({ etiqueta: et.texto, icono: et.icono, onClick: () => ejecutar(e, a), peligrosa: a === "cancelar" });
    }
    return items;
  }

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <Eyebrow>{titulo}</Eyebrow>
          <p className="mt-1 text-[12px] text-ink-3">Entrevista humana, médica, psicométrica, socioeconómica, técnica o práctica, referencias laborales u otra. Solo la entrevista humana mueve a Filtro humano; los resultados no cambian la columna.</p>
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
                etiquetas={etiquetasDe(e)}
                menu={menuDe(e)}
                onCopiar={(l) => copiar(l.url, l.titulo)}
                onEnviarLiga={(l) => enviarLiga(e, l)}
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
          onRevisar={(e) => { setVer(null); setRevisar(e); }}
        />
      )}
      {revisar && (
        <ModalRevisar
          e={revisar}
          onClose={() => setRevisar(null)}
          onListo={(r) => { setRevisar(null); listo(r, `«${r.evaluacion.nombre}» revisada. El candidato sigue en su etapa.`); }}
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

/** Una liga externa: Abrir / Copiar / Enviar o reenviar siempre visibles; el estado del último envío va aparte y un
 * envío fallido no deshabilita nada. */
function FilaLiga({ l, live, ocupado, onCopiar, onEnviar }: {
  l: LigaEvaluacion; live: boolean; ocupado: boolean; onCopiar: () => void; onEnviar: () => void;
}) {
  const u = l.ultimoEnvio;
  return (
    <li className="rounded-lg border border-border-soft bg-surface-2/40 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold text-ink">{l.titulo}</p>
          <p className="truncate font-mono text-[11px] text-ink-3" title={l.url}>{l.url}</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <a href={l.url} target="_blank" rel="noreferrer" className="inline-flex h-8 items-center gap-1 rounded-lg border border-border-soft px-2.5 text-[12px] font-semibold text-ink-2 hover:border-brand/50 hover:text-brand">
            <ExternalLink className="h-3.5 w-3.5" /> Abrir
          </a>
          <button type="button" onClick={onCopiar} className="inline-flex h-8 items-center gap-1 rounded-lg border border-border-soft px-2.5 text-[12px] font-semibold text-ink-2 hover:border-brand/50 hover:text-brand">
            <Copy className="h-3.5 w-3.5" /> Copiar
          </button>
          {live && (
            <button
              type="button"
              onClick={onEnviar}
              disabled={ocupado || !l.puedeEnviar}
              title={l.puedeEnviar ? undefined : l.motivoNoEnvio}
              className="inline-flex h-8 items-center gap-1 rounded-lg border border-brand/40 px-2.5 text-[12px] font-semibold text-brand hover:bg-brand-soft disabled:cursor-not-allowed disabled:opacity-50"
            >
              {ocupado ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />} {u ? "Reenviar" : "Enviar"}
            </button>
          )}
        </div>
      </div>
      <p className={cn("mt-1 text-[11px] leading-relaxed", !u ? "text-ink-3" : u.enviado ? "text-good" : "text-warn")}>
        {!u
          ? (l.puedeEnviar ? `Sin enviar · para ${l.para}` : l.motivoNoEnvio)
          : <>
              {u.enviado ? "Enviado" : "Envío fallido"} a {l.para}{u.fecha ? ` · ${textoFechaHora(u.fecha)}` : ""}:{" "}
              {u.envios.map((x) => `${x.enviado ? "✓" : "✗"} ${CANAL[x.canal] ?? (x.canal || "sin canal")}${!x.enviado && x.detalle ? ` (${x.detalle})` : ""}`).join(" · ")}
              {!u.enviado && " — la liga sigue disponible para abrir o copiar."}
            </>}
      </p>
    </li>
  );
}

/** Resultado en la tarjeta: conclusión ya va en etiqueta; aquí observaciones, reporte (abrir/descargar), autor y la
 * revisión de RH. Lo médico restringido solo muestra estado y conclusión. */
function ResultadoEnTarjeta({ e }: { e: Evaluacion }) {
  if (e.estado !== "con_resultado") return null;
  if (e.restringido) {
    return <p className="mt-2 flex items-center gap-1.5 text-[12px] text-ink-3"><Lock className="h-3.5 w-3.5" /> Detalle médico restringido: solo ves el estado y la conclusión.</p>;
  }
  return (
    <div className="mt-2.5 flex flex-col gap-2 rounded-lg border border-border-soft bg-surface-2/30 px-3 py-2.5">
      {e.comentarios && <p className="line-clamp-4 whitespace-pre-line text-[13px] leading-relaxed text-ink-2">{e.comentarios}</p>}
      {e.adjuntos.length > 0 && <ListaAdjuntos e={e} />}
      <p className="text-[11px] text-ink-3">
        Realizada por {e.realizadaPor || "No especificado"} · registrada por {e.registradaPor || "—"}{e.registradaVia ? ` (${VIA[e.registradaVia] ?? e.registradaVia})` : ""}
        {e.registradaEn ? ` · ${textoFechaHora(e.registradaEn)}` : ""}
      </p>
      {e.revision ? (
        <p className="text-[12px] text-good">
          <BadgeCheck className="mr-1 inline h-3.5 w-3.5" />
          Revisada por {e.revision.revisadaPor}{e.revision.revisadaEn ? ` · ${textoFechaHora(e.revision.revisadaEn)}` : ""} · Conclusión de RH: <b>{e.revision.conclusionTexto}</b>
          {e.revision.comentario ? <span className="block text-ink-2">{e.revision.comentario}</span> : null}
        </p>
      ) : (
        <p className="text-[12px] text-warn">Resultado recibido · pendiente de revisión de RH.</p>
      )}
    </div>
  );
}

function ListaAdjuntos({ e }: { e: Evaluacion }) {
  return (
    <ul className="flex flex-col gap-1">
      {e.adjuntos.map((a) => (
        <li key={a.id} className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
          <span className="inline-flex min-w-0 items-center gap-1.5 text-sm font-semibold text-ink"><FileText className="h-4 w-4 shrink-0" /> <span className="truncate">{a.nombre}</span></span>
          <a href={urlAdjuntoEvaluacion(e.codigo, a.id)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[12px] font-semibold text-brand hover:underline"><ExternalLink className="h-3.5 w-3.5" /> Abrir</a>
          <a href={urlAdjuntoEvaluacion(e.codigo, a.id, true)} className="inline-flex items-center gap-1 text-[12px] font-semibold text-brand hover:underline"><Download className="h-3.5 w-3.5" /> Descargar</a>
          <span className="text-[11px] text-ink-3">{a.subidoPor}{a.subidoEn ? ` · ${textoFechaHora(a.subidoEn)}` : ""}</span>
        </li>
      ))}
    </ul>
  );
}

function TarjetaEvaluacion({ e, live, ocupado, onAccion, etiquetas, menu, onCopiar, onEnviarLiga }: {
  e: Evaluacion;
  live: boolean;
  ocupado: boolean;
  onAccion: (a: string) => void;
  etiquetas: Record<string, { texto: string; icono: React.ReactNode }>;
  menu: { etiqueta: string; icono: React.ReactNode; onClick: () => void; peligrosa?: boolean }[];
  onCopiar: (l: LigaEvaluacion) => void;
  onEnviarLiga: (l: LigaEvaluacion) => void;
}) {
  const principal = e.acciones.principal;
  const secundaria = e.acciones.secundaria;
  // flujo simple de psicometría (solo Cuentas «demo-grupak»): Sin enviar / Enviada / Completada
  const simple = usePsicometriaSimple();
  const estadoSimple = simple ? estadoPsicometriaSimple(e) : null;
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
            {e.tipo === "psicometrica" && e.proveedor && e.responsable !== e.proveedor ? ` · Proveedor: ${proveedorVisible(e.proveedor)}` : ""}
            {e.cita?.fechaHora ? ` · ${textoCita(e.cita.fechaHora, { dateStyle: "medium", timeStyle: "short" })}` : ""}
            {e.pasoIntegrada && e.forma === "integrada" && e.estado !== "con_resultado" && !e.estadoProveedorTexto ? ` · Proveedor: ${PASOS[e.pasoIntegrada] ?? e.pasoIntegrada}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {/* psicometría del proveedor: solo Pendiente / En curso / Completada (la revisión de RH va en su insignia) */}
          {estadoSimple
            ? <Badge tone={estadoSimple.tono}>{estadoSimple.texto}</Badge>
            : <Badge tone={tonoEstado(e)}>{e.estadoProveedorTexto || e.seguimientoTexto || e.estadoTexto}</Badge>}
          {e.conclusionTexto && <Badge tone={tonoConclusion(e.conclusion)} dot>{e.conclusionTexto}</Badge>}
          {e.revision && e.revision.conclusion !== e.conclusion && <Badge tone={tonoConclusion(e.revision.conclusion)} dot>RH: {e.revision.conclusionTexto}</Badge>}
          {e.sinConclusion && <Badge tone="neutral">Resultado recibido · Sin conclusión</Badge>}
          {e.consentimientoTexto && <Badge tone={e.consentimiento === "rechazado" ? "bad" : "warn"}>{e.consentimientoTexto}</Badge>}
          {e.nuevoResultado && <Badge tone="human" dot><Sparkles className="h-3 w-3" /> Nuevo resultado</Badge>}
        </div>
      </div>
      {(e.motivoEstado && (e.estado === "no_realizada" || e.estado === "cancelada")) && <p className="mt-1.5 text-[12px] text-ink-3">Motivo: {e.motivoEstado}</p>}
      {e.claveProveedor && <p className="mt-1.5 text-[12px] text-ink-3">Proveedor {proveedorVisible(e.proveedor)} · clave <span className="font-mono">{e.claveProveedor}</span></p>}
      {(e.ligas?.length ?? 0) > 0 && (
        <ul className="mt-2.5 flex flex-col gap-1.5">
          {e.ligas!.map((l) => (
            <FilaLiga key={l.clave} l={l} live={live} ocupado={ocupado} onCopiar={() => onCopiar(l)} onEnviar={() => onEnviarLiga(l)} />
          ))}
        </ul>
      )}
      {Object.keys(e.envios ?? {}).length > 0 && (
        // 2026-10-08: trazabilidad por destinatario (último envío a cada quien)
        <p className="mt-1.5 flex flex-wrap gap-x-3 text-[11px] text-ink-3">
          {Object.entries(e.envios!).map(([d, x]) => (
            <span key={d}>{DESTINATARIO[d] ?? d}: <b className={x.estado === "fallido" ? "text-bad" : x.estado === "entregado" ? "text-good" : "text-ink-2"}>{x.estadoTexto}</b></span>
          ))}
        </p>
      )}
      {e.tipo === "referencias" && ((e.referencias?.length ?? 0) > 0 || e.esperandoReferencias) && (
        <div className="mt-2.5">
          {e.esperandoReferencias
            ? <p className="text-[12px] text-ink-3">Esperando a que el candidato capture sus referencias.</p>
            : <ReferenciasDictamen referencias={e.referencias ?? []} soloLectura={!live || e.estado === "cancelada"}
                onDictaminar={(rid, datos) => dictaminarReferencia(e.codigo, rid, datos)} />}
        </div>
      )}
      <ResultadoEnTarjeta e={e} />
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

export function ModalVerResultado({ codigo, live, onClose, onComplementar, onRevisar }: {
  codigo: string;
  live: boolean;
  onClose: () => void;
  onComplementar: (e: Evaluacion) => void;
  onRevisar?: (e: Evaluacion) => void;
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
          {(e.guion?.preguntas?.length ?? 0) > 0 && (
            <Dato etiqueta={`Guion de la entrevista${e.guion?.tipoTexto ? ` · ${e.guion.tipoTexto}` : ""}`}>
              {e.guion?.enfoque && <span className="block text-ink-3">{e.guion.enfoque}</span>}
              <ol className="mt-1 list-decimal pl-5">{e.guion!.preguntas!.map((q, i) => <li key={i}>{q}</li>)}</ol>
            </Dato>
          )}
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
                    {e.adjuntos.length > 0 && <ListaAdjuntos e={e} />}
                  </>
                )}
                <div className="grid gap-3 sm:grid-cols-2">
                  <Dato etiqueta="Realizada por">{e.realizadaPor || "No especificado"}{e.realizadaEn ? <span className="text-ink-3"> · {textoFechaHora(e.realizadaEn)}</span> : null}</Dato>
                  <Dato etiqueta="Registrada por">{e.registradaPor || "—"}{e.registradaVia ? <span className="text-ink-3"> ({VIA[e.registradaVia] ?? e.registradaVia})</span> : null}{e.registradaEn ? <span className="text-ink-3"> · {textoFechaHora(e.registradaEn)}</span> : null}</Dato>
                </div>
              </div>
            )}
          </section>

          {e.estado === "con_resultado" && (
            <section className={cn("rounded-xl border p-4", e.revision ? "border-good/30 bg-good-soft/30" : "border-warn/30 bg-warn-soft/30")}>
              <h3 className="text-sm font-semibold">Revisión de RH</h3>
              {e.revision ? (
                <div className="mt-2 grid gap-3 sm:grid-cols-2">
                  <Dato etiqueta="Conclusión de RH"><Badge tone={tonoConclusion(e.revision.conclusion)} dot>{e.revision.conclusionTexto}</Badge></Dato>
                  <Dato etiqueta="Revisada por">{e.revision.revisadaPor}{e.revision.revisadaEn ? <span className="text-ink-3"> · {textoFechaHora(e.revision.revisadaEn)}</span> : null}</Dato>
                  {e.revision.comentario && <div className="sm:col-span-2"><Dato etiqueta="Comentario"><span className="whitespace-pre-line">{e.revision.comentario}</span></Dato></div>}
                </div>
              ) : (
                <p className="mt-1 text-sm text-ink-2">Pendiente. Recibir un resultado no lo marca como revisado.</p>
              )}
            </section>
          )}

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
              <Button size="sm" variant={e.revision ? "primary" : "outline"} onClick={() => onComplementar(e)}><Pencil className="h-4 w-4" /> Complementar o corregir</Button>
            )}
            {live && onRevisar && e.estado === "con_resultado" && !e.restringido && (
              <Button size="sm" variant={e.revision ? "outline" : "primary"} onClick={() => onRevisar(e)}>
                <BadgeCheck className="h-4 w-4" /> {e.revision ? "Cambiar revisión" : "Marcar como revisada"}
              </Button>
            )}
          </div>
        </div>
      )}
    </ModalMarco>
  );
}

/* ============================================================ Marcar como revisada ============================================================ */

function ModalRevisar({ e, onClose, onListo }: { e: Evaluacion; onClose: () => void; onListo: (r: RespuestaEvaluacion) => void }) {
  const [conclusion, setConclusion] = useState(e.revision?.conclusion ?? e.conclusion ?? "");
  const [comentario, setComentario] = useState(e.revision?.comentario ?? "");
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState("");
  return (
    <ModalMarco
      titulo={`${e.revision ? "Cambiar revisión" : "Marcar como revisada"} · ${e.nombre}`}
      subtitulo="Queda registrado tu nombre y la fecha. Revisar no mueve al candidato de etapa: esa decisión sigue siendo aparte."
      onClose={onClose}
      ancho="max-w-xl"
    >
      <div className="flex flex-col gap-4">
        <section className="rounded-xl border border-border-soft bg-surface-2/40 p-3.5">
          <p className="text-[11px] font-medium uppercase tracking-wide text-ink-3">Resultado recibido</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {e.conclusionTexto ? <Badge tone={tonoConclusion(e.conclusion)} dot>{e.conclusionTexto}</Badge> : <Badge tone="neutral">Sin conclusión del evaluador</Badge>}
          </div>
          {e.comentarios && <p className="mt-2 line-clamp-6 whitespace-pre-line text-[13px] leading-relaxed text-ink-2">{e.comentarios}</p>}
          {e.adjuntos.length > 0 && <div className="mt-2"><ListaAdjuntos e={e} /></div>}
        </section>
        <fieldset>
          <legend className="text-sm font-medium text-ink-2">Conclusión de RH <span className="text-bad">*</span></legend>
          <div className={cn("mt-1.5 grid gap-2", e.conclusionesPosibles.length === 3 ? "sm:grid-cols-3" : "sm:grid-cols-2")}>
            {e.conclusionesPosibles.map((o) => (
              <button
                key={o.valor}
                type="button"
                onClick={() => setConclusion(o.valor)}
                aria-pressed={conclusion === o.valor}
                className={cn("min-h-11 rounded-xl border px-3 py-2 text-sm font-semibold transition",
                  conclusion === o.valor ? "border-brand bg-brand-soft text-brand" : "border-border-soft text-ink-2 hover:border-brand/50")}
              >
                {o.texto}
              </button>
            ))}
          </div>
        </fieldset>
        <Campo etiqueta={<>Comentario <span className="font-normal text-ink-3">(opcional)</span></>}>
          <textarea value={comentario} onChange={(x) => setComentario(x.target.value)} rows={3} className="rounded-xl border border-border-soft bg-surface px-3.5 py-2.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
        </Campo>
        {error && <p role="alert" className="text-sm font-semibold text-bad">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose} disabled={ocupado}>Cancelar</Button>
          <Button size="sm" disabled={ocupado} onClick={async () => {
            if (!conclusion) return setError("Elige la conclusión de RH.");
            setOcupado(true);
            const r = await revisarEvaluacion(e.codigo, conclusion, comentario.trim());
            setOcupado(false);
            if (!r.ok) return setError(r.error);
            onListo(r.data);
          }}>
            {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : <BadgeCheck className="h-4 w-4" />} Marcar como revisada
          </Button>
        </div>
      </div>
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
  const [proveedor, setProveedor] = useState(e.proveedor);
  const psicoLibre = e.tipo === "psicometrica" && e.forma !== "integrada";
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
        {psicoLibre && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Campo etiqueta="Nombre de prueba o batería"><input value={nombre} onChange={(x) => setNombre(x.target.value)} className={inputEv} /></Campo>
            <Campo etiqueta="Proveedor"><input value={proveedor} onChange={(x) => setProveedor(x.target.value)} className={inputEv} /></Campo>
          </div>
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
              nombre: e.tipo === "otra" || psicoLibre ? nombre : undefined, proveedor: psicoLibre ? proveedor : undefined, instrucciones, ligaExternaCandidato: e.forma === "liga_otro_sistema" ? liga : undefined,
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
