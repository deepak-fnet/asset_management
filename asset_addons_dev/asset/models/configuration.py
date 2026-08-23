from odoo import _, api, fields, models
from odoo.tools.safe_eval import safe_eval


class PhysicalLocation(models.Model):
    _name = "physical.location"
    _description = "Physical Location"
    _order = "id desc"

    name = fields.Char("Name")


class Plant(models.Model):
    _name = "plant.master"
    _description = "Plant"
    _order = "id desc"

    name = fields.Char("Name")

class FromLocation(models.Model):
    _name = "from.location"
    _description = "From Location"
    _order = "id desc"

    name = fields.Char("Name")

class ToLocation(models.Model):
    _name = "to.location"
    _description = "To Location"
    _order = "id desc"

    name = fields.Char("Name")

class AssetCategory(models.Model):
    """Top level asset classification (was the free-text "Machine Type")."""

    _name = "asset.category"
    _description = "Asset Category"
    _order = "name"

    name = fields.Char("Category", required=True)
    sub_category_ids = fields.One2many('asset.sub.category', 'category_id', "Sub Categories")
    sub_category_count = fields.Integer("Sub Categories", compute='_compute_sub_category_count')
    asset_count = fields.Integer("Assets", compute='_compute_asset_count')
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint(
        'unique (name)',
        'An asset category with this name already exists.',
    )

    def _compute_sub_category_count(self):
        grouped = self.env['asset.sub.category']._read_group(
            [('category_id', 'in', self.ids)], ['category_id'], ['__count'])
        counts = {categ.id: count for categ, count in grouped}
        for categ in self:
            categ.sub_category_count = counts.get(categ.id, 0)

    def _compute_asset_count(self):
        grouped = self.env['asset.addition']._read_group(
            [('category_id', 'in', self.ids)], ['category_id'], ['__count'])
        counts = {categ.id: count for categ, count in grouped}
        for categ in self:
            categ.asset_count = counts.get(categ.id, 0)

    def action_view_assets(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("%s Assets", self.name),
            'res_model': 'asset.addition',
            'view_mode': 'list,form',
            'domain': [('category_id', '=', self.id)],
        }


class AssetSubCategory(models.Model):
    _name = "asset.sub.category"
    _description = "Asset Sub Category"
    _order = "category_id, name"

    name = fields.Char("Sub Category", required=True)
    category_id = fields.Many2one(
        'asset.category', "Category", required=True, ondelete='cascade', index=True)
    active = fields.Boolean(default=True)

    _name_per_category_uniq = models.Constraint(
        'unique (name, category_id)',
        'This sub category already exists for the selected category.',
    )

    @api.depends('name', 'category_id')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.category_id.name} / {rec.name}" if rec.category_id else rec.name


class MachineType(models.Model):
    _name = "machine.type"
    _description = "Machine Type"

    name = fields.Char('name')

class UnifiedAssetKanban(models.Model):
    _name = "unified.asset.kanban"
    _description = "Unified Kanban for All Asset Operations"
    _rec_name = "name"

    name = fields.Char("Name")
    model_name = fields.Char("Model Name")
    total_count = fields.Integer("Total Records")
    state_json = fields.Json("State Counts")  # {"draft": 10, "submit": 3, ...}
    department_json = fields.Json("Department Counts")
    action_id = fields.Many2one("ir.actions.act_window", "Action to Open Records")

    def action_open_records(self):
        self.ensure_one()
        return self.action_id.read()[0]

    @api.model
    def create_unified_records(self):
        models_map = {
            "asset.addition": {
                "name": "Asset Addition",
                "action_xml_id": "asset.action_asset_creation",
            },
            "asset.internal.transfer": {
                "name": "Asset Internal Transfer",
                "action_xml_id": "asset.action_asset_transfer",
            },
            "asset.removal": {
                "name": "Asset Removal",
                "action_xml_id": "asset.action_asset_removal",
            },
            # "physical.verification": {
            #     "name": "Physical Verifications",
            #     "action_xml_id": "asset.action_physical_verification",
            # },
        }

        self.search([]).unlink()

        for model, config in models_map.items():
            model_obj = self.env[model]

            # Total records
            all_records = model_obj.search([])

            # --- STATE COUNTS ---
            state_counts = {}
            if "state" in model_obj._fields:
                state_counts = {
                    state: model_obj.search_count([("state", "=", state)])
                    for state, label in model_obj._fields["state"].selection
                }

            # --- DEPARTMENT COUNTS ---
            department_counts = {}
            if "department_id" in model_obj._fields:
                departments = self.env["hr.department"].search([])
                for dep in departments:
                    department_counts[dep.name] = model_obj.search_count([
                        ("department_id", "=", dep.id)
                    ])

            self.create({
                "name": config["name"],
                "model_name": model,
                "total_count": len(all_records),
                "state_json": state_counts,
                "department_json": department_counts,
                "action_id": self.env.ref(config["action_xml_id"]).id,
            })

    def action_open_state_records(self):
        self.ensure_one()

        state = self.env.context.get("state")
        department = self.env.context.get("department")

        action = self.action_id.read()[0]

        domain = action.get("domain")

        if not domain:
            domain = []
        elif isinstance(domain, str):
            domain = safe_eval(domain)
        else:
            domain = list(domain)

        # Filter by state
        if state:
            domain.append(("state", "=", state))

        # Filter by department
        if department:
            department_id = self.env["hr.department"].search([("name", "=", department)], limit=1).id
            if department_id:
                domain.append(("department_id", "=", department_id))

        action["domain"] = domain

        # popup title
        if state and department:
            action["display_name"] = f"{self.name} - {state} - {department}"
        elif state:
            action["display_name"] = f"{self.name} - {state}"
        elif department:
            action["display_name"] = f"{self.name} - {department}"

        return action
