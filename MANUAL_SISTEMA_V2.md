# Manual del sistema Red Human AI — V2

> Estado al **2026-10-07**. Esta es la referencia del sistema **tal como funciona hoy**. Las reglas finas, decisión por
> decisión, viven en `CLAUDE.md`. Este manual explica qué hace cada módulo y cómo probar el flujo completo de punta a punta.

- **Frontend:** `red-human-app/` (Next.js 15, UI en español de México).
- **Backend:** `red-human-api/` (FastAPI + SQLAlchemy). Las claves viven solo en `red-human-api/.env`.
- **Regresión:** `red-human-api/scripts/verificar_*.py`. Cada script levanta su propia base desechable en modo demo;
  hoy pasan los 52.

---

## 0. Conceptos que atraviesan todo

| Concepto | Qué es |
|---|---|
| **Cuenta** | La empresa que usa Red Human (p. ej. Grupo CARBE). Todo dato se aísla por Cuenta; se cambia arriba a la izquierda. |
| **Cliente** | Empresa para la que recluta una Cuenta. Sus vacantes viven en la Cuenta. |
| **Candidato** | La PERSONA: nombre, teléfono, correo, CV y archivos. |
| **Postulación** (`P-####`) | Un proceso de esa persona en una vacante: etapa, resultados, chat, evaluaciones y expediente. Una tarjeta del Kanban = una postulación. |
| **Etapas** (5, fijas) | Prefiltro → Filtro Red Human → Filtro humano → Contratación → Onboarding. Los valores internos son `Prefiltro`, `Entrevista IA`, `Entrevista Humana`, `Contratación` y `Onboarding`. |
| **Ruta** | Los pasos que debe cumplir una postulación, ubicados en esas 5 etapas. Cada postulación guarda una COPIA fija de su ruta. Ver §10. |
| **Human-in-the-loop** | La IA solo recomienda. Avanzar, descartar y dar de alta lo decide una persona de RH, y queda en la bitácora con su nombre (LFPDPPP). |
| **Modo Prueba** | Interruptor en Configuración. Relaja validaciones (documentos, duplicados, alta) para probar sin datos reales. |
| **AMBIENTE_PRUEBA** | Variable del `.env` (solo desarrollo): TODO el contacto con candidatos va por Telegram. Ver §12. |

---

## 1. Módulos (qué hacen hoy)

### 1.1 Tablero de control (`/dashboard`)
- Una sola fuente: `GET /metricas/tablero`, que se recarga sola. **No hay números inventados**: sin datos se ven ceros o estados vacíos.
- **Muestra:**
  - candidatos activos por etapa;
  - colaboradores activos;
  - Onboarding activo y su avance;
  - evaluaciones pendientes o esperando revisión;
  - ciclos de Desempeño en curso.
- Todo filtrado por la Cuenta actual. Un clic en cualquier contador abre Candidatos filtrado por esa etapa.

### 1.2 Vacantes (`/dashboard/vacantes`)
- **Formulario único**, el mismo para «Nueva vacante» y para Plantillas, en este orden:
  1. Datos principales: condiciones reales (sueldo estructurado, ubicación, modalidad, horario).
  2. «Sobre la vacante»: descripción breve, indispensables, deseables, prestaciones, enfoque.
  3. **Generar vacante con Red Human**.
  4. «Configuración avanzada» (plegada): prefiltro web, prefiltro WhatsApp y los textos.
- **Red Human nunca inventa condiciones reales.** Lo que RH no capturó queda vacío o pendiente, y lo que sí capturó se respeta tal cual.
- **Liga con plantillas:**
  - Las plantillas de **contenido** (Configuración → Plantillas) precargan el formulario.
  - Las plantillas de **proceso** (Configuración → Procesos de selección) se copian a la vacante en la sección **«Proceso de selección»**.
  - La vacante puede personalizar su copia sin tocar la plantilla; cada cambio sube su versión.
- **Ciclo de vida:**
  - Borrador → Publicada → Cerrada.
  - «Eliminar» es una baja lógica (`estado="Eliminada"`): cierra sus postulaciones activas y desaparece de listados, portal y WhatsApp.
