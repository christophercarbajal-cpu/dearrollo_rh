"""Prefiltro conversacional (2026-10-08; retro 2026-10-09: TODAS las Cuentas cuya ruta trae «Prefiltro por WhatsApp»).

Reglas del bot (retro 2026-10-09, garantizadas en código): solo preguntas CERRADAS sobre los INDISPENSABLES, de una en
una (lo no indispensable solo se registra si vino del formulario); nunca autopercepciones («¿te consideras
organizado?»); lo que ya respondió en la web se reconfirma con el dato exacto; un número sin unidad («8») pide «¿8 años
o 8 meses?»; «no entiendo» reformula la misma pregunta; un indispensable incumplido = mensaje de rechazo y cierre.
Una postulación que ya iba a medio prefiltro con el flujo anterior (o lo terminó) lo conserva.

UNA entidad (`Postulacion.analisis["prefiltro_conversacional"]`) junta lo que el candidato contestó en el formulario web
y lo que contesta en el chat (WhatsApp o Telegram: la fachada `services/whatsapp.py` decide el canal; aquí no hay nada
propio de Meta). Los criterios salen de `Vacante.preguntas_filtro`:

  * `descarta: true` = INDISPENSABLE: la respuesta contraria cierra la postulación con el motivo exacto.
  * sin `descarta` = solo se REGISTRA (NSS / situación fiscal, experiencia, ubicación…): nunca excluye.
  * `clave: "ubicacion"` (o `solo_con_ubicacion`) = solo se pregunta si la vacante tiene ubicación configurada.

Dinámica del bot: pregunta SOLO lo que falte, una pregunta por mensaje y espera la respuesta. Un saludo, `/start` o
cualquier reconexión repite la pregunta pendiente EXACTA. Una respuesta ambigua en un indispensable pide aclaración
(hasta `MAX_ACLARACIONES`; después decide RH con «Revisar prefiltro»). Un «Parcial» o una respuesta faltante del
formulario web NUNCA aprueba: se pregunta en el chat.

Reconfirmación (2026-10-09): si la vacante trae en «Prefiltro · WhatsApp» preguntas que RECONFIRMAN un indispensable
del web (`reconfirma`), después de un «Sí» del formulario se pide el DATO CONCRETO (cuánto tiempo, cuál opción). Si el dato
contradice el «Sí» es una INCONSISTENCIA: nunca cierra la postulación — queda «Revisar prefiltro» para RH y la
contradicción entra a «Puntos por validar».

Datos de WhatsApp (2026-10-11, tarde): si la vacante trae en «Prefiltro · WhatsApp» las preguntas `tipo: "dato"`
(`ia.DATOS_PREFILTRO`), el bot las hace PRIMERO y en este orden estricto, una por mensaje: 1) escolaridad exacta (hasta
qué año), 2) colonia/municipio, 3) tiempo de traslado, 4) medio de transporte, 5) sueldo («El sueldo es desde… ¿Te
parece bien?», solo si la vacante tiene sueldo capturado; se registra y NUNCA descarta). Ante una respuesta vaga repregunta por un
dato concreto UNA sola vez; si sigue vaga queda en Puntos por validar. La escolaridad DESCARTA de inmediato si no cumple
el nivel indispensable de la vacante (mensaje de cierre); el traslado NUNCA descarta en esta etapa. Los datos quedan en
`analisis.prefiltro_datos` (la ficha los muestra).

Resolución al vuelo (sin esperar a RH):
  * Cumple → prefiltro aprobado; el motor de ruta avanza y manda la liga de la Entrevista Red Human por el canal
    conectado (`motor_ruta._disparar_entrevista`). Con la entrevista por liga NUNCA se pide agendar videollamada.
  * Incumple un indispensable → postulación cerrada (`MOTIVO_CIERRE`) con el motivo exacto y mensaje de cierre.
    RH puede revertirlo con «Continuar por decisión de RH» (`reabrir_por_excepcion`): se conservan las respuestas, se
    registra quién autorizó y la ruta sigue sola (liga de la entrevista incluida).
"""

import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import Postulacion, registrar, ruta_automatica

ENTIDAD = "prefiltro_conversacional"
# Especificación 2026-10-10: el AGENTE decide el prefiltro (no existe «Revisar prefiltro»). Incumplir un indispensable
# = «Descartado» (el MISMO cierre que el descarte de RH, con motivo y mensaje); RH lo revierte con «Reactivar».
MOTIVO_CIERRE = "descartado"
MOTIVO_CIERRE_LEGADO = "prefiltro_no_aprobado"  # cierres anteriores a la especificación
ACTOR = "Red Human (prefiltro)"
MAX_ACLARACIONES = 2

# ============================================================ criterios de la vacante


def _norm(texto: str) -> str:
    from ..routers.candidatos import _norm_resp

    return re.sub(r"\s+", " ", re.sub(r"[¿?¡!.,;:()«»\"']", " ", _norm_resp(texto or ""))).strip()


def ubicacion_vacante(v) -> str:
    if v is None or (v.modalidad or "").lower().startswith("remot"):
        return ""
    return (v.ubicacion or "").strip() or ", ".join(x for x in (v.ubicacion_municipio, v.ubicacion_estado) if x)


def criterios_de(v, p: Optional[Postulacion] = None) -> List[dict]:
    """Criterios del prefiltro (`_criterios_base`) + Cambio 47: con varias ubicaciones, «¿En qué terminal te gustaría
    trabajar?» justo después de los indispensables (si el candidato no la eligió ya en el formulario web); con la
    terminal elegida, las preguntas de traslado nombran ESA terminal."""
    salida = _criterios_base(v, p)
    if v is None:
        return salida
    from . import solicitud_web as ssw
    from . import ubicaciones as subic

    elegida = subic.ubicacion_de(None, p) if p is not None and p.ubicacion_id else None
    for c in salida:
        if c.get("dato") == "interes":
            # Cambio 51: «El puesto es [puesto] en [ciudad, estado]. ¿Te interesa?» (la terminal elegida manda)
            c["chat"] = c["pregunta"] = ssw.pregunta_interes(v, elegida)
    salida = _con_reubicacion(salida, v, p, elegida)
    if elegida is not None:
        for c in salida:
            if c.get("dato") == "traslado":
                # Fletes (2026-10-15): «La Terminal [ciudad] está en [dirección]. ¿Cuánto tiempo harías de tu casa?»
                c["chat"] = c["pregunta"] = (f"La {elegida.nombre} está en {elegida.direccion.strip().rstrip('.')}. ¿Cuánto tiempo harías "
                                             f"de tu casa?" if (elegida.direccion or "").strip() else
                                             f"¿Cuánto tiempo harías de tu casa a {elegida.nombre}?")
            elif c.get("dato") == "puede_trasladarse":
                c["chat"] = c["pregunta"] = f"El trabajo es en {elegida.nombre}. ¿Puedes trasladarte?"
        return salida
    terminal = subic.criterio_chat(None, v)
    if terminal is None:
        return salida
    i = 0
    while i < len(salida) and ((salida[i].get("indispensable") and not salida[i].get("base")) or salida[i].get("dato") == "propia"):
        i += 1  # 2026-10-15: las preguntas propias de RH van con su requisito (antes de la terminal)
    return salida[:i] + [terminal] + salida[i:]


# Cambio 51: si el estado donde vive el candidato es OTRO que el de la vacante → «El trabajo es en [ciudad, estado]. ¿Vives
# ahí o te cambiarías de ciudad?» (justo después de la colonia). Solo se registra en el resumen; nunca descarta.
ESTADOS_MX = {
    "Aguascalientes": ("aguascalientes", "ags"), "Baja California": ("baja california", "tijuana", "mexicali", "ensenada"),
    "Baja California Sur": ("baja california sur", "bcs", "la paz", "los cabos"), "Campeche": ("campeche",),
    "Chiapas": ("chiapas", "tuxtla"), "Chihuahua": ("chihuahua", "ciudad juarez", "cd juarez"),
    "Ciudad de México": ("ciudad de mexico", "cdmx", "df", "distrito federal", "iztapalapa", "coyoacan", "azcapotzalco",
                         "gustavo a madero", "tlalpan", "xochimilco", "benito juarez", "cuauhtemoc", "miguel hidalgo"),
    "Coahuila": ("coahuila", "saltillo", "torreon", "monclova"), "Colima": ("colima", "manzanillo"),
    "Durango": ("durango",), "Estado de México": ("estado de mexico", "edomex", "edo mex", "ecatepec", "nezahualcoyotl", "neza",
                                                  "tlalnepantla", "naucalpan", "toluca", "tultepec", "tultitlan", "cuautitlan",
                                                  "texcoco", "chalco", "coacalco", "nicolas romero", "huehuetoca"),
    "Guanajuato": ("guanajuato", "leon", "irapuato", "celaya", "salamanca"), "Guerrero": ("guerrero", "acapulco", "chilpancingo"),
    "Hidalgo": ("hidalgo", "pachuca", "tula", "tizayuca"),
    "Jalisco": ("jalisco", "guadalajara", "zapopan", "tlaquepaque", "tonala", "tlajomulco", "puerto vallarta"),
    "Michoacán": ("michoacan", "morelia", "uruapan"), "Morelos": ("morelos", "cuernavaca", "cuautla"),
    "Nayarit": ("nayarit", "tepic"),
    "Nuevo León": ("nuevo leon", "monterrey", "apodaca", "guadalupe nl", "san nicolas", "escobedo", "santa catarina",
                   "san pedro garza", "juarez nl", "salinas victoria", "cienega de flores", "garcia nl"),
    "Oaxaca": ("oaxaca",), "Puebla": ("puebla", "tehuacan", "cholula"), "Querétaro": ("queretaro", "qro", "corregidora", "el marques"),
    "Quintana Roo": ("quintana roo", "cancun", "playa del carmen", "chetumal"), "San Luis Potosí": ("san luis potosi", "slp"),
    "Sinaloa": ("sinaloa", "culiacan", "mazatlan", "los mochis"), "Sonora": ("sonora", "hermosillo", "ciudad obregon", "nogales", "guaymas"),
    "Tabasco": ("tabasco", "villahermosa"), "Tamaulipas": ("tamaulipas", "reynosa", "matamoros", "nuevo laredo", "tampico", "victoria"),
    "Tlaxcala": ("tlaxcala", "apizaco"), "Veracruz": ("veracruz", "xalapa", "coatzacoalcos", "cordoba", "orizaba", "poza rica"),
    "Yucatán": ("yucatan", "merida"), "Zacatecas": ("zacatecas", "fresnillo"),
}


def estado_de_texto(texto: str) -> Optional[str]:
    """Estado de México mencionado en una respuesta libre («Col. Centro, Apodaca» → «Nuevo León»). None si no se reconoce."""
    t = f" {_norm(texto)} "
    mejor, largo = None, 0
    for estado, claves in ESTADOS_MX.items():
        for k in claves:
            if f" {k} " in t and len(k) > largo:
                mejor, largo = estado, len(k)
    return mejor


def _estado_vacante(v, elegida=None) -> str:
    if elegida is not None and (elegida.ubicacion_estado or "").strip():
        return elegida.ubicacion_estado.strip()
    return (getattr(v, "ubicacion_estado", "") or "").strip()


def _con_reubicacion(salida: List[dict], v, p: Optional[Postulacion], elegida=None) -> List[dict]:
    if p is None or (getattr(v, "modalidad", "") or "").lower().startswith("remot"):
        return salida
    zona = (((p.analisis or {}).get("prefiltro_datos")) or {}).get("zona") or ""
    vive = estado_de_texto(zona) if zona else None
    estado_v = _estado_vacante(v, elegida)
    if not vive or not estado_v or _norm(vive) == _norm(estado_v):
        return salida
    from . import solicitud_web as ssw

    lugar = ssw.lugar_vacante(v, elegida) or estado_v
    c = {"id": "dato-reubicacion", "clave": "reubicacion", "dato": "reubicacion", "criterio": f"Vive en otro estado ({vive})",
         "pregunta": f"El trabajo es en {lugar}. ¿Vives ahí o te cambiarías de ciudad?", "indispensable": False, "esperada": "si",
         "tipo": "abierta", "unificado": True, "registra": True, "otro_estado": vive}
    c["chat"] = c["pregunta"]
    i = next((n for n, x in enumerate(salida) if x.get("dato") == "zona"), None)
    return salida + [c] if i is None else salida[:i + 1] + [c] + salida[i + 1:]


