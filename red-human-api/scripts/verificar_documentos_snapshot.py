"""Aceptación «Chofer con auto propio» — Documentos alineados a la vacante (2026-10-17). Base desechable; Meta SIMULADA.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_documentos_snapshot.py

1. Snapshot: al nacer, la postulación guarda la COPIA FIJA de los documentos que pide la vacante; si la vacante se edita
   después, al candidato en curso no le cambia nada (el candidato nuevo toma la versión nueva).
2. Estado y porcentaje: SOLO sobre el snapshot (nunca INE, CURP, NSS… de la configuración base que la vacante no pidió).
   Todos aprobados → «Documentos aprobados»; alguno falta o se rechazó → no está aprobado.
3. La liga y la solicitud por WhatsApp piden SOLO los del snapshot.
4. La ficha (expediente) muestra SOLO los del snapshot; sin documentos pedidos (o sin la actividad en la ruta) la sección
   no existe y nada bloquea por documentos.
5. Lo que RH agrega a mano entra al snapshot de ESA postulación y sí se muestra.
"""

import asyncio
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_docsnap_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "docsnap.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-docsnap"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Postulacion, Usuario, UsuarioCuenta, Vacante, clave_documento, normalizar_documentos_vacante  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.serial import expediente_dict, postulacion_dict  # noqa: E402
from app.services import documentos_vacante as sdv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
ENVIADOS = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _meta_falso(cuerpo):
    ENVIADOS.append(cuerpo)
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "mensaje_id": f"wamid.{len(ENVIADOS)}"}


whatsapp._meta_post = _meta_falso

