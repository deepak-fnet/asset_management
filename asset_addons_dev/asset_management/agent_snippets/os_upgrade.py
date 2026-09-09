# -*- coding: utf-8 -*-
"""
AGENT-SIDE SNIPPET — add this to your Linux agent (agent.py).

Two independent pieces:

1. get_os_version_payload() — read /etc/os-release and add its version/
   codename to the payload your agent already sends on every regular sync
   (the one hitting /api/laptop_monitor). Paste its two keys into that
   existing payload dict. This is read-only and safe to call every cycle.

2. check_os_upgrade(...) — poll Odoo for a pending distro release upgrade
   (e.g. Ubuntu 24.04 -> 26.04) and, if one is due, run it. THIS IS
   DISRUPTIVE: it runs `do-release-upgrade`, which reboots the machine on
   success with no further confirmation. Read the whole "Why a state file"
   section below before wiring this in — a naive implementation loses
   track of the upgrade the moment the box reboots.

Server side: asset_management/models/asset_os_upgrade.py (the
asset.os.upgrade.request model) and the two routes in
controllers/asset_agent_api.py (/api/asset/os_upgrade/instructions,
/api/asset/os_upgrade/result). An admin creates + approves a request in
Odoo with a future maintenance-window start time; nothing here fires
before that.

Integration:
  1. Paste get_os_version_payload()'s two keys into your normal sync
     payload builder (wherever os_name/os_type/platform are already set).
  2. Call check_os_upgrade(SERVER_URL, SERIAL_NUMBER) once per sync cycle,
     from the SAME long-running daemon process that already runs as root
     (do-release-upgrade requires root). Do not call this from the
     per-user worker.
  3. Call it again on daemon STARTUP, before the first regular sync — this
     is what notices a reboot completed and finalizes the report. See
     "Why a state file" below.

Why a state file
-----------------
do-release-upgrade with the non-interactive frontend reboots the machine
itself once it finishes successfully - there is no "it's done, let me
report that" moment your own process gets to run before the reboot
happens. So this does NOT try to capture do-release-upgrade's exit code.
Instead:
  - Before starting, it writes STATE_FILE with the request_id/target.
  - The upgrade itself runs detached (survives this daemon being killed
    or restarted, e.g. by systemd, before the reboot).
  - After the box reboots and this daemon starts again, it checks
    STATE_FILE. If /etc/os-release now reports the target codename, that
    IS the success signal - reports 'done'. If too much time has passed
    and it still doesn't match, reports 'failed' with the upgrade log's
    tail as the error message.
This is deliberately how real fleet tools verify a release upgrade -
trying to capture a live exit code across a reboot is not reliable.
"""

import json
import logging
import os
import subprocess
import time

import requests

logger = logging.getLogger(__name__)

STATE_FILE = '/var/lib/asset_agent/os_upgrade_state.json'
UPGRADE_LOG = '/var/log/asset_agent_os_upgrade.log'
UPGRADE_SCRIPT = '/var/lib/asset_agent/run_os_upgrade.sh'

# How long to keep waiting for the target codename to show up before
# giving up and reporting failure. do-release-upgrade routinely takes
# 30-90 minutes even on a fast connection; this is deliberately generous.
UPGRADE_TIMEOUT_SECONDS = 3 * 60 * 60  # 3 hours


def _read_os_release():
    """Parse /etc/os-release into a dict. Empty dict if unreadable."""
    values = {}
    try:
        with open('/etc/os-release') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                values[key] = value.strip().strip('"')
    except OSError as exc:
        logger.debug('Could not read /etc/os-release: %s', exc)
    return values


def get_os_version_payload():
    """The two keys to merge into your regular sync payload.

    os_name (already sent elsewhere) is often just "Linux" from a generic
    platform library - PRETTY_NAME here is the actual distro string, e.g.
    "Ubuntu 24.04.4 LTS (Noble Numbat)".
    """
    release = _read_os_release()
    return {
        'os_version': release.get('PRETTY_NAME') or release.get('VERSION') or '',
        'os_codename': release.get('VERSION_CODENAME') or '',
    }


def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def _clear_state():
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


def _tail_log(path, lines=40):
    try:
        with open(path) as f:
            return ''.join(f.readlines()[-lines:])
    except OSError:
        return ''


def _report(server_url, serial_number, request_id, status, message=None,
            new_os_version=None, new_os_codename=None, timeout=30):
    try:
        response = requests.post(
            f'{server_url}/api/asset/os_upgrade/result',
            json={
                'serial_number': serial_number,
                'request_id': request_id,
                'status': status,
                'message': message,
                'new_os_version': new_os_version,
                'new_os_codename': new_os_codename,
            },
            timeout=timeout,
        )
        return response.status_code == 200
    except Exception as exc:
        logger.error('OS upgrade result report failed: %s', exc)
        return False


