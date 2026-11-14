"""Sesiones multi-turno. El historial y los hechos del proyecto viven separados."""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.attachments.extractor import attachment_label, extract_text, join_attachments, read_up_to
from app.config import get_settings
from app.observability import traced_span
from app.schemas.estimation import DetailLevel, ErrorResponse, ProjectType, UsageInfo
from app.schemas.session import MemoryChange, ProjectMetadata, SessionCreated, SessionEstimateResponse
from app.security.caller import CallerContext, get_caller
from app.services.errors import AttachmentRejectedError, AttachmentTooLargeError, InputRejectedError
from app.services.llm_service import ContextOverrides, generate_estimation
from app.services.metadata_extractor import extract_metadata_update
from app.services.prompt_builder import DynamicContext
from app.state.memory import apply_update
from app.state.sessions import get_session_store

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])

SESSION_PROMPT_VERSION = "v4"
MEMORY_LOG_MAX = 200  # Registro de auditoría acotado por sesión.

_ERRORS = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}


@router.post(
    "",
    operation_id="create_session",
    response_model=SessionCreated,
    responses={401: {"model": ErrorResponse}},
)
async def create_session(caller: CallerContext = Depends(get_caller)) -> SessionCreated:
    """Crea una sesión del llamante. Con JWT, solo su ``sub`` podrá usarla."""
    session = get_session_store().create(owner=caller.user_id)
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
    attachments: list[UploadFile] | None = File(default=None),
    caller: CallerContext = Depends(get_caller),
) -> SessionEstimateResponse:
    """Un turno de sesión. El perfil (tier) sale del token del llamante, nunca del formulario."""
    store = get_session_store()
    session = store.get(session_id, owner=caller.user_id)
    async with session.lock:
        text = _bounded_transcript(transcript, first_turn=session.history.turn_count() == 0)
        extracted = await _read_attachments(attachments or [])
        combined = join_attachments(text, extracted)
        _remember_form(session, project_type, detail_level)
        history = list(session.history.messages)
        memory = None if session.metadata.is_empty() else session.metadata.model_dump()
        result = await generate_estimation(
            combined,
            history,
            overrides=ContextOverrides(
                prompt_version=SESSION_PROMPT_VERSION,
                form_detail_level=session.form_detail_level,
                project_type=session.project_type,
                dynamic=DynamicContext(memory=memory),
                session_window=True,
                tier=caller.tier.value,
            ),
            session_id=session.id,
        )
        user_turn = result.scrubbed_input or combined
        with traced_span("extract-metadata"):
            extraction = await extract_metadata_update(
                previous=session.metadata,
                user_turn=user_turn,
                assistant_turn=_assistant_turn(result),
            )
        turn_number = session.turns + 1
        merged = apply_update(session.metadata, extraction.update, turn=turn_number)
        if merged.discarded:
            log.info("memory_update_discarded", session_id=session.id, reasons=merged.discarded)
        session.metadata = merged.metadata
        changes = [*_tier_change(session.last_tier, caller.tier.value, turn_number), *merged.changes]
        session.last_tier = caller.tier.value
        session.memory_log.extend(changes)
        del session.memory_log[:-MEMORY_LOG_MAX]
        session.history.append(user_turn, result.history_content, result.kind)
        session.turns = turn_number
        store.touch(session)
        return _response(session.id, session.metadata, changes, extraction, result)


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


def _remember_form(session, project_type: str | None, detail_level: str | None) -> None:
    """El formulario de la sesión: tipo de proyecto y profundidad. La estructura la fija el tier (D7)."""
    if project_type:
        session.project_type = _choice(ProjectType, project_type)
    elif session.project_type is None:
        raise InputRejectedError("Falta project_type.")
    if detail_level:
        session.form_detail_level = _choice(DetailLevel, detail_level)
    elif session.form_detail_level is None:
        session.form_detail_level = DetailLevel.MEDIUM


def _tier_change(previous: str | None, current: str, turn: int) -> list[MemoryChange]:
    """El cambio de perfil entre turnos queda en la auditoría de la sesión."""
    if previous is None or previous == current:
        return []
    return [MemoryChange(turn=turn, field="tier", before=previous, after=current)]


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


def _assistant_turn(result) -> str:
    """Lo que el extractor ve de la respuesta: mensaje, título y resumen ya validados."""
    parts = [result.message or ""]
    estimation = result.estimation
    if estimation is not None:
        parts.append(getattr(estimation, "project_title", "") or "")
        parts.append(getattr(estimation, "summary", "") or "")
    return "\n".join(part for part in parts if part).strip()


def _response(
    session_id: str,
    memory: ProjectMetadata,
    changes: list[MemoryChange],
    extraction,
    result,
) -> SessionEstimateResponse:
    view = None if result.kind == "answer" else result.view
    usage = _plus_usage(result.usage, extraction.usage)
    cost = _plus_cost(result.cost_usd, extraction.cost_usd)
    return SessionEstimateResponse(
        session_id=session_id,
        project_metadata=memory,
        memory_changes=changes,
        kind=result.kind,
        message=result.message,
        estimation=result.estimation_markdown,
        tier=result.tier,
        estimation_structured=view,
        warnings=result.warnings,
        model=result.model,
        provider=result.provider,
        prompt_version=result.prompt_version,
        usage=usage,
        cost_usd=cost,
        latency_ms=result.latency_ms + extraction.latency_ms,
        cache_hit=result.cache_hit,
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
