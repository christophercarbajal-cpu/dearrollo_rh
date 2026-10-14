"""Demo «Grupo DECASA» (2026-10-13) — Cuenta, Cliente, CEDIS, 3 procesos de selección y 3 vacantes publicadas.

Solo DATOS, con las MISMAS funciones de los endpoints (`POST /procesos/plantillas` → `normalizar_pasos`;
`POST /vacantes` → `routers.vacantes.crear`), así cada registro queda idéntico a uno capturado en pantalla:
  1. Cuenta «Grupo DECASA» (rutas base incluidas, igual que `POST /cuentas`) + Cliente «Grupo DECASA».
  2. CEDIS: la plataforma no tiene un catálogo de sedes; los 8 CEDIS viven en `CEDIS` (Estado/Municipio del catálogo
     INEGI) y cada vacante toma el suyo como ubicación («CEDIS Cortázar 1 · Cortazar, Guanajuato»).
  3. Procesos (Cambios 30-31): «Decasa almacén», «Decasa choferes» (+ Estudio socioeconómico tras el médico) y
     «Decasa ventas» (Solicitud web CON CV —ya trae el análisis— y Entrevista Red Human con avatar). Contratación =
     «Oferta laboral» (reemplaza la carta de intención) + «Contrato y firma».
  4. Vacantes: Almacenista (Cortázar 1), Chofer de Torton (Puebla), Agente de ventas (Guadalajara). Indispensables con
     pregunta NATURAL (Cambio 31); «No más de 2 transportes» descarta a quien diga 3 o más (dato `transportes`).
     No se configura edad, estado civil ni créditos como filtro.

Seguridad:
  * Sin `--aplicar` es un SIMULACRO: corre todo en UNA transacción y al final la deshace (los `commit` del endpoint se
    vuelven `flush`). Con `--aplicar` también es una sola transacción: o queda todo o nada.
  * Idempotente: Cuenta por slug/nombre, Cliente y procesos por nombre, vacantes por título dentro de la Cuenta.
  * Cero comunicaciones: no crea candidatos ni manda mensajes.

Uso (desde red-human-api/, con el .env del ambiente; la API debe haber arrancado una vez con esta versión):
    python scripts/seed_decasa.py              # simulacro
    python scripts/seed_decasa.py --aplicar    # aplica
Opciones: --borrador (vacantes en Borrador en vez de Publicadas) · --canal whatsapp|telegram|ambos
"""

import asyncio
import argparse
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
    CANALES_MENSAJERIA, Cliente, Cuenta, PlantillaProceso, Postulacion, Usuario, UsuarioCuenta, Vacante, registrar,
)
from app.routers import vacantes as rvac  # noqa: E402
from app.seed import slug_cuenta_unico  # noqa: E402
from app.services import guiones as sgui  # noqa: E402
from app.services import ia  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import sincronizacion_vacante as ssv  # noqa: E402

ACTOR = "script:seed_decasa"
NOMBRE = "Grupo DECASA"

# ============================================================ 1. CEDIS (Estado / Municipio del catálogo INEGI)
CEDIS = {
    "Cortázar 1": ("Guanajuato", "Cortazar"),
    "Cortázar 2": ("Guanajuato", "Cortazar"),
    "Veracruz": ("Veracruz", "Veracruz"),
    "Aguascalientes": ("Aguascalientes", "Aguascalientes"),
    "Guadalajara": ("Jalisco", "Guadalajara"),
    "Puebla": ("Puebla", "Puebla"),
    "Torreón": ("Coahuila", "Torreón"),
    "Tuxtla Gutiérrez": ("Chiapas", "Tuxtla Gutiérrez"),
}

