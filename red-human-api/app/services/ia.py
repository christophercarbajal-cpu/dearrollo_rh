"""
Servicio de IA — OpenAI.

API necesaria: OPENAI_API_KEY  →  https://platform.openai.com (API Keys)
Sin clave, cada función regresa un resultado determinista en "modo demo"
para que la plataforma siga funcionando de punta a punta.
"""

import json
import re
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from ..config import settings
from ..fechas import TZ_ORG

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..models import Postulacion

MODEL = settings.openai_model  # gpt-5.6-luna (configurable con OPENAI_MODEL en .env)


def _client():
    if not settings.openai_api_key:
        return None
    from openai import OpenAI

    return OpenAI(api_key=settings.openai_api_key)


def ia_activa() -> bool:
    return bool(settings.openai_api_key)


# ============================================================
# 1) Generador de publicaciones de vacante (módulo 3.5)
# ============================================================

SENIORITY = Literal["Sin experiencia", "Junior", "Semi-senior", "Senior", "Jefatura", "Dirección"]


class BloquePlataforma(BaseModel):
    """Publicación lista para pegar en una plataforma concreta.

    `texto_copy` viaja como `copy` en el JSON (alias): `copy` chocaría con un
    atributo de BaseModel, pero es el nombre que espera el frontend.
    """

    model_config = ConfigDict(populate_by_name=True)

    titulo: str = Field(description="Título del puesto optimizado para esta plataforma.")
    texto_copy: str = Field(alias="copy", description="Texto corto de difusión (anuncio/post) para esta plataforma.")
    page: str = Field(description="Cuerpo completo de la publicación, listo para pegar en el formulario de la plataforma.")
    etiquetas: List[str] = Field(description="Palabras clave o skills que usa esta plataforma para hacer match.")

    def bloque(self) -> dict:
        return self.model_dump(by_alias=True)


class PreguntaRequisito(BaseModel):
    """Cambio 31: la pregunta NATURAL con la que el agente explora un requisito indispensable (se usa tal cual)."""

    requisito: str = Field(description="El requisito indispensable, copiado literal.")
    pregunta: str = Field(description=(
        "Pregunta DIRECTA, como la haría un reclutador por chat, que pide el DATO concreto del requisito (ej. «¿Cuántos años "
        "llevas en derecho corporativo?», «¿Puedes cargar material pesado?», «¿Tienes disponibilidad para rolar turnos?»). "
        "Una sola idea, sin «Para confirmar», sin opciones. PROHIBIDO el relleno conversacional («Cuéntame,», «Platícame,», "
        "«Dime,») y PROHIBIDO pegar el requisito en una frase fija («Para esta vacante se pide: …»)."))


class PreguntaFiltro(BaseModel):
    """Pregunta de prefiltro ligada a un requisito, con criterio de descarte explícito."""

    pregunta: str = Field(description="Pregunta cerrada, en español mexicano, directa al candidato.")
    tipo: Literal["si_no", "numero", "opcion", "texto_corto"]
    valida: str = Field(description="Requisito indispensable que valida esta pregunta.")
    respuesta_esperada: str = Field(description="Respuesta que indica que la persona cumple (ej. 'Sí', '>= 2 años').")
    descarta: bool = Field(description="true si NO cumplirla es motivo de descarte (knock-out) para este puesto.")
    opciones: List[str] = Field(
        default_factory=list,
        description=(
            "SOLO para tipo='numero': 4 rangos ordenados de menor a mayor que el candidato puede "
            "elegir (ej. 'Menos de 1 año', '1-2 años', '2-3 años', 'Más de 3 años'), ajustados al "
            "nivel de experiencia que pide la vacante. Para tipo='si_no' u 'opcion' déjalo vacío."
        ),
    )


class VacanteGenerada(BaseModel):
    resumen: str = Field(description="Gancho de 1-2 frases que resume la oportunidad.")
    descripcion: str = Field(description="Descripción del puesto en 2-3 párrafos cortos, español mexicano, sin exagerar.")
    perfil_ideal: str = Field(description="En 2-3 frases, a quién le queda esta vacante (experiencia, actitudes, contexto).")
    responsabilidades: List[str] = Field(description="5 a 7 actividades principales del puesto, en infinitivo.")
    requisitos_indispensables: List[str] = Field(description=(
        "MÁXIMO 5 requisitos sin los cuales la persona no puede ocupar el puesto. Nunca una tarea o responsabilidad (eso va "
        "en responsabilidades). Si el seniority es «Sin experiencia», PROHIBIDO un requisito de experiencia. Lo que la "
        "descripción marque como «preferentemente» va a requisitos_deseables. Requisitos IMPLÍCITOS (Cambio 43): si el "
        "puesto es una profesión regulada (abogado, contador, médico, ingeniero, arquitecto, psicólogo…), propón «Cédula "
        "profesional»; si el seniority es Senior o superior, propón «[n] años de experiencia en [área]» (Senior 5, "
        "Jefatura 7, Dirección 10)."))
    # Cambio 37: los indispensables que NO capturó RH (los propone Red Human). Lo llena el sistema: déjalo vacío.
    indispensables_propuestos: List[str] = Field(default_factory=list, description="Déjalo vacío: lo llena el sistema.")
    requisitos_deseables: List[str] = Field(description="2 a 4 requisitos que suman pero no descartan.")
    beneficios: List[str] = Field(description="Prestaciones y beneficios; SOLO los que se puedan sostener con los datos dados.")
    palabras_clave: List[str] = Field(description="8 a 12 términos con los que los candidatos en México buscan este puesto.")
    seniority: SENIORITY
    rango_salarial_sugerido: str = Field(description="Rango mensual bruto en MXN sugerido para el puesto y la plaza.")
    avisos_cumplimiento: List[str] = Field(
        description="Ajustes hechos por cumplimiento legal mexicano (LFT art. 3 y 133: no discriminación) o datos faltantes que RH debe confirmar."
    )
    texto_whatsapp: str = Field(description="Mensaje breve para WhatsApp/estados, emojis moderados, con llamado a la acción.")
    occ: BloquePlataforma = Field(description="Publicación para OCC Mundial.")
    linkedin: BloquePlataforma = Field(description="Publicación para LinkedIn.")
    portal: BloquePlataforma = Field(description="Publicación para el portal propio de Red Human.")
    preguntas_filtro: List[PreguntaFiltro] = Field(
        description=(
            "4 a 6 preguntas de prefiltro WEB ligadas a los requisitos indispensables. TODAS cerradas: se responden "
            "ÚNICAMENTE con Sí / No (tipo 'si_no'). Prohibido texto libre: nada de «Describe», «Explica», "
            "«Cuéntanos», «Menciona». Un requisito numérico se convierte en umbral («¿Tienes al menos 2 años de experiencia en caja?»)."
        )
    )
    # Cambio 31: una pregunta NATURAL por requisito indispensable (el motor de conversación la usa tal cual)
    preguntas_requisitos: List[PreguntaRequisito] = Field(
        default_factory=list,
        description=(
            "UNA por cada requisito indispensable: la pregunta directa en lenguaje natural con la que un reclutador pediría "
            "el dato concreto (ej. «¿Cuántos años llevas en derecho corporativo?», «¿Qué tipo de licencia de manejo "
            "tienes?», «¿Puedes cargar material pesado?»). Las de Sí/No son DIRECTAS, sin relleno («Cuéntame,», «Platícame,») "
            "y sin pegar el requisito en una frase fija («Para esta vacante se pide: …»). Nunca «¿Cuánto tiempo de "
            "experiencia en…?», nunca «Para confirmar…», nunca Sí/No con opciones. La escolaridad SIEMPRE es «¿Cuál es tu "
            "último grado de estudios?»."
        ),
    )
    # 2026-09-16 (prefiltro dual): la IA elige SOLO los puntos críticos que vale la pena confirmar por chat.
    preguntas_filtro_whatsapp: List[PreguntaFiltro] = Field(
        default_factory=list,
        description=(
            "2 a 3 preguntas para WhatsApp que CONFIRMAN solo los puntos críticos (experiencia, ubicación/"
            "traslado, disponibilidad o el requisito eliminatorio principal), en tono conversacional; nunca "
            "repiten toda la lista web. UNA pregunta por criterio: prohibido preguntar dos cosas en la misma frase "
            "(años de experiencia Y uso de un software van en dos preguntas). Aquí sí se aceptan respuestas abiertas."
        ),
    )


_PLANTILLAS = (
    "PLANTILLAS POR PLATAFORMA — respeta el formato de cada una:\n\n"
    "OCC Mundial (occ.com.mx):\n"
    "· titulo: máx. 70 caracteres, sin emojis, formato «Puesto - Ciudad».\n"
    "· copy: 1 párrafo de máx. 300 caracteres para el anuncio destacado; menciona sueldo y ciudad.\n"
    "· page: TEXTO PLANO (OCC no admite markdown). Secciones en MAYÚSCULAS seguidas de viñetas con «• »:\n"
    "  SOBRE LA VACANTE / ACTIVIDADES PRINCIPALES / REQUISITOS / OFRECEMOS / CÓMO POSTULARTE.\n"
    "· etiquetas: 6-10 términos de búsqueda que usa un candidato mexicano en OCC.\n\n"
    "LinkedIn:\n"
    "· titulo: máx. 100 caracteres, incluye seniority y modalidad (Presencial/Híbrido/Remoto).\n"
    "· copy: post para el feed de la página, máx. 700 caracteres. Primera línea = gancho de una sola frase, "
    "luego 3-4 renglones cortos, cierra con llamado a la acción y exactamente 3 hashtags al final.\n"
    "· page: descripción para LinkedIn Jobs con encabezados en negritas markdown y viñetas «- »:\n"
    "  **Sobre el rol** / **Lo que harás** / **Lo que buscamos** / **Lo que ofrecemos**.\n"
    "· etiquetas: skills tal como aparecen en el catálogo de LinkedIn.\n\n"
    "Portal propio (página pública /aplicar):\n"
    "· titulo: título comercial y claro.\n"
    "· copy: meta descripción SEO de máx. 160 caracteres.\n"
    "· page: texto de la landing con markdown, cálido y en segunda persona («tú»), cerrando con la invitación a postularse.\n"
    "· etiquetas: palabras clave para SEO."
)

_REGLAS = (
    "Eres el redactor de vacantes de Red Human, plataforma de RH en México. Escribes en español mexicano, "
    "claro, inclusivo y concreto.\n"
    "REGLAS NO NEGOCIABLES:\n"
    "1. NUNCA inventes condiciones reales: sueldo, periodicidad de pago, ubicación, modalidad, horario ni "
    "prestaciones/beneficios. Usa EXCLUSIVAMENTE lo que trae la ficha. Si un dato no viene, no lo asumas ni lo "
    "rellenes con un valor típico: omítelo del texto público y anótalo en avisos_cumplimiento como pendiente "
    "de confirmar por RH. `beneficios` de salida = EXACTAMENTE las prestaciones capturadas por RH (lista vacía "
    "si no capturó ninguna; no agregues 'prestaciones de ley' ni nada parecido por tu cuenta).\n"
    "2. RESPETA lo capturado por RH: los requisitos indispensables capturados salen literal y como "
    "indispensables; los deseables capturados salen literal y como deseables. Puedes AGREGAR requisitos que "
    "falten, pero nunca reclasificar, reescribir ni quitar los capturados. La descripción breve capturada es "
    "la guía obligatoria de la descripción completa (mismo sentido, sin contradecirla). El seniority es el "
    "capturado: úsalo tal cual en todos los títulos.\n"
    "2b. REQUISITOS INDISPENSABLES (Cambio 37): MÁXIMO 5 en total. Nunca una tarea o responsabilidad («Atender clientes», "
    "«Realizar inventarios» van en responsabilidades). Si el seniority es «Sin experiencia», PROHIBIDO un indispensable de "
    "experiencia. Lo que la descripción marque como «preferentemente» / «de preferencia» / «deseable» va a "
    "requisitos_deseables. Si RH no capturó indispensables, propón SOLO condiciones explícitas de la descripción "
    "(licencia, horario, turnos).\n"
    "3. Cumplimiento LFT (art. 3 y 133): prohibido pedir o insinuar edad, sexo, estado civil, embarazo, religión, "
    "apariencia, origen étnico, condición de salud u orientación. Si el usuario los incluyó en los requisitos, "
    "reescríbelos en términos de competencias y regístralo en avisos_cumplimiento.\n"
    "4. Usa lenguaje incluyente con la forma «(a)» del español mexicano (Cajero(a), Repartidor(a)).\n"
    "5. PREGUNTAS FILTRO (`preguntas_requisitos`, Cambio 37): UNA pregunta por requisito indispensable, en lenguaje natural "
    "y pidiendo el DATO concreto («¿Cuántos años de experiencia tienes en derecho corporativo?», «¿Tienes licencia de "
    "manejo tipo B?»). Las de Sí/No (atributos o condiciones) son preguntas DIRECTAS: «¿Puedes cargar material pesado?», "
    "«¿Tienes disponibilidad para rolar turnos?». PROHIBIDO el relleno conversacional en preguntas cerradas o de opción "
    "(«Cuéntame,», «Platícame,», «Dime,»). PROHIBIDO copiar el requisito como pregunta («¿Cumples con…?») o pegarlo en una "
    "frase fija («Para esta vacante se pide: …»), y frases como «¿Cuál es tu caso?» o «Para confirmar». `preguntas_filtro` y `preguntas_filtro_whatsapp` déjalas vacías: el sistema arma la lista única.\n"
    "6. Cada plataforma tiene su propio tono y formato: no repitas el mismo texto en las tres.\n"
    "7. Todo lo que escribas en `copy` y `page` lo lee el candidato. Nunca uses etiquetas internas como "
    "«indicado por RH», «según RH» o «no especificado»: escribe el sueldo directo («$10,500 mensuales») solo "
    "si viene en la ficha; si es «A convenir» o no viene, simplemente no menciones cifras.\n"
    "8. `rango_salarial_sugerido` es solo una referencia informativa para RH: nunca lo uses en los textos."
)


# Respaldo genérico cuando una pregunta tipo='numero' (típicamente años de experiencia) se queda
# sin `opciones` — tanto en modo demo como si la IA real no las llenó.
_RANGO_ANOS_GENERICO = ["Menos de 1 año", "1-2 años", "2-3 años", "Más de 3 años"]


class FichaVacante(BaseModel):
    """Lo que RH capturó ANTES de generar (Parte 3). Es la única fuente de condiciones reales."""

    titulo: str
    area: str = ""
    seniority: str = ""
    ubicacion: str = ""
    modalidad: str = ""
    sueldo_texto: str = ""  # texto derivado (models.texto_sueldo) o «A convenir»; "" = no capturado
    empresa: str = ""
    descripcion_breve: str = ""
    requisitos_indispensables: List[str] = Field(default_factory=list)
    requisitos_deseables: List[str] = Field(default_factory=list)
    beneficios: List[str] = Field(default_factory=list)
    # 2026-10-09: ¿la ruta de la vacante pide CV? (Análisis de CV o solicitud con CV). None = no se sabe → como siempre.
    pide_cv: Optional[bool] = None


def _clave_texto(t: str) -> str:
    import unicodedata
    return " ".join(unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower().split())


def _unir_capturado(capturados: List[str], generados: List[str], excluir: Optional[List[str]] = None) -> List[str]:
    """Capturados primero y literal; después lo generado que no repita ni pertenezca a `excluir`."""
    vistos = {_clave_texto(x) for x in capturados if x.strip()}
    prohibidos = {_clave_texto(x) for x in (excluir or []) if x.strip()}
    salida = [x.strip() for x in capturados if x.strip()]
    for g in generados or []:
        k = _clave_texto(g)
        if not k or k in vistos or k in prohibidos:
            continue
        vistos.add(k)
        salida.append(g.strip())
    return salida


# ============================================================ Cambio 37: reglas de los requisitos indispensables

MAX_INDISPENSABLES = 5
_RE_PREFERENTE = re.compile(r"\b(preferentemente|de preferencia|preferible\w*|deseable\w*|se valora\w*|valorable|es un plus|plus)\b",
                            re.IGNORECASE)
# Verbos con los que SÍ empieza un requisito («Saber leer y escribir», «Contar con licencia»); cualquier otro infinitivo al
# inicio describe una TAREA («Atender clientes», «Realizar inventarios») y nunca es indispensable
_VERBOS_REQUISITO = {"tener", "contar", "saber", "manejar", "poder", "vivir", "ser", "estar", "hablar", "dominar", "conocer",
                     "radicar", "residir", "disponer", "leer", "escribir"}
_RE_LICENCIA_DESC = re.compile(r"licencia(?:\s+de\s+(?:manejo|conducir|chofer))?(?:\s+(?:tipo|clase))?\s+([a-e])\b|"
                               r"licencia\s+de\s+(?:manejo|conducir|chofer)(?:\s+vigente)?", re.IGNORECASE)
_RE_HORARIO_DESC = re.compile(r"\b(?:horario|turno)s?\s*(?:de|:)?\s*((?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|\d)"
                              r"[^.;\n]{2,60})", re.IGNORECASE)
_RE_TURNOS_DESC = re.compile(r"\b(rolar turnos|turnos rotativos|rotar turnos|turno nocturno|fines de semana|tiempo completo)\b", re.IGNORECASE)


def es_tarea(req: str, responsabilidades: Optional[List[str]] = None) -> bool:
    """Una tarea o responsabilidad del puesto NO puede ser requisito indispensable (Cambio 37)."""
    k = _clave_texto(req).strip(" .")
    if not k:
        return False
    for r in responsabilidades or []:
        rk = _clave_texto(r).strip(" .")
        if rk and (k == rk or (len(k) > 15 and (k in rk or rk in k))):
            return True
    primera = k.split()[0]
    return bool(re.fullmatch(r"[a-z]+(ar|er|ir)(se)?", primera)) and primera not in _VERBOS_REQUISITO


def es_de_experiencia(req: str) -> bool:
    return "experiencia" in _clave_texto(req) or bool(_RE_ANOS.search(req or ""))


def _oraciones(texto: str) -> List[str]:
    return [x.strip() for x in re.split(r"[.;\n•]+", texto or "") if x.strip()]


def preferentes_de(texto: str) -> List[str]:
    """«Preferentemente con experiencia en almacén» → «Experiencia en almacén» (va a Requisitos deseables)."""
    salida = []
    for o in _oraciones(texto):
        for m in _RE_PREFERENTE.finditer(o):
            resto = o[m.end():].strip(" ,:")
            resto = re.sub(r"^(?:que\s+)?(?:con|tener|tenga|cuente con|contar con|haber)\s+", "", resto, flags=re.IGNORECASE).strip(" ,.")
            resto = re.split(r",|\by\s+(?:que|se)\b", resto)[0].strip(" .")
            if 3 <= len(resto) <= 80:
                salida.append(resto[:1].upper() + resto[1:])
    return salida


def condiciones_explicitas(texto: str) -> List[str]:
    """Condiciones EXPLÍCITAS de la descripción que pueden ser indispensables (licencia, horario, turnos, escolaridad).
    Nunca lo marcado como «preferentemente» (eso es deseable). Determinista: nada que el texto no diga."""
    salida: List[str] = []
    for o in _oraciones(texto):
        if _RE_PREFERENTE.search(o):
            continue
        m = _RE_LICENCIA_DESC.search(o)
        if m:
            salida.append(f"Licencia de manejo tipo {m.group(1).upper()}" if m.group(1) else "Licencia de manejo vigente")
        h = _RE_HORARIO_DESC.search(o)
        if h:
            salida.append(f"Disponibilidad para el horario: {h.group(1).strip(' ,.')}")
        elif _RE_TURNOS_DESC.search(o):
            salida.append(f"Disponibilidad para {_RE_TURNOS_DESC.search(o).group(1).lower()}")
        n = _RE_NIVEL_DESC.search(o)
        if n and n.group(2):  # solo un nivel con «terminada/concluida» explícito
            salida.append(n.group(0)[:1].upper() + n.group(0)[1:])
    vistos, unicas = set(), []
    for r in salida:
        if _clave_texto(r) not in vistos:
            vistos.add(_clave_texto(r))
            unicas.append(r)
    return unicas


def indispensable_por_defecto(modalidad: str, ubicacion: str) -> str:
    """«Disponibilidad para trabajar [modalidad] en [ubicación]» (Cambio 37), sin inventar lo que RH no capturó."""
    mod = (modalidad or "presencial").strip().lower()
    lugar = (ubicacion or "").strip()
    return f"Disponibilidad para trabajar {mod}" + (f" en {lugar}" if lugar and not mod.startswith("remot") else "")


# Cambio 43 (2026-10-14): requisitos IMPLÍCITOS que Red Human propone («Propuesto por Red Human», dentro del máximo de 5)
_RE_PROFESION_REGULADA = re.compile(
    r"\b(abogad\w*|licenciad\w* en derecho|juridic\w*|contador\w*|contadora|contaduria|contable publico|medic[oa]s?|"
    r"doctor\w*|enfermer\w*|ingenier\w*|arquitect\w*|psicolog\w*|odontolog\w*|dentista|cirujan\w*|nutriolog\w*|"
    r"quimic\w* farmac\w*|farmaceutic\w*|actuari\w*|veterinari\w*|notari\w*|corredor publico)\b")
ANOS_POR_SENIORITY = {"senior": 5, "jefatura": 7, "direccion": 10}
CEDULA_PROFESIONAL = "Cédula profesional"


def es_profesion_regulada(titulo: str) -> bool:
    return bool(_RE_PROFESION_REGULADA.search(_clave_texto(titulo)))


def indispensables_implicitos(titulo: str, area: str = "", seniority: str = "") -> List[str]:
    """Cambio 43: «Cédula profesional» si el puesto es una profesión regulada; «[n] años de experiencia en [área]» si el
    seniority es Senior o superior (Senior 5 · Jefatura 7 · Dirección 10). Determinista."""
    salida = []
    if es_profesion_regulada(titulo):
        salida.append(CEDULA_PROFESIONAL)
    n = ANOS_POR_SENIORITY.get(_clave_texto(seniority).strip())
    if n:
        tema = " ".join(str(area or "").split()) or " ".join(str(titulo or "").split()) or "el puesto"
        salida.append(f"{n} años de experiencia en {_minuscula_inicial(tema)}")
    return salida


def reglas_indispensables(capturados: List[str], generados: List[str], *, seniority: str = "", descripcion: str = "",
                          responsabilidades: Optional[List[str]] = None, deseables: Optional[List[str]] = None,
                          modalidad: str = "", ubicacion: str = "", titulo: str = "",
                          area: str = "") -> Tuple[List[str], List[str], List[str]]:
    """Cambio 37 — (indispensables, propuestos por Red Human, deseables extra). Lo capturado por RH se respeta literal.
    * máximo 5 indispensables (lo de RH nunca se recorta; lo propuesto solo completa hasta 5);
    * seniority «Sin experiencia» → PROHIBIDO proponer indispensables de experiencia;
    * una tarea o responsabilidad nunca es indispensable; lo «preferentemente…» va a deseables;
    * sin indispensables de RH → SOLO las condiciones explícitas de la descripción; si no hay, el de disponibilidad."""
    sin_exp = (seniority or "").strip().lower() == "sin experiencia"
    capt = [x.strip() for x in capturados or [] if x.strip()]
    preferentes = preferentes_de(descripcion)
    claves_pref = {_clave_texto(x) for x in preferentes}
    claves_des = {_clave_texto(x) for x in deseables or []}

    from .preguntas_filtro import _RE_RASGO

    def admisible(r: str) -> bool:
        k = _clave_texto(r)
        return (bool(k) and not es_tarea(r, responsabilidades) and not es_autopercepcion(r) and not _RE_RASGO.search(k)
                and not (sin_exp and es_de_experiencia(r)) and k not in claves_des
                and not any(p in k or k in p for p in claves_pref if len(p) > 6))

    # Cambio 43: los implícitos van primero entre lo propuesto (salvo que RH ya capture uno equivalente)
    implicitos = [x for x in indispensables_implicitos(titulo, area, seniority)
                  if not (x == CEDULA_PROFESIONAL and any("cedula" in _clave_texto(c) for c in capt))
                  and not (x != CEDULA_PROFESIONAL and any(es_de_experiencia(c) for c in capt)) and admisible(x)]
    if capt:
        candidatos = implicitos + [g for g in generados or [] if admisible(g)
                                   and not (implicitos and es_de_experiencia(g) and any(es_de_experiencia(i) for i in implicitos))]
    else:
        candidatos = implicitos + [c for c in condiciones_explicitas(descripcion) if admisible(c)]
        if not candidatos:
            candidatos = [indispensable_por_defecto(modalidad, ubicacion)]
    vistos = {_clave_texto(x) for x in capt}
    propuestos: List[str] = []
    for g in candidatos:
        if len(capt) + len(propuestos) >= MAX_INDISPENSABLES:
            break
        if _clave_texto(g) not in vistos:
            vistos.add(_clave_texto(g))
            propuestos.append(g.strip())
    return capt + propuestos, propuestos, preferentes


_RE_MD_TITULO = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_RE_MD_VINETA = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)


def sin_markdown(texto: str) -> str:
    """Cambio 43: el texto de WhatsApp va SIN Markdown — sin «#», «**», «__» ni «`»; las viñetas pasan a «•»."""
    t = _RE_MD_TITULO.sub("", str(texto or ""))
    t = _RE_MD_VINETA.sub("• ", t)
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)
    return t.replace("**", "").replace("`", "").strip()


def _asegurar_capturado(salida: VacanteGenerada, ficha: FichaVacante) -> VacanteGenerada:
    """Garantía en Python (no depende del modelo) de las dos reglas no negociables de la Parte 3:
    (a) no inventar condiciones — beneficios = capturados; (b) respetar lo capturado — indispensables y
    deseables literal, en su categoría y primero; seniority = el elegido. Cambio 37: reglas de los indispensables
    (`reglas_indispensables`)."""
    indisp, propuestos, extra_des = reglas_indispensables(
        ficha.requisitos_indispensables, salida.requisitos_indispensables, seniority=ficha.seniority or salida.seniority,
        descripcion=ficha.descripcion_breve, responsabilidades=list(salida.responsabilidades or []),
        deseables=ficha.requisitos_deseables, modalidad=ficha.modalidad, ubicacion=ficha.ubicacion, titulo=ficha.titulo,
        area=ficha.area)
    salida.requisitos_indispensables = indisp
    salida.indispensables_propuestos = propuestos
    salida.requisitos_deseables = _unir_capturado(
        ficha.requisitos_deseables, list(salida.requisitos_deseables or []) + extra_des, excluir=indisp
    )
    salida.beneficios = [b.strip() for b in ficha.beneficios if b.strip()]
    salida.texto_whatsapp = sin_markdown(salida.texto_whatsapp)  # Cambio 43: WhatsApp sin Markdown
    if ficha.seniority:
        salida.seniority = ficha.seniority  # type: ignore[assignment]
    avisos = list(salida.avisos_cumplimiento)
    if not ficha.beneficios:
        avisos.append("Prestaciones no capturadas: RH debe confirmarlas antes de publicar (no se inventaron).")
    if not ficha.sueldo_texto or ficha.sueldo_texto == "A convenir":
        avisos.append("Sueldo no capturado («A convenir»): RH debe confirmarlo antes de publicar (no se inventó).")
    if not ficha.ubicacion:
        avisos.append("Ubicación no capturada: RH debe confirmarla antes de publicar.")
    salida.avisos_cumplimiento = avisos_vigentes(avisos, sueldo=ficha.sueldo_texto, ubicacion=ficha.ubicacion, beneficios=ficha.beneficios)
    if ficha.pide_cv is False:
        _quitar_cv(salida)
    if not salida.preguntas_filtro_whatsapp:
        salida.preguntas_filtro_whatsapp = puntos_criticos_whatsapp(salida.preguntas_filtro)
    salida.preguntas_filtro = cerrar_preguntas_web(salida.preguntas_filtro)
    salida.preguntas_filtro_whatsapp = separar_preguntas_whatsapp(salida.preguntas_filtro_whatsapp)
    # Cambio 31: cada indispensable trae su pregunta natural (la de la IA si sirve; si no, la determinista)
    salida.preguntas_requisitos = [PreguntaRequisito(**x) for x in asegurar_preguntas_requisitos(
        salida.requisitos_indispensables, [q.model_dump() for q in salida.preguntas_requisitos])]
    for p in salida.preguntas_filtro_whatsapp:
        if p.tipo == "numero" and not p.opciones:
            p.opciones = _RANGO_ANOS_GENERICO
    return salida


# 2026-10-09: avisos de cumplimiento UNA sola vez y solo mientras sigan vigentes (un dato capturado borra su aviso).
_TEMAS_AVISO = {
    "sueldo": ("sueldo", "salario", "a convenir"),
    "ubicacion": ("ubicacion",),
    "prestaciones": ("prestacion", "beneficio"),
}
_PENDIENTE = ("no captur", "pendiente", "confirmar", "falta", "sin dato", "no se invent", "no especific")


def avisos_vigentes(avisos: Optional[List[str]], *, sueldo: str = "", ubicacion: str = "", beneficios: Optional[List[str]] = None) -> List[str]:
    """Sin duplicados (mismo texto o mismo dato pendiente) y sin los avisos de un dato que RH ya capturó."""
    capturado = {"sueldo": bool(sueldo) and sueldo != "A convenir", "ubicacion": bool((ubicacion or "").strip()),
                 "prestaciones": bool([b for b in (beneficios or []) if str(b).strip()])}
    vistos, temas, salida = set(), set(), []
    for a in avisos or []:
        t = " ".join(str(a or "").split())
        k = _clave_texto(t)
        if not k or k in vistos:
            continue
        tema = next((x for x, pal in _TEMAS_AVISO.items() if any(p in k for p in pal)), None) if any(p in k for p in _PENDIENTE) else None
        if tema and (capturado[tema] or tema in temas):
            continue
        if k.startswith("modo demo") and any(_clave_texto(x).startswith("modo demo") for x in salida):
            continue
        vistos.add(k)
        if tema:
            temas.add(tema)
        salida.append(t)
    return salida


# Publicación sin CV (2026-10-09): si la ruta no pide CV, ningún texto público dice «envía tu CV».
_FRASES_CV = [
    (re.compile(r"Env[ií]a tu (CV|curr[ií]culum)( vitae)? por este medio", re.IGNORECASE), "Postúlate desde la liga de la vacante"),
    (re.compile(r"solo necesitas tu (CV|curr[ií]culum) o responder unas preguntas r[aá]pidas", re.IGNORECASE), "solo responde unas preguntas rápidas"),
    (re.compile(r"(env[ií]a|manda|comparte|adjunta|sube)(nos)? tu (CV|curr[ií]culum)( vitae)?", re.IGNORECASE), "postúlate en la liga"),
    (re.compile(r"(con|y) tu (CV|curr[ií]culum)( vitae)?", re.IGNORECASE), ""),
]


