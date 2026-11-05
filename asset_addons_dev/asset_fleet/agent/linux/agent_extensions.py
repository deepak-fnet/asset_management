# -*- coding: utf-8 -*-
"""
agent_extensions.py — everything your agent is currently missing.

Add this next to agent.py (or paste it in wholesale) and call
start_all_extensions() from main(). It supplies four things:

  1. Application updates   — populates the Update Center's app list
  2. Telemetry             — populates the Agent Telemetry tab
  3. Listening ports       — populates the Vulnerability Report's port table
  4. Correct OS endpoint   — stops Linux packages landing in the Windows table

Design notes
------------
Every loop is defensive: a failure in any collector logs and continues rather
than killing the thread. Telemetry and port reporting are diagnostic — they
must never disturb the agent's primary job of reporting asset state.

All four run as daemon threads on independent intervals, so a slow telemetry
collection cannot delay a heartbeat.

Integration
-----------
    # near the top of agent.py
    from agent_extensions import start_all_extensions

    # at the end of main(), after your existing threads
    start_all_extensions(SERVER_URL, get_serial_number())

If you build a single-file agent with PyInstaller, paste the contents of this
file into agent.py instead of importing, and call start_all_extensions()
the same way.
"""

import glob
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone

import requests

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger(__name__)

# ── Intervals (seconds) ───────────────────────────────────────────────────
APP_UPDATE_CHECK_INTERVAL = 3600      # detect outdated apps hourly
APP_UPDATE_POLL_INTERVAL = 120        # check for upgrade orders every 2 min
# apt runs in its own scope, outside the agent service's memory limit
SCOPE = ('systemd-run --scope --quiet --collect -- '
         if hasattr(os, 'geteuid') and os.geteuid() == 0 and os.path.isdir('/run/systemd/system') else '')
TELEMETRY_INTERVAL = 900              # full telemetry every 15 min
TELEMETRY_SNAPSHOT_EVERY = 4          # keep history every 4th run (hourly)
PORTS_INTERVAL = 1800                 # listening ports every 30 min

IS_WINDOWS = platform.system() == 'Windows'
WIN_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if IS_WINDOWS else 0


def _run_full(cmd, timeout=20, shell=False):
    """Run a command and report properly.

    Returns (returncode, stdout, stderr). returncode is None if the command
    could not be executed at all.

    This exists because _run() below only returns stdout, which cannot
    distinguish "succeeded quietly" from "failed". Several tools -- snap
    being the obvious one -- write everything to stderr and leave stdout
    empty on success. Judging those by stdout alone reports every successful
    refresh as a failure.
    """
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            shell=shell, creationflags=WIN_FLAGS,
        )
        return (result.returncode,
                (result.stdout or '').strip(),
                (result.stderr or '').strip())
    except subprocess.TimeoutExpired:
        return None, '', f'timed out after {timeout}s'
    except FileNotFoundError:
        return None, '', 'command not found'
    except Exception as exc:
        return None, '', str(exc)


def _run(cmd, timeout=20, shell=False):
    """Run a command, return stdout or None.

    Convenience wrapper for collectors that only care about output. Do NOT
    use this to decide whether a command succeeded -- use _run_full().
    """
    _rc, out, _err = _run_full(cmd, timeout=timeout, shell=shell)
    return out or None


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as exc:
        logger.debug('Collector %s failed: %s', getattr(fn, '__name__', fn), exc)
        return default



def _detect_distro():
    """Distro name, with fallbacks. lsb_release is not always installed."""
    out = _run(['lsb_release', '-ds'])
    if out:
        return out.strip('"')

    try:
        with open('/etc/os-release') as fh:
            for line in fh:
                if line.startswith('PRETTY_NAME='):
                    return line.split('=', 1)[1].strip().strip('"')
    except Exception:
        pass

    for path in ('/etc/redhat-release', '/etc/debian_version'):
        try:
            with open(path) as fh:
                return fh.read().strip()
        except Exception:
            continue

    return f'{platform.system()} {platform.release()}'


def is_root():
    """True when running with the privileges the agent will actually have."""
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False        # Windows


def _human(n):
    if n is None:
        return None
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(n) < 1024.0:
            return f'{n:.1f} {unit}'
        n /= 1024.0
    return f'{n:.1f} PB'


# ══════════════════════════════════════════════════════════════════════════
# 1. APPLICATION UPDATES
# ══════════════════════════════════════════════════════════════════════════
def get_apt_upgrades():
    """Parse `apt list --upgradable`.

    Line format:
        firefox/noble-updates 132.0+build2-0ubuntu0.24.04.1 amd64
            [upgradable from: 131.0+build2-0ubuntu0.24.04.1]
    """
    _run(SCOPE.split() + ['apt-get', 'update', '-qq'], timeout=180)
    out = _run(['apt', 'list', '--upgradable'], timeout=60)
    if not out:
        return []

    updates = []
    for line in out.splitlines():
        if '/' not in line or 'upgradable from' not in line:
            continue
        try:
            pkg = line.split('/')[0].strip()
            parts = line.split()
            available = parts[1] if len(parts) > 1 else ''
            m = re.search(r'upgradable from:\s*([^\]]+)', line)
            installed = m.group(1).strip() if m else ''
            if not pkg or not available:
                continue
            updates.append({
                'app_name': pkg,
                'package_id': pkg,
                'installed_version': installed,
                'available_version': available,
            })
        except Exception:
            continue
    return updates


def _snap_installed_map():
    """Map snap name -> currently installed version, from `snap list`."""
    rc, out, _err = _run_full(['snap', 'list'], timeout=60)
    if rc != 0 or not out:
        return {}
    versions = {}
    for line in out.splitlines()[1:]:      # skip header
        parts = line.split()
        if len(parts) >= 2:
            versions[parts[0]] = parts[1]
    return versions


def get_snap_upgrades():
    """Parse `snap refresh --list`.

    That command reports only the AVAILABLE version, so installed versions
    are looked up separately with `snap list`. Without this the Update Center
    shows a blank "Installed" column, which makes it impossible to judge how
    far behind a snap actually is.
    """
    rc, out, err = _run_full(['snap', 'refresh', '--list'], timeout=60)
    combined = f'{out}\n{err}'.strip()

    if rc is None:
        return []                                  # snap not installed
    if 'all snaps up to date' in combined.lower():
        return []
    if not out:
        return []

    installed = _snap_installed_map()

    updates = []
    for line in out.splitlines()[1:]:              # skip header
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        updates.append({
            'app_name': f'{name} (Snap)',
            'package_id': name,
            'installed_version': installed.get(name, ''),
            'available_version': parts[1],
        })
    return updates


def get_winget_upgrades():
    """Parse `winget upgrade`, using header offsets for column positions."""
    out = _run(['winget', 'upgrade', '--include-unknown',
                '--accept-source-agreements'], timeout=180)
    if not out:
        return []

    lines = out.splitlines()
    sep_idx = next(
        (i for i, ln in enumerate(lines)
         if set(ln.strip()) == {'-'} and len(ln.strip()) > 10), None)
    if sep_idx is None or sep_idx == 0:
        return []

    header = lines[sep_idx - 1]
    try:
        col_id = header.index('Id')
        col_ver = header.index('Version')
        col_avail = header.index('Available')
    except ValueError:
        return []

    updates = []
    for line in lines[sep_idx + 1:]:
        if not line.strip() or line.strip().startswith('-'):
            continue
        if re.match(r'^\s*\d+\s+upgrade', line, re.IGNORECASE):
            continue
        try:
            name = line[:col_id].strip()
            pkg_id = line[col_id:col_ver].strip()
            installed = line[col_ver:col_avail].strip()
            tail = line[col_avail:].strip()
            available = tail.split()[0] if tail else ''
        except Exception:
            continue
        if not name or not available or available == 'Unknown':
            continue
        updates.append({
            'app_name': name,
            'package_id': pkg_id,
            'installed_version': installed,
            'available_version': available,
        })
    return updates


def get_brew_upgrades():
    out = _run(['brew', 'outdated', '--json=v2'], timeout=120)
    if not out:
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []

    updates = []
    for item in data.get('formulae', []) + data.get('casks', []):
        installed = item.get('installed_versions') or item.get('installed_version')
        if isinstance(installed, list):
            installed = installed[0] if installed else ''
        updates.append({
            'app_name': item.get('name', ''),
            'package_id': item.get('name', ''),
            'installed_version': installed or '',
            'available_version': item.get('current_version', ''),
        })
    return updates


def detect_app_updates():
    """Return (source, updates) for this platform.

    On Linux both apt and snap are checked, because a machine can have
    outdated packages in either.
    """
    system = platform.system()
    if system == 'Windows':
        return 'winget', _safe(get_winget_upgrades, [])
    if system == 'Darwin':
        return 'brew', _safe(get_brew_upgrades, [])

    updates = _safe(get_apt_upgrades, []) or []
    updates += _safe(get_snap_upgrades, []) or []
    return 'apt', updates


