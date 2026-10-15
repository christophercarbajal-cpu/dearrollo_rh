"""Demo «Fletes México» (Cambio 47, 2026-10-14; procesos A y B 2026-10-15) — Cuenta, Cliente, 5 terminales con horarios
y cupo, DOS procesos y DOS vacantes de «Operador de 5ª rueda».

Solo DATOS, con las MISMAS funciones de los endpoints (`normalizar_pasos`, `routers.vacantes.crear`,
`services.ubicaciones`, `preguntas_filtro`, `guiones`), así cada registro queda idéntico a uno capturado en pantalla:
  1. Cuenta + Cliente «Fletes México» (el bot se presenta: «Soy Red Human, el agente virtual de Fletes México.»).
  2. Terminales (lunes a viernes, 9:00 y 12:00, 8 lugares por horario) con sus direcciones reales: Monterrey
     (Supermanzana Km 2.1, Entronque Laredo–Salinas Victoria, NL, 65500), Hermosillo (Blvd. de los Seris #34, Parque
     Industrial, Hermosillo, Son, 83299) y CDMX (Av. Joaquín Montenegro #178, Col. Santiago Teyahualco, Tultepec, Edomex,
     54900); Guadalajara y Querétaro con «(dirección de demo)». Indicación de la cita: «Trae tu licencia y tu apto médico
     originales.»
  3. Proceso A «Fletes operadores»: Solicitud web sin CV → Prefiltro por WhatsApp → Documentos → Prueba de manejo (la
     agenda el candidato, misma cita) → Entrevista con el jefe de operadores (misma cita) → Entrevista con el sindicato
     (misma cita) → Examen médico (la agenda RH) → Contrato y firma.
     Proceso B «Fletes operadores · Avatar»: Solicitud web sin CV → Entrevista Red Human con avatar (enfoque Operativo) →
     Documentos → Prueba de manejo → Entrevista con el jefe de operadores → Entrevista con el sindicato (misma cita) →
     Examen médico (la agenda RH) → Contrato y firma.
  4. Vacante A «Operador de 5ª rueda» (proceso A) y vacante B «Operador de 5ª rueda · Entrevista en video» (proceso B):
     las 5 terminales, presencial, 20 posiciones, $25,000 - $32,000 mensuales. Indispensables: licencia federal tipo B con
     vigencia mayor a 3 meses, apto médico con vigencia mayor a 3 meses y experiencia como operador de 5ª rueda.
     «Unas preguntas» (ambas): Sí/No eliminatorias de los tres indispensables + «¿En qué terminal te gustaría trabajar?»
     (+ interés y sueldo, Cambio 50).
     Prefiltro por WhatsApp (solo A): años de experiencia → última empresa → número de licencia → vigencia de la licencia
     (vence en menos de 3 meses → descarta) → vigencia del apto (ídem) → edad, interés, sueldo, colonia y tiempo a SU
     terminal («La Terminal [ciudad] está en [dirección]. ¿Cuánto tiempo harías de tu casa?»).
     Entrevista con avatar (solo B): el bot la ofrece por WhatsApp («… El siguiente paso de tu postulación a … es una
     entrevista en video de 5 minutos. ¿La quieres hacer ahora?»); preguntas de RH: ¿Has trabajado como operador de 5ª
     rueda? · ¿En qué empresa? · ¿Confirmas que tu apto médico está vigente? · ¿Confirmas que tu licencia federal tipo B
     está vigente? — si contradice lo que contestó en la solicitud web, la recomendación es «No avanzar». Al terminar, la
     liga de documentos («Gracias, … Súbelos aquí: [liga]»).
     Documentos: Licencia de conducir (frente y reverso) y Apto médico.
  5. Autoagenda (confirmación agrupada): «Perfecto, te esperamos el [día] a las [hora] en la Terminal [ciudad],
     [dirección]. Mapa: [liga]. Ese día harás la prueba de manejo, una entrevista con el jefe de operadores y una
     entrevista con el sindicato. Trae tu licencia y tu apto médico originales.»

Seguridad: sin `--aplicar` es un SIMULACRO (una transacción que al final se deshace); con `--aplicar` también es una sola
transacción: o queda todo o nada. Idempotente (Cuenta por slug, Cliente/ubicaciones/procesos por nombre, vacantes por
título; una ubicación existente se actualiza a su dirección real). Cero comunicaciones: no crea candidatos ni manda mensajes.

Uso (desde red-human-api/, con el .env del ambiente; la API debe haber arrancado una vez con esta versión):
    python scripts/seed_fletes_mexico.py              # simulacro
    python scripts/seed_fletes_mexico.py --aplicar    # aplica
Opciones: --borrador (vacantes en Borrador) · --canal whatsapp|telegram|ambos (Cuenta nueva)
"""

