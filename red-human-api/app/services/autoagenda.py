"""Autoagenda por WhatsApp (Cambio 47, 2026-10-14).

Una actividad presencial con «La agenda el candidato» (`paso.tipo_agenda == "candidato"`) no espera a RH:

1. Al habilitarse (motor de ruta → `revisar`), el bot toma la UBICACIÓN del candidato (`ubicaciones.ubicacion_de`) y le
   ofrece los próximos 3 días hábiles CON CUPO. Si la actividad va en «misma cita» con otras, se agenda UNA sola vez para
   todo el bloque (`proceso.grupo_de`).
2. Elige día → el bot ofrece los horarios con lugar de ese día (un horario lleno nunca se ofrece). Elige hora → se reserva
   el lugar (`ubicaciones.reservar`, resta 1 al cupo; si se llenó en ese instante, se le ofrecen los que quedan).
3. Al confirmar: cada actividad del bloque crea SU evaluación con la cita (`actividades.iniciar`) y cada evaluador recibe
   SU aviso con su liga (cascada del Cambio 46); el candidato recibe UNA confirmación:
   «Perfecto, te esperamos el [día] a las [hora] en [ubicación], [dirección]. [Indicaciones]. Mapa: [liga]» y, si es un
   bloque, «Ese día harás: [actividad 1], [actividad 2]…».
4. Recordatorios al candidato 24 h y 2 h antes (`job_recordatorios`, cada 10 min; cada uno se RECLAMA antes de enviar).
5. «No puedo ir», «reagendar», «cambiar mi cita»… con una cita futura → se libera el lugar, se cancelan las evaluaciones
   de esa cita (avisando a cada evaluador) y se ofrecen nuevas fechas.

Sin ubicación (vacante sin sedes y candidato sin elegir) o sin cupo en los próximos días, la actividad vuelve a «Pendiente
de agendar» para RH (`analisis.autoagenda.error`). Estado en `Postulacion.analisis["autoagenda"]`.
"""

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..fechas import TZ_ORG
from ..models import Evaluacion, Postulacion, ReservaCita, Ubicacion, registrar
from . import proceso as sproc
from . import ubicaciones as subic

CLAVE = "autoagenda"
ACTOR = "Red Human (autoagenda)"
_DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
_NUM = {"una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
        "once": 11, "doce": 12}
_RE_REAGENDAR = re.compile(r"\b(no (voy a |creo |)pod(er|re|ria) (ir|asistir|llegar|presentarme)|no puedo (ir|asistir|llegar|presentarme)|"
                           r"no (voy a |)(ir|asistir|llegar)|reagend\w*|reprogram\w*|cambiar (mi |la |de |)(cita|fecha|dia|hora\w*)|"
                           r"otra fecha|otro dia|otro horario|cancelar (mi |la |)cita|se me complico|no alcanzo a llegar)\b")


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9: ]", " ", t).split())


def estado(p: Postulacion) -> dict:
    return dict(((p.analisis or {}).get(CLAVE)) or {})


def _guardar(p: Postulacion, e: Optional[dict]) -> None:
    a = dict(p.analisis or {})
    if e is None:
        a.pop(CLAVE, None)
    else:
        a[CLAVE] = e
    p.analisis = a
    flag_modified(p, "analisis")


def esperando(p: Postulacion) -> bool:
    """El chat está a la mitad de una autoagenda (eligiendo día u hora)."""
    return bool(p.activa and estado(p).get("fase") in ("dia", "hora"))


def sin_autoagenda(p: Postulacion) -> bool:
    """La autoagenda no pudo correr (sin ubicación o sin cupo): la actividad vuelve a RH («Pendiente de agendar»)."""
    return bool(estado(p).get("error"))


def _actor() -> SimpleNamespace:
    return SimpleNamespace(nombre=ACTOR, correo="", id=None, rol="Sistema", puede_autorizar_omisiones=lambda: False,
                           puede_ver_informe_medico=lambda: False)


def _nombre(p: Postulacion) -> str:
    from ..routers.candidatos import nombre_ficha

    n = (nombre_ficha(p) or "").split(" ")[0]
    return "" if not n or n.startswith("candidato") or n.startswith("Candidato") else n


