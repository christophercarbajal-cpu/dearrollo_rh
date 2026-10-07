"""Entorno de demostración «Demo Grupak» (prospecto) — 2026-10-07.

Solo DATOS: no toca código, endpoints ni pantallas. Usa los modelos y servicios actuales:
  1. Cuenta «Demo Grupak» (slug `demo-grupak`) con las tres rutas base (`proceso.asegurar_rutas_base`), igual que
     `POST /cuentas`.
  2. Catálogo psicométrico de la Cuenta: Cleaver, Zavic, Terman, Herrmann, 16PF y MOSS (tipo «prueba») y tres
     baterías (tipo «batería»): Ayudante, Cumplimiento y Gerente. Cada batería lleva en «Identificador en el
     proveedor» la unión de los identificadores de sus pruebas, así «Asignar y enviar» da de alta todas juntas.
  3. Plantilla de ruta «Ruta Demo Grupak» con 12 actividades repartidas en las 5 etapas (validada con
     `proceso.normalizar_pasos`; predeterminada de la Cuenta).
  4. Tres vacantes con esa ruta; cada una cambia SOLO en su copia la batería de la actividad «Batería psicométrica»
     (`proceso.proceso_para_vacante`, la ruta no se altera).
  5. Tres candidatos ficticios (uno por vacante) en etapas distintas, con registros reales para que el estado de cada
     actividad se DERIVE solo: Prefiltro, Filtro Red Human y Filtro humano.

Seguridad:
  * Sin `--aplicar` es un SIMULACRO: hace todo dentro de una transacción y al final la deshace (nada queda escrito).
  * Idempotente: la Cuenta se reconoce por su slug/nombre; pruebas por clave, ruta por nombre, vacantes por título y
    candidatos por correo dentro de la Cuenta. Lo que ya existe se reutiliza, nunca se duplica ni se sobrescribe
    (si RH ya editó la ruta o una vacante, se respeta).
  * Cero comunicaciones: no llama a WhatsApp, Telegram, correo, Teams ni a Psicométricas.mx (saldo compartido con
    producción). Las evaluaciones psicométricas de los candidatos demo quedan en modo integrado SIMULADO (sin clave
    del proveedor, así el job de recordatorios nunca les escribe). Los candidatos usan correos `@demo.invalid`
    (dominio reservado, imposible de entregar) y no tienen teléfono.
  * Vacantes en «En revisión» (no salen en el portal global ni en el menú del WhatsApp compartido). Con
    `--publicar` quedan «Publicadas» y aparecen en el portal de la Cuenta.
  * No crea usuarios ni toca contraseñas salvo que se pida con `--crear-usuario` (la contraseña se genera al azar,
    se imprime UNA vez y se pide cambiarla al entrar).

Identificadores de Psicométricas.mx: los de abajo (IDS_PROVEEDOR) son de EJEMPLO. En un servidor SIN las llaves
del proveedor no importa (modo simulado). Si el servidor tiene `PSICOMETRICAS_TOKEN`/`PASSWORD`, «Asignar y enviar»
llamará a la API REAL con esos números: antes de la demo corre el script con los reales:
    --ids-proveedor "Cleaver=12,Zavic=34,Terman=5,Herrmann=8,16PF=2,MOSS=9"
(en una segunda corrida actualiza los identificadores de las pruebas y baterías demo; nada más).

Uso (desde red-human-api/, con el .env del ambiente; la API debe haber arrancado al menos una vez con esta versión
para que el esquema esté al día):
    .venv/Scripts/python.exe scripts/seed_demo_grupak.py                      # simulacro (no escribe)
    .venv/Scripts/python.exe scripts/seed_demo_grupak.py --aplicar            # aplica
Opciones:
    --publicar                 deja las vacantes Publicadas
    --admin CORREO             (repetible) usuario EXISTENTE que podrá ver la Cuenta. Sin esta opción se vincula a
                               todos los Administradores activos (un Administrador solo ve las Cuentas vinculadas).
    --crear-usuario CORREO     crea (si no existe) un usuario Administrador vinculado SOLO a «Demo Grupak»
    --ids-proveedor "N=ID,…"   identificadores reales de Psicométricas.mx
"""

import argparse
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

try:  # la consola de Windows (cp1252) no imprime acentos/símbolos
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from sqlalchemy import func, inspect as sa_inspect  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Candidato, Cuenta, Entrevista, Evaluacion, PlantillaProceso, Postulacion, PruebaPsicometrica, Usuario,
    UsuarioCuenta, Vacante, registrar, texto_sueldo, texto_ubicacion,
)
from app.routers.auth import crear_usuario_basico  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.routers.vacantes import _slug_unico  # noqa: E402
from app.seed import slug_cuenta_unico  # noqa: E402
from app.serial import nombre_empresa  # noqa: E402
from app.services import evaluaciones as sev  # noqa: E402
from app.services import proceso as sproc  # noqa: E402

