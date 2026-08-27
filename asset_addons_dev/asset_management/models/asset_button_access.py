# -*- coding: utf-8 -*-
"""Config-driven button visibility (user-wise / group-wise).

Button visibility is normally either hardcoded in XML (groups="...") or
driven by record state. Neither lets an admin decide, from the UI, "only
these specific users" or "only this group" can see a given button without a
code change.

asset.button.access is the config record; asset.button.access.mixin is what
a model inherits to gate one of its own buttons with it. A button with no
configured rule stays visible to everyone - it only becomes gated once an
admin adds a rule for it.
"""

import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)


class AssetButtonAccess(models.Model):
    _name = "asset.button.access"
    _description = "Button Access Control"
    _rec_name = "button_key"

    res_model = fields.Char(
        string="Model",
        required=True,
        help="Technical model name the button lives on, "
             "e.g. asset.exit.process.")
    button_key = fields.Char(
        string="Button",
        required=True,
        help="The button's technical name (its <button name=\"...\"/> in "
             "the view), e.g. action_start. Only buttons a developer has "
             "wired to check asset.button.access can actually be gated "
             "this way - adding a rule for an unwired button has no effect.")
    visibility_type = fields.Selection(
        [("keep", "Keep Original Restriction"),
         ("all", "Everyone (no restriction)"),
         ("user", "User wise"),
         ("group", "Group wise")],
        string="Visibility", required=True, default="keep",
        help="'Keep Original Restriction' does not change who sees the "
             "button at all - it stays whatever it was before this rule "
             "existed (a static groups=\"...\" in the view, or open to "
             "everyone if it had none). Use this when the only reason for "
             "this rule is the Python Code below - e.g. sending a mail on "
             "trigger - and you do NOT want to touch visibility.\n"
             "'Everyone' is different: it FORCES the button open to "
             "everyone, overriding any restriction that existed before.")
    user_ids = fields.Many2many("res.users", string="Users")
    group_id = fields.Many2one("res.groups", string="Group")
    active = fields.Boolean(default=True)

    # Optional: run arbitrary Python whenever the gated button is actually
    # triggered - e.g. send a mail. Evaluated the same way ir.actions.server
    # evaluates its own "Execute Python Code" field (see
    # _run_button_access_action below), so the same record/records/env
    # sandbox is available here, without needing an actual ir.actions.server
    # record to point at.
    code = fields.Text(
        string="Python Code",
        help="Optional. Runs whenever this button is triggered, in "
             "addition to the visibility rule above. Same sandbox as a "
             "server action's Execute Python Code: record, records, env, "
             "model are available. Example:\n"
             "for rec in records:\n"
             "    template = env.ref('module.my_email_template')\n"
             "    template.send_mail(rec.id, force_send=True)")

    # Extra visibility condition, ORed with whichever Visibility type is
    # picked above: "no restriction" OR condition, "user wise" OR
    # condition, "group wise" OR condition. Lets a rule grant visibility to
    # someone the static user/group list cannot express - e.g. "whoever is
    # this specific record's department manager", which is a per-record
    # fact, not a fixed list of people.
    condition = fields.Text(
        string="Extra Condition",
        help="Optional. A Python boolean expression, evaluated per record - "
             "self/record is the record the button is on, env and user are "
             "also available. The button is visible if EITHER the "
             "Visibility rule above matches OR this expression is True. "
             "A condition that raises an error is treated as False (fails "
             "closed) and logged, rather than breaking the view. Example:\n"
             "self.department_id.manager_id.user_id == user")

    _res_model_button_uniq = models.Constraint(
        "unique (res_model, button_key)",
        "Only one access rule is allowed per button.")

    @api.constrains("visibility_type", "user_ids", "group_id")
    def _check_visibility_fields(self):
        for rec in self:
            if rec.visibility_type == "user" and not rec.user_ids:
                raise ValidationError(_("Select at least one user."))
            if rec.visibility_type == "group" and not rec.group_id:
                raise ValidationError(_("Select a group."))

    @api.onchange("visibility_type")
    def _onchange_visibility_type(self):
        if self.visibility_type != "group":
            self.group_id = False
        if self.visibility_type != "user":
            self.user_ids = [(5, 0, 0)]

    def _eval_condition(self, record):
        """Evaluate this rule's Extra Condition against one record.

        Fails closed: any error (bad field path, wrong types, etc.) is
        logged and treated as False rather than raised - a broken condition
        must not take down the form it is gating.
        """
        self.ensure_one()
        if not self.condition:
            return False
        eval_context = {
            "self": record,
            "record": record,
            "env": record.env,
            "user": record.env.user,
        }
        try:
            return bool(safe_eval(self.condition.strip(), eval_context,
                                  mode="eval"))
        except Exception:
            _logger.exception(
                "asset.button.access condition failed for rule %s (%s/%s)",
                self.id, self.res_model, self.button_key)
            return False


