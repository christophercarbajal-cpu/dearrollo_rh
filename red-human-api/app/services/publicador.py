"""Cambio 36 (2026-10-14) — motor de publicación MULTICANAL de vacantes.

Canales (`CANALES`, en orden):
* INTERNOS (siempre conectados): Portal Red Human, WhatsApp y Google Empleos (la página /aplicar trae el JobPosting).
  «Publicar» los deja «Publicada» con su liga.
* EXTERNOS (OCC, LinkedIn, Indeed, Computrabajo): «conectados» solo si el servidor tiene su conector
  (`PUBLICADOR_<CANAL>_URL` + `PUBLICADOR_<CANAL>_TOKEN`): se le manda la vacante (POST JSON con `accion` = publicar |
  actualizar | pausar | reanudar | cerrar) y su respuesta decide «Publicada» (con la liga que regrese) o «Error: motivo».
* MODO DEMO (Cuenta demo o Modo Prueba activo): todos los canales aparecen conectados y todo se SIMULA — «Publicada» con
  su liga, sin ninguna llamada externa.

Toda liga de postulación lleva `?canal=<canal>` (la postulación registra de dónde llegó). El estado por canal vive en
`Vacante.distribucion` = {canal: {estado, liga, motivo, en, simulado}}; `Vacante.plataformas` conserva los nombres de los
canales donde quedó publicada (compatibilidad con el portal y los tableros). Ningún error de un canal frena a los demás.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.orm.attributes import flag_modified

from ..config import settings

CANALES: Dict[str, dict] = {
    "portal": {"nombre": "Portal", "interno": True},
    "whatsapp": {"nombre": "WhatsApp", "interno": True},
    "google": {"nombre": "Google Empleos", "interno": True},
    "occ": {"nombre": "OCC Mundial", "interno": False},
    "linkedin": {"nombre": "LinkedIn", "interno": False},
    "indeed": {"nombre": "Indeed", "interno": False},
    "computrabajo": {"nombre": "Computrabajo", "interno": False},
}
# nombres que usaba `Vacante.plataformas` antes del Cambio 36
_LEGADO = {"Portal": "portal", "WhatsApp": "whatsapp", "Google Empleos": "google", "OCC": "occ", "LinkedIn": "linkedin",
           "Indeed": "indeed", "Computrabajo": "computrabajo"}
ESTADOS = {"publicada": "Publicada", "error": "Error", "pausada": "Pausada", "cerrada": "Cerrada", "pendiente": "Sin publicar"}


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def clave_canal(valor: str) -> Optional[str]:
    v = (valor or "").strip()
    if v in CANALES:
        return v
    if v in _LEGADO:
        return _LEGADO[v]
    return next((k for k, c in CANALES.items() if c["nombre"].lower() == v.lower()), None)


def modo_demo(db, v) -> bool:
    """Demo = Cuenta demo o Modo Prueba activo: se simula todo, sin requests externos."""
    from ..models import es_cuenta_demo
    from .configuracion import modo_prueba_activo

    try:
        return es_cuenta_demo(getattr(v, "cuenta", None)) or (db is not None and modo_prueba_activo(db))
    except Exception:  # noqa: BLE001
        return False


def _conector(canal: str) -> tuple:
    return (getattr(settings, f"publicador_{canal}_url", "") or "").strip(), (getattr(settings, f"publicador_{canal}_token", "") or "").strip()


def conectado(canal: str, demo: bool) -> bool:
    c = CANALES.get(canal)
    if c is None:
        return False
    return c["interno"] or demo or bool(_conector(canal)[0])


def conectados(db, v=None, cuenta=None) -> List[dict]:
    """Panel «Publicar en»: los canales conectados para esta vacante/Cuenta (marcados por defecto)."""
    from types import SimpleNamespace

    demo = modo_demo(db, v if v is not None else SimpleNamespace(cuenta=cuenta))
    dist = dict(getattr(v, "distribucion", None) or {}) if v is not None else {}
    return [{"canal": k, "nombre": c["nombre"], "interno": c["interno"], "simulado": demo and not c["interno"],
             **_vista(dist.get(k))} for k, c in CANALES.items() if conectado(k, demo)]


def liga(v, canal: str) -> str:
    """Liga de postulación del canal: `/aplicar/<slug>?canal=<canal>`."""
    from ..models import slugificar

    return f"{settings.app_url}/aplicar/{v.slug or slugificar(v.titulo)}?canal={canal}"


def _vista(e: Optional[dict]) -> dict:
    e = e or {}
    estado = e.get("estado") or "pendiente"
    return {"estado": estado, "estadoTexto": ESTADOS.get(estado, estado) + (f": {e['motivo']}" if estado == "error" and e.get("motivo") else ""),
            "liga": e.get("liga") or "", "motivo": e.get("motivo") or "", "en": e.get("en")}


def _cuerpo(v, accion: str, canal: str) -> dict:
    return {"accion": accion, "canal": canal, "vacante": {
        "codigo": v.codigo, "titulo": v.titulo, "descripcion": v.descripcion or "", "ubicacion": v.ubicacion or "",
        "modalidad": v.modalidad or "", "sueldo": v.sueldo or "", "requisitos": v.requisitos or "",
        "publicacion": (v.publicaciones or {}).get(canal) or (v.publicaciones or {}).get("portal") or {},
        "liga": liga(v, canal)}}


async def _externo(v, canal: str, accion: str) -> dict:
    url, token = _conector(canal)
    if not url:
        return {"estado": "error", "motivo": f"{CANALES[canal]['nombre']} no está conectado (falta PUBLICADOR_{canal.upper()}_URL)"}
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.post(url, json=_cuerpo(v, accion, canal), headers={"Authorization": f"Bearer {token}"} if token else {})
        if r.status_code >= 400:
            try:
                detalle = r.json().get("error") or r.json().get("detail") or r.text
            except Exception:  # noqa: BLE001
                detalle = r.text
            return {"estado": "error", "motivo": f"{CANALES[canal]['nombre']} respondió {r.status_code}: {str(detalle)[:200]}"}
        datos = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        return {"estado": "ok", "liga": str(datos.get("url") or datos.get("liga") or "")}
    except Exception as ex:  # noqa: BLE001 — un canal caído nunca frena a los demás
        return {"estado": "error", "motivo": f"No se pudo conectar con {CANALES[canal]['nombre']}: {type(ex).__name__}"}


async def _aplicar(db, v, canal: str, accion: str, demo: bool) -> dict:
    """Ejecuta `accion` en un canal y regresa su nuevo estado."""
    destino = {"publicar": "publicada", "actualizar": "publicada", "reanudar": "publicada", "pausar": "pausada",
               "cerrar": "cerrada"}[accion]
    previo = dict((v.distribucion or {}).get(canal) or {})
    base = {"estado": destino, "liga": previo.get("liga") or liga(v, canal), "motivo": "", "en": _ahora()}
    if CANALES[canal]["interno"] or demo:
        if not CANALES[canal]["interno"]:
            base["simulado"] = True
        base["liga"] = liga(v, canal)
        return base
    r = await _externo(v, canal, accion)
    if r["estado"] == "error":
        return {**previo, "estado": "error", "motivo": r["motivo"], "en": _ahora(), "liga": previo.get("liga") or ""}
    return {**base, "liga": r.get("liga") or base["liga"]}


def _guardar(v, dist: dict) -> None:
    v.distribucion = dist
    v.plataformas = [CANALES[k]["nombre"] for k, e in dist.items() if k in CANALES and e.get("estado") == "publicada"]
    for campo in ("distribucion", "plataformas"):
        try:
            flag_modified(v, campo)
        except Exception:  # noqa: BLE001
            pass


async def publicar(db, v, canales: List[str]) -> dict:
    """«Publicar»: distribuye a TODOS los canales marcados (los no conectados se reportan como error). Regresa la
    distribución completa."""
    demo = modo_demo(db, v)
    dist = dict(v.distribucion or {})
    for valor in canales:
        canal = clave_canal(valor)
        if canal is None:
            continue
        if not conectado(canal, demo):
            dist[canal] = {"estado": "error", "motivo": f"{CANALES[canal]['nombre']} no está conectado", "en": _ahora(), "liga": ""}
            continue
        dist[canal] = await _aplicar(db, v, canal, "publicar", demo)
    _guardar(v, dist)
    return vista(v)


async def a_todos(db, v, accion: str) -> dict:
    """Editar (actualizar), Pausar, Reanudar o Cerrar en cada canal donde la vacante está (o estuvo) publicada."""
    demo = modo_demo(db, v)
    dist = dict(v.distribucion or {})
    activos = {"actualizar": ("publicada",), "pausar": ("publicada",), "reanudar": ("pausada",),
               "cerrar": ("publicada", "pausada")}[accion]
    for canal, e in list(dist.items()):
        if canal in CANALES and e.get("estado") in activos:
            dist[canal] = await _aplicar(db, v, canal, accion, demo)
    _guardar(v, dist)
    return vista(v)


def vista(v) -> dict:
    dist = dict(getattr(v, "distribucion", None) or {})
    return {k: {"canal": k, "nombre": CANALES[k]["nombre"], **_vista(e), **({"simulado": True} if e.get("simulado") else {})}
            for k, e in dist.items() if k in CANALES}
