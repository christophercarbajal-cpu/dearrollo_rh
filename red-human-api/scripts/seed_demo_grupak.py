"""Entorno de demostración «Demo Grupak» (prospecto) — 2026-10-07 (v2).

Solo DATOS: no toca código, endpoints ni pantallas. Siembra con los modelos y, donde existen, con las MISMAS funciones
de los endpoints (crear ciclo, evaluar, crear medición, responder…), así cada registro queda idéntico a uno capturado
en pantalla:
  1. Cuenta «Demo Grupak» (slug `demo-grupak`) con las tres rutas base, igual que `POST /cuentas`.
  2. Catálogo psicométrico: Cleaver, Zavic, Terman, Herrmann, 16PF y MOSS + Baterías Ayudante, Cumplimiento y Gerente.
  3. «Ruta Demo Grupak»: 12 actividades en las 5 etapas (validada con `proceso.normalizar_pasos`).
  4. Tres vacantes con la ruta; cada una con su batería solo en SU copia (la ruta no se altera).
  5. Tres candidatos en Prefiltro, Filtro Red Human y Filtro humano, con registros reales (el estado de cada actividad
     se DERIVA solo). «Persona bajo la lluvia» lleva consigna e instrucciones de aplicación; «Referencias y revisión
     documental» lleva el formulario de referencias laborales (liga del evaluador `/evaluacion/{token}`); la
     Entrevista profunda Red Human y las entrevistas humanas llevan su guion.
  6. Colaboradores (roster = base maestra de Desempeño y Clima).
  7. Clima: medición «Liderazgo y comunicación» (escala 1-5 + opción múltiple + abierta), aplicada al roster con
     respuestas simuladas y cerrada, para que el tablero muestre participación, índice y resultados por pregunta.
  8. Desempeño: evaluación SEMESTRAL (criterios medibles con meta + descriptivos con niveles 1-5) y una evaluación
     360° del Gerente de Ventas.
  9. Base de Conocimiento: 3 documentos PUBLICADOS sobre el proceso.

v2 — corrección del crash de /dashboard/entrevistas: la Entrevista Red Human de la v1 tenía `recomendacion` en texto
libre (la pantalla solo admite avanzar | revision | no_avanzar), le faltaban `calif_experiencia`/`calif_comunicacion`
(se formatean con .toFixed) y el transcript usaba roles agente/candidato (el sistema usa assistant/user). Ahora la
evaluación y el guion se arman con los esquemas reales (`ia.EvaluacionEntrevista`, `ia.GuionEntrevista`), que validan
cada campo, y la fase de reparación CORRIGE las entrevistas que la v1 ya sembró (no hay que borrar nada).

360°: la plataforma no tiene un tipo «360» propio; con datos se representa como TRES evaluaciones de desempeño de la
misma persona y las mismas competencias, una por perspectiva (Autoevaluación · Jefe directo · Compañeros). Cada una se
abre, compara y cierra en Desempeño como cualquier otra.

Seguridad:
  * Sin `--aplicar` es un SIMULACRO: corre todo en UNA transacción y al final la deshace. Con `--aplicar` también es una
    sola transacción: o queda todo o no queda nada (los `commit` internos de los endpoints se vuelven `flush`).
  * Idempotente: Cuenta por slug/nombre; pruebas por clave; ruta por nombre; vacantes y mediciones por título;
    candidatos y colaboradores por correo; ciclos de desempeño por nombre; documentos por título. Lo existente se
    reutiliza (y se REPARA si viene de la v1), nunca se duplica ni se sobrescribe lo que RH haya editado.
  * Cero comunicaciones: no llama a WhatsApp, Telegram, correo, Teams ni a Psicométricas.mx. La medición de clima se
    abre con el helper interno de invitación (el endpoint `abrir` mandaría la liga a cada invitado). Ninguna
    evaluación lleva cita (el job de recordatorios solo escribe a las que tienen cita). Personas con correo
    `@demo.invalid` (dominio reservado) y sin teléfono. Psicometrías en modo integrado SIMULADO (sin clave).
  * Vacantes «En revisión» salvo `--publicar`. No crea usuarios salvo `--crear-usuario`.

Uso (desde red-human-api/, con el .env del ambiente; la API debe haber arrancado una vez con esta versión):
    .venv/Scripts/python.exe scripts/seed_demo_grupak.py                      # simulacro (no escribe)
    .venv/Scripts/python.exe scripts/seed_demo_grupak.py --aplicar            # aplica
Opciones:
    --publicar                 deja las vacantes Publicadas
    --admin CORREO             (repetible) usuario EXISTENTE que podrá ver la Cuenta (default: todos los Administradores)
    --crear-usuario CORREO     crea (si no existe) un Administrador vinculado SOLO a «Demo Grupak»
    --ids-proveedor "N=ID,…"   identificadores reales de Psicométricas.mx (los de abajo son de EJEMPLO)
"""

import argparse
import random
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

try:  # la consola de Windows (cp1252) no imprime acentos/símbolos
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import func, inspect as sa_inspect  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    TIPOS_ENTREVISTA_HUMANA, Candidato, CicloDesempeno, Colaborador, Cuenta, DocumentoConocimiento, Entrevista,
    Evaluacion, EvaluacionDesempeno, MedicionClima, PlantillaProceso, Postulacion, PruebaPsicometrica, Usuario,
    UsuarioCuenta, Vacante, registrar, texto_sueldo, texto_ubicacion,
)
from app.routers import clima as rclima  # noqa: E402
from app.routers import colaboradores as rcol  # noqa: E402
from app.routers import desempeno as rdes  # noqa: E402
from app.routers.auth import crear_usuario_basico  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.routers.vacantes import _slug_unico  # noqa: E402
from app.seed import slug_cuenta_unico  # noqa: E402
from app.serial import nombre_empresa  # noqa: E402
from app.services import clima_resultados, ia, rag  # noqa: E402
from app.services import desempeno_calculo as calc  # noqa: E402
from app.services import evaluaciones as sev  # noqa: E402
from app.services import proceso as sproc  # noqa: E402

ACTOR = "script:seed_demo_grupak"
NOMBRE_CUENTA = "Demo Grupak"
SLUG_CUENTA = "demo-grupak"
DOMINIO = "demo.invalid"
PROVEEDOR = "Psicométricas.mx"
NOMBRE_RUTA = "Ruta Demo Grupak"
PASO_BATERIA = "bateria-psicometrica"
AZAR = random.Random(2026)  # respuestas simuladas reproducibles

# ============================================================ catálogo psicométrico
# Identificadores de EJEMPLO en Psicométricas.mx (se reemplazan con --ids-proveedor).
IDS_PROVEEDOR = {"Cleaver": "101", "Zavic": "102", "Terman": "103", "Herrmann": "104", "16PF": "105", "MOSS": "106"}
PRUEBAS = {
    "Cleaver": "Perfil conductual DISC: estilo de trabajo bajo condiciones normales, de presión y motivación.",
    "Zavic": "Valores (moral, legalidad, indiferencia, corrupción) e intereses (económico, político, social, religioso).",
    "Terman": "Capacidad intelectual (Terman-Merrill): razonamiento verbal, numérico y abstracto.",
    "Herrmann": "Dominancia cerebral: preferencias de pensamiento analítico, organizado, interpersonal e innovador.",
    "16PF": "Cuestionario de 16 factores de personalidad (Cattell).",
    "MOSS": "Adaptabilidad social para puestos de supervisión: relaciones interpersonales y manejo de personal.",
}
BATERIAS = {
    "Batería Ayudante": ["Cleaver", "Zavic"],
    "Batería Cumplimiento": ["Terman", "Cleaver", "Herrmann", "16PF"],
    "Batería Gerente": ["Terman", "MOSS", "Cleaver", "Herrmann", "16PF"],
}


def clave_prueba(nombre: str) -> str:
    return "GRP-" + "".join(ch for ch in nombre.upper() if ch.isalnum())


# ============================================================ ruta (12 actividades en las 5 etapas)
PASOS_RUTA = [
    {"id": "prefiltro", "tipo": "prefiltro_web", "nombre": "Prefiltro", "etapa": "Prefiltro"},
    {"id": "persona-lluvia", "tipo": "otra", "nombre": "Persona bajo la lluvia", "etapa": "Entrevista IA",
     "responsable": {"tipo": "rh"}},
    {"id": PASO_BATERIA, "tipo": "psicometrica", "nombre": "Batería psicométrica", "etapa": "Entrevista IA",
     "responsable": {"tipo": "externo", "nombre": PROVEEDOR}},
    {"id": "entrevista_red_human", "tipo": "entrevista_agente", "nombre": "Entrevista profunda Red Human",
     "etapa": "Entrevista IA", "tipo_entrevista": "profesional_personal"},
    {"id": "referencias", "tipo": "referencias", "nombre": "Referencias y revisión documental", "etapa": "Entrevista Humana"},
    {"id": "entrevista-sindicato", "tipo": "entrevista_humana", "nombre": "Entrevista con sindicato",
     "etapa": "Entrevista Humana", "tipo_entrevista": "general", "responsable": {"tipo": "externo", "nombre": "Sindicato"}},
    {"id": "medica", "tipo": "medica", "nombre": "Examen médico", "etapa": "Entrevista Humana",
     "responsable": {"tipo": "externo", "nombre": "Médico de la empresa"}},
    {"id": "entrevista-lider", "tipo": "entrevista_humana", "nombre": "Entrevista con líder", "etapa": "Entrevista Humana",
     "tipo_entrevista": "jefe_directo", "depende_de": ["entrevista-sindicato"],
     "responsable": {"tipo": "externo", "nombre": "Líder del área"}},
    {"id": "propuesta", "tipo": "condiciones", "nombre": "Propuesta y aceptación", "etapa": "Contratación"},
    {"id": "documentos-ingreso", "tipo": "documentos", "nombre": "Documentos de ingreso", "etapa": "Contratación"},
    {"id": "contratacion", "tipo": "carta_contrato", "nombre": "Contratación", "etapa": "Contratación",
     "depende_de": ["propuesta", "documentos-ingreso"]},
    {"id": "onboarding", "tipo": "onboarding", "nombre": "Onboarding", "etapa": "Onboarding"},
]
ETAPAS_RUTA = {e: {"avance_automatico": True} for e in ("Prefiltro", "Entrevista IA", "Entrevista Humana")}

