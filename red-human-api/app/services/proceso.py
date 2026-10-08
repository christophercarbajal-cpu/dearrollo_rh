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
    conclusiones_de, nombre_etapa, registrar, ruta_automatica, score_de_entrevista,
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
        if etapa not in defs["etapas"] and not (crudo.get("adhoc") and etapa in ETAPAS_CANDIDATO):
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
        if tipo == "solicitud_web":
            paso["con_cv"] = bool(crudo.get("con_cv", crudo.get("conCv", False)))
        if tipo == "psicometrica":
            # 2026-10-07: batería predeterminada del catálogo (ids de PruebaPsicometrica). La ruta la define; la vacante
            # la hereda en su COPIA y puede cambiarla sin tocar la ruta. La existencia se valida al asignar.
            ids = []
            for x in (crudo.get("pruebas", crudo.get("prueba_ids")) or []):
                try:
                    ids.append(int(x))
                except (TypeError, ValueError):
                    raise ErrorProceso(400, f"La batería de «{defs['nombre']}» debe ser una lista de pruebas del catálogo.")
            paso["pruebas"] = list(dict.fromkeys(ids))
        if tipo == "documentos" and crudo.get("documentos"):
            paso["documentos"] = [str(d).strip()[:120] for d in crudo["documentos"] if str(d).strip()]
        for bandera in ("adhoc", "heredado"):  # actividad agregada solo a ESTA postulación / fuera del proceso vigente
            if crudo.get(bandera):
                paso[bandera] = True
        if isinstance(crudo.get("config"), dict) and crudo["config"]:
            # 2026-10-08: configuración completa guardada al AGREGAR la actividad; «Iniciar» la ejecuta sin volver a pedirla
            paso["config"] = limpiar_config(crudo["config"])
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


CAMPOS_CONFIG = ("forma", "evaluador", "cita", "instrucciones", "liga_externa_candidato", "proveedor", "prueba_ids", "examen",
                 "referencias", "iniciar_al_guardar")


def limpiar_config(config: dict) -> dict:
    """Solo los campos conocidos de la configuración de una actividad (formulario «Agregar actividad»)."""
    salida = {}
    for k in CAMPOS_CONFIG:
        v = config.get(k)
        if v in (None, "", [], {}):
            continue
        if k in ("instrucciones", "liga_externa_candidato", "proveedor", "examen", "forma") and isinstance(v, str):
            v = v.strip()[:4000]
        salida[k] = v
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


# ============================================================ rutas base precargadas (2026-10-06)
# Las tres rutas del documento de reglas. Se siembran como plantillas EDITABLES en cada Cuenta (`asegurar_rutas_base`)
# y viven aquí como respaldo: «Corporativos sin psicometría» es el último nivel de la cascada de asignación. El orden
# visual NO crea dependencias: solo las explícitas (`depende_de`, «Esperar a…»); lo demás corre en paralelo.

RUTA_RESPALDO = "corporativos"
_AUTO_TODAS = {e: {"avance_automatico": True} for e in ETAPAS_CANDIDATO}  # Contratación/Onboarding se apagan al normalizar


def _cola_contratacion_onboarding() -> List[dict]:
    """Contratación y Onboarding, iguales en las tres rutas. Los documentos de ingreso viven en Onboarding: nunca
    bloquean la ENTRADA a esa etapa (la compuerta solo revisa las etapas que se dejan atrás)."""
    return [
        {"id": "condiciones", "tipo": "condiciones", "nombre": "Condiciones de contratación", "etapa": "Contratación"},
        {"id": "carta-contrato", "tipo": "carta_contrato", "nombre": "Carta intención / contrato", "etapa": "Contratación",
         "depende_de": ["condiciones"]},
        {"id": "documentos-ingreso", "tipo": "documentos", "nombre": "Documentos de ingreso", "etapa": "Onboarding"},
        {"id": "induccion", "tipo": "induccion", "nombre": "Inducción", "etapa": "Onboarding"},
        {"id": "alta", "tipo": "alta", "nombre": "Alta como colaborador", "etapa": "Onboarding",
         "depende_de": ["documentos-ingreso", "induccion"]},
    ]


RUTAS_BASE = {
    "masivos": {
        "nombre": "Masivos",
        "descripcion": "Solicitud web sin CV → prefiltro por WhatsApp → documentos por liga → Entrevista Red Human → médica y "
                       "entrevista humana → contratación → onboarding (12 pasos).",
        "pasos": [
            {"id": "solicitud-web", "tipo": "solicitud_web", "nombre": "Solicitud web sin CV", "con_cv": False},
            {"id": "prefiltro-whatsapp", "tipo": "prefiltro_whatsapp", "nombre": "Continuar prefiltro por WhatsApp",
             "depende_de": ["solicitud-web"]},
            {"id": "solicitar-documentos", "tipo": "solicitud_documentos", "nombre": "Solicitar documentos por liga",
             "etapa": "Prefiltro", "depende_de": ["prefiltro-whatsapp"]},
            {"id": "validar-documentos", "tipo": "documentos", "nombre": "Validar documentos", "etapa": "Prefiltro",
             "depende_de": ["solicitar-documentos"]},
            {"id": "entrevista_red_human", "tipo": "entrevista_agente", "nombre": "Entrevista Red Human"},
            {"id": "medica", "tipo": "medica", "nombre": "Evaluación médica", "etapa": "Entrevista Humana"},
            {"id": "entrevista-humana", "tipo": "entrevista_humana", "nombre": "Entrevista humana", "etapa": "Entrevista Humana"},
            *_cola_contratacion_onboarding(),
        ],
    },
    "corporativos": {
        "nombre": "Corporativos sin psicometría",
        "descripcion": "Solicitud web con CV y prefiltro → Análisis de CV y Entrevista Red Human → entrevista humana → "
                       "contratación → onboarding (9 pasos).",
        "pasos": [
            {"id": "solicitud-web", "tipo": "prefiltro_web", "nombre": "Solicitud web con CV y prefiltro"},
            {"id": "analisis_cv", "tipo": "analisis_cv", "nombre": "Análisis de CV", "etapa": "Entrevista IA"},
            {"id": "entrevista_red_human", "tipo": "entrevista_agente", "nombre": "Entrevista Red Human"},
            {"id": "entrevista-humana", "tipo": "entrevista_humana", "nombre": "Entrevista humana", "etapa": "Entrevista Humana"},
            *_cola_contratacion_onboarding(),
        ],
    },
    "corporativos_psicometria": {
        "nombre": "Corporativos con psicometría",
        "descripcion": "Igual que Corporativos, con Psicometría en Filtro humano (10 pasos).",
        "pasos": [
            {"id": "solicitud-web", "tipo": "prefiltro_web", "nombre": "Solicitud web con CV y prefiltro"},
            {"id": "analisis_cv", "tipo": "analisis_cv", "nombre": "Análisis de CV", "etapa": "Entrevista IA"},
            {"id": "entrevista_red_human", "tipo": "entrevista_agente", "nombre": "Entrevista Red Human"},
            {"id": "psicometria", "tipo": "psicometrica", "nombre": "Psicometría", "etapa": "Entrevista Humana"},
            {"id": "entrevista-humana", "tipo": "entrevista_humana", "nombre": "Entrevista humana", "etapa": "Entrevista Humana"},
            *_cola_contratacion_onboarding(),
        ],
    },
}


def ruta_base(clave: str) -> dict:
    """Ruta base normalizada (nombre, descripción, pasos, etapas con avance automático encendido)."""
    if clave not in RUTAS_BASE:
        raise ErrorProceso(404, "Ruta base no encontrada.")
    r = copy.deepcopy(RUTAS_BASE[clave])
    return {"nombre": r["nombre"], "descripcion": r["descripcion"], "pasos": normalizar_pasos(r["pasos"]),
            "etapas": normalizar_etapas(_AUTO_TODAS)}


def asegurar_rutas_base(db: Session, cuenta_id: int, por: str = "sistema") -> int:
    """Siembra las tres rutas base como plantillas EDITABLES de la Cuenta, una sola vez (por `ruta_base`; si RH la
    desactivó o la editó, se respeta). Idempotente; regresa cuántas creó. Nunca falla el arranque."""
    if not _tablas_proceso():
        return 0
    try:
        existentes = {r for (r,) in db.query(PlantillaProceso.ruta_base).filter(PlantillaProceso.cuenta_id == cuenta_id,
                                                                                 PlantillaProceso.ruta_base != "")}
    except Exception:  # noqa: BLE001 — tabla del paso no fatal ausente: la cascada usa la ruta en código
        return 0
    n = 0
    for clave in RUTAS_BASE:
        if clave in existentes:
            continue
        r = ruta_base(clave)
        db.add(PlantillaProceso(cuenta_id=cuenta_id, nombre=r["nombre"], descripcion=r["descripcion"], pasos=r["pasos"],
                                etapas=r["etapas"], version=1, predeterminada=False, activa=True, creado_por=por,
                                actualizada_por=por, ruta_base=clave))
        n += 1
    if n:
        db.flush()
    return n


def asegurar_rutas_base_todas(db: Session) -> int:
    from ..models import Cuenta

    try:
        ids = [cid for (cid,) in db.query(Cuenta.id).filter(Cuenta.estado != "Eliminada")]
    except Exception:  # noqa: BLE001
        return 0
    return sum(asegurar_rutas_base(db, cid) for cid in ids)


# ============================================================ plantillas → vacante → postulación (copias)

