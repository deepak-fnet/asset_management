# -*- coding: utf-8 -*-
"""OS-level security event detection for the Ubuntu agent.

Poll-based, matching this agent's existing style throughout (os_release.py,
the OS-update sync, etc.) - no PAM hooks, no auditd rules, no changes to the
target machine's own logging config. Runs from agent.py's main daemon
(which is root - see its systemd unit, no `User=` line), because reading
/etc/shadow and seeing every user's login session (not just one desktop
user's own) both need root. This is why it lives here and not in
asset_remote_worker.py, which runs as the logged-in desktop user.

Detects, once per POLL_INTERVAL_SECONDS:
  - user login / logout                  (`who`, diffed against last poll)
  - failed login attempts                (journalctl, PAM auth failures)
  - username changed                     (/etc/passwd, diffed by uid)
  - user account created / deleted       (/etc/passwd, diffed by uid)
  - password changed                     (/etc/shadow's last-changed field)
  - sudo/admin group membership granted  (/etc/group, diffed)

Reports each detected event through report_security_event() to
/api/asset/security/report - one endpoint for all of these (see that
route's own docstring for why), not one per event type. The chmod-bypass
check (a locked path's permissions getting overridden) is NOT here: that
needs agent.py's own _file_access_policy state, which already lives in
agent.py's file-access code - see the periodic re-check added there instead
of duplicating that state in this module.
"""

import json
import logging
import os
import re
import subprocess
import time

try:
    import requests
except ImportError:  # pragma: no cover - agent always ships requests
    requests = None

logger = logging.getLogger(__name__)

STATE_DIR = '/var/lib/asset_agent'
STATE_FILE = os.path.join(STATE_DIR, 'security_audit_state.json')

POLL_INTERVAL_SECONDS = 60

# Real user accounts start at 1000 on Debian/Ubuntu - system/service
# accounts (root, daemon, sshd, ...) are below this and are not
# interesting for "account created/deleted" reporting.
MIN_REAL_UID = 1000

SUDO_GROUPS = ('sudo', 'admin', 'wheel')

# journalctl lines matching this were already reported once - a plain
# "since last check" window can still re-read a line that arrived right at
# the boundary. Timestamp-based dedup (below) is the real guard; this is a
# fallback so a clock jump can't cause a flood.
_FAILED_LOGIN_PATTERNS = re.compile(
    r'Failed password for(?: invalid user)? (\S+)|'
    r'authentication failure.*user=(\S+)|'
    r'pam_unix\(.*\): authentication failure.*ruser=(\S+)',
)


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f)
    except OSError as exc:
        logger.debug('Could not write %s: %s', path, exc)


def report_security_event(server_url, serial_number, action, description,
                          old_value=None, new_value=None, timeout=15,
                          db_name=None):
    """POST one security event to Odoo. Shared with agent.py's file-access
    code (chmod-bypass detection) so there is exactly one place that knows
    the request shape - see /api/asset/security/report's own docstring.

    db_name is passed through from agent.py's own ODOO_DB_NAME - on a
    multi-DB box, a request with no session cookie and no X-Odoo-Database
    header 404s with "No database is selected" before it ever reaches the
    route, so this can't be left off just because a single-DB box wouldn't
    need it."""
    if requests is None:
        return False
    headers = {'X-Odoo-Database': db_name} if db_name else None
    try:
        response = requests.post(
            '%s/api/asset/security/report' % server_url.rstrip('/'),
            json={
                'serial_number': serial_number,
                'action': action,
                'description': description,
                'old_value': old_value,
                'new_value': new_value,
            },
            headers=headers,
            timeout=timeout,
        )
        return response.status_code == 200
    except Exception as exc:  # noqa: BLE001
        logger.debug('security event report failed (%s): %s', action, exc)
        return False


# ─────────────────────────────────────────────────────────────────────────
#  /etc/passwd, /etc/shadow, /etc/group snapshots
# ─────────────────────────────────────────────────────────────────────────
def _read_passwd():
    """{uid: username} for real user accounts only (uid >= MIN_REAL_UID)."""
    users = {}
    try:
        with open('/etc/passwd') as f:
            for line in f:
                parts = line.strip().split(':')
                if len(parts) < 3:
                    continue
                name, uid_str = parts[0], parts[2]
                try:
                    uid = int(uid_str)
                except ValueError:
                    continue
                if uid >= MIN_REAL_UID:
                    users[uid] = name
    except OSError as exc:
        logger.debug('could not read /etc/passwd: %s', exc)
    return users


def _read_shadow_changed_days():
    """{username: days_since_epoch_of_last_password_change} - root only."""
    out = {}
    try:
        with open('/etc/shadow') as f:
            for line in f:
                parts = line.strip().split(':')
                if len(parts) < 3:
                    continue
                name, changed = parts[0], parts[2]
                if changed.isdigit():
                    out[name] = int(changed)
    except OSError as exc:
        # Expected (and silent) if this agent process is somehow not root -
        # password-change detection just does not fire in that case rather
        # than crashing the poll loop.
        logger.debug('could not read /etc/shadow (need root): %s', exc)
    return out