ACTOR = "script:seed_demo_grupak"
NOMBRE_CUENTA = "Demo Grupak"
SLUG_CUENTA = "demo-grupak"
DOMINIO = "demo.invalid"
PROVEEDOR = "Psicométricas.mx"
NOMBRE_RUTA = "Ruta Demo Grupak"
PASO_BATERIA = "bateria-psicometrica"

# ------------------------------------------------------------ catálogo psicométrico
# Identificadores de EJEMPLO en Psicométricas.mx (ver docstring; se reemplazan con --ids-proveedor).
IDS_PROVEEDOR = {"Cleaver": "101", "Zavic": "102", "Terman": "103", "Herrmann": "104", "16PF": "105", "MOSS": "106"}
PRUEBAS = {
    "Cleaver": "Perfil conductual DISC: estilo de trabajo bajo condiciones normales, de presión y motivación.",
    "Zavic": "Valores (moral, legalidad, indiferencia, corrupción) e intereses (económico, político, social, religioso).",
    "Terman": "Capacidad intelectual (Terman-Merrill): razonamiento verbal, numérico y abstracto.",
    "Herrmann": "Dominancia cerebral: preferencias de pensamiento analítico, organizado, interpersonal e innovador.",
    "16PF": "Cuestionario de 16 factores de personalidad (Cattell).",
    "MOSS": "Adaptabilidad social para puestos de supervisión: relaciones interpersonales y manejo de personal.",
}
BATERIAS = {
    "Batería Ayudante": ["Cleaver", "Zavic"],
    "Batería Cumplimiento": ["Terman", "Cleaver", "Herrmann", "16PF"],
    "Batería Gerente": ["Terman", "MOSS", "Cleaver", "Herrmann", "16PF"],
}


def clave_prueba(nombre: str) -> str:
    return "GRP-" + "".join(ch for ch in nombre.upper() if ch.isalnum())


# ------------------------------------------------------------ ruta (12 actividades en las 5 etapas)
PASOS_RUTA = [
    # Prefiltro
    {"id": "prefiltro", "tipo": "prefiltro_web", "nombre": "Prefiltro", "etapa": "Prefiltro"},
    # Filtro Red Human (valor interno «Entrevista IA»)
    {"id": "persona-lluvia", "tipo": "otra", "nombre": "Persona bajo la lluvia", "etapa": "Entrevista IA",
     "responsable": {"tipo": "rh"}},
    {"id": PASO_BATERIA, "tipo": "psicometrica", "nombre": "Batería psicométrica", "etapa": "Entrevista IA",
     "responsable": {"tipo": "externo", "nombre": PROVEEDOR}},
    {"id": "entrevista_red_human", "tipo": "entrevista_agente", "nombre": "Entrevista profunda Red Human",
     "etapa": "Entrevista IA", "tipo_entrevista": "profesional_personal"},
    # Filtro humano (valor interno «Entrevista Humana»)
    {"id": "referencias", "tipo": "referencias", "nombre": "Referencias y revisión documental", "etapa": "Entrevista Humana"},
    {"id": "entrevista-sindicato", "tipo": "entrevista_humana", "nombre": "Entrevista con sindicato",
     "etapa": "Entrevista Humana", "tipo_entrevista": "general", "responsable": {"tipo": "externo", "nombre": "Sindicato"}},
    {"id": "medica", "tipo": "medica", "nombre": "Examen médico", "etapa": "Entrevista Humana",
     "responsable": {"tipo": "externo", "nombre": "Médico de la empresa"}},
    {"id": "entrevista-lider", "tipo": "entrevista_humana", "nombre": "Entrevista con líder", "etapa": "Entrevista Humana",
     "tipo_entrevista": "jefe_directo", "depende_de": ["entrevista-sindicato"],
     "responsable": {"tipo": "externo", "nombre": "Líder del área"}},
    # Contratación
    {"id": "propuesta", "tipo": "condiciones", "nombre": "Propuesta y aceptación", "etapa": "Contratación"},
    {"id": "documentos-ingreso", "tipo": "documentos", "nombre": "Documentos de ingreso", "etapa": "Contratación"},
    {"id": "contratacion", "tipo": "carta_contrato", "nombre": "Contratación", "etapa": "Contratación",
     "depende_de": ["propuesta", "documentos-ingreso"]},
    # Onboarding
    {"id": "onboarding", "tipo": "onboarding", "nombre": "Onboarding", "etapa": "Onboarding"},
]
# Avance automático como en las rutas base (Contratación y Onboarding nunca avanzan solas).
ETAPAS_RUTA = {e: {"avance_automatico": True} for e in ("Prefiltro", "Entrevista IA", "Entrevista Humana")}