def texto_sin_cv(texto: str) -> str:
    salida = texto or ""
    for patron, reemplazo in _FRASES_CV:
        salida = patron.sub(reemplazo, salida)
    return re.sub(r"[ \t]{2,}", " ", salida)


def _quitar_cv(salida: "VacanteGenerada") -> None:
    salida.texto_whatsapp = texto_sin_cv(salida.texto_whatsapp)
    for bloque in (salida.occ, salida.linkedin, salida.portal):
        bloque.texto_copy = texto_sin_cv(bloque.texto_copy)
        bloque.page = texto_sin_cv(bloque.page)


# 2026-09-20 (Bloque 1): reglas de prefiltro garantizadas en código, no solo en el prompt.
_VERBOS_ABIERTOS = re.compile(r"^\s*¿?\s*(describe|explica|cu[eé]ntanos|cu[eé]ntame|menciona|detalla|platícanos|platica|qu[eé]\s|c[oó]mo\s|por qu[eé]\s|cu[aá]l(es)?\s)", re.IGNORECASE)
# 2026-10-11 (tarde): el prefiltro web se responde SOLO con Sí / No («Parcial» ya no existe)
OPCIONES_CERRADAS = ["Sí", "No"]
MIN_PREFILTRO_WEB = 3
MENSAJE_MIN_PREFILTRO = ("Para el prefiltro necesitamos al menos 3 requisitos clave. Agrega uno: horario, experiencia o "
                         "condición del trabajo")


def _pregunta_cerrada_desde_requisito(valida: str, pregunta: str) -> str:
    base = (valida or "").strip().rstrip(".")
    if base:
        return f"¿Cumples con este requisito: {base}?"
    limpio = re.sub(r"^\s*¿?\s*(describe|explica|cu[eé]ntanos|cu[eé]ntame|menciona|detalla|platícanos|platica)\s*", "", pregunta, flags=re.IGNORECASE).strip(" ?¿.")
    return f"¿Cuentas con {limpio[0].lower() + limpio[1:] if limpio else 'este requisito'}?"


def cerrar_preguntas_web(preguntas: List[PreguntaFiltro]) -> List[PreguntaFiltro]:
    """Prefiltro WEB: todas cerradas (Sí / No). Una pregunta abierta o numérica se reescribe como
    umbral/cumplimiento del requisito que valida; el tipo queda 'si_no' con opciones fijas."""
    salida: List[PreguntaFiltro] = []
    for p in preguntas:
        q = (p.pregunta or "").strip()
        if p.tipo in ("texto_corto",) or _VERBOS_ABIERTOS.match(q) or not q.endswith("?"):
            q = _pregunta_cerrada_desde_requisito(p.valida, q)
        elif p.tipo == "numero":
            # «¿Cuántos años de experiencia tienes…?» → umbral cerrado con la respuesta esperada
            umbral = re.sub(r"[^0-9]", " ", p.respuesta_esperada or "").split()
            n = umbral[0] if umbral else ""
            tema = re.sub(r"^\s*¿?\s*cu[aá]ntos?\s+(años|anos|meses)\s+(de\s+)?", "", q, flags=re.IGNORECASE)
            tema = re.sub(r"\b(tienes|llevas|cuentas|posees)\b\s*", "", tema, flags=re.IGNORECASE)
            tema = re.sub(r"\s{2,}", " ", tema).strip(" ?¿")
            unidad = "año" if n == "1" else "años"
            q = f"¿Tienes al menos {n} {unidad} de {tema}?" if n and tema else _pregunta_cerrada_desde_requisito(p.valida, q)
        if not q.startswith("¿"):
            q = "¿" + q
        p.pregunta = q
        p.tipo = "si_no"
        p.opciones = list(OPCIONES_CERRADAS)
        if not p.respuesta_esperada or p.respuesta_esperada.lower() not in ("sí", "si", "no"):
            p.respuesta_esperada = "Sí"
        salida.append(p)
    return salida


def separar_preguntas_whatsapp(preguntas: List[PreguntaFiltro]) -> List[PreguntaFiltro]:
    """Prefiltro WHATSAPP: una pregunta por criterio. Una pregunta compuesta («¿…? ¿…?» o «¿… y …?» con dos
    interrogaciones) se divide en preguntas independientes que validan el mismo requisito."""
    salida: List[PreguntaFiltro] = []
    for p in preguntas:
        partes = [x.strip() for x in re.split(r"\?\s*(?=¿|[A-ZÁÉÍÓÚ])", (p.pregunta or "").strip()) if x.strip()]
        partes = [x if x.endswith("?") else x + "?" for x in partes]
        if len(partes) <= 1:
            salida.append(p)
            continue
        for i, parte in enumerate(partes):
            salida.append(PreguntaFiltro(
                pregunta=parte if parte.startswith("¿") else "¿" + parte, tipo=p.tipo if i == 0 else "texto_corto", valida=p.valida,
                respuesta_esperada=p.respuesta_esperada if i == 0 else "", descarta=p.descarta if i == 0 else False, opciones=p.opciones if i == 0 else [],
            ))
    return salida


# 2026-10-09 (guiones por vacante): las entrevistas y llamadas SOLO llevan preguntas abiertas. Una pregunta cerrada
# («¿Tienes…?», «¿Cuentas con…?», «¿Has…?») se reescribe como invitación a contar con evidencia.
_RE_CERRADA = re.compile(r"^\s*¿?\s*(tienes|cuentas|has|puedes|estás|estas|sabes|manejas|vives|eres|te\s+\w+|conoces|dominas|"
                         r"podrías|podrias|tendrías|tendrias|aceptas|cumples)\b\s*(con\s+|en\s+|de\s+)?", re.IGNORECASE)


def abrir_preguntas(preguntas: List[str]) -> List[str]:
    """Garantía en código: ninguna pregunta de entrevista/llamada se contesta con «sí» o «no»."""
    salida: List[str] = []
    for q in preguntas or []:
        t = " ".join(str(q or "").split())
        if not t:
            continue
        m = _RE_CERRADA.match(t)
        if m:
            tema = t[m.end():].strip(" ¿?.")
            if not tema:
                t = "Cuéntame un ejemplo concreto de tu experiencia."
            elif tema.lower().startswith(("experiencia", "conocimiento")):
                t = f"Cuéntame sobre tu {tema[0].lower() + tema[1:]}: un ejemplo concreto."
            else:
                t = f"Cuéntame tu experiencia con {tema}: un ejemplo concreto."
        salida.append(t)
    return salida


def puntos_criticos_whatsapp(web: List[PreguntaFiltro]) -> List[PreguntaFiltro]:
    """Prefiltro de WhatsApp derivado del web cuando la IA no lo entregó (o en modo demo): experiencia
    (tipo numero) + hasta 2 eliminatorias, en tono de chat. Máximo 3."""
    salida: List[PreguntaFiltro] = []
    exp = next((p for p in web if p.tipo == "numero"), None)
    if exp:
        salida.append(PreguntaFiltro(
            pregunta="Cuéntame, ¿cuánto tiempo llevas haciendo algo parecido a este puesto?",
            tipo="numero", valida=exp.valida, respuesta_esperada=exp.respuesta_esperada, descarta=exp.descarta, opciones=exp.opciones,
        ))
    for p in web:
        if len(salida) >= 3:
            break
        if p.descarta and p.tipo != "numero":
            salida.append(PreguntaFiltro(
                pregunta=p.pregunta if p.pregunta.endswith("?") else p.pregunta + "?",
                tipo=p.tipo, valida=p.valida, respuesta_esperada=p.respuesta_esperada, descarta=True, opciones=p.opciones,
            ))
    return salida


def _demo_vacante(f: FichaVacante) -> VacanteGenerada:
    """Plantilla determinista para modo demo (sin OPENAI_API_KEY) — misma estructura que la salida de IA
    y las mismas reglas: nada de sueldo, horario ni prestaciones que RH no haya capturado."""
    titulo, area, empresa = f.titulo, f.area, f.empresa or "la empresa"
    lugar = f.ubicacion or ""
    en_lugar = f" en {lugar}" if lugar else ""
    reqs = list(f.requisitos_indispensables) or ["Experiencia comprobable en un puesto similar", "Documentación en regla (INE, CURP, RFC)"]
    deseables = list(f.requisitos_deseables) or ["Experiencia previa en un puesto similar"]
    actividades = [
        f"Ejecutar las actividades diarias del puesto de {titulo}.",
        f"Coordinarte con el equipo de {area or 'la operación'} para cumplir los objetivos.",
        "Reportar avances e incidencias a tu jefe(a) directo(a).",
        "Cuidar el orden, la seguridad y la calidad en tu área de trabajo.",
        "Dar seguimiento a los indicadores del puesto.",
    ]
    con_sueldo = bool(f.sueldo_texto) and f.sueldo_texto != "A convenir"
    linea_sueldo = f" Sueldo {f.sueldo_texto}." if con_sueldo else ""
    ofrecemos = list(f.beneficios) + ([f"Sueldo {f.sueldo_texto}"] if con_sueldo else [])
    lista_act = "\n".join(f"• {a}" for a in actividades)
    viñetas = "\n".join(f"• {r}" for r in reqs)
    lista_ben = "\n".join(f"• {b}" for b in ofrecemos) or "• Condiciones a confirmar con RH"
    base_desc = (f.descripcion_breve.strip() + "\n\n") if f.descripcion_breve.strip() else ""
    modalidad = f" Modalidad {f.modalidad.lower()}." if f.modalidad else ""

    return VacanteGenerada(
        resumen=f"Buscamos {titulo} para {empresa}{en_lugar}.{linea_sueldo}",
        descripcion=(
            f"{base_desc}{empresa} busca {titulo} para su equipo de {area or 'operación'}{en_lugar}.{modalidad}\n\n"
            "Es una posición con actividades claras, acompañamiento desde el primer día y un equipo que te respalda."
        ),
        perfil_ideal=(
            f"Personas con perfil {f.seniority.lower() if f.seniority else 'operativo'}, responsables y con actitud de "
            "servicio. Se valora experiencia previa en actividades similares."
        ),
        responsabilidades=actividades,
        requisitos_indispensables=reqs,
        requisitos_deseables=deseables,
        beneficios=list(f.beneficios),
        palabras_clave=[x for x in [titulo.lower(), (area or "empleo").lower(), lugar.lower(), "vacante", "empleo"] if x],
        seniority=(f.seniority or "Junior"),  # type: ignore[arg-type]
        rango_salarial_sugerido=f.sueldo_texto or "Sin referencia (modo demo)",
        avisos_cumplimiento=["Modo demo: agrega OPENAI_API_KEY en la API para generar el contenido con IA."],
        texto_whatsapp=(
            f"📢 *{titulo}*{(' — ' + lugar) if lugar else ''}\n" + (f"💰 {f.sueldo_texto}\n" if con_sueldo else "")
            + "\nContéstame por aquí y en 2 minutos hacemos tu registro. ¡Va! 🙌"
        ),
        occ=BloquePlataforma(
            titulo=f"{titulo}{(' - ' + lugar) if lugar else ''}"[:70],
            copy=f"{empresa} solicita {titulo}{en_lugar}.{linea_sueldo} Postúlate hoy."[:300],
            page=(
                f"SOBRE LA VACANTE\n{empresa} busca {titulo} para su equipo{en_lugar}.\n\n"
                f"ACTIVIDADES PRINCIPALES\n{lista_act}\n\nREQUISITOS\n{viñetas}\n\nOFRECEMOS\n{lista_ben}\n\n"
                + ("CÓMO POSTULARTE\n• Envía tu CV por este medio y el equipo de RH te contactará." if f.pide_cv is not False
                 else "CÓMO POSTULARTE\n• Postúlate desde la liga de la vacante: solo responde unas preguntas rápidas.")
            ),
            etiquetas=[x for x in [titulo.lower(), area.lower() or "empleo", lugar.lower(), "vacante"] if x],
        ),
        linkedin=BloquePlataforma(
            titulo=f"{titulo} | {empresa}{(' | ' + lugar) if lugar else ''}"[:100],
            copy=(
                f"Estamos contratando: {titulo}{en_lugar}.\n\n"
                f"En {empresa} buscamos a alguien que quiera crecer con nosotros.{linea_sueldo}\n\n"
                "¿Te interesa o conoces a alguien? Postúlate desde la liga de esta publicación.\n\n"
                "#Empleo #Vacantes #México"
            ),
            page=(
                f"**Sobre el rol**\n{titulo}{en_lugar} para el equipo de {area or 'operación'} de {empresa}.\n\n"
                "**Lo que harás**\n" + "\n".join(f"- {a}" for a in actividades) + "\n\n"
                "**Lo que buscamos**\n" + "\n".join(f"- {r}" for r in reqs)
                + ("\n\n**Lo que ofrecemos**\n" + "\n".join(f"- {b}" for b in ofrecemos) if ofrecemos else "")
            ),
            etiquetas=[titulo, area or "Operaciones", "Trabajo en equipo"],
        ),
        portal=BloquePlataforma(
            titulo=titulo,
            copy=f"{titulo}{en_lugar}.{linea_sueldo} Postúlate en 2 minutos."[:160],
            page=(
                f"## {titulo}\n\n{'¿Buscas trabajo en ' + lugar + '? ' if lugar else ''}En {empresa} estamos contratando.\n\n"
                "**Lo que harás**\n" + "\n".join(f"- {a}" for a in actividades) + "\n\n"
                "**Lo que necesitas**\n" + "\n".join(f"- {r}" for r in reqs)
                + ("\n\n**Lo que te damos**\n" + "\n".join(f"- {b}" for b in ofrecemos) if ofrecemos else "") + "\n\n"
                + ("Postúlate en 2 minutos: solo necesitas tu CV o responder unas preguntas rápidas." if f.pide_cv is not False
                 else "Postúlate en 2 minutos: solo responde unas preguntas rápidas.")
            ),
            etiquetas=[x for x in [titulo.lower(), f"empleo {lugar.lower()}" if lugar else "", "vacante"] if x],
        ),
        preguntas_filtro=[
            PreguntaFiltro(
                pregunta=f"¿Cumples con: {r}?" if len(r) < 70 else f"¿Cumples con el requisito «{r[:60]}…»?",
                tipo="si_no", valida=r, respuesta_esperada="Sí", descarta=True,
            )
            for r in reqs[:4]
        ] + [
            PreguntaFiltro(
                pregunta="¿Cuántos años de experiencia tienes en un puesto similar?",
                tipo="numero", valida="Experiencia previa", respuesta_esperada=">= 1 año", descarta=False,
                opciones=_RANGO_ANOS_GENERICO,
            ),
        ],
    )


def generar_vacante(ficha: FichaVacante) -> Tuple[VacanteGenerada, bool]:
    """Parte 3: genera/completa la vacante a partir de la ficha capturada por RH. Las dos reglas no
    negociables (no inventar condiciones; respetar lo capturado) se piden en el prompt Y se garantizan
    en Python con _asegurar_capturado, en modo IA y en modo demo."""
    client = _client()
    if client is None:
        return _asegurar_capturado(_demo_vacante(ficha), ficha), False

    def lista(xs: List[str]) -> str:
        return ("\n" + "\n".join(f"  - {x}" for x in xs)) if xs else " (sin dato — no inventes)"

    resp = client.responses.parse(
        model=MODEL,
        instructions=f"{_REGLAS}\n\n{_PLANTILLAS}",
        input=(
            "Ficha capturada por RH (insumo interno, no la cites literalmente):\n"
            f"- Puesto: {ficha.titulo}\n- Área: {ficha.area or '(sin dato)'}\n- Seniority: {ficha.seniority or '(sin dato)'}\n"
            f"- Empresa: {ficha.empresa or '(sin dato)'}\n- Ubicación: {ficha.ubicacion or '(sin dato — no inventes)'}\n"
            f"- Modalidad: {ficha.modalidad or '(sin dato — no inventes)'}\n- Sueldo: {ficha.sueldo_texto or '(sin dato — no inventes cifras)'}\n"
            f"- Descripción breve (guía obligatoria a expandir): {ficha.descripcion_breve or '(sin dato)'}\n"
            f"- Requisitos indispensables capturados (conservar literal, como indispensables):{lista(ficha.requisitos_indispensables)}\n"
            f"- Requisitos deseables capturados (conservar literal, como deseables):{lista(ficha.requisitos_deseables)}\n"
            f"- Prestaciones capturadas (las únicas que puedes mencionar):{lista(ficha.beneficios)}\n"
            + ("- La ruta de esta vacante NO pide CV: nunca escribas «envía tu CV» ni nada parecido; invita a postularse en la "
               "liga respondiendo unas preguntas rápidas.\n" if ficha.pide_cv is False else "")
            + "\n"
            "Genera la publicación completa a partir de esta ficha, complementando SOLO lo que falte."
        ),
        text_format=VacanteGenerada,
    )
    return _asegurar_capturado(resp.output_parsed, ficha), True


_RE_PREGUNTA_ARMADA = re.compile(r"(cu[aá]nto tiempo de experiencia|para confirmar|primero conf[ií]rmame|cumples con|"
                                 r"cu[aá]l de estas opciones|por ejemplo|para esta vacante se pide|cu[aá]l es tu caso)", re.IGNORECASE)


# Cambio 48: relleno conversacional al inicio de una pregunta cerrada («Cuéntame, ¿…?») — se quita en código
_RE_RELLENO = re.compile(r"^\s*(?:cu[eé]ntame|cu[eé]ntanos|plat[ií]came|plat[ií]canos|dime|oye|a ver|bueno|entonces)\s*,?\s*(?=¿)",
                         re.IGNORECASE)


def sin_relleno(pregunta: str) -> str:
    """Cambio 48: «Cuéntame, ¿cuántos años llevas en almacén?» → «¿Cuántos años llevas en almacén?». Solo quita el relleno
    que va ANTES de los signos de interrogación (una pregunta abierta «Cuéntame un ejemplo…» se queda igual)."""
    q = " ".join(str(pregunta or "").split())
    m = _RE_RELLENO.match(q)
    if not m:
        return q
    q = q[m.end():]
    return q[:1] + q[1:2].upper() + q[2:] if q.startswith("¿") else q


def pregunta_natural_requisito(req: str) -> str:
    """Cambio 31 (sin IA): la pregunta natural de un requisito indispensable. Escolaridad → «¿Cuál es tu último grado
    de estudios?»; experiencia → «¿Cuántos años llevas en …?»; licencia → «¿Qué tipo de licencia tienes?»; lo demás,
    DIRECTA («¿Tienes disponibilidad para rolar turnos?», «¿Puedes cargar material pesado?»). Cambio 48: sin «Cuéntame,»."""
    r = " ".join(str(req or "").split()).strip(" .")
    if not r:
        return ""
    if RE_NIVEL_ESTUDIOS.search(r):
        return PREGUNTA_ESCOLARIDAD
    if _RE_ANOS.search(r) or "experiencia" in _clave_texto(r):
        tema = _tema_experiencia(r)
        primera = (tema.split() or [""])[0].lower()
        if tema == "un puesto similar":
            return "¿Cuántos años de experiencia tienes en un puesto similar?"
        return f"¿Cuántos años llevas {tema}?" if primera.endswith(("ando", "endo", "iendo")) else f"¿Cuántos años llevas en {tema}?"
    if re.search(r"\blicencia\b", _clave_texto(r)):
        return "¿Qué tipo de licencia tienes?"
    return pregunta_si_no_natural(r)


def asegurar_preguntas_requisitos(indispensables: List[str], propuestas: Optional[List[dict]] = None) -> List[dict]:
    """Una pregunta natural por indispensable: la propuesta por la IA si es una sola pregunta natural (sin frases armadas
    ni «Para confirmar»); si no, `pregunta_natural_requisito`. La escolaridad siempre con su pregunta fija."""
    por_req = {_norm_req(x.get("requisito") or ""): str(x.get("pregunta") or "").strip() for x in propuestas or [] if isinstance(x, dict)}
    salida = []
    for req in [r for r in indispensables or [] if str(r or "").strip()]:
        propia = sin_relleno(por_req.get(_norm_req(req), ""))  # Cambio 48: sin «Cuéntame,» aunque la IA lo ponga
        valida = (propia.count("?") == 1 and propia.endswith("?") and not _RE_PREGUNTA_ARMADA.search(propia)
                  and not RE_NIVEL_ESTUDIOS.search(req) and len(propia.split()) <= 20)
        salida.append({"requisito": req.strip(), "pregunta": propia if valida else pregunta_natural_requisito(req)})
    return salida


def texto_preguntas(preguntas: Optional[list]) -> List[str]:
    """Normaliza preguntas de filtro: acepta la forma vieja (list[str]) y la nueva (list[PreguntaFiltro])."""
    salida: List[str] = []
    for p in preguntas or []:
        if isinstance(p, str):
            salida.append(p)
        elif isinstance(p, dict) and p.get("pregunta"):
            salida.append(str(p["pregunta"]))
    return salida


def criterios_prefiltro(preguntas: Optional[list]) -> str:
    """Resumen legible de los criterios de descarte para el prompt del agente de prefiltro."""
    lineas = []
    for p in preguntas or []:
        if isinstance(p, dict) and p.get("pregunta"):
            marca = "DESCARTA si no cumple" if p.get("descarta") else "suma pero no descarta"
            lineas.append(f"- {p['pregunta']} → esperado: {p.get('respuesta_esperada', 'n/d')} ({marca})")
        elif isinstance(p, str):
            lineas.append(f"- {p}")
    return "\n".join(lineas) or "- (sin criterios definidos)"


# ============================================================
# 2) Extractor de información de CVs (módulo 3.7)
# ============================================================


class AjustePerfil(BaseModel):
    """Qué tanto empata el CV con la vacante — RECOMENDACIÓN, no decisión (LFPDPPP)."""

    score: int = Field(description="0-100, empate del CV con los requisitos de la vacante.")
    estado: Literal["cumple", "revision", "no_cumple"]
    requisitos_cumplidos: List[str] = Field(description="Requisitos que el CV sí acredita, citando dónde.")
    brechas: List[str] = Field(description="Requisitos indispensables que el CV NO acredita o deja en duda.")
    evidencia: str = Field(description="1-2 frases objetivas que sustentan el score, citando el CV.")
    # 2026-09-13 — bloque «Análisis de CV» que ve RH desde el inicio (independiente del prefiltro):
    fortalezas: List[str] = Field(default_factory=list, description="2 a 4 fortalezas del CV para ESTA vacante, con evidencia del CV.")
    compatibilidad: str = Field(default="", description="1-2 frases: qué tan compatible es el perfil con el puesto y por qué.")


class CVExtraido(BaseModel):
    nombre: Optional[str] = Field(default=None, description="Nombre completo del candidato.")
    correo: Optional[str] = None
    telefono: Optional[str] = Field(default=None, description="Teléfono a 10 dígitos si es posible.")
    ubicacion: Optional[str] = Field(default=None, description="Ciudad y estado.")
    anios_experiencia: Optional[float] = Field(default=None, description="Años de experiencia laboral total.")
    experiencia_resumen: str = Field(description="Resumen de la experiencia en 1-2 frases.")
    puesto_actual: Optional[str] = Field(default=None, description="Puesto más reciente que aparece en el CV.")
    ultimo_empleo: Optional[str] = Field(default=None, description="Empresa y periodo del empleo más reciente.")
    estudios: List[str] = Field(default_factory=list)
    habilidades: List[str] = Field(default_factory=list)
    idiomas: List[str] = Field(default_factory=list)
    # --- Ficha de candidato, punto 3.B: perfil profesional listo para decidir sin leer el CV completo ---
    resumen_profesional: str = Field(
        description="Resumen profesional de 3 A 5 líneas (más completo que experiencia_resumen): "
        "quién es, su trayectoria y su fortaleza principal. Español mexicano, tono neutral."
    )
    experiencia_relevante: Optional[str] = Field(
        default=None,
        description="1-2 frases de la experiencia del CV que sea específicamente relevante para la vacante de "
        "referencia (no un resumen genérico). Null si no se dio una vacante de referencia.",
    )
    conocimientos_relevantes: List[str] = Field(
        default_factory=list,
        description="Subconjunto de las habilidades/conocimientos del CV que aplican directamente a los "
        "requisitos de la vacante de referencia (no la lista completa de habilidades). Vacío si no se dio "
        "una vacante de referencia.",
    )
    datos_faltantes: List[str] = Field(default_factory=list, description="Datos que no aparecen o no son legibles en el CV.")
    alertas: List[str] = Field(
        default_factory=list,
        description="Focos de atención objetivos: CV ilegible, huecos largos sin explicar, incongruencias de fechas. Nunca datos sensibles.",
    )
    es_cv: bool = Field(description="false si el archivo no es un currículum (otro documento, foto no relacionada, etc.).")
    ajuste: Optional[AjustePerfil] = Field(
        default=None, description="Solo cuando se proporcionó una vacante de referencia; si no, null."
    )


_MEDIA = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


def _bloque_archivo(archivo_b64: str, extension: str, nombre: str) -> dict:
    """Convierte un archivo base64 en un bloque de entrada de la Responses API."""
    ext = extension.lower().lstrip(".")
    media = _MEDIA.get(ext, "application/pdf")
    if media == "application/pdf":
        return {"type": "input_file", "filename": f"{nombre}.pdf", "file_data": f"data:application/pdf;base64,{archivo_b64}"}
    return {"type": "input_image", "image_url": f"data:{media};base64,{archivo_b64}"}


def extraer_cv(
    archivo_b64: str,
    extension: str,
    vacante_titulo: str = "",
    vacante_requisitos: str = "",
) -> Tuple[CVExtraido, bool]:
    """Extrae los datos del CV y, si se da una vacante, califica el ajuste en la misma llamada."""
    client = _client()
    if client is None:
        demo = CVExtraido(
            nombre=None,
            experiencia_resumen="Modo demo: agrega OPENAI_API_KEY para extraer los datos reales del CV.",
            resumen_profesional="Modo demo: agrega OPENAI_API_KEY para generar el resumen profesional real del CV.",
            datos_faltantes=["extracción real pendiente de API key"],
            es_cv=True,
            ajuste=AjustePerfil(
                score=60,
                estado="revision",
                requisitos_cumplidos=[],
                brechas=["Sin evaluación real (modo demo)"],
                evidencia="Modo demo: el expediente queda marcado para revisión humana.",
                fortalezas=["Modo demo: fortalezas del CV pendientes de OPENAI_API_KEY"],
                compatibilidad="Modo demo: compatibilidad no evaluada.",
            )
            if vacante_titulo
            else None,
        )
        return demo, False

    contexto = (
        f"\n\nVACANTE DE REFERENCIA\n- Puesto: {vacante_titulo}\n- Requisitos indispensables: {vacante_requisitos or 'no especificados'}\n"
        "Llena `ajuste` comparando el CV contra estos requisitos. Sé conservador: si un requisito no se puede "
        "acreditar con el CV, va en brechas y el estado no puede ser 'cumple'. En `ajuste.fortalezas` pon 2-4 "
        "fortalezas del CV para esta vacante y en `ajuste.compatibilidad` 1-2 frases de compatibilidad. Llena también "
        "`experiencia_relevante` (qué de su trayectoria aplica a ESTA vacante) y `conocimientos_relevantes` "
        "(el subconjunto de sus habilidades que aplica a ESTA vacante, no la lista completa)."
        if vacante_titulo
        else "\n\nNo hay vacante de referencia: deja `ajuste`, `experiencia_relevante` y "
        "`conocimientos_relevantes` en null/vacío."
    )

    ext = extension.lower().lstrip(".")
    if ext == "pdf":
        try:
            import base64
            import io
            from pypdf import PdfReader
            pdf_bytes = base64.b64decode(archivo_b64)
            reader = PdfReader(io.BytesIO(pdf_bytes))
            texto_pdf = ""
            for page in reader.pages:
                texto_pdf += (page.extract_text() or "") + "\n"
            
            if texto_pdf.strip():
                content_blocks = [
                    {"type": "input_text", "text": f"CONTENIDO DEL CV (TEXTO EXTRAÍDO DEL PDF):\n\n{texto_pdf}\n\nExtrae los datos de este currículum y evalúa el ajuste con la vacante."}
                ]
            else:
                content_blocks = [
                    {"type": "input_text", "text": "El archivo PDF no contiene texto legible (podría ser una imagen escaneada sin OCR). Por favor, indica esto en las observaciones/alertas."}
                ]
        except Exception as e:
            content_blocks = [
                {"type": "input_text", "text": f"Error al extraer texto del PDF usando pypdf: {str(e)}"}
            ]
    else:
        content_blocks = [
            _bloque_archivo(archivo_b64, extension, "cv"),
            {"type": "input_text", "text": "Extrae los datos de este currículum y evalúa el ajuste con la vacante."},
        ]

    resp = client.responses.parse(
        model=MODEL,
        instructions=(
            "Eres el extractor de CVs de Red Human AI (México). Extrae SOLO lo que realmente aparece en el "
            "documento; si un dato no existe o no es legible, déjalo nulo y regístralo en datos_faltantes. "
            "Nunca inventes información ni infieras datos sensibles (edad, sexo, estado civil, embarazo, "
            "religión, salud, origen). Tu salida es insumo para una persona de RH que toma la decisión final. "
            "Llena SIEMPRE `resumen_profesional` (3-5 líneas, más completo que experiencia_resumen): quién es "
            "el candidato, su trayectoria y su fortaleza principal, en español mexicano."
            + contexto
        ),
        input=[
            {
                "role": "user",
                "content": content_blocks,
            }
        ],
        text_format=CVExtraido,
    )
    return resp.output_parsed, True


# ============================================================
# 3) Agente de prefiltro conversacional (módulo 3.9)
# ============================================================


class RespuestaCriterio(BaseModel):
    criterio: str = Field(description="Nombre del criterio o pregunta evaluada")
    pregunta: str = Field(description="Pregunta formulada al candidato")
    respuesta: str = Field(description="Resumen o cita de la respuesta del candidato")
    cumple: Optional[bool] = Field(default=None, description="true si cumple, false si descarta, null si aún no concluyente")