def _criterios_base(v, p: Optional[Postulacion] = None) -> List[dict]:
    """Criterios del prefiltro de la vacante, en el orden configurado. Con `p` se ajustan a SU ruta (Cambio 24: en la
    ruta corporativa no se pregunta colonia ni transporte y el sueldo es la expectativa) y se suman las brechas de SU
    Análisis de CV (Cambio 22). Cambio 29: en rutas Masivos (operativos) es la BASE FIJA de `prefiltro_operativo`."""
    from . import preguntas_filtro as pfu
    from . import prefiltro_operativo as pop

    if pfu.aplica(v):
        # Cambio 37: la entidad ÚNICA «Preguntas filtro» de la vacante (todas las rutas; la corporativa ajusta sus base)
        unificados = pfu.criterios(v, corporativa=p is not None and _es_corporativa(p))
        brechas = criterios_brecha(unificados, p) if p is not None else []
        for b in brechas:
            if not b.get("por_brecha"):
                b.update({"unificado": True, "tipo": "si_no", "dato": "registro", "indispensable": False})
        return _con_brechas(unificados, brechas)
    if pop.aplica(v, p):
        return pop.criterios(v)
    salida: List[dict] = []
    usados = set()
    ubic = ubicacion_vacante(v)
    for i, pv in enumerate((v.preguntas_filtro if v is not None else None) or []):
        if not isinstance(pv, dict) or not str(pv.get("pregunta", "")).strip():
            continue
        clave = str(pv.get("clave") or "").strip() or f"c{i + 1}"
        es_ubicacion = clave == "ubicacion" or bool(pv.get("solo_con_ubicacion"))
        if es_ubicacion and not ubic:
            continue  # la vacante no tiene ubicación configurada: no se pregunta
        cid = clave if clave not in usados else f"{clave}-{i + 1}"
        usados.add(cid)
        pregunta = str(pv["pregunta"]).strip()
        from . import ia

        if ia.es_autopercepcion(pregunta):
            continue  # retro 2026-10-09: una autopercepción no se puede verificar — el bot nunca la pregunta
        chat = ia.chat_natural(str(pv.get("pregunta_chat") or "").strip() or pregunta, str(pv.get("valida") or ""))
        if es_ubicacion:
            chat = f"¿Vives en {ubic} o te queda cerca para trasladarte?"
        salida.append({
            "id": cid, "clave": clave, "criterio": str(pv.get("valida") or pregunta).strip(), "pregunta": pregunta,
            "chat": chat, "indispensable": bool(pv.get("descarta")),
            "esperada": "no" if _norm(pv.get("respuesta_esperada") or "Sí") == "no" else "si",
        })
    # Retro 2026-10-09: preguntas PROPIAS del prefiltro por WhatsApp (no reconfirman nada): entran las cerradas e
    # indispensables que el web no cubre (las abiertas de vacantes anteriores no: el bot solo pregunta cerradas).
    ya = {_norm(c["criterio"]) for c in salida} | {_norm(c["pregunta"]) for c in salida}
    from . import ia

    for i, pw in enumerate((v.preguntas_filtro_whatsapp if v is not None else None) or []):
        if (not isinstance(pw, dict) or pw.get("reconfirma") or not pw.get("descarta") or not str(pw.get("pregunta", "")).strip()
                or (pw.get("tipo") or "si_no") not in ("si_no", "opcion", "numero") or ia.es_autopercepcion(pw["pregunta"])):
            continue
        pregunta = str(pw["pregunta"]).strip()
        criterio = str(pw.get("valida") or pregunta).strip()
        if _norm(criterio) in ya or _norm(pregunta) in ya:
            continue
        ya.add(_norm(criterio))
        cid = f"wa{i + 1}"
        usados.add(cid)
        salida.append({"id": cid, "clave": cid, "criterio": criterio, "pregunta": pregunta,
                       "chat": ia.chat_natural(str(pw.get("pregunta_chat") or "").strip() or pregunta, criterio), "indispensable": True,
                       "esperada": "no" if _norm(pw.get("respuesta_esperada") or "Sí") == "no" else "si"})
    # 2026-10-09: reconfirmaciones de WhatsApp (dato concreto) de los indispensables del web
    por_criterio = {_norm(c["criterio"]): c for c in salida if c["indispensable"]}
    from .guiones import preguntas_requisitos

    naturales = preguntas_requisitos(v)
    for pw in (v.preguntas_filtro_whatsapp if v is not None else None) or []:
        if not isinstance(pw, dict) or not pw.get("reconfirma") or not str(pw.get("pregunta", "")).strip():
            continue
        base = por_criterio.get(_norm(pw["reconfirma"]))
        if base is None or f"rc-{base['id']}" in usados:
            continue
        usados.add(f"rc-{base['id']}")
        opciones = [str(o) for o in pw.get("opciones") or [] if str(o).strip()]
        # especificación 2026-10-10: el chat NUNCA ofrece opciones múltiples ni numeradas — el candidato contesta con
        # sus palabras y el bot interpreta (las opciones solo sirven para evaluar). Cambio 31: la pregunta es la NATURAL
        # del requisito (la que redactó el generador de la vacante), tal cual — nunca «Para confirmar: ¿cuánto tiempo…?»
        chat = naturales.get(ia._norm_req(base["criterio"])) or ia.chat_natural(str(pw["pregunta"]).strip(), base["criterio"])
        salida.append({
            "id": f"rc-{base['id']}", "clave": "reconfirma", "criterio": base["criterio"], "pregunta": str(pw["pregunta"]).strip(),
            "chat": chat, "indispensable": False, "esperada": "si", "reconfirma": base["id"], "tipo": pw.get("tipo") or "opcion",
            "opciones": opciones, "opciones_validas": [str(o) for o in pw.get("opciones_validas") or []], "minimo": pw.get("minimo"),
            "maximo": pw.get("maximo"),
        })
    # Cambio 19 (2026-10-12): condiciones de la DESCRIPCIÓN («Se registra»): se preguntan en el chat, nunca descartan;
    # un «no» va a Puntos por validar
    registro: List[dict] = []
    for i, pw in enumerate((v.preguntas_filtro_whatsapp if v is not None else None) or []):
        if (not isinstance(pw, dict) or not pw.get("registra") or pw.get("descarta") or pw.get("reconfirma")
                or (pw.get("tipo") or "si_no") != "si_no" or not str(pw.get("pregunta", "")).strip() or ia.es_autopercepcion(pw["pregunta"])):
            continue
        criterio = str(pw.get("valida") or pw["pregunta"]).strip()
        if _norm(criterio) in ya:
            continue
        ya.add(_norm(criterio))
        registro.append({"id": f"reg{i + 1}", "clave": f"reg{i + 1}", "criterio": criterio, "pregunta": str(pw["pregunta"]).strip(),
                         "chat": ia.chat_natural(str(pw["pregunta"]).strip(), criterio), "indispensable": False, "registra": True,
                         "esperada": "si"})
    # 2026-10-11 (tarde) / Cambio 19: los datos de WhatsApp; la escolaridad reconfirma el nivel de estudios indispensable
    # (si la vacante lo pide) y sustituye su reconfirmación sí/no
    datos: List[dict] = []
    for pw in (v.preguntas_filtro_whatsapp if v is not None else None) or []:
        if not isinstance(pw, dict) or pw.get("tipo") != "dato" or pw.get("dato") not in ia.DATOS_PREFILTRO:
            continue
        if any(d["dato"] == pw["dato"] for d in datos) or not str(pw.get("pregunta", "")).strip():
            continue
        d = {"id": f"dato-{pw['dato']}", "clave": pw["dato"], "dato": pw["dato"], "criterio": str(pw.get("valida") or pw["pregunta"]).strip(),
             "pregunta": str(pw["pregunta"]).strip(), "chat": str(pw["pregunta"]).strip(), "indispensable": False,
             "esperada": "si", "tipo": "dato"}
        if pw["dato"] == "escolaridad":
            d["pregunta"] = d["chat"] = ia.PREGUNTA_ESCOLARIDAD  # Cambio 31: siempre «¿Cuál es tu último grado de estudios?»
        if pw["dato"] == "escolaridad":
            # Cambio 19: el nivel sale de la vacante — indispensable («Descarta si…») o de la descripción («Se registra»)
            nivel = nivel_estudios(str(pw.get("respuesta_esperada") or pw.get("valida") or ""))[0]
            if nivel is not None and ia.RE_NIVEL_ESTUDIOS.search(str(pw.get("respuesta_esperada") or pw.get("valida") or "")):
                d["nivel_requerido"] = nivel
                d["indispensable" if pw.get("descarta") else "registra"] = True
        datos.append(d)
    corporativa = p is not None and _es_corporativa(p)
    if corporativa:
        # Cambio 24: ruta corporativa — sin colonia ni transporte; el sueldo pregunta la EXPECTATIVA del candidato
        datos = [d for d in datos if d["dato"] not in ("zona", "transporte", "sueldo")]
        datos.append(dato_expectativa(v))
        # Cambio 31 (Bloque 4): el tiempo de traslado solo se pregunta si la vacante lo tiene como INDISPENSABLE
        from ..routers.vacantes import requisitos_lista

        if not ia.pregunta_traslado_aplica(True, requisitos_lista(v.requisitos) if v is not None else []):
            datos = [d for d in datos if d["dato"] != "traslado"]
    brechas = criterios_brecha(salida, p) if p is not None else []
    if not datos:
        return _con_brechas(salida + registro, brechas)
    q_sueldo = ia.pregunta_sueldo(v.sueldo or "")
    if q_sueldo and not any(d["dato"] == "sueldo" for d in datos):
        # C2 (prueba del 11 de octubre): vacantes con los 4 datos ya guardados también preguntan el sueldo (sin regenerar)
        datos.append({"id": "dato-sueldo", "clave": "sueldo", "dato": "sueldo", "criterio": f"Sueldo: {v.sueldo.strip()}",
                      "pregunta": q_sueldo, "chat": q_sueldo, "indispensable": False, "esperada": "si", "tipo": "dato"})
    datos.sort(key=lambda d: ia.DATOS_PREFILTRO.index(d["dato"]))
    base = next((c for c in salida if c["indispensable"] and not c.get("reconfirma") and ia.RE_NIVEL_ESTUDIOS.search(c["criterio"])), None)
    esc = next((d for d in datos if d["dato"] == "escolaridad"), None)
    if base is not None and esc is not None:
        esc.update({"reconfirma": base["id"], "criterio": base["criterio"], "nivel_requerido": nivel_estudios(base["criterio"])[0]})
        esc.pop("registra", None)
        esc["indispensable"] = False
        salida = [c for c in salida if c.get("reconfirma") != base["id"]]
    # Cambio 19: orden — 1) indispensables (la primera pregunta del chat siempre es un indispensable) · 2) condiciones de la
    # descripción («Se registra») · 3) colonia, traslado, transporte y sueldo · lo demás del formulario (solo se registra)
    esc_desc = [d for d in datos if d.get("registra")]
    esc_indisp = [d for d in datos if d["dato"] == "escolaridad" and (d.get("reconfirma") or d["indispensable"])]
    otros = [d for d in datos if d not in esc_desc and d not in esc_indisp]
    primero = [c for c in salida if c["indispensable"] or c.get("reconfirma")]
    resto = [c for c in salida if c not in primero]
    return _con_brechas(esc_indisp + primero + esc_desc + registro + otros + resto, brechas)


# ============================================================ Cambios 22 y 24: ruta corporativa y brechas del CV


def _es_corporativa(p: Postulacion) -> bool:
    from .proceso import es_corporativa

    return es_corporativa(p)


def tope_sueldo(v) -> Optional[int]:
    """El sueldo más alto que ofrece la vacante (rango «hasta» o, si no, «desde»)."""
    for x in (getattr(v, "sueldo_hasta", None), getattr(v, "sueldo_desde", None)):
        if isinstance(x, (int, float)) and x > 0:
            return int(x)
    return None


MARGEN_EXPECTATIVA = 0.20  # Cambio 24: una expectativa 20 % arriba del sueldo de la vacante va a Puntos por validar


def dato_expectativa(v) -> dict:
    periodo = str(getattr(v, "sueldo_periodicidad", "") or "").strip().lower()
    q = "¿Cuánto esperas ganar " + {"semanal": "a la semana", "quincenal": "a la quincena", "anual": "al año"}.get(periodo, "al mes") + "?"
    return {"id": "dato-sueldo", "clave": "sueldo", "dato": "sueldo", "expectativa": True, "tope": tope_sueldo(v),
            "criterio": f"Sueldo: {(getattr(v, 'sueldo', '') or '').strip()}".rstrip(": ") or "Expectativa de sueldo",
            "pregunta": q, "chat": q, "indispensable": False, "esperada": "si", "tipo": "dato",
            # Cambio 31: evasiva («lo que ofrezcan») → UNA repregunta suave, sin ejemplos de formato
            "repregunta": "Claro. Solo para tenerlo en cuenta, ¿con qué cantidad " + {"semanal": "a la semana", "quincenal": "a la quincena",
                                                                                     "anual": "al año"}.get(periodo, "al mes") + " te sentirías cómodo?"}


_RE_MONTO = re.compile(r"(\d+(?:\.\d+)?)\s*(mil|k)?\b")


def monto_de(texto: str) -> Optional[int]:
    """«15 mil», «$15,000», «18k», «entre 15 y 18 mil» → 15000 (la primera cifra). None si no hay una cifra clara."""
    t = re.sub(r"(?<=\d)[,\s](?=\d{3}\b)", "", (texto or "").lower()).replace("$", " ")
    m = _RE_MONTO.search(t)
    if not m:
        return None
    n = float(m.group(1))
    if m.group(2) or (n < 1000 and re.search(r"\bmil\b", t)):
        n *= 1000
    return int(n) if n >= 1000 else None


_RE_PREFIJO_BRECHA = re.compile(r"^(no (acredita|menciona|demuestra|muestra|indica|especifica)|sin (evidencia|experiencia comprobable|"
                                r"mencion)( de| en| sobre)?|falta( de)?|no se (acredita|menciona))\s*:?\s*", re.IGNORECASE)


def _texto_brecha(b: str) -> str:
    t = _RE_PREFIJO_BRECHA.sub("", " ".join(str(b or "").split())).strip(" .")
    return t[:1].upper() + t[1:] if t else ""


