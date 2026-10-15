"""Demo «Fletes México» (Cambio 47, 2026-10-14) — Cuenta, Cliente, 5 terminales con horarios y cupo, el proceso «Fletes
operadores» (autoagenda + misma cita) y la vacante «Operador de 5ª rueda».

Solo DATOS, con las MISMAS funciones de los endpoints (`normalizar_pasos`, `routers.vacantes.crear`,
`services.ubicaciones`), así cada registro queda idéntico a uno capturado en pantalla:
  1. Cuenta + Cliente «Fletes México» (el bot se presenta: «Soy Red Human, el agente virtual de Fletes México.»).
  2. Ubicaciones: Terminal Guadalajara, Monterrey, CDMX, Querétaro y Hermosillo — lunes a viernes, 9:00 y 12:00, 8 lugares
     por horario.
  3. Proceso «Fletes operadores»:
       Prefiltro: Solicitud web sin CV → Prefiltro por WhatsApp → Documentos
       Filtro humano: Prueba de manejo (la agenda el candidato, misma cita) → Entrevista con jefe de operadores (misma cita)
                      → Entrevista con sindicato (misma cita) → Examen médico interno y antidoping (la agenda RH)
       Contratación: Contrato y firma
  4. Vacante «Operador de 5ª rueda»: las 5 terminales, presencial, 20 posiciones, $25,000 - $32,000 mensuales.
     Indispensables con vigencia (> 3 meses) de la licencia federal tipo B y del apto médico: el formulario web los pregunta
     con Sí / No directos y el WhatsApp pide la FECHA de vencimiento y descarta si vence en menos de 3 meses. Con 5
     terminales, la solicitud y el WhatsApp preguntan «¿En qué terminal te gustaría trabajar?».
     Documentos: Licencia federal tipo B (frente y reverso) y Apto médico.

Seguridad: sin `--aplicar` es un SIMULACRO (una transacción que al final se deshace); con `--aplicar` también es una sola
transacción: o queda todo o nada. Idempotente (Cuenta por slug, Cliente/ubicaciones/proceso por nombre, vacante por título).
Cero comunicaciones: no crea candidatos ni manda mensajes.

Uso (desde red-human-api/, con el .env del ambiente; la API debe haber arrancado una vez con esta versión):
    python scripts/seed_fletes_mexico.py              # simulacro
    python scripts/seed_fletes_mexico.py --aplicar    # aplica
Opciones: --borrador (vacante en Borrador) · --canal whatsapp|telegram|ambos (Cuenta nueva)
"""

import argparse
import asyncio
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:  # la consola de Windows (cp1252) no imprime acentos
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import func  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    CANALES_MENSAJERIA, Cliente, Cuenta, PlantillaProceso, Postulacion, Ubicacion, Usuario, UsuarioCuenta, Vacante, registrar,
)
from app.routers import vacantes as rvac  # noqa: E402
from app.seed import slug_cuenta_unico  # noqa: E402
from app.services import ia  # noqa: E402
from app.services import preguntas_filtro as pfu  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import ubicaciones as subic  # noqa: E402

ACTOR = "script:seed_fletes_mexico"
NOMBRE = "Fletes México"
SLUG = "fletes-mexico"

# ============================================================ 1. Terminales (Estado / Municipio del catálogo INEGI)
HORARIOS = [{"dias": [0, 1, 2, 3, 4], "horas": ["09:00", "12:00"], "cupo": 8}]
INDICACIONES = "Presenta en caseta tu INE y tu licencia federal; llega 15 minutos antes."
TERMINALES = [
    ("Terminal Guadalajara", "Jalisco", "Guadalajara", "Zona Industrial, Guadalajara, Jalisco"),
    ("Terminal Monterrey", "Nuevo León", "Monterrey", "Parque Industrial, Monterrey, Nuevo León"),
    ("Terminal CDMX", "Ciudad de México", "Azcapotzalco", "Zona Industrial Vallejo, Azcapotzalco, Ciudad de México"),
    ("Terminal Querétaro", "Querétaro", "Querétaro", "Parque Industrial Benito Juárez, Querétaro, Querétaro"),
    ("Terminal Hermosillo", "Sonora", "Hermosillo", "Parque Industrial, Hermosillo, Sonora"),
]

# ============================================================ 2. Proceso «Fletes operadores»
PROCESO = "Fletes operadores"


