"""Red Human AI — API (FastAPI).

Módulos implementados:
  1. Reclutamiento y selección  → /vacantes, /candidatos, /webhooks/whatsapp
  2. Contratación e integración → /contratacion
  4. Requisiciones inteligentes → /requisiciones

Documentación interactiva: http://localhost:8000/docs
"""

from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, SessionLocal, engine
from .migraciones import crear_tablas_base, crear_tablas_conocimiento, crear_tablas_modulos_rh, candidatos_sin_postulacion, relajar_not_null, sincronizar, evaluaciones_sin_migrar
from .migraciones import asegurar_reglas_entrevistador
from .routers import agente, auth, candidatos, capacitacion, clientes, clima, colaboradores, configuracion, conocimiento, contratacion, cuentas, desempeno, emails_preview, empleados, entrevistas, evaluaciones, expediente_publico, feeds, firmas, webhooks_proveedores, metricas, notificaciones, onboarding, plantillas, requisiciones, vacantes, webhooks, integraciones
from .seed import rellenar_slugs_cuentas, sembrar, sembrar_admin
from .models import TABLAS_CONOCIMIENTO, TABLAS_MODULOS_RH
from .services import modulos_rh, rag
from .services.agenda import revisar_videollamadas_noshow
from .services.recordatorios import revisar_recordatorios_documentos
from .services.recordatorios_entrevista import revisar_recordatorios_entrevista
from .routers.entrevistas import cerrar_entrevistas_inactivas
from .services.clima_cierre import cerrar_mediciones_vencidas
from .services.avatar import avatar_activo, estado_avatar
from .services.ia import ia_activa
from .services.whatsapp import proveedor as whatsapp_proveedor, whatsapp_activo

# Zero-Touch fase 1: barre videollamadas vencidas cada 5 min (ver services/agenda.py).
scheduler = AsyncIOScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # HOTFIX 2026-09-18: el núcleo se crea como siempre (fatal si falla); las tablas de la Base de
    # Conocimiento van aparte y NUNCA tumban el arranque — si fallan, el módulo responde 503 y se
    # imprime el error exacto del motor para corregirlo con calma.
    crear_tablas_base(engine)
    error_rag = crear_tablas_conocimiento(engine)
    rag.marcar_disponible(error_rag is None, error_rag or "")
    if error_rag:
        print(f"[conocimiento] ⚠️ Base de Conocimiento DESHABILITADA: no se pudieron crear sus tablas → {error_rag}", flush=True)
    # 2026-09-22 (andamiaje Desempeño/Clima): mismo trato que la Base de Conocimiento — nunca fatales.
    error_modulos = crear_tablas_modulos_rh(engine)
    modulos_rh.marcar_disponible(error_modulos is None, error_modulos or "")
    if error_modulos:
        print(f"[modulos-rh] ⚠️ Desempeño/Clima DESHABILITADOS: no se pudieron crear sus tablas → {error_modulos}", flush=True)
    omitir = set()
    if error_rag:
        omitir |= set(TABLAS_CONOCIMIENTO)
    if error_modulos:
        omitir |= set(TABLAS_MODULOS_RH)
    cambios = sincronizar(engine, omitir=omitir or None)  # columnas nuevas sobre una base ya existente
    if cambios:
        print(f"[esquema] columnas agregadas: {', '.join(cambios)}")
    # Hotfix 2026-09-23: columnas que el modelo ya permite en NULL y la base vieja aún exige
    # (asignaciones_curso.colaborador_id → la liga demo / asignaciones externas daban 500).
    relajadas = relajar_not_null(engine, omitir=omitir or None)
    if relajadas:
        print(f"[esquema] NOT NULL retirado: {', '.join(relajadas)}", flush=True)
    with SessionLocal() as db:
        sembrar(db)
        sembrar_admin(db)
        if rellenar_slugs_cuentas(db):  # 2026-09-17: portal por Cuenta
            print("[cuentas] slugs generados para el portal por Cuenta", flush=True)
        # 2026-09-15 (Fase 1): «solo le llega al candidato» — Cuentas cuya regla de entrevista_agendada
        # nació apagada para el entrevistador antes de 7A y que nadie editó a mano: se encienden.
        rescatadas = asegurar_reglas_entrevistador(db)
        if rescatadas:
            print(f"[notificaciones] regla entrevista_agendada: entrevistador encendido en {rescatadas} Cuenta(s)", flush=True)
        # Fase 2: la migración de DATOS es manual por convención del proyecto (script con
        # confirmación, nunca automática). Garantía de despliegue: si hay candidatos sin
        # postulación, la API NO arranca — el código nuevo jamás sirve peticiones sobre una base
        # sin migrar (Kanban vacío, webhook creando postulaciones duplicadas, etc.).
        pendientes = candidatos_sin_postulacion(db)
        if pendientes:
            raise RuntimeError(
                f"[fase2] {pendientes} candidato(s) sin postulación: la base no está migrada a Fase 2. "
                "Corre `python scripts/migrar_postulaciones.py --forzar` (desde red-human-api/) y vuelve a arrancar."
            )
        # Evaluaciones unificadas (2026-09-29): mismo principio. El código nuevo solo lee `evaluaciones`; con
        # entrevistas/evaluaciones del modelo anterior sin migrar la ficha las «perdería» → no arranca.
        if not error_modulos:
            sin_migrar = evaluaciones_sin_migrar(db)
            if sin_migrar:
                raise RuntimeError(
                    f"[evaluaciones] {sin_migrar} entrevista(s)/evaluación(es) del modelo anterior sin migrar. "
                    "Corre `python scripts/migrar_evaluaciones_unificadas.py --forzar` (desde red-human-api/) y vuelve a arrancar."
                )

    # 2026-09-14: en el log de arranque queda qué variables de Anam ve ESTE proceso (presencia, no
    # valores). Si la sala "cae a texto" en producción, aquí se ve si es configuración o Anam.
    ea = estado_avatar()
    print(
        "[avatar] Anam "
        + ("ACTIVO" if ea["activo"] else "INACTIVO (entrevistas por texto)")
        + f" · ANAM_API_KEY={'ok' if ea['ANAM_API_KEY'] else 'FALTA'}"
        + f" ANAM_AVATAR_ID={'ok' if ea['ANAM_AVATAR_ID'] else 'FALTA'}"
        + f" ANAM_LLM_ID={'ok' if ea['ANAM_LLM_ID'] else 'FALTA'}",
        flush=True,
    )

    # max_instances=1 + coalesce: si una corrida se alarga (Meta lento) la siguiente NO se encola
    # encima ni se acumulan disparos perdidos — junto con la reclamación del flag en
    # services/agenda.py evita el aviso de reagendar duplicado (2026-09-15).
    if settings.whatsapp_provider == "meta" and not settings.meta_plantilla_aviso:
        print(
            "[whatsapp] ⚠️ META_PLANTILLA_AVISO sin configurar: los WhatsApp a números que no han escrito a la "
            "empresa (entrevistadores, contactos del Cliente) serán rechazados por Meta (131047).",
            flush=True,
        )

    scheduler.add_job(
        revisar_videollamadas_noshow, "interval", minutes=5,
        id="noshow_videollamadas", replace_existing=True,
        max_instances=1, coalesce=True, misfire_grace_time=120,
    )
    # Fase 3 (2026-09-15): recordatorios automáticos de documentos (cada hora decide si toca).
    scheduler.add_job(
        revisar_recordatorios_documentos, "interval", minutes=60,
        id="recordatorios_documentos", replace_existing=True,
        max_instances=1, coalesce=True, misfire_grace_time=300,
    )
    # 2026-09-19: recordatorio automático de la Entrevista Humana (N horas antes, configurable).
    scheduler.add_job(
        revisar_recordatorios_entrevista, "interval", minutes=10,
        id="recordatorios_entrevista", replace_existing=True,
        max_instances=1, coalesce=True, misfire_grace_time=300,
    )
    # 2026-09-17: entrevistas IA abandonadas (pestaña cerrada sin /finalizar) se cierran y evalúan.
    scheduler.add_job(
        cerrar_entrevistas_inactivas, "interval", minutes=5,
        id="entrevistas_inactivas", replace_existing=True,
        max_instances=1, coalesce=True, misfire_grace_time=120,
    )
    # 2026-09-27 (Clima v2): una medición abierta pasa a cerrada al llegar su fecha de cierre.
    scheduler.add_job(
        cerrar_mediciones_vencidas, "interval", minutes=5,
        id="clima_cierre", replace_existing=True,
        max_instances=1, coalesce=True, misfire_grace_time=120,
    )
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="Red Human AI · API",
    version="0.2.0",
    description="Agente integral de RH — Módulo 1 (Reclutamiento y selección) y Módulo 2 (Contratación e integración).",
    lifespan=lifespan,
)

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
default_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://localhost:3002",
    "http://127.0.0.1:3002",
    "http://192.168.100.50:3000",
]
for d in default_origins:
    if d not in origins:
        origins.append(d)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(requisiciones.router)
