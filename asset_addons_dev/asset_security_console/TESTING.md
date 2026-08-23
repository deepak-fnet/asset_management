# How to Test and Configure — Read This First

You now have four modules stacked. This explains what each one does, how to
verify it works, and in what order.

---

## What you actually have

| # | Module | What it gives you |
|---|---|---|
| 1 | `asset_management` | Your original module — assets, agents, OS patch records |
| 2 | `asset_patch_management` | Auto-patch policies + Windows 11 eligibility scanner |
| 3 | `asset_patch_vuln` | Patch dashboard + Vulnerability dashboard + CVE database |
| 4 | `asset_update_center` | App updates (Chrome/Firefox), per-asset tabs, CVE diagnostics |
| 5 | `asset_security_console` ← **new** | Asset-wise console, health check, demo data |

Install order matters: 1 → 2 → 3 → 4 → 5.

---

## Install this module

```bash
cd /home/fnetadmin/odoo17/axles_v17/custom/axles_addons/
sudo unzip /path/to/asset_security_console.zip
sudo chown -R fnetadmin:fnetadmin asset_security_console
sudo systemctl restart odoo
```

Apps → Update Apps List → install **Asset Security Console**.

Three new items under **Security ▾**:

- **Asset Security Console** — asset-wise view
- **Setup & Health Check** — tells you what is configured
- **Demo Data (Testing)** — lets you test without agents

---

## Step 1 — Find out what is actually working

**Security → Setup & Health Check → Run Health Check**

It checks nine things and gives each a ✓ / ⚠ / ✗ with the exact next action:

| Check | What it tells you |
|---|---|
| Managed assets | Do any assets have platform set? |
| Agents reporting OS patches | Is patch data arriving at all? |
| Patch records filed under correct platform | **Detects the Linux-in-Windows-table bug** |
| Application update tracking | Has the agent snippet been deployed? |
| CVE database | Is it populated? Vulnerability scanning is dead without it |
| NVD API key | Set correctly? |
| Vulnerability scans | Has one ever completed? |
| Auto-patch policies | Configured and active? |
| Scheduled actions | Which crons are on? |

**Run this first, every time something looks wrong.** It is faster than
guessing.

---

## Step 2 — Test the UI without waiting for agents

This is the part that unblocks you today.

**Security → Demo Data (Testing)**

1. Pick one asset (a test machine, not production)
2. Leave all three checkboxes ticked
3. Click **Create Demo Data**

It creates on that asset:

- 5 OS patches — 3 pending, 2 installed, backdated 10+ days
- 5 application updates — Chrome, Firefox, 7-Zip, Zoom, Notepad++
- 5 demo CVEs with findings — critical through low, mixed confidence
- 1 completed scan record

Everything is marked (`DEMO-KB…`, `CVE-DEMO-…`) so removal is clean.

### Now walk through every screen

**A. Asset Security Console** — Security → Asset Security Console

Your test asset should show:
```
Pending: 3   Queued: 0   Installed: 2   Apps: 5   Crit: 1   High: 2
Status: At Risk
```

**B. The asset itself** — click into it:

- **Update Center** tab → *"3 OS patches · 5 app updates pending"*, an
  **Update Everything** button, and the app list with per-row **Update**
  buttons. This is the "chrome → click here to upgrade" you asked for.
- **Vulnerability Report** tab → severity summary, findings list, **Scan This
  Asset** button.

**C. Test the patch workflow:**

1. Update Center → click **Update** on Google Chrome
2. Status changes `available` → `queued`
3. Console now shows Apps: 4

That is the full approval flow. In production the agent picks up `queued` on
its next poll and installs.

**D. Test bulk actions:**

1. Asset Security Console → tick several assets
2. **Action → Approve All Pending Patches**
3. Pending drops to 0, Queued rises

**E. Patch dashboard** — Security → Patch Management → Patch Dashboard.
Numbers should now be non-zero.

### When finished testing

**Demo Data (Testing) → Remove Demo Data.** Deletes only marked records; real
agent data is untouched.

