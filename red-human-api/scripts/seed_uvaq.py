"""Demo «Universidad Vasco de Quiroga (UVAQ)» (2026-10-15) — Cuenta, agente «Vasco», 13 sedes, 3 vacantes con su ruta
propia, escala de valores UVAQ, dictamen médico y 17 candidatos precargados (ficticios).

Solo DATOS, con las MISMAS funciones de los endpoints (`normalizar_pasos`, `proceso_para_vacante`, `routers.vacantes.crear`,
`preguntas_filtro`, `guiones`, `ubicaciones`):
  1. Cuenta «Universidad Vasco de Quiroga (UVAQ)», slug `uvaq`. El agente se presenta: «Soy Vasco, asistente de Talento
     UVAQ.» (`Cuenta.nombre_agente`).
     Plantillas de Meta (`Cuenta.plantillas_meta`, `services/plantillas_cuenta.py`): SOLO las aprobadas —
     `inicio_entrevista_rh` (1 variable: nombre) para arrancar el WhatsApp; `aviso_proceso_rh` (sin variables) para avisar al
     candidato de su cita con el jefe / Rectoría; `alerta_entrevista_asignada` para el jefe o el panel (el aviso al
     evaluador `aviso_evaluador_rh` se traduce a ella); `oferta_laboral_rh`. Ninguna otra sale: lo demás va en texto
     libre dentro de la ventana de 24 h (si está cerrada, queda pendiente y sale en cuanto la persona escriba).
  2. 13 sedes (Ubicaciones, «dirección de demo», L-V 9:00 y 12:00). Turnos MAT-01 (06:00–14:00 L-V), MAT-03
     (08:00–16:00 L-V), MIX-02 (13:00–21:00 L-V) y ADM-01 (09:00–18:00 L-V) en la vacante (`Vacante.guiones["turnos"]`) y en
     la pregunta de horario.
  3. Escala de valores UVAQ 1-5 (Dignidad, Espíritu de servicio, Honestidad, Responsabilidad, Compromiso, Trabajo en
     equipo; `services/escala_valores.py`): Operativo promedio ≥ 3.5 (alerta con 1 o 2), Jefatura ≥ 4.0 (alerta < 3),
     Dirección ≥ 4.2 (alerta < 3.5). No excluyente: si falla → «Requiere revisión».
  4. Vacantes (ruta PROPIA en cada vacante):
     V1 Auxiliar de Intendencia — Morelia (3), Pátzcuaro (1), Zamora (1); MAT-01/MAT-03/MIX-02; $9,200.
        Prefiltro por WhatsApp → Entrevista por WhatsApp (Operativo, 5 preguntas) → Psicometría (valores, Operativo) →
        Documentos → Referencias → Examen médico → Listo para contratar (SAEKO).
        Filtros: sede (excluyente), horario (excluyente), estudios < secundaria (excluyente), experiencia < 6 meses (revisión).
     V2 Jefe(a) de Departamento de Mercadotecnia — Morelia; ADM-01; $28,000.
        Solicitud web → Entrevista con avatar (Profesional: planeación, liderazgo y Caso Zamora) → Psicometría (valores,
        Jefatura) → Entrevista con el jefe → Referencias → Documentos → Examen médico → Listo para contratar (SAEKO).
        Filtros: licenciatura titulada (excluyente), experiencia < 2 años (excluyente) / 2-3 años (revisión), expectativa
        > $30,000 (revisión).
     V3 Director(a) de Planeación — Morelia; ADM-01; $55,000; trato de «usted».
        Solicitud web → Entrevista con avatar (Caso Rectoría) → Psicometría (valores, Dirección) → Panel de Rectoría →
        Referencias → Documentos → Examen médico → Listo para contratar (SAEKO).
        Filtros: maestría titulada (excluyente), 8 años de experiencia con 3 directivos (excluyente).
     Examen médico (las 3): «Apto» → la ruta sigue a «Listo para contratar (SAEKO)»; «Apto con restricciones» → «Requiere
     revisión»; «No apto» → descartado automático (decisión del usuario, `services/dictamen_medico.py`).
     SAEKO es 100 % simulado: la última actividad es «Listo para contratar (SAEKO)».
  5. 17 candidatos ficticios en su estado (con el motor de ruta PAUSADO: cero mensajes al sembrarlos).

Seguridad: sin `--aplicar` es un SIMULACRO (una transacción que se deshace); con `--aplicar`, todo o nada. Idempotente
(Cuenta por slug/nombre, sedes y vacantes por nombre/título, candidatos por teléfono ficticio dentro de la Cuenta).
Cero comunicaciones: no manda ningún mensaje.

Uso (desde red-human-api/, con el .env del ambiente; la API debe haber arrancado una vez con esta versión):
    python scripts/seed_uvaq.py              # simulacro
    python scripts/seed_uvaq.py --aplicar    # aplica
Opciones: --borrador (vacantes en Borrador) · --sin-candidatos
"""

import argparse
import asyncio
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:  # la consola de Windows (cp1252) no imprime acentos
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import func  # noqa: E402
from sqlalchemy.orm.attributes import flag_modified  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    BASE_CODIGO_EVALUACION, Candidato, Cuenta, Entrevista, Evaluacion, Postulacion, PruebaPsicometrica, Ubicacion, Usuario,
    UsuarioCuenta, Vacante, normalizar_documentos_vacante, registrar, texto_sueldo,
)
from app.routers import vacantes as rvac  # noqa: E402
from app.seed import slug_cuenta_unico  # noqa: E402
from app.services import guiones as sgui  # noqa: E402
from app.services import preguntas_filtro as pfu  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import ubicaciones as subic  # noqa: E402

ACTOR = "script:seed_uvaq"
NOMBRE = "Universidad Vasco de Quiroga (UVAQ)"
SLUG = "uvaq"
AGENTE = "Vasco, asistente de Talento UVAQ"
PRESENTACION = "Soy Vasco, asistente de Talento UVAQ."

# ============================================================ 1. plantillas de Meta (solo las aprobadas)
PLANTILLAS_APROBADAS = ["inicio_entrevista_rh", "aviso_proceso_rh", "alerta_entrevista_asignada", "oferta_laboral_rh"]
ALERTA = {"plantilla": "alerta_entrevista_asignada", "params": "evaluador_a_alerta"}
POLITICA_PLANTILLAS = {
    "permitidas": PLANTILLAS_APROBADAS,
    "mapa": {
        "inicio_entrevista_rh": {"plantilla": "inicio_entrevista_rh", "params": "solo_nombre"},
        "entrevista_video_rh": {"plantilla": "inicio_entrevista_rh", "params": "solo_nombre"},
        "aviso_evaluador_rh": ALERTA, "aviso_medico": ALERTA, "aviso_instructor": ALERTA,
        "alerta_entrevista_asig": {"plantilla": "alerta_entrevista_asignada", "params": "entrevista8_a_alerta"},
    },
    "aviso_ventana": "aviso_proceso_rh",
}