- **Portal público:**
  - `/portal?cuenta=<id|slug>` es la bolsa de trabajo de una Cuenta (incluye las vacantes de sus Clientes). Sin parámetro es la bolsa global.
  - El botón **«Ver Bolsa de Trabajo»** del menú lateral abre la bolsa de la Cuenta actual.
  - Postularse es UN solo paso: `/aplicar/<slug>`.

### 1.3 Candidatos (`/dashboard/candidatos`)
- **Kanban de 5 columnas.** Por defecto muestra solo postulaciones activas; «Mostrar cerradas» incluye las descartadas en su misma columna con la etiqueta «No cumple».
- **Ficha del candidato** (clic en la tarjeta). Pestañas:
  - **Resumen** (nueva): arriba la **ruta completa** del candidato, de la solicitud a «Alta como colaborador»:
    - agrupada por etapa;
    - por cada paso: **Responsable**, **Estado** (Pendiente / En curso / Completada / Omitida / Cancelada) y exactamente qué falta o qué lo bloquea, **Resultado** (Favorable / Con observaciones / No favorable) y **Acción**;
    - arriba, la **siguiente acción principal**;
    - el estado se **deriva** de registros reales, así que una actividad terminada o con resultado nunca aparece «Pendiente».
    - Debajo de la ruta: Evaluación integral, tarjetas de evaluaciones y datos generales.
  - **Evaluación integral:** Análisis de CV + Entrevista Red Human + evaluaciones. Es un resultado acumulado, no una etapa.
  - **CV y documentos:**
    - CV extraído y archivos;
    - trazabilidad de los documentos (cuándo se pidieron y cuándo se recibieron);
    - en rutas Masivos, el bloque **«Validar documentos»** para aprobar o rechazar antes de Contratación.
  - **WhatsApp:** el chat. Por Telegram se registra igual, como canal «telegram».
  - **Contratación / Expediente:** solo en Contratación y Onboarding. Condiciones, carta, contrato, «Enviar a Onboarding» y tareas.
- **Acciones de la ficha:**
  - **Botón único «Agregar evaluación»:** entrevista humana, médica, psicométrica, socioeconómica, técnica, referencias u otra.
  - **Menú «…»:** «Mover a otra etapa», «Avanzar a Filtro humano», «Descartar candidato…».
  - **Menú «…» de la ruta:** «Agregar actividad a este candidato…» (un paso extra SOLO para esa postulación) y «Avanzar … omitiendo obligatorios…».
- **Histórico:**
  - `Postulacion.historial` acumula, sin borrar nada, cada decisión humana: pasos omitidos o cancelados con quién y por qué, «Entrevista Red Human omitida manualmente», rutas asignadas, ingreso confirmado, etc.
  - Todo queda además en la bitácora hash-encadenada.
  - Una persona con varias postulaciones conserva cada una con su propio historial y expediente.

### 1.4 Entrevistas (`/dashboard/entrevistas`)
- **Entrevista Red Human** (etapa Filtro Red Human):
  - Sala pública `/entrevista/<token>`.
  - **Anam** pone la cara, la voz y el oído (STT/TTS); el que piensa es **nuestro modelo de OpenAI**, registrado en Anam como LLM propio (`ANAM_LLM_ID`).
  - La entrevistadora se presenta solo como «Red Human».
  - Cada turno se guarda (transcript durable). Si el avatar falla, la sala cae a modo texto.
  - **Cierre:** `POST /finalizar` evalúa (afinidad, fortalezas, puntos por validar, recomendación). Con pocas respuestas queda `parcial` o `interrumpida`, y RH puede «Reintentar» o «Evaluar con lo que hay».
  - **Modo tótem** (`?totem=1`): pantalla vertical de 55".
- **Entrevista humana** (etapa Filtro humano):
  - Es una evaluación más: «Agregar evaluación» → Entrevista humana.
  - El entrevistador es un Usuario de la Cuenta, un contacto del Cliente o «Otro».
  - Cita: presencial, teléfono o videollamada; con Microsoft Teams conectado, la reunión se crea sola.
  - El entrevistador registra el resultado desde su liga `/entrevista-humana/<token>` (Avanzar / No avanzar / Requiere otra entrevista), o lo registra RH.
  - Los correos salen con el layout corporativo.