def plantilla_dict(pl: PlantillaProceso) -> dict:
    return {
        "id": pl.id, "nombre": pl.nombre, "descripcion": pl.descripcion or "", "pasos": pl.pasos or [],
        "etapas": normalizar_etapas(pl.etapas), "version": pl.version or 1, "predeterminada": bool(pl.predeterminada),
        "activa": bool(pl.activa), "creadoPor": pl.creado_por, "actualizadaPor": pl.actualizada_por or pl.creado_por,
        "actualizadaEn": pl.actualizada_en.isoformat() if pl.actualizada_en else None, "rutaBase": getattr(pl, "ruta_base", "") or "",
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


def _tablas_proceso() -> bool:
    from .modulos_rh import disponible

    return disponible()  # plantillas_proceso vive en el paso NO fatal del arranque


def _plantilla_ruta(db: Optional[Session], cuenta_id: Optional[int], clave: str) -> Optional[PlantillaProceso]:
    if db is None or not cuenta_id or not _tablas_proceso():
        return None
    try:
        return (db.query(PlantillaProceso)
                .filter(PlantillaProceso.cuenta_id == cuenta_id, PlantillaProceso.ruta_base == clave, PlantillaProceso.activa.is_(True))
                .first())
    except Exception:  # noqa: BLE001
        return None


def ruta_para(db: Optional[Session], cuenta_id: Optional[int], v=None) -> dict:
    """Cascada de asignación (documento de reglas): 1) proceso de la vacante, 2) proceso predeterminado de la Cuenta,
    3) «Corporativos sin psicometría» (la plantilla de la Cuenta si sigue activa; si no, la ruta en código). Regresa una
    COPIA lista para guardarse en la postulación, con `origen` = vacante | cuenta | base."""
    if v is not None and tiene_proceso(v):
        proc = copy.deepcopy(v.proceso)
        proc.update({"origen": "vacante", "vacante_version": int(proc.get("version") or 1), "vacante_id": v.id})
        return proc
    pl = predeterminada(db, cuenta_id) if db is not None and cuenta_id and _tablas_proceso() else None
    origen = "cuenta"
    if pl is None:
        pl, origen = _plantilla_ruta(db, cuenta_id, RUTA_RESPALDO), "base"
    if pl is not None:
        proc = proceso_desde_plantilla(pl)
    else:
        r = ruta_base(RUTA_RESPALDO)
        proc = {"plantilla_id": None, "plantilla_nombre": r["nombre"], "plantilla_version": 1, "version": 1,
                "personalizado": False, "pasos": r["pasos"], "etapas": r["etapas"]}
    proc.update({"origen": origen, "ruta_base": getattr(pl, "ruta_base", "") or (RUTA_RESPALDO if origen == "base" else "")})
    if v is not None:
        proc["vacante_id"] = v.id
    return proc


def congelar(p: Postulacion, v=None, db: Optional[Session] = None) -> bool:
    """Toda postulación guarda una COPIA estática de su ruta al nacer (cascada de `ruta_para`). Una ya congelada nunca
    se reemplaza, salvo la PROVISIONAL de una postulación que nació sin vacante (menú de WhatsApp/Telegram): al elegir
    la vacante toma la de la cascada con esa vacante, mientras RH no haya tomado decisiones sobre sus pasos. Regresa
    True si congeló."""
    v = v if v is not None else p.vacante
    actual = p.proceso or {}
    if tiene_proceso(p) and not (actual.get("provisional") and v is not None and not (p.proceso_estado or {})):
        return False
    db = db or object_session(p)
    proc = ruta_para(db, p.cuenta_id or (v.cuenta_id if v is not None else None), v)
    if v is None:
        proc["provisional"] = True
    proc["congelado_en"] = _ahora().isoformat()
    proc["etapa_base"] = p.etapa if p.etapa in ETAPAS_CANDIDATO else ETAPAS_CANDIDATO[0]
    p.proceso = proc
    return True


def desactualizado(p: Postulacion) -> bool:
    v = p.vacante
    if not tiene_proceso(p):
        return bool(v is not None and tiene_proceso(v))
    if v is None or not tiene_proceso(v):
        return False
    if (p.proceso or {}).get("origen") in ("cuenta", "base"):
        return True  # la vacante tiene hoy un proceso propio y esta postulación conserva la ruta de respaldo
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


def liga_actividad(paso: dict, p: Postulacion, ev: Optional[Evaluacion], r: dict) -> Optional[dict]:
    """La URL REAL de la actividad (2026-10-08, reemplaza la «Liga de Telegram» genérica): {url, texto}. Abrirla o
    copiarla NUNCA marca nada como enviado. None si la actividad no tiene liga propia."""
    from ..config import settings

    tipo = paso["tipo"]
    if r["estado"] in ("omitida", "cancelada"):
        return None
    if tipo == "entrevista_agente":
        e = next((x for x in reversed(list(p.entrevistas or [])) if x.estado != "evaluada"), None)
        return {"url": f"{settings.app_url}/entrevista/{e.token}", "texto": "Sala de la entrevista"} if e is not None and e.token else None
    if tipo in TIPOS_PASO_EVALUACION and ev is not None and ev.estado != "cancelada":
        from . import evaluaciones as sev

        if ev.consentimiento == "pendiente" and ev.consentimiento_token:
            return {"url": sev.liga_consentimiento(ev), "texto": "Liga del consentimiento"}
        if sev.esperando_referencias(ev) and ev.referencias_token:
            return {"url": sev.liga_referencias(ev), "texto": "Liga para capturar referencias"}
        if ev.forma == "integrada" and ev.clave_proveedor:
            ps = r.get("psicometria") or {}
            url = ps.get("liga") or sev._url_proveedor(ev)
            return {"url": url, "texto": "Liga de la prueba", "clave": ev.clave_proveedor} if url else None
        if ev.forma == "liga_otro_sistema" and ev.liga_externa_candidato:
            return {"url": ev.liga_externa_candidato, "texto": "Liga de la prueba"}
        if ev.token_evaluador and sev.liga_evaluador_disponible(ev):
            return {"url": sev.liga_evaluador(ev), "texto": "Liga del médico" if ev.tipo == "medica" else "Liga del evaluador"}
        return None
    if tipo in ("solicitud_documentos", "documentos") and p.expediente is not None and p.expediente.token:
        return {"url": f"{settings.app_url}/expediente/{p.expediente.token}", "texto": "Liga de documentos"}
    return None


def _proveedor_visible(nombre: str) -> str:
    """En la interfaz la plataforma psicométrica es «Red Human» (2026-10-08); el valor crudo sigue en la base."""
    return "Red Human" if "psicom" in (nombre or "").lower() else (nombre or "")


def _paso_evaluacion(paso: dict, ev: Optional[Evaluacion]) -> dict:
    regla = paso["regla"]
    if ev is None:
        return {"estado": "pendiente", "resultado": None, "cumple": False, "espera": "", "detalle": "Sin agregar",
                "revisadoPor": "Pendiente de revisión", "evaluacion": None, "terminado_en": None}
    if ev.estado == "cancelada" and ev.consentimiento == "rechazado":
        # 2026-10-08 (médica en dos fases): el candidato RECHAZÓ el consentimiento → la evaluación se canceló sola y la
        # actividad queda «No aprobada» (nunca «cumplida»): detiene la compuerta y RH decide (descartar u omitir).
        return {"estado": "completada", "resultado": "no_favorable", "cumple": False, "espera": "",
                "detalle": "El candidato rechazó el consentimiento médico", "revisadoPor": "Pendiente de revisión",
                "evaluacion": ev.codigo, "terminado_en": _aware(ev.actualizado_en)}
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
        quien = ev.evaluador_nombre or _proveedor_visible(ev.proveedor)
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
        # 2026-10-08: los resultados del proveedor llegan solos (webhook + reintentos del job). «Reintentar
        # sincronización» SOLO con una falla CONFIRMADA de recuperación (`Evaluacion.sincronizacion.estado == fallida`).
        "sincronizable": bool(ev.clave_proveedor) and ev.estado in ("pendiente", "realizada_sin_resultado")
                         and (ev.sincronizacion or {}).get("estado") == "fallida",
        # 2026-10-08: «Enviada» SOLO con un envío CONFIRMADO de la liga (`enviada_en`); crear la evaluación, abrir o
        # copiar su liga no la marcan. Asignada sin enviar ni iniciar = «Sin iniciar».
        "programada": ev.estado == "pendiente" and not getattr(ev, "iniciada_en", None) and bool(ev.enviada_en),
        "sin_iniciar_visible": ev.estado == "pendiente" and not getattr(ev, "iniciada_en", None) and not ev.enviada_en,
    }


def bloque_psicometria(paso: dict, ev: Optional[Evaluacion], ahora: Optional[datetime] = None) -> dict:
    """Flujo simple de psicometría (2026-10-07, red-human-psicometria.md §5 y §8): SOLO tres estados derivados de la
    evaluación ligada al paso — Sin enviar · Enviada · Completada — más el aviso «Sin respuesta en N días». Es un dato
    ADICIONAL del paso (no cambia `estado`/`resultado`, que siguen usando la compuerta y la evaluación integral); el
    frontend solo lo pinta en las Cuentas de `CUENTAS_PSICOMETRIA_SIMPLE`."""
    from ..config import settings
    from ..models import ESTADOS_PSICOMETRIA_SIMPLE

    umbral = max(int(settings.psicometria_sin_respuesta_dias or 0), 0)
    vivo = ev is not None and ev.estado != "cancelada"
    status = "sin_enviar"
    if vivo and ev.estado == "con_resultado":
        status = "completada"
    elif vivo and ev.forma == "integrada":
        if ev.clave_proveedor or (ev.paso_integrada or "asignada") != "asignada" or ev.estado != "pendiente":
            status = "enviada"
    elif vivo and ev.forma != "registro_directo":
        status = "enviada"  # liga de otro sistema o asignada a una persona: ya salió del sistema
    if status == "enviada" and ev.forma == "integrada" and ev.clave_proveedor and not ev.enviada_en:
        # transacción 1 OK (clave generada) pero la 2 no confirmó ningún envío al candidato
        status = "error_envio"
    enviada_en = _aware((ev.enviada_en or ev.creado_en) if vivo and status != "sin_enviar" else None)
    dias = None
    if status == "enviada" and enviada_en is not None:
        dias = max(((ahora or _ahora()) - enviada_en).days, 0)
    resumen_txt = None
    archivo = None
    if status == "completada":
        legible = conclusiones_de(ev.tipo).get(ev.conclusion_vigente, "")
        comentario = " ".join((ev.comentarios or "").split())
        if len(comentario) > 160:
            comentario = comentario[:157].rstrip() + "…"
        resumen_txt = " · ".join(x for x in (legible, comentario) if x) or "Resultado recibido"
        adj = [a for a in (ev.adjuntos or []) if isinstance(a, dict) and a.get("id")]
        pdf = next((a for a in adj if "pdf" in (a.get("mime") or "").lower() or (a.get("nombre") or "").lower().endswith(".pdf")), None)
        elegido = pdf or (adj[0] if adj else None)
        if elegido:
            archivo = f"/evaluaciones/{ev.codigo}/adjuntos/{elegido['id']}"
    reenvio = None
    liga = None
    if status in ("enviada", "error_envio"):
        if ev.forma == "integrada" and ev.clave_proveedor:
            from . import psicometricas as psi

            reenvio = "proveedor"
            liga = (ev.liga_externa_candidato or "").strip() or psi.url_candidato(ev.clave_proveedor) or psi.PORTAL_SUSTENTANTE
        elif ev.forma == "liga_otro_sistema" and ev.liga_externa_candidato:
            reenvio = "otro_sistema"
            liga = ev.liga_externa_candidato
    return {
        "status": status,
        "statusTexto": ESTADOS_PSICOMETRIA_SIMPLE[status],
        "test_id": ev.prueba_id if vivo and ev.forma == "integrada" else None,
        "test_name": (ev.nombre_visible if vivo else "") or None,
        "is_external": bool(vivo and ev.forma != "integrada"),
        "required": bool(paso.get("obligatorio")),
        "sent_at": enviada_en.isoformat() if enviada_en else None,
        "completed_at": _aware(ev.registrada_en).isoformat() if status == "completada" and ev.registrada_en else None,
        "result_summary": resumen_txt,
        "result_file_url": archivo,
        "evaluacion": ev.codigo if vivo else None,
        "simulado": bool(vivo and ev.forma == "integrada" and not ev.clave_proveedor),
        "reenvio": reenvio,
        # liga REAL del candidato para «Copiar liga» (copiarla nunca marca nada como enviado) + su clave de acceso
        "liga": liga,
        "clave": ev.clave_proveedor if vivo and ev.forma == "integrada" and ev.clave_proveedor else None,
        "dias_sin_respuesta": dias if dias is not None and umbral and dias > umbral else None,
        "umbral_sin_respuesta": umbral,
    }


