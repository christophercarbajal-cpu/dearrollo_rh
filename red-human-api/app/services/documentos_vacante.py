"""Cambio 25 (2026-10-12) — módulo de recolección y revisión de documentos.

- La vacante define «Documentos y fotos que se piden» (`Vacante.documentos_solicitados`, [{nombre, cantidad, foto}]); la
  IA lo llena si la descripción los menciona (`detectar`, determinista: nunca inventa documentos que nadie mencionó).
- Con elementos, la ruta de cada postulación suma la actividad «Documentos» DESPUÉS del prefiltro de WhatsApp
  (`con_documentos_vacante`, al congelar la ruta). Sin elementos, la ruta no cambia.
- Al habilitarse, el agente manda la liga para subir archivos (`revisar`, desde `proceso.avanzar_seguro`); una foto que
  llegue por el chat antes de Contratación se ataja con «Súbela aquí, por favor: [liga]» (`liga_si_intercepta`).
- Cada archivo es su propio registro (`ArchivoDocumento`) ligado al documento solicitado; las fotos del auto exigen 4.
- RH revisa en la pestaña «Documentos»: Aprobar · Pedir corrección (elige cuáles; reemplazo por WhatsApp) · Marcar
  excepción. Estado del expediente: Pendiente · Por revisar · Corrección pedida · Documentos aprobados · Excepción.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (DOCUMENTOS_SUGERIDOS_VACANTE, ESTADOS_DOCUMENTOS_EXPEDIENTE, ArchivoDocumento, Documento, Expediente,
                      Postulacion, normalizar_documentos_vacante, registrar)

ID_PASO = "documentos-vacante"
ACTOR = "Red Human"
MIN_MOTIVO_EXCEPCION = 10


def _plano(t: str) -> str:
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


# ============================================================ vacante

def de_vacante(v, pasos: Optional[list] = None, tempranos: Optional[bool] = None) -> List[dict]:
    """Documentos que pide la vacante. Cambio 28 («Se pide en:»): con `tempranos=True` solo los que se piden ANTES de
    Contratación (la actividad «Documentos» de la vacante); con `False`, los de Contratación/Onboarding."""
    docs = normalizar_documentos_vacante(getattr(v, "documentos_solicitados", None) or []) if v is not None else []
    if tempranos is None:
        return docs
    from .proceso import _indice

    if pasos is None:
        pasos = list(((getattr(v, "proceso", None) or {}).get("pasos")) or [])
    return [d for d in docs if (_indice(etapa_doc(d, pasos)) < _indice("Contratación")) == tempranos]


def etapas_documentos(pasos: Optional[list]) -> List[str]:
    """Etapas de la ruta con actividad «Documentos» (opciones de «Se pide en:»)."""
    from .proceso import _indice

    etapas = {x.get("etapa") for x in pasos or [] if isinstance(x, dict) and x.get("tipo") == "documentos" and not x.get("heredado")}
    return sorted((e for e in etapas if e), key=_indice)


def etapa_doc(d: dict, pasos: Optional[list]) -> str:
    """«Se pide en:» de un documento: la elegida; si no, la primera actividad «Documentos» antes de Contratación (o el
    Prefiltro, donde se agrega sola después del prefiltro). «Documentos de ingreso» de Contratación es aparte."""
    from .proceso import _indice

    etapas = etapas_documentos(pasos)
    if d.get("etapa") in etapas:
        return d["etapa"]
    previas = [e for e in etapas if _indice(e) < _indice("Contratación")]
    if d.get("etapa") and not previas and _indice(d["etapa"]) >= _indice("Contratación"):
        return d["etapa"]
    return previas[0] if previas else "Prefiltro"


def asegurar_finales(db: Session, p: Postulacion) -> List[Documento]:
    """Al llegar a Contratación/Onboarding: los documentos de la vacante con «Se pide en: Contratación» (u Onboarding)
    entran al expediente como obligatorios. Idempotente."""
    if p.expediente is None or p.vacante is None:
        return []
    docs = de_vacante(p.vacante, (p.proceso or {}).get("pasos") or [], tempranos=False)
    return asegurar_documentos(db, p.expediente, docs) if docs else []


def detectar(*textos) -> List[dict]:
    """Documentos que MENCIONA el texto de la vacante (descripción, requisitos, responsabilidades). Solo los sugeridos del
    catálogo y solo si se mencionan: nunca agrega algo que la descripción no pide."""
    plano = " " + _plano(" ".join(str(t) for t in textos if t)) + " "
    salida = []
    for x in DOCUMENTOS_SUGERIDOS_VACANTE:
        if any(re.search(rf"(?<![a-z]){re.escape(p)}(?![a-z])", plano) for p in x["palabras"]):
            salida.append({"nombre": x["nombre"], "cantidad": x["cantidad"], "foto": x["foto"]})
    return normalizar_documentos_vacante(salida)


def sugeridos() -> List[dict]:
    return [{"nombre": x["nombre"], "cantidad": x["cantidad"], "foto": x["foto"]} for x in DOCUMENTOS_SUGERIDOS_VACANTE]


def texto_documento(d: dict) -> str:
    n = int(d.get("cantidad") or 1)
    return f"{d['nombre']} ({n} fotos)" if n > 1 and d.get("foto") else (f"{d['nombre']} ({n} archivos)" if n > 1 else d["nombre"])


# ============================================================ ruta

def con_documentos_vacante(proc: dict, v) -> dict:
    """Suma la actividad «Documentos» después del prefiltro de WhatsApp si la vacante pide documentos. Si la ruta ya trae
    documentos antes de Contratación (Masivos con documentos), ESA actividad valida los de la vacante (no se duplica)."""
    from .proceso import _indice, _regla, _responsable

    docs = de_vacante(v, (proc or {}).get("pasos") or [], tempranos=True)
    if not docs or not proc or not proc.get("pasos"):
        return proc
    nombres = [d["nombre"] for d in docs]
    pasos = [dict(x) for x in proc["pasos"]]
    previo = next((x for x in pasos if x["tipo"] == "documentos" and _indice(x["etapa"]) < _indice("Contratación")), None)
    if previo is not None:
        previo["documentos"] = list(dict.fromkeys([*(previo.get("documentos") or []), *nombres]))
        previo["documentos_vacante"] = True
    else:
        ancla = next((x for t in ("prefiltro_whatsapp", "prefiltro_web", "solicitud_web") for x in pasos if x["tipo"] == t), None)
        paso = {"id": ID_PASO, "tipo": "documentos", "nombre": "Documentos", "etapa": "Prefiltro", "obligatorio": True,
                "depende_de": [ancla["id"]] if ancla else [], "responsable": _responsable(None, "documentos"),
                "regla": _regla(None, "documentos"), "plazo_dias": None, "orden": 0, "documentos": nombres,
                "documentos_vacante": True}
        i = pasos.index(ancla) + 1 if ancla else next((k for k, x in enumerate(pasos) if _indice(x["etapa"]) > 0), len(pasos))
        pasos.insert(i, paso)
    for k, x in enumerate(pasos):
        x["orden"] = k
    return {**proc, "pasos": pasos}


def paso_de(p: Postulacion) -> Optional[dict]:
    from .proceso import _indice

    return next((x for x in (p.proceso or {}).get("pasos", []) if x["tipo"] == "documentos" and x.get("documentos_vacante")
                 and _indice(x["etapa"]) < _indice("Contratación")), None)


# ============================================================ expediente y archivos

def _tablas() -> bool:
    from .modulos_rh import disponible

    return disponible()


def asegurar_documentos(db: Session, e: Expediente, docs: List[dict]) -> List[Documento]:
    """Crea (o ajusta) en el expediente un `Documento` por cada documento que pide la vacante. Idempotente."""
    salida = []
    for d in docs:
        doc = next((x for x in e.documentos if _plano(x.tipo) == _plano(d["nombre"]) and not x.interno), None)
        if doc is None:
            doc = Documento(expediente_id=e.id, tipo=d["nombre"], obligatorio=True)
            db.add(doc)
            e.documentos.append(doc)
        doc.cantidad = int(d.get("cantidad") or 1)
        doc.es_foto = bool(d.get("foto"))
        if doc.estado == "no_aplica" and not doc.motivo_no_aplica:
            doc.estado = "pendiente"
        salida.append(doc)
    db.flush()
    return salida


def archivos(db: Session, doc: Documento, solo_vigentes: bool = True) -> List[ArchivoDocumento]:
    if not _tablas() or doc.id is None:
        return []
    try:
        q = db.query(ArchivoDocumento).filter(ArchivoDocumento.documento_id == doc.id)
        if solo_vigentes:
            q = q.filter(ArchivoDocumento.vigente.is_(True))
        return q.order_by(ArchivoDocumento.id).all()
    except Exception:  # noqa: BLE001
        return []


def multiple(doc: Documento) -> bool:
    """Se recibe sin el validador de documentos oficiales: fotos (las revisa RH) o documentos de varios archivos."""
    return bool(doc.es_foto) or int(doc.cantidad or 1) > 1


def registrar_archivo(db: Session, e: Expediente, doc: Documento, ruta: str, validado, canal: str, subido_por: str,
                      convertido_de: str = "") -> Optional[ArchivoDocumento]:
    """Guarda el archivo como su PROPIO registro. Un documento de un solo archivo lo reemplaza (el anterior queda como no
    vigente); uno de N archivos conserva los últimos N."""
    if not _tablas():
        return None
    try:
        reg = ArchivoDocumento(documento_id=doc.id, expediente_id=e.id, archivo=ruta, nombre_archivo=validado.nombre,
                               mime=validado.mime, tamano=validado.tamano, canal=canal, subido_por=subido_por[:150],
                               convertido_de=convertido_de, vigente=True, subido_en=datetime.now(timezone.utc))
        db.add(reg)
        db.flush()
        vigentes = archivos(db, doc)
        tope = max(1, int(doc.cantidad or 1))
        for viejo in vigentes[:-tope] if len(vigentes) > tope else []:
            viejo.vigente = False
        return reg
    except Exception:  # noqa: BLE001
        return None


def conteo(db: Session, doc: Documento) -> int:
    n = len(archivos(db, doc))
    if n or not doc.archivo or archivos(db, doc, solo_vigentes=False):
        return n
    return 1  # documento previo al módulo: su único archivo vive en `Documento.archivo`


def docs_en_juego(e: Expediente) -> List[Documento]:
    """Antes de Contratación: los documentos que se le pidieron al candidato (o que ya entregó); si a nada se le ha
    mandado la liga, todos los aplicables. En Contratación/Onboarding: todo el expediente. Los internos de RH y los
    «No aplica» nunca cuentan."""
    docs = [d for d in (e.documentos or []) if not d.interno and d.estado != "no_aplica"]
    p = e.postulacion
    if p is None or p.etapa in ("Contratación", "Onboarding"):
        return docs  # el expediente de ingreso completo
    pedidos =[d for d in docs if d.solicitado_en or d.entregado or d.correccion_en or d.archivo]
    return pedidos or docs


def estado_expediente(e: Optional[Expediente]) -> dict:
    """Pendiente · Por revisar · Corrección pedida · Documentos aprobados · Excepción."""
    if e is None:
        clave = "pendiente"
    elif (e.documentos_excepcion or {}).get("en"):
        clave = "excepcion"
    else:
        docs = docs_en_juego(e)
        if any(d.estado == "rechazado" and d.correccion_en for d in docs):
            clave = "correccion"
        elif docs and all(d.aprobado for d in docs):
            clave = "aprobados"
        elif docs and all(d.entregado for d in docs):
            clave = "por_revisar"
        else:
            clave = "pendiente"
    return {"clave": clave, "texto": ESTADOS_DOCUMENTOS_EXPEDIENTE[clave]}


def liga(p: Postulacion) -> str:
    e = p.expediente
    if e is None:
        return ""
    if not e.token:
        e.token = secrets.token_urlsafe(24)
    return f"{settings.app_url}/expediente/{e.token}"


# ============================================================ acciones de RH

def aprobar(db: Session, e: Expediente, tipos: List[str], actor: str) -> List[str]:
    """Aprueba los documentos elegidos (vacío = todos los entregados). Solo lo entregado se aprueba."""
    elegidos = {_plano(t) for t in tipos or []}
    hechos = []
    for d in docs_en_juego(e):
        if elegidos and _plano(d.tipo) not in elegidos:
            continue
        if not d.entregado and not (d.estado == "pendiente" and d.archivo and multiple(d)):
            continue
        d.estado = "recibido"
        d.revisado_por = actor
        d.recibido_en = d.recibido_en or d.subido_en or datetime.now(timezone.utc)
        hechos.append(d.tipo)
    return hechos


def pedir_correccion(db: Session, e: Expediente, tipos: List[str], motivo: str, actor: str) -> List[Documento]:
    """Marca los elegidos como «Corrección pedida»: sus archivos dejan de estar vigentes (se conservan) y el candidato debe
    reemplazarlos por la misma liga."""
    elegidos = {_plano(t) for t in tipos or []}
    ahora = datetime.now(timezone.utc)
    salida = []
    for d in docs_en_juego(e):
        if _plano(d.tipo) not in elegidos:
            continue
        d.estado = "rechazado"
        d.revisado_por = ""
        d.recibido_en = None
        d.recibido_canal = ""
        d.correccion_motivo = (motivo or "").strip()[:500]
        d.correccion_por = actor
        d.correccion_en = ahora
        for a in archivos(db, d):
            a.vigente = False
        salida.append(d)
    return salida


def texto_correccion(p: Postulacion, docs: List[Documento], motivo: str, url: str) -> str:
    nombre = (p.nombre or "").split(" ")[0]
    puesto = p.vacante.titulo if p.vacante else "la vacante"
    lineas = [f"Hola{', ' + nombre if nombre else ''}. Revisamos tus documentos para {puesto} y necesitamos que vuelvas a subir:"]
    lineas += [f"• {d.tipo}" for d in docs]
    if (motivo or "").strip():
        lineas.append(f"Motivo: {motivo.strip()}")
    lineas.append(f"Súbelos aquí, por favor: {url}")
    return "\n".join(lineas)


def marcar_excepcion(e: Expediente, motivo: str, actor: str) -> dict:
    e.documentos_excepcion = {"por": actor, "motivo": (motivo or "").strip()[:500], "en": datetime.now(timezone.utc).isoformat()}
    return e.documentos_excepcion


# ============================================================ agente

def texto_solicitud(p: Postulacion, docs: List[dict], url: str) -> str:
    nombre = (p.nombre or "").split(" ")[0]
    puesto = p.vacante.titulo if p.vacante else "la vacante"
    lineas = [f"{nombre + ', p' if nombre else 'P'}ara seguir con tu postulación a {puesto} necesitamos estos documentos:"]
    lineas += [f"• {texto_documento(d)}" for d in docs]
    lineas.append(f"Súbelos aquí (puedes tomar la foto con tu celular o elegirla de tu galería): {url}")
    return "\n".join(lineas)


def _pausado(p: Postulacion) -> bool:
    return bool(((p.analisis or {}).get("motor_ruta") or {}).get("pausado"))


async def revisar(db: Session, p: Optional[Postulacion]) -> Optional[str]:
    """Cuando la actividad «Documentos» de la vacante se habilita (prefiltro aprobado), manda UNA vez la liga para subir
    los archivos por el canal del candidato. Reclama antes de enviar; nunca truena. Regresa el texto enviado."""
    if p is None or not p.activa or not p.consentimiento or _pausado(p) or p.etapa in ("Contratación", "Onboarding"):
        return None
    paso = paso_de(p)
    docs = de_vacante(p.vacante, (p.proceso or {}).get("pasos") or [], tempranos=True)
    analisis = dict(p.analisis or {})
    if paso is None or not docs or (analisis.get("documentos_vacante") or {}).get("solicitado_en"):
        return None
    from . import proceso as sproc

    puede, _ = sproc.agente_puede_actuar(p, "documentos")
    estado = next((x for x in sproc.estado_pasos(p) if x["id"] == paso["id"]), None)
    if not puede or estado is None or estado["estado"] in ("completada", "omitida", "cancelada"):
        return None
    analisis["documentos_vacante"] = {"solicitado_en": datetime.now(timezone.utc).isoformat(), "entregado": None}
    p.analisis = analisis
    db.commit()
    return (await enviar_solicitud(db, p, ACTOR))["texto"]


async def enviar_solicitud(db: Session, p: Postulacion, actor: str) -> dict:
    """Abre el expediente (con anticipación, si hace falta), agrega los documentos de la vacante y manda la liga por el
    canal del candidato. Lo usan el agente (`revisar`) y «Enviar liga de documentos» de RH. Exige consentimiento."""
    from types import SimpleNamespace

    from ..routers.candidatos import _abrir_expediente
    from .motor_ruta import _avisar

    paso = paso_de(p)
    docs = de_vacante(p.vacante, (p.proceso or {}).get("pasos") or [], tempranos=True)
    if p.expediente is None:
        exp = _abrir_expediente(db, p, SimpleNamespace(nombre=actor, correo="", id=None))
        registrar(db, actor, "expediente_anticipado", "postulacion", p.codigo, {"expediente": exp.id, "etapa": p.etapa, "motivo": "documentos_vacante"})
    pedidos = asegurar_documentos(db, p.expediente, docs)
    url = liga(p)
    texto = texto_solicitud(p, docs, url)
    entregado, detalle = await _avisar(db, p, texto, f"Tus documentos — {p.vacante.titulo if p.vacante else 'vacante'}", url,
                                       motivo="documentos", paso_id=paso["id"] if paso else "", actor=actor)
    if entregado:
        ahora = datetime.now(timezone.utc)
        canal = "correo" if detalle == "correo" else "whatsapp"
        for d in pedidos:
            if not d.solicitado_en:
                d.solicitado_en, d.solicitado_canal = ahora, canal
            d.solicitudes = [*(d.solicitudes or []), {"en": ahora.isoformat(), "canal": canal, "tipo": "solicitud", "por": actor}]
    analisis = dict(p.analisis or {})
    analisis["documentos_vacante"] = {**(analisis.get("documentos_vacante") or {}), "solicitado_en": datetime.now(timezone.utc).isoformat(),
                                      "entregado": entregado, "detalle": detalle[:200]}
    p.analisis = analisis
    registrar(db, actor, "documentos_vacante_solicitados", "postulacion", p.codigo,
              {"documentos": [d["nombre"] for d in docs], "entregado": entregado, "detalle": detalle[:200]})
    db.commit()
    return {"texto": texto, "liga": url, "enviado": entregado, "detalle": detalle,
            "resultados": [{"destinatario": "candidato", "canal": "correo" if detalle == "correo" else "whatsapp", "enviado": entregado,
                            "detalle": detalle}]}


def liga_si_intercepta(p: Postulacion) -> str:
    """Una foto/archivo por el chat ANTES de Contratación, con documentos de la vacante por subir: se contesta con la liga
    (los archivos se suben ahí, un renglón por documento). Regresa la liga o ""."""
    if p is None or p.etapa in ("Contratación", "Onboarding") or paso_de(p) is None or p.expediente is None:
        return ""
    nombres = {_plano(d["nombre"]) for d in de_vacante(p.vacante)}
    faltan = [d for d in p.expediente.documentos if _plano(d.tipo) in nombres and not d.aprobado]
    return liga(p) if faltan else ""