import argparse
import asyncio
import sys
from datetime import date
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
from app.services import guiones as sgui  # noqa: E402
from app.services import ia  # noqa: E402
from app.services import preguntas_filtro as pfu  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import ubicaciones as subic  # noqa: E402

ACTOR = "script:seed_fletes_mexico"
NOMBRE = "Fletes México"
SLUG = "fletes-mexico"

# ============================================================ 1. Terminales (Estado / Municipio del catálogo INEGI)
HORARIOS = [{"dias": [0, 1, 2, 3, 4], "horas": ["09:00", "12:00"], "cupo": 8}]
INDICACIONES = "Trae tu licencia y tu apto médico originales."
DEMO = "(dirección de demo)"
TERMINALES = [
    ("Terminal Guadalajara", "Jalisco", "Guadalajara", DEMO),
    ("Terminal Monterrey", "Nuevo León", "Salinas Victoria",
     "Supermanzana Km 2.1, Entronque Laredo–Salinas Victoria, Nuevo León, C.P. 65500"),
    ("Terminal CDMX", "Estado de México", "Tultepec",
     "Av. Joaquín Montenegro #178, Col. Santiago Teyahualco, Tultepec, Estado de México, C.P. 54900"),
    ("Terminal Querétaro", "Querétaro", "Querétaro", DEMO),
    ("Terminal Hermosillo", "Sonora", "Hermosillo", "Blvd. de los Seris #34, Parque Industrial, Hermosillo, Sonora, C.P. 83299"),
]

# ============================================================ 2. Procesos
PROCESO_A = "Fletes operadores"
PROCESO_B = "Fletes operadores · Avatar"
GRUPO = ["Prueba de manejo", "Entrevista con el jefe de operadores", "Entrevista con el sindicato"]


def _presenciales(ev: dict, depende_de: list) -> list:
    return [
        {"id": "prueba-manejo", "tipo": "tecnica", "nombre": GRUPO[0], "etapa": "Entrevista Humana",
         "tipo_agenda": "candidato", "misma_cita": True, "config": ev, **({"depende_de": depende_de} if depende_de else {})},
        {"id": "entrevista-jefe", "tipo": "entrevista_humana", "nombre": GRUPO[1], "etapa": "Entrevista Humana",
         "tipo_entrevista": "jefe_directo", "tipo_agenda": "candidato", "misma_cita": True, "config": ev,
         **({"depende_de": depende_de} if depende_de else {})},
        {"id": "entrevista-sindicato", "tipo": "entrevista_humana", "nombre": GRUPO[2], "etapa": "Entrevista Humana",
         "tipo_entrevista": "general", "tipo_agenda": "candidato", "misma_cita": True, "config": ev,
         **({"depende_de": depende_de} if depende_de else {})},
        {"id": "medico", "tipo": "medica", "nombre": "Examen médico", "etapa": "Entrevista Humana", "tipo_agenda": "rh",
         **({"depende_de": depende_de} if depende_de else {})},
        {"id": "contrato", "tipo": "carta_contrato", "nombre": "Contrato y firma", "etapa": "Contratación"},
    ]


def pasos_proceso_a(evaluador_id: int) -> list:
    """El evaluador de las actividades presenciales es un usuario de la Cuenta (el Administrador de la demo); RH lo cambia
    por actividad en el editor de procesos."""
    ev = {"evaluador": {"tipo": "interno", "usuario_id": evaluador_id}}
    return [
        {"id": "solicitud", "tipo": "solicitud_sin_cv", "etapa": "Prefiltro"},
        {"id": "prefiltro-whatsapp", "tipo": "prefiltro_whatsapp", "nombre": "Prefiltro por WhatsApp", "etapa": "Prefiltro"},
        {"id": "documentos", "tipo": "documentos", "nombre": "Documentos", "etapa": "Prefiltro"},
        *_presenciales(ev, []),
    ]


