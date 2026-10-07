"""Evaluaciones unificadas — Fase 1 (2026-09-29, especificación de Raúl). Lógica del objeto único `Evaluacion`.

* Una evaluación = una tarjeta, un formulario, un botón principal que cambia con el estado. Tipos, formas, estados
  y conclusiones: ver `models.TIPOS_EVALUACION_U` y vecinos. La Entrevista Red Human (IA) queda fuera.
* Solo cinco estados; el consentimiento médico es una CONDICIÓN aparte. Mientras esté pendiente no se envía la liga
  al evaluador ni se guarda resultado; rechazado → la única acción es Cancelar.
* Nadie sobrescribe a nadie: todo cambio va a `eventos_evaluacion` con los valores anteriores. Si RH ya registró
  un resultado, el evaluador solo complementa. Dos guardados simultáneos → el segundo se rechaza (`resultado_version`).
* Recibir o guardar un resultado NUNCA escribe `Postulacion.etapa` ni avisa al candidato que avanza (HITL): lo único
  que toca la etapa es ASIGNAR una entrevista humana (lo hace el router, comportamiento actual).
"""

import secrets
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from .. import fechas
from ..config import settings
from ..models import (
    BASE_CODIGO_EVALUACION,
    CONSENTIMIENTOS,
    ESTADOS_EVALUACION_U,
    FORMAS_EVALUACION,
    MODALIDADES_CITA,
    PASOS_INTEGRADA,
    SEGUIMIENTO_EVALUACION,
    TIPO_DESDE_LEGADO,
    TIPOS_CON_CONSENTIMIENTO,
    TIPOS_EVALUACION_U,
    TRANSICIONES_EVALUACION,
    ClienteContacto,
    Evaluacion,
    EventoEvaluacion,
    NotificacionEnviada,
    Postulacion,
    Usuario,
    conclusiones_de,
    puede_registrar_resultado,
)

MODOS_RESULTADO = ("registrar", "corregir", "complementar")
RE_CORREO_SIMPLE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class ErrorEvaluacion(Exception):
    """Error de negocio con su código HTTP (el router lo traduce a HTTPException)."""

    def __init__(self, status: int, mensaje: str):
        super().__init__(mensaje)
        self.status = status
        self.mensaje = mensaje


# ------------------------------------------------------------ consultas


def de_postulacion(db: Session, p: Postulacion) -> List[Evaluacion]:
    """Más reciente primero (orden de las tarjetas en «Resumen»)."""
    return db.query(Evaluacion).filter(Evaluacion.postulacion_id == p.id).order_by(Evaluacion.id.desc()).all()


def por_codigo(db: Session, codigo: str, cuenta_id: int) -> Evaluacion:
    ev = db.query(Evaluacion).filter(Evaluacion.codigo == codigo, Evaluacion.cuenta_id == cuenta_id).first()
    if not ev:
        raise ErrorEvaluacion(404, "Evaluación no encontrada.")
    return ev


def por_token(db: Session, token: str) -> Evaluacion:
    ev = db.query(Evaluacion).filter(Evaluacion.token_evaluador == token).first() if token else None
    if not ev:
        raise ErrorEvaluacion(404, "Esta liga no es válida.")
    return ev


def postulacion_de(db: Session, ev: Evaluacion) -> Postulacion:
    p = db.get(Postulacion, ev.postulacion_id)
    if not p:
        raise ErrorEvaluacion(404, "La postulación de esta evaluación ya no existe.")
    return p


def eventos_de(db: Session, ev: Evaluacion) -> List[EventoEvaluacion]:
    return db.query(EventoEvaluacion).filter(EventoEvaluacion.evaluacion_id == ev.id).order_by(EventoEvaluacion.id).all()


def ultima_entrevista_humana(db: Session, p: Postulacion, con_resultado: bool = False) -> Optional[Evaluacion]:
    q = db.query(Evaluacion).filter(Evaluacion.postulacion_id == p.id, Evaluacion.tipo == "entrevista_humana")
    if con_resultado:
        q = q.filter(Evaluacion.estado == "con_resultado")
    return q.order_by(Evaluacion.id.desc()).first()


def liga_evaluador(ev: Evaluacion) -> str:
    return f"{settings.app_url}/evaluacion/{ev.token_evaluador}"


def liga_consentimiento(ev: Evaluacion) -> str:
    return f"{settings.app_url}/consentimiento/{ev.consentimiento_token}" if ev.consentimiento_token else ""


# ------------------------------------------------------------ ligas externas (2026-10-01)
# Toda liga externa (consentimiento, evaluador/médico, otro sistema, proveedor) EXISTE aunque su envío automático
# falle: se genera al crear la evaluación (o la primera vez que se pide, si es un registro viejo) y se REUTILIZA.
# La interfaz siempre ofrece Abrir / Copiar / Enviar o reenviar, y el estado del último envío viaja aparte.

CLAVES_LIGA = ("consentimiento", "evaluador", "otro_sistema", "proveedor")


def asegurar_ligas(ev: Evaluacion) -> bool:
    """Genera los tokens que falten (nunca reemplaza uno existente). Regresa True si generó alguno."""
    cambio = False
    if not ev.token_evaluador:
        ev.token_evaluador = secrets.token_urlsafe(24)
        cambio = True
    if ev.consentimiento == "pendiente" and not ev.consentimiento_token:
        ev.consentimiento_token = secrets.token_urlsafe(24)
        cambio = True
    return cambio


def liga_evaluador_disponible(ev: Evaluacion) -> bool:
    """Liga para capturar el resultado: la del evaluador asignado y, en la médica, la «Liga del médico» (cualquier
    forma salvo proveedor integrado). Nunca mientras el consentimiento esté pendiente o rechazado."""
    if ev.estado == "cancelada" or bloqueo_consentimiento(ev):
        return False
    return ev.forma == "asignada" or (ev.tipo == "medica" and ev.forma != "integrada")


def _url_proveedor(ev: Evaluacion) -> str:
    from . import psicometricas as psi

    if not ev.clave_proveedor:
        return ""
    # 2026-10-07: la liga interceptada de la respuesta de agregaCandidato (forma integrada) manda sobre la plantilla.
    if ev.forma == "integrada" and ev.liga_externa_candidato and psi.es_liga_candidato(ev.liga_externa_candidato):
        return ev.liga_externa_candidato
    return psi.url_candidato(ev.clave_proveedor) or ""


