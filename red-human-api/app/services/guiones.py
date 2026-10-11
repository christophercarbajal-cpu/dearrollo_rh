"""Plantillas de conversación por vacante (2026-10-09, «Generación de contenido según la ruta»).

Cada vacante guarda SUS guiones, uno por actividad de su ruta — nunca en la Cuenta ni en la plantilla de vacante, y
ajustar la ruta de la vacante nunca toca la plantilla general de la Cuenta (la ruta de la vacante es una copia):

  * «Prefiltro · Web»                              → `Vacante.preguntas_filtro`            (actividad prefiltro_web o solicitud_web)
  * «Prefiltro · WhatsApp»                         → `Vacante.preguntas_filtro_whatsapp`   (actividad prefiltro_whatsapp)
  * «Entrevista Red Human por WhatsApp — guion»    → `Vacante.guiones.secciones.entrevista_whatsapp` (actividad entrevista_whatsapp)
  * «Entrevista con avatar — guion»                → `Vacante.guiones.secciones.entrevista_avatar`   (actividad entrevista_agente)
  * «Llamada — guion»                              → `Vacante.guiones.secciones.llamada`             (actividad llamada_agente)

Los prefiltros siguen viviendo en sus columnas (una sola fuente para el portal, el agente y el prefiltro
conversacional); `Vacante.guiones.meta` lleva la trazabilidad de TODAS las secciones: cuándo se generó, con qué datos de
la vacante (`huella`) y si RH la editó a mano (`editado`, comparando contra lo generado — lo decide el servidor).
"""

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

SECCIONES: Dict[str, dict] = {
    "prefiltro_web": {"titulo": "Prefiltro · Web", "tipos": ("prefiltro_web", "solicitud_web"), "clase": "prefiltro",
                      "campo": "preguntas_filtro"},
    "prefiltro_whatsapp": {"titulo": "Prefiltro · WhatsApp", "tipos": ("prefiltro_whatsapp",), "clase": "prefiltro",
                           "campo": "preguntas_filtro_whatsapp"},
    "entrevista_whatsapp": {"titulo": "Entrevista Red Human por WhatsApp — guion", "tipos": ("entrevista_whatsapp",), "clase": "guion"},
    "entrevista_avatar": {"titulo": "Entrevista con avatar — guion", "tipos": ("entrevista_agente",), "clase": "guion"},
    "llamada": {"titulo": "Llamada — guion", "tipos": ("llamada_agente",), "clase": "guion"},
}
SECCION_DE_PASO = {"entrevista_agente": "entrevista_avatar", "entrevista_whatsapp": "entrevista_whatsapp",
                   "llamada_agente": "llamada", "prefiltro_whatsapp": "prefiltro_whatsapp", "prefiltro_web": "prefiltro_web",
                   "solicitud_web": "prefiltro_web"}
TIPOS_PREGUNTA_CERRADA = ("si_no", "numero", "opcion")
MAX_PREGUNTAS = 15


