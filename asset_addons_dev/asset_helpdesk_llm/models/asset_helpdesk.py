import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext
from . import llm_service

_logger = logging.getLogger(__name__)


class AssetHelpdesk(models.Model):
    _inherit = 'asset.helpdesk'

    llm_response = fields.Html(
        string="AI Response",
        readonly=True,
        copy=False,
        sanitize=True,
    )
    llm_status = fields.Selection(
        [
            ('none', 'Not Run'),
            ('done', 'Done'),
            ('failed', 'Failed'),
        ],
        string="AI Status",
        default='none',
        readonly=True,
        copy=False,
    )

    def _get_llm_config(self):
        ICP = self.env['ir.config_parameter'].sudo()
        return {
            'enabled': ICP.get_param(llm_service.PARAM_ENABLED) in ('True', 'true', '1'),
            'url': ICP.get_param(llm_service.PARAM_URL),
            'model': ICP.get_param(llm_service.PARAM_MODEL),
            'prompt': ICP.get_param(llm_service.PARAM_PROMPT) or llm_service.DEFAULT_PROMPT,
            'timeout': int(ICP.get_param(llm_service.PARAM_TIMEOUT) or 30),
        }

    def _llm_ticket_text(self):
        """Everything the ticket says about the problem, as plain text.

        The issue lives in the subject and the HTML problem description
        (and, for mailed-in tickets, the mail thread) - `notes` is usually
        empty, so sending only that gave the model nothing to work with."""
        self.ensure_one()
        lines = []

        def add(label, value):
            value = (value or '').strip()
            if value:
                lines.append("%s: %s" % (label, value))

        add("Subject", self.subject)
        add("Ticket type", self.ticket_type_id.display_name)
        add("Category", self.category_id.display_name)
        add("Priority", dict(self._fields['priority'].selection).get(self.priority))
        if self.asset_id:
            asset = self.asset_id
            add("Asset", asset.display_name)
            for fname, label in (('os_name', "OS"), ('processor', "Processor"),
                                 ('ram_size', "RAM (GB)")):
                if fname in asset._fields and asset[fname]:
                    add(label, str(asset[fname]))
        add("Location", self.location_id.display_name if 'location_id' in self._fields else '')
        problem = html2plaintext(self.problem_html or '') if 'problem_html' in self._fields else ''
        add("Problem description", problem)
        add("Notes", self.notes)
        if not problem and 'mail_thread_html' in self._fields and self.mail_thread_html:
            add("Mail thread", html2plaintext(self.mail_thread_html)[:3000])
        return "\n".join(lines)

    def _run_llm(self):
        """Send the ticket to the LLM and store the response as HTML."""
        cfg = self._get_llm_config()
        for rec in self:
            message = rec._llm_ticket_text()
            if not message:
                continue
            ok, result = llm_service.call_llm(
                url=cfg['url'],
                model=cfg['model'],
                prompt=cfg['prompt'],
                message=message,
                timeout=cfg['timeout'],
            )
            if ok:
                rec.llm_response = llm_service.markdown_to_html(result)
            else:
                rec.llm_response = llm_service.error_html(result)
            rec.llm_status = 'done' if ok else 'failed'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        cfg = records._get_llm_config() if records else {'enabled': False}
        if cfg.get('enabled'):
            # Guard the whole thing: a down LLM must never block ticket creation.
            try:
                records._run_llm()
            except Exception as e:  # noqa: BLE001
                _logger.error("LLM auto-run on create failed: %s", e)
        return records

    def action_run_llm(self):
        """Manual button to (re)generate the AI response."""
        if not self._get_llm_config()['url']:
            raise UserError(_(
                "The AI server is not configured. Set the LLM URL in "
                "Settings > Asset Helpdesk LLM."))
        self._run_llm()
        failed = self.filtered(lambda r: r.llm_status == 'failed')
        if failed:
            return {
                'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _("AI request failed"), 'type': 'danger',
                           'message': html2plaintext(failed[0].llm_response or ''),
                           'next': {'type': 'ir.actions.act_window_close'}},
            }
        return True
