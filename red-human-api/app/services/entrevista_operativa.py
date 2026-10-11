"""Entrevista OPERATIVA (2026-10-10, Cambio 1 — sugerida para las rutas Masivos).

En lugar de que la IA invente todo el guion, el enfoque «operativo» ARMA la entrevista con 6 secciones fijas:

  1. Trayectoria               — fija
  2. Verificación del oficio   — de la Biblioteca de Perfiles (3 datos + 1 situación esperada del oficio)
  3. Indispensables            — la IA los redacta UNA vez al generar el guion (máximo 3 se exploran)
  4. Situación actual          — fija
  5. Motivación                — fija
  6. Logística                 — fija (traslado, horario, inicio) con los datos REALES de la vacante

Biblioteca de Perfiles = 10 oficios en código (`BIBLIOTECA`): nunca se editan globalmente. Cada vacante guarda SU copia
(`Vacante.guiones["perfil_operativo"]`) y solo esa copia se ajusta.

Evaluación (garantizada en código, `evaluar` + `aplicar`): cuatro criterios — A Experiencia en el oficio, B Procedimiento
(la situación esperada), C Indispensables, D Logística. Si C o D fallan la recomendación es «No recomendable» (solo
recomienda: NUNCA descarta; la decisión es de RH). Las alertas (empleo < 3 meses, despido, traslado > 90 min, …) van a
«Puntos por validar».
"""

import copy
import re
import unicodedata
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

