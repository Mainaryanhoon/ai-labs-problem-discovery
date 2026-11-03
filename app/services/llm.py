"""OpenAI-compatible LiteLLM client for Tatvic analysis."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

MAX_REQUEST_ATTEMPTS = 3
RETRYABLE_HTTP_STATUSES = {408, 500, 502, 503, 504}


class LLMNotConfigured(RuntimeError):
    pass


class LLMAnalysisError(RuntimeError):
    pass


class LLMRateLimitError(LLMAnalysisError):
    pass


def llm_configured() -> bool:
    settings = get_settings()
    return bool(settings.litellm_api_key.strip() and settings.litellm_base_url.strip())


def _base_url() -> str:
    settings = get_settings()
    return settings.litellm_base_url.rstrip("/")


def chat_completion_json(
    *,
    system: str,
    user: str,
    temperature: float = 0.1,
) -> str:
    """Call LiteLLM chat completions and return assistant text."""
    settings = get_settings()
    if not llm_configured():
        raise LLMNotConfigured(
            "LITELLM_API_KEY or LITELLM_BASE_URL is missing. "
            "Add them to .env and restart the API."
        )

    url = f"{_base_url()}/chat/completions"
    payload = {
        "model": settings.litellm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": settings.litellm_max_tokens,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {settings.litellm_api_key}",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(url, headers=headers, json=payload)
            if response.status_code == 429:
                raise LLMRateLimitError("LiteLLM rate limit or quota exceeded.")
            if response.status_code in RETRYABLE_HTTP_STATUSES:
                raise httpx.HTTPStatusError(
                    f"HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
            if response.status_code >= 400:
                raise LLMAnalysisError(
                    f"LiteLLM rejected the request (HTTP {response.status_code})."
                )
            data = response.json()
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                raise LLMAnalysisError("LiteLLM returned no choices.")
            message = choices[0].get("message") or {}
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise LLMAnalysisError("LiteLLM returned an empty response.")
            return content.strip()
        except LLMRateLimitError:
            raise
        except LLMAnalysisError:
            raise
        except (
            httpx.TimeoutException,
            httpx.TransportError,
            httpx.HTTPStatusError,
            TimeoutError,
            ConnectionError,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
            if attempt == MAX_REQUEST_ATTEMPTS - 1:
                break
            delay = 0.5 * (2 ** attempt)
            logger.warning(
                "LiteLLM transient error type=%s; retrying in %.1fs (%s/%s).",
                type(exc).__name__,
                delay,
                attempt + 1,
                MAX_REQUEST_ATTEMPTS,
            )
            time.sleep(delay)

    raise LLMAnalysisError(
        f"LiteLLM request failed after {MAX_REQUEST_ATTEMPTS} attempts "
        f"({type(last_error).__name__ if last_error else 'unknown'})."
    )


def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Embeddings via LiteLLM when configured; else deterministic local vectors."""
    settings = get_settings()
    model = settings.litellm_embedding_model.strip()
    if llm_configured() and model:
        url = f"{_base_url()}/embeddings"
        headers = {
            "Authorization": f"Bearer {settings.litellm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": model, "input": texts}
        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise LLMAnalysisError(
                f"LiteLLM embeddings failed (HTTP {response.status_code})."
            )
        data = response.json()
        items = data.get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            raise LLMAnalysisError("LiteLLM returned incomplete embeddings.")
        vectors = []
        for item in sorted(items, key=lambda row: row.get("index", 0)):
            embedding = item.get("embedding")
            if not isinstance(embedding, list) or not embedding:
                raise LLMAnalysisError("LiteLLM returned an empty embedding.")
            vectors.append([float(v) for v in embedding])
        return vectors
    return [_local_embedding(text) for text in texts]


def _local_embedding(text: str, dims: int = 64) -> list[float]:
    """Stable bag-of-tokens hash embedding for offline / test clustering."""
    vector = [0.0] * dims
    tokens = [t for t in "".join(
        ch.lower() if ch.isalnum() else " " for ch in (text or "")
    ).split() if t]
    if not tokens:
        tokens = ["empty"]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = digest[0] % dims
        sign = 1.0 if digest[1] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerating markdown fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise LLMAnalysisError("Model response was not valid JSON.")
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise LLMAnalysisError("Model JSON root must be an object.")
    return parsed
