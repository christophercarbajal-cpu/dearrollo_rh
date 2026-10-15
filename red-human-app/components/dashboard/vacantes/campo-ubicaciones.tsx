"use client";

/* Cambio 47 (2026-10-14): ubicaciones de la vacante (muchos-a-muchos). Con más de una, el candidato elige en cuál
   trabajar («¿En qué terminal te gustaría trabajar?») en la solicitud web o en el prefiltro de WhatsApp; esa elección
   define el traslado, la dirección de las citas y la autoagenda. Las ubicaciones se dan de alta en Configuración. */

import { useEffect, useState } from "react";
import { fetchUbicaciones, type Ubicacion } from "@/lib/api";

export function CampoUbicaciones({ valor, onChange }: { valor: number[]; onChange: (ids: number[]) => void }) {
  const [ubicaciones, setUbicaciones] = useState<Ubicacion[] | null>(null);

  useEffect(() => {
    let vivo = true;
    fetchUbicaciones().then((u) => vivo && setUbicaciones(u ?? []));
    return () => {
      vivo = false;
    };
  }, []);

  if (!ubicaciones?.length) return null;
  const alternar = (id: number) => onChange(valor.includes(id) ? valor.filter((x) => x !== id) : [...valor, id]);

  return (
    <fieldset className="flex flex-col gap-1.5 text-xs text-ink-2 sm:col-span-2">
      <legend className="mb-1 font-medium">Ubicaciones de la vacante</legend>
      <div className="flex flex-wrap gap-2">
        {ubicaciones.map((u) => (
          <label key={u.id} className="flex min-h-11 items-center gap-2 rounded-lg border border-border-soft bg-surface px-3 py-2 text-sm text-ink">
            <input type="checkbox" className="h-4 w-4 rounded accent-brand" checked={valor.includes(u.id)} onChange={() => alternar(u.id)} />
            <span>
              {u.nombre}
              {u.horariosTexto && <span className="block text-[11px] text-ink-3">{u.horariosTexto}</span>}
            </span>
          </label>
        ))}
      </div>
      <span className="text-[11px] text-ink-3">
        {valor.length > 1
          ? "Con varias ubicaciones, el candidato elige en cuál quiere trabajar al postularse."
          : "Elige una o varias. Sus horarios y cupos son los que usa la autoagenda."}
      </span>
    </fieldset>
  );
}
