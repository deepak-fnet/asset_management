import time
import requests
import socket
import platform
import psutil
import subprocess
import json
import os
import tempfile
import threading
import logging
import sys
import re
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs


def _scoped(cmd):
    """Run apt/dpkg in its own systemd scope, outside asset-agent.service's
    cgroup: the service has MemoryMax/MemoryHigh so the agent cannot take the
    desktop down, but a package install inside that cgroup (Chrome unpacking
    hundreds of MB) then throttles the agent itself into a stall."""
    if (hasattr(os, "geteuid") and os.geteuid() == 0 and os.path.isdir("/run/systemd/system")
            and shutil.which("systemd-run")):
        return ["systemd-run", "--scope", "--quiet", "--collect", "--"] + list(cmd)
    return cmd

# ============================================================================
# VERSION & UPDATE CONFIGURATION
# ============================================================================
AGENT_VERSION = "2"  # 2: OS release reporting + distro release upgrades

# ============================================================================
# Server base URL — single source of truth for the extensions
# ============================================================================
# Set by build.sh. Every other URL constant in this file is derived from the
# same host, so they cannot drift apart.
SERVER_URL = "http://10.4.10.39:8069"

# Installed from the Odoo link (/asset/agent/install.sh) instead of a .deb with
# the address baked in: the server and database come from this file.
AGENT_CONFIG_FILE = "/etc/asset-agent/agent.json"
try:
    with open(AGENT_CONFIG_FILE, encoding="utf-8") as _fh:
        _AGENT_CFG = json.load(_fh)
except Exception:  # noqa: BLE001 - no file = values baked in at build time
    _AGENT_CFG = {}
SERVER_URL = (_AGENT_CFG.get("server") or SERVER_URL).rstrip("/")

# ---------------------------------------------------------------------------
# Agent extensions: application updates, telemetry, listening ports.
# Imported defensively — if the module is missing the agent still runs, it
# just loses those three features.
# ---------------------------------------------------------------------------
try:
    from agent_extensions import start_all_extensions, report_os_updates
    EXTENSIONS_AVAILABLE = True
except ImportError as _ext_err:
    EXTENSIONS_AVAILABLE = False
    _EXTENSIONS_IMPORT_ERROR = str(_ext_err)

# ---------------------------------------------------------------------------
# OS release reporting + distro release upgrades (e.g. Ubuntu 24.04 -> 26.04).
# Same defensive import: a missing module costs the OS-upgrade feature only,
# not the whole agent.
# ---------------------------------------------------------------------------
try:
    import os_release as os_release_mod
    OS_RELEASE_AVAILABLE = True
except ImportError as _osrel_err:
    OS_RELEASE_AVAILABLE = False
    _OS_RELEASE_IMPORT_ERROR = str(_osrel_err)

# ---------------------------------------------------------------------------
# Security audit: login/logout, account/password/sudo changes. Same
# defensive import - reads /etc/shadow etc., which only this root daemon
# can do (not asset_remote_worker.py, which runs as the desktop user).
# ---------------------------------------------------------------------------
try:
    import security_audit as security_audit_mod
    SECURITY_AUDIT_AVAILABLE = True
except ImportError as _secaudit_err:
    SECURITY_AUDIT_AVAILABLE = False
    _SECURITY_AUDIT_IMPORT_ERROR = str(_secaudit_err)

UPDATE_CHECK_URL = f"{SERVER_URL}/api/agent/version"
UPDATE_DOWNLOAD_URL = f"{SERVER_URL}/downloads/AssetAgent_ubuntu_latest.tar.gz"
UPDATE_CHECK_INTERVAL = 21600  # Check every 6 hours

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================
log_dir = '/var/log/asset-agent'
log_file_path = os.path.join(log_dir, 'asset_agent.log')
try:
    os.makedirs(log_dir, exist_ok=True)
    # os.makedirs(exist_ok=True) does not raise if the directory already
    # exists but is owned by someone else (e.g. root, from an earlier
    # install run as a systemd service) - it only fails on CREATING a new
    # directory without permission. Actually opening the log file is the
    # real test of whether this process can write here.
    with open(log_file_path, 'a'):
        pass
except (PermissionError, OSError):
    # Fallback to user directory if no root access / directory not ours
    log_dir = os.path.expanduser('~/.asset-agent')
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, 'asset_agent.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# ODOO API CONFIGURATION
# ============================================================================
ODOO_API_URL = f"{SERVER_URL}/api/laptop_monitor"
ODOO_DB_NAME = "odoo19"  # Must match db_name in odoo.conf
ODOO_DB_NAME = _AGENT_CFG.get("db") or ODOO_DB_NAME
ODOO_HEADERS = {
    "Content-Type": "application/json",
    "X-Odoo-Database": ODOO_DB_NAME,
}
STATIC_SYNC_INTERVAL = 1800  # 30 minutes
LIVE_SYNC_INTERVAL = 60  # 1 minute heartbeat
SHOW_UI = False

# ============================================================================
# OS UPDATE CONFIGURATION (Linux equivalent of Windows Update)
# ============================================================================
OS_UPDATE_BASE_URL = f"{SERVER_URL}/api/asset/updates"
OS_UPDATE_SYNC_INTERVAL = 3600  # 1 hour

# ============================================================================
# FILE ACCESS POLICY CONFIGURATION
# ============================================================================
FILE_ACCESS_BASE_URL = f"{SERVER_URL}/api/asset/file_access"
FILE_ACCESS_SYNC_INTERVAL = 300  # Poll policy from Odoo every 5 minutes


def get_real_user_home():
    """Return home dir of the actual desktop user.
    
    When the agent runs under systemd as root, os.path.expanduser('~') returns
    '/root', which has no Desktop/Documents/Downloads. This helper finds the
    real human user (first UID >= 1000 with a home in /home) so the file
    scanner sees the user's actual files.
    """
    try:
        import pwd
        # Honour SUDO_USER if the agent was started via sudo
        user = os.environ.get('SUDO_USER')
        if user and user != 'root':
            return pwd.getpwnam(user).pw_dir
        # Otherwise find the first real human user on this machine
        for p in pwd.getpwall():
            if 1000 <= p.pw_uid < 65534 and p.pw_dir.startswith('/home/') and os.path.isdir(p.pw_dir):
                return p.pw_dir
    except Exception:
        pass
    # Last-resort fallback
    return os.path.expanduser("~")


def get_monitored_folders():
    """Return the folders we scan: Desktop, Documents, Downloads, Pictures, Trash.

    'Trash' on Linux is at ~/.local/share/Trash/files (XDG spec), not ~/Trash.
    We expose it under the friendly name 'Trash' in Odoo.
    """
    home = get_real_user_home()
    return [
        os.path.join(home, "Desktop"),
        os.path.join(home, "Documents"),
        os.path.join(home, "Downloads"),
        os.path.join(home, "Pictures"),
        os.path.join(home, ".local", "share", "Trash", "files"),
    ]

# ============================================================================
# ANTIVIRUS DEPLOYMENT CONFIGURATION
# ============================================================================
ANTIVIRUS_BASE_URL = f"{SERVER_URL}/api/antivirus"
ANTIVIRUS_POLL_INTERVAL = 120  # Poll every 2 minutes

# ============================================================================
# SOFTWARE DEPLOYMENT CONFIGURATION
# ============================================================================
SOFTWARE_BASE_URL = f"{SERVER_URL}/api/asset/software"
SOFTWARE_SYNC_INTERVAL = 120  # Poll every 2 minutes

# ============================================================================
# APP DEPLOYMENT CONFIGURATION (package-manager-based)
# ============================================================================
APP_DEPLOY_BASE_URL = f"{SERVER_URL}/asset_management/api/agent"
APP_DEPLOY_POLL_INTERVAL = 120  # Poll every 2 minutes

# ============================================================================
# APPLICATION UNINSTALL CONFIGURATION
# ============================================================================
APP_UNINSTALL_BASE_URL = f"{SERVER_URL}/api/asset/apps"
APP_UNINSTALL_SYNC_INTERVAL = 120  # Poll every 2 minutes

# ============================================================================
# ENTERPRISE FILESYSTEM INVENTORY CONFIGURATION
# ============================================================================
EXTRA_FILES_INCLUDE_HIDDEN = False

# ============================================================================
# GLOBAL CACHES AND LOCKS
# ============================================================================
_cached_serial_number = None
_cached_device_model = None
_cached_graphics_card = None
_cached_location = None
_last_location_fetch = 0
_cache_lock = threading.Lock()
_static_sync_lock = threading.Lock()
_live_sync_lock = threading.Lock()
_os_update_lock = threading.Lock()

FILE_BROWSER_PORT = 8000


# ============================================================================
# AUTO-UPDATE FUNCTIONS
# ============================================================================

def check_for_updates():
    """Check if a new version is available"""
    try:
        logger.info(f"Checking for updates (current version: {AGENT_VERSION})")
        response = requests.get(
            UPDATE_CHECK_URL,
            timeout=10,
            params={"current_version": AGENT_VERSION, "platform": "ubuntu"},
            headers=ODOO_HEADERS
        )
        if response.status_code == 200:
            data = response.json()
            latest_version = data.get("latest_version", AGENT_VERSION)
            download_url = data.get("download_url", UPDATE_DOWNLOAD_URL)

            def parse_version(v):
                return tuple(map(int, v.split('.')))

            if parse_version(latest_version) > parse_version(AGENT_VERSION):
                logger.info(f"INFO New version available: {latest_version}")
                return True, latest_version, download_url
            else:
                logger.info("OK Agent is up to date")
                return False, None, None
        else:
            logger.warning(f"Update check failed: HTTP {response.status_code}")
            return False, None, None
    except Exception as e:
        logger.warning(f"Error checking for updates: {e}")
        return False, None, None


