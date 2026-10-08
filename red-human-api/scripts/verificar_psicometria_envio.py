"""Regresión del Bloque 1 (2026-10-08): IDs del proveedor, creación desacoplada del envío y «Error de envío».

    .venv/Scripts/python.exe scripts/verificar_psicometria_envio.py

Base desechable. Psicométricas.mx se SIMULA (httpx falso, sin red): NUNCA consume el saldo compartido con producción.
Cubre: catálogo oficial de IDs (Herrmann / IDs inventados → 400 SIN llamar), formato `Tests`, redirección 301 como error
claro; transacción 1 (creación) fallida → «No se pudo generar la prueba. Intenta nuevamente.», nada guardado y
bitácora `psicometria_alta_fallida`; transacción 2 (envío) fallida → la prueba queda con su clave y el bloque en
«Error de envío» con su liga real; «Reintentar envío» la deja «Enviada»; cascada de canales solo en demo-grupak.
"""

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

_dir = tempfile.mkdtemp(prefix="rh_psico_envio_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "envio.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "PSICOMETRICAS_TOKEN",
          "PSICOMETRICAS_PASSWORD", "PSICOMETRICAS_USUARIO", "PSICOMETRICAS_WEBHOOK_SECRET", "PSICOMETRICAS_URL_CANDIDATO",
          "TELEGRAM_BOT_TOKEN"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-psico-envio"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import usuario_actual, usuario_admin, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Bitacora, Cuenta, Evaluacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import correo as correo_srv  # noqa: E402
from app.services import psicometricas as psi  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


class R:
    def __init__(self, status, datos=None, headers=None):
        self.status_code, self._d, self.content, self.text = status, datos, b"", ""
        self.headers = headers or {}

    def json(self):
        if self._d is None:
            raise ValueError("sin json")
        return self._d


print("\n--- 1. Catálogo oficial de IDs (sin llamar al proveedor) ---")
check(psi.tests_de("1, 7, 1") == "1,7", "«1, 7, 1» → «1,7» (sin espacios ni repetidos)")
for malo, que in (("104", "Herrmann de ejemplo"), ("1,7,104", "batería con un ID inexistente"), ("abc", "texto"), ("", "vacío")):
    try:
        psi.tests_de(malo)
        check(False, f"{que} debió rechazarse")
    except psi.PsicometricasError as ex:
        check(ex.status == 400 and "Psicométricas" not in str(ex), f"{que} → 400 sin nombrar al proveedor")
check(psi.tests_de("7,15,1,10") == "7,15,1,10", "Batería Gerente con IDs reales (Terman, Moss, Cleaver, 16PF)")
try:
    psi._revisar(R(301, None, {"location": "https://admin.psicometricas.mx/api/agregaCandidato/"}))
    check(False, "un 301 debió ser error")
except psi.PsicometricasError as ex:
    check(ex.status == 301 and "redirección 301" in str(ex), "una redirección 301 se reporta clara (no se sigue)")

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuentas = {}
    for slug in ("demo-grupak", "otra-cuenta"):
        cu = Cuenta(nombre=slug, nombre_comercial=slug, razon_social=slug, estado="Activa", slug=slug)
        db.add(cu)
        db.flush()
        db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cu.id))
        cuentas[slug] = cu
    vacs = db.query(Vacante).filter(Vacante.estado == "Publicada").limit(2).all()
    vacs[0].cuenta_id, vacs[1].cuenta_id = cuentas["demo-grupak"].id, cuentas["otra-cuenta"].id
    for v in vacs:
        v.proceso = {}
    obtener(db).modo_prueba = False
    db.commit()
    for dep in (usuario_actual, usuario_decisor, usuario_admin):
        app.dependency_overrides[dep] = lambda: admin
    HG = {"X-Cuenta-Id": str(cuentas["demo-grupak"].id)}
    HO = {"X-Cuenta-Id": str(cuentas["otra-cuenta"].id)}

    def prueba(h, nombre, ids):
        return client.post("/evaluaciones/pruebas", headers=h, json={"nombre": nombre, "tipo": "bateria", "id_proveedor": ids,
                                                                      "activa": True, "modo": "integrada", "proveedor": "Psicométricas.mx"})

    def candidato(h, nombre, tel, correo, vac):
        return client.post("/candidatos", headers=h, json={"nombre": nombre, "telefono": tel, "correo": correo, "vacante": vac,
                                                             "consentimiento": True, "fuente": "RH"}).json()["id"]

    GER = prueba(HG, "Batería Gerente", "7,15,1,10").json()["id"]
    VIEJA = prueba(HG, "Batería Gerente (con Herrmann)", "7,15,1,104,10").json()["id"]
    OTRA = prueba(HO, "Batería Otra", "1,7").json()["id"]
    P1 = candidato(HG, "Jorge Gerente", "5512348001", "jorge@correo.mx", vacs[0].codigo)

    settings.psicometricas_token, settings.psicometricas_password = "T" * 20, "P" * 20
    LLAMADAS = []
    orig_post, orig_correo = psi.httpx.post, correo_srv.enviar_correo
    CORREO_OK = {"v": False}

    async def correo_falso(destino, asunto, html, **kw):
        return {"enviado": CORREO_OK["v"], "detalle": "" if CORREO_OK["v"] else "RESEND_API_KEY sin configurar"}

    correo_srv.enviar_correo = correo_falso
    try:
        print("\n--- 2. ID inexistente en la batería → 400 antes de llamar ---")
        psi.httpx.post = lambda url, data=None, timeout=None: (LLAMADAS.append(data), R(200, {"status": "200", "clave": "X"}))[1]
        r = client.post(f"/evaluaciones/postulaciones/{P1}/psicometria", headers=HG, json={"prueba_ids": [VIEJA]})
        check(r.status_code == 400 and "104" in r.json()["detail"] and not LLAMADAS, "batería con Herrmann (104) → 400 SIN llamar al proveedor")

        print("\n--- 3. Transacción 1 (creación) fallida ---")
        psi.httpx.post = lambda url, data=None, timeout=None: (LLAMADAS.append(data), R(301, None, {"location": "https://x/"}))[1]
        r = client.post(f"/evaluaciones/postulaciones/{P1}/psicometria", headers=HG, json={"prueba_ids": [GER]})
        check(r.status_code == 502 and r.json()["detail"] == "No se pudo generar la prueba. Intenta nuevamente.",
              "el proveedor falla → «No se pudo generar la prueba. Intenta nuevamente.»")
        check(LLAMADAS[-1]["Tests"] == "7,15,1,10", "el payload llevó los IDs reales de la batería")
        db.expire_all()
        check(db.query(Evaluacion).filter(Evaluacion.tipo == "psicometrica").count() == 0, "…y no quedó ninguna evaluación guardada")
        b = db.query(Bitacora).filter(Bitacora.accion == "psicometria_alta_fallida").first()
        check(b is not None and "301" in (b.detalle or {}).get("detalle", ""), "el detalle técnico queda en bitácora (`psicometria_alta_fallida`)")

        print("\n--- 4. Transacción 2 (envío) fallida → Error de envío ---")
        psi.httpx.post = lambda url, data=None, timeout=None: (LLAMADAS.append(data), R(200, {"status": "200", "clave": "1-JOR-0001"}))[1]
        r = client.post(f"/evaluaciones/postulaciones/{P1}/psicometria", headers=HG, json={"prueba_ids": [GER]})
        check(r.status_code == 201 and r.json()["evaluacion"]["claveProveedor"] == "1-JOR-0001", "creación OK: la evaluación y su clave se guardan")
        check(r.json()["envioCandidato"]["enviado"] is False, "el aviso al candidato no salió (sin canal disponible)")
        seg = client.get(f"/procesos/postulaciones/{P1}", headers=HG).json()
        ps = next(x for e in seg["etapas"] for x in e["pasos"] if x.get("psicometria") and x["psicometria"]["evaluacion"])["psicometria"]
        check(ps["status"] == "error_envio" and ps["statusTexto"] == "Error de envío", "el bloque queda «Error de envío»")
        check(ps["liga"] and ps["clave"] == "1-JOR-0001" and ps["reenvio"] == "proveedor", "trae la liga REAL y la clave para «Copiar liga» / «Reintentar»")
        n = len(LLAMADAS)
        seg2 = client.get(f"/procesos/postulaciones/{P1}", headers=HG).json()
        check(len(LLAMADAS) == n and seg2 == seg, "leer/copiar la liga no llama al proveedor ni cambia el estado")

        print("\n--- 5. Reintentar envío ---")
        CORREO_OK["v"] = True
        r = client.post(f"/evaluaciones/{ps['evaluacion']}/ligas/proveedor/enviar", headers=HG)
        check(r.status_code == 200 and any(x["enviado"] and x["canal"] == "correo" for x in r.json()["resultados"]),
              "«Reintentar envío»: sale por correo (en demo-grupak el correo es el respaldo del canal de mensajería)")
        check(len(LLAMADAS) == n, "…sin volver a dar de alta en el proveedor (misma clave)")
        seg = client.get(f"/procesos/postulaciones/{P1}", headers=HG).json()
        ps = next(x for e in seg["etapas"] for x in e["pasos"] if x.get("psicometria") and x["psicometria"]["evaluacion"])["psicometria"]
        check(ps["status"] == "enviada", "la fila pasa a «Enviada»")

        print("\n--- 6. Otras Cuentas: sin cambios de comportamiento ---")
        CORREO_OK["v"] = False
        P2 = candidato(HO, "Olga Otra", "5512348002", "olga@correo.mx", vacs[1].codigo)
        r = client.post(f"/evaluaciones/postulaciones/{P2}/psicometria", headers=HO, json={"prueba_ids": [OTRA]})
        check(r.status_code == 201, "otra Cuenta: «Asignar y enviar» funciona igual")
        c = client.get(f"/candidatos/{P2}", headers=HO).json()
        check(c.get("psychometric_alert") is None, "otra Cuenta: sin alertas del flujo simple en el tablero")
    finally:
        psi.httpx.post, correo_srv.enviar_correo = orig_post, orig_correo
        settings.psicometricas_token = settings.psicometricas_password = ""
    db.close()

print(f"\n{OK} comprobaciones OK")
