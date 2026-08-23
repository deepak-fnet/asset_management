# asset_management — Odoo 17 → 19 Migration Notes

Module version: **17.0.1.0.0 → 19.0.1.0.0**

Scope: 213 files, ~47k LOC (18k Python, 17.6k XML, 11.4k JS).
This is a **code migration only**. Database upgrades must still be run
sequentially 17 → 18 → 19 through Odoo's upgrade service.

---

## 1. Tree → List (Odoo 18 view rename)

`<tree>` was renamed to `<list>`. Applied everywhere:

| Change | Count |
|---|---|
| `<tree>` / `</tree>` → `<list>` / `</list>` | 130 |
| `view_mode` / `mode=` / views-tuples / `tree_view_ref` (XML) | 70 |
| `'view_mode'` and views-tuples (Python) | 41 |

XML *ids* containing the word `tree` (e.g. `view_asset_camera_tree`) were
deliberately left alone — renaming them would break `ref=` lookups and
existing `ir_model_data` rows for no benefit.

## 2. Groups / category changes (Odoo 19)

`res.groups.category_id` was removed and replaced by `privilege_id`, a
Many2one to the **new `res.groups.privilege` model**, which itself carries
the `ir.module.category`.

The original five groups had **no category at all**, so they would have
appeared ungrouped in the user form. `security/asset_security_groups.xml`
was rewritten to add:

- `ir.module.category` → `module_category_asset_management`
- `res.groups.privilege` → `res_groups_privilege_asset_management`
  (Asset User → Manager → Admin, the implied_ids ladder)
- `res.groups.privilege` → `res_groups_privilege_asset_department`
  (IT User, HR)

The department groups were put on a **separate privilege on purpose**. A
privilege renders as a single selection, so folding all five into one would
have made IT/HR mutually exclusive with the Manager/Admin ladder — a
behaviour change versus v17, where they were independent checkboxes.

`ir.rule.groups` is **unchanged** in Odoo 19 — verified against
`odoo/addons/base/models/ir_rule.py@19.0`, which still declares
`groups = fields.Many2many('res.groups', 'rule_group_rel', ...)`. The
`groups_id` → `group_ids` rename applies to `res.users`, not to `ir.rule`.

**Not applicable to this module:** the `res.users.groups_id` → `group_ids`
and `res.groups.users` → `user_ids` renames. The module never referenced
either field, so that cascade (which also touches `ir.actions.*`,
`ir.ui.view`, `ir.ui.menu`) does not affect it.

## 3. JSON-RPC changes

**Server** — `type='json'` was removed in Odoo 19 in favour of
`type='jsonrpc'`. 12 routes updated across `ksc_controller.py` and
`asset_agent_api.py`. Routes already on `type='http'` were left untouched,
including `/api/antivirus/deploy`, which parses
`request.httprequest.data` directly and is not a JSON-RPC endpoint.

**Client** — the three antivirus dashboards were bypassing the framework
with raw `fetch()` and hand-built envelopes:

```js
// before
const result = await fetch('/api/antivirus/ksc/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', method: 'call', id: 1, params: {} }),
}).then(r => r.json());
const data = result.result || result;

// after
const data = await rpc('/api/antivirus/ksc/test', {});
```

15 call sites converted across `linux_`, `macos_` and
`windows_antivirus_dashboard.js`, with
`import { rpc } from "@web/core/network/rpc";` added to each. `rpc()`
builds the envelope, forwards session context and CSRF, unwraps `result`,
and routes failures through the error service — none of which the raw
`fetch` did.

## 4. Other breaking changes fixed

- **`name_get()` removed** (Odoo 18) → 4 overrides converted to
  `_compute_display_name()` with `@api.depends`:
  `asset_agent`, `asset_network_device`, `asset_storage_volume`, `asset_camera`.
- **`read_group()` removed** (Odoo 18) → 5 calls converted to `_read_group()`.
  Note the return shape changed from dicts to **tuples**, and the groupby
  value for a Many2one is now a **recordset**, not an `(id, name)` pair.
  Consuming loops were rewritten accordingly:
  ```python
  # before                          # after
  for group in data:                for state, count in data:
      state = group.get("state")        ...
      count = group.get("__count", 0)
  ```
- **`t-name="kanban-box"` removed** (Odoo 19) → 7 templates renamed to
  `t-name="card"`.
- **`kanban_image()` QWeb helper removed** → 2 uses rewritten as direct
  `/web/image/<model>/<id>/<field>` URLs.
- **Chatter** → 9 `<div class="oe_chatter">…</div>` blocks replaced with
  `<chatter/>`.
- **`t-esc` → `t-out`** on 19 server-side QWeb occurrences in `views/`.
  The 472 occurrences inside `static/src/xml/` were **left alone** — those
  are OWL templates, where `t-esc` remains valid.
- **`owl="1"`** added to 3 templates that were missing it.

## 5. Already clean in the v17 source

No `attrs=` / `states=`, no `self._uid`, no `odoo.osv.expression`, no
`user_has_groups`, no `res.config.settings`. JS registry registrations were
already in 17+ object form (`{component, supportedTypes}`) and carry over
to 19 unchanged, as do the `useService("orm"/"notification"/"action")` hooks.

The `hr` dependency was checked against Odoo 19's `hr_contract` → core merge
and `hr.contract` → `hr.version` rename: the module only uses `hr.employee`
and `hr.department`, both unaffected.

## 6. Verification performed

- All 100+ XML files parse (`ElementTree`)
- All Python compiles (`compileall`, exit 0)
- All 21 JS files parse clean as ESM (`node --check`)
- All 67 manifest `data` entries and 62 asset-bundle paths resolve on disk
- Residual grep audit for every v17 pattern returns clean

