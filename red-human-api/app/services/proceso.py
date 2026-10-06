"""Proceso configurable y seguimiento de candidatos (2026-10-06).

El proceso configurado determina la ejecución y el seguimiento de cada candidato, REUTILIZANDO lo que ya existía:
las cinco etapas fijas (`models.ETAPAS_CANDIDATO`), el prefiltro, el Análisis de CV, la Entrevista Red Human,
«Agregar evaluación» (`Evaluacion`), el expediente de Contratación y las tareas de Onboarding. Aquí NO se guarda el
estado de los pasos: se DERIVA en cada lectura de esos registros reales; lo único que se guarda son las decisiones de
RH sobre un paso (omitirlo / cancelarlo, `Postulacion.proceso_estado`).

Niveles y versionado (siempre por COPIA, nunca por referencia):
  PlantillaProceso (Cuenta) → Vacante.proceso (personalizable sin tocar la plantilla) → Postulacion.proceso (congelado
  al crear la postulación). Editar una plantilla o una vacante NUNCA modifica a los candidatos existentes ni cancela
  actividades en curso; «Aplicar versión vigente» es explícito y conserva lo que ya se hizo.

Reglas:
  * Estado (Pendiente / En curso / Completada / Omitida / Cancelada) y resultado (Favorable / Con observaciones /
    No favorable) son independientes: «Completada · No favorable» es válido.
  * Un paso cumple su condición de avance según su regla: ninguna, calificación mínima, dictamen aceptado o
    validación de una persona de RH.
  * Dependencias EXPLÍCITAS: sin dependencias, los pasos de la misma etapa corren en paralelo. Un paso de una etapa
    posterior se habilita al llegar a esa etapa.
  * Una etapa sin pasos obligatorios nunca bloquea; avanzar con un obligatorio sin cumplir exige omitirlo con
    justificación y autorización explícita (`Usuario.puede_autorizar_omisiones`).
  * Plazo vencido = solo alerta. Nunca descarta ni mueve a nadie.
  * Avance automático por etapa (interruptor del proceso): cuando TODOS los obligatorios de la etapa cumplen su regla,
    la postulación pasa a la siguiente etapa con pasos (las vacías no bloquean). Nunca sale de Contratación (a
    Onboarding solo con «Iniciar Onboarding») ni descarta: un «No favorable» deja la decisión a RH.
"""

import copy
import re
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session, object_session

from ..models import (
    CALIFICACION_MINIMA_DEFAULT, DICTAMENES_ACEPTADOS_DEFAULT, DICTAMENES_ACEPTADOS_GENERAL, ENFOQUES_ENTREVISTA,
    ESTADOS_PASO, ETAPAS_CANDIDATO, ETAPAS_SIN_AVANCE_AUTOMATICO, REGLAS_APROBACION, RESPONSABLES_PASO, RESULTADOS_PASO,
    TIPOS_ENTREVISTA_HUMANA, TIPOS_PASO, TIPOS_PASO_EVALUACION, Evaluacion, PlantillaProceso, Postulacion, Usuario,
    conclusiones_de, nombre_etapa, registrar,
)

ACTOR_AUTOMATICO = "Red Human (avance automático)"
CONCLUSION_NEGATIVA = ("no_avanzar", "no_apto", "desfavorable")
CONCLUSION_OBSERVACIONES = ("con_observaciones", "apto_con_restricciones")
MOTIVO_MINIMO = 10  # caracteres de la justificación para omitir/cancelar un paso obligatorio


class ErrorProceso(Exception):
    def __init__(self, status: int, mensaje: str):
        super().__init__(mensaje)
        self.status = status
        self.mensaje = mensaje


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _indice(etapa: str) -> int:
    return ETAPAS_CANDIDATO.index(etapa) if etapa in ETAPAS_CANDIDATO else -1


# ============================================================ configuración (normalizar / validar)

def _id_paso(valor, usados: set) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "-", str(valor or "").strip().lower())[:40].strip("-")
    if not base or base in usados:
        base = f"paso-{secrets.token_hex(3)}"
        while base in usados:
            base = f"paso-{secrets.token_hex(3)}"
    return base


def _regla(crudo, tipo: str) -> dict:
    crudo = crudo if isinstance(crudo, dict) else {"tipo": crudo} if isinstance(crudo, str) else {}
    regla = crudo.get("tipo") or TIPOS_PASO[tipo]["regla"]
    if regla not in REGLAS_APROBACION:
        raise ErrorProceso(400, f"Condición de avance inválida en «{TIPOS_PASO[tipo]['nombre']}». Usa una de: {', '.join(REGLAS_APROBACION)}.")
    salida = {"tipo": regla}
    if regla == "calificacion":
        try:
            minimo = int(crudo.get("minimo", CALIFICACION_MINIMA_DEFAULT))
        except (TypeError, ValueError):
            raise ErrorProceso(400, "La calificación mínima debe ser un número de 0 a 100.")
        if not 0 <= minimo <= 100:
            raise ErrorProceso(400, "La calificación mínima debe ser un número de 0 a 100.")
        salida["minimo"] = minimo
    if regla in ("dictamen", "calificacion") and tipo in TIPOS_PASO_EVALUACION:
        validos = conclusiones_de(tipo)
        aceptados = [a for a in (crudo.get("aceptados") or []) if a in validos] or list(
            DICTAMENES_ACEPTADOS_DEFAULT.get(tipo, DICTAMENES_ACEPTADOS_GENERAL))
        salida["aceptados"] = aceptados
    return salida


def _responsable(crudo, tipo: str) -> dict:
    crudo = crudo if isinstance(crudo, dict) else {"tipo": crudo} if isinstance(crudo, str) else {}
    rtipo = crudo.get("tipo") or TIPOS_PASO[tipo]["responsable"]
    if rtipo not in RESPONSABLES_PASO:
        raise ErrorProceso(400, f"Responsable inválido. Usa uno de: {', '.join(RESPONSABLES_PASO)}.")
    salida = {"tipo": rtipo, "nombre": str(crudo.get("nombre") or "").strip()[:150]}
    if rtipo == "usuario":
        uid = crudo.get("usuario_id", crudo.get("usuarioId"))
        salida["usuario_id"] = int(uid) if str(uid or "").isdigit() else None
    return salida