def _filas_envio(e: EventoEvaluacion, clave: str) -> list:
    """Filas de un evento «envio» que corresponden a esa liga (los eventos viejos no traen `liga`: se deducen)."""
    d = e.detalle or {}
    filas = [f for f in (d.get("envios") or []) if isinstance(f, dict)]
    if d.get("liga"):
        return filas if d.get("liga") == clave else []
    if clave == "consentimiento":
        return filas if d.get("que") == "liga_consentimiento" else []
    if not d.get("evento"):
        return []
    if clave == "evaluador":
        return [f for f in filas if f.get("destinatario") == "entrevistador"]
    if clave == "otro_sistema":
        return [f for f in filas if f.get("destinatario") == "candidato"]
    return []


def ultimo_envio(eventos: List[EventoEvaluacion], clave: str) -> Optional[dict]:
    for e in reversed(eventos):
        if e.accion != "envio":
            continue
        filas = _filas_envio(e, clave)
        if filas:
            return {
                "fecha": fechas.iso(e.fecha), "por": e.actor or "", "enviado": any(f.get("enviado") for f in filas),
                "envios": [{"canal": f.get("canal") or "", "destino": f.get("destino") or "", "enviado": bool(f.get("enviado")),
                            "detalle": str(f.get("detalle") or "")[:200]} for f in filas],
            }
    return None


def ligas_de(db: Session, ev: Evaluacion) -> List[dict]:
    """Ligas externas vigentes de la evaluación con su último envío (nunca bloquea Abrir/Copiar)."""
    if ev.estado == "cancelada":
        return []
    p = db.get(Postulacion, ev.postulacion_id)
    contacto_candidato = bool(p and (p.telefono or p.correo))
    eventos = [e for e in eventos_de(db, ev) if e.accion == "envio"]
    salida = []

    def agregar(clave, titulo, url, para, puede, motivo=""):
        salida.append({"clave": clave, "titulo": titulo, "url": url, "para": para, "puedeEnviar": puede,
                       "motivoNoEnvio": "" if puede else motivo, "ultimoEnvio": ultimo_envio(eventos, clave)})

    if ev.consentimiento == "pendiente" and ev.consentimiento_token:
        agregar("consentimiento", "Liga de consentimiento del candidato", liga_consentimiento(ev), "Candidato",
                contacto_candidato, "El candidato no tiene teléfono ni correo registrados.")
    if liga_evaluador_disponible(ev):
        con_contacto = ev.forma == "asignada" and bool(ev.evaluador_correo or ev.evaluador_whatsapp)
        agregar("evaluador", "Liga del médico" if ev.tipo == "medica" else "Liga del evaluador", liga_evaluador(ev),
                ev.evaluador_nombre or ("Médico" if ev.tipo == "medica" else "Evaluador"), con_contacto,
                "Sin evaluador asignado con correo o WhatsApp: copia la liga y compártela tú.")
    if ev.forma == "liga_otro_sistema" and ev.liga_externa_candidato and not bloqueo_consentimiento(ev):
        agregar("otro_sistema", "Liga del otro sistema (candidato)", ev.liga_externa_candidato, "Candidato",
                contacto_candidato, "El candidato no tiene teléfono ni correo registrados.")
    url_prov = _url_proveedor(ev)
    if ev.forma == "integrada" and url_prov and not bloqueo_consentimiento(ev):
        agregar("proveedor", f"Liga de {ev.proveedor or 'proveedor'} (candidato)", url_prov, "Candidato",
                contacto_candidato, "El candidato no tiene teléfono ni correo registrados.")
    return salida


async def enviar_liga_a_candidato(db: Session, ev: Evaluacion, p: Postulacion, actor: str, clave: str, asunto: str,
                                  texto: str, liga: str, usuario_id: Optional[int] = None) -> List[dict]:
    """Manda una liga al candidato por WhatsApp y correo (lo que tenga). Un canal caído nunca rompe la acción: cada
    resultado regresa a RH y queda en el historial (también los fallidos)."""
    from ..serial import nombre_empresa_candidato
    from . import plantillas_correo
    from .correo import enviar_correo
    from .mensajeria import de_cuenta
    from .whatsapp import enviar_mensaje

    empresa = nombre_empresa_candidato(p.vacante) if p.vacante else ""
    resultados = []
    if p.telefono:
        try:
            with de_cuenta(p.cuenta_id):
                r = await enviar_mensaje(p.telefono, f"{texto} {liga}")
        except Exception as ex:  # noqa: BLE001
            r = {"enviado": False, "detalle": str(ex)[:200]}
        resultados.append({"destinatario": "candidato", "canal": "whatsapp", "destino": p.telefono, "enviado": bool(r.get("enviado")), "detalle": str(r.get("detalle") or "")})
    if p.correo:
        try:
            asunto_, html = plantillas_correo.html_aviso(asunto, texto, empresa, [], ("Abrir", liga))
            r = await enviar_correo(p.correo, asunto_, html)
        except Exception as ex:  # noqa: BLE001
            r = {"enviado": False, "detalle": str(ex)[:200]}
        resultados.append({"destinatario": "candidato", "canal": "correo", "destino": p.correo, "enviado": bool(r.get("enviado")), "detalle": str(r.get("detalle") or "")})
    if not resultados:
        resultados.append({"destinatario": "candidato", "canal": "", "destino": "", "enviado": False, "detalle": "El candidato no tiene teléfono ni correo registrados."})
    que = "liga_consentimiento" if clave == "consentimiento" else f"liga_{clave}"
    evento(db, ev, "envio", actor, usuario_id=usuario_id, que=que, liga=clave, envios=resultados)
    if clave in ("otro_sistema", "proveedor"):
        marcar_enviada(ev, resultados)
    return resultados