def lista_es(nombres: List[str]) -> str:
    nombres = [n for n in nombres if n]
    return nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + f" y {nombres[-1]}"


def texto_dias(dias: List[date]) -> str:
    textos = [f"el {subic.texto_dia(d)}" for d in dias]
    return textos[0] if len(textos) == 1 else ", ".join(textos[:-1]) + f" o {textos[-1]}"


def texto_horas(libres: List[dict]) -> str:
    textos = [f"a las {subic.texto_hora(h['hora'])}" for h in libres]
    return textos[0] if len(textos) == 1 else ", ".join(textos[:-1]) + f" o {textos[-1]}"


# ============================================================ disparo (motor de ruta)

def pendiente(p: Postulacion, pasos: Optional[List[dict]] = None) -> Optional[List[dict]]:
    """El bloque (una actividad o varias con «misma cita») que el candidato debe agendar ya. None si no hay."""
    if not p.activa or not sproc.tiene_proceso(p):
        return None
    pasos = pasos if pasos is not None else sproc.estado_pasos(p)
    actual = sproc._indice(p.etapa)
    for x in pasos:
        if (x.get("autoagenda") and x["disponible"] and x["estado"] == "pendiente" and not x["heredado"]
                and sproc._indice(x["etapa"]) <= actual):
            ids = [g["id"] for g in sproc.grupo_de(p, x["id"])]
            return [y for y in pasos if y["id"] in ids]
    return None


async def revisar(db: Session, p: Postulacion) -> bool:
    """Motor de ruta: si hay un bloque por autoagendar y nada se ha ofrecido, ofrece los días. Reclama ANTES de enviar
    (commit). Regresa True si mandó la oferta."""
    if not p.activa or not p.consentimiento or ((p.analisis or {}).get("motor_ruta") or {}).get("pausado"):
        return False
    e = estado(p)
    if e.get("fase") in ("dia", "hora") or e.get("error"):
        return False
    pasos = sproc.estado_pasos(p)
    if sproc.bloqueo_no_aprobada(p, pasos) is not None:
        return False
    bloque = pendiente(p, pasos)
    if not bloque:
        return False
    ids = [x["id"] for x in bloque]
    if e.get("fase") == "agendada" and e.get("pasos") == ids and subic.reserva_activa(db, p) is not None:
        return False
    u = subic.ubicacion_de(db, p)
    if u is None:
        _guardar(p, {"error": "El candidato no ha elegido ubicación (o la vacante no tiene ubicaciones)", "pasos": ids})
        db.commit()
        return False
    dias = subic.dias_disponibles(db, u)
    if not dias:
        _guardar(p, {"error": f"Sin cupo en {u.nombre} en los próximos días", "pasos": ids, "ubicacion_id": u.id})
        registrar(db, ACTOR, "autoagenda_sin_cupo", "postulacion", p.codigo, {"ubicacion": u.nombre, "pasos": ids})
        db.commit()
        return False
    _guardar(p, {"fase": "dia", "pasos": ids, "ubicacion_id": u.id, "dias": [d.isoformat() for d in dias],
                 "en": datetime.now(timezone.utc).isoformat()})
    db.commit()  # reclamada: nunca se ofrece dos veces
    texto = oferta_dias(p, u, [x["nombre"] for x in bloque], dias)
    from .motor_ruta import _avisar

    entregado, detalle = await _avisar(db, p, texto, f"Agenda tu cita para {p.vacante.titulo if p.vacante else 'tu proceso'}", "",
                                       motivo="autoagenda", paso_id=ids[0], actor=ACTOR)
    registrar(db, ACTOR, "autoagenda_ofrecida", "postulacion", p.codigo,
              {"pasos": ids, "ubicacion": u.nombre, "dias": [d.isoformat() for d in dias], "entregado": entregado, "detalle": detalle})
    db.commit()
    return True


def oferta_dias(p: Postulacion, u: Ubicacion, actividades: List[str], dias: List[date], reagenda: bool = False) -> str:
    n = _nombre(p)
    que = (f"tu {actividades[0][0].lower() + actividades[0][1:]}" if len(actividades) == 1
           else f"tu cita para: {lista_es(actividades)}")
    inicio = "Sin problema" + (f", {n}" if n else "") + ". Te ofrezco otras fechas." if reagenda else \
        (f"{n}, el" if n else "El") + f" siguiente paso es {que} en {u.nombre}."
    return f"{inicio} ¿Qué día puedes venir? Tengo lugar {texto_dias(dias)}."