def pasos_proceso(evaluador_id: int) -> list:
    """El evaluador de las actividades presenciales es un usuario de la Cuenta (el Administrador de la demo); RH lo cambia
    por actividad en el editor de procesos."""
    ev = {"evaluador": {"tipo": "interno", "usuario_id": evaluador_id}}
    return [
        {"id": "solicitud", "tipo": "solicitud_sin_cv", "etapa": "Prefiltro"},
        {"id": "prefiltro-whatsapp", "tipo": "prefiltro_whatsapp", "nombre": "Prefiltro por WhatsApp", "etapa": "Prefiltro"},
        {"id": "documentos", "tipo": "documentos", "nombre": "Documentos", "etapa": "Prefiltro"},
        {"id": "prueba-manejo", "tipo": "tecnica", "nombre": "Prueba de manejo", "etapa": "Entrevista Humana",
         "tipo_agenda": "candidato", "misma_cita": True, "config": ev},
        {"id": "entrevista-jefe", "tipo": "entrevista_humana", "nombre": "Entrevista con jefe de operadores", "etapa": "Entrevista Humana",
         "tipo_entrevista": "jefe_directo", "tipo_agenda": "candidato", "misma_cita": True, "config": ev},
        {"id": "entrevista-sindicato", "tipo": "entrevista_humana", "nombre": "Entrevista con sindicato", "etapa": "Entrevista Humana",
         "tipo_entrevista": "general", "tipo_agenda": "candidato", "misma_cita": True, "config": ev},
        {"id": "medico", "tipo": "medica", "nombre": "Examen médico interno y antidoping", "etapa": "Entrevista Humana",
         "tipo_agenda": "rh"},
        {"id": "contrato", "tipo": "carta_contrato", "nombre": "Contrato y firma", "etapa": "Contratación"},
    ]


# ============================================================ 3. Vacante
LICENCIA = "Licencia federal tipo B con vigencia mayor a 3 meses"
APTO = "Apto médico con vigencia mayor a 3 meses"
VACANTE = {
    "titulo": "Operador de 5ª rueda", "area": "Operaciones", "seniority": "Semi-senior", "posiciones": 20,
    "sueldo": (25000, 32000),
    "descripcion": ("Operador de tractocamión de 5ª rueda para rutas nacionales de Fletes México: traslado de carga entre "
                    "terminales, revisión de la unidad y entrega de documentación de embarque."),
    "responsabilidades": ["Conducir tractocamión de 5ª rueda en rutas nacionales", "Revisar la unidad antes y después de cada viaje",
                          "Entregar la carga y la documentación de embarque", "Cumplir con las normas de seguridad vial"],
    "indispensables": [LICENCIA, APTO],
    "beneficios": ["Prestaciones de ley", "Viáticos", "Seguro de vida"],
    "documentos": [{"nombre": "Licencia de conducir", "cantidad": 2, "foto": True},
                   {"nombre": "Apto médico", "cantidad": 1, "foto": False}],
}
PREGUNTAS_WEB = {LICENCIA: "¿Tu licencia federal tipo B tiene vigencia de más de 3 meses?",
                 APTO: "¿Tu apto médico tiene vigencia de más de 3 meses?"}
PREGUNTAS_CHAT = {LICENCIA: "¿Hasta cuándo está vigente tu licencia federal tipo B?",
                  APTO: "¿Hasta cuándo está vigente tu apto médico?"}


class Abortar(Exception):
    pass


def ok(t: str) -> None:
    print(f"  ✓ {t}")


def nota(t: str) -> None:
    print(f"  · {t}")


def asegurar_cuenta(db, canal: str) -> Cuenta:
    cu = (db.query(Cuenta).filter((Cuenta.slug == SLUG) | (func.lower(Cuenta.nombre) == NOMBRE.lower())).order_by(Cuenta.id).first())
    if cu is not None:
        if cu.estado == "Eliminada":
            raise Abortar(f"La Cuenta «{cu.nombre_visible}» (id {cu.id}) está eliminada. Restáurala y vuelve a correr el script.")
        nota(f"Cuenta existente: {cu.nombre_visible} (id {cu.id}, slug {cu.slug})")
    else:
        cu = Cuenta(nombre=NOMBRE, nombre_comercial=NOMBRE, razon_social=NOMBRE, contacto_nombre="RH Fletes México",
                    canal_mensajeria=canal, estado="Activa")
        db.add(cu)
        db.flush()
        cu.slug = slug_cuenta_unico(db, cu.nombre_comercial, cu.id)
        registrar(db, ACTOR, "cuenta_creada", "cuenta", str(cu.id), {"nombre": NOMBRE})
        ok(f"Cuenta creada: {NOMBRE} (id {cu.id}, slug {cu.slug}, canal {canal})")
    with db.begin_nested():
        sproc.asegurar_rutas_base(db, cu.id, ACTOR)
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


