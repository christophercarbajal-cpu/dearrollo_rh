"""Bloque 2 (2026-10-16): creación / ficha de vacante. Base desechable.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_bloque2_vacante.py

1. «Mostrar sueldo en la publicación»: la vacante guarda `mostrar_sueldo`; desmarcado, todo lo público (portal, liga
   /aplicar) dice «Sueldo competitivo» y RH sigue viendo el monto; `models.sueldo_publicado` lo respeta.
2. Preguntas base que se quitan con la «X»: se conservan marcadas (`quitada`), nadie las pregunta y se pueden restaurar.
3. Red Human propone SIEMPRE al menos 3 requisitos (= 3 preguntas obligatorias); RH puede llegar a 5; cada pregunta trae su
   origen («De tu descripción», «Propuesto por Red Human», «Agregado por ti»).
4. La web pregunta Sí/No por requisito (`pregunta_web`) y el chat retoma la experiencia conversando.
5. «Regenerar» conserva lo «Agregado por ti» y vuelve a proponer el resto.
6. UI: checkbox de sueldo, vista previa en el orden de la ruta (3.1 · 3.2 · 3.3) y sin «mínimo 1 para publicar».
"""

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
_dir = _temporal.directorio("rh_b2vac_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "b2vac.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-b2vac"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Usuario, UsuarioCuenta, Vacante, sueldo_publicado  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def main():
    from app.services import ia
    from app.services import preguntas_filtro as pfu
    from app.services import proceso as sproc

    with TestClient(app) as client:
        db = SessionLocal()
        admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
        cuenta = Cuenta(nombre="B2 interna", nombre_comercial="Almacenes Bajío", estado="Activa", slug="b2-bajio")
        db.add(cuenta)
        db.flush()
        db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
        db.commit()
        for dep in (usuario_actual, usuario_decisor):
            app.dependency_overrides[dep] = lambda: admin
        app.dependency_overrides[cuenta_actual] = lambda: cuenta
        MASIVOS = sproc.ruta_base("masivos_sin_documentos")["pasos"]
        ficha = {"titulo": "Almacenista", "area": "Operaciones", "seniority": "Junior", "ubicacion_estado": "Guanajuato",
                 "ubicacion_municipio": "León", "modalidad": "Presencial", "sueldo_desde": 9500, "sueldo_periodicidad": "mensual",
                 "descripcion": "Buscamos almacenista con experiencia en almacén para surtir pedidos.",
                 "requisitos_indispensables": ["Secundaria terminada"], "proceso": {"pasos": MASIVOS}}

        # ===================================================== 3/4. tres preguntas obligatorias con origen
        print("\n--- 3/4. Siempre 3 preguntas, con origen; web Sí/No y chat conversacional ---")
        r = client.post("/vacantes/generar", json=ficha)
        check(r.status_code == 200, f"/vacantes/generar ({r.status_code})")
        g = r.json()
        reqs = g["requisitos_indispensables"]
        check(len(reqs) == 3 and reqs[0] == "Secundaria terminada", f"lo de RH primero y Red Human completa hasta 3 ({reqs})")
        indisp = [q for q in g["filtro"] if q.get("indispensable")]
        check(len(indisp) == 3, "una pregunta obligatoria por requisito")
        orig = {q["requisito"]: q.get("origen") for q in indisp}
        check(orig["Secundaria terminada"] == "rh" and all(o in pfu.ORIGENES for o in orig.values()),
              f"cada pregunta trae su origen ({orig})")
        check(next(q for q in indisp if q["requisito"] == "Secundaria terminada")["etiqueta"] == "Agregado por ti",
              "lo capturado por RH se etiqueta «Agregado por ti»")
        check(all(q["tipo"] == "si_no" for q in g["preguntas_filtro"] if not q.get("base")),
              "la solicitud web pregunta SOLO Sí/No por requisito")
        esc = next(q for q in indisp if q["requisito"] == "Secundaria terminada")
        check(esc.get("pregunta_web") == "¿Tienes secundaria terminada?", f"versión Sí/No para la web ({esc.get('pregunta_web')})")
        q_exp = pfu.armar(["2 años de experiencia en almacén"], propuestos=["2 años de experiencia en almacén"],
                          descripcion="experiencia en almacén")[0]
        check(q_exp["pregunta"].startswith("Háblame de tu experiencia") and q_exp["pregunta_web"].startswith("¿Tienes al menos 2 años"),
              f"experiencia: el chat conversa y la web pregunta Sí/No («{q_exp['pregunta']}» / «{q_exp['pregunta_web']}»)")
        check(q_exp["origen"] == "descripcion" and q_exp["etiqueta"] == "De tu descripción",
              "lo propuesto que menciona la descripción → «De tu descripción»")
        cinco, prop, _ = ia.reglas_indispensables(["A", "B", "C", "D", "E", "F"][:5], ["Licencia tipo B"], titulo="Chofer")
        check(len(cinco) == 5 and not prop, "con 5 capturados por RH no se propone nada más (máximo 5)")
        tres, prop3, _ = ia.reglas_indispensables([], [], titulo="Cajero", seniority="Sin experiencia")
        check(len(tres) == 3 and not any(ia.es_de_experiencia(x) for x in tres),
              f"sin nada capturado: 3 propuestas y ninguna de experiencia si es «Sin experiencia» ({tres})")

        # ===================================================== 5. Regenerar
        print("\n--- 5. Regenerar ---")
        r = client.post("/vacantes/preguntas-filtro/regenerar", json={**ficha, "requisitos_indispensables": reqs,
                                                                       "indispensables_propuestos": g["indispensables_propuestos"],
                                                                       "filtro": g["filtro"]})
        check(r.status_code == 200, f"POST /vacantes/preguntas-filtro/regenerar ({r.status_code})")
        rg = r.json()
        check("Secundaria terminada" in rg["requisitos"] and len(rg["requisitos"]) == 3
              and "Secundaria terminada" not in rg["indispensables_propuestos"],
              f"conserva lo «Agregado por ti» y vuelve a proponer el resto ({rg['requisitos']})")

        # ===================================================== 1. mostrar sueldo
        print("\n--- 1. Mostrar sueldo en la publicación ---")
        r = client.post("/vacantes", json={**ficha, "mostrar_sueldo": False, "publicar": True, "filtro": g["filtro"],
                                           "requisitos_indispensables": reqs, "indispensables_propuestos": g["indispensables_propuestos"]})
        check(r.status_code == 201, f"crear vacante con el sueldo oculto ({r.status_code} {r.text[:200]})")
        cod = r.json()["id"]
        v = db.query(Vacante).filter(Vacante.codigo == cod).first()
        db.refresh(v)
        check(v.mostrar_sueldo is False and not sueldo_publicado(v), "se guarda y `sueldo_publicado` lo respeta")
        rh = client.get(f"/vacantes/{cod}").json()
        check("9,500" in rh["sueldo"] and rh["mostrarSueldo"] is False and rh["sueldoPublico"] == "Sueldo competitivo",
              f"RH sigue viendo el monto ({rh['sueldo']})")
        pub = client.get(f"/vacantes/slug/{v.slug}").json()
        check(pub["sueldo"] == "Sueldo competitivo" and pub.get("sueldoDesde") is None, "la liga /aplicar dice «Sueldo competitivo»")
        portal = [x for x in client.get(f"/vacantes/publicas?cuenta={cuenta.id}").json() if x["id"] == cod]
        check(portal and portal[0]["sueldo"] == "Sueldo competitivo", "el portal dice «Sueldo competitivo»")
        r = client.patch(f"/vacantes/{cod}", json={"mostrar_sueldo": True})
        check(r.status_code == 200, f"PATCH mostrar_sueldo ({r.status_code})")
        db.refresh(v)
        check(sueldo_publicado(v) and "9,500" in client.get(f"/vacantes/slug/{v.slug}").json()["sueldo"],
              "marcado otra vez → lo público muestra el monto")

        # ===================================================== 2. quitar preguntas base
        print("\n--- 2. Preguntas base con «X» ---")
        filtro = client.get(f"/vacantes/{cod}").json()["filtro"]
        zona = next((q for q in filtro if q.get("dato") == "zona"), None)
        check(zona is not None and zona["base"], "la vacante trae la pregunta base de colonia")
        r = client.patch(f"/vacantes/{cod}", json={"filtro": [{**q, "quitada": True} if q.get("dato") == "zona" else q for q in filtro]})
        check(r.status_code == 200, f"PATCH filtro con la base quitada ({r.status_code})")
        db.refresh(v)
        check(pfu.bases_quitadas(v) == ["zona"], f"queda registrada como quitada ({pfu.bases_quitadas(v)})")
        check(not any(q.get("dato") == "zona" for q in pfu.de_vacante(v)) and not any(c.get("dato") == "zona" for c in pfu.criterios(v)),
              "nadie la pregunta (ni el chat ni el formulario)")
        filtro2 = client.get(f"/vacantes/{cod}").json()["filtro"]
        check(any(q.get("dato") == "zona" and q.get("quitada") for q in filtro2), "el editor la sigue viendo (para restaurarla)")
        r = client.patch(f"/vacantes/{cod}", json={"filtro": [{**q, "quitada": False} for q in filtro2]})
        db.refresh(v)
        check(r.status_code == 200 and not pfu.bases_quitadas(v) and any(c.get("dato") == "zona" for c in pfu.criterios(v)),
              "«Restaurar» la vuelve a preguntar")
        db.close()

    # ===================================================== 6. UI
    print("\n--- 6. Formulario ---")
    app_dir = RAIZ.parent / "red-human-app"
    form = (app_dir / "components/dashboard/vacantes/formulario-contenido.tsx").read_text(encoding="utf-8")
    vista = (app_dir / "components/dashboard/vacantes/vista-previa-preguntas.tsx").read_text(encoding="utf-8")
    check("Mostrar sueldo en la publicación" in form and "mostrar_sueldo: c.mostrar_sueldo" in form, "checkbox «Mostrar sueldo en la publicación»")
    check("para publicar)" not in form and "minIndispensables && n <" not in form, "sin «mínimo 1 para publicar»")
    check(all(t in form for t in ("Solicitud web`}", "Prefiltro por WhatsApp`}", "Entrevista`}", 'numero("solicitud")')),
          "Bloque 2 en el orden de la ruta (3.1 Solicitud web · 3.2 Prefiltro por WhatsApp · 3.3 Entrevista)")
    check(all(t in vista for t in ("Agregar pregunta", "Regenerar", "MAX_PREGUNTAS_FILTRO", "Restaurar", "Editar", "Borrar")),
          "vista previa con Editar, Borrar, Regenerar, Agregar (máx. 5) y base con «X» / Restaurar")
    print(f"\n{OK} comprobaciones OK")


if __name__ == "__main__":
    main()
