"""Regresión de Evaluaciones y verificaciones (2026-09-28; sobre Evaluaciones unificadas desde 2026-09-29). Base desechable, SIN proveedores externos ni claves.

    .venv/Scripts/python.exe scripts/verificar_evaluaciones.py

Catálogo de Pruebas psicométricas, «Agregar evaluación» sin mover el pipeline, consentimientos (general y expreso
por escrito para la evaluación médica, como condición), proveedor integrado simulado, conclusiones generales y
médicas, permisos sobre el informe médico y la sugerencia por vacante con aviso antes de Onboarding.
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

_dir = tempfile.mkdtemp(prefix="rh_eval_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "eval.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-eval"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor, usuario_admin  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Bitacora, Cuenta, Evaluacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
PDF_MIN = b"%PDF-1.4\n" + b"%" * 600 + b"\n%%EOF\n"


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def como(usuario):
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: usuario


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Evaluaciones", nombre_comercial="Empresa EV", razon_social="Empresa EV SA", estado="Activa")
    otra = Cuenta(nombre="Otra", nombre_comercial="Otra", estado="Activa")
    db.add_all([cuenta, otra])
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    rh = Usuario(correo="rh.sin.permiso@empresa.mx", nombre="RH Sin Permiso", rol="Usuario", hash_pass="x", activo=True)
    db.add(rh)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=rh.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    obtener(db).modo_prueba = False
    db.commit()
    como(admin)
    app.dependency_overrides[usuario_admin] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    print("\n--- 1. Catálogo: Pruebas psicométricas ---")
    r = client.post("/evaluaciones/pruebas", json={"clave": "PSI-CLEAVER", "nombre": "Cleaver", "descripcion": "Estilo de comportamiento",
                                                     "puestos": ["Chofer repartidor", " "], "modo": "integrada", "proveedor": "Psicometrix", "id_proveedor": "clv-01"})
    check(r.status_code == 201 and r.json()["modo"] == "integrada" and r.json()["puestos"] == ["Chofer repartidor"] and r.json()["activa"],
          "alta con identificador, nombre, descripción, puestos, modo, proveedor, id en proveedor y estado")
    CLEAVER = r.json()["id"]
    check(client.post("/evaluaciones/pruebas", json={"clave": "psi-cleaver", "nombre": "Otra"}).status_code == 409, "el identificador interno no se repite")
    check(client.post("/evaluaciones/pruebas", json={"clave": "X", "nombre": "X", "modo": "enlace"}).status_code == 400, "«Enlace externo» exige la liga")
    check(client.post("/evaluaciones/pruebas", json={"clave": "Y", "nombre": "Y", "modo": "integrada"}).status_code == 400, "«Integrada» exige proveedor")
    check(client.post("/evaluaciones/pruebas", json={"clave": "Z", "nombre": "Z", "modo": "telepatia"}).status_code == 400, "modo inválido → 400")
    r = client.post("/evaluaciones/pruebas", json={"clave": "PSI-TERMAN", "nombre": "Terman", "modo": "enlace", "url": "https://pruebas.example/terman"})
    TERMAN = r.json()["id"]
    r = client.post("/evaluaciones/pruebas", json={"clave": "PSI-VIEJA", "nombre": "Vieja", "modo": "manual"})
    VIEJA = r.json()["id"]
    lista = client.get("/evaluaciones/pruebas", params={"puesto": "chofer REPARTIDOR"}).json()
    check(lista[0]["nombre"] == "Cleaver" and lista[0]["sugerida"], "con puesto, las sugeridas para ese puesto van primero")
    r = client.delete(f"/evaluaciones/pruebas/{VIEJA}")
    check(r.json()["activa"] is False and all(x["id"] != VIEJA for x in client.get("/evaluaciones/pruebas").json()), "«Eliminar» = Inactiva (sale del listado)")
    check(any(x["id"] == VIEJA for x in client.get("/evaluaciones/pruebas", params={"incluir_inactivas": True}).json()), "…pero sigue existiendo")
    check(client.patch(f"/evaluaciones/pruebas/{TERMAN}", json={"descripcion": "Inteligencia general"}).json()["descripcion"] == "Inteligencia general", "se edita")
    app.dependency_overrides[cuenta_actual] = lambda: otra
    check(client.patch(f"/evaluaciones/pruebas/{TERMAN}", json={"nombre": "x"}).status_code == 404, "otra Cuenta no la ve (404)")
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    print("\n--- 2. Agregar evaluación (sin mover el pipeline) ---")
    # Evaluaciones unificadas (2026-09-29): pantalla única; el flujo completo de estados/resultado vive en
    # verificar_evaluaciones_unificadas.py. Aquí: catálogo, proveedor integrado simulado, consentimientos, permisos
    # médicos y avisos antes de Onboarding.
    vac = client.get("/vacantes").json()[0]
    r = client.post("/candidatos", json={"nombre": "Carla Méndez", "telefono": "5512121212", "correo": "carla@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Evaluación", "manual": True})

    def crear(post, **cuerpo):
        return client.post(f"/evaluaciones/postulaciones/{post}", json=cuerpo)

    check(crear(P, tipo="horoscopo", forma="registro_directo").status_code == 400, "tipo inválido → 400")
    check(crear(P, tipo="psicometrica", forma="integrada").status_code == 400, "proveedor integrado exige una prueba del catálogo")
    check(crear(P, tipo="psicometrica", forma="integrada", prueba_id=VIEJA).status_code == 400, "…activa y conectada a un proveedor")
    r = crear(P, tipo="psicometrica", forma="integrada", prueba_id=CLEAVER)
    PSI = r.json()["evaluacion"]
    check(r.status_code == 201 and PSI["nombre"] == "Cleaver" and PSI["forma"] == "integrada" and PSI["proveedor"] == "Psicometrix",
          "la psicométrica integrada toma nombre y proveedor del catálogo")
    check(PSI["estado"] == "pendiente" and PSI["pasoIntegrada"] == "asignada", "con consentimiento de privacidad nace Pendiente (Integrada: Asignada)")
    tipos = {}
    for tipo in ("tecnica", "referencias", "socioeconomica"):
        tipos[tipo] = crear(P, tipo=tipo, forma="registro_directo").json()["evaluacion"]
    tipos["otra"] = crear(P, tipo="otra", nombre="Prueba de manejo", forma="registro_directo").json()["evaluacion"]
    check([tipos[t]["tipoTexto"] for t in tipos] == ["Técnica o caso práctico", "Referencias", "Socioeconómica", "Otra"], "tipos del menú (en femenino: Socioeconómica)")
    check(client.get(f"/candidatos/{P}").json()["etapa"] == "Evaluación", "agregar evaluaciones NO mueve la columna del pipeline")

    print("\n--- 3. Consentimiento general (LFPDPPP) ---")
    r = client.post("/candidatos", json={"nombre": "Sin Consentimiento", "telefono": "5534343434", "vacante": vac["id"], "consentimiento": False, "fuente": "RH"})
    P2 = r.json()["id"]
    r = crear(P2, tipo="referencias", forma="registro_directo")
    check(r.status_code == 409 and "consentimiento" in r.json()["detail"].lower(), "sin consentimiento de privacidad no se agrega la evaluación")
    client.post(f"/candidatos/{P2}/consentimiento", json={"acepta": True, "medio": "escrito"})
    check(crear(P2, tipo="referencias", forma="registro_directo").status_code == 201, "al registrar el consentimiento ya se agrega")

    print("\n--- 4. Evaluación médica: consentimiento expreso por escrito (condición, no estado) ---")
    r = crear(P, tipo="medica", nombre="Examen médico de ingreso", forma="registro_directo")
    MED = r.json()["evaluacion"]
    check(MED["estado"] == "pendiente" and MED["consentimiento"] == "pendiente" and MED["ligaConsentimiento"],
          "la médica nace Pendiente + «En espera de consentimiento» aunque haya consentimiento general, con liga de aceptación")
    check(client.post(f"/evaluaciones/{MED['id']}/realizada").status_code == 409, "sin consentimiento expreso no avanza")
    check(client.post(f"/evaluaciones/{MED['id']}/resultado", data={"comentarios": "x", "version": "0"}).status_code == 409, "…ni se carga resultado")
    token = MED["ligaConsentimiento"].rsplit("/", 1)[-1]
    r = client.post(f"/evaluaciones/{MED['id']}/consentimiento/enviar")
    check(r.status_code == 200 and {x["canal"] for x in r.json()["resultados"]} == {"whatsapp", "correo"}, "RH manda la liga por WhatsApp y correo (resultado visible)")
    pub = client.get(f"/evaluaciones/publica/consentimiento/{token}").json()
    check("Carla Méndez" in pub["texto"] and "datos personales sensibles" in pub["texto"] and not pub["aceptado"], "la persona lee el texto con su nombre")
    check(client.post(f"/evaluaciones/publica/consentimiento/{token}/aceptar", json={"nombre": "Carla Méndez"}).status_code == 400, "sin marcar «Acepto» no cuenta")
    check(client.post(f"/evaluaciones/publica/consentimiento/{token}/aceptar", json={"nombre": "CM", "acepto": True}).status_code == 400, "exige el nombre completo como firma")
    r = client.post(f"/evaluaciones/publica/consentimiento/{token}/aceptar", json={"nombre": "Carla Méndez Ruiz", "acepto": True}, headers={"user-agent": "PruebaNavegador/1.0"})
    check(r.status_code == 200 and r.json()["estado"] == "pendiente", "aceptado → sigue Pendiente (el estado no cambia; la condición sí)")
    db.expire_all()
    ev = db.query(Evaluacion).filter(Evaluacion.codigo == MED["id"]).one()
    check(ev.consentimiento == "otorgado" and ev.consentimiento_texto.startswith("Yo, Carla Méndez") and ev.consentimiento_en is not None, "se guarda el texto EXACTO aceptado y la fecha")
    evid = ev.consentimiento_evidencia
    check(evid["nombre_escrito"] == "Carla Méndez Ruiz" and evid["navegador"] == "PruebaNavegador/1.0" and len(evid["huella_sha256"]) == 64 and evid["medio"] == "electronico",
          "evidencia: nombre escrito, navegador, IP y huella SHA-256")
    check(db.query(Bitacora).filter(Bitacora.accion == "consentimiento_medico_otorgado").count() == 1, "queda en la bitácora hash-encadenada")
    check(client.post(f"/evaluaciones/publica/consentimiento/{token}/aceptar", json={"nombre": "Carla Méndez", "acepto": True}).status_code == 409, "no se acepta dos veces")

    print("\n--- 5. Proveedor integrado simulado ---")
    r = client.post(f"/evaluaciones/{PSI['id']}/enviar")
    check(r.json()["evaluacion"]["pasoIntegrada"] == "enviada" and r.json()["evaluacion"]["estado"] == "pendiente", "Asignada → Enviada (sigue Pendiente)")
    pasos = []
    for _ in range(2):
        r = client.post(f"/evaluaciones/{PSI['id']}/integracion/avanzar")
        pasos.append((r.json()["evaluacion"]["pasoIntegrada"], r.json()["evaluacion"]["estado"]))
    check(pasos == [("iniciada", "pendiente"), ("completada", "realizada_sin_resultado")], f"Enviada → Iniciada → Completada = Realizada · Resultado pendiente ({pasos})")
    check(client.post(f"/evaluaciones/{PSI['id']}/integracion/avanzar").status_code == 409, "el resultado ya no se simula: se registra con el formulario único")
    check(client.post(f"/evaluaciones/{tipos['tecnica']['id']}/integracion/avanzar").status_code == 409, "solo la forma integrada avanza por pasos")

    print("\n--- 6. Resultado: conclusiones por tipo ---")
    check(client.post(f"/evaluaciones/{PSI['id']}/resultado", data={"conclusion": "apto", "version": "0"}).status_code == 400, "una conclusión médica no sirve para una prueba general")
    r = client.post(f"/evaluaciones/{PSI['id']}/resultado", data={"conclusion": "favorable", "comentarios": "Perfil adecuado", "version": "0"})
    e = r.json()["evaluacion"]
    check(e["estado"] == "con_resultado" and e["conclusionTexto"] == "Favorable" and e["registradaPor"] == admin.nombre, "Con resultado: Favorable, con quién lo capturó")
    TEC = tipos["tecnica"]["id"]
    check(client.post(f"/evaluaciones/{TEC}/resultado", data={"comentarios": "", "version": "0"}).status_code == 400, "resultado vacío → 400")
    r = client.post(f"/evaluaciones/{TEC}/resultado", data={"comentarios": "Resolvió 4 de 5 casos", "conclusion": "con_observaciones", "version": "0"},
                    files={"archivos": ("caso.pdf", PDF_MIN, "application/pdf")})
    e = r.json()["evaluacion"]
    check(e["estado"] == "con_resultado" and e["adjuntos"] and e["registradaPor"] == admin.nombre and e["registradaEn"] and e["conclusionTexto"] == "Con observaciones",
          "adjunto cargado a mano: quién lo registró y cuándo; Con observaciones")
    check(client.get(f"/evaluaciones/{TEC}/adjuntos/{e['adjuntos'][0]['id']}").status_code == 200, "el adjunto general se descarga")

    print("\n--- 7. Evaluación médica: permisos sobre el informe ---")
    como(rh)
    check(client.post(f"/evaluaciones/{MED['id']}/resultado", data={"comentarios": "x", "version": "0"}).status_code == 403, "sin permiso no se carga el resultado médico")
    como(admin)
    check(client.post(f"/evaluaciones/{MED['id']}/resultado", data={"conclusion": "favorable", "version": "0"}).status_code == 400, "la médica solo acepta Apto / Apto con restricciones / No apto")
    r = client.post(f"/evaluaciones/{MED['id']}/resultado", data={"conclusion": "apto_con_restricciones", "comentarios": "Hipertensión controlada", "version": "0"},
                    files={"archivos": ("medico.pdf", PDF_MIN, "application/pdf")})
    check(r.status_code == 200 and r.json()["evaluacion"]["comentarios"] == "Hipertensión controlada", "con permiso (Administrador) se carga y se ve completo")
    como(rh)
    vista = next(x for x in client.get(f"/evaluaciones/postulaciones/{P}").json() if x["id"] == MED["id"])
    check(vista["restringido"] and vista["comentarios"] == "" and vista["adjuntos"] == [], "sin permiso NO viaja el informe (ni comentarios, ni adjuntos)")
    check(vista["estadoTexto"] == "Con resultado" and vista["conclusionTexto"] == "Apto con restricciones", "…pero sí el estado y la conclusión")
    med_db = db.query(Evaluacion).filter(Evaluacion.codigo == MED["id"]).one()
    db.refresh(med_db)
    aid = med_db.adjuntos[0]["id"]
    check(client.get(f"/evaluaciones/{MED['id']}/adjuntos/{aid}").status_code == 403, "descargar el informe médico sin permiso → 403")
    tec_vista = next(x for x in client.get(f"/evaluaciones/postulaciones/{P}").json() if x["id"] == TEC)
    check(not tec_vista["restringido"] and tec_vista["comentarios"], "la restricción es solo para lo médico")
    como(admin)
    r = client.patch(f"/auth/usuarios/{rh.id}", json={"acceso_informes_medicos": True})
    check(r.status_code == 200 and r.json()["puedeVerInformeMedico"], "un Administrador otorga el permiso de informes médicos")
    db.expire_all()
    rh = db.get(Usuario, rh.id)
    como(rh)
    antes = db.query(Bitacora).filter(Bitacora.accion == "informe_medico_consultado").count()
    check(client.get(f"/evaluaciones/{MED['id']}/adjuntos/{aid}").status_code == 200
          and db.query(Bitacora).filter(Bitacora.accion == "informe_medico_consultado").count() == antes + 1, "con el permiso ya lo descarga (y queda en bitácora)")
    como(admin)

    print("\n--- 8. Cancelada ---")
    REF = tipos["referencias"]["id"]
    r = client.post(f"/evaluaciones/{REF}/cancelar", json={"motivo": "Las referencias no respondieron"})
    check(r.json()["evaluacion"]["estado"] == "cancelada" and r.json()["evaluacion"]["motivoEstado"] == "Las referencias no respondieron", "Cancelada con su motivo")
    check(client.post(f"/evaluaciones/{REF}/realizada").status_code == 409, "una cancelada ya no se mueve")
    eventos = client.get(f"/evaluaciones/{REF}").json()["eventos"]
    check(len(eventos) >= 2 and eventos[-1]["estadoNuevo"] == "cancelada", "cada cambio queda en el historial de la evaluación")
    check(client.get(f"/candidatos/{P}").json()["etapa"] == "Evaluación", "nada de lo anterior movió el pipeline")

    print("\n--- 9. Vacante: sugerencias y «Avisar antes de Onboarding» ---")
    r = client.patch(f"/vacantes/{vac['id']}", json={
        "evaluaciones_sugeridas": [{"tipo": "psicometrica", "prueba_id": CLEAVER}, {"tipo": "medico"}, {"tipo": "referencias"}, {"tipo": "tarot"}, {"tipo": "medica"}],
        "avisar_evaluaciones_antes_onboarding": True,
    })
    v2 = r.json()
    check(r.status_code == 200 and [s["tipo"] for s in v2["evaluacionesSugeridas"]] == ["psicometrica", "medica", "referencias"] and v2["evaluacionesSugeridas"][0]["nombre"] == "Cleaver",
          "sugerencias limpias (sin tipos inválidos ni duplicados; «medico» previo = «medica») con el nombre del catálogo")
    check(v2["avisarEvaluacionesAntesOnboarding"] is True, "casilla «Avisar antes de Onboarding»")
    r = client.post("/candidatos", json={"nombre": "Diego Onboarding", "telefono": "5556565656", "correo": "diego@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P3 = r.json()["id"]
    EXP3 = client.patch(f"/candidatos/{P3}/etapa", json={"etapa": "Contratación", "manual": True}).json()["expedienteId"]
    crear(P3, tipo="psicometrica", forma="integrada", prueba_id=CLEAVER)
    avisos = client.get(f"/onboarding/expedientes/{EXP3}/resumen").json()["avisosEvaluaciones"]
    check(any("Médica" in a for a in avisos) and any("Referencias" in a for a in avisos) and any("Cleaver" in a and "Pendiente" in a for a in avisos),
          f"el resumen de «Enviar a Onboarding» avisa lo sugerido que falta y lo que no tiene resultado ({len(avisos)} avisos)")
    client.patch(f"/vacantes/{vac['id']}", json={"avisar_evaluaciones_antes_onboarding": False})
    check(client.get(f"/onboarding/expedientes/{EXP3}/resumen").json()["avisosEvaluaciones"] == [], "sin la casilla no hay aviso (nunca bloquea)")

    db.close()

print(f"\n🎉 Evaluaciones y verificaciones: {OK} verificaciones OK")
