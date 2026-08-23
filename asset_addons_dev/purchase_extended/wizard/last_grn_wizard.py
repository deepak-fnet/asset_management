# wizard/last_grn_wizard.py

from odoo import models, fields, api

class LastGrnWizard(models.TransientModel):
    _name = "last.grn.wizard"
    _description = "Last 3 GRN Wizard"

    product_id = fields.Many2one("product.product", string="Product")
    html_data = fields.Html(string="GRN Details", readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        product_id = self.env.context.get('default_product_id')
        if not product_id:
            return res
        product = self.env['product.product'].browse(product_id)
        pickings = self.env['stock.picking'].search([
            ('state', '=', 'done'),
            ('picking_type_id.code', '=', 'incoming'),
            ('move_ids.product_id', '=', product.id),
        ], order="date_done desc", limit=3)

        rows = ""
        count = 0

        for picking in pickings:
            for move in picking.move_ids.filtered(lambda m: m.product_id.id == product.id):

                # Skip if no purchase line or no price
                if not move.purchase_line_id or not move.purchase_line_id.price_unit:
                    continue

                rows += f"""
                    <tr style="background-color:#ffffff;">
                        <td style="padding:8px; border-bottom:1px solid #f0f0f0;">
                            {picking.partner_id.name}
                        </td>
                        <td style="padding:8px; border-bottom:1px solid #f0f0f0; color:#007bff;">
                            {picking.name}
                        </td>
                        <td style="padding:8px; border-bottom:1px solid #f0f0f0; text-align:left;">
                            {picking.date_done.strftime('%d/%m/%Y') if picking.date_done else ''}
                        </td>
                        <td style="padding:8px; border-bottom:1px solid #f0f0f0; text-align:right;">
                            {'%.1f' % move.quantity}
                        </td>
                        <td style="padding:8px; border-bottom:1px solid #f0f0f0; text-align:right;">
                            {'%.2f' % move.purchase_line_id.price_unit}
                        </td>
                    </tr>
                """

                count += 1
                if count == 3:
                    break
            if count == 3:
                break
        html_table = f"""
            <div style="padding:15px;">
                <table style="
                    width:100%;
                    border-collapse:separate;
                    border-spacing:0;
                    font-size:13px;
                    font-family: Arial, sans-serif;
                    box-shadow:0 2px 8px rgba(0,0,0,0.05);
                    border-radius:8px;
                    overflow:hidden;
                ">
                    <thead>
                        <tr style="background-color:#e8f4fd; color:#1f4e79;">
                            <th style="padding:10px; text-align:left;">Vendor</th>
                            <th style="padding:10px; text-align:left;">GRN No</th>
                            <th style="padding:10px; text-align:left;">Date</th>
                            <th style="padding:10px; text-align:right;">Qty</th>
                            <th style="padding:10px; text-align:right;">Price</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows if rows else '''
                        <tr>
                            <td colspan="6" style="padding:12px; text-align:center; color:#888;">
                                No Records Found
                            </td>
                        </tr>
                        '''}
                    </tbody>
                </table>
            </div>
        """

        res['html_data'] = html_table
        res['product_id'] = product.id
        return res