def report_app_updates(server_url, serial_number, timeout=60):
    """Detect outdated apps and POST them."""
    try:
        source, updates = detect_app_updates()
        resp = requests.post(
            f'{server_url}/api/asset/app_updates/report',
            json={'serial_number': serial_number,
                  'source': source,
                  'updates': updates},
            timeout=timeout,
        )
        if resp.status_code == 200:
            logger.info('[APP-UPDATE] Reported %s outdated app(s) via %s',
                        len(updates), source)
            return True
        logger.warning('[APP-UPDATE] HTTP %s', resp.status_code)
        return False
    except Exception as exc:
        logger.error('[APP-UPDATE] Report failed: %s', exc)
        return False


def _snap_installed_version(snap_name):
    """Current version of an installed snap, or ''."""
    rc, out, _err = _run_full(['snap', 'list', snap_name], timeout=30)
    if rc != 0 or not out:
        return ''
    lines = out.splitlines()
    if len(lines) < 2:
        return ''
    parts = lines[1].split()
    return parts[1] if len(parts) > 1 else ''


def _upgrade_snap(snap_name):
    """Refresh one snap. Returns (success, new_version, error).

    snap writes progress and most messages to stderr and leaves stdout empty
    on success, so success is judged by exit code, never by output.
    """
    rc, out, err = _run_full(['snap', 'refresh', snap_name], timeout=1800)
    combined = f'{out}\n{err}'.strip()
    lowered = combined.lower()

    if rc is None:
        return False, '', f'Could not run snap: {err}'

    if rc == 0:
        return True, _snap_installed_version(snap_name), ''

    # snap exits non-zero for "nothing to do", which is not a failure.
    if 'has no updates available' in lowered or 'is up to date' in lowered:
        return True, _snap_installed_version(snap_name), ''

    # Common, actionable failures worth naming explicitly.
    if 'has running apps' in lowered or 'has running hooks' in lowered:
        return False, '', (
            f'{snap_name} is currently running, so snap will not refresh it. '
            f'Close the application and retry, or the snap will auto-refresh '
            f'once it is closed. Detail: {combined[:300]}')

    if 'permission denied' in lowered or 'requires authentication' in lowered:
        return False, '', ('snap refresh needs root. The agent must run as '
                           'root (it does under systemd).')

    if 'cannot find' in lowered or 'not installed' in lowered:
        return False, '', f'snap "{snap_name}" is not installed'

    return False, '', f'snap refresh exit {rc}: {combined[:400]}'


def upgrade_one_app(package_id, app_name, source):
    """Perform a single upgrade. Returns (success, new_version, error).

    Success is determined by EXIT CODE, not by whether the tool printed
    anything to stdout. snap in particular writes to stderr and produces no
    stdout on success -- judging by stdout reported every successful refresh
    as a failure.
    """
    try:
        # ── Windows ───────────────────────────────────────────────────────
        if source == 'winget':
            target = package_id or app_name
            rc, out, err = _run_full(
                ['winget', 'upgrade', '--id', target, '--silent',
                 '--accept-package-agreements', '--accept-source-agreements'],
                timeout=1800)
            combined = f'{out}\n{err}'.strip()
            if rc is None:
                return False, '', f'Could not run winget: {err}'
            if rc == 0:
                return True, '', ''
            lowered = combined.lower()
            if 'no applicable update' in lowered or 'no available upgrade' in lowered:
                return True, '', ''
            return False, '', f'winget exit {rc}: {combined[:400]}'

        # ── Linux ─────────────────────────────────────────────────────────
        if source == 'apt':
            if app_name.endswith('(Snap)'):
                snap_name = (package_id or
                             app_name.replace('(Snap)', '')).strip()
                return _upgrade_snap(snap_name)

            rc, out, err = _run_full(
                f'DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a NEEDRESTART_SUSPEND=1 '
                f'{SCOPE}apt-get install -y -o Dpkg::Options::=--force-confdef '
                f'-o Dpkg::Options::=--force-confold '
                f'--only-upgrade {package_id}',
                timeout=1800, shell=True)
            combined = f'{out}\n{err}'.strip()

            if rc is None:
                return False, '', f'Could not run apt-get: {err}'
            if rc != 0:
                lowered = combined.lower()
                if 'could not get lock' in lowered or 'is another process' in lowered:
                    return False, '', ('apt is locked by another process '
                                       '(unattended-upgrades or a manual '
                                       'apt run). Retry shortly.')
                return False, '', f'apt-get exit {rc}: {combined[:400]}'

            ver = _run(['dpkg-query', '-W', '-f=${Version}', package_id]) or ''
            return True, ver.strip(), ''

        # ── macOS ─────────────────────────────────────────────────────────
        if source == 'brew':
            rc, out, err = _run_full(['brew', 'upgrade', package_id],
                                     timeout=1800)
            combined = f'{out}\n{err}'.strip()
            if rc is None:
                return False, '', f'Could not run brew: {err}'
            if rc == 0:
                return True, '', ''
            if 'already installed' in combined.lower():
                return True, '', ''
            return False, '', f'brew exit {rc}: {combined[:400]}'

        return False, '', f'Unsupported source: {source}'

    except Exception as exc:
        return False, '', f'Unexpected error: {exc}'


def process_app_update_orders(server_url, serial_number, timeout=60):
    """Ask Odoo what to upgrade, do it, report each result."""
    try:
        resp = requests.get(
            f'{server_url}/api/asset/app_updates/instructions',
            params={'serial_number': serial_number}, timeout=timeout)
        if resp.status_code != 200:
            return False

        data = resp.json()
        if not data.get('success'):
            return False

        jobs = data.get('upgrade_list', [])
        if not jobs:
            return True

        logger.info('[APP-UPDATE] %s upgrade order(s) received', len(jobs))
        for job in jobs:
            app_name = job.get('app_name', '')
            logger.info('[APP-UPDATE] Upgrading %s…', app_name)
            ok, new_version, error = upgrade_one_app(
                job.get('package_id', ''), app_name,
                job.get('source', 'apt'))

            try:
                requests.post(
                    f'{server_url}/api/asset/app_updates/result',
                    json={'serial_number': serial_number,
                          'app_name': app_name,
                          'success': ok,
                          'new_version': new_version,
                          'error': error},
                    timeout=timeout)
            except Exception as exc:
                logger.error('[APP-UPDATE] Could not report result: %s', exc)

            logger.info('[APP-UPDATE] %s -> %s', app_name,
                        'OK' if ok else f'FAILED: {error[:120]}')
        return True

    except Exception as exc:
        logger.error('[APP-UPDATE] Order processing failed: %s', exc)
        return False


# ══════════════════════════════════════════════════════════════════════════
# 2. TELEMETRY
# ══════════════════════════════════════════════════════════════════════════
def _collect_system():
    boot = psutil.boot_time() if psutil else None
    return {
        'hostname': socket.gethostname(),
        'fqdn': socket.getfqdn(),
        'platform': platform.system(),
        'kernel': platform.release(),
        'architecture': platform.machine(),
        'python_version': platform.python_version(),
        'distro': _detect_distro(),
        'boot_time': datetime.fromtimestamp(boot, timezone.utc).isoformat()
                     if boot else None,
        'uptime_seconds': int(time.time() - boot) if boot else None,
        'current_time': datetime.now(timezone.utc).isoformat(),
    }


def _collect_cpu():
    if not psutil:
        return None
    freq = psutil.cpu_freq()
    try:
        l1, l5, l15 = os.getloadavg()
    except Exception:
        l1 = l5 = l15 = None
    return {
        'model': _run(['sh', '-c',
                       "lscpu | grep 'Model name' | cut -d: -f2"])
                 or platform.processor(),
        'physical_cores': psutil.cpu_count(logical=False),
        'logical_cores': psutil.cpu_count(logical=True),
        'usage_percent_total': psutil.cpu_percent(interval=0.5),
        'usage_percent_per_core': psutil.cpu_percent(interval=0.3, percpu=True),
        'frequency_mhz': {
            'current': round(freq.current, 1) if freq else None,
            'max': round(freq.max, 1) if freq else None,
        },
        'load_average': {'1min': l1, '5min': l5, '15min': l15},
    }


def _collect_memory():
    if not psutil:
        return None
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return {
        'virtual': {
            'total_bytes': vm.total, 'total_human': _human(vm.total),
            'available_bytes': vm.available,
            'available_human': _human(vm.available),
            'used_bytes': vm.used, 'used_human': _human(vm.used),
            'percent': vm.percent,
            'cached_bytes': getattr(vm, 'cached', None),
        },
        'swap': {'total_bytes': sw.total, 'total_human': _human(sw.total),
                 'used_bytes': sw.used, 'percent': sw.percent},
    }


def _collect_disks():
    if not psutil:
        return None
    skip = {'tmpfs', 'devtmpfs', 'squashfs', 'overlay', 'proc', 'sysfs'}
    mounts = []
    for part in psutil.disk_partitions(all=False):
        if part.fstype in skip:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        mounts.append({
            'device': part.device, 'mount': part.mountpoint,
            'fstype': part.fstype,
            'total_human': _human(u.total), 'used_human': _human(u.used),
            'free_human': _human(u.free), 'percent': u.percent,
            'total_bytes': u.total, 'used_bytes': u.used, 'free_bytes': u.free,
        })
    return {'mounts': mounts}


