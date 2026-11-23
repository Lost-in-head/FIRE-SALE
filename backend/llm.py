"""One completion helper for every agent that needs a model.

Agents call ``complete`` and do not import a provider SDK. The default is
Anthropic Claude Haiku. ``LLM_PROVIDER=openai_compatible`` uses the OpenAI
SDK against OpenAI or another host with the same chat-completions API.

A missing key raises ``EnvironmentError`` so the director can skip that
stage. API keys and email addresses are removed from errors before they
are raised, so logs do not print them.
"""

import logging
import os
import re
from dataclasses import dataclass
from json import JSONDecodeError
from typing import NoReturn
from urllib.parse import urlsplit, urlunsplit

import anthropic
from openai import OpenAI

log = logging.getLogger(__name__)

DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
ANTHROPIC_PROVIDER = "anthropic"
OPENAI_COMPATIBLE_PROVIDER = "openai_compatible"

# Preset names for LLM_BASE_URL. A full URL is also accepted.
# Gemini's OpenAI-compatible root keeps the trailing slash from Google's docs.
OPENAI_COMPATIBLE_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.ai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "xai": "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}

_EMAIL = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_REJECT_MARKERS = (
    "unsupported",
    "not supported",
    "unknown",
    "unrecognized",
    "unexpected",
    "invalid",
    "extra",
    "does not support",
    "isn't supported",
    "is not allowed",
)
_ANNOUNCED: set[tuple[str, str]] = set()

_SCORE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "score": {"type": "integer"},
        "verdict": {"type": "string"},
        "flags": {"type": "string"},
        "action": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["score", "verdict", "flags", "action", "summary"],
}
_EMAIL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["subject", "body"],
}


@dataclass(frozen=True)
class LlmSettings:
    provider: str
    model: str
    api_key: str
    base_url: str | None

    def __repr__(self) -> str:
        return (
            "LlmSettings("
            f"provider={self.provider!r}, model={self.model!r}, "
            f"api_key='[redacted]', base_url={_safe_url(self.base_url)!r})"
        )


def require_llm() -> LlmSettings:
    """Read provider settings. Raise EnvironmentError when the key is missing."""
    settings = load_settings()
    _announce(settings)
    return settings


def complete(prompt: str, max_tokens: int, json: bool = False) -> str:
    """Return the model text. When json is true, retry once if it is not a JSON object."""
    settings = require_llm()
    text = _dispatch(settings, prompt, max_tokens, json)
    if not json:
        return text
    # agents/__init__ imports every agent, and those agents import this module.
    # A top-level import of agents.messaging would cycle, so the parser is
    # imported only once a call actually needs it.
    from agents.messaging import parse_json_object

    try:
        parse_json_object(text)
    except JSONDecodeError:
        return _dispatch(settings, prompt, max_tokens, True)
    return text


def load_settings() -> LlmSettings:
    provider = _env("LLM_PROVIDER").lower() or ANTHROPIC_PROVIDER
    if provider not in (ANTHROPIC_PROVIDER, OPENAI_COMPATIBLE_PROVIDER):
        raise EnvironmentError(
            f"LLM_PROVIDER '{provider}' is not supported. "
            "Use anthropic or openai_compatible."
        )
    model = _env("LLM_MODEL")
    if not model:
        if provider != ANTHROPIC_PROVIDER:
            raise EnvironmentError(
                "LLM_MODEL is not set. Set it to the model id for this provider."
            )
        model = DEFAULT_ANTHROPIC_MODEL
    return LlmSettings(
        provider=provider,
        model=model,
        api_key=_api_key(provider),
        base_url=_base_url(provider),
    )


def _dispatch(settings: LlmSettings, prompt: str, max_tokens: int, json_mode: bool) -> str:
    if settings.provider == ANTHROPIC_PROVIDER:
        return _anthropic_complete(settings, prompt, max_tokens, json_mode)
    if settings.provider == OPENAI_COMPATIBLE_PROVIDER:
        return _openai_complete(settings, prompt, max_tokens, json_mode)
    raise EnvironmentError(
        f"LLM_PROVIDER '{settings.provider}' is not supported. "
        "Use anthropic or openai_compatible."
    )


def _anthropic_complete(
    settings: LlmSettings,
    prompt: str,
    max_tokens: int,
    json_mode: bool,
) -> str:
    """Adaptive thinking at low effort. Drop a control the model rejects.

    Haiku 5.5 thinks adaptively and bills thinking tokens against max_tokens.
    Effort ``low`` is the cheap setting. If that still returns no text, one
    follow-up turns thinking off so the reply fits the caller's budget.
    """
    schema = _schema_for_prompt(prompt) if json_mode else None
    state = {"thinking": "adaptive", "schema": schema is not None, "effort": "low"}
    seen: set[tuple] = set()
    last_error: BaseException | None = None
    for _ in range(4):
        signature = (state["thinking"], state["schema"], state["effort"])
        if signature in seen:
            break
        seen.add(signature)
        try:
            text = _anthropic_request(
                settings,
                prompt,
                max_tokens,
                schema if state["schema"] else None,
                str(state["thinking"]),
                state["effort"] if isinstance(state["effort"], str) else None,
            )
        except Exception as exc:
            last_error = exc
            changed = False
            if state["schema"] and _param_rejected(exc, ("format", "json_schema")):
                state["schema"] = False
                changed = True
            if state["effort"] and _param_rejected(exc, ("effort",)):
                state["effort"] = None
                changed = True
            if state["thinking"] == "adaptive" and _param_rejected(exc, ("thinking", "adaptive")):
                state["thinking"] = "disabled"
                changed = True
            if not changed:
                _raise_api(exc, settings.api_key)
            continue
        if text.strip() or state["thinking"] == "disabled":
            return text
        state["thinking"] = "disabled"
    if last_error is not None:
        _raise_api(last_error, settings.api_key)
    return ""


