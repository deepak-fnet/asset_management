# Getting Patch & Vulnerability Working — Step by Step

Module: `asset_update_center` — extends `asset_patch_vuln`

---

## First: the terminology, because it is causing confusion

You asked:

> LENOVO 20S1S3B00F have pending update / upgrade all / chrome → click here to
> upgrade / firefox → click here to upgrade — so this is called patch update
> am I right?

Almost. What you described is **two different systems** that people lump
together as "patching":

| | OS Patch | Application Update |
|---|---|---|
| Example | `KB5066791`, `krb5-locales` | Chrome 141 → 142 |
| Comes from | Windows Update / apt | Google / Mozilla directly |
| Identified by | KB number / package name | Application name |
| Odoo model | `asset.windows.update` | `asset.app.update` ← **new** |

Before this patch you only had the left column. **`asset.app.update` is the
new piece** — it is what makes "Chrome → click here to upgrade" possible.

Both together = what a commercial tool calls "patch management". So yes, your
mental model is right; the system just needed the second half built.

---

## Bug found in your existing module — read this first

Your screenshot shows **Missing Patches by Platform: Windows 520 (100%),
Linux 0** — while Top Missing Patches lists `alsa-ucm-conf`, `apport`,
`fwupd`, `krb5-locales`. Those are Ubuntu packages.

**Cause:** your module has exactly one update endpoint,
`/api/asset/updates/report`, and it writes to `asset.windows.update`
unconditionally. Your Linux agents post there, so Ubuntu package names are
being stored as Windows KB numbers.

The dashboard is reporting the data correctly. The data is wrong.

**Fix, in two parts:**

1. This patch adds `/api/asset/os_updates/report`, which routes by the
   asset's `platform` field.
2. Point your **Linux and macOS agents** at the new endpoint. Windows agents
   can stay on the old one, or move too — the new endpoint handles all three.

Old agent call:
```python
requests.post(f'{SERVER}/api/asset/updates/report',
              json={'serial_number': SERIAL, 'updates': [...], 'installed_kbs': [...]})
```

New:
```python
requests.post(f'{SERVER}/api/asset/os_updates/report',
              json={'serial_number': SERIAL,
                    'updates': [{'identifier': 'krb5-locales',
                                 'title': 'krb5-locales 1.20.1-6ubuntu2.7',
                                 'severity': 'security'}],
                    'installed': ['pkg1', 'pkg2']})
```

Note `identifier` replaces `kb_number`, and `installed` replaces
`installed_kbs`, because the field name should not assume Windows.

**Cleaning up the bad data.** Once the Linux agents are repointed, remove the
mis-filed rows from an Odoo shell:

```python
# Inspect first — do not delete blindly
bad = env['asset.windows.update'].search([
    ('asset_id.platform', '!=', 'windows')
])
print(len(bad), 'rows filed under the wrong platform')

# When you are satisfied the list is correct:
bad.unlink()
env.cr.commit()
```

Linux agents will repopulate `asset.linux.update` correctly on their next sync.

---

## Why the CVE sync failed

The generic error told you nothing. This patch adds a proper diagnostic.

### Run the connectivity test

**Security → Vulnerability Management → CVE Database**, then
**Action → Test NVD Connectivity**.

It checks four things in order and tells you exactly which one failed:

1. DNS resolution of `services.nvd.nist.gov`
2. TCP connect on port 443
3. Whether the API key is actually configured
4. A live API call, reporting the real HTTP status

### The usual causes, in order of likelihood

**Corporate firewall blocking outbound HTTPS.** You are on `192.168.105.x`
behind a company network — this is the most likely cause. Test from the Odoo
server itself:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://services.nvd.nist.gov/rest/json/cves/2.0?resultsPerPage=1"
```

- `200` — network is fine, the problem is elsewhere
- `000` — blocked or DNS failure, talk to your network team
- `403` — reachable but rate limited, see below

**HTTP 403 = rate limiting.** Without a key NVD allows 5 requests per 30
seconds. The sync makes several. Get a free key at
<https://nvd.nist.gov/developers/request-an-api-key> and store it at:

**Settings → Technical → System Parameters → Create**
- Key: `asset_vulnerability.nvd_api_key`
- Value: your key

> You said you set the API key. Confirm it is in **System Parameters** with
> exactly that key name. If you saved it somewhere else it is not being sent,
> and you are still rate-limited.

**Odoo behind a proxy.** If your network requires an HTTP proxy, Odoo needs it
in its systemd unit:

```ini
# /etc/systemd/system/odoo.service
[Service]
Environment="https_proxy=http://proxy.yourcompany.com:8080"
Environment="http_proxy=http://proxy.yourcompany.com:8080"
```
Then `sudo systemctl daemon-reload && sudo systemctl restart odoo`.

### If the server genuinely cannot reach the internet

Use **Security → Vulnerability Management → Offline CVE Import**.

On any machine that does have internet:

```bash
curl -o cves.json \
  "https://services.nvd.nist.gov/rest/json/cves/2.0?resultsPerPage=2000"
