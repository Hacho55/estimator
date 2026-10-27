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


def _fallback_name(name: str) -> str:
    """Sesión 3 · fallback: Langfuse separa la llamada al modelo de reserva."""
    if name.endswith("-fallback"):
        return name
    return f"{name}-fallback"


def _error_text(exc: Exception) -> str:
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body is not None:
        parts.append(str(body))
    return " ".join(parts).lower()


def _fallback_reason(exc: Exception) -> str | None:
    """Sesión 3 · fallback: el tipo de error decide si hay un segundo modelo.

    Timeout, corte de conexión, 429 y 5xx pasan. Un 400 pasa solo si el mensaje
    dice que ese modelo rechazó el formato. 401, 403 y el resto de 400 no pasan:
    cambiar de modelo no los arregla.
    """
    if isinstance(exc, APITimeoutError):
        return "timeout"
    if isinstance(exc, APIConnectionError):
        return "connection"
    status = getattr(exc, "status_code", None)
    if status == 429:
        return "rate_limit"
    if isinstance(status, int) and status >= 500:
        return "server_error"
    if status == 400:
        text = _error_text(exc)
        markers = ("response_format", "json_schema", "formato de respuesta", "response format")
        if any(marker in text for marker in markers):
            return "response_format"
    return None


def _as_service_error(exc: Exception) -> LLMTimeoutError | LLMUnavailableError:
    if isinstance(exc, APITimeoutError):
        return LLMTimeoutError(f"El modelo no respondió a tiempo: {exc}")
    return LLMUnavailableError(f"LiteLLM no pudo completar la llamada: {exc}")


async def _request(
    messages: list[dict],
    response_format: dict,
    *,
    name: str,
    metadata: dict | None,
    model: str,
) -> LLMResult:
    settings = get_settings()
    payload: dict = {
        "model": model,
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
    raw = await client.chat.completions.with_raw_response.create(**payload)
    latency_ms = (time.perf_counter() - started) * 1000
    parsed = raw.parse()
    choice = parsed.choices[0]
    content = choice.message.content or ""
    usage = parsed.usage
    input_tokens, output_tokens, total_tokens, cached, created = _usage_details(usage)
    return LLMResult(
        content=content,
        model=parsed.model or model,
        finish_reason=choice.finish_reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached,
        cache_creation_tokens=created,
        cost_usd=_cost_from_header(raw.headers),
        latency_ms=latency_ms,
    )


class TokenStream:
    """Sesión 3 · streaming: trozos con la forma de OpenAI, vía LiteLLM.

    Si falla antes del primer token, vale un solo intento con el modelo de reserva.
    Si ya salió texto, no se cambia de modelo a mitad de frase. El resultado queda
    en `result` cuando el generador termina.
    """

    def __init__(
        self,
        messages: list[dict],
        response_format: dict,
        *,
        name: str,
        metadata: dict | None = None,
        model: str | None = None,
    ) -> None:
        self.messages = messages
        self.response_format = response_format
        self.name = name
        self.metadata = metadata
        self.model = model
        self.result: LLMResult | None = None

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        settings = get_settings()
        chosen = self.model or settings.LLM_MODEL
        emitted = False
        try:
            async for piece in self._consume(chosen, self.name, self.metadata):
                emitted = True
                yield piece
            return
        except (APITimeoutError, APIConnectionError, APIError) as exc:
            if emitted:
                raise _as_service_error(exc) from exc
            reserve = settings.LLM_FALLBACK_MODEL.strip()
            reason = _fallback_reason(exc)
            if not (reason and reserve and reserve != chosen):
                raise _as_service_error(exc) from exc
            fallback_metadata = {**(self.metadata or {}), "fallback": True, "error_type": reason}
            try:
                async for piece in self._consume(reserve, _fallback_name(self.name), fallback_metadata):
                    yield piece
            except (APITimeoutError, APIConnectionError, APIError) as second:
                raise _as_service_error(second) from second

    async def _consume(self, model: str, name: str, metadata: dict | None):
        settings = get_settings()
        payload: dict = {
            "model": model,
            "messages": _public_messages(self.messages),
            "temperature": settings.LLM_TEMPERATURE,
            "max_tokens": settings.LLM_MAX_TOKENS,
            "response_format": self.response_format,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if settings.langfuse_enabled:
            payload["name"] = name
            payload["metadata"] = metadata or {}

        started = time.perf_counter()
        stream = await client.chat.completions.create(**payload)
        parts: list[str] = []
        finish_reason = None
        usage = None
        answered = model
        async for chunk in stream:
            chunk_model = getattr(chunk, "model", None)
            if chunk_model:
                answered = chunk_model
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = chunk_usage
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = getattr(choice, "delta", None)
            text = getattr(delta, "content", None) or ""
            if text:
                parts.append(text)
                yield text

        input_tokens, output_tokens, total_tokens, cached, created = _usage_details(usage)
        self.result = LLMResult(
            content="".join(parts),
            model=answered,
            finish_reason=finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached,
            cache_creation_tokens=created,
            cost_usd=None,
            latency_ms=(time.perf_counter() - started) * 1000,
        )


async def complete(
    messages: list[dict],
    response_format: dict,
    *,
    name: str,
    metadata: dict | None = None,
    model: str | None = None,
) -> LLMResult:
    settings = get_settings()
    chosen = model or settings.LLM_MODEL
    try:
        return await _request(messages, response_format, name=name, metadata=metadata, model=chosen)
    except (APITimeoutError, APIConnectionError, APIError) as exc:
        reserve = settings.LLM_FALLBACK_MODEL.strip()
        reason = _fallback_reason(exc)
        # Sesión 3 · fallback: un solo intento, y solo si el reserva es otro modelo.
        if reason and reserve and reserve != chosen:
            fallback_metadata = {**(metadata or {}), "fallback": True, "error_type": reason}
            try:
                return await _request(
                    messages,
                    response_format,
                    name=_fallback_name(name),
                    metadata=fallback_metadata,
                    model=reserve,
                )
            except (APITimeoutError, APIConnectionError, APIError) as second:
                raise _as_service_error(second) from second
        raise _as_service_error(exc) from exc


def _public_messages(messages: list[dict]) -> list[dict]:
    """Quita claves internas (segments) antes de enviarlas al proveedor."""
    cleaned = []
    for message in messages:
        cleaned.append({key: value for key, value in message.items() if key in {"role", "content"}})
    return cleaned
