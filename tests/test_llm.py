"""Provider selection, defaults, and the OpenAI-compatible path. No live calls."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import llm
from llm import (
    DEFAULT_ANTHROPIC_MODEL,
    OPENAI_COMPATIBLE_BASE_URLS,
    complete,
    require_llm,
)

ROOT = Path(__file__).resolve().parents[1]
_LLM_ENV = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "ANTHROPIC_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolated_llm_env(monkeypatch):
    for name in _LLM_ENV:
        monkeypatch.delenv(name, raising=False)
    llm._ANNOUNCED.clear()


def _text_message(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    message = MagicMock()
    message.content = [block]
    return message


def _chat_message(text: str):
    message = MagicMock()
    message.content = text
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


def test_blank_actions_vars_default_to_haiku_and_the_anthropic_key():
    """GitHub Actions passes unset variables as empty strings."""
    import os

    os.environ["LLM_PROVIDER"] = ""
    os.environ["LLM_MODEL"] = "  "
    os.environ["LLM_API_KEY"] = ""
    os.environ["LLM_BASE_URL"] = ""
    os.environ["ANTHROPIC_API_KEY"] = "anthropic-fallback-key"
    client = MagicMock()
    client.messages.create.return_value = _text_message("ok")
    with patch("llm.anthropic.Anthropic", return_value=client) as ctor:
        assert complete("Hello", 16) == "ok"
    ctor.assert_called_once_with(api_key="anthropic-fallback-key")
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == DEFAULT_ANTHROPIC_MODEL
    assert kwargs["model"] == "claude-haiku-5-5"
    assert kwargs["max_tokens"] == 16
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": "low"}


def test_llm_api_key_wins_over_the_anthropic_fallback():
    import os

    os.environ["LLM_API_KEY"] = "preferred-llm-key"
    os.environ["ANTHROPIC_API_KEY"] = "anthropic-fallback-key"
    os.environ["LLM_MODEL"] = "claude-haiku-4-5"
    client = MagicMock()
    client.messages.create.return_value = _text_message("ok")
    with patch("llm.anthropic.Anthropic", return_value=client) as ctor:
        complete("Hello", 8)
    ctor.assert_called_once_with(api_key="preferred-llm-key")
    assert client.messages.create.call_args.kwargs["model"] == "claude-haiku-4-5"


def test_missing_anthropic_key_names_both_variables_and_hides_nothing_secret():
    with pytest.raises(EnvironmentError, match="LLM_API_KEY") as exc:
        require_llm()
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_openai_compatible_does_not_accept_the_anthropic_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-fallback-key")
    with pytest.raises(EnvironmentError, match="LLM_API_KEY is not set") as exc:
        require_llm()
    assert "anthropic-fallback-key" not in str(exc.value)


def test_openai_compatible_requires_a_model():
    import os

    os.environ["LLM_PROVIDER"] = "OpenAI_Compatible"
    os.environ["LLM_API_KEY"] = "provider-key"
    with pytest.raises(EnvironmentError, match="LLM_MODEL"):
        require_llm()


def test_unknown_provider_is_rejected():
    import os

    os.environ["LLM_PROVIDER"] = "vendor-x"
    os.environ["LLM_API_KEY"] = "provider-key"
    with pytest.raises(EnvironmentError, match="openai_compatible"):
        require_llm()


def test_groq_preset_uses_json_mode_and_reasoning_effort_none():
    import os

    os.environ["LLM_PROVIDER"] = "openai_compatible"
    os.environ["LLM_MODEL"] = "llama-3.3-70b-versatile"
    os.environ["LLM_API_KEY"] = "groq-test-key"
    os.environ["LLM_BASE_URL"] = "groq"
    client = MagicMock()
    client.chat.completions.create.return_value = _chat_message('{"subject": "Hi", "body": "There"}')
    prompt = 'Return JSON {"subject": "...", "body": "..."}'
    with patch("llm.OpenAI", return_value=client) as ctor:
        text = complete(prompt, 40, json=True)
    assert text == '{"subject": "Hi", "body": "There"}'
    ctor.assert_called_once_with(
        api_key="groq-test-key",
        base_url="https://api.groq.com/openai/v1",
    )
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "llama-3.3-70b-versatile"
    assert kwargs["max_tokens"] == 40
    assert kwargs["reasoning_effort"] == "none"
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["messages"][0]["content"] == prompt


def test_explicit_base_url_is_passed_through_and_plain_text_skips_json_mode():
    import os

    os.environ["LLM_PROVIDER"] = "openai_compatible"
    os.environ["LLM_MODEL"] = "local"
    os.environ["LLM_API_KEY"] = "ollama"
    os.environ["LLM_BASE_URL"] = "http://localhost:11434/v1"
    client = MagicMock()
    client.chat.completions.create.return_value = _chat_message("plain")
    with patch("llm.OpenAI", return_value=client) as ctor:
        assert complete("Write a heading", 12, json=False) == "plain"
    ctor.assert_called_once_with(api_key="ollama", base_url="http://localhost:11434/v1")
    kwargs = client.chat.completions.create.call_args.kwargs
    assert "response_format" not in kwargs
    assert kwargs["reasoning_effort"] == "none"


def test_reasoning_effort_falls_back_to_minimal_then_omitted():
    import os

    os.environ["LLM_PROVIDER"] = "openai_compatible"
    os.environ["LLM_MODEL"] = "gpt-test"
    os.environ["LLM_API_KEY"] = "openai-test-key"
    os.environ["LLM_BASE_URL"] = "openai"
    client = MagicMock()
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        effort = kwargs.get("reasoning_effort")
        if effort == "none":
            raise RuntimeError("400 unsupported parameter: reasoning_effort")
        if effort == "minimal":
            raise RuntimeError("400 invalid reasoning_effort")
        return _chat_message("done")

    client.chat.completions.create.side_effect = create
    with patch("llm.OpenAI", return_value=client):
        assert complete("Say done", 8) == "done"
    assert [call.get("reasoning_effort", None) for call in calls] == ["none", "minimal", None]
    assert calls[-1]["model"] == "gpt-test"
    assert OPENAI_COMPATIBLE_BASE_URLS["openai"] == "https://api.openai.com/v1"


def test_json_mode_and_max_tokens_are_dropped_when_the_host_rejects_them():
    import os

    os.environ["LLM_PROVIDER"] = "openai_compatible"
    os.environ["LLM_MODEL"] = "legacy"
    os.environ["LLM_API_KEY"] = "openai-test-key"
    client = MagicMock()
    calls = []

    def create(**kwargs):
        calls.append(dict(kwargs))
        if "response_format" in kwargs:
            raise RuntimeError("400 response_format is not supported")
        if "max_tokens" in kwargs:
            raise RuntimeError("400 unsupported max_tokens; use max_completion_tokens")
        return _chat_message('{"ok": true}')

    client.chat.completions.create.side_effect = create
    with patch("llm.OpenAI", return_value=client):
        text = complete("Return a subject and body", 9, json=True)
    assert text == '{"ok": true}'
    assert "response_format" not in calls[-1]
    assert "max_completion_tokens" in calls[-1]
    assert "json" in calls[-1]["messages"][0]["content"].lower()


def test_bad_json_is_retried_once():
    import os

    os.environ["ANTHROPIC_API_KEY"] = "anthropic-fallback-key"
    client = MagicMock()
    client.messages.create.side_effect = [
        _text_message("not json"),
        _text_message('{"ok": true}'),
    ]
    with patch("llm.anthropic.Anthropic", return_value=client):
        assert complete("Return a JSON object", 20, json=True) == '{"ok": true}'
    assert client.messages.create.call_count == 2


def test_a_second_bad_json_reply_is_returned_for_the_caller_to_parse():
    import os

    os.environ["ANTHROPIC_API_KEY"] = "anthropic-fallback-key"
    client = MagicMock()
    client.messages.create.side_effect = [
        _text_message("nope"),
        _text_message("still nope"),
    ]
    with patch("llm.anthropic.Anthropic", return_value=client):
        assert complete("Return a JSON object", 20, json=True) == "still nope"
    assert client.messages.create.call_count == 2


def test_valid_json_is_not_requested_twice():
    import os

    os.environ["ANTHROPIC_API_KEY"] = "anthropic-fallback-key"
    client = MagicMock()
    client.messages.create.return_value = _text_message('```json\n{"ok": 1}\n```')
    with patch("llm.anthropic.Anthropic", return_value=client):
        text = complete("Return a JSON object", 20, json=True)
    assert text == '```json\n{"ok": 1}\n```'
    assert client.messages.create.call_count == 1


def test_score_and_email_prompts_ask_for_json_schema():
    import os

    os.environ["ANTHROPIC_API_KEY"] = "anthropic-fallback-key"
    client = MagicMock()
    client.messages.create.return_value = _text_message('{"score": 1}')
    with patch("llm.anthropic.Anthropic", return_value=client):
        complete('Return ONLY JSON {"score": 1, "verdict": "HOT"}', 30, json=True)
    schema = client.messages.create.call_args.kwargs["output_config"]["format"]
    assert schema["type"] == "json_schema"
    assert "score" in schema["schema"]["properties"]
    assert schema["schema"]["additionalProperties"] is False

    client.messages.create.return_value = _text_message('{"subject": "Hi", "body": "There"}')
    with patch("llm.anthropic.Anthropic", return_value=client):
        complete('Return ONLY JSON {"subject": "Hi", "body": "There"}', 30, json=True)
    email_schema = client.messages.create.call_args.kwargs["output_config"]["format"]["schema"]
    assert set(email_schema["properties"]) == {"subject", "body"}


def test_empty_thinking_block_is_followed_by_one_call_with_thinking_off():
    import os

    os.environ["ANTHROPIC_API_KEY"] = "anthropic-fallback-key"
    thinking = MagicMock()
    thinking.type = "thinking"
    thinking.text = "plan for lead@example.com"
    empty = MagicMock()
    empty.content = [thinking]
    client = MagicMock()
    client.messages.create.side_effect = [empty, _text_message("visible answer")]
    with patch("llm.anthropic.Anthropic", return_value=client):
        assert complete("Hello", 20) == "visible answer"
    assert client.messages.create.call_args_list[0].kwargs["thinking"] == {"type": "adaptive"}
    assert client.messages.create.call_args_list[1].kwargs["thinking"] == {"type": "disabled"}


def test_rejected_json_schema_is_retried_without_it():
    import os

    os.environ["ANTHROPIC_API_KEY"] = "anthropic-fallback-key"
    client = MagicMock()

    def create(**kwargs):
        if "format" in kwargs.get("output_config", {}):
            raise RuntimeError("400 unknown parameter output_config.format is not supported")
        return _text_message('{"score": 2, "verdict": "COLD"}')

    client.messages.create.side_effect = create
    with patch("llm.anthropic.Anthropic", return_value=client):
        text = complete('Return ONLY JSON {"score": 1, "verdict": "HOT"}', 30, json=True)
    assert '"score"' in text
    assert client.messages.create.call_count == 2


def test_api_errors_drop_the_key_and_email_addresses(caplog):
    import os

    secret = "sk-live-secret-value"
    os.environ["LLM_API_KEY"] = secret
    client = MagicMock()

    class Denied(Exception):
        status_code = 401

    client.messages.create.side_effect = Denied(
        f"auth failed for {secret} contact pat@shop.test"
    )
    caplog.set_level(logging.INFO)
    with patch("llm.anthropic.Anthropic", return_value=client):
        with pytest.raises(RuntimeError) as exc:
            complete("Hello", 8)
    assert client.messages.create.call_count == 1
    assert secret not in str(exc.value)
    assert "pat@shop.test" not in str(exc.value)
    assert "[redacted]" in str(exc.value)
    assert "[redacted-email]" in str(exc.value)
    assert secret not in caplog.text
    assert "pat@shop.test" not in caplog.text
    assert secret not in repr(require_llm())


def test_settings_repr_strips_a_key_hidden_in_the_base_url():
    import os

    os.environ["LLM_PROVIDER"] = "openai_compatible"
    os.environ["LLM_MODEL"] = "local"
    os.environ["LLM_API_KEY"] = "provider-key-value"
    os.environ["LLM_BASE_URL"] = "https://user:sk-live-secret-value@example.test/v1?token=sk-live-secret-value"
    settings = require_llm()
    rendered = repr(settings)
    assert "provider-key-value" not in rendered
    assert "sk-live-secret-value" not in rendered
    assert "example.test" in rendered


def test_documented_preset_urls():
    assert OPENAI_COMPATIBLE_BASE_URLS == {
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
    guide = (ROOT / "docs" / "first-internal-test-run.md").read_text()
    example = (ROOT / ".env.example").read_text()
    readme = (ROOT / "README.md").read_text()
    for name, url in OPENAI_COMPATIBLE_BASE_URLS.items():
        assert name in readme
        assert url in guide
        assert url in example


def test_agents_do_not_import_anthropic():
    for name in (
        "lead_gen_agent.py",
        "outreach_agent.py",
        "followup_agent.py",
        "closer_agent.py",
    ):
        text = (ROOT / "backend" / "agents" / name).read_text()
        assert "import anthropic" not in text
        assert "claude-opus-4-5" not in text
        assert "messages.create" not in text
        assert "from llm import complete" in text


def test_director_skips_a_provider_with_no_key(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LEADS_DB_PATH", str(tmp_path / "leads.sqlite3"))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_MODEL", "some-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-be-used")
    import importlib
    import db as db_module
    import agents.director_agent as director

    importlib.reload(db_module)
    importlib.reload(director)
    db_module.init_db()
    director.DirectorAgent(stage="lead_gen").run()
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert "::warning::" in blob
    assert "LLM_API_KEY" in blob
    assert "should-not-be-used" not in blob

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    director.DirectorAgent(stage="lead_gen").run()
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert "ANTHROPIC_API_KEY" in blob
    assert "::warning::" in blob