# ============================================================ chat

def _hora_en(texto: str, libres: List[dict]) -> Optional[dict]:
    t = _norm(texto)
    for palabra, n in _NUM.items():
        t = re.sub(rf"\b{palabra}\b", str(n), t)
    if re.search(r"\bmedio ?dia\b", t):
        return next((h for h in libres if h["hora"] == "12:00"), None)
    nums = [int(m.group(1)) for m in re.finditer(r"\b(\d{1,2})(?::\d{2})?\b", t)]
    for n in nums:
        for h in libres:
            hh = int(h["hora"][:2])
            if n == hh or (n < 12 and n + 12 == hh and not re.search(r"\b(am|a m|manana)\b", t)):
                return h
    if re.search(r"\b(manana|temprano)\b", t) and not re.search(r"\bpasado manana\b", t):
        man = [h for h in libres if int(h["hora"][:2]) < 12]
        if len(man) == 1:
            return man[0]
    if re.search(r"\btarde\b", t):
        tar = [h for h in libres if int(h["hora"][:2]) >= 12]
        if len(tar) == 1:
            return tar[0]
    return None


def _dia_en(texto: str, dias: List[date], hoy: date) -> Optional[date]:
    t = _norm(texto)
    # sin las horas («a las 9», «9 am», «12:00») para no confundirlas con el número del día
    sin_horas = re.sub(r"\b(a las|a la|las)\s+\d{1,2}(:\d{2})?\b|\b\d{1,2}(:\d{2})\s*\b|\b\d{1,2}\s*(am|pm|a m|p m)\b", " ", t)
    if "pasado manana" in t:
        d = hoy + timedelta(days=2)
        return d if d in dias else None
    if re.search(r"\bmanana\b", t.replace("de la manana", "").replace("en la manana", "")):
        d = hoy + timedelta(days=1)
        if d in dias:
            return d
    por_nombre = [d for d in dias if re.search(rf"\b{_DIAS[d.weekday()]}\b", t)]
    if len(por_nombre) == 1:
        return por_nombre[0]
    nums = [int(m.group(1)) for m in re.finditer(r"\b(\d{1,2})\b", sin_horas)]
    por_numero = [d for d in dias if d.day in nums]
    if len(por_numero) == 1:
        return por_numero[0]
    if re.search(r"\b(el primero|la primera|primer dia|el primer)\b", t):
        return dias[0]
    return None


async def _responder(db: Session, p: Postulacion, texto: str, canal: str, **extra) -> dict:
    from ..routers.candidatos import _enviar_whatsapp, guardar_mensaje

    envio = await _enviar_whatsapp(p, texto, canal)
    guardar_mensaje(db, p, "assistant", texto, canal, envio)
    db.commit()
    return {"respuesta": texto, "clasificacion": None, "ia": False, "whatsapp": envio, "autoagenda": True, **extra}


def quiere_reagendar(db: Session, p: Postulacion, texto: str) -> bool:
    """«No puedo ir», «reagendar», «cambiar mi cita»… y el candidato tiene una cita autoagendada a futuro."""
    if not _RE_REAGENDAR.search(_norm(texto)):
        return False
    r = subic.reserva_activa(db, p)
    return r is not None


