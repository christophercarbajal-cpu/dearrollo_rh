"""Verificación BLOQUE 1/2 (2026-09-19): correos HTML en todos los eventos + vacante publicada, recordatorio
automático de Entrevista Humana, liga del evaluador con expediente, autocierre al evaluar y resultado
con comentarios opcionales (sobre Evaluaciones unificadas desde 2026-09-29). Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_bloque1_entrevista.py
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_b1_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "b1.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-b1"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Archivo, Cliente, ClienteContacto, Cuenta, Evaluacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import notificaciones as sn  # noqa: E402
from app.services.notificaciones import TZ_MEXICO  # noqa: E402
from app.services import recordatorios_entrevista as sre  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
CORREOS = []
WA = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _fake_correo(destino, asunto, html):
    CORREOS.append((destino, asunto, html))
    return {"enviado": True, "proveedor": "resend", "detalle": 200}


async def _fake_wa(tel, texto):
    WA.append((tel, texto))
    return {"enviado": True, "proveedor": "meta", "detalle": 200}


sn.enviar_correo = _fake_correo
sn.enviar_mensaje = _fake_wa
import app.services.correo as _scorreo  # noqa: E402

_scorreo.enviar_correo = _fake_correo  # el aviso a RH de un resultado importa enviar_correo al momento

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    admin.telefono = "3399998888"
    cuenta = Cuenta(nombre="Grupo CARBE", nombre_comercial="Grupo Carbe", estado="Activa", correo_comunicacion="rh@carbe.mx")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    cliente = Cliente(cuenta_id=cuenta.id, nombre="Tiendas Sol", nombre_comercial="Sol Retail", estado="Activo")
    db.add(cliente)
    db.flush()
    db.add(ClienteContacto(cliente_id=cliente.id, nombre="Paola", apellidos="Ruiz", puesto="Gerente", correo="paola@sol.mx", telefono=""))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    cfg = obtener(db)
    cfg.recordatorio_entrevista_horas = 24
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ================= 1. Vacante publicada → correo HTML =================
    print("\n--- 1. Vacante publicada (HTML al Cliente y responsable) ---")
    CORREOS.clear()
    r = client.post("/vacantes", json={"titulo": "Abogado Fiscalista", "descripcion": "Especialista en auditorías del SAT.", "generar_si_falta": False, "publicar": False,
                                       "ubicacion_estado": "Jalisco", "ubicacion_municipio": "Zapopan", "sueldo_desde": 25000, "sueldo_hasta": 30000, "sueldo_periodicidad": "mensual", "cliente_id": cliente.id, "responsable_id": admin.id})
    VAC = r.json()["id"]
    r = client.post(f"/vacantes/{VAC}/publicar", json={"plataformas": ["Portal"]})
    check(r.status_code == 200 and r.json()["estado"] == "Publicada" and "notificaciones" in r.json(), "publicar responde con las notificaciones")
    destinos = {c[0]: c for c in CORREOS}
    check("paola@sol.mx" in destinos and admin.correo in destinos, "correo al contacto del Cliente y al responsable")
    asunto, html = destinos["paola@sol.mx"][1], destinos["paola@sol.mx"][2]
    check(asunto == "Vacante publicada: Abogado Fiscalista" and "<!doctype html>" in html.lower() and "Ver la vacante" in html and "/aplicar/" in html and "auditorías del SAT" in html, "HTML corporativo con descripción y CTA a la vacante")

    # ================= 2. Entrevista humana: HTML en todos los eventos + liga con expediente =================
    print("\n--- 2. Liga del entrevistador con expediente completo ---")
    # 2026-09-23: la cita se calcula SIEMPRE 5 h en el futuro EN HORA DE MÉXICO (que es como la captura
    # RH). Antes era la fecha de «dentro de 5 h» con la hora fija 10:00, así que la prueba fallaba sola
    # al correrla después de las 10:00 de México: la cita quedaba en el pasado y el job no mandaba nada.
    _cita = datetime.now(TZ_MEXICO) + timedelta(hours=5)
    _cita_en_5h = (_cita.strftime("%Y-%m-%d"), _cita.strftime("%H:%M"))
    r = client.post("/candidatos", json={"nombre": "Carlos Hernández", "telefono": "5512345678", "correo": "carlos@correo.mx", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    from app.models import Postulacion  # noqa: E402
    p = db.query(Postulacion).filter(Postulacion.codigo == P).one()
    p.analisis = {"requisitos_cumplidos": ["Título en Derecho"], "brechas": ["Sin experiencia en SAT"], "fortalezas_cv": ["Comunicación"], "resumen": "Perfil sólido."}
    p.score = 78
    p.candidato.cv_datos = {"resumen_profesional": "Abogado con 5 años de experiencia.", "habilidades": ["Fiscal", "Litigio"], "estudios": ["Lic. Derecho"], "idiomas": ["Inglés"]}
    db.add(Archivo(candidato_id=p.candidato_id, tipo="cv", nombre="cv.pdf", ruta="", mime="application/pdf"))
    db.commit()
    client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Evaluación", "manual": True})
    CORREOS.clear()
    # Evaluaciones unificadas (2026-09-29): «Agregar evaluación» → entrevista humana asignada con cita
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "evaluador": {"tipo": "interno", "usuario_id": admin.id},
        "cita": {"fecha": _cita_en_5h[0], "hora": _cita_en_5h[1], "modalidad": "Teléfono"},
        "notificar": {"cliente_correo": False, "cliente_whatsapp": False},
    })
    check(r.status_code == 201, "entrevista humana programada")
    COD = r.json()["evaluacion"]["codigo"]
    db.expire_all()
    ev = db.query(Evaluacion).filter_by(codigo=COD).one()
    token = ev.token_evaluador
    r = client.get(f"/evaluaciones/publica/{token}")
    check(r.status_code == 200 and r.json()["expediente"], "GET público trae el expediente")
    exp = r.json()["expediente"]
    check(exp["candidato"]["nombre"] == "Carlos Hernández" and exp["score"] == 78 and exp["cv"]["resumen"].startswith("Abogado") and exp["cv"]["habilidades"] == ["Fiscal", "Litigio"], "CV extraído y afinidad de Luna")
    check(exp["analisis"]["brechas"] == ["Sin experiencia en SAT"] and exp["analisis"]["requisitosCumplidos"] == ["Título en Derecho"], "análisis de Luna (requisitos, brechas)")
    check(exp["archivos"] and exp["archivos"][0]["tipo"] == "cv" and exp["vacante"]["titulo"] == "Abogado Fiscalista" and exp["vacante"]["empresa"], "archivos del candidato y vacante/empresa")
    check(r.json()["yaTieneResultado"] is False, "todavía sin resultado")

    # ================= 3. Recordatorio automático =================
    print("\n--- 3. Recordatorio automático de la cita (job) ---")
    CORREOS.clear(); WA.clear()
    n = asyncio.run(sre.revisar_recordatorios_entrevista())
    check(n == 1, "cita dentro de las próximas 24 h → 1 recordatorio")
    check(any(c[0] == "carlos@correo.mx" and "Recordatorio" in c[1] and "<!doctype html>" in c[2].lower() for c in CORREOS), "candidato: correo HTML de recordatorio")
    check(any(c[0] == admin.correo and "Recordatorio" in c[1] for c in CORREOS) and any(t[0] == "3399998888" for t in WA), "evaluador: correo + WhatsApp")
    check(asyncio.run(sre.revisar_recordatorios_entrevista()) == 0, "no se repite (recordatorio_enviado_en)")
    db.expire_all()
    check(db.query(Evaluacion).filter_by(codigo=COD).one().recordatorio_enviado_en is not None, "queda marcado en la evaluación")
    cfg = obtener(db); cfg.recordatorio_entrevista_horas = 0; db.commit()
    check(asyncio.run(sre.revisar_recordatorios_entrevista()) == 0, "con 0 horas el job está apagado")
    cfg.recordatorio_entrevista_horas = 24; db.commit()

    # ================= 4. Liga del evaluador: reenviar + resultado =================
    print("\n--- 4. Resultado desde la liga del evaluador ---")
    CORREOS.clear()
    r = client.post(f"/evaluaciones/{COD}/reenviar-liga")
    check(r.status_code == 200, "reenviar liga al evaluador")
    c_e = next((c for c in CORREOS if c[0] == admin.correo), None)
    check(c_e is not None and "<!doctype html>" in c_e[2].lower() and f"/evaluacion/{token}" in c_e[2], "correo HTML corporativo con la liga del evaluador")
    check(not any(c[0] == "carlos@correo.mx" for c in CORREOS), "reenviar la liga no le escribe al candidato")
    CORREOS.clear(); WA.clear()
    etapa_antes = db.query(Postulacion).filter(Postulacion.codigo == P).one().etapa
    r = client.post(f"/evaluaciones/publica/{token}/resultado", data={"conclusion": "no_avanzar", "version": "0"})
    check(r.status_code == 200 and r.json()["evaluacion"]["estado"] == "con_resultado", "resultado desde la liga sin comentario (opcional) → Con resultado")
    db.expire_all()
    ev = db.query(Evaluacion).filter_by(codigo=COD).one()
    check(ev.realizada_en is not None and ev.registrada_via == "liga_evaluador" and ev.conclusion == "no_avanzar", "queda realizada y con resultado (autocierre), capturado vía liga")
    check(any(c[0] == admin.correo and "Nuevo resultado" in c[1] and "No avanzar" in c[2] for c in CORREOS), "RH (responsable): correo HTML con el resultado")
    check(not any(c[0] in ("carlos@correo.mx", "paola@sol.mx") for c in CORREOS) and not WA, "el candidato y el Cliente NO reciben nada por un resultado (especificación, sección 7)")
    check(db.query(Postulacion).filter(Postulacion.codigo == P).one().etapa == etapa_antes, "el resultado no mueve la etapa")
    r = client.get(f"/evaluaciones/publica/{token}")
    check(r.status_code == 200 and r.json()["yaTieneResultado"] is True and r.json()["expediente"], "la liga sigue mostrando el expediente con resultado (solo lectura)")
    r = client.post(f"/evaluaciones/publica/{token}/resultado", data={"conclusion": "avanzar", "version": str(ev.resultado_version)})
    db.expire_all()
    check(r.status_code == 400 and db.query(Evaluacion).filter_by(codigo=COD).one().conclusion == "no_avanzar",
          "…un segundo envío no sobrescribe: solo acepta complemento (comentario o adjunto)")
    check(all("<!doctype html>" in c[2].lower() for c in CORREOS), "cero texto plano en los correos")

    # ================= 5. RH: resultado con comentario opcional =================
    print("\n--- 5. RH registra resultado en un solo paso ---")
    r = client.post("/candidatos", json={"nombre": "Ana Ruiz", "telefono": "5599990000", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P2 = r.json()["id"]
    client.patch(f"/candidatos/{P2}/etapa", json={"etapa": "Evaluación", "manual": True})
    r = client.post(f"/evaluaciones/postulaciones/{P2}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "evaluador": {"tipo": "interno", "usuario_id": admin.id},
        "cita": {"fecha": "2026-10-01", "hora": "10:00", "modalidad": "Teléfono"}, "notificar": {"cliente_correo": False, "cliente_whatsapp": False},
    })
    COD2 = r.json()["evaluacion"]["codigo"]
    r = client.post(f"/evaluaciones/{COD2}/resultado", data={"conclusion": "no_avanzar", "version": "0"})
    e2 = r.json()["evaluacion"]
    check(r.status_code == 200 and e2["estado"] == "con_resultado" and e2["realizadaEn"] and e2["conclusion"] == "no_avanzar",
          "«Registrar resultado» sin comentario → realizada y con resultado en un paso")

    db.close()

print(f"\n🎉 Bloque 1/2 verificado: {OK} comprobaciones OK.")
