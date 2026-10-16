"""Cambio 50 (2026-10-15) — la Solicitud web ES el prefiltro web.

La solicitud web trae el paso «Unas preguntas»: las preguntas filtro de la vacante (`Vacante.preguntas_filtro`) + el
interés en el puesto + el sueldo (y, si la vacante lo pide, edad o colonia: `Vacante.guiones["solicitud_web_extra"]`).
Al ENVIAR, si una respuesta coincide con el «Descarta si» de una pregunta, la postulación se descarta en ese momento con
el mensaje GENÉRICO (sin motivo; el motivo queda solo en el historial y en la bitácora). Lo que no descarta se registra en
la ficha (`analisis.prefiltro_datos`) y ya no se vuelve a preguntar por WhatsApp (regla de oro del Cambio 52).

La actividad «Prefiltro web» sale del catálogo; `migrar_cambio50` la quita de plantillas y vacantes (las rutas que la
traían ya la cumplen con la solicitud) y resuelve a los candidatos que estaban detenidos en ella: sus respuestas web se
evalúan y la ruta sigue sola. Idempotente y no fatal (marca en bitácora).
"""

import re
import unicodedata
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import Postulacion, registrar

ACTOR = "Red Human (solicitud web)"
CLAVE_EXTRA = "solicitud_web_extra"  # datos base adicionales que la vacante pide en «Unas preguntas» (edad, zona)
EXTRAS_VALIDOS = ("edad", "zona")
MARCA_MIGRACION = "migracion_cambio50_prefiltro_web"


def _n(t) -> str:
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[¿?¡!.,;:()«»\"']", " ", t).split())


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================ textos

def lugar_vacante(v, ubicacion=None) -> str:
    """«Monterrey, Nuevo León». Con una ubicación (terminal elegida) manda la de ESA ubicación. Remoto o con varias
    ubicaciones sin elegir → vacío."""
    if v is None or (getattr(v, "modalidad", "") or "").lower().startswith("remot"):
        return ""
    if ubicacion is not None:
        return ", ".join(x for x in (ubicacion.ubicacion_municipio, ubicacion.ubicacion_estado) if x)
    try:
        from . import ubicaciones as subic

        if len(subic.de_vacante(None, v)) > 1:
            return ""
    except Exception:  # noqa: BLE001
        pass
    return ", ".join(x for x in (getattr(v, "ubicacion_municipio", ""), getattr(v, "ubicacion_estado", "")) if x)


def pregunta_interes(v, ubicacion=None) -> str:
    """Cambio 51: «El puesto es [puesto] en [ciudad, estado]. ¿Te interesa?»."""
    titulo = ((getattr(v, "titulo", "") if v is not None else "") or "esta vacante").strip()
    lugar = lugar_vacante(v, ubicacion)
    return f"El puesto es {titulo}{' en ' + lugar if lugar else ''}. ¿Te interesa?"


def pregunta_sueldo(v) -> str:
    from .preguntas_filtro import _texto_sueldo

    sueldo = (getattr(v, "sueldo", "") or "") if v is not None else ""
    return "" if sueldo.strip() in ("", "A convenir") else _texto_sueldo(sueldo)


PREGUNTA_EDAD = "¿Cuántos años tienes?"
PREGUNTA_ZONA = "¿En qué colonia y municipio vives?"


def extras_de(v) -> List[str]:
    return [x for x in (((getattr(v, "guiones", None) or {}).get(CLAVE_EXTRA)) or []) if x in EXTRAS_VALIDOS]


def _es_interes(q: str) -> bool:
    t = _n(q)
    return "te interesa" in t and "puesto es" in t


def _es_sueldo(q: str) -> bool:
    t = _n(q)
    return "sueldo" in t and ("te interesa" in t or "te parece" in t)


