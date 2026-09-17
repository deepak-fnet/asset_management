# -*- coding: utf-8 -*-
from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = 'project.task'

    cm_stage_id = fields.Many2one(
        'cm.stage', string='Construction Stage', ondelete='cascade', index=True,
        help="Set on a top-level task to represent a room/area of physical work under this "
             "construction stage. Leave empty on sub-tasks - they are already linked through "
             "their parent task.")
