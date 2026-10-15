"""Demo «Agencia del Centro» (2026-10-15) — Cuenta, 4 procesos y 5 vacantes en Ciudad de México.

Solo DATOS, con las MISMAS funciones de los endpoints (`normalizar_pasos`, `routers.vacantes.crear`, `preguntas_filtro`,
`guiones`, `ubicaciones`), así cada registro queda idéntico a uno capturado en pantalla:
  1. Cuenta: la que hoy se llama «Agencia de talento» pasa a «Agencia del Centro» (nombre interno y comercial; la razón
     social solo si era el nombre viejo; el slug NO cambia para no romper ligas publicadas). El bot se presenta con el
     nombre comercial: «Soy Red Human, el agente virtual de Agencia del Centro.» (`ia.presentacion_bot`). Si no existe
     ninguna de las dos, se crea «Agencia del Centro».
  2. Procesos: se DESACTIVAN (nunca se borran) «Operativos», «Masivos con documentos (iniciales)» y «Masivos sin
     documentos (iniciales)»; se crean / actualizan:
       P1 «Masivo Documentos y pruebas»: Solicitud web sin CV → Documentos | Evaluación técnica o práctica (la agenda el
          candidato) → Evaluación médica (la agenda RH) | Contrato y firma.
       P2 «Operativo WhatsApp»: Solicitud web sin CV → Documentos | Entrevista Red Human por WhatsApp (Operativo) |
          Entrevista humana | Contrato y firma.  ← predeterminada de la Cuenta
       P3 «Operativo Avatar»: Solicitud web sin CV → Documentos | Entrevista con avatar (Operativo) → Psicometría digital
          (Informativa: Cleaver) | Entrevista humana | Contrato y firma.
       P4 «Administrativo Avatar»: Solicitud web con CV | Entrevista con avatar (Profesional + personal) → Psicometría
          digital (Con mínimos por escala: Cleaver + Terman + Moss, CI ≥ 100) | Entrevista humana | Documentos → Oferta
          laboral → Contrato y firma.
     Pruebas del catálogo (Configuración → Pruebas psicométricas): Cleaver (1), Terman (7) y Moss (15), integradas.
  3. Ubicación «Ciudad de México» (lunes a viernes 9:00 y 12:00, 8 lugares) para la autoagenda de la prueba práctica
     (solo la vacante de P1 la usa). Dirección de demo: RH la corrige en Configuración → Ubicaciones.
  4. Vacantes (Ciudad de México, presencial, mensual MXN), cada una con su proceso, «Unas preguntas» Sí/No que
     descartan con «No», y sus documentos en la etapa correcta:
       Ayudante general (P1) · Almacenista (P2) · Chofer con auto propio (P3) — documentos en la solicitud (Prefiltro).
       Gerente de Recursos Humanos y Gerente de Ventas (P4) — documentos «Se pide en: Contratación».
     Guiones de entrevista generados con la ruta (Operativo = base fija; Profesional + personal = guion propio por vacante).

Seguridad: sin `--aplicar` es un SIMULACRO (una transacción que al final se deshace); con `--aplicar` también es una sola
transacción: o queda todo o nada. Idempotente (Cuenta por nombre/slug, procesos por nombre, vacantes por título).
Cero comunicaciones: no crea candidatos ni manda mensajes; las postulaciones existentes conservan su copia de la ruta.

Uso (desde red-human-api/, con el .env del ambiente; la API debe haber arrancado una vez con esta versión):
    python scripts/seed_agencia_centro.py              # simulacro
    python scripts/seed_agencia_centro.py --aplicar    # aplica
Opciones: --borrador (vacantes en Borrador)
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
    Cuenta, PlantillaProceso, Postulacion, PruebaPsicometrica, Ubicacion, Usuario, UsuarioCuenta, Vacante,
    normalizar_documentos_vacante, registrar, texto_sueldo,
)
from app.routers import vacantes as rvac  # noqa: E402
from app.seed import slug_cuenta_unico  # noqa: E402
from app.services import guiones as sgui  # noqa: E402
from app.services import preguntas_filtro as pfu  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import ubicaciones as subic  # noqa: E402

ACTOR = "script:seed_agencia_centro"
NOMBRE_ANTERIOR = "Agencia de talento"
NOMBRE = "Agencia del Centro"
PRESENTACION = "Soy Red Human, el agente virtual de Agencia del Centro."

# ============================================================ 1. Procesos
P1, P2, P3, P4 = "Masivo Documentos y pruebas", "Operativo WhatsApp", "Operativo Avatar", "Administrativo Avatar"
PREDETERMINADO = P2  # el más parecido a «Masivos sin documentos», que era la predeterminada
# Procesos viejos que se desactivan (por nombre, sin acentos ni mayúsculas, o por su ruta base)
ANTERIORES = ("operativos", "masivos con documentos", "masivos sin documentos")
RUTAS_BASE_ANTERIORES = ("masivos_con_documentos", "masivos_sin_documentos")

# Pruebas psicométricas del catálogo: (nombre, id en el proveedor)
PRUEBAS = {"cleaver": ("Cleaver", "1"), "terman": ("Terman", "7"), "moss": ("Moss", "15")}
BATERIA_OPERATIVA = ["cleaver"]
BATERIA_ADMINISTRATIVA = ["cleaver", "terman", "moss"]
MINIMOS_ADMINISTRATIVO = {"CI": 100}  # Terman: coeficiente intelectual promedio o superior (RH lo ajusta en el editor)

SOLICITUD_SIN_CV = {"id": "solicitud", "tipo": "solicitud_sin_cv", "etapa": "Prefiltro"}
DOCUMENTOS_PREFILTRO = {"id": "documentos", "tipo": "documentos", "nombre": "Documentos", "etapa": "Prefiltro"}
ENTREVISTA_HUMANA = {"id": "entrevista-humana", "tipo": "entrevista_humana", "nombre": "Entrevista humana",
                     "etapa": "Entrevista Humana", "tipo_entrevista": "general", "tipo_agenda": "rh"}
CONTRATO = {"id": "contrato", "tipo": "carta_contrato", "nombre": "Contrato y firma", "etapa": "Contratación"}


def _avatar(enfoque: str) -> dict:
    return {"id": "entrevista-avatar", "tipo": "entrevista_agente", "nombre": "Entrevista Red Human con avatar",
            "etapa": "Entrevista IA", "enfoque": enfoque, "tipo_entrevista": enfoque}


def _psicometria(pruebas: list, evaluacion: dict) -> dict:
    return {"id": "psicometria", "tipo": "psicometrica", "nombre": "Psicometría digital", "etapa": "Entrevista IA",
            "pruebas": pruebas, "evaluacion_psicometria": evaluacion, "depende_de": ["entrevista-avatar"]}


def pasos_procesos(evaluador_id: int, pruebas: dict) -> dict:
    """{nombre: pasos crudos}. `pruebas` = {clave: id de PruebaPsicometrica}. El evaluador de la prueba práctica es un
    usuario de la Cuenta (el Administrador de la demo; RH lo cambia en el editor): sin él, la autoagenda no podría
    crear la evaluación al reservar."""
    ev = {"evaluador": {"tipo": "interno", "usuario_id": evaluador_id}}
    return {
        P1: [SOLICITUD_SIN_CV, DOCUMENTOS_PREFILTRO,
             {"id": "prueba-practica", "tipo": "tecnica", "nombre": "Evaluación técnica o práctica", "etapa": "Entrevista Humana",
              "tipo_agenda": "candidato", "config": ev},
             {"id": "medica", "tipo": "medica", "nombre": "Evaluación médica", "etapa": "Entrevista Humana", "tipo_agenda": "rh",
              "depende_de": ["prueba-practica"]},
             CONTRATO],
        P2: [SOLICITUD_SIN_CV, DOCUMENTOS_PREFILTRO,
             {"id": "entrevista-whatsapp", "tipo": "entrevista_whatsapp", "nombre": "Entrevista Red Human por WhatsApp",
              "etapa": "Entrevista IA", "enfoque": "operativo", "tipo_entrevista": "operativo"},
             ENTREVISTA_HUMANA, CONTRATO],
        P3: [SOLICITUD_SIN_CV, DOCUMENTOS_PREFILTRO, _avatar("operativo"),
             _psicometria([pruebas[k] for k in BATERIA_OPERATIVA], {"modalidad": "informativa"}),
             ENTREVISTA_HUMANA, CONTRATO],
        P4: [{"id": "solicitud", "tipo": "solicitud_con_cv", "etapa": "Prefiltro"}, _avatar("profesional_personal"),
             _psicometria([pruebas[k] for k in BATERIA_ADMINISTRATIVA], {"modalidad": "minimos", "minimos": MINIMOS_ADMINISTRATIVO}),
             ENTREVISTA_HUMANA,
             {"id": "documentos", "tipo": "documentos", "nombre": "Documentos", "etapa": "Contratación"},
             {"id": "oferta", "tipo": "oferta", "nombre": "Oferta laboral", "etapa": "Contratación"},
             CONTRATO],
    }


DESCRIPCIONES = {
    P1: "Masivos: solicitud con documentos, prueba práctica que agenda el candidato y examen médico.",
    P2: "Operativos: solicitud con documentos, entrevista por WhatsApp y entrevista humana.",
    P3: "Operativos: solicitud con documentos, entrevista en video, psicometría informativa y entrevista humana.",
    P4: "Administrativos: solicitud con CV, entrevista en video, psicometría con mínimos, entrevista humana y documentos al contratar.",
}
TIPOS_ESPERADOS = {
    P1: ["solicitud_sin_cv", "documentos", "tecnica", "medica", "carta_contrato"],
    P2: ["solicitud_sin_cv", "documentos", "entrevista_whatsapp", "entrevista_humana", "carta_contrato"],
    P3: ["solicitud_sin_cv", "documentos", "entrevista_agente", "psicometrica", "entrevista_humana", "carta_contrato"],
    P4: ["solicitud_con_cv", "entrevista_agente", "psicometrica", "entrevista_humana", "documentos", "oferta", "carta_contrato"],
}

# ============================================================ 2. Ubicación (autoagenda de la prueba práctica)
UBICACION = {"nombre": "Ciudad de México", "estado": "Ciudad de México", "municipio": "",
             "direccion": "Ciudad de México (dirección de demo)",
             "indicaciones": "Trae tu INE original y ropa cómoda para la prueba práctica.",
             "horarios": [{"dias": [0, 1, 2, 3, 4], "horas": ["09:00", "12:00"], "cupo": 8}]}

# ============================================================ 3. Vacantes
DOCS_BASICOS = [{"nombre": "INE (frente y reverso)"}, {"nombre": "CURP"}, {"nombre": "Comprobante de domicilio"},
                {"nombre": "Número de Seguridad Social (NSS)"}]
DOCS_CHOFER = [{"nombre": "Licencia de conducir"},
               {"nombre": "Foto del auto de frente", "cantidad": 1, "foto": True},
               {"nombre": "Foto del auto costado izquierdo", "cantidad": 1, "foto": True},
               {"nombre": "Foto del auto costado derecho", "cantidad": 1, "foto": True}]
DOCS_GERENTE = [dict(d, etapa="Contratación") for d in (
    {"nombre": "INE (frente y reverso)"}, {"nombre": "CURP"}, {"nombre": "Constancia de Situación Fiscal (RFC)"},
    {"nombre": "Número de Seguridad Social (NSS)"}, {"nombre": "Comprobante de domicilio"}, {"nombre": "Comprobante de estudios"})]

VACANTES = [
    {"titulo": "Ayudante general", "proceso": P1, "area": "Operaciones", "seniority": "Sin experiencia", "sueldo": (9500, 11000),
     "filtros": {"Disponibilidad para rolar turnos": "¿Tienes disponibilidad para rolar turnos?",
                 "Disponibilidad para cargar material pesado": "¿Puedes cargar material pesado?"},
     "descripcion": "Apoyo en carga, descarga y acomodo de materiales, y limpieza del área de trabajo.",
     "responsabilidades": ["Cargar y descargar material", "Acomodar materiales en el área asignada", "Mantener limpia el área de trabajo"],
     "beneficios": ["Prestaciones de ley"], "documentos": DOCS_BASICOS, "ubicacion": True},
    {"titulo": "Almacenista", "proceso": P2, "area": "Almacén", "seniority": "Junior", "sueldo": (11000, 13000),
     "filtros": {"Experiencia en almacén (recepción, acomodo o surtido)":
                 "¿Tienes experiencia en almacén (recepción, acomodo o surtido)?",
                 "Disponibilidad para trabajar de lunes a sábado": "¿Puedes trabajar de lunes a sábado?"},
     "descripcion": "Recepción, acomodo y surtido de mercancía en almacén; control de entradas y salidas.",
     "responsabilidades": ["Recibir y revisar mercancía", "Acomodar mercancía en su ubicación", "Surtir pedidos",
                           "Registrar entradas y salidas"],
     "beneficios": ["Prestaciones de ley"], "documentos": DOCS_BASICOS},
    {"titulo": "Chofer con auto propio", "proceso": P3, "area": "Logística", "seniority": "Junior", "sueldo": (15000, 18000),
     "filtros": {"Auto propio para trabajar": "¿Tienes auto propio para trabajar?",
                 "Licencia de manejo vigente": "¿Tienes licencia de manejo vigente?"},
     "descripcion": "Traslado de personal y paquetería en Ciudad de México con auto propio.",
     "responsabilidades": ["Trasladar personal y paquetería en las rutas asignadas", "Mantener el auto limpio y en buen estado",
                           "Cumplir con los horarios de entrega"],
     "beneficios": ["Prestaciones de ley"], "documentos": DOCS_CHOFER},
    {"titulo": "Gerente de Recursos Humanos", "proceso": P4, "area": "Recursos Humanos", "seniority": "Jefatura",
     "sueldo": (45000, 55000),
     "filtros": {"5 años de experiencia en Recursos Humanos, con 2 o más a cargo de un equipo":
                 "¿Tienes al menos 5 años de experiencia en Recursos Humanos, con 2 o más a cargo de un equipo?"},
     "descripcion": ("Dirigir el área de Recursos Humanos: reclutamiento, nómina, relaciones laborales, clima y desarrollo "
                     "del personal."),
     "responsabilidades": ["Dirigir los procesos de reclutamiento y selección", "Supervisar nómina y prestaciones",
                           "Atender relaciones laborales", "Liderar al equipo de Recursos Humanos"],
     "beneficios": ["Prestaciones de ley"], "documentos": DOCS_GERENTE},
    {"titulo": "Gerente de Ventas", "proceso": P4, "area": "Ventas", "seniority": "Jefatura", "sueldo": (50000, 60000),
     "filtros": {"5 años de experiencia en ventas, con 2 o más dirigiendo un equipo":
                 "¿Tienes al menos 5 años de experiencia en ventas, con 2 o más dirigiendo un equipo?"},
     "descripcion": "Dirigir al equipo comercial, definir metas de venta y dar seguimiento a clientes clave. Sueldo más comisiones.",
     "responsabilidades": ["Definir y dar seguimiento a las metas de venta", "Dirigir y capacitar al equipo de ventas",
                           "Atender clientes clave", "Reportar resultados a la dirección"],
     "beneficios": ["Prestaciones de ley", "Comisiones por ventas"], "documentos": DOCS_GERENTE},
]


class Abortar(Exception):
    pass


def ok(t: str) -> None:
    print(f"  ✓ {t}")


def nota(t: str) -> None:
    print(f"  · {t}")


def _plano(t: str) -> str:
    import unicodedata

    return " ".join(unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower().split())


# ============================================================ pasos

def asegurar_cuenta(db) -> Cuenta:
    nombres = [_plano(NOMBRE_ANTERIOR), _plano(NOMBRE)]
    candidatas = [c for c in db.query(Cuenta).filter(Cuenta.estado != "Eliminada").order_by(Cuenta.id).all()
                  if _plano(c.nombre) in nombres or _plano(c.nombre_comercial) in nombres]
    # primero la que se llama como antes (la que hay que renombrar); si ya se renombró, la nueva
    cu = next((c for c in candidatas if _plano(NOMBRE_ANTERIOR) in (_plano(c.nombre), _plano(c.nombre_comercial))), None) \
        or next(iter(candidatas), None)
    if cu is None:
        cu = Cuenta(nombre=NOMBRE, nombre_comercial=NOMBRE, razon_social=NOMBRE, contacto_nombre=f"RH {NOMBRE}",
                    canal_mensajeria="whatsapp", estado="Activa")
        db.add(cu)
        db.flush()
        cu.slug = slug_cuenta_unico(db, cu.nombre_comercial, cu.id)
        registrar(db, ACTOR, "cuenta_creada", "cuenta", str(cu.id), {"nombre": NOMBRE})
        ok(f"Cuenta creada (no existía «{NOMBRE_ANTERIOR}»): {NOMBRE} (id {cu.id}, slug {cu.slug})")
    else:
        antes = {"nombre": cu.nombre, "nombre_comercial": cu.nombre_comercial, "razon_social": cu.razon_social}
        cu.nombre, cu.nombre_comercial = NOMBRE, NOMBRE
        if _plano(cu.razon_social) in ("", _plano(NOMBRE_ANTERIOR)):
            cu.razon_social = NOMBRE  # una razón social real distinta (la del contrato) se respeta
        if cu.estado != "Activa":
            cu.estado = "Activa"
        despues = {"nombre": cu.nombre, "nombre_comercial": cu.nombre_comercial, "razon_social": cu.razon_social}
        if antes != despues:
            registrar(db, ACTOR, "cuenta_renombrada", "cuenta", str(cu.id), {"antes": antes, "despues": despues})
            ok(f"Cuenta {cu.id}: «{antes['nombre_comercial']}» → «{NOMBRE}» (slug {cu.slug} sin cambio)")
        else:
            nota(f"Cuenta existente: {NOMBRE} (id {cu.id}, slug {cu.slug})")
    with db.begin_nested():
        # las rutas base se siembran aquí (si faltan) para desactivarlas YA: si no, el arranque las crearía activas
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


def desactivar_anteriores(db, cu: Cuenta) -> None:
    nuevos = {P1, P2, P3, P4}
    for pl in db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cu.id).order_by(PlantillaProceso.id).all():
        if pl.nombre in nuevos:
            continue
        if not (any(_plano(pl.nombre).startswith(x) for x in ANTERIORES) or (pl.ruta_base or "") in RUTAS_BASE_ANTERIORES):
            continue
        if pl.activa or pl.predeterminada:
            pl.activa, pl.predeterminada = False, False
            registrar(db, ACTOR, "plantilla_proceso_desactivada", "plantilla_proceso", str(pl.id), {"nombre": pl.nombre})
            ok(f"Proceso «{pl.nombre}» desactivado (las vacantes y candidatos que lo usan conservan su copia)")
        else:
            nota(f"Proceso «{pl.nombre}» ya estaba desactivado")
    db.flush()


def asegurar_pruebas(db, cu: Cuenta, u: Usuario) -> dict:
    from app.routers.evaluaciones import _clave_automatica

    salida = {}
    for clave, (nombre, id_prov) in PRUEBAS.items():
        pr = (db.query(PruebaPsicometrica).filter(PruebaPsicometrica.cuenta_id == cu.id, PruebaPsicometrica.id_proveedor == id_prov)
              .order_by(PruebaPsicometrica.id).first())
        if pr is None:
            pr = PruebaPsicometrica(cuenta_id=cu.id, nombre=nombre, tipo="prueba", modo="integrada", proveedor="Psicométricas.mx",
                                    id_proveedor=id_prov, activa=True, creado_por=u.nombre, clave="")
            db.add(pr)
            db.flush()
            pr.clave = _clave_automatica(db, cu.id, nombre, pr.id)
            registrar(db, ACTOR, "prueba_psicometrica_creada", "prueba_psicometrica", str(pr.id), {"nombre": nombre})
            ok(f"Prueba psicométrica «{nombre}» (proveedor {id_prov})")
        else:
            pr.activa = True
            nota(f"Prueba existente: {pr.nombre} (id {pr.id})")
        salida[clave] = pr.id
    db.flush()
    return salida


def asegurar_proceso(db, cu: Cuenta, u: Usuario, nombre: str, crudos: list) -> PlantillaProceso:
    try:
        pasos = sproc.normalizar_pasos([dict(x) for x in crudos], previos=[])
    except sproc.ErrorProceso as e:
        raise Abortar(f"Proceso «{nombre}»: {e.mensaje}")
    pl = db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cu.id, PlantillaProceso.nombre == nombre).first()
    if pl is None:
        pl = PlantillaProceso(cuenta_id=cu.id, nombre=nombre, descripcion=DESCRIPCIONES[nombre], pasos=pasos,
                              etapas=sproc.normalizar_etapas({}), predeterminada=False, version=1, creado_por=u.nombre,
                              actualizada_por=u.nombre)
        db.add(pl)
        db.flush()
        registrar(db, ACTOR, "plantilla_proceso_creada", "plantilla_proceso", str(pl.id), {"nombre": nombre, "pasos": len(pasos)})
        ok(f"Proceso «{nombre}»: {' → '.join(x['nombre'] for x in pasos)}")
        return pl
    pl.activa = True
    if pl.pasos != pasos:
        pl.pasos = pasos
        pl.descripcion = DESCRIPCIONES[nombre]
        pl.version = int(pl.version or 1) + 1
        pl.actualizada_por = u.nombre
        registrar(db, ACTOR, "plantilla_proceso_actualizada", "plantilla_proceso", str(pl.id), {"version": pl.version})
        ok(f"Proceso «{nombre}» actualizado a v{pl.version}: {' → '.join(x['nombre'] for x in pasos)}")
    else:
        nota(f"Proceso existente: «{nombre}» (v{pl.version})")
    return pl


def fijar_predeterminada(db, cu: Cuenta, procesos: dict) -> None:
    activas = db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cu.id, PlantillaProceso.activa.is_(True),
                                                PlantillaProceso.predeterminada.is_(True)).all()
    if activas:
        nota(f"Predeterminada de la Cuenta: «{activas[0].nombre}»")
        return
    procesos[PREDETERMINADO].predeterminada = True
    registrar(db, ACTOR, "plantilla_proceso_predeterminada", "plantilla_proceso", str(procesos[PREDETERMINADO].id),
              {"nombre": PREDETERMINADO})
    ok(f"Predeterminada de la Cuenta: «{PREDETERMINADO}»")
    db.flush()


def asegurar_ubicacion(db, cu: Cuenta) -> Ubicacion:
    d = UBICACION
    u = db.query(Ubicacion).filter(Ubicacion.cuenta_id == cu.id, Ubicacion.nombre == d["nombre"]).first()
    if u is None:
        u = Ubicacion(cuenta_id=cu.id, nombre=d["nombre"], direccion=d["direccion"], indicaciones=d["indicaciones"],
                      ubicacion_estado=d["estado"], ubicacion_municipio=d["municipio"],
                      horarios_citas=subic.normalizar_horarios(d["horarios"]), activa=True)
        db.add(u)
        db.flush()
        registrar(db, ACTOR, "ubicacion_creada", "ubicacion", str(u.id), {"nombre": d["nombre"]})
        ok(f"Ubicación «{d['nombre']}»: {subic.texto_horarios(u.horarios_citas)} (dirección de demo)")
    else:
        u.activa = True
        if not u.horarios_citas:
            u.horarios_citas = subic.normalizar_horarios(d["horarios"])
            ok(f"Ubicación «{u.nombre}»: horarios de citas {subic.texto_horarios(u.horarios_citas)}")
        else:
            nota(f"Ubicación existente: {u.nombre} (id {u.id}; dirección y horarios se respetan)")
    db.flush()
    return u


def configurar_preguntas(v: Vacante, filtros: dict) -> None:
    """«Unas preguntas» de la solicitud: una Sí/No por indispensable; un «No» descarta al enviar (Cambio 50)."""
    reqs = list(filtros)
    if rvac.requisitos_lista(v.requisitos or "") != reqs:
        v.requisitos = rvac.SEPARADOR_REQUISITOS.join(reqs)
    lista = [dict(q) for q in pfu.de_vacante(v)]
    previas = []
    for req, pregunta in filtros.items():
        q = next((x for x in lista if x.get("requisito") == req and not x.get("propia")), {"requisito": req, "indispensable": True})
        previas.append({**q, "pregunta": pregunta, "pregunta_web": pregunta, "tipo": "si_no", "descarta": True,
                        "regla": "Descarta si responde No"})
    pfu.sincronizar(v, entrada=previas)


def _proceso_de(db, v: Vacante, pl: PlantillaProceso) -> None:
    """La vacante toma la ruta VIGENTE de su proceso (nueva versión si cambió). Las postulaciones conservan su copia."""
    actual = v.proceso or {}
    vigente = sproc.proceso_desde_plantilla(pl)
    if actual.get("plantilla_id") == pl.id and sproc._firma_pasos(actual.get("pasos")) == sproc._firma_pasos(vigente.get("pasos")) \
            and [x.get("nombre") for x in actual.get("pasos") or []] == [x.get("nombre") for x in vigente.get("pasos") or []] \
            and all(a.get(k) == b.get(k) for a, b in zip(actual.get("pasos") or [], vigente.get("pasos") or [])
                    for k in ("enfoque", "pruebas", "evaluacion_psicometria", "tipo_agenda", "con_cv")):
        return
    vigente["version"] = int(actual.get("version") or 0) + 1
    v.proceso = vigente
    n = db.query(Postulacion).filter(Postulacion.vacante_id == v.id).count()
    registrar(db, ACTOR, "vacante_proceso_actualizado", "vacante", v.codigo,
              {"proceso": pl.nombre, "version": vigente["version"], "postulaciones_con_copia": n})
    ok(f"{v.codigo}: ruta «{pl.nombre}» v{vigente['version']}" + (f" ({n} postulación(es) conservan su copia)" if n else ""))


def asegurar_vacante(db, cu: Cuenta, u: Usuario, pl: PlantillaProceso, ubic: Ubicacion, d: dict, publicar: bool) -> Vacante:
    desde, hasta = d["sueldo"]
    v = (db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == d["titulo"], Vacante.estado != "Eliminada")
         .order_by(Vacante.id).first())
    if v is None:
        datos = rvac.CrearIn(
            titulo=d["titulo"], area=d["area"], seniority=d["seniority"], ubicacion_estado="Ciudad de México", ubicacion_municipio="",
            modalidad="Presencial", sueldo_desde=desde, sueldo_hasta=hasta, sueldo_moneda="MXN", sueldo_periodicidad="mensual",
            descripcion=d["descripcion"], resumen=d["descripcion"], requisitos_indispensables=list(d["filtros"]),
            responsabilidades=d["responsabilidades"], beneficios=d["beneficios"], proceso={"plantilla_id": pl.id},
            documentos_solicitados=d["documentos"], generar_si_falta=False, publicar=False, plataformas=[],
            preguntas_requisitos=[{"requisito": r, "pregunta": q} for r, q in d["filtros"].items()],
            **({"ubicaciones_ids": [ubic.id]} if d.get("ubicacion") else {}),
        )
        try:
            asyncio.run(rvac.crear(datos, db, u, cu))
        except HTTPException as e:
            raise Abortar(f"Vacante «{d['titulo']}»: {e.detail}")
        v = db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == d["titulo"]).order_by(Vacante.id.desc()).first()
        ok(f"Vacante {v.codigo} {v.titulo} · {v.sueldo} · {pl.nombre}")
    else:
        nota(f"Vacante existente: {v.codigo} {v.titulo} ({v.estado})")
        v.area, v.seniority, v.modalidad = d["area"], d["seniority"], "Presencial"
        v.ubicacion_estado, v.ubicacion_municipio = "Ciudad de México", ""
        v.ubicacion = "Ciudad de México"
        v.sueldo_desde, v.sueldo_hasta, v.sueldo_moneda, v.sueldo_periodicidad = desde, hasta, "MXN", "mensual"
        v.sueldo = texto_sueldo(desde, hasta, "MXN", "mensual")
        v.cliente_id = None  # el candidato ve «Agencia del Centro»
        subic.asignar(db, v, [ubic.id] if d.get("ubicacion") else [])
        if d.get("ubicacion"):
            v.ubicacion = "Ciudad de México"
    _proceso_de(db, v, pl)
    docs = normalizar_documentos_vacante(d["documentos"])
    if v.documentos_solicitados != docs:
        v.documentos_solicitados = docs
        flag_modified(v, "documentos_solicitados")
    configurar_preguntas(v, d["filtros"])
    rvac._enfoque_por_ruta(db, v)
    db.flush()
    secciones = [c for c in ("entrevista_whatsapp", "entrevista_avatar") if c in sgui.secciones_aplicables(sgui.tipos_de_ruta(v.proceso["pasos"]))]
    if secciones:
        sgui.generar_para_vacante(db, v, u.nombre, sobrescribir_editadas=True, solo=secciones)
        nota(f"{v.codigo}: guion de la entrevista ({', '.join(secciones)}) con enfoque «{v.enfoque_entrevista}»")
    if publicar and v.estado != "Publicada":
        v.estado = "Publicada"
        v.publicada_en = v.publicada_en or datetime.now(timezone.utc)
        registrar(db, ACTOR, "vacante_publicada", "vacante", v.codigo, {"titulo": v.titulo})
        ok(f"{v.codigo} publicada")
    registrar(db, ACTOR, "vacante_configurada", "vacante", v.codigo, {"proceso": pl.nombre})
    db.flush()
    return v


# ============================================================ validación (mismas funciones que usa el bot)

def validar(db, cu: Cuenta, vacantes: dict) -> list:
    from app.serial import nombre_empresa_candidato, vacante_dict
    from app.services import documentos_vacante as sdv
    from app.services import ia
    from app.services import sincronizacion_vacante as ssv

    problemas = []
    activos = {pl.nombre for pl in db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cu.id, PlantillaProceso.activa.is_(True))}
    for viejo in activos:
        if any(_plano(viejo).startswith(x) for x in ANTERIORES):
            problemas.append(f"Proceso anterior sigue activo: «{viejo}»")
    for d in VACANTES:
        v = vacantes[d["titulo"]]
        t = d["titulo"]
        pasos = v.proceso.get("pasos") or []
        tipos = [sproc.clave_catalogo(x) for x in pasos]
        if tipos != TIPOS_ESPERADOS[d["proceso"]]:
            problemas.append(f"{t}: ruta {tipos} ≠ {TIPOS_ESPERADOS[d['proceso']]}")
        if ia.presentacion_bot(nombre_empresa_candidato(v)) != PRESENTACION:
            problemas.append(f"{t}: presentación «{ia.presentacion_bot(nombre_empresa_candidato(v))}»")
        criterios = vacante_dict(v).get("criterios") or []
        for q in d["filtros"].values():
            c = next((x for x in criterios if x["pregunta"] == q), None)
            if c is None or not c.get("descarta") or c.get("tipo") != "si_no":
                problemas.append(f"{t}: «Unas preguntas» sin «{q}» como Sí/No que descarta → {[x['pregunta'] for x in criterios]}")
        sim = Postulacion(proceso=dict(v.proceso), analisis={})
        tempranos = [x["nombre"] for x in sdv.de_vacante(v, pasos, tempranos=True)]
        finales = [x["nombre"] for x in sdv.de_vacante(v, pasos, tempranos=False)]
        if d["proceso"] == P4:
            if tempranos or len(finales) != 6:
                problemas.append(f"{t}: documentos al inicio {tempranos} / en Contratación {finales}")
            if not ssv.exige_cv(pasos):
                problemas.append(f"{t}: la solicitud no exige CV")
            if sdv.siguen_a_solicitud(sim):
                problemas.append(f"{t}: pide documentos en la solicitud")
        else:
            if len(tempranos) != 4 or finales:
                problemas.append(f"{t}: documentos en la solicitud {tempranos} / después {finales}")
            if not sdv.siguen_a_solicitud(sim):
                problemas.append(f"{t}: los documentos no se piden en la misma página de la solicitud")
        enfoques = sproc.enfoques_de_ruta(v.proceso)
        esperado = {P2: {"entrevista_whatsapp": "operativo"}, P3: {"entrevista_agente": "operativo"},
                    P4: {"entrevista_agente": "profesional_personal"}}.get(d["proceso"], {})
        if enfoques != esperado:
            problemas.append(f"{t}: enfoques {enfoques} ≠ {esperado}")
        if d["proceso"] == P1:
            practica = next((x for x in pasos if x["tipo"] == "tecnica"), {})
            if practica.get("tipo_agenda") != "candidato" or len(subic.de_vacante(db, v)) != 1:
                problemas.append(f"{t}: la prueba práctica no la agenda el candidato en la ubicación de la vacante")
            if any(x["tipo"] in sproc.TIPOS_PASO_ENTREVISTA_IA or x["tipo"] == "entrevista_humana" for x in pasos):
                problemas.append(f"{t}: la ruta trae entrevistas")
        psi = next((x for x in pasos if x["tipo"] == "psicometrica"), None)
        if d["proceso"] == P3 and (psi or {}).get("evaluacion_psicometria", {}).get("modalidad") != "informativa":
            problemas.append(f"{t}: la psicometría no es Informativa")
        if d["proceso"] == P4 and (psi or {}).get("evaluacion_psicometria", {}).get("minimos") != MINIMOS_ADMINISTRATIVO:
            problemas.append(f"{t}: la psicometría no tiene los mínimos {MINIMOS_ADMINISTRATIVO}")
        faltan = ssv.faltantes_publicacion(db, v)
        if faltan:
            problemas.append(f"{t}: para publicar falta {faltan}")
        nota(f"{v.codigo} {t}: {' → '.join(x['nombre'] for x in pasos)}")
    return problemas


def configurar(db, publicar: bool = True) -> dict:
    cu = asegurar_cuenta(db)
    u = vincular_admins(db, cu)
    print("\n-- Procesos de selección --")
    desactivar_anteriores(db, cu)
    pruebas = asegurar_pruebas(db, cu, u)
    procesos = {n: asegurar_proceso(db, cu, u, n, crudos) for n, crudos in pasos_procesos(u.id, pruebas).items()}
    fijar_predeterminada(db, cu, procesos)
    print("\n-- Ubicación --")
    ubic = asegurar_ubicacion(db, cu)
    vacantes = {}
    for d in VACANTES:
        print(f"\n-- {d['titulo']} --")
        vacantes[d["titulo"]] = asegurar_vacante(db, cu, u, procesos[d["proceso"]], ubic, d, publicar)
    return {"cuenta": cu, "procesos": procesos, "vacantes": vacantes, "ubicacion": ubic}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aplicar", action="store_true", help="guarda los cambios (sin esto es un simulacro)")
    ap.add_argument("--borrador", action="store_true", help="deja las vacantes en Borrador")
    a = ap.parse_args()

    db = SessionLocal()
    commit_real = db.commit
    db.commit = db.flush  # una sola transacción: los commits del endpoint solo hacen flush
    try:
        print(f"\n== {NOMBRE} ({'APLICAR' if a.aplicar else 'SIMULACRO'}) ==")
        r = configurar(db, publicar=not a.borrador)
        print("\n-- Validación --")
        problemas = validar(db, r["cuenta"], r["vacantes"])
        if problemas:
            raise Abortar("Validación fallida:\n    " + "\n    ".join(problemas))
        ok("Validación: Cuenta, 4 procesos, 5 vacantes, «Unas preguntas», documentos por etapa y enfoques correctos")
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
