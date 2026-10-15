"""Entrevista OPERATIVA (2026-10-10, Cambio 1 — sugerida para las rutas Masivos).

En lugar de que la IA invente todo el guion, el enfoque «operativo» ARMA la entrevista con 6 secciones fijas:

  1. Trayectoria               — fija
  2. Verificación del oficio   — de la Biblioteca de Perfiles (3 datos + 1 situación esperada del oficio)
  3. Indispensables            — la IA los redacta UNA vez al generar el guion (máximo 3 se exploran)
  4. Situación actual          — fija
  5. Motivación                — fija
  6. Logística                 — fija: SOLO la fecha de inicio (2026-10-11 tarde: el traslado se pregunta en el prefiltro)

Estructura (Cambio 19, 2026-10-12 — `RANGOS`; reemplaza las 18 preguntas fijas): 1. Trayectoria (último y anterior
trabajo) · 2. Oficio (1 o 2 preguntas concretas del perfil; la situación del oficio solo si es VERIFICABLE, es decir, trae
respuesta esperada) · 3. Evidencia de indispensables (máximo 3; en la conversación se omite la que el candidato ya
demostró) · 4. Situación actual · 5. Motivación · 6. Inicio · 7. Condiciones descriptivas restantes (horario, modalidad).
Cero reglas fijas: lo que se explora sale de los indispensables y condiciones de CADA vacante.

Biblioteca de Perfiles = 10 oficios en código (`BIBLIOTECA`): nunca se editan globalmente. Cada vacante guarda SU copia
(`Vacante.guiones["perfil_operativo"]`) y solo esa copia se ajusta.

Generación (2026-10-11, versión definitiva): `generar_guion` pide a la IA el guion completo con `PROMPT_GENERACION`
(«Eres un reclutador experto en personal operativo en México…»): 6 secciones en orden estricto, una pregunta por línea,
máximo 15 palabras, y condicionales «[Solo si responde sí]» / «[Solo si responde no]» (aplican a la pregunta anterior de sí
o no). `revisar_guion` lo valida (un solo «?», ≤ 15 palabras, sin palabras prohibidas, 6 secciones, sin pedir documentos
ni escolaridad); si falla se regenera con los problemas como retroalimentación (máximo 2 veces) y, si el tercer intento
tampoco pasa, se guarda con `operativo.revision.problemas` para que RH vea qué falló. Sin IA se usa el guion determinista
(`armar_guion`), que cumple las mismas reglas.

Evaluación (Cambio 19, garantizada en código, `evaluar` + `aplicar`): SIN puntaje y SIN umbrales fijos. Criterios A-D
(experiencia, procedimiento, indispensables, logística) como evidencia; la recomendación sale SOLO de la experiencia en
un puesto similar (A con datos concretos) y de los «Puntos por validar» — condiciones de la vacante no cumplidas,
sueldo no aceptado u otra cifra, inconsistencias y salidas por despido o conflicto (nunca habilidades blandas):
Recomendable = experiencia y ningún punto · No recomendable = sin experiencia y con puntos · lo demás (o una respuesta
dudosa) Con reservas. Solo recomienda: NUNCA descarta; la ruta se detiene y RH decide.
"""

import copy
import re
import unicodedata
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

ENFOQUE = "operativo"
# Cambio 19 (2026-10-12): estructura de 7 secciones con cupos por sección (mínimo, máximo); sin total fijo
RANGOS = {"trayectoria": (4, 9), "oficio": (1, 3), "indispensables": (0, 3), "situacion_actual": (1, 3), "motivacion": (1, 1),
          "logistica": (1, 1), "condiciones": (0, 3)}
ESTRUCTURA = tuple((k, mx) for k, (_, mx) in RANGOS.items())  # compatibilidad: (sección, cupo máximo)
MAX_INDISPENSABLES = 3
MAX_PALABRAS = 15
MAX_REGENERACIONES = 2  # 1 intento + 2 regeneraciones; al tercero que falle se guarda con los problemas a la vista de RH
MARCA_SI = "[Solo si responde sí]"
MARCA_NO = "[Solo si responde no]"

# ============================================================ Biblioteca de Perfiles (10 oficios, estándar de la industria)

# `palabras`: evidencia de experiencia en el oficio (además de los sinónimos). `excluyentes`: lo que NO cuenta como
# experiencia en el oficio (texto para RH y para la entrevistadora) y sus `palabras_excluyentes` (detección).
# `situacion.esperado`: elementos de un procedimiento correcto, cada uno con palabras clave para la evaluación sin IA.
BIBLIOTECA: Dict[str, dict] = {
    "almacenista": {
        "nombre": "Almacenista",
        "sinonimos": ["almacenista", "auxiliar de almacen", "encargado de almacen", "almacen", "inventarios", "surtidor"],
        "palabras": ["almacen", "inventario", "entradas", "salidas", "surtir", "surtido", "picking", "racks", "anaquel", "recibo"],
        "excluyentes": ["Acomodar mercancía en piso de tienda sin control de entradas y salidas no cuenta como almacén."],
        "palabras_excluyentes": ["acomodar en tienda", "piso de venta"],
        "datos": [
            "¿Qué tipo de mercancía manejabas en el almacén?",
            "¿Cómo registrabas entradas y salidas: papel, Excel, escáner o sistema?",
            "¿Cada cuándo hacían inventarios o conteos?",
        ],
        "situacion": {
            "pregunta": "Si en un conteo faltan 10 piezas contra el sistema, ¿qué haces?",
            "esperado": [
                {"texto": "Vuelve a contar", "palabras": ["recont", "volver a contar", "vuelvo a contar", "contar de nuevo", "otra vez"]},
                {"texto": "Revisa movimientos y documentos", "palabras": ["revis", "movimiento", "remision", "factura", "salida", "entrada", "document"]},
                {"texto": "Reporta al supervisor", "palabras": ["report", "avis", "supervisor", "jefe", "encargado"]},
                {"texto": "No ajusta sin autorización", "palabras": ["autoriz", "no ajust", "no muevo el sistema"]},
            ],
        },
    },
    "ayudante_general": {
        "nombre": "Ayudante general",
        "sinonimos": ["ayudante general", "auxiliar general", "operario general", "auxiliar de produccion", "ayudante"],
        "palabras": ["ayudante", "fabrica", "planta", "produccion", "linea", "empaque", "limpieza", "carga", "almacen", "obra"],
        "excluyentes": ["Ayudar en casa o a familiares sin un empleo no cuenta como experiencia laboral."],
        "palabras_excluyentes": ["en mi casa", "a mi familia", "a mi papa", "a mi mama"],
        "datos": [
            "¿Qué tareas hacías en un día normal?",
            "¿Qué herramientas o equipo usabas en tu trabajo?",
            "¿Cuántas horas de tu turno pasabas de pie o cargando?",
        ],
        "situacion": {
            "pregunta": "Si te piden algo urgente y falta tu equipo de seguridad, ¿qué haces?",
            "esperado": [
                {"texto": "No empieza sin equipo de seguridad", "palabras": ["no empiez", "no lo hago", "no trabajo sin", "sin equipo no", "primero el equipo"]},
                {"texto": "Avisa al supervisor", "palabras": ["avis", "supervisor", "jefe", "report"]},
                {"texto": "Pide el equipo", "palabras": ["pido", "pedir", "solicit", "consigo"]},
            ],
        },
    },
    "cargador": {
        "nombre": "Cargador",
        "sinonimos": ["cargador", "estibador", "maniobrista", "carga y descarga", "macheteros", "machetero"],
        "palabras": ["cargar", "descargar", "carga", "descarga", "estiba", "camion", "trailer", "bultos", "cajas", "tarimas", "maniobra"],
        "excluyentes": ["Mudanzas ocasionales o cargar sin un empleo formal no cuentan como experiencia de cargador."],
        "palabras_excluyentes": ["mudanza de un amigo", "de vez en cuando"],
        "datos": [
            "¿Cuánto pesaba más o menos cada bulto o caja?",
            "¿Cargabas a mano o con equipo como diablito o patín?",
            "¿Cuántos camiones o tráileres cargaban o descargaban en un turno?",
        ],
        "situacion": {
            "pregunta": "Si al descargar notas cajas dañadas o mojadas, ¿qué haces?",
            "esperado": [
                {"texto": "Separa la mercancía dañada", "palabras": ["separ", "apart", "hago a un lado"]},
                {"texto": "Reporta al supervisor o a recibo", "palabras": ["avis", "report", "supervisor", "encargado", "jefe", "recibo"]},
                {"texto": "Documenta (anota o toma foto)", "palabras": ["anot", "foto", "document", "registr", "evidencia"]},
                {"texto": "No firma de recibido sin anotar", "palabras": ["no firm", "firmar con observ", "nota en la remision"]},
            ],
        },
    },
    "montacarguista": {
        "nombre": "Montacarguista",
        "sinonimos": ["montacarguista", "operador de montacargas", "montacargas", "forklift"],
        "palabras": ["montacargas", "montacarga", "forklift", "contrabalanceado", "retractil", "tarimas", "estiba", "racks", "dc-3", "dc3"],
        "excluyentes": ["Usar patín manual (hidráulico) o diablito NO cuenta como operar montacargas."],
        "palabras_excluyentes": ["patin", "diablito", "patin hidraulico"],
        "datos": [
            "¿Qué montacargas operabas: combustión, eléctrico, retráctil o contrabalanceado?",
            "¿A qué altura acomodabas las tarimas?",
            "¿Qué capacitación formal tienes para operar montacargas, como la DC-3?",
        ],
        "situacion": {
            "pregunta": "Antes de empezar tu turno, ¿qué le revisas al montacargas?",
            "esperado": [
                {"texto": "Hace la inspección previa (checklist)", "palabras": ["checklist", "check list", "inspeccion", "revision previa", "bitacora"]},
                {"texto": "Revisa frenos", "palabras": ["freno"]},
                {"texto": "Revisa claxon, alarma o luces", "palabras": ["claxon", "alarma", "torreta", "luces", "bocina"]},
                {"texto": "Revisa combustible o batería y fluidos", "palabras": ["gas", "combustible", "bateria", "carga", "aceite", "agua", "fuga"]},
                {"texto": "Revisa uñas, llantas y cadenas", "palabras": ["unas", "horquilla", "llanta", "cadena", "mastil"]},
                {"texto": "Si hay falla, lo reporta y no lo opera", "palabras": ["report", "no lo uso", "no lo opero", "aviso"]},
            ],
        },
    },
    "maquilador": {
        "nombre": "Maquilador",
        "sinonimos": ["maquilador", "maquiladora", "operador de produccion", "operadora de produccion", "costurera", "costurero",
                      "operadora de maquina", "operador de maquina", "ensamblador", "ensambladora", "operador de linea"],
        "palabras": ["maquila", "costura", "overlock", "recta", "ensamble", "linea", "produccion", "piezas", "meta", "prensa", "estacion"],
        "excluyentes": ["Coser o ensamblar en casa por tu cuenta, sin metas ni control de calidad, no cuenta igual."],
        "palabras_excluyentes": ["en mi casa", "por mi cuenta"],
        "datos": [
            "¿Qué producto fabricabas o ensamblabas?",
            "¿Qué máquina o estación operabas: recta, overlock, prensa o línea?",
            "¿Cuál era tu meta de producción por turno?",
        ],
        "situacion": {
            "pregunta": "Si las piezas de tu estación salen con un defecto, ¿qué haces?",
            "esperado": [
                {"texto": "Detiene la máquina o la producción", "palabras": ["detengo", "par", "deten", "apag"]},
                {"texto": "Separa las piezas defectuosas", "palabras": ["separ", "apart", "rechaz", "scrap"]},
                {"texto": "Avisa al supervisor o a calidad", "palabras": ["avis", "report", "supervisor", "calidad", "jefe", "lider"]},
            ],
        },
    },
    "chofer": {
        "nombre": "Chofer",
        "sinonimos": ["chofer", "chofer repartidor", "operador de tracto", "conductor", "repartidor", "operador de reparto"],
        "palabras": ["chofer", "manej", "ruta", "reparto", "entregas", "camioneta", "rabon", "torton", "trailer", "licencia", "unidad"],
        "excluyentes": ["Manejar tu auto particular o de plataforma (Uber, DiDi) no cuenta como experiencia de chofer de carga o reparto."],
        "palabras_excluyentes": ["uber", "didi", "mi carro", "mi coche", "plataforma"],
        "datos": [
            "¿Qué tipo de unidad manejabas: camioneta, rabón, tórton o tráiler?",
            "¿Qué tipo de licencia de manejo tienes?",
            "¿Cuántas entregas hacías por día?",
        ],
        "situacion": {
            "pregunta": "Si en la ruta el cliente no está para recibir, ¿qué haces?",
            "esperado": [
                {"texto": "Intenta contactar al cliente", "palabras": ["llam", "marc", "contact", "busco al cliente"]},
                {"texto": "Avisa a su base o supervisor", "palabras": ["avis", "base", "supervisor", "despacho", "jefe", "trafico"]},
                {"texto": "No deja la mercancía sin firma", "palabras": ["no la dejo", "no dejo", "sin firma", "firma"]},
                {"texto": "Registra y reprograma", "palabras": ["anot", "registr", "reprogram", "evidencia", "foto", "sigo con la ruta"]},
            ],
        },
    },
    "cajero": {
        "nombre": "Cajero",
        "sinonimos": ["cajero", "cajera", "cajero de tienda", "cobrador", "caja"],
        "palabras": ["caja", "cobr", "corte", "efectivo", "tarjeta", "terminal", "vales", "ticket", "punto de venta"],
        "excluyentes": ["Cobrar en un negocio familiar sin hacer corte de caja no cuenta igual."],
        "palabras_excluyentes": ["negocio de mi familia", "tienda de mi"],
        "datos": [
            "¿Cuántos clientes atendías al día?",
            "¿Qué formas de pago manejabas: efectivo, tarjeta, vales o transferencias?",
            "¿Cómo hacías tu corte de caja al cerrar?",
        ],
        "situacion": {
            "pregunta": "Si al hacer tu corte te faltan 200 pesos, ¿qué haces?",
            "esperado": [
                {"texto": "Vuelve a contar", "palabras": ["recont", "vuelvo a contar", "contar de nuevo", "otra vez"]},
                {"texto": "Revisa tickets y movimientos", "palabras": ["revis", "ticket", "voucher", "movimiento", "cambio"]},
                {"texto": "Avisa al supervisor", "palabras": ["avis", "report", "supervisor", "encargado", "gerente", "jefe"]},
                {"texto": "No lo oculta", "palabras": ["no lo escondo", "no oculto", "no lo pongo yo", "honest", "transparen"]},
            ],
        },
    },
    "guardia": {
        "nombre": "Guardia de seguridad",
        "sinonimos": ["guardia", "guardia de seguridad", "vigilante", "oficial de seguridad", "seguridad privada"],
        "palabras": ["guardia", "vigil", "rondin", "accesos", "caseta", "bitacora", "seguridad", "custodi"],
        "excluyentes": ["Cuidar un negocio familiar sin funciones formales de vigilancia no cuenta igual."],
        "palabras_excluyentes": ["negocio de mi familia", "cuidaba la casa"],
        "datos": [
            "¿Qué instalación cuidabas: planta, plaza, residencial u oficinas?",
            "¿Qué funciones hacías: accesos, rondines o revisión de vehículos?",
            "¿Qué cursos de seguridad privada has tomado?",
        ],
        "situacion": {
            "pregunta": "Si llega un proveedor que no está en la lista, ¿qué haces?",
            "esperado": [
                {"texto": "No la deja pasar", "palabras": ["no la dejo", "no lo dejo", "no pasa", "esperar afuera", "espere"]},
                {"texto": "Pide identificación", "palabras": ["identific", "ine", "gafete", "credencial"]},
                {"texto": "Verifica con el área o supervisor", "palabras": ["verific", "llam", "confirm", "supervisor", "area", "radio"]},
                {"texto": "Registra en bitácora", "palabras": ["bitacora", "registr", "anot"]},
            ],
        },
    },
    "limpieza": {
        "nombre": "Limpieza",
        "sinonimos": ["limpieza", "intendencia", "intendente", "afanador", "afanadora", "auxiliar de limpieza", "camarista"],
        "palabras": ["limpieza", "intendencia", "trapear", "barrer", "pulidora", "quimicos", "sanitarios", "desinfect", "areas"],
        "excluyentes": ["La limpieza de tu propia casa no cuenta como experiencia laboral."],
        "palabras_excluyentes": ["mi casa", "en casa"],
        "datos": [
            "¿Qué lugar limpiabas: oficinas, hospital, planta o plaza?",
            "¿Qué químicos o máquinas usabas, como pulidora o hidrolavadora?",
            "¿Cómo se organizaban las áreas y horarios de limpieza?",
        ],
        "situacion": {
            "pregunta": "Si encuentras un químico derramado en un pasillo, ¿qué haces?",
            "esperado": [
                {"texto": "Señaliza o acordona el área", "palabras": ["senal", "letrero", "acordon", "aislar", "cono", "piso mojado"]},
                {"texto": "Usa equipo de protección", "palabras": ["guantes", "proteccion", "cubrebocas", "lentes", "equipo"]},
                {"texto": "Identifica el producto", "palabras": ["identific", "hoja de seguridad", "que quimico", "etiqueta"]},
                {"texto": "Avisa al supervisor", "palabras": ["avis", "report", "supervisor", "jefe", "encargado"]},
            ],
        },
    },
    "vendedor_piso": {
        "nombre": "Vendedor de piso",
        "sinonimos": ["vendedor de piso", "vendedora de piso", "asesor de ventas", "asesora de ventas", "vendedor de mostrador",
                      "dependiente", "piso de ventas"],
        "palabras": ["venta", "vend", "clientes", "meta", "piso", "mostrador", "tienda", "comision", "exhibi"],
        "excluyentes": ["Vender por catálogo o por tu cuenta no cuenta igual que piso de ventas."],
        "palabras_excluyentes": ["catalogo", "por mi cuenta", "por mi lado"],
        "datos": [
            "¿Qué producto vendías?",
            "¿Qué meta de venta tenías al mes?",
            "¿Qué hacías además de vender: acomodo, inventario o cobro?",
        ],
        "situacion": {
            "pregunta": "Si un cliente reclama que en caja le cobran más, ¿qué haces?",
            "esperado": [
                {"texto": "Escucha al cliente", "palabras": ["escuch", "disculp", "calma", "atiend"]},
                {"texto": "Verifica el precio", "palabras": ["verific", "revis", "checo", "confirm"]},
                {"texto": "Respeta el precio exhibido (política / PROFECO)", "palabras": ["respet", "precio exhibido", "profeco", "politica", "el menor"]},
                {"texto": "Avisa al encargado y corrige la etiqueta", "palabras": ["encargado", "gerente", "supervisor", "etiqueta", "corrij"]},
            ],
        },
    },
}
OFICIOS = list(BIBLIOTECA)

