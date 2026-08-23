# asset_workspace — Odoo 17 → 19 Migration Notes

Module version: **17.0.1.0.0 → 19.0.1.0.0**
Size: 11 files, 1,317 lines.

## Summary

This module needed **almost nothing**. It was already written against the
Odoo 17.2+ / OWL 2 idiom, which carries forward to 19 unchanged. Every claim
below was checked against the Odoo 19 sources on
`raw.githubusercontent.com/odoo/odoo/19.0`, not from memory.

## Changes made

| # | Change | Reason |
|---|---|---|
| 1 | `version` → `19.0.1.0.0` | required |
| 2 | Dropped unused `from odoo import api, SUPERUSER_ID` in `hooks.py` | dead import |
| 3 | Removed duplicate `menu_asset_list_root` in `LEGACY_MENUS_TO_HIDE` | pre-existing |
| 4 | Removed duplicate `menu_ws_config_remote` in `OPTIONAL_MENUS` | pre-existing |

Items 3 and 4 are **pre-existing bugs, not migration issues**. Both were
harmless — the duplicate hide was idempotent, and the duplicate menu entry was
skipped by the `env.ref(...)` existence check on the second pass — but both
were plainly unintended.

## Verified as already correct (no change needed)

| Concern | Source checked | Result |
|---|---|---|
| `<menuitem groups="…">` still supported | `tools/convert.py:318-327` | attribute is read and written to `group_ids` automatically — no change needed |
| `ir.ui.menu.group_ids` (renamed from `groups_id`) | `ir_ui_menu.py:29` | module never writes this field directly |
| `post_init_hook(env)` signature | `modules/loading.py:241` | still `hook(env)` |
| `uninstall_hook(env)` signature | `modules/loading.py:544` | still `hook(env)` |
| `fields.Datetime.subtract` | `orm/fields_temporal.py:33` | still present |
| OWL templates need `owl="1"` | `web/.../kanban_renderer.xml` | core uses plain `<templates xml:space="preserve">` — this module already matches |
| `t-esc` in OWL templates | — | valid in OWL 2; only *server-side* QWeb prefers `t-out` |
| `useService("orm"/"action"/"notification")` | — | all still present in 19 |
| `registry.category("actions").add(tag, Component)` | — | unchanged |
| `views: [[false, "list"]]` in `doAction` | — | already uses `list`, not `tree` |

## Clean on every v17 → v19 breaking pattern

Scanned and clear: `<tree>`, `view_mode` with `tree`, `kanban-box`,
`oe_chatter`, `name_get`, `read_group`, `type='json'`, `numbercall`/`doall`,
`attrs=`/`states=`, `user_has_groups`, `res.users.groups_id`,
`ir.rule` field names, `self._uid`, `kanban_image()`, hand-rolled JSON-RPC
envelopes, `useService("rpc")`.

The model uses only `search_count`, `env.ref(..., raise_if_not_found=False)`
and `_fields` introspection — all stable APIs.

## Cross-module reference check

The workspace references `asset_management` XML IDs heavily. All 45 distinct
references were resolved against the **migrated** `asset_management`:

```
asset_management XML ids defined  : 310
distinct refs from asset_workspace: 45
HARD refs that would fail install : 0
```

One soft reference, `asset_management.action_remote_session` (`hooks.py:85`),
does not exist — but it is the first of three candidates, and the second,
`asset_management.action_asset_remote_session`, is defined in
`views/asset_remote_session_views.xml:120`. The fallback design works as
intended.

`asset_management.group_asset_manager` and `menu_asset_root`, used by
`menus.xml` and `telemetry_menus.xml`, both resolve.

## Undeclared dependencies (unchanged from v17, worth knowing)

`hooks.py` and the dashboard registry reference modules that are **not** in
`depends`: `asset_helpdesk`, `asset_ticket`, `asset_remote`,
`asset_patch_vuln`, `asset_security_console`. This is deliberate — every one
is looked up with `raise_if_not_found=False` and logged rather than raised, so
the module installs and works without them. No change made.

Declared dependencies are `asset_management`, `asset_telemetry`,
`asset_telemetry_alert`. **Both telemetry modules must also be migrated to 19
before this module can install.**

## Still needs a running instance

1. The client action renders via `registry.category("actions")` — confirm the
   dashboard opens and `get_hub_data` returns.
2. `post_init_hook` menu rewiring — check the log line
   `[Workspace] Wired optional menus: …` and any `Could not resolve` warnings.
3. Uninstall and confirm legacy menus reappear.

---

## 10. Post-install fix — non-stored field in a search domain

**Symptom**
```
ValueError: Cannot convert asset.asset.is_online to SQL because it is not stored
  ... asset_hub_dashboard.py line 159, in _count_assets
      online = Asset.search_count(domain + [('is_online', '=', True)])
```

**Cause — a pre-existing bug, not a rename.** `_count_assets` guarded on
*field existence*:

```python
if 'is_online' in Asset._fields:
```

But `asset.asset.is_online` is declared `compute="_compute_live_metrics",
store=False` with **no `search=` method** (`asset_asset.py:860`). Presence in
`_fields` says nothing about whether a field can go in a domain. A non-stored
computed field without a search method cannot be converted to SQL.

**Fix.** A helper that tests searchability rather than existence:

```python
def _is_searchable(model, field_name):
    field = model._fields.get(field_name)
    return bool(field) and (field.store or field.search)
```

and a corrected fallback chain that prefers **`agent_status`** — also
non-stored, but it defines `search="_search_agent_status"`
(`asset_asset.py:971`), which resolves to `last_sync_time` against the
`asset_management.agent_heartbeat_timeout` config parameter. Using it means
the hub's online/offline split agrees with the per-platform dashboards
instead of inventing its own 30-minute window.

Verified storedness of every field this module puts in a domain:

| Field | Status |
|---|---|
| `asset.asset.is_online` | not stored, no `search=` → **unusable** |
| `asset.asset.agent_status` | not stored, **searchable** → now used |
| `asset.asset.last_sync_time` | stored → fallback |
| `asset.asset.last_agent_sync` | stored → last-resort fallback |
| `asset.camera.is_online` | stored |
| `asset.network.device.connection_status` | stored |
| `asset.asset.security_status` / `last_vuln_scan` | absent (from `asset_patch_vuln`) → guarded |

**Bonus fix.** `_count_other('asset.network.device')` previously tested
`is_online` then `status`; that model has **neither** — it has
`connection_status`. So network devices always reported `online: 0`. A
`connection_status` branch was added.

**Also hardened** (same class of bug, would have crashed if
`asset_patch_vuln` were installed with non-stored fields):
`get_fleet_health`'s `last_vuln_scan` check and `get_attention_items`'
`security_status` check now use `_is_searchable` too.

The original 30-minute-window fallback also called
`fields.Datetime.subtract(...)`, which is valid but inconsistent with the
rest of the module; the fallbacks now use the shared `_heartbeat_timeout()`
helper.