def _coincide(c: dict, textos: List[str]) -> Optional[str]:
    claves = _claves(c)
    for t in textos:
        palabras = {w for w in re.findall(r"[a-z]{5,}", _norm(t)) if w not in _PALABRAS_VACIAS}
        if claves and palabras and len(claves & palabras) >= max(1, min(2, len(claves) // 2)):
            return t
    return None


def criterios_brecha(salida: List[dict], p: Optional[Postulacion]) -> List[dict]:
    """Cambio 22 → Cambio 42: el Análisis de CV ya NO agrega preguntas al prefiltro ni frena la ruta. Una omisión del CV es
    «No lo menciona el CV» y una contradicción real va a «Puntos por validar» (`comparativo_indispensables`); ninguna de
    las dos pone la tarjeta en «Requiere revisión»."""
    return []


def _criterios_brecha_anterior(salida: List[dict], p: Optional[Postulacion]) -> List[dict]:
    a = (p.analisis or {}) if p is not None else {}
    if a.get("ia") is False:
        return []  # análisis demo (sin IA): sus brechas no son reales
    brechas = [str(b) for b in a.get("brechas") or [] if str(b or "").strip() and "modo demo" not in str(b).lower()]
    salida_cv: List[dict] = []
    indisp = [c for c in salida if c["indispensable"] and not c.get("reconfirma")]
    reconfirmados = {c.get("reconfirma") for c in salida if c.get("reconfirma")}
    vistos = set()
    from . import ia

    for i, b in enumerate(brechas[:4]):
        base = next((c for c in indisp if _coincide(c, [b])), None)
        if base is not None:
            if base["id"] in vistos or base["id"] in reconfirmados:
                continue
            vistos.add(base["id"])
            salida_cv.append({**base, "id": f"cv-{base['id']}", "clave": "brecha", "por_brecha": base["id"], "brecha": b})
            continue
        req = _texto_brecha(b)
        if not req or len(req) > 90 or ia.es_autopercepcion(req):
            continue
        q = ia.pregunta_si_no_natural(req)
        salida_cv.append({"id": f"cvb{i + 1}", "clave": "brecha", "criterio": req, "pregunta": q, "chat": q,
                          "indispensable": False, "registra": True, "esperada": "si", "brecha": b})
    return salida_cv


def _con_brechas(criterios: List[dict], brechas: List[dict]) -> List[dict]:
    """Las brechas de un indispensable van con los indispensables; las demás, con lo que se registra."""
    if not brechas:
        return criterios
    indisp = [b for b in brechas if b.get("por_brecha")]
    otras = [b for b in brechas if not b.get("por_brecha")]
    corte = next((i for i, c in enumerate(criterios) if not (c["indispensable"] or c.get("reconfirma"))), len(criterios))
    return criterios[:corte] + indisp + otras + criterios[corte:]


# Cambio 42: una brecha que solo dice que el CV no trae el dato es OMISIÓN («No lo menciona el CV»), nunca inconsistencia
_RE_OMISION = re.compile(r"^(no (lo )?(menciona|indica|especifica|acredita|muestra|demuestra|incluye|detalla|refiere)|sin (mencion|evidencia|"
                         r"informacion|dato|datos|referencia)|falta(n)? (informacion|evidencia|datos?)|no se (menciona|acredita|especifica|"
                         r"indica|incluye)|no hay (informacion|evidencia|mencion)|no aparece|no esta claro|no queda claro)")
NO_LO_MENCIONA = "No lo menciona el CV"


def es_omision(brecha: str) -> bool:
    return bool(_RE_OMISION.match(_norm(brecha)))


NO_APLICA_CV = "No aplica"
# Cambio 53 (2026-10-15): el CV solo se compara contra la respuesta en datos DEMOSTRABLES — experiencia, escolaridad y
# licencias / certificaciones. Lo demás (disponibilidad, horarios, rolar turnos, cargar peso, traslado) = «No aplica» en el
# CV y nunca genera «Inconsistencia».
_RE_DEMOSTRABLE = re.compile(r"\b(experiencia|anos?|meses|escolaridad|estudios?|primaria|secundaria|preparatoria|prepa|bachillerato|"
                             r"licenciatura|ingenieria|carrera|titulo|titulad\w*|cedula|posgrado|maestria|doctorado|tecnico|"
                             r"licencia|certificac\w*|certificad\w*|diplomado|curso|ingles|idioma)\b")
_RE_NO_DEMOSTRABLE = re.compile(r"\b(disponibilidad|disponible|horario|turnos?|rolar|fines? de semana|cargar|carga|peso|pesad\w*|"
                                r"trasladar\w*|traslado|vivir|radio|cambiar(se)? de ciudad|viajar|edad)\b")


def demostrable_en_cv(c: dict) -> bool:
    """¿El requisito se puede demostrar (o contradecir) con un CV? Experiencia, escolaridad, licencias y certificaciones."""
    if c.get("dato") in ("escolaridad", "experiencia", "licencia"):
        return True
    t = _norm(c.get("criterio") or "")
    if _RE_NO_DEMOSTRABLE.search(t) and not re.search(r"\b(experiencia|licencia|cedula|titulo)\b", t):
        return False
    return bool(_RE_DEMOSTRABLE.search(t))


def comparativo_indispensables(p: Postulacion) -> List[dict]:
    """Cambio 22 (ficha de RH): por cada indispensable, lo que dice el CV contra lo que respondió el candidato. Cambio 42:
    «Inconsistencia» SOLO con una contradicción explícita (el CV dice otra cosa); si el CV no trae el dato →
    «No lo menciona el CV» (no es inconsistencia). Nada de esto frena la ruta ni pone «Requiere revisión»."""
    if p is None or p.vacante is None:
        return []
    a = p.analisis or {}
    acreditados = [str(x) for x in a.get("requisitos_cumplidos") or [] if str(x or "").strip()]
    brechas = [str(x) for x in a.get("brechas") or [] if str(x or "").strip()]
    hay_cv = bool(acreditados or brechas) and a.get("ia") is not False
    criterios = criterios_de(p.vacante, p)
    e = estado(p)
    respuestas = e.get("respuestas") or {}
    if not e.get("web_importada"):
        respuestas = (_sembrar_web(p, dict(e), criterios).get("respuestas") or {})
    filas = []
    for c in criterios:
        if c.get("reconfirma") or c.get("por_brecha") or not (c["indispensable"] or (c.get("dato") == "escolaridad" and c.get("nivel_requerido") is not None and not c.get("registra"))):
            continue
        texto_cv, contradice = None, ""
        if hay_cv and not demostrable_en_cv(c):
            texto_cv = NO_APLICA_CV  # Cambio 53: disponibilidad, horarios, cargar peso… no se demuestran con un CV
        elif hay_cv:
            brecha = _coincide(c, brechas)
            if brecha and not es_omision(brecha):
                texto_cv, contradice = f"Contradice: {_texto_brecha(brecha) or brecha}", brecha
            elif _coincide(c, acreditados):
                texto_cv = "Acreditado"
            else:
                texto_cv = NO_LO_MENCIONA
        # la respuesta más reciente: la del chat por la brecha, la reconfirmación o la del indispensable
        r = (respuestas.get(f"cv-{c['id']}") or next((respuestas[x["id"]] for x in criterios if x.get("reconfirma") == c["id"] and x["id"] in respuestas), None)
             or respuestas.get(c["id"]) or {})
        valor = r.get("valor")
        cumple = None if valor is None else valor == c["esperada"]
        filas.append({"requisito": c["criterio"], "cv": texto_cv, "omision": texto_cv == NO_LO_MENCIONA,
                      "respuesta": r.get("respuesta", ""), "fuente": r.get("fuente", ""), "cumple": cumple,
                      "inconsistencia": bool(contradice) and cumple is True, "cvDice": contradice})
    return filas


# ============================================================ datos de WhatsApp (escolaridad, zona, traslado, transporte)

# Cambio 48: jerarquía ORDINAL Primaria < Secundaria < Preparatoria < Licenciatura < Posgrado — cualquier nivel igual o
# superior al que pide la vacante pasa; solo uno estrictamente menor descarta (`cumple_nivel`). Se revisa del más alto al
# más bajo: «licenciatura y maestría» = Posgrado.
_NIVELES = ((5, r"maestria\w*|posgrado\w*|postgrado\w*|doctorado\w*|mba"),
            (4, r"licenciatura|universidad|ingenier\w*|licenciad\w*"),
            (3, r"prepa\w*|preparatoria|bachiller\w*|cbtis|conalep|cetis|cecyt\w*|cobach|tecnico|carrera tecnica"),
            (2, r"secundaria|secu|telesecundaria"),
            (1, r"primaria"))
NOMBRE_NIVEL = {0: "Sin estudios", 1: "Primaria", 2: "Secundaria", 3: "Preparatoria", 4: "Licenciatura", 5: "Posgrado"}
_GRADO_FINAL = {1: 6, 2: 3, 3: 3}
_ORDINALES = {"primero": 1, "primer": 1, "segundo": 2, "tercero": 3, "tercer": 3, "cuarto": 4, "quinto": 5, "sexto": 6}
_RE_GRADO = re.compile(r"\b([1-9])\s*(?:er|ro|do|to|o|vo|mo|no|ero|º|°)?\s*(ano|anos|grado|semestre|semestres|sem)?\b|\b("
                       + "|".join(_ORDINALES) + r")\b(?:\s*(ano|grado|semestre))?")
_RE_SIN_ESTUDIOS = re.compile(r"\b(no estudie|sin estudios|no fui a la escuela|nunca estudie|no tengo estudios|ninguno|ninguna)\b")


def nivel_estudios(texto: str) -> Tuple[Optional[int], Optional[bool]]:
    """(nivel, terminado) de una respuesta libre: «1er año de secundaria» → (2, False); «terminé la prepa» → (3, True);
    «hasta 6to de primaria» → (1, True). Nivel None = no se entiende."""
    t = limpiar(texto)
    if _RE_SIN_ESTUDIOS.search(t):
        return 0, True
    nivel = next((n for n, rx in _NIVELES if re.search(r"\b(" + rx + r")\b", t)), None)
    if nivel is None:
        return None, None
    if _RE_INCOMPLETA.search(t):
        return nivel, False
    m = _RE_GRADO.search(t)
    if m and nivel in _GRADO_FINAL:
        grado = int(m.group(1)) if m.group(1) else _ORDINALES[m.group(3)]
        semestre = "sem" in (m.group(2) or m.group(4) or "")
        final = 6 if (semestre and nivel == 3) else _GRADO_FINAL[nivel]
        return nivel, grado >= final
    if _RE_GRADO_PARCIAL.search(t) and not _RE_TERMINADA.search(t):
        return nivel, False
    return nivel, True


def cumple_nivel(requerido: Optional[int], texto: str) -> Optional[str]:
    """«si» / «no» / None contra el nivel indispensable de la vacante (un nivel superior, aunque esté trunco, cumple)."""
    nivel, terminado = nivel_estudios(texto)
    if nivel is None:
        return None
    if not requerido:
        return "si"
    if nivel > requerido:
        return "si"
    if nivel == requerido:
        return "si" if terminado else "no"
    return "no"


_RE_ZONA_VAGA = re.compile(r"^(cerca|aqui cerca|muy cerca|por aqui|aqui|aca|por aca|lejos|no tan lejos|algo lejos|por ahi|en la ciudad|"
                           r"aqui mismo|no se|no lo se|normal|en mi casa|mi casa)$")
_RE_TIEMPO_VAGO = re.compile(r"^(poco|muy poco|no mucho|nada|un rato|rapido|cerca|lejos|mucho|depende|no se|normal|lo normal|"
                             r"no tanto|un buen rato|bastante)$")
_RE_TRANSPORTE_VAGO = re.compile(r"^(normal|como sea|como pueda|ahi veo|ya vere|lo que sea|no se|depende|como siempre|cualquiera)$")
_MEDIOS = ((r"transporte de (personal|la empresa)|ruta de personal|camion de la empresa", "Transporte de personal"),
           (r"metrobus|metro|tren|mexibus|tren ligero|trolebus|cablebus", "Metro / tren"),
           (r"camion|micro|combi|pesero|colectivo|ruta|transporte publico|autobus|bus", "Transporte público"),
           (r"moto|motocicleta", "Moto"),
           (r"bici|bicicleta", "Bicicleta"),
           (r"caminando|a pie|camino|caminar", "A pie"),
           (r"uber|didi|taxi|indrive", "Taxi o app"),
           (r"auto|carro|coche|camioneta|vehiculo|mi propio", "Auto propio"))


def minutos_de(texto: str) -> Optional[int]:
    """Minutos de traslado de una respuesta libre («40 min», «hora y media», «1:30», «media hora», «una hora»)."""
    from .entrevista_operativa import minutos_traslado

    m = re.search(r"\b(\d{1,2}):(\d{2})\b", texto or "")
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    t = limpiar(texto)
    if re.search(r"\bhoras? y media\b", t) and "hora y media" not in t:
        return (minutos_traslado(re.sub(r"\by media\b", "", t)) or 0) + 30
    if re.search(r"\bmedia hora\b", t):
        return 30 + (minutos_traslado(t.replace("media hora", "")) or 0)
    if re.search(r"\bcuarto de hora\b", t):
        return 15
    mins = minutos_traslado(t)
    if mins is not None:
        return mins
    if re.search(r"\buna hora\b|\bun hora\b", t):
        return 60
    m = re.fullmatch(r"(?:como |unos |unas |mas o menos |aprox\w* )?(\d{1,3})", t)
    if m:
        n = int(m.group(1))
        return n * 60 if n <= 4 else n  # «2» = 2 horas; «40» = 40 minutos
    return None


def medio_de(texto: str) -> Optional[str]:
    t = limpiar(texto)
    medios = [nombre for rx, nombre in _MEDIOS if re.search(r"\b(" + rx + r")\b", t)]
    if medios:
        return " y ".join(dict.fromkeys(medios))
    if not t or _RE_TRANSPORTE_VAGO.match(t) or len(t) < 3:
        return None
    return texto.strip()[:60]


REPREGUNTA_DATO = {
    # Cambio 31: repreguntas SUAVES y sin ejemplos de formato
    "escolaridad": "¿Hasta qué grado llegaste en la escuela?",
    "zona": "¿Me dices el nombre de tu colonia y de tu municipio?",
    "traslado": "¿Cuántos minutos te tomaría llegar?",
    "transporte": "¿Qué transporte usarías, por ejemplo camión, metro o auto?",
    "sueldo": "Solo para confirmar: ¿el sueldo te parece bien?",
}


def clasificar_dato_prefiltro(c: dict, texto: str) -> Tuple[Optional[str], dict]:
    """(valor, extra) de un dato de WhatsApp. valor None = respuesta vaga (se repregunta una vez)."""
    t = limpiar(texto)
    dato = c.get("dato")
    if not t:
        return None, {}
    if c.get("operativo"):
        from . import prefiltro_operativo as pop  # Cambio 29: datos concretos del prefiltro operativo

        return pop.clasificar(c, texto)
    if dato == "terminal":
        # Cambio 47: la terminal donde quiere trabajar (nombre, ciudad o estado); ambigua → se repregunta una vez
        from . import ubicaciones as subic

        o = subic.elegir_en(c.get("ubicaciones") or [], texto)
        return (None, {}) if o is None else ("si", {"ubicacion": o})
    if dato == "escolaridad":
        nivel, terminado = nivel_estudios(texto)
        if nivel is None:
            return None, {}
        extra = {"nivel": NOMBRE_NIVEL.get(nivel, ""), "terminado": terminado}
        return (cumple_nivel(c.get("nivel_requerido"), texto) if c.get("nivel_requerido") is not None else "si"), extra
    if dato == "zona":
        return (None, {}) if (_RE_ZONA_VAGA.match(t) or len(t) < 3) else ("si", {"zona": texto.strip()[:120]})
    if dato == "traslado":
        if _RE_TIEMPO_VAGO.match(t):
            return None, {}
        mins = minutos_de(texto)
        return (None, {}) if mins is None else ("si", {"minutos": mins})
    if dato == "transporte":
        medio = medio_de(texto)
        return (None, {}) if medio is None else ("si", {"medio": medio})
    if dato == "sueldo" and c.get("expectativa"):
        monto = monto_de(texto)
        return (None, {}) if monto is None else ("si", {"monto": monto})
    if dato == "sueldo":
        # C2 (prueba del 11 de octubre): se registra si le parece bien; un «no» nunca descarta (RH lo ve en la ficha)
        v = clasificar({"criterio": "sueldo", "pregunta": c.get("pregunta") or "", "chat": c.get("chat") or ""}, texto)
        return (None, {}) if v is None else (v, {"acepta": v == "si"})
    return "si", {}


def guardar_datos(p: Postulacion, c: dict, texto: str, extra: dict) -> None:
    """`analisis.prefiltro_datos`: lo que la ficha muestra (zona, traslado, transporte, escolaridad)."""
    if c["dato"] == "terminal":
        if extra.get("ubicacion"):
            from . import ubicaciones as subic

            subic.fijar(p, extra["ubicacion"], "prefiltro por WhatsApp")  # Cambio 47: queda en la ficha
        return
    a = dict(p.analisis or {})
    d = dict(a.get("prefiltro_datos") or {})
    d[c["dato"]] = texto.strip()[:200]
    if c["dato"] == "traslado":
        d["traslado_min"] = extra.get("minutos")
    elif c["dato"] == "transporte":
        d["transporte_medio"] = extra.get("medio") or texto.strip()[:60]
    elif c["dato"] == "sueldo" and c.get("expectativa") and extra.get("flexible"):
        d["sueldo"] = "Flexible"  # Cambio 31: evadió dos veces la cifra — se registra y se avanza
        d["sueldo_expectativa"] = None
    elif c["dato"] == "sueldo" and c.get("expectativa"):
        # Cambio 24: expectativa del candidato; más de 20 % arriba del sueldo de la vacante → Puntos por validar
        d["sueldo_expectativa"] = extra.get("monto")
        d["sueldo_ofrecido"] = str(c.get("criterio") or "").removeprefix("Sueldo:").strip()
        tope = c.get("tope")
        if extra.get("monto") and tope and extra["monto"] > tope * (1 + MARGEN_EXPECTATIVA):
            d["sueldo_sobre_tope"] = True
            a["puntos_validar_prefiltro"] = list(a.get("puntos_validar_prefiltro") or []) + [
                f"Expectativa de sueldo: ${extra['monto']:,} (más de 20 % arriba del sueldo de la vacante: "
                f"{d['sueldo_ofrecido'] or f'${tope:,}'})"]
    elif c["dato"] == "sueldo":
        d["sueldo_acepta"] = extra.get("acepta")
        d["sueldo_ofrecido"] = str(c.get("criterio") or "").removeprefix("Sueldo: ").strip()
    elif c["dato"] == "escolaridad":
        d["nivel"] = extra.get("nivel") or ""
        d["terminado"] = extra.get("terminado")
    if c.get("base_operativa"):
        from . import prefiltro_operativo as pop

        pop.guardar(a, d, c, texto, extra)
    d["en"] = _ahora()
    a["prefiltro_datos"] = d
    p.analisis = a
    flag_modified(p, "analisis")


_NUMEROS = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
            "diez": 10, "medio": 0.5}


def _anos(t: str) -> Optional[float]:
    """Años de experiencia de una respuesta libre («3 años», «menos de un año», «más de 4», «6 meses»)."""
    m = re.search(r"(\d+(?:[.,]\d+)?)|\b(" + "|".join(_NUMEROS) + r")\b", t)
    if not m:
        return None
    n = float(m.group(1).replace(",", ".")) if m.group(1) else float(_NUMEROS[m.group(2)])
    if re.search(r"\bmes(es)?\b", t) and not re.search(r"\bano", t):
        n = n / 12
    if re.search(r"\bmenos de\b", t):
        n = max(0.0, n - 0.5)
    elif re.search(r"\b(mas de|arriba de)\b", t):
        n = n + 0.5
    return n


# Especificación 2026-10-10: el bot interpreta lenguaje natural libre e IGNORA el tono (una grosería con respuesta
# válida cuenta). Emoji de sí/no cuentan como respuesta.
_EMOJI_SI = ("👍", "✅", "👌", "💯", "☑", "✔", "🙌", "👍🏻", "👍🏼", "👍🏽", "👍🏾", "👍🏿")
_EMOJI_NO = ("👎", "❌", "🚫", "✖", "⛔")
_RE_GROSERIAS = re.compile(r"\b(pinche\w*|chingad\w*|chingue\w*|chingo|carajo|verga\w*|pendej\w*|put[oa]s?|putamadre|madres?|"
                           r"cabr[oó]n\w*|cabrona|wey|guey|we|joder|mierda|chale|alv|ptm|nmms|neta|ching[ao]\w*|culer[oa]s?|"
                           r"idiota|estupid\w*|imbecil\w*|bot)\b")
_RE_RELLENO = re.compile(r"^(?:(?:oye|mira|bueno|pues|este|ah|eh|ok|okay|a ver|ya te dije|te dije|que|jaja\w*)\s+)+")


def limpiar(texto: str) -> str:
    """Normaliza una respuesta libre: emoji de sí/no → «si»/«no», sin groserías ni muletillas al inicio."""
    crudo = (texto or "").strip()
    if crudo and any(x in crudo for x in _EMOJI_SI) and not any(x in crudo for x in _EMOJI_NO):
        if not re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ0-9]", crudo):
            return "si"
    if crudo and any(x in crudo for x in _EMOJI_NO) and not re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ0-9]", crudo):
        return "no"
    t = _RE_GROSERIAS.sub(" ", _norm(crudo))
    t = re.sub(r"\s+", " ", t).strip()
    return _RE_RELLENO.sub("", t).strip()