async def notificar_psicometria(db: Session, ev: Evaluacion, p: Postulacion, actor: str, usuario_id: Optional[int] = None,
                                evento_notificacion: str = "psicometria_enviada") -> List[dict]:
    """Psicométricas.mx (2026-10-07): Red Human es el ÚNICO que avisa al candidato — no se depende del correo del
    proveedor. Manda por su canal de mensajería activo (Telegram/WhatsApp, vía la fachada) y por correo corporativo: la
    URL del portal del sustentante, la clave de agregaCandidato y los pasos. Nunca lanza; cada resultado queda en el
    historial y marca «Enviada» con el primer envío confirmado.
    2026-10-07: los canales los decide la regla de la Cuenta (Configuración → Notificaciones): «Psicometría enviada» o
    «Recordatorio de psicometría pendiente» (`evento_notificacion`)."""
    from ..serial import nombre_empresa_candidato
    from .notificaciones import canales_candidato
    from . import plantillas_correo
    from . import psicometricas as psi
    from .correo import enviar_correo
    from .mensajeria import de_cuenta
    from .whatsapp import enviar_con_boton

    clave = ev.clave_proveedor
    liga = _url_proveedor(ev) or psi.url_candidato(clave) or psi.PORTAL_SUSTENTANTE
    empresa = nombre_empresa_candidato(p.vacante) if p.vacante else ""
    vacante = p.vacante.titulo if p.vacante else ""
    nombre = ((p.nombre or "").split(" ")[0]) if p.nombre and not p.nombre.startswith("Candidato") else ""
    recordatorio = evento_notificacion == "recordatorio_psicometria"
    canales = canales_candidato(db, p.cuenta_id, evento_notificacion)
    resultados = []
    if p.telefono and canales["whatsapp"]:
        try:
            with de_cuenta(p.cuenta_id):
                r = await enviar_con_boton(p.telefono, psi.mensaje_candidato(nombre, clave, liga, empresa, vacante, recordatorio=recordatorio),
                                           "Ir a mi evaluación", liga, cuenta_id=p.cuenta_id)
        except Exception as ex:  # noqa: BLE001
            r = {"enviado": False, "detalle": str(ex)[:200]}
        canal = r.get("proveedor") if r.get("proveedor") == "telegram" else "whatsapp"
        resultados.append({"destinatario": "candidato", "canal": canal, "destino": p.telefono, "enviado": bool(r.get("enviado")), "detalle": str(r.get("detalle") or "")})
    if p.correo and canales["correo"]:
        try:
            asunto, html = plantillas_correo.html_psicometria({
                "nombre": nombre, "empresa": empresa, "vacante": vacante, "prueba": ev.nombre_visible,
                "liga": liga, "clave": clave, "instrucciones": psi.instrucciones(clave), "recordatorio": recordatorio,
            })
            r = await enviar_correo(p.correo, asunto, html)
        except Exception as ex:  # noqa: BLE001
            r = {"enviado": False, "detalle": str(ex)[:200]}
        resultados.append({"destinatario": "candidato", "canal": "correo", "destino": p.correo, "enviado": bool(r.get("enviado")), "detalle": str(r.get("detalle") or "")})
    if not resultados:
        detalle = ("El aviso al candidato está apagado en Configuración → Notificaciones." if not (canales["whatsapp"] or canales["correo"])
                   else "El candidato no tiene teléfono ni correo registrados.")
        resultados.append({"destinatario": "candidato", "canal": "", "destino": "", "enviado": False, "detalle": detalle})
    evento(db, ev, "envio", actor, usuario_id=usuario_id, que="recordatorio_psicometria" if recordatorio else "liga_proveedor",
           liga="proveedor", envios=resultados)
    marcar_enviada(ev, resultados)
    return resultados


def marcar_enviada(ev: Evaluacion, resultados: List[dict], destinatario: Optional[str] = None) -> None:
    """Primer envío CONFIRMADO de la liga a quien realiza la evaluación (seguimiento «Enviada»)."""
    if ev.enviada_en:
        return
    if any(r.get("enviado") and (destinatario is None or r.get("destinatario") == destinatario) for r in resultados or []):
        ev.enviada_en = datetime.now(timezone.utc)


def seguimiento(ev: Evaluacion) -> tuple:
    """(clave, texto) del seguimiento visible. Psicométrica: Pendiente / Enviada / En curso / Resultado recibido /
    Revisada. Socioeconómica: Pendiente / En proceso / Resultado recibido / Revisada. Resto: el estado + «Revisada».
    No realizada y Cancelada se muestran tal cual en todos los tipos."""
    if ev.estado in ("no_realizada", "cancelada"):
        return ev.estado, ESTADOS_EVALUACION_U[ev.estado]
    etiquetas = SEGUIMIENTO_EVALUACION.get(ev.tipo)
    if ev.estado == "con_resultado":
        if ev.revisada_en:
            return "revisada", "Revisada"
        return ("resultado_recibido", "Resultado recibido") if etiquetas else (ev.estado, ESTADOS_EVALUACION_U[ev.estado])
    if not etiquetas:
        return ev.estado, ESTADOS_EVALUACION_U.get(ev.estado, ev.estado)
    iniciada = bool(ev.iniciada_en) or ev.estado == "realizada_sin_resultado" or ev.paso_integrada in ("iniciada", "completada")
    if ev.tipo == "psicometrica":
        if iniciada:
            return "en_curso", etiquetas["en_curso"]
        if ev.enviada_en or ev.clave_proveedor or ev.paso_integrada == "enviada":
            return "enviada", etiquetas["enviada"]
        return "pendiente", etiquetas["pendiente"]
    return ("en_proceso", etiquetas["en_proceso"]) if iniciada else ("pendiente", etiquetas["pendiente"])


def puede_confirmar_inicio(ev: Evaluacion) -> bool:
    return (ev.tipo in SEGUIMIENTO_EVALUACION and ev.estado == "pendiente" and not ev.iniciada_en
            and ev.paso_integrada not in ("iniciada", "completada") and not bloqueo_consentimiento(ev))


def confirmar_inicio(db: Session, ev: Evaluacion, actor: str, canal: str = "sistema", usuario_id: Optional[int] = None) -> None:
    """Confirmación de inicio (RH, evaluador o proveedor): psicométrica → «En curso», socioeconómica → «En proceso».
    No cambia el estado base ni la etapa."""
    if ev.iniciada_en:
        return
    if not puede_confirmar_inicio(ev):
        raise ErrorEvaluacion(409, "Solo se confirma el inicio de una evaluación psicométrica o socioeconómica pendiente.")
    ev.iniciada_en = datetime.now(timezone.utc)
    evento(db, ev, "inicio_confirmado", actor, canal, usuario_id=usuario_id)