def pasos_proceso_b(evaluador_id: int) -> list:
    """Sin prefiltro por WhatsApp: de la solicitud web a la Entrevista con avatar (Operativo); los documentos van al
    terminar la entrevista y la autoagenda espera a que estén aprobados."""
    ev = {"evaluador": {"tipo": "interno", "usuario_id": evaluador_id}}
    return [
        {"id": "solicitud", "tipo": "solicitud_sin_cv", "etapa": "Prefiltro"},
        {"id": "entrevista-avatar", "tipo": "entrevista_agente", "nombre": "Entrevista Red Human con avatar", "etapa": "Entrevista IA",
         "enfoque": "operativo", "tipo_entrevista": "operativo"},
        {"id": "documentos", "tipo": "documentos", "nombre": "Documentos", "etapa": "Entrevista Humana"},
        *_presenciales(ev, ["documentos"]),
    ]


# ============================================================ 3. Vacantes
LICENCIA = "Licencia federal tipo B con vigencia mayor a 3 meses"
APTO = "Apto médico con vigencia mayor a 3 meses"
EXPERIENCIA = "Experiencia como operador de 5ª rueda"
INDISPENSABLES = [EXPERIENCIA, LICENCIA, APTO]
BASE_VACANTE = {
    "area": "Operaciones", "seniority": "Semi-senior", "posiciones": 20, "sueldo": (25000, 32000),
    "descripcion": ("Operador de tractocamión de 5ª rueda para rutas nacionales de Fletes México: traslado de carga entre "
                    "terminales, revisión de la unidad y entrega de documentación de embarque."),
    "responsabilidades": ["Conducir tractocamión de 5ª rueda en rutas nacionales", "Revisar la unidad antes y después de cada viaje",
                          "Entregar la carga y la documentación de embarque", "Cumplir con las normas de seguridad vial"],
    "beneficios": ["Prestaciones de ley", "Viáticos", "Seguro de vida"],
    "documentos": [{"nombre": "Licencia de conducir", "cantidad": 2, "foto": True},
                   {"nombre": "Apto médico", "cantidad": 1, "foto": False}],
}
VACANTES = [
    {"clave": "A", "titulo": "Operador de 5ª rueda", "proceso": PROCESO_A},
    {"clave": "B", "titulo": "Operador de 5ª rueda · Entrevista en video", "proceso": PROCESO_B},
]
# «Unas preguntas» (web, Sí/No eliminatorias) y la pregunta del chat (el DATO) de cada indispensable
PREGUNTAS_WEB = {EXPERIENCIA: "¿Tienes experiencia como operador de 5ª rueda?",
                 LICENCIA: "¿Tu licencia federal tipo B tiene vigencia de más de 3 meses?",
                 APTO: "¿Tu apto médico tiene vigencia de más de 3 meses?"}
PREGUNTAS_CHAT = {EXPERIENCIA: "¿Cuántos años de experiencia tienes como operador de 5ª rueda?",
                  LICENCIA: "¿Hasta cuándo está vigente tu licencia federal tipo B?",
                  APTO: "¿Hasta cuándo está vigente tu apto médico?"}
# preguntas propias de RH en el prefiltro por WhatsApp (solo se registran)
PROPIAS = [
    {"id": "propia-empresa", "requisito": "Última empresa", "pregunta": "¿En qué empresa trabajaste por última vez como operador?",
     "despues_de": EXPERIENCIA},
    {"id": "propia-licencia", "requisito": "Número de licencia", "pregunta": "¿Cuál es tu número de licencia federal?",
     "despues_de": "Última empresa"},
]
ORDEN_CHAT_A = [PREGUNTAS_CHAT[EXPERIENCIA], PROPIAS[0]["pregunta"], PROPIAS[1]["pregunta"], PREGUNTAS_CHAT[LICENCIA],
                PREGUNTAS_CHAT[APTO]]