def asegurar_cliente(db, cu: Cuenta) -> Cliente:
    cl = db.query(Cliente).filter(Cliente.cuenta_id == cu.id, func.lower(Cliente.nombre) == NOMBRE.lower()).first()
    if cl is None:
        cl = Cliente(cuenta_id=cu.id, nombre=NOMBRE, razon_social=NOMBRE, nombre_comercial=NOMBRE, estado="Activo")
        db.add(cl)
        db.flush()
        registrar(db, ACTOR, "cliente_creado", "cliente", str(cl.id), {"nombre": NOMBRE})
        ok(f"Cliente creado: {NOMBRE} (id {cl.id})")
    else:
        nota(f"Cliente existente: {NOMBRE} (id {cl.id})")
    return cl


def asegurar_ubicaciones(db, cu: Cuenta) -> list:
    salida = []
    for nombre, estado, municipio, direccion in TERMINALES:
        u = db.query(Ubicacion).filter(Ubicacion.cuenta_id == cu.id, Ubicacion.nombre == nombre).first()
        if u is None:
            u = Ubicacion(cuenta_id=cu.id, nombre=nombre, direccion=direccion, indicaciones=INDICACIONES, ubicacion_estado=estado,
                          ubicacion_municipio=municipio, horarios_citas=subic.normalizar_horarios(HORARIOS), activa=True)
            db.add(u)
            db.flush()
            registrar(db, ACTOR, "ubicacion_creada", "ubicacion", str(u.id), {"nombre": nombre})
            ok(f"{nombre}: {direccion} · {subic.texto_horarios(u.horarios_citas)}")
        else:
            u.activa = True
            nota(f"Ubicación existente: {nombre} (id {u.id})")
        salida.append(u)
    return salida


def asegurar_proceso(db, cu: Cuenta, u: Usuario) -> PlantillaProceso:
    pl = db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cu.id, PlantillaProceso.nombre == PROCESO).first()
    try:
        pasos = sproc.normalizar_pasos(pasos_proceso(u.id), previos=[])
    except sproc.ErrorProceso as e:
        raise Abortar(f"Proceso «{PROCESO}»: {e.mensaje}")
    if pl is None:
        pl = PlantillaProceso(cuenta_id=cu.id, nombre=PROCESO, descripcion="Operadores de tractocamión: autoagenda en la terminal y "
                              "prueba de manejo + entrevistas en la misma cita.", pasos=pasos, etapas=sproc.normalizar_etapas({}),
                              predeterminada=False, version=1, creado_por=u.nombre, actualizada_por=u.nombre)
        db.add(pl)
        db.flush()
        registrar(db, ACTOR, "plantilla_proceso_creada", "plantilla_proceso", str(pl.id), {"nombre": PROCESO, "pasos": len(pasos)})
        ok(f"Proceso «{PROCESO}»: {' → '.join(x['nombre'] for x in pasos)}")
    else:
        pl.activa = True
        nota(f"Proceso existente: «{PROCESO}» (v{pl.version})")
    return pl


def asegurar_vacante(db, cu: Cuenta, cl: Cliente, u: Usuario, pl: PlantillaProceso, terminales: list, publicar: bool) -> Vacante:
    d = VACANTE
    v = db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == d["titulo"], Vacante.estado != "Eliminada").first()
    if v is not None:
        nota(f"Vacante existente: {v.codigo} {v.titulo} ({v.estado})")
        return v
    desde, hasta = d["sueldo"]
    datos = rvac.CrearIn(
        titulo=d["titulo"], area=d["area"], seniority=d["seniority"], ubicacion_estado="Ciudad de México",
        ubicacion_municipio="Azcapotzalco", modalidad="Presencial", sueldo_desde=desde, sueldo_hasta=hasta, sueldo_moneda="MXN",
        sueldo_periodicidad="mensual", descripcion=d["descripcion"], resumen=d["descripcion"], requisitos_indispensables=d["indispensables"],
        responsabilidades=d["responsabilidades"], beneficios=d["beneficios"], cliente_id=cl.id, mostrar_cliente_candidato=True,
        proceso={"plantilla_id": pl.id}, documentos_solicitados=d["documentos"], generar_si_falta=False, publicar=publicar,
        enfoque_entrevista="operativo",  # operadores: sueldo ofrecido, colonia y traslado a SU terminal
        plataformas=["Portal", "WhatsApp"] if publicar else [], ubicaciones_ids=[t.id for t in terminales], posiciones=d["posiciones"],
        preguntas_requisitos=[{"requisito": r, "pregunta": q} for r, q in PREGUNTAS_CHAT.items()],
    )
    try:
        asyncio.run(rvac.crear(datos, db, u, cu))
    except HTTPException as e:
        raise Abortar(f"Vacante «{d['titulo']}»: {e.detail}")
    v = db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == d["titulo"]).order_by(Vacante.id.desc()).first()
    ok(f"Vacante {v.codigo} {v.titulo} · {v.ubicacion} · {v.sueldo} · {v.posiciones} posiciones · {PROCESO} · {v.estado}")
    return v


