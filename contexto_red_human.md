# Red Human IA — Contexto técnico completo del repositorio

> Generado el 2026-09-13 a partir del código real, `git log`, `CLAUDE.md` y `CONTEXTO_SESION.md`.
> Propósito: dar a otra IA el contexto necesario para resolver bugs recientes. La sección 7
> (**Historial de fases y últimos cambios**) es la crítica: lista exactamente qué se tocó en los
> últimos 8 commits (2026-09-11 → 2026-09-12) y qué contratos cambiaron.

Rama de trabajo: `red-human-v2.0` (el script de despliegue usa `origin/main` del repo oficial
`christophercarbajal-cpu/dearrollo_rh`). Último commit: `ff61558` (2026-09-12). Working tree limpio al generar esto.

---

## 1. Árbol de directorios principal

```
redhuman-v2/
├── CLAUDE.md                     # Reglas del proyecto para agentes (fuente de verdad de convenciones)
├── CONTEXTO_SESION.md            # Bitácora larga de cada fase: decisiones, verificación, pendientes
├── contexto_proyecto.md          # Contexto de negocio original
├── README.md / CONTRIBUTING.md
├── .github/workflows/ci.yml      # Python 3.12 + scripts/verificar_dependencias.py
├── scripts/
│   ├── verificar_dependencias.py
│   └── servidor/{redesplegar.sh, revertir.sh, verificar.sh}   # deploy en VPS (/opt/redhuman)
│
├── red-human-api/                # BACKEND — FastAPI + SQLAlchemy 2.0 (SQLite)
│   ├── .env / .env.example       # TODAS las claves (nunca en código ni en el cliente)
│   ├── requirements.txt
│   ├── README.md                 # incluye pasos de Azure (Fase 7B) y producción
│   ├── app/
│   │   ├── main.py               # lifespan: create_all + migraciones.sincronizar() + seed + guard Fase 2
│   │   ├── config.py             # pydantic-settings (env → settings.<minúsculas>)
│   │   ├── database.py           # engine, SessionLocal, Base
│   │   ├── deps.py               # usuario_actual / usuario_decisor / usuario_admin / cuenta_actual (X-Cuenta-Id)
│   │   ├── models.py             # 29 tablas (ver §3) + constantes de negocio
│   │   ├── migraciones.py        # sincronizar(engine): ALTER TABLE para columnas nuevas; migrar_postulaciones
│   │   ├── serial.py             # serializadores a camelCase (vacante_dict, postulacion_dict, candidato_dict…)
│   │   ├── seed.py               # datos demo + sembrar_admin
│   │   ├── routers/              # 20 routers, 145 rutas (ver §5)
│   │   └── services/
│   │       ├── ia.py             # OpenAI: generador de vacantes, prefiltro WhatsApp, CV, Entrevista IA, evaluación, agenda, onboarding
│   │       ├── whatsapp.py       # Meta Cloud API (envío, plantillas, parseo de webhook, listas interactivas)
│   │       ├── notificaciones.py # Fase D: disparar(evento, postulación) → correo/WhatsApp a candidato/entrevistador/cliente
│   │       ├── correo.py         # Resend
│   │       ├── avatar.py         # Anam (avatar de video de la Entrevista IA)
│   │       ├── teams.py          # Fase 7B: OAuth Microsoft 365 + Graph (reunión de Teams + calendario)
│   │       ├── entrevistas.py    # guion de Entrevista IA
│   │       ├── agenda.py         # Zero-Touch: videollamada mock, no-show (APScheduler)
│   │       ├── agente.py         # Fase F: agente "Pregunta a Red Human" (tools sobre routers)
│   │       ├── archivos.py, auth.py, configuracion.py
│   └── scripts/
│       ├── migrar_cuentas.py / migrar_postulaciones.py / migrar_entrevistas_humanas.py  # migraciones de DATOS (manuales)
│       ├── sembrar_reglas_notificacion.py / backfill_resultado_apto.py / seed_demo_candidatos.py / borrar_demo_candidatos.py
│       ├── verificar_fase2.py (67) · verificar_config_admin.py (53) · verificar_entrevista_ia.py (52)
│       ├── verificar_formulario_vacante.py (40) · verificar_entrevista_humana.py (28) · verificar_teams.py (41)
│       └── spike_anam_tools.py / .html   # spike pendiente (client tools de Anam)
│
└── red-human-app/                # FRONTEND — Next.js 15 (App Router) + React 19 + Tailwind + TypeScript
    ├── .env.local (NEXT_PUBLIC_API_URL)
    ├── middleware.ts             # protege /dashboard (cookie de sesión)
    ├── app/
    │   ├── page.tsx (landing) · login/ · portal/ (bolsa pública) · aplicar/[slug]/ (postulación web)
    │   ├── entrevista/[token]/           # sala pública de Entrevista IA (avatar Anam o texto)
    │   ├── entrevista-humana/[token]/    # liga pública del entrevistador (evaluación)
    │   ├── expediente/[token]/           # liga pública de documentos
    │   ├── capacitacion/[token]/
    │   └── dashboard/ {layout, page, candidatos, vacantes, entrevistas, configuracion, onboarding,
    │                   requisiciones, colaboradores, capacitacion, conocimiento, clima, desempeno}
    ├── components/
    │   ├── ui.tsx · sesion.tsx · theme-toggle.tsx · cambiar-password.tsx
    │   └── dashboard/ {shell, parts, campos, charts, subida, por-cuenta, linea-notificar,
    │                   confirmacion-accion, perfil-profundo, vacantes/formulario-contenido, agente/*}
    └── lib/ {api.ts (cliente HTTP + tipos), data.ts (tipos de dominio + mocks), phase2.ts, utils.ts}
```

---

## 2. Stack tecnológico

