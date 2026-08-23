# OneIT — Active Directory Automation (Odoo 14)

Port of the OneIT Django application's AD lifecycle workflow to Odoo 14.

```
Request created (pending)
        ↓
Team Lead approves  ── selects the AD access to grant
        ↓
BU Head approves    ── status: approved
        ↓
   ┌────────────────┬────────────────────┬─────────────────────────┐
Onboarding      Team update           Offboarding
creates the     cron: moves OU +      cron: disables at LWD
AD user now     regroups              → Ready to delete
        ↓               ↓             → cron deletes after N days
      Done            Done                      → Done
```

Any request can be rejected at either approval stage. IT can revert an
offboarding before it completes.

## Requirements

- Odoo 14.0
- Python: `ldap3`

```bash
pip3 install ldap3
```

## Installation

1. Copy `oneit_ad_automation/` into your Odoo addons path.
2. Restart Odoo, then Apps → Update Apps List.
3. Install **OneIT - Active Directory Automation**.
   Install with demo data enabled to get two sample teams and their AD groups.

## Simulation mode (start here)

The module installs with **Simulation Mode ON**
(`Settings → OneIT AD Automation`). No LDAP calls are made — every AD
operation is written to the Odoo log and reported as successful. This lets
you validate the whole approval workflow before pointing it at a domain
controller. Turn it off only once the flow is confirmed.

## Configuration

**Settings → OneIT AD Automation**

| Setting | Notes |
|---|---|
| Simulation Mode | ON = no LDAP calls, log only |
| LDAP Server URI | `ldaps://10.4.10.10:636` |
| Bind DN / Password | service account used for all AD writes |
| Base DN | `DC=example,DC=local` |
| AD Domain | builds the `userPrincipalName` |
| Default New-User Password | initial password on created accounts |
| Deletion Delay (days) | days after the last working day before deletion (default 30) |
| Max Onboarding Per Day | 0 disables the cap |

**OneIT → Configuration → AD Groups** — register each OU and security group.
Tag each one:

- `AD Folder` — the OU the user object itself is created in / moved to
- `Citrix` / `File Server` — memberships granted to the user

**OneIT → Configuration → Teams** — a team is the unit of access. Map it to
its AD groups (exactly one AD Folder) and name its **Team Leads** (first
approval) and **BU Heads** (final approval). Approvers need an email address
to be notified.

## Security groups

| Group | Can do |
|---|---|
| HR / Requester | create and submit requests, see their own |
| Team Lead | + first-stage approval, selects the AD access |
| BU Head | + final approval, which triggers the AD change |
| IT Administrator | + configuration, revert offboardings, run AD actions manually |

Each group implies the one above it. Assign them under
Settings → Users & Companies → Users.

## Scheduled actions

Three crons replace the Django management commands. **They install
inactive** — enable them once AD is configured
(Settings → Technical → Automation → Scheduled Actions).

| Scheduled action | Replaces | Default |
|---|---|---|
| Apply approved team updates | `ad_update` | hourly |
| Disable offboarded AD accounts | `ad_disable` | daily |
| Delete offboarded AD accounts | `ad_remove_user` | daily |

For testing, the **Run AD Action Now** button on an approved request (IT
group) triggers the same code path immediately, so you don't have to wait
for or enable a cron.

## Sample data

Install with **demo data enabled** (Odoo loads the `demo` files) and you get
a full working dataset: 6 users, 18 AD groups, 5 teams, 11 requests and 30
approval lines.

### Demo users — password is `demo` for all of them

| Login | Name | Role |
|---|---|---|
| `aarti.hr` | Aarti Menon | HR / Requester |
| `rohan.tl` | Rohan Iyer | Team Lead (Finance, Sales, HR Ops) |
| `vikram.tl` | Vikram Nair | Team Lead (Engineering, Support) |
| `priya.bu` | Priya Raghavan | BU Head (Finance, Engineering, Sales) |
| `deepa.bu` | Deepa Krishnan | BU Head (Support, HR Ops) |
| `suresh.it` | Suresh Babu | IT Administrator |

`admin` is also listed as both Team Lead and BU Head on **Finance** and
**Engineering**, so you can walk both approval stages without switching users.

### Teams and their AD groups

| Team | AD Folder (OU) | Citrix | File Server |
|---|---|---|---|
| Finance | FIN-Users-OU | ERP, BI | Accounts, Payroll |
| Engineering | ENG-Users-OU | DevTools | Source, Builds |
| Sales | SLS-Users-OU | CRM | Proposals |
| Support | SUP-Users-OU | Helpdesk | KnowledgeBase |
| HR Operations | HRO-Users-OU | HRMS | Records |

### Sample requests — one per state