async def turno(db: Session, p: Postulacion, texto: str, canal: str) -> dict:
    """Mensaje del candidato mientras elige día/hora, o para reagendar una cita autoagendada."""
    if not esperando(p) and quiere_reagendar(db, p, texto):
        return await reagendar(db, p, canal)
    e = estado(p)
    u = db.get(Ubicacion, e.get("ubicacion_id")) if e.get("ubicacion_id") else None
    if u is None:
        _guardar(p, {**e, "fase": None, "error": "La ubicación ya no existe"})
        return await _responder(db, p, "Gracias. En breve una persona del equipo de RH te contacta para agendar tu cita.", canal)
    hoy = datetime.now(timezone.utc).astimezone(TZ_ORG).date()
    dias = [d for d in (date.fromisoformat(x) for x in e.get("dias") or []) if subic.horarios_libres(db, u, d)]
    if not dias:
        dias = subic.dias_disponibles(db, u)
        if not dias:
            _guardar(p, {**e, "fase": None, "error": f"Sin cupo en {u.nombre} en los próximos días"})
            return await _responder(db, p, "Por ahora no tengo lugares disponibles. Una persona del equipo de RH te contactará para "
                                         "agendar tu cita.", canal)
        e["dias"] = [d.isoformat() for d in dias]
    dia = _dia_en(texto, dias, hoy)
    if dia is None and e.get("fase") == "hora" and e.get("dia"):
        dia = date.fromisoformat(e["dia"])
        if dia not in dias:
            # se llenó mientras el candidato elegía la hora
            _guardar(p, {**e, "fase": "dia", "dia": None})
            return await _responder(db, p, f"Ese día ya se llenó. Tengo lugar {texto_dias(dias)}. ¿Qué día puedes venir?", canal)
    if dia is None:
        _guardar(p, {**e, "fase": "dia"})
        return await _responder(db, p, f"¿Qué día puedes venir? Tengo lugar {texto_dias(dias)}.", canal)
    libres = subic.horarios_libres(db, u, dia)
    hora = _hora_en(texto, libres) if (e.get("fase") == "hora" or re.search(r"\d|medio ?dia|manana|tarde", _norm(texto))) else None
    if e.get("fase") == "dia" and hora is not None and not re.search(r"\b(a las|a la|am|pm|hrs?|horas?|medio ?dia)\b|\d:\d", _norm(texto)):
        hora = None  # «el 20» es el día, no las 8 p.m.
    if hora is None:
        _guardar(p, {**e, "fase": "hora", "dia": dia.isoformat()})
        return await _responder(db, p, f"El {subic.texto_dia(dia)} tengo lugar {texto_horas(libres)}. ¿A qué hora puedes?", canal)
    return await confirmar(db, p, u, dia, hora, e, canal)


async def confirmar(db: Session, p: Postulacion, u: Ubicacion, dia: date, hora: dict, e: dict, canal: str) -> dict:
    """Reserva el lugar, crea cada evaluación del bloque (avisa a cada evaluador) y manda UNA confirmación."""
    ids = list(e.get("pasos") or [])
    r = subic.reservar(db, p, u, hora["inicio"], ids)
    if r is None:
        libres = subic.horarios_libres(db, u, dia)
        _guardar(p, {**e, "fase": "hora" if libres else "dia", "dia": dia.isoformat()})
        db.commit()
        if libres:
            return await _responder(db, p, f"Ese horario se acaba de llenar. El {subic.texto_dia(dia)} todavía tengo lugar "
                                           f"{texto_horas(libres)}. ¿A qué hora puedes?", canal)
        dias = subic.dias_disponibles(db, u)
        _guardar(p, {**e, "fase": "dia", "dias": [d.isoformat() for d in dias]})
        return await _responder(db, p, f"Ese día ya se llenó. Tengo lugar {texto_dias(dias)}. ¿Qué día puedes venir?", canal)
    cita = {"fecha": dia.isoformat(), "hora": hora["hora"], "modalidad": "Presencial", "direccion": u.direccion or u.nombre,
            "instrucciones": u.indicaciones or None}
    _poner_cita_en_pasos(p, ids, cita)
    _guardar(p, {"fase": "agendada", "pasos": ids, "ubicacion_id": u.id, "reserva_id": r.id, "inicio": hora["inicio"].isoformat(),
                 "en": datetime.now(timezone.utc).isoformat()})
    db.commit()
    nombres = [x["nombre"] for x in (p.proceso or {}).get("pasos", []) if x["id"] in ids]
    codigos, avisos = await _crear_evaluaciones(db, p, ids, cita)
    r = db.get(ReservaCita, r.id)
    r.evaluaciones = codigos
    p = db.get(Postulacion, p.id)
    registrar(db, ACTOR, "autoagenda_confirmada", "postulacion", p.codigo,
              {"ubicacion": u.nombre, "inicio": hora["inicio"].isoformat(), "pasos": ids, "evaluaciones": codigos, "avisos": avisos})
    db.commit()
    return await _responder(db, p, texto_confirmacion(u, dia, hora["hora"], nombres), canal, agendada=True, evaluaciones=codigos)