# ============================================================ 2. Procesos de selección
# Los `tipo` son las entradas del catálogo (models.CATALOGO_ACTIVIDADES); `normalizar_pasos` valida etapa y orden.
FILTRO_HUMANO = [
    {"id": "entrevista-rh", "tipo": "entrevista_humana", "nombre": "Entrevista con RH", "etapa": "Entrevista Humana",
     "tipo_entrevista": "general"},
    {"id": "entrevista-jefe", "tipo": "entrevista_humana", "nombre": "Entrevista con jefe directo", "etapa": "Entrevista Humana",
     "tipo_entrevista": "jefe_directo"},
    {"id": "medico", "tipo": "medica", "nombre": "Examen médico y antidoping", "etapa": "Entrevista Humana"},
]
SOCIOECONOMICO = {"id": "socioeconomico", "tipo": "socioeconomica", "nombre": "Estudio socioeconómico", "etapa": "Entrevista Humana"}
CONTRATACION = [
    {"id": "oferta", "tipo": "oferta", "nombre": "Oferta laboral", "etapa": "Contratación"},
    {"id": "contrato", "tipo": "carta_contrato", "nombre": "Contrato y firma", "etapa": "Contratación"},
]


def _prefiltro(con_cv: bool) -> list:
    return [
        {"id": "solicitud", "tipo": "solicitud_con_cv" if con_cv else "solicitud_sin_cv", "etapa": "Prefiltro"},
        {"id": "prefiltro-web", "tipo": "prefiltro_web", "nombre": "Prefiltro web", "etapa": "Prefiltro"},
        {"id": "prefiltro-whatsapp", "tipo": "prefiltro_whatsapp", "nombre": "Prefiltro por WhatsApp", "etapa": "Prefiltro"},
        {"id": "documentos", "tipo": "documentos", "nombre": "Documentos", "etapa": "Prefiltro"},
    ]


PROCESOS = {
    "Decasa almacén": _prefiltro(False) + [
        {"id": "entrevista-whatsapp", "tipo": "entrevista_whatsapp", "nombre": "Entrevista Red Human por WhatsApp",
         "etapa": "Entrevista IA"},
    ] + FILTRO_HUMANO + CONTRATACION,
    "Decasa choferes": _prefiltro(False) + [
        {"id": "entrevista-whatsapp", "tipo": "entrevista_whatsapp", "nombre": "Entrevista Red Human por WhatsApp",
         "etapa": "Entrevista IA"},
    ] + FILTRO_HUMANO + [SOCIOECONOMICO] + CONTRATACION,
    "Decasa ventas": _prefiltro(True) + [
        {"id": "entrevista-avatar", "tipo": "entrevista_agente", "nombre": "Entrevista Red Human con avatar",
         "etapa": "Entrevista IA"},
    ] + FILTRO_HUMANO + [SOCIOECONOMICO] + CONTRATACION,
}
DESCRIPCION_PROCESO = {
    "Decasa almacén": "Personal operativo de almacén en CEDIS.",
    "Decasa choferes": "Choferes de reparto (torton): incluye estudio socioeconómico.",
    "Decasa ventas": "Agentes de ventas: solicitud con CV y entrevista con avatar.",
}

# ============================================================ 3. Vacantes
TRANSPORTES = "No más de 2 transportes para llegar al CEDIS"
PREGUNTA_TRANSPORTES = "¿Cuántos transportes tomarías para llegar al CEDIS?"
DOCS_BASE = [
    {"nombre": "INE (frente y reverso)", "cantidad": 2, "foto": True},
    {"nombre": "Comprobante de domicilio", "cantidad": 1, "foto": False},
    {"nombre": "CURP", "cantidad": 1, "foto": False},
    {"nombre": "Acta de nacimiento", "cantidad": 1, "foto": False},
    {"nombre": "Número de Seguridad Social (NSS)", "cantidad": 1, "foto": False},
    {"nombre": "Constancia de situación fiscal (RFC)", "cantidad": 1, "foto": False},
]
PRESTACIONES = ["Prestaciones de ley", "Vales de despensa", "Fondo de ahorro"]


def _web(pregunta: str, requisito: str, descarta: bool = True) -> dict:
    """Prefiltro web: SOLO Sí / No (Cambio 2026-10-11); la eliminatoria es el indispensable copiado literal."""
    return {"pregunta": pregunta, "tipo": "si_no", "valida": requisito, "respuesta_esperada": "Sí", "descarta": descarta,
            "opciones": ["Sí", "No"]}


