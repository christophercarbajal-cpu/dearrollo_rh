"""Cambio 43 (2026-10-14). Base desechable; IA SIMULADA (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambio43.py

- «Enfoque» por actividad de entrevista (WhatsApp = Operativo y avatar = Profesional por defecto). Operativo = entrevista
  base FIJA (sin guion de IA); Profesional = guion ≤ 10 preguntas (una de trayectoria, técnicas y casos reales, sin historial
  trabajo por trabajo, sin lugares, sin «operativa»); Profesional + personal = + 2-3 de motivación.
- Preguntas base: Operativo = colonia, traslado y sueldo afirmativo; Profesional = sin colonia, traslado solo si es
  indispensable y sueldo como expectativa.
- Indispensables implícitos: «Cédula profesional» (profesión regulada) y «[n] años de experiencia en [área]» (Senior+),
  «Propuesto por Red Human», máximo 5.
- Textos por plataforma: Markdown pintado sin símbolos, WhatsApp sin Markdown, «Copiar» con la liga `?canal=`; cambiar el
  enfoque después de generar → «Volver a generar» y regenerar limpia el aviso.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
_dir = tempfile.mkdtemp(prefix="rh_c43_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c43.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "simulado"
os.environ["ADMIN_PASSWORD"] = "prueba-c43"
os.environ["SEMBRAR_DEMO"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import entrevista_whatsapp_fija as ewf  # noqa: E402
from app.services import ia  # noqa: E402
from app.services import preguntas_filtro as pfu  # noqa: E402
from app.services import proceso as sproc  # noqa: E402

OK = 0
APP_DIR = RAIZ.parent / "red-human-app"


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def n(t: str) -> str:
    return " ".join(unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower().split())


def ruta(*pasos):
    return {"pasos": [{"id": "solicitud", "tipo": "solicitud_web", "etapa": "Prefiltro"},
                      {"id": "prefiltro", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"}, *pasos,
                      {"id": "entrevista_humana", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"}]}


AVATAR = lambda enfoque=None: {"id": "avatar", "tipo": "entrevista_agente", "etapa": "Entrevista IA", **({"enfoque": enfoque} if enfoque else {})}  # noqa: E731
WHATS = lambda enfoque=None: {"id": "whats", "tipo": "entrevista_whatsapp", "etapa": "Entrevista IA", **({"enfoque": enfoque} if enfoque else {})}  # noqa: E731

ABOGADO = {"titulo": "Abogado corporativo", "area": "Derecho corporativo", "seniority": "Senior", "ubicacion_estado": "Nuevo León",
           "ubicacion_municipio": "Monterrey", "modalidad": "Presencial", "sueldo_desde": 45000, "sueldo_periodicidad": "mensual",
           "descripcion": "Asesoría legal a la dirección en contratos y sociedades.",
           "responsabilidades": ["Redactar y revisar contratos mercantiles", "Llevar asambleas y actas societarias",
                                 "Asesorar en fusiones y adquisiciones"]}
CONTADOR = {**ABOGADO, "titulo": "Contador general", "area": "Contabilidad", "seniority": "Junior",
            "descripcion": "Contabilidad general y cumplimiento fiscal.",
            "responsabilidades": ["Elaborar estados financieros mensuales", "Presentar declaraciones fiscales",
                                  "Conciliar cuentas bancarias"]}
_RE_HISTORIAL = re.compile(r"(ultimo trabajo|trabajo anterior|otro trabajo antes|en (que|cual) trabajo|laboraste|por que saliste|"
                           r"trabajas actualmente|llevas sin trabajar)")
_RE_LUGAR = re.compile(r"(colonia|municipio|donde vives|traslad|monterrey|nuevo leon)")


def generar(client, ficha, proceso):
    r = client.post("/vacantes/generar", json={**ficha, "proceso": proceso})
    check(r.status_code == 200, f"«Generar» {ficha['titulo']} ({r.status_code} {r.text[:200]})")
    return r.json()


def revisar_profesional(preguntas, nombre):
    k = [n(q) for q in preguntas]
    check(0 < len(preguntas) <= 10, f"{nombre}: máximo 10 preguntas ({len(preguntas)})")
    check("trayectoria" in k[0] and sum("trayectoria" in x for x in k) == 1, f"{nombre}: UNA pregunta general de trayectoria, la primera")
    check(not any(_RE_HISTORIAL.search(x) for x in k), f"{nombre}: sin historial trabajo por trabajo ni «¿En qué trabajo laboraste…?»")
    check(not any(_RE_LUGAR.search(x) for x in k), f"{nombre}: sin preguntas de ubicación ni repeticiones del lugar")
    check(not any("operativ" in x for x in k), f"{nombre}: jamás la palabra «operativa»")
    check(len(set(k)) == len(k), f"{nombre}: sin preguntas repetidas")


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="C43", nombre_comercial="Despacho Norte", estado="Activa", slug="c43")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ===================================================== 1. Campo «Enfoque»
    print("\n--- 1. Campo «Enfoque» en las entrevistas de Red Human ---")
    ops = client.get("/procesos/opciones").json()["enfoquesEntrevistaAgente"]
    check([o["texto"] for o in ops] == ["Profesional", "Profesional + personal", "Operativo"],
          f"opciones: Operativo · Profesional · Profesional + personal ({[o['texto'] for o in ops]})")
    from app.models import ENFOQUE_DEFAULT_PASO

    editor = (APP_DIR / "components/dashboard/procesos/editor-proceso.tsx").read_text(encoding="utf-8")
    po = (APP_DIR / "components/dashboard/vacantes/perfil-operativo.tsx").read_text(encoding="utf-8")
    check(ENFOQUE_DEFAULT_PASO == {"entrevista_whatsapp": "operativo", "entrevista_agente": "profesional", "llamada_agente": "profesional"}
          and 'entrevista_whatsapp: "operativo", entrevista_agente: "profesional"' in po
          and "enfoque: ENFOQUE_DEFAULT[t.valor]" in editor, "por defecto: WhatsApp = Operativo; avatar (y llamada) = Profesional")
    check(sproc.normalizar_pasos([AVATAR("profesional_personal")])[0]["enfoque"] == "profesional_personal", "el enfoque elegido se guarda")
    try:
        sproc.normalizar_pasos([AVATAR("gerencial")])
        check(False, "un enfoque inválido se rechaza")
    except sproc.ErrorProceso as e:
        check(e.status == 400, "un enfoque inválido se rechaza (400)")
    check(sproc.enfoques_de_ruta(ruta(WHATS())["pasos"]) == {"entrevista_whatsapp": "operativo"}
          and sproc.enfoques_de_ruta(ruta(AVATAR())["pasos"]) == {},
          "rutas previas sin «Enfoque»: Masivos = Operativo; las demás las decide la vacante (como antes)")

    # ===================================================== 2. Operativo = solo la base fija
    print("\n--- 2. Enfoque Operativo: SOLO la entrevista base (sin guion de IA) ---")
    g = generar(client, CONTADOR, ruta(WHATS("operativo")))
    sec = g["guiones"]["secciones"]
    check(sec.get("entrevista_whatsapp", {}).get("base_fija") is True and sec["entrevista_whatsapp"]["preguntas"] == ewf.guion_visible()["preguntas"],
          "WhatsApp Operativo = la entrevista base fija (situación actual, historial de 3 trabajos…)")
    check(g["enfoque_entrevista"] == "operativo", "la vacante queda con enfoque Operativo")
    g = generar(client, CONTADOR, ruta(AVATAR("operativo")))
    check(g["guiones"]["secciones"]["entrevista_avatar"].get("base_fija") is True, "avatar Operativo también usa SOLO la base, sin guion de IA")
    form = (APP_DIR / "components/dashboard/vacantes/formulario-contenido.tsx").read_text(encoding="utf-8")
    check("{!enfoquePorRuta && <PlantillasConversacion" in form and "{enfoquePorRuta && (\n                <EntrevistaWhatsappBase" in form,
          "la vacante muestra UNA sola entrevista: la base (Operativo) o el guion (Profesional), nunca las dos")

    # ===================================================== 3. Profesional: guion ≤ 10, técnico y de casos reales
    print("\n--- 3. Enfoque Profesional (Abogado y Contador) ---")
    ga = generar(client, ABOGADO, ruta(AVATAR("profesional")))
    qa = ga["guiones"]["secciones"]["entrevista_avatar"]["preguntas"]
    revisar_profesional(qa, "Abogado")
    gc = generar(client, CONTADOR, ruta(AVATAR("profesional")))
    qc = gc["guiones"]["secciones"]["entrevista_avatar"]["preguntas"]
    revisar_profesional(qc, "Contador")
    distintas = {n(x) for x in qa[1:]} ^ {n(x) for x in qc[1:]}
    check(len(distintas) >= 6, "Abogado y Contador reciben preguntas distintas")
    check(any("contrato" in n(q) for q in qa) and any("estados financieros" in n(q) or "declaraciones" in n(q) for q in qc),
          "las preguntas salen de las responsabilidades reales de cada puesto")
    # garantía en código aunque la IA se equivoque
    sucio = ["¿Cuál fue tu último trabajo?", "¿En qué trabajo laboraste en Monterrey?", "Cuéntame tu trayectoria.",
             "Cuéntame tu trayectoria profesional.", "¿Cómo manejas la parte operativa?", "Platícame un caso de litigio mercantil."] + \
            [f"Pregunta técnica {i}: ¿cómo resolviste el caso {i}?" for i in range(12)]
    limpio = ia.asegurar_guion_profesional(sucio, "profesional")
    revisar_profesional(limpio, "Guion de la IA corregido en código")

    # ===================================================== 4. Profesional + personal
    print("\n--- 4. Enfoque Profesional + personal ---")
    gp = generar(client, ABOGADO, ruta(AVATAR("profesional_personal")))
    qp = gp["guiones"]["secciones"]["entrevista_avatar"]["preguntas"]
    revisar_profesional(qp, "Abogado (+ personal)")
    mot = [q for q in qp if re.search(r"(motiva|interes|disfrut|aprender)", n(q))]
    check(2 <= len(mot) <= 3, f"incluye 2-3 preguntas de intereses y motivaciones ({len(mot)})")
    check(qp[-len(mot):] == mot, "las de motivación van al final")

    # ===================================================== 5. Preguntas base según el enfoque
    print("\n--- 5. Preguntas base ---")
    base_prof = {q.get("dato"): q for q in ga["filtro"] if q.get("base") and not q.get("indispensable")}
    check("zona" not in base_prof, "Profesional: OMITE la colonia")
    check("traslado" not in base_prof, "Profesional: sin indispensable de traslado, no pregunta el traslado")
    check(base_prof.get("sueldo", {}).get("pregunta") == "¿Cuál es tu expectativa de sueldo mensual?",
          f"Profesional: el sueldo es la expectativa ({base_prof.get('sueldo', {}).get('pregunta')})")
    gt = generar(client, {**ABOGADO, "requisitos_indispensables": ["Traslado máximo de 60 minutos"]}, ruta(AVATAR("profesional")))
    check(any(q.get("dato") == "traslado" and q.get("indispensable") for q in gt["filtro"]),
          "Profesional con indispensable de traslado: SÍ pregunta el traslado")
    go = generar(client, CONTADOR, ruta(WHATS("operativo")))
    base_op = {q.get("dato"): q for q in go["filtro"] if q.get("base")}
    check("zona" in base_op and "traslado" in base_op, "Operativo: pregunta colonia y traslado")
    check(base_op["sueldo"]["pregunta"].startswith("El sueldo es") and base_op["sueldo"]["pregunta"].endswith("¿Te interesa?"),
          f"Operativo: sueldo afirmativo ({base_op['sueldo']['pregunta']})")
    # la conversación usa la expectativa guardada
    v = Vacante(titulo="Abogado", sueldo="Desde $45,000 MXN mensuales", sueldo_periodicidad="mensual", enfoque_entrevista="profesional",
                guiones={pfu.CLAVE: ga["filtro"]}, requisitos="")
    crit = {c.get("dato"): c for c in pfu.criterios(v)}
    check(crit["sueldo"].get("expectativa") and crit["sueldo"]["pregunta"] == "¿Cuál es tu expectativa de sueldo mensual?" and "zona" not in crit,
          "el bot pregunta la expectativa de sueldo y no la colonia")

    # ===================================================== 6. Indispensables implícitos
    print("\n--- 6. Indispensables implícitos ---")
    req = ga["requisitos_indispensables"]
    prop = ga["indispensables_propuestos"]
    check("Cédula profesional" in req and "Cédula profesional" in prop, "Abogado: propone «Cédula profesional» (Propuesto por Red Human)")
    check("5 años de experiencia en derecho corporativo" in req and "5 años de experiencia en derecho corporativo" in prop,
          "Abogado Senior: propone «5 años de experiencia en derecho corporativo»")
    check(len(req) <= 5, "máximo 5 indispensables")
    check(any(q.get("propuesto") and q["requisito"] == "Cédula profesional" for q in ga["filtro"]),
          "en «Requisitos» la cédula lleva la etiqueta «Propuesto por Red Human»")
    cinco = [f"Requisito capturado {i}" for i in range(5)]
    llenos = ia.reglas_indispensables(cinco, [], seniority="Senior", titulo="Abogado", area="Legal")
    check(llenos[0] == cinco and llenos[1] == [], "con 5 indispensables de RH no se agrega ninguno (límite de 5)")
    check(ia.indispensables_implicitos("Contador general", "Contabilidad", "Junior") == ["Cédula profesional"],
          "Contador Junior: cédula sí, años de experiencia no")
    check(ia.indispensables_implicitos("Almacenista", "Operaciones", "Jefatura") == ["7 años de experiencia en operaciones"],
          "Jefatura (no regulada): solo «7 años de experiencia en [área]»")
    check(ia.reglas_indispensables(["5 años en litigio"], [], seniority="Senior", titulo="Abogado")[1] == ["Cédula profesional"],
          "si RH ya capturó experiencia, no se duplica la de años")

    # ===================================================== 7. Textos por plataforma (Markdown) y «Copiar»
    print("\n--- 7. Textos por plataforma ---")
    check(ia.sin_markdown("## Únete\n**Abogado** *Senior*\n- Contratos") == "Únete\nAbogado Senior\n• Contratos",
          "el texto de WhatsApp se guarda SIN Markdown")
    nodo = shutil.which("node")
    programa = r"""
