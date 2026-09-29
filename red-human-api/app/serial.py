"""Serializadores → formas exactas que consume el frontend (lib/data.ts / lib/phase2.ts)."""

from datetime import datetime, timezone
from typing import List, Optional

from . import fechas
from .config import settings
from .models import CONCLUSIONES_ENTREVISTA, NIVELES_RECORDATORIO, estado_documento_onboarding, AsignacionCurso, Archivo, Candidato, Colaborador, Curso, Documento, Entrevista, Expediente, Postulacion, Vacante
from .services.avatar import avatar_activo
from .services.ia import texto_preguntas, texto_util_candidato

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def hace(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    s = int(delta.total_seconds())
    if s < 3600:
        return f"hace {max(1, s // 60)} min"
    if s < 86400:
        return f"hace {s // 3600} h"
    if s < 7 * 86400:
        d = s // 86400
        return f"hace {d} día{'s' if d > 1 else ''}"
    return f"hace {s // (7 * 86400)} semana{'s' if s // (7 * 86400) > 1 else ''}"


iso = fechas.iso  # 2026-09-29: siempre UTC con «Z» (regla única en app/fechas.py)


def fecha_corta(dt: Optional[datetime]) -> str:
    return f"{dt.day} {MESES[dt.month - 1]}" if dt else ""


# ------------------------------------------------------------
# Módulo 1 · Vacantes
# ------------------------------------------------------------


def nombre_empresa(cuenta, cliente=None, mostrar_cliente: bool = True) -> str:
    """Regla única de identidad de empresa (Fase 4, Punto 1) SIN necesitar una Vacante: el
    Cliente (nombre visible) solo si existe y está marcado para mostrarse; si no, el nombre
    comercial de la Cuenta. La usa el generador de vacantes antes de que la vacante exista."""
    if cliente is not None and mostrar_cliente:
        return cliente.nombre_visible
    if cuenta is not None:
        return cuenta.nombre_comercial
    return ""


def nombre_empresa_candidato(v: Vacante) -> str:
    """Nombre de empresa que debe ver el candidato (Fase B, punto 10): el Cliente real solo si
    hay uno asignado y `mostrar_cliente_candidato` está activo; si no, el nombre comercial de la
    Cuenta — nunca el texto libre `empresa` salvo que la vacante no tenga Cuenta (no debería
    pasar tras la migración de Fase A, es solo un respaldo defensivo)."""
    if v.cliente_id and v.mostrar_cliente_candidato and v.cliente:
        return v.cliente.nombre_visible
    if v.cuenta:
        return v.cuenta.nombre_comercial
    return v.empresa or ""


def vacante_dict(
    v: Vacante,
    n_candidatos: int = 0,
    n_nuevos: int = 0,
    embudo: Optional[dict] = None,
    colaboradores: Optional[List[str]] = None,
) -> dict:
    return {
        "id": v.codigo,
        "slug": v.slug or "",
        "titulo": v.titulo,
        "area": v.area,
        "empresa": v.empresa,
        # --- Fase B: Cliente/Responsable/Colaboradores/visibilidad ---
        "cliente": v.cliente.nombre if v.cliente else None,
        "clienteId": v.cliente_id,
        "responsable": v.responsable.nombre if v.responsable else None,
        "colaboradores": colaboradores or [],
        "mostrarClienteCandidato": v.mostrar_cliente_candidato,
        # nombre que ve el candidato — RH siempre ve la relación real arriba, sin importar el flag
        "nombreEmpresa": nombre_empresa_candidato(v),
        "ubicacion": v.ubicacion,
        "modalidad": v.modalidad,
        "sueldo": v.sueldo,
        # Parte 3: sueldo estructurado (el texto de arriba es el derivado que se muestra)
        "sueldoDesde": v.sueldo_desde,
        "sueldoHasta": v.sueldo_hasta,
        "sueldoMoneda": v.sueldo_moneda or "MXN",
        "sueldoPeriodicidad": v.sueldo_periodicidad or "",
        "estado": v.estado,
        "enfoqueEntrevista": v.enfoque_entrevista or "profesional",
        "candidatos": n_candidatos,
        "nuevos": n_nuevos,
        "publicada": hace(v.creada_en) if v.estado == "Publicada" else "borrador",
        "plataformas": v.plataformas or [],
        # contenido base
        "descripcion": v.descripcion,
        "requisitos": v.requisitos,
        "resumen": v.resumen or "",
        "perfilIdeal": v.perfil_ideal or "",
        "responsabilidades": v.responsabilidades or [],
        "requisitosDeseables": v.requisitos_deseables or [],
        "beneficios": v.beneficios or [],
        "palabrasClave": v.palabras_clave or [],
        "seniority": v.seniority or "",
        "avisosCumplimiento": v.avisos_cumplimiento or [],
        # publicaciones por plataforma: {occ|linkedin|portal|whatsapp: {titulo, copy, page, etiquetas}}
        "publicaciones": v.publicaciones or {},
        "textoWhatsapp": v.texto_whatsapp or "",
        "textoBolsa": v.texto_bolsa or "",
        # prefiltro
        "preguntas_filtro": texto_preguntas(v.preguntas_filtro),
        "criterios": [p for p in (v.preguntas_filtro or []) if isinstance(p, dict)],
        # Fase 4: prefiltro por WhatsApp independiente + ubicación estructurada
        "criteriosWhatsapp": [p for p in (v.preguntas_filtro_whatsapp or []) if isinstance(p, dict)],
        "ubicacionEstado": v.ubicacion_estado or "",
        "ubicacionMunicipio": v.ubicacion_municipio or "",
        # Capacitación universal (2026-09-16): curso que se asigna como filtro al quedar apto
        "cursoFiltroId": v.curso_filtro.codigo if v.curso_filtro else None,
        # Evaluaciones (2026-09-28): solo SUGERENCIAS + aviso opcional al enviar a Onboarding
        # tipos con las claves unificadas (2026-09-29): «medico»/«socioeconomico» previos se leen como «medica»/«socioeconomica»
        "evaluacionesSugeridas": [{**x, "tipo": {"medico": "medica", "socioeconomico": "socioeconomica"}.get(x.get("tipo"), x.get("tipo"))}
                                  for x in (v.evaluaciones_sugeridas or [])],
        "avisarEvaluacionesAntesOnboarding": bool(v.avisar_evaluaciones_antes_onboarding),
        "cursoFiltroTitulo": v.curso_filtro.titulo if v.curso_filtro else None,
        # embudo de esta vacante (conecta con el pipeline de candidatos)
        "embudo": embudo or {},
        "eliminadaEn": iso(v.eliminada_en),
        "eliminadaPor": v.eliminada_por or "",
        "creada": iso(v.creada_en),
        # Fase C: fecha de primera publicación (ISO string, null si nunca se publicó)
        "publicadaEn": iso(v.publicada_en),
        "actualizada": iso(v.actualizada_en),
    }


# ------------------------------------------------------------
# Módulo 1 · Candidatos
# ------------------------------------------------------------


def archivo_dict(a: Archivo) -> dict:
    return {
        "id": a.id,
        "tipo": a.tipo,
        "nombre": a.nombre,
        "mime": a.mime,
        "tamano": a.tamano,
        "estado": a.estado,
        "notas": a.notas_ia or "",
        "subidoPor": a.subido_por,
        "subido": hace(a.subido_en),
    }


def _dedupe_cap(items: List[Optional[str]], maximo: int) -> List[str]:
    vistos = set()
    salida: List[str] = []
    for it in items:
        if not it:
            continue
        clave = it.strip().casefold()
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append(it.strip())
        if len(salida) >= maximo:
            break
    return salida


def _sintesis_global(p: Postulacion) -> dict:
    """Puntos 3 (D/E/F/G) y 5: combina CV + Prefiltro + Entrevista IA + Entrevista Humana en
    una sola síntesis — determinista, SIN llamada a IA nueva (decisión confirmada). Se calcula
    al vuelo en cada lectura, nunca se persiste: así "recalcular cuando el CV se carga después"
    se cumple gratis, sin enganchar este cálculo en cada punto de mutación.

    Fase 2: todo sale de la Postulación — las entrevistas de OTRA postulación de la misma
    persona no cuentan para esta (cada aplicación se evalúa por sí sola)."""
    a = p.analisis or {}
    score = p.score  # SOLO del Análisis de CV (el prefiltro ya no genera score, 2026-09-13)
    resultado_apto = p.resultado_apto
    ultima_eh = _ultima_entrevista_humana_con_resultado(p)  # Evaluaciones unificadas (2026-09-29)
    ultima_ent = p.entrevistas[-1] if p.entrevistas else None

    # 2026-09-13: solo una Entrevista Red Human EVALUADA entra a la evaluación integral. Una
    # interrumpida (sin respuestas / desconexión) o parcial no aporta score ni fortalezas.
    entrevista_valida = bool(ultima_ent and ultima_ent.estado == "evaluada" and ultima_ent.evaluacion)
    eval_ia = (ultima_ent.evaluacion or {}) if entrevista_valida else {}
    match_ia = eval_ia.get("match_perfil")
    respuestas = a.get("respuestas_prefiltro") or []
    hay_cv = bool(a.get("requisitos_cumplidos") or a.get("brechas") or a.get("fortalezas_cv"))

    # --- C. Prefiltro: SOLO un status de entrada (Cumple / No cumple) — no participa en la
    # evaluación integral ni aporta afinidad, fortalezas ni puntos por validar (2026-09-13). ---
    prefiltro_resumen = None
    if respuestas or a.get("prefiltro_resultado"):
        cumple_n = sum(1 for r in respuestas if r.get("cumple") is True)
        incumplidos = [r.get("criterio") or r.get("pregunta") for r in respuestas if r.get("cumple") is False]
        prefiltro_resumen = {
            "cumple": cumple_n, "total": len(respuestas), "incumplidos": incumplidos,
            "resultado": a.get("prefiltro_resultado") or ("no_cumple" if p.estado == "no_cumple" else "cumple" if p.prefiltro_completo else None),
        }

    # --- Status de la Entrevista Red Human (bloque propio en la ficha) ---
    entrevista_status = None
    if ultima_ent:
        ev_raw = ultima_ent.evaluacion or {}
        entrevista_status = {
            "codigo": ultima_ent.codigo, "estado": ultima_ent.estado, "cierre": ultima_ent.cierre or "",
            "motivo": ultima_ent.motivo or "", "turnosCandidato": sum(1 for m in (ultima_ent.transcript or []) if m.get("rol") == "user"),
            "faltante": list(ev_raw.get("faltante") or []), "motivoIa": ev_raw.get("motivo_ia") or "",
            "intentosPrevios": len(ultima_ent.intentos_previos or []),
            "accionSiguiente": "reintentar" if ultima_ent.estado in ("interrumpida", "parcial") else None,
            # 2026-09-17: respuestas con contenido real → habilita «Evaluar con lo que hay» (RH)
            "turnosUtiles": texto_util_candidato(ultima_ent.transcript or [])[0],
        }

    # --- D. Afinidad (EVALUACIÓN INTEGRAL): Análisis de CV + Entrevista Red Human válida,
    # ajustada por la Entrevista Humana si la hay (la señal más autoritativa). El prefiltro
    # queda estrictamente fuera. Cada fuente usada queda citada en `sintesisAfinidad`. ---
    afinidad: Optional[int] = None
    fuentes: List[str] = []
    if score and hay_cv:
        afinidad = score
        fuentes.append(f"Análisis de CV: {score}/100 de ajuste")
    if match_ia is not None:
        afinidad = round(((afinidad or 0) + match_ia) / 2) if afinidad is not None else match_ia
        fuentes.append(f"Entrevista Red Human: {match_ia}/100 de afinidad")
    if ultima_eh and ultima_eh.conclusion in ("avanzar", "no_avanzar"):
        legible = CONCLUSIONES_ENTREVISTA[ultima_eh.conclusion]
        fuentes.append(f"Entrevista humana con {ultima_eh.realizada_por or ultima_eh.evaluador_nombre or 'RH'}: {legible}")
        objetivo = 100 if ultima_eh.conclusion == "avanzar" else 0
        afinidad = round(objetivo if afinidad is None else afinidad * 0.5 + objetivo * 0.5)
    if afinidad is not None:
        afinidad = max(0, min(100, afinidad))

    # --- E/F. Fortalezas principales y puntos por validar — unión deduplicada de lo que cada
    # etapa YA calificó, prioridad a la señal más reciente (Entrevista > Prefiltro > CV). ---
    fortalezas = _dedupe_cap(
        [*(eval_ia.get("fortalezas") or []),
         *(a.get("fortalezas_cv") or []),
         *(a.get("requisitos_cumplidos") or [])],
        4,
    )
    puntos_por_validar = _dedupe_cap(
        [*(eval_ia.get("riesgos") or []),
         *[f"No se cubrió en la entrevista: {t}" for t in (eval_ia.get("faltante") or [])],
         *(a.get("brechas") or []),
         *(["Requiere otra entrevista" + (f": {ultima_eh.comentarios[:200]}" if ultima_eh.comentarios else "")]
           if ultima_eh and ultima_eh.conclusion == "requiere_otra_entrevista" else [])],
        4,
    )

    # --- G. Recomendación de Red Human — reusa el "más reciente gana" de resultado_apto
    # (Fase C/D), nunca reinventa la lógica de negocio. ---
    recomendacion: Optional[str] = None
    motivo = ""
    if resultado_apto is False:
        recomendacion = "No avanzar"
        motivo = "El resultado más reciente del proceso marca al candidato como no apto."
    elif ultima_eh and ultima_eh.conclusion == "no_avanzar":
        recomendacion = "No avanzar"
        motivo = "La entrevista humana concluyó no avanzar."
    elif ultima_eh and ultima_eh.conclusion == "avanzar":
        # Solo una recomendación: pasar a Contratación sigue siendo una acción manual de RH.
        recomendacion = "Avanzar a contratación"
        motivo = "La entrevista humana concluyó avanzar. La decisión de pasar a Contratación la toma RH."
    elif ultima_ent and ultima_ent.estado in ("interrumpida", "parcial") and not ultima_eh:
        # 2026-09-13: sin entrevista válida no se recomienda entrevista humana — primero reintentar.
        recomendacion = "Reintentar Entrevista Red Human"
        motivo = (
            "La Entrevista Red Human quedó sin respuestas del candidato." if ultima_ent.motivo == "sin_respuestas"
            else "La Entrevista Red Human quedó parcial: la información no alcanza para una evaluación integral." if ultima_ent.estado == "parcial"
            else "La Entrevista Red Human se interrumpió antes de terminar."
        )
    elif resultado_apto is True and entrevista_valida:
        recomendacion = "Realizar entrevista humana"
        motivo = (
            "La entrevista humana concluyó que se requiere otra entrevista antes de decidir."
            if ultima_eh and ultima_eh.conclusion == "requiere_otra_entrevista"
            else "Compatible según Análisis de CV y Entrevista Red Human, pero falta la validación de una Entrevista Humana."
        )
    elif resultado_apto is True:
        recomendacion = "Realizar Entrevista Red Human"
        motivo = "Pasó el prefiltro; la evaluación integral requiere la Entrevista Red Human."

    return {
        "prefiltroResumen": prefiltro_resumen,
        # 2026-09-16: prefiltro dual — respuestas del formulario web y contradicciones Web vs WhatsApp
        "respuestasWeb": a.get("respuestas_web") or [],
        "inconsistencias": a.get("inconsistencias") or [],
        "actividadesOmitidas": p.actividades_omitidas or [],
        "historial": list(p.historial or []),  # 2026-09-22: notas de decisiones humanas (nunca se borran)
        # Capacitación universal: cursos de filtro cursados por el candidato (resultado en su evaluación)
        "capacitacion": a.get("capacitacion") or [],
        "entrevistaStatus": entrevista_status,
        "evaluacionIntegral": bool(match_ia is not None or (score and hay_cv and entrevista_valida)),
        "afinidadGlobal": afinidad,
        "sintesisAfinidad": " · ".join(fuentes),
        "fortalezasPrincipales": fortalezas,
        "puntosPorValidar": puntos_por_validar,
        "recomendacionRedHuman": recomendacion,
        "recomendacionMotivo": motivo,
    }


def _persona_dict(c: Candidato) -> dict:
    """Ficha de la PERSONA (maestro de identidad) — va embebida en cada postulación como
    `candidato` y es lo que regresa candidato_dict()."""
    return {
        "id": c.codigo,
        "codigo": c.codigo,
        "nombre": c.nombre,
        "correo": c.correo,
        "telefono": c.telefono,
        "ubicacion": c.ubicacion or "",
        "experiencia": c.experiencia or "",
        "fuente": c.fuente,
        "esPrueba": c.es_prueba,
        "totalPostulaciones": len(c.postulaciones),
        "postulacionesActivas": len(c.postulaciones_activas),
        "archivos": len(c.archivos),
        "creadoEn": iso(c.creado_en),
    }


def _postulacion_resumen_dict(p: Postulacion) -> dict:
    """Renglón del historial de postulaciones de una persona (pestaña Resumen)."""
    return {
        "id": p.codigo,
        "puesto": p.vacante.titulo if p.vacante else "",
        "empresaVisible": nombre_empresa_candidato(p.vacante) if p.vacante else "",  # 2026-09-18: vista previa de correos
        "vacanteId": p.vacante.codigo if p.vacante else "",
        "etapa": p.etapa,
        "estado": p.estado,
        "score": p.score,
        "activa": p.activa,
        "motivoCierre": p.motivo_cierre,
        "creado": hace(p.creado_en),
        "creadoEn": iso(p.creado_en),
        "cerradaEn": iso(p.cerrada_en),
    }


def postulacion_dict(p: Postulacion, detalle: bool = False, n_mensajes: Optional[int] = None) -> dict:
    """La tarjeta del Kanban (decisión P4: una por Postulación). `id` es el código P-####
    — es lo que el frontend manda a /candidatos/{codigo}/...; los datos de persona vienen
    aplanados (nombre, teléfono…) por compatibilidad y también en `candidato`.
    `n_mensajes`: conteo ya calculado por el listado (evita cargar el chat completo por tarjeta)."""
    c = p.candidato
    v = p.vacante
    exp = p.expediente
    ultima = p.entrevistas[-1] if p.entrevistas else None
    total_postulaciones = len(c.postulaciones)

    base = {
        "id": p.codigo,
        "codigo": p.codigo,
        "postulacionId": p.id,
        # Convención: `candidatoId` es SIEMPRE lo que se manda a /candidatos/{codigo} (la
        # postulación) — igual que en entrevista_dict/expediente_dict; la persona va en
        # `candidatoCodigo` y en `candidato`.
        "candidatoId": p.codigo,
        "candidatoCodigo": c.codigo,
        "nombre": c.nombre,
        "puesto": v.titulo if v else "",
        "vacanteId": v.codigo if v else "",
        "vacanteTitulo": v.titulo if v else "",
        "fuente": c.fuente,
        "origen": p.origen,
        "estado": p.estado,
        "etapa": p.etapa,
        "score": p.score,
        "experiencia": c.experiencia or "",
        "ubicacion": c.ubicacion or "",
        "aplicado": hace(p.creado_en),
        "creadoEn": iso(p.creado_en),
        "tono": (c.id or 0) % 4,
        "evidencia": p.evidencia or "Prefiltro en curso.",
        "telefono": c.telefono,
        "correo": c.correo,
        "consentimiento": p.consentimiento,
        "prefiltroCompleto": p.prefiltro_completo,
        "activa": p.activa,
        "motivoCierre": p.motivo_cierre,
        "cerradaEn": iso(p.cerrada_en),
        "esPrueba": c.es_prueba or p.es_prueba,
        "totalPostulaciones": total_postulaciones,
        "yaAplicoAntes": total_postulaciones > 1,
        "enConversacion": c.postulacion_conversacion_id == p.id,
        # Evaluaciones unificadas (2026-09-29): las evaluaciones (entrevista humana incluida) ya no viajan aquí;
        # la ficha las pide a GET /evaluaciones/postulaciones/{codigo}.
        # --- Expediente (Contratación) — pertenece a ESTA postulación (decisión P5) ---
        "expedienteId": exp.id if exp else None,
        "expedienteProgreso": exp.progreso if exp else None,
        "recordatorioNivel": exp.nivel_recordatorio if exp else None,
        "recordatoriosEnviados": (exp.recordatorios_enviados or 0) if exp else None,
        "expedienteEstado": exp.estado if exp else None,
        "expedienteCondiciones": {
            "puesto": exp.puesto,
            "sueldo": exp.sueldo,
            "tipoContratacion": exp.tipo_contratacion,
            "ubicacion": exp.ubicacion,
            "jefeDirecto": exp.jefe_directo,
            "fechaIngreso": iso(exp.fecha_ingreso),
            "instruccionesIngreso": exp.instrucciones_ingreso or "",  # Fase 5
            "empresa": exp.empresa or "",  # 2026-09-19
            "duracionContrato": exp.duracion_contrato,  # 2026-09-20 (B2): solo Tiempo determinado
            "duracionUnidad": exp.duracion_unidad or "",
            "fechaTermino": iso(exp.fecha_termino),  # calculada, nunca capturada
            "guardadasEn": iso(exp.condiciones_guardadas_en),
            # listo para generar documentos: puesto + sueldo + tipo + fecha (lo mínimo de una carta/contrato)
            "completas": bool(exp.puesto and exp.sueldo and exp.tipo_contratacion and exp.fecha_ingreso),
        }
        if exp
        else None,
        # --- Entrevista IA ---
        "entrevistaId": ultima.codigo if ultima else None,
        "entrevistaEstado": ultima.estado if ultima else None,
        "entrevistaMatch": (ultima.evaluacion or {}).get("match_perfil") if ultima else None,
        "entrevistaRecomendacion": (ultima.evaluacion or {}).get("recomendacion") if ultima else None,
        "archivos": len(c.archivos),
        "mensajes": n_mensajes if n_mensajes is not None else len(p.mensajes),
        # --- Fase C ---
        "ultimaActividadEn": iso(p.ultima_actividad_en),
        "resultadoApto": p.resultado_apto,
        "clienteVacante": v.cliente.nombre if v and v.cliente else None,
        "clienteIdVacante": v.cliente_id if v else None,  # Fase 7A: para elegir contactos/entrevistador externo
        # --- Persona (maestro) ---
        "candidato": _persona_dict(c),
    }

    if not detalle:
        return base

    return {
        **base,
        **_sintesis_global(p),
        "cvDatos": c.cv_datos or {},
        "analisis": p.analisis or {},
        "listaArchivos": [archivo_dict(a) for a in c.archivos],
        "vacante": {
            "id": v.codigo,
            "titulo": v.titulo,
            "requisitos": v.requisitos,
            "preguntas": texto_preguntas(v.preguntas_filtro),
        }
        if v
        else None,
        "entrevistas": [
            {
                "id": e.codigo,
                "estado": e.estado,
                "tipo": e.tipo,
                "token": e.token,
                "evaluacion": e.evaluacion or None,
                "creada": hace(e.creada_en),
            }
            for e in p.entrevistas
        ],
        "consentimientoFecha": iso(p.consentimiento_fecha),
        # Otras postulaciones de la misma persona (más reciente primero) — pestaña Resumen.
        "historialPostulaciones": [_postulacion_resumen_dict(hp) for hp in reversed(c.postulaciones) if hp.id != p.id],
    }


def candidato_dict(c: Candidato, detalle: bool = False) -> dict:
    """Ficha de PERSONA. Fase 2: ya no es la tarjeta del Kanban (eso es postulacion_dict);
    se usa donde se habla de la persona en sí (dedup, historial). `postulaciones` trae el
    resumen de todas sus aplicaciones."""
    base = _persona_dict(c)
    base["postulaciones"] = [_postulacion_resumen_dict(p) for p in reversed(c.postulaciones)]
    if not detalle:
        return base
    return {**base, "cvDatos": c.cv_datos or {}, "listaArchivos": [archivo_dict(a) for a in c.archivos]}


# ------------------------------------------------------------
# Módulo 1 · Entrevistas
# ------------------------------------------------------------


def entrevista_dict(e: Entrevista) -> dict:
    p = e.postulacion
    c = e.candidato
    vac = p.vacante if p else None
    return {
        "id": e.codigo,
        # `candidatoId` es lo que el frontend manda a /candidatos/{codigo}: la Postulación.
        "candidatoId": p.codigo if p else (c.codigo if c else ""),
        "postulacionId": p.codigo if p else None,
        "candidatoCodigo": c.codigo if c else "",
        "nombre": c.nombre if c else "",
        "puesto": vac.titulo if vac else "",
        "tipo": e.tipo,
        "estado": e.estado,
        "token": e.token,
        "consentimiento": e.consentimiento,
        "programada": iso(e.programada_para),
        "creada": hace(e.creada_en),
        "guion": e.guion or {},
        "mensajes": len(e.transcript or []),
        "turnosCandidato": sum(1 for m in (e.transcript or []) if m.get("rol") == "user"),
        "evaluacion": e.evaluacion or None,
        # Fase 4: cómo cerró y cuándo; intentos previos si RH la reabrió.
        "cierre": e.cierre or "",
        "motivo": e.motivo or "",  # sin_respuestas | desconexion | parcial | "" (ver MOTIVOS_ENTREVISTA)
        "iniciadaEn": iso(e.iniciada_en),
        "finalizadaEn": iso(e.finalizada_en),
        "ultimaActividadEn": iso(e.ultima_actividad_en),
        "intentosPrevios": len(e.intentos_previos or []),
        "tono": (c.id if c else 0) % 4,
        "ligaMeet": e.liga_meet or "",
    }


# ------------------------------------------------------------
# Capacitación (Fase 1)
# ------------------------------------------------------------


def curso_dict(c: Curso, detalle: bool = False) -> dict:
    """Módulo universal (2026-09-16): resumen + (detalle) módulos, evaluación integrada y adjuntos."""
    asigs = [a for a in c.asignaciones if a.viva]  # 2026-09-18: sin colaboradores eliminados
    completadas = [a for a in asigs if a.estado == "completado"]
    base = {
        "id": c.codigo,
        "titulo": c.titulo,
        "categoria": c.categoria,
        "duracionHoras": c.duracion_horas,
        "duracion": texto_duracion_curso(c),  # 2026-09-19: libre (hotfix 2026-09-22: cursos legado sin duración)
        "modalidad": c.modalidad or "autoguiado",
        "objetivo": c.objetivo,
        "estado": c.estado,
        "obligatorio": c.obligatorio,
        "creadoPor": c.creado_por,
        "creado": hace(c.creado_en),
        "modulos": len(c.modulos),
        "preguntas": len(c.evaluacion or []),
        "calificacionMinima": c.calificacion_minima or 70,
        "asignados": len(asigs),
        "completados": len(completadas),
        "aprobados": sum(1 for a in completadas if a.aprobado),
        "adjuntos": [x.get("nombre") for x in (c.adjuntos or [])],
    }
    if detalle:
        base["contexto"] = c.contexto or ""
        base["listaModulos"] = [{"orden": m.orden, "titulo": m.titulo, "contenido": m.contenido} for m in sorted(c.modulos, key=lambda m: m.orden)]
        base["evaluacion"] = [
            {"pregunta": q.get("pregunta", ""), "tipo": q.get("tipo", "opcion"), "opciones": q.get("opciones") or [], "correcta": q.get("correcta", 0), "explicacion": q.get("explicacion", "")}
            for q in (c.evaluacion or [])
        ]
    return base


def texto_duracion_curso(c) -> str:
    """Duración legible de un curso. 2026-09-22 (hotfix): `modalidad`/`duracion_texto`/`duracion_horas` son
    columnas agregadas después (migraciones.sincronizar) y en filas viejas pueden venir NULL — formatearlas
    con `:g` reventaba la serialización (500) al abrir o asignar esos cursos."""
    texto = (getattr(c, "duracion_texto", "") or "").strip()
    if texto:
        return texto
    horas = getattr(c, "duracion_horas", None)
    try:
        return f"{float(horas):g} h" if horas else ""
    except (TypeError, ValueError):
        return ""


def asignacion_dict(a: AsignacionCurso) -> dict:
    """Fila del tablero único de seguimiento: quién (colaborador / candidato / externo), curso, avance y resultado."""
    total = len(a.curso.modulos) if a.curso else 0
    p = a.postulacion if a.tipo == "candidato" else None
    return {
        "id": a.codigo,
        "cursoId": a.curso.codigo if a.curso else "",
        "cursoTitulo": a.curso.titulo if a.curso else "",
        "tipo": a.tipo,
        "persona": a.nombre_persona or "(externo sin registrar)",
        "correo": a.correo_persona,
        "telefono": a.telefono_persona,
        "organizacion": a.externo_organizacion or "",
        "colaboradorId": a.colaborador.codigo if a.colaborador else None,
        "postulacionId": p.codigo if p else None,
        "vacante": p.vacante.titulo if p and p.vacante else None,
        "estado": a.estado,
        "moduloActual": a.modulo_actual,
        "totalModulos": total,
        "avance": round(a.modulo_actual / total * 100) if total else 0,
        "calificacion": a.calificacion,
        "aprobado": a.aprobado,
        "asignadoPor": a.asignado_por,
        "asignado": hace(a.asignado_en),
        "asignadoEn": iso(a.asignado_en),
        "iniciadoEn": iso(a.iniciado_en),
        "completado": iso(a.completado_en),
        "token": a.token,
        "liga": f"{settings.app_url}/capacitacion/{a.token}",
    }


def asignacion_publica_dict(a: AsignacionCurso) -> dict:
    """Lo que ve la persona en la sala pública: módulos completos (avanza uno por uno), la evaluación SIN
    las respuestas correctas (una pregunta por pantalla) y su resultado al terminar."""
    curso = a.curso
    modulos = sorted(curso.modulos, key=lambda m: m.orden) if curso else []
    preguntas = curso.evaluacion or [] if curso else []
    respondidas = len(((a.resultado_evaluacion or {}).get("respuestas")) or [])
    res = a.resultado_evaluacion or {}
    return {
        "persona": a.nombre_persona,
        "tipo": a.tipo,
        "requiereRegistro": a.tipo == "externo" and not a.externo_nombre,
        "curso": curso.titulo if curso else "",
        "modalidad": (curso.modalidad or "autoguiado") if curso else "autoguiado",
        "duracion": texto_duracion_curso(curso) if curso else "",
        "objetivo": curso.objetivo if curso else "",
        "categoria": curso.categoria if curso else "",
        "duracionHoras": curso.duracion_horas if curso else 0,
        "empresa": (curso.cuenta.nombre_comercial if curso and curso.cuenta else "") or "Red Human",
        "estado": a.estado,
        "modulosCompletados": a.modulo_actual,
        "totalModulos": len(modulos),
        "modulos": [{"orden": m.orden, "titulo": m.titulo, "contenido": m.contenido, "completado": i < a.modulo_actual} for i, m in enumerate(modulos)],
        "totalPreguntas": len(preguntas),
        "preguntasRespondidas": respondidas,
        "pregunta": (
            {"indice": respondidas, "pregunta": preguntas[respondidas].get("pregunta", ""), "tipo": preguntas[respondidas].get("tipo", "opcion"), "opciones": preguntas[respondidas].get("opciones") or []}
            if a.modulo_actual >= len(modulos) and respondidas < len(preguntas) and a.estado != "completado" else None
        ),
        "resultado": (
            {"calificacion": res.get("calificacion"), "aprobado": res.get("aprobado"), "aciertos": res.get("aciertos"), "total": res.get("total"), "minimo": res.get("minimo"),
             "detalle": [
                 {"pregunta": preguntas[r["indice"]].get("pregunta", ""), "correcta": r["correcta"], "explicacion": preguntas[r["indice"]].get("explicacion", "")}
                 for r in (res.get("respuestas") or []) if r.get("indice", 0) < len(preguntas)
             ]}
            if a.estado == "completado" else None
        ),
    }


# ------------------------------------------------------------
# Módulo 2 · Contratación e integración
# ------------------------------------------------------------


def documento_dict(d: Documento) -> dict:
    v = d.validacion or {}
    return {
        "nombre": d.tipo,
        "estado": d.estado,  # pendiente | revision | recibido | rechazado | no_aplica
        # Onboarding v2 (2026-09-28): Pendiente | Por revisar | Aprobado | Rechazado | No aplica
        "estadoOnboarding": estado_documento_onboarding(d),
        "motivoNoAplica": d.motivo_no_aplica or "",
        "interno": bool(d.interno),  # contrato firmado: documento de RH, fuera del porcentaje
        "aprobado": d.aprobado,
        "noAplicaPor": d.no_aplica_por or "",
        "obligatorio": d.obligatorio,
        "notas": d.notas_ia or "",
        "archivo": d.nombre_archivo or "",
        "tieneArchivo": bool(d.archivo),
        "mime": d.mime or "",
        "tamano": d.tamano or 0,
        "subido": hace(d.subido_en) if d.subido_en else "",
        "revisadoPor": d.revisado_por or "",
        # 2026-09-20 (B3): trazabilidad — solicitud (fecha/hora + canal), recepción (fecha/hora + canal), historial
        "solicitadoEn": iso(d.solicitado_en),
        "solicitadoCanal": d.solicitado_canal or "",
        "solicitudes": list(d.solicitudes or []),
        "recibidoEn": iso(d.recibido_en),
        "recibidoCanal": d.recibido_canal or "",
        # Estado simple para la pestaña «CV y documentos»: Pendiente | Recibido (recibido o digital en revisión)
        "estadoSimple": "Recibido" if d.entregado else ("Rechazado" if d.estado == "rechazado" else ("No aplica" if d.estado == "no_aplica" else "Pendiente")),
        "validacion": {
            "tipoDetectado": v.get("tipo_detectado"),
            "coincideTipo": v.get("coincide_tipo"),
            "legible": v.get("legible"),
            "completo": v.get("completo"),
            "vigente": v.get("vigente"),
            "coincideTitular": v.get("coincide_titular"),
            "motivoRechazo": v.get("motivo_rechazo"),
        }
        if v
        else None,
    }


def expediente_dict(e: Expediente) -> dict:
    p = e.postulacion
    c = e.candidato
    vac = p.vacante if p else None
    ultima = p.entrevistas[-1] if p and p.entrevistas else None
    score = p.score if p else 0
    analisis = (p.analisis if p else None) or {}
    evidencia = p.evidencia if p else ""
    # 'alta' es el único estado que persiste; el resto se deriva del avance real de los documentos
    estado = "alta" if e.estado == "alta" else ("completo" if e.progreso == 100 else "integracion")
    # documentos que la IA aprobó pero que nadie de RH ha confirmado todavía (bloquean el alta)
    sin_confirmar = e.sin_confirmar  # 2026-09-15: incluye digitales en revisión (cuentan para el %)
    return {
        "id": f"N-{500 + e.id}",
        "expedienteId": e.id,
        "postulacionId": p.codigo if p else None,
        "nombre": c.nombre if c else "",
        "puesto": e.puesto,
        "ubicacion": (c.ubicacion if c else "") or "N/D",
        "ingreso": f"Ingresa el {fecha_corta(e.fecha_ingreso)}" if e.fecha_ingreso else "Fecha por definir",
        "fechaIngreso": iso(e.fecha_ingreso),
        # --- condiciones finales de contratación (formulario de la etapa Contratación) ---
        "sueldo": e.sueldo,
        "tipoContratacion": e.tipo_contratacion,
        "ubicacionTrabajo": e.ubicacion,
        "jefeDirecto": e.jefe_directo,
        # --- preparación de ingreso (Onboarding, bloque 4) ---
        "contrato": e.contrato,
        "altaAdministrativa": e.alta_administrativa,
        "equipoAccesos": e.equipo_accesos,
        "progreso": e.progreso,
        "tono": (e.id or 0) % 4,
        "estado": estado,
        "documentos": [documento_dict(d) for d in e.documentos],
        "pendientes": e.pendientes,
        "noAprobados": e.no_aprobados,  # Onboarding v2: lo que falta APROBAR para el 100 %
        "porRevisar": e.por_revisar,
        "sinConfirmar": sin_confirmar,
        # Fase 3: recordatorios automáticos de documentos
        "documentosHasta": e.documentos_hasta.isoformat() if e.documentos_hasta else None,
        # 2026-09-17: recordatorios en 3 niveles
        "recordatoriosEnviados": e.recordatorios_enviados or 0,
        "nivelRecordatorio": e.nivel_recordatorio,
        "tonoRecordatorio": NIVELES_RECORDATORIO[e.nivel_recordatorio],
        "recordatoriosAgotados": e.recordatorios_agotados,
        "ultimoRecordatorioEn": e.ultimo_recordatorio_en.isoformat() if e.ultimo_recordatorio_en else None,
        "listoParaAlta": estado == "completo" and not sin_confirmar,
        # --- puentes hacia el módulo 1 (candidatoId = Postulación: es lo que /candidatos/{codigo} espera) ---
        "candidatoId": p.codigo if p else (c.codigo if c else ""),
        "candidatoCodigo": c.codigo if c else "",
        "telefono": c.telefono if c else "",
        "correo": c.correo if c else "",
        "vacanteId": vac.codigo if vac else "",
        "score": score,
        "entrevistaMatch": (ultima.evaluacion or {}).get("match_perfil") if ultima else None,
        "entrevistaRecomendacion": (ultima.evaluacion or {}).get("recomendacion") if ultima else None,
        # --- Bloque 2 (resumen de evaluación): lo que ya sabemos del candidato sin ir a buscarlo aparte ---
        "evaluacion": {
            "score": score,
            "requisitosCumplidos": analisis.get("requisitos_cumplidos", []),
            "brechas": analisis.get("brechas", []),
            "alertas": analisis.get("alertas", []),
            "evidencia": evidencia,
        }
        if c
        else None,
        # --- trazabilidad HITL ---
        "seleccionadoPor": e.seleccionado_por or "",
        "altaAutorizadaPor": e.alta_autorizada_por or "",
        "altaFecha": iso(e.alta_fecha),
        "creado": hace(e.creado_en),
    }


# ------------------------------------------------------------
# Colaboradores (alta al cierre del Onboarding)
# ------------------------------------------------------------


def _jefe_codigo(col) -> Optional[str]:
    if not col.jefe_id:
        return None
    from sqlalchemy.orm import object_session

    sesion = object_session(col)
    jefe = sesion.get(Colaborador, col.jefe_id) if sesion else None
    return jefe.codigo if jefe else None


def colaborador_dict(col: Colaborador) -> dict:
    return {
        "id": col.codigo,
        "nombre": col.nombre,
        "correo": col.correo,
        "telefono": col.telefono,
        "puesto": col.puesto,
        "area": col.area or "",  # 2026-09-22: permisos de Conocimiento y tableros de Desempeño/Clima
        "salario": col.salario,
        "empresa": col.empresa,
        "tipoContratacion": col.tipo_contratacion or "",  # 2026-09-19
        "condicionesIngreso": col.condiciones_ingreso or {},
        "ubicacion": col.ubicacion,
        "jefeDirecto": col.jefe_directo,
        "jefeId": _jefe_codigo(col),  # 2026-09-27: jefe como otro colaborador del roster
        "origenAlta": "contratacion" if (col.candidato_origen_id or col.expediente_id) else "manual",
        "estatus": "Activo" if col.activo else "Inactivo",
        "cvNombre": col.cv_nombre,
        "tieneCv": bool(col.cv_ruta),
        "fechaIngreso": iso(col.fecha_ingreso),
        "activo": col.activo,
        "dadoDeAltaPor": col.dado_de_alta_por,
        "candidatoOrigenId": col.candidato_origen.codigo if col.candidato_origen else None,
        "expedienteId": col.expediente_id,
        # Fase 5: Cliente para el que se contrató (filtro del tablero de Colaboradores)
        "clienteId": col.cliente_id,
        "clienteNombre": col.cliente.nombre if col.cliente else None,  # nombre interno (RH), no el comercial
        "creado": hace(col.creado_en),
        # baja / eliminación lógica (2026-09-15)
        "bajaEn": iso(col.baja_en),
        "bajaMotivo": col.baja_motivo or "",
        "bajaPor": col.baja_por or "",
        "eliminadoEn": iso(col.eliminado_en),
    }


def colaborador_detalle_dict(col: Colaborador) -> dict:
    """Perfil completo (panel de Colaboradores): datos + expediente con sus documentos + origen."""
    exp = col.expediente
    c = col.candidato_origen
    return {
        **colaborador_dict(col),
        "tipoContratacion": exp.tipo_contratacion if exp else "",
        "instruccionesIngreso": (exp.instrucciones_ingreso if exp else "") or "",
        "altaAutorizadaPor": (exp.alta_autorizada_por if exp else "") or col.dado_de_alta_por,
        "altaFecha": iso(exp.alta_fecha) if exp else None,
        "expediente": expediente_dict(exp) if exp else None,
        "candidatoOrigen": {
            "codigo": c.codigo, "nombre": c.nombre, "fuente": c.fuente, "correo": c.correo, "telefono": c.telefono,
            "eliminado": bool(c.eliminado_en),
        } if c else None,
        "vacante": (
            {"codigo": exp.postulacion.vacante.codigo, "titulo": exp.postulacion.vacante.titulo}
            if exp and exp.postulacion and exp.postulacion.vacante else None
        ),
    }


# ============================================================
# Desempeño y Clima (andamiaje 2026-09-22) — la persona SIEMPRE viene de `colaboradores`
# ============================================================


def ciclo_desempeno_dict(c, detalle: bool = False) -> dict:
    from .models import normalizar_estado_ciclo
    from .services import desempeno_calculo as calc

    av = calc.avance(c)
    salida = {
        "id": c.codigo,
        "nombre": c.nombre,
        "periodo": c.periodo or "",
        "descripcion": c.descripcion or "",
        "puestoObjetivo": c.puesto_objetivo or "",
        "equipo": c.equipo or c.puesto_objetivo or "",
        "criterios": calc.criterios_de(c),
        "pesosPersonalizados": calc.usa_pesos(c),
        "origenCriterios": c.origen_criterios or "",
        "objetivos": list(c.objetivos or []),  # legado (antes de v2)
        "kpis": list(c.kpis or []),            # legado (antes de v2)
        "escalaMaxima": 100,
        "generadoConIa": bool(c.generado_con_ia),
        "estado": normalizar_estado_ciclo(c.estado),
        "participantes": av["incluidas"],
        "completadas": av["completadas"],
        # avance = personas completadas ÷ personas incluidas (nunca por filas vacías)
        "avance": av["porcentaje"],
        "creadoPor": c.creado_por or "",
        "creado": hace(c.creado_en),
        "creadoEn": iso(c.creado_en),
        "iniciadoEn": iso(c.iniciado_en),
        "cerradoEn": iso(c.cerrado_en),
        "cerradoPor": c.cerrado_por or "",
        "duplicadoDe": c.duplicado_de or "",
    }
    if detalle:
        salida["evaluaciones"] = [evaluacion_desempeno_dict(e) for e in calc.incluidas(c)]
        salida["historialCambios"] = list(c.historial_cambios or [])
    return salida


def _brechas(e) -> list:
    from .routers.desempeno import normalizar_brechas  # import tardío

    return normalizar_brechas(list(e.brechas or []))


def evaluacion_desempeno_dict(e, detalle: bool = False) -> dict:
    from .models import normalizar_estado_persona
    from .services import desempeno_calculo as calc

    col = e.colaborador
    salida = {
        "id": e.codigo,
        "cicloId": e.ciclo.codigo if e.ciclo else "",
        "ciclo": e.ciclo.nombre if e.ciclo else "",
        "periodo": e.ciclo.periodo if e.ciclo else "",
        # identidad SIEMPRE tomada del roster maestro (nunca se recaptura en el módulo)
        "colaboradorId": col.codigo if col else None,
        "colaborador": col.nombre if col else "",
        "puesto": col.puesto if col else "",
        "area": (col.area or "") if col else "",
        "empresa": (col.empresa or "") if col else "",   # Desempeño toma empresa/área/puesto/jefe de la base maestra
        "jefe": (col.jefe_directo or "") if col else "",
        "evaluador": e.evaluador or "",
        "evaluadorUsuarioId": e.evaluador_usuario_id,
        "estado": normalizar_estado_persona(e.estado),
        "calificacion": e.calificacion,
        "escalaMaxima": 100,
        "brechas": _brechas(e),
        "creadoEn": iso(e.creado_en),
        "completadaEn": iso(e.completada_en),
        "completadaPor": e.completada_por or "",
    }
    if detalle:
        calculo = calc.calcular(e)
        salida["criterios"] = calc.criterios_efectivos(e)
        salida["resultados"] = list(e.resultados or [])
        salida["cumplimiento"] = calculo["detalle"]
        salida["faltantes"] = calculo["faltantes"]
        salida["comentarios"] = e.comentarios or ""
        salida["conclusion"] = e.conclusion or ""
        salida["resumen"] = e.resumen or ""
        salida["fortalezas"] = list(e.fortalezas or [])
        salida["propuestaIa"] = dict(e.propuesta_ia or {}) or None
        salida["notas"] = list(e.notas or [])
        ids = {c["id"] for c in salida["criterios"]}
        # historial: cambios de la evaluación general a sus criterios + ajustes individuales de la persona
        salida["historialCambios"] = sorted(
            [h for h in (e.ciclo.historial_cambios or []) if h.get("criterio_id") in ids] + list(e.historial_cambios or []),
            key=lambda h: h.get("fecha") or "",
        ) if e.ciclo else list(e.historial_cambios or [])
        salida["objetivos"] = list(e.ciclo.objetivos or []) if e.ciclo else []  # legado
        salida["kpis"] = list(e.ciclo.kpis or []) if e.ciclo else []            # legado
    return salida


def medicion_clima_dict(m, liga: str = "", detalle: bool = False) -> dict:
    salida = {
        "id": m.codigo,
        "titulo": m.titulo,
        "descripcion": m.descripcion or "",
        "anonima": bool(m.anonima),
        "permiteExternos": bool(m.permite_externos),
        "estado": m.estado,
        "estadoEtiqueta": {"borrador": "Borrador", "abierta": "Abierta", "cerrada": "Cerrada"}.get(m.estado, m.estado),
        "preguntas": len(m.preguntas or []),
        "dimensiones": list(m.dimensiones or []),
        # Clima v2: `respuestas` = reales internas (sin prueba ni externas); las demás van aparte
        "respuestas": sum(1 for r in (m.respuestas or []) if not r.es_prueba and not r.es_externa and r.origen != "externo"),
        "respuestasPrueba": sum(1 for r in (m.respuestas or []) if r.es_prueba),
        "respuestasExternas": sum(1 for r in (m.respuestas or []) if not r.es_prueba and (r.es_externa or r.origen == "externo")),
        "invitados": len(m.participaciones or []),
        "respondieron": sum(1 for p in (m.participaciones or []) if p.respondio),
        "liga": liga,
        "abiertaEn": iso(m.abierta_en),
        "cierraEn": iso(m.cierra_en),
        "cerradaEn": iso(m.cerrada_en),
        "creadoPor": m.creado_por or "",
        "creado": hace(m.creado_en),
    }
    if detalle:
        salida["cuestionario"] = list(m.preguntas or [])
    return salida


def plantilla_clima_dict(p, detalle: bool = False) -> dict:
    salida = {
        "id": p.id,
        "nombre": p.nombre,
        "descripcion": p.descripcion or "",
        "dimensiones": list(p.dimensiones or []),
        "preguntas": len(p.preguntas or []),
        "tipos": sorted({q.get("tipo") for q in (p.preguntas or [])}),
        "activa": bool(p.activa),
        "creadoPor": p.creado_por or "",
        "actualizada": iso(p.actualizada_en),
    }
    if detalle:
        salida["cuestionario"] = list(p.preguntas or [])
    return salida


def medicion_clima_publica_dict(m) -> dict:
    """Lo que ve quien abre la liga pública: nada de resultados ni de quién respondió."""
    return {
        "id": m.codigo,
        "titulo": m.titulo,
        "descripcion": m.descripcion or "",
        "anonima": bool(m.anonima),
        "permiteExternos": bool(m.permite_externos),
        "abierta": m.estado == "abierta",
        "preguntas": list(m.preguntas or []),
        "dimensiones": list(m.dimensiones or []),
        "aviso": (
            "Tus respuestas son ANÓNIMAS: no se guarda quién contestó."
            if m.anonima
            else "Esta medición es identificada: tus respuestas quedan ligadas a tu nombre."
        ),
    }


# ------------------------------------------------------------
# Onboarding v2 (2026-09-28)
# ------------------------------------------------------------


def plantilla_onboarding_dict(p, curso_titulo: str = "") -> dict:
    from .services import onboarding as onb

    cfg = onb.config_de_plantilla(p)
    return {
        "id": p.id,
        "nombre": p.nombre,
        "alcance": p.alcance,
        "empresa": p.empresa or "",
        "puesto": p.puesto or "",
        "documentos": cfg["documentos"],
        "recursos": cfg["recursos"],
        "responsables": cfg["responsables"],
        "plazos": cfg["plazos"],
        "cursoInduccionId": p.curso_induccion_id,
        "cursoInduccion": curso_titulo,
        "activa": bool(p.activa),
        "creadoPor": p.creado_por or "",
        "actualizada": iso(p.actualizada_en),
    }


def tarea_onboarding_dict(t) -> dict:
    from .services import onboarding as onb

    return {
        "id": t.id,
        "expedienteId": t.expediente_id,
        "clave": t.clave,
        "nombre": t.nombre,
        "tipo": t.tipo,
        "fija": bool(t.fija),
        "obligatoria": bool(t.obligatoria),
        "responsable": t.responsable or "",
        "diasRelativos": t.dias_relativos,
        "fechaLimite": iso(t.fecha_limite),
        "estado": t.estado,  # pendiente | realizada | cancelada
        "atrasada": onb.atrasada(t),
        "motivoCancelacion": t.motivo_cancelacion or "",
        "notas": t.notas or "",
        "realizadaPor": t.realizada_por or "",
        "realizadaEn": iso(t.realizada_en),
        "canceladaPor": t.cancelada_por or "",
        "canceladaEn": iso(t.cancelada_en),
        "cierreConAccion": onb.CIERRE_CON_ACCION.get(t.clave, ""),
    }


# ------------------------------------------------------------
# Evaluaciones y verificaciones (2026-09-28)
# ------------------------------------------------------------


def prueba_psicometrica_dict(pr) -> dict:
    from .models import MODOS_PRUEBA

    return {
        "id": pr.id,
        "clave": pr.clave,
        "nombre": pr.nombre,
        "descripcion": pr.descripcion or "",
        "puestos": list(pr.puestos or []),
        "modo": pr.modo,
        "modoTexto": MODOS_PRUEBA.get(pr.modo, pr.modo),
        "proveedor": pr.proveedor or "",
        "idProveedor": pr.id_proveedor or "",
        "url": pr.url or "",
        "activa": bool(pr.activa),
        "actualizada": iso(pr.actualizada_en),
    }


def _url_psico(clave: str):
    from .services import psicometricas as psi

    return psi.url_candidato(clave) if clave else None


def _ultima_entrevista_humana_con_resultado(p):
    from sqlalchemy.orm import object_session

    from .models import Evaluacion

    db = object_session(p)
    if db is None:
        return None
    return (db.query(Evaluacion).filter(Evaluacion.postulacion_id == p.id, Evaluacion.tipo == "entrevista_humana",
                                        Evaluacion.estado == "con_resultado").order_by(Evaluacion.id.desc()).first())


def evaluacion_dict(ev, usuario=None, *, publico: bool = False) -> dict:
    """Evaluación unificada (tarjeta + detalle). El detalle médico COMPLETO (comentarios y adjuntos) solo viaja a quien
    tiene permiso (`Usuario.puede_ver_informe_medico`) o al evaluador por su liga (`publico=True`); el resto ve estado
    y conclusión. `acciones` = lo que la interfaz ofrece en este estado (botón principal, secundario y menú «⋯»)."""
    from .models import CONSENTIMIENTOS, ESTADOS_EVALUACION_U, FORMAS_EVALUACION, TIPOS_EVALUACION_U, conclusiones_de
    from .services import evaluaciones as sev

    restringido = ev.tipo == "medica" and not publico and not (usuario is not None and usuario.puede_ver_informe_medico())
    opciones = conclusiones_de(ev.tipo)
    if ev.forma == "asignada":
        responsable = ev.evaluador_nombre or "Evaluador"
    elif ev.forma == "integrada":
        responsable = ev.proveedor or "Proveedor integrado"
    elif ev.forma == "liga_otro_sistema":
        responsable = "Liga de otro sistema"
    else:
        responsable = "Registro directo"
    salida = {
        "id": ev.codigo,
        "codigo": ev.codigo,
        "tipo": ev.tipo,
        "tipoTexto": TIPOS_EVALUACION_U.get(ev.tipo, ev.tipo),
        "nombre": ev.nombre_visible,
        "nombrePropio": ev.nombre or "",
        "forma": ev.forma,
        "formaTexto": FORMAS_EVALUACION.get(ev.forma, ev.forma),
        "responsable": responsable,
        "estado": ev.estado,
        "estadoTexto": ESTADOS_EVALUACION_U.get(ev.estado, ev.estado),
        "motivoEstado": ev.motivo_estado or "",
        "consentimiento": ev.consentimiento or "no_requerido",
        "consentimientoTexto": CONSENTIMIENTOS.get(ev.consentimiento or "no_requerido", ""),
        "consentimientoEn": iso(ev.consentimiento_en),
        "ligaConsentimiento": sev.liga_consentimiento(ev) if ev.consentimiento == "pendiente" and not publico else None,
        "evaluador": {
            "tipo": ev.evaluador_tipo, "usuarioId": ev.evaluador_usuario_id, "contactoId": ev.evaluador_contacto_id,
            "nombre": ev.evaluador_nombre or "", "correo": "" if publico else (ev.evaluador_correo or ""),
            "whatsapp": "" if publico else (ev.evaluador_whatsapp or ""),
        } if ev.forma == "asignada" else None,
        "instrucciones": ev.instrucciones or "",
        "ligaExternaCandidato": ev.liga_externa_candidato or "",
        "pruebaId": ev.prueba_id,
        "proveedor": ev.proveedor or "",
        "idProveedor": ev.id_proveedor or "",
        "claveProveedor": ev.clave_proveedor or None,
        "urlCandidatoProveedor": _url_psico(ev.clave_proveedor),
        "usaPsicometricas": sev.usa_psicometricas(ev),
        "pasoIntegrada": ev.paso_integrada or None,
        "siguientePaso": sev.siguiente_paso(ev) if ev.forma == "integrada" and not ev.clave_proveedor and ev.estado in ("pendiente", "realizada_sin_resultado") else None,
        "cita": {
            "fechaHora": iso(ev.cita_fecha_hora), "zona": ev.cita_zona_horaria or "", "modalidad": ev.cita_modalidad or "",
            "direccion": ev.cita_direccion or "", "ligaVideollamada": ev.cita_liga_videollamada or "",
            "telefono": ev.cita_telefono or "", "porTeams": bool(ev.teams_evento_id),
        } if ev.cita_fecha_hora else None,
        "conclusion": ev.conclusion or None,
        "conclusionTexto": opciones.get(ev.conclusion, "") if ev.conclusion else "",
        "conclusionesPosibles": [{"valor": k, "texto": t} for k, t in opciones.items()],
        "conclusionObligatoria": ev.tipo == "entrevista_humana",
        "sinConclusion": ev.estado == "con_resultado" and not ev.conclusion,
        "realizadaPor": ev.realizada_por or "",
        "realizadaEn": iso(ev.realizada_en),
        "registradaPor": ev.registrada_por or "",
        "registradaVia": ev.registrada_via or "",
        "registradaEn": iso(ev.registrada_en),
        "resultadoVersion": int(ev.resultado_version or 0),
        "nuevoResultado": ev.estado == "con_resultado" and ev.registrada_via in ("liga_evaluador", "proveedor") and ev.resultado_visto_en is None,
        "restringido": restringido,
        "tieneAdjuntos": bool(ev.adjuntos),
        "creadoPor": ev.creado_por or "",
        "creadoEn": iso(ev.creado_en),
        "actualizadoEn": iso(ev.actualizado_en),
        "acciones": acciones_evaluacion(ev),
    }
    if not publico:
        salida["ligaEvaluador"] = sev.liga_evaluador(ev) if ev.forma == "asignada" else None
    if not restringido:
        salida["comentarios"] = ev.comentarios or ""
        salida["adjuntos"] = [
            {"id": a.get("id"), "nombre": a.get("nombre") or "archivo", "mime": a.get("mime") or "", "subidoPor": a.get("subido_por") or "",
             "subidoVia": a.get("subido_via") or "", "subidoEn": a.get("subido_en")}
            for a in (ev.adjuntos or [])
        ]
    else:
        salida["comentarios"] = ""
        salida["adjuntos"] = []
    return salida


def sev_siguiente(ev):
    from .services.evaluaciones import siguiente_paso

    return siguiente_paso(ev)


def acciones_evaluacion(ev) -> dict:
    """Botón principal / secundario / menú «⋯» por estado (especificación, sección 6)."""
    con_cita = bool(ev.cita_fecha_hora)
    bloqueo = ev.consentimiento in ("pendiente", "rechazado")
    principal, secundaria, menu = None, None, []
    if ev.estado == "pendiente":
        principal = None if bloqueo else "registrar_resultado"
        secundaria = None if bloqueo else "marcar_realizada"
        if ev.consentimiento == "pendiente":
            menu.append("enviar_consentimiento")
        if not bloqueo:
            menu += ["recordatorio", "reenviar_liga"] if ev.forma in ("asignada", "liga_otro_sistema") else []
            menu.append("modificar")
            if con_cita:
                menu.append("reprogramar")
            menu.append("no_realizada")
        menu.append("cancelar")
    elif ev.estado == "realizada_sin_resultado":
        principal = "registrar_resultado"
        menu += ["reenviar_liga"] if ev.forma == "asignada" else []
        menu += ["modificar", "cancelar"]
    elif ev.estado == "con_resultado":
        principal, secundaria = "ver_resultado", "complementar"
        if ev.tipo == "entrevista_humana" and ev.conclusion == "requiere_otra_entrevista":
            menu.append("programar_otra")
    elif ev.estado == "no_realizada":
        principal = "reprogramar"
        menu.append("cancelar")
    if ev.forma == "integrada" and ev.estado in ("pendiente", "realizada_sin_resultado") and not bloqueo:
        if ev.clave_proveedor:
            menu.insert(0, "sincronizar")
        elif ev.estado == "pendiente" and (ev.paso_integrada or "asignada") == "asignada":
            menu.insert(0, "enviar_proveedor")
        elif sev_siguiente(ev):
            menu.insert(0, "avanzar_paso")
    return {"principal": principal, "secundaria": secundaria, "menu": menu}


def evento_evaluacion_dict(e) -> dict:
    from .models import ESTADOS_EVALUACION_U

    etiquetas = {
        "creada": "Creación", "modificada": "Modificación", "reprogramada": "Reprogramación", "envio": "Envío",
        "recordatorio": "Recordatorio", "cambio_estado": "Cambio de estado", "consentimiento": "Consentimiento",
        "resultado_registrado": "Resultado registrado", "resultado_corregido": "Resultado corregido",
        "resultado_complementado": "Resultado complementado", "adjunto_agregado": "Adjunto agregado", "migrada": "Migración al modelo unificado",
    }
    canales = {"sistema": "Sistema", "liga_evaluador": "Liga del evaluador", "liga_candidato": "Liga del candidato", "proveedor": "Proveedor", "migracion": "Migración"}
    return {
        "id": e.id, "accion": e.accion, "accionTexto": etiquetas.get(e.accion, e.accion), "actor": e.actor or "",
        "canal": e.canal, "canalTexto": canales.get(e.canal, e.canal), "fecha": iso(e.fecha),
        "estadoAnterior": e.estado_anterior or "", "estadoNuevo": e.estado_nuevo or "",
        "estadoAnteriorTexto": ESTADOS_EVALUACION_U.get(e.estado_anterior, "") if e.estado_anterior else "",
        "estadoNuevoTexto": ESTADOS_EVALUACION_U.get(e.estado_nuevo, "") if e.estado_nuevo else "",
        "anteriores": e.anteriores or {}, "detalle": e.detalle or {},
    }