VACANTES = [
    {
        "titulo": "Almacenista", "cedis": "Cortázar 1", "proceso": "Decasa almacén", "area": "Almacén", "seniority": "Junior",
        "sueldo": (11500, 13000),
        "descripcion": ("Almacenista para el CEDIS Cortázar 1 de Grupo DECASA: recepción, acomodo, surtido y embarque de "
                        "mercancía; conteos de inventario y orden del área."),
        "responsabilidades": ["Recibir y revisar mercancía contra factura", "Acomodar y surtir pedidos",
                              "Cargar y descargar unidades", "Apoyar en conteos de inventario"],
        "indispensables": {
            TRANSPORTES: PREGUNTA_TRANSPORTES,
            "Al menos 6 meses de experiencia en almacén": "Cuéntame, ¿cuánto tiempo has trabajado en almacén?",
            "Disponibilidad para rolar turnos": "¿Qué disponibilidad tienes para rolar turnos?",
        },
        "web": [
            _web("¿Tomarías máximo 2 transportes para llegar al CEDIS Cortázar 1?", TRANSPORTES),
            _web("¿Tienes al menos 6 meses de experiencia en almacén?", "Al menos 6 meses de experiencia en almacén"),
            _web("¿Puedes rolar turnos?", "Disponibilidad para rolar turnos"),
        ],
        "documentos": DOCS_BASE,
    },
    {
        "titulo": "Chofer de Torton", "cedis": "Puebla", "proceso": "Decasa choferes", "area": "Distribución",
        "seniority": "Semi-senior", "sueldo": (18000, 22000),
        "descripcion": ("Chofer de torton para el CEDIS Puebla de Grupo DECASA: reparto a clientes en ruta asignada, "
                        "revisión de la unidad y entrega de documentación de embarque."),
        "responsabilidades": ["Realizar las entregas de la ruta asignada", "Revisar la unidad antes de salir",
                              "Entregar evidencias y documentación de embarque", "Cuidar la mercancía y la unidad"],
        "indispensables": {
            "Licencia federal tipo B o C vigente": "¿Qué tipo de licencia tienes y hasta cuándo está vigente?",
            TRANSPORTES: PREGUNTA_TRANSPORTES,
            "Al menos 1 año de experiencia manejando torton": "¿Cuánto tiempo llevas manejando torton?",
        },
        "web": [
            _web("¿Tienes licencia federal tipo B o C vigente?", "Licencia federal tipo B o C vigente"),
            _web("¿Tomarías máximo 2 transportes para llegar al CEDIS Puebla?", TRANSPORTES),
            _web("¿Tienes al menos 1 año de experiencia manejando torton?", "Al menos 1 año de experiencia manejando torton"),
        ],
        "documentos": DOCS_BASE + [{"nombre": "Licencia federal de conducir (frente y reverso)", "cantidad": 2, "foto": True}],
        "entrevista_whatsapp": ["¿Has manejado torton? ¿Cuánto tiempo?"],
    },
    {
        "titulo": "Agente de ventas", "cedis": "Guadalajara", "proceso": "Decasa ventas", "area": "Ventas",
        "seniority": "Junior", "sueldo": (12000, None), "comisiones": True,
        "descripcion": ("Agente de ventas para el CEDIS Guadalajara de Grupo DECASA: prospección de clientes, visitas en "
                        "campo, levantamiento de pedidos y seguimiento de cartera. Sueldo base de $12,000 más comisiones."),
        "responsabilidades": ["Prospectar y visitar clientes en campo", "Levantar pedidos y dar seguimiento a la cartera",
                              "Cerrar ventas y manejar objeciones", "Reportar avance de ventas"],
        "indispensables": {
            TRANSPORTES: PREGUNTA_TRANSPORTES,
            "Al menos 1 año de experiencia en ventas": "Cuéntame, ¿cuánto tiempo llevas trabajando en ventas?",
            "Disponibilidad para visitar clientes en campo": "¿Qué disponibilidad tienes para visitar clientes en campo?",
        },
        "web": [
            _web("¿Tomarías máximo 2 transportes para llegar al CEDIS Guadalajara?", TRANSPORTES),
            _web("¿Tienes al menos 1 año de experiencia en ventas?", "Al menos 1 año de experiencia en ventas"),
            _web("¿Puedes visitar clientes en campo?", "Disponibilidad para visitar clientes en campo"),
        ],
        # Prefiltro configurable (ruta corporativa): reconfirmaciones con dato concreto. El de transportes es un tope.
        "whatsapp": [
            {"pregunta": PREGUNTA_TRANSPORTES, "tipo": "numero", "valida": TRANSPORTES, "reconfirma": TRANSPORTES,
             "maximo": 2, "descarta": True, "opciones": ["0", "1", "2", "3 o más"]},
            {"pregunta": "¿Cuánto tiempo llevas trabajando en ventas?", "tipo": "numero", "valida": "Al menos 1 año de experiencia en ventas",
             "reconfirma": "Al menos 1 año de experiencia en ventas", "minimo": 1, "descarta": True,
             "opciones": ["Menos de 1 año", "1 a 2 años", "Más de 2 años"]},
            {"pregunta": "¿Puedes visitar clientes en campo?", "tipo": "si_no", "valida": "Disponibilidad para visitar clientes en campo",
             "reconfirma": "Disponibilidad para visitar clientes en campo", "descarta": True, "opciones_validas": ["Sí"]},
        ],
        "documentos": DOCS_BASE,
        "avatar": {
            "enfoque": ("Entrevista por competencias de ventas: prospección, cierre, manejo de objeciones, seguimiento de "
                        "cartera y orientación a resultados."),
            "temas": ["Prospección", "Cierre de ventas", "Manejo de objeciones", "Seguimiento de cartera", "Orientación a resultados"],
            "preguntas": [
                "Cuéntame cómo consigues clientes nuevos en una zona que no conoces.",
                "Platícame de la última venta difícil que cerraste: ¿qué hiciste para cerrarla?",
                "Cuando un cliente te dice que está muy caro, ¿cómo le respondes?",
                "¿Cómo organizas tus visitas y el seguimiento de tu cartera durante la semana?",
                "Cuéntame de una meta de ventas que no alcanzaste y qué cambiaste después.",
                "¿Qué resultados concretos lograste en tu último trabajo de ventas?",
            ],
        },
    },
]