def _paso_red_human(paso: dict, p: Postulacion) -> dict:
    """Prefiltro (WhatsApp / web), Análisis de CV y Entrevista Red Human: los resuelve Red Human."""
    tipo, regla = paso["tipo"], paso["regla"]
    a = p.analisis or {}
    base = {"estado": "pendiente", "resultado": None, "cumple": False, "espera": "", "detalle": "",
            "revisadoPor": "Pendiente de revisión", "evaluacion": None, "terminado_en": None}
    if tipo == "prefiltro_web" and isinstance(a.get("prefiltro_web"), dict) and a["prefiltro_web"].get("resultado"):
        # Motor de ruta automatizado (2026-10-08): el prefiltro web ya se resolvió contra la vacante (o RH lo decidió)
        pw = a["prefiltro_web"]
        res = pw["resultado"]
        if res == "revision":
            return {**base, "estado": "en_curso", "espera": "Revisar prefiltro: " + (pw.get("motivo") or "respuesta no concluyente"),
                    "detalle": "Revisar prefiltro", "revisar_prefiltro": True}
        cumple = res == "cumple"
        por = pw.get("decidido_por") or "Red Human"
        return {**base, "estado": "completada", "resultado": "favorable" if cumple else "no_favorable", "cumple": cumple,
                "detalle": "Cumple" if cumple else ("No cumple: " + (pw.get("motivo") or "criterio excluyente")),
                "revisadoPor": f"Revisado por: {por}", "terminado_en": _aware_iso(pw.get("en"))}
    if tipo in ("prefiltro_whatsapp", "prefiltro_web") and not (a.get("prefiltro_web") or {}).get("resultado"):
        # 2026-10-08 (ruta automática): prefiltro conversacional — lo que falta lo pregunta el bot; nunca se aprueba solo
        from . import prefiltro_conversacional as pconv

        rc = pconv.resumen(p)
        if rc is not None and rc["pendiente"]:
            return {**base, "estado": "en_curso", "espera": f"Esperando respuesta del candidato: {rc['pendiente']}"}
    if tipo in ("prefiltro_whatsapp", "prefiltro_web"):
        web = bool(a.get("respuestas_web"))
        hecho = bool(p.prefiltro_completo) if tipo == "prefiltro_whatsapp" else (web or bool(p.prefiltro_completo))
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
    puntaje = score_de_entrevista(evaluada.evaluacion) if evaluada is not None else None
    if puntaje is not None:
        r = _con_score(base, regla, puntaje)  # score PROPIO de la entrevista (2026-10-08), nunca el del CV
        r["terminado_en"] = _aware(evaluada.finalizada_en)
        return r
    if evaluada is not None:
        return {**base, "estado": "completada", "resultado": None, "cumple": regla["tipo"] == "ninguna",
                "espera": "" if regla["tipo"] == "ninguna" else "Falta la revisión de RH (evaluación sin afinidad)",
                "detalle": "Evaluada", "revisadoPor": "Revisado por: Red Human", "terminado_en": _aware(evaluada.finalizada_en)}
    ultima = entrevistas[-1] if entrevistas else None
    if ultima is not None:
        espera = {"interrumpida": "Se interrumpió: falta reanudar o reintentar", "parcial": "Quedó parcial: falta reintentar",
                  "programada": "Falta que el candidato realice la entrevista", "en_curso": "En curso con el candidato",
                  "completada": "Red Human está evaluando"}.get(ultima.estado, "")
        return {**base, "estado": "en_curso", "espera": espera}
    if p.videollamada_agendada_en:
        return {**base, "estado": "en_curso", "espera": "Videollamada agendada con el candidato"}
    return {**base, "espera": "Falta agendar la entrevista con el candidato"}


def _aware_iso(texto) -> Optional[datetime]:
    try:
        return _aware(datetime.fromisoformat(texto)) if texto else None
    except (TypeError, ValueError):
        return None


def _paso_solicitud(paso: dict, p: Postulacion) -> dict:
    """La solicitud ES la postulación: terminada en cuanto existe con consentimiento (y con CV si el paso lo pide)."""
    base = {"estado": "pendiente", "resultado": None, "cumple": False, "espera": "", "detalle": "",
            "revisadoPor": "Pendiente de revisión", "evaluacion": None, "terminado_en": None}
    if not p.consentimiento:
        return {**base, "estado": "en_curso", "espera": "Falta el consentimiento del candidato (aviso de privacidad)"}
    if paso.get("con_cv") and not (p.cv_datos or {}):
        return {**base, "estado": "en_curso", "espera": "Falta el CV del candidato"}
    via = {"formulario": "web", "web": "web", "whatsapp": "WhatsApp", "telegram": "Telegram"}.get(p.origen or "", p.origen or "web")
    return {**base, "estado": "completada", "resultado": "favorable", "cumple": True, "detalle": f"Solicitud recibida por {via}",
            "revisadoPor": "Revisado por: Red Human", "terminado_en": _aware(p.consentimiento_fecha or p.creado_en)}


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
    if tipo == "solicitud_documentos":
        return _paso_solicitud_documentos(base, p, exp)
    if tipo == "documentos" and _indice(paso["etapa"]) < _indice("Contratación"):
        return _paso_documentos_previos(base, paso, exp)
    if tipo == "carta_contrato":
        return _paso_carta_contrato(base, p, exp)
    if tipo == "induccion":
        return _paso_induccion(base, p)
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


def _docs_candidato(exp) -> list:
    return [d for d in (exp.documentos if exp is not None else []) if not getattr(d, "interno", False)]


def _bitacora(p: Postulacion, acciones: tuple, entidad: str = "postulacion", entidad_id: str = "") -> list:
    db = object_session(p)
    if db is None:
        return []
    from ..models import Bitacora

    try:
        return (db.query(Bitacora).filter(Bitacora.accion.in_(acciones), Bitacora.entidad == entidad,
                                          Bitacora.entidad_id == (entidad_id or p.codigo)).order_by(Bitacora.id).all())
    except Exception:  # noqa: BLE001
        return []


def _paso_solicitud_documentos(base: dict, p: Postulacion, exp) -> dict:
    """Terminado cuando la liga llegó al candidato (`Documento.solicitado_en`, B3) o cuando él ya subió algo con ella."""
    docs = _docs_candidato(exp)
    solicitados = [d.solicitado_en for d in docs if d.solicitado_en]
    if solicitados or any(d.entregado for d in docs):
        cuando = min(solicitados) if solicitados else None
        canal = next((d.solicitado_canal for d in docs if d.solicitado_canal), "")
        return {**base, "estado": "completada", "resultado": "favorable", "cumple": True,
                "detalle": "Liga enviada" + (f" por {canal}" if canal else "") if solicitados else "El candidato ya subió documentos",
                "revisadoPor": "Revisado por: RH", "terminado_en": _aware(cuando)}
    if _bitacora(p, ("documentos_solicitados",)):
        return {**base, "estado": "en_curso", "espera": "La liga no se pudo entregar: reenvíala o compártela con el candidato"}
    return {**base, "espera": "Falta enviar la liga de documentos al candidato"}


def _paso_documentos_previos(base: dict, paso: dict, exp) -> dict:
    """«Validar documentos» antes de Contratación: valida lo SOLICITADO (o la lista del paso, si RH la definió)."""
    docs = _docs_candidato(exp)
    lista = paso.get("documentos") or []
    revisar = [d for d in docs if d.tipo in lista] if lista else [d for d in docs if d.solicitado_en or d.entregado]
    revisar = [d for d in revisar if d.estado != "no_aplica"]
    if not revisar:
        return {**base, "espera": "Falta solicitar los documentos al candidato"}
    faltan = [d.tipo for d in revisar if not d.aprobado]
    if faltan:
        recibidos = sum(1 for d in revisar if d.entregado)
        return {**base, "estado": "en_curso" if recibidos else "pendiente", "espera": _texto_falta(faltan),
                "detalle": f"{len(revisar) - len(faltan)}/{len(revisar)} aprobados"}
    revisores = sorted({d.revisado_por for d in revisar if d.revisado_por})
    return {**base, "estado": "completada", "resultado": "favorable", "cumple": True, "detalle": f"{len(revisar)}/{len(revisar)} aprobados",
            "revisadoPor": f"Revisado por: {', '.join(revisores)}" if revisores else "Revisado por: RH"}


def _paso_carta_contrato(base: dict, p: Postulacion, exp) -> dict:
    """Terminado con la carta o el contrato FIRMADOS (Dropbox Sign o carga manual) o con la carta enviada al candidato;
    en curso con una firma pendiente o un contrato en borrador."""
    from ..models import TIPO_CARTA_FIRMADA, TIPO_CONTRATO_FIRMADO

    if exp is None:
        return {**base, "espera": "El expediente se abre al llegar a Contratación"}
    firmados = [d for d in (exp.documentos or []) if getattr(d, "interno", False) and d.tipo in (TIPO_CARTA_FIRMADA, TIPO_CONTRATO_FIRMADO)]
    if firmados:
        d = firmados[-1]
        return {**base, "estado": "completada", "resultado": "favorable", "cumple": True, "detalle": f"{d.tipo}",
                "revisadoPor": "Revisado por: RH", "terminado_en": _aware(getattr(d, "recibido_en", None))}
    eventos = _bitacora(p, ("carta_intencion_enviada", "contrato_generado"), "expediente", str(exp.id))
    enviada = [b for b in eventos if b.accion == "carta_intencion_enviada" and (b.detalle or {}).get("enviado")]
    if enviada:
        return {**base, "estado": "completada", "resultado": "favorable", "cumple": True, "detalle": "Carta de intención enviada",
                "revisadoPor": f"Revisado por: {enviada[-1].actor}", "terminado_en": _aware(enviada[-1].ts)}
    db = object_session(p)
    firma = None
    if db is not None:
        try:
            from ..models import FirmaDocumento

            firma = (db.query(FirmaDocumento).filter(FirmaDocumento.expediente_id == exp.id, FirmaDocumento.estado != "cancelada")
                     .order_by(FirmaDocumento.id.desc()).first())
        except Exception:  # noqa: BLE001
            firma = None
    if firma is not None:
        nombre = "carta de intención" if firma.documento == "carta" else "contrato"
        if firma.estado in ("firmada", "descargada"):
            return {**base, "estado": "completada", "resultado": "favorable", "cumple": True, "detalle": f"{nombre.capitalize()} firmado(a)",
                    "revisadoPor": "Revisado por: RH", "terminado_en": _aware(firma.firmada_en)}
        if firma.estado == "error":
            return {**base, "estado": "en_curso", "espera": f"La firma del {nombre} falló: vuelve a mandarla"}
        return {**base, "estado": "en_curso", "espera": f"Falta la firma del {nombre}"}
    if eventos:
        return {**base, "estado": "en_curso", "espera": "Documento generado: falta enviarlo o firmarlo"}
    return {**base, "espera": "Falta generar la carta de intención o el contrato"}