### 1.5 Colaboradores (`/dashboard/colaboradores`)
- Es la **base maestra de personas internas**: Desempeño, Clima y Conocimiento referencian a `Colaborador`.
- **Cómo entra alguien:**
  - por el **alta** al final del Onboarding (toma estrictamente las condiciones del expediente y guarda una foto inmutable `condiciones_ingreso`);
  - por alta manual;
  - por importación (vista previa + confirmar).
- Baja (reversible) ≠ eliminación lógica.

### 1.6 Onboarding (`/dashboard/onboarding`)
- **Entrada:**
  1. Desde Contratación: capturar condiciones (puesto, sueldo, tipo, fecha de ingreso, empresa contratante) → «Enviar a Onboarding».
  2. Se abre un resumen precargado con la Plantilla de Onboarding (documentos, recursos, responsables, curso de inducción, plazos).
  3. **«Iniciar Onboarding»** es el ÚNICO gatillo de la etapa.
- **Documentos:**
  - Estados: Pendiente / Por revisar / Aprobado / Rechazado / No aplica.
  - «Aprobado» = recibido **y** revisado por una persona. Lo que validó solo la IA queda «Por revisar».
  - El porcentaje del expediente cuenta solo los Aprobados.
  - El candidato los sube con su liga `/expediente/<token>` o por WhatsApp/Telegram.
  - Recordatorios automáticos en 3 niveles.
- **Tareas:**
  - Tres fijas: Contrato firmado, Alta IMSS/nómina y Confirmar ingreso.
  - Más los recursos (correo, equipo, accesos), con plazos relativos a la fecha de ingreso.
- **Cierre:**
  - «Confirmar ingreso» → «Dar de alta» (crea el Colaborador) → «Cerrar Onboarding» (manual).
  - «No ingresó» cancela todo y cierra la postulación.
- **Firma electrónica:** con Dropbox Sign configurado, la carta y el contrato se firman incrustados (RH + candidato). El contrato firmado cierra la tarea.

### 1.7 Capacitación e Inducción (`/dashboard/capacitacion`)
- **Un solo tipo de curso:**
  1. Tema + contexto + adjuntos + duración libre + modalidad («Instructor IA» con avatar, o «Autoguiado»).
  2. Red Human genera el objetivo, los módulos (con resumen y puntos clave) y la evaluación.
  3. Crear → Revisar → **Finalizar** → Asignar.
- **A quién se asigna:** a colaboradores, candidatos (postulación) o externos (liga abierta).
- **Sala pública** `/capacitacion/<token>`: módulo por módulo, una pregunta por pantalla, resultado Aprobado/No aprobado con porcentaje y PDF del material.
- **Inducción:** el curso de inducción de la Plantilla de Onboarding se asigna al «Iniciar Onboarding». Terminado, el paso «Inducción» de la ruta queda Completado.
- **Modo Expo:** «Generar Liga Directa» crea una liga de demo sin mandar mensajes.

### 1.8 Desempeño (`/dashboard/desempeno`) y Clima (`/dashboard/clima`)
- **Desempeño:**
  - Evaluación Borrador → En curso → Cerrada.
  - Criterios medibles (meta, sentido) o descriptivos (escala 1-5) con pesos. La IA los propone y nunca inventa metas.
  - Evaluador = Usuario de la Cuenta (se propone el jefe del roster).
  - Calificación = promedio ponderado SOLO de los resultados válidos.
  - Fortalezas y brechas confirmadas por una persona → acciones (p. ej. asignar un curso).
- **Clima:**
  - Medición Borrador → Abierta → Cerrada (nunca se reabre).
  - Preguntas de escala 1-5, opción múltiple o abiertas, por dimensión.
  - Liga **personal** por invitado; en las anónimas solo se marca «respondió».
  - Índice = promedio de las dimensiones con respuestas. Sin respuestas se ve «Aún no hay respuestas reales», nunca 0 %.
  - El análisis con IA solo corre cuando RH lo pide, sobre datos agregados.

### 1.9 Base de conocimiento (`/dashboard/conocimiento`)
- **RAG real:**
  - documentos por Cuenta → fragmentos → embeddings → búsqueda;
  - respuesta estructurada que cita sus fuentes y se redacta SOLO con la evidencia.
- **Acceso:**
  - Estado del documento: publicado / borrador.
  - Permisos por área y puesto del roster.
  - Un borrador o un documento fuera del área nunca alimenta una respuesta.
