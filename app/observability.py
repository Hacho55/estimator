"""Observabilidad. Este módulo no estima nada.

Dos piezas, separadas más abajo:

- Logs JSON y el identificador de petición. Siempre activos. Salen por stdout.
- Trazas de Langfuse. Solo si hay claves. Sin claves, cada función de traza
  vuelve al momento y el estimador sigue igual.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.config import APP_VERSION, get_settings

# --- Logs. No van a Langfuse. ---

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
logger = logging.getLogger("estimador")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        extra = getattr(record, "event", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False, default=str)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = request_id
        return response


def setup_observability(app) -> None:
    settings = get_settings()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if settings.LOG_FORMAT == "json" else logging.Formatter("%(message)s"))
    logger.handlers = [handler]
    logger.setLevel(settings.LOG_LEVEL)
    logger.propagate = False
    app.add_middleware(RequestIdMiddleware)
    configure_call_logs()
    configure_langfuse()


def log_event(event: str, **fields) -> None:
    """Una línea JSON en stdout. No abre una traza."""
    logger.info(event, extra={"event": {"event": event, **fields}})


def _bind_request_id(_logger, _method, event_dict):
    event_dict["request_id"] = request_id_var.get()
    return event_dict


def configure_call_logs() -> None:
    """Sesión 3 · logs: consola legible o JSON, con el mismo interruptor de siempre."""
    settings = get_settings()
    renderer = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if settings.LOG_FORMAT == "json"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _bind_request_id,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, settings.LOG_LEVEL)),
        cache_logger_on_first_use=False,
    )


def _call_logger():
    return structlog.get_logger("estimador.llm")


def log_llm_started(*, model: str, provider: str, tokens_in: int | None, cache_hit: bool, attempt: int = 1) -> None:
    """Sesión 3 · logs: una línea al empezar la llamada, también si sale de caché."""
    _call_logger().info(
        "llm_call_started",
        model=model,
        provider=provider,
        tokens_in=tokens_in,
        cache_hit=cache_hit,
        attempt=attempt,
    )


def log_llm_completed(
    *,
    model: str,
    provider: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: float,
    cost_usd: float | None,
    finish_reason: str | None,
    fallback_used: bool,
    cache_hit: bool,
) -> None:
    """Sesión 3 · logs: una línea al terminar, con el modelo que contestó."""
    _call_logger().info(
        "llm_call_completed",
        model=model,
        provider=provider,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=round(latency_ms, 2),
        cost_usd=cost_usd,
        finish_reason=finish_reason,
        fallback_used=fallback_used,
        cache_hit=cache_hit,
    )


def log_llm_failed(
    *,
    model: str,
    provider: str,
    error_type: str,
    will_fallback: bool,
    attempt: int,
    latency_ms: float,
) -> None:
    """Sesión 3 · logs: una línea si la llamada falla. El reintento del SDK no se cuenta."""
    _call_logger().error(
        "llm_call_failed",
        model=model,
        provider=provider,
        error_type=error_type,
        will_fallback=will_fallback,
        attempt=attempt,
        latency_ms=round(latency_ms, 2),
    )


# --- Trazas de Langfuse. Quien llama no tiene que comprobar si hay claves. ---

_langfuse_configured = False


def _mask_trace_value(data):
    """Aplica el mismo filtro de PII que los guardrails a lo que sale hacia Langfuse."""
    from app.services.guardrails import redact_pii

    if isinstance(data, str):
        return redact_pii(data)[0]
    if isinstance(data, dict):
        return {key: _mask_trace_value(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_mask_trace_value(item) for item in data]
    if isinstance(data, tuple):
        return tuple(_mask_trace_value(item) for item in data)
    return data


def _mask_langfuse(*, data, **_kwargs):
    return _mask_trace_value(data)


def _mask_otel_spans(*, params):
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches = {}
    for identifier, span in params.spans.items():
        replacements = {}
        for key, value in span.attributes.items():
            if not isinstance(value, str):
                continue
            masked = _mask_trace_value(value)
            if masked != value:
                replacements[key] = masked
        if replacements:
            patches[identifier] = OtelSpanPatch(set_attributes=replacements)
    return MaskOtelSpansResult(span_patches=patches) if patches else None


def configure_langfuse(settings=None) -> None:
    """Crea el cliente con máscaras de PII. Idempotente; hay que llamarlo antes del wrapper OpenAI."""
    global _langfuse_configured
    if _langfuse_configured:
        return
    settings = settings or get_settings()
    if not settings.langfuse_enabled:
        return
    from langfuse import Langfuse

    Langfuse(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY.get_secret_value(),
        base_url=settings.LANGFUSE_BASE_URL,
        environment=settings.APP_ENV,
        mask=_mask_langfuse,
        mask_otel_spans=_mask_otel_spans,
    )
    _langfuse_configured = True


@contextmanager
def traced_span(name: str, *, as_type: str = "span"):
    """Un paso dentro de la traza raíz (build-context, build-prompt, postprocess)."""
    settings = get_settings()
    if not settings.langfuse_enabled:
        yield None
        return
    configure_langfuse()
    from langfuse import get_client

    with get_client().start_as_current_observation(as_type=as_type, name=name) as observation:
        yield observation


@contextmanager
def estimation_trace(
    session_id: str | None,
    tags: list[str],
    metadata: dict,
    *,
    transcription: str,
):
    """Traza raíz de un turno. Los atributos se propagan a la raíz y a cada hija.

    En Langfuse v4 el coste y la sesión se agregan por observación, así que
    session_id, entorno y versión tienen que estar puestos antes de crear las
    generaciones. La entrada y la salida de la traza salen de esta observación.
    """
    settings = get_settings()
    if not settings.langfuse_enabled:
        yield None
        return
    configure_langfuse()
    from langfuse import get_client, propagate_attributes

    request_id = request_id_var.get()
    propagated_metadata = dict(metadata)
    if request_id != "-":
        propagated_metadata["request_id"] = request_id
    propagated: dict = {
        "tags": tags,
        "metadata": propagated_metadata,
        "version": APP_VERSION,
        "environment": settings.APP_ENV,
        "trace_name": "generate-estimation",
    }
    if session_id:
        propagated["session_id"] = session_id
    with get_client().start_as_current_observation(
        as_type="span",
        name="generate-estimation",
        input=transcription,
    ) as observation:
        with propagate_attributes(**propagated):
            yield observation


def set_trace_io(*, input_text: str, output_text: str, metadata: dict) -> None:
    """Entrada y salida que Langfuse muestra en la traza. No cambia la respuesta HTTP."""
    if not get_settings().langfuse_enabled:
        return
    from langfuse import get_client

    get_client().update_current_span(input=input_text, output=output_text, metadata=metadata)


def current_trace_id() -> str | None:
    """Identificador para devolverlo en la respuesta. None si Langfuse está apagado."""
    if not get_settings().langfuse_enabled:
        return None
    from langfuse import get_client

    return get_client().get_current_trace_id()


def record_scores(scores: dict[str, float]) -> None:
    """Cuelga las puntuaciones de la observación raíz. No las mete en la estimación."""
    if not get_settings().langfuse_enabled:
        return
    from langfuse import get_client

    client = get_client()
    for name, value in scores.items():
        client.score_current_span(name=name, value=float(value), data_type="NUMERIC")


def flush_langfuse() -> None:
    """Envía las trazas pendientes. Se llama al apagar el proceso."""
    if not get_settings().langfuse_enabled:
        return
    from langfuse import get_client

    get_client().flush()