def _paso_induccion(base: dict, p: Postulacion) -> dict:
    """El curso de inducción asignado a la postulación (Capacitación); el curso filtro de la vacante no cuenta."""
    db = object_session(p)
    if db is None:
        return {**base, "espera": "Falta asignar el curso de inducción"}
    try:
        from ..models import AsignacionCurso

        filtro = p.vacante.curso_filtro_id if p.vacante is not None else None
        asignaciones = [a for a in db.query(AsignacionCurso).filter(AsignacionCurso.postulacion_id == p.id).order_by(AsignacionCurso.id).all()
                        if a.curso_id != filtro]
    except Exception:  # noqa: BLE001
        asignaciones = []
    if not asignaciones:
        return {**base, "espera": "Falta asignar el curso de inducción (se asigna al iniciar el Onboarding si la plantilla lo trae)"}
    a = asignaciones[-1]
    titulo = a.curso.titulo if a.curso is not None else "inducción"
    if a.estado == "completado":
        resultado = "no_favorable" if a.aprobado is False else "favorable"
        return {**base, "estado": "completada", "resultado": resultado, "cumple": True,
                "detalle": f"«{titulo}» completado" + (f" · {a.calificacion}%" if a.calificacion is not None else ""),
                "revisadoPor": "Revisado por: Red Human", "terminado_en": _aware(a.completado_en)}
    return {**base, "estado": "en_curso", "espera": f"Falta que termine el curso «{titulo}»"}


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
        return _proveedor_visible(r.get("nombre") or "") or "Externo"
    v = p.vacante
    return (v.responsable.nombre if v is not None and v.responsable else "") or "RH"


def estado_pasos(p: Postulacion, evaluaciones=None, solo_evaluables: bool = False, precarga: Optional[dict] = None) -> List[dict]:
    """Estado DERIVADO de cada paso del proceso congelado de la postulación (lista vacía si no tiene proceso).
    `solo_evaluables`: solo pasos que alimentan la evaluación integral (sin consultar expediente/tareas).
    `precarga` (2026-10-08, tablero sin N+1): lo que el listado ya trajo EN BLOQUE para todas las tarjetas —
    {"envios": [EnvioActividad de ESTA postulación], "eventos_envio": {evaluacion_id: [EventoEvaluacion «envio»]},
    "tareas": [TareaOnboarding], "usuarios": {id: nombre}}. Sin ella, cada dato se consulta como siempre."""
    precarga = precarga or {}
    if not tiene_proceso(p):
        return []
    pasos = p.proceso["pasos"]
    if solo_evaluables:
        pasos = [x for x in pasos if x["tipo"] in TIPOS_PASO_EVALUACION or x["tipo"] in ("analisis_cv", "entrevista_agente")]
    evs = _evaluaciones_de(p, evaluaciones)
    por_paso = asignar_evaluaciones(pasos, evs)
    tareas = None if solo_evaluables else (precarga["tareas"] if "tareas" in precarga else _tareas(p))
    decisiones = p.proceso_estado or {}
    usuarios: Dict[int, str] = {}
    db = object_session(p)
    ids_u = {(x.get("responsable") or {}).get("usuario_id") for x in pasos} - {None}
    if "usuarios" in precarga:
        usuarios = precarga["usuarios"]
    elif ids_u and db is not None and not solo_evaluables:  # el Kanban (solo evaluables) no muestra responsables: sin N+1
        usuarios = {u.id: u.nombre for u in db.query(Usuario).filter(Usuario.id.in_(ids_u)).all()}

    actual = _indice(p.etapa)
    auto = ruta_automatica(p.cuenta)
    disparos = ((p.analisis or {}).get("motor_ruta") or {}).get("envios") or {} if auto else {}
    # 2026-10-08: trazabilidad por destinatario (una consulta por postulación; el Kanban no la necesita)
    from . import envios as senv

    if "envios" in precarga:
        filas_env = precarga["envios"]
    else:
        filas_env = senv.filas_de(db, p) if (db is not None and not solo_evaluables) else None
    legado_env = precarga.get("eventos_envio")
    calculados: Dict[str, dict] = {}
    for paso in pasos:
        tipo = paso["tipo"]
        if tipo in TIPOS_PASO_EVALUACION:
            r = _paso_evaluacion(paso, por_paso.get(paso["id"]))
            if tipo == "psicometrica":
                r["psicometria"] = bloque_psicometria(paso, por_paso.get(paso["id"]))
                if r["psicometria"]["status"] == "error_envio":
                    r["error"] = "Error de envío: la prueba se generó, pero no le llegó al candidato"
                    r["espera"] = r["error"]
        elif tipo in ("prefiltro_whatsapp", "prefiltro_web", "analisis_cv", "entrevista_agente"):
            r = _paso_red_human(paso, p)
        elif tipo == "solicitud_web":
            r = _paso_solicitud(paso, p)
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
        if decision.get("excepcion") and r["estado"] == "completada" and r.get("resultado") == "no_favorable":
            # 2026-10-08: «Continuar por decisión de RH». El resultado reprobatorio y su score se CONSERVAN (la actividad
            # no se omite): solo cuenta como cumplida para la compuerta, con quién, cuándo y por qué.
            d = decision["excepcion"]
            r = {**r, "cumple": True, "excepcion": d, "espera": "",
                 "revisadoPor": f"Continúa por decisión de RH: {d.get('por', '')}"}
        disparo = disparos.get(paso["id"]) or {}
        if r["estado"] == "en_curso" and disparo.get("entregado") is False and not r.get("error"):
            # la actividad se creó (p. ej. la liga de la Entrevista Red Human) pero el aviso no le llegó al candidato
            r = {**r, "error": "La liga no le llegó al candidato: " + (disparo.get("detalle") or ""),
                 "espera": "Error de envío: " + (disparo.get("detalle") or "reenvía la liga")}
        if r["estado"] == "pendiente" and disparo and disparo.get("ok") is False:
            # el motor intentó dispararla sola y no pudo: «Error» con el motivo (RH reintenta con su acción)
            r = {**r, "error": disparo.get("detalle") or "No se pudo enviar automáticamente",
                 "espera": "Error al enviar: " + (disparo.get("detalle") or "intenta de nuevo")}
        r["responsableTexto"] = r.pop("responsable", "") or _texto_responsable(paso, p, usuarios)
        ev_paso = por_paso.get(paso["id"]) if tipo in TIPOS_PASO_EVALUACION else None
        env = None
        if filas_env is not None:
            env = (senv.de_evaluacion(db, ev_paso, filas_env, legado_env) if ev_paso is not None
                   else senv.resumen(filas_env, motivos=MOTIVOS_PASO.get(tipo)) if tipo in MOTIVOS_PASO else {})
            r["envios"] = env
        if not solo_evaluables:
            try:
                r["liga"] = liga_actividad(paso, p, ev_paso, r)
            except Exception:  # noqa: BLE001 — una liga nunca tumba el seguimiento
                r["liga"] = None
        r = _con_cuello(paso, r, ev_paso, env)
        if tipo in TIPOS_PASO_EVALUACION and paso.get("config") and r["estado"] == "pendiente" and (ev_paso is None or ev_paso.estado == "cancelada"):
            r = {**r, "cuello": {"clave": "lista_para_iniciar", "texto": "Lista para iniciar", "quien": None},
                 "espera": r.get("espera") or "Configurada: «Iniciar» la ejecuta tal cual"}
        if tipo == "psicometrica" and falta_correo_psicometria(p, paso["id"], r, ev_paso):
            # 2026-10-08: no es «Error de envío» (eso es una falla HTTP del proveedor): falta un DATO y se resuelve con
            # «Agregar correo»; al guardarlo, el envío pendiente se retoma solo (`actividades.reanudar_por_correo`).
            r = {**r, "error": None, "falta_correo": True, "espera": "Falta el correo del candidato para enviar la prueba",
                 "cuello": {"clave": "falta_correo", "texto": "Falta correo para enviar la prueba", "quien": None}}
        calculados[paso["id"]] = r

    salida = []
    for paso in pasos:
        r = calculados[paso["id"]]
        deps = [calculados[d] for d in paso.get("depende_de", []) if d in calculados]
        deps_listas = all(d["estado"] in ("completada", "omitida", "cancelada") for d in deps)
        faltan_deps = [x["nombre"] for x in pasos if x["id"] in paso.get("depende_de", [])
                       and calculados[x["id"]]["estado"] not in ("completada", "omitida", "cancelada")]
        etapa_alcanzada = _indice(paso["etapa"]) <= actual
        previos_pendientes: List[str] = []
        if auto and not etapa_alcanzada:
            # Ruta automática (2026-10-08): la actividad se habilita cuando las OBLIGATORIAS de las etapas anteriores
            # quedan cumplidas (las de una misma etapa corren en paralelo), no cuando la tarjeta cambia de columna.
            previos_pendientes = [x["nombre"] for x in pasos if x["obligatorio"] and not x.get("heredado")
                                  and _indice(x["etapa"]) < _indice(paso["etapa"]) and not _cumplido(calculados[x["id"]])]
            etapa_alcanzada = not previos_pendientes
        disponible = deps_listas and etapa_alcanzada and r["estado"] in ("pendiente", "en_curso")
        espera = r["espera"]
        if r["estado"] == "pendiente" and not deps_listas:
            espera = f"Falta completar: {', '.join(faltan_deps)}"
        elif r["estado"] == "pendiente" and not etapa_alcanzada:
            espera = (f"Falta completar: {', '.join(previos_pendientes)}" if previos_pendientes
                      else f"Se habilita en {nombre_etapa(paso['etapa'])}")
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
            "pruebas": list(paso.get("pruebas") or []),
            "configurada": bool(paso.get("config")),
            "responsable": r["responsableTexto"], "responsableConfig": paso.get("responsable") or {},
            "estado": r["estado"], "estadoTexto": ESTADOS_PASO[r["estado"]],
            "resultado": r["resultado"], "resultadoTexto": RESULTADOS_PASO.get(r["resultado"] or "", ""),
            "detalle": r.get("detalle", ""), "cumpleRegla": bool(r["cumple"]) and r["estado"] in ("completada", "omitida", "cancelada"),
            "espera": espera, "disponible": disponible, "revisadoPor": r.get("revisadoPor", ""),
            "evaluacion": r.get("evaluacion"), "score": r.get("score"),
            "plazoDias": paso.get("plazo_dias"), "fechaLimite": limite.isoformat() if limite else None, "vencido": vencido,
            "decision": r.get("decision"), "heredado": bool(paso.get("heredado")), "adhoc": bool(paso.get("adhoc")),
            "sincronizable": bool(r.get("sincronizable")),
            "psicometria": r.get("psicometria"),  # solo pasos psicométricos (flujo simple, 2026-10-07)
            "automatica": paso["tipo"] in TIPOS_AUTOMATICOS or (auto and paso["tipo"] in TIPOS_DISPARABLES),
            "liga": r.get("liga"),
            "error": r.get("error"),
            "revisarPrefiltro": bool(r.get("revisar_prefiltro")),
            "excepcionRH": r.get("excepcion"),
            "faltaCorreo": bool(r.get("falta_correo")),
            "referenciasCapturadas": bool(r.get("referencias_capturadas")),
            # 2026-10-08: condición de avance legible («Requiere completarse» / «Requiere aprobación» / «Opcional»),
            # a quién se espera (cuello de botella real) y el estado de cada destinatario (intento/enviado/entregado/fallido)
            "condicion": _condicion(paso), "condicionTexto": CONDICIONES[_condicion(paso)],
            "esperando": {k: v for k, v in (r.get("cuello") or {}).items() if k in ("clave", "texto", "quien")} or None,
            "envios": {d: {k: v for k, v in x.items() if k != "porMotivo"} for d, x in (r.get("envios") or {}).items()},
            **_estado_unificado(r, paso["regla"]),
            "accion": _accion(paso, r, disponible),
            "_terminado_en": r.get("terminado_en"),
        })
        if not solo_evaluables:
            ultimo = salida[-1]
            ev_paso = por_paso.get(paso["id"]) if paso["tipo"] in TIPOS_PASO_EVALUACION else None
            ultimo["reenvios"] = _reenvios(paso, r, ev_paso)
            ultimo["menu"] = _menu(paso, ultimo, r, ev_paso)
    return salida