class Abortar(Exception):
    pass


def ok(t: str) -> None:
    print(f"  ✓ {t}")


def nota(t: str) -> None:
    print(f"  · {t}")


# ============================================================ pasos

def asegurar_cuenta(db, canal: str) -> Cuenta:
    cu = (db.query(Cuenta).filter((Cuenta.slug == "grupo-decasa") | (func.lower(Cuenta.nombre) == NOMBRE.lower()))
          .order_by(Cuenta.id).first())
    if cu is not None:
        if cu.estado == "Eliminada":
            raise Abortar(f"La Cuenta «{cu.nombre_visible}» (id {cu.id}) está eliminada. Restáurala y vuelve a correr el script.")
        nota(f"Cuenta existente: {cu.nombre_visible} (id {cu.id}, slug {cu.slug})")
    else:
        cu = Cuenta(nombre=NOMBRE, nombre_comercial=NOMBRE, razon_social=NOMBRE, contacto_nombre="RH Grupo DECASA",
                    canal_mensajeria=canal, estado="Activa")
        db.add(cu)
        db.flush()
        cu.slug = slug_cuenta_unico(db, cu.nombre_comercial, cu.id)
        registrar(db, ACTOR, "cuenta_creada", "cuenta", str(cu.id), {"nombre": NOMBRE})
        ok(f"Cuenta creada: {NOMBRE} (id {cu.id}, slug {cu.slug}, canal {canal})")
    with db.begin_nested():
        n = sproc.asegurar_rutas_base(db, cu.id, ACTOR)
    if n:
        ok(f"Rutas base sembradas: {n}")
    return cu


def vincular_admins(db, cu: Cuenta) -> Usuario:
    admins = [u for u in db.query(Usuario).filter(Usuario.rol == "Administrador").order_by(Usuario.id).all()
              if getattr(u, "activo", True)]
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
        registrar(db, ACTOR, "cliente_creado", "cliente", str(cl.id), {"nombre": NOMBRE, "cedis": list(CEDIS)})
        ok(f"Cliente creado: {NOMBRE} (id {cl.id})")
    else:
        nota(f"Cliente existente: {NOMBRE} (id {cl.id})")
    for nombre, (estado, municipio) in CEDIS.items():
        nota(f"CEDIS {nombre}: {municipio}, {estado}")
    return cl