const ts = require(process.argv[1] + '/node_modules/typescript');
const fs = require('fs');
const src = fs.readFileSync(process.argv[1] + '/lib/markdown-texto.ts', 'utf8');
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const m = { exports: {} }; new Function('module', 'exports', js)(m, m.exports); const o = m.exports;
const md = '# Abogado corporativo\n\n## Lo que harás\n- **Redactar** contratos\n- Llevar *asambleas*\n\nÚnete a __Despacho Norte__.';
const liga = o.ligaCanal('https://app.redhuman.mx/', 'abogado-corporativo', 'occ');
const r = {
  texto: o.markdownATexto(md),
  bloques: o.bloquesMarkdown(md).map(b => b.tipo),
  negrita: o.bloquesMarkdown(md)[2].partes,
  liga,
  ligaWa: o.ligaCanal('https://app.redhuman.mx', 'abogado-corporativo', 'whatsapp'),
  copiar: o.textoParaCopiar(md, liga),
  html: o.htmlParaCopiar(md, liga),
  slug: o.slugificar('Cajero(a) de sucursal'),
};
console.log(JSON.stringify(r));
"""
    salida = subprocess.run([nodo, "-e", programa, str(APP_DIR)], capture_output=True, text=True, encoding="utf-8")
    check(salida.returncode == 0, f"el módulo de Markdown corre en Node ({salida.stderr[:200]})")
    r = json.loads(salida.stdout.strip().splitlines()[-1])
    check(not re.search(r"(^|\n)#|\*\*|__|(?<!\w)\*(?!\s)", r["texto"]), f"sin «#», «##» ni «**» en el texto ({r['texto']!r})")
    check(r["bloques"] == ["titulo", "titulo", "vineta", "vineta", "parrafo"], "el Markdown se pinta como títulos, viñetas y párrafos")
    check(r["negrita"][0] == {"texto": "Redactar", "negrita": True}, "las negritas se pintan como negritas (sin asteriscos)")
    check(r["liga"] == "https://app.redhuman.mx/aplicar/abogado-corporativo?canal=occ"
          and r["ligaWa"].endswith("?canal=whatsapp"), "la liga de cada canal lleva su identificador (?canal=occ, ?canal=whatsapp)")
    check(r["copiar"].endswith("👉 Postúlate aquí: https://app.redhuman.mx/aplicar/abogado-corporativo?canal=occ") and "**" not in r["copiar"],
          "«Copiar» lleva el texto limpio y la liga del canal")
    check("<strong>Redactar</strong>" in r["html"] and 'href="https://app.redhuman.mx/aplicar/abogado-corporativo?canal=occ"' in r["html"],
          "y también texto enriquecido (HTML) con la liga")
    check(r["slug"] == "cajero-a-de-sucursal", "la liga provisional de una vacante nueva usa la misma regla de slug que el servidor")
    tp = (APP_DIR / "components/dashboard/vacantes/texto-plataforma.tsx").read_text(encoding="utf-8")
    pagina = (APP_DIR / "app/dashboard/vacantes/page.tsx").read_text(encoding="utf-8")
    check("BotonCopiarTexto" in tp and "textoParaCopiar(texto, liga)" in tp and 'canal="whatsapp"' in form and "sinMarkdown" in form,
          "cada texto del formulario trae «Copiar» y WhatsApp va sin Markdown")
    check("<MarkdownVista" in pagina and "ligaDe(activa)" in pagina and "<pre" not in pagina.split("function PestanasPlataforma")[1].split("\nfunction ")[0],
          "el detalle de la vacante pinta el Markdown y copia con la liga del canal")

    # ===================================================== 8. «Volver a generar» al cambiar el enfoque
    print("\n--- 8. Cambio de enfoque → «Volver a generar» ---")
    firma = form.split("export function firmaBloque1")[1].split("}")[0]
    check("c.enfoque_entrevista" in firma, "la firma del Bloque 1 incluye el «Enfoque» (cambiarlo después de generar → «Volver a generar»)")
    check("setFirmaGen(firmaBloque1(nuevo))" in form, "al regenerar la firma es la del contenido NUEVO: el aviso «Cambiaste los datos…» desaparece")
    check("enfoqueDeRuta(rutaGeneracion)" in form, "el formulario vigila el enfoque de la ruta")
    seccion = (APP_DIR / "components/dashboard/procesos/seccion-proceso-vacante.tsx").read_text(encoding="utf-8")
    check("enfoque: e.target.value" in seccion, "«Proceso de selección» de la vacante permite cambiar el enfoque de cada entrevista")
    # servidor: cambiar el enfoque en la ruta de la vacante cambia su enfoque y sus preguntas base
    r = client.post("/vacantes", json={**ABOGADO, "proceso": ruta(WHATS("operativo"))})
    check(r.status_code == 201, f"alta de la vacante ({r.status_code} {r.text[:150]})")
    cod = r.json()["id"]
    vac = db.query(Vacante).filter(Vacante.codigo == cod).first()
    check(vac.enfoque_entrevista == "operativo" and any(q.get("dato") == "zona" for q in pfu.de_vacante(vac)),
          "con WhatsApp Operativo la vacante pregunta la colonia")
    r = client.put(f"/procesos/vacantes/{cod}", json={"pasos": ruta(WHATS("profesional"))["pasos"]})
    check(r.status_code == 200, f"cambiar el enfoque en la ruta de la vacante ({r.status_code} {r.text[:150]})")
    db.expire_all()
    vac = db.query(Vacante).filter(Vacante.codigo == cod).first()
    check(vac.enfoque_entrevista == "profesional", "la vacante toma el enfoque nuevo de su ruta")
    check(not any(q.get("dato") == "zona" for q in pfu.de_vacante(vac))
          and any(q.get("expectativa") for q in pfu.de_vacante(vac)), "y sus preguntas base se recalculan (sin colonia, sueldo como expectativa)")

    db.close()
    print(f"\n{OK} comprobaciones OK — Cambio 43 verificado.")
