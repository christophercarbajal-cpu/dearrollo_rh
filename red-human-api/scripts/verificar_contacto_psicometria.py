"""Regresión del correo en el modal de psicometría y la edición de contacto en la ficha (2026-10-07).

    .venv/Scripts/python.exe scripts/verificar_contacto_psicometria.py

Base desechable. Psicométricas.mx se SIMULA (httpx falso, sin red): NUNCA consume el saldo compartido con producción.
Cubre: la ficha trae el correo; `PATCH /candidatos/{codigo}/contacto` (correo y teléfono, formato, duplicado de otra
persona, cualquier etapa, bitácora); «Asignar y enviar» con `correo` guarda el correo ANTES de llamar al proveedor
(también si el proveedor falla), lo manda en el payload y sin `correo` se comporta como antes (compatibilidad).
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

_dir = tempfile.mkdtemp(prefix="rh_contacto_psico_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "contacto.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "PSICOMETRICAS_TOKEN",
          "PSICOMETRICAS_PASSWORD", "PSICOMETRICAS_USUARIO", "PSICOMETRICAS_WEBHOOK_SECRET", "PSICOMETRICAS_URL_CANDIDATO"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-contacto-psico"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import usuario_actual, usuario_admin, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Bitacora, Candidato, Cuenta, Evaluacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
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
    def __init__(self, status, datos=None):
        self.status_code, self._d, self.content, self.text = status, datos, b"", ""

    def json(self):
        return self._d


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    B = Cuenta(nombre="Contacto", nombre_comercial="Contacto SA", razon_social="Contacto SA", estado="Activa")
    db.add(B)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=B.id))
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").first()
    v.cuenta_id = B.id
    v.proceso = {}
    obtener(db).modo_prueba = False  # dedupe normal (con Modo Prueba se permiten duplicados)
    db.commit()
    for dep in (usuario_actual, usuario_decisor, usuario_admin):
        app.dependency_overrides[dep] = lambda: admin
    H = {"X-Cuenta-Id": str(B.id)}

    def candidato(nombre, tel, correo):
        return client.post("/candidatos", headers=H, json={"nombre": nombre, "telefono": tel, "correo": correo, "vacante": v.codigo,
                                                             "consentimiento": True, "fuente": "WhatsApp"}).json()["id"]

    P1 = candidato("Sofía Sin Correo", "5512349001", "")
    P2 = candidato("Tomás Con Correo", "5512349002", "tomas@correo.mx")

    print("\n--- 1. La ficha trae el correo actual ---")
    check(client.get(f"/candidatos/{P2}", headers=H).json()["correo"] == "tomas@correo.mx", "GET ficha: `correo` del candidato")
    check(client.get(f"/candidatos/{P1}", headers=H).json()["correo"] == "", "sin correo: viene vacío (no null)")

    print("\n--- 2. Ficha → editar correo y teléfono (cualquier etapa) ---")
    r = client.patch(f"/candidatos/{P1}/contacto", headers=H, json={"correo": "  Sofia@Correo.MX "})
    check(r.status_code == 200 and r.json()["correo"] == "sofia@correo.mx" and r.json()["telefono"] == "5512349001",
          "correo guardado (minúsculas, sin espacios); el teléfono no se tocó")
    r = client.patch(f"/candidatos/{P1}/contacto", headers=H, json={"telefono": "+52 1 55 1234 9099"})
    check(r.status_code == 200 and r.json()["telefono"] == "5512349099", "teléfono normalizado a 10 dígitos")
    check(client.patch(f"/candidatos/{P1}/contacto", headers=H, json={"correo": "no-es-correo"}).status_code == 400, "correo inválido → 400")
    check(client.patch(f"/candidatos/{P1}/contacto", headers=H, json={"telefono": "12345"}).status_code == 400, "teléfono incompleto → 400")
    r = client.patch(f"/candidatos/{P1}/contacto", headers=H, json={"correo": "tomas@correo.mx"})
    check(r.status_code == 409 and "Tomás" in r.json()["detail"], "correo de OTRA persona de la Cuenta → 409 con su nombre")
    bit = db.query(Bitacora).filter(Bitacora.accion == "contacto_actualizado").all()
    check(len(bit) == 2, "cada cambio queda en bitácora (`contacto_actualizado`)")
    client.patch(f"/candidatos/{P2}/etapa", headers=H, json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True,
                                                              "comentario": "Prueba de edición de contacto en Contratación"})
    r = client.patch(f"/candidatos/{P2}/contacto", headers=H, json={"correo": "tomas.nuevo@correo.mx"})
    check(r.status_code == 200 and r.json()["correo"] == "tomas.nuevo@correo.mx" and r.json()["etapa"] == "Contratación",
          "se edita también en Contratación y la etapa no cambia")

    print("\n--- 3. Modal de psicometría: correo capturado ahí mismo ---")
    BAT = client.post("/evaluaciones/pruebas", headers=H, json={"nombre": "Batería", "tipo": "bateria", "id_proveedor": "1,7",
                                                                 "activa": True, "modo": "integrada", "proveedor": "Psicométricas.mx"}).json()["id"]
    P3 = candidato("Ulises Sin Correo", "5512349003", "")
    settings.psicometricas_token, settings.psicometricas_password = "T" * 20, "P" * 20
    LLAMADAS = []
    orig_post = psi.httpx.post
    try:
        psi.httpx.post = lambda url, data=None, timeout=None: (LLAMADAS.append(data), R(401, {"code": "1001", "msg": "Token inválido"}))[1]
        r = client.post(f"/evaluaciones/postulaciones/{P3}/psicometria", headers=H, json={"prueba_ids": [BAT]})
        check(r.status_code == 409 and not LLAMADAS, "sin correo y sin capturarlo: 409 SIN llamar al proveedor (como antes)")
        r = client.post(f"/evaluaciones/postulaciones/{P3}/psicometria", headers=H, json={"prueba_ids": [BAT], "correo": "mal@"})
        check(r.status_code == 400 and not LLAMADAS, "correo inválido en el modal → 400 sin llamar al proveedor")
        r = client.post(f"/evaluaciones/postulaciones/{P3}/psicometria", headers=H, json={"prueba_ids": [BAT], "correo": "ulises@correo.mx"})
        db.expire_all()
        c3 = db.query(Candidato).filter(Candidato.nombre == "Ulises Sin Correo").first()
        check(r.status_code in (400, 502) and len(LLAMADAS) == 1 and c3.correo == "ulises@correo.mx",
              "el proveedor falla, pero el correo YA quedó guardado en la ficha (se guarda antes de llamar)")
        check(not db.query(Evaluacion).filter(Evaluacion.postulacion_id == c3.postulaciones[0].id).count(),
              "…y la evaluación no quedó (rollback del envío fallido)")
        psi.httpx.post = lambda url, data=None, timeout=None: (LLAMADAS.append(data), R(200, {"status": "200", "clave": "1-ULI-0001"}))[1]
        r = client.post(f"/evaluaciones/postulaciones/{P3}/psicometria", headers=H, json={"prueba_ids": [BAT], "correo": "Ulises.Ok@Correo.mx"})
        check(r.status_code == 201 and LLAMADAS[-1]["Email"] == "ulises.ok@correo.mx" and r.json()["candidato"]["correo"] == "ulises.ok@correo.mx",
              "«Enviar ahora» con correo corregido: se actualiza la ficha y el proveedor recibe ESE correo")
        check(r.json()["evaluacion"]["claveProveedor"] == "1-ULI-0001", "la psicometría queda enviada con su clave")
        P4 = candidato("Vera Con Correo", "5512349004", "vera@correo.mx")
        r = client.post(f"/evaluaciones/postulaciones/{P4}/psicometria", headers=H, json={"prueba_ids": [BAT]})
        check(r.status_code == 201 and LLAMADAS[-1]["Email"] == "vera@correo.mx", "sin el campo `correo` (otras Cuentas) funciona igual que antes")
    finally:
        psi.httpx.post = orig_post
        settings.psicometricas_token = settings.psicometricas_password = ""
    db.close()

print(f"\n{OK} comprobaciones OK")
