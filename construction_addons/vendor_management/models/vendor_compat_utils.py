# -*- coding: utf-8 -*-
"""
Odoo 19 migration compatibility helpers.

Odoo 19 renamed a few core fields that this module relies on:
  - res.groups.users            -> res.groups.user_ids
  - res.partner.title           -> model res.partner.title was REMOVED
  - res.partner.mobile          -> field REMOVED (merged into phone usage)

These helpers keep the module working on Odoo 19 while staying defensive
in case a future core patch restores/renames these again, or in case this
module is ever reused on an older server during a staged migration.
"""


def group_members(group):
    """Return the res.users recordset for a res.groups record.

    Odoo 19 renamed res.groups.users to res.groups.user_ids.
    """
    if not group:
        return group
    if 'user_ids' in group._fields:
        return group.user_ids
    return group.users


def group_member_emails(group):
    """Return a comma-joined string of member emails for a group."""
    members = group_members(group)
    return ",".join(filter(None, members.mapped('email'))) if members else ""


def has_field(record, field_name):
    """Safe check whether a model currently has a given field.

    Used to guard optional/removed fields (e.g. 'title', 'mobile',
    'l10n_in_pan' when the l10n_in module is not installed).
    """
    return bool(record) and field_name in record._fields
