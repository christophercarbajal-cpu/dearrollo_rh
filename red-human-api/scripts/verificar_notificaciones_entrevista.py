"""Verificación (2026-09-18; sobre Evaluaciones unificadas desde 2026-09-29): notificaciones automatizadas de Entrevista Humana — WhatsApp (plantilla de Meta
«alerta_entrevista_asignada» con 6 parámetros posicionales al entrevistador) y correo (Resend, plantillas
HTML corporativas para entrevistador y candidato) + vistas previas. Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_notificaciones_entrevista.py
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_notif_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "notif.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-notif"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import correo as scorreo  # noqa: E402
from app.services import notificaciones as sn  # noqa: E402
from app.services import whatsapp as swa  # noqa: E402

OK = 0
WA_PLANTILLA = []   # (telefono, plantilla, parametros)
WA_TEXTO = []       # (telefono, texto)
CORREOS = []        # (destino, asunto, html)


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


PLANTILLA_OK = {"ok": True}


async def _fake_plantilla(telefono, plantilla, parametros=None, idioma=None):
    WA_PLANTILLA.append((telefono, plantilla, list(parametros or [])))
    return {"enviado": PLANTILLA_OK["ok"], "proveedor": "meta", "detalle": "ok" if PLANTILLA_OK["ok"] else "(#132001) Template name does not exist"}


async def _fake_texto(telefono, texto):
    WA_TEXTO.append((telefono, texto))
    return {"enviado": True, "proveedor": "meta", "detalle": "ok"}


async def _fake_correo(destino, asunto, html):
    CORREOS.append((destino, asunto, html))
    return {"enviado": True, "proveedor": "resend", "detalle": 200}


swa.enviar_plantilla = _fake_plantilla
swa.enviar_mensaje = _fake_texto
sn.enviar_mensaje = _fake_texto
sn.enviar_correo = _fake_correo
settings.whatsapp_provider = "meta"  # para que la ruta de plantilla se active (el POST real está simulado)

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    admin.telefono = "3399998888"
    cuenta = Cuenta(nombre="Grupo CARBE", nombre_comercial="Grupo CARBE", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ================= 1. Vistas previas =================
    print("\n--- 1. Vistas previas de correo ---")
    r = client.get("/api/emails/preview/entrevistador")
    check(r.status_code == 200 and r.headers["content-type"].startswith("text/html") and "Ver expediente del candidato" in r.text, "GET /api/emails/preview/entrevistador renderiza HTML con el CTA")
    check("Red</span><span" in r.text and "Human</span>" in r.text and 'max-width: 620px' in r.text, "logo Red Human + CSS responsivo")
    r = client.get("/api/emails/preview/candidato")
    check(r.status_code == 200 and "Unirme a la entrevista" in r.text and "10:30 h" in r.text and "teams.microsoft.com" in r.text, "GET /api/emails/preview/candidato con fecha, hora y liga de conexión")
    r = client.get("/api/emails/preview/candidato?modalidad=Presencial")
    check("Reforma 222" in r.text and "Unirme a la entrevista" not in r.text, "variante presencial (lugar en vez de liga)")
    r = client.get("/api/emails/preview/entrevistador?json=1").json()
    check(r["parametros_meta"] == ["Mariana López", "Carlos Hernández Ruiz", "Abogado Fiscalista", "jueves 24 de septiembre de 2026", "10:30 h (hora de Ciudad de México)", r["datos"]["liga_expediente"]], "orden de los 6 parámetros de Meta en la vista previa (hora con su zona, 2026-09-29)")

    # ================= 2. Disparo al asignar Entrevista Humana =================
    print("\n--- 2. Asignar Entrevista Humana → WhatsApp (plantilla) + correos ---")
    vac = client.get("/vacantes").json()[0]
    r = client.post("/candidatos", json={"nombre": "Carlos Hernández", "telefono": "5512345678", "correo": "carlos@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Evaluación", "manual": True})
    WA_PLANTILLA.clear(); WA_TEXTO.clear(); CORREOS.clear()
    # Evaluaciones unificadas (2026-09-29): «Agregar evaluación» → entrevista humana asignada con cita
    SIN_CLIENTE = {"cliente_correo": False, "cliente_whatsapp": False}
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "evaluador": {"tipo": "interno", "usuario_id": admin.id},
        "instrucciones": "Validar experiencia con auditorías.",
        "cita": {"fecha": "2026-09-24", "hora": "10:30", "modalidad": "Videollamada", "liga_videollamada": "https://teams.microsoft.com/l/meetup-join/abc"},
        "notificar": SIN_CLIENTE,
    })
    check(r.status_code == 201, f"entrevista humana programada ({r.status_code})")
    COD = r.json()["evaluacion"]["codigo"]
    res = {(x["destinatario"], x["canal"]): x for x in r.json()["resultados"]}
    check(len(WA_PLANTILLA) == 1 and WA_PLANTILLA[0][1] == "alerta_entrevista_asignada" and WA_PLANTILLA[0][0] == "3399998888", "al entrevistador le llega la plantilla de Meta «alerta_entrevista_asignada» a su WhatsApp del perfil")
    params = WA_PLANTILLA[0][2]
    check(len(params) == 6, "exactamente 6 parámetros posicionales")
    check(params[0] == admin.nombre and params[1] == "Carlos Hernández" and params[2] == vac["titulo"], "1 entrevistador · 2 candidato · 3 vacante")
    check("2026" in params[3] and "septiembre" in params[3] and params[4].endswith(" h (hora de Ciudad de México)"), f"4 fecha «{params[3]}» · 5 hora «{params[4]}» (hora de México)")
    check(params[5].startswith(settings.app_url + "/evaluacion/") and len(params[5]) > len(settings.app_url) + 25, "6 liga al expediente del candidato (liga del evaluador)")
    check(res[("entrevistador", "whatsapp")]["enviado"] is True and "plantilla" in res[("entrevistador", "whatsapp")], "resultado visible para RH: plantilla enviada")
    check(any(t[0] == "5512345678" for t in WA_TEXTO), "el candidato recibe su WhatsApp de texto (aviso de entrevista)")
    correos = {c[0]: c for c in CORREOS}
    check(admin.correo in correos and "carlos@correo.mx" in correos, "salen los dos correos: entrevistador y candidato")
    asunto_e, html_e = correos[admin.correo][1], correos[admin.correo][2]
    check(asunto_e.startswith("Nueva entrevista asignada: Carlos Hernández") and "Ver expediente del candidato" in html_e and params[5] in html_e, "correo del entrevistador: plantilla corporativa con CTA al expediente")
    check("Validar experiencia" in html_e and "Red</span>" in html_e and "<!doctype html>" in html_e.lower(), "…con nota de RH y logo Red Human")
    asunto_c, html_c = correos["carlos@correo.mx"][1], correos["carlos@correo.mx"][2]
    check("quedó agendada" in asunto_c and "https://teams.microsoft.com/l/meetup-join/abc" in html_c and "10:30 h" in html_c and "septiembre" in html_c, "correo del candidato: fecha, hora y liga de conexión (Teams)")
    check("Grupo CARBE" in html_c and "Unirme a la entrevista" in html_c, "…con la empresa visible y botón de unirse")

    # ================= 2b. Mismo layout para modificada / recordatorio / cancelada =================
    print("\n--- 2b. Modificada, recordatorio y cancelada con el layout corporativo ---")
    CORREOS.clear()
    r = client.patch(f"/evaluaciones/{COD}", json={"cita": {"fecha": "2026-09-25", "hora": "12:00", "modalidad": "Videollamada", "liga_videollamada": "https://teams.microsoft.com/l/meetup-join/abc"},
                                                  "notificar": {"candidato_correo": True, "entrevistador_correo": True, **SIN_CLIENTE}})
    check(r.status_code == 200, f"reprogramar entrevista ({r.status_code})")
    cm = {c[0]: c for c in CORREOS}
    check("modificada" in cm[admin.correo][1].lower() and "<!doctype html>" in cm[admin.correo][2].lower() and "Ver expediente del candidato" in cm[admin.correo][2] and "25 de septiembre" in cm[admin.correo][2], "entrevistador · modificada: HTML corporativo con la nueva fecha y CTA")
    check("modificada" in cm["carlos@correo.mx"][1].lower() and "12:00 h" in cm["carlos@correo.mx"][2] and "Unirme a la entrevista" in cm["carlos@correo.mx"][2], "candidato · modificada: HTML con nueva hora y liga")
    CORREOS.clear()
    r = client.post(f"/evaluaciones/{COD}/recordatorio", json={"a": "ambos", "notificar": {"candidato_correo": True, "entrevistador_correo": True, **SIN_CLIENTE}})
    check(r.status_code == 200, f"recordatorio ({r.status_code})")
    cm = {c[0]: c for c in CORREOS}
    check(admin.correo in cm and "Recordatorio" in cm[admin.correo][1] and "<!doctype html>" in cm[admin.correo][2].lower(), "entrevistador · recordatorio: HTML corporativo")
    check("carlos@correo.mx" in cm and "Recordatorio" in cm["carlos@correo.mx"][1] and "se acerca" in cm["carlos@correo.mx"][2], "candidato · recordatorio: HTML corporativo")
    CORREOS.clear()
    r = client.post(f"/evaluaciones/{COD}/cancelar", json={"motivo": "Prueba", "notificar": {"candidato_correo": True, "entrevistador_correo": True, **SIN_CLIENTE}})
    check(r.status_code == 200, f"cancelar ({r.status_code})")
    cm = {c[0]: c for c in CORREOS}
    check(admin.correo in cm and "cancelada" in cm[admin.correo][1].lower() and "<!doctype html>" in cm[admin.correo][2].lower() and "Ver expediente" not in cm[admin.correo][2], "entrevistador · cancelada: HTML corporativo sin CTA")
    check("carlos@correo.mx" in cm and "cancelada" in cm["carlos@correo.mx"][1].lower() and "Unirme" not in cm["carlos@correo.mx"][2], "candidato · cancelada: HTML sin botón de unirse")
    check(all("<!doctype html>" in c[2].lower() for c in CORREOS), "ningún correo de entrevista sale en texto plano")

    # ================= 2c. Vista previa con datos reales =================
    print("\n--- 2c. Vista previa dinámica (query params) ---")
    r = client.get("/api/emails/preview/candidato?evento=modificada&candidato=Ana%20Ruiz&entrevistador=Luis%20P%C3%A9rez&vacante=Cajera&empresa=Grupo%20CARBE&fecha=2026-09-24&hora=10:30&modalidad=Videollamada&liga=https://meet.google.com/abc&json=1").json()
    check(r["asunto"] == "Tu entrevista para Cajera fue modificada" and r["datos"]["fecha"] == "jueves 24 de septiembre de 2026" and r["datos"]["hora"] == "10:30 h (hora de Ciudad de México)", "fecha ISO + hora del formulario → texto legible en México")
    check(r["datos"]["liga_conexion"] == "https://meet.google.com/abc" and r["datos"]["entrevistador"] == "Luis Pérez", "liga y entrevistador reales")
    r = client.get("/api/emails/preview/entrevistador?candidato=Ana%20Ruiz&vacante=Cajera&modalidad=Presencial&ubicacion=Reforma%20222&fecha=2026-09-24")
    check(r.status_code == 200 and "Ana Ruiz" in r.text and "Reforma 222" in r.text and "Mariana" not in r.text, "vista previa del entrevistador con datos reales (sin mock)")
    r = client.get("/api/emails/preview/entrevistador?evento=cancelada&candidato=Ana&vacante=Cajera")
    check("cancel" in r.text.lower() and "Ver expediente" not in r.text, "evento=cancelada en la vista previa")
    r = client.get("/api/emails/preview/entrevistador")
    check("Mariana" in r.text, "sin parámetros sigue el ejemplo de prueba")

    # ================= 3. Respaldo si la plantilla de Meta falla =================
    print("\n--- 3. Respaldos ---")
    PLANTILLA_OK["ok"] = False
    WA_PLANTILLA.clear(); WA_TEXTO.clear()
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "evaluador": {"tipo": "interno", "usuario_id": admin.id},
        "cita": {"fecha": "2026-09-25", "hora": "12:00", "modalidad": "Teléfono"}, "notificar": SIN_CLIENTE,
    })
    res = {(x["destinatario"], x["canal"]): x for x in r.json()["resultados"]}
    check(len(WA_PLANTILLA) == 1 and any(t[0] == "3399998888" and "/evaluacion/" in t[1] for t in WA_TEXTO), "si Meta rechaza la plantilla, sale texto libre al entrevistador con la liga (nunca silencio)")
    check("motivo_fallback" in res[("entrevistador", "whatsapp")], "el resultado explica el respaldo")
    PLANTILLA_OK["ok"] = True

    # correo sin clave: warning, sin excepción
    sn.enviar_correo = scorreo.enviar_correo
    settings.resend_api_key = ""
    r = asyncio.run(scorreo.enviar_correo("x@y.mx", "asunto", "<p>hola</p>"))
    check(r["enviado"] is False and "RESEND_API_KEY" in str(r["detalle"]), "sin RESEND_API_KEY: warning y no enviado, sin romper")
    settings.resend_api_key = "re_falsa"
    settings.resend_from = "Red Human AI <onboarding@resend.dev>"
    import httpx  # noqa: E402

    class _Cli:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): raise httpx.ConnectError("sin red")

    httpx_real = scorreo.httpx.AsyncClient
    scorreo.httpx.AsyncClient = _Cli
    r = asyncio.run(scorreo.enviar_correo("x@y.mx", "asunto", "<p>hola</p>"))
    scorreo.httpx.AsyncClient = httpx_real
    check(r["enviado"] is False and "sin red" in str(r["detalle"]), "sandbox + Resend caído: warning en log y resultado no enviado, sin excepción")
    settings.resend_api_key = ""

    db.close()

print(f"\n🎉 Notificaciones de Entrevista Humana verificadas: {OK} comprobaciones OK.")