class TurnoPrefiltro(BaseModel):
    respuesta: str = Field(description="Siguiente mensaje del agente al candidato, breve y cálido, español mexicano.")
    clasificacion_lista: bool = Field(description="true solo cuando ya hay información suficiente para clasificar.")
    # 2026-09-13: el prefiltro es SOLO un filtro básico de entrada — su único resultado es cumple o
    # no_cumple. No genera score ni participa en la evaluación integral (CV + Entrevista Red Human).
    estado: Optional[Literal["cumple", "no_cumple"]] = Field(default=None)
    evidencia: Optional[str] = Field(default=None, description="Evidencia objetiva que sustenta la clasificación.")
    respuestas_extraidas: List[RespuestaCriterio] = Field(
        default_factory=list,
        description="Lista acumulada de preguntas realizadas y respuestas estructuradas obtenidas del candidato hasta el momento."
    )
    # --- Zero-Touch fase 1: solo se llenan cuando este turno invocó agendar_videollamada ---
    cita_fecha_hora: Optional[str] = Field(
        default=None, description="Fecha/hora ISO 8601 de la videollamada agendada en este turno, si aplica."
    )
    cita_liga: Optional[str] = Field(
        default=None, description="Liga de la videollamada agendada en este turno, si aplica."
    )


def prefiltro_turno(
    vacante_titulo: str,
    requisitos: str,
    preguntas: list,
    historial: List[dict],
    *,
    empresa: str = "",
    ubicacion: str = "",
    sueldo: str = "",
    modalidad: str = "",
    beneficios: Optional[List[str]] = None,
    perfil_ideal: str = "",
    nombre_candidato: str = "",
    nota: str = "",
) -> Tuple[TurnoPrefiltro, bool]:
    """historial: [{"rol": "user"|"assistant", "texto": str}, ...] — el último es del candidato.
    `nota` (2026-09-16): contexto extra, p. ej. una inconsistencia Web vs WhatsApp que hay que aclarar."""
    client = _client()
    if client is None:
        n_agente = sum(1 for m in historial if m["rol"] == "assistant")
        qs = texto_preguntas(preguntas) or ["¿Cuentas con disponibilidad de horario?", "¿Tienes experiencia en un puesto similar?"]
        if n_agente < len(qs):
            return TurnoPrefiltro(respuesta=qs[n_agente], clasificacion_lista=False), False
        return (
            TurnoPrefiltro(
                respuesta=(
                    "¡Gracias por tus respuestas! Tu información quedó registrada y el equipo de RH la revisará. "
                    "Te contactamos muy pronto por este medio. 😊"
                ),
                clasificacion_lista=True,
                estado="cumple",
                evidencia="Modo demo: clasificación simulada. Agrega OPENAI_API_KEY para el prefiltro real.",
                respuestas_extraidas=[
                    RespuestaCriterio(criterio=q, pregunta=q, respuesta="Respuesta registrada en modo demo", cumple=True)
                    for q in qs
                ],
            ),
            False,
        )

    # --- Contexto enriquecido de la vacante ---
    lineas_contexto = [f"Vacante: {vacante_titulo}."]
    if empresa:
        lineas_contexto.append(f"Empresa: {empresa}.")
    if ubicacion:
        lineas_contexto.append(f"Ubicación: {ubicacion}.")
    if sueldo:
        lineas_contexto.append(f"Sueldo: {sueldo}.")
    if modalidad:
        lineas_contexto.append(f"Modalidad: {modalidad}.")
    if requisitos:
        lineas_contexto.append(f"Requisitos indispensables: {requisitos}.")
    if perfil_ideal:
        lineas_contexto.append(f"Perfil ideal: {perfil_ideal}.")
    if beneficios:
        lineas_contexto.append(f"Beneficios: {', '.join(beneficios)}.")

    saludo = f" Te diriges al candidato como «{nombre_candidato}»." if nombre_candidato else ""

    mensajes = [{"role": ("user" if m["rol"] == "user" else "assistant"), "content": m["texto"]} for m in historial]
    resp = client.responses.parse(
        model=MODEL,
        instructions=(
            "Eres el agente de prefiltro de Red Human AI, hablas por WhatsApp con candidatos en México.\n"
            + "\n".join(lineas_contexto) + "\n"
            f"Criterios de prefiltro:\n{criterios_prefiltro(preguntas)}\n\n"
            "Reglas: (1) una sola pregunta por mensaje y UN solo criterio por pregunta (nunca compuestas: «¿cuántos años tienes y has usado SAP?» son dos mensajes), tono cálido y breve — hablas como un reclutador "
            "humano, NO como un cuestionario robótico;" + saludo + " (1b) SOLO preguntas CERRADAS (sí/no o un dato "
            "concreto) sobre los criterios indispensables (DESCARTA); NUNCA preguntes autopercepciones («¿te consideras "
            "organizado?», «¿eres responsable?»); si ya respondió en el formulario web, pide el DATO EXACTO (cuántos años, en "
            "qué empresa); si la respuesta es ambigua (p. ej. «8» sin unidad) pide aclaración («¿8 años o 8 meses?»); si "
            "dice que no entiende, reformula la MISMA pregunta con otras palabras más sencillas — NUNCA repitas el mismo texto; "
            "(1c) PROHIBIDO ofrecer opciones múltiples o numeradas: el candidato contesta con sus palabras; interpreta lenguaje "
            "natural libre («ya la acabé», «claro» = sí); IGNORA el tono (si hay groserías pero responde, la respuesta vale); "
            "una contradicción entre el formulario web y el chat NUNCA descarta: anótala para «Puntos por validar»; "
            "(2) recorre los criterios en orden "
            "y no repitas los que ya quedaron contestados — no es necesario agotarlos todos: en cuanto "
            "puedas clasificar con confianza, cierra antes (ver regla 5); (3) si el candidato pregunta "
            "sobre sueldo, ubicación, beneficios o el puesto, contesta con los datos de la vacante que "
            "tienes arriba; (4) en `respuestas_extraidas` mantén una lista estructurada y acumulada de "
            "los criterios evaluados, la pregunta, la respuesta del candidato y si cumple (true/false/"
            "null); (5) cuando tengas suficiente información marca clasificacion_lista=true con estado y "
            "evidencia OBJETIVA citando lo que dijo la persona — el prefiltro es SOLO un filtro básico de "
            "entrada: su único resultado es 'cumple' o 'no_cumple', sin calificaciones ni puntajes; "
            "(6) si falla un criterio marcado como DESCARTA, el estado es 'no_cumple'; con dudas menores, "
            "'cumple' (RH lo valida después con el CV y la Entrevista Red Human); (7) NUNCA le "
            "comuniques un rechazo al candidato: si no cumple, agradece y di que RH revisará su caso — "
            "la decisión final siempre la toma una persona de RH; (8) no pidas datos sensibles (salud, "
            "embarazo, religión, estado civil, edad); (9) si el candidato dice que ya no le interesa, "
            "agradece y clasifica como 'no_cumple' con evidencia 'candidato declinó participar'; "
            "(10) en tu PRIMER mensaje de la conversación (revisa el historial: si no hay turnos tuyos "
            "previos, es el primero), preséntate como «Red Human» — nunca como «asistente virtual» ni "
            "«asistente de reclutamiento» — y menciona el título de la vacante a la que se postula."
            + (f"\nContexto adicional: {nota}" if nota else "")
        ),
        input=mensajes,
        text_format=TurnoPrefiltro,
    )
    return resp.output_parsed, True


# ============================================================
# 3a) Zero-Touch fase 1 — herramienta agendar_videollamada (function calling)
# ============================================================
#
# Una vez que el candidato queda clasificado como apto (score >= UMBRAL_ZERO_TOUCH, ver
# candidatos._auto_decision_zero_touch), el agente sigue la conversación con el único
# objetivo de coordinar una videollamada. Cuando el candidato confirma fecha/hora, el
# modelo invoca esta herramienta en vez de inventarse la confirmación.

HERRAMIENTA_AGENDAR_VIDEOLLAMADA = {
    "type": "function",
    "name": "agendar_videollamada",
    "description": (
        "Agenda la videollamada de entrevista con el candidato. Úsala SOLO cuando el candidato "
        "ya te dio una fecha/hora concreta de disponibilidad — nunca la inventes ni confirmes "
        "una cita sin haberla invocado primero."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "fecha_hora": {
                "type": "string",
                "description": (
                    "Fecha y hora acordada con el candidato, normalizada por ti a ISO 8601 con "
                    "zona horaria de México (ej. '2026-09-02T11:00:00-06:00')."
                ),
            },
        },
        "required": ["fecha_hora"],
        "additionalProperties": False,
    },
}

def agendar_videollamada_mock(
    nombre_candidato: str,
    fecha_hora: str,
    *,
    db: "Session",
    candidato: "Postulacion",
) -> dict:
    """Ejecuta la herramienta 'agendar_videollamada' (Zero-Touch, vía `agenda_turno`): crea una
    `Entrevista` real con avatar de Anam — reusa `services.entrevistas.crear_entrevista_para_candidato`,
    la misma función que usa RH al agendar a mano — y regresa su liga pública `/entrevista/{token}`.

    Ya no es un mock ni tiene modo sin `db`/`candidato`: el único llamador que queda
    (`agenda_turno`) siempre los manda. El botón manual «Generar liga de Google Meet» que antes
    compartía esta función se eliminó — el flujo de entrevista con candidatos ya es 100%
    automatizado por el avatar.

    Firma base — (nombre, fecha_hora) -> {"liga": str, "fecha_hora": str} — sin romper: `db` y
    `candidato` son keyword-only, es lo único que ve el function-calling del modelo (el dict de
    salida, vía `agenda_turno`); el modelo nunca ve estos dos parámetros.
    """
    from .entrevistas import crear_entrevista_para_candidato  # import local: evita el ciclo ia <-> entrevistas

    e, _con_ia = crear_entrevista_para_candidato(db, candidato, "agente-ia")
    liga = f"{settings.app_url}/entrevista/{e.token}"
    print(f"[agendar_videollamada] Entrevista con avatar {e.codigo} creada para {nombre_candidato} el {fecha_hora}")
    return {"liga": liga, "fecha_hora": fecha_hora}


# Nombres de día en español — evita depender del locale del servidor (strftime %A regresa
# nombres en inglés salvo que el sistema tenga es_MX instalado, algo que no podemos garantizar).
_DIAS_SEMANA_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def agenda_turno(
    nombre_candidato: str,
    vacante_titulo: str,
    historial: List[dict],
    *,
    db: "Session",
    candidato: "Postulacion",
    nota: str = "",
) -> Tuple[TurnoPrefiltro, bool]:
    """Turno posterior a la clasificación para un candidato ya apto: pregunta disponibilidad y,
    en cuanto el candidato confirma fecha/hora, invoca agendar_videollamada (function calling),
    que crea la entrevista real con avatar (ver `agendar_videollamada_mock` y
    `_procesar_turno_agenda` en candidatos.py, su único llamador).

    historial: [{"rol": "user"|"assistant", "texto": str}, ...] — el último es del candidato.
    """
    client = _client()
    if client is None:
        return (
            TurnoPrefiltro(
                respuesta="¡Perfecto! En cuanto tengamos lista la agenda automática te comparto la liga (modo demo).",
                clasificacion_lista=False,
            ),
            False,
        )

    # Ancla temporal explícita: sin esto el modelo no tiene forma de saber qué día es "hoy" y
    # no puede resolver fechas relativas ("mañana", "el próximo lunes", "8am") de forma confiable.
    ahora = datetime.now(TZ_ORG)
    referencia_fecha = (
        f"Hoy es {_DIAS_SEMANA_ES[ahora.weekday()]} {ahora.isoformat(timespec='minutes')} "
        "(hora de Ciudad de México, America/Mexico_City). Úsalo como referencia para interpretar "
        "cualquier fecha/hora relativa que diga el candidato al normalizarla a ISO 8601."
    )

    instrucciones = (
        "Eres el agente de Red Human AI (México). Ya clasificaste a este candidato como apto para "
        f"{vacante_titulo or 'la vacante'}; tu único objetivo ahora es coordinar una videollamada.\n"
        f"{referencia_fecha}\n"
        f"Te diriges a {nombre_candidato}. Reglas: (1) si todavía no sabes su disponibilidad, "
        "pregúntasela en un mensaje breve y cálido; (2) en cuanto el candidato te dé una fecha/hora "
        "concreta, DEBES invocar la herramienta agendar_videollamada con esa fecha/hora en ISO 8601 "
        "— nunca confirmes una cita sin haberla invocado; (3) después de invocarla, confirma la fecha "
        "con calidez, pero NO escribas tú la liga ni intentes transcribirla — el sistema la agrega "
        "textualmente al final del mensaje; (4) tono cálido, una sola idea por mensaje."
        + (f"\nContexto: {nota}" if nota else "")
    )
    mensajes = [{"role": ("user" if m["rol"] == "user" else "assistant"), "content": m["texto"]} for m in historial]

    resp = client.responses.parse(
        model=MODEL,
        instructions=instrucciones,
        input=mensajes,
        text_format=TurnoPrefiltro,
        tools=[HERRAMIENTA_AGENDAR_VIDEOLLAMADA],
    )

    llamada = next(
        (it for it in resp.output if getattr(it, "type", None) == "function_call" and it.name == "agendar_videollamada"),
        None,
    )
    if llamada is None:
        return resp.output_parsed, True

    args = json.loads(llamada.arguments or "{}")
    fecha_hora = args.get("fecha_hora", "")
    resultado_tool = agendar_videollamada_mock(nombre_candidato, fecha_hora, db=db, candidato=candidato)

    # Se reenvía resp.output completo (no solo el function_call): en modelos con razonamiento
    # la Responses API exige también el ítem de 'reasoning' que precedió a la llamada, o rechaza
    # la petición con 400 ("function_call was provided without its required reasoning item").
    resp2 = client.responses.parse(
        model=MODEL,
        instructions=instrucciones,
        input=mensajes + resp.output + [
            {
                "type": "function_call_output",
                "call_id": llamada.call_id,
                "output": json.dumps(resultado_tool, ensure_ascii=False),
            },
        ],
        text_format=TurnoPrefiltro,
    )
    turno = resp2.output_parsed
    # se fuerzan con el valor real de la herramienta, para no depender de que el modelo los copie bien
    turno.cita_fecha_hora = resultado_tool["fecha_hora"]
    turno.cita_liga = resultado_tool["liga"]
    return turno, True


# ============================================================
# Zero-Touch fase 2 — asistente conversacional de Onboarding
# ============================================================
#
# Una vez que el candidato llega a la etapa "Onboarding" (ver models.ETAPAS_CANDIDATO), el
# objetivo del agente cambia por completo: ya no evalúa ni agenda nada, solo acompaña la
# recolección de documentos que RH detonó con /candidatos/{codigo}/solicitar-documentos o
# /recordatorio-documentos (ver candidatos.py). El checklist real y la validación de cada
# documento siguen viviendo en el módulo 2 (contratacion.py + ia.validar_documento) — este
# turno es solo la conversación de acompañamiento por WhatsApp.


class TurnoOnboarding(BaseModel):
    respuesta: str = Field(description="Siguiente mensaje del agente al candidato, breve y cálido, español mexicano.")


def onboarding_turno(nombre_candidato: str, vacante_titulo: str, historial: List[dict]) -> Tuple[TurnoOnboarding, bool]:
    """historial: [{"rol": "user"|"assistant", "texto": str}, ...] — el último es del candidato."""
    client = _client()
    if client is None:
        return (
            TurnoOnboarding(
                respuesta="¡Gracias! En cuanto activemos la confirmación automática te aviso que lo recibí (modo demo)."
            ),
            False,
        )

    mensajes = [{"role": ("user" if m["rol"] == "user" else "assistant"), "content": m["texto"]} for m in historial]
    resp = client.responses.parse(
        model=MODEL,
        instructions=(
            "Eres el agente de Red Human AI (México). Este candidato YA fue contratado y está en la "
            f"etapa de Onboarding para {vacante_titulo or 'su nuevo puesto'}; te diriges a él/ella como "
            f"{nombre_candidato}. Tu objetivo cambió por completo respecto al resto de la conversación: "
            "ya NO evalúas su perfil ni agendas videollamadas — ahora solo lo acompañas a juntar los "
            "documentos que RH ya le pidió en un mensaje anterior de esta misma conversación.\n"
            "Reglas: (1) si pregunta qué documentos faltan o cómo mandarlos, contesta con base en lo "
            "que ya se le pidió arriba en la conversación, no inventes documentos nuevos; (2) si dice "
            "que ya envió, adjuntó o mandó una foto/PDF de un documento, agradécele cálidamente y "
            "confírmale que RH lo va a revisar — nunca digas que ya quedó validado o aceptado, eso lo "
            "confirma una persona de RH desde el expediente; (3) si pregunta algo fuera de documentos "
            "(fecha exacta de ingreso, sueldo, horario), sé honesto: dile que RH se lo confirma "
            "directamente, no lo inventes; (4) tono cálido, breve, una sola idea por mensaje; (5) nunca "
            "vuelvas a hacer preguntas de prefiltro o de disponibilidad para entrevista — esa etapa ya "
            "quedó atrás; (6) Cambio 54: los documentos se suben SIEMPRE en la LIGA de su expediente — nunca le "
            "pidas que los mande «por este medio» ni por el chat; (7) nunca prometas nada fuera de la vacante: "
            "«Eso lo puedes platicar con el equipo de RH»."
        ),
        input=mensajes,
        text_format=TurnoOnboarding,
    )
    return resp.output_parsed, True


# ============================================================
# Entrevista IA (módulo 3.10) — Fase 4: guion por temas, entrevistadora con protocolo y evaluación
# con conocimiento profundo del candidato.
# ============================================================

# Datos que la entrevistadora y la evaluadora tienen PROHIBIDO pedir, inferir o registrar (LFT art. 3
# y 133, LFPDPPP). Si el candidato los menciona por su cuenta, se ignoran: no se repreguntan, no
# se anotan como evidencia ni pesan en ninguna conclusión. Regla no negociable del documento.
DATOS_SENSIBLES_PROHIBIDOS = (
    "estado civil, hijos o planes de tener hijos, con quién vive, religión, salud o embarazo, "
    "discapacidad, orientación sexual, edad exacta, origen étnico, opinión política, situación "
    "económica o deudas personales"
)

# Qué cubre cada enfoque de entrevista (Punto 6). 2026-10-10: + «operativo» (guion ARMADO, ver entrevista_operativa).
ENFOQUE_ENTREVISTA_TEMAS = {
    "operativo": (
        "trayectoria laboral, experiencia REAL en el oficio (datos concretos y un procedimiento), indispensables del puesto, "
        "situación laboral actual, motivación y logística (traslado, horario, inicio)"
    ),
    "profesional": (
        "experiencia real, conocimientos del puesto, nivel de responsabilidad, criterio, resolución "
        "de problemas, toma de decisiones, comunicación, manejo de presión y errores, manejo de "
        "conflicto, motivadores laborales, estilo de trabajo, relación con jefaturas, objetivos y "
        "expectativas de crecimiento profesional"
    ),
    "profesional_personal": (
        "todo lo del enfoque profesional Y ADEMÁS objetivos personales no sensibles, prioridades de "
        "vida en relación con el trabajo, motivadores más amplios, disciplina y hábitos, valores, "
        "visión de futuro"
    ),
}


def _enfoque_valido(enfoque: str) -> str:
    return enfoque if enfoque in ENFOQUE_ENTREVISTA_TEMAS else "profesional"


class GuionEntrevista(BaseModel):
    enfoque: str = Field(description="En 1-2 frases, qué debe validar esta entrevista para este puesto y este candidato.")
    temas: List[str] = Field(
        default_factory=list,
        description="5 a 7 temas a cubrir, en orden sugerido de lo general a lo específico. Son áreas, no preguntas.",
    )
    preguntas: List[str] = Field(
        description=(
            "5 a 7 preguntas abiertas de REFERENCIA (una por tema), en español mexicano, cortas. La "
            "entrevistadora las usa como inspiración de tono, nunca como script literal."
        )
    )


def _guion_demo(titulo: str, enfoque_entrevista: str = "profesional") -> GuionEntrevista:
    temas = [
        "Experiencia reciente relacionada con el puesto",
        "Responsabilidades y nivel de autonomía",
        "Resolución de un problema real",
        "Manejo de presión y errores",
        "Motivación para este puesto",
        "Disponibilidad y condiciones",
    ]
    preguntas = [
        "Cuéntame de tu experiencia más reciente relacionada con este puesto.",
        "¿Qué decisiones tomabas tú y cuáles pasaban por tu jefe?",
        "Cuéntame de un problema difícil que resolviste en el trabajo.",
        "¿Cómo reaccionas cuando algo sale mal por un error tuyo?",
        "¿Qué te motivó a postularte a esta vacante?",
        "¿Cómo es tu disponibilidad de horario y traslado?",
    ]
    if enfoque_entrevista == "profesional_personal":
        temas.append("Objetivos personales y visión de futuro")
        preguntas.append("¿Cómo te ves en dos o tres años?")
    return GuionEntrevista(
        enfoque=f"Validar experiencia real, criterio y motivación para {titulo}.",
        temas=temas,
        preguntas=preguntas,
    )


def guion_entrevista(
    titulo: str,
    requisitos: str,
    candidato_resumen: str,
    enfoque_entrevista: str = "profesional",
    perfil_ideal: str = "",
    responsabilidades: Optional[List[str]] = None,
) -> Tuple[GuionEntrevista, bool]:
    """Guion por TEMAS (Fase 4): la entrevistadora cubre áreas y profundiza según la respuesta; las
    preguntas son solo referencia. `enfoque_entrevista` (Punto 6) decide qué áreas entran."""
    enfoque_entrevista = _enfoque_valido(enfoque_entrevista)
    client = _client()
    if client is None:
        return _guion_demo(titulo, enfoque_entrevista), False

    resp = client.responses.parse(
        model=MODEL,
        instructions=(
            "Diseñas guiones de entrevista para Red Human AI (RH en México). Devuelve TEMAS a cubrir (áreas, "
            "de lo general a lo específico) y una pregunta corta de referencia por tema, abierta y conductual, "
            "ligada a los requisitos del puesto; adapta 1-2 temas al perfil del candidato. "
            f"Áreas permitidas para este enfoque ({enfoque_entrevista}): {ENFOQUE_ENTREVISTA_TEMAS[enfoque_entrevista]}. "
            f"PROHIBIDO cualquier tema sobre: {DATOS_SENSIBLES_PROHIBIDOS}."
        ),
        input=(
            f"Puesto: {titulo}\nRequisitos indispensables: {requisitos}\n"
            f"Perfil ideal: {perfil_ideal or 'no especificado'}\n"
            f"Responsabilidades: {'; '.join(responsabilidades or []) or 'no especificadas'}\n"
            f"Resumen del candidato: {candidato_resumen or 'sin información previa'}"
        ),
        text_format=GuionEntrevista,
    )
    return resp.output_parsed, True


# Proceso configurable (2026-10-06): cada TIPO de entrevista humana genera su propio guion para quien entrevista
# (models.TIPOS_ENTREVISTA_HUMANA). Es apoyo, no script: la persona entrevistadora decide y registra el resultado.
ENFOQUE_ENTREVISTA_HUMANA = {
    "general": "trayectoria, motivación, expectativas del puesto, disponibilidad y condiciones, comunicación y ajuste general",
    "tecnica": "dominio técnico real del puesto: herramientas, procedimientos, casos prácticos y resolución de problemas del área",
    "jefe_directo": "cómo trabajaría en el equipo: prioridades, autonomía, seguimiento, manejo de errores, metas y estilo de trabajo con su jefe",
    "valores": "valores y conducta en el trabajo: ética, colaboración, servicio, responsabilidad, manejo de conflicto y adaptación a la cultura",
}
_PREGUNTAS_DEMO_HUMANA = {
    "general": ["Cuéntame de tu trayectoria y qué te trae a esta vacante.", "¿Qué esperas encontrar en este puesto?",
                "¿Cómo es tu disponibilidad de horario y traslado?", "¿Qué logro laboral te enorgullece más?",
                "¿Por qué dejarías tu empleo actual o anterior?"],
    "tecnica": ["Explícame paso a paso cómo resuelves la tarea principal del puesto.", "¿Qué herramientas dominas y en qué nivel?",
                "Cuéntame de un problema técnico difícil y cómo lo resolviste.", "¿Cómo verificas que tu trabajo quedó bien?",
                "Te planteo un caso práctico del área: ¿qué harías primero?"],
    "jefe_directo": ["¿Cómo organizas tus prioridades en una semana complicada?", "¿Qué tipo de seguimiento esperas de tu jefe?",
                     "Cuéntame de un error tuyo y qué hiciste después.", "¿Cómo te gusta recibir retroalimentación?",
                     "¿Qué metas te pondrías para tus primeros tres meses?"],
    "valores": ["Cuéntame de una vez que tuviste que decir que no a algo que no era correcto.",
                "¿Cómo resuelves un desacuerdo con un compañero?", "¿Qué significa para ti dar un buen servicio?",
                "Cuéntame de un cambio en el trabajo que te costó y cómo te adaptaste.", "¿Qué ambiente de trabajo te hace rendir mejor?"],
}


def guion_entrevista_humana(tipo: str, titulo: str, requisitos: str = "", candidato_resumen: str = "") -> Tuple[GuionEntrevista, bool]:
    """Guion de la entrevista HUMANA según su tipo (general | tecnica | jefe_directo | valores). Sin clave de OpenAI
    (o si falla) regresa uno de ejemplo del mismo tipo — nunca bloquea la creación de la evaluación."""
    tipo = tipo if tipo in ENFOQUE_ENTREVISTA_HUMANA else "general"
    demo = GuionEntrevista(
        enfoque=f"Entrevista {tipo.replace('_', ' ')} para {titulo}: validar {ENFOQUE_ENTREVISTA_HUMANA[tipo].split(':')[0]}.",
        temas=[q.rstrip("?.").lstrip("¿") for q in _PREGUNTAS_DEMO_HUMANA[tipo]],
        preguntas=list(_PREGUNTAS_DEMO_HUMANA[tipo]),
    )
    client = _client()
    if client is None:
        return demo, False
    try:
        resp = client.responses.parse(
            model=MODEL,
            instructions=(
                "Diseñas el guion de apoyo para una ENTREVISTA HUMANA de RH en México (la realiza una persona, no la IA). "
                "Devuelve 5 a 7 temas y una pregunta abierta y conductual por tema, en español mexicano, cortas y ligadas "
                f"al puesto. Este tipo de entrevista se enfoca en: {ENFOQUE_ENTREVISTA_HUMANA[tipo]}. "
                f"PROHIBIDO cualquier tema sobre: {DATOS_SENSIBLES_PROHIBIDOS}."
            ),
            input=(f"Puesto: {titulo}\nRequisitos: {requisitos or 'no especificados'}\n"
                   f"Resumen del candidato: {candidato_resumen or 'sin información previa'}"),
            text_format=GuionEntrevista,
        )
        return resp.output_parsed, True
    except Exception:  # noqa: BLE001 — el guion es apoyo: nunca bloquea
        return demo, False


def temas_de_guion(guion: dict) -> List[str]:
    """Temas del guion; para guiones previos a Fase 4 (solo `preguntas`) usa las preguntas como temas."""
    g = guion or {}
    return list(g.get("temas") or g.get("preguntas") or [])


# Despedida FIJA de la entrevistadora: es el marcador que el navegador detecta y que el servidor
# verifica en /finalizar (Punto 4, fallback "marcador"). Cambiarla aquí cambia la verificación.
DESPEDIDA_ENTREVISTA = "Con esto terminamos la entrevista"


# Aviso de silencio (texto fijo; el navegador lo replica con talk() en modo avatar).
AVISO_SILENCIO = "{nombre}, no te escuché. ¿Comenzamos?"
# Lo que la entrevistadora dice si el candidato pide un momento antes de empezar.
ESPERA_INICIO = "Tómate tu tiempo, avísame cuando quieras comenzar."


_AGENTES = {"en": 0.0, "por_empresa": {}}


def agente_de_empresa(empresa: str) -> str:
    """2026-10-15 (UVAQ): nombre del agente de la Cuenta cuyo nombre comercial es `empresa` («Vasco, asistente de Talento
    UVAQ»); "" = Red Human. Se lee de la base con un caché de 60 s (la presentación se arma en muchos lugares)."""
    import time

    empresa = " ".join(str(empresa or "").split())
    if not empresa:
        return ""
    if time.time() - _AGENTES["en"] > 60:
        try:
            from ..database import SessionLocal
            from ..models import Cuenta

            with SessionLocal() as db:
                filas = db.query(Cuenta.nombre_comercial, Cuenta.nombre_agente).filter(Cuenta.nombre_agente != "").all()
            _AGENTES["por_empresa"] = {" ".join((n or "").split()): (a or "").strip() for n, a in filas if n and a}
        except Exception:  # noqa: BLE001 — sin base (pruebas unitarias) o columna aún sin crear: Red Human
            _AGENTES["por_empresa"] = {}
        _AGENTES["en"] = time.time()
    return _AGENTES["por_empresa"].get(empresa, "")


def refrescar_agentes() -> None:
    _AGENTES["en"] = 0.0


def precargar_agente(empresa: str, agente: str) -> None:
    """Lo deja en el caché sin esperar al commit (scripts de configuración que validan dentro de su transacción)."""
    agente_de_empresa(empresa)  # carga lo de la base si el caché está vencido
    _AGENTES["por_empresa"][" ".join(str(empresa or "").split())] = (agente or "").strip()


def presentacion_bot(empresa: str = "") -> str:
    """La presentación EXACTA del bot en TODAS las rutas (2026-10-13, checklist Grupak; antes «…agente virtual de RH de
    [empresa].»): «Soy Red Human, el agente virtual de [empresa].» La empresa es la de `serial.nombre_empresa_candidato`
    (el Cliente de la vacante si se muestra al candidato; si no, la Cuenta) — la MISMA que usa el cierre. Se dice UNA sola
    vez por postulación (`prefiltro_conversacional.ya_presentado`)."""
    empresa = " ".join(str(empresa or "").split())
    agente = agente_de_empresa(empresa)
    if agente:
        return f"Soy {agente.rstrip('.')}."  # 2026-10-15 (UVAQ): nombre del agente de la Cuenta
    return f"Soy Red Human, el agente virtual de {empresa}." if empresa else "Soy Red Human, el agente virtual de RH."


def mensaje_inicial_entrevista(titulo_vacante: str, empresa: str = "") -> str:
    """Introducción EXACTA: initialMessage del avatar y primer mensaje del modo texto. Cambio 31: con la presentación
    unificada («Soy Red Human, el agente virtual de RH de [empresa].») y el nombre real de la vacante."""
    puesto = (titulo_vacante or "").strip() or "el puesto"
    return (
        f"Hola. {presentacion_bot(empresa)} Gracias por participar en el proceso para {puesto}. "
        "Vamos a conversar sobre tu experiencia, tus intereses y algunos aspectos relevantes para el puesto. ¿Comenzamos?"
    )