| Capa | Tecnología | Notas |
|---|---|---|
| Backend | Python 3.12, FastAPI ≥0.115, SQLAlchemy 2.0 (Mapped/mapped_column), Pydantic v2, pydantic-settings, httpx, APScheduler, pypdf, WeasyPrint (carta de intención PDF), **cryptography** (nuevo en 7B) | Corre con `uvicorn app.main:app`. Sin Alembic. |
| Base de datos | SQLite (`DATABASE_URL`, default `sqlite:///./redhuman.db`; README recomienda Postgres en prod) | Esquema: `Base.metadata.create_all()` para tablas nuevas + `migraciones.sincronizar(engine)` agrega columnas faltantes vía `ALTER TABLE` al arrancar. Migraciones de **datos** siempre por script manual con confirmación. **SQLite pierde el tzinfo**: toda fecha se normaliza a UTC antes de guardar y se le repone `tzinfo=utc` al leer. |
| Frontend | Next.js 15.3 (App Router), React 19, TypeScript, Tailwind, lucide-react, recharts, framer-motion, three (landing), `@anam-ai/js-sdk` 4.23 | `lib/api.ts` = único cliente HTTP (`get/post/patch/eliminar`), inyecta `X-Cuenta-Id` desde `localStorage("rh-cuenta-id")`. Respuestas `{ok, data}` / `{ok:false, error}`. |
| IA | OpenAI (`OPENAI_API_KEY`, modelo `OPENAI_MODEL`, hoy `gpt-5.6-luna`) vía `client.responses.parse` con salida estructurada Pydantic | Sin clave = **modo demo determinista** en todos los servicios (`_client() is None`). |
| Avatar | Anam (`ANAM_API_KEY`, `ANAM_AVATAR_ID`, `ANAM_LLM_ID`, `ANAM_VOICE_ID`) | El LLM registrado en Anam es nuestro endpoint de OpenAI; `avatar_activo()` exige las 3 primeras. Sin clave → entrevista en modo texto. |
| WhatsApp | Meta Cloud API (`WHATSAPP_PROVIDER=meta`, `META_WHATSAPP_TOKEN`, `META_PHONE_NUMBER_ID`, `META_WABA_ID`, `META_VERIFY_TOKEN`, `META_APP_SECRET`, `META_PLANTILLA_AVISO`) | Plantilla de inicio `inicio_entrevista_rh`. Teléfonos a 10 dígitos en base; `52`+10 hacia Meta. |
| Correo | Resend (`RESEND_API_KEY`, `RESEND_FROM`) | Sin clave = «no enviado» silencioso registrado en `notificaciones_enviadas`. El remitente sandbox `onboarding@resend.dev` solo entrega al dueño de la cuenta Resend. |
| Microsoft 365 | Graph API con OAuth delegado (`TEAMS_CLIENT_ID`, `TEAMS_TENANT_ID`, `TEAMS_CLIENT_SECRET`, opcional `TEAMS_REDIRECT_URI`) | Solo configurado en producción. |
| Sesión | Cookie httpOnly (`services/auth.py`: hash de token en tabla `sesiones`), roles `Administrador` \| `Usuario` | `middleware.ts` en el front. |
| Legal | LFPDPPP: la IA solo recomienda; avanzar/descartar/alta lo firma una persona de RH en `bitacora` (hash-encadenada). Consentimiento explícito por postulación. |

---

## 3. Modelos de base de datos y relaciones (`app/models.py`)

### Multi-cuenta (Fase A / Puntos 9-13)
- **Cuenta** (`cuentas`): empresa reclutadora. `nombre` (interno), `nombre_comercial` (lo ve el candidato), `razon_social`, `logo`, `correo_comunicacion`, `whatsapp_comunicacion` (**usado por el webhook para enrutar por número**), `estado` Activa/Inactiva. `nombre_visible` = `nombre or nombre_comercial`.
- **Cliente** (`clientes`): empresa para la que recluta una Cuenta (`cuenta_id`). `nombre`, `nombre_comercial`, `razon_social`, `estado`. `nombre_visible`.
- **ClienteContacto** (`cliente_contactos`): `cliente_id`, `nombre`, `apellidos`, `puesto`, `correo`, `telefono`. No son usuarios. Destinatarios de "Notificar al Cliente" y **entrevistadores externos** (7A).
- **Usuario** (`usuarios`): `correo`, `nombre`, `puesto`, **`telefono`** (WhatsApp, capturado desde 7A), `rol`, `activo`, `hash_pass`, `debe_cambiar_pass`, `ve_equipo`, `reporta_a_id`. **UsuarioCuenta** (`usuario_cuentas`) = puente N:N Usuario↔Cuenta. Un admin solo ve las Cuentas vinculadas a él.
- **Sesion**, **UsoAgente** (tope diario del agente), **Bitacora** (append-only, `hash`/`hash_prev`, `cuenta_id`), **ConfiguracionSistema** (fila única: `modo_prueba`, `modo_prueba_ventana_min`).
- **IntegracionTeams** (`integraciones_teams`, 7B): una fila por `cuenta_id` (unique): `usuario_m365`, `nombre_m365`, `access_token_cifrado`, `refresh_token_cifrado` (Fernet), `expira_en`, `scopes`, `conectado_por/en`, `ultimo_error`.