# ------------------------------------------------------------ vacantes
VACANTES = [
    {
        "titulo": "Ayudante general", "bateria": "Batería Ayudante", "area": "Operaciones", "seniority": "Sin experiencia",
        "estado": "Nuevo León", "municipio": "Monterrey", "modalidad": "Presencial",
        "sueldo": (9500, 11000, "mensual"),
        "descripcion": "Apoyo en carga, descarga, acomodo de materiales y limpieza del área de trabajo en planta.",
        "indispensables": ["Secundaria terminada", "Disponibilidad para rolar turnos"],
        "deseables": ["Experiencia en almacén o producción"],
        "beneficios": ["Prestaciones de ley", "Comedor", "Transporte de personal"],
        "enfoque": "profesional",
    },
    {
        "titulo": "Especialista en Cumplimiento Normativo", "bateria": "Batería Cumplimiento", "area": "Legal y Cumplimiento",
        "seniority": "Senior", "estado": "Ciudad de México", "municipio": "Miguel Hidalgo", "modalidad": "Híbrido",
        "sueldo": (38000, 45000, "mensual"),
        "descripcion": "Asegurar el cumplimiento regulatorio (laboral, fiscal y de protección de datos), coordinar "
                       "auditorías internas y dar seguimiento a planes de acción.",
        "indispensables": ["Licenciatura en Derecho, Contaduría o afín", "3 años en cumplimiento o auditoría"],
        "deseables": ["Conocimiento de la LFPDPPP", "Certificación en compliance"],
        "beneficios": ["Prestaciones superiores a las de ley", "Seguro de gastos médicos mayores", "Esquema híbrido"],
        "enfoque": "profesional_personal",
    },
    {
        "titulo": "Gerente Trainee de Ventas", "bateria": "Batería Gerente", "area": "Ventas", "seniority": "Junior",
        "estado": "Jalisco", "municipio": "Guadalajara", "modalidad": "Presencial",
        "sueldo": (25000, 30000, "mensual"),
        "descripcion": "Programa de formación para liderar un equipo comercial: prospección, seguimiento de cartera, "
                       "metas de venta y desarrollo de personas.",
        "indispensables": ["Licenciatura terminada", "Experiencia en ventas o atención a clientes"],
        "deseables": ["Haber coordinado a un equipo", "Licencia de manejo"],
        "beneficios": ["Prestaciones de ley", "Comisiones", "Plan de carrera"],
        "enfoque": "profesional_personal",
    },
]

# ------------------------------------------------------------ candidatos ficticios (uno por vacante)
CANDIDATOS = [
    {"vacante": "Ayudante general", "nombre": "Luis Alberto Ramírez Soto", "correo": f"luis.ramirez@{DOMINIO}",
     "ubicacion": "Monterrey, Nuevo León", "experiencia": "1 año como auxiliar de almacén", "escenario": "prefiltro"},
    {"vacante": "Especialista en Cumplimiento Normativo", "nombre": "Mariana Castillo Herrera",
     "correo": f"mariana.castillo@{DOMINIO}", "ubicacion": "Ciudad de México",
     "experiencia": "5 años en auditoría interna y cumplimiento", "escenario": "filtro_red_human"},
    {"vacante": "Gerente Trainee de Ventas", "nombre": "Jorge Iván Morales Treviño", "correo": f"jorge.morales@{DOMINIO}",
     "ubicacion": "Guadalajara, Jalisco", "experiencia": "3 años en ventas B2B; coordinó a 4 ejecutivos",
     "escenario": "filtro_humano"},
]


class Abortar(Exception):
    pass


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def nota(msg: str) -> None:
    print(f"  · {msg}")


# ============================================================ verificaciones previas


def verificar_esquema() -> None:
    insp = sa_inspect(engine)
    tablas = set(insp.get_table_names())
    faltan = [t for t in ("cuentas", "vacantes", "candidatos", "postulaciones", "pruebas_psicometricas",
                          "plantillas_proceso", "evaluaciones", "eventos_evaluacion", "entrevistas") if t not in tablas]
    if faltan:
        raise Abortar(f"Faltan tablas ({', '.join(faltan)}). Arranca la API una vez con esta versión y vuelve a correr.")
    columnas = {
        "pruebas_psicometricas": "tipo",
        "evaluaciones": "pruebas",
    }
    for tabla, col in columnas.items():
        if col not in {c["name"] for c in insp.get_columns(tabla)}:
            raise Abortar(f"La tabla {tabla} no tiene la columna «{col}». Arranca la API una vez con esta versión.")