# ============================================================ 2. sedes y turnos
DEMO = "(dirección de demo)"
HORARIOS_CITAS = [{"dias": [0, 1, 2, 3, 4], "horas": ["09:00", "12:00"], "cupo": 5}]
SEDES = [  # (nombre, estado, municipio)
    ("Morelia", "Michoacán", "Morelia"), ("Pátzcuaro", "Michoacán", "Pátzcuaro"), ("Quiroga", "Michoacán", "Quiroga"),
    ("Puruándiro", "Michoacán", "Puruándiro"), ("Zamora", "Michoacán", "Zamora"),
    ("San Luis Potosí", "San Luis Potosí", "San Luis Potosí"), ("Irapuato", "Guanajuato", "Irapuato"),
    ("Zacapu", "Michoacán", "Zacapu"), ("Tacámbaro", "Michoacán", "Tacámbaro"), ("Ciudad Hidalgo", "Michoacán", "Hidalgo"),
    ("Lázaro Cárdenas", "Michoacán", "Lázaro Cárdenas"), ("Querétaro", "Querétaro", "Querétaro"),
    ("Álvaro Obregón", "Michoacán", "Álvaro Obregón"),
]
TURNOS = {"MAT-01": "06:00–14:00 L-V", "MAT-03": "08:00–16:00 L-V", "MIX-02": "13:00–21:00 L-V", "ADM-01": "09:00–18:00 L-V"}

# ============================================================ 3. vacantes
DOCS_INGRESO = [{"nombre": n} for n in ("INE (frente y reverso)", "CURP", "Acta de nacimiento", "Comprobante de domicilio",
                                        "Número de Seguridad Social (NSS)", "Constancia de Situación Fiscal (RFC)",
                                        "Comprobante de estudios")]  # 7 documentos
SAEKO = "Listo para contratar (SAEKO)"
MEDICO = {"id": "medico", "tipo": "medica", "nombre": "Examen médico", "etapa": "Entrevista Humana", "tipo_agenda": "rh",
          "no_apto_descarta": True, "restricciones_revision": True}


def _psico(nivel: str, pruebas: list, depende: str) -> dict:
    from app.services.escala_valores import NIVELES

    return {"id": "psicometria", "tipo": "psicometrica", "nombre": f"Psicometría · valores UVAQ ({NIVELES[nivel]['nombre']})",
            "etapa": "Entrevista IA", "pruebas": pruebas, "evaluacion_psicometria": {"modalidad": "valores", "nivel": nivel},
            "depende_de": [depende]}


def _cola(previo: str) -> list:
    """Referencias → Documentos → Examen médico (en orden, en Filtro humano) → Listo para contratar (SAEKO)."""
    return [
        {"id": "referencias", "tipo": "referencias", "nombre": "Referencias", "etapa": "Entrevista Humana", "depende_de": [previo]},
        {"id": "documentos", "tipo": "documentos", "nombre": "Documentos", "etapa": "Entrevista Humana", "depende_de": ["referencias"]},
        {**MEDICO, "depende_de": ["documentos"]},
        {"id": "saeko", "tipo": "otra", "nombre": SAEKO, "etapa": "Contratación"},
    ]


def pasos_v1(pruebas: list) -> list:
    return [
        {"id": "solicitud", "tipo": "solicitud_sin_cv", "etapa": "Prefiltro"},
        {"id": "prefiltro-whatsapp", "tipo": "prefiltro_whatsapp", "nombre": "Prefiltro por WhatsApp", "etapa": "Prefiltro"},
        {"id": "entrevista-whatsapp", "tipo": "entrevista_whatsapp", "nombre": "Entrevista por WhatsApp", "etapa": "Entrevista IA",
         "enfoque": "operativo", "tipo_entrevista": "operativo"},
        _psico("operativo", pruebas, "entrevista-whatsapp"),
        {"id": "documentos", "tipo": "documentos", "nombre": "Documentos", "etapa": "Entrevista Humana"},
        {"id": "referencias", "tipo": "referencias", "nombre": "Referencias", "etapa": "Entrevista Humana", "depende_de": ["documentos"]},
        {**MEDICO, "depende_de": ["referencias"]},
        {"id": "saeko", "tipo": "otra", "nombre": SAEKO, "etapa": "Contratación"},
    ]


def _con_avatar(nivel: str, pruebas: list, humana: dict) -> list:
    return [
        {"id": "solicitud", "tipo": "solicitud_sin_cv", "etapa": "Prefiltro"},
        {"id": "entrevista-avatar", "tipo": "entrevista_agente", "nombre": "Entrevista con avatar", "etapa": "Entrevista IA",
         "enfoque": "profesional", "tipo_entrevista": "profesional"},
        _psico(nivel, pruebas, "entrevista-avatar"),
        humana, *_cola(humana["id"]),
    ]


def pasos_v2(pruebas: list, evaluador_id: int) -> list:
    return _con_avatar("jefatura", pruebas, {"id": "entrevista-jefe", "tipo": "entrevista_humana", "nombre": "Entrevista con el jefe",
                                             "etapa": "Entrevista Humana", "tipo_entrevista": "jefe_directo", "tipo_agenda": "rh",
                                             "config": {"evaluador": {"tipo": "interno", "usuario_id": evaluador_id}}})


def pasos_v3(pruebas: list, evaluador_id: int) -> list:
    return _con_avatar("direccion", pruebas, {"id": "panel-rectoria", "tipo": "entrevista_humana", "nombre": "Panel de Rectoría",
                                              "etapa": "Entrevista Humana", "tipo_entrevista": "general", "tipo_agenda": "rh",
                                              "config": {"evaluador": {"tipo": "interno", "usuario_id": evaluador_id}}})


V1, V2, V3 = "Auxiliar de Intendencia", "Jefe(a) de Departamento de Mercadotecnia", "Director(a) de Planeación"
CASO_ZAMORA = "La matrícula de primer ingreso en Zamora bajó 15 %. ¿Qué haría en los próximos 90 días?"
CASO_RECTORIA = ("Rectoría le pide decidir si abre o cierra la oferta de licenciaturas en una sede con baja matrícula. ¿Qué "
                 "información pide y cómo decide?")
