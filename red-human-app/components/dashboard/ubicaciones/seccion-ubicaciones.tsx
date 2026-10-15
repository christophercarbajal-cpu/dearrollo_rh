"use client";

/* Configuración → Ubicaciones (Cambio 47, 2026-10-14). Sedes de la Cuenta (terminales, CEDIS, sucursales) con dirección,
   liga de Google Maps (si no se captura, se arma con la dirección), indicaciones para el candidato y horarios de citas con
   cupo por horario. Una vacante puede tener varias; la autoagenda ofrece SOLO horarios con lugar. Eliminar = desactivar:
   las vacantes y citas existentes no cambian. */

import { useCallback, useEffect, useState } from "react";
import { Loader2, MapPin, PenLine, Plus, Save, Trash2 } from "lucide-react";
import { Button, Card } from "@/components/ui";
import { MenuAcciones } from "@/components/dashboard/menu-acciones";
import { AvisoLinea, CampoRH, ModalMarco, inputRH, type AvisoRH } from "@/components/dashboard/modulos-rh";
import {
  crearUbicacion, desactivarUbicacion, editarUbicacion, fetchUbicaciones, type HorarioCitas, type Ubicacion, type UbicacionIn,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const DIAS = ["L", "M", "Mi", "J", "V", "S", "D"];
const DIAS_LARGOS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];
const HORARIO_NUEVO: HorarioCitas = { dias: [0, 1, 2, 3, 4], horas: ["09:00", "12:00"], cupo: 8 };

