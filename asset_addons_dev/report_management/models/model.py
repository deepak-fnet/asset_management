from odoo import models, api

class ReportManagement(models.AbstractModel):
    _name = 'report.management'
    _description = 'Report Management'

    @api.model
    def amount_total_in_words(self):
        try:
            amount_in_words = self.currency_id.amount_to_text(
                self.amount_total
            ).replace(',', '')
            return amount_in_words
        except Exception as e:
            return f"Error converting amount to words: {e}"


from odoo import models

class CustomAgedReceivableReport(models.AbstractModel):
    _name = 'report.report_management.custom_aged_receivable_pdf'
    _description = 'Custom Aged Receivable PDF'

    def _get_report_values(self, docids, data=None):
        report = self.env['account.report'].browse(
            self.env.context.get('active_id')
        )

        options = report._get_options()
        lines = report._get_lines(options)

        return {
            'doc_ids': docids,
            'doc_model': 'account.report',
            'docs': report,
            'lines': lines,
            'options': options,
        }