ENFOQUE = "operativo"
MAX_INDISPENSABLES = 3
TRASLADO_MAX_MIN = 90
EMPLEO_MIN_MESES = 3

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
            "¿Qué tipo de mercancía manejabas y en qué volumen aproximado?",
            "¿Con qué sistema registrabas las entradas y salidas: papel, Excel, escáner o algún sistema como SAP?",
            "¿Cómo hacían los inventarios o conteos y cada cuándo?",
        ],
        "situacion": {
            "pregunta": "Si al hacer un conteo te faltan 10 piezas contra el sistema, ¿qué haces paso a paso?",
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
            "¿En qué tipo de lugar trabajaste (fábrica, almacén, obra, tienda) y qué hacías en un día normal?",
            "¿Qué herramientas o equipo usabas en tu trabajo?",
            "¿Cómo era tu turno y cuántas horas pasabas de pie o cargando?",
        ],
        "situacion": {
            "pregunta": "Si tu supervisor te pide terminar algo urgente pero falta tu equipo de seguridad, ¿qué haces?",
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
            "¿Qué cargabas y cuánto pesaba más o menos cada bulto o caja?",
            "¿Cargabas a mano o con algún equipo como diablito o patín?",
            "¿Cuántos camiones o tráileres cargaban o descargaban en un turno?",
        ],
        "situacion": {
            "pregunta": "Si al descargar un camión notas cajas dañadas o mojadas, ¿qué haces?",
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
            "¿Qué tipo de montacargas operabas: de combustión, eléctrico, retráctil o contrabalanceado?",
            "¿Qué capacidad tenía y a qué altura acomodabas las tarimas?",
            "¿Qué constancia o licencia tienes para operar montacargas (por ejemplo, la DC-3) y de qué año es?",
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
            "¿Qué máquina o estación operabas: recta, overlock, prensa o línea de ensamble?",
            "¿Cuál era tu meta de producción por hora o por turno y cómo te iba con ella?",
        ],
        "situacion": {
            "pregunta": "Si notas que las piezas que salen de tu estación traen un defecto, ¿qué haces?",
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
            "¿Qué tipo de licencia tienes y cuándo vence?",
            "¿Qué rutas o zonas cubrías y cuántas entregas hacías por día?",
        ],
        "situacion": {
            "pregunta": "Si en la ruta el cliente no está para recibir la mercancía, ¿qué haces?",
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
            "¿Qué tipo de negocio era y a cuántos clientes atendías en un día?",
            "¿Qué formas de pago manejabas: efectivo, tarjeta, vales o transferencias?",
            "¿Cómo hacías tu corte de caja al cerrar?",
        ],
        "situacion": {
            "pregunta": "Al hacer tu corte te faltan 200 pesos. ¿Qué haces?",
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
            "¿Qué tipo de instalación cuidabas: planta, plaza, residencial u oficinas?",
            "¿Qué funciones hacías: control de accesos, rondines o revisión de vehículos?",
            "¿Qué cursos o capacitación de seguridad privada has tomado?",
        ],
        "situacion": {
            "pregunta": "Llega una persona que dice ser proveedor, pero no está en la lista de accesos. ¿Qué haces?",
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
            "¿Qué tipo de lugar limpiabas: oficinas, hospital, planta o plaza?",
            "¿Qué productos químicos o máquinas usabas, como pulidora o hidrolavadora?",
            "¿Cómo se organizaban las áreas y los horarios de limpieza?",
        ],
        "situacion": {
            "pregunta": "Si encuentras un derrame de un químico en un pasillo, ¿qué haces?",
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
            "¿Qué producto vendías y en qué tipo de tienda?",
            "¿Qué meta de venta tenías y cómo te iba con ella normalmente?",
            "¿Qué hacías además de vender: acomodo, inventario o cobro?",
        ],
        "situacion": {
            "pregunta": "Un cliente se queja porque el precio del anaquel es menor que el que le cobran en caja. ¿Qué haces?",
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
    ("indispensables", "Indispensables", "ia"),
    ("situacion_actual", "Situación actual", "fijo"),
    ("motivacion", "Motivación", "fijo"),
    ("logistica", "Logística", "fijo"),
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
RECOMENDACIONES = {"recomendable": ("Recomendable", "avanzar"),
                   "con_reservas": ("Recomendable con reservas", "revision"),
                   "no_recomendable": ("No recomendable", "no_avanzar")}


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


def pregunta_indispensable_demo(req: str) -> str:
    r = req.strip().rstrip(".")
    r = r[0].lower() + r[1:] if len(r) > 1 and not r[:2].isupper() else r
    return f"Platícame dónde y cómo has cumplido con esto: {r}. Dame un ejemplo concreto."


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


def preguntas_logistica(ubicacion: str = "", horario: str = "") -> List[str]:
    traslado = (f"¿Cuánto tiempo harías de tu casa a {ubicacion.strip()} y en qué te transportarías?" if (ubicacion or "").strip()
                else "¿Cuánto tiempo harías de tu casa al trabajo y en qué te transportarías?")
    turno = (f"El horario es {horario.strip()}. ¿Cómo te acomoda con tus demás actividades?" if (horario or "").strip()
             else "¿Qué horario o turno puedes cubrir?")
    return [traslado, turno, "¿Cuándo podrías empezar a trabajar?"]


def armar_guion(titulo: str, perfil: Optional[dict], indispensables: List[str], preguntas_indisp: List[str], *,
                ubicacion: str = "", horario: str = "") -> dict:
    """Guion operativo ARMADO: 6 secciones en orden fijo. Regresa el formato de guion de siempre (`enfoque`, `temas`,
    `preguntas`) + `operativo` (secciones, oficio, perfil congelado) para la entrevistadora y la evaluación."""
    puesto = (titulo or "").strip() or "este puesto"
    reqs = [r for r in (indispensables or []) if str(r or "").strip()][:MAX_INDISPENSABLES]
    secciones = []
    secciones.append({"clave": "trayectoria", "titulo": "Trayectoria", "origen": "fijo", "preguntas": [
        "Cuéntame de tu trabajo más reciente: dónde fue, qué hacías y cuánto tiempo estuviste.",
        "¿Por qué saliste de ese trabajo?",
    ]})
    if perfil:
        oficio = list(perfil.get("datos") or [])[:3] + [str((perfil.get("situacion") or {}).get("pregunta") or "")]
    else:
        oficio = [f"¿Qué experiencia tienes en un puesto de {puesto}?", "¿Qué tareas hacías en un día normal?"]
    secciones.append({"clave": "oficio", "titulo": "Verificación del oficio", "origen": "biblioteca" if perfil else "fijo",
                      "preguntas": [q for q in oficio if q.strip()]})
    if reqs:
        secciones.append({"clave": "indispensables", "titulo": "Indispensables", "origen": "ia",
                          "preguntas": list(preguntas_indisp or [])[:len(reqs)] or [pregunta_indispensable_demo(r) for r in reqs],
                          "requisitos": reqs})
    secciones.append({"clave": "situacion_actual", "titulo": "Situación actual", "origen": "fijo",
                      "preguntas": ["Platícame cómo está tu situación laboral ahora mismo."]})
    secciones.append({"clave": "motivacion", "titulo": "Motivación", "origen": "fijo",
                      "preguntas": [f"¿Qué te interesa de este trabajo de {puesto}?"]})
    secciones.append({"clave": "logistica", "titulo": "Logística", "origen": "fijo", "preguntas": preguntas_logistica(ubicacion, horario)})
    preguntas = [q for s in secciones for q in s["preguntas"]]
    perfil_congelado = None
    if perfil:
        perfil_congelado = {k: copy.deepcopy(perfil.get(k)) for k in ("oficio", "nombre", "sinonimos", "palabras", "excluyentes",
                                                                       "palabras_excluyentes", "datos", "situacion")}
    return {
        "enfoque": f"Entrevista operativa para {puesto}: trayectoria, verificación del oficio, indispensables, situación actual, "
                   "motivación y logística.",
        "temas": [s["titulo"] for s in secciones],
        "preguntas": preguntas,
        "operativo": {"oficio": (perfil or {}).get("oficio") or "", "nombre_oficio": (perfil or {}).get("nombre") or "",
                      "secciones": secciones, "indispensables": reqs, "perfil": perfil_congelado},
    }


def normalizar_operativo(op) -> Optional[dict]:
    """Conserva el bloque `operativo` de un guion (lo que RH edita del guion son las preguntas; esto es la estructura)."""
    if not isinstance(op, dict) or not isinstance(op.get("secciones"), list):
        return None
    secciones = []
    for s in op["secciones"]:
        if isinstance(s, dict) and s.get("clave") in TITULO_SECCION:
            secciones.append({"clave": s["clave"], "titulo": TITULO_SECCION[s["clave"]], "origen": s.get("origen") or "fijo",
                              "preguntas": _textos(s.get("preguntas"), 300, 6), **({"requisitos": _textos(s.get("requisitos"), 200, 3)}
                                                                                   if s.get("requisitos") else {})})
    if not secciones:
        return None
    return {"oficio": op.get("oficio") if op.get("oficio") in BIBLIOTECA else "", "nombre_oficio": str(op.get("nombre_oficio") or "")[:80],
            "secciones": secciones, "indispensables": _textos(op.get("indispensables"), 200, MAX_INDISPENSABLES),
            "perfil": op.get("perfil") if isinstance(op.get("perfil"), dict) else None}


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


def preguntas_para_conversacion(guion: dict, previas: List[dict]) -> List[str]:
    """Preguntas del guion operativo SIN las de logística que el prefiltro ya cubrió (ubicación/traslado, horario,
    inicio): una pregunta por mensaje y sin repetir lo del prefiltro (garantizado en código, no solo en el prompt)."""
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
    logistica = {q for s in op.get("secciones") or [] if s.get("clave") == "logistica" for q in s.get("preguntas") or []}
    return [q for q in preguntas if not (q in logistica and _tema_logistica(q) in cubiertos)]


def bloque_prompt(guion: dict, previas: List[dict]) -> str:
    """Instrucciones del modo Operativo para el prompt del entrevistador (piezas fijas, en orden)."""
    op = (guion or {}).get("operativo") or {}
    lista = preguntas_para_conversacion(guion, previas)
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
        "Si la respuesta es vaga, repregunta UNA vez pidiendo un dato concreto (dónde, cuánto tiempo, qué equipo) y sigue.\n"
        + "\n".join(lineas) + "\n"
        + (f"EXCLUYENTES DEL OFICIO ({perfil.get('nombre') or 'oficio'}): {excl} Si su experiencia es con algo excluyente, "
           "pregúntale con naturalidad si tiene experiencia con lo que pide el puesto; no le expliques la regla.\n" if excl else "")
        + ("YA LO RESPONDIÓ EN EL PREFILTRO O EL FORMULARIO (NO lo vuelvas a preguntar; si hace falta, parte de ahí):\n"
           + "\n".join(f"- {x}" for x in previo) + "\n" if previo else "")
        + "No hables de escolaridad ni pidas documentos: eso se valida en Documentos.\n"
    )


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
    catalogo = [(s["clave"], q, set(_n(q).split())) for s in op.get("secciones") or [] for q in s.get("preguntas") or []]
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


