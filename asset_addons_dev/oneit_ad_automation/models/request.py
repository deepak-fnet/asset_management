# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from markupsafe import Markup

_logger = logging.getLogger(__name__)

REQUEST_TYPES = [
    ('onboarding', 'Onboarding'),
    ('team_update', 'Team Update'),
    ('offboarding', 'Offboarding'),
]

STATES = [
    ('draft', 'Draft'),
    ('pending', 'Pending Team Lead'),
    ('tl_approved', 'Pending BU Head'),
    ('approved', 'Approved'),
    ('ready_to_delete', 'Ready to Delete'),
    ('done', 'Done'),
    ('rejected', 'Rejected'),
]


class OneitRequest(models.Model):
    """The AD lifecycle request.

    Flow: draft -> pending (Team Lead) -> tl_approved (BU Head) -> approved,
    then execution depends on the type:

    * onboarding  -> AD user created synchronously on final approval -> done
    * team_update -> scheduled action moves + regroups the user      -> done
    * offboarding -> scheduled action disables at the last working
                     day (ready_to_delete), deletes after retention -> done
    """
    _name = 'oneit.request'
    _description = 'OneIT AD Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'

    # ------------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------------
    name = fields.Char(
        string='Reference', default='/', copy=False, readonly=True, index=True)
    request_type = fields.Selection(
        REQUEST_TYPES, string='Request Type', required=True,
        default='onboarding', tracking=True)
    state = fields.Selection(
        STATES, string='Status', default='draft', required=True,
        tracking=True, copy=False, index=True)

    submitted_by_id = fields.Many2one(
        'res.users', string='Submitted By', readonly=True, copy=False,
        default=lambda self: self.env.user)
    submitted_on = fields.Datetime(string='Submitted On', readonly=True, copy=False)

    # Employee details
    employee_ref = fields.Char(
        string='Employee ID', required=True, tracking=True,
        help="Used as the AD sAMAccountName / login.")
    employee_name = fields.Char(string='Employee Name', required=True, tracking=True)
    employee_email = fields.Char(string='Email')
    can_edit_ad_access = fields.Boolean(
        string='Can Edit AD Access', compute='_compute_can_edit_ad_access',
        help="Whether the CURRENT VIEWER may select AD access. True for "
             "Team Lead and anyone above (BU Head, IT Admin implies it "
             "through the group hierarchy); false for a pure HR/Requester "
             "user. Not stored - depends on who is looking, not on the "
             "record. Drives the readonly state of ad_group_ids in the "
             "form; the actual enforcement is OneitRequest.write().")

    ad_person_id = fields.Many2one(
        'oneit.ad.person', string='AD Account', ondelete='set null',
        compute='_compute_ad_person_id', store=True,
        help="The directory account this request refers to, matched on AD "
             "username. Empty until the account exists in AD and has been "
             "picked up by the sync.")
    employee_phone = fields.Char(string='Contact Number')
    date_of_joining = fields.Date(string='Date of Joining')
    last_working_day = fields.Date(
        string='Last Working Day',
        help="Offboarding: the AD account is disabled on this date.")

    # Teams
    team_id = fields.Many2one(
        'oneit.team', string='Team', required=True, tracking=True,
        help="Onboarding/Offboarding: the employee's team. "
             "Team Update: the team whose access is being granted.")
    current_team_id = fields.Many2one(
        'oneit.team', string='Current Team',
        help="Team Update: the team the employee is moving away from.")

    # Access selection
    available_ad_group_ids = fields.Many2many(
        'oneit.ad.group', string='Available AD Groups',
        compute='_compute_available_ad_group_ids')
    ad_group_ids = fields.Many2many(
        'oneit.ad.group', 'oneit_request_ad_group_rel', 'request_id', 'ad_group_id',
        string='Granted AD Access', tracking=True,
        help="Selected by the Team Lead at the first approval stage.")
    ad_folder_group_id = fields.Many2one(
        'oneit.ad.group', string='Target OU',
        compute='_compute_ad_folder_group_id', store=False)

    # --- Current access, read live from AD (team updates) --------------
    current_ad_group_ids = fields.Many2many(
        'oneit.ad.group',
        'oneit_request_current_ad_group_rel', 'request_id', 'ad_group_id',
        string='Current Access in AD', readonly=True,
        help="What this user belongs to in Active Directory. Populated by "
             "the Fetch button - it is a snapshot, not a live field.")
    current_access_fetched_on = fields.Datetime(
        string='Access Last Fetched', readonly=True)
    unmapped_group_dns = fields.Text(
        string='Unmapped AD Memberships', readonly=True,
        help="Groups the user belongs to in AD with no matching record "
             "here. Usually means the group sync is overdue.")
    groups_to_add_ids = fields.Many2many(
        'oneit.ad.group', compute='_compute_access_diff',
        string='Will Be Added')
    groups_to_remove_ids = fields.Many2many(
        'oneit.ad.group', compute='_compute_access_diff',
        string='Will Be Removed')

    # Approvals
    approval_ids = fields.One2many(
        'oneit.approval', 'request_id', string='Approvals', copy=False)
    skip_team_lead = fields.Boolean(
        string='Skip Team Lead Approval', tracking=True,
        help="Set automatically when the submitter is themselves a Team Lead "
             "of the target team.")
    can_approve = fields.Boolean(
        string='Can Approve', compute='_compute_can_approve',
        search='_search_can_approve')
    approval_stage_label = fields.Char(
        string='Waiting On', compute='_compute_approval_stage_label')

    # Execution results
    ad_user_dn = fields.Char(string='AD User DN', readonly=True, copy=False)
    executed_on = fields.Datetime(string='Executed On', readonly=True, copy=False)
    disabled_on = fields.Datetime(string='Disabled On', readonly=True, copy=False)
    scheduled_delete_date = fields.Date(
        string='Scheduled Deletion', compute='_compute_scheduled_delete_date',
        store=True)
    execution_log = fields.Text(string='Execution Log', readonly=True, copy=False)
    rejection_reason = fields.Text(
        string='Rejection Reason', copy=False,
        help="Fill this in before clicking Reject.")

    _sql_constraints = [
        ('name_uniq', 'unique(name)', 'The request reference must be unique.'),
    ]

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    @api.depends('team_id', 'team_id.ad_group_ids')
    def _compute_available_ad_group_ids(self):
        for req in self:
            req.available_ad_group_ids = req.team_id.ad_group_ids

    @api.depends('ad_group_ids', 'ad_group_ids.group_type')
    def _compute_ad_folder_group_id(self):
        for req in self:
            folders = req.ad_group_ids.filtered(
                lambda g: g.group_type == 'ad_folder')
            req.ad_folder_group_id = folders[:1]

    @api.depends('last_working_day', 'request_type')
    def _compute_scheduled_delete_date(self):
        retention = self._get_retention_days()
        for req in self:
            if req.request_type == 'offboarding' and req.last_working_day:
                req.scheduled_delete_date = (
                    req.last_working_day + timedelta(days=retention))
            else:
                req.scheduled_delete_date = False

    @api.depends('state', 'approval_ids.state', 'approval_ids.approver_id')
    def _compute_can_approve(self):
        for req in self:
            req.can_approve = bool(req._get_my_pending_approval())

    def _search_can_approve(self, operator, value):
        """Allow ('can_approve', '=', True) in domains and filters."""
        if operator not in ('=', '!='):
            raise UserError(_("Unsupported operator for 'Can Approve'."))
        want = bool(value) if operator == '=' else not bool(value)
        # Requests where the current user has a pending line for the stage the
        # request is actually sitting at.
        approvals = self.env['oneit.approval'].search([
            ('approver_id', '=', self.env.user.id),
            ('state', '=', 'pending'),
            '|',
            '&', ('approver_role', '=', 'team_lead'),
                 ('request_id.state', '=', 'pending'),
            '&', ('approver_role', '=', 'bu_head'),
                 ('request_id.state', '=', 'tl_approved'),
        ])
        request_ids = approvals.mapped('request_id').ids
        return [('id', 'in' if want else 'not in', request_ids)]

    @api.depends('state', 'approval_ids.state')
    def _compute_approval_stage_label(self):
        for req in self:
            if req.state == 'pending':
                names = req.approval_ids.filtered(
                    lambda a: a.approver_role == 'team_lead'
                    and a.state == 'pending').mapped('approver_id.name')
                req.approval_stage_label = ', '.join(names) or _('No Team Lead set')
            elif req.state == 'tl_approved':
                names = req.approval_ids.filtered(
                    lambda a: a.approver_role == 'bu_head'
                    and a.state == 'pending').mapped('approver_id.name')
                req.approval_stage_label = ', '.join(names) or _('No BU Head set')
            else:
                req.approval_stage_label = ''

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @api.model
    def _get_param(self, key, default=''):
        return self.env['ir.config_parameter'].sudo().get_param(key, default)

    @api.model
    def _get_retention_days(self):
        try:
            return int(self._get_param('oneit_ad.retention_days', '30'))
        except (TypeError, ValueError):
            return 30

    @api.model
    def _get_max_daily_onboarding(self):
        try:
            return int(self._get_param('oneit_ad.max_daily_onboarding', '10'))
        except (TypeError, ValueError):
            return 10

    def _current_stage_role(self):
        self.ensure_one()
        if self.state == 'pending':
            return 'team_lead'
        if self.state == 'tl_approved':
            return 'bu_head'
        return False

    def _get_my_pending_approval(self):
        """The approval line the current user may act on right now."""
        self.ensure_one()
        role = self._current_stage_role()
        if not role:
            return self.env['oneit.approval']
        return self.approval_ids.filtered(
            lambda a: a.state == 'pending'
            and a.approver_role == role
            and a.approver_id == self.env.user)[:1]

    def _log(self, message):
        self.ensure_one()
        stamp = fields.Datetime.to_string(fields.Datetime.now())
        existing = self.execution_log or ''
        self.sudo().write({
            'execution_log': '%s[%s] %s' % (
                existing + '\n' if existing else '', stamp, message),
        })
        self.message_post(body=message)

    # ------------------------------------------------------------------
    # Onchange / constraints
    # ------------------------------------------------------------------
    @api.depends_context('uid')
    def _compute_can_edit_ad_access(self):
        # has_group() walks implied_ids itself, so this correctly returns
        # True for team_lead, bu_head, AND it_admin - each implies
        # team_lead in the hierarchy - and False for a user who is only
        # in group_oneit_hr. Same predicate the write() guard uses, so UI
        # and enforcement never disagree.
        can_edit = self.env.user.has_group(
            'oneit_ad_automation.group_oneit_team_lead')
        for req in self:
            req.can_edit_ad_access = can_edit

    @api.depends('employee_ref')
    def _compute_ad_person_id(self):
        Person = self.env['oneit.ad.person']
        for req in self:
            req.ad_person_id = Person.search(
                [('username', '=ilike', req.employee_ref)], limit=1
            ) if req.employee_ref else False

    @api.depends('ad_group_ids', 'current_ad_group_ids')
    def _compute_access_diff(self):
        """Show the approver the CHANGE, not two lists to compare by eye."""
        member_types = ('citrix', 'file_server')
        for req in self:
            # AD Folder is a move, not a membership change, so it is shown
            # separately on the form and excluded from this diff.
            cur = req.current_ad_group_ids.filtered(
                lambda g: g.group_type in member_types)
            wanted = req.ad_group_ids.filtered(
                lambda g: g.group_type in member_types)
            req.groups_to_add_ids = wanted - cur
            req.groups_to_remove_ids = cur - wanted

    def action_fetch_current_access(self):
        """Read the user's live memberships from AD and map them to records.

        Matching is on distinguished_name, never on name: two groups in
        different OUs can share a CN, and AD returns full DNs in memberOf.
        """
        self.ensure_one()

        if not self.employee_ref:
            raise UserError(_(
                "Set the employee's AD username before fetching access."))

        connector = self.env['oneit.ad.connector']

        user_dn = connector.find_user_dn(self.employee_ref)
        if not user_dn:
            raise UserError(_(
                "No account found in Active Directory for '%s'.\n\n"
                "Check the username - or the account may already have been "
                "removed."
            ) % self.employee_ref)

        member_of = connector.get_user_groups(self.employee_ref)

        AdGroup = self.env['oneit.ad.group'].with_context(active_test=False)
        matched = AdGroup.browse()
        unmapped = []

        for dn in member_of:
            group = AdGroup.search(
                [('distinguished_name', '=ilike', dn)], limit=1)
            if group:
                matched |= group
            else:
                unmapped.append(dn)

        # The OU the account currently sits in, derived from its own DN by
        # dropping the leading CN= component.
        current_ou_dn = ','.join(user_dn.split(',')[1:])
        current_ou = AdGroup.search([
            ('distinguished_name', '=ilike', current_ou_dn),
            ('group_type', '=', 'ad_folder'),
        ], limit=1)
        if current_ou:
            matched |= current_ou
        elif current_ou_dn:
            unmapped.append(_('%s (current OU)') % current_ou_dn)

        self.write({
            'current_ad_group_ids': [(6, 0, matched.ids)],
            'current_access_fetched_on': fields.Datetime.now(),
            'unmapped_group_dns': '\n'.join(unmapped) if unmapped else False,
            'ad_user_dn': user_dn,
        })

        # Seed the request from reality on a fresh draft only - never
        # clobber a selection someone has already made.
        if self.state == 'draft' and not self.ad_group_ids:
            self.ad_group_ids = [(6, 0, matched.ids)]

        message = _("Found %s mapped membership(s).") % len(matched)
        if unmapped:
            message += _(
                "\n\n%s membership(s) have no matching record here. Run "
                "Configuration > AD Groups > Sync from Active Directory."
            ) % len(unmapped)
        if connector._simulate():
            message += _(
                "\n\nSIMULATION MODE - no real directory was read.")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Current Access Fetched"),
                'message': message,
                'type': 'warning' if unmapped else 'success',
                'sticky': bool(unmapped),
            },
        }

    @api.onchange('team_id', 'request_type')
    def _onchange_team_id(self):
        """Set the skip-team-lead flag. Deliberately does NOT pre-fill the
        access selection.

        Earlier versions loaded the team's entire group list as a default.
        That is an anti-pattern for an approval step: the Team Lead is
        supposed to make a positive choice about what this person needs,
        and a pre-ticked list turns that into a rubber stamp - the path of
        least resistance becomes "grant everything the team can grant".

        Starting empty forces a deliberate selection. The team's groups
        are still the only options offered; they just are not pre-chosen.
        """
        self.skip_team_lead = bool(
            self.team_id and self.env.user in self.team_id.team_lead_ids)

    @api.constrains('request_type', 'last_working_day', 'date_of_joining')
    def _check_dates(self):
        for req in self:
            if req.request_type == 'offboarding' and not req.last_working_day:
                raise ValidationError(_(
                    "An offboarding request needs a Last Working Day - it is "
                    "the date the AD account gets disabled."))

    @api.constrains('employee_ref')
    def _check_duplicate_onboarding(self):
        for req in self:
            if req.request_type != 'onboarding':
                continue
            duplicate = self.search([
                ('id', '!=', req.id),
                ('request_type', '=', 'onboarding'),
                ('employee_ref', '=', req.employee_ref),
                ('state', 'not in', ('rejected',)),
            ], limit=1)
            if duplicate:
                raise ValidationError(_(
                    "An onboarding request already exists for employee ID "
                    "'%s' (%s)."
                ) % (req.employee_ref, duplicate.name))

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'oneit.request') or '/'
        return super(OneitRequest, self).create(vals_list)

    def unlink(self):
        is_it_admin = self.env.user.has_group(
            'oneit_ad_automation.group_oneit_it_admin')
        for req in self:
            # HR may only remove their own untouched drafts. A rejected
            # request is part of the audit trail - the record of a refusal
            # is often more important than the record of an approval - so
            # only IT can remove those.
            allowed = ('draft', 'rejected') if is_it_admin else ('draft',)
            if req.state not in allowed:
                if req.state == 'rejected':
                    raise UserError(_(
                        "Request %s was rejected and is kept as a record. "
                        "Only IT can delete it."
                    ) % req.name)
                raise UserError(_(
                    "Request %s is %s and cannot be deleted. Reject it "
                    "instead."
                ) % (req.name, dict(STATES).get(req.state)))
        return super(OneitRequest, self).unlink()

    def write(self, vals):
        """Keep AD access selection out of the requester's hands.

        Requirement: HR raises the request but never decides access. The
        form makes the field readonly for them, but a readonly widget is a
        UI convention, not a security boundary - it can be bypassed from
        the RPC layer. This is the actual enforcement.
        """
        if 'ad_group_ids' in vals and not self.env.su:
            user = self.env.user
            is_approver = user.has_group(
                'oneit_ad_automation.group_oneit_team_lead')
            if not is_approver:
                raise UserError(_(
                    "AD access is selected by the Team Lead at the approval "
                    "stage. Submit the request and the Team Lead will decide "
                    "what access to grant."))
        return super(OneitRequest, self).write(vals)

    # ------------------------------------------------------------------
    # Workflow: submit
    # ------------------------------------------------------------------
    def action_submit(self):
        for req in self:
            if req.state != 'draft':
                raise UserError(_("Only draft requests can be submitted."))
            req._check_daily_limit()
            req._validate_before_submit()
            req._build_approvals()

            skip_tl = req.skip_team_lead or not req.team_id.team_lead_ids
            req.write({
                'state': 'tl_approved' if skip_tl else 'pending',
                'submitted_by_id': req.submitted_by_id.id or self.env.user.id,
                'submitted_on': fields.Datetime.now(),
            })
            if skip_tl:
                req.approval_ids.sudo().filtered(
                    lambda a: a.approver_role == 'team_lead').write({
                        'state': 'skipped',
                        'acted_on': fields.Datetime.now(),
                    })
                req._log(_("Submitted. Team Lead stage skipped."))
                req._notify_stage('bu_head')
            else:
                req._log(_("Submitted for Team Lead approval."))
                req._notify_stage('team_lead')
        return True

    def _check_daily_limit(self):
        self.ensure_one()
        if self.request_type != 'onboarding':
            return
        limit = self._get_max_daily_onboarding()
        if limit <= 0:
            return
        today = fields.Date.context_today(self)
        count = self.search_count([
            ('request_type', '=', 'onboarding'),
            ('state', 'not in', ('draft', 'rejected')),
            ('submitted_on', '>=', '%s 00:00:00' % today),
            ('submitted_on', '<=', '%s 23:59:59' % today),
        ])
        if count >= limit:
            raise UserError(_(
                "Today's onboarding request limit (%s) has been reached. "
                "Adjust it under Settings > OneIT AD Automation."
            ) % limit)

    def _validate_before_submit(self):
        self.ensure_one()
        if not self.team_id.ad_group_ids:
            raise UserError(_(
                "Team '%s' has no AD groups mapped. Configure its access "
                "footprint first (OneIT > Configuration > Teams)."
            ) % self.team_id.name)
        if not self.team_id.bu_head_ids:
            raise UserError(_(
                "Team '%s' has no BU Head. Final approval would have nobody "
                "to route to."
            ) % self.team_id.name)
        if self.request_type == 'offboarding' and not self.last_working_day:
            raise UserError(_("Set the Last Working Day before submitting."))

    def _build_approvals(self):
        """Create one approval line per Team Lead and per BU Head."""
        self.ensure_one()
        # Building approval lines is part of the submit workflow, not a
        # thing HR does directly - HR's own ACL on oneit.approval is
        # read-only by design, so this uses sudo() to create/replace the
        # lines regardless of who is submitting. The actual gate is
        # action_submit() only being reachable from state == 'draft', not
        # this ACL.
        Approval = self.env['oneit.approval'].sudo()
        self.approval_ids.sudo().unlink()
        lines = []
        for user in self.team_id.team_lead_ids:
            lines.append({
                'request_id': self.id,
                'approver_id': user.id,
                'approver_role': 'team_lead',
                'state': 'pending',
            })
        for user in self.team_id.bu_head_ids:
            lines.append({
                'request_id': self.id,
                'approver_id': user.id,
                'approver_role': 'bu_head',
                'state': 'pending',
            })
        Approval.create(lines)

    def _notify_stage(self, role):
        """Email the approvers of the given stage."""
        self.ensure_one()

        approvers = self.approval_ids.filtered(
            lambda a: a.approver_role == role and a.state == 'pending'
        )

        request_type = (
            'Onboarding'
            if self.request_type == 'onboarding'
            else 'Offboarding'
        )

        record_url = (
            f"{self.get_base_url()}/web#id={self.id}"
            f"&model=oneit.request&view_type=form"
        )

        for approval in approvers:
            partner = approval.approver_id.partner_id
            email = partner.email

            if not email:
                _logger.warning(
                    "Approver %s has no email; no notification sent for %s",
                    approval.approver_id.name,
                    self.name,
                )
                continue

            subject = (
                f"Action Required: {request_type} - "
                f"{self.employee_name or ''} ({self.employee_ref or ''})"
            )

            body_html = Markup(f"""
<div style="font-family:Arial,sans-serif;font-size:14px;color:#333;">
    <p>Hello {approval.approver_id.name},</p>

    <p>
        Request <strong>{self.name or ''}</strong> for
        <strong>{self.employee_name or ''} - {self.employee_ref or ''}</strong>
        requires your review and approval.
    </p>

    <table cellpadding="4" cellspacing="0" border="0">
        <tr>
            <td><strong>Request Type</strong></td>
            <td>{request_type}</td>
        </tr>
        <tr>
            <td><strong>Team</strong></td>
            <td>{self.team_id.name or ''}</td>
        </tr>
        <tr>
            <td><strong>Submitted By</strong></td>
            <td>{self.submitted_by_id.name or ''}</td>
        </tr>
    </table>

    <p style="margin-top:20px;">
        <a href="{record_url}"
           style="background:#0b5ed7;color:#fff;padding:10px 18px;
                  text-decoration:none;border-radius:4px;">
            Review &amp; Approve Request
        </a>
    </p>

    <p>Regards,<br/><strong>IT Support Team</strong></p>
</div>
""")

            mail_values = {
                'subject': subject,
                'body_html': body_html,
                'email_to': email,
                'email_from': (
                        self.env.user.email_formatted
                        or self.env.company.email
                ),
                'author_id': self.env.user.partner_id.id,
                'auto_delete': False,
                'model': self._name,
                'res_id': self.id,
            }

            self.env['mail.mail'].sudo().create(mail_values)

        return True

    # ------------------------------------------------------------------
    # Workflow: approve / reject
    # ------------------------------------------------------------------
    def action_approve(self):
        for req in self:
            approval = req._get_my_pending_approval()
            if not approval:
                raise UserError(_(
                    "You are not a pending approver for %s at this stage."
                ) % req.name)
            role = approval.approver_role

            if role == 'team_lead':
                req._validate_team_lead_selection()

            approval.write({
                'state': 'approved',
                'acted_on': fields.Datetime.now(),
            })
            # One approver per stage is enough; mark the rest of the stage done.
            req.approval_ids.filtered(
                lambda a: a.approver_role == role
                and a.state == 'pending'
                and a.id != approval.id).write({
                    'state': 'skipped',
                    'acted_on': fields.Datetime.now(),
                })

            if role == 'team_lead':
                req.state = 'tl_approved'
                req._log(_("Approved by Team Lead %s. Access selected: %s") % (
                    self.env.user.name,
                    ', '.join(req.ad_group_ids.mapped('name')) or _('none')))
                req._notify_stage('bu_head')
            else:
                req.state = 'approved'
                req._log(_("Approved by BU Head %s.") % self.env.user.name)
                req._on_final_approval()
        return True

    def _validate_team_lead_selection(self):
        """The Team Lead must leave a usable access selection behind."""
        self.ensure_one()
        if self.request_type == 'offboarding':
            return
        if not self.ad_group_ids:
            raise UserError(_(
                "Select the AD access to grant before approving."))
        folders = self.ad_group_ids.filtered(
            lambda g: g.group_type == 'ad_folder')
        if len(folders) != 1:
            raise UserError(_(
                "Select exactly one AD Folder group - it is the OU the user "
                "object is created in or moved to. Currently selected: %s."
            ) % len(folders))
        outside = self.ad_group_ids - self.team_id.ad_group_ids
        if outside:
            raise UserError(_(
                "These groups are not part of team '%s': %s"
            ) % (self.team_id.name, ', '.join(outside.mapped('name'))))

    def action_reject(self):
        """Open the rejection wizard-less prompt via context, or reject directly."""
        for req in self:
            approval = req._get_my_pending_approval()
            if not approval:
                raise UserError(_(
                    "You are not a pending approver for %s at this stage."
                ) % req.name)
            reason = (self.env.context.get('rejection_reason')
                      or req.rejection_reason or '')
            approval.write({
                'state': 'rejected',
                'acted_on': fields.Datetime.now(),
                'comment': reason,
            })
            req.approval_ids.filtered(
                lambda a: a.state == 'pending').write({
                    'state': 'skipped',
                    'acted_on': fields.Datetime.now(),
                })
            req.write({'state': 'rejected', 'rejection_reason': reason})
            req._log(_("Rejected by %s.%s") % (
                self.env.user.name,
                _(" Reason: %s") % reason if reason else ''))
        return True

    def action_reset_to_draft(self):
        for req in self:
            if req.state not in ('rejected',):
                raise UserError(_("Only rejected requests can be reset to draft."))
            req.approval_ids.sudo().unlink()
            req.write({
                'state': 'draft',
                'rejection_reason': False,
                'submitted_on': False,
            })
            req._log(_("Reset to draft by %s.") % self.env.user.name)
        return True

    def action_revert(self):
        """IT can pull back an offboarding before it completes."""
        for req in self:
            if req.request_type != 'offboarding':
                raise UserError(_("Only offboarding requests can be reverted."))
            if req.state not in ('pending', 'tl_approved', 'approved',
                                 'ready_to_delete'):
                raise UserError(_(
                    "Request %s is %s and can no longer be reverted."
                ) % (req.name, dict(STATES).get(req.state)))
            was_disabled = req.state == 'ready_to_delete'
            req.approval_ids.filtered(lambda a: a.state == 'pending').write({
                'state': 'skipped', 'acted_on': fields.Datetime.now()})
            req.write({'state': 'rejected'})
            msg = _("Offboarding reverted by %s.") % self.env.user.name
            if was_disabled:
                msg += _(" The AD account was already disabled and must be "
                         "re-enabled manually.")
            req._log(msg)
        return True

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def _on_final_approval(self):
        """Onboarding runs now; the other two are picked up by cron."""
        self.ensure_one()
        if self.request_type == 'onboarding':
            self._execute_onboarding()
        elif self.request_type == 'team_update':
            self._log(_("Queued for the team-update scheduled action."))
        elif self.request_type == 'offboarding':
            self._log(_(
                "Approved. The account will be disabled on %s and deleted on %s."
            ) % (self.last_working_day, self.scheduled_delete_date))

    def _execute_onboarding(self):
        """Create the AD user in the target OU and grant group memberships."""
        self.ensure_one()
        connector = self.env['oneit.ad.connector']
        folder = self.ad_folder_group_id
        if not folder or not folder.distinguished_name:
            raise UserError(_(
                "No AD Folder group with a distinguished name is selected; "
                "there is nowhere to create the user object."))

        user_dn = connector.create_user(
            sam_account_name=self.employee_ref,
            display_name=self.employee_name,
            email=self.employee_email,
            user_ou=folder.distinguished_name,
        )
        self.sudo().write({'ad_user_dn': user_dn})
        self._log(_("AD user created: %s") % user_dn)

        for group in self.ad_group_ids.filtered(
                lambda g: g.group_type in ('citrix', 'file_server')):
            connector.add_user_to_group(user_dn, group.distinguished_name)
            self._log(_("Added to %s group: %s") % (
                dict(group._fields['group_type'].selection).get(
                    group.group_type), group.name))

        self.sudo().write({
            'state': 'done',
            'executed_on': fields.Datetime.now(),
        })
        self._log(_("Onboarding completed."))

    def _execute_team_update(self):
        """Move the user to the new OU, strip old groups, apply the new ones."""
        self.ensure_one()
        connector = self.env['oneit.ad.connector']
        folder = self.ad_folder_group_id
        if not folder or not folder.distinguished_name:
            raise UserError(_("No target OU selected for the team update."))

        # Refresh membership at execution time: the request may have been
        # approved days ago and AD can have moved on since.
        live_dns = connector.get_user_groups(self.employee_ref)

        new_dn = connector.move_user(self.employee_ref, folder.distinguished_name)
        self._log(_("Moved to %s") % new_dn)

        requested = self.ad_group_ids.filtered(
            lambda g: g.group_type in ('citrix', 'file_server'))
        requested_dns = {g.distinguished_name.lower()
                         for g in requested if g.distinguished_name}

        # Only remove groups that are (a) currently held, (b) known to this
        # module, and (c) not selected. A membership this module has never
        # seen - VPN, distribution lists, access another team granted - is
        # left alone: we cannot tell "granted deliberately elsewhere" from
        # "should not be there".
        managed = self.env['oneit.ad.group'].with_context(
            active_test=False).search([
                ('group_type', 'in', ('citrix', 'file_server')),
            ])
        managed_dns = {g.distinguished_name.lower()
                       for g in managed if g.distinguished_name}

        to_remove = [dn for dn in live_dns
                     if dn.lower() in managed_dns
                     and dn.lower() not in requested_dns]
        if to_remove:
            connector.remove_user_from_groups(new_dn, to_remove)
            self._log(_("Removed %s membership(s): %s")
                      % (len(to_remove), ', '.join(to_remove)))
        else:
            self._log(_("No memberships to remove."))

        live_lower = {dn.lower() for dn in live_dns}
        added = 0
        for group in requested:
            if group.distinguished_name.lower() in live_lower:
                continue  # already a member
            connector.add_user_to_group(new_dn, group.distinguished_name)
            self._log(_("Added to group: %s") % group.name)
            added += 1
        if not added:
            self._log(_("No new memberships to add."))

        self.sudo().write({
            'state': 'done',
            'ad_user_dn': new_dn,
            'executed_on': fields.Datetime.now(),
        })
        self._log(_("Team update completed."))

    def _execute_offboarding_disable(self):
        self.ensure_one()
        self.env['oneit.ad.connector'].disable_user(self.employee_ref)
        self.sudo().write({
            'state': 'ready_to_delete',
            'disabled_on': fields.Datetime.now(),
        })
        self._log(_("AD account disabled. Deletion scheduled for %s.")
                  % self.scheduled_delete_date)

    def _execute_offboarding_delete(self):
        self.ensure_one()
        self.env['oneit.ad.connector'].delete_user(self.employee_ref)
        self.sudo().write({
            'state': 'done',
            'executed_on': fields.Datetime.now(),
        })
        self._log(_("AD account deleted. Offboarding completed."))

    def action_run_now(self):
        """Manual trigger, so the flow can be tested without waiting for cron."""
        for req in self:
            if req.request_type == 'team_update' and req.state == 'approved':
                req._execute_team_update()
            elif req.request_type == 'offboarding' and req.state == 'approved':
                req._execute_offboarding_disable()
            elif req.request_type == 'offboarding' and req.state == 'ready_to_delete':
                req._execute_offboarding_delete()
            else:
                raise UserError(_(
                    "Nothing to run for %s in state '%s'."
                ) % (req.name, dict(STATES).get(req.state)))
        return True

    # ------------------------------------------------------------------
    # Scheduled actions
    # ------------------------------------------------------------------
    @api.model
    def cron_apply_team_update(self):
        """Equivalent of the Django ``ad_update`` management command."""
        requests = self.search([
            ('request_type', '=', 'team_update'),
            ('state', '=', 'approved'),
        ])
        _logger.info("OneIT cron: %s team update(s) to apply", len(requests))
        for req in requests:
            try:
                req._execute_team_update()
                self.env.cr.commit()
            except Exception as exc:
                self.env.cr.rollback()
                _logger.exception("Team update failed for %s", req.name)
                req._log(_("Team update FAILED: %s") % exc)
                self.env.cr.commit()
        return True

    @api.model
    def cron_disable_offboarded_users(self):
        """Equivalent of the Django ``ad_disable`` management command."""
        today = fields.Date.context_today(self)
        requests = self.search([
            ('request_type', '=', 'offboarding'),
            ('state', '=', 'approved'),
            ('last_working_day', '<=', today),
        ])
        _logger.info("OneIT cron: %s account(s) to disable", len(requests))
        for req in requests:
            try:
                req._execute_offboarding_disable()
                self.env.cr.commit()
            except Exception as exc:
                self.env.cr.rollback()
                _logger.exception("Disable failed for %s", req.name)
                req._log(_("Disable FAILED: %s") % exc)
                self.env.cr.commit()
        return True

    @api.model
    def cron_delete_offboarded_users(self):
        """Equivalent of the Django ``ad_remove_user`` management command."""
        today = fields.Date.context_today(self)
        requests = self.search([
            ('request_type', '=', 'offboarding'),
            ('state', '=', 'ready_to_delete'),
            ('scheduled_delete_date', '<=', today),
        ])
        _logger.info("OneIT cron: %s account(s) to delete", len(requests))
        for req in requests:
            try:
                req._execute_offboarding_delete()
                self.env.cr.commit()
            except Exception as exc:
                self.env.cr.rollback()
                _logger.exception("Delete failed for %s", req.name)
                req._log(_("Delete FAILED: %s") % exc)
                self.env.cr.commit()
        return True
