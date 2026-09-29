"use client";

/* 2026-09-15 — Perfil detallado del colaborador (panel lateral): datos de alta y condiciones, expediente con
   sus documentos, candidato de origen, y las dos acciones administrativas con confirmación:
   «Dar de baja» (activo=false; conserva historial; reversible) y «Eliminar» (baja lógica total para limpiar
   registros de prueba; desaparece de listados y conteos, la fila se conserva). */

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Briefcase,
  Building2,
  CalendarClock,
  ExternalLink,
  FileText,
  Mail,
  MapPin,
  Phone,
  RotateCcw,
  ShieldCheck,
  Trash2,
  UserMinus,
  Users,
  X,
} from "lucide-react";
import { Avatar, Badge, Button, Card, Eyebrow } from "@/components/ui";
import { Aviso } from "@/components/dashboard/subida";
import { ModalEditarColaborador } from "@/components/dashboard/colaboradores/alta-importar";
import { HistorialDesempeno } from "@/components/dashboard/desempeno/cierre-evaluacion";
import {
  darDeBajaColaborador,
  fetchHistorialDesempenoColaborador,
  eliminarColaborador,
  fetchColaborador,
  reactivarColaborador,
  urlDocumento,
  type Colaborador,
  type ColaboradorDetalle,
  type EvaluacionDesempeno,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { textoDia, textoFecha } from "@/lib/fechas";

const TONO_DOC: Record<string, string> = {
  recibido: "bg-good-soft text-good",
  revision: "bg-warn-soft text-warn",
  rechazado: "bg-bad-soft text-bad",
  pendiente: "bg-surface-2 text-ink-3",
};

function Dato({ etiqueta, valor }: { etiqueta: string; valor?: string | null }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] uppercase tracking-wide text-ink-3">{etiqueta}</p>
      <p className="truncate text-sm text-ink">{valor || "—"}</p>
    </div>
  );
}

