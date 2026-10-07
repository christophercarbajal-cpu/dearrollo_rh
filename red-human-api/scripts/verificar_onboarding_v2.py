"""Regresión de Onboarding v2 (2026-09-28). Base desechable y SIN claves externas (modo demo).

    .venv/Scripts/python.exe scripts/verificar_onboarding_v2.py

Fase 1: Plantillas de Onboarding (CRUD en Configuración, jerarquía puesto > empresa > predeterminada, la
configuración aplicada es una COPIA), documentos con estados Pendiente / Por revisar / Aprobado / Rechazado /
No aplica («No aplica» solo RH y con motivo, fuera del porcentaje, no se le pide al candidato) y tareas
Pendiente / Realizada / Cancelada (motivo obligatorio; tres fijas obligatorias que no se cancelan).
Fase 2: el porcentaje cuenta solo Aprobados; «Iniciar Onboarding» es el único paso a Onboarding (requisitos:
condiciones + consentimiento), resumen precargado, «Cambiar selección», avisos a responsables, contrato firmado
como documento interno, y Modo Prueba se salta las reglas nuevas.
Fase 3: tablero (aprobados vs pendientes; «No aplica»/«Cancelada» aparte), botón legado, recálculo de plazos y
atrasos, «Confirmar ingreso» → «Dar de alta» → «Cerrar Onboarding» (manual) y «No ingresó».
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

_dir = tempfile.mkdtemp(prefix="rh_onb2_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "onb2.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-onb2"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cliente, Cuenta, Curso, Expediente, PlantillaOnboarding, TareaOnboarding, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import onboarding as onb  # noqa: E402
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


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Onboarding v2", nombre_comercial="Empresa O2", razon_social="Empresa O2 SA de CV", estado="Activa")
    otra = Cuenta(nombre="Otra", nombre_comercial="Otra", estado="Activa")
    db.add_all([cuenta, otra])
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.add(Cliente(cuenta_id=cuenta.id, nombre="Cliente Logística", razon_social="Logística Norte SA", estado="Activo"))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    curso = Curso(codigo="CUR-ONB", titulo="Inducción general", cuenta_id=cuenta.id, estado="Publicado")
    curso_ajeno = Curso(codigo="CUR-OTRA", titulo="De otra Cuenta", cuenta_id=otra.id)
    db.add_all([curso, curso_ajeno])
    cfg = obtener(db)
    cfg.modo_prueba = False
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    print("\n--- 1. Plantillas de Onboarding (Configuración) ---")
    op = client.get("/onboarding/plantillas/opciones").json()
    check(op["razonesSociales"][0] == "Empresa O2 SA de CV" and "Logística Norte SA" in op["razonesSociales"], "opciones: razones sociales de la Cuenta y de sus Clientes")
    check([t["clave"] for t in op["tareasFijas"]] == ["contrato_firmado", "alta_imss_nomina", "confirmar_ingreso"], "las tres tareas fijas: Contrato firmado, Alta IMSS/nómina, Confirmar ingreso")
    check(op["estadosDocumento"] == ["Pendiente", "Por revisar", "Aprobado", "Rechazado", "No aplica"], "vocabulario de estados de documento")
    check(any(c["titulo"] == "Inducción general" for c in op["cursos"]) and not any(c["titulo"] == "De otra Cuenta" for c in op["cursos"]), "cursos de inducción: solo los de la Cuenta")

    r = client.post("/onboarding/plantillas", json={"nombre": "General", "alcance": "empresa"})
    check(r.status_code == 201 and len(r.json()["documentos"]) == 6, "plantilla de empresa sin documentos = los 6 predeterminados")
    GEN = r.json()["id"]
    check(r.json()["plazos"]["documentos"] == -3 and r.json()["plazos"]["confirmar_ingreso"] == 0, "plazos predeterminados relativos a la fecha de ingreso")
    check(client.post("/onboarding/plantillas", json={"nombre": "Otra general", "alcance": "empresa"}).status_code == 409,
          "no hay dos plantillas activas para el mismo alcance")
    check(client.post("/onboarding/plantillas", json={"nombre": "X", "alcance": "empresa", "empresa": "Inventada SA"}).status_code == 400,
          "la empresa debe ser una razón social configurada (nunca texto libre)")
    check(client.post("/onboarding/plantillas", json={"nombre": "X", "alcance": "puesto"}).status_code == 400, "plantilla de puesto sin puesto → 400")
    check(client.post("/onboarding/plantillas", json={"nombre": "X", "alcance": "planeta"}).status_code == 400, "alcance inválido → 400")
    check(client.post("/onboarding/plantillas", json={"nombre": "X", "alcance": "empresa", "empresa": "Logística Norte SA", "documentos": []}).status_code == 400,
          "una plantilla sin documentos requeridos → 400")
    check(client.post("/onboarding/plantillas", json={"nombre": "X", "alcance": "empresa", "empresa": "Logística Norte SA", "curso_induccion_id": curso_ajeno.id}).status_code == 400,
          "curso de inducción de otra Cuenta → 400")

    r = client.post("/onboarding/plantillas", json={
        "nombre": "Logística", "alcance": "empresa", "empresa": "logística norte sa",
        "documentos": [{"tipo": "Identificación oficial"}, {"tipo": "CURP"}, {"tipo": "Licencia de manejo", "obligatorio": False}, {"tipo": "curp"}],
        "responsables": {"alta_imss_nomina": "Nómina Norte"},
    })
    check(r.status_code == 201 and r.json()["empresa"] == "Logística Norte SA", "la razón social se guarda con su escritura oficial")
    check([d["tipo"] for d in r.json()["documentos"]] == ["Identificación oficial", "CURP", "Licencia de manejo"], "documentos sin duplicados (CURP/curp)")
    LOG = r.json()["id"]

    r = client.post("/onboarding/plantillas", json={
        "nombre": "Chofer", "alcance": "puesto", "puesto": "Chofer repartidor",
        "documentos": [{"tipo": "Identificación oficial"}, {"tipo": "Licencia federal"}, {"tipo": "Carta de no antecedentes", "obligatorio": False}],
        "recursos": [{"nombre": "Unidad asignada", "tipo": "equipo", "responsable": "Flotilla", "dias": -1},
                     {"nombre": "Acceso a la app de rutas", "tipo": "accesos", "responsable": "TI", "dias": 0},
                     {"nombre": "Correo corporativo", "tipo": "raro", "dias": "x"}],
        "responsables": {"contrato_firmado": "Jurídico", "confirmar_ingreso": "Jefe de patio"},
        "plazos": {"contrato_firmado": -2, "documentos": "no-num"},
        "curso_induccion_id": curso.id,
    })
    check(r.status_code == 201, "plantilla de puesto con recursos internos, responsables, plazos y curso")
    CH = r.json()
    check(CH["cursoInduccion"] == "Inducción general", "la plantilla muestra el curso de inducción")
    check([x["tipo"] for x in CH["recursos"]] == ["equipo", "accesos", "otro"] and CH["recursos"][2]["dias"] == 0,
          "recursos normalizados (tipo desconocido → otro, días inválidos → 0)")
    check(CH["plazos"]["contrato_firmado"] == -2 and CH["plazos"]["documentos"] == -3, "plazos: lo capturado se respeta y lo inválido queda en el predeterminado")

    lista = client.get("/onboarding/plantillas").json()
    check([p["nombre"] for p in lista][0] == "Chofer", "el listado muestra primero las de puesto (prevalecen)")

    print("\n--- 2. Jerarquía: puesto > empresa > predeterminada ---")
    r = client.get("/onboarding/plantillas/resolver", params={"puesto": "chofer  REPARTIDOR", "empresa": "Empresa O2 SA de CV"}).json()
    check(r["origen"] == "puesto" and r["plantilla"] == "Chofer", "la plantilla de PUESTO prevalece (sin distinguir mayúsculas/espacios)")
    r = client.get("/onboarding/plantillas/resolver", params={"puesto": "Almacenista", "empresa": "Logística Norte SA"}).json()
    check(r["origen"] == "empresa" and r["plantilla"] == "Logística", "sin plantilla de puesto: la de la EMPRESA contratante gana a la general")
    r = client.get("/onboarding/plantillas/resolver", params={"puesto": "Almacenista", "empresa": "Empresa O2 SA de CV"}).json()
    check(r["origen"] == "empresa" and r["plantilla"] == "General", "otra razón social → la plantilla general de la Cuenta")
    client.delete(f"/onboarding/plantillas/{GEN}")
    r = client.get("/onboarding/plantillas/resolver", params={"puesto": "Almacenista"}).json()
    check(r["origen"] == "predeterminada" and len(r["documentos"]) == 6, "sin plantilla activa → configuración predeterminada")
    check(all(p["id"] != GEN for p in client.get("/onboarding/plantillas").json()), "«eliminar» = desactivar (sale del listado)")
    check(any(p["id"] == GEN for p in client.get("/onboarding/plantillas", params={"incluir_inactivas": True}).json()), "…pero sigue existiendo (baja lógica)")
    r = client.patch(f"/onboarding/plantillas/{GEN}", json={"activa": True})
    check(r.status_code == 200 and r.json()["activa"], "se puede reactivar")
    check(client.patch(f"/onboarding/plantillas/{LOG}", json={"empresa": ""}).status_code == 409, "editar hacia un alcance ya ocupado → 409")
    check(client.get(f"/onboarding/plantillas/{LOG}").json()["empresa"] == "Logística Norte SA", "…y la edición rechazada no dejó cambios a medias")
    app.dependency_overrides[cuenta_actual] = lambda: otra
    check(client.get(f"/onboarding/plantillas/{LOG}").status_code == 404, "otra Cuenta no ve las plantillas (404)")
    check(client.get("/onboarding/plantillas").json() == [], "…ni en su listado")
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    print("\n--- 3. La configuración aplicada es una COPIA ---")
    cfg_p = onb.configuracion_para(db, cuenta.id, "Chofer repartidor", "")
    cfg_p["documentos"].append({"tipo": "Solo para esta persona", "obligatorio": True})
    cfg_p["recursos"].pop()
    db.expire_all()
    check(len(db.get(PlantillaOnboarding, CH["id"]).documentos) == 3 and len(db.get(PlantillaOnboarding, CH["id"]).recursos) == 3,
          "cambiar la selección de una persona no altera la plantilla")

    print("\n--- 4. Documentos: Pendiente / Por revisar / Aprobado / Rechazado / No aplica ---")
    vac = client.get("/vacantes").json()[0]
    r = client.post("/candidatos", json={"nombre": "Jorge Pérez", "telefono": "5511112222", "correo": "jorge@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    EXP = client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"}).json()["expedienteId"]
    e = db.get(Expediente, EXP)
    docs = {d["nombre"]: d for d in client.get(f"/contratacion/expedientes/{EXP}").json()["documentos"]}
    check(all(d["estadoOnboarding"] == "Pendiente" for d in docs.values()), "al abrir el expediente todos los documentos están «Pendiente»")

    client.post(f"/contratacion/expedientes/{EXP}/documentos", data={"tipo": "CURP"}, files={"archivo": ("curp.pdf", PDF_MIN, "application/pdf")})
    d = {x["nombre"]: x for x in client.get(f"/contratacion/expedientes/{EXP}").json()["documentos"]}["CURP"]
    check(d["estadoOnboarding"] == "Por revisar", f"un documento subido sin revisión de RH queda «Por revisar» ({d['estado']})")
    r = client.post(f"/contratacion/expedientes/{EXP}/documentos/estado", json={"tipo": "CURP", "estado": "aprobado"})
    d = {x["nombre"]: x for x in r.json()["documentos"]}["CURP"]
    check(d["estado"] == "recibido" and d["estadoOnboarding"] == "Aprobado", "RH lo aprueba (alias «aprobado» → recibido) = «Aprobado»")
    r = client.post(f"/contratacion/expedientes/{EXP}/documentos/estado", json={"tipo": "Comprobante de domicilio", "estado": "rechazado", "notas": "Ilegible"})
    check({x["nombre"]: x for x in r.json()["documentos"]}["Comprobante de domicilio"]["estadoOnboarding"] == "Rechazado", "«Rechazado»")
    check(client.post(f"/contratacion/expedientes/{EXP}/documentos/estado", json={"tipo": "Número de Seguridad Social", "estado": "aprobado"}).status_code == 409,
          "aprobar sin archivo ni entrega física → 409 (igual que antes)")

    antes = db.get(Expediente, EXP)
    db.refresh(antes)
    progreso_antes = antes.progreso
    r = client.post(f"/contratacion/expedientes/{EXP}/documentos/estado", json={"tipo": "Cuenta bancaria / CLABE", "estado": "no_aplica"})
    check(r.status_code == 400 and "motivo" in r.json()["detail"], "«No aplica» sin motivo → 400")
    r = client.post(f"/contratacion/expedientes/{EXP}/documentos/estado", json={"tipo": "Cuenta bancaria / CLABE", "estado": "No aplica", "motivo": "Se le paga con cuenta de nómina existente"})
    d = {x["nombre"]: x for x in r.json()["documentos"]}["Cuenta bancaria / CLABE"]
    check(r.status_code == 200 and d["estadoOnboarding"] == "No aplica" and d["motivoNoAplica"].startswith("Se le paga") and d["noAplicaPor"] == admin.nombre,
          "RH marca «No aplica» con motivo y queda quién lo decidió")
    db.refresh(antes)
    check(antes.progreso > progreso_antes and "Cuenta bancaria / CLABE" not in antes.pendientes,
          f"«No aplica» sale del porcentaje y de los pendientes ({progreso_antes} % → {antes.progreso} %)")
    tok = antes.token
    if tok:
        pub = client.get(f"/expedientes/publica/{tok}").json()
        check(all(x["tipo"] != "Cuenta bancaria / CLABE" for x in pub["documentos"]), "la liga del candidato no le pide un documento «No aplica»")
        r = client.post(f"/expedientes/publica/{tok}/documentos", data={"tipo": "Cuenta bancaria / CLABE"}, files={"archivo": ("c.pdf", PDF_MIN, "application/pdf")})
        check(r.status_code == 409, "el candidato no puede subir a un documento «No aplica»")
    from app.routers.contratacion import documento_para_adjunto  # noqa: E402

    db.refresh(antes)
    destino = documento_para_adjunto(db, antes, "mi clabe", "estado_cuenta.pdf")
    check(destino.tipo != "Cuenta bancaria / CLABE", f"un adjunto de WhatsApp nunca cae en un documento «No aplica» (→ {destino.tipo})")
    db.rollback()
    r = client.post(f"/contratacion/expedientes/{EXP}/documentos/estado", json={"tipo": "Cuenta bancaria / CLABE", "estado": "pendiente"})
    d = {x["nombre"]: x for x in r.json()["documentos"]}["Cuenta bancaria / CLABE"]
    check(d["estadoOnboarding"] == "Pendiente" and d["motivoNoAplica"] == "", "RH puede volver a pedirlo: regresa a «Pendiente» y se limpia el motivo")

    print("\n--- 5. Tareas: Pendiente / Realizada / Cancelada ---")
    db.expire_all()
    e = db.get(Expediente, EXP)
    e.fecha_ingreso = datetime(2026, 10, 12, tzinfo=timezone.utc)
    db.commit()
    cfg_p = onb.configuracion_para(db, cuenta.id, "Chofer repartidor", "")
    tareas = onb.generar_tareas(db, e, cuenta.id, cfg_p, admin.nombre)
    db.commit()
    check([t.clave for t in tareas[:3]] == ["contrato_firmado", "alta_imss_nomina", "confirmar_ingreso"] and all(t.fija and t.obligatoria for t in tareas[:3]),
          "se generan las tres tareas fijas y obligatorias")
    check(len(tareas) == 6 and tareas[3].nombre == "Unidad asignada", "más una tarea por cada recurso interno de la plantilla")
    fija = {t.clave: t for t in tareas}
    check(fija["contrato_firmado"].responsable == "Jurídico" and fija["confirmar_ingreso"].responsable == "Jefe de patio", "responsables por defecto de la plantilla")
    check(fija["contrato_firmado"].fecha_limite.date() == datetime(2026, 10, 10).date(), "plazo relativo: contrato 2 días antes del ingreso")
    check(tareas[3].fecha_limite.date() == datetime(2026, 10, 11).date(), "plazo relativo del recurso: 1 día antes")
    check(len(onb.generar_tareas(db, e, cuenta.id, cfg_p, admin.nombre)) == 6, "generar de nuevo es idempotente (no duplica)")

    r = client.get(f"/onboarding/expedientes/{EXP}/tareas").json()
    check(len(r) == 6 and r[0]["estado"] == "pendiente", "GET tareas del expediente")
    T_REC = r[3]["id"]
    T_CONTRATO = r[0]["id"]
    T_IMSS = r[1]["id"]
    r = client.patch(f"/onboarding/tareas/{T_REC}", json={"estado": "cancelada"})
    check(r.status_code == 400 and "motivo" in r.json()["detail"], "cancelar sin motivo → 400")
    r = client.patch(f"/onboarding/tareas/{T_REC}", json={"estado": "cancelada", "motivo": "Usará su propio vehículo"})
    check(r.status_code == 200 and r.json()["estado"] == "cancelada" and r.json()["canceladaPor"] == admin.nombre, "cancelada con motivo y quién")
    r = client.patch(f"/onboarding/tareas/{T_REC}", json={"estado": "pendiente"})
    check(r.json()["estado"] == "pendiente" and r.json()["motivoCancelacion"] == "", "se puede reabrir (queda en bitácora)")
    r = client.patch(f"/onboarding/tareas/{T_IMSS}", json={"estado": "cancelada", "motivo": "x"})
    check(r.status_code == 409 and "fija" in r.json()["detail"], "una tarea fija no se cancela por separado")
    r = client.patch(f"/onboarding/tareas/{T_CONTRATO}", json={"estado": "realizada"})
    check(r.status_code == 409 and "contrato firmado" in r.json()["detail"].lower(), "«Contrato firmado» no se marca a mano: exige cargar el contrato firmado")
    r = client.patch(f"/onboarding/tareas/{T_IMSS}", json={"estado": "realizada"})
    check(r.json()["estado"] == "realizada" and r.json()["realizadaPor"] == admin.nombre, "«Alta IMSS / nómina» realizada con quién")
    check(client.patch(f"/onboarding/tareas/{T_IMSS}", json={"estado": "lista"}).status_code == 400, "estado inválido → 400")
    r = client.post(f"/onboarding/expedientes/{EXP}/tareas", json={"nombre": "Uniforme talla M", "tipo": "equipo", "dias": -5})
    check(r.status_code == 201 and r.json()["fechaLimite"].startswith("2026-10-07"), "tarea adicional solo para esta persona con su plazo")
    check(client.post(f"/onboarding/expedientes/{EXP}/tareas", json={"nombre": "uniforme talla m"}).status_code == 409, "sin tareas duplicadas")
    check(len(db.get(PlantillaOnboarding, CH["id"]).recursos) == 3, "…y la plantilla sigue intacta")

    print("\n--- 6. Plazos: recálculo y atraso ---")
    db.expire_all()
    e = db.get(Expediente, EXP)
    e.fecha_ingreso = datetime(2026, 10, 19, tzinfo=timezone.utc)
    n = onb.recalcular_fechas(db, e)
    db.commit()
    t = {x.clave: x for x in onb.tareas_de(db, e)}
    check(t["contrato_firmado"].fecha_limite.date() == datetime(2026, 10, 17).date(), "al cambiar la fecha de ingreso se recalculan los plazos pendientes")
    check(t["alta_imss_nomina"].fecha_limite.date() == datetime(2026, 10, 12).date() and n >= 1, "…pero no los de tareas ya realizadas")
    vieja = TareaOnboarding(cuenta_id=cuenta.id, expediente_id=EXP, clave="otra", nombre="Vencida", fecha_limite=datetime.now(timezone.utc) - timedelta(days=2))
    db.add(vieja)
    db.commit()
    check(onb.atrasada(vieja) and not onb.atrasada(t["alta_imss_nomina"]), "una pendiente vencida es «atrasada»; una realizada nunca")
    app.dependency_overrides[cuenta_actual] = lambda: otra
    check(client.get(f"/onboarding/expedientes/{EXP}/tareas").status_code == 404, "otra Cuenta no ve las tareas (404)")
    check(client.patch(f"/onboarding/tareas/{T_REC}", json={"estado": "realizada"}).status_code == 404, "…ni las modifica")
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    print("\n--- 7. Sin tocar la etapa (B5) ---")
    etapa = client.get(f"/candidatos/{P}").json()
    check(etapa.get("etapa") == "Contratación", f"nada de lo anterior movió la etapa ({etapa.get('etapa')})")

    # ======================= FASE 2: de Contratación a Onboarding =======================
    print("\n--- 8. Fase 2 · el porcentaje solo cuenta documentos Aprobados ---")
    r = client.post("/candidatos", json={"nombre": "Luisa Chofer", "telefono": "5533334444", "correo": "luisa@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P2 = r.json()["id"]
    EXP2 = client.patch(f"/candidatos/{P2}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"}).json()["expedienteId"]
    client.post(f"/contratacion/expedientes/{EXP2}/documentos", data={"tipo": "CURP"}, files={"archivo": ("curp.pdf", PDF_MIN, "application/pdf")})
    x = client.get(f"/contratacion/expedientes/{EXP2}").json()
    check(x["progreso"] == 0 and "CURP" in x["noAprobados"], f"subido pero sin aprobar NO suma al porcentaje ({x['progreso']} %)")
    x = client.post(f"/contratacion/expedientes/{EXP2}/documentos/estado", json={"tipo": "CURP", "estado": "aprobado"}).json()
    check(x["progreso"] == 17 and "CURP" not in x["noAprobados"], f"aprobado por RH sí suma ({x['progreso']} %)")
    r = client.get(f"/contratacion/expedientes/{EXP2}/contrato")
    check(r.status_code == 409 and "Aprobados" in r.json()["detail"], "el contrato exige el 100 % de documentos Aprobados")

    print("\n--- 9. «Iniciar Onboarding» es el ÚNICO gatillo hacia Onboarding ---")
    r = client.patch(f"/candidatos/{P2}/etapa", json={"etapa": "Onboarding", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    check(r.status_code == 409 and "Iniciar Onboarding" in r.json()["detail"], "mover a Onboarding por la etapa → 409 (Modo Prueba apagado)")
    res = client.get(f"/onboarding/expedientes/{EXP2}/resumen").json()
    check(res["puedeIniciar"] is False and "Sueldo" in res["requisitos"]["faltan"] and "Fecha de ingreso" in res["requisitos"]["faltan"],
          f"sin condiciones no se puede enviar a Onboarding (faltan: {res['requisitos']['faltan']})")
    r = client.post(f"/onboarding/expedientes/{EXP2}/iniciar", json={"documentos": [{"tipo": "CURP"}]})
    check(r.status_code == 409 and "Sueldo" in r.json()["detail"], "el backend lo refuerza (409 con lo que falta)")
    client.patch(f"/candidatos/{P2}/condiciones-contratacion", json={
        "puesto": "Chofer repartidor", "sueldo": "$14,000 mensuales", "tipo_contratacion": "Tiempo indeterminado", "fecha_ingreso": "2026-11-02",
    })
    client.post(f"/candidatos/{P2}/consentimiento", json={"acepta": False, "medio": "verbal"})
    res = client.get(f"/onboarding/expedientes/{EXP2}/resumen").json()
    check(res["requisitos"]["faltan"] == ["Consentimiento de privacidad (LFPDPPP)"] and not res["puedeIniciar"],
          "sin consentimiento de privacidad tampoco se habilita")
    client.post(f"/candidatos/{P2}/consentimiento", json={"acepta": True, "medio": "escrito"})
    res = client.get(f"/onboarding/expedientes/{EXP2}/resumen").json()
    check(res["puedeIniciar"] and res["requisitos"]["completos"], "puesto + sueldo + tipo + fecha + consentimiento → habilitado")
    cfg = res["configuracion"]
    check(cfg["origen"] == "puesto" and cfg["plantilla"] == "Chofer" and [d["tipo"] for d in cfg["documentos"]] == ["Identificación oficial", "Licencia federal", "Carta de no antecedentes"],
          "el resumen viene PRECARGADO con la plantilla del puesto")

    print("\n--- 10. «Cambiar selección» (solo para esta persona) + Iniciar ---")
    admin_nombre = admin.nombre
    seleccion = {
        "omitir_obligatorios": True, "comentario": "Prueba: carta/contrato fuera del alcance de esta verificación",
        "documentos": [{"tipo": "Identificación oficial"}, {"tipo": "CURP"}, {"tipo": "Licencia federal"}, {"tipo": "Número de Seguridad Social"}],
        "recursos": [{"nombre": "Unidad asignada", "tipo": "equipo", "responsable": admin_nombre, "dias": -1}],
        "responsables": {**cfg["responsables"], "alta_imss_nomina": admin_nombre},
        "plazos": cfg["plazos"], "plantilla_id": cfg["plantillaId"], "curso_induccion_id": cfg["cursoInduccionId"],
    }
    r = client.post(f"/onboarding/expedientes/{EXP2}/iniciar", json=seleccion)
    check(r.status_code == 200, f"«Iniciar Onboarding» ({r.status_code} {r.text[:200] if r.status_code != 200 else ''})")
    out = r.json()
    check(out["candidato"]["etapa"] == "Onboarding", "cambia la etapa a Onboarding (el mismo movimiento de siempre)")
    check([t["clave"] for t in out["tareas"]][:3] == ["contrato_firmado", "alta_imss_nomina", "confirmar_ingreso"] and len(out["tareas"]) == 4,
          "genera las tres tareas fijas + el recurso seleccionado")
    check(out["documentosAgregados"] == ["Licencia federal"], "agrega los documentos seleccionados que faltaban")
    check(set(out["documentosNoAplica"]) == {"Constancia de Situación Fiscal / RFC", "Comprobante de domicilio", "Cuenta bancaria / CLABE"},
          "lo que RH quitó de la selección queda «No aplica» (nunca se borra)")
    check(out["documentosConservados"] == [], "nada entregado se tocó")
    docs2 = {d["nombre"]: d for d in client.get(f"/contratacion/expedientes/{EXP2}").json()["documentos"]}
    check(docs2["Cuenta bancaria / CLABE"]["estadoOnboarding"] == "No aplica" and docs2["CURP"]["estadoOnboarding"] == "Aprobado", "estados correctos tras iniciar")
    check(len(db.get(PlantillaOnboarding, CH["id"]).documentos) == 3, "la plantilla no cambió con la selección de la persona")
    avisos = {a["destinatario"]: a for a in out["avisosResponsables"]}
    check(admin_nombre in avisos and avisos[admin_nombre]["destino"] == admin.correo and "RESEND" in avisos[admin_nombre]["detalle"],
          "avisa por correo al responsable interno que es usuario de la Cuenta (sin Resend: queda visible que no salió)")
    check(avisos.get("Jurídico", {}).get("enviado") is False and "usuario de la Cuenta" in avisos["Jurídico"]["detalle"],
          "un responsable que no es usuario se reporta, nunca en silencio")
    check(isinstance(out["solicitudDocumentos"], list) and out["solicitudDocumentos"], "hace la primera solicitud de documentos al candidato")
    check(out["cursoInduccion"] and out["cursoInduccion"]["curso"] == "Inducción general", "asigna el curso de inducción")
    check(client.post(f"/onboarding/expedientes/{EXP2}/iniciar", json=seleccion).status_code == 409, "no se inicia dos veces")

    print("\n--- 11. Contrato: borrador y contrato firmado ---")
    tareas2 = {t["clave"]: t for t in client.get(f"/onboarding/expedientes/{EXP2}/tareas").json()}
    check(client.patch(f"/onboarding/tareas/{tareas2['contrato_firmado']['id']}", json={"estado": "realizada"}).status_code == 409,
          "«Contrato firmado» no se marca a mano")
    check(client.patch(f"/contratacion/expedientes/{EXP2}/preparacion", json={"contrato": "Firmado"}).status_code == 409,
          "…tampoco por el checklist viejo")
    r = client.post(f"/onboarding/expedientes/{EXP2}/contrato-firmado", files={"archivo": ("firma.png", b"\x89PNG\r\n\x1a\n" + b"0" * 800, "image/png")})
    check(r.status_code == 400 and "PDF" in r.json()["detail"], "el contrato firmado debe ser PDF")
    r = client.post(f"/onboarding/expedientes/{EXP2}/contrato-firmado", files={"archivo": ("contrato-firmado.pdf", PDF_MIN, "application/pdf")})
    check(r.status_code == 200 and r.json()["tarea"]["estado"] == "realizada" and r.json()["tarea"]["realizadaPor"] == admin_nombre,
          "«Cargar contrato firmado» → tarea Realizada con quién y cuándo")
    x = client.get(f"/contratacion/expedientes/{EXP2}").json()
    firmado = next(d for d in x["documentos"] if d["nombre"] == "Contrato firmado")
    check(firmado["interno"] and x["contrato"] == "Firmado", "se guarda como documento interno y el campo viejo se deriva de la tarea")
    check("Contrato firmado" not in x["noAprobados"] and x["progreso"] == 25, f"el contrato firmado NO suma al porcentaje ({x['progreso']} %)")
    check(client.get(f"/onboarding/expedientes/{EXP2}/contrato-firmado").status_code == 200, "se puede descargar")
    e2 = db.get(Expediente, EXP2)
    db.refresh(e2)
    pub = client.get(f"/expedientes/publica/{e2.token}").json()
    check(all(d["tipo"] != "Contrato firmado" for d in pub["documentos"]), "el candidato no ve el contrato firmado como documento por subir")
    r = client.post(f"/contratacion/expedientes/{EXP2}/documentos", data={"tipo": "Contrato firmado"}, files={"archivo": ("c.pdf", PDF_MIN, "application/pdf")})
    check(r.status_code == 409, "el contrato firmado no se sube por la vía de documentos del candidato")
    r = client.patch(f"/contratacion/expedientes/{EXP2}/preparacion", json={"alta_administrativa": "Realizada"})
    t_imss = {t["clave"]: t for t in client.get(f"/onboarding/expedientes/{EXP2}/tareas").json()}["alta_imss_nomina"]
    check(r.status_code == 200 and t_imss["estado"] == "realizada" and r.json()["altaAdministrativa"] == "Realizada",
          "el checklist viejo ahora actualiza la TAREA (fuente de verdad)")

    print("\n--- 12. Modo Prueba se salta las reglas nuevas ---")
    r = client.post("/candidatos", json={"nombre": "Prueba Directa", "telefono": "5599990000", "correo": "p@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P3 = r.json()["id"]
    EXP3 = client.patch(f"/candidatos/{P3}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"}).json()["expedienteId"]
    cfg_sis = obtener(db)
    cfg_sis.modo_prueba = True
    db.commit()
    check(client.get(f"/onboarding/expedientes/{EXP3}/resumen").json()["puedeIniciar"], "Modo Prueba: se puede iniciar sin condiciones")
    check(client.get(f"/contratacion/expedientes/{EXP3}/contrato").status_code == 200, "Modo Prueba: el contrato (borrador) se genera sin el 100 %")
    r = client.patch(f"/candidatos/{P3}/etapa", json={"etapa": "Onboarding", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    check(r.status_code == 200 and r.json()["etapa"] == "Onboarding", "Modo Prueba: mover directo a Onboarding sí se permite")
    check(len(client.get(f"/onboarding/expedientes/{EXP3}/tareas").json()) == 3, "…y las tareas fijas nacen igual")
    cfg_sis.modo_prueba = False
    db.commit()

    # ======================= FASE 3: gestión activa, tablero y cierre =======================
    from datetime import date  # noqa: E402

    from app.models import Postulacion  # noqa: E402

    HOY = date.today().isoformat()
    print("\n--- 13. Tablero: aprobados vs pendientes, «No aplica» y «Cancelada» aparte ---")
    fila = next(x for x in client.get("/contratacion/expedientes").json() if x["expedienteId"] == EXP2)
    ob = fila["onboarding"]
    check(ob["iniciado"] and ob["documentos"]["total"] == 4 and ob["documentos"]["aprobados"] == ["CURP"] and ob["documentos"]["pct"] == 25,
          f"documentos: 1 aprobado de 4 aplicables = 25 % ({ob['documentos']})")
    check(len(ob["documentos"]["noAplica"]) == 3 and all(x["motivo"] for x in ob["documentos"]["noAplica"]), "«No aplica» fuera del total y aparte con su motivo")
    check(ob["tareas"]["realizadas"] == 2 and ob["tareas"]["total"] == 4 and ob["tareas"]["pct"] == 50, f"tareas: 2 de 4 realizadas = 50 % ({ob['tareas']})")
    t_unidad = next(t for t in client.get(f"/onboarding/expedientes/{EXP2}/tareas").json() if t["nombre"] == "Unidad asignada")
    client.patch(f"/onboarding/tareas/{t_unidad['id']}", json={"estado": "cancelada", "motivo": "Usará su vehículo propio"})
    est = client.get(f"/onboarding/expedientes/{EXP2}/estado").json()
    check(est["tareas"]["total"] == 3 and est["tareas"]["pct"] == 67 and est["tareas"]["canceladas"][0]["motivo"] == "Usará su vehículo propio",
          "una tarea cancelada sale del total (67 %) y se muestra aparte con su motivo")

    print("\n--- 14. Botón legado: «Generar tareas» sin mover la etapa ---")
    r = client.post("/candidatos", json={"nombre": "Pedro Legado", "telefono": "5544445555", "correo": "pedro@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P4 = r.json()["id"]
    EXP4 = client.patch(f"/candidatos/{P4}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"}).json()["expedienteId"]
    check(client.post(f"/onboarding/expedientes/{EXP4}/generar-tareas").status_code == 409, "en Contratación no: ahí se usa «Enviar a Onboarding»")
    db.expire_all()
    post4 = db.query(Postulacion).filter(Postulacion.id == db.get(Expediente, EXP4).postulacion_id).one()
    post4.etapa = "Onboarding"  # simula a quien ya estaba en Onboarding antes de Onboarding v2
    db.commit()
    check(client.get(f"/onboarding/expedientes/{EXP4}/estado").json()["iniciado"] is False, "legado: en Onboarding y sin tareas")
    r = client.post(f"/onboarding/expedientes/{EXP4}/generar-tareas")
    check(r.status_code == 200 and [t["clave"] for t in r.json()["tareas"]] == ["contrato_firmado", "alta_imss_nomina", "confirmar_ingreso"],
          f"«Generar tareas» crea las tres fijas desde la plantilla que aplica ({r.json().get('origen')})")
    check(client.get(f"/candidatos/{P4}").json()["etapa"] == "Onboarding", "…sin mover la etapa")
    check(client.post(f"/onboarding/expedientes/{EXP4}/generar-tareas").status_code == 409, "no se generan dos veces")

    print("\n--- 15. Fechas: recálculo y atrasos ---")
    t2 = {t["clave"]: t for t in client.get(f"/onboarding/expedientes/{EXP2}/tareas").json()}
    check(t2["confirmar_ingreso"]["fechaLimite"].startswith("2026-11-02"), "plazo de «Confirmar ingreso» = fecha prevista")
    check(client.patch(f"/onboarding/tareas/{t2['confirmar_ingreso']['id']}", json={"estado": "realizada"}).status_code == 409,
          "«Confirmar ingreso» no se marca a mano (tiene su acción)")
    client.patch(f"/candidatos/{P2}/condiciones-contratacion", json={
        "puesto": "Chofer repartidor", "sueldo": "$14,000 mensuales", "tipo_contratacion": "Tiempo indeterminado", "fecha_ingreso": "2026-11-09",
    })
    t2 = {t["clave"]: t for t in client.get(f"/onboarding/expedientes/{EXP2}/tareas").json()}
    check(t2["confirmar_ingreso"]["fechaLimite"].startswith("2026-11-09"), "cambiar la fecha prevista recalcula los plazos pendientes")
    check(t2["contrato_firmado"]["estado"] == "realizada", "…las realizadas no se tocan")
    r = client.post(f"/onboarding/expedientes/{EXP2}/tareas", json={"nombre": "Examen de manejo", "dias": -500})
    check(r.json()["atrasada"] is True, "una tarea pendiente con fecha límite vencida es «Atrasada»")
    check(client.get(f"/onboarding/expedientes/{EXP2}/estado").json()["tareas"]["atrasadas"] == 1, "el tablero cuenta las atrasadas")
    T_EXAMEN = r.json()["id"]

    print("\n--- 16. Confirmar ingreso → Dar de alta → Cerrar Onboarding ---")
    for tipo in ("Identificación oficial", "Licencia federal", "Número de Seguridad Social"):
        client.post(f"/contratacion/expedientes/{EXP2}/documentos/estado", json={"tipo": tipo, "estado": "recibido", "recibido_fisico": True})
    check(client.get(f"/contratacion/expedientes/{EXP2}").json()["progreso"] == 100, "expediente al 100 % aprobado")
    check(client.get(f"/onboarding/expedientes/{EXP2}/estado").json()["puedeAlta"] is False, "sin confirmar ingreso, «Dar de alta» deshabilitado")
    r = client.post(f"/contratacion/expedientes/{EXP2}/alta", json={})
    check(r.status_code == 409 and "Confirmar ingreso" in r.json()["detail"], "el backend lo refuerza aunque todo lo demás esté completo")
    check(client.post(f"/onboarding/expedientes/{EXP2}/cerrar").status_code == 409, "no se cierra el Onboarding antes del alta")
    check(client.post(f"/onboarding/expedientes/{EXP2}/confirmar-ingreso", json={"fecha_real": "2099-01-01"}).status_code == 400, "la fecha real no puede ser futura")
    r = client.post(f"/onboarding/expedientes/{EXP2}/confirmar-ingreso", json={"fecha_real": HOY})
    check(r.status_code == 200 and r.json()["ingreso"]["confirmado"] and r.json()["ingreso"]["real"].startswith(HOY) and r.json()["puedeAlta"],
          "«Confirmar ingreso» registra la fecha real (quién y cuándo) y habilita el alta")
    tt = {t["nombre"]: t for t in r.json()["listaTareas"]}
    check(tt["Confirmar ingreso"]["estado"] == "realizada", "…y cierra la tarea fija")
    check(client.patch(f"/onboarding/tareas/{tt['Confirmar ingreso']['id']}", json={"estado": "pendiente"}).status_code == 409,
          "«Confirmar ingreso» no se reabre desde la lista de tareas")
    esperado = (date.fromisoformat(HOY) - timedelta(days=500)).isoformat()
    check(tt["Examen de manejo"]["fechaLimite"].startswith(esperado), "si la fecha real difiere, los plazos pendientes se recalculan contra ella")
    db.expire_all()
    historial = [h["evento"] for h in (db.query(Postulacion).filter(Postulacion.id == db.get(Expediente, EXP2).postulacion_id).one().historial or [])]
    check("ingreso_confirmado" in historial, "queda en el historial del candidato")
    r = client.post(f"/contratacion/expedientes/{EXP2}/alta", json={"notificar": {"candidato_whatsapp": False, "candidato_correo": False}})
    check(r.status_code == 200, f"«Dar de alta» después de confirmar ingreso ({r.status_code})")
    est = client.get(f"/onboarding/expedientes/{EXP2}/estado").json()
    check(est["alta"] and not est["cerrado"] and est["puedeCerrar"] is False and any("Examen de manejo" in f for f in est["faltanCierre"]),
          "tras el alta NO se cierra solo; falta la tarea pendiente")
    r = client.post(f"/onboarding/expedientes/{EXP2}/cerrar")
    check(r.status_code == 409 and "Examen de manejo" in r.json()["detail"], "«Cerrar Onboarding» bloqueado con tareas abiertas")
    client.patch(f"/onboarding/tareas/{T_EXAMEN}", json={"estado": "realizada"})
    est = client.get(f"/onboarding/expedientes/{EXP2}/estado").json()
    check(est["puedeCerrar"] and not est["cerrado"], "todo Realizado/Cancelado y documentos Aprobados/No aplica → se habilita, pero sigue abierto (nunca automático)")
    r = client.post(f"/onboarding/expedientes/{EXP2}/cerrar")
    check(r.status_code == 200 and r.json()["cerrado"] and r.json()["cerradoPor"] == admin_nombre, "«Cerrar Onboarding» manual con quién")
    check(all(x["expedienteId"] != EXP2 for x in client.get("/contratacion/expedientes").json()), "el cerrado sale del tablero")
    check(any(x["expedienteId"] == EXP2 for x in client.get("/contratacion/expedientes", params={"cerrados": True}).json()), "…y se consulta con «cerrados»")
    check(client.post(f"/onboarding/expedientes/{EXP2}/no-ingreso", json={"motivo": "x"}).status_code == 409, "«No ingresó» no aplica después del alta")

    print("\n--- 17. «No ingresó» ---")
    r = client.post("/candidatos", json={"nombre": "Nora Ausente", "telefono": "5566667777", "correo": "nora@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P5 = r.json()["id"]
    EXP5 = client.patch(f"/candidatos/{P5}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"}).json()["expedienteId"]
    client.patch(f"/candidatos/{P5}/condiciones-contratacion", json={"puesto": "Almacenista", "sueldo": "$11,000", "tipo_contratacion": "Tiempo indeterminado", "fecha_ingreso": "2026-11-16"})
    res5 = client.get(f"/onboarding/expedientes/{EXP5}/resumen").json()
    client.post(f"/onboarding/expedientes/{EXP5}/iniciar", json={
        "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta", "documentos": res5["configuracion"]["documentos"], "responsables": {"alta_imss_nomina": admin_nombre}, "notificar_responsables": False,
    })
    client.patch(f"/contratacion/expedientes/{EXP5}/preparacion", json={"documentos_hasta": "2099-12-31"})
    check(client.post(f"/onboarding/expedientes/{EXP5}/no-ingreso", json={}).status_code == 400, "«No ingresó» exige motivo")
    r = client.post(f"/onboarding/expedientes/{EXP5}/no-ingreso", json={"motivo": "Aceptó otra oferta"})
    check(r.status_code == 200 and r.json()["noIngreso"]["motivo"] == "Aceptó otra oferta", "«No ingresó» registrado con motivo")
    check(all(t["estado"] == "cancelada" and t["motivoCancelacion"].startswith("No ingresó") for t in r.json()["listaTareas"]),
          "cancela TODAS las tareas abiertas (también las fijas) con el motivo")
    check(any(a["destino"] == admin.correo for a in r.json()["avisosResponsables"]), "avisa a los responsables")
    db.expire_all()
    e5 = db.get(Expediente, EXP5)
    p5 = db.query(Postulacion).filter(Postulacion.id == e5.postulacion_id).one()
    check(e5.documentos_hasta is None and not p5.activa and p5.motivo_cierre == "no_ingreso", "detiene los recordatorios automáticos y cierra la postulación")
    check(any(h["evento"] == "no_ingreso" and "Aceptó otra oferta" in h["texto"] for h in p5.historial or []), "queda en el historial del candidato")
    check(client.post(f"/contratacion/expedientes/{EXP5}/recordatorio", json={}).status_code == 409, "ya no se le mandan recordatorios manuales")
    check(client.post(f"/contratacion/expedientes/{EXP5}/alta", json={}).status_code == 409, "…ni se le puede dar de alta")
    check(all(x["expedienteId"] != EXP5 for x in client.get("/contratacion/expedientes").json()), "sale del tablero de Onboarding")

    db.close()

print(f"\n🎉 Onboarding v2 — Fases 1, 2 y 3: {OK} verificaciones OK")
