"""Cambio 30 (2026-10-13) — «Oferta laboral»: une «Propuesta y aceptación» y «Carta de intención».

Estado en `Postulacion.analisis["oferta"]`: {token, datos (carta), generada_en/por, enviada_en/por, entregada,
respuesta: acepta | no_acepta, respondida_en, canal, anteriores[]}. La actividad se cumple cuando el candidato ACEPTA; si
no acepta queda «Oferta no aceptada» (no favorable: la ruta se detiene y RH decide, nunca descarta). Las rutas congeladas
con «Propuesta y aceptación» siguen con su flujo (`services/propuesta.py`); una propuesta aceptada también cumple la
oferta.
"""

import re
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import fechas
from ..config import settings
from ..models import Postulacion, registrar

CLAVE = "oferta"
ACTOR = "Red Human (oferta)"


def estado(p: Postulacion) -> dict:
    v = (p.analisis or {}).get(CLAVE)
    return dict(v) if isinstance(v, dict) else {}


def _fecha(texto) -> Optional[object]:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(texto).replace("Z", "+00:00")) if texto else None
    except ValueError:
        return None


def estado_paso(base: dict, p: Postulacion) -> dict:
    """Estado DERIVADO de la actividad «Oferta laboral»."""
    o = estado(p)
    if o.get("respuesta") == "acepta":
        return {**base, "estado": "completada", "resultado": "favorable", "cumple": True, "detalle": "Oferta aceptada",
                "revisadoPor": "Respuesta del candidato" if o.get("canal") != "rh" else "Revisado por: RH",
                "terminado_en": _fecha(o.get("respondida_en"))}
    if o.get("respuesta") == "no_acepta":
        return {**base, "estado": "completada", "resultado": "no_favorable", "cumple": False, "detalle": "Oferta no aceptada",
                "texto_no_aprobada": "Oferta no aceptada", "revisadoPor": "Respuesta del candidato",
                "terminado_en": _fecha(o.get("respondida_en"))}
    prop = (p.analisis or {}).get("propuesta") or {}
    if prop.get("estado") == "rechazada":
        return {**base, "estado": "completada", "resultado": "no_favorable", "cumple": False, "detalle": "Oferta no aceptada",
                "texto_no_aprobada": "Oferta no aceptada", "revisadoPor": "Respuesta del candidato"}
    if prop.get("estado") == "aceptada":  # flujo anterior (chat): también cumple la oferta
        return {**base, "estado": "completada", "resultado": "favorable", "cumple": True, "detalle": "Propuesta aceptada",
                "revisadoPor": "Respuesta del candidato"}
    if o.get("enviada_en") or prop.get("estado") == "enviada":
        return {**base, "estado": "en_curso", "espera": "Esperando la respuesta del candidato a la oferta",
                "cuello": {"clave": "esperando_candidato", "texto": "Esperando respuesta del candidato", "quien": None}}
    if p.etapa not in ("Contratación", "Onboarding"):
        return {**base, "espera": "La oferta se envía al llegar a Contratación"}
    return {**base, "espera": "Falta generar y enviar la oferta laboral",
            "cuello": {"clave": "pendiente_condiciones", "texto": "Pendiente: enviar oferta", "quien": None}}


# ============================================================ carta oferta (Bloque 2)

CAMPOS = ("puesto", "empresa", "sueldo", "ubicacion", "modalidad", "horario", "prestaciones", "fecha_ingreso", "tipo_contratacion",
          "jefe_directo", "mensaje")


def _guardar(p: Postulacion, datos: dict) -> None:
    a = dict(p.analisis or {})
    a[CLAVE] = datos
    p.analisis = a
    flag_modified(p, "analisis")


