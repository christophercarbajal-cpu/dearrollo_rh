#!/usr/bin/env bash
# Verifica que producción esté sana. Sale con código 1 si algo está mal.
#   uso:  /opt/redhuman/verificar.sh
#
# Comprueba exactamente las cosas que un despliegue mal hecho suele romper.
set -uo pipefail

APP=/opt/redhuman/app
CONFIG=/opt/redhuman/config
DOMINIO="https://srv1893825.hstgr.cloud"
fallos=0

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
mal()  { printf "  \033[31m✗\033[0m %s\n" "$1"; fallos=$((fallos+1)); }
aviso(){ printf "  \033[33m!\033[0m %s\n" "$1"; }

echo "── Configuración ──"

# 1. La config canónica vive fuera del repo y el repo solo la enlaza
[ -f "$CONFIG/api.env" ] && ok "api.env fuera del repo" || mal "falta $CONFIG/api.env"
[ -L "$APP/red-human-api/.env" ] && ok ".env del repo es enlace a la config canónica" \
  || mal ".env del repo NO es enlace — un deploy puede pisarlo"
[ -L "$APP/red-human-app/.env.local" ] && ok ".env.local del repo es enlace" \
  || mal ".env.local del repo NO es enlace"

# 2. La base debe ser ruta ABSOLUTA: con ruta relativa la app se crea otra base
db_url=$(grep -E "^DATABASE_URL=" "$CONFIG/api.env" | cut -d= -f2-)
case "$db_url" in
  sqlite:////*) ok "DATABASE_URL absoluta ($db_url)" ;;
  *)            mal "DATABASE_URL NO es absoluta: '$db_url' — la app usaría otra base" ;;
esac

db_file=${db_url#sqlite:///}
if sudo -u redhuman test -w "$db_file"; then ok "la base es escribible por el servicio"
else mal "la base NO es escribible por redhuman — todo write fallará"; fi

sueltas=$(find "$APP" -name "*.db" 2>/dev/null | wc -l)
[ "$sueltas" -eq 0 ] && ok "sin bases sueltas dentro del código" \
  || mal "$sueltas base(s) dentro del árbol de código (señal de DATABASE_URL relativa)"

# 3. La URL de la API viaja al navegador: nunca puede ser localhost
api_url=$(grep -E "^NEXT_PUBLIC_API_URL=" "$CONFIG/web.env" | cut -d= -f2-)
case "$api_url" in
  https://*) ok "NEXT_PUBLIC_API_URL pública ($api_url)" ;;
  *)         mal "NEXT_PUBLIC_API_URL='$api_url' — el navegador del visitante no puede alcanzarla" ;;
esac
en_bundle=$(grep -rl "localhost:800" "$APP/red-human-app/.next/static/chunks" 2>/dev/null | wc -l)
[ "$en_bundle" -eq 0 ] && ok "sin localhost en el bundle del navegador" \
  || mal "$en_bundle archivo(s) del bundle apuntan a localhost"

# 4. Claves que, vacías, apagan funciones sin avisar
for k in OPENAI_API_KEY ANAM_API_KEY ANAM_AVATAR_ID META_WHATSAPP_TOKEN META_APP_SECRET; do
  v=$(grep -E "^${k}=" "$CONFIG/api.env" | cut -d= -f2-)
  [ -n "$v" ] && ok "$k con valor" || aviso "$k vacía (la función que depende de ella queda apagada)"
done

echo
echo "── Código y dependencias ──"
cd "$APP" || exit 1
rama=$(git rev-parse --abbrev-ref HEAD)
remoto=$(git remote get-url origin 2>/dev/null)
[ "$rama" = "main" ] && ok "en rama main" || mal "en rama '$rama' — producción debe seguir main"
case "$remoto" in
  *christophercarbajal-cpu/dearrollo_rh*) ok "origin apunta al repo oficial" ;;
  *)                       mal "origin apunta a '$remoto' — no es el repo oficial" ;;
esac
[ -z "$(git status --porcelain)" ] && ok "árbol limpio (sin parches a mano)" \
  || aviso "hay cambios locales sin commitear en el servidor"

"$APP/red-human-api/.venv/bin/python" -c "import pypdf" 2>/dev/null \
  && ok "pypdf instalado" || mal "pypdf falta — la lectura de CVs en PDF revienta"

echo
echo "── Servicios ──"
for s in redhuman-api redhuman-web nginx; do
  [ "$(systemctl is-active $s)" = "active" ] && ok "$s activo" || mal "$s NO activo"
done

echo
echo "── Respuesta pública ──"
salud=$(curl -s -m 20 "$DOMINIO/api/salud")
echo "$salud" | grep -q '"ok":true' && ok "API responde" || mal "API no responde en $DOMINIO/api/salud"
for campo in ia_configurada avatar_configurado whatsapp_webhook_firmado; do
  echo "$salud" | grep -q "\"$campo\":true" && ok "$campo" || aviso "$campo en false"
done

cod=$(curl -s -o /dev/null -w "%{http_code}" -m 20 "$DOMINIO/api/candidatos")
[ "$cod" = "401" ] && ok "los datos de candidatos exigen sesión" || mal "/api/candidatos responde $cod (debería ser 401)"

cod=$(curl -s -o /dev/null -w "%{http_code}" -m 20 -X POST "$DOMINIO/webhooks/whatsapp" \
      -H "Content-Type: application/json" -d '{"entry":[]}')
[ "$cod" = "403" ] && ok "el webhook rechaza POST sin firma" || mal "webhook responde $cod sin firma (debería ser 403)"

echo
if [ "$fallos" -eq 0 ]; then
  printf "\033[32m✓ Producción sana\033[0m\n"; exit 0
else
  printf "\033[31m✗ %s comprobación(es) fallida(s)\033[0m\n" "$fallos"; exit 1
fi
