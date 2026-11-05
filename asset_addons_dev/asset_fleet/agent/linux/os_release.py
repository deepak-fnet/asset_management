# -*- coding: utf-8 -*-
"""OS release reporting and distro release upgrades for the Ubuntu agent.

Two separate jobs, deliberately kept apart:

1. REPORTING (safe, runs constantly)
   - what release this machine is on, from /etc/os-release
   - whether a newer release is being OFFERED right now, from
     `do-release-upgrade -c`
   - what the next release IS, from Ubuntu's meta-release data

   The last two are not the same question and routinely disagree. Ubuntu
   does not open the LTS-to-LTS upgrade path on release day: 26.04.1 LTS
   ("resolute") can be published, fully described in meta-release, and
   still not be offered to a 24.04 box for months. Reporting only the
   boolean would hide that a next release exists; reporting only the
   version would imply the upgrade can be run today. Odoo shows both.

2. EXECUTION (disruptive, only on an approved+scheduled Odoo request)
   - polls /api/asset/os_upgrade/instructions
   - runs do-release-upgrade detached, survives agent restarts
   - confirms the result AFTER the reboot, via /etc/os-release

Why a state file for execution
------------------------------
do-release-upgrade reboots the machine itself on success - the agent never
gets a "finished, let me report it" moment before the reboot. So this does
not try to capture an exit code. It records the request in a state file
before starting, and on the next agent startup checks whether
/etc/os-release now reports the target codename. That match IS the success
signal. This is how fleet tools verify a release upgrade; capturing a live
exit code across a reboot is not reliable.
"""

import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:  # pragma: no cover - agent always ships requests
    requests = None

logger = logging.getLogger(__name__)

STATE_DIR = '/var/lib/asset_agent'
STATE_FILE = os.path.join(STATE_DIR, 'os_upgrade_state.json')
CHECK_CACHE_FILE = os.path.join(STATE_DIR, 'os_release_check.json')
UPGRADE_LOG = '/var/log/asset_agent_os_upgrade.log'
UPGRADE_SCRIPT = os.path.join(STATE_DIR, 'run_os_upgrade.sh')

META_RELEASE_FILES = (
    '/var/lib/update-manager/meta-release-lts',
    '/var/lib/update-manager/meta-release',
)

# `do-release-upgrade -c` reaches out to the network. The static sync runs
# far more often than the answer can change, so the check runs on its own
# schedule and the sync payload reads the cached result.
CHECK_INTERVAL_SECONDS = 6 * 60 * 60          # 6 hours
CHECK_TIMEOUT_SECONDS = 180

# do-release-upgrade routinely takes 30-90 minutes. Deliberately generous
# before we give up waiting for the target codename to appear.
UPGRADE_TIMEOUT_SECONDS = 3 * 60 * 60         # 3 hours
# Set by the upgrade script when the pre-upgrade updates need a reboot first
# (do-release-upgrade refuses to run with a reboot pending): after that
# reboot the agent starts the script again for the release upgrade itself.
RESUME_FLAG = os.path.join(STATE_DIR, 'os_upgrade_resume')
# Written by the upgrade script when do-release-upgrade did nothing; holds
# the reason, which is reported to Odoo straight away (no 3-hour wait).
FAILED_FLAG = os.path.join(STATE_DIR, 'os_upgrade_failed')


# ─────────────────────────────────────────────────────────────────────────
#  /etc/os-release
# ─────────────────────────────────────────────────────────────────────────
def read_os_release(path='/etc/os-release'):
    """Parse an os-release file into a dict. Empty dict if unreadable."""
    values = {}
    try:
        with open(path) as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError as exc:
        logger.debug('Could not read %s: %s', path, exc)
    return values


def get_os_version_payload():
    """The current-release keys for the regular sync payload.

    os_name elsewhere in the agent is whatever platform.platform() returns,
    which on Linux is a kernel string. PRETTY_NAME is the actual distro.
    """
    release = read_os_release()
    return {
        'os_version': release.get('PRETTY_NAME') or release.get('VERSION') or '',
        'os_codename': release.get('VERSION_CODENAME') or '',
    }


# ─────────────────────────────────────────────────────────────────────────
#  Is an upgrade offered / what is the next release
# ─────────────────────────────────────────────────────────────────────────
def _parse_meta_release(path):
    """Yield each release stanza in a meta-release file as a dict."""
    entries = []
    current = {}
    try:
        with open(path) as handle:
            for line in handle:
                line = line.rstrip('\n')
                if not line.strip():
                    if current:
                        entries.append(current)
                        current = {}
                    continue
                if ':' not in line:
                    continue
                key, _, value = line.partition(':')
                current[key.strip()] = value.strip()
    except OSError as exc:
        logger.debug('Could not read %s: %s', path, exc)
        return []
    if current:
        entries.append(current)
    return entries