def mensaje_inicial_entrevista_whatsapp(titulo_vacante: str = "", nombre: str = "", primera: str = "", pedir_permiso: bool = False) -> str:
    """Transición a la Entrevista Red Human por WhatsApp (2026-10-11 tarde): UN solo mensaje, natural, sin reglas internas
    (ni «unos 10 minutos» ni «las revisa RH») y sin pedir permiso — el aviso de IA ya va en el aviso de privacidad, que el
    candidato aceptó. Se concatena con la primera pregunta del guion:
    «Listo, [nombre]. Soy Red Human, te haré unas preguntas sobre tu experiencia.\n¿Cuál fue tu último trabajo?».
    `pedir_permiso` solo para una postulación sin consentimiento registrado (no debería pasar: el prefiltro lo exige)."""
    # Cambio 19 (2026-10-12): texto EXACTO «Listo, [nombre]. Ahora te haré unas preguntas sobre tu experiencia. [primera]» — sin
    # pedir permiso y sin volver a presentarse (ya se presentó en el saludo del prefiltro)
    inicio = f"Listo, {nombre.strip()}." if (nombre or "").strip() else "Listo."
    texto = f"{inicio} Ahora te haré unas preguntas sobre tu experiencia."
    if pedir_permiso:
        return f"{texto} ¿Comenzamos?"
    return f"{texto} {primera.strip()}" if (primera or "").strip() else texto


CANAL_ENTREVISTA = {
    "whatsapp": "CANAL: conversas por WhatsApp (texto). Mensajes CORTOS de chat (1-3 renglones), una sola pregunta por mensaje, "
                "sin listas ni formato.",
    "llamada": "CANAL: es una LLAMADA de voz. Frases muy cortas y fáciles de escuchar, sin enumeraciones; confirma que te "
               "escucha bien si la respuesta llega cortada.",
}


def prompt_entrevistador(
    titulo: str,
    requisitos: str,
    candidato_nombre: str,
    preguntas: Optional[List[str]] = None,
    *,
    empresa: str = "",
    temas: Optional[List[str]] = None,
    enfoque: str = "",
    enfoque_entrevista: str = "profesional",
    ubicacion: str = "",
    modalidad: str = "",
    sueldo: str = "",
    beneficios: Optional[List[str]] = None,
    area: str = "",
    canal: str = "avatar",
    guion_de_vacante: bool = False,
    bloque_operativo: str = "",
    ya_respondido: Optional[List[dict]] = None,
) -> str:
    """System prompt compartido por el avatar (Anam) y el modo texto — misma personalidad en ambos.
    Cambios 51-52 (2026-10-15): `ya_respondido` = lo que el candidato contestó en la solicitud web y en el prefiltro (REGLA
    DE ORO: no se vuelve a preguntar); reglas de comprensión (refrasear, «sí o no» solo ante ambigüedad) y de límites (nada
    fuera de la vacante: «Eso lo puedes platicar con el equipo de RH de [empresa]»).
    2026-10-10: con `bloque_operativo` (enfoque operativo) las preguntas son las del guion ARMADO, en orden.
    2026-10-09: también para la Entrevista por WhatsApp y la Llamada Red Human (`canal`). Con `guion_de_vacante` las
    preguntas son las de la plantilla de conversación de la vacante (aprobadas o editadas por RH): se cubren todas.

    Fase 4 (Punto 3): protocolo de inicio y silencio, sin numerar, una pregunta por intervención, de
    lo general a lo específico, sin repetir lo ya respondido, entrevistadora (no lectora de
    cuestionario). El guion es referencia de temas, nunca script literal ni obligatorio de agotar."""
    enfoque_entrevista = _enfoque_valido(enfoque_entrevista)
    temas = temas or preguntas or []
    lista_temas = "\n".join(f"- {t}" for t in temas) or "- Experiencia relacionada con el puesto"
    referencia = "\n".join(f"- {p}" for p in (preguntas or []))
    condiciones = "; ".join(
        x for x in [
            f"ubicación: {ubicacion}" if ubicacion else "",
            f"modalidad: {modalidad}" if modalidad else "",
            f"sueldo: {sueldo}" if sueldo and sueldo != "A convenir" else "",
            f"beneficios: {', '.join(beneficios)}" if beneficios else "",
            f"área: {area}" if area else "",
        ] if x
    ) or "sin datos adicionales"
    empresa_txt = empresa or "la empresa"
    return (
        (f"Eres {agente_de_empresa(empresa).rstrip('.')}, la entrevistadora de RH de {empresa} (México). Si te presentas, usa "
         f"EXACTAMENTE «{presentacion_bot(empresa)}» y nunca otro nombre. Si te preguntan directamente si eres una IA, respóndelo "
         "con naturalidad y sigue. " if agente_de_empresa(empresa) else
         f"Eres Red Human, la entrevistadora de RH (México). Si te presentas, usa EXACTAMENTE «{presentacion_bot(empresa)}» "
         "(Cambio 31): nunca otro título — nunca como 'asistente virtual', 'agente de inteligencia artificial', 'asistente de "
         "reclutamiento' ni con un nombre de persona. Si te preguntan directamente si eres una IA, respóndelo "
         "con naturalidad y sigue. ") +
        f"Entrevistas a {candidato_nombre} para el puesto de {titulo} en {empresa_txt}. "
        f"Cuando hables de la empresa, llámala siempre «{empresa_txt}», nunca de otra forma.\n"
        f"Requisitos indispensables: {requisitos or 'no especificados'}.\n"
        f"Condiciones concretas de la vacante: {condiciones}. Si el candidato pregunta por horario, ubicación, "
        "modalidad o sueldo, usa EXACTAMENTE estos datos; lo que no esté aquí, di que RH lo confirmará. "
        "Nunca hables en genérico cuando tienes el dato.\n\n"
        f"Objetivo de la entrevista: {enfoque or 'validar experiencia real, criterio y motivación para el puesto'}.\n"
        f"Enfoque: {enfoque_entrevista} — cubre: {ENFOQUE_ENTREVISTA_TEMAS[enfoque_entrevista]}.\n"
        f"Temas a cubrir (en este orden aproximado):\n{lista_temas}\n"
        + (bloque_operativo if bloque_operativo else
           (f"GUION DE LA VACANTE (definido por RH): cubre TODAS estas preguntas, en este orden, adaptando solo el tono y "
             f"repreguntando brevemente cuando haga falta; no agregues preguntas de otros temas:\n{referencia}\n") if referencia and guion_de_vacante
           else (f"Preguntas de referencia (inspiración de tono, NO script; no tienes que hacerlas todas ni tal cual):\n{referencia}\n" if referencia else ""))
        + (f"{CANAL_ENTREVISTA[canal]}\n" if canal in CANAL_ENTREVISTA else "")
        + "\n"
        # 2026-10-11 (tarde): por WhatsApp la transición ya hizo la primera pregunta (sin pedir permiso)
        + ("PROTOCOLO DE INICIO: tu primer mensaje ya se presentó («Listo, … Soy Red Human…») e hizo la PRIMERA pregunta "
           "del guion; lo que conteste el candidato es su respuesta a esa pregunta. Sigue con la siguiente pregunta, sin "
           "volver a presentarte, sin frases de transición y sin pedir permiso nunca.\n\n" if canal == "whatsapp" else "")
        + ("" if canal == "whatsapp" else
        "PROTOCOLO DE INICIO: tu primer mensaje ya se presentó y terminó con «¿Comenzamos?». Si la respuesta "
        "es afirmativa (sí, claro, vamos, adelante, listo, lista, ok, dale, comencemos), haz DE INMEDIATO la "
        "primera pregunta: sin frases de transición ('perfecto, empecemos', 'muy bien', 'excelente'), sin "
        "volver a presentarte y sin volver a pedir confirmación nunca más. Si contesta que no o pide un "
        f"momento, responde solo: «{ESPERA_INICIO}» y espera. Si recibes un turno vacío o sin contenido antes "
        f"de empezar, di: «{AVISO_SILENCIO.format(nombre=candidato_nombre)}».\n\n")
        + "CÓMO ENTREVISTAS: (1) frases cortas y lenguaje sencillo, en español mexicano; (2) UNA sola pregunta "
        "principal por intervención — jamás dos preguntas en la misma oración; SOLO preguntas ABIERTAS (qué, cómo, "
        "cuéntame, platícame) — nunca de sí/no ni de prefiltro; (3) primero pregunta algo "
        "general del tema y luego profundiza según lo que responda, con repreguntas también cortas (máximo "
        "dos por tema); si la respuesta es general o vaga, REPREGUNTA pidiendo un ejemplo concreto (qué hizo, "
        "cuándo, cómo y con qué resultado); (4) NUNCA digas 'Pregunta 1', 'Pregunta 2', ni numeres ni enumeres las preguntas; "
        "(5) si la persona ya respondió algo que ibas a preguntar después, NO lo vuelvas a preguntar: "
        "reconoce brevemente y sigue con lo siguiente; (6) compórtate como una entrevistadora que conversa, "
        "no como quien lee un cuestionario: reacciona a lo que dice, conecta temas, sé cálida y profesional; "
        "(7) no evalúes en voz alta, no prometas nada sobre el resultado: PROHIBIDO decir «tu perfil es "
        "compatible», «vas muy bien», «seguro pasas» o que avanzará en el proceso — la decisión la toma una persona "
        "de RH; (8) cuando ya tengas suficiente de un tema, cambia de tema sin anunciarlo.\n\n"
        + TONO_CORPORATIVO.format(nombre=candidato_nombre) + "\n\n"
        + reglas_comprension_y_limites(empresa_txt) + "\n\n"
        + bloque_ya_respondido(ya_respondido or [])
        + f"CUMPLIMIENTO (NO NEGOCIABLE): nunca preguntes, insinúes ni registres datos sobre {DATOS_SENSIBLES_PROHIBIDOS}. "
        "Si la persona los menciona por su cuenta, no repreguntes, no comentes y sigue con el tema laboral.\n\n"
        "CIERRE: cuando hayas cubierto los temas (o la persona no tenga más que aportar), despídete en un "
        f"solo mensaje que empiece EXACTAMENTE con «{DESPEDIDA_ENTREVISTA}, {candidato_nombre}.» seguido de un "
        "agradecimiento breve y de que el equipo de RH le contactará. Después de despedirte no hagas más "
        "preguntas ni reabras la conversación."
    )


class TurnoEntrevista(BaseModel):
    respuesta: str = Field(description="Siguiente mensaje de la entrevistadora, breve, español mexicano.")
    terminada: bool = Field(description="true solo cuando ya cubriste los temas y te despediste en este mensaje.")


def entrevista_turno(system_prompt: str, historial: List[dict], preguntas: Optional[List[str]] = None,
                     operativo: bool = False) -> Tuple[TurnoEntrevista, bool]:
    """Modo texto (demo o fallback sin avatar). historial: [{"rol","texto"}], el último es del candidato.
    `preguntas` (2026-10-09): las del guion de la vacante — en modo demo se hacen tal cual, en orden.
    `operativo` (2026-10-11): el guion operativo respeta sus condicionales, pide UN dato concreto ante una respuesta vaga
    y reformula sin cambiar de tema ante «no entiendo» (`entrevista_operativa.turno_demo`)."""
    client = _client()
    # 2026-10-11 (tarde): por WhatsApp la transición ya hizo la primera pregunta (sin pedir permiso): la primera respuesta
    # del candidato es la respuesta a esa pregunta, no una confirmación para empezar
    from .entrevista_operativa import texto_pregunta

    qs0 = [q for q in (preguntas or []) if (q or "").strip()]
    primera_hecha = bool(qs0 and historial and historial[0]["rol"] == "assistant"
                         and " ".join(texto_pregunta(qs0[0]).split()) in " ".join(historial[0]["texto"].split()))
    if operativo:
        # Cambio 19: el motivo de salida se repregunta UNA vez (despido, conflicto, renuncia sin motivo o vaga) —
        # garantizado en código, con o sin IA
        from .entrevista_operativa import repregunta_pendiente

        rq = repregunta_pendiente(historial)
        if rq:
            return TurnoEntrevista(respuesta=rq, terminada=False), client is not None
    if client is None and operativo:
        from .entrevista_operativa import turno_demo

        n_agente = sum(1 for m in historial if m["rol"] == "assistant")
        n_usuario = sum(1 for m in historial if m["rol"] == "user")
        ultimo = (historial[-1]["texto"] if historial else "").strip().lower()
        if not primera_hecha and n_usuario == 1 and n_agente == 1 and not any(w in ultimo for w in ("si", "sí", "listo", "lista", "adelante", "vamos", "claro",
                                                                               "ok", "dale", "comencemos", "empecemos")):
            return TurnoEntrevista(respuesta=ESPERA_INICIO, terminada=False), False
        texto, fin = turno_demo([q for q in (preguntas or []) if (q or "").strip()], historial)
        if not fin:
            return TurnoEntrevista(respuesta=texto, terminada=False), False
        return TurnoEntrevista(respuesta=f"{DESPEDIDA_ENTREVISTA}, gracias. ¡Muchas gracias por tu tiempo! El equipo de RH revisará tu "
                                         "entrevista y te contactará pronto. 😊", terminada=True), False
    if client is None:
        n_agente = sum(1 for m in historial if m["rol"] == "assistant")
        n_usuario = sum(1 for m in historial if m["rol"] == "user")
        demo_qs = [q for q in (preguntas or []) if (q or "").strip()] or _guion_demo("el puesto").preguntas
        ultimo = (historial[-1]["texto"] if historial else "").strip().lower()
        # Protocolo de inicio: el primer turno del candidato es la confirmación de que está listo.
        if n_usuario == 1 and n_agente == 1 and not primera_hecha:
            afirmativo = any(w in ultimo for w in ("si", "sí", "listo", "lista", "adelante", "vamos", "claro", "ok", "dale", "comencemos"))
            if not afirmativo:
                return TurnoEntrevista(respuesta=ESPERA_INICIO, terminada=False), False
        # n_agente incluye el saludo inicial: la pregunta i-ésima es demo_qs[n_agente - 1]
        i = n_agente if primera_hecha else max(0, n_agente - 1)
        if i < len(demo_qs):
            return TurnoEntrevista(respuesta=demo_qs[i], terminada=False), False
        nombre = "gracias"
        return (
            TurnoEntrevista(
                respuesta=f"{DESPEDIDA_ENTREVISTA}, {nombre}. ¡Muchas gracias por tu tiempo! El equipo de RH revisará tu entrevista y te contactará pronto. 😊",
                terminada=True,
            ),
            False,
        )

    mensajes = [{"role": ("user" if m["rol"] == "user" else "assistant"), "content": m["texto"]} for m in historial]
    resp = client.responses.parse(
        model=MODEL,
        instructions=system_prompt,
        input=mensajes,
        text_format=TurnoEntrevista,
    )
    turno = resp.output_parsed
    turno.respuesta = sin_elogios(sin_promesas(turno.respuesta))  # retro 2026-10-09 / Cambio 31: en código, no solo en el prompt
    turno.respuesta = dosificar_agradecimiento(sin_conteo(turno.respuesta), historial)  # Cambio 19
    # 2026-10-11: la marca de una condicional del guion («[Solo si responde sí]») nunca llega al candidato
    turno.respuesta = re.sub(r"\[\s*solo si (?:responde|contesta|dice)[^\]]*\]\s*", "", turno.respuesta, flags=re.IGNORECASE).strip()
    return turno, True


# Retro 2026-10-09: frases que valoran o prometen avance. El agente NUNCA las dice (entrevista, prefiltro ni agenda).
_RE_PROMESA = re.compile(
    r"[^.!?\n]*\b(perfil (es |resulta )?(muy )?(compatible|ideal|adecuado)|eres (el|la) (candidat[oa] )?(ideal|indicad[oa])|"
    r"vas (muy )?bien|seguro (que )?(pasas|avanzas|quedas)|(vas a|vas|seguro) (avanzar|pasar|quedar)|"
    r"avanzar[aá]s|pasar[aá]s a la siguiente|(quedaste|est[aá]s) (seleccionad|contratad|aceptad)\w*)\b[^.!?\n]*[.!?]?",
    re.IGNORECASE)


# Cambio 19 (2026-10-12): el agente no anuncia cuántas preguntas faltan ni dice «gracias» en cada mensaje
_RE_CONTEO = re.compile(
    r"[^.!?:¿\n]*\b((me |nos |te )?(faltan|quedan|falta|queda) (solo |nada mas |unas |un par de )?(\d+|una|dos|tres|cuatro|cinco|pocas|"
    r"unas cuantas)? ?(preguntas?|cosas|datos)|pregunta (\d+|numero \d+) de \d+|(es la |la |esta es la |una )?[uú]ltima pregunta|"
    r"ya (casi )?(terminamos|acabamos)|estamos por terminar|unas? (preguntas?|cosas) m[aá]s)\b[^.!?:¿\n]*[.!?:]?",
    re.IGNORECASE)
_RE_GRACIAS_INICIAL = re.compile(r"^\s*[¡!]?\s*(perfecto|muy bien|excelente|genial|de acuerdo|ok|vale|claro)?[,!]?\s*(muchas )?gracias"
                                 r"( por [^.!?\n]*)?\s*[.!,]*\s*(🙌|🙂|😊|👍)?\s*", re.IGNORECASE)
# Cambio 31: agradecimientos BREVES y VARIADOS, nunca elogios ni calificaciones («¡Excelente!», «Muy bien»)
VARIANTES_GRACIAS = ("Gracias, {nombre}.", "Entendido.", "Gracias.", "Anotado.")
def reglas_comprension_y_limites(empresa: str) -> str:
    """Cambio 51 (2026-10-15): comprensión y límites del agente — las mismas reglas que aplica el código del prefiltro y de
    la entrevista fija (`prefiltro_conversacional.no_entiende` / `es_negociacion`)."""
    emp = empresa or "la empresa"
    return (
        "COMPRENSIÓN Y LÍMITES (NO NEGOCIABLE): (a) si el candidato NO entiende («No sé qué sea eso», «¿qué es eso?», «no le "
        "entendí»), REFRASEA la pregunta con otras palabras y explica en una frase a qué te refieres — NUNCA repitas el mismo "
        "texto; (b) «Confírmame con un sí o un no» SOLO cuando su respuesta fue ambigua («más o menos», «depende»), nunca "
        "cuando no entendió ni como forma normal de preguntar; (c) NUNCA prometas, ofrezcas ni negocies nada que no esté en "
        "los datos de la vacante (sueldo distinto, bonos, horarios, cambios de turno, fechas): si pregunta «¿se puede "
        f"negociar?» o algo fuera de la vacante, responde EXACTAMENTE «Eso lo puedes platicar con el equipo de RH de {emp}.» "
        "y retoma tu pregunta; (d) si después de un tema sigue una actividad automática, NO digas que RH le escribirá: usa "
        "la transición («Ahora te haré unas preguntas sobre tu experiencia…»); (e) Cambio 59 — si el candidato PREGUNTA algo "
        "de la vacante (ubicación, sueldo, horario, prestaciones, actividades), responde con el dato EXACTO de la vacante en "
        "UNA línea («El trabajo es en [ubicación].», «El sueldo es de [sueldo].»); si ese dato no está en la vacante, "
        f"responde EXACTAMENTE «Eso lo puedes platicar con el equipo de RH de {emp}.»; NUNCA tomes su pregunta como respuesta a "
        "la tuya y, en seguida, repite tu pregunta pendiente; (f) si su respuesta no contesta tu pregunta, repregúntala UNA sola "
        "vez con otras palabras y en lenguaje natural (NUNCA con el nombre interno de un campo, p. ej. «tiempo de traslado»: "
        "di «¿Cuánto tardarías en llegar de tu casa al trabajo, más o menos?»); si vuelve a no contestar, regístralo como "
        "«Sin respuesta» y pasa a la siguiente pregunta."
    )


def bloque_ya_respondido(previas: List[dict]) -> str:
    """Cambio 52 — REGLA DE ORO: lo que el candidato YA contestó en la solicitud web y en los prefiltros entra al prompt y
    no se vuelve a preguntar (salvo que el guion de la vacante pida confirmarlo de forma expresa: «¿Confirmas…?»)."""
    filas = [f"- {r.get('pregunta') or r.get('criterio') or ''}: «{str(r.get('respuesta') or '')[:160]}» ({r.get('fuente') or 'antes'})"
             for r in previas if isinstance(r, dict) and str(r.get("respuesta") or "").strip()][:25]
    if not filas:
        return ""
    return ("LO QUE YA RESPONDIÓ (solicitud web y prefiltro) — REGLA DE ORO: NO vuelvas a preguntar nada de esto (puesto, "
            "sueldo, ubicación, traslado, edad, colonia, requisitos); úsalo como contexto. Si en el cierre ya se dijeron puesto, "
            "sueldo y ubicación, pregunta SOLO «¿Cuándo podrías empezar?». La única excepción es una pregunta del guion de la "
            "vacante que pida confirmarlo de forma EXPRESA («¿Confirmas…?»); si su respuesta contradice lo que dijo antes, "
            "regístralo como inconsistencia:\n" + "\n".join(filas) + "\n\n")


TONO_CORPORATIVO = (
    "TONO (experto de RH, corporativo): (a) agradece BREVE y con variedad — «Gracias, {nombre}.», «Entendido.», «Anotado.» — "
    "y no en cada mensaje; (b) PROHIBIDO elogiar o calificar respuestas («¡Excelente!», «¡Muy bien!», «¡Perfecto!», «Buena "
    "respuesta», «Qué interesante»); (c) PROHIBIDO frases de formulario («Para confirmar:», «Primero confírmame:», «Para no "
    "equivocarme») y ejemplos de formato («Por ejemplo, «15 mil al mes»»): pregunta directo; (d) la escolaridad se pregunta "
    "EXACTAMENTE «" + "¿Cuál es tu último grado de estudios?" + "»; (e) EVASIVAS («lo que ofrezcan», «depende», «lo que sea»): "
    "repregunta UNA SOLA VEZ pidiendo el dato concreto (la frase «Claro. Solo para tenerlo en cuenta, …» es SOLO para el "
    "sueldo: «Claro. Solo para tenerlo en cuenta, ¿con qué cantidad…?»); si vuelve a evadir, registra lo que dijo (p. ej. "
    "«Flexible») y AVANZA sin insistir. Una respuesta completa NUNCA se repregunta."
)
_RE_ELOGIO = re.compile(r"^\s*[¡!]?\s*(excelente|muy bien|perfecto|genial|fant[aá]stico|maravilloso|qu[eé] bien|qu[eé] interesante|"
                        r"buen[ií]simo|incre[ií]ble|s[uú]per|estupendo|buena respuesta|muy buena respuesta|me encanta)\b"
                        r"( respuesta)?\s*[!.,]*\s*(🙌|🙂|😊|👍|👏)?\s*", re.IGNORECASE)


def sin_elogios(texto: str) -> str:
    """Cambio 31: quita el elogio o la calificación con que arranca un mensaje («¡Excelente! ¿Y…?» → «¿Y…?»)."""
    t = texto or ""
    m = _RE_ELOGIO.match(t)
    if not m or not t[m.end():].strip():
        return t
    resto = t[m.end():].strip()
    i = 1 if resto.startswith("¿") else 0
    return resto[:i] + resto[i:i + 1].upper() + resto[i + 1:]
CADA_CUANTOS_GRACIAS = 3


def sin_conteo(texto: str) -> str:
    """Quita las frases que anuncian cuántas preguntas faltan («Solo me faltan 2 preguntas», «Última pregunta»)."""
    limpio = _RE_CONTEO.sub("", texto or "")
    limpio = re.sub(r"[ \t]{2,}", " ", limpio).strip(" ,")
    if not re.search(r"\w", limpio):
        return (texto or "").strip()
    i = 1 if limpio.startswith("¿") else 0
    return limpio[:i] + limpio[i:i + 1].upper() + limpio[i + 1:]


def dosificar_agradecimiento(texto: str, historial: List[dict]) -> str:
    """«Gracias» como mucho una vez cada 3 mensajes del agente: si alguno de los últimos 2 ya agradeció, se quita el
    agradecimiento del inicio (la pregunta queda intacta)."""
    recientes = [m.get("texto") or "" for m in historial or [] if m.get("rol") == "assistant"][-(CADA_CUANTOS_GRACIAS - 1):]
    if not any(re.search(r"\bgracias\b", r, re.IGNORECASE) for r in recientes):
        return texto
    m = _RE_GRACIAS_INICIAL.match(texto or "")
    if not m or not (texto or "")[m.end():].strip():
        return texto
    resto = texto[m.end():].strip()
    return resto[0].upper() + resto[1:] if resto[:1].islower() else resto


def sin_promesas(texto: str) -> str:
    """Quita las oraciones que valoran el perfil o prometen avanzar («Tu perfil es compatible…»)."""
    limpio = _RE_PROMESA.sub("", texto or "")
    limpio = re.sub(r"[ \t]{2,}", " ", limpio)
    limpio = re.sub(r"\n{3,}", "\n\n", limpio).strip()
    return limpio or (texto or "").strip()


# ---------- Evaluación: recomendación + conocimiento profundo del candidato (Punto 5) ----------

DIMENSIONES_PERFIL = [
    "motivadores", "estilo_trabajo", "valores", "decisiones", "aprendizaje",
    "resiliencia", "objetivos", "riesgos", "compatibilidad", "relacion_jefatura",
]


class DimensionPerfil(BaseModel):
    evaluado: bool = Field(description="false si la entrevista no dio información suficiente sobre esta dimensión.")
    conclusion: str = Field(default="", description="1-2 frases. Vacío si no se evaluó.")
    evidencia: List[str] = Field(default_factory=list, description="Citas o paráfrasis textuales del candidato que sustentan la conclusión.")


class PerfilProfundo(BaseModel):
    """Conocimiento profundo del candidato (Punto 5). Cada conclusión trae la evidencia que la
    sustenta; lo que la entrevista no cubrió queda como evaluado=false, nunca inventado."""
    motivadores: DimensionPerfil = Field(description="Qué lo mueve en el trabajo (y en lo personal solo si el enfoque lo incluye).")
    estilo_trabajo: DimensionPerfil = Field(description="Cómo trabaja: organización, autonomía, colaboración, comunicación.")
    valores: DimensionPerfil = Field(description="Valores profesionales que muestra.")
    decisiones: DimensionPerfil = Field(description="Criterio, toma de decisiones y nivel de responsabilidad real.")
    aprendizaje: DimensionPerfil = Field(description="Cómo aprende y cómo maneja errores.")
    resiliencia: DimensionPerfil = Field(description="Manejo de presión, conflicto y adversidad.")
    objetivos: DimensionPerfil = Field(description="Objetivos y expectativas de crecimiento.")
    riesgos: DimensionPerfil = Field(description="Señales de riesgo para el puesto (brechas, inconsistencias, rotación).")
    compatibilidad: DimensionPerfil = Field(description="Ajuste con el puesto y las condiciones ofrecidas.")
    relacion_jefatura: DimensionPerfil = Field(description="Cómo se relaciona con jefes y autoridad.")


class EvaluacionEntrevista(BaseModel):
    resumen: str = Field(description="Resumen ejecutivo de la evaluación integral (CV + entrevista) en 2-3 frases para RH.")
    fortalezas: List[str] = Field(description="2 a 4 fortalezas, con evidencia del CV o de lo que dijo la persona.")
    riesgos: List[str] = Field(default_factory=list, description="PUNTOS POR VALIDAR: 0 a 4 brechas o dudas contra los requisitos que RH debe validar.")
    areas_desarrollo: List[str] = Field(default_factory=list, description="0 a 3 áreas de desarrollo, con evidencia.")
    calif_experiencia: float = Field(description="0 a 10 — solidez de la experiencia contra los requisitos.")
    calif_comunicacion: float = Field(description="0 a 10 — claridad y estructura al comunicar.")
    match_perfil: int = Field(description="AFINIDAD 0 a 100 — empate global con el perfil del puesto, integrando CV y entrevista.")
    # 2026-10-08: score PROPIO de la Entrevista Red Human (su actividad en la ruta y la evaluación integral lo usan; el
    # CV ya aporta el suyo — `match_perfil` mezcla ambos y no sirve como calificación de la entrevista).
    score_entrevista: int = Field(description="0 a 100 — desempeño SOLO en la entrevista (lo que dijo la persona y cómo lo sustentó), SIN considerar el CV.")
    recomendacion: Literal["avanzar", "revision", "no_avanzar"] = Field(
        description="Recomendación PRELIMINAR para RH; la decisión final siempre es humana."
    )
    evidencia: str = Field(description="Citas o paráfrasis concretas de la entrevista que sustentan la recomendación.")
    perfil: Optional[PerfilProfundo] = Field(default=None, description="Conocimiento profundo del candidato con evidencia por dimensión.")
    # 2026-09-13: lo que la entrevista NO alcanzó a cubrir (entrevista suficiente pero corta) — RH lo
    # valida en la Entrevista Humana. Vacío cuando la entrevista fue completa.
    faltante: List[str] = Field(default_factory=list, description="Temas que la entrevista no cubrió y que RH debe validar después.")
    # 2026-10-09: contradicciones entre lo que dijo en la entrevista y sus respuestas previas (CV, formulario web,
    # prefiltro). NUNCA descartan: van a «Puntos por validar» como «Inconsistencia: …» para que RH las aclare.
    inconsistencias: List[str] = Field(default_factory=list, description="Contradicciones concretas entre la entrevista y las "
                                       "respuestas previas o el CV, citando ambas versiones. Vacío si no hay.")


PREFIJO_INCONSISTENCIA = "Inconsistencia: "


def _sin_escolaridad(ev: "EvaluacionEntrevista") -> "EvaluacionEntrevista":
    """2026-10-10 (Cambio 3): la entrevista IA no genera alertas de escolaridad ni de documentos — se validan en la
    actividad de Documentos. Garantizado en código (puntos por validar, faltantes e inconsistencias)."""
    from .entrevista_operativa import es_alerta_escolaridad

    ev.riesgos = [r for r in ev.riesgos or [] if not es_alerta_escolaridad(r)]
    ev.faltante = [r for r in ev.faltante or [] if not es_alerta_escolaridad(r)]
    ev.inconsistencias = [r for r in ev.inconsistencias or [] if not es_alerta_escolaridad(r)]
    return ev


def _aplicar_inconsistencias(ev: "EvaluacionEntrevista") -> "EvaluacionEntrevista":
    """Garantía en código: cada contradicción queda en Puntos por validar y, por sí sola, nunca lleva a «No avanzar» —
    RH la aclara antes de decidir (la IA no descarta). También quita las alertas de escolaridad/documentos."""
    ev = _sin_escolaridad(ev)
    nuevas = [x.strip() for x in ev.inconsistencias or [] if x and x.strip()]
    if not nuevas:
        return ev
    previos = list(ev.riesgos or [])
    ev.riesgos = [f"{PREFIJO_INCONSISTENCIA}{x}" if not x.startswith(PREFIJO_INCONSISTENCIA) else x for x in nuevas] + [
        r for r in previos if not r.startswith(PREFIJO_INCONSISTENCIA)]
    if ev.recomendacion == "no_avanzar":
        ev.recomendacion = "revision"  # type: ignore[assignment]
    return ev