def alertas_deterministas(transcript: List[dict], guion: dict) -> List[str]:
    """Alertas garantizadas en código (Puntos por validar): empleo < 3 meses, despido, traslado > 90 min, trabaja
    actualmente (confirmar fecha de salida), horario o inicio condicionados."""
    por = respuestas_por_seccion(transcript, guion)
    alertas: List[str] = []
    trayectoria = " ".join(r for _, r in por.get("trayectoria", []))
    candidato = " ".join(str(m.get("texto") or "") for m in transcript or [] if m.get("rol") == "user")
    for _, r in por.get("trayectoria", []) or [("", candidato)]:
        meses = _meses(r)
        if meses is not None and meses < EMPLEO_MIN_MESES and _RE_CONTEXTO_EMPLEO.search(_n(r)):
            alertas.append(f"Empleo reciente de menos de {EMPLEO_MIN_MESES} meses («{r.strip()[:90]}»).")
            break
    if _RE_DESPIDO.search(_n(trayectoria or candidato)):
        alertas.append("Mencionó un despido en su trayectoria: validar el motivo con referencias.")
    for q, r in por.get("logistica", []):
        tema = _tema_logistica(q)
        if tema == "traslado":
            mins = minutos_traslado(r)
            if mins is not None and mins > TRASLADO_MAX_MIN:
                alertas.append(f"Traslado de más de {TRASLADO_MAX_MIN} minutos (~{mins} min).")
        elif tema in ("horario", "inicio") and _RE_NEGATIVA_LOGISTICA.search(_n(r)):
            alertas.append(("Horario condicionado" if tema == "horario" else "Fecha de inicio condicionada") + f": «{r.strip()[:90]}».")
    actual = " ".join(r for _, r in por.get("situacion_actual", []))
    if _RE_TRABAJA.search(_n(actual)):
        alertas.append("Trabaja actualmente: confirmar su fecha de salida y disponibilidad de inicio.")
    return alertas


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
            criterios["A"] = _criterio("parcial", texto_a[:200])
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

    # C — indispensables (máximo 3 explorados; lo demás lo cubrió el prefiltro)
    resp_c = por.get("indispensables", [])
    if not op.get("indispensables"):
        criterios["C"] = _criterio("cumple", "La vacante no tiene indispensables por explorar.")
    elif not resp_c:
        criterios["C"] = _criterio("sin_dato")
    elif any(_RE_NEGACION.search(_n(r)) for _, r in resp_c):
        r = next(r for _, r in resp_c if _RE_NEGACION.search(_n(r)))
        criterios["C"] = _criterio("no_cumple", f"«{r.strip()[:160]}»")
    else:
        criterios["C"] = _criterio("cumple" if len(resp_c) >= len(op.get("indispensables") or []) else "parcial",
                                   " · ".join(r.strip()[:80] for _, r in resp_c))

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
    alertas: List[str] = Field(default_factory=list, description="Alertas para RH: empleo de menos de 3 meses, despido, traslado de "
                                                                  "más de 90 minutos, rotación alta, horario condicionado, etc. NUNCA "
                                                                  "escolaridad ni documentos.")