- **Pestañas:**
  - **«Documentos y accesos»:** subir, pegar o «Generar con Red Human».
  - **«Consultar a Red Human»:** con «Ver como» para probar los permisos de un colaborador.

### 1.10 Configuración (`/dashboard/configuracion`, solo Administrador)

**Secciones:** Cuentas · Usuarios (incluye los permisos «Autorizar omisiones» y «Acceso a informes médicos») · Clientes · Plantillas (de vacante) · **Procesos de selección** · Plantillas de clima · Plantillas de Onboarding · Pruebas psicométricas · Notificaciones · Integraciones · Modo prueba.

**Motor de Procesos de selección (rutas):**
- **Tres rutas base precargadas** y editables en cada Cuenta:

  | Ruta | Pasos | Prefiltro | Filtro Red Human | Filtro humano | Contratación | Onboarding |
  |---|---|---|---|---|---|---|
  | **Masivos** | 12 | Solicitud web sin CV · Continuar prefiltro por WhatsApp · Solicitar documentos por liga · Validar documentos | Entrevista Red Human | Evaluación médica · Entrevista humana | Condiciones · Carta intención / contrato | Documentos de ingreso · Inducción · Alta como colaborador |
  | **Corporativos sin psicometría** | 9 | Solicitud web con CV y prefiltro | Análisis de CV · Entrevista Red Human | Entrevista humana | Condiciones · Carta intención / contrato | Documentos de ingreso · Inducción · Alta |
  | **Corporativos con psicometría** | 10 | Solicitud web con CV y prefiltro | Análisis de CV · Entrevista Red Human | Psicometría · Entrevista humana | Condiciones · Carta intención / contrato | Documentos de ingreso · Inducción · Alta |

- **Editor:**
  - Pasos elegidos del **catálogo** por etapa.
  - Responsable, condición de avance (ninguna / calificación mínima / dictamen / validación de RH), plazo y **Ejecución: «En paralelo» o «Esperar a…»**. El orden visual NO crea dependencias.
  - **«Avance automático»** por etapa (encendido por defecto en las 3 rutas, salvo Contratación y Onboarding).
  - Si borras o desactivas una ruta base, **«Restaurar rutas base»** la recupera.
- **Asignación** (cascada) al crear la postulación:
  1. Proceso de la vacante.
  2. Proceso predeterminado de la Cuenta (⭐ «Usar como predeterminado»).
  3. «Corporativos sin psicometría».

  La postulación guarda una copia fija: editar la plantilla después no cambia a los candidatos existentes. «Aplicar versión vigente» lo hace explícito.
- **Compuerta:**
  - Avanzar de etapa exige que los pasos **obligatorios** de las etapas que se dejan atrás estén cumplidos u **omitidos con justificación** (al menos 10 caracteres) y el permiso **«Autorizar omisiones»**. Sin el permiso responde 403.
  - Las etapas sin obligatorios no bloquean.
  - Los documentos de Onboarding no bloquean la entrada a Onboarding.
  - Un «No favorable» nunca descarta solo: la decisión es de RH.
- **Históricos:** al arrancar, la API asigna ruta a TODA postulación que no tenga una (`scripts/migrar_rutas_candidatos.py` hace lo mismo con simulación). No mueve etapas, no manda mensajes y nunca marca pasos como completados por la etapa.

---

## 2. Ruta de pruebas (paso a paso)

### 2.0 Preparación del ambiente (una sola vez)

1. **`red-human-api/.env`**, además de lo de siempre (`OPENAI_API_KEY`, `ANAM_*` si vas a probar el avatar):
   ```env
   AMBIENTE_PRUEBA=true                  # todo el contacto con candidatos va por Telegram
   TELEGRAM_BOT_TOKEN=<token de @BotFather>
   TELEGRAM_BOT_USERNAME=<usuario del bot, sin @>
   PSICOMETRICAS_TOKEN=<token>           # solo para el inciso (d)
   PSICOMETRICAS_PASSWORD=<password>
   APP_URL=http://localhost:3000
   ```
   ⚠️ Psicométricas.mx comparte el saldo con producción (100 peticiones). Cada «Enviar al proveedor» consume una petición; «Sincronizar resultado» es una consulta.
