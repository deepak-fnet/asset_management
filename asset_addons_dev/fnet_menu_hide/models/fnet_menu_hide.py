# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError


class FnetMenuHide(models.Model):
    _name = "fnet.menu.hide"
    _description = "Hidden Odoo Menu Rule"
    _order = "menu_id"
    _rec_name = "menu_id"

    menu_id = fields.Many2one(
        "ir.ui.menu",
        string="Menu to Hide",
        required=True,
        ondelete="cascade",
        help="Pick any Odoo menu. Use the Entire Menu flag to also hide "
             "everything nested under it.",
    )
    menu_complete_name = fields.Char(
        string="Full Path",
        compute="_compute_menu_complete_name",
        store=True,
    )
    entire_menu = fields.Boolean(
        string="Entire Menu",
        default=True,
        help="If ticked, hides this menu AND all of its sub menus at every "
             "level. If unticked, only this single menu item is hidden.",
    )
    state = fields.Selection(
        [("draft", "Not Applied"), ("applied", "Hidden")],
        default="draft",
        readonly=True,
        string="Status",
    )
    hidden_menu_ids = fields.Many2many(
        "ir.ui.menu",
        "fnet_menu_hide_hidden_rel",
        "rule_id",
        "menu_id",
        string="Actually Hidden",
        readonly=True,
        help="The menus this rule archived. Restore only re-enables these, so "
             "menus hidden for other reasons are never touched.",
    )
    hidden_count = fields.Integer(
        string="Hidden Menus",
        compute="_compute_hidden_count",
    )
    note = fields.Char(string="Reason / Note")

    _sql_constraints = [
        ("menu_uniq", "unique(menu_id)",
         "There is already a hide rule for this menu."),
    ]

    # ------------------------------------------------------------------
    @api.depends("menu_id")
    def _compute_menu_complete_name(self):
        for rec in self:
            menu = rec.menu_id.with_context(active_test=False)
            rec.menu_complete_name = menu.complete_name or menu.name or ""

    @api.depends("hidden_menu_ids")
    def _compute_hidden_count(self):
        # active_test=False is REQUIRED: reading a m2m to a model that has an
        # `active` field silently drops archived records, and every menu we
        # track here is archived by definition.
        for rec in self.with_context(active_test=False):
            rec.hidden_count = len(rec.hidden_menu_ids)

    # ------------------------------------------------------------------
    def _clear_menu_cache(self):
        """Invalidate the menu cache in a version-safe way.

        ir.ui.menu.write() already does this in recent Odoo, but calling it
        explicitly keeps behaviour predictable across versions.
        """
        try:
            self.env.registry.clear_cache()
        except AttributeError:
            # very old versions
            self.env["ir.ui.menu"].clear_caches()

    def _target_menus(self):
        """Menus this rule should hide: itself, plus descendants if requested."""
        self.ensure_one()
        Menu = self.env["ir.ui.menu"].with_context(active_test=False)
        if self.entire_menu:
            # child_of walks the whole subtree at any depth
            return Menu.search([("id", "child_of", self.menu_id.id)])
        return self.menu_id.with_context(active_test=False)

    def action_apply(self):
        """Archive the target menus and remember what was archived."""
        # see note in _compute_hidden_count about active_test
        for rec in self.with_context(active_test=False):
            if not rec.menu_id:
                continue
            targets = rec._target_menus()
            # only touch menus that are currently visible
            to_hide = targets.filtered(lambda m: m.active)
            if to_hide:
                to_hide.write({"active": False})
            rec.hidden_menu_ids = [(6, 0, (rec.hidden_menu_ids | to_hide).ids)]
            rec.state = "applied"
        self._clear_menu_cache()
        return True

    def action_restore(self):
        """Un-archive exactly the menus this rule hid."""
        for rec in self.with_context(active_test=False):
            to_restore = rec.hidden_menu_ids
            if not to_restore:
                # Fallback: nothing tracked (e.g. a rule saved by an older
                # buggy version, or tracking lost). Re-enable the rule's
                # target subtree so the menus are never left stranded.
                to_restore = rec._target_menus()
            if to_restore:
                to_restore.write({"active": True})
            rec.hidden_menu_ids = [(5, 0, 0)]
            rec.state = "draft"
        self._clear_menu_cache()
        return True

    # --- keep things consistent ----------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.action_apply()
        return records

    def write(self, vals):
        # If the target or the scope changes, restore first then re-apply.
        touch = {"menu_id", "entire_menu"} & set(vals)
        if touch:
            self.with_context(active_test=False).action_restore()
        res = super().write(vals)
        if touch:
            self.with_context(active_test=False).action_apply()
        return res

    def unlink(self):
        # Deleting a rule should give the menus back.
        self.with_context(active_test=False).action_restore()
        return super().unlink()

    # --- upgrade safety -------------------------------------------------
    @api.model
    def reapply_all(self):
        """Re-hide everything after a module upgrade.

        Upgrading the app that owns a menu can reset its `active` flag back to
        True, so this is called from a data <function> on every upgrade.
        """
        rules = self.search([])
        if rules:
            rules.with_context(active_test=False).action_apply()
        return True

    # --- shortcut used from the ir.ui.menu list -------------------------
    @api.model
    def hide_menus(self, menu_ids, entire_menu=True):
        """Create (or update) hide rules for the given ir.ui.menu ids."""
        created = self.browse()
        for menu in self.env["ir.ui.menu"].with_context(
            active_test=False
        ).browse(menu_ids):
            rule = self.search([("menu_id", "=", menu.id)], limit=1)
            if rule:
                rule.entire_menu = entire_menu
            else:
                rule = self.create({
                    "menu_id": menu.id,
                    "entire_menu": entire_menu,
                })
            created |= rule
        return created.ids

    def action_open_hidden_menus(self):
        """Smart button: see exactly which menus got hidden."""
        self.ensure_one()
        rec = self.with_context(active_test=False)
        return {
            "type": "ir.actions.act_window",
            "name": "Hidden Menus",
            "res_model": "ir.ui.menu",
            "view_mode": "list,form",
            "domain": [("id", "in", rec.hidden_menu_ids.ids)],
            "context": {"active_test": False},
        }
