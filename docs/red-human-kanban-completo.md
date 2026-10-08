# Red Human — Rediseño del tablero de Candidatos (paquete completo)

Este archivo contiene todo lo necesario para implementar el rediseño:

1. **Instrucciones para la IA desarrolladora** (qué hacer y qué no tocar)
2. **Especificación** del diseño y sus reglas
3. **Modelo de datos** que debe exponer el backend
4. **Implementación de referencia** funcional (HTML + CSS + JS, sin dependencias) que se puede abrir en el navegador y adaptar al stack del proyecto
5. **Criterios de aceptación**

---

## 1. Instrucciones para la IA desarrolladora

> Vas a rediseñar la vista **Candidatos → tablero Kanban** de Red Human.
>
> - Usa la sección 4 como referencia visual y de comportamiento: ábrela en un navegador para ver el resultado esperado.
> - **Adáptala al stack y a los componentes existentes del proyecto.** No copies el HTML literal si el proyecto ya tiene componentes de tarjeta, chip, botón o layout; reutilízalos y ajusta sus estilos.
> - **No cambies la lógica de negocio, las etapas del proceso, los permisos ni las rutas.** Es un cambio de presentación más algunos campos calculados.
> - Si un campo de la sección 3 no existe en el backend, créalo o calcúlalo. Si no puedes obtenerlo, deja la zona oculta en la tarjeta (nunca muestres "null", "undefined" ni texto de relleno).
> - Antes de terminar, revisa la lista de la sección 5 punto por punto.

---

## 2. Especificación

### Objetivo
Hoy las tarjetas no tienen jerarquía: todo pesa igual y el dato clave (el score) está escondido. El reclutador debe **escanear una columna en 2 segundos** y ver a quién avanzar, a quién revisar y quién está atorado.

Cada tarjeta tiene **un protagonista (el score)** y tres niveles de lectura:
1. **De lejos:** nombre + score.
2. **Si interesa:** puesto, canal, siguiente paso, porqué del score.
3. **Solo si hay problema:** alertas (sin consentimiento, atorado, expediente incompleto).

### 2.1 Anatomía de la tarjeta (de arriba hacia abajo)

| Zona | Contenido | Reglas |
|---|---|---|
| Arriba izquierda | **Nombre completo** | 15px semibold. Hasta 2 líneas; nunca truncar a 1. |
| Debajo del nombre | Puesto · Canal de origen | 13px gris. Canal: WhatsApp / Web / Referido. |
| Arriba derecha | **Score** en cuadro de 46×46px, radio 12px | Número 19px bold, color por nivel. Sin score: "—" en gris. |
| Siguiente paso | Ícono flecha + texto | 13px peso medio. Texto completo, sin truncar. |
| Porqué del score | `Fuerte: X · Falta: Y` | 12.5px gris, fondo gris claro, radio 8px. Oculto si no hay dato. |
| Expediente | "Expediente" + % y barra de 6px | Solo en etapa Contratación. Ámbar < 100%, verde = 100%. |
| Pie | Chips a la izquierda; días en etapa a la derecha | Ver abajo. |

**Niveles de score**

| Score | Fondo | Texto |
|---|---|---|
| ≥ 85 | `#E3F2E8` | `#1E6B43` |
| 70–84 | `#FBEFD5` | `#8A5300` |
| < 70 | `#FCE5E3` | `#A8261C` |
| Sin score | `#F0EEEA` | `#5E5A54` |

**Chips del pie** (solo los que apliquen): un chip de estado — **Cumple** (verde), **Revisar** (ámbar) o **No cumple** (rojo) — y **Sin consentimiento** (rojo) en la tarjeta afectada. Alto 24px, radio completo, 12px semibold.

**Días en la etapa:** `N d` con ícono de reloj, a la derecha, gris. Si N supera el umbral (constante configurable, **7 días** por defecto): `N d · atorado` en ámbar y semibold.

**Se elimina de la tarjeta:** la píldora "Pendiente" / "Integral: Pendiente · score parcial", "Score CV: X%", "match X", la etiqueta "Demo" y la tipografía monoespaciada.

### 2.2 Encabezado de columna
- Punto de color de la etapa + nombre + conteo a la derecha.
- Debajo (12px gris): `% pasa desde la etapa anterior · días promedio`. Ej.: `43% pasa · 5.8 d promedio`.
- Al final de la columna: botón "Ver N más" con borde punteado.
- Orden por defecto dentro de cada columna: **score descendente** (sin score al final).
- El nombre de la etapa nunca se parte en dos líneas.