class SuficienciaEntrevista(BaseModel):
    """Antes de evaluar una entrevista corta (2026-09-13): ¿alcanza para una evaluación integral?"""

    suficiente: bool = Field(description="true solo si el candidato aportó información real y verificable sobre la mayoría de los temas.")
    temas_cubiertos: List[str] = Field(default_factory=list, description="Temas del guion que sí quedaron respondidos con contenido.")
    temas_faltantes: List[str] = Field(default_factory=list, description="Temas del guion sin respuesta o con respuesta vacía/evasiva.")
    motivo: str = Field(description="1-2 frases para RH explicando por qué es o no suficiente.")


def texto_util_candidato(transcript: List[dict]) -> Tuple[int, int]:
    """(turnos con contenido real, caracteres útiles) del candidato — un «sí», «ok» o un turno
    vacío no cuentan como respuesta de entrevista."""
    turnos, chars = 0, 0
    for m in transcript or []:
        if m.get("rol") != "user":
            continue
        t = " ".join(str(m.get("texto", "")).split())
        if len(t) >= 12:
            turnos += 1
            chars += len(t)
    return turnos, chars


def suficiencia_entrevista(titulo: str, temas: List[str], transcript: List[dict]) -> Tuple[SuficienciaEntrevista, bool]:
    """Juicio de suficiencia para una entrevista corta. Demo: heurística por turnos útiles."""
    client = _client()
    turnos, _chars = texto_util_candidato(transcript)
    if client is None:
        suficiente = turnos >= 3
        return (
            SuficienciaEntrevista(
                suficiente=suficiente,
                temas_cubiertos=list(temas or [])[:turnos],
                temas_faltantes=list(temas or [])[turnos:],
                motivo=("Modo demo: hubo respuestas suficientes para evaluar." if suficiente
                        else "Modo demo: el candidato contestó muy poco; no alcanza para una evaluación integral."),
            ),
            False,
        )
    dialogo = "\n".join(f"{'Entrevistadora' if m['rol'] == 'assistant' else 'Candidato'}: {m['texto']}" for m in transcript)
    resp = client.responses.parse(
        model=MODEL,
        instructions=(
            "Decides si una entrevista laboral CORTA aporta información suficiente para evaluar al candidato. "
            "Sé estricto: respuestas de una palabra, evasivas o sin contenido verificable NO cuentan. "
            "Es suficiente solo si la mayoría de los temas tiene una respuesta con contenido real."
        ),
        input=f"Puesto: {titulo}\nTemas que debía cubrir la entrevista: {'; '.join(temas or []) or 'no especificados'}\n\nTranscripción:\n{dialogo}",
        text_format=SuficienciaEntrevista,
    )
    return resp.output_parsed, True


def _perfil_demo() -> PerfilProfundo:
    d = lambda c, e: DimensionPerfil(evaluado=True, conclusion=c, evidencia=[e])  # noqa: E731
    no = DimensionPerfil(evaluado=False)
    return PerfilProfundo(
        motivadores=d("Le motiva la estabilidad y aprender del puesto.", "«Me interesa un lugar donde pueda crecer»"),
        estilo_trabajo=d("Ordenado y orientado a cumplir.", "«Me gusta llevar todo anotado»"),
        valores=no, decisiones=d("Decide dentro de su ámbito y escala lo demás.", "«Lo que no me toca lo consulto con mi jefe»"),
        aprendizaje=no, resiliencia=d("Reconoce errores y corrige.", "«Cuando me equivoco lo digo y lo arreglo»"),
        objetivos=no, riesgos=no, compatibilidad=d("Disponibilidad compatible con la vacante.", "«Puedo el horario que me digan»"),
        relacion_jefatura=no,
    )


def _bloque_cv(analisis_cv: Optional[dict], cv_datos: Optional[dict]) -> str:
    """Resumen del Análisis de CV que entra a la evaluación integral. NUNCA incluye nada del
    prefiltro por WhatsApp (2026-09-13): el prefiltro es un filtro de entrada, no una evaluación."""
    a = analisis_cv or {}
    d = cv_datos or {}
    if not a.get("requisitos_cumplidos") and not a.get("brechas") and not d.get("resumen_profesional"):
        return "(sin CV analizado — evalúa solo con la entrevista y dilo en el resumen)"
    lineas = []
    if d.get("resumen_profesional"):
        lineas.append(f"Resumen profesional: {d['resumen_profesional']}")
    if d.get("experiencia_relevante"):
        lineas.append(f"Experiencia relevante: {d['experiencia_relevante']}")
    if a.get("fortalezas_cv"):
        lineas.append("Fortalezas del CV: " + "; ".join(a["fortalezas_cv"]))
    if a.get("requisitos_cumplidos"):
        lineas.append("Requisitos acreditados en el CV: " + "; ".join(a["requisitos_cumplidos"]))
    if a.get("brechas"):
        lineas.append("Brechas / requisitos NO acreditados en el CV: " + "; ".join(a["brechas"]))
    if a.get("compatibilidad_cv"):
        lineas.append(f"Compatibilidad según el CV: {a['compatibilidad_cv']}")
    return "\n".join(lineas)


def evaluar_entrevista(
    titulo: str,
    requisitos: str,
    transcript: List[dict],
    *,
    perfil_ideal: str = "",
    temas: Optional[List[str]] = None,
    enfoque_entrevista: str = "profesional",
    faltante: Optional[List[str]] = None,
    analisis_cv: Optional[dict] = None,
    cv_datos: Optional[dict] = None,
    respuestas_previas: Optional[List[dict]] = None,
) -> Tuple[EvaluacionEntrevista, bool]:
    """EVALUACIÓN INTEGRAL (2026-09-13): se basa ÚNICAMENTE en Análisis de CV + Entrevista Red Human.
    Los datos del prefiltro por WhatsApp quedan estrictamente fuera (evita contradicciones).
    Estructura: Afinidad (match_perfil) / Fortalezas / Puntos por validar (riesgos) / Recomendación,
    más lo que faltó (`faltante`) cuando la entrevista fue corta pero suficiente."""
    enfoque_entrevista = _enfoque_valido(enfoque_entrevista)
    faltante = list(faltante or [])
    previas = [r for r in (respuestas_previas or []) if isinstance(r, dict) and str(r.get("respuesta") or "").strip()]
    client = _client()
    if client is None:
        return (
            _aplicar_inconsistencias(EvaluacionEntrevista(
                resumen="Modo demo: agrega OPENAI_API_KEY para la evaluación integral real (CV + Entrevista Red Human).",
                fortalezas=["Completó la entrevista"],
                riesgos=(["No se cubrió: " + ", ".join(faltante)] if faltante else []),
                areas_desarrollo=[],
                calif_experiencia=7.0,
                calif_comunicacion=7.0,
                match_perfil=70,
                score_entrevista=70,
                recomendacion="revision",
                evidencia="Evaluación simulada (modo demo).",
                perfil=_perfil_demo(),
                faltante=faltante,
                inconsistencias=inconsistencias_demo(transcript, previas),
            )),
            False,
        )

    dialogo = "\n".join(f"{'Entrevistadora' if m['rol'] == 'assistant' else 'Candidato'}: {m['texto']}" for m in transcript)
    bloque_previas = "\n".join(f"- [{r.get('fuente') or 'previa'}] {r.get('pregunta') or r.get('criterio')}: {r.get('respuesta')}"
                               for r in previas[:30])
    resp = client.responses.parse(
        model=MODEL,
        instructions=(
            "Haces la EVALUACIÓN INTEGRAL de un candidato para Red Human (México) con DOS fuentes y solo dos: "
            "(1) el Análisis de CV y (2) la transcripción de la Entrevista Red Human. Ignora por completo "
            "cualquier prefiltro o cuestionario previo. Califica SOLO con base en esas dos fuentes — nunca "
            "inventes. Estructura tu salida así: AFINIDAD (match_perfil 0-100) integrando CV y entrevista; "
            "SCORE DE LA ENTREVISTA (score_entrevista 0-100) que califica ÚNICAMENTE la entrevista, sin el CV; "
            "FORTALEZAS (fortalezas, con evidencia del CV o de lo dicho); PUNTOS POR VALIDAR (riesgos: brechas "
            "del CV no resueltas en la entrevista y dudas que RH debe validar); RECOMENDACIÓN preliminar "
            "(recomendacion) — la decisión final la toma una persona de RH (human-in-the-loop, LFPDPPP). "
            "Si el CV acredita algo que la entrevista contradice, o viceversa, dilo en puntos por validar. "
            "INCONSISTENCIAS: compara lo que dijo en la entrevista con sus RESPUESTAS PREVIAS (formulario web, prefiltro) y con "
            "el CV SOLO para detectar contradicciones concretas; cada una va en `inconsistencias` citando ambas versiones "
            "(«En el formulario dijo X; en la entrevista dijo Y»). Una contradicción NUNCA es motivo de descarte: RH la aclara. "
            "NO generes puntos por validar ni faltantes sobre escolaridad, estudios o documentos (INE, actas, comprobantes, "
            "certificados): se validan aparte en Documentos. "
            "Si se te indican temas que la entrevista NO cubrió, repítelos en `faltante`, no los infieras y "
            "no los califiques. Construye el perfil profundo por dimensión; si la entrevista no cubrió una "
            "dimensión, márcala evaluado=false y déjala vacía. "
            f"Enfoque de la entrevista: {enfoque_entrevista} ({ENFOQUE_ENTREVISTA_TEMAS[enfoque_entrevista]}); "
            "no evalúes dimensiones personales si el enfoque es solo profesional. "
            f"CUMPLIMIENTO (NO NEGOCIABLE): nunca registres, cites ni uses datos sobre {DATOS_SENSIBLES_PROHIBIDOS}, "
            "aunque el candidato los haya mencionado; omítelos por completo."
        ),
        input=(
            f"Puesto: {titulo}\nRequisitos indispensables: {requisitos}\nPerfil ideal: {perfil_ideal or 'no especificado'}\n"
            f"Temas que la entrevista debía cubrir: {'; '.join(temas or []) or 'no especificados'}\n"
            + (f"Temas que la entrevista NO cubrió (RH los validará después): {'; '.join(faltante)}\n" if faltante else "")
            + f"\nANÁLISIS DE CV:\n{_bloque_cv(analisis_cv, cv_datos)}\n\n"
            + (f"RESPUESTAS PREVIAS DEL CANDIDATO (solo para detectar contradicciones; NO se califican):\n{bloque_previas}\n\n" if bloque_previas else "")
            + f"ENTREVISTA RED HUMAN (transcripción):\n{dialogo}"
        ),
        text_format=EvaluacionEntrevista,
    )
    salida = resp.output_parsed
    if faltante and not salida.faltante:
        salida.faltante = faltante
    return _aplicar_inconsistencias(salida), True


_RE_NUM = re.compile(r"\d+")


def inconsistencias_demo(transcript: List[dict], previas: List[dict]) -> List[str]:
    """Modo demo (sin IA): detección determinista mínima — una respuesta previa afirmativa contra una negación explícita
    en la entrevista sobre el mismo requisito, o años de experiencia distintos."""
    salida: List[str] = []
    dichos = [_clave_texto(m.get("texto", "")) for m in transcript or [] if m.get("rol") == "user"]
    for r in previas:
        tema = _clave_texto(str(r.get("criterio") or r.get("pregunta") or ""))
        resp = _clave_texto(str(r.get("respuesta") or ""))
        claves = [w for w in tema.split() if len(w) > 5][:3]
        if not claves:
            continue
        for d in dichos:
            if not any(w in d for w in claves):
                continue
            nums_prev, nums_ent = _RE_NUM.findall(resp), _RE_NUM.findall(d)
            negacion = resp.startswith("si") and re.search(r"\b(no tengo|nunca he|no he|no cuento|no se|no esta vigente|"
                                                           r"ya no (esta|lo tengo|la tengo)|(se me |ya se |ya )?venci\w*)\b", d)
            if negacion or (nums_prev and nums_ent and nums_prev[0] != nums_ent[0]):
                salida.append(f"En {r.get('fuente') or 'una respuesta previa'} respondió «{r.get('respuesta')}» a «{r.get('pregunta') or r.get('criterio')}»; "
                              f"en la entrevista dijo «{d[:160]}».")
                break
    return salida[:4]

# ============================================================
# 3.5) Capacitación (Fase 1) — generación de curso con IA
# ============================================================


class PreguntaVerificacion(BaseModel):
    pregunta: str = Field(description="Pregunta breve para verificar que se entendió el módulo.")
    criterio_respuesta_correcta: str = Field(
        description="Qué debe incluir una respuesta correcta, para poder evaluarla después."
    )


class ModuloCursoGenerado(BaseModel):
    titulo: str
    contenido: str = Field(
        description="Instructor IA: guion CONVERSACIONAL (frases cortas, lenguaje natural, pausas para preguntar «¿alguna duda "
        "hasta aquí?»), como si el instructor hablara. Autoguiado: contenido breve, visual y modular (encabezados, listas). "
        "Español mexicano."
    )
    resumen: str = Field(default="", description="2-3 frases del módulo para el material de apoyo (PDF).")
    puntos_clave: List[str] = Field(default_factory=list, description="3 a 5 puntos clave del módulo para el material de apoyo.")
    preguntas_verificacion: List[PreguntaVerificacion] = Field(default_factory=list, description="1 a 2 preguntas de comprensión.")


class PreguntaEvaluacion(BaseModel):
    """Pregunta de la evaluación final INTEGRADA (2026-09-16): opción múltiple o verdadero/falso, calificable sola."""

    pregunta: str = Field(description="Pregunta clara sobre el contenido de los módulos, español mexicano.")
    tipo: Literal["opcion", "vf"] = Field(description="'opcion' = opción múltiple (3-4 opciones); 'vf' = verdadero/falso.")
    opciones: List[str] = Field(description="Para 'opcion': 3 a 4 opciones. Para 'vf': exactamente ['Verdadero', 'Falso'].")
    correcta: int = Field(description="Índice (0-based) de la opción correcta dentro de `opciones`. DEBE ser la única opción verdadera.")
    explicacion: str = Field(default="", description="Una línea que justifica POR QUÉ `opciones[correcta]` es la respuesta de ESTA pregunta (coincide 100 % con ella; no menciona otra opción como correcta).")


class GuionCurso(BaseModel):
    objetivo: str = Field(
        description="Objetivo del curso en 2-3 frases: qué sabrá o podrá hacer la persona al terminar."
    )
    categoria: str = Field(default="", description="Categoría corta del curso (p. ej. Seguridad, Ventas, Inducción, Cumplimiento).")
    modulos: List[ModuloCursoGenerado] = Field(
        description="Módulos ordenados de contenido: el primero de bienvenida y contexto; NO incluyas un módulo de evaluación (va aparte)."
    )
    evaluacion: List[PreguntaEvaluacion] = Field(
        default_factory=list,
        description="Evaluación final integrada: 5 a 10 preguntas (opción múltiple o V/F) que cubren todos los módulos.",
    )


def _guion_curso_demo(tema: str, duracion_horas: float) -> GuionCurso:
    """Plantilla determinista para modo demo (sin OPENAI_API_KEY) — misma estructura que la salida de IA."""
    return GuionCurso(
        objetivo=f"Modo demo: agrega OPENAI_API_KEY para generar el objetivo real de «{tema}».",
        modulos=[
            ModuloCursoGenerado(
                titulo="Bienvenida y objetivos",
                contenido=f"Introducción al curso «{tema}» y a lo que se espera lograr en {duracion_horas} horas.",
                preguntas_verificacion=[
                    PreguntaVerificacion(
                        pregunta="¿Cuál es el objetivo principal de este curso?",
                        criterio_respuesta_correcta="Menciona el propósito general del curso con sus propias palabras.",
                    )
                ],
            ),
            ModuloCursoGenerado(
                titulo=tema,
                contenido="Modo demo: agrega OPENAI_API_KEY para generar el contenido real de este módulo.",
                preguntas_verificacion=[
                    PreguntaVerificacion(
                        pregunta=f"¿Qué aprendiste sobre {tema.lower()}?",
                        criterio_respuesta_correcta="Respuesta registrada en modo demo.",
                    )
                ],
            ),
            ModuloCursoGenerado(
                titulo="Evaluación final",
                contenido="Repaso de los puntos clave del curso y cierre.",
                preguntas_verificacion=[
                    PreguntaVerificacion(
                        pregunta="¿Te sientes preparado(a) para aplicar lo aprendido?",
                        criterio_respuesta_correcta="Respuesta registrada en modo demo.",
                    )
                ],
            ),
        ],
    )


def _evaluacion_demo(tema: str, modulos: List[ModuloCursoGenerado]) -> List[PreguntaEvaluacion]:
    salida = [
        PreguntaEvaluacion(
            pregunta=f"¿Cuál es el objetivo principal del curso «{tema}»?",
            tipo="opcion",
            opciones=["Aplicar lo aprendido en el trabajo diario", "Memorizar definiciones", "Cumplir un trámite", "Ninguna de las anteriores"],
            correcta=0, explicacion="El curso busca que apliques el contenido en tu trabajo.",
        ),
        PreguntaEvaluacion(pregunta="El primer módulo presenta los objetivos del curso.", tipo="vf", opciones=["Verdadero", "Falso"], correcta=0, explicacion="El módulo de bienvenida presenta los objetivos."),
    ]
    for m in modulos[1:4]:
        salida.append(PreguntaEvaluacion(
            pregunta=f"El módulo «{m.titulo}» forma parte de este curso.", tipo="vf", opciones=["Verdadero", "Falso"], correcta=0,
            explicacion=f"«{m.titulo}» es uno de los módulos del curso.",
        ))
    return salida


def _asegurar_evaluacion(g: GuionCurso, tema: str) -> GuionCurso:
    """La evaluación es parte del curso: si el modelo no la trajo o vino inválida, se completa."""
    validas: List[PreguntaEvaluacion] = []
    for q in g.evaluacion:
        if q.tipo == "vf":
            q.opciones = ["Verdadero", "Falso"]
        if not q.opciones or not (0 <= q.correcta < len(q.opciones)):
            continue
        validas.append(q)
    g.evaluacion = validas or _evaluacion_demo(tema, g.modulos)
    # los módulos son de contenido: un «Evaluación final» heredado se quita (la evaluación va integrada aparte)
    g.modulos = [m for m in g.modulos if not m.titulo.strip().lower().startswith("evaluaci")] or g.modulos
    return g


class _CorreccionPregunta(BaseModel):
    indice: int
    correcta: int = Field(description="Índice (0-based) de la opción verdaderamente correcta, decidido de forma independiente.")
    explicacion: str = Field(description="Explicación que justifica EXACTAMENTE esa opción para ESA pregunta.")
    consistente: bool = Field(description="true si la pregunta original ya tenía correcta y explicación coherentes.")


class _VerificacionEvaluacion(BaseModel):
    preguntas: List[_CorreccionPregunta]


def _verificar_evaluacion(client, tema: str, modulos: List[ModuloCursoGenerado], preguntas: List[PreguntaEvaluacion]) -> List[PreguntaEvaluacion]:
    """Segunda pasada (2026-09-19, fix de evaluaciones): el modelo vuelve a resolver cada pregunta SIN ver la
    respuesta marcada y regresa índice + explicación coherentes; se adoptan sus correcciones. Cualquier fallo
    deja las preguntas como estaban (la evaluación nunca se pierde)."""
    if not preguntas:
        return preguntas
    try:
        contexto = "\n\n".join(f"[{m.titulo}] {m.contenido[:1500]}" for m in modulos[:8])
        listado = "\n".join(f"{i}. {q.pregunta}\n   opciones: " + " | ".join(f"({k}) {o}" for k, o in enumerate(q.opciones)) for i, q in enumerate(preguntas))
        resp = client.responses.parse(
            model=MODEL,
            instructions=(
                "Eres el revisor de la evaluación final de un curso de capacitación (México). Para CADA pregunta decide de forma "
                "independiente cuál opción es la correcta con base en el contenido del curso, y escribe una explicación de una línea "
                "que justifique EXACTAMENTE esa opción y hable de ESA pregunta (nunca de otra). Si una pregunta es ambigua o tiene dos "
                "opciones válidas, elige la más sustentada por el contenido. Regresa todas las preguntas, en el mismo orden."
            ),
            input=f"TEMA: {tema}\n\nCONTENIDO DEL CURSO:\n{contexto}\n\nPREGUNTAS:\n{listado}",
            text_format=_VerificacionEvaluacion,
        )
        por_indice = {c.indice: c for c in resp.output_parsed.preguntas}
        for i, q in enumerate(preguntas):
            c = por_indice.get(i)
            if c is None or not (0 <= c.correcta < len(q.opciones)):
                continue
            q.correcta = c.correcta
            if c.explicacion.strip():
                q.explicacion = c.explicacion.strip()
    except Exception as ex:  # noqa: BLE001
        print(f"[ia] verificación de evaluación omitida: {ex}", flush=True)
    return preguntas


def guion_curso(tema: str, duracion_horas: float, contexto: str = "", material: str = "", modalidad: str = "autoguiado", duracion_texto: str = "") -> Tuple[GuionCurso, bool]:
    """Módulo universal (2026-09-16): a partir del tema, el contexto opcional de RH, el material adjunto
    (texto extraído) y la duración, genera objetivo, categoría, módulos y la evaluación final integrada.
    2026-09-19 (Bloque 4): `modalidad` diferencia el prompt — instructor_ia = guion conversacional que el avatar
    dice en voz alta; autoguiado = contenido breve, visual y modular. En ambos, el PDF es material de apoyo
    (resumen + puntos clave), no la experiencia principal."""
    client = _client()
    if client is None:
        g = _guion_curso_demo(tema, duracion_horas)
        g.categoria = g.categoria or "General"
        for m in g.modulos:
            if not m.resumen:
                m.resumen = m.contenido[:240]
            if not m.puntos_clave:
                m.puntos_clave = [x.strip("•- ").strip() for x in m.contenido.split("\n") if x.strip()][:4]
        return _asegurar_evaluacion(g, tema), False

    duracion = duracion_texto.strip() or f"{duracion_horas} horas"
    entrada = f"Tema del curso: {tema}\nDuración total: {duracion}\nModalidad: {'Instructor IA (avatar que explica en voz alta y responde dudas)' if modalidad == 'instructor_ia' else 'Autoguiado (la persona lee en pantalla a su ritmo)'}"
    if contexto.strip():
        entrada += f"\nContexto de RH (público, tono, énfasis): {contexto.strip()[:2000]}"
    if material.strip():
        entrada += f"\n\nMaterial de referencia adjunto (úsalo como fuente principal del contenido):\n{material.strip()[:24000]}"
    if modalidad == "instructor_ia":
        estilo = (
            "MODALIDAD INSTRUCTOR IA: el `contenido` de cada módulo es un GUION CONVERSACIONAL que un instructor con avatar dirá en voz "
            "alta: lenguaje natural y cercano, frases cortas (máximo ~20 palabras), una idea por párrafo, ejemplos cotidianos, y pausas "
            "explícitas para preguntar («¿Alguna duda hasta aquí?», «¿Te ha pasado algo así?») cada 3-4 párrafos. NADA de instructivos "
            "densos, listas largas ni encabezados en mayúsculas: si un tema es largo, se divide en más módulos cortos. "
        )
    else:
        estilo = (
            "MODALIDAD AUTOGUIADO: el `contenido` de cada módulo es BREVE, visual y modular: encabezados cortos, listas con viñetas, "
            "3-6 ideas por módulo, ejemplos concretos; se lee en pantalla en pocos minutos. "
        )
    resp = client.responses.parse(
        model=MODEL,
        instructions=(
            "Diseñas cursos de capacitación para Red Human AI (México) que se cursan en pantalla y terminan con una evaluación "
            "integrada calificada automáticamente. " + estilo +
            "Reglas comunes: (1) el primer módulo es de bienvenida y contexto, breve; (2) el número y tamaño de los módulos es "
            "proporcional a la duración total indicada (un curso de 5 minutos = 2-3 módulos muy cortos; de 1 hora = 4-6; máximo 8); "
            "(3) cada módulo trae `resumen` (2-3 frases) y `puntos_clave` (3-5) para el material de apoyo en PDF; "
            "(4) NO agregues un módulo de evaluación: la evaluación va en `evaluacion` como 5 a 10 preguntas de opción múltiple "
            "(3-4 opciones, UNA sola correcta, sin «todas las anteriores») o verdadero/falso que cubran todos los módulos; en cada "
            "pregunta `correcta` DEBE apuntar a la única opción verdadera según el contenido y `explicacion` DEBE justificar esa "
            "misma opción y referirse a esa misma pregunta — verifica cada una antes de responder; (5) si hay material adjunto, "
            "básate en él y no inventes datos que lo contradigan; (6) asigna una `categoria` corta; (7) nunca pidas ni menciones datos "
            "sensibles (salud, embarazo, religión, estado civil, orientación)."
        ),
        input=entrada,
        text_format=GuionCurso,
    )
    g = _asegurar_evaluacion(resp.output_parsed, tema)
    g.evaluacion = _verificar_evaluacion(client, tema, g.modulos, g.evaluacion)
    return g, True


def horas_desde_texto(texto: str, default: float = 0.5) -> float:
    """«5 min» → 0.08, «15 minutos» → 0.25, «1h» → 1, «1 h 30» → 1.5, «2 horas» → 2, «90» → 1.5 (minutos). Para KPIs."""
    t = (texto or "").strip().lower().replace(",", ".")
    if not t:
        return default
    horas = 0.0
    encontrado = False
    m = re.search(r"(\d+(?:\.\d+)?)\s*(h|hr|hrs|hora|horas)\b", t)
    if m:
        horas += float(m.group(1)); encontrado = True
    m2 = re.search(r"(\d+(?:\.\d+)?)\s*(m|min|mins|minuto|minutos)\b", t)
    if m2:
        horas += float(m2.group(1)) / 60; encontrado = True
    if not encontrado:
        m3 = re.search(r"(\d+(?:\.\d+)?)", t)
        if m3:
            n = float(m3.group(1))
            horas = n / 60 if n >= 10 else n  # «90» = minutos; «2» = horas
            encontrado = True
    return round(horas, 2) if encontrado and horas > 0 else default


class TurnoCurso(BaseModel):
    respuesta: str = Field(description="Siguiente mensaje del instructor, breve, español mexicano.")


def curso_turno(system_prompt: str, historial: List[dict]) -> Tuple[TurnoCurso, bool]:
    """Modo texto (demo o fallback sin avatar) para un módulo de capacitación. historial:
    [{"rol","texto"}], el último es de la persona. A diferencia de entrevista_turno, no hay
    flag `terminada` — el avance de módulo siempre lo dispara el botón "Continuar", nunca la IA
    (ver services/entrevistas.py y la decisión de diseño en el plan de Fase 2)."""
    client = _client()
    if client is None:
        return TurnoCurso(respuesta="Modo demo: agrega OPENAI_API_KEY para conversar con el instructor real."), False

    mensajes = [{"role": ("user" if m["rol"] == "user" else "assistant"), "content": m["texto"]} for m in historial]
    resp = client.responses.parse(
        model=MODEL,
        instructions=system_prompt,
        input=mensajes,
        text_format=TurnoCurso,
    )
    return resp.output_parsed, True


class EvaluacionPregunta(BaseModel):
    pregunta: str
    respondida_correctamente: bool
    evidencia: str = Field(description="Cita o paráfrasis de la respuesta que sustenta la calificación.")


class EvaluacionModulo(BaseModel):
    comprendio: bool = Field(description="true si en general demostró haber entendido el módulo.")
    preguntas: List[EvaluacionPregunta]
    comentario: str = Field(description="1-2 frases de retroalimentación para la persona.")


def evaluar_modulo_curso(
    titulo: str, contenido: str, preguntas_verificacion: list, transcript: List[dict]
) -> Tuple[EvaluacionModulo, bool]:
    """Evalúa las respuestas de un módulo de capacitación contra sus criterios — mismo patrón
    que evaluar_entrevista, pero contra `criterio_respuesta_correcta` en vez del perfil del puesto."""
    client = _client()
    if client is None:
        return (
            EvaluacionModulo(
                comprendio=True,
                preguntas=[
                    EvaluacionPregunta(pregunta=p["pregunta"], respondida_correctamente=True, evidencia="Modo demo")
                    for p in preguntas_verificacion
                ],
                comentario="Modo demo: agrega OPENAI_API_KEY para evaluar la comprensión real.",
            ),
            False,
        )

    dialogo = "\n".join(f"{'Instructor' if m['rol'] == 'assistant' else 'Persona'}: {m['texto']}" for m in transcript)
    preguntas_txt = "\n".join(
        f"- Pregunta: {p['pregunta']}\n  Criterio de respuesta correcta: {p['criterio_respuesta_correcta']}"
        for p in preguntas_verificacion
    )
    resp = client.responses.parse(
        model=MODEL,
        instructions=(
            "Evalúas la comprensión de un módulo de capacitación corporativa para Red Human AI "
            "(México). Califica SOLO con base en lo dicho en la transcripción — nunca inventes ni "
            "infieras. Para cada pregunta de verificación, compárala contra su criterio de respuesta "
            "correcta y decide si la persona la respondió correctamente, citando evidencia concreta "
            "de lo que dijo. Sé constructivo en el comentario final."
        ),
        input=f"Módulo: {titulo}\nContenido explicado:\n{contenido}\n\nPreguntas de verificación:\n{preguntas_txt}\n\nTranscripción:\n{dialogo}",
        text_format=EvaluacionModulo,
    )
    return resp.output_parsed, True


# ============================================================
# 4) Validador de documentos del expediente (módulo 3.11)
# ============================================================


class DocumentoValidado(BaseModel):
    tipo_detectado: str = Field(description="Qué documento parece ser (INE, CURP, RFC, comprobante, NSS, acta, título, otro). Si es basura (tarea, foto casual, captura, meme, documento de otro trámite) escribe 'otro' y descríbelo en observaciones.")
    es_documento_oficial: bool = Field(description="true SOLO si el archivo es un documento oficial/válido del tipo esperado (con formato, sellos, folios o campos propios de ese documento). Cualquier otra cosa → false.")
    coincide_tipo: bool = Field(description="true únicamente si corresponde claramente al tipo de documento solicitado; ante duda → false.")
    legible: bool = Field(description="true si el documento se lee completo, sin cortes, reflejos ni desenfoque.")
    completo: bool = Field(description="false si falta parte del documento (por ejemplo solo el frente de la INE).")
    vigente: Optional[bool] = Field(default=None, description="null si el documento no tiene vigencia visible.")
    nombre_detectado: Optional[str] = Field(default=None, description="Nombre del titular tal como aparece; null si no se lee.")
    coincide_titular: Optional[bool] = Field(
        default=None, description="true/false comparando con el titular esperado; null si no se dio o no se lee."
    )
    motivo_rechazo: Optional[str] = Field(
        default=None, description="Si el documento no sirve, la razón en una frase entendible para el candidato."
    )
    observaciones: str = Field(description="Observaciones breves para RH en español.")