def normalizar_pasos(pasos: Iterable[dict]) -> List[dict]:
    """Valida y completa los pasos (tipo, etapa permitida, dependencias que existen, sin ciclos ni hacia una etapa
    posterior, responsable, regla, plazo). Regresa la lista en orden de etapa conservando el orden capturado."""
    salida: List[dict] = []
    usados: set = set()
    for i, crudo in enumerate(pasos or []):
        if not isinstance(crudo, dict):
            raise ErrorProceso(400, "Cada paso debe ser un objeto.")
        tipo = str(crudo.get("tipo") or "").strip()
        if tipo not in TIPOS_PASO:
            raise ErrorProceso(400, f"Tipo de paso inválido: «{tipo}». Usa uno de: {', '.join(TIPOS_PASO)}.")
        defs = TIPOS_PASO[tipo]
        etapa = crudo.get("etapa") or defs["etapa"]
        if etapa not in defs["etapas"]:
            raise ErrorProceso(400, f"«{defs['nombre']}» no puede ir en {nombre_etapa(etapa)}. Etapas permitidas: "
                                    f"{', '.join(nombre_etapa(e) for e in defs['etapas'])}.")
        pid = _id_paso(crudo.get("id"), usados)
        usados.add(pid)
        plazo = crudo.get("plazo_dias", crudo.get("plazoDias"))
        if plazo in ("", None):
            plazo = None
        else:
            try:
                plazo = int(plazo)
            except (TypeError, ValueError):
                raise ErrorProceso(400, f"El plazo de «{defs['nombre']}» debe ser un número de días.")
            if plazo < 0 or plazo > 365:
                raise ErrorProceso(400, f"El plazo de «{defs['nombre']}» debe estar entre 0 y 365 días.")
        paso = {
            "id": pid,
            "tipo": tipo,
            "nombre": str(crudo.get("nombre") or "").strip()[:200] or defs["nombre"],
            "etapa": etapa,
            "obligatorio": bool(crudo.get("obligatorio", True)),
            "depende_de": [str(d) for d in (crudo.get("depende_de", crudo.get("dependeDe")) or []) if str(d).strip()],
            "responsable": _responsable(crudo.get("responsable"), tipo),
            "regla": _regla(crudo.get("regla"), tipo),
            "plazo_dias": plazo,
            "orden": i,
        }
        if tipo == "entrevista_humana":
            t = crudo.get("tipo_entrevista", crudo.get("tipoEntrevista")) or "general"
            if t not in TIPOS_ENTREVISTA_HUMANA:
                raise ErrorProceso(400, f"Tipo de entrevista humana inválido. Usa uno de: {', '.join(TIPOS_ENTREVISTA_HUMANA)}.")
            paso["tipo_entrevista"] = t
        elif tipo == "entrevista_agente":
            t = crudo.get("tipo_entrevista", crudo.get("tipoEntrevista")) or "profesional"
            if t not in ENFOQUES_ENTREVISTA:
                raise ErrorProceso(400, f"Enfoque de la Entrevista Red Human inválido. Usa uno de: {', '.join(ENFOQUES_ENTREVISTA)}.")
            paso["tipo_entrevista"] = t
        salida.append(paso)

    por_id = {p["id"]: p for p in salida}
    for p in salida:
        for d in p["depende_de"]:
            if d == p["id"]:
                raise ErrorProceso(400, f"«{p['nombre']}» no puede depender de sí mismo.")
            if d not in por_id:
                raise ErrorProceso(400, f"«{p['nombre']}» depende de un paso que no existe ({d}).")
            if _indice(por_id[d]["etapa"]) > _indice(p["etapa"]):
                raise ErrorProceso(400, f"«{p['nombre']}» no puede depender de «{por_id[d]['nombre']}», que va en una etapa posterior.")
        p["depende_de"] = list(dict.fromkeys(p["depende_de"]))
    # ciclos (DFS)
    estado: Dict[str, int] = {}

    def visitar(pid: str, cadena: List[str]) -> None:
        if estado.get(pid) == 2:
            return
        if estado.get(pid) == 1:
            raise ErrorProceso(400, "Las dependencias forman un ciclo: " + " → ".join(por_id[x]["nombre"] for x in cadena + [pid]) + ".")
        estado[pid] = 1
        for d in por_id[pid]["depende_de"]:
            visitar(d, cadena + [pid])
        estado[pid] = 2

    for pid in por_id:
        visitar(pid, [])
    salida.sort(key=lambda p: (_indice(p["etapa"]), p["orden"]))
    for i, p in enumerate(salida):
        p["orden"] = i
    return salida


def normalizar_etapas(etapas) -> dict:
    etapas = etapas if isinstance(etapas, dict) else {}
    salida = {}
    for e in ETAPAS_CANDIDATO:
        cfg = etapas.get(e) or {}
        auto = bool(cfg.get("avance_automatico", cfg.get("avanceAutomatico", False))) if isinstance(cfg, dict) else False
        salida[e] = {"avance_automatico": auto and e not in ETAPAS_SIN_AVANCE_AUTOMATICO}
    return salida


def tiene_proceso(obj) -> bool:
    return bool((getattr(obj, "proceso", None) or {}).get("pasos"))


def config_de(obj) -> dict:
    return getattr(obj, "proceso", None) or {}


# ============================================================ ejemplos (los tres escenarios de validación)

PROCESOS_EJEMPLO = {
    "operativo_minimo": {
        "nombre": "Operativo mínimo",
        "descripcion": "Prefiltro por WhatsApp → documentos → contratación. Las etapas sin pasos no bloquean.",
        "pasos": [
            {"id": "prefiltro", "tipo": "prefiltro_whatsapp", "obligatorio": True, "plazo_dias": 2},
            {"id": "documentos", "tipo": "documentos", "obligatorio": True, "plazo_dias": 5},
            {"id": "condiciones", "tipo": "condiciones", "obligatorio": True, "depende_de": ["documentos"]},
        ],
        "etapas": {"Prefiltro": {"avance_automatico": True}},
    },
    "administrativo_completo": {
        "nombre": "Administrativo completo",
        "descripcion": "Prefiltro web → Entrevista Red Human → psicometría → entrevista humana → referencias → contratación → onboarding.",
        "pasos": [
            {"id": "prefiltro_web", "tipo": "prefiltro_web", "obligatorio": True},
            {"id": "entrevista_agente", "tipo": "entrevista_agente", "obligatorio": True, "regla": {"tipo": "calificacion", "minimo": 70}},
            {"id": "psicometria", "tipo": "psicometrica", "obligatorio": True, "plazo_dias": 5},
            {"id": "entrevista_humana", "tipo": "entrevista_humana", "obligatorio": True, "depende_de": ["psicometria"],
             "tipo_entrevista": "jefe_directo", "plazo_dias": 7},
            {"id": "referencias", "tipo": "referencias", "obligatorio": True, "depende_de": ["entrevista_humana"],
             "regla": {"tipo": "validacion"}},
            {"id": "condiciones", "tipo": "condiciones", "obligatorio": True},
            {"id": "onboarding", "tipo": "onboarding", "obligatorio": True},
            {"id": "alta", "tipo": "alta", "obligatorio": True, "depende_de": ["onboarding"]},
        ],
        "etapas": {"Entrevista IA": {"avance_automatico": False}},
    },
    "paralelo_medica_socioeconomica": {
        "nombre": "Médica y socioeconómica en paralelo",
        "descripcion": "Evaluación médica y socioeconómica disponibles al mismo tiempo y obligatorias antes de Contratación.",
        "pasos": [
            {"id": "prefiltro", "tipo": "prefiltro_whatsapp", "obligatorio": True},
            {"id": "entrevista_humana", "tipo": "entrevista_humana", "obligatorio": True, "tipo_entrevista": "general"},
            {"id": "medica", "tipo": "medica", "obligatorio": True, "plazo_dias": 5},
            {"id": "socioeconomica", "tipo": "socioeconomica", "obligatorio": True, "plazo_dias": 5},
            {"id": "condiciones", "tipo": "condiciones", "obligatorio": True},
        ],
        "etapas": {},
    },
}