### 2.3 Barra superior
- Título "Candidatos" + subtítulo con total en proceso y vacantes abiertas.
- Buscador por nombre, selector **Vacante** (nuevo) y selector **Ordenar** (Score / Días en etapa).
- **El banner amarillo de consentimiento se reemplaza** por tres chips que filtran el tablero al hacer clic (y se desactivan con un segundo clic):
  - `N sin consentimiento` (rojo)
  - `N atorados más de 7 días` (ámbar)
  - `N expedientes incompletos` (neutro)

### 2.4 Estilo general
- Fondo `#F6F5F3`; tarjetas blancas, borde `#E7E4DF`, radio 12px, sombra muy sutil.
- Texto principal `#1C1B19`; secundario `#5E5A54`.
- El rojo de marca se reserva para logo, navegación activa y alertas urgentes. Nunca como decoración.
- Columnas de 268px con 16px de separación; scroll horizontal si no caben.
- Números con `font-variant-numeric: tabular-nums`.

### 2.5 Otros arreglos detectados en la pantalla actual
- Quitar datos de prueba visibles (ej. "Sigue: PRUEBA ABSURDA").
- En el menú lateral, la tarjeta "Agente activo" tapa "Base de conocimiento": corregir espacio o permitir scroll del menú.

---

## 3. Modelo de datos

### Por candidato

| Campo | Tipo | Uso |
|---|---|---|
| `id` | string | Identificador |
| `name` | string | Nombre completo |
| `role` | string | Puesto al que aplica |
| `vacancy_id` | string | Filtro por vacante |
| `stage` | `prefiltro` \| `filtro_ia` \| `filtro_humano` \| `contratacion` \| `onboarding` | Columna |
| `score` | number 0–100 \| null | Número protagonista |
| `filter_status` | `cumple` \| `revisar` \| `no_cumple` \| null | Chip de estado |
| `score_reason` | `{ fortaleza: string, faltante: string }` \| null | Línea del porqué; la genera la IA al calificar el CV |
| `source_channel` | `whatsapp` \| `web` \| `referido` | Canal de origen |
| `next_step` | string | Siguiente paso |
| `stage_entered_at` | ISO date | Para calcular días en la etapa |
| `has_consent` | boolean | Alerta de consentimiento (LFPDPPP) |
| `expediente_pct` | number 0–100 \| null | Barra en Contratación |

### Por etapa (métricas de columna)

| Campo | Uso |
|---|---|
| `stage` | Etapa |
| `total` | Conteo total (no solo las tarjetas cargadas) |
| `conversion_pct` | % que llega a esta etapa desde la anterior (null en Prefiltro) |
| `avg_days` | Promedio de días en la etapa |

### Ejemplo de respuesta

```json
{
  "stats": [
    { "stage": "filtro_humano", "total": 46, "conversion_pct": 43, "avg_days": 5.8 }
  ],
  "candidates": [
    {
      "id": "c_1029",
      "name": "Daniela Morales Delgado",
      "role": "Operadora de producción",
      "vacancy_id": "v_op_prod",
      "stage": "filtro_humano",
      "score": 82,
      "filter_status": "cumple",
      "score_reason": { "fortaleza": "4 años en línea de ensamble", "faltante": "manejo de montacargas" },
      "source_channel": "whatsapp",
      "next_step": "Confirmar entrevista",
      "stage_entered_at": "2026-09-25T15:00:00Z",
      "has_consent": true,
      "expediente_pct": null
    }
  ]
}
```

---

## 4. Implementación de referencia

Guardar como `kanban-referencia.html` y abrir en el navegador. Es funcional: buscador, filtro por vacante, orden y chips de alerta filtran el tablero. Los datos son de ejemplo; en el proyecto real vienen de la API (sección 3).