SECCIONES = (
    ("trayectoria", "Trayectoria", "fijo"),
    ("oficio", "Verificación del oficio", "biblioteca"),
    ("indispensables", "Evidencia de indispensables", "vacante"),
    ("situacion_actual", "Situación actual", "fijo"),
    ("motivacion", "Motivación", "fijo"),
    ("logistica", "Inicio", "fijo"),  # la clave interna sigue siendo «logistica» (guiones guardados)
    ("condiciones", "Condiciones del puesto", "vacante"),
)
TITULO_SECCION = {k: t for k, t, _ in SECCIONES}

CRITERIOS = (
    ("A", "Experiencia en el oficio"),
    ("B", "Procedimiento"),
    ("C", "Indispensables"),
    ("D", "Logística"),
)
RESULTADOS = ("cumple", "parcial", "no_cumple", "sin_dato")
TEXTO_RESULTADO = {"cumple": "Cumple", "parcial": "Parcial", "no_cumple": "No cumple", "sin_dato": "Sin dato"}
# Cambio 53 (2026-10-15): textos visibles «Avanzar» / «Avanzar con reservas» / «No avanzar» (claves internas sin cambio)
RECOMENDACIONES = {"recomendable": ("Avanzar", "avanzar"),
                   "con_reservas": ("Avanzar con reservas", "revision"),
                   "no_recomendable": ("No avanzar", "no_avanzar")}
# Cambio 19 (2026-10-12): SIN puntaje y SIN umbrales fijos (se eliminaron traslado ≤ 90/120 min, inicio ≤ 14 días y
# empleo < 3 meses). La recomendación sale de dos requisitos: experiencia en un puesto similar con datos concretos y
# ningún «Punto por validar».
REQUISITOS = (("experiencia", "Experiencia en puesto similar"), ("puntos", "Sin puntos por validar"))
SIMBOLO = {"cumple": "✓", "no_cumple": "✗", "sin_dato": "?", "no_aplica": "—"}


def _n(t: str) -> str:
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9\s\-]", " ", t).split())


def _contiene(texto: str, palabras) -> bool:
    t = f" {_n(texto)} "
    return any(f" {_n(p)}" in t for p in palabras if _n(p))


# ============================================================ perfil de la vacante (copia editable SOLO por vacante)


def detectar_oficio(titulo: str, responsabilidades: Optional[List[str]] = None) -> Optional[str]:
    """Oficio de la biblioteca que corresponde al puesto (por sus sinónimos; el título manda sobre las
    responsabilidades y el sinónimo más largo gana: «chofer repartidor» antes que «repartidor»)."""
    for fuente in [titulo or ""] + list(responsabilidades or []):
        t = f" {_n(fuente)} "
        mejor, largo = None, 0
        for clave, o in BIBLIOTECA.items():
            for s in o["sinonimos"]:
                s2 = _n(s)
                if s2 and f" {s2} " in t and len(s2) > largo:
                    mejor, largo = clave, len(s2)
        if mejor:
            return mejor
    return None


def perfil_base(oficio: str) -> dict:
    """COPIA del perfil de la biblioteca (nunca la referencia: la biblioteca no se edita)."""
    if oficio not in BIBLIOTECA:
        raise ValueError(f"Oficio desconocido: {oficio}")
    return {"oficio": oficio, **copy.deepcopy(BIBLIOTECA[oficio]), "editado": False}


def _textos(xs, n: int = 300, maximo: int = 10) -> List[str]:
    return [" ".join(str(x).split())[:n] for x in (xs or []) if str(x or "").strip()][:maximo]


def normalizar_perfil(entrada: dict) -> dict:
    """Perfil ajustado POR VACANTE: parte del de la biblioteca y solo cambia lo que RH mandó. Exige 3 datos y 1 situación."""
    if not isinstance(entrada, dict) or entrada.get("oficio") not in BIBLIOTECA:
        raise ValueError(f"Elige un oficio de la biblioteca: {', '.join(o['nombre'] for o in BIBLIOTECA.values())}.")
    base = perfil_base(entrada["oficio"])
    salida = dict(base)
    editado = False
    if "datos" in entrada:
        datos = _textos(entrada.get("datos"), maximo=3)
        if len(datos) != 3:
            raise ValueError("El perfil del oficio lleva exactamente 3 datos a verificar.")
        editado |= datos != base["datos"]
        salida["datos"] = datos
    if "situacion" in entrada:
        s = entrada.get("situacion") or {}
        pregunta = " ".join(str(s.get("pregunta") or "").split())[:300]
        if not pregunta:
            raise ValueError("El perfil del oficio lleva 1 situación esperada (pregunta).")
        esperado = []
        for x in s.get("esperado") or []:
            if isinstance(x, str) and x.strip():
                x = {"texto": x.strip()[:160], "palabras": [w for w in _n(x).split() if len(w) > 4][:4]}
            if isinstance(x, dict) and str(x.get("texto") or "").strip():
                esperado.append({"texto": str(x["texto"]).strip()[:160], "palabras": _textos(x.get("palabras"), 40, 12)})
        if not esperado and "esperado" not in s:
            esperado = copy.deepcopy(base["situacion"]["esperado"])  # solo cambió la pregunta: se conserva lo esperado
        if not esperado:
            raise ValueError("La situación necesita al menos un elemento de respuesta esperada.")
        nueva = {"pregunta": pregunta, "esperado": esperado[:8]}
        editado |= nueva != base["situacion"]
        salida["situacion"] = nueva
    for campo in ("sinonimos", "excluyentes"):
        if campo in entrada:
            valores = _textos(entrada.get(campo), 160, 12)
            editado |= valores != base[campo]
            salida[campo] = valores
    salida["editado"] = editado
    return salida


def perfil_de_vacante(v) -> Optional[dict]:
    """El perfil guardado en la vacante o, si no hay, el de la biblioteca que corresponde a su puesto."""
    guardado = ((getattr(v, "guiones", None) or {}).get("perfil_operativo")) if v is not None else None
    if isinstance(guardado, dict) and guardado.get("oficio") in BIBLIOTECA:
        return guardado
    oficio = detectar_oficio(getattr(v, "titulo", "") or "", list(getattr(v, "responsabilidades", None) or []))
    return perfil_base(oficio) if oficio else None


