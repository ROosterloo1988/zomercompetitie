#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/zomercompetitie"
SERVICE_FILE="/etc/systemd/system/zomercompetitie.service"
NGINX_FILE="/etc/nginx/sites-available/zomercompetitie"

if [[ "${EUID}" -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

${SUDO} apt update
${SUDO} apt install -y python3 python3-venv python3-pip nginx git rsync

${SUDO} mkdir -p "$APP_DIR"
${SUDO} rsync -a --delete ./ "$APP_DIR"/
cd "$APP_DIR"

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install .

${SUDO} tee "$SERVICE_FILE" >/dev/null <<SERVICE
[Unit]
Description=Zomercompetitie FastAPI
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/.venv/bin"
ExecStart=$APP_DIR/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
SERVICE

${SUDO} tee "$NGINX_FILE" >/dev/null <<NGINX
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
NGINX

${SUDO} ln -sf "$NGINX_FILE" /etc/nginx/sites-enabled/zomercompetitie
${SUDO} rm -f /etc/nginx/sites-enabled/default
${SUDO} systemctl daemon-reload
${SUDO} systemctl enable --now zomercompetitie
${SUDO} nginx -t
${SUDO} systemctl reload nginx

echo "Installatie gereed: http://<server-ip>/"
