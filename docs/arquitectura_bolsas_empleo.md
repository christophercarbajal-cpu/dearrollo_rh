# Arquitectura: selección de portales de una vacante → bolsas de empleo

> Análisis del código al 2026-09-26 (rama `red-human-v2.0`). Alcance: cómo se registra hoy la
> selección de portales al crear/publicar una vacante, cómo se guarda, y qué hace falta para que esa
> misma selección alimente los feeds XML de **Jooble** y **Talent.com** y el marcado estructurado
> de **Google Empleos** (`JobPosting`).

---

## 1. Cómo funciona hoy el registro

### 1.1 Qué ve el usuario

La sección **«Publicación · Canales»** está al final del formulario «Nueva vacante»
(`red-human-app/app/dashboard/vacantes/page.tsx:1042-1074`). Es una rejilla de 4 casillas que sale
de una constante **del frontend**:

```ts
// red-human-app/app/dashboard/vacantes/page.tsx:91-96
const PLATAFORMAS = [
  { clave: "whatsapp", nombre: "WhatsApp", api: "WhatsApp", nota: "Mensaje y estados" },
  { clave: "occ",      nombre: "OCC",      api: "OCC",      nota: "Texto plano para el formulario de OCC" },
  { clave: "linkedin", nombre: "LinkedIn", api: "LinkedIn", nota: "Post del feed + LinkedIn Jobs" },
  { clave: "portal",   nombre: "Portal",   api: "Portal",   nota: "Landing pública /aplicar" },
];
```

- Estado local: `const [destinos, setDestinos] = useState<string[]>(["WhatsApp", "Portal"])`
  (`page.tsx:801`). Cada clic agrega o quita el valor `api` de la plataforma (`page.tsx:1047-1051`).
- «Publicar vacante» se deshabilita si `destinos` está vacío (`page.tsx:1079`).
- La misma rejilla vuelve a aparecer en el **detalle de la vacante** para publicar después
  (`page.tsx:1499`, `1642-1664`). Ahí arranca con `v.plataformas` o, si está vacío, con
  `["WhatsApp", "Portal"]`.

### 1.2 Qué recibe el backend

**Al crear** — `POST /vacantes` (`red-human-api/app/routers/vacantes.py:357`, modelo `CrearIn`, `:330-354`).
El frontend manda (`page.tsx:822-841`, tipo en `red-human-app/lib/api.ts:690-692`):

| Campo | Tipo | Qué lleva |
|---|---|---|
| `publicar` | `bool` | `true` = «Publicar vacante», `false` = «Guardar borrador» |
| `plataformas` | `List[str]` | `destinos` **solo si** `publicar` es `true`; si no, `[]` (`page.tsx:833`) |
| `publicaciones` | `Dict[str, dict]` | Textos generados por plataforma: `{whatsapp, occ, linkedin, portal}` → cada uno `{titulo, copy, page, etiquetas}` (`page.tsx:824-831`; `BloquePlataforma` en `lib/api.ts:512`) |

Lógica del endpoint (`vacantes.py:372-374` y `:398`, `:408`):

```python
plataformas = [p for p in datos.plataformas if p in PLATAFORMAS]   # lista blanca del backend
if datos.publicar and not plataformas:
    plataformas = ["WhatsApp", "Portal"]                            # default silencioso
...
estado="Publicada" if datos.publicar else "Borrador",
plataformas=plataformas if datos.publicar else [],
```

La lista blanca vive en `red-human-api/app/models.py:25`:

```python
PLATAFORMAS = ["WhatsApp", "OCC", "LinkedIn", "Portal"]
```

**Al publicar después** — `POST /vacantes/{codigo}/publicar` (`vacantes.py:667-690`, `PublicarIn`
`:663-664`, default `["WhatsApp", "Portal"]`). Filtra contra `PLATAFORMAS` y exige al menos una
válida (400). Hace la **unión** con lo que ya había, en el orden de `PLATAFORMAS`:
`v.plataformas = sorted(set((v.plataformas or []) + plataformas), key=PLATAFORMAS.index)`. Después
estampa `publicada_en` (solo la primera vez), escribe en bitácora `vacante_publicada {plataformas}` y
dispara `notificar_vacante_publicada`.