| Request | Type | State | Team | Use it to test |
|---|---|---|---|---|
| Nithya Balan | Onboarding | Draft | Support | submitting a request |
| Karthik Subramanian | Onboarding | Pending Team Lead | Engineering | the Team Lead stage (admin can approve) |
| Sneha Pillai | Onboarding | Pending BU Head | Sales | the BU Head stage → AD user creation |
| Arjun Venkatesh | Onboarding | Done | Finance | a completed audit trail |
| Manoj Gupta | Onboarding | Rejected | Finance | Reset to Draft |
| Ramesh Kumar | Offboarding | Pending Team Lead | Support | the offboarding approval chain |
| Anand Mohan | Offboarding | Approved | Engineering | a disable that is not yet due (LWD +3 days) |
| Lakshmi Narayan | Offboarding | Ready to Delete | Finance | the delete step — already past its retention date |
| Farida Sheikh | Offboarding | Done | Sales | a finished offboarding |
| Divya Ramesh | Team Update | Pending BU Head | Support → Engineering | approve, then Run AD Action Now |
| Sanjay Desai | Team Update | Done | HR Ops → Finance | a completed move |

All dates are relative to the install date, so the records stay meaningful
whenever you load them.

> The two records that are *due* for a cron action (Lakshmi Narayan for
> deletion, and Anand Mohan once his last working day arrives) will be acted
> on if you enable the scheduled actions. With Simulation Mode ON that is
> harmless — it just writes to the log. Don't load demo data into an instance
> pointed at a real domain controller.

## Testing the flow as a single user

Because demo data makes `admin` both Team Lead and BU Head of each team, you
can exercise both approval stages alone.

1. **Onboarding** — OneIT → Requests → Onboarding → Create.
   Fill in Employee ID, Name, Email, Team, Date of Joining.
   Leave **Skip Team Lead Approval** unchecked so the full two-stage path is
   exercised. Submit.
   - Status is `Pending Team Lead`. Open the AD Access tab, trim the list to
     what should be granted (keep exactly one AD Folder), click **Approve**.
   - Status is `Pending BU Head`. Click **Approve** again → the AD user is
     created immediately and the status becomes `Done`.
   - Check the Execution tab log and the chatter for the AD operations.
2. **Offboarding** — same two approvals, but set a **Last Working Day** in
   the past so it is immediately due. After final approval the status is
   `Approved`; click **Run AD Action Now** → the account is disabled and the
   status becomes `Ready to Delete`. Run it again once the scheduled
   deletion date has passed → `Done`.
3. **Team update** — pick the target team, approve twice, then
   **Run AD Action Now** → the user is moved to the new OU, old groups are
   stripped and the new ones applied.
4. **Rejection** — type a reason in Rejection Reason and click **Reject**.
   IT can then **Reset to Draft**.
5. **My Approvals** (OneIT → Approvals) is the equivalent of the Django
   "Pending Requests" page: it lists only requests sitting at a stage you
   are an approver for.

## Mapping from the Django project

| Django | Odoo 14 |
|---|---|
| `ADGroup` | `oneit.ad.group` |
| `Team` (+ `adgroup` m2m) | `oneit.team` |
| `AppUser.role` (TeamCategory) | security groups + team lead/BU head lists |
| `Request` + `RequestDetails` JSON | `oneit.request` (typed fields) |
| `Approval` | `oneit.approval` |
| `StatusHistory` / `Notification` | `mail.thread` chatter + `execution_log` |
| `AutoITHelper` | `oneit.ad.connector` |
| `ad_update` / `ad_disable` / `ad_remove_user` | `ir.cron` scheduled actions |
| HTML email in views | `mail.template` |
| Role-gated homepage tiles | menus with `groups=` |
| `MAX_ONBOARDING_REQUEST` | Max Onboarding Per Day setting |
| Login OTP | use Odoo's `auth_totp` (two-factor) — not re-implemented |

## Notes / deliberate differences

- **Secrets are not in source.** AD credentials live in
  `ir.config_parameter` via the settings screen, not in a committed file.
- **One approver per stage clears it.** If a team has two Team Leads, the
  first to act clears the stage; the others are marked `skipped`.
- **Exactly one AD Folder** is enforced per team and per approved request —
  the Django version assumed this and crashed when it wasn't true.
- **Duplicate onboarding** for the same Employee ID is blocked (the Django
  form intended this but its validation never ran).
- `Scheduled Deletion` is computed from the last working day and the
  retention setting. Changing the retention setting does not retroactively
  recompute existing requests.
- **OTP login was not ported.** Odoo has its own two-factor support; enable
  `auth_totp` rather than rebuilding the Django OTP flow.
