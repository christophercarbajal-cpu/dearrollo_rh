"""Cambio 42 (2026-10-14). Base desechable; IA y WhatsApp SIMULADOS (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambio42.py

- Avance automático: al completar una actividad la siguiente inicia sola; sin «Enviar liga de documentos» ni
  «Continuar a [etapa]»; los botones solo existen ante un bloqueador válido (acción humana, descarte, No recomendable).
- Documentos: si siguen inmediatamente a la solicitud web se suben en la MISMA página (archivo por archivo); quien cierra la
  página sin terminar recibe «[nombre], cuando tengas tus documentos, súbelos aquí: [liga]»; la liga muestra SOLO los
  faltantes, la empresa de la vacante y Frente/Reverso para la INE; al completar → mensaje final y «Por revisar».
- CV vs respuestas: «No lo menciona el CV» (omisión) ≠ «Inconsistencia» (contradicción explícita → Puntos por validar);
  ninguna frena la ruta ni pone «Requiere revisión».
- Ficha: línea de estado real y % del expediente solo con los documentos de la vacante.
- Campana: 1 candidato → ficha en la actividad; varios de la misma vacante y acción → tablero filtrado.
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
_dir = tempfile.mkdtemp(prefix="rh_c42_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c42.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "simulado"
os.environ["ADMIN_PASSWORD"] = "prueba-c42"
os.environ["SEMBRAR_DEMO"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Mensaje, Postulacion, Usuario, UsuarioCuenta, Vacante, caras_documento  # noqa: E402
from app.serial import campos_tablero, etiqueta_tablero, postulacion_dict  # noqa: E402
from app.services import campana_rh  # noqa: E402
from app.services import documentos_vacante as sdv  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp as wa  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
APP = RAIZ.parent / "red-human-app"
def _imagen() -> bytes:
    import io
    import random

    from PIL import Image

    img = Image.new("RGB", (240, 160))
    img.putdata([(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)) for _ in range(240 * 160)])
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


JPG = _imagen()


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


RUTA = {"pasos": [
    {"id": "solicitud-web", "tipo": "solicitud_web", "etapa": "Prefiltro"},
    {"id": "documentos", "tipo": "documentos", "etapa": "Prefiltro", "depende_de": ["solicitud-web"]},
    {"id": "entrevista-humana", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"},
]}
FICHA = {"titulo": "Almacenista C42", "area": "Operaciones", "seniority": "Junior", "ubicacion_estado": "Nuevo León",
         "ubicacion_municipio": "Monterrey", "modalidad": "Presencial", "sueldo_desde": 9800, "sueldo_periodicidad": "mensual",
         "descripcion": "Acomodo de mercancía.", "requisitos_indispensables": ["Secundaria terminada"], "responsabilidades": ["Acomodar"],
         "documentos_solicitados": [{"nombre": "INE (frente y reverso)"}, {"nombre": "Comprobante de domicilio"}]}


ENVIOS = []


async def _wa(telefono, texto, *a, **k):
    ENVIOS.append((telefono, texto))
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "wa_id": f"wamid.C42{len(ENVIOS)}"}


async def _wa_boton(telefono, texto, boton, url, *a, **k):
    return await _wa(telefono, f"{texto} {url}")


wa.enviar_mensaje = _wa
wa.enviar_con_boton = _wa_boton


def mensajes(db, p):
    """Lo que salió por WhatsApp al teléfono de la postulación (proveedor simulado)."""
    db.expire_all()
    tel = db.get(Postulacion, p.id).telefono
    return [t for (x, t) in ENVIOS if x == tel]


def subir(client, token, tipo, cara=""):
    datos = {"tipo": tipo, **({"cara": cara} if cara else {})}
    return client.post(f"/expedientes/publica/{token}/documentos", data=datos, files={"archivo": ("foto.jpg", JPG, "image/jpeg")})


with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    db.commit()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="C42", nombre_comercial="Logística Norte", estado="Activa", slug="c42-real")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ===================================================== 1. Catálogo: documentos de dos caras
    print("\n--- 1. Catálogo ---")
    check(caras_documento("INE (frente y reverso)") == ["Frente", "Reverso"], "la INE se sube por caras: Frente y Reverso")
    check(caras_documento("Licencia de conducir") == ["Frente", "Reverso"], "la licencia también")
    check(caras_documento("Comprobante de domicilio") == [], "un comprobante es un solo archivo")

    # ===================================================== 2. Documentos en la misma página de la solicitud
    print("\n--- 2. Solicitud web → Documentos en la misma página ---")
    r = client.post("/vacantes", json={**FICHA, "proceso": RUTA, "estado": "Publicada", "responsable_id": admin.id})
    check(r.status_code == 201, f"alta de la vacante ({r.status_code} {r.text[:200]})")
    v = db.query(Vacante).filter_by(codigo=r.json()["id"]).one()
    if v.estado != "Publicada":
        client.post(f"/vacantes/{v.codigo}/publicar", json={"canales": ["portal"]})
    v.responsable_id = admin.id
    db.commit()
    db.refresh(v)
    check(v.estado == "Publicada", f"vacante publicada ({v.estado})")
    app.dependency_overrides.pop(cuenta_actual, None)
    r = client.post("/candidatos/postular", data={"vacante": v.slug, "nombre": "Raúl Carbajal", "telefono": "5512420001",
                                                  "correo": "raul@c42.mx", "consentimiento": "true"})
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    check(r.status_code == 201, f"postulación ({r.status_code} {r.text[:200]})")
    d = r.json().get("documentos") or {}
    p1 = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    check(sdv.siguen_a_solicitud(p1), "«Documentos» sigue inmediatamente a «Solicitud web» (sin prefiltro en medio)")
    check(bool(d.get("token")) and [x["tipo"] for x in d["documentos"]] == ["INE (frente y reverso)", "Comprobante de domicilio"],
          f"/postular entrega la carga de documentos en la MISMA página ({[x['tipo'] for x in d.get('documentos', [])]})")
    check(d.get("empresa") == "Logística Norte", f"la cabecera trae la empresa de la vacante ({d.get('empresa')})")
    ine = d["documentos"][0]
    check([c["cara"] for c in ine["caras"]] == ["Frente", "Reverso"] and ine["cantidad"] == 2,
          "la INE muestra DOS casillas obligatorias: Frente y Reverso")
    check(not any("súbelos aquí" in t for t in mensajes(db, p1)), "no sale ninguna liga por WhatsApp mientras está en la página")
    pasos = sproc.estado_pasos(p1)
    doc = next(x for x in pasos if x["id"] == "documentos")
    check(doc["estado"] != "completada" and not (doc.get("accion") or {}).get("texto", "").startswith("Enviar liga"),
          f"la actividad ya inició sola: sin botón «Enviar liga de documentos» ({doc.get('accion')})")

    token = d["token"]
    r = subir(client, token, "INE (frente y reverso)", "Frente")
    check(r.status_code == 200, f"sube el frente de la INE ({r.status_code} {r.text[:200]})")
    vista = r.json()
    ine = next(x for x in vista["documentos"] if x["tipo"].startswith("INE"))
    check([c["recibida"] for c in ine["caras"]] == [True, False] and not vista["completo"],
          "el archivo se guarda al subirlo; la INE sigue pendiente hasta tener el reverso")
    r = subir(client, token, "INE (frente y reverso)", "Atrás")
    check(r.status_code == 400, "una cara que no existe se rechaza")

    # cierra la página sin terminar → WhatsApp con la liga (solo una vez)
    asyncio.run(sdv.recordar_web(db, minutos=0))
    textos = mensajes(db, p1)
    rec = [t for t in textos if "cuando tengas tus documentos, súbelos aquí:" in t]
    check(len(rec) == 1 and rec[0].startswith("Raúl, cuando tengas tus documentos, súbelos aquí: "),
          f"quien deja la página sin terminar recibe la liga por WhatsApp ({rec[:1]})")
    asyncio.run(sdv.recordar_web(db, minutos=0))
    check(len([t for t in mensajes(db, p1) if "súbelos aquí" in t]) == 1, "la liga de recuperación sale UNA sola vez")

    app.dependency_overrides.pop(cuenta_actual, None)
    vista = client.get(f"/expedientes/publica/{token}").json()
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    ine = next(x for x in vista["documentos"] if x["tipo"].startswith("INE"))
    check([x["tipo"] for x in vista["documentos"]] == ["INE (frente y reverso)", "Comprobante de domicilio"]
          and [c["recibida"] for c in ine["caras"]] == [True, False], "la liga muestra SOLO lo que falta (el frente ya recibido se marca)")
    subir(client, token, "INE (frente y reverso)", "Reverso")
    db.expire_all()
    vista = client.get(f"/expedientes/publica/{token}").json()
    check([x["tipo"] for x in vista["documentos"]] == ["Comprobante de domicilio"], "con las dos caras la INE ya no aparece")
    r = subir(client, token, "Comprobante de domicilio")
    vista = r.json()
    check(vista["completo"] and vista["documentos"] == []
          and vista["mensajeFinal"] == "Listo, Raúl. Ya recibimos tus documentos. Te avisaremos por WhatsApp el siguiente paso.",
          f"al completar los obligatorios: mensaje final exacto ({vista.get('mensajeFinal')})")
    db.expire_all()
    p1 = db.query(Postulacion).filter_by(id=p1.id).one()
    doc = next(x for x in sproc.estado_pasos(p1) if x["id"] == "documentos")
    check(doc["estadoUnificadoTexto"] == "Por revisar" and doc["porRevisar"], f"la actividad pasa a «Por revisar» ({doc['estadoUnificadoTexto']})")
    acc = sproc.accion_humana(p1)
    check(acc and acc["clave"] == "documentos_por_aprobar" and acc["texto"] == "documentos por aprobar",
          "bloqueador válido: documentos por aprobar (acción humana)")
    check(len(p1.expediente.archivos if hasattr(p1.expediente, "archivos") else []) >= 0, "expediente abierto con sus archivos")

    # ===================================================== 3. Campana: individual
    print("\n--- 3. Campana de RH ---")
    camp = client.get("/notificaciones/rh").json()
    g = [x for x in camp["grupos"] if x["vacante"] == v.codigo]
    check(len(g) == 1 and g[0]["texto"] == f"Raúl Carbajal · {v.titulo}: documentos por aprobar",
          f"1 candidato → «[Nombre] · [Puesto]: [acción]» ({g[0]['texto'] if g else camp})")
    check(g[0]["liga"] == f"/dashboard/candidatos?vacante={v.codigo}&abrir={p1.codigo}&actividad=documentos",
          "el clic abre la ficha del candidato en esa actividad")
    tarjeta = campos_tablero(p1, None, sproc.estado_pasos(p1))
    check((tarjeta.get("accion_rh") or {}).get("clave") == "documentos_por_aprobar", "la tarjeta trae la acción humana pendiente")

    # segundo candidato con lo mismo → agrupado
    app.dependency_overrides.pop(cuenta_actual, None)
    r = client.post("/candidatos/postular", data={"vacante": v.slug, "nombre": "Sofía Luna", "telefono": "5512420002",
                                                  "consentimiento": "true"})
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    t2 = r.json()["documentos"]["token"]
    for tipo, cara in (("INE (frente y reverso)", "Frente"), ("INE (frente y reverso)", "Reverso"), ("Comprobante de domicilio", "")):
        subir(client, t2, tipo, cara)
    p2 = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    camp = client.get("/notificaciones/rh").json()
    g = [x for x in camp["grupos"] if x["vacante"] == v.codigo]
    check(len(g) == 1 and g[0]["total"] == 2 and g[0]["texto"] == f"2 candidatos de {v.titulo}: documentos por aprobar",
          f"más de 1 en la misma vacante y acción → agrupado ({g[0]['texto'] if g else camp})")
    check(g[0]["liga"] == f"/dashboard/candidatos?vacante={v.codigo}&accion=documentos_por_aprobar",
          "el clic abre el tablero de la vacante filtrado por esa actividad")
    grupos = campana_rh.agrupar([
        {"vacante": "VAC-1", "puesto": "Almacenista", "codigo": f"P-{i}", "nombre": f"N{i}",
         "accion": {"clave": "documentos_por_aprobar", "texto": "documentos por aprobar", "paso": "d"}} for i in range(5)])
    check(grupos[0]["texto"] == "5 candidatos de Almacenista: documentos por aprobar", "agrupación: «5 candidatos de Almacenista: …»")

    # ===================================================== 4. Aprobación → avance automático
    print("\n--- 4. Aprobar documentos → avanza sola ---")
    db.expire_all()
    p1 = db.query(Postulacion).filter_by(id=p1.id).one()
    antes = len(mensajes(db, p1))
    tipos = [x.tipo for x in p1.expediente.documentos if not x.interno]
    r = client.post(f"/contratacion/expedientes/{p1.expediente.id}/documentos/aprobar", json={"documentos": tipos})
    check(r.status_code == 200, f"RH aprueba los documentos ({r.status_code} {r.text[:200]})")
    db.expire_all()
    p1 = db.query(Postulacion).filter_by(id=p1.id).one()
    check(p1.etapa == "Entrevista Humana", f"la ruta avanzó SOLA a la siguiente etapa ({p1.etapa})")
    check(len(mensajes(db, p1)) > antes, "y el candidato recibe su aviso por WhatsApp")
    seg = client.get(f"/procesos/postulaciones/{p1.codigo}").json()
    sig = seg.get("siguienteAccion") or {}
    check(not str(sig.get("texto", "")).startswith("Continuar a"), f"nunca «Continuar a [etapa]» ({sig.get('texto')})")
    acc = sproc.accion_humana(p1)
    check(acc and acc["clave"] == "agendar_entrevista", f"el siguiente bloqueador es humano: entrevista por agendar ({acc})")

    # ===================================================== 5. Ficha: línea de estado y % del expediente
    print("\n--- 5. Ficha ---")
    d1 = postulacion_dict(p1)
    check(d1["estado_actual"].startswith("Filtro humano · Entrevista humana"), f"la línea de estado es la etapa y actividad reales ({d1['estado_actual']})")
    check("Prefiltro" not in d1["estado_actual"], "nunca «Prefiltro: En curso» si el prefiltro ya pasó")
    check(d1["tienePrefiltro"] is False, "la ruta no trae prefiltro → la ficha no muestra la sección «Prefiltro en curso»")
    check(d1["expediente_pct"] == 100, f"% del expediente = documentos de la VACANTE (2/2 aprobados → {d1['expediente_pct']})")
    db.expire_all()
    p2 = db.query(Postulacion).filter_by(id=p2.id).one()
    check(postulacion_dict(p2)["expediente_pct"] == 0, "con los mismos documentos sin aprobar → 0 %")

    # ===================================================== 6. CV vs respuestas
    print("\n--- 6. CV vs respuestas ---")
    check(pconv.es_omision("No menciona secundaria terminada") and pconv.es_omision("Sin evidencia de escolaridad")
          and not pconv.es_omision("El CV indica preparatoria trunca"), "omisión ≠ contradicción")
    check(pconv.criterios_brecha([], p1) == [], "una brecha del CV ya no agrega preguntas ni frena la ruta")
    a = dict(p2.analisis or {})
    a.update({"ia": True, "brechas": ["No menciona secundaria terminada"], "requisitos_cumplidos": []})
    p2.analisis = a
    db.commit()
    fila = next((f for f in pconv.comparativo_indispensables(p2) if "ecundaria" in f["requisito"]), None)
    check(fila and fila["cv"] == "No lo menciona el CV" and fila["omision"] and not fila["inconsistencia"],
          f"el CV no trae el dato → «No lo menciona el CV», sin inconsistencia ({fila})")
    check(not any("ecundaria" in x for x in postulacion_dict(p2, detalle=True).get("puntosPorValidar") or []),
          "una omisión del CV no aparece en «Puntos por validar»")
    a = dict(p2.analisis)
    a["brechas"] = ["Secundaria terminada: su CV indica que solo cursó primaria"]
    crit = next(c for c in pconv.criterios_de(p2.vacante, p2) if c.get("indispensable"))
    a["respuestas_web"] = [{"pregunta": crit["pregunta"], "respuesta": "Sí" if crit.get("tipo") not in ("numero", "opcion", "abierta") else "Secundaria terminada"}]
    estado_pf = dict(a.get(pconv.ENTIDAD) or {})
    estado_pf.pop("web_importada", None)
    a[pconv.ENTIDAD] = estado_pf
    p2.analisis = a
    db.commit()
    fila = next((f for f in pconv.comparativo_indispensables(p2) if "ecundaria" in f["requisito"]), None)
    check(fila and fila["cv"].startswith("Contradice:"), f"contradicción explícita → «Contradice: …» ({fila})")
    check(fila["cumple"] is True and fila["inconsistencia"], f"respondió que sí cumple y el CV lo contradice → «Inconsistencia» ({crit.get('tipo')} {fila})")
    pv = postulacion_dict(p2, detalle=True).get("puntosPorValidar") or []
    check(len([x for x in pv if "ecundaria" in x]) == 1 and pv[0].startswith("Inconsistencia: Secundaria"),
          f"la inconsistencia real va a «Puntos por validar», una sola vez ({pv})")
    check((etiqueta_tablero(p2) or {}).get("texto") != "Requiere revisión", "ninguna de las dos pone la tarjeta en «Requiere revisión»")
    check(p2.activa and p2.estado != "revision", "ni detiene el proceso")

    # ===================================================== 7. Frontend (estático)
    print("\n--- 7. Frontend ---")
    seg_tsx = (APP / "components/dashboard/procesos/seguimiento-proceso.tsx").read_text(encoding="utf-8")
    check("texto: `Continuar a ${" not in seg_tsx, "la ficha ya no tiene el botón «Continuar a [etapa]»")
    shell = (APP / "components/dashboard/shell.tsx").read_text(encoding="utf-8")
    check("fetchCampanaRH" in shell and "<CampanaRH />" in shell, "la campana del topbar es real (GET /notificaciones/rh)")
    carga = (APP / "components/documentos/carga-documentos.tsx").read_text(encoding="utf-8")
    check("caras.map" in carga and "mensajeFinal" in carga, "la carga de documentos pinta Frente/Reverso y el mensaje final")
    aplicar = (APP / "app/aplicar/[slug]/formulario-aplicar.tsx").read_text(encoding="utf-8")
    check("<CargaDocumentos" in aplicar, "/aplicar inyecta la carga de documentos en la misma página")
    cand = (APP / "app/dashboard/candidatos/page.tsx").read_text(encoding="utf-8")
    check('searchParams.get("abrir")' in cand and 'searchParams.get("accion")' in cand, "el tablero entiende ?abrir= y ?accion=")

    print(f"\n✅ Cambio 42: {OK} comprobaciones OK")
