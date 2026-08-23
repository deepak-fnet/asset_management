from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime


class AssetInstalledApplication(models.Model):
    _name = 'asset.installed.application'
    _description = 'Installed Applications on Asset'
    _rec_name = 'name'

    asset_id = fields.Many2one('asset.asset', string='Asset', required=True, ondelete='cascade')
    name = fields.Char(string='Application Name', required=True)
    publisher = fields.Char(string='Publisher')
    version = fields.Char(string='Version')
    installed_date = fields.Char(string='Installed Date')
    size = fields.Float(string='Size (KB)')

    # Uninstall tracking fields
    uninstall_status = fields.Selection([
        ('pending', 'Pending'),
        ('uninstalling', 'Uninstalling'),
        ('uninstalled', 'Uninstalled'),
        ('failed', 'Failed')
    ], string='Uninstall Status', default='pending')
    
    uninstall_date = fields.Datetime(string='Uninstall Date', copy=False)
    uninstall_error_message = fields.Text(string='Uninstall Error', copy=False)

    installed_date_formatted = fields.Char(
        string='Installed On',
        compute='_compute_installed_date_formatted'
    )

    # Computed field to show asset platform for button visibility
    asset_platform = fields.Selection(
        related='asset_id.platform',
        string='Asset Platform',
        store=False
    )

    @api.depends('installed_date')
    def _compute_installed_date_formatted(self):
        for record in self:
            if not record.installed_date:
                record.installed_date_formatted = '—'
                continue

            # If already formatted as DD/MM/YYYY (length 10)
            if len(record.installed_date) == 10 and '/' in record.installed_date:
                record.installed_date_formatted = record.installed_date
            # If in YYYYMMDD format (length 8)
            elif len(record.installed_date) == 8 and record.installed_date.isdigit():
                try:
                    date_obj = datetime.strptime(record.installed_date, '%Y%m%d')
                    record.installed_date_formatted = date_obj.strftime('%d/%m/%Y')
                except (ValueError, TypeError):
                    record.installed_date_formatted = record.installed_date
            else:
                record.installed_date_formatted = record.installed_date

    def action_uninstall_app(self):
        """
        Initiate uninstall process for selected application(s).
        Sets uninstall_status to 'uninstalling' which triggers agent to process.
        """
        # Filter only Windows assets
        windows_apps = self.filtered(lambda r: r.asset_id.platform == 'windows')
        non_windows_apps = self - windows_apps
        
        if non_windows_apps:
            raise UserError(_(
                "Uninstall is only supported for Windows assets. "
                "Selected non-Windows assets: %s"
            ) % ', '.join(non_windows_apps.mapped('asset_id.name')))
        
        if not windows_apps:
            raise UserError(_("No Windows applications selected for uninstall."))
        
        # Check if any app is already being uninstalled or uninstalled
        already_processed = windows_apps.filtered(
            lambda r: r.uninstall_status in ('uninstalling', 'uninstalled')
        )
        if already_processed:
            raise UserError(_(
                "The following applications are already being processed: %s"
            ) % ', '.join(already_processed.mapped('name')))
        
        # Force save any pending changes before updating status
        for app in windows_apps:
            # Ensure the record is properly saved
            app.ensure_one()
            app.write({
                'uninstall_status': 'uninstalling',
                'uninstall_error_message': False
            })
            # Flush to ensure data is written to database
            app.invalidate_recordset()
        
        # Create audit log entry
        self._create_uninstall_audit_log(windows_apps)
        
        # Return action to refresh the view
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def _create_uninstall_audit_log(self, apps):
        """Create audit log entries for uninstallation requests."""
        AuditLog = self.env['asset.audit.log'].sudo()
        for app in apps:
            AuditLog.create({
                'asset_id': app.asset_id.id,
                'action': 'uninstall_request',
                'description': f"Uninstall requested for application: {app.name} (v{app.version}) by {app.publisher}",
                'user_id': self.env.uid,
            })

    def action_reset_uninstall_status(self):
        """Reset uninstall status to pending (for retry after failure)."""
        for record in self:
            if record.uninstall_status == 'failed':
                record.write({
                    'uninstall_status': 'pending',
                    'uninstall_error_message': False,
                    'uninstall_date': False
                })
        return True

    def open_uninstall_wizard(self):
        """Open the uninstall confirmation wizard."""
        self.ensure_one()
        
        # Check if Windows asset
        if self.asset_id.platform != 'windows':
            raise UserError(_("Uninstall is only supported for Windows assets."))
        
        # Force save any pending changes in the current record
        # This is needed for inline editable list views
        self._cr.commit()  # Commit any pending transaction
        
        return {
            'type': 'ir.actions.act_window',
            'name': _('Confirm Application Uninstall'),
            'res_model': 'asset.app.uninstall.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_asset_id': self.asset_id.id,
                'default_app_id': self.id,
            }
        }


class AssetAppUninstallWizard(models.TransientModel):
    """Wizard for confirming application uninstall."""
    _name = 'asset.app.uninstall.wizard'
    _description = 'Application Uninstall Confirmation Wizard'

    asset_id = fields.Many2one('asset.asset', string='Asset', required=True, ondelete='cascade')
    asset_name = fields.Char(string='Asset Name', compute='_compute_asset_data', readonly=True, store=False)
    app_id = fields.Many2one('asset.installed.application', string='Application', required=True, ondelete='cascade')
    app_name = fields.Char(string='Application Name', compute='_compute_app_data', readonly=True, store=False)
    app_publisher = fields.Char(string='Publisher', compute='_compute_app_data', readonly=True, store=False)
    app_version = fields.Char(string='Version', compute='_compute_app_data', readonly=True, store=False)
    confirm_checkbox = fields.Boolean(string='I understand this will uninstall the application')

    @api.depends('asset_id')
    def _compute_asset_data(self):
        for record in self:
            if record.asset_id:
                record.asset_name = record.asset_id.name or ''
            else:
                record.asset_name = ''

    @api.depends('app_id')
    def _compute_app_data(self):
        for record in self:
            if record.app_id:
                record.app_name = record.app_id.name or ''
                record.app_publisher = record.app_id.publisher or ''
                record.app_version = record.app_id.version or ''
            else:
                record.app_name = ''
                record.app_publisher = ''
                record.app_version = ''

    def action_confirm_uninstall(self):
        """Confirm and execute the uninstall."""
        self.ensure_one()
        
        if not self.confirm_checkbox:
            raise UserError(_("Please confirm that you understand this will uninstall the application."))
        
        # Check if Windows asset
        if self.asset_id.platform != 'windows':
            raise UserError(_("Uninstall is only supported for Windows assets."))
        
        # Execute uninstall
        self.app_id.action_uninstall_app()
        
        return {'type': 'ir.actions.act_window_close'}