def revisar(db: Session, ev: Evaluacion, *, actor: str, usuario_id: Optional[int], conclusion: str, comentario: str) -> None:
    """«Marcar como revisada»: conclusión de RH (obligatoria) + comentario, con quién y cuándo. Recibir un resultado
    nunca la marca; revisarla NUNCA mueve la etapa ni avisa al candidato."""
    if ev.estado != "con_resultado":
        raise ErrorEvaluacion(409, "Solo se revisa una evaluación con resultado recibido.")
    conclusion = (conclusion or "").strip()
    opciones = conclusiones_de(ev.tipo)
    if conclusion not in opciones:
        raise ErrorEvaluacion(400, f"Elige la conclusión de RH: {', '.join(opciones.values())}.")
    anteriores = {"conclusion_rh": ev.conclusion_rh, "comentario_rh": ev.comentario_rh, "revisada_por": ev.revisada_por,
                  "revisada_en": fechas.iso(ev.revisada_en)} if ev.revisada_en else {}
    ev.conclusion_rh, ev.comentario_rh = conclusion, (comentario or "").strip()[:4000]
    ev.revisada_por, ev.revisada_por_usuario_id, ev.revisada_en = (actor or "")[:150], usuario_id, datetime.now(timezone.utc)
    ev.resultado_visto_en = ev.resultado_visto_en or ev.revisada_en
    evento(db, ev, "revisada", actor, usuario_id=usuario_id, anteriores=anteriores, conclusion_rh=conclusion, comentario=ev.comentario_rh)


def _reiniciar_revision(db: Session, ev: Evaluacion, actor: str, canal: str) -> None:
    """Un resultado nuevo, corregido o complementado vuelve a requerir revisión (la anterior queda en el historial)."""
    if not ev.revisada_en:
        return
    anteriores = {"conclusion_rh": ev.conclusion_rh, "comentario_rh": ev.comentario_rh, "revisada_por": ev.revisada_por,
                  "revisada_en": fechas.iso(ev.revisada_en)}
    ev.revisada_en, ev.revisada_por, ev.revisada_por_usuario_id, ev.conclusion_rh, ev.comentario_rh = None, "", None, "", ""
    evento(db, ev, "revision_reiniciada", actor, canal, anteriores=anteriores)


# ------------------------------------------------------------ historial y estados


def evento(db: Session, ev: Evaluacion, accion: str, actor: str, canal: str = "sistema", *, de: str = "", a: str = "",
           anteriores: Optional[dict] = None, usuario_id: Optional[int] = None, **detalle) -> EventoEvaluacion:
    """Agrega un evento al historial (append-only; nunca se edita ni se borra)."""
    e = EventoEvaluacion(
        evaluacion_id=ev.id, cuenta_id=ev.cuenta_id, accion=accion, actor=(actor or "")[:150], actor_usuario_id=usuario_id,
        canal=canal, estado_anterior=de, estado_nuevo=a, anteriores=_jsonable(anteriores or {}), detalle=_jsonable(detalle),
    )
    db.add(e)
    return e


def _jsonable(d: dict) -> dict:
    salida = {}
    for k, v in (d or {}).items():
        if isinstance(v, datetime):
            v = fechas.iso(v)
        salida[k] = v
    return salida


def cambiar_estado(db: Session, ev: Evaluacion, nuevo: str, actor: str, canal: str = "sistema", motivo: str = "",
                   usuario_id: Optional[int] = None, accion: str = "cambio_estado") -> None:
    if nuevo not in TRANSICIONES_EVALUACION.get(ev.estado, ()):
        raise ErrorEvaluacion(409, f"No se puede pasar de «{ESTADOS_EVALUACION_U.get(ev.estado, ev.estado)}» a «{ESTADOS_EVALUACION_U.get(nuevo, nuevo)}».")
    anterior = ev.estado
    ev.estado = nuevo
    if motivo:
        ev.motivo_estado = motivo[:2000]
    evento(db, ev, accion, actor, canal, de=anterior, a=nuevo, usuario_id=usuario_id, motivo=motivo)


def requiere_consentimiento(tipo: str) -> bool:
    return tipo in TIPOS_CON_CONSENTIMIENTO


def bloqueo_consentimiento(ev: Evaluacion) -> str:
    """Texto del bloqueo o "" si el consentimiento no impide avanzar."""
    if ev.consentimiento == "pendiente":
        return "En espera de consentimiento: el candidato aún no otorga su consentimiento expreso para la evaluación médica."
    if ev.consentimiento == "rechazado":
        return "El candidato rechazó el consentimiento; la única acción disponible es cancelar la evaluación."
    return ""


# ------------------------------------------------------------ creación


def normalizar_tipo(tipo: str) -> str:
    tipo = TIPO_DESDE_LEGADO.get((tipo or "").strip(), (tipo or "").strip())
    if tipo not in TIPOS_EVALUACION_U:
        raise ErrorEvaluacion(400, f"Tipo inválido. Usa uno de: {', '.join(TIPOS_EVALUACION_U)}.")
    return tipo


def resolver_evaluador(db: Session, p: Postulacion, datos: dict, actor: str) -> dict:
    """Evaluador de la forma «Asignar a una persona». Interno = Usuario; externo = contacto reutilizable del Cliente
    o «+ Nuevo evaluador» (nombre + correo y/o WhatsApp), que queda guardado como contacto del Cliente."""
    import re

    tipo = (datos.get("tipo") or "").strip()
    if tipo == "interno":
        uid = datos.get("usuario_id")
        u = db.query(Usuario).filter(Usuario.id == uid, Usuario.activo.is_(True)).first() if uid else None
        if not u:
            raise ErrorEvaluacion(400, "Elige al evaluador interno (usuario activo).")
        return {"evaluador_tipo": "interno", "evaluador_usuario_id": u.id, "evaluador_contacto_id": None,
                "evaluador_nombre": u.nombre, "evaluador_correo": u.correo or "", "evaluador_whatsapp": u.telefono or ""}
    if tipo != "externo":
        raise ErrorEvaluacion(400, "Indica si el evaluador es interno o externo.")
    cliente_id = p.vacante.cliente_id if p.vacante else None
    if datos.get("contacto_id"):
        k = (db.query(ClienteContacto).filter(ClienteContacto.id == datos["contacto_id"], ClienteContacto.cliente_id == cliente_id).first()
             if cliente_id else None)
        if not k:
            raise ErrorEvaluacion(400, "El contacto elegido no pertenece al Cliente de la vacante de esta postulación.")
        nombre, correo, whatsapp, contacto_id = f"{k.nombre} {k.apellidos or ''}".strip(), (k.correo or "").strip(), (k.telefono or "").strip(), k.id
    else:
        nombre = (datos.get("nombre") or "").strip()
        correo = (datos.get("correo") or "").strip()
        whatsapp = (datos.get("whatsapp") or "").strip()
        contacto_id = None
    if not nombre:
        raise ErrorEvaluacion(400, "Captura el nombre del evaluador.")
    if not correo and not whatsapp:
        raise ErrorEvaluacion(400, "Captura correo o WhatsApp para enviarle la solicitud.")
    if correo and not re.match(RE_CORREO_SIMPLE, correo):
        raise ErrorEvaluacion(400, "El correo del evaluador no tiene un formato válido.")
    if contacto_id is None and cliente_id:
        # «+ Nuevo evaluador» queda como contacto reutilizable del Cliente (especificación, sección 4)
        partes = nombre.split(" ", 1)
        k = ClienteContacto(cliente_id=cliente_id, nombre=partes[0][:150], apellidos=(partes[1] if len(partes) > 1 else "")[:150],
                            puesto="Evaluador(a)", correo=correo[:200], telefono=whatsapp[:30])
        db.add(k)
        db.flush()
        contacto_id = k.id
    return {"evaluador_tipo": "externo", "evaluador_usuario_id": None, "evaluador_contacto_id": contacto_id,
            "evaluador_nombre": nombre[:150], "evaluador_correo": correo[:200], "evaluador_whatsapp": whatsapp[:30]}


