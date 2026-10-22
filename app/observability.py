"""Logs JSON, identificador de petición y trazas de Langfuse si hay claves."""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.config import APP_VERSION, get_settings

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


def log_event(event: str, **fields) -> None:
    logger.info(event, extra={"event": {"event": event, **fields}})


@contextmanager
def traced_span(name: str, *, as_type: str = "span"):
    settings = get_settings()
    if not settings.langfuse_enabled:
        yield None
        return
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
    if not get_settings().langfuse_enabled:
        return
    from langfuse import get_client

    get_client().update_current_span(input=input_text, output=output_text, metadata=metadata)


def current_trace_id() -> str | None:
    if not get_settings().langfuse_enabled:
        return None
    from langfuse import get_client

    return get_client().get_current_trace_id()


def record_scores(scores: dict[str, float]) -> None:
    if not get_settings().langfuse_enabled:
        return
    from langfuse import get_client

    client = get_client()
    for name, value in scores.items():
        client.score_current_span(name=name, value=float(value), data_type="NUMERIC")


def flush_langfuse() -> None:
    if not get_settings().langfuse_enabled:
        return
    from langfuse import get_client

    get_client().flush()