# ============================================================ contenido de las evaluaciones (puntos 3, 4, 7 y 8)
INSTRUCCIONES_LLUVIA = (
    "Prueba proyectiva «Persona bajo la lluvia» (aplicación presencial, 10-15 minutos).\n"
    "Material: hoja blanca tamaño carta en vertical, lápiz del número 2 y goma. Sin regla ni colores.\n"
    "Consigna al candidato: «Dibuja una persona bajo la lluvia. Tómate el tiempo que necesites.»\n"
    "Al terminar pide: nombre, edad y qué está pensando o sintiendo la persona dibujada.\n"
    "Observa: tamaño y ubicación de la figura, presencia y tipo de protección (paraguas, techo), intensidad de la "
    "lluvia, trazo y borraduras.\n"
    "Interpretación: la realiza un psicólogo con cédula. Registra la conclusión (Favorable / Con observaciones / "
    "Desfavorable) y adjunta la foto o el escaneo del dibujo. No se pregunta ni se registra ningún dato sensible."
)
INSTRUCCIONES_REFERENCIAS = (
    "Formulario de referencias laborales y revisión documental.\n"
    "1) Contacta al menos DOS referencias laborales de los últimos 5 años (jefe directo de preferencia). Identifícate "
    "como RH de Demo Grupak y pide 5 minutos.\n"
    "2) Haz las preguntas del guion de abajo; anota respuestas textuales.\n"
    "3) Revisión documental: compara puestos y fechas del CV contra lo que confirmen las referencias, constancias o el "
    "historial del IMSS que entregue el candidato.\n"
    "4) Registra el resultado con «Registrar resultado / Adjuntar reporte»: Favorable, Con observaciones o "
    "Desfavorable, con el nombre y la empresa de cada referencia.\n"
    "Nunca preguntes por salud, embarazo, religión, estado civil ni afiliación sindical."
)
PREGUNTAS_REFERENCIAS = [
    "¿Cuál fue su relación laboral con la persona y durante qué periodo?",
    "¿Qué puesto ocupaba y cuáles eran sus responsabilidades principales?",
    "¿Cuáles eran sus fortalezas más visibles en el trabajo?",
    "¿En qué aspecto necesitaba más acompañamiento o desarrollo?",
    "¿Cómo era su puntualidad, asistencia y apego a las reglas?",
    "¿Cuál fue el motivo de su salida?",
    "¿La volvería a contratar? ¿Por qué?",
]
INSTRUCCIONES_SINDICATO = ("Entrevista con la representación sindical: presentación del contrato colectivo, "
                           "reglamento interior y cuotas. El sindicato registra si avanza.")
INSTRUCCIONES_LIDER = ("Entrevista con el líder del área. Usa el guion como referencia y registra la conclusión "
                       "(Avanzar / No avanzar / Requiere otra entrevista) desde tu liga.")
GUIONES_HUMANOS = {
    "general": ia.GuionEntrevista(
        enfoque="Validar la adaptación a las condiciones laborales y al contrato colectivo.",
        temas=["Contrato colectivo", "Reglamento interior", "Turnos y prestaciones", "Expectativas"],
        preguntas=["¿Qué sabes del contrato colectivo de trabajo de la empresa?",
                   "¿Tienes dudas sobre turnos, descansos o prestaciones?",
                   "¿Cómo te gustaría que el sindicato te apoye en tu desarrollo?",
                   "¿Qué esperas de tu primer año con nosotros?"]),
    "jefe_directo": ia.GuionEntrevista(
        enfoque="Confirmar el ajuste con el equipo y el estilo de liderazgo del área.",
        temas=["Resultados", "Trabajo en equipo", "Retroalimentación", "Metas a 90 días"],
        preguntas=["Cuéntame de una meta difícil que hayas logrado y cómo lo hiciste.",
                   "¿Cómo manejas un desacuerdo con un compañero?",
                   "¿Qué tipo de retroalimentación te ayuda más?",
                   "¿Qué te gustaría haber logrado en tus primeros 90 días?",
                   "¿Qué necesitas de tu líder para dar tu mejor esfuerzo?"]),
}


def guion_humano(tipo: str) -> dict:
    return {**GUIONES_HUMANOS[tipo].model_dump(), "tipo": tipo, "tipoTexto": TIPOS_ENTREVISTA_HUMANA[tipo], "ia": False}


def guion_referencias() -> dict:
    return {**ia.GuionEntrevista(enfoque="Confirmar trayectoria, desempeño y motivo de salida con referencias laborales.",
                                 temas=["Relación laboral", "Responsabilidades", "Fortalezas", "Desarrollo", "Salida"],
                                 preguntas=PREGUNTAS_REFERENCIAS).model_dump(),
            "tipo": "referencias", "tipoTexto": "Referencias laborales", "ia": False}


# ============================================================ vacantes
VACANTES = [
    {
        "titulo": "Ayudante general", "bateria": "Batería Ayudante", "area": "Operaciones", "seniority": "Sin experiencia",
        "estado": "Nuevo León", "municipio": "Monterrey", "modalidad": "Presencial", "sueldo": (9500, 11000, "mensual"),
        "descripcion": "Apoyo en carga, descarga, acomodo de materiales y limpieza del área de trabajo en planta.",
        "indispensables": ["Secundaria terminada", "Disponibilidad para rolar turnos"],
        "deseables": ["Experiencia en almacén o producción"],
        "beneficios": ["Prestaciones de ley", "Comedor", "Transporte de personal"], "enfoque": "profesional",
    },
    {
        "titulo": "Especialista en Cumplimiento Normativo", "bateria": "Batería Cumplimiento", "area": "Legal y Cumplimiento",
        "seniority": "Senior", "estado": "Ciudad de México", "municipio": "Miguel Hidalgo", "modalidad": "Híbrido",
        "sueldo": (38000, 45000, "mensual"),
        "descripcion": "Asegurar el cumplimiento regulatorio (laboral, fiscal y de protección de datos), coordinar "
                       "auditorías internas y dar seguimiento a planes de acción.",
        "indispensables": ["Licenciatura en Derecho, Contaduría o afín", "3 años en cumplimiento o auditoría"],
        "deseables": ["Conocimiento de la LFPDPPP", "Certificación en compliance"],
        "beneficios": ["Prestaciones superiores a las de ley", "Seguro de gastos médicos mayores", "Esquema híbrido"],
        "enfoque": "profesional_personal",
    },
    {
        "titulo": "Gerente Trainee de Ventas", "bateria": "Batería Gerente", "area": "Ventas", "seniority": "Junior",
        "estado": "Jalisco", "municipio": "Guadalajara", "modalidad": "Presencial", "sueldo": (25000, 30000, "mensual"),
        "descripcion": "Programa de formación para liderar un equipo comercial: prospección, seguimiento de cartera, "
                       "metas de venta y desarrollo de personas.",
        "indispensables": ["Licenciatura terminada", "Experiencia en ventas o atención a clientes"],
        "deseables": ["Haber coordinado a un equipo", "Licencia de manejo"],
        "beneficios": ["Prestaciones de ley", "Comisiones", "Plan de carrera"], "enfoque": "profesional_personal",
    },
]

# ============================================================ candidatos ficticios (uno por vacante)
CANDIDATOS = [
    {"vacante": "Ayudante general", "nombre": "Luis Alberto Ramírez Soto", "correo": f"luis.ramirez@{DOMINIO}",
     "ubicacion": "Monterrey, Nuevo León", "experiencia": "1 año como auxiliar de almacén", "escenario": "prefiltro"},
    {"vacante": "Especialista en Cumplimiento Normativo", "nombre": "Mariana Castillo Herrera",
     "correo": f"mariana.castillo@{DOMINIO}", "ubicacion": "Ciudad de México",
     "experiencia": "5 años en auditoría interna y cumplimiento", "escenario": "filtro_red_human"},
    {"vacante": "Gerente Trainee de Ventas", "nombre": "Jorge Iván Morales Treviño", "correo": f"jorge.morales@{DOMINIO}",
     "ubicacion": "Guadalajara, Jalisco", "experiencia": "3 años en ventas B2B; coordinó a 4 ejecutivos",
     "escenario": "filtro_humano"},
]

