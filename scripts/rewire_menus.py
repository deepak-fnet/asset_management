"""Re-apply the Asset Management menu layout (run after upgrading asset modules).

    python3.12 odoo-bin shell -c config/<conf> --no-http < scripts/rewire_menus.py
"""
env['ir.module.module']._workspace_rewire_menus()  # noqa: F821 (env comes from odoo shell)
env.cr.commit()  # noqa: F821
print('* menus rewired')