```

Open `cves.json`, copy everything, paste into the wizard, click Import.

Repeat with different `startIndex` values (`&startIndex=2000`, `4000`, …) to
build up the database.

---

## Where to see per-asset updates

Open any asset → new **Update Center** tab.

It shows, for that one machine:

- A summary line: *"3 OS patches · 2 app updates pending"*
- **Update Everything** — queues both in one click
- The application list with a per-row **Update** button, which is exactly the
  "chrome → click here to upgrade" behaviour you described
- A **Block** button for apps you do not want touched

Fleet-wide view: **Security → Patch Management → Application Updates**,
grouped by asset by default.

### The app list will be empty until you deploy the agent snippet

Nothing currently tells Odoo that Chrome is out of date. That data has to come
from the agent.

Add `agent_snippets/app_updates.py` to your agent and call, once per sync:

```python
check_and_report_app_updates(SERVER_URL, SERIAL_NUMBER)      # detect + report
process_app_update_instructions(SERVER_URL, SERIAL_NUMBER)   # perform queued
report_listening_ports(SERVER_URL, SERIAL_NUMBER)            # for vuln report
```

It shells out to `winget upgrade` on Windows, `apt list --upgradable` on
Linux, `brew outdated` on macOS. Those tools already know what is outdated —
no version-tracking engine needed, and nothing to maintain when a vendor
changes their release cadence.

**Requirement:** `winget` must be present on Windows endpoints. Check with
`winget --version`. It ships with Windows 10 1809+ and Windows 11, but is
sometimes absent on stripped images.

### The full flow, end to end

```
Agent runs `winget upgrade`
        │
        ▼
POST /api/asset/app_updates/report      →  rows appear as "Update Available"
        │
        ▼
Admin clicks Update (or Update Everything)  →  status = "queued"
        │
        ▼
GET /api/asset/app_updates/instructions  →  agent receives the upgrade list
        │
        ▼
Agent runs `winget upgrade --id Google.Chrome --silent`
        │
        ▼
POST /api/asset/app_updates/result       →  status = "updated"
```

Identical in shape to the OS patch loop you already have.

---

## Per-asset vulnerability scanning

New **Vulnerability Report** tab on every asset:

- Severity summary for that machine
- **Scan This Asset** button
- Findings list with CVE, CVSS, confidence, and Apply Patch
- Listening ports, once the agent reports them

### Being straight about what this is

The report you sent as a reference is **OpenVAS output**. OpenVAS is a network
scanner — it probes ports from outside, fingerprints services, and tests
whether they are exploitable.

What we built is **agent-based correlation**: it reads the software inventory
the agent already collects and matches it against the CVE database.

| | Ours (agent) | OpenVAS (network) |
|---|---|---|
| Sees every installed app | Yes | No — only network-facing services |
| Needs no network access to target | Yes | No |
| Works on laptops off-site | Yes | No |
| Tests real exploitability | **No** | Yes |
| Finds weak/default credentials | **No** | Yes |
| TLS/cipher misconfiguration | **No** | Yes |
| Scans devices with no agent | **No** | Yes |

Neither replaces the other. If you need genuine network scanning, the right
move is integrating OpenVAS and pulling its results in — not reimplementing a
scanner. That is a separate project.

The port list we show comes from the agent reading its own socket table
(`netstat` / `ss`). That tells you *what is listening*, not whether it is
exploitable.

---

## Do this in order

**1. Install this patch**
```bash
cd /home/fnetadmin/odoo17/axles_v17/custom/axles_addons/
sudo unzip /path/to/asset_update_center.zip
sudo chown -R fnetadmin:fnetadmin asset_update_center
sudo systemctl restart odoo
```
Apps → Update Apps List → install **Asset Update Center & Vulnerability Report**

**2. Diagnose the CVE sync** — run Test NVD Connectivity, fix whatever it
reports, or fall back to Offline Import.

**3. Fix the Linux data** — repoint Linux agents at
`/api/asset/os_updates/report`, then clean the mis-filed rows.

**4. Deploy the agent snippet** — this is what populates Chrome/Firefox
updates. Nothing appears until you do.

**5. Verify on one machine** — open an asset, check the Update Center tab
shows app updates, click Update on one, confirm the status moves
available → queued → updating → updated.

**6. Only then** run a fleet vulnerability scan.

---

## Realistic expectations

- **Steps 1–3 work immediately.** Patch management is functional today.
- **Step 4 needs an agent rebuild and redeploy.** Until then the Application
  Updates list stays empty — that is expected, not a bug.
- **Vulnerability management is blocked on CVE data.** No CVE database, no
  findings, no exceptions. Sort step 2 first or it will look broken.
- **Findings need triage.** Low-confidence matches land in the Review Queue
  by design. Expect to spend time there in the first week; the alternative is
  a dashboard full of false positives that everyone learns to ignore.
