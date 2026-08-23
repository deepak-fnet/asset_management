# -*- coding: utf-8 -*-
"""
AGENT SNIPPET — comprehensive system telemetry.

Collects far more than the fixed fields the agent currently reports, bundles
it into one JSON object, and posts it to Odoo. Odoo stores it verbatim and
renders it as a browsable list — no Odoo-side changes needed when you add a
new section here.

Integration
-----------
1. Paste into your existing agent.py
2. Add to main():

       threading.Thread(target=telemetry_sync_loop, daemon=True).start()

3. Set TELEMETRY_URL to match your server.

What it collects
----------------
    system      hostname, kernel, distro, uptime, boot time, timezone
    cpu         model, cores, per-core usage, load average, frequency
    memory      total/used/free/cached/swap, in bytes and human form
    disks       per-mount usage, filesystem type, IO counters
    processes   top 15 by CPU and by memory
    network     interfaces, addresses, IO counters, listening ports
    services    failed systemd units
    users       logged-in sessions
    packages    counts by manager, pending updates
    hardware    sensors, battery, DMI
    raw         verbatim output of df -h, free -h, uptime, etc.

Everything is best-effort. A missing tool yields a null section rather than
an exception — telemetry must never break the agent.
"""

import json
import logging
import os
import platform
import socket
import subprocess
import time
from datetime import datetime, timezone

import requests

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger(__name__)

TELEMETRY_URL = "http://10.189.128.71:8015/api/asset/telemetry/report"
TELEMETRY_INTERVAL = 900          # 15 minutes
TELEMETRY_SNAPSHOT_EVERY = 4      # keep history every Nth run (= hourly)