### Reclutamiento
- **Vacante** (`vacantes`): `codigo` VAC-####, `slug`, `titulo`, `area`, `empresa` (**texto DERIVADO por la regla Cliente-visible/Cuenta, nunca libre**), `ubicacion`, `modalidad`, `sueldo` (**texto derivado**), `sueldo_desde/hasta/moneda/periodicidad` (Parte 3), `estado` Borrador|Publicada|En revisión|Cerrada, `requisitos` (texto; indispensables unidos por « · »), `descripcion`, `resumen`, `perfil_ideal`, `responsabilidades[]`, `requisitos_deseables[]`, `beneficios[]`, `palabras_clave[]`, `seniority`, `avisos_cumplimiento[]`, `preguntas_filtro[]` (dicts `PreguntaFiltro`: pregunta/tipo/valida/respuesta_esperada/descarta/opciones), `publicaciones{}` (whatsapp/occ/linkedin/portal), `plataformas[]`, `enfoque_entrevista` (profesional|profesional_personal), `publicada_en`, FKs `cuenta_id`, `cliente_id`, `responsable_id`, `colaboradores_ids[]`, `mostrar_cliente_candidato`, `plantilla_id`, `requisicion_id`.
- **Plantilla** (`plantillas`): mismos campos reutilizables que Vacante (`CAMPOS_PLANTILLA`, 23 campos) + `nombre`, `cuenta_id`, `cliente_id` (General o de un Cliente), `activa`.
- **Requisicion** → se convierte en Vacante (`convertir_vacante`). **SugerenciaMovilidad**, **Empleado** (radar interno).
- **Candidato** (`candidatos`) = **PERSONA** (Fase 2): `codigo` C-####, `nombre`, `telefono`, `correo`, `wa_id`, `wa_nombre`, `fuente`, `es_prueba`, `cuenta_id`, `cv_datos`, `postulacion_conversacion_id` (puntero de la conversación de WhatsApp, lo mueve SOLO el candidato). Sus columnas de proceso (`etapa`, `score`…) son **LEGADO** solo para la migración; ningún endpoint las escribe.
- **Postulacion** (`postulaciones`) = **PROCESO** (una tarjeta del Kanban): `codigo` P-####, `candidato_id`, `vacante_id` (nullable), `cuenta_id`, `etapa` (Prefiltro → **"Entrevista IA"** [valor interno; en UI se muestra «Entrevista Red Human»] → Evaluación → Entrevista Humana → Contratación → Onboarding), `estado` pendiente|revision|cumple|no_cumple, `score`, `evidencia`, `analisis{}` (respuestas_prefiltro, brechas…), `consentimiento`, `prefiltro_completo`, `videollamada_agendada_en`, `resultado_apto`, `ultima_actividad_en`, `activa`, `motivo_cierre`, `origen`, `espera_respuesta` (propiedad). Relaciones: `mensajes`, `entrevistas`, `entrevistas_humanas`, `expediente`. Propiedades proxy a la persona: `nombre`, `correo`, `telefono`.
- **Mensaje** (`mensajes`): chat de WhatsApp/prefiltro por postulación (`candidato_id`, `postulacion_id`, `rol`, `texto`, `canal`, `enviado`, `wa_id`).
- **Archivo** (CV y adjuntos de la persona), **Entrevista** (`entrevistas`, Entrevista IA: `token`, `tipo` avatar|texto, `estado` programada|en_curso|completada|evaluada|interrumpida, `guion{temas,preguntas,enfoque}`, `transcript[]`, `evaluacion{}` (+`perfil` 10 dimensiones), `cierre`, `iniciada_en`, `finalizada_en`, `intentos_previos[]`, `consentimiento`).
- **EntrevistaHumana** (`entrevistas_humanas`, una fila por ronda): `postulacion_id`, `tipo` interno|externo, `usuario_id` (interno), `correo_externo`, `whatsapp_externo`, **`contacto_id`** (7A), `entrevistador` (nombre), `fecha` (UTC), `modalidad` Presencial|Videollamada|Llamada, `liga`, **`teams_evento_id`** (7B), `ubicacion`, `telefono_contacto`, `comentario`, `realizada`, `cancelada`, `resultado`, `recomendacion`, `token` (liga pública del entrevistador).
- **Expediente** / **Documento** (contratación; expediente por postulación con `token` público), **Colaborador** (alta), **Curso/ModuloCurso/AsignacionCurso** (capacitación).
- **ReglaNotificacion** (`reglas_notificacion`): por `(cuenta_id, evento)` 6 booleanos `candidato_correo/whatsapp`, `entrevistador_*`, `cliente_*`. **NotificacionEnviada**: bitácora operativa de cada envío (`destinatario_tipo`, `canal`, `destino`, `enviado`, `detalle`).

### Constantes de negocio en `models.py`
`ETAPAS_CANDIDATO`, `ESTADOS_ENTREVISTA`, `CIERRES_ENTREVISTA`/`CIERRES_COMPLETOS`, `ENFOQUES_ENTREVISTA`, `CAMPOS_PLANTILLA`, `PERIODICIDADES_SUELDO`, `MONEDAS_SUELDO`, `texto_sueldo()`, `EVENTOS_NOTIFICACION` (10), **`REGLAS_NOTIFICACION_DEFAULT`** (7A), `PLATAFORMAS`, `ROLES`, `DOCUMENTOS_BASE`, `registrar()` (bitácora), `slugificar()`, `ahora()`.

---

## 4. Flujos de trabajo y lógica de negocio

### 4.1 Creación de vacante (Parte 3, `formulario-contenido.tsx` + `routers/vacantes.py`)
Orden fijo: Datos principales (Puesto, Área, Seniority [6 niveles `ia.SENIORITY`], Cliente [obligatorio si la Cuenta tiene Clientes: un Cliente o «recluta directo» = `0`], Ubicación, Modalidad, Sueldo estructurado) → Guía opcional (descripción breve, indispensables, deseables, prestaciones) → botón «Generar vacante con Red Human» (`POST /vacantes/generar` con `GenerarIn` = ficha completa; `empresa` la resuelve el servidor) → contenido editable → Prefiltro (criterios con `descarta`) → Entrevista Red Human (enfoque) → Gestión (responsable, colaboradores) → Publicación (canales; `POST /vacantes` con `publicar`). Reglas duras en `ia._asegurar_capturado`: no inventar condiciones (beneficios = capturados; sueldo nunca sugerido) y respetar lo capturado (indispensables/deseables literal y en su categoría). `Vacante.sueldo` se deriva con `texto_sueldo`.

### 4.2 Captación
- Web: `/aplicar/[slug]` → `POST /candidatos/postular` (form-data; consentimiento aceptado en la web) → `postulacion_para_vacante` → plantilla Meta `inicio_entrevista_rh` (`_disparar_plantilla_inicio`, solo postulaciones nuevas).
- WhatsApp: `POST /webhooks/whatsapp` → `parsear_webhook` → `_cuenta_whatsapp` (ruteo por número receptor ↔ `Cuenta.whatsapp_comunicacion`; fallback Cuenta más antigua) → `_buscar_o_crear_candidato` (wa_id OR teléfono, más reciente) → `_resolver_postulacion` (puntero de conversación / única que espera / lista interactiva / nueva) → menú de vacantes → aviso de privacidad + «Sí» (`_es_aceptacion`, palabra completa) → `procesar_prefiltro` (IA `ia.prefiltro_turno`, salida `TurnoPrefiltro`) → clasificación → `_auto_decision_zero_touch` (apto → etapa Entrevista IA + agenda de videollamada mock por `ia.agenda_turno` con function calling).
- CV: `POST /candidatos/cv` (masivo) / `POST /candidatos/{codigo}/archivos` → `ia.extraer_cv` → `_aplicar_cv` (merge de `analisis`, nunca pisa `respuestas_prefiltro`).

