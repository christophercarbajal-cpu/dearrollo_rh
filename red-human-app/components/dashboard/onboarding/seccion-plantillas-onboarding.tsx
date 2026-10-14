"use client";

/* Configuración → Plantillas de Onboarding (Onboarding v2, 2026-09-28). Qué documentos se piden, qué recursos
   internos se preparan (correo, equipo, accesos), quién es responsable por defecto, el curso de inducción y los
   plazos RELATIVOS a la fecha de ingreso. Jerarquía: la plantilla de PUESTO prevalece sobre la de EMPRESA; sin
   ninguna aplica la configuración predeterminada. Aplicarla a una persona COPIA la configuración: lo que RH
   cambie para un candidato nunca altera la plantilla. */

import { useCallback, useEffect, useState } from "react";
import { Briefcase, Building2, ClipboardCheck, Loader2, PenLine, Plus, Save, Trash2, X } from "lucide-react";
import { Badge, Button, Card } from "@/components/ui";
import { MenuAcciones } from "@/components/dashboard/menu-acciones";
import { AvisoLinea, CampoRH, ModalMarco, inputRH, type AvisoRH } from "@/components/dashboard/modulos-rh";
import {
  crearPlantillaOnboarding, desactivarPlantillaOnboarding, editarPlantillaOnboarding, fetchOpcionesPlantillaOnboarding,
  fetchPlantillasOnboarding,
  type ClavePlazoOnboarding, type DocumentoPlantillaOnboarding, type OpcionesPlantillaOnboarding, type PlantillaOnboarding,
  type RecursoPlantillaOnboarding, type TipoRecursoOnboarding,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const ETIQUETA_RECURSO: Record<TipoRecursoOnboarding, string> = { correo: "Correo", equipo: "Equipo", accesos: "Accesos", otro: "Otro" };

function textoPlazo(dias: number) {
  if (!dias) return "el día de ingreso";
  return dias < 0 ? `${-dias} día${dias === -1 ? "" : "s"} antes del ingreso` : `${dias} día${dias === 1 ? "" : "s"} después del ingreso`;
}

export function SeccionPlantillasOnboarding() {
  const [lista, setLista] = useState<PlantillaOnboarding[] | null>(null);
  const [opciones, setOpciones] = useState<OpcionesPlantillaOnboarding | null>(null);
  const [editor, setEditor] = useState<{ plantilla: PlantillaOnboarding | null } | null>(null);
  const [baja, setBaja] = useState<PlantillaOnboarding | null>(null);
  const [aviso, setAviso] = useState<AvisoRH>(null);

  const recargar = useCallback(async () => setLista((await fetchPlantillasOnboarding()) ?? []), []);
  useEffect(() => {
    void recargar();
    fetchOpcionesPlantillaOnboarding().then((o) => setOpciones(o ?? null));
  }, [recargar]);

  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand"><ClipboardCheck className="h-4 w-4" /></span>
          <div>
            <h2 className="font-display text-base font-bold">Plantillas de Onboarding</h2>
            <p className="mt-0.5 text-sm text-ink-2">
              Documentos, recursos internos, responsables, curso de inducción y plazos por empresa o por puesto. La de puesto prevalece sobre la de empresa.
            </p>
          </div>
        </div>
        <Button size="sm" onClick={() => setEditor({ plantilla: null })} disabled={!opciones}><Plus className="h-4 w-4" /> Nueva plantilla</Button>
      </div>

      {aviso && <AvisoLinea aviso={aviso} onCerrar={() => setAviso(null)} />}

      <div className="mt-4">
        {lista === null ? (
          <Loader2 className="h-5 w-5 animate-spin text-ink-3" />
        ) : lista.length === 0 ? (
          <p className="text-sm text-ink-3">Sin plantillas: cada Onboarding usa los 6 documentos básicos y las tareas fijas (Contrato firmado, Alta IMSS / nómina, Confirmar ingreso).</p>
        ) : (
          <ul className="divide-y divide-border-faint">
            {lista.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
                    <span className="truncate">{p.nombre}</span>
                    <Badge tone={p.alcance === "puesto" ? "brand" : "neutral"}>
                      {p.alcance === "puesto" ? `Puesto: ${p.puesto}` : "Empresa"}
                    </Badge>
                  </p>
                  <p className="truncate text-[11px] text-ink-3">
                    {p.empresa || "Toda la Cuenta"} · {p.documentos.length} documentos · {p.recursos.length} recursos
                    {p.cursoInduccion ? ` · Inducción: ${p.cursoInduccion}` : ""}
                  </p>
                </div>
                <MenuAcciones
                  acciones={[
                    { etiqueta: "Editar", icono: <PenLine className="h-4 w-4" />, onClick: () => setEditor({ plantilla: p }), disabled: !opciones },
                    { etiqueta: "Eliminar", icono: <Trash2 className="h-4 w-4" />, peligrosa: true, onClick: () => setBaja(p) },
                  ]}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      {editor && opciones && (
        <ModalPlantillaOnboarding
          plantilla={editor.plantilla}
          opciones={opciones}
          onClose={() => setEditor(null)}
          onGuardada={(n) => { setEditor(null); setAviso({ tono: "ok", texto: `Plantilla «${n}» guardada.` }); void recargar(); }}
        />
      )}
      {baja && (
        <ModalMarco titulo="Eliminar plantilla" subtitulo={`«${baja.nombre}» deja de aplicarse. Los Onboardings ya generados con ella no cambian.`} onClose={() => setBaja(null)}>
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setBaja(null)}>Cancelar</Button>
            <Button
              size="sm"
              onClick={async () => {
                const r = await desactivarPlantillaOnboarding(baja.id);
                setBaja(null);
                if (!r.ok) return setAviso({ tono: "error", texto: r.error });
                void recargar();
              }}
            >
              <Trash2 className="h-4 w-4" /> Eliminar
            </Button>
          </div>
        </ModalMarco>
      )}
    </Card>
  );
}

function ModalPlantillaOnboarding({ plantilla, opciones, onClose, onGuardada }: {
  plantilla: PlantillaOnboarding | null;
  opciones: OpcionesPlantillaOnboarding;
  onClose: () => void;
  onGuardada: (nombre: string) => void;
}) {
  const [nombre, setNombre] = useState(plantilla?.nombre ?? "");
  const [alcance, setAlcance] = useState(plantilla?.alcance ?? "empresa");
  const [empresa, setEmpresa] = useState(plantilla?.empresa ?? "");
  const [puesto, setPuesto] = useState(plantilla?.puesto ?? "");
  const [documentos, setDocumentos] = useState<DocumentoPlantillaOnboarding[]>(
    plantilla?.documentos ?? opciones.documentosBase.map((tipo) => ({ tipo, obligatorio: true })),
  );
  const [nuevoDoc, setNuevoDoc] = useState("");
  const [recursos, setRecursos] = useState<RecursoPlantillaOnboarding[]>(plantilla?.recursos ?? []);
  const [responsables, setResponsables] = useState<Partial<Record<ClavePlazoOnboarding, string>>>(plantilla?.responsables ?? {});
  const [plazos, setPlazos] = useState<Record<ClavePlazoOnboarding, number>>(plantilla?.plazos ?? opciones.plazosDefault);
  const [curso, setCurso] = useState<number | null>(plantilla?.cursoInduccionId ?? null);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState("");

  const filasPlazo: { clave: ClavePlazoOnboarding; nombre: string }[] = [{ clave: "documentos", nombre: "Documentos completos" }, ...opciones.tareasFijas];

  // Cambio 34: solo del catálogo único; «Otro» con el nombre que escriba RH
  const [nuevoOtro, setNuevoOtro] = useState("");
  const catalogo = opciones.catalogoDocumentos ?? [];
  function agregarDoc() {
    const elegido = catalogo.find((x) => x.id === nuevoDoc);
    const t = (elegido?.id === "otro" ? nuevoOtro : elegido?.nombre ?? "").trim();
    if (!t) return;
    if (documentos.some((d) => d.tipo.toLowerCase() === t.toLowerCase())) return setError(`«${t}» ya está en la lista.`);
    setNuevoOtro("");
    setDocumentos([...documentos, { tipo: t, obligatorio: true }]);
    setNuevoDoc("");
    setError("");
  }

  async function guardar() {
    if (!nombre.trim()) return setError("Ponle nombre a la plantilla.");
    if (alcance === "puesto" && !puesto.trim()) return setError("Indica el puesto al que aplica.");
    if (!documentos.length) return setError("Incluye al menos un documento requerido.");
    setOcupado(true);
    const datos = {
      nombre, alcance, empresa, puesto: alcance === "puesto" ? puesto : "", documentos,
      recursos: recursos.filter((r) => r.nombre.trim()), responsables, plazos, curso_induccion_id: curso,
    };
    const r = plantilla
      ? await editarPlantillaOnboarding(plantilla.id, { ...datos, quitar_curso: curso === null })
      : await crearPlantillaOnboarding(datos);
    setOcupado(false);
    if (!r.ok) return setError(r.error);
    onGuardada(r.data.nombre);
  }

  return (
    <ModalMarco
      titulo={plantilla ? "Editar plantilla de Onboarding" : "Nueva plantilla de Onboarding"}
      subtitulo="Los Onboardings ya iniciados no cambian: al aplicarse, la plantilla se copia a la persona."
      onClose={onClose}
      ancho="max-w-3xl"
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <CampoRH label="Nombre"><input value={nombre} onChange={(e) => setNombre(e.target.value)} className={inputRH} placeholder="p. ej. Operativos Monterrey" /></CampoRH>
        <CampoRH label="Aplica a">
          <div className="grid grid-cols-2 gap-2">
            {(["empresa", "puesto"] as const).map((a) => (
              <button
                key={a}
                type="button"
                onClick={() => setAlcance(a)}
                className={cn(
                  "flex h-11 items-center justify-center gap-2 rounded-xl border text-sm font-medium transition",
                  alcance === a ? "border-brand bg-brand-soft text-brand" : "border-border-soft text-ink-2 hover:border-brand/50",
                )}
              >
                {a === "empresa" ? <Building2 className="h-4 w-4" /> : <Briefcase className="h-4 w-4" />} {a === "empresa" ? "Empresa" : "Puesto"}
              </button>
            ))}
          </div>
        </CampoRH>
        <CampoRH label="Empresa contratante" ayuda="Vacía = cualquier razón social de la Cuenta.">
          <select value={empresa} onChange={(e) => setEmpresa(e.target.value)} className={inputRH}>
            <option value="">Toda la Cuenta</option>
            {opciones.razonesSociales.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </CampoRH>
        {alcance === "puesto" && (
          <CampoRH label="Puesto" ayuda="Prevalece sobre la plantilla de empresa.">
            <input value={puesto} onChange={(e) => setPuesto(e.target.value)} className={inputRH} list="puestos-onboarding" placeholder="p. ej. Chofer repartidor" />
            <datalist id="puestos-onboarding">{opciones.puestos.map((p) => <option key={p} value={p} />)}</datalist>
          </CampoRH>
        )}
      </div>

      <h3 className="mt-6 text-sm font-semibold">Documentos requeridos</h3>
      <ul className="mt-2 divide-y divide-border-faint rounded-xl border border-border-soft">
        {documentos.map((d, i) => (
          <li key={d.tipo} className="flex items-center gap-3 px-3 py-2 text-sm">
            <span className="min-w-0 flex-1 truncate">{d.tipo}</span>
            <label className="flex items-center gap-1.5 text-xs text-ink-2">
              <input
                type="checkbox"
                checked={d.obligatorio}
                onChange={(e) => setDocumentos(documentos.map((x, j) => (j === i ? { ...x, obligatorio: e.target.checked } : x)))}
              />
              Obligatorio
            </label>
            <button type="button" aria-label={`Quitar ${d.tipo}`} onClick={() => setDocumentos(documentos.filter((_, j) => j !== i))} className="text-ink-3 hover:text-bad">
              <X className="h-4 w-4" />
            </button>
          </li>
        ))}
      </ul>
      <div className="mt-2 flex gap-2">
        <select value={nuevoDoc} onChange={(e) => setNuevoDoc(e.target.value)} className={inputRH} aria-label="Documento del catálogo">
          <option value="">Agregar documento del catálogo…</option>
          {catalogo.filter((x) => x.id === "otro" || !documentos.some((d) => d.tipo === x.nombre)).map((x) => (
            <option key={x.id} value={x.id}>{x.nombre}</option>
          ))}
        </select>
        {nuevoDoc === "otro" && (
          <input value={nuevoOtro} onChange={(e) => setNuevoOtro(e.target.value)} className={inputRH} placeholder="Nombre del documento"
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); agregarDoc(); } }} />
        )}
        <Button variant="outline" size="sm" className="h-11" onClick={agregarDoc}><Plus className="h-4 w-4" /> Agregar</Button>
      </div>

      <h3 className="mt-6 text-sm font-semibold">Tareas fijas: responsables y plazos</h3>
      <p className="mt-0.5 text-[11px] text-ink-3">Días respecto a la fecha de ingreso (negativo = antes). Si la fecha de ingreso cambia, los plazos se recalculan.</p>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[520px] text-sm">
          <thead>
            <tr className="text-left text-[11px] text-ink-3">
              <th className="py-1.5 font-medium">Tarea</th>
              <th className="py-1.5 font-medium">Responsable por defecto</th>
              <th className="w-28 py-1.5 font-medium">Días</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-faint">
            {filasPlazo.map((f) => (
              <tr key={f.clave}>
                <td className="py-2 pr-3">
                  <span className="block">{f.nombre}</span>
                  <span className="text-[11px] text-ink-3">{textoPlazo(plazos[f.clave] ?? 0)}</span>
                </td>
                <td className="py-2 pr-3">
                  <input value={responsables[f.clave] ?? ""} onChange={(e) => setResponsables({ ...responsables, [f.clave]: e.target.value })} className={inputRH} placeholder="p. ej. Nómina" />
                </td>
                <td className="py-2">
                  <input
                    type="number"
                    value={plazos[f.clave] ?? 0}
                    onChange={(e) => setPlazos({ ...plazos, [f.clave]: Number(e.target.value) || 0 })}
                    className={inputRH}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <details className="mt-6 rounded-xl border border-border-soft" open={false}>
        <summary className="cursor-pointer select-none px-3 py-2.5 text-sm font-semibold">
          Recursos internos y curso de inducción <span className="font-normal text-ink-3">({recursos.length} recursos{curso ? " · con curso" : ""})</span>
        </summary>
        <div className="border-t border-border-faint p-3">
          <p className="text-[11px] text-ink-3">Cada recurso se vuelve una tarea del Onboarding con su responsable y plazo.</p>
          <div className="mt-2 space-y-2">
            {recursos.map((r, i) => (
              <div key={i} className="grid gap-2 sm:grid-cols-[1fr_120px_1fr_90px_auto]">
                <input value={r.nombre} onChange={(e) => setRecursos(recursos.map((x, j) => (j === i ? { ...x, nombre: e.target.value } : x)))} className={inputRH} placeholder="Recurso (p. ej. Laptop)" />
                <select value={r.tipo} onChange={(e) => setRecursos(recursos.map((x, j) => (j === i ? { ...x, tipo: e.target.value as TipoRecursoOnboarding } : x)))} className={inputRH}>
                  {opciones.tiposRecurso.map((t) => <option key={t} value={t}>{ETIQUETA_RECURSO[t]}</option>)}
                </select>
                <input value={r.responsable} onChange={(e) => setRecursos(recursos.map((x, j) => (j === i ? { ...x, responsable: e.target.value } : x)))} className={inputRH} placeholder="Responsable" />
                <input type="number" value={r.dias} onChange={(e) => setRecursos(recursos.map((x, j) => (j === i ? { ...x, dias: Number(e.target.value) || 0 } : x)))} className={inputRH} aria-label="Días respecto al ingreso" />
                <button type="button" aria-label="Quitar recurso" onClick={() => setRecursos(recursos.filter((_, j) => j !== i))} className="grid h-11 w-11 place-items-center text-ink-3 hover:text-bad">
                  <X className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => setRecursos([...recursos, { nombre: "", tipo: "equipo", responsable: "", dias: -1 }])}>
            <Plus className="h-4 w-4" /> Agregar recurso
          </Button>
          <div className="mt-4 max-w-md">
            <CampoRH label="Curso de inducción (opcional)">
              <select value={curso ?? ""} onChange={(e) => setCurso(e.target.value ? Number(e.target.value) : null)} className={inputRH}>
                <option value="">Sin curso</option>
                {opciones.cursos.map((c) => <option key={c.id} value={c.id}>{c.titulo}</option>)}
              </select>
            </CampoRH>
          </div>
        </div>
      </details>

      {error && <p className="mt-3 text-sm font-semibold text-bad">{error}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onClose} disabled={ocupado}>Cancelar</Button>
        <Button size="sm" onClick={guardar} disabled={ocupado}>
          {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Guardar plantilla
        </Button>
      </div>
    </ModalMarco>
  );
}
