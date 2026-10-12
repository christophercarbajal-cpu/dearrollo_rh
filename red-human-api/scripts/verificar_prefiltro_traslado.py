"""Prefiltro web de 3 preguntas Sí/No y datos de traslado por WhatsApp (prueba del 2026-10-11 por la tarde, Bloque 1).
Base desechable, SIN red.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_prefiltro_traslado.py

Cubre: el prefiltro web se responde solo con Sí / No (sin «Parcial») y lleva al menos 3 preguntas clave (indispensables,
escolaridad, exigencias, ubicación u horario); con menos de 3 la vacante no se guarda y RH ve el mensaje exacto. El
prefiltro de WhatsApp pregunta, una por mensaje y en orden estricto, escolaridad exacta → colonia/municipio → tiempo de
traslado → medio de transporte; ante una respuesta vaga repregunta UNA sola vez; la escolaridad que no cumple descarta
de inmediato con el mensaje de cierre y el traslado nunca descarta.
"""

import asyncio
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

_dir = tempfile.mkdtemp(prefix="rh_pf_traslado_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "pf_traslado.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "RESEND_API_KEY",
          "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-pf-traslado"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.deps import usuario_actual, usuario_admin, usuario_decisor  # noqa: E402
from app.models import Cuenta, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.services import guiones as sgui  # noqa: E402
from app.services import ia  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp as swa  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
WA = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _meta_falso(cuerpo):
    WA.append(cuerpo)
    return {"enviado": True, "proveedor": "meta", "detalle": "", "wa_id": f"wamid.pft.{len(WA)}"}


settings.whatsapp_provider = "meta"
settings.meta_whatsapp_token, settings.meta_phone_number_id = "x", "123"
swa._meta_post = _meta_falso

# ================= 1. Generación: prefiltro web ≥ 3 Sí/No y datos de WhatsApp en orden =================
print("\n--- 1. Generación del prefiltro ---")
ficha = ia.FichaGuion(titulo="Cargador", requisitos_indispensables=["Secundaria terminada"],
                      responsabilidades=["Cargar y descargar cajas de hasta 25 kg", "Acomodar mercancía en tarimas"],
                      ubicacion="Tlalnepantla, Estado de México")
web, wa = ia.asegurar_prefiltros([], [], ficha, hay_web=True, hay_whatsapp=True)
check(len(web) >= 3, f"con 1 indispensable el prefiltro web se completa a {len(web)} preguntas clave (exigencias / ubicación)")
check(all(q["opciones"] == ["Sí", "No"] for q in web) and not any("Parcial" in str(q) for q in web), "todas se responden solo Sí / No")
check([q for q in web if q["descarta"]] == [web[0]] and web[0]["valida"] == "Secundaria terminada",
      "solo el indispensable de RH es eliminatorio (lo agregado nunca descarta)")
check(any("cargar y descargar cajas" in q["pregunta"] for q in web) and any(q.get("clave") == "ubicacion" for q in web),
      "salen de las exigencias del trabajo y de la ubicación")
check([q.get("dato") for q in wa[:4]] == ["escolaridad", "zona", "traslado", "transporte"],
      "WhatsApp: escolaridad → colonia/municipio → traslado → transporte, en ese orden")
check(wa[0]["descarta"] and wa[0]["valida"] == "Secundaria terminada" and not any(q.get("reconfirma") == "Secundaria terminada" for q in wa),
      "la escolaridad exacta reconfirma el nivel indispensable (sin pregunta sí/no repetida)")
check("Tlalnepantla" in wa[2]["pregunta"] and not wa[2]["descarta"], "el traslado usa la ubicación real y nunca descarta")
check(sgui.normalizar_pregunta({"pregunta": "¿Tienes licencia?", "tipo": "si_no", "opciones": ["Sí", "No", "Parcial"]})["opciones"] == ["Sí", "No"],
      "una pregunta guardada con «Parcial» se normaliza a Sí / No")
pocos, _ = ia.asegurar_prefiltros([], [], ia.FichaGuion(titulo="Ayudante", requisitos_indispensables=["Saber leer"]), hay_web=True,
                                 hay_whatsapp=False)
check(len(pocos) < 3, "sin datos suficientes el generador no inventa: quedan menos de 3 (RH debe agregar)")

RUTA = [{"id": "sw", "tipo": "solicitud_web", "etapa": "Prefiltro"},
        {"id": "pw", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro", "obligatorio": True},
        {"id": "ew", "tipo": "entrevista_whatsapp", "etapa": "Entrevista IA", "obligatorio": True},
        {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"}]

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    obtener(db).modo_prueba = False
    cuenta = Cuenta(nombre="PF Traslado", nombre_comercial="PF Traslado", estado="Activa", slug="pf-traslado")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_admin, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    h = {"X-Cuenta-Id": str(cuenta.id)}

    # ================= 2. Mínimo de 3 preguntas al guardar =================
    print("\n--- 2. Bloqueo con menos de 3 preguntas ---")
    base = {"titulo": "Cargador demo", "area": "Operaciones", "ubicacion_estado": "Ciudad de México", "ubicacion_municipio": "Iztapalapa",
            "modalidad": "Presencial", "descripcion": "Carga y descarga.", "requisitos_indispensables": ["Secundaria terminada"],
            "proceso": {"pasos": RUTA}, "generar_si_falta": False}
    dos = [{"pregunta": "¿Terminaste la secundaria?", "tipo": "si_no", "valida": "Secundaria terminada", "respuesta_esperada": "Sí", "descarta": True},
           {"pregunta": "¿Puedes rolar turnos?", "tipo": "si_no", "valida": "Rolar turnos", "respuesta_esperada": "Sí", "descarta": False}]
    r = client.post("/vacantes", headers=h, json={**base, "preguntas_filtro": dos})
    check(r.status_code == 400 and r.json()["detail"] == ia.MENSAJE_MIN_PREFILTRO,
          "con 2 preguntas NO se crea: «Para el prefiltro necesitamos al menos 3 requisitos clave…»")
    tres = dos + [{"pregunta": "¿Puedes cargar cajas de 25 kg?", "tipo": "si_no", "valida": "Cargar 25 kg", "respuesta_esperada": "Sí"}]
    r = client.post("/vacantes", headers=h, json={**base, "preguntas_filtro": tres})
    check(r.status_code == 201, "con 3 preguntas sí se crea")
    cod = r.json()["id"]
    r = client.patch(f"/vacantes/{cod}", headers=h, json={"preguntas_filtro": dos})
    check(r.status_code == 400 and "al menos 3 requisitos clave" in r.json()["detail"], "editar y dejar 2 también se bloquea")

    # ================= 3. Conversación por WhatsApp =================
    print("\n--- 3. Datos por WhatsApp: uno por mensaje, repregunta única ---")
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).first()
    v.cuenta_id, v.titulo, v.ubicacion = cuenta.id, "Cargador", "Tlalnepantla"
    v.preguntas_filtro = web
    v.preguntas_filtro_whatsapp = wa
    v.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": RUTA})
    db.commit()
    tel = iter(range(5542220001, 5542229999))

    def nueva(nombre):
        c = _crear_candidato(db, cuenta.id, nombre, "WhatsApp", False, telefono=str(next(tel)))
        p = crear_postulacion(db, c, db.get(Vacante, v.id), cuenta.id, "whatsapp", consentimiento=True)
        db.commit()
        return p

    def turno(p, texto):
        return asyncio.run(pconv.turno(db, db.query(Postulacion).filter_by(codigo=p.codigo).one(), texto, "whatsapp"))

    def fresca(p):
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=p.codigo).one()

    p = nueva("Ana Torres")
    r = turno(p, "Hola")
    check(r["respuesta"].endswith("¿Hasta qué año estudiaste?") and "Parcial" not in r["respuesta"], "1) primero la escolaridad exacta")
    r = turno(p, "pues normal")
    check(r["respuesta"] == pconv.REPREGUNTA_DATO["escolaridad"], "respuesta vaga → repregunta por un dato concreto")
    r = turno(p, "Terminé la secundaria")
    check(r["respuesta"].endswith("¿En qué colonia y municipio vives?"), "2) después colonia y municipio")
    r = turno(p, "cerca")
    check(r["respuesta"] == pconv.REPREGUNTA_DATO["zona"], "«cerca» es vago → pide el nombre de la colonia")
    r = turno(p, "por aquí")
    check("¿Cuánto tiempo haces de tu casa a Tlalnepantla" in r["respuesta"],
          "la repregunta es UNA sola vez: si sigue vaga, sigue con el traslado (3)")
    p = fresca(p)
    check(any("Colonia y municipio" in x for x in p.analisis.get("puntos_validar_prefiltro") or []),
          "lo que siguió vago queda en Puntos por validar")
    r = turno(p, "como 2 horas y media")
    check(r["respuesta"].endswith("¿En qué transporte llegarías?"), "4) al final el medio de transporte; el traslado largo NO descarta")
    r = turno(p, "En camión y metro")
    p = fresca(p)
    d = p.analisis.get("prefiltro_datos") or {}
    check(p.activa and (p.analisis.get("prefiltro_web") or {}).get("resultado") == "cumple", "con la escolaridad cumplida el prefiltro aprueba")
    check(d.get("traslado_min") == 150 and d.get("nivel") == "Secundaria" and "Metro" in d.get("transporte_medio", "")
          and d.get("zona") == "por aquí", "los datos quedan guardados para la ficha (zona, minutos, transporte, escolaridad)")

    print("\n--- 4. Escolaridad que no cumple → descarte inmediato ---")
    q = nueva("Beto Parcial")
    turno(q, "Hola")
    r = turno(q, "Llegué hasta 2do de secundaria")
    q = fresca(q)
    check(not q.activa and q.motivo_cierre == "descartado" and q.estado == "no_cumple", "«2do de secundaria» con secundaria terminada → DESCARTADO")
    check("necesitamos secundaria terminada" in r["respuesta"] and "colonia" not in r["respuesta"],
          "mensaje de cierre de inmediato (ya no pregunta colonia ni traslado)")

    print("\n--- 5. Formulario web + WhatsApp ---")
    w = nueva("Carla Web")
    w.analisis = {"respuestas_web": [{"pregunta": web[0]["pregunta"], "respuesta": "Sí"}]}
    db.commit()
    r = turno(w, "Hola")
    check("¿Hasta qué año estudiaste?" in r["respuesta"], "con «Sí» en el formulario también pide la escolaridad exacta")
    r = turno(w, "primero de secundaria")
    w = fresca(w)
    check(not w.activa and w.motivo_cierre == "descartado" and (w.analisis.get("inconsistencias") or [{}])[-1].get("fuente") == "reconfirmacion",
          "el dato contradice el «Sí» → descarta y la contradicción queda registrada")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — prefiltro web de 3 preguntas y traslado por WhatsApp")
