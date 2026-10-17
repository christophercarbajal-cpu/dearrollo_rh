"""Generación de vacantes por IA (2026-10-17). Base desechable; la IA se SIMULA (cliente falso, sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_prefiltro_web_whatsapp.py

1. Prompt: «Generar con Red Human» Y «Regenerar» (vista previa) usan el MISMO prompt con la estructura exacta de los dos
   bloques (PREFILTRO WEB Sí/No · PREFILTRO WHATSAPP de continuación que NO repite la web).
2. Garantía en código: cada requisito trae su pregunta web y su continuación de WhatsApp; si la IA repite la web, se
   reemplaza por la continuación determinista. Modo demo (sin clave) igual.
3. Chat: la continuación solo se pregunta si el candidato ya dijo «Sí» en la web; nunca descarta.
4. «Mostrar sueldo» apagado: la regla «Sueldo competitivo» va en el prompt de la redacción y ninguna cifra de dinero queda en
   la descripción ni en los textos públicos aunque el modelo la escriba. Encendido, el sueldo se respeta.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_pfwa_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "pfwa.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-pfwa"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Usuario, UsuarioCuenta  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


class ClienteFalso:
    """Imita `client.responses.parse`: guarda el prompt y regresa lo que `respuesta(ficha_input)` arme."""

    def __init__(self, respuesta):
        self.llamadas = []
        self.respuesta = respuesta
        self.responses = SimpleNamespace(parse=self.parse)

    def parse(self, model, instructions, input, text_format):  # noqa: A002 — misma firma que el SDK
        self.llamadas.append({"instructions": instructions, "input": input})
        return SimpleNamespace(output_parsed=self.respuesta())


def main():
    from app.services import ia
    from app.services import preguntas_filtro as pfu
    from app.services import prefiltro_conversacional as pconv
    from app.services import proceso as sproc

    with TestClient(app) as client:
        db = SessionLocal()
        admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
        cuenta = Cuenta(nombre="PFWA interna", nombre_comercial="Logística Norte", estado="Activa", slug="pfwa-norte")
        db.add(cuenta)
        db.flush()
        db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
        db.commit()
        for dep in (usuario_actual, usuario_decisor):
            app.dependency_overrides[dep] = lambda: admin
        app.dependency_overrides[cuenta_actual] = lambda: cuenta
        MASIVOS = sproc.ruta_base("masivos_sin_documentos")["pasos"]
        reqs = ["Licencia de manejo tipo B", "Disponibilidad para rolar turnos", "Saber usar montacargas"]
        ficha = {"titulo": "Operador de montacargas", "area": "Operaciones", "seniority": "Junior", "ubicacion_estado": "Nuevo León",
                 "ubicacion_municipio": "Apodaca", "modalidad": "Presencial", "sueldo_desde": 12000, "sueldo_periodicidad": "mensual",
                 "descripcion": "Operador de montacargas para centro de distribución. Sueldo de $12,000 MXN al mes más bonos.",
                 "requisitos_indispensables": reqs, "proceso": {"pasos": MASIVOS}}

        # ===================================================== 1. el prompt
        print("\n--- 1. Prompt con los dos bloques (generar y regenerar) ---")
        texto = ia.REGLAS_PREFILTRO_WEB_WHATSAPP
        for frase in ("1. PREFILTRO WEB (3 preguntas):", "Redacta 3 preguntas directas (Sí/No) para formulario web",
                      "Su función es registrar la respuesta del candidato.", "2. PREFILTRO WHATSAPP (3 preguntas):",
                      "Cada una debe ser la continuación natural de las preguntas del prefiltro web.",
                      "REGLA OBLIGATORIA ESTRICTA: NO repitas las preguntas de la web.",
                      "asume que ya cumplió el requisito y continúa el tema pidiendo detalles, contexto, tamaño de operaciones o ejemplos concretos"):
            check(frase in texto, f"estructura exacta: «{frase[:60]}…»")
        check(texto in ia._REGLAS, "el bloque va dentro de las reglas del redactor (system prompt)")

        def respuesta_ia(repetir=False, con_monto=True):
            def armar():
                g = ia._demo_vacante(ia.FichaVacante(titulo="Operador de montacargas", requisitos_indispensables=reqs))
                g.requisitos_indispensables = list(reqs)
                web = {"Licencia de manejo tipo B": "¿Tienes licencia de manejo tipo B?",
                       "Disponibilidad para rolar turnos": "¿Tienes disponibilidad para rolar turnos?",
                       "Saber usar montacargas": "¿Sabes usar montacargas?"}
                wa = {"Licencia de manejo tipo B": "¿Qué unidades has manejado con esa licencia?",
                      "Disponibilidad para rolar turnos": "¿En qué turnos has trabajado antes?",
                      "Saber usar montacargas": "¿Qué capacidad de carga tenían los montacargas que has operado?"}
                g.preguntas_requisitos = [ia.PreguntaRequisito(requisito=r, pregunta=web[r], pregunta_web=web[r],
                                                               pregunta_whatsapp=web[r] if repetir else wa[r]) for r in reqs]
                if con_monto:
                    g.descripcion = "Buscamos operador de montacargas. Ofrecemos un sueldo de $12,000 MXN mensuales. Turnos rotativos."
                    g.resumen = "Operador de montacargas con sueldo de 12 mil pesos al mes."
                    g.portal.page = "**Lo que ofrecemos**\n- Sueldo $12,000 mensuales\n- Bonos de productividad"
                    g.texto_whatsapp = "¡Vacante! Operador de montacargas. Sueldo $12,000. Postúlate."
                return g
            return armar

        falso = ClienteFalso(respuesta_ia())
        original = ia._client
        ia._client = lambda: falso
        try:
            r = client.post("/vacantes/generar", json={**ficha, "mostrar_sueldo": True})
            check(r.status_code == 200, f"/vacantes/generar con IA simulada ({r.status_code})")
            g = r.json()
            check(ia.REGLAS_PREFILTRO_WEB_WHATSAPP in falso.llamadas[-1]["instructions"], "«Generar» manda el prompt de los dos bloques")
            check(ia.REGLA_SUELDO_COMPETITIVO not in falso.llamadas[-1]["instructions"], "con sueldo publicado no va la regla de «Sueldo competitivo»")
            check("12,000" in g["descripcion"], "con sueldo publicado la descripción conserva la cifra")
            ind = [q for q in g["filtro"] if q.get("indispensable")]
            check(len(ind) == 3 and all(q.get("seguimiento") for q in ind), "cada requisito trae su pregunta de WhatsApp (continuación)")
            web_de = {q["requisito"]: (q.get("pregunta_web") or q["pregunta"]) for q in ind}
            check(web_de["Licencia de manejo tipo B"] == "¿Tienes licencia de manejo tipo B?" and
                  web_de["Saber usar montacargas"] == "¿Sabes usar montacargas?", f"web = la pregunta Sí/No de la IA ({web_de})")
            check(all(not ia.repite_pregunta(q["seguimiento"], web_de[q["requisito"]]) for q in ind),
                  "WhatsApp nunca repite la pregunta web")
            check({q["requisito"]: q["seguimiento"] for q in ind}["Saber usar montacargas"]
                  == "¿Qué capacidad de carga tenían los montacargas que has operado?", "WhatsApp = la continuación propuesta por la IA")
            check([q["pregunta"] for q in g["preguntas_filtro"]] == [web_de[x] for x in reqs],
                  "el formulario web (derivado) trae solo las 3 preguntas Sí/No de los requisitos")

            # «Regenerar» de la vista previa: mismo prompt; si la IA repite la web, se corrige en código
            falso.respuesta = respuesta_ia(repetir=True)
            n = len(falso.llamadas)
            r = client.post("/vacantes/preguntas-filtro/regenerar", json={**ficha, "filtro": [], "indispensables_propuestos": []})
            check(r.status_code == 200 and len(falso.llamadas) == n + 1, f"«Regenerar» llama a la IA ({r.status_code})")
            check(ia.REGLAS_PREFILTRO_WEB_WHATSAPP in falso.llamadas[-1]["instructions"], "«Regenerar» usa el MISMO prompt actualizado")
            ind2 = [q for q in r.json()["filtro"] if q.get("indispensable")]
            check(all(q.get("seguimiento") and not ia.repite_pregunta(q["seguimiento"], q.get("pregunta_web") or q["pregunta"])
                      for q in ind2), "IA que repite la web → se reemplaza por la continuación determinista")
            check(next(q for q in ind2 if q["requisito"] == "Licencia de manejo tipo B")["seguimiento"]
                  == "¿Qué tipo de unidades has manejado con tu licencia?", "continuación determinista de la licencia")

            # ===================================================== 4. sueldo competitivo
            print("\n--- 4. «Mostrar sueldo» apagado: sin cifras ---")
            falso.respuesta = respuesta_ia()
            r = client.post("/vacantes/generar", json={**ficha, "mostrar_sueldo": False})
            check(r.status_code == 200, f"/vacantes/generar sin mostrar sueldo ({r.status_code})")
            s = r.json()
            ins, inp = falso.llamadas[-1]["instructions"], falso.llamadas[-1]["input"]
            check(ia.REGLA_SUELDO_COMPETITIVO in ins, "la regla crítica de «Sueldo competitivo» va en el prompt")
            check("REGLA CRÍTICA: La vacante es de 'Sueldo competitivo'. TIENES ESTRICTAMENTE PROHIBIDO mencionar cifras" in ins,
                  "texto exacto de la regla")
            check("- Sueldo: Sueldo competitivo" in inp and "12,000 MXN mensual" not in inp.split("- Descripción breve")[0],
                  "la ficha que recibe la IA no trae la cifra en el campo de sueldo")
            for campo in ("descripcion", "resumen", "texto_whatsapp"):
                check(not ia.tiene_monto(s[campo]), f"sin cifras de dinero en «{campo}» ({s[campo][:70]!r})")
            check(not ia.tiene_monto(s["portal"]["page"]) and "Bonos de productividad" in s["portal"]["page"],
                  "la página del portal pierde solo el renglón del sueldo")
            check(s["resumen"].startswith("Buscamos Operador de montacargas") and "sueldo competitivo" in s["resumen"],
                  f"el resumen que era solo el sueldo no queda vacío ({s['resumen']!r})")
            check("sueldo competitivo" in s["descripcion"].lower() and "Turnos rotativos." in s["descripcion"],
                  "la descripción dice «sueldo competitivo» y conserva el resto")
        finally:
            ia._client = original

        # demo (sin clave): también sin cifras y con continuación de WhatsApp
        r = client.post("/vacantes/generar", json={**ficha, "mostrar_sueldo": False})
        d = r.json()
        check(r.status_code == 200 and not ia.tiene_monto(d["descripcion"]) and not ia.tiene_monto(d["resumen"]),
              "modo demo: sin cifras con «Mostrar sueldo» apagado")
        check(all(q.get("seguimiento") for q in d["filtro"] if q.get("indispensable")), "modo demo: cada requisito trae su continuación")
        check(ia.sin_montos("Pago semanal de 3,500 pesos. Contratación inmediata.") == "Contratación inmediata.", "sin_montos: «3,500 pesos»")
        check(ia.sin_montos("Horario de 8 a 17 h.") == "Horario de 8 a 17 h.", "sin_montos no toca números que no son dinero")

        # ===================================================== 3. chat
        print("\n--- 3. Chat: la continuación solo tras el «Sí» de la web ---")
        v = SimpleNamespace(guiones={pfu.CLAVE: g["filtro"]}, preguntas_filtro=g["preguntas_filtro"], preguntas_filtro_whatsapp=[],
                            sueldo_periodicidad="mensual", sueldo="$12,000 mensual")
        crit = pfu.criterios(v)
        seg = [c for c in crit if c.get("seguimiento_de")]
        check(len(seg) == 3 and all(not c["indispensable"] and c["tipo"] == "abierta" for c in seg),
              "3 continuaciones en el chat, abiertas y que nunca descartan")
        i_lic = next(k for k, c in enumerate(crit) if c.get("criterio") == "Licencia de manejo tipo B")
        check(crit[i_lic + 1].get("seguimiento_de") == crit[i_lic]["id"], "la continuación va justo después de su requisito")
        padre = crit[i_lic]["id"]
        check(pconv._falta(crit[i_lic + 1], {padre: {"valor": "si", "fuente": "web"}}), "«Sí» en la web → el chat sigue con la continuación")
        check(not pconv._falta(crit[i_lic + 1], {padre: {"valor": "si", "fuente": "whatsapp"}}),
              "contestado solo en el chat → no se repite el tema")
        check(not pconv._falta(crit[i_lic + 1], {padre: {"valor": "no", "fuente": "web"}}), "«No» en la web → se confirma, no se pide detalle")
        check(pconv._falta(crit[i_lic + 1], {padre: {"valor": "si", "fuente": "whatsapp", "confirmacion_web": "corregida"}}),
              "corrigió en el chat el «No» de la web → sigue con la continuación")
        # ===================================================== 5. Ficha de Nueva vacante (2026-10-17)
        print("\n--- 5. Tipos, validación por sección con reintentos y respaldo ---")
        from app.services import validacion_preguntas as vpr

        esperados = {"3 años de experiencia en almacén": "Experiencia", "Vivir en Apodaca": "Ubicación",
                     "Disponibilidad para iniciar de inmediato": "Fecha de inicio", "Disponibilidad para rolar turnos": "Horario",
                     "Secundaria terminada": "Escolaridad", "Licencia de manejo tipo B": "Licencia",
                     "Manejo de Excel intermedio": "Herramienta", "Inglés avanzado": "Idioma", "Saber leer y escribir": "Otro"}
        check(all(vpr.clasificar(r) == t for r, t in esperados.items()) and set(esperados.values()) == set(vpr.TIPOS_INDISPENSABLE),
              "cada indispensable se clasifica en uno de los 9 tipos")
        check(all(vpr.menciona(vpr.respaldo(r, t, s), r, t) for r, t in esperados.items() for s in vpr.SECCIONES),
              "cada tipo tiene su pregunta de respaldo fija (web y WhatsApp) y nombra el concepto")
        check(vpr.problemas([("Licencia de manejo tipo B", "Licencia", "¿Tienes licencia tipo B?"),
                             ("Disponibilidad para rolar turnos", "Horario", "¿Tienes licencia tipo B?")]) == {1: "repite la pregunta de «Licencia de manejo tipo B»"}
              or 1 in vpr.problemas([("Licencia de manejo tipo B", "Licencia", "¿Tienes licencia tipo B?"),
                                     ("Disponibilidad para rolar turnos", "Horario", "¿Tienes licencia tipo B?")]),
              "dos preguntas iguales/traslapadas = problema")
        check(1 in vpr.problemas([("Licencia de manejo tipo B", "Licencia", "¿Tienes licencia tipo B?"),
                                  ("Saber usar montacargas", "Herramienta", "¿Tienes buena actitud?")]),
              "una pregunta que no menciona el concepto de su indispensable = problema")
        check("CONTEXTO ÚNICO" in ia._REGLAS and "nada automotriz en una vacante de software" in ia._REGLAS,
              "prompt: sin mezcla de contextos (el nombre de la empresa no define la industria)")
        check("SUELDO EXACTO" in ia._REGLAS and "«El sueldo es… ¿te interesa?», «El puesto es… ¿te interesa?» están prohibidas" in ia._REGLAS,
              "prompt: sueldo exacto de la vacante y sin «¿te interesa?» en el prefiltro")

        class ClienteSecciones(ClienteFalso):
            """La generación regresa preguntas MALAS (repetidas / sin concepto); cada reintento de sección también."""

            def __init__(self, respuesta, reintento):
                super().__init__(respuesta)
                self.reintento = reintento
                self.secciones = []

            def parse(self, model, instructions, input, text_format):  # noqa: A002
                if text_format.__name__ == "_SeccionRegenerada":
                    self.secciones.append(instructions)
                    return SimpleNamespace(output_parsed=self.reintento(len(self.secciones)))
                return super().parse(model, instructions, input, text_format)

        def generacion_mala():
            g = respuesta_ia(con_monto=False)()
            g.preguntas_requisitos = [ia.PreguntaRequisito(requisito=r, pregunta="¿Tienes buena actitud?", pregunta_web="¿Tienes buena actitud?",
                                                           pregunta_whatsapp="¿Cuál es tu color favorito?", tipo="")
                                      for r in reqs]
            return g

        def reintento_malo(n):
            return ia._SeccionRegenerada(preguntas=[ia._PreguntaSeccion(requisito=r, pregunta="¿Te gusta trabajar?") for r in reqs])

        malo = ClienteSecciones(generacion_mala, reintento_malo)
        ia._client = lambda: malo
        try:
            r = client.post("/vacantes/generar", json={**ficha, "mostrar_sueldo": True})
        finally:
            ia._client = original
        ind = [q for q in r.json()["filtro"] if q.get("indispensable")]
        check(r.status_code == 200 and len(malo.secciones) == 2 * vpr.MAX_REINTENTOS,
              f"máximo {vpr.MAX_REINTENTOS} reintentos por sección (web y WhatsApp) en batch ({len(malo.secciones)} llamadas)")
        web_f = [q.get("pregunta_web") or q["pregunta"] for q in ind]
        check(web_f == [vpr.respaldo(q["requisito"], q["tipo_indispensable"], "web") for q in ind],
              f"tras los reintentos fallidos → la pregunta web de respaldo de cada tipo ({web_f})")
        check([q["seguimiento"] for q in ind] == [vpr.respaldo(q["requisito"], q["tipo_indispensable"], "whatsapp") for q in ind],
              "… y la de WhatsApp de respaldo")
        check([q["tipo_indispensable"] for q in ind] == ["Licencia", "Horario", "Herramienta"], f"cada pregunta lleva el tipo de su indispensable ({[q['tipo_indispensable'] for q in ind]})")

        def reintento_bueno(n):
            return ia._SeccionRegenerada(preguntas=[ia._PreguntaSeccion(requisito=r, pregunta=q) for r, q in zip(reqs, (
                "¿Tienes licencia de manejo tipo B vigente?", "¿Puedes rolar turnos cada semana?", "¿Sabes operar un montacargas?"))])

        bien = ClienteSecciones(generacion_mala, reintento_bueno)
        ia._client = lambda: bien
        try:
            r = client.post("/vacantes/generar", json={**ficha, "mostrar_sueldo": True, "instrucciones": "Tono más formal, enfocado en seguridad."})
        finally:
            ia._client = original
        ind = [q for q in r.json()["filtro"] if q.get("indispensable")]
        check((ind[2].get("pregunta_web") or ind[2]["pregunta"]) == "¿Sabes operar un montacargas?",
              f"un reintento que corrige la sección se usa tal cual ({ind[2].get('pregunta_web') or ind[2]['pregunta']})")
        check("Instrucciones adicionales de RH" in bien.llamadas[0]["input"] and "Tono más formal, enfocado en seguridad." in bien.llamadas[0]["input"],
              "«Da más instrucciones a Red Human» llega al prompt de la generación")

        print("\n--- 5b. El sueldo de los textos = el de la vacante ---")

        def con_sueldo_ajeno():
            g = respuesta_ia(con_monto=False)()
            g.descripcion = "Buscamos operador. Ofrecemos $15,000 mensuales. Turnos rotativos."
            g.resumen = "Operador con sueldo de $12,000 MXN mensuales."
            return g

        ajeno = ClienteFalso(con_sueldo_ajeno)
        ia._client = lambda: ajeno
        try:
            s2 = client.post("/vacantes/generar", json={**ficha, "mostrar_sueldo": True}).json()
        finally:
            ia._client = original
        check("15,000" not in s2["descripcion"] and "Turnos rotativos." in s2["descripcion"], f"un sueldo distinto al de la vacante se quita ({s2['descripcion']!r})")
        check("12,000" in s2["resumen"], "el sueldo capturado ($12,000) se conserva")
        vq = SimpleNamespace(guiones={pfu.CLAVE: s2["filtro"]}, preguntas_filtro=s2["preguntas_filtro"], preguntas_filtro_whatsapp=[],
                             sueldo_periodicidad="mensual", sueldo="$12,000 MXN mensual", mostrar_sueldo=True, modalidad="Presencial",
                             ubicaciones=[], ubicacion_estado="", ubicacion_municipio="", ubicacion="", titulo="Operador de montacargas")
        check(not any(q.get("dato") == "sueldo" for q in s2["filtro"]) and not any(c.get("dato") in ("sueldo", "interes") for c in pfu.criterios(vq)),
              "el prefiltro de WhatsApp ya no trae «El sueldo es… ¿te interesa?» ni «El puesto es… ¿te interesa?»")
        db.close()
    print(f"\nTodo OK ({OK} comprobaciones)")


if __name__ == "__main__":
    main()
