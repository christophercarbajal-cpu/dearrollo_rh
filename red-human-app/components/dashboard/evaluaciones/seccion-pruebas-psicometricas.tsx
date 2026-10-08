"use client";

/* Configuración → Pruebas psicométricas (2026-09-28; simplificado 2026-10-07). Catálogo por Cuenta de pruebas y
   baterías del proveedor integrado: Nombre, Tipo, Identificador en el proveedor y Activa. Las rutas («Corporativos con
   psicometría») y las vacantes eligen de aquí su batería predeterminada. «Eliminar» = inactivar; las evaluaciones ya
   asignadas no cambian. Las ligas externas o los resultados manuales van por «Agregar prueba externa» en la ficha. */

import { useCallback, useEffect, useState } from "react";
import { Brain, Loader2, PenLine, Plus, Power, Save } from "lucide-react";
import { Badge, Button, Card } from "@/components/ui";
import { MenuAcciones } from "@/components/dashboard/menu-acciones";
import { AvisoLinea, CampoRH, ModalMarco, inputRH, type AvisoRH } from "@/components/dashboard/modulos-rh";
import {
  TIPOS_PRUEBA, crearPruebaPsicometrica, editarPruebaPsicometrica, fetchPruebasPsicometricas, inactivarPruebaPsicometrica,
  type PruebaPsicometrica, type TipoPrueba,
} from "@/lib/api";

const PROVEEDOR_INTEGRADO = "Psicométricas.mx";

