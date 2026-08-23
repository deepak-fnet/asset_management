# -*- coding: utf-8 -*-
"""Age-bucket dashboard.

Lives in its own file so that its SQL view is created *after* the tables it
joins: Odoo runs ``init()`` in model-registration order, i.e. the order the
model files are imported in ``models/__init__.py``.
"""

from odoo import _, fields, models


class LaptopAgeDashboard(models.Model):
    _name = "laptop.age.dashboard"
    _description = "Laptop Age Dashboard"
    _auto = False

    name = fields.Char()
    count = fields.Integer()
    color_code = fields.Char()
    machine_breakdown = fields.Text()  # "<category>: <count>" lines

    # Age buckets, in months, keyed by the synthetic row id of the SQL view.
    AGE_BUCKETS = {
        1: (None, 36),
        2: (36, 60),
        3: (60, 120),
        4: (120, 180),
        5: (180, None),
    }

    def init(self):
        self.env.cr.execute("DROP VIEW IF EXISTS laptop_age_dashboard")
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW laptop_age_dashboard AS (
                SELECT
                    1 AS id,
                    '0 - 3 Years' AS name,
                    COUNT(*) AS count,
                    '#4CAF50' AS color_code,
                    (
                        SELECT string_agg(cat_name || ': ' || cnt, E'\n' ORDER BY cat_name)
                        FROM (
                            SELECT COALESCE(ac.name, 'Undefined') AS cat_name, COUNT(*) AS cnt
                            FROM asset_addition aa
                            LEFT JOIN asset_category ac ON ac.id = aa.category_id
                            WHERE aa.laptop_age_total_months <= 36
                              AND aa.state != 'removed'
                            GROUP BY COALESCE(ac.name, 'Undefined')
                        ) AS breakdown
                    ) AS machine_breakdown
                FROM asset_addition aa
                WHERE aa.laptop_age_total_months <= 36
                  AND aa.state != 'removed'

                UNION ALL

                SELECT
                    2 AS id,
                    '3 - 5 Years' AS name,
                    COUNT(*) AS count,
                    '#2196F3' AS color_code,
                    (
                        SELECT string_agg(cat_name || ': ' || cnt, E'\n' ORDER BY cat_name)
                        FROM (
                            SELECT COALESCE(ac.name, 'Undefined') AS cat_name, COUNT(*) AS cnt
                            FROM asset_addition aa
                            LEFT JOIN asset_category ac ON ac.id = aa.category_id
                            WHERE aa.laptop_age_total_months > 36 AND aa.laptop_age_total_months <= 60
                              AND aa.state != 'removed'
                            GROUP BY COALESCE(ac.name, 'Undefined')
                        ) AS breakdown
                    ) AS machine_breakdown
                FROM asset_addition aa
                WHERE aa.laptop_age_total_months > 36 AND aa.laptop_age_total_months <= 60
                  AND aa.state != 'removed'

                UNION ALL

                SELECT
                    3 AS id,
                    '5 - 10 Years' AS name,
                    COUNT(*) AS count,
                    '#FF9800' AS color_code,
                    (
                        SELECT string_agg(cat_name || ': ' || cnt, E'\n' ORDER BY cat_name)
                        FROM (
                            SELECT COALESCE(ac.name, 'Undefined') AS cat_name, COUNT(*) AS cnt
                            FROM asset_addition aa
                            LEFT JOIN asset_category ac ON ac.id = aa.category_id
                            WHERE aa.laptop_age_total_months > 60 AND aa.laptop_age_total_months <= 120
                              AND aa.state != 'removed'
                            GROUP BY COALESCE(ac.name, 'Undefined')
                        ) AS breakdown
                    ) AS machine_breakdown
                FROM asset_addition aa
                WHERE aa.laptop_age_total_months > 60 AND aa.laptop_age_total_months <= 120
                  AND aa.state != 'removed'

                UNION ALL

                SELECT
                    4 AS id,
                    '10 - 15 Years' AS name,
                    COUNT(*) AS count,
                    '#E53935' AS color_code,
                    (
                        SELECT string_agg(cat_name || ': ' || cnt, E'\n' ORDER BY cat_name)
                        FROM (
                            SELECT COALESCE(ac.name, 'Undefined') AS cat_name, COUNT(*) AS cnt
                            FROM asset_addition aa
                            LEFT JOIN asset_category ac ON ac.id = aa.category_id
                            WHERE aa.laptop_age_total_months > 120 AND aa.laptop_age_total_months <= 180
                              AND aa.state != 'removed'
                            GROUP BY COALESCE(ac.name, 'Undefined')
                        ) AS breakdown
                    ) AS machine_breakdown
                FROM asset_addition aa
                WHERE aa.laptop_age_total_months > 120 AND aa.laptop_age_total_months <= 180
                  AND aa.state != 'removed'

                UNION ALL

                SELECT
                    5 AS id,
                    '15+ Years' AS name,
                    COUNT(*) AS count,
                    '#424242' AS color_code,
                    (
                        SELECT string_agg(cat_name || ': ' || cnt, E'\n' ORDER BY cat_name)
                        FROM (
                            SELECT COALESCE(ac.name, 'Undefined') AS cat_name, COUNT(*) AS cnt
                            FROM asset_addition aa
                            LEFT JOIN asset_category ac ON ac.id = aa.category_id
                            WHERE aa.laptop_age_total_months > 180
                              AND aa.state != 'removed'
                            GROUP BY COALESCE(ac.name, 'Undefined')
                        ) AS breakdown
                    ) AS machine_breakdown
                FROM asset_addition aa
                WHERE aa.laptop_age_total_months > 180
                  AND aa.state != 'removed'
            );
        """)

    def _age_domain(self):
        """Domain restricting assets to this card's age bucket."""
        self.ensure_one()
        low, high = self.AGE_BUCKETS.get(self.id, (None, None))
        domain = [('state', '!=', 'removed')]
        if low is not None:
            domain.append(('laptop_age_total_months', '>', low))
        if high is not None:
            domain.append(('laptop_age_total_months', '<=', high))
        return domain

    def action_open_records(self):
        """Redirect to asset records for this age range."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("%s Assets", self.name),
            'view_mode': 'list,form',
            'res_model': 'asset.addition',
            'domain': self._age_domain(),
        }

    def action_open_machine_type(self):
        """Drill down into one category within this age range."""
        self.ensure_one()
        category_name = self.env.context.get('machine_type')
        if not category_name:
            return self.action_open_records()

        domain = self._age_domain()
        if category_name == 'Undefined':
            domain.append(('category_id', '=', False))
        else:
            domain.append(('category_id.name', '=', category_name))

        return {
            'type': 'ir.actions.act_window',
            'name': _("%(category)s (%(age)s) Assets", category=category_name, age=self.name),
            'view_mode': 'list,form',
            'res_model': 'asset.addition',
            'domain': domain,
        }