def armar_cita(datos: dict) -> dict:
    """Valida la cita (opcional). Fecha y hora se interpretan en la zona de la organización y se guardan en UTC."""
    fecha, hora = (datos.get("fecha") or "").strip(), (datos.get("hora") or "").strip()
    modalidad = (datos.get("modalidad") or "").strip()
    modalidad = {"Llamada": "Teléfono", "Telefono": "Teléfono"}.get(modalidad, modalidad)
    if not fecha or not hora:
        raise ErrorEvaluacion(400, "La cita necesita fecha y hora.")
    if modalidad not in MODALIDADES_CITA:
        raise ErrorEvaluacion(400, f"Modalidad inválida. Usa una de: {', '.join(MODALIDADES_CITA)}.")
    try:
        cuando = fechas.desde_local(fecha, hora)
    except ValueError:
        raise ErrorEvaluacion(400, "Fecha u hora inválida (fecha: 2026-09-05, hora: 14:30).")
    direccion = (datos.get("direccion") or "").strip()
    liga = (datos.get("liga_videollamada") or "").strip()
    if modalidad == "Presencial" and not direccion:
        raise ErrorEvaluacion(400, "Falta la dirección de la cita.")
    return {
        "cita_fecha_hora": cuando, "cita_zona_horaria": fechas.TZ_ORG.key, "cita_modalidad": modalidad,
        "cita_direccion": direccion[:300] if modalidad == "Presencial" else "",
        "cita_liga_videollamada": liga[:500] if modalidad == "Videollamada" else "",
        "cita_telefono": (datos.get("telefono") or "").strip()[:30] if modalidad == "Teléfono" else "",
    }


def nueva(p: Postulacion, cuenta_id: int, actor: str, usuario_id: Optional[int], **campos) -> Evaluacion:
    ev = Evaluacion(
        codigo="", cuenta_id=cuenta_id, postulacion_id=p.id, candidato_id=p.candidato_id, vacante_id=p.vacante_id,
        token_evaluador=secrets.token_urlsafe(24), creado_por=actor, creado_por_usuario_id=usuario_id, **campos,
    )
    if requiere_consentimiento(ev.tipo):
        ev.consentimiento = "pendiente"
        ev.consentimiento_token = secrets.token_urlsafe(24)
    return ev


def asignar_codigo(ev: Evaluacion) -> None:
    ev.codigo = f"EVA-{BASE_CODIGO_EVALUACION + ev.id}"


# ------------------------------------------------------------ resultado (formulario único)