_RE_DUDA_SI = re.compile(r"^(creo|pienso|supongo|me parece|yo creo|casi seguro|seguramente) (que )?(si|sip)\b")


def es_duda(texto: str) -> bool:
    """«creo que sí»: cuenta como «sí», pero queda en Puntos por validar."""
    return bool(_RE_DUDA_SI.match(limpiar(texto)))


# Cambio 51: «No sé qué sea eso», «¿qué es eso?», «no sé a qué te refieres» → NO entiende (se refrasea; no es evasiva)
_RE_NO_SABE_QUE_ES = re.compile(r"\b(no se (que|ni que) (sea|es|seria|significa)( eso)?|que (es|seria|significa) eso|no se lo que (es|sea)|"
                                r"no se a que te refieres|no se de que (me )?hablas|ni idea de que (es|sea)|que es (un|una|el|la) )")


def no_entiende(texto: str) -> bool:
    """El candidato no entendió la pregunta (Cambio 51): hay que REFRASEAR, no repetir ni pedir «sí o no»."""
    t = limpiar(texto)
    return bool(_RE_NO_ENTIENDE.fullmatch(t) or _RE_NO_SABE_QUE_ES.search(t) or es_pregunta_de_vuelta(texto))


# Cambio 51: el agente NO promete nada fuera de la vacante («¿se puede negociar?», «¿me pueden pagar más?»)
_RE_NEGOCIA = re.compile(r"\b(negocia\w*|se puede (subir|mejorar|aumentar|cambiar|ajustar)|(me )?(pueden|podrian) (pagar|dar) mas|"
                         r"mas (sueldo|dinero|paga)|subir(le)? (el )?sueldo|aumento|me lo (pueden|podrian) (mejorar|subir)|"
                         r"hay (bono|bonos|vales|aguinaldo extra)|(es|sera) negociable|se puede (hablar|ver) (lo del|el) (sueldo|horario))\b")


def es_negociacion(texto: str) -> bool:
    return bool(_RE_NEGOCIA.search(limpiar(texto)))


def texto_negociacion(p: Postulacion) -> str:
    """Cambio 51: «Eso lo puedes platicar con el equipo de RH de [empresa].» — nunca se promete ni se niega nada."""
    emp = empresa_de(p)
    return f"Eso lo puedes platicar con el equipo de RH{' de ' + emp if emp else ''}."


def es_pregunta_de_vuelta(texto: str) -> bool:
    """El candidato pregunta en lugar de responder («¿qué procesos?»): hay que reformular, no repetir."""
    crudo = (texto or "").strip()
    t = limpiar(crudo)
    return ("?" in crudo or "¿" in crudo) and not re.match(r"^(si|no)\b", t) and len(t.split()) <= 8


_RE_SOLO_NUMERO = re.compile(r"(?:como |unos |unas |mas o menos |aprox\w* )?(\d+(?:[.,]\d+)?|" + "|".join(_NUMEROS) + r")")


def numero_sin_unidad(c: dict, texto: str) -> Optional[str]:
    """«8» en una pregunta de tiempo: ¿años o meses? Regresa el número (texto) si hay que aclarar la unidad."""
    if c.get("tipo") != "numero":
        return None
    m = _RE_SOLO_NUMERO.fullmatch(limpiar(texto))
    return m.group(1) if m else None


def clasificar_dato(c: dict, texto: str) -> Optional[str]:
    """Reconfirmación con dato concreto: «si» si el dato cumple, «no» si lo contradice, None si no se entiende. El
    candidato contesta con sus palabras (nunca se le muestran opciones numeradas)."""
    t = limpiar(texto)
    if not t:
        return None
    if c.get("tipo") != "numero" and _RE_NIVEL_REQ.search(_norm(c["criterio"] + " " + (c.get("pregunta") or ""))):
        # 2026-10-11: nivel de estudios — «sí, 1er año de secundaria» NO cumple «secundaria terminada» (antes de leer opciones)
        v = clasificar({**c, "chat": c.get("chat") or c.get("pregunta") or ""}, texto)
        if v is not None:
            return v
    opciones = c.get("opciones") or []
    validas = {_norm(o) for o in c.get("opciones_validas") or []}
    elegida = None
    if elegida is None:
        elegida = next((o for o in opciones if _norm(o) == t), None) or next((o for o in sorted(opciones, key=len, reverse=True)
                                                                              if _norm(o) and _norm(o) in t), None)
    if elegida is not None and validas:
        return "si" if _norm(elegida) in validas else "no"
    if c.get("tipo") == "numero" and c.get("maximo") not in (None, ""):
        # 2026-10-13: tope numérico («No más de 2 transportes»): más del tope NO cumple
        from .prefiltro_operativo import cantidad_de

        n = cantidad_de(t)
        return None if n is None else ("si" if n <= float(c["maximo"]) else "no")
    if c.get("tipo") == "numero" and c.get("minimo") not in (None, ""):
        n = _anos(t)
        if n is not None:
            return "si" if n >= float(c["minimo"]) else "no"
        return None
    if validas:
        # 2026-10-11: un dato que indica que NO cumple («1er año de secundaria») se interpreta con la regla del requisito
        base = clasificar({**c, "chat": c.get("chat") or c.get("pregunta") or ""}, texto)
        if base is not None:
            return base
        if _RE_NO.match(t):
            return "no" if not any(x.startswith("no") for x in validas) else "si"
        if (_RE_SI.match(t) or _RE_DUDA_SI.match(t)) and any(x.startswith("si") for x in validas):
            return "si"
    return None


def aplica(p: Optional[Postulacion]) -> bool:
    if p is None or p.vacante is None or not criterios_de(p.vacante, p):
        return False
    if ruta_automatica(p.cuenta):
        return True
    # Retro 2026-10-09: todas las Cuentas, si la ruta trae «Prefiltro por WhatsApp» — salvo una postulación que ya iba (o
    # terminó) con el prefiltro anterior: lo conserva.
    pasos = (p.proceso or {}).get("pasos") or []
    if not any(x.get("tipo") == "prefiltro_whatsapp" and not x.get("heredado") for x in pasos):
        return False
    if estado(p):
        return True
    return not (p.prefiltro_completo or (p.analisis or {}).get("respuestas_prefiltro"))


# ============================================================ clasificación de respuestas

_RE_AMBIGUA = re.compile(r"\b(depende|tal vez|quiza|quizas|a veces|mas o menos|no se|no estoy segur\w*|creo que|parcial\w*|"
                         r"algunos?|algunas?|casi|todavia no se|lo veo|lo pienso|puede ser|en ocasiones)\b")
_RE_SI_SIN_PROBLEMA = re.compile(r"\b(no hay problema|sin problema|no tengo problema|ningun problema|no me molesta|no importa)\b")
_RE_NO = re.compile(r"^(no|nop|nel|negativo|nunca|para nada|tampoco|aun no|todavia no)\b")
_RE_SI = re.compile(r"^(?:ya |obvio |claro que |por supuesto que |desde luego que )?(?:la |lo |las |los )?"
                    r"(si|sii+|sip|simon|claro|obvio|por supuesto|afirmativo|correcto|asi es|exacto|yes|ok|okey|va|vale|dale|"
                    r"desde luego|con gusto|cuento con|tengo|puedo|termine|acabe|vivo|estoy|manejo|se manejar|he trabajado|"
                    r"trabaje|estudie|si tengo|ya tengo|listo|sale)\b")
_RE_NO_ENTIENDE = re.compile(r"(no (le )?entiendo( la pregunta)?|no entendi( la pregunta)?|no le entendi|no comprendo|que|como|mande|"
                             r"perdon|a que te refieres|que quieres decir|que significa( eso)?|no se a que te refieres|explicame|me explicas)")
_RE_SUPERIOR = re.compile(r"\b(prepa|preparatoria|bachiller\w*|licenciatura|universidad|carrera|ingenier\w*|tecnico|cbtis|conalep|cetis|cecyt\w*|maestria|posgrado|postgrado|doctorado)\b")
_RE_INCOMPLETA = re.compile(r"\b(trunca|no (la )?termine|sin terminar|inconclus\w*|me falto|incomplet\w*|la deje|no la acabe)\b")
# 2026-10-11: «1er año de secundaria», «llegué hasta segundo», «me quedé en primero» = el nivel NO está terminado
_RE_GRADO_PARCIAL = re.compile(r"\b(1er|1ro|1o|1ero|primer|primero|2do|2o|segundo|un ano|1 ano|dos anos|2 anos|medio ano|"
                               r"me quede en|me sali en|sali en|deje en|nomas (hice|curse|estudie)|solo (hice|curse|estudie)|hasta (el )?"
                               r"(1|2|primer|primero|segundo))\b")