def _start_upgrade_process(target_codename):
    """Launch do-release-upgrade detached, so it outlives this daemon.

    start_new_session=True puts it in its own session (like nohup) - a
    systemd restart, or even a crash, of this agent will not kill it.
    """
    script = f"""#!/bin/bash
exec >> {UPGRADE_LOG} 2>&1
echo "=== OS upgrade to '{target_codename}' started at $(date) ==="
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
DEBIAN_FRONTEND=noninteractive apt-get -y install update-manager-core
# Prompt=lts so an LTS-to-LTS jump (the normal case, e.g. 24.04 -> 26.04)
# is offered at all - Prompt=normal would also offer non-LTS interim
# releases, which is very likely not what a fleet upgrade wants.
sed -i 's/^Prompt=.*/Prompt=lts/' /etc/update-manager/release-upgrades
DEBIAN_FRONTEND=noninteractive do-release-upgrade -f DistUpgradeViewNonInteractive
echo "=== do-release-upgrade returned $(date) - if this machine has not
already rebooted, the upgrade did not need one or did not complete ==="
"""
    os.makedirs(os.path.dirname(UPGRADE_SCRIPT), exist_ok=True)
    with open(UPGRADE_SCRIPT, 'w') as f:
        f.write(script)
    os.chmod(UPGRADE_SCRIPT, 0o700)

    subprocess.Popen(
        ['/bin/bash', UPGRADE_SCRIPT],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _resume_if_in_progress(server_url, serial_number):
    """Called at the top of check_os_upgrade() - finishes a run that was
    started before a reboot happened."""
    state = _load_state()
    if not state:
        return

    release = _read_os_release()
    current_codename = release.get('VERSION_CODENAME') or ''

    if current_codename == state['target_codename']:
        logger.info('OS upgrade to %s confirmed complete.', current_codename)
        _report(
            server_url, serial_number, state['request_id'], 'done',
            message='Confirmed via /etc/os-release after reboot.',
            new_os_version=release.get('PRETTY_NAME') or release.get('VERSION'),
            new_os_codename=current_codename,
        )
        _clear_state()
        return

    elapsed = time.time() - state.get('started_at', 0)
    if elapsed > UPGRADE_TIMEOUT_SECONDS:
        logger.warning(
            'OS upgrade to %s did not complete within %ss - reporting failed.',
            state['target_codename'], UPGRADE_TIMEOUT_SECONDS,
        )
        _report(
            server_url, serial_number, state['request_id'], 'failed',
            message='Timed out waiting for the target codename after '
                    f'{UPGRADE_TIMEOUT_SECONDS}s. Still on '
                    f'{current_codename or "unknown"}. Log tail:\n'
                    + _tail_log(UPGRADE_LOG),
        )
        _clear_state()
    # else: still plausibly running (or awaiting reboot) - check again
    # next cycle, no report sent yet.


def check_os_upgrade(server_url, serial_number, timeout=30):
    """Call this once per sync cycle, from the root daemon.

    Safe to call even when nothing is pending - it is a no-op GET unless
    an admin has approved a request whose maintenance window has arrived.
    """
    try:
        _resume_if_in_progress(server_url, serial_number)

        # Don't ask for new instructions while one is still being tracked -
        # a second concurrent do-release-upgrade would corrupt dpkg's
        # state, and _resume_if_in_progress above hasn't necessarily
        # cleared STATE_FILE yet on this same call.
        if _load_state():
            return

        response = requests.get(
            f'{server_url}/api/asset/os_upgrade/instructions',
            params={'serial_number': serial_number},
            timeout=timeout,
        )
        if response.status_code != 200:
            return
        data = response.json()
        if data.get('action') != 'upgrade':
            return

        request_id = data['request_id']
        target_codename = data['target_codename']
        logger.warning(
            'Starting OS upgrade to %s (request %s) - this WILL reboot '
            'the machine on success.', target_codename, request_id,
        )

        _report(server_url, serial_number, request_id, 'in_progress',
                message='Upgrade process launched.')
        _save_state({
            'request_id': request_id,
            'target_codename': target_codename,
            'started_at': time.time(),
        })
        _start_upgrade_process(target_codename)

    except Exception as exc:
        # Never let this take down the main sync loop.
        logger.error('OS upgrade check failed: %s', exc)
