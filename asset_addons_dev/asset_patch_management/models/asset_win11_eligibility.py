# -*- coding: utf-8 -*-
"""
Windows 11 upgrade eligibility scanner.

READ-ONLY by design. This module never upgrades anything — it only records
what each machine reports about its hardware and works out whether Windows 11
would install. Use it to answer "how much of our fleet can actually move?"
and to size a hardware refresh budget.

Two data sources, in priority order:

  1. Agent-reported facts (accurate). The agent posts TPM version, Secure Boot
     state, and firmware mode to /api/asset/win11/report. Requires the agent
     snippet in agent_snippets/win11_check.ps1 to be deployed.

  2. Server-side inference (approximate). If the agent hasn't reported yet,
     we fall back to what asset.asset already knows — processor string,
     ram_size, rom_size. This catches obvious failures (2 GB RAM, ancient CPU)
     but CANNOT determine TPM or Secure Boot, so the verdict stays "unknown"
     rather than "eligible".

Never treat an inferred result as authoritative. A machine can pass every
inferred check and still fail on TPM.
"""

import logging
import re

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Microsoft's documented Windows 11 minimums.
MIN_RAM_GB = 4.0
MIN_STORAGE_GB = 64.0

# Intel Core generation is the leading number in the model, e.g.
# "Intel(R) Core(TM) i5-8250U" → 8th gen. Microsoft's supported list starts
# at 8th gen for Intel Core. This regex is deliberately conservative: it only
# matches the well-formed "i<N>-<gen>..." pattern and gives up otherwise.
INTEL_CORE_RE = re.compile(r'i[3579][\s-]{0,2}(\d{4,5})', re.IGNORECASE)

# AMD Ryzen 2000 series and newer are supported.
AMD_RYZEN_RE = re.compile(r'ryzen\s+\d\s+(\d{4})', re.IGNORECASE)


