# -*- coding: utf-8 -*-
"""
AGENT-SIDE SNIPPET — add this to your Windows agent (agent.py).

Collects the four facts the server cannot infer on its own — TPM version,
Secure Boot state, firmware mode, and CPU support — and POSTs them to
/api/asset/win11/report.

This is READ-ONLY. It runs PowerShell queries and reports the answers.
It never modifies the machine and never triggers an upgrade.

Integration:
  1. Paste the functions below into your existing agent.py
  2. Call report_win11_eligibility(SERVER_URL, SERIAL_NUMBER) once per full
     sync cycle (every 30 min is plenty — this data changes rarely, usually
     only when someone enables TPM in BIOS)
"""

import json
import logging
import re
import subprocess

import requests

logger = logging.getLogger(__name__)

# Requires elevation for Get-Tpm and Confirm-SecureBootUEFI. The agent
# already runs as LocalSystem via NSSM, so this is satisfied.
PS_FLAGS = ['powershell', '-NoProfile', '-NonInteractive', '-Command']


def _run_ps(command, timeout=20):
    """Run a PowerShell command, return stripped stdout or None."""
    try:
        result = subprocess.run(
            PS_FLAGS + [command],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode == 0:
            return (result.stdout or '').strip()
        logger.debug('PowerShell rc=%s for %r: %s',
                     result.returncode, command, result.stderr)
        return None
    except Exception as exc:
        logger.debug('PowerShell failed for %r: %s', command, exc)
        return None


def check_tpm():
    """Return (present: bool, version: str)."""
    # SpecVersion looks like "2.0, 0, 1.38" — we want the leading number.
    out = _run_ps(
        '(Get-CimInstance -Namespace root/CIMV2/Security/MicrosoftTpm '
        '-ClassName Win32_Tpm).SpecVersion'
    )
    if out:
        version = out.split(',')[0].strip()
        return True, version

    # Fallback: Get-Tpm reports presence even when the WMI class is absent.
    out = _run_ps('(Get-Tpm).TpmPresent')
    if out and out.lower() == 'true':
        return True, ''

    return False, ''


def check_secure_boot():
    """Return True if Secure Boot is enabled.

    Confirm-SecureBootUEFI throws on legacy BIOS machines, which is itself
    informative — a throw means not UEFI, therefore not Secure Boot.
    """
    out = _run_ps(
        'try { Confirm-SecureBootUEFI } catch { "NOTSUPPORTED" }'
    )
    return bool(out) and out.strip().lower() == 'true'


def check_uefi_mode():
    """Return True if the firmware is UEFI (not legacy BIOS)."""
    out = _run_ps('$env:firmware_type')
    if out:
        return 'uefi' in out.lower()

    out = _run_ps(
        '(Get-CimInstance -ClassName Win32_ComputerSystem).BootupState'
    )
    # Less reliable; treat an unknown result as "not confirmed UEFI".
    return False


def check_cpu_supported():
    """Best-effort CPU generation check, mirroring the server-side logic.

    Returns (supported: bool|None, name: str). None means undetermined —
    report it honestly rather than guessing.
    """
    name = _run_ps('(Get-CimInstance -ClassName Win32_Processor).Name')
    if not name:
        return None, ''

    name = name.strip()

    m = re.search(r'i[3579][\s-]{0,2}(\d{4,5})', name, re.IGNORECASE)
    if m:
        model = m.group(1)
        gen = int(model[0]) if len(model) == 4 else int(model[:2])
        return gen >= 8, name

    m = re.search(r'ryzen\s+\d\s+(\d{4})', name, re.IGNORECASE)
    if m:
        return int(m.group(1)) >= 2000, name

    if re.search(r'pentium|celeron|atom', name, re.IGNORECASE):
        return False, name

    return None, name


def gather_win11_facts():
    """Collect everything into a single payload dict."""
    tpm_present, tpm_version = check_tpm()
    secure_boot = check_secure_boot()
    uefi = check_uefi_mode()
    cpu_supported, cpu_name = check_cpu_supported()

    # Local verdict is advisory only — the server recomputes authoritatively.
    if tpm_present and tpm_version.startswith('2') and secure_boot \
            and uefi and cpu_supported:
        verdict = 'eligible'
    elif cpu_supported is None:
        verdict = 'undetermined'
    else:
        verdict = 'blocked'

    return {
        'tpm_present': tpm_present,
        'tpm_version': tpm_version,
        'secure_boot': secure_boot,
        'uefi_mode': uefi,
        'cpu_supported': bool(cpu_supported),
        'cpu_name': cpu_name,
        'verdict': verdict,
    }


def report_win11_eligibility(server_url, serial_number, timeout=30):
    """Gather facts and POST them to Odoo. Safe to call on every sync."""
    try:
        payload = gather_win11_facts()
        payload['serial_number'] = serial_number

        response = requests.post(
            f'{server_url}/api/asset/win11/report',
            json=payload,
            timeout=timeout,
        )
        if response.status_code == 200:
            logger.info('Win11 eligibility reported: %s', payload['verdict'])
            return True

        logger.warning('Win11 report returned HTTP %s', response.status_code)
        return False

    except Exception as exc:
        # Never let this break the main sync loop — it's diagnostic data only.
        logger.error('Win11 eligibility report failed: %s', exc)
        return False
