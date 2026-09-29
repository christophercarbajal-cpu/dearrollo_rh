"""Fase F — agente global "Pregunta a Red Human" (punto 29).

Principio de arquitectura (no negociable, aprobado en plan): las "tools" que ve el modelo son
wrappers DELGADOS sobre las MISMAS funciones de los routers ya existentes, invocadas en
proceso (nunca por HTTP, nunca con una segunda lógica de negocio ni una base de datos
paralela). `@router.get/post/patch` no envuelve la función — el decorador la registra pero
regresa el mismo objeto Python, así que se puede llamar directo pasándole `db`/`u`/`cuenta`
ya resueltos en vez de dejar que FastAPI los inyecte.

Separación dura lectura/escritura: el modelo solo puede EJECUTAR tools de lectura (sin efectos
secundarios). Las tools de escritura tienen su propio schema (para que el modelo arme bien los
argumentos), pero cuando el modelo las invoca, este módulo NUNCA las ejecuta ahí mismo — corta
el loop, arma un resumen determinista (Python, no texto libre del modelo) y lo regresa como
`accion_propuesta`. Solo `ejecutar_accion()` — llamada por un endpoint aparte, disparada por un
clic real de confirmación del usuario — ejecuta de verdad.
"""

import inspect
import json
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Candidato, Cuenta, Postulacion, Usuario, UsuarioCuenta, UsoAgente, Vacante
from ..routers import auth as r_auth
from ..routers import candidatos as r_candidatos
from ..routers import capacitacion as r_capacitacion
from ..routers import clientes as r_clientes
from ..routers import colaboradores as r_colaboradores
from ..routers import contratacion as r_contratacion
from ..routers import entrevistas as r_entrevistas
from ..routers import metricas as r_metricas
from ..routers import notificaciones as r_notificaciones
from ..routers import requisiciones as r_requisiciones
from ..routers import vacantes as r_vacantes
from ..routers import webhooks as r_webhooks

MODEL = settings.openai_model
from ..fechas import TZ_ORG as TZ_MEXICO  # zona de la organización (app/fechas.py)
MUESTRA_MAXIMA = 10  # Q4: conteo total + muestra de 8-10 + navegación, nunca una tabla completa
MAX_RONDAS = 6  # tope duro de vueltas de function-calling por pregunta


def _client():
    if not settings.openai_api_key:
        return None
    from openai import OpenAI

    return OpenAI(api_key=settings.openai_api_key)


def ia_activa() -> bool:
    return bool(settings.openai_api_key)


async def _llamar(fn: Callable, *args, **kwargs):
    """La mitad de las funciones de router son `async def` (las que mandan notificaciones) y
    la otra mitad no — se detecta y se espera solo cuando hace falta, sin tener que llevar la
    cuenta manualmente de cuáles son cuáles."""
    resultado = fn(*args, **kwargs)
    if inspect.isawaitable(resultado):
        resultado = await resultado
    return resultado


# ============================================================
# Límite diario de mensajes (Q7) — solo cuenta, nunca guarda texto (Q6)
# ============================================================


def verificar_uso(db: Session, u: Usuario) -> Dict[str, int]:
    """Regresa {mensajesHoy, limite}. NO incrementa — se llama antes de gastar una llamada a
    OpenAI, para poder responder 429 sin siquiera intentar la pregunta."""
    hoy = date.today()
    fila = db.query(UsoAgente).filter(UsoAgente.usuario_id == u.id, UsoAgente.fecha == hoy).first()
    return {"mensajesHoy": fila.mensajes if fila else 0, "limite": settings.agente_limite_mensajes_dia}


def _incrementar_uso(db: Session, u: Usuario) -> int:
    hoy = date.today()
    fila = db.query(UsoAgente).filter(UsoAgente.usuario_id == u.id, UsoAgente.fecha == hoy).first()
    if not fila:
        fila = UsoAgente(usuario_id=u.id, fecha=hoy, mensajes=0)
        db.add(fila)
        db.flush()
    fila.mensajes += 1
    db.commit()
    return fila.mensajes


# ============================================================
# Alcance de Cuenta(s) — Q2: "todas mis Cuentas" nunca cruza a una Cuenta ajena
# ============================================================


def cuentas_de_usuario(db: Session, u: Usuario) -> List[Cuenta]:
    return (
        db.query(Cuenta)
        .join(UsuarioCuenta, UsuarioCuenta.cuenta_id == Cuenta.id)
        .filter(UsuarioCuenta.usuario_id == u.id, Cuenta.estado == "Activa")
        .order_by(Cuenta.id)
        .all()
    )


def _resolver_alcance(db: Session, u: Usuario, cuenta_actual: Cuenta, alcance: str) -> List[Cuenta]:
    if alcance == "todas_mis_cuentas":
        cuentas = cuentas_de_usuario(db, u)
        return cuentas or [cuenta_actual]
    return [cuenta_actual]


# ============================================================
# Helpers de resumen (nombres legibles para las tarjetas de confirmación — Q5/arquitectura)
# ============================================================


def _nombre_candidato(db: Session, cuenta_id: int, codigo: str) -> str:
    """Fase 2: el agente maneja códigos de POSTULACIÓN (P-####, lo que ve en el Kanban); por
    compatibilidad también resuelve el de la persona (C-####)."""
    if codigo.startswith("P-"):
        p = db.query(Postulacion).filter(Postulacion.codigo == codigo, Postulacion.cuenta_id == cuenta_id).first()
        return f"{p.nombre} ({p.vacante.titulo})" if p and p.vacante else (p.nombre if p else codigo)
    c = db.query(Candidato).filter(Candidato.codigo == codigo, Candidato.cuenta_id == cuenta_id).first()
    return c.nombre if c else codigo


def _titulo_vacante(db: Session, cuenta_id: int, codigo: str) -> str:
    v = db.query(Vacante).filter(Vacante.codigo == codigo, Vacante.cuenta_id == cuenta_id).first()
    return v.titulo if v else codigo


def _candidato_de_expediente(db: Session, cuenta_id: int, exp_id: int) -> str:
    try:
        e = r_contratacion._expediente(db, exp_id, cuenta_id)
    except HTTPException:
        return f"expediente {exp_id}"
    return e.candidato.nombre if e.candidato else f"expediente {exp_id}"


def _resultado_lista(items: List[dict]) -> dict:
    """Q4: nunca vuelca una tabla completa al modelo — conteo total + muestra acotada."""
    return {"total": len(items), "muestra": items[:MUESTRA_MAXIMA], "hayMas": len(items) > MUESTRA_MAXIMA}


# ============================================================
# Catálogo de LECTURA — se ejecutan directo, cero efectos secundarios
# ============================================================


def _leer_buscar_vacantes(db, cuentas, u, **kw):
    items = []
    for cuenta in cuentas:
        items.extend(r_vacantes.listar(db=db, _=u, cuenta=cuenta, **kw))
    return _resultado_lista(items)


def _leer_detalle_vacante(db, cuentas, u, codigo):
    return r_vacantes.detalle(codigo, db=db, _=u, cuenta=cuentas[0])


def _si_no(kw: dict, clave: str) -> None:
    """Traduce un filtro tri-estado 'si'/'no' (o ausente) a True/False/ausente. Los booleanos
    JSON puros son poco confiables aquí: algunos modelos rellenan `false` en un parámetro
    opcional que el usuario nunca pidió, y ese `false` sí filtra de verdad (ver Fase F,
    hallazgo de la pasada manual con el modelo real) — con un enum de string, "ausente" tiene
    una representación explícita que el modelo respeta mucho mejor."""
    if clave in kw:
        kw[clave] = kw.pop(clave) == "si"


def _leer_buscar_candidatos(db, cuentas, u, **kw):
    for clave in ("consentimiento", "apto", "duplicados"):
        _si_no(kw, clave)
    items = []
    for cuenta in cuentas:
        items.extend(r_candidatos.listar(db=db, _=u, cuenta=cuenta, **kw))
    return _resultado_lista(items)


def _leer_resumen_candidato(db, cuentas, u, codigo):
    return r_candidatos.detalle(codigo, db=db, _=u, cuenta=cuentas[0])


def _leer_mensajes_candidato(db, cuentas, u, codigo):
    return r_candidatos.mensajes(codigo, db=db, _=u, cuenta=cuentas[0])


