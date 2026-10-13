"""Módulo UNIVERSAL de Capacitación (2026-09-16).

Un solo módulo, un solo tipo de curso:
- Crear: solo Tema, contexto opcional, adjuntos y duración → «Generar curso con IA» produce objetivo, módulos,
  categoría y la EVALUACIÓN FINAL INTEGRADA (opción múltiple / verdadero-falso, calificada automáticamente).
- Asignar: el mismo curso publicado se asigna a Colaboradores, Candidatos (filtro dentro de una vacante) o
  Externos (proveedores/clientes: liga pública que solo pide nombre y correo/WhatsApp).
- Cursar: la persona avanza módulo por módulo y, al final, contesta la evaluación una pregunta por pantalla, en
  la misma página. Resultado = Aprobado/No aprobado + %; queda en el historial del colaborador, en la
  evaluación del candidato (postulacion.analisis.capacitacion) o en la asignación del externo.
- Seguimiento: un solo tablero (GET /capacitacion/asignaciones) con filtros.
Sin plataformas externas ni módulos separados de evaluación.
"""

import io
import secrets
import traceback
from datetime import datetime, timezone
from typing import List, Optional

from fastapi.responses import Response
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import cuenta_actual, usuario_actual, usuario_decisor
from ..models import MODALIDADES_CURSO, slugificar, AsignacionCurso, Candidato, Colaborador, Cuenta, Curso, ModuloCurso, Postulacion, Usuario, registrar
from ..serial import asignacion_dict, asignacion_publica_dict, curso_dict
from ..services import archivos as fs
from ..services import ia
from ..services.avatar import crear_sesion_avatar
from ..services.pdf import pdf_curso
from ..services.correo import enviar_correo
from ..services.whatsapp import enviar_mensaje, enviar_texto_sin_plantilla

router = APIRouter(prefix="/capacitacion", tags=["capacitacion"])

TIPOS_ASIGNACION = ("colaborador", "candidato", "externo")
MAX_ADJUNTOS = 5
MAX_TEXTO_ADJUNTO = 12000


def _por_codigo(db: Session, codigo: str, cuenta_id: int) -> Curso:
    c = db.query(Curso).filter(Curso.codigo == codigo, Curso.cuenta_id == cuenta_id).first()
    if not c:
        raise HTTPException(404, "Curso no encontrado")
    return c


def liga_asignacion(a: AsignacionCurso) -> str:
    return f"{settings.app_url}/capacitacion/{a.token}"


def _texto_de_adjunto(nombre: str, contenido: bytes) -> str:
    """Texto plano del material adjunto para la IA: PDF (pypdf) o texto; otros formatos solo se guardan."""
    n = (nombre or "").lower()
    try:
        if n.endswith(".pdf"):
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(contenido))
            return "\n".join((pg.extract_text() or "") for pg in reader.pages)[:MAX_TEXTO_ADJUNTO]
        if n.endswith((".txt", ".md", ".csv")):
            return contenido.decode("utf-8", errors="ignore")[:MAX_TEXTO_ADJUNTO]
    except Exception as ex:  # noqa: BLE001 — un adjunto ilegible no tumba la generación
        return f"[No se pudo leer «{nombre}»: {ex}]"
    return ""


# ------------------------------------------------------------
# Crear / generar / editar / publicar
# ------------------------------------------------------------


@router.post("/generar", status_code=201)
async def generar(
    tema: str = Form(...),
    duracion_horas: Optional[float] = Form(default=None),
    duracion: str = Form(default=""),  # 2026-09-19: libre («5 min», «15 min», «1 h», «2 horas»)
    modalidad: str = Form(default="autoguiado"),  # instructor_ia | autoguiado
    contexto: str = Form(default=""),
    archivos: List[UploadFile] = File(default=[]),
    db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual),
):
    """«Generar curso con IA»: Tema + modalidad (Instructor IA / Autoguiado) + duración libre + contexto opcional +
    adjuntos → objetivo, categoría, módulos (guion conversacional o contenido modular) y evaluación final integrada.
    Queda como Borrador para que RH lo revise y lo FINALICE (Crear → Revisar → Finalizar → Asignar)."""
    if not tema.strip():
        raise HTTPException(400, "El tema del curso es obligatorio.")
    if modalidad not in MODALIDADES_CURSO:
        raise HTTPException(400, "modalidad debe ser instructor_ia o autoguiado.")
    duracion_texto = duracion.strip()
    if duracion_horas is None or duracion_horas <= 0:
        if not duracion_texto:
            raise HTTPException(400, "Indica la duración (por ejemplo «15 min» o «1 h»).")
        duracion_horas = ia.horas_desde_texto(duracion_texto)
    if not duracion_texto:
        duracion_texto = f"{duracion_horas:g} h"
    if len(archivos) > MAX_ADJUNTOS:
        raise HTTPException(400, f"Máximo {MAX_ADJUNTOS} archivos adjuntos.")

    c = Curso(codigo="TMP", cuenta_id=cuenta.id, titulo=tema.strip(), duracion_horas=duracion_horas, duracion_texto=duracion_texto, modalidad=modalidad, contexto=contexto.strip(), estado="Borrador", creado_por=u.nombre)
    db.add(c)
    db.flush()
    c.codigo = f"CUR-{100 + c.id}"

    material_partes: List[str] = []
    adjuntos: List[dict] = []
    for archivo in archivos:
        if not archivo.filename:
            continue
        contenido = await archivo.read()
        if not contenido:
            continue
        texto = _texto_de_adjunto(archivo.filename, contenido)
        ruta = fs.guardar_bytes(contenido, f"cursos/{c.id}", archivo.filename)
        adjuntos.append({"nombre": archivo.filename, "ruta": ruta, "caracteres": len(texto)})
        if texto.strip():
            material_partes.append(f"### {archivo.filename}\n{texto}")
    c.adjuntos = adjuntos

    guion, con_ia = ia.guion_curso(tema.strip(), duracion_horas, contexto=contexto, material="\n\n".join(material_partes), modalidad=modalidad, duracion_texto=duracion_texto)
    c.objetivo = guion.objetivo
    c.categoria = guion.categoria or "General"
    for i, m in enumerate(guion.modulos, start=1):
        db.add(ModuloCurso(curso_id=c.id, orden=i, titulo=m.titulo, contenido=m.contenido, resumen=m.resumen or "", puntos_clave=list(m.puntos_clave or []), preguntas_verificacion=[p.model_dump() for p in m.preguntas_verificacion]))
    c.evaluacion = [q.model_dump() for q in guion.evaluacion]
    registrar(db, u.nombre, "curso_generado", "curso", c.codigo, {"tema": tema, "ia": con_ia, "modulos": len(guion.modulos), "preguntas": len(c.evaluacion), "adjuntos": [a["nombre"] for a in adjuntos]})
    db.commit()
    return {"ia": con_ia, **curso_dict(c, detalle=True)}