Q_SEDE = "¿Puedes trabajar en Morelia, Pátzcuaro o Zamora?"
Q_TURNO = "¿Puedes trabajar en alguno de estos turnos de lunes a viernes: 6:00 a 14:00, 8:00 a 16:00 o 13:00 a 21:00?"
Q_ESTUDIOS = "¿Cuál es tu último grado de estudios?"
Q_EXP_INT = "¿Cuánto tiempo de experiencia tienes en limpieza o intendencia?"
Q_TITULO = "¿Tienes licenciatura titulada?"
Q_EXP_MKT = "¿Cuántos años de experiencia tienes en mercadotecnia?"
Q_SUELDO_MKT = "¿Cuál es tu expectativa de sueldo mensual?"
Q_MAESTRIA = "¿Tiene maestría titulada?"
Q_EXP_DIR = "¿Tiene al menos 8 años de experiencia profesional, con 3 o más en puestos directivos?"

VACANTES = [
    {"clave": "V1", "titulo": V1, "area": "Servicios generales", "seniority": "Junior", "sueldo": 9200, "sedes": ["Morelia", "Pátzcuaro", "Zamora"],
     "plazas": {"Morelia": 3, "Pátzcuaro": 1, "Zamora": 1}, "turnos": ["MAT-01", "MAT-03", "MIX-02"], "trato": "tu",
     "descripcion": "Limpieza y mantenimiento de aulas, oficinas y áreas comunes de la UVAQ, en turno matutino o mixto.",
     "responsabilidades": ["Limpiar aulas, oficinas y sanitarios", "Mantener ordenadas las áreas comunes", "Reportar desperfectos"],
     "beneficios": ["Prestaciones de ley", "Uniforme"], "documentos": DOCS_INGRESO,
     # (requisito, pregunta, descarta)
     "filtros": [("Disponibilidad para trabajar en Morelia, Pátzcuaro o Zamora", Q_SEDE, True),
                 ("Disponibilidad en turno MAT-01, MAT-03 o MIX-02", Q_TURNO, True),
                 ("Secundaria terminada", Q_ESTUDIOS, True),
                 ("Experiencia en limpieza o intendencia", Q_EXP_INT, False)],
     "revision": [{"pregunta": Q_EXP_INT, "tipo": "minimo", "valor": 0.5, "unidad": "anios",
                   "motivo": "Experiencia menor a 6 meses en limpieza o intendencia"}]},
    {"clave": "V2", "titulo": V2, "area": "Mercadotecnia", "seniority": "Jefatura", "sueldo": 28000, "sedes": ["Morelia"],
     "plazas": {"Morelia": 1}, "turnos": ["ADM-01"], "trato": "tu",
     "descripcion": ("Planear y dirigir la mercadotecnia institucional y las campañas de admisiones de la UVAQ, con un equipo "
                     "a cargo."),
     "responsabilidades": ["Elaborar y dar seguimiento al plan anual de mercadotecnia", "Dirigir las campañas de admisiones",
                           "Liderar al equipo de mercadotecnia", "Medir resultados de matrícula por campus"],
     "beneficios": ["Prestaciones de ley"], "documentos": [dict(d, etapa="Entrevista Humana") for d in DOCS_INGRESO],
     "filtros": [("Licenciatura titulada", Q_TITULO, True),
                 ("2 años de experiencia en mercadotecnia", Q_EXP_MKT, True),
                 ("Expectativa salarial", Q_SUELDO_MKT, False)],
     "revision": [{"pregunta": Q_EXP_MKT, "tipo": "minimo", "valor": 3, "unidad": "anios",
                   "motivo": "Experiencia de 2 a 3 años en mercadotecnia"},
                  {"pregunta": Q_SUELDO_MKT, "tipo": "maximo", "valor": 30000, "unidad": "pesos",
                   "motivo": "Expectativa salarial mayor a $30,000"}],
     "guion": {"enfoque": "profesional", "temas": ["Planeación", "Liderazgo", "Caso Zamora"],
               "preguntas": ["¿Cuál ha sido tu último puesto en mercadotecnia y qué resultados lograste?",
                             "¿Cómo armas un plan anual de mercadotecnia y cómo das seguimiento a sus metas?",
                             "¿Cómo repartes el trabajo de tu equipo cuando hay varias campañas al mismo tiempo?",
                             "¿Cómo mides si una campaña de admisiones funcionó?",
                             CASO_ZAMORA]}},
    {"clave": "V3", "titulo": V3, "area": "Planeación", "seniority": "Dirección", "sueldo": 55000, "sedes": ["Morelia"],
     "plazas": {"Morelia": 1}, "turnos": ["ADM-01"], "trato": "usted",
     "descripcion": "Dirigir la planeación estratégica institucional de la UVAQ y dar seguimiento a sus indicadores con Rectoría.",
     "responsabilidades": ["Coordinar el plan estratégico institucional", "Dar seguimiento a indicadores por campus",
                           "Preparar análisis para las decisiones de Rectoría"],
     "beneficios": ["Prestaciones de ley"], "documentos": [dict(d, etapa="Entrevista Humana") for d in DOCS_INGRESO],
     "filtros": [("Maestría titulada", Q_MAESTRIA, True),
                 ("8 años de experiencia con 3 en puestos directivos", Q_EXP_DIR, True)],
     "revision": [],
     "guion": {"enfoque": "profesional", "temas": ["Planeación estratégica", "Indicadores", "Caso Rectoría"],
               "preguntas": ["¿Cuál ha sido su experiencia dirigiendo áreas de planeación?",
                             "¿Cómo construye y da seguimiento a un plan estratégico institucional?",
                             "¿Qué indicadores usa para medir el avance de una institución educativa?",
                             "¿Cómo presenta una recomendación difícil a la alta dirección?",
                             CASO_RECTORIA]}},
]
# Entrevista por WhatsApp de 5 preguntas: ¿Trabajas actualmente? · empresa · qué hacías · cuánto tiempo · por qué saliste
QUITADAS_WA = ["sin_trabajo", "puesto", "otro", "conf_puesto", "conf_sueldo", "conf_traslado", "inicio"]


class Abortar(Exception):
    pass


def ok(t: str) -> None:
    print(f"  ✓ {t}")


def nota(t: str) -> None:
    print(f"  · {t}")


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================ configuración