CHOFER = ["Licencia de conducir", "Fotos del auto", "Tarjeta de circulación", "Póliza de seguro"]
BASE = ("INE (frente y reverso)", "CURP", "Número de Seguridad Social (NSS)", "Constancia de Situación Fiscal (RFC)",
        "Comprobante de domicilio", "Cuenta bancaria / CLABE")

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Fletes Snap", nombre_comercial="Fletes Rápidos", estado="Activa", slug="fletes-snap")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    def vacante(codigo, titulo, docs, ruta="masivos_con_documentos"):
        v = Vacante(codigo=codigo, titulo=titulo, estado="Publicada", cuenta_id=cuenta.id, slug=codigo.lower(), sueldo="$14,000 MXN mensuales",
                    ubicacion="Monterrey, Nuevo León", requisitos="Licencia de manejo tipo B", modalidad="Presencial",
                    documentos_solicitados=normalizar_documentos_vacante([{"nombre": n, "cantidad": 4 if n == "Fotos del auto" else 1,
                                                                           "foto": n == "Fotos del auto"} for n in docs]))
        db.add(v)
        db.flush()
        v.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": sproc.ruta_base(ruta)["pasos"]})
        db.commit()
        return v

    def postular(v, nombre, tel):
        c = _crear_candidato(db, cuenta.id, nombre, "Portal", False, telefono=tel)
        p = crear_postulacion(db, c, v, cuenta.id, "web", consentimiento=True)
        db.commit()
        return p

    def nombres(lista):
        return sorted(clave_documento(x) for x in lista)

    # ================================================== 1. snapshot
    print("\n--- 1. Snapshot al nacer; editar la vacante no altera al candidato en curso ---")
    vc = vacante("VAC-CH1", "Chofer con auto propio", CHOFER)
    pa = postular(vc, "Arturo Chofer", "5542000001")
    snap = [d["nombre"] for d in sdv.snapshot(pa)]
    check(nombres(snap) == nombres(CHOFER) and isinstance((pa.analisis or {}).get(sdv.CLAVE_SNAPSHOT), dict),
          f"la postulación nace con su copia fija de los documentos de la vacante ({snap})")
    fotos = next(d for d in sdv.snapshot(pa) if clave_documento(d["nombre"]) == clave_documento("Fotos del auto"))
    check(fotos["cantidad"] == 4 and fotos["foto"], "la copia conserva cantidad (4 fotos) y si es foto")
    vc.documentos_solicitados = normalizar_documentos_vacante([{"nombre": n} for n in ("Licencia de conducir", "INE (frente y reverso)")])
    db.commit()
    db.expire_all()
    pa = db.get(Postulacion, pa.id)
    check(nombres(d["nombre"] for d in sdv.snapshot(pa)) == nombres(CHOFER), "la vacante se editó → el candidato en curso conserva SUS documentos")
    pb = postular(vc, "Beto Nuevo", "5542000002")
    check(nombres(d["nombre"] for d in sdv.snapshot(pb)) == nombres(["Licencia de conducir", "INE (frente y reverso)"]),
          "un candidato nuevo toma la versión vigente de la vacante")
    vc.documentos_solicitados = normalizar_documentos_vacante([{"nombre": n, "cantidad": 4 if n == "Fotos del auto" else 1,
                                                                "foto": n == "Fotos del auto"} for n in CHOFER])
    db.commit()

    # ================================================== 2-3. solicitud, liga y estado
    print("\n--- 2-3. La liga y la solicitud piden SOLO el snapshot; el estado se calcula sobre él ---")
    desde = len(ENVIADOS)
    db.expire_all()
    pa = db.get(Postulacion, pa.id)
    r = asyncio.run(sdv.enviar_solicitud(db, pa, "RH Prueba"))
    db.expire_all()
    pa = db.get(Postulacion, pa.id)
    e = pa.expediente
    check(e is not None and nombres(d.tipo for d in sdv.de_snapshot(e) if not d.interno) == nombres(CHOFER),
          f"el expediente pide los 4 documentos del snapshot ({[d.tipo for d in e.documentos]})")
    check(not any(clave_documento(d.tipo) in {clave_documento(b) for b in BASE} for d in e.documentos),
          "el expediente NO trae INE, CURP, NSS, RFC, domicilio ni CLABE (catálogo base que la vacante no pidió)")
    texto = r["texto"]
    check(all(n.split(" ")[0] in texto for n in ("Licencia", "Fotos", "Tarjeta", "Póliza")) and "CURP" not in texto and "INE" not in texto,
          f"el WhatsApp lista solo los del snapshot («{texto[:120]}…»)")
    vista = sdv.vista_publica(db, e)
    check(nombres(d["tipo"] for d in vista["documentos"]) == nombres(CHOFER), "la liga web muestra solo los 4 del snapshot")
    check(sdv.estado_expediente(e)["clave"] == "pendiente", "nada entregado → «Pendiente»")
    for d in sdv.docs_en_juego(e):
        d.estado, d.archivo, d.recibido_en = "revision", "x.pdf", None
    db.flush()
    hechos = sdv.aprobar(db, e, [], "RH Prueba")
    db.flush()
    check(sorted(map(clave_documento, hechos)) == nombres(CHOFER) and sdv.estado_expediente(e)["clave"] == "aprobados",
          "todos los del snapshot aprobados → «Documentos aprobados»")
    check(e.progreso == 100 and sdv.progreso(e) == 100, "el porcentaje es 100 % con SOLO los del snapshot")
    sdv.pedir_correccion(db, e, ["Póliza de seguro"], "La póliza está vencida", "RH Prueba")
    db.flush()
    check(sdv.estado_expediente(e)["clave"] != "aprobados" and e.progreso < 100, "uno rechazado → ya no está aprobado (pendiente de corrección)")
    sdv.aprobar(db, e, [], "RH Prueba")  # la mecánica de los botones no cambia: lo rechazado sin archivo no se aprueba
    db.commit()

    # ================================================== 4. ficha / contratación
    print("\n--- 4. La ficha muestra solo el snapshot; sin documentos pedidos, la sección no existe ---")
    r = client.patch(f"/candidatos/{pa.codigo}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True,
                                                              "comentario": "Prueba: documentos alineados a la vacante"})
    check(r.status_code == 200, f"pasa a Contratación ({r.status_code})")
    db.expire_all()
    pa = db.get(Postulacion, pa.id)
    ex = expediente_dict(pa.expediente)
    check(nombres(d["nombre"] for d in ex["documentos"] if not d.get("interno")) == nombres(CHOFER) and ex["documentosVisibles"],
          f"en Contratación el expediente sigue con SOLO los del snapshot ({[d['nombre'] for d in ex['documentos']]})")
    check(postulacion_dict(pa, detalle=True)["documentosVisibles"] is True, "la ficha muestra la sección «Documentos»")
    check(set(pa.expediente.no_aprobados) == {"Póliza de seguro"} or "Póliza" in " ".join(pa.expediente.no_aprobados),
          f"falta solo lo del snapshot ({pa.expediente.no_aprobados})")

    vs = vacante("VAC-CH2", "Ayudante sin documentos", [])
    pc = postular(vs, "Carla Sin Docs", "5542000003")
    r = client.patch(f"/candidatos/{pc.codigo}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True,
                                                              "comentario": "Prueba: vacante sin documentos"})
    db.expire_all()
    pc = db.get(Postulacion, pc.id)
    exc = expediente_dict(pc.expediente)
    check(r.status_code == 200 and exc["documentos"] == [] and exc["documentosVisibles"] is False
          and postulacion_dict(pc, detalle=True)["documentosVisibles"] is False,
          "vacante sin documentos → el expediente no trae ninguno y la sección «Documentos» se oculta")
    check(pc.expediente.progreso == 100 and pc.expediente.sin_documentos_requeridos, "sin documentos pedidos, nada bloquea por documentos (100 %)")

    vm = vacante("VAC-CH3", "Chofer ruta sin documentos", CHOFER, ruta="corporativo")
    pd_ = postular(vm, "Diego Corporativo", "5542000004")
    check(not sdv.ruta_con_documentos(pd_) or sdv.documentos_visibles(pd_), "la regla de visibilidad sigue a la ruta")
    if not sdv.ruta_con_documentos(pd_):
        check(sdv.requeridos(pd_) == [] and not sdv.documentos_visibles(pd_), "la ruta sin actividad de documentos no pide ni muestra documentos")

    # ================================================== 5. RH agrega a mano
    print("\n--- 5. Lo que RH agrega a mano sí se pide y se muestra ---")
    r = client.post(f"/contratacion/expedientes/{pc.expediente.id}/documentos/agregar", json={"tipo": "Carta de recomendación laboral", "obligatorio": True})
    check(r.status_code in (200, 201) and [d["nombre"] for d in r.json()["documentos"]] == ["Carta de recomendación laboral"]
          and r.json()["documentosVisibles"] is True, f"agregado por RH → visible y requerido ({r.status_code})")
    r = client.post(f"/contratacion/expedientes/{pc.expediente.id}/documentos/agregar", json={"tipo": "Carta de recomendación laboral", "obligatorio": True})
    check(r.status_code == 409, "agregarlo dos veces → 409 (la mecánica de siempre)")
    db.close()

print(f"\nTodo OK ({OK} comprobaciones)")
