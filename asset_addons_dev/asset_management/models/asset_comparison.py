from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AssetComparison(models.TransientModel):
    """Compare hardware specifications of multiple assets"""
    _name = "asset.comparison"
    _description = "Asset Comparison Wizard"

    asset_ids = fields.Many2many('asset.asset', string="Assets to Compare")
    asset_count = fields.Integer(compute='_compute_asset_count', string="Asset Count")
    comparison_html = fields.Html(string="Comparison", compute='_compute_comparison_html')

    @api.depends('asset_ids')
    def _compute_asset_count(self):
        for record in self:
            record.asset_count = len(record.asset_ids)

    @api.model
    def default_get(self, fields_list):
        """Get selected assets from context"""
        res = super().default_get(fields_list)

        active_ids = self.env.context.get('active_ids', [])
        if active_ids:
            res['asset_ids'] = [(6, 0, active_ids)]

        return res

    @api.depends('asset_ids')
    def _compute_comparison_html(self):
        """Generate comparison table HTML"""
        for record in self:
            if not record.asset_ids:
                record.comparison_html = '<p class="text-muted">No assets selected for comparison.</p>'
                continue

            if len(record.asset_ids) > 5:
                record.comparison_html = '<p class="text-danger">Maximum 5 assets can be compared at once.</p>'
                continue

            html = self._generate_comparison_table(record.asset_ids)
            record.comparison_html = html

    def _generate_comparison_table(self, assets):
        """Generate HTML comparison table"""

        # Comparison fields
        fields_to_compare = [
            ('asset_code', 'Asset Code'),
            ('asset_name', 'Asset Name'),
            ('serial_number', 'Serial Number'),
            ('category_id', 'Category'),
            ('state', 'State'),
            ('processor', 'Processor'),
            ('ram_size', 'RAM (GB)'),
            ('rom_size', 'Storage (GB)'),
            ('graphics_card', 'Graphics Card'),
            ('os_platform', 'Platform'),
            ('os_name', 'Operating System'),
            ('os_type', 'OS Type'),
            ('disk_type', 'Disk Type'),
            ('battery_capacity', 'Battery Capacity'),
            ('assigned_employee_id', 'Assigned To'),
            ('department_id', 'Department'),
            ('purchase_date', 'Purchase Date'),
            ('purchase_value', 'Purchase Value'),
            ('warranty_end_date', 'Warranty End'),
            ('agent_status', 'Agent Status'),
            ('last_agent_sync', 'Last Sync'),
        ]

        html = '''
        <style>
            .comparison-table {
                width: 100%;
                border-collapse: collapse;
                font-size: 13px;
                margin-top: 20px;
            }
            .comparison-table th {
                background: #667eea;
                color: white;
                padding: 12px 8px;
                text-align: left;
                font-weight: 600;
                border: 1px solid #5568d3;
            }
            .comparison-table td {
                padding: 10px 8px;
                border: 1px solid #e9ecef;
            }
            .comparison-table tr:nth-child(even) {
                background: #f8f9fa;
            }
            .comparison-table tr:hover {
                background: #e9ecef;
            }
            .field-label {
                font-weight: 600;
                color: #495057;
                min-width: 150px;
            }
            .highlight-diff {
                background: #fff3cd;
            }
            .status-badge {
                display: inline-block;
                padding: 3px 10px;
                border-radius: 12px;
                font-size: 11px;
                font-weight: 600;
            }
            .status-assigned { background: #d1e7dd; color: #0f5132; }
            .status-draft { background: #e9ecef; color: #495057; }
            .status-maintenance { background: #fff3cd; color: #664d03; }
            .status-scrapped { background: #f8d7da; color: #842029; }
            .agent-active { color: #198754; font-weight: 600; }
            .agent-offline { color: #ffc107; font-weight: 600; }
            .agent-never { color: #6c757d; font-weight: 600; }
        </style>
        <table class="comparison-table">
            <thead>
                <tr>
                    <th class="field-label">Specification</th>
        '''

        # Add column headers for each asset
        for asset in assets:
            html += f'<th style="text-align: center;">{asset.asset_code}</th>'

        html += '</tr></thead><tbody>'

        # Add comparison rows
        for field_name, field_label in fields_to_compare:
            html += f'<tr><td class="field-label">{field_label}</td>'

            values = []
            for asset in assets:
                field_value = getattr(asset, field_name)

                # Format value based on type
                if hasattr(field_value, 'name'):  # Many2one field
                    display_value = field_value.name or '-'
                elif isinstance(field_value, bool):
                    display_value = 'Yes' if field_value else 'No'
                elif isinstance(field_value, (int, float)):
                    display_value = str(field_value) if field_value else '-'
                elif field_value:
                    display_value = str(field_value)
                else:
                    display_value = '-'

                values.append(display_value)

                # Apply special formatting
                if field_name == 'state':
                    css_class = f'status-{field_value}' if field_value else ''
                    html += f'<td style="text-align: center;"><span class="status-badge {css_class}">{display_value}</span></td>'
                elif field_name == 'agent_status':
                    css_class = f'agent-{field_value}' if field_value else ''
                    html += f'<td style="text-align: center;" class="{css_class}">{display_value}</td>'
                else:
                    # Highlight differences
                    is_different = len(set(values)) > 1 if values else False
                    td_class = 'highlight-diff' if is_different and len(values) == len(assets) else ''
                    html += f'<td style="text-align: center;" class="{td_class}">{display_value}</td>'

            html += '</tr>'

        html += '</tbody></table>'

        # Add summary
        html += '''
        <div style="margin-top: 20px; padding: 15px; background: #f8f9fa; border-radius: 8px;">
            <h5 style="margin-top: 0;">Comparison Summary</h5>
            <ul style="margin-bottom: 0;">
        '''

        # RAM comparison
        ram_values = [asset.ram_size for asset in assets if asset.ram_size]
        if ram_values:
            html += f'<li><strong>RAM Range:</strong> {min(ram_values):.1f} GB - {max(ram_values):.1f} GB</li>'

        # Storage comparison
        rom_values = [asset.rom_size for asset in assets if asset.rom_size]
        if rom_values:
            html += f'<li><strong>Storage Range:</strong> {min(rom_values):.1f} GB - {max(rom_values):.1f} GB</li>'

        # Price comparison
        price_values = [asset.purchase_value for asset in assets if asset.purchase_value]
        if price_values:
            html += f'<li><strong>Price Range:</strong> ${min(price_values):,.2f} - ${max(price_values):,.2f}</li>'

        # Unique processors
        processors = list(set([asset.processor for asset in assets if asset.processor]))
        if processors:
            html += f'<li><strong>Processors:</strong> {len(processors)} different type(s)</li>'

        # Agent status summary
        active = sum(1 for asset in assets if asset.agent_status == 'active')
        offline = sum(1 for asset in assets if asset.agent_status == 'offline')
        never = sum(1 for asset in assets if asset.agent_status == 'never')
        html += f'<li><strong>Agent Status:</strong> {active} Active, {offline} Offline, {never} Never Synced</li>'

        html += '''
            </ul>
        </div>
        '''

        return html

    def action_export_comparison(self):
        """Export comparison as PDF or Excel"""
        self.ensure_one()

        if not self.asset_ids:
            raise UserError(_("No assets selected for comparison."))

        # For now, return a simple notification
        # In production, you would generate a PDF report here
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Export'),
                'message': _('Comparison export feature will be available in the next update.'),
                'type': 'info',
            }
        }