_FEMENINOS = {"terminal", "planta", "sucursal", "sede", "oficina", "bodega", "nave", "prueba", "evaluacion", "entrevista",
              "capacitacion", "induccion", "psicometria"}


def _sin_acentos(t: str) -> str:
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def con_articulo(nombre: str) -> str:
    """«Terminal Monterrey» → «la Terminal Monterrey»; «CEDIS Norte» → «el CEDIS Norte»."""
    n = (nombre or "").strip()
    if not n or _sin_acentos(n).split(" ")[0] in ("la", "el", "los", "las"):
        return n
    return f"{'la' if _sin_acentos(n).split(' ')[0] in _FEMENINOS else 'el'} {n}"


def frase_actividad(nombre: str) -> str:
    """«Prueba de manejo» → «la prueba de manejo»; «Entrevista con el sindicato» → «una entrevista con el sindicato»;
    «Examen médico» → «el examen médico»."""
    n = (nombre or "").strip()
    if not n:
        return ""
    primera = _sin_acentos(n).split(" ")[0]
    minus = n[:1].lower() + n[1:] if not n[:2].isupper() else n
    if primera == "entrevista":
        return f"una {minus}"
    return f"{'la' if primera in _FEMENINOS or primera.endswith('a') else 'el'} {minus}"


def texto_confirmacion(u: Ubicacion, dia: date, hora: str, actividades: List[str]) -> str:
    """2026-10-15 (Fletes): «Perfecto, te esperamos el [día] a las [hora] en la Terminal [ciudad], [dirección]. Mapa: [liga].
    Ese día harás la prueba de manejo, una entrevista con el jefe de operadores y una entrevista con el sindicato. Trae tu
    licencia y tu apto médico originales.» (las indicaciones de la ubicación van al final)."""
    partes = [f"Perfecto, te esperamos el {subic.texto_dia(dia)} a las {subic.texto_hora(hora)} en {con_articulo(u.nombre)}"
              + (f", {u.direccion.strip().rstrip('.')}." if (u.direccion or "").strip() else ".")]
    partes.append(f"Mapa: {subic.liga_mapa(u)}.")
    if len(actividades) > 1:
        partes.append(f"Ese día harás {lista_es([frase_actividad(x) for x in actividades])}.")
    if (u.indicaciones or "").strip():
        partes.append(u.indicaciones.strip().rstrip(".") + ".")
    return " ".join(partes)


def _poner_cita_en_pasos(p: Postulacion, ids: List[str], cita: Optional[dict]) -> None:
    """La cita queda en la configuración de cada actividad del bloque (copia de la postulación): si una evaluación no se
    pudo crear (p. ej. falta el evaluador), RH solo completa ese dato y «Iniciar» usa esta misma cita."""
    proceso = dict(p.proceso or {})
    pasos = []
    for x in proceso.get("pasos") or []:
        if x["id"] in ids:
            config = dict(x.get("config") or {})
            if cita:
                config["cita"] = cita
            else:
                config.pop("cita", None)
            x = {**x, "config": config}
        pasos.append(x)
    proceso["pasos"] = pasos
    p.proceso = proceso
    flag_modified(p, "proceso")


async def _crear_evaluaciones(db: Session, p: Postulacion, ids: List[str], cita: dict) -> tuple:
    """Una evaluación por actividad (cada evaluador recibe SU aviso con SU liga, cascada del Cambio 46). Al candidato no
    se le manda nada aquí: recibe la confirmación única."""
    from fastapi import HTTPException

    from . import actividades

    codigos, avisos = [], []
    sin_candidato = {"candidato_correo": False, "candidato_whatsapp": False}
    for pid in ids:
        p = db.get(Postulacion, p.id)
        paso = sproc._paso(p, pid)
        try:
            if paso["tipo"] == "induccion":
                codigo = await _crear_capacitacion(db, p, paso, cita, sin_candidato)
                if codigo:
                    codigos.append(codigo)
                continue
            r = await actividades.iniciar(db, p, pid, _actor(), p.cuenta, {"cita": cita, "notificar": sin_candidato})
        except HTTPException as ex:
            avisos.append({"paso": pid, "error": str(ex.detail)[:200]})
            db.rollback()
            continue
        except Exception as ex:  # noqa: BLE001 — una actividad que truene no deja sin cita a las demás
            avisos.append({"paso": pid, "error": f"{type(ex).__name__}: {str(ex)[:200]}"})
            db.rollback()
            continue
        if r.get("evaluacion"):
            codigos.append(r["evaluacion"])
        if r.get("faltan"):
            avisos.append({"paso": pid, "faltan": r["faltan"], "mensaje": r.get("mensaje")})
    return codigos, avisos