# ============================================================ roster (base maestra de Desempeño y Clima)
# (clave, nombre, puesto, área, sede, jefe por clave)
COLABORADORES = [
    ("dir", "Patricia Garza Leal", "Directora Comercial", "Ventas", "Guadalajara", ""),
    ("ger", "Andrés Villarreal Ruiz", "Gerente de Ventas", "Ventas", "Guadalajara", "dir"),
    ("eje1", "Sofía Treviño Cantú", "Ejecutiva de Ventas", "Ventas", "Guadalajara", "ger"),
    ("eje2", "Diego Hernández Salas", "Ejecutivo de Ventas", "Ventas", "Guadalajara", "ger"),
    ("eje3", "Valeria Ortiz Medina", "Ejecutiva de Ventas", "Ventas", "Guadalajara", "ger"),
    ("cum", "Ricardo Benítez Lara", "Coordinador de Cumplimiento", "Legal y Cumplimiento", "Ciudad de México", "dir"),
    ("ana", "Fernanda Ruiz Escobedo", "Analista de Cumplimiento", "Legal y Cumplimiento", "Ciudad de México", "cum"),
    ("sup", "Héctor Martínez Olvera", "Supervisor de Operaciones", "Operaciones", "Monterrey", "dir"),
    ("ayu1", "Juan Pablo Reyes Cruz", "Ayudante general", "Operaciones", "Monterrey", "sup"),
    ("ayu2", "Karla Domínguez Pérez", "Ayudante general", "Operaciones", "Monterrey", "sup"),
]


def correo_col(clave: str) -> str:
    nombre = next(c[1] for c in COLABORADORES if c[0] == clave).split()
    plano = f"{nombre[0]}.{nombre[1]}".lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        plano = plano.replace(a, b)
    return f"{plano}@{DOMINIO}"


# ============================================================ clima (punto 11)
TITULO_CLIMA = "Clima laboral 2026 · Liderazgo y comunicación"
PREGUNTAS_CLIMA = [
    {"id": "lid1", "dimension": "Liderazgo", "tipo": "escala", "texto": "Mi líder me da objetivos claros."},
    {"id": "lid2", "dimension": "Liderazgo", "tipo": "escala", "texto": "Mi líder reconoce mi trabajo cuando hago las cosas bien."},
    {"id": "lid3", "dimension": "Liderazgo", "tipo": "escala", "texto": "Confío en las decisiones de mi líder."},
    {"id": "lid4", "dimension": "Liderazgo", "tipo": "opcion", "texto": "¿Con qué frecuencia recibes retroalimentación de tu líder?",
     "opciones": ["Cada semana", "Cada mes", "Rara vez", "Nunca"]},
    {"id": "com1", "dimension": "Comunicación", "tipo": "escala", "texto": "Me entero a tiempo de los cambios que afectan mi trabajo."},
    {"id": "com2", "dimension": "Comunicación", "tipo": "escala", "texto": "Puedo expresar mis ideas sin temor."},
    {"id": "com3", "dimension": "Comunicación", "tipo": "escala", "texto": "La comunicación entre áreas es efectiva."},
    {"id": "com4", "dimension": "Comunicación", "tipo": "opcion", "texto": "¿Por qué medio te enteras principalmente de los cambios?",
     "opciones": ["Mi líder directo", "Correo o comunicados", "Juntas generales", "Por compañeros"]},
    {"id": "abi1", "dimension": "Comentarios", "tipo": "abierta", "texto": "¿Qué mejorarías en la comunicación de tu área?"},
]
COMENTARIOS_CLIMA = ["Más juntas cortas de seguimiento.", "Que los cambios se avisen antes por escrito.",
                     "Me gustaría más retroalimentación individual.", "Mejor coordinación con Operaciones."]

# ============================================================ desempeño (punto 12)
NOMBRE_SEMESTRAL = "Evaluación semestral 2026-S1"
CRITERIOS_SEMESTRAL = [
    {"id": "objetivos", "tipo": "medible", "nombre": "Cumplimiento de objetivos del puesto", "unidad": "%", "meta": 100,
     "sentido": "mayor_es_mejor", "descripcion": "Porcentaje de las metas del semestre alcanzadas."},
    {"id": "asistencia", "tipo": "medible", "nombre": "Asistencia y puntualidad", "unidad": "%", "meta": 98,
     "sentido": "mayor_es_mejor", "descripcion": "Días laborados a tiempo sobre días programados."},
    {"id": "incidencias", "tipo": "medible", "nombre": "Incidencias de calidad o seguridad", "unidad": "incidencias",
     "meta": 2, "sentido": "menor_es_mejor", "descripcion": "Incidencias registradas en el semestre (menos es mejor)."},
    {"id": "equipo", "tipo": "descriptivo", "nombre": "Trabajo en equipo",
     "esperado": "Colabora con su equipo y otras áreas para lograr los objetivos comunes.",
     "escala": [{"valor": 1, "significado": "Trabaja aislado; genera fricción"},
                {"valor": 2, "significado": "Colabora solo cuando se le pide"},
                {"valor": 3, "significado": "Colabora de forma constante"},
                {"valor": 4, "significado": "Impulsa la colaboración del equipo"},
                {"valor": 5, "significado": "Referente de colaboración entre áreas"}]},
    {"id": "comunicacion", "tipo": "descriptivo", "nombre": "Comunicación",
     "esperado": "Comunica con claridad, escucha y mantiene informados a los involucrados."},
]
# resultados por colaborador: (objetivos, asistencia, incidencias, equipo, comunicación, completar)
RESULTADOS_SEMESTRAL = {
    "ger": (104, 99, 0, 5, 4, True), "eje1": (112, 97, 1, 4, 4, True), "eje2": (86, 95, 2, 3, 3, True),
    "eje3": (94, 99, 0, 4, 5, True), "cum": (100, 98, 0, 4, 4, True), "ana": (91, 96, 1, 3, 4, True),
    "sup": (97, 99, 3, 4, 3, True), "ayu1": (88, 93, 2, 3, 3, False), "ayu2": (None, None, None, None, None, False),
}
NOMBRE_360 = "Evaluación 360° 2026 · Andrés Villarreal (Gerente de Ventas)"
CRITERIOS_360 = [
    {"id": "liderazgo", "tipo": "descriptivo", "nombre": "Liderazgo", "esperado": "Da dirección, inspira y se hace responsable de los resultados del equipo."},
    {"id": "comunicacion", "tipo": "descriptivo", "nombre": "Comunicación", "esperado": "Comunica con claridad, escucha activamente y da retroalimentación oportuna."},
    {"id": "decisiones", "tipo": "descriptivo", "nombre": "Toma de decisiones", "esperado": "Decide con información, a tiempo y asume las consecuencias."},
    {"id": "desarrollo", "tipo": "descriptivo", "nombre": "Desarrollo de su equipo", "esperado": "Identifica el talento, delega y acompaña el crecimiento de las personas."},
    {"id": "integridad", "tipo": "descriptivo", "nombre": "Integridad", "esperado": "Actúa con apego a valores, normas y compromisos."},
]
PERSPECTIVAS_360 = [
    ("Autoevaluación", "Andrés Villarreal Ruiz (autoevaluación)", {"liderazgo": 4, "comunicacion": 5, "decisiones": 4, "desarrollo": 4, "integridad": 5},
     "Me percibo sólido en comunicación e integridad; quiero delegar más."),
    ("Jefe directo", "Patricia Garza Leal (jefa directa)", {"liderazgo": 4, "comunicacion": 4, "decisiones": 4, "desarrollo": 3, "integridad": 5},
     "Buen líder comercial con resultados; necesita formar a su segundo nivel."),
    ("Compañeros", "Compañeros (consolidado de 3 pares)", {"liderazgo": 4, "comunicacion": 3, "decisiones": 4, "desarrollo": 3, "integridad": 5},
     "Pares: reconocen su integridad; piden más comunicación con otras áreas."),
]

