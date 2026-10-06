"""Cuentas (Punto 9) — gestión completa: listado, alta, ficha (datos, usuarios, clientes, portal).

Alcance (decisión 2026-09-11): un Administrador solo ve y administra las Cuentas a las que está
vinculado vía `usuario_cuentas`; al crear una nueva queda vinculado automáticamente. Nunca hay
vista global de todas las Cuentas del sistema — los datos de una cuenta no se mezclan con otra.
Las rutas `/cuentas/actual` se conservan por compatibilidad (misma lógica sobre `cuenta_actual`).
"""
import os
import shutil
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..seed import slug_cuenta_unico
from ..deps import cuenta_actual, usuario_actual, usuario_admin
from ..models import CANALES_MENSAJERIA, normalizar_canal_mensajeria, ROLES, Cliente, Cuenta, Usuario, UsuarioCuenta, registrar
from .auth import CORREO_RE, crear_usuario_basico

router = APIRouter(prefix="/cuentas", tags=["cuentas"])

LOGOS_DIR = Path("archivos/logos")
EXTENSIONES_LOGO = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
ESTADOS_CUENTA = ("Activa", "Inactiva")


def _cuenta_dict(cu: Cuenta, actual_id: Optional[int] = None) -> dict:
    return {
        "id": cu.id,
        "nombre": cu.nombre_visible,
        "nombreComercial": cu.nombre_comercial,
        "razonSocial": cu.razon_social,
        "logo": cu.logo,          # ruta en disco; el frontend construye la URL con urlArchivo()
        "contactoNombre": cu.contacto_nombre,
        "correoComunicacion": cu.correo_comunicacion,
        "whatsappComunicacion": cu.whatsapp_comunicacion,
        "whatsappExclusivo": bool(cu.whatsapp_exclusivo),  # 2026-09-17: número dedicado (Premium)
        # 2026-10-06: canal con candidatos (whatsapp | telegram | ambos) y si el bot de Telegram está configurado
        "canalMensajeria": normalizar_canal_mensajeria(cu.canal_mensajeria),
        "telegramDisponible": bool((settings.telegram_bot_token or "").strip()),
        "slug": cu.slug or "",
        "portalUrl": f"{settings.app_url}/portal?cuenta={cu.slug}" if cu.slug else f"{settings.app_url}/portal",  # 2026-09-17
        "estado": cu.estado,
        "esActual": cu.id == actual_id,
        "eliminadaEn": cu.eliminada_en.isoformat() if cu.eliminada_en else None,
        "usuarios": len(cu.usuarios),
        "clientes": len(cu.clientes),
    }


def _usuario_cuenta_dict(u: Usuario) -> dict:
    return {"id": u.id, "nombre": u.nombre, "correo": u.correo, "puesto": u.puesto or "", "telefono": u.telefono or "", "rol": u.rol, "activo": u.activo}


def _ficha_dict(cu: Cuenta, actual_id: Optional[int]) -> dict:
    return {
        **_cuenta_dict(cu, actual_id),
        "usuariosDetalle": [_usuario_cuenta_dict(uc.usuario) for uc in cu.usuarios],
        "clientesDetalle": [
            {"id": c.id, "nombre": c.nombre, "nombreComercial": c.nombre_comercial, "estado": c.estado, "contactos": len(c.contactos)}
            for c in cu.clientes
        ],
        "portal": {
            "logo": cu.logo,
            "nombreComercial": cu.nombre_comercial,
            "url": f"{settings.app_url}/portal",
        },
    }


def _por_id(db: Session, cuenta_id: int, admin: Usuario) -> Cuenta:
    """Solo Cuentas vinculadas al admin — una ajena responde 404, nunca 403 (no se revela)."""
    cu = (
        db.query(Cuenta)
        .join(UsuarioCuenta, UsuarioCuenta.cuenta_id == Cuenta.id)
        .filter(Cuenta.id == cuenta_id, UsuarioCuenta.usuario_id == admin.id)
        .first()
    )
    if not cu:
        raise HTTPException(404, "Cuenta no encontrada")
    return cu


# ------------------------------------------------------------
# Listado y alta
# ------------------------------------------------------------