_RE_TERMINADA = re.compile(r"\b(termine|terminada|terminado|concluida|concluido|conclui|completa|completo|acabe|certificado)\b")
_RE_NIVEL_REQ = re.compile(r"\b(primaria|secundaria|preparatoria|prepa|bachillerato|licenciatura|maestria|posgrado|postgrado|doctorado)\b")
SALUDOS = {"hola", "holi", "ola", "buenas", "buen dia", "buenos dias", "buenas tardes", "buenas noches", "hey", "hi", "hello",
           "start", "/start", "continuar", "seguir", "retomar", "sigo aqui", "ya regrese", "inicio", "empezar", "menu"}


def es_saludo(texto: str) -> bool:
    t = _norm(texto).lstrip("/")
    return t in SALUDOS or t.startswith("start ")


def clasificar(c: dict, texto: str) -> Optional[str]:
    """«si» / «no» / None (ambigua). Determinista primero; la IA solo desempata lo que el léxico no resuelve."""
    t = limpiar(texto)
    if not t:
        return None
    if _RE_DUDA_SI.match(t):
        return "si"  # «creo que sí» cuenta como sí (queda en Puntos por validar, ver `turno`)
    base = _norm(c["criterio"] + " " + c["pregunta"])
    nivel = _RE_NIVEL_REQ.search(base)
    if nivel:
        # Cambio 48: jerarquía ordinal — si la respuesta trae un nivel de estudios, decide `cumple_nivel` (igual o superior
        # pasa; solo uno menor, o el mismo sin terminar, no cumple). Sin nivel en la respuesta («sí, la terminé») sigue abajo.
        requerido = nivel_estudios(c["criterio"])[0] or nivel_estudios(base)[0]
        if requerido and nivel_estudios(texto)[0] is not None:
            return cumple_nivel(requerido, texto)
        # 2026-10-11: un nivel CURSADO a medias no cumple un nivel «terminado» → el agente descarta (nunca aprueba)
        parcial = bool(_RE_GRADO_PARCIAL.search(t)) and not _RE_TERMINADA.search(t)
        if nivel.group(1) == "secundaria":
            if _RE_SUPERIOR.search(t) and not parcial:
                return "si"  # bachillerato o más (aunque esté trunco): la secundaria está terminada
            if "secundaria" in t:
                return "no" if (_RE_INCOMPLETA.search(t) or _RE_NO.match(t) or parcial) else "si"
            if "primaria" in t:
                return "no"
        elif nivel.group(1) in t or ("prepa" in t and nivel.group(1) in ("preparatoria", "bachillerato")):
            if _RE_INCOMPLETA.search(t) or parcial or _RE_NO.match(t):
                return "no"
        if parcial:
            return "no"
    if _RE_AMBIGUA.search(t):
        return None
    if _RE_SI_SIN_PROBLEMA.search(t):
        return "si"
    if _RE_NO.match(t):
        return "no"
    if _RE_SI.match(t):
        return "si"
    from . import ia

    if ia.ia_activa():
        return ia.clasificar_respuesta_prefiltro(c["chat"], texto)
    return None


# ============================================================ entidad única (web + chat)


def estado(p: Postulacion) -> dict:
    return dict((p.analisis or {}).get(ENTIDAD) or {})


def _guardar(p: Postulacion, e: dict) -> None:
    a = dict(p.analisis or {})
    a[ENTIDAD] = e
    p.analisis = a
    flag_modified(p, "analisis")


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sembrar_web(p: Postulacion, e: dict, criterios: List[dict]) -> dict:
    """Las respuestas del formulario web entran a la MISMA entidad. «Sí»/«No» claros cuentan; «Parcial» o vacío quedan
    pendientes y el bot los pregunta."""
    respuestas = dict(e.get("respuestas") or {})
    web = {_norm(r.get("pregunta", "")): str(r.get("respuesta", "")).strip()
           for r in (p.analisis or {}).get("respuestas_web") or [] if isinstance(r, dict)}
    from . import solicitud_web as ssw

    for c in criterios:
        if c["id"] not in respuestas and c.get("dato") in ("interes", "sueldo", "edad", "zona") and not c.get("expectativa"):
            # Cambio 50/52 (regla de oro): lo que ya contestó en «Unas preguntas» de la solicitud no se vuelve a preguntar
            valor_b = ssw.respuesta_web(p, c)
            if valor_b:
                from . import preguntas_filtro as pfu

                sentido_b, extra_b = pfu.valor_web({**c, "tipo": c.get("tipo") if c.get("tipo") in pfu.TIPOS else "si_no"}, valor_b)
                if sentido_b is not None:
                    respuestas[c["id"]] = {"respuesta": valor_b, "valor": sentido_b, "fuente": "web", "en": _ahora(), **extra_b}
                    continue
        if c["id"] in respuestas or c.get("por_brecha") or c.get("base_operativa"):
            # Cambio 22: la brecha del CV se pregunta en el chat aunque el formulario dijera «Sí»; Cambio 29: en el
            # prefiltro operativo cada indispensable se confirma con un dato concreto en el chat
            continue
        if c.get("unificado") and c.get("pregunta_web"):
            # Cambio 47: vigencia — el web es Sí/No: un «No» descarta; un «Sí» se confirma en el chat con la FECHA
            valor_w = web.get(_norm(c["pregunta_web"]))
            if valor_w and _norm(valor_w) == "no":
                respuestas[c["id"]] = {"respuesta": valor_w, "valor": "no", "fuente": "web", "en": _ahora()}
            continue
        valor = web.get(_norm(c["pregunta"])) or web.get(_norm(c["criterio"]))
        if not valor:
            continue
        if c.get("unificado"):
            # Cambio 37: la respuesta del formulario a una pregunta de la entidad única (Sí/No, número u opción). Lo que no
            # quedó claro lo pregunta el bot; un «No» explícito a un indispensable descarta igual que en el chat.
            from . import preguntas_filtro as pfu

            sentido, extra = pfu.valor_web(c, valor)
            if sentido is None:
                continue
            respuestas[c["id"]] = {"respuesta": valor, "valor": sentido, "fuente": "web", "en": _ahora(), **extra}
            pfu.registrar_datos(p, c, valor, extra)
            continue
        v = _norm(valor)
        sentido = "si" if v.startswith("si") else "no" if v == "no" else None
        if sentido is None:
            continue  # «Parcial»: no aprueba ni descarta — se aclara en el chat
        respuestas[c["id"]] = {"respuesta": valor, "valor": sentido, "fuente": "web", "en": _ahora()}
    e["respuestas"] = respuestas
    e["web_importada"] = True
    return e


def _evaluar(criterios: List[dict], e: dict) -> Tuple[Optional[str], Optional[dict], Optional[dict]]:
    """(resultado, criterio que incumple, siguiente criterio por preguntar). resultado ∈ cumple | no_cumple | None."""
    respuestas = e.get("respuestas") or {}
    por_id = {c["id"]: c for c in criterios}
    for c in criterios:
        r = respuestas.get(c["id"])
        if r and c["indispensable"] and r.get("valor") and r["valor"] != c["esperada"]:
            return "no_cumple", c, None
        # 2026-10-11 (descarte estricto): si la reconfirmación de un indispensable indica que NO lo cumple («1er año de
        # secundaria» cuando se pide terminada), el agente DESCARTA — la contradicción queda además en Puntos por validar
        if r and c.get("reconfirma") and r.get("valor") == "no" and por_id.get(c["reconfirma"], {}).get("indispensable"):
            return "no_cumple", por_id[c["reconfirma"]], None
    def falta(c: dict) -> bool:
        if c["id"] in respuestas:
            return False
        if c.get("por_brecha"):
            # Cambio 22: brecha del CV en un indispensable — se pregunta si el formulario dijo «Sí» (si no, ya lo cubre la
            # pregunta del indispensable)
            base = respuestas.get(c["por_brecha"]) or {}
            return base.get("valor") == "si" and base.get("fuente") == "web"
        if c.get("dato") or c.get("registra") or c.get("unificado"):
            return True  # los datos de WhatsApp y las condiciones de la descripción («Se registra») siempre se preguntan
        if c.get("reconfirma"):
            # solo se reconfirma un «Sí» dado en el FORMULARIO (lo que ya contestó en el chat no se vuelve a preguntar)
            base = respuestas.get(c["reconfirma"]) or {}
            return base.get("valor") == "si" and base.get("fuente") == "web"
        return c["indispensable"]  # retro 2026-10-09: el chat solo pregunta indispensables (lo demás se registra)

    siguiente = next((c for c in criterios if falta(c)), None)
    if siguiente:
        return None, None, siguiente
    # Cambio 37: un indispensable que siguió sin resolverse tras su repregunta → «Requiere revisión» (RH decide)
    revision = next((c for c in criterios if c.get("unificado") and c["indispensable"] and (respuestas.get(c["id"]) or {}).get("revision")), None)
    return ("revision", revision, None) if revision else ("cumple", None, None)


def _criterios_resueltos(criterios: List[dict], e: dict) -> List[dict]:
    respuestas = e.get("respuestas") or {}
    salida = []
    for c in criterios:
        r = respuestas.get(c["id"]) or {}
        if not r:
            veredicto = "sin_respuesta"
        elif not c["indispensable"]:
            veredicto = "registrado"
        elif r.get("valor") is None:
            veredicto = "no_concluyente"  # pasó a «Puntos por validar»
        else:
            veredicto = "cumple" if r.get("valor") == c["esperada"] else "no_cumple"
        if c.get("reconfirma") and r:
            veredicto = "cumple" if r.get("valor") == "si" else "inconsistencia"
        if r.get("revision"):
            veredicto = "requiere_revision"  # Cambio 37
        if c.get("dato") and r:
            veredicto = ("no_concluyente" if r.get("valor") is None else "registrado") if not (c.get("reconfirma") or c["indispensable"]) else \
                ("cumple" if r.get("valor") == "si" else "no_cumple" if r.get("valor") == "no" else "no_concluyente")
        salida.append({"criterio": c["criterio"], "pregunta": c["pregunta"] if c.get("reconfirma") else c["chat"],
                       "respuesta": r.get("respuesta", ""), "valor": r.get("valor"), "excluyente": c["indispensable"],
                       "veredicto": veredicto, "fuente": r.get("fuente", ""), **({"reconfirma": True} if c.get("reconfirma") else {})})
    return salida


def resumen(p: Postulacion) -> Optional[dict]:
    """Para la ficha y el paso del prefiltro: qué se respondió, por dónde, y qué falta."""
    if not aplica(p):
        return None
    criterios = criterios_de(p.vacante, p)
    e = estado(p)
    if not e.get("web_importada"):
        e = _sembrar_web(p, dict(e), criterios)
    resultado, _incumple, siguiente = _evaluar(criterios, e)
    return {"resultado": e.get("resultado") or None, "pendiente": (siguiente or {}).get("criterio") if not e.get("resultado") else None,
            "preguntaPendiente": (siguiente or {}).get("chat") if not e.get("resultado") else None,
            "criterios": _criterios_resueltos(criterios, e), "iniciado": bool(e.get("pendiente") or e.get("respuestas"))}


def pregunta_pendiente(p: Postulacion) -> Optional[str]:
    """Texto EXACTO de la pregunta en curso (para retomar desde un deep link o una reconexión)."""
    if not aplica(p):
        return None
    e = estado(p)
    if e.get("resultado"):
        return None
    c = next((x for x in criterios_de(p.vacante, p) if x["id"] == e.get("pendiente")), None)
    return c["chat"] if c else None


# ============================================================ resolución


def _resolver(db: Session, p: Postulacion, criterios: List[dict], e: dict, resultado: str, motivo: str) -> None:
    """Escribe la decisión en la entidad, en `analisis.prefiltro_web` (lo que lee el paso de la ruta) y en el tablero.
    Cambio 37: «revision» = «Requiere revisión» (la ruta se detiene y RH decide: continuar o descartar)."""
    e["resultado"] = resultado
    e["motivo"] = motivo
    e["en"] = _ahora()
    e["pendiente"] = None
    _guardar(p, e)
    a = dict(p.analisis or {})
    resueltos = _criterios_resueltos(criterios, e)
    a["prefiltro_web"] = {"resultado": resultado, "motivo": motivo, "criterios": resueltos, "en": e["en"], "decidido_por": "Red Human",
                          "conversacional": True, **({"requiere_revision": True} if resultado == "revision" else {})}
    a["respuestas_prefiltro"] = [{"criterio": x["criterio"], "pregunta": x["pregunta"], "respuesta": x["respuesta"],
                                  "cumple": None if x["veredicto"] in ("registrado", "sin_respuesta") else x["veredicto"] == "cumple"}
                                 for x in resueltos if x["respuesta"]]
    a["prefiltro_resultado"] = resultado
    p.analisis = a
    flag_modified(p, "analisis")
    p.estado = resultado
    p.prefiltro_completo = True  # «revision» también: el chat ya no pregunta, decide RH («Revisar prefiltro»)
    registrar(db, ACTOR, "prefiltro_conversacional_resuelto", "postulacion", p.codigo,
              {"resultado": resultado, "motivo": motivo, "criterios": resueltos})


def registrar_inconsistencia(db: Session, p: Postulacion, criterios: List[dict], e: dict, c: dict) -> None:
    """El dato concreto contradice lo que contestó en el formulario: «Inconsistencia» en Puntos por validar. Nunca
    descarta ni detiene el prefiltro (especificación 2026-10-10: no existe «Revisar prefiltro»). No hace commit."""
    respuestas = e.get("respuestas") or {}
    web = (respuestas.get(c["reconfirma"]) or {}).get("respuesta", "")
    chat = (respuestas.get(c["id"]) or {}).get("respuesta", "")
    a = dict(p.analisis or {})
    a["inconsistencias"] = list(a.get("inconsistencias") or []) + [{
        "criterio": c["criterio"], "web": web, "whatsapp": chat, "aclarada": False, "fuente": "reconfirmacion", "en": _ahora()}]
    p.analisis = a
    flag_modified(p, "analisis")
    registrar(db, ACTOR, "prefiltro_inconsistencia_detectada", "postulacion", p.codigo,
              {"criterio": c["criterio"], "web": web, "chat": chat})


_PALABRAS_VACIAS = {"para", "como", "este", "esta", "tienes", "cuentas", "puedes", "experiencia", "vacante", "puesto", "trabajo",
                    "disponibilidad", "requisito", "tener", "contar", "minimo", "menos", "sobre", "donde", "desde"}


def _claves(c: dict) -> set:
    return {w for w in re.findall(r"[a-z]{5,}", _norm(c["criterio"] + " " + c["pregunta"])) if w not in _PALABRAS_VACIAS}


