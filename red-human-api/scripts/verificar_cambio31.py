"""Cambio 31 (2026-10-13): preguntas de requisitos, presentación del bot y tono corporativo. Base desechable; IA, Meta y
Bot API SIMULADOS (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambio31.py

Bloque 1: Open Graph de las ligas del candidato («[Tipo de liga] · [puesto]» + «[empresa]», sin textos comerciales) y
una pregunta NATURAL por requisito indispensable (el generador la redacta; el bot la usa tal cual).
Bloque 2: presentación «Soy Red Human, el agente virtual de [empresa].» (Cliente visible o Cuenta) en todas las
rutas; tras responder a la plantilla de WhatsApp, directo a la primera pregunta; el cierre usa la misma empresa.
Bloque 3: tono corporativo (sin ejemplos de formato, sin «Para confirmar», agradecimientos breves y variados, sin
elogios, escolaridad «¿Cuál es tu último grado de estudios?») y evasivas (una repregunta suave; la segunda se registra).
Bloque 4: en rutas corporativas la pregunta de tiempo de traslado solo existe si la vacante tiene un indispensable de traslado.
"""

import asyncio
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
import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_c31_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c31.db").replace(chr(92), "/")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-c31"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers.candidatos import _abrir_expediente, _crear_candidato, crear_postulacion  # noqa: E402
from app.services import ia  # noqa: E402
from app.services import oferta as soferta  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402
from app.services.entrevistas import crear_entrevista_para_candidato  # noqa: E402

OK = 0
ENVIADOS = []
APP_DIR = RAIZ.parent / "red-human-app"


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def leer(rel):
    return (APP_DIR / rel).read_text(encoding="utf-8")


async def _meta_falso(cuerpo):
    ENVIADOS.append(cuerpo)
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "mensaje_id": f"wamid.{len(ENVIADOS)}"}


