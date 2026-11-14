"""Sesiones multi-turno. El historial y los hechos del proyecto viven separados."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import PurePosixPath

import structlog
from fastapi import APIRouter, Body, Depends, File, Form, UploadFile

from app.attachments.extractor import ExtractedAttachment, attachment_label, extract_text, read_up_to
from app.config import get_settings
from app.observability import traced_span
from app.schemas.estimation import DetailLevel, ErrorResponse, ProjectType, UsageInfo, ValidationIssue
from app.schemas.session import (
    AttachmentInfo,
    MemoryChange,
    ProjectMetadata,
    SessionCreated,
    SessionCreateRequest,
    SessionEstimateResponse,
    SessionState,
)
from app.security.caller import CallerContext, get_caller
from app.services.errors import AttachmentRejectedError, InputRejectedError
from app.services.guardrails import guard_dynamic_text
from app.services.llm_service import ContextOverrides, generate_estimation
from app.services.metadata_extractor import extract_metadata_update
from app.services.prompt_builder import DynamicContext
from app.state.memory import apply_update
from app.state.sessions import get_session_store

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])

SESSION_PROMPT_VERSION = "v4"
MEMORY_LOG_MAX = 200  # Registro de auditoría acotado por sesión.
FIRST_TURN_MIN_CHARS = 50

_ERRORS = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}


@router.post(
    "",
    operation_id="create_session",
    response_model=SessionCreated,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def create_session(
    request: SessionCreateRequest | None = Body(default=None),
    caller: CallerContext = Depends(get_caller),
) -> SessionCreated:
    """Crea una sesión del llamante. Con JWT, solo su ``sub`` podrá usarla.

    Olvido explícito (fase 2): con ``previous_session_id`` la anterior queda archivada
    para auditoría (política 3, reset). Con ``inherit_memory`` la nueva arranca con
    sus hechos y sin historial (política 2, reanudar tras la caducidad).
    """
    store = get_session_store()
    request = request or SessionCreateRequest()
    if not request.previous_session_id:
        session = store.create(owner=caller.user_id)
        return SessionCreated(session_id=session.id)
    previous = store.get(request.previous_session_id, owner=caller.user_id, allow_archived=True)
    # Con su lock: si la anterior está a mitad de turno, se espera. Crear, archivar y heredar
    # van juntos y sin await entre medias, así la nueva no puede desalojarse a medio hacer.
    async with previous.lock:
        session = store.create(owner=caller.user_id)
        store.archive(previous, "reset")
        if request.inherit_memory:
            session.metadata = previous.metadata.model_copy(deep=True)
            session.inherited_from = previous.id
            session.memory_log.append(MemoryChange(turn=0, field="inherited_from", before=None, after=previous.id))
    return SessionCreated(session_id=session.id, inherited_from=session.inherited_from)


@router.get(
    "/{session_id}",
    operation_id="get_session",
    response_model=SessionState,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_session(session_id: str, caller: CallerContext = Depends(get_caller)) -> SessionState:
    """Estado auditable: memoria, registro de cambios y ciclo de vida. Sin el texto del historial."""
    session = get_session_store().get(session_id, owner=caller.user_id, allow_archived=True)
    return SessionState(
        session_id=session.id,
        status=session.status,
        archived_reason=session.archived_reason,
        inherited_from=session.inherited_from,
        turns=session.turns,
        window_turns=session.history.turn_count(),
        tier=session.last_tier,
        project_metadata=session.metadata,
        memory_log=session.memory_log,
        created_at=session.created_at,
        updated_at=session.updated_at,
        archived_at=session.archived_at,
    )


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
        store.ensure_current(session)  # Pudo archivarse o purgarse mientras esperaba el lock.
        attachment_items = await _read_attachments(attachments or [])
        attached_chars = sum(item.chars for item in attachment_items)
        text = _bounded_transcript(transcript, first_turn=session.turns == 0, attached_chars=attached_chars)
        _remember_form(session, project_type, detail_level)
        history = list(session.history.messages)
        memory = None if session.metadata.is_empty() else session.metadata.model_dump()
        dynamic_attachments = [
            {"name": item.name, "text": item.text, "truncated": item.truncated} for item in attachment_items if item.text
        ]
        result = await generate_estimation(
            text,
            history,
            overrides=ContextOverrides(
                prompt_version=SESSION_PROMPT_VERSION,
                form_detail_level=session.form_detail_level,
                project_type=session.project_type,
                dynamic=DynamicContext(memory=memory, attachments=dynamic_attachments),
                session_window=True,
                tier=caller.tier.value,
            ),
            session_id=session.id,
        )
        scrubbed = result.scrubbed_input or text
        with traced_span("extract-metadata"):
            extraction = await extract_metadata_update(
                previous=session.metadata,
                user_turn=_extractor_turn(scrubbed, result.attachments_used),
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
        # D2: el historial guarda la referencia al adjunto, no su texto; lo que importa ya está en la memoria.
        session.history.append(_history_turn(scrubbed, attachment_items), result.history_content, result.kind)
        session.turns = turn_number
        store.touch(session)
        warnings = [*result.warnings, *_attachment_warnings(attachment_items)]
        return _response(session.id, session.metadata, changes, extraction, result, attachment_items, warnings)


def _bounded_transcript(transcript: str, *, first_turn: bool, attached_chars: int = 0) -> str:
    """El primer turno necesita contenido: 50 caracteres entre transcripción y adjuntos."""
    text = transcript.strip()
    settings = get_settings()
    if first_turn and len(text) + attached_chars < FIRST_TURN_MIN_CHARS:
        raise InputRejectedError(
            "El primer turno necesita al menos 50 caracteres entre la transcripción y los adjuntos."
        )
    if not text and not attached_chars:
        raise InputRejectedError("El mensaje está vacío.")
    if len(text) > settings.TRANSCRIPTION_MAX_CHARS:
        raise InputRejectedError("La transcripción supera el largo máximo.")
    return text or "(Ver adjuntos.)"


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


async def _read_attachments(uploads: list[UploadFile]) -> list[ExtractedAttachment]:
    """Extrae cada adjunto con su span. El texto de todos comparte ATTACHMENT_MAX_CHARS (D3)."""
    settings = get_settings()
    present = [upload for upload in uploads if upload.filename]
    if len(present) > settings.ATTACHMENT_MAX_FILES:
        raise AttachmentRejectedError("Demasiados adjuntos.")
    items: list[ExtractedAttachment] = []
    room = settings.ATTACHMENT_MAX_CHARS
    for upload in present:
        data = await read_up_to(upload, settings.ATTACHMENT_MAX_BYTES)
        name = attachment_label(upload.filename or "adjunto")
        if room <= 0:
            # Cupo agotado por los anteriores: este no se lee, pero se informa (D3), no se rechaza.
            items.append(ExtractedAttachment(name=_safe_label(name), text="", pages=0, chars=0, truncated=True, ms=0))
            continue
        with traced_span("extract-attachment") as span:
            # pypdf y python-docx son CPU: fuera del event loop del único worker.
            item = await asyncio.to_thread(extract_text, name, data, max_chars=room)
            # El nombre también llega al modelo y al historial: pasa el filtro de PII (sin perder la extensión).
            item = replace(item, name=_safe_label(item.name))
            _annotate_attachment(span, item)
        room = max(0, room - len(item.text))  # Lo que ocupa en el prompt, marcas incluidas.
        items.append(item)
    return items


def _safe_label(name: str) -> str:
    path = PurePosixPath(name)
    stem = guard_dynamic_text(path.stem, source="attachment_name")
    return f"{stem}{path.suffix}"


def _annotate_attachment(span, item: ExtractedAttachment) -> None:
    if span is None:
        return
    span.update(
        output={"name": item.name, "pages": item.pages, "chars": item.chars, "truncated": item.truncated, "ms": item.ms}
    )


def _attachment_warnings(items: list[ExtractedAttachment]) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            code="attachment_truncated",
            severity="warning",
            message=f"El texto de {item.name} se recortó a {item.chars} caracteres; el resto no se tuvo en cuenta.",
            field="attachments",
            corrected_value=str(item.chars),
        )
        for item in items
        if item.truncated
    ]


def _history_turn(text: str, items: list[ExtractedAttachment]) -> str:
    references = "\n".join(item.reference() for item in items)
    return f"{text}\n{references}".strip() if references else text


def _extractor_turn(text: str, attachments: list[dict]) -> str:
    """El extractor de memoria ve el turno y, acotado, el texto ya tachado de los adjuntos."""
    budget = get_settings().METADATA_ATTACHMENT_CHARS
    parts = [text]
    for item in attachments:
        if budget <= 0:
            break
        excerpt = item["text"][:budget]
        budget -= len(excerpt)
        parts.append(f"[adjunto {item['name']}]\n{excerpt}")
    return "\n\n".join(parts)


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
    attachments: list[ExtractedAttachment],
    warnings: list[ValidationIssue],
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
        warnings=warnings,
        attachments=[
            AttachmentInfo(name=item.name, pages=item.pages, chars=item.chars, truncated=item.truncated, ms=item.ms)
            for item in attachments
        ],
        review=result.review,
        tools=result.tools,
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