def _leer_pipeline_cuenta(db, cuentas, u):
    # fuente única de conteos (mismo endpoint que alimenta el Tablero) — se agrega si el
    # alcance pide varias Cuentas, nunca se recalcula con una query propia.
    resultados = [r_metricas.pipeline(db=db, cuenta=cuenta) for cuenta in cuentas]
    return resultados[0] if len(resultados) == 1 else {"porCuenta": resultados}


def _leer_metricas_entrevistas(db, cuentas, u):
    resultados = [r_entrevistas.metricas(db=db, _=u, cuenta=cuenta) for cuenta in cuentas]
    return resultados[0] if len(resultados) == 1 else {"porCuenta": resultados}


def _leer_listar_expedientes(db, cuentas, u, **kw):
    items = []
    for cuenta in cuentas:
        items.extend(r_contratacion.listar(db=db, _=u, cuenta=cuenta, **kw))
    return _resultado_lista(items)


def _leer_detalle_expediente(db, cuentas, u, exp_id):
    return r_contratacion.detalle(exp_id, db=db, _=u, cuenta=cuentas[0])


def _leer_metricas_contratacion(db, cuentas, u):
    resultados = [r_contratacion.metricas(db=db, _=u, cuenta=cuenta) for cuenta in cuentas]
    return resultados[0] if len(resultados) == 1 else {"porCuenta": resultados}


def _leer_buscar_requisiciones(db, cuentas, u, **kw):
    items = []
    for cuenta in cuentas:
        items.extend(r_requisiciones.listar(db=db, _=u, cuenta=cuenta, **kw))
    return _resultado_lista(items)


def _leer_detalle_requisicion(db, cuentas, u, codigo):
    return r_requisiciones.detalle(codigo, db=db, _=u, cuenta=cuentas[0])


def _leer_sugerencias_movilidad(db, cuentas, u, codigo):
    return r_requisiciones.sugerencias(codigo, db=db, _=u, cuenta=cuentas[0])


def _leer_listar_colaboradores(db, cuentas, u, **kw):
    _si_no(kw, "activo")
    items = []
    for cuenta in cuentas:
        items.extend(r_colaboradores.listar(db=db, _=u, cuenta=cuenta, **kw))
    return _resultado_lista(items)


def _leer_listar_cursos(db, cuentas, u):
    items = []
    for cuenta in cuentas:
        items.extend(r_capacitacion.listar(db=db, _=u, cuenta=cuenta))
    return _resultado_lista(items)


def _leer_kpis_capacitacion(db, cuentas, u):
    resultados = [r_capacitacion.kpis(db=db, _=u, cuenta=cuenta) for cuenta in cuentas]
    return resultados[0] if len(resultados) == 1 else {"porCuenta": resultados}


def _leer_detalle_curso(db, cuentas, u, codigo):
    return r_capacitacion.detalle(codigo, db=db, _=u, cuenta=cuentas[0])


def _leer_listar_clientes(db, cuentas, u, **kw):
    items = []
    for cuenta in cuentas:
        items.extend(r_clientes.listar(db=db, _=u, cuenta=cuenta, **kw))
    return _resultado_lista(items)


def _leer_bitacora(db, cuentas, u, limite=50):
    items = []
    for cuenta in cuentas:
        items.extend(r_webhooks.bitacora(limite=limite, db=db, _=u, cuenta=cuenta))
    return _resultado_lista(items)


# --- admin-only (mismo Depends(usuario_admin) que sus endpoints; validado en `responder()`) ---


def _leer_listar_usuarios(db, cuentas, u, **kw):
    items = []
    for cuenta in cuentas:
        items.extend(r_auth.listar(db=db, _=u, cuenta=cuenta))
    return _resultado_lista(items)


def _leer_reglas_notificacion(db, cuentas, u):
    resultados = [r_notificaciones.listar_reglas(db=db, _=u, cuenta=cuenta) for cuenta in cuentas]
    return resultados[0] if len(resultados) == 1 else {"porCuenta": resultados}


def _leer_historial_notificaciones(db, cuentas, u, limite=50):
    items = []
    for cuenta in cuentas:
        items.extend(r_notificaciones.historial(limite=limite, db=db, _=u, cuenta=cuenta))
    return _resultado_lista(items)


def _p(tipo: str, descripcion: str, enum: Optional[List[str]] = None) -> dict:
    """Parámetro OPCIONAL de una tool de LECTURA, bajo "strict" function-calling de OpenAI:
    tipo nullable + se lista en 'required' de todos modos (lo exige strict mode) — así el
    modelo manda `null` EXPLÍCITO cuando el filtro no aplica, en vez de inventar "", 0 o
    "si"/"no" de relleno. Verificado con el modelo real (Fase F): sin esto, un modelo que
    rellena valores por defecto en parámetros opcionales puede filtrar de más y esconder
    resultados reales que sí existen — con `null` real, `_limpiar_args_lectura` lo descarta
    limpio y el filtro correspondiente simplemente no se aplica."""
    d = {"type": [tipo, "null"], "description": f"{descripcion} Manda null si no aplica — nunca inventes un valor de relleno."}
    if enum:
        d["enum"] = enum + [None]
    return d


def _pr(tipo: str, descripcion: str, enum: Optional[List[str]] = None) -> dict:
    """Parámetro genuinamente OBLIGATORIO de una tool de lectura (no nullable)."""
    d = {"type": tipo, "description": descripcion}
    if enum:
        d["enum"] = enum
    return d


def _parametros_lectura(propiedades: dict) -> dict:
    """Bajo strict mode, 'required' debe listar TODAS las propiedades — las opcionales ya
    son nullable vía `_p()`, así que esto no vuelve obligatorio nada que no lo fuera antes."""
    return {"type": "object", "properties": propiedades, "required": list(propiedades.keys()), "additionalProperties": False}


def _tool_lectura(nombre: str, descripcion: str, propiedades: dict) -> dict:
    return {"type": "function", "name": nombre, "description": descripcion, "strict": True, "parameters": _parametros_lectura(propiedades)}


def _limpiar_args_lectura(args: dict) -> dict:
    """El modelo a veces manda TODOS los parámetros opcionales del schema aunque no vengan al
    caso, rellenando strings con "" e ids con 0 en vez de omitirlos. Las funciones de router
    usan `Optional[X] = None` + `is not None` para decidir si un filtro aplica — un "" o un 0
    explícito ahí SÍ filtra (ej. `cliente_id == 0`, que nunca existe, regresando 0 resultados).
    Se limpia antes de llamar, nunca dentro de las funciones de router (esas siguen intactas)."""
    limpio = {}
    for k, v in args.items():
        if v is None or v == "":
            continue
        if k.endswith("_id") and v == 0:
            continue
        limpio[k] = v
    return limpio