# ============================================================ cuello de botella, reenvíos y menú (2026-10-08)

# Avisos de la POSTULACIÓN (sin evaluación) que cuentan para cada actividad.
MOTIVOS_PASO = {"entrevista_agente": ("entrevista",), "solicitud_documentos": ("documentos",), "documentos": ("documentos",)}
CONDICIONES = {"opcional": "Opcional", "completarse": "Requiere completarse", "aprobacion": "Requiere aprobación"}
QUE_SE_ENVIA = {"consentimiento": "el consentimiento", "referencias": "la liga para capturar sus referencias",
                "proveedor": "la liga de la prueba", "otro_sistema": "la liga de la prueba", "evaluador": "la liga",
                "entrevista": "la liga de la entrevista", "documentos": "la liga de documentos"}
TEXTO_DESTINATARIO = {"candidato": "al candidato", "medico": "al médico", "entrevistador": "al entrevistador",
                      "evaluador": "al evaluador"}


def _condicion(paso: dict) -> str:
    """Solo detienen el avance las actividades que «requieren completarse» (obligatoria sin regla) o «requieren
    aprobación» (obligatoria con calificación, dictamen o validación). Las opcionales y las omitidas nunca bloquean."""
    if not paso.get("obligatorio") or paso.get("heredado"):
        return "opcional"
    return "completarse" if (paso.get("regla") or {}).get("tipo", "ninguna") == "ninguna" else "aprobacion"


def esperando_referencias(ev: Optional[Evaluacion]) -> bool:
    """Referencias laborales (dos fases): el candidato aún no captura sus contactos → al evaluador no le sale nada."""
    from . import evaluaciones as sev

    return ev is not None and sev.esperando_referencias(ev)


def _cuello_evaluacion(ev: Optional[Evaluacion]) -> Optional[dict]:
    """¿A quién espera una evaluación viva sin resultado? {clave, texto, quien, motivos, que, legado}."""
    if ev is None or ev.estado in ("cancelada", "con_resultado"):
        return None
    if ev.estado == "no_realizada":
        return {"clave": "sin_iniciar", "texto": "No realizada · reprogramar", "quien": None}
    if ev.consentimiento == "pendiente":
        return {"clave": "esperando_consentimiento", "texto": "Esperando consentimiento", "quien": "candidato",
                "motivos": ("consentimiento",), "que": "consentimiento"}
    if esperando_referencias(ev):
        return {"clave": "esperando_referencias", "texto": "Esperando referencias del candidato", "quien": "candidato",
                "motivos": ("referencias",), "que": "referencias"}
    if ev.tipo == "referencias" and ev.referencias_capturadas_en and ev.estado == "pendiente":
        # el candidato ya entregó sus contactos: le toca al responsable revisarlos y registrar el resultado
        return {"clave": "pendiente_revision", "texto": "Pendiente de revisión", "quien": None}
    if ev.estado == "realizada_sin_resultado":
        return {"clave": "pendiente_resultado", "texto": "Esperando resultado", "quien": None}
    iniciada = bool(ev.iniciada_en) or ev.paso_integrada in ("iniciada", "completada")
    if ev.forma in ("integrada", "liga_otro_sistema"):
        if iniciada:
            return {"clave": "en_curso", "texto": "En curso", "quien": None}
        motivos = ("proveedor", "recordatorio") if ev.forma == "integrada" else ("otro_sistema", "aviso", "cita", "recordatorio")
        # simulado (sin plataforma conectada) o registros previos: el envío confirmado vive en `enviada_en`
        return {"clave": "esperando_candidato", "texto": "Esperando candidato", "quien": "candidato", "motivos": motivos,
                "que": "proveedor" if ev.forma == "integrada" else "otro_sistema",
                "legado": bool(ev.enviada_en) or (ev.forma == "integrada" and not ev.clave_proveedor)}
    if ev.forma == "asignada":
        from . import envios as senv

        dest = senv.destinatario_evaluador(ev)
        texto = {"medico": "Esperando médico", "entrevistador": "Esperando entrevistador"}.get(dest, "Esperando evaluador")
        return {"clave": "esperando_evaluador", "texto": texto, "quien": dest, "motivos": None, "que": "evaluador",
                "legado": bool(ev.enviada_en)}
    return {"clave": "pendiente_resultado", "texto": "Pendiente de registrar resultado", "quien": None}


def _con_cuello(paso: dict, r: dict, ev: Optional[Evaluacion], env: Optional[dict]) -> dict:
    """Agrega `cuello` (a quién espera la actividad) y, si el ÚLTIMO envío a esa persona falló, el estado «Error».
    `env` None = no se cargaron los envíos (Kanban): no se degrada nada por envíos."""
    from . import envios as senv

    if r["estado"] in ("omitida", "cancelada", "completada") or r.get("error"):
        return r
    tipo = paso["tipo"]
    cuello = None
    if tipo in TIPOS_PASO_EVALUACION:
        cuello = _cuello_evaluacion(ev)
        if ev is not None and ev.tipo == "referencias" and ev.referencias_capturadas_en:
            r = {**r, "referencias_capturadas": True}
    elif r.get("revisar_prefiltro"):
        cuello = {"clave": "pendiente_revision", "texto": "Pendiente de revisión", "quien": None}
    elif r["estado"] == "en_curso" and tipo in ("entrevista_agente", "solicitud_documentos", "prefiltro_whatsapp", "prefiltro_web", "solicitud_web"):
        motivos = MOTIVOS_PASO.get(tipo)
        cuello = {"clave": "esperando_candidato", "texto": "Esperando candidato", "quien": "candidato",
                  "motivos": motivos, "que": (motivos or ("",))[0], "legado": True}
    if not cuello:
        return r
    r = {**r, "cuello": cuello}
    quien = cuello.get("quien")
    if not quien or env is None:
        return r
    estado = senv.estado_de(env, quien, cuello.get("motivos"))
    que = QUE_SE_ENVIA.get(cuello.get("que") or "", "la liga")
    a_quien = TEXTO_DESTINATARIO.get(quien, "")
    if estado == "fallido":
        canales = ((env.get(quien) or {}).get("canales") or [])
        motivo = "; ".join(f"{c['canal'] or 'sin canal'}: {c['detalle']}" for c in canales if c.get("detalle"))[:240]
        return {**r, "error": f"No se pudo enviar {que} {a_quien}" + (f" ({motivo})" if motivo else ""),
                "espera": f"Error de envío {a_quien}: reenvía o copia la liga"}
    if estado is None and not cuello.get("legado"):
        # a quien debe actuar no le ha salido nada todavía: la actividad no ha empezado (copiar la liga no cuenta)
        return {**r, "cuello": {**cuello, "clave": "sin_iniciar", "texto": "Sin enviar"}, "espera": f"Falta enviar {que} {a_quien}"}
    return r


def _reenvios(paso: dict, r: dict, ev: Optional[Evaluacion]) -> List[dict]:
    """Reenvíos GRANULARES de la actividad (cada uno manda su propia liga; ninguno reinicia el avance)."""
    from . import envios as senv
    from . import evaluaciones as sev

    if r["estado"] in ("omitida", "cancelada"):
        return []
    env = r.get("envios") or {}
    salida: List[dict] = []

    def agregar(a: str, motivo: str):
        ya = bool(env.get(a)) or (a != "candidato" and ev is not None and bool(ev.enviada_en))
        salida.append({"a": a, "motivo": motivo, "texto": f"{'Reenviar' if ya else 'Enviar'} {TEXTO_DESTINATARIO.get(a, '')}".strip()})

    tipo = paso["tipo"]
    if tipo in TIPOS_PASO_EVALUACION:
        if ev is None or ev.estado == "cancelada":
            return []
        abierta = ev.estado in ("pendiente", "realizada_sin_resultado")
        if ev.consentimiento == "pendiente" and ev.consentimiento_token:
            agregar("candidato", "consentimiento")
        elif esperando_referencias(ev):
            agregar("candidato", "referencias")
        elif abierta and ev.forma == "integrada" and ev.clave_proveedor and (sev.estado_proveedor(ev) or ("",))[0] != "completada":
            agregar("candidato", "proveedor")
        elif abierta and ev.forma == "liga_otro_sistema" and ev.liga_externa_candidato:
            agregar("candidato", "otro_sistema")
        elif abierta and ev.cita_fecha_hora:
            agregar("candidato", "cita")
        if (abierta and ev.forma == "asignada" and (ev.evaluador_correo or ev.evaluador_whatsapp)
                and not sev.bloqueo_consentimiento(ev) and not esperando_referencias(ev)):
            agregar(senv.destinatario_evaluador(ev), "evaluador")
        return salida
    if tipo == "entrevista_agente" and r["estado"] == "en_curso" and r.get("liga"):
        agregar("candidato", "entrevista")
    elif tipo in ("solicitud_documentos", "documentos") and r["estado"] in ("en_curso", "pendiente") and r.get("liga"):
        agregar("candidato", "documentos")
    return salida


ETIQUETAS_MENU = {"abrir_liga": "Abrir liga", "copiar_liga": "Copiar liga", "registrar_resultado": "Registrar resultado",
                  "reintentar_sincronizacion": "Reintentar sincronización", "omitir": "Omitir actividad", "reactivar": "Reactivar",
                  "continuar_excepcion": "Continuar por decisión de RH"}


def _menu(paso: dict, x: dict, r: dict, ev: Optional[Evaluacion]) -> List[dict]:
    """Submenú «…» de la actividad con nombres ESTANDARIZADOS y sin duplicados. La acción propia de la actividad va
    primero; `resumen` la quita del menú de la actividad que ya es el botón principal de la ficha."""
    menu: List[dict] = []
    vistos: set = set()

    def poner(clave: str, texto: str = "", **extra):
        if clave in vistos:
            return
        vistos.add(clave)
        menu.append({"clave": clave, "texto": texto or ETIQUETAS_MENU.get(clave, clave), **extra})

    estado = x["estado"]
    if x.get("accion") and estado not in ("omitida", "cancelada"):
        poner("accion", x["accion"]["texto"] if x["tipo"] != "alta" else "Dar de alta", accion=x["accion"])
    if x.get("liga") and x["liga"].get("url"):
        poner("abrir_liga")
        poner("copiar_liga")
    for rv in x.get("reenvios") or []:
        poner(f"reenviar_{rv['a']}", rv["texto"], a=rv["a"])
    if puede_registrar_resultado_paso(paso, estado, ev) and (x.get("accion") or {}).get("clave") != "registrar_resultado":
        poner("registrar_resultado")
    if x.get("sincronizable"):
        poner("reintentar_sincronizacion")
    if (estado == "completada" and x.get("resultado") == "no_favorable" and not x.get("excepcionRH") and not x.get("heredado")):
        poner("continuar_excepcion")
    if not x.get("heredado") and (estado in ("pendiente", "en_curso") or (estado == "completada" and x.get("resultado") == "no_favorable"
                                                                         and not x.get("excepcionRH"))):
        poner("omitir")
    if estado in ("omitida", "cancelada"):
        poner("reactivar")
    return menu


