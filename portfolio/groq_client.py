"""Shared, bounded Groq structured-output invocation."""

from __future__ import annotations

from typing import Any, Sequence

from .config import DEFAULT_GROQ_MODEL, normalize_groq_model


GROQ_FALLBACK_MODELS = (
    DEFAULT_GROQ_MODEL,
    "openai/gpt-oss-120b",
)


class GroqModelUnavailableError(RuntimeError):
    """No configured production text model was available to the account."""


def _chat_groq_class():
    from langchain_groq import ChatGroq

    return ChatGroq


def _model_candidates(preferred: str | None) -> tuple[str, ...]:
    candidates = [normalize_groq_model(preferred), *GROQ_FALLBACK_MODELS]
    return tuple(dict.fromkeys(candidates))


def _is_model_unavailable(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    if status_code == 404:
        return True
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        details = body.get("error", body)
        if isinstance(details, dict) and details.get("code") == "model_not_found":
            return True
    message = str(error).lower()
    return "model_not_found" in message or (
        "model" in message
        and ("does not exist" in message or "do not have access" in message)
    )


def invoke_structured_groq(
    settings: Any,
    schema: dict[str, Any],
    messages: Sequence[tuple[str, str]],
    *,
    max_retries: int = 0,
    max_tokens: int | None = None,
) -> Any:
    """Invoke one approved schema, retrying only an unavailable model ID."""
    if not getattr(settings, "groq_api_key", None):
        raise ValueError("GROQ_API_KEY is not configured.")

    candidates = _model_candidates(getattr(settings, "groq_model", None))
    unavailable: list[str] = []
    chat_groq = _chat_groq_class()
    for model_name in candidates:
        options: dict[str, Any] = {
            "model": model_name,
            "temperature": 0,
            "max_retries": max_retries,
            "api_key": settings.groq_api_key,
        }
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        try:
            model = chat_groq(**options).with_structured_output(
                schema,
                method="json_schema",
                include_raw=False,
                strict=True,
            )
            return model.invoke(list(messages))
        except Exception as exc:
            if not _is_model_unavailable(exc):
                raise
            unavailable.append(model_name)

    raise GroqModelUnavailableError(
        "No supported Groq text model is available to this account. "
        f"Tried: {', '.join(unavailable)}. Set GROQ_MODEL to an active text model "
        "listed in the Groq console."
    )
