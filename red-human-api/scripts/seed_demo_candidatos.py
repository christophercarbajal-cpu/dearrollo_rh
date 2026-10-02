"""Semilla de candidatos DEMO para mostrar el pipeline completo (los 6 pasos de
ETAPAS_CANDIDATO) mientras Meta aprueba la plantilla de WhatsApp — separado de
`app/seed.py` (esos son los datos de arranque de una instalación nueva; estos son
desechables y pensados para borrarse después de la demo, ver `borrar_demo_candidatos.py`).

No manda NADA por WhatsApp ni toca Meta: solo inserta filas directo en la base, con
números de teléfono obviamente falsos (lada "555", inexistente en México) para que
nunca puedan confundirse con un número real ni recibir tráfico real si algo llamara
`enviar_mensaje` por error.

Marca de identificación: todo `Candidato.codigo` creado aquí empieza con "DEMO-" —
es lo que usa `borrar_demo_candidatos.py` para encontrarlos y borrarlos sin tocar
nada más de la base.

Uso (desde red-human-api/):
    .venv/bin/python scripts/seed_demo_candidatos.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # para poder importar `app.*`

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Candidato,
    Documento,
    Entrevista,
    Expediente,
    Mensaje,
    Vacante,
    registrar,
    slugificar,
)
from app.services import ia  # noqa: E402
from app.services.ia import EvaluacionEntrevista  # noqa: E402
from app.services.whatsapp import numero_e164  # noqa: E402

PREFIJO = "DEMO-"


def _hace(**kw) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kw)


def _en(**kw) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**kw)


def _vacante_publicada(db) -> Vacante:
    """Reusa cualquier vacante ya Publicada; si no hay ninguna, crea una de demo."""
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id.desc()).first()
    if v:
        print(f"  Vacante reusada: {v.codigo} — {v.titulo}")
        return v

    v = Vacante(
        codigo=f"{PREFIJO}VAC-01",
        titulo="Ejecutivo(a) de Atención a Clientes",
        area="Operaciones",
        empresa="Grupo Carbe",
        ubicacion="Ciudad de México",
        modalidad="Presencial",
        sueldo="$11,000 – 13,500",
        estado="Publicada",
        requisitos="Bachillerato concluido, 1 año de experiencia en atención a clientes, disponibilidad de horario.",
        descripcion="Vacante de ejemplo creada por seed_demo_candidatos.py — no había ninguna Publicada en la base.",
        plataformas=["WhatsApp", "Portal"],
    )
    db.add(v)
    db.flush()
    v.slug = slugificar(v.titulo)
    print(f"  Vacante creada: {v.codigo} — {v.titulo}")
    return v


def _mensajes_prefiltro(db, candidato_id: int, vacante_titulo: str, completo: bool) -> None:
    """Conversación de WhatsApp de ejemplo — para que la pestaña de WhatsApp del candidato
    no se vea vacía en ninguna etapa, como si de verdad hubiera pasado por el prefiltro."""
    charla = [
        ("assistant", (
            f"¡Hola! Soy Red Human. Vi tu interés en la vacante de {vacante_titulo}. "
            "Antes de comenzar, necesito tu autorización para tratar tus datos conforme a nuestro "
            "Aviso de Privacidad. ¿Autorizas continuar? (Responde *Sí* o *Acepto*)"
        )),
        ("user", "Sí, acepto."),
        ("assistant", "¡Gracias! ¿Cuentas con la disponibilidad de horario que pide la vacante?"),
        ("user", "Sí, tengo total disponibilidad."),
    ]
    if completo:
        charla += [
            ("assistant", "Perfecto, cuéntame un poco: ¿cuánta experiencia tienes en un puesto similar?"),
            ("user", "Un poco más de dos años en un puesto muy parecido."),
        ]
    for rol, texto in charla:
        db.add(Mensaje(candidato_id=candidato_id, rol=rol, texto=texto, canal="whatsapp"))


def _transcript_demo(nombre: str, vacante_titulo: str) -> list:
    """5 turnos de ejemplo — mismo shape que arma el frontend en modo avatar/texto
    ([{"rol": "assistant"|"user", "texto": str}, ...])."""
    primer_nombre = nombre.split(" ")[0]
    return [
        {"rol": "assistant", "texto": (
            f"Hola, soy Red Human. Gracias por participar en el proceso para {vacante_titulo}. "
            f"{primer_nombre}, cuéntame de tu experiencia más reciente relacionada con este puesto."
        )},
        {"rol": "user", "texto": (
            "Claro. Estuve poco más de dos años en un puesto muy similar, donde me tocaba atender "
            "clientes directamente y resolver incidencias del día a día."
        )},
        {"rol": "assistant", "texto": "Qué bien. ¿Cuál ha sido el reto más difícil que has enfrentado ahí y cómo lo resolviste?"},
        {"rol": "user", "texto": (
            "Una vez tuvimos un pico de demanda muy fuerte en temporada alta; organicé al equipo por "
            "turnos y prioridades y salimos adelante sin bajar el servicio."
        )},
        {"rol": "assistant", "texto": (
            f"Excelente, {primer_nombre}. Con eso concluimos las preguntas — muchas gracias por tu "
            "tiempo. El equipo de RH va a revisar tu entrevista y te contactará pronto. 😊"
        )},
    ]


def _evaluacion_demo(vacante_titulo: str, recomendacion: str, match: int, calif: float) -> EvaluacionEntrevista:
    """Mismo formato que produce ia.evaluar_entrevista() — se usa la clase real para
    garantizar que el shape del JSON guardado sea idéntico al de una evaluación real."""
    if recomendacion == "avanzar":
        fortalezas = [
            "Comunicación clara y estructurada durante toda la entrevista.",
            "Experiencia directamente relacionada con las funciones del puesto.",
            "Actitud proactiva ante una situación de carga de trabajo elevada.",
        ]
        riesgos = []
        resumen = f"Candidato(a) con comunicación sólida y experiencia alineada con los requisitos de {vacante_titulo}."
    else:  # "revision"
        fortalezas = [
            "Buena disposición y comunicación cordial.",
            "Experiencia relacionada, aunque no exactamente en el mismo giro.",
        ]
        riesgos = ["Experiencia algo por debajo de lo ideal para el nivel de seniority del puesto."]
        resumen = f"Candidato(a) con comunicación aceptable y experiencia parcialmente alineada con {vacante_titulo}; vale la pena que RH profundice."

    return EvaluacionEntrevista(
        resumen=resumen,
        fortalezas=fortalezas,
        riesgos=riesgos,
        calif_experiencia=calif,
        calif_comunicacion=min(10.0, calif + 0.5),
        match_perfil=match,
        recomendacion=recomendacion,
        evidencia="Transcripción: describió su experiencia reciente y cómo resolvió un reto de carga de trabajo elevada.",
    )


def _crear_entrevista(db, candidato: Candidato, vacante: Vacante, numero: int, recomendacion: str, match: int, calif: float) -> Entrevista:
    """Entrevista ya evaluada — mismos campos que deja `finalizar()` en routers/entrevistas.py
    al cerrar una entrevista real con avatar."""
    import secrets

    guion = ia._guion_demo(vacante.titulo)
    ev = _evaluacion_demo(vacante.titulo, recomendacion, match, calif)
    e = Entrevista(
        codigo=f"{PREFIJO}ENT-{numero:02d}",
        candidato_id=candidato.id,
        token=secrets.token_urlsafe(24),
        tipo="avatar",
        estado="evaluada",
        guion=guion.model_dump(),
        transcript=_transcript_demo(candidato.nombre, vacante.titulo),
        evaluacion=ev.model_dump(),
        consentimiento=True,
        consentimiento_fecha=_hace(days=1),
    )
    db.add(e)
    db.flush()
    candidato.score = max(candidato.score, ev.match_perfil)
    candidato.evidencia = ev.evidencia
    return e


def sembrar_demo(db) -> None:
    ya_existe = db.query(Candidato).filter(Candidato.codigo.like(f"{PREFIJO}%")).first()
    if ya_existe:
        print(f"Ya hay candidatos demo en la base (ej. {ya_existe.codigo}). No se creó nada.")
        print("Corre borrar_demo_candidatos.py primero si quieres regenerarlos.")
        return

    print("Vacante:")
    v = _vacante_publicada(db)
    puesto = v.titulo

    print("\nCandidatos:")

    # ------------------------------------------------------------------
    # 1) Prefiltro — a media conversación, todavía sin clasificar
    # ------------------------------------------------------------------
    c1 = Candidato(
        codigo=f"{PREFIJO}C-01", nombre="Karla Sofía Mendoza", telefono="5555550001",
        wa_nombre="Karla Mendoza", wa_id=numero_e164("5555550001"),
        ubicacion="Ciudad de México", experiencia="", fuente="Formulario",
        estado="pendiente", etapa="Prefiltro", score=0, prefiltro_completo=False,
        vacante_id=v.id, consentimiento=True, consentimiento_fecha=_hace(hours=1),
        creado_en=_hace(hours=1),
    )
    db.add(c1)
    db.flush()
    _mensajes_prefiltro(db, c1.id, puesto, completo=False)
    print(f"  {c1.codigo} — {c1.nombre} (Prefiltro, en curso)")

    # ------------------------------------------------------------------
    # 2) Entrevista IA — prefiltro completo, entrevista con avatar ya evaluada
    # ------------------------------------------------------------------
    c2 = Candidato(
        codigo=f"{PREFIJO}C-02", nombre="Diego Armando Salcedo", telefono="5555550002",
        wa_nombre="Diego Salcedo", wa_id=numero_e164("5555550002"),
        ubicacion="Guadalajara, JAL", experiencia="2 años en atención a clientes", fuente="Formulario",
        estado="cumple", etapa="Entrevista IA", score=0, prefiltro_completo=True,
        vacante_id=v.id, consentimiento=True, consentimiento_fecha=_hace(days=2),
        creado_en=_hace(days=2),
    )
    db.add(c2)
    db.flush()
    _mensajes_prefiltro(db, c2.id, puesto, completo=True)
    _crear_entrevista(db, c2, v, numero=2, recomendacion="avanzar", match=90, calif=8.5)
    print(f"  {c2.codigo} — {c2.nombre} (Entrevista IA, ya evaluada)")

    # ------------------------------------------------------------------
    # 3) Evaluación — entrevista evaluada, recomendación "revisión" para que
    #    se note por qué le toca a RH decidir aquí
    # ------------------------------------------------------------------
    c3 = Candidato(
        codigo=f"{PREFIJO}C-03", nombre="Renata Isabel Cordero", telefono="5555550003",
        wa_nombre="Renata Cordero", wa_id=numero_e164("5555550003"),
        ubicacion="Monterrey, NL", experiencia="8 meses en un puesto similar", fuente="Formulario",
        estado="cumple", etapa="Entrevista IA", score=0, prefiltro_completo=True,
        vacante_id=v.id, consentimiento=True, consentimiento_fecha=_hace(days=3),
        creado_en=_hace(days=3),
    )
    db.add(c3)
    db.flush()
    _mensajes_prefiltro(db, c3.id, puesto, completo=True)
    _crear_entrevista(db, c3, v, numero=3, recomendacion="revision", match=76, calif=6.5)
    print(f"  {c3.codigo} — {c3.nombre} (Evaluación, recomendación: revisión)")

    # ------------------------------------------------------------------
    # 4) Entrevista Humana — ya agendada, todavía sin realizarse
    # ------------------------------------------------------------------
    c4 = Candidato(
        codigo=f"{PREFIJO}C-04", nombre="Bruno Alejandro Ferreyra", telefono="5555550004",
        wa_nombre="Bruno Ferreyra", wa_id=numero_e164("5555550004"),
        ubicacion="Puebla, PUE", experiencia="3 años en atención a clientes", fuente="Formulario",
        estado="cumple", etapa="Entrevista Humana", score=0, prefiltro_completo=True,
        vacante_id=v.id, consentimiento=True, consentimiento_fecha=_hace(days=4),
        creado_en=_hace(days=4),
        entrevista_humana_entrevistador="Iván Olvera",
        entrevista_humana_fecha=_en(days=2, hours=3),
        entrevista_humana_modalidad="Videollamada",
        entrevista_humana_comentario="Segunda validación técnica antes de pasar a Contratación.",
        entrevista_humana_realizada=False,
    )
    db.add(c4)
    db.flush()
    _mensajes_prefiltro(db, c4.id, puesto, completo=True)
    _crear_entrevista(db, c4, v, numero=4, recomendacion="avanzar", match=85, calif=8.0)
    print(f"  {c4.codigo} — {c4.nombre} (Entrevista Humana, agendada)")

    # ------------------------------------------------------------------
    # 5) Contratación — ya tuvo Entrevista Humana, expediente recién abierto
    #    con progreso PARCIAL (ni vacío ni completo)
    # ------------------------------------------------------------------
    c5 = Candidato(
        codigo=f"{PREFIJO}C-05", nombre="Ximena Paola Duarte", telefono="5555550005",
        wa_nombre="Ximena Duarte", wa_id=numero_e164("5555550005"),
        ubicacion="Ciudad de México", experiencia="4 años en atención a clientes", fuente="Formulario",
        estado="cumple", etapa="Contratación", score=0, prefiltro_completo=True,
        vacante_id=v.id, consentimiento=True, consentimiento_fecha=_hace(days=6),
        creado_en=_hace(days=6),
        entrevista_humana_entrevistador="Iván Olvera",
        entrevista_humana_fecha=_hace(days=2),
        entrevista_humana_modalidad="Presencial",
        entrevista_humana_realizada=True,
    )
    db.add(c5)
    db.flush()
    _mensajes_prefiltro(db, c5.id, puesto, completo=True)
    _crear_entrevista(db, c5, v, numero=5, recomendacion="avanzar", match=91, calif=9.0)

    exp5 = Expediente(
        candidato_id=c5.id, puesto=puesto, sueldo=v.sueldo, tipo_contratacion="Tiempo indeterminado",
        ubicacion=v.ubicacion, jefe_directo="Iván Olvera", fecha_ingreso=_en(days=10),
        seleccionado_por="Iván Olvera",
    )
    db.add(exp5)
    db.flush()
    docs5 = [
        ("Identificación oficial", "recibido"), ("CURP", "recibido"),
        ("Constancia de Situación Fiscal / RFC", "revision"),
        ("Comprobante de domicilio", "pendiente"), ("Número de Seguridad Social", "pendiente"),
        ("Cuenta bancaria / CLABE", "pendiente"),
    ]
    for tipo, estado in docs5:
        db.add(Documento(
            expediente_id=exp5.id, tipo=tipo, estado=estado,
            revisado_por="Iván Olvera" if estado in ("recibido", "revision") else "",
            subido_en=_hace(days=1) if estado in ("recibido", "revision") else None,
            notas_ia="Documento legible y vigente." if estado == "recibido" else "",
        ))
    print(f"  {c5.codigo} — {c5.nombre} (Contratación, expediente con progreso parcial)")

    # ------------------------------------------------------------------
    # 6) Onboarding — expediente casi completo, checklist avanzado
    # ------------------------------------------------------------------
    c6 = Candidato(
        codigo=f"{PREFIJO}C-06", nombre="Santiago Emmanuel Rosales", telefono="5555550006",
        wa_nombre="Santiago Rosales", wa_id=numero_e164("5555550006"),
        ubicacion="Querétaro, QRO", experiencia="5 años en atención a clientes", fuente="Formulario",
        estado="cumple", etapa="Onboarding", score=0, prefiltro_completo=True,
        vacante_id=v.id, consentimiento=True, consentimiento_fecha=_hace(days=10),
        creado_en=_hace(days=10),
        entrevista_humana_entrevistador="Iván Olvera",
        entrevista_humana_fecha=_hace(days=6),
        entrevista_humana_modalidad="Videollamada",
        entrevista_humana_realizada=True,
    )
    db.add(c6)
    db.flush()
    _mensajes_prefiltro(db, c6.id, puesto, completo=True)
    _crear_entrevista(db, c6, v, numero=6, recomendacion="avanzar", match=94, calif=9.3)

    exp6 = Expediente(
        candidato_id=c6.id, puesto=puesto, sueldo=v.sueldo, tipo_contratacion="Tiempo indeterminado",
        ubicacion=v.ubicacion, jefe_directo="Iván Olvera", fecha_ingreso=_hace(days=-3),
        seleccionado_por="Iván Olvera", contrato="Firmado", alta_administrativa="Realizada",
        equipo_accesos="Listo",
    )
    db.add(exp6)
    db.flush()
    docs6 = [
        ("Identificación oficial", "recibido"), ("CURP", "recibido"),
        ("Constancia de Situación Fiscal / RFC", "recibido"), ("Número de Seguridad Social", "recibido"),
        ("Cuenta bancaria / CLABE", "recibido"), ("Comprobante de domicilio", "pendiente"),
    ]
    for tipo, estado in docs6:
        db.add(Documento(
            expediente_id=exp6.id, tipo=tipo, estado=estado,
            revisado_por="Iván Olvera" if estado == "recibido" else "",
            subido_en=_hace(days=2) if estado == "recibido" else None,
            notas_ia="Documento legible y vigente." if estado == "recibido" else "",
        ))
    print(f"  {c6.codigo} — {c6.nombre} (Onboarding, checklist avanzado)")

    # Fase 2: los DEMO se escriben en forma legado (proceso en la persona) y aquí se
    # convierten a Postulaciones — misma lógica que scripts/migrar_postulaciones.py.
    from app.migraciones import migrar_postulaciones
    migrar_postulaciones(db)

    registrar(
        db, "sistema", "semilla_demo_cargada", "sistema", "seed_demo",
        {"vacante": v.codigo, "candidatos": [f"{PREFIJO}C-{i:02d}" for i in range(1, 7)]},
    )
    db.commit()
    print(f"\nListo — 6 candidatos demo creados, uno por etapa, sobre la vacante {v.codigo}.")
    print(f"Para borrarlos después de la demo: .venv/bin/python scripts/borrar_demo_candidatos.py")


def main() -> int:
    db = SessionLocal()
    try:
        sembrar_demo(db)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