export function SeccionUbicaciones() {
  const [lista, setLista] = useState<Ubicacion[] | null>(null);
  const [editor, setEditor] = useState<{ ubicacion: Ubicacion | null } | null>(null);
  const [aviso, setAviso] = useState<AvisoRH>(null);

  const recargar = useCallback(async () => setLista((await fetchUbicaciones()) ?? []), []);
  useEffect(() => {
    void recargar();
  }, [recargar]);

  async function desactivar(u: Ubicacion) {
    const r = await desactivarUbicacion(u.id);
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setAviso({ tono: "ok", texto: `«${u.nombre}» desactivada. Las vacantes y citas que ya la usan no cambian.` });
    void recargar();
  }

  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand"><MapPin className="h-4 w-4" /></span>
          <div>
            <h2 className="font-display text-base font-bold">Ubicaciones</h2>
            <p className="mt-0.5 text-sm text-ink-2">
              Sedes donde se trabaja y se cita a los candidatos. Sus horarios y cupos son los que ofrece la autoagenda por WhatsApp.
            </p>
          </div>
        </div>
        <Button size="sm" onClick={() => setEditor({ ubicacion: null })}><Plus className="h-4 w-4" /> Nueva ubicación</Button>
      </div>

      {aviso && <AvisoLinea aviso={aviso} onCerrar={() => setAviso(null)} />}

      <div className="mt-4">
        {lista === null ? (
          <Loader2 className="h-5 w-5 animate-spin text-ink-3" />
        ) : lista.length === 0 ? (
          <p className="text-sm text-ink-3">Sin ubicaciones. Agrega una para asignarla a tus vacantes y habilitar la autoagenda.</p>
        ) : (
          <ul className="divide-y divide-border-faint">
            {lista.map((u) => (
              <li key={u.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold">{u.nombre}</p>
                  <p className="truncate text-[11px] text-ink-3">{u.direccion || "Sin dirección"}</p>
                  <p className="text-[11px] text-ink-3">{u.horariosTexto || "Sin horarios de citas"}</p>
                </div>
                <MenuAcciones
                  acciones={[
                    { etiqueta: "Editar", icono: <PenLine />, onClick: () => setEditor({ ubicacion: u }) },
                    { etiqueta: "Desactivar", icono: <Trash2 />, peligrosa: true, onClick: () => desactivar(u) },
                  ]}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      {editor && (
        <EditorUbicacion
          ubicacion={editor.ubicacion}
          onClose={() => setEditor(null)}
          onGuardada={(u) => {
            setEditor(null);
            setAviso({ tono: "ok", texto: `«${u.nombre}» guardada.` });
            void recargar();
          }}
        />
      )}
    </Card>
  );
}

function EditorUbicacion({ ubicacion, onClose, onGuardada }: { ubicacion: Ubicacion | null; onClose: () => void; onGuardada: (u: Ubicacion) => void }) {
  const [datos, setDatos] = useState<UbicacionIn>({
    nombre: ubicacion?.nombre ?? "",
    direccion: ubicacion?.direccion ?? "",
    mapa: ubicacion?.mapaCapturado ?? "",
    indicaciones: ubicacion?.indicaciones ?? "",
    horarios: ubicacion?.horarios?.length ? ubicacion.horarios : [HORARIO_NUEVO],
  });
  const [horasTexto, setHorasTexto] = useState<string[]>((datos.horarios ?? []).map((h) => h.horas.join(", ")));
  const [error, setError] = useState("");
  const [guardando, setGuardando] = useState(false);
  const horarios = datos.horarios ?? [];

  function cambiarHorario(i: number, c: Partial<HorarioCitas>) {
    setDatos({ ...datos, horarios: horarios.map((h, j) => (j === i ? { ...h, ...c } : h)) });
  }

  async function guardar() {
    setError("");
    if (!(datos.nombre ?? "").trim()) return setError("El nombre es obligatorio.");
    const cuerpo = { ...datos, horarios: horarios.map((h, i) => ({ ...h, horas: (horasTexto[i] ?? "").split(/[,\s]+/).filter(Boolean) })) };
    setGuardando(true);
    const r = ubicacion ? await editarUbicacion(ubicacion.id, cuerpo) : await crearUbicacion(cuerpo);
    setGuardando(false);
    if (!r.ok) return setError(r.error);
    onGuardada(r.data);
  }

  return (
    <ModalMarco titulo={ubicacion ? `Editar «${ubicacion.nombre}»` : "Nueva ubicación"} onClose={onClose}>
      <div className="grid gap-4">
        <CampoRH label="Nombre *">
          <input className={inputRH} value={datos.nombre ?? ""} onChange={(e) => setDatos({ ...datos, nombre: e.target.value })} placeholder="Ej. Terminal Monterrey" />
        </CampoRH>
        <CampoRH label="Dirección">
          <input className={inputRH} value={datos.direccion ?? ""} onChange={(e) => setDatos({ ...datos, direccion: e.target.value })} placeholder="Calle, número, colonia, municipio" />
        </CampoRH>
        <CampoRH label="Liga de Google Maps" ayuda="Opcional: si la dejas vacía se arma con la dirección.">
          <input className={inputRH} value={datos.mapa ?? ""} onChange={(e) => setDatos({ ...datos, mapa: e.target.value })} placeholder="https://maps.app.goo.gl/…" />
        </CampoRH>
        <CampoRH label="Indicaciones para el candidato" ayuda="Opcional. Van en la confirmación de la cita.">
          <textarea className={cn(inputRH, "min-h-[64px] py-2")} value={datos.indicaciones ?? ""}
            onChange={(e) => setDatos({ ...datos, indicaciones: e.target.value })} placeholder="Ej. Presenta tu INE en caseta." />
        </CampoRH>

        <div className="flex flex-col gap-2">
          <span className="text-xs font-medium text-ink-2">Horarios de citas</span>
          {horarios.map((h, i) => (
            <div key={i} className="flex flex-col gap-2 rounded-xl border border-border-soft p-3">
              <div className="flex flex-wrap gap-1.5">
                {DIAS.map((d, n) => (
                  <button key={d} type="button" aria-pressed={h.dias.includes(n)} title={DIAS_LARGOS[n]}
                    onClick={() => cambiarHorario(i, { dias: h.dias.includes(n) ? h.dias.filter((x) => x !== n) : [...h.dias, n].sort() })}
                    className={cn("h-9 min-w-9 rounded-lg border px-2 text-xs font-semibold",
                      h.dias.includes(n) ? "border-brand bg-brand-soft text-brand" : "border-border-soft text-ink-3")}>
                    {d}
                  </button>
                ))}
              </div>
              <div className="grid gap-2 sm:grid-cols-[1fr_120px_auto]">
                <input className={inputRH} aria-label="Horas" value={horasTexto[i] ?? ""} placeholder="09:00, 12:00"
                  onChange={(e) => setHorasTexto(horasTexto.map((x, j) => (j === i ? e.target.value : x)))} />
                <input className={inputRH} aria-label="Lugares por horario" type="number" min={1} value={h.cupo}
                  onChange={(e) => cambiarHorario(i, { cupo: Number(e.target.value) || 0 })} />
                <Button size="sm" variant="ghost" type="button" disabled={horarios.length === 1}
                  onClick={() => {
                    setDatos({ ...datos, horarios: horarios.filter((_, j) => j !== i) });
                    setHorasTexto(horasTexto.filter((_, j) => j !== i));
                  }}>
                  Quitar
                </Button>
              </div>
              <span className="text-[11px] text-ink-3">Horas separadas por coma · lugares por horario</span>
            </div>
          ))}
          <Button size="sm" variant="ghost" type="button" className="self-start"
            onClick={() => {
              setDatos({ ...datos, horarios: [...horarios, HORARIO_NUEVO] });
              setHorasTexto([...horasTexto, HORARIO_NUEVO.horas.join(", ")]);
            }}>
            <Plus className="h-3.5 w-3.5" /> Agregar horario
          </Button>
        </div>

        {error && <p role="alert" className="text-sm text-bad">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={guardar} disabled={guardando}>{guardando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Guardar</Button>
        </div>
      </div>
    </ModalMarco>
  );
}