@router.get("")
def listar(
    incluir_eliminadas: bool = False,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(usuario_admin),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Cuentas vinculadas al administrador (activas e inactivas), la actual marcada. Las eliminadas
    (baja lógica, Fase 2) no aparecen salvo `incluir_eliminadas=true`."""
    q = (
        db.query(Cuenta)
        .join(UsuarioCuenta, UsuarioCuenta.cuenta_id == Cuenta.id)
        .filter(UsuarioCuenta.usuario_id == admin.id)
    )
    if not incluir_eliminadas:
        q = q.filter(Cuenta.estado != "Eliminada")
    cuentas = q.order_by(Cuenta.id).all()
    return [{**_cuenta_dict(c, cuenta.id), "esPredeterminada": c.id == admin.cuenta_predeterminada_id} for c in cuentas]


@router.delete("/{cuenta_id}")
def eliminar_cuenta(
    cuenta_id: int, db: Session = Depends(get_db), admin: Usuario = Depends(usuario_admin),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Baja LÓGICA de una Cuenta (Fase 2, 2026-09-15): estado «Eliminada» + fecha/quién. No se borra
    nada (vacantes, postulaciones, bitácora, usuarios vinculados siguen en la base) y se puede
    restaurar. Reglas: no se elimina la Cuenta con la que se está operando, ni la última activa del
    administrador (se quedaría sin acceso). Si era la predeterminada de alguien, se le quita."""
    cu = _por_id(db, cuenta_id, admin)
    if cu.estado == "Eliminada":
        raise HTTPException(409, "Esta Cuenta ya está eliminada.")
    if cu.id == cuenta.id:
        raise HTTPException(409, "No puedes eliminar la Cuenta con la que estás operando: cambia de Cuenta primero.")
    activas = (
        db.query(Cuenta)
        .join(UsuarioCuenta, UsuarioCuenta.cuenta_id == Cuenta.id)
        .filter(UsuarioCuenta.usuario_id == admin.id, Cuenta.estado == "Activa", Cuenta.id != cu.id)
        .count()
    )
    if activas == 0:
        raise HTTPException(409, "No puedes eliminar tu única Cuenta activa.")
    cu.estado = "Eliminada"
    cu.eliminada_en = datetime.now(timezone.utc)
    cu.eliminada_por = admin.nombre
    for u in db.query(Usuario).filter(Usuario.cuenta_predeterminada_id == cu.id).all():
        u.cuenta_predeterminada_id = None
    registrar(db, admin.nombre, "cuenta_eliminada", "cuenta", str(cu.id), {"nombre": cu.nombre_visible, "correo_rh": admin.correo})
    db.commit()
    return {"ok": True, "cuenta": _cuenta_dict(cu, cuenta.id)}


@router.post("/{cuenta_id}/restaurar")
def restaurar_cuenta(
    cuenta_id: int, db: Session = Depends(get_db), admin: Usuario = Depends(usuario_admin),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Deshace la baja lógica: la Cuenta vuelve a «Activa»."""
    cu = _por_id(db, cuenta_id, admin)
    if cu.estado != "Eliminada":
        raise HTTPException(409, "Esta Cuenta no está eliminada.")
    cu.estado = "Activa"
    cu.eliminada_en = None
    cu.eliminada_por = ""
    registrar(db, admin.nombre, "cuenta_restaurada", "cuenta", str(cu.id), {"nombre": cu.nombre_visible, "correo_rh": admin.correo})
    db.commit()
    return _cuenta_dict(cu, cuenta.id)


@router.post("/{cuenta_id}/predeterminada")
def marcar_predeterminada(
    cuenta_id: int, db: Session = Depends(get_db), admin: Usuario = Depends(usuario_admin),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Cuenta con la que arranca la sesión de ESTE usuario cuando tiene varias (Fase 2). Es por
    usuario (Usuario.cuenta_predeterminada_id), no global: dos admins pueden preferir distinta."""
    cu = _por_id(db, cuenta_id, admin)
    if cu.estado != "Activa":
        raise HTTPException(409, "Solo una Cuenta activa puede ser la predeterminada.")
    admin.cuenta_predeterminada_id = cu.id
    registrar(db, admin.nombre, "cuenta_predeterminada", "cuenta", str(cu.id), {"usuario": admin.correo})
    db.commit()
    return {"ok": True, "cuentaPredeterminadaId": cu.id}


class CrearCuentaIn(BaseModel):
    nombre: str
    nombre_comercial: str = ""
    razon_social: str = ""
    contacto_nombre: str = ""
    correo_comunicacion: str = ""
    whatsapp_comunicacion: str = ""
    whatsapp_exclusivo: bool = False
    canal_mensajeria: str = "whatsapp"
    estado: str = "Activa"


@router.post("", status_code=201)
def crear(
    datos: CrearCuentaIn,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(usuario_admin),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    nombre = datos.nombre.strip()
    if not nombre:
        raise HTTPException(400, "El nombre de la cuenta es obligatorio.")
    if datos.estado not in ESTADOS_CUENTA:
        raise HTTPException(400, "Estado inválido. Usa 'Activa' o 'Inactiva'.")
    correo = datos.correo_comunicacion.strip()
    if correo and not CORREO_RE.match(correo):
        raise HTTPException(400, "El correo no tiene un formato válido.")
    nueva = Cuenta(
        nombre=nombre,
        nombre_comercial=datos.nombre_comercial.strip() or nombre,
        razon_social=datos.razon_social.strip(),
        contacto_nombre=datos.contacto_nombre.strip(),
        correo_comunicacion=correo,
        whatsapp_comunicacion=datos.whatsapp_comunicacion.strip(),
        whatsapp_exclusivo=bool(datos.whatsapp_exclusivo),
        canal_mensajeria=_canal_valido(datos.canal_mensajeria),
        estado=datos.estado,
    )
    db.add(nueva)
    db.flush()
    # Quien la crea queda vinculado: si no, la cuenta nacería sin nadie que pudiera verla.
    nueva.slug = slug_cuenta_unico(db, nueva.nombre_comercial or nueva.nombre, nueva.id)  # 2026-09-17
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=nueva.id))
    registrar(db, admin.nombre, "cuenta_creada", "cuenta", str(nueva.id), {"nombre": nombre, "correo_rh": admin.correo})
    db.commit()
    db.refresh(nueva)
    return _ficha_dict(nueva, cuenta.id)


# ------------------------------------------------------------
# Ficha por id (y alias /actual por compatibilidad con Punto 2/27)
# ------------------------------------------------------------


class ActualizarCuentaIn(BaseModel):
    nombre: Optional[str] = None
    nombre_comercial: Optional[str] = None
    razon_social: Optional[str] = None
    contacto_nombre: Optional[str] = None
    correo_comunicacion: Optional[str] = None
    whatsapp_comunicacion: Optional[str] = None
    whatsapp_exclusivo: Optional[bool] = None
    canal_mensajeria: Optional[str] = None
    estado: Optional[str] = None


def _canal_valido(valor: str) -> str:
    v = (valor or "").strip().lower()
    if v not in CANALES_MENSAJERIA:
        raise HTTPException(400, "Canal inválido. Usa 'whatsapp', 'telegram' o 'ambos'.")
    return v


def _aplicar_actualizacion(db: Session, cu: Cuenta, datos: ActualizarCuentaIn, admin: Usuario) -> dict:
    payload = datos.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(400, "No se enviaron campos a actualizar.")
    if "estado" in payload and payload["estado"] not in ESTADOS_CUENTA:
        raise HTTPException(400, "Estado inválido. Usa 'Activa' o 'Inactiva'.")
    if "nombre" in payload and not payload["nombre"].strip():
        raise HTTPException(400, "El nombre de la cuenta no puede quedar vacío.")
    if "nombre_comercial" in payload and not payload["nombre_comercial"].strip():
        raise HTTPException(400, "El nombre comercial no puede quedar vacío.")
    if payload.get("correo_comunicacion") and not CORREO_RE.match(payload["correo_comunicacion"].strip()):
        raise HTTPException(400, "El correo no tiene un formato válido.")
    if "canal_mensajeria" in payload:
        payload["canal_mensajeria"] = _canal_valido(payload["canal_mensajeria"])
    for campo, valor in payload.items():
        setattr(cu, campo, valor.strip() if isinstance(valor, str) else valor)
    registrar(db, admin.nombre, "cuenta_actualizada", "cuenta", str(cu.id), {"campos": list(payload)})
    db.commit()
    db.refresh(cu)
    return payload


async def _guardar_logo(db: Session, cu: Cuenta, archivo: UploadFile, admin: Usuario) -> None:
    """PNG, JPG, SVG o WebP. Si ya había un logo anterior en nuestra carpeta, se borra."""
    ext = Path(archivo.filename or "logo.png").suffix.lower()
    if ext not in EXTENSIONES_LOGO:
        raise HTTPException(400, f"Formato no soportado. Usa: {', '.join(sorted(EXTENSIONES_LOGO))}")
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    ruta_nueva = LOGOS_DIR / f"logo_{cu.id}_{uuid.uuid4().hex[:8]}{ext}"
    if cu.logo:
        ruta_anterior = Path(cu.logo)
        if ruta_anterior.exists() and ruta_anterior.parent == LOGOS_DIR:
            try:
                os.remove(ruta_anterior)
            except OSError:
                pass  # si no se puede borrar, continúa de todos modos
    with ruta_nueva.open("wb") as f:
        shutil.copyfileobj(archivo.file, f)
    cu.logo = str(ruta_nueva)
    registrar(db, admin.nombre, "cuenta_logo_actualizado", "cuenta", str(cu.id), {})
    db.commit()
    db.refresh(cu)


@router.get("/actual")
def obtener(db: Session = Depends(get_db), _: Usuario = Depends(usuario_admin), cuenta: Cuenta = Depends(cuenta_actual)):
    """Datos de la Cuenta activa del usuario (solo administradores)."""
    return _ficha_dict(cuenta, cuenta.id)


@router.get("/actual/razones-sociales")
def razones_sociales(db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """2026-09-20 (B2): razones sociales con las que ESTA Cuenta puede contratar — la de la Cuenta (primero,
    es la predeterminada) y las de sus Clientes activos. Es la ÚNICA lista válida para «Empresa contratante»
    en las condiciones de contratación (nunca texto libre). Cualquier sesión de la Cuenta puede leerla."""
    return razones_sociales_de(db, cuenta)


def razones_sociales_de(db: Session, cuenta: Cuenta) -> list:
    from ..models import Cliente

    salida = [{"razonSocial": (cuenta.razon_social or cuenta.nombre_visible).strip(), "origen": "cuenta", "clienteId": None, "predeterminada": True}]
    vistas = {salida[0]["razonSocial"].lower()}
    for c in db.query(Cliente).filter(Cliente.cuenta_id == cuenta.id, Cliente.estado == "Activo").order_by(Cliente.nombre).all():
        rs = (c.razon_social or "").strip()
        if not rs or rs.lower() in vistas:
            continue
        vistas.add(rs.lower())
        salida.append({"razonSocial": rs, "origen": "cliente", "clienteId": c.id, "predeterminada": False})
    return salida


@router.patch("/actual")
def actualizar(
    datos: ActualizarCuentaIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_admin), cuenta: Cuenta = Depends(cuenta_actual)
):
    _aplicar_actualizacion(db, cuenta, datos, u)
    return _ficha_dict(cuenta, cuenta.id)


@router.post("/actual/logo")
async def subir_logo(
    archivo: UploadFile = File(...), db: Session = Depends(get_db), u: Usuario = Depends(usuario_admin), cuenta: Cuenta = Depends(cuenta_actual)
):
    await _guardar_logo(db, cuenta, archivo, u)
    return _ficha_dict(cuenta, cuenta.id)


@router.get("/{cuenta_id}")
def ficha(cuenta_id: int, db: Session = Depends(get_db), admin: Usuario = Depends(usuario_admin), cuenta: Cuenta = Depends(cuenta_actual)):
    """Ficha completa: datos generales, usuarios, clientes y portal."""
    return _ficha_dict(_por_id(db, cuenta_id, admin), cuenta.id)


@router.patch("/{cuenta_id}")
def actualizar_por_id(
    cuenta_id: int, datos: ActualizarCuentaIn, db: Session = Depends(get_db), admin: Usuario = Depends(usuario_admin),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    cu = _por_id(db, cuenta_id, admin)
    _aplicar_actualizacion(db, cu, datos, admin)
    return _ficha_dict(cu, cuenta.id)


@router.post("/{cuenta_id}/logo")
async def subir_logo_por_id(
    cuenta_id: int, archivo: UploadFile = File(...), db: Session = Depends(get_db), admin: Usuario = Depends(usuario_admin),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    cu = _por_id(db, cuenta_id, admin)
    await _guardar_logo(db, cu, archivo, admin)
    return _ficha_dict(cu, cuenta.id)


# ------------------------------------------------------------
# Usuarios de una Cuenta (sección "Usuarios" de la ficha)
# ------------------------------------------------------------


class AgregarUsuarioIn(BaseModel):
    nombre: str = ""
    correo: str
    rol: str = "Usuario"
    puesto: str = ""
    telefono: str = ""  # Fase 7A: WhatsApp del perfil (entrevistador interno)
    password: Optional[str] = None  # solo si el usuario es nuevo; si falta, se genera una temporal


@router.post("/{cuenta_id}/usuarios", status_code=201)
def agregar_usuario(
    cuenta_id: int, datos: AgregarUsuarioIn, db: Session = Depends(get_db), admin: Usuario = Depends(usuario_admin),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """«+ Agregar usuario» en la ficha. Si el correo ya existe como usuario del sistema, solo se
    le da acceso a esta Cuenta (no se toca su rol ni su contraseña); si no existe, se crea con la
    misma validación que POST /auth/usuarios y queda vinculado. Regresa la contraseña temporal
    UNA sola vez cuando se generó aquí (el admin se la comparte; debe cambiarla al entrar)."""
    cu = _por_id(db, cuenta_id, admin)
    correo = datos.correo.strip().lower()
    if not CORREO_RE.match(correo):
        raise HTTPException(400, "El correo no tiene un formato válido.")
    existente = db.query(Usuario).filter(Usuario.correo == correo).first()
    password_temporal = None
    if existente:
        ya = db.query(UsuarioCuenta).filter_by(usuario_id=existente.id, cuenta_id=cu.id).first()
        if ya:
            raise HTTPException(409, "Ese usuario ya tiene acceso a esta Cuenta.")
        u = existente
        accion = "usuario_vinculado_cuenta"
    else:
        if datos.rol not in ROLES:
            raise HTTPException(400, f"Rol inválido. Usa uno de: {', '.join(ROLES)}")
        if len(datos.nombre.strip()) < 3:
            raise HTTPException(400, "El nombre debe tener al menos 3 caracteres.")
        password_temporal = datos.password or uuid.uuid4().hex[:10] + "Aa1!"
        u = crear_usuario_basico(db, correo, datos.nombre, datos.puesto, datos.rol, password_temporal, datos.telefono)
        if datos.password:
            password_temporal = None  # la eligió el admin, no se reimprime
        accion = "usuario_creado"
    db.add(UsuarioCuenta(usuario_id=u.id, cuenta_id=cu.id))
    registrar(db, admin.nombre, accion, "usuario", correo, {"cuenta": cu.id, "rol": u.rol, "correo_rh": admin.correo})
    db.commit()
    db.refresh(cu)
    return {"usuario": _usuario_cuenta_dict(u), "nuevo": existente is None, "passwordTemporal": password_temporal, "cuenta": _ficha_dict(cu, cuenta.id)}


@router.delete("/{cuenta_id}/usuarios/{usuario_id}")
def quitar_usuario(
    cuenta_id: int, usuario_id: int, db: Session = Depends(get_db), admin: Usuario = Depends(usuario_admin),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Quita el acceso de un usuario a esta Cuenta (no borra al usuario). Nunca a uno mismo ni al
    último administrador activo de la Cuenta."""
    cu = _por_id(db, cuenta_id, admin)
    if usuario_id == admin.id:
        raise HTTPException(409, "No puedes quitarte a ti mismo de la Cuenta.")
    vinculo = db.query(UsuarioCuenta).filter_by(usuario_id=usuario_id, cuenta_id=cu.id).first()
    if not vinculo:
        raise HTTPException(404, "Ese usuario no tiene acceso a esta Cuenta.")
    u = vinculo.usuario
    if u.rol == "Administrador" and u.activo:
        otros_admin = [
            uc for uc in cu.usuarios
            if uc.usuario_id != usuario_id and uc.usuario.rol == "Administrador" and uc.usuario.activo
        ]
        if not otros_admin:
            raise HTTPException(409, "Es el único administrador activo de esta Cuenta; asigna otro antes de quitarlo.")
    db.delete(vinculo)
    registrar(db, admin.nombre, "usuario_desvinculado_cuenta", "usuario", u.correo, {"cuenta": cu.id, "correo_rh": admin.correo})
    db.commit()
    db.refresh(cu)
    return _ficha_dict(cu, cuenta.id)


@router.get("/{cuenta_id}/clientes")
def clientes_de_cuenta(cuenta_id: int, db: Session = Depends(get_db), admin: Usuario = Depends(usuario_admin)):
    """Lista de solo lectura para la ficha; administrarlos requiere cambiar a esa Cuenta."""
    cu = _por_id(db, cuenta_id, admin)
    return [
        {"id": c.id, "nombre": c.nombre, "nombreComercial": c.nombre_comercial, "estado": c.estado, "contactos": len(c.contactos)}
        for c in db.query(Cliente).filter(Cliente.cuenta_id == cu.id).order_by(Cliente.nombre).all()
    ]
