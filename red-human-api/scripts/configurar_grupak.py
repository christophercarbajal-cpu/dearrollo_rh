"""Grupak · VAC-1156 (2026-10-13; reestructura 2026-10-15) — proceso «Operativo básico» y conversación de la vacante.

Solo DATOS, con las MISMAS funciones de los endpoints (`normalizar_pasos`, `proceso_para_vacante`, `routers.vacantes.crear`,
`preguntas_filtro`, `guiones`), así cada registro queda idéntico a uno capturado en pantalla:
  1. Cuenta: la de la VAC-1156 si existe; si no, la Cuenta Grupak («Demo Grupak» / «Grupak») o una nueva «Grupak».
     Cliente «Grupak» en esa Cuenta, visible al candidato (el bot dice «Soy Red Human, el agente virtual de Grupak.»).
  2. Proceso «Operativo básico» (SIN Prefiltro web ni Prefiltro por WhatsApp): Solicitud web sin CV → Entrevista Red
     Human por WhatsApp (enfoque Operativo) → Entrevista humana (la agenda RH) → Contrato y firma (Contratación) →
     Confirmación de ingreso (Onboarding).
  3. VAC-1156 (Ayudante general · Monterrey): toma ese proceso; «Unas preguntas» de la solicitud (Cambio 50) = ¿Puedes
     rolar turnos? y ¿Puedes cargar material pesado? (NO descartan) + edad + colonia y municipio (no descartan) + interés y
     sueldo. La «Secundaria terminada» pasa a deseable (ya no filtra). Entrevista por WhatsApp = base operativa SIN preguntas
     agregadas; arranca sola por WhatsApp al recibir la solicitud y cierra EXACTO: «Con esto terminamos, [nombre]. Gracias
     por tu tiempo. El equipo de Recursos Humanos de Grupak te contactará para agendar una entrevista.» (sin direcciones,
     sin repetir sueldo ni puesto: ya se dijeron en la solicitud). Si la vacante pedía documentos se vacían: esta ruta no
     lleva la actividad «Documentos».
     Si la VAC-1156 no existe en ESTA base (p. ej. la base local), se crea «Ayudante general» en Monterrey; recibe el
     siguiente código libre (el código lo da el id) y el script lo reporta.

Seguridad:
  * Sin `--aplicar` es un SIMULACRO: una transacción que al final se deshace. Con `--aplicar`, todo o nada.
  * Idempotente: Cuenta por código de vacante/slug/nombre, Cliente y proceso por nombre, vacante por código o título.
  * Cero comunicaciones: no crea candidatos ni manda mensajes; no toca postulaciones existentes (conservan su copia).

Uso (desde red-human-api/, con el .env del ambiente):
    python scripts/configurar_grupak.py              # simulacro
    python scripts/configurar_grupak.py --aplicar    # aplica
Opciones: --codigo VAC-#### (por defecto VAC-1156)
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
from sqlalchemy.orm.attributes import flag_modified  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Cliente, Cuenta, PlantillaProceso, Usuario, UsuarioCuenta, Vacante, registrar  # noqa: E402
from app.routers import vacantes as rvac  # noqa: E402
from app.seed import slug_cuenta_unico  # noqa: E402
from app.services import guiones as sgui  # noqa: E402
from app.services import ia  # noqa: E402
from app.services import preguntas_filtro as pfu  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import sincronizacion_vacante as ssv  # noqa: E402
from app.services import solicitud_web as ssw  # noqa: E402

ACTOR = "script:configurar_grupak"
NOMBRE = "Grupak"
SLUGS = ("demo-grupak", "grupak")
CODIGO = "VAC-1156"
NOMBRE_PROCESO = "Operativo básico"
PASOS = [
    {"id": "solicitud", "tipo": "solicitud_sin_cv", "etapa": "Prefiltro"},
    {"id": "entrevista-whatsapp", "tipo": "entrevista_whatsapp", "nombre": "Entrevista Red Human por WhatsApp", "etapa": "Entrevista IA",
     "enfoque": "operativo", "tipo_entrevista": "operativo"},
    {"id": "entrevista-humana", "tipo": "entrevista_humana", "nombre": "Entrevista humana", "etapa": "Entrevista Humana",
     "tipo_entrevista": "general", "tipo_agenda": "rh"},
    {"id": "contrato", "tipo": "carta_contrato", "nombre": "Contrato y firma", "etapa": "Contratación"},
    {"id": "alta", "tipo": "alta", "nombre": "Confirmación de ingreso", "etapa": "Onboarding"},
]
TIPOS_ESPERADOS = ["solicitud_sin_cv", "entrevista_whatsapp", "entrevista_humana", "carta_contrato", "alta"]

# «Unas preguntas» (Cambio 50): los dos filtros NO descartan; edad y colonia se agregan (tampoco descartan)
TURNOS = "Disponibilidad para rolar turnos"
PESADO = "Disponibilidad para cargar material pesado"
FILTROS = {TURNOS: "¿Puedes rolar turnos?", PESADO: "¿Puedes cargar material pesado?"}
EXTRAS_WEB = ["edad", "zona"]
DESEABLE = "Secundaria terminada"
CIERRE = ("Con esto terminamos, {nombre}. Gracias por tu tiempo. El equipo de Recursos Humanos de Grupak te contactará para "
          "agendar una entrevista.")

# Solo si la VAC-1156 no existe en esta base: «Ayudante general» en Monterrey (datos de la demo Grupak)
AYUDANTE = {
    "titulo": "Ayudante general", "area": "Operaciones", "seniority": "Sin experiencia",
    "estado": "Nuevo León", "municipio": "Monterrey", "sueldo": (9500, 11000),
    "descripcion": "Apoyo en carga, descarga, acomodo de materiales y limpieza del área de trabajo en planta.",
    "responsabilidades": ["Cargar y descargar material", "Acomodar materiales en el área asignada", "Mantener limpia el área de trabajo"],
    "beneficios": ["Prestaciones de ley", "Comedor", "Transporte de personal"],
}


class Abortar(Exception):
    pass


def ok(t: str) -> None:
    print(f"  ✓ {t}")


def nota(t: str) -> None:
    print(f"  · {t}")


# ============================================================ pasos

def buscar_vacante(db, codigo: str):
    return db.query(Vacante).filter(Vacante.codigo == codigo, Vacante.estado != "Eliminada").first()


def asegurar_cuenta(db, v) -> Cuenta:
    if v is not None:
        cu = db.get(Cuenta, v.cuenta_id)
        nota(f"Cuenta de la {v.codigo}: {cu.nombre_visible} (id {cu.id}, slug {cu.slug})")
        return cu
    cu = (db.query(Cuenta).filter(Cuenta.estado != "Eliminada")
          .filter(Cuenta.slug.in_(SLUGS) | func.lower(Cuenta.nombre).in_(["demo grupak", "grupak"]))
          .order_by(Cuenta.id).first())
    if cu is not None:
        nota(f"Cuenta existente: {cu.nombre_visible} (id {cu.id}, slug {cu.slug})")
        return cu
    cu = Cuenta(nombre=NOMBRE, nombre_comercial=NOMBRE, razon_social=NOMBRE, contacto_nombre="RH Grupak",
                canal_mensajeria="whatsapp", estado="Activa")
    db.add(cu)
    db.flush()
    cu.slug = slug_cuenta_unico(db, cu.nombre_comercial, cu.id)
    registrar(db, ACTOR, "cuenta_creada", "cuenta", str(cu.id), {"nombre": NOMBRE})
    with db.begin_nested():
        sproc.asegurar_rutas_base(db, cu.id, ACTOR)
    ok(f"Cuenta creada: {NOMBRE} (id {cu.id}, slug {cu.slug})")
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
        registrar(db, ACTOR, "cliente_creado", "cliente", str(cl.id), {"nombre": NOMBRE})
        ok(f"Cliente creado: {NOMBRE} (id {cl.id})")
    else:
        if cl.estado != "Activo":
            cl.estado = "Activo"
        nota(f"Cliente existente: {NOMBRE} (id {cl.id})")
    return cl


def asegurar_proceso(db, cu: Cuenta, u: Usuario) -> PlantillaProceso:
    try:
        pasos = sproc.normalizar_pasos([dict(x) for x in PASOS], previos=[])
    except sproc.ErrorProceso as e:
        raise Abortar(f"Proceso «{NOMBRE_PROCESO}»: {e.mensaje}")
    pl = db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cu.id, PlantillaProceso.nombre == NOMBRE_PROCESO).first()
    if pl is None:
        pl = PlantillaProceso(cuenta_id=cu.id, nombre=NOMBRE_PROCESO, descripcion="Operativos: solicitud web, entrevista por WhatsApp, "
                              "entrevista humana, contrato e ingreso.", pasos=pasos, etapas=sproc.normalizar_etapas({}),
                              predeterminada=False, version=1, creado_por=u.nombre, actualizada_por=u.nombre)
        db.add(pl)
        db.flush()
        registrar(db, ACTOR, "plantilla_proceso_creada", "plantilla_proceso", str(pl.id), {"nombre": NOMBRE_PROCESO})
        ok(f"Proceso «{NOMBRE_PROCESO}»: {' → '.join(x['nombre'] for x in pasos)}")
        return pl
    pl.activa = True
    if [sproc.clave_catalogo(x) for x in pl.pasos or []] != TIPOS_ESPERADOS:
        pl.pasos = pasos
        pl.version = int(pl.version or 1) + 1
        pl.actualizada_por = u.nombre
        registrar(db, ACTOR, "plantilla_proceso_actualizada", "plantilla_proceso", str(pl.id), {"version": pl.version})
        ok(f"Proceso «{NOMBRE_PROCESO}» actualizado a v{pl.version}: {' → '.join(x['nombre'] for x in pasos)}")
    else:
        nota(f"Proceso existente: «{NOMBRE_PROCESO}» (v{pl.version})")
    return pl


def crear_ayudante(db, cu: Cuenta, cl: Cliente, u: Usuario, pl: PlantillaProceso) -> Vacante:
    v = (db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == AYUDANTE["titulo"], Vacante.estado != "Eliminada")
         .order_by(Vacante.id).first())
    if v is not None:
        nota(f"Vacante existente (la {CODIGO} no está en esta base): {v.codigo} {v.titulo}")
        return v
    d = AYUDANTE
    datos = rvac.CrearIn(
        titulo=d["titulo"], area=d["area"], seniority=d["seniority"], ubicacion_estado=d["estado"], ubicacion_municipio=d["municipio"],
        modalidad="Presencial", sueldo_desde=d["sueldo"][0], sueldo_hasta=d["sueldo"][1], sueldo_moneda="MXN",
        sueldo_periodicidad="mensual", descripcion=d["descripcion"], resumen=d["descripcion"],
        requisitos_indispensables=list(FILTROS), requisitos_deseables=[DESEABLE], responsabilidades=d["responsabilidades"],
        beneficios=d["beneficios"], cliente_id=cl.id, mostrar_cliente_candidato=True, proceso={"plantilla_id": pl.id},
        preguntas_requisitos=[{"requisito": r, "pregunta": q} for r, q in FILTROS.items()],
        generar_si_falta=False, publicar=True, plataformas=["Portal", "WhatsApp"], enfoque_entrevista="operativo",
    )
    try:
        asyncio.run(rvac.crear(datos, db, u, cu))  # Cambio 36: el alta es asíncrona (publica en sus canales)
    except HTTPException as e:
        raise Abortar(f"Vacante «{d['titulo']}»: {e.detail}")
    v = db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == d["titulo"]).order_by(Vacante.id.desc()).first()
    ok(f"Vacante creada (la {CODIGO} no está en esta base): {v.codigo} {v.titulo} · {v.ubicacion} · {v.sueldo}")
    return v


def configurar_preguntas(v: Vacante) -> None:
    """«Unas preguntas»: rolar turnos y cargar material pesado (NO descartan) + edad y colonia; «Secundaria terminada»
    pasa a deseable (ya no filtra)."""
    actuales = rvac.requisitos_lista(v.requisitos or "")
    if actuales != list(FILTROS):
        v.requisitos = " · ".join(FILTROS)
        if DESEABLE in actuales or any("secundaria" in x.lower() for x in actuales):
            v.requisitos_deseables = list(dict.fromkeys(list(v.requisitos_deseables or []) + [DESEABLE]))
        nota(f"{v.codigo}: requisitos {actuales} → {list(FILTROS)} (la secundaria queda como deseable)")
    lista = [dict(q) for q in pfu.de_vacante(v)]
    previas = []
    for req, pregunta in FILTROS.items():
        q = next((x for x in lista if x.get("requisito") == req), {"requisito": req, "indispensable": True})
        previas.append({**q, "pregunta": pregunta, "tipo": "si_no", "descarta": False})
    pfu.sincronizar(v, entrada=previas)
    g = dict(v.guiones or {})
    g[ssw.CLAVE_EXTRA] = list(EXTRAS_WEB)
    v.guiones = g
    flag_modified(v, "guiones")


def configurar_vacante(db, v: Vacante, cl: Cliente, u: Usuario, pl: PlantillaProceso) -> None:
    """Proceso, cliente visible, enfoque operativo, entrevista base y «Unas preguntas» (lo mismo que hace RH en el formulario)."""
    try:
        nuevo = sproc.proceso_para_vacante(db, v.cuenta_id, v.proceso or {}, {"plantilla_id": pl.id})
    except sproc.ErrorProceso as e:
        raise Abortar(f"{v.codigo}: {e.mensaje}")
    if [sproc.clave_catalogo(x) for x in (nuevo.get("pasos") or [])] != TIPOS_ESPERADOS:
        nuevo = sproc.proceso_desde_plantilla(pl)
        nuevo["version"] = int((v.proceso or {}).get("version") or 0) + 1
    if nuevo != (v.proceso or {}):
        v.proceso = nuevo
        ok(f"{v.codigo}: proceso «{NOMBRE_PROCESO}» (v{nuevo.get('version')})")
    if v.cliente_id != cl.id or not v.mostrar_cliente_candidato:
        v.cliente_id, v.mostrar_cliente_candidato = cl.id, True
        ok(f"{v.codigo}: Cliente «{NOMBRE}» visible al candidato")
    if v.documentos_solicitados:
        nota(f"{v.codigo}: se quitan los documentos que pedía ({', '.join(x.get('nombre', '') for x in v.documentos_solicitados)}): "
             "la ruta «Operativo básico» no lleva la actividad «Documentos»")
        v.documentos_solicitados = []
    antes = v.enfoque_entrevista
    rvac._enfoque_por_ruta(db, v)  # el «Enfoque» de la actividad (Operativo)
    if v.enfoque_entrevista != "operativo":
        raise Abortar(f"{v.codigo}: la ruta no quedó como operativa (enfoque «{v.enfoque_entrevista}»)")
    if antes != "operativo":
        ok(f"{v.codigo}: entrevista «{antes or 'sin enfoque'}» → «operativo»")
    configurar_preguntas(v)
    g = dict(v.guiones or {})
    g["entrevista_whatsapp_base"] = {"quitadas": [], "propias": []}  # base operativa, sin preguntas agregadas
    v.guiones = g
    flag_modified(v, "guiones")
    db.flush()
    sgui.generar_para_vacante(db, v, u.nombre, sobrescribir_editadas=True, solo=["entrevista_whatsapp"])
    ok(f"{v.codigo}: «Unas preguntas» (rolar turnos, cargar material pesado, edad, colonia, interés, sueldo) y entrevista base operativa")
    registrar(db, ACTOR, "vacante_configurada", "vacante", v.codigo, {"proceso": NOMBRE_PROCESO})
    db.flush()


def validar(db, v: Vacante) -> list:
    from app.models import Postulacion
    from app.serial import nombre_empresa_candidato, vacante_dict
    from app.services import entrevista_whatsapp_fija as ewf

    problemas = []
    pasos = sgui.pasos_de_vacante(db, v)
    tipos = [sproc.clave_catalogo(x) for x in pasos]
    if tipos != TIPOS_ESPERADOS:
        problemas.append(f"ruta {tipos} ≠ {TIPOS_ESPERADOS}")
    if any(x.get("tipo") in ("prefiltro_web", "prefiltro_whatsapp") for x in pasos):
        problemas.append("la ruta trae un prefiltro (Operativo básico no lleva Prefiltro web ni por WhatsApp)")
    if v.enfoque_entrevista != "operativo" or sproc.enfoques_de_ruta(v.proceso).get("entrevista_whatsapp") != "operativo":
        problemas.append("la entrevista por WhatsApp no es Operativa")
    base = ewf.vista(v)
    if not base["esBase"]:
        problemas.append(f"la entrevista por WhatsApp no es la base: {base['quitadas']} / {base['propias']}")
    empresa = nombre_empresa_candidato(v)
    if empresa != NOMBRE or ia.presentacion_bot(empresa) != "Soy Red Human, el agente virtual de Grupak.":
        problemas.append(f"presentación: «{ia.presentacion_bot(empresa)}»")
    criterios = vacante_dict(v).get("criterios") or []
    textos = [c["pregunta"] for c in criterios]
    for q in FILTROS.values():
        c = next((x for x in criterios if x["pregunta"] == q), None)
        if c is None or c.get("descarta"):
            problemas.append(f"«Unas preguntas»: «{q}» debe estar y NO descartar → {textos}")
    for q in (ssw.PREGUNTA_EDAD, ssw.PREGUNTA_ZONA):  # 2026-10-16: sin «¿Te interesa?»
        if q not in textos:
            problemas.append(f"«Unas preguntas» sin «{q}» → {textos}")
    # 2026-10-16 (bug del tester): la vacante publica su sueldo → no se pide la expectativa
    if ssw.pregunta_expectativa(v) in textos:
        problemas.append(f"«Unas preguntas» pide la expectativa aunque el sueldo está publicado → {textos}")
    if any(c.get("descarta") for c in criterios):
        problemas.append("ninguna pregunta de Grupak descarta")
    sim = Postulacion(proceso=dict(v.proceso or {}), analisis={"prefiltro_datos": {"interes_puesto": True, "sueldo_acepta": True}})
    sim.vacante = v
    try:
        secuencia = ewf._secuencia_d(sim, [])
    finally:
        if sim in db:
            db.expunge(sim)  # simulación: nunca se guarda
        sim.vacante = None
    # 2026-10-16 (QA): el cierre es UNA confirmación («Antes de terminar… ¿Sigues interesado?») y después la fecha de inicio
    if secuencia != ["conf_final", "inicio"]:
        problemas.append(f"el cierre no es la confirmación única + inicio: {secuencia}")
    faltan = ssv.faltantes_publicacion(db, v)
    if faltan:
        nota(f"Para publicar falta: {' '.join(faltan)}")
    nota(f"Ruta: {' → '.join(x['nombre'] for x in pasos)}")
    nota(f"Unas preguntas: {' | '.join(textos)}")
    nota(f"Entrevista por WhatsApp: {' | '.join(b['texto'] for b in base['base'])}")
    return problemas


def configurar(db, codigo: str = CODIGO) -> Vacante:
    v = buscar_vacante(db, codigo)
    cu = asegurar_cuenta(db, v)
    u = vincular_admins(db, cu)
    cl = asegurar_cliente(db, cu)
    pl = asegurar_proceso(db, cu, u)
    if v is None:
        v = crear_ayudante(db, cu, cl, u, pl)
    else:
        nota(f"Vacante: {v.codigo} {v.titulo} · {v.ubicacion} ({v.estado})")
    configurar_vacante(db, v, cl, u, pl)
    return v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aplicar", action="store_true", help="guarda los cambios (sin esto es un simulacro)")
    ap.add_argument("--codigo", default=CODIGO, help=f"código de la vacante (por defecto {CODIGO})")
    a = ap.parse_args()

    db = SessionLocal()
    commit_real = db.commit
    db.commit = db.flush  # una sola transacción: los commits del endpoint solo hacen flush
    try:
        print(f"\n== Grupak · {a.codigo} ({'APLICAR' if a.aplicar else 'SIMULACRO'}) ==")
        v = configurar(db, a.codigo)
        print("\n-- Validación --")
        problemas = validar(db, v)
        if problemas:
            raise Abortar("Validación fallida:\n    " + "\n    ".join(problemas))
        ok("Validación: ruta Operativo básico, «Unas preguntas», entrevista base operativa y presentación correctas")
        if a.aplicar:
            commit_real()
            print(f"\nListo: cambios guardados ({v.codigo}).")
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
