from types import SimpleNamespace

import pytest

from portfolio.config import DEFAULT_GROQ_MODEL
from portfolio.groq_client import invoke_structured_groq


class _ModelNotFound(Exception):
    status_code = 404
    body = {"error": {"code": "model_not_found"}}


def _settings(model: str):
    return SimpleNamespace(groq_api_key="secret", groq_model=model)


def test_retired_model_is_migrated_before_request(monkeypatch) -> None:
    requested = []

    class FakeChatGroq:
        def __init__(self, **options):
            requested.append(options["model"])

        def with_structured_output(self, *_args, **_kwargs):
            return self

        def invoke(self, _messages):
            return {"status": "ok"}

    monkeypatch.setattr("portfolio.groq_client._chat_groq_class", lambda: FakeChatGroq)

    result = invoke_structured_groq(
        _settings("llama-3.3-70b-versatile"),
        {"type": "object"},
        [("human", "test")],
    )

    assert result == {"status": "ok"}
    assert requested == [DEFAULT_GROQ_MODEL]


def test_missing_custom_model_falls_back_to_current_default(monkeypatch) -> None:
    requested = []

    class FakeChatGroq:
        def __init__(self, **options):
            self.model = options["model"]
            requested.append(self.model)

        def with_structured_output(self, *_args, **_kwargs):
            return self

        def invoke(self, _messages):
            if self.model == "removed-custom-model":
                raise _ModelNotFound("model does not exist")
            return {"model": self.model}

    monkeypatch.setattr("portfolio.groq_client._chat_groq_class", lambda: FakeChatGroq)

    result = invoke_structured_groq(
        _settings("removed-custom-model"),
        {"type": "object"},
        [("human", "test")],
    )

    assert result == {"model": DEFAULT_GROQ_MODEL}
    assert requested == ["removed-custom-model", DEFAULT_GROQ_MODEL]


def test_non_model_error_is_not_hidden_by_fallback(monkeypatch) -> None:
    requested = []

    class FakeChatGroq:
        def __init__(self, **options):
            requested.append(options["model"])

        def with_structured_output(self, *_args, **_kwargs):
            return self

        def invoke(self, _messages):
            raise RuntimeError("rate limit exceeded")

    monkeypatch.setattr("portfolio.groq_client._chat_groq_class", lambda: FakeChatGroq)

    with pytest.raises(RuntimeError, match="rate limit exceeded"):
        invoke_structured_groq(
            _settings("configured-model"),
            {"type": "object"},
            [("human", "test")],
        )

    assert requested == ["configured-model"]


def test_client_uses_strict_json_schema_without_rewriting_messages(monkeypatch) -> None:
    structured_calls = []
    received = []

    class FakeChatGroq:
        def __init__(self, **_options):
            pass

        def with_structured_output(self, schema, **options):
            structured_calls.append((schema, options))
            return self

        def invoke(self, messages):
            received.append(messages)
            return {"status": "ok"}

    monkeypatch.setattr("portfolio.groq_client._chat_groq_class", lambda: FakeChatGroq)

    schema = {"title": "Result", "type": "object"}
    messages = [("system", "Follow the supplied schema."), ("human", "test")]
    invoke_structured_groq(
        _settings(DEFAULT_GROQ_MODEL),
        schema,
        messages,
    )

    assert structured_calls == [
        (
            schema,
            {"method": "json_schema", "include_raw": False, "strict": True},
        )
    ]
    assert received == [messages]


def test_client_can_use_json_object_mode_without_strict_schema(monkeypatch) -> None:
    structured_calls = []

    class FakeChatGroq:
        def __init__(self, **_options):
            pass

        def with_structured_output(self, schema, **options):
            structured_calls.append((schema, options))
            return self

        def invoke(self, _messages):
            return {"status": "ok"}

    monkeypatch.setattr("portfolio.groq_client._chat_groq_class", lambda: FakeChatGroq)

    schema = {"title": "Result", "type": "object"}
    result = invoke_structured_groq(
        _settings(DEFAULT_GROQ_MODEL),
        schema,
        [("system", "Return JSON."), ("human", "test")],
        method="json_mode",
    )

    assert result == {"status": "ok"}
    assert structured_calls == [
        (schema, {"method": "json_mode", "include_raw": False})
    ]


def test_client_retries_once_after_short_token_rate_limit(monkeypatch) -> None:
    attempts = []
    sleeps = []

    class TokenRateLimit(Exception):
        status_code = 429
        body = {
            "error": {
                "code": "rate_limit_exceeded",
                "message": "Please try again in 1.5s.",
            }
        }

    class FakeChatGroq:
        def __init__(self, **_options):
            pass

        def with_structured_output(self, *_args, **_kwargs):
            return self

        def invoke(self, _messages):
            attempts.append(True)
            if len(attempts) == 1:
                raise TokenRateLimit("rate limited")
            return {"status": "ok"}

    monkeypatch.setattr("portfolio.groq_client._chat_groq_class", lambda: FakeChatGroq)
    monkeypatch.setattr("portfolio.groq_client.time.sleep", sleeps.append)

    result = invoke_structured_groq(
        _settings(DEFAULT_GROQ_MODEL),
        {"type": "object"},
        [("human", "test")],
        rate_limit_retries=1,
    )

    assert result == {"status": "ok"}
    assert len(attempts) == 2
    assert sleeps == [1.75]