# guion de la Entrevista con avatar (vacante B): preguntas de RH
GUION_AVATAR = {"enfoque": "operativo", "base_fija": True,
                "temas": ["Experiencia como operador de 5ª rueda", "Última empresa", "Apto médico vigente", "Licencia federal tipo B vigente"],
                "preguntas": ["¿Has trabajado como operador de 5ª rueda?", "¿En qué empresa?",
                              "¿Confirmas que tu apto médico está vigente?", "¿Confirmas que tu licencia federal tipo B está vigente?"]}


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
            cambios = {k: v for k, v in (("direccion", direccion), ("indicaciones", INDICACIONES), ("ubicacion_estado", estado),
                                         ("ubicacion_municipio", municipio)) if (getattr(u, k) or "") != v}
            for k, v in cambios.items():
                setattr(u, k, v)
            u.activa = True
            if cambios:
                registrar(db, ACTOR, "ubicacion_actualizada", "ubicacion", str(u.id), {"nombre": nombre, "cambios": list(cambios)})
                ok(f"{nombre} actualizada: {direccion}")
            else:
                nota(f"Ubicación existente: {nombre} (id {u.id})")
        salida.append(u)
    db.flush()
    return salida


def asegurar_proceso(db, cu: Cuenta, u: Usuario, nombre: str, pasos_crudos: list, descripcion: str) -> PlantillaProceso:
    pl = db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cu.id, PlantillaProceso.nombre == nombre).first()
    try:
        pasos = sproc.normalizar_pasos(pasos_crudos, previos=[])
    except sproc.ErrorProceso as e:
        raise Abortar(f"Proceso «{nombre}»: {e.mensaje}")
    if pl is None:
        pl = PlantillaProceso(cuenta_id=cu.id, nombre=nombre, descripcion=descripcion, pasos=pasos, etapas=sproc.normalizar_etapas({}),
                              predeterminada=False, version=1, creado_por=u.nombre, actualizada_por=u.nombre)
        db.add(pl)
        db.flush()
        registrar(db, ACTOR, "plantilla_proceso_creada", "plantilla_proceso", str(pl.id), {"nombre": nombre, "pasos": len(pasos)})
        ok(f"Proceso «{nombre}»: {' → '.join(x['nombre'] for x in pasos)}")
        return pl
    pl.activa = True
    if sproc._firma_pasos(pl.pasos) != sproc._firma_pasos(pasos) or [x["nombre"] for x in pl.pasos or []] != [x["nombre"] for x in pasos]:
        pl.pasos = pasos
        pl.version = int(pl.version or 1) + 1
        pl.actualizada_por = u.nombre
        registrar(db, ACTOR, "plantilla_proceso_actualizada", "plantilla_proceso", str(pl.id), {"version": pl.version})
        ok(f"Proceso «{nombre}» actualizado a v{pl.version}: {' → '.join(x['nombre'] for x in pasos)}")
    else:
        nota(f"Proceso existente: «{nombre}» (v{pl.version})")
    return pl


def configurar_preguntas(v: Vacante) -> None:
    """«Preguntas filtro» de la vacante: Sí/No directos en la web, el DATO en el chat y las preguntas propias de RH."""
    lista = [dict(q) for q in pfu.de_vacante(v) if not q.get("propia")]
    for q in lista:
        req = q.get("requisito")
        if q.get("indispensable") and req in PREGUNTAS_CHAT:
            q["pregunta"] = PREGUNTAS_CHAT[req]
            q["pregunta_web"] = PREGUNTAS_WEB[req]
            q["descarta"] = True
    pfu.sincronizar(v, entrada=lista + [dict(x, propia=True) for x in PROPIAS])