def puede_registrar_resultado_paso(paso: dict, estado: str, ev: Optional[Evaluacion]) -> bool:
    """«Registrar resultado» (captura de algo hecho FUERA del sistema): solo actividades de evaluación sin resultado.
    La médica exige el consentimiento otorgado (LFPDPPP); una evaluación cancelada ya no acepta resultados."""
    if paso["tipo"] not in TIPOS_PASO_EVALUACION or estado in ("omitida", "cancelada", "completada"):
        return False
    if ev is not None and ev.estado in ("cancelada", "con_resultado"):
        return False
    if paso["tipo"] == "medica":
        return ev is not None and ev.consentimiento == "otorgado"
    return True


# Estados VISIBLES unificados — vocabulario ÚNICO (2026-10-08): Sin iniciar · Enviada · En curso · Completada ·
# Aprobada · No aprobada · Omitida · Error. «Completada» = terminó (sin regla, o falta la revisión de RH:
# `pendienteAprobacion`); «Aprobada» = cumplió su condición (calificación mínima, dictamen o validación); «No aprobada»
# = no la cumplió (p. ej. 68/100 con mínimo 70). Omitida y Cancelada se muestran igual («Omitida»). No reemplaza
# `estado`/`resultado` (la compuerta y la evaluación integral siguen leyéndolos).
# 2026-10-08 (trazabilidad por destinatario): «Enviada» dejó de ser un estado; la actividad dice a QUIÉN espera
# (Esperando candidato / referencias / consentimiento / médico·entrevistador·evaluador) o «Pendiente de revisión». El
# detalle de cada envío (intento · enviado · entregado · fallido) viaja aparte en `envios`.
ESTADOS_UNIFICADOS = {
    "sin_iniciar": "Sin iniciar", "esperando_candidato": "Esperando candidato", "esperando_referencias": "Esperando referencias",
    "esperando_consentimiento": "Esperando consentimiento", "esperando_evaluador": "Esperando evaluador",
    "pendiente_resultado": "Pendiente de resultado", "en_curso": "En curso", "pendiente_revision": "Pendiente de revisión",
    "completada": "Completada", "aprobada": "Aprobada", "no_aprobada": "No aprobada", "omitida": "Omitida", "error": "Error",
    # 2026-10-08: no aprobada, pero RH decidió continuar (el resultado reprobatorio se conserva) · falta un dato
    "aprobada_excepcion": "Continúa por decisión de RH", "falta_correo": "Falta correo para enviar la prueba",
    "lista_para_iniciar": "Lista para iniciar",
}
# Las resuelve Red Human solas (el candidato las responde o la IA las califica): la ficha solo muestra su estado.
TIPOS_AUTOMATICOS = ("solicitud_web", "prefiltro_whatsapp", "prefiltro_web", "analisis_cv")
# Ruta automática: el motor las DISPARA solas al habilitarse (services/motor_ruta.py). Las que piden agendar,
# aplicar en persona, revisar o aprobar (entrevistas humanas, médica, referencias, condiciones…) siguen manuales.
TIPOS_DISPARABLES = ("psicometrica", "entrevista_agente", "solicitud_documentos", "referencias")


def _cumplido(r: dict) -> bool:
    return r["estado"] in ("omitida", "cancelada") or (r["estado"] == "completada" and bool(r.get("cumple")))


def _estado_unificado(r: dict, regla: Optional[dict] = None) -> dict:
    estado, resultado = r["estado"], r.get("resultado")
    pendiente_aprobacion = False
    texto = None
    if estado in ("omitida", "cancelada"):
        clave = "omitida"
    elif r.get("excepcion"):
        clave = "aprobada_excepcion"
    elif r.get("falta_correo"):
        clave = "falta_correo"
    elif r.get("error"):
        clave = "error"
    elif resultado == "no_favorable":
        clave = "no_aprobada"
    elif estado == "completada":
        if not r.get("cumple"):
            clave, pendiente_aprobacion = "pendiente_revision", True
        else:
            clave = "aprobada" if (regla or {}).get("tipo", "ninguna") != "ninguna" else "completada"
    elif r.get("cuello"):
        clave, texto = r["cuello"]["clave"], r["cuello"].get("texto")
    elif estado == "en_curso":
        clave = "sin_iniciar" if r.get("sin_iniciar_visible") else "en_curso"
    else:
        clave = "sin_iniciar"
    return {"estadoUnificado": clave, "estadoUnificadoTexto": texto or ESTADOS_UNIFICADOS[clave], "pendienteAprobacion": pendiente_aprobacion}


def siguiente_actividad(p: Postulacion, evaluaciones=None, pasos: Optional[List[dict]] = None) -> Optional[dict]:
    """La actividad que sigue en la ruta (tarjeta del tablero): la primera obligatoria sin cumplir de la etapa actual;
    si no hay, la primera sin cumplir de esa etapa; si la etapa está lista, la primera pendiente de las siguientes.
    `pasos`: el resultado de `estado_pasos` ya calculado (el tablero lo reutiliza para sus alertas)."""
    pasos = [x for x in (pasos if pasos is not None else estado_pasos(p, evaluaciones)) if not x["heredado"]]
    if not pasos:
        return None
    pendientes = [x for x in pasos if not _satisfecho(x)]
    actual = _indice(p.etapa)
    de_etapa = [x for x in pendientes if x["etapa"] == p.etapa]
    x = next((y for y in de_etapa if y["obligatorio"]), None) or (de_etapa[0] if de_etapa else None)
    if x is None:
        x = next((y for y in pendientes if _indice(y["etapa"]) > actual), None)
    if x is None:
        return None
    return {"id": x["id"], "nombre": x["nombre"], "estado": x["estadoUnificado"], "estadoTexto": x["estadoUnificadoTexto"]}


def descarte_sugerido(p: Postulacion, pasos: Optional[List[dict]]) -> Optional[dict]:
    """Ruta automática (2026-10-08): una actividad OBLIGATORIA calificada «No aprobada» (prefiltro con criterio
    excluyente, calificación bajo el mínimo, dictamen desfavorable) deja la postulación en «Descarte sugerido». NUNCA
    descarta sola (LFPDPPP): el funnel se detiene en la compuerta y RH confirma el descarte u omite la actividad."""
    if not pasos or not p.activa or not ruta_automatica(p.cuenta):
        return None
    x = next((y for y in pasos if y["obligatorio"] and not y["heredado"] and y["resultado"] == "no_favorable"
              and y["estado"] not in ("omitida", "cancelada") and not y.get("excepcionRH")), None)
    if x is None:
        return None
    detalle = x.get("detalle") or ""
    motivo = f"{x['nombre']}: {detalle}" if detalle and detalle != x["nombre"] else f"{x['nombre']}: No aprobada"
    return {"paso": x["id"], "nombre": x["nombre"], "motivo": motivo}


def bloqueo_no_aprobada(p: Postulacion, pasos: Optional[List[dict]]) -> Optional[dict]:
    """TODAS las Cuentas (2026-10-08): una obligatoria «No aprobada» (bajo el mínimo, dictamen desfavorable,
    consentimiento rechazado) ya alcanzada por la ruta detiene el avance. RH decide: «Confirmar descarte» o
    «Continuar por decisión de RH» (`excepcion_rh`). Nunca se descarta ni se libera solo.
    2026-10-08: también la postulación que el prefiltro conversacional CERRÓ por un indispensable (`cerradaPorPrefiltro`):
    «Continuar por decisión de RH» la reabre y la ruta sigue sola."""
    from .prefiltro_conversacional import MOTIVO_CIERRE

    cerrada_prefiltro = not p.activa and p.motivo_cierre == MOTIVO_CIERRE
    if not pasos or not (p.activa or cerrada_prefiltro):
        return None
    actual = _indice(p.etapa)
    x = next((y for y in pasos if y["obligatorio"] and not y["heredado"] and y["resultado"] == "no_favorable"
              and y["estado"] == "completada" and not y.get("excepcionRH") and _indice(y["etapa"]) <= actual), None)
    if x is None:
        return None
    detalle = x.get("detalle") or ""
    return {"paso": x["id"], "nombre": x["nombre"], "cerradaPorPrefiltro": cerrada_prefiltro,
            "motivo": f"{x['nombre']}: {detalle}" if detalle and detalle != x["nombre"] else f"{x['nombre']}: No aprobada"}


def falta_correo_psicometria(p: Postulacion, paso_id: str, r: dict, ev: Optional[Evaluacion]) -> bool:
    """La psicometría quiso enviarse (RH o el motor) y no pudo porque el candidato no tiene correo: el proveedor lo exige.
    Se marca en `analisis.psicometria_pendiente_correo` y se apaga sola en cuanto hay correo o una asignación viva."""
    from . import psicometricas as psi

    if r["estado"] in ("omitida", "cancelada", "completada") or (p.correo or "").strip():
        return False
    if ev is not None and ev.estado != "cancelada" and (ev.clave_proveedor or ev.forma != "integrada"):
        return False
    pendientes = (p.analisis or {}).get("psicometria_pendiente_correo") or []
    return paso_id in pendientes and psi.configurado()


def excepcion_rh(db: Session, p: Postulacion, paso_id: str, u, motivo: str) -> dict:
    """«Continuar por decisión de RH» sobre una actividad «No aprobada»: la libera para la compuerta SIN cambiar su
    resultado ni su score y SIN omitirla. Motivo obligatorio; en una obligatoria exige el permiso «Autorizar omisiones».
    No hace commit."""
    paso = _paso(p, paso_id)
    motivo = (motivo or "").strip()
    actual = next((x for x in estado_pasos(p) if x["id"] == paso_id), None)
    if actual is None or actual["resultado"] != "no_favorable" or actual["estado"] != "completada":
        raise ErrorProceso(409, f"«{paso['nombre']}» no está «No aprobada»: no hay nada que liberar.")
    if actual.get("excepcionRH"):
        raise ErrorProceso(409, f"«{paso['nombre']}» ya continúa por decisión de RH.")
    if len(motivo) < MOTIVO_MINIMO:
        raise ErrorProceso(400, f"Escribe el motivo de la decisión (al menos {MOTIVO_MINIMO} caracteres).")
    if paso["obligatorio"] and not u.puede_autorizar_omisiones():
        raise ErrorProceso(403, f"«{paso['nombre']}» es obligatoria: continuar pese al resultado requiere el permiso «Autorizar omisiones».")
    sello = _ahora()
    decisiones = dict(p.proceso_estado or {})
    d = dict(decisiones.get(paso_id) or {})
    d["excepcion"] = {"por": u.nombre, "motivo": motivo[:500], "fecha": sello.isoformat(), "obligatorio": paso["obligatorio"],
                      "resultado": actual.get("resultadoTexto") or "No favorable", "score": actual.get("score"),
                      "detalle": actual.get("detalle") or ""}
    decisiones[paso_id] = d
    p.proceso_estado = decisiones
    p.historial = list(p.historial or []) + [{
        "evento": "paso_excepcion_rh", "usuario": u.nombre, "fecha": sello.isoformat(), "paso": paso_id, "motivo": motivo[:500],
        "texto": f"«{paso['nombre']}» ({actual.get('detalle') or 'No aprobada'}) continúa por decisión de RH ({u.nombre}): {motivo}",
    }]
    registrar(db, u.nombre, "proceso_excepcion_rh", "postulacion", p.codigo,
              {"paso": paso_id, "nombre": paso["nombre"], "resultado": actual.get("detalle"), "score": actual.get("score"),
               "motivo": motivo[:500], "correo_rh": getattr(u, "correo", "")})
    return d["excepcion"]


