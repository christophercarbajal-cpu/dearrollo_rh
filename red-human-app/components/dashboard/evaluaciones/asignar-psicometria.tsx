"use client";

/* Asignación simplificada de psicometrías (2026-10-07). Vista LIMPIA de la actividad: nombre de la batería
   configurada (ruta o vacante), «Cambiar selección» y UN botón «Asignar y enviar» (alta en el proveedor + clave + aviso
   al candidato por Notificaciones). Sin «¿Cómo se realizará?», evaluador ni cita: eso solo existe en el flujo
   secundario «Agregar prueba externa» (ligas de otro sistema o resultados manuales).
   Doble clic: el botón se bloquea con un candado síncrono (useRef) y la API además rechaza (409) una segunda
   asignación viva con las mismas pruebas. Si el proveedor falla se muestra su motivo y NO queda nada asignado. */

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Brain, ChevronDown, Loader2, Search, Send } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import { asignarPsicometria, fetchVistaPsicometria, type PruebaPsicometrica, type RespuestaEvaluacion, type VistaPsicometria } from "@/lib/api";
import type { Candidato } from "@/lib/data";
import { cn } from "@/lib/utils";

/** Lista compacta con buscador y selección múltiple: solo nombres; tocar el nombre despliega el detalle. */
export function SelectorPruebas({ catalogo, seleccion, onChange, deshabilitado = false }: {
  catalogo: PruebaPsicometrica[];
  seleccion: number[];
  onChange: (ids: number[]) => void;
  deshabilitado?: boolean;
}) {
  const [q, setQ] = useState("");
  const [abierta, setAbierta] = useState<number | null>(null);
  const visibles = useMemo(() => {
    const t = q.trim().toLowerCase();
    return t ? catalogo.filter((p) => `${p.nombre} ${p.idProveedor} ${p.tipoTexto}`.toLowerCase().includes(t)) : catalogo;
  }, [catalogo, q]);
  const alternar = (id: number) => onChange(seleccion.includes(id) ? seleccion.filter((x) => x !== id) : [...seleccion, id]);

  return (
    <div className="rounded-xl border border-border-soft bg-surface">
      <label className="flex items-center gap-2 border-b border-border-faint px-3 py-2">
        <Search className="h-3.5 w-3.5 shrink-0 text-ink-3" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar prueba o batería…" aria-label="Buscar prueba o batería"
          className="min-w-0 flex-1 bg-transparent text-sm outline-none" />
        {seleccion.length > 0 && <span className="shrink-0 text-[11px] text-ink-3">{seleccion.length} elegida{seleccion.length === 1 ? "" : "s"}</span>}
      </label>
      {catalogo.length === 0 ? (
        <p className="px-3 py-3 text-xs text-ink-3">El catálogo está vacío. Agrégalas en Configuración → Pruebas psicométricas.</p>
      ) : visibles.length === 0 ? (
        <p className="px-3 py-3 text-xs text-ink-3">Sin coincidencias.</p>
      ) : (
        <ul className="max-h-64 divide-y divide-border-faint overflow-y-auto">
          {visibles.map((p) => (
            <li key={p.id} className="px-3 py-1.5">
              <div className="flex items-center gap-2">
                <input type="checkbox" className="h-4 w-4 shrink-0 rounded accent-brand" checked={seleccion.includes(p.id)} disabled={deshabilitado}
                  onChange={() => alternar(p.id)} aria-label={`Elegir ${p.nombre}`} />
                <button type="button" onClick={() => setAbierta(abierta === p.id ? null : p.id)}
                  className="flex min-w-0 flex-1 items-center justify-between gap-2 py-1 text-left text-sm text-ink">
                  <span className="truncate">{p.nombre}</span>
                  <ChevronDown className={cn("h-3.5 w-3.5 shrink-0 text-ink-3 transition", abierta === p.id && "rotate-180")} />
                </button>
              </div>
              {abierta === p.id && (
                <p className="pb-1.5 pl-6 text-[11px] leading-relaxed text-ink-3">
                  {p.tipoTexto} · Identificador en el proveedor: {p.idProveedor || "—"}{p.proveedor ? ` · ${p.proveedor}` : ""}
                  {p.descripcion ? <><br />{p.descripcion}</> : null}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function VistaAsignarPsicometria({ c, pasoId, onListo, onExterna, onCancelar }: {
  c: Candidato;
  pasoId?: string;
  onListo: (r: RespuestaEvaluacion, texto: string) => void;
  onExterna: () => void;
  onCancelar: () => void;
}) {
  const [vista, setVista] = useState<VistaPsicometria | null>(null);
  const [errorCarga, setErrorCarga] = useState("");
  const [seleccion, setSeleccion] = useState<number[]>([]);
  const [cambiando, setCambiando] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState("");
  const candado = useRef(false);

  useEffect(() => {
    let vivo = true;
    fetchVistaPsicometria(c.id, pasoId ?? "").then((v) => {
      if (!vivo) return;
      if (!v) return setErrorCarga("No se pudo cargar la configuración de la psicometría.");
      setVista(v);
      setSeleccion(v.seleccion);
      if (!v.seleccion.length) setCambiando(true);
    });
    return () => { vivo = false; };
  }, [c.id, pasoId]);

  const porId = useMemo(() => new Map((vista?.catalogo ?? []).map((p) => [p.id, p])), [vista]);
  const elegidas = seleccion.map((id) => porId.get(id)).filter((p): p is PruebaPsicometrica => Boolean(p));
  const cambiada = vista ? JSON.stringify(seleccion) !== JSON.stringify(vista.seleccion) : false;
  const bloqueo = !vista ? "" : !vista.consentimiento ? "Falta el consentimiento de privacidad del candidato (LFPDPPP)."
    : vista.conectado && vista.faltaCorreo ? "El proveedor necesita el correo del candidato: captúralo en su ficha." : "";

  async function asignar() {
    if (candado.current) return; // doble clic: la segunda pulsación no llega a la API
    if (!elegidas.length) return setError("Elige la batería o al menos una prueba del catálogo.");
    candado.current = true;
    setEnviando(true);
    setError("");
    const r = await asignarPsicometria(c.id, { pruebaIds: seleccion, pasoId });
    setEnviando(false);
    if (!r.ok) {
      candado.current = false;
      return setError(r.error);
    }
    const ev = r.data.evaluacion;
    const env = r.data.envioCandidato;
    const texto = r.data.simulado
      ? `«${ev.nombre}» asignada en modo simulado: ${r.data.aviso ?? "sin conexión con el proveedor."}`
      : `«${ev.nombre}» asignada en el proveedor (clave ${ev.claveProveedor}). ${env?.enviado
        ? `Aviso enviado al candidato por ${(env.canales ?? []).join(" y ")}.`
        : `El aviso al candidato no salió${env?.detalle ? `: ${env.detalle}` : ""}; usa «Reenviar» en la tarjeta.`}`;
    onListo(r.data, texto);
  }

  if (errorCarga) return <p role="alert" className="text-sm font-semibold text-bad">{errorCarga}</p>;
  if (!vista) return <Loader2 className="h-5 w-5 animate-spin text-ink-3" />;

  return (
    <div className="flex flex-col gap-4">
      <section className="rounded-2xl border border-border-soft p-4">
        <div className="flex items-start gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand"><Brain className="h-4 w-4" /></span>
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-3">
              {cambiada ? "Selección para este candidato" : vista.origen === "vacante" ? "Batería de la vacante" : vista.origen === "ruta" ? "Batería de la ruta" : "Batería"}
            </p>
            {elegidas.length ? (
              <p className="mt-0.5 text-base font-semibold text-ink">{elegidas.map((p) => p.nombre).join(" + ")}</p>
            ) : (
              <p className="mt-0.5 text-sm text-ink-2">No hay batería configurada. Elige una del catálogo.</p>
            )}
            {vista.noDisponibles > 0 && !cambiada && (
              <p className="mt-1 text-[11px] text-warn">{vista.noDisponibles} prueba(s) de la configuración ya no están activas en el catálogo.</p>
            )}
            {!vista.asignada && (
              <button type="button" onClick={() => setCambiando((x) => !x)} disabled={enviando}
                className="mt-1.5 text-[12px] font-semibold text-brand hover:underline">
                {cambiando ? "Listo" : "Cambiar selección"}
              </button>
            )}
          </div>
        </div>
        {cambiando && !vista.asignada && (
          <div className="mt-3">
            <SelectorPruebas catalogo={vista.catalogo} seleccion={seleccion} onChange={setSeleccion} deshabilitado={enviando} />
            {cambiada && <p className="mt-1.5 text-[11px] text-ink-3">El cambio aplica solo a este candidato; la ruta y la vacante no cambian.</p>}
          </div>
        )}
      </section>

      {vista.asignada && (
        <p className="flex flex-wrap items-center gap-2 rounded-xl border border-border-soft bg-surface-2/50 px-3.5 py-2.5 text-[12px] text-ink-2">
          Ya se asignó y envió ({vista.asignada.codigo}).
          {vista.asignada.estadoProveedorTexto && <Badge tone="neutral">{vista.asignada.estadoProveedorTexto}</Badge>}
          Para volver a avisar al candidato usa «Reenviar» o «Recordatorio» en su tarjeta.
        </p>
      )}
      {!vista.conectado && (
        <p className="text-[12px] text-ink-3">Este servidor no tiene las llaves del proveedor: la asignación se registra en modo simulado y no se avisa al candidato.</p>
      )}
      {bloqueo && (
        <p className="flex items-start gap-2 rounded-xl border border-warn/30 bg-warn-soft/50 px-3.5 py-2.5 text-[12px] text-warn">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {bloqueo}
        </p>
      )}
      {error && <p role="alert" className="text-sm font-semibold text-bad">{error}</p>}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <button type="button" onClick={onExterna} disabled={enviando} className="text-[12px] font-semibold text-ink-2 hover:text-ink hover:underline">
          Agregar prueba externa
        </button>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onCancelar} disabled={enviando}>Cancelar</Button>
          <Button onClick={asignar} disabled={enviando || Boolean(vista.asignada) || Boolean(bloqueo) || !elegidas.length}>
            {enviando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} {enviando ? "Asignando…" : "Asignar y enviar"}
          </Button>
        </div>
      </div>
    </div>
  );
}
