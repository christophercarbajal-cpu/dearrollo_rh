"use client";

/* Cambio 30 (2026-10-13): Capacitación PRESENCIAL. Reutiliza el sistema de citas de la Entrevista humana (mismo
   selector de evaluador y mismos campos de cita), con el campo «Instructor». El candidato recibe «Tienes una
   capacitación programada para [puesto]» y el instructor «Aviso para el instructor. Vas a capacitar a…» (textos del
   servidor). El resultado lo registra el instructor en su liga (o RH); un dictamen desfavorable = «No aprobó el curso». */

import { useState } from "react";
import { CalendarClock } from "lucide-react";
import { Button } from "@/components/ui";
import { ModalMarco } from "@/components/dashboard/modulos-rh";
import {
  CamposCita, SelectorEvaluador, citaEntrada, citaVacia, evaluadorEntrada, evaluadorVacio, useCatalogoEvaluadores, useTeamsConectado,
  validarCita, validarEvaluador, type EstadoCita,
} from "@/components/dashboard/evaluaciones/campos-evaluacion";
import { crearEvaluacion } from "@/lib/api";
import type { Candidato, PasoSeguimiento } from "@/lib/data";

export function ModalCapacitacionPresencial({ c, paso, onClose, onProgramada }: {
  c: Candidato;
  paso: PasoSeguimiento;
  onClose: () => void;
  onProgramada: (aviso: string) => void;
}) {
  const { internos, contactos } = useCatalogoEvaluadores(c.clienteIdVacante ?? null);
  const teams = useTeamsConectado();
  const [instructor, setInstructor] = useState(evaluadorVacio);
  const [cita, setCita] = useState<EstadoCita>({ ...citaVacia, modalidad: "Presencial" });
  const [error, setError] = useState("");
  const [guardando, setGuardando] = useState(false);

  async function guardar() {
    const m = validarEvaluador(instructor).replace("evaluador", "instructor") || validarCita(cita, teams);
    if (m) return setError(m);
    setGuardando(true);
    setError("");
    const r = await crearEvaluacion(c.id, {
      tipo: "otra", nombre: paso.nombre, forma: "asignada", evaluador: evaluadorEntrada(instructor), cita: citaEntrada(cita, teams),
      pasoId: paso.id,
    });
    setGuardando(false);
    if (!r.ok) return setError(r.error);
    onProgramada("Capacitación programada: el candidato y el instructor recibieron su aviso.");
  }

  return (
    <ModalMarco titulo={`Programar ${paso.nombre.toLowerCase()}`} subtitulo="Capacitación presencial: fecha, hora, lugar e instructor."
      onClose={onClose} ancho="sm:max-w-2xl">
      <div className="flex flex-col gap-4">
        <SelectorEvaluador rotulo="Instructor" valor={instructor} onChange={setInstructor} internos={internos} contactos={contactos}
          clienteNombre={c.clienteVacante ?? null} />
        <CamposCita valor={cita} onChange={setCita} teams={teams} rol="instructor" />
        {error && <p className="rounded-xl border border-bad/40 bg-bad-soft px-3 py-2 text-sm font-semibold text-bad">{error}</p>}
        <div className="flex justify-end gap-2 border-t border-border-faint pt-3">
          <Button variant="outline" size="sm" onClick={onClose} disabled={guardando}>Cancelar</Button>
          <Button size="sm" onClick={guardar} disabled={guardando}><CalendarClock className="h-4 w-4" /> {guardando ? "Programando…" : "Programar capacitación"}</Button>
        </div>
      </div>
    </ModalMarco>
  );
}
