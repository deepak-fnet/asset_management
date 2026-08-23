# -*- coding: utf-8 -*-
"""
AGENT SNIPPET — third-party application updates.

Paste into your existing agent.py and call once per sync cycle:

    check_and_report_app_updates(SERVER_URL, SERIAL_NUMBER)
    process_app_update_instructions(SERVER_URL, SERIAL_NUMBER)

Why winget / apt / brew rather than version tracking
----------------------------------------------------
These tools already know what is outdated. Building a version-comparison
engine would mean scraping vendor sites for "latest version" — fragile and
endless maintenance. `winget upgrade` gives the answer directly.

Requirements
------------
Windows : winget (App Installer). Present on Win 10 1809+ / Win 11.
          Verify with `winget --version`.
Linux   : apt (Debian/Ubuntu)
macOS   : Homebrew, optional
"""

import json
import logging
import platform
import re
import subprocess

import requests

logger = logging.getLogger(__name__)

WIN_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _run(cmd, timeout=180, shell=False):
    """Run a command, return stdout or None."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            shell=shell, creationflags=WIN_FLAGS if platform.system() == 'Windows' else 0,
        )
        return result.stdout if result.returncode in (0, 1) else None
    except Exception as exc:
        logger.debug('Command failed %r: %s', cmd, exc)
        return None


# ══════════════════════════════════════════════════════════════════════════
# Detection
# ══════════════════════════════════════════════════════════════════════════
def get_winget_upgrades():
    """Parse `winget upgrade` output.

    Sample:
        Name              Id                 Version      Available   Source
        ---------------------------------------------------------------------
        Google Chrome     Google.Chrome      141.0.7390   142.0.7444  winget
    """
    out = _run(['winget', 'upgrade', '--include-unknown',
                '--accept-source-agreements'])
    if not out:
        return []

    lines = out.splitlines()

    # Locate the header separator row of dashes.
    sep_idx = next(
        (i for i, ln in enumerate(lines) if set(ln.strip()) == {'-'} and len(ln.strip()) > 10),
        None,
    )
    if sep_idx is None or sep_idx == 0:
        return []

    header = lines[sep_idx - 1]
    # winget pads columns, so derive column offsets from the header.
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
        # Trailing summary lines like "3 upgrades available."
        if re.match(r'^\s*\d+\s+upgrade', line, re.IGNORECASE):
            continue
        try:
            name = line[:col_id].strip()
            pkg_id = line[col_id:col_ver].strip()
            installed = line[col_ver:col_avail].strip()
            available = line[col_avail:].strip().split()[0] if line[col_avail:].strip() else ''
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


def get_apt_upgrades():
    """Parse `apt list --upgradable`.

    Sample:
        firefox/noble-updates 132.0+build2-0ubuntu0.24.04.1 amd64
            [upgradable from: 131.0+build2-0ubuntu0.24.04.1]
    """
    _run(['apt-get', 'update', '-qq'], timeout=120)
    out = _run(['apt', 'list', '--upgradable'])
    if not out:
        return []

    updates = []
    for line in out.splitlines():
        if '/' not in line or 'upgradable from' not in line:
            continue
        try:
            pkg = line.split('/')[0].strip()
            available = line.split()[1]
            m = re.search(r'upgradable from:\s*([^\]]+)', line)
            installed = m.group(1).strip() if m else ''
            updates.append({
                'app_name': pkg,
                'package_id': pkg,
                'installed_version': installed,
                'available_version': available,
            })
        except Exception:
            continue
    return updates


def get_brew_upgrades():
    """Parse `brew outdated --json`."""
    out = _run(['brew', 'outdated', '--json=v2'])
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
    """Return (source, updates) for the current platform."""
    system = platform.system()
    if system == 'Windows':
        return 'winget', get_winget_upgrades()
    if system == 'Linux':
        return 'apt', get_apt_upgrades()
    if system == 'Darwin':
        return 'brew', get_brew_upgrades()
    return 'other', []


# ══════════════════════════════════════════════════════════════════════════
# Reporting
# ══════════════════════════════════════════════════════════════════════════
def check_and_report_app_updates(server_url, serial_number, timeout=60):
    """Detect outdated apps and POST them to Odoo."""
    try:
        source, updates = detect_app_updates()
        payload = {
            'serial_number': serial_number,
            'source': source,
            'updates': updates,
        }
        resp = requests.post(
            f'{server_url}/api/asset/app_updates/report',
            json=payload, timeout=timeout,
        )
        if resp.status_code == 200:
            logger.info('Reported %s outdated application(s)', len(updates))
            return True
        logger.warning('App update report returned HTTP %s', resp.status_code)
        return False
    except Exception as exc:
        # Diagnostic data only — never break the main sync loop.
        logger.error('App update report failed: %s', exc)
        return False


# ══════════════════════════════════════════════════════════════════════════
# Execution
# ══════════════════════════════════════════════════════════════════════════
def upgrade_app(package_id, app_name, source):
    """Perform one upgrade. Returns (success, new_version, error)."""
    try:
        if source == 'winget':
            target = package_id or app_name
            out = _run([
                'winget', 'upgrade', '--id', target, '--silent',
                '--accept-package-agreements', '--accept-source-agreements',
            ], timeout=1800)
            if out is None:
                return False, '', 'winget returned a non-zero exit code'
            ok = 'successfully installed' in out.lower() or \
                 'successfully upgraded' in out.lower()
            return ok, '', '' if ok else out[-400:]

        if source == 'apt':
            out = _run(
                f'DEBIAN_FRONTEND=noninteractive apt-get install -y '
                f'--only-upgrade {package_id}',
                timeout=1800, shell=True,
            )
            if out is None:
                return False, '', 'apt-get returned a non-zero exit code'
            ver = _run(['dpkg-query', '-W', '-f=${Version}', package_id]) or ''
            return True, ver.strip(), ''

        if source == 'brew':
            out = _run(['brew', 'upgrade', package_id], timeout=1800)
            if out is None:
                return False, '', 'brew returned a non-zero exit code'
            return True, '', ''

        return False, '', f'Unsupported source: {source}'

    except Exception as exc:
        return False, '', str(exc)


def process_app_update_instructions(server_url, serial_number, timeout=60):
    """Ask Odoo what to upgrade, do it, report results."""
    try:
        resp = requests.get(
            f'{server_url}/api/asset/app_updates/instructions',
            params={'serial_number': serial_number}, timeout=timeout,
        )
        if resp.status_code != 200:
            return False

        data = resp.json()
        if not data.get('success'):
            return False

        for job in data.get('upgrade_list', []):
            app_name = job.get('app_name', '')
            logger.info('Upgrading %s…', app_name)
            ok, new_version, error = upgrade_app(
                job.get('package_id', ''), app_name, job.get('source', 'winget')
            )
            requests.post(
                f'{server_url}/api/asset/app_updates/result',
                json={
                    'serial_number': serial_number,
                    'app_name': app_name,
                    'success': ok,
                    'new_version': new_version,
                    'error': error,
                },
                timeout=timeout,
            )
            logger.info('%s upgrade %s', app_name, 'OK' if ok else 'FAILED')

        return True

    except Exception as exc:
        logger.error('App update instruction processing failed: %s', exc)
        return False


# ══════════════════════════════════════════════════════════════════════════
# Listening ports — feeds the per-asset vulnerability report
# ══════════════════════════════════════════════════════════════════════════
def get_listening_ports():
    """Return a sorted list of TCP ports in LISTEN state.

    Read-only. This is not a port scan — it inspects the local socket table,
    which is why it needs no elevated network privileges and touches nothing
    on the network.
    """
    ports = set()
    system = platform.system()

    if system == 'Windows':
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
    else:
        out = _run(['ss', '-tlnH']) or _run(['netstat', '-tln'])
        if out:
            for line in out.splitlines():
                m = re.search(r':(\d+)\s', line)
                if m:
                    ports.add(int(m.group(1)))

    return sorted(ports)


def report_listening_ports(server_url, serial_number, timeout=30):
    try:
        resp = requests.post(
            f'{server_url}/api/asset/ports/report',
            json={'serial_number': serial_number,
                  'ports': get_listening_ports()},
            timeout=timeout,
        )
        return resp.status_code == 200
    except Exception as exc:
        logger.error('Port report failed: %s', exc)
        return False