def _collect_processes(top_n=15):
    if not psutil:
        return None
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'username',
                                  'cpu_percent', 'memory_percent', 'status']):
        try:
            i = p.info
            procs.append({
                'pid': i['pid'], 'name': i['name'], 'user': i['username'],
                'cpu_percent': i['cpu_percent'],
                'memory_percent': round(i['memory_percent'] or 0, 2),
                'status': i['status'],
            })
        except Exception:
            continue
    return {
        'total_count': len(procs),
        'top_by_cpu': sorted(procs, key=lambda x: x['cpu_percent'] or 0,
                             reverse=True)[:top_n],
        'top_by_memory': sorted(procs, key=lambda x: x['memory_percent'] or 0,
                                reverse=True)[:top_n],
    }


def _collect_network():
    if not psutil:
        return None
    interfaces = {}
    try:
        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            st = stats.get(name)
            interfaces[name] = {
                'is_up': st.isup if st else None,
                'speed_mbps': st.speed if st else None,
                'mtu': st.mtu if st else None,
                'addresses': [{'family': str(a.family), 'address': a.address,
                               'netmask': a.netmask} for a in addrs],
            }
    except Exception:
        pass
    return {'interfaces': interfaces,
            'listening_ports': get_listening_ports()}


def _collect_services():
    raw = _run(['systemctl', 'list-units', '--failed',
                '--no-legend', '--no-pager'])
    failed = []
    if raw:
        for line in raw.splitlines():
            parts = line.split(None, 4)
            if parts:
                failed.append({'unit': parts[0].lstrip('●').strip(),
                               'description': parts[4] if len(parts) > 4 else ''})
    return {'failed_units': failed, 'failed_count': len(failed)}


def _collect_packages():
    counts = {}
    for key, cmd in (
        ('dpkg', "dpkg -l 2>/dev/null | grep -c '^ii'"),
        ('snap', 'snap list 2>/dev/null | tail -n +2 | wc -l'),
        ('flatpak', 'flatpak list --app 2>/dev/null | wc -l'),
    ):
        val = _run(['sh', '-c', cmd])
        if val and val.isdigit():
            counts[key] = int(val)

    upgradable = _run(['sh', '-c',
                       'apt list --upgradable 2>/dev/null | tail -n +2 | wc -l'])
    return {
        'installed_counts': counts,
        'upgradable_count': int(upgradable) if upgradable
                            and upgradable.isdigit() else None,
        'reboot_required': os.path.exists('/var/run/reboot-required'),
    }


def _collect_hardware():
    sensors = None
    battery = None
    if psutil:
        try:
            temps = psutil.sensors_temperatures()
            sensors = {n: [{'label': e.label, 'current': e.current,
                            'high': e.high, 'critical': e.critical}
                           for e in entries]
                       for n, entries in temps.items()}
        except Exception:
            pass
        try:
            b = psutil.sensors_battery()
            if b:
                battery = {'percent': round(b.percent, 1),
                           'plugged_in': b.power_plugged,
                           'seconds_left': b.secsleft if b.secsleft > 0 else None}
        except Exception:
            pass

    def dmi(field):
        try:
            with open(f'/sys/class/dmi/id/{field}') as fh:
                return fh.read().strip()
        except Exception:
            return None

    return {
        'temperatures': sensors,
        'battery': battery,
        'dmi': {'vendor': dmi('sys_vendor'), 'product': dmi('product_name'),
                'serial': dmi('product_serial'),
                'bios_version': dmi('bios_version'),
                'bios_date': dmi('bios_date')},
    }


def _collect_raw():
    """Verbatim output of what an admin would actually type."""
    return {
        'df_h': _run(['df', '-h']),
        'free_h': _run(['free', '-h']),
        'uptime': _run(['uptime']),
        'uname_a': _run(['uname', '-a']),
        'lsblk': _run(['lsblk', '-o', 'NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE']),
        'ip_addr': _run(['ip', '-brief', 'addr']),
        'ip_route': _run(['ip', 'route']),
        'ss_listening': _run(['ss', '-tulnp']),
        'top_snapshot': _run(['sh', '-c', 'top -b -n 1 | head -25']),
        'systemctl_failed': _run(['systemctl', '--failed', '--no-pager']),
        'dmesg_errors': _run(['sh', '-c',
                              'dmesg --level=err,crit 2>/dev/null | tail -20']),
        'who': _run(['who']),
        'last_reboots': _run(['sh', '-c',
                              'last -x reboot 2>/dev/null | head -5']),
    }


SECTION_COLLECTORS = {
    'system': _collect_system, 'cpu': _collect_cpu, 'memory': _collect_memory, 'disks': _collect_disks,
    'processes': _collect_processes, 'network': _collect_network, 'services': _collect_services,
    'packages': _collect_packages, 'hardware': _collect_hardware, 'raw': _collect_raw,
}
_last_command_run = {}