def evaluar(transcript: List[dict], guion: dict, previas: Optional[List[dict]] = None, titulo: str = "") -> Tuple[dict, bool]:
    """Criterios A-D (IA o determinista) + alertas (las deterministas SIEMPRE se suman) + recomendación estricta."""
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
                    "el oficio, B procedimiento (la situación planteada), C indispensables explorados, D logística. Califica SOLO con "
                    "lo que dijo el candidato (cumple / parcial / no_cumple / sin_dato) y cita la evidencia. Lo excluyente del oficio "
                    "NO cuenta como experiencia. Si un dato ya lo confirmó en el prefiltro y la entrevista no lo contradice, cuenta. "
                    "Nunca evalúes escolaridad ni documentos (se validan aparte). "
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
    alertas = []
    for a in alertas_deterministas(transcript, guion) + list(base.get("alertas") or []):
        if a and not es_alerta_escolaridad(a) and _n(a) not in {_n(x) for x in alertas}:
            alertas.append(a)
    return resultado(base["criterios"], alertas, perfil), con_ia


_RE_ESCOLARIDAD = re.compile(r"\b(escolaridad|secundaria|preparatoria|bachillerato|certificado de estudios|titulo|estudios|"
                             r"documento|documentos|comprobante|acta|curp|rfc|nss|ine)\b")