def registrar_resultado(
    db: Session, ev: Evaluacion, *, actor: str, canal: str, conclusion: str, comentarios: str, realizada_por: Optional[str],
    adjuntos: list, version: Optional[int], modo: str = "registrar", usuario_id: Optional[int] = None,
) -> str:
    """Formulario único de resultado — el MISMO para RH (canal «sistema») y para la liga del evaluador
    (canal «liga_evaluador»). Regresa la acción registrada. Nunca toca la etapa de la postulación.

    - registrar: primer resultado. corregir: RH reemplaza conclusión/comentarios (lo anterior va al historial).
      complementar: agrega comentario/adjuntos (y conclusión solo si no había). El evaluador, si ya hay resultado,
      solo complementa.
    - `version` debe coincidir con `ev.resultado_version` (dos personas guardando a la vez → la segunda se rechaza).
    """
    if modo not in MODOS_RESULTADO:
        raise ErrorEvaluacion(400, "Modo de guardado inválido.")
    if not puede_registrar_resultado(ev.estado):
        raise ErrorEvaluacion(409, "Esta evaluación fue cancelada; ya no acepta resultados.")
    bloqueo = bloqueo_consentimiento(ev)
    if bloqueo:
        raise ErrorEvaluacion(409, bloqueo)
    if version is not None and int(version) != int(ev.resultado_version or 0):
        raise ErrorEvaluacion(409, "Este resultado cambió mientras lo editabas.")
    ya = ev.estado == "con_resultado"
    if ya and modo == "registrar":
        modo = "corregir" if canal == "sistema" else "complementar"
    if not ya and modo != "registrar":
        modo = "registrar"
    if canal != "sistema" and ya and modo != "complementar":
        raise ErrorEvaluacion(409, "Ya hay un resultado registrado; solo puedes agregar un complemento.")

    conclusion = (conclusion or "").strip()
    comentarios = (comentarios or "").strip()[:10000]
    opciones = conclusiones_de(ev.tipo)
    if conclusion and conclusion not in opciones:
        raise ErrorEvaluacion(400, f"Conclusión inválida para esta evaluación. Usa: {', '.join(opciones.values())}.")
    if modo in ("registrar", "corregir"):
        if ev.tipo == "entrevista_humana" and not conclusion:
            raise ErrorEvaluacion(400, "Elige la conclusión de la entrevista: Avanzar, No avanzar o Requiere otra entrevista.")
        if not (conclusion or comentarios or adjuntos or (modo == "corregir" and ev.adjuntos)):
            raise ErrorEvaluacion(400, "Agrega al menos un comentario o un adjunto.")
    elif not (comentarios or adjuntos or (conclusion and not ev.conclusion)):
        raise ErrorEvaluacion(400, "El complemento necesita un comentario o un adjunto.")

    ahora = datetime.now(timezone.utc)
    anteriores = {"conclusion": ev.conclusion, "comentarios": ev.comentarios, "realizada_por": ev.realizada_por,
                  "registrada_por": ev.registrada_por, "registrada_en": fechas.iso(ev.registrada_en)} if ya else {}
    if modo == "complementar":
        if comentarios:
            sello = f"Complemento de {actor} ({fechas.local(ahora).strftime('%d/%m/%Y %H:%M')}):"
            ev.comentarios = f"{ev.comentarios}\n\n{sello}\n{comentarios}".strip() if ev.comentarios else comentarios
        if conclusion and not ev.conclusion:
            ev.conclusion = conclusion
    else:
        ev.conclusion = conclusion
        ev.comentarios = comentarios
    if realizada_por is not None and modo != "complementar":
        ev.realizada_por = realizada_por.strip()[:200]
    ev.adjuntos = list(ev.adjuntos or []) + list(adjuntos or [])
    if modo != "complementar" or not ev.registrada_por:
        ev.registrada_por, ev.registrada_via, ev.registrada_en = actor[:150], canal, ahora
        ev.registrada_por_usuario_id = usuario_id
    ev.realizada_en = ev.realizada_en or ahora
    ev.resultado_version = int(ev.resultado_version or 0) + 1
    _reiniciar_revision(db, ev, actor, canal)  # recibir/corregir un resultado nunca lo deja «revisado»
    # «Nuevo resultado» hasta que RH lo abre, cuando llega de afuera (liga del evaluador / proveedor)
    ev.resultado_visto_en = ahora if canal == "sistema" else None
    anterior = ev.estado
    if ev.estado != "con_resultado":
        ev.estado = "con_resultado"
    accion = {"registrar": "resultado_registrado", "corregir": "resultado_corregido", "complementar": "resultado_complementado"}[modo]
    evento(db, ev, accion, actor, canal, de=anterior, a=ev.estado, anteriores=anteriores, usuario_id=usuario_id,
           conclusion=ev.conclusion, comentario=comentarios, adjuntos=[a["nombre"] for a in adjuntos or []],
           realizada_por=ev.realizada_por)
    return accion


def adjunto_de(archivo, ruta: str, actor: str, canal: str) -> dict:
    return {"id": secrets.token_hex(6), "archivo": ruta, "nombre": archivo.nombre, "mime": archivo.mime,
            "subido_por": actor[:150], "subido_via": canal, "subido_en": fechas.iso(datetime.now(timezone.utc))}


# ------------------------------------------------------------ notificaciones (especificación, sección 7)


class CitaComoEntrevista:
    """Adaptador de la evaluación para las plantillas de entrevista que ya existen (`notificaciones.datos_entrevista_humana`,
    `_texto_cita_entrevista_humana`, plantilla de Meta): expone los atributos que esas funciones leen."""

    def __init__(self, ev: Evaluacion):
        self.ev = ev
        self.entrevistador = ev.evaluador_nombre or ""
        self.tipo = ev.evaluador_tipo or "externo"
        self.usuario_id = ev.evaluador_usuario_id
        self.correo_externo = ev.evaluador_correo or ""
        self.whatsapp_externo = ev.evaluador_whatsapp or ""
        self.contacto_id = ev.evaluador_contacto_id
        self.fecha = ev.cita_fecha_hora
        self.modalidad = ev.cita_modalidad or ""
        self.liga = ev.cita_liga_videollamada or ""
        self.ubicacion = ev.cita_direccion or ""
        self.telefono_contacto = ev.cita_telefono or ""
        self.comentario = ev.instrucciones or ""
        self.token = ev.token_evaluador
        self.teams_evento_id = ev.teams_evento_id or ""
        self.resultado = ""
        self.recomendacion = ev.conclusion or ""


def datos_evaluacion(ev: Evaluacion, p: Postulacion) -> dict:
    from ..serial import nombre_empresa_candidato
    from . import plantillas_correo

    fecha, hora = plantillas_correo.fecha_hora_mx(ev.cita_fecha_hora) if ev.cita_fecha_hora else ("", "")
    cita = ""
    if ev.cita_fecha_hora:
        from .notificaciones import _fecha_hora_legible_mx

        cita = f"Cita: {_fecha_hora_legible_mx(ev.cita_fecha_hora)}, modalidad {ev.cita_modalidad}."
        if ev.cita_modalidad == "Presencial" and ev.cita_direccion:
            cita += f" Dirección: {ev.cita_direccion}."
        elif ev.cita_modalidad == "Videollamada" and ev.cita_liga_videollamada:
            cita += f" Liga de videollamada: {ev.cita_liga_videollamada}."
        elif ev.cita_modalidad == "Teléfono" and ev.cita_telefono:
            cita += f" Teléfono: {ev.cita_telefono}."
    return {
        "tipo": ev.tipo, "tipo_nombre": ev.nombre_visible, "con_cita": bool(ev.cita_fecha_hora),
        "vacante": p.vacante.titulo if p.vacante else "", "empresa": nombre_empresa_candidato(p.vacante) if p.vacante else "",
        "fecha": fecha, "hora": hora, "modalidad": ev.cita_modalidad or "", "direccion": ev.cita_direccion or "",
        "liga_videollamada": ev.cita_liga_videollamada or "", "instrucciones": ev.instrucciones or "", "cita_texto": cita,
        "liga_evaluador": liga_evaluador(ev) if ev.forma == "asignada" else "",
        "liga_externa": ev.liga_externa_candidato if ev.forma == "liga_otro_sistema" else "",
    }


