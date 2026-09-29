from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./redhuman.db"

    # Zona horaria de la organización (2026-09-29): la base guarda UTC; todo lo que se MUESTRA o se
    # CAPTURA como hora de reloj usa esta zona (`app/fechas.py`). El frontend lee NEXT_PUBLIC_ZONA_HORARIA.
    zona_horaria: str = "America/Mexico_City"

    # 2026-09-15 (arranque en vivo): las vacantes/candidatos de EJEMPLO ya no se siembran solos en una
    # base vacía. Solo con SEMBRAR_DEMO=true (demos, scripts de verificación). Para una base que ya los
    # tiene: `scripts/limpiar_datos_demo.py --forzar` (baja lógica, conserva historial).
    sembrar_demo: bool = False

    # IA (OpenAI)
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"

    # Fase F — agente global "Pregunta a Red Human" (punto 29): tope diario de mensajes por
    # usuario (Q7) — evita abuso/costo descontrolado, sin bloquear el uso normal.
    agente_limite_mensajes_dia: int = 60

    # Avatar de entrevistas (Anam) — vacío = modo demo (entrevista por texto)
    anam_api_key: str = ""
    anam_avatar_id: str = ""
    anam_voice_id: str = ""
    anam_llm_id: str = ""
    anam_max_sesion_seg: int = 900

    # URL pública del frontend (ligas de entrevista para candidatos)
    app_url: str = "http://localhost:3000"

    # Primer administrador. Si no se define contraseña, se genera una al arrancar
    # y se imprime UNA sola vez en el log del servicio.
    admin_email: str = "admin@redhuman.mx"
    admin_nombre: str = "Administrador"
    admin_password: str = ""

    # WhatsApp
    # "meta" = WhatsApp Cloud API oficial (Meta) · "waha"/"evolution" = gateway propio
    # "" = modo demo (el mensaje se guarda en la base pero no sale)
    whatsapp_provider: str = ""
    whatsapp_public_number: str = ""  # número legible para deep-links wa.me

    # --- Meta · WhatsApp Cloud API ---
    meta_phone_number_id: str = ""   # id del número emisor (panel de Meta)
    meta_waba_id: str = ""           # id de la cuenta de WhatsApp Business
    meta_whatsapp_token: str = ""    # token de acceso (System User, permanente)
    # Mismo string que capturas al dar de alta el webhook en Meta.
    # TODO: mover a .env — este default quedó en el repo y conviene rotarlo.
    meta_verify_token: str = "redhuman_webhook_verify_token_2026_x89a"
    meta_app_secret: str = ""        # App Secret: valida la firma X-Hub-Signature-256
    meta_api_version: str = "v21.0"
    # Plantilla aprobada para escribirle a alguien fuera de la ventana de 24 h.
    # Sin ella, esos mensajes los rechaza Meta con el error 131047.
    meta_plantilla_aviso: str = ""
    meta_plantilla_idioma: str = "es_MX"
    # 2026-09-15 — Solicitud/recordatorio de documentos por WhatsApp (Contratación/Onboarding).
    # Plantilla aprobada en Meta que pide los papeles; se dispara con «Solicitar documentos» y
    # «Enviar recordatorio». Sus variables {{1}}..{{n}} se llenan en el orden de
    # META_PLANTILLA_DOCUMENTOS_PARAMS (valores válidos: nombre, documentos, liga, empresa, vacante).
    # Si la plantilla falla (no aprobada, nombre distinto) o está vacía, sale el texto libre de siempre.
    meta_plantilla_documentos: str = "solicitud_documentos_rh"
    meta_plantilla_documentos_params: str = "nombre,documentos,liga"
    # 2026-09-17: recordatorios en 3 niveles. El texto de una plantilla de Meta es fijo, así que el tono
    # por nivel va en el texto libre (ventana de 24 h abierta) y en el correo; si registras plantillas
    # con tono intermedio/definitivo en Meta, pon aquí su nombre (mismas variables/orden que la base).
    # Vacías → se usa META_PLANTILLA_DOCUMENTOS para todos los niveles.
    meta_plantilla_recordatorio_2: str = ""
    meta_plantilla_recordatorio_3: str = ""
    # 2026-09-18: plantilla aprobada en Meta para avisar al ENTREVISTADOR de una Entrevista Humana asignada.
    # 6 variables posicionales: {{1}} entrevistador, {{2}} candidato, {{3}} vacante, {{4}} fecha, {{5}} hora,
    # {{6}} liga al expediente. Si Meta la rechaza (no aprobada, nombre distinto) sale texto libre.
    meta_plantilla_entrevista: str = "alerta_entrevista_asignada"

    # --- Gateway propio (alternativa sin costo por mensaje) ---
    waha_url: str = "http://localhost:3001"
    waha_api_key: str = ""
    waha_session: str = "default"
    evolution_url: str = "http://localhost:8080"
    evolution_api_key: str = ""
    evolution_instance: str = "redhuman"

    # --- Correo (Resend) ---
    # Vacío = modo demo (el envío se registra en bitácora como "no enviado"). El remitente
    # de sandbox (onboarding@resend.dev) solo entrega al correo con el que se creó la cuenta;
    # al verificar un dominio propio en Resend basta con cambiar RESEND_FROM, sin tocar código.
    resend_api_key: str = ""
    # 2026-09-18: el remitente SIEMPRE es del dominio redhuman.mx (correo.remitente() lo garantiza aunque
    # el .env traiga otro). El dominio debe estar verificado en Resend.
    resend_from: str = "Red Human AI <notificaciones@redhuman.mx>"

    # --- Microsoft Teams / Microsoft 365 (Fase 7B) ---
    # Nombres EXACTOS de las variables: TEAMS_CLIENT_ID, TEAMS_TENANT_ID, TEAMS_CLIENT_SECRET.
    # Vacías = integración no disponible (la videollamada pide la liga a mano, como siempre).
    # TEAMS_REDIRECT_URI es opcional: sin ella se usa el host real de la API + /integraciones/teams/callback.
    teams_client_id: str = ""
    teams_tenant_id: str = ""
    teams_client_secret: str = ""
    teams_redirect_uri: str = ""

    # --- Dropbox Sign: firma electrónica incrustada (2026-09-29) ---
    # Nombres EXACTOS: DROPBOX_SIGN_API_KEY, DROPBOX_SIGN_CLIENT_ID (API app con dominio y marca blanca configurados
    # en Dropbox Sign). Vacías = firma electrónica no disponible: se sigue generando el PDF y se carga el firmado a mano.
    # DROPBOX_SIGN_TEST_MODE=true manda las solicitudes en modo prueba (sin validez legal, no consume cuota).
    dropbox_sign_api_key: str = ""
    dropbox_sign_client_id: str = ""
    dropbox_sign_test_mode: bool = False

    # --- Psicométricas.mx (2026-09-29) --- https://admin.psicometricas.mx/api/
    # La API autentica con `Token` + `Password` (20 caracteres cada uno). Nombres: PSICOMETRICAS_TOKEN y
    # PSICOMETRICAS_PASSWORD; por compatibilidad con lo ya inyectado en producción, si falta PSICOMETRICAS_PASSWORD
    # se usa PSICOMETRICAS_USUARIO como `Password`. Vacías = modo Integrada simulado (a mano).
    psicometricas_token: str = ""
    psicometricas_password: str = ""
    psicometricas_usuario: str = ""
    psicometricas_base_url: str = "https://admin.psicometricas.mx/api"
    # Su webhook NO trae firma: se protege con un secreto propio en la URL registrada en Psicométricas
    # (…/api/webhooks/psicometricas?secreto=XXXX) y además cada aviso se CONFIRMA consultando su API.
    psicometricas_webhook_secret: str = ""
    # La API no devuelve la liga del candidato (Psicométricas se la manda por correo con su clave). Si se conoce la
    # liga de acceso, se puede configurar con {clave}, p. ej. https://…/{clave}; vacío = solo se muestra la clave.
    psicometricas_url_candidato: str = ""

    cors_origins: str = "http://localhost:3000"


settings = Settings()
