import html
import logging
import re

import requests

_logger = logging.getLogger(__name__)

# Keys used in ir.config_parameter
PARAM_URL = 'asset_helpdesk_llm.url'
PARAM_MODEL = 'asset_helpdesk_llm.model'
PARAM_PROMPT = 'asset_helpdesk_llm.prompt'
PARAM_TIMEOUT = 'asset_helpdesk_llm.timeout'
PARAM_ENABLED = 'asset_helpdesk_llm.enabled'

DEFAULT_PROMPT = (
    "You are an experienced IT / facilities helpdesk engineer. Read the ticket "
    "below and reply in Markdown with exactly these sections:\n"
    "## Likely Cause\n(one or two sentences)\n"
    "## Steps to Fix\n(a numbered list of clear, safe steps an engineer can follow)\n"
    "## If It Still Fails\n(short bullet list: what to check next or when to escalate / call the vendor)\n"
    "Be practical and specific to the equipment named. No preamble, no closing remarks.\n\n"
    "Ticket:\n"
)

_INLINE_RULES = [
    (re.compile(r'`([^`]+)`'), r'<code>\1</code>'),
    (re.compile(r'\*\*([^*]+)\*\*'), r'<strong>\1</strong>'),
    (re.compile(r'(?<![*\w])\*([^*\n]+)\*(?!\*)'), r'<em>\1</em>'),
]


def _inline(text):
    text = html.escape(text, quote=False)
    for rx, repl in _INLINE_RULES:
        text = rx.sub(repl, text)
    return text


def markdown_to_html(text):
    """Small Markdown -> HTML converter for LLM answers (headings, numbered
    and bullet lists, bold/italic/code, paragraphs). Avoids a dependency on
    the `markdown` package, which is not installed on the servers."""
    out, para, list_tag = [], [], None

    def flush_para():
        if para:
            out.append('<p>%s</p>' % '<br/>'.join(_inline(p) for p in para))
            para.clear()

    def close_list():
        nonlocal list_tag
        if list_tag:
            out.append('</%s>' % list_tag)
            list_tag = None

    for raw in (text or '').splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_para()
            continue
        m_head = re.match(r'^(#{1,6})\s+(.*)$', stripped)
        m_num = re.match(r'^\d+[.)]\s+(.*)$', stripped)
        m_bul = re.match(r'^[-*+•]\s+(.*)$', stripped)
        if m_head:
            flush_para(); close_list()
            level = min(len(m_head.group(1)) + 2, 6)   # ## -> h4: fits a form tab
            out.append('<h%d>%s</h%d>' % (level, _inline(m_head.group(2).strip('# ')), level))
        elif m_num or m_bul:
            flush_para()
            tag = 'ol' if m_num else 'ul'
            if list_tag != tag:
                close_list()
                out.append('<%s>' % tag)
                list_tag = tag
            out.append('<li>%s</li>' % _inline((m_num or m_bul).group(1)))
        elif list_tag and raw[:1] in (' ', '\t'):
            # continuation of the previous list item
            out[-1] = out[-1][:-5] + '<br/>' + _inline(stripped) + '</li>'
        else:
            close_list()
            para.append(stripped)
    flush_para(); close_list()
    return ''.join(out)


def error_html(message):
    return '<p class="text-danger"><strong>AI request failed:</strong> %s</p>' % html.escape(message or '')


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
        "max_tokens": 900,
        # Qwen3 (vLLM): answer directly instead of a long <think> block.
        # Servers that do not know the key ignore it.
        "chat_template_kwargs": {"enable_thinking": False},
    }

    try:
        resp = requests.post(url, json=payload, timeout=timeout,
                             headers={"ngrok-skip-browser-warning": "1"})
        resp.raise_for_status()
        data = resp.json()
        # OpenAI-compatible shape (vLLM, llama.cpp server, LM Studio, Ollama /v1)
        content = data["choices"][0]["message"]["content"] or ""
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
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
