"""Transporte genérico hacia LiteLLM. No sabe nada de estimaciones.

Un solo cliente, creado al importar el módulo. Si falta la clave, la aplicación
no arranca. Quien necesite embeddings más adelante reutiliza este cliente.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from openai import APIConnectionError, APIError, APITimeoutError

from app.config import get_settings
from app.services.errors import LLMTimeoutError, LLMUnavailableError

_settings = get_settings()

# Observabilidad: con claves, este cliente es el de Langfuse y abre la generación
# él solo a partir de name=. La llamada sigue siendo chat.completions. Sin claves,
# es el cliente de openai y no hay traza.
if _settings.langfuse_enabled:
    from langfuse.openai import AsyncOpenAI
else:
    from openai import AsyncOpenAI


def _build_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.LITELLM_API_KEY.get_secret_value(),
        base_url=settings.LITELLM_BASE_URL,
        timeout=settings.LLM_TIMEOUT_SECONDS,
        max_retries=settings.LLM_MAX_RETRIES,
    )


client = _build_client()


def reset_client() -> None:
    """Nuevo cliente HTTP. El anterior queda atado al bucle de asyncio que ya se cerró."""
    global client
    client = _build_client()


@dataclass
class LLMResult:
    content: str
    model: str
    finish_reason: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_tokens: int
    cache_creation_tokens: int | None
    cost_usd: float | None
    latency_ms: float


def _usage_details(usage: object) -> tuple[int, int, int, int, int | None]:
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", 0) or input_tokens + output_tokens)
    details = getattr(usage, "prompt_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0) if details is not None else 0
    created = None
    if details is not None:
        created = getattr(details, "cache_creation_tokens", None)
        if created is None:
            created = getattr(details, "cache_creation_input_tokens", None)
        if created is not None:
            created = int(created)
    return input_tokens, output_tokens, total_tokens, cached, created


def _cost_from_header(headers: object) -> float | None:
    raw = None
    if hasattr(headers, "get"):
        raw = headers.get("x-litellm-response-cost")
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


async def complete(
    messages: list[dict],
    response_format: dict,
    *,
    name: str,
    metadata: dict | None = None,
    model: str | None = None,
) -> LLMResult:
    settings = get_settings()
    payload: dict = {
        "model": model or settings.LLM_MODEL,
        "messages": _public_messages(messages),
        "temperature": settings.LLM_TEMPERATURE,
        "max_tokens": settings.LLM_MAX_TOKENS,
        "response_format": response_format,
    }
    # Observabilidad: name y metadata solo los lee el wrapper de Langfuse.
    if settings.langfuse_enabled:
        payload["name"] = name
        payload["metadata"] = metadata or {}

    started = time.perf_counter()
    try:
        raw = await client.chat.completions.with_raw_response.create(**payload)
    except APITimeoutError as exc:
        raise LLMTimeoutError(f"El modelo no respondió a tiempo: {exc}") from exc
    except (APIError, APIConnectionError) as exc:
        raise LLMUnavailableError(f"LiteLLM no pudo completar la llamada: {exc}") from exc

    latency_ms = (time.perf_counter() - started) * 1000
    parsed = raw.parse()
    choice = parsed.choices[0]
    content = choice.message.content or ""
    usage = parsed.usage
    input_tokens, output_tokens, total_tokens, cached, created = _usage_details(usage)
    return LLMResult(
        content=content,
        model=parsed.model or payload["model"],
        finish_reason=choice.finish_reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached,
        cache_creation_tokens=created,
        cost_usd=_cost_from_header(raw.headers),
        latency_ms=latency_ms,
    )


def _public_messages(messages: list[dict]) -> list[dict]:
    """Quita claves internas (segments) antes de enviarlas al proveedor."""
    cleaned = []
    for message in messages:
        cleaned.append({key: value for key, value in message.items() if key in {"role", "content"}})
    return cleaned