def biblioteca_publica() -> List[dict]:
    return [{"oficio": k, "nombre": o["nombre"], "sinonimos": o["sinonimos"], "excluyentes": o["excluyentes"], "datos": o["datos"],
             "situacion": {"pregunta": o["situacion"]["pregunta"], "esperado": [x["texto"] for x in o["situacion"]["esperado"]]}}
            for k, o in BIBLIOTECA.items()]


# ============================================================ ensamblaje de las 6 secciones


def palabras(texto: str) -> int:
    return len(str(texto or "").split())


def _recorta(texto: str, n: int) -> str:
    ws = str(texto or "").split()
    return " ".join(ws[:n])


def pregunta_indispensable_demo(req: str) -> str:
    """Compatibilidad: la pregunta de EVIDENCIA de un indispensable (`pregunta_evidencia`)."""
    return pregunta_evidencia(req)


def _minuscula(req: str) -> str:
    r = " ".join(str(req or "").split()).strip().rstrip(".")
    return r[0].lower() + r[1:] if len(r) > 1 and not r[:2].isupper() else r


def pregunta_evidencia(req: str) -> str:
    """Cambio 19: UNA pregunta abierta que pide EVIDENCIA del indispensable (dónde lo ha hecho), una sola idea, ≤ 15
    palabras. «Cargar cajas de 25 kg» → «¿En qué trabajo te tocó cargar cajas de 25 kg?»."""
    from . import ia

    r = _minuscula(req)
    t = _n(r)
    if "experiencia" in t:
        tema = ia._tema_experiencia(r)
        q = f"¿Dónde adquiriste tu {ia._de_experiencia(tema)[3:]}?"
    elif re.match(r"^conocimientos? (de|en|sobre) ", t):
        q = f"¿Dónde aprendiste {re.sub(r'^conocimientos? (de|en|sobre) ', '', r, flags=re.I)}?"
    elif re.match(r"^(licencia|constancia|certificaci\w*|certificado|curso|carnet|cartilla)\b", t):
        q = f"¿Desde cuándo tienes {r}?"
    elif re.match(r"^disponibilidad (para|de) ", t):
        q = f"¿En qué trabajo anterior te tocó {re.sub(r'^disponibilidad (para|de) ', '', r, flags=re.I)}?"
    elif re.fullmatch(r"[a-z]+(ar|er|ir)(se)?", (t.split() or [""])[0]):
        q = f"¿En qué trabajo te tocó {r}?"
    else:
        q = f"¿Dónde has cumplido antes con esto: {r}?"
    return _si_cabe(q, "¿En qué trabajo anterior cumpliste este requisito?")


class IndispensablesIA(BaseModel):
    preguntas: List[str] = Field(description="Una pregunta ABIERTA por requisito, en el MISMO orden, español mexicano, corta, "
                                             "que explore cómo lo cumple (dónde, cuánto tiempo, un ejemplo). Nunca de sí/no.")


def redactar_indispensables(titulo: str, indispensables: List[str]) -> Tuple[List[str], bool]:
    """La IA redacta UNA vez (al generar el guion) las preguntas de los indispensables a explorar — máximo 3. Sin IA,
    redacción determinista. Nunca repite la pregunta cerrada del prefiltro: explora cómo lo cumple."""
    from . import ia

    reqs = [r for r in (indispensables or []) if str(r or "").strip()][:MAX_INDISPENSABLES]
    if not reqs:
        return [], False
    client = ia._client()
    if client is not None:
        try:
            resp = client.responses.parse(
                model=ia.MODEL,
                instructions=("Redactas preguntas de entrevista OPERATIVA (puestos masivos en México). Por cada requisito indispensable "
                              "escribe UNA pregunta abierta, corta y sencilla, que explore CÓMO lo cumple (dónde, cuánto tiempo, un "
                              "ejemplo concreto). El candidato ya contestó en el prefiltro que lo cumple: no le preguntes si lo tiene. "
                              f"PROHIBIDO preguntar sobre: {ia.DATOS_SENSIBLES_PROHIBIDOS}."),
                input=f"Puesto: {titulo}\nRequisitos indispensables:\n" + "\n".join(f"- {r}" for r in reqs),
                text_format=IndispensablesIA,
            )
            preguntas = ia.abrir_preguntas([q for q in resp.output_parsed.preguntas if q.strip()])
            if len(preguntas) == len(reqs):
                return preguntas, True
        except Exception as ex:  # noqa: BLE001 — sin IA disponible se redacta en modo determinista
            print(f"[operativa] redactar_indispensables cayó a modo determinista: {str(ex)[:200]}", flush=True)
    return [pregunta_indispensable_demo(r) for r in reqs], False


def _si_cabe(pregunta: str, alterna: str) -> str:
    return pregunta if palabras(pregunta) <= MAX_PALABRAS else alterna


PREGUNTA_INICIO = "¿Cuándo podrías empezar a trabajar?"


def preguntas_logistica(ubicacion: str = "", horario: str = "") -> List[str]:
    """6. Inicio = SOLO la fecha de inicio (zona, traslado, transporte y sueldo ya se preguntan en el prefiltro)."""
    return [PREGUNTA_INICIO]


def preguntas_condiciones(horario: str = "", modalidad: str = "") -> List[str]:
    """7. Condiciones descriptivas restantes (Cambio 19): lo que la vacante describe y el prefiltro de WhatsApp no
    pregunta — horario y modalidad (si no es presencial). Sin datos capturados, la sección no existe."""
    salida = []
    if (horario or "").strip():
        salida.append(_si_cabe(f"¿Puedes trabajar en el horario {horario.strip()}?", "¿Puedes trabajar en el horario del puesto?"))
    mod = (modalidad or "").strip()
    if mod and not mod.lower().startswith("presencial"):
        salida.append(_si_cabe(f"¿Puedes trabajar en modalidad {mod.lower()}?", "¿Puedes trabajar en la modalidad del puesto?"))
    return salida[:RANGOS["condiciones"][1]]


def indispensables_de_entrevista(indispensables: List[str]) -> List[str]:
    """Indispensables que se exploran en la entrevista: nunca escolaridad ni documentos (se validan en el prefiltro y en
    Documentos)."""
    return [r for r in (indispensables or []) if str(r or "").strip() and not es_alerta_escolaridad(r)][:MAX_INDISPENSABLES]


def preguntas_indispensables(reqs: List[str], redactadas: Optional[List[str]] = None) -> List[str]:
    """3. Evidencia de indispensables (Cambio 19): UNA pregunta de evidencia por indispensable, en el mismo orden que
    `requisitos` (máximo 3). Sin indispensables por explorar, la sección no existe."""
    redactadas = [q for q in (redactadas or []) if str(q or "").strip()]
    return [(redactadas[i] if i < len(redactadas) else pregunta_evidencia(r)) for i, r in enumerate(reqs[:MAX_INDISPENSABLES])]


def condicion_de(pregunta: str) -> Tuple[str, str]:
    """(«si» | «no» | «», texto sin la marca). La condición aplica a la pregunta ANTERIOR (que es de sí o no)."""
    t = " ".join(str(pregunta or "").split())
    m = _RE_MARCA.match(t)
    if m:
        return ("no" if m.group(1).lower() == "no" else "si"), t[m.end():].strip()
    return "", t


_RE_MARCA = re.compile(r"^\[\s*solo si (?:responde|contesta|dice)\s+(s[ií]|no)\s*\]\s*", re.I)


def con_condicion(texto: str, condicion: str) -> str:
    texto = " ".join(str(texto or "").split())
    return f"{MARCA_SI if condicion == 'si' else MARCA_NO} {texto}" if condicion in ("si", "no") else texto


def texto_pregunta(pregunta: str) -> str:
    return condicion_de(pregunta)[1]


def situacion_verificable(perfil: Optional[dict]) -> str:
    """La situación del oficio solo entra si es VERIFICABLE: trae la pregunta Y los elementos de respuesta esperada."""
    s = (perfil or {}).get("situacion") or {}
    return str(s.get("pregunta") or "").strip() if s.get("esperado") else ""


def armar_guion(titulo: str, perfil: Optional[dict], indispensables: List[str], preguntas_indisp: List[str], *,
                ubicacion: str = "", horario: str = "", modalidad: str = "") -> dict:
    """Guion operativo ARMADO (sin IA): 7 secciones en orden fijo, cada pregunta con UNA sola idea. Regresa el formato de
    guion de siempre (`enfoque`, `temas`, `preguntas`) + `operativo` (secciones, oficio, perfil congelado)."""
    puesto = (titulo or "").strip() or "este puesto"
    reqs = indispensables_de_entrevista(indispensables)
    secciones = []
    # 1. Trayectoria: el último trabajo y el anterior (las condicionales dependen de «¿Tuviste otro trabajo antes?»)
    secciones.append({"clave": "trayectoria", "titulo": TITULO_SECCION["trayectoria"], "origen": "fijo", "preguntas": [
        "¿Cuál fue tu último trabajo?",  # va en el mismo mensaje de la transición
        "¿Qué hacías en ese trabajo?",
        "¿Cuánto tiempo trabajaste ahí?",
        "¿Por qué saliste de ese trabajo?",
        "¿Tuviste otro trabajo antes de ese?",
        con_condicion("¿Qué hacías en ese trabajo anterior?", "si"),
        con_condicion("¿Cuánto tiempo estuviste ahí?", "si"),
        con_condicion("¿Por qué dejaste ese empleo?", "si"),
    ]})
    # 2. Oficio: 1-2 preguntas concretas; la situación solo si es verificable
    if perfil:
        oficio = list(perfil.get("datos") or [])[:2] + [situacion_verificable(perfil)]
    else:
        oficio = [_si_cabe(f"¿En qué trabajo hiciste tareas de {puesto}?", "¿En qué trabajo hiciste tareas parecidas a este puesto?")]
    secciones.append({"clave": "oficio", "titulo": TITULO_SECCION["oficio"], "origen": "biblioteca" if perfil else "fijo",
                      "preguntas": [q for q in oficio if q.strip()]})
    # 3. Evidencia de indispensables (máximo 3; solo si la vacante los tiene)
    if reqs:
        secciones.append({"clave": "indispensables", "titulo": TITULO_SECCION["indispensables"], "origen": "vacante",
                          "preguntas": preguntas_indispensables(reqs, preguntas_indisp), "requisitos": reqs})
    secciones.append({"clave": "situacion_actual", "titulo": TITULO_SECCION["situacion_actual"], "origen": "fijo", "preguntas": [
        "¿Estás trabajando actualmente?",
        con_condicion("¿Por qué quieres cambiar de trabajo?", "si"),
        con_condicion("¿Desde cuándo no trabajas?", "no"),
    ]})
    secciones.append({"clave": "motivacion", "titulo": TITULO_SECCION["motivacion"], "origen": "fijo",
                      "preguntas": [_si_cabe(f"¿Qué te interesa de este trabajo de {puesto}?", "¿Qué te interesa de este trabajo?")]})
    secciones.append({"clave": "logistica", "titulo": TITULO_SECCION["logistica"], "origen": "fijo", "preguntas": preguntas_logistica(ubicacion, horario)})
    condiciones = preguntas_condiciones(horario, modalidad)
    if condiciones:
        secciones.append({"clave": "condiciones", "titulo": TITULO_SECCION["condiciones"], "origen": "vacante", "preguntas": condiciones})
    preguntas = [q for s in secciones for q in s["preguntas"]]
    perfil_congelado = None
    if perfil:
        perfil_congelado = {k: copy.deepcopy(perfil.get(k)) for k in ("oficio", "nombre", "sinonimos", "palabras", "excluyentes",
                                                                       "palabras_excluyentes", "datos", "situacion")}
    return {
        "enfoque": f"Entrevista operativa para {puesto}: trayectoria, oficio, evidencia de indispensables, situación actual, "
                   "motivación, inicio y condiciones del puesto.",
        "temas": [s["titulo"] for s in secciones],
        "preguntas": preguntas,
        "operativo": {"oficio": (perfil or {}).get("oficio") or "", "nombre_oficio": (perfil or {}).get("nombre") or "",
                      "secciones": secciones, "indispensables": reqs, "perfil": perfil_congelado},
    }


# ============================================================ generación con IA + revisión automática (2026-10-11)