app.include_router(empleados.router)
app.include_router(vacantes.router)
app.include_router(clientes.router)
app.include_router(plantillas.router)
app.include_router(candidatos.router)
app.include_router(entrevistas.router)
app.include_router(contratacion.router)
app.include_router(expediente_publico.router)
app.include_router(colaboradores.router)
app.include_router(metricas.router)
app.include_router(configuracion.router)
app.include_router(cuentas.router)
app.include_router(notificaciones.router)
app.include_router(capacitacion.router)
app.include_router(conocimiento.router)
app.include_router(desempeno.router)
app.include_router(clima.router)
app.include_router(emails_preview.router)
app.include_router(webhooks.router)
app.include_router(agente.router)
app.include_router(integraciones.router)
app.include_router(feeds.router)  # 2026-09-27: feeds XML públicos (Jooble, Talent.com)
app.include_router(onboarding.router)  # 2026-09-28: Onboarding v2 (plantillas y tareas)
app.include_router(evaluaciones.router)  # 2026-09-28: evaluaciones y verificaciones del candidato
app.include_router(firmas.router)  # 2026-09-29: firma electrónica incrustada (Dropbox Sign)
app.include_router(webhooks_proveedores.router)  # 2026-09-29: /api/webhooks/dropbox y /api/webhooks/psicometricas


@app.get("/salud")
def salud():
    return {
        "ok": True,
        "ia_configurada": ia_activa(),
        "whatsapp_configurado": whatsapp_activo(),
        "whatsapp_proveedor": whatsapp_proveedor(),
        "whatsapp_webhook_firmado": bool(settings.meta_app_secret) if settings.whatsapp_provider == "meta" else None,
        "avatar_configurado": avatar_activo(),
        "modelo": settings.openai_model,
        "modo": "producción" if ia_activa() else "demo (sin OPENAI_API_KEY)",
    }