def leer_ids_proveedor(texto: str) -> dict:
    ids = dict(IDS_PROVEEDOR)
    if not texto:
        return ids
    for parte in texto.split(","):
        if "=" not in parte:
            raise Abortar(f"--ids-proveedor: «{parte}» no tiene la forma Nombre=ID.")
        nombre, valor = (x.strip() for x in parte.split("=", 1))
        nombre = next((n for n in PRUEBAS if n.lower() == nombre.lower()), None)
        if nombre is None:
            raise Abortar(f"--ids-proveedor: prueba desconocida. Usa: {', '.join(PRUEBAS)}.")
        if not valor.isdigit():
            raise Abortar(f"--ids-proveedor: el identificador de {nombre} debe ser numérico.")
        ids[nombre] = valor
    return ids


# ============================================================ 1. Cuenta


def asegurar_cuenta(db) -> Cuenta:
    cu = (db.query(Cuenta)
          .filter((Cuenta.slug == SLUG_CUENTA) | (func.lower(Cuenta.nombre) == NOMBRE_CUENTA.lower()))
          .order_by(Cuenta.id).first())
    if cu is not None:
        if cu.estado == "Eliminada":
            raise Abortar(f"La Cuenta «{cu.nombre_visible}» (id {cu.id}) está eliminada. Restáurala desde "
                          "Configuración → Cuentas y vuelve a correr el script.")
        nota(f"Cuenta existente: {cu.nombre_visible} (id {cu.id}, slug {cu.slug})")
    else:
        cu = Cuenta(nombre=NOMBRE_CUENTA, nombre_comercial=NOMBRE_CUENTA, razon_social="Demo Grupak, S.A. de C.V.",
                    contacto_nombre="Equipo comercial Red Human", estado="Activa")
        db.add(cu)
        db.flush()
        cu.slug = slug_cuenta_unico(db, cu.nombre_comercial, cu.id)
        registrar(db, ACTOR, "cuenta_creada", "cuenta", str(cu.id), {"nombre": NOMBRE_CUENTA, "demo": True})
        ok(f"Cuenta creada: {NOMBRE_CUENTA} (id {cu.id}, slug {cu.slug})")
    with db.begin_nested():
        n = sproc.asegurar_rutas_base(db, cu.id, ACTOR)
    if n:
        ok(f"Rutas base sembradas en la Cuenta: {n}")
    return cu


def vincular_usuarios(db, cu: Cuenta, admins: list, crear: str) -> list:
    """Quién podrá ver la Cuenta. Regresa [(correo, contraseña_o_None)]."""
    salida = []
    if admins:
        usuarios = []
        for correo in admins:
            u = db.query(Usuario).filter(func.lower(Usuario.correo) == correo.strip().lower()).first()
            if u is None:
                raise Abortar(f"--admin: no existe el usuario {correo}. No se crea nada.")
            usuarios.append(u)
    else:
        usuarios = db.query(Usuario).filter(Usuario.rol == "Administrador").all()
        usuarios = [u for u in usuarios if getattr(u, "activo", True)]
    for u in usuarios:
        if not db.query(UsuarioCuenta).filter(UsuarioCuenta.usuario_id == u.id, UsuarioCuenta.cuenta_id == cu.id).first():
            db.add(UsuarioCuenta(usuario_id=u.id, cuenta_id=cu.id))
            ok(f"Acceso a la Cuenta para {u.correo}")
        salida.append((u.correo, None))
    if crear:
        correo = crear.strip().lower()
        u = db.query(Usuario).filter(func.lower(Usuario.correo) == correo).first()
        password = None
        if u is None:
            password = secrets.token_urlsafe(9) + "-7a"
            u = crear_usuario_basico(db, correo, "Demo Grupak", "Demostración", "Administrador", password)
            ok(f"Usuario creado: {correo} (deberá cambiar la contraseña al entrar)")
        else:
            nota(f"El usuario {correo} ya existía: su contraseña no se toca")
        if not db.query(UsuarioCuenta).filter(UsuarioCuenta.usuario_id == u.id, UsuarioCuenta.cuenta_id == cu.id).first():
            db.add(UsuarioCuenta(usuario_id=u.id, cuenta_id=cu.id))
        if not u.cuenta_predeterminada_id:
            u.cuenta_predeterminada_id = cu.id
        salida.append((correo, password))
    db.flush()
    return salida


# ============================================================ 2. Catálogo


def asegurar_prueba(db, cu: Cuenta, nombre: str, tipo: str, id_proveedor: str, descripcion: str) -> PruebaPsicometrica:
    clave = clave_prueba(nombre)
    pr = db.query(PruebaPsicometrica).filter(PruebaPsicometrica.cuenta_id == cu.id, PruebaPsicometrica.clave == clave).first()
    if pr is None:
        pr = PruebaPsicometrica(cuenta_id=cu.id, clave=clave, nombre=nombre, descripcion=descripcion, modo="integrada",
                                proveedor=PROVEEDOR, id_proveedor=id_proveedor, tipo=tipo, activa=True, creado_por=ACTOR)
        db.add(pr)
        db.flush()
        ok(f"{'Batería' if tipo == 'bateria' else 'Prueba'}: {nombre} (id {pr.id}, proveedor «{id_proveedor}»)")
    else:
        if pr.id_proveedor != id_proveedor:
            nota(f"{nombre}: identificador en el proveedor {pr.id_proveedor or '—'} → {id_proveedor}")
            pr.id_proveedor = id_proveedor
        if not pr.activa:
            pr.activa = True
            nota(f"{nombre}: reactivada")
        nota(f"Ya existía: {nombre} (id {pr.id})")
    return pr