2. **Levanta la API.** En el log debe aparecer:
   `[mensajeria] AMBIENTE_PRUEBA activo: TODO el contacto con candidatos va por Telegram (@tu_bot)`.
   Si dice «falta TELEGRAM_BOT_TOKEN», revisa el `.env`.
3. **Webhook de Telegram.** Telegram necesita HTTPS público:
   1. Abre un túnel (p. ej. `ngrok http 8000`).
   2. Registra el webhook: `python scripts/configurar_webhook_telegram.py --url https://<tu-túnel>/api/webhooks/telegram`.
4. **Entra al panel** como Administrador y elige la Cuenta (p. ej. Grupo CARBE) en el selector de arriba a la izquierda.
5. En **Configuración → Modo prueba**, déjalo **apagado** para ver los bloqueos reales. Enciéndelo solo si quieres saltar validaciones de documentos.

### a) Crear una vacante y asignarle una ruta base

1. Ve a **Configuración → Procesos de selección**. Verifica que existan «Masivos», «Corporativos sin psicometría» y «Corporativos con psicometría», con la etiqueta «Ruta base». Si falta alguna, usa **«Restaurar rutas base»**.
2. (Opcional) Abre «…» → **Editar** en una ruta para ver sus pasos por etapa, «En paralelo / Esperar a…» y el interruptor «Avance automático».
3. Ve a **Vacantes → Nueva vacante** y llena «Datos principales» y «Sobre la vacante».
4. Pulsa **«Generar vacante con Red Human»** y revisa el contenido.
5. En la sección **«Proceso de selección»**, elige **«Corporativos con psicometría»** (para el inciso d) o «Masivos».
6. Guarda y pulsa **Publicar**.
7. Comprueba la publicación con el botón **«Ver Bolsa de Trabajo»** del menú lateral: abre `/portal?cuenta=<id>` y la vacante debe aparecer.

### b) Simular una postulación y continuar en Telegram

1. En la bolsa de trabajo, abre la vacante → **Postularme**.
2. Llena nombre, **tu celular real a 10 dígitos** (el mismo número de tu cuenta de Telegram), correo, CV y el prefiltro.
3. Acepta el aviso de privacidad y envía.
4. En «¡Postulación enviada con éxito!» debe aparecer **solo** el botón azul **«Continuar en Telegram»**. Con `AMBIENTE_PRUEBA` nunca sale WhatsApp. La liga es `t.me/<bot>?start=p_<token>`.
5. Pulsa el botón. Telegram abre el bot; tócale **Iniciar**.
6. El bot pide **«📱 Compartir mi número»**. Tócalo: solo se acepta tu propio contacto, y debe coincidir con el teléfono del formulario.
7. El bot retoma tu postulación y sigue el prefiltro. Contesta.
8. En el panel, ve a **Candidatos** y abre la tarjeta: en **Resumen** la ruta muestra «Solicitud web con CV y prefiltro» y el chat aparece en la pestaña WhatsApp con canal «telegram».

**Si algo falla:**
- **El bot no contesta:** revisa el webhook (paso 2.0-3) y el log `[telegram]`.
- **«Esta liga pertenece a otro número»:** el número de Telegram no coincide con el del formulario.

### c) Mover al candidato en «Resumen» respetando los bloqueos

1. Abre la ficha → pestaña **Resumen**. Arriba ves la **etapa actual** y la **siguiente acción**; abajo, todos los pasos por etapa.
2. **Avance automático:** al terminar el prefiltro con resultado «Cumple», el candidato pasa solo a **Filtro Red Human** y recibe por Telegram la invitación a la Entrevista Red Human.
3. Haz la Entrevista Red Human con la liga que manda el bot. Al evaluarse con afinidad ≥ 70 y CV ≥ 70, pasa solo a **Filtro humano**.
4. **Probar un bloqueo:**
   1. En otro candidato todavía en Prefiltro, abre «…» → **Mover a otra etapa** → elige «Contratación» → **Mover**.
   2. El modal muestra **«No se puede avanzar…: faltan pasos obligatorios…»** con la lista exacta.
   3. Escribe una justificación de 10 caracteres o más → **«Omitir obligatorios y mover»**. Requiere el permiso «Autorizar omisiones» (el Administrador lo tiene).
   4. Los pasos saltados quedan «Omitida» con tu nombre y el motivo.
