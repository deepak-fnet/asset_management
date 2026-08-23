from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class AssetBulkOperations(models.TransientModel):
    """Wizard for bulk operations on assets"""
    _name = "asset.bulk.operations"
    _description = "Asset Bulk Operations Wizard"

    operation_type = fields.Selection([
        ('assign', 'Assign to Employee'),
        ('state_change', 'Change State'),
        ('category', 'Change Category'),
        ('maintenance', 'Schedule Maintenance'),
        ('warranty', 'Update Warranty'),
        ('export', 'Export Assets'),
    ], string="Operation Type", required=True, default='assign')

    # Assignment fields
    employee_id = fields.Many2one('hr.employee', string="Assign to Employee")
    assignment_date = fields.Date(string="Assignment Date", default=fields.Date.today)

    # State change fields
    target_state = fields.Selection([
        ('draft', 'Draft'),
        ('assigned', 'Assigned'),
        ('maintenance', 'Maintenance'),
        ('scrapped', 'Scrapped'),
    ], string="Target State")

    # Category fields
    category_id = fields.Many2one('asset.category', string="Category")

    # Maintenance fields
    maintenance_date = fields.Date(string="Maintenance Date")
    maintenance_notes = fields.Text(string="Maintenance Notes")

    # Warranty fields
    warranty_start = fields.Date(string="Warranty Start")
    warranty_end = fields.Date(string="Warranty End")

    # Export fields
    export_format = fields.Selection([
        ('xlsx', 'Excel (XLSX)'),
        ('csv', 'CSV'),
    ], string="Export Format", default='xlsx')

    # Statistics
    asset_count = fields.Integer(string="Selected Assets", compute='_compute_asset_count')
    asset_ids = fields.Many2many('asset.asset', string="Assets")

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

    def action_execute(self):
        """Execute the selected bulk operation"""
        self.ensure_one()

        if not self.asset_ids:
            raise UserError(_("No assets selected for bulk operation."))

        if self.operation_type == 'assign':
            return self._execute_bulk_assign()
        elif self.operation_type == 'state_change':
            return self._execute_state_change()
        elif self.operation_type == 'category':
            return self._execute_category_change()
        elif self.operation_type == 'maintenance':
            return self._execute_maintenance()
        elif self.operation_type == 'warranty':
            return self._execute_warranty()
        elif self.operation_type == 'export':
            return self._execute_export()

    def _execute_bulk_assign(self):
        """Bulk assign assets to employee"""
        if not self.employee_id:
            raise UserError(_("Please select an employee."))

        success_count = 0
        failed_assets = []

        for asset in self.asset_ids:
            try:
                asset.write({
                    'assigned_employee_id': self.employee_id.id,
                    'assignment_date': self.assignment_date,
                    'state': 'assigned',
                })
                success_count += 1
            except Exception as e:
                failed_assets.append(f"{asset.asset_code}: {str(e)}")
                _logger.error(f"Failed to assign asset {asset.asset_code}: {str(e)}")

        message = f"Successfully assigned {success_count} asset(s) to {self.employee_id.name}."
        if failed_assets:
            message += f"\n\nFailed ({len(failed_assets)}):\n" + "\n".join(failed_assets)

        return self._show_result_message(message, 'success' if not failed_assets else 'warning')

    def _execute_state_change(self):
        """Bulk change asset state"""
        if not self.target_state:
            raise UserError(_("Please select a target state."))

        success_count = 0
        failed_assets = []

        for asset in self.asset_ids:
            try:
                asset.write({'state': self.target_state})
                success_count += 1
            except Exception as e:
                failed_assets.append(f"{asset.asset_code}: {str(e)}")

        message = f"Successfully changed state for {success_count} asset(s)."
        if failed_assets:
            message += f"\n\nFailed ({len(failed_assets)}):\n" + "\n".join(failed_assets)

        return self._show_result_message(message, 'success' if not failed_assets else 'warning')

    def _execute_category_change(self):
        """Bulk change asset category"""
        if not self.category_id:
            raise UserError(_("Please select a category."))

        self.asset_ids.write({'category_id': self.category_id.id})

        return self._show_result_message(
            f"Successfully updated category for {len(self.asset_ids)} asset(s).",
            'success'
        )

    def _execute_maintenance(self):
        """Bulk schedule maintenance"""
        if not self.maintenance_date:
            raise UserError(_("Please select a maintenance date."))

        self.asset_ids.write({
            'next_maintenance_date': self.maintenance_date,
            'state': 'maintenance',
        })

        # Post message on each asset
        for asset in self.asset_ids:
            asset.message_post(
                body=f"Maintenance scheduled for {self.maintenance_date}. Notes: {self.maintenance_notes or 'N/A'}",
                subject="Bulk Maintenance Scheduled"
            )

        return self._show_result_message(
            f"Successfully scheduled maintenance for {len(self.asset_ids)} asset(s).",
            'success'
        )

    def _execute_warranty(self):
        """Bulk update warranty"""
        if not self.warranty_start or not self.warranty_end:
            raise UserError(_("Please provide warranty start and end dates."))

        self.asset_ids.write({
            'warranty_start_date': self.warranty_start,
            'warranty_end_date': self.warranty_end,
        })

        return self._show_result_message(
            f"Successfully updated warranty for {len(self.asset_ids)} asset(s).",
            'success'
        )

    def _execute_export(self):
        """Export selected assets"""
        if self.export_format == 'xlsx':
            return self._export_xlsx()
        else:
            return self._export_csv()

    def _export_xlsx(self):
        """Export assets to Excel"""
        try:
            import xlsxwriter
            from io import BytesIO
            import base64
        except ImportError:
            raise UserError(_("xlsxwriter library not installed. Please install it first."))

        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('Assets')

        # Formats
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#4472C4',
            'font_color': 'white',
            'border': 1
        })
        cell_format = workbook.add_format({'border': 1})

        # Headers
        headers = [
            'Asset Code', 'Asset Name', 'Serial Number', 'Category',
            'State', 'Employee', 'Department', 'Purchase Date',
            'Purchase Value', 'Warranty End', 'Agent Status'
        ]

        for col, header in enumerate(headers):
            worksheet.write(0, col, header, header_format)
            worksheet.set_column(col, col, 15)

        # Data
        row = 1
        for asset in self.asset_ids:
            worksheet.write(row, 0, asset.asset_code or '', cell_format)
            worksheet.write(row, 1, asset.asset_name or '', cell_format)
            worksheet.write(row, 2, asset.serial_number or '', cell_format)
            worksheet.write(row, 3, asset.category_id.name or '', cell_format)
            worksheet.write(row, 4, asset.state or '', cell_format)
            worksheet.write(row, 5, asset.assigned_employee_id.name or '', cell_format)
            worksheet.write(row, 6, asset.department_id.name or '', cell_format)
            worksheet.write(row, 7, str(asset.purchase_date) if asset.purchase_date else '', cell_format)
            worksheet.write(row, 8, asset.purchase_value or 0, cell_format)
            worksheet.write(row, 9, str(asset.warranty_end_date) if asset.warranty_end_date else '', cell_format)
            worksheet.write(row, 10, asset.agent_status or '', cell_format)
            row += 1

        workbook.close()
        output.seek(0)

        # Create attachment
        attachment = self.env['ir.attachment'].create({
            'name': 'assets_export.xlsx',
            'type': 'binary',
            'datas': base64.b64encode(output.read()),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    def _export_csv(self):
        """Export assets to CSV"""
        import csv
        from io import StringIO
        import base64

        output = StringIO()
        writer = csv.writer(output)

        # Headers
        writer.writerow([
            'Asset Code', 'Asset Name', 'Serial Number', 'Category',
            'State', 'Employee', 'Department', 'Purchase Date',
            'Purchase Value', 'Warranty End', 'Agent Status'
        ])

        # Data
        for asset in self.asset_ids:
            writer.writerow([
                asset.asset_code or '',
                asset.asset_name or '',
                asset.serial_number or '',
                asset.category_id.name or '',
                asset.state or '',
                asset.assigned_employee_id.name or '',
                asset.department_id.name or '',
                str(asset.purchase_date) if asset.purchase_date else '',
                asset.purchase_value or 0,
                str(asset.warranty_end_date) if asset.warranty_end_date else '',
                asset.agent_status or '',
            ])

        # Create attachment
        attachment = self.env['ir.attachment'].create({
            'name': 'assets_export.csv',
            'type': 'binary',
            'datas': base64.b64encode(output.getvalue().encode('utf-8')),
            'mimetype': 'text/csv',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    def _show_result_message(self, message, message_type='info'):
        """Show result message to user"""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Bulk Operation Complete'),
                'message': message,
                'type': message_type,
                'sticky': False,
            }
        }