> Do this before going live. Otherwise your dashboards show fake numbers and
> nobody will trust them.

---

## Step 3 — Fix the real problems the health check found

### Problem A: CVE database empty

Everything vulnerability-related is blocked until this is fixed.

**Test connectivity from the Odoo server:**

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://services.nvd.nist.gov/rest/json/cves/2.0?resultsPerPage=1"
```

| Result | Meaning | Fix |
|---|---|---|
| `200` | Network fine | Sync should work — check API key placement |
| `000` | Blocked / DNS failure | Firewall. Ask network team, or use Offline Import |
| `403` | Rate limited | API key not being sent — see below |

**API key must be in the right place.** Settings → Technical → System
Parameters → key exactly `asset_vulnerability.nvd_api_key`. If you saved it
anywhere else it is not being used.

**No internet at all?** Security → Vulnerability Management → Offline CVE
Import. Run the curl on any machine that has internet, paste the JSON in.

### Problem B: Linux patches filed as Windows

If the health check flags this, your Ubuntu agents are posting Ubuntu package
names into `asset.windows.update`.

1. Repoint Linux/macOS agents at `/api/asset/os_updates/report`
2. Clean up from an Odoo shell:

```python
bad = env['asset.windows.update'].search([
    ('asset_id.platform', '!=', 'windows'),
    ('asset_id.platform', '!=', False),
])
print(len(bad), 'misfiled rows')   # check the number first
bad.unlink()
env.cr.commit()
```

Agents repopulate `asset.linux.update` correctly on their next sync.

### Problem C: No application updates

Expected until you deploy the agent snippet. Add
`asset_update_center/agent_snippets/app_updates.py` to your agent and call:

```python
check_and_report_app_updates(SERVER_URL, SERIAL_NUMBER)
process_app_update_instructions(SERVER_URL, SERIAL_NUMBER)
report_listening_ports(SERVER_URL, SERIAL_NUMBER)
```

Rebuild and redeploy the agent. Requires `winget` on Windows —
verify with `winget --version`.

---

## Step 4 — Go live, in this order

Only move to the next step when the current one is verified.

| Order | Action | Verify by |
|---|---|---|
| 1 | Remove demo data | Console shows real numbers |
| 2 | Fix CVE database | Health check shows ✓ |
| 3 | Fix Linux misfiling | Health check shows ✓ |
| 4 | Deploy agent snippet | App updates appear for real machines |
| 5 | Run one real scan | Findings appear, review the confidence levels |
| 6 | Triage the review queue | Confirm or dismiss low-confidence findings |
| 7 | Create one patch policy, Preview it | Preview lists sensible KB/machine pairs |
| 8 | Enable crons one at a time | Watch each for a day before enabling the next |

---

## Daily use, once configured

**Asset Security Console** is the screen to live in.

- Filter **At Risk** → machines with a critical CVE or pending security patch
- Select several → **Action → Approve All Pending Patches**
- Select several → **Action → Scan Selected for Vulnerabilities**
- Click any machine → Update Center or Vulnerability Report tab

Column meanings:

| Column | Meaning |
|---|---|
| Pending | Detected, awaiting approval |
| Queued | Approved, waiting for the agent to install |
| Installed | Completed successfully |
| Failed | Install attempted and failed — investigate |
| Apps | Third-party applications out of date |
| Crit / High | Open vulnerability findings by severity |

---

## Being straight about the limits

- **Vulnerability scanning is software correlation, not a network scan.** It
  reads what the agent reports and matches against CVEs. It cannot tell you
  whether a listening service is actually exploitable, cannot find weak
  passwords, and cannot see machines with no agent. If you need that, the
  right answer is integrating OpenVAS, not extending this.
- **CVE matching is heuristic and will produce some false positives.** That is
  why low-confidence findings land in the Review Queue instead of the KPI
  numbers. Expect to spend real time triaging in the first week.
- **Nothing works without agents.** Every number on every dashboard comes from
  agent-reported data.
- **App updates need `winget`.** Absent on some stripped Windows images.