def fetch_telemetry_policy(server_url, serial_number, timeout=20):
    """Telemetry policy for this machine from Odoo (IT Asset > Agent
    Telemetry > Telemetry Policies). None = server without policies: send
    everything every TELEMETRY_INTERVAL, as before."""
    try:
        resp = requests.get(f'{server_url}/api/asset/telemetry/policy',
                            params={'serial_number': serial_number}, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('success'):
                return data.get('policy')
    except Exception as exc:
        logger.debug('[TELEMETRY] policy fetch failed: %s', exc)
    return None


def _run_custom_commands(policy, force=False):
    out = {}
    now = time.time()
    plat = {'darwin': 'macos'}.get(platform.system().lower(), platform.system().lower())
    for c in (policy or {}).get('commands') or []:
        if c.get('platform') not in (None, '', 'all', plat):
            continue
        key = c.get('key')
        if not force and now - _last_command_run.get(key, 0) < int(c.get('interval') or 0):
            continue
        try:
            p = subprocess.run(c['command'], shell=True, capture_output=True, text=True,
                               timeout=int(c.get('timeout') or 60))
            out[key] = {'name': c.get('name'), 'exit': p.returncode, 'output': (p.stdout or p.stderr)[-5000:]}
        except subprocess.TimeoutExpired:
            out[key] = {'name': c.get('name'), 'exit': 124, 'output': 'timeout'}
        _last_command_run[key] = now
    return out


# ══════════════════════════════════════════════════════════════════════════
# POLICY RESTRICTIONS (IT Asset > Agent Telemetry > Telemetry Policies >
# Restrictions). Everything is applied from the policy and undone when the
# policy turns it off.
#   screenshots / screen recording   GNOME (dconf lock), capture tools, files
#   browsers                          Chrome / Edge / Brave / Chromium managed
#                                     policy, Firefox policies.json
#   capture extensions                found automatically in every profile
#   websites, USB storage, admin rights, blocked apps, screen lock
# ══════════════════════════════════════════════════════════════════════════
STATE_DIR_R = '/var/lib/asset_agent'
RESTRICT_DCONF = '/etc/dconf/db/local.d/90-asset-restrictions'
RESTRICT_LOCKS = '/etc/dconf/db/local.d/locks/90-asset-restrictions'
DCONF_PROFILE = '/etc/dconf/profile/user'
USB_MODPROBE = '/etc/modprobe.d/90-asset-usb-storage.conf'
USB_UDEV = '/etc/udev/rules.d/90-asset-usb-readonly.rules'
ADMIN_STATE = os.path.join(STATE_DIR_R, 'admin_removed.json')
FF_STATE = os.path.join(STATE_DIR_R, 'firefox_blocked.json')
ADMIN_GROUPS = ('sudo', 'admin', 'wheel')

SCREENSHOT_TOOLS = ('gnome-screenshot', 'flameshot', 'shutter', 'ksnip', 'spectacle', 'scrot', 'maim',
                    'xfce4-screenshooter', 'deepin-screenshot', 'grim')
RECORDER_TOOLS = ('simplescreenrecorder', 'obs', 'kazam', 'vokoscreenNG', 'vokoscreen', 'peek', 'kooha',
                  'recordmydesktop', 'gtk-recordmydesktop', 'gpu-screen-recorder', 'wf-recorder', 'byzanz-record')
SCREENSHOT_DIRS = ('Pictures/Screenshots',)
RECORDING_DIRS = ('Videos/Screencasts',)

# browser extensions that capture the screen - matched on name / description
CAPTURE_WORDS = re.compile(r'screen\s*shot|screenshot|screen\s*capture|screen\s*recorder|screen\s*record|'
                           r'screencast|capture\s+(the\s+)?(screen|page|tab)|full\s*page\s*capture|'
                           r'record\s+(your\s+)?screen|snipping|snagit', re.I)
CHROME_PROFILES = {   # browser key -> profile roots (relative to a home dir)
    'chrome': ('.config/google-chrome',),
    'chromium': ('.config/chromium', 'snap/chromium/common/chromium'),
    'edge': ('.config/microsoft-edge',),
    'brave': ('.config/BraveSoftware/Brave-Browser', 'snap/brave/current/.config/BraveSoftware/Brave-Browser'),
}
FIREFOX_PROFILES = ('.mozilla/firefox', 'snap/firefox/common/.mozilla/firefox')
# managed-policy dir -> paths that show the browser is installed
CHROME_POLICY_DIRS = {
    '/etc/opt/chrome/policies/managed': ('/opt/google/chrome',),
    '/etc/chromium/policies/managed': ('/usr/lib/chromium', '/snap/chromium', '/usr/bin/chromium'),
    '/etc/chromium-browser/policies/managed': ('/usr/lib/chromium-browser', '/usr/bin/chromium-browser'),
    '/etc/opt/edge/policies/managed': ('/opt/microsoft/msedge',),
    '/etc/brave/policies/managed': ('/opt/brave.com', '/snap/brave'),
}
CHROME_POLICY_FILE = 'asset-agent.json'
FIREFOX_POLICY = '/etc/firefox/policies/policies.json'

_restrictions = {'screenshot': False, 'screen_record': False, 'killed': 0, 'removed_files': 0,
                 'applied_at': None, 'detected_extensions': [], 'blocked_websites': 0, 'usb_storage': 'allow',
                 'admin_removed': [], 'blocked_apps_killed': 0, 'screen_lock_minutes': 0}


def _write_if_changed(path, text):
    try:
        with open(path) as fh:
            if fh.read() == text:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as fh:
        fh.write(text)
    return True


def _remove(path):
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def _read_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def _homes():
    out = []
    for home in glob.glob('/home/*') + ['/root']:
        try:
            if os.path.isdir(home) and (home == '/root' or os.stat(home).st_uid >= 1000):
                out.append(home)
        except OSError:
            pass
    return out


# ---------------------------------------------------------------- GNOME dconf
def _ensure_dconf_profile():
    try:
        with open(DCONF_PROFILE) as fh:
            lines = [l.strip() for l in fh if l.strip()]
    except OSError:
        lines = []
    if 'system-db:local' in lines:
        return
    if 'user-db:user' not in lines:
        lines.insert(0, 'user-db:user')
    lines.append('system-db:local')
    os.makedirs(os.path.dirname(DCONF_PROFILE), exist_ok=True)
    with open(DCONF_PROFILE, 'w') as fh:
        fh.write('\n'.join(lines) + '\n')
    _restrictions['relogin_needed'] = True
    logger.warning('[RESTRICT] dconf profile created - GNOME locks apply from the next login')


def _system_dconf_value(path, key):
    """Value another system keyfile (not ours) sets, e.g. a mandatory
    extension list - so ours can extend it instead of replacing it."""
    val = None
    for f in sorted(glob.glob('/etc/dconf/db/local.d/*')):
        if os.path.isdir(f) or f == RESTRICT_DCONF:
            continue
        section = None
        try:
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith('['):
                        section = line.strip('[]')
                    elif section == path and line.split('=', 1)[0].strip() == key:
                        val = line.split('=', 1)[1].strip()
        except OSError:
            pass
    return val


def _locked_elsewhere(dconf_key):
    for f in glob.glob('/etc/dconf/db/local.d/locks/*'):
        if f == RESTRICT_LOCKS:
            continue
        try:
            with open(f) as fh:
                if dconf_key in (l.strip() for l in fh):
                    return True
        except OSError:
            pass
    return False


def _dconf_settings(r):
    """{dconf path: {key: value}} for the policy."""
    out = {}

    def put(path, key, value):
        out.setdefault(path, {})[key] = value
    if r.get('block_screenshot'):
        for k in ('screenshot', 'screenshot-window', 'show-screenshot-ui'):
            put('org/gnome/shell/keybindings', k, '@as []')
        for k in ('screenshot', 'window-screenshot', 'area-screenshot', 'screenshot-clip',
                  'window-screenshot-clip', 'area-screenshot-clip'):
            put('org/gnome/settings-daemon/plugins/media-keys', k, '@as []')
    if r.get('block_screen_record'):
        put('org/gnome/shell/keybindings', 'show-screen-recording-ui', '@as []')
        put('org/gnome/settings-daemon/plugins/media-keys', 'screencast', '@as []')
    if r.get('track_screen_time', True) and _locked_elsewhere('/org/gnome/shell/enabled-extensions'):
        # extensions are a mandatory, locked list on this machine: add ours to it
        cur = _system_dconf_value('org/gnome/shell', 'enabled-extensions') or '[]'
        items = re.findall(r"'([^']+)'", cur)
        if FOCUS_EXT_UUID not in items:
            items.append(FOCUS_EXT_UUID)
        put('org/gnome/shell', 'enabled-extensions', '[%s]' % ', '.join("'%s'" % i for i in items))
    minutes = int(r.get('screen_lock_minutes') or 0)
    if minutes > 0:
        put('org/gnome/desktop/session', 'idle-delay', 'uint32 %d' % (minutes * 60))
        put('org/gnome/desktop/screensaver', 'lock-enabled', 'true')
        put('org/gnome/desktop/screensaver', 'lock-delay', 'uint32 0')
        put('org/gnome/desktop/screensaver', 'idle-activation-enabled', 'true')
    return out


def apply_dconf(r):
    settings = _dconf_settings(r)
    changed = False
    if settings:
        _ensure_dconf_profile()
        conf, locks = [], []
        for path, kv in settings.items():
            conf.append('[%s]' % path)
            conf.extend('%s=%s' % (k, v) for k, v in kv.items())
            conf.append('')
            locks.extend('/%s/%s' % (path, k) for k in kv if not (path == 'org/gnome/shell' and k == 'enabled-extensions'))
        changed |= _write_if_changed(RESTRICT_DCONF, '\n'.join(conf) + '\n')
        changed |= _write_if_changed(RESTRICT_LOCKS, '\n'.join(locks) + '\n')
    else:
        changed |= _remove(RESTRICT_DCONF)
        changed |= _remove(RESTRICT_LOCKS)
    if changed and shutil.which('dconf'):
        subprocess.run(['dconf', 'update'], capture_output=True, timeout=30)
    return changed


# ------------------------------------------------------------ browser policies
def _url_patterns(sites):
    """'facebook.com' -> Chrome 'facebook.com' (covers sub-domains) and
    Firefox match patterns."""
    chrome, firefox = [], []
    for s in sites:
        s = re.sub(r'^\w+://', '', s.strip()).strip('/').lstrip('*.').strip()
        s = re.sub(r'^www\.', '', s)
        if not s:
            continue
        chrome.append(s)
        host, _sep, path = s.partition('/')
        tail = '/' + path + '*' if path else '/*'
        firefox += ['*://%s%s' % (host, tail), '*://*.%s%s' % (host, tail)]
    return chrome, firefox


HOSTS = '/etc/hosts'
HOSTS_BEGIN, HOSTS_END = '# BEGIN asset-agent blocked websites', '# END asset-agent blocked websites'


def apply_hosts(sites):
    """Blocked sites in /etc/hosts too: every browser and app, at once, no
    browser restart (paths like reddit.com/r/all stay browser-policy only)."""
    domains = []
    for s_ in sites:
        host = re.sub(r'^\w+://', '', s_.strip()).strip('/').lstrip('*.').split('/')[0]
        host = re.sub(r'^www\.', '', host)
        if host and '/' not in s_.strip().strip('/').split('://')[-1]:
            domains += [host, 'www.' + host]
    try:
        with open(HOSTS) as fh:
            text = fh.read()
    except OSError:
        return False
    text_wo = re.sub(r'\n?%s\n.*?%s\n?' % (re.escape(HOSTS_BEGIN), re.escape(HOSTS_END)), '\n', text,
                     flags=re.S).rstrip('\n') + '\n'
    if domains:
        text_wo += '%s\n%s\n%s\n' % (HOSTS_BEGIN, '\n'.join('0.0.0.0 %s' % d for d in domains), HOSTS_END)
    if text_wo == text:
        return False
    with open(HOSTS, 'w') as fh:
        fh.write(text_wo)
    if shutil.which('resolvectl'):
        subprocess.run(['resolvectl', 'flush-caches'], capture_output=True, timeout=10)
    return True


def _notify_users(title, body):
    """Desktop notification in every logged-in session."""
    for home in _homes():
        try:
            uid, user = os.stat(home).st_uid, os.path.basename(home)
            run = '/run/user/%d' % uid
            if uid < 1000 or not os.path.exists(run + '/bus') or not shutil.which('notify-send'):
                continue
            subprocess.run(['sudo', '-u', user, 'env', 'XDG_RUNTIME_DIR=' + run,
                            'DBUS_SESSION_BUS_ADDRESS=unix:path=%s/bus' % run,
                            'notify-send', '-u', 'critical', '-a', 'IT Policy', title, body],
                           capture_output=True, timeout=10)
        except Exception:
            pass


def _firefox_running():
    if not psutil:
        return False
    for proc in psutil.process_iter(['name']):
        if (proc.info.get('name') or '').startswith('firefox'):
            return True
    return False


def apply_browser_policies(r, chrome_ids, firefox_ids):
    shot, rec = bool(r.get('block_screenshot')), bool(r.get('block_screen_record'))
    chrome_sites, firefox_sites = _url_patterns(r.get('blocked_websites') or [])
    pol = {}
    if shot:
        pol['DisableScreenshots'] = True
    if rec:
        pol['ScreenCaptureAllowed'] = False
    if shot or rec:
        # any extension asking for screen / tab capture cannot install or run
        pol['ExtensionSettings'] = {'*': {'blocked_permissions': ['desktopCapture', 'tabCapture']}}
    if chrome_ids:
        pol['ExtensionInstallBlocklist'] = sorted(chrome_ids)
    if chrome_sites:
        pol['URLBlocklist'] = chrome_sites
        pol['DnsOverHttpsMode'] = 'off'      # keep /etc/hosts in charge
    changed = False
    for d, markers in CHROME_POLICY_DIRS.items():
        path = os.path.join(d, CHROME_POLICY_FILE)
        if pol and any(os.path.exists(m) for m in markers):
            changed |= _write_if_changed(path, json.dumps(pol, indent=2) + '\n')
        elif not pol:
            changed |= _remove(path)

    # Firefox: one shared policies.json - only our own keys are touched
    if os.path.isdir('/usr/lib/firefox') or os.path.isdir('/snap/firefox') or os.path.exists(FIREFOX_POLICY):
        data = _read_json(FIREFOX_POLICY, {})
        p = data.setdefault('policies', {})
        before = json.dumps(data, sort_keys=True)
        if shot:
            p['DisableFirefoxScreenshots'] = True
        else:
            p.pop('DisableFirefoxScreenshots', None)
        prefs = p.get('Preferences') or {}
        if rec:
            prefs['media.getdisplaymedia.enabled'] = {'Value': False, 'Status': 'locked'}
        else:
            prefs.pop('media.getdisplaymedia.enabled', None)
        if prefs:
            p['Preferences'] = prefs
        else:
            p.pop('Preferences', None)
        ours = set(_read_json(FF_STATE, []))
        ext = {k: v for k, v in (p.get('ExtensionSettings') or {}).items() if k not in ours}
        for i in firefox_ids:
            ext[i] = {'installation_mode': 'blocked'}
        if ext:
            p['ExtensionSettings'] = ext
        else:
            p.pop('ExtensionSettings', None)
        _write_if_changed(FF_STATE, json.dumps(sorted(firefox_ids)))
        if firefox_sites:
            p['WebsiteFilter'] = {'Block': firefox_sites}
            p['DNSOverHTTPS'] = {'Enabled': False, 'Locked': True}
        else:
            p.pop('WebsiteFilter', None)
            p.pop('DNSOverHTTPS', None)
        if json.dumps(data, sort_keys=True) != before:
            if p:
                _write_if_changed(FIREFOX_POLICY, json.dumps(data, indent=2) + '\n')
            else:
                _remove(FIREFOX_POLICY)
            changed = True
            if _firefox_running():
                _notify_users('IT policy updated',
                              'Please close and reopen Firefox - the new IT policy applies after a restart.')
    if apply_hosts(r.get('blocked_websites') or []):
        changed = True
    _restrictions['blocked_websites'] = len(chrome_sites)
    return changed


def detect_capture_extensions():
    """Screenshot / screen-recording extensions installed in any browser
    profile on this machine: [{browser, id, name}]."""
    found = {}
    for home in _homes():
        for browser, roots in CHROME_PROFILES.items():
            for root in roots:
                for man in glob.glob(os.path.join(home, root, '*', 'Extensions', '*', '*', 'manifest.json')):
                    ext_id = man.split(os.sep)[-3]
                    if not re.fullmatch(r'[a-p]{32}', ext_id) or ('chrome', ext_id) in found:
                        continue
                    try:
                        m = _read_json(man, {})
                        name, desc = m.get('name', ''), m.get('description', '')
                        loc_dir = os.path.join(os.path.dirname(man), '_locales')
                        for loc in ('en', 'en_US', 'en_GB'):
                            msgs = _read_json(os.path.join(loc_dir, loc, 'messages.json'), None)
                            if msgs:
                                low = {k.lower(): v.get('message', '') for k, v in msgs.items()}
                                if name.startswith('__MSG_'):
                                    name = low.get(name[6:-2].lower(), name)
                                if desc.startswith('__MSG_'):
                                    desc = low.get(desc[6:-2].lower(), desc)
                                break
                        perms = set(m.get('permissions') or []) | set(m.get('optional_permissions') or [])
                        if CAPTURE_WORDS.search('%s %s' % (name, desc)) or perms & {'desktopCapture', 'tabCapture'}:
                            found[('chrome', ext_id)] = {'browser': browser, 'id': ext_id, 'name': name}
                    except Exception:
                        continue
        for root in FIREFOX_PROFILES:
            for ej in glob.glob(os.path.join(home, root, '*', 'extensions.json')):
                for a in _read_json(ej, {}).get('addons', []):
                    if a.get('type') != 'extension' or a.get('location') != 'app-profile':
                        continue
                    loc = a.get('defaultLocale') or {}
                    text = '%s %s' % (loc.get('name') or '', loc.get('description') or '')
                    if CAPTURE_WORDS.search(text) and ('firefox', a['id']) not in found:
                        found[('firefox', a['id'])] = {'browser': 'firefox', 'id': a['id'],
                                                       'name': loc.get('name') or a['id']}
    return list(found.values())


# ----------------------------------------------------------------- USB storage
def apply_usb(mode):
    changed = False
    if mode == 'block':
        changed |= _write_if_changed(USB_MODPROBE, '# asset agent policy: USB storage blocked\n'
                                     'blacklist usb-storage\nblacklist uas\n'
                                     'install usb-storage /bin/false\ninstall uas /bin/false\n')
        changed |= _remove(USB_UDEV)
        if changed:
            for mod in ('uas', 'usb_storage'):
                subprocess.run(['modprobe', '-r', mod], capture_output=True, timeout=20)
    elif mode == 'read_only':
        changed |= _remove(USB_MODPROBE)
        changed |= _write_if_changed(USB_UDEV, '# asset agent policy: USB storage read-only\n'
                                     'ACTION=="add|change", SUBSYSTEM=="block", ENV{ID_BUS}=="usb", '
                                     'RUN+="/sbin/blockdev --setro /dev/%k"\n')
    else:
        changed |= _remove(USB_MODPROBE)
        changed |= _remove(USB_UDEV)
    if changed and shutil.which('udevadm'):
        subprocess.run(['udevadm', 'control', '--reload-rules'], capture_output=True, timeout=20)
        if mode == 'read_only':
            subprocess.run(['udevadm', 'trigger', '--subsystem-match=block', '--action=change'],
                           capture_output=True, timeout=30)
    _restrictions['usb_storage'] = mode
    return changed


# --------------------------------------------------------------- admin rights
def _group_members(group):
    try:
        with open('/etc/group') as fh:
            for line in fh:
                parts = line.strip().split(':')
                if len(parts) >= 4 and parts[0] == group:
                    return [m for m in parts[3].split(',') if m]
    except OSError:
        pass
    return []


def apply_admin_rights(remove, allowed):
    """Take human users out of sudo/admin (except the allowed ones); put back
    exactly who was taken out when the policy stops removing."""
    removed = _read_json(ADMIN_STATE, {})          # {group: [users]}
    changed = False
    if remove:
        for g in ADMIN_GROUPS:
            for u in _group_members(g):
                if u in allowed or u == 'root':
                    continue
                if subprocess.run(['gpasswd', '-d', u, g], capture_output=True, timeout=20).returncode == 0:
                    removed.setdefault(g, [])
                    if u not in removed[g]:
                        removed[g].append(u)
                    changed = True
                    logger.warning('[RESTRICT] removed %s from %s', u, g)
        # users allowed again since the last run get their rights back
        for g, users in list(removed.items()):
            for u in [x for x in users if x in allowed]:
                subprocess.run(['gpasswd', '-a', u, g], capture_output=True, timeout=20)
                users.remove(u)
                changed = True
    else:
        for g, users in removed.items():
            for u in users:
                subprocess.run(['gpasswd', '-a', u, g], capture_output=True, timeout=20)
                logger.warning('[RESTRICT] restored %s to %s', u, g)
                changed = True
        removed = {}
    _write_if_changed(ADMIN_STATE, json.dumps(removed))
    _restrictions['admin_removed'] = sorted({u for us in removed.values() for u in us})
    return changed


# ------------------------------------------------------------- enforcement
def _clear_clipboard(home):
    """GNOME's screenshot UI also copies the image to the clipboard."""
    try:
        uid, user = os.stat(home).st_uid, os.path.basename(home)
        run = '/run/user/%d' % uid
        if not os.path.isdir(run):
            return
        env = ['env', 'XDG_RUNTIME_DIR=' + run, 'DBUS_SESSION_BUS_ADDRESS=unix:path=%s/bus' % run]
        wl = [f for f in glob.glob(run + '/wayland-*') if not f.endswith('.lock')]
        if wl and shutil.which('wl-copy'):
            cmd = env + ['WAYLAND_DISPLAY=' + os.path.basename(wl[0]), 'wl-copy', '--clear']
        elif shutil.which('xclip'):
            xa = glob.glob(run + '/.mutter-Xwaylandauth.*') + [home + '/.Xauthority']
            cmd = env + ['DISPLAY=:0', 'XAUTHORITY=' + xa[0], 'sh', '-c', 'printf "" | xclip -selection clipboard']
        else:
            return
        subprocess.run(['sudo', '-u', user] + cmd, capture_output=True, timeout=10)
    except Exception as exc:
        logger.debug('[RESTRICT] clipboard clear failed: %s', exc)


def _enforce_once(r, seen):
    shot, rec = bool(r.get('block_screenshot')), bool(r.get('block_screen_record'))
    capture = set((SCREENSHOT_TOOLS if shot else ()) + (RECORDER_TOOLS if rec else ()))
    apps = {a.lower() for a in (r.get('blocked_apps') or [])}
    if (capture or apps) and psutil:
        for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
            try:
                n = proc.info.get('name') or ''
                exe = os.path.basename(proc.info.get('exe') or '')
                hit_capture = n in capture or exe in capture
                hit_app = bool(apps) and (n.lower() in apps or exe.lower() in apps)
                if hit_capture or hit_app:
                    proc.kill()
                    _restrictions['killed' if hit_capture else 'blocked_apps_killed'] += 1
                    logger.warning('[RESTRICT] stopped %s (pid %s)', n or exe, proc.info['pid'])
            except Exception:
                pass
    dirs = (SCREENSHOT_DIRS if shot else ()) + (RECORDING_DIRS if rec else ())
    for home in (_homes() if dirs else []):
        for d in dirs:
            path = os.path.join(home, d)
            try:
                entries = os.listdir(path)
            except OSError:
                continue
            for e in entries:
                fp = os.path.join(path, e)
                if fp in seen:
                    continue
                try:
                    if os.path.isfile(fp) and os.path.getmtime(fp) >= seen.get('__since__', 0):
                        os.remove(fp)
                        _restrictions['removed_files'] += 1
                        logger.warning('[RESTRICT] removed capture %s', fp)
                        _clear_clipboard(home)
                    else:
                        seen[fp] = True
                except OSError:
                    pass


def apply_restrictions(r):
    """Bring the machine in line with the policy (idempotent)."""
    if platform.system() != 'Linux' or not hasattr(os, 'geteuid') or os.geteuid() != 0:
        return
    shot, rec = bool(r.get('block_screenshot')), bool(r.get('block_screen_record'))
    chrome_ids = {i for i in r.get('blocked_extensions') or [] if re.fullmatch(r'[a-p]{32}', i)}
    firefox_ids = {i for i in r.get('blocked_extensions') or [] if not re.fullmatch(r'[a-p]{32}', i)}
    detected = detect_capture_extensions() if (shot or rec) and r.get('auto_block_capture_extensions', True) else []
    for e in detected:
        (firefox_ids if e['browser'] == 'firefox' else chrome_ids).add(e['id'])
    changes = []
    if apply_dconf(r):
        changes.append('gnome')
    if apply_browser_policies(r, chrome_ids, firefox_ids):
        changes.append('browsers')
    if apply_usb(r.get('usb_storage') or 'allow'):
        changes.append('usb')
    if apply_admin_rights(bool(r.get('remove_admin_rights')), set(r.get('admin_allowed_users') or [])):
        changes.append('admin')
    _restrictions.update({'screenshot': shot, 'screen_record': rec,
                          'detected_extensions': detected,
                          'screen_lock_minutes': int(r.get('screen_lock_minutes') or 0),
                          'applied_at': datetime.now(timezone.utc).isoformat()})
    if changes:
        logger.info('[RESTRICT] applied (%s): screenshot=%s record=%s websites=%s usb=%s admin_removed=%s '
                    'apps=%s lock=%smin extensions=%s', ', '.join(changes), shot, rec,
                    _restrictions['blocked_websites'], _restrictions['usb_storage'],
                    _restrictions['admin_removed'], len(r.get('blocked_apps') or []),
                    _restrictions['screen_lock_minutes'], len(chrome_ids) + len(firefox_ids))


# ══════════════════════════════════════════════════════════════════════════
# SCREEN TIME - on / idle / locked / off per minute and time per application
# ══════════════════════════════════════════════════════════════════════════
SCREENTIME_DIR = '/var/lib/asset_agent/screentime'
FOCUS_EXT_UUID = 'asset-focus@futurenet.in'
IDLE_AFTER = 300            # no input for 5 min = idle


def _sh_user(user, uid, cmd, timeout=10):
    run = '/run/user/%d' % uid
    return subprocess.run(['sudo', '-u', user, 'env', 'XDG_RUNTIME_DIR=' + run,
                           'DBUS_SESSION_BUS_ADDRESS=unix:path=%s/bus' % run] + cmd,
                          capture_output=True, text=True, timeout=timeout)


def _graphical_session():
    """(user, uid, locked, session_type) of the active local desktop session, or None."""
    try:
        out = subprocess.run(['loginctl', 'list-sessions', '--no-legend'], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return None
    for line in out.splitlines():
        sid = line.split()[0] if line.split() else ''
        if not sid:
            continue
        props = {}
        r = subprocess.run(['loginctl', 'show-session', sid, '-p', 'Name', '-p', 'User', '-p', 'Type', '-p',
                            'Class', '-p', 'Active', '-p', 'Remote', '-p', 'LockedHint'],
                           capture_output=True, text=True, timeout=10).stdout
        for l in r.splitlines():
            k, _, v = l.partition('=')
            props[k] = v
        if (props.get('Class') == 'user' and props.get('Type') in ('wayland', 'x11')
                and props.get('Active') == 'yes' and props.get('Remote') != 'yes'):
            return props.get('Name'), int(props.get('User') or 0), props.get('LockedHint') == 'yes', props.get('Type')
    return None


def _idle_seconds(user, uid):
    try:
        r = _sh_user(user, uid, ['gdbus', 'call', '--session', '--dest', 'org.gnome.Mutter.IdleMonitor',
                                 '--object-path', '/org/gnome/Mutter/IdleMonitor/Core',
                                 '--method', 'org.gnome.Mutter.IdleMonitor.GetIdletime'])
        m = re.search(r'(\d+)', r.stdout or '')
        return int(m.group(1)) / 1000.0 if m else 0
    except Exception:
        return 0


def _focused_app(user, uid, home, session_type):
    """(app_id, app_name) in front: our GNOME extension's file, else xdotool on X11."""
    data = _read_json(os.path.join(home, '.cache', 'asset-agent', 'focus.json'), {})
    if data and time.time() - int(data.get('ts') or 0) < 120:
        app_id, name = data.get('app') or '', data.get('name') or ''
        if app_id.startswith('window:'):          # no .desktop file: older extension gave "window:N"
            app_id = name
        if name.lower() in ('gjs', 'ding', 'com.rastersoft.ding') or app_id.lower() in ('gjs', 'com.rastersoft.ding'):
            app_id, name = 'desktop', 'Desktop'   # the desktop itself (Desktop Icons NG runs in gjs)
        return (app_id or name), (name or app_id)
    if session_type == 'x11' and shutil.which('xdotool'):
        try:
            r = _sh_user(user, uid, ['env', 'DISPLAY=:0', 'xdotool', 'getactivewindow', 'getwindowpid'])
            pid = int((r.stdout or '0').strip() or 0)
            if pid and psutil:
                n = psutil.Process(pid).name()
                return n, n
        except Exception:
            pass
    return '', ''


def _ensure_focus_extension(user, uid):
    """Turn the screen-time extension on for this user (takes effect from
    the next login if the shell started before it was installed)."""
    if not os.path.isdir('/usr/share/gnome-shell/extensions/' + FOCUS_EXT_UUID):
        return
    try:
        cur = _sh_user(user, uid, ['gsettings', 'get', 'org.gnome.shell', 'enabled-extensions']).stdout.strip()
        if FOCUS_EXT_UUID in cur:
            return
        items = re.findall(r"'([^']+)'", cur)
        items.append(FOCUS_EXT_UUID)
        _sh_user(user, uid, ['gsettings', 'set', 'org.gnome.shell', 'enabled-extensions',
                             '[%s]' % ', '.join("'%s'" % i for i in items)])
        _sh_user(user, uid, ['gnome-extensions', 'enable', FOCUS_EXT_UUID])
        logger.info('[SCREENTIME] focus extension enabled for %s', user)
    except Exception as exc:
        logger.debug('[SCREENTIME] could not enable extension: %s', exc)


def _screentime_sample():
    """One minute: (state, user, app_id, app_name)."""
    sess = _graphical_session()
    if not sess:
        return 'off', '', '', ''
    user, uid, locked, stype = sess
    if locked:
        return 'locked', user, '', ''
    idle = _idle_seconds(user, uid)
    home = os.path.expanduser('~' + user)
    if idle >= IDLE_AFTER:
        return 'idle', user, '', ''
    app_id, app_name = _focused_app(user, uid, home, stype)
    return 'active', user, app_id, app_name


DESKTOP_DIRS = ('/usr/share/applications', '/usr/local/share/applications', '/var/lib/snapd/desktop/applications',
                '/var/lib/flatpak/exports/share/applications')
ICON_DIRS = ('/usr/share/icons/hicolor', '/usr/share/icons/Yaru', '/usr/share/icons/Adwaita',
             '/var/lib/flatpak/exports/share/icons/hicolor')
_icons_sent = set()


def _desktop_file(app_id):
    if not app_id:
        return None
    names = [app_id if app_id.endswith('.desktop') else app_id + '.desktop']
    for home in _homes():
        for d in (os.path.join(home, '.local/share/applications'),) + DESKTOP_DIRS:
            for n in names:
                p = os.path.join(d, n)
                if os.path.isfile(p):
                    return p
    return None


def _app_icon_b64(app_id, app_name):
    """PNG / SVG of the application's icon (from its .desktop Icon=), base64."""
    import base64
    icon = None
    desk = _desktop_file(app_id)
    if desk:
        try:
            with open(desk, errors='ignore') as fh:
                for line in fh:
                    if line.startswith('Icon='):
                        icon = line.split('=', 1)[1].strip()
                        break
        except OSError:
            pass
    icon = icon or (app_name or '').lower()
    if not icon:
        return None
    candidates = []
    if os.path.isabs(icon):
        candidates.append(icon)
    else:
        for base in ICON_DIRS:
            for size in ('64x64', '48x48', '128x128', '96x96', '256x256', 'scalable'):
                for ext in ('png', 'svg'):
                    candidates.append(os.path.join(base, size, 'apps', '%s.%s' % (icon, ext)))
        candidates += ['/usr/share/pixmaps/%s.png' % icon, '/usr/share/pixmaps/%s.svg' % icon]
    for c in candidates:
        try:
            if not (os.path.isfile(c) and c.endswith(('.png', '.svg'))):
                continue
            if c.endswith('.png') and os.path.getsize(c) > 40000:
                # big PNG (snaps ship 512-1024 px icons): shrink to 64 px
                try:
                    import io
                    from PIL import Image
                    im = Image.open(c)
                    im.thumbnail((64, 64))
                    buf = io.BytesIO()
                    im.save(buf, 'PNG', optimize=True)
                    return base64.b64encode(buf.getvalue()).decode()
                except Exception:
                    continue
            if os.path.getsize(c) < 200000:
                with open(c, 'rb') as fh:
                    return base64.b64encode(fh.read()).decode()
        except OSError:
            continue
    return None


def _day_summary(day, samples):
    """samples [[ts, state, user, app_id, app_name]] -> segments + per-app seconds."""
    segs, apps = [], {}
    for ts, state, user, app_id, app_name in sorted(samples):
        if segs and segs[-1]['state'] == state and segs[-1]['user'] == user and ts - segs[-1]['end'] <= 150:
            segs[-1]['end'] = ts + 60
        else:
            segs.append({'start': ts, 'end': ts + 60, 'state': state, 'user': user})
        if state == 'active':
            key = app_id or app_name or 'Unknown'
            a = apps.setdefault(key, {'app_id': app_id,
                                      'name': app_name or app_id or 'Not tracked (before login)', 'seconds': 0})
            a['seconds'] += 60
    for a in apps.values():
        key = a['app_id'] or a['name']
        if key not in _icons_sent and a['app_id']:
            icon = _safe(lambda: _app_icon_b64(a['app_id'], a['name']))
            if icon:
                a['icon'] = icon
            _icons_sent.add(key)
    return {'date': day, 'segments': segs, 'apps': list(apps.values())}


def screentime_loop(server_url, serial_number):
    logger.info('[SCREENTIME] Loop started')
    os.makedirs(SCREENTIME_DIR, exist_ok=True)
    last_upload, enabled = 0, True
    while True:
        try:
            if time.time() - last_upload >= 300:
                policy = fetch_telemetry_policy(server_url, serial_number)
                enabled = policy is None or policy.get('track_screen_time', True)
            if enabled and hasattr(os, 'geteuid') and os.geteuid() == 0:
                state, user, app_id, app_name = _screentime_sample()
                now = int(time.time())
                day = time.strftime('%Y-%m-%d', time.localtime(now))
                path = os.path.join(SCREENTIME_DIR, day + '.json')
                data = _read_json(path, {'samples': []})
                if state != 'off':                 # off = no samples (gaps)
                    data['samples'].append([now - now % 60, state, user, app_id, app_name])
                    with open(path, 'w') as fh:
                        json.dump(data, fh)
                    if state == 'active' and user and now % 3600 < 60:
                        _ensure_focus_extension(user, _graphical_session()[1])
                if time.time() - last_upload >= 300:
                    days = sorted(glob.glob(os.path.join(SCREENTIME_DIR, '*.json')))[-2:]   # today + yesterday
                    payload = {'serial_number': serial_number, 'tz_offset': -(time.altzone if time.localtime().tm_isdst else time.timezone),
                               'days': [
                                   _day_summary(os.path.basename(f)[:-5], _read_json(f, {}).get('samples', []))
                                   for f in days]}
                    resp = requests.post('%s/api/asset/screentime' % server_url, json=payload, timeout=30)
                    if resp.status_code == 200:
                        last_upload = time.time()
                    for f in sorted(glob.glob(os.path.join(SCREENTIME_DIR, '*.json')))[:-14]:
                        os.remove(f)          # keep two weeks locally
        except Exception as exc:
            logger.error('[SCREENTIME] error: %s', exc)
        time.sleep(60 - time.time() % 60 + 1)


AGENT_CONFIG = '/etc/asset-agent/agent.json'
UPDATE_EVERY = 600


def agent_update_loop(server_url, serial_number):
    """Self-update: when the agent Odoo serves differs from the one installed
    here, run the install link again (detached, outside this service's
    cgroup - the installer restarts this service)."""
    import random
    logger.info('[SELF-UPDATE] Loop started')
    time.sleep(90 + random.randint(0, 120))
    while True:
        try:
            cfg = _read_json(AGENT_CONFIG, {})
            token = cfg.get('token')
            if token and hasattr(os, 'geteuid') and os.geteuid() == 0:
                resp = requests.get('%s/api/asset/agent/version' % server_url, timeout=20)
                latest = resp.json().get('version') if resp.status_code == 200 else None
                if latest and latest != cfg.get('version'):
                    logger.warning('[SELF-UPDATE] %s -> %s: reinstalling from %s',
                                   cfg.get('version'), latest, server_url)
                    url = '%s/asset/agent/install.sh?token=%s&db=%s' % (server_url, token, cfg.get('db') or '')
                    subprocess.Popen(['systemd-run', '--unit', 'asset-agent-selfupdate-%d' % int(time.time()),
                                      '--collect', '--quiet', '/bin/bash', '-c',
                                      'curl -fsSL "%s" | bash > /var/log/asset-agent/selfupdate.log 2>&1' % url],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(UPDATE_EVERY * 3)   # the installer restarts us long before this
                    continue
        except Exception as exc:
            logger.debug('[SELF-UPDATE] check failed: %s', exc)
        time.sleep(UPDATE_EVERY + random.randint(0, 60))


def restrictions_loop(server_url, serial_number):
    """Policy (and an extension scan) every 60 s; while something needs
    watching, enforce every 3 s."""
    logger.info('[RESTRICT] Loop started')
    time.sleep(20)
    seen = {'__since__': time.time()}
    r, last_fetch = {}, 0
    while True:
        try:
            if time.time() - last_fetch >= 60:
                policy = fetch_telemetry_policy(server_url, serial_number)
                if policy is not None:
                    new = policy.get('restrictions') or {}
                    capture_was = r.get('block_screenshot') or r.get('block_screen_record')
                    if (new.get('block_screenshot') or new.get('block_screen_record')) and not capture_was:
                        seen = {'__since__': time.time()}
                    r = dict(new, track_screen_time=policy.get('track_screen_time', True))
                    apply_restrictions(r)
                last_fetch = time.time()
            if r.get('block_screenshot') or r.get('block_screen_record') or r.get('blocked_apps'):
                _enforce_once(r, seen)
        except Exception as exc:
            logger.error('[RESTRICT] error: %s', exc)
        busy = r.get('block_screenshot') or r.get('block_screen_record') or r.get('blocked_apps')
        time.sleep(3 if busy else 30)


def gather_telemetry(policy=None):
    # with a policy: exactly its sections (none = only the custom commands);
    # without one (older Odoo): everything, as before
    sections = list(SECTION_COLLECTORS) if policy is None else (policy.get('sections') or [])
    data = {
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'collector_version': '1.1',
    }
    if policy:
        data['policy'] = {'name': policy.get('name'), 'revision': policy.get('revision')}
    data['restrictions'] = dict(_restrictions)
    for name in sections:
        fn = SECTION_COLLECTORS.get(name)
        if fn:
            data[name] = _safe(fn)
    custom = _run_custom_commands(policy)
    if custom:
        data['custom'] = custom
    return data


def report_telemetry(server_url, serial_number, keep_snapshot=True, timeout=90, policy=None):
    try:
        payload = {'serial_number': serial_number,
                   'telemetry': gather_telemetry(policy),
                   'keep_snapshot': keep_snapshot}
        size = len(json.dumps(payload, default=str).encode('utf-8'))
        resp = requests.post(f'{server_url}/api/asset/telemetry/report',
                             json=payload, timeout=timeout)
        if resp.status_code == 200:
            logger.info('[TELEMETRY] Reported %s bytes', size)
            return True
        logger.warning('[TELEMETRY] HTTP %s', resp.status_code)
        return False
    except Exception as exc:
        logger.error('[TELEMETRY] Report failed: %s', exc)
        return False


# ══════════════════════════════════════════════════════════════════════════
# 3. LISTENING PORTS
# ══════════════════════════════════════════════════════════════════════════
def _ports_from_proc_net():
    """Read listening TCP ports straight from /proc/net/tcp{,6}.

    This is the bulletproof path: the file is world-readable, needs no
    external tool and no root. Used when psutil is blocked and neither ss nor
    netstat is installed.

    Format: local_address is HEX_IP:HEX_PORT, st == 0A means LISTEN.
    """
    ports = set()
    for path in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            with open(path) as fh:
                next(fh, None)          # skip header
                for line in fh:
                    parts = line.split()
                    if len(parts) < 4:
                        continue
                    if parts[3].upper() != '0A':   # 0A = TCP_LISTEN
                        continue
                    local = parts[1]
                    if ':' not in local:
                        continue
                    try:
                        ports.add(int(local.rsplit(':', 1)[1], 16))
                    except ValueError:
                        continue
        except Exception:
            continue
    return ports


def get_listening_ports():
    """TCP ports in LISTEN state.

    Four strategies, tried in order, because no single one works everywhere:

      1. psutil        — richest, but net_connections needs root on Linux
      2. ss            — standard on modern Ubuntu, absent on minimal images
      3. netstat       — older systems, part of net-tools which is often absent
      4. /proc/net/tcp — always present on Linux, always readable, no root

    Reads the local socket table. This is not a port scan; nothing is sent
    over the network.
    """
    ports = set()

    # 1. psutil
    if psutil:
        try:
            for conn in psutil.net_connections(kind='inet'):
                if conn.status == 'LISTEN' and conn.laddr:
                    ports.add(int(conn.laddr.port))
        except Exception:
            pass   # AccessDenied without root — fall through

    if ports:
        return sorted(ports)

    if IS_WINDOWS:
        out = _run(['netstat', '-ano', '-p', 'TCP'])
        if out:
            for line in out.splitlines():
                if 'LISTENING' not in line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    m = re.search(r':(\d+)$', parts[1])
                    if m:
                        ports.add(int(m.group(1)))
        return sorted(ports)

    # 2 and 3. ss, then netstat
    for cmd in (['ss', '-tln'], ['netstat', '-tln']):
        out = _run(cmd)
        if not out:
            continue
        for line in out.splitlines():
            if 'LISTEN' not in line.upper() and cmd[0] == 'netstat':
                continue
            # Match the port in the local-address column specifically
            for m in re.finditer(r'[\d.:\[\]a-fA-F*]+:(\d+)\s', line):
                ports.add(int(m.group(1)))
        if ports:
            return sorted(ports)

    # 4. /proc — cannot fail on Linux
    ports |= _ports_from_proc_net()
    return sorted(ports)


def report_listening_ports(server_url, serial_number, timeout=30):
    try:
        ports = get_listening_ports()
        resp = requests.post(f'{server_url}/api/asset/ports/report',
                             json={'serial_number': serial_number,
                                   'ports': ports},
                             timeout=timeout)
        if resp.status_code == 200:
            logger.info('[PORTS] Reported %s listening port(s)', len(ports))
            return True
        return False
    except Exception as exc:
        logger.error('[PORTS] Report failed: %s', exc)
        return False


# ══════════════════════════════════════════════════════════════════════════
# 4. OS UPDATES — platform-correct endpoint
# ══════════════════════════════════════════════════════════════════════════
def report_os_updates(server_url, serial_number, updates, installed,
                      timeout=60):
    """Post to /api/asset/os_updates/report, which routes by platform.

    The old /api/asset/updates/report writes to asset.windows.update
    unconditionally, which is why Ubuntu package names ended up filed as
    Windows KB numbers.

    updates:   [{'identifier': 'firefox', 'title': '...', 'severity': '...'}]
    installed: ['pkg1', 'pkg2']
    """
    try:
        resp = requests.post(
            f'{server_url}/api/asset/os_updates/report',
            json={'serial_number': serial_number,
                  'updates': updates,
                  'installed': installed},
            timeout=timeout)
        if resp.status_code == 200:
            body = resp.json()
            logger.info('[OS-UPDATE] Reported to %s model — %s new',
                        body.get('platform'), body.get('created'))
            return True
        logger.warning('[OS-UPDATE] HTTP %s', resp.status_code)
        return False
    except Exception as exc:
        logger.error('[OS-UPDATE] Report failed: %s', exc)
        return False


# ══════════════════════════════════════════════════════════════════════════
# Loops
# ══════════════════════════════════════════════════════════════════════════
def app_update_detect_loop(server_url, serial_number):
    logger.info('[APP-UPDATE] Detection loop started (%ss)',
                APP_UPDATE_CHECK_INTERVAL)
    time.sleep(60)      # let the primary sync settle first
    while True:
        try:
            report_app_updates(server_url, serial_number)
        except Exception as exc:
            logger.error('[APP-UPDATE] Detection loop error: %s', exc)
        time.sleep(APP_UPDATE_CHECK_INTERVAL)


def app_update_order_loop(server_url, serial_number):
    logger.info('[APP-UPDATE] Order loop started (%ss)',
                APP_UPDATE_POLL_INTERVAL)
    time.sleep(90)
    while True:
        try:
            process_app_update_orders(server_url, serial_number)
        except Exception as exc:
            logger.error('[APP-UPDATE] Order loop error: %s', exc)
        time.sleep(APP_UPDATE_POLL_INTERVAL)


def telemetry_loop(server_url, serial_number):
    """Sends what the machine's telemetry policy asks for, as often as it
    asks; the policy is fetched again every cycle, so a change in Odoo is
    picked up at the next report without touching the machine."""
    logger.info('[TELEMETRY] Loop started')
    time.sleep(45)
    run = 0
    while True:
        policy = fetch_telemetry_policy(server_url, serial_number)
        interval = int((policy or {}).get('interval') or TELEMETRY_INTERVAL)
        every = int((policy or {}).get('snapshot_every') or TELEMETRY_SNAPSHOT_EVERY)
        try:
            if policy is None or policy.get('enabled', True):
                run += 1
                report_telemetry(server_url, serial_number, keep_snapshot=(run % every) == 1, policy=policy)
        except Exception as exc:
            logger.error('[TELEMETRY] Loop error: %s', exc)
        time.sleep(max(interval, 30))


def ports_loop(server_url, serial_number):
    logger.info('[PORTS] Loop started (%ss)', PORTS_INTERVAL)
    time.sleep(120)
    while True:
        try:
            report_listening_ports(server_url, serial_number)
        except Exception as exc:
            logger.error('[PORTS] Loop error: %s', exc)
        time.sleep(PORTS_INTERVAL)


def start_all_extensions(server_url, serial_number):
    """Launch every extension loop as a daemon thread.

    Call once from main(), after your existing threads are started.
    """
    if not serial_number:
        logger.error('[EXTENSIONS] No serial number — extensions not started.')
        return

    server_url = server_url.rstrip('/')
    loops = [
        ('app-update-detect', app_update_detect_loop),
        ('app-update-orders', app_update_order_loop),
        ('telemetry', telemetry_loop),
        ('restrictions', restrictions_loop),
        ('self-update', agent_update_loop),
        ('screentime', screentime_loop),
        ('ports', ports_loop),
    ]
    for name, target in loops:
        threading.Thread(target=target, args=(server_url, serial_number),
                         daemon=True, name=name).start()
        logger.info('[EXTENSIONS] Started %s thread', name)

    logger.info('[EXTENSIONS] All %s extension loop(s) running', len(loops))


# ══════════════════════════════════════════════════════════════════════════
# Standalone test — run this file directly to check collection works
# ══════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    print('=' * 62)
    print('Agent extensions — self test')
    print('=' * 62)

    root = is_root()
    print(f'\nRunning as root: {"YES" if root else "NO"}')
    if not root:
        print('  WARNING: several collectors need root and will under-report.')
        print('  The agent runs as root via systemd, so production results')
        print('  will be better than this. For an accurate test:')
        print('      sudo python3 agent_extensions.py')

    print('\n[1] Application updates')
    source, updates = detect_app_updates()
    print(f'    source: {source}')
    print(f'    found : {len(updates)} outdated package(s)')
    for u in updates[:8]:
        print(f'      {u["app_name"]}: '
              f'{u["installed_version"]} -> {u["available_version"]}')
    if len(updates) > 8:
        print(f'      … and {len(updates) - 8} more')
    if not updates:
        print('    Nothing found. Either the machine is fully patched, or:')
        if not root:
            print('      - apt-get update needs root; rerun with sudo')
        print('      - check manually: apt list --upgradable')

    print('\n[2] Listening ports')
    ports = get_listening_ports()
    print(f'    {len(ports)} port(s): {ports[:20]}')
    if not ports:
        print('    Nothing found, which is very unlikely on a real machine.')
        print('    Check manually: ss -tln   or   cat /proc/net/tcp')

    print('\n[3] Telemetry')
    tel = gather_telemetry()
    size = len(json.dumps(tel, default=str).encode('utf-8'))
    print(f'    sections: {list(tel.keys())}')
    print(f'    size    : {size:,} bytes')
    sys_info = tel.get("system") or {}
    print(f'    hostname: {sys_info.get("hostname")}')
    print(f'    distro  : {sys_info.get("distro")}')

    empty = [k for k, v in tel.items() if v is None]
    if empty:
        print(f'    EMPTY sections: {", ".join(empty)}')
        if not root:
            print('    Most of these need root. Rerun with sudo.')

    raw = tel.get('raw') or {}
    missing_cmds = [k for k, v in raw.items() if not v]
    if missing_cmds:
        print(f'    Commands returning nothing: {", ".join(missing_cmds)}')
        print('    (tool not installed, or needs root)')

    print('\n' + '=' * 62)
    print('If all three sections show data, the collectors work.')
    print('Wire start_all_extensions() into main() to send it to Odoo.')
    print('=' * 62)