def _read_sudo_members():
    """{group_name: set(usernames)} for SUDO_GROUPS only."""
    out = {}
    try:
        with open('/etc/group') as f:
            for line in f:
                parts = line.strip().split(':')
                if len(parts) < 4:
                    continue
                name, members = parts[0], parts[3]
                if name in SUDO_GROUPS:
                    out[name] = set(m for m in members.split(',') if m)
    except OSError as exc:
        logger.debug('could not read /etc/group: %s', exc)
    return out


def _current_sessions():
    """{"username|tty": login_time_str} from `who` (string keys: the state
    is saved as JSON, which cannot hold tuple keys)."""
    sessions = {}
    try:
        out = subprocess.run(['who'], capture_output=True, text=True,
                             timeout=10).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                user, tty = parts[0], parts[1]
                when = ' '.join(parts[2:4]) if len(parts) >= 4 else ''
                sessions['%s|%s' % (user, tty)] = when
    except Exception as exc:  # noqa: BLE001
        logger.debug('`who` failed: %s', exc)
    return sessions


def _check_failed_logins(state, server_url, serial_number, db_name=None):
    """Tail the journal for PAM auth failures since the last poll."""
    since = state.get('failed_login_cursor')
    cmd = ['journalctl', '-q', '--no-pager', '-g',
           'Failed password|authentication failure']
    if since:
        cmd += ['--since', since]
    else:
        cmd += ['--since', '-%ss' % (POLL_INTERVAL_SECONDS * 2)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=10).stdout
    except Exception as exc:  # noqa: BLE001
        logger.debug('journalctl query failed: %s', exc)
        return
    for line in out.splitlines():
        m = _FAILED_LOGIN_PATTERNS.search(line)
        if not m:
            continue
        user = next((g for g in m.groups() if g), 'unknown')
        report_security_event(
            server_url, serial_number, 'failed_login',
            'Failed login attempt for user "%s"' % user, db_name=db_name)
    state['failed_login_cursor'] = time.strftime('%Y-%m-%d %H:%M:%S')


def _check_sessions(state, server_url, serial_number, db_name=None):
    current = _current_sessions()
    previous = state.get('sessions', {})
    for key in current:
        if key not in previous:
            user, _sep, tty = key.partition('|')
            report_security_event(
                server_url, serial_number, 'user_login',
                'User "%s" logged in (%s)' % (user, tty), db_name=db_name)
    for key in previous:
        if key not in current:
            user, _sep, tty = key.partition('|')
            report_security_event(
                server_url, serial_number, 'user_logout',
                'User "%s" logged out (%s)' % (user, tty), db_name=db_name)
    state['sessions'] = current


def _check_accounts(state, server_url, serial_number, db_name=None):
    current = _read_passwd()
    previous = state.get('passwd', {})
    if previous:  # skip the very first poll - nothing to diff against yet
        for uid, name in current.items():
            if uid not in previous:
                report_security_event(
                    server_url, serial_number, 'user_created',
                    'User account "%s" (uid %s) created' % (name, uid),
                    db_name=db_name)
            elif previous[uid] != name:
                report_security_event(
                    server_url, serial_number, 'username_changed',
                    'Account uid %s renamed' % uid,
                    old_value=previous[uid], new_value=name, db_name=db_name)
        for uid, name in previous.items():
            if uid not in current:
                report_security_event(
                    server_url, serial_number, 'user_deleted',
                    'User account "%s" (uid %s) deleted' % (name, uid),
                    db_name=db_name)
    state['passwd'] = current


def _check_passwords(state, server_url, serial_number, db_name=None):
    current = _read_shadow_changed_days()
    previous = state.get('shadow', {})
    if previous:
        for name, days in current.items():
            prev_days = previous.get(name)
            if prev_days is not None and days > prev_days:
                report_security_event(
                    server_url, serial_number, 'password_changed',
                    'Password changed for user "%s"' % name, db_name=db_name)
    state['shadow'] = current


def _check_sudo(state, server_url, serial_number, db_name=None):
    current = _read_sudo_members()
    previous = state.get('sudo_groups', {})
    if previous:
        for group, members in current.items():
            new_members = members - set(previous.get(group, []))
            for user in new_members:
                report_security_event(
                    server_url, serial_number, 'sudo_granted',
                    'User "%s" added to "%s" group' % (user, group),
                    db_name=db_name)
    state['sudo_groups'] = {g: sorted(m) for g, m in current.items()}


def security_audit_loop(server_url, get_serial_number, db_name=None,
                        interval=POLL_INTERVAL_SECONDS):
    """Background thread: run every check above once per interval.

    Each check function updates `state` in place with this poll's snapshot,
    which becomes "previous" on the next tick - state.json persists this
    across agent restarts so a restart does not look like every account/
    session in existence just "appeared".
    """
    logger.info('[SECURITY AUDIT] loop started (poll: %ss)', interval)
    state = _read_json(STATE_FILE) or {}
    while True:
        try:
            serial_number = get_serial_number() if callable(get_serial_number) else get_serial_number
            if serial_number:
                _check_sessions(state, server_url, serial_number, db_name)
                _check_failed_logins(state, server_url, serial_number, db_name)
                _check_accounts(state, server_url, serial_number, db_name)
                _check_passwords(state, server_url, serial_number, db_name)
                _check_sudo(state, server_url, serial_number, db_name)
                _write_json(STATE_FILE, state)
        except Exception as exc:  # noqa: BLE001
            logger.error('[SECURITY AUDIT] poll failed: %s', exc)
        time.sleep(interval)