# ============================================================ base de conocimiento (punto 13)
DOCUMENTOS = [
    ("Manual del proceso de selección Grupak", "proceso",
     "Proceso de selección de Demo Grupak.\n\n"
     "1. Prefiltro: el candidato se postula en el portal y responde preguntas cerradas (Sí / No / Parcial) sobre los "
     "requisitos indispensables. Red Human solo recomienda; RH decide.\n"
     "2. Filtro Red Human: se aplican la prueba proyectiva Persona bajo la lluvia, la batería psicométrica del puesto "
     "(Ayudante: Cleaver y Zavic; Cumplimiento: Terman, Cleaver, Herrmann y 16PF; Gerente: Terman, MOSS, Cleaver, "
     "Herrmann y 16PF) y la Entrevista profunda Red Human.\n"
     "3. Filtro humano: referencias laborales y revisión documental, entrevista con el sindicato, examen médico (con "
     "consentimiento expreso por escrito del candidato) y entrevista con el líder del área.\n"
     "4. Contratación: propuesta y aceptación de condiciones, documentos de ingreso y firma del contrato.\n"
     "5. Onboarding: tareas de ingreso, inducción y alta como colaborador.\n\n"
     "Tiempos objetivo: Prefiltro 2 días, Filtro Red Human 5 días, Filtro humano 7 días, Contratación 5 días."),
    ("Política de evaluaciones psicométricas y médicas", "politica",
     "Política de evaluaciones de Demo Grupak.\n\n"
     "Las pruebas psicométricas se asignan desde la actividad Batería psicométrica con «Asignar y enviar»; el "
     "candidato recibe una clave y entra al portal oficial del proveedor. Los resultados solo los consulta RH.\n"
     "El examen médico requiere el consentimiento expreso y por escrito del candidato antes de realizarse. El informe "
     "médico completo solo lo ven las personas con permiso de informes médicos; los demás ven el dictamen (Apto, Apto "
     "con restricciones o No apto).\n"
     "Ninguna evaluación decide por sí misma: cada resultado lo revisa una persona de RH, que registra su conclusión.\n"
     "Nunca se pregunta ni se registra información sobre salud fuera del examen médico, embarazo, religión, estado "
     "civil u orientación sexual."),
    ("Guía de Onboarding: primeros 30 días", "manual",
     "Guía de Onboarding de Demo Grupak.\n\n"
     "Día 1: bienvenida, entrega de equipo y accesos, recorrido por las instalaciones y presentación con el líder.\n"
     "Semana 1: curso de inducción (cultura, reglamento interior, seguridad e higiene) y reunión con el sindicato.\n"
     "Semana 2 a 4: plan de 30 días con objetivos claros acordados con el líder; retroalimentación cada semana.\n"
     "Día 30: revisión del periodo con RH y el líder; se confirma el ingreso y se cierra el Onboarding.\n"
     "Documentos de ingreso: identificación oficial, CURP, RFC, NSS, comprobante de domicilio y estado de cuenta "
     "para nómina."),
]


class Abortar(Exception):
    pass


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def nota(msg: str) -> None:
    print(f"  · {msg}")


def llamar(etiqueta: str, fn, *a, **k):
    """Llama la función del endpoint y traduce su HTTPException a un aborto legible."""
    try:
        return fn(*a, **k)
    except HTTPException as ex:
        raise Abortar(f"{etiqueta}: {ex.detail}") from None


# ============================================================ verificaciones previas


def verificar_esquema() -> None:
    insp = sa_inspect(engine)
    tablas = set(insp.get_table_names())
    faltan = [t for t in ("cuentas", "vacantes", "candidatos", "postulaciones", "pruebas_psicometricas",
                          "plantillas_proceso", "evaluaciones", "eventos_evaluacion", "entrevistas", "colaboradores",
                          "ciclos_desempeno", "evaluaciones_desempeno", "mediciones_clima", "respuestas_clima",
                          "participaciones_clima", "documentos_conocimiento", "fragmentos_conocimiento") if t not in tablas]
    if faltan:
        raise Abortar(f"Faltan tablas ({', '.join(faltan)}). Arranca la API una vez con esta versión y vuelve a correr.")
    for tabla, col in {"pruebas_psicometricas": "tipo", "evaluaciones": "pruebas"}.items():
        if col not in {c["name"] for c in insp.get_columns(tabla)}:
            raise Abortar(f"La tabla {tabla} no tiene la columna «{col}». Arranca la API una vez con esta versión.")


def leer_ids_proveedor(texto: str) -> dict:
    ids = dict(IDS_PROVEEDOR)
    if not texto:
        return ids
    for parte in texto.split(","):
        if "=" not in parte:
            raise Abortar(f"--ids-proveedor: «{parte}» no tiene la forma Nombre=ID.")
        nombre, valor = (x.strip() for x in parte.split("=", 1))
        nombre = next((n for n in PRUEBAS if n.lower() == nombre.lower()), None)
        if nombre is None:
            raise Abortar(f"--ids-proveedor: prueba desconocida. Usa: {', '.join(PRUEBAS)}.")
        if not valor.isdigit():
            raise Abortar(f"--ids-proveedor: el identificador de {nombre} debe ser numérico.")
        ids[nombre] = valor
    return ids


# ============================================================ 1. Cuenta y accesos


def asegurar_cuenta(db) -> Cuenta:
    cu = (db.query(Cuenta)
          .filter((Cuenta.slug == SLUG_CUENTA) | (func.lower(Cuenta.nombre) == NOMBRE_CUENTA.lower()))
          .order_by(Cuenta.id).first())
    if cu is not None:
        if cu.estado == "Eliminada":
            raise Abortar(f"La Cuenta «{cu.nombre_visible}» (id {cu.id}) está eliminada. Restáurala desde "
                          "Configuración → Cuentas y vuelve a correr el script.")
        nota(f"Cuenta existente: {cu.nombre_visible} (id {cu.id}, slug {cu.slug})")
    else:
        cu = Cuenta(nombre=NOMBRE_CUENTA, nombre_comercial=NOMBRE_CUENTA, razon_social="Demo Grupak, S.A. de C.V.",
                    contacto_nombre="Equipo comercial Red Human", estado="Activa")
        db.add(cu)
        db.flush()
        cu.slug = slug_cuenta_unico(db, cu.nombre_comercial, cu.id)
        registrar(db, ACTOR, "cuenta_creada", "cuenta", str(cu.id), {"nombre": NOMBRE_CUENTA, "demo": True})
        ok(f"Cuenta creada: {NOMBRE_CUENTA} (id {cu.id}, slug {cu.slug})")
    with db.begin_nested():
        n = sproc.asegurar_rutas_base(db, cu.id, ACTOR)
    if n:
        ok(f"Rutas base sembradas en la Cuenta: {n}")
    return cu


def vincular_usuarios(db, cu: Cuenta, admins: list, crear: str) -> list:
    """Quién podrá ver la Cuenta. Regresa [(correo, contraseña_o_None)]."""
    salida = []
    if admins:
        usuarios = []
        for correo in admins:
            u = db.query(Usuario).filter(func.lower(Usuario.correo) == correo.strip().lower()).first()
            if u is None:
                raise Abortar(f"--admin: no existe el usuario {correo}. No se crea nada.")
            usuarios.append(u)
    else:
        usuarios = [u for u in db.query(Usuario).filter(Usuario.rol == "Administrador").all() if getattr(u, "activo", True)]
    for u in usuarios:
        if not db.query(UsuarioCuenta).filter(UsuarioCuenta.usuario_id == u.id, UsuarioCuenta.cuenta_id == cu.id).first():
            db.add(UsuarioCuenta(usuario_id=u.id, cuenta_id=cu.id))
            ok(f"Acceso a la Cuenta para {u.correo}")
        salida.append((u.correo, None))
    if crear:
        correo = crear.strip().lower()
        u = db.query(Usuario).filter(func.lower(Usuario.correo) == correo).first()
        password = None
        if u is None:
            password = secrets.token_urlsafe(9) + "-7a"
            u = llamar("--crear-usuario", crear_usuario_basico, db, correo, "Demo Grupak", "Demostración", "Administrador", password)
            ok(f"Usuario creado: {correo} (deberá cambiar la contraseña al entrar)")
        else:
            nota(f"El usuario {correo} ya existía: su contraseña no se toca")
        if not db.query(UsuarioCuenta).filter(UsuarioCuenta.usuario_id == u.id, UsuarioCuenta.cuenta_id == cu.id).first():
            db.add(UsuarioCuenta(usuario_id=u.id, cuenta_id=cu.id))
        if not u.cuenta_predeterminada_id:
            u.cuenta_predeterminada_id = cu.id
        salida.append((correo, password))
    db.flush()
    return salida


def actor_rh(db, cu: Cuenta) -> Usuario:
    """Administrador vinculado a la Cuenta: firma (bitácora) lo que se siembra con las funciones de los endpoints."""
    u = (db.query(Usuario).join(UsuarioCuenta, UsuarioCuenta.usuario_id == Usuario.id)
         .filter(UsuarioCuenta.cuenta_id == cu.id, Usuario.rol == "Administrador").order_by(Usuario.id).first())
    if u is None:
        raise Abortar("Ningún Administrador ve la Cuenta «Demo Grupak»: usa --admin con un Administrador o --crear-usuario.")
    return u


# ============================================================ 2. Catálogo


def asegurar_prueba(db, cu: Cuenta, nombre: str, tipo: str, id_proveedor: str, descripcion: str) -> PruebaPsicometrica:
    clave = clave_prueba(nombre)
    pr = db.query(PruebaPsicometrica).filter(PruebaPsicometrica.cuenta_id == cu.id, PruebaPsicometrica.clave == clave).first()
    if pr is None:
        pr = PruebaPsicometrica(cuenta_id=cu.id, clave=clave, nombre=nombre, descripcion=descripcion, modo="integrada",
                                proveedor=PROVEEDOR, id_proveedor=id_proveedor, tipo=tipo, activa=True, creado_por=ACTOR)
        db.add(pr)
        db.flush()
        ok(f"{'Batería' if tipo == 'bateria' else 'Prueba'}: {nombre} (id {pr.id}, proveedor «{id_proveedor}»)")
    else:
        if pr.id_proveedor != id_proveedor:
            nota(f"{nombre}: identificador en el proveedor {pr.id_proveedor or '—'} → {id_proveedor}")
            pr.id_proveedor = id_proveedor
        if not pr.activa:
            pr.activa = True
            nota(f"{nombre}: reactivada")
    return pr


