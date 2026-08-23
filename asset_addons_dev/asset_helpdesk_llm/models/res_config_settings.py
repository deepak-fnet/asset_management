from odoo import api, fields, models, _
from . import llm_service


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    llm_enabled = fields.Boolean(
        string="Enable LLM on Helpdesk",
        config_parameter=llm_service.PARAM_ENABLED,
    )
    llm_url = fields.Char(
        string="LLM URL",
        config_parameter=llm_service.PARAM_URL,
        help="Full endpoint, e.g. http://localhost:8001/v1/chat/completions",
    )
    llm_model = fields.Char(
        string="Model Name",
        config_parameter=llm_service.PARAM_MODEL,
        help="Model identifier your server expects.",
    )
    llm_prompt = fields.Char(
        string="Default Prompt",
        config_parameter=llm_service.PARAM_PROMPT,
        default=llm_service.DEFAULT_PROMPT,
        help="Prepended before the ticket notes when calling the LLM.",
    )
    llm_timeout = fields.Integer(
        string="Timeout (seconds)",
        config_parameter=llm_service.PARAM_TIMEOUT,
        default=30,
    )

    def action_test_llm_connection(self):
        """Test button: send a short probe message to the LLM."""
        self.ensure_one()
        ok, result = llm_service.call_llm(
            url=self.llm_url,
            model=self.llm_model,
            prompt="Reply with a short confirmation that you are working.",
            message="",
            timeout=self.llm_timeout or 30,
        )
        if ok:
            title = _("Connection Successful")
            msg = _("LLM responded:\n\n%s") % (result[:500],)
            ntype = 'success'
        else:
            title = _("Connection Failed")
            msg = result
            ntype = 'danger'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': msg,
                'type': ntype,
                'sticky': True,
            },
        }
