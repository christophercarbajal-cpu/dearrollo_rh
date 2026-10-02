"""Semilla de datos de demostración (misma información que el frontend usa en modo mock)."""

import secrets
from typing import Optional
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .config import settings
from .models import Candidato, Cuenta, Documento, Expediente, Mensaje, Vacante, registrar, slugificar


def _hace(**kw) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kw)


def sembrar(db: Session) -> None:
    """Datos de EJEMPLO (6 vacantes, 11 candidatos). Desde 2026-09-15 SOLO con SEMBRAR_DEMO=true: una
    instalación en vivo arranca con la lista de vacantes 100% vacía."""
    if db.query(Vacante).count() > 0:
        _rellenar_slugs(db)
        return
    if not settings.sembrar_demo:
        return

    vacantes = [
        Vacante(codigo="VAC-1042", titulo="Cajero(a) de sucursal", area="Operaciones", empresa="Grupo Carbe",
                ubicacion="Guadalajara, JAL", modalidad="Presencial", sueldo="$9,500 – 11,000", estado="Publicada",
                requisitos="Secundaria concluida, experiencia en manejo de efectivo, disponibilidad de horario.",
                preguntas_filtro=["¿Cuentas con disponibilidad para turnos rotativos?", "¿Tienes experiencia en manejo de efectivo?", "¿Vives en la zona metropolitana de Guadalajara?"],
                plataformas=["WhatsApp", "Jooble", "Portal"], creada_en=_hace(days=3)),
        Vacante(codigo="VAC-1041", titulo="Ejecutivo(a) de ventas telefónicas", area="Comercial", empresa="Grupo Carbe",
                ubicacion="CDMX", modalidad="Híbrido", sueldo="$12,000 + comisiones", estado="Publicada",
                requisitos="1 año en ventas o call center, buena dicción, manejo básico de CRM.",
                preguntas_filtro=["¿Tienes experiencia en ventas o call center?", "¿Cuentas con disponibilidad de lunes a sábado?"],
                plataformas=["WhatsApp", "Google Empleos", "Portal"], creada_en=_hace(days=5)),
        Vacante(codigo="VAC-1040", titulo="Auxiliar de almacén", area="Logística", empresa="Distribuidora Norte",
                ubicacion="Monterrey, NL", modalidad="Presencial", sueldo="$8,800 – 10,200", estado="Publicada",
                requisitos="Experiencia mínima de 1 año en almacén, disponibilidad de turno nocturno.",
                preguntas_filtro=["¿Tienes experiencia en almacén?", "¿Puedes trabajar turno nocturno?"],
                plataformas=["WhatsApp", "Talent.com"], creada_en=_hace(days=7)),
        Vacante(codigo="VAC-1039", titulo="Analista de nómina", area="Recursos Humanos", empresa="Grupo Carbe",
                ubicacion="Querétaro, QRO", modalidad="Híbrido", sueldo="$18,000 – 22,000", estado="En revisión",
                requisitos="3 años en nómina, dominio de IMSS, ISR y timbrado.",
                preguntas_filtro=["¿Cuántos años de experiencia tienes en nómina?", "¿Dominas timbrado e IMSS?"],
                plataformas=["Portal"], creada_en=_hace(days=2)),
        Vacante(codigo="VAC-1038", titulo="Desarrollador(a) Full-Stack", area="Tecnología", empresa="Grupo Carbe",
                ubicacion="Remoto (MX)", modalidad="Remoto", sueldo="$45,000 – 60,000", estado="Publicada",
                requisitos="4+ años con React/Node, deseable Next.js y Postgres.",
                preguntas_filtro=["¿Cuántos años llevas trabajando con React?", "¿Has trabajado con Next.js en producción?"],
                plataformas=["Google Empleos", "Portal"], creada_en=_hace(days=2)),
        Vacante(codigo="VAC-1037", titulo="Supervisor(a) de piso", area="Operaciones", empresa="Retail Bajío",
                ubicacion="León, GTO", modalidad="Presencial", sueldo="$16,500 – 19,000", estado="Borrador",
                requisitos="2 años supervisando equipos en retail.", creada_en=_hace(days=1)),
    ]
    db.add_all(vacantes)
    db.flush()
    for v in vacantes:
        v.slug = slugificar(v.titulo)
    vac = {v.codigo: v for v in vacantes}

    candidatos = [
        Candidato(codigo="C-8801", nombre="María Fernanda López", telefono="3311112222", ubicacion="Guadalajara",
                  experiencia="3 años en retail", fuente="WhatsApp", estado="cumple", etapa="Entrevista IA", score=92,
                  evidencia="Cumple escolaridad, disponibilidad de horario y experiencia en manejo de efectivo.",
                  vacante_id=vac["VAC-1042"].id, consentimiento=True, prefiltro_completo=True, creado_en=_hace(hours=2)),
        Candidato(codigo="C-8802", nombre="Jorge Alberto Ramírez", telefono="5522223333", ubicacion="CDMX",
                  experiencia="5 años en call center", fuente="LinkedIn", estado="cumple", etapa="Entrevista IA", score=88,
                  evidencia="Supera meta de experiencia; buen manejo de objeciones en el prefiltro por voz.",
                  vacante_id=vac["VAC-1041"].id, consentimiento=True, prefiltro_completo=True, creado_en=_hace(hours=4)),
        Candidato(codigo="C-8803", nombre="Ana Sofía Herrera", telefono="8133334444", ubicacion="Monterrey",
                  experiencia="1 año", fuente="OCC", estado="revision", etapa="Prefiltro", score=71,
                  evidencia="Experiencia limítrofe al mínimo requerido. Requiere validación de disponibilidad de turno nocturno.",
                  vacante_id=vac["VAC-1040"].id, consentimiento=True, creado_en=_hace(hours=6)),
        Candidato(codigo="C-8804", nombre="Luis Ángel Torres", telefono="5544445555", ubicacion="Remoto",
                  experiencia="6 años, React/Node", fuente="LinkedIn", estado="cumple", etapa="Onboarding", score=95,
                  evidencia="Stack alineado con el perfil; portafolio verificado; pretensión salarial dentro de rango.",
                  vacante_id=vac["VAC-1038"].id, consentimiento=True, prefiltro_completo=True, creado_en=_hace(days=1)),
        Candidato(codigo="C-8805", nombre="Diana Karen Méndez", telefono="3355556666", ubicacion="Zapopan",
                  experiencia="6 meses", fuente="WhatsApp", estado="revision", etapa="Prefiltro", score=64,
                  evidencia="Actitud positiva pero experiencia por debajo del mínimo. Sugerido para revisión humana.",
                  vacante_id=vac["VAC-1042"].id, consentimiento=True, creado_en=_hace(hours=3)),
        Candidato(codigo="C-8806", nombre="Roberto Carlos Nava", telefono="7226667777", ubicacion="Toluca",
                  experiencia="Sin experiencia comercial", fuente="Indeed", estado="no_cumple", etapa="Prefiltro", score=38,
                  evidencia="No cumple requisito indispensable de experiencia en ventas ni disponibilidad de horario.",
                  vacante_id=vac["VAC-1041"].id, consentimiento=True, prefiltro_completo=True, creado_en=_hace(hours=5)),
        Candidato(codigo="C-8807", nombre="Gabriela Ruiz Ponce", telefono="4427778888", ubicacion="Querétaro",
                  experiencia="4 años, IMSS/ISR", fuente="Formulario", estado="cumple", etapa="Onboarding", score=84,
                  evidencia="Domina timbrado, IMSS e ISR; conocimiento de sistema de nómina confirmado.",
                  vacante_id=vac["VAC-1039"].id, consentimiento=True, prefiltro_completo=True, creado_en=_hace(hours=8)),
        Candidato(codigo="C-8808", nombre="Emiliano Cruz Vega", telefono="8188889999", ubicacion="Monterrey",
                  experiencia="2 años", fuente="WhatsApp", estado="cumple", etapa="Onboarding", score=90,
                  evidencia="Cumple todos los requisitos; expediente en integración, pendiente comprobante de domicilio.",
                  vacante_id=vac["VAC-1040"].id, consentimiento=True, prefiltro_completo=True, creado_en=_hace(days=1)),
        Candidato(codigo="C-8809", nombre="Valeria Jiménez Soto", telefono="5599990000", ubicacion="Remoto",
                  experiencia="4 años, Next.js", fuente="LinkedIn", estado="cumple", etapa="Entrevista Humana", score=87,
                  evidencia="Fuerte en frontend; a validar experiencia en backend distribuido.",
                  vacante_id=vac["VAC-1038"].id, consentimiento=True, prefiltro_completo=True, creado_en=_hace(days=2)),
        Candidato(codigo="C-8810", nombre="Héctor Manuel Ríos", telefono="3300001111", ubicacion="Tlaquepaque",
                  experiencia="", fuente="WhatsApp", estado="no_cumple", etapa="Prefiltro", score=29,
                  evidencia="No completó el prefiltro; sin disponibilidad para el horario de la vacante.",
                  vacante_id=vac["VAC-1042"].id, consentimiento=True, creado_en=_hace(hours=1)),
        Candidato(codigo="C-8811", nombre="Fernando Castillo Prado", telefono="3322221111", ubicacion="Guadalajara",
                  experiencia="4 años en retail", fuente="OCC", estado="cumple", etapa="Contratación", score=91,
                  evidencia="Entrevista humana con RH concluida; oferta en preparación, aún sin expediente abierto.",
                  vacante_id=vac["VAC-1042"].id, consentimiento=True, prefiltro_completo=True, creado_en=_hace(hours=10)),
    ]
    db.add_all(candidatos)
    db.flush()
    cand = {c.codigo: c for c in candidatos}

    # conversación de ejemplo del prefiltro (María Fernanda por WhatsApp)
    charla = [
        ("assistant", "¡Hola! Soy Red Human. Vi tu interés en la vacante de Cajero(a) de sucursal en Guadalajara. ¿Te hago unas preguntas rápidas? 😊"),
        ("user", "¡Hola! Sí, claro."),
        ("assistant", "¿Cuentas con disponibilidad para turnos rotativos?"),
        ("user", "Sí, no tengo problema con los horarios."),
        ("assistant", "¿Tienes experiencia en manejo de efectivo?"),
        ("user", "Sí, 3 años como cajera en una tienda departamental."),
    ]
    for rol, texto in charla:
        db.add(Mensaje(candidato_id=cand["C-8801"].id, rol=rol, texto=texto, canal="whatsapp"))

    # Entrevista Humana ya programada para Valeria (etapa "Entrevista Humana")
    cand["C-8809"].entrevista_humana_entrevistador = "Iván Olvera"
    cand["C-8809"].entrevista_humana_fecha = _hace(hours=-26)  # en las próximas horas
    cand["C-8809"].entrevista_humana_modalidad = "Videollamada"
    cand["C-8809"].entrevista_humana_comentario = "Segunda ronda: profundizar en backend distribuido."

    # Fernando ya tuvo su Entrevista Humana y RH lo movió a Contratación
    cand["C-8811"].entrevista_humana_entrevistador = "Iván Olvera"
    cand["C-8811"].entrevista_humana_fecha = _hace(days=1)
    cand["C-8811"].entrevista_humana_modalidad = "Presencial"
    cand["C-8811"].entrevista_humana_realizada = True

    # expedientes de contratación (módulo 2) — reflejan el mock de onboarding
    exp1 = Expediente(candidato_id=cand["C-8808"].id, puesto="Auxiliar de almacén", sueldo="$8,800 – 10,200",
                       tipo_contratacion="Tiempo indeterminado", ubicacion="Monterrey, NL", jefe_directo="Iván Olvera",
                       fecha_ingreso=_hace(days=-2), seleccionado_por="Iván Olvera")
    exp2 = Expediente(candidato_id=cand["C-8807"].id, puesto="Analista de nómina", sueldo="$18,000 – 22,000",
                       tipo_contratacion="Tiempo indeterminado", ubicacion="Querétaro, QRO", jefe_directo="Iván Olvera",
                       fecha_ingreso=_hace(days=-4), seleccionado_por="Iván Olvera")
    exp3 = Expediente(candidato_id=cand["C-8804"].id, puesto="Desarrollador Full-Stack", sueldo="$45,000 – 60,000",
                       tipo_contratacion="Tiempo indeterminado", ubicacion="Remoto (MX)", jefe_directo="Iván Olvera",
                       fecha_ingreso=_hace(days=-12), seleccionado_por="Iván Olvera")
    # Fernando (Contratación) — expediente recién abierto, condiciones aún sin capturar del todo
    exp4 = Expediente(candidato_id=cand["C-8811"].id, puesto="Cajero(a) de sucursal", sueldo="$9,500 – 11,000",
                       ubicacion="Guadalajara, JAL", seleccionado_por="Iván Olvera")
    db.add_all([exp1, exp2, exp3, exp4])
    db.flush()

    docs = {
        exp1.id: [("Identificación oficial", "recibido"), ("CURP", "recibido"), ("Constancia de Situación Fiscal / RFC", "recibido"),
                  ("Comprobante de domicilio", "pendiente"), ("Número de Seguridad Social", "revision"), ("Cuenta bancaria / CLABE", "pendiente")],
        exp2.id: [("Identificación oficial", "recibido"), ("CURP", "recibido"), ("Constancia de Situación Fiscal / RFC", "revision"),
                  ("Comprobante de domicilio", "pendiente"), ("Número de Seguridad Social", "pendiente"), ("Cuenta bancaria / CLABE", "recibido")],
        exp3.id: [("Identificación oficial", "recibido"), ("CURP", "recibido"), ("Constancia de Situación Fiscal / RFC", "recibido"),
                  ("Comprobante de domicilio", "recibido"), ("Número de Seguridad Social", "recibido"), ("Cuenta bancaria / CLABE", "recibido")],
        exp4.id: [("Identificación oficial", "pendiente"), ("CURP", "pendiente"), ("Constancia de Situación Fiscal / RFC", "pendiente"),
                  ("Comprobante de domicilio", "pendiente"), ("Número de Seguridad Social", "pendiente"), ("Cuenta bancaria / CLABE", "pendiente")],
    }
    for exp_id, lista in docs.items():
        for tipo, estado in lista:
            db.add(
                Documento(
                    expediente_id=exp_id,
                    tipo=tipo,
                    estado=estado,
                    # los ya recibidos vienen confirmados por RH: así el expediente completo puede darse de alta
                    revisado_por="Iván Olvera" if estado == "recibido" else "",
                    subido_en=_hace(days=1) if estado in ("recibido", "revision") else None,
                    notas_ia="Documento legible y vigente." if estado == "recibido" else "",
                )
            )
    exp3.estado = "completo"

    # Fase 2: la semilla se escribe en la forma legado (proceso en la persona) y aquí se
    # convierte a Postulaciones — misma lógica que scripts/migrar_postulaciones.py.
    from .migraciones import migrar_postulaciones
    migrar_postulaciones(db)

    registrar(db, "sistema", "semilla_cargada", "sistema", "seed", {"vacantes": len(vacantes), "candidatos": len(candidatos)})
    db.commit()


