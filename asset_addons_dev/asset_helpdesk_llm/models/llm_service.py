import logging
import requests

_logger = logging.getLogger(__name__)

# Keys used in ir.config_parameter
PARAM_URL = 'asset_helpdesk_llm.url'
PARAM_MODEL = 'asset_helpdesk_llm.model'
PARAM_PROMPT = 'asset_helpdesk_llm.prompt'
PARAM_TIMEOUT = 'asset_helpdesk_llm.timeout'
PARAM_ENABLED = 'asset_helpdesk_llm.enabled'

DEFAULT_PROMPT = (
    "You are an IT helpdesk assistant. Read the user's issue below and give a "
    "short, clear troubleshooting suggestion in plain language.\n\nIssue:\n"
)


def call_llm(url, model, prompt, message, timeout=30):
    """Send `prompt + message` to an OpenAI-compatible LLM endpoint.

    Returns a tuple: (ok: bool, result: str)
      - ok=True  -> result is the AI text
      - ok=False -> result is a human-readable error message
    """
    if not url:
        return False, "LLM URL is not configured."

    full_prompt = (prompt or DEFAULT_PROMPT) + (message or "")

    payload = {
        "model": model or "local-model",
        "messages": [
            {"role": "user", "content": full_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 800,
    }

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        # OpenAI-compatible shape (vLLM, llama.cpp server, LM Studio, Ollama /v1)
        content = data["choices"][0]["message"]["content"].strip()
        return True, content
    except requests.exceptions.ConnectionError:
        msg = "Could not connect to the LLM at %s. Is the server running?" % url
        _logger.warning(msg)
        return False, msg
    except requests.exceptions.Timeout:
        msg = "The LLM request timed out after %s seconds." % timeout
        _logger.warning(msg)
        return False, msg
    except (KeyError, IndexError, ValueError) as e:
        msg = "Unexpected response format from the LLM: %s" % e
        _logger.error(msg)
        return False, msg
    except Exception as e:
        msg = "LLM call failed: %s" % e
        _logger.error(msg)
        return False, msg