def validar_documento(
    archivo_b64: str,
    extension: str,
    tipo_esperado: str,
    titular_esperado: str = "",
) -> Tuple[DocumentoValidado, bool]:
    client = _client()
    if client is None:
        return (
            DocumentoValidado(
                tipo_detectado=tipo_esperado,
                es_documento_oficial=False,
                coincide_tipo=True,
                legible=True,
                completo=True,
                vigente=None,
                nombre_detectado=None,
                coincide_titular=None,
                motivo_rechazo=None,
                observaciones="Modo demo: documento marcado para revisión humana. Agrega OPENAI_API_KEY para validación real.",
            ),
            False,
        )

    ext = extension.lower().lstrip(".")
    if ext == "pdf":
        try:
            import base64
            import io
            from pypdf import PdfReader
            pdf_bytes = base64.b64decode(archivo_b64)
            reader = PdfReader(io.BytesIO(pdf_bytes))
            texto_pdf = ""
            for page in reader.pages:
                texto_pdf += (page.extract_text() or "") + "\n"
            
            if texto_pdf.strip():
                content_blocks = [
                    {"type": "input_text", "text": f"CONTENIDO DEL DOCUMENTO (TEXTO EXTRAÍDO DEL PDF):\n\n{texto_pdf}\n\nDocumento esperado: {tipo_esperado}. Valida este archivo."}
                ]
            else:
                content_blocks = [
                    {"type": "input_text", "text": "El archivo PDF no contiene texto legible (podría ser una imagen escaneada sin OCR)."}
                ]
        except Exception as e:
            content_blocks = [
                {"type": "input_text", "text": f"Error al extraer texto del PDF usando pypdf: {str(e)}"}
            ]
    else:
        bloque = _bloque_archivo(archivo_b64, extension, "documento")
        content_blocks = [
            bloque,
            {"type": "input_text", "text": f"Documento esperado: {tipo_esperado}. Valida este archivo."}
        ]

    titular = (
        f"Titular esperado del documento: {titular_esperado}. Compara el nombre y llena coincide_titular "
        "(tolera abreviaturas y orden distinto de apellidos; marca false solo si claramente es otra persona)."
        if titular_esperado
        else "No se proporcionó titular esperado: deja coincide_titular en null."
    )
    resp = client.responses.parse(
        model=MODEL,
        instructions=(
            "Validas documentos de expedientes laborales en México (INE, CURP, RFC, comprobante de domicilio, NSS, "
            "acta de nacimiento, título profesional) mediante visión/OCR. Revisa tipo, legibilidad, integridad y vigencia visible. "
            f"{titular} VALIDACIÓN ESTRICTA (2026-09-18): el candidato puede subir cualquier cosa (una tarea escolar, una foto "
            "casual, una captura de pantalla, un recibo ajeno, un documento de otro tipo). Solo es válido si es CLARAMENTE el "
            "documento oficial solicitado, con los elementos propios de ese documento (formato, campos, folios, sellos, logotipos "
            "de la institución). Si no lo es, coincide_tipo=false, es_documento_oficial=false, tipo_detectado='otro' y un "
            "motivo_rechazo claro y amable para el candidato (qué subió y qué debe subir). Ante duda real sobre el tipo, "
            "rechaza; la duda solo sobre el titular va a revisión humana. "
            "COMPROBANTES DE DOMICILIO (luz, agua, teléfono, predial, estado de cuenta): en México es normal que vengan a nombre "
            "de un tercero (padres, familiares, arrendador). Para ese tipo NO compares el titular: deja coincide_titular=null y "
            "fíjate únicamente en que el comprobante en sí sea legítimo (emisor real, domicilio completo, fecha de emisión reciente, "
            "formato del proveedor). "
            "No transcribas datos personales completos (nada de CURP, RFC ni domicilio íntegros) en las observaciones."
        ),
        input=[
            {
                "role": "user",
                "content": content_blocks,
            }
        ],
        text_format=DocumentoValidado,
    )
    return resp.output_parsed, True


# ============================================================
# Desempeño (andamiaje 2026-09-22) — «Crear evaluación con IA»
# ============================================================
#
# La IA PROPONE objetivos y KPIs para un puesto/periodo; RH los edita y decide (HITL). Sin OPENAI_API_KEY
# hay una propuesta demo determinista, igual que en vacantes y cursos. Nunca inventa metas numéricas de
# negocio que RH no dio: cuando no hay dato, la meta queda como texto guía para que RH la ajuste.


class ObjetivoDesempeno(BaseModel):
    titulo: str = Field(description="Objetivo claro y medible, en una línea.")
    descripcion: str = Field(default="", description="Qué se espera lograr y cómo se observa.")
    peso: int = Field(default=0, description="Peso en % dentro de la evaluación (la suma de objetivos + KPIs es 100).")


class KpiDesempeno(BaseModel):
    nombre: str = Field(description="Indicador medible (ej. «Tickets resueltos a tiempo»).")
    descripcion: str = Field(default="", description="Cómo se mide y de dónde sale el dato.")
    unidad: str = Field(default="", description="%, piezas, pesos, días…")
    meta: str = Field(default="", description="Meta esperada del periodo; vacía si RH no la definió.")
    peso: int = Field(default=0, description="Peso en % dentro de la evaluación.")


class PlanDesempeno(BaseModel):
    objetivos: List[ObjetivoDesempeno]
    kpis: List[KpiDesempeno]


def _plan_desempeno_demo(puesto: str, periodo: str) -> PlanDesempeno:
    p = puesto.strip() or "el puesto"
    return PlanDesempeno(
        objetivos=[
            ObjetivoDesempeno(titulo=f"Cumplir las responsabilidades clave de {p}", descripcion="Entregar el trabajo del periodo en tiempo y forma, con la calidad acordada.", peso=30),
            ObjetivoDesempeno(titulo="Colaboración y trabajo en equipo", descripcion="Apoya a su equipo, comparte información y participa en las juntas de seguimiento.", peso=20),
            ObjetivoDesempeno(titulo="Desarrollo y capacitación", descripcion="Completa la capacitación asignada y aplica lo aprendido en su operación.", peso=10),
        ],
        kpis=[
            KpiDesempeno(nombre="Cumplimiento de entregas", descripcion="Entregas completadas en la fecha comprometida.", unidad="%", meta="", peso=20),
            KpiDesempeno(nombre="Calidad del trabajo", descripcion="Retrabajos o errores detectados en el periodo.", unidad="%", meta="", peso=10),
            KpiDesempeno(nombre="Asistencia y puntualidad", descripcion="Días trabajados sin incidencias.", unidad="%", meta="", peso=10),
        ],
    )


def plan_desempeno(puesto: str, periodo: str = "", contexto: str = "") -> Tuple[PlanDesempeno, bool]:
    """Propuesta de objetivos y KPIs para un ciclo de desempeño. Regresa (plan, con_ia)."""
    client = _client()
    if client is None:
        return _plan_desempeno_demo(puesto, periodo), False
    try:
        resp = client.responses.parse(
            model=MODEL,
            instructions=(
                "Diseñas evaluaciones de desempeño para empresas en México (Red Human AI). A partir del puesto y el periodo "
                "propones 3 a 5 OBJETIVOS observables y 3 a 5 KPIs medibles, con pesos en % que sumen 100 entre todos. "
                "Reglas: (1) nada de datos sensibles (salud, embarazo, religión, estado civil, orientación) ni rasgos de "
                "personalidad: solo conducta y resultados de trabajo; (2) si no te dieron una meta numérica real, deja `meta` "
                "vacía — NUNCA inventes cifras de negocio; (3) lenguaje claro, español de México, sin anglicismos innecesarios; "
                "(4) la evaluación la hace una persona de RH o la jefatura: tú solo propones el marco."
            ),
            input=f"Puesto: {puesto or 'general'}\nPeriodo: {periodo or 'sin especificar'}\nContexto de RH: {contexto.strip()[:2000] or 'sin contexto adicional'}",
            text_format=PlanDesempeno,
        )
        plan = resp.output_parsed
        if not plan.objetivos and not plan.kpis:
            return _plan_desempeno_demo(puesto, periodo), False
        return plan, True
    except Exception as ex:  # noqa: BLE001 — la IA nunca bloquea: RH captura a mano
        print(f"[ia] plan de desempeño demo ({ex})", flush=True)
        return _plan_desempeno_demo(puesto, periodo), False


# ============================================================
# Base de Conocimiento — «Generar con Red Human» (2026-09-23)
# ============================================================


class BorradorConocimiento(BaseModel):
    titulo: str = Field(description="Título del documento, claro y corto.")
    texto: str = Field(description="Documento completo en texto plano con encabezados y viñetas simples.")
    avisos: List[str] = Field(default_factory=list, description="Datos que RH debe confirmar o llenar antes de publicar.")


def borrador_conocimiento(tema: str, tipo: str = "politica", notas: str = "", empresa: str = "") -> Tuple[BorradorConocimiento, bool]:
    """Redacta un BORRADOR de política/proceso/manual para la base de conocimiento. Nunca inventa cifras,
    montos ni plazos que RH no haya dado: los deja marcados como «[por definir]» y los lista en `avisos`.
    El documento SIEMPRE lo revisa y publica una persona (HITL). Regresa (borrador, con_ia)."""
    etiqueta = {"politica": "política interna", "proceso": "proceso de RH", "manual": "manual", "reglamento": "reglamento interno", "faq": "preguntas frecuentes"}.get(tipo, "documento interno")
    client = _client()
    if client is None:
        cuerpo = (
            f"# {tema.strip() or etiqueta.capitalize()}\n\n"
            f"## Objetivo\nDejar por escrito {etiqueta} de {empresa or 'la empresa'} para que cualquier colaborador sepa cómo aplica.\n\n"
            "## Alcance\nAplica a [por definir: áreas o puestos].\n\n"
            "## Lineamientos\n- [por definir]\n- [por definir]\n\n"
            "## Responsables\n- Recursos Humanos\n\n"
            f"## Vigencia\nA partir de [por definir].\n\n{notas.strip()}"
        )
        return BorradorConocimiento(titulo=tema.strip() or etiqueta.capitalize(), texto=cuerpo,
                                    avisos=["Sin OPENAI_API_KEY: este es un esqueleto. Completa lo marcado como «[por definir]» antes de publicar."]), False
    try:
        resp = client.responses.parse(
            model=MODEL,
            instructions=(
                f"Redactas documentos internos de Recursos Humanos para {empresa or 'una empresa'} en México (Red Human AI). "
                f"Escribe un BORRADOR de {etiqueta} claro, en español mexicano, con encabezados cortos y viñetas, listo para que RH lo "
                "revise. Reglas: (1) NUNCA inventes montos, días, plazos, porcentajes ni nombres: si falta un dato escribe «[por definir]» "
                "y enlístalo en `avisos`; (2) apégate a la Ley Federal del Trabajo y a la LFPDPPP cuando apliquen, sin citar artículos que "
                "no conozcas con certeza; (3) nada de datos sensibles ni de discriminación; (4) sin relleno ni frases de marketing."
            ),
            input=f"Tema: {tema}\nTipo: {etiqueta}\nNotas de RH: {notas.strip()[:3000] or 'sin notas'}",
            text_format=BorradorConocimiento,
        )
        b = resp.output_parsed
        if not b.texto.strip():
            raise ValueError("borrador vacío")
        return b, True
    except Exception as ex:  # noqa: BLE001 — la IA nunca bloquea: RH puede pegar el texto a mano
        print(f"[ia] borrador de conocimiento demo ({ex})", flush=True)
        return borrador_conocimiento(tema, tipo, notas, empresa) if False else (BorradorConocimiento(
            titulo=tema.strip() or "Documento interno", texto=notas.strip() or "[por definir]",
            avisos=[f"La IA no respondió ({str(ex)[:120]}). Captura el contenido a mano."]), False)


# ============================================================
# Clima laboral v2 (2026-09-27): encuesta con Red Human y análisis a demanda
# ============================================================


class PreguntaClimaIA(BaseModel):
    texto: str = Field(description="Pregunta o afirmación breve, en español de México, de tú a tú.")
    tipo: Literal["escala", "opcion", "abierta"] = Field(
        description="escala = afirmación que se califica de 1 (totalmente en desacuerdo) a 5 (totalmente de acuerdo); "
        "opcion = opción múltiple; abierta = comentario libre."
    )
    dimension: str = Field(description="Dimensión de clima a la que pertenece (debe ser una de `dimensiones`).")
    opciones: List[str] = Field(default_factory=list, description="SOLO para tipo=opcion: 3 a 5 opciones cortas.")


class EncuestaClimaIA(BaseModel):
    titulo: str = Field(description="Nombre corto de la encuesta.")
    descripcion: str = Field(description="1-2 frases para quien responde: para qué es y que toma pocos minutos.")
    dimensiones: List[str] = Field(description="3 a 6 dimensiones de clima, en el orden en que se presentan.")
    preguntas: List[PreguntaClimaIA] = Field(description="8 a 18 preguntas agrupadas por dimensión.")


_BASE_CLIMA_DEMO = [
    ("Liderazgo", "Mi jefe o jefa directa me da retroalimentación útil sobre mi trabajo."),
    ("Liderazgo", "Confío en las decisiones de mi jefe o jefa directa."),
    ("Comunicación", "Recibo a tiempo la información que necesito para hacer mi trabajo."),
    ("Comunicación", "Me siento con libertad de expresar mis ideas y opiniones."),
    ("Trabajo en equipo", "En mi equipo nos apoyamos cuando hay mucha carga de trabajo."),
    ("Trabajo en equipo", "Hay colaboración entre mi área y las demás áreas."),
    ("Reconocimiento y desarrollo", "Mi esfuerzo se reconoce cuando hago un buen trabajo."),
    ("Reconocimiento y desarrollo", "Tengo oportunidades para aprender y crecer aquí."),
    ("Bienestar", "Mi carga de trabajo me permite equilibrar mi vida personal."),
    ("Bienestar", "Recomendaría esta empresa como un buen lugar para trabajar."),
]


def _encuesta_clima_demo(prompt: str) -> EncuestaClimaIA:
    preguntas = [PreguntaClimaIA(texto=t, tipo="escala", dimension=d) for d, t in _BASE_CLIMA_DEMO]
    preguntas.append(PreguntaClimaIA(
        texto="¿Qué es lo que más valoras de trabajar aquí?", tipo="opcion", dimension="Bienestar",
        opciones=["El equipo", "El liderazgo", "El aprendizaje", "Las prestaciones", "La estabilidad"],
    ))
    preguntas.append(PreguntaClimaIA(texto="¿Qué cambiarías para trabajar mejor?", tipo="abierta", dimension="Bienestar"))
    dims = list(dict.fromkeys(p.dimension for p in preguntas))
    return EncuestaClimaIA(
        titulo="Encuesta de clima laboral",
        descripcion="Queremos saber cómo te sientes en tu trabajo. Toma unos 5 minutos.",
        dimensiones=dims, preguntas=preguntas,
    )


def encuesta_clima(prompt: str, empresa: str = "") -> Tuple[EncuestaClimaIA, bool]:
    """«¿Qué quieres saber de tu equipo?» → encuesta propuesta (nombre, dimensiones y preguntas). Es solo
    una PROPUESTA: RH la edita y la guarda. Nunca pide datos sensibles ni datos que identifiquen a quien
    responde. Regresa (encuesta, con_ia)."""
    client = _client()
    if client is None:
        return _encuesta_clima_demo(prompt), False
    try:
        resp = client.responses.parse(
            model=MODEL,
            instructions=(
                "Diseñas encuestas de CLIMA LABORAL para empresas en México (Red Human AI). A partir de lo que RH quiere "
                "saber de su equipo propones: un nombre corto, 3 a 6 dimensiones y 8 a 18 preguntas agrupadas por dimensión. "
                "Reglas: (1) prefiere tipo `escala` redactada como AFIRMACIÓN en primera persona que se califica de 1 a 5 "
                "(desacuerdo → acuerdo), en sentido positivo para que 4-5 sea favorable; (2) usa `opcion` solo cuando aporte "
                "(3 a 5 opciones) y 1 o 2 `abierta` al final; (3) cada pregunta mide UNA sola cosa; (4) NUNCA preguntes ni "
                f"insinúes datos sensibles ({DATOS_SENSIBLES_PROHIBIDOS}); (5) NUNCA pidas datos que identifiquen a la "
                "persona (nombre, puesto exacto, antigüedad exacta, jefe específico): la encuesta puede ser anónima; "
                "(6) español de México, claro y sin anglicismos; (7) cada `dimension` de una pregunta debe estar en `dimensiones`."
            ),
            input=f"Empresa: {empresa or 'no indicada'}\nLo que RH quiere saber de su equipo: {prompt.strip()[:2000]}",
            text_format=EncuestaClimaIA,
        )
        enc = resp.output_parsed
        if not enc or not enc.preguntas:
            return _encuesta_clima_demo(prompt), False
        return enc, True
    except Exception as ex:  # noqa: BLE001 — la IA nunca bloquea: RH puede usar plantilla o crear a mano
        print(f"[ia] encuesta de clima demo ({ex})", flush=True)
        return _encuesta_clima_demo(prompt), False


class AnalisisClimaIA(BaseModel):
    estado: Literal["Favorable", "En observación", "Requiere atención"] = Field(
        description="Diagnóstico general del clima según las métricas (no según suposiciones)."
    )
    resumen: str = Field(description="2-3 frases para RH con lo más importante, citando cifras que SÍ vienen en las métricas.")
    fortalezas: List[str] = Field(description="2 a 4 fortalezas con evidencia (dimensión/pregunta y su % favorable).")
    focos_atencion: List[str] = Field(description="2 a 4 focos de atención con evidencia (dimensión/pregunta y su %).")
    puntos_por_validar: List[str] = Field(
        description="Lo que los datos NO alcanzan a confirmar (poca participación, pocas respuestas, dimensiones sin resultado, comentarios aislados)."
    )
    acciones_sugeridas: List[str] = Field(description="3 a 5 acciones concretas y realistas para RH, ligadas a los focos.")


def _analisis_clima_demo(m: dict) -> AnalisisClimaIA:
    dims = [d for d in m.get("dimensiones", []) if d.get("favorable") is not None]
    fuertes = [d for d in dims if d["favorable"] >= 80]
    focos = [d for d in dims if d["favorable"] < 60]
    indice = (m.get("indice") or {}).get("valor")
    part = (m.get("participacion") or {}).get("porcentaje")
    estado = "En observación" if indice is None else ("Favorable" if indice >= 75 else "En observación" if indice >= 60 else "Requiere atención")
    validar = []
    if part is not None and part < 60:
        validar.append(f"Participación de {part}%: los resultados podrían no representar a todo el equipo.")
    if m.get("respuestasConsideradas", 0) < 5:
        validar.append(f"Solo {m.get('respuestasConsideradas', 0)} respuestas reales: tómalos como una primera lectura.")
    sin = [d["nombre"] for d in m.get("dimensiones", []) if d.get("favorable") is None]
    if sin:
        validar.append("Dimensiones sin resultado en escala: " + ", ".join(sin) + ".")
    return AnalisisClimaIA(
        estado=estado,
        resumen=(f"Índice de clima de {indice}% con {m.get('respuestasConsideradas', 0)} respuestas reales." if indice is not None
                 else "Aún no hay dimensiones con resultado suficiente para un índice de clima."),
        fortalezas=[f"{d['nombre']}: {d['favorable']}% favorable." for d in fuertes] or ["Sin dimensiones por arriba del 80% favorable todavía."],
        focos_atencion=[f"{d['nombre']}: {d['favorable']}% favorable." for d in focos] or ["Sin dimensiones por debajo del 60% favorable."],
        puntos_por_validar=validar or ["Sin puntos críticos por validar con los datos actuales."],
        acciones_sugeridas=[f"Conversar con los equipos sobre «{d['nombre']}» y acordar 1-2 acciones con fecha." for d in focos][:4]
        or ["Compartir los resultados con los equipos y reconocer lo que funciona."],
    )


def analisis_clima(metricas: dict, titulo: str = "", empresa: str = "") -> Tuple[AnalisisClimaIA, bool]:
    """«Analizar resultados con Red Human» (a demanda, nunca automático). Recibe SOLO las métricas
    agregadas del motor (`services.clima_resultados.calcular`) — sin identidades — y redacta el
    diagnóstico. Nunca inventa cifras ni intenta identificar a nadie por sus comentarios. Regresa
    (analisis, con_ia)."""
    client = _client()
    if client is None:
        return _analisis_clima_demo(metricas), False
    try:
        import json as _json

        resp = client.responses.parse(
            model=MODEL,
            instructions=(
                "Eres analista de CLIMA LABORAL de Red Human AI (México). Recibes las métricas agregadas de una encuesta: "
                "participación, índice de clima, % favorable (respuestas 4-5 de 5) por dimensión y por pregunta, "
                "distribuciones de opción múltiple y comentarios abiertos anónimos. Reglas: (1) usa SOLO las cifras que "
                "vienen en las métricas; nunca inventes datos ni porcentajes; (2) si la participación es baja o hay pocas "
                "respuestas, dilo en `puntos_por_validar`; (3) NUNCA intentes identificar a una persona por sus comentarios "
                "ni menciones datos sensibles; (4) acciones concretas, realistas y en español de México; (5) es una "
                "recomendación: las decisiones las toma una persona de RH."
            ),
            input=f"Encuesta: {titulo}\nEmpresa: {empresa}\nMétricas (JSON):\n{_json.dumps(metricas, ensure_ascii=False)[:30000]}",
            text_format=AnalisisClimaIA,
        )
        a = resp.output_parsed
        if not a:
            return _analisis_clima_demo(metricas), False
        return a, True
    except Exception as ex:  # noqa: BLE001 — la IA nunca bloquea
        print(f"[ia] análisis de clima demo ({ex})", flush=True)
        return _analisis_clima_demo(metricas), False


# ============================================================
# Desempeño v2 (2026-09-27): criterios con tipo (medible | descriptivo)
# ============================================================


class NivelEscalaIA(BaseModel):
    valor: int = Field(description="1 a 5")
    significado: str = Field(description="Qué conducta observable corresponde a este nivel, breve.")


class CriterioDesempenoIA(BaseModel):
    tipo: Literal["medible", "descriptivo"] = Field(
        description="medible = se cuenta con un número (unidad y sentido); descriptivo = se observa una conducta y se valora 1-5."
    )
    nombre: str = Field(description="Nombre corto y claro del criterio.")
    descripcion: str = Field(description="Qué mide y por qué importa para el puesto, 1 frase.")
    unidad: str = Field(default="", description="SOLO medible: unidad (%, proyectos, días, incidencias…).")
    sentido: Literal["mayor_es_mejor", "menor_es_mejor"] = Field(default="mayor_es_mejor", description="SOLO medible.")
    esperado: str = Field(default="", description="SOLO descriptivo: qué se espera observar en el día a día.")
    escala: List[NivelEscalaIA] = Field(default_factory=list, description="SOLO descriptivo: exactamente 5 niveles, del 1 al 5.")


class PropuestaCriteriosIA(BaseModel):
    criterios: List[CriterioDesempenoIA] = Field(description="4 a 7 criterios, mezclando medibles y descriptivos cuando tenga sentido.")


def _criterios_demo(puesto: str) -> PropuestaCriteriosIA:
    p = puesto.strip() or "el puesto"
    return PropuestaCriteriosIA(criterios=[
        CriterioDesempenoIA(tipo="medible", nombre="Cumplimiento de entregas", descripcion=f"Entregables de {p} terminados en la fecha comprometida.",
                            unidad="%", sentido="mayor_es_mejor"),
        CriterioDesempenoIA(tipo="medible", nombre="Retrabajos o errores", descripcion="Entregables que tuvieron que corregirse en el periodo.",
                            unidad="incidencias", sentido="menor_es_mejor"),
        CriterioDesempenoIA(tipo="descriptivo", nombre="Comunicación y seguimiento", descripcion="Mantiene informadas a las partes interesadas.",
                            esperado="Informa avances, riesgos y cambios a tiempo y por escrito."),
        CriterioDesempenoIA(tipo="descriptivo", nombre="Trabajo en equipo", descripcion="Colabora y comparte información con su equipo.",
                            esperado="Apoya a sus compañeros y cumple los acuerdos de equipo."),
    ])


def criterios_desempeno(puesto: str, periodo: str = "", contexto: str = "") -> Tuple[PropuestaCriteriosIA, bool]:
    """Propuesta de criterios para un PUESTO o EQUIPO (obligatorio: sin él no se propone nada). Nunca
    inventa metas numéricas: los medibles salen SIN meta y RH la captura. Regresa (propuesta, con_ia)."""
    client = _client()
    if client is None:
        return _criterios_demo(puesto), False
    try:
        resp = client.responses.parse(
            model=MODEL,
            instructions=(
                "Diseñas evaluaciones de desempeño para empresas en México (Red Human AI). Para el puesto o equipo indicado "
                "propones 4 a 7 CRITERIOS de dos tipos: `medible` (se cuenta: unidad y sentido del indicador) y `descriptivo` "
                "(se observa una conducta: qué se espera ver y una escala 1-5 donde cada nivel describe una conducta "
                "observable). Reglas: (1) NUNCA propongas metas ni cifras de negocio — el campo meta no existe: RH la "
                "captura; (2) nada de datos sensibles ni rasgos de personalidad, solo conducta y resultados de trabajo; "
                "(3) específicos para ESE puesto, no genéricos; (4) español de México, claro."
            ),
            input=f"Puesto o equipo: {puesto}\nPeriodo: {periodo or 'sin especificar'}\nContexto de RH: {contexto.strip()[:2000] or 'sin contexto adicional'}",
            text_format=PropuestaCriteriosIA,
        )
        prop = resp.output_parsed
        if not prop or not prop.criterios:
            return _criterios_demo(puesto), False
        return prop, True
    except Exception as ex:  # noqa: BLE001 — la IA nunca bloquea: RH captura a mano
        print(f"[ia] criterios de desempeño demo ({ex})", flush=True)
        return _criterios_demo(puesto), False


class BrechaIA(BaseModel):
    tema: str = Field(description="Tema corto de la brecha.")
    descripcion: str = Field(description="Qué falta, con la evidencia de los resultados capturados.")
    criterio: str = Field(default="", description="Nombre EXACTO del criterio del que sale la evidencia, si aplica.")


class ResumenDesempenoIA(BaseModel):
    resumen: str = Field(description="2-3 frases sobre el periodo, usando SOLO los resultados capturados.")
    fortalezas: List[str] = Field(default_factory=list, description="0 a 4 fortalezas con evidencia de los resultados.")
    brechas: List[BrechaIA] = Field(default_factory=list, description="0 a 4 brechas con evidencia; VACÍO si el desempeño es bueno.")


def _resumen_desempeno_demo(datos: dict) -> ResumenDesempenoIA:
    con_valor = [c for c in datos.get("criterios", []) if c.get("cumplimiento") is not None]
    altos = sorted(con_valor, key=lambda c: c["cumplimiento"], reverse=True)[:2]
    bajos = [c for c in con_valor if c["cumplimiento"] < 60]
    calif = datos.get("calificacion")
    return ResumenDesempenoIA(
        resumen=(f"Calificación de {calif}% con {len(con_valor)} criterio(s) evaluado(s)." if calif is not None
                 else "Aún no hay resultados válidos para resumir."),
        fortalezas=[f"{c['nombre']}: {c['cumplimiento']}% de cumplimiento." for c in altos if c["cumplimiento"] >= 75],
        brechas=[BrechaIA(tema=c["nombre"], descripcion=f"Cumplimiento de {c['cumplimiento']}% en «{c['nombre']}».", criterio=c["nombre"]) for c in bajos],
    )


def resumen_desempeno(datos: dict) -> Tuple[ResumenDesempenoIA, bool]:
    """Propuesta de resumen, fortalezas y brechas basada ESTRICTAMENTE en los resultados capturados (el
    evaluador la edita o confirma). Nunca inventa datos ni fuerza brechas: un buen periodo puede no tener
    ninguna. Regresa (propuesta, con_ia)."""
    client = _client()
    if client is None:
        return _resumen_desempeno_demo(datos), False
    try:
        import json as _json

        resp = client.responses.parse(
            model=MODEL,
            instructions=(
                "Eres analista de desempeño de Red Human AI (México). Recibes los criterios de UNA persona con su resultado "
                "capturado, el % de cumplimiento calculado, comentarios y notas de avance. Redacta: resumen, fortalezas y "
                "brechas. Reglas: (1) usa SOLO lo capturado; nunca inventes cifras, hechos ni criterios; (2) un criterio sin "
                "resultado o «No aplica» NO es fortaleza ni brecha; (3) si el desempeño es bueno, deja `brechas` vacío — no "
                "fuerces brechas; (4) nada de datos sensibles ni rasgos de personalidad; (5) es una propuesta: la confirma "
                "o edita el evaluador."
            ),
            input=_json.dumps(datos, ensure_ascii=False)[:20000],
            text_format=ResumenDesempenoIA,
        )
        prop = resp.output_parsed
        if not prop:
            return _resumen_desempeno_demo(datos), False
        return prop, True
    except Exception as ex:  # noqa: BLE001 — la IA nunca bloquea
        print(f"[ia] resumen de desempeño demo ({ex})", flush=True)
        return _resumen_desempeno_demo(datos), False


# ============================================================
# Prefiltro conversacional (2026-10-08): desempate de respuestas Sí / No
# ============================================================


class ClasificacionRespuesta(BaseModel):
    sentido: Literal["si", "no", "ambigua"] = Field(description="«si» si la respuesta afirma, «no» si niega, «ambigua» si no queda claro.")


# Retro 2026-10-09: el prefiltro NUNCA pregunta autopercepciones («¿te consideras organizado?»): no se pueden verificar.
_RE_AUTOPERCEPCION = re.compile(
    r"\b(te consideras|te describes|te defines|te percibes|te calificar\w*|c[oó]mo te ves|te sientes (una persona|capaz)|"
    r"eres (una )?persona|eres (muy )?(organizad|responsable|puntual|proactiv|trabajador|honest|comprometid|l[ií]der|creativ|"
    r"paciente|ordenad|din[aá]mic|amable|tolerante|sociable|respetuos|disciplinad|eficiente|atent))\w*",
    re.IGNORECASE)


