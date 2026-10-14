"""Regresión de Google Empleos (2026-09-26): GET /vacantes/slug/{slug}/jobposting.

404 si la vacante no existe, no está Publicada, su Cuenta no está activa o no trae «Google Empleos» en
`plataformas`; JSON-LD `JobPosting` con empresa por la regla única, ubicación estructurada y
`baseSalary` solo con sueldo capturado (quincenal → MONTH ×2). Base desechable.

    .venv/Scripts/python.exe scripts/verificar_google_empleos.py
"""
import os, sys, tempfile
from pathlib import Path
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
d = _temporal.directorio("rh_google_empleos_"); os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(d)/"p.db").replace("\\","/")
for k in ("OPENAI_API_KEY","WHATSAPP_PROVIDER","META_WHATSAPP_TOKEN","ANAM_API_KEY","RESEND_API_KEY"): os.environ[k] = ""
os.environ["ADMIN_PASSWORD"]="x"; os.environ["SEMBRAR_DEMO"]="false"
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import Cuenta, Cliente, Vacante
ok=0
def check(c,m):
    global ok
    print(("✅ " if c else "❌ ")+m); ok+=bool(c)
    if not c: sys.exit(1)
with TestClient(app) as cl:
    db=SessionLocal()
    cu=Cuenta(nombre="Interna", nombre_comercial="Reclutadora X", estado="Activa"); db.add(cu); db.flush()
    cli=Cliente(cuenta_id=cu.id, nombre="Banco Interno", nombre_comercial="Banco Visible"); db.add(cli); db.flush()
    def vac(slug, **kw):
        base=dict(codigo=slug.upper()[:20], slug=slug, titulo="Cajero <de> sucursal", cuenta_id=cu.id, cliente_id=cli.id,
                  mostrar_cliente_candidato=True, estado="Publicada", plataformas=["Portal","Google Empleos"],
                  resumen="Atender caja.\nOrientar clientes & usuarios.", responsabilidades=["Cuadrar caja","Atender filas"],
                  requisitos="Manejo de efectivo · Atención al cliente", ubicacion_estado="Ciudad de México",
                  ubicacion_municipio="Benito Juárez", modalidad="Presencial", sueldo_desde=12000, sueldo_hasta=15500,
                  sueldo_moneda="MXN", sueldo_periodicidad="mensual")
        base.update(kw); v=Vacante(**base); db.add(v); db.commit(); return v
    vac("ok"); vac("borrador", estado="Borrador"); vac("cerrada", estado="Cerrada"); vac("singoogle", plataformas=["Portal","Jooble"])
    vac("quincenal", sueldo_periodicidad="quincenal"); vac("semanal", sueldo_periodicidad="semanal", sueldo_desde=3000, sueldo_hasta=None)
    vac("anual", sueldo_periodicidad="anual"); vac("convenir", sueldo_periodicidad="a_convenir"); vac("sinmonto", sueldo_desde=None, sueldo_hasta=None)
    vac("sinperiodo", sueldo_periodicidad=""); vac("oculto", mostrar_cliente_candidato=False); vac("remoto", modalidad="Remoto")
    vac("legacy", publicada_en=None)
    j=cl.get("/vacantes/slug/ok/jobposting")
    check(j.status_code==200, "vacante publicada con Google Empleos → 200 sin sesión")
    j=j.json()
    check(j["@context"]=="https://schema.org/" and j["@type"]=="JobPosting" and j["title"]=="Cajero <de> sucursal", "@context, @type y title")
    check("<li>Cuadrar caja</li>" in j["description"] and "<li>Manejo de efectivo</li>" in j["description"] and "&amp;" in j["description"] and "<de>" not in j["description"], "description HTML: resumen + responsabilidades + requisitos, escapado")
    check(j["datePosted"] and j["hiringOrganization"]=={"@type":"Organization","name":"Banco Visible"}, "datePosted y empresa = Cliente visible")
    check(j["jobLocation"]["address"]=={"@type":"PostalAddress","addressCountry":"MX","addressLocality":"Benito Juárez","addressRegion":"Ciudad de México"}, "jobLocation con municipio, estado y MX")
    check(j["baseSalary"]=={"@type":"MonetaryAmount","currency":"MXN","value":{"@type":"QuantitativeValue","minValue":12000,"maxValue":15500,"unitText":"MONTH"}}, "baseSalary mensual")
    for s in ("borrador","cerrada","singoogle","noexiste"):
        check(cl.get(f"/vacantes/slug/{s}/jobposting").status_code==404, f"{s} → 404")
    g=lambda s: cl.get(f"/vacantes/slug/{s}/jobposting").json()
    check(g("quincenal")["baseSalary"]["value"]=={"@type":"QuantitativeValue","minValue":24000,"maxValue":31000,"unitText":"MONTH"}, "quincenal → MONTH ×2")
    check(g("semanal")["baseSalary"]["value"]=={"@type":"QuantitativeValue","value":3000,"unitText":"WEEK"}, "semanal con un solo monto → value/WEEK")
    check(g("anual")["baseSalary"]["value"]["unitText"]=="YEAR", "anual → YEAR")
    check(all("baseSalary" not in g(s) for s in ("convenir","sinmonto","sinperiodo")), "a convenir / sin montos / sin periodicidad → sin baseSalary")
    check(g("oculto")["hiringOrganization"]["name"]=="Reclutadora X", "sin «mostrar cliente» → nombre comercial de la Cuenta")
    r=g("remoto"); check(r["jobLocationType"]=="TELECOMMUTE" and r["applicantLocationRequirements"]["name"]=="MX", "Remoto → TELECOMMUTE")
    check(g("legacy")["datePosted"], "publicada antes de Fase C (sin publicada_en) → fecha de creación")
    cu.estado="Inactiva"; db.commit()
    check(cl.get("/vacantes/slug/ok/jobposting").status_code==404, "Cuenta inactiva → 404")
print(f"🎉 Google Empleos (JobPosting) verificado: {ok} comprobaciones OK.")
