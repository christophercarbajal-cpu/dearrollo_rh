# Cómo se trabaja en Red Human AI

Este documento existe porque un despliegue directo a producción tiró la
aplicación durante días. Las reglas de abajo no son burocracia: cada una
corresponde a algo que ya falló.

## El flujo

```
tu fork / tu rama  →  Pull Request a christophercarbajal-cpu/dearrollo_rh:main  →  CI verde
                                                                      ↓
                                          /opt/redhuman/redesplegar.sh en el VPS
```

**Nadie despliega a producción desde su propio fork.** El servidor sigue
únicamente `main` del repositorio oficial, y el script de despliegue se niega a
correr si `origin` apunta a otro lado.

### Para colaborar

1. Trabaja en tu fork o en una rama.
2. Abre un Pull Request hacia `main` de `christophercarbajal-cpu/dearrollo_rh`.
3. Espera a que el CI pase (corre solo, tarda un par de minutos).
4. Cuando se apruebe y se integre, quien opere producción ejecuta el despliegue.

### Para desplegar

```bash
ssh root@srv1893825.hstgr.cloud /opt/redhuman/redesplegar.sh
```

El script deja un punto de retorno, actualiza el código, repone la
configuración, reconstruye, reinicia y **verifica**. Si algo queda mal, lo dice y
sale con error. Para revisar producción sin desplegar:

```bash
ssh root@srv1893825.hstgr.cloud /opt/redhuman/verificar.sh
```

### Si un despliegue rompe producción

```bash
ssh -t root@srv1893825.hstgr.cloud /opt/redhuman/revertir.sh
```

Regresa al commit que estaba corriendo antes del último despliegue y reconstruye.
La base **no** se toca: restaurarla descartaría todo lo capturado desde entonces,
así que solo se hace con `--con-base` y a propósito. `--lista` muestra los puntos
de retorno disponibles.

Revertir deja el servidor detrás de `main`. Es una medida temporal: arregla la
causa, súbela por Pull Request y vuelve a desplegar.

## La configuración de producción no vive en el repositorio

Está en el servidor, fuera del árbol de código:

| Archivo | Contiene |
|---|---|
| `/opt/redhuman/config/api.env` | Claves de OpenAI, Anam, Meta; `DATABASE_URL` |
| `/opt/redhuman/config/web.env` | `NEXT_PUBLIC_API_URL` |

Dentro del repositorio, `red-human-api/.env` y `red-human-app/.env.local` son
**enlaces simbólicos** a esos archivos. Un `git reset --hard`, un clon nuevo o un
`rsync` ya no pueden pisar la configuración. Para cambiar algo en producción se
edita el archivo en `/opt/redhuman/config/` y se reinicia el servicio.

`red-human-api/.env.example` documenta las variables; nunca lleva valores reales.

## Dos errores que cuestan caro

**`DATABASE_URL` tiene que ser ruta absoluta** — cuatro barras:

```
DATABASE_URL=sqlite:////opt/redhuman/data/redhuman.db   ✓
DATABASE_URL=sqlite:///./redhuman.db                    ✗ crea otra base vacía
```

Con ruta relativa la aplicación se fabrica una base nueva junto al código, deja
de ver los datos reales y —si el archivo queda de otro usuario— **ninguna
escritura funciona**: nadie puede ni iniciar sesión.

**`NEXT_PUBLIC_API_URL` se compila dentro del navegador.** Tiene que ser la URL
pública, nunca `localhost`: si no, cada visitante intenta llamar a su propia
computadora y el panel queda muerto para todos.

## Lo que el CI revisa en cada PR

- Que toda librería importada esté en `requirements.txt`, **incluidos los imports
  dentro de funciones** (así se coló `pypdf` y reventó la lectura de CVs en PDF).
- Que la aplicación arranque y registre sus rutas.
- Que los endpoints con datos de candidatos exijan sesión (LFPDPPP).
- Que el frontend compile sin errores de tipos.
- Que el bundle del navegador no contenga URLs `localhost`.
- Que no se versione ningún `.env`, base de datos ni CV de candidato.

## Reglas del producto que el código debe respetar

Están en `CLAUDE.md`, pero dos merecen repetirse porque ya se rompieron:

- **El consentimiento se pide, no se asume.** Marcar `consentimiento = True`
  porque alguien eligió una vacante, y registrarlo en la bitácora, es asentar un
  hecho que no ocurrió. La bitácora es el respaldo legal ante la autoridad.
- **Toda decisión la firma una persona identificable.** El actor sale siempre de
  la sesión, nunca de un campo que mande el cliente.
