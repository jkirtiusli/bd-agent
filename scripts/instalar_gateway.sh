#!/usr/bin/env bash
# Instala el Agente en un gateway Debian/Ubuntu. Idempotente: se puede volver
# a correr para actualizar sin romper nada.
#
#   sudo ./scripts/instalar_gateway.sh --granja astillas_de_plata \
#        --url https://core.flowkore.com/ingest \
#        --origen /mnt/bdcopy/csv \
#        --token-file /root/token-astillas.txt
#
set -euo pipefail

DESTINO=/opt/bd-agent
ETC=/etc/bd-agent
USUARIO=bdagent
GRANJA="" ; URL="" ; ORIGEN="" ; TOKEN_FILE="" ; TZ_GRANJA="America/Argentina/Buenos_Aires"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --granja)     GRANJA="$2"; shift 2 ;;
    --url)        URL="$2"; shift 2 ;;
    --origen)     ORIGEN="$2"; shift 2 ;;
    --token-file) TOKEN_FILE="$2"; shift 2 ;;
    --tz)         TZ_GRANJA="$2"; shift 2 ;;
    -h|--help)    sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "opcion desconocida: $1" >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "Correr con sudo." >&2; exit 1; }
for req in GRANJA URL ORIGEN TOKEN_FILE; do
  [[ -n "${!req}" ]] || { echo "Falta --${req,,}" >&2; exit 2; }
done
[[ -f "$TOKEN_FILE" ]] || { echo "No existe el archivo de token: $TOKEN_FILE" >&2; exit 2; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo ">> Paquetes del sistema"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip cifs-utils ca-certificates \
                      systemd-timesyncd >/dev/null
timedatectl set-ntp true || true   # el reloj correcto no es opcional: todo es por fecha

echo ">> Usuario de servicio ($USUARIO)"
id -u "$USUARIO" &>/dev/null || useradd --system --home /var/lib/bd-agent \
                                        --shell /usr/sbin/nologin "$USUARIO"

echo ">> Codigo en $DESTINO"
mkdir -p "$DESTINO"
# --delete no: no queremos borrar el .venv existente
cp -r "$REPO/bd_agent" "$DESTINO/"
cp "$REPO/requirements.txt" "$DESTINO/"
[[ -d "$DESTINO/.venv" ]] || python3 -m venv "$DESTINO/.venv"
"$DESTINO/.venv/bin/pip" install -q --upgrade pip
"$DESTINO/.venv/bin/pip" install -q -r "$DESTINO/requirements.txt"
chown -R root:root "$DESTINO"

echo ">> Token y config en $ETC"
mkdir -p "$ETC"
install -o root -g "$USUARIO" -m 0640 "$TOKEN_FILE" "$ETC/token"
if [[ -f "$ETC/config.yaml" ]]; then
  echo "   config.yaml ya existe, no se toca"
else
  cat > "$ETC/config.yaml" <<YAML
# Config del gateway de $GRANJA. El token NO va aca: lo monta systemd.
granja: "$GRANJA"
zona_horaria: "$TZ_GRANJA"

ruta_csv: "$ORIGEN"
intervalo_segundos: 900
centinela: true

spool:
  ruta: "/var/lib/bd-agent/spool.db"

destino:
  modo: "http"
  url: "$URL"
  token_file: "\${CREDENTIALS_DIRECTORY}/core_token"
  lote: 2000
  reintentos: 5
  timeout: 60
YAML
  chown root:"$USUARIO" "$ETC/config.yaml"
  chmod 0640 "$ETC/config.yaml"
fi

echo ">> Cola en /var/lib/bd-agent"
mkdir -p /var/lib/bd-agent
chown "$USUARIO":"$USUARIO" /var/lib/bd-agent
chmod 0750 /var/lib/bd-agent

echo ">> Servicios y timers"
cp "$REPO"/scripts/systemd/bd-agent*.service "$REPO"/scripts/systemd/bd-agent*.timer \
   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now bd-agent.timer bd-agent-heartbeat.timer

echo ">> Actualizaciones de seguridad automaticas"
apt-get install -y -qq unattended-upgrades >/dev/null
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true

echo
echo "Listo. Verificar con:"
echo "  sudo -u $USUARIO $DESTINO/.venv/bin/python -m bd_agent.agente \\"
echo "       --config $ETC/config.yaml --diagnostico"
echo "  systemctl list-timers 'bd-agent*'"
echo "  journalctl -u bd-agent.service -n 50"
echo
echo "Falta (a mano, una sola vez):"
echo "  1. Montar la carpeta de CSV en $ORIGEN — SOLO LECTURA. En /etc/fstab:"
echo "     //IP-DE-LA-PC/csv $ORIGEN cifs ro,credentials=/etc/bd-agent/smb.cred,\\"
echo "     uid=$USUARIO,iocharset=utf8,vers=3.0,_netdev,nofail 0 0"
echo "  2. Instalar Tailscale:  curl -fsSL https://tailscale.com/install.sh | sh"
echo "     y luego:             tailscale up --ssh --hostname gw-$GRANJA"