def asegurar_procesos(db, cu: Cuenta, u: Usuario) -> dict:
    salida = {}
    for nombre, pasos in PROCESOS.items():
        pl = db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cu.id, PlantillaProceso.nombre == nombre).first()
        try:
            normalizados = sproc.normalizar_pasos(pasos, previos=[])
        except sproc.ErrorProceso as e:
            raise Abortar(f"Proceso «{nombre}»: {e.mensaje}")
        if pl is None:
            pl = PlantillaProceso(cuenta_id=cu.id, nombre=nombre, descripcion=DESCRIPCION_PROCESO[nombre], pasos=normalizados,
                                  etapas=sproc.normalizar_etapas({}), predeterminada=False, version=1, creado_por=u.nombre,
                                  actualizada_por=u.nombre)
            db.add(pl)
            db.flush()
            registrar(db, ACTOR, "plantilla_proceso_creada", "plantilla_proceso", str(pl.id), {"nombre": nombre, "pasos": len(normalizados)})
            ok(f"Proceso «{nombre}»: {' → '.join(p['nombre'] for p in normalizados)}")
        else:
            if not pl.activa:
                pl.activa = True
            nota(f"Proceso existente: «{nombre}» (v{pl.version}, {len(pl.pasos or [])} actividades)")
        salida[nombre] = pl
    return salida


def asegurar_vacantes(db, cu: Cuenta, cl: Cliente, u: Usuario, procesos: dict, publicar: bool) -> list:
    hechas = []
    for d in VACANTES:
        v = db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == d["titulo"], Vacante.estado != "Eliminada").first()
        if v is not None:
            nota(f"Vacante existente: {v.codigo} {v.titulo} ({v.estado})")
            hechas.append(v)
            continue
        estado, municipio = CEDIS[d["cedis"]]
        desde, hasta = d["sueldo"]
        guiones = {"secciones": {}}
        if d.get("avatar"):
            guiones["secciones"]["entrevista_avatar"] = d["avatar"]
        if d.get("entrevista_whatsapp"):
            guiones["entrevista_whatsapp_base"] = {"propias": d["entrevista_whatsapp"]}
        beneficios = PRESTACIONES + (["Comisiones por venta"] if d.get("comisiones") else [])
        datos = rvac.CrearIn(
            titulo=d["titulo"], area=d["area"], seniority=d["seniority"], ubicacion_estado=estado, ubicacion_municipio=municipio,
            modalidad="Presencial", sueldo_desde=desde, sueldo_hasta=hasta, sueldo_moneda="MXN", sueldo_periodicidad="mensual",
            descripcion=d["descripcion"], resumen=d["descripcion"], requisitos_indispensables=list(d["indispensables"]),
            responsabilidades=d["responsabilidades"], beneficios=beneficios, cliente_id=cl.id, mostrar_cliente_candidato=True,
            proceso={"plantilla_id": procesos[d["proceso"]].id}, preguntas_filtro=d["web"],
            preguntas_filtro_whatsapp=d.get("whatsapp") or [], documentos_solicitados=d["documentos"],
            preguntas_requisitos=[{"requisito": r, "pregunta": q} for r, q in d["indispensables"].items()],
            guiones=guiones, generar_si_falta=False, publicar=publicar, plataformas=["Portal", "WhatsApp"] if publicar else [],
        )
        try:
            asyncio.run(rvac.crear(datos, db, u, cu))  # Cambio 36: el alta es asíncrona (publica en sus canales)
        except HTTPException as e:
            raise Abortar(f"Vacante «{d['titulo']}»: {e.detail}")
        v = db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == d["titulo"]).order_by(Vacante.id.desc()).first()
        v.ubicacion = f"CEDIS {d['cedis']} · {municipio}, {estado}"
        # Plantillas de conversación que falten según la ruta (Masivos: base fija sin IA; lo capturado aquí se conserva)
        sgui.generar_para_vacante(db, v, u.nombre)
        sgui.guardar_seccion(v, "prefiltro_web", d["web"], u.nombre)  # el prefiltro web de DECASA, tal cual
        if d.get("whatsapp"):
            # las reconfirmaciones de DECASA (tope de transportes, mínimo de experiencia) reemplazan a las generadas
            propias = {x["reconfirma"]: x for x in d["whatsapp"]}
            generadas = list(v.preguntas_filtro_whatsapp or [])
            nuevas = [propias.pop(q.get("reconfirma"), q) if isinstance(q, dict) else q for q in generadas]
            sgui.guardar_seccion(v, "prefiltro_whatsapp", list(propias.values()) + nuevas, u.nombre)
        db.flush()
        ok(f"Vacante {v.codigo} {v.titulo} · {v.ubicacion} · {v.sueldo} · {d['proceso']} · {v.estado}")
        hechas.append(v)
    return hechas


