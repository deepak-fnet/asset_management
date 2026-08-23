# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError
import base64
import csv
from io import StringIO
from datetime import datetime
import logging

_logger = logging.getLogger(__name__)


class AssetRfidComparison(models.Model):
    _name = "asset.rfid.comparison"
    _description = "RFID CSV Asset Comparison"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _rec_name = "name"

    name = fields.Char(
        string="Reference",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _("New")
    )
    upload_date = fields.Datetime(
        string="Upload Date",
        default=fields.Datetime.now,
        readonly=True
    )
    csv_file = fields.Binary(
        string="CSV File",
        required=True,
        attachment=True
    )
    csv_filename = fields.Char(string="CSV Filename")
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Compared')
    ], default='draft', string="Status", tracking=True)
    user_id = fields.Many2one(
        'res.users',
        string="Uploaded By",
        default=lambda self: self.env.user,
        readonly=True
    )
    verification_id = fields.Many2one(
        'physical.verification',
        string="Physical Verification",
        domain="[('state', '=', 'draft')]"
    )
    notes = fields.Text(string="Notes")

    # Statistics
    total_csv_count = fields.Integer(
        string="Total in CSV",
        readonly=True,
        compute="_compute_statistics",
        store=True
    )
    total_erp_count = fields.Integer(
        string="Total in ERP",
        readonly=True,
        compute="_compute_statistics",
        store=True
    )
    matched_count = fields.Integer(
        string="Matched",
        readonly=True,
        compute="_compute_statistics",
        store=True
    )
    missing_in_erp_count = fields.Integer(
        string="Missing in ERP",
        readonly=True,
        compute="_compute_statistics",
        store=True
    )
    missing_in_csv_count = fields.Integer(
        string="Missing in CSV",
        readonly=True,
        compute="_compute_statistics",
        store=True
    )

    # Comparison lines
    comparison_line_ids = fields.One2many(
        'asset.rfid.comparison.line',
        'comparison_id',
        string="Comparison Results"
    )

    # Filtered lines for easy access
    matched_line_ids = fields.One2many(
        'asset.rfid.comparison.line',
        'comparison_id',
        string="Matched Assets",
        domain=[('status', '=', 'matched')]
    )
    missing_erp_line_ids = fields.One2many(
        'asset.rfid.comparison.line',
        'comparison_id',
        string="Missing in ERP",
        domain=[('status', '=', 'missing_in_erp')]
    )
    missing_csv_line_ids = fields.One2many(
        'asset.rfid.comparison.line',
        'comparison_id',
        string="Missing in CSV",
        domain=[('status', '=', 'missing_in_csv')]
    )

    @api.depends('comparison_line_ids', 'comparison_line_ids.status')
    def _compute_statistics(self):
        for record in self:
            lines = record.comparison_line_ids
            record.matched_count = len(lines.filtered(lambda l: l.status == 'matched'))
            record.missing_in_erp_count = len(lines.filtered(lambda l: l.status == 'missing_in_erp'))
            record.missing_in_csv_count = len(lines.filtered(lambda l: l.status == 'missing_in_csv'))
            record.total_csv_count = record.matched_count + record.missing_in_erp_count
            record.total_erp_count = record.matched_count + record.missing_in_csv_count

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('asset.rfid.comparison') or _('New')
        return super().create(vals_list)

    def action_compare(self):
        """Parse CSV and compare with ERP assets by tag_number"""
        for record in self:
            if not record.csv_file:
                raise UserError(_("Please upload a CSV file first."))

            # Clear existing lines
            record.comparison_line_ids.unlink()

            try:
                # Decode the CSV file
                csv_content = base64.b64decode(record.csv_file).decode('utf-8')
                lines = csv_content.strip().split('\n')
                
                # Parse the RFID scanner format:
                # Row 1: INVENTORY SUMMARY
                # Row 2: UNIQUE COUNT: X
                # Row 3: TOTAL COUNT: X
                # Row 4: READ TIME: X
                # Row 5: Empty
                # Row 6: Headers (none, TAG, COUNT, RSSI)
                # Row 7+: Data
                
                # Extract summary info from header rows
                scan_summary = []
                for i, line in enumerate(lines[:5]):
                    if line.strip():
                        scan_summary.append(line.strip())
                
                # Find the header row (contains TAG column)
                header_row_idx = None
                for i, line in enumerate(lines):
                    if 'TAG' in line.upper():
                        header_row_idx = i
                        break
                
                if header_row_idx is None:
                    # Fallback: try standard CSV format with tag_number
                    reader = csv.DictReader(StringIO(csv_content))
                    if 'tag_number' in (reader.fieldnames or []):
                        csv_tags = {}
                        for row in reader:
                            tag = row.get('tag_number', '').strip()
                            if tag:
                                csv_tags[tag] = row
                    else:
                        raise UserError(_(
                            "CSV file format not recognized.\n"
                            "Expected RFID scanner format with TAG column or standard format with tag_number column.\n"
                            "Found: %s"
                        ) % lines[0] if lines else "Empty file")
                else:
                    # Parse the RFID scanner format
                    # Get data rows starting after header
                    data_content = '\n'.join(lines[header_row_idx:])
                    reader = csv.DictReader(StringIO(data_content))
                    
                    # Find the TAG column (might be labeled differently)
                    tag_column = None
                    for col in (reader.fieldnames or []):
                        if col and 'TAG' in col.upper():
                            tag_column = col
                            break
                    
                    if not tag_column:
                        raise UserError(_(
                            "Could not find TAG column in CSV.\n"
                            "Found columns: %s"
                        ) % ', '.join(reader.fieldnames or []))
                    
                    csv_tags = {}
                    for row in reader:
                        tag = row.get(tag_column, '').strip()
                        if tag:
                            csv_tags[tag] = {
                                'tag': tag,
                                'count': row.get('COUNT', ''),
                                'rssi': row.get('RSSI', ''),
                                'raw': str(row)
                            }
                
                # Get all ERP assets with tag numbers, optionally scoped by verification
                domain = [
                    ('tag_number', '!=', False),
                    ('tag_number', '!=', ''),
                    ('state', '!=', 'removed'),
                ]
                
                if record.verification_id:
                    # Scope the ERP assets to the verification content
                    if record.verification_id.plant_id:
                        domain.append(('plant_id', '=', record.verification_id.plant_id.id))
                    if record.verification_id.department_id:
                        domain.append(('department_id', '=', record.verification_id.department_id.id))
                    if record.verification_id.physical_location_id:
                        domain.append(('physical_location_id', '=', record.verification_id.physical_location_id.id))
                
                erp_assets = self.env['asset.addition'].search(domain)
                erp_tags = {a.tag_number.strip(): a for a in erp_assets if a.tag_number}

                # Compare sets
                csv_set = set(csv_tags.keys())
                erp_set = set(erp_tags.keys())

                matched = csv_set & erp_set
                missing_in_erp = csv_set - erp_set
                missing_in_csv = erp_set - csv_set

                lines_to_create = []

                # Create lines for matched assets
                for tag in matched:
                    asset = erp_tags[tag]
                    csv_row = csv_tags[tag]
                    lines_to_create.append({
                        'comparison_id': record.id,
                        'tag_number': tag,
                        'asset_id': asset.id,
                        'asset_name': asset.asset_name,
                        'department_id': asset.department_id.id if asset.department_id else False,
                        'plant_id': asset.plant_id.id if asset.plant_id else False,
                        'physical_location_id': asset.physical_location_id.id if asset.physical_location_id else False,
                        'csv_data': str(csv_row),
                        'scan_count': csv_row.get('count', '') if isinstance(csv_row, dict) else '',
                        'rssi': csv_row.get('rssi', '') if isinstance(csv_row, dict) else '',
                        'status': 'matched',
                    })

                # Create lines for assets missing in ERP (scoped location)
                # But check if tag exists in OTHER departments/locations
                all_erp_assets = self.env['asset.addition'].search([
                    ('tag_number', '!=', False),
                    ('tag_number', '!=', ''),
                    ('state', '!=', 'removed'),
                ])
                all_erp_tags = {a.tag_number.strip(): a for a in all_erp_assets if a.tag_number}
                
                for tag in missing_in_erp:
                    csv_row = csv_tags[tag]
                    line_data = {
                        'comparison_id': record.id,
                        'tag_number': tag,
                        'csv_data': str(csv_row),
                        'scan_count': csv_row.get('count', '') if isinstance(csv_row, dict) else '',
                        'rssi': csv_row.get('rssi', '') if isinstance(csv_row, dict) else '',
                        'status': 'missing_in_erp',
                    }
                    
                    # Check if this tag exists in another department/location
                    if tag in all_erp_tags:
                        other_asset = all_erp_tags[tag]
                        line_data.update({
                            'asset_id': other_asset.id,
                            'asset_name': other_asset.asset_name,
                            'department_id': other_asset.department_id.id if other_asset.department_id else False,
                            'plant_id': other_asset.plant_id.id if other_asset.plant_id else False,
                            'physical_location_id': other_asset.physical_location_id.id if other_asset.physical_location_id else False,
                        })
                    
                    lines_to_create.append(line_data)

                # Create lines for assets missing in CSV
                for tag in missing_in_csv:
                    asset = erp_tags[tag]
                    lines_to_create.append({
                        'comparison_id': record.id,
                        'tag_number': tag,
                        'asset_id': asset.id,
                        'asset_name': asset.asset_name,
                        'department_id': asset.department_id.id if asset.department_id else False,
                        'plant_id': asset.plant_id.id if asset.plant_id else False,
                        'physical_location_id': asset.physical_location_id.id if asset.physical_location_id else False,
                        'status': 'missing_in_csv',
                    })

                # Bulk create all lines
                if lines_to_create:
                    self.env['asset.rfid.comparison.line'].create(lines_to_create)

                # Update notes with scan summary
                if scan_summary and not record.notes:
                    record.notes = "Scan Summary:\n" + "\n".join(scan_summary)

                record.state = 'done'

                _logger.info(
                    "RFID Comparison %s completed: %d matched, %d missing in ERP, %d missing in CSV",
                    record.name, len(matched), len(missing_in_erp), len(missing_in_csv)
                )

            except UnicodeDecodeError:
                raise UserError(_("Could not decode CSV file. Please ensure it is a valid UTF-8 encoded CSV."))
            except csv.Error as e:
                raise UserError(_("Error parsing CSV file: %s") % str(e))

        return True

    def action_reset_draft(self):
        """Reset comparison to draft state"""
        for record in self:
            record.comparison_line_ids.unlink()
            record.state = 'draft'
        return True

    def action_view_matched(self):
        """Open list of matched assets"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Matched Assets'),
            'res_model': 'asset.rfid.comparison.line',
            'view_mode': 'list,form',
            'domain': [('comparison_id', '=', self.id), ('status', '=', 'matched')],
            'context': {'default_comparison_id': self.id},
        }

    def action_view_missing_erp(self):
        """Open list of assets missing in ERP"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Missing in ERP'),
            'res_model': 'asset.rfid.comparison.line',
            'view_mode': 'list,form',
            'domain': [('comparison_id', '=', self.id), ('status', '=', 'missing_in_erp')],
            'context': {'default_comparison_id': self.id},
        }

    def action_view_missing_csv(self):
        """Open list of assets missing in CSV"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Missing in CSV'),
            'res_model': 'asset.rfid.comparison.line',
            'view_mode': 'list,form',
            'domain': [('comparison_id', '=', self.id), ('status', '=', 'missing_in_csv')],
            'context': {'default_comparison_id': self.id},
        }


class AssetRfidComparisonLine(models.Model):
    _name = "asset.rfid.comparison.line"
    _description = "RFID Comparison Result Line"
    _order = "status, tag_number"

    comparison_id = fields.Many2one(
        'asset.rfid.comparison',
        string="Comparison",
        required=True,
        ondelete='cascade'
    )
    tag_number = fields.Char(string="Tag Number", required=True)
    asset_id = fields.Many2one('asset.addition', string="Asset")
    asset_name = fields.Char(string="Asset Name")
    department_id = fields.Many2one('hr.department', string="Department")
    plant_id = fields.Many2one('plant.master', string="Plant")
    physical_location_id = fields.Many2one('physical.location', string="Physical Location")
    csv_data = fields.Text(string="CSV Row Data")
    scan_count = fields.Char(string="Scan Count")
    rssi = fields.Char(string="RSSI")
    status = fields.Selection([
        ('matched', 'Matched'),
        ('missing_in_erp', 'Missing in ERP'),
        ('missing_in_csv', 'Missing in CSV'),
    ], string="Status", required=True)

    # Computed field for display
    status_display = fields.Char(
        string="Status Display",
        compute="_compute_status_display"
    )

    @api.depends('status')
    def _compute_status_display(self):
        status_map = {
            'matched': '✅ Matched',
            'missing_in_erp': '⚠️ Missing in ERP',
            'missing_in_csv': '❌ Missing in CSV',
        }
        for record in self:
            record.status_display = status_map.get(record.status, record.status)

    def action_rectified(self):
        pass
    def action_rejected(self):
        pass

    def action_open_asset(self):
        """Open linked asset record"""
        self.ensure_one()
        if self.asset_id:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Asset'),
                'res_model': 'asset.addition',
                'res_id': self.asset_id.id,
                'view_mode': 'form',
            }
        return False