def ejemplo(clave: str) -> dict:
    if clave not in PROCESOS_EJEMPLO:
        raise ErrorProceso(404, "Ejemplo de proceso no encontrado.")
    e = copy.deepcopy(PROCESOS_EJEMPLO[clave])
    return {"nombre": e["nombre"], "descripcion": e["descripcion"], "pasos": normalizar_pasos(e["pasos"]),
            "etapas": normalizar_etapas(e["etapas"])}


# ============================================================ plantillas → vacante → postulación (copias)

def plantilla_dict(pl: PlantillaProceso) -> dict:
    return {
        "id": pl.id, "nombre": pl.nombre, "descripcion": pl.descripcion or "", "pasos": pl.pasos or [],
        "etapas": normalizar_etapas(pl.etapas), "version": pl.version or 1, "predeterminada": bool(pl.predeterminada),
        "activa": bool(pl.activa), "creadoPor": pl.creado_por, "actualizadaPor": pl.actualizada_por or pl.creado_por,
        "actualizadaEn": pl.actualizada_en.isoformat() if pl.actualizada_en else None,
    }


def proceso_desde_plantilla(pl: PlantillaProceso) -> dict:
    return {
        "plantilla_id": pl.id, "plantilla_nombre": pl.nombre, "plantilla_version": pl.version or 1, "version": 1,
        "personalizado": False, "pasos": copy.deepcopy(pl.pasos or []), "etapas": normalizar_etapas(pl.etapas),
    }


def predeterminada(db: Session, cuenta_id: int) -> Optional[PlantillaProceso]:
    try:
        return (db.query(PlantillaProceso)
                .filter(PlantillaProceso.cuenta_id == cuenta_id, PlantillaProceso.activa.is_(True), PlantillaProceso.predeterminada.is_(True))
                .first())
    except Exception:  # noqa: BLE001 — tabla del paso no fatal ausente: sin proceso
        return None


def proceso_para_vacante(db: Session, cuenta_id: int, actual: Optional[dict], datos: Optional[dict]) -> dict:
    """Arma el `Vacante.proceso` a partir de lo que manda el formulario de vacante:
    {plantilla_id} copia la plantilla; {pasos, etapas} personaliza (sin tocar la plantilla); {quitar: true} deja la
    vacante sin proceso. Cada cambio sube `version` (las postulaciones existentes conservan la suya)."""
    actual = actual or {}
    if not datos:
        return actual
    if datos.get("quitar"):
        return {}
    nuevo: dict
    pid = datos.get("plantilla_id", datos.get("plantillaId"))
    base = dict(actual)
    if pid and pid != actual.get("plantilla_id"):
        pl = db.query(PlantillaProceso).filter(PlantillaProceso.id == int(pid), PlantillaProceso.cuenta_id == cuenta_id,
                                               PlantillaProceso.activa.is_(True)).first()
        if not pl:
            raise ErrorProceso(404, "Plantilla de proceso no encontrada en esta Cuenta.")
        base = proceso_desde_plantilla(pl)
    nuevo = dict(base)
    if "pasos" in datos and datos["pasos"] is not None:
        pasos = normalizar_pasos(datos["pasos"])
        if pasos != base.get("pasos"):
            nuevo["personalizado"] = bool(base.get("plantilla_id")) or bool(actual.get("personalizado"))
        nuevo["pasos"] = pasos
    if "etapas" in datos and datos["etapas"] is not None:
        etapas = normalizar_etapas(datos["etapas"])
        if etapas != normalizar_etapas(base.get("etapas")):
            nuevo["personalizado"] = bool(base.get("plantilla_id")) or bool(actual.get("personalizado"))
        nuevo["etapas"] = etapas
    if not nuevo.get("pasos"):
        return {}
    nuevo["etapas"] = normalizar_etapas(nuevo.get("etapas"))
    if _firma(nuevo) == _firma(actual):
        return actual  # nada cambió: no sube versión
    nuevo["version"] = int(actual.get("version") or 0) + 1
    nuevo["actualizado_en"] = _ahora().isoformat()
    return nuevo


def _firma(proc: dict) -> tuple:
    proc = proc or {}
    return (proc.get("plantilla_id"), repr(proc.get("pasos") or []), repr(normalizar_etapas(proc.get("etapas"))))


def congelar(p: Postulacion, v=None) -> bool:
    """Copia el proceso VIGENTE de la vacante a la postulación (al nacer o al elegir vacante por WhatsApp). Nunca
    reemplaza uno ya congelado. Regresa True si congeló."""
    v = v if v is not None else p.vacante
    if tiene_proceso(p) or v is None or not tiene_proceso(v):
        return False
    proc = copy.deepcopy(v.proceso)
    proc["vacante_version"] = int(proc.get("version") or 1)
    proc["vacante_id"] = v.id
    proc["congelado_en"] = _ahora().isoformat()
    p.proceso = proc
    return True


def desactualizado(p: Postulacion) -> bool:
    v = p.vacante
    if not tiene_proceso(p):
        return bool(v is not None and tiene_proceso(v))
    if v is None or not tiene_proceso(v):
        return False
    if p.proceso.get("vacante_id") not in (None, v.id):
        return True  # RH reasignó la postulación a otra vacante: su proceso sigue siendo el de la anterior
    return int(v.proceso.get("version") or 1) != int(p.proceso.get("vacante_version") or 1)


# ============================================================ ejecución: estado derivado de cada paso

def _evaluaciones_de(p: Postulacion, evaluaciones) -> List[Evaluacion]:
    if evaluaciones is not None:
        return sorted(evaluaciones, key=lambda e: e.id or 0)
    db = object_session(p)
    if db is None:
        return []
    try:
        return db.query(Evaluacion).filter(Evaluacion.postulacion_id == p.id).order_by(Evaluacion.id).all()
    except Exception:  # noqa: BLE001
        return []


def asignar_evaluaciones(pasos: List[dict], evaluaciones: List[Evaluacion]) -> Dict[str, Optional[Evaluacion]]:
    """Qué evaluación cumple cada paso de evaluación: la que trae `paso_id`; si no, la primera VIVA del mismo tipo
    agregada fuera del proceso y no usada por otro paso. Si solo hay canceladas, la última cancelada (para mostrarla)."""
    usados: set = set()
    salida: Dict[str, Optional[Evaluacion]] = {}
    ids = {p["id"] for p in pasos}
    for paso in pasos:
        if paso["tipo"] not in TIPOS_PASO_EVALUACION:
            continue
        propias = [e for e in evaluaciones if e.paso_id == paso["id"]]
        vivas = [e for e in propias if e.estado != "cancelada"]
        elegida = vivas[-1] if vivas else None
        if elegida is None:
            libres = [e for e in evaluaciones if e.tipo == paso["tipo"] and e.estado != "cancelada" and e.id not in usados
                      and (not e.paso_id or e.paso_id not in ids)]
            elegida = libres[0] if libres else None
        if elegida is None and propias:
            elegida = propias[-1]
        if elegida is not None:
            usados.add(elegida.id)
        salida[paso["id"]] = elegida
    return salida


def _texto_falta(nombres: List[str]) -> str:
    def suave(t: str) -> str:
        return t[0].lower() + t[1:] if len(t) > 1 and t[1].islower() else t
    return "Falta " + ", ".join(suave(n) for n in nombres)


