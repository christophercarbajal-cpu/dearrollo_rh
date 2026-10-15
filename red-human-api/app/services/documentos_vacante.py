"""Cambio 25 (2026-10-12) — módulo de recolección y revisión de documentos.

- La vacante define «Documentos y fotos que se piden» (`Vacante.documentos_solicitados`, [{nombre, cantidad, foto}]); la
  IA lo llena si la descripción los menciona (`detectar`, determinista: nunca inventa documentos que nadie mencionó).
- Cambio 36: la vacante NUNCA modifica el proceso. Si la ruta trae «Documentos» antes de Contratación, esa actividad
  recoge los documentos de la vacante (`con_documentos_vacante`); si no, el campo ni se muestra en la vacante.
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
                      Postulacion, caras_documento, clave_documento, normalizar_documentos_vacante, registrar)

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
    """Cambio 36 («El proceso manda»): la vacante NUNCA modifica el proceso. Si la ruta ya trae «Documentos» antes de
    Contratación (Masivos con documentos), ESA actividad recoge los documentos de la vacante; si no la trae, la ruta queda
    tal cual (ya no se inyecta una actividad «Documentos» después del prefiltro)."""
    from .proceso import _indice

    docs = de_vacante(v, (proc or {}).get("pasos") or [], tempranos=True)
    if not docs or not proc or not proc.get("pasos"):
        return proc
    pasos = [dict(x) for x in proc["pasos"]]
    previo = next((x for x in pasos if x["tipo"] == "documentos" and _indice(x["etapa"]) < _indice("Contratación")), None)
    if previo is None:
        return proc
    previo["documentos"] = list(dict.fromkeys([*(previo.get("documentos") or []), *[d["nombre"] for d in docs]]))
    previo["documentos_vacante"] = True
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
        doc = next((x for x in e.documentos if clave_documento(x.tipo) == clave_documento(d["nombre"]) and not x.interno), None)
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
                      convertido_de: str = "", cara: str = "") -> Optional[ArchivoDocumento]:
    """Guarda el archivo como su PROPIO registro. Un documento de un solo archivo lo reemplaza (el anterior queda como no
    vigente); uno de N archivos conserva los últimos N. Cambio 42: con `cara` (Frente / Reverso) solo reemplaza esa cara."""
    if not _tablas():
        return None
    try:
        reg = ArchivoDocumento(documento_id=doc.id, expediente_id=e.id, archivo=ruta, nombre_archivo=validado.nombre,
                               mime=validado.mime, tamano=validado.tamano, canal=canal, subido_por=subido_por[:150],
                               convertido_de=convertido_de, cara=cara, vigente=True, subido_en=datetime.now(timezone.utc))
        db.add(reg)
        db.flush()
        vigentes = archivos(db, doc)
        if cara:
            for viejo in vigentes:
                if viejo.id != reg.id and (viejo.cara == cara or not viejo.cara):
                    viejo.vigente = False
            return reg
        tope = max(1, int(doc.cantidad or 1))
        for viejo in vigentes[:-tope] if len(vigentes) > tope else []:
            viejo.vigente = False
        return reg
    except Exception:  # noqa: BLE001
        return None


def conteo(db: Session, doc: Documento) -> int:
    if caras_documento(doc.tipo) and caras_recibidas(db, doc):
        return len(caras_recibidas(db, doc))
    n = len(archivos(db, doc))
    if n or not doc.archivo or archivos(db, doc, solo_vigentes=False):
        return n
    return 1  # documento previo al módulo: su único archivo vive en `Documento.archivo`


def docs_en_juego(e: Expediente) -> List[Documento]:
    """Antes de Contratación: los documentos que pide la VACANTE (Cambio 42); si la vacante no define ninguno, los que se
    le pidieron al candidato (o que ya entregó) y, si a nada se le ha mandado la liga, todos los aplicables. En
    Contratación/Onboarding: todo el expediente. Los internos de RH y los «No aplica» nunca cuentan."""
    docs = [d for d in (e.documentos or []) if not d.interno and d.estado != "no_aplica"]
    p = e.postulacion
    if p is None or p.etapa in ("Contratación", "Onboarding"):
        return docs  # el expediente de ingreso completo
    vac = {clave_documento(d["nombre"]) for d in de_vacante(p.vacante, (p.proceso or {}).get("pasos") or [], tempranos=True)} if p.vacante else set()
    de_la_vacante = [d for d in docs if clave_documento(d.tipo) in vac]
    if de_la_vacante:
        return de_la_vacante
    pedidos = [d for d in docs if d.solicitado_en or d.entregado or d.correccion_en or d.archivo]
    return pedidos or docs


def progreso(e: Optional[Expediente]) -> int:
    """Cambio 42: % del expediente sobre los documentos REQUERIDOS (antes de Contratación, los de la vacante; en
    Contratación/Onboarding, los obligatorios del expediente — `Expediente.progreso`)."""
    if e is None:
        return 0
    p = e.postulacion
    if p is None or p.etapa in ("Contratación", "Onboarding"):
        return e.progreso
    docs = [d for d in docs_en_juego(e) if d.obligatorio]
    return round(100 * sum(1 for d in docs if d.aprobado) / len(docs)) if docs else 0


def caras_recibidas(db: Session, doc: Documento) -> List[str]:
    """Caras ya subidas (vigentes) de un documento de dos caras."""
    return sorted({a.cara for a in archivos(db, doc) if a.cara})


def faltantes(e: Expediente) -> List[Documento]:
    """Cambio 42: lo que el candidato todavía debe subir (la liga muestra SOLO esto)."""
    return [d for d in docs_en_juego(e) if not d.entregado and not d.aprobado]


def documento_publico(db: Session, d: Documento) -> dict:
    caras = caras_documento(d.tipo)
    hechas = caras_recibidas(db, d) if caras else []
    return {"tipo": d.tipo, "estado": d.estado, "obligatorio": d.obligatorio, "cantidad": len(caras) or int(d.cantidad or 1),
            "esFoto": bool(d.es_foto), "recibidos": len(hechas) if caras else conteo(db, d),
            "caras": [{"cara": c, "recibida": c in hechas} for c in caras],
            "correccion": d.correccion_motivo if d.estado == "rechazado" and d.correccion_en else ""}


TEXTO_LISTO = "Listo, {nombre}. Ya recibimos tus documentos. Te avisaremos por WhatsApp el siguiente paso."


def vista_publica(db: Session, e: Expediente) -> dict:
    """La liga del candidato: empresa de la vacante en la cabecera, SOLO los faltantes y, al completar los obligatorios,
    el mensaje final (la actividad queda «Por revisar»)."""
    from ..serial import nombre_empresa_candidato

    p = e.postulacion
    nombre = ((e.candidato.nombre if e.candidato else "") or "").split(" ")[0]
    en_juego = docs_en_juego(e)
    pendientes = faltantes(e)
    completo = bool(en_juego) and not [d for d in pendientes if d.obligatorio]
    return {"candidato": e.candidato.nombre if e.candidato else "", "nombre": nombre,
            "empresa": nombre_empresa_candidato(p.vacante) if p is not None and p.vacante is not None else "",
            "puesto": e.puesto or (p.vacante.titulo if p is not None and p.vacante is not None else ""),
            "estado": e.estado, "documentos": [documento_publico(db, d) for d in pendientes],
            "completo": completo, "mensajeFinal": TEXTO_LISTO.format(nombre=nombre or "").replace("Listo, .", "Listo.") if completo else ""}


def siguen_a_solicitud(p: Optional[Postulacion]) -> bool:
    """Cambio 42: «Documentos» sigue INMEDIATAMENTE a «Solicitud web» (sin prefiltro en medio) → la carga de documentos
    continúa en la misma página web al terminar la solicitud."""
    pasos = [x for x in ((p.proceso or {}).get("pasos") or []) if not x.get("heredado")] if p is not None else []
    i = next((k for k, x in enumerate(pasos) if x["tipo"] == "solicitud_web"), None)
    if i is None:
        return False
    siguiente = next((x for x in pasos[i + 1:] if x["tipo"] != "analisis_cv"), None)
    from .proceso import _indice

    return bool(siguiente and siguiente["tipo"] == "documentos" and _indice(siguiente.get("etapa") or "Prefiltro") < _indice("Contratación"))


async def preparar_web(db: Session, p: Postulacion) -> Optional[dict]:
    """Abre el expediente y deja listos los documentos de la vacante para subirlos en la MISMA página de la solicitud.
    No manda nada por WhatsApp: si el candidato cierra la página sin terminar, `recordar_web` le manda la liga después."""
    from types import SimpleNamespace

    from ..routers.candidatos import _abrir_expediente

    docs = de_vacante(p.vacante, (p.proceso or {}).get("pasos") or [], tempranos=True)
    if not docs or not p.consentimiento:
        return None
    if p.expediente is None:
        exp = _abrir_expediente(db, p, SimpleNamespace(nombre=ACTOR, correo="", id=None))
        registrar(db, ACTOR, "expediente_anticipado", "postulacion", p.codigo, {"expediente": exp.id, "etapa": p.etapa, "motivo": "documentos_web"})
    pedidos = asegurar_documentos(db, p.expediente, docs)
    ahora = datetime.now(timezone.utc)
    for d in pedidos:
        if not d.solicitado_en:
            d.solicitado_en, d.solicitado_canal = ahora, "web"
    url = liga(p)
    analisis = dict(p.analisis or {})
    analisis["documentos_vacante"] = {**(analisis.get("documentos_vacante") or {}), "solicitado_en": ahora.isoformat(), "canal": "web",
                                      "web_en": ahora.isoformat(), "entregado": True}
    p.analisis = analisis
    registrar(db, ACTOR, "documentos_vacante_en_web", "postulacion", p.codigo, {"documentos": [d["nombre"] for d in docs]})
    db.commit()
    return {"token": p.expediente.token, "liga": url, **vista_publica(db, p.expediente)}


TEXTO_RECUPERACION = "{nombre}, cuando tengas tus documentos, súbelos aquí: {liga}"


async def recordar_web(db: Session, minutos: Optional[int] = None) -> List[str]:
    """Cambio 42: quien empezó a subir sus documentos en la página de la solicitud y la cerró sin terminar recibe UNA vez por
    WhatsApp «[nombre], cuando tengas tus documentos, súbelos aquí: [liga]» (la liga muestra solo los faltantes)."""
    from datetime import timedelta

    from .motor_ruta import _avisar

    espera = timedelta(minutes=minutos if minutos is not None else settings.documentos_web_recordatorio_min)
    hechos: List[str] = []
    for p in db.query(Postulacion).filter(Postulacion.activa.is_(True)).all():
        dv = (p.analisis or {}).get("documentos_vacante") or {}
        if not dv.get("web_en") or dv.get("recordatorio_web_en") or p.expediente is None or _pausado(p):
            continue
        try:
            desde = datetime.fromisoformat(dv["web_en"])
        except (TypeError, ValueError):
            continue
        if desde.tzinfo is None:
            desde = desde.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - desde < espera:
            continue
        pendientes = [d for d in faltantes(p.expediente) if d.obligatorio]
        analisis = dict(p.analisis or {})
        analisis["documentos_vacante"] = {**dv, "recordatorio_web_en": datetime.now(timezone.utc).isoformat()}
        p.analisis = analisis
        db.commit()  # reclamado antes de enviar: nunca sale dos veces
        if not pendientes:
            continue
        nombre = (p.nombre or "").split(" ")[0] if p.nombre and not p.nombre.startswith("Candidato") else ""
        texto = TEXTO_RECUPERACION.format(nombre=nombre or "Hola", liga=liga(p))
        paso = paso_de(p)
        entregado, detalle = await _avisar(db, p, texto, "Tus documentos", liga(p), motivo="documentos", paso_id=paso["id"] if paso else "")
        registrar(db, ACTOR, "documentos_web_recordatorio", "postulacion", p.codigo,
                  {"faltan": [d.tipo for d in pendientes], "entregado": entregado, "detalle": detalle[:200]})
        db.commit()
        hechos.append(p.codigo)
    return hechos


async def job_recordar_web() -> None:
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        await recordar_web(db)
    except Exception as ex:  # noqa: BLE001
        db.rollback()
        print(f"[documentos] recordatorio de la web: {ex}", flush=True)
    finally:
        db.close()


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
    elegidos = {clave_documento(t) for t in tipos or []}
    hechos = []
    for d in docs_en_juego(e):
        if elegidos and clave_documento(d.tipo) not in elegidos:
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
    elegidos = {clave_documento(t) for t in tipos or []}
    ahora = datetime.now(timezone.utc)
    salida = []
    for d in docs_en_juego(e):
        if clave_documento(d.tipo) not in elegidos:
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

def _tras_entrevista(p: Postulacion) -> bool:
    """¿La actividad de documentos llega justo después de una Entrevista Red Human ya evaluada?"""
    return any(getattr(e, "estado", "") == "evaluada" for e in (getattr(p, "entrevistas", None) or []))


def texto_solicitud(p: Postulacion, docs: List[dict], url: str) -> str:
    nombre = (p.nombre or "").split(" ")[0]
    puesto = p.vacante.titulo if p.vacante else "la vacante"
    if _tras_entrevista(p):
        # 2026-10-15 (Fletes «Entrevista en video»): al terminar la entrevista con Red Human — «Gracias… sube tus documentos aquí»
        lineas = [f"Gracias{', ' + nombre if nombre else ''}. Para seguir con tu postulación a {puesto} necesitamos estos documentos:"]
    else:
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
    nombres = {clave_documento(d["nombre"]) for d in de_vacante(p.vacante)}
    faltan = [d for d in p.expediente.documentos if clave_documento(d.tipo) in nombres and not d.aprobado]
    return liga(p) if faltan else ""
