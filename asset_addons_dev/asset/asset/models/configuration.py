from odoo import models, fields


class PhysicalLocation(models.Model):
    _name = "physical.location"
    _order = "id desc"

    name = fields.Char("Name")


class Plant(models.Model):
    _name = "plant.master"
    _order = "id desc"

    name = fields.Char("Name")

class FromLocation(models.Model):
    _name = "from.location"
    _order = "id desc"

    name = fields.Char("Name")

class ToLocation(models.Model):
    _name = "to.location"
    _order = "id desc"

    name = fields.Char("Name")