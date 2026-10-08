# Red Human — Flujo de pruebas psicométricas (instrucciones de implementación)

## 1. Instrucciones para la IA desarrolladora

> Vas a corregir el flujo para agregar y enviar pruebas psicométricas a un candidato en Red Human.
>
> - **Principio rector: el usuario ve el mínimo de pasos; la complejidad se queda en el sistema.** No agregues estados, campos ni botones que no estén en este documento.
> - Reutiliza los modales, botones y componentes que ya existen en el proyecto.
> - **No cambies** la lógica de rutas, etapas ni permisos fuera de lo que aquí se indica.
> - Revisa los criterios de aceptación (sección 8) antes de terminar.

---

## 2. Problema actual

1. El reclutador agrega la actividad "Psicométrica" a la ruta del candidato.
2. Después abre "Iniciar: Psicométrica" y descubre que el catálogo está vacío. No hay forma de continuar: solo puede cancelar.

Además:
- El texto "Listo" en rojo aparece aunque no se haya elegido ninguna batería y no se entiende si es estado o botón.
- "Agregar prueba externa" es la única salida y está como enlace discreto.
- El botón deshabilitado en rosa parece activo pero apagado.
- El estado "Programada / Enviada" mezcla dos situaciones.
- Agregar la actividad a la ruta parece equivaler a haber enviado la prueba, y no es así.

---

## 3. Flujo nuevo (un solo modal)

Al elegir **Psicométrica** en "Agregar actividad a este candidato", el mismo modal muestra la selección de la prueba. No hay un segundo modal separado.

**Modal "Agregar actividad a este candidato"**

| Campo | Comportamiento |
|---|---|
| Actividad del catálogo | Selector. Al elegir "Psicométrica" aparece el bloque de prueba (abajo). |
| Nombre | Renombrar a **"Nombre (opcional)"**. Si se deja vacío, usa el nombre de la actividad. |
| Etapa | Igual que hoy. |
| Obligatoria | Igual que hoy. Texto: *"Obligatoria (el candidato no puede avanzar de etapa sin completarla)"*. |
| **Prueba o batería** (solo Psicométrica) | Buscador con el catálogo de pruebas. |

**Botones del pie**

- **Enviar ahora** (primario): agrega la actividad, asigna la prueba y la envía al candidato. Deshabilitado si no hay prueba elegida.
- **Agregar sin enviar** (secundario): agrega la actividad a la ruta con estado *Sin enviar*. No requiere elegir prueba.
- **Cancelar**.
- **Agregar prueba externa** (botón secundario visible, no enlace): ver sección 6.

**Texto de ayuda bajo el título** (reemplaza los actuales):
> *Solo para esta postulación. No mueve al candidato de columna; si es obligatoria, sí impide avanzarlo hasta completarla.*

**Botón deshabilitado:** gris (no rosa), con la pista debajo: *"Elige una prueba para enviarla"*.

**Quitar** el texto "Listo".

---

## 4. Catálogo vacío

Si no hay pruebas ni baterías configuradas, en lugar del buscador se muestra:

> **Aún no hay pruebas psicométricas configuradas.**

- Si el usuario **es administrador**: botón **"Configurar pruebas"** que lleva a *Configuración → Pruebas psicométricas*.
- Si **no es administrador**: texto *"Pide a tu administrador que agregue pruebas."* sin botón.
- En ambos casos siguen disponibles **"Agregar sin enviar"** y **"Agregar prueba externa"**.
- **No mostrar el buscador** cuando el catálogo está vacío.

**Datos de demo:** las cuentas demo deben traer 2 o 3 baterías de ejemplo precargadas para que el flujo se pueda mostrar completo.

---

## 5. Estados de la actividad (solo tres)

| Estado | Significado | Chip |
|---|---|---|
| **Sin enviar** | La actividad está en la ruta pero la prueba no se ha enviado. | Gris |
| **Enviada** | El candidato recibió la prueba. | Ámbar |
| **Completada** | El candidato la terminó y hay resultado. | Verde |

- **Eliminar** el estado "Programada / Enviada".
- No crear estados "Asignada", "En curso" ni "Vencida".
- **Sin respuesta:** si una prueba lleva más de **5 días** en *Enviada* (umbral configurable), sigue siendo *Enviada* pero se muestra un aviso ámbar: *"Sin respuesta en N días"*.

---

## 6. Acción en la fila de la ruta

En "Avance de la ruta", cada fila de psicométrica muestra su estado y **una sola acción**, según el estado:

| Estado | Acción en la fila |
|---|---|
| Sin enviar | **Enviar prueba** → abre la selección de prueba (mismo bloque de la sección 3). |
| Enviada | **Reenviar** (envía de nuevo el mismo enlace al candidato). |
| Completada | **Ver resultado**; además muestra el resultado resumido en la fila. |

**Prueba externa:** permite registrar el nombre de la prueba y **subir el PDF de resultados**. Al guardar queda directamente en estado *Completada* y la fila muestra "Ver resultado".

---

## 7. Alerta en el tablero Kanban

Si una psicométrica es **obligatoria** y está **Sin enviar**, o **Enviada sin respuesta** más allá del umbral, la tarjeta del candidato en el Kanban muestra un chip:

- `Psicométrica sin enviar` (ámbar), o
- `Psicométrica sin respuesta` (ámbar).

Si el usuario intenta mover al candidato de etapa con una psicométrica obligatoria pendiente, mostrar: *"No puede avanzar: falta completar la psicométrica."* con el botón de la acción correspondiente (Enviar prueba / Reenviar).

---

## 8. Datos

Por actividad psicométrica:

| Campo | Tipo | Uso |
|---|---|---|
| `status` | `sin_enviar` \| `enviada` \| `completada` | Estado |
| `test_id` | string \| null | Prueba o batería del catálogo (null en externa o sin enviar) |
| `is_external` | boolean | Prueba externa |
| `required` | boolean | Obligatoria |
| `sent_at` | ISO date \| null | Calcular días sin respuesta |
| `completed_at` | ISO date \| null | |
| `result_summary` | string \| null | Resumen que se muestra en la fila |
| `result_file_url` | string \| null | PDF de resultados (externa o generado) |

Configuración: `psychometric_no_response_days` (por defecto 5).

---

## 9. Criterios de aceptación

- [ ] Se puede agregar una psicométrica y enviarla en **un solo modal**, sin abrir un segundo modal.
- [ ] "Agregar sin enviar" deja la actividad en estado *Sin enviar* y la fila muestra "Enviar prueba".
- [ ] Con catálogo vacío no aparece el buscador; un administrador ve "Configurar pruebas" y otro usuario ve el aviso para pedirlo.
- [ ] Con catálogo vacío siempre hay una salida: "Agregar sin enviar" o "Agregar prueba externa".
- [ ] No aparece "Listo" en ningún estado.
- [ ] El botón deshabilitado es gris y explica qué falta.
- [ ] Solo existen los estados Sin enviar, Enviada y Completada; "Programada / Enviada" ya no aparece.
- [ ] Una prueba enviada hace más de 5 días muestra "Sin respuesta en N días" y el botón Reenviar.
- [ ] Una prueba externa con PDF queda en *Completada* y se puede ver el resultado.
- [ ] Una psicométrica obligatoria pendiente se ve como alerta en la tarjeta del Kanban y bloquea el avance con un mensaje claro.
- [ ] Las cuentas demo tienen baterías de ejemplo precargadas.
