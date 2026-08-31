"""Shared, bounded Groq structured-output invocation."""

from __future__ import annotations

import re
import time
from typing import Any, Literal, Sequence

from .config import DEFAULT_GROQ_MODEL, normalize_groq_model


GROQ_FALLBACK_MODELS = (
    DEFAULT_GROQ_MODEL,
    "openai/gpt-oss-120b",
)
GROQ_REASONING_MODEL = "openai/gpt-oss-120b"
GROQ_FAST_MODEL = "openai/gpt-oss-20b"


class GroqModelUnavailableError(RuntimeError):
    """No configured production text model was available to the account."""


def _chat_groq_class():
    from langchain_groq import ChatGroq

    return ChatGroq


def _model_candidates(
    preferred: str | None,
    configured: str | None = None,
) -> tuple[str, ...]:
    candidates = [
        normalize_groq_model(preferred),
        normalize_groq_model(configured),
        *GROQ_FALLBACK_MODELS,
    ]
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


def _rate_limit_retry_after(error: Exception) -> float | None:
    """Return a short provider-requested delay for token rate limits."""
    status_code = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    details = body.get("error", body) if isinstance(body, dict) else None
    code = details.get("code") if isinstance(details, dict) else None
    if status_code != 429 and code != "rate_limit_exceeded":
        return None
    message = str(details.get("message") if isinstance(details, dict) else error)
    match = re.search(
        r"try again in\s+([0-9]+(?:\.[0-9]+)?)(ms|s)\b",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    delay = float(match.group(1))
    if match.group(2).casefold() == "ms":
        delay /= 1_000
    return delay if 0 < delay <= 30 else None


def invoke_structured_groq(
    settings: Any,
    schema: dict[str, Any],
    messages: Sequence[tuple[str, str]],
    *,
    max_retries: int = 0,
    max_tokens: int | None = None,
    method: Literal["json_schema", "json_mode"] = "json_schema",
    rate_limit_retries: int = 0,
    preferred_model: str | None = None,
) -> Any:
    """Invoke one approved schema, retrying only an unavailable model ID."""
    if not getattr(settings, "groq_api_key", None):
        raise ValueError("GROQ_API_KEY is not configured.")

    configured_model = getattr(settings, "groq_model", None)
    candidates = _model_candidates(
        preferred_model or configured_model,
        configured_model if preferred_model else None,
    )
    unavailable: list[str] = []
    chat_groq = _chat_groq_class()
    for model_name in candidates:
        options: dict[str, Any] = {
            "model": model_name,
            "temperature": 0,
            "max_retries": max_retries,
            "api_key": settings.groq_api_key,
        }
        if model_name.startswith("openai/gpt-oss-"):
            options["reasoning_format"] = "hidden"
            options["reasoning_effort"] = "low"
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        structured_options: dict[str, Any] = {
            "method": method,
            "include_raw": False,
        }
        if method == "json_schema":
            structured_options["strict"] = True
        retries_remaining = max(0, int(rate_limit_retries))
        while True:
            try:
                model = chat_groq(**options).with_structured_output(
                    schema,
                    **structured_options,
                )
                return model.invoke(list(messages))
            except Exception as exc:
                delay = _rate_limit_retry_after(exc)
                if retries_remaining and delay is not None:
                    retries_remaining -= 1
                    time.sleep(delay + 0.25)
                    continue
                if not _is_model_unavailable(exc):
                    raise
                unavailable.append(model_name)
                break

    raise GroqModelUnavailableError(
        "No supported Groq text model is available to this account. "
        f"Tried: {', '.join(unavailable)}. Set GROQ_MODEL to an active text model "
        "listed in the Groq console."
    )


def invoke_text_groq(
    settings: Any,
    messages: Sequence[tuple[str, str]],
    *,
    max_retries: int = 0,
    max_tokens: int | None = None,
    rate_limit_retries: int = 0,
    preferred_model: str | None = None,
) -> str:
    """Invoke Groq without provider-enforced JSON generation."""
    if not getattr(settings, "groq_api_key", None):
        raise ValueError("GROQ_API_KEY is not configured.")

    unavailable: list[str] = []
    chat_groq = _chat_groq_class()
    configured_model = getattr(settings, "groq_model", None)
    candidates = _model_candidates(
        preferred_model or configured_model,
        configured_model if preferred_model else None,
    )
    for model_name in candidates:
        options: dict[str, Any] = {
            "model": model_name,
            "temperature": 0,
            "max_retries": max_retries,
            "api_key": settings.groq_api_key,
        }
        if model_name.startswith("openai/gpt-oss-"):
            options["reasoning_format"] = "hidden"
            options["reasoning_effort"] = "low"
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        retries_remaining = max(0, int(rate_limit_retries))
        while True:
            try:
                response = chat_groq(**options).invoke(list(messages))
                content = getattr(response, "content", response)
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = []
                    for block in content:
                        if isinstance(block, str):
                            parts.append(block)
                        elif isinstance(block, dict) and isinstance(block.get("text"), str):
                            parts.append(block["text"])
                    if parts:
                        return "".join(parts)
                raise RuntimeError("Groq returned an empty text response.")
            except Exception as exc:
                delay = _rate_limit_retry_after(exc)
                if retries_remaining and delay is not None:
                    retries_remaining -= 1
                    time.sleep(delay + 0.25)
                    continue
                if not _is_model_unavailable(exc):
                    raise
                unavailable.append(model_name)
                break

    raise GroqModelUnavailableError(
        "No supported Groq text model is available to this account. "
        f"Tried: {', '.join(unavailable)}. Set GROQ_MODEL to an active text model "
        "listed in the Groq console."
    )
