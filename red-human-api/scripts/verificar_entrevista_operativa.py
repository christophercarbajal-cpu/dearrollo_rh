"""Entrevista OPERATIVA y Biblioteca de Perfiles (2026-10-10, Cambio 1). Base desechable, SIN red ni OpenAI.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_entrevista_operativa.py

Cubre: biblioteca de 10 oficios (fija, ajustable solo por vacante), guion ARMADO de 6 secciones (máx. 3 indispensables),
prompt del modo operativo sin repetir lo del prefiltro (una pregunta por mensaje), evaluación A-B-C-D y — C6 (prueba del 11
de octubre) — recomendación SIN puntaje por 5 requisitos ✓ / ✗ / ? (traslado ≤ 90 min, sueldo, experiencia, indispensables,
inicio ≤ 14 días): un ✗ grave (traslado > 120 min incluido) → «No recomendable» (detiene la ruta, nunca descarta); alertas sin habilidades blandas; C7
«Siguiente paso: …» tras la decisión de RH; C11 etiqueta en la tarjeta.
"""

import asyncio
import copy
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_dir = tempfile.mkdtemp(prefix="rh_operativa_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "operativa.db").replace("\\", "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET",
          "DROPBOX_SIGN_API_KEY", "DROPBOX_SIGN_CLIENT_ID"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-operativa"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ENFOQUES_ENTREVISTA, Cuenta, Postulacion, Vacante  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.services import entrevista_operativa as eop  # noqa: E402
from app.services import entrevistas as sent  # noqa: E402
from app.services import guiones as sgui  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


# ================= 1. Biblioteca =================
print("\n--- 1. Biblioteca de Perfiles ---")
NOMBRES = {"Almacenista", "Ayudante general", "Cargador", "Montacarguista", "Maquilador", "Chofer", "Cajero", "Guardia de seguridad",
           "Limpieza", "Vendedor de piso"}
check(len(eop.BIBLIOTECA) == 10 and {o["nombre"] for o in eop.BIBLIOTECA.values()} == NOMBRES, "10 oficios fijos en la biblioteca")
check(all(len(o["datos"]) == 3 and o["situacion"]["pregunta"] and o["situacion"]["esperado"] and o["sinonimos"] and o["excluyentes"]
          for o in eop.BIBLIOTECA.values()), "cada oficio trae sinónimos, excluyentes, 3 datos y 1 situación esperada")
check(eop.detectar_oficio("Montacarguista turno nocturno") == "montacarguista" and eop.detectar_oficio("Chofer repartidor") == "chofer"
      and eop.detectar_oficio("Ayudante general de almacén") == "ayudante_general" and eop.detectar_oficio("Contador senior") is None,
      "el oficio se detecta por sinónimos del puesto (el más específico gana)")
check("operativo" in ENFOQUES_ENTREVISTA, "nuevo enfoque «operativo»")
check(eop.minutos_traslado("como hora y media") == 90 and eop.minutos_traslado("2 horas en camión") == 120
      and eop.minutos_traslado("40 minutos") == 40, "traslado se interpreta en minutos")
# Cambio 19: sin umbrales fijos — la recomendación sale de la experiencia y de los Puntos por validar
EXP = {"experiencia": {"estado": "cumple"}, "puntos": {"estado": "cumple"}}
check(eop.recomendacion_de(EXP) == "recomendable", "Cambio 19: experiencia en puesto similar y sin puntos → Recomendable")
check(eop.recomendacion_de({**EXP, "puntos": {"estado": "no_cumple", "cantidad": 1}}) == "con_reservas", "experiencia + algún punto → Con reservas")
check(eop.recomendacion_de({"experiencia": {"estado": "no_cumple"}, "puntos": {"estado": "no_cumple", "cantidad": 2}}) == "no_recomendable",
      "sin experiencia y con puntos → No recomendable")
check(eop.recomendacion_de({"experiencia": {"estado": "no_cumple"}, "puntos": {"estado": "cumple"}}) == "con_reservas",
      "sin experiencia pero sin puntos → Con reservas")
check(eop.recomendacion_de({"experiencia": {"estado": "sin_dato"}, "puntos": {"estado": "cumple"}}) == "con_reservas"
      and eop.recomendacion_de({**EXP, "puntos": {"estado": "cumple", "dudas": ["Respuesta no concluyente"]}}) == "con_reservas",
      "experiencia o respuesta dudosa → Con reservas")
reqs = eop.requisitos_de({k: {"resultado": "cumple", "evidencia": "3 años en almacén"} for k in "ABCD"}, [], {}, {"traslado_min": 300})
check(eop.recomendacion_de(reqs) == "recomendable" and not any(hasattr(eop, x) for x in ("TRASLADO_MAX_MIN", "TRASLADO_NO_RECOMENDABLE_MIN",
                                                                                         "INICIO_MAX_DIAS", "EMPLEO_MIN_MESES")),
      "un traslado de 300 min ya no cambia nada: no hay umbrales fijos en el código")
from app.services import evaluacion_integral as sint  # noqa: E402
# Cambio 38: un solo vocabulario (Recomendable / Con reservas / No recomendable) también en el perfil Corporativo
check(sint._resultado([], operativo=False)["texto"] == "Recomendable" and sint._resultado([], operativo=True)["texto"] == "Recomendable",
      "Cambio 38: Corporativo y Operativo dicen «Recomendable» («Apto» solo en médica y socioeconómico)")
check([eop.dias_inicio(x) for x in ("mañana", "el lunes", "en 2 semanas", "en un mes", "no sé")] == [1, 7, 14, 30, None],
      "la fecha de inicio se lee en días (solo informativo: ya no hay umbral)")
check(eop.faltan_de({"experiencia": {"estado": "no_cumple"}, "puntos": {"estado": "no_cumple", "cantidad": 2}})
      == ["✗ Experiencia en puesto similar", "✗ 2 puntos por validar"], "C11: lo que falta se lista en la tarjeta")
check(eop.es_soft_skill("Mostró poca comunicación y actitud reservada") and not eop.es_soft_skill("Despido en su último empleo"),
      "C6: las habilidades blandas no son Puntos por validar")
original = copy.deepcopy(eop.BIBLIOTECA)

REQS = ["Experiencia operando montacargas", "Constancia DC-3 vigente", "Disponibilidad para rolar turnos", "Secundaria terminada",
        "Saber leer y escribir"]

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    cuenta = Cuenta(nombre="Operativa SA", nombre_comercial="Operativa", razon_social="Operativa SA de CV", estado="Activa", slug="operativa-sa")
    db.add(cuenta)
    db.flush()
    sproc.asegurar_rutas_base(db, cuenta.id)
    from app.models import UsuarioCuenta, Usuario

    from app.deps import usuario_actual, usuario_admin, usuario_decisor

    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for dep in (usuario_actual, usuario_decisor, usuario_admin):
        app.dependency_overrides[dep] = lambda: admin
    vacs = db.query(Vacante).filter(Vacante.estado == "Publicada").limit(2).all()
    v, v2 = vacs[0], vacs[1]
    for x, titulo in ((v, "Montacarguista"), (v2, "Cajero")):
        x.cuenta_id, x.titulo, x.enfoque_entrevista = cuenta.id, titulo, "operativo"
        x.requisitos = " · ".join(REQS)
        x.ubicacion = "Tultitlán, Estado de México"
        x.guiones = {}
        x.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": sproc.ruta_base("masivos_sin_documentos")["pasos"]
                                                                  # Cambio 29: con avatar opcional la ruta NO es Masivos (en Masivos la entrevista es fija)
                                                                  + [{"id": "avatar", "tipo": "entrevista_agente", "etapa": "Entrevista IA", "obligatorio": False}]})
    db.commit()
    H = {"X-Cuenta-Id": str(cuenta.id)}

    opciones = client.get("/procesos/opciones", headers=H).json()
    check(any(e["valor"] == "operativo" and "Masivos" in e["texto"] for e in opciones["enfoquesEntrevistaAgente"]),
          "el enfoque se ofrece como «Operativo (sugerido para Masivos)»")
    lib = client.get("/vacantes/perfiles-operativos", headers=H)
    check(lib.status_code == 200 and len(lib.json()["oficios"]) == 10 and [s["clave"] for s in lib.json()["secciones"]] ==
          ["trayectoria", "oficio", "indispensables", "situacion_actual", "motivacion", "logistica", "condiciones"], "GET /vacantes/perfiles-operativos")

    # ================= 2. Guion armado =================
    print("\n--- 2. Guion armado de 6 secciones ---")
    r = client.post(f"/vacantes/{v.codigo}/guiones/generar", headers=H, json={})
    check(r.status_code == 200, f"genera los guiones de la ruta ({r.status_code} {r.text[:200]})")
    db.expire_all()
    g = sgui.contenido_de(db.get(Vacante, v.id), "entrevista_whatsapp")
    op = g.get("operativo") or {}
    check([s["clave"] for s in op.get("secciones", [])] == ["trayectoria", "oficio", "indispensables", "situacion_actual", "motivacion", "logistica"]
          and [s["origen"] for s in op["secciones"]] == ["fijo", "biblioteca", "vacante", "fijo", "fijo", "fijo"],
          "secciones en orden fijo (Cambio 19: sin condiciones capturadas no hay sección 7): fijo · biblioteca · vacante · fijo · fijo · fijo")
    sec = {s["clave"]: s for s in op["secciones"]}
    # 2026-10-11 (tarde): estructura exacta de 18 — el oficio son las preguntas 8-10 (2 datos + la situación)
    check(op["oficio"] == "montacarguista" and sec["oficio"]["preguntas"][:2] == eop.BIBLIOTECA["montacarguista"]["datos"][:2]
          and sec["oficio"]["preguntas"][2:] == [eop.BIBLIOTECA["montacarguista"]["situacion"]["pregunta"]],
          "Verificación del oficio = 2 datos + 1 situación de la biblioteca (preguntas 8-10)")
    check(not eop.revisar_guion(g), f"Cambio 19: el guion pasa la revisión ({eop.revisar_guion(g)[:2]})")
    check(len(op["indispensables"]) == 3 and len(sec["indispensables"]["preguntas"]) == 3, "se exploran máximo 3 indispensables")
    check(sec["logistica"]["preguntas"] == [eop.PREGUNTA_INICIO], "Inicio = solo la fecha de inicio (el traslado va en el prefiltro)")
    check(all(not q.lower().startswith(("¿tienes", "¿cuentas", "¿puedes")) for q in g["preguntas"]), "todas las preguntas son abiertas")
    vista = client.get(f"/vacantes/{v.codigo}", headers=H).json()["guiones"]
    check((vista.get("perfilOperativo") or {}).get("oficio") == "montacarguista" and not vista["perfilOperativoPropio"],
          "la vacante muestra el perfil detectado de la biblioteca")

    # ================= 3. Ajuste por vacante =================
    print("\n--- 3. La biblioteca no se edita globalmente: solo por vacante ---")
    r = client.put(f"/vacantes/{v.codigo}/perfil-operativo", headers=H, json={"oficio": "montacarguista", "datos": ["a?", "b?"]})
    check(r.status_code == 400, "un perfil sin exactamente 3 datos → 400")
    nuevos = ["¿Qué montacargas has operado?", "¿Cuál es la carga máxima que has movido?", "¿En qué almacenes trabajaste?"]
    r = client.put(f"/vacantes/{v.codigo}/perfil-operativo", headers=H, json={"oficio": "montacarguista", "datos": nuevos})
    check(r.status_code == 200 and r.json()["guiones"]["perfilOperativo"]["datos"] == nuevos and r.json()["guiones"]["perfilOperativoPropio"],
          "PUT /vacantes/{codigo}/perfil-operativo guarda la copia de ESA vacante")
    check(eop.BIBLIOTECA == original and eop.perfil_de_vacante(db.get(Vacante, v2.id))["oficio"] == "cajero",
          "la biblioteca y las demás vacantes no cambian")
    check(r.json()["guiones"]["desactualizado"], "los guiones ya generados quedan «desactualizados» hasta regenerar")
    r = client.post(f"/vacantes/{v.codigo}/guiones/generar", headers=H, json={"sobrescribir_editadas": True})
    db.expire_all()
    g = sgui.contenido_de(db.get(Vacante, v.id), "entrevista_whatsapp")
    check(r.status_code == 200 and g["operativo"]["secciones"][1]["preguntas"][:2] == nuevos[:2],
          "al regenerar se usa el perfil de la vacante (sus 2 primeros datos: la estructura de 18 deja 3 preguntas al oficio)")

    # ================= 4. Conversación =================
    print("\n--- 4. Conversación por WhatsApp: una pregunta por mensaje, sin repetir el prefiltro ---")
    tel = iter(range(5540000001, 5540009999))

    def postulacion(nombre: str) -> Postulacion:
        t = str(next(tel))
        c = _crear_candidato(db, cuenta.id, nombre, "WhatsApp", False, telefono=t, correo=f"o{t}@correo.mx")
        p = crear_postulacion(db, c, db.get(Vacante, v.id), cuenta.id, "whatsapp", consentimiento=True)
        p.analisis = {**(p.analisis or {}), "respuestas_prefiltro": [
            {"criterio": "Disponibilidad para rolar turnos", "pregunta": "¿Puedes rolar turnos (horario)?", "respuesta": "Sí"}],
            # C6: traslado y sueldo vienen del prefiltro de WhatsApp (que ya quedó cumplido)
            "prefiltro_datos": {"traslado": "40 minutos", "traslado_min": 40, "sueldo": "Sí", "sueldo_acepta": True},
            "prefiltro_web": {"resultado": "cumple", "motivo": "", "en": "2026-10-11T10:00:00+00:00", "decidido_por": "Red Human"}}
        p.estado, p.prefiltro_completo = "cumple", True
        db.commit()
        return p

    RESP_BUENAS = {
        "último trabajo": "Trabajé 3 años en un almacén de Bodega Norte operando montacargas de combustión.",
        "saliste": "Cerraron la sucursal y buscamos algo más cerca.",
        "montacargas has operado": "Contrabalanceado de combustión y retráctil eléctrico, de 2.5 toneladas.",
        "carga máxima": "Hasta 2.5 toneladas en tarimas de bebidas a 8 metros de altura.",
        "almacenes trabajaste": "En Bodega Norte y en un centro de distribución de abarrotes.",
        "revisas al montacargas": "Hago el checklist: frenos, claxon, nivel de gas, llantas y uñas; si hay falla lo reporto y no lo opero.",
        "cumplido con esto": "Tengo mi DC-3 desde 2023 y siempre he rolado turnos en la bodega.",
        # Cambio 19: preguntas de evidencia de los indispensables
        "te tocó": "Tengo mi DC-3 desde 2023 y siempre he rolado turnos en la bodega.",
        "adquiriste": "En Bodega Norte, 3 años operando montacargas.",
        "cumpliste": "Tengo mi DC-3 desde 2023 y siempre he rolado turnos en la bodega.",
        "situación laboral": "Ahorita estoy buscando trabajo desde hace dos semanas.",
        "te interesa": "Me interesa la estabilidad y que es mi oficio.",
        "de tu casa": "Como 40 minutos en camión.",
        "empezar": "Puedo empezar el lunes.",
    }

    def responder_a(pregunta: str, respuestas: dict) -> str:
        for k, txt in respuestas.items():
            if k.lower() in pregunta.lower():
                return txt
        return "Sí, con gusto; en mi trabajo anterior hacía de todo un poco en el almacén."

    async def entrevistar(p: Postulacion, respuestas: dict):
        ok, _, _, e = await sent.iniciar_whatsapp(db, p, "prueba")
        db.commit()
        historial_preguntas = []
        # 2026-10-11 (tarde): la transición ya hizo la primera pregunta (sin «¿Comenzamos?»)
        salida = {"respuesta": e.transcript[0]["texto"]}
        for _ in range(25):
            pregunta = salida["respuesta"]
            if salida.get("terminada"):
                break
            historial_preguntas.append(pregunta)
            salida = await sent.turno_whatsapp(db, p, e, responder_a(pregunta, respuestas), "whatsapp")
        db.refresh(e)
        return e, historial_preguntas

    p1 = postulacion("Mario Montacargas")
    e1, preguntas = asyncio.run(entrevistar(p1, RESP_BUENAS))
    prompt = sent.system_prompt(e1)
    check("MODO OPERATIVO" in prompt and "YA LO RESPONDIÓ EN EL PREFILTRO" in prompt and "rolar turnos" in prompt,
          "el prompt usa el modo operativo con lo que ya respondió en el prefiltro")
    check(not any("horario" in q.lower() for q in preguntas), "no vuelve a preguntar el horario que ya contestó en el prefiltro")
    check(all(q.count("?") <= 1 for q in preguntas) and len(preguntas) >= 10, "una pregunta por mensaje y recorre las secciones")
    ev = e1.evaluacion or {}
    opv = ev.get("operativa") or {}
    check(e1.estado == "evaluada" and set(opv.get("criterios", {})) == {"A", "B", "C", "D"}, "la evaluación trae los criterios A-D")
    check(opv["criterios"]["A"]["resultado"] == "cumple" and opv["criterios"]["B"]["resultado"] == "cumple"
          and opv["criterios"]["D"]["resultado"] == "cumple", "experiencia, procedimiento y logística se acreditan con evidencia")
    check(opv["recomendacion"] == "recomendable" and ev["recomendacion"] == "avanzar" and ev["score_entrevista"] is None
          and opv["score"] is None, "C6: los 5 requisitos ✓ → Recomendable, SIN puntaje (ni base 100 ni mínimo 70)")
    check(all(r["estado"] == "cumple" for r in opv["requisitos"].values()) and opv["faltan"] == [],
          "C6: traslado (prefiltro), sueldo (prefiltro), experiencia, indispensables e inicio («el lunes») ✓")
    check(sproc.score_de_entrevista(ev) is None if hasattr(sproc, "score_de_entrevista") else True, "la entrevista operativa no aporta score")
    f1 = client.get(f"/candidatos/{p1.codigo}", headers=H).json()
    check(f1["recomendacionOperativa"]["texto"] == "Recomendable" and f1["scoreEntrevista"] is None,
          "C6: la ficha trae la recomendación operativa (sin «x/100»)")

    # Cambio 19: «No recomendable» = SIN experiencia en puesto similar y CON Puntos por validar (despido, sueldo)
    MALAS = {**RESP_BUENAS,
             "último trabajo": "Estuve 2 meses en una tienda de ropa atendiendo clientes.",
             "otro trabajo antes": "No.",
             "hacías en ese trabajo": "Atendía clientes en la caja.",
             "cuánto tiempo trabajaste": "Dos meses.",
             "montacargas has operado": "Ninguno, nunca he operado montacargas.",
             "carga máxima": "No sé.",
             "almacenes trabajaste": "En ninguno.",
             "revisas al montacargas": "No sé.",
             "adquiriste": "No tengo esa experiencia.",
             "saliste": "Me corrieron porque llegué tarde varias veces.",
             "empezar": "No puedo empezar antes de un mes.",
             "situación laboral": "Estoy trabajando todavía en una tienda."}
    p2 = postulacion("Nora Lejos")
    # el traslado de 130 min y el inicio en un mes ya NO cuentan (sin umbrales fijos); el sueldo no aceptado sí
    p2.analisis = {**(p2.analisis or {}), "prefiltro_datos": {"traslado": "Como 2 horas y 10", "traslado_min": 130,
                                                              "sueldo": "No, quiero 12 mil", "sueldo_acepta": False}}
    p2.etapa = "Entrevista IA"
    db.commit()
    e2, _ = asyncio.run(entrevistar(p2, MALAS))
    ev2 = e2.evaluacion or {}
    op2 = ev2.get("operativa") or {}
    check(op2["requisitos"]["experiencia"]["estado"] != "cumple" and op2["requisitos"]["puntos"]["estado"] == "no_cumple"
          and op2["recomendacion"] == "no_recomendable" and ev2["recomendacion"] == "no_avanzar",
          f"Cambio 19: sin experiencia y con Puntos por validar → «No recomendable» ({op2['requisitos']})")
    check("✗ Experiencia en puesto similar" in op2["faltan"] and any("por validar" in x for x in op2["faltan"]),
          f"los requisitos que faltan quedan listados: {op2['faltan']}")
    check(not any(eop.es_soft_skill(r) for r in ev2["riesgos"]), "Puntos por validar sin habilidades blandas")
    db.refresh(p2)
    check(p2.activa and p2.motivo_cierre in (None, ""), "…y NO descarta: la postulación sigue activa (RH decide)")
    puntos = " ".join(op2["puntos"])
    check("Salida por despido" in puntos and "Sueldo: pide otra cifra" in puntos and "raslado" not in puntos
          and "3 meses" not in puntos and "Trabaja actualmente" not in puntos,
          "Puntos por validar = salida por despido + sueldo; nada de umbrales (traslado, empleo corto, trabaja actualmente)")
    check(ev2["riesgos"] == op2["puntos"], "«Puntos por validar» de la ficha = los de la evaluación (solo las 4 categorías)")

    # Excluyente del oficio: patín hidráulico no es montacargas
    EXCL = {**RESP_BUENAS,
            "último trabajo": "Trabajé un año en una tienda moviendo cajas con patín hidráulico.",
            "montacargas has operado": "Solo el patín hidráulico, montacargas nunca.",
            "carga máxima": "Unas tarimas con el patín.",
            "almacenes trabajaste": "En la tienda nada más."}
    p3 = postulacion("Pepe Patín")
    e3, _ = asyncio.run(entrevistar(p3, EXCL))
    op3 = (e3.evaluacion or {}).get("operativa") or {}
    esperado3 = "con_reservas"  # Cambio 19: sin experiencia pero sin Puntos por validar
    check(op3["requisitos"]["experiencia"]["estado"] != "cumple" and op3["recomendacion"] == esperado3,
          f"experiencia con algo excluyente (patín) no acredita el oficio → {op3['recomendacion_texto']}")

    # ================= 5. Ruta, ficha y tarjeta (C6, C7, C11) =================
    print("\n--- 5. «No recomendable» detiene la ruta; RH continúa; tarjeta con etiqueta ---")
    s2 = client.get(f"/procesos/postulaciones/{p2.codigo}", headers=H).json()
    pasos2 = [x for e in s2["etapas"] for x in e["pasos"]]
    ew = next(x for x in pasos2 if x["tipo"] == "entrevista_whatsapp")
    check(ew["estadoUnificado"] == "no_aprobada" and ew["etiqueta"] == "No recomendable" and ew["score"] is None
          and "mínimo" not in ew["detalle"], f"C6: la actividad queda «No aprobada» por su etiqueta, sin mínimo 70: {ew['detalle']}")
    check(s2["bloqueo"] and s2["bloqueo"]["paso"] == ew["id"] and s2["recomendacion"]["texto"] == "Decisión de RH pendiente",
          "C6: «No recomendable» detiene la ruta para que RH decida")
    db.refresh(p2)
    check(p2.activa and p2.etapa == "Entrevista IA", "…sin descartar ni avanzar solo")
    r = client.post(f"/procesos/postulaciones/{p2.codigo}/pasos/{ew['id']}/excepcion", headers=H,
                    json={"motivo": "Vive lejos pero tiene auto propio, lo validamos en la entrevista"})
    check(r.status_code == 200, f"RH continúa por decisión propia ({r.status_code} {r.text[:150]})")
    s2 = client.get(f"/procesos/postulaciones/{p2.codigo}", headers=H).json()
    rec = s2["recomendacion"] or {}
    check(rec.get("texto", "").startswith("Siguiente paso: ") and "No avanzar" not in rec.get("texto", "")
          and "Entrevista humana" in rec.get("texto", ""), f"C7: la recomendación es la acción actual de la ruta: {rec.get('texto')}")
    lista = client.get(f"/candidatos?vacante={v.codigo}", headers=H).json()
    t1 = next(x for x in lista if x["id"] == p1.codigo)
    t2 = next(x for x in lista if x["id"] == p2.codigo)
    check(t1["card_label"] == {"clave": "recomendable", "texto": "Recomendable", "faltan": []},
          "C11: la tarjeta muestra solo la etiqueta «Recomendable»")
    db.refresh(p2)
    check((t2["card_label"] or {}).get("texto") == "No recomendable" and "✗ Experiencia en puesto similar" in (t2["card_label"] or {}).get("faltan", []),
          f"C11: …y los requisitos que faltan (✗ / ?) ({t2['card_label']} {t2['etapa']} {[(x.codigo, x.tipo, x.estado) for x in p2.entrevistas]})")
    integral = client.get(f"/candidatos/{p2.codigo}", headers=H).json()["resultadoIntegral"]
    check(integral["texto"] in ("Pendiente", "Con reservas", "Recomendable", "No recomendable") and "No apto" not in integral["texto"],
          f"C6: la evaluación integral usa las etiquetas nuevas («{integral['texto']}»)")
    check(not any(eop.es_alerta_escolaridad(a) for a in op2["alertas"] + op3["alertas"]), "ninguna alerta de escolaridad o documentos")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — entrevista operativa")