def _respuestas_adelantadas(criterios: List[dict], e: dict, texto: str, actual: str, fuente: str) -> None:
    """«Respuestas en otro orden» (especificación 2026-10-10): si en el mismo mensaje el candidato ya contestó OTRO
    indispensable pendiente («sí, y también puedo rolar turnos»), se registra y no se le vuelve a preguntar."""
    respuestas = dict(e.get("respuestas") or {})
    partes = [x.strip() for x in re.split(r"[.;,\n]| y | pero | tambien ", limpiar(texto)) if x.strip()]
    for c in criterios:
        if c["id"] == actual or c["id"] in respuestas or not c["indispensable"] or c.get("reconfirma") or c.get("por_brecha"):
            continue
        claves = _claves(c)
        for parte in partes:
            palabras = set(re.findall(r"[a-z]{5,}", parte))
            if claves and palabras & claves:
                v = "no" if re.search(r"\b(no|nunca|tampoco)\b", parte) else "si"
                respuestas[c["id"]] = {"respuesta": parte[:300], "valor": v, "fuente": fuente, "en": _ahora(), "adelantada": True}
                break
    e["respuestas"] = respuestas


def condicion_no_cumplida(p: Postulacion, texto: str) -> None:
    """Cambio 19: una condición de la DESCRIPCIÓN que no cumple («No terminó la secundaria») → Puntos por validar.
    Nunca descarta."""
    a = dict(p.analisis or {})
    previas = list(a.get("condiciones_no_cumplidas") or [])
    if texto not in previas:
        a["condiciones_no_cumplidas"] = previas + [texto[:300]]
        p.analisis = a
        flag_modified(p, "analisis")


def punto_por_validar(p: Postulacion, texto: str) -> None:
    """Lo que el prefiltro no pudo concluir va a «Puntos por validar» (nunca a una revisión que frene al candidato)."""
    a = dict(p.analisis or {})
    a["puntos_validar_prefiltro"] = list(a.get("puntos_validar_prefiltro") or []) + [texto[:300]]
    p.analisis = a
    flag_modified(p, "analisis")


def _no_concluyente(db: Session, p: Postulacion, e: dict, c: dict, respuesta: str, fuente: str) -> None:
    e["respuestas"] = {**(e.get("respuestas") or {}),
                       c["id"]: {"respuesta": respuesta[:300], "valor": None, "fuente": fuente, "en": _ahora(), "no_concluyente": True}}
    punto_por_validar(p, f"Respuesta no concluyente en «{c['criterio']}»" + (f": «{respuesta[:120]}»" if respuesta else " (sin respuesta)"))
    registrar(db, ACTOR, "prefiltro_no_concluyente", "postulacion", p.codigo, {"criterio": c["criterio"], "respuesta": respuesta[:200]})


# Cambio 48: el cierre es GENÉRICO — nunca dice qué requisito no cumplió. El motivo detallado vive SOLO en la base
# (`Postulacion.historial`, evento «descartado»; bitácora), visible para RH en la ficha.
TEXTO_DESCARTE = ("Gracias por tu interés{en}. En esta ocasión no continuaremos con tu postulación. Te tendremos en cuenta "
                  "para futuras vacantes.")


def texto_descarte(empresa: str = "") -> str:
    """Cambio 31: la misma empresa que la presentación. Cambio 48: sin motivo."""
    return TEXTO_DESCARTE.format(en=f" en {empresa}" if empresa else "")


def _texto_cierre(c: dict, empresa: str = "") -> str:
    return texto_descarte(empresa)


def parametros_descarte(p: Postulacion) -> list:
    """Variables de META_PLANTILLA_DESCARTE en el orden de META_PLANTILLA_DESCARTE_PARAMS (default «empresa»)."""
    from ..config import settings
    from ..routers.candidatos import nombre_ficha

    valores = {"empresa": empresa_de(p) or "Red Human", "nombre": (nombre_ficha(p) or "").split(" ")[0] or "candidato(a)",
               "puesto": (p.vacante.titulo if p.vacante else "") or "la vacante"}
    claves = [k.strip().lower() for k in (settings.meta_plantilla_descarte_params or "empresa").split(",") if k.strip()]
    return [valores[k] for k in claves if k in valores]


async def enviar_descarte(db: Session, p: Postulacion, texto: str, canal: str = "whatsapp") -> dict:
    """Cambio 48: el mensaje de descarte sale por la cascada del Cambio 46 — texto libre (ventana de 24 h abierta, lo
    normal dentro del chat) → plantilla META_PLANTILLA_DESCARTE → «Pendiente de plantilla» (se reintenta) si Meta aún no
    la tiene. Queda en `notificaciones_enviadas`. Nunca lanza. Telegram / sin teléfono: igual que cualquier respuesta."""
    from ..config import settings
    from ..routers.candidatos import _enviar_whatsapp
    from .mensajeria import de_cuenta
    from .reintento_plantillas import registrar_envio
    from .whatsapp import enviar_en_cascada

    if canal != "whatsapp" or not p.telefono:
        return await _enviar_whatsapp(p, texto, canal)
    try:
        with de_cuenta(p.cuenta_id):
            envio = await enviar_en_cascada(p.telefono, texto, (settings.meta_plantilla_descarte or "descarte_postulacion_rh").strip(),
                                            parametros_descarte(p), settings.meta_plantilla_idioma)
    except Exception as ex:  # noqa: BLE001 — un error de Meta nunca rompe el descarte
        envio = {"enviado": False, "proveedor": "error", "detalle": f"{type(ex).__name__}: {str(ex)[:200]}"}
    try:
        registrar_envio(db, p, "descarte_prefiltro", envio, "descarte_prefiltro")
    except Exception as ex:  # noqa: BLE001
        print(f"[whatsapp][HSM] {p.codigo}: no se pudo registrar el envío del descarte — {ex}", flush=True)
    return envio


def cerrar_no_aprobado(db: Session, p: Postulacion, criterios: List[dict], e: dict, c: dict) -> str:
    """Incumple un indispensable: cierra la postulación (No aprobado) con el motivo EXACTO. No hace commit. Regresa el
    texto del mensaje de cierre (lo envía quien llama, por el canal de la conversación)."""
    motivo = f"No cumple el requisito indispensable: {c['criterio']}"
    respuesta = ((e.get("respuestas") or {}).get(c["id"]) or {}).get("respuesta", "")
    if respuesta:
        motivo += f" (respondió: «{str(respuesta)[:120]}»)"  # Cambio 37: el motivo del descarte queda con la respuesta
    _resolver(db, p, criterios, e, "no_cumple", motivo)
    sello = datetime.now(timezone.utc)
    p.historial = list(p.historial or []) + [{
        "evento": "descartado", "usuario": ACTOR, "fecha": sello.isoformat(), "motivo": motivo, "origen": "prefiltro",
        "texto": f"Descartado por Red Human en el prefiltro — {motivo}. RH puede reactivarlo desde «…» → «Reactivar».",
    }]
    p.cerrar(MOTIVO_CIERRE)
    registrar(db, ACTOR, "postulacion_cerrada_prefiltro", "postulacion", p.codigo, {"motivo": motivo, "criterio": c["criterio"]})
    return _texto_cierre(c, empresa_de(p))


def historial(p: Postulacion, evento: str, texto: str, **extra) -> None:
    """Cambio 37: cada repregunta, «Requiere revisión» y descarte del prefiltro queda en el historial con su motivo."""
    p.historial = list(p.historial or []) + [{"evento": evento, "usuario": ACTOR, "fecha": _ahora(), "texto": texto[:500], **extra}]


def resolver_revision(db: Session, p: Postulacion, criterios: List[dict], e: dict, c: dict) -> str:
    """Cambio 37: un indispensable quedó sin resolver tras su ÚNICA repregunta → «Requiere revisión». La postulación sigue
    activa, el estado es «revision» y la ruta se detiene hasta que RH decida. Regresa el mensaje al candidato."""
    respuesta = ((e.get("respuestas") or {}).get(c["id"]) or {}).get("respuesta", "")
    motivo = f"Requiere revisión: «{c['criterio']}»" + (f" (respondió: «{str(respuesta)[:120]}»)" if respuesta else "")
    _resolver(db, p, criterios, e, "revision", motivo)
    historial(p, "requiere_revision", f"Prefiltro: {motivo}. RH decide si continúa o se descarta.", motivo=motivo, origen="prefiltro")
    registrar(db, ACTOR, "prefiltro_requiere_revision", "postulacion", p.codigo, {"criterio": c["criterio"], "respuesta": str(respuesta)[:200]})
    from ..routers.candidatos import nombre_ficha

    nombre = (nombre_ficha(p) or "").split(" ")[0]
    emp = empresa_de(p)
    return (f"Gracias{', ' + nombre if nombre and not nombre.startswith('Candidato') else ''}. Registré tus respuestas; una persona "
            f"del equipo de RH{' de ' + emp if emp else ''} revisará tu postulación y te escribirá por este medio.")


# ============================================================ después del descarte (Cambio 37)

MOTIVOS_DESCARTE = (MOTIVO_CIERRE, MOTIVO_CIERRE_LEGADO)
_RE_REFUTA = re.compile(r"^(pero|si (tengo|cuento|cumplo|termine|lo tengo|la tengo|soy|puedo|se)|claro que si|yo si|eso no es (cierto|verdad|asi)|"
                        r"no es (cierto|verdad|asi)|te equivoc\w*|se equivoc\w*|esta mal|estas mal|en realidad|de hecho|"
                        r"me equivoque|me confundi|perdon,? (si|me equivoque)|no,? si (tengo|cumplo|cuento))\b")
_RE_REFUTA_EN = re.compile(r"\b(si tengo|si cuento|si cumplo|si termine|tengo \d+ anos?|si la tengo|si lo tengo|me equivoque|me confundi)\b")


def es_refutacion(texto: str) -> bool:
    """«pero sí tengo», «sí cuento con licencia», «me equivoqué, sí terminé la prepa», «eso no es cierto»."""
    t = limpiar(texto)
    return bool(t) and bool(_RE_REFUTA.match(t) or _RE_REFUTA_EN.search(t))


def descartada(p: Optional[Postulacion]) -> bool:
    return p is not None and not p.activa and (p.motivo_cierre or "") in MOTIVOS_DESCARTE


TEXTO_ACLARACION = "Gracias por aclararlo. Una persona del equipo de RH{emp} revisará tu caso y te escribirá por este medio."


def refutar_descarte(db: Session, p: Postulacion, texto: str) -> str:
    """El candidato refuta el descarte: la postulación SIGUE en su vacante (nunca se cambia de vacante), se reabre con
    estado «revision» («Requiere revisión») y la ruta queda detenida hasta que RH decida. No hace commit."""
    p.activa = True
    p.motivo_cierre = ""
    p.cerrada_en = None
    p.estado = "revision"
    a = dict(p.analisis or {})
    motivo = f"El candidato refutó el descarte: «{texto.strip()[:200]}»"
    pw = dict(a.get("prefiltro_web") or {})
    pw.update({"resultado": "revision", "requiere_revision": True, "motivo": motivo, "en": _ahora(), "refutado": True,
               "motivo_descarte": (a.get("prefiltro_web") or {}).get("motivo", "")})
    a["prefiltro_web"] = pw
    a["prefiltro_resultado"] = "revision"
    e = dict(a.get(ENTIDAD) or {})
    if e:
        e["resultado"] = "revision"
        e["refutado"] = True
        a[ENTIDAD] = e
    p.analisis = a
    flag_modified(p, "analisis")
    historial(p, "descarte_refutado", f"{motivo}. Requiere revisión de RH.", motivo=motivo, origen="prefiltro")
    registrar(db, ACTOR, "descarte_refutado", "postulacion", p.codigo, {"texto": texto[:300]})
    emp = empresa_de(p)
    return TEXTO_ACLARACION.format(emp=f" de {emp}" if emp else "")


def texto_post_descarte(p: Postulacion) -> str:
    puesto = p.vacante.titulo if p.vacante else "esta vacante"
    return (f"Tu postulación a {puesto} no continúa en esta ocasión. Si crees que hubo un error, cuéntame qué requisito sí "
            "cumples y una persona de RH lo revisará.")


MARCA_LIBERACION = "prefiltro_revision_liberada_2026_10_10"


def liberar_revisiones(db: Session) -> dict:
    """UNA vez (marca en bitácora; migración aprobada 2026-10-10): las postulaciones ACTIVAS que quedaron en «Revisar
    prefiltro» siguen activas y el agente decide en su siguiente mensaje. Se conservan las respuestas válidas; las no
    concluyentes se vuelven a preguntar; la contradicción con el formulario ya no detiene (sigue en Puntos por validar).
    Nada se cierra ni se borra. Idempotente y no fatal. No hace commit."""
    from ..models import Bitacora

    try:
        if db.query(Bitacora.id).filter(Bitacora.accion == MARCA_LIBERACION).first():
            return {"liberadas": 0, "yaAplicada": True}
        candidatas = db.query(Postulacion).filter(Postulacion.activa.is_(True)).all()
    except Exception:  # noqa: BLE001
        return {"liberadas": 0}
    liberadas = []
    for p in candidatas:
        a = dict(p.analisis or {})
        e = dict(a.get(ENTIDAD) or {})
        pw = a.get("prefiltro_web") or {}
        if e.get("resultado") != "revision" and pw.get("resultado") != "revision":
            continue
        for k in ("resultado", "motivo", "en", "pendiente", "aclaraciones"):
            e.pop(k, None)
        e["respuestas"] = {k: v for k, v in (e.get("respuestas") or {}).items() if (v or {}).get("valor") is not None}
        if e:
            a[ENTIDAD] = e
        if pw.get("resultado") == "revision":
            a.pop("prefiltro_web", None)
            a.pop("prefiltro_resultado", None)
        p.analisis = a
        flag_modified(p, "analisis")
        p.prefiltro_completo = False
        p.estado = "pendiente"
        p.historial = list(p.historial or []) + [{
            "evento": "prefiltro_revision_liberada", "usuario": "sistema", "fecha": _ahora(),
            "texto": "«Revisar prefiltro» ya no existe: Red Human decide el prefiltro con el siguiente mensaje del candidato.",
        }]
        liberadas.append(p.codigo)
    registrar(db, "sistema", MARCA_LIBERACION, "postulacion", "", {"postulaciones": liberadas})
    db.flush()
    return {"liberadas": len(liberadas)}


