#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/zomercompetitie"

echo "🔄 Bezig met updaten van de Zomercompetitie App..."

# 1. Sync de nieuwe bestanden, maar laat de database en virtuele omgeving met rust
sudo rsync -a --delete --exclude ".venv" --exclude "data" ./ "$APP_DIR"/

# 2. Stap de app-map in
cd "$APP_DIR"

# 3. Werk eventuele nieuwe Python packages bij in de bubbel
sudo /opt/zomercompetitie/.venv/bin/pip install .

# 4. Zet de rechten weer netjes voor de webserver
sudo chown -R www-data:www-data "$APP_DIR"

# 5. Herstart de app (zonder de beveiligingssleutels aan te raken!)
sudo systemctl restart zomercompetitie

echo "✅ Update succesvol uitgevoerd! De inlog-sessies zijn behouden gebleven."