def download_update(download_url):
    """Download the new agent version"""
    try:
        temp_dir = tempfile.gettempdir()
        update_file = os.path.join(temp_dir, "AssetAgent_update.tar.gz")
        logger.info(f"Downloading update from {download_url}")
        response = requests.get(download_url, stream=True, timeout=300)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        with open(update_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0 and downloaded % (1024 * 1024) == 0:
                        progress = (downloaded / total_size) * 100
                        logger.info(f"Download progress: {progress:.1f}%")
        logger.info(f"OK Update downloaded successfully: {update_file}")
        return update_file
    except Exception as e:
        logger.error(f"Error downloading update: {e}")
        return None


def apply_update(update_file):
    """Apply the update by launching updater and exiting"""
    try:
        current_exe = os.path.abspath(__file__)
        updater_script = os.path.join(tempfile.gettempdir(), "agent_updater.sh")

        bash_content = f'''#!/bin/bash
echo "[AssetAgent Updater] Waiting for agent to exit..."
sleep 3

echo "[AssetAgent Updater] Backing up old version..."
if [ -f "{current_exe}.bak" ]; then
    rm "{current_exe}.bak"
fi
mv "{current_exe}" "{current_exe}.bak"

echo "[AssetAgent Updater] Extracting new version..."
tar -xzf "{update_file}" -C "$(dirname {current_exe})"

if [ -f "{current_exe}" ]; then
    echo "[AssetAgent Updater] Setting permissions..."
    chmod +x "{current_exe}"

    echo "[AssetAgent Updater] Starting updated agent..."
    nohup python3 "{current_exe}" > /dev/null 2>&1 &
    echo "[AssetAgent Updater] Update complete!"
else
    echo "[AssetAgent Updater] ERROR: Update failed, restoring backup..."
    mv "{current_exe}.bak" "{current_exe}"
    nohup python3 "{current_exe}" > /dev/null 2>&1 &
fi

sleep 2
rm -f "{update_file}"
rm -f "$0"
'''

        with open(updater_script, 'w') as f:
            f.write(bash_content)

        os.chmod(updater_script, 0o755)

        logger.info("RESTART Launching updater and exiting current agent...")

        subprocess.Popen(
            [updater_script],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        time.sleep(1)
        sys.exit(0)

    except Exception as e:
        logger.error(f"Error applying update: {e}")
        return False


def update_checker_thread():
    """Background thread that periodically checks for updates"""
    logger.info(f"Update checker started (interval: {UPDATE_CHECK_INTERVAL}s)")
    time.sleep(60)
    while True:
        try:
            has_update, new_version, download_url = check_for_updates()
            if has_update:
                logger.info(f"UPDATE Updating to version {new_version}...")
                update_file = download_update(download_url)
                if update_file and os.path.exists(update_file):
                    logger.info("Applying update now...")
                    apply_update(update_file)
                    break
                else:
                    logger.error("ERROR Update download failed, will retry next interval")
            time.sleep(UPDATE_CHECK_INTERVAL)
        except Exception as e:
            logger.error(f"Error in update checker: {e}")
            time.sleep(UPDATE_CHECK_INTERVAL)


# ============================================================================
# NETWORK / HTTP
# ============================================================================

def send_with_retry(url, payload, max_retries=3, timeout=30):
    """Send HTTP POST request with exponential backoff retry logic"""
    for attempt in range(max_retries):
        try:
            response = requests.post(
                url, json=payload, timeout=timeout,
                headers=ODOO_HEADERS
            )
            if response.status_code == 200:
                logger.info(f"Successfully sent data to {url}")
                return True, response
            else:
                logger.warning(f"Request to {url} failed with status {response.status_code}")
                if response.status_code >= 500 and attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + (time.time() % 1)
                    logger.info(f"Retrying in {wait_time:.2f}s...")
                    time.sleep(wait_time)
                    continue
                return False, response
        except requests.exceptions.Timeout:
            logger.warning(f"Request to {url} timed out (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep((2 ** attempt) + (time.time() % 1))
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error to {url}: {e} (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep((2 ** attempt) + (time.time() % 1))
        except Exception as e:
            logger.error(f"Unexpected error sending to {url}: {e}")
            if attempt < max_retries - 1:
                time.sleep((2 ** attempt) + (time.time() % 1))
    logger.error(f"Failed to send data to {url} after {max_retries} attempts")
    return False, None


class NetworkMonitor:
    def __init__(self):
        self.last_bytes_sent = 0
        self.last_bytes_recv = 0
        self.last_check_time = time.time()
        self.lock = threading.Lock()
        self._initialize()

    def _initialize(self):
        try:
            counters = psutil.net_io_counters()
            self.last_bytes_sent = counters.bytes_sent
            self.last_bytes_recv = counters.bytes_recv
            self.last_check_time = time.time()
        except:
            pass

    def get_network_usage(self):
        with self.lock:
            try:
                counters = psutil.net_io_counters()
                current_time = time.time()
                time_delta = current_time - self.last_check_time
                if time_delta < 0.1:
                    return 0, 0
                bytes_sent = counters.bytes_sent - self.last_bytes_sent
                bytes_recv = counters.bytes_recv - self.last_bytes_recv
                upload_bps = (bytes_sent * 8) / time_delta if time_delta > 0 else 0
                download_bps = (bytes_recv * 8) / time_delta if time_delta > 0 else 0
                upload_mbps = round(upload_bps / 1_000_000, 2)
                download_mbps = round(download_bps / 1_000_000, 2)
                self.last_bytes_sent = counters.bytes_sent
                self.last_bytes_recv = counters.bytes_recv
                self.last_check_time = current_time
                return upload_mbps, download_mbps
            except:
                return 0, 0


network_monitor = NetworkMonitor()


def get_local_ip():
    """Get the real LAN IP (Wi-Fi or Ethernet), skip Docker/virtual interfaces."""
    try:
        skip_keywords = ['docker', 'veth', 'vmnet', 'virbr', 'lo', 'vbox', 'br-', 'virtual', 'wsl']
        for iface_name, addrs in psutil.net_if_addrs().items():
            if any(kw in iface_name.lower() for kw in skip_keywords):
                continue
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    ip = addr.address
                    if ip.startswith('192.168.') or ip.startswith('10.'):
                        logger.info(f"Detected LAN IP: {ip} on interface: {iface_name}")
                        return ip
    except Exception as e:
        logger.warning(f"Error detecting LAN IP via psutil: {e}")

    # Fallback: connect to external DNS to detect outbound IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        logger.info(f"Detected IP via socket fallback: {ip}")
        return ip
    except Exception as e:
        logger.warning(f"Socket fallback failed: {e}")
        return '127.0.0.1'


def get_mac_address():
    """Get the MAC address of the active LAN interface (same interface as get_local_ip)."""
    try:
        skip_keywords = ['docker', 'veth', 'vmnet', 'virbr', 'lo', 'vbox', 'br-', 'virtual', 'wsl']
        for iface_name, addrs in psutil.net_if_addrs().items():
            if any(kw in iface_name.lower() for kw in skip_keywords):
                continue
            has_lan_ip = any(
                addr.family == socket.AF_INET and
                (addr.address.startswith('192.168.') or addr.address.startswith('10.'))
                for addr in addrs
            )
            if not has_lan_ip:
                continue
            for addr in addrs:
                if addr.family == psutil.AF_LINK and addr.address and addr.address != '00:00:00:00:00:00':
                    logger.info(f"Detected MAC: {addr.address} on interface: {iface_name}")
                    return addr.address.upper()
    except Exception as e:
        logger.warning(f"Error detecting MAC address: {e}")
    return ''


# ============================================================================
# LOCATION DETECTION (GeoClue for Ubuntu + IP fallback)
# ============================================================================

def get_location_data():
    """Get location using Ubuntu GeoClue service with caching"""
    global _cached_location, _last_location_fetch

    current_time = time.time()
    with _cache_lock:
        if _cached_location and (current_time - _last_location_fetch < 1800):
            return _cached_location

    try:
        # Try to use GeoClue2 via D-Bus
        try:
            import dbus
            from dbus.mainloop.glib import DBusGMainLoop

            DBusGMainLoop(set_as_default=True)
            bus = dbus.SystemBus()

            geoclue = bus.get_object('org.freedesktop.GeoClue2', '/org/freedesktop/GeoClue2/Manager')
            manager = dbus.Interface(geoclue, 'org.freedesktop.GeoClue2.Manager')

            client_path = manager.GetClient()
            client = bus.get_object('org.freedesktop.GeoClue2', client_path)
            client_interface = dbus.Interface(client, 'org.freedesktop.GeoClue2.Client')

            props = dbus.Interface(client, 'org.freedesktop.DBus.Properties')
            props.Set('org.freedesktop.GeoClue2.Client', 'DesktopId', 'asset-agent')
            props.Set('org.freedesktop.GeoClue2.Client', 'RequestedAccuracyLevel', dbus.UInt32(8))

            client_interface.Start()

            location_path = props.Get('org.freedesktop.GeoClue2.Client', 'Location')

            if location_path and location_path != '/':
                location = bus.get_object('org.freedesktop.GeoClue2', location_path)
                location_props = dbus.Interface(location, 'org.freedesktop.DBus.Properties')

                latitude = float(location_props.Get('org.freedesktop.GeoClue2.Location', 'Latitude'))
                longitude = float(location_props.Get('org.freedesktop.GeoClue2.Location', 'Longitude'))
                accuracy = float(location_props.Get('org.freedesktop.GeoClue2.Location', 'Accuracy'))

                client_interface.Stop()

                logger.info(f"Successfully obtained location from GeoClue (lat: {latitude}, lon: {longitude}, accuracy: {accuracy}m)")

                result = {
                    "public_ip": "",
                    "location_country": "",
                    "location_region": "",
                    "location_city": "",
                    "location_latitude": latitude,
                    "location_longitude": longitude,
                    "location_source": "geoclue"
                }

                with _cache_lock:
                    _cached_location = result
                    _last_location_fetch = current_time

                return result

        except Exception as e:
            logger.warning(f"GeoClue error: {e}")

        # Fallback to IP-based location if GeoClue fails
        logger.info("Falling back to IP-based location")

        providers = [
            {"url": "https://ipapi.co/json/", "name": "ipapi.co"},
            {"url": "https://ipinfo.io/json", "name": "ipinfo.io"},
            {"url": "https://ifconfig.co/json", "name": "ifconfig.co"}
        ]

        for provider in providers:
            try:
                response = requests.get(provider["url"], timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    logger.info(f"Successfully fetched IP-based location data from {provider['name']}")

                    res = {
                        "public_ip": str(data.get("ip", "")),
                        "location_country": "",
                        "location_region": "",
                        "location_city": str(data.get("city", "")),
                        "location_latitude": 0.0,
                        "location_longitude": 0.0,
                        "location_source": "ip"
                    }

                    if provider["name"] == "ipapi.co":
                        res["location_country"] = str(data.get("country_name", ""))
                        res["location_region"] = str(data.get("region", ""))
                        res["location_latitude"] = float(data.get("latitude", 0.0))
                        res["location_longitude"] = float(data.get("longitude", 0.0))
                    elif provider["name"] == "ipinfo.io":
                        res["location_country"] = str(data.get("country", ""))
                        res["location_region"] = str(data.get("region", ""))
                        loc = data.get("loc", "").split(',')
                        if len(loc) == 2:
                            res["location_latitude"] = float(loc[0])
                            res["location_longitude"] = float(loc[1])
                    elif provider["name"] == "ifconfig.co":
                        res["location_country"] = str(data.get("country", ""))
                        res["location_region"] = str(data.get("region_name", ""))
                        res["location_latitude"] = float(data.get("latitude", 0.0))
                        res["location_longitude"] = float(data.get("longitude", 0.0))

                    with _cache_lock:
                        _cached_location = res
                        _last_location_fetch = current_time

                    return res
                else:
                    logger.warning(f"IP provider {provider['name']} failed (HTTP {response.status_code})")
            except Exception as e:
                logger.warning(f"Error fetching from {provider['name']}: {e}")

        with _cache_lock:
            if _cached_location:
                return _cached_location

        result = {
            "public_ip": "",
            "location_country": "",
            "location_region": "",
            "location_city": "",
            "location_latitude": 0.0,
            "location_longitude": 0.0,
            "location_source": "unavailable"
        }

        with _cache_lock:
            _cached_location = result
            _last_location_fetch = current_time

        return result

    except Exception as e:
        logger.error(f"Unexpected error in location detection: {e}")
        result = {
            "public_ip": "",
            "location_country": "",
            "location_region": "",
            "location_city": "",
            "location_latitude": 0.0,
            "location_longitude": 0.0,
            "location_source": "unavailable"
        }
        with _cache_lock:
            _cached_location = result
            _last_location_fetch = current_time
        return result


# ============================================================================
# SYSTEM INFORMATION FUNCTIONS (CACHED)
# ============================================================================

def run_command(cmd, shell=False):
    """Run a shell command and return output"""
    try:
        if shell:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        else:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except Exception as e:
        logger.debug(f"Command failed: {cmd}, Error: {e}")
        return None


def _share_serial(serial):
    """Publish the serial for the remote-assistance worker.

    The worker runs as the logged-in desktop user (it needs the screen), and
    a normal user cannot run dmidecode or read /sys/class/dmi/id/product_serial.
    Without this file it cannot find its own asset, so SSH Connect and
    Connect Remote never reach the machine."""
    path = "/var/lib/asset-agent/serial"
    try:
        try:
            with open(path) as f:
                if f.read().strip() == serial:
                    return
        except OSError:
            pass
        os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
        with open(path, "w") as f:
            f.write(serial + "\n")
        os.chmod(path, 0o644)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Could not write {path}: {e}")


def get_serial_number():
    """Get system serial number using multiple methods (cached)"""
    global _cached_serial_number
    with _cache_lock:
        if _cached_serial_number is not None:
            return _cached_serial_number

    try:
        serial = None

        # Method 1: Try dmidecode (requires root)
        output = run_command(['dmidecode', '-s', 'system-serial-number'])
        if output and output.upper() not in ['TO BE FILLED BY O.E.M.', '0', 'NONE', 'UNKNOWN', 'DEFAULT STRING', 'NOT SPECIFIED', '']:
            serial = output

        # Method 2-5: Try various /sys/class/dmi/id/ files
        if not serial:
            for dmi_file in ['product_serial', 'board_serial', 'product_uuid', 'chassis_serial']:
                try:
                    path = f'/sys/class/dmi/id/{dmi_file}'
                    if os.path.exists(path):
                        with open(path, 'r') as f:
                            val = f.read().strip()
                            if val and val.upper() not in ['TO BE FILLED BY O.E.M.', '0', 'NONE', 'UNKNOWN', 'DEFAULT STRING', 'NOT SPECIFIED', '']:
                                serial = val
                                break
                except:
                    continue

        # Method 6: Try /proc/cpuinfo
        if not serial:
            try:
                if os.path.exists('/proc/cpuinfo'):
                    with open('/proc/cpuinfo', 'r') as f:
                        for line in f:
                            if line.lower().startswith('serial'):
                                val = line.split(':')[-1].strip()
                                if val and val not in ['0000000000000000']:
                                    serial = val
                                    break
            except:
                pass

        # Method 7: Fallback to MAC address
        if not serial:
            try:
                for interface, addrs in psutil.net_if_addrs().items():
                    if interface == 'lo':
                        continue
                    for addr in addrs:
                        if hasattr(psutil, 'AF_LINK') and addr.family == psutil.AF_LINK:
                            serial = f"MAC-{addr.address.replace(':', '').upper()}"
                            break
                    if serial:
                        break
            except:
                pass

        if serial:
            with _cache_lock:
                _cached_serial_number = serial
            _share_serial(serial)
            return serial

        with _cache_lock:
            _cached_serial_number = "UNKNOWN"
        return "UNKNOWN"

    except Exception as e:
        logger.warning(f"Error getting serial number: {e}")
        return "UNKNOWN"


def get_device_model():
    """Get device manufacturer and model (cached)"""
    global _cached_device_model
    with _cache_lock:
        if _cached_device_model is not None:
            return _cached_device_model

    try:
        manufacturer = None
        model = None

        manufacturer = run_command(['dmidecode', '-s', 'system-manufacturer'])
        model = run_command(['dmidecode', '-s', 'system-product-name'])

        if not manufacturer:
            try:
                with open('/sys/class/dmi/id/sys_vendor', 'r') as f:
                    manufacturer = f.read().strip()
            except:
                pass

        if not model:
            try:
                with open('/sys/class/dmi/id/product_name', 'r') as f:
                    model = f.read().strip()
            except:
                pass

        if manufacturer and manufacturer.upper() in ['TO BE FILLED BY O.E.M.', 'SYSTEM MANUFACTURER', 'DEFAULT STRING']:
            manufacturer = None
        if model and model.upper() in ['TO BE FILLED BY O.E.M.', 'SYSTEM PRODUCT NAME', 'DEFAULT STRING']:
            model = None

        if not manufacturer and not model:
            device_model = socket.gethostname()
        elif not manufacturer:
            device_model = model or 'Unknown'
        elif not model:
            device_model = manufacturer or 'Unknown'
        elif model.lower().startswith(manufacturer.lower()):
            device_model = model
        else:
            device_model = f"{manufacturer} {model}"

        with _cache_lock:
            _cached_device_model = device_model
        return device_model

    except Exception as e:
        logger.warning(f"Error getting device model: {e}")
        hostname = socket.gethostname()
        with _cache_lock:
            _cached_device_model = hostname
        return hostname


def get_graphics_card():
    """Get graphics card information (cached)"""
    global _cached_graphics_card
    with _cache_lock:
        if _cached_graphics_card is not None:
            return _cached_graphics_card

    try:
        output = run_command(['lspci'], shell=False)
        if output:
            gpu_lines = [line for line in output.split('\n') if 'VGA' in line or 'Display' in line or '3D' in line]
            if gpu_lines:
                gpus = []
                for line in gpu_lines:
                    if ':' in line:
                        gpu_name = line.split(':', 2)[-1].strip()
                        gpus.append(gpu_name)
                if gpus:
                    gpu_info = ", ".join(gpus)
                    with _cache_lock:
                        _cached_graphics_card = gpu_info
                    return gpu_info

        # Fallback: Check /sys/class/drm
        try:
            drm_path = '/sys/class/drm'
            if os.path.exists(drm_path):
                cards = [d for d in os.listdir(drm_path) if d.startswith('card') and '-' not in d]
                if cards:
                    gpu_info = f"DRM Graphics Card ({len(cards)} device(s))"
                    with _cache_lock:
                        _cached_graphics_card = gpu_info
                    return gpu_info
        except:
            pass

        with _cache_lock:
            _cached_graphics_card = "No GPU Detected"
        return "No GPU Detected"

    except Exception as e:
        logger.warning(f"Error getting graphics card: {e}")
        with _cache_lock:
            _cached_graphics_card = "Detection Failed"
        return "Detection Failed"


def get_disk_type():
    """Detect if primary disk is SSD or HDD"""
    try:
        output = run_command(['lsblk', '-d', '-o', 'NAME,ROTA,TYPE'], shell=False)
        if output:
            for line in output.split('\n')[1:]:
                parts = line.split()
                if len(parts) >= 3 and parts[2] == 'disk':
                    name, rota = parts[0], parts[1]
                    if rota == '0':
                        return "SSD"
                    elif rota == '1':
                        return "HDD"

        root_partition = run_command(['df', '/', '--output=source'], shell=False)
        if root_partition:
            lines = root_partition.split('\n')
            if len(lines) >= 2:
                device_path = lines[1].strip()
                device_name = os.path.basename(device_path)
                m = re.match(r'(nvme\d+n\d+)(p\d+)?', device_name)
                if m:
                    device_name = m.group(1)
                else:
                    device_name = re.sub(r'\d+$', '', device_name)
                rotational_path = f'/sys/block/{device_name}/queue/rotational'
                if os.path.exists(rotational_path):
                    with open(rotational_path, 'r') as f:
                        if f.read().strip() == '0':
                            return "SSD"
                        else:
                            return "HDD"

        if os.path.exists('/sys/block/'):
            for dev in os.listdir('/sys/block/'):
                if dev.startswith(('sd', 'nvme', 'vd')):
                    rot_path = f'/sys/block/{dev}/queue/rotational'
                    if os.path.exists(rot_path):
                        with open(rot_path, 'r') as f:
                            if f.read().strip() == '0':
                                return "SSD"

        return "Unknown"
    except Exception as e:
        logger.debug(f"Error detecting disk type: {e}")
        return "Unknown"


def get_storage_volumes():
    """Detect all mounted filesystems"""
    try:
        volumes = []
        partitions = psutil.disk_partitions(all=False)
        seen_mountpoints = set()

        for partition in partitions:
            mount = partition.mountpoint
            if not partition.fstype or mount in seen_mountpoints:
                continue
            if partition.fstype in ['tmpfs', 'proc', 'sysfs', 'overlay', 'devtmpfs', 'squashfs']:
                continue

            try:
                usage = psutil.disk_usage(mount)
                volumes.append({
                    'drive_letter': mount,
                    'total_size': round(usage.total / (1024 ** 3), 2),
                    'free_space': round(usage.free / (1024 ** 3), 2),
                    'used_space': round(usage.used / (1024 ** 3), 2),
                    'drive_label': "System" if mount == "/" else os.path.basename(mount) or "Data"
                })
                seen_mountpoints.add(mount)
            except:
                continue

        return json.dumps(volumes)
    except Exception as e:
        logger.warning(f"Error getting storage volumes: {e}")
        return "[]"


def get_installed_apps():
    """Collect installed applications from dpkg, rpm, snap, and flatpak"""
    all_apps = {}

    has_dpkg = run_command(['which', 'dpkg']) is not None
    has_rpm = run_command(['which', 'rpm']) is not None
    has_snap = run_command(['which', 'snap']) is not None
    has_flatpak = run_command(['which', 'flatpak']) is not None

    # Method 1: dpkg (Debian/Ubuntu)
    if has_dpkg:
        try:
            manual_output = run_command(['apt-mark', 'showmanual'], shell=False)
            manual_packages = set(line.strip() for line in manual_output.split('\n') if line.strip()) if manual_output else set()

            # Sizes for ALL packages in ONE call, instead of spawning a
            # dpkg-query subprocess per package inside the loop below.
            #
            # That per-package fork was the expensive part: on a machine with
            # a few hundred manual packages it meant several hundred forks
            # every static sync. Forking is proportional to the parent's
            # memory footprint (page tables are copied), so once the agent is
            # large each fork is costly - and under swap pressure the whole
            # burst thrashes the disk, which is what a "hang" feels like.
            # One call instead of hundreds.
            _pkg_sizes = {}
            try:
                _size_out = run_command(
                    ['dpkg-query', '-W', '-f=${Package}\\t${Installed-Size}\\n'],
                    shell=False)
                for _l in (_size_out or '').split('\n'):
                    if '\t' not in _l:
                        continue
                    _n, _sz = _l.split('\t', 1)
                    try:
                        _pkg_sizes[_n.strip()] = float(_sz.strip() or 0)
                    except ValueError:
                        pass
            except Exception as e:
                logger.warning(f"Bulk package size lookup failed: {e}")

            output = run_command(['dpkg', '-l'], shell=False)
            if output:
                lines = output.split('\n')
                for line in lines:
                    if line.startswith('ii'):
                        parts = line.split()
                        if len(parts) >= 3:
                            name = parts[1]
                            pkg_name = name.split(':')[0]
                            version = parts[2]

                            if pkg_name not in manual_packages and name not in manual_packages:
                                continue

                            exclude_prefixes = (
                                'libc', 'lib', 'linux-', 'systemd', 'python3-', 'gcc-',
                                'grub', 'snapd', 'udev', 'mesa', 'xserver', 'fonts-', 'firmware-',
                                'binutils', 'coreutils', 'cpp-', 'dash', 'diffutils', 'dpkg',
                                'findutils', 'grep', 'gzip', 'hostname', 'init', 'iproute2',
                                'iptables', 'iputils-', 'kmod', 'login', 'lsb-', 'mount',
                                'netbase', 'ncurses-', 'pam', 'perl', 'sed', 'tar', 'util-linux'
                            )

                            if pkg_name.startswith(exclude_prefixes):
                                continue

                            installed_date = ""
                            list_file = f"/var/lib/dpkg/info/{name}.list"
                            if os.path.exists(list_file):
                                try:
                                    st = os.stat(list_file)
                                    installed_date = datetime.fromtimestamp(st.st_ctime).strftime('%Y-%m-%d')
                                except Exception:
                                    pass

                            # Looked up from the bulk map above - no subprocess here.
                            size_kb = _pkg_sizes.get(name)
                            if size_kb is None:
                                size_kb = _pkg_sizes.get(pkg_name, 0.0)
                            size_mb = round((size_kb or 0.0) / 1024, 2)

                            all_apps[name] = {
                                'name': name[:255],
                                'publisher': 'System Package',
                                'version': version[:100],
                                'installed_date': installed_date,
                                'size': size_mb
                            }
            logger.info(f"dpkg: Found {len(all_apps)} packages")
        except Exception as e:
            logger.warning(f"dpkg read failed: {e}")

    # Method 2: rpm (RedHat/CentOS/Fedora)
    if has_rpm:
        try:
            output = run_command(['rpm', '-qa', '--queryformat', '%{NAME}|%{VERSION}|%{VENDOR}\n'], shell=False)
            if output:
                lines = output.split('\n')
                for line in lines:
                    if '|' in line:
                        parts = line.split('|')
                        if len(parts) >= 2:
                            name = parts[0].strip()
                            version = parts[1].strip()
                            publisher = parts[2].strip() if len(parts) > 2 else 'System Package'
                            if name not in all_apps:
                                all_apps[name] = {
                                    'name': name[:255],
                                    'publisher': publisher[:255],
                                    'version': version[:100],
                                    'installed_date': '',
                                    'size': 0
                                }
            logger.info(f"rpm: Found {len(all_apps)} packages")
        except Exception as e:
            logger.warning(f"rpm read failed: {e}")

    # Method 3: Snap packages
    if has_snap:
        try:
            output = run_command(['snap', 'list'], shell=False)
            if output:
                lines = output.split('\n')[1:]
                for line in lines:
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 2:
                            name = parts[0]
                            version = parts[1]
                            publisher = parts[3] if len(parts) > 3 else 'Snap Store'
                            snap_name = f"{name} (Snap)"
                            if snap_name not in all_apps:
                                all_apps[snap_name] = {
                                    'name': snap_name[:255],
                                    'publisher': publisher[:255],
                                    'version': version[:100],
                                    'installed_date': '',
                                    'size': 0
                                }
            logger.info(f"snap: Found {len([k for k in all_apps.keys() if 'Snap' in k])} snap packages")
        except Exception as e:
            logger.warning(f"snap read failed: {e}")

    # Method 4: Flatpak packages
    if has_flatpak:
        try:
            output = run_command(['flatpak', 'list', '--app', '--columns=name,version,origin'], shell=False)
            if output:
                lines = output.split('\n')[1:]
                for line in lines:
                    if line.strip():
                        parts = line.split('\t')
                        if len(parts) >= 1:
                            name = parts[0].strip()
                            version = parts[1].strip() if len(parts) > 1 else ''
                            origin = parts[2].strip() if len(parts) > 2 else 'Flathub'
                            flatpak_name = f"{name} (Flatpak)"
                            if flatpak_name not in all_apps:
                                all_apps[flatpak_name] = {
                                    'name': flatpak_name[:255],
                                    'publisher': origin[:255],
                                    'version': version[:100],
                                    'installed_date': '',
                                    'size': 0
                                }
            logger.info(f"flatpak: Found {len([k for k in all_apps.keys() if 'Flatpak' in k])} flatpak packages")
        except Exception as e:
            logger.warning(f"flatpak read failed: {e}")

    if len(all_apps) == 0:
        logger.warning("No applications found from any package manager")
        return "[]"

    logger.info(f"OK Total unique applications: {len(all_apps)}")
    return json.dumps(list(all_apps.values()))


def get_os_version():
    """Detect OS name and version"""
    try:
        output = run_command(['lsb_release', '-d'], shell=False)
        if output and ':' in output:
            return output.split(':', 1)[1].strip()
        if os.path.exists('/etc/os-release'):
            with open('/etc/os-release', 'r') as f:
                for line in f:
                    if line.startswith('PRETTY_NAME='):
                        return line.split('=', 1)[1].strip().strip('"')
        return f"{platform.system()} {platform.release()}"
    except Exception as e:
        logger.debug(f"Error getting OS version: {e}")
        return f"{platform.system()} {platform.release()}"


def get_processor_info():
    """Get processor information"""
    try:
        output = run_command(['lscpu'], shell=False)
        if output:
            for line in output.split('\n'):
                if line.startswith('Model name:'):
                    return line.split(':', 1)[1].strip()
        if os.path.exists('/proc/cpuinfo'):
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('model name'):
                        return line.split(':', 1)[1].strip()
        return platform.processor() or "Unknown Processor"
    except Exception as e:
        logger.debug(f"Error getting processor info: {e}")
        return "Unknown Processor"
def get_chassis_type():
    """Read DMI chassis type (Linux).
    
    Returns one of: laptop, desktop, server, vm, unknown.
    See https://www.dmtf.org/sites/default/files/standards/documents/DSP0134_3.7.0.pdf
    """
    try:
        with open('/sys/class/dmi/id/chassis_type') as f:
            n = int(f.read().strip())
        # DMI chassis type code → our asset_type
        if n in (8, 9, 10, 11, 14, 31, 32):    # Portable, Laptop, Notebook, Hand-held, Sub-Notebook, Convertible, Detachable
            return 'laptop'
        if n in (3, 4, 5, 6, 7, 13, 15, 16):   # Desktop, Low-Profile, Pizza-box, Mini-Tower, Tower, All-in-One, Space-saving, Lunchbox
            return 'desktop'
        if n in (17, 23, 25, 28):              # Server-chassis, Rack-mount, Multi-system, Blade
            return 'server'
        if n == 1:                              # "Other"
            return 'unknown'
    except Exception:
        pass
    # Fallback: if it has a battery, it's a laptop
    try:
        if os.path.exists('/sys/class/power_supply'):
            for d in os.listdir('/sys/class/power_supply'):
                if d.startswith('BAT'):
                    return 'laptop'
    except Exception:
        pass
    return 'unknown'

def get_battery_capacity():
    """Get battery capacity from /sys/class/power_supply/"""
    try:
        power_supply_path = '/sys/class/power_supply'
        if not os.path.exists(power_supply_path):
            return 0, 0
        batteries = [d for d in os.listdir(power_supply_path) if d.startswith('BAT')]
        if not batteries:
            return 0, 0
        battery_path = os.path.join(power_supply_path, batteries[0])
        energy_full_path = os.path.join(battery_path, 'energy_full')
        charge_full_path = os.path.join(battery_path, 'charge_full')
        capacity_mwh = 0
        if os.path.exists(energy_full_path):
            with open(energy_full_path, 'r') as f:
                capacity_uwh = int(f.read().strip())
                capacity_mwh = capacity_uwh / 1000
        elif os.path.exists(charge_full_path):
            with open(charge_full_path, 'r') as f:
                capacity_uah = int(f.read().strip())
                capacity_mah = capacity_uah / 1000
                capacity_mwh = capacity_mah * 11.1
        if capacity_mwh == 0:
            return 0, 0
        capacity_mah = round(capacity_mwh / 11.1, 2)
        return int(capacity_mwh), capacity_mah
    except Exception as e:
        logger.debug(f"Error getting battery capacity: {e}")
        return 0, 0


def get_uptime():
    """Get system uptime in human-readable format"""
    try:
        if os.path.exists('/proc/uptime'):
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.readline().split()[0])
        else:
            uptime_seconds = time.time() - psutil.boot_time()
        days = int(uptime_seconds // (24 * 3600))
        hours = int((uptime_seconds % (24 * 3600)) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        parts = []
        if days > 0:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes > 0 or not parts:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        return ", ".join(parts)
    except Exception as e:
        logger.debug(f"Error getting uptime: {e}")
        return "Unknown"


def get_total_storage_size():
    """Get total storage size in GB"""
    try:
        total_gb = 0.0
        partitions = psutil.disk_partitions(all=False)
        seen_mountpoints = set()
        for partition in partitions:
            mount = partition.mountpoint
            if not partition.fstype or mount in seen_mountpoints:
                continue
            if partition.fstype in ['tmpfs', 'proc', 'sysfs', 'overlay', 'devtmpfs', 'squashfs']:
                continue
            is_valid_mount = mount == "/" or mount == "/home" or mount == "/data" or mount.startswith("/mnt/")
            if is_valid_mount:
                try:
                    usage = psutil.disk_usage(mount)
                    total_gb += usage.total / (1024 ** 3)
                    seen_mountpoints.add(mount)
                except:
                    continue
        return round(total_gb, 2)
    except Exception as e:
        logger.warning(f"Error calculating total storage size: {e}")
        return 0.0


# ============================================================================
# OS UPDATE FUNCTIONS (Linux equivalent of Windows Update)
# ============================================================================

def scan_os_updates():
    """
    Scan available OS updates using apt.
    Returns list of update dicts: kb_number, title, severity, size, version.
    """
    logger.info("Scanning for available OS updates...")
    updates = []
    try:
        # Run apt update first (requires root)
        run_command(_scoped(['apt-get', 'update', '-qq']))

        # Get upgradable packages
        result = subprocess.run(
            ['apt', 'list', '--upgradable'],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            logger.warning(f"apt list --upgradable failed: {result.stderr}")
            return []

        lines = result.stdout.strip().split('\n')
        for line in lines:
            if '/' not in line or 'Listing...' in line:
                continue
            try:
                # Format: package/source version arch [upgradable from: old_version]
                pkg_part = line.split('/')[0].strip()
                rest = line.split(' ')
                new_version = rest[1] if len(rest) > 1 else ''
                arch = rest[2] if len(rest) > 2 else ''

                # Determine severity
                severity = 'optional'
                source_part = line.split('/')[1].split(' ')[0] if '/' in line else ''
                if 'security' in source_part.lower():
                    severity = 'security'

                updates.append({
                    'kb_number': pkg_part,
                    'title': f"{pkg_part} {new_version}",
                    'description': f"Update {pkg_part} to version {new_version}",
                    'severity': severity,
                    'size': '',
                    'version': new_version[:100],
                })
            except Exception:
                continue

        logger.info(f"OK Found {len(updates)} pending OS updates")
        return updates

    except Exception as e:
        logger.error(f"Error scanning OS updates: {e}")
        return []


def scan_installed_updates():
    """
    Scan recently installed updates.
    Returns list of package names that are currently installed.
    """
    logger.info("Scanning installed packages...")
    installed_kbs = []
    try:
        # Get list of manually installed packages
        output = run_command(['apt-mark', 'showmanual'])
        if output:
            for line in output.strip().split('\n'):
                pkg = line.strip()
                if pkg:
                    installed_kbs.append(pkg)
        logger.info(f"OK Found {len(installed_kbs)} installed packages")
        return installed_kbs
    except Exception as e:
        logger.warning(f"Error scanning installed packages: {e}")
        return []


def report_updates_to_odoo(serial_number, updates, installed_kbs=None):
    """Send scanned updates to Odoo.

    Posts to /api/asset/os_updates/report, which routes by the asset's
    platform. The previous endpoint (/api/asset/updates/report) wrote to
    asset.windows.update regardless of platform, which is why Ubuntu package
    names were stored as Windows KB numbers.

    Falls back to the legacy endpoint if the extensions module is absent, so
    an agent built without it still reports something.
    """
    if not updates and not installed_kbs:
        logger.info("No updates to report to Odoo")
        return

    if EXTENSIONS_AVAILABLE:
        # Translate to the platform-aware payload: kb_number -> identifier
        translated = []
        for upd in updates:
            item = dict(upd)
            if 'kb_number' in item and 'identifier' not in item:
                item['identifier'] = item.pop('kb_number')
            translated.append(item)

        ok = report_os_updates(
            SERVER_URL, serial_number,
            updates=translated,
            installed=installed_kbs or [],
        )
        if ok:
            logger.info("OK Reported %s available + %s installed updates "
                        "(platform-aware endpoint)",
                        len(translated), len(installed_kbs or []))
        else:
            logger.warning("Failed to report updates to Odoo")
        return

    # Legacy path
    try:
        payload = {
            "serial_number": serial_number,
            "updates": updates,
            "installed_kbs": installed_kbs or []
        }
        url = f"{OS_UPDATE_BASE_URL}/report"
        success, response = send_with_retry(url, payload, max_retries=3, timeout=30)
        if success:
            logger.info(f"OK Reported {len(updates)} available + {len(installed_kbs or [])} installed updates to Odoo")
        else:
            logger.warning("Failed to report updates to Odoo")
    except Exception as e:
        logger.error(f"Error reporting updates to Odoo: {e}")



def get_update_instructions(serial_number):
    """Poll Odoo for update instructions."""
    default = {"is_locked": False, "blocklist": [], "push_list": [], "uninstall_list": [], "cancel_list": []}
    try:
        url = f"{OS_UPDATE_BASE_URL}/instructions"
        response = requests.get(
            url, params={"serial_number": serial_number},
            timeout=15, headers=ODOO_HEADERS
        )
        if response.status_code == 200:
            data = response.json()
            logger.info(
                f"Received instructions — locked: {data.get('is_locked')}, "
                f"block: {len(data.get('blocklist', []))}, "
                f"push: {len(data.get('push_list', []))}, "
                f"uninstall: {len(data.get('uninstall_list', []))}, "
                f"cancel: {len(data.get('cancel_list', []))}"
            )
            return data
        else:
            logger.warning(f"Failed to get update instructions: HTTP {response.status_code}")
            return default
    except Exception as e:
        logger.warning(f"Error getting update instructions: {e}")
        return default


def install_os_update(package_name):
    """Install a specific package update via apt."""
    logger.info(f"Installing OS update: {package_name}")
    try:
        result = subprocess.run(
            _scoped(['apt-get', 'install', '-y', '--only-upgrade', package_name]),
            capture_output=True, text=True, timeout=600
        )
        output = result.stdout.strip()
        logger.info(f"Install result for {package_name}: exit code {result.returncode}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout installing {package_name}")
        return False
    except Exception as e:
        logger.error(f"Error installing {package_name}: {e}")
        return False


def uninstall_os_update(package_name):
    """Remove a specific package via apt."""
    logger.info(f"Uninstalling package: {package_name}")
    try:
        result = subprocess.run(
            _scoped(['apt-get', 'remove', '-y', package_name]),
            capture_output=True, text=True, timeout=300
        )
        logger.info(f"Uninstall result for {package_name}: exit code {result.returncode}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout uninstalling {package_name}")
        return False
    except Exception as e:
        logger.error(f"Error uninstalling {package_name}: {e}")
        return False


def report_update_result(serial_number, kb_number, status):
    """Report install/uninstall/cancel result back to Odoo."""
    try:
        payload = {"serial_number": serial_number, "kb_number": kb_number, "status": status}
        url = f"{OS_UPDATE_BASE_URL}/result"
        success, response = send_with_retry(url, payload, max_retries=3, timeout=15)
        if success:
            logger.info(f"OK Reported {kb_number} result ({status}) to Odoo")
        else:
            logger.warning(f"Failed to report result for {kb_number}")
    except Exception as e:
        logger.error(f"Error reporting update result: {e}")


def enforce_update_lock(is_locked):
    """
    When locked:  disable apt auto-updates.
    When unlocked: restore normal apt behavior.
    """
    apt_conf_path = "/etc/apt/apt.conf.d/99-asset-agent-lock"
    if is_locked:
        logger.info("[LOCK] Disabling apt auto-update capability")
        try:
            content = 'APT::Periodic::Update-Package-Lists "0";\nAPT::Periodic::Unattended-Upgrade "0";\n'
            with open(apt_conf_path, 'w') as f:
                f.write(content)
            logger.info("[LOCK] OK apt auto-updates disabled")
        except PermissionError:
            logger.warning("[LOCK] No root permission to lock apt updates")
        except Exception as e:
            logger.error(f"[LOCK] Error applying lock: {e}")
    else:
        logger.info("[LOCK] Restoring apt auto-update capability")
        try:
            if os.path.exists(apt_conf_path):
                os.remove(apt_conf_path)
            logger.info("[LOCK] OK apt auto-updates restored")
        except PermissionError:
            logger.warning("[LOCK] No root permission to unlock apt updates")
        except Exception as e:
            logger.error(f"[LOCK] Error removing lock: {e}")


def execute_update_instructions(serial_number, instructions):
    """Execute admin instructions from Odoo."""
    is_locked = instructions.get('is_locked', False)
    blocklist = instructions.get('blocklist', [])
    push_list = instructions.get('push_list', [])
    uninstall_list = instructions.get('uninstall_list', [])
    cancel_list = instructions.get('cancel_list', [])

    enforce_update_lock(is_locked)

    if is_locked:
        logger.info("Device is locked — no update actions will be performed by agent")
        return

    if blocklist:
        logger.info(f"Blocked updates (suppressed by admin): {blocklist}")
        # Hold packages via apt-mark
        for pkg in blocklist:
            try:
                subprocess.run(['apt-mark', 'hold', pkg], capture_output=True, text=True, timeout=10)
                logger.info(f"[BLOCK] Held package: {pkg}")
            except Exception as e:
                logger.warning(f"[BLOCK] Failed to hold {pkg}: {e}")

    if cancel_list:
        logger.info(f"[CANCEL] Admin requested cancellation of: {cancel_list}")
        for pkg in cancel_list:
            report_update_result(serial_number, pkg, 'cancelled')
            logger.info(f"[CANCEL] Reported cancelled for {pkg}")

    if push_list:
        logger.info(f"[INSTALL] Admin requested installation of: {push_list}")
        for pkg in push_list:
            try:
                success = install_os_update(pkg)
                status = 'installed' if success else 'failed'
                report_update_result(serial_number, pkg, status)
                logger.info(f"[INSTALL] {'SUCCESS' if success else 'FAILED'} {pkg} -> {status}")
            except Exception as e:
                logger.error(f"[INSTALL] ERROR installing {pkg}: {e}")
                report_update_result(serial_number, pkg, 'failed')

    if uninstall_list:
        logger.info(f"[UNINSTALL] Admin requested uninstall of: {uninstall_list}")
        for pkg in uninstall_list:
            try:
                success = uninstall_os_update(pkg)
                status = 'uninstalled' if success else 'failed'
                report_update_result(serial_number, pkg, status)
                logger.info(f"[UNINSTALL] {'SUCCESS' if success else 'FAILED'} {pkg} -> {status}")
            except Exception as e:
                logger.error(f"[UNINSTALL] ERROR uninstalling {pkg}: {e}")
                report_update_result(serial_number, pkg, 'failed')


def os_update_sync_loop():
    """Background thread for OS Update sync."""
    logger.info(f"OS Update sync loop started (interval: {OS_UPDATE_SYNC_INTERVAL}s)")
    time.sleep(30)

    while True:
        try:
            if not _os_update_lock.acquire(blocking=False):
                logger.warning("OS Update sync already in progress, skipping")
                time.sleep(OS_UPDATE_SYNC_INTERVAL)
                continue
            try:
                logger.info("=" * 40)
                logger.info("Starting OS Update sync cycle...")
                serial = get_serial_number()
                updates = scan_os_updates()
                installed_kbs = scan_installed_updates()
                if updates or installed_kbs:
                    report_updates_to_odoo(serial, updates, installed_kbs)
                instructions = get_update_instructions(serial)
                execute_update_instructions(serial, instructions)
                logger.info("OS Update sync cycle complete")
                logger.info("=" * 40)
            finally:
                _os_update_lock.release()
        except Exception as e:
            logger.error(f"Error in OS Update sync loop: {e}")
        time.sleep(OS_UPDATE_SYNC_INTERVAL)


# ============================================================================
# APPLICATION UNINSTALL FUNCTIONS
# ============================================================================

def find_application_installed(app_name, publisher=None, version=None):
    """
    Search installed packages across apt / snap / flatpak for an application.
    Returns dict with package_name, display_name, publisher, version, pkg_manager.

    Matching strategy (each search adds candidates to found_apps with a score):
      1. EXACT match on package name              -> score 1000
      2. EXACT match on display/snap name         -> score  900
      3. Package name starts with the search term -> score  500
      4. Search term starts with package name     -> score  400  (e.g. "vlc media player" -> "vlc")
      5. Either contains the other (substring)    -> score  200
      6. Penalty for sub-packages (libfoo, foo-data, foo-bin, foo-plugin-*)  -> -150
      7. Boost snap/flatpak slightly over apt by 50 points (the running app)
    """
    logger.info(f"[APP UNINSTALL] Searching for: {app_name}")
    needle = app_name.lower().strip()
    # Drop trailing qualifiers Odoo often adds (e.g. "VLC media player" -> also try "vlc")
    needle_short = needle.split()[0] if needle else needle

    SUBPKG_HINTS = ('lib', 'python3-', 'gir1.2-')
    SUBPKG_SUFFIXES = ('-data', '-common', '-bin', '-dev', '-doc', '-dbg', '-l10n')
    SUBPKG_PATTERNS = ('-plugin-', '-plugins-', '-extra', '-utils', '-tools-')

    def is_subpkg(pkg):
        p = pkg.lower()
        if any(p.startswith(h) for h in SUBPKG_HINTS):
            return True
        if any(p.endswith(s) for s in SUBPKG_SUFFIXES):
            return True
        if any(pat in p for pat in SUBPKG_PATTERNS):
            return True
        return False

    def score_match(pkg_name, display_name=None):
        """Return match score, or 0 if no match."""
        p = pkg_name.lower()
        d = (display_name or '').lower()
        s = 0
        # 1. Exact match
        if needle == p:
            s = 1000
        elif d and needle == d:
            s = 900
        # 2. Short name exact (e.g. "vlc media player" -> "vlc")
        elif needle_short == p:
            s = 850
        # 3. Starts-with
        elif p.startswith(needle) or (needle_short and p.startswith(needle_short)):
            s = 500
        elif needle.startswith(p) and len(p) >= 3:
            s = 400
        # 4. Loose substring
        elif needle in p or p in needle:
            s = 200
        else:
            return 0
        # Penalty for sub-packages
        if is_subpkg(pkg_name):
            s -= 150
        return s

    found_apps = []

    # --- Search dpkg/apt ---
    try:
        output = run_command(['dpkg', '-l'])
        if output:
            for line in output.split('\n'):
                if line.startswith('ii'):
                    parts = line.split()
                    if len(parts) >= 3:
                        pkg = parts[1].split(':')[0]
                        ver = parts[2]
                        s = score_match(pkg)
                        if s > 0:
                            found_apps.append({
                                'package_name': pkg, 'display_name': pkg,
                                'publisher': 'dpkg', 'version': ver,
                                'pkg_manager': 'apt', '_score': s,
                            })
    except Exception as e:
        logger.debug(f"[APP UNINSTALL] dpkg search failed: {e}")

    # --- Search snap ---
    try:
        output = run_command(['snap', 'list'])
        if output:
            for line in output.split('\n')[1:]:
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 2:
                        pkg = parts[0]
                        s = score_match(pkg)
                        if s > 0:
                            found_apps.append({
                                'package_name': pkg,
                                'display_name': f"{pkg} (Snap)",
                                'publisher': parts[3] if len(parts) > 3 else 'Snap Store',
                                'version': parts[1],
                                'pkg_manager': 'snap',
                                '_score': s + 50,  # slight preference: snap is the running one
                            })
    except Exception as e:
        logger.debug(f"[APP UNINSTALL] snap search failed: {e}")

    # --- Search flatpak ---
    try:
        output = run_command(['flatpak', 'list', '--app', '--columns=application,name,version'])
        if output:
            for line in output.split('\n'):
                if line.strip():
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        app_id = parts[0].strip()
                        name = parts[1].strip() if len(parts) > 1 else app_id
                        ver = parts[2].strip() if len(parts) > 2 else ''
                        s = max(score_match(app_id), score_match(name, name))
                        if s > 0:
                            found_apps.append({
                                'package_name': app_id,
                                'display_name': f"{name} (Flatpak)",
                                'publisher': 'Flatpak', 'version': ver,
                                'pkg_manager': 'flatpak', '_score': s + 50,
                            })
    except Exception as e:
        logger.debug(f"[APP UNINSTALL] flatpak search failed: {e}")

    if not found_apps:
        logger.warning(f"[APP UNINSTALL] No matching application found for: {app_name}")
        return None

    # Highest score wins
    found_apps.sort(key=lambda a: a['_score'], reverse=True)
    best = found_apps[0]
    logger.info(
        f"[APP UNINSTALL] Found: {best['display_name']} via {best['pkg_manager']} "
        f"(score={best['_score']}, considered={len(found_apps)} candidates)"
    )
    # Strip internal score field before returning
    best.pop('_score', None)
    return best


def uninstall_application(app_name, publisher, version):
    """Uninstall an application by name. Returns (success, error_message)."""
    logger.info(f"[APP UNINSTALL] Starting uninstall for: {app_name}")

    try:
        app_info = find_application_installed(app_name, publisher, version)
        if not app_info:
            error_msg = f"Application '{app_name}' not found"
            logger.error(f"[APP UNINSTALL] {error_msg}")
            return (False, error_msg)

        pkg_name = app_info['package_name']
        pkg_manager = app_info['pkg_manager']

        if pkg_manager == 'apt':
            result = subprocess.run(
                _scoped(['apt-get', 'remove', '-y', pkg_name]),
                capture_output=True, text=True, timeout=600
            )
        elif pkg_manager == 'snap':
            result = subprocess.run(
                ['snap', 'remove', pkg_name],
                capture_output=True, text=True, timeout=600
            )
        elif pkg_manager == 'flatpak':
            result = subprocess.run(
                ['flatpak', 'uninstall', '-y', pkg_name],
                capture_output=True, text=True, timeout=600
            )
        else:
            return (False, f"Unknown package manager: {pkg_manager}")

        logger.info(f"[APP UNINSTALL] Exit code: {result.returncode}")
        if result.stdout:
            logger.info(f"[APP UNINSTALL] stdout: {result.stdout[:500]}")
        if result.stderr:
            logger.warning(f"[APP UNINSTALL] stderr: {result.stderr[:500]}")

        if result.returncode == 0:
            logger.info(f"[APP UNINSTALL] SUCCESS: {app_name} uninstalled")
            return (True, None)
        else:
            error_msg = f"Uninstall failed with exit code {result.returncode}"
            logger.error(f"[APP UNINSTALL] FAILED: {error_msg}")
            return (False, error_msg)

    except subprocess.TimeoutExpired:
        return (False, "Uninstall timed out after 10 minutes")
    except Exception as e:
        return (False, f"Uninstall error: {str(e)}")


def get_app_uninstall_instructions(serial_number):
    """Poll Odoo for application uninstall instructions."""
    default = {"success": True, "uninstall_list": []}
    try:
        url = f"{APP_UNINSTALL_BASE_URL}/uninstall_command"
        response = requests.get(url, params={"serial_number": serial_number}, timeout=15, headers=ODOO_HEADERS)
        if response.status_code == 200:
            data = response.json()
            uninstall_list = data.get('uninstall_list', [])
            logger.info(f"[APP UNINSTALL] Received {len(uninstall_list)} uninstall commands from Odoo")
            return data
        else:
            logger.warning(f"[APP UNINSTALL] Failed to get instructions: HTTP {response.status_code}")
            return default
    except Exception as e:
        logger.warning(f"[APP UNINSTALL] Error getting instructions: {e}")
        return default


def report_app_uninstall_result(serial_number, app_name, app_publisher, app_version, status, error_message=None):
    """Report application uninstall result back to Odoo."""
    try:
        payload = {
            "serial_number": serial_number,
            "app_name": app_name,
            "app_publisher": app_publisher,
            "app_version": app_version,
            "status": status,
        }
        if error_message:
            payload["error_message"] = error_message
        url = f"{APP_UNINSTALL_BASE_URL}/uninstall_result"
        success, response = send_with_retry(url, payload, max_retries=3, timeout=15)
        if success:
            logger.info(f"[APP UNINSTALL] Reported result: {app_name} -> {status}")
        else:
            logger.warning(f"[APP UNINSTALL] Failed to report result for {app_name}")
    except Exception as e:
        logger.error(f"[APP UNINSTALL] Error reporting result: {e}")


def execute_app_uninstall_instructions(serial_number, instructions):
    """Execute application uninstall instructions from Odoo."""
    uninstall_list = instructions.get('uninstall_list', [])
    if not uninstall_list:
        return

    logger.info(f"[APP UNINSTALL] Processing {len(uninstall_list)} uninstall commands")

    for app in uninstall_list:
        app_name = app.get('name', '')
        app_publisher = app.get('publisher', '')
        app_version = app.get('version', '')

        if not app_name:
            continue

        logger.info(f"[APP UNINSTALL] Processing: {app_name} (v{app_version}) by {app_publisher}")

        try:
            success, error_message = uninstall_application(app_name, app_publisher, app_version)
            status = 'uninstalled' if success else 'failed'
            report_app_uninstall_result(serial_number, app_name, app_publisher, app_version, status, error_message)
            logger.info(f"[APP UNINSTALL] {'SUCCESS' if success else 'FAILED'}: {app_name} -> {status}")
        except Exception as e:
            logger.error(f"[APP UNINSTALL] ERROR processing {app_name}: {e}")
            report_app_uninstall_result(serial_number, app_name, app_publisher, app_version, 'failed', str(e))


def app_uninstall_sync_loop():
    """Background thread for application uninstall sync."""
    logger.info(f"[APP UNINSTALL] Sync loop started (interval: {APP_UNINSTALL_SYNC_INTERVAL}s)")
    time.sleep(30)

    serial = get_serial_number()

    while True:
        try:
            instructions = get_app_uninstall_instructions(serial)
            if instructions.get('uninstall_list'):
                execute_app_uninstall_instructions(serial, instructions)
            else:
                logger.debug("[APP UNINSTALL] No pending uninstall commands")
        except Exception as e:
            logger.error(f"[APP UNINSTALL] Error in sync loop: {e}")
        time.sleep(APP_UNINSTALL_SYNC_INTERVAL)


# ============================================================================
# FILE ACCESS POLICY — BLOCK + NOTIFY + REPORT
# ============================================================================

_file_access_policy = {}
_file_access_lock = threading.Lock()
_file_access_watchers = {}


def fa_show_notification(blocked_path):
    """Show desktop notification when access is blocked."""
    try:
        title = "Access Blocked by Admin Policy"
        message = f"Access to '{os.path.basename(blocked_path)}' has been blocked by your administrator."
        # Try notify-send (standard Linux desktop notification)
        subprocess.Popen(
            ['notify-send', '--urgency=critical', '--icon=dialog-warning', title, message],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        logger.info(f"[FILE ACCESS] Notification shown for: {blocked_path}")
    except Exception as e:
        logger.warning(f"[FILE ACCESS] Notification error: {e}")


def fa_is_safe_to_block(path):
    """Safety check — never allow blocking entire drives or system folders."""
    path = os.path.normpath(path).rstrip("/")
    home = get_real_user_home()

    # Never block root
    if path == "/":
        logger.warning(f"[FILE ACCESS] SAFETY: Refused to block root: {path}")
        return False

    # Never block these protected paths
    protected = [
        home,
        "/",
        "/home",
        "/tmp",
        "/etc",
        "/usr",
        "/var",
        "/bin",
        "/sbin",
        "/boot",
        os.path.join(home, "Desktop"),
        os.path.join(home, "Documents"),
        os.path.join(home, "Downloads"),
        os.path.join(home, "Pictures"),
        os.path.join(home, "Music"),
        os.path.join(home, "Videos"),
    ]
    for p in protected:
        if path == os.path.normpath(p):
            logger.warning(f"[FILE ACCESS] SAFETY: Refused to block protected path: {path}")
            return False

    return True


def fa_block_path(path):
    """Block access to a file or folder using chmod."""
    try:
        username = os.environ.get('USER', os.environ.get('LOGNAME', ''))
        if not username:
            return False

        # Remove all permissions for the user
        result = subprocess.run(
            ['chmod', '000', path],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            logger.info(f"[FILE ACCESS] Blocked path: {path}")
            return True
        else:
            # Try with sudo
            result = subprocess.run(
                ['sudo', 'chmod', '000', path],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                logger.info(f"[FILE ACCESS] Blocked path (sudo): {path}")
                return True
            logger.warning(f"[FILE ACCESS] chmod block failed: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"[FILE ACCESS] Block error: {e}")
        return False


def fa_unblock_path(path):
    """Restore access to a file or folder."""
    try:
        if os.path.isdir(path):
            mode = '755'
        else:
            mode = '644'

        result = subprocess.run(
            ['chmod', mode, path],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            logger.info(f"[FILE ACCESS] Unblocked path: {path}")
            return True

        result = subprocess.run(
            ['sudo', 'chmod', mode, path],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            logger.info(f"[FILE ACCESS] Unblocked path (sudo): {path}")
            return True
        return False
    except Exception as e:
        logger.error(f"[FILE ACCESS] Unblock error: {e}")
        return False


def fa_report_violation(serial, blocked_path, action_taken="blocked"):
    """Report access violation to Odoo."""
    try:
        payload = {
            "serial_number": serial,
            "path": blocked_path,
            "folder": os.path.dirname(blocked_path),
            "filename": os.path.basename(blocked_path),
            "action_taken": action_taken,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        ok, _ = send_with_retry(f"{FILE_ACCESS_BASE_URL}/violation", payload, max_retries=2, timeout=10)
        if ok:
            logger.info(f"[FILE ACCESS] Violation reported: {blocked_path}")
    except Exception as e:
        logger.warning(f"[FILE ACCESS] Report violation error: {e}")


def fa_get_policy(serial):
    """Poll Odoo for file access policy."""
    try:
        url = f"{FILE_ACCESS_BASE_URL}/policy?serial_number={serial}"
        response = requests.get(url, timeout=10, headers=ODOO_HEADERS)
        if response.status_code == 200:
            data = response.json()
            paths = data.get("blocked_paths", [])
            logger.info(f"[FILE ACCESS] Policy received: {len(paths)} blocked path(s)")
            return paths
        else:
            logger.warning(f"[FILE ACCESS] Policy fetch failed: HTTP {response.status_code}")
            return []
    except Exception as e:
        logger.warning(f"[FILE ACCESS] Policy fetch error: {e}")
        return []


def fa_check_lock_bypass(serial, path):
    """A path this agent locked (chmod 000) should still BE 000 every time
    this runs. If it isn't, someone ran chmod (or chown/setfacl) directly on
    it to override the lock outside of anything the agent did - report it
    as a security event, then re-block it back to 000 so the override does
    not stick between polls.

    Called from fa_enforce_policy() below, which already runs on a
    schedule (FILE_ACCESS_SYNC_INTERVAL) - this rides that same cycle
    instead of needing its own poll loop.
    """
    if not SECURITY_AUDIT_AVAILABLE or not os.path.exists(path):
        return
    try:
        mode = oct(os.stat(path).st_mode & 0o777)
    except OSError:
        return
    if mode != '0o0':
        logger.warning(
            f"[FILE ACCESS] Locked path permissions overridden: {path} "
            f"is now {mode}, expected 0o0")
        security_audit_mod.report_security_event(
            SERVER_URL, serial, 'file_lock_bypassed',
            f'Locked path "{path}" permissions changed to {mode} '
            f'(e.g. via chmod) - re-locking it.',
            old_value='0o0', new_value=mode, db_name=ODOO_DB_NAME)
        fa_block_path(path)


def fa_enforce_policy(serial, blocked_paths):
    """Compare current policy with new policy. Block/unblock as needed."""
    global _file_access_policy

    new_policy = {p: True for p in blocked_paths}

    with _file_access_lock:
        for path in list(_file_access_policy.keys()):
            if path not in new_policy:
                fa_unblock_path(path)
                del _file_access_policy[path]
                logger.info(f"[FILE ACCESS] Policy removed — unblocked: {path}")
            else:
                fa_check_lock_bypass(serial, path)

        for path in new_policy:
            if path not in _file_access_policy:
                if not fa_is_safe_to_block(path):
                    logger.warning(f"[FILE ACCESS] Skipped unsafe path: {path}")
                    continue
                if os.path.exists(path):
                    ok = fa_block_path(path)
                    if ok:
                        _file_access_policy[path] = True
                        fa_show_notification(path)
                        fa_report_violation(serial, path, action_taken="blocked_by_policy")
                else:
                    logger.warning(f"[FILE ACCESS] Path does not exist: {path}")


def fa_monitor_access(serial):
    """Monitor folders using watchdog with per-path debouncing to avoid
    duplicate violations (a single file save can fire many fs events)."""
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        class AccessHandler(FileSystemEventHandler):
            # Window during which repeat events on the same path are dropped
            DEBOUNCE_SECONDS = 60

            def __init__(self, serial):
                self.serial = serial
                self._recent = {}        # path -> last_reported_timestamp
                self._recent_lock = threading.Lock()

            def _is_duplicate(self, path):
                now = time.time()
                with self._recent_lock:
                    last = self._recent.get(path)
                    if last and (now - last) < self.DEBOUNCE_SECONDS:
                        return True
                    self._recent[path] = now
                    # Opportunistically purge old entries to bound memory
                    if len(self._recent) > 500:
                        cutoff = now - self.DEBOUNCE_SECONDS
                        self._recent = {p: t for p, t in self._recent.items() if t >= cutoff}
                return False

            def _check_and_enforce(self, event_path):
                with _file_access_lock:
                    for blocked in _file_access_policy:
                        if event_path.startswith(blocked):
                            if self._is_duplicate(event_path):
                                return  # already reported recently — silent skip
                            logger.warning(f"[FILE ACCESS] Access attempt on blocked path: {event_path}")
                            fa_block_path(blocked)
                            fa_show_notification(event_path)
                            fa_report_violation(self.serial, event_path, action_taken="blocked")

            def on_created(self, event):
                if not event.is_directory:
                    self._check_and_enforce(event.src_path)

            def on_modified(self, event):
                if not event.is_directory:
                    self._check_and_enforce(event.src_path)

        monitored = get_monitored_folders()
        observer = Observer()
        handler = AccessHandler(serial)

        for folder in monitored:
            if os.path.exists(folder):
                observer.schedule(handler, folder, recursive=True)
                logger.info(f"[FILE ACCESS] Watching: {folder}")

        observer.start()
        logger.info("[FILE ACCESS] File system monitor started")
        return observer

    except ImportError:
        logger.warning("[FILE ACCESS] watchdog not installed — monitoring disabled. Run: pip install watchdog")
        return None
    except Exception as e:
        logger.error(f"[FILE ACCESS] Monitor start error: {e}")
        return None


_fa_observer = None


def fa_scan_folders(serial):
    """Recursively scan user folders and send to Odoo.
    
    Each folder gets its own quota so one huge folder (e.g. Desktop with
    an extracted archive containing thousands of files) can't starve
    the others. Total payload is also capped to keep Odoo healthy.
    """
    try:
        home = get_real_user_home()
        root_folders = {
            "Desktop":   os.path.join(home, "Desktop"),
            "Documents": os.path.join(home, "Documents"),
            "Downloads": os.path.join(home, "Downloads"),
            "Pictures":  os.path.join(home, "Pictures"),
            "Trash":     os.path.join(home, ".local", "share", "Trash", "files"),
        }

        records = []
        MAX_RECORDS_TOTAL = 2000      # was 5000 — too heavy on Odoo
        MAX_RECORDS_PER_FOLDER = 400  # NEW: ensure every folder gets coverage
        EXCLUSIONS = {
            "node_modules", ".git", ".next", "dist", "build",
            ".cache", "__pycache__", ".venv", "venv",
            "bin", "obj", ".idea", ".vscode"
        }

        for folder_name, folder_path in root_folders.items():
            if not os.path.exists(folder_path):
                continue
            if len(records) >= MAX_RECORDS_TOTAL:
                break

            folder_start = len(records)  # how many records we had before this folder

            try:
                st = os.stat(folder_path)
                records.append({
                    "type": "folder",
                    "name": folder_name,
                    "path": folder_path,
                    "parent_path": os.path.dirname(folder_path),
                    "parent_folder": folder_name,
                    "size_kb": 0,
                    "last_modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                })
            except:
                pass

            for dirpath, dirnames, filenames in os.walk(folder_path):
                dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in EXCLUSIONS]

                folder_count = len(records) - folder_start
                if folder_count >= MAX_RECORDS_PER_FOLDER or len(records) >= MAX_RECORDS_TOTAL:
                    break

                for dname in dirnames:
                    folder_count = len(records) - folder_start
                    if folder_count >= MAX_RECORDS_PER_FOLDER or len(records) >= MAX_RECORDS_TOTAL:
                        break
                    full_p = os.path.join(dirpath, dname)
                    try:
                        st = os.stat(full_p)
                        records.append({
                            "type": "folder",
                            "name": dname,
                            "path": full_p,
                            "parent_path": os.path.dirname(full_p),
                            "parent_folder": folder_name,
                            "size_kb": 0,
                            "last_modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                        })
                    except:
                        continue

                for fname in filenames:
                    folder_count = len(records) - folder_start
                    if folder_count >= MAX_RECORDS_PER_FOLDER or len(records) >= MAX_RECORDS_TOTAL:
                        break
                    full_p = os.path.join(dirpath, fname)
                    try:
                        st = os.stat(full_p)
                        records.append({
                            "type": "file",
                            "name": fname,
                            "path": full_p,
                            "parent_path": os.path.dirname(full_p),
                            "parent_folder": folder_name,
                            "size_kb": round(st.st_size / 1024, 2),
                            "last_modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                        })
                    except:
                        continue

        if records:
            # Log breakdown by folder so we can see distribution
            by_folder = {}
            for r in records:
                pf = r.get("parent_folder", "?")
                by_folder[pf] = by_folder.get(pf, 0) + 1
            breakdown = ", ".join(f"{k}:{v}" for k, v in by_folder.items())

            payload = {
                "serial_number": serial,
                "records": records,
                "scanned_at": datetime.now(timezone.utc).isoformat(),
            }
            ok, _ = send_with_retry(f"{FILE_ACCESS_BASE_URL}/scan", payload, max_retries=1, timeout=120)
            if ok:
                logger.info(f"[FILE ACCESS] Scanned {len(records)} items ({breakdown}) → sent to Odoo")
            else:
                logger.warning("[FILE ACCESS] Scan send failed")
        else:
            logger.warning("[FILE ACCESS] No records found")

    except Exception as e:
        logger.error(f"[FILE ACCESS] Scan error: {e}")


def file_access_sync_loop():
    """Background thread for file access policy sync.
    
    Two separate cadences:
      - File system SCAN: heavy (sends 100s-1000s of records to Odoo).
        Runs every FA_SCAN_INTERVAL (default 30 min).
      - Policy POLL + enforcement: lightweight (small GET + chmod).
        Runs every FILE_ACCESS_SYNC_INTERVAL (default 5 min).
    """
    global _fa_observer
    FA_SCAN_INTERVAL = 1800  # full file scan every 30 minutes
    logger.info(
        f"[FILE ACCESS v1.6] Sync loop started "
        f"(scan: {FA_SCAN_INTERVAL}s, policy: {FILE_ACCESS_SYNC_INTERVAL}s)"
    )

    serial = get_serial_number()
    _fa_observer = fa_monitor_access(serial)

    last_scan_at = 0  # epoch seconds of last scan; 0 = never, force first scan immediately

    while True:
        try:
            now = time.time()

            # Always: lightweight policy poll + enforce
            blocked_paths = fa_get_policy(serial)
            fa_enforce_policy(serial, blocked_paths)

            # Conditionally: heavy file scan (only every FA_SCAN_INTERVAL)
            if now - last_scan_at >= FA_SCAN_INTERVAL:
                fa_scan_folders(serial)
                last_scan_at = time.time()

        except Exception as e:
            logger.error(f"[FILE ACCESS] Sync loop error: {e}")
        time.sleep(FILE_ACCESS_SYNC_INTERVAL)


# ============================================================================
# ANTIVIRUS DETECTION & DEPLOYMENT FUNCTIONS
# ============================================================================

def get_antivirus_info():
    """Get comprehensive antivirus information (cross-platform dispatcher)."""
    try:
        return get_antivirus_info_linux()
    except Exception as e:
        logger.warning(f"[AV] Error getting antivirus info: {e}")
        return {
            "antivirus_installed": False,
            "antivirus_product": f"Error: {e}",
            "antivirus_version": "unknown",
            "antivirus_running": False
        }


def get_antivirus_info_linux():
    """Get antivirus information for Linux systems."""
    result = {
        "antivirus_installed": False,
        "antivirus_product": "None",
        "antivirus_version": "unknown",
        "antivirus_running": False
    }

    try:
        av_detection_map = {
            "clamav": {"service": "clamav-daemon", "process": "clamd", "package": "clamav", "name": "ClamAV"},
            "comodo": {"service": "cavdaemon", "process": "cmdscan", "package": "comodo-antivirus", "name": "Comodo Antivirus"},
            "sophos": {"service": "sophos-av", "process": "savd", "package": "sophos-av", "name": "Sophos Anti-Virus"},
            "eset": {"service": "esets", "process": "esets_daemon", "package": "eset", "name": "ESET NOD32"},
            "bitdefender": {"service": "bdagent", "process": "vbd", "package": "bitdefender", "name": "Bitdefender GravityZone"},
            "kaspersky": {"service": "kav", "process": "kavdaemon", "package": "kaspersky", "name": "Kaspersky Endpoint Security"},
            "f-secure": {"service": "fsma", "process": "fses", "package": "f-secure", "name": "F-Secure Protection"},
        }

        # Method 1: Check services via systemctl
        for av_key, av_info in av_detection_map.items():
            try:
                service_check = subprocess.run(
                    ["systemctl", "is-active", av_info["service"]],
                    capture_output=True, text=True, timeout=5
                )
                is_active = service_check.stdout.strip() == "active"
                if is_active:
                    result["antivirus_installed"] = True
                    result["antivirus_product"] = av_info["name"]
                    result["antivirus_running"] = True
                    version = get_av_version_linux(av_info["package"])
                    if version:
                        result["antivirus_version"] = version
                    logger.info(f"[AV-Linux] Service: {av_info['service']} -> {av_info['name']}")
                    return result
            except Exception:
                continue

        # Method 2: Check running processes
        try:
            ps_result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
            if ps_result.returncode == 0:
                processes = ps_result.stdout.lower()
                for av_key, av_info in av_detection_map.items():
                    if av_info["process"] in processes:
                        result["antivirus_installed"] = True
                        result["antivirus_product"] = av_info["name"]
                        result["antivirus_running"] = True
                        version = get_av_version_linux(av_info["package"])
                        if version:
                            result["antivirus_version"] = version
                        logger.info(f"[AV-Linux] Process: {av_info['process']} -> {av_info['name']}")
                        return result
        except Exception:
            pass

        # Method 3: Check packages installed
        for av_key, av_info in av_detection_map.items():
            try:
                dpkg_result = subprocess.run(["dpkg", "-l", av_info["package"]], capture_output=True, text=True, timeout=5)
                if dpkg_result.returncode == 0 and av_info["package"] in dpkg_result.stdout:
                    result["antivirus_installed"] = True
                    result["antivirus_product"] = av_info["name"]
                    result["antivirus_running"] = False
                    version = get_av_version_linux(av_info["package"])
                    if version:
                        result["antivirus_version"] = version
                    logger.info(f"[AV-Linux] Package: {av_info['package']} -> {av_info['name']}")
                    return result
            except Exception:
                pass

        logger.info("[AV-Linux] No antivirus detected")
        return result

    except Exception as e:
        logger.warning(f"[AV-Linux] Error: {e}")
        result["antivirus_product"] = f"Error: {e}"
        return result


def get_av_version_linux(package_name):
    """Get antivirus version from Linux package manager."""
    try:
        dpkg_result = subprocess.run(["dpkg", "-s", package_name], capture_output=True, text=True, timeout=5)
        if dpkg_result.returncode == 0:
            for line in dpkg_result.stdout.split('\n'):
                if line.startswith('Version:'):
                    return line.split(':', 1)[1].strip()
        rpm_result = subprocess.run(["rpm", "-q", "--qf", "%{VERSION}", package_name], capture_output=True, text=True, timeout=5)
        if rpm_result.returncode == 0 and rpm_result.stdout:
            return rpm_result.stdout.strip()
        for binary_name in [package_name, f"{package_name}-daemon", f"{package_name}d"]:
            try:
                version_result = subprocess.run([binary_name, "--version"], capture_output=True, text=True, timeout=5)
                if version_result.returncode == 0 and version_result.stdout:
                    version_line = version_result.stdout.split('\n')[0].strip()
                    if len(version_line) < 100:
                        return version_line
            except Exception:
                continue
        return None
    except Exception:
        return None


def check_antivirus_installed():
    """Check if antivirus is installed and running."""
    try:
        av_info = get_antivirus_info()
        if av_info["antivirus_installed"]:
            return av_info["antivirus_running"], av_info["antivirus_product"]
        return False, "No antivirus detected"
    except Exception as e:
        logger.warning(f"[AV] Error checking antivirus: {e}")
        return False, f"Check failed: {e}"


def download_installer(installer_url, platform_name="linux"):
    """Download installer to temp folder."""
    try:
        temp_dir = tempfile.gettempdir()
        url_path = installer_url.split('?')[0]
        filename = url_path.split('/')[-1] or f"av_installer_{platform_name}"
        local_path = os.path.join(temp_dir, filename)

        logger.info(f"[AV] Downloading installer from: {installer_url}")
        response = requests.get(installer_url, stream=True, timeout=300)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0 and downloaded % (5 * 1024 * 1024) == 0:
                        progress = (downloaded / total_size) * 100
                        logger.info(f"[AV] Download progress: {progress:.1f}%")
        logger.info(f"[AV] Download complete: {local_path} ({downloaded} bytes)")
        return True, local_path
    except Exception as e:
        logger.error(f"[AV] Download failed: {e}")
        return False, str(e)


def run_silent_installer(installer_path, platform_name="linux"):
    """Run installer silently on Linux."""
    try:
        if not os.path.exists(installer_path):
            return False, f"Installer not found: {installer_path}"

        ext = os.path.splitext(installer_path)[1].lower()
        logger.info(f"[AV] Running installer: {installer_path} (type: {ext})")

        if ext == '.deb':
            logger.info(f"[AV-Linux] Installing .deb package: {installer_path}")
            cmd = ["dpkg", "-i", installer_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                logger.warning("[AV-Linux] dpkg failed, trying apt --fix-broken-install")
                subprocess.run(_scoped(["apt-get", "install", "-f", "-y"]), capture_output=True, text=True, timeout=300)
        elif ext == '.rpm':
            logger.info(f"[AV-Linux] Installing .rpm package: {installer_path}")
            cmd = ["rpm", "-ivh", installer_path, "--force"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        elif ext == '.sh':
            logger.info(f"[AV-Linux] Running shell installer: {installer_path}")
            os.chmod(installer_path, 0o755)
            cmd = ["bash", installer_path, "-s", "-q"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        else:
            # Generic: make executable and run
            os.chmod(installer_path, os.stat(installer_path).st_mode | 0o755)
            cmd = [installer_path, "--silent", "-silent", "/S", "/quiet"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        logger.info(f"[AV-Linux] Installer exit code: {result.returncode}")
        if result.stdout:
            logger.info(f"[AV-Linux] stdout: {result.stdout[:500]}")
        if result.stderr:
            logger.warning(f"[AV-Linux] stderr: {result.stderr[:500]}")

        if result.returncode in (0, 3010):
            return True, f"Installation completed (exit code: {result.returncode})"
        else:
            return False, f"Installation failed (exit code: {result.returncode})"

    except Exception as e:
        logger.error(f"[AV-Linux] Installation error: {e}")
        return False, str(e)


def report_antivirus_status(serial_number, deployment_id, status, av_version=None, error_message=None, agent_log=None):
    """Report antivirus deployment status back to Odoo."""
    try:
        payload = {"serial_number": serial_number, "deployment_id": deployment_id, "status": status}
        if av_version:
            payload["av_version"] = av_version
        if error_message:
            payload["error_message"] = error_message
        if agent_log:
            payload["agent_log"] = agent_log
        url = f"{ANTIVIRUS_BASE_URL}/status"
        success, response = send_with_retry(url, payload, max_retries=3, timeout=15)
        if success:
            logger.info(f"[AV] Status reported: {status}")
        else:
            logger.warning(f"[AV] Failed to report status: {status}")
        return success
    except Exception as e:
        logger.error(f"[AV] Error reporting status: {e}")
        return False


def antivirus_sync_loop():
    """Background thread for antivirus deployment polling."""
    logger.info(f"[AV] Antivirus sync loop started (interval: {ANTIVIRUS_POLL_INTERVAL}s)")
    time.sleep(15)

    serial = get_serial_number()

    while True:
        try:
            logger.info("[AV] Polling for antivirus deployment command...")
            url = f"{ANTIVIRUS_BASE_URL}/command"
            response = requests.get(url, params={"serial_number": serial}, timeout=15, headers=ODOO_HEADERS)

            if response.status_code != 200:
                logger.warning(f"[AV] Poll failed: HTTP {response.status_code}")
                time.sleep(ANTIVIRUS_POLL_INTERVAL)
                continue

            data = response.json()
            command = data.get("command", "none")

            if command != "install":
                logger.info(f"[AV] No deployment pending: {data.get('message', 'none')}")
                time.sleep(ANTIVIRUS_POLL_INTERVAL)
                continue

            deployment_id = data.get("deployment_id")
            installer_url = data.get("installer_url")
            platform_name = data.get("platform", "linux")
            product = data.get("product", "antivirus")

            logger.info(f"[AV] Deploy command! deployment_id={deployment_id}, product={product}")

            if not installer_url:
                report_antivirus_status(serial, deployment_id, "failed", error_message="No installer URL")
                time.sleep(ANTIVIRUS_POLL_INTERVAL)
                continue

            report_antivirus_status(serial, deployment_id, "downloading")
            dl_success, installer_path = download_installer(installer_url, platform_name)

            if not dl_success:
                report_antivirus_status(serial, deployment_id, "failed", error_message=f"Download failed: {installer_path}")
                time.sleep(ANTIVIRUS_POLL_INTERVAL)
                continue

            report_antivirus_status(serial, deployment_id, "installing")
            install_success, install_msg = run_silent_installer(installer_path, platform_name)
            logger.info(f"[AV] Install result: {install_success} — {install_msg}")

            try:
                os.remove(installer_path)
            except Exception:
                pass

            logger.info("[AV] Waiting 10 seconds before checking AV status...")
            time.sleep(10)

            av_detected, av_product = check_antivirus_installed()

            if install_success and av_detected:
                report_antivirus_status(serial, deployment_id, "installed", av_version=av_product,
                                        agent_log=f"Installed: {install_msg}. Detected: {av_product}")
            elif install_success and not av_detected:
                report_antivirus_status(serial, deployment_id, "installed", av_version=product,
                                        agent_log=f"Installer succeeded but AV not yet detected. {install_msg}")
            else:
                report_antivirus_status(serial, deployment_id, "failed", error_message=install_msg,
                                        agent_log=f"Install failed: {install_msg}")

        except requests.exceptions.ConnectionError as e:
            logger.warning(f"[AV] Connection error: {e}")
        except requests.exceptions.Timeout:
            logger.warning("[AV] Timeout polling antivirus command")
        except Exception as e:
            logger.error(f"[AV] Error in antivirus sync loop: {e}")

        time.sleep(ANTIVIRUS_POLL_INTERVAL)


# ============================================================================
# SOFTWARE DEPLOYMENT FUNCTIONS
# ============================================================================

def get_pending_software_deployments(serial_number):
    """Poll Odoo for pending software deployments."""
    logger.info("[SOFTWARE] Polling for pending deployments...")
    try:
        url = f"{SOFTWARE_BASE_URL}/poll"
        response = requests.get(url, json={'serial_number': serial_number}, timeout=15, headers=ODOO_HEADERS)
        if response.status_code == 200:
            data = response.json()
            deployments = data.get('deployments', [])
            logger.info(f"[SOFTWARE] Found {len(deployments)} pending deployment(s)")
            return deployments
        else:
            logger.warning(f"[SOFTWARE] Poll failed: HTTP {response.status_code}")
            return []
    except Exception as e:
        logger.warning(f"[SOFTWARE] Error polling deployments: {e}")
        return []


def download_software_installer(installer_url, filename):
    """Download software installer to temp folder."""
    try:
        temp_dir = tempfile.gettempdir()
        local_path = os.path.join(temp_dir, filename)
        logger.info(f"[SOFTWARE] Downloading: {filename}")
        response = requests.get(installer_url, stream=True, timeout=300)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0 and downloaded % (5 * 1024 * 1024) == 0:
                        progress = (downloaded / total_size) * 100
                        logger.info(f"[SOFTWARE] Download progress: {progress:.1f}%")
        logger.info(f"[SOFTWARE] Download complete: {local_path} ({downloaded} bytes)")
        return True, local_path
    except Exception as e:
        logger.error(f"[SOFTWARE] Download failed: {e}")
        return False, str(e)


def install_software(installer_path, silent_flags):
    """Run software installer silently on Linux."""
    try:
        if not os.path.exists(installer_path):
            return False, f"Installer not found: {installer_path}"

        ext = os.path.splitext(installer_path)[1].lower()
        logger.info(f"[SOFTWARE] Installing: {installer_path} (type: {ext})")

        if ext == '.deb':
            cmd = ['dpkg', '-i', installer_path]
        elif ext == '.rpm':
            cmd = ['rpm', '-ivh', installer_path, '--force']
        elif ext == '.sh':
            os.chmod(installer_path, 0o755)
            flags = silent_flags.split() if silent_flags else ['-s', '-q']
            cmd = ['bash', installer_path] + flags
        else:
            os.chmod(installer_path, os.stat(installer_path).st_mode | 0o755)
            flags = silent_flags.split() if silent_flags else ['--silent']
            cmd = [installer_path] + flags

        logger.info(f"[SOFTWARE] Command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        logger.info(f"[SOFTWARE] Exit code: {result.returncode}")

        # Fix deps if deb failed
        if ext == '.deb' and result.returncode != 0:
            subprocess.run(_scoped(['apt-get', 'install', '-f', '-y']), capture_output=True, text=True, timeout=300)

        if result.returncode in (0, 3010):
            return True, f"Installation completed (exit code: {result.returncode})"
        else:
            return False, f"Installation failed (exit code: {result.returncode})"

    except subprocess.TimeoutExpired:
        return False, "Installation timed out after 600 seconds"
    except Exception as e:
        logger.error(f"[SOFTWARE] Installation error: {e}")
        return False, str(e)


def verify_software_installed(software_name):
    """Check if software appears in installed packages."""
    try:
        installed_apps_json = get_installed_apps()
        installed_apps = json.loads(installed_apps_json)
        for app in installed_apps:
            if software_name.lower() in app.get('name', '').lower():
                logger.info(f"[SOFTWARE] Verified: {software_name} found")
                return True
        logger.warning(f"[SOFTWARE] Not found: {software_name}")
        return False
    except Exception as e:
        logger.error(f"[SOFTWARE] Verification error: {e}")
        return False


def report_software_deployment_status(serial, deployment_id, status, error_message=None, agent_log=None):
    """Report deployment status to Odoo."""
    try:
        payload = {"serial_number": serial, "deployment_id": deployment_id, "status": status}
        if error_message:
            payload["error_message"] = error_message
        if agent_log:
            payload["agent_log"] = agent_log
        url = f"{SOFTWARE_BASE_URL}/report"
        success, response = send_with_retry(url, payload, max_retries=3, timeout=15)
        if success:
            logger.info(f"[SOFTWARE] Status reported: {status}")
        else:
            logger.warning("[SOFTWARE] Failed to report status")
    except Exception as e:
        logger.error(f"[SOFTWARE] Error reporting status: {e}")


def software_deployment_sync_loop():
    """Background thread for software deployment polling."""
    logger.info(f"[SOFTWARE] Deployment sync started (interval: {SOFTWARE_SYNC_INTERVAL}s)")
    time.sleep(30)

    serial = get_serial_number()

    while True:
        try:
            deployments = get_pending_software_deployments(serial)
            for dep in deployments:
                deployment_id = dep['deployment_id']
                software_name = dep['software_name']
                software_version = dep['software_version']
                installer_url = dep['installer_url']
                installer_filename = dep['installer_filename']
                silent_flags = dep['silent_flags']

                logger.info(f"[SOFTWARE] Processing: {software_name} {software_version}")

                report_software_deployment_status(serial, deployment_id, "downloading")
                success, installer_path = download_software_installer(installer_url, installer_filename)

                if not success:
                    report_software_deployment_status(serial, deployment_id, "failed", error_message=f"Download failed: {installer_path}")
                    continue

                report_software_deployment_status(serial, deployment_id, "installing")
                success, install_msg = install_software(installer_path, silent_flags)

                try:
                    os.remove(installer_path)
                except Exception:
                    pass

                time.sleep(10)
                is_installed = verify_software_installed(software_name)

                if success and is_installed:
                    report_software_deployment_status(serial, deployment_id, "installed",
                                                      agent_log=f"Downloaded {installer_filename}, installed, verified")
                elif success and not is_installed:
                    report_software_deployment_status(serial, deployment_id, "installed",
                                                      agent_log=f"Installer succeeded but not yet detected. {install_msg}")
                else:
                    report_software_deployment_status(serial, deployment_id, "failed", error_message=install_msg)

        except Exception as e:
            logger.error(f"[SOFTWARE] Error in sync loop: {e}")
        time.sleep(SOFTWARE_SYNC_INTERVAL)


# ============================================================================
# APP DEPLOYMENT SYNC (package-manager-based)
# ============================================================================

def get_pending_app_deployments(serial_number):
    """Poll Odoo for pending package-manager app deployments."""
    logger.info("[APP DEPLOY] Polling for pending deployments...")
    try:
        url = f"{APP_DEPLOY_BASE_URL}/poll"
        response = requests.get(url, params={'serial_number': serial_number}, timeout=15, headers=ODOO_HEADERS)
        if response.status_code == 200:
            data = response.json()
            deployments = data.get('deployments', [])
            logger.info(f"[APP DEPLOY] Found {len(deployments)} pending deployment(s)")
            return deployments
        else:
            logger.warning(f"[APP DEPLOY] Poll failed: HTTP {response.status_code}")
            return []
    except Exception as e:
        logger.warning(f"[APP DEPLOY] Error polling: {e}")
        return []


def report_app_deployment_status(deployment_id, status, error_message=None):
    """Report app deployment status back to Odoo."""
    try:
        payload = {'deployment_id': deployment_id, 'status': status}
        if error_message:
            payload['error_message'] = error_message
        url = f"{APP_DEPLOY_BASE_URL}/deployment_status"
        success, _ = send_with_retry(url, payload, max_retries=3, timeout=15)
        if success:
            logger.info(f"[APP DEPLOY] Status reported: {status} for deployment {deployment_id}")
        else:
            logger.warning(f"[APP DEPLOY] Failed to report status for deployment {deployment_id}")
    except Exception as e:
        logger.error(f"[APP DEPLOY] Error reporting status: {e}")


def run_install_command(command, timeout=300):
    """Execute the install/uninstall command via shell."""
    logger.info(f"[APP DEPLOY] Running: {command}")
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        output = (result.stdout or '') + (result.stderr or '')
        success = result.returncode == 0
        logger.info(f"[APP DEPLOY] Command exit code: {result.returncode}")
        if not success:
            logger.warning(f"[APP DEPLOY] Command output: {output[:500]}")
        return success, output.strip()
    except subprocess.TimeoutExpired:
        msg = f"Command timed out after {timeout}s: {command}"
        logger.error(f"[APP DEPLOY] {msg}")
        return False, msg
    except Exception as e:
        msg = f"Command execution error: {e}"
        logger.error(f"[APP DEPLOY] {msg}")
        return False, msg


def run_url_installer(dep):
    """Download an installer from a URL and execute it natively."""
    import urllib.request
    import shutil

    url = dep.get('installer_url', '')
    itype = dep.get('installer_type', 'deb').lower()
    args = dep.get('installer_args', '') or ''
    app_name = dep.get('application_name', 'app')

    if not url:
        return False, 'No installer URL provided'

    from urllib.parse import urlparse as _urlparse
    path = _urlparse(url).path
    fname = path.rstrip('/').split('/')[-1] or f'installer_{itype}'
    tmp_path = os.path.join(tempfile.gettempdir(), fname)

    logger.info(f"[URL INSTALL] Downloading {url} → {tmp_path}")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'AssetAgent/1.5'})
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp_path, 'wb') as f:
            shutil.copyfileobj(resp, f)
        logger.info(f"[URL INSTALL] Download complete: {os.path.getsize(tmp_path)} bytes")
    except Exception as e:
        return False, f"Download failed: {e}"

    try:
        if itype == 'deb':
            result = subprocess.run(_scoped(['dpkg', '-i', tmp_path]), capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                subprocess.run(_scoped(['apt-get', 'install', '-f', '-y']), capture_output=True, text=True, timeout=300)
                result = subprocess.run(_scoped(['dpkg', '-i', tmp_path]), capture_output=True, text=True, timeout=300)

        elif itype == 'rpm':
            result = subprocess.run(['rpm', '-ivh', tmp_path], capture_output=True, text=True, timeout=300)

        elif itype == 'sh':
            os.chmod(tmp_path, 0o755)
            result = subprocess.run(['bash', tmp_path], capture_output=True, text=True, timeout=600)

        elif itype == 'appimage':
            os.chmod(tmp_path, 0o755)
            # Move to /opt or ~/Applications
            dest_dir = os.path.expanduser("~/Applications")
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, fname)
            shutil.move(tmp_path, dest_path)
            result = type('R', (), {'returncode': 0, 'stdout': f'AppImage ready at {dest_path}', 'stderr': ''})()

        elif itype == 'zip':
            import zipfile
            dest = tmp_path.replace('.zip', '').replace('.ZIP', '')
            os.makedirs(dest, exist_ok=True)
            with zipfile.ZipFile(tmp_path, 'r') as zf:
                zf.extractall(dest)
            result = type('R', (), {'returncode': 0, 'stdout': f'Extracted to {dest}', 'stderr': ''})()

        elif itype == 'tar.gz' or itype == 'tgz':
            dest = tmp_path.replace('.tar.gz', '').replace('.tgz', '')
            os.makedirs(dest, exist_ok=True)
            result = subprocess.run(['tar', '-xzf', tmp_path, '-C', dest], capture_output=True, text=True, timeout=300)

        else:
            return False, f"Unsupported installer type: {itype}"

        output = (getattr(result, 'stdout', '') or '') + (getattr(result, 'stderr', '') or '')
        success = result.returncode == 0
        if success:
            logger.info(f"[URL INSTALL] SUCCESS: {app_name}")
        else:
            logger.warning(f"[URL INSTALL] FAILED rc={result.returncode}: {output[:300]}")
        return success, output.strip()

    except subprocess.TimeoutExpired:
        return False, f"Installer timed out for {app_name}"
    except Exception as e:
        return False, f"Installer error: {e}"
    finally:
        try:
            if itype != 'appimage' and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def app_deployment_sync_loop():
    """Background thread for package-manager app deployment polling."""
    logger.info(f"[APP DEPLOY] Sync loop started (interval: {APP_DEPLOY_POLL_INTERVAL}s)")
    time.sleep(15)

    serial = get_serial_number()

    while True:
        try:
            deployments = get_pending_app_deployments(serial)

            for dep in deployments:
                deployment_id = dep.get('deployment_id')
                app_name = dep.get('application_name', 'Unknown')
                install_command = dep.get('install_command', '')
                package_manager = dep.get('package_manager', '')
                application_source = dep.get('application_source', 'preset')
                action_type = dep.get('action_type', 'install')
                installer_url = dep.get('installer_url', '')

                logger.info(
                    f"[APP DEPLOY] Processing: {app_name} | "
                    f"source={application_source} action={action_type} pm={package_manager}"
                )

                report_app_deployment_status(deployment_id, 'in_progress')

                if application_source == 'url' and action_type == 'install' and installer_url:
                    logger.info(f"[APP DEPLOY] Using native URL installer for {app_name}")
                    success, output = run_url_installer(dep)
                elif install_command:
                    timeout = 900 if application_source == 'url' else 300
                    success, output = run_install_command(install_command, timeout=timeout)
                else:
                    logger.warning(f"[APP DEPLOY] No command for deployment {deployment_id}")
                    report_app_deployment_status(deployment_id, 'failed', 'No command provided')
                    continue

                if success:
                    logger.info(f"[APP DEPLOY] SUCCESS: {app_name}")
                    report_app_deployment_status(deployment_id, 'success')
                else:
                    logger.error(f"[APP DEPLOY] FAILED: {app_name} — {output[:200]}")
                    report_app_deployment_status(deployment_id, 'failed', output[:500])

        except Exception as e:
            logger.error(f"[APP DEPLOY] Error in sync loop: {e}")

        time.sleep(APP_DEPLOY_POLL_INTERVAL)


# ============================================================================
# LIVE FILE BROWSER HTTP API SERVER
# ============================================================================

def list_directory(path):
    """List ONLY one level. Uses os.scandir. Restricted to safe folders."""
    home = os.path.expanduser("~")
    safe_roots = [
        os.path.join(home, "Desktop"),
        os.path.join(home, "Documents"),
        os.path.join(home, "Downloads")
    ]

    if not path or path.lower() == "desktop":
        path = safe_roots[0]
    elif path.lower() == "documents":
        path = safe_roots[1]
    elif path.lower() == "downloads":
        path = safe_roots[2]

    abs_path = os.path.abspath(path)

    is_safe = False
    for root in safe_roots:
        if abs_path.startswith(root):
            is_safe = True
            break

    if not is_safe:
        return {"error": "Access denied. Only Desktop, Documents, and Downloads are allowed.", "status": 403}
    if not os.path.exists(abs_path):
        return {"error": "Path does not exist", "status": 404}
    if not os.path.isdir(abs_path):
        return {"error": "Path is not a directory", "status": 400}

    try:
        items = []
        with os.scandir(abs_path) as entries:
            for entry in entries:
                try:
                    stat = entry.stat()
                    items.append({
                        "name": entry.name,
                        "full_path": entry.path,
                        "type": "folder" if entry.is_dir() else "file",
                        "size_kb": round(stat.st_size / 1024, 2) if entry.is_file() else 0,
                        "last_modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
                    })
                except Exception:
                    continue
        return {"path": abs_path, "files": items, "status": 200}
    except PermissionError:
        return {"error": "Permission denied", "status": 403}
    except Exception as e:
        return {"error": str(e), "status": 500}


class FileBrowserAPI(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/browse':
            qs = parse_qs(parsed.query)
            if 'path' in qs:
                target_path = qs['path'][0]
                result = list_directory(target_path)
                self.send_response(result["status"])
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))
            else:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Missing 'path' parameter"}).encode('utf-8'))
        else:
            self.send_response(404)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Endpoint not found"}).encode('utf-8'))


def start_file_browser():
    """Start the local file browsing API in a daemon thread."""
    global FILE_BROWSER_PORT
    port = 8000
    while port < 8010:
        try:
            # Bind to loopback ONLY.
            #
            # This was '0.0.0.0', which exposed an unauthenticated file
            # browsing API to the entire network - anyone on the LAN could
            # read this machine's Desktop/Documents/Downloads by hitting
            # http://<machine-ip>:8000/browse. There is no auth check in
            # FileBrowserAPI at all.
            #
            # Nothing legitimately needs remote access to it: Odoo receives
            # file data through the agent's outbound reports, not by
            # connecting inbound. If a genuine remote-browse feature is
            # wanted later it needs a token check before the bind is
            # widened again.
            server = HTTPServer(('127.0.0.1', port), FileBrowserAPI)
            logger.info(f"File Browser API started on http://0.0.0.0:{port}/browse")
            FILE_BROWSER_PORT = port
            server.serve_forever()
            break
        except OSError:
            port += 1
    if port >= 8010:
        logger.error("Failed to start File Browser API: No available ports")


# ============================================================================
# DATA COLLECTION FUNCTIONS
# ============================================================================

def collect_static_data():
    """Collect static system information"""
    try:
        battery = psutil.sensors_battery()
        battery_percentage = round(battery.percent, 2) if battery else 0
        _, battery_capacity_mah = get_battery_capacity()

        av_info = get_antivirus_info()

        payload = {
            "serial_number": get_serial_number(),
            "hostname": socket.gethostname(),
            "device_name": get_device_model(),
            "processor": get_processor_info(),
            "os_type": platform.architecture()[0],
            "os_name": get_os_version(),
            "ram_size": round(psutil.virtual_memory().total / (1024 ** 3), 2),
            "rom_size": get_total_storage_size(),
            "disk_type": get_disk_type(),
            "graphics_card_raw": get_graphics_card(),
            "battery_capacity": battery_capacity_mah,
            "battery_percentage": battery_percentage,
            "storage_volumes": get_storage_volumes(),
            "installed_apps": get_installed_apps(),
            "uptime": get_uptime(),
            "agent_version": AGENT_VERSION,
            "local_ip": get_local_ip(),
            "mac_address": get_mac_address(),
            "chassis_type": get_chassis_type(),
            "file_browser_port": FILE_BROWSER_PORT,
            # Antivirus information
            "antivirus_installed": av_info["antivirus_installed"],
            "antivirus_product": av_info["antivirus_product"],
            "antivirus_version": av_info["antivirus_version"],
            "antivirus_running": av_info["antivirus_running"],
        }

        location_data = get_location_data()
        payload.update(location_data)

        # Distro release + whether a release upgrade is being offered.
        # Reads a cache written by the os_release loop, so this never blocks
        # the sync on do-release-upgrade's network call.
        if OS_RELEASE_AVAILABLE:
            try:
                payload.update(os_release_mod.get_os_upgrade_payload())
            except Exception as os_exc:
                logger.error(f"OS release payload failed: {os_exc}")

        return payload

    except Exception as e:
        logger.error(f"Error collecting static data: {e}", exc_info=True)
        payload = {
            "serial_number": get_serial_number(),
            "hostname": socket.gethostname(),
            "device_name": "Unknown",
            "processor": "Unknown",
            "os_type": "64bit",
            "os_name": "Linux",
            "ram_size": 0,
            "rom_size": 0,
            "disk_type": "Unknown",
            "graphics_card_raw": "Unknown",
            "battery_capacity": 0,
            "battery_percentage": 0,
            "storage_volumes": "[]",
            "installed_apps": "[]",
            "uptime": "Unknown",
            "agent_version": AGENT_VERSION,
            "local_ip": get_local_ip(),
            "mac_address": get_mac_address(),
            "file_browser_port": FILE_BROWSER_PORT,
            "antivirus_installed": False,
            "antivirus_product": "Error",
            "antivirus_version": "unknown",
            "antivirus_running": False,
        }
        location_data = get_location_data()
        payload.update(location_data)
        return payload


def collect_live_data():
    """Collect live system metrics"""
    serial_number = get_serial_number()
    hostname = socket.gethostname()
    current_heartbeat = datetime.now(timezone.utc).isoformat()
    try:
        cpu_usage = round(psutil.cpu_percent(interval=0.5), 2)
        ram = psutil.virtual_memory()
        ram_usage = round(ram.percent, 2)
        disk = psutil.disk_usage('/')
        disk_usage = round(disk.percent, 2)
        upload_mbps, download_mbps = network_monitor.get_network_usage()
        battery = psutil.sensors_battery()
        battery_percentage = round(battery.percent, 2) if battery else 0
        return {
            "serial_number": serial_number,
            "hostname": hostname,
            "cpu_usage_percent": cpu_usage,
            "ram_usage_percent": ram_usage,
            "disk_usage_percent": disk_usage,
            "network_upload_mbps": upload_mbps,
            "network_download_mbps": download_mbps,
            "battery_percentage": battery_percentage,
            "uptime": get_uptime(),
            "heartbeat": current_heartbeat,
            "agent_version": AGENT_VERSION,
            "local_ip": get_local_ip(),
            "file_browser_port": FILE_BROWSER_PORT,
        }
    except Exception as e:
        logger.warning(f"Error collecting live data: {e}")
        return {
            "serial_number": serial_number,
            "hostname": hostname,
            "cpu_usage_percent": 0,
            "ram_usage_percent": 0,
            "disk_usage_percent": 0,
            "network_upload_mbps": 0,
            "network_download_mbps": 0,
            "battery_percentage": 0,
            "uptime": get_uptime(),
            "heartbeat": current_heartbeat,
            "agent_version": AGENT_VERSION,
            "local_ip": get_local_ip(),
            "file_browser_port": FILE_BROWSER_PORT,
        }


# ============================================================================
# MAIN FUNCTION
# ============================================================================

def main():
    """Main entry point"""
    logger.info("=" * 60)
    logger.info(f"Ubuntu Asset Agent v{AGENT_VERSION} Starting")
    logger.info("=" * 60)

    logger.info("Running in Headless Mode")
    logger.info("=" * 60)

    # Start auto-update checker
    threading.Thread(target=update_checker_thread, daemon=True).start()
    logger.info(f"Auto-update checker started (checks every {UPDATE_CHECK_INTERVAL}s)")

    # Start OS Update sync (Linux equivalent of Windows Update)
    threading.Thread(target=os_update_sync_loop, daemon=True).start()
    logger.info(f"OS Update sync started (interval: {OS_UPDATE_SYNC_INTERVAL}s)")

    # Start OS Release sync: reports the current distro release and whether a
    # release upgrade (24.04 -> 26.04) is being offered, and runs one when an
    # admin has approved it in Odoo. Distinct from the OS Update sync above,
    # which handles individual apt packages.
    if OS_RELEASE_AVAILABLE:
        threading.Thread(
            target=os_release_mod.os_release_loop,
            args=(SERVER_URL, get_serial_number),
            daemon=True,
        ).start()
        logger.info(
            f"[OS RELEASE] sync started (poll: {os_release_mod.POLL_INTERVAL_SECONDS}s, "
            f"availability check: {os_release_mod.CHECK_INTERVAL_SECONDS}s)")
    else:
        logger.warning(
            f"[OS RELEASE] module unavailable, release upgrades disabled: "
            f"{_OS_RELEASE_IMPORT_ERROR}")

    # Start File Access Policy sync
    threading.Thread(target=file_access_sync_loop, daemon=True).start()
    logger.info(f"[FILE ACCESS] Policy sync started (interval: {FILE_ACCESS_SYNC_INTERVAL}s)")

    # Start Security Audit: login/logout, failed logins, account/password/
    # sudo changes - see security_audit.py's own docstring for the full list.
    if SECURITY_AUDIT_AVAILABLE:
        threading.Thread(
            target=security_audit_mod.security_audit_loop,
            args=(SERVER_URL, get_serial_number, ODOO_DB_NAME),
            daemon=True,
        ).start()
        logger.info(
            f"[SECURITY AUDIT] sync started (poll: "
            f"{security_audit_mod.POLL_INTERVAL_SECONDS}s)")
    else:
        logger.warning(
            f"[SECURITY AUDIT] module unavailable, security event logging "
            f"disabled: {_SECURITY_AUDIT_IMPORT_ERROR}")

    # Start Antivirus deployment sync
    threading.Thread(target=antivirus_sync_loop, daemon=True).start()
    logger.info(f"[AV] Antivirus deployment sync started (interval: {ANTIVIRUS_POLL_INTERVAL}s)")

    # Start Software Deployment sync
    threading.Thread(target=software_deployment_sync_loop, daemon=True).start()
    logger.info(f"[SOFTWARE] Deployment sync started (interval: {SOFTWARE_SYNC_INTERVAL}s)")

    # Start Application Uninstall sync
    threading.Thread(target=app_uninstall_sync_loop, daemon=True).start()
    logger.info(f"[APP UNINSTALL] Sync started (interval: {APP_UNINSTALL_SYNC_INTERVAL}s)")

    # Start App Deployment sync (package-manager-based)
    threading.Thread(target=app_deployment_sync_loop, daemon=True).start()
    logger.info(f"[APP DEPLOY] App deployment sync started (interval: {APP_DEPLOY_POLL_INTERVAL}s)")

    # Start File Browser API
    threading.Thread(target=start_file_browser, daemon=True).start()


    # ---- Agent extensions -------------------------------------------------
    # App updates, telemetry and listening ports. Started last so a slow
    # first telemetry collection cannot delay the heartbeat threads above.
    if EXTENSIONS_AVAILABLE:
        try:
            start_all_extensions(SERVER_URL, get_serial_number())
            logger.info("[EXTENSIONS] App updates, telemetry and port "
                        "reporting started")
        except Exception as _ext_exc:
            logger.error("[EXTENSIONS] Failed to start: %s", _ext_exc)
    else:
        logger.warning("[EXTENSIONS] agent_extensions not importable (%s) — "
                       "app updates, telemetry and ports are disabled",
                       _EXTENSIONS_IMPORT_ERROR)

    # Initial system detection
    logger.info("Performing initial system detection...")
    serial = get_serial_number()
    hostname = socket.gethostname()
    model = get_device_model()
    logger.info(f"Device: {model}")
    logger.info(f"Hostname: {hostname}")
    logger.info(f"Serial: {serial}")
    logger.info(f"Version: {AGENT_VERSION}")
    logger.info(f"OS: {get_os_version()}")
    logger.info(f"Uptime: {get_uptime()}")
    logger.info(f"Local IP: {get_local_ip()}")
    logger.info(f"MAC Address: {get_mac_address()}")

    # Fetch and log location
    logger.info("Fetching location data...")
    location = get_location_data()
    logger.info(f"Location: {location.get('location_city', 'Unknown')}, {location.get('location_region', 'Unknown')}, {location.get('location_country', 'Unknown')}")
    logger.info(f"Public IP: {location.get('public_ip', 'Unknown')}")
    logger.info(f"Location Source: {location.get('location_source', 'Unknown')}")

    # Initial static sync
    logger.info("Performing initial static sync...")
    try:
        payload = collect_static_data()
        success, response = send_with_retry(ODOO_API_URL, payload, max_retries=3, timeout=30)
        if success:
            logger.info("OK Initial static sync completed successfully")
        else:
            logger.warning("Initial static sync failed, will retry in next interval")
    except Exception as e:
        logger.error(f"Initial static sync error: {e}")

    # Initial live sync (heartbeat)
    logger.info("Performing initial live sync (heartbeat)...")
    try:
        payload = collect_live_data()
        success, response = send_with_retry(f"{ODOO_API_URL}/live", payload, max_retries=3, timeout=10)
        if success:
            logger.info("OK Initial heartbeat sent successfully")
        else:
            logger.warning("Initial heartbeat failed, will retry in next interval")
    except Exception as e:
        logger.error(f"Initial heartbeat error: {e}")

    # Start continuous monitoring loop
    logger.info(f"Starting continuous monitoring loop...")
    logger.info(f"Static sync interval:   {STATIC_SYNC_INTERVAL}s")
    logger.info(f"Live sync interval:     {LIVE_SYNC_INTERVAL}s")
    logger.info(f"OS Update sync interval: {OS_UPDATE_SYNC_INTERVAL}s")

    # Sync immediately on startup instead of waiting out a full interval.
    # The static payload is what carries OS version/codename and the release
    # upgrade status, so deferring it left a freshly installed agent showing
    # nothing in Odoo for 30 minutes - which reads as "the agent is broken"
    # to whoever just installed it. It also matters right after a reboot,
    # which is exactly when a completed release upgrade needs reporting.
    next_static_sync = time.monotonic()
    next_live_sync = time.monotonic()

    try:
        while True:
            current_time = time.monotonic()

            # Static sync
            if current_time >= next_static_sync:
                if _static_sync_lock.acquire(blocking=False):
                    try:
                        logger.info("Performing static sync...")
                        try:
                            payload = collect_static_data()
                            success, response = send_with_retry(ODOO_API_URL, payload, max_retries=3, timeout=30)
                            if success:
                                logger.info("OK Static sync completed")
                            else:
                                logger.warning("Static sync failed, will retry")
                        except Exception as e:
                            logger.error(f"Static sync error: {e}", exc_info=True)
                        finally:
                            next_static_sync = time.monotonic() + STATIC_SYNC_INTERVAL
                    finally:
                        _static_sync_lock.release()
                else:
                    logger.warning("Static sync already in progress, skipping")
                    next_static_sync = time.monotonic() + STATIC_SYNC_INTERVAL

            # Live sync
            if current_time >= next_live_sync:
                if _live_sync_lock.acquire(blocking=False):
                    try:
                        try:
                            payload = collect_live_data()
                            success, response = send_with_retry(f"{ODOO_API_URL}/live", payload, max_retries=2, timeout=10)
                            if not success:
                                logger.warning("Live sync failed, will retry")
                        except Exception as e:
                            logger.error(f"Live sync error: {e}", exc_info=True)
                        finally:
                            next_live_sync = time.monotonic() + LIVE_SYNC_INTERVAL
                    finally:
                        _live_sync_lock.release()
                else:
                    logger.warning("Live sync already in progress, skipping")
                    next_live_sync = time.monotonic() + LIVE_SYNC_INTERVAL

            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Received shutdown signal, stopping agent...")
    except Exception as e:
        logger.critical(f"Fatal error in main loop: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
