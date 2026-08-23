import logging

from odoo import api, fields, models
from . import llm_service

_logger = logging.getLogger(__name__)


class AssetHelpdesk(models.Model):
    _inherit = 'asset.helpdesk'

    llm_response = fields.Text(
        string="AI Response",
        readonly=True,
        copy=False,
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

    def _run_llm(self):
        """Send self.notes to the LLM and store the response."""
        cfg = self._get_llm_config()
        for rec in self:
            if not rec.notes:
                continue
            ok, result = llm_service.call_llm(
                url=cfg['url'],
                model=cfg['model'],
                prompt=cfg['prompt'],
                message=rec.notes,
                timeout=cfg['timeout'],
            )
            rec.llm_response = result
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
        self._run_llm()
