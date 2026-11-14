"""Sesiones multi-turno. El historial y los hechos del proyecto viven separados."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, Form, UploadFile

from app.attachments.extractor import attachment_label, extract_text, join_attachments, read_up_to
from app.config import get_settings
from app.observability import traced_span
from app.schemas.conversation import ChatMessage
from app.schemas.estimation import DetailLevel, ErrorResponse, OutputFormat, ProjectType, UsageInfo
from app.schemas.session import SessionCreated, SessionEstimateResponse
from app.services.errors import AttachmentRejectedError, AttachmentTooLargeError, InputRejectedError
from app.services.llm_service import ContextOverrides, generate_estimation
from app.services.metadata_extractor import extract_project_metadata
from app.state.sessions import get_session_store

router = APIRouter(prefix="/sessions", tags=["sessions"])

SESSION_PROMPT_VERSION = "v3"

_ERRORS = {
    404: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}


@router.post("", operation_id="create_session", response_model=SessionCreated)
async def create_session() -> SessionCreated:
    session = get_session_store().create()
    return SessionCreated(session_id=session.id)


@router.post(
    "/{session_id}/estimate",
    operation_id="estimate_session",
    response_model=SessionEstimateResponse,
    responses=_ERRORS,
)
async def estimate_session(
    session_id: str,
    transcript: str = Form(),
    project_type: str | None = Form(default=None),
    detail_level: str | None = Form(default=None),
    output_format: str | None = Form(default=None),
    attachments: list[UploadFile] | None = File(default=None),
) -> SessionEstimateResponse:
    store = get_session_store()
    session = store.get(session_id)
    async with session.lock:
        text = _bounded_transcript(transcript, first_turn=session.history.turn_count() == 0)
        extracted = await _read_attachments(attachments or [])
        combined = join_attachments(text, extracted)
        _remember_form(session, project_type, detail_level, output_format)
        listed = session.history.to_messages_list(session.metadata.as_prompt_block())
        history = [
            ChatMessage(role=item["role"], content=item["content"], kind=item.get("kind"))
            for item in listed
            if item["role"] != "system"
        ]
        result = await generate_estimation(
            combined,
            history,
            overrides=ContextOverrides(
                prompt_version=SESSION_PROMPT_VERSION,
                form_detail_level=session.form_detail_level,
                output_format=session.output_format,
                project_type=session.project_type,
                project_metadata=session.metadata.as_prompt_block(),
                session_window=True,
            ),
            session_id=session.id,
        )
        summary = _summary(result)
        with traced_span("extract-metadata"):
            extraction = await extract_project_metadata(
                previous=session.metadata,
                turn=result.scrubbed_input or combined,
                summary=summary,
            )
        session.metadata = extraction.metadata
        session.history.append(
            result.scrubbed_input or combined,
            result.history_content,
            result.kind,
        )
        store.touch(session)
        return _response(session.id, extraction, result)


def _bounded_transcript(transcript: str, *, first_turn: bool) -> str:
    text = transcript.strip()
    settings = get_settings()
    minimum = 50 if first_turn else 1
    if len(text) < minimum:
        raise InputRejectedError(
            "La transcripción necesita al menos 50 caracteres."
            if first_turn
            else "El mensaje está vacío."
        )
    if len(text) > settings.TRANSCRIPTION_MAX_CHARS:
        raise InputRejectedError("La transcripción supera el largo máximo.")
    return text


def _remember_form(session, project_type: str | None, detail_level: str | None, output_format: str | None) -> None:
    if project_type:
        session.project_type = _choice(ProjectType, project_type)
    elif session.project_type is None:
        raise InputRejectedError("Falta project_type.")
    if detail_level:
        session.form_detail_level = _choice(DetailLevel, detail_level)
    elif session.form_detail_level is None:
        session.form_detail_level = DetailLevel.MEDIUM
    if output_format:
        session.output_format = _choice(OutputFormat, output_format)
    elif session.output_format is None:
        session.output_format = OutputFormat.PHASES_TABLE


def _choice(enum_cls, value: str):
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise InputRejectedError(f"Valor no admitido: {value}.") from exc


async def _read_attachments(uploads: list[UploadFile]) -> list[tuple[str, str]]:
    settings = get_settings()
    present = [upload for upload in uploads if upload.filename]
    if len(present) > settings.ATTACHMENT_MAX_FILES:
        raise AttachmentRejectedError("Demasiados adjuntos.")
    extracted: list[tuple[str, str]] = []
    total_chars = 0
    for upload in present:
        data = await read_up_to(upload, settings.ATTACHMENT_MAX_BYTES)
        name = attachment_label(upload.filename or "adjunto")
        # pypdf y python-docx son CPU: fuera del event loop del único worker.
        text = await asyncio.to_thread(extract_text, name, data)
        total_chars += len(text)
        if total_chars > settings.TRANSCRIPTION_MAX_CHARS:
            raise AttachmentTooLargeError("El texto de los adjuntos supera el largo máximo.")
        extracted.append((name, text))
    return extracted


def _summary(result) -> str:
    estimation = result.estimation
    if estimation is None:
        return result.message or ""
    title = getattr(estimation, "project_title", "") or ""
    summary = getattr(estimation, "summary", "") or ""
    return f"{title}. {summary}".strip()


def _response(session_id: str, extraction, result) -> SessionEstimateResponse:
    estimation = None if result.kind == "answer" else result.estimation
    usage = _plus_usage(result.usage, extraction.usage)
    cost = _plus_cost(result.cost_usd, extraction.cost_usd)
    return SessionEstimateResponse(
        session_id=session_id,
        project_metadata=extraction.metadata,
        kind=result.kind,
        message=result.message,
        estimation=result.estimation_markdown,
        estimation_structured=estimation,
        warnings=result.warnings,
        model=result.model,
        provider=result.provider,
        prompt_version=result.prompt_version,
        usage=usage,
        cost_usd=cost,
        latency_ms=result.latency_ms + extraction.latency_ms,
        cache_hit=result.cache_hit and extraction.usage is None,
        cache_kind=result.cache_kind,
        cache_similarity=result.cache_similarity,
        trace_id=result.trace_id,
    )


def _plus_usage(left: UsageInfo, right: UsageInfo | None) -> UsageInfo:
    if right is None:
        return left
    created = (left.cache_creation_tokens or 0) + (right.cache_creation_tokens or 0)
    return UsageInfo(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
        cached_tokens=left.cached_tokens + right.cached_tokens,
        cache_creation_tokens=created or None,
    )


def _plus_cost(left: float | None, right: float | None) -> float | None:
    if left is None and right is None:
        return None
    return (left or 0) + (right or 0)