### 4.3 Entrevista IA (Fase 4 + Parte 2)
`POST /entrevistas` o `/entrevistas/inmediata` genera guion (`services/entrevistas.py` → `ia.guion_entrevista(enfoque)`), token y liga `/entrevista/[token]`. Sala pública: `GET /entrevistas/publica/{token}` → consentimiento → `POST /sesion` (Anam con `persona_config("Red Human", prompt, saludo)` o modo texto) → `POST /turno` (texto) → `POST /finalizar` `{transcript, cierre}` con verificación server-side de la despedida `ia.DESPEDIDA_ENTREVISTA` (`_cierre_verificado`; desconexión con <2 turnos → `interrumpida`) → `ia.evaluar_entrevista` (+`PerfilProfundo`) → postulación a Evaluación. `POST /entrevistas/{codigo}/reabrir` archiva el intento. Introducción exacta: «Hola, soy Red Human. Gracias por participar en el proceso para {vacante}. … ¿Comenzamos?» (`ia.mensaje_inicial_entrevista(titulo)`); silencio `ia.AVISO_SILENCIO`; nunca "Alma"/"asistente virtual".

### 4.4 Entrevista Humana (Fase D + 7A + 7B)
`POST /candidatos/{codigo}/entrevista-humana` (`EntrevistaHumanaIn`): entrevistador interno (`entrevistador_usuario_id` → correo y WhatsApp del perfil `Usuario`), externo por contacto (`entrevistador_contacto_id`, validado contra el Cliente de la vacante) u «Otro» (nombre/correo/WhatsApp manual). Modalidad Videollamada: **si la Cuenta tiene Teams conectado y `usar_teams` (default True) y no llega `liga` → `teams.crear_reunion` ANTES de guardar (falla → 502, no se guarda nada)**; si no → `liga` manual obligatoria. Luego `notificaciones.disparar("entrevista_agendada")` → correo/WhatsApp a candidato, entrevistador y contactos del Cliente (`cliente_contactos_ids`). **Respuesta: `{resultados, candidato}`** (antes solo el candidato). Modificar/Cancelar/Realizada/Resultado/Recordatorio en el mismo router; el entrevistador evalúa por liga pública `/entrevista-humana/[token]` (`routers/entrevista_humana.py`).

### 4.5 Notificaciones (Fase D)
`services/notificaciones.disparar(db, evento, postulacion, actor, eh=, liga=, extra=, override=)`: regla guardada de la Cuenta (`ReglaNotificacion`) + `override` de la acción (`NotificarIn` desde la línea «Notificar · Editar», `LineaNotificar`/`ConfirmacionAccion`). Textos en `_mensaje(evento, audiencia, canal)`. Cada envío regresa `{destinatario, canal, destino, enviado, proveedor, detalle}` y se registra en `notificaciones_enviadas`. Las reglas NACEN con `REGLAS_NOTIFICACION_DEFAULT` (siembra perezosa en `GET /notificaciones/reglas`); nunca se tocan solas después.

### 4.6 Contratación / Onboarding
Etapa Contratación crea `Expediente` (documentos por liga pública `/expediente/[token]`, checklist, validación IA, carta de intención PDF) → `POST /contratacion/expedientes/{id}/alta` crea `Colaborador` y cierra la postulación como `contratado`. Onboarding: la IA acompaña documentos por WhatsApp (`_procesar_turno_onboarding`).

### 4.7 Agente "Pregunta a Red Human" (Fase F)
`POST /agente/preguntar` ejecuta tools de LECTURA en proceso (wrappers sobre funciones de routers en `services/agente.py`); las de ESCRITURA solo se ejecutan con confirmación humana vía `POST /agente/ejecutar`. Los wrappers construyen los `*In` de los routers directamente (p. ej. `r_vacantes.CrearIn`, `r_candidatos.EntrevistaHumanaIn`) → **cualquier cambio de firma en un router puede romper una tool**.

### 4.8 Reglas transversales
- `cuenta_actual` filtra TODO por Cuenta (cabecera `X-Cuenta-Id` cuando el usuario tiene varias). El webhook público no tiene sesión.
- Modo Prueba (`ConfiguracionSistema.modo_prueba`): cada postulación web de prueba crea persona nueva; conversación fría (> ventana) se cierra; `POST /candidatos/{codigo}/reiniciar`.
- La API **se niega a arrancar** si hay candidatos sin postulación (guard de Fase 2 en `main.py`).

---

## 5. Endpoints principales (145 rutas en 20 routers)