## 7. What still needs a running instance

Static analysis cannot confirm these — smoke-test after install:

1. **`hr.view_employee_form` inherit** — the only inherit into a core view.
   Core form structure shifts between majors; verify the xpath still lands.
2. **Two `patch(ListRenderer.prototype)` and one
   `patch(KanbanRenderer.prototype)`** in `antivirus.js` /
   `asset_list_dashboard.js`. Renderer internals are private API and change
   freely between versions; these are the most likely runtime breakage.
3. **Kanban `card` templates** — the wrapper element and click handling
   differ from `kanban-box`; check spacing and that cards open records.
4. **`asset_software_dashboard_action.xml`** points an image tag at
   `installer_file`, a binary installer rather than an image field. This
   was very likely already broken in v17; worth fixing properly rather than
   carrying forward.
5. **Permission check** — log in as each role and confirm access matches
   v17, given the groups were restructured onto privileges.
6. **`external_dependencies`**: `qrcode`, `xlsxwriter`, `requests` must be
   present, and Odoo 19 requires Python 3.10+.

---

## 8. Post-release fix — `asset.agent` display_name

**Symptom:**
```
ValueError: Wrong @depends on '_compute_display_name' (compute method of field
asset.agent.display_name). Dependency field 'name' not found in model asset.agent.
```

**Cause:** the `@api.depends` was derived from the field names appearing in the
v17 `name_get()` body without checking them against the model. `asset.agent`
has **no `name` field** — it declares `_rec_name = 'agent_id'` and stores the
label in `hostname` / `device_name`.

This was latent in v17: `name_get()` had no `@api.depends`, so the bad
reference sat in the fallback chain `device.hostname or device.name` and only
fired when `hostname` was empty. Converting to a computed field turned it into
a load-time validation error.

**Fix:**
```python
# before
@api.depends("hostname", "name", "last_seen", "department_id")
...
f"{indicator} {device.hostname or device.name or 'Unknown'}{user_name}"

# after
@api.depends("hostname", "device_name", "agent_id", "last_seen", "department_id")
...
label = device.hostname or device.device_name or device.agent_id or 'Unknown'
f"{indicator} {label}{user_name}"
```

`agent_id` was added to the fallback chain since it is the model's `_rec_name`
and is required, guaranteeing a non-empty label.

**Audit:** all 65 `@api.depends` declarations in the module were then checked
against the fields actually declared on each of the 67 discovered models,
including a strict pass that assumed nothing about `name` / `state` / `active`.
Zero further mismatches. The other three `name_get` conversions
(`asset_network_device`, `asset_storage_volume`, `asset_camera`) were verified
individually and are correct.

---

## 9. Post-release fixes (round 2) — verified against Odoo 19 source

Rather than continue reasoning from memory, the Odoo 19 sources were
downloaded from `raw.githubusercontent.com/odoo/odoo/19.0` and every claim
in this document was re-checked against them.

### 9.1 `ir.rule.groups` — my error, reverted

**Symptom:** `ParseError ... <record id="rule_asset_remote_own" model="ir.rule">`

I had renamed `ir.rule.groups` → `group_ids`. **That rename does not exist.**
`ir_rule.py@19.0` line 25 still declares:

```python
groups = fields.Many2many('res.groups', 'rule_group_rel', 'rule_group_id', 'group_id', ondelete='restrict')
```

The `groups_id` → `group_ids` rename is on **`res.users`**
(`res_users.py@19.0` line 257) and does not extend to `ir.rule`. Both
records in `asset_remote_security.xml` reverted to `name="groups"`.

### 9.2 `ir.cron.numbercall` / `doall` — pre-existing, removed in Odoo 17

Not caught in round 1. Neither field exists in `ir_cron.py@19.0`. Three XML
occurrences removed:

- `data/asset_update_policy_cron.xml` (`numbercall`, `doall`)
- `data/asset_remote_data.xml` (`numbercall`)

Plus one **Python** occurrence in `models/network_discovery.py`, which built a
one-shot cron via `numbercall: 1`. Since one-shot crons no longer exist, the
job now deactivates itself after running:

```python
cron.code = (
    f"model.browse({self.id}).run_discovery()\n"
    f"env['ir.cron'].browse({cron.id}).write({{'active': False}})"
)
```

### 9.3 Claims re-verified as correct against source

| Claim | Source | Verdict |
|---|---|---|
| `res.groups.privilege_id` replaces `category_id` | `res_groups.py:36` | correct |
| `res.groups.privilege` with `category_id` | `res_groups_privilege.py:13` | correct |
| `res.users.group_ids` (not used by module) | `res_users.py:257` | correct |
| `name_get()` removed | absent from `orm/models.py` | correct |
| `_read_group()` returns `list[tuple]` | `orm/models.py:1861` | correct |
| `read_group()` deprecated | `orm/models.py:2748` | correct |
| kanban `card` replaces `kanban-box` | `kanban_arch_parser.js:8` | correct |
| `type='json'` → `'jsonrpc'` | `http.py:815` | correct (deprecated alias) |

Note `res.groups` also gained `_name_uniq = UNIQUE(privilege_id, name)`.
The two-privilege split in §2 keeps all five group names unique within their
privilege, so this constraint is satisfied.

### 9.4 Automated validation now in place

A validator (`validate_core_fields.py`) parses the Odoo 19 core sources,
resolves both classic (`_inherit`) and delegation (`_inherits`) inheritance,
and checks every `<field name="…">` inside every `<record model="…">` that
targets a core model.

```
Odoo 19 core models resolved: 53
core-model <field> references checked: 1065
PROBLEMS: 0
```

This is the check that would have caught both §9.1 and §9.2 before delivery.
