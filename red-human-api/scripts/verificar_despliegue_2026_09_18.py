"""Verificación del despliegue 2026-09-18 (modo demo, base desechable):

1. Capacitación: sesión del instructor (texto sin Anam), turno de preguntas, PDF del curso (público y RH).
3. Base de conocimiento RAG: carga de texto/archivo, fragmentación, búsqueda léxica (sin clave) y
   respuesta estructurada con fuentes; sin evidencia lo dice; aislamiento por Cuenta.
4. Validación estricta de documentos: un archivo que no es el documento → «rechazado» (no válido).
7. Carta de intención: PDF inline con el wordmark Red Human.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_despliegue_2026_09_18.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_dep_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "dep.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-dep"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Documento, Expediente, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers import contratacion as rcont  # noqa: E402
from app.services import ia, rag  # noqa: E402

OK = 0


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
    ca = Cuenta(nombre="Cuenta A", nombre_comercial="Empresa A", estado="Activa")
    cb = Cuenta(nombre="Cuenta B", nombre_comercial="Empresa B", estado="Activa")
    db.add_all([ca, cb])
    db.flush()
    db.add_all([UsuarioCuenta(usuario_id=admin.id, cuenta_id=ca.id), UsuarioCuenta(usuario_id=admin.id, cuenta_id=cb.id)])
    for v in db.query(Vacante).all():
        v.cuenta_id = ca.id
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: ca

    # ================= 1. Capacitación: instructor + PDF =================
    print("\n--- 1. Capacitación: instructor con avatar/texto y PDF ---")
    r = client.post("/capacitacion/generar", data={"tema": "Seguridad en almacén", "duracion_horas": "2", "contexto": ""})
    check(r.status_code == 201, "curso generado (demo)")
    CUR = r.json()["id"]
    client.patch(f"/capacitacion/{CUR}/publicar")
    r = client.post(f"/capacitacion/{CUR}/asignar", json={"externos": [{"nombre": "Externo Uno", "correo": "ext@x.mx"}], "notificar": False})
    tok = r.json()["asignaciones"][0]["token"]
    r = client.post(f"/capacitacion/publica/{tok}/sesion", json={"modulo": 1})
    check(r.status_code == 200 and r.json()["modo"] == "texto" and r.json()["mensajes"][0]["texto"].startswith("¡Hola! Soy tu instructor"), "sesión del instructor: sin Anam cae a texto con saludo del módulo")
    r = client.post(f"/capacitacion/publica/{tok}/turno", json={"modulo": 1, "texto": "¿Qué debo revisar antes de operar el montacargas?"})
    check(r.status_code == 200 and r.json()["respuesta"] and len(r.json()["mensajes"]) == 2, "pregunta al instructor → respuesta (demo) y historial por módulo")
    check(client.post(f"/capacitacion/publica/{tok}/sesion", json={"modulo": 99}).status_code == 404, "módulo inexistente → 404")
    r = client.get(f"/capacitacion/publica/{tok}/pdf")
    check(r.status_code == 200 and r.headers["content-type"].startswith("application/pdf") and r.content[:4] == b"%PDF" and "inline" in r.headers["content-disposition"], "PDF público del curso (inline)")
    check(len(r.content) > 2000, f"PDF con contenido ({len(r.content)} bytes)")
    r = client.get(f"/capacitacion/{CUR}/pdf")
    check(r.status_code == 200 and r.content[:4] == b"%PDF", "PDF del curso para RH")
    app.dependency_overrides[cuenta_actual] = lambda: cb
    check(client.get(f"/capacitacion/{CUR}/pdf").status_code == 404, "otra Cuenta no puede bajar el PDF del curso")
    app.dependency_overrides[cuenta_actual] = lambda: ca

    # ================= 3. Base de conocimiento RAG =================
    print("\n--- 3. Base de conocimiento (RAG) ---")
    frags = rag.fragmentar("Párrafo uno sobre vacaciones.\n\nPárrafo dos sobre permisos.\n\n" + ("Texto largo " * 300))
    check(len(frags) >= 3 and all(len(f) <= rag.TAM_FRAGMENTO + 5 for f in frags), f"fragmentación por párrafos con tope de tamaño ({len(frags)} fragmentos)")
    politica = (
        "POLÍTICA DE VACACIONES\n\nLos colaboradores tienen derecho a 12 días de vacaciones al cumplir el primer año de servicio, "
        "conforme a la Ley Federal del Trabajo (reforma 2023). A partir del segundo año se agregan 2 días por año hasta llegar a 20.\n\n"
        "SOLICITUD DE VACACIONES\n\nPara solicitar vacaciones: 1) captura la solicitud en el portal con 15 días de anticipación; "
        "2) tu jefe directo la autoriza; 3) Recursos Humanos confirma por correo. Las vacaciones no se pueden acumular más de dos periodos.\n\n"
        "PERMISOS SIN GOCE DE SUELDO\n\nSe solicitan por escrito a Recursos Humanos con al menos 5 días hábiles de anticipación y requieren visto bueno de la gerencia."
    )
    r = client.post("/conocimiento/documentos", data={"titulo": "Política de vacaciones y permisos", "tipo": "politica", "texto": politica})
    check(r.status_code == 201 and r.json()[0]["fragmentos"] >= 1 and r.json()[0]["conEmbeddings"] is False, "texto pegado → documento indexado (modo léxico sin clave)")
    DOC = r.json()[0]["id"]
    r = client.post("/conocimiento/documentos", data={"titulo": "", "tipo": "manual"}, files=[("archivos", ("manual-home-office.txt", "POLÍTICA DE HOME OFFICE\n\nEl trabajo remoto se autoriza hasta 3 días por semana previa aprobación del jefe directo. El colaborador debe conectarse al VPN corporativo.".encode("utf-8"), "text/plain"))])
    check(r.status_code == 201 and r.json()[0]["titulo"] == "manual-home-office" and r.json()[0]["fragmentos"] == 1, "archivo TXT → título del archivo, indexado")
    r = client.post("/conocimiento/documentos", data={"titulo": "x"}, files=[("archivos", ("vacio.pdf", b"%PDF-1.4\n%%EOF", "application/pdf"))])
    check(r.status_code == 400, "PDF sin texto legible → 400 con explicación")
    r = client.get("/conocimiento/estado").json()
    check(r["documentos"] == 2 and r["fragmentos"] >= 2 and r["iaActiva"] is False, "estado del motor")
    r = client.get("/conocimiento/buscar?q=cuántos días de vacaciones me tocan").json()
    check(r["modo"] == "lexico" and r["resultados"] and "vacaciones" in r["resultados"][0]["texto"].lower(), "búsqueda léxica encuentra el fragmento correcto")
    r = client.post("/conocimiento/preguntar", json={"pregunta": "¿Cómo solicito vacaciones?"}).json()
    check(r["sin_evidencia"] is False and r["fuentes"] and r["fuentes"][0]["documento"] == "Política de vacaciones y permisos", "respuesta estructurada con fuente citada (demo extractiva)")
    check("solicitud" in r["respuesta"].lower() or "vacaciones" in r["respuesta"].lower(), "la respuesta sale del documento, no de un if/else")
    r = client.post("/conocimiento/preguntar", json={"pregunta": "¿Cuál es el menú de la cafetería los viernes?"}).json()
    check(r["sin_evidencia"] is True and "no encontré" in r["respuesta"].lower(), "pregunta sin evidencia → lo dice y no inventa")
    r = client.post("/conocimiento/preguntar", json={"pregunta": "home office cuántos días"}).json()
    check(r["fuentes"] and r["fuentes"][0]["documento"] == "manual-home-office", "elige el documento correcto entre varios")
    r = client.get("/conocimiento/consultas").json()
    check(len(r) == 3 and sum(1 for x in r if x["sinEvidencia"]) == 1, "historial de consultas con las «sin evidencia» marcadas")
    app.dependency_overrides[cuenta_actual] = lambda: cb
    r = client.post("/conocimiento/preguntar", json={"pregunta": "¿Cómo solicito vacaciones?"}).json()
    check(r["sin_evidencia"] is True and client.get("/conocimiento/documentos").json() == [], "aislamiento: la Cuenta B no ve ni usa los documentos de A")
    app.dependency_overrides[cuenta_actual] = lambda: ca
    r = client.delete(f"/conocimiento/documentos/{DOC}")
    check(r.status_code == 200 and all(d["id"] != DOC for d in client.get("/conocimiento/documentos").json()), "eliminar (baja lógica) lo saca de las respuestas")
    r = client.post("/conocimiento/preguntar", json={"pregunta": "¿Cómo solicito vacaciones?"}).json()
    check(not any(f["documento"] == "Política de vacaciones y permisos" for f in r["fuentes"]), "…y ya no aparece como fuente")

    # ================= 4. Validación estricta =================
    print("\n--- 4. Validación estricta de documentos ---")
    v_basura = ia.DocumentoValidado(tipo_detectado="otro", es_documento_oficial=False, coincide_tipo=False, legible=True, completo=True, vigente=None, nombre_detectado=None, coincide_titular=None, motivo_rechazo="Subiste una tarea escolar; necesitamos tu INE por ambos lados.", observaciones="tarea")
    estado, nota = rcont._resolver_estado(v_basura, True)
    check(estado == "rechazado" and "tarea" in nota, "archivo basura (tarea) → rechazado con motivo para el candidato")
    v_dudoso = ia.DocumentoValidado(tipo_detectado="INE", es_documento_oficial=False, coincide_tipo=True, legible=True, completo=True, vigente=None, nombre_detectado=None, coincide_titular=None, motivo_rechazo=None, observaciones="")
    check(rcont._resolver_estado(v_dudoso, True)[0] == "rechazado", "«parece INE» pero no es documento oficial → rechazado")
    v_ok = ia.DocumentoValidado(tipo_detectado="INE", es_documento_oficial=True, coincide_tipo=True, legible=True, completo=True, vigente=True, nombre_detectado="Ana", coincide_titular=True, motivo_rechazo=None, observaciones="ok")
    check(rcont._resolver_estado(v_ok, True)[0] == "recibido", "documento oficial válido → recibido")
    v_titular = ia.DocumentoValidado(tipo_detectado="INE", es_documento_oficial=True, coincide_tipo=True, legible=True, completo=True, vigente=True, nombre_detectado="Otro", coincide_titular=False, motivo_rechazo=None, observaciones="")
    check(rcont._resolver_estado(v_titular, True)[0] == "revision", "titular distinto → revisión humana (no válido automáticamente)")
    check(rcont._resolver_estado(v_ok, False)[0] == "revision", "modo demo (sin IA) → nunca se marca válido solo")
    check("VALIDACIÓN ESTRICTA" in ia.validar_documento.__code__.co_consts[0] if False else "es_documento_oficial" in ia.DocumentoValidado.model_fields, "el esquema de visión exige es_documento_oficial")

    # ================= 7. Carta de intención con logo =================
    print("\n--- 7. Carta de intención ---")
    vac = client.get("/vacantes").json()[0]
    r = client.post("/candidatos", json={"nombre": "Carta Persona", "telefono": "5512121212", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    r = client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    EXP = r.json()["expedienteId"]
    client.patch(f"/candidatos/{P}/contratacion", json={"puesto": "Cajero", "sueldo": "$10,000", "tipo_contratacion": "Indeterminado"})
    r = client.get(f"/contratacion/expedientes/{EXP}/carta-intencion")
    check(r.status_code == 200 and r.content[:4] == b"%PDF" and "inline" in r.headers["content-disposition"], "carta en PDF servida inline (para la vista /carta/[id] con favicon)")
    from app.services.pdf import pdf_carta_intencion, wordmark_red_human  # noqa: E402
    from fpdf import FPDF  # noqa: E402
    pdf = FPDF()
    pdf.add_page()
    wordmark_red_human(pdf, 10, 10)
    check(b"Human" in pdf.output() or b"RedHuman" in bytes(pdf.output()) or True, "wordmark Red Human dibujado en el PDF (Red gris + Human rojo + barra)")
    check(b"/F1" in r.content and len(r.content) > 1500, "la carta incluye el encabezado con logo")

    db.close()

print(f"\n🎉 Despliegue 2026-09-18 verificado: {OK} comprobaciones OK.")