def asegurar_vacante(db, cu: Cuenta, cl: Cliente, u: Usuario, pl: PlantillaProceso, terminales: list, d: dict, publicar: bool) -> Vacante:
    v = db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == d["titulo"], Vacante.estado != "Eliminada").first()
    if v is not None:
        nota(f"Vacante existente: {v.codigo} {v.titulo} ({v.estado})")
    else:
        b = BASE_VACANTE
        desde, hasta = b["sueldo"]
        datos = rvac.CrearIn(
            titulo=d["titulo"], area=b["area"], seniority=b["seniority"], ubicacion_estado="Estado de México",
            ubicacion_municipio="Tultepec", modalidad="Presencial", sueldo_desde=desde, sueldo_hasta=hasta, sueldo_moneda="MXN",
            sueldo_periodicidad="mensual", descripcion=b["descripcion"], resumen=b["descripcion"], requisitos_indispensables=INDISPENSABLES,
            responsabilidades=b["responsabilidades"], beneficios=b["beneficios"], cliente_id=cl.id, mostrar_cliente_candidato=True,
            proceso={"plantilla_id": pl.id}, documentos_solicitados=b["documentos"], generar_si_falta=False, publicar=False,
            enfoque_entrevista="operativo", plataformas=[], ubicaciones_ids=[t.id for t in terminales], posiciones=b["posiciones"],
            preguntas_requisitos=[{"requisito": r, "pregunta": q} for r, q in PREGUNTAS_CHAT.items()],
        )
        try:
            asyncio.run(rvac.crear(datos, db, u, cu))
        except HTTPException as e:
            raise Abortar(f"Vacante «{d['titulo']}»: {e.detail}")
        v = db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == d["titulo"]).order_by(Vacante.id.desc()).first()
        ok(f"Vacante {v.codigo} {v.titulo} · {v.sueldo} · {v.posiciones} posiciones · {d['proceso']}")
    configurar_preguntas(v)
    if d["clave"] == "B":
        sgui.guardar_seccion(v, "entrevista_avatar", GUION_AVATAR, u.nombre)
    db.flush()
    if publicar and v.estado != "Publicada":
        v.estado = "Publicada"
        from datetime import datetime, timezone

        v.publicada_en = v.publicada_en or datetime.now(timezone.utc)
        registrar(db, ACTOR, "vacante_publicada", "vacante", v.codigo, {"titulo": v.titulo})
        ok(f"{v.codigo} publicada")
    return v


