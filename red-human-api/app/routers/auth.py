"""Login, sesión y administración de usuarios de RH."""
import re
from typing import List, Optional
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from ..config import settings
from ..database import get_db
from ..deps import cuenta_actual, usuario_actual, usuario_admin
from ..models import es_cuenta_demo, ROLES, Cuenta, Usuario, UsuarioCuenta, psicometria_simple, registrar
from ..services import auth, masivo
from ..services.whatsapp import clave_telefono

router = APIRouter(prefix="/auth", tags=["auth"])

CORREO_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

def usuario_dict(u: Usuario) -> dict:
   return {
       "id": u.id,
       "correo": u.correo,
       "nombre": u.nombre,
       "puesto": u.puesto or "",
       "telefono": u.telefono or "",  # Fase 7A: WhatsApp del entrevistador interno
       "rol": u.rol,
       "activo": u.activo,
       "debeCambiarPass": u.debe_cambiar_pass,
       "puedeDecidir": u.puede_decidir(),
       # Evaluaciones (2026-09-28): ver informes médicos completos (el Administrador siempre)
       "accesoInformesMedicos": bool(u.acceso_informes_medicos),
       "puedeVerInformeMedico": u.puede_ver_informe_medico(),
       # Proceso configurable (2026-10-06): autorizar la omisión de pasos obligatorios (el Administrador siempre)
       "autorizaOmisiones": bool(u.autoriza_omisiones),
       "puedeAutorizarOmisiones": u.puede_autorizar_omisiones(),
       "ultimoAcceso": u.ultimo_acceso.isoformat() if u.ultimo_acceso else None,
       # Punto 27: lista de Cuentas activas del usuario para el selector multi-cuenta del
       # frontend. Cuando solo hay una, el selector no aparece (regla de negocio Fase A).
       "cuentas": [
           {"id": uc.cuenta.id, "nombre": uc.cuenta.nombre_visible, "nombreComercial": uc.cuenta.nombre_comercial,
            # 2026-10-07: el frontend decide con el slug qué flujo de psicometría pinta (CUENTAS_PSICOMETRIA_SIMPLE)
            "slug": uc.cuenta.slug or "", "psicometriaSimple": psicometria_simple(uc.cuenta),
            # especificación 2026-10-10: «Simular respuesta» (chat de la ficha) solo existe en Cuentas demo
            "demo": es_cuenta_demo(uc.cuenta)}
           for uc in u.cuentas
           if uc.cuenta.estado == "Activa"
       ],
       # Fase 2: con varias Cuentas, con esta arranca la sesión (null = la primera).
       "cuentaPredeterminadaId": u.cuenta_predeterminada_id,
   }


def crear_usuario_basico(db: Session, correo: str, nombre: str, puesto: str, rol: str, password: str, telefono: str = "") -> Usuario:
   """Alta de un Usuario con las validaciones de siempre (correo, rol, unicidad, fortaleza) y
   `debe_cambiar_pass=True` (la contraseña la eligió el admin, no la persona). NO lo vincula a
   ninguna Cuenta ni hace commit: el llamador decide (POST /auth/usuarios → cuenta actual;
   POST /cuentas/{id}/usuarios → esa cuenta). Punto 9."""
   correo = str(correo).strip().lower()
   if not CORREO_RE.match(correo):
       raise HTTPException(400, "El correo no tiene un formato válido.")
   if rol not in ROLES:
       raise HTTPException(400, f"Rol inválido. Usa uno de: {', '.join(ROLES)}")
   if db.query(Usuario).filter(Usuario.correo == correo).first():
       raise HTTPException(409, "Ya existe un usuario con ese correo.")
   motivo = auth.validar_fortaleza(password)
   if motivo:
       raise HTTPException(400, motivo)
   u = Usuario(
       correo=correo,
       nombre=nombre.strip(),
       puesto=(puesto or "").strip(),
       telefono=clave_telefono(telefono or ""),
       rol=rol,
       hash_pass=auth.hashear(password),
       debe_cambiar_pass=True,
   )
   db.add(u)
   db.flush()
   return u

