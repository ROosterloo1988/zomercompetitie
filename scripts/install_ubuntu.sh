#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/zomercompetitie"
DATA_DIR="/var/lib/zomercompetitie"
BACKUP_DIR="/var/lib/zomercompetitie/backups"
SERVICE_FILE="/etc/systemd/system/zomercompetitie.service"
NGINX_FILE="/etc/nginx/sites-available/zomercompetitie"
DOMAIN="${DOMAIN:-}"
TLS_EMAIL="${TLS_EMAIL:-}"
ENABLE_TLS="${ENABLE_TLS:-0}"
TLS_MODE="${TLS_MODE:-http}"
DNS_PROVIDER="${DNS_PROVIDER:-}"
DNS_CREDENTIALS_FILE="${DNS_CREDENTIALS_FILE:-}"
ENABLE_ONTWIKKELTOOLS="${ENABLE_ONTWIKKELTOOLS:-true}"
ENABLE_UPDATE_CHECK="${ENABLE_UPDATE_CHECK:-true}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-}"

if [[ "${EUID}" -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

${SUDO} apt-get update
# Zorg dat alle benodigde pakketten (inclusief sqlite3 en cron) geïnstalleerd zijn
${SUDO} apt-get install -y python3 python3-venv python3-pip nginx git rsync certbot python3-certbot-nginx sqlite3 cron

${SUDO} mkdir -p "$APP_DIR" "$DATA_DIR" "$BACKUP_DIR"
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
Environment="ENABLE_ONTWIKKELTOOLS=$ENABLE_ONTWIKKELTOOLS"
Environment="ENABLE_UPDATE_CHECK=$ENABLE_UPDATE_CHECK"
Environment="GITHUB_REPOSITORY=$GITHUB_REPOSITORY"
ExecStart=$APP_DIR/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
SERVICE

# --- AUTOMATISCHE DAGELIJKSE BACK-UP INSTELLEN ---
echo "Configureer automatische database back-ups (dagelijks, bewaartijd 14 dagen)..."
${SUDO} tee /etc/cron.daily/zomercompetitie-backup >/dev/null <<EOF
#!/usr/bin/env bash
# Automatische backup voor de Zomercompetitie database
# Gegenereerd door install_ubuntu.sh

DB_PATH="$DATA_DIR/zomercompetitie.db"
B_DIR="$BACKUP_DIR"
DATE=\$(date +%Y-%m-%d)
BACKUP_FILE="\$B_DIR/zomercompetitie_\$DATE.db"

if [ -f "\$DB_PATH" ]; then
    sqlite3 "\$DB_PATH" ".backup '\$BACKUP_FILE'"
    chown www-data:www-data "\$BACKUP_FILE"
    find "\$B_DIR" -type f -name "zomercompetitie_*.db" -mtime +14 -delete
fi
EOF
${SUDO} chmod +x /etc/cron.daily/zomercompetitie-backup
# --------------------------------------------------

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

  # Controleer of we DHPARAM al hebben, anders maken we hem aan
  if [ ! -f /etc/nginx/dhparam.pem ]; then
      echo "Genereren van sterke Diffie-Hellman parameters voor A+ beveiliging (dit duurt ca. 1 minuut)..."
      ${SUDO} openssl dhparam -out /etc/nginx/dhparam.pem 2048
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
    
    # --- SSL Optimalisatie ---
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384;
    ssl_dhparam /etc/nginx/dhparam.pem;
    
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;
    
    ssl_stapling on;
    ssl_stapling_verify on;
    resolver 8.8.8.8 8.8.4.4 valid=300s;
    resolver_timeout 5s;

    server_tokens off;

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

        # --- A+ Security Headers (Nu in het location blok) ---
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
        add_header X-Frame-Options "SAMEORIGIN" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Cross-Origin-Resource-Policy "same-origin" always;
        add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;
        add_header Content-Security-Policy "default-src 'self' 'unsafe-inline'; frame-src 'self' https://tv.dartconnect.com; img-src 'self' data: https:;" always;
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
