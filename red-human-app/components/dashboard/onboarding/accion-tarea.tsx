"use client";

/* Acción directa de una tarea de Onboarding (2026-10-08) — la MISMA desde la fila de la ficha y desde el botón
   principal de la ruta, sin ir al tablero de Onboarding:
   - «Confirmar ingreso»: fecha real de llegada (no futura).
   - «Registrar alta IMSS / nómina» y demás tareas internas: registro MANUAL (no hay integración con IMSS ni nómina).
   - «Adjuntar contrato firmado»: PDF de la firma física / manual; es el MISMO contrato de Contratación. */

import { useRef, useState } from "react";
import { Check, FileSignature, Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui";
import { CampoRH, ModalMarco, inputRH } from "@/components/dashboard/modulos-rh";
import { cambiarTareaOnboarding, confirmarIngresoOnboarding, subirContratoFirmado, type TareaOnboarding } from "@/lib/api";
import { hoyLocal } from "@/lib/fechas";

export function ModalAccionTarea({ expedienteId, tarea, onClose, onHecho }: {
  expedienteId: number;
  tarea: TareaOnboarding;
  onClose: () => void;
  onHecho: (mensaje: string) => void;
}) {
  const accion = tarea.accion;
  const [fecha, setFecha] = useState(hoyLocal());
  const [notas, setNotas] = useState("");
  const [archivo, setArchivo] = useState<File | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  if (!accion) return null;

  async function guardar() {
    if (!accion) return;
    setOcupado(true);
    setError("");
    let r: { ok: true } | { ok: false; error: string };
    let mensaje = "";
    if (accion.clave === "confirmar_ingreso") {
      if (!fecha) {
        setOcupado(false);
        return setError("Indica la fecha real de ingreso.");
      }
      r = await confirmarIngresoOnboarding(expedienteId, fecha);
      mensaje = "Ingreso confirmado.";
    } else if (accion.clave === "contrato_firmado") {
      if (!archivo) {
        setOcupado(false);
        return setError("Selecciona el PDF del contrato firmado.");
      }
      r = await subirContratoFirmado(expedienteId, archivo);
      mensaje = "Contrato firmado guardado en el expediente.";
    } else {
      r = await cambiarTareaOnboarding(tarea.id, { estado: "realizada", notas: notas.trim() || undefined });
      mensaje = `«${tarea.nombre}» registrada.`;
    }
    setOcupado(false);
    if (!r.ok) return setError(r.error);
    onHecho(mensaje);
  }

  const subtitulo = accion.clave === "confirmar_ingreso"
    ? "Registra el día en que la persona llegó. Los plazos pendientes se recalculan con esa fecha."
    : accion.clave === "contrato_firmado"
      ? "Sube el PDF del contrato firmado (firma física o manual). Es el mismo contrato de Contratación: no se duplica."
      : "Registro manual: confirma que la tarea ya se hizo. Red Human no se conecta con el IMSS ni con la nómina.";

  return (
    <ModalMarco titulo={accion.texto} subtitulo={subtitulo} onClose={onClose} ancho="max-w-md">
      {accion.clave === "confirmar_ingreso" && (
        <CampoRH label="Fecha real de ingreso">
          <input type="date" value={fecha} max={hoyLocal()} onChange={(e) => setFecha(e.target.value)} className={inputRH} autoFocus />
        </CampoRH>
      )}
      {accion.clave === "contrato_firmado" && (
        <div>
          <input ref={input} type="file" accept="application/pdf" className="hidden" onChange={(e) => setArchivo(e.target.files?.[0] ?? null)} />
          <Button size="sm" variant="outline" onClick={() => input.current?.click()}>
            <Upload className="h-4 w-4" /> {archivo ? archivo.name : "Elegir PDF firmado"}
          </Button>
        </div>
      )}
      {accion.clave === "registrar_tarea" && (
        <CampoRH label="Nota (opcional)">
          <input value={notas} onChange={(e) => setNotas(e.target.value)} className={inputRH}
            placeholder={tarea.clave === "alta_imss_nomina" ? "p. ej. NSS dado de alta el día de ingreso" : "Detalle opcional"} />
        </CampoRH>
      )}
      {error && <p className="mt-3 text-sm font-semibold text-bad">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onClose}>Volver</Button>
        <Button size="sm" onClick={() => void guardar()} disabled={ocupado}>
          {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : accion.clave === "contrato_firmado" ? <FileSignature className="h-4 w-4" /> : <Check className="h-4 w-4" />}
          {accion.texto}
        </Button>
      </div>
    </ModalMarco>
  );
}
