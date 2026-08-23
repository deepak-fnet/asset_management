import base64
import io

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class InventoryReportWizard(models.TransientModel):
    _name = "inventory.report.wizard"
    _description = "Inventory Report Wizard"

    date_from = fields.Date(
        string="From Date", required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1),
    )
    date_to = fields.Date(
        string="To Date", required=True,
        default=lambda self: fields.Date.context_today(self),
    )
    picking_type_code = fields.Selection(
        [
            ("incoming", "Receipt"),
            ("internal", "Internal Transfer"),
            ("outgoing", "Delivery"),
        ],
        string="Operation Type", required=True, default="incoming",
    )
    warehouse_id = fields.Many2one("stock.warehouse", string="Warehouse")
    product_id = fields.Many2one("product.product", string="Product")
    state = fields.Selection(
        [
            ("all", "All"),
            ("done", "Done Only"),
            ("pending", "Not Done Only"),
        ],
        string="Status", default="done", required=True,
    )
    report_format = fields.Selection(
        [("xlsx", "Excel (XLSX)"), ("pdf", "PDF")],
        string="Format", default="xlsx", required=True,
    )

    # Download holders
    file_data = fields.Binary(string="File", readonly=True)
    file_name = fields.Char(string="File Name", readonly=True)

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for wiz in self:
            if wiz.date_from and wiz.date_to and wiz.date_from > wiz.date_to:
                raise UserError(_("'From Date' must be earlier than or equal to 'To Date'."))

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def _get_domain(self):
        self.ensure_one()
        # date_to is inclusive: stock.move.date is a Datetime, so compare against
        # the end of the chosen day rather than midnight, otherwise every move
        # made during the final day would be silently excluded.
        # `picking_code` is a related field on stock.move
        # (picking_type_id.code). If it is ever absent in your version, swap the
        # first leaf for ("picking_type_id.code", "=", self.picking_type_code).
        domain = [
            ("picking_type_id.code", "=", self.picking_type_code),
            ("date", ">=", fields.Datetime.to_string(
                fields.Datetime.to_datetime(self.date_from))),
            ("date", "<=", fields.Datetime.to_string(
                fields.Datetime.to_datetime(self.date_to).replace(
                    hour=23, minute=59, second=59))),
        ]
        if self.state == "done":
            domain.append(("state", "=", "done"))
        elif self.state == "pending":
            domain.append(("state", "not in", ["done", "cancel"]))
        else:
            domain.append(("state", "!=", "cancel"))

        if self.product_id:
            domain.append(("product_id", "=", self.product_id.id))
        if self.warehouse_id:
            domain.append(
                ("picking_id.picking_type_id.warehouse_id", "=", self.warehouse_id.id)
            )
        return domain

    def _get_moves(self):
        self.ensure_one()
        return self.env["stock.move"].search(
            self._get_domain(), order="date, picking_id, id"
        )

    def _get_report_rows(self):
        """Flat list of dicts — shared by both the XLSX and the PDF renderer so
        the two formats can never drift apart."""
        self.ensure_one()
        rows = []
        for move in self._get_moves():
            picking = move.picking_id
            rows.append({
                "date": fields.Datetime.context_timestamp(
                    self, move.date).strftime("%Y-%m-%d %H:%M") if move.date else "",
                "reference": picking.name or move.reference or "",
                "partner": picking.partner_id.display_name or "",
                "product": move.product_id.display_name or "",
                "from_location": move.location_id.complete_name or "",
                "to_location": move.location_dest_id.complete_name or "",
                "demand": move.product_uom_qty,
                "quantity": move.quantity,
                "uom": move.product_uom.name or "",
                "state": dict(
                    move._fields["state"].selection
                ).get(move.state, move.state),
            })
        return rows

    def _report_title(self):
        self.ensure_one()
        return dict(self._fields["picking_type_code"].selection)[self.picking_type_code]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_generate_report(self):
        self.ensure_one()
        rows = self._get_report_rows()
        if not rows:
            raise UserError(_(
                "No stock moves found for the selected criteria.\n\n"
                "Operation Type: %s\nPeriod: %s to %s"
            ) % (self._report_title(), self.date_from, self.date_to))

        if self.report_format == "pdf":
            return self.env.ref(
                "inventory_report.action_report_inventory_movement"
            ).report_action(self)
        return self._generate_xlsx(rows)

    def _generate_xlsx(self, rows):
        self.ensure_one()
        try:
            import xlsxwriter
        except ImportError:
            raise UserError(_(
                "The Python library 'xlsxwriter' is required to export XLSX files.\n"
                "Install it with: pip install xlsxwriter\n\n"
                "Alternatively, choose the PDF format."
            ))

        buffer = io.BytesIO()
        workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
        sheet = workbook.add_worksheet(self._report_title()[:31])

        f_title = workbook.add_format({
            "bold": True, "font_size": 14, "align": "center", "valign": "vcenter",
        })
        f_sub = workbook.add_format({"italic": True, "font_size": 10, "align": "center"})
        f_head = workbook.add_format({
            "bold": True, "bg_color": "#D9D9D9", "border": 1,
            "align": "center", "valign": "vcenter", "text_wrap": True,
        })
        f_text = workbook.add_format({"border": 1, "valign": "vcenter"})
        f_num = workbook.add_format({"border": 1, "num_format": "0.00", "align": "right"})
        f_total = workbook.add_format({"bold": True, "border": 1, "bg_color": "#F2F2F2"})
        f_total_num = workbook.add_format({
            "bold": True, "border": 1, "bg_color": "#F2F2F2",
            "num_format": "0.00", "align": "right",
        })

        headers = [
            ("Date", 18), ("Reference", 20), ("Partner", 24), ("Product", 32),
            ("From", 24), ("To", 24), ("Demand", 12), ("Quantity", 12),
            ("UoM", 10), ("Status", 14),
        ]
        last_col = len(headers) - 1

        sheet.merge_range(0, 0, 0, last_col, "%s Report" % self._report_title(), f_title)
        sheet.merge_range(
            1, 0, 1, last_col,
            "Period: %s to %s%s%s" % (
                self.date_from, self.date_to,
                " | Warehouse: %s" % self.warehouse_id.name if self.warehouse_id else "",
                " | Product: %s" % self.product_id.display_name if self.product_id else "",
            ),
            f_sub,
        )

        row_idx = 3
        for col, (label, width) in enumerate(headers):
            sheet.write(row_idx, col, label, f_head)
            sheet.set_column(col, col, width)
        sheet.freeze_panes(row_idx + 1, 0)

        total_demand = total_qty = 0.0
        for line in rows:
            row_idx += 1
            sheet.write(row_idx, 0, line["date"], f_text)
            sheet.write(row_idx, 1, line["reference"], f_text)
            sheet.write(row_idx, 2, line["partner"], f_text)
            sheet.write(row_idx, 3, line["product"], f_text)
            sheet.write(row_idx, 4, line["from_location"], f_text)
            sheet.write(row_idx, 5, line["to_location"], f_text)
            sheet.write_number(row_idx, 6, line["demand"] or 0.0, f_num)
            sheet.write_number(row_idx, 7, line["quantity"] or 0.0, f_num)
            sheet.write(row_idx, 8, line["uom"], f_text)
            sheet.write(row_idx, 9, line["state"], f_text)
            total_demand += line["demand"] or 0.0
            total_qty += line["quantity"] or 0.0

        row_idx += 1
        sheet.merge_range(row_idx, 0, row_idx, 5, "Total (%d moves)" % len(rows), f_total)
        sheet.write_number(row_idx, 6, total_demand, f_total_num)
        sheet.write_number(row_idx, 7, total_qty, f_total_num)
        sheet.write(row_idx, 8, "", f_total)
        sheet.write(row_idx, 9, "", f_total)

        workbook.close()
        buffer.seek(0)

        filename = "Inventory_%s_%s_to_%s.xlsx" % (
            self._report_title().replace(" ", "_"), self.date_from, self.date_to,
        )
        self.write({
            "file_data": base64.b64encode(buffer.read()),
            "file_name": filename,
        })
        buffer.close()

        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/?model=%s&id=%s&field=file_data&filename_field=file_name&download=true"
                   % (self._name, self.id),
            "target": "self",
        }