class ErrorGuion(Exception):
    def __init__(self, status: int, mensaje: str):
        super().__init__(mensaje)
        self.status, self.mensaje = status, mensaje


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(valor) -> str:
    return hashlib.sha256(json.dumps(valor, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


# ============================================================ ruta y datos de la vacante


def tipos_de_ruta(pasos: Optional[list]) -> List[str]:
    return [x.get("tipo") for x in (pasos or []) if isinstance(x, dict) and not x.get("heredado")]


def pasos_de_vacante(db: Optional[Session], v) -> list:
    """Pasos de la ruta EFECTIVA de la vacante: su copia o, si no tiene, la que le tocaría (predeterminada de la Cuenta
    o la de respaldo) — la misma cascada que reciben sus candidatos."""
    from . import proceso as sproc

    if v is not None and sproc.tiene_proceso(v):
        return list(v.proceso["pasos"])
    try:
        return list(sproc.ruta_para(db, getattr(v, "cuenta_id", None), None).get("pasos") or [])
    except Exception:  # noqa: BLE001 — sin tablas de proceso: la ruta de respaldo en código
        return list(sproc.ruta_base(sproc.RUTA_RESPALDO)["pasos"])


def pide_cv(pasos: Optional[list]) -> bool:
    """¿La ruta exige CV? Análisis de CV o una «Solicitud web» con CV. Sin CV en la ruta, la publicación no lo pide."""
    for x in pasos or []:
        if not isinstance(x, dict) or x.get("heredado"):
            continue
        if x.get("tipo") == "analisis_cv" or (x.get("tipo") == "solicitud_web" and x.get("con_cv")):
            return True
    return False


def secciones_aplicables(tipos: List[str]) -> List[str]:
    """Secciones que corresponden a las actividades de la ruta, en el orden fijo. Lo que no está en la ruta no existe."""
    presentes = set(tipos or [])
    return [k for k, s in SECCIONES.items() if presentes & set(s["tipos"])]


DATOS_HUELLA = ("titulo", "responsabilidades", "requisitos", "requisitos_deseables", "ubicacion", "modalidad", "sueldo",
                "enfoque_entrevista", "horario")


def huella_datos(datos: dict) -> str:
    """Huella de los datos de la vacante que alimentan los guiones: si cambia, lo generado quedó desactualizado. El
    perfil del oficio solo cuenta con enfoque operativo (las vacantes de otros enfoques conservan su huella)."""
    base = {k: datos.get(k) for k in DATOS_HUELLA}
    if datos.get("enfoque_entrevista") == "operativo":
        base["perfil_operativo"] = datos.get("perfil_operativo")
    return _hash(base)


def datos_de_vacante(v) -> dict:
    from ..routers.vacantes import requisitos_lista

    return {"titulo": v.titulo or "", "responsabilidades": list(v.responsabilidades or []),
            "requisitos": requisitos_lista(v.requisitos), "requisitos_deseables": list(v.requisitos_deseables or []),
            "ubicacion": v.ubicacion or "", "modalidad": v.modalidad or "", "sueldo": v.sueldo or "",
            "enfoque_entrevista": v.enfoque_entrevista or "profesional", "horario": "",
            **({"perfil_operativo": _perfil_operativo(v)} if (v.enfoque_entrevista or "") == "operativo" else {})}


def _perfil_operativo(v) -> Optional[dict]:
    from . import entrevista_operativa as eop

    return eop.perfil_de_vacante(v)


# ============================================================ normalización (lo que RH edita y lo que genera la IA)


def _texto(x, n: int = 400) -> str:
    return " ".join(str(x or "").split())[:n]


def normalizar_pregunta(q) -> Optional[dict]:
    """Pregunta de prefiltro: SIEMPRE cerrada (Sí/No/Parcial, número con rangos u opción múltiple). Conserva las claves
    propias de la ruta (reconfirma, minimo, opciones_validas, clave, pregunta_chat, solo_con_ubicacion)."""
    if isinstance(q, str):
        q = {"pregunta": q, "tipo": "si_no"}
    if not isinstance(q, dict) or not _texto(q.get("pregunta")):
        return None
    tipo = q.get("tipo") if q.get("tipo") in TIPOS_PREGUNTA_CERRADA else None
    from . import ia

    if ia.es_autopercepcion(_texto(q.get("pregunta"))):
        raise ErrorGuion(400, f"«{_texto(q.get('pregunta'), 80)}»: el prefiltro no pregunta autopercepciones (no se pueden "
                              "verificar). Pregunta por un requisito concreto (experiencia, documento, disponibilidad).")
    if tipo is None:
        raise ErrorGuion(400, f"«{_texto(q.get('pregunta'), 80)}»: los prefiltros solo admiten preguntas cerradas "
                              "(Sí / No, número o una opción de una lista).")
    salida = {"pregunta": _texto(q["pregunta"]), "tipo": tipo, "valida": _texto(q.get("valida") or q["pregunta"], 200),
              "respuesta_esperada": _texto(q.get("respuesta_esperada") or ("Sí" if tipo == "si_no" else ""), 120),
              "descarta": bool(q.get("descarta")),
              "opciones": [_texto(o, 80) for o in (q.get("opciones") or []) if _texto(o)][:8]}
    if tipo == "si_no" and not salida["opciones"]:
        salida["opciones"] = ["Sí", "No", "Parcial"]
    if tipo in ("numero", "opcion") and len(salida["opciones"]) < 2:
        raise ErrorGuion(400, f"«{salida['pregunta'][:80]}»: una pregunta de número u opción necesita al menos dos opciones.")
    for k in ("reconfirma", "clave", "pregunta_chat"):
        if _texto(q.get(k)):
            salida[k] = _texto(q[k], 200)
    if q.get("minimo") not in (None, ""):
        try:
            salida["minimo"] = float(q["minimo"])
        except (TypeError, ValueError):
            pass
    if isinstance(q.get("opciones_validas"), list):
        salida["opciones_validas"] = [_texto(o, 80) for o in q["opciones_validas"] if _texto(o)]
    if q.get("solo_con_ubicacion"):
        salida["solo_con_ubicacion"] = True
    return salida


def normalizar_prefiltro(preguntas) -> List[dict]:
    salida = [x for x in (normalizar_pregunta(q) for q in (preguntas or [])) if x]
    if len(salida) > MAX_PREGUNTAS:
        raise ErrorGuion(400, f"Un prefiltro admite como máximo {MAX_PREGUNTAS} preguntas.")
    return salida


def normalizar_guion(g) -> dict:
    """Guion de entrevista o llamada: enfoque + temas + preguntas ABIERTAS (nunca preguntas de prefiltro)."""
    from . import ia

    g = g if isinstance(g, dict) else {}
    preguntas = [_texto(x, 300) for x in (g.get("preguntas") or []) if _texto(x)][:MAX_PREGUNTAS]
    preguntas = ia.abrir_preguntas(preguntas)
    temas = [_texto(x, 160) for x in (g.get("temas") or []) if _texto(x)][:MAX_PREGUNTAS]
    if not preguntas and not temas:
        return {}
    salida = {"enfoque": _texto(g.get("enfoque"), 500), "temas": temas or [p.rstrip("?.").lstrip("¿") for p in preguntas],
              "preguntas": preguntas}
    # 2026-10-10: guion OPERATIVO — se conserva su estructura (secciones, oficio y perfil congelado para la evaluación)
    from . import entrevista_operativa as eop

    op = eop.normalizar_operativo(g.get("operativo"))
    if op:
        salida["operativo"] = op
    return salida


def contenido_de(v, clave: str):
    s = SECCIONES[clave]
    if s["clase"] == "prefiltro":
        return [q for q in (getattr(v, s["campo"]) or []) if isinstance(q, dict)]
    return dict(((v.guiones or {}).get("secciones") or {}).get(clave) or {})


def vacio(contenido) -> bool:
    if isinstance(contenido, list):
        return not contenido
    return not (contenido or {}).get("preguntas") and not (contenido or {}).get("temas")


# ============================================================ vista y guardado


def vista(v, pasos: Optional[list] = None, db: Optional[Session] = None) -> dict:
    """Lo que ve RH: cada sección con su contenido, si aplica a la ruta y su trazabilidad."""
    if pasos is None:
        pasos = pasos_de_vacante(db, v)
    aplican = secciones_aplicables(tipos_de_ruta(pasos))
    meta = dict((v.guiones or {}).get("meta") or {})
    huella = huella_datos(datos_de_vacante(v))
    salida = []
    for clave, s in SECCIONES.items():
        m = meta.get(clave) or {}
        contenido = contenido_de(v, clave)
        salida.append({
            "clave": clave, "titulo": s["titulo"], "clase": s["clase"], "aplica": clave in aplican, "contenido": contenido,
            "vacia": vacio(contenido), "generadoEn": m.get("generado_en"), "generadoIa": m.get("generado_ia"),
            "editado": bool(m.get("editado")), "editadoPor": m.get("editado_por") or "", "editadoEn": m.get("editado_en"),
            "desactualizado": bool(m.get("huella")) and m.get("huella") != huella and not vacio(contenido),
        })
    perfil = None
    if (getattr(v, "enfoque_entrevista", "") or "") == "operativo":
        from . import entrevista_operativa as eop

        perfil = eop.perfil_de_vacante(v)
    return {"secciones": salida, "aplican": aplican, "meta": meta, "huella": huella,
            "desactualizado": any(x["desactualizado"] for x in salida if x["aplica"]),
            # 2026-10-10: perfil del oficio de la entrevista operativa (copia de la vacante o el detectado en la biblioteca)
            "perfilOperativo": perfil, "perfilOperativoPropio": bool((v.guiones or {}).get("perfil_operativo"))}


def _marcar(v) -> None:
    flag_modified(v, "guiones")


def guardar_seccion(v, clave: str, contenido, por: str, generado: bool = False, generado_ia: Optional[bool] = None,
                    huella: str = "") -> bool:
    """Escribe una sección. `generado` = la escribió el generador (queda como referencia para detectar ediciones);
    si no, es una edición de RH y `editado` se decide comparando contra lo último generado. Solo lo que CAMBIA se valida
    con las reglas estrictas (contenido previo intacto, aunque sea de antes de estas reglas, se respeta). Regresa si
    cambió."""
    if clave not in SECCIONES:
        raise ErrorGuion(404, "Sección de guion desconocida.")
    s = SECCIONES[clave]
    anterior = contenido_de(v, clave)
    cambio = generado or _hash(contenido if contenido is not None else []) != _hash(anterior)
    if cambio:
        limpio = normalizar_prefiltro(contenido) if s["clase"] == "prefiltro" else normalizar_guion(contenido)
    else:
        limpio = anterior
    g = copy.deepcopy(v.guiones or {})
    g.setdefault("secciones", {})
    g.setdefault("meta", {})
    m = dict(g["meta"].get(clave) or {})
    if cambio:
        if s["clase"] == "prefiltro":
            setattr(v, s["campo"], limpio)
        elif limpio:
            g["secciones"][clave] = limpio
        else:
            g["secciones"].pop(clave, None)
    if generado:
        m.update({"generado_en": _ahora(), "generado_ia": bool(generado_ia), "generado_hash": _hash(limpio), "editado": False,
                  "editado_por": "", "editado_en": None, "huella": huella or m.get("huella", "")})
    elif m.get("generado_hash"):
        editado = _hash(limpio) != m["generado_hash"]
        if editado != bool(m.get("editado")) or (editado and cambio):
            m.update({"editado": editado, "editado_por": por if editado else "", "editado_en": _ahora() if editado else None})
    elif cambio and not vacio(limpio):
        m.update({"editado": True, "editado_por": por, "editado_en": _ahora()})  # escrito a mano, nunca generado
    if m:
        g["meta"][clave] = m
    v.guiones = g
    _marcar(v)
    return bool(cambio and _hash(limpio) != _hash(anterior))


def guardar_desde_formulario(v, entrada: Optional[dict], por: str, preguntas_web=None, preguntas_whatsapp=None) -> List[str]:
    """Formulario de vacante (alta o edición): `entrada` = {secciones: {clave: contenido}, meta?: {clave: {...}}}. La
    meta de generación que trae el formulario (lo que acaba de generar la API y aún no se guardaba) se respeta; el
    servidor decide `editado`. Los prefiltros llegan por sus campos de siempre. Regresa las secciones que cambiaron."""
    entrada = entrada or {}
    meta_in = entrada.get("meta") if isinstance(entrada.get("meta"), dict) else {}
    g = copy.deepcopy(v.guiones or {})
    g.setdefault("meta", {})
    if "perfil_operativo" in entrada:  # 2026-10-10: copia del perfil del oficio SOLO para esta vacante
        g = guardar_perfil_operativo(v, entrada.get("perfil_operativo"), g)
    for clave, m in meta_in.items():
        # solo la meta de GENERACIÓN (de la respuesta de /generar); `editado` lo recalcula el servidor
        if clave in SECCIONES and isinstance(m, dict) and m.get("generado_hash"):
            actual = dict(g["meta"].get(clave) or {})
            if m.get("generado_hash") != actual.get("generado_hash"):
                actual.update({k: m.get(k) for k in ("generado_en", "generado_ia", "generado_hash", "huella")})
                actual.update({"editado": False, "editado_por": "", "editado_en": None})
                g["meta"][clave] = actual
    v.guiones = g
    _marcar(v)
    cambiaron = []
    secciones_in = entrada.get("secciones") if isinstance(entrada.get("secciones"), dict) else {}
    pares = [(k, c) for k, c in secciones_in.items() if k in SECCIONES and SECCIONES[k]["clase"] == "guion"]
    if preguntas_web is not None:
        pares.append(("prefiltro_web", preguntas_web))
    if preguntas_whatsapp is not None:
        pares.append(("prefiltro_whatsapp", preguntas_whatsapp))
    for clave, contenido in pares:
        if guardar_seccion(v, clave, contenido, por):
            cambiaron.append(clave)
    return cambiaron


def guardar_perfil_operativo(v, entrada, g: Optional[dict] = None) -> dict:
    """Guarda (o quita, con None) la copia del perfil del oficio en la vacante. La Biblioteca de Perfiles nunca se edita."""
    from . import entrevista_operativa as eop

    g = copy.deepcopy(v.guiones or {}) if g is None else g
    actual = g.get("perfil_operativo") or {}
    if isinstance(entrada, dict) and set(entrada) <= {"oficio"} and entrada.get("oficio") == actual.get("oficio"):
        return g  # el formulario solo reafirma el oficio: se conservan los ajustes de la vacante
    if entrada in (None, {}, ""):
        g.pop("perfil_operativo", None)
    else:
        try:
            g["perfil_operativo"] = eop.normalizar_perfil(entrada)
        except ValueError as e:
            raise ErrorGuion(400, str(e))
    v.guiones = g
    _marcar(v)
    return g


# ============================================================ generación (Bloque 2)


def ficha_guion(datos: dict, empresa: str = "") -> "ia.FichaGuion":
    from . import entrevista_operativa as eop
    from . import ia

    perfil = None
    if (datos.get("enfoque_entrevista") or "") == "operativo":
        perfil = datos.get("perfil_operativo")
        if not isinstance(perfil, dict) or perfil.get("oficio") not in eop.BIBLIOTECA:
            oficio = eop.detectar_oficio(datos.get("titulo") or "", list(datos.get("responsabilidades") or []))
            perfil = eop.perfil_base(oficio) if oficio else None
    return ia.FichaGuion(perfil_operativo=perfil, titulo=datos.get("titulo") or "", responsabilidades=list(datos.get("responsabilidades") or []),
                         requisitos_indispensables=list(datos.get("requisitos") or []),
                         requisitos_deseables=list(datos.get("requisitos_deseables") or []), ubicacion=datos.get("ubicacion") or "",
                         modalidad=datos.get("modalidad") or "", horario=datos.get("horario") or "",
                         sueldo="" if (datos.get("sueldo") or "") == "A convenir" else (datos.get("sueldo") or ""),
                         empresa=empresa, enfoque_entrevista=datos.get("enfoque_entrevista") or "profesional")


def generar(datos: dict, pasos: list, actuales: Optional[dict] = None, conservar: Optional[List[str]] = None,
            solo: Optional[List[str]] = None, empresa: str = "") -> dict:
    """Genera las secciones de la ruta (o solo `solo`), sin guardar. `actuales` = {clave: contenido vigente};
    `conservar` = secciones que NO se tocan (ediciones de RH que pidió conservar). Regresa {secciones, meta, aplican,
    conservadas, generadas, ia, huella}. Lo que no está en la ruta no se genera."""
    from . import ia

    actuales = actuales or {}
    aplican = secciones_aplicables(tipos_de_ruta(pasos))
    objetivo = [c for c in aplican if not solo or c in solo]
    conservadas = [c for c in objetivo if c in (conservar or []) and not vacio(actuales.get(c))]
    claves = [c for c in objetivo if c not in conservadas]
    huella = huella_datos(datos)
    web_actual = actuales.get("prefiltro_web") if "prefiltro_web" not in claves else None
    crudo, con_ia = ia.generar_guiones(ficha_guion(datos, empresa), claves, web_actual=web_actual,
                                       hay_web="prefiltro_web" in aplican, hay_whatsapp="prefiltro_whatsapp" in aplican)
    secciones, meta = {}, {}
    for c in claves:
        limpio = normalizar_prefiltro(crudo.get(c)) if SECCIONES[c]["clase"] == "prefiltro" else normalizar_guion(crudo.get(c))
        secciones[c] = limpio
        meta[c] = {"generado_en": _ahora(), "generado_ia": con_ia, "generado_hash": _hash(limpio), "huella": huella,
                   "editado": False, "editado_por": "", "editado_en": None}
    return {"secciones": secciones, "meta": meta, "aplican": aplican, "conservadas": conservadas, "generadas": claves,
            "ia": con_ia, "huella": huella}


def editadas(v, claves: List[str]) -> List[str]:
    meta = (v.guiones or {}).get("meta") or {}
    return [c for c in claves if (meta.get(c) or {}).get("editado") and not vacio(contenido_de(v, c))]


def generar_para_vacante(db: Optional[Session], v, por: str, sobrescribir_editadas: bool = False,
                         solo: Optional[List[str]] = None) -> dict:
    """Genera y GUARDA en la vacante. Las secciones editadas a mano se conservan salvo `sobrescribir_editadas`."""
    from ..serial import nombre_empresa_candidato

    pasos = pasos_de_vacante(db, v)
    aplican = secciones_aplicables(tipos_de_ruta(pasos))
    objetivo = [c for c in aplican if not solo or c in solo]
    conservar = [] if sobrescribir_editadas else editadas(v, objetivo)
    r = generar(datos_de_vacante(v), pasos, {c: contenido_de(v, c) for c in SECCIONES}, conservar=conservar, solo=solo,
                empresa=nombre_empresa_candidato(v) if getattr(v, "cuenta", None) is not None else (v.empresa or ""))
    for c, contenido in r["secciones"].items():
        guardar_seccion(v, c, contenido, por, generado=True, generado_ia=r["ia"], huella=r["huella"])
    return r


def asegurar(db: Session, v, clave: str, motivo: str = "") -> object:
    """Seguro de ejecución (JIT): el agente NUNCA arranca una conversación sin guion. Si la vacante no tiene la sección
    que necesita la actividad (p. ej. un candidato antiguo de una vacante previa a los guiones), se genera AHORA, se guarda
    en la vacante (la reutilizan los siguientes candidatos) y queda en bitácora. Regresa el contenido."""
    from ..models import registrar
    from ..serial import nombre_empresa_candidato

    if v is None or clave not in SECCIONES:
        return [] if SECCIONES.get(clave, {}).get("clase") == "prefiltro" else {}
    actual = contenido_de(v, clave)
    if not vacio(actual):
        return actual
    pasos = pasos_de_vacante(db, v)
    aplican = secciones_aplicables(tipos_de_ruta(pasos))
    datos = datos_de_vacante(v)
    from . import ia

    crudo, con_ia = ia.generar_guiones(ficha_guion(datos, nombre_empresa_candidato(v)), [clave],
                                       web_actual=contenido_de(v, "prefiltro_web") or None,
                                       hay_web="prefiltro_web" in aplican or clave == "prefiltro_web",
                                       hay_whatsapp=clave == "prefiltro_whatsapp")
    guardar_seccion(v, clave, crudo.get(clave), "Red Human", generado=True, generado_ia=con_ia, huella=huella_datos(datos))
    registrar(db, "Red Human", "guion_generado_jit", "vacante", v.codigo, {"seccion": clave, "ia": con_ia, "motivo": motivo[:200]})
    return contenido_de(v, clave)
