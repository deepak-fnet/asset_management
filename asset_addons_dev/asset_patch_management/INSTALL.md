# Auto-Patching + Windows 11 Eligibility — Installation

Two features, added as a patch to your existing `asset_management` module.
No new module, no new dependencies.

- **Step 1 — Auto-patching policies.** Approves Windows updates automatically
  based on rules you define. The existing agent loop does the installing —
  no agent changes required.
- **Step 2 — Windows 11 eligibility scanner.** Read-only. Reports which
  machines could move to Windows 11 and why the others cannot. Never upgrades
  anything.

---

## 1. Copy the files in

```
asset_management/
├── models/
│   ├── asset_update_policy.py          ← NEW
│   └── asset_win11_eligibility.py      ← NEW
├── controllers/
│   └── win11_eligibility_api.py        ← NEW
├── views/
│   ├── asset_update_policy_views.xml   ← NEW
│   ├── asset_win11_views.xml           ← NEW
│   └── asset_patch_menus.xml           ← NEW
├── data/
│   ├── asset_update_policy_cron.xml    ← NEW
│   └── asset_update_policy_demo.xml    ← NEW
└── security/
    └── ir.model.access.csv             ← APPEND 3 lines (see step 3)
```

## 2. Register the Python files

**`models/__init__.py`** — append:

```python
from . import asset_update_policy
from . import asset_win11_eligibility
```

**`controllers/__init__.py`** — append:

```python
from . import win11_eligibility_api
```

## 3. Append the access rules

Add these three lines to the **end** of your existing
`security/ir.model.access.csv` (do not replace the file — append only):

```csv
access_asset_update_policy_user,asset.update.policy user,model_asset_update_policy,asset_management.group_asset_user,1,0,0,0
access_asset_update_policy_manager,asset.update.policy manager,model_asset_update_policy,asset_management.group_asset_manager,1,1,1,1
access_asset_update_policy_admin,asset.update.policy admin,model_asset_update_policy,asset_management.group_asset_admin,1,1,1,1
```

## 4. Register the data files in `__manifest__.py`

Add to the `"data"` list. **Order matters** — security before views, views
before menus:

```python
"data": [
    # ... your existing entries ...
    "views/asset_update_policy_views.xml",
    "views/asset_win11_views.xml",
    "views/asset_patch_menus.xml",
    "data/asset_update_policy_cron.xml",
    "data/asset_update_policy_demo.xml",
],
```

## 5. Upgrade

```bash
sudo systemctl restart odoo
# Then: Apps → Asset Management → Upgrade
```

Two new items should appear under the **Windows ▾** menu:
**Update Policies** and **Windows 11 Readiness**.

---

## Configuring auto-patching — do this carefully

The cron ships **disabled on purpose**. Nothing is auto-approved until you
explicitly turn it on. Recommended sequence:

### a. Review the starter policy

Open **Windows ▾ → Update Policies**. A policy named
*"Standard — Security & Critical Only"* is pre-created and inactive:

| Setting | Default | Why |
|---|---|---|
| Severities | Security + Critical only | Important and Optional include drivers and preview builds — riskier |
| Delay | 7 days | Bad patches usually get pulled within a week. This is the single most valuable setting. |
| Window | Sunday 02:00–05:00 | Reboots never land mid-workday |
| Max per run | 25 | A misconfigured policy cannot hit the whole fleet at once |

### b. Add exclusions before enabling anything

On the **Targeting** tab, add to **Excluded Assets**:
- Production servers
- Any machine running a fragile legacy application
- Executive laptops, if an unexpected reboot would be costly

### c. Use Preview

Click **Preview**. It shows exactly which KB/machine pairs would be approved
right now, and writes nothing. Run this before every change to a live policy.

### d. Set up a pilot group

On the **Staged Rollout** tab, enable it and pick 3–5 machines you can afford
to have broken for a day — ideally spanning your different hardware models.

With staged rollout on:
- Pilot machines are approved immediately
- Everyone else waits until the same KB has been installed cleanly on the
  pilot for `pilot_soak_days`
- **If any pilot machine reports `failed` for a KB, fleet rollout for that KB
  halts automatically**

### e. Activate the policy, then the cron

1. Set the policy to **Active**
2. Go to **Settings → Technical → Scheduled Actions**
3. Find *"Asset: Run Windows Update Policies"*
4. Set it **Active**

The cron runs hourly but each policy enforces its own maintenance window, so
outside the window it does nothing.

### f. Watch the first cycle

After the first Sunday window, check:
- **Update Policies → Total Auto-Approved** — did it approve a sane number?
- The Windows Updates tab on a few machines — did they move to `installed`?
- Any stuck at `installing` for more than a day means the agent isn't picking
  up instructions

---

## Windows 11 eligibility — two levels of accuracy

### Level 1: works immediately, approximate

As soon as you upgrade the module, **Windows 11 Readiness** is populated using
inventory data you already have — `processor`, `ram_size`, `rom_size`.

This catches obvious failures (4 GB RAM, an old Celeron) but **cannot
determine TPM or Secure Boot**. Machines that pass every inferred check are
marked **Unknown**, not Eligible — because a machine can pass everything
inferable and still fail on TPM.

The `Data Source` column shows `Inferred (approximate)` for these.

### Level 2: definitive, needs an agent change

To get real answers, add `agent_snippets/win11_check.py` to your Windows
agent:

1. Paste the functions into your existing `agent.py`
2. Call it once per full sync cycle:

```python
report_win11_eligibility(SERVER_URL, SERIAL_NUMBER)
```

3. Rebuild and redeploy the agent

The agent runs four read-only PowerShell queries (TPM, Secure Boot, firmware
mode, CPU model) and POSTs the results to `/api/asset/win11/report`. It
modifies nothing.

Machines that have reported show `Agent-Verified` as their data source, and
only those can ever be marked **Eligible**.

---

## What this does NOT do

Stated plainly so there's no surprise later:

- **No feature upgrades.** Nothing here upgrades Windows 10 → 11. The scanner
  reports eligibility only. Triggering the actual upgrade is a separate,
  riskier feature we deliberately deferred.
- **No reboot control.** If a patch requires a reboot, the existing agent
  behaviour applies. Policy `maintenance_window` controls *when the install is
  approved*, not reboot behaviour on the endpoint.
- **No rollback.** If an auto-approved patch causes a problem, uninstalling is
  still the existing manual action on the Windows Updates tab.
- **The CPU check is a heuristic.** It reads generation from the processor
  string. Unusual or server-class CPUs return "undetermined" rather than a
  guess. It is not a copy of Microsoft's full supported-CPU list.

---

## Rollback

If you need to undo this:

1. Deactivate the cron (Settings → Technical → Scheduled Actions)
2. Remove the entries added in steps 2, 3, 4
3. Delete the new files
4. Upgrade the module

Existing Windows Update records are untouched by removal — the policy engine
only ever writes to the `status` field that manual actions already use.