def es_autopercepcion(texto: str) -> bool:
    return bool(_RE_AUTOPERCEPCION.search(texto or ""))


# Cambio 59 (2026-10-15): repreguntas de los DATOS base en lenguaje natural. Nunca se arma una repregunta con el nombre
# interno del campo («¿cuántos años de experiencia tienes en tiempo de traslado?» está prohibido).
PARAFRASIS_DATO = {
    "traslado": "¿Cuánto tardarías en llegar de tu casa al trabajo, más o menos?",
    "zona": "¿En qué colonia y municipio está tu casa?",
    "edad": "¿Cuántos años cumpliste en tu último cumpleaños?",
    "transporte": "¿En qué te moverías para llegar al trabajo: camión, metro, combi, auto?",
    "transportes": "¿Cuántos camiones, metros o combis tomarías para llegar?",
    "experiencia": "¿Cuánto tiempo llevas trabajando en algo parecido?",
    "escolaridad": "¿Cuál fue el último año que terminaste en la escuela?",
    "licencia": "¿Qué licencia de manejo tienes y de qué tipo es?",
    "vigencia": "¿Hasta qué mes y año está vigente?",
    "interes": "¿Te gustaría seguir con tu postulación para este puesto?",
    "puede_trasladarse": "¿Podrías llegar hasta el lugar de trabajo todos los días?",
    "disponibilidad": "¿Qué días y en qué horario podrías trabajar?",
    "terminal": "¿En cuál de las terminales te gustaría trabajar?",
    "sueldo": "Solo para tenerlo en cuenta, ¿el sueldo te parece bien?",
}
_RE_EXPERIENCIA = re.compile(r"\b(experiencia|anos|años|meses|tiempo trabajando)\b", re.IGNORECASE)


_DATO_POR_ETIQUETA = (("traslado", "traslado"), ("colonia", "zona"), ("municipio", "zona"), ("transporte", "transporte"),
                      ("edad", "edad"), ("sueldo", "sueldo"), ("interes en el puesto", "interes"), ("terminal", "terminal"))


def _dato_de_criterio(criterio: str) -> str:
    """Etiqueta interna de un dato base («Tiempo de traslado», «Colonia y municipio») → su clave de paráfrasis."""
    import unicodedata

    t = " ".join(unicodedata.normalize("NFKD", str(criterio or "")).encode("ascii", "ignore").decode().lower().split())
    return next((d for k, d in _DATO_POR_ETIQUETA if k in t), "") if len(t.split()) <= 5 else ""


def reformular_pregunta(pregunta: str, criterio: str, opciones: Optional[List[str]] = None, intento: int = 1,
                        tipo: str = "si_no", dato: str = "") -> str:
    """«No entiendo» / «¿qué procesos?»: la MISMA pregunta con OTRAS palabras (nunca el mismo texto: cambia en cada
    intento), sencilla y sin opciones numeradas (especificación 2026-10-10). Determinista; la IA solo la pule.
    Cambio 59: un DATO base (traslado, colonia, edad…) se refrasea con su paráfrasis natural (`PARAFRASIS_DATO`): el
    nombre interno del campo nunca llega al candidato."""
    dato = dato or _dato_de_criterio(criterio)
    if dato in PARAFRASIS_DATO:
        return f"Te lo pregunto de otra forma: {PARAFRASIS_DATO[dato]}"
    req = (criterio or pregunta).strip().rstrip(".?¿")
    req = req[:1].lower() + req[1:]
    if tipo == "numero":
        # Cambio 31: sin ejemplos de formato. Cambio 59: «experiencia» solo si el requisito habla de experiencia
        if not _RE_EXPERIENCIA.search(req):
            return (f"Te lo pregunto de otra forma: sobre {req}, ¿qué cantidad sería en tu caso, más o menos?" if intento <= 1
                    else f"En pocas palabras: ¿qué número me darías sobre {req}?")
        base = (f"Te lo pregunto de otra forma: ¿cuánto tiempo llevas con esto: {req}?"
                if intento <= 1 else f"En pocas palabras: ¿cuántos años o meses de experiencia tienes en {req}?")
    elif tipo == "opcion":
        base = (f"Te lo pregunto de otra forma: para esta vacante se necesita {req}. ¿Me cuentas con tus palabras si lo cumples?"
                if intento <= 1 else f"En pocas palabras: ¿cumples con {req}? Con un «sí» o un «no» me basta.")
    else:
        # Cambio 51 (2026-10-15): «No sé qué sea eso» → se REFRASEA (nunca se repite ni se pide «sí o no»: eso solo ante una
        # respuesta ambigua)
        base = (f"Te lo pregunto de otra forma: me refiero a esto: {req}. ¿Es tu caso?"
                if intento <= 1 else f"En pocas palabras: ¿tienes {req}?")
    client = _client()
    if client is None or tipo != "si_no":
        return base
    try:
        resp = client.responses.create(
            model=MODEL,
            instructions=("El candidato no entendió la pregunta del prefiltro («no sé qué sea eso»). REFRASÉALA: explica en "
                          "palabras sencillas a qué te refieres y vuelve a preguntar; UNA sola pregunta, español mexicano, máximo "
                          "30 palabras, sin autopercepciones, sin «Para confirmar», sin elogios, sin ejemplos de formato y sin "
                          "pedir que conteste «sí o no». Nunca repitas la pregunta original con las mismas palabras."),
            input=f"Pregunta original: {pregunta}\nRequisito: {criterio}",
        )
        texto = (getattr(resp, "output_text", "") or "").strip()
        return f"Te lo pregunto de otra forma: {texto}" if texto and "?" in texto else base
    except Exception:  # noqa: BLE001 — la versión determinista basta
        return base


def clasificar_respuesta_prefiltro(pregunta: str, respuesta: str) -> Optional[str]:
    """«si» / «no» / None. Solo se llama cuando el léxico determinista no pudo decidir; ante cualquier duda (o sin
    clave) regresa None y el bot pide aclaración. Nunca decide el prefiltro: solo interpreta la respuesta."""
    client = _client()
    if client is None:
        return None
    try:
        resp = client.responses.parse(
            model=MODEL,
            instructions=("Interpretas UNA respuesta de un candidato a una pregunta cerrada de prefiltro (Sí/No). Es lenguaje natural "
                          "libre y coloquial de México («ya la acabé», «claro», «simón» = sí). IGNORA el tono: si hay groserías "
                          "pero responde la pregunta, la respuesta vale. Regresa «si» o «no» SOLO si la respuesta lo dice con "
                          "claridad; si es condicional, parcial o no responde la pregunta, «ambigua»."),
            input=f"Pregunta: {pregunta[:300]}\nRespuesta del candidato: {respuesta[:300]}",
            text_format=ClasificacionRespuesta,
        )
        sentido = resp.output_parsed.sentido
        return None if sentido == "ambigua" else sentido
    except Exception as ex:  # noqa: BLE001 — sin IA se pide aclaración
        print(f"[ia] clasificación de respuesta no disponible ({ex})", flush=True)
        return None


# ============================================================
# Generación de contenido según la ruta (2026-10-09) — plantillas de conversación por vacante
# ============================================================
#
# Reglas NO negociables (garantizadas en código, no solo en el prompt):
#   * Prefiltros (Web y WhatsApp): SOLO preguntas cerradas (Sí/No, número con rangos u opción); eliminatorias permitidas.
#   * Entrevistas y llamadas: SOLO preguntas abiertas y guion; nunca una pregunta de prefiltro.
#   * Reconfirmación: con Prefiltro · Web Y Prefiltro · WhatsApp en la ruta, cada INDISPENSABLE del web se reconfirma en
#     WhatsApp pidiendo un DATO CONCRETO (web: «¿tienes experiencia?» → WhatsApp: «¿cuánto tiempo?»). Lo no indispensable
#     no se repite.
#   * Solo se genera lo que está en la ruta de la vacante.


class PreguntaPrefiltroIA(BaseModel):
    pregunta: str = Field(description="Pregunta CERRADA en español mexicano.")
    tipo: Literal["si_no", "numero", "opcion"] = Field(description="si_no = Sí/No; numero = rangos; opcion = una de una lista.")
    valida: str = Field(description="Requisito que valida (texto del requisito).")
    respuesta_esperada: str = Field(description="Respuesta que cumple (ej. 'Sí', '>= 2 años', la opción válida).")
    descarta: bool = Field(description="true solo si el requisito es INDISPENSABLE (eliminatoria).")
    opciones: List[str] = Field(description="numero/opcion: 3 a 5 opciones ordenadas; si_no: vacío.")
    reconfirma: str = Field(description="Solo en WhatsApp: el requisito indispensable (texto EXACTO) del web que esta pregunta "
                                        "reconfirma pidiendo un dato concreto; vacío si no reconfirma nada.")
    regla: str = Field(description="Cambio 19: «Descarta si …» (la condición exacta que descarta) para un INDISPENSABLE; "
                                   "«Se registra» para una condición de la descripción.")


class GuionesIA(BaseModel):
    prefiltro_web: List[PreguntaPrefiltroIA] = Field(default_factory=list)
    prefiltro_whatsapp: List[PreguntaPrefiltroIA] = Field(default_factory=list)
    entrevista_whatsapp: Optional[GuionEntrevista] = None
    entrevista_avatar: Optional[GuionEntrevista] = None
    llamada: Optional[GuionEntrevista] = None


class FichaGuion(BaseModel):
    titulo: str
    responsabilidades: List[str] = Field(default_factory=list)
    requisitos_indispensables: List[str] = Field(default_factory=list)
    requisitos_deseables: List[str] = Field(default_factory=list)
    ubicacion: str = ""
    modalidad: str = ""
    horario: str = ""
    sueldo: str = ""
    empresa: str = ""
    enfoque_entrevista: str = "profesional"
    # 2026-10-10: perfil del oficio (copia de la Biblioteca de Perfiles ajustada por vacante) para el enfoque operativo
    perfil_operativo: Optional[dict] = None
    # Cambio 29: la ruta es Masivos (operativos) → el prefiltro de WhatsApp es la base FIJA (`prefiltro_operativo`)
    ruta_masiva: bool = False
    # Cambio 31 (Bloque 4): la ruta es corporativa (trae Análisis de CV) → el traslado solo se pregunta si es indispensable
    ruta_corporativa: bool = False
    # Cambio 19 (2026-10-12): la descripción de la vacante — sus condiciones (p. ej. escolaridad) se REGISTRAN en el prefiltro
    descripcion: str = ""
    # Cambio 43: enfoque POR SECCIÓN de guion ({entrevista_whatsapp|entrevista_avatar|llamada: enfoque}), del «Enfoque» de
    # cada actividad de la ruta. Lo que no venga usa `enfoque_entrevista`.
    enfoques: Dict[str, str] = Field(default_factory=dict)


ESTILO_GUION = {
    "entrevista_whatsapp": "Entrevista Red Human por WhatsApp (texto en el chat): mensajes CORTOS, una pregunta por mensaje, "
                           "tono cálido de chat; 5 a 6 preguntas.",
    "entrevista_avatar": "Entrevista Red Human con avatar (video, se dice en voz alta): frases naturales y breves; 5 a 7 preguntas.",
    "llamada": "Llamada Red Human (conversación por VOZ conducida por la IA): frases muy cortas, fáciles de escuchar, sin listas "
               "ni enumeraciones; 4 a 6 preguntas.",
}

_RE_ANOS = re.compile(r"(\d+(?:[.,]\d+)?)\s*(años|año|anos|ano|meses|mes)\b", re.IGNORECASE)


def _norm_req(t: str) -> str:
    return _clave_texto(t).strip(" .")


def _rangos(n: float) -> Tuple[List[str], List[str]]:
    """Rangos de años alrededor del mínimo pedido y cuáles cumplen."""
    n = max(1, int(round(n)))
    if n == 1:
        ops = ["Menos de 6 meses", "6 meses a 1 año", "1 a 3 años", "Más de 3 años"]
        return ops, ops[2:]
    ops = ["Menos de 1 año", "1 año" if n == 2 else f"1 a {n - 1} años", f"{n} a {n + 2} años", f"Más de {n + 2} años"]
    return ops, ops[2:]


def _tema_experiencia(req: str) -> str:
    t = _RE_ANOS.sub("", req).strip(" .")
    t = re.sub(r"^\s*(de|con)\s+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\s*experiencia\s+(de|en|como|con)?\s*", "", t, flags=re.IGNORECASE).strip(" .")
    return t or "un puesto similar"


def _de_experiencia(tema: str) -> str:
    """«de experiencia operando montacargas» / «de experiencia en almacén»."""
    primera = (tema.split() or [""])[0].lower()
    return f"de experiencia {tema}" if primera.endswith(("ando", "endo", "iendo")) else f"de experiencia en {tema}"


def _minuscula_inicial(t: str) -> str:
    t = t.strip().rstrip(".")
    return t[0].lower() + t[1:] if len(t) > 1 and not t[:2].isupper() else t


def pregunta_web_cerrada(req: str, descarta: bool = True) -> dict:
    """Prefiltro web (y WhatsApp sin web): cumplimiento del requisito, Sí/No."""
    m = _RE_ANOS.search(req)
    if m:
        n = m.group(1).replace(",", ".")
        unidad = m.group(2).lower().replace("ano", "año")
        q = f"¿Tienes al menos {n} {unidad} {_de_experiencia(_tema_experiencia(req))}?"
    else:
        q = f"¿Cumples con este requisito: {_minuscula_inicial(req)}?"
    return {"pregunta": q, "tipo": "si_no", "valida": req.strip(), "respuesta_esperada": "Sí", "descarta": descarta,
            "opciones": list(OPCIONES_CERRADAS)}


def pregunta_reconfirmacion(req: str) -> dict:
    """WhatsApp reconfirma un indispensable del web pidiendo un DATO CONCRETO, pero cerrado (rangos u opciones)."""
    m = _RE_ANOS.search(req)
    if m or "experiencia" in _clave_texto(req):
        n = float(m.group(1).replace(",", ".")) if m else 1.0
        if m and m.group(2).lower().startswith("mes"):
            n = max(1.0, n / 12)
        ops, validas = _rangos(n)
        return {"pregunta": f"Para confirmar: ¿cuánto tiempo {_de_experiencia(_tema_experiencia(req))} tienes?", "tipo": "numero",
                "valida": req.strip(), "respuesta_esperada": f">= {int(n) if n == int(n) else n} años", "descarta": False,
                "opciones": ops, "reconfirma": req.strip(), "minimo": n, "opciones_validas": validas}
    # 2026-10-11: sí/no en lenguaje natural — nunca «¿cuál de estas opciones te describe?»
    q = pregunta_si_no_natural(req)
    return {"pregunta": f"Para confirmar, {q[0] + q[1].lower() + q[2:]}", "tipo": "si_no", "valida": req.strip(),
            "respuesta_esperada": "Sí", "descarta": False, "opciones": ["Sí", "No"], "reconfirma": req.strip(), "opciones_validas": ["Sí"]}


_VERBOS_TU = {"saber": "sabes", "conocer": "conoces", "manejar": "manejas", "tener": "tienes", "vivir": "vives", "poder": "puedes",
              "hablar": "hablas", "leer": "lees", "escribir": "escribes", "operar": "operas", "contar con": "cuentas con"}
_RE_NIVEL = re.compile(r"^(?:la |el )?(primaria|secundaria|preparatoria|prepa|bachillerato|licenciatura|carrera t[eé]cnica)\s+"
                       r"(?:terminad[ao]|concluid[ao]|complet[ao]|acabad[ao])", re.IGNORECASE)
_RE_FRASE_CERRADA = re.compile(r"^\s*¿\s*(cumples con(?: este requisito)?|cuentas con(?: este requisito)?)\s*:\s*(.+?)\s*\?\s*$", re.IGNORECASE)
_RE_CUAL_OPCION = re.compile(r"cu[aá]l de estas opciones", re.IGNORECASE)


def pregunta_si_no_natural(req: str) -> str:
    """Un requisito como pregunta de sí/no en lenguaje natural: «Secundaria terminada» → «¿Terminaste la secundaria?»,
    «Disponibilidad para rolar turnos» → «¿Tienes disponibilidad para rolar turnos?», «Saber leer y escribir» → «¿Sabes
    leer y escribir?». Lo demás → «¿Cuentas con …?»."""
    r = " ".join(str(req or "").split()).strip(" .")
    if not r:
        return "¿Cumples con este requisito?"
    m = _RE_NIVEL.match(r)
    if m:
        nivel = m.group(1).lower()
        return f"¿Terminaste {'el' if nivel == 'bachillerato' else 'la'} {nivel}?"
    bajo = r[0].lower() + r[1:] if len(r) > 1 and not r[:2].isupper() else r
    for inf, tu in sorted(_VERBOS_TU.items(), key=lambda x: -len(x[0])):
        if bajo.lower().startswith(inf + " "):
            return f"¿{tu[0].upper() + tu[1:]} {bajo[len(inf) + 1:]}?"
    m = re.match(r"^disponibilidad (?:para|de) (.+)$", bajo, re.IGNORECASE)
    if m:
        return f"¿Tienes disponibilidad para {m.group(1)}?"
    if re.match(r"^(licencia|constancia|certificaci[oó]n|curso|conocimient)", bajo, re.IGNORECASE):
        return f"¿Tienes {bajo}?"
    if re.fullmatch(r"[a-záéíóúñ]+(ar|er|ir)(se)?", (bajo.split() or [""])[0].lower()):
        return f"¿Puedes {bajo}?"  # «Cargar cajas de 25 kg» → «¿Puedes cargar cajas de 25 kg?»
    return f"¿Cuentas con {bajo}?"


def chat_natural(pregunta: str, criterio: str = "") -> str:
    """Texto que ve el candidato en el chat: las preguntas armadas como «¿Cumples con este requisito: X?» o «¿cuál de
    estas opciones te describe?» (vacantes guardadas antes del 2026-10-11) se dicen como sí/no en lenguaje natural."""
    t = " ".join(str(pregunta or "").split())
    m = _RE_FRASE_CERRADA.match(t)
    if m:
        return pregunta_si_no_natural(m.group(2))
    if _RE_CUAL_OPCION.search(t):
        q = pregunta_si_no_natural(criterio or re.sub(r"^.*?«(.+?)».*$", r"\1", t))
        return f"Para confirmar, {q[0] + q[1].lower() + q[2:]}"
    return t


def _pregunta_dict(p: "PreguntaPrefiltroIA") -> dict:
    d = p.model_dump()
    if not d.get("reconfirma"):
        d.pop("reconfirma", None)
    if not d.get("regla"):
        d.pop("regla", None)
    if d["tipo"] == "si_no":
        d["opciones"] = list(OPCIONES_CERRADAS)
    return d


_RE_EXIGENCIA = re.compile(r"\b(carg\w*|levant\w*|de pie|turno\w*|rolar|nocturn\w*|fines? de semana|domingos?|altura\w*|"
                           r"esfuerzo|intemperie|fr[ií]o|calor|maneja\w*|kg|kilos?)\b", re.IGNORECASE)
_RE_HORARIO = re.compile(r"\b(horario|turno\w*|rolar|nocturn\w*|fines? de semana|lunes a|de \d{1,2}(:\d\d)? ?(a\.? ?m\.?|p\.? ?m\.?)?"
                         r" a \d)", re.IGNORECASE)
# Cambio 31: la escolaridad se pregunta SIEMPRE así (sin variantes)
PREGUNTA_ESCOLARIDAD = "¿Cuál es tu último grado de estudios?"
RE_NIVEL_ESTUDIOS = re.compile(r"\b(primaria|secundaria|preparatoria|prepa|bachillerato|licenciatura|maestr[ií]a|posgrado|postgrado|"
                               r"doctorado|escolaridad)\b", re.IGNORECASE)


def _pregunta_exigencia(texto: str) -> str:
    """Una condición del trabajo como pregunta de Sí/No: «Cargar cajas de 25 kg» → «¿Puedes cargar cajas de 25 kg?»."""
    t = _minuscula_inicial(" ".join(str(texto or "").split()))
    primera = (t.split() or [""])[0]
    if re.fullmatch(r"[a-záéíóúñ]+(ar|er|ir)(se)?", primera):
        return f"¿Puedes {t}?"
    return pregunta_si_no_natural(texto)


def preguntas_clave_extra(ficha: FichaGuion, ya: List[dict], faltan: int) -> List[dict]:
    """2026-10-11 (tarde): el prefiltro web lleva al menos 3 preguntas clave. Si los indispensables no alcanzan, se
    completan con lo que RH capturó: escolaridad (deseable), exigencias del trabajo (responsabilidades con esfuerzo físico,
    turnos…), ubicación y horario. Nunca son eliminatorias (las eliminatorias son solo los indispensables de RH)."""
    vistos = {_norm_req(q.get("valida") or "") for q in ya} | {_clave_texto(q.get("pregunta") or "") for q in ya}
    candidatos: List[dict] = []

    def agregar(pregunta: str, valida: str, **extra) -> None:
        if _norm_req(valida) in vistos or _clave_texto(pregunta) in vistos:
            return
        vistos.add(_norm_req(valida))
        candidatos.append({"pregunta": pregunta, "tipo": "si_no", "valida": valida.strip(), "respuesta_esperada": "Sí",
                           "descarta": False, "opciones": list(OPCIONES_CERRADAS), **extra})

    for r in ficha.requisitos_deseables:
        if RE_NIVEL_ESTUDIOS.search(r or ""):
            agregar(pregunta_si_no_natural(r), r)
    textos = list(ficha.responsabilidades) + list(ficha.requisitos_deseables)
    for r in textos:
        if r and _RE_EXIGENCIA.search(r) and not RE_NIVEL_ESTUDIOS.search(r) and len(r.split()) <= 14:
            agregar(_pregunta_exigencia(r), r)
    mod = (ficha.modalidad or "").strip()
    if mod and not mod.lower().startswith("presencial"):  # C1 (prueba del 11 de octubre): la modalidad también es clave
        agregar(f"¿Puedes trabajar en modalidad {mod.lower()}?", f"Modalidad: {mod}", clave="modalidad")
    if (ficha.ubicacion or "").strip() and not (ficha.modalidad or "").lower().startswith("remot"):
        agregar(f"¿Puedes trasladarte a {ficha.ubicacion.strip()}?", f"Trasladarse a {ficha.ubicacion.strip()}",
                clave="ubicacion", solo_con_ubicacion=True)
    if (ficha.horario or "").strip():
        agregar(f"¿Puedes trabajar en el horario {ficha.horario.strip()}?", f"Horario: {ficha.horario.strip()}", clave="horario")
    else:
        for r in textos:
            if r and _RE_HORARIO.search(r) and len(r.split()) <= 14:
                agregar(_pregunta_exigencia(r), r, clave="horario")
                break
    return candidatos[:max(0, faltan)]


# Cambio 19 (2026-10-12) — CERO REGLAS FIJAS: el prefiltro de WhatsApp sale de los atributos de CADA vacante.
#   1) cada requisito INDISPENSABLE → pregunta cerrada con la regla «Descarta si …» (la primera pregunta del chat siempre
#      es un indispensable, si la vacante tiene);
#   2) cada condición de la DESCRIPCIÓN (deseables, escolaridad mencionada en la descripción) → «Se registra» (nunca
#      descarta; si no la cumple, va a Puntos por validar);
#   3) SIEMPRE: colonia/municipio, tiempo de traslado, transporte y aceptación del sueldo («El sueldo es desde… ¿Te
#      parece bien?», solo si hay sueldo capturado) → «Se registra».
# La escolaridad solo se pregunta si la vacante la menciona (indispensable → descarta; descripción → se registra).
DATOS_PREFILTRO = ("escolaridad", "zona", "traslado", "transporte", "sueldo")
DATOS_SIEMPRE = ("zona", "traslado", "transporte", "sueldo")
REGLA_REGISTRA = "Se registra"
MAX_CONDICIONES_REGISTRO = 4
MAX_REGENERACIONES_PREFILTRO = 2
_RE_NIVEL_DESC = re.compile(r"\b(primaria|secundaria|preparatoria|prepa|bachillerato|licenciatura|carrera t[eé]cnica)"
                            r"(\s+(terminad[ao]|concluid[ao]|complet[ao]|trunca))?", re.IGNORECASE)


def regla_descarta(req: str) -> str:
    """«Descarta si no cumple: secundaria terminada» — la regla sale del texto del indispensable de la vacante."""
    return f"Descarta si no cumple: {_minuscula_inicial(req)}"


def pregunta_sueldo(sueldo: str) -> str:
    """«Desde $9,000 MXN mensuales» → «El sueldo es desde $9,000 MXN mensuales. ¿Te parece bien?»."""
    t = " ".join(str(sueldo or "").split()).strip().rstrip(".")
    if not t or t.lower() in ("a convenir", "por definir"):
        return ""
    if t.lower().startswith("desde "):
        cuerpo = "desde " + t[6:]
    elif "–" in t or " - " in t:
        a, b = [x.strip() for x in re.split(r"\s*[–-]\s+|\s+[–-]\s*", t, maxsplit=1)]
        cuerpo = f"de {a} a {b}"
    else:
        cuerpo = f"de {t}"
    return f"El sueldo es {cuerpo}. ¿Te parece bien?"


def condiciones_descripcion(ficha: FichaGuion) -> List[str]:
    """Condiciones descriptivas (NO eliminatorias) de la vacante: los requisitos deseables y la escolaridad que mencione la
    descripción. Nunca autopercepciones ni lo que ya es indispensable."""
    indisp = {_norm_req(r) for r in ficha.requisitos_indispensables if r.strip()}
    salida = [r.strip() for r in ficha.requisitos_deseables if r.strip() and not es_autopercepcion(r) and _norm_req(r) not in indisp]
    if not any(RE_NIVEL_ESTUDIOS.search(r) for r in salida + list(ficha.requisitos_indispensables)):
        m = _RE_NIVEL_DESC.search(ficha.descripcion or "")
        if m:
            nivel = m.group(0).strip()
            salida.insert(0, nivel[0].upper() + nivel[1:])
    vistos, unicas = set(), []
    for r in salida:
        if _norm_req(r) not in vistos:
            vistos.add(_norm_req(r))
            unicas.append(r)
    return unicas[:MAX_CONDICIONES_REGISTRO]


def pregunta_escolaridad(nivel: str, descarta: bool) -> dict:
    """«¿Cuál es tu último grado de estudios?» (Cambio 31): descarta si el nivel es INDISPENSABLE; si viene de la
    descripción, se registra."""
    return {"pregunta": PREGUNTA_ESCOLARIDAD, "tipo": "dato", "dato": "escolaridad", "valida": nivel,
            "respuesta_esperada": nivel, "descarta": descarta, "regla": regla_descarta(nivel) if descarta else REGLA_REGISTRA,
            "opciones": []}


def requisito_traslado(indispensables: List[str]) -> str:
    """El indispensable de traslado de la vacante («Vivir a máximo 40 minutos de la planta»), o "" si no hay."""
    from .prefiltro_operativo import _RE_TRASLADO, _n

    return next((r for r in indispensables or [] if str(r or "").strip() and _RE_TRASLADO.search(_n(r))), "")


def pregunta_traslado_aplica(corporativa: bool, indispensables: List[str]) -> bool:
    """Cambio 31 (Bloque 4): en una ruta corporativa la pregunta «¿Cuánto tiempo haces de tu casa a…?» SOLO existe si la
    vacante tiene capturado un requisito INDISPENSABLE de traslado. Las demás rutas conservan su regla."""
    return not corporativa or bool(requisito_traslado(indispensables))


def preguntas_datos_siempre(ficha: FichaGuion) -> List[dict]:
    """Los datos que el prefiltro de WhatsApp pregunta SIEMPRE (solo se registran): colonia, traslado, transporte y
    aceptación del sueldo (este último solo con sueldo capturado: Red Human nunca inventa condiciones). En una ruta
    corporativa el traslado solo va si la vacante lo pide como indispensable (Cambio 31)."""
    lugar = (ficha.ubicacion or "").strip()
    traslado = f"¿Cuánto tiempo haces de tu casa a {lugar}?" if lugar and len(lugar.split()) <= 8 else "¿Cuánto tiempo harías de tu casa al trabajo?"
    salida = [
        {"pregunta": "¿En qué colonia y municipio vives?", "tipo": "dato", "dato": "zona", "valida": "Colonia y municipio"},
        {"pregunta": traslado, "tipo": "dato", "dato": "traslado", "valida": "Tiempo de traslado"},
        {"pregunta": "¿En qué transporte llegarías?", "tipo": "dato", "dato": "transporte", "valida": "Medio de transporte"},
    ]
    if not pregunta_traslado_aplica(ficha.ruta_corporativa, ficha.requisitos_indispensables):
        salida = [q for q in salida if q["dato"] != "traslado"]
    if pregunta_sueldo(ficha.sueldo):
        salida.append({"pregunta": pregunta_sueldo(ficha.sueldo), "tipo": "dato", "dato": "sueldo", "valida": f"Sueldo: {ficha.sueldo.strip()}",
                       "respuesta_esperada": "Sí"})
    return [{"respuesta_esperada": "", **q, "descarta": False, "regla": REGLA_REGISTRA, "opciones": []} for q in salida]


def preguntas_datos_whatsapp(ficha: FichaGuion) -> List[dict]:
    """Compatibilidad: escolaridad (solo si la vacante la menciona) + los datos de siempre."""
    nivel = next((r for r in ficha.requisitos_indispensables if r.strip() and RE_NIVEL_ESTUDIOS.search(r)), "")
    desc = next((r for r in condiciones_descripcion(ficha) if RE_NIVEL_ESTUDIOS.search(r)), "")
    esc = [pregunta_escolaridad(nivel, True)] if nivel else [pregunta_escolaridad(desc, False)] if desc else []
    return esc + preguntas_datos_siempre(ficha)


def _une_dos_ideas(texto: str) -> bool:
    from .entrevista_operativa import une_dos_ideas

    return une_dos_ideas(texto)


def preguntas_registro(ficha: FichaGuion, propuestas: Optional[List[dict]] = None) -> List[dict]:
    """«Se registra»: una pregunta cerrada (Sí/No, lenguaje natural) por condición de la descripción. Si la IA propuso una
    redacción válida para esa condición se usa la suya."""
    por_req = {_norm_req(q.get("valida") or ""): q for q in propuestas or []
               if not q.get("descarta") and q.get("tipo") == "si_no" and not es_autopercepcion(q.get("pregunta", ""))
               and not _une_dos_ideas(q.get("pregunta", "")) and "[" not in q.get("pregunta", "")}
    salida = []
    for cond in condiciones_descripcion(ficha):
        if RE_NIVEL_ESTUDIOS.search(cond):
            salida.append(pregunta_escolaridad(cond, False))
            continue
        propia = por_req.get(_norm_req(cond))
        salida.append({"pregunta": (propia or {}).get("pregunta") or _pregunta_exigencia(cond), "tipo": "si_no", "valida": cond,
                       "respuesta_esperada": "Sí", "descarta": False, "registra": True, "regla": REGLA_REGISTRA,
                       "opciones": list(OPCIONES_CERRADAS)})
    return salida