def asegurar_cuenta(db) -> Cuenta:
    cu = (db.query(Cuenta).filter((Cuenta.slug == SLUG) | (func.lower(Cuenta.nombre) == NOMBRE.lower())).order_by(Cuenta.id).first())
    if cu is not None and cu.estado == "Eliminada":
        raise Abortar(f"La Cuenta «{cu.nombre_visible}» está eliminada. Restáurala y vuelve a correr.")
    if cu is None:
        cu = Cuenta(nombre=NOMBRE, nombre_comercial=NOMBRE, razon_social="Universidad Vasco de Quiroga, A.C.",
                    contacto_nombre="Talento UVAQ", canal_mensajeria="whatsapp", estado="Activa")
        db.add(cu)
        db.flush()
        cu.slug = slug_cuenta_unico(db, "uvaq", cu.id)
        registrar(db, ACTOR, "cuenta_creada", "cuenta", str(cu.id), {"nombre": NOMBRE})
        ok(f"Cuenta creada: {NOMBRE} (id {cu.id}, slug {cu.slug})")
    else:
        nota(f"Cuenta existente: {cu.nombre_visible} (id {cu.id}, slug {cu.slug})")
    cu.nombre, cu.nombre_comercial, cu.canal_mensajeria = NOMBRE, NOMBRE, "whatsapp"
    if cu.nombre_agente != AGENTE or (cu.plantillas_meta or {}) != POLITICA_PLANTILLAS:
        cu.nombre_agente = AGENTE
        cu.plantillas_meta = dict(POLITICA_PLANTILLAS)
        flag_modified(cu, "plantillas_meta")
        registrar(db, ACTOR, "cuenta_configurada", "cuenta", str(cu.id), {"agente": AGENTE, "plantillas": PLANTILLAS_APROBADAS})
        ok(f"Agente «{AGENTE}» · plantillas de Meta: {', '.join(PLANTILLAS_APROBADAS)}")
    with db.begin_nested():
        sproc.asegurar_rutas_base(db, cu.id, ACTOR)
    db.flush()
    from app.services import ia, plantillas_cuenta

    ia.precargar_agente(NOMBRE, AGENTE)  # la validación corre dentro de esta misma transacción (aún sin commit)
    plantillas_cuenta.precargar(cu.id, POLITICA_PLANTILLAS)
    return cu


def vincular_admins(db, cu: Cuenta) -> Usuario:
    admins = [u for u in db.query(Usuario).filter(Usuario.rol == "Administrador").order_by(Usuario.id).all() if getattr(u, "activo", True)]
    if not admins:
        raise Abortar("No hay ningún Administrador activo: arranca la API una vez (siembra el admin) y vuelve a correr.")
    for u in admins:
        if not db.query(UsuarioCuenta).filter(UsuarioCuenta.usuario_id == u.id, UsuarioCuenta.cuenta_id == cu.id).first():
            db.add(UsuarioCuenta(usuario_id=u.id, cuenta_id=cu.id))
            ok(f"Acceso a la Cuenta para {u.correo}")
    db.flush()
    return admins[0]


def asegurar_sedes(db, cu: Cuenta) -> dict:
    salida = {}
    for nombre, estado, municipio in SEDES:
        u = db.query(Ubicacion).filter(Ubicacion.cuenta_id == cu.id, Ubicacion.nombre == nombre).first()
        if u is None:
            u = Ubicacion(cuenta_id=cu.id, nombre=nombre, direccion=f"Campus {nombre} {DEMO}", indicaciones="Presenta tu INE original.",
                          ubicacion_estado=estado, ubicacion_municipio=municipio,
                          horarios_citas=subic.normalizar_horarios(HORARIOS_CITAS), activa=True)
            db.add(u)
            db.flush()
            registrar(db, ACTOR, "ubicacion_creada", "ubicacion", str(u.id), {"nombre": nombre})
        u.activa = True
        salida[nombre] = u
    ok(f"{len(SEDES)} sedes: {', '.join(n for n, _, _ in SEDES)}")
    return salida


def asegurar_prueba(db, cu: Cuenta, u: Usuario) -> int:
    """Batería del proveedor que se aplica junto con la escala de valores UVAQ (Cleaver)."""
    from app.routers.evaluaciones import _clave_automatica

    pr = (db.query(PruebaPsicometrica).filter(PruebaPsicometrica.cuenta_id == cu.id, PruebaPsicometrica.id_proveedor == "1")
          .order_by(PruebaPsicometrica.id).first())
    if pr is None:
        pr = PruebaPsicometrica(cuenta_id=cu.id, nombre="Cleaver + valores UVAQ", tipo="prueba", modo="integrada",
                                proveedor="Psicométricas.mx", id_proveedor="1", activa=True, creado_por=u.nombre, clave="")
        db.add(pr)
        db.flush()
        pr.clave = _clave_automatica(db, cu.id, pr.nombre, pr.id)
    return pr.id


def configurar_preguntas(v: Vacante, d: dict) -> None:
    reqs = [r for r, _, _ in d["filtros"]]
    if rvac.requisitos_lista(v.requisitos or "") != reqs:
        v.requisitos = rvac.SEPARADOR_REQUISITOS.join(reqs)
    lista = [dict(q) for q in pfu.de_vacante(v)]
    previas = []
    for req, pregunta, descarta in d["filtros"]:
        q = next((x for x in lista if x.get("requisito") == req and not x.get("propia")), {"requisito": req, "indispensable": True})
        q = {**q, "pregunta": pregunta, "descarta": descarta}
        q.pop("pregunta_web", None)
        if pregunta in (Q_EXP_INT, Q_EXP_MKT, Q_SUELDO_MKT):
            q["tipo"] = "numero" if pregunta != Q_EXP_INT else "abierta"  # el número se compara con su regla (no Sí/No)
        elif pregunta != Q_ESTUDIOS:
            q["tipo"], q["pregunta_web"] = "si_no", pregunta
        previas.append(q)
    pfu.sincronizar(v, entrada=previas)
    g = dict(v.guiones or {})
    g["filtros_revision"] = d["revision"]
    g["turnos"] = {t: TURNOS[t] for t in d["turnos"]}
    g["plazas_por_sede"] = d["plazas"]
    if d["clave"] == "V1":
        g["entrevista_whatsapp_base"] = {"quitadas": list(QUITADAS_WA), "propias": []}
    v.guiones = g
    flag_modified(v, "guiones")


