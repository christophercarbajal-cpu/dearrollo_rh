"""Regresión de los feeds XML de bolsas de empleo (2026-09-27): GET /feeds/jooble.xml y /feeds/talent.xml.

Públicos (sin sesión) y XML bien formado; solo entran vacantes Publicadas, de Cuenta activa y con el
portal marcado en `plataformas`; sin sueldo capturado no hay <salary>; empresa por la regla única; liga
a {app_url}/aplicar/{slug}; texto con <, & y comillas queda escapado. Base desechable.

    .venv/Scripts/python.exe scripts/verificar_feeds_bolsas.py
"""

import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_feeds_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "feeds.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-feeds"
os.environ["SEMBRAR_DEMO"] = "false"
os.environ["APP_URL"] = "https://app.ejemplo.mx"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cliente, Cuenta, Vacante  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


with TestClient(app) as cl:
    vacio = cl.get("/feeds/jooble.xml")
    check(vacio.status_code == 200 and ET.fromstring(vacio.content).tag == "jobs" and len(ET.fromstring(vacio.content)) == 0,
          "sin vacantes: feed vacío pero XML válido")

    db = SessionLocal()
    cu = Cuenta(nombre="Interna", nombre_comercial="Reclutadora X", estado="Activa")
    inactiva = Cuenta(nombre="Apagada", nombre_comercial="Apagada", estado="Inactiva")
    db.add_all([cu, inactiva])
    db.flush()
    cli = Cliente(cuenta_id=cu.id, nombre="Banco Interno", nombre_comercial="Banco Visible")
    db.add(cli)
    db.flush()

    def vac(codigo, **kw):
        base = dict(
            codigo=codigo, slug=codigo.lower(), titulo='Cajero <A> & "B"', cuenta_id=cu.id, cliente_id=cli.id,
            mostrar_cliente_candidato=True, estado="Publicada", plataformas=["Portal", "Jooble", "Talent.com"],
            resumen="Atender caja.", responsabilidades=["Cuadrar caja"], requisitos="Manejo de efectivo",
            ubicacion_estado="Puebla", ubicacion_municipio="Cholula", modalidad="Presencial",
            sueldo_desde=12000, sueldo_hasta=15000, sueldo_moneda="MXN", sueldo_periodicidad="mensual",
        )
        base.update(kw)
        db.add(Vacante(**base))

    vac("VAC-1")
    vac("VAC-2", plataformas=["Portal", "Jooble"], sueldo_periodicidad="a_convenir", mostrar_cliente_candidato=False)
    vac("VAC-3", plataformas=["Portal", "Talent.com"], sueldo_desde=None, sueldo_hasta=None, modalidad="Remoto")
    vac("VAC-BORRADOR", estado="Borrador")
    vac("VAC-CERRADA", estado="Cerrada")
    vac("VAC-GOOGLE", plataformas=["Portal", "Google Empleos"])
    vac("VAC-INACTIVA", cuenta_id=inactiva.id, cliente_id=None)
    db.commit()

    r = cl.get("/feeds/jooble.xml")
    check(r.status_code == 200 and r.headers["content-type"].startswith("application/xml"), "Jooble: 200 sin sesión, application/xml")
    jobs = {j.get("id"): j for j in ET.fromstring(r.content)}
    check(set(jobs) == {"VAC-1", "VAC-2"}, f"Jooble: solo Publicadas, Cuenta activa y con Jooble marcado ({sorted(jobs)})")
    j1 = jobs["VAC-1"]
    check(j1.findtext("link") == "https://app.ejemplo.mx/aplicar/vac-1?utm_source=jooble", "Jooble: <link> a la landing pública")
    check(j1.findtext("name") == 'Cajero <A> & "B"' and b"&lt;A&gt; &amp;" in r.content, "Jooble: texto especial escapado y legible")
    check(j1.findtext("company") == "Banco Visible" and jobs["VAC-2"].findtext("company") == "Reclutadora X",
          "Jooble: empresa según «mostrar cliente al candidato»")
    check(j1.findtext("region") == "Cholula, Puebla", "Jooble: <region> municipio, estado")
    check(j1.findtext("salary") == "$12,000 – $15,000 MXN mensuales" and jobs["VAC-2"].find("salary") is None,
          "Jooble: <salary> solo con sueldo capturado (a convenir → sin etiqueta)")
    check("<li>Cuadrar caja</li>" in j1.findtext("description") and j1.findtext("pubdate"), "Jooble: <description> HTML y <pubdate>")

    t = cl.get("/feeds/talent.xml")
    raiz = ET.fromstring(t.content)
    check(t.status_code == 200 and raiz.tag == "source" and raiz.findtext("publisherurl") == "https://app.ejemplo.mx",
          "Talent.com: 200 sin sesión, <source> con publisher")
    tjobs = {j.findtext("referencenumber"): j for j in raiz.findall("job")}
    check(set(tjobs) == {"VAC-1", "VAC-3"}, f"Talent.com: solo las que tienen Talent.com marcado ({sorted(tjobs)})")
    t1 = tjobs["VAC-1"]
    check((t1.findtext("city"), t1.findtext("state"), t1.findtext("country")) == ("Cholula", "Puebla", "MX"), "Talent.com: city/state/country MX")
    check(t1.findtext("url") == "https://app.ejemplo.mx/aplicar/vac-1?utm_source=talent" and t1.findtext("dateposted"), "Talent.com: <url> y <dateposted>")
    check(tjobs["VAC-3"].find("salary") is None and tjobs["VAC-3"].findtext("remotetype") == "Remoto", "Talent.com: sin sueldo → sin <salary>; remoto marcado")

    v = db.query(Vacante).filter_by(codigo="VAC-1").one()
    v.plataformas = ["Portal", "Talent.com"]
    db.commit()
    ids = {j.get("id") for j in ET.fromstring(cl.get("/feeds/jooble.xml").content)}
    check("VAC-1" not in ids, "desmarcar Jooble la saca del feed en la siguiente lectura")

print(f"\n🎉 Feeds XML (Jooble, Talent.com) verificados: {OK} comprobaciones OK.")
