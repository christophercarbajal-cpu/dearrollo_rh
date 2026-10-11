"""Entrevista OPERATIVA y Biblioteca de Perfiles (2026-10-10, Cambio 1). Base desechable, SIN red ni OpenAI.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_entrevista_operativa.py

Cubre: biblioteca de 10 oficios (fija, ajustable solo por vacante), guion ARMADO de 6 secciones (máx. 3 indispensables),
prompt del modo operativo sin repetir lo del prefiltro (una pregunta por mensaje), evaluación A-B-C-D con la regla estricta
(falla en indispensables o logística → «No recomendable», nunca descarta) y alertas en «Puntos por validar».
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
check(eop.recomendacion_de({"A": {"resultado": "cumple"}, "B": {"resultado": "cumple"}, "C": {"resultado": "cumple"},
                            "D": {"resultado": "no_cumple"}}) == "no_recomendable"
      and eop.recomendacion_de({"A": {"resultado": "no_cumple"}, "B": {"resultado": "cumple"}, "C": {"resultado": "cumple"},
                                "D": {"resultado": "cumple"}}) == "con_reservas", "regla estricta: C o D fallan → No recomendable")
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
        x.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": sproc.ruta_base("masivos_sin_documentos")["pasos"]})
    db.commit()
    H = {"X-Cuenta-Id": str(cuenta.id)}

    opciones = client.get("/procesos/opciones", headers=H).json()
    check(any(e["valor"] == "operativo" and "Masivos" in e["texto"] for e in opciones["enfoquesEntrevistaAgente"]),
          "el enfoque se ofrece como «Operativo (sugerido para Masivos)»")
    lib = client.get("/vacantes/perfiles-operativos", headers=H)
    check(lib.status_code == 200 and len(lib.json()["oficios"]) == 10 and [s["clave"] for s in lib.json()["secciones"]] ==
          ["trayectoria", "oficio", "indispensables", "situacion_actual", "motivacion", "logistica"], "GET /vacantes/perfiles-operativos")

    # ================= 2. Guion armado =================
    print("\n--- 2. Guion armado de 6 secciones ---")
    r = client.post(f"/vacantes/{v.codigo}/guiones/generar", headers=H, json={})
    check(r.status_code == 200, f"genera los guiones de la ruta ({r.status_code} {r.text[:200]})")
    db.expire_all()
    g = sgui.contenido_de(db.get(Vacante, v.id), "entrevista_whatsapp")
    op = g.get("operativo") or {}
    check([s["clave"] for s in op.get("secciones", [])] == ["trayectoria", "oficio", "indispensables", "situacion_actual", "motivacion", "logistica"]
          and [s["origen"] for s in op["secciones"]] == ["fijo", "biblioteca", "ia", "fijo", "fijo", "fijo"],
          "6 secciones en orden fijo: fijo · biblioteca · IA · fijo · fijo · fijo")
    sec = {s["clave"]: s for s in op["secciones"]}
    check(op["oficio"] == "montacarguista" and sec["oficio"]["preguntas"][:3] == eop.BIBLIOTECA["montacarguista"]["datos"]
          and sec["oficio"]["preguntas"][3] == eop.BIBLIOTECA["montacarguista"]["situacion"]["pregunta"],
          "Verificación del oficio = 3 datos + 1 situación de la biblioteca")
    check(len(op["indispensables"]) == 3 and len(sec["indispensables"]["preguntas"]) == 3, "se exploran máximo 3 indispensables")
    check("Tultitlán" in sec["logistica"]["preguntas"][0], "Logística usa la ubicación REAL de la vacante")
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
    check(r.status_code == 200 and g["operativo"]["secciones"][1]["preguntas"][:3] == nuevos, "al regenerar se usa el perfil de la vacante")

    # ================= 4. Conversación =================
    print("\n--- 4. Conversación por WhatsApp: una pregunta por mensaje, sin repetir el prefiltro ---")
    tel = iter(range(5540000001, 5540009999))

    def postulacion(nombre: str) -> Postulacion:
        t = str(next(tel))
        c = _crear_candidato(db, cuenta.id, nombre, "WhatsApp", False, telefono=t, correo=f"o{t}@correo.mx")
        p = crear_postulacion(db, c, db.get(Vacante, v.id), cuenta.id, "whatsapp", consentimiento=True)
        p.analisis = {**(p.analisis or {}), "respuestas_prefiltro": [
            {"criterio": "Disponibilidad para rolar turnos", "pregunta": "¿Puedes rolar turnos (horario)?", "respuesta": "Sí"}]}
        db.commit()
        return p

    RESP_BUENAS = {
        "trabajo más reciente": "Trabajé 3 años en un almacén de Bodega Norte operando montacargas de combustión.",
        "saliste": "Cerraron la sucursal y buscamos algo más cerca.",
        "montacargas has operado": "Contrabalanceado de combustión y retráctil eléctrico, de 2.5 toneladas.",
        "carga máxima": "Hasta 2.5 toneladas en tarimas de bebidas a 8 metros de altura.",
        "almacenes trabajaste": "En Bodega Norte y en un centro de distribución de abarrotes.",
        "revisas al montacargas": "Hago el checklist: frenos, claxon, nivel de gas, llantas y uñas; si hay falla lo reporto y no lo opero.",
        "cumplido con esto": "Tengo mi DC-3 desde 2023 y siempre he rolado turnos en la bodega.",
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
        salida = await sent.turno_whatsapp(db, p, e, "Sí, comencemos", "whatsapp")
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
    check(opv["recomendacion"] == "recomendable" and ev["recomendacion"] == "avanzar" and ev["score_entrevista"] == 100,
          "todo cumple → Recomendable (score de la entrevista desde los criterios)")

    # No recomendable por logística + alertas
    MALAS = {**RESP_BUENAS,
             "trabajo más reciente": "Estuve 2 meses en una bodega operando montacargas, antes en otra empresa.",
             "saliste": "Me corrieron porque llegué tarde varias veces.",
             "de tu casa": "Como 2 horas en dos camiones.",
             "empezar": "No puedo empezar antes de un mes.",
             "situación laboral": "Estoy trabajando todavía en una tienda."}
    p2 = postulacion("Nora Lejos")
    e2, _ = asyncio.run(entrevistar(p2, MALAS))
    ev2 = e2.evaluacion or {}
    op2 = ev2.get("operativa") or {}
    check(op2["criterios"]["D"]["resultado"] == "no_cumple" and op2["recomendacion"] == "no_recomendable" and ev2["recomendacion"] == "no_avanzar",
          "falla de logística → «No recomendable»")
    db.refresh(p2)
    check(p2.activa and p2.motivo_cierre in (None, ""), "…y NO descarta: la postulación sigue activa (RH decide)")
    alertas = " ".join(op2["alertas"])
    check("menos de 3 meses" in alertas and "despido" in alertas and "90 minutos" in alertas and "Trabaja actualmente" in alertas,
          "alertas: empleo < 3 meses, despido, traslado > 90 min, trabaja actualmente")
    check(any(r.startswith("Alerta: ") for r in ev2["riesgos"]), "las alertas encabezan «Puntos por validar»")

    # Excluyente del oficio: patín hidráulico no es montacargas
    EXCL = {**RESP_BUENAS,
            "trabajo más reciente": "Trabajé un año en una tienda moviendo cajas con patín hidráulico.",
            "montacargas has operado": "Solo el patín hidráulico, montacargas nunca.",
            "carga máxima": "Unas tarimas con el patín.",
            "almacenes trabajaste": "En la tienda nada más."}
    p3 = postulacion("Pepe Patín")
    e3, _ = asyncio.run(entrevistar(p3, EXCL))
    op3 = (e3.evaluacion or {}).get("operativa") or {}
    check(op3["criterios"]["A"]["resultado"] != "cumple" and op3["recomendacion"] == "con_reservas",
          "experiencia con algo excluyente (patín) no acredita el oficio → con reservas")
    check(not any(eop.es_alerta_escolaridad(a) for a in op2["alertas"] + op3["alertas"]), "ninguna alerta de escolaridad o documentos")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — entrevista operativa")