def preguntas_extra(v, actuales: List[dict]) -> List[dict]:
    """Lo que «Unas preguntas» agrega a las preguntas filtro de la vacante: interés + sueldo (+ edad / colonia si la
    vacante los pide). Nunca descartan. Una pregunta de interés ya derivada por la entidad se REESCRIBE al texto con
    ciudad y estado (ver `criterios_web`)."""
    textos = [str(q.get("pregunta") or "") for q in actuales]
    salida = []
    if not any(_es_interes(t) for t in textos):
        salida.append({"id": "base-interes", "pregunta": pregunta_interes(v), "tipo": "si_no", "valida": "Interés en el puesto",
                       "respuesta_esperada": "Sí", "descarta": False, "opciones": ["Sí", "No"], "base": True, "dato": "interes"})
    q_s = pregunta_sueldo(v)
    if q_s and not any(_es_sueldo(t) for t in textos):
        salida.append({"id": "base-sueldo", "pregunta": q_s, "tipo": "si_no", "valida": f"Sueldo: {v.sueldo}",
                       "respuesta_esperada": "Sí", "descarta": False, "opciones": ["Sí", "No"], "base": True, "dato": "sueldo"})
    extras = extras_de(v)
    if "edad" in extras and not any(_n(t) == _n(PREGUNTA_EDAD) for t in textos):
        salida.append({"id": "base-edad", "pregunta": PREGUNTA_EDAD, "tipo": "numero", "valida": "Edad", "respuesta_esperada": "",
                       "descarta": False, "opciones": [], "unidad": "años", "base": True, "dato": "edad"})
    if "zona" in extras and not any(_n(t) == _n(PREGUNTA_ZONA) for t in textos):
        salida.append({"id": "base-zona", "pregunta": PREGUNTA_ZONA, "tipo": "abierta", "valida": "Colonia y municipio",
                       "respuesta_esperada": "", "descarta": False, "opciones": [], "base": True, "dato": "zona"})
    return salida


def criterios_web(v) -> List[dict]:
    """Las preguntas de «Unas preguntas» (sin la de la terminal, que agrega `serial`)."""
    from ..serial import _sin_parcial

    base = [_sin_parcial(dict(q)) for q in (getattr(v, "preguntas_filtro", None) or []) if isinstance(q, dict)]
    for q in base:
        if _es_interes(q.get("pregunta")):
            q["pregunta"] = pregunta_interes(v)
            q["dato"] = "interes"
        elif _es_sueldo(q.get("pregunta")):
            q["dato"] = "sueldo"
    return base + preguntas_extra(v, base)


def textos_web_de(c: dict, v) -> List[str]:
    """Textos con los que pudo llegar la respuesta web de un criterio del chat (para no volver a preguntarlo)."""
    dato = c.get("dato")
    salida = [c.get("pregunta") or "", c.get("chat") or "", c.get("criterio") or ""]
    if dato == "interes":
        salida += [pregunta_interes(v), f"El puesto es {getattr(v, 'titulo', '')}. ¿Te interesa?"]
    elif dato == "sueldo" and not c.get("expectativa"):
        salida.append(pregunta_sueldo(v))
    elif dato == "edad":
        salida.append(PREGUNTA_EDAD)
    elif dato == "zona":
        salida.append(PREGUNTA_ZONA)
    return [s for s in salida if s]


def respuesta_web(p: Postulacion, c: dict) -> Optional[str]:
    web = {_n(r.get("pregunta")): str(r.get("respuesta") or "").strip()
           for r in (p.analisis or {}).get("respuestas_web") or [] if isinstance(r, dict)}
    for t in textos_web_de(c, p.vacante):
        if web.get(_n(t)):
            return web[_n(t)]
    return None


_VACIAS = {"tienes", "cuentas", "puedes", "con", "para", "este", "esta", "mayor", "menor", "meses", "anos", "vigencia", "vigente",
           "tipo", "tu", "de", "la", "el", "en", "mas", "que", "una", "uno", "como"}