export function SeccionPruebasPsicometricas() {
  const [lista, setLista] = useState<PruebaPsicometrica[] | null>(null);
  const [editor, setEditor] = useState<{ prueba: PruebaPsicometrica | null } | null>(null);
  const [aviso, setAviso] = useState<AvisoRH>(null);

  const recargar = useCallback(async () => setLista((await fetchPruebasPsicometricas(true)) ?? []), []);
  useEffect(() => {
    void recargar();
  }, [recargar]);

  async function alternar(p: PruebaPsicometrica) {
    const r = p.activa ? await inactivarPruebaPsicometrica(p.id) : await editarPruebaPsicometrica(p.id, { activa: true });
    if (!r.ok) return setAviso({ tono: "error", texto: r.error });
    setAviso({ tono: "ok", texto: `«${p.nombre}» ${p.activa ? "inactivada" : "activada"}.` });
    void recargar();
  }

  return (
    <Card id="pruebas-psicometricas" className="mt-4 scroll-mt-24 p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand"><Brain className="h-4 w-4" /></span>
          <div>
            <h2 className="font-display text-base font-bold">Pruebas psicométricas</h2>
            <p className="mt-0.5 text-sm text-ink-2">Pruebas y baterías del proveedor. Las rutas y las vacantes eligen de aquí su batería predeterminada.</p>
          </div>
        </div>
        <Button size="sm" onClick={() => setEditor({ prueba: null })}><Plus className="h-4 w-4" /> Nueva prueba</Button>
      </div>
      {aviso && <AvisoLinea aviso={aviso} onCerrar={() => setAviso(null)} />}
      <div className="mt-4">
        {lista === null ? (
          <Loader2 className="h-5 w-5 animate-spin text-ink-3" />
        ) : lista.length === 0 ? (
          <p className="text-sm text-ink-3">Aún no hay pruebas en el catálogo.</p>
        ) : (
          <ul className="divide-y divide-border-faint">
            {lista.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
                    <span className="truncate">{p.nombre}</span>
                    <Badge tone="neutral">{p.tipoTexto}</Badge>
                    <Badge tone={p.activa ? "good" : "neutral"}>{p.activa ? "Activa" : "Inactiva"}</Badge>
                  </p>
                  <p className="truncate text-[11px] text-ink-3">
                    {p.idProveedor ? `Identificador en el proveedor: ${p.idProveedor}` : "Sin identificador en el proveedor"}
                    {p.modo !== "integrada" ? ` · ${p.modoTexto}` : ""}
                  </p>
                </div>
                <MenuAcciones
                  acciones={[
                    { etiqueta: "Editar", icono: <PenLine className="h-4 w-4" />, onClick: () => setEditor({ prueba: p }) },
                    { etiqueta: p.activa ? "Inactivar" : "Activar", icono: <Power className="h-4 w-4" />, peligrosa: p.activa, onClick: () => void alternar(p) },
                  ]}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
      {editor && (
        <ModalPrueba
          prueba={editor.prueba}
          onClose={() => setEditor(null)}
          onGuardada={(n) => { setEditor(null); setAviso({ tono: "ok", texto: `Prueba «${n}» guardada.` }); void recargar(); }}
        />
      )}
    </Card>
  );
}

/** 2026-10-07 (asignación simplificada): solo Nombre, Tipo (prueba o batería), Identificador en el proveedor y Activa.
 * El identificador interno lo genera la API; las altas nuevas son del proveedor integrado (Psicométricas.mx). Al editar
 * una prueba previa solo se mandan estos cuatro campos: su modo, proveedor, liga y puestos se conservan. */
function ModalPrueba({ prueba, onClose, onGuardada }: { prueba: PruebaPsicometrica | null; onClose: () => void; onGuardada: (n: string) => void }) {
  const [nombre, setNombre] = useState(prueba?.nombre ?? "");
  const [tipo, setTipo] = useState<TipoPrueba>(prueba?.tipo ?? "prueba");
  const [idProveedor, setIdProveedor] = useState(prueba?.idProveedor ?? "");
  const [activa, setActiva] = useState(prueba?.activa ?? true);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState("");

  async function guardar() {
    if (!nombre.trim()) return setError("Captura el nombre.");
    if (!idProveedor.trim()) return setError("Captura el identificador en el proveedor (p. ej. 1,7).");
    setOcupado(true);
    setError("");
    const base = { nombre: nombre.trim(), tipo, id_proveedor: idProveedor.trim(), activa };
    const r = prueba
      ? await editarPruebaPsicometrica(prueba.id, base)
      : await crearPruebaPsicometrica({ ...base, clave: "", descripcion: "", puestos: [], modo: "integrada", proveedor: PROVEEDOR_INTEGRADO, url: "" });
    setOcupado(false);
    if (!r.ok) return setError(r.error);
    onGuardada(r.data.nombre);
  }

  return (
    <ModalMarco titulo={prueba ? "Editar prueba psicométrica" : "Nueva prueba psicométrica"} onClose={onClose}>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <CampoRH label="Nombre"><input value={nombre} onChange={(e) => setNombre(e.target.value)} className={inputRH} placeholder="Batería corporativa (Cleaver + Terman)" autoFocus /></CampoRH>
        </div>
        <CampoRH label="Tipo">
          <select value={tipo} onChange={(e) => setTipo(e.target.value as TipoPrueba)} className={inputRH}>
            {TIPOS_PRUEBA.map((t) => <option key={t.valor} value={t.valor}>{t.texto}</option>)}
          </select>
        </CampoRH>
        <CampoRH label="Identificador en el proveedor" ayuda="ID numérico en Psicométricas.mx; varios separados por coma (1,7).">
          <input value={idProveedor} onChange={(e) => setIdProveedor(e.target.value)} className={inputRH} placeholder="1,7" inputMode="numeric" />
        </CampoRH>
        <label className="flex items-center gap-2 text-sm text-ink-2 sm:col-span-2">
          <input type="checkbox" checked={activa} onChange={(e) => setActiva(e.target.checked)} /> Activa
        </label>
      </div>
      {error && <p className="mt-3 text-sm font-semibold text-bad">{error}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onClose} disabled={ocupado}>Cancelar</Button>
        <Button size="sm" onClick={guardar} disabled={ocupado}>
          {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Guardar prueba
        </Button>
      </div>
    </ModalMarco>
  );
}