def _resultado_conclusion(conclusion: str) -> Optional[str]:
    if not conclusion or conclusion == "requiere_otra_entrevista":
        return None
    if conclusion in CONCLUSION_NEGATIVA:
        return "no_favorable"
    if conclusion in CONCLUSION_OBSERVACIONES:
        return "con_observaciones"
    return "favorable"


def _paso_evaluacion(paso: dict, ev: Optional[Evaluacion]) -> dict:
    regla = paso["regla"]
    if ev is None:
        return {"estado": "pendiente", "resultado": None, "cumple": False, "espera": "", "detalle": "Sin agregar",
                "revisadoPor": "Pendiente de revisión", "evaluacion": None, "terminado_en": None}
    if ev.estado == "cancelada":
        return {"estado": "pendiente", "resultado": None, "cumple": False, "espera": "",
                "detalle": f"{ev.codigo} se canceló{': ' + ev.motivo_estado if ev.motivo_estado else ''}; agrega otra.",
                "revisadoPor": "Pendiente de revisión", "evaluacion": ev.codigo, "terminado_en": None}
    conclusion = ev.conclusion_vigente
    resultado = _resultado_conclusion(conclusion) if ev.estado == "con_resultado" else None
    legible = conclusiones_de(ev.tipo).get(conclusion, "")
    espera = ""
    if ev.consentimiento == "pendiente":
        espera = "Falta el consentimiento médico del candidato"
    elif ev.consentimiento == "rechazado":
        espera = "El candidato rechazó el consentimiento médico"
    elif ev.estado == "no_realizada":
        espera = "No se realizó: falta reprogramar"
    elif ev.estado in ("pendiente", "realizada_sin_resultado"):
        quien = ev.evaluador_nombre or ev.proveedor or ""
        espera = f"Falta el resultado{' de ' + quien if quien else ''}"
    elif conclusion == "requiere_otra_entrevista":
        espera = "Requiere otra entrevista"
    completada = ev.estado == "con_resultado" and conclusion != "requiere_otra_entrevista"
    cumple = False
    if completada:
        if regla["tipo"] == "ninguna":
            cumple = True
        elif regla["tipo"] == "validacion":
            cumple = bool(ev.revisada_en) and conclusion not in CONCLUSION_NEGATIVA
            if not ev.revisada_en:
                espera = "Falta la revisión de RH"
        else:  # dictamen / calificación
            cumple = conclusion in (regla.get("aceptados") or [])
            if not conclusion:
                espera = "Falta el dictamen (RH lo registra al revisar)"
    revisado = f"Revisado por: {ev.revisada_por}" if ev.revisada_en and ev.revisada_por else (
        f"Resultado de: {ev.realizada_por}" if completada and ev.realizada_por else "Pendiente de revisión")
    return {
        "estado": "completada" if completada else "en_curso", "resultado": resultado, "cumple": cumple, "espera": espera,
        "detalle": legible or ("Resultado recibido" if ev.estado == "con_resultado" else ""),
        "revisadoPor": revisado, "evaluacion": ev.codigo,
        "terminado_en": _aware(ev.revisada_en or ev.registrada_en) if completada else None,
        "responsable": ev.evaluador_nombre or "",
    }


def _paso_red_human(paso: dict, p: Postulacion) -> dict:
    """Prefiltro (WhatsApp / web), Análisis de CV y Entrevista Red Human: los resuelve Red Human."""
    tipo, regla = paso["tipo"], paso["regla"]
    a = p.analisis or {}
    base = {"estado": "pendiente", "resultado": None, "cumple": False, "espera": "", "detalle": "",
            "revisadoPor": "Pendiente de revisión", "evaluacion": None, "terminado_en": None}
    if tipo in ("prefiltro_whatsapp", "prefiltro_web"):
        web = bool(a.get("respuestas_web"))
        hecho = bool(p.prefiltro_completo) if tipo == "prefiltro_whatsapp" else (web or (p.prefiltro_completo and p.origen != "whatsapp"))
        if not hecho:
            iniciado = tipo == "prefiltro_whatsapp" and bool(a.get("respuestas_prefiltro"))
            return {**base, "estado": "en_curso" if iniciado else "pendiente",
                    "espera": "Falta que el candidato termine el prefiltro" if iniciado else "Falta que el candidato responda el prefiltro"}
        resultado = {"cumple": "favorable", "revision": "con_observaciones", "no_cumple": "no_favorable"}.get(p.estado)
        cumple = p.estado == "cumple" if regla["tipo"] in ("validacion", "dictamen", "calificacion") else True
        espera = "Falta la revisión de RH (contradicción o caso dudoso)" if p.estado == "revision" else ""
        return {**base, "estado": "completada", "resultado": resultado, "cumple": cumple, "espera": espera,
                "detalle": {"cumple": "Cumple", "revision": "En revisión", "no_cumple": "No cumple"}.get(p.estado, "Completado"),
                "revisadoPor": "Revisado por: Red Human"}
    if tipo == "analisis_cv":
        hay_cv = bool(a.get("requisitos_cumplidos") or a.get("brechas") or a.get("fortalezas_cv"))
        if not hay_cv or not p.score:
            return {**base, "espera": "Falta el CV del candidato"}
        return _con_score(base, regla, int(p.score))
    # entrevista_agente
    entrevistas = list(p.entrevistas or [])
    evaluada = next((e for e in reversed(entrevistas) if e.estado == "evaluada" and e.evaluacion), None)
    if evaluada is not None and (evaluada.evaluacion or {}).get("match_perfil") is not None:
        r = _con_score(base, regla, int(evaluada.evaluacion["match_perfil"]))
        r["terminado_en"] = _aware(evaluada.finalizada_en)
        return r
    ultima = entrevistas[-1] if entrevistas else None
    if ultima is not None:
        espera = {"interrumpida": "Se interrumpió: falta reanudar o reintentar", "parcial": "Quedó parcial: falta reintentar",
                  "programada": "Falta que el candidato realice la entrevista", "en_curso": "En curso con el candidato",
                  "completada": "Red Human está evaluando"}.get(ultima.estado, "")
        return {**base, "estado": "en_curso", "espera": espera}
    if p.videollamada_agendada_en:
        return {**base, "estado": "en_curso", "espera": "Videollamada agendada con el candidato"}
    return {**base, "espera": "Falta agendar la entrevista con el candidato"}


def _con_score(base: dict, regla: dict, score: int) -> dict:
    minimo = regla.get("minimo", CALIFICACION_MINIMA_DEFAULT) if regla["tipo"] == "calificacion" else None
    cumple = score >= minimo if minimo is not None else True
    resultado = "favorable" if cumple else "no_favorable"
    return {**base, "estado": "completada", "resultado": resultado, "cumple": cumple,
            "detalle": f"{score}/100" + (f" · mínimo {minimo}" if minimo is not None else ""), "score": score,
            "revisadoPor": "Revisado por: Red Human"}