def alerta_psicometria(pasos: List[dict], etapa: str) -> Optional[str]:
    """Tablero (flujo simple de psicometría): «sin_enviar» si una psicométrica OBLIGATORIA vigente de la etapa actual (o
    de una anterior) sigue sin enviarse; «sin_respuesta» si se envió y pasó el umbral sin resultado. None si no aplica.
    Las de etapas futuras no alertan: todavía no toca enviarlas."""
    actual = _indice(etapa)
    vivos = [x for x in pasos if x["tipo"] == "psicometrica" and x["obligatorio"] and not x["heredado"]
             and _indice(x["etapa"]) <= actual
             and x["estado"] not in ("omitida", "cancelada") and x.get("psicometria")]
    if any(x.get("faltaCorreo") for x in vivos):
        return "falta_correo"  # 2026-10-08: falta un dato (correo), no es una falla de envío
    if any(x["psicometria"]["status"] == "error_envio" for x in vivos):
        return "error_envio"  # 2026-10-08: generada pero la liga no le llegó al candidato
    if any(x["psicometria"]["status"] == "sin_enviar" for x in vivos):
        return "sin_enviar"
    if any(x["psicometria"]["status"] == "enviada" and x["psicometria"].get("dias_sin_respuesta") for x in vivos):
        return "sin_respuesta"
    return None


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
        if r.get("falta_correo"):
            return {"clave": "agregar_correo", "texto": "Agregar correo"}
        cuello = r.get("cuello") or {}
        if tipo == "referencias" and cuello.get("que") == "referencias" and (cuello.get("clave") == "sin_iniciar" or r.get("error")):
            # la actividad existe pero al candidato no le ha llegado su liga: «Solicitar referencias» (envía ESA liga)
            return {"clave": "reenviar", "a": "candidato", "texto": "Solicitar referencias", "urgente": True}
        if r.get("referencias_capturadas") and estado == "en_curso":
            return {"clave": "registrar_resultado", "texto": "Registrar revisión de referencias", "urgente": True}
        if r.get("evaluacion") and estado != "pendiente":
            return {"clave": "consultar_evaluacion", "texto": "Consultar", "evaluacion": r["evaluacion"]}
        if disponible:
            # psicometría: abre la vista limpia con la batería de la ruta/vacante y «Asignar y enviar»
            texto = {"psicometrica": "Asignar y enviar", "referencias": "Solicitar referencias"}.get(tipo, "Iniciar")
            return {"clave": "iniciar_evaluacion", "texto": texto}
        return None
    destino = {"prefiltro_whatsapp": "whatsapp", "prefiltro_web": "documentos", "analisis_cv": "documentos",
               "entrevista_agente": "evaluaciones", "documentos": "contratacion", "condiciones": "contratacion",
               "onboarding": "contratacion", "alta": "contratacion", "solicitud_web": "resumen",
               "solicitud_documentos": "contratacion", "carta_contrato": "contratacion", "induccion": "contratacion"}[tipo]
    previo = _indice(paso["etapa"]) < _indice("Contratación")
    if tipo in ("documentos", "solicitud_documentos") and previo:
        destino = "documentos"  # antes de Contratación los documentos se ven y validan en «CV y documentos»
    if tipo == "solicitud_documentos" and disponible and estado != "completada":
        # la misma acción ejecuta y refresca la vista: el estado, el resultado y la siguiente acción se recalculan
        return {"clave": "solicitar_documentos", "texto": "Enviar liga de documentos" if estado == "pendiente" else "Reenviar liga"}
    if tipo == "documentos" and previo and disponible and estado != "completada":
        return {"clave": "validar_documentos", "texto": "Validar documentos", "pestana": "documentos"}
    if estado == "completada" or not disponible:
        return {"clave": "consultar", "texto": "Consultar", "pestana": destino} if estado != "pendiente" else None
    texto = {"documentos": "Solicitar documentos", "condiciones": "Capturar condiciones", "alta": "Dar de alta",
             "onboarding": "Ver tareas", "carta_contrato": "Generar carta / contrato", "induccion": "Ver inducción"}.get(tipo, "Consultar")
    return {"clave": "abrir", "texto": texto, "pestana": destino}


# ============================================================ etapas: compuerta, siguiente etapa, resumen

def _satisfecho(paso: dict) -> bool:
    return paso["estado"] in ("omitida", "cancelada") or (paso["estado"] == "completada" and paso["cumpleRegla"])


def desde_compuerta(p: Postulacion) -> str:
    """Desde qué etapa revisa la compuerta (2026-10-08): TODAS las actividades previas en paralelo, no solo las de la
    etapa actual — a partir de la etapa en la que se congeló la ruta (`etapa_base`). Rutas anteriores a este cambio no
    traen `etapa_base`: conservan la revisión de la etapa actual (retrocompatibilidad: nada que ya avanzó se bloquea)."""
    base = (p.proceso or {}).get("etapa_base")
    if base in ETAPAS_CANDIDATO and _indice(base) < _indice(p.etapa):
        return base
    return p.etapa


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
    falta_actual = faltantes(pasos, desde_compuerta(p), sig) if sig else []
    bloqueo = bloqueo_no_aprobada(p, pasos)
    accion_principal = _siguiente_accion(p, pasos, sig, falta_actual)
    onboarding = accion_onboarding(p)
    if onboarding is not None:
        accion_principal = onboarding  # 2026-10-08: en Onboarding manda la tarea pendiente (o el alta / cierre)
    if bloqueo:
        accion_principal = {"tipo": "bloqueo", "paso": bloqueo["paso"], "texto": f"Decisión de RH: {bloqueo['nombre']}",
                            "detalle": bloqueo["motivo"]}
    if accion_principal.get("paso") and accion_principal.get("accion"):
        for x in pasos:  # UNA acción principal visible: la del paso principal no se duplica en su «…»
            if x["id"] == accion_principal["paso"] and (accion_principal["tipo"] == "paso" or x.get("pendienteAprobacion")):
                x["menu"] = [m for m in x.get("menu") or [] if m["clave"] != "accion"]
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
        # ruta automática (2026-10-08): el motor dispara y avanza solo; el descarte SIEMPRE lo confirma RH
        "rutaAutomatica": ruta_automatica(p.cuenta),
        "descarteSugerido": descarte_sugerido(p, pasos),
        # 2026-10-08 (todas las Cuentas): una obligatoria «No aprobada» detiene la ruta → «Confirmar descarte» o
        # «Continuar por decisión de RH». La recomendación de la ficha sale de ESTE mismo cálculo.
        "bloqueo": bloqueo,
        "recomendacion": _recomendacion_ruta(bloqueo, accion_principal, p),
        "plantilla": p.proceso.get("plantilla_nombre") or "", "origen": p.proceso.get("origen") or "vacante",
        "personalizado": bool(p.proceso.get("personalizado")),
        "version": int(p.proceso.get("vacante_version") or p.proceso.get("version") or 1),
        "desactualizado": desactualizado(p),
        "etapaActual": p.etapa, "etapaTexto": nombre_etapa(p.etapa),
        "siguienteEtapa": sig, "siguienteEtapaTexto": nombre_etapa(sig) if sig else None,
        "listaParaAvanzar": bool(sig) and not falta_actual,
        "siguienteAccion": accion_principal,
        "alertas": [{"paso": x["id"], "texto": f"Plazo vencido: {x['nombre']}", "fechaLimite": x["fechaLimite"]}
                    for x in pasos if x["vencido"]],
        "etapas": etapas,
    }


def _recomendacion_ruta(bloqueo: Optional[dict], accion: dict, p: Optional[Postulacion] = None) -> Optional[dict]:
    """Recomendación derivada de la RUTA (misma fuente que la acción principal y los avisos). None = la ficha muestra
    la recomendación de Red Human (CV + entrevista). 2026-10-08: en Contratación y Onboarding la recomendación es SIEMPRE
    el pendiente actual (nunca «Avanzar a contratación» de la entrevista humana, que ya quedó atrás)."""
    if bloqueo:
        return {"texto": "Decisión de RH pendiente", "motivo": f"{bloqueo['motivo']}. Confirma el descarte o continúa por decisión de RH.",
                "tono": "bad"}
    if accion.get("accion", {}) and (accion.get("accion") or {}).get("clave") == "agregar_correo":
        return {"texto": "Falta un dato para continuar", "motivo": "Agrega el correo del candidato: la prueba se envía sola al guardarlo.",
                "tono": "warn"}
    if p is None or p.etapa not in ("Contratación", "Onboarding"):
        return None
    tipo = accion.get("tipo")
    if tipo == "tarea":
        return {"texto": accion["detalle"], "motivo": f"Resuélvelo aquí mismo con «{accion['texto']}».", "tono": "warn"}
    if tipo == "alta":
        return {"texto": "Lista para dar de alta", "motivo": accion.get("detalle") or "", "tono": "good"}
    if tipo == "cerrar_onboarding":
        return {"texto": "Colaborador dado de alta", "motivo": "Cierra el Onboarding para terminar el proceso.", "tono": "good"}
    if tipo == "fin":
        return {"texto": accion.get("texto") or "Proceso completo", "motivo": accion.get("detalle") or "", "tono": "good"}
    if tipo in ("paso", "abrir", "avanzar", "esperar") and accion.get("texto"):
        return {"texto": accion["texto"], "motivo": accion.get("detalle") or "", "tono": "warn" if tipo == "esperar" else "good"}
    return None


