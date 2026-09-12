"""
Odoo controller bridging the browser's recorded audio chunks to the
Parakeet TDT microservice.

Exposes JSON endpoints:
  POST /api/voice_to_text/config      — fetch chunking/enabled config
  POST /api/voice_to_text/transcribe  — transcribe one WAV audio chunk
"""
import base64
import logging

from odoo import http
from odoo.http import request

from ..models import stt_service

_logger = logging.getLogger(__name__)


class VoiceToTextController(http.Controller):

    @http.route("/api/voice_to_text/config", type='jsonrpc', auth="user", methods=["POST"], csrf=False)
    def get_config(self, **kwargs):
        params = request.env["ir.config_parameter"].sudo()
        return {
            "ok": True,
            "enabled": params.get_param(stt_service.PARAM_ENABLED, default=False) in (True, 'True', '1', 1),
            "chunk_seconds": int(params.get_param(
                stt_service.PARAM_CHUNK_SECONDS, default=stt_service.DEFAULT_CHUNK_SECONDS)),
        }

    @http.route("/api/voice_to_text/transcribe", type='jsonrpc', auth="user", methods=["POST"], csrf=False)
    def transcribe(self, **kwargs):
        audio_b64 = kwargs.get("audio_b64")
        duration = kwargs.get("duration") or 0.0

        if not audio_b64:
            return {"ok": False, "message": "No audio data provided."}

        params = request.env["ir.config_parameter"].sudo()
        base_url = params.get_param(stt_service.PARAM_URL, default=stt_service.DEFAULT_URL)
        timeout = int(params.get_param(stt_service.PARAM_TIMEOUT, default=stt_service.DEFAULT_TIMEOUT))

        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception as e:
            return {"ok": False, "message": "Invalid audio data: %s" % e}

        ok, text_or_error, latency_ms = stt_service.transcribe_audio(
            base_url, audio_bytes, filename="chunk.wav", timeout=timeout,
        )

        if ok:
            request.env["voice.transcript.log"].sudo().create({
                "transcript": text_or_error,
                "duration": duration,
                "latency_ms": latency_ms,
            })
            return {"ok": True, "text": text_or_error, "latency_ms": latency_ms}

        _logger.warning("Voice to Text transcription failed: %s", text_or_error)
        return {"ok": False, "message": text_or_error}