def _anthropic_request(
    settings: LlmSettings,
    prompt: str,
    max_tokens: int,
    schema: dict | None,
    thinking: str,
    effort: str | None,
) -> str:
    kwargs: dict = {"api_key": settings.api_key}
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    client = anthropic.Anthropic(**kwargs)
    request: dict = {
        "model": settings.model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "thinking": {"type": thinking},
    }
    output_config: dict = {}
    if effort:
        output_config["effort"] = effort
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    if output_config:
        request["output_config"] = output_config
    response = client.messages.create(**request)
    return _anthropic_text(response)


def _anthropic_text(response) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "thinking":
            continue
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts).strip()


def _openai_complete(
    settings: LlmSettings,
    prompt: str,
    max_tokens: int,
    json_mode: bool,
) -> str:
    """Chat completions. Prefer reasoning_effort none, then minimal, then omit it."""
    client_kwargs: dict = {"api_key": settings.api_key}
    if settings.base_url:
        client_kwargs["base_url"] = settings.base_url
    client = OpenAI(**client_kwargs)
    content = prompt
    if json_mode and "json" not in prompt.lower():
        content = prompt + "\n\nReturn a JSON object."
    messages = [{"role": "user", "content": content}]
    efforts: tuple[str | None, ...] = ("none", "minimal", None)
    effort_index = 0
    use_json = json_mode
    token_field = "max_tokens"
    seen: set[tuple] = set()
    last_error: BaseException | None = None
    for _ in range(5):
        effort = efforts[effort_index]
        signature = (effort, use_json, token_field)
        if signature in seen:
            break
        seen.add(signature)
        request: dict = {
            "model": settings.model,
            "messages": messages,
            token_field: max_tokens,
        }
        if effort is not None:
            request["reasoning_effort"] = effort
        if use_json:
            request["response_format"] = {"type": "json_object"}
        try:
            response = client.chat.completions.create(**request)
            return _openai_text(response)
        except Exception as exc:
            last_error = exc
            if _status_code(exc) not in (None, 400, 404, 422) and not isinstance(exc, TypeError):
                break
            changed = False
            if effort is not None and _param_rejected(exc, ("reasoning_effort", "reasoning effort")):
                if effort_index < len(efforts) - 1:
                    effort_index += 1
                    changed = True
            if use_json and _param_rejected(exc, ("response_format", "json_object")):
                use_json = False
                changed = True
            if (
                token_field == "max_tokens"
                and _param_rejected(exc, ("max_tokens",))
                and "max_completion_tokens" in str(exc).lower()
            ):
                token_field = "max_completion_tokens"
                changed = True
            if not changed:
                break
    if last_error is None:
        _raise_api(RuntimeError("LLM request failed."), settings.api_key)
    _raise_api(last_error, settings.api_key)


def _openai_text(response) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    content = getattr(choices[0].message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            text = getattr(part, "text", None)
            if text is None and isinstance(part, dict):
                text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts).strip()
    return ""


def _schema_for_prompt(prompt: str) -> dict | None:
    """Use JSON schema mode for the two object shapes the pipeline asks for."""
    if '"subject"' in prompt and '"body"' in prompt:
        return _EMAIL_SCHEMA
    if '"score"' in prompt and '"verdict"' in prompt:
        return _SCORE_SCHEMA
    return None


def _api_key(provider: str) -> str:
    own = _env("LLM_API_KEY")
    if own:
        return own
    if provider == ANTHROPIC_PROVIDER:
        fallback = _env("ANTHROPIC_API_KEY")
        if fallback:
            return fallback
        raise EnvironmentError(
            "LLM_API_KEY is not set and ANTHROPIC_API_KEY is not set."
        )
    raise EnvironmentError("LLM_API_KEY is not set.")


def _base_url(provider: str) -> str | None:
    raw = _env("LLM_BASE_URL")
    if not raw:
        return None
    if provider == OPENAI_COMPATIBLE_PROVIDER:
        return OPENAI_COMPATIBLE_BASE_URLS.get(raw.lower(), raw)
    if "://" not in raw:
        raise EnvironmentError(
            "LLM_BASE_URL must be a full URL for the anthropic provider."
        )
    return raw


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _announce(settings: LlmSettings) -> None:
    key = (settings.provider, settings.model)
    if key in _ANNOUNCED:
        return
    _ANNOUNCED.add(key)
    log.info("LLM provider=%s model=%s", settings.provider, settings.model)


def _param_rejected(exc: BaseException, names: tuple[str, ...]) -> bool:
    status = _status_code(exc)
    if status is not None and status not in (400, 404, 422):
        return False
    text = str(exc).lower()
    if not any(name in text for name in names):
        return False
    if isinstance(exc, TypeError):
        return True
    return any(marker in text for marker in _REJECT_MARKERS)


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    return None


def _public_error(exc: BaseException, secret: str) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    if secret:
        text = text.replace(secret, "[redacted]")
    text = _EMAIL.sub("[redacted-email]", text)
    if len(text) > 400:
        text = text[:400] + "..."
    return text


def _raise_api(exc: BaseException, secret: str) -> NoReturn:
    raise RuntimeError(_public_error(exc, secret)) from None


def _safe_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))