def sembrar_admin(db: Session) -> None:
    """Crea el primer administrador si la instalación no tiene ninguno.

    La contraseña sale de ADMIN_PASSWORD; si no está definida se genera una y se
    imprime una sola vez en el log (journalctl -u redhuman-api), nunca se guarda
    en claro. En ambos casos el usuario debe cambiarla al primer ingreso.
    """
    from .config import settings
    from .models import Usuario
    from .services import auth

    if db.query(Usuario).filter(Usuario.rol == "Administrador", Usuario.activo.is_(True)).count() > 0:
        return

    correo = settings.admin_email.strip().lower()
    existente = db.query(Usuario).filter(Usuario.correo == correo).first()
    if existente:  # existía pero sin rol admin (o desactivado): se restituye
        existente.rol, existente.activo = "Administrador", True
        db.commit()
        return

    generada = not settings.admin_password
    password = settings.admin_password or secrets.token_urlsafe(12)
    db.add(
        Usuario(
            correo=correo,
            nombre=settings.admin_nombre,
            puesto="Administrador de la plataforma",
            rol="Administrador",
            hash_pass=auth.hashear(password),
            debe_cambiar_pass=True,
        )
    )
    registrar(db, "sistema", "usuario_creado", "usuario", correo, {"rol": "Administrador", "via": "arranque inicial"})
    db.commit()

    if generada:
        print("\n" + "=" * 62, flush=True)
        print("  PRIMER ACCESO A RED HUMAN AI", flush=True)
        print(f"  usuario:    {correo}", flush=True)
        print(f"  contraseña: {password}", flush=True)
        print("  Cámbiala al entrar. Este mensaje no se vuelve a mostrar.", flush=True)
        print("=" * 62 + "\n", flush=True)