TOOLS_LECTURA: Dict[str, dict] = {
    "buscar_vacantes": {
        "fn": _leer_buscar_vacantes, "admin": False,
        "schema": _tool_lectura("buscar_vacantes", "Busca vacantes con filtros. Regresa conteo total + una muestra.", {
            "estado": _p("string", "Borrador | Publicada | Cerrada | En revisión"),
            "busqueda": _p("string", "Texto libre sobre el título"),
            "cliente_id": _p("integer", "Filtra por Cliente"),
            "area": _p("string", "Texto libre sobre el área"),
            "ubicacion": _p("string", "Texto libre sobre la ubicación"),
        }),
    },
    "detalle_vacante": {
        "fn": _leer_detalle_vacante, "admin": False,
        "schema": _tool_lectura("detalle_vacante", "Ficha completa de una vacante por su código (ej. VAC-1042).", {
            "codigo": _pr("string", "Código de la vacante"),
        }),
    },
    "buscar_candidatos": {
        "fn": _leer_buscar_candidatos, "admin": False,
        "schema": _tool_lectura(
            "buscar_candidatos",
            "Busca candidatos con filtros. Regresa conteo total + una muestra. Para resolver "
            "un nombre propio ('Jorge') usa `nombre` — si matchea más de un candidato, "
            "PREGUNTA cuál antes de proponer cualquier acción, nunca adivines. TODOS los "
            "parámetros son opcionales (nullable): manda null en cualquiera que el usuario no "
            "haya pedido explícitamente — un `apto`/`consentimiento`/`duplicados` que no sea "
            "null SÍ filtra de verdad y puede esconder candidatos reales.",
            {
                "nombre": _p("string", "Texto libre sobre el nombre del candidato"),
                "vacante": _p("string", "Código de vacante (VAC-####)"),
                "etapa": _p("string", "Prefiltro | Entrevista IA | Evaluación | Entrevista Humana | Contratación | Onboarding"),
                "estado": _p("string", "cumple | revision | no_cumple | pendiente"),
                "fuente": _p("string", "Formulario | WhatsApp | OCC | LinkedIn | Indeed | RH"),
                "cliente_id": _p("integer", "Filtra por Cliente de la vacante"),
                "consentimiento": _p("string", "'si'/'no' filtra por consentimiento LFPDPPP registrado", enum=["si", "no"]),
                "apto": _p("string", "'si' = apto, 'no' = no apto (resultado_apto)", enum=["si", "no"]),
                "duplicados": _p("string", "'si' = solo candidatos con teléfono/correo repetido", enum=["si", "no"]),
                "score_min": _p("integer", "Score CV mínimo 0-100"),
                "score_max": _p("integer", "Score CV máximo 0-100"),
            },
        ),
    },
    "resumen_candidato": {
        "fn": _leer_resumen_candidato, "admin": False,
        "schema": _tool_lectura(
            "resumen_candidato",
            "Ficha COMPLETA de un candidato por su código (ej. C-8801): CV, prefiltro, "
            "entrevistas, documentos, actividad reciente. Úsala para 'resúmeme todo lo que ha pasado con X'.",
            {"codigo": _pr("string", "Código de la postulación (P-####, el id de la tarjeta; se acepta C-#### de la persona)")},
        ),
    },
    "mensajes_candidato": {
        "fn": _leer_mensajes_candidato, "admin": False,
        "schema": _tool_lectura("mensajes_candidato", "Historial de conversación de WhatsApp con un candidato.", {
            "codigo": _pr("string", "Código de la postulación (P-####, el id de la tarjeta; se acepta C-#### de la persona)"),
        }),
    },
    "pipeline_cuenta": {
        "fn": _leer_pipeline_cuenta, "admin": False,
        "schema": _tool_lectura(
            "pipeline_cuenta",
            "Conteos y embudo de punta a punta (vacantes, candidatos por etapa/estado/fuente, "
            "contratación, acciones pendientes). ES LA FUENTE ÚNICA para cualquier "
            "'¿cuántos...?' — nunca inventes un conteo, siempre llama esto.",
            {},
        ),
    },
    "metricas_entrevistas": {
        "fn": _leer_metricas_entrevistas, "admin": False,
        "schema": _tool_lectura("metricas_entrevistas", "Métricas del motor de Entrevista IA (avatar).", {}),
    },
    "listar_expedientes": {
        "fn": _leer_listar_expedientes, "admin": False,
        "schema": _tool_lectura("listar_expedientes", "Expedientes de contratación/onboarding, opcionalmente por estado.", {
            "estado": _p("string", "integracion | completo | alta | cancelado"),
        }),
    },
    "detalle_expediente": {
        "fn": _leer_detalle_expediente, "admin": False,
        "schema": _tool_lectura("detalle_expediente", "Ficha completa de un expediente por su id numérico.", {
            "exp_id": _pr("integer", "Id del expediente"),
        }),
    },
    "metricas_contratacion": {
        "fn": _leer_metricas_contratacion, "admin": False,
        "schema": _tool_lectura("metricas_contratacion", "Resumen del módulo de Contratación/Onboarding para el tablero.", {}),
    },
    "buscar_requisiciones": {
        "fn": _leer_buscar_requisiciones, "admin": False,
        "schema": _tool_lectura("buscar_requisiciones", "Busca requisiciones internas (Módulo 4), opcionalmente por estado/área.", {
            "estado": _p("string", "borrador | autorizacion | autorizada | rechazada | convertida"),
            "area": _p("string", "Área solicitante"),
        }),
    },
    "detalle_requisicion": {
        "fn": _leer_detalle_requisicion, "admin": False,
        "schema": _tool_lectura("detalle_requisicion", "Ficha completa de una requisición por su código (ej. REQ-####).", {
            "codigo": _pr("string", "Código de la requisición"),
        }),
    },
    "sugerencias_movilidad": {
        "fn": _leer_sugerencias_movilidad, "admin": False,
        "schema": _tool_lectura("sugerencias_movilidad", "Empleados internos sugeridos (Radar Interno) para una requisición.", {
            "codigo": _pr("string", "Código de la requisición"),
        }),
    },
    "listar_colaboradores": {
        "fn": _leer_listar_colaboradores, "admin": False,
        "schema": _tool_lectura("listar_colaboradores", "Roster de colaboradores dados de alta.", {
            "activo": _p("string", "'si'/'no' filtra por activo/inactivo", enum=["si", "no"]),
        }),
    },
    "listar_cursos": {
        "fn": _leer_listar_cursos, "admin": False,
        "schema": _tool_lectura("listar_cursos", "Cursos de capacitación existentes.", {}),
    },
    "detalle_curso": {
        "fn": _leer_detalle_curso, "admin": False,
        "schema": _tool_lectura("detalle_curso", "Ficha completa de un curso por su código.", {
            "codigo": _pr("string", "Código del curso"),
        }),
    },
    "kpis_capacitacion": {
        "fn": _leer_kpis_capacitacion, "admin": False,
        "schema": _tool_lectura("kpis_capacitacion", "KPIs globales de capacitación (cursos activos, en formación, tasa de finalización).", {}),
    },
    "listar_clientes": {
        "fn": _leer_listar_clientes, "admin": False,
        "schema": _tool_lectura("listar_clientes", "Clientes (empresas para las que recluta la Cuenta).", {
            "estado": _p("string", "Activo | Inactivo"),
        }),
    },
    "bitacora": {
        "fn": _leer_bitacora, "admin": False,
        "schema": _tool_lectura("bitacora", "Últimos eventos de auditoría registrados (quién hizo qué y cuándo).", {
            "limite": _p("integer", "Máximo de filas, por defecto 50"),
        }),
    },
    "listar_usuarios": {
        "fn": _leer_listar_usuarios, "admin": True,
        "schema": _tool_lectura("listar_usuarios", "[Solo administrador] Personas de RH con acceso al sistema.", {}),
    },
    "reglas_notificacion": {
        "fn": _leer_reglas_notificacion, "admin": True,
        "schema": _tool_lectura("reglas_notificacion", "[Solo administrador] Configuración actual de Notificaciones (qué evento avisa a quién por qué canal).", {}),
    },
    "historial_notificaciones": {
        "fn": _leer_historial_notificaciones, "admin": True,
        "schema": _tool_lectura("historial_notificaciones", "[Solo administrador] Últimos envíos de notificaciones (evento, destinatario, canal, si se envió).", {
            "limite": _p("integer", "Máximo de filas, por defecto 50"),
        }),
    },
}


# ============================================================
# Catálogo de ESCRITURA — el modelo solo PROPONE (ver responder()); ejecutar_accion() ejecuta
# ============================================================


def _ejecutar_mover_etapa(db, u, cuenta, a):
    return _llamar(r_candidatos.mover_etapa, a["codigo"], r_candidatos.EtapaIn(etapa=a["etapa"], comentario=a.get("comentario", "")),
                    forzar_prueba=False, db=db, u=u, cuenta=cuenta)


def _ejecutar_descartar_candidato(db, u, cuenta, a):
    return _llamar(r_candidatos.decision, a["codigo"], r_candidatos.DecisionIn(accion="descartar", comentario=a.get("comentario", "")),
                    db=db, u=u, cuenta=cuenta)


def _ejecutar_asignar_vacante(db, u, cuenta, a):
    return _llamar(r_candidatos.asignar, a["codigo"], r_candidatos.AsignarIn(vacante=a["vacante"], reevaluar=True),
                    db=db, u=u, cuenta=cuenta)


def _ejecutar_programar_entrevista(db, u, cuenta, a):
    datos = r_candidatos.EntrevistaHumanaIn(
        tipo_entrevistador=a["tipo_entrevistador"],
        entrevistador_usuario_id=a.get("entrevistador_usuario_id"),
        entrevistador_nombre=a.get("entrevistador_nombre", ""),
        entrevistador_correo=a.get("entrevistador_correo", ""),
        entrevistador_whatsapp=a.get("entrevistador_whatsapp", ""),
        fecha=a["fecha"], hora=a["hora"], modalidad=a["modalidad"],
        liga=a.get("liga", ""), ubicacion=a.get("ubicacion", ""),
        telefono_contacto=a.get("telefono_contacto", ""), comentario=a.get("comentario", ""),
    )
    return _llamar(r_candidatos.programar_entrevista_humana, a["codigo"], datos, db=db, u=u, cuenta=cuenta)


