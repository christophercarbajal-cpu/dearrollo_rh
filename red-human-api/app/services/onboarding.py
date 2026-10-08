"""Onboarding v2 (2026-09-28) — Fase 1: plantillas, resolución por jerarquía y tareas.

* Jerarquía: la plantilla de PUESTO prevalece sobre la de EMPRESA. Dentro de cada nivel, la que nombra la
  razón social contratante gana a la que aplica a toda la Cuenta (`empresa` vacía). Sin ninguna, aplica la
  configuración predeterminada (`DOCUMENTOS_BASE` + las tres tareas fijas + `PLAZOS_ONBOARDING_DEFAULT`).
* La configuración que se aplica a una persona es una COPIA (`configuracion_para`): lo que RH cambie para
  ese candidato nunca altera la plantilla.
* Tareas: pendiente → realizada | cancelada (motivo obligatorio). Las tres fijas nacen siempre y no se
  cancelan una por una. Los plazos son relativos a la fecha de ingreso (`fecha_limite`).
* Nada de esto escribe `Postulacion.etapa` (regla B5).
"""

import copy
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import (
    DOCUMENTOS_BASE,
    PLAZOS_ONBOARDING_DEFAULT,
    TAREAS_FIJAS_ONBOARDING,
    TIPOS_RECURSO_ONBOARDING,
    Documento,
    Expediente,
    PlantillaOnboarding,
    TareaOnboarding,
)

CLAVES_FIJAS = {c for c, _ in TAREAS_FIJAS_ONBOARDING}
EXTENSIONES_CONTRATO = (".pdf",)
# Tareas que solo se cierran con su acción propia (no con «marcar realizada» genérico).
CIERRE_CON_ACCION = {
    "contrato_firmado": "Se marca como realizada al cargar el contrato firmado (PDF final).",
    "confirmar_ingreso": "Se marca con «Confirmar ingreso» (registra la fecha real de llegada).",
}
# 2026-10-08: «Contrato firmado» refleja la MISMA actividad de Contratación (`carta_contrato`). Si RH la omitió con
# autorización allá, aquí queda «Omitida» (cancelada con este prefijo) y no vuelve a bloquear.
PREFIJO_OMITIDA = "Omitida en Contratación"
# Acción directa de cada tarea fija desde la ficha (2026-10-08) y cómo se dice lo que falta.
ACCION_TAREA = {
    "confirmar_ingreso": ("confirmar_ingreso", "Confirmar ingreso", "Falta confirmar el ingreso"),
    "alta_imss_nomina": ("registrar_tarea", "Registrar alta IMSS / nómina", "Falta registrar el alta IMSS / nómina"),
    "contrato_firmado": ("contrato_firmado", "Adjuntar contrato firmado", "Falta el contrato firmado"),
}


def fecha_base(e: Expediente) -> Optional[datetime]:
    """Fecha contra la que corren los plazos: la REAL si ya se confirmó el ingreso; si no, la prevista."""
    return e.fecha_ingreso_real or e.fecha_ingreso


def norm(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().split())


