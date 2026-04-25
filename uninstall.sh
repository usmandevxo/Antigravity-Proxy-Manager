#!/bin/bash

# AGPM - Antigravity Proxy Manager Uninstaller
# Author: Usman

# Color codes
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${RED}🌌 AGPM - Antigravity Proxy Manager Uninstaller${NC}"
echo -e "${RED}================================================${NC}"

# Confirm uninstallation
read -p "Are you sure you want to uninstall AGPM and stop all services? (y/N): " confirm
if [[ ! $confirm =~ ^[Yy]$ ]]; then
    echo "Uninstallation cancelled."
    exit 0
fi

echo "[*] Stopping AGPM services..."
systemctl --user stop agpm-web.service 2>/dev/null || true
systemctl --user disable agpm-web.service 2>/dev/null || true

echo "[*] Removing systemd unit files..."
rm -f ~/.config/systemd/user/agpm-web.service
systemctl --user daemon-reload

echo -e "[*] Removing global 'agpm' command..."
rm -f ~/.local/bin/agpm

# Ask about data removal
echo -e "\n${YELLOW}Cleanup Options:${NC}"
read -p "Do you want to remove the virtual environment (.venv)? (y/N): " rm_venv
read -p "Do you want to remove all configuration and databases (data directory)? (y/N): " rm_data

if [[ $rm_venv =~ ^[Yy]$ ]]; then
    echo -e "[*] Removing virtual environment..."
    rm -rf .venv
fi

if [[ $rm_data =~ ^[Yy]$ ]]; then
    echo -e "[*] Removing data directory (databases and config)..."
    rm -rf data
fi

echo -e "\n${GREEN}✅ AGPM has been uninstalled.${NC}"
echo -e "Services have been stopped and system hooks removed."