PROMPT_GENERACION = (
    "Eres un reclutador experto en personal operativo en México (almacén, producción, logística, reparto, limpieza, "
    "seguridad y ventas de piso). Redactas el guion de una entrevista por chat para UNA vacante, en español mexicano, de tú "
    "y con palabras sencillas. TODO sale de los datos de la vacante: no inventes requisitos ni condiciones.\n"
    "REGLAS ESTRICTAS:\n"
    "1. Una sola pregunta por línea, con UN solo signo de interrogación y UNA sola idea: nunca unas dos ideas con «y» "
    "(«¿Qué hacías ahí y cuánto tiempo estuviste?» está PROHIBIDO: son dos preguntas).\n"
    f"2. Máximo {MAX_PALABRAS} palabras por pregunta.\n"
    "3. Estructura OBLIGATORIA, en este orden:\n"
    "   1) trayectoria: «¿Cuál fue tu último trabajo?» SIEMPRE es la primera; qué hacía, cuánto tiempo, por qué salió; si "
    "tuvo otro trabajo antes (sí/no) y, condicionales a eso, qué hacía, cuánto tiempo y por qué lo dejó.\n"
    "   2) oficio: 1 o 2 preguntas CONCRETAS sobre el oficio (datos verificables: equipo, cantidades, procesos). La "
    "situación del oficio solo si se te da (cópiala tal cual).\n"
    "   3) indispensables: UNA pregunta de EVIDENCIA por requisito indispensable (dónde o en qué trabajo lo hizo), máximo 3, "
    "en el mismo orden. Nunca escolaridad ni documentos. Si no hay indispensables, deja la sección vacía.\n"
    "   4) situacion_actual: «¿Estás trabajando actualmente?» y una condicional para el sí y otra para el no.\n"
    "   5) motivacion (1): qué le interesa de este trabajo.\n"
    "   6) logistica (1): SOLO la fecha de inicio («¿Cuándo podrías empezar a trabajar?»).\n"
    "   7) condiciones: las condiciones descriptivas de la vacante que se te den (horario, modalidad), una por pregunta; "
    "vacía si no hay.\n"
    "   YA SE PREGUNTARON EN EL PREFILTRO y están PROHIBIDOS en la entrevista: ubicación (colonia, municipio, dónde vive), "
    "traslado (tiempo o transporte), sueldo (cuánto gana o ganaba, pretensiones) y escolaridad.\n"
    "4. Condicionales: usa el campo condicion «si» (solo si responde sí) o «no» (solo si responde no); aplica a la pregunta "
    "ANTERIOR, que debe contestarse con sí o no. NUNCA escribas «[Solo si…]» dentro del texto de la pregunta.\n"
    "5. PROHIBIDO: preguntas de manual de RH (fortalezas, debilidades, virtudes, trabajo bajo presión, errores, «¿dónde te "
    "ves en 5 años?», desarrollarte, crecer, «¿cómo organizas…?», «cuéntame un ejemplo»), pedir documentos (INE, CURP, RFC, "
    "comprobantes, actas, certificados) o hablar de escolaridad o estudios, y anunciar cuántas preguntas faltan."
)

# Cambio 19: una pregunta = una idea. «y» seguido de otra pregunta (cuánto, por qué, dónde…) son dos ideas.
_RE_DOS_IDEAS = re.compile(r"\by\s+(que|cuant\w*|cuando|donde|adonde|como|por ?que|cual\w*|quien\w*|con quien|desde cuando|"
                           r"hace cuanto|en que|de que|a que|para que)\b")
_RE_MARCA_VISIBLE = re.compile(r"\[|\]|\bsolo si (responde|contesta|dice)\b")


def une_dos_ideas(texto: str) -> bool:
    """«¿Qué hacías ahí y cuánto tiempo estuviste?» → True (dos preguntas en una)."""
    return bool(_RE_DOS_IDEAS.search(_n(texto)))


def marca_visible(texto: str) -> bool:
    """El texto que vería el candidato trae una marca interna («[Solo si…]») fuera de su lugar."""
    return bool(_RE_MARCA_VISIBLE.search(str(texto or "").lower()))


_RE_PROHIBIDAS = re.compile(r"\b(error|errores|equivocaste|presion|fortaleza|fortalezas|debilidad|debilidades|virtud|virtudes|"
                            r"cualidad|cualidades|punto debil|puntos debiles|donde te ves|en cinco anos|en 5 anos|"
                            r"deberiamos contratarte|te definirias|desarrollarte|desarrollar\w*|crecer|crecimiento|"
                            r"como organizas|como te organizas|cuentame un ejemplo|dame un ejemplo)\b")


# C4 (prueba del 11 de octubre): ubicación, traslado y sueldo ya se preguntaron en el prefiltro — la entrevista no los repite
_RE_TEMA_PREFILTRO = re.compile(r"\b(sueldo|salario|ganabas|ganas|ganarias|te pagaban|pretension\w*|traslad\w*|transporte|"
                                r"colonia|municipio|donde vives|de tu casa|que tan lejos|ubicacion)\b")


def tema_de_prefiltro(texto: str) -> Optional[str]:
    """El tema del prefiltro (ubicación, traslado, sueldo) que repite una pregunta de la entrevista, o None."""
    m = _RE_TEMA_PREFILTRO.search(_n(texto))
    return m.group(1) if m else None


def revisar_guion(guion: dict) -> List[str]:
    """Revisión AUTOMÁTICA del guion operativo (Cambio 19). Regresa la lista de problemas (vacía = aprobado)."""
    problemas: List[str] = []
    op = (guion or {}).get("operativo") or {}
    preguntas = [q for q in (guion or {}).get("preguntas") or [] if str(q or "").strip()]
    vigentes = set(preguntas)
    secciones = op.get("secciones") or []
    claves = [s.get("clave") for s in secciones if any(q in vigentes for q in s.get("preguntas") or [])]
    reqs = list(op.get("indispensables") or [])
    obligatorias = [k for k, (minimo, _) in RANGOS.items() if minimo > 0 or (k == "indispensables" and reqs)]
    faltan = [TITULO_SECCION[k] for k in obligatorias if k not in claves]
    if faltan:
        problemas.append("Faltan secciones: " + ", ".join(faltan) + ".")
    orden = [k for k, _, _ in SECCIONES if k in claves]
    if claves != orden:
        problemas.append("Las secciones no van en el orden 1. Trayectoria → 7. Condiciones del puesto.")
    for clave, (minimo, maximo) in RANGOS.items():
        s = next((x for x in secciones if x.get("clave") == clave), None)
        hay = len([q for q in (s or {}).get("preguntas") or [] if q in vigentes])
        if s is not None and hay and not (minimo <= hay <= maximo):
            problemas.append(f"{TITULO_SECCION[clave]}: lleva {hay} pregunta{'s' if hay != 1 else ''} "
                             f"(deben ser {minimo if minimo == maximo else f'de {minimo} a {maximo}'}).")
    if reqs:
        ind = next((x for x in secciones if x.get("clave") == "indispensables"), None)
        hay = len([q for q in (ind or {}).get("preguntas") or [] if q in vigentes])
        if ind is not None and hay < len(reqs):
            problemas.append(f"Evidencia de indispensables: lleva {hay} pregunta{'s' if hay != 1 else ''} para {len(reqs)} indispensables.")
    inicio = next((x for x in secciones if x.get("clave") == "logistica"), None)
    for q in [q for q in (inicio or {}).get("preguntas") or [] if q in vigentes]:
        if not re.search(r"\b(empezar|iniciar|inicio|comenzar|incorporarte)\b", _n(q)):
            problemas.append(f"«{texto_pregunta(q)[:70]}»: Inicio solo pregunta la fecha de inicio (el traslado va en el prefiltro).")
    anterior = ""
    for q in preguntas:
        cond, t = condicion_de(q)
        corto = t if len(t) <= 70 else t[:67] + "…"
        if marca_visible(t):
            problemas.append(f"«{corto}»: el texto visible lleva una marca «[Solo si…]» (va en la condición, nunca en el texto).")
        if t.count("?") != 1 or t.count("¿") > 1:
            problemas.append(f"«{corto}»: debe llevar UN solo signo de interrogación.")
        if une_dos_ideas(t):
            problemas.append(f"«{corto}»: une dos ideas con «y» (sepáralas en dos preguntas).")
        if palabras(t) > MAX_PALABRAS:
            problemas.append(f"«{corto}»: tiene {palabras(t)} palabras (máximo {MAX_PALABRAS}).")
        hallada = _RE_PROHIBIDAS.search(_n(t))
        if hallada:
            problemas.append(f"«{corto}»: usa una palabra prohibida («{hallada.group(1)}»).")
        if _RE_ESCOLARIDAD.search(_n(t)):
            problemas.append(f"«{corto}»: pide documentos o habla de escolaridad.")
        if tema_de_prefiltro(t):
            problemas.append(f"«{corto}»: pregunta ubicación, traslado o sueldo (ya están en el prefiltro).")
        if cond and not anterior:
            problemas.append(f"«{corto}»: es condicional pero no sigue a una pregunta de sí o no.")
        if not cond:
            anterior = q
    return problemas


class PreguntaIA(BaseModel):
    texto: str = Field(description=f"UNA pregunta, máximo {MAX_PALABRAS} palabras, un solo signo de interrogación.")
    condicion: Literal["", "si", "no"] = Field(default="", description="«si» = solo si respondió sí a la anterior; «no» = solo si "
                                                                         "respondió no; vacío = siempre.")


class SeccionIA(BaseModel):
    clave: Literal["trayectoria", "oficio", "indispensables", "situacion_actual", "motivacion", "logistica", "condiciones"]
    preguntas: List[PreguntaIA]


class GuionOperativoIA(BaseModel):
    secciones: List[SeccionIA] = Field(description="Las secciones en orden: trayectoria, oficio, indispensables, "
                                                   "situacion_actual, motivacion, logistica (inicio), condiciones.")


def _desde_ia(crudo: GuionOperativoIA, base: dict) -> dict:
    """Guion con el formato de siempre a partir de lo que redactó la IA (perfil, indispensables y condiciones salen de
    `base`: la IA nunca agrega ni quita requisitos)."""
    g = copy.deepcopy(base)
    op = g["operativo"]
    origen = {s["clave"]: s.get("origen") for s in op["secciones"]}
    de_base = {s["clave"]: s for s in op["secciones"]}
    requisitos = (de_base.get("indispensables") or {}).get("requisitos") or []
    situacion = situacion_verificable(op.get("perfil"))
    propias = {s.clave: s for s in crudo.secciones}
    secciones = []
    for clave, _, _ in SECCIONES:
        if clave not in de_base:
            continue  # sin indispensables / sin condiciones en la vacante: la sección no existe aunque la IA la mande
        s = propias.get(clave)
        qs = [con_condicion(re.sub(r"\[[^\]]*\]", "", x.texto).strip(), x.condicion) for x in (s.preguntas if s else [])
              if (x.texto or "").strip()][:RANGOS[clave][1]]
        if clave == "oficio" and situacion and _n(situacion) not in {_n(texto_pregunta(q)) for q in qs}:
            qs = qs[:RANGOS["oficio"][1] - 1] + [situacion]  # la situación verificable va TAL CUAL (la evaluación B la reconoce)
        if clave == "indispensables":
            qs = qs[:len(requisitos)]
        if not qs:
            qs = list(de_base[clave]["preguntas"])  # la IA no la mandó: la determinista
        secciones.append({"clave": clave, "titulo": TITULO_SECCION[clave], "origen": "ia" if clave != "oficio" else origen.get("oficio", "ia"),
                          "preguntas": qs, **({"requisitos": requisitos} if clave == "indispensables" else {})})
    op["secciones"] = secciones
    g["preguntas"] = [q for s in secciones for q in s["preguntas"]]
    g["temas"] = [s["titulo"] for s in secciones]
    return g


def con_revision(guion: dict, intentos: int, con_ia: bool, problemas: Optional[List[str]] = None) -> dict:
    problemas = revisar_guion(guion) if problemas is None else problemas
    guion.setdefault("operativo", {})["revision"] = {"ok": not problemas, "intentos": intentos, "con_ia": con_ia, "problemas": problemas[:20]}
    return guion