def _claves(texto: str) -> set:
    return {w for w in _n(texto).split() if len(w) > 3 and w not in _VACIAS}


def contradice_indispensable(p: Postulacion, inconsistencias: List[str]) -> Optional[str]:
    """Fletes «Entrevista en video» (2026-10-15): ¿alguna inconsistencia de la entrevista contradice un INDISPENSABLE que
    el candidato contestó en la solicitud web? Regresa el requisito (o None). Se reconoce por las palabras clave del
    requisito dentro del texto de la inconsistencia («licencia», «apto», «médico», «quinta rueda»…)."""
    if p is None or p.vacante is None or not inconsistencias:
        return None
    respondidas = {_n(r.get("pregunta")) for r in (p.analisis or {}).get("respuestas_web") or [] if isinstance(r, dict)}
    for q in criterios_web(p.vacante):
        if not q.get("descarta") or _n(q.get("pregunta")) not in respondidas:
            continue
        claves = _claves(q.get("valida") or "") or _claves(q.get("pregunta") or "")
        if not claves:
            continue
        for inc in inconsistencias:
            presentes = claves & _claves(inc)
            if len(presentes) >= min(2, len(claves)):
                return q.get("valida") or q["pregunta"]
    return None


# ============================================================ evaluación al enviar

def _criterio_unificado(v, q: dict) -> Optional[dict]:
    from . import preguntas_filtro as pfu

    if not pfu.aplica(v):
        return None
    for c in pfu.criterios(v):
        if _n(c.get("pregunta")) == _n(q.get("pregunta")) or _n(c.get("pregunta_web")) == _n(q.get("pregunta")):
            return c
    return None


def evaluar(p: Postulacion) -> Tuple[str, str, List[dict]]:
    """(resultado, motivo, criterios). resultado ∈ cumple | no_cumple. Solo descarta una respuesta CLARA a una pregunta
    con «Descarta si» (un «No» a un Sí/No, un número o una opción que no alcanza). Lo vacío o dudoso no descarta: si la
    ruta tiene prefiltro por WhatsApp, el bot lo pregunta; si no, queda en Puntos por validar."""
    v = p.vacante
    respuestas = {_n(r.get("pregunta")): str(r.get("respuesta") or "").strip()
                  for r in (p.analisis or {}).get("respuestas_web") or [] if isinstance(r, dict)}
    criterios, incumple = [], []
    for q in criterios_web(v) if v is not None else []:
        respuesta = respuestas.get(_n(q.get("pregunta")))
        veredicto = "sin_respuesta" if not respuesta else "registrado"
        if respuesta and q.get("descarta"):
            c = _criterio_unificado(v, q)
            if c is not None and not (c.get("pregunta_web") and _n(c.get("pregunta_web")) == _n(q.get("pregunta"))):
                from .preguntas_filtro import valor_web

                valor, _extra = valor_web(c, respuesta)
            else:
                esperada = _n(q.get("respuesta_esperada") or ("Sí" if (q.get("tipo") or "si_no") == "si_no" else ""))
                r = _n(respuesta)
                if esperada and (r == esperada or (esperada == "si" and r.startswith("si"))):
                    valor = "si"
                elif (q.get("tipo") or "si_no") == "si_no" and r.split(" ")[0] in ("si", "no"):
                    valor = "no"  # contestó lo contrario de lo esperado
                elif q.get("tipo") == "opcion" and esperada:
                    valor = "no"
                else:
                    valor = None
            veredicto = {"si": "cumple", "no": "no_cumple"}.get(valor, "no_concluyente")
            if valor == "no":
                incumple.append((q.get("valida") or q["pregunta"], respuesta))
        criterios.append({"criterio": q.get("valida") or q["pregunta"], "pregunta": q["pregunta"], "respuesta": respuesta or "",
                          "excluyente": bool(q.get("descarta")), "veredicto": veredicto})
    if incumple:
        motivo = "No cumple el requisito indispensable: " + ", ".join(f"{c} (respondió: «{r[:80]}»)" for c, r in incumple)
        return "no_cumple", motivo, criterios
    return "cumple", "Respondió las preguntas de la solicitud", criterios


