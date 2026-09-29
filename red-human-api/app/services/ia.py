"""
Servicio de IA — OpenAI.

API necesaria: OPENAI_API_KEY  →  https://platform.openai.com (API Keys)
Sin clave, cada función regresa un resultado determinista en "modo demo"
para que la plataforma siga funcionando de punta a punta.
"""

import json
import re
from datetime import datetime
from typing import TYPE_CHECKING, List, Literal, Optional, Tuple

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
    requisitos_indispensables: List[str] = Field(description="4 a 6 requisitos sin los cuales la persona no puede ocupar el puesto.")
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
            "ÚNICAMENTE con Sí / No / Parcial (tipo 'si_no'). Prohibido texto libre: nada de «Describe», «Explica», "
            "«Cuéntanos», «Menciona». Un requisito numérico se convierte en umbral («¿Tienes al menos 2 años de experiencia en caja?»)."
        )
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
    "3. Cumplimiento LFT (art. 3 y 133): prohibido pedir o insinuar edad, sexo, estado civil, embarazo, religión, "
    "apariencia, origen étnico, condición de salud u orientación. Si el usuario los incluyó en los requisitos, "
    "reescríbelos en términos de competencias y regístralo en avisos_cumplimiento.\n"
    "4. Usa lenguaje incluyente con la forma «(a)» del español mexicano (Cajero(a), Repartidor(a)).\n"
    "5. PREFILTRO WEB (`preguntas_filtro`): salen PRINCIPALMENTE de los requisitos indispensables (primero los "
    "capturados por RH) y marcan descarta=true SOLO cuando el requisito sea realmente indispensable. REGLA ESTRICTA: "
    "TODAS son preguntas CERRADAS que se responden únicamente con Sí / No / Parcial (tipo 'si_no'); está PROHIBIDO "
    "el texto libre (no uses «Describe», «Explica», «Cuéntanos», «Menciona», «Detalla», «¿Qué…?», «¿Cómo…?»). Un "
    "requisito numérico se pregunta como umbral cerrado («¿Tienes al menos 2 años de experiencia en caja?»).\n"
    "5b. PREFILTRO WHATSAPP (`preguntas_filtro_whatsapp`): SOLO 2 o 3 puntos críticos (experiencia, ubicación/"
    "traslado, disponibilidad o el requisito eliminatorio principal) redactados como en una conversación de "
    "WhatsApp. REGLA ESTRICTA: UNA pregunta por criterio — prohibidas las preguntas compuestas (nunca «¿cuántos "
    "años tienes de experiencia y has usado SAP?»: son dos preguntas). Aquí sí se permiten respuestas abiertas.\n"
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


def _asegurar_capturado(salida: VacanteGenerada, ficha: FichaVacante) -> VacanteGenerada:
    """Garantía en Python (no depende del modelo) de las dos reglas no negociables de la Parte 3:
    (a) no inventar condiciones — beneficios = capturados; (b) respetar lo capturado — indispensables y
    deseables literal, en su categoría y primero; seniority = el elegido."""
    salida.requisitos_indispensables = _unir_capturado(
        ficha.requisitos_indispensables, salida.requisitos_indispensables, excluir=ficha.requisitos_deseables
    )
    salida.requisitos_deseables = _unir_capturado(
        ficha.requisitos_deseables, salida.requisitos_deseables, excluir=ficha.requisitos_indispensables
    )
    salida.beneficios = [b.strip() for b in ficha.beneficios if b.strip()]
    if ficha.seniority:
        salida.seniority = ficha.seniority  # type: ignore[assignment]
    avisos = list(salida.avisos_cumplimiento)
    if not ficha.beneficios:
        avisos.append("Prestaciones no capturadas: RH debe confirmarlas antes de publicar (no se inventaron).")
    if not ficha.sueldo_texto or ficha.sueldo_texto == "A convenir":
        avisos.append("Sueldo no capturado («A convenir»): RH debe confirmarlo antes de publicar (no se inventó).")
    if not ficha.ubicacion:
        avisos.append("Ubicación no capturada: RH debe confirmarla antes de publicar.")
    salida.avisos_cumplimiento = avisos
    if not salida.preguntas_filtro_whatsapp:
        salida.preguntas_filtro_whatsapp = puntos_criticos_whatsapp(salida.preguntas_filtro)
    salida.preguntas_filtro = cerrar_preguntas_web(salida.preguntas_filtro)
    salida.preguntas_filtro_whatsapp = separar_preguntas_whatsapp(salida.preguntas_filtro_whatsapp)
    for p in salida.preguntas_filtro_whatsapp:
        if p.tipo == "numero" and not p.opciones:
            p.opciones = _RANGO_ANOS_GENERICO
    return salida