def precarga(p: Postulacion) -> dict:
    """La carta oferta se genera con los datos de la VACANTE (lo que ya esté en el expediente manda). Red Human nunca
    inventa condiciones: lo que no esté capturado queda vacío."""
    from ..serial import nombre_empresa_candidato

    v, e = p.vacante, p.expediente
    o = estado(p)
    if o.get("datos"):
        return dict(o["datos"])

    def de(exp_val, vac_val):
        return (exp_val or "").strip() or (vac_val or "").strip()

    fecha = fechas.dia(e.fecha_ingreso).isoformat() if e and e.fecha_ingreso else ""
    return {
        "puesto": de(e.puesto if e else "", v.titulo if v else ""),
        "empresa": de(e.empresa if e else "", nombre_empresa_candidato(v) if v else ""),
        "sueldo": sueldo_exacto(de(e.sueldo if e else "", (v.sueldo if v and v.sueldo != "A convenir" else "") if v else "")),
        "ubicacion": de(e.ubicacion if e else "", v.ubicacion if v else ""),
        "modalidad": (v.modalidad if v else "") or "",
        "horario": "",
        "prestaciones": ", ".join(v.beneficios or []) if v else "",
        "fecha_ingreso": fecha,
        "tipo_contratacion": (e.tipo_contratacion if e else "") or "",
        "jefe_directo": (e.jefe_directo if e else "") or "",
        "mensaje": "",
    }


def limpiar(datos: Optional[dict], base: dict) -> dict:
    datos = datos if isinstance(datos, dict) else {}
    salida = {}
    for k in CAMPOS:
        valor = str(datos.get(k, base.get(k, "")) or "")
        salida[k] = valor.strip()[:1500] if k == "mensaje" else " ".join(valor.split())[:300]
    return salida


def liga(o: dict) -> str:
    return f"{settings.app_url}/oferta/{o['token']}" if o.get("token") else ""


def nombre_corto(p: Postulacion) -> str:
    from ..routers.candidatos import nombre_ficha

    nombre = nombre_ficha(p) or ""
    return "" if nombre.startswith("Candidato") else nombre.split(" ")[0]


def texto_aviso(p: Postulacion, url: str) -> str:
    """Mensaje 1 (2026-10-16, QA — texto definido por el usuario)."""
    nombre = nombre_corto(p)
    return (f"¡Buenas noticias{', ' + nombre if nombre else ''}! Fuiste seleccionado para el puesto. Revisa aquí tu sueldo, "
            f"horario y fecha de inicio para confirmar tu contratación: {url} ¡Gracias!")


_RE_DESDE = re.compile(r"^\s*desde\s+", re.IGNORECASE)


def sueldo_exacto(texto: str) -> str:
    """2026-10-16 (QA): en la oferta el sueldo va con la cifra exacta, sin «Desde»: «$8,000 MXN mensuales». Un número
    suelto se formatea; un texto ya formateado solo pierde el «Desde»."""
    t = _RE_DESDE.sub("", (texto or "").strip())
    if not t or t.lower() == "a convenir":
        return ""
    solo = re.sub(r"[,\s$]", "", t)
    if re.fullmatch(r"\d+(?:\.\d+)?", solo):
        return f"${int(float(solo)):,} MXN mensuales"
    return t


def _fecha_larga(valor: str) -> str:
    try:
        d = datetime.fromisoformat(str(valor)[:10])
    except (TypeError, ValueError):
        return str(valor or "")
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"{d.day} de {meses[d.month - 1]} de {d.year}"


def texto_propuesta_formal(nombre: str, empresa: str, puesto: str, sueldo: str, ubicacion: str, fecha: str, tipo: str, jefe: str) -> str:
    """Mensaje 2 (2026-10-16, QA — texto definido por el usuario). Los datos que no estén capturados se omiten (nunca se
    inventan condiciones)."""
    partes = [f"Hola{' ' + nombre if nombre else ''}, ¡felicidades! Esta es tu propuesta formal{' en ' + empresa if empresa else ''}:"]
    if puesto:
        partes.append(f"Puesto: {puesto}")
    if sueldo:
        partes.append(f"Sueldo: {sueldo}")
    if ubicacion:
        partes.append(f"Ubicación: {ubicacion}.")
    if fecha:
        partes.append(f"Fecha de ingreso: {fecha}")
    if tipo:
        partes.append(f"Tipo de puesto: {tipo}")
    if jefe:
        partes.append(f"Jefe directo: {jefe}.")
    partes.append("¿Estás de acuerdo? Responde Sí para confirmar tu lugar y enviarte tu contrato para firma.")
    return " ".join(partes)