def _ejecutar_modificar_entrevista(db, u, cuenta, a):
    datos = r_candidatos.EntrevistaHumanaModificarIn(
        fecha=a["fecha"], hora=a["hora"], modalidad=a["modalidad"],
        liga=a.get("liga", ""), ubicacion=a.get("ubicacion", ""),
        telefono_contacto=a.get("telefono_contacto", ""), comentario=a.get("comentario", ""),
    )
    return _llamar(r_candidatos.modificar_entrevista_humana, a["codigo"], datos, db=db, u=u, cuenta=cuenta)


def _ejecutar_cancelar_entrevista(db, u, cuenta, a):
    return _llamar(r_candidatos.cancelar_entrevista_humana, a["codigo"], db=db, u=u, cuenta=cuenta)


def _ejecutar_marcar_realizada(db, u, cuenta, a):
    return _llamar(r_candidatos.marcar_entrevista_humana_realizada, a["codigo"], forzar_prueba=False, db=db, u=u, cuenta=cuenta)


def _ejecutar_registrar_resultado(db, u, cuenta, a):
    datos = r_candidatos.EntrevistaHumanaResultadoIn(
        resultado=a["resultado"], recomendacion=a["recomendacion"], comentario=a.get("comentario", "")
    )
    return _llamar(r_candidatos.registrar_resultado_entrevista_humana, a["codigo"], datos, forzar_prueba=False, db=db, u=u, cuenta=cuenta)


def _ejecutar_recordatorio_entrevista(db, u, cuenta, a):
    return _llamar(r_candidatos.recordatorio_entrevista_humana, a["codigo"], forzar_prueba=False, db=db, u=u, cuenta=cuenta)


def _ejecutar_solicitar_documentos(db, u, cuenta, a):
    return _llamar(r_candidatos.solicitar_documentos, a["codigo"], db=db, u=u, cuenta=cuenta)


def _ejecutar_recordatorio_documentos(db, u, cuenta, a):
    return _llamar(r_candidatos.recordatorio_documentos, a["codigo"], db=db, u=u, cuenta=cuenta)


def _ejecutar_condiciones_contratacion(db, u, cuenta, a):
    datos = r_candidatos.CondicionesContratacionIn(
        puesto=a.get("puesto", ""), sueldo=a.get("sueldo", ""), tipo_contratacion=a.get("tipo_contratacion", ""),
        fecha_ingreso=a.get("fecha_ingreso"), ubicacion=a.get("ubicacion", ""), jefe_directo=a.get("jefe_directo", ""),
    )
    return _llamar(r_candidatos.guardar_condiciones_contratacion, a["codigo"], datos, db=db, u=u, cuenta=cuenta)


def _ejecutar_alta_expediente(db, u, cuenta, a):
    return _llamar(r_contratacion.alta, a["exp_id"], r_contratacion.AltaIn(fecha_ingreso=a.get("fecha_ingreso")),
                    forzar_prueba=False, db=db, u=u, cuenta=cuenta)


def _ejecutar_cancelar_expediente(db, u, cuenta, a):
    return _llamar(r_contratacion.cancelar, a["exp_id"], r_contratacion.CancelarIn(motivo=a["motivo"]), db=db, u=u, cuenta=cuenta)


def _ejecutar_marcar_documento(db, u, cuenta, a):
    datos = r_contratacion.EstadoDocIn(
        tipo=a["tipo"], estado=a["estado"], notas=a.get("notas", ""), recibido_fisico=a.get("recibido_fisico", False)
    )
    return _llamar(r_contratacion.marcar_documento, a["exp_id"], datos, db=db, u=u, cuenta=cuenta)


def _ejecutar_crear_vacante(db, u, cuenta, a):
    datos = r_vacantes.CrearIn(
        titulo=a["titulo"], area=a.get("area", ""), ubicacion=a.get("ubicacion", ""),
        sueldo=a.get("sueldo", "A convenir"), requisitos=a.get("requisitos", ""),
        modalidad=a.get("modalidad", "Presencial"), descripcion=a.get("notas", ""),  # Parte 3: notas → descripción breve (guía)
        cliente_id=a.get("cliente_id"), responsable_id=a.get("responsable_id"),
        generar_si_falta=True, publicar=False,
    )
    return _llamar(r_vacantes.crear, datos, db=db, u=u, cuenta=cuenta)


def _ejecutar_actualizar_vacante(db, u, cuenta, a):
    campos = {k: v for k, v in a.items() if k != "codigo"}
    return _llamar(r_vacantes.actualizar, a["codigo"], r_vacantes.ActualizarIn(**campos), db=db, u=u, cuenta=cuenta)


def _ejecutar_publicar_vacante(db, u, cuenta, a):
    plataformas = a.get("plataformas") or ["WhatsApp", "Portal"]
    return _llamar(r_vacantes.publicar, a["codigo"], r_vacantes.PublicarIn(plataformas=plataformas), db=db, u=u, cuenta=cuenta)


def _ejecutar_cerrar_vacante(db, u, cuenta, a):
    return _llamar(r_vacantes.cerrar, a["codigo"], r_vacantes.PublicarIn(), db=db, u=u, cuenta=cuenta)


def _ejecutar_crear_requisicion(db, u, cuenta, a):
    campos = {k: v for k, v in a.items()}
    return _llamar(r_requisiciones.crear, r_requisiciones.CrearIn(**campos), db=db, u=u, cuenta=cuenta)


def _ejecutar_autorizar_requisicion(db, u, cuenta, a):
    return _llamar(r_requisiciones.autorizar, a["codigo"], r_requisiciones.AutorizarIn(comentario=a.get("comentario", "")),
                    db=db, u=u, cuenta=cuenta)


def _ejecutar_rechazar_requisicion(db, u, cuenta, a):
    return _llamar(r_requisiciones.rechazar, a["codigo"], r_requisiciones.RechazarIn(comentario=a.get("comentario", "")),
                    db=db, u=u, cuenta=cuenta)


def _ejecutar_convertir_requisicion(db, u, cuenta, a):
    datos = r_requisiciones.ConvertirVacanteIn(generar_contenido=a.get("generar_contenido", True), notas=a.get("notas", ""))
    return _llamar(r_requisiciones.convertir_vacante, a["codigo"], datos, db=db, u=u, cuenta=cuenta)


def _ejecutar_decidir_sugerencia(db, u, cuenta, a):
    datos = r_requisiciones.DecidirSugerenciaIn(estado=a["estado"], comentario=a.get("comentario", ""))
    return _llamar(r_requisiciones.decidir_sugerencia, a["codigo"], a["sugerencia_id"], datos, db=db, u=u, cuenta=cuenta)


def _ejecutar_asignar_curso(db, u, cuenta, a):
    datos = r_capacitacion.AsignarCursoIn(colaborador_ids=a["colaborador_ids"])
    return _llamar(r_capacitacion.asignar, a["codigo"], datos, db=db, u=u, cuenta=cuenta)


def _ejecutar_crear_cliente(db, u, cuenta, a):
    return _llamar(r_clientes.crear, r_clientes.CrearIn(nombre=a["nombre"], estado=a.get("estado", "Activo")), db=db, u=u, cuenta=cuenta)


def _ejecutar_actualizar_cliente(db, u, cuenta, a):
    campos = {k: v for k, v in a.items() if k != "cliente_id"}
    return _llamar(r_clientes.actualizar, a["cliente_id"], r_clientes.ActualizarIn(**campos), db=db, u=u, cuenta=cuenta)


def _ejecutar_crear_usuario(db, u, cuenta, a):
    datos = r_auth.CrearUsuarioIn(
        correo=a["correo"], nombre=a["nombre"], puesto=a.get("puesto", ""), rol=a.get("rol", "Usuario"),
        password=a["password"],
    )
    return _llamar(r_auth.crear, datos, db=db, admin=u, cuenta=cuenta)


def _ejecutar_actualizar_usuario(db, u, cuenta, a):
    campos = {k: v for k, v in a.items() if k != "usuario_id"}
    return _llamar(r_auth.actualizar, a["usuario_id"], r_auth.ActualizarUsuarioIn(**campos), db=db, admin=u, cuenta=cuenta)


def _ejecutar_regla_notificacion(db, u, cuenta, a):
    campos = {k: v for k, v in a.items() if k != "evento"}
    return _llamar(r_notificaciones.actualizar_regla, a["evento"], r_notificaciones.ReglaNotificacionIn(**campos),
                    db=db, u=u, cuenta=cuenta)