def _paso_contratacion(paso: dict, p: Postulacion, tareas: Optional[list]) -> dict:
    tipo = paso["tipo"]
    exp = p.expediente
    base = {"estado": "pendiente", "resultado": None, "cumple": False, "espera": "", "detalle": "",
            "revisadoPor": "Pendiente de revisión", "evaluacion": None, "terminado_en": None}
    if tipo == "documentos":
        if exp is None:
            return {**base, "espera": "El expediente se abre al llegar a Contratación"}
        faltan = exp.no_aprobados
        if not exp.obligatorios:
            return {**base, "estado": "completada", "resultado": "favorable", "cumple": True, "detalle": "Sin documentos obligatorios",
                    "revisadoPor": "Revisado por: RH"}
        if faltan:
            recibidos = sum(1 for d in exp.obligatorios if d.entregado)
            return {**base, "estado": "en_curso" if recibidos else "pendiente", "espera": _texto_falta(faltan),
                    "detalle": f"{exp.progreso}% aprobado"}
        revisores = sorted({d.revisado_por for d in exp.obligatorios if d.revisado_por})
        return {**base, "estado": "completada", "resultado": "favorable", "cumple": True, "detalle": "100% aprobado",
                "revisadoPor": f"Revisado por: {', '.join(revisores)}" if revisores else "Revisado por: RH"}
    if tipo == "condiciones":
        if exp is None:
            return {**base, "espera": "El expediente se abre al llegar a Contratación"}
        if exp.condiciones_guardadas_en and exp.puesto and exp.sueldo and exp.tipo_contratacion and exp.fecha_ingreso:
            return {**base, "estado": "completada", "resultado": "favorable", "cumple": True, "detalle": "Condiciones guardadas",
                    "revisadoPor": "Revisado por: RH", "terminado_en": _aware(exp.condiciones_guardadas_en)}
        return {**base, "espera": "Falta capturar las condiciones (puesto, sueldo, tipo y fecha de ingreso)"}
    if tipo == "alta":
        if exp is not None and exp.estado == "alta":
            return {**base, "estado": "completada", "resultado": "favorable", "cumple": True,
                    "detalle": "Colaborador dado de alta", "revisadoPor": f"Revisado por: {exp.alta_autorizada_por or 'RH'}",
                    "terminado_en": _aware(exp.alta_fecha)}
        return {**base, "espera": "Falta dar de alta como colaborador"}
    # onboarding
    if exp is None or p.etapa != "Onboarding" and not tareas:
        return {**base, "espera": "Se habilita con «Iniciar Onboarding»"}
    vigentes = [t for t in (tareas or []) if t.estado != "cancelada"]
    if not vigentes:
        return {**base, "espera": "Faltan generar las tareas de Onboarding"}
    pendientes = [t.nombre for t in vigentes if t.estado != "realizada"]
    if pendientes:
        return {**base, "estado": "en_curso", "espera": _texto_falta(pendientes), "detalle": f"{len(vigentes) - len(pendientes)}/{len(vigentes)} tareas"}
    return {**base, "estado": "completada", "resultado": "favorable", "cumple": True, "detalle": f"{len(vigentes)}/{len(vigentes)} tareas",
            "revisadoPor": "Revisado por: RH"}


def _tareas(p: Postulacion) -> list:
    exp = p.expediente
    db = object_session(p)
    if exp is None or db is None:
        return []
    try:
        from ..models import TareaOnboarding

        return db.query(TareaOnboarding).filter(TareaOnboarding.expediente_id == exp.id).all()
    except Exception:  # noqa: BLE001
        return []


def _texto_responsable(paso: dict, p: Postulacion, usuarios: Dict[int, str]) -> str:
    r = paso.get("responsable") or {}
    t = r.get("tipo")
    if t == "red_human":
        return "Red Human"
    if t == "candidato":
        return "Candidato"
    if t == "usuario":
        return usuarios.get(r.get("usuario_id") or 0) or r.get("nombre") or "Usuario por asignar"
    if t == "externo":
        return r.get("nombre") or "Externo"
    v = p.vacante
    return (v.responsable.nombre if v is not None and v.responsable else "") or "RH"


def estado_pasos(p: Postulacion, evaluaciones=None, solo_evaluables: bool = False) -> List[dict]:
    """Estado DERIVADO de cada paso del proceso congelado de la postulación (lista vacía si no tiene proceso).
    `solo_evaluables`: solo pasos que alimentan la evaluación integral (sin consultar expediente/tareas)."""
    if not tiene_proceso(p):
        return []
    pasos = p.proceso["pasos"]
    if solo_evaluables:
        pasos = [x for x in pasos if x["tipo"] in TIPOS_PASO_EVALUACION or x["tipo"] in ("analisis_cv", "entrevista_agente")]
    evs = _evaluaciones_de(p, evaluaciones)
    por_paso = asignar_evaluaciones(pasos, evs)
    tareas = None if solo_evaluables else _tareas(p)
    decisiones = p.proceso_estado or {}
    usuarios: Dict[int, str] = {}
    db = object_session(p)
    ids_u = {(x.get("responsable") or {}).get("usuario_id") for x in pasos} - {None}
    if ids_u and db is not None and not solo_evaluables:  # el Kanban (solo evaluables) no muestra responsables: sin N+1
        usuarios = {u.id: u.nombre for u in db.query(Usuario).filter(Usuario.id.in_(ids_u)).all()}

    actual = _indice(p.etapa)
    calculados: Dict[str, dict] = {}
    for paso in pasos:
        tipo = paso["tipo"]
        if tipo in TIPOS_PASO_EVALUACION:
            r = _paso_evaluacion(paso, por_paso.get(paso["id"]))
        elif tipo in ("prefiltro_whatsapp", "prefiltro_web", "analisis_cv", "entrevista_agente"):
            r = _paso_red_human(paso, p)
        else:
            r = _paso_contratacion(paso, p, tareas)
        decision = decisiones.get(paso["id"]) or {}
        if r["estado"] != "completada":
            for clave in ("omitida", "cancelada"):
                if decision.get(clave):
                    d = decision[clave]
                    r = {**r, "estado": clave, "cumple": True, "espera": "",
                         "detalle": f"{ESTADOS_PASO[clave]} por {d.get('por', '')}: {d.get('motivo', '')}".strip(": "),
                         "decision": d}
                    break
        r["responsableTexto"] = r.pop("responsable", "") or _texto_responsable(paso, p, usuarios)
        calculados[paso["id"]] = r

    salida = []
    for paso in pasos:
        r = calculados[paso["id"]]
        deps = [calculados[d] for d in paso.get("depende_de", []) if d in calculados]
        deps_listas = all(d["estado"] in ("completada", "omitida", "cancelada") for d in deps)
        faltan_deps = [x["nombre"] for x in pasos if x["id"] in paso.get("depende_de", [])
                       and calculados[x["id"]]["estado"] not in ("completada", "omitida", "cancelada")]
        etapa_alcanzada = _indice(paso["etapa"]) <= actual
        disponible = deps_listas and etapa_alcanzada and r["estado"] in ("pendiente", "en_curso")
        espera = r["espera"]
        if r["estado"] == "pendiente" and not deps_listas:
            espera = f"Falta completar: {', '.join(faltan_deps)}"
        elif r["estado"] == "pendiente" and not etapa_alcanzada:
            espera = f"Se habilita en {nombre_etapa(paso['etapa'])}"
        # plazo: corre desde que el paso quedó disponible (entrada a la etapa o la última dependencia cumplida)
        limite = None
        vencido = False
        if paso.get("plazo_dias") is not None and etapa_alcanzada and deps_listas:
            inicio = _aware(p.etapa_desde if paso["etapa"] == p.etapa else None) or _aware(p.creado_en) or _ahora()
            for d in deps:
                if d.get("terminado_en") and d["terminado_en"] > inicio:
                    inicio = d["terminado_en"]
            limite = inicio + timedelta(days=int(paso["plazo_dias"]))
            vencido = r["estado"] in ("pendiente", "en_curso") and _ahora() > limite
        salida.append({
            "id": paso["id"], "tipo": paso["tipo"], "nombre": paso["nombre"], "etapa": paso["etapa"],
            "etapaTexto": nombre_etapa(paso["etapa"]), "obligatorio": paso["obligatorio"],
            "dependeDe": paso.get("depende_de", []), "regla": paso["regla"],
            "reglaTexto": _texto_regla(paso["regla"]), "tipoEntrevista": paso.get("tipo_entrevista"),
            "responsable": r["responsableTexto"], "responsableConfig": paso.get("responsable") or {},
            "estado": r["estado"], "estadoTexto": ESTADOS_PASO[r["estado"]],
            "resultado": r["resultado"], "resultadoTexto": RESULTADOS_PASO.get(r["resultado"] or "", ""),
            "detalle": r.get("detalle", ""), "cumpleRegla": bool(r["cumple"]) and r["estado"] in ("completada", "omitida", "cancelada"),
            "espera": espera, "disponible": disponible, "revisadoPor": r.get("revisadoPor", ""),
            "evaluacion": r.get("evaluacion"), "score": r.get("score"),
            "plazoDias": paso.get("plazo_dias"), "fechaLimite": limite.isoformat() if limite else None, "vencido": vencido,
            "decision": r.get("decision"), "heredado": bool(paso.get("heredado")),
            "accion": _accion(paso, r, disponible),
            "_terminado_en": r.get("terminado_en"),
        })
    return salida