async def _crear_capacitacion(db: Session, p: Postulacion, paso: dict, cita: dict, notificar: dict) -> Optional[str]:
    """Capacitación presencial (Cambio 30): evaluación «otra» ligada a la actividad, con el instructor configurado."""
    from ..routers.evaluaciones import CitaIn, CrearEvaluacionIn, EvaluadorIn, crear_evaluacion
    from .notificaciones import NotificarIn

    ev = (paso.get("config") or {}).get("evaluador")
    cuerpo = CrearEvaluacionIn(tipo="otra", nombre=paso["nombre"], forma="asignada" if ev else "registro_directo", paso_id=paso["id"],
                               evaluador=EvaluadorIn(**ev) if ev else None,
                               cita=CitaIn(**{k: v for k, v in cita.items() if k in CitaIn.model_fields}), notificar=NotificarIn(**notificar))
    r = await crear_evaluacion(p.codigo, cuerpo, db=db, u=_actor(), cuenta=p.cuenta)
    return (r.get("evaluacion") or {}).get("codigo")


async def reagendar(db: Session, p: Postulacion, canal: str) -> dict:
    """Cancela la cita autoagendada (libera el lugar y avisa a cada evaluador) y ofrece nuevas fechas."""
    from . import evaluaciones as sev

    r = subic.reserva_activa(db, p)
    if r is None:
        return await _responder(db, p, "No encuentro una cita próxima a tu nombre. Una persona del equipo de RH te contactará.", canal)
    u = db.get(Ubicacion, r.ubicacion_id)
    subic.cancelar(r, "El candidato pidió reagendar")
    for codigo in r.evaluaciones or []:
        ev = db.query(Evaluacion).filter(Evaluacion.codigo == codigo).first()
        if ev is None or ev.estado not in ("pendiente", "no_realizada"):
            continue
        try:
            sev.cambiar_estado(db, ev, "cancelada", ACTOR, motivo="El candidato pidió reagendar su cita")
            await sev.notificar(db, ev, p, "evaluacion_cancelada", ACTOR, audiencias={"entrevistador"})
        except Exception as ex:  # noqa: BLE001
            print(f"[autoagenda] {p.codigo}: no se pudo cancelar {codigo} — {ex}", flush=True)
    ids = list(r.pasos or [])
    _poner_cita_en_pasos(p, ids, None)
    dias = subic.dias_disponibles(db, u) if u is not None else []
    registrar(db, ACTOR, "autoagenda_reagendar", "postulacion", p.codigo,
              {"reserva": r.id, "inicio": r.inicio.isoformat() if r.inicio else "", "evaluaciones": list(r.evaluaciones or [])})
    if not dias:
        _guardar(p, {"error": "Sin cupo para reagendar", "pasos": ids, "ubicacion_id": r.ubicacion_id})
        db.commit()
        return await _responder(db, p, "Cancelé tu cita. Por ahora no tengo lugares disponibles; una persona del equipo de RH te "
                                       "contactará para darte una nueva fecha.", canal, reagendada=True)
    _guardar(p, {"fase": "dia", "pasos": ids, "ubicacion_id": u.id, "dias": [d.isoformat() for d in dias],
                 "en": datetime.now(timezone.utc).isoformat(), "reagenda": True})
    db.commit()
    nombres = [x["nombre"] for x in (p.proceso or {}).get("pasos", []) if x["id"] in ids]
    return await _responder(db, p, oferta_dias(p, u, nombres, dias, reagenda=True), canal, reagendada=True)


