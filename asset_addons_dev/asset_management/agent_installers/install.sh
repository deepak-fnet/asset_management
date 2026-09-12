#!/bin/bash
# Asset Agent - one-command installer for Ubuntu.
#
# This file is a template: Odoo fills in the server URL and database from the
# address you fetched it from, so there is nothing in here to edit by hand.
#
#   curl -fsSL __SERVER_URL__/agent/install.sh | sudo bash
#
# Re-running it is the upgrade path: apt installs the newer .deb over the old
# one and the local Odoo URL/database in remote.json is kept.
set -euo pipefail

SERVER_URL='__SERVER_URL__'
DATABASE='__DATABASE__'
DEB_URL='__DEB_URL__'

SERVICE=asset-agent
CONFIG=/etc/asset-agent/remote.json

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m    %s\033[0m\n' "$1"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$1" >&2; exit 1; }

echo
echo "Asset Agent installer"
echo "  server:   $SERVER_URL"
echo "  database: $DATABASE"
echo

[ "$(id -u)" -eq 0 ] || die "must run as root - pipe this into 'sudo bash', not 'bash'."
command -v apt-get >/dev/null || die "apt-get not found - this installer is for Debian/Ubuntu."

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
DEB="$TMP/asset-agent.deb"

step "Downloading the agent package"
echo "    $DEB_URL"
curl -fsSL --retry 3 --connect-timeout 15 -o "$DEB" "$DEB_URL" \
    || die "could not download $DEB_URL"
ok "$(dpkg-deb --field "$DEB" Package) $(dpkg-deb --field "$DEB" Version)"

step "Installing"
# --force-confold/confdef keep the existing /etc/asset-agent/remote.json on a
# reinstall: it is a dpkg conffile, so without these a re-run would stop on an
# interactive "keep or replace?" prompt with no terminal to answer it.
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    -o Dpkg::Options::=--force-confold \
    -o Dpkg::Options::=--force-confdef \
    "$DEB"

step "Writing $CONFIG"
mkdir -p "$(dirname "$CONFIG")"
cat > "$CONFIG" <<JSON
{
  "odoo": "$SERVER_URL",
  "db": "$DATABASE"
}
JSON
chmod 644 "$CONFIG"
ok "$CONFIG"

# postinst already started the service, but it did so with whatever URL was in
# the package's own remote.json - restart so it picks up the one just written.
step "Restarting $SERVICE"
systemctl restart "$SERVICE"

step "Verifying"
for _ in $(seq 1 15); do
    systemctl is-active --quiet "$SERVICE" && break
    sleep 1
done

if ! systemctl is-active --quiet "$SERVICE"; then
    echo
    journalctl -u "$SERVICE" -n 30 --no-pager || true
    die "$SERVICE failed to start."
fi
ok "$SERVICE is active"

sleep 5
echo
echo "--- recent log ---"
journalctl -u "$SERVICE" -n 15 --no-pager || true

echo
printf '\033[32mDone. The asset appears in Odoo under its serial number.\033[0m\n'
echo "  status:    systemctl status $SERVICE"
echo "  logs:      journalctl -u $SERVICE -f"
echo "  config:    $CONFIG"
echo "  uninstall: sudo apt-get remove -y asset-agent"
echo