TOOLS_ESCRITURA: Dict[str, dict] = {
    "mover_etapa_candidato": {
        "permiso": "decisor", "ejecutar": _ejecutar_mover_etapa,
        "resumen": lambda db, cuenta, a: f"Mover a {_nombre_candidato(db, cuenta.id, a['codigo'])} a la etapa «{a['etapa']}»."
        + (f" Comentario: {a['comentario']}" if a.get("comentario") else ""),
        "schema": {
            "type": "function", "name": "mover_etapa_candidato",
            "description": "Mueve un candidato a otra etapa del pipeline (excepto a Entrevista Humana: usa programar_entrevista_humana).",
            "parameters": {"type": "object", "properties": {
                "codigo": _p("string", "Código de la postulación (P-####, el id de la tarjeta; se acepta C-#### de la persona)"),
                "etapa": _p("string", "Prefiltro | Entrevista IA | Evaluación | Contratación | Onboarding"),
                "comentario": _p("string", "Comentario opcional"),
            }, "required": ["codigo", "etapa"], "additionalProperties": False},
        },
    },
    "descartar_candidato": {
        "permiso": "decisor", "ejecutar": _ejecutar_descartar_candidato,
        "resumen": lambda db, cuenta, a: f"Descartar a {_nombre_candidato(db, cuenta.id, a['codigo'])}."
        + (f" Motivo: {a['comentario']}" if a.get("comentario") else ""),
        "schema": {
            "type": "function", "name": "descartar_candidato",
            "description": "Descarta a un candidato del proceso.",
            "parameters": {"type": "object", "properties": {
                "codigo": _p("string", "Código de la postulación (P-####, el id de la tarjeta; se acepta C-#### de la persona)"), "comentario": _p("string", "Motivo del descarte"),
            }, "required": ["codigo"], "additionalProperties": False},
        },
    },
    "asignar_vacante_candidato": {
        "permiso": "decisor", "ejecutar": _ejecutar_asignar_vacante,
        "resumen": lambda db, cuenta, a: (
            f"Asignar a {_nombre_candidato(db, cuenta.id, a['codigo'])} a la vacante "
            f"«{_titulo_vacante(db, cuenta.id, a['vacante'])}» (se vuelve a evaluar el prefiltro)."
        ),
        "schema": {
            "type": "function", "name": "asignar_vacante_candidato",
            "description": "Asigna (o reasigna) un candidato a una vacante.",
            "parameters": {"type": "object", "properties": {
                "codigo": _p("string", "Código de la postulación (P-####, el id de la tarjeta; se acepta C-#### de la persona)"), "vacante": _p("string", "Código de la vacante"),
            }, "required": ["codigo", "vacante"], "additionalProperties": False},
        },
    },
    "programar_entrevista_humana": {
        "permiso": "decisor", "ejecutar": _ejecutar_programar_entrevista,
        "resumen": lambda db, cuenta, a: (
            f"Agendar entrevista con {_nombre_candidato(db, cuenta.id, a['codigo'])} "
            f"el {a['fecha']} {a['hora']} ({a['modalidad']})."
        ),
        "schema": {
            "type": "function", "name": "programar_entrevista_humana",
            "description": "Agenda una ronda nueva de Entrevista Humana y mueve al candidato a esa etapa.",
            "parameters": {"type": "object", "properties": {
                "codigo": _p("string", "Código de la postulación (P-####, el id de la tarjeta; se acepta C-#### de la persona)"),
                "tipo_entrevistador": _p("string", "interno | externo", enum=["interno", "externo"]),
                "entrevistador_usuario_id": _p("integer", "Id de Usuario si es interno"),
                "entrevistador_nombre": _p("string", "Nombre si es externo"),
                "entrevistador_correo": _p("string", "Correo si es externo"),
                "entrevistador_whatsapp": _p("string", "WhatsApp si es externo (opcional)"),
                "fecha": _p("string", "Fecha ISO, ej. 2026-09-20"),
                "hora": _p("string", "Hora HH:MM, hora de México"),
                "modalidad": _p("string", "Presencial | Videollamada | Llamada"),
                "liga": _p("string", "Liga de videollamada, si aplica"),
                "ubicacion": _p("string", "Ubicación, si es presencial"),
                "telefono_contacto": _p("string", "Teléfono si es llamada"),
                "comentario": _p("string", "Comentario opcional"),
            }, "required": ["codigo", "tipo_entrevistador", "fecha", "hora", "modalidad"], "additionalProperties": False},
        },
    },
    "modificar_entrevista_humana": {
        "permiso": "decisor", "ejecutar": _ejecutar_modificar_entrevista,
        "resumen": lambda db, cuenta, a: (
            f"Modificar la entrevista de {_nombre_candidato(db, cuenta.id, a['codigo'])} "
            f"a {a['fecha']} {a['hora']} ({a['modalidad']})."
        ),
        "schema": {
            "type": "function", "name": "modificar_entrevista_humana",
            "description": "Cambia fecha/modalidad de la ronda de Entrevista Humana vigente (no aplica si ya fue cancelada o realizada).",
            "parameters": {"type": "object", "properties": {
                "codigo": _p("string", "Código de la postulación (P-####, el id de la tarjeta; se acepta C-#### de la persona)"),
                "fecha": _p("string", "Fecha ISO"), "hora": _p("string", "Hora HH:MM"),
                "modalidad": _p("string", "Presencial | Videollamada | Llamada"),
                "liga": _p("string", "Liga de videollamada, si aplica"),
                "ubicacion": _p("string", "Ubicación, si es presencial"),
                "telefono_contacto": _p("string", "Teléfono si es llamada"),
                "comentario": _p("string", "Comentario opcional"),
            }, "required": ["codigo", "fecha", "hora", "modalidad"], "additionalProperties": False},
        },
    },
    "cancelar_entrevista_humana": {
        "permiso": "decisor", "ejecutar": _ejecutar_cancelar_entrevista,
        "resumen": lambda db, cuenta, a: f"Cancelar la entrevista programada de {_nombre_candidato(db, cuenta.id, a['codigo'])}.",
        "schema": {
            "type": "function", "name": "cancelar_entrevista_humana",
            "description": "Cancela la ronda de Entrevista Humana vigente. No mueve la etapa del candidato.",
            "parameters": {"type": "object", "properties": {"codigo": _p("string", "Código de la postulación (P-####, el id de la tarjeta; se acepta C-#### de la persona)")},
                            "required": ["codigo"], "additionalProperties": False},
        },
    },
    "marcar_entrevista_humana_realizada": {
        "permiso": "decisor", "ejecutar": _ejecutar_marcar_realizada,
        "resumen": lambda db, cuenta, a: f"Marcar como realizada la entrevista de {_nombre_candidato(db, cuenta.id, a['codigo'])}.",
        "schema": {
            "type": "function", "name": "marcar_entrevista_humana_realizada",
            "description": "Confirma que la Entrevista Humana ya ocurrió.",
            "parameters": {"type": "object", "properties": {"codigo": _p("string", "Código de la postulación (P-####, el id de la tarjeta; se acepta C-#### de la persona)")},
                            "required": ["codigo"], "additionalProperties": False},
        },
    },
    "registrar_resultado_entrevista_humana": {
        "permiso": "decisor", "ejecutar": _ejecutar_registrar_resultado,
        "resumen": lambda db, cuenta, a: (
            f"Registrar resultado de {_nombre_candidato(db, cuenta.id, a['codigo'])}: "
            f"{a['resultado']} ({a['recomendacion']})."
        ),
        "schema": {
            "type": "function", "name": "registrar_resultado_entrevista_humana",
            "description": "Captura o corrige el resultado de la Entrevista Humana (respaldo manual de RH).",
            "parameters": {"type": "object", "properties": {
                "codigo": _p("string", "Código de la postulación (P-####, el id de la tarjeta; se acepta C-#### de la persona)"),
                "resultado": _p("string", "aprobado | no_aprobado", enum=["aprobado", "no_aprobado"]),
                "recomendacion": _p("string", "avanzar | no_avanzar | segunda_entrevista",
                                     enum=["avanzar", "no_avanzar", "segunda_entrevista"]),
                "comentario": _p("string", "Obligatorio si no_aprobado o segunda_entrevista"),
            }, "required": ["codigo", "resultado", "recomendacion"], "additionalProperties": False},
        },
    },
    "recordatorio_entrevista_humana": {
        "permiso": "decisor", "ejecutar": _ejecutar_recordatorio_entrevista,
        "resumen": lambda db, cuenta, a: (
            f"Enviar el recordatorio de entrevista configurado (Configuración → Notificaciones) "
            f"a {_nombre_candidato(db, cuenta.id, a['codigo'])}."
        ),
        "schema": {
            "type": "function", "name": "recordatorio_entrevista_humana",
            "description": (
                "Dispara el evento 'recordatorio_entrevista' — a quién llega (candidato/"
                "entrevistador/Cliente) y por qué canal lo decide la regla ya configurada de "
                "la Cuenta, NO este tool. No existe forma de mandarlo solo a un destinatario "
                "específico; si el usuario pide eso, explícaselo en vez de proponer esta acción."
            ),
            "parameters": {"type": "object", "properties": {"codigo": _p("string", "Código de la postulación (P-####, el id de la tarjeta; se acepta C-#### de la persona)")},
                            "required": ["codigo"], "additionalProperties": False},
        },
    },
    "solicitar_documentos": {
        "permiso": "decisor", "ejecutar": _ejecutar_solicitar_documentos,
        "resumen": lambda db, cuenta, a: f"Solicitar documentos de onboarding a {_nombre_candidato(db, cuenta.id, a['codigo'])}.",
        "schema": {
            "type": "function", "name": "solicitar_documentos",
            "description": "Rompe el hielo de Onboarding pidiendo documentos (candidato debe estar en esa etapa).",
            "parameters": {"type": "object", "properties": {"codigo": _p("string", "Código de la postulación (P-####, el id de la tarjeta; se acepta C-#### de la persona)")},
                            "required": ["codigo"], "additionalProperties": False},
        },
    },
    "recordatorio_documentos": {
        "permiso": "decisor", "ejecutar": _ejecutar_recordatorio_documentos,
        "resumen": lambda db, cuenta, a: f"Enviar recordatorio de documentos pendientes a {_nombre_candidato(db, cuenta.id, a['codigo'])}.",
        "schema": {
            "type": "function", "name": "recordatorio_documentos",
            "description": "Recordatorio de documentos de Onboarding pendientes.",
            "parameters": {"type": "object", "properties": {"codigo": _p("string", "Código de la postulación (P-####, el id de la tarjeta; se acepta C-#### de la persona)")},
                            "required": ["codigo"], "additionalProperties": False},
        },
    },
    "guardar_condiciones_contratacion": {
        "permiso": "decisor", "ejecutar": _ejecutar_condiciones_contratacion,
        "resumen": lambda db, cuenta, a: f"Guardar condiciones de contratación de {_nombre_candidato(db, cuenta.id, a['codigo'])}.",
        "schema": {
            "type": "function", "name": "guardar_condiciones_contratacion",
            "description": "Actualiza puesto/sueldo/tipo de contratación/fecha de ingreso/ubicación/jefe directo.",
            "parameters": {"type": "object", "properties": {
                "codigo": _p("string", "Código de la postulación (P-####, el id de la tarjeta; se acepta C-#### de la persona)"), "puesto": _p("string", ""), "sueldo": _p("string", ""),
                "tipo_contratacion": _p("string", ""), "fecha_ingreso": _p("string", "ISO, ej. 2026-09-15"),
                "ubicacion": _p("string", ""), "jefe_directo": _p("string", ""),
            }, "required": ["codigo"], "additionalProperties": False},
        },
    },
    "alta_expediente": {
        "permiso": "decisor", "ejecutar": _ejecutar_alta_expediente,
        "resumen": lambda db, cuenta, a: f"Dar de alta como colaborador a {_candidato_de_expediente(db, cuenta.id, a['exp_id'])}.",
        "schema": {
            "type": "function", "name": "alta_expediente",
            "description": "Autoriza el alta como Colaborador (el expediente debe estar 100% y sin documentos por revisar).",
            "parameters": {"type": "object", "properties": {
                "exp_id": _p("integer", "Id del expediente"), "fecha_ingreso": _p("string", "ISO, opcional"),
            }, "required": ["exp_id"], "additionalProperties": False},
        },
    },
    "cancelar_expediente": {
        "permiso": "decisor", "ejecutar": _ejecutar_cancelar_expediente,
        "resumen": lambda db, cuenta, a: f"Cancelar la contratación de {_candidato_de_expediente(db, cuenta.id, a['exp_id'])}. Motivo: {a['motivo']}",
        "schema": {
            "type": "function", "name": "cancelar_expediente",
            "description": "Cancela un expediente que no llegó a alta; regresa al candidato a Entrevista Humana.",
            "parameters": {"type": "object", "properties": {
                "exp_id": _p("integer", "Id del expediente"), "motivo": _p("string", "Motivo de la cancelación"),
            }, "required": ["exp_id", "motivo"], "additionalProperties": False},
        },
    },
    "marcar_documento": {
        "permiso": "decisor", "ejecutar": _ejecutar_marcar_documento,
        "resumen": lambda db, cuenta, a: (
            f"Marcar el documento «{a['tipo']}» de {_candidato_de_expediente(db, cuenta.id, a['exp_id'])} como «{a['estado']}»."
        ),
        "schema": {
            "type": "function", "name": "marcar_documento",
            "description": "Cambia el estado de un documento del expediente.",
            "parameters": {"type": "object", "properties": {
                "exp_id": _p("integer", "Id del expediente"), "tipo": _p("string", "Tipo de documento (ej. INE)"),
                "estado": _p("string", "recibido | rechazado | revision | pendiente"),
                "notas": _p("string", "Notas opcionales"), "recibido_fisico": _p("boolean", "true si se recibió fuera del sistema"),
            }, "required": ["exp_id", "tipo", "estado"], "additionalProperties": False},
        },
    },
    "crear_vacante": {
        "permiso": "decisor", "ejecutar": _ejecutar_crear_vacante,
        "resumen": lambda db, cuenta, a: f"Crear la vacante «{a['titulo']}»" + (f" en {a['ubicacion']}" if a.get("ubicacion") else "") + ".",
        "schema": {
            "type": "function", "name": "crear_vacante",
            "description": "Crea una vacante nueva (el contenido/copy se genera con IA si no se da).",
            "parameters": {"type": "object", "properties": {
                "titulo": _p("string", "Título del puesto"), "area": _p("string", ""), "ubicacion": _p("string", ""),
                "sueldo": _p("string", "Ej. $10,000 - 12,000"), "requisitos": _p("string", "Requisitos indispensables, separados por coma"),
                "modalidad": _p("string", "Presencial | Híbrido | Remoto"),
                "cliente_id": _p("integer", "Cliente para el que se recluta, opcional"),
                "responsable_id": _p("integer", "Usuario responsable, opcional (default quien crea)"),
                "notas": _p("string", "Notas para la generación de contenido"),
            }, "required": ["titulo"], "additionalProperties": False},
        },
    },
    "actualizar_vacante": {
        "permiso": "decisor", "ejecutar": _ejecutar_actualizar_vacante,
        "resumen": lambda db, cuenta, a: f"Actualizar la vacante «{_titulo_vacante(db, cuenta.id, a['codigo'])}».",
        "schema": {
            "type": "function", "name": "actualizar_vacante",
            "description": "Edita campos de una vacante existente (solo manda los campos a cambiar).",
            "parameters": {"type": "object", "properties": {
                "codigo": _p("string", "Código de la vacante"), "titulo": _p("string", ""), "area": _p("string", ""),
                "ubicacion": _p("string", ""), "modalidad": _p("string", ""), "sueldo": _p("string", ""),
                "requisitos": _p("string", ""),
            }, "required": ["codigo"], "additionalProperties": False},
        },
    },
    "publicar_vacante": {
        "permiso": "decisor", "ejecutar": _ejecutar_publicar_vacante,
        "resumen": lambda db, cuenta, a: (
            f"Publicar la vacante «{_titulo_vacante(db, cuenta.id, a['codigo'])}» en "
            f"{', '.join(a.get('plataformas') or ['WhatsApp', 'Portal'])}."
        ),
        "schema": {
            "type": "function", "name": "publicar_vacante",
            "description": "Publica una vacante en una o más plataformas (debe tener contenido generado).",
            "parameters": {"type": "object", "properties": {
                "codigo": _p("string", "Código de la vacante"),
                "plataformas": {"type": "array", "items": {"type": "string"}, "description": "Portal | WhatsApp | Google Empleos | Jooble | Talent.com"},
            }, "required": ["codigo"], "additionalProperties": False},
        },
    },
    "cerrar_vacante": {
        "permiso": "decisor", "ejecutar": _ejecutar_cerrar_vacante,
        "resumen": lambda db, cuenta, a: f"Cerrar la vacante «{_titulo_vacante(db, cuenta.id, a['codigo'])}».",
        "schema": {
            "type": "function", "name": "cerrar_vacante",
            "description": "Cierra una vacante (deja de recibir postulaciones).",
            "parameters": {"type": "object", "properties": {"codigo": _p("string", "Código de la vacante")},
                            "required": ["codigo"], "additionalProperties": False},
        },
    },
    "crear_requisicion": {
        "permiso": "decisor", "ejecutar": _ejecutar_crear_requisicion,
        "resumen": lambda db, cuenta, a: f"Crear una requisición para «{a['puesto']}».",
        "schema": {
            "type": "function", "name": "crear_requisicion",
            "description": "Crea una requisición interna nueva (borrador, o directo a autorización).",
            "parameters": {"type": "object", "properties": {
                "puesto": _p("string", "Puesto solicitado"), "area": _p("string", ""),
                "motivo": _p("string", "Crecimiento | Reemplazo"), "ubicacion": _p("string", ""),
                "justificacion": _p("string", ""), "enviar_a_autorizacion": _p("boolean", "true = enviar directo a autorizar"),
            }, "required": ["puesto"], "additionalProperties": False},
        },
    },
    "autorizar_requisicion": {
        "permiso": "decisor", "ejecutar": _ejecutar_autorizar_requisicion,
        "resumen": lambda db, cuenta, a: f"Autorizar la requisición {a['codigo']} (corre el Radar Interno de empleados).",
        "schema": {
            "type": "function", "name": "autorizar_requisicion",
            "description": "Autoriza una requisición pendiente.",
            "parameters": {"type": "object", "properties": {
                "codigo": _p("string", "Código de la requisición"), "comentario": _p("string", "Opcional"),
            }, "required": ["codigo"], "additionalProperties": False},
        },
    },
    "rechazar_requisicion": {
        "permiso": "decisor", "ejecutar": _ejecutar_rechazar_requisicion,
        "resumen": lambda db, cuenta, a: f"Rechazar la requisición {a['codigo']}." + (f" Motivo: {a['comentario']}" if a.get("comentario") else ""),
        "schema": {
            "type": "function", "name": "rechazar_requisicion",
            "description": "Rechaza una requisición pendiente.",
            "parameters": {"type": "object", "properties": {
                "codigo": _p("string", "Código de la requisición"), "comentario": _p("string", "Motivo"),
            }, "required": ["codigo"], "additionalProperties": False},
        },
    },
    "convertir_requisicion_vacante": {
        "permiso": "decisor", "ejecutar": _ejecutar_convertir_requisicion,
        "resumen": lambda db, cuenta, a: f"Convertir la requisición {a['codigo']} en una vacante para buscar afuera.",
        "schema": {
            "type": "function", "name": "convertir_requisicion_vacante",
            "description": "Convierte una requisición autorizada en Vacante (cuando el Radar Interno no dio match suficiente).",
            "parameters": {"type": "object", "properties": {
                "codigo": _p("string", "Código de la requisición"),
                "generar_contenido": _p("boolean", "true = generar el contenido con IA"),
                "notas": _p("string", "Notas para la generación"),
            }, "required": ["codigo"], "additionalProperties": False},
        },
    },
    "decidir_sugerencia_movilidad": {
        "permiso": "decisor", "ejecutar": _ejecutar_decidir_sugerencia,
        "resumen": lambda db, cuenta, a: f"Marcar la sugerencia de movilidad interna #{a['sugerencia_id']} como «{a['estado']}».",
        "schema": {
            "type": "function", "name": "decidir_sugerencia_movilidad",
            "description": "Avanza/descarta una sugerencia de movilidad interna (Radar Interno).",
            "parameters": {"type": "object", "properties": {
                "codigo": _p("string", "Código de la requisición"), "sugerencia_id": _p("integer", "Id de la sugerencia"),
                "estado": _p("string", "notificada | interesado | no_interesado | avanzo | descartada"),
                "comentario": _p("string", "Opcional"),
            }, "required": ["codigo", "sugerencia_id", "estado"], "additionalProperties": False},
        },
    },
    "asignar_curso": {
        "permiso": "decisor", "ejecutar": _ejecutar_asignar_curso,
        "resumen": lambda db, cuenta, a: f"Asignar el curso {a['codigo']} a {len(a['colaborador_ids'])} colaborador(es).",
        "schema": {
            "type": "function", "name": "asignar_curso",
            "description": "Asigna un curso de capacitación a uno o más colaboradores.",
            "parameters": {"type": "object", "properties": {
                "codigo": _p("string", "Código del curso"),
                "colaborador_ids": {"type": "array", "items": {"type": "string"}, "description": "Códigos de Colaborador (ej. COL-12)"},
            }, "required": ["codigo", "colaborador_ids"], "additionalProperties": False},
        },
    },
    "crear_cliente": {
        "permiso": "decisor", "ejecutar": _ejecutar_crear_cliente,
        "resumen": lambda db, cuenta, a: f"Crear el Cliente «{a['nombre']}».",
        "schema": {
            "type": "function", "name": "crear_cliente",
            "description": "Da de alta un Cliente nuevo de la Cuenta.",
            "parameters": {"type": "object", "properties": {"nombre": _p("string", "Nombre del Cliente")},
                            "required": ["nombre"], "additionalProperties": False},
        },
    },
    "actualizar_cliente": {
        "permiso": "decisor", "ejecutar": _ejecutar_actualizar_cliente,
        "resumen": lambda db, cuenta, a: f"Actualizar el Cliente #{a['cliente_id']}.",
        "schema": {
            "type": "function", "name": "actualizar_cliente",
            "description": "Edita nombre/estado de un Cliente.",
            "parameters": {"type": "object", "properties": {
                "cliente_id": _p("integer", "Id del Cliente"), "nombre": _p("string", ""), "estado": _p("string", "Activo | Inactivo"),
            }, "required": ["cliente_id"], "additionalProperties": False},
        },
    },
    "crear_usuario": {
        "permiso": "admin", "ejecutar": _ejecutar_crear_usuario,
        "resumen": lambda db, cuenta, a: f"Crear el usuario {a['nombre']} ({a['correo']}), rol {a.get('rol', 'Usuario')}.",
        "schema": {
            "type": "function", "name": "crear_usuario",
            "description": "[Solo administrador] Da de alta una persona de RH nueva.",
            "parameters": {"type": "object", "properties": {
                "correo": _p("string", ""), "nombre": _p("string", ""), "puesto": _p("string", ""),
                "rol": _p("string", "Administrador | Usuario"), "password": _p("string", "Contraseña temporal"),
            }, "required": ["correo", "nombre", "password"], "additionalProperties": False},
        },
    },
    "actualizar_usuario": {
        "permiso": "admin", "ejecutar": _ejecutar_actualizar_usuario,
        "resumen": lambda db, cuenta, a: f"Actualizar el usuario #{a['usuario_id']}.",
        "schema": {
            "type": "function", "name": "actualizar_usuario",
            "description": "[Solo administrador] Edita nombre/puesto/rol/activo de un usuario.",
            "parameters": {"type": "object", "properties": {
                "usuario_id": _p("integer", ""), "nombre": _p("string", ""), "puesto": _p("string", ""),
                "rol": _p("string", "Administrador | Usuario"), "activo": _p("boolean", ""),
            }, "required": ["usuario_id"], "additionalProperties": False},
        },
    },
    "actualizar_regla_notificacion": {
        "permiso": "admin", "ejecutar": _ejecutar_regla_notificacion,
        "resumen": lambda db, cuenta, a: f"Actualizar la regla de notificación del evento «{a['evento']}».",
        "schema": {
            "type": "function", "name": "actualizar_regla_notificacion",
            "description": "[Solo administrador] Cambia a quién/por qué canal notifica un evento (Configuración → Notificaciones).",
            "parameters": {"type": "object", "properties": {
                "evento": _p("string", "entrevista_agendada | recordatorio_entrevista | entrevista_modificada | "
                                        "entrevista_cancelada | candidato_apto | entrevista_humana_terminada | "
                                        "recomendacion_final | contratacion | solicitud_documentos | recordatorio_documentos"),
                "candidato_correo": _p("boolean", ""), "candidato_whatsapp": _p("boolean", ""),
                "entrevistador_correo": _p("boolean", ""), "entrevistador_whatsapp": _p("boolean", ""),
                "cliente_correo": _p("boolean", ""), "cliente_whatsapp": _p("boolean", ""),
            }, "required": ["evento"], "additionalProperties": False},
        },
    },
}