5. **Omitir un solo paso:** en la fila del paso, «…» → **Omitir paso…** (o **Cancelar paso…**). Con «Reactivar» lo deshaces.
6. **En Filtro humano:**
   1. Pulsa **Iniciar** en «Entrevista humana». Se abre «Agregar evaluación» precargada.
   2. Registra el resultado **«Avanzar»**.
   3. Con todos los obligatorios cumplidos, el candidato pasa solo a **Contratación**.
7. **En Contratación:**
   1. Captura las condiciones.
   2. Genera y envía la **carta de intención**: el paso «Carta intención / contrato» se completa.
   3. «Enviar a Onboarding» → **Iniciar Onboarding**. Contratación nunca avanza sola.
   - Los documentos de Onboarding no bloquean la entrada.
8. **En Onboarding:** documentos aprobados, curso de inducción, «Confirmar ingreso» → **Dar de alta**. La ruta termina con **«Alta como colaborador» Completada** y la persona aparece en **Colaboradores**.

### d) Psicometría ad hoc y «Sincronizar resultado»

1. **Configura la prueba** en **Configuración → Pruebas psicométricas → Nueva**:
   - modo **Integrada**;
   - proveedor **«Psicométricas.mx»**;
   - **ID en proveedor** = los IDs numéricos de sus pruebas, separados por coma (p. ej. `1,7`).
2. Abre la ficha del candidato. El candidato **debe tener correo**: sin correo la API responde 409 antes de gastar saldo.
3. **Agrega la evaluación**, por cualquiera de dos caminos:
   - **Ad hoc:** «Agregar evaluación» → **Psicométrica** → **«Usar proveedor integrado»** → elige la prueba → **Agregar evaluación**. Si la ruta no tenía ese paso, queda como actividad **«solo este candidato»** en Resumen, sin tocar la plantilla.
   - O bien: menú «…» de la ruta → **Agregar actividad a este candidato…** → «Psicométrica».
4. En la tarjeta de la evaluación pulsa **«Enviar al proveedor»** (gasta 1 petición). Se guarda la **clave** del candidato.
   - La API no consume otra petición si ya existe una clave en curso para las mismas pruebas.
5. **El candidato contesta:** Psicométricas.mx le manda su liga **por correo** (su API no la regresa). Ábrelo desde ese correo y contesta las pruebas.
6. **Sincroniza el resultado:**
   1. En **Resumen**, el paso de psicometría está «En curso» y tiene el botón **«Sincronizar resultado»**. Púlsalo: el webhook del proveedor apunta a la Demo, así que en desarrollo esto es lo que trae el resultado.
   2. Si aún no termina, el aviso dice «todavía no reporta … como terminada»; repite más tarde.
   3. Si ya terminó, se descargan el **JSON y el PDF**, la evaluación queda «Con resultado» y el paso **Completado** en la misma vista.
7. **Revisa el resultado:** abre la tarjeta de la evaluación → **Ver resultado** para descargar el PDF y registra la **revisión de RH** (dictamen). El dictamen alimenta la condición de avance del paso.

---

## 3. Dónde mirar cuando algo no cuadra

| Síntoma | Revisa |
|---|---|
| El candidato no avanza solo | Resumen → la etapa dice qué falta. ¿«Avance automático» encendido en esa etapa de SU copia de la ruta? Contratación y Onboarding nunca avanzan solos. |
| 409 «faltan pasos obligatorios» | Es la compuerta: complétalos u omítelos con justificación y el permiso «Autorizar omisiones». |
| 403 al omitir | Falta el permiso «Autorizar omisiones» (Configuración → Usuarios). |
| Llegan mensajes por WhatsApp en desarrollo | `AMBIENTE_PRUEBA=true` y `TELEGRAM_BOT_TOKEN` en el `.env`, y reinicia la API. |
| La psicométrica no llega | Usa «Sincronizar resultado». ¿La prueba es «Integrada» con proveedor «Psicométricas.mx»? ¿Hay `PSICOMETRICAS_TOKEN`? |
| Postulaciones sin ruta | `python scripts/migrar_rutas_candidatos.py` (simulación) / `--forzar`. La API también las asigna al arrancar. |
| Regresión general | `python scripts/verificar_<módulo>.py` desde `red-human-api/` (base desechable, sin claves). |