def _version_sort_key(version_string):
    """'26.04.1 LTS' -> (26, 4, 1). Unparseable versions sort first."""
    numbers = re.findall(r'\d+', version_string or '')
    return tuple(int(n) for n in numbers[:3]) or (0,)


def find_next_release(current_codename=None, current_version=None):
    """The next distro release after this one, per Ubuntu's own data.

    Returns {'version': ..., 'codename': ..., 'supported': bool} or None.

    This deliberately ignores whether the upgrade is being OFFERED - that
    is what check_release_upgrade() answers. This one answers "what comes
    next", which stays useful (and visible in Odoo) during the months
    before the upgrade path opens.
    """
    release = read_os_release()
    current_codename = current_codename or release.get('VERSION_CODENAME') or ''
    current_version = current_version or release.get('VERSION_ID') or ''
    current_key = _version_sort_key(current_version)

    best = None
    for meta_file in META_RELEASE_FILES:
        if not os.path.exists(meta_file):
            continue
        for entry in _parse_meta_release(meta_file):
            codename = entry.get('Dist')
            version = entry.get('Version')
            if not codename or not version or codename == current_codename:
                continue
            key = _version_sort_key(version)
            if key <= current_key:
                continue
            # Nearest release above the current one, not the newest one -
            # do-release-upgrade only ever moves one release at a time.
            if best is None or key < best[0]:
                best = (key, {
                    'version': version,
                    'codename': codename,
                    'name': entry.get('Name') or '',
                    'supported': entry.get('Supported') == '1',
                })
    return best[1] if best else None


def _ensure_prompt_lts():
    """Force Prompt=lts in /etc/update-manager/release-upgrades.

    Prompt=never (a common fleet hardening default, to stop the desktop
    popup) makes `do-release-upgrade -c` ALWAYS report "not offered" - not
    because no release is available, but because the tool is configured
    never to look. That used to be a deadlock: the only place this got
    fixed was inside _start_upgrade_process(), which only runs AFTER this
    check already said "not offered", so Prompt=never blocked itself
    forever - every Odoo approval failed with "not being offered yet",
    with no way to reach the code that would have fixed it. Fixing Prompt
    here, before every check (not just before the real upgrade attempt),
    closes that loop.
    """
    try:
        subprocess.run(
            ['sed', '-i', 's/^Prompt=.*/Prompt=lts/',
             '/etc/update-manager/release-upgrades'],
            check=False, timeout=10,
        )
    except Exception as exc:
        logger.debug('Could not enforce Prompt=lts: %s', exc)


def check_release_upgrade(timeout=CHECK_TIMEOUT_SECONDS):
    """Run `do-release-upgrade -c` and interpret the result.

    Exit code is the authoritative signal: 0 means a new release is being
    offered, non-zero means it is not. The text is captured too, because
    "not offered" has several very different causes (no LTS yet, Prompt=
    never, no network) and an admin looking at Odoo needs to see which.
    """
    _ensure_prompt_lts()

    result = {
        'os_upgrade_available': False,
        'os_upgrade_check_output': '',
        'os_upgrade_checked_date': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
    }

    try:
        completed = subprocess.run(
            ['do-release-upgrade', '-c'],
            capture_output=True, text=True, timeout=timeout,
        )
        output = ((completed.stdout or '') + (completed.stderr or '')).strip()
        result['os_upgrade_check_output'] = output
        result['os_upgrade_available'] = completed.returncode == 0
    except FileNotFoundError:
        result['os_upgrade_check_output'] = (
            'do-release-upgrade is not installed (ubuntu-release-upgrader-core).')
    except subprocess.TimeoutExpired:
        result['os_upgrade_check_output'] = (
            'Release upgrade check timed out after %ss.' % timeout)
    except Exception as exc:  # never let a check break the sync
        result['os_upgrade_check_output'] = 'Release upgrade check failed: %s' % exc

    # What the next release IS, independent of whether it is offered.
    next_release = find_next_release()
    if next_release:
        result['os_upgrade_available_version'] = next_release['version']
        result['os_upgrade_available_codename'] = next_release['codename']
        if not result['os_upgrade_available'] and not next_release['supported']:
            result['os_upgrade_check_output'] = (
                (result['os_upgrade_check_output'] + '\n\n').lstrip()
                + 'Ubuntu %s (%s) exists but is not yet flagged as an available '
                  'upgrade for this release, so the upgrader will not offer it '
                  'yet. This is normal before the LTS-to-LTS path opens.'
                  % (next_release['version'], next_release['codename'])
            )
    else:
        result['os_upgrade_available_version'] = ''
        result['os_upgrade_available_codename'] = ''

    return result


