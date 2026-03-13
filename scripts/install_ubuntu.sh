#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/zomercompetitie"
SERVICE_FILE="/etc/systemd/system/zomercompetitie.service"
NGINX_FILE="/etc/nginx/sites-available/zomercompetitie"

sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx git

sudo mkdir -p "$APP_DIR"
sudo rsync -a --delete ./ "$APP_DIR"/
cd "$APP_DIR"

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install .

sudo tee "$SERVICE_FILE" >/dev/null <<SERVICE
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

sudo tee "$NGINX_FILE" >/dev/null <<NGINX
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
NGINX

sudo ln -sf "$NGINX_FILE" /etc/nginx/sites-enabled/zomercompetitie
sudo rm -f /etc/nginx/sites-enabled/default
sudo systemctl daemon-reload
sudo systemctl enable --now zomercompetitie
sudo nginx -t
sudo systemctl reload nginx

echo "Installatie gereed: http://<server-ip>/"