def asegurar_catalogo(db, cu: Cuenta, ids: dict) -> dict:
    catalogo = {}
    for nombre, desc in PRUEBAS.items():
        catalogo[nombre] = asegurar_prueba(db, cu, nombre, "prueba", ids[nombre], desc)
    for nombre, pruebas in BATERIAS.items():
        id_prov = ",".join(dict.fromkeys(ids[p] for p in pruebas))
        desc = "Incluye: " + ", ".join(pruebas) + "."
        catalogo[nombre] = asegurar_prueba(db, cu, nombre, "bateria", id_prov, desc)
    return catalogo


# ============================================================ 3. Ruta


def asegurar_ruta(db, cu: Cuenta) -> PlantillaProceso:
    pl = (db.query(PlantillaProceso)
          .filter(PlantillaProceso.cuenta_id == cu.id, PlantillaProceso.nombre == NOMBRE_RUTA).first())
    if pl is not None:
        if not pl.activa:
            pl.activa = True
            nota("Ruta reactivada")
        nota(f"Ruta existente: {NOMBRE_RUTA} (id {pl.id}, versión {pl.version}) — no se sobrescribe")
        return pl
    pasos = sproc.normalizar_pasos(PASOS_RUTA)
    if len(pasos) != 12:
        raise Abortar(f"La ruta debía tener 12 actividades y tiene {len(pasos)}.")
    # una sola predeterminada por Cuenta
    db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cu.id).update({"predeterminada": False})
    pl = PlantillaProceso(
        cuenta_id=cu.id, nombre=NOMBRE_RUTA,
        descripcion="Prefiltro → Persona bajo la lluvia, batería psicométrica y Entrevista profunda Red Human → "
                    "referencias, sindicato, médico y líder → propuesta, documentos y contratación → onboarding (12 pasos).",
        pasos=pasos, etapas=sproc.normalizar_etapas(ETAPAS_RUTA), version=1, predeterminada=True, activa=True,
        creado_por=ACTOR, actualizada_por=ACTOR,
    )
    db.add(pl)
    db.flush()
    ok(f"Ruta creada: {NOMBRE_RUTA} (id {pl.id}, 12 actividades, predeterminada de la Cuenta)")
    return pl


# ============================================================ 4. Vacantes


def proceso_con_bateria(db, cu: Cuenta, pl: PlantillaProceso, bateria_id: int) -> dict:
    pasos = [dict(p) for p in pl.pasos]
    if not any(p["id"] == PASO_BATERIA for p in pasos):
        raise Abortar(f"La ruta «{pl.nombre}» ya no tiene la actividad «{PASO_BATERIA}» (¿la editaron?).")
    for p in pasos:
        if p["id"] == PASO_BATERIA:
            p["pruebas"] = [bateria_id]
    return sproc.proceso_para_vacante(db, cu.id, {}, {"plantilla_id": pl.id, "pasos": pasos, "etapas": pl.etapas})