def validar(db, cu: Cuenta, vacantes: list) -> list:
    """Lo que la demo necesita: ruta con sus actividades, sin faltantes para publicar y preguntas naturales."""
    from app.services import prefiltro_conversacional as pconv

    problemas = []
    for v in vacantes:
        pasos = sgui.pasos_de_vacante(db, v)
        tipos = [sproc.clave_catalogo(x) for x in pasos]
        for prohibido in ("analisis_cv", "carta_intencion", "condiciones"):
            if prohibido in tipos:
                problemas.append(f"{v.titulo}: la ruta trae «{prohibido}» (legado)")
        if "oferta" not in tipos:
            problemas.append(f"{v.titulo}: falta «Oferta laboral»")
        faltan = ssv.faltantes_publicacion(db, v)
        if faltan:
            problemas.append(f"{v.titulo}: {' '.join(faltan)}")
        if len(v.preguntas_filtro or []) < ia.MIN_PREFILTRO_WEB:
            problemas.append(f"{v.titulo}: prefiltro web con menos de {ia.MIN_PREFILTRO_WEB} preguntas")
        # postulación simulada (no se guarda) con la ruta de la vacante: así se evalúa la ruta corporativa como en vivo
        criterios = pconv.criterios_de(v, Postulacion(proceso=dict(v.proceso or {}), analisis={}))
        chats = [c.get("chat") or c.get("pregunta") for c in criterios]
        if PREGUNTA_TRANSPORTES not in chats:
            problemas.append(f"{v.titulo}: el prefiltro de WhatsApp no pregunta «{PREGUNTA_TRANSPORTES}» → {chats}")
        tr = next((c for c in criterios if (c.get("chat") or c.get("pregunta")) == PREGUNTA_TRANSPORTES), {})
        if tr.get("dato") == "transportes":
            from app.services import prefiltro_operativo as pop

            if pop.clasificar(tr, "tres camiones")[0] != "no" or pop.clasificar(tr, "2")[0] != "si":
                problemas.append(f"{v.titulo}: «más de 2 transportes» no descarta")
        elif pconv.clasificar_dato(tr, "tres camiones") != "no" or pconv.clasificar_dato(tr, "dos") != "si":
            problemas.append(f"{v.titulo}: «más de 2 transportes» no descarta (reconfirmación)")
        if any("Para confirmar" in (c or "") for c in chats):
            problemas.append(f"{v.titulo}: quedó una pregunta de formulario «Para confirmar…»")
        nota(f"{v.titulo}: {' → '.join(x['nombre'] for x in pasos)}")
        nota(f"   prefiltro WhatsApp: {' | '.join(c for c in chats if c)}")
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
        print(f"\n== Grupo DECASA ({'APLICAR' if a.aplicar else 'SIMULACRO'}) ==")
        cu = asegurar_cuenta(db, a.canal)
        u = vincular_admins(db, cu)
        cl = asegurar_cliente(db, cu)
        print("\n-- Procesos de selección --")
        procesos = asegurar_procesos(db, cu, u)
        print("\n-- Vacantes --")
        vacantes = asegurar_vacantes(db, cu, cl, u, procesos, publicar=not a.borrador)
        print("\n-- Validación --")
        problemas = validar(db, cu, vacantes)
        if problemas:
            raise Abortar("Validación fallida:\n    " + "\n    ".join(problemas))
        ok("Validación: rutas, preguntas naturales, tope de transportes y requisitos de publicación correctos")
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