**Otros puntos que tocan la selección**
- `POST /vacantes/{codigo}/cerrar` (`vacantes.py:693-705`) y `DELETE /vacantes/{codigo}` (`:708-725`)
  ponen `v.plataformas = []`.
- Agente conversacional: la herramienta `publicar_vacante` llama al mismo endpoint
  (`red-human-api/app/services/agente.py:610-611`, esquema en `:926-929`).
- Salida: `serial.vacante_dict` expone `"plataformas": v.plataformas or []` (`serial.py:101`); el
  tablero pinta los chips con eso (`page.tsx:461-465`).
- `PATCH /vacantes/{codigo}` (`ActualizarIn`, `vacantes.py:539`) **no** acepta `plataformas`.
- Las Plantillas **no** guardan plataformas: el campo no está en `models.CAMPOS_PLANTILLA`
  (`models.py:1016-1023`).

### 1.3 Qué hace realmente cada plataforma hoy

| Plataforma | Efecto real al seleccionarla |
|---|---|
| **Portal** | Ninguno propio. El portal público (`GET /vacantes/publicas`, `vacantes.py:482`) y la landing `/aplicar/[slug]` (`GET /vacantes/slug/{slug}`, `:448-455`) filtran **solo por `estado == "Publicada"`**. Una vacante publicada sin «Portal» también aparece. |
| **WhatsApp** | Ninguno propio. El menú del agente (`routers/webhooks.py:118`, `_vacantes_publicadas`) también filtra solo por `estado == "Publicada"`. |
| **OCC / LinkedIn** | Ninguna integración. Solo es una etiqueta: el contenido para esos portales es `Vacante.publicaciones["occ" \| "linkedin"]`, texto que RH **copia y pega a mano** (`PestanasPlataforma`, `page.tsx:1164-1165`). No sale ninguna llamada a OCC ni a LinkedIn. |

**Conclusión:** hoy `plataformas` es un **registro declarativo** de dónde se dijo que se publicaría.
Ninguna parte del sistema lo usa para decidir qué se muestra ni a dónde se envía.

### 1.4 Inconsistencias detectadas

1. **Borrador pierde la selección.** Con «Guardar borrador» se manda `plataformas: []`, y lo que RH
   marcó no se guarda.
2. **Default silencioso.** Si se publica sin plataformas válidas, el backend pone `["WhatsApp", "Portal"]` sin avisar.
3. **No se puede retirar un solo portal.** `publicar` solo agrega (unión) y `PATCH` no acepta el
   campo. La única forma de quitar es `cerrar`/`DELETE`, que vacían la lista completa.
4. **Sin historial por portal.** Al cerrar, la lista se borra. `publicada_en` es una sola fecha
   global, sin fecha por portal ni fecha de retiro (solo queda en bitácora).
5. **Catálogo duplicado.** La lista existe en el backend (`models.PLATAFORMAS`) y en el frontend
   (`PLATAFORMAS` de `page.tsx`). Agregar un portal exige tocar ambas, y el agente la trae en un
   tercer lugar (`agente.py:929`).
6. **Datos de ejemplo fuera de catálogo.** `seed.py:36` y `lib/data.ts:413` usan `"Indeed"`, que no
   está en `models.PLATAFORMAS` (la API lo descartaría).

---

## 2. Cómo se guarda hoy en la base

**Una sola columna JSON en `vacantes`**, sin tabla puente:

```python
# red-human-api/app/models.py:81
plataformas: Mapped[list] = mapped_column(JSON, default=list)
```

- En SQLite queda como texto JSON, p. ej. `["WhatsApp","OCC","Portal"]`, ordenado según
  `models.PLATAFORMAS` cuando pasa por `publicar`.
- Es el mismo patrón que `colaboradores_ids` y `preguntas_filtro` (comentario en `models.py:116`).
- Columnas relacionadas de la misma vacante que un feed necesita:

| Columna | Contenido |
|---|---|
| `estado` | `Publicada \| Borrador \| En revisión \| Cerrada \| Eliminada` |
| `publicada_en` | Primera publicación (global) |
| `actualizada_en` | `onupdate` automático |
| `slug` | Ruta pública `/aplicar/{slug}` |
| `publicaciones` | JSON `{whatsapp, occ, linkedin, portal: {titulo, copy, page, etiquetas}}` |
| `titulo`, `descripcion`, `resumen`, `responsabilidades`, `requisitos` (texto « · »), `requisitos_deseables`, `beneficios` | Contenido |
| `sueldo_desde`, `sueldo_hasta`, `sueldo_moneda`, `sueldo_periodicidad` | Sueldo estructurado (`sueldo` es el texto derivado) |
| `ubicacion_estado`, `ubicacion_municipio`, `modalidad` (`Presencial \| Híbrido \| Remoto`), `area`, `seniority` | Ubicación y perfil |
| `cuenta_id`, `cliente_id`, `mostrar_cliente_candidato` | Identidad de la empresa (regla `serial.nombre_empresa_candidato`) |
| `Cuenta.logo` | **Ruta en disco** (`routers/cuentas.py:260-275`); no hay URL pública servida |

No existe ninguna tabla de publicaciones por portal, ni ids externos, ni fechas de vigencia
(`validThrough`/expiración).

---

## 3. Qué hace falta para alimentar Jooble, Talent.com y Google Empleos

### 3.1 Decisiones previas (de producto)

1. **¿La selección debe gobernar la visibilidad?** Para los feeds nuevos sí: una vacante entra al
   feed de Jooble **solo** si `"Jooble"` está en `plataformas`. Queda por decidir si «Portal» y
   «WhatsApp» también pasan a filtrar (hoy no lo hacen; ver 1.3). Google Empleos depende de que la
   landing `/aplicar/{slug}` sea pública, así que marcar «Google Empleos» implica «Portal».
2. **Sueldo quincenal en Google.** `baseSalary.unitText` solo admite `HOUR | DAY | WEEK | MONTH | YEAR`.
   Opciones: convertir quincenal a mensual (×2, que es exacto en México) u omitir el sueldo.
   `a_convenir` o sin montos: omitir siempre (regla «no inventar condiciones»).
3. **Vigencia.** Los tres destinos esperan una fecha de expiración. Hay que decidir si se captura
   (`vigente_hasta`) o se deriva (`publicada_en + N días` configurable).
4. **Tipo de jornada.** `employmentType` / `jobtype` no existen en la vacante (el «tipo de
   contratación» vive en el Expediente). Se captura un campo nuevo o se omite.

### 3.2 Backend

**a) Catálogo único de portales**
- Extender `models.PLATAFORMAS` con `"Jooble"`, `"Talent.com"` y `"Google Empleos"`, o mejor un
  catálogo con metadatos:
  `PORTALES = {"Jooble": {"tipo": "feed"}, "Google Empleos": {"tipo": "jsonld", "requiere": "Portal"}, …}`.
- Exponer `GET /vacantes/plataformas` para que el frontend y el agente dejen de duplicar la lista
  (corrige 1.4-5). Mantener `PLATAFORMAS` como lista derivada por compatibilidad (`publicar` usa
  `PLATAFORMAS.index`).

**b) Guardar la selección siempre y poder retirarla**
- `crear`: guardar `datos.plataformas` también en Borrador (corrige 1.4-1). El estado sigue
  decidiendo si se publica.
- Nuevo `PUT /vacantes/{codigo}/plataformas {plataformas: [...]}` que **reemplaza** la lista (agrega
  y retira), con bitácora `vacante_plataformas {agregadas, retiradas}` (corrige 1.4-3).
- Recomendado: tabla `publicaciones_vacante` (`vacante_id` FK, `plataforma`, `publicada_en`,
  `retirada_en`, `por`) para tener historial y métricas por portal (corrige 1.4-4).
  - Se crea en el arranque igual que las demás, con `create_all` más `sincronizar`.
  - `Vacante.plataformas` queda como vista rápida de las activas.
  - `cerrar`/`DELETE` marcan `retirada_en` en lugar de perder el dato.

**c) Un solo normalizador de la vacante pública** — nuevo `services/bolsas.py`