def asegurar_vacantes(db, cu: Cuenta, pl: PlantillaProceso, catalogo: dict, publicar: bool) -> dict:
    vacantes = {}
    responsable = (db.query(Usuario).join(UsuarioCuenta, UsuarioCuenta.usuario_id == Usuario.id)
                   .filter(UsuarioCuenta.cuenta_id == cu.id).order_by(Usuario.id).first())
    for d in VACANTES:
        v = (db.query(Vacante)
             .filter(Vacante.cuenta_id == cu.id, Vacante.titulo == d["titulo"], Vacante.estado != "Eliminada").first())
        bateria = catalogo[d["bateria"]]
        if v is not None:
            if not sproc.tiene_proceso(v):
                v.proceso = proceso_con_bateria(db, cu, pl, bateria.id)
                nota(f"{v.codigo}: se le asignó la ruta con «{bateria.nombre}»")
            nota(f"Vacante existente: {v.codigo} {v.titulo} ({v.estado}) — no se sobrescribe")
            vacantes[d["titulo"]] = v
            continue
        desde, hasta, periodo = d["sueldo"]
        v = Vacante(
            codigo="TMP", cuenta_id=cu.id, cliente_id=None, responsable_id=responsable.id if responsable else None,
            titulo=d["titulo"], area=d["area"], empresa=nombre_empresa(cu, None, True),
            enfoque_entrevista=d["enfoque"], ubicacion_estado=d["estado"], ubicacion_municipio=d["municipio"],
            ubicacion=texto_ubicacion(d["estado"], d["municipio"]), modalidad=d["modalidad"],
            sueldo_desde=desde, sueldo_hasta=hasta, sueldo_moneda="MXN", sueldo_periodicidad=periodo,
            sueldo=texto_sueldo(desde, hasta, "MXN", periodo),
            estado="Publicada" if publicar else "En revisión",
            requisitos=" · ".join(d["indispensables"]), requisitos_deseables=d["deseables"], beneficios=d["beneficios"],
            descripcion=d["descripcion"], resumen=d["descripcion"], seniority=d["seniority"],
            plataformas=["Portal"] if publicar else [],
            preguntas_filtro=[
                {"pregunta": f"¿Cumples con: {r}?", "tipo": "si_no", "valida": r, "respuesta_esperada": "Sí",
                 "descarta": True, "opciones": ["Sí", "No", "Parcial"]}
                for r in d["indispensables"]
            ],
        )
        v.proceso = proceso_con_bateria(db, cu, pl, bateria.id)
        db.add(v)
        db.flush()
        v.codigo = f"VAC-{1036 + v.id}"
        v.slug = _slug_unico(db, v.titulo, v.id)
        if publicar:
            v.publicada_en = v.creada_en
        registrar(db, ACTOR, "vacante_creada", "vacante", v.codigo,
                  {"titulo": v.titulo, "estado": v.estado, "proceso": NOMBRE_RUTA, "bateria": bateria.nombre, "demo": True})
        ok(f"Vacante: {v.codigo} {v.titulo} · {bateria.nombre} · {v.estado}")
        vacantes[d["titulo"]] = v
    return vacantes


# ============================================================ 5. Candidatos con avance


def _nota_historial(p: Postulacion, texto: str) -> None:
    p.historial = list(p.historial or []) + [
        {"evento": "demo_sembrado", "texto": texto, "usuario": ACTOR, "fecha": ahora().isoformat()}]


def _nueva_evaluacion(db, p: Postulacion, cu: Cuenta, **campos) -> Evaluacion:
    ev = sev.nueva(p, cu.id, ACTOR, None, **campos)
    sev.asegurar_ligas(ev)
    db.add(ev)
    db.flush()
    sev.asignar_codigo(ev)
    sev.evento(db, ev, "creada", ACTOR, a=ev.estado, tipo=ev.tipo, forma=ev.forma, demo=True)
    return ev


def _resultado(db, ev: Evaluacion, conclusion: str, comentarios: str, realizada_por: str, canal: str = "sistema",
               revisar: bool = True) -> None:
    sev.registrar_resultado(db, ev, actor=ACTOR, canal=canal, conclusion=conclusion, comentarios=comentarios,
                            realizada_por=realizada_por, adjuntos=[], version=None)
    if revisar:
        sev.revisar(db, ev, actor="RH Demo Grupak", usuario_id=None, conclusion=conclusion,
                    comentario="Revisión de demostración.")


def _psicometria(db, p: Postulacion, cu: Cuenta, bateria: PruebaPsicometrica, terminada: bool) -> Evaluacion:
    """Misma forma que «Asignar y enviar» en modo simulado (sin clave del proveedor: nunca se le escribe)."""
    ev = _nueva_evaluacion(db, p, cu, tipo="psicometrica", nombre=bateria.nombre, forma="integrada", prueba_id=bateria.id,
                           pruebas=[bateria.id], proveedor=PROVEEDOR, id_proveedor=bateria.id_proveedor,
                           paso_integrada="asignada", paso_id=PASO_BATERIA)
    sev.aplicar_paso(db, ev, "enviada", ACTOR)
    if terminada:
        sev.aplicar_paso(db, ev, "iniciada", ACTOR)
        sev.aplicar_paso(db, ev, "completada", ACTOR)
        _resultado(db, ev, "favorable", "Resultado SIMULADO para la demostración: perfil compatible con el puesto.",
                   f"{PROVEEDOR} (simulado)", canal="proveedor")
    return ev


def _prefiltro_cumple(p: Postulacion, score: int) -> None:
    p.analisis = {**(p.analisis or {}),
                  "respuestas_web": {"cumple_requisitos": "Sí", "disponibilidad": "Sí"},
                  "fortalezas_cv": ["Experiencia relevante para el puesto", "Estabilidad laboral"],
                  "requisitos_cumplidos": ["Escolaridad", "Experiencia mínima"]}
    p.prefiltro_completo = True
    p.estado = "cumple"
    p.score = score