def _entero(v, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ---------- normalización de lo que captura RH ----------

def normalizar_documentos(docs: List[dict]) -> List[dict]:
    salida, vistos = [], set()
    for d in docs or []:
        tipo = str((d or {}).get("tipo") or "").strip()[:80]
        if not tipo or norm(tipo) in vistos:
            continue
        vistos.add(norm(tipo))
        salida.append({"tipo": tipo, "obligatorio": bool((d or {}).get("obligatorio", True))})
    return salida


def normalizar_recursos(recursos: List[dict]) -> List[dict]:
    salida, vistos = [], set()
    for r in recursos or []:
        nombre = str((r or {}).get("nombre") or "").strip()[:200]
        if not nombre or norm(nombre) in vistos:
            continue
        vistos.add(norm(nombre))
        tipo = str((r or {}).get("tipo") or "otro").strip().lower()
        salida.append({
            "nombre": nombre,
            "tipo": tipo if tipo in TIPOS_RECURSO_ONBOARDING else "otro",
            "responsable": str((r or {}).get("responsable") or "").strip()[:150],
            "dias": _entero((r or {}).get("dias"), 0),
        })
    return salida


def normalizar_plazos(plazos: dict) -> dict:
    salida = dict(PLAZOS_ONBOARDING_DEFAULT)
    for k in PLAZOS_ONBOARDING_DEFAULT:
        v = _entero((plazos or {}).get(k))
        if v is not None:
            salida[k] = v
    return salida


def normalizar_responsables(resp: dict) -> dict:
    claves = ["documentos", *CLAVES_FIJAS]
    return {k: str((resp or {}).get(k) or "").strip()[:150] for k in claves}


# ---------- resolución ----------

def configuracion_predeterminada() -> dict:
    return {
        "documentos": [{"tipo": t, "obligatorio": True} for t in DOCUMENTOS_BASE],
        "recursos": [],
        "responsables": normalizar_responsables({}),
        "plazos": dict(PLAZOS_ONBOARDING_DEFAULT),
        "cursoInduccionId": None,
    }


def config_de_plantilla(p: PlantillaOnboarding) -> dict:
    return {
        "documentos": normalizar_documentos(list(p.documentos or [])),
        "recursos": normalizar_recursos(list(p.recursos or [])),
        "responsables": normalizar_responsables(dict(p.responsables or {})),
        "plazos": normalizar_plazos(dict(p.plazos or {})),
        "cursoInduccionId": p.curso_induccion_id,
    }


def resolver_plantilla(db: Session, cuenta_id: int, puesto: str = "", empresa: str = "") -> Tuple[Optional[PlantillaOnboarding], str]:
    """(plantilla, origen) con origen ∈ puesto | empresa | predeterminada."""
    activas = (
        db.query(PlantillaOnboarding)
        .filter(PlantillaOnboarding.cuenta_id == cuenta_id, PlantillaOnboarding.activa.is_(True))
        .order_by(PlantillaOnboarding.id)
        .all()
    )
    np_, ne = norm(puesto), norm(empresa)

    def empresa_ok(p):
        return not norm(p.empresa) or (ne and norm(p.empresa) == ne)

    def mejor(cands):
        # la que nombra la empresa contratante gana a la genérica de la Cuenta
        especificas = [p for p in cands if norm(p.empresa)]
        return (especificas or cands)[0] if cands else None

    if np_:
        p = mejor([p for p in activas if p.alcance == "puesto" and norm(p.puesto) == np_ and empresa_ok(p)])
        if p:
            return p, "puesto"
    p = mejor([p for p in activas if p.alcance == "empresa" and empresa_ok(p)])
    if p:
        return p, "empresa"
    return None, "predeterminada"


def configuracion_para(db: Session, cuenta_id: int, puesto: str = "", empresa: str = "") -> dict:
    """COPIA de la configuración que le toca a una persona (la plantilla nunca se toca)."""
    p, origen = resolver_plantilla(db, cuenta_id, puesto, empresa)
    base = config_de_plantilla(p) if p else configuracion_predeterminada()
    return {"plantillaId": p.id if p else None, "plantilla": p.nombre if p else "", "origen": origen, **copy.deepcopy(base)}


# ---------- tareas ----------

def fecha_limite(fecha_ingreso: Optional[datetime], dias: Optional[int]) -> Optional[datetime]:
    if not fecha_ingreso or dias is None:
        return None
    return fecha_ingreso + timedelta(days=int(dias))


def tareas_de(db: Session, e: Expediente) -> List[TareaOnboarding]:
    fijas = {c: i for i, (c, _) in enumerate(TAREAS_FIJAS_ONBOARDING)}
    tareas = db.query(TareaOnboarding).filter(TareaOnboarding.expediente_id == e.id).all()
    return sorted(tareas, key=lambda t: (0 if t.fija else 1, fijas.get(t.clave, 99), t.id))


def generar_tareas(db: Session, e: Expediente, cuenta_id: int, config: dict, por: str) -> List[TareaOnboarding]:
    """Idempotente: crea las tres fijas y un recurso por cada `config["recursos"]` que no exista ya (por
    nombre). Nunca borra ni cambia el estado de una tarea existente."""
    existentes = tareas_de(db, e)
    por_clave = {t.clave for t in existentes if t.fija}
    recursos = {norm(t.nombre) for t in existentes if not t.fija}
    responsables = config.get("responsables") or {}
    plazos = config.get("plazos") or PLAZOS_ONBOARDING_DEFAULT
    for clave, nombre in TAREAS_FIJAS_ONBOARDING:
        if clave in por_clave:
            continue
        dias = _entero(plazos.get(clave), PLAZOS_ONBOARDING_DEFAULT.get(clave))
        t = TareaOnboarding(
            cuenta_id=cuenta_id, expediente_id=e.id, clave=clave, nombre=nombre, tipo="fija", fija=True, obligatoria=True,
            responsable=responsables.get(clave, ""), dias_relativos=dias, fecha_limite=fecha_limite(fecha_base(e), dias), creada_por=por,
        )
        db.add(t)
    for r in normalizar_recursos(config.get("recursos") or []):
        if norm(r["nombre"]) in recursos:
            continue
        db.add(TareaOnboarding(
            cuenta_id=cuenta_id, expediente_id=e.id, clave="recurso", nombre=r["nombre"], tipo=r["tipo"], fija=False, obligatoria=True,
            responsable=r["responsable"], dias_relativos=r["dias"], fecha_limite=fecha_limite(fecha_base(e), r["dias"]), creada_por=por,
        ))
    db.flush()
    sincronizar_contrato(db, e)  # el contrato firmado (u omitido) en Contratación ya cuenta aquí
    return tareas_de(db, e)


def aplicar_documentos(db: Session, e: Expediente, documentos: List[dict]) -> List[str]:
    """Agrega al expediente los documentos de la configuración que falten (por tipo). Nunca borra ni cambia
    el estado de uno existente. Regresa los tipos agregados."""
    existentes = {norm(d.tipo) for d in e.documentos}
    nuevos = []
    for d in normalizar_documentos(documentos):
        if norm(d["tipo"]) in existentes:
            continue
        doc = Documento(expediente_id=e.id, tipo=d["tipo"], obligatorio=d["obligatorio"])
        db.add(doc)
        e.documentos.append(doc)
        nuevos.append(d["tipo"])
    db.flush()
    return nuevos


def recalcular_fechas(db: Session, e: Expediente) -> int:
    """Recalcula la fecha límite de las tareas PENDIENTES a partir de la fecha de ingreso. Regresa cuántas cambió."""
    n = 0
    for t in tareas_de(db, e):
        if t.estado != "pendiente" or t.dias_relativos is None:
            continue
        nueva = fecha_limite(fecha_base(e), t.dias_relativos)
        if nueva != t.fecha_limite:
            t.fecha_limite = nueva
            n += 1
    return n


def atrasada(t: TareaOnboarding, ahora: Optional[datetime] = None) -> bool:
    if t.estado != "pendiente" or not t.fecha_limite:
        return False
    limite = t.fecha_limite if t.fecha_limite.tzinfo else t.fecha_limite.replace(tzinfo=timezone.utc)
    return limite.date() < (ahora or datetime.now(timezone.utc)).date()


def cambiar_estado_tarea(t: TareaOnboarding, estado: str, motivo: str, por: str) -> Optional[str]:
    """Aplica la transición. Regresa un mensaje de error (409/400) o None si se aplicó."""
    estado = (estado or "").strip().lower()
    motivo = (motivo or "").strip()
    if estado == t.estado:
        return None
    ahora = datetime.now(timezone.utc)
    if estado == "realizada":
        if t.clave in CIERRE_CON_ACCION:
            return CIERRE_CON_ACCION[t.clave]
        t.estado, t.realizada_por, t.realizada_en = "realizada", por, ahora
        t.cancelada_por, t.cancelada_en, t.motivo_cancelacion = "", None, ""
    elif estado == "cancelada":
        if t.fija:
            return f"«{t.nombre}» es una tarea fija y obligatoria del Onboarding; no se cancela por separado."
        if not motivo:
            return "Indica el motivo para cancelar la tarea."
        t.estado, t.cancelada_por, t.cancelada_en, t.motivo_cancelacion = "cancelada", por, ahora, motivo[:1000]
        t.realizada_por, t.realizada_en = "", None
    elif estado == "pendiente":  # reabrir
        if t.clave in CIERRE_CON_ACCION and t.estado == "realizada":
            return f"«{t.nombre}» se cerró con su acción propia y no se reabre desde la lista de tareas."
        t.estado = "pendiente"
        t.realizada_por, t.realizada_en, t.cancelada_por, t.cancelada_en, t.motivo_cancelacion = "", None, "", None, ""
    else:
        return "Estado inválido. Usa pendiente, realizada o cancelada."
    return None


# ---------- Fase 2: tareas = fuente de verdad, requisitos e inicio ----------

def sincronizar_legado(db: Session, e: Expediente) -> None:
    """Las TAREAS son la fuente de verdad (decisión del usuario, 2026-09-28). Los campos viejos del expediente
    (`contrato`, `alta_administrativa`, `equipo_accesos`) se DERIVAN de ellas para no romper pantallas ni
    reportes que todavía los leen. Sin tareas (Onboarding no iniciado) no se toca nada."""
    tareas = tareas_de(db, e)
    if not tareas:
        return
    por_clave = {t.clave: t for t in tareas if t.fija}
    if "contrato_firmado" in por_clave:
        e.contrato = "Firmado" if por_clave["contrato_firmado"].estado == "realizada" else "Pendiente"
    if "alta_imss_nomina" in por_clave:
        e.alta_administrativa = "Realizada" if por_clave["alta_imss_nomina"].estado == "realizada" else "Pendiente"
    recursos = [t for t in tareas if not t.fija and t.tipo in ("correo", "equipo", "accesos")]
    if recursos:
        if all(t.estado == "cancelada" for t in recursos):
            e.equipo_accesos = "No aplica"
        elif all(t.estado in ("realizada", "cancelada") for t in recursos):
            e.equipo_accesos = "Listo"
        else:
            e.equipo_accesos = "Pendiente"


def requisitos_inicio(e: Expediente) -> dict:
    """Lo que habilita «Enviar a Onboarding»: puesto, sueldo, tipo de contratación, fecha de ingreso y el
    consentimiento de privacidad (LFPDPPP) de la postulación."""
    p = e.postulacion
    items = [
        ("puesto", "Puesto", bool((e.puesto or "").strip())),
        ("sueldo", "Sueldo", bool((e.sueldo or "").strip())),
        ("tipo_contratacion", "Tipo de contratación", bool((e.tipo_contratacion or "").strip())),
        ("fecha_ingreso", "Fecha de ingreso", bool(e.fecha_ingreso)),
        ("consentimiento", "Consentimiento de privacidad (LFPDPPP)", bool(p and p.consentimiento)),
    ]
    faltan = [n for _, n, ok in items if not ok]
    return {"items": [{"clave": c, "nombre": n, "ok": ok} for c, n, ok in items], "faltan": faltan, "completos": not faltan}


def onboarding_iniciado(db: Session, e: Expediente) -> bool:
    return bool(tareas_de(db, e))


def aplicar_seleccion_documentos(db: Session, e: Expediente, documentos: List[dict], por: str) -> Tuple[List[str], List[str], List[str]]:
    """Aplica la selección de documentos de ESTA persona: agrega los que falten y ajusta si son obligatorios;
    un documento del expediente que RH quitó de la selección pasa a «No aplica» con motivo (nunca se borra).
    Uno ya entregado se conserva tal cual. Regresa (agregados, no_aplica, conservados)."""
    seleccion = {norm(d["tipo"]): d for d in normalizar_documentos(documentos)}
    agregados = aplicar_documentos(db, e, list(seleccion.values()))
    no_aplica, conservados = [], []
    ahora = datetime.now(timezone.utc)
    for d in e.documentos:
        if d.interno:
            continue
        sel = seleccion.get(norm(d.tipo))
        if sel:
            d.obligatorio = bool(sel["obligatorio"])
            continue
        if d.estado in ("pendiente", "rechazado") and not d.archivo:
            d.estado, d.motivo_no_aplica, d.no_aplica_por, d.no_aplica_en = "no_aplica", "No seleccionado al iniciar el Onboarding.", por, ahora
            no_aplica.append(d.tipo)
        elif d.estado != "no_aplica":
            conservados.append(d.tipo)
    db.flush()
    return agregados, no_aplica, conservados


def tareas_por_responsable(tareas: List[TareaOnboarding]) -> dict:
    grupos: dict = {}
    for t in tareas:
        if t.estado == "pendiente" and (t.responsable or "").strip():
            grupos.setdefault(t.responsable.strip(), []).append(t)
    return grupos


# ---------- Fase 3: tablero, alta, cierre ----------

def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def documentos_aplicables(e: Expediente) -> List[Documento]:
    """Los documentos que cuentan para el avance y el cierre: todos menos los internos y los «No aplica»."""
    return [d for d in e.documentos if not d.interno and d.estado != "no_aplica"]


def pendientes_cierre(e: Expediente, tareas: List[TareaOnboarding]) -> List[str]:
    """Qué impide «Cerrar Onboarding»: tareas abiertas y documentos que no están Aprobados ni «No aplica»."""
    from ..models import estado_documento_onboarding

    faltan = [f"Tarea «{t.nombre}» pendiente" for t in tareas if t.estado == "pendiente"]
    faltan += [f"Documento «{d.tipo}» {estado_documento_onboarding(d).lower()}" for d in documentos_aplicables(e) if not d.aprobado]
    if not tareas:
        faltan.insert(0, "El Onboarding no tiene tareas generadas")
    return faltan


def resumen_tablero(e: Expediente, tareas: List[TareaOnboarding]) -> dict:
    """Lo que pinta el tablero de Onboarding. «No aplica» y «Cancelada» se excluyen del total y se muestran
    aparte con su motivo, así el 100 % nunca es falso."""
    from ..models import estado_documento_onboarding

    aplicables = documentos_aplicables(e)
    aprobados = [d for d in aplicables if d.aprobado]
    vivas = [t for t in tareas if t.estado != "cancelada"]
    realizadas = [t for t in vivas if t.estado == "realizada"]
    cerrado = bool(e.onboarding_cerrado_en)
    faltan = pendientes_cierre(e, tareas)
    confirmado = bool(e.ingreso_confirmado_en)
    return {
        "iniciado": bool(tareas),
        "documentos": {
            "aprobados": [d.tipo for d in aprobados],
            "faltantes": [{"tipo": d.tipo, "estado": estado_documento_onboarding(d), "obligatorio": d.obligatorio} for d in aplicables if not d.aprobado],
            "noAplica": [{"tipo": d.tipo, "motivo": d.motivo_no_aplica or "", "por": d.no_aplica_por or ""} for d in e.documentos if d.estado == "no_aplica" and not d.interno],
            "total": len(aplicables),
            # 2026-10-08: UN solo porcentaje en tablero, Onboarding y ficha = `Expediente.progreso` (obligatorios Aprobados)
            "pct": e.progreso,
            "obligatoriosAprobados": sum(1 for d in e.obligatorios if d.aprobado),
            "obligatorios": len(e.obligatorios),
        },
        "tareas": {
            "realizadas": len(realizadas),
            "pendientes": len(vivas) - len(realizadas),
            "atrasadas": sum(1 for t in vivas if atrasada(t)),
            "total": len(vivas),
            "pct": round(len(realizadas) / len(vivas) * 100) if vivas else 0,
            "canceladas": [{"nombre": t.nombre, "motivo": t.motivo_cancelacion or "", "por": t.cancelada_por or ""} for t in tareas if t.estado == "cancelada"],
        },
        "ingreso": {
            "prevista": _iso(e.fecha_ingreso),
            "real": _iso(e.fecha_ingreso_real),
            "confirmado": confirmado,
            "confirmadoPor": e.ingreso_confirmado_por or "",
            "confirmadoEn": _iso(e.ingreso_confirmado_en),
        },
        "alta": e.estado == "alta",
        "puedeAlta": e.estado != "alta" and confirmado and not e.no_ingreso_en,
        "cerrado": cerrado,
        "cerradoPor": e.onboarding_cerrado_por or "",
        "cerradoEn": _iso(e.onboarding_cerrado_en),
        "puedeCerrar": not cerrado and e.estado == "alta" and not faltan,
        "faltanCierre": faltan,
        "noIngreso": {"en": _iso(e.no_ingreso_en), "por": e.no_ingreso_por or "", "motivo": e.no_ingreso_motivo or ""} if e.no_ingreso_en else None,
        "puedeNoIngreso": e.estado != "alta" and not e.no_ingreso_en,
    }


# ---------- Documentos firmados (carga manual o Dropbox Sign, 2026-09-29) ----------

def guardar_documento_firmado(db: Session, e: Expediente, tipo: str, contenido: bytes, nombre_archivo: str, por: str, canal: str = "rh") -> Documento:
    """Guarda (o reemplaza) un PDF firmado como documento INTERNO del expediente: no suma al porcentaje ni se le
    pide al candidato. Si es el contrato, la tarea fija «Contrato firmado» queda Realizada (quién y cuándo)."""
    from ..models import TIPO_CONTRATO_FIRMADO
    from . import archivos as fs

    validado = fs.validar_bytes(contenido, nombre_archivo, tipo)
    if validado.extension != "pdf":
        raise ValueError("El documento firmado debe ser un PDF.")
    doc = next((d for d in e.documentos if d.interno and d.tipo == tipo), None)
    if doc is None:
        doc = Documento(expediente_id=e.id, tipo=tipo, obligatorio=False, interno=True)
        e.documentos.append(doc)
    ahora = datetime.now(timezone.utc)
    doc.archivo = fs.guardar(validado, f"expedientes/{e.id}", tipo.replace(" ", "_"))
    doc.nombre_archivo, doc.mime, doc.tamano = validado.nombre, validado.mime, validado.tamano
    doc.subido_en = doc.recibido_en = ahora
    doc.recibido_canal = canal
    doc.estado, doc.revisado_por = "recibido", por
    doc.notas_ia = f"Firmado · {por}."
    if tipo == TIPO_CONTRATO_FIRMADO:
        tarea = next((t for t in tareas_de(db, e) if t.fija and t.clave == "contrato_firmado"), None)
        if tarea:
            tarea.estado, tarea.realizada_por, tarea.realizada_en = "realizada", por, ahora
            tarea.cancelada_por, tarea.cancelada_en, tarea.motivo_cancelacion = "", None, ""
            tarea.notas = f"Contrato firmado · {por}."
        sincronizar_legado(db, e)
    db.flush()
    return doc


def contrato_ya_firmado(e: Expediente) -> bool:
    from ..models import TIPO_CONTRATO_FIRMADO

    return any(d.interno and d.tipo == TIPO_CONTRATO_FIRMADO and d.archivo for d in e.documentos)


# ---------- 2026-10-08: contrato ÚNICO, expediente reutilizable, tareas desde la ficha ----------

def firma_contrato(db: Session, e: Expediente):
    """La solicitud de firma electrónica del contrato ya firmada por todos (aunque el PDF siga descargándose)."""
    try:
        from ..models import FirmaDocumento

        return (db.query(FirmaDocumento).filter(FirmaDocumento.expediente_id == e.id, FirmaDocumento.documento == "contrato",
                                               FirmaDocumento.estado.in_(("firmada", "descargada")))
                .order_by(FirmaDocumento.id.desc()).first())
    except Exception:  # noqa: BLE001
        return None


def omision_contrato(e: Expediente) -> Optional[dict]:
    """La decisión de RH de omitir la actividad de contrato en Contratación (ruta de la postulación)."""
    p = e.postulacion
    pasos = ((p.proceso or {}).get("pasos") or []) if p is not None else []
    for paso in pasos:
        if paso.get("tipo") == "carta_contrato":
            d = ((p.proceso_estado or {}).get(paso["id"]) or {})
            if d.get("omitida") or d.get("cancelada"):
                return d.get("omitida") or d.get("cancelada")
    return None


def sincronizar_contrato(db: Session, e: Expediente) -> bool:
    """«Contrato firmado» apunta a la MISMA entidad de Contratación: PDF firmado (manual o Dropbox Sign) o solicitud
    de firma completa → Realizada; actividad omitida con autorización → «Omitida» (no bloquea); si RH reactiva la
    actividad, la tarea vuelve a Pendiente. Idempotente; no hace commit. Regresa True si cambió algo."""
    tarea = next((t for t in tareas_de(db, e) if t.fija and t.clave == "contrato_firmado"), None)
    if tarea is None or tarea.estado == "realizada":
        return False
    ahora = datetime.now(timezone.utc)
    firma = None if contrato_ya_firmado(e) else firma_contrato(db, e)
    if contrato_ya_firmado(e) or firma is not None:
        tarea.estado, tarea.realizada_por, tarea.realizada_en = "realizada", "Firma electrónica" if firma else "Contratación", ahora
        tarea.cancelada_por, tarea.cancelada_en, tarea.motivo_cancelacion = "", None, ""
        tarea.notas = "Contrato firmado en Contratación." if firma is None else "Contrato firmado electrónicamente en Contratación."
        sincronizar_legado(db, e)
        return True
    omision = omision_contrato(e)
    omitida = tarea.estado == "cancelada" and (tarea.motivo_cancelacion or "").startswith(PREFIJO_OMITIDA)
    if omision and not omitida:
        tarea.estado, tarea.cancelada_por, tarea.cancelada_en = "cancelada", omision.get("por") or "RH", ahora
        tarea.motivo_cancelacion = f"{PREFIJO_OMITIDA}: {omision.get('motivo') or 'sin motivo'}"[:1000]
        sincronizar_legado(db, e)
        return True
    if omitida and not omision:
        tarea.estado = "pendiente"
        tarea.cancelada_por, tarea.cancelada_en, tarea.motivo_cancelacion = "", None, ""
        sincronizar_legado(db, e)
        return True
    return False


def es_omitida(t: TareaOnboarding) -> bool:
    return t.estado == "cancelada" and (t.motivo_cancelacion or "").startswith(PREFIJO_OMITIDA)


def documentos_por_solicitar(e: Expediente) -> List[Documento]:
    """Lo que de verdad hay que pedirle al candidato: obligatorios sin entregar (Pendiente o Rechazado). Lo Aprobado o
    ya Recibido (aunque esté en revisión) NUNCA se vuelve a pedir."""
    return [d for d in e.obligatorios if d.estado in ("pendiente", "rechazado") and not d.entregado]


def pendientes_obligatorias(tareas: List[TareaOnboarding]) -> List[TareaOnboarding]:
    """Tareas que faltan para el alta, en orden (las fijas primero)."""
    return [t for t in tareas if t.estado == "pendiente" and t.obligatoria]


def accion_tarea(t: TareaOnboarding) -> dict:
    """Acción directa (botón en la fila de la ficha) y texto de lo que falta."""
    clave, texto, falta = ACCION_TAREA.get(t.clave, ("registrar_tarea", f"Marcar realizada: {t.nombre}", f"Falta: {t.nombre}"))
    return {"clave": clave, "texto": texto, "falta": falta, "tarea": t.id}