```python
def datos_publicos(v: Vacante) -> dict:
    """Única fuente para los tres destinos. Nunca inventa: lo no capturado se omite."""
    # id=v.codigo, titulo, empresa=nombre_empresa_candidato(v), url=f"{settings.app_url}/aplicar/{v.slug}",
    # descripcion_html (resumen + descripcion + responsabilidades + requisitos_lista(v.requisitos)
    #                   + requisitos_deseables + beneficios, escapado),
    # estado=ubicacion_estado, municipio=ubicacion_municipio, pais="MX", remoto=(modalidad == "Remoto"),
    # sueldo={desde, hasta, moneda, periodicidad} solo si hay montos, publicada_en, actualizada_en,
    # vigente_hasta, logo_url (ver f)

def vacantes_para(db, plataforma: str, cuenta_slug: str | None = None) -> list[Vacante]:
    # estado == "Publicada" AND Cuenta.estado == "Activa" AND plataforma en v.plataformas
    # (SQLite: filtrar en Python o con json_each; mismo aislamiento por Cuenta que /vacantes/publicas)
```

Tres serializadores encima de ese dict, para que ningún portal tenga reglas propias de negocio:

**d) Feeds XML públicos** — nuevo `routers/feeds.py`
- `GET /feeds/jooble.xml` y `GET /feeds/talent.xml` (opcional `?cuenta=<slug>`), con
  `Content-Type: application/xml`, generados con la librería estándar (`xml.etree.ElementTree`;
  `lxml` no está en `requirements.txt`). Recomendado: `ETag`/`Last-Modified` con el `max(actualizada_en)`.
- Son públicos a propósito (solo datos de la vacante, ningún dato de candidato). Hay que agregarlos
  a la lista de rutas públicas que revisa el CI y registrar el router en `main.py`.
- Mapeo de campos (**la estructura exacta debe confirmarse contra la especificación que cada portal
  entrega al dar de alta el feed**):

| `datos_publicos` | Jooble (tags habituales) | Talent.com (formato tipo Indeed) |
|---|---|---|
| `id` (VAC-####) | `<job id="…">` | `<referencenumber>` |
| `titulo` | `<name>` | `<title>` |
| `url` (+ `?utm_source=jooble`) | `<link>` | `<url>` |
| `descripcion_html` | `<description>` | `<description>` |
| `empresa` | `<company>` | `<company>` |
| `municipio`, `estado` | `<region>` | `<city>`, `<state>`, `<country>MX` |
| `sueldo` (si existe) | `<salary>` | `<salary>` |
| `publicada_en` / `actualizada_en` | `<pubdate>` / `<updated>` | `<dateposted>` |
| `vigente_hasta` | `<expire>` | `<expirationdate>` |
| tipo de jornada (si se captura) | `<jobtype>` | `<jobtype>` |

- Una vacante sale del feed en cuanto deja de cumplir el filtro (cerrada, eliminada, retirada del
  portal). No hace falta avisar al portal: ellos vuelven a leer el feed.

**e) Google Empleos (JSON-LD `JobPosting`)**
- Nuevo `GET /vacantes/slug/{slug}/jobposting` que regresa el JSON-LD armado en el backend con
  `datos_publicos`. Solo si `"Google Empleos"` está en `plataformas`; si no, 404.
- Mapeo:
  - `title` ← `titulo`
  - `description` ← `descripcion_html` (Google exige HTML)
  - `datePosted` ← `publicada_en`
  - `validThrough` ← `vigente_hasta`
  - `hiringOrganization` ← `{@type: Organization, name: empresa, logo: logo_url}`
  - `identifier` ← `{@type: PropertyValue, name: empresa, value: codigo}`
  - `jobLocation` ← `{@type: Place, address: {@type: PostalAddress, addressLocality: municipio, addressRegion: estado, addressCountry: "MX"}}`
  - `Remoto` → `jobLocationType: "TELECOMMUTE"` + `applicantLocationRequirements: {@type: Country, name: "MX"}`
  - `baseSalary` ← `{@type: MonetaryAmount, currency, value: {@type: QuantitativeValue, minValue, maxValue, unitText}}`, con `mensual→MONTH`, `semanal→WEEK`, `anual→YEAR` y `quincenal` según la decisión 3.1-2
  - `directApply: true`: la postulación es un solo paso en `/aplicar/{slug}`
  - `employmentType` solo si se captura (3.1-4)
