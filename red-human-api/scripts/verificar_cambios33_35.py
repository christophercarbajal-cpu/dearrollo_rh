"""Cambios 33, 34 y 35 (2026-10-13). Base desechable; Meta, IA y correo SIMULADOS (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambios33_35.py

Cambio 33: ruta SIN actividades de Onboarding → «Listo para pasar a colaborador» sin enviar nada; «Documentos
pendientes: N»; «Solicitar documentos pendientes» pide SOLO lo no aprobado; «Pasar a colaborador» da de alta dejando lo
pendiente «Pendiente» en el expediente.
Cambio 34: catálogo único; los documentos de la Cuenta + los de la vacante se suman sin repetir; la migración fusiona
«Identificación oficial» en «INE (frente y reverso)» y los duplicados del expediente (el Aprobado y su archivo ganan).
Cambio 35: los avisos a evaluadores salen con plantilla de 6 variables (nunca texto libre) y el error de Meta llega
legible junto al «Fallido».
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
_dir = tempfile.mkdtemp(prefix="rh_c33_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c33.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-c33"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_admin, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (Colaborador, Cuenta, Documento, Expediente, Postulacion, Usuario, UsuarioCuenta, Vacante,  # noqa: E402
                        clave_documento, documento_catalogo, normalizar_documentos_vacante)
from app.routers.candidatos import _abrir_expediente, _crear_candidato, crear_postulacion  # noqa: E402
from app.services import catalogo_documentos as scat  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
ENVIADOS = []
INE = "INE (frente y reverso)"


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


RECHAZAR = {"plantilla": None}


# Cambio 46: el aviso al evaluador intenta primero texto libre; estos números NO han escrito en 24 h (Meta lo rechaza) y
# el aviso sigue con la plantilla
FUERA_DE_VENTANA = {"523311112222"}


async def _meta_falso(cuerpo):
    ENVIADOS.append(cuerpo)
    if cuerpo.get("type") == "text" and cuerpo.get("to") in FUERA_DE_VENTANA:
        return {"enviado": False, "proveedor": "meta", "detalle": "131047: Re-engagement message", "codigo": 131047}
    if RECHAZAR["plantilla"] and cuerpo.get("type") == "template" and cuerpo["template"]["name"] == RECHAZAR["plantilla"]:
        return {"enviado": False, "proveedor": "meta", "detalle": "131047: Re-engagement message", "codigo": "131047"}
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "mensaje_id": f"wamid.{len(ENVIADOS)}"}


whatsapp._meta_post = _meta_falso
RUTA_SIN_ONBOARDING = [{"id": "cond", "tipo": "condiciones", "nombre": "Propuesta", "etapa": "Contratación"}]

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cu = Cuenta(nombre="C33", nombre_comercial="Operaciones C33", estado="Activa", slug="c33")
    db.add(cu)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cu.id))
    v = Vacante(codigo="VAC-C33", titulo="Repartidor", estado="Publicada", cuenta_id=cu.id, requisitos="Licencia vigente",
                documentos_solicitados=normalizar_documentos_vacante(["INE", "Licencia de conducir"]))
    db.add(v)
    db.flush()
    v.proceso = sproc.proceso_para_vacante(db, cu.id, {}, {"pasos": RUTA_SIN_ONBOARDING, "etapas": {}})
    db.commit()
    for dep in (usuario_actual, usuario_decisor, usuario_admin):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cu

    # ================= Cambio 34: catálogo único =================
    print("\n--- Cambio 34 · catálogo único y documentos Cuenta + vacante ---")
    check(documento_catalogo("Identificación oficial")["nombre"] == INE and clave_documento("INE") == clave_documento(INE) == "ine",
          "«Identificación oficial» e «INE» son el mismo documento del catálogo (id fijo «ine»)")
    check(documento_catalogo("Carta de no antecedentes penales") == {"id": "otro", "nombre": "Carta de no antecedentes penales", "cantidad": 1, "foto": False},
          "lo que no está en el catálogo es «Otro» con su nombre")
    r = client.post("/onboarding/plantillas", json={"nombre": "Documentos de contratación", "alcance": "empresa",
                                                    "documentos": [{"tipo": "Identificación oficial"}, {"tipo": "CURP"}]})
    check(r.status_code == 201 and [d["tipo"] for d in r.json()["documentos"]] == [INE, "CURP"],
          "la configuración de la Cuenta guarda los documentos con el nombre del catálogo")
    persona = _crear_candidato(db, cu.id, "Ana Lista", "Formulario", False, correo="ana@c33.mx", telefono="5511330001")
    p = crear_postulacion(db, persona, v, cu.id, "formulario", consentimiento=True)
    p.etapa = "Contratación"
    e = _abrir_expediente(db, p, admin)
    db.commit()
    tipos = [d.tipo for d in e.documentos if not d.interno]
    check(tipos == [INE, "CURP", "Licencia de conducir"],
          f"expediente = Cuenta (INE, CURP) + vacante (INE, Licencia) sin repetir → {tipos}")

    # ================= Cambio 33: sin actividades de Onboarding =================
    print("\n--- Cambio 33 · ruta sin actividades de Onboarding ---")
    ine = next(d for d in e.documentos if d.tipo == INE)
    ine.estado, ine.revisado_por, ine.archivo, ine.nombre_archivo = "recibido", admin.nombre, f"{_dir}/ine.pdf", "ine.pdf"
    e.puesto, e.sueldo, e.tipo_contratacion = "Repartidor", "$12,000 mensuales", "Tiempo indeterminado"
    e.fecha_ingreso = datetime.now(timezone.utc) + timedelta(days=3)
    e.condiciones_guardadas_en = datetime.now(timezone.utc)
    p.analisis = {**(p.analisis or {}), "propuesta": {"estado": "aceptada", "canal": "rh"}}
    db.commit()
    desde = len(ENVIADOS)
    r = client.post(f"/onboarding/expedientes/{e.id}/iniciar", json={"documentos": [{"tipo": INE}], "omitir_obligatorios": True,
                                                                  "comentario": "Prueba: documentos se piden en Onboarding"})
    check(r.status_code == 200 and r.json().get("estadoOnboarding") == "listo_colaborador", f"Iniciar Onboarding ({r.status_code} {r.text[:160]})")
    db.expire_all()
    p = db.get(Postulacion, p.id)
    check(p.etapa == "Onboarding" and p.activa and p.expediente.estado != "alta", "pasa a Onboarding y sigue activo")
    check(len(ENVIADOS) == desde, "«Listo para pasar a colaborador»: no se envía nada a nadie")
    check(client.get(f"/onboarding/expedientes/{e.id}/tareas").json() == [], "no se generan tareas")
    ficha = client.get(f"/candidatos/{p.codigo}").json()
    onb = ficha.get("onboarding") or {}
    check(onb.get("estado") == "listo_colaborador" and onb.get("texto") == "Listo para pasar a colaborador",
          f"la ficha dice «Listo para pasar a colaborador» ({onb})")
    check(onb.get("documentosPendientes") == 2 and set(onb.get("documentosPendientesLista")) == {"CURP", "Licencia de conducir"},
          "«Documentos pendientes: 2» (lo aprobado no cuenta)")
    s = client.get(f"/procesos/postulaciones/{p.codigo}").json()
    check(s["siguienteAccion"]["tipo"] == "pasar_colaborador", "acción principal: «Pasar a colaborador»")

    print("\n--- Cambio 33 · «Solicitar documentos pendientes» ---")
    r = client.post(f"/onboarding/expedientes/{e.id}/solicitar-pendientes")
    check(r.status_code == 200 and r.json()["documentos"] == ["CURP", "Licencia de conducir"],
          f"pide SOLO los no aprobados ({r.status_code} {r.json().get('documentos') if r.status_code == 200 else r.text[:120]})")
    db.expire_all()
    p = db.get(Postulacion, p.id)
    adhoc = next((x for x in p.proceso["pasos"] if x.get("adhoc") and x["tipo"] == "documentos"), None)
    check(adhoc is not None and adhoc["etapa"] == "Onboarding" and adhoc["documentos"] == ["CURP", "Licencia de conducir"]
          and p.etapa == "Onboarding", "crea la actividad «Documentos» solo para esta persona, en Onboarding (no cambia la etapa)")
    check(v.proceso == db.get(Vacante, v.id).proceso and not any(x.get("adhoc") for x in db.get(Vacante, v.id).proceso["pasos"]),
          "la ruta de la vacante no cambia")
    estado_adhoc = next(x for e_ in client.get(f"/procesos/postulaciones/{p.codigo}").json()["etapas"] for x in e_["pasos"] if x["id"] == adhoc["id"])
    check("CURP" in (estado_adhoc.get("espera") or "") and INE not in (estado_adhoc.get("espera") or ""),
          f"la actividad espera solo lo pedido («{estado_adhoc.get('espera')}»)")
    check(client.get(f"/candidatos/{p.codigo}").json()["onboarding"]["documentosPendientes"] == 2, "el contador sigue en 2 hasta que se aprueben")

    print("\n--- Cambio 33 · «Pasar a colaborador» con pendientes ---")
    r = client.post(f"/onboarding/expedientes/{e.id}/pasar-colaborador", json={"comentario": "Ingresa hoy; entrega luego"})
    check(r.status_code == 200 and set(r.json()["pendientes"]["documentos"]) == {"CURP", "Licencia de conducir"},
          f"pasa a Colaboradores aunque falten documentos ({r.status_code} {r.text[:160]})")
    db.expire_all()
    p = db.get(Postulacion, p.id)
    check(not p.activa and p.motivo_cierre == "contratado" and p.expediente.estado == "alta"
          and db.query(Colaborador).filter(Colaborador.expediente_id == e.id).count() == 1, "queda como Colaborador (alta sobre el mismo expediente)")
    pend = {d.tipo: d.estado for d in p.expediente.documentos if not d.interno}
    check(pend["CURP"] == "pendiente" and pend["Licencia de conducir"] == "pendiente" and pend[INE] == "recibido",
          f"lo pendiente se mantiene «Pendiente» en su expediente ({pend})")
    check(any(h.get("evento") == "pasado_a_colaborador" for h in p.historial or []), "queda en el historial con sus pendientes")

    # ================= Cambio 34: migración =================
    print("\n--- Cambio 34 · migración del expediente (Eduardo Rodríguez) ---")
    ed = _crear_candidato(db, cu.id, "Eduardo Rodríguez", "Formulario", False, correo="eduardo@c33.mx", telefono="5511330002")
    pe = crear_postulacion(db, ed, v, cu.id, "formulario", consentimiento=True)
    exp = Expediente(candidato_id=ed.id, puesto="Repartidor", seleccionado_por=admin.nombre, token="tok-eduardo")
    pe.expediente = exp
    db.add(exp)
    db.flush()
    for tipo, estado, archivo, por in (("Identificación oficial", "recibido", "ine.pdf", "RH"), ("CURP", "pendiente", "", ""),
                                       ("CURP", "recibido", "curp.pdf", "RH"), ("INE", "pendiente", "", "")):
        exp.documentos.append(Documento(expediente_id=exp.id, tipo=tipo, estado=estado, archivo=archivo, nombre_archivo=archivo,
                                        revisado_por=por, obligatorio=True))
    v2 = Vacante(codigo="VAC-C33B", titulo="Chofer", estado="Borrador", cuenta_id=cu.id,
                 documentos_solicitados=[{"nombre": "Identificación oficial", "cantidad": 1, "foto": False}, {"nombre": "INE", "cantidad": 1, "foto": False}])
    db.add(v2)
    db.commit()
    plan = scat.migrar(db, aplicar=False)
    db.rollback()
    check(plan["totales"]["fusionados"] == 2 and "VAC-C33B" in plan["vacantes"], f"la simulación muestra el plan sin guardar ({plan['totales']})")
    r = scat.migrar(db, aplicar=True)
    db.commit()
    db.expire_all()
    exp = db.get(Expediente, exp.id)
    docs = [(d.tipo, d.estado, d.archivo) for d in exp.documentos if not d.interno]
    check(sorted(d[0] for d in docs) == ["CURP", INE], f"sin duplicados y sin «Identificación oficial» → {docs}")
    check(all(estado == "recibido" and archivo for _, estado, archivo in docs), "el estado Aprobado y su archivo sobreviven a la fusión")
    check([d["nombre"] for d in db.get(Vacante, v2.id).documentos_solicitados] == [INE], "la vacante queda con un solo «INE (frente y reverso)»")
    again = scat.migrar(db, aplicar=True)
    check(not any(again["totales"].values()), "idempotente: correrla otra vez no cambia nada")

    # ================= Cambio 35: plantillas para evaluadores =================
    print("\n--- Cambio 35 · avisos a evaluadores con plantilla y error de Meta visible ---")
    pc = crear_postulacion(db, _crear_candidato(db, cu.id, "Carla Cita", "Formulario", False, correo="carla@c33.mx", telefono="5511330003"),
                           v, cu.id, "formulario", consentimiento=True)
    pc.etapa = "Entrevista Humana"
    db.commit()
    fecha = (datetime.now(timezone.utc) + timedelta(days=4)).strftime("%Y-%m-%d")
    desde = len(ENVIADOS)
    r = client.post(f"/evaluaciones/postulaciones/{pc.codigo}", json={
        "tipo": "medica", "forma": "asignada", "evaluador": {"tipo": "externo", "nombre": "Dra. Paula Médica", "whatsapp": "3311112222"},
        "cita": {"fecha": fecha, "hora": "09:00", "modalidad": "Presencial", "direccion": "Clínica Centro, Av. Juárez 10"}})
    check(r.status_code == 201, f"evaluación médica con cita ({r.status_code} {r.text[:120]})")
    ev = r.json()["evaluacion"]["codigo"]
    client.post(f"/evaluaciones/publica/consentimiento/{db.query(__import__('app.models', fromlist=['Evaluacion']).Evaluacion).filter_by(codigo=ev).one().consentimiento_token}/aceptar",
                json={"nombre": "Carla Cita", "acepto": True})
    al_medico = [c for c in ENVIADOS[desde:] if c.get("to") == "523311112222"]
    check(al_medico and al_medico[-1].get("type") == "template"
          and al_medico[-1]["template"]["name"] == "aviso_medico" and len(al_medico[-1]["template"]["components"][0]["parameters"]) == 6,
          f"fuera de la ventana de 24 h al médico le llega la plantilla «aviso_medico» de 6 variables ({[c.get('type') for c in al_medico]})")
    ps = [x["text"] for x in al_medico[-1]["template"]["components"][0]["parameters"]]
    check(ps[0] == "Dra. Paula Médica" and ps[1] == "Carla Cita" and ps[2] == "Repartidor" and "Clínica Centro" in ps[4] and "/evaluacion/" in ps[5],
          f"[nombre_evaluador, candidato, puesto, fecha_y_hora, lugar, liga] → {ps}")
    RECHAZAR["plantilla"] = "aviso_medico"
    r = client.post(f"/evaluaciones/{ev}/ligas/evaluador/enviar")
    RECHAZAR["plantilla"] = None
    s = client.get(f"/procesos/postulaciones/{pc.codigo}").json()
    pm = next(x for e_ in s["etapas"] for x in e_["pasos"] if x["tipo"] == "medica")
    envio = next((x for d, x in (pm.get("envios") or {}).items() if d != "candidato"), {})
    check(envio.get("estado") == "fallido" and envio.get("error", "").startswith("Fuera de la ventana de 24 horas"),
          f"«Reenviar» fuera de la ventana → «Fallido» con el error de Meta legible ({envio.get('estadoTexto')}: {envio.get('error')})")
    app_dir = RAIZ.parent / "red-human-app"
    seg_tsx = (app_dir / "components/dashboard/procesos/seguimiento-proceso.tsx").read_text(encoding="utf-8")
    check('e.estado === "fallido" && e.error' in seg_tsx, "la vista de seguimiento muestra el error junto al «Fallido»")
    acc = (app_dir / "components/dashboard/onboarding/acciones-onboarding.tsx").read_text(encoding="utf-8")
    check("Documentos pendientes: {pendientes}" in acc and "pendientes > 0" in acc and "Pasar a colaborador" in acc
          and "Solicitar documentos pendientes" in acc and "Asignar tareas" in acc,
          "la ficha en Onboarding trae «Documentos pendientes: N» (oculto en 0) y los tres botones")

    app.dependency_overrides.clear()
    db.close()

print(f"\n🎉 Cambios 33-35: {OK} comprobaciones OK")
