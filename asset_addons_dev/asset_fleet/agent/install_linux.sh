#!/bin/bash
# Asset agent installer - served by Odoo with the addresses filled in:
#   curl -fsSL "__SERVER__/asset/agent/install.sh?token=__TOKEN__&db=__DB__" | sudo bash
# Installs the same agent the .deb ships (monitoring, software, storage,
# antivirus, app / OS updates, SSH terminal, screen sharing), but takes the
# server and database from /etc/asset-agent/agent.json instead of a build.
set -e
SERVER="__SERVER__"
DB="__DB__"
TOKEN="__TOKEN__"
VERSION="__VERSION__"
FILES="__FILES__"
DIR=/usr/local/bin/asset-agent
[ "$(id -u)" = "0" ] || { echo "run as root (sudo)"; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }

echo "==> Asset agent $VERSION for $SERVER (database $DB)"

# ---- clean up older installs ---------------------------------------------
if dpkg -s asset-agent >/dev/null 2>&1; then
  echo "==> removing the asset-agent .deb (replaced by this install)"
  systemctl stop asset-agent asset-terminal 2>/dev/null || true
  dpkg -r asset-agent >/dev/null 2>&1 || true
fi
for u in asset-fleet-agent asset-fleet-terminal; do
  if [ -f /etc/systemd/system/$u.service ]; then
    systemctl disable --now $u >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/$u.service
  fi
done
rm -rf /opt/asset-fleet /etc/asset-fleet /var/lib/asset-fleet

# ---- dependencies ----------------------------------------------------------
if command -v apt-get >/dev/null; then
  echo "==> packages"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-psutil python3-requests python3-websockets \
      python3-pil python3-tk wl-clipboard xclip >/dev/null || echo "!! could not install some python3 packages"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q ubuntu-release-upgrader-core >/dev/null 2>&1 || true
fi

# ---- files ----------------------------------------------------------------
echo "==> downloading the agent"
mkdir -p "$DIR" /etc/asset-agent /var/lib/asset-agent /var/lib/asset_agent
TMP=$(mktemp -d)
for f in $FILES; do
  curl -fsSL "$SERVER/asset/agent/file/$f?token=$TOKEN&db=$DB" -o "$TMP/$f"
done
for f in $TMP/*.py; do python3 -m py_compile "$f"; done
install -m 755 $TMP/*.py "$DIR/"
mkdir -p /var/lib/asset_agent/screentime
install -m 644 $TMP/asset-agent.service $TMP/asset-terminal.service /etc/systemd/system/
# GNOME extension for Screen Time (application in front - name only)
EXT=/usr/share/gnome-shell/extensions/asset-focus@futurenet.in
mkdir -p "$EXT"
install -m 644 $TMP/focus_extension.js "$EXT/extension.js"
install -m 644 $TMP/focus_metadata.json "$EXT/metadata.json"
rm -rf "$TMP" "$DIR/__pycache__"

cat > /etc/asset-agent/agent.json <<CFG
{"server": "$SERVER", "db": "$DB", "token": "$TOKEN", "version": "$VERSION"}
CFG
chmod 600 /etc/asset-agent/agent.json
cat > /etc/asset-agent/remote.json <<CFG
{"odoo": "$SERVER", "db": "$DB"}
CFG
touch /etc/asset-agent/root-terminal
chmod 750 /var/lib/asset_agent
touch /var/log/asset_agent_os_upgrade.log && chmod 640 /var/log/asset_agent_os_upgrade.log

# unattended apt / OS upgrades: no needrestart dialogs
mkdir -p /etc/needrestart/conf.d
cat > /etc/needrestart/conf.d/99-asset-agent.conf <<'NR'
$nrconf{restart} = 'a';
$nrconf{kernelhints} = -1;
$nrconf{ucodehints} = 0;
NR

# screen sharing worker: starts in every desktop session
cat > /etc/xdg/autostart/asset-remote.desktop <<DESK
[Desktop Entry]
Type=Application
Name=Asset Remote Assistance
Comment=Consent-based remote support for this managed asset
Exec=/usr/bin/python3 $DIR/asset_remote_worker.py
X-GNOME-Autostart-enabled=true
NoDisplay=true
Terminal=false
DESK

# ---- services ---------------------------------------------------------------
systemctl daemon-reload
systemctl enable asset-agent asset-terminal >/dev/null 2>&1
systemctl restart asset-agent
sleep 3
systemctl restart asset-terminal

# start the screen-sharing worker now in sessions already logged in
for RUN in /run/user/*; do
  UID_=$(basename "$RUN"); [ "$UID_" -ge 1000 ] 2>/dev/null || continue
  [ -S "$RUN/bus" ] || continue
  USER_=$(id -nu "$UID_" 2>/dev/null) || continue
  ps -u "$UID_" -o pid=,args= | awk '/asset_remote_worker.py/ && !/awk/ {print $1}' | xargs -r kill 2>/dev/null || true
  sleep 1
  sudo -u "$USER_" XDG_RUNTIME_DIR="$RUN" DBUS_SESSION_BUS_ADDRESS="unix:path=$RUN/bus" \
      systemd-run --user --collect --quiet /usr/bin/python3 "$DIR/asset_remote_worker.py" 2>/dev/null \
      && echo "==> screen sharing started for $USER_" || true
  CUR=$(sudo -u "$USER_" XDG_RUNTIME_DIR="$RUN" DBUS_SESSION_BUS_ADDRESS="unix:path=$RUN/bus" \
        gsettings get org.gnome.shell enabled-extensions 2>/dev/null || echo "@as []")
  case "$CUR" in *asset-focus@futurenet.in*) ;; *)
    NEW=$(python3 -c "import ast,sys; l=ast.literal_eval(sys.argv[1].replace('@as ','')) or []; l.append('asset-focus@futurenet.in'); print(str(l))" "$CUR")
    sudo -u "$USER_" XDG_RUNTIME_DIR="$RUN" DBUS_SESSION_BUS_ADDRESS="unix:path=$RUN/bus" \
        gsettings set org.gnome.shell enabled-extensions "$NEW" 2>/dev/null \
        && echo "==> Screen Time extension enabled for $USER_ (active from the next login)" ;;
  esac
done

echo "==> installed: asset-agent (data) and asset-terminal (remote terminal) are running"
echo "   logs: journalctl -u asset-agent -f"