def reabrir_por_excepcion(db: Session, p: Postulacion, u) -> bool:
    """«Continuar por decisión de RH» sobre un prefiltro reprobado que cerró la postulación: la reabre conservando las
    respuestas originales (la entidad y el resultado No aprobado no se tocan). No hace commit."""
    if p.activa or p.motivo_cierre not in (MOTIVO_CIERRE, MOTIVO_CIERRE_LEGADO):
        return False
    p.activa = True
    p.motivo_cierre = ""
    p.cerrada_en = None
    p.estado = "cumple"
    sello = datetime.now(timezone.utc)
    p.historial = list(p.historial or []) + [{
        "evento": "reabierta_por_excepcion", "usuario": u.nombre, "fecha": sello.isoformat(),
        "texto": f"Postulación reabierta por decisión de RH ({u.nombre}); las respuestas del prefiltro se conservan.",
    }]
    registrar(db, u.nombre, "postulacion_reabierta_excepcion_prefiltro", "postulacion", p.codigo, {"correo_rh": getattr(u, "correo", "")})
    return True


# ============================================================ desde el formulario web (motor de ruta)


async def avisar_descarte(db: Session, p: Postulacion, texto: str) -> None:
    """Cambio 48: el candidato todavía no escribe (no hay ventana de 24 h) → cascada: texto libre → plantilla de descarte →
    «Pendiente de plantilla» (se reintenta). Solo un fallo definitivo cae al correo. Hace commit. (Cambio 50: también lo
    usa el descarte al enviar la solicitud web.)"""
    envio = await enviar_descarte(db, p, texto, "whatsapp") if p.telefono else {"enviado": False}
    if envio.get("enviado") or envio.get("estado") == "pendiente":
        from ..routers.candidatos import guardar_mensaje

        guardar_mensaje(db, p, "assistant", texto if envio.get("enviado") else f"[Pendiente de plantilla] {texto}", "whatsapp", envio)
    elif p.correo:
        from .motor_ruta import _avisar

        await _avisar(db, p, texto, f"Tu postulación a {p.vacante.titulo if p.vacante else 'la vacante'}", "", motivo="prefiltro")
    db.commit()


async def desde_web(db: Session, p: Postulacion) -> Optional[str]:
    """Al recibir la postulación web (o en el barrido del motor): importa las respuestas del formulario y resuelve lo que
    ya se pueda. Pendientes → nada (el bot pregunta cuando el candidato escriba); sin teléfono para preguntarle → RH
    revisa. Hace commit si decide algo. Regresa el resultado o None."""
    e = estado(p)
    if e.get("resultado") or not (p.analisis or {}).get("respuestas_web"):
        return None
    criterios = criterios_de(p.vacante, p)
    if not e.get("web_importada"):
        e = _sembrar_web(p, e, criterios)
        _guardar(p, e)
    resultado, incumple, siguiente = _evaluar(criterios, e)
    if resultado == "no_cumple":
        texto = cerrar_no_aprobado(db, p, criterios, e, incumple)
        db.commit()
        await avisar_descarte(db, p, texto)
        return "no_cumple"
    if resultado == "cumple":
        _resolver(db, p, criterios, e, "cumple", "Cumple los requisitos indispensables de la vacante")
        db.commit()
        return "cumple"
    if resultado == "revision":
        resolver_revision(db, p, criterios, e, incumple)
        db.commit()
        return "revision"
    if not p.telefono:
        # sin teléfono no se le puede preguntar: lo pendiente pasa a «Puntos por validar» y el agente decide con lo demás
        while siguiente is not None:
            if siguiente.get("dato"):
                e["respuestas"] = {**(e.get("respuestas") or {}), siguiente["id"]: {"respuesta": "", "valor": None, "fuente": "web",
                                                                                   "en": _ahora(), "sin_telefono": True}}
            else:
                _no_concluyente(db, p, e, siguiente, "", "web")
            resultado, incumple, siguiente = _evaluar(criterios, e)
        if resultado == "no_cumple":
            texto = cerrar_no_aprobado(db, p, criterios, e, incumple)
            db.commit()
            return "no_cumple"
        _resolver(db, p, criterios, e, "cumple", "Cumple los requisitos indispensables (con puntos por validar)")
        db.commit()
        return "cumple"
    db.commit()
    return None


# ============================================================ turno del chat

def empresa_de(p: Postulacion) -> str:
    """Cambio 31: la empresa de la presentación y del cierre (una sola regla: `serial.nombre_empresa_candidato`)."""
    from ..serial import nombre_empresa_candidato

    return nombre_empresa_candidato(p.vacante) if p.vacante is not None else ""


CLAVE_PRESENTADO = "bot_presentado"


def ya_presentado(p: Postulacion) -> bool:
    """2026-10-13: el bot se presenta UNA sola vez por postulación (aviso de privacidad, plantilla de inicio o saludo)."""
    return bool((p.analisis or {}).get(CLAVE_PRESENTADO))


def marcar_presentado(p: Postulacion) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    a = dict(p.analisis or {})
    a.setdefault(CLAVE_PRESENTADO, _ahora())
    p.analisis = a
    flag_modified(p, "analisis")


def saludo(p: Postulacion, pregunta: str, presentar: bool = True) -> str:
    """Cambio 31 (2026-10-13): «Hola, [nombre]. Soy Red Human, el agente virtual de RH de [empresa]. [primera pregunta]»
    (reemplaza «el asistente virtual que te acompaña… Primero confírmame:»). `presentar=False` (el candidato respondió a la
    plantilla de WhatsApp, que ya lo presentó) → SOLO la primera pregunta."""
    from ..routers.candidatos import nombre_ficha

    nombre = nombre_ficha(p) or ""
    nombre = "" if not nombre or nombre.startswith("Candidato") else nombre.split(" ")[0]
    vacante = p.vacante.titulo if p.vacante else "la vacante"
    q = re.sub(r"^\s*para confirmar,?\s*", "", (pregunta or "").strip(), flags=re.IGNORECASE)
    q = q[:1].upper() + q[1:] if q[:1] != "¿" else "¿" + q[1:2].upper() + q[2:]
    if not presentar:
        return q
    if ya_presentado(p):
        # ya se presentó (p. ej. en el aviso de privacidad): solo el nombre y la pregunta, nunca dos veces
        return f"Hola{', ' + nombre if nombre else ''}. {q}" if nombre else q
    from .ia import presentacion_bot

    marcar_presentado(p)
    return f"Hola{', ' + nombre if nombre else ''}. {presentacion_bot(empresa_de(p))} {q}"


def con_acuse(e: dict, pregunta: str, nombre: str = "") -> str:
    """«Gracias» como mucho una vez cada 3 mensajes, con variantes (Cambio 19); los demás van directo a la pregunta."""
    from .ia import CADA_CUANTOS_GRACIAS, VARIANTES_GRACIAS

    n = int(e.get("sin_gracias") or 0) + 1
    if n < CADA_CUANTOS_GRACIAS:
        e["sin_gracias"] = n
        return pregunta
    e["sin_gracias"] = 0
    i = int(e.get("variante_gracias") or 0)
    e["variante_gracias"] = i + 1
    variantes = [x for x in VARIANTES_GRACIAS if nombre or "{nombre}" not in x]  # sin nombre: sin repetir «Gracias.»
    return f"{variantes[i % len(variantes)].format(nombre=nombre)} {pregunta}"


def _limite_minutos(texto: str) -> Optional[int]:
    """«¿Haces menos de 1 hora a la planta?» → 60. None si la pregunta no trae un tiempo."""
    t = limpiar(texto)
    if not re.search(r"\b(hora|horas|minutos?|mins?|min)\b", t):
        return None
    return minutos_de(re.sub(r"^.*?\b(menos de|maximo|hasta|no mas de|a lo mucho)\b", "", t))


def contradicciones_web(db: Session, p: Postulacion, criterios: List[dict], e: dict, c: dict, texto: str, extra: dict) -> Optional[dict]:
    """Cambio 19: un dato del chat que CONTRADICE lo que contestó en el formulario web (web: «Sí, menos de 1 hora» → chat:
    «3 horas»; web: «Sí terminé la secundaria» → chat: «1ro de secundaria») queda como «Inconsistencia: [requisito]». Si
    el requisito contradicho es INDISPENSABLE, la respuesta del formulario deja de valer y el agente descarta. Regresa el
    criterio contradicho o None. No hace commit."""
    respuestas = e.get("respuestas") or {}
    for w in criterios:
        r = respuestas.get(w["id"]) or {}
        if w["id"] == c.get("reconfirma") or r.get("fuente") != "web" or r.get("valor") != "si" or r.get("contradicha"):
            continue
        texto_w = f"{w['criterio']} {w['pregunta']}"
        contradice = False
        if c.get("dato") == "traslado" and extra.get("minutos") is not None:
            limite = _limite_minutos(texto_w)
            contradice = limite is not None and extra["minutos"] > limite
        elif c.get("dato") == "escolaridad":
            from .ia import RE_NIVEL_ESTUDIOS

            if RE_NIVEL_ESTUDIOS.search(w["criterio"]):
                contradice = cumple_nivel(nivel_estudios(w["criterio"])[0], texto) == "no"
        if not contradice:
            continue
        a = dict(p.analisis or {})
        a["inconsistencias"] = list(a.get("inconsistencias") or []) + [{
            "criterio": w["criterio"], "web": r.get("respuesta", ""), "whatsapp": texto.strip()[:200], "aclarada": False,
            "fuente": "dato_chat", "indispensable": bool(w["indispensable"]), "en": _ahora()}]
        p.analisis = a
        flag_modified(p, "analisis")
        registrar(db, ACTOR, "prefiltro_inconsistencia_detectada", "postulacion", p.codigo,
                  {"criterio": w["criterio"], "web": r.get("respuesta", ""), "chat": texto[:200], "indispensable": bool(w["indispensable"])})
        if w["indispensable"]:
            e["respuestas"] = {**respuestas, w["id"]: {**r, "valor": "no", "contradicha": True, "chat": texto.strip()[:200]}}
        return w
    return None