def asegurar_vacante(db, cu: Cuenta, u: Usuario, sedes: dict, d: dict, pasos: list, publicar: bool) -> Vacante:
    v = db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == d["titulo"], Vacante.estado != "Eliminada").first()
    ids = [sedes[s].id for s in d["sedes"]]
    if v is None:
        datos = rvac.CrearIn(
            titulo=d["titulo"], area=d["area"], seniority=d["seniority"], ubicacion_estado="Michoacán", ubicacion_municipio="Morelia",
            modalidad="Presencial", sueldo_desde=d["sueldo"], sueldo_hasta=d["sueldo"], sueldo_moneda="MXN", sueldo_periodicidad="mensual",
            descripcion=d["descripcion"], resumen=d["descripcion"], requisitos_indispensables=[r for r, _, _ in d["filtros"]],
            responsabilidades=d["responsabilidades"], beneficios=d["beneficios"], proceso={"pasos": pasos},
            documentos_solicitados=d["documentos"], generar_si_falta=False, publicar=False, plataformas=[],
            preguntas_requisitos=[{"requisito": r, "pregunta": q} for r, q, _ in d["filtros"]], ubicaciones_ids=ids,
            posiciones=sum(d["plazas"].values()),
        )
        try:
            asyncio.run(rvac.crear(datos, db, u, cu))
        except HTTPException as e:
            raise Abortar(f"Vacante «{d['titulo']}»: {e.detail}")
        v = db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == d["titulo"]).order_by(Vacante.id.desc()).first()
        ok(f"Vacante {v.codigo} {v.titulo} · {v.sueldo}")
    else:
        nota(f"Vacante existente: {v.codigo} {v.titulo}")
        try:
            nuevo = sproc.proceso_para_vacante(db, cu.id, v.proceso or {}, {"pasos": pasos})
        except sproc.ErrorProceso as e:
            raise Abortar(f"{v.codigo}: {e.mensaje}")
        if nuevo != (v.proceso or {}):
            v.proceso = nuevo
            ok(f"{v.codigo}: ruta actualizada (v{nuevo.get('version')}); las postulaciones conservan su copia")
        v.sueldo_desde = v.sueldo_hasta = d["sueldo"]
        v.sueldo = texto_sueldo(d["sueldo"], d["sueldo"], "MXN", "mensual")
        v.documentos_solicitados = normalizar_documentos_vacante(d["documentos"])
        subic.asignar(db, v, ids)
        v.posiciones = sum(d["plazas"].values())
    v.trato = d["trato"]
    v.cliente_id = None  # el candidato ve a la UVAQ
    configurar_preguntas(v, d)
    rvac._enfoque_por_ruta(db, v)
    db.flush()
    if d.get("guion"):
        sgui.guardar_seccion(v, "entrevista_avatar", d["guion"], u.nombre)
    else:
        sgui.generar_para_vacante(db, v, u.nombre, sobrescribir_editadas=True, solo=["entrevista_whatsapp"])
    if publicar and v.estado != "Publicada":
        v.estado, v.publicada_en = "Publicada", v.publicada_en or _ahora()
        registrar(db, ACTOR, "vacante_publicada", "vacante", v.codigo, {"titulo": v.titulo})
    db.flush()
    return v


# ============================================================ candidatos precargados

VALORES_OK = {"Dignidad": 4, "Espíritu de servicio": 4, "Honestidad": 5, "Responsabilidad": 4, "Compromiso": 4, "Trabajo en equipo": 4}


def _nueva(db, cu, v, nombre, tel, respuestas=None, etapa="Prefiltro", correo=""):
    from app.routers.candidatos import _crear_candidato, crear_postulacion

    c = db.query(Candidato).filter(Candidato.cuenta_id == cu.id, Candidato.telefono == tel).first()
    if c is not None and any(x.vacante_id == v.id for x in c.postulaciones):
        return None  # idempotente: ya sembrado
    c = c or _crear_candidato(db, cu.id, nombre, "Portal", False, telefono=tel, correo=correo or f"{tel}@demo-uvaq.mx")
    p = crear_postulacion(db, c, v, cu.id, "formulario", consentimiento=True, etapa=etapa)
    a = {"motor_ruta": {"pausado": True}, "demo_uvaq": True}  # sembrado: el motor no le escribe a nadie
    if respuestas:
        a["respuestas_web"] = [{"pregunta": q, "respuesta": r} for q, r in respuestas.items()]
    p.analisis = a
    db.flush()
    return p


def _analisis(p, **campos):
    a = dict(p.analisis or {})
    a.update(campos)
    p.analisis = a
    flag_modified(p, "analisis")


def _descartar(p, motivo):
    p.historial = list(p.historial or []) + [{"evento": "descartado", "usuario": "Red Human", "fecha": _ahora().isoformat(),
                                              "motivo": motivo, "texto": f"Descartado por Red Human — {motivo}"}]
    _analisis(p, prefiltro_web={"resultado": "no_cumple", "motivo": motivo, "en": _ahora().isoformat(), "decidido_por": "Red Human"},
              prefiltro_resultado="no_cumple")
    p.estado, p.prefiltro_completo = "no_cumple", True
    p.cerrar("descartado")


def _prefiltro_cumple(p):
    _analisis(p, prefiltro_web={"resultado": "cumple", "motivo": "Cumple los requisitos indispensables", "en": _ahora().isoformat(),
                                "decidido_por": "Red Human", "conversacional": True},
              prefiltro_resultado="cumple", solicitud_web={"resultado": "cumple", "en": _ahora().isoformat()})
    p.estado, p.prefiltro_completo = "cumple", True


def _entrevista(db, p, tipo, estado, evaluacion=None, transcript=None):
    e = Entrevista(codigo="TMP", candidato_id=p.candidato_id, postulacion_id=p.id, token=secrets.token_urlsafe(16), tipo=tipo,
                   estado=estado, evaluacion=evaluacion or {}, transcript=transcript or [], consentimiento=True,
                   consentimiento_fecha=_ahora(), iniciada_en=_ahora() - timedelta(hours=2),
                   finalizada_en=_ahora() - timedelta(hours=1) if estado == "evaluada" else None)
    db.add(e)
    db.flush()
    e.codigo = f"ENT-{300 + e.id}"
    return e


def _eval_entrevista(score=None, operativa=False, texto="Experiencia sólida y respuestas concretas."):
    ev = {"resumen": texto, "fortalezas": ["Respuestas concretas con ejemplos"], "riesgos": [], "areas_desarrollo": [],
          "calif_experiencia": 8.0, "calif_comunicacion": 8.0, "match_perfil": score or 80, "recomendacion": "avanzar",
          "evidencia": texto, "faltante": [], "inconsistencias": []}
    if operativa:
        ev.update({"score_entrevista": None, "operativa": {"recomendacion": "recomendable", "requisitos": {}}})
    else:
        ev["score_entrevista"] = score
    return ev


def _evaluacion(db, p, tipo, paso_id, **campos):
    ev = Evaluacion(codigo="TMP", cuenta_id=p.cuenta_id, postulacion_id=p.id, candidato_id=p.candidato_id, vacante_id=p.vacante_id,
                    tipo=tipo, forma=campos.pop("forma", "asignada"), token_evaluador=secrets.token_urlsafe(16), paso_id=paso_id,
                    registrada_por=ACTOR, registrada_via="sistema", **campos)
    db.add(ev)
    db.flush()
    ev.codigo = f"EVA-{BASE_CODIGO_EVALUACION + ev.id}"
    return ev