# 2026-09-20 (Bloque 1): reglas de prefiltro garantizadas en código, no solo en el prompt.
_VERBOS_ABIERTOS = re.compile(r"^\s*¿?\s*(describe|explica|cu[eé]ntanos|cu[eé]ntame|menciona|detalla|platícanos|platica|qu[eé]\s|c[oó]mo\s|por qu[eé]\s|cu[aá]l(es)?\s)", re.IGNORECASE)
OPCIONES_CERRADAS = ["Sí", "No", "Parcial"]


def _pregunta_cerrada_desde_requisito(valida: str, pregunta: str) -> str:
    base = (valida or "").strip().rstrip(".")
    if base:
        return f"¿Cumples con este requisito: {base}?"
    limpio = re.sub(r"^\s*¿?\s*(describe|explica|cu[eé]ntanos|cu[eé]ntame|menciona|detalla|platícanos|platica)\s*", "", pregunta, flags=re.IGNORECASE).strip(" ?¿.")
    return f"¿Cuentas con {limpio[0].lower() + limpio[1:] if limpio else 'este requisito'}?"


def cerrar_preguntas_web(preguntas: List[PreguntaFiltro]) -> List[PreguntaFiltro]:
    """Prefiltro WEB: todas cerradas (Sí / No / Parcial). Una pregunta abierta o numérica se reescribe como
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
        if not p.respuesta_esperada or p.respuesta_esperada.lower() not in ("sí", "si", "no", "parcial"):
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
                "CÓMO POSTULARTE\n• Envía tu CV por este medio y el equipo de RH te contactará."
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
                "Postúlate en 2 minutos: solo necesitas tu CV o responder unas preguntas rápidas."
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
            f"- Prestaciones capturadas (las únicas que puedes mencionar):{lista(ficha.beneficios)}\n\n"
            "Genera la publicación completa a partir de esta ficha, complementando SOLO lo que falte."
        ),
        text_format=VacanteGenerada,
    )
    return _asegurar_capturado(resp.output_parsed, ficha), True


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
            "humano, NO como un cuestionario robótico;" + saludo + " (2) recorre los criterios en orden "
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
            "quedó atrás."
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

# Qué cubre cada enfoque de entrevista (Punto 6). Solo estos dos niveles.
ENFOQUE_ENTREVISTA_TEMAS = {
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


def mensaje_inicial_entrevista(titulo_vacante: str) -> str:
    """Introducción EXACTA (definida por el usuario, 2026-09-11): initialMessage del avatar y primer
    mensaje del modo texto. Se presenta como «Red Human» — nunca con nombre de persona, nunca como
    "asistente virtual" ni "agente de inteligencia artificial" — e inserta el nombre real de la vacante."""
    puesto = (titulo_vacante or "").strip() or "el puesto"
    return (
        f"Hola, soy Red Human. Gracias por participar en el proceso para {puesto}. "
        "Vamos a conversar sobre tu experiencia, tus intereses y algunos aspectos relevantes para el puesto. ¿Comenzamos?"
    )


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
) -> str:
    """System prompt compartido por el avatar (Anam) y el modo texto — misma personalidad en ambos.

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
        "Eres Red Human, la entrevistadora de Red Human (México). Te presentas SOLO como «Red Human»: nunca "
        "como 'asistente virtual', 'agente de inteligencia artificial', 'asistente de reclutamiento' ni con un "
        "nombre de persona. Si te preguntan directamente si eres una IA, respóndelo con naturalidad y sigue. "
        f"Entrevistas a {candidato_nombre} para el puesto de {titulo} en {empresa_txt}. "
        f"Cuando hables de la empresa, llámala siempre «{empresa_txt}», nunca de otra forma.\n"
        f"Requisitos indispensables: {requisitos or 'no especificados'}.\n"
        f"Condiciones concretas de la vacante: {condiciones}. Si el candidato pregunta por horario, ubicación, "
        "modalidad o sueldo, usa EXACTAMENTE estos datos; lo que no esté aquí, di que RH lo confirmará. "
        "Nunca hables en genérico cuando tienes el dato.\n\n"
        f"Objetivo de la entrevista: {enfoque or 'validar experiencia real, criterio y motivación para el puesto'}.\n"
        f"Enfoque: {enfoque_entrevista} — cubre: {ENFOQUE_ENTREVISTA_TEMAS[enfoque_entrevista]}.\n"
        f"Temas a cubrir (en este orden aproximado):\n{lista_temas}\n"
        + (f"Preguntas de referencia (inspiración de tono, NO script; no tienes que hacerlas todas ni tal cual):\n{referencia}\n" if referencia else "")
        + "\n"
        "PROTOCOLO DE INICIO: tu primer mensaje ya se presentó y terminó con «¿Comenzamos?». Si la respuesta "
        "es afirmativa (sí, claro, vamos, adelante, listo, lista, ok, dale, comencemos), haz DE INMEDIATO la "
        "primera pregunta: sin frases de transición ('perfecto, empecemos', 'muy bien', 'excelente'), sin "
        "volver a presentarte y sin volver a pedir confirmación nunca más. Si contesta que no o pide un "
        f"momento, responde solo: «{ESPERA_INICIO}» y espera. Si recibes un turno vacío o sin contenido antes "
        f"de empezar, di: «{AVISO_SILENCIO.format(nombre=candidato_nombre)}».\n\n"
        "CÓMO ENTREVISTAS: (1) frases cortas y lenguaje sencillo, en español mexicano; (2) UNA sola pregunta "
        "principal por intervención — jamás dos preguntas en la misma oración; (3) primero pregunta algo "
        "general del tema y luego profundiza según lo que responda, con repreguntas también cortas (máximo "
        "dos por tema); (4) NUNCA digas 'Pregunta 1', 'Pregunta 2', ni numeres ni enumeres las preguntas; "
        "(5) si la persona ya respondió algo que ibas a preguntar después, NO lo vuelvas a preguntar: "
        "reconoce brevemente y sigue con lo siguiente; (6) compórtate como una entrevistadora que conversa, "
        "no como quien lee un cuestionario: reacciona a lo que dice, conecta temas, sé cálida y profesional; "
        "(7) no evalúes en voz alta, no prometas nada sobre el resultado: la decisión la toma una persona "
        "de RH; (8) cuando ya tengas suficiente de un tema, cambia de tema sin anunciarlo.\n\n"
        f"CUMPLIMIENTO (NO NEGOCIABLE): nunca preguntes, insinúes ni registres datos sobre {DATOS_SENSIBLES_PROHIBIDOS}. "
        "Si la persona los menciona por su cuenta, no repreguntes, no comentes y sigue con el tema laboral.\n\n"
        "CIERRE: cuando hayas cubierto los temas (o la persona no tenga más que aportar), despídete en un "
        f"solo mensaje que empiece EXACTAMENTE con «{DESPEDIDA_ENTREVISTA}, {candidato_nombre}.» seguido de un "
        "agradecimiento breve y de que el equipo de RH le contactará. Después de despedirte no hagas más "
        "preguntas ni reabras la conversación."
    )


