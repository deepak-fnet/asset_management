import logging
import time

import requests

_logger = logging.getLogger(__name__)

# Keys used in ir.config_parameter
PARAM_URL = 'voice_to_text.stt_url'
PARAM_TIMEOUT = 'voice_to_text.stt_timeout'
PARAM_CHUNK_SECONDS = 'voice_to_text.stt_chunk_seconds'
PARAM_ENABLED = 'voice_to_text.stt_enabled'

DEFAULT_URL = 'http://localhost:8010'
DEFAULT_TIMEOUT = 30
DEFAULT_CHUNK_SECONDS = 4


def check_health(base_url, timeout=10):
    """Ping the STT microservice's /health endpoint.

    Returns (ok: bool, result: dict|str)
    """
    if not base_url:
        return False, "STT service URL is not configured."
    try:
        resp = requests.get(base_url.rstrip('/') + '/health', timeout=timeout)
        resp.raise_for_status()
        return True, resp.json()
    except requests.exceptions.ConnectionError:
        msg = "Could not connect to the STT service at %s. Is it running?" % base_url
        _logger.warning(msg)
        return False, msg
    except requests.exceptions.Timeout:
        msg = "The STT health check timed out after %s seconds." % timeout
        _logger.warning(msg)
        return False, msg
    except Exception as e:
        msg = "STT health check failed: %s" % e
        _logger.error(msg)
        return False, msg


def transcribe_audio(base_url, audio_bytes, filename="chunk.wav", timeout=30, model="parakeet"):
    """Send a WAV audio chunk to the STT microservice for transcription.

    `model` selects the backend: "parakeet" (Parakeet TDT) or "indian_voice"
    (Whisper, tuned for Indian-accented English).

    Returns (ok: bool, text_or_error: str, latency_ms: float)
    """
    if not base_url:
        return False, "STT service URL is not configured.", 0.0

    start = time.time()
    try:
        resp = requests.post(
            base_url.rstrip('/') + '/transcribe',
            files={"audio": (filename, audio_bytes, "audio/wav")},
            data={"model": model},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        latency_ms = (time.time() - start) * 1000.0
        if not data.get("ok"):
            return False, data.get("error", "Unknown error from STT service."), latency_ms
        return True, data.get("text", ""), data.get("latency_ms", latency_ms)
    except requests.exceptions.ConnectionError:
        msg = "Could not connect to the STT service at %s. Is it running?" % base_url
        _logger.warning(msg)
        return False, msg, (time.time() - start) * 1000.0
    except requests.exceptions.Timeout:
        msg = "The STT request timed out after %s seconds." % timeout
        _logger.warning(msg)
        return False, msg, (time.time() - start) * 1000.0
    except (KeyError, ValueError) as e:
        msg = "Unexpected response format from the STT service: %s" % e
        _logger.error(msg)
        return False, msg, (time.time() - start) * 1000.0
    except Exception as e:
        msg = "STT request failed: %s" % e
        _logger.error(msg)
        return False, msg, (time.time() - start) * 1000.0
