from odoo import api, fields, models, _
from . import stt_service


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    stt_enabled = fields.Boolean(
        string="Enable Voice to Text",
        config_parameter=stt_service.PARAM_ENABLED,
    )
    stt_url = fields.Char(
        string="STT Service URL",
        config_parameter=stt_service.PARAM_URL,
        default=stt_service.DEFAULT_URL,
        help="Base URL of the Parakeet TDT microservice, e.g. http://localhost:8010",
    )
    stt_timeout = fields.Integer(
        string="Timeout (seconds)",
        config_parameter=stt_service.PARAM_TIMEOUT,
        default=stt_service.DEFAULT_TIMEOUT,
    )
    stt_chunk_seconds = fields.Integer(
        string="Chunk Length (seconds)",
        config_parameter=stt_service.PARAM_CHUNK_SECONDS,
        default=stt_service.DEFAULT_CHUNK_SECONDS,
        help="Length of each audio window the browser records and sends for "
             "transcription. Shorter = lower latency but less context for the "
             "model; longer = better accuracy but slower feedback.",
    )

    def action_test_stt_connection(self):
        """Test button: ping the STT microservice's health endpoint."""
        self.ensure_one()
        ok, result = stt_service.check_health(
            base_url=self.stt_url,
            timeout=self.stt_timeout or stt_service.DEFAULT_TIMEOUT,
        )
        if ok:
            title = _("Connection Successful")
            msg = _("STT service responded:\n\n%s") % (result,)
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