def es_alerta_escolaridad(texto: str) -> bool:
    """Escolaridad y documentos se validan en «Documentos», nunca como alerta de la entrevista."""
    return bool(_RE_ESCOLARIDAD.search(_n(texto)))


def recomendacion_de(criterios: Dict[str, dict]) -> str:
    """Regla ESTRICTA: falla en indispensables (C) o logística (D) → «No recomendable» (no descarta: RH decide). Todo
    cumple → «Recomendable». Lo demás → «Recomendable con reservas»."""
    res = {k: (criterios.get(k) or {}).get("resultado") for k, _ in CRITERIOS}
    if res.get("C") == "no_cumple" or res.get("D") == "no_cumple":
        return "no_recomendable"
    if all(v == "cumple" for v in res.values()):
        return "recomendable"
    return "con_reservas"


PUNTOS = {"cumple": 25, "parcial": 12, "sin_dato": 8, "no_cumple": 0}


def resultado(criterios: Dict[str, dict], alertas: List[str], perfil: Optional[dict] = None) -> dict:
    rec = recomendacion_de(criterios)
    return {
        "oficio": (perfil or {}).get("oficio") or "", "nombre_oficio": (perfil or {}).get("nombre") or "",
        "criterios": {k: {"nombre": nombre, **(criterios.get(k) or _criterio("sin_dato")),
                          "texto": TEXTO_RESULTADO[(criterios.get(k) or {}).get("resultado") or "sin_dato"]} for k, nombre in CRITERIOS},
        "alertas": alertas[:8],
        "recomendacion": rec, "recomendacion_texto": RECOMENDACIONES[rec][0],
        "score": sum(PUNTOS[(criterios.get(k) or {}).get("resultado") or "sin_dato"] for k, _ in CRITERIOS),
    }


PREFIJO_ALERTA = "Alerta: "


def aplicar(ev, op: dict):
    """Lleva el resultado operativo a la evaluación de siempre (`ia.EvaluacionEntrevista`): recomendación y score de la
    entrevista salen de los criterios A-D; las alertas encabezan «Puntos por validar». Nunca descarta."""
    ev.recomendacion = RECOMENDACIONES[op["recomendacion"]][1]
    ev.score_entrevista = int(op["score"])
    riesgos = [r for r in (ev.riesgos or []) if not es_alerta_escolaridad(r)]
    ev.riesgos = [f"{PREFIJO_ALERTA}{a}" for a in op["alertas"]] + [r for r in riesgos if not r.startswith(PREFIJO_ALERTA)]
    cumplidos = [f"{c['nombre']}: {c['evidencia']}" for c in op["criterios"].values() if c["resultado"] == "cumple" and c["evidencia"]]
    if not ev.fortalezas or ev.fortalezas == ["Completó la entrevista"]:
        ev.fortalezas = cumplidos[:4] or list(ev.fortalezas or [])
    return ev
