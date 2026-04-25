#!/bin/bash

# AGPM - Antigravity Proxy Manager Installer
# Author: Usman

set -e

# Color codes
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${CYAN}AGPM - Antigravity Proxy Manager Installer${NC}"
echo -e "${CYAN}==============================================${NC}"

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: python3 is not installed. Please install it first.${NC}"
    exit 1
fi

# Get absolute path of the project directory
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_DIR"

# Create venv
if [ ! -d ".venv" ]; then
    echo -e "[*] Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate venv and install
echo -e "[*] Installing package and dependencies..."
source .venv/bin/activate
pip install --upgrade pip
pip install -e .

# Configuration Wizard
echo -e "\n${YELLOW}Configuration Wizard${NC}"
echo -e "--------------------"

read -p "Enter Portal Port [5000]: " PORTAL_PORT
PORTAL_PORT=${PORTAL_PORT:-5000}

read -p "Enter Admin URL Slug [admin]: " ADMIN_SLUG
ADMIN_SLUG=${ADMIN_SLUG:-admin}

read -p "Enter Public URL (for OAuth) [http://localhost:$PORTAL_PORT]: " PUBLIC_URL
PUBLIC_URL=${PUBLIC_URL:-http://localhost:$PORTAL_PORT}

read -p "Enter Admin Username [admin]: " ADMIN_USER
ADMIN_USER=${ADMIN_USER:-admin}

read -s -p "Enter Admin Password [admin]: " ADMIN_PASS
echo ""
ADMIN_PASS=${ADMIN_PASS:-admin}

# Standard Google OAuth credentials (hardcoded for consistency)
CLIENT_ID="1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENT_SECRET="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"

# Create data directory
mkdir -p data

# Generate/Update config.json
echo -e "[*] Updating configuration..."
python3 <<EOF
import json, os
path = 'data/config.json'
cfg = {}
if os.path.exists(path):
    try:
        with open(path, 'r') as f: cfg = json.load(f)
    except: pass

if 'portal' not in cfg: cfg['portal'] = {}
cfg['portal'].update({
    'port': $PORTAL_PORT,
    'admin_slug': "$ADMIN_SLUG",
    'public_url': "$PUBLIC_URL"
})

if 'auth' not in cfg: cfg['auth'] = {}
cfg['auth'].update({
    'username': "$ADMIN_USER",
    'password': "$ADMIN_PASS"
})

if 'oauth' not in cfg: cfg['oauth'] = {}
cfg['oauth'].update({
    'client_id': "$CLIENT_ID",
    'client_secret': "$CLIENT_SECRET"
})

with open(path, 'w') as f:
    json.dump(cfg, f, indent=2)
EOF
chmod 600 data/config.json

# Register CLI command globally in ~/.local/bin
echo -e "[*] Registering 'agpm' command..."
mkdir -p ~/.local/bin
cat > ~/.local/bin/agpm <<EOF
#!/bin/bash
"$PROJECT_DIR/.venv/bin/agpm" "\$@"
EOF
chmod +x ~/.local/bin/agpm

# Create systemd user services
echo -e "[*] Configuring systemd user services..."
mkdir -p ~/.config/systemd/user/

# Web Portal Service
cat > ~/.config/systemd/user/agpm-web.service <<EOF
[Unit]
Description=AGPM Unified Server
After=network.target

[Service]
Type=simple
ExecStart=/bin/bash -c "cd '$PROJECT_DIR' && exec ./.venv/bin/agpm-web"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

echo -e "[*] Reloading systemd daemon..."
systemctl --user daemon-reload

echo -e "[*] Enabling and starting service..."
systemctl --user enable agpm-web.service
systemctl --user restart agpm-web.service

echo -e "\n${GREEN}✅ AGPM Installation Complete!${NC}"
echo -e "${CYAN}------------------------------${NC}"
echo -e "Dashboard URL: ${YELLOW}http://localhost:$PORTAL_PORT/$ADMIN_SLUG${NC}"
if [ "$PUBLIC_URL" != "http://localhost:$PORTAL_PORT" ]; then
    echo -e "Public URL:    ${YELLOW}$PUBLIC_URL/$ADMIN_SLUG${NC}"
fi
echo -e "CLI Command:   ${YELLOW}agpm${NC}"
echo -e "${CYAN}------------------------------${NC}"
echo -e "If 'agpm' command is not found, ensure ~/.local/bin is in your PATH."
echo -e "Add this to your .bashrc: ${YELLOW}export PATH=\$PATH:\$HOME/.local/bin${NC}"
echo -e "Then run: ${YELLOW}source ~/.bashrc${NC}"
echo -e "\nEnjoy using AGPM! 🚀"
