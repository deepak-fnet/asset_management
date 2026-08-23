# Patch & Vulnerability Management — Install Guide

Module name: `asset_patch_vuln`

Two dashboards matching the designs you supplied:

- **Patch Management** — fleet patch compliance, deployment batches, trends
- **Vulnerability Management** — CVE correlation against your software inventory

---

## Prerequisites

| Requirement | Why |
|---|---|
| `asset_management` installed | Provides `asset.asset` and the three per-platform update models |
| **`asset_patch_management` patch installed** | Provides `asset.update.policy`. Without it the Policy panel on the patch dashboard is empty (it degrades gracefully, it does not crash) |
| Outbound HTTPS to `services.nvd.nist.gov` | Required for CVE sync. **Vulnerability management is non-functional without it.** |
| Agent software inventory populated | The scanner correlates what the agent reports. No inventory, no findings. |

## Install

```bash
cd /home/fnetadmin/odoo17/axles_v17/custom/axles_addons/
sudo unzip /path/to/asset_patch_vuln.zip
sudo chown -R fnetadmin:fnetadmin asset_patch_vuln
sudo systemctl restart odoo
```

Then: **Apps → Update Apps List → search "Patch & Vulnerability" → Install**

A new **Security ▾** menu appears under Asset Management with both sections.

---

## Part 1 — Patch Management

Works immediately after install. It reads the update records your agents
already produce.

### Compliance definition

A machine is **compliant** when it has zero pending patches of severity
`security` or `critical`. Important and optional patches are excluded
deliberately — include them and the number never reaches 100%, at which point
people stop looking at it.

```
fleet compliance = compliant machines / total machines × 100
```

### The trend chart needs a night to populate

"Compliance Over Time" reads `asset.patch.compliance.snapshot`, written once
a day by a cron that is **enabled by default**. Until it runs the first time
the chart shows an empty state. That is expected, not a bug.

To seed it immediately, run once from an Odoo shell:

```python
env['asset.patch.compliance.snapshot'].cron_capture_snapshot()
env.cr.commit()
```

### Deployment batches

`asset.patch.deployment` groups many patches × many machines into one named
unit so you can track "June Security Updates — Windows" as a single item.

Deploying does **not** bypass the existing pipeline. It writes
`status = 'installing'` on the same underlying update records the manual
buttons use, so agents pick them up through the existing
`/api/asset/updates/instructions` endpoint. No agent change required.

---

## Part 2 — Vulnerability Management

### Read this before enabling anything

This is **database-side correlation, not a network scanner.** It takes the
software inventory your agent already reports and cross-references it against
a local CVE cache. Nothing is sent to or executed on the endpoint, and no
ports are probed.

### Step 1 — populate the CVE database

The dashboard shows a warning banner until this is done, because a scan
against an empty CVE table always returns zero findings — which looks like
good news and is not.

Click **Sync CVE DB** on the dashboard, or run:

```python
env['asset.cve'].sync_from_nvd(days_back=30)
env.cr.commit()
```

This pulls CVEs *modified* in the window. A full NVD download is ~250,000
records and takes hours; incremental sync keeps the cache current at a
fraction of the cost.

**Optional but recommended — an NVD API key.** Free from
<https://nvd.nist.gov/developers/request-an-api-key>. Raises the rate limit
from 5 to 50 requests per 30 seconds. Store it at:

**Settings → Technical → System Parameters**
Key: `asset_vulnerability.nvd_api_key`

### Step 2 — run a scan

**Run New Scan** on the dashboard, or from a shell:

```python
env['asset.vulnerability.engine'].run_scan()
env.cr.commit()
```

### Step 3 — enable the crons (only after steps 1 and 2 work)

**Settings → Technical → Scheduled Actions**

- *Vulnerability: Sync CVE Database (NVD)* — daily
- *Vulnerability: Nightly Scan* — daily

Both ship **disabled**. Enable them only once you have confirmed the sync
reaches NVD and a manual scan produces sensible results.

---

## The confidence model — please read

Matching "Google Chrome 141.0.7390.55" as reported by an agent onto the right
CPE entry in NVD is genuinely imprecise. Vendors name products inconsistently
and installers report names differently. A naive matcher produces a dashboard
full of false positives, and people stop trusting it within a week.

So every finding carries a confidence level:

| Confidence | Meaning | Opens as |
|---|---|---|
| **High** | Product name matched AND installed version falls inside an explicit NVD version range | `open` — counted in the KPIs |
| **Medium** | Name matched but version could not be parsed, or the CVE has no version bounds | `needs_review` |
| **Low** | Partial/token name match only | `needs_review` |

**Only high-confidence findings count toward the dashboard numbers.**
Everything else sits in the Review Queue until a human confirms it. The
"Needs Review" banner links straight there.

Triage actions on each finding:

- **Confirm** — promotes to `open`, counted
- **False Positive** — the match was wrong
- **Ignore** — real but accepted risk
- **Mark Remediated** — fixed

### Deliberate design choices worth knowing

- If a product name matches but the version is **outside** the affected range,
  the finding is **discarded**, not downgraded. That is a genuine negative.
- Generic tokens (`update`, `microsoft`, `windows`, `driver`, `runtime`,
  `framework`) are excluded from matching. They match everything and mean
  nothing.
- **Apply Patch** only works when the CVE text yields a KB number *and* that
  KB is already known on the asset. Otherwise it tells you plainly rather than
  guessing — it will point you to the vendor advisory instead.

---

## Honest limitations

Stated plainly so nothing surprises you later:

1. **CVE matching is heuristic.** It will miss things and it will over-report.
   The confidence model manages this; it does not eliminate it. This is not a
   substitute for a dedicated scanner like Nessus or Qualys.
2. **Only correlates installed software.** It does not detect misconfigurations,
   weak passwords, open ports, or OS-level CVEs not tied to a package.
3. **NVD coverage varies.** Well-known commercial software is well covered.
   Niche or in-house applications will produce nothing.
4. **No exploit-availability data.** CVSS score only — no EPSS, no KEV catalog
   cross-reference. A 9.8 with no public exploit and a 7.5 being actively
   exploited look the same here.
5. **The scan is point-in-time.** It reflects the last agent software sync,
   not live state.

---

## Rollback

```
Apps → Patch & Vulnerability Management → Uninstall
```

Removes all CVE cache, findings, scans, deployments, and compliance history.
The underlying `asset.windows.update` / `asset.linux.update` /
`asset.macos.update` records are **not** touched — this module only ever reads
them and writes the same `status` field the manual buttons already use.