def generar_guion(titulo: str, perfil: Optional[dict], indispensables: List[str], *, ubicacion: str = "", horario: str = "",
                  responsabilidades: Optional[List[str]] = None, modalidad: str = "") -> Tuple[dict, bool]:
    """Guion operativo definitivo: la IA lo redacta con `PROMPT_GENERACION`, `revisar_guion` lo valida y, si falla, se
    regenera con los problemas como retroalimentación (máximo `MAX_REGENERACIONES`). Si el último intento también falla se
    guarda IGUAL con `operativo.revision.problemas` (RH ve qué falló). Sin IA: guion determinista (cumple las reglas)."""
    from . import ia

    reqs = indispensables_de_entrevista(indispensables)
    base = armar_guion(titulo, perfil, reqs, [], ubicacion=ubicacion, horario=horario, modalidad=modalidad)
    client = ia._client()
    if client is None:
        return con_revision(base, 1, False), False
    datos = "\n".join(f"- {x}" for x in (perfil or {}).get("datos") or []) or "- (sin perfil del oficio: pregunta por su experiencia en el puesto)"
    situacion = situacion_verificable(perfil) or "(sin situación verificable: no la incluyas)"
    entrada = (
        f"Puesto: {titulo}\nOficio: {(perfil or {}).get('nombre') or 'no identificado'}\nDatos del oficio a verificar:\n{datos}\n"
        f"Situación del oficio (cópiala tal cual): {situacion}\n"
        f"Lo que NO cuenta como experiencia: {'; '.join((perfil or {}).get('excluyentes') or []) or 'n/a'}\n"
        f"Requisitos indispensables (una pregunta de evidencia por cada uno):\n" + ("\n".join(f"- {r}" for r in reqs) or "- (ninguno: deja la sección vacía)") + "\n"
        f"Condiciones descriptivas para la sección 7:\n" + ("\n".join(f"- {texto_pregunta(q)}" for q in preguntas_condiciones(horario, modalidad))
                                                        or "- (ninguna: deja la sección vacía)") + "\n"
        f"Responsabilidades: {'; '.join(responsabilidades or []) or '(sin dato)'}\n"
        f"Ubicación: {ubicacion or '(sin dato)'}\nHorario: {horario or '(sin dato)'}"
    )
    ultimo, problemas = None, []
    for intento in range(1, MAX_REGENERACIONES + 2):
        retro = ("\n\nTu versión anterior NO pasó la revisión automática. Corrige EXACTAMENTE esto:\n" + "\n".join(f"- {p}" for p in problemas)) \
            if problemas else ""
        try:
            resp = client.responses.parse(
                model=ia.MODEL, instructions=PROMPT_GENERACION + f"\nPROHIBIDO preguntar sobre: {ia.DATOS_SENSIBLES_PROHIBIDOS}.",
                input=entrada + retro, text_format=GuionOperativoIA,
            )
            g = _desde_ia(resp.output_parsed, base)
        except Exception as ex:  # noqa: BLE001 — sin IA disponible: el guion determinista
            print(f"[operativa] generar_guion (intento {intento}) cayó a modo determinista: {str(ex)[:200]}", flush=True)
            break
        problemas = revisar_guion(g)
        ultimo = (g, intento, problemas)
        if not problemas:
            return con_revision(g, intento, True, []), True
    if ultimo is not None:
        print(f"[operativa] el guion de «{titulo}» no pasó la revisión tras {ultimo[1]} intentos: se guarda con sus problemas", flush=True)
        return con_revision(ultimo[0], ultimo[1], True, ultimo[2]), True
    return con_revision(base, 1, False), False


def normalizar_operativo(op) -> Optional[dict]:
    """Conserva el bloque `operativo` de un guion (lo que RH edita del guion son las preguntas; esto es la estructura)."""
    if not isinstance(op, dict) or not isinstance(op.get("secciones"), list):
        return None
    secciones = []
    for s in op["secciones"]:
        if isinstance(s, dict) and s.get("clave") in TITULO_SECCION:
            secciones.append({"clave": s["clave"], "titulo": TITULO_SECCION[s["clave"]], "origen": s.get("origen") or "fijo",
                              "preguntas": _textos(s.get("preguntas"), 300, 9), **({"requisitos": _textos(s.get("requisitos"), 200, 3)}
                                                                                   if s.get("requisitos") else {})})
    if not secciones:
        return None
    salida = {"oficio": op.get("oficio") if op.get("oficio") in BIBLIOTECA else "", "nombre_oficio": str(op.get("nombre_oficio") or "")[:80],
              "secciones": secciones, "indispensables": _textos(op.get("indispensables"), 200, MAX_INDISPENSABLES),
              "perfil": op.get("perfil") if isinstance(op.get("perfil"), dict) else None}
    rev = op.get("revision")
    if isinstance(rev, dict):
        salida["revision"] = {"ok": bool(rev.get("ok")), "intentos": int(rev.get("intentos") or 1), "con_ia": bool(rev.get("con_ia")),
                              "problemas": _textos(rev.get("problemas"), 300, 20)}
    return salida


# ============================================================ conversación: no repetir lo del prefiltro

_TEMAS_LOGISTICA = {
    "traslado": ("vives", "ubicacion", "zona", "traslad", "transport", "cerca", "colonia", "municipio"),
    "horario": ("horario", "turno", "rolar", "nocturno", "fines de semana", "disponibilidad de horario"),
    "inicio": ("empezar", "iniciar", "incorporar", "disponibilidad inmediata", "cuando podrias"),
}


def _tema_logistica(pregunta: str) -> Optional[str]:
    t = _n(pregunta)
    if "transport" in t or "de tu casa" in t:
        return "traslado"
    if "horario" in t or "turno" in t:
        return "horario"
    if "empezar" in t:
        return "inicio"
    return None


def ya_respondido(previas: List[dict]) -> List[str]:
    """Lo que el candidato ya contestó (formulario web / prefiltro): la entrevistadora no lo vuelve a preguntar."""
    salida = []
    for r in previas or []:
        q = str(r.get("pregunta") or r.get("criterio") or "").strip()
        a = str(r.get("respuesta") or "").strip()
        if q and a:
            salida.append(f"{q} → «{a[:120]}»")
    return salida[:15]


# ============================================================ memoria de la conversación (Cambio 19, 2026-10-12)

_VACIAS_EVIDENCIA = {"experiencia", "disponibilidad", "conocimiento", "conocimientos", "capacidad", "para", "como", "tener", "contar",
                     "minimo", "menos", "anos", "meses", "kilos", "puesto", "trabajo", "requisito", "cumplir", "saber", "poder",
                     "manejo", "manejar", "buena", "bueno", "desde", "hasta", "sobre", "entre"}
_RE_NEGADA = re.compile(r"\b(no|nunca|jamas|tampoco|ningun\w*)\b")


def _raices(texto: str) -> set:
    return {w[:4] for w in re.findall(r"[a-z]{4,}", _n(texto)) if w not in _VACIAS_EVIDENCIA}


def evidencia_de(requisito: str, respuestas: List[str]) -> bool:
    """¿El candidato ya dio EVIDENCIA de este indispensable en lo que contó («cargaba cajas» ↔ «Cargar cajas de 25 kg»)?
    Busca las palabras clave del requisito (por raíz) en una misma frase afirmativa — sin negaciones."""
    claves = _raices(requisito)
    if not claves:
        return False
    necesarias = len(claves) if len(claves) <= 2 else 2
    for r in respuestas or []:
        for frase in re.split(r"[.;:!?\n]+|\bpero\b", _n(r)):
            if frase.strip() and not _RE_NEGADA.search(frase) and len(_raices(frase) & claves) >= necesarias:
                return True
    return False


def _ya_preguntada(q: str, transcript: List[dict]) -> bool:
    t = _n(texto_pregunta(q))
    return any(m.get("rol") == "assistant" and t and t in _n(m.get("texto") or "") for m in transcript or [])


def indispensables_demostrados(guion: dict, transcript: List[dict]) -> List[str]:
    """Preguntas de «Evidencia de indispensables» que se OMITEN porque el candidato ya dio esa evidencia antes de que se
    le hicieran (memoria de la conversación). Una pregunta ya hecha nunca se omite (no se pierde el hilo)."""
    op = (guion or {}).get("operativo") or {}
    sec = next((s for s in op.get("secciones") or [] if s.get("clave") == "indispensables"), None)
    if not sec:
        return []
    dichas = [str(m.get("texto") or "") for m in transcript or [] if m.get("rol") == "user"]
    salida = []
    for q, req in zip(sec.get("preguntas") or [], sec.get("requisitos") or []):
        if not _ya_preguntada(q, transcript) and evidencia_de(req, dichas):
            salida.append(q)
    return salida


def preguntas_para_conversacion(guion: dict, previas: List[dict], transcript: Optional[List[dict]] = None) -> List[str]:
    """Preguntas del guion operativo SIN las de inicio/condiciones que el prefiltro ya cubrió y SIN la evidencia de un
    indispensable que el candidato ya demostró (Cambio 19): una pregunta por mensaje y sin repetir (garantizado en
    código, no solo en el prompt)."""
    preguntas = list((guion or {}).get("preguntas") or [])
    op = (guion or {}).get("operativo")
    if not op:
        return preguntas
    cubiertos = set()
    for r in previas or []:
        t = _n(f"{r.get('pregunta') or ''} {r.get('criterio') or ''}")
        for tema, palabras in _TEMAS_LOGISTICA.items():
            if any(_n(p) in t for p in palabras):
                cubiertos.add(tema)
    logistica = {q for s in op.get("secciones") or [] if s.get("clave") in ("logistica", "condiciones") for q in s.get("preguntas") or []}
    demostrados = set(indispensables_demostrados(guion, transcript or []))
    return [q for q in preguntas if not (q in logistica and _tema_logistica(q) in cubiertos) and q not in demostrados]


def bloque_prompt(guion: dict, previas: List[dict], transcript: Optional[List[dict]] = None) -> str:
    """Instrucciones del modo Operativo para el prompt del entrevistador (piezas fijas, en orden)."""
    op = (guion or {}).get("operativo") or {}
    lista = preguntas_para_conversacion(guion, previas, transcript)
    permitidas = set(lista)
    lineas = []
    for i, s in enumerate(op.get("secciones") or [], 1):
        qs = [q for q in s.get("preguntas") or [] if q in permitidas]
        if qs:
            lineas.append(f"{i}. {s['titulo']}:\n" + "\n".join(f"   - {q}" for q in qs))
    de_secciones = {q for s in op.get("secciones") or [] for q in s.get("preguntas") or []}
    extra = [q for q in lista if q not in de_secciones]  # preguntas que RH editó o agregó al guion de la vacante
    if extra:
        lineas.append("Preguntas agregadas por RH (hazlas al final, antes de Logística si aplica):\n" + "\n".join(f"   - {q}" for q in extra))
    perfil = op.get("perfil") or {}
    excl = "; ".join(perfil.get("excluyentes") or [])
    previo = ya_respondido(previas)
    return (
        "MODO OPERATIVO (guion ARMADO por Red Human; no inventes preguntas nuevas):\n"
        "Recorre las secciones EN ESTE ORDEN, una pregunta por mensaje, con las preguntas tal cual (solo adapta el tono). "
        "Tu primer mensaje es directamente la primera pregunta: sin saludo ni presentación (ya se hizo).\n"
        "CONDICIONALES: una pregunta marcada «[Solo si responde sí]» se hace SOLO si contestó que sí a la pregunta anterior; "
        "«[Solo si responde no]», SOLO si contestó que no. Si no aplica, sáltala. Nunca escribas la marca.\n"
        "RESPUESTA VAGA («normal», «de todo», «varias cosas»): pide UN solo dato concreto (dónde, cuánto tiempo o qué equipo) "
        "y continúa con la siguiente pregunta aunque la segunda respuesta siga siendo general.\n"
        "«NO ENTIENDO» o una pregunta de vuelta: reformula la MISMA pregunta con otras palabras más sencillas, sin cambiar de "
        "tema ni pasar a la siguiente.\n"
        "MEMORIA: si en lo que ya contó dio evidencia de un indispensable (p. ej. «cargaba cajas» cuando se pide cargar "
        "cajas), NO hagas esa pregunta de evidencia.\n"
        "SIN EXPERIENCIA: si dice que nunca ha trabajado, antes de seguir pregunta UNA vez «¿Has hecho algún trabajo, aunque "
        "sea por tu cuenta o ayudando a alguien?» y, si dice que sí, sigue con ese trabajo como su experiencia.\n"
        "MOTIVO DE SALIDA (una sola repregunta): despido → «¿Por qué te despidieron?»; problemas o conflicto → «¿Con quién "
        "tuviste problemas?»; renuncia sin motivo → «¿Por qué renunciaste?»; respuesta vaga → «¿Por qué decidiste salir?».\n"
        "NO digas «gracias» en cada mensaje (como mucho uno de cada 3 o 4, con variantes) y NUNCA anuncies cuántas preguntas "
        "faltan ni cuál es la última.\n"
        + "\n".join(lineas) + "\n"
        + (f"EXCLUYENTES DEL OFICIO ({perfil.get('nombre') or 'oficio'}): {excl} Si su experiencia es con algo excluyente, "
           "pregúntale con naturalidad si tiene experiencia con lo que pide el puesto; no le expliques la regla.\n" if excl else "")
        + ("YA LO RESPONDIÓ EN EL PREFILTRO O EL FORMULARIO (NO lo vuelvas a preguntar; si hace falta, parte de ahí):\n"
           + "\n".join(f"- {x}" for x in previo) + "\n" if previo else "")
        + "No hables de escolaridad ni pidas documentos: eso se valida en Documentos. Tampoco preguntes ubicación, traslado "
          "ni sueldo: ya se preguntaron en el prefiltro.\n"
    )