class AssetAssetWin11(models.Model):
    _inherit = 'asset.asset'

    # ── Agent-reported raw facts ──────────────────────────────────────────
    win11_tpm_version = fields.Char(
        string='TPM Version', readonly=True,
        help='As reported by the agent, e.g. "2.0". Empty means not yet reported.',
    )
    win11_tpm_present = fields.Boolean(
        string='TPM Present', readonly=True,
    )
    win11_secure_boot = fields.Boolean(
        string='Secure Boot Enabled', readonly=True,
    )
    win11_uefi_mode = fields.Boolean(
        string='UEFI Firmware', readonly=True,
        help='Windows 11 requires UEFI. Legacy BIOS machines are ineligible.',
    )
    win11_cpu_supported = fields.Boolean(
        string='CPU on Supported List', readonly=True,
    )
    win11_reported_date = fields.Datetime(
        string='Eligibility Last Checked', readonly=True,
    )
    win11_agent_verdict = fields.Char(
        string='Agent Verdict', readonly=True,
        help='Raw verdict string from the agent-side compatibility check, '
             'if the agent ran one.',
    )

    # ── Computed verdict ──────────────────────────────────────────────────
    win11_status = fields.Selection(
        [
            ('already_11', 'Already on Windows 11'),
            ('eligible', 'Eligible'),
            ('blocked', 'Blocked'),
            ('unknown', 'Unknown — Not Scanned'),
            ('not_windows', 'Not a Windows Asset'),
        ],
        string='Windows 11 Status',
        compute='_compute_win11_status',
        store=True,
        help='Eligible means every checkable requirement passed AND the agent '
             'has reported TPM/Secure Boot. Unknown means we lack the data to '
             'be sure.',
    )
    win11_blockers = fields.Text(
        string='Blockers',
        compute='_compute_win11_status',
        store=True,
        help='Human-readable list of why this machine cannot upgrade.',
    )
    win11_data_source = fields.Selection(
        [('agent', 'Agent-Reported'), ('inferred', 'Inferred (approximate)'),
         ('none', 'No Data')],
        string='Data Source',
        compute='_compute_win11_status',
        store=True,
    )

    # ══════════════════════════════════════════════════════════════════════
    # CPU heuristics
    # ══════════════════════════════════════════════════════════════════════
    @staticmethod
    def _infer_cpu_supported(processor):
        """Best-effort guess at CPU support from the processor string.

        Returns (verdict, note) where verdict is True / False / None.
        None means "cannot tell" — which is a legitimate and common outcome,
        and must not be silently treated as a pass.
        """
        if not processor:
            return None, 'Processor string not available'

        proc = processor.strip()

        m = INTEL_CORE_RE.search(proc)
        if m:
            model_num = m.group(1)
            # 4-digit → gen is first digit (8250 → 8). 5-digit → first two
            # digits (10510 → 10).
            gen = int(model_num[0]) if len(model_num) == 4 else int(model_num[:2])
            if gen >= 8:
                return True, f'Intel Core {gen}th gen (supported)'
            return False, f'Intel Core {gen}th gen — Windows 11 requires 8th gen or newer'

        m = AMD_RYZEN_RE.search(proc)
        if m:
            series = int(m.group(1))
            if series >= 2000:
                return True, f'AMD Ryzen {series} series (supported)'
            return False, f'AMD Ryzen {series} series — Windows 11 requires 2000 series or newer'

        if re.search(r'xeon|epyc', proc, re.IGNORECASE):
            return None, 'Server-class CPU — check Microsoft list manually'

        if re.search(r'pentium|celeron|atom', proc, re.IGNORECASE):
            return False, f'{proc} — entry-level CPU, almost certainly unsupported'

        return None, f'Could not identify CPU generation from "{proc}"'

    # ══════════════════════════════════════════════════════════════════════
    # Verdict
    # ══════════════════════════════════════════════════════════════════════
    @api.depends(
        'platform', 'os_name', 'processor', 'ram_size', 'rom_size',
        'win11_tpm_present', 'win11_tpm_version', 'win11_secure_boot',
        'win11_uefi_mode', 'win11_cpu_supported', 'win11_reported_date',
    )
    def _compute_win11_status(self):
        for asset in self:
            blockers = []

            # Non-Windows machines are out of scope entirely.
            if asset.platform and asset.platform != 'windows':
                asset.win11_status = 'not_windows'
                asset.win11_blockers = False
                asset.win11_data_source = 'none'
                continue

            # Already on 11?
            os_name = (asset.os_name or '').lower()
            if 'windows 11' in os_name or 'win 11' in os_name or 'win11' in os_name:
                asset.win11_status = 'already_11'
                asset.win11_blockers = False
                asset.win11_data_source = 'agent' if asset.win11_reported_date else 'none'
                continue

            has_agent_data = bool(asset.win11_reported_date)

            # ── RAM ───────────────────────────────────────────────────────
            if asset.ram_size and asset.ram_size > 0:
                if asset.ram_size < MIN_RAM_GB:
                    blockers.append(
                        f'RAM {asset.ram_size:.1f} GB — requires {MIN_RAM_GB:.0f} GB minimum'
                    )
            else:
                blockers.append('RAM size unknown')

            # ── Storage ───────────────────────────────────────────────────
            if asset.rom_size and asset.rom_size > 0:
                if asset.rom_size < MIN_STORAGE_GB:
                    blockers.append(
                        f'Storage {asset.rom_size:.1f} GB — requires {MIN_STORAGE_GB:.0f} GB minimum'
                    )
            else:
                blockers.append('Storage size unknown')

            # ── CPU ───────────────────────────────────────────────────────
            if has_agent_data and asset.win11_cpu_supported:
                pass  # agent confirmed it's on the supported list
            else:
                cpu_ok, cpu_note = self._infer_cpu_supported(asset.processor)
                if cpu_ok is False:
                    blockers.append(cpu_note)
                elif cpu_ok is None:
                    blockers.append(f'CPU: {cpu_note}')

            # ── TPM / Secure Boot / UEFI — agent data only ────────────────
            if has_agent_data:
                if not asset.win11_tpm_present:
                    blockers.append('TPM not present — hardware blocker')
                elif asset.win11_tpm_version and \
                        not asset.win11_tpm_version.startswith('2'):
                    blockers.append(
                        f'TPM {asset.win11_tpm_version} — Windows 11 requires TPM 2.0'
                    )
                if not asset.win11_secure_boot:
                    blockers.append('Secure Boot disabled or unsupported')
                if not asset.win11_uefi_mode:
                    blockers.append('Legacy BIOS — Windows 11 requires UEFI')
            else:
                blockers.append(
                    'TPM / Secure Boot not scanned — deploy the agent '
                    'eligibility check for a definitive answer'
                )

            # ── Final verdict ─────────────────────────────────────────────
            asset.win11_blockers = '\n'.join(f'• {b}' for b in blockers) if blockers else False
            asset.win11_data_source = 'agent' if has_agent_data else (
                'inferred' if asset.processor or asset.ram_size else 'none'
            )

            if not blockers and has_agent_data:
                asset.win11_status = 'eligible'
            elif not has_agent_data:
                # Without TPM data we can never say "eligible" honestly.
                # If inference already found a hard failure, say so; otherwise
                # the answer is genuinely unknown.
                hard_fail = any(
                    'requires' in b or 'unsupported' in b for b in blockers
                )
                asset.win11_status = 'blocked' if hard_fail else 'unknown'
            else:
                asset.win11_status = 'blocked'

    # ══════════════════════════════════════════════════════════════════════
    # Dashboard data
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def get_win11_readiness_summary(self):
        """Fleet-wide counts for the readiness dashboard."""
        Asset = self.sudo()
        base = [('platform', '=', 'windows')]

        def count(status):
            return Asset.search_count(base + [('win11_status', '=', status)])

        total = Asset.search_count(base)
        eligible = count('eligible')
        blocked = count('blocked')
        unknown = count('unknown')
        already = count('already_11')

        scanned = Asset.search_count(base + [('win11_reported_date', '!=', False)])

        return {
            'total': total,
            'eligible': eligible,
            'blocked': blocked,
            'unknown': unknown,
            'already_11': already,
            'scanned': scanned,
            'scan_coverage_pct': round(100.0 * scanned / total, 1) if total else 0.0,
            'ready_pct': round(100.0 * (eligible + already) / total, 1) if total else 0.0,
        }

    def action_view_win11_blockers(self):
        """Open a filtered list of blocked machines."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Windows 11 — Blocked Machines',
            'res_model': 'asset.asset',
            'view_mode': 'list,form',
            'domain': [('platform', '=', 'windows'),
                       ('win11_status', '=', 'blocked')],
        }