def _run(cmd, timeout=15, shell=False):
    """Run a command, return stdout, or None. Never raises."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, shell=shell,
        )
        out = (result.stdout or '').strip()
        return out or None
    except Exception:
        return None


def _safe(fn, default=None):
    """Call a collector, swallow any failure."""
    try:
        return fn()
    except Exception as exc:
        logger.debug('Telemetry collector %s failed: %s',
                     getattr(fn, '__name__', fn), exc)
        return default


def _bytes_human(n):
    if n is None:
        return None
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(n) < 1024.0:
            return f'{n:.1f} {unit}'
        n /= 1024.0
    return f'{n:.1f} PB'


# ══════════════════════════════════════════════════════════════════════════
# Collectors
# ══════════════════════════════════════════════════════════════════════════
def collect_system():
    boot = psutil.boot_time() if psutil else None
    return {
        'hostname': socket.gethostname(),
        'fqdn': socket.getfqdn(),
        'platform': platform.system(),
        'kernel': platform.release(),
        'kernel_full': platform.version(),
        'architecture': platform.machine(),
        'python_version': platform.python_version(),
        'distro': _run(['lsb_release', '-ds']) or _run(
            ['sh', '-c', 'cat /etc/os-release | grep PRETTY_NAME']),
        'boot_time': datetime.fromtimestamp(boot, timezone.utc).isoformat()
                     if boot else None,
        'uptime_seconds': int(time.time() - boot) if boot else None,
        'timezone': time.tzname[0] if time.tzname else None,
        'current_time': datetime.now(timezone.utc).isoformat(),
    }


def collect_cpu():
    if not psutil:
        return None
    freq = psutil.cpu_freq()
    try:
        load1, load5, load15 = os.getloadavg()
    except Exception:
        load1 = load5 = load15 = None
    return {
        'model': _run(['sh', '-c',
                       "lscpu | grep 'Model name' | cut -d: -f2"]) or
                 platform.processor(),
        'physical_cores': psutil.cpu_count(logical=False),
        'logical_cores': psutil.cpu_count(logical=True),
        'usage_percent_total': psutil.cpu_percent(interval=0.5),
        'usage_percent_per_core': psutil.cpu_percent(interval=0.3, percpu=True),
        'frequency_mhz': {
            'current': round(freq.current, 1) if freq else None,
            'min': round(freq.min, 1) if freq else None,
            'max': round(freq.max, 1) if freq else None,
        },
        'load_average': {'1min': load1, '5min': load5, '15min': load15},
        'times_percent': psutil.cpu_times_percent(interval=0.3)._asdict()
                         if hasattr(psutil.cpu_times_percent(interval=0),
                                    '_asdict') else None,
    }


def collect_memory():
    if not psutil:
        return None
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return {
        'virtual': {
            'total_bytes': vm.total, 'total_human': _bytes_human(vm.total),
            'available_bytes': vm.available,
            'available_human': _bytes_human(vm.available),
            'used_bytes': vm.used, 'used_human': _bytes_human(vm.used),
            'free_bytes': vm.free,
            'percent': vm.percent,
            'cached_bytes': getattr(vm, 'cached', None),
            'buffers_bytes': getattr(vm, 'buffers', None),
        },
        'swap': {
            'total_bytes': sw.total, 'total_human': _bytes_human(sw.total),
            'used_bytes': sw.used, 'percent': sw.percent,
        },
    }


def collect_disks():
    if not psutil:
        return None
    mounts = []
    skip_fs = {'tmpfs', 'devtmpfs', 'squashfs', 'overlay', 'proc', 'sysfs'}
    for part in psutil.disk_partitions(all=False):
        if part.fstype in skip_fs:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        mounts.append({
            'device': part.device,
            'mount': part.mountpoint,
            'fstype': part.fstype,
            'options': part.opts,
            'total_bytes': usage.total, 'total_human': _bytes_human(usage.total),
            'used_bytes': usage.used, 'used_human': _bytes_human(usage.used),
            'free_bytes': usage.free, 'free_human': _bytes_human(usage.free),
            'percent': usage.percent,
        })

    io = None
    try:
        counters = psutil.disk_io_counters(perdisk=True)
        io = {name: c._asdict() for name, c in counters.items()}
    except Exception:
        pass

    return {'mounts': mounts, 'io_counters': io}


def collect_processes(top_n=15):
    if not psutil:
        return None
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent',
                                  'memory_percent', 'status', 'create_time']):
        try:
            info = p.info
            procs.append({
                'pid': info['pid'],
                'name': info['name'],
                'user': info['username'],
                'cpu_percent': info['cpu_percent'],
                'memory_percent': round(info['memory_percent'] or 0, 2),
                'status': info['status'],
            })
        except Exception:
            continue

    by_cpu = sorted(procs, key=lambda x: x['cpu_percent'] or 0, reverse=True)
    by_mem = sorted(procs, key=lambda x: x['memory_percent'] or 0, reverse=True)
    return {
        'total_count': len(procs),
        'top_by_cpu': by_cpu[:top_n],
        'top_by_memory': by_mem[:top_n],
    }


def collect_network():
    if not psutil:
        return None
    interfaces = {}
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        for name, addr_list in addrs.items():
            st = stats.get(name)
            interfaces[name] = {
                'is_up': st.isup if st else None,
                'speed_mbps': st.speed if st else None,
                'mtu': st.mtu if st else None,
                'addresses': [
                    {'family': str(a.family), 'address': a.address,
                     'netmask': a.netmask, 'broadcast': a.broadcast}
                    for a in addr_list
                ],
            }
    except Exception:
        pass

    io = None
    try:
        io = {n: c._asdict() for n, c in
              psutil.net_io_counters(pernic=True).items()}
    except Exception:
        pass

    listening = []
    try:
        for conn in psutil.net_connections(kind='inet'):
            if conn.status == 'LISTEN' and conn.laddr:
                listening.append({
                    'port': conn.laddr.port,
                    'address': conn.laddr.ip,
                    'pid': conn.pid,
                })
    except Exception:
        # Needs root on some systems; fall back to ss output in `raw`
        pass

    return {
        'interfaces': interfaces,
        'io_counters': io,
        'listening_ports': sorted(listening, key=lambda x: x['port']),
    }


def collect_services():
    failed_raw = _run(['systemctl', 'list-units', '--failed',
                       '--no-legend', '--no-pager'])
    failed = []
    if failed_raw:
        for line in failed_raw.splitlines():
            parts = line.split(None, 4)
            if parts:
                failed.append({
                    'unit': parts[0].lstrip('●').strip(),
                    'description': parts[4] if len(parts) > 4 else '',
                })
    running_count = _run(['sh', '-c',
                          'systemctl list-units --type=service --state=running '
                          '--no-legend --no-pager | wc -l'])
    return {
        'failed_units': failed,
        'failed_count': len(failed),
        'running_service_count': int(running_count) if running_count
                                 and running_count.isdigit() else None,
    }


def collect_users():
    if not psutil:
        return None
    sessions = []
    try:
        for u in psutil.users():
            sessions.append({
                'name': u.name,
                'terminal': u.terminal,
                'host': u.host,
                'started': datetime.fromtimestamp(
                    u.started, timezone.utc).isoformat(),
            })
    except Exception:
        pass
    return {'active_sessions': sessions, 'count': len(sessions)}


def collect_packages():
    counts = {}
    dpkg = _run(['sh', '-c', "dpkg -l 2>/dev/null | grep -c '^ii'"])
    if dpkg and dpkg.isdigit():
        counts['dpkg'] = int(dpkg)
    snap = _run(['sh', '-c', 'snap list 2>/dev/null | tail -n +2 | wc -l'])
    if snap and snap.isdigit():
        counts['snap'] = int(snap)
    flatpak = _run(['sh', '-c', 'flatpak list --app 2>/dev/null | wc -l'])
    if flatpak and flatpak.isdigit():
        counts['flatpak'] = int(flatpak)

    upgradable = _run(['sh', '-c',
                       "apt list --upgradable 2>/dev/null | tail -n +2 | wc -l"])
    security = _run(['sh', '-c',
                     "apt list --upgradable 2>/dev/null | grep -c security"])

    return {
        'installed_counts': counts,
        'upgradable_count': int(upgradable) if upgradable
                            and upgradable.isdigit() else None,
        'security_upgradable_count': int(security) if security
                                     and security.isdigit() else None,
        'reboot_required': os.path.exists('/var/run/reboot-required'),
    }


def collect_hardware():
    sensors = None
    if psutil and hasattr(psutil, 'sensors_temperatures'):
        try:
            temps = psutil.sensors_temperatures()
            sensors = {
                name: [{'label': e.label, 'current': e.current,
                        'high': e.high, 'critical': e.critical} for e in entries]
                for name, entries in temps.items()
            }
        except Exception:
            pass

    battery = None
    if psutil:
        try:
            b = psutil.sensors_battery()
            if b:
                battery = {
                    'percent': round(b.percent, 1),
                    'plugged_in': b.power_plugged,
                    'seconds_left': b.secsleft if b.secsleft > 0 else None,
                }
        except Exception:
            pass

    def dmi(field):
        path = f'/sys/class/dmi/id/{field}'
        try:
            with open(path) as fh:
                return fh.read().strip()
        except Exception:
            return None

    return {
        'temperatures': sensors,
        'battery': battery,
        'dmi': {
            'vendor': dmi('sys_vendor'),
            'product': dmi('product_name'),
            'serial': dmi('product_serial'),
            'bios_version': dmi('bios_version'),
            'bios_date': dmi('bios_date'),
            'chassis_type': dmi('chassis_type'),
        },
    }


def collect_raw_commands():
    """Verbatim output of the commands an admin would actually run.

    Kept separate so the structured sections stay clean, and so an admin can
    read exactly what they would see on the machine.
    """
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
        'last_reboots': _run(['sh', '-c', 'last -x reboot 2>/dev/null | head -5']),
        'dmesg_errors': _run(['sh', '-c',
                              'dmesg --level=err,crit 2>/dev/null | tail -20']),
        'who': _run(['who']),
        'mount': _run(['sh', '-c', 'mount | grep -v snap | head -30']),
    }


# ══════════════════════════════════════════════════════════════════════════
# Assembly and transport
# ══════════════════════════════════════════════════════════════════════════
def gather_telemetry():
    """Build the complete payload. Every section is best-effort."""
    return {
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'collector_version': '1.0',
        'system': _safe(collect_system),
        'cpu': _safe(collect_cpu),
        'memory': _safe(collect_memory),
        'disks': _safe(collect_disks),
        'processes': _safe(collect_processes),
        'network': _safe(collect_network),
        'services': _safe(collect_services),
        'users': _safe(collect_users),
        'packages': _safe(collect_packages),
        'hardware': _safe(collect_hardware),
        'raw': _safe(collect_raw_commands),
    }


def report_telemetry(server_url, serial_number, keep_snapshot=True, timeout=60):
    """Collect and POST. Returns True on success."""
    try:
        payload = {
            'serial_number': serial_number,
            'telemetry': gather_telemetry(),
            'keep_snapshot': keep_snapshot,
        }
        size = len(json.dumps(payload, default=str).encode('utf-8'))
        logger.info('[TELEMETRY] Sending %s bytes', size)

        resp = requests.post(f'{server_url}/api/asset/telemetry/report',
                             json=payload, timeout=timeout)
        if resp.status_code == 200:
            logger.info('[TELEMETRY] Reported OK')
            return True
        logger.warning('[TELEMETRY] HTTP %s', resp.status_code)
        return False
    except Exception as exc:
        # Telemetry is diagnostic. It must never break the agent.
        logger.error('[TELEMETRY] Report failed: %s', exc)
        return False


def telemetry_sync_loop(server_url, serial_number):
    """Background thread. Add to main() with threading.Thread(...)."""
    logger.info('[TELEMETRY] Sync loop started (interval: %ss)',
                TELEMETRY_INTERVAL)
    time.sleep(45)   # let the agent finish its initial sync first

    run = 0
    while True:
        try:
            run += 1
            # Only keep history every Nth run, so the snapshot table stays small
            keep = (run % TELEMETRY_SNAPSHOT_EVERY) == 1
            report_telemetry(server_url, serial_number, keep_snapshot=keep)
        except Exception as exc:
            logger.error('[TELEMETRY] Loop error: %s', exc)
        time.sleep(TELEMETRY_INTERVAL)