# ============================================================ conversación sin IA (demo / respaldo) — 2026-10-11

_RE_NO_ENTIENDE = re.compile(r"^(no (le )?entiendo( la pregunta)?|no entendi( la pregunta)?|no le entendi|no comprendo|como|que|mande|"
                             r"a que te refieres|que quieres decir|que significa( eso)?|explicame|me explicas|perdon|no se a que te refieres)$")
_RE_VAGA = re.compile(r"^(pues |este |mmm+ |eh )?(normal|lo normal|bien|muy bien|si bien|varias cosas|de todo|de todo un poco|"
                      r"lo que sea|lo que me pidieran|lo que me pedian|nada en especial|cosas|muchas cosas|mas o menos|ahi nomas|"
                      r"lo de siempre|pues nada|todo)$")
_RE_SI_CHAT = re.compile(r"^(si|sii+|sip|simon|claro|obvio|por supuesto|afirmativo|asi es|correcto|exacto|ya|yes|ajam?|aja)\b")
_RE_NO_CHAT = re.compile(r"^(no|nop|nel|negativo|nunca|para nada|tampoco|aun no|todavia no|ahorita no|ya no)\b")
PREFIJO_REFORMULA = "Te lo pregunto de otra forma"
PREFIJO_DATO = "Para tenerlo claro"


def es_si(texto: str) -> bool:
    return bool(_RE_SI_CHAT.match(_n(texto)))


def es_no(texto: str) -> bool:
    return bool(_RE_NO_CHAT.match(_n(texto)))


def _reformula(q: str) -> str:
    t = texto_pregunta(q).strip().lstrip("¿").rstrip("?").strip()
    return f"{PREFIJO_REFORMULA}, con tus palabras: ¿{t[:1].lower() + t[1:]}?"


# ============================================================ motivo de salida (Cambio 19): UNA repregunta

REPREGUNTA_SALIDA = {"despido": "¿Por qué te despidieron?", "conflicto": "¿Con quién tuviste problemas?",
                     "renuncia": "¿Por qué renunciaste?", "vago": "¿Por qué decidiste salir?"}
_RE_PREG_SALIDA = re.compile(r"\bpor que (saliste|dejaste|te saliste|te fuiste|lo dejaste|la dejaste)\b")
_RE_SAL_DESPIDO = re.compile(r"\b((me|lo|la|nos|los|las) (corrieron|despidieron|liquidaron|sacaron|dieron de baja|botaron)|despid\w*|me (hicieron|pidieron) "
                             r"(la )?renuncia)\b")
_RE_SAL_RAZON_EXTERNA = re.compile(r"\b(recorte\w*|cerr(o|aron)|quebr\w*|se acabo|termino (el|mi) contrato|se termino|fin de (contrato|temporada|"
                                   r"obra|proyecto)|temporal|por temporada|reestructura\w*|liquidaron a todos)\b")
_RE_SAL_CONFLICTO = re.compile(r"\b(problema\w*|conflicto\w*|pleito\w*|me pele\w*|discut\w*|no me llevaba|mal ambiente|me trataban mal|"
                               r"acoso|hostig\w*|roces?)\b")
_RE_SAL_CON_QUIEN = re.compile(r"\bcon (mi|mis|el|la|los|las|un|una|unos|unas) (jefe|jefa|supervisor\w*|encargad\w*|compa\w*|patron\w*|"
                               r"gerente|coordinador\w*|lider|dueno|duena|cliente\w*)\b")
_RE_SAL_RENUNCIA = re.compile(r"\b(renunci\w*|me sali|me fui|lo deje|la deje|deje (el|ese) (trabajo|empleo)|decidi salir(me)?)\b")
_RE_SAL_RAZON = re.compile(r"\b(porque|por que|ya que|debido|pues (me|mi|el|la|se)|por (el|la|mi|mis|un|una|falta|faltas|cambio|"
                           r"horario|sueldo|salud|estudi\w*|distancia|lejos|enfermedad|mudanza|embarazo)|para (estudiar|cuidar|buscar)|"
                           r"me (cambie|mude)|cambio de casa|me pagaban|pagaban (poco|muy)|horario|sueldo|estudiar|cuidar)\b")
_RE_SAL_VAGA = re.compile(r"\b(personal(es)?|pues ya|nomas|nada mas|ya no quise|por nada|no se|cosas|asi paso|equis|ya no)\b")


def motivo_salida(respuesta: str) -> Optional[str]:
    """Clasifica el motivo de salida que dio el candidato: «despido» | «conflicto» | «renuncia» (sin motivo) | «vago» |
    None (dio una razón concreta)."""
    t = _n(respuesta)
    if not t:
        return None
    if _RE_SAL_DESPIDO.search(t):
        return None if _RE_SAL_RAZON_EXTERNA.search(t) or _RE_SAL_RAZON.search(t) else "despido"
    if _RE_SAL_CONFLICTO.search(t):
        return None if _RE_SAL_CON_QUIEN.search(t) else "conflicto"
    if _RE_SAL_RAZON_EXTERNA.search(t):
        return None
    if _RE_SAL_VAGA.search(t) and not _RE_SAL_RAZON.search(t.replace("personales", "")):
        return "vago"
    if _RE_SAL_RENUNCIA.search(t):
        return None if _RE_SAL_RAZON.search(t) else "renuncia"
    if _RE_SAL_RAZON.search(t) or len(t.split()) > 3:
        return None
    return "vago"


def repregunta_salida(respuesta: str) -> Optional[str]:
    m = motivo_salida(respuesta)
    return REPREGUNTA_SALIDA.get(m) if m else None


def es_pregunta_salida(texto: str) -> bool:
    t = _n(texto)
    return bool(_RE_PREG_SALIDA.search(t)) and t not in {_n(x) for x in REPREGUNTA_SALIDA.values()}


def repregunta_pendiente(historial: List[dict]) -> Optional[str]:
    """Si la última pregunta fue la del motivo de salida y la respuesta es despido, conflicto, renuncia sin motivo o
    vaga → la repregunta (UNA sola vez: la repregunta misma nunca dispara otra)."""
    if not historial or historial[-1].get("rol") != "user":
        return None
    previo = next((m for m in reversed(historial[:-1]) if m.get("rol") == "assistant"), None)
    if previo is None:
        return None
    ultima_linea = (str(previo.get("texto") or "").strip().splitlines() or [""])[-1]
    if not es_pregunta_salida(ultima_linea):
        return None
    return repregunta_salida(historial[-1].get("texto") or "")


def turno_demo(preguntas: List[str], historial: List[dict]) -> Tuple[str, bool]:
    """Siguiente mensaje de la entrevistadora SIN IA (guion operativo). Respeta las condicionales «[Solo si responde
    sí/no]», ante una respuesta vaga pide UN dato concreto (una sola vez por pregunta) y ante «no entiendo» reformula la
    MISMA pregunta (máximo 2 veces). Regresa (texto, terminada)."""
    qs = [q for q in preguntas or [] if str(q or "").strip()]
    textos = [_n(texto_pregunta(q)) for q in qs]
    actual, desde = -1, 0
    respuestas: Dict[int, List[str]] = {}
    for i, m in enumerate(historial or []):
        t = _n(m.get("texto") or "")
        if m.get("rol") == "assistant":
            j = next((k for k in range(actual + 1, len(qs)) if textos[k] and textos[k] in t), None)
            if j is not None:
                actual, desde = j, i
        elif actual >= 0 and t:
            respuestas.setdefault(actual, []).append(m.get("texto") or "")
    if not qs:
        return "", True
    if actual < 0:
        return texto_pregunta(qs[0]), False
    ultimo = _n((historial or [{}])[-1].get("texto") or "") if (historial or [{}])[-1].get("rol") == "user" else ""
    tras = [_n(m.get("texto") or "") for m in (historial or [])[desde + 1:] if m.get("rol") == "assistant"]
    if ultimo and _RE_NO_ENTIENDE.match(ultimo) and sum(1 for x in tras if x.startswith(_n(PREFIJO_REFORMULA))) < 2:
        return _reformula(qs[actual]), False
    rq = repregunta_pendiente(historial or [])
    if rq:
        return rq, False  # Cambio 19: motivo de salida — una sola repregunta
    repreguntada = any(x in {_n(r) for r in REPREGUNTA_SALIDA.values()} for x in tras)
    es_parental = actual + 1 < len(qs) and bool(condicion_de(qs[actual + 1])[0])
    if ultimo and _RE_VAGA.match(ultimo) and not es_parental and not repreguntada and not any(x.startswith(_n(PREFIJO_DATO)) for x in tras):
        return f"{PREFIJO_DATO}, dame un dato concreto: ¿dónde, cuánto tiempo o con qué equipo?", False
    k = actual + 1
    while k < len(qs):
        cond, _ = condicion_de(qs[k])
        if not cond:
            break
        padre = max((x for x in range(k) if not condicion_de(qs[x])[0]), default=None)
        r = " ".join((respuestas.get(padre) or [""])[:1]) if padre is not None else ""
        if (cond == "si" and es_si(r)) or (cond == "no" and es_no(r)):
            break
        k += 1
    if k >= len(qs):
        return "", True
    return texto_pregunta(qs[k]), False


# ============================================================ evaluación A · B · C · D + alertas

_NUM = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "media": 0.5}


def _num(x: str) -> float:
    x = x.replace(",", ".")
    try:
        return float(x)
    except ValueError:
        return float(_NUM.get(x, 0))


_RE_DURACION = re.compile(r"\b(\d+(?:[.,]\d+)?|un|una|dos|tres)\s*(semanas?|mes(?:es)?)\b")
_RE_CONTEXTO_EMPLEO = re.compile(r"\b(estuve|dure|trabaje|trabajando|labore|meti|entre|me sali|sali|dur[oó])\b")
_RE_DESPIDO = re.compile(r"\b(me (corrieron|despidieron|liquidaron|sacaron|dieron de baja|recortaron)|despid\w*|recorte de personal)\b")
_RE_HORAS = re.compile(r"\b(\d+(?:[.,]\d+)?|una|un|dos|tres|cuatro)\s*(horas?|hrs?|h)\b")
_RE_MIN = re.compile(r"\b(\d+)\s*(minutos?|mins?|min)\b")
_RE_NEGATIVA_LOGISTICA = re.compile(r"\b(no puedo|no me acomoda|no me queda|no podria|imposible|no alcanzo|no me es posible|solo puedo|nada mas puedo)\b")
_RE_TRABAJA = re.compile(r"\b(trabajo actualmente|actualmente trabajo|estoy trabajando|sigo trabajando|todavia trabajo|aun trabajo)\b")
_RE_NEGACION = re.compile(r"\b(no tengo|nunca he|no he|no cuento|no se|nunca|ninguna|ninguno|no lo he)\b")


def minutos_traslado(texto: str) -> Optional[int]:
    t = _n(texto)
    if "hora y media" in t:
        base = 90
        t = t.replace("hora y media", "")
        extra = _RE_MIN.search(t)
        return base + (int(extra.group(1)) if extra else 0)
    total, hubo = 0.0, False
    for m in _RE_HORAS.finditer(t):
        total += _num(m.group(1)) * 60
        hubo = True
    for m in _RE_MIN.finditer(t):
        total += int(m.group(1))
        hubo = True
    return int(total) if hubo else None


_DIAS_SEMANA = r"lunes|martes|miercoles|jueves|viernes|sabado|domingo"
_RE_INMEDIATO = re.compile(r"\b(inmediat\w*|ya mismo|desde ya|de una vez|hoy|manana|ahorita|cuando (sea|ustedes|me digan|gusten|quieran)|"
                           r"lo antes posible|en cuanto (me|ustedes)|ya puedo|cuando usted\w*)\b")
_RE_SEMANA = re.compile(r"\b(esta semana|la (proxima|siguiente|otra) semana|la semana que (entra|viene)|el (proximo )?(" + _DIAS_SEMANA
                        + r")|(este |el )?fin de semana|pasado manana)\b")