def registrar_respuestas(p: Postulacion) -> None:
    """Lo que no descarta (interés, sueldo, edad, colonia, números) queda en `analisis.prefiltro_datos` como si lo
    hubiera dicho en el chat (misma función que el bot)."""
    from . import preguntas_filtro as pfu

    v = p.vacante
    if v is None:
        return
    for q in criterios_web(v):
        respuesta = respuesta_web(p, {"pregunta": q["pregunta"]})
        if not respuesta:
            continue
        c = _criterio_unificado(v, q)
        if c is None and q.get("dato"):
            c = {"id": q["id"], "dato": q["dato"], "tipo": q.get("tipo") or "si_no", "criterio": q.get("valida") or q["pregunta"],
                 "pregunta": q["pregunta"], "chat": q["pregunta"], "indispensable": False}
        if c is None:
            continue
        valor, extra = pfu.valor_web(c, respuesta)
        if valor is not None:
            pfu.registrar_datos(p, c, respuesta, extra)
        if valor == "no" and not q.get("descarta") and q.get("dato") not in ("interes", "sueldo") and (q.get("tipo") or "si_no") == "si_no":
            # un requisito que NO descarta contestado con «No» (p. ej. «¿Puedes rolar turnos?») → Puntos por validar
            from .prefiltro_conversacional import punto_por_validar

            punto_por_validar(p, f"Solicitud web: respondió «{respuesta}» a «{q['pregunta']}»")


def descartar(db: Session, p: Postulacion, motivo: str, criterios: List[dict]) -> str:
    """Cierra la postulación como «Descartado» (el MISMO cierre del prefiltro). No hace commit. Regresa el mensaje
    GENÉRICO para el candidato (sin motivo)."""
    from . import prefiltro_conversacional as pconv

    sello = _ahora()
    a = dict(p.analisis or {})
    a["prefiltro_web"] = {"resultado": "no_cumple", "motivo": motivo, "criterios": criterios, "en": sello,
                          "decidido_por": "Red Human", "origen": "solicitud_web"}
    a["solicitud_web"] = {"resultado": "no_cumple", "motivo": motivo, "en": sello}
    a["prefiltro_resultado"] = "no_cumple"
    p.analisis = a
    flag_modified(p, "analisis")
    p.estado = "no_cumple"
    p.prefiltro_completo = True
    p.historial = list(p.historial or []) + [{
        "evento": "descartado", "usuario": ACTOR, "fecha": sello, "motivo": motivo, "origen": "solicitud_web",
        "texto": f"Descartado por Red Human al enviar la solicitud web — {motivo}. RH puede reactivarlo desde «…» → «Reactivar».",
    }]
    p.cerrar(pconv.MOTIVO_CIERRE)
    registrar(db, ACTOR, "postulacion_descartada_solicitud_web", "postulacion", p.codigo, {"motivo": motivo})
    return pconv.texto_descarte(pconv.empresa_de(p))