@router.get("")
def listar(db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    return [curso_dict(c) for c in db.query(Curso).filter(Curso.cuenta_id == cuenta.id, Curso.estado != "Archivado").order_by(Curso.id.desc()).all()]


@router.get("/kpis")
def kpis(db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """KPIs del tablero — antes de /{codigo} a propósito ('kpis' no es un código)."""
    cursos = db.query(Curso).filter(Curso.cuenta_id == cuenta.id, Curso.estado != "Archivado").all()
    asignaciones = [a for a in db.query(AsignacionCurso).join(Curso, AsignacionCurso.curso_id == Curso.id).filter(Curso.cuenta_id == cuenta.id).all() if a.viva]
    completadas = [a for a in asignaciones if a.estado == "completado"]
    aprobadas = [a for a in completadas if a.aprobado]
    return {
        "cursosActivos": sum(1 for c in cursos if c.estado == "Publicado"),
        "enFormacion": sum(1 for a in asignaciones if a.estado in ("pendiente", "en_curso")),
        "tasaFinalizacion": round(len(completadas) / len(asignaciones) * 100) if asignaciones else 0,
        "tasaAprobacion": round(len(aprobadas) / len(completadas) * 100) if completadas else 0,
        "horasImpartidas": round(sum((a.curso.duracion_horas if a.curso else 0) for a in completadas), 1),
        "porTipo": {t: sum(1 for a in asignaciones if a.tipo == t) for t in TIPOS_ASIGNACION},
    }


@router.get("/asignaciones")
def tablero(
    tipo: Optional[str] = None, estado: Optional[str] = None, curso: Optional[str] = None, aprobado: Optional[bool] = None,
    db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual),
):
    """Tablero ÚNICO de seguimiento: todas las asignaciones de la Cuenta con filtros por tipo de persona,
    estado, curso y resultado. Antes de /{codigo} a propósito."""
    q = db.query(AsignacionCurso).join(Curso, AsignacionCurso.curso_id == Curso.id).filter(Curso.cuenta_id == cuenta.id)
    if tipo in TIPOS_ASIGNACION:
        q = q.filter(AsignacionCurso.tipo == tipo)
    if estado:
        q = q.filter(AsignacionCurso.estado == estado)
    if curso:
        q = q.filter(Curso.codigo == curso)
    if aprobado is not None:
        q = q.filter(AsignacionCurso.aprobado.is_(aprobado))
    return [asignacion_dict(a) for a in q.order_by(AsignacionCurso.id.desc()).all() if a.viva]


@router.get("/{codigo}")
def detalle(codigo: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    return curso_dict(_por_codigo(db, codigo, cuenta.id), detalle=True)


class ModuloIn(BaseModel):
    titulo: str
    contenido: str = ""


class PreguntaIn(BaseModel):
    pregunta: str
    tipo: str = "opcion"  # opcion | vf
    opciones: List[str] = []
    correcta: int = 0
    explicacion: str = ""


class EditarCursoIn(BaseModel):
    titulo: Optional[str] = None
    objetivo: Optional[str] = None
    categoria: Optional[str] = None
    duracion_horas: Optional[float] = None
    duracion: Optional[str] = None  # 2026-09-19: duración libre
    modalidad: Optional[str] = None
    calificacion_minima: Optional[int] = None
    modulos: Optional[List[ModuloIn]] = None
    evaluacion: Optional[List[PreguntaIn]] = None


@router.patch("/{codigo}")
def editar(codigo: str, datos: EditarCursoIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """La IA genera; RH edita solo si quiere (objetivo, módulos, preguntas, mínimo aprobatorio)."""
    c = _por_codigo(db, codigo, cuenta.id)
    if datos.titulo is not None and datos.titulo.strip():
        c.titulo = datos.titulo.strip()
    if datos.objetivo is not None:
        c.objetivo = datos.objetivo
    if datos.categoria is not None:
        c.categoria = datos.categoria.strip()
    if datos.duracion_horas is not None and datos.duracion_horas > 0:
        c.duracion_horas = datos.duracion_horas
    if datos.duracion is not None and datos.duracion.strip():
        c.duracion_texto = datos.duracion.strip()[:40]
        c.duracion_horas = ia.horas_desde_texto(c.duracion_texto, c.duracion_horas or 0.5)
    if datos.modalidad in MODALIDADES_CURSO:
        c.modalidad = datos.modalidad
    if datos.calificacion_minima is not None:
        if not (1 <= datos.calificacion_minima <= 100):
            raise HTTPException(400, "La calificación mínima debe estar entre 1 y 100.")
        c.calificacion_minima = datos.calificacion_minima
    if datos.modulos is not None:
        if not datos.modulos:
            raise HTTPException(400, "El curso necesita al menos un módulo.")
        for m in list(c.modulos):
            db.delete(m)
        db.flush()
        for i, m in enumerate(datos.modulos, start=1):
            db.add(ModuloCurso(curso_id=c.id, orden=i, titulo=m.titulo.strip() or f"Módulo {i}", contenido=m.contenido))
    if datos.evaluacion is not None:
        validas = []
        for q in datos.evaluacion:
            ops = ["Verdadero", "Falso"] if q.tipo == "vf" else [o for o in q.opciones if o.strip()]
            if not q.pregunta.strip() or len(ops) < 2 or not (0 <= q.correcta < len(ops)):
                raise HTTPException(400, f"Pregunta inválida: «{q.pregunta[:60]}» (revisa opciones y respuesta correcta).")
            validas.append({"pregunta": q.pregunta.strip(), "tipo": "vf" if q.tipo == "vf" else "opcion", "opciones": ops, "correcta": q.correcta, "explicacion": q.explicacion})
        if not validas:
            raise HTTPException(400, "La evaluación necesita al menos una pregunta.")
        c.evaluacion = validas
    registrar(db, u.nombre, "curso_editado", "curso", c.codigo, {"campos": [k for k, v in datos.model_dump().items() if v is not None]})
    db.commit()
    db.refresh(c)
    return curso_dict(c, detalle=True)


@router.patch("/{codigo}/finalizar")
@router.patch("/{codigo}/publicar")  # alias histórico
def publicar(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Finalizar curso» (2026-09-19: Crear → Revisar → Finalizar → Asignar). El estado interno sigue siendo
    «Publicado» por compatibilidad; la UI lo muestra como Finalizado."""
    c = _por_codigo(db, codigo, cuenta.id)
    if not c.modulos or not c.evaluacion:
        raise HTTPException(409, "El curso necesita módulos y evaluación antes de finalizarse.")
    c.estado = "Publicado"
    registrar(db, u.nombre, "curso_publicado", "curso", c.codigo, {})
    db.commit()
    return curso_dict(c, detalle=True)


@router.delete("/{codigo}")
def archivar(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Baja lógica: el curso deja de listarse y de asignarse; las asignaciones y resultados se conservan."""
    c = _por_codigo(db, codigo, cuenta.id)
    c.estado = "Archivado"
    registrar(db, u.nombre, "curso_archivado", "curso", c.codigo, {})
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------
# Asignación universal
# ------------------------------------------------------------


class ExternoIn(BaseModel):
    nombre: str = ""
    correo: str = ""
    telefono: str = ""
    organizacion: str = ""


class AsignarIn(BaseModel):
    colaborador_ids: List[str] = []   # códigos COL-####
    postulacion_ids: List[str] = []   # códigos P-#### (candidatos)
    externos: List[ExternoIn] = []    # con datos → se notifica; sin datos → liga abierta que pide nombre y correo/WA
    notificar: bool = True


def _empresa_curso(curso: Curso) -> str:
    cu = getattr(curso, "cuenta", None)
    return (cu.nombre_comercial or cu.nombre) if cu else "Red Human"


def mensaje_curso_colaborador(nombre: str, curso: Curso, liga: str) -> str:
    """WhatsApp de texto libre al COLABORADOR al asignarle un curso (2026-09-18, sin plantilla de Meta):
    «Hola [Nombre], se te asignó el curso de capacitación de [Empresa]: [Título]. Accede a tu curso,
    interactúa con el avatar y descarga tu material en la siguiente liga: [URL]»."""
    primer = (nombre or "").split(" ")[0] or "colaborador(a)"
    return (
        f"Hola {primer}, se te asignó el curso de capacitación de {_empresa_curso(curso)}: {curso.titulo}. "
        f"Accede a tu curso, interactúa con el avatar y descarga tu material en la siguiente liga: {liga}"
    )


def _mensaje_liga(nombre: str, curso: Curso, liga: str, tipo: str) -> str:
    primer = (nombre or "").split(" ")[0]
    saludo = f"¡Hola {primer}!" if primer else "¡Hola!"
    motivo = " como parte de tu proceso de selección" if tipo == "candidato" else ""
    return (
        f"{saludo} 📚 Te asignamos el curso *{curso.titulo}*{motivo} (duración aprox. {curso.duracion_horas} h). "
        f"Se cursa en línea, módulo por módulo, con una evaluación breve al final: {liga}"
    )


def _html_liga(nombre: str, curso: Curso, liga: str, tipo: str) -> str:
    primer = (nombre or "").split(" ")[0] or "hola"
    motivo = " como parte de tu proceso de selección" if tipo == "candidato" else ""
    return (
        f"<p>¡Hola {primer}!</p><p>Te asignamos el curso <strong>{curso.titulo}</strong>{motivo} (duración aproximada: {curso.duracion_horas} horas).</p>"
        f"<p>Se cursa en línea, módulo por módulo, con una evaluación breve al final:</p><p><a href=\"{liga}\">{liga}</a></p><p>Saludos,<br>Red Human AI</p>"
    )


async def _notificar(a: AsignacionCurso) -> dict:
    """WhatsApp y/o correo con la liga; que un proveedor falle nunca tumba la asignación.

    2026-09-22 (hotfix 500): además de los try/except por canal, TODO el armado del mensaje va protegido
    (`notificar_seguro`): leer el curso (modalidad, duración, empresa), la persona o la liga nunca puede
    tumbar la asignación — el aviso es un efecto secundario, la asignación es el dato."""
    curso = a.curso
    liga = liga_asignacion(a)
    salida = {"whatsapp": None, "correo": None}
    tel, correo, nombre = a.telefono_persona, a.correo_persona, a.nombre_persona
    if tel:
        try:
            if a.tipo == "colaborador":
                # 2026-09-18: texto libre (sin plantilla) con la liga absoluta al curso; si Meta lo rechaza por la
                # ventana de 24 h queda el warning en el log y la asignación se guarda igual.
                salida["whatsapp"] = await enviar_texto_sin_plantilla(tel, mensaje_curso_colaborador(nombre, curso, liga))
            else:
                salida["whatsapp"] = await enviar_mensaje(tel, _mensaje_liga(nombre, curso, liga, a.tipo))
            if salida["whatsapp"] and salida["whatsapp"].get("fuera_de_ventana"):
                print(f"[capacitacion] ⚠️ Mensaje de texto rechazado por ventana de 24h — asignación {a.codigo} guardada sin aviso por WhatsApp.", flush=True)
        except Exception as ex:  # noqa: BLE001 — salvavidas: la asignación se guarda aunque WhatsApp falle
            print(f"[capacitacion] ⚠️ WhatsApp no disponible para la asignación {a.codigo}: {ex}", flush=True)
            salida["whatsapp"] = {"enviado": False, "proveedor": "error", "detalle": str(ex)[:200]}
    if correo:
        try:
            salida["correo"] = await enviar_correo(correo, f"Curso asignado: {curso.titulo}", _html_liga(nombre, curso, liga, a.tipo))
        except Exception as ex:  # noqa: BLE001
            salida["correo"] = {"enviado": False, "proveedor": "error", "detalle": str(ex)[:200]}
    return salida


async def notificar_seguro(a: AsignacionCurso) -> dict:
    """Envoltura a prueba de fallos de `_notificar` (2026-09-22): cualquier excepción queda en el log y en
    el resultado que ve RH, pero la asignación se guarda. Úsala SIEMPRE desde los endpoints."""
    try:
        return await _notificar(a)
    except Exception as ex:  # noqa: BLE001
        traceback.print_exc()
        print(f"[capacitacion] ⚠️ No se pudo avisar de la asignación {a.codigo}: {ex}", flush=True)
        return {"whatsapp": None, "correo": None, "error": f"No se pudo enviar el aviso: {str(ex)[:200]}"}


def _nueva_asignacion(db: Session, curso: Curso, tipo: str, asignado_por: str, **campos) -> AsignacionCurso:
    a = AsignacionCurso(codigo="TMP", curso_id=curso.id, tipo=tipo, token=secrets.token_urlsafe(24), asignado_por=asignado_por, **campos)
    db.add(a)
    db.flush()
    a.codigo = f"ASIG-{5000 + a.id}"
    return a


async def asignar_a_postulacion(db: Session, p: Postulacion, curso: Curso, actor: str = "sistema", notificar: bool = True) -> AsignacionCurso:
    """Curso como filtro de la vacante: se asigna al candidato (una sola vez por postulación) y se le manda
    la liga por WhatsApp/correo. Lo usa candidatos.py cuando la postulación queda apta."""
    existente = db.query(AsignacionCurso).filter(AsignacionCurso.curso_id == curso.id, AsignacionCurso.postulacion_id == p.id).first()
    if existente:
        return existente
    a = _nueva_asignacion(db, curso, "candidato", actor, postulacion_id=p.id)
    envio = await notificar_seguro(a) if notificar else {}
    registrar(db, actor, "curso_asignado_candidato", "postulacion", p.codigo, {"curso": curso.codigo, "asignacion": a.codigo, "envio": envio})
    return a


@router.post("/{codigo}/asignar", status_code=201)
async def asignar(codigo: str, datos: AsignarIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Un mismo curso publicado → colaboradores, candidatos (postulaciones) y/o externos. Idempotente por
    persona: si ya tiene una asignación no completada de este curso, se reutiliza (no se reavisa)."""
    curso = _por_codigo(db, codigo, cuenta.id)
    if curso.estado != "Publicado":
        raise HTTPException(409, "Solo se pueden asignar cursos publicados.")
    if not (datos.colaborador_ids or datos.postulacion_ids or datos.externos):
        raise HTTPException(400, "Elige al menos una persona (colaborador, candidato o externo).")

    creadas: List[AsignacionCurso] = []
    reutilizadas: List[AsignacionCurso] = []
    no_encontrados: List[str] = []

    for cod in datos.colaborador_ids:
        col = db.query(Colaborador).filter(Colaborador.codigo == cod, Colaborador.cuenta_id == cuenta.id, Colaborador.eliminado_en.is_(None)).first()
        if not col:
            no_encontrados.append(cod)
            continue
        prev = db.query(AsignacionCurso).filter(AsignacionCurso.curso_id == curso.id, AsignacionCurso.colaborador_id == col.id, AsignacionCurso.estado != "completado").first()
        if prev:
            reutilizadas.append(prev)
            continue
        creadas.append(_nueva_asignacion(db, curso, "colaborador", u.nombre, colaborador_id=col.id))

    for cod in datos.postulacion_ids:
        p = db.query(Postulacion).join(Candidato, Postulacion.candidato_id == Candidato.id).filter(
            Postulacion.codigo == cod, Postulacion.cuenta_id == cuenta.id, Candidato.eliminado_en.is_(None)
        ).first()
        if not p:
            no_encontrados.append(cod)
            continue
        prev = db.query(AsignacionCurso).filter(AsignacionCurso.curso_id == curso.id, AsignacionCurso.postulacion_id == p.id, AsignacionCurso.estado != "completado").first()
        if prev:
            reutilizadas.append(prev)
            continue
        creadas.append(_nueva_asignacion(db, curso, "candidato", u.nombre, postulacion_id=p.id))

    for ext in datos.externos:
        creadas.append(_nueva_asignacion(
            db, curso, "externo", u.nombre,
            externo_nombre=ext.nombre.strip(), externo_correo=ext.correo.strip().lower(), externo_telefono=ext.telefono.strip(), externo_organizacion=ext.organizacion.strip(),
        ))

    if not creadas and not reutilizadas:
        raise HTTPException(404, "Ninguna de las personas indicadas existe.")

    envios = []
    for a in creadas:
        envio = await notificar_seguro(a) if datos.notificar and (a.telefono_persona or a.correo_persona) else {}
        envios.append({"asignacion": a.codigo, "tipo": a.tipo, "persona": a.nombre_persona, "liga": liga_asignacion(a), **envio})
    registrar(db, u.nombre, "curso_asignado", "curso", curso.codigo, {"creadas": [a.codigo for a in creadas], "reutilizadas": [a.codigo for a in reutilizadas], "no_encontrados": no_encontrados, "envios": envios})
    db.commit()
    return {"asignaciones": [asignacion_dict(a) for a in [*creadas, *reutilizadas]], "envios": envios, "noEncontrados": no_encontrados}


# ------------------------------------------------------------
# «Botón Mágico» de la Expo (2026-09-23) — liga pública al instante
# ------------------------------------------------------------

ORGANIZACION_DEMO = "Demo Expo"
NOMBRE_INVITADO_DEMO = "Invitado (demo)"


@router.post("/{codigo}/demo", status_code=201)
def liga_demo(
    codigo: str, nueva: bool = False, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Liga pública inmediata para demostrar el curso, SIN asignarlo a nadie real y SIN mandar
    WhatsApp ni correo (nunca llama a `_notificar`).

    Se crea una asignación de tipo «externo» con nombre de invitado: así la sala abre directo (no pide
    registro) y NO se toca la tabla de Colaboradores — el roster es la base maestra y no se contamina
    con datos de demo. Queda marcada con `externo_organizacion = "Demo Expo"` para distinguirla en el
    tablero y en los reportes.

    Funciona aunque el curso siga en Borrador (en la Expo se genera y se muestra en el mismo minuto);
    `nueva=true` fuerza una liga limpia en vez de reutilizar la anterior."""
    curso = _por_codigo(db, codigo, cuenta.id)
    if not curso.modulos:
        raise HTTPException(409, "El curso todavía no tiene contenido: genéralo antes de crear la liga de demostración.")

    a = None
    if not nueva:
        # se reutiliza la liga de demo viva (no completada) para no llenar el tablero de ligas sueltas
        a = (
            db.query(AsignacionCurso)
            .filter(
                AsignacionCurso.curso_id == curso.id,
                AsignacionCurso.tipo == "externo",
                AsignacionCurso.externo_organizacion == ORGANIZACION_DEMO,
                AsignacionCurso.estado != "completado",
            )
            .order_by(AsignacionCurso.id.desc())
            .first()
        )
    reutilizada = a is not None
    if a is None:
        a = _nueva_asignacion(
            db, curso, "externo", u.nombre,
            externo_nombre=NOMBRE_INVITADO_DEMO, externo_organizacion=ORGANIZACION_DEMO,
        )
        registrar(db, u.nombre, "curso_liga_demo", "curso", curso.codigo,
                  {"asignacion": a.codigo, "modo": "expo", "sin_notificacion": True, "correo_rh": u.correo})
    db.commit()

    liga = liga_asignacion(a)
    return {
        "asignacion": asignacion_dict(a),
        "token": a.token,
        "liga": liga,
        # Modo Tótem (2026-09-21): pantalla vertical 1080×1920, video a tamaño real y botones táctiles.
        "ligaTotem": f"{liga}?totem=1",
        "reutilizada": reutilizada,
        "estadoCurso": curso.estado,
    }


@router.get("/{codigo}/asignaciones")
def asignaciones_curso(codigo: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    curso = _por_codigo(db, codigo, cuenta.id)
    return [asignacion_dict(a) for a in db.query(AsignacionCurso).filter(AsignacionCurso.curso_id == curso.id).order_by(AsignacionCurso.id.desc()).all() if a.viva]


# ------------------------------------------------------------
# Sala pública: la persona cursa módulo por módulo y contesta la evaluación (1 pregunta por pantalla)
# ------------------------------------------------------------


def _asignacion_por_token(db: Session, token: str) -> AsignacionCurso:
    a = db.query(AsignacionCurso).filter(AsignacionCurso.token == token).first()
    if not a or not a.curso or a.curso.estado == "Archivado":
        raise HTTPException(404, "Esta liga de curso no existe o ya no está activa.")
    return a


@router.get("/publica/{token}")
def publica(token: str, db: Session = Depends(get_db)):
    return asignacion_publica_dict(_asignacion_por_token(db, token))


class RegistroExternoIn(BaseModel):
    nombre: str
    correo: str = ""
    telefono: str = ""


@router.post("/publica/{token}/registro")
def registro_externo(token: str, datos: RegistroExternoIn, db: Session = Depends(get_db)):
    """Liga abierta para externos: antes de empezar se pide solo nombre y correo o WhatsApp."""
    a = _asignacion_por_token(db, token)
    if a.tipo != "externo":
        raise HTTPException(409, "Esta liga ya está a nombre de una persona.")
    if not datos.nombre.strip():
        raise HTTPException(400, "Escribe tu nombre.")
    if not (datos.correo.strip() or datos.telefono.strip()):
        raise HTTPException(400, "Deja tu correo o tu WhatsApp para enviarte tu resultado.")
    a.externo_nombre = datos.nombre.strip()[:200]
    a.externo_correo = datos.correo.strip().lower()[:200]
    a.externo_telefono = datos.telefono.strip()[:30]
    registrar(db, "externo", "curso_registro_externo", "asignacion_curso", a.codigo, {"nombre": a.externo_nombre})
    db.commit()
    return asignacion_publica_dict(a)


# ------------------------------------------------------------
# Instructor con avatar (restaurado 2026-09-18): el avatar explica el módulo y responde dudas; convive
# con el contenido escrito (que sigue siendo la fuente de verdad del curso). Sin Anam → chat de texto.
# ------------------------------------------------------------


def _modulo_por_orden(a: AsignacionCurso, orden: int) -> ModuloCurso:
    for m in sorted(a.curso.modulos or [], key=lambda x: x.orden):
        if m.orden == orden:
            return m
    raise HTTPException(404, "Ese módulo no existe en el curso.")


def _prompt_instructor(a: AsignacionCurso, m: ModuloCurso) -> str:
    total = len(a.curso.modulos or [])
    nombre = (a.nombre_persona or "").split(" ")[0] or "la persona"
    return (
        f"Eres el instructor virtual de Red Human AI (México) impartiendo el curso «{a.curso.titulo}» a {nombre}. "
        f"Estás en el módulo «{m.titulo}» ({m.orden} de {total}).\n\n"
        f"Objetivo del curso: {a.curso.objetivo}\n\nContenido del módulo (fuente de verdad, no inventes nada fuera de esto):\n{m.contenido}\n\n"
        "Instrucciones: (1) explica el contenido de forma conversacional, cálida y clara, en español mexicano, en "
        "mensajes cortos (se dicen en voz alta); (2) responde las dudas de la persona SOLO con base en el contenido; si "
        "algo no está en el módulo, dilo y sugiere consultarlo con Recursos Humanos; (3) al terminar de explicar, invita a "
        "la persona a dar clic en «Siguiente módulo»; (4) nunca pidas ni menciones datos sensibles (salud, embarazo, "
        "religión, estado civil, orientación)."
    )


class SesionCursoIn(BaseModel):
    modulo: int  # orden (1-based) del módulo que se está viendo


@router.post("/publica/{token}/sesion")
async def sesion_instructor(token: str, datos: SesionCursoIn, db: Session = Depends(get_db)):
    """Sesión del avatar instructor para UN módulo (token efímero de Anam) o modo texto si no hay
    avatar. Nunca tumba la sala: cualquier falla cae a texto."""
    a = _asignacion_por_token(db, token)
    if a.tipo == "externo" and not a.externo_nombre:
        raise HTTPException(409, "Regístrate con tu nombre antes de empezar.")
    m = _modulo_por_orden(a, datos.modulo)
    saludo = f"¡Hola! Soy tu instructor de Red Human. Vamos con el módulo «{m.titulo}». Te lo explico y me preguntas lo que quieras."
    ses = None
    try:
        ses = await crear_sesion_avatar("Instructor Red Human", _prompt_instructor(a, m), saludo)
    except Exception as ex:  # el avatar nunca debe tumbar la sala: cae a modo texto
        print(f"[ERROR] crear_sesion_avatar falló (curso {a.codigo}): {str(ex)}", flush=True)
        registrar(db, "sistema", "avatar_error", "asignacion_curso", a.codigo, {"error": str(ex)[:300]})
    if a.iniciado_en is None:
        a.iniciado_en = datetime.now(timezone.utc)
        a.estado = "en_curso"
    db.commit()
    base = {"modulo": datos.modulo, "mensajes": [{"rol": "assistant", "texto": saludo}]}
    if ses is None:
        return {"modo": "texto", **base}
    return {"modo": "avatar", **ses, **base}


class TurnoCursoIn(BaseModel):
    modulo: int
    texto: str


@router.post("/publica/{token}/turno")
def turno_instructor(token: str, datos: TurnoCursoIn, db: Session = Depends(get_db)):
    """Pregunta escrita al instructor sobre el módulo (modo texto o junto al avatar). El historial
    por módulo se guarda en `AsignacionCurso.transcript`."""
    a = _asignacion_por_token(db, token)
    if not datos.texto.strip():
        raise HTTPException(400, "Escribe tu pregunta.")
    m = _modulo_por_orden(a, datos.modulo)
    bloques = list(a.transcript or [])
    bloque = next((b for b in bloques if b.get("modulo") == datos.modulo), None)
    historial = list(bloque.get("mensajes", [])) if bloque else []
    historial = historial + [{"rol": "user", "texto": datos.texto.strip()[:2000]}]
    t, con_ia = ia.curso_turno(_prompt_instructor(a, m), historial)
    historial = historial + [{"rol": "assistant", "texto": t.respuesta}]
    if bloque:
        bloque["mensajes"] = historial[-40:]
    else:
        bloques.append({"modulo": datos.modulo, "titulo": m.titulo, "mensajes": historial})
    a.transcript = bloques
    db.commit()
    return {"respuesta": t.respuesta, "ia": con_ia, "mensajes": historial}


def _datos_pdf_curso(curso: Curso, a: Optional[AsignacionCurso] = None) -> dict:
    empresa = curso.cuenta.nombre_comercial if getattr(curso, "cuenta", None) and curso.cuenta else ""
    d = {
        "titulo": curso.titulo, "categoria": curso.categoria, "objetivo": curso.objetivo, "duracion_horas": curso.duracion_horas,
        "empresa": empresa,
        "modalidad": curso.modalidad or "autoguiado",
        "duracion_texto": curso.duracion_texto or "",
        # 2026-09-19: el PDF es MATERIAL DE APOYO — en Instructor IA lleva resumen + puntos clave (no el guion hablado);
        # en Autoguiado el contenido modular ya es breve y se incluye completo.
        "modulos": [
            {"orden": m.orden, "titulo": m.titulo,
             "contenido": (m.contenido if (curso.modalidad or "autoguiado") == "autoguiado" or not (m.resumen or m.puntos_clave) else m.resumen),
             "puntos_clave": list(m.puntos_clave or [])}
            for m in sorted(curso.modulos or [], key=lambda x: x.orden)
        ],
        "evaluacion": [{"pregunta": q.get("pregunta"), "opciones": q.get("opciones") or []} for q in (curso.evaluacion or [])],
    }
    if a is not None:
        d["persona"] = a.nombre_persona
        if a.estado == "completado" and a.calificacion is not None:
            r = a.resultado_evaluacion or {}
            d["resultado"] = {"calificacion": a.calificacion, "aprobado": a.aprobado, "aciertos": r.get("aciertos"), "total": r.get("total"), "minimo": curso.calificacion_minima}
    return d


def _respuesta_pdf(contenido: bytes, nombre: str) -> Response:
    return Response(content=contenido, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{nombre}"'})


@router.get("/publica/{token}/pdf")
def pdf_publico(token: str, db: Session = Depends(get_db)):
    """Descarga del contenido del curso (2026-09-18) para la persona asignada — solo si ya empezó o
    terminó (la liga es la credencial)."""
    a = _asignacion_por_token(db, token)
    if a.tipo == "externo" and not a.externo_nombre:
        raise HTTPException(409, "Regístrate con tu nombre antes de descargar el material.")
    return _respuesta_pdf(pdf_curso(_datos_pdf_curso(a.curso, a)), f"{slugificar(a.curso.titulo)}.pdf")


@router.get("/{codigo}/pdf")
def pdf_curso_rh(codigo: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Material del curso en PDF para RH (ficha del curso)."""
    curso = _por_codigo(db, codigo, cuenta.id)
    return _respuesta_pdf(pdf_curso(_datos_pdf_curso(curso)), f"{slugificar(curso.titulo)}.pdf")


class AvanzarIn(BaseModel):
    modulo: int  # orden (1-based) del módulo que se terminó


@router.post("/publica/{token}/avanzar")
def avanzar(token: str, datos: AvanzarIn, db: Session = Depends(get_db)):
    a = _asignacion_por_token(db, token)
    if a.estado == "completado":
        return asignacion_publica_dict(a)
    if a.tipo == "externo" and not a.externo_nombre:
        raise HTTPException(409, "Regístrate con tu nombre antes de empezar.")
    total = len(a.curso.modulos)
    if datos.modulo != a.modulo_actual + 1 or datos.modulo > total:
        raise HTTPException(409, f"Sigue en orden: te toca el módulo {min(a.modulo_actual + 1, total)}.")
    if a.iniciado_en is None:
        a.iniciado_en = datetime.now(timezone.utc)
    a.estado = "en_curso"
    a.modulo_actual = datos.modulo
    registrar(db, a.nombre_persona or "persona", "modulo_completado", "asignacion_curso", a.codigo, {"modulo": datos.modulo, "de": total})
    db.commit()
    return asignacion_publica_dict(a)


class ResponderIn(BaseModel):
    indice: int      # índice (0-based) de la pregunta
    respuesta: int   # índice (0-based) de la opción elegida


@router.post("/publica/{token}/responder")
async def responder(token: str, datos: ResponderIn, db: Session = Depends(get_db)):
    """Una pregunta a la vez. Se califica automáticamente; al contestar la última se calcula el resultado
    (Aprobado / No aprobado + %) y se guarda donde corresponde según el tipo de persona."""
    a = _asignacion_por_token(db, token)
    if a.estado == "completado":
        return {"terminado": True, "correcta": None, "explicacion": "", "siguiente": None, **asignacion_publica_dict(a)}
    curso = a.curso
    if a.modulo_actual < len(curso.modulos):
        raise HTTPException(409, "Termina todos los módulos antes de la evaluación.")
    preguntas = curso.evaluacion or []
    respuestas = list((a.resultado_evaluacion or {}).get("respuestas") or [])
    if datos.indice != len(respuestas) or datos.indice >= len(preguntas):
        raise HTTPException(409, f"Te toca la pregunta {len(respuestas) + 1} de {len(preguntas)}.")
    q = preguntas[datos.indice]
    if not (0 <= datos.respuesta < len(q.get("opciones") or [])):
        raise HTTPException(400, "Elige una de las opciones.")
    correcta = int(q.get("correcta", 0)) == datos.respuesta
    respuestas.append({"indice": datos.indice, "respuesta": datos.respuesta, "correcta": correcta})
    a.resultado_evaluacion = {**(a.resultado_evaluacion or {}), "respuestas": respuestas}

    terminado = len(respuestas) >= len(preguntas)
    if terminado:
        aciertos = sum(1 for r in respuestas if r["correcta"])
        calificacion = round(aciertos / len(preguntas) * 100) if preguntas else 0
        a.calificacion = calificacion
        a.aprobado = calificacion >= (curso.calificacion_minima or 70)
        a.estado = "completado"
        a.completado_en = datetime.now(timezone.utc)
        a.resultado_evaluacion = {"respuestas": respuestas, "aciertos": aciertos, "total": len(preguntas), "calificacion": calificacion, "aprobado": a.aprobado, "minimo": curso.calificacion_minima}
        # Resultado donde corresponde: el candidato lo lleva en su evaluación (analisis.capacitacion).
        if a.tipo == "candidato" and a.postulacion is not None:
            p = a.postulacion
            analisis = dict(p.analisis or {})
            hist = [x for x in (analisis.get("capacitacion") or []) if x.get("curso") != curso.codigo]
            hist.append({"curso": curso.codigo, "titulo": curso.titulo, "calificacion": calificacion, "aprobado": a.aprobado, "fecha": a.completado_en.isoformat(), "asignacion": a.codigo})
            analisis["capacitacion"] = hist
            p.analisis = analisis
        registrar(db, a.nombre_persona or "persona", "curso_completado", "asignacion_curso", a.codigo, {"curso": curso.codigo, "tipo": a.tipo, "calificacion": calificacion, "aprobado": a.aprobado})
    db.commit()
    if terminado and a.tipo == "candidato" and a.postulacion is not None:
        # Cambio 28: «Capacitación» de la ruta — aprobado → la ruta sigue sola; reprobado → «No aprobó el curso» (RH decide)
        from ..services import proceso as sproc

        await sproc.avanzar_seguro(db, a.postulacion)
        db.commit()
    return {
        "terminado": terminado,
        "correcta": correcta,
        "explicacion": q.get("explicacion", ""),
        "siguiente": None if terminado else len(respuestas),
        **asignacion_publica_dict(a),
    }