def _rellenar_slugs(db: Session) -> None:
    """Bases anteriores a la columna `slug`: genera el que falte para /aplicar/[slug]."""
    faltantes = db.query(Vacante).filter((Vacante.slug == "") | (Vacante.slug.is_(None))).all()
    if not faltantes:
        return
    usados = {s for (s,) in db.query(Vacante.slug).filter(Vacante.slug != "").all()}
    for v in faltantes:
        base = slugificar(v.titulo)
        v.slug = base if base not in usados else f"{base}-{v.id}"
        usados.add(v.slug)
    db.commit()


def slug_cuenta_unico(db: Session, nombre: str, cuenta_id: Optional[int] = None) -> str:
    """Slug público de una Cuenta (2026-09-17), único entre Cuentas."""
    base = slugificar(nombre or "") or "cuenta"
    usados = {sl for (sl,) in db.query(Cuenta.slug).filter(Cuenta.slug != "", Cuenta.id != (cuenta_id or 0)).all()}
    if base not in usados:
        return base
    n = 2
    while f"{base}-{n}" in usados:
        n += 1
    return f"{base}-{n}"


def rellenar_slugs_cuentas(db: Session) -> int:
    """Bases anteriores a `Cuenta.slug`: genera el que falte (portal por Cuenta)."""
    faltantes = db.query(Cuenta).filter((Cuenta.slug == "") | (Cuenta.slug.is_(None))).order_by(Cuenta.id).all()
    for cu in faltantes:
        cu.slug = slug_cuenta_unico(db, cu.nombre_comercial or cu.nombre, cu.id)
        db.flush()
    if faltantes:
        db.commit()
    return len(faltantes)