def _verificar_permiso(u: Usuario, permiso: str) -> None:
    if permiso == "admin" and u.rol != "Administrador":
        raise HTTPException(403, "Solo un administrador puede hacer esto.")


# ============================================================
# Contexto "dónde está parado el usuario" — se prerresuelve, nunca se vuelve a preguntar
# ============================================================


def _texto_contexto(db: Session, cuenta: Cuenta, contexto: Optional[dict]) -> str:
    if not contexto:
        return ""
    pantalla = contexto.get("pantalla", "")
    partes = [f"El usuario está en la pantalla: {pantalla}."]
    entidad = contexto.get("entidad") or {}
    if entidad.get("tipo") == "candidato" and entidad.get("codigo"):
        try:
            ficha = r_candidatos.detalle(entidad["codigo"], db=db, _=None, cuenta=cuenta)
            partes.append(f"Candidato que está viendo ahora mismo (usa esto, no vuelvas a preguntar cuál):\n{json.dumps(ficha, ensure_ascii=False)}")
        except HTTPException:
            pass
    elif entidad.get("tipo") == "vacante" and entidad.get("codigo"):
        try:
            ficha = r_vacantes.detalle(entidad["codigo"], db=db, _=None, cuenta=cuenta)
            partes.append(f"Vacante que está viendo ahora mismo (usa esto, no vuelvas a preguntar cuál):\n{json.dumps(ficha, ensure_ascii=False)}")
        except HTTPException:
            pass
    return "\n".join(partes)