async def turno(db: Session, p: Postulacion, texto: str, canal: str, reconexion: bool = False) -> Optional[dict]:
    """Un turno del bot (el mensaje del candidato ya está guardado). None = el prefiltro ya está resuelto y el turno lo
    atiende el flujo de siempre (seguimiento, documentos…)."""
    from ..routers.candidatos import _actualizar_ultima_actividad, _enviar_whatsapp, guardar_mensaje, nombre_ficha
    from .mensajeria import canal_registro

    e = estado(p)
    if e.get("resultado"):
        return None
    from . import plantillas_meta

    post_plantilla = False
    respondio_plantilla = plantillas_meta.marcar_respuesta(db, p, texto)
    if respondio_plantilla and not plantillas_meta.es_confirmacion_inicio(texto) and plantillas_meta.es_aplazamiento(texto):
        # Cambio 37: «¿Tienes 5 minutos…?» → «ahorita no»: se respeta y se espera a que escriba (sin preguntar nada)
        _guardar(p, e)
        aviso = (f"Claro. Escríbeme cuando tengas 5 minutos y seguimos con tu postulación a "
                 f"{p.vacante.titulo if p.vacante else 'la vacante'}.")
        envio = await _enviar_whatsapp(p, aviso, canal)
        guardar_mensaje(db, p, "assistant", aviso, canal, envio)
        _actualizar_ultima_actividad(p)
        db.commit()
        return {"respuesta": aviso, "clasificacion": None, "ia": False, "whatsapp": envio, "prefiltro": None, "aplazada": True}
    if respondio_plantilla and plantillas_meta.es_confirmacion_inicio(texto):
        # 2026-10-13: respuesta al botón de `inicio_entrevista_rh` («Sí, empezar ahora»): abre la ventana de 24 h y el bot
        # contesta YA con la primera pregunta (nunca se toma como respuesta a un criterio). Cambio 31: SIN presentarse —
        # la plantilla ya lo presentó.
        reconexion = True
        post_plantilla = True
    criterios = criterios_de(p.vacante, p)
    if not e.get("web_importada"):
        e = _sembrar_web(p, e, criterios)

    async def responder(mensaje: str, _descarte: bool = False, **extra) -> dict:
        _guardar(p, e)
        # Cambio 48: el descarte sale por la cascada (texto libre → plantilla → «Pendiente de plantilla»)
        envio = await (enviar_descarte(db, p, mensaje, canal) if _descarte else _enviar_whatsapp(p, mensaje, canal))
        guardar_mensaje(db, p, "assistant", mensaje, canal, envio)
        _actualizar_ultima_actividad(p)
        db.commit()
        return {"respuesta": mensaje, "clasificacion": None, "ia": False, "whatsapp": envio, "prefiltro": e.get("resultado"), **extra}

    vacante = p.vacante.titulo if p.vacante else "la vacante"
    pendiente = next((c for c in criterios if c["id"] == e.get("pendiente")), None)
    aclaraciones = dict(e.get("aclaraciones") or {})
    if pendiente is not None and (reconexion or es_saludo(texto)):
        # reconexión: la pregunta pendiente EXACTA, sin contarla como respuesta
        return await responder(f"Retomemos tu postulación a *{vacante}* 👇\n\n{pendiente['chat']}", retomada=True)
    if pendiente is not None and es_negociacion(texto):
        # Cambio 51: nunca prometer condiciones fuera de la vacante; se responde y se retoma la MISMA pregunta
        registrar(db, ACTOR, "prefiltro_negociacion", "postulacion", p.codigo, {"texto": texto[:200]})
        return await responder(f"{texto_negociacion(p)} {pendiente['chat']}", aclaracion=True)
    if pendiente is not None and pendiente.get("unificado"):
        # Cambio 37 — «Preguntas filtro»: descarta SOLO una respuesta clara que no cumple; afirmativa sin dato, parcial,
        # ambigua o evasiva → UNA repregunta concreta y, si sigue sin resolverse, «Requiere revisión». Todo al historial.
        from . import preguntas_filtro as pfu

        fuente = canal_registro(canal)
        unidad = dict(e.get("unidad_pendiente") or {})
        texto_eval = texto
        if pendiente["id"] in unidad:
            t_u = _norm(texto)
            if re.search(r"\bmes", t_u) or re.search(r"\bano", t_u):
                texto_eval = f"{unidad.pop(pendiente['id'])} {'meses' if re.search(r'\bmes', t_u) else 'años'}"
                e["unidad_pendiente"] = unidad
        no_entiende_ = no_entiende(texto)
        valor, extra = (None, {}) if no_entiende_ else pfu.clasificar(pendiente, texto_eval)
        if no_entiende_ and aclaraciones.get(pendiente["id"], 0) < MAX_ACLARACIONES and pendiente.get("dato") != "sueldo":
            # Cambio 51: «No sé qué sea eso» → se REFRASEA con otras palabras (nunca se repite ni se pide «sí o no»)
            n = aclaraciones.get(pendiente["id"], 0) + 1
            aclaraciones[pendiente["id"]] = n
            e["aclaraciones"] = aclaraciones
            from . import ia

            historial(p, "prefiltro_repregunta", f"Pregunta refraseada en «{pendiente['criterio']}»: no la entendió («{texto.strip()[:120]}»)",
                      motivo="no entendió la pregunta", criterio=pendiente["criterio"])
            registrar(db, ACTOR, "prefiltro_pregunta_reformulada", "postulacion", p.codigo, {"criterio": pendiente["criterio"], "intento": n})
            return await responder(ia.reformular_pregunta(pendiente["pregunta"], pendiente["criterio"], None, intento=n,
                                                          tipo=pendiente.get("tipo") if pendiente.get("tipo") in ("numero", "opcion") else "si_no"),
                                   aclaracion=True)
        if extra.get("unidad") and pendiente["id"] not in unidad:
            # «uno» en una pregunta de tiempo: «¿Un año o un mes?» (no cuenta como la repregunta)
            unidad[pendiente["id"]] = extra["unidad"]
            e["unidad_pendiente"] = unidad
            historial(p, "prefiltro_repregunta", f"Repregunta en «{pendiente['criterio']}»: número sin unidad («{texto.strip()[:80]}»)",
                      motivo="numero_sin_unidad", criterio=pendiente["criterio"])
            return await responder(pfu.pregunta_unidad(extra["unidad"]), aclaracion=True)
        if valor is None and not aclaraciones.get(pendiente["id"]):
            aclaraciones[pendiente["id"]] = 1
            e["aclaraciones"] = aclaraciones
            motivo = ("no entendió la pregunta" if no_entiende_ else "respuesta parcial o ambigua" if extra.get("parcial")
                      else "respuesta evasiva o vacía" if pfu.es_evasiva(texto) else "respuesta sin el dato concreto")
            historial(p, "prefiltro_repregunta", f"Repregunta en «{pendiente['criterio']}»: {motivo} («{texto.strip()[:120]}»)",
                      motivo=motivo, criterio=pendiente["criterio"])
            registrar(db, ACTOR, "prefiltro_aclaracion_solicitada", "postulacion", p.codigo,
                      {"criterio": pendiente["criterio"], "respuesta": texto[:200], "intento": 1, "motivo": motivo})
            q = pfu.repregunta(pendiente)
            if no_entiende_ and pendiente.get("dato") != "sueldo":
                q = f"Te lo pregunto de otra forma: {q}"
            return await responder(q, aclaracion=True)
        if valor is None and pendiente.get("expectativa"):
            valor, extra = "si", {"flexible": True, "evasiva": texto.strip()[:200]}  # Cambio 31: expectativa «Flexible»
        if valor is None:
            # segunda vez sin resolver: «Requiere revisión» (nunca se toma como «No»)
            e["respuestas"] = {**(e.get("respuestas") or {}), pendiente["id"]: {
                "respuesta": texto.strip()[:300], "valor": None, "fuente": fuente, "en": _ahora(), "revision": True}}
            punto_por_validar(p, f"Requiere revisión: «{pendiente['criterio']}» — respondió «{texto.strip()[:120]}»")
            historial(p, "prefiltro_sin_resolver", f"«{pendiente['criterio']}» sigue sin resolverse tras la repregunta: requiere "
                      f"revisión («{texto.strip()[:120]}»)", motivo="sin_resolver", criterio=pendiente["criterio"])
        else:
            e["respuestas"] = {**(e.get("respuestas") or {}), pendiente["id"]: {
                "respuesta": texto_eval.strip()[:300], "valor": valor, "fuente": fuente, "en": _ahora(), **extra}}
            pfu.registrar_datos(p, pendiente, texto_eval, extra)
            if valor == "si" and es_duda(texto):
                punto_por_validar(p, f"Respondió con duda en «{pendiente['criterio']}»: «{texto.strip()[:120]}»")
        e["pendiente"] = None
        pendiente = None
    if pendiente is not None and no_entiende(texto):
        # retro 2026-10-09: «no entiendo» → la MISMA pregunta con otras palabras (cuenta como aclaración)
        n = aclaraciones.get(pendiente["id"], 0) + 1
        if n <= MAX_ACLARACIONES:
            aclaraciones[pendiente["id"]] = n
            e["aclaraciones"] = aclaraciones
            from . import ia

            registrar(db, ACTOR, "prefiltro_pregunta_reformulada", "postulacion", p.codigo, {"criterio": pendiente["criterio"], "intento": n})
            return await responder(ia.reformular_pregunta(pendiente["pregunta"], pendiente["criterio"], None, intento=n,
                                                          tipo=pendiente.get("tipo") or "si_no"), aclaracion=True)
        # sigue sin entender: nunca se toma como «No» — pasa a «Puntos por validar» y el agente decide con lo demás
        _no_concluyente(db, p, e, pendiente, texto.strip(), canal_registro(canal))
        e["pendiente"] = None
        pendiente = None
    if pendiente is not None and pendiente.get("dato"):
        # 2026-10-11 (tarde): dato de WhatsApp — una respuesta vaga (o «no entiendo») se repregunta UNA sola vez por un dato
        # concreto; si sigue vaga queda en Puntos por validar y se continúa. Nunca se toma como «No».
        valor, extra = (None, {}) if (_RE_NO_ENTIENDE.fullmatch(limpiar(texto)) or es_pregunta_de_vuelta(texto)) \
            else clasificar_dato_prefiltro(pendiente, texto)
        if valor is None and not aclaraciones.get(pendiente["id"]):
            aclaraciones[pendiente["id"]] = 1
            e["aclaraciones"] = aclaraciones
            registrar(db, ACTOR, "prefiltro_aclaracion_solicitada", "postulacion", p.codigo,
                      {"criterio": pendiente["criterio"], "respuesta": texto[:200], "intento": 1})
            return await responder(pendiente.get("repregunta") or REPREGUNTA_DATO[pendiente["dato"]], aclaracion=True)
        fuente = canal_registro(canal)
        if valor is None and pendiente.get("expectativa") and not pendiente.get("indispensable"):
            # Cambio 31: segunda evasiva sobre la expectativa de sueldo → se registra «Flexible» y se avanza, sin insistir ni
            # mandarlo a Puntos por validar (los demás datos vagos siguen yendo a Puntos por validar, y también se avanza)
            valor, extra = "si", {"flexible": True, "evasiva": texto.strip()[:200]}
        if valor is None:
            _no_concluyente(db, p, e, pendiente, texto.strip(), fuente)
        else:
            e["respuestas"] = {**(e.get("respuestas") or {}),
                               pendiente["id"]: {"respuesta": texto.strip()[:300], "valor": valor, "fuente": fuente, "en": _ahora(), **extra}}
        guardar_datos(p, pendiente, texto, extra)
        if valor == "no" and pendiente.get("registra") and pendiente.get("dato") == "escolaridad":
            nombre = NOMBRE_NIVEL.get(pendiente.get("nivel_requerido"), "") or pendiente["criterio"]
            condicion_no_cumplida(p, f"No terminó la {nombre.lower()}" if pendiente.get("nivel_requerido") in (1, 2, 3, 4)
                                  else f"No cumple: {pendiente['criterio']}")
        if valor is not None:
            contradicciones_web(db, p, criterios, e, pendiente, texto, extra)  # Cambio 19: contradice al formulario web
        if pendiente.get("reconfirma"):
            base = (e.get("respuestas") or {}).get(pendiente["reconfirma"])
            if base is None:  # sin respuesta del formulario: la escolaridad exacta también responde el requisito
                e["respuestas"] = {**(e.get("respuestas") or {}), pendiente["reconfirma"]: {
                    "respuesta": texto.strip()[:300], "valor": valor, "fuente": fuente, "en": _ahora(), "desde_escolaridad": True}}
            elif valor == "no" and base.get("fuente") == "web" and base.get("valor") == "si":
                registrar_inconsistencia(db, p, criterios, e, pendiente)
        e["pendiente"] = None
        pendiente = None
    unidad = dict(e.get("unidad_pendiente") or {})
    if pendiente is not None and pendiente["id"] in unidad:
        # respuesta a «¿8 años o 8 meses?»: se completa el dato con la unidad elegida
        t = _norm(texto)
        es_mes = bool(re.search(r"\bmes", t))
        if es_mes or re.search(r"\bano", t):
            texto = f"{unidad.pop(pendiente['id'])} {'meses' if es_mes else 'años'}"
            e["unidad_pendiente"] = unidad
    elif pendiente is not None and pendiente.get("reconfirma") and numero_sin_unidad(pendiente, texto):
        n_txt = numero_sin_unidad(pendiente, texto)
        unidad[pendiente["id"]] = n_txt
        e["unidad_pendiente"] = unidad
        from .preguntas_filtro import pregunta_unidad

        return await responder(pregunta_unidad(n_txt), aclaracion=True)  # Cambio 37: «¿Un año o un mes?»
    if pendiente is not None:
        valor = clasificar_dato(pendiente, texto) if pendiente.get("reconfirma") else clasificar(pendiente, texto)
        if valor is None and (pendiente["indispensable"] or pendiente.get("reconfirma")):
            n = aclaraciones.get(pendiente["id"], 0) + 1
            if n > MAX_ACLARACIONES:
                # el agente decide: lo no concluyente pasa a «Puntos por validar» y el prefiltro sigue
                _no_concluyente(db, p, e, pendiente, texto.strip(), canal_registro(canal))
                valor = None
            else:
                aclaraciones[pendiente["id"]] = n
                e["aclaraciones"] = aclaraciones
                registrar(db, ACTOR, "prefiltro_aclaracion_solicitada", "postulacion", p.codigo,
                          {"criterio": pendiente["criterio"], "respuesta": texto[:200], "intento": n})
                # Cambio 31: repregunta sin frases de formulario ni ejemplos de formato. Cambio 37: «Claro. Solo para
                # tenerlo en cuenta…» solo en sueldo — aquí se pide el dato directo
                q = pendiente["chat"].strip()
                return await responder(f"¿Me confirmas con un sí o un no? {q}" if pendiente.get("tipo", "si_no") == "si_no"
                                       else q, aclaracion=True)
        if pendiente["id"] not in (e.get("respuestas") or {}):
            e["respuestas"] = {**(e.get("respuestas") or {}),
                               pendiente["id"]: {"respuesta": texto.strip()[:300], "valor": valor, "fuente": canal_registro(canal), "en": _ahora()}}
        e["pendiente"] = None
        if pendiente.get("reconfirma") and valor == "no":
            registrar_inconsistencia(db, p, criterios, e, pendiente)  # → Puntos por validar; el prefiltro sigue
        if pendiente.get("registra") and valor == "no":
            condicion_no_cumplida(p, f"No cumple: {pendiente['criterio']}")  # Cambio 19: se registra, nunca descarta
        if valor == "si" and es_duda(texto):
            punto_por_validar(p, f"Respondió con duda en «{pendiente['criterio']}»: «{texto.strip()[:120]}»")
        _respuestas_adelantadas(criterios, e, texto, pendiente["id"], canal_registro(canal))

    criterios = criterios_de(p.vacante, p)  # Cambio 51: la colonia recién contestada puede sumar «¿Vives ahí o te cambiarías…?»
    resultado, incumple, siguiente = _evaluar(criterios, e)
    if resultado == "no_cumple":
        mensaje = cerrar_no_aprobado(db, p, criterios, e, incumple)
        return await responder(mensaje, _descarte=True, clasificacion_final="no_cumple")
    if resultado == "revision":
        # Cambio 37: un indispensable sin resolver tras su repregunta → «Requiere revisión»; la ruta se detiene para RH
        mensaje = resolver_revision(db, p, criterios, e, incumple)
        return await responder(mensaje, clasificacion_final="revision")
    if siguiente is not None:
        primera = not e.get("preguntadas")
        e["pendiente"] = siguiente["id"]
        e["preguntadas"] = list(e.get("preguntadas") or []) + [siguiente["id"]]
        if primera:
            # Cambio 19: saludo EXACTO; la primera pregunta es un indispensable (orden de `criterios_de`) y nunca se
            # anuncia cuántas preguntas faltan
            return await responder(saludo(p, siguiente["chat"], presentar=not post_plantilla))
        _n = nombre_ficha(p) or ""
        return await responder(con_acuse(e, siguiente["chat"], "" if _n.startswith("Candidato") else _n.split(" ")[0]))

    # Cumple: se aprueba al instante y la ruta sigue sola (la liga de la entrevista sale por el canal conectado)
    _resolver(db, p, criterios, e, "cumple", "Cumple los requisitos indispensables de la vacante")
    _actualizar_ultima_actividad(p)
    db.commit()
    from . import proceso as sproc

    antes = {m.id for m in p.mensajes or [] if m.rol == "assistant"}
    await sproc.avanzar_seguro(db, p)
    db.refresh(p)
    ultimo = next((m for m in reversed(p.mensajes) if m.rol == "assistant"), None)
    if ultimo is not None and ultimo.id not in antes:
        # la ruta ya mandó el siguiente paso (Entrevista por WhatsApp en este chat, liga o propuesta de cita)
        return {"respuesta": ultimo.texto, "clasificacion": {"estado": "cumple", "evidencia": ""}, "ia": False,
                "whatsapp": {"enviado": bool(ultimo.enviado)}, "prefiltro": "cumple"}
    # retro 2026-10-09: sin valoraciones ni promesas de avance («Tu perfil es compatible» está prohibido)
    _emp = empresa_de(p)  # Cambio 31: misma empresa que la presentación
    mensaje = (f"Gracias, {nombre_ficha(p).split(' ')[0]}. Registré tus respuestas; una persona del equipo de RH{' de ' + _emp if _emp else ''} te escribirá "
               "por este medio. 🙌")
    return await responder(mensaje, clasificacion={"estado": "cumple", "evidencia": ""})