def _poner_cookie(resp: Response, token: str) -> None:
   # `secure` solo en producción: en dev el front corre en http://localhost
   seguro = settings.app_url.startswith("https://")
   resp.set_cookie(
       auth.COOKIE,
       token,
       max_age=int(auth.DURACION_SESION.total_seconds()),
       httponly=True,  # inaccesible a JavaScript → un XSS no se lleva la sesión
       secure=seguro,
       samesite="lax",
       path="/",
   )

# ------------------------------------------------------------
# Sesión
# ------------------------------------------------------------

class LoginIn(BaseModel):
   correo: str
   password: str

@router.post("/login")
def login(datos: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
   generico = "Correo o contraseña incorrectos."  # mismo mensaje siempre: no revela qué correos existen
   u = db.query(Usuario).filter(Usuario.correo == datos.correo.strip().lower()).first()
   if not u or not u.activo:
       raise HTTPException(401, generico)
   if u.bloqueado:
       raise HTTPException(429, f"Demasiados intentos fallidos. Vuelve a intentar en {auth.minutos_restantes(u)} min.")
   if not auth.verificar(datos.password, u.hash_pass):
       auth.registrar_fallo(db, u)
       registrar(db, u.correo, "login_fallido", "usuario", u.correo, {"ip": request.client.host if request.client else ""})
       db.commit()
       if u.bloqueado:
           raise HTTPException(429, f"Demasiados intentos fallidos. Cuenta bloqueada {auth.minutos_restantes(u)} min.")
       raise HTTPException(401, generico)
   auth.registrar_exito(db, u)
   auth.purgar_expiradas(db)
   token, _ = auth.crear_sesion(
       db, u, ip=request.client.host if request.client else "", agente=request.headers.get("user-agent", "")
   )
   registrar(db, u.nombre, "login", "usuario", u.correo, {"ip": request.client.host if request.client else ""})
   db.commit()
   _poner_cookie(response, token)
   return {"ok": True, "usuario": usuario_dict(u)}

@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
   token = request.cookies.get(auth.COOKIE)
   u = auth.sesion_valida(db, token)
   if auth.cerrar_sesion(db, token) and u:
       registrar(db, u.nombre, "logout", "usuario", u.correo, {})
   db.commit()
   response.delete_cookie(auth.COOKIE, path="/")
   return {"ok": True}

@router.get("/yo")
def yo(u: Usuario = Depends(usuario_actual)):
   """Quién soy — lo usa el frontend para pintar el shell y decidir permisos."""
   return usuario_dict(u)

@router.get("/entrevistadores")
def entrevistadores(db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
   """Usuarios activos de la Cuenta actual para el selector «Entrevistador» (Fase 7A): cualquier
   persona con sesión la puede pedir. Regresa nombre, correo y WhatsApp del perfil (Configuración →
   Usuarios) para que RH vea a dónde se notificará — nunca se vuelven a capturar en la entrevista."""
   filas = (
       db.query(Usuario.id, Usuario.nombre, Usuario.correo, Usuario.telefono)
       .join(UsuarioCuenta, UsuarioCuenta.usuario_id == Usuario.id)
       .filter(Usuario.activo.is_(True), UsuarioCuenta.cuenta_id == cuenta.id)
       .order_by(Usuario.nombre)
       .all()
   )
   return [{"id": id_, "nombre": nombre, "correo": correo, "telefono": telefono or ""} for (id_, nombre, correo, telefono) in filas]

class CambiarPassIn(BaseModel):
   actual: str
   nueva: str

@router.post("/cambiar-password")
def cambiar_password(datos: CambiarPassIn, request: Request, response: Response, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
   if not auth.verificar(datos.actual, u.hash_pass):
       raise HTTPException(401, "Tu contraseña actual no es correcta.")
   motivo = auth.validar_fortaleza(datos.nueva)
   if motivo:
       raise HTTPException(400, motivo)
   u.hash_pass = auth.hashear(datos.nueva)
   u.debe_cambiar_pass = False
   auth.cerrar_todas(db, u.id)  # cambiar contraseña cierra sesión en todos los dispositivos
   token, _ = auth.crear_sesion(db, u, ip=request.client.host if request.client else "")
   registrar(db, u.nombre, "password_cambiada", "usuario", u.correo, {})
   db.commit()
   _poner_cookie(response, token)
   return {"ok": True}

# ------------------------------------------------------------
# Administración de usuarios
# ------------------------------------------------------------

@router.get("/usuarios")
def listar(db: Session = Depends(get_db), _: Usuario = Depends(usuario_admin), cuenta: Cuenta = Depends(cuenta_actual)) -> List[dict]:
   filas = (
       db.query(Usuario)
       .join(UsuarioCuenta, UsuarioCuenta.usuario_id == Usuario.id)
       .filter(UsuarioCuenta.cuenta_id == cuenta.id)
       .order_by(Usuario.id)
       .all()
   )
   return [usuario_dict(x) for x in filas]

class CrearUsuarioIn(BaseModel):
   correo: str
   nombre: str = Field(min_length=3)
   puesto: str = ""
   telefono: str = ""  # Fase 7A: WhatsApp (10 dígitos) — lo usa la notificación al entrevistador interno
   rol: str = "Usuario"
   password: str
@router.post("/usuarios", status_code=201)
def crear(
   datos: CrearUsuarioIn, db: Session = Depends(get_db), admin: Usuario = Depends(usuario_admin),
   cuenta: Cuenta = Depends(cuenta_actual),
):
   u = crear_usuario_basico(db, datos.correo, datos.nombre, datos.puesto, datos.rol, datos.password, datos.telefono)
   correo = u.correo
   # el usuario nuevo queda con acceso a la Cuenta desde la que lo creó el admin — sin esto,
   # cuenta_actual le daría 403 en su primer login por no tener ninguna Cuenta asignada.
   db.add(UsuarioCuenta(usuario_id=u.id, cuenta_id=cuenta.id))
   registrar(db, admin.nombre, "usuario_creado", "usuario", correo, {"rol": u.rol})
   db.commit()
   return usuario_dict(u)

@router.post("/usuarios/masivo", status_code=201)
async def crear_masivo(
   archivo: UploadFile = File(...), db: Session = Depends(get_db), admin: Usuario = Depends(usuario_admin),
   cuenta: Cuenta = Depends(cuenta_actual),
):
   """Fase 2 (2026-09-15) — alta masiva de usuarios desde CSV/Excel. Columnas: correo, nombre,
   puesto, telefono, rol, password (opcional: si falta se genera una temporal y se regresa en la
   fila). Cada fila se valida con las MISMAS reglas que POST /usuarios; las que fallan se reportan
   con su número de fila y las demás sí se crean (savepoint por fila). Quedan vinculadas a la
   Cuenta actual."""
   filas = await masivo.leer_tabla(archivo)
   resultado = masivo.Resultado()
   for numero, fila in filas:
       correo = fila.get("correo", "")
       nombre = fila.get("nombre", "")
       if not correo and not nombre:
           continue  # renglón vacío
       password = fila.get("password") or fila.get("contrasena") or ""
       generada = ""
       if not password:
           password = generada = auth.password_temporal()
       rol = fila.get("rol") or "Usuario"
       rol = "Administrador" if rol.strip().lower().startswith("admin") else "Usuario"
       sp = db.begin_nested()
       try:
           if len(nombre.strip()) < 3:
               raise HTTPException(400, "El nombre debe tener al menos 3 caracteres.")
           u = crear_usuario_basico(db, correo, nombre, fila.get("puesto", ""), rol, password, fila.get("telefono", ""))
           db.add(UsuarioCuenta(usuario_id=u.id, cuenta_id=cuenta.id))
           db.flush()
           sp.commit()
           resultado.ok(numero, {"id": u.id, "correo": u.correo, "nombre": u.nombre, "rol": u.rol, "passwordTemporal": generada or None})
       except HTTPException as ex:
           sp.rollback()
           resultado.error(numero, str(ex.detail), correo or nombre)
   registrar(db, admin.nombre, "usuarios_carga_masiva", "cuenta", str(cuenta.id), {"archivo": archivo.filename, **resultado.resumen()})
   db.commit()
   return resultado.dict()


@router.get("/usuarios/masivo/plantilla")
def plantilla_masivo_usuarios(_: Usuario = Depends(usuario_admin)):
   """CSV de ejemplo con las columnas que acepta la carga masiva de usuarios."""
   return masivo.csv_plantilla(
       "usuarios",
       ["correo", "nombre", "puesto", "telefono", "rol", "password"],
       [["ana@empresa.mx", "Ana López", "Reclutadora", "5512345678", "Usuario", ""]],
   )


class ActualizarUsuarioIn(BaseModel):
   nombre: Optional[str] = None
   puesto: Optional[str] = None
   telefono: Optional[str] = None  # Fase 7A
   rol: Optional[str] = None
   activo: Optional[bool] = None
   password: Optional[str] = None
   acceso_informes_medicos: Optional[bool] = None  # solo lo cambia un Administrador (este endpoint ya lo exige)
   autoriza_omisiones: Optional[bool] = None  # proceso configurable: omitir pasos obligatorios con justificación

@router.patch("/usuarios/{usuario_id}")
def actualizar(
   usuario_id: int, datos: ActualizarUsuarioIn, db: Session = Depends(get_db), admin: Usuario = Depends(usuario_admin),
   cuenta: Cuenta = Depends(cuenta_actual),
):
   u = (
       db.query(Usuario)
       .join(UsuarioCuenta, UsuarioCuenta.usuario_id == Usuario.id)
       .filter(Usuario.id == usuario_id, UsuarioCuenta.cuenta_id == cuenta.id)
       .first()
   )
   if not u:
       raise HTTPException(404, "Usuario no encontrado")
   if datos.rol is not None and datos.rol not in ROLES:
       raise HTTPException(400, f"Rol inválido. Usa uno de: {', '.join(ROLES)}")
   # no dejar la Cuenta sin quien administre
   quita_admin = (datos.rol is not None and datos.rol != "Administrador") or datos.activo is False
   if u.rol == "Administrador" and quita_admin:
       otros = (
           db.query(Usuario)
           .join(UsuarioCuenta, UsuarioCuenta.usuario_id == Usuario.id)
           .filter(
               Usuario.rol == "Administrador", Usuario.activo.is_(True), Usuario.id != u.id,
               UsuarioCuenta.cuenta_id == cuenta.id,
           )
           .count()
       )
       if otros == 0:
           raise HTTPException(409, "Es el único administrador activo de esta Cuenta: nombra otro antes de cambiarlo o desactivarlo.")
   cambios = []
   for campo in ("nombre", "puesto", "rol", "activo"):
       valor = getattr(datos, campo)
       if valor is not None:
           setattr(u, campo, valor)
           cambios.append(campo)
   if datos.telefono is not None:
       u.telefono = clave_telefono(datos.telefono)
       cambios.append("telefono")
   if datos.acceso_informes_medicos is not None:
       u.acceso_informes_medicos = bool(datos.acceso_informes_medicos)
       cambios.append("acceso_informes_medicos")
   if datos.autoriza_omisiones is not None:
       u.autoriza_omisiones = bool(datos.autoriza_omisiones)
       cambios.append("autoriza_omisiones")
   if datos.password:
       motivo = auth.validar_fortaleza(datos.password)
       if motivo:
           raise HTTPException(400, motivo)
       u.hash_pass = auth.hashear(datos.password)
       u.debe_cambiar_pass = True
       auth.cerrar_todas(db, u.id)
       cambios.append("password")
   if datos.activo is False:
       auth.cerrar_todas(db, u.id)  # la baja surte efecto de inmediato
   registrar(db, admin.nombre, "usuario_actualizado", "usuario", u.correo, {"campos": cambios})
   db.commit()
   return usuario_dict(u)