# ============================================================
# Loop principal
# ============================================================

_INSTRUCCIONES = (
    "Eres el agente global de Red Human AI (México), 'Pregunta a Red Human'. Ayudas a RH a "
    "consultar, analizar, encontrar y ejecutar acciones sobre reclutamiento, contratación y "
    "onboarding — SOLO con datos reales obtenidos de tus herramientas, nunca inventados.\n"
    "Reglas estrictas:\n"
    "1. Para cualquier dato (conteos, listas, fichas) SIEMPRE usa una herramienta de lectura — "
    "nunca respondas un número o un hecho que no venga de una llamada real.\n"
    "2. Si una búsqueda por nombre/criterio matchea más de una entidad posible, PREGUNTA cuál "
    "es antes de proponer cualquier acción — nunca adivines ni tomes la primera.\n"
    "3. Para ejecutar CUALQUIER acción que cambie datos o mande una comunicación, SOLO puedes "
    "invocar la herramienta de esa acción — nunca la ejecutas tú directamente ni prometes que "
    "'ya está hecho': el sistema la muestra como propuesta y el usuario decide confirmarla.\n"
    "4. Si te piden avisar/recordar a un destinatario específico (ej. 'solo al entrevistador') "
    "y la herramienta de recordatorio dispara el evento completo según la regla configurada de "
    "la Cuenta, dilo con claridad — nunca inventes un envío dirigido que no existe.\n"
    "5. Respuestas breves, en español mexicano, directas — esto es una barra de trabajo, no una "
    "conversación larga.\n"
    "6. Si preguntan un conteo global ('¿cuántos...?'), usa SIEMPRE pipeline_cuenta — es la "
    "misma fuente que alimenta el Tablero.\n"
    "7. CRÍTICO al llamar cualquier herramienta de búsqueda: manda ÚNICAMENTE los parámetros "
    "que el usuario pidió explícitamente. NUNCA rellenes los demás con \"\", 0 o false — "
    "OMÍTELOS por completo del JSON. Un booleano en false que tú agregaste 'por si acaso' SÍ "
    "filtra de verdad y puede esconder resultados reales que existen (ej. mandar "
    "consentimiento=false cuando el usuario no preguntó nada de consentimiento hace que la "
    "búsqueda excluya candidatos que sí tienen consentimiento). Si dudas si un filtro aplica, "
    "no lo mandes."
)