def asegurar_catalogo(db, cu: Cuenta, ids: dict) -> dict:
    catalogo = {n: asegurar_prueba(db, cu, n, "prueba", ids[n], d) for n, d in PRUEBAS.items()}
    for nombre, pruebas in BATERIAS.items():
        catalogo[nombre] = asegurar_prueba(db, cu, nombre, "bateria", ",".join(dict.fromkeys(ids[p] for p in pruebas)),
                                           "Incluye: " + ", ".join(pruebas) + ".")
    nota(f"Catálogo listo: {len(PRUEBAS)} pruebas y {len(BATERIAS)} baterías")
    return catalogo


# ============================================================ 3. Ruta


def asegurar_ruta(db, cu: Cuenta) -> PlantillaProceso:
    pl = db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cu.id, PlantillaProceso.nombre == NOMBRE_RUTA).first()
    if pl is not None:
        if not pl.activa:
            pl.activa = True
            nota("Ruta reactivada")
        nota(f"Ruta existente: {NOMBRE_RUTA} (id {pl.id}, versión {pl.version}) — no se sobrescribe")
        return pl
    pasos = sproc.normalizar_pasos(PASOS_RUTA)
    if len(pasos) != 12:
        raise Abortar(f"La ruta debía tener 12 actividades y tiene {len(pasos)}.")
    db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cu.id).update({"predeterminada": False})
    pl = PlantillaProceso(
        cuenta_id=cu.id, nombre=NOMBRE_RUTA,
        descripcion="Prefiltro → Persona bajo la lluvia, batería psicométrica y Entrevista profunda Red Human → "
                    "referencias, sindicato, médico y líder → propuesta, documentos y contratación → onboarding (12 pasos).",
        pasos=pasos, etapas=sproc.normalizar_etapas(ETAPAS_RUTA), version=1, predeterminada=True, activa=True,
        creado_por=ACTOR, actualizada_por=ACTOR,
    )
    db.add(pl)
    db.flush()
    ok(f"Ruta creada: {NOMBRE_RUTA} (id {pl.id}, 12 actividades, predeterminada de la Cuenta)")
    return pl


# ============================================================ 4. Vacantes


def proceso_con_bateria(db, cu: Cuenta, pl: PlantillaProceso, bateria_id: int) -> dict:
    pasos = [dict(p) for p in pl.pasos]
    if not any(p["id"] == PASO_BATERIA for p in pasos):
        raise Abortar(f"La ruta «{pl.nombre}» ya no tiene la actividad «{PASO_BATERIA}» (¿la editaron?).")
    for p in pasos:
        if p["id"] == PASO_BATERIA:
            p["pruebas"] = [bateria_id]
    return sproc.proceso_para_vacante(db, cu.id, {}, {"plantilla_id": pl.id, "pasos": pasos, "etapas": pl.etapas})


def asegurar_vacantes(db, cu: Cuenta, pl: PlantillaProceso, catalogo: dict, publicar: bool, u: Usuario) -> dict:
    vacantes = {}
    for d in VACANTES:
        v = db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.titulo == d["titulo"], Vacante.estado != "Eliminada").first()
        bateria = catalogo[d["bateria"]]
        if v is not None:
            if not sproc.tiene_proceso(v):
                v.proceso = proceso_con_bateria(db, cu, pl, bateria.id)
                nota(f"{v.codigo}: se le asignó la ruta con «{bateria.nombre}»")
            nota(f"Vacante existente: {v.codigo} {v.titulo} ({v.estado})")
            vacantes[d["titulo"]] = v
            continue
        desde, hasta, periodo = d["sueldo"]
        v = Vacante(
            codigo="TMP", cuenta_id=cu.id, cliente_id=None, responsable_id=u.id, titulo=d["titulo"], area=d["area"],
            empresa=nombre_empresa(cu, None, True), enfoque_entrevista=d["enfoque"], ubicacion_estado=d["estado"],
            ubicacion_municipio=d["municipio"], ubicacion=texto_ubicacion(d["estado"], d["municipio"]),
            modalidad=d["modalidad"], sueldo_desde=desde, sueldo_hasta=hasta, sueldo_moneda="MXN",
            sueldo_periodicidad=periodo, sueldo=texto_sueldo(desde, hasta, "MXN", periodo),
            estado="Publicada" if publicar else "En revisión", requisitos=" · ".join(d["indispensables"]),
            requisitos_deseables=d["deseables"], beneficios=d["beneficios"], descripcion=d["descripcion"],
            resumen=d["descripcion"], seniority=d["seniority"], plataformas=["Portal"] if publicar else [],
            preguntas_filtro=[{"pregunta": f"¿Cumples con: {r}?", "tipo": "si_no", "valida": r, "respuesta_esperada": "Sí",
                               "descarta": True, "opciones": ["Sí", "No", "Parcial"]} for r in d["indispensables"]],
        )
        v.proceso = proceso_con_bateria(db, cu, pl, bateria.id)
        db.add(v)
        db.flush()
        v.codigo = f"VAC-{1036 + v.id}"
        v.slug = _slug_unico(db, v.titulo, v.id)
        if publicar:
            v.publicada_en = v.creada_en
        registrar(db, ACTOR, "vacante_creada", "vacante", v.codigo,
                  {"titulo": v.titulo, "estado": v.estado, "proceso": NOMBRE_RUTA, "bateria": bateria.nombre, "demo": True})
        ok(f"Vacante: {v.codigo} {v.titulo} · {bateria.nombre} · {v.estado}")
        vacantes[d["titulo"]] = v
    return vacantes


# ============================================================ 5. Candidatos con avance


def _nota_historial(p: Postulacion, texto: str) -> None:
    p.historial = list(p.historial or []) + [{"evento": "demo_sembrado", "texto": texto, "usuario": ACTOR, "fecha": ahora().isoformat()}]


def _evaluacion_de_paso(db, p: Postulacion, paso_id: str):
    return (db.query(Evaluacion).filter(Evaluacion.postulacion_id == p.id, Evaluacion.paso_id == paso_id,
                                        Evaluacion.estado != "cancelada").order_by(Evaluacion.id.desc()).first())


def _nueva_evaluacion(db, p: Postulacion, cu: Cuenta, **campos) -> Evaluacion:
    ev = sev.nueva(p, cu.id, ACTOR, None, **campos)
    sev.asegurar_ligas(ev)
    db.add(ev)
    db.flush()
    sev.asignar_codigo(ev)
    sev.evento(db, ev, "creada", ACTOR, a=ev.estado, tipo=ev.tipo, forma=ev.forma, demo=True)
    return ev


def _resultado(db, ev: Evaluacion, conclusion: str, comentarios: str, realizada_por: str, canal: str = "sistema",
               revisar: bool = True) -> None:
    sev.registrar_resultado(db, ev, actor=ACTOR, canal=canal, conclusion=conclusion, comentarios=comentarios,
                            realizada_por=realizada_por, adjuntos=[], version=None)
    if revisar:
        sev.revisar(db, ev, actor="RH Demo Grupak", usuario_id=None, conclusion=conclusion, comentario="Revisión de demostración.")


def _psicometria(db, p: Postulacion, cu: Cuenta, bateria: PruebaPsicometrica, terminada: bool) -> Evaluacion:
    """Misma forma que «Asignar y enviar» en modo simulado (sin clave del proveedor: nunca se le escribe)."""
    ev = _nueva_evaluacion(db, p, cu, tipo="psicometrica", nombre=bateria.nombre, forma="integrada", prueba_id=bateria.id,
                           pruebas=[bateria.id], proveedor=PROVEEDOR, id_proveedor=bateria.id_proveedor,
                           paso_integrada="asignada", paso_id=PASO_BATERIA)
    sev.aplicar_paso(db, ev, "enviada", ACTOR)
    if terminada:
        sev.aplicar_paso(db, ev, "iniciada", ACTOR)
        sev.aplicar_paso(db, ev, "completada", ACTOR)
        _resultado(db, ev, "favorable", "Resultado SIMULADO para la demostración: perfil compatible con el puesto.",
                   f"{PROVEEDOR} (simulado)", canal="proveedor")
    return ev


def _prefiltro_cumple(p: Postulacion, score: int) -> None:
    p.analisis = {**(p.analisis or {}), "respuestas_web": {"cumple_requisitos": "Sí", "disponibilidad": "Sí"},
                  "fortalezas_cv": ["Experiencia relevante para el puesto", "Estabilidad laboral"],
                  "requisitos_cumplidos": ["Escolaridad", "Experiencia mínima"]}
    p.prefiltro_completo = True
    p.estado = "cumple"
    p.score = score


# --- Entrevista profunda Red Human: SIEMPRE con los esquemas reales (corrige el crash de la v1) ---

def guion_red_human() -> dict:
    return ia.GuionEntrevista(
        enfoque="Conocer a fondo la trayectoria comercial, el estilo de liderazgo y la motivación para el programa trainee.",
        temas=["Experiencia reciente en ventas", "Resultados medibles", "Coordinación de equipo", "Manejo de presión",
               "Motivación y objetivos", "Disponibilidad"],
        preguntas=["Cuéntame de tu experiencia más reciente en ventas.",
                   "¿Qué resultado concreto te enorgullece y cómo lo mediste?",
                   "¿Cómo coordinas y das seguimiento a tu equipo?",
                   "Platícame de una situación de mucha presión y cómo la resolviste.",
                   "¿Qué te motiva de este programa de Gerente Trainee?",
                   "¿Tienes disponibilidad para viajar dentro de la región?"],
    ).model_dump()