def accion_onboarding(p: Postulacion) -> Optional[dict]:
    """Acción principal en Onboarding (2026-10-08), resuelta DESDE LA FICHA: la primera tarea obligatoria pendiente
    (Confirmar ingreso, Registrar alta IMSS / nómina, contrato…) → con todo resuelto, «Dar de alta como colaborador»
    sobre el MISMO expediente → con el alta hecha, «Cerrar Onboarding». None fuera de Onboarding o sin tareas."""
    exp = p.expediente
    db = object_session(p)
    if p.etapa != "Onboarding" or exp is None or db is None:
        return None
    from . import onboarding as onb

    try:
        tareas = onb.tareas_de(db, exp)
    except Exception:  # noqa: BLE001 — sin tablas de módulos la ruta sigue como antes
        return None
    if exp.estado == "alta":
        if exp.onboarding_cerrado_en:
            return {"tipo": "fin", "texto": "Proceso completo", "detalle": f"Colaborador dado de alta y Onboarding cerrado por {exp.onboarding_cerrado_por}."}
        faltan = onb.pendientes_cierre(exp, tareas)
        if faltan:
            return {"tipo": "esperar", "texto": "Cerrar Onboarding", "detalle": "; ".join(faltan[:3])}
        return {"tipo": "cerrar_onboarding", "texto": "Cerrar Onboarding", "detalle": "Alta registrada y todas las tareas resueltas."}
    if not p.activa or not tareas:
        return None
    pendientes = onb.pendientes_obligatorias(tareas)
    if pendientes:
        t = pendientes[0]
        a = onb.accion_tarea(t)
        return {"tipo": "tarea", "tarea": t.id, "clave": t.clave, "texto": a["texto"], "detalle": a["falta"], "accionTarea": a}
    if exp.no_aprobados:
        return {"tipo": "esperar", "texto": "Documentos por aprobar", "detalle": _texto_falta(exp.no_aprobados)}
    return {"tipo": "alta", "texto": "Dar de alta como colaborador",
            "detalle": "Todas las tareas obligatorias de Onboarding están resueltas (realizadas u omitidas con autorización)."}


def _siguiente_accion(p: Postulacion, pasos: List[dict], sig: Optional[str], falta: List[dict]) -> dict:
    if not p.activa:
        return {"tipo": "cerrada", "texto": "Postulación cerrada", "detalle": "Mover de etapa la reabre."}
    de_etapa = [x for x in pasos if x["etapa"] == p.etapa]
    # 2026-10-08: si lo que detiene a la ruta es un DATO concreto (correo, referencias por revisar), el botón principal
    # es ESA acción — nunca un «Registrar resultado» genérico.
    urgentes = [x for x in pasos if x.get("accion") and x["accion"]["clave"] in ("agregar_correo",) + (("registrar_resultado", "reenviar") if x["accion"].get("urgente") else ())
                and x["estado"] not in ("omitida", "cancelada", "completada") and _indice(x["etapa"]) <= _indice(p.etapa)]
    if urgentes:
        x = next((y for y in urgentes if y["obligatorio"]), urgentes[0])
        return {"tipo": "paso", "paso": x["id"], "texto": f"{x['accion']['texto']}: {x['nombre']}" if x["accion"]["clave"] != "agregar_correo"
                else "Agregar correo", "accion": x["accion"], "detalle": x["espera"]}
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
    falta = faltantes(estado_pasos(p), desde_compuerta(p), hacia)
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
    nuevo["origen"] = "vacante"
    nuevo["congelado_en"] = _ahora().isoformat()
    nuevo["etapa_base"] = p.etapa  # lo nuevo de etapas ya rebasadas no bloquea hacia atrás
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
    if paso and (p.proceso or {}).get("origen") in ("cuenta", "base") and not paso.get("adhoc"):
        paso = None  # ruta de la Cuenta o de respaldo: el enfoque lo decide la vacante (lo que RH eligió al crearla)
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


def documentos_anticipados(p: Postulacion) -> bool:
    """¿La ruta pide documentos ANTES de Contratación en una etapa que ya alcanzó (Masivos)? Entonces «Solicitar
    documentos por liga» abre el expediente con anticipación."""
    actual = _indice(p.etapa)
    return any(x["tipo"] in ("solicitud_documentos", "documentos") and _indice(x["etapa"]) < _indice("Contratación")
               and _indice(x["etapa"]) <= actual for x in (p.proceso or {}).get("pasos", []))


def mueve_entrevista_ia(p: Postulacion) -> bool:
    """¿Entrar a Filtro Red Human debe iniciar la agenda de la Entrevista Red Human? Siempre sin proceso; con proceso,
    solo si el proceso la incluye."""
    return not tiene_proceso(p) or paso_de_tipo(p, "entrevista_agente") is not None


def etapa_lista(p: Postulacion) -> Tuple[Optional[str], List[dict]]:
    sig = siguiente_etapa(p)
    if not sig:
        return None, []
    return sig, faltantes(estado_pasos(p), desde_compuerta(p), sig)


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
        encendido = cfg.get(p.etapa, {}).get("avance_automatico") or ruta_automatica(p.cuenta)
        if p.etapa in ETAPAS_SIN_AVANCE_AUTOMATICO or not encendido:
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
        if ruta_automatica(p.cuenta):
            from . import motor_ruta

            return await motor_ruta.procesar(db, p)
        return await avanzar_si_corresponde(db, p)
    except Exception as ex:  # noqa: BLE001
        # Sin rollback: la acción que lo disparó (un turno del prefiltro, un resultado…) puede traer cambios sin guardar.
        try:
            registrar(db, "sistema", "avance_automatico_error", "postulacion", p.codigo, {"error": str(ex)[:300]})
        except Exception:  # noqa: BLE001
            pass
        return []


# ============================================================ actividades ad hoc (solo ESTA postulación)

def agregar_paso_adhoc(db: Session, p: Postulacion, datos: dict, u, registrar_evento: bool = True) -> dict:
    """Agrega una actividad extra (entrevista, prueba, documento…) SOLO a esta postulación: se suma a su copia del
    proceso y nunca toca la plantilla ni la vacante. Por defecto NO es obligatoria (no frena el avance) y va en la etapa
    actual si el tipo lo permite. No hace commit. Regresa el paso normalizado."""
    tipo = str((datos or {}).get("tipo") or "").strip()
    if tipo not in TIPOS_PASO:
        raise ErrorProceso(400, f"Tipo de actividad inválido: «{tipo}».")
    if not tiene_proceso(p):
        congelar(p, db=db)
    # 2026-10-08: la actividad adicional va en la etapa ACTUAL (o una posterior si RH la elige). Nunca en una anterior:
    # agregarla jamás regresa ni detiene al candidato en una etapa que ya rebasó.
    etapa = datos.get("etapa") or p.etapa
    if etapa not in ETAPAS_CANDIDATO or _indice(etapa) < _indice(p.etapa):
        etapa = p.etapa
    previos = list(p.proceso.get("pasos") or [])
    ids = {x["id"] for x in previos}
    crudo = {k: v for k, v in datos.items() if k not in ("id", "etapa", "obligatorio", "adhoc")}
    crudo.update({"id": f"extra-{tipo.replace('_', '-')}", "etapa": etapa, "obligatorio": bool(datos.get("obligatorio", False)),
                  "adhoc": True})
    pasos = normalizar_pasos([*previos, crudo])
    nuevo = next(x for x in pasos if x["id"] not in ids)
    proc = dict(p.proceso)
    proc["pasos"] = pasos
    p.proceso = proc
    if registrar_evento:
        sello = _ahora()
        quien = getattr(u, "nombre", "") or "sistema"
        p.historial = list(p.historial or []) + [{
            "evento": "actividad_adhoc", "usuario": quien, "fecha": sello.isoformat(), "paso": nuevo["id"],
            "texto": f"Actividad agregada solo a este candidato: «{nuevo['nombre']}» ({nombre_etapa(etapa)}) por {quien}",
        }]
        registrar(db, quien, "proceso_actividad_adhoc", "postulacion", p.codigo,
                  {"paso": nuevo["id"], "tipo": tipo, "etapa": etapa, "obligatorio": nuevo["obligatorio"], "correo_rh": getattr(u, "correo", "")})
    return nuevo


def paso_adhoc_para_evaluacion(db: Session, p: Postulacion, ev: Evaluacion, u) -> Optional[dict]:
    """«Agregar evaluación» fuera de la ruta: la evaluación se vuelve una actividad ad hoc de la postulación (visible en
    el seguimiento con su estado y resultado) y queda ligada a ella por `paso_id`."""
    if ev.tipo not in TIPOS_PASO_EVALUACION:
        return None
    if ev.paso_id and any(x["id"] == ev.paso_id for x in (p.proceso or {}).get("pasos", [])):
        return None
    paso = agregar_paso_adhoc(db, p, {"tipo": ev.tipo, "nombre": ev.nombre_visible}, u)
    ev.paso_id = paso["id"]
    return paso


# ============================================================ migración: ninguna postulación sin ruta

def asignar_rutas_faltantes(db: Session, aplicar: bool = True, por: str = "migración rutas 2026-10-06") -> dict:
    """Asigna la ruta (cascada de `ruta_para`) a TODA postulación que no tenga una — activas y cerradas. Solo escribe
    `Postulacion.proceso` y una nota en su historial: NO cambia la etapa, NO toca resultados, documentos ni evaluaciones,
    NO manda mensajes ni dispara el avance automático. El estado de cada paso NO se guarda: se deriva de los registros
    reales (prefiltro, CV, entrevistas, evaluaciones, expediente, tareas, cursos), así que nada aparece «Completado» solo
    por la etapa en la que está el candidato. Idempotente. `aplicar=False` = solo contar. No hace commit."""
    resumen: dict = {"revisadas": 0, "sin_ruta": 0, "asignadas": 0, "por_origen": {}, "por_cuenta": {},
                     "pasos": {"completada": 0, "en_curso": 0, "pendiente": 0, "omitida": 0, "cancelada": 0}}
    sello = _ahora()
    for p in db.query(Postulacion).order_by(Postulacion.id).all():
        resumen["revisadas"] += 1
        if tiene_proceso(p):
            continue
        resumen["sin_ruta"] += 1
        proc = ruta_para(db, p.cuenta_id, p.vacante)
        resumen["por_origen"][proc["origen"]] = resumen["por_origen"].get(proc["origen"], 0) + 1
        clave = str(p.cuenta_id)
        resumen["por_cuenta"][clave] = resumen["por_cuenta"].get(clave, 0) + 1
        if not aplicar:
            continue
        proc.update({"congelado_en": sello.isoformat(), "migrado_en": sello.isoformat()})
        p.proceso = proc
        p.historial = list(p.historial or []) + [{
            "evento": "ruta_asignada", "usuario": por, "fecha": sello.isoformat(),
            "texto": f"Ruta asignada: «{proc.get('plantilla_nombre') or 'Proceso de la vacante'}» (origen: {proc['origen']}). "
                     "Se conservan la etapa, los resultados, los documentos y el historial.",
        }]
        resumen["asignadas"] += 1
        try:
            for x in estado_pasos(p):
                resumen["pasos"][x["estado"]] = resumen["pasos"].get(x["estado"], 0) + 1
        except Exception:  # noqa: BLE001 — el conteo es informativo
            pass
    if aplicar and resumen["asignadas"]:
        registrar(db, por, "rutas_asignadas", "postulacion", "migracion",
                  {k: resumen[k] for k in ("asignadas", "por_origen", "por_cuenta", "pasos")})
    return resumen


def postulaciones_sin_ruta(db: Session) -> int:
    return sum(1 for p in db.query(Postulacion).all() if not tiene_proceso(p))