def _entrevista_red_human(db, p: Postulacion, match: int) -> Entrevista:
    fin = ahora() - timedelta(days=1)
    e = Entrevista(
        codigo="TMP", candidato_id=p.candidato_id, token=secrets.token_urlsafe(24), tipo="texto", estado="evaluada",
        cierre="manual", consentimiento=True, consentimiento_fecha=fin - timedelta(minutes=25),
        iniciada_en=fin - timedelta(minutes=22), finalizada_en=fin, ultima_actividad_en=fin,
        guion={"enfoque": "profesional_personal", "temas": ["Experiencia", "Logros", "Liderazgo", "Motivación"]},
        transcript=[
            {"rol": "agente", "texto": "Hola, soy Red Human. Gracias por tu tiempo; platiquemos de tu experiencia."},
            {"rol": "candidato", "texto": "Claro. Llevo tres años en ventas B2B y coordino a un equipo de cuatro ejecutivos."},
            {"rol": "agente", "texto": "¿Qué resultado de ese equipo te enorgullece más?"},
            {"rol": "candidato", "texto": "Crecimos la cartera 18 % en un año con un proceso de seguimiento semanal."},
        ],
        evaluacion={
            "match_perfil": match,
            "fortalezas": ["Orientación a resultados con evidencia concreta", "Experiencia coordinando a un equipo"],
            "riesgos": ["Validar manejo de equipos más grandes"],
            "recomendacion": "Avanzar a Filtro humano (simulado para la demostración).",
            "faltante": [],
        },
    )
    p.entrevistas.append(e)
    db.add(e)
    db.flush()
    e.codigo = f"ENT-{300 + e.id}"
    return e


def sembrar_candidato(db, cu: Cuenta, d: dict, v: Vacante, catalogo: dict) -> tuple:
    c = (db.query(Candidato)
         .filter(Candidato.cuenta_id == cu.id, func.lower(Candidato.correo) == d["correo"], Candidato.eliminado_en.is_(None))
         .first())
    if c is not None:
        p = next((x for x in c.postulaciones_activas if x.vacante_id == v.id), None)
        if p is not None:
            nota(f"Candidato existente: {c.nombre} · {p.codigo} en {p.etapa} — no se repite su avance")
            return c, p
    else:
        c = _crear_candidato(db, cu.id, d["nombre"], "Formulario", False, correo=d["correo"], telefono="",
                             ubicacion=d["ubicacion"], experiencia=d["experiencia"],
                             cv_datos={"resumen": d["experiencia"], "demo": True})
    p = crear_postulacion(db, c, v, cu.id, "formulario", consentimiento=True)
    bateria = catalogo[next(x["bateria"] for x in VACANTES if x["titulo"] == v.titulo)]
    esc = d["escenario"]

    if esc == "prefiltro":
        _nota_historial(p, "Candidato de demostración: postulación recibida, prefiltro pendiente.")

    elif esc == "filtro_red_human":
        _prefiltro_cumple(p, 82)
        p.etapa = "Entrevista IA"
        lluvia = _nueva_evaluacion(db, p, cu, tipo="otra", nombre="Persona bajo la lluvia", forma="registro_directo",
                                   paso_id="persona-lluvia")
        _resultado(db, lluvia, "favorable", "Prueba proyectiva SIMULADA: manejo adecuado de la presión.",
                   "Psic. Laura Méndez (demo)")
        _psicometria(db, p, cu, bateria, terminada=False)
        _nota_historial(p, "Candidato de demostración: prefiltro cumple, Persona bajo la lluvia revisada y batería "
                           "psicométrica enviada (simulada); falta la Entrevista profunda Red Human.")

    elif esc == "filtro_humano":
        _prefiltro_cumple(p, 88)
        p.etapa = "Entrevista Humana"
        lluvia = _nueva_evaluacion(db, p, cu, tipo="otra", nombre="Persona bajo la lluvia", forma="registro_directo",
                                   paso_id="persona-lluvia")
        _resultado(db, lluvia, "favorable", "Prueba proyectiva SIMULADA: seguridad y recursos ante la presión.",
                   "Psic. Laura Méndez (demo)")
        _psicometria(db, p, cu, bateria, terminada=True)
        _entrevista_red_human(db, p, 86)
        sindicato = _nueva_evaluacion(db, p, cu, tipo="entrevista_humana", nombre="Entrevista con sindicato",
                                      forma="registro_directo", paso_id="entrevista-sindicato",
                                      realizada_por="Comité sindical (demo)")
        _resultado(db, sindicato, "avanzar", "Entrevista SIMULADA: sin observaciones del sindicato.",
                   "Comité sindical (demo)", revisar=False)
        _nueva_evaluacion(db, p, cu, tipo="medica", nombre="Examen médico", forma="registro_directo", paso_id="medica")
        _nota_historial(p, "Candidato de demostración: Filtro Red Human completo (batería, Persona bajo la lluvia y "
                           "Entrevista profunda simuladas) y entrevista con sindicato aprobada; faltan referencias, "
                           "consentimiento médico y entrevista con líder.")
    registrar(db, ACTOR, "candidato_demo_sembrado", "postulacion", p.codigo,
              {"vacante": v.codigo, "etapa": p.etapa, "escenario": esc, "demo": True})
    ok(f"Candidato: {c.nombre} ({c.codigo}) · {p.codigo} · {v.titulo} · etapa {p.etapa}")
    return c, p