def _texto_regla(regla: dict) -> str:
    t = regla.get("tipo")
    if t == "calificacion":
        return f"Calificación mínima {regla.get('minimo', CALIFICACION_MINIMA_DEFAULT)}"
    if t == "dictamen":
        return "Dictamen favorable"
    return REGLAS_APROBACION.get(t, "")


def _accion(paso: dict, r: dict, disponible: bool) -> Optional[dict]:
    """Acción del paso en la ficha. Reutiliza SIEMPRE lo que ya existe: «Agregar evaluación» (precargada), la tarjeta
    de la evaluación, la pestaña del chat / CV / evaluación integral o el expediente."""
    tipo, estado = paso["tipo"], r["estado"]
    if estado in ("omitida", "cancelada"):
        return None
    if tipo in TIPOS_PASO_EVALUACION:
        if r.get("evaluacion") and estado != "pendiente":
            return {"clave": "consultar_evaluacion", "texto": "Consultar", "evaluacion": r["evaluacion"]}
        if disponible:
            return {"clave": "iniciar_evaluacion", "texto": "Iniciar"}
        return None
    destino = {"prefiltro_whatsapp": "whatsapp", "prefiltro_web": "documentos", "analisis_cv": "documentos",
               "entrevista_agente": "evaluaciones", "documentos": "contratacion", "condiciones": "contratacion",
               "onboarding": "contratacion", "alta": "contratacion"}[tipo]
    if estado == "completada" or not disponible:
        return {"clave": "consultar", "texto": "Consultar", "pestana": destino} if estado != "pendiente" else None
    texto = {"documentos": "Solicitar documentos", "condiciones": "Capturar condiciones", "alta": "Dar de alta",
             "onboarding": "Ver tareas"}.get(tipo, "Consultar")
    return {"clave": "abrir", "texto": texto, "pestana": destino}


# ============================================================ etapas: compuerta, siguiente etapa, resumen

def _satisfecho(paso: dict) -> bool:
    return paso["estado"] in ("omitida", "cancelada") or (paso["estado"] == "completada" and paso["cumpleRegla"])


def faltantes(pasos: List[dict], desde: str, hasta: str) -> List[dict]:
    """Obligatorios de las etapas [desde, hasta) que no cumplen su condición de avance (no completados, sin la regla
    cumplida o pendientes). Las etapas sin obligatorios nunca aparecen."""
    i, j = _indice(desde), _indice(hasta)
    return [x for x in pasos if x["obligatorio"] and not x["heredado"] and i <= _indice(x["etapa"]) < j and not _satisfecho(x)]


def siguiente_etapa(p: Postulacion, etapa: Optional[str] = None) -> Optional[str]:
    """La siguiente etapa CON pasos (las vacías no bloquean); si ninguna posterior tiene pasos, la inmediata."""
    etapa = etapa or p.etapa
    i = _indice(etapa)
    if i < 0 or i >= len(ETAPAS_CANDIDATO) - 1:
        return None
    con_pasos = {x["etapa"] for x in (p.proceso or {}).get("pasos", [])}
    for e in ETAPAS_CANDIDATO[i + 1:]:
        if e in con_pasos:
            return e
    return ETAPAS_CANDIDATO[i + 1]


def _texto_faltante(x: dict) -> str:
    if x["estado"] == "completada" and not x["cumpleRegla"]:
        return f"{x['nombre']} ({x['resultadoTexto'] or 'no cumple la condición'}{': ' + x['espera'] if x['espera'] else ''})"
    return f"{x['nombre']}" + (f" ({x['espera']})" if x["espera"] else "")


def mensaje_bloqueo(falta: List[dict], hacia: str) -> str:
    return (f"No se puede avanzar a {nombre_etapa(hacia)}: faltan pasos obligatorios del proceso — "
            + "; ".join(_texto_faltante(x) for x in falta)
            + ". Complétalos u omítelos con justificación (requiere autorización).")


def resumen(p: Postulacion, evaluaciones=None) -> dict:
    """Vista de seguimiento de la ficha: etapa actual + siguiente acción principal arriba, y todos los pasos
    agrupados por etapa (nombre, responsable, estado, resultado, acción)."""
    if not tiene_proceso(p):
        v = p.vacante
        return {"tieneProceso": False, "vacanteTieneProceso": bool(v is not None and tiene_proceso(v))}
    pasos = estado_pasos(p, evaluaciones)
    cfg = normalizar_etapas(p.proceso.get("etapas"))
    sig = siguiente_etapa(p)
    falta_actual = faltantes(pasos, p.etapa, sig) if sig else []
    etapas = []
    for e in ETAPAS_CANDIDATO:
        de_etapa = [x for x in pasos if x["etapa"] == e]
        falta_e = [x for x in de_etapa if x["obligatorio"] and not x["heredado"] and not _satisfecho(x)]
        etapas.append({
            "etapa": e, "texto": nombre_etapa(e), "actual": e == p.etapa, "avanceAutomatico": cfg[e]["avance_automatico"],
            "sinPasos": not de_etapa, "lista": not falta_e, "faltantes": [_texto_faltante(x) for x in falta_e],
            "pasos": [{k: v for k, v in x.items() if not k.startswith("_")} for x in de_etapa],
        })
    return {
        "tieneProceso": True,
        "plantilla": p.proceso.get("plantilla_nombre") or "",
        "personalizado": bool(p.proceso.get("personalizado")),
        "version": int(p.proceso.get("vacante_version") or p.proceso.get("version") or 1),
        "desactualizado": desactualizado(p),
        "etapaActual": p.etapa, "etapaTexto": nombre_etapa(p.etapa),
        "siguienteEtapa": sig, "siguienteEtapaTexto": nombre_etapa(sig) if sig else None,
        "listaParaAvanzar": bool(sig) and not falta_actual,
        "siguienteAccion": _siguiente_accion(p, pasos, sig, falta_actual),
        "alertas": [{"paso": x["id"], "texto": f"Plazo vencido: {x['nombre']}", "fechaLimite": x["fechaLimite"]}
                    for x in pasos if x["vencido"]],
        "etapas": etapas,
    }