def evaluacion_red_human(match: int) -> dict:
    return ia.EvaluacionEntrevista(
        resumen="Perfil comercial sólido con evidencia de resultados y primera experiencia coordinando a un equipo. "
                "Comunicación clara y estructurada. (Evaluación SIMULADA para la demostración.)",
        fortalezas=["Orientación a resultados: creció la cartera 18 % en un año.",
                    "Experiencia coordinando a cuatro ejecutivos con seguimiento semanal.",
                    "Comunicación clara y con ejemplos concretos."],
        riesgos=["Validar el manejo de equipos de más de 10 personas.", "Confirmar disponibilidad para viajar."],
        areas_desarrollo=["Formación de líderes de segundo nivel."],
        calif_experiencia=8.4, calif_comunicacion=8.8, match_perfil=match, recomendacion="avanzar",
        evidencia="«Crecimos la cartera 18 % en un año con un proceso de seguimiento semanal.»",
        perfil=None, faltante=[],
    ).model_dump()


TRANSCRIPT_RED_HUMAN = [
    {"rol": "assistant", "texto": "Hola, soy Red Human. Gracias por tu tiempo; platiquemos de tu experiencia en ventas."},
    {"rol": "user", "texto": "Claro. Llevo tres años en ventas B2B y coordino a un equipo de cuatro ejecutivos."},
    {"rol": "assistant", "texto": "¿Qué resultado concreto te enorgullece y cómo lo mediste?"},
    {"rol": "user", "texto": "Crecimos la cartera 18 % en un año con un proceso de seguimiento semanal por cliente."},
    {"rol": "assistant", "texto": "¿Cómo das seguimiento a tu equipo cuando alguien no llega a su meta?"},
    {"rol": "user", "texto": "Hago una sesión uno a uno, revisamos su embudo y acordamos tres acciones para la semana."},
    {"rol": "assistant", "texto": "Gracias por tu tiempo. Red Human compartirá tus respuestas con el equipo de RH."},
]


def _entrevista_valida(e: Entrevista) -> bool:
    try:
        ia.EvaluacionEntrevista.model_validate(e.evaluacion or {})
        ia.GuionEntrevista.model_validate(e.guion or {})
    except Exception:  # noqa: BLE001
        return False
    return all(m.get("rol") in ("assistant", "user") for m in (e.transcript or []))


def asegurar_entrevista_red_human(db, p: Postulacion, match: int) -> str:
    """Crea la Entrevista profunda evaluada o REPARA la que sembró la v1 (causa del crash de /dashboard/entrevistas)."""
    e = next((x for x in reversed(list(p.entrevistas or [])) if x.estado == "evaluada"), None)
    fin = ahora() - timedelta(days=1)
    if e is None:
        e = Entrevista(codigo="TMP", candidato_id=p.candidato_id, token=secrets.token_urlsafe(24), tipo="texto")
        p.entrevistas.append(e)
        db.add(e)
        db.flush()
        e.codigo = f"ENT-{300 + e.id}"
        accion = "creada"
    elif _entrevista_valida(e):
        return "ya válida"
    else:
        accion = "reparada"
    e.estado, e.cierre, e.motivo = "evaluada", "manual", ""
    e.consentimiento = True
    e.consentimiento_fecha = e.consentimiento_fecha or fin - timedelta(minutes=25)
    e.iniciada_en = e.iniciada_en or fin - timedelta(minutes=22)
    e.finalizada_en = e.finalizada_en or fin
    e.ultima_actividad_en = e.ultima_actividad_en or fin
    e.guion = guion_red_human()
    e.transcript = list(TRANSCRIPT_RED_HUMAN)
    e.evaluacion = evaluacion_red_human(match)
    if accion == "reparada":
        registrar(db, ACTOR, "entrevista_demo_reparada", "entrevista", e.codigo, {"postulacion": p.codigo, "motivo": "esquema v1 incompleto"})
    return accion


def _completar_actividades(db, cu: Cuenta, p: Postulacion, esc: str) -> list:
    """Idempotente (también sobre lo que sembró la v1): instrucciones, formularios y guiones de las actividades."""
    hechos = []
    lluvia = _evaluacion_de_paso(db, p, "persona-lluvia")
    if lluvia is not None and not lluvia.instrucciones:
        lluvia.instrucciones = INSTRUCCIONES_LLUVIA
        hechos.append("instrucciones de Persona bajo la lluvia")
    if esc == "filtro_humano":
        estado = asegurar_entrevista_red_human(db, p, 86)
        if estado != "ya válida":
            hechos.append(f"Entrevista profunda Red Human {estado}")
        sindicato = _evaluacion_de_paso(db, p, "entrevista-sindicato")
        if sindicato is not None and not (sindicato.guion or {}).get("preguntas"):
            sindicato.guion = guion_humano("general")
            sindicato.instrucciones = sindicato.instrucciones or INSTRUCCIONES_SINDICATO
            hechos.append("guion de la entrevista con sindicato")
        if _evaluacion_de_paso(db, p, "referencias") is None:
            _nueva_evaluacion(db, p, cu, tipo="referencias", nombre="Referencias y revisión documental", forma="asignada",
                              paso_id="referencias", evaluador_tipo="externo", evaluador_nombre="Equipo de RH Demo Grupak",
                              evaluador_correo=f"rh@{DOMINIO}", instrucciones=INSTRUCCIONES_REFERENCIAS,
                              guion=guion_referencias())
            hechos.append("formulario de referencias laborales")
        if _evaluacion_de_paso(db, p, "entrevista-lider") is None:
            _nueva_evaluacion(db, p, cu, tipo="entrevista_humana", nombre="Entrevista con líder", forma="asignada",
                              paso_id="entrevista-lider", evaluador_tipo="externo",
                              evaluador_nombre="Andrés Villarreal Ruiz (Gerente de Ventas)",
                              evaluador_correo=correo_col("ger"), instrucciones=INSTRUCCIONES_LIDER,
                              guion=guion_humano("jefe_directo"))
            hechos.append("entrevista con líder asignada (sin cita)")
    return hechos


def sembrar_candidato(db, cu: Cuenta, d: dict, v: Vacante, catalogo: dict) -> tuple:
    c = (db.query(Candidato).filter(Candidato.cuenta_id == cu.id, func.lower(Candidato.correo) == d["correo"],
                                    Candidato.eliminado_en.is_(None)).first())
    p = next((x for x in c.postulaciones_activas if x.vacante_id == v.id), None) if c is not None else None
    esc = d["escenario"]
    if p is not None:
        nota(f"Candidato existente: {c.nombre} · {p.codigo} en {p.etapa}")
    else:
        if c is None:
            c = _crear_candidato(db, cu.id, d["nombre"], "Formulario", False, correo=d["correo"], telefono="",
                                 ubicacion=d["ubicacion"], experiencia=d["experiencia"],
                                 cv_datos={"resumen": d["experiencia"], "demo": True})
        p = crear_postulacion(db, c, v, cu.id, "formulario", consentimiento=True)
        bateria = catalogo[next(x["bateria"] for x in VACANTES if x["titulo"] == v.titulo)]
        if esc == "prefiltro":
            _nota_historial(p, "Candidato de demostración: postulación recibida, prefiltro pendiente.")
        elif esc == "filtro_red_human":
            _prefiltro_cumple(p, 82)
            p.etapa = "Entrevista IA"
            lluvia = _nueva_evaluacion(db, p, cu, tipo="otra", nombre="Persona bajo la lluvia", forma="registro_directo",
                                       paso_id="persona-lluvia", instrucciones=INSTRUCCIONES_LLUVIA)
            _resultado(db, lluvia, "favorable", "Prueba proyectiva SIMULADA: manejo adecuado de la presión.", "Psic. Laura Méndez (demo)")
            _psicometria(db, p, cu, bateria, terminada=False)
            _nota_historial(p, "Candidato de demostración: prefiltro cumple, Persona bajo la lluvia revisada y batería "
                               "psicométrica enviada (simulada); falta la Entrevista profunda Red Human.")
        elif esc == "filtro_humano":
            _prefiltro_cumple(p, 88)
            p.etapa = "Entrevista Humana"
            lluvia = _nueva_evaluacion(db, p, cu, tipo="otra", nombre="Persona bajo la lluvia", forma="registro_directo",
                                       paso_id="persona-lluvia", instrucciones=INSTRUCCIONES_LLUVIA)
            _resultado(db, lluvia, "favorable", "Prueba proyectiva SIMULADA: seguridad y recursos ante la presión.", "Psic. Laura Méndez (demo)")
            _psicometria(db, p, cu, bateria, terminada=True)
            sindicato = _nueva_evaluacion(db, p, cu, tipo="entrevista_humana", nombre="Entrevista con sindicato",
                                          forma="registro_directo", paso_id="entrevista-sindicato",
                                          instrucciones=INSTRUCCIONES_SINDICATO, guion=guion_humano("general"))
            _resultado(db, sindicato, "avanzar", "Entrevista SIMULADA: sin observaciones del sindicato.",
                       "Comité sindical (demo)", revisar=False)
            _nueva_evaluacion(db, p, cu, tipo="medica", nombre="Examen médico", forma="registro_directo", paso_id="medica")
            _nota_historial(p, "Candidato de demostración: Filtro Red Human completo (simulado), entrevista con sindicato "
                               "aprobada; faltan referencias, consentimiento médico y entrevista con líder.")
        registrar(db, ACTOR, "candidato_demo_sembrado", "postulacion", p.codigo, {"vacante": v.codigo, "etapa": p.etapa, "escenario": esc, "demo": True})
        ok(f"Candidato: {c.nombre} ({c.codigo}) · {p.codigo} · {v.titulo} · etapa {p.etapa}")
    for h in _completar_actividades(db, cu, p, esc):
        ok(f"{p.codigo}: {h}")
    return c, p