# ============================================================ main


def main() -> int:
    ap = argparse.ArgumentParser(description="Siembra el entorno de demostración «Demo Grupak».")
    ap.add_argument("--aplicar", action="store_true", help="escribe los cambios (sin esto es un simulacro)")
    ap.add_argument("--publicar", action="store_true", help="deja las vacantes Publicadas")
    ap.add_argument("--admin", action="append", default=[], help="usuario existente que verá la Cuenta (repetible)")
    ap.add_argument("--crear-usuario", default="", help="crea un Administrador vinculado solo a Demo Grupak")
    ap.add_argument("--ids-proveedor", default="", help='identificadores reales: "Cleaver=12,Zavic=34,…"')
    args = ap.parse_args()

    print(f"Base: {engine.url.render_as_string(hide_password=True)}")
    print("Modo: " + ("APLICAR" if args.aplicar else "SIMULACRO (no se escribe nada; usa --aplicar)"))
    db = SessionLocal()
    try:
        verificar_esquema()
        ids = leer_ids_proveedor(args.ids_proveedor)

        print("\n1 · Cuenta")
        cu = asegurar_cuenta(db)
        accesos = vincular_usuarios(db, cu, args.admin, args.crear_usuario)
        print("\n2 · Catálogo psicométrico")
        catalogo = asegurar_catalogo(db, cu, ids)
        print("\n3 · Ruta")
        pl = asegurar_ruta(db, cu)
        print("\n4 · Vacantes")
        vacantes = asegurar_vacantes(db, cu, pl, catalogo, args.publicar)
        print("\n5 · Candidatos")
        postulaciones = [sembrar_candidato(db, cu, d, vacantes[d["vacante"]], catalogo) for d in CANDIDATOS]

        # Comprobación: cada candidato tiene la ruta con SU batería, y la ruta general no cambió
        for (_c, p), d in zip(postulaciones, CANDIDATOS):
            paso = next((x for x in (p.proceso or {}).get("pasos", []) if x["id"] == PASO_BATERIA), None)
            esperada = catalogo[next(x["bateria"] for x in VACANTES if x["titulo"] == d["vacante"])].id
            if paso is None or paso.get("pruebas") != [esperada]:
                raise Abortar(f"{p.codigo}: la ruta congelada no trae la batería esperada.")
        if any(x.get("pruebas") for x in pl.pasos if x["id"] == PASO_BATERIA):
            nota("La ruta general trae una batería predeterminada (la editó RH); las vacantes tienen la suya.")

        if args.aplicar:
            db.commit()
        else:
            db.rollback()
    except Abortar as ex:
        db.rollback()
        print(f"\n✗ {ex}\nNo se escribió nada.")
        return 1
    except Exception:
        db.rollback()
        print("\n✗ Error inesperado; no se escribió nada.")
        raise
    finally:
        db.close()

    app = settings.app_url.rstrip("/")
    print("\n" + "=" * 70)
    print("SIMULACRO completo: todo valida. Corre con --aplicar para guardarlo." if not args.aplicar
          else "Listo: «Demo Grupak» quedó sembrada.")
    print("=" * 70)
    print(f"Cuenta: {NOMBRE_CUENTA} (id {cu.id}) — elígela en el selector de Cuenta del tablero.")
    print(f"Tablero:            {app}/dashboard")
    print(f"Candidatos:         {app}/dashboard/candidatos")
    print(f"Bolsa de trabajo:   {app}/portal?cuenta={cu.slug}"
          + ("" if args.publicar else "   (vacía hasta publicar: --publicar o «Publicar» en cada vacante)"))
    for d in VACANTES:
        v = vacantes[d["titulo"]]
        print(f"  {v.codigo}  {v.titulo:<40} {d['bateria']:<22} {app}/aplicar/{v.slug}")
    print("Candidatos demo:")
    for (c, p), d in zip(postulaciones, CANDIDATOS):
        print(f"  {p.codigo}  {c.nombre:<32} {d['vacante']:<40} etapa interna: {p.etapa}")
    print("Accesos:")
    for correo, password in accesos:
        if password:
            print(f"  {correo}  contraseña temporal: {password}   (se muestra UNA sola vez; se pide cambiarla al entrar)")
        else:
            print(f"  {correo}  (usa su contraseña de siempre)")
    if not args.ids_proveedor:
        print("\n⚠ Los identificadores de Psicométricas.mx son de EJEMPLO (101-106). Si este servidor tiene las llaves del "
              "proveedor, vuelve a correr con --ids-proveedor y los reales antes de usar «Asignar y enviar».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