_RE_MES = re.compile(r"\b(el (proximo|siguiente) mes|el mes que (entra|viene)|la (proxima|siguiente|otra) quincena)\b")
_RE_CANTIDAD_TIEMPO = re.compile(r"\b(\d+|un|una|dos|tres|cuatro|cinco|seis|media)\s*(dias?|semanas?|mes(?:es)?|quincenas?)\b")


def dias_inicio(texto: str) -> Optional[int]:
    """Días hasta que puede empezar, leídos de su respuesta («mañana» → 1, «el lunes» → 7, «en 2 semanas» → 14, «no
    puedo antes de un mes» → 30). None si no da un dato concreto."""
    t = _n(texto)
    if not t:
        return None
    m = _RE_CANTIDAD_TIEMPO.search(t)
    if m:
        n = _num(m.group(1)) if m.group(1) != "media" else 0.5
        unidad = m.group(2)
        mult = 1 if unidad.startswith("dia") else 7 if unidad.startswith("semana") else 15 if unidad.startswith("quincena") else 30
        return int(round(n * mult))
    if _RE_MES.search(t):
        return 30 if "mes" in t else 15
    if _RE_SEMANA.search(t):
        return 7
    if _RE_INMEDIATO.search(t) and not _RE_NEGATIVA_LOGISTICA.search(t):
        return 1
    return None


def _meses(texto: str) -> Optional[float]:
    m = _RE_DURACION.search(_n(texto))
    if not m:
        return None
    n = _num(m.group(1))
    return n / 4.3 if m.group(2).startswith("semana") else n


def respuestas_por_seccion(transcript: List[dict], guion: dict) -> Dict[str, List[Tuple[str, str]]]:
    """[(pregunta, respuesta)] por sección: cada mensaje de la entrevistadora se asigna a la pregunta del guion con más
    palabras en común; la respuesta es el siguiente mensaje del candidato."""
    op = (guion or {}).get("operativo") or {}
    catalogo = [(s["clave"], q, set(_n(texto_pregunta(q)).split())) for s in op.get("secciones") or [] for q in s.get("preguntas") or []]
    salida: Dict[str, List[Tuple[str, str]]] = {}
    actual: Optional[Tuple[str, str]] = None
    for m in transcript or []:
        texto = str(m.get("texto") or "")
        if m.get("rol") == "assistant":
            w = set(_n(texto).split())
            mejor, puntos = None, 0.0
            for clave, q, palabras in catalogo:
                if palabras:
                    p = len(w & palabras) / len(palabras)
                    if p > puntos:
                        mejor, puntos = (clave, q), p
            actual = mejor if puntos >= 0.5 else (actual if actual and len(_n(texto).split()) <= 12 else None)
        elif actual is not None and texto.strip():
            salida.setdefault(actual[0], []).append((actual[1], texto))
    return salida


def salidas_conflictivas(transcript: List[dict]) -> List[str]:
    """Puntos por validar por el MOTIVO DE SALIDA (Cambio 19): lo que contestó a «¿Por qué saliste…?» (o a su repregunta)
    cuando fue un despido (no un cierre o recorte) o un conflicto."""
    puntos: List[str] = []
    repreguntas = {_n(x) for x in REPREGUNTA_SALIDA.values()}
    pendiente = False
    for m in transcript or []:
        texto = str(m.get("texto") or "")
        if m.get("rol") == "assistant":
            ultima = (texto.strip().splitlines() or [""])[-1]
            pendiente = es_pregunta_salida(ultima) or _n(ultima) in repreguntas
            continue
        if not pendiente or not texto.strip():
            continue
        pendiente = False
        t = _n(texto)
        if _RE_SAL_DESPIDO.search(t) and not _RE_SAL_RAZON_EXTERNA.search(t):
            puntos.append(f"Salida por despido: «{texto.strip()[:120]}»")
        elif _RE_SAL_CONFLICTO.search(t):
            puntos.append(f"Salida por conflicto: «{texto.strip()[:120]}»")
    return puntos[:3]


def es_salida_conflictiva(texto: str) -> bool:
    t = _n(texto)
    return bool((_RE_SAL_DESPIDO.search(t) and not _RE_SAL_RAZON_EXTERNA.search(t)) or _RE_SAL_CONFLICTO.search(t))


def texto_inconsistencia(i: dict) -> str:
    """Texto ÚNICO de una inconsistencia guardada en `analisis.inconsistencias` (ficha y evaluación lo comparten)."""
    return (f"Inconsistencia: {i.get('criterio')} — formulario «{i.get('web')}», chat «{i.get('whatsapp')}»"
            + (f" (aclaró: {str(i.get('aclaracion'))[:120]})" if i.get("aclarada") and i.get("aclaracion") else ""))


_RE_SIN_PROBLEMA = re.compile(r"\b(no (tengo|hay) (ningun )?problema|sin problema|no me (molesta|cuesta)|no importa)\b")


def _niega(texto: str) -> bool:
    """«No lo he hecho», «nunca» → niega; «no tengo problema» no cuenta como negación."""
    t = _n(texto)
    return bool(_RE_NEGACION.search(t)) and not _RE_SIN_PROBLEMA.search(t)


def _criterio(resultado: str, evidencia: str = "") -> dict:
    return {"resultado": resultado if resultado in RESULTADOS else "sin_dato", "evidencia": (evidencia or "").strip()[:400]}


def evaluar_demo(transcript: List[dict], guion: dict, previas: Optional[List[dict]] = None) -> dict:
    """Evaluación determinista (sin IA) de los criterios A-D con lo que dijo el candidato."""
    op = (guion or {}).get("operativo") or {}
    perfil = op.get("perfil") or {}
    por = respuestas_por_seccion(transcript, guion)
    criterios: Dict[str, dict] = {}

    # A — experiencia en el oficio (trayectoria + datos del oficio)
    situacion_q = str((perfil.get("situacion") or {}).get("pregunta") or "")
    if situacion_q and not any(q == situacion_q for q, _ in por.get("oficio", [])):
        # guion redactado por la IA: la situación es la ÚLTIMA pregunta de la sección del oficio
        sec = next((s for s in op.get("secciones") or [] if s.get("clave") == "oficio"), None)
        if sec and sec.get("preguntas"):
            situacion_q = sec["preguntas"][-1]
    resp_oficio = [r for q, r in por.get("oficio", []) if q != situacion_q]
    texto_a = " ".join([r for _, r in por.get("trayectoria", [])] + resp_oficio)
    if not texto_a.strip():
        criterios["A"] = _criterio("sin_dato")
    elif perfil:
        positivas = list(perfil.get("sinonimos") or []) + list(perfil.get("palabras") or [])
        excluyentes = list(perfil.get("palabras_excluyentes") or [])
        # por cláusula: «montacargas nunca» o «tarimas con el patín» no acreditan el oficio
        clausulas = [c for c in re.split(r"[.,;:!?\n]+|\by\b|\bpero\b", texto_a) if c.strip()]
        acredita = [c for c in clausulas if _contiene(c, positivas) and not _RE_NEGACION.search(_n(c)) and not _contiene(c, excluyentes)]
        if acredita:
            criterios["A"] = _criterio("cumple", texto_a[:200])
        elif _contiene(texto_a, perfil.get("palabras_excluyentes") or []):
            criterios["A"] = _criterio("no_cumple", f"Su experiencia es con algo excluyente: {texto_a[:160]}")
        else:
            # Cambio 19: sin datos concretos del oficio no hay «experiencia en puesto similar» (la duda la marca la IA)
            criterios["A"] = _criterio("no_cumple", f"Sin experiencia comprobable en el oficio: {texto_a[:160]}")
    else:
        criterios["A"] = _criterio("cumple" if len(texto_a) > 40 else "parcial", texto_a[:200])

    # B — procedimiento (situación esperada)
    resp_b = " ".join(r for q, r in por.get("oficio", []) if q == situacion_q)
    esperado = list((perfil.get("situacion") or {}).get("esperado") or [])
    if not resp_b.strip() or not esperado:
        criterios["B"] = _criterio("sin_dato")
    else:
        hits = [x["texto"] for x in esperado if _contiene(resp_b, x.get("palabras") or [])]
        res = "cumple" if len(hits) >= 2 else ("parcial" if hits else "no_cumple")
        criterios["B"] = _criterio(res, ("Mencionó: " + ", ".join(hits)) if hits else f"Respuesta sin los pasos esperados: {resp_b[:160]}")

    # C — indispensables (máximo 3 explorados; lo demás lo cubrió el prefiltro). Cambio 19: el que ya demostró en lo que
    # contó (memoria: la pregunta se omitió) cuenta como cubierto
    resp_c = por.get("indispensables", [])
    sec_c = next((s for s in op.get("secciones") or [] if s.get("clave") == "indispensables"), {}) or {}
    dichas = [str(m.get("texto") or "") for m in transcript or [] if m.get("rol") == "user"]
    preguntadas = {q for q, _ in resp_c}
    demostrados = [req for q, req in zip(sec_c.get("preguntas") or [], sec_c.get("requisitos") or [])
                   if q not in preguntadas and evidencia_de(req, dichas)]
    if not op.get("indispensables"):
        criterios["C"] = _criterio("cumple", "La vacante no tiene indispensables por explorar.")
    elif not resp_c and demostrados:
        criterios["C"] = _criterio("cumple" if len(demostrados) >= len(op.get("indispensables") or []) else "parcial",
                                   "Lo demostró en su trayectoria: " + ", ".join(demostrados))
    elif not resp_c:
        criterios["C"] = _criterio("sin_dato")
    elif any(_niega(r) for _, r in resp_c):
        r = next(r for _, r in resp_c if _niega(r))
        criterios["C"] = _criterio("no_cumple", f"«{r.strip()[:160]}»")
    else:
        criterios["C"] = _criterio("cumple" if len(resp_c) + len(demostrados) >= len(op.get("indispensables") or []) else "parcial",
                                   " · ".join([r.strip()[:80] for _, r in resp_c] + [f"Demostrado: {d}" for d in demostrados]))

    # D — logística
    resp_d = por.get("logistica", [])
    negativas = [r for q, r in resp_d if _tema_logistica(q) in ("horario", "inicio") and _RE_NEGATIVA_LOGISTICA.search(_n(r))
                 and not _n(r).startswith("solo puedo")]
    if not resp_d:
        criterios["D"] = _criterio("cumple" if previas else "sin_dato", "Cubierto en el prefiltro." if previas else "")
    elif negativas:
        criterios["D"] = _criterio("no_cumple", f"«{negativas[0].strip()[:160]}»")
    else:
        criterios["D"] = _criterio("cumple", " · ".join(r.strip()[:80] for _, r in resp_d))
    return {"criterios": criterios, "alertas": []}


class CriterioIA(BaseModel):
    resultado: Literal["cumple", "parcial", "no_cumple", "sin_dato"]
    evidencia: str = Field(description="Cita o paráfrasis concreta de lo que dijo el candidato. Vacío si sin_dato.")


class EvaluacionOperativaIA(BaseModel):
    A: CriterioIA = Field(description="Experiencia REAL en el oficio (lo excluyente no cuenta).")
    B: CriterioIA = Field(description="Procedimiento: ¿su respuesta a la situación incluye los pasos esperados?")
    C: CriterioIA = Field(description="Indispensables explorados: no_cumple si dijo que no lo cumple o su ejemplo lo contradice.")
    D: CriterioIA = Field(description="Logística: no_cumple SOLO si no puede cubrir el horario, la ubicación o el inicio.")
    alertas: List[str] = Field(default_factory=list, description="SOLO si salió de un trabajo por despido o por un conflicto "
                                                                  "(con quién): «Salida por despido: …» / «Salida por conflicto: …». "
                                                                  "NUNCA habilidades blandas, escolaridad ni documentos.")