# ─────────────────────────────────────────────────────────────────────────
#  Cached check, so the sync payload never waits on the network
# ─────────────────────────────────────────────────────────────────────────
def _read_json(path):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as handle:
            json.dump(data, handle)
    except OSError as exc:
        logger.debug('Could not write %s: %s', path, exc)


def refresh_release_check(force=False):
    """Re-run the check if the cache is stale. Returns the cached result."""
    cached = _read_json(CHECK_CACHE_FILE)
    if not force and cached:
        age = time.time() - cached.get('_checked_at', 0)
        if age < CHECK_INTERVAL_SECONDS:
            return cached

    result = check_release_upgrade()
    result['_checked_at'] = time.time()
    _write_json(CHECK_CACHE_FILE, result)
    logger.info(
        'Release upgrade check: offered=%s next=%s (%s)',
        result.get('os_upgrade_available'),
        result.get('os_upgrade_available_version') or 'none',
        result.get('os_upgrade_available_codename') or '-',
    )
    return result


def get_os_upgrade_payload():
    """Everything OS-release-related for the regular sync payload.

    Reads the cache only - never blocks the sync on do-release-upgrade.
    """
    payload = get_os_version_payload()
    cached = _read_json(CHECK_CACHE_FILE) or {}
    for key in ('os_upgrade_available', 'os_upgrade_available_version',
                'os_upgrade_available_codename', 'os_upgrade_check_output',
                'os_upgrade_checked_date'):
        if key in cached:
            payload[key] = cached[key]
    return payload


# ─────────────────────────────────────────────────────────────────────────
#  Execution of an approved upgrade
# ─────────────────────────────────────────────────────────────────────────
def _tail(path, lines=40):
    try:
        with open(path) as handle:
            return ''.join(handle.readlines()[-lines:])
    except OSError:
        return ''


