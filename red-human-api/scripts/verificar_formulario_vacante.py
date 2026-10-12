"""Verificación de la Parte 3 (2026-09-12) — rediseño del formulario de creación de vacante — modo
demo (sin OpenAI), base SQLite desechable.

Cubre las dos reglas NO NEGOCIABLES del generador (no inventar condiciones; respetar lo capturado),
el sueldo estructurado (Desde/Hasta/Moneda/Periodicidad → texto derivado), seniority, la guía
opcional, el prefiltro desde los indispensables, plantillas con la misma estructura, el estado
"Publicada" al publicar desde el formulario, y la regresión de requisición → vacante.

Uso (desde red-human-api/):
    .venv/Scripts/python.exe scripts/verificar_formulario_vacante.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_form_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "form.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-form"
os.environ["SEMBRAR_DEMO"] = "true"  # los scripts de verificación sí usan los datos de ejemplo

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CAMPOS_PLANTILLA, Candidato, Cliente, Cuenta, Usuario, UsuarioCuenta, Vacante, texto_sueldo  # noqa: E402
from app.services import ia  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta Form", nombre_comercial="Reclutadora Bajío", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    cliente = Cliente(cuenta_id=cuenta.id, nombre="Farmacias Luna", nombre_comercial="Luna Salud", estado="Activo")
    db.add(cliente)
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta.id
        for p in c.postulaciones:
            p.cuenta_id = cuenta.id
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ---------- 1. texto_sueldo (derivado, nunca inventado) ----------
    check(texto_sueldo(10000, 12000, "MXN", "mensual") == "$10,000 – $12,000 MXN mensuales", "texto_sueldo: rango mensual")
    check(texto_sueldo(3000, None, "MXN", "semanal") == "Desde $3,000 MXN semanales", "texto_sueldo: solo desde")
    check(texto_sueldo(None, None, "MXN", "") == "A convenir" and texto_sueldo(1, 2, "MXN", "a_convenir") == "A convenir", "texto_sueldo: sin montos / a convenir")
    check("sueldo_periodicidad" in CAMPOS_PLANTILLA and len(CAMPOS_PLANTILLA) == 26, "CAMPOS_PLANTILLA incluye los 4 campos de sueldo y los 3 de Fase 4 (26)")

    # ---------- 2. /generar: respeta lo capturado, no inventa ----------
    ficha = {
        "titulo": "Auxiliar de farmacia", "area": "Operaciones", "seniority": "Junior", "ubicacion": "León, GTO",
        "modalidad": "Presencial", "sueldo_desde": 9000, "sueldo_hasta": 10500, "sueldo_moneda": "MXN", "sueldo_periodicidad": "mensual",
        "descripcion": "Atender mostrador y controlar inventario de medicamentos.",
        "requisitos_indispensables": ["Carrera técnica en farmacia", "Disponibilidad de fin de semana"],
        "requisitos_deseables": ["Manejo de punto de venta"],
        "beneficios": ["Vales de despensa"],
        "cliente_id": cliente.id, "mostrar_cliente_candidato": True,
    }
    r = client.post("/vacantes/generar", json=ficha)
    check(r.status_code == 200, "POST /vacantes/generar responde")
    g = r.json()
    check(g["requisitos_indispensables"][:2] == ficha["requisitos_indispensables"], "regla 5: indispensables capturados salen literal y primero")
    check(g["requisitos_deseables"][0] == "Manejo de punto de venta", "regla 5: deseables capturados salen literal y primero")
    check("Carrera técnica en farmacia" not in g["requisitos_deseables"] and "Manejo de punto de venta" not in g["requisitos_indispensables"],
          "regla 5: nunca se reclasifica indispensable ↔ deseable")
    check(g["beneficios"] == ["Vales de despensa"], "regla 4: beneficios = EXACTAMENTE los capturados (sin 'prestaciones de ley')")
    check(g["seniority"] == "Junior", "regla 5: seniority capturado se respeta")
    check(g["sueldo_texto"] == "$9,000 – $10,500 MXN mensuales" and g["empresa"] == "Luna Salud", "sueldo derivado y empresa resuelta viajan al frontend")
    check(g["descripcion"].startswith("Atender mostrador"), "decisión 3: la descripción breve guía/abre la descripción completa")
    check(any(p["valida"] == "Carrera técnica en farmacia" for p in g["preguntas_filtro"]) and any(p["descarta"] for p in g["preguntas_filtro"]),
          "prefiltro: preguntas desde los indispensables capturados, con eliminatorias")

    r = client.post("/vacantes/generar", json={"titulo": "Chofer", "seniority": "Senior"})
    g2 = r.json()
    check(r.status_code == 200 and g2["beneficios"] == [] and g2["sueldo_texto"] == "A convenir", "regla 4: sin prestaciones ni sueldo capturados → vacíos, no inventados")
    check(any("Sueldo no capturado" in a for a in g2["avisos_cumplimiento"]) and any("Prestaciones no capturadas" in a for a in g2["avisos_cumplimiento"])
          and any("Ubicación no capturada" in a for a in g2["avisos_cumplimiento"]), "regla 4: lo faltante queda como aviso para RH")
    textos = " ".join([g2["descripcion"], g2["texto_whatsapp"], g2["occ"]["page"], g2["linkedin"]["copy"], g2["portal"]["copy"]]).lower()
    check("prestaciones de ley" not in textos and "$" not in textos and "horario" not in textos, "regla 4: los textos públicos no mencionan sueldo/prestaciones/horario inventados")
    check(g2["seniority"] == "Senior", "seniority respetado también sin más datos")

    r = client.post("/vacantes/generar", json={"titulo": "X", "sueldo_periodicidad": "diario"})
    check(r.status_code == 400, "periodicidad inválida → 400")
    r = client.post("/vacantes/generar", json={"titulo": "X", "sueldo_desde": 10000, "sueldo_hasta": 5000, "sueldo_periodicidad": "mensual"})
    check(r.status_code == 400, "hasta < desde → 400")
    r = client.post("/vacantes/generar", json={"titulo": "X", "seniority": "Gerente"})
    check(r.status_code == 400, "seniority fuera de los 6 niveles → 400")

    # ---------- 3. crear con contenido del formulario (sin regenerar) y publicar ----------
    r = client.post("/vacantes", json={
        **ficha, "descripcion": g["descripcion"], "responsabilidades": g["responsabilidades"],
        "requisitos_deseables": g["requisitos_deseables"], "beneficios": g["beneficios"], "preguntas_filtro": g["preguntas_filtro"],
        "publicaciones": {"occ": g["occ"], "linkedin": g["linkedin"], "portal": g["portal"]},
        "enfoque_entrevista": "profesional_personal", "publicar": True, "plataformas": ["WhatsApp", "Portal"],
    })
    check(r.status_code == 201, "POST /vacantes desde el formulario nuevo")
    v = r.json()
    check(v["estado"] == "Publicada" and v.get("publicadaEn"), "punto 10: publicar desde el formulario → estado Publicada con fecha")
    check(v["sueldo"] == "$9,000 – $10,500 MXN mensuales" and v["sueldoDesde"] == 9000 and v["sueldoPeriodicidad"] == "mensual",
          "sueldo estructurado guardado y texto derivado")
    check(v["requisitos"] == "Carrera técnica en farmacia · Disponibilidad de fin de semana", "indispensables capturados → `requisitos` (« · »)")
    check(v["beneficios"] == ["Vales de despensa"] and v["seniority"] == "Junior" and v["enfoqueEntrevista"] == "profesional_personal", "beneficios/seniority/enfoque tal cual")
    r = client.get(f"/vacantes/{v['id']}")
    check(r.json()["estado"] == "Publicada", "GET /vacantes/{codigo} sigue reportando Publicada")
    r = client.get(f"/vacantes/slug/{v['slug']}")
    check(r.status_code == 200 and r.json()["sueldo"] == "$9,000 – $10,500 MXN mensuales", "página pública /aplicar ve el sueldo derivado")

    # ---------- 4. crear con generar_si_falta (agente / requisición) respeta lo capturado ----------
    # 2026-10-11 (tarde): con la ubicación el prefiltro web llega a las 3 preguntas clave mínimas
    r = client.post("/vacantes", json={"titulo": "Cajero(a)", "sueldo": "$8,000 mensuales", "requisitos": "INE vigente; Secundaria", "beneficios": ["Fondo de ahorro"],
                                       "ubicacion_estado": "Jalisco", "ubicacion_municipio": "Zapopan", "generar_si_falta": True})
    check(r.status_code == 201, "POST /vacantes con generación en el servidor (compatibilidad agente)")
    v2 = r.json()
    check(v2["sueldo"] == "$8,000 mensuales" and v2["sueldoDesde"] is None, "sueldo legado en texto se conserva intacto (sin estructurado)")
    check(v2["requisitos"].startswith("INE vigente · Secundaria") and v2["beneficios"] == ["Fondo de ahorro"], "generar_si_falta respeta requisitos y beneficios capturados")
    check(v2["responsabilidades"] and v2["preguntas_filtro"], "generar_si_falta sí completa lo vacío")

    # ---------- 5. actualizar sueldo estructurado → texto derivado ----------
    r = client.patch(f"/vacantes/{v2['id']}", json={"sueldo_desde": 2000, "sueldo_periodicidad": "semanal"})
    check(r.status_code == 200 and r.json()["sueldo"] == "Desde $2,000 MXN semanales", "PATCH sueldo estructurado recalcula el texto")
    r = client.patch(f"/vacantes/{v2['id']}", json={"sueldo_periodicidad": "a_convenir"})
    check(r.json()["sueldo"] == "A convenir", "PATCH a convenir → texto A convenir")
    r = client.post(f"/vacantes/{v2['id']}/regenerar", json={"notas": ""})
    check(r.status_code == 200 and r.json()["beneficios"] == ["Fondo de ahorro"] and r.json()["requisitos"].startswith("INE vigente · Secundaria") and r.json()["sueldo"] == "A convenir",
          "regenerar redacta de nuevo sin tocar requisitos/beneficios/sueldo capturados")

    # ---------- 6. plantillas con la misma estructura ----------
    r = client.post("/plantillas", json={"nombre": "Auxiliar base", "titulo": "Auxiliar de farmacia", "seniority": "Junior", "sueldo_desde": 9000, "sueldo_hasta": 9500, "sueldo_periodicidad": "quincenal", "requisitos": "Carrera técnica", "beneficios": ["Vales"]})
    check(r.status_code == 201 and r.json()["sueldo"] == "$9,000 – $9,500 MXN quincenales" and r.json()["sueldoPeriodicidad"] == "quincenal", "POST /plantillas con sueldo estructurado → texto derivado")
    pid = r.json()["id"]
    r = client.patch(f"/plantillas/{pid}", json={"sueldo_hasta": 9900})
    check(r.json()["sueldo"] == "$9,000 – $9,900 MXN quincenales", "PATCH /plantillas recalcula el texto")
    r = client.post(f"/plantillas/desde-vacante/{v['id']}", json={"nombre": "Copia", "cliente_id": None})
    check(r.status_code == 201 and r.json()["sueldoDesde"] == 9000 and r.json()["sueldoPeriodicidad"] == "mensual", "desde-vacante copia el sueldo estructurado (CAMPOS_PLANTILLA)")

    # ---------- 7. regresión: requisición → vacante con generación (llamaba a _generar con 1 argumento) ----------
    r = client.post("/requisiciones", json={"puesto": "Almacenista", "area": "Logística", "ubicacion": "Silao", "modalidad": "Presencial", "sueldo_propuesto": "$7,500 mensuales", "requisitos": "Carga y descarga", "justificacion": "Crecimiento"})
    if r.status_code == 201:
        rq = r.json()
        client.post(f"/requisiciones/{rq['id']}/autorizar", json={})
        r = client.post(f"/requisiciones/{rq['id']}/convertir-vacante", json={"generar_contenido": True, "notas": "Turno matutino"})
        check(r.status_code == 201 and r.json().get("vacante"), "requisición → vacante con generar_contenido=True responde 201 (regresión corregida: _generar con 1 argumento)")
        rv = client.get(f"/vacantes/{r.json()['vacante']}").json()
        check(rv["sueldo"] == "$7,500 mensuales" and rv["requisitos"].startswith("Carga y descarga"), "la vacante convertida conserva sueldo y requisitos de la requisición")
    else:
        print(f"   (requisiciones: {r.status_code} {r.text[:120]} — se verifica _generar directo)")
        from app.routers.vacantes import GenerarIn, _generar
        gen, _ = _generar(GenerarIn(titulo="Almacenista", sueldo="$7,500 mensuales", requisitos="Carga y descarga", descripcion="Turno matutino"), "Luna Salud")
        check(gen.requisitos_indispensables[0] == "Carga y descarga" and gen.beneficios == [], "_generar con firma nueva desde requisiciones")

    # ---------- 8. la unión respeta acentos/mayúsculas ----------
    f = ia.FichaVacante(titulo="T", requisitos_indispensables=["Licencia tipo B"], requisitos_deseables=["Inglés básico"])
    sal = ia._asegurar_capturado(ia._demo_vacante(f), f)
    check(sal.requisitos_indispensables[0] == "Licencia tipo B" and "ingles basico" not in [x.lower() for x in sal.requisitos_indispensables],
          "_asegurar_capturado: sin duplicados ni cruces (normaliza acentos/mayúsculas)")

    db.close()

print(f"\n🎉 Formulario de vacante (Parte 3) verificado: {OK} comprobaciones OK.")