def revisar_prefiltro_whatsapp(preguntas: List[dict], indispensables: List[str]) -> List[str]:
    """Validación del prefiltro de WhatsApp (Cambio 19): TODOS los indispensables de la vacante tienen su pregunta con regla
    «Descarta si…»; ninguna pregunta une dos ideas con «y» ni lleva marcas internas visibles. Vacía = aprobado."""
    problemas: List[str] = []
    cubiertos = {_norm_req(q.get("reconfirma") or "") for q in preguntas or [] if q.get("reconfirma")}
    cubiertos |= {_norm_req(q.get("valida") or "") for q in preguntas or [] if q.get("descarta")}
    escolaridad = any(q.get("dato") == "escolaridad" and q.get("descarta") for q in preguntas or [])
    for r in [r for r in indispensables or [] if str(r or "").strip()]:
        if _norm_req(r) in cubiertos or (escolaridad and RE_NIVEL_ESTUDIOS.search(r)):
            continue
        problemas.append(f"Falta la pregunta del indispensable «{r.strip()}» (regla «Descarta si…»).")
    for q in preguntas or []:
        t = str(q.get("pregunta") or "")
        if _une_dos_ideas(t):
            problemas.append(f"«{t[:70]}»: une dos ideas con «y».")
        if "[" in t or "]" in t:
            problemas.append(f"«{t[:70]}»: lleva una marca interna visible.")
    return problemas


def asegurar_prefiltros(web: List[dict], whatsapp: List[dict], ficha: FichaGuion, hay_web: bool,
                        hay_whatsapp: bool) -> Tuple[List[dict], List[dict]]:
    """Reglas de prefiltro garantizadas en código (IA o demo):
    * Web: cerradas Sí/No, primero cada indispensable (eliminatoria) y después lo deseable (solo suma).
    * WhatsApp (Cambio 19): 1) indispensables con «Descarta si…» (con web en la ruta, reconfirmación con dato concreto
      de cada uno; sin web, la pregunta principal) → 2) condiciones de la descripción con «Se registra» → 3) colonia,
      traslado, transporte y sueldo («Se registra»)."""
    indisp = [r for r in ficha.requisitos_indispensables if r.strip()]
    # retro 2026-10-09: una autopercepción («¿te consideras organizado?») nunca entra al prefiltro
    web = [q for q in web or [] if not es_autopercepcion(q.get("pregunta", ""))]
    whatsapp = [q for q in whatsapp or [] if not es_autopercepcion(q.get("pregunta", ""))]
    salida_web: List[dict] = []
    if hay_web:
        if web and not any(q.get("reconfirma") for q in web) and all(q.get("tipo") == "si_no" for q in web) and not indisp:
            salida_web = [dict(q) for q in web]
        else:
            cerradas = cerrar_preguntas_web([
                PreguntaFiltro(pregunta=q["pregunta"], tipo=q.get("tipo") if q.get("tipo") in ("si_no", "numero", "opcion", "texto_corto") else "si_no",
                               valida=q.get("valida") or q["pregunta"], respuesta_esperada=q.get("respuesta_esperada") or "Sí",
                               descarta=bool(q.get("descarta")))
                for q in web if q.get("pregunta")])
            por_req = {_norm_req(q.valida): q.model_dump() for q in cerradas}
            for r in indisp:  # cada indispensable tiene su eliminatoria
                q = por_req.pop(_norm_req(r), None) or pregunta_web_cerrada(r)
                q["descarta"] = True
                salida_web.append(q)
            for q in por_req.values():
                q["descarta"] = False  # la IA no decide eliminatorias que RH no marcó como indispensables
                salida_web.append(q)
        if not salida_web:
            salida_web = [pregunta_web_cerrada(r, False) for r in ficha.requisitos_deseables[:3] if r.strip()]
        for q in salida_web:
            if q.get("tipo") == "si_no":
                q["opciones"] = list(OPCIONES_CERRADAS)
        if len(salida_web) < MIN_PREFILTRO_WEB:  # mínimo 3 preguntas clave (si lo capturado alcanza)
            salida_web += preguntas_clave_extra(ficha, salida_web, MIN_PREFILTRO_WEB - len(salida_web))
    salida_wa: List[dict] = []
    if hay_whatsapp and ficha.ruta_masiva:
        # Cambio 29: rutas Masivos (operativos) — base FIJA del prefiltro de WhatsApp (sin IA)
        from . import prefiltro_operativo as pop

        return salida_web, pop.preguntas_guardables(ficha)
    if hay_whatsapp:
        nivel = next((r for r in indisp if RE_NIVEL_ESTUDIOS.search(r)), "")
        # 1) indispensables («Descarta si…»): la escolaridad exacta reconfirma el nivel indispensable
        if nivel:
            salida_wa.append(pregunta_escolaridad(nivel, True))
        if hay_web:
            reconf = {_norm_req(q.get("reconfirma", "")): q for q in whatsapp
                      if q.get("reconfirma") and q.get("tipo") in ("numero", "opcion", "si_no") and len(q.get("opciones") or []) >= 2
                      and not _RE_CUAL_OPCION.search(q.get("pregunta") or "") and not _une_dos_ideas(q.get("pregunta") or "")}
            for q in [x for x in salida_web if x.get("descarta")]:
                if nivel and _norm_req(q["valida"]) == _norm_req(nivel):
                    continue  # la escolaridad exacta («¿Cuál es tu último grado de estudios?») ya reconfirma el nivel de estudios
                det = pregunta_reconfirmacion(q["valida"])
                propia = reconf.get(_norm_req(q["valida"]))
                if propia and propia["tipo"] == det["tipo"] and propia.get("opciones") == det["opciones"]:
                    elegida = {**propia, "reconfirma": q["valida"], "opciones_validas": det["opciones_validas"],
                               **({"minimo": det["minimo"]} if "minimo" in det else {})}
                elif propia and propia.get("opciones_validas"):
                    elegida = {**propia, "reconfirma": q["valida"]}
                else:
                    elegida = det  # la regla de reconfirmación nunca depende de que la IA la cumpla
                salida_wa.append({**elegida, "descarta": True, "regla": regla_descarta(q["valida"])})
        else:
            cerradas = [dict(q) for q in whatsapp if q.get("tipo") in ("si_no", "numero", "opcion") and not q.get("reconfirma")
                        and q.get("descarta") and not _une_dos_ideas(q.get("pregunta") or "")]
            por_req = {_norm_req(q.get("valida", "")): q for q in cerradas}
            for r in indisp:
                if nivel and _norm_req(r) == _norm_req(nivel):
                    continue  # lo cubre la escolaridad exacta
                q = por_req.pop(_norm_req(r), None) or pregunta_web_cerrada(r)
                q["descarta"] = True
                if q["tipo"] == "si_no":
                    q["opciones"] = list(OPCIONES_CERRADAS)
                elif len(q.get("opciones") or []) < 2:
                    q = pregunta_web_cerrada(r)
                q["regla"] = regla_descarta(r)
                salida_wa.append(q)
        # 2) condiciones de la descripción («Se registra») · 3) colonia, traslado, transporte y sueldo («Se registra»)
        salida_wa += preguntas_registro(ficha, whatsapp)
        salida_wa += preguntas_datos_siempre(ficha)
    return salida_web, salida_wa


def guion_demo_seccion(clave: str, ficha: FichaGuion) -> GuionEntrevista:
    """Guion determinista (sin clave de OpenAI) con el estilo de cada actividad y los datos REALES de la vacante. Con el
    enfoque «profesional y personal» siempre conserva su tema propio (objetivos y visión de futuro)."""
    enfoque = _enfoque_valido(ficha.enfoque_entrevista)
    g = _guion_demo(ficha.titulo, enfoque)
    pares = list(zip(g.temas, g.preguntas))
    if ficha.responsabilidades:
        r = _minuscula_inicial(ficha.responsabilidades[0])
        pares.insert(1, (f"Experiencia en: {r}", f"Cuéntame cómo has hecho algo parecido a: {r}."))
    if ficha.requisitos_indispensables:
        req = ficha.requisitos_indispensables[0].strip().rstrip(".")
        pares.insert(2, (f"Requisito: {req}", f"Platícame un ejemplo concreto relacionado con: {req}."))
    limite = {"entrevista_whatsapp": 6, "llamada": 5}.get(clave, 8)
    if len(pares) > limite:
        pares = pares[:limite - 1] + [pares[-1]] if enfoque == "profesional_personal" else pares[:limite]
    return GuionEntrevista(enfoque=g.enfoque, temas=[t for t, _ in pares], preguntas=[q for _, q in pares])


def generar_guiones(ficha: FichaGuion, claves: List[str], web_actual: Optional[List[dict]] = None,
                    hay_web: bool = False, hay_whatsapp: bool = False) -> Tuple[dict, bool]:
    """Genera SOLO las secciones `claves` (las de la ruta que no se conservan). `web_actual`: el Prefiltro · Web vigente
    cuando se conserva (las reconfirmaciones de WhatsApp salen de él). Regresa ({clave: contenido}, con_ia)."""
    claves = list(dict.fromkeys(claves))
    guion_claves = [c for c in claves if c in ESTILO_GUION]
    enfoque = _enfoque_valido(ficha.enfoque_entrevista)
    con_ia = False
    crudo = GuionesIA()
    client = _client()
    # 2026-10-11 (versión definitiva): con enfoque OPERATIVO el guion lo redacta la IA con su prompt propio (6 secciones,
    # ≤ 15 palabras, condicionales) y pasa por la REVISIÓN automática (regenera hasta 2 veces); UNA generación para todas
    # las secciones. Sin IA: el guion determinista (Biblioteca de Perfiles + piezas fijas).
    armados: dict = {}
    # Cambio 43: el «Enfoque» de cada actividad decide su guion. Operativo = la entrevista base FIJA
    # (services/entrevista_whatsapp_fija: situación actual, historial de hasta 3 trabajos, confirmación) — NUNCA guion de IA.
    enfoques = enfoques_de_guion(ficha, guion_claves)
    operativas = [c for c in guion_claves if enfoques[c] == "operativo" and c in enfoques_explicitos(ficha)]
    if operativas:
        from . import entrevista_whatsapp_fija as ewf

        for c in operativas:
            armados[c] = ewf.guion_visible()
        guion_claves = [c for c in guion_claves if c not in operativas]
    # rutas anteriores al Cambio 43 (sin «Enfoque» en la actividad) con la VACANTE en Operativo: su guion operativo de siempre
    if guion_claves and enfoque == "operativo":
        import copy as _copy

        from . import entrevista_operativa as eop

        guion_op, ia_op = eop.generar_guion(ficha.titulo, ficha.perfil_operativo, ficha.requisitos_indispensables,
                                            ubicacion=ficha.ubicacion, horario=ficha.horario, responsabilidades=ficha.responsabilidades,
                                            modalidad=ficha.modalidad)
        legado = [c for c in guion_claves if enfoques[c] == "operativo"]
        for c in legado:
            armados[c] = _copy.deepcopy(guion_op)
        con_ia = con_ia or (ia_op and bool(legado))
        guion_claves = [c for c in guion_claves if c not in legado]
    if ficha.ruta_masiva and "prefiltro_whatsapp" in claves and hay_whatsapp:
        # Cambio 29: en rutas Masivos (operativos) el prefiltro de WhatsApp es la BASE FIJA — cero IA generativa
        from . import prefiltro_operativo as pop

        armados["prefiltro_whatsapp"] = pop.preguntas_guardables(ficha)
        claves = [c for c in claves if c != "prefiltro_whatsapp"]
    if client is not None and claves and [c for c in claves if c not in armados]:
        def lista(xs: List[str]) -> str:
            return ("\n" + "\n".join(f"  - {x}" for x in xs)) if xs else " (sin dato)"

        pedido = []
        if "prefiltro_web" in claves:
            pedido.append("prefiltro_web: preguntas CERRADAS (si_no) — una por requisito indispensable (descarta=true) y, si "
                          "acaso, 1-2 deseables (descarta=false). Un requisito numérico es un umbral («¿Tienes al menos 2 años…?»).")
        if "prefiltro_whatsapp" in claves:
            pedido.append(
                "prefiltro_whatsapp: preguntas CERRADAS en tono de chat, UNA idea por pregunta (nunca unas dos con «y»). "
                "Convierte CADA requisito INDISPENSABLE en una pregunta con regla «Descarta si …» (descarta=true) y CADA "
                "condición de la descripción (requisitos deseables, escolaridad que mencione la descripción) en una pregunta "
                "con regla «Se registra» (descarta=false). NO agregues colonia, traslado, transporte ni sueldo: el sistema "
                "los agrega siempre. " + (
                    "La ruta TAMBIÉN tiene prefiltro web: cada requisito INDISPENSABLE se RECONFIRMA pidiendo un DATO CONCRETO "
                    "como pregunta cerrada (tipo numero con rangos, o si_no en lenguaje natural: «¿Terminaste la secundaria?»; "
                    "NUNCA «¿cuál de estas opciones te describe?»), con `reconfirma` = el requisito exacto. Ejemplo: web "
                    "«¿Tienes experiencia operando montacargas?» → WhatsApp «¿Cuánto tiempo llevas operando montacargas?» con "
                    "rangos. NO repitas nada que no sea indispensable." if hay_web else
                    "Es el PRIMER filtro: una pregunta por requisito indispensable (descarta=true), cerradas."))
        for c in guion_claves:
            pedido.append(f"{c}: guion con enfoque, temas y SOLO preguntas ABIERTAS (nunca de sí/no, nunca preguntas de "
                          f"prefiltro). Estilo: {ESTILO_GUION[c]} {reglas_guion_profesional(enfoques[c])}")
        web_txt = ""
        if web_actual and "prefiltro_web" not in claves:
            web_txt = "\nPrefiltro web vigente (las reconfirmaciones salen de aquí):\n" + "\n".join(
                f"  - {q.get('pregunta')} [valida: {q.get('valida')}; {'indispensable' if q.get('descarta') else 'no indispensable'}]"
                for q in web_actual)
        try:
            resp = client.responses.parse(
                model=MODEL,
                instructions=(
                    "Generas las plantillas de conversación de UNA vacante para Red Human (RH en México), en español mexicano. "
                    "Llena SOLO las secciones pedidas; deja vacías las demás. Usa EXCLUSIVAMENTE los datos de la vacante: nunca "
                    "inventes condiciones (sueldo, horario, ubicación, prestaciones). "
                    + "".join(f"Enfoque de {c}: {TEXTO_ENFOQUE_GUION[enfoques[c]]} ({ENFOQUE_ENTREVISTA_TEMAS[enfoques[c]]}). "
                              for c in guion_claves) +
                    f"PROHIBIDO preguntar sobre: {DATOS_SENSIBLES_PROHIBIDOS}.\nSecciones pedidas:\n- " + "\n- ".join(pedido)
                ),
                input=(
                    f"Puesto: {ficha.titulo}\nEmpresa: {ficha.empresa or '(sin dato)'}\nResponsabilidades:{lista(ficha.responsabilidades)}\n"
                    f"Requisitos indispensables:{lista(ficha.requisitos_indispensables)}\n"
                    f"Requisitos deseables:{lista(ficha.requisitos_deseables)}\nUbicación: {ficha.ubicacion or '(sin dato)'}\n"
                    f"Modalidad: {ficha.modalidad or '(sin dato)'}\nHorario: {ficha.horario or '(sin dato)'}\n"
                    f"Sueldo: {ficha.sueldo or '(sin dato)'}\nDescripción: {(ficha.descripcion or '(sin dato)')[:1500]}{web_txt}"
                ),
                text_format=GuionesIA,
            )
            crudo, con_ia = resp.output_parsed, True
            # Cambio 19: validación estricta del prefiltro de WhatsApp — si falta un indispensable o una pregunta une dos
            # ideas, se regenera esa sección con los problemas como retroalimentación (máximo 2 veces). Lo que aún
            # falte lo completa la regla determinista (`asegurar_prefiltros`).
            for intento in range(MAX_REGENERACIONES_PREFILTRO):
                if "prefiltro_whatsapp" not in claves:
                    break
                problemas = revisar_prefiltro_whatsapp([_pregunta_dict(q) for q in crudo.prefiltro_whatsapp],
                                                       [r for r in ficha.requisitos_indispensables if r.strip()])
                if not problemas:
                    break
                print(f"[ia] prefiltro de WhatsApp de «{ficha.titulo}» no pasó la revisión (intento {intento + 1}): {problemas[:3]}", flush=True)
                try:
                    otra = client.responses.parse(
                        model=MODEL,
                        instructions=("Corriges el prefiltro de WhatsApp de una vacante (RH en México). Llena SOLO "
                                      "prefiltro_whatsapp; deja vacío lo demás.\n- " + pedido[[i for i, x in enumerate(pedido)
                                                                                                 if x.startswith("prefiltro_whatsapp")][0]]
                                      + f"\nPROHIBIDO preguntar sobre: {DATOS_SENSIBLES_PROHIBIDOS}."),
                        input=(f"Puesto: {ficha.titulo}\nRequisitos indispensables:{lista(ficha.requisitos_indispensables)}\n"
                               f"Requisitos deseables:{lista(ficha.requisitos_deseables)}{web_txt}\n\nTu versión anterior NO pasó "
                               "la revisión automática. Corrige EXACTAMENTE esto:\n" + "\n".join(f"- {p}" for p in problemas)),
                        text_format=GuionesIA,
                    )
                    crudo.prefiltro_whatsapp = otra.output_parsed.prefiltro_whatsapp
                except Exception as ex:  # noqa: BLE001 — la regla determinista completa lo que falte
                    print(f"[ia] regeneración del prefiltro de WhatsApp no disponible: {str(ex)[:200]}", flush=True)
                    break
        except Exception as ex:  # noqa: BLE001 — sin IA disponible se genera en modo determinista; nunca bloquea
            print(f"[ia] generar_guiones cayó a modo determinista: {str(ex)[:200]}", flush=True)
            crudo, con_ia = GuionesIA(), False

    salida: dict = {}
    if "prefiltro_web" in claves or "prefiltro_whatsapp" in claves:
        web_in = [_pregunta_dict(q) for q in crudo.prefiltro_web] if "prefiltro_web" in claves else list(web_actual or [])
        wa_in = [_pregunta_dict(q) for q in crudo.prefiltro_whatsapp]
        web_out, wa_out = asegurar_prefiltros(web_in, wa_in, ficha, hay_web=hay_web, hay_whatsapp=hay_whatsapp)
        if "prefiltro_web" in claves:
            salida["prefiltro_web"] = web_out
        if "prefiltro_whatsapp" in claves:
            salida["prefiltro_whatsapp"] = wa_out
    for c in guion_claves:
        g = getattr(crudo, c, None)
        if g is None or not g.preguntas:
            g = guion_profesional_demo(ficha, enfoques[c])
        preguntas = asegurar_guion_profesional(abrir_preguntas(list(g.preguntas)), enfoques[c], ficha)
        salida[c] = {"enfoque": _sin_operativo(g.enfoque) or guion_profesional_demo(ficha, enfoques[c]).enfoque,
                     "temas": list(dict.fromkeys(tema_de_pregunta(q) for q in preguntas)), "preguntas": preguntas}
    salida.update(armados)
    return salida, con_ia


# ============================================================ Cambio 43: guion por enfoque (Profesional / + personal)

MAX_PREGUNTAS_PROFESIONAL = 10
TEXTO_ENFOQUE_GUION = {"operativo": "Operativo", "profesional": "Profesional", "profesional_personal": "Profesional + personal"}
PREGUNTA_TRAYECTORIA = "Cuéntame brevemente tu trayectoria profesional y qué te trae a esta vacante."
PREGUNTAS_MOTIVACION = [
    "¿Qué es lo que más te motiva de tu profesión?",
    "¿Qué te gustaría aprender o desarrollar en los próximos años?",
    "Fuera del trabajo, ¿qué intereses o actividades disfrutas?",
]
# Historial trabajo por trabajo y preguntas de lugar: propias del enfoque operativo, PROHIBIDAS en el profesional
_RE_HISTORIAL = re.compile(r"(ultimo trabajo|trabajo anterior|otro trabajo antes|en (que|cual) trabajo|donde trabajaste|laboraste|"
                           r"por que saliste|por que renunciaste|te despidieron|trabajas actualmente|llevas sin trabajar|"
                           r"cada (uno de tus )?(trabajo|empleo)s?|empleo anterior)")
_RE_LUGAR = re.compile(r"(colonia|municipio|donde vives|en que (ciudad|lugar|zona)|traslad|de tu casa)")
_RE_MOTIVACION = re.compile(r"(motiva|interes|disfrut|aprender|te gustaria|desarrollar|pasion|metas|objetivos personales|"
                            r"fuera del trabajo)")


def enfoques_explicitos(ficha: "FichaGuion") -> Dict[str, str]:
    """Los enfoques que define la RUTA: el «Enfoque» de cada actividad y, en Masivos, la entrevista por WhatsApp Operativo."""
    salida = dict(ficha.enfoques or {})
    if ficha.ruta_masiva:
        salida.setdefault("entrevista_whatsapp", "operativo")
    return salida


def enfoques_de_guion(ficha: "FichaGuion", claves: List[str]) -> Dict[str, str]:
    """{sección de guion: enfoque válido}. El de la actividad de la ruta; si no, el de la vacante."""
    explicitos = enfoques_explicitos(ficha)
    return {c: _enfoque_valido(explicitos.get(c) or ficha.enfoque_entrevista) for c in claves}


def tema_de_pregunta(q: str) -> str:
    """Tema (para el entrevistador) de una pregunta del guion profesional."""
    k = _clave_texto(q)
    if "trayectoria" in k:
        return "Trayectoria profesional"
    if _RE_MOTIVACION.search(k):
        return "Intereses, motivaciones y visión de futuro"
    return q.rstrip("?.").lstrip("¿")[:120]


def reglas_guion_profesional(enfoque: str) -> str:
    """Cambio 43: reglas del prompt según el enfoque (el operativo no llega aquí: es la base fija)."""
    reglas = (f"MÁXIMO {MAX_PREGUNTAS_PROFESIONAL} preguntas en total. La PRIMERA es UNA sola pregunta general de trayectoria "
              f"(«{PREGUNTA_TRAYECTORIA}»). Después, preguntas TÉCNICAS del puesto y de CASOS REALES («Platícame de un caso en "
              "el que…») ligadas a sus responsabilidades y requisitos, distintas para cada puesto. PROHIBIDO: recorrer el "
              "historial trabajo por trabajo (último trabajo, el anterior, por qué saliste de cada uno); preguntar «¿En qué "
              "trabajo laboraste en [lugar]?» o cualquier pregunta sobre lugares, colonia o traslado; repetir preguntas; usar "
              "la palabra «operativa» u «operativo».")
    if enfoque == "profesional_personal":
        reglas += (" Agrega al FINAL 2 o 3 preguntas sobre intereses y motivaciones (qué le motiva, qué quiere aprender, qué "
                   f"disfruta), sin datos sensibles; el total sigue siendo máximo {MAX_PREGUNTAS_PROFESIONAL}.")
    return reglas


def _sin_operativo(texto: str) -> str:
    t = " ".join(str(texto or "").split())
    return "" if re.search(r"operativ", _clave_texto(t)) else t


def _es_trayectoria(q: str) -> bool:
    return "trayectoria" in _clave_texto(q)


def asegurar_guion_profesional(preguntas: List[str], enfoque: str, ficha: Optional["FichaGuion"] = None) -> List[str]:
    """Garantía en código del guion Profesional (+ personal): una sola pregunta de trayectoria (la primera), sin historial
    trabajo por trabajo, sin lugares, sin repetidas, sin «operativa», máximo 10; en «+ personal», 2 o 3 de motivación."""
    personal = enfoque == "profesional_personal"
    vistas, tecnicas, motivacion = set(), [], []
    trayectoria = ""
    for q in preguntas or []:
        t = " ".join(str(q or "").split())
        k = _clave_texto(t).strip(" ¿?.")
        if not t or k in vistas or _RE_HISTORIAL.search(k) or _RE_LUGAR.search(k) or "operativ" in k:
            continue
        vistas.add(k)
        if _es_trayectoria(t):
            trayectoria = trayectoria or t
        elif _RE_MOTIVACION.search(k):
            motivacion.append(t)
        else:
            tecnicas.append(t)
    if len(tecnicas) < 3 and ficha is not None:
        for q in guion_profesional_demo(ficha, "profesional").preguntas[1:]:
            k = _clave_texto(q).strip(" ¿?.")
            if k not in vistas and "operativ" not in k:
                vistas.add(k)
                tecnicas.append(q)
            if len(tecnicas) >= 6:
                break
    if personal:
        for q in PREGUNTAS_MOTIVACION:
            if len(motivacion) >= 2:
                break
            if _clave_texto(q).strip(" ¿?.") not in vistas:
                motivacion.append(q)
        motivacion = motivacion[:3]
    else:
        tecnicas += motivacion  # en el profesional puro una pregunta de motivación cuenta como una más
        motivacion = []
    cupo = MAX_PREGUNTAS_PROFESIONAL - 1 - len(motivacion)
    return [trayectoria or PREGUNTA_TRAYECTORIA] + tecnicas[:cupo] + motivacion


def guion_profesional_demo(ficha: "FichaGuion", enfoque: str = "profesional") -> GuionEntrevista:
    """Guion determinista (sin IA) del enfoque Profesional: trayectoria + casos reales y preguntas técnicas construidas con
    las responsabilidades, los requisitos y el puesto REALES de la vacante (un abogado y un contador reciben preguntas
    distintas)."""
    puesto = " ".join((ficha.titulo or "este puesto").split())
    p_min = _minuscula_inicial(puesto)
    preguntas = [PREGUNTA_TRAYECTORIA]
    for r in [x for x in ficha.responsabilidades if x.strip()][:3]:
        preguntas.append(f"Platícame un caso real en el que te tocó {_minuscula_inicial(r.strip().rstrip('.'))}. "
                         "¿Qué hiciste y cuál fue el resultado?")
    for req in [x for x in ficha.requisitos_indispensables if x.strip()]:
        k = _clave_texto(req)
        if any(w in k for w in ("cedula", "disponibilidad", "anos de experiencia", "edad", "traslado", "licencia")):
            continue
        preguntas.append(f"¿Cómo has aplicado {_minuscula_inicial(req.strip().rstrip('.'))} en tu trabajo? Dame un ejemplo concreto.")
        if len(preguntas) >= 6:
            break
    preguntas += [
        f"¿Cuál ha sido el problema más complejo que resolviste como {p_min} y cómo lo abordaste?",
        f"¿Qué herramientas, normas o metodologías usas en tu día a día como {p_min}?",
        f"Cuéntame de una decisión difícil que tomaste como {p_min}: ¿qué consideraste?",
        f"¿Cómo te mantienes actualizado en lo que exige el puesto de {p_min}?",
    ]
    if enfoque == "profesional_personal":
        preguntas += PREGUNTAS_MOTIVACION[:2]
    enfoque_txt = (f"Validar la experiencia técnica y los casos reales de {p_min}"
                   + (", y sus intereses y motivaciones." if enfoque == "profesional_personal" else "."))
    preguntas = asegurar_guion_profesional(preguntas, enfoque)
    return GuionEntrevista(enfoque=enfoque_txt, temas=list(dict.fromkeys(tema_de_pregunta(q) for q in preguntas)), preguntas=preguntas)


def documentos_de_descripcion(*textos) -> list:
    """Cambio 25 (2026-10-12): «Documentos y fotos que se piden» que MENCIONA la vacante (descripción, requisitos,
    responsabilidades). Garantizado en código: solo los del catálogo y solo si el texto los nombra — nunca inventa."""
    from .documentos_vacante import detectar

    return detectar(*textos)


# ============================================================ 2026-10-15 (UVAQ): trato de «usted»

REGLA_USTED = ("TRATO (NO NEGOCIABLE): háblale al candidato de USTED en todo momento («¿Cuál es su experiencia…?», «Le "
               "agradezco», «¿Podría contarme…?»); nunca lo tutees.")
_USTED = (
    (r"\btienes\b", "tiene"), (r"\bpuedes\b", "puede"), (r"\bquieres\b", "quiere"), (r"\beres\b", "es"),
    (r"\bestás\b", "está"), (r"\bestas\b", "está"), (r"\btrabajas\b", "trabaja"), (r"\bharías\b", "haría"),
    (r"\bpodrías\b", "podría"), (r"\bcuentas\b", "cuenta"), (r"\bvives\b", "vive"), (r"\bterminaste\b", "terminó"),
    (r"\btrabajaste\b", "trabajó"), (r"\bestuviste\b", "estuvo"), (r"\bllevas\b", "lleva"), (r"\bnecesitas\b", "necesita"),
    (r"\bte gustaría\b", "le gustaría"), (r"\bte interesa\b", "le interesa"), (r"\bte parece\b", "le parece"),
    (r"\bte escribir", "le escribir"), (r"\bte contactar", "le contactar"), (r"\bte esperamos\b", "le esperamos"),
    (r"\bte enviamos\b", "le enviamos"), (r"\bte ayudo\b", "le ayudo"), (r"\bte darán\b", "le darán"),
    (r"\bte haré\b", "le haré"), (r"\bte lo pregunto\b", "se lo pregunto"), (r"\bte\b", "le"),
    (r"\btus\b", "sus"), (r"\btu\b", "su"), (r"\bcontigo\b", "con usted"), (r"\bti\b", "usted"),
    (r"\bescríbeme\b", "escríbame"), (r"\bdime\b", "dígame"), (r"\bsúbelos\b", "súbalos"), (r"\bsúbela\b", "súbala"),
    (r"\bconfírmame\b", "confírmeme"), (r"\bentra\b", "entre"), (r"\bresponde\b", "responda"), (r"\bavísame\b", "avíseme"),
)


def a_usted(texto: str) -> str:
    """Pasa un mensaje FIJO del bot de «tú» a «usted» (los generados por la IA ya vienen en usted por `REGLA_USTED`)."""
    salida = texto or ""
    for patron, nuevo in _USTED:
        salida = re.sub(patron, lambda m, n=nuevo: (n[:1].upper() + n[1:]) if m.group(0)[:1].isupper() else n, salida,
                        flags=re.IGNORECASE)
    return salida


def trato_usted(v) -> bool:
    return (getattr(v, "trato", "") or "") == "usted"