class AssetButtonAccessMixin(models.AbstractModel):
    """Inherit this to gate one of your model's own buttons.

    Usage: add ONE computed boolean field per gated button -

        can_show_action_start = fields.Boolean(compute="_compute_can_show_action_start")

        def _compute_can_show_action_start(self):
            for rec in self:
                rec.can_show_action_start = rec._is_button_visible("action_start")

    then reference that field in the button's invisible="..." in the view.

    If the button used to be restricted some other way before it was wired
    up here (e.g. a static groups="..." in the view), pass that as `default`
    instead of leaving it at True - that way deleting the asset.button.access
    rule later restores the ORIGINAL restriction instead of opening the
    button to everyone:

        can_show_action_approve = fields.Boolean(compute="_compute_can_show_action_approve")

        def _compute_can_show_action_approve(self):
            for rec in self:
                rec.can_show_action_approve = rec._is_button_visible(
                    "action_approve",
                    default=rec.env.user.has_group("asset_management.group_asset_manager"))
    """
    _name = "asset.button.access.mixin"
    _description = "Adds _is_button_visible() for gating buttons via asset.button.access"

    def _is_button_visible(self, button_key, default=True):
        """True unless a rule exists for this button and the current user
        does not match it. No rule -> `default` applies - True (visible to
        everyone) unless the caller passed the button's original
        restriction, in which case deleting the rule falls back to that
        instead of opening the button up.
        """
        self.ensure_one()
        rule = self.env["asset.button.access"].sudo().search([
            ("res_model", "=", self._name),
            ("button_key", "=", button_key),
        ], limit=1)
        if not rule:
            return default
        if rule.visibility_type == "keep":
            visible = default
        elif rule.visibility_type == "all":
            return True
        elif rule.visibility_type == "user":
            visible = self.env.user in rule.user_ids
        else:
            visible = rule.group_id in self.env.user.all_group_ids
        if not visible and rule.condition:
            visible = rule._eval_condition(self)
        return visible

    def _run_button_access_action(self, button_key):
        """Run the configured Python code (if any) for this button.

        Call this from inside the button's own method (e.g. action_start),
        after its state changes have been applied - lets an admin attach
        arbitrary logic (send a mail, post a message, whatever) purely by
        writing Python on asset.button.access, no code change here. A
        silent no-op if no rule, or a rule with no code, exists for this
        button.

        Evaluated the same way ir.actions.server evaluates its own
        "Execute Python Code" field (odoo/addons/base/models/ir_actions.py,
        IrActionsServer._run_action_code_multi/_get_eval_context) - same
        safe_eval sandbox, same variable names, just built directly here
        instead of going through an actual ir.actions.server record.
        """
        for rec in self:
            rule = rec.env["asset.button.access"].sudo().search([
                ("res_model", "=", rec._name),
                ("button_key", "=", button_key),
            ], limit=1)
            if not rule or not rule.code:
                continue
            eval_context = {
                "env": rec.env,
                "model": rec.env[rec._name],
                "record": rec,
                "records": rec,
                "UserError": UserError,
            }
            safe_eval(rule.code.strip(), eval_context, mode="exec",
                     filename=f"asset.button.access({rule.id})")