class TurnoEntrevista(BaseModel):
    respuesta: str = Field(description="Siguiente mensaje de la entrevistadora, breve, español mexicano.")
    terminada: bool = Field(description="true solo cuando ya cubriste los temas y te despediste en este mensaje.")


def entrevista_turno(system_prompt: str, historial: List[dict]) -> Tuple[TurnoEntrevista, bool]:
    """Modo texto (demo o fallback sin avatar). historial: [{"rol","texto"}], el último es del candidato."""
    client = _client()
    if client is None:
        n_agente = sum(1 for m in historial if m["rol"] == "assistant")
        n_usuario = sum(1 for m in historial if m["rol"] == "user")
        demo_qs = _guion_demo("el puesto").preguntas
        ultimo = (historial[-1]["texto"] if historial else "").strip().lower()
        # Protocolo de inicio: el primer turno del candidato es la confirmación de que está listo.
        if n_usuario == 1 and n_agente == 1:
            afirmativo = any(w in ultimo for w in ("si", "sí", "listo", "lista", "adelante", "vamos", "claro", "ok", "dale", "comencemos"))
            if not afirmativo:
                return TurnoEntrevista(respuesta=ESPERA_INICIO, terminada=False), False
        # n_agente incluye el saludo inicial: la pregunta i-ésima es demo_qs[n_agente - 1]
        i = max(0, n_agente - 1)
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
    return resp.output_parsed, True


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
    recomendacion: Literal["avanzar", "revision", "no_avanzar"] = Field(
        description="Recomendación PRELIMINAR para RH; la decisión final siempre es humana."
    )
    evidencia: str = Field(description="Citas o paráfrasis concretas de la entrevista que sustentan la recomendación.")
    perfil: Optional[PerfilProfundo] = Field(default=None, description="Conocimiento profundo del candidato con evidencia por dimensión.")
    # 2026-09-13: lo que la entrevista NO alcanzó a cubrir (entrevista suficiente pero corta) — RH lo
    # valida en la Entrevista Humana. Vacío cuando la entrevista fue completa.
    faltante: List[str] = Field(default_factory=list, description="Temas que la entrevista no cubrió y que RH debe validar después.")


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
) -> Tuple[EvaluacionEntrevista, bool]:
    """EVALUACIÓN INTEGRAL (2026-09-13): se basa ÚNICAMENTE en Análisis de CV + Entrevista Red Human.
    Los datos del prefiltro por WhatsApp quedan estrictamente fuera (evita contradicciones).
    Estructura: Afinidad (match_perfil) / Fortalezas / Puntos por validar (riesgos) / Recomendación,
    más lo que faltó (`faltante`) cuando la entrevista fue corta pero suficiente."""
    enfoque_entrevista = _enfoque_valido(enfoque_entrevista)
    faltante = list(faltante or [])
    client = _client()
    if client is None:
        return (
            EvaluacionEntrevista(
                resumen="Modo demo: agrega OPENAI_API_KEY para la evaluación integral real (CV + Entrevista Red Human).",
                fortalezas=["Completó la entrevista"],
                riesgos=(["No se cubrió: " + ", ".join(faltante)] if faltante else []),
                areas_desarrollo=[],
                calif_experiencia=7.0,
                calif_comunicacion=7.0,
                match_perfil=70,
                recomendacion="revision",
                evidencia="Evaluación simulada (modo demo).",
                perfil=_perfil_demo(),
                faltante=faltante,
            ),
            False,
        )

    dialogo = "\n".join(f"{'Entrevistadora' if m['rol'] == 'assistant' else 'Candidato'}: {m['texto']}" for m in transcript)
    resp = client.responses.parse(
        model=MODEL,
        instructions=(
            "Haces la EVALUACIÓN INTEGRAL de un candidato para Red Human (México) con DOS fuentes y solo dos: "
            "(1) el Análisis de CV y (2) la transcripción de la Entrevista Red Human. Ignora por completo "
            "cualquier prefiltro o cuestionario previo. Califica SOLO con base en esas dos fuentes — nunca "
            "inventes. Estructura tu salida así: AFINIDAD (match_perfil 0-100) integrando CV y entrevista; "
            "FORTALEZAS (fortalezas, con evidencia del CV o de lo dicho); PUNTOS POR VALIDAR (riesgos: brechas "
            "del CV no resueltas en la entrevista y dudas que RH debe validar); RECOMENDACIÓN preliminar "
            "(recomendacion) — la decisión final la toma una persona de RH (human-in-the-loop, LFPDPPP). "
            "Si el CV acredita algo que la entrevista contradice, o viceversa, dilo en puntos por validar. "
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
            f"ENTREVISTA RED HUMAN (transcripción):\n{dialogo}"
        ),
        text_format=EvaluacionEntrevista,
    )
    salida = resp.output_parsed
    if faltante and not salida.faltante:
        salida.faltante = faltante
    return salida, True

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