def evaluar(transcript: List[dict], guion: dict, previas: Optional[List[dict]] = None, titulo: str = "",
            prefiltro: Optional[dict] = None, extra: Optional[List[str]] = None) -> Tuple[dict, bool]:
    """Criterios A-D (IA o determinista) + Puntos por validar (Cambio 19) + recomendación. `extra`: inconsistencias que
    detectó la evaluación general de la entrevista (contradicciones con el formulario, el prefiltro o el CV)."""
    from . import ia

    op = (guion or {}).get("operativo") or {}
    perfil = op.get("perfil") or {}
    con_ia = False
    base = None
    client = ia._client()
    if client is not None:
        dialogo = "\n".join(f"{'Entrevistadora' if m.get('rol') == 'assistant' else 'Candidato'}: {m.get('texto')}" for m in transcript or [])
        esperado = "; ".join(x.get("texto", "") for x in (perfil.get("situacion") or {}).get("esperado") or [])
        try:
            resp = client.responses.parse(
                model=ia.MODEL,
                instructions=(
                    "Evalúas una ENTREVISTA OPERATIVA (puestos masivos, México) con cuatro criterios y nada más: A experiencia real en "
                    "un puesto similar (con datos concretos: dónde, cuánto tiempo, qué hacía), B procedimiento (la situación planteada), "
                    "C indispensables explorados, D logística. Califica SOLO con lo que dijo el candidato (cumple / parcial / no_cumple "
                    "/ sin_dato) y cita la evidencia. Lo excluyente del oficio NO cuenta como experiencia. Si un dato ya lo confirmó "
                    "en el prefiltro y la entrevista no lo contradice, cuenta. No apliques umbrales propios (minutos, días, meses). "
                    "Nunca evalúes escolaridad, documentos ni habilidades blandas. "
                    f"PROHIBIDO usar datos sobre: {ia.DATOS_SENSIBLES_PROHIBIDOS}."
                ),
                input=(
                    f"Puesto: {titulo}\nOficio: {perfil.get('nombre') or 'no identificado'}\n"
                    f"Excluyentes: {'; '.join(perfil.get('excluyentes') or []) or 'ninguno'}\n"
                    f"Situación planteada: {(perfil.get('situacion') or {}).get('pregunta') or 'ninguna'}\nRespuesta esperada: {esperado or 'n/a'}\n"
                    f"Indispensables explorados: {'; '.join(op.get('indispensables') or []) or 'ninguno'}\n"
                    f"Respuestas previas (prefiltro/formulario):\n" + ("\n".join(f"- {x}" for x in ya_respondido(previas or [])) or "- ninguna")
                    + f"\n\nENTREVISTA:\n{dialogo}"
                ),
                text_format=EvaluacionOperativaIA,
            )
            r = resp.output_parsed
            base = {"criterios": {k: _criterio(getattr(r, k).resultado, getattr(r, k).evidencia) for k, _ in CRITERIOS},
                    "alertas": [a.strip() for a in r.alertas if a and a.strip()]}
            con_ia = True
        except Exception as ex:  # noqa: BLE001 — sin IA disponible se evalúa en modo determinista
            print(f"[operativa] evaluar cayó a modo determinista: {str(ex)[:200]}", flush=True)
    if base is None:
        base = evaluar_demo(transcript, guion, previas)
    if not op.get("indispensables"):
        base["criterios"]["C"] = _criterio("cumple", "La vacante no tiene indispensables por explorar.")
    # de la IA solo se aceptan salidas por despido/conflicto (las demás «alertas» no son Puntos por validar)
    ia_salidas = [a for a in base.get("alertas") or [] if es_salida_conflictiva(a) and not es_soft_skill(a)]
    puntos = puntos_de(base["criterios"], transcript, prefiltro, list(extra or []) + ia_salidas)
    reqs = requisitos_de(base["criterios"], transcript, guion, prefiltro, puntos)
    return resultado(base["criterios"], puntos, perfil, reqs), con_ia


# C6: «Puntos por validar» de la entrevista operativa = hechos verificables; nunca habilidades blandas
_RE_SOFT = re.compile(r"\b(comunicacion|comunica\w*|actitud|liderazgo|trabajo en equipo|proactiv\w*|personalidad|confianza en si|"
                      r"seguridad en si|iniciativa|empatia|habilidades blandas|soft skills?|expresion|se expresa|fluidez|entusiasmo|"
                      r"motivacion|motivado|interes genuino|disposicion|actitud de servicio|orientacion al cliente|"
                      r"inteligencia emocional|manejo del estres|adaptabilidad|creatividad|claridad al hablar|respuestas breves|"
                      r"respuestas cortas|poco detalle|profundiz\w*)\b")


def es_soft_skill(texto: str) -> bool:
    return bool(_RE_SOFT.search(_n(texto)))


_RE_ESCOLARIDAD = re.compile(r"\b(escolaridad|secundaria|preparatoria|bachillerato|certificado de estudios|titulo|estudios|"
                             r"documento|documentos|comprobante|acta|curp|rfc|nss|ine)\b")


def es_alerta_escolaridad(texto: str) -> bool:
    """Escolaridad y documentos se validan en «Documentos», nunca como alerta de la entrevista."""
    return bool(_RE_ESCOLARIDAD.search(_n(texto)))


def contexto_prefiltro(p) -> dict:
    """Lo que el prefiltro ya dejó registrado y alimenta los Puntos por validar (Cambio 19): sueldo (si no lo aceptó),
    condiciones de la vacante no cumplidas, inconsistencias con el formulario y respuestas dudosas."""
    from . import ia

    a = (getattr(p, "analisis", None) or {}) if p is not None else {}
    d = a.get("prefiltro_datos") or {}
    v = getattr(p, "vacante", None) if p is not None else None
    return {"sueldo_aplica": bool(ia.pregunta_sueldo((v.sueldo if v is not None else "") or "")) or "sueldo" in d,
            "sueldo_acepta": d.get("sueldo_acepta"), "sueldo": d.get("sueldo") or "", "sueldo_ofrecido": d.get("sueldo_ofrecido") or "",
            "condiciones": [str(x) for x in a.get("condiciones_no_cumplidas") or [] if str(x or "").strip()],
            "inconsistencias": [texto_inconsistencia(i) for i in a.get("inconsistencias") or [] if isinstance(i, dict)],
            "dudas": [str(x) for x in a.get("puntos_validar_prefiltro") or [] if str(x or "").strip()]}


def _req(estado: str, evidencia: str = "") -> dict:
    return {"estado": estado, "evidencia": (evidencia or "").strip()[:200]}


def _unicos(xs: List[str]) -> List[str]:
    vistos, salida = set(), []
    for x in xs:
        if x and _n(x) not in vistos:
            vistos.add(_n(x))
            salida.append(x)
    return salida


def puntos_de(criterios: Dict[str, dict], transcript: List[dict], prefiltro: Optional[dict], extra: Optional[List[str]] = None) -> List[str]:
    """«Puntos por validar» (Cambio 19) — SOLO: condiciones de la vacante no cumplidas, sueldo no aceptado u otra cifra,
    inconsistencias (con el formulario, el prefiltro o la entrevista) y salidas por despido o conflicto. Nunca habilidades
    blandas, escolaridad como alerta, documentos ni umbrales fijos."""
    pf = prefiltro or {}
    puntos: List[str] = list(pf.get("condiciones") or [])
    if pf.get("sueldo_aplica") and pf.get("sueldo_acepta") is False:
        r = str(pf.get("sueldo") or "").strip()
        if re.search(r"\d", r) or re.search(r"\b(mil|pesos|varos)\b", _n(r)):
            puntos.append(f"Sueldo: pide otra cifra («{r[:80]}»)")
        else:
            puntos.append(f"Sueldo: no aceptó {pf.get('sueldo_ofrecido') or 'el sueldo ofrecido'}" + (f" («{r[:80]}»)" if r else ""))
    puntos += list(pf.get("inconsistencias") or [])
    c = criterios.get("C") or {}
    if c.get("resultado") == "no_cumple":
        puntos.append(f"Inconsistencia: indispensables — cumplía en el prefiltro y en la entrevista dijo {c.get('evidencia') or 'que no'}")
    for x in extra or []:
        x = str(x or "").strip()
        if not x or es_soft_skill(x):
            continue
        if es_salida_conflictiva(x) and not x.lower().startswith(("salida por", "inconsistencia")):
            x = f"Salida por {'conflicto' if _RE_SAL_CONFLICTO.search(_n(x)) else 'despido'}: {x}"
        elif not es_salida_conflictiva(x) and not x.lower().startswith("inconsistencia"):
            continue  # solo inconsistencias y salidas por despido o conflicto son Puntos por validar
        puntos.append(x)
    puntos += salidas_conflictivas(transcript)
    return _unicos(puntos)[:8]


def requisitos_de(criterios: Dict[str, dict], transcript: List[dict], guion: dict, prefiltro: Optional[dict],
                  puntos: Optional[List[str]] = None) -> Dict[str, dict]:
    """Los 2 requisitos de la recomendación operativa (Cambio 19), cada uno ✓ / ✗ / ?: experiencia en un puesto similar
    con datos concretos (A) y ningún Punto por validar."""
    puntos = puntos_de(criterios, transcript, prefiltro) if puntos is None else puntos
    a = criterios.get("A") or {}
    estado_a = {"cumple": "cumple", "parcial": "sin_dato"}.get(a.get("resultado"), "no_cumple")
    dudas = list((prefiltro or {}).get("dudas") or [])
    return {
        "experiencia": _req(estado_a, a.get("evidencia", "") or ("Sin datos concretos de experiencia" if estado_a == "no_cumple" else "")),
        "puntos": {**_req("no_cumple" if puntos else "cumple", " · ".join(puntos)), "cantidad": len(puntos), "dudas": dudas[:4]},
    }


def recomendacion_de(requisitos: Dict[str, dict]) -> str:
    """Cambio 19 (sin umbrales): «Recomendable» = experiencia en puesto similar con datos concretos y NINGÚN Punto por
    validar (ni respuesta dudosa); «No recomendable» = SIN experiencia y CON Puntos por validar; lo demás (algún punto,
    experiencia dudosa o respuesta dudosa) → «Con reservas». Solo recomienda: nunca descarta."""
    exp = (requisitos.get("experiencia") or {}).get("estado")
    pts = requisitos.get("puntos") or {}
    hay_puntos = pts.get("estado") == "no_cumple"
    if exp == "cumple" and not hay_puntos and not pts.get("dudas"):
        return "recomendable"
    if exp == "no_cumple" and hay_puntos:
        return "no_recomendable"
    return "con_reservas"


def faltan_de(requisitos: Dict[str, dict]) -> List[str]:
    """Lo que la tarjeta y la ficha muestran: «✗ Experiencia en puesto similar», «2 puntos por validar»…"""
    salida = []
    exp = requisitos.get("experiencia") or {}
    if exp.get("estado") in ("no_cumple", "sin_dato"):
        salida.append(f"{SIMBOLO[exp['estado']]} Experiencia en puesto similar")
    pts = requisitos.get("puntos") or {}
    n = int(pts.get("cantidad") or (1 if pts.get("estado") == "no_cumple" else 0))
    if n:
        salida.append(f"✗ {n} punto{'s' if n != 1 else ''} por validar")
    if pts.get("dudas"):
        salida.append("? Respuesta dudosa en el prefiltro")
    return salida


def resultado(criterios: Dict[str, dict], puntos: List[str], perfil: Optional[dict] = None,
              requisitos: Optional[Dict[str, dict]] = None) -> dict:
    requisitos = requisitos if requisitos is not None else requisitos_de(criterios, [], {}, None, puntos)
    rec = recomendacion_de(requisitos)
    nombres = dict(REQUISITOS)
    return {
        "oficio": (perfil or {}).get("oficio") or "", "nombre_oficio": (perfil or {}).get("nombre") or "",
        "criterios": {k: {"nombre": nombre, **(criterios.get(k) or _criterio("sin_dato")),
                          "texto": TEXTO_RESULTADO[(criterios.get(k) or {}).get("resultado") or "sin_dato"]} for k, nombre in CRITERIOS},
        "requisitos": {k: {"nombre": nombres[k], **requisitos[k], "simbolo": SIMBOLO[requisitos[k]["estado"]]} for k, _ in REQUISITOS},
        "faltan": faltan_de(requisitos),
        "puntos": list(puntos),
        "alertas": list(puntos),  # compatibilidad: la ficha lee «alertas»
        "recomendacion": rec, "recomendacion_texto": RECOMENDACIONES[rec][0],
        "score": None,  # sin puntaje (ni base 100 ni mínimo 70)
    }


def aplicar(ev, op: dict):
    """Lleva el resultado operativo a la evaluación de siempre (`ia.EvaluacionEntrevista`): la recomendación sale de la
    regla de experiencia + Puntos por validar; «Puntos por validar» = SOLO los de `puntos_de` (Cambio 19). Nunca descarta."""
    ev.recomendacion = RECOMENDACIONES[op["recomendacion"]][1]
    ev.score_entrevista = None  # sin puntaje — la actividad se resuelve con la etiqueta (models.score_de_entrevista)
    ev.riesgos = list(op.get("puntos") or op.get("alertas") or [])
    cumplidos = [f"{c['nombre']}: {c['evidencia']}" for c in op["criterios"].values() if c["resultado"] == "cumple" and c["evidencia"]]
    if not ev.fortalezas or ev.fortalezas == ["Completó la entrevista"]:
        ev.fortalezas = cumplidos[:4] or list(ev.fortalezas or [])
    return ev