# ============================================================ recordatorios (24 h y 2 h)

def _reclamar(db: Session, r: ReservaCita, campo, ahora: datetime) -> bool:
    filas = (db.query(ReservaCita).filter(ReservaCita.id == r.id, campo.is_(None), ReservaCita.estado == "activa")
             .update({campo: ahora}, synchronize_session=False))
    db.commit()
    return filas == 1


def texto_recordatorio(p: Postulacion, u: Ubicacion, inicio: datetime, horas: int, actividades: List[str]) -> str:
    n = _nombre(p)
    local = inicio.astimezone(TZ_ORG)
    hora = subic.texto_hora(local.strftime("%H:%M"))
    donde = f"{u.nombre}" + (f", {u.direccion.strip().rstrip('.')}" if (u.direccion or "").strip() else "")
    if horas == 2:
        texto = f"Hola{', ' + n if n else ''}. Tu cita es hoy a las {hora} en {donde}. Te esperamos. Mapa: {subic.liga_mapa(u)}"
    else:
        hoy = datetime.now(timezone.utc).astimezone(TZ_ORG).date()
        cuando = "mañana" if local.date() == hoy + timedelta(days=1) else f"el {subic.texto_dia(local.date())}"
        texto = (f"Hola{', ' + n if n else ''}. Te recordamos tu cita {cuando} a las {hora} en {donde}. Mapa: {subic.liga_mapa(u)}. "
                 "Si no puedes asistir, respóndeme y te ofrezco otra fecha.")
    if len(actividades) > 1:
        texto += f"\n\nEse día harás: {lista_es(actividades)}."
    return texto


async def recordatorios(db: Session, ahora: Optional[datetime] = None) -> dict:
    """Una pasada: recordatorio de 24 h (cita dentro de las próximas 24 h y a más de 2 h) y de 2 h."""
    from .motor_ruta import _avisar

    ahora = ahora or datetime.now(timezone.utc)
    cuenta = {"24h": 0, "2h": 0}
    try:
        reservas = (db.query(ReservaCita).filter(ReservaCita.estado == "activa", ReservaCita.inicio > ahora,
                                                 ReservaCita.inicio <= ahora + timedelta(hours=24)).all())
    except Exception:  # noqa: BLE001 — tabla del paso NO fatal sin crear
        return cuenta
    for r in reservas:
        p = db.get(Postulacion, r.postulacion_id)
        u = db.get(Ubicacion, r.ubicacion_id)
        if p is None or u is None or not p.activa:
            continue
        faltan = r.inicio - ahora
        if faltan <= timedelta(hours=2):
            campo, horas, clave = ReservaCita.recordatorio_2_en, 2, "2h"
        elif r.recordatorio_24_en is None and (r.creada_en is None or r.creada_en <= r.inicio - timedelta(hours=24)):
            # agendada con más de 24 h de anticipación (si se agendó dentro de esas 24 h, la confirmación acaba de salir)
            campo, horas, clave = ReservaCita.recordatorio_24_en, 24, "24h"
        else:
            continue
        if not _reclamar(db, r, campo, ahora):
            continue
        nombres = [x["nombre"] for x in (p.proceso or {}).get("pasos", []) if x["id"] in (r.pasos or [])]
        texto = texto_recordatorio(p, u, r.inicio, horas, nombres)
        entregado, detalle = await _avisar(db, p, texto, "Recordatorio de tu cita", "", motivo="recordatorio_cita", actor=ACTOR)
        registrar(db, ACTOR, f"autoagenda_recordatorio_{clave}", "postulacion", p.codigo,
                  {"reserva": r.id, "inicio": r.inicio.isoformat(), "entregado": entregado, "detalle": detalle})
        db.commit()
        cuenta[clave] += 1
    return cuenta


async def job_recordatorios() -> None:
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        r = await recordatorios(db)
        if r["24h"] or r["2h"]:
            print(f"[autoagenda] recordatorios de cita enviados: {r}", flush=True)
    except Exception as ex:  # noqa: BLE001
        db.rollback()
        print(f"[autoagenda] recordatorios: {ex}", flush=True)
    finally:
        db.close()