async def responder(
    db: Session, u: Usuario, cuenta_actual: Cuenta, mensaje: str, historial: List[dict],
    contexto: Optional[dict], alcance: str,
) -> dict:
    cuentas = _resolver_alcance(db, u, cuenta_actual, alcance)
    client = _client()

    uso = verificar_uso(db, u)
    if uso["mensajesHoy"] >= uso["limite"]:
        raise HTTPException(429, f"Llegaste al límite de {uso['limite']} preguntas al agente hoy. Vuelve mañana.")

    if client is None:
        _incrementar_uso(db, u)
        return {
            "texto": "El agente está en modo demo: agrega OPENAI_API_KEY en la API para respuestas reales.",
            "navegacion": [], "accionPropuesta": None, "uso": verificar_uso(db, u),
        }

    ahora_mx = datetime.now(TZ_MEXICO)
    instrucciones = (
        f"{_INSTRUCCIONES}\nHoy es {ahora_mx.isoformat(timespec='minutes')} (hora de Ciudad de México).\n"
        f"{_texto_contexto(db, cuentas[0], contexto)}"
    )

    tools = [t["schema"] for t in TOOLS_LECTURA.values()] + [t["schema"] for t in TOOLS_ESCRITURA.values()]
    mensajes = [{"role": ("user" if m["rol"] == "user" else "assistant"), "content": m["texto"]} for m in historial]
    mensajes.append({"role": "user", "content": mensaje})

    accion_propuesta = None
    for _ronda in range(MAX_RONDAS):
        resp = client.responses.create(model=MODEL, instructions=instrucciones, input=mensajes, tools=tools)
        llamadas = [it for it in resp.output if getattr(it, "type", None) == "function_call"]
        if not llamadas:
            texto = resp.output_text or ""
            break

        # Si CUALQUIER llamada de esta ronda es de escritura, se corta ahí: se propone, no se ejecuta.
        escritura = next((it for it in llamadas if it.name in TOOLS_ESCRITURA), None)
        if escritura is not None:
            args = json.loads(escritura.arguments or "{}")
            tool = TOOLS_ESCRITURA[escritura.name]
            try:
                _verificar_permiso(u, tool["permiso"])
                resumen = tool["resumen"](db, cuentas[0], args)
            except HTTPException as ex:
                accion_propuesta = None
                texto = f"No puedo proponer esa acción: {ex.detail}"
                break
            accion_propuesta = {"tool": escritura.name, "argumentos": args, "resumen": resumen}
            texto = resp.output_text or f"Esto es lo que voy a hacer: {resumen}"
            break

        mensajes += resp.output
        for it in llamadas:
            fn_lectura = TOOLS_LECTURA[it.name]
            args = _limpiar_args_lectura(json.loads(it.arguments or "{}"))
            if fn_lectura["admin"] and u.rol != "Administrador":
                resultado = {"error": "Solo un administrador puede consultar esto."}
            else:
                try:
                    resultado = fn_lectura["fn"](db, cuentas, u, **args)
                except HTTPException as ex:
                    resultado = {"error": ex.detail}
                except TypeError as ex:
                    resultado = {"error": f"Argumentos inválidos para {it.name}: {ex}"}
            mensajes.append({"type": "function_call_output", "call_id": it.call_id, "output": json.dumps(resultado, ensure_ascii=False, default=str)})
        texto = None
    else:
        texto = "Necesito que seas más específico — la pregunta requirió demasiados pasos."

    _incrementar_uso(db, u)
    return {
        "texto": texto or "",
        "navegacion": [],
        "accionPropuesta": accion_propuesta,
        "uso": verificar_uso(db, u),
    }


async def ejecutar_accion(db: Session, u: Usuario, cuenta: Cuenta, tool: str, argumentos: dict) -> Any:
    """Único punto donde una acción de escritura se ejecuta de verdad — llamado por
    POST /agente/ejecutar, disparado por un clic real de confirmación. Revalida el permiso
    aquí mismo (nunca confía en que el LLM ya lo filtró)."""
    if tool not in TOOLS_ESCRITURA:
        raise HTTPException(400, f"Acción desconocida: {tool}")
    catalogo = TOOLS_ESCRITURA[tool]
    _verificar_permiso(u, catalogo["permiso"])
    return await catalogo["ejecutar"](db, u, cuenta, argumentos)