whatsapp._meta_post = _meta_falso
REQS = ["5 años de experiencia en derecho corporativo", "Licenciatura en Derecho", "Cédula profesional vigente"]

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="C31 interna", nombre_comercial="Despacho Legal MX", estado="Activa", slug="c31")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ================= Bloque 1 · Open Graph y preguntas naturales =================
    print("\n--- Bloque 1 · Open Graph de las ligas del candidato ---")
    v = Vacante(codigo="VAC-C31", titulo="Abogado corporativo", estado="Publicada", cuenta_id=cuenta.id, slug="abogado-c31",
                requisitos=" · ".join(REQS), sueldo="Desde $40,000 MXN mensuales", ubicacion="Monterrey, Nuevo León")
    db.add(v)
    db.flush()
    v.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": sproc.ruta_base("corporativo")["pasos"]})
    db.commit()
    c = _crear_candidato(db, cuenta.id, "Laura Legal", "Portal", False, telefono="5531310001")
    p = crear_postulacion(db, c, v, cuenta.id, "web", consentimiento=True)
    db.commit()
    e, _ = crear_entrevista_para_candidato(db, p, "prueba", paso_tipo="entrevista_agente")
    exp = _abrir_expediente(db, p, admin)
    db.commit()
    asyncio.run(soferta.enviar(db, p, "prueba", {"puesto": "Abogado corporativo", "sueldo": "$40,000"}))
    token_oferta = soferta.estado(p)["token"]
    r1 = client.get(f"/publico/meta/entrevista/{e.token}").json()
    r2 = client.get(f"/publico/meta/documentos/{exp.token}").json()
    r3 = client.get(f"/publico/meta/oferta/{token_oferta}").json()
    check(r1 == {"titulo": "Entrevista · Abogado corporativo", "descripcion": "Despacho Legal MX"},
          f"entrevista: «Entrevista · [puesto]» + «[empresa]» → {r1}")
    check(r2["titulo"] == "Documentos · Abogado corporativo" and r3["titulo"] == "Oferta laboral · Abogado corporativo"
          and r2["descripcion"] == r3["descripcion"] == "Despacho Legal MX", "documentos y oferta con el mismo formato")
    check(client.get("/publico/meta/oferta/no-existe-xxxxx").json()["titulo"] == "Oferta laboral", "liga desconocida: solo el tipo")
    for ruta in ("entrevista", "expediente", "oferta"):
        lay = leer(f"app/{ruta}/[token]/layout.tsx")
        check("generateMetadata" in lay and "metadatosLiga(" in lay and "Red Human AI" not in lay, f"/{ruta}/[token] arma su Open Graph en el servidor")
    meta_ts = leer("lib/meta-publica.ts")
    check("openGraph" in meta_ts and "Red Human AI" not in meta_ts and "Plataforma" not in meta_ts, "sin textos comerciales de la plataforma")

    print("\n--- Bloque 1 · pregunta natural por requisito indispensable ---")
    ficha = ia.FichaVacante(titulo="Abogado corporativo", requisitos_indispensables=REQS)
    g, _ = ia.generar_vacante(ficha)
    pr = {x.requisito: x.pregunta for x in g.preguntas_requisitos}
    check(pr.get(REQS[0]) == "¿Cuántos años llevas en derecho corporativo?",
          f"experiencia → «¿Cuántos años llevas en derecho corporativo?» ({pr.get(REQS[0])})")
    check(all("cuánto tiempo de experiencia" not in q.lower() and "para confirmar" not in q.lower() for q in pr.values())
          and len(pr) == 3, "nunca «¿Cuánto tiempo de experiencia en…?» ni «Para confirmar»")
    propuestas = [{"requisito": REQS[0], "pregunta": "¿Cuántos años llevas litigando en derecho corporativo?"},
                  {"requisito": REQS[2], "pregunta": "Para confirmar: ¿cuentas con cédula?"}]
    asegurada = {x["requisito"]: x["pregunta"] for x in ia.asegurar_preguntas_requisitos(REQS, propuestas)}
    check(asegurada[REQS[0]] == "¿Cuántos años llevas litigando en derecho corporativo?" and not asegurada[REQS[2]].startswith("Para confirmar"),
          "se usa la pregunta de la IA tal cual (y se rechaza una con frase de formulario)")
    check("preguntas_requisitos" in ia.VacanteGenerada.model_fields and "lenguaje natural" in
          ia.VacanteGenerada.model_fields["preguntas_requisitos"].description, "el prompt del generador pide la pregunta natural")
    r = client.patch(f"/vacantes/{v.codigo}", json={"preguntas_requisitos": [
        {"requisito": REQS[0], "pregunta": "Cuéntame, ¿cuántos años llevas en derecho corporativo?"},
        {"requisito": REQS[2], "pregunta": "¿Tu cédula profesional está vigente?"}]})
    check(r.status_code == 200 and {x["requisito"]: x["pregunta"] for x in r.json()["preguntasRequisitos"]}[REQS[2]]
          == "¿Tu cédula profesional está vigente?", "la vacante guarda las preguntas (RH las puede ajustar)")
    db.expire_all()
    v = db.get(Vacante, v.id)
    vm = Vacante(codigo="VAC-C31M", titulo="Almacenista", estado="Publicada", cuenta_id=cuenta.id,
                 requisitos="2 años de experiencia en almacén · Disponibilidad para rolar turnos · Mayor de edad")
    db.add(vm)
    db.flush()
    vm.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": sproc.ruta_base("masivos_sin_documentos")["pasos"]})
    db.commit()
    crit = pconv.criterios_de(vm)
    check(crit[0]["chat"] == "¿Cuántos años llevas en almacén?" and crit[0]["dato"] == "experiencia",
          f"el motor (Masivos) usa la pregunta natural tal cual → «{crit[0]['chat']}»")
    tsx = leer("components/dashboard/vacantes/formulario-contenido.tsx")
    # Cambio 37: la pregunta de cada requisito vive en la lista única «Preguntas filtro» (se guarda como `filtro`)
    check("EditorPreguntasFiltro" in tsx and "Preguntas filtro" in tsx and "filtro: c.filtro" in tsx,
          "el formulario muestra y guarda las preguntas (en «Preguntas filtro», Cambio 37)")

    # ================= Bloque 2 · presentación unificada y salto post-plantilla =================
    print("\n--- Bloque 2 · presentación «Soy Red Human, el agente virtual de [empresa].» ---")
    from app.models import Cliente
    from app.routers.webhooks import _texto_aviso_privacidad
    from app.services import plantillas_meta

    check(ia.presentacion_bot("Despacho Legal MX") == "Soy Red Human, el agente virtual de Despacho Legal MX.", "cadena exacta")
    cli = Cliente(cuenta_id=cuenta.id, nombre="Grupo Industrial interno", nombre_comercial="Grupo Industrial", estado="Activo")
    db.add(cli)
    db.flush()
    vc = Vacante(codigo="VAC-C31C", titulo="Almacenista", estado="Publicada", cuenta_id=cuenta.id, cliente_id=cli.id,
                 mostrar_cliente_candidato=True, requisitos="2 años de experiencia en almacén · Turnos · Mayor de edad")
    db.add(vc)
    db.flush()
    vc.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": sproc.ruta_base("masivos_sin_documentos")["pasos"]})
    db.commit()
    db.refresh(vc)
    from app.serial import nombre_empresa_candidato

    emp_cli = nombre_empresa_candidato(vc)

    def chat(pp, texto):
        r = client.post(f"/candidatos/{pp.codigo}/prefiltro", json={"texto": texto})
        assert r.status_code == 200, r.text
        return r.json().get("respuesta") or ""

    def nuevo(nombre, tel, vac):
        cc = _crear_candidato(db, cuenta.id, nombre, "Portal", False, telefono=tel)
        pp = crear_postulacion(db, cc, vac, cuenta.id, "web", consentimiento=True)
        db.commit()
        return pp

    pa = nuevo("Ana Cliente", "5531310002", vc)
    r = chat(pa, "Hola")
    check(emp_cli == "Grupo Industrial" and r == f"Hola, Ana. Soy Red Human, el agente virtual de {emp_cli}. ¿Cuántos años llevas en almacén?",
          f"saludo con el CLIENTE visible de la vacante → «{r}»")
    pd = nuevo("Diego Directo", "5531310003", vm)
    r = chat(pd, "Hola")
    check(r.startswith("Hola, Diego. Soy Red Human, el agente virtual de Despacho Legal MX. "), "sin Cliente: la Cuenta (directo)")
    check(not any(x in r for x in ("asistente virtual", "Primero confírmame", "te acompaña")), "sin las frases anteriores")
    aviso = _texto_aviso_privacidad("Ana", vc)
    check(f"Soy Red Human, el agente virtual de {emp_cli}." in aviso, "el aviso de privacidad usa la misma presentación")
    check(ia.mensaje_inicial_entrevista("Almacenista", emp_cli).startswith(f"Hola. Soy Red Human, el agente virtual de {emp_cli}."),
          "la entrevista con avatar se presenta igual")

    print("\n--- Bloque 2 · respuesta a la plantilla: sin presentación, directo a la primera pregunta ---")
    pp = nuevo("Pepe Plantilla", "5531310004", vc)
    plantillas_meta._guardar(pp, {"enviada_en": "2026-10-13T10:00:00+00:00", "plantilla": "inicio_entrevista_rh"})
    db.commit()
    r = chat(pp, "Sí, empezar ahora")
    check(r == "¿Cuántos años llevas en almacén?", f"«Sí, empezar ahora» → SOLO la primera pregunta → «{r}»")

    print("\n--- Bloque 2 · cierre con la misma empresa ---")
    pr = nuevo("Rita Rechazo", "5531310005", vc)
    chat(pr, "Hola")
    r = chat(pr, "Nunca he trabajado en almacén")
    check(r.startswith(f"Gracias por tu interés en {emp_cli}.") , f"cierre por requisito con la misma empresa → «{r[:80]}…»")
    from app.services import entrevista_whatsapp_fija as ewf

    db.refresh(pa)
    check(f"Recursos Humanos de {emp_cli} te contactará" in ewf.cierre(pa), "cierre de la entrevista fija con la misma empresa")
    src = (RAIZ / "app/services/prefiltro_conversacional.py").read_text(encoding="utf-8")
    check("una persona del equipo de RH{' de ' + _emp if _emp else ''}" in src, "cierre del prefiltro aprobado con la misma empresa")

    # ================= Bloque 3 · tono corporativo, agradecimientos variados y evasivas =================
    print("\n--- Bloque 3 · textos: sin ejemplos de formato, sin «Para confirmar», escolaridad exacta ---")
    from app.services import prefiltro_operativo as pop

    check(ia.PREGUNTA_ESCOLARIDAD == "¿Cuál es tu último grado de estudios?", "escolaridad = «¿Cuál es tu último grado de estudios?»")
    check(ia.pregunta_escolaridad("Secundaria", True)["pregunta"] == ia.PREGUNTA_ESCOLARIDAD, "la pregunta de escolaridad sale de la constante")
    fuentes = {r: (RAIZ / r).read_text(encoding="utf-8") for r in ("app/services/ia.py", "app/services/prefiltro_conversacional.py",
                                                                  "app/services/prefiltro_operativo.py")}
    check(not any("¿Hasta qué año estudiaste?" in s for s in fuentes.values()), "ya no existe «¿Hasta qué año estudiaste?»")
    chats = [c["chat"] for c in pconv.criterios_de(vm)] + [c.get("repregunta") or "" for c in pconv.criterios_de(vm)]
    chats += list(pconv.REPREGUNTA_DATO.values()) + list(pop.REPREGUNTAS.values())
    check(not any(x in t for t in chats for x in ("Para confirmar", "Primero confírmame", "Por ejemplo", "15 mil")),
          "ninguna pregunta ni repregunta dice «Para confirmar», «Primero confírmame» ni «Por ejemplo»")
    # Cambio 37: «Claro. Solo para tenerlo en cuenta…» es EXCLUSIVA de la pregunta de sueldo
    check(all(t.startswith("Claro. Solo para tenerlo en cuenta,") == (k == "sueldo") for k, t in pop.REPREGUNTAS.items()),
          "las repreguntas piden el dato directo; «Claro. Solo para tenerlo en cuenta…» solo en sueldo (Cambio 37)")
    dv = pconv.dato_expectativa(vm)
    check(dv["repregunta"] == "Claro. Solo para tenerlo en cuenta, ¿con qué cantidad al mes te sentirías cómodo?", f"repregunta de sueldo → «{dv['repregunta']}»")

    print("\n--- Bloque 3 · agradecimientos breves y variados, sin elogios ---")
    check(set(ia.VARIANTES_GRACIAS) == {"Gracias, {nombre}.", "Entendido.", "Gracias.", "Anotado."}, "variantes: «Gracias, [Nombre].», «Entendido.»…")
    e = {}
    salidas = [pconv.con_acuse(e, "¿Q?", "Ana") for _ in range(3 * ia.CADA_CUANTOS_GRACIAS)]
    acuses = [s[:-3].strip() for s in salidas if s != "¿Q?"]
    check(acuses and acuses[0] == "Gracias, Ana." and len(set(acuses)) == len(acuses), f"se alternan y usan el nombre → {acuses}")
    check(not any(x in " ".join(salidas) for x in ("Perfecto", "Muy bien", "Excelente")), "nunca «Perfecto», «Muy bien» ni «Excelente»")
    check(ia.sin_elogios("¡Excelente! ¿Y qué hacías ahí?") == "¿Y qué hacías ahí?", "«¡Excelente!» se quita del turno de la IA")
    check(ia.sin_elogios("Muy bien, ¿cuánto tiempo estuviste?") == "¿Cuánto tiempo estuviste?", "«Muy bien,» también")
    check(ia.sin_elogios("Entendido. ¿Qué más hacías?") == "Entendido. ¿Qué más hacías?", "un acuse neutro se conserva")
    check("TONO_CORPORATIVO" in fuentes["app/services/ia.py"] and "PROHIBIDO elogiar" in ia.TONO_CORPORATIVO and "Para confirmar" in ia.TONO_CORPORATIVO,
          "el prompt del entrevistador lleva el tono corporativo (sin elogios ni frases de formulario)")

    print("\n--- Bloque 3 · evasivas: UNA repregunta suave; la segunda se registra («Flexible») y se avanza ---")
    vcorp = Vacante(codigo="VAC-C31E", titulo="Analista de compras", estado="Publicada", cuenta_id=cuenta.id,
                    requisitos="3 años en compras · Inglés intermedio · Manejo de SAP",
                    sueldo_periodicidad="a_convenir", sueldo="A convenir")  # 2026-10-16: sin sueldo publicado se pide la expectativa
    db.add(vcorp)
    db.flush()
    vcorp.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": sproc.ruta_base("corporativo")["pasos"]})
    db.commit()
    pe2 = nuevo("Elsa Evasiva", "5531310009", vcorp)
    r = chat(pe2, "Hola")
    sueldo_visto = False
    for _ in range(12):
        if "esperas ganar" in r:
            sueldo_visto = True
            break
        r = chat(pe2, "Sí")
    check(sueldo_visto, f"el prefiltro llega a la expectativa de sueldo → «{r}»")
    r1 = chat(pe2, "Lo que ustedes ofrezcan")
    check(r1 == "Claro. Solo para tenerlo en cuenta, ¿con qué cantidad al mes te sentirías cómodo?", f"1.ª evasiva → repregunta suave → «{r1}»")
    r2 = chat(pe2, "La verdad lo que sea, soy flexible")
    db.expire_all()
    pe2 = db.get(type(pe2), pe2.id)
    est = (pe2.analisis or {}).get("prefiltro_conversacional") or {}
    resp = (est.get("respuestas") or {}).get("dato-sueldo") or {}
    datos = (pe2.analisis or {}).get("prefiltro_datos") or {}
    check(resp.get("flexible") and datos.get("sueldo") == "Flexible", f"2.ª evasiva → se registra «Flexible» → {datos.get('sueldo')}")
    check("cantidad" not in r2 and "esperas ganar" not in r2, f"…y se avanza sin volver a preguntar → «{r2[:80]}»")
    pts = " ".join(str(x) for x in ((pe2.analisis or {}).get("puntos_validar_prefiltro") or []))
    check("sueldo" not in pts.lower(), "la evasiva no se manda a Puntos por validar")

    # ================= Bloque 4 · traslado en corporativos solo con indispensable de traslado =================
    print("\n--- Bloque 4 · generación: «¿Cuánto tiempo haces de tu casa a…?» solo si es indispensable ---")

    def ficha(corp, reqs):
        return ia.FichaGuion(titulo="Analista de compras", requisitos_indispensables=reqs, ubicacion="Monterrey",
                             sueldo="$20,000 a $25,000 MXN mensuales", ruta_corporativa=corp)

    def datos_de(f):
        return [q["dato"] for q in ia.preguntas_datos_siempre(f)]

    sin_tr = ["3 años en compras", "Inglés intermedio", "Manejo de SAP"]
    con_tr = sin_tr + ["Vivir a máximo 40 minutos de la oficina"]
    check("traslado" not in datos_de(ficha(True, sin_tr)), "corporativo SIN indispensable de traslado → no se genera la pregunta")
    check("traslado" in datos_de(ficha(True, con_tr)), "corporativo CON indispensable de traslado → sí se genera")
    check("traslado" in datos_de(ficha(False, sin_tr)), "otras rutas conservan su regla")
    _, wa = ia.asegurar_prefiltros([], [], ficha(True, sin_tr), hay_web=True, hay_whatsapp=True)
    check(not any("de tu casa" in q["pregunta"] for q in wa), "el prefiltro de WhatsApp generado (corporativo) no la trae")
    check(ia.requisito_traslado(con_tr) == "Vivir a máximo 40 minutos de la oficina" and not ia.requisito_traslado(sin_tr),
          "se detecta el indispensable de traslado")
    from app.services import guiones as sguiones

    src_g = (RAIZ / "app/services/guiones.py").read_text(encoding="utf-8")
    check("ficha.ruta_corporativa = es_corporativa(" in src_g and hasattr(sguiones, "ficha_guion"), "la generación de la vacante marca la ruta corporativa")

    print("\n--- Bloque 4 · conversación: el agente solo pregunta el traslado si es indispensable ---")
    vcorp.preguntas_filtro_whatsapp = ia.preguntas_datos_siempre(ficha(False, sin_tr))  # guardado viejo CON traslado
    db.commit()
    pt_sin = nuevo("Tere Traslado", "5531310010", vcorp)
    check(not any(c.get("dato") == "traslado" for c in pconv.criterios_de(vcorp, pt_sin)),
          "corporativo sin indispensable: aunque el prefiltro guardado la traiga, no se pregunta")
    vtr = Vacante(codigo="VAC-C31T", titulo="Analista de compras", estado="Publicada", cuenta_id=cuenta.id,
                  requisitos="3 años en compras · Vivir a máximo 40 minutos de la oficina", sueldo_desde=20000, sueldo_hasta=25000,
                  sueldo_periodicidad="mensual")
    db.add(vtr)
    db.flush()
    vtr.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": sproc.ruta_base("corporativo")["pasos"]})
    vtr.preguntas_filtro_whatsapp = ia.preguntas_datos_siempre(ficha(True, ["3 años en compras", "Vivir a máximo 40 minutos de la oficina"]))
    db.commit()
    pt_con = nuevo("Toño Traslado", "5531310011", vtr)
    check(any(c.get("dato") == "traslado" for c in pconv.criterios_de(vtr, pt_con)), "corporativo con indispensable de traslado: sí se pregunta")
    pt_m = nuevo("Mario Masivo", "5531310012", vm)
    check(any("de tu casa" in (c.get("chat") or "") for c in pconv.criterios_de(vm, pt_m)), "Masivos conserva su pregunta de traslado")

    app.dependency_overrides.clear()
    db.close()

print(f"\n🎉 Cambio 31: {OK} comprobaciones OK")