async def notificar(db: Session, ev: Evaluacion, p: Postulacion, evento_: str, actor: str, *, override: Optional[dict] = None,
                    audiencias: Optional[set] = None) -> List[dict]:
    """Aplica la matriz de la especificación sobre la regla de la Cuenta y dispara el evento. Nunca truena.

    - creada con cita → candidato + evaluador · creada sin cita → evaluador (y candidato SOLO con liga de otro sistema)
    - reprogramada → ambos · cancelada → evaluador + candidato si había cita · recordatorio → según elección
    - mientras el consentimiento médico esté pendiente/rechazado NO sale nada al evaluador.
    """
    from . import notificaciones

    matriz = {"candidato": True, "entrevistador": ev.forma == "asignada"}
    con_cita = bool(ev.cita_fecha_hora)
    if evento_ == "evaluacion_asignada":
        matriz["candidato"] = con_cita or ev.forma == "liga_otro_sistema"
    elif evento_ == "evaluacion_cancelada":
        matriz["candidato"] = con_cita or ev.forma == "liga_otro_sistema"
    elif evento_ == "recordatorio_evaluacion":
        matriz["candidato"] = con_cita or ev.forma == "liga_otro_sistema"
    if bloqueo_consentimiento(ev):
        matriz["entrevistador"] = False
    if audiencias is not None:
        matriz = {k: v and k in audiencias for k, v in matriz.items()}
    forzado = dict(override or {})
    for aud, permitido in matriz.items():
        if not permitido:
            forzado[f"{aud}_correo"] = False
            forzado[f"{aud}_whatsapp"] = False
    try:
        resultados = await notificaciones.disparar(
            db, evento_, p, actor, eh=CitaComoEntrevista(ev), override=forzado,
            extra={"_datos_evaluacion": datos_evaluacion(ev, p)},
        )
    except Exception as ex:  # noqa: BLE001 — un aviso caído nunca rompe la acción de RH
        resultados = [{"destinatario": "sistema", "canal": "", "destino": "", "enviado": False, "detalle": f"No se pudo avisar: {ex}"[:300]}]
    if resultados:
        # también los fallidos: el estado del envío se muestra junto a cada liga (nunca en silencio)
        evento(db, ev, "envio", actor, "sistema", evento=evento_,
               envios=[{k: r.get(k) for k in ("destinatario", "canal", "destino", "enviado", "detalle")} for r in resultados])
        if evento_ in ("evaluacion_asignada", "evaluacion_reprogramada", "recordatorio_evaluacion"):
            marcar_enviada(ev, resultados, "entrevistador" if ev.forma == "asignada" else "candidato" if ev.forma == "liga_otro_sistema" else "-")
    return resultados


async def notificar_rh_resultado(db: Session, ev: Evaluacion, p: Postulacion) -> Optional[dict]:
    """Resultado recibido vía liga → aviso a RH (responsable de la vacante o correo de la Cuenta)."""
    from ..models import Cuenta
    from . import plantillas_correo
    from .correo import enviar_correo

    v = p.vacante
    correo = (v.responsable.correo if v and v.responsable and v.responsable.correo else "") or ""
    if not correo and p.cuenta_id:
        cu = db.query(Cuenta).filter(Cuenta.id == p.cuenta_id).first()
        correo = (cu.correo_comunicacion or "") if cu else ""
    if not correo:
        return None
    conclusion = conclusiones_de(ev.tipo).get(ev.conclusion, "") if ev.conclusion else "Resultado recibido · Sin conclusión"
    filas = [("Candidato", p.nombre), ("Vacante", v.titulo if v else ""), ("Evaluación", ev.nombre_visible),
             ("Conclusión", conclusion), ("Registró", ev.registrada_por or "Evaluador vía liga")]
    liga = f"{settings.app_url}/dashboard/candidatos?abrir={p.codigo}"
    asunto, html = plantillas_correo.html_aviso(
        f"Nuevo resultado: {ev.nombre_visible} de {p.nombre}",
        "El evaluador registró su resultado. Recibir un resultado no aprueba ni mueve al candidato: la decisión sigue siendo de RH.",
        "", [f for f in filas if f[1]], ("Ver la evaluación", liga),
    )
    try:
        envio = await enviar_correo(correo, asunto, html)
    except Exception as ex:  # noqa: BLE001
        envio = {"enviado": False, "proveedor": "error", "detalle": str(ex)[:200]}
    db.add(NotificacionEnviada(cuenta_id=p.cuenta_id, candidato_id=p.candidato_id, evento="evaluacion_resultado", destinatario_tipo="rh",
                               canal="correo", destino=correo, enviado=bool(envio.get("enviado")), detalle=str(envio.get("detalle", ""))))
    return {"destinatario": "rh", "canal": "correo", "destino": correo, **envio, "detalle": str(envio.get("detalle", ""))}


# ------------------------------------------------------------ proveedor integrado (Psicométricas.mx y simulado)


def usa_psicometricas(ev: Evaluacion) -> bool:
    from . import psicometricas as psi

    return ev.forma == "integrada" and psi.es_psicometricas(ev.proveedor)


def siguiente_paso(ev: Evaluacion) -> Optional[str]:
    if ev.forma != "integrada":
        return None
    actual = ev.paso_integrada or "asignada"
    i = PASOS_INTEGRADA.index(actual) if actual in PASOS_INTEGRADA else 0
    return PASOS_INTEGRADA[i + 1] if i + 1 < len(PASOS_INTEGRADA) else None


def aplicar_paso(db: Session, ev: Evaluacion, paso: str, actor: str, origen: str = "simulado") -> None:
    """Paso del modo integrado. «completada» → Realizada · Resultado pendiente; el resultado entra con
    `registrar_resultado` (canal proveedor). Los pasos intermedios solo quedan en el historial."""
    ev.paso_integrada = paso
    if paso == "enviada" and not ev.enviada_en:
        ev.enviada_en = datetime.now(timezone.utc)
    if paso in ("iniciada", "completada") and not ev.iniciada_en:
        ev.iniciada_en = datetime.now(timezone.utc)  # confirmación de inicio del proveedor
    if paso == "completada" and ev.estado == "pendiente":
        cambiar_estado(db, ev, "realizada_sin_resultado", actor, "proveedor" if origen != "simulado" else "sistema", f"Proveedor ({origen}): completada")
    else:
        evento(db, ev, "envio" if paso in ("enviada", "iniciada") else "cambio_estado", actor,
               "proveedor" if origen != "simulado" else "sistema", paso=paso, origen=origen)