| Prefijo | Rutas clave |
|---|---|
| `/auth` | `POST /login`, `/logout`, `GET /yo`, **`GET /entrevistadores`** (ahora `{id,nombre,correo,telefono}`), `GET/POST /usuarios`, `PATCH /usuarios/{id}` (acepta `telefono`), `POST /cambiar-password` |
| `/cuentas` | `GET`, `POST`, `GET/PATCH /actual`, `POST /actual/logo`, `GET/PATCH /{id}`, `POST /{id}/usuarios` (acepta `telefono`), `DELETE /{id}/usuarios/{uid}`, `GET /{id}/clientes` |
| `/clientes` | CRUD + `POST/PATCH/DELETE /{id}/contactos[/{cid}]`; `GET /{id}` trae `listaContactos` |
| `/plantillas` | CRUD, `POST /desde-vacante/{codigo}`, `POST /{id}/duplicar` (copian `CAMPOS_PLANTILLA`) |
| `/vacantes` | `GET`, **`POST /generar`** (`GenerarIn` = ficha completa, regresa `{ia, empresa, sueldo_texto, …VacanteGenerada}`), **`POST`** (`CrearIn`, `publicar`), `GET /publicas`, `GET /slug/{slug}`, `GET /{codigo}`, `GET /{codigo}/vista-previa`, `PATCH /{codigo}`, `POST /{codigo}/regenerar`, `/publicar`, `/cerrar`, `GET /{codigo}/publicacion/{plataforma}` |
| `/candidatos` | `GET` (Kanban: solo activas salvo `mostrar_cerradas`), `GET /{codigo}` (P-####; C-#### aceptado), `POST` (ingresar), `POST /cv`, **`POST /postular`** (público, form-data), archivos, `POST /{codigo}/asignar`, `/reiniciar`, `GET /{codigo}/mensajes`, `POST /{codigo}/prefiltro`, `/consentimiento`, `/decision` (descartar), `PATCH /{codigo}/etapa`, **`POST /{codigo}/entrevista-humana`** (→ `{resultados, candidato}`), `PATCH …/entrevista-humana` (→ dict + `avisoTeams`), `POST …/cancelar` (→ dict + `avisoTeams`), `/realizada`, `/resultado`, `/recordatorio`, `GET /{codigo}/expediente`, `PATCH /condiciones-contratacion`, `POST /solicitar-documentos`, `/recordatorio-documentos`, `POST /prueba/eliminar` |
| `/entrevistas` | `GET`, `GET /metricas`, `POST` (agendar), `POST /inmediata`, **públicos** `GET /publica/{token}`, `POST /publica/{token}/consentimiento`, `/sesion`, `/turno`, `/finalizar`; `POST /{codigo}/reabrir` |
| `/entrevista-humana` | 2 rutas públicas por token (evaluación del entrevistador) |
| `/contratacion` | expedientes, documentos, `POST /expedientes/{id}/alta`, `/recordatorio`, `/cancelar`, `GET …/carta-intencion` (PDF) |
| `/expedientes` | 2 públicas por token (subida de documentos) |
| `/notificaciones` | `GET /reglas` (siembra perezosa con defaults), `PATCH /reglas/{evento}`, `PUT /reglas`, `GET /historial` |
| **`/integraciones`** (7B) | `GET /teams`, `POST /teams/conectar`, **`GET /teams/callback`** (público, llega desde Microsoft), `POST /teams/probar`, `DELETE /teams` |
| `/webhooks` | **`POST /webhooks/whatsapp`** (público), `GET /webhooks/whatsapp` (handshake), `GET /bitacora` |
| `/agente` | `POST /preguntar`, `POST /ejecutar`, `GET /uso` |
| `/configuracion` | `GET`, `PATCH` (modo prueba) |
| otros | `/requisiciones` (10), `/empleados` (6), `/capacitacion` (12), `/colaboradores` (1), `/metricas/pipeline`, `GET /salud` (main) |

---

## 6. Convenciones importantes para depurar
- **Verificación**: cada fase tiene un script `scripts/verificar_*.py` (TestClient, base SQLite desechable, `OPENAI_API_KEY=""`, `WHATSAPP_PROVIDER=""`, `ANAM_API_KEY=""`). Correrlos así desde `red-human-api/`: `OPENAI_API_KEY="" WHATSAPP_PROVIDER="" ANAM_API_KEY="" PYTHONPATH=. .venv/Scripts/python.exe scripts/verificar_fase2.py`. El `.env` local tiene una clave real de OpenAI: si no se vacía, los scripts gastan llamadas reales.
- **Modo demo** en todos los servicios cuando falta la clave: `ia._client() is None`, `whatsapp.enviar_mensaje` → `{enviado:false, proveedor:"demo"}`, `correo.enviar_correo` → «RESEND_API_KEY sin configurar», `avatar` → None (texto), `teams.teams_configurado()` False.
- Los fallos de proveedores externos **nunca lanzan**: se registran en `notificaciones_enviadas.detalle`, `Mensaje.enviado`, `integraciones_teams.ultimo_error` y en la bitácora. Para saber por qué "no llegó" un mensaje: `GET /notificaciones/historial` o la pestaña WhatsApp del candidato.
- Frontend: los tipos de `lib/api.ts` y `lib/data.ts` deben coincidir con los serializadores de `serial.py`; `next build` corre `tsc`.
- El **token de Meta del `.env` local caducó el 25-Ago-2026** (Graph error 190); producción usa otro. El README exige token de System User sin caducidad.

---

## 7. Historial de fases y ÚLTIMOS CAMBIOS (CRÍTICO)

### 7.1 Línea de tiempo (commits, del más antiguo al más reciente)
| Commit | Fase | Resumen |
|---|---|---|
| `74d71ce` | Fix seguridad | `POST /auth/usuarios` recupera `usuario_admin` + bitácora |
| `036f2c3`, `816fe05` | **Fase A** | Multi-cuenta: `Cuenta`, `Cliente`, `ClienteContacto`, `UsuarioCuenta`, `cuenta_id` en todo, `deps.cuenta_actual`, `migrar_cuentas.py` |
| `ea4353b` | **Fase B** | Vacante con Cliente/Responsable/Colaboradores, `Plantilla`, vista previa, `nombre_empresa_candidato` |
| `6b87ba3`, `e871aaa`, `e879b0f` | **Fase C** | Filtros, vistas lista, `resultado_apto`, `ultima_actividad_en`, `publicada_en` |
| `20d5374` | **Fase D** | Notificaciones configurables (`ReglaNotificacion`, `NotificacionEnviada`, `services/notificaciones.py`), 10 eventos |
| `33f1b4a`, `5e176a5` | Puntos 2/27/28 + **Fase F** | Selector de Cuenta (`X-Cuenta-Id`), Configuración en secciones, agente «Pregunta a Red Human» |
| `9440dd2` | 6 correcciones | Botón Publicar, CV con reintento, pestaña Resumen con afinidad (`serial._sintesis_global`) |
| `24acfd7` | **Fase 2** | **Separación Candidato (persona) / Postulacion (proceso)**; webhook reescrito (ruteo por contexto); consentimiento siempre; `migrar_postulaciones.py`; la API no arranca sin migrar |
| `47a4a8b` | Puntos 9-13 | 5 pantallas de Configuración: Cuentas (crear vinculando), Clientes+contactos, Plantillas (formulario único `formulario-contenido.tsx`, `CAMPOS_PLANTILLA`), Notificaciones con `override` por acción (`NotificarIn`, `LineaNotificar`), Modo Prueba configurable |
| `8b45d18` | **Fase 4** (2026-09-11) | Entrevista IA: empresa/candidato, prompt con protocolo, cierre verificable, `PerfilProfundo`, `enfoque_entrevista`; **hotfix**: `entrevistas.py`/`contratacion.py` leían `c.vacante` (500 en la liga pública desde Fase 2) |
| `785516f` | **Hotfix WhatsApp** (2026-09-11) | Webhook: `_cuenta_unica` (500 con ≥2 Cuentas activas) → `_cuenta_whatsapp` (ruteo por número); persona más reciente por `wa_id OR teléfono` |
| `4dd3031` | **Parte 2** (2026-09-11) | Introducción «Hola, soy Red Human…», eliminación total de "Alma", protocolo inicio/silencio |
| `c1f3cd3` | **Parte 3** (2026-09-12) | **Rediseño del formulario de vacante**, sueldo estructurado, reglas no-inventar/respetar, «Entrevista Red Human» visual, fix `convertir_vacante` |
| `c67f2d6` | **Fase 7A** (2026-09-12) | Entrevistador interno/externo, contactos del Cliente seleccionables, corrección del correo (defaults + resultado visible) |
| `ff61558` | **Fase 7B** (2026-09-12) | Microsoft Teams: OAuth por Cuenta, tokens cifrados, reunión + invitaciones automáticas |

Entre `47a4a8b` y `ff61558`: **51 archivos, +4489/−603 líneas**. Todo lo de abajo está desplegado en producción (según `CONTEXTO_SESION.md`), salvo la validación real de Teams que depende de credenciales que solo existen en el servidor.

### 7.2 Detalle de cada cambio reciente (qué se tocó y qué contrato cambió)

#### A. Fase 4 — Entrevista IA (`8b45d18`)
Archivos: `models.py`, `serial.py`, `services/ia.py` (bloque de entrevista reescrito), `services/entrevistas.py`, `services/avatar.py`, `routers/entrevistas.py` (sección pública reescrita), `routers/vacantes.py`, `routers/plantillas.py`, `routers/candidatos.py`, `routers/contratacion.py`, frontend `entrevista/[token]/page.tsx`, `dashboard/entrevistas/page.tsx`, `dashboard/vacantes/page.tsx`, `dashboard/candidatos/page.tsx`, `formulario-contenido.tsx`, nuevo `components/dashboard/perfil-profundo.tsx`, `lib/api.ts`, `lib/data.ts`.
- **Esquema**: `vacantes.enfoque_entrevista`, `plantillas.enfoque_entrevista`, `entrevistas.cierre/iniciada_en/finalizada_en/intentos_previos`; estado nuevo `interrumpida`.
- **Contratos cambiados**:
  - `POST /vacantes/generar`: el campo `empresa` (texto libre) **se ignora**; se resuelve con `cliente_id` + `mostrar_cliente_candidato` (`serial.nombre_empresa`). `routers/vacantes._generar(datos, empresa)` pasó a 2 argumentos (esto rompió `requisiciones.convertir_vacante`, corregido en Parte 3).
  - `Vacante.empresa` se fija SIEMPRE con la regla en `crear`/`actualizar` (en update: `flush + refresh` antes).
  - `POST /entrevistas/publica/{token}/sesion` → 403 sin consentimiento, 409 si cerrada; devuelve `nombre`. `POST /turno` → 403 si no `en_curso`. `POST /finalizar` recibe `{transcript, cierre}`; 409 si no `en_curso`; idempotente. Nuevo `POST /entrevistas/{codigo}/reabrir`.
  - `avatar.avatar_activo()` ahora **exige `ANAM_LLM_ID`** además de `ANAM_API_KEY` y `ANAM_AVATAR_ID` (si falta, la sala cae a modo texto). `persona_config(nombre, prompt, inicial, extras)`.
  - `candidatos.nombre_ficha(p)` reemplaza `p.wa_nombre or p.nombre.split(" ")[0]` en prefiltro/agenda/onboarding.
  - `ia.guion_entrevista(titulo, requisitos, resumen, enfoque_entrevista=, perfil_ideal=, responsabilidades=)`, `ia.prompt_entrevistador(...)` keyword-only, `ia.evaluar_entrevista(..., perfil_ideal=, temas=, enfoque_entrevista=)` — **firmas nuevas**.
  - Frontend: `Entrevista.estado` incluye `interrumpida`; `finalizarEntrevista(token, transcript, cierre)`; `DatosVacante.empresa` deprecado; el campo «Empresa» desapareció de Nueva vacante.
- **Hotfix**: `routers/entrevistas.py::_contexto(e)` obtiene `(postulacion, vacante, empresa)` desde `e.postulacion` (antes `e.candidato.vacante` → AttributeError/500). `contratacion._html_carta_intencion` usa `e.postulacion.vacante`.

#### B. Hotfix WhatsApp (`785516f`)
Archivos: `routers/webhooks.py`, `services/whatsapp.py`, `scripts/verificar_fase2.py`.
- `services/whatsapp.parsear_webhook` agrega `numero_receptor` (`metadata.display_phone_number`).
- `webhooks._cuenta_unica` (lanzaba **500 si había ≠ 1 Cuenta activa**, es decir, en cuanto se creó una segunda Cuenta desde Configuración → Cuentas todo el WhatsApp entrante moría) → **`_cuenta_whatsapp(db, numero_receptor)`**: Cuenta cuyo `whatsapp_comunicacion` (normalizado a 10 dígitos) coincide; si ninguna coincide y hay una activa → esa; varias → la más antigua con aviso `[webhook] ⚠️` en log.
- `_buscar_o_crear_candidato`: una sola consulta `wa_id == X OR telefono == Y`, `order_by id desc` (antes buscaba primero por `wa_id` y en Modo Prueba caía en la persona de la prueba anterior).
- Causa raíz secundaria diagnosticada (no corregida por código): token de Meta caducado en `.env` local.

#### C. Parte 2 — Introducción (`4dd3031`)
Archivos: `services/ia.py`, `routers/entrevistas.py`, `scripts/seed_demo_candidatos.py`, `scripts/spike_anam_tools.py`, frontend `entrevista/[token]/page.tsx`, `dashboard/entrevistas/page.tsx`, `formulario-contenido.tsx`.
- **`ia.mensaje_inicial_entrevista(titulo_vacante)`** — antes `(nombre, empresa)`: cualquier llamador viejo rompe. Nuevas constantes `ia.AVISO_SILENCIO`, `ia.ESPERA_INICIO`.
- `crear_sesion_avatar("Red Human", …)` (antes `"Alma"`); prompt sin Alma; frase de silencio «{Nombre}, no te escuché. ¿Comenzamos?».

#### D. Parte 3 — Formulario de vacante (`c1f3cd3`) — cambio grande
Archivos: `models.py`, `services/ia.py` (generador reescrito), `routers/vacantes.py`, `routers/plantillas.py`, `routers/requisiciones.py`, `services/agente.py`, `serial.py`, frontend `formulario-contenido.tsx` (reescrito), `campos.tsx` (+`CampoSueldo`), `dashboard/vacantes/page.tsx`, `dashboard/candidatos/page.tsx`, `dashboard/entrevistas/page.tsx`, `dashboard/page.tsx`, `lib/api.ts`, `lib/data.ts`; nuevo `scripts/verificar_formulario_vacante.py`.
- **Esquema**: `vacantes` y `plantillas` ganan `sueldo_desde`, `sueldo_hasta`, `sueldo_moneda`, `sueldo_periodicidad`. `CAMPOS_PLANTILLA` 19 → 23.
- **`ia.generar_vacante(ficha: FichaVacante)`** — antes `(titulo, area, ubicacion, sueldo, requisitos, empresa, modalidad, notas)`: firma totalmente nueva. `ia._demo_vacante(ficha)`, `ia._asegurar_capturado`, `ia._unir_capturado`. Se eliminó `notas`.
- `routers/vacantes`: `GenerarIn` = ficha (nuevos `seniority`, `sueldo_*`, `descripcion`, `requisitos_indispensables[]`, `requisitos_deseables[]`, `beneficios[]`; `sueldo` texto legado aceptado); `_validar_sueldo`; `_ficha`; `requisitos_lista`; `SEPARADOR_REQUISITOS = " · "`; **`_aplicar_generado` ahora solo rellena vacíos** (antes sobrescribía todo) y `crear` lo dispara si `generar_si_falta and not publicaciones and not responsabilidades` (antes `not descripcion`); `crear` fija `sueldo = datos.sueldo_texto() or "A convenir"` y `requisitos = " · ".join(indispensables)`; `actualizar` recalcula `sueldo` si cambian campos estructurados; `regenerar` construye `FichaVacante` desde la vacante y limpia textos generados antes de aplicar.
- `routers/requisiciones.convertir_vacante`: llamada a `_generar(GenerarIn(...), v.empresa)` (antes 1 argumento → TypeError) y respuesta `{**_salida(r), "vacante": v.codigo}` (antes `"vacante": null`).
- `services/agente._ejecutar_crear_vacante`: `notas` → `descripcion`.
- `serial.vacante_dict`: `sueldoDesde/Hasta/Moneda/Periodicidad`. `plantillas._plantilla_dict` igual.
- Frontend: `ContenidoVacante.requisitos` pasó de `string` a **`string[]`**; `contenidoComoPayload` manda `requisitos` (unido) + `requisitos_indispensables`; `contenidoDesdeGenerado` ya no toca sueldo/beneficios/seniority; `faltantesDatosPrincipales`; props del formulario cambiaron (`slotDatosPrincipales`, `faltaCliente`; se quitaron `notasIA/onNotasIA/empresa`); `CrearVacante` usa `clienteId` `""` = sin elegir, `0` = recluta directo; botón «Publicar vacante»; `nombreEtapa()` (`lib/api.ts`) muestra «Entrevista Red Human» donde antes se pintaba el valor `"Entrevista IA"` (Kanban, chips, «Enviar a», embudos, select de Entrevistas). El valor interno de la etapa NO cambió.

#### E. Fase 7A — Entrevista Humana (`c67f2d6`)
Archivos: `models.py`, `routers/auth.py`, `routers/cuentas.py`, `routers/candidatos.py`, `routers/notificaciones.py`, `services/notificaciones.py`, `serial.py`, `scripts/sembrar_reglas_notificacion.py`, `.env.example`, frontend `dashboard/candidatos/page.tsx` (`ModalProgramarEntrevista` reescrito), `dashboard/configuracion/page.tsx`, `linea-notificar.tsx`, `confirmacion-accion.tsx`, `lib/api.ts`, `lib/data.ts`; nuevo `scripts/verificar_entrevista_humana.py`; `scripts/verificar_config_admin.py` ajustado.
- **Esquema**: `entrevistas_humanas.contacto_id`. Nueva constante `models.REGLAS_NOTIFICACION_DEFAULT`.
- **Contratos cambiados**:
  - **`POST /candidatos/{codigo}/entrevista-humana` ahora responde `{"resultados": [...], "candidato": {...}}`** (antes el dict del candidato directo). Cualquier consumidor que lea `r.json()["etapa"]` o `r.data.etapa` directamente se rompe (frontend actualizado: `onListo(r.data.candidato, r.data.resultados)`; el agente `_ejecutar_programar_entrevista` devuelve el dict tal cual).
  - `EntrevistaHumanaIn` gana `entrevistador_contacto_id`; interno toma `whatsapp` de `Usuario.telefono`.
  - `NotificarIn.cliente_contactos_ids` (None = todos, [] = ninguno); `_ReglaEfectiva.cliente_contactos_ids`; `disparar` filtra contactos.
  - `_enviar_y_registrar` devuelve `{destinatario, canal, destino, enviado, proveedor, detalle}` (antes el dict crudo del proveedor o `{enviado:false}`).
  - `GET /auth/entrevistadores` ahora expone `correo` y `telefono`; `usuario_dict` expone `telefono`; `CrearUsuarioIn/ActualizarUsuarioIn/AgregarUsuarioIn.telefono`; `crear_usuario_basico(..., telefono="")`.
  - Siembra perezosa de reglas: `entrevista_agendada` **nace encendida** (correo+WhatsApp a candidato y entrevistador). Texto del WhatsApp al candidato ya no dice "asistente de IA".
  - `serial.postulacion_dict`: `clienteIdVacante`; `_entrevista_humana_dict`: `whatsappExterno`, `contactoId`.
- Frontend: `LineaNotificar` y `ConfirmacionAccion` reciben `clienteId` y cargan contactos con `fetchCliente`; `lineasResultados()` para mostrar ✓/✗ por canal; campo «WhatsApp» en usuarios; etiqueta «Persona de RH» → «Entrevistador».

#### F. Fase 7B — Microsoft Teams (`ff61558`)
Archivos: `config.py`, `models.py`, `main.py`, `routers/candidatos.py`, `serial.py`, `requirements.txt` (+`cryptography`), `.env.example`, `README.md`; nuevos `services/teams.py`, `routers/integraciones.py`, `scripts/verificar_teams.py`; frontend `dashboard/candidatos/page.tsx`, `dashboard/configuracion/page.tsx` (+`SeccionIntegraciones`), `lib/api.ts`, `lib/data.ts`.
- **Esquema**: tabla `integraciones_teams`; `entrevistas_humanas.teams_evento_id`.
- **Dependencia nueva `cryptography`**: si no se instala en el servidor, `services/teams._fernet()` lanza `ModuleNotFoundError` **solo cuando se usa** (import diferido dentro de la función) — el arranque no falla.
- `programar_entrevista_humana`: con Videollamada, si `teams.integracion_de(db, p.cuenta_id)` (requiere las 3 `TEAMS_*` **y** fila conectada) y `usar_teams` (default **True**) y no llega `liga` → `crear_reunion` (Graph `POST /me/events`) antes de guardar; error → **HTTP 502** «No se pudo crear la reunión de Teams: … Reintenta o usa «Usar otra liga».» y `db.commit()` del `ultimo_error`. Sin Teams → `liga` obligatoria (400 «Falta la liga de la videollamada.» como antes).
- `modificar_entrevista_humana` y `cancelar_entrevista_humana` responden `{**postulacion_dict, "avisoTeams": str|None}`; Modificar conserva la liga de Teams si no se manda `liga`; si deja de ser videollamada cancela el evento.
- `GET /integraciones/teams/callback` es público y redirige a `{APP_URL}/dashboard/configuracion?teams=ok|error`. La Redirect URI se arma con `request.base_url` (detrás de proxy puede salir `http://127.0.0.1:8000/...` — por eso existe `TEAMS_REDIRECT_URI`).
- Frontend: `programarEntrevistaHumana` manda `usar_teams`; el modal consulta `fetchIntegracionTeams()` (endpoint solo admin: para un `Usuario` no admin regresa 403 → `teamsConectado=false` → campo manual); `porTeams` en la ficha.

#### G. Correcciones urgentes de negocio (2026-09-13, sin commit al generar esta versión)
Archivos: `models.py` (`Entrevista.motivo`, estado `parcial`, `MOTIVOS_ENTREVISTA`), `services/ia.py`
(`TurnoPrefiltro` sin `score` ni `revision`; `AjustePerfil.fortalezas/compatibilidad`;
`SuficienciaEntrevista`, `suficiencia_entrevista`, `texto_util_candidato`; `evaluar_entrevista(...,
faltante=, analisis_cv=, cv_datos=)` con `EvaluacionEntrevista.faltante`), `routers/entrevistas.py`
(`SesionIn{modo}`, `ESTADOS_CERRADOS`, `MENSAJE_CERRADA`, `_cerrar_sin_evaluar`, `/finalizar` valida
el transcript, `reabrir` acepta `parcial`, `GET /publica.motivo`), `routers/candidatos.py`
(`procesar_prefiltro` ya no escribe `p.score`; `_auto_decision_zero_touch(db, p, resultado_prefiltro)`;
`_aplicar_cv` guarda `fortalezas_cv/compatibilidad_cv/experiencia_relevante_cv`), `serial.py`
(`_sintesis_global`: sin prefiltro, `entrevistaStatus`, `evaluacionIntegral`, recomendaciones
«Reintentar Entrevista Red Human»/«Realizar Entrevista Red Human»; `entrevista_dict.motivo`),
`routers/vacantes._embudo` y `metricas.pipeline` (solo activas). Frontend: `entrevista/[token]/page.tsx`
(fases `cerrada`/`error`, `rechazoSesion`, `entrarTexto`, vigilante del avatar, `caerATexto`) +
nuevo `entrevista/[token]/error.tsx`; `lib/api.ts` (`iniciarEntrevista(token, forzarTexto)`, estado
`parcial`, `motivo`, `faltante`), `lib/data.ts` (`entrevistaStatus`, `evaluacionIntegral`,
`prefiltroResumen.resultado`, campos `*_cv`), `candidatos/page.tsx` (Resumen y Evaluaciones en 3
bloques), `entrevistas/page.tsx` (estado `parcial`, botón «Reintentar»), `vacantes/page.tsx`
(recarga al volver/30 s).

### 7.3 Dónde mirar primero si «algo que servía dejó de funcionar»
1. **Programar Entrevista Humana** (7A/7B): la respuesta cambió a `{resultados, candidato}`; `usar_teams` default True; con Teams conectado no se manda `liga`. Si el modal falla al abrir, revisar `fetchIntegracionTeams` (403 para no admin es esperado y tolerado) y `fetchCliente(clienteIdVacante)`.
2. **Notificaciones** (7A): `_enviar_y_registrar` cambió el shape; `NotificarIn` acepta `cliente_contactos_ids`; las reglas nuevas nacen encendidas (más correos/WhatsApp que antes). El correo real requiere `RESEND_API_KEY` + `RESEND_FROM` con dominio verificado.
3. **Vacantes** (Parte 3 / Fase 4): firmas nuevas `ia.generar_vacante(FichaVacante)`, `_generar(datos, empresa)`, `GenerarIn`; `requisitos` como lista en el front; `sueldo` derivado; `Vacante.empresa` resuelto por regla; el generador ya no inventa beneficios ni sueldo (contenido «más pobre» es intencional); `_aplicar_generado` solo rellena vacíos. Agente y requisiciones usan estos mismos objetos.
4. **Entrevista IA** (Fase 4 / Parte 2): `mensaje_inicial_entrevista(titulo)`; `avatar_activo()` exige `ANAM_LLM_ID`; `/sesion` exige consentimiento y estado abierto; `/finalizar` exige `en_curso`; despedida fija verificada; frontend `cerrar(cierre)` único.
5. **WhatsApp** (hotfix): `_cuenta_whatsapp` enruta por `Cuenta.whatsapp_comunicacion` — si la Cuenta correcta no tiene capturado el número del WABA y hay varias activas, el mensaje se asigna a la más antigua; persona resuelta por `wa_id OR telefono` más reciente.
6. **UI**: etapa «Entrevista Red Human» es solo etiqueta (`nombreEtapa`); comparaciones deben seguir usando `"Entrevista IA"`. `Usuario.telefono` nuevo en formularios; `clienteId` `0` vs `""` en Nueva vacante.
7. **Despliegue**: nuevas columnas/tablas las agrega `sincronizar()`/`create_all` al arrancar; nuevo paquete `cryptography` (7B) requiere `pip install -r requirements.txt`; la API no arranca si hay candidatos sin postulación (Fase 2).

### 7.4 Pendientes conocidos (no son bugs del código)
- Spike de Anam (client tools) sin correr; guion de ejemplo de la Entrevista IA sin entregar; tiempos de silencio (12/25 s) son supuestos.
- `RESEND_API_KEY`/`RESEND_FROM` en producción; `sembrar_reglas_notificacion.py` en Cuentas con reglas ya guardadas apagadas.
- Validación real de Teams (Redirect URI registrada en Azure, «Conectar cuenta», «Probar conexión»).
- Deuda técnica: CVs adjuntos por WhatsApp se pierden (no se descargan medios); «Mío/Mi equipo» (`ve_equipo`) no aplicado en queries; badges de navegación con valores demo; ruteo de WhatsApp multi-WABA.
