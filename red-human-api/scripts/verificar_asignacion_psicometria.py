"""Regresión de «Conectar y simplificar la asignación de psicometrías» (2026-10-07).

    .venv/Scripts/python.exe scripts/verificar_asignacion_psicometria.py

Base desechable. Psicométricas.mx se SIMULA (httpx falso, sin red): NUNCA consume el saldo compartido con producción.
Cubre: catálogo simplificado (Nombre, Tipo, Identificador, Activa), batería predeterminada en la ruta «Corporativos
con psicometría», herencia/sobreescritura en la vacante sin tocar la ruta, vista limpia, «Asignar y enviar» (alta en el
proveedor + clave + aviso por Notificaciones), doble clic sin duplicar, fallo del proveedor sin marcar nada, tres
estados visibles (Pendiente / En curso / Completada), mismo registro en Ruta y Evaluaciones, repetición y adicional con
historial propio, eventos «Psicometría enviada» y «Recordatorio de psicometría pendiente» (regla de la Cuenta) y el
recordatorio automático.
"""

import asyncio
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

_dir = tempfile.mkdtemp(prefix="rh_asig_psico_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "asig.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "PSICOMETRICAS_TOKEN",
          "PSICOMETRICAS_PASSWORD", "PSICOMETRICAS_USUARIO", "PSICOMETRICAS_WEBHOOK_SECRET", "PSICOMETRICAS_URL_CANDIDATO"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-asig-psico"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import usuario_actual, usuario_admin, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Evaluacion, EventoEvaluacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import psicometricas as psi  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
PDF = b"%PDF-1.4\n" + b"%" * 900 + b"\n%%EOF\n"


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


class R:
    def __init__(self, status, datos=None, contenido=b""):
        self.status_code, self._d, self.content = status, datos, contenido
        self.text = ""

    def json(self):
        if self._d is None:
            raise ValueError("sin json")
        return self._d


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    B = Cuenta(nombre="Psico", nombre_comercial="Psico SA", razon_social="Psico SA", estado="Activa")
    db.add(B)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=B.id))
    vacs = db.query(Vacante).filter(Vacante.estado == "Publicada").limit(2).all()
    for v in vacs:
        v.cuenta_id = B.id
        v.proceso = {}
    obtener(db).modo_prueba = False
    db.commit()
    for dep in (usuario_actual, usuario_decisor, usuario_admin):
        app.dependency_overrides[dep] = lambda: admin
    H = {"X-Cuenta-Id": str(B.id)}
    V1, V2 = vacs[0].codigo, vacs[1].codigo

    print("\n--- 1. Catálogo simplificado: Nombre, Tipo, Identificador en el proveedor, Activa ---")
    SIMPLE = {"modo": "integrada", "proveedor": "Psicométricas.mx"}  # constantes que manda el formulario (no se capturan)
    r = client.post("/evaluaciones/pruebas", headers=H, json={"nombre": "Batería Corporativa", "tipo": "bateria", "id_proveedor": "1, 7", "activa": True, **SIMPLE})
    check(r.status_code == 201 and r.json()["tipo"] == "bateria" and r.json()["tipoTexto"] == "Batería" and r.json()["clave"] == "PSI-BATERIA-CORPORATIVA",
          "alta sin identificador interno: se genera solo y el tipo queda «Batería»")
    BAT = r.json()["id"]
    r = client.post("/evaluaciones/pruebas", headers=H, json={"nombre": "Batería Corporativa", "tipo": "bateria", "id_proveedor": "1", **SIMPLE})
    check(r.status_code == 201 and r.json()["clave"] == "PSI-BATERIA-CORPORATIVA-2", "mismo nombre: el identificador interno automático no se repite")
    DUP = r.json()["id"]
    client.delete(f"/evaluaciones/pruebas/{DUP}")
    KOS = client.post("/evaluaciones/pruebas", headers=H, json={"nombre": "Kostick", "tipo": "prueba", "id_proveedor": "2", **SIMPLE}).json()["id"]
    TER = client.post("/evaluaciones/pruebas", headers=H, json={"nombre": "Terman", "tipo": "prueba", "id_proveedor": "7", **SIMPLE}).json()["id"]
    check(client.post("/evaluaciones/pruebas", headers=H, json={"nombre": "X", "tipo": "otra", **SIMPLE}).status_code == 400, "tipo inválido → 400")
    check(client.patch(f"/evaluaciones/pruebas/{KOS}", headers=H, json={"nombre": "Kostick (PAPI)"}).json()["tipo"] == "prueba",
          "editar solo el nombre conserva tipo, modo y proveedor")

    print("\n--- 2. Ruta «Corporativos con psicometría» con batería predeterminada; la vacante la hereda o la cambia ---")
    client.post("/procesos/plantillas/rutas-base", headers=H)
    pl = next(x for x in client.get("/procesos/plantillas", headers=H).json() if x["rutaBase"] == "corporativos_psicometria")
    pasos = [dict(x, pruebas=[BAT]) if x["tipo"] == "psicometrica" else x for x in pl["pasos"]]
    r = client.patch(f"/procesos/plantillas/{pl['id']}", headers=H, json={"pasos": pasos})
    check(r.status_code == 200 and next(x for x in r.json()["pasos"] if x["tipo"] == "psicometrica")["pruebas"] == [BAT],
          "la ruta guarda la batería por defecto del catálogo")
    r = client.put(f"/procesos/vacantes/{V1}", headers=H, json={"plantilla_id": pl["id"]})
    check(next(x for x in r.json()["proceso"]["pasos"] if x["tipo"] == "psicometrica")["pruebas"] == [BAT], "vacante 1: hereda la batería de la ruta")
    client.put(f"/procesos/vacantes/{V2}", headers=H, json={"plantilla_id": pl["id"]})
    pasos_v2 = [dict(x, pruebas=[KOS, TER]) if x["tipo"] == "psicometrica" else x for x in client.get(f"/procesos/vacantes/{V2}", headers=H).json()["proceso"]["pasos"]]
    r = client.put(f"/procesos/vacantes/{V2}", headers=H, json={"pasos": pasos_v2})
    check(next(x for x in r.json()["proceso"]["pasos"] if x["tipo"] == "psicometrica")["pruebas"] == [KOS, TER], "vacante 2: elige otra selección del catálogo")
    pl2 = next(x for x in client.get("/procesos/plantillas", headers=H).json() if x["id"] == pl["id"])
    check(next(x for x in pl2["pasos"] if x["tipo"] == "psicometrica")["pruebas"] == [BAT] and pl2["version"] == pl["version"] + 1,
          "…sin alterar la ruta general")

    def candidato(nombre, tel, correo, vac):
        return client.post("/candidatos", headers=H, json={"nombre": nombre, "telefono": tel, "correo": correo, "vacante": vac,
                                                             "consentimiento": True, "fuente": "RH"}).json()["id"]

    P1 = candidato("Ana Psico", "5512340001", "ana@correo.mx", V1)
    P2 = candidato("Beto Psico", "5512340002", "beto@correo.mx", V2)
    v1 = client.get(f"/evaluaciones/postulaciones/{P1}/psicometria", headers=H).json()
    check(v1["seleccion"] == [BAT] and v1["origen"] == "vacante" and v1["paso"] and v1["asignada"] is None and not v1["conectado"],
          "vista limpia del candidato 1: batería configurada (de la vacante), sin asignación previa")
    check(all(x["id"] != DUP for x in v1["catalogo"]) and {x["id"] for x in v1["catalogo"]} >= {BAT, KOS, TER},
          "«Cambiar selección»: el catálogo trae solo las pruebas/baterías activas")
    check(client.get(f"/evaluaciones/postulaciones/{P2}/psicometria", headers=H).json()["seleccion"] == [KOS, TER],
          "candidato 2: la selección propia de su vacante")
    seg = client.get(f"/procesos/postulaciones/{P1}", headers=H).json()
    paso_psi = next(x for e in seg["etapas"] for x in e["pasos"] if x["tipo"] == "psicometrica")
    check(paso_psi["pruebas"] == [BAT] and (paso_psi["accion"] is None or paso_psi["accion"]["texto"] == "Asignar y enviar"),
          "la actividad de la ruta trae su batería y su acción es «Asignar y enviar»")

    print("\n--- 3. Sin llaves del proveedor: asignación simulada y sin duplicados ---")
    r = client.post(f"/evaluaciones/postulaciones/{P2}/psicometria", headers=H, json={})
    e = r.json()["evaluacion"]
    check(r.status_code == 201 and r.json()["simulado"] and e["pasoIntegrada"] == "enviada" and not e["claveProveedor"]
          and e["nombre"] == "Kostick (PAPI) + Terman" and e["idProveedor"] == "2,7" and e["pruebas"] == [KOS, TER],
          "sin llaves: simulada, con la selección completa (una sola asignación)")
    check(client.post(f"/evaluaciones/postulaciones/{P2}/psicometria", headers=H, json={}).status_code == 409, "segundo clic → 409, no duplica")
    check(db.query(Evaluacion).filter(Evaluacion.codigo == e["codigo"]).count() == 1
          and len([x for x in client.get(f"/evaluaciones/postulaciones/{P2}", headers=H).json() if x["tipo"] == "psicometrica"]) == 1,
          "una sola evaluación psicométrica en la base")

    print("\n--- 4. Con llaves (proveedor simulado): fallo de la API no marca nada ---")
    settings.psicometricas_token, settings.psicometricas_password = "T" * 20, "P" * 20
    LLAMADAS, MENSAJES, CORREOS = [], [], []
    ESTADO = {"inicio": None, "fin": None}
    orig_post, orig_get = psi.httpx.post, psi.httpx.get
    psi.httpx.post = lambda url, data=None, timeout=None: (LLAMADAS.append(data), R(401, {"code": "1001", "msg": "Token inválido"}))[1]

    def _get(url, params=None, timeout=None):
        if url.endswith("consultaCandidato"):
            return R(200, [{"clave": params["Clave"], "id_prueba": t, "fecha_inicio": ESTADO["inicio"], "fecha_fin": ESTADO["fin"]} for t in (1, 7)])
        if params.get("Pdf") == "true":
            return R(200, None, PDF)
        return R(200, {"cleaver": {"D": 12}, "terman": {"ci": 101}})

    psi.httpx.get = _get
    import app.services.correo as _correo
    import app.services.whatsapp as _wa

    async def _correo_falso(destinatario, asunto, cuerpo_html, adjuntos=None):
        CORREOS.append((destinatario, asunto, cuerpo_html))
        return {"enviado": True, "proveedor": "resend", "detalle": "ok"}

    async def _boton_falso(telefono, texto, boton, url, cuenta_id=None):
        MENSAJES.append((telefono, texto))
        return {"enviado": True, "proveedor": "telegram", "detalle": "ok"}

    orig_correo, orig_boton = _correo.enviar_correo, _wa.enviar_con_boton
    _correo.enviar_correo, _wa.enviar_con_boton = _correo_falso, _boton_falso
    try:
        r = client.post(f"/evaluaciones/postulaciones/{P1}/psicometria", headers=H, json={"paso_id": paso_psi["id"]})
        check(r.status_code == 502 and "1001" in r.json()["detail"], "API rechaza → 502 con el motivo del proveedor (claro para RH)")
        db.expire_all()
        check(len(LLAMADAS) == 1 and not [x for x in client.get(f"/evaluaciones/postulaciones/{P1}", headers=H).json() if x["tipo"] == "psicometrica"]
              and not MENSAJES and not CORREOS, "…y NO queda registro «Asignada/Enviada» ni se avisa al candidato")
        seg = client.get(f"/procesos/postulaciones/{P1}", headers=H).json()
        check(next(x for e in seg["etapas"] for x in e["pasos"] if x["id"] == paso_psi["id"])["evaluacion"] is None, "la actividad de la ruta sigue sin asignar")

        print("\n--- 5. «Asignar y enviar»: proveedor + clave + aviso por Notificaciones ---")
        psi.httpx.post = lambda url, data=None, timeout=None: (LLAMADAS.append(data), R(200, {"status": "200", "clave": "1-ANA-0001", "msg": "ok"}))[1]
        r = client.post(f"/evaluaciones/postulaciones/{P1}/psicometria", headers=H, json={"paso_id": paso_psi["id"]})
        e1 = r.json()["evaluacion"]
        check(r.status_code == 201 and LLAMADAS[-1]["Tests"] == "1,7" and LLAMADAS[-1]["Email"] == "ana@correo.mx" and e1["claveProveedor"] == "1-ANA-0001",
              "alta en Psicométricas.mx con la batería de la ruta y clave guardada")
        check(e1["estadoProveedor"] == "pendiente" and e1["estadoProveedorTexto"] == "Pendiente" and e1["pasoId"] == paso_psi["id"],
              "estado visible «Pendiente» y ligada a la actividad de la ruta")
        env = r.json()["envioCandidato"]
        check(env["enviado"] and set(env["canales"]) == {"telegram", "correo"} and "1-ANA-0001" in MENSAJES[-1][1] and "1-ANA-0001" in CORREOS[-1][1],
              "evento «Psicometría enviada»: portal + clave por el canal activo y por correo")
        seg = client.get(f"/procesos/postulaciones/{P1}", headers=H).json()
        paso_ruta = next(x for e in seg["etapas"] for x in e["pasos"] if x["id"] == paso_psi["id"])
        check(paso_ruta["evaluacion"] == e1["codigo"] and paso_ruta["estadoUnificado"] == "programada",
              "Ruta y Evaluaciones leen el MISMO registro (Programada / Enviada en la ruta)")
        n = len(LLAMADAS)
        r = client.post(f"/evaluaciones/postulaciones/{P1}/psicometria", headers=H, json={"paso_id": paso_psi["id"]})
        check(r.status_code == 409 and len(LLAMADAS) == n, "doble clic: 409 SIN volver a llamar al proveedor (no gasta saldo ni duplica)")
        r = client.post(f"/evaluaciones/postulaciones/{P1}/psicometria", headers=H, json={})
        check(r.status_code == 409 and len(LLAMADAS) == n, "…también desde «Agregar evaluación» sin paso (mismas pruebas)")
        vista = client.get(f"/evaluaciones/postulaciones/{P1}/psicometria", headers=H).json()
        check(vista["asignada"] and vista["asignada"]["codigo"] == e1["codigo"], "la vista limpia muestra la asignación vigente")

        print("\n--- 6. Notificaciones configurables: «Recordatorio de psicometría pendiente» ---")
        reglas = {x["evento"] for x in client.get("/notificaciones/reglas", headers=H).json()}
        check({"psicometria_enviada", "recordatorio_psicometria"} <= reglas, "los dos eventos nuevos aparecen en Configuración → Notificaciones")
        client.patch("/notificaciones/reglas/recordatorio_psicometria", headers=H, json={"candidato_whatsapp": True, "candidato_correo": False})
        nm, nc = len(MENSAJES), len(CORREOS)
        r = client.post(f"/evaluaciones/{e1['codigo']}/recordatorio", headers=H, json={"a": "candidato"})
        check(r.status_code == 200 and len(MENSAJES) == nm + 1 and len(CORREOS) == nc and "te recordamos" in MENSAJES[-1][1]
              and "1-ANA-0001" in MENSAJES[-1][1] and r.json()["evaluacion"]["recordatoriosPsicometria"] == 1,
              "recordatorio manual respeta la regla (solo canal activo, sin correo) y lleva portal + clave")

        print("\n--- 7. Sincronización: solo Pendiente / En curso / Completada ---")
        ESTADO["inicio"] = "2026-10-07 09:00:00"
        r = client.post(f"/evaluaciones/{e1['codigo']}/sincronizar", headers=H)
        check(r.status_code == 200 and r.json()["evaluacion"]["estadoProveedor"] == "en_curso", "el proveedor confirma inicio → «En curso»")
        seg = client.get(f"/procesos/postulaciones/{P1}", headers=H).json()
        check(next(x for e in seg["etapas"] for x in e["pasos"] if x["id"] == paso_psi["id"])["estadoUnificado"] == "en_curso", "…y la ruta muestra «En curso»")
        check(client.post(f"/evaluaciones/{e1['codigo']}/sincronizar", headers=H).json()["evaluacion"]["estadoProveedor"] == "en_curso"
              and db.query(EventoEvaluacion).join(Evaluacion, Evaluacion.id == EventoEvaluacion.evaluacion_id)
              .filter(Evaluacion.codigo == e1["codigo"], EventoEvaluacion.detalle.contains("iniciada")).count() == 1,
              "sincronizar otra vez no repite el evento de inicio")
        ESTADO["fin"] = "2026-10-07 10:00:00"
        r = client.post(f"/evaluaciones/{e1['codigo']}/sincronizar", headers=H)
        check(r.json()["sincronizacion"] == "resultado_recibido" and r.json()["evaluacion"]["estadoProveedor"] == "completada"
              and r.json()["evaluacion"]["estadoProveedorTexto"] == "Completada", "terminó → «Completada» con el informe")
        check(client.post(f"/evaluaciones/{e1['codigo']}/recordatorio", headers=H, json={"a": "candidato"}).status_code == 409,
              "ya no se recuerda una psicometría completada")

        print("\n--- 8. Repetición y prueba adicional: cada una con su historial ---")
        psi.httpx.post = lambda url, data=None, timeout=None: (LLAMADAS.append(data), R(200, {"status": "200", "clave": f"1-ANA-{len(LLAMADAS):04d}"}))[1]
        ESTADO.update(inicio=None, fin=None)
        r = client.post(f"/evaluaciones/postulaciones/{P1}/psicometria", headers=H, json={})
        rep = r.json()["evaluacion"]
        check(r.status_code == 201 and rep["pasoId"] == paso_psi["id"] and rep["codigo"] != e1["codigo"] and rep["claveProveedor"] != e1["claveProveedor"],
              "repetir la batería: nueva asignación (clave nueva) ligada a la MISMA actividad de la ruta")
        seg = client.get(f"/procesos/postulaciones/{P1}", headers=H).json()
        check(next(x for e in seg["etapas"] for x in e["pasos"] if x["id"] == paso_psi["id"])["evaluacion"] == rep["codigo"]
              and client.get(f"/evaluaciones/{e1['codigo']}", headers=H).json()["evaluacion"]["estado"] == "con_resultado"
              and len(client.get(f"/evaluaciones/{e1['codigo']}", headers=H).json()["eventos"]) >= 4,
              "la ruta muestra la vigente; la anterior conserva su resultado e historial")
        r = client.post(f"/evaluaciones/postulaciones/{P1}/psicometria", headers=H, json={"prueba_ids": [KOS]})
        adi = r.json()["evaluacion"]
        check(r.status_code == 201 and adi["pasoId"] and adi["pasoId"] != paso_psi["id"] and adi["nombre"] == "Kostick (PAPI)",
              "otra prueba fuera de la ruta: actividad adicional (ad hoc) con su propio registro")
        check(client.post(f"/evaluaciones/postulaciones/{P1}/psicometria", headers=H, json={"prueba_ids": [999999]}).status_code == 400,
              "una prueba inexistente o inactiva → 400 claro")

        print("\n--- 9. Recordatorio automático (sin consultar la API del proveedor) ---")
        from app.services.recordatorios_psicometria import revisar_recordatorios_psicometria

        settings.psicometricas_recordatorio_dias, settings.psicometricas_recordatorios_max = 2, 2
        fila = db.query(Evaluacion).filter(Evaluacion.codigo == adi["codigo"]).one()
        db.refresh(fila)
        fila.enviada_en = datetime.now(timezone.utc) - timedelta(days=3)
        db.query(Evaluacion).filter(Evaluacion.codigo == rep["codigo"]).one().enviada_en = datetime.now(timezone.utc)
        db.commit()
        consultas = len(LLAMADAS)
        nm = len(MENSAJES)
        check(asyncio.run(revisar_recordatorios_psicometria()) == 1 and len(MENSAJES) == nm + 1 and "te recordamos" in MENSAJES[-1][1],
              "pendiente desde hace 3 días → un recordatorio (la recién enviada no)")
        check(asyncio.run(revisar_recordatorios_psicometria()) == 0, "no se repite antes de que pasen otros 2 días")
        db.expire_all()
        fila = db.query(Evaluacion).filter(Evaluacion.codigo == adi["codigo"]).one()
        fila.recordatorio_psicometria_en = datetime.now(timezone.utc) - timedelta(days=3)
        fila.iniciada_en = datetime.now(timezone.utc)
        db.commit()
        check(asyncio.run(revisar_recordatorios_psicometria()) == 0 and len(LLAMADAS) == consultas, "ya iniciada: no se le recuerda; y el job nunca llama al proveedor")
    finally:
        psi.httpx.post, psi.httpx.get = orig_post, orig_get
        _correo.enviar_correo, _wa.enviar_con_boton = orig_correo, orig_boton
    db.close()

print(f"\n🎉 Asignación simplificada de psicometrías: {OK} verificaciones OK")