def estado_proveedor(ev: Evaluacion) -> Optional[tuple]:
    """Psicometría del proveedor integrado → (clave, texto) de SOLO tres estados: Pendiente (asignada, sin iniciar),
    En curso (el proveedor confirmó el inicio) y Completada (terminó; con o sin resultado descargado). None si la
    evaluación no es psicométrica integrada o se canceló."""
    from ..models import ESTADOS_PROVEEDOR_PSICOMETRIA as E

    if ev.tipo != "psicometrica" or ev.forma != "integrada" or ev.estado in ("cancelada", "no_realizada"):
        return None
    if ev.estado in ("realizada_sin_resultado", "con_resultado") or ev.paso_integrada in ("completada", "resultado_recibido"):
        return "completada", E["completada"]
    if ev.iniciada_en or ev.paso_integrada == "iniciada":
        return "en_curso", E["en_curso"]
    return "pendiente", E["pendiente"]


def resumen_resultado(datos) -> str:
    """Texto breve a partir del JSON del proveedor (sin interpretar: lo revisa una persona)."""
    import json as _json

    return f"Resultado recibido de Psicométricas.mx ({len(_json.dumps(datos, ensure_ascii=False))} caracteres). Revisa el informe PDF adjunto."


def sincronizar_psicometricas(db: Session, ev: Evaluacion, por: str = "Psicométricas.mx (automático)") -> str:
    """Confirma con su API (consultaCandidato → fecha_fin) y, si ya terminó, guarda JSON + PDF como resultado de la
    evaluación (canal proveedor, autor = proveedor). Idempotente. Regresa: sin_clave | en_curso | ya_estaba |
    resultado_recibido."""
    from . import archivos as fs
    from . import psicometricas as psi

    if not ev.clave_proveedor:
        return "sin_clave"
    if ev.estado in ("con_resultado", "cancelada"):
        return "ya_estaba"
    filas = psi.consultar_candidato(ev.clave_proveedor)
    if not psi.terminado(filas):
        # 2026-10-07: si el proveedor ya registra el inicio de alguna prueba → «En curso» (una sola vez, con historial)
        if psi.iniciado(filas) and not ev.iniciada_en and ev.estado == "pendiente":
            aplicar_paso(db, ev, "iniciada", por, "Psicométricas.mx")
        return "en_curso"
    datos = psi.resultado_json(ev.clave_proveedor)
    pdf = psi.resultado_pdf(ev.clave_proveedor)
    ev.resultado_json = datos if isinstance(datos, dict) else {"resultados": datos}
    adjuntos = []
    if pdf:
        validado = fs.validar_bytes(pdf, f"psicometricas-{ev.clave_proveedor}.pdf", f"informe «{ev.nombre_visible}»")
        ruta = fs.guardar(validado, f"evaluaciones/{ev.id}", f"informe_{ev.codigo}_{secrets.token_hex(3)}")
        adjuntos.append(adjunto_de(validado, ruta, por, "proveedor"))
    if ev.paso_integrada != "completada":
        aplicar_paso(db, ev, "completada", por, "Psicométricas.mx")
    ev.paso_integrada = "resultado_recibido"
    registrar_resultado(db, ev, actor=por, canal="proveedor", conclusion="", comentarios=resumen_resultado(datos),
                        realizada_por=ev.proveedor or "Psicométricas.mx", adjuntos=adjuntos, version=None, modo="registrar")
    ev.resultado_visto_en = None  # «Nuevo resultado» hasta que RH lo abra
    return "resultado_recibido"


# ------------------------------------------------------------ vacante: sugeridas y avisos antes de Onboarding


def normalizar_sugeridas(lista: List[dict], pruebas_validas: dict) -> List[dict]:
    """Sugerencias de la vacante: [{tipo, prueba_id?, nombre?}] sin duplicados; la prueba debe ser del catálogo.
    Acepta las claves de tipo previas («medico», «socioeconomico») y las guarda con las nuevas."""
    salida, vistos = [], set()
    for x in lista or []:
        tipo = TIPO_DESDE_LEGADO.get(str((x or {}).get("tipo") or "").strip(), str((x or {}).get("tipo") or "").strip())
        if tipo not in TIPOS_EVALUACION_U:
            continue
        prueba_id = (x or {}).get("prueba_id")
        try:
            prueba_id = int(prueba_id) if prueba_id not in (None, "") else None
        except (TypeError, ValueError):
            prueba_id = None
        if prueba_id is not None and prueba_id not in pruebas_validas:
            prueba_id = None
        nombre = str((x or {}).get("nombre") or "").strip()[:200] or (pruebas_validas.get(prueba_id) if prueba_id else TIPOS_EVALUACION_U[tipo])
        clave = (tipo, prueba_id, nombre.lower())
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append({"tipo": tipo, "prueba_id": prueba_id, "nombre": nombre})
    return salida


def avisos_antes_onboarding(p: Postulacion, evaluaciones: List[Evaluacion]) -> List[str]:
    """Si la vacante pide «Avisar antes de Onboarding»: sugeridas sin asignar, evaluaciones sin resultado y
    conclusiones desfavorables. Solo AVISA (RH decide); nunca bloquea."""
    v = p.vacante
    if not v or not v.avisar_evaluaciones_antes_onboarding:
        return []
    avisos = []
    vivas = [e for e in evaluaciones if e.estado != "cancelada"]
    for s in v.evaluaciones_sugeridas or []:
        tipo = TIPO_DESDE_LEGADO.get(s.get("tipo"), s.get("tipo"))
        hay = any(e.tipo == tipo and (not s.get("prueba_id") or e.prueba_id == s.get("prueba_id")) for e in vivas)
        if not hay:
            avisos.append(f"Sugerida por la vacante y no asignada: {s.get('nombre') or TIPOS_EVALUACION_U.get(tipo, '')}.")
    for e in vivas:
        if e.estado != "con_resultado":
            avisos.append(f"{e.nombre_visible}: {seguimiento(e)[1]}.")
        elif e.conclusion_vigente in ("desfavorable", "no_apto", "no_avanzar"):
            avisos.append(f"{e.nombre_visible}: {conclusiones_de(e.tipo).get(e.conclusion_vigente, e.conclusion_vigente)}.")
        elif not e.revisada_en:
            avisos.append(f"{e.nombre_visible}: resultado recibido sin revisar por RH.")
    return avisos


def etiqueta_forma(forma: str) -> str:
    return FORMAS_EVALUACION.get(forma, forma)


def etiqueta_consentimiento(ev: Evaluacion) -> str:
    return CONSENTIMIENTOS.get(ev.consentimiento or "no_requerido", "")