def texto_mensaje_2(p: Postulacion, carta: dict) -> str:
    return texto_propuesta_formal(nombre_corto(p), carta.get("empresa", ""), carta.get("puesto", ""), sueldo_exacto(carta.get("sueldo", "")),
                                  carta.get("ubicacion", ""), _fecha_larga(carta["fecha_ingreso"]) if carta.get("fecha_ingreso") else "",
                                  carta.get("tipo_contratacion", ""), carta.get("jefe_directo", ""))


# ============================================================ respuesta por el chat (2026-10-16, QA)

# «ok», «va» o «sale» solos son acuses, no una aceptación (mismo criterio que la agenda): se repite la pregunta
_RE_ACEPTA = re.compile(r"\b(si|sii+|sip|simon|claro|acepto|aceptar|aceptamos|de acuerdo|deacuerdo|"
                        r"perfecto|por supuesto|desde luego|confirmo|me interesa|estoy de acuerdo|con gusto|adelante)\b")
_RE_RECHAZA = re.compile(r"^(no|nop|nel|nah)\b|\bno (acepto|me interesa|estoy de acuerdo|gracias|puedo|quiero)\b|\brechazo\b|\bdeclino\b")


def _norm(texto: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", " ", t).strip()


def clasificar_respuesta(texto: str) -> Optional[bool]:
    """True = acepta, False = no acepta, None = no se entiende. 👍 cuenta como sí."""
    if "👍" in (texto or "") or "✅" in (texto or ""):
        return True
    t = _norm(texto)
    if _RE_RECHAZA.search(t):
        return False
    if _RE_ACEPTA.search(t):
        return True
    return None


def esperando_respuesta(p: Postulacion) -> bool:
    """La oferta ya salió y el candidato no ha respondido: su siguiente mensaje es la respuesta a la oferta."""
    o = estado(p)
    return bool(p.activa and o.get("enviada_en") and not o.get("respuesta"))


async def turno(db: Session, p: Postulacion, texto_candidato: str, canal: str) -> dict:
    """Mensaje del candidato mientras la oferta espera respuesta. «Sí» → Oferta aceptada y la ruta sigue DE INMEDIATO a
    «Contrato y firma» (antes el «Sí» se iba a otra postulación o mandaba la liga de la entrevista). «No» → Oferta no
    aceptada (aviso a RH, nunca descarta). Otra cosa → se repite la pregunta."""
    from ..routers.candidatos import _enviar_whatsapp, guardar_mensaje

    decision = clasificar_respuesta(texto_candidato)
    if decision is None:
        msg = "Para continuar necesito tu respuesta a la oferta. ¿Estás de acuerdo? Responde Sí para confirmar tu lugar."
        envio = await _enviar_whatsapp(p, msg, canal)
        guardar_mensaje(db, p, "assistant", msg, canal, envio)
        db.commit()
        return {"respuesta": msg, "oferta": "enviada", "clasificacion": None, "ia": False, "whatsapp": envio}
    r = await responder(db, p, decision, canal="whatsapp", por=p.nombre or "candidato", texto=texto_candidato)
    return {"respuesta": r.get("mensaje", ""), "oferta": r["respuesta"], "clasificacion": None, "ia": False}


def publica(p: Postulacion) -> Optional[dict]:
    """Resumen para RH (ficha y tablero)."""
    o = estado(p)
    if not o:
        return None
    resp = o.get("respuesta") or ""
    return {"estado": {"acepta": "aceptada", "no_acepta": "no_aceptada"}.get(resp, "enviada" if o.get("enviada_en") else "generada"),
            "enviadaEn": o.get("enviada_en"), "enviadaPor": o.get("enviada_por"), "entregada": o.get("entregada"),
            "respondidaEn": o.get("respondida_en"), "liga": liga(o), "datos": o.get("datos") or {},
            "enRuta": o.get("en_ruta", True)}


def vista_candidato(p: Postulacion) -> dict:
    """La carta que ve el candidato en `/oferta/{token}` (sin datos internos)."""
    from ..routers.candidatos import nombre_ficha

    o = estado(p)
    return {"candidato": nombre_ficha(p), "datos": o.get("datos") or {}, "respuesta": o.get("respuesta"),
            "respondidaEn": o.get("respondida_en"), "activa": bool(p.activa)}


async def enviar(db: Session, p: Postulacion, actor: str, datos: Optional[dict] = None, en_ruta: bool = True) -> dict:
    """Genera la carta (datos de la vacante + lo que RH ajustó) y la manda: plantilla de WhatsApp META_PLANTILLA_OFERTA
    ({{1}} nombre, {{2}} liga) y, si no sale, el MISMO texto por el canal del candidato (mensajería → correo). Reenviar =
    misma liga; una respuesta previa se archiva."""
    from .mensajeria import de_cuenta
    from .motor_ruta import _avisar
    from .whatsapp import enviar_plantilla

    previo = estado(p)
    carta = limpiar(datos, precarga(p))
    anteriores = list(previo.get("anteriores") or [])
    if previo.get("respuesta"):
        anteriores.append({k: previo.get(k) for k in ("respuesta", "respondida_en", "enviada_en")})
    ahora = fechas.iso(datetime.now(timezone.utc))
    o = {"token": previo.get("token") or secrets.token_urlsafe(18), "datos": carta, "generada_en": previo.get("generada_en") or ahora,
         "enviada_en": ahora, "enviada_por": actor, "en_ruta": en_ruta, "anteriores": anteriores[-5:]}
    _guardar(p, o)
    db.commit()  # la carta y su liga quedan guardadas ANTES de enviar
    url = liga(o)
    texto = texto_aviso(p, url)
    entregado, detalle = False, ""
    plantilla = (settings.meta_plantilla_oferta or "").strip()
    if p.telefono and plantilla and settings.whatsapp_provider == "meta":
        try:
            with de_cuenta(p.cuenta_id):
                r = await enviar_plantilla(p.telefono, plantilla, [nombre_corto(p) or "Hola", url], settings.meta_plantilla_idioma, texto=texto)
            entregado = bool(r.get("enviado"))
            detalle = "whatsapp" if entregado else str(r.get("detalle") or "")[:200]
            if entregado:
                from ..routers.candidatos import guardar_mensaje

                guardar_mensaje(db, p, "assistant", texto, "whatsapp", r)
        except Exception as ex:  # noqa: BLE001 — la plantilla nunca rompe el envío: sigue el texto
            detalle = str(ex)[:200]
    if not entregado:
        entregado, detalle = await _avisar(db, p, texto, f"Oferta laboral — {carta.get('puesto') or 'vacante'}", url,
                                           motivo="oferta", actor=actor)
    o["entregada"] = bool(entregado)
    o["detalle_envio"] = str(detalle or "")[:200]
    texto_2 = ""
    if entregado and p.telefono:
        # Mensaje 2 (2026-10-16, QA): la propuesta formal con «¿Estás de acuerdo? Responde Sí…»; su respuesta en el chat
        # la atiende `turno`. Best effort: si no sale (p. ej. fuera de la ventana de 24 h), la liga del mensaje 1 basta.
        from ..routers.candidatos import guardar_mensaje
        from .whatsapp import enviar_mensaje

        texto_2 = texto_mensaje_2(p, carta)
        try:
            with de_cuenta(p.cuenta_id):
                r2 = await enviar_mensaje(p.telefono, texto_2)
            guardar_mensaje(db, p, "assistant", texto_2, "whatsapp", r2)
            o["mensaje_2"] = bool(r2.get("enviado"))
        except Exception as ex:  # noqa: BLE001
            o["mensaje_2"] = False
            print(f"[oferta] mensaje 2 de {p.codigo} no salió: {str(ex)[:200]}", flush=True)
    _guardar(p, o)
    p.historial = list(p.historial or []) + [{"evento": "oferta_enviada", "usuario": actor, "fecha": ahora,
                                              "texto": f"Oferta laboral enviada por {actor}" + ("" if entregado else f" (no se entregó: {detalle})")}]
    registrar(db, actor, "oferta_enviada", "postulacion", p.codigo, {"entregada": bool(entregado), "detalle": str(detalle)[:200], "en_ruta": en_ruta})
    db.commit()
    return {"enviada": bool(entregado), "detalle": detalle, "texto": texto, "texto2": texto_2, "liga": url}


async def responder(db: Session, p: Postulacion, acepta: bool, canal: str = "liga", por: str = "", texto: str = "") -> dict:
    """«Acepto» → la actividad se cumple y la ruta sigue. «No acepto» → «Oferta no aceptada» (aviso a RH; nunca descarta).

    2026-10-16 (QA): respondida por el CHAT, el candidato recibe el siguiente paso en el mismo turno — sus documentos de
    ingreso si faltan (liga, solo lo pendiente) o la firma del contrato — y la ruta avanza de inmediato a «Contrato y firma»."""
    from . import proceso as sproc
    from .motor_ruta import _avisar, _disparar_documentos
    from .propuesta import avisar_rh_rechazo

    o = estado(p)
    ahora_dt = datetime.now(timezone.utc)
    ahora = fechas.iso(ahora_dt)
    o.update({"respuesta": "acepta" if acepta else "no_acepta", "respondida_en": ahora, "canal": canal})
    _guardar(p, o)
    quien = por or (p.nombre or "candidato")
    p.historial = list(p.historial or []) + [{
        "evento": "oferta_aceptada" if acepta else "oferta_no_aceptada", "usuario": quien, "fecha": ahora,
        "texto": ("Oferta aceptada" if acepta else "Oferta no aceptada") + f" — {fechas.con_zona(fechas.local(ahora_dt).strftime('%d/%m/%Y %H:%M'))}"}]
    registrar(db, quien, "oferta_aceptada" if acepta else "oferta_no_aceptada", "postulacion", p.codigo, {"canal": canal})
    aviso = None if acepta else await avisar_rh_rechazo(db, p, (f"No aceptó la oferta laboral: «{texto[:200]}»" if texto else "No aceptó la oferta laboral"))
    db.commit()
    mensaje, documentos = "", None
    if canal == "whatsapp":
        if acepta:
            # los documentos de ingreso van primero SOLO si la ruta los pide en Contratación (Masivos: «Documentos de
            # ingreso» antes de «Contrato y firma»); si no, lo que sigue es el contrato
            paso_docs = next((x for x in (p.proceso or {}).get("pasos", []) if x.get("tipo") in ("documentos", "solicitud_documentos")
                              and x.get("etapa") == "Contratación" and not x.get("heredado")), None)
            faltan = list(p.expediente.pendientes) if p.expediente and paso_docs else []
            mensaje = ("Gracias, registramos tu aceptación. Siguiente paso: " +
                       ("sube tus documentos de ingreso; en seguida te mando la liga con solo los que faltan." if faltan
                        else "la firma de tu contrato; en breve te lo enviamos para firma."))
            await _avisar(db, p, mensaje, "Tu oferta laboral", "", motivo="oferta", actor=ACTOR)
            if faltan:
                ok, detalle, entregado = await _disparar_documentos(db, p, paso_docs)
                documentos = {"ok": ok, "detalle": detalle, "enviado": entregado, "pendientes": faltan}
        else:
            mensaje = "Gracias por avisarnos. Lo comparto con el equipo de RH, que se pondrá en contacto contigo."
            await _avisar(db, p, mensaje, "Tu oferta laboral", "", motivo="oferta", actor=ACTOR)
        db.commit()
    movidas = await sproc.avanzar_seguro(db, p)
    return {"respuesta": o["respuesta"], "respondidaEn": ahora, "avisoRh": aviso, "mensaje": mensaje, "documentos": documentos,
            "movidas": movidas}


def postulacion_por_token(db: Session, token: str) -> Optional[Postulacion]:
    """La postulación de una liga `/oferta/{token}` (el token vive en `analisis.oferta.token`)."""
    if not token or len(token) < 10:
        return None
    for p in db.query(Postulacion).filter(Postulacion.analisis.isnot(None)).order_by(Postulacion.id.desc()).all():
        if ((p.analisis or {}).get(CLAVE) or {}).get("token") == token:
            return p
    return None