def _siguiente_accion(p: Postulacion, pasos: List[dict], sig: Optional[str], falta: List[dict]) -> dict:
    if not p.activa:
        return {"tipo": "cerrada", "texto": "Postulación cerrada", "detalle": "Mover de etapa la reabre."}
    de_etapa = [x for x in pasos if x["etapa"] == p.etapa]
    iniciables = [x for x in de_etapa if x["disponible"] and x["estado"] == "pendiente" and x["accion"]]
    obligatorios = [x for x in iniciables if x["obligatorio"]] or iniciables
    if obligatorios:
        x = obligatorios[0]
        return {"tipo": "paso", "paso": x["id"], "texto": f"{x['accion']['texto']}: {x['nombre']}", "accion": x["accion"],
                "detalle": x["espera"]}
    if sig and not falta:
        if p.etapa == "Contratación" and sig == "Onboarding":
            return {"tipo": "abrir", "texto": "Enviar a Onboarding", "accion": {"clave": "abrir", "pestana": "contratacion"},
                    "detalle": "Revisa el resumen e inicia el Onboarding desde el expediente."}
        return {"tipo": "avanzar", "etapa": sig, "texto": f"Avanzar a {nombre_etapa(sig)}",
                "detalle": "Todos los pasos obligatorios de esta etapa cumplen su condición."}
    en_espera = [x for x in de_etapa if x["estado"] in ("en_curso", "completada") and not _satisfecho(x) and x["obligatorio"]]
    if en_espera:
        x = en_espera[0]
        return {"tipo": "esperar", "paso": x["id"], "texto": f"En espera: {x['nombre']}",
                "detalle": x["espera"] or x["resultadoTexto"], "accion": x["accion"]}
    if falta:
        return {"tipo": "esperar", "texto": "En espera", "detalle": "; ".join(_texto_faltante(x) for x in falta)}
    return {"tipo": "fin", "texto": "Proceso completo", "detalle": ""}


# ============================================================ decisiones de RH sobre un paso

def _paso(p: Postulacion, paso_id: str) -> dict:
    paso = next((x for x in (p.proceso or {}).get("pasos", []) if x["id"] == paso_id), None)
    if paso is None:
        raise ErrorProceso(404, "Paso del proceso no encontrado.")
    return paso


def omitir(db: Session, p: Postulacion, paso_id: str, u, motivo: str, clave: str = "omitida") -> dict:
    """Omitir (o cancelar) un paso. Obligatorio → justificación (mínimo 10 caracteres) y autorización explícita
    (`puede_autorizar_omisiones`); la decisión queda con nombre en el historial y la bitácora. No hace commit."""
    paso = _paso(p, paso_id)
    motivo = (motivo or "").strip()
    actual = next((x for x in estado_pasos(p) if x["id"] == paso_id), None)
    if actual and actual["estado"] == "completada":
        raise ErrorProceso(409, f"«{paso['nombre']}» ya está completada.")
    if paso["obligatorio"]:
        if len(motivo) < MOTIVO_MINIMO:
            raise ErrorProceso(400, f"Omitir «{paso['nombre']}» (obligatorio) requiere una justificación de al menos {MOTIVO_MINIMO} caracteres.")
        if not u.puede_autorizar_omisiones():
            raise ErrorProceso(403, f"«{paso['nombre']}» es obligatorio: omitirlo requiere autorización. Pídelo a un Administrador o a "
                                    "quien tenga el permiso «Autorizar omisiones».")
    sello = _ahora()
    decisiones = dict(p.proceso_estado or {})
    d = dict(decisiones.get(paso_id) or {})
    d.pop("omitida", None)
    d.pop("cancelada", None)
    d[clave] = {"por": u.nombre, "motivo": motivo[:500], "fecha": sello.isoformat(), "obligatorio": paso["obligatorio"],
                "autorizado_por": u.nombre if paso["obligatorio"] else ""}
    decisiones[paso_id] = d
    p.proceso_estado = decisiones
    accion = "Omitido" if clave == "omitida" else "Cancelado"
    p.historial = list(p.historial or []) + [{
        "evento": f"paso_{clave}", "usuario": u.nombre, "fecha": sello.isoformat(), "paso": paso_id, "motivo": motivo[:500],
        "texto": f"{accion} el paso «{paso['nombre']}»{' (obligatorio, autorizado)' if paso['obligatorio'] else ''} por {u.nombre}: {motivo or 'sin motivo'}",
    }]
    registrar(db, u.nombre, f"proceso_paso_{clave}", "postulacion", p.codigo,
              {"paso": paso_id, "nombre": paso["nombre"], "obligatorio": paso["obligatorio"], "motivo": motivo[:500],
               "autorizado_por": u.nombre if paso["obligatorio"] else "", "correo_rh": getattr(u, "correo", "")})
    return d[clave]


def reactivar(db: Session, p: Postulacion, paso_id: str, u) -> None:
    paso = _paso(p, paso_id)
    decisiones = dict(p.proceso_estado or {})
    d = dict(decisiones.get(paso_id) or {})
    if not (d.pop("omitida", None) or d.pop("cancelada", None)):
        raise ErrorProceso(409, f"«{paso['nombre']}» no está omitido ni cancelado.")
    decisiones[paso_id] = d
    p.proceso_estado = decisiones
    sello = _ahora()
    p.historial = list(p.historial or []) + [{"evento": "paso_reactivado", "usuario": u.nombre, "fecha": sello.isoformat(),
                                              "paso": paso_id, "texto": f"Reactivado el paso «{paso['nombre']}» por {u.nombre}"}]
    registrar(db, u.nombre, "proceso_paso_reactivado", "postulacion", p.codigo, {"paso": paso_id, "correo_rh": getattr(u, "correo", "")})


def verificar_avance(db: Session, p: Postulacion, hacia: str, u, omitir_obligatorios: bool = False, motivo: str = "") -> List[str]:
    """Compuerta de un avance de RH hacia `hacia` (solo hacia adelante y con proceso). Sin pendientes → []. Con
    obligatorios sin cumplir: si RH no pidió omitirlos → 409 con lo que falta; si lo pidió → exige justificación y
    autorización, los marca «Omitida» y regresa sus nombres. No hace commit."""
    if not tiene_proceso(p) or _indice(hacia) <= _indice(p.etapa):
        return []
    falta = faltantes(estado_pasos(p), p.etapa, hacia)
    if not falta:
        return []
    if not omitir_obligatorios:
        raise ErrorProceso(409, mensaje_bloqueo(falta, hacia))
    for x in falta:
        omitir(db, p, x["id"], u, motivo)
    return [x["nombre"] for x in falta]