async def al_enviar(db: Session, p: Postulacion) -> Optional[dict]:
    """En `/candidatos/postular`, ya guardada la postulación: registra las respuestas y, si una coincide con «Descarta
    si», descarta y avisa (mensaje genérico). Regresa {"descartado": True, "mensaje": …} o None. Hace commit."""
    if not (p.analisis or {}).get("respuestas_web"):
        return None
    resultado, motivo, criterios = evaluar(p)
    a = dict(p.analisis or {})
    a["solicitud_web"] = {"resultado": resultado, "motivo": motivo, "en": _ahora(), "criterios": criterios}
    if resultado != "no_cumple":
        # 2026-10-15 (UVAQ): reglas de revisión de la vacante (no excluyentes) → la solicitud queda «Requiere revisión»
        from . import filtros_revision

        revision = filtros_revision.web(p)
        if revision:
            a["solicitud_web"]["revision"] = revision
            p.historial = list(p.historial or []) + [{"evento": "requiere_revision", "usuario": ACTOR, "fecha": _ahora(),
                                                      "motivo": "; ".join(revision), "origen": "solicitud_web",
                                                      "texto": "Solicitud web: requiere revisión de RH — " + "; ".join(revision)}]
            registrar(db, ACTOR, "solicitud_web_requiere_revision", "postulacion", p.codigo, {"motivos": revision})
    p.analisis = a
    flag_modified(p, "analisis")
    registrar_respuestas(p)
    if resultado != "no_cumple":
        db.commit()
        return None
    from . import prefiltro_conversacional as pconv

    if pconv.aplica(p):
        # la ruta trae prefiltro por WhatsApp: el MISMO descarte de siempre (entidad resuelta, así «Reactivar» la retoma)
        db.commit()
        await pconv.desde_web(db, p)
        db.refresh(p)
        if not p.activa:
            return {"descartado": True, "mensaje": pconv.texto_descarte(pconv.empresa_de(p))}
    texto = descartar(db, p, motivo, criterios)
    db.commit()
    await pconv.avisar_descarte(db, p, texto)
    return {"descartado": True, "mensaje": texto}


# ============================================================ Cambio 50: migración

def _sin_prefiltro_web(pasos: list) -> Tuple[list, bool]:
    """Quita «Prefiltro web» de una ruta (re-apunta sus dependencias a la solicitud web; si no hay, la agrega)."""
    from .proceso import normalizar_pasos

    pasos = [dict(x) for x in pasos or [] if isinstance(x, dict)]
    quitar = [x for x in pasos if x.get("tipo") == "prefiltro_web" and not x.get("heredado")]
    if not quitar:
        return pasos, False
    sol = next((x for x in pasos if x.get("tipo") == "solicitud_web"), None)
    if sol is None:
        sol = {"id": "solicitud-web", "tipo": "solicitud_web", "nombre": "Solicitud web sin CV", "etapa": "Prefiltro",
               "obligatorio": True, "con_cv": False}
        pasos.insert(0, sol)
    ids = {x["id"] for x in quitar}
    salida = [x for x in pasos if x["id"] not in ids]
    for x in salida:
        x["depende_de"] = list(dict.fromkeys(sol["id"] if d in ids else d for d in x.get("depende_de") or [] if
                                             (sol["id"] if d in ids else d) != x["id"]))
    return normalizar_pasos(salida), True


