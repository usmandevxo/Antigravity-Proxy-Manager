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

read -p "Enter Google OAuth Client ID (Optional): " CLIENT_ID
read -p "Enter Google OAuth Client Secret (Optional): " CLIENT_SECRET

# Create data directory
mkdir -p data

# Generate config.json
echo -e "[*] Generating configuration..."
cat > data/config.json <<EOF
{
  "portal": {
    "port": $PORTAL_PORT,
    "admin_slug": "$ADMIN_SLUG",
    "public_url": "$PUBLIC_URL"
  },
  "auth": {
    "username": "$ADMIN_USER",
    "password": "$ADMIN_PASS"
  },
  "oauth": {
    "client_id": "${CLIENT_ID:-your_google_client_id_here}",
    "client_secret": "${CLIENT_SECRET:-your_google_client_secret_here}"
  }
}
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
echo -e "Unified Dashboard & API: ${YELLOW}http://localhost:5000${NC}"
echo -e "CLI Command: ${YELLOW}agpm${NC}"
echo -e "${CYAN}------------------------------${NC}"
echo -e "If 'agpm' command is not found, ensure ~/.local/bin is in your PATH."
echo -e "Add this to your .bashrc: ${YELLOW}export PATH=\$PATH:\$HOME/.local/bin${NC}"
echo -e "Then run: ${YELLOW}source ~/.bashrc${NC}"
echo -e "\nEnjoy using AGPM! 🚀"