export function PerfilColaborador({
  colaborador,
  puedeDecidir,
  onClose,
  onCambio,
  onEliminado,
}: {
  colaborador: Colaborador;
  puedeDecidir: boolean;
  onClose: () => void;
  /** Se llama con el perfil actualizado tras baja/reactivar. */
  onCambio: (c: ColaboradorDetalle) => void;
  onEliminado: () => void;
}) {
  const [detalle, setDetalle] = useState<ColaboradorDetalle | null>(null);
  const [cargando, setCargando] = useState(true);
  const [confirmar, setConfirmar] = useState<null | "baja" | "eliminar">(null);
  const [motivo, setMotivo] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState("");
  const [aviso, setAviso] = useState("");
  const [editar, setEditar] = useState(false);
  const [pestana, setPestana] = useState<"datos" | "desempeno">("datos");
  const [desempeno, setDesempeno] = useState<EvaluacionDesempeno[] | null>(null);
  useEffect(() => {
    if (pestana === "desempeno" && desempeno === null) fetchHistorialDesempenoColaborador(colaborador.id).then((d) => setDesempeno(d ?? []));
  }, [pestana, desempeno, colaborador.id]);

  useEffect(() => {
    let vivo = true;
    fetchColaborador(colaborador.id).then((d) => {
      if (!vivo) return;
      setDetalle(d);
      setCargando(false);
    });
    return () => {
      vivo = false;
    };
  }, [colaborador.id]);

  const c = detalle ?? (colaborador as ColaboradorDetalle);

  async function baja() {
    setOcupado(true);
    setError("");
    const r = await darDeBajaColaborador(c.id, motivo);
    setOcupado(false);
    if (!r.ok) return setError(r.error);
    setConfirmar(null);
    setMotivo("");
    setDetalle(r.data);
    setAviso("Colaborador dado de baja. Su historial se conserva y puede reactivarse.");
    onCambio(r.data);
  }

  async function reactivar() {
    setOcupado(true);
    setError("");
    const r = await reactivarColaborador(c.id);
    setOcupado(false);
    if (!r.ok) return setError(r.error);
    setDetalle(r.data);
    setAviso("Colaborador reactivado.");
    onCambio(r.data);
  }

  async function eliminar() {
    setOcupado(true);
    setError("");
    const r = await eliminarColaborador(c.id);
    setOcupado(false);
    if (!r.ok) return setError(r.error);
    setConfirmar(null);
    onEliminado();
  }

  const exp = c.expediente;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div className="relative flex h-full w-full max-w-2xl flex-col overflow-y-auto border-l border-border-soft bg-bg shadow-2xl">
        <div className="glass sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-border-soft px-6 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <Avatar name={c.nombre} />
            <div className="min-w-0">
              <Eyebrow>{c.id}</Eyebrow>
              <h2 className="font-display truncate text-xl font-bold">{c.nombre}</h2>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Badge tone={c.activo ? "good" : "neutral"} dot>
              {c.activo ? "Activo" : "Baja"}
            </Badge>
            <button onClick={onClose} className="grid h-9 w-9 place-items-center rounded-xl text-ink-2 hover:bg-surface-2" aria-label="Cerrar">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        <div className="scroll-x flex gap-1 border-b border-border-soft px-6" role="tablist">
          {([["datos", "Datos"], ["desempeno", "Desempeño"]] as const).map(([k, t]) => (
            <button key={k} role="tab" aria-selected={pestana === k} onClick={() => setPestana(k)}
              className={cn("border-b-2 px-3 py-2.5 text-sm font-semibold", pestana === k ? "border-brand text-ink" : "border-transparent text-ink-3 hover:text-ink")}>
              {t}
            </button>
          ))}
        </div>

        {pestana === "desempeno" && (
          <div className="p-6">
            {desempeno === null ? <div className="h-16 animate-pulse rounded-xl bg-surface-2/60" /> : (
              <HistorialDesempeno evaluaciones={desempeno} vacio="Todavía no tiene evaluaciones de desempeño." />
            )}
          </div>
        )}

        <div className={cn("flex flex-col gap-5 p-6", pestana !== "datos" && "hidden")}>
          {aviso && <Aviso tono="ok" onCerrar={() => setAviso("")}>{aviso}</Aviso>}
          {error && !confirmar && <Aviso tono="error" onCerrar={() => setError("")}>{error}</Aviso>}
          {!c.activo && (
            <Aviso tono="warn">
              Dado de baja{c.bajaPor ? ` por ${c.bajaPor}` : ""}
              {c.bajaEn ? ` el ${textoFecha(c.bajaEn)}` : ""}
              {c.bajaMotivo ? ` — motivo: ${c.bajaMotivo}` : ""}. El historial se conserva.
            </Aviso>
          )}

          {/* Acciones administrativas */}
          {puedeDecidir && (
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setEditar(true)} disabled={ocupado}>Editar datos</Button>
              {c.activo ? (
                <Button variant="outline" size="sm" onClick={() => setConfirmar("baja")} disabled={ocupado}>
                  <UserMinus className="h-4 w-4" /> Dar de baja
                </Button>
              ) : (
                <Button variant="outline" size="sm" onClick={reactivar} disabled={ocupado}>
                  <RotateCcw className="h-4 w-4" /> Reactivar
                </Button>
              )}
              <Button variant="outline" size="sm" className="border-bad/40 text-bad hover:bg-bad-soft" onClick={() => setConfirmar("eliminar")} disabled={ocupado}>
                <Trash2 className="h-4 w-4" /> Eliminar
              </Button>
            </div>
          )}

          {/* Datos del puesto */}
          <Card className="p-5">
            <Eyebrow>Puesto y condiciones</Eyebrow>
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              <Dato etiqueta="Puesto" valor={c.puesto} />
              <Dato etiqueta="Sueldo" valor={c.salario} />
              <Dato etiqueta="Tipo de contratación" valor={c.tipoContratacion} />
              <Dato etiqueta="Fecha de ingreso" valor={textoDia(c.fechaIngreso)} />
              <Dato etiqueta="Ubicación" valor={c.ubicacion} />
              <Dato etiqueta="Jefe(a) directo(a)" valor={c.jefeDirecto ? `${c.jefeDirecto}${c.jefeId ? ` (${c.jefeId})` : ""}` : ""} />
              <Dato etiqueta="Área" valor={c.area} />
              <Dato etiqueta="Empresa" valor={c.empresa} />
              <Dato etiqueta="Cliente" valor={c.clienteNombre ?? "Directo (sin Cliente)"} />
            </div>
            {c.instruccionesIngreso && (
              <div className="mt-4 rounded-xl border border-border-soft bg-surface-2/50 p-3 text-sm text-ink-2">
                <p className="text-[11px] uppercase tracking-wide text-ink-3">Instrucciones de ingreso</p>
                <p className="mt-1 whitespace-pre-wrap">{c.instruccionesIngreso}</p>
              </div>
            )}
          </Card>

          {/* Contacto */}
          <Card className="p-5">
            <Eyebrow>Contacto</Eyebrow>
            <div className="mt-3 flex flex-col gap-2 text-sm text-ink-2">
              <span className="flex items-center gap-2"><Mail className="h-4 w-4 text-ink-3" /> {c.correo || "—"}</span>
              <span className="flex items-center gap-2"><Phone className="h-4 w-4 text-ink-3" /> {c.telefono || "—"}</span>
              <span className="flex items-center gap-2"><MapPin className="h-4 w-4 text-ink-3" /> {c.ubicacion || "—"}</span>
              {c.vacante && (
                <span className="flex items-center gap-2"><Briefcase className="h-4 w-4 text-ink-3" /> Vacante: {c.vacante.titulo} ({c.vacante.codigo})</span>
              )}
              {c.clienteNombre && (
                <span className="flex items-center gap-2"><Building2 className="h-4 w-4 text-ink-3" /> {c.clienteNombre}</span>
              )}
            </div>
          </Card>

          {/* Expediente */}
          <Card className="p-5">
            <div className="flex items-center justify-between gap-2">
              <Eyebrow>Expediente</Eyebrow>
              {exp && (
                <span className="font-mono text-[11px] text-ink-3">
                  {exp.progreso}% · {exp.estado === "alta" ? "alta autorizada" : exp.estado}
                </span>
              )}
            </div>
            {cargando ? (
              <div className="mt-3 h-16 animate-pulse rounded-xl bg-surface-2/60" />
            ) : exp ? (
              <ul className="mt-3 divide-y divide-border-faint">
                {exp.documentos.map((d) => (
                  <li key={d.nombre} className="flex items-center gap-3 py-2 text-sm">
                    <FileText className="h-4 w-4 shrink-0 text-ink-3" />
                    <span className="min-w-0 flex-1 truncate">{d.nombre}</span>
                    <span className={cn("rounded-md px-2 py-0.5 font-mono text-[10px] uppercase", TONO_DOC[d.estado] ?? TONO_DOC.pendiente)}>
                      {d.estado}
                    </span>
                    {d.tieneArchivo && c.expedienteId != null && (
                      <a href={urlDocumento(c.expedienteId, d.nombre)} target="_blank" rel="noreferrer" className="text-ink-3 hover:text-brand" aria-label={`Abrir ${d.nombre}`}>
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-sm text-ink-3">Sin expediente ligado.</p>
            )}
          </Card>

          {/* Origen y alta */}
          <Card className="p-5">
            <Eyebrow>Origen y alta</Eyebrow>
            <div className="mt-3 flex flex-col gap-1.5 text-xs text-ink-3">
              <span className="flex items-center gap-1.5"><ShieldCheck className="h-3.5 w-3.5 text-human" /> Alta autorizada por {c.altaAutorizadaPor || c.dadoDeAltaPor || "RH"}{c.altaFecha ? ` el ${textoFecha(c.altaFecha)}` : ""}</span>
              {c.candidatoOrigen && (
                <span className="flex items-center gap-1.5">
                  <Users className="h-3.5 w-3.5" /> Candidato de origen: {c.candidatoOrigen.codigo} · fuente {c.candidatoOrigen.fuente}
                  {c.candidatoOrigen.eliminado ? " (candidato eliminado)" : ""}
                </span>
              )}
              <span className="flex items-center gap-1.5"><CalendarClock className="h-3.5 w-3.5" /> Registro {c.creado}</span>
            </div>
          </Card>
        </div>

        {editar && detalle && (
          <ModalEditarColaborador
            colaborador={detalle}
            onClose={() => setEditar(false)}
            onGuardado={(d) => { setEditar(false); setDetalle(d); setAviso("Datos actualizados."); onCambio(d); }}
          />
        )}

        {confirmar && (
          <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={() => !ocupado && setConfirmar(null)}>
            <Card className="w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-start gap-3">
                <span className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-full", confirmar === "eliminar" ? "bg-bad-soft text-bad" : "bg-warn-soft text-warn")}>
                  <AlertTriangle className="h-5 w-5" />
                </span>
                <div className="min-w-0 flex-1">
                  {confirmar === "baja" ? (
                    <>
                      <h3 className="font-display text-lg font-bold">¿Dar de baja a {c.nombre}?</h3>
                      <p className="mt-2 text-sm leading-relaxed text-ink-2">
                        Pasará a <b>Inactivo</b>. Se conserva todo su historial (expediente, documentos, bitácora) y se puede reactivar después.
                      </p>
                      <label className="mt-3 flex flex-col gap-1.5">
                        <span className="text-xs font-medium text-ink-2">Motivo (opcional)</span>
                        <input
                          value={motivo}
                          onChange={(e) => setMotivo(e.target.value)}
                          placeholder="Ej. Renuncia voluntaria, fin de contrato…"
                          className="h-10 rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
                        />
                      </label>
                    </>
                  ) : (
                    <>
                      <h3 className="font-display text-lg font-bold">¿Eliminar a {c.nombre}?</h3>
                      <p className="mt-2 text-sm leading-relaxed text-ink-2">
                        Desaparecerá de la lista, de los filtros y de los conteos de Colaboradores. Nada se borra físicamente (queda en bitácora),
                        pero esta acción <b>no se puede deshacer desde la interfaz</b>. Úsala para limpiar registros de prueba o erróneos; para una
                        salida real usa «Dar de baja».
                      </p>
                    </>
                  )}
                  {error && <p className="mt-2 text-sm font-semibold text-bad">{error}</p>}
                </div>
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setConfirmar(null)} disabled={ocupado}>
                  Cancelar
                </Button>
                {confirmar === "baja" ? (
                  <Button size="sm" onClick={baja} disabled={ocupado}>
                    <UserMinus className="h-4 w-4" /> {ocupado ? "Aplicando…" : "Sí, dar de baja"}
                  </Button>
                ) : (
                  <Button size="sm" className="bg-bad text-white hover:bg-bad/90" onClick={eliminar} disabled={ocupado}>
                    <Trash2 className="h-4 w-4" /> {ocupado ? "Eliminando…" : "Sí, eliminar"}
                  </Button>
                )}
              </div>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