def validar(db, v: Vacante) -> list:
    """Lo que la demo necesita, revisado con las mismas funciones que usa el bot."""
    from app.serial import nombre_empresa_candidato, vacante_dict
    from app.services import prefiltro_conversacional as pconv

    problemas = []
    if ia.presentacion_bot(nombre_empresa_candidato(v)) != "Soy Red Human, el agente virtual de Fletes México.":
        problemas.append(f"Presentación del bot: «{ia.presentacion_bot(nombre_empresa_candidato(v))}»")
    if len(subic.de_vacante(db, v)) != 5:
        problemas.append("La vacante no tiene las 5 terminales")
    criterios_web = vacante_dict(v).get("criterios") or []
    preguntas_web = [c["pregunta"] for c in criterios_web]
    for r, q in PREGUNTAS_WEB.items():
        if q not in preguntas_web:
            problemas.append(f"Formulario web sin «{q}» → {preguntas_web}")
    if subic.PREGUNTA_TERMINAL not in preguntas_web:
        problemas.append("El formulario web no pregunta la terminal")
    if any(q.lower().startswith("cuéntame") for q in preguntas_web):
        problemas.append("Una pregunta web empieza con «Cuéntame»")
    criterios = pconv.criterios_de(v, Postulacion(proceso=dict(v.proceso or {}), analisis={}))
    chats = [c.get("chat") or c.get("pregunta") for c in criterios]
    for r, q in PREGUNTAS_CHAT.items():
        c = next((x for x in criterios if x.get("chat") == q), None)
        if c is None:
            problemas.append(f"El WhatsApp no pregunta «{q}» → {chats}")
            continue
        if pfu.clasificar(c, "en un mes")[0] != "no" or pfu.clasificar(c, "diciembre de 2030")[0] != "si":
            problemas.append(f"«{q}»: no descarta una vigencia menor a 3 meses")
    if not any(c.get("dato") == "terminal" for c in criterios):
        problemas.append("El WhatsApp no pregunta la terminal")
    pasos = (v.proceso or {}).get("pasos") or []
    grupo = [x["nombre"] for g in sproc.grupos_misma_cita(pasos) for x in g]
    if grupo != ["Prueba de manejo", "Entrevista con jefe de operadores", "Entrevista con sindicato"]:
        problemas.append(f"«Misma cita» no agrupa las 3 actividades → {grupo}")
    medico = next((x for x in pasos if x["tipo"] == "medica"), {})
    if medico.get("tipo_agenda") != "rh" or medico.get("misma_cita"):
        problemas.append("El examen médico debe agendarlo RH y no ir en la misma cita")
    nota(f"Ruta: {' → '.join(x['nombre'] for x in pasos)}")
    nota(f"Formulario web: {' | '.join(preguntas_web)}")
    nota(f"WhatsApp: {' | '.join(c for c in chats if c)}")
    return problemas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aplicar", action="store_true", help="guarda los cambios (sin esto es un simulacro)")
    ap.add_argument("--borrador", action="store_true", help="deja la vacante en Borrador")
    ap.add_argument("--canal", default="whatsapp", choices=list(CANALES_MENSAJERIA), help="canal con candidatos de una Cuenta NUEVA")
    a = ap.parse_args()

    db = SessionLocal()
    commit_real = db.commit
    db.commit = db.flush  # una sola transacción: los commits del endpoint solo hacen flush
    try:
        print(f"\n== Fletes México ({'APLICAR' if a.aplicar else 'SIMULACRO'}) ==")
        cu = asegurar_cuenta(db, a.canal)
        u = vincular_admins(db, cu)
        cl = asegurar_cliente(db, cu)
        print("\n-- Terminales --")
        terminales = asegurar_ubicaciones(db, cu)
        print("\n-- Proceso de selección --")
        pl = asegurar_proceso(db, cu, u)
        print("\n-- Vacante --")
        v = asegurar_vacante(db, cu, cl, u, pl, terminales, publicar=not a.borrador)
        print("\n-- Validación --")
        problemas = validar(db, v)
        if problemas:
            raise Abortar("Validación fallida:\n    " + "\n    ".join(problemas))
        ok("Validación: terminales, autoagenda, misma cita, vigencias y pregunta de terminal correctos")
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
