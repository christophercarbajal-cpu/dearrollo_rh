"""Cambio 58 (2026-10-15): nombre del proceso para RH en vacantes homónimas, nunca para el candidato. Base desechable.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambio58.py

1. La demo Fletes México deja DOS vacantes «Operador de 5ª rueda»; RH las distingue por `procesoNombre`
   («Fletes operadores» vs «Fletes operadores · Avatar»).
2. Los payloads públicos (/aplicar → `/vacantes/slug/{slug}`, portal → `/vacantes/publicas`) no traen el proceso ni su
   nombre; los textos y variables de WhatsApp (inicio del prefiltro, invitación al video) usan solo el título.
3. `limpiar_titulo_fletes.py` renombra el título temporal «Operador de 5ª rueda · Entrevista en video» (simulación por
   defecto, idempotente, la liga publicada no cambia) y el seed lo reconoce por su proceso (no duplica).
4. El frontend muestra «Proceso: …» en la tarjeta, la columna «Proceso» en la lista y el subtítulo del detalle.
"""

import json
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
_dir = _temporal.directorio("rh_c58_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c58.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-c58"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cuenta, Postulacion, Vacante  # noqa: E402
from app.serial import nombre_proceso, vacante_dict  # noqa: E402
from app.services import agenda_chat, plantillas_meta  # noqa: E402

OK = 0
TITULO = "Operador de 5ª rueda"
PROHIBIDOS = ("Fletes operadores", "Avatar", "Entrevista en video", "procesoNombre", "plantilla_nombre")


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def vacantes_fletes(db):
    cu = db.query(Cuenta).filter_by(slug="fletes-mexico").one()
    return cu, db.query(Vacante).filter(Vacante.cuenta_id == cu.id, Vacante.estado != "Eliminada").order_by(Vacante.id).all()


def main():
    import limpiar_titulo_fletes as limpieza
    import seed_fletes_mexico as seed

    with TestClient(app) as client:
        # ===================================================== 1. dos vacantes homónimas, distinto proceso
        print("\n--- 1. Fletes: vacantes homónimas distinguidas por el proceso ---")
        sys.argv = ["seed", "--aplicar"]
        check(seed.main() == 0, "seed_fletes_mexico.py --aplicar")
        db = SessionLocal()
        cu, vs = vacantes_fletes(db)
        check(len(vs) == 2 and all(v.titulo == TITULO for v in vs), f"dos vacantes «{TITULO}» → {[v.titulo for v in vs]}")
        nombres = sorted(vacante_dict(v)["procesoNombre"] for v in vs)
        check(nombres == [seed.PROCESO_A, seed.PROCESO_B], f"RH las distingue: Proceso: {nombres}")
        va = next(v for v in vs if vacante_dict(v)["procesoNombre"] == seed.PROCESO_A)
        vb = next(v for v in vs if vacante_dict(v)["procesoNombre"] == seed.PROCESO_B)
        sin_proceso = Vacante(titulo="x", cuenta_id=cu.id, proceso={})
        check(nombre_proceso(sin_proceso).endswith("(predeterminado)"),
              "vacante sin proceso → la ruta que recibirán sus candidatos, marcada «(predeterminado)»")

        # ===================================================== 2. nada del proceso hacia el candidato
        print("\n--- 2. Protección al candidato ---")
        for v in (va, vb):
            r = client.get(f"/vacantes/slug/{v.slug}")
            cuerpo = r.json()
            texto = json.dumps(cuerpo, ensure_ascii=False)
            check(r.status_code == 200 and cuerpo["titulo"] == TITULO and "proceso" not in cuerpo and "procesoNombre" not in cuerpo
                  and not any(x in texto for x in PROHIBIDOS),
                  f"/aplicar ({v.codigo}): solo «{TITULO}», sin el proceso ni su nombre")
        r = client.get("/vacantes/publicas", params={"cuenta": "fletes-mexico"})
        texto = json.dumps(r.json(), ensure_ascii=False)
        check(r.status_code == 200 and len(r.json()) == 2 and not any(x in texto for x in PROHIBIDOS)
              and all("proceso" not in x for x in r.json()), "portal público: sin el proceso ni su nombre")

        for v in (va, vb):
            p = Postulacion(vacante=v, candidato=Candidato(nombre="Juan Pérez", cuenta_id=v.cuenta_id), cuenta_id=v.cuenta_id, analisis={})
            mensajes = [agenda_chat.invitacion_video(p), " ".join(agenda_chat.parametros_video(p)),
                        plantillas_meta.texto_inicio(p), " ".join(plantillas_meta.parametros_inicio(p))]
            check(all(not any(x in m for x in PROHIBIDOS) for m in mensajes) and TITULO in mensajes[0] and TITULO in mensajes[2],
                  f"WhatsApp ({vacante_dict(v)['procesoNombre']}): invitación, inicio y sus variables usan solo el título")
        db.rollback()

        # ===================================================== 3. limpieza del título temporal
        print("\n--- 3. limpiar_titulo_fletes.py ---")
        slug_b = vb.slug
        vb.titulo = limpieza.TITULO_ANTERIOR
        db.commit()
        check(limpieza.limpiar(db, aplicar=False) == [(vb.codigo, seed.PROCESO_B, vb.estado)], "simulación: encuentra la vacante B")
        db.rollback()
        db.refresh(vb)
        check(vb.titulo == limpieza.TITULO_ANTERIOR, "la simulación no escribe")
        sys.argv = ["limpiar", "--forzar"]
        check(limpieza.main() == 0, "limpiar_titulo_fletes.py --forzar")
        db.expire_all()
        check(vb.titulo == TITULO and vb.slug == slug_b, f"título «{TITULO}» y la liga publicada no cambia ({slug_b})")
        check(limpieza.limpiar(db, aplicar=True) == [], "idempotente: la segunda corrida no encuentra nada")
        db.rollback()

        vb.titulo = limpieza.TITULO_ANTERIOR
        db.commit()
        db.close()
        sys.argv = ["seed", "--aplicar"]
        check(seed.main() == 0, "el seed vuelve a correr con la B en su título temporal")
        db = SessionLocal()
        _cu, vs = vacantes_fletes(db)
        check(len(vs) == 2 and all(v.titulo == TITULO for v in vs)
              and sorted(vacante_dict(v)["procesoNombre"] for v in vs) == [seed.PROCESO_A, seed.PROCESO_B],
              "el seed reconoce cada vacante por su proceso: renombra la B y no duplica")
        db.close()

    # ===================================================== 4. frontend
    print("\n--- 4. Frontend ---")
    pagina = (RAIZ.parent / "red-human-app/app/dashboard/vacantes/page.tsx").read_text(encoding="utf-8")
    check("Proceso: {v.procesoNombre}" in pagina, "tarjeta: «Proceso: …» debajo del área y la empresa")
    check('<th className="px-4 py-3 text-left">Proceso</th>' in pagina and "{v.procesoNombre || \"—\"}" in pagina,
          "lista: columna «Proceso»")
    check("subtitulo={v.procesoNombre ? `Proceso: ${v.procesoNombre}` : undefined}" in pagina, "detalle: «Proceso: …» bajo el título")
    for ruta in ("app/aplicar", "app/portal"):
        for f in (RAIZ.parent / "red-human-app" / ruta).rglob("*.tsx"):
            check("procesoNombre" not in f.read_text(encoding="utf-8"), f"{f.relative_to(RAIZ.parent / 'red-human-app')}: sin el proceso")

    print(f"\n{OK} verificaciones OK")


if __name__ == "__main__":
    main()