def _report(server_url, serial_number, request_id, status, message=None,
            new_os_version=None, new_os_codename=None, timeout=30):
    if requests is None:
        return False
    try:
        response = requests.post(
            '%s/api/asset/os_upgrade/result' % server_url.rstrip('/'),
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


def _start_upgrade_process(target_codename, resumed=False):
    """Launch do-release-upgrade detached, so it outlives this agent.

    start_new_session=True gives it its own session (like nohup): a systemd
    restart of the agent - or the agent crashing - will not kill an upgrade
    that is halfway through replacing the system's packages.
    """
    script = """#!/bin/bash
exec >> {log} 2>&1
echo "=== OS upgrade to '{target}' started at $(date) ==="
# Fully unattended: no debconf questions, keep existing config files, and
# needrestart must neither show the "Pending kernel upgrade" dialog nor ask
# which services to restart.
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a NEEDRESTART_SUSPEND=1 UCF_FORCE_CONFFOLD=1
mkdir -p /etc/needrestart/conf.d
cat > /etc/needrestart/conf.d/99-asset-agent.conf <<'NR'
$nrconf{{restart}} = 'a';
$nrconf{{kernelhints}} = -1;
$nrconf{{ucodehints}} = 0;
NR
APT_OPTS="-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"
BEFORE=$(. /etc/os-release; echo "$VERSION_CODENAME")
rm -f {failed}
dpkg --configure -a
apt-get update
# dist-upgrade, not upgrade: a new kernel / firmware comes as NEW packages,
# which `upgrade` keeps back - and do-release-upgrade then refuses with
# "Please install all available updates for your release before upgrading".
apt-get $APT_OPTS -o APT::Get::Always-Include-Phased-Updates=true dist-upgrade
apt-get $APT_OPTS install ubuntu-release-upgrader-core
# Prompt=lts so an LTS-to-LTS jump is offered at all. Prompt=normal would
# also offer interim (9-month) releases, which is not what a managed fleet
# wants.
sed -i 's/^Prompt=.*/Prompt=lts/' /etc/update-manager/release-upgrades
if [ "$1" != "resume" ] && [ -f /var/run/reboot-required ]; then
    # do-release-upgrade will not start with a reboot pending: reboot into
    # the updated system first; the agent runs this script again after boot.
    echo "=== updates need a reboot first ($(tr '\n' ' ' < /var/run/reboot-required.pkgs 2>/dev/null)); rebooting, the release upgrade continues after boot"
    touch {resume}
    sync
    shutdown -r +1 "Asset agent: rebooting to finish updates before the OS upgrade"
    exit 0
fi
OUT=/tmp/asset_dru_output.txt
do-release-upgrade -f DistUpgradeViewNonInteractive 2>&1 | tee $OUT
echo "=== do-release-upgrade returned at $(date)"
# The non-interactive upgrader never reboots by itself (RealReboot=False).
AFTER=$(. /etc/os-release; echo "$VERSION_CODENAME")
if [ "$AFTER" != "$BEFORE" ]; then
    # Skip GNOME's one-time "upgraded to a new release" welcome wizard
    # (gnome-initial-setup --upgrade-user) for every desktop user.
    VER=$(. /etc/os-release; echo "$VERSION_ID")
    for HOME_DIR in /home/*; do
        U=$(stat -c %U "$HOME_DIR" 2>/dev/null) || continue
        [ -d "$HOME_DIR/.config" ] || continue
        install -d -o "$U" -g "$U" "$HOME_DIR/.config/gnome-initial-setup"
        install -o "$U" -g "$U" -m 644 /dev/null "$HOME_DIR/.config/gnome-initial-setup/upgrade-$VER-done"
    done
    echo "=== release $BEFORE -> $AFTER; rebooting in 1 minute"
    sync
    shutdown -r +1 "Asset agent: OS upgrade finished ($BEFORE -> $AFTER), restarting"
else
    echo "=== release unchanged ($BEFORE) - the upgrade did not run"
    {{ echo "do-release-upgrade did not upgrade (still $BEFORE). Its output:"; tail -15 $OUT; }} > {failed}
fi
""".format(log=UPGRADE_LOG, target=target_codename, resume=RESUME_FLAG, failed=FAILED_FLAG)

    os.makedirs(STATE_DIR, exist_ok=True)
    with open(UPGRADE_SCRIPT, 'w') as handle:
        handle.write(script)
    os.chmod(UPGRADE_SCRIPT, 0o700)

    subprocess.Popen(
        # own scope: outside asset-agent.service's cgroup and memory limit
        (['systemd-run', '--scope', '--quiet', '--collect', '--'] if os.path.isdir('/run/systemd/system') else [])
        + ['/bin/bash', UPGRADE_SCRIPT] + (['resume'] if resumed else []),
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _upgrade_script_running():
    """True while run_os_upgrade.sh (or the release upgrader) is alive."""
    try:
        out = subprocess.run(['pgrep', '-f', 'run_os_upgrade.sh|--mode=server --frontend=DistUpgrade'],
                             capture_output=True, text=True, timeout=10).stdout
        return bool(out.strip())
    except Exception:  # noqa: BLE001
        return False


def _resume_if_in_progress(server_url, serial_number):
    """Finish reporting an upgrade that was started before a reboot."""
    state = _read_json(STATE_FILE)
    if not state:
        return

    release = read_os_release()
    current_codename = release.get('VERSION_CODENAME') or ''

    if os.path.exists(FAILED_FLAG):
        try:
            with open(FAILED_FLAG) as handle:
                reason = handle.read()
        except OSError:
            reason = ''
        logger.warning('OS upgrade to %s did not run: %s', state.get('target_codename'), reason[:200])
        _report(server_url, serial_number, state['request_id'], 'failed',
                message='%s\n\n%s' % (reason, _tail(UPGRADE_LOG)))
        for path in (FAILED_FLAG, STATE_FILE, RESUME_FLAG):
            try:
                os.remove(path)
            except OSError:
                pass
        return

    if os.path.exists(RESUME_FLAG) and current_codename != state.get('target_codename'):
        # only once the machine has actually rebooted since the flag was set
        try:
            with open('/proc/uptime') as handle:
                booted_at = time.time() - float(handle.read().split()[0])
        except (OSError, ValueError):
            booted_at = 0
        if os.path.getmtime(RESUME_FLAG) < booted_at:
            os.remove(RESUME_FLAG)
            logger.warning('Rebooted after updates - starting the release upgrade to %s now.',
                           state.get('target_codename'))
            _report(server_url, serial_number, state['request_id'], 'in_progress',
                    message='Updates installed and machine rebooted; release upgrade started.')
            _start_upgrade_process(state.get('target_codename'), resumed=True)
        return

    # The upgrade rewrites /etc/os-release (base-files) long before it has
    # finished configuring packages: never call it done while the upgrade
    # script is still running - wait for it to end and reboot.
    if _upgrade_script_running():
        return

    if current_codename and current_codename == state.get('target_codename'):
        logger.info('OS upgrade to %s confirmed complete.', current_codename)
        _report(
            server_url, serial_number, state['request_id'], 'done',
            message='Confirmed via /etc/os-release after reboot.',
            new_os_version=release.get('PRETTY_NAME') or release.get('VERSION'),
            new_os_codename=current_codename,
        )
        try:
            os.remove(STATE_FILE)
        except OSError:
            pass
        return

    if time.time() - state.get('started_at', 0) > UPGRADE_TIMEOUT_SECONDS:
        logger.warning('OS upgrade to %s timed out.', state.get('target_codename'))
        _report(
            server_url, serial_number, state['request_id'], 'failed',
            message='Timed out waiting for the target codename. Still on %s.\n\n%s'
                    % (current_codename or 'unknown', _tail(UPGRADE_LOG)),
        )
        try:
            os.remove(STATE_FILE)
        except OSError:
            pass


def check_os_upgrade(server_url, serial_number, timeout=30):
    """Poll Odoo for an approved upgrade and run it if one is due.

    A no-op unless an admin has approved a request AND its maintenance
    window has arrived - the server decides that, not the agent.
    """
    if requests is None:
        return
    try:
        _resume_if_in_progress(server_url, serial_number)

        # Never ask for new instructions while one is still being tracked:
        # two concurrent do-release-upgrade runs would corrupt dpkg state.
        if _read_json(STATE_FILE):
            return

        response = requests.get(
            '%s/api/asset/os_upgrade/instructions' % server_url.rstrip('/'),
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

        # Confirm the upgrade is actually being offered BEFORE touching the
        # system. Odoo lets an admin approve a request whenever they like -
        # including months before Ubuntu opens the LTS-to-LTS path - and
        # without this check that approval would: run a full `apt-get -y
        # upgrade` nobody asked for, then watch do-release-upgrade decline to
        # do anything, then sit for three hours before finally reporting a
        # failure. Fail immediately and say why instead.
        offered = check_release_upgrade()
        if not offered.get('os_upgrade_available'):
            logger.warning(
                'Upgrade request %s ignored: no release upgrade is being '
                'offered on this machine yet.', request_id,
            )
            _report(
                server_url, serial_number, request_id, 'failed',
                message='This machine is not being offered a release upgrade '
                        'yet, so nothing was run and the system was left '
                        'untouched. Re-approve once it is offered.\n\n%s'
                        % (offered.get('os_upgrade_check_output') or ''),
            )
            return

        logger.warning(
            'Starting OS upgrade to %s (request %s) - this WILL reboot the '
            'machine on success.', target_codename, request_id,
        )

        _report(server_url, serial_number, request_id, 'in_progress',
                message='Upgrade process launched by the agent.')
        _write_json(STATE_FILE, {
            'request_id': request_id,
            'target_codename': target_codename,
            'started_at': time.time(),
        })
        _start_upgrade_process(target_codename)

    except Exception as exc:
        logger.error('OS upgrade check failed: %s', exc)


POLL_INTERVAL_SECONDS = 300  # 5 minutes


def os_release_loop(server_url, get_serial_number, interval=POLL_INTERVAL_SECONDS):
    """Background thread: keep the availability cache warm, run approved upgrades.

    Ticks on the SHORT interval, because the two things it does have very
    different costs and urgencies:

      - check_os_upgrade() is a cheap GET and must be responsive: it is how
        an admin's approval in Odoo actually reaches the machine, and how a
        completed upgrade gets confirmed after a reboot. Waiting a whole
        availability-check period for that would make approving an upgrade
        feel broken.
      - refresh_release_check() shells out to do-release-upgrade over the
        network, so it self-throttles: the call below is a no-op until its
        cache is older than CHECK_INTERVAL_SECONDS.
    """
    logger.info(
        '[OS RELEASE] loop started (poll: %ss, availability check: %ss)',
        interval, CHECK_INTERVAL_SECONDS,
    )
    while True:
        try:
            refresh_release_check()
        except Exception as exc:
            logger.error('[OS RELEASE] availability check failed: %s', exc)

        try:
            serial_number = get_serial_number() if callable(get_serial_number) else get_serial_number
            if serial_number:
                check_os_upgrade(server_url, serial_number)
        except Exception as exc:
            logger.error('[OS RELEASE] upgrade poll failed: %s', exc)

        time.sleep(interval)