def _psicometria(db, p, pruebas, valores=None, en_curso=False):
    from app.services import evaluaciones as sev

    if valores is None:
        return _evaluacion(db, p, "psicometrica", "psicometria", forma="integrada", proveedor="Psicométricas.mx", id_proveedor="1",
                           pruebas=pruebas, estado="pendiente", enviada_en=_ahora() - timedelta(hours=5),
                           iniciada_en=_ahora() - timedelta(hours=1) if en_curso else None)
    ev = _evaluacion(db, p, "psicometrica", "psicometria", forma="integrada", proveedor="Psicométricas.mx", id_proveedor="1",
                     pruebas=pruebas, estado="con_resultado", resultado_json={"valores": dict(valores)}, realizada_por="Psicométricas.mx",
                     registrada_en=_ahora() - timedelta(hours=3), enviada_en=_ahora() - timedelta(days=1))
    sev.dictaminar_automatico(db, ev)  # escala de valores UVAQ: favorable o «Requiere revisión»
    return ev


def _cita(dias=1, hora=10):
    from app.fechas import TZ_ORG

    local = (datetime.now(TZ_ORG) + timedelta(days=dias)).replace(hour=hora, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


def _humana(db, p, paso_id, u, nombre_evaluador, resultado=None):
    campos = dict(evaluador_tipo="interno", evaluador_usuario_id=u.id, evaluador_nombre=nombre_evaluador, evaluador_correo=u.correo or "",
                  cita_fecha_hora=_cita(1, 10), cita_hasta=_cita(1, 11), cita_modalidad="Presencial", cita_direccion=f"Campus Morelia {DEMO}")
    if resultado:
        campos.update(estado="con_resultado", conclusion=resultado, realizada_por=nombre_evaluador, registrada_en=_ahora())
    return _evaluacion(db, p, "entrevista_humana", paso_id, **campos)


def _referencias(db, p):
    return _evaluacion(db, p, "referencias", "referencias", estado="con_resultado", conclusion="favorable", realizada_por="RH UVAQ",
                       registrada_en=_ahora(), revisada_en=_ahora(), revisada_por="RH UVAQ")


def _medica(db, p, conclusion=None):
    campos = dict(evaluador_tipo="externo", evaluador_nombre="Clínica de la UVAQ (demo)", consentimiento="otorgado",
                  consentimiento_en=_ahora() - timedelta(days=1), cita_fecha_hora=_cita(1, 9), cita_hasta=_cita(1, 10),
                  cita_modalidad="Presencial", cita_direccion=f"Clínica UVAQ, Campus Morelia {DEMO}")
    if conclusion:
        campos.update(estado="con_resultado", conclusion=conclusion, realizada_por="Clínica de la UVAQ (demo)", registrada_en=_ahora(),
                      revisada_en=_ahora(), revisada_por="RH UVAQ")
    return _evaluacion(db, p, "medica", "medico", **campos)


def _expediente(db, p, u, aprobados: int):
    from app.routers.candidatos import _abrir_expediente
    from app.services import documentos_vacante as sdv

    exp = p.expediente or _abrir_expediente(db, p, u)
    docs = sdv.de_vacante(p.vacante)
    sdv.asegurar_documentos(db, exp, docs)
    db.flush()
    claves = [d["nombre"] for d in docs]
    for i, nombre in enumerate(claves):
        doc = next((x for x in exp.documentos if x.tipo == nombre), None)
        if doc is None:
            continue
        doc.solicitado_en, doc.solicitado_canal = _ahora() - timedelta(days=1), "whatsapp"
        if i < aprobados:
            doc.archivo, doc.nombre_archivo, doc.mime = f"demo/uvaq/{p.codigo}-{i}.pdf", f"{nombre}.pdf", "application/pdf"
            doc.estado, doc.revisado_por, doc.subido_en, doc.recibido_en = "recibido", "RH UVAQ", _ahora(), _ahora()
    db.flush()
    return exp


def sembrar_candidatos(db, cu, u, vacantes: dict, pruebas: list) -> int:
    v1, v2, v3 = vacantes["V1"], vacantes["V2"], vacantes["V3"]
    n = 0
    hoy = _ahora()

    def tel(i):
        return f"44399{i:05d}"

    # ---------------- V1 · Auxiliar de Intendencia
    def v1_web():
        return None  # V1 no tiene preguntas en la solicitud: el prefiltro va por WhatsApp

    p = _nueva(db, cu, v1, "María López Hernández", tel(1))  # Sin iniciar
    if p:
        n += 1
    p = _nueva(db, cu, v1, "José Luis Ramírez Torres", tel(2))  # En curso
    if p:
        from app.services import prefiltro_conversacional as pconv

        crit = pconv.criterios_de(v1, p)
        e = {"respuestas": {crit[0]["id"]: {"respuesta": "Sí", "valor": "si", "fuente": "whatsapp", "en": hoy.isoformat()}},
             "pendiente": crit[1]["id"] if len(crit) > 1 else None, "preguntadas": [c["id"] for c in crit[:2]], "web_importada": True}
        _analisis(p, **{pconv.ENTIDAD: e, "plantilla_inicio": {"enviada_en": hoy.isoformat(), "respondida_en": hoy.isoformat()}})
        n += 1
    p = _nueva(db, cu, v1, "Ana Karen Morales Díaz", tel(3))  # No cumple turno
    if p:
        _descartar(p, "No cumple el requisito indispensable: Disponibilidad en turno MAT-01, MAT-03 o MIX-02 (respondió: «No, solo "
                      "puedo en la noche»)")
        n += 1
    p = _nueva(db, cu, v1, "Rosa Elena Villanueva Cruz", tel(4))  # Revisión por experiencia
    if p:
        motivo = "Requiere revisión: Experiencia menor a 6 meses en limpieza o intendencia (respondió: «4 meses»)"
        _analisis(p, prefiltro_web={"resultado": "revision", "motivo": motivo, "en": hoy.isoformat(), "decidido_por": "Red Human",
                                    "conversacional": True, "requiere_revision": True}, prefiltro_resultado="revision")
        p.estado, p.prefiltro_completo = "revision", True
        p.historial = list(p.historial or []) + [{"evento": "requiere_revision", "usuario": "Red Human", "fecha": hoy.isoformat(),
                                                  "motivo": motivo, "texto": f"Prefiltro: {motivo}. RH decide si continúa o se descarta."}]
        n += 1
    p = _nueva(db, cu, v1, "Juan Carlos Pérez Solís", tel(5), etapa="Entrevista IA")  # Entrevista por WhatsApp en curso
    if p:
        _prefiltro_cumple(p)
        _entrevista(db, p, "whatsapp", "en_curso", transcript=[
            {"rol": "assistant", "texto": "Listo, Juan Carlos. Ahora te haré unas preguntas sobre tu experiencia. ¿Trabajas actualmente?"},
            {"rol": "user", "texto": "Sí, en una empresa de limpieza"},
            {"rol": "assistant", "texto": "¿Cómo se llama la empresa donde trabajas?"}])
        n += 1

    def v1_hasta_psico(p, valores):
        _prefiltro_cumple(p)
        _entrevista(db, p, "whatsapp", "evaluada", _eval_entrevista(operativa=True))
        return _psicometria(db, p, pruebas, valores)

    p = _nueva(db, cu, v1, "Miguel Ángel Ortiz Reyes", tel(6), etapa="Entrevista IA")  # Revisión psicométrica
    if p:
        v1_hasta_psico(p, {**VALORES_OK, "Espíritu de servicio": 2, "Trabajo en equipo": 3, "Compromiso": 3})
        n += 1
    p = _nueva(db, cu, v1, "Laura Gutiérrez Mendoza", tel(7), etapa="Entrevista Humana")  # Documentos 5/7
    if p:
        v1_hasta_psico(p, VALORES_OK)
        _expediente(db, p, u, aprobados=5)
        n += 1
    p = _nueva(db, cu, v1, "Sandra Jiménez Castro", tel(8), etapa="Entrevista Humana")  # Examen médico agendado
    if p:
        v1_hasta_psico(p, VALORES_OK)
        _expediente(db, p, u, aprobados=7)
        _referencias(db, p)
        _medica(db, p)
        n += 1
    p = _nueva(db, cu, v1, "Raúl Esquivel Navarro", tel(9), etapa="Contratación")  # Listo para contratar, expediente completo
    if p:
        v1_hasta_psico(p, {**VALORES_OK, "Honestidad": 5, "Compromiso": 5})
        _expediente(db, p, u, aprobados=7)
        _referencias(db, p)
        _medica(db, p, "apto")
        n += 1

    # ---------------- V2 · Jefe(a) de Mercadotecnia
    def web2(titulo="Sí", exp="5 años", sueldo="28,000"):
        return {Q_TITULO: titulo, Q_EXP_MKT: exp, Q_SUELDO_MKT: sueldo}

    p = _nueva(db, cu, v2, "Daniela Herrera Fuentes", tel(11), web2(), etapa="Entrevista IA")  # Cumple
    if p:
        _analisis(p, solicitud_web={"resultado": "cumple", "en": hoy.isoformat()})
        p.estado = "cumple"
        n += 1
    p = _nueva(db, cu, v2, "Andrés Salgado Vega", tel(12), web2(sueldo="35,000"))  # Revisión por salario
    if p:
        _analisis(p, solicitud_web={"resultado": "cumple", "en": hoy.isoformat(),
                                    "revision": ["Expectativa salarial mayor a $30,000 (respondió: «35,000»)"]})
        p.estado = "revision"
        n += 1
    p = _nueva(db, cu, v2, "Karla Domínguez Ríos", tel(13), web2(titulo="No"))  # No cumple título
    if p:
        _descartar(p, "No cumple el requisito indispensable: Licenciatura titulada (respondió: «No»)")
        n += 1
    p = _nueva(db, cu, v2, "Roberto Aguilar Méndez", tel(14), web2(exp="6 años"), etapa="Entrevista IA")  # Avatar 82/100
    if p:
        _analisis(p, solicitud_web={"resultado": "cumple", "en": hoy.isoformat()})
        p.estado = "cumple"
        _entrevista(db, p, "avatar", "evaluada", _eval_entrevista(82, texto="Buen dominio de planeación; resolvió el Caso Zamora "
                                                                          "con un plan de 90 días por etapas."))
        n += 1
    p = _nueva(db, cu, v2, "Eduardo Campos Bravo", tel(15), web2(exp="7 años"), etapa="Entrevista Humana")  # Entrevista jefe agendada
    if p:
        _analisis(p, solicitud_web={"resultado": "cumple", "en": hoy.isoformat()})
        p.estado = "cumple"
        _entrevista(db, p, "avatar", "evaluada", _eval_entrevista(85))
        _psicometria(db, p, pruebas, {**VALORES_OK, "Honestidad": 5, "Compromiso": 5})
        _humana(db, p, "entrevista-jefe", u, "Jefe de Comunicación Institucional (demo)")
        n += 1

    # ---------------- V3 · Director(a) de Planeación
    def web3(maestria="Sí", exp="Sí"):
        return {Q_MAESTRIA: maestria, Q_EXP_DIR: exp}

    p = _nueva(db, cu, v3, "Claudia Morales Echeverría", tel(21), web3(), etapa="Entrevista Humana")  # Panel agendado
    if p:
        _analisis(p, solicitud_web={"resultado": "cumple", "en": hoy.isoformat()})
        p.estado = "cumple"
        _entrevista(db, p, "avatar", "evaluada", _eval_entrevista(88, texto="Respondió el Caso Rectoría pidiendo matrícula, costo por "
                                                                          "alumno y demanda regional antes de decidir."))
        _psicometria(db, p, pruebas, {"Dignidad": 5, "Espíritu de servicio": 4, "Honestidad": 5, "Responsabilidad": 4.5,
                                      "Compromiso": 4.5, "Trabajo en equipo": 4})  # promedio 4.5
        _humana(db, p, "panel-rectoria", u, "Panel de Rectoría (demo)")
        _expediente(db, p, u, aprobados=7)
        n += 1
    p = _nueva(db, cu, v3, "Sergio Rentería Galván", tel(22), web3(), etapa="Entrevista IA")  # Psicometría en curso
    if p:
        _analisis(p, solicitud_web={"resultado": "cumple", "en": hoy.isoformat()})
        p.estado = "cumple"
        _entrevista(db, p, "avatar", "evaluada", _eval_entrevista(79))
        _psicometria(db, p, pruebas, None, en_curso=True)
        n += 1
    p = _nueva(db, cu, v3, "Fernando Ibarra Lozano", tel(23), web3(maestria="No"))  # No cumple maestría
    if p:
        _descartar(p, "No cumple el requisito indispensable: Maestría titulada (respondió: «No»)")
        n += 1
    db.flush()
    return n


# ============================================================ validación

ESTADOS_ESPERADOS = {  # nombre → (vacante, etapa, activa, comprobación)
    "María López Hernández": ("V1", "Prefiltro", True),
    "José Luis Ramírez Torres": ("V1", "Prefiltro", True),
    "Ana Karen Morales Díaz": ("V1", "Prefiltro", False),
    "Rosa Elena Villanueva Cruz": ("V1", "Prefiltro", True),
    "Juan Carlos Pérez Solís": ("V1", "Entrevista IA", True),
    "Miguel Ángel Ortiz Reyes": ("V1", "Entrevista IA", True),
    "Laura Gutiérrez Mendoza": ("V1", "Entrevista Humana", True),
    "Sandra Jiménez Castro": ("V1", "Entrevista Humana", True),
    "Raúl Esquivel Navarro": ("V1", "Contratación", True),
    "Daniela Herrera Fuentes": ("V2", "Entrevista IA", True),
    "Andrés Salgado Vega": ("V2", "Prefiltro", True),
    "Karla Domínguez Ríos": ("V2", "Prefiltro", False),
    "Roberto Aguilar Méndez": ("V2", "Entrevista IA", True),
    "Eduardo Campos Bravo": ("V2", "Entrevista Humana", True),
    "Claudia Morales Echeverría": ("V3", "Entrevista Humana", True),
    "Sergio Rentería Galván": ("V3", "Entrevista IA", True),
    "Fernando Ibarra Lozano": ("V3", "Prefiltro", False),
}


def validar(db, cu: Cuenta, vacantes: dict, con_candidatos: bool = True) -> list:
    from app.serial import nombre_empresa_candidato, vacante_dict
    from app.services import ia, plantillas_cuenta

    problemas = []
    for d in VACANTES:
        v = vacantes[d["clave"]]
        if ia.presentacion_bot(nombre_empresa_candidato(v)) != PRESENTACION:
            problemas.append(f"{v.titulo}: presentación «{ia.presentacion_bot(nombre_empresa_candidato(v))}»")
        criterios = vacante_dict(v).get("criterios") or []
        if d["clave"] != "V1":
            for _req, q, descarta in d["filtros"]:
                c = next((x for x in criterios if x["pregunta"] == q), None)
                if c is None or bool(c.get("descarta")) != descarta:
                    problemas.append(f"{v.titulo}: «Unas preguntas» sin «{q}» (descarta={descarta}) → {[x['pregunta'] for x in criterios]}")
        tipos = [sproc.clave_catalogo(x) for x in v.proceso["pasos"]]
        if tipos[-1] != "otra" or v.proceso["pasos"][-1]["nombre"] != SAEKO:
            problemas.append(f"{v.titulo}: la ruta no termina en «{SAEKO}» → {tipos}")
        med = next((x for x in v.proceso["pasos"] if x["tipo"] == "medica"), {})
        if not (med.get("no_apto_descarta") and med.get("restricciones_revision")):
            problemas.append(f"{v.titulo}: el examen médico no trae sus reglas")
        psi = next((x for x in v.proceso["pasos"] if x["tipo"] == "psicometrica"), {})
        if (psi.get("evaluacion_psicometria") or {}).get("modalidad") != "valores":
            problemas.append(f"{v.titulo}: la psicometría no usa la escala de valores")
        nota(f"{v.codigo} {v.titulo}: {' → '.join(x['nombre'] for x in v.proceso['pasos'])}")
    # plantillas: TODO lo que pueda salir hacia Meta queda dentro de las aprobadas
    for origen in ("inicio_entrevista_rh", "entrevista_video_rh", "aviso_evaluador_rh", "aviso_medico", "alerta_entrevista_asig",
                   "oferta_laboral_rh", "aviso_proceso_rh"):
        r = plantillas_cuenta.aplicar(origen, ["Juan Pérez", "x", "y", "lunes 20, 10:00 (hora de Ciudad de México)", "z", "w", "v", "u"], cu.id)
        if r["bloquear"] or r["plantilla"] not in set(PLANTILLAS_APROBADAS) | plantillas_cuenta.aprobadas_globales():
            problemas.append(f"Plantilla «{origen}» → «{r['plantilla']}» no anclada a las aprobadas")
    for origen in ("descarte_postulacion_rh", "solicitud_documentos_rh", "recordatorio_psicometria_rh", "cita_cambio_evaluador_rh",
                   "cita_cancelada_evaluador_rh"):
        if not plantillas_cuenta.aplicar(origen, [], cu.id)["bloquear"]:
            problemas.append(f"Plantilla «{origen}» no aprobada y NO se bloqueó")
    if con_candidatos:
        for nombre, (clave, etapa, activa) in ESTADOS_ESPERADOS.items():
            p = (db.query(Postulacion).join(Candidato, Candidato.id == Postulacion.candidato_id)
                 .filter(Postulacion.vacante_id == vacantes[clave].id, Candidato.nombre == nombre).first())
            if p is None or p.etapa != etapa or bool(p.activa) != activa:
                problemas.append(f"{nombre}: {None if p is None else (p.etapa, p.activa)} ≠ {(etapa, activa)}")
    return problemas


def configurar(db, publicar: bool = True, con_candidatos: bool = True) -> dict:
    cu = asegurar_cuenta(db)
    u = vincular_admins(db, cu)
    print("\n-- Sedes --")
    sedes = asegurar_sedes(db, cu)
    prueba = asegurar_prueba(db, cu, u)
    pasos = {"V1": pasos_v1([prueba]), "V2": pasos_v2([prueba], u.id), "V3": pasos_v3([prueba], u.id)}
    vacantes = {}
    for d in VACANTES:
        print(f"\n-- {d['titulo']} --")
        vacantes[d["clave"]] = asegurar_vacante(db, cu, u, sedes, d, pasos[d["clave"]], publicar)
    n = 0
    if con_candidatos:
        print("\n-- Candidatos precargados (ficticios) --")
        n = sembrar_candidatos(db, cu, u, vacantes, [prueba])
        ok(f"{n} candidato(s) nuevo(s)") if n else nota("Los candidatos ya estaban sembrados")
    return {"cuenta": cu, "vacantes": vacantes, "usuario": u, "candidatos": n}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aplicar", action="store_true", help="guarda los cambios (sin esto es un simulacro)")
    ap.add_argument("--borrador", action="store_true", help="deja las vacantes en Borrador")
    ap.add_argument("--sin-candidatos", action="store_true", help="no siembra los 17 candidatos de demostración")
    a = ap.parse_args()

    db = SessionLocal()
    commit_real = db.commit
    db.commit = db.flush  # una sola transacción: los commits de los endpoints solo hacen flush
    try:
        print(f"\n== UVAQ ({'APLICAR' if a.aplicar else 'SIMULACRO'}) ==")
        r = configurar(db, publicar=not a.borrador, con_candidatos=not a.sin_candidatos)
        print("\n-- Validación --")
        problemas = validar(db, r["cuenta"], r["vacantes"], con_candidatos=not a.sin_candidatos)
        if problemas:
            raise Abortar("Validación fallida:\n    " + "\n    ".join(problemas))
        ok("Validación: Cuenta, agente, plantillas ancladas, 3 vacantes, escala de valores, dictamen médico y candidatos")
        if a.aplicar:
            commit_real()
            print("\nListo: cambios guardados.")
        else:
            db.rollback()
            print("\nSimulacro: nada se guardó. Corre con --aplicar para guardar.")
        return 0
    except Abortar as e:
        db.rollback()
        print(f"\n✗ {e}\nNada se guardó.")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
