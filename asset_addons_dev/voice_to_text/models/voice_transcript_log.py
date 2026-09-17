from odoo import api, fields, models


class VoiceTranscriptLog(models.Model):
    _name = 'voice.transcript.log'
    _description = 'Voice to Text Transcript Log'
    _order = 'create_date desc'
    _rec_name = 'transcript'

    user_id = fields.Many2one(
        'res.users', string="User", default=lambda self: self.env.user, readonly=True,
    )
    model = fields.Selection(
        [('parakeet', 'Parakeet TDT'), ('indian_voice', 'Indian Voice')],
        string="Model", default='parakeet', readonly=True,
    )
    transcript = fields.Text(string="Transcript", readonly=True)
    duration = fields.Float(
        string="Chunk Duration (s)", readonly=True,
        help="Length of the audio chunk that produced this transcript.",
    )
    latency_ms = fields.Float(string="Latency (ms)", readonly=True)
    words_per_minute = fields.Float(
        string="Words / Minute", compute='_compute_words_per_minute', store=True,
    )

    @api.depends('transcript', 'duration')
    def _compute_words_per_minute(self):
        for rec in self:
            word_count = len((rec.transcript or '').split())
            if rec.duration:
                rec.words_per_minute = (word_count / rec.duration) * 60.0
            else:
                rec.words_per_minute = 0.0