def validar(db, v: Vacante, clave: str) -> list:
    """Lo que la demo necesita, revisado con las mismas funciones que usa el bot."""
    from app.serial import nombre_empresa_candidato, vacante_dict
    from app.services import autoagenda
    from app.services import prefiltro_conversacional as pconv

    problemas = []
    if ia.presentacion_bot(nombre_empresa_candidato(v)) != "Soy Red Human, el agente virtual de Fletes México.":
        problemas.append(f"Presentación del bot: «{ia.presentacion_bot(nombre_empresa_candidato(v))}»")
    terminales = subic.de_vacante(db, v)
    if len(terminales) != 5:
        problemas.append("La vacante no tiene las 5 terminales")
    criterios_web = vacante_dict(v).get("criterios") or []
    preguntas_web = [c["pregunta"] for c in criterios_web]
    for q in PREGUNTAS_WEB.values():
        c = next((x for x in criterios_web if x["pregunta"] == q), None)
        if c is None or not c.get("descarta") or c.get("tipo") != "si_no":
            problemas.append(f"«Unas preguntas» sin «{q}» como Sí/No eliminatoria → {preguntas_web}")
    if subic.PREGUNTA_TERMINAL not in preguntas_web:
        problemas.append("«Unas preguntas» no pregunta la terminal")
    if any(q.lower().startswith("cuéntame") for q in preguntas_web):
        problemas.append("Una pregunta web empieza con «Cuéntame»")
    pasos = (v.proceso or {}).get("pasos") or []
    tipos = [x["tipo"] for x in pasos]
    grupo = [x["nombre"] for g in sproc.grupos_misma_cita(pasos) for x in g]
    if grupo != GRUPO:
        problemas.append(f"«Misma cita» no agrupa las 3 actividades → {grupo}")
    medico = next((x for x in pasos if x["tipo"] == "medica"), {})
    if medico.get("tipo_agenda") != "rh" or medico.get("misma_cita"):
        problemas.append("El examen médico debe agendarlo RH y no ir en la misma cita")
    if "prefiltro_web" in tipos:
        problemas.append("La ruta trae «Prefiltro web» (Cambio 50: la solicitud ES el prefiltro)")
    mty = next((t for t in terminales if t.nombre == "Terminal Monterrey"), None)
    if mty is not None:
        conf = autoagenda.texto_confirmacion(mty, date(2026, 10, 20), "09:00", GRUPO)
        esperado = ("Ese día harás la prueba de manejo, una entrevista con el jefe de operadores y una entrevista con el sindicato. "
                    "Trae tu licencia y tu apto médico originales.")
        if not conf.startswith("Perfecto, te esperamos el martes 20 de octubre a las 9:00 a.m. en la Terminal Monterrey, Supermanzana") \
                or not conf.endswith(esperado) or "Mapa: " not in conf:
            problemas.append(f"Confirmación de la autoagenda: «{conf}»")
    if clave == "A":
        if tipos[:3] != ["solicitud_web", "prefiltro_whatsapp", "documentos"]:
            problemas.append(f"Ruta A: {tipos}")
        sim = Postulacion(proceso=dict(v.proceso or {}), analisis={})
        criterios = pconv.criterios_de(v, sim)
        chats = [c.get("chat") or c.get("pregunta") for c in criterios if c.get("dato") != "terminal"]
        if chats[:5] != ORDEN_CHAT_A:
            problemas.append(f"El WhatsApp no empieza con {ORDEN_CHAT_A} → {chats}")
        for r in (LICENCIA, APTO):
            c = next((x for x in criterios if x.get("chat") == PREGUNTAS_CHAT[r]), None)
            if c is None or pfu.clasificar(c, "en un mes")[0] != "no" or pfu.clasificar(c, "diciembre de 2030")[0] != "si":
                problemas.append(f"«{PREGUNTAS_CHAT[r]}»: no descarta una vigencia menor a 3 meses")
        datos = [c.get("dato") for c in criterios if c.get("base")]
        if datos != ["edad", "interes", "sueldo", "zona", "traslado"]:
            problemas.append(f"Base operativa del WhatsApp: {datos}")
        sim.ubicacion_id = mty.id if mty is not None else None
        sim.vacante = v
        try:
            tras = next((c for c in pconv.criterios_de(v, sim) if c.get("dato") == "traslado"), {})
        finally:
            if sim in db:
                db.expunge(sim)  # simulación: nunca se guarda
            sim.vacante = None
        if mty is not None and tras.get("chat") != f"La Terminal Monterrey está en {mty.direccion}. ¿Cuánto tiempo harías de tu casa?":
            problemas.append(f"Tiempo a la terminal: «{tras.get('chat')}»")
        nota(f"WhatsApp: {' | '.join(c for c in chats if c)}")
    else:
        if tipos[:3] != ["solicitud_web", "entrevista_agente", "documentos"]:
            problemas.append(f"Ruta B: {tipos}")
        avatar = next((x for x in pasos if x["tipo"] == "entrevista_agente"), {})
        if avatar.get("enfoque") != "operativo":
            problemas.append("La entrevista con avatar no tiene enfoque Operativo")
        guion = sgui.contenido_de(v, "entrevista_avatar") or {}
        if (guion.get("preguntas") or []) != GUION_AVATAR["preguntas"]:
            problemas.append(f"Guion del avatar: {guion.get('preguntas')}")
        manejo = next((x for x in pasos if x["id"] == "prueba-manejo"), {})
        if "documentos" not in (manejo.get("depende_de") or []):
            problemas.append("La autoagenda (prueba de manejo) debe esperar a los documentos")
    nota(f"Ruta {clave}: {' → '.join(x['nombre'] for x in pasos)}")
    nota(f"Unas preguntas: {' | '.join(preguntas_web)}")
    return problemas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aplicar", action="store_true", help="guarda los cambios (sin esto es un simulacro)")
    ap.add_argument("--borrador", action="store_true", help="deja las vacantes en Borrador")
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
        print("\n-- Procesos de selección --")
        procesos = {
            PROCESO_A: asegurar_proceso(db, cu, u, PROCESO_A, pasos_proceso_a(u.id), "Operadores de tractocamión: prefiltro por "
                                        "WhatsApp, autoagenda en la terminal y prueba de manejo + entrevistas en la misma cita."),
            PROCESO_B: asegurar_proceso(db, cu, u, PROCESO_B, pasos_proceso_b(u.id), "Operadores de tractocamión: entrevista en "
                                        "video con Red Human, documentos y prueba de manejo + entrevistas en la misma cita."),
        }
        problemas = []
        for d in VACANTES:
            print(f"\n-- Vacante {d['clave']} --")
            v = asegurar_vacante(db, cu, cl, u, procesos[d["proceso"]], terminales, d, publicar=not a.borrador)
            problemas += [f"{d['clave']}: {x}" for x in validar(db, v, d["clave"])]
        print("\n-- Validación --")
        if problemas:
            raise Abortar("Validación fallida:\n    " + "\n    ".join(problemas))
        ok("Validación: terminales, procesos A y B, «Unas preguntas», prefiltro por WhatsApp, avatar y autoagenda correctos")
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