def aplicar_version_vigente(db: Session, p: Postulacion, u) -> dict:
    """«Aplicar versión vigente» (explícito, nunca automático): la postulación toma el proceso actual de su vacante.
    Se conservan las decisiones de RH por paso; un paso que ya NO existe pero tiene actividad (evaluación ligada o
    algo completado) se conserva como «heredado» (fuera del proceso, no obligatorio) para no cancelar nada en curso."""
    v = p.vacante
    if v is None or not tiene_proceso(v):
        raise ErrorProceso(409, "La vacante no tiene un proceso configurado.")
    anterior = estado_pasos(p) if tiene_proceso(p) else []
    nuevo = copy.deepcopy(v.proceso)
    ids_nuevos = {x["id"] for x in nuevo["pasos"]}
    heredados = []
    for x in anterior:
        if x["id"] in ids_nuevos or x["heredado"]:
            continue
        if x["evaluacion"] or x["estado"] in ("en_curso", "completada"):
            original = next(o for o in p.proceso["pasos"] if o["id"] == x["id"])
            heredados.append({**original, "obligatorio": False, "depende_de": [], "heredado": True})
    nuevo["pasos"] = nuevo["pasos"] + heredados
    nuevo["vacante_version"] = int(nuevo.get("version") or 1)
    nuevo["vacante_id"] = v.id
    nuevo["congelado_en"] = _ahora().isoformat()
    previa = int((p.proceso or {}).get("vacante_version") or 0)
    p.proceso = nuevo
    sello = _ahora()
    p.historial = list(p.historial or []) + [{
        "evento": "proceso_actualizado", "usuario": u.nombre, "fecha": sello.isoformat(),
        "texto": f"Proceso actualizado a la versión {nuevo['vacante_version']} de la vacante por {u.nombre}"
                 + (f"; se conservan {len(heredados)} paso(s) con actividad fuera del proceso vigente" if heredados else ""),
    }]
    registrar(db, u.nombre, "proceso_version_aplicada", "postulacion", p.codigo,
              {"de": previa, "a": nuevo["vacante_version"], "heredados": [h["id"] for h in heredados], "correo_rh": getattr(u, "correo", "")})
    return {"version": nuevo["vacante_version"], "heredados": [h["nombre"] for h in heredados]}


# ============================================================ conexión con la ejecución real

def paso_de_tipo(p: Postulacion, tipo: str) -> Optional[dict]:
    return next((x for x in (p.proceso or {}).get("pasos", []) if x["tipo"] == tipo and not x.get("heredado")), None)


def enfoque_entrevista_agente(p: Optional[Postulacion], v) -> str:
    """Enfoque de la Entrevista Red Human: el del paso del proceso si lo trae; si no, el de la vacante."""
    paso = paso_de_tipo(p, "entrevista_agente") if p is not None and tiene_proceso(p) else None
    if paso and paso.get("tipo_entrevista") in ENFOQUES_ENTREVISTA:
        return paso["tipo_entrevista"]
    return ((v.enfoque_entrevista if v else "profesional") or "profesional")


def paso_para_evaluacion(p: Postulacion, tipo: str, paso_id: str = "", evaluaciones=None) -> Optional[dict]:
    """Paso que va a cumplir una evaluación NUEVA: el indicado (debe ser del mismo tipo) o el primero de ese tipo que
    aún no tiene una evaluación viva. None = evaluación fuera del proceso."""
    if not tiene_proceso(p):
        if paso_id:
            raise ErrorProceso(409, "Esta postulación no tiene proceso configurado.")
        return None
    pasos = p.proceso["pasos"]
    if paso_id:
        paso = _paso(p, paso_id)
        if paso["tipo"] != tipo:
            raise ErrorProceso(400, f"El paso «{paso['nombre']}» es de otro tipo.")
        return paso
    asignadas = asignar_evaluaciones(pasos, _evaluaciones_de(p, evaluaciones))
    for paso in pasos:
        if paso["tipo"] == tipo and not paso.get("heredado"):
            ev = asignadas.get(paso["id"])
            if ev is None or ev.estado == "cancelada":
                return paso
    return None


def mueve_entrevista_ia(p: Postulacion) -> bool:
    """¿Entrar a Filtro Red Human debe iniciar la agenda de la Entrevista Red Human? Siempre sin proceso; con proceso,
    solo si el proceso la incluye."""
    return not tiene_proceso(p) or paso_de_tipo(p, "entrevista_agente") is not None


def etapa_lista(p: Postulacion) -> Tuple[Optional[str], List[dict]]:
    sig = siguiente_etapa(p)
    if not sig:
        return None, []
    return sig, faltantes(estado_pasos(p), p.etapa, sig)


async def avanzar_si_corresponde(db: Session, p: Postulacion) -> List[str]:
    """Interruptor de avance automático de la etapa: si está encendido y TODOS los obligatorios de la etapa cumplen su
    condición, mueve a la siguiente etapa con pasos (las vacías no bloquean) con la MISMA función que usa RH
    (`candidatos.aplicar_movimiento`, que hace commit). Nunca sale de Contratación ni de Onboarding, nunca reabre ni
    descarta. Un bloqueo (p. ej. sin consentimiento para abrir el expediente) solo queda en bitácora. Regresa las
    etapas a las que se movió."""
    from fastapi import HTTPException

    if not tiene_proceso(p) or not p.activa:
        return []
    movidas: List[str] = []
    for _ in range(len(ETAPAS_CANDIDATO)):
        cfg = normalizar_etapas(p.proceso.get("etapas"))
        if p.etapa in ETAPAS_SIN_AVANCE_AUTOMATICO or not cfg.get(p.etapa, {}).get("avance_automatico"):
            break
        sig, falta = etapa_lista(p)
        if not sig or falta or sig == "Onboarding":
            break
        from ..routers.candidatos import EtapaIn, aplicar_movimiento

        actor = SimpleNamespace(nombre=ACTOR_AUTOMATICO, correo="", id=None, puede_autorizar_omisiones=lambda: False)
        desde = p.etapa
        try:
            await aplicar_movimiento(db, p, EtapaIn(etapa=sig, manual=True, comentario="Avance automático del proceso"), actor,
                                     automatico=True)
        except HTTPException as ex:
            registrar(db, ACTOR_AUTOMATICO, "avance_automatico_detenido", "postulacion", p.codigo,
                      {"de": desde, "a": sig, "motivo": str(ex.detail)[:300]})
            db.commit()
            break
        movidas.append(sig)
    return movidas


async def avanzar_seguro(db: Session, p: Optional[Postulacion]) -> List[str]:
    """Igual que `avanzar_si_corresponde` pero nunca rompe la acción que lo dispara (registrar un resultado, revisar,
    aprobar documentos, terminar la entrevista…)."""
    if p is None:
        return []
    try:
        return await avanzar_si_corresponde(db, p)
    except Exception as ex:  # noqa: BLE001
        # Sin rollback: la acción que lo disparó (un turno del prefiltro, un resultado…) puede traer cambios sin guardar.
        try:
            registrar(db, "sistema", "avance_automatico_error", "postulacion", p.codigo, {"error": str(ex)[:300]})
        except Exception:  # noqa: BLE001
            pass
        return []