# ============================================================ 6. Roster


def asegurar_colaboradores(db, cu: Cuenta, u: Usuario) -> dict:
    salida = {}
    for clave, nombre, puesto, area, sede, jefe in COLABORADORES:
        correo = correo_col(clave)
        col = db.query(Colaborador).filter(Colaborador.cuenta_id == cu.id, func.lower(Colaborador.correo) == correo,
                                           Colaborador.eliminado_en.is_(None)).first()
        if col is None:
            datos = rcol.ColaboradorIn(nombre=nombre, correo=correo, puesto=puesto, area=area, ubicacion=sede,
                                       jefe=salida[jefe].codigo if jefe else "", fecha_ingreso="2024-03-04",
                                       tipo_contratacion="Tiempo indeterminado")
            col = llamar("Colaborador", rcol._crear, db, cu, datos, ACTOR, rcol._roster(db, cu.id))
            registrar(db, u.nombre, "colaborador_alta_manual", "colaborador", col.codigo, {"nombre": nombre, "demo": True})
            ok(f"Colaborador: {col.codigo} {nombre} · {puesto}")
        salida[clave] = col
    nota(f"Roster: {len(salida)} colaboradores")
    return salida


# ============================================================ 7. Clima


def asegurar_clima(db, cu: Cuenta, u: Usuario, roster: dict) -> MedicionClima:
    m = db.query(MedicionClima).filter(MedicionClima.cuenta_id == cu.id, MedicionClima.titulo == TITULO_CLIMA).first()
    if m is not None:
        nota(f"Medición existente: {m.codigo} ({m.estado}) — no se repite")
        return m
    llamar("Medición de clima", rclima.crear_medicion, rclima.MedicionIn(
        titulo=TITULO_CLIMA, descripcion="Encuesta anónima de ejemplo: liderazgo y comunicación (respuestas simuladas).",
        preguntas=PREGUNTAS_CLIMA, dimensiones=["Liderazgo", "Comunicación", "Comentarios"], anonima=True),
        db=db, u=u, cuenta=cu)
    m = db.query(MedicionClima).filter(MedicionClima.cuenta_id == cu.id, MedicionClima.titulo == TITULO_CLIMA).one()
    # Igual que «Enviar encuesta» (`abrir`) pero SIN mandar la liga a nadie: abrir + invitar con el helper interno.
    m.anonima, m.estado, m.abierta_en = True, "abierta", ahora()
    m.cierra_en = ahora() + timedelta(days=7)
    m.filtros_envio = {"areas": [], "sedes": [], "seleccion": True}
    codigos = [c.codigo for c in roster.values()]
    partes, _ = rclima._invitar_colaboradores(db, m, codigos, cu, u.nombre)
    registrar(db, u.nombre, "medicion_clima_abierta", "clima", m.codigo,
              {"invitados": len(partes), "anonima": True, "avisos": "sin envío (demo)", "demo": True})
    # respuestas simuladas de 8 de 10 invitados (participación 80 %), con tendencia realista por dimensión
    for col in list(roster.values())[:8]:
        resp = {}
        for p in PREGUNTAS_CLIMA:
            if p["tipo"] == "escala":
                base = 4 if p["dimension"] == "Liderazgo" else 3
                resp[p["id"]] = max(1, min(5, base + AZAR.choice([-1, 0, 0, 1])))
            elif p["tipo"] == "opcion":
                resp[p["id"]] = AZAR.choices(p["opciones"], weights=[4, 3, 2, 1])[0]
            elif AZAR.random() < 0.6:
                resp[p["id"]] = AZAR.choice(COMENTARIOS_CLIMA)
        llamar("Respuesta de clima", rclima.responder_interno, m.codigo,
               rclima.ResponderIn(respuestas=resp, colaborador_id=col.codigo), db=db, u=u, cuenta=cu)
    rclima.cerrar_medicion(m, u.nombre)
    registrar(db, u.nombre, "medicion_clima_cerrada", "clima", m.codigo, {"demo": True})
    r = clima_resultados.calcular(m)
    ok(f"Clima: {m.codigo} «{TITULO_CLIMA}» · 8/10 respuestas · cerrada · resumen: "
       f"{ {k: v for k, v in r.items() if k in ('participacion', 'indice')} }")
    return m


# ============================================================ 8. Desempeño y 360°


def _ciclo(db, cu: Cuenta, nombre: str):
    return db.query(CicloDesempeno).filter(CicloDesempeno.cuenta_id == cu.id, CicloDesempeno.nombre == nombre).first()


def _crear_ciclo(db, cu: Cuenta, u: Usuario, nombre: str, periodo: str, descripcion: str, equipo: str, criterios: list) -> CicloDesempeno:
    llamar(nombre, rdes.crear_ciclo, rdes.CicloIn(nombre=nombre, periodo=periodo, descripcion=descripcion, equipo=equipo,
                                                   criterios=criterios, origen_criterios="manual"), db=db, u=u, cuenta=cu)
    return _ciclo(db, cu, nombre)


def _participar(db, cu: Cuenta, u: Usuario, c: CicloDesempeno, col: Colaborador, evaluador: str) -> EvaluacionDesempeno:
    llamar(c.nombre, rdes.agregar_participantes, c.codigo,
           rdes.ParticipantesIn(colaborador_ids=[col.codigo], evaluador=evaluador), db=db, u=u, cuenta=cu)
    return db.query(EvaluacionDesempeno).filter(EvaluacionDesempeno.ciclo_id == c.id, EvaluacionDesempeno.colaborador_id == col.id).one()


def asegurar_semestral(db, cu: Cuenta, u: Usuario, roster: dict) -> CicloDesempeno:
    c = _ciclo(db, cu, NOMBRE_SEMESTRAL)
    if c is not None:
        nota(f"Evaluación existente: {c.codigo} {c.nombre} ({c.estado}) — no se repite")
        return c
    c = _crear_ciclo(db, cu, u, NOMBRE_SEMESTRAL, "2026-S1",
                     "Evaluación semestral de toda la plantilla: metas medibles + competencias con niveles 1-5 (datos simulados).",
                     "Toda la plantilla", CRITERIOS_SEMESTRAL)
    jefes = {k: next(x[1] for x in COLABORADORES if x[0] == j) for k, _n, _p, _a, _s, j in COLABORADORES if j}
    evals = {k: _participar(db, cu, u, c, roster[k], jefes[k]) for k in RESULTADOS_SEMESTRAL}
    llamar("Iniciar evaluación semestral", rdes.iniciar, c.codigo, db=db, u=u, cuenta=cu)
    for k, (obj, asis, inc, equipo, com, completar) in RESULTADOS_SEMESTRAL.items():
        if obj is None:
            continue  # queda «Pendiente»: la demo muestra avance parcial
        filas = [{"criterio_id": "objetivos", "real": obj}, {"criterio_id": "asistencia", "real": asis},
                 {"criterio_id": "incidencias", "real": inc}, {"criterio_id": "equipo", "valoracion": equipo},
                 {"criterio_id": "comunicacion", "valoracion": com}]
        if not completar:
            filas = filas[:3]  # «En proceso»: falta valorar competencias
        fortalezas = ["Supera sus metas del semestre"] if obj >= 100 else []
        brechas = [{"tema": "Cumplimiento de metas", "descripcion": "Reforzar seguimiento semanal de su embudo.",
                    "criterio_id": "objetivos", "confirmada": True}] if obj < 90 else []
        llamar(f"Evaluar {k}", rdes.evaluar, evals[k].codigo, rdes.EvaluarIn(
            resultados=filas, fortalezas=fortalezas, brechas=brechas,
            conclusion="Evaluación de demostración capturada por su jefe directo." if completar else None,
            completar=completar), db=db, u=u, cuenta=cu)
    db.expire(c)  # recarga sus evaluaciones antes de calcular el avance
    av = calc.avance(c)
    ok(f"Desempeño semestral: {c.codigo} · {len(evals)} personas · avance {av}")
    return c