async def migrar_cambio50(db: Session, aplicar: bool = True, avanzar: bool = True) -> dict:
    """1) Plantillas y vacantes: «Prefiltro web» sale de la ruta (sube la versión). 2) Postulaciones ACTIVAS detenidas en
    su «Prefiltro web» congelado: sus respuestas web se evalúan (resultado en `analisis.prefiltro_web`); sin respuestas,
    la actividad queda «Omitida» con el motivo del Cambio 50; la ruta sigue sola (`avanzar_seguro`). Una respuesta que
    coincide con «Descarta si» NO descarta en la migración: queda en Puntos por validar (RH decide). Idempotente.
    `avanzar=False` (arranque de la API): no mueve nada en ese momento — el job `motor_ruta` (5 min) sigue la ruta."""
    from ..models import Bitacora, PlantillaProceso, Vacante
    from . import proceso as sproc

    salida = {"plantillas": [], "vacantes": [], "postulaciones": [], "aplicada": aplicar}
    try:
        if aplicar and db.query(Bitacora.id).filter(Bitacora.accion == MARCA_MIGRACION).first():
            return {**salida, "yaAplicada": True}
    except Exception:  # noqa: BLE001
        return salida
    try:
        plantillas = db.query(PlantillaProceso).all() if sproc._tablas_proceso() else []
    except Exception:  # noqa: BLE001
        plantillas = []
    for pl in plantillas:
        try:
            nuevos, cambio = _sin_prefiltro_web(pl.pasos or [])
        except sproc.ErrorProceso:
            continue
        if cambio:
            salida["plantillas"].append(pl.id)
            if aplicar:
                pl.pasos = nuevos
                pl.version = (pl.version or 1) + 1
                pl.actualizada_por = "Migración Cambio 50"
    try:
        vacantes = db.query(Vacante).filter(Vacante.proceso.isnot(None)).all()
    except Exception:  # noqa: BLE001
        db.rollback()
        return salida
    for v in vacantes:
        proc = dict(v.proceso or {})
        try:
            nuevos, cambio = _sin_prefiltro_web(proc.get("pasos") or [])
        except sproc.ErrorProceso:
            continue
        if cambio:
            salida["vacantes"].append(v.codigo)
            if aplicar:
                v.proceso = {**proc, "pasos": nuevos, "version": int(proc.get("version") or 0) + 1, "migrado_cambio50": True}
    detenidas = []
    for p in db.query(Postulacion).filter(Postulacion.activa.is_(True), Postulacion.proceso.isnot(None)).all():
        paso = next((x for x in (p.proceso or {}).get("pasos") or [] if x.get("tipo") == "prefiltro_web" and not x.get("heredado")), None)
        if paso is None:
            continue
        r = next((x for x in sproc.estado_pasos(p) if x["id"] == paso["id"]), None)
        if r is None or r["estado"] in ("completada", "omitida", "cancelada"):
            continue
        detenidas.append(p)
        salida["postulaciones"].append(p.codigo)
        if not aplicar:
            continue
        a = dict(p.analisis or {})
        sello = _ahora()
        if a.get("respuestas_web") and p.vacante is not None:
            resultado, motivo, criterios = evaluar(p)
            if resultado == "no_cumple":
                a["puntos_validar_prefiltro"] = list(a.get("puntos_validar_prefiltro") or []) + [f"Solicitud web: {motivo}"]
            a["prefiltro_web"] = {"resultado": "cumple", "motivo": "Respuestas de la solicitud web (migración Cambio 50)",
                                  "criterios": criterios, "en": sello, "decidido_por": "Red Human", "origen": "migracion_cambio50"}
            p.analisis = a
            flag_modified(p, "analisis")
            registrar_respuestas(p)
            texto = "Prefiltro web resuelto con las respuestas de la solicitud (Cambio 50)"
        else:
            decisiones = dict(p.proceso_estado or {})
            d = dict(decisiones.get(paso["id"]) or {})
            d["omitida"] = {"por": "Migración Cambio 50", "motivo": "El Prefiltro web se integró a la Solicitud web",
                            "fecha": sello, "obligatorio": bool(paso.get("obligatorio")), "autorizado_por": "sistema"}
            decisiones[paso["id"]] = d
            p.proceso_estado = decisiones
            texto = "Prefiltro web omitido: se integró a la Solicitud web (Cambio 50)"
        p.historial = list(p.historial or []) + [{"evento": "migracion_cambio50", "usuario": "sistema", "fecha": sello, "texto": texto}]
        registrar(db, "sistema", "prefiltro_web_migrado", "postulacion", p.codigo, {"texto": texto})
    if aplicar:
        registrar(db, "sistema", MARCA_MIGRACION, "postulacion", "", {k: salida[k] for k in ("plantillas", "vacantes", "postulaciones")})
        db.commit()
        for p in detenidas if avanzar else []:
            try:
                await sproc.avanzar_seguro(db, p)
            except Exception as ex:  # noqa: BLE001
                db.rollback()
                print(f"[cambio50] {p.codigo}: {ex}", flush=True)
    return salida
