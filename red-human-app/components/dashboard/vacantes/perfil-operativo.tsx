"use client";

/**
 * Entrevista operativa (2026-10-10, Cambio 1): elegir el oficio de la Biblioteca de Perfiles y, si hace falta, ajustar
 * sus 3 datos y su situación SOLO para esta vacante. La biblioteca nunca se edita desde aquí.
 */

import { useEffect, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { Field, Selector } from "@/components/dashboard/campos";
import { fetchPerfilesOperativos, type OficioBiblioteca, type PerfilOperativo, type ProcesoEntrada } from "@/lib/api";

/** ¿La ruta elegida es de tipo Masivos? (entrevista por WhatsApp). Sin ruta elegida, la predeterminada es Masivos. */
export function rutaMasiva(ruta?: ProcesoEntrada): boolean {
  if (!ruta) return true;
  if ("quitar" in ruta) return false;
  return (ruta.pasos ?? []).some((p) => p.tipo === "entrevista_whatsapp");
}

export function PerfilOperativoCampo({ value, detectado, onChange }: {
  value: PerfilOperativo | null;
  detectado: string;
  onChange: (p: PerfilOperativo | null) => void;
}) {
  const [oficios, setOficios] = useState<OficioBiblioteca[]>([]);
  const [ajustar, setAjustar] = useState(false);

  useEffect(() => {
    let vivo = true;
    fetchPerfilesOperativos().then((r) => {
      if (vivo && r) setOficios(r.oficios);
    });
    return () => {
      vivo = false;
    };
  }, []);

  function elegir(oficio: string) {
    if (!oficio) return onChange(null);
    const o = oficios.find((x) => x.oficio === oficio);
    if (o) onChange({ oficio: o.oficio, nombre: o.nombre, datos: [...o.datos], situacion: { pregunta: o.situacion.pregunta } });
  }

  const base = oficios.find((x) => x.oficio === value?.oficio);
  const opciones = [
    { valor: "", texto: detectado ? `Detectar por el puesto (${detectado})` : "Detectar por el puesto" },
    ...oficios.map((o) => ({ valor: o.oficio, texto: o.nombre })),
  ];

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border-soft bg-surface-2/40 p-4">
      <Selector label="Oficio (Biblioteca de Perfiles)" value={value?.oficio ?? ""} onChange={elegir} opciones={opciones} />
      {base && (
        <p className="text-xs leading-relaxed text-ink-3">
          <b className="text-ink-2">No cuenta como experiencia:</b> {base.excluyentes.join(" ")}
        </p>
      )}
      {value && (
        <>
          <button type="button" onClick={() => setAjustar(!ajustar)} className="flex items-center gap-1 self-start text-xs font-semibold text-brand">
            {ajustar ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />} Ajustar para esta vacante
          </button>
          {ajustar && (
            <div className="flex flex-col gap-3">
              {[0, 1, 2].map((i) => (
                <Field
                  key={i}
                  label={`Dato ${i + 1} a verificar`}
                  value={value.datos[i] ?? ""}
                  onChange={(t) => {
                    const datos = [...value.datos];
                    datos[i] = t;
                    onChange({ ...value, datos });
                  }}
                />
              ))}
              <Field
                label="Situación esperada"
                value={value.situacion.pregunta}
                onChange={(t) => onChange({ ...value, situacion: { pregunta: t } })}
                ayuda="Solo cambia esta vacante; la biblioteca no se modifica. Vuelve a generar los guiones para aplicarlo."
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