def asegurar_360(db, cu: Cuenta, u: Usuario, roster: dict) -> list:
    ciclos = []
    for perspectiva, evaluador, valores, conclusion in PERSPECTIVAS_360:
        nombre = f"{NOMBRE_360} — {perspectiva}"
        c = _ciclo(db, cu, nombre)
        if c is not None:
            nota(f"360° existente: {c.codigo} {perspectiva} ({c.estado})")
            ciclos.append(c)
            continue
        c = _crear_ciclo(db, cu, u, nombre, "2026",
                         f"Evaluación 360° (perspectiva: {perspectiva}). Mismas competencias en las tres perspectivas "
                         "para compararlas. Datos simulados.", "Gerente de Ventas", CRITERIOS_360)
        e = _participar(db, cu, u, c, roster["ger"], evaluador)
        llamar(f"Iniciar 360 {perspectiva}", rdes.iniciar, c.codigo, db=db, u=u, cuenta=cu)
        llamar(f"Evaluar 360 {perspectiva}", rdes.evaluar, e.codigo, rdes.EvaluarIn(
            resultados=[{"criterio_id": k, "valoracion": v} for k, v in valores.items()],
            fortalezas=["Integridad", "Orientación a resultados"],
            brechas=[{"tema": "Desarrollo de su equipo", "descripcion": "Formar a su segundo nivel de liderazgo.",
                      "criterio_id": "desarrollo", "confirmada": True}],
            conclusion=conclusion, completar=True), db=db, u=u, cuenta=cu)
        llamar(f"Cerrar 360 {perspectiva}", rdes.cerrar, c.codigo, rdes.CerrarIn(), db=db, u=u, cuenta=cu)
        ok(f"360° {perspectiva}: {c.codigo} · calificación {db.get(EvaluacionDesempeno, e.id).calificacion}")
        ciclos.append(c)
    return ciclos


# ============================================================ 9. Base de conocimiento


def asegurar_conocimiento(db, cu: Cuenta, u: Usuario) -> list:
    docs = []
    for titulo, tipo, texto in DOCUMENTOS:
        d = db.query(DocumentoConocimiento).filter(DocumentoConocimiento.cuenta_id == cu.id, DocumentoConocimiento.titulo == titulo,
                                                   DocumentoConocimiento.activo.is_(True)).first()
        if d is not None:
            if not d.publicado:
                nota(f"«{titulo}» existe como borrador: no se publica solo (decisión de RH)")
            else:
                nota(f"Documento existente: «{titulo}»")
            docs.append(d)
            continue
        # Igual que «Nuevo documento → Pegar texto» (POST /conocimiento/documentos): se crea e indexa al instante.
        d = DocumentoConocimiento(cuenta_id=cu.id, titulo=titulo, tipo=tipo, texto=texto, creado_por=u.nombre,
                                  publicado=True, areas=[], puestos=[])
        db.add(d)
        db.flush()
        rag.indexar_documento(db, d)
        registrar(db, u.nombre, "conocimiento_documento_cargado", "documento_conocimiento", str(d.id),
                  {"titulo": d.titulo, "fragmentos": d.fragmentos_total, "embeddings": d.con_embeddings, "demo": True})
        ok(f"Conocimiento: «{titulo}» publicado ({d.fragmentos_total} fragmentos, "
           f"{'con' if d.con_embeddings else 'sin'} embeddings)")
        docs.append(d)
    return docs


def armar_resumen(db, args, cu, vacantes, postulaciones, medicion, semestral, ciclos_360, accesos) -> list:
    app = settings.app_url.rstrip("/")
    L = [
        f"Cuenta: {NOMBRE_CUENTA} (id {cu.id}) — elígela en el selector de Cuenta del tablero.",
        f"Tablero            {app}/dashboard",
        f"Candidatos         {app}/dashboard/candidatos",
        f"Entrevistas        {app}/dashboard/entrevistas",
        f"Clima              {app}/dashboard/clima          ({medicion.codigo})",
        f"Desempeño          {app}/dashboard/desempeno      ({semestral.codigo} semestral · "
        + ", ".join(c.codigo for c in ciclos_360) + " 360°)",
        f"Conocimiento       {app}/dashboard/conocimiento",
        f"Bolsa de trabajo   {app}/portal?cuenta={cu.slug}"
        + ("" if args.publicar else "   (vacía hasta publicar: --publicar o «Publicar» en cada vacante)"),
    ]
    for d in VACANTES:
        v = vacantes[d["titulo"]]
        L.append(f"  {v.codigo}  {v.titulo:<40} {d['bateria']:<22} {app}/aplicar/{v.slug}")
    L.append("Candidatos demo:")
    for (c, p), d in zip(postulaciones, CANDIDATOS):
        L.append(f"  {p.codigo}  {c.nombre:<32} {d['vacante']:<40} etapa interna: {p.etapa}")
    L.append("Ligas de formularios (Jorge, Filtro humano):")
    p = postulaciones[2][1]
    for paso_id, etiqueta in (("referencias", "Referencias laborales"), ("entrevista-lider", "Entrevista con líder")):
        ev = _evaluacion_de_paso(db, p, paso_id)
        liga = sev.liga_evaluador(ev) if (ev is not None and args.aplicar) else "(la liga definitiva se genera al aplicar)"
        L.append(f"  {etiqueta:<22} {liga}")
    L.append("Accesos:")
    for correo, password in accesos:
        L.append(f"  {correo}  contraseña temporal: {password}   (se muestra UNA sola vez; se pide cambiarla al entrar)"
                 if password else f"  {correo}  (usa su contraseña de siempre)")
    if not args.ids_proveedor:
        L.append("\n⚠ Los identificadores de Psicométricas.mx son de EJEMPLO (101-106). Si este servidor tiene las llaves "
                 "del proveedor, vuelve a correr con --ids-proveedor y los reales antes de usar «Asignar y enviar».")
    return L


# ============================================================ main


def main() -> int:
    ap = argparse.ArgumentParser(description="Siembra el entorno de demostración «Demo Grupak».")
    ap.add_argument("--aplicar", action="store_true", help="escribe los cambios (sin esto es un simulacro)")
    ap.add_argument("--publicar", action="store_true", help="deja las vacantes Publicadas")
    ap.add_argument("--admin", action="append", default=[], help="usuario existente que verá la Cuenta (repetible)")
    ap.add_argument("--crear-usuario", default="", help="crea un Administrador vinculado solo a Demo Grupak")
    ap.add_argument("--ids-proveedor", default="", help='identificadores reales: "Cleaver=12,Zavic=34,…"')
    args = ap.parse_args()

    print(f"Base: {engine.url.render_as_string(hide_password=True)}")
    print("Modo: " + ("APLICAR (una sola transacción)" if args.aplicar else "SIMULACRO (no se escribe nada; usa --aplicar)"))
    db = SessionLocal()
    confirmar = db.commit
    db.commit = db.flush  # los endpoints hacen commit: aquí todo queda en UNA transacción (atómica / reversible)
    try:
        verificar_esquema()
        ids = leer_ids_proveedor(args.ids_proveedor)

        print("\n1 · Cuenta")
        cu = asegurar_cuenta(db)
        accesos = vincular_usuarios(db, cu, args.admin, args.crear_usuario)
        u = actor_rh(db, cu)
        print("\n2 · Catálogo psicométrico")
        catalogo = asegurar_catalogo(db, cu, ids)
        print("\n3 · Ruta")
        pl = asegurar_ruta(db, cu)
        print("\n4 · Vacantes")
        vacantes = asegurar_vacantes(db, cu, pl, catalogo, args.publicar, u)
        print("\n5 · Candidatos")
        postulaciones = [sembrar_candidato(db, cu, d, vacantes[d["vacante"]], catalogo) for d in CANDIDATOS]
        print("\n6 · Colaboradores")
        roster = asegurar_colaboradores(db, cu, u)
        print("\n7 · Clima")
        medicion = asegurar_clima(db, cu, u, roster)
        print("\n8 · Desempeño y 360°")
        semestral = asegurar_semestral(db, cu, u, roster)
        ciclos_360 = asegurar_360(db, cu, u, roster)
        print("\n9 · Base de conocimiento")
        asegurar_conocimiento(db, cu, u)

        # Comprobaciones finales (si algo falla no se guarda nada)
        for (_c, p), d in zip(postulaciones, CANDIDATOS):
            paso = next((x for x in (p.proceso or {}).get("pasos", []) if x["id"] == PASO_BATERIA), None)
            esperada = catalogo[next(x["bateria"] for x in VACANTES if x["titulo"] == d["vacante"])].id
            if paso is None or paso.get("pruebas") != [esperada]:
                raise Abortar(f"{p.codigo}: la ruta congelada no trae la batería esperada.")
            for e in p.entrevistas or []:
                if e.estado == "evaluada" and not _entrevista_valida(e):
                    raise Abortar(f"{e.codigo}: la entrevista no cumple el esquema que espera /dashboard/entrevistas.")
        resumen = armar_resumen(db, args, cu, vacantes, postulaciones, medicion, semestral, ciclos_360, accesos)
        db.flush()
        if args.aplicar:
            confirmar()
        else:
            db.rollback()
    except Abortar as ex:
        db.rollback()
        print(f"\n✗ {ex}\nNo se escribió nada.")
        return 1
    except Exception:
        db.rollback()
        print("\n✗ Error inesperado; no se escribió nada.")
        raise
    finally:
        db.close()

    print("\n" + "=" * 78)
    print("SIMULACRO completo: todo valida. Corre con --aplicar para guardarlo." if not args.aplicar
          else "Listo: «Demo Grupak» quedó sembrada.")
    print("=" * 78)
    print("\n".join(resumen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
