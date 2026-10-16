"""Bugs del tester (2026-10-16). Base desechable.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_bugs_2026_10_16.py

1. Expectativa de sueldo: con sueldo publicado NO se pregunta (ni en la solicitud web ni en el chat) y no hay regla de
   «Requiere revisión» por expectativa; sin sueldo se sigue preguntando.
2. TTS del avatar: los montos del systemPrompt / initialMessage se dicen «X pesos mexicanos» (sin «$», sin comas de
   miles, sin centavos en cero, sin MXN).
3. Tablero de Candidatos: sin ?vacante= abre en «Todas las vacantes» (no autoselecciona la de más candidatos).
4. CLABE: en la configuración base ya no es obligatoria (no bloquea contrato/alta/«Documentos»); si la vacante la pide,
   sí; la corrección única deja opcional la CLABE heredada de la base en expedientes existentes.
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
_dir = _temporal.directorio("rh_b1016_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "b1016.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-b1016"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Documento, Expediente, Postulacion, Vacante, clave_documento, documentos_base_requeridos  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def main():
    from app.services import catalogo_documentos, onboarding
    from app.services import prefiltro_conversacional as pconv
    from app.services import solicitud_web as ssw
    from app.services.avatar import persona_config, texto_para_voz

    with TestClient(app):
        db = SessionLocal()
        # ===================================================== 1. expectativa de sueldo
        print("\n--- 1. Expectativa de sueldo ---")
        v = db.query(Vacante).filter(Vacante.estado != "Eliminada").order_by(Vacante.id).first()
        check(v is not None, f"vacante de prueba {v.codigo if v else ''}")
        v.sueldo_desde, v.sueldo_hasta, v.sueldo_moneda, v.sueldo_periodicidad = 55000, None, "MXN", "mensual"
        v.sueldo = "$55,000 MXN mensuales"
        db.flush()
        check(pconv.sueldo_publicado(v), "la vacante tiene sueldo publicado")
        web = ssw.criterios_web(v)
        check(not any(q.get("expectativa") for q in web), "solicitud web: sin pregunta de expectativa")
        chat = pconv.criterios_de(v)
        check(not any(c.get("dato") == "sueldo" and c.get("expectativa") for c in chat), "chat: sin pregunta de expectativa")
        check(pconv.regla_expectativa(v) is None, "sin regla de «Requiere revisión» por expectativa (no congela la ruta)")
        v.sueldo_desde = v.sueldo_hasta = None
        v.sueldo_periodicidad, v.sueldo = "a_convenir", "A convenir"
        db.flush()
        check(not pconv.sueldo_publicado(v), "«A convenir» no es sueldo publicado")
        check(any(q.get("expectativa") for q in ssw.criterios_web(v)), "sin sueldo publicado la solicitud SÍ pide la expectativa")
        db.rollback()

        # ===================================================== 2. TTS del avatar
        print("\n--- 2. Montos para el TTS del avatar ---")
        casos = {
            "El sueldo es $55,000 MXN mensuales.": "El sueldo es 55000 pesos mexicanos mensuales.",
            "$8,000 – $10,000 MXN quincenales": "8000 a 10000 pesos mexicanos quincenales",
            "Desde $12,500.00 MXN": "Desde 12500 pesos mexicanos",
            "pagamos 9,500 MXN al mes": "pagamos 9500 pesos mexicanos al mes",
            "$55,000": "55000 pesos mexicanos",
            "Tiene 3 años de experiencia": "Tiene 3 años de experiencia",
        }
        for crudo, esperado in casos.items():
            check(texto_para_voz(crudo) == esperado, f"«{crudo}» → «{texto_para_voz(crudo)}»")
        pc = persona_config("Red Human", "Sueldo: $55,000 MXN mensuales", "El sueldo es $55,000 MXN.")
        check("$" not in pc["systemPrompt"].split("\n\n")[0] and "55000 pesos mexicanos" in pc["systemPrompt"],
              "systemPrompt sin «$» y con «pesos mexicanos»")
        check(pc["initialMessage"] == "El sueldo es 55000 pesos mexicanos.", "initialMessage sanitizado")

        # ===================================================== 3. tablero de candidatos
        print("\n--- 3. Tablero: «Todas las vacantes» por defecto ---")
        pagina = (RAIZ.parent / "red-human-app" / "app" / "dashboard" / "candidatos" / "page.tsx").read_text(encoding="utf-8")
        check("x.candidatos > m.candidatos" not in pagina and "if (mayor) setFiltroVacante" not in pagina,
              "ya no se autoselecciona la vacante con más candidatos")

        # ===================================================== 4. CLABE
        print("\n--- 4. CLABE ---")
        base = {clave_documento(d["tipo"]): d["obligatorio"] for d in documentos_base_requeridos()}
        check(base.get("cuenta_bancaria") is False and base.get("ine") is True, "base: CLABE opcional, INE obligatoria")
        mezcla = onboarding.normalizar_documentos(documentos_base_requeridos() + [{"tipo": "Cuenta bancaria / CLABE", "obligatorio": True}])
        check(next(d for d in mezcla if clave_documento(d["tipo"]) == "cuenta_bancaria")["obligatorio"],
              "si la vacante la pide como obligatoria, gana el obligatorio")
        e = db.query(Expediente).join(Postulacion, Postulacion.id == Expediente.postulacion_id).first()
        check(e is not None, "expediente de la demo")
        for d in list(e.documentos):
            if clave_documento(d.tipo) == "cuenta_bancaria":
                e.documentos.remove(d)
        db.flush()
        clabe = Documento(expediente_id=e.id, tipo="Cuenta bancaria / CLABE", obligatorio=True, estado="pendiente")
        e.documentos.append(clabe)
        for d in e.documentos:
            if d is not clabe and d.obligatorio:
                d.estado, d.revisado_por = "recibido", "RH"
        db.flush()
        check(e.progreso < 100, f"antes: la CLABE heredada bloquea ({e.progreso} %)")
        r = catalogo_documentos.clabe_opcional(db, aplicar=True, forzar=True)
        check(clabe.id in r["documentos"] and clabe.obligatorio is False, "la corrección la deja opcional")
        check(e.progreso == 100 and "Cuenta bancaria / CLABE" not in e.no_aprobados, f"después: ya no bloquea ({e.progreso} %)")
        r2 = catalogo_documentos.clabe_opcional(db, aplicar=True)
        check(r2["yaAplicada"], "una sola vez (marca en bitácora)")
        db.rollback()
        db.close()
    print(f"\n✅ {OK} verificaciones OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
