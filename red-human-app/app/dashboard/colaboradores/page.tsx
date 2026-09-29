"use client";

import { useCallback, useEffect, useState } from "react";
import { Briefcase, Building2, CalendarClock, ExternalLink, Mail, MapPin, Phone, ShieldCheck, Users, UserSquare2 } from "lucide-react";
import { Card, Badge, Avatar, Eyebrow, Button } from "@/components/ui";
import { FileSpreadsheet, UserPlus } from "lucide-react";
import { ModalAltaColaborador, ModalImportarColaboradores } from "@/components/dashboard/colaboradores/alta-importar";
import { AvisoLinea, type AvisoRH } from "@/components/dashboard/modulos-rh";
import { PageHeader } from "@/components/dashboard/parts";
import { fetchClientesColaboradores, fetchColaboradores, type Colaborador } from "@/lib/api";
import { usePolling } from "@/lib/use-polling";
import { PerfilColaborador } from "@/components/dashboard/perfil-colaborador";
import { usePuedeDecidir } from "@/components/sesion";
import { textoDia } from "@/lib/fechas";

export default function Colaboradores() {
  const [datos, setDatos] = useState<Colaborador[]>([]);
  const [cargando, setCargando] = useState(true);
  const [live, setLive] = useState(false);
  // Fase 5: filtro superior por Cliente ("" = todos; 0 = directo sin Cliente)
  const [clientes, setClientes] = useState<{ id: number; nombre: string; colaboradores: number }[]>([]);
  const [clienteId, setClienteId] = useState<string>("");
  // 2026-09-15: tarjeta clickeable → perfil detallado con Baja / Eliminar
  const [sel, setSel] = useState<Colaborador | null>(null);
  const puedeDecidir = usePuedeDecidir();
  const [modal, setModal] = useState<"" | "alta" | "importar">("");
  const [aviso, setAviso] = useState<AvisoRH>(null);

  const recargar = useCallback(async () => {
    const d = await fetchColaboradores(undefined, clienteId === "" ? null : Number(clienteId));
    if (d) {
      setDatos(d);
      setLive(true);
    }
    setCargando(false);
  }, [clienteId]);

  useEffect(() => {
    recargar();
  }, [recargar]);
  useEffect(() => {
    fetchClientesColaboradores().then((c) => setClientes(c ?? []));
  }, [datos.length]);
  usePolling(recargar, undefined, sel === null);

  const activos = datos.filter((c) => c.activo).length;
  const clienteActual = clientes.find((c) => String(c.id) === clienteId);

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
      <PageHeader
        title="Colaboradores"
        subtitle="Base maestra de personas: llegan al cerrar el Onboarding o por alta manual / importación de empleados existentes."
      >
        {puedeDecidir && (
          <>
            <button onClick={() => setModal("importar")} title="Importar colaboradores (Excel o CSV)" aria-label="Importar colaboradores" className="grid h-9 w-9 place-items-center rounded-xl border border-border-soft text-ink-2 transition hover:border-brand/50 hover:text-brand">
              <FileSpreadsheet className="h-4 w-4" />
            </button>
            <Button size="sm" onClick={() => setModal("alta")}><UserPlus className="h-4 w-4" /> Alta manual</Button>
          </>
        )}
        {live && (
          <Badge tone="good" dot>
            API en vivo
          </Badge>
        )}
        <Badge tone="brand" dot>
          {activos} activo{activos !== 1 ? "s" : ""}
        </Badge>
      </PageHeader>

      {aviso && <AvisoLinea aviso={aviso} onCerrar={() => setAviso(null)} />}
      {modal === "alta" && (
        <ModalAltaColaborador onClose={() => setModal("")} onCreado={(c) => { setModal(""); setAviso({ tono: "ok", texto: `${c.nombre} quedó dado de alta (${c.id}).` }); void recargar(); }} />
      )}
      {modal === "importar" && (
        <ModalImportarColaboradores onClose={() => setModal("")} onListo={(texto) => { setModal(""); setAviso({ tono: "ok", texto }); void recargar(); }} />
      )}

      {/* Fase 5: filtro por Cliente — solo aparece cuando la Cuenta contrata para Clientes */}
      {clientes.length > 0 && (
        <div className="mt-5 flex flex-wrap items-center gap-3 rounded-2xl border border-border-soft bg-surface px-4 py-3">
          <Briefcase className="h-4 w-4 text-ink-3" />
          <label className="flex items-center gap-2 text-sm text-ink-2">
            Cliente
            <select
              value={clienteId}
              onChange={(e) => {
                setCargando(true);
                setClienteId(e.target.value);
              }}
              className="h-10 min-w-[220px] rounded-xl border border-border-soft bg-surface px-3 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20"
            >
              <option value="">Todos los clientes</option>
              {clientes.map((c) => (
                <option key={c.id} value={String(c.id)}>
                  {c.nombre} ({c.colaboradores})
                </option>
              ))}
            </select>
          </label>
          {clienteActual && (
            <span className="text-xs text-ink-3">
              Mostrando {datos.length} colaborador{datos.length !== 1 ? "es" : ""} de {clienteActual.nombre}.
            </span>
          )}
        </div>
      )}

      {cargando ? (
        <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-40 animate-pulse rounded-2xl border border-border-soft bg-surface-2/60" />
          ))}
        </div>
      ) : datos.length === 0 ? (
        <Card className="mt-6 flex flex-col items-center gap-2 p-12 text-center">
          <UserSquare2 className="h-8 w-8 text-ink-3" />
          <p className="text-sm font-medium text-ink-2">Todavía no hay colaboradores dados de alta.</p>
          <p className="max-w-sm text-xs text-ink-3">
            Aparecen aquí en cuanto RH presiona «Dar de alta como colaborador» al cerrar el expediente de un
            candidato en Onboarding.
          </p>
        </Card>
      ) : (
        <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {datos.map((c) => (
            <Card
              key={c.id}
              hover
              className="flex cursor-pointer flex-col gap-3.5 p-5"
              onClick={() => setSel(c)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && setSel(c)}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-3 min-w-0">
                  <Avatar name={c.nombre} />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold">{c.nombre}</p>
                    <p className="truncate text-xs text-ink-3">{c.puesto || "Puesto sin definir"}</p>
                  </div>
                </div>
                <Badge tone={c.activo ? "good" : "neutral"} dot>
                  {c.estatus}
                </Badge>
              </div>

              <div className="flex flex-wrap gap-2 text-xs">
                {c.salario && (
                  <span className="inline-flex items-center gap-1.5 rounded-lg border border-border-soft bg-surface-2 px-2 py-1 font-mono text-brand">
                    {c.salario}
                  </span>
                )}
                {c.empresa && (
                  <span className="inline-flex items-center gap-1.5 text-ink-2">
                    <Building2 className="h-3.5 w-3.5 text-ink-3" /> {c.empresa}
                  </span>
                )}
                {c.clienteNombre && (
                  <span className="inline-flex items-center gap-1.5 rounded-lg border border-border-soft bg-surface-2 px-2 py-1 text-ink-2">
                    <Briefcase className="h-3.5 w-3.5 text-ink-3" /> {c.clienteNombre}
                  </span>
                )}
                {c.ubicacion && (
                  <span className="inline-flex items-center gap-1.5 text-ink-2">
                    <MapPin className="h-3.5 w-3.5 text-ink-3" /> {c.ubicacion}
                  </span>
                )}
              </div>

              <div className="flex flex-col gap-1 text-xs text-ink-3">
                {c.fechaIngreso && (
                  <span className="flex items-center gap-1.5">
                    <CalendarClock className="h-3.5 w-3.5" />
                    Ingresó {textoDia(c.fechaIngreso)}
                  </span>
                )}
                {c.jefeDirecto && (
                  <span className="flex items-center gap-1.5">
                    <Users className="h-3.5 w-3.5" /> Jefe directo: {c.jefeDirecto}
                  </span>
                )}
                {c.correo && (
                  <span className="flex items-center gap-1.5">
                    <Mail className="h-3.5 w-3.5" /> {c.correo}
                  </span>
                )}
                {c.telefono && (
                  <span className="flex items-center gap-1.5">
                    <Phone className="h-3.5 w-3.5" /> {c.telefono}
                  </span>
                )}
              </div>

              <div className="mt-auto flex items-center justify-between border-t border-border-faint pt-3 text-[11px] text-ink-3">
                <span className="font-mono">Folio {c.id}</span>
                <span className="flex items-center gap-1.5">
                  <ShieldCheck className="h-3.5 w-3.5 text-human" /> Alta por {c.dadoDeAltaPor || "RH"}
                </span>
              </div>

              {c.candidatoOrigenId && (
                <a
                  href="/dashboard/candidatos"
                  onClick={(e) => e.stopPropagation()}
                  className="flex items-center gap-1 self-start text-[11px] text-brand transition hover:underline"
                >
                  <ExternalLink className="h-3 w-3" /> Candidato de origen: {c.candidatoOrigenId}
                </a>
              )}
            </Card>
          ))}
        </div>
      )}

      {sel && (
        <PerfilColaborador
          colaborador={sel}
          puedeDecidir={puedeDecidir}
          onClose={() => setSel(null)}
          onCambio={(actualizado) => {
            setDatos((prev) => prev.map((x) => (x.id === actualizado.id ? { ...x, ...actualizado } : x)));
          }}
          onEliminado={() => {
            setSel(null);
            recargar();
          }}
        />
      )}
    </div>
  );
}