```html
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Red Human · Candidatos</title>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#F6F5F3; --card:#FFFFFF; --line:#E7E4DF; --line-2:#E2DED8;
    --ink:#1C1B19; --ink-2:#5E5A54; --ink-3:#6B665F;
    --green-bg:#E3F2E8; --green:#1E6B43;
    --amber-bg:#FBEFD5; --amber:#8A5300;
    --red-bg:#FCE5E3;   --red:#A8261C;
    --gray-bg:#F0EEEA;  --gray:#5E5A54;
    --brand:#D2453D;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font-family:'Instrument Sans',system-ui,sans-serif;font-variant-numeric:tabular-nums}
  button,input,select{font:inherit}
  .page{padding:28px 32px 40px;display:flex;flex-direction:column;gap:20px}

  /* Barra superior */
  .top{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px}
  .top h1{margin:0;font-size:26px;font-weight:700;letter-spacing:-.01em}
  .top .sub{font-size:14px;color:var(--ink-2);margin-top:4px}
  .controls{display:flex;flex-wrap:wrap;gap:8px}
  .field{height:40px;padding:0 12px;background:var(--card);border:1px solid var(--line-2);
         border-radius:10px;font-size:14px;color:var(--ink)}
  .search{display:flex;align-items:center;gap:8px;min-width:240px;color:var(--ink-2)}
  .search input{border:0;outline:none;background:transparent;flex:1;min-width:0;color:var(--ink)}

  /* Chips de alerta (reemplazan el banner) */
  .alerts{display:flex;flex-wrap:wrap;gap:8px}
  .alert{height:36px;padding:0 14px;border-radius:999px;font-size:13px;font-weight:600;
         display:flex;align-items:center;gap:6px;cursor:pointer;border:1px solid}
  .alert.red{background:var(--red-bg);color:var(--red);border-color:#F3C4BF}
  .alert.amber{background:var(--amber-bg);color:var(--amber);border-color:#F0D9A8}
  .alert.neutral{background:var(--card);color:var(--ink-2);border-color:var(--line-2);font-weight:500}
  .alert[aria-pressed="true"]{outline:2px solid currentColor;outline-offset:1px}

  /* Tablero */
  .board-wrap{overflow-x:auto;padding-bottom:8px}
  .board{display:flex;gap:16px;align-items:flex-start;min-width:min-content}
  .col{width:268px;flex:0 0 268px;display:flex;flex-direction:column;gap:10px}
  .col-head{padding:0 4px 6px;border-bottom:2px solid var(--line-2)}
  .col-title{display:flex;align-items:center;gap:8px;white-space:nowrap}
  .dot{width:9px;height:9px;border-radius:999px}
  .col-name{font-size:15px;font-weight:600}
  .col-count{margin-left:auto;font-size:13px;font-weight:600;color:var(--ink-2)}
  .col-meta{font-size:12px;color:var(--ink-3);margin-top:4px}
  .more{height:40px;border:1px dashed #D6D1CA;background:transparent;border-radius:10px;
        font-size:13px;color:var(--ink-2);cursor:pointer}
  .empty{font-size:13px;color:var(--ink-3);padding:12px 4px}

  /* Tarjeta */
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;
        display:flex;flex-direction:column;gap:10px;box-shadow:0 1px 2px rgba(28,27,25,.04);cursor:pointer}
  .card:hover{border-color:#D6D1CA}
  .card-top{display:flex;gap:12px;align-items:flex-start}
  .who{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px}
  .name{font-size:15px;font-weight:600;line-height:1.3;
        display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .role{font-size:13px;color:var(--ink-2)}
  .score{flex:0 0 auto;width:46px;height:46px;border-radius:12px;display:flex;align-items:center;
         justify-content:center;font-size:19px;font-weight:700}
  .t-green{background:var(--green-bg);color:var(--green)}
  .t-amber{background:var(--amber-bg);color:var(--amber)}
  .t-red{background:var(--red-bg);color:var(--red)}
  .t-gray{background:var(--gray-bg);color:var(--gray)}
  .next{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:500}
  .why{font-size:12.5px;line-height:1.45;color:var(--ink-2);background:var(--bg);border-radius:8px;padding:8px 10px}
  .exp-row{display:flex;justify-content:space-between;font-size:12px;color:var(--ink-2)}
  .exp-row b{color:var(--ink)}
  .bar{height:6px;background:#EDEAE5;border-radius:999px;overflow:hidden;margin-top:5px}
  .bar span{display:block;height:100%;border-radius:999px}
  .foot{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
  .chip{display:inline-flex;align-items:center;height:24px;padding:0 9px;border-radius:999px;font-size:12px;font-weight:600}
  .days{margin-left:auto;display:inline-flex;align-items:center;gap:4px;font-size:12px;font-weight:500;color:var(--ink-3)}
  .days.stuck{color:var(--amber);font-weight:600}

  @media (max-width:640px){ .page{padding:20px 16px} .search{min-width:0;flex:1} }
</style>
</head>
<body>
<div class="page">
  <div class="top">
    <div>
      <h1>Candidatos</h1>
      <div class="sub" id="subtitle"></div>
    </div>
    <div class="controls">
      <label class="field search">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
        <input id="q" type="text" placeholder="Buscar por nombre" aria-label="Buscar por nombre">
      </label>
      <select id="vacancy" class="field" aria-label="Vacante"></select>
      <select id="sort" class="field" aria-label="Ordenar">
        <option value="score">Ordenar: Score</option>
        <option value="days">Ordenar: Días en etapa</option>
      </select>
    </div>
  </div>

  <div class="alerts" id="alerts"></div>

  <div class="board-wrap"><div class="board" id="board"></div></div>
</div>

<script>
/* ===== Configuración ===== */
const STUCK_DAYS = 7;            // umbral para marcar "atorado"
const CARDS_PER_COLUMN = 4;      // tarjetas visibles antes de "Ver N más"

const STAGES = [
  { id: 'prefiltro',     name: 'Prefiltro',        color: '#8E8880' },
  { id: 'filtro_ia',     name: 'Filtro Red Human', color: '#D2453D' },
  { id: 'filtro_humano', name: 'Filtro humano',    color: '#C2410C' },
  { id: 'contratacion',  name: 'Contratación',     color: '#B7791F' },
  { id: 'onboarding',    name: 'Onboarding',       color: '#2E8A57' },
];
const CHANNEL = { whatsapp: 'WhatsApp', web: 'Web', referido: 'Referido' };
const STATUS  = { cumple: ['Cumple','t-green'], revisar: ['Revisar','t-amber'], no_cumple: ['No cumple','t-red'] };

/* ===== Datos de ejemplo (en producción vienen de la API) ===== */
const daysAgo = n => new Date(Date.now() - n * 864e5).toISOString();
const VACANCIES = [
  { id: 'v_gte_planta', name: 'Gerente de planta' },
  { id: 'v_ing_cal',    name: 'Ingeniero de calidad' },
  { id: 'v_op_prod',    name: 'Operador de producción' },
  { id: 'v_conta',      name: 'Contador general' },
  { id: 'v_admin',      name: 'Gerente de administración' },
  { id: 'v_mtto',       name: 'Técnico de mantenimiento' },
];
const STATS = [
  { stage: 'prefiltro',     total: 113, conversion_pct: null, avg_days: 2.1 },
  { stage: 'filtro_ia',     total: 107, conversion_pct: 95,   avg_days: 3.4 },
  { stage: 'filtro_humano', total: 46,  conversion_pct: 43,   avg_days: 5.8 },
  { stage: 'contratacion',  total: 29,  conversion_pct: 63,   avg_days: 4.1 },
  { stage: 'onboarding',    total: 12,  conversion_pct: 41,   avg_days: 5.0 },
];
const C = (o) => ({ score: null, filter_status: null, score_reason: null, has_consent: true, expediente_pct: null, ...o, stage_entered_at: daysAgo(o.days) });
const CANDIDATES = [
  C({ id:'1',  name:'Ximena Vargas Valdés',        role:'Gerente de planta',        vacancy_id:'v_gte_planta', stage:'prefiltro', source_channel:'whatsapp', next_step:'Contestar prefiltro', days:11, has_consent:false }),
  C({ id:'2',  name:'Óscar Vargas Trejo',          role:'Gerente de planta',        vacancy_id:'v_gte_planta', stage:'prefiltro', source_channel:'web',      next_step:'Contestar prefiltro', days:4,  has_consent:false }),
  C({ id:'3',  name:'Ricardo Vargas Escobar',      role:'Ingeniero de calidad',     vacancy_id:'v_ing_cal',    stage:'prefiltro', source_channel:'whatsapp', next_step:'Subir CV',            days:2 }),
  C({ id:'4',  name:'Daniela Vargas Espinoza',     role:'Ingeniera de calidad',     vacancy_id:'v_ing_cal',    stage:'prefiltro', source_channel:'web',      next_step:'Contestar prefiltro', days:1 }),
  C({ id:'5',  name:'Isabel Morales Figueroa',     role:'Técnica de mantenimiento', vacancy_id:'v_mtto',       stage:'prefiltro', source_channel:'whatsapp', next_step:'Contestar prefiltro', days:3 }),

  C({ id:'6',  name:'Ana Sofía Cruz Padilla',      role:'Gerente de planta',        vacancy_id:'v_gte_planta', stage:'filtro_ia', source_channel:'whatsapp', next_step:'Entrevista humana', days:3, score:87, filter_status:'cumple',  score_reason:{fortaleza:'8 años dirigiendo planta', faltante:'certificación Lean'} }),
  C({ id:'7',  name:'Adrián Vargas Ibarra',        role:'Gerente de planta',        vacancy_id:'v_gte_planta', stage:'filtro_ia', source_channel:'web',      next_step:'Entrevista humana', days:5, score:74, filter_status:'revisar', score_reason:{fortaleza:'ISO 9001', faltante:'manejo de equipos grandes'} }),
  C({ id:'8',  name:'Santiago Vargas Fuentes',     role:'Ingeniero de calidad',     vacancy_id:'v_ing_cal',    stage:'filtro_ia', source_channel:'referido', next_step:'Entrevista humana', days:9, score:72, filter_status:'revisar', score_reason:{fortaleza:'Six Sigma', faltante:'inglés técnico'} }),
  C({ id:'9',  name:'Lucía Vargas Rosales',        role:'Ingeniera de calidad',     vacancy_id:'v_ing_cal',    stage:'filtro_ia', source_channel:'web',      next_step:'Decidir descarte',  days:2, score:59, filter_status:'no_cumple', score_reason:{fortaleza:'disponibilidad inmediata', faltante:'experiencia en manufactura'} }),
  C({ id:'10', name:'Adrián Morales Cortés',       role:'Técnico de mantenimiento', vacancy_id:'v_mtto',       stage:'filtro_ia', source_channel:'whatsapp', next_step:'Entrevista humana', days:1, score:80, filter_status:'cumple',  score_reason:{fortaleza:'PLC Allen-Bradley', faltante:'turno nocturno'} }),

  C({ id:'11', name:'Ricardo Rivera Padilla',      role:'Gerente de administración', vacancy_id:'v_admin',     stage:'filtro_humano', source_channel:'referido', next_step:'Enviar condiciones',   days:2,  score:97, filter_status:'cumple', score_reason:{fortaleza:'liderazgo de 12 personas', faltante:'SAP'} }),
  C({ id:'12', name:'Daniela Flores Ibarra',       role:'Contadora general',        vacancy_id:'v_conta',      stage:'filtro_humano', source_channel:'whatsapp', next_step:'Enviar condiciones',   days:4,  score:94, filter_status:'cumple' }),
  C({ id:'13', name:'Ana Sofía Vargas Bautista',   role:'Técnica de mantenimiento', vacancy_id:'v_mtto',       stage:'filtro_humano', source_channel:'web',      next_step:'Enviar condiciones',   days:6,  score:82, filter_status:'cumple' }),
  C({ id:'14', name:'Daniela Morales Delgado',     role:'Operadora de producción',  vacancy_id:'v_op_prod',    stage:'filtro_humano', source_channel:'whatsapp', next_step:'Confirmar entrevista', days:12, score:82, filter_status:'cumple', score_reason:{fortaleza:'4 años en ensamble', faltante:'montacargas'} }),
  C({ id:'15', name:'Ricardo Flores Bautista',     role:'Contador general',         vacancy_id:'v_conta',      stage:'filtro_humano', source_channel:'web',      next_step:'Enviar condiciones',   days:3,  score:79, filter_status:'revisar' }),

  C({ id:'16', name:'Miguel Ángel Cruz Rangel',    role:'Gerente de planta',        vacancy_id:'v_gte_planta', stage:'contratacion', source_channel:'referido', next_step:'Firmar carta intención', days:3, score:91, expediente_pct:67 }),
  C({ id:'17', name:'Ricardo Morales Montes',      role:'Operador de producción',   vacancy_id:'v_op_prod',    stage:'contratacion', source_channel:'whatsapp', next_step:'Firmar carta intención', days:1, score:85, expediente_pct:100 }),
  C({ id:'18', name:'Lucía Rivera Rangel',         role:'Gerente de administración', vacancy_id:'v_admin',     stage:'contratacion', source_channel:'web',      next_step:'Pedir consentimiento',   days:8, score:82, expediente_pct:40, has_consent:false }),
  C({ id:'19', name:'Isabel Vargas Miranda',       role:'Ingeniera de calidad',     vacancy_id:'v_ing_cal',    stage:'contratacion', source_channel:'web',      next_step:'Firmar carta intención', days:2, score:76, expediente_pct:83 }),

  C({ id:'20', name:'Óscar Ramírez Rangel',        role:'Auxiliar administrativo',  vacancy_id:'v_admin',      stage:'onboarding', source_channel:'web',      next_step:'Inducción · día 4 de 5', days:4, score:97 }),
  C({ id:'21', name:'Ricardo Torres Soto',         role:'Ejecutivo de recursos humanos', vacancy_id:'v_admin', stage:'onboarding', source_channel:'referido', next_step:'Inducción · día 2 de 5', days:2, score:91 }),
  C({ id:'22', name:'Miguel Ángel Vargas Cervantes', role:'Técnico de mantenimiento', vacancy_id:'v_mtto',     stage:'onboarding', source_channel:'whatsapp', next_step:'Inducción · día 3 de 5', days:3, score:85 }),
  C({ id:'23', name:'Santiago Flores Espinoza',    role:'Contador general',         vacancy_id:'v_conta',      stage:'onboarding', source_channel:'web',      next_step:'Asignar mentor',         days:9, score:79 }),
];

/* ===== Reglas ===== */
const daysInStage = c => Math.floor((Date.now() - new Date(c.stage_entered_at)) / 864e5);
const isStuck     = c => daysInStage(c) > STUCK_DAYS;
const expIncomplete = c => c.stage === 'contratacion' && c.expediente_pct != null && c.expediente_pct < 100;
const tier = s => s == null ? 't-gray' : s >= 85 ? 't-green' : s >= 70 ? 't-amber' : 't-red';
const esc = s => String(s).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));

const ALERTS = [
  { id: 'consent', cls: 'red',     test: c => !c.has_consent, label: n => `${n} sin consentimiento`, icon: 'warn' },
  { id: 'stuck',   cls: 'amber',   test: isStuck,             label: n => `${n} atorados más de ${STUCK_DAYS} días`, icon: 'clock' },
  { id: 'exp',     cls: 'neutral', test: expIncomplete,       label: n => `${n} expedientes incompletos` },
];

/* ===== Estado ===== */
const state = { q: '', vacancy: 'all', sort: 'score', alert: null };

/* ===== Íconos ===== */
const ICON = {
  arrow: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6B665F" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M13 6l6 6-6 6"/></svg>',
  clock: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  warn:  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 3l9.5 17h-19z"/><path d="M12 10v4"/><path d="M12 17.5v.01"/></svg>',
};

/* ===== Render ===== */
function cardHTML(c) {
  const d = daysInStage(c), stuck = isStuck(c);
  const chips = [];
  if (c.filter_status) { const [l, t] = STATUS[c.filter_status]; chips.push(`<span class="chip ${t}">${l}</span>`); }
  if (!c.has_consent) chips.push(`<span class="chip t-red">Sin consentimiento</span>`);

  const why = c.score_reason
    ? `<div class="why">Fuerte: ${esc(c.score_reason.fortaleza)} · Falta: ${esc(c.score_reason.faltante)}</div>` : '';

  const exp = (c.stage === 'contratacion' && c.expediente_pct != null)
    ? `<div><div class="exp-row"><span>Expediente</span><b>${c.expediente_pct}%</b></div>
         <div class="bar"><span style="width:${c.expediente_pct}%;background:${c.expediente_pct >= 100 ? '#2E8A57' : '#C98A12'}"></span></div></div>` : '';

  return `
  <article class="card" tabindex="0" data-id="${esc(c.id)}">
    <div class="card-top">
      <div class="who">
        <div class="name">${esc(c.name)}</div>
        <div class="role">${esc(c.role)} · ${CHANNEL[c.source_channel] || ''}</div>
      </div>
      <div class="score ${tier(c.score)}" aria-label="Score">${c.score == null ? '—' : c.score}</div>
    </div>
    <div class="next">${ICON.arrow}<span>${esc(c.next_step)}</span></div>
    ${why}${exp}
    <div class="foot">
      ${chips.join('')}
      <span class="days ${stuck ? 'stuck' : ''}">${ICON.clock}${d} d${stuck ? ' · atorado' : ''}</span>
    </div>
  </article>`;
}

function filtered() {
  const q = state.q.trim().toLowerCase();
  const alert = ALERTS.find(a => a.id === state.alert);
  return CANDIDATES.filter(c =>
    (state.vacancy === 'all' || c.vacancy_id === state.vacancy) &&
    (!q || c.name.toLowerCase().includes(q)) &&
    (!alert || alert.test(c)));
}

function sorter(a, b) {
  if (state.sort === 'days') return daysInStage(b) - daysInStage(a);
  return (b.score ?? -1) - (a.score ?? -1);
}

function render() {
  const list = filtered();
  const scope = CANDIDATES.filter(c => state.vacancy === 'all' || c.vacancy_id === state.vacancy);

  document.getElementById('subtitle').textContent =
    `${STATS.reduce((n, s) => n + s.total, 0)} en proceso · ${VACANCIES.length} vacantes abiertas`;

  document.getElementById('alerts').innerHTML = ALERTS.map(a => {
    const n = scope.filter(a.test).length;
    if (!n) return '';
    return `<button class="alert ${a.cls}" data-alert="${a.id}" aria-pressed="${state.alert === a.id}">
              ${a.icon ? ICON[a.icon] : ''}${a.label(n)}</button>`;
  }).join('');

  document.getElementById('board').innerHTML = STAGES.map(st => {
    const s = STATS.find(x => x.stage === st.id) || {};
    const cards = list.filter(c => c.stage === st.id).sort(sorter);
    const shown = cards.slice(0, CARDS_PER_COLUMN);
    const meta = [s.conversion_pct != null ? `${s.conversion_pct}% pasa` : 'Entrada',
                  s.avg_days != null ? `${s.avg_days} d promedio` : null].filter(Boolean).join(' · ');
    const filtering = state.q || state.alert || state.vacancy !== 'all';
    const total = filtering ? cards.length : (s.total ?? cards.length);
    const rest = total - shown.length;
    return `
    <section class="col">
      <header class="col-head">
        <div class="col-title"><span class="dot" style="background:${st.color}"></span>
          <span class="col-name">${st.name}</span><span class="col-count">${total}</span></div>
        <div class="col-meta">${meta}</div>
      </header>
      ${shown.map(cardHTML).join('') || '<div class="empty">Sin candidatos</div>'}
      ${rest > 0 ? `<button class="more">Ver ${rest} más</button>` : ''}
    </section>`;
  }).join('');
}

/* ===== Eventos ===== */
const vacSel = document.getElementById('vacancy');
vacSel.innerHTML = `<option value="all">Vacante: Todas</option>` +
  VACANCIES.map(v => `<option value="${v.id}">${esc(v.name)}</option>`).join('');
vacSel.onchange = e => { state.vacancy = e.target.value; render(); };
document.getElementById('sort').onchange = e => { state.sort = e.target.value; render(); };
document.getElementById('q').oninput = e => { state.q = e.target.value; render(); };
document.getElementById('alerts').onclick = e => {
  const b = e.target.closest('[data-alert]'); if (!b) return;
  state.alert = state.alert === b.dataset.alert ? null : b.dataset.alert; render();
};
document.getElementById('board').onclick = e => {
  const card = e.target.closest('.card');
  if (card) console.log('Abrir candidato', card.dataset.id); // conectar al detalle existente
};

render();
</script>
</body>
</html>
```

---

## 5. Criterios de aceptación

- [ ] A un metro de distancia se distinguen los scores verdes, ámbar y rojos de una columna.
- [ ] Ningún nombre ni siguiente paso se trunca a una línea.
- [ ] Ninguna tarjeta muestra "Pendiente", "Score CV", "match" ni "Demo".
- [ ] Cada candidato sin consentimiento muestra la alerta en su propia tarjeta.
- [ ] Los candidatos que superan el umbral de días se marcan como "atorado".
- [ ] Los chips superiores filtran el tablero y se desactivan con un segundo clic.
- [ ] El filtro por vacante y el orden funcionan en todas las columnas.
- [ ] El encabezado de cada columna muestra conteo, % de conversión y días promedio.
- [ ] Ninguna zona muestra "null", "undefined" o texto de prueba.
- [ ] Al hacer clic en una tarjeta se abre el detalle del candidato que ya existe.
- [ ] El menú lateral ya no tapa "Base de conocimiento".
