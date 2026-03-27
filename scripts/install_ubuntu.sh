#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/zomercompetitie"
DATA_DIR="/var/lib/zomercompetitie"
SERVICE_FILE="/etc/systemd/system/zomercompetitie.service"
NGINX_FILE="/etc/nginx/sites-available/zomercompetitie"
DOMAIN="${DOMAIN:-}"
TLS_EMAIL="${TLS_EMAIL:-}"
ENABLE_TLS="${ENABLE_TLS:-0}"
TLS_MODE="${TLS_MODE:-http}"
DNS_PROVIDER="${DNS_PROVIDER:-}"
DNS_CREDENTIALS_FILE="${DNS_CREDENTIALS_FILE:-}"

if [[ "${EUID}" -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

${SUDO} apt-get update
${SUDO} apt-get install -y python3 python3-venv python3-pip nginx git rsync certbot python3-certbot-nginx

${SUDO} mkdir -p "$APP_DIR" "$DATA_DIR"
${SUDO} rsync -a --delete --exclude ".venv" --exclude "data" ./ "$APP_DIR"/
cd "$APP_DIR"

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install .

${SUDO} tee "$SERVICE_FILE" >/dev/null <<SERVICE
[Unit]
Description=Zomercompetitie FastAPI
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/.venv/bin"
Environment="ZOMERCOMP_DB_PATH=$DATA_DIR/zomercompetitie.db"
ExecStart=$APP_DIR/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
SERVICE

if [[ -z "$DOMAIN" ]]; then
  SERVER_NAME="_"
else
  SERVER_NAME="$DOMAIN"
fi

${SUDO} tee "$NGINX_FILE" >/dev/null <<NGINX
server {
    listen 80;
    server_name $SERVER_NAME;

    client_max_body_size 16m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
NGINX

${SUDO} ln -sf "$NGINX_FILE" /etc/nginx/sites-enabled/zomercompetitie
${SUDO} rm -f /etc/nginx/sites-enabled/default
${SUDO} chown -R www-data:www-data "$APP_DIR" "$DATA_DIR"
${SUDO} chmod -R u+rwX "$APP_DIR" "$DATA_DIR"
${SUDO} systemctl daemon-reload
${SUDO} systemctl enable --now zomercompetitie
${SUDO} nginx -t
${SUDO} systemctl reload nginx

if [[ "$ENABLE_TLS" == "1" ]]; then
  if [[ -z "$DOMAIN" || -z "$TLS_EMAIL" ]]; then
    echo "ERROR: Voor TLS zijn DOMAIN en TLS_EMAIL verplicht."
    exit 1
  fi

  if [[ "$TLS_MODE" == "http" ]]; then
    ${SUDO} certbot --nginx --agree-tos --email "$TLS_EMAIL" --redirect -d "$DOMAIN" --non-interactive
  elif [[ "$TLS_MODE" == "dns" ]]; then
    if [[ -z "$DNS_PROVIDER" ]]; then
      echo "ERROR: Voor DNS mode is DNS_PROVIDER verplicht."
      exit 1
    fi

    case "$DNS_PROVIDER" in
      cloudflare)
        if [[ -z "$DNS_CREDENTIALS_FILE" ]]; then
          echo "ERROR: DNS_CREDENTIALS_FILE ontbreekt voor Cloudflare."
          exit 1
        fi
        ${SUDO} apt-get install -y python3-certbot-dns-cloudflare
        DNS_PLUGIN_ARGS=(--dns-cloudflare --dns-cloudflare-credentials "$DNS_CREDENTIALS_FILE")
        ;;
      digitalocean)
        if [[ -z "$DNS_CREDENTIALS_FILE" ]]; then
          echo "ERROR: DNS_CREDENTIALS_FILE ontbreekt voor DigitalOcean."
          exit 1
        fi
        ${SUDO} apt-get install -y python3-certbot-dns-digitalocean
        DNS_PLUGIN_ARGS=(--dns-digitalocean --dns-digitalocean-credentials "$DNS_CREDENTIALS_FILE")
        ;;
      route53)
        ${SUDO} apt-get install -y python3-certbot-dns-route53
        DNS_PLUGIN_ARGS=(--dns-route53)
        ;;
      *)
        echo "ERROR: DNS_PROVIDER '$DNS_PROVIDER' niet ondersteund. Gebruik: cloudflare, digitalocean of route53."
        exit 1
        ;;
    esac

    ${SUDO} certbot certonly \
      --agree-tos \
      --email "$TLS_EMAIL" \
      --non-interactive \
      "${DNS_PLUGIN_ARGS[@]}" \
      -d "$DOMAIN"

    ${SUDO} tee "$NGINX_FILE" >/dev/null <<NGINX_TLS
server {
    listen 80;
    server_name $DOMAIN;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name $DOMAIN;

    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    client_max_body_size 16m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
NGINX_TLS
    ${SUDO} nginx -t
    ${SUDO} systemctl reload nginx
  else
    echo "ERROR: TLS_MODE moet 'http' of 'dns' zijn."
    exit 1
  fi

  ${SUDO} systemctl enable --now certbot.timer
  echo "Installatie gereed: https://$DOMAIN/"
else
  echo "Installatie gereed: http://<server-ip>/"
fi