- Al cerrar o eliminar, `/vacantes/slug/{slug}` ya responde 410 (`vacantes.py:453-454`); el
  JSON-LD debe desaparecer igual. Opcional: Indexing API de Google (admite `JobPosting`) para
  avisar altas y bajas al momento; requiere cuenta de servicio y es un servicio aparte best-effort,
  como Teams y Resend.

**f) Logo público.** `Cuenta.logo` es una ruta en disco. Hace falta un endpoint público
(p. ej. `GET /cuentas/publica/{slug}/logo`) para dar a Google y a los feeds una URL absoluta.

**g) Trazabilidad de la fuente.** Las URLs de los feeds llevan `utm_source` (jooble | talent | google).
`/candidatos/postular` debe leerlo y guardar `Candidato.fuente` / `Postulacion.origen` con valores
nuevos (`Jooble`, `Talent.com`, `Google`; extender `ORIGENES_POSTULACION`). Así «por fuente» en
`/metricas/pipeline` mide cada portal.

**h) Campos nuevos en `Vacante`** (columnas nullable; `migraciones.sincronizar` las agrega solas):
- `vigente_hasta` (DateTime), si se decide capturarla (3.1-3).
- `tipo_jornada` (catálogo: tiempo completo, medio tiempo, temporal, prácticas, por proyecto), si se
  decide (3.1-4). Agregarlo también a `CAMPOS_PLANTILLA` y al formulario compartido.

**i) Regresión** — `scripts/verificar_bolsas_empleo.py`:
- una vacante solo entra al feed del portal elegido;
- Borrador, Cerrada, Eliminada y Cuenta inactiva nunca salen;
- el XML es válido;
- el JSON-LD trae los campos obligatorios;
- no aparece sueldo si no se capturó;
- la empresa respeta «mostrar cliente al candidato»;
- `PUT …/plataformas` retira el portal.

### 3.3 Frontend

1. **Catálogo desde la API.** La rejilla «Publicación · Canales» (`page.tsx:1042-1074` y
   `1642-1664`) se arma con `GET /vacantes/plataformas`, no con la constante local. Se agrupa en
   «Canales propios» (Portal, WhatsApp), «Bolsas conectadas» (Jooble, Talent.com, Google Empleos) y
   «Copiar y pegar» (OCC, LinkedIn), con una nota honesta de lo que hace cada una.
2. **Guardar la selección también en borrador** (`page.tsx:833`: mandar `destinos` siempre).
3. **Editar portales de una vacante publicada** en el detalle con `PUT …/plataformas`, que sirve
   para agregar y para retirar. Retirar va en el menú «…» (regla de UI: una acción principal).
4. **Regla «Google Empleos requiere Portal»:** al marcar Google se marca Portal y se explica.
5. **JSON-LD en la landing.** `red-human-app/app/aplicar/[slug]/page.tsx` es `"use client"`, así
   que el marcado quedaría solo del lado del navegador. Se parte en dos:
   - un **Server Component** (`page.tsx`) que en el servidor pide `GET /vacantes/slug/{slug}/jobposting`,
     define `generateMetadata` (título y descripción) y emite
     `<script type="application/ld+json">`;
   - el formulario actual pasa a un componente cliente (`formulario-aplicar.tsx`) sin cambios de
     flujo (sigue siendo un solo paso).
6. **Indexación.** Agregar `app/sitemap.ts` con las `/aplicar/{slug}` publicadas en Google y
   `app/robots.ts` que permita `/aplicar/` y `/portal`; hoy no existe ninguno de los dos.
7. **Limpiar los datos de ejemplo** que usan `"Indeed"` (`lib/data.ts:413`), o agregarlo al
   catálogo si se va a conectar.

### 3.4 Orden sugerido

1. Catálogo único, guardar la selección en borrador y `PUT …/plataformas`.
2. `services/bolsas.py` (`datos_publicos`, `vacantes_para`) y el logo público.
3. Feeds `/feeds/jooble.xml` y `/feeds/talent.xml`, más el alta del feed en cada portal.
4. JSON-LD con Server Component, `sitemap.ts` y `robots.ts`, validado con la prueba de resultados
   enriquecidos de Google.
5. `utm_source` hacia `fuente`/`origen`, tabla `publicaciones_vacante` y regresión.
