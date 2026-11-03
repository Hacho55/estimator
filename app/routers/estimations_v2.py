"""API v2: formulario tipado, versión de prompt y la misma estimación estructurada."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.config import get_settings
from app.prompts.loader import available_versions
from app.routers.estimations import _estimate_event, _state, _turn_response
from app.schemas.conversation import (
    ChatMessage,
    ConversationDetail,
    ConversationMessageRequest,
    ConversationTurnResponse,
)
from app.schemas.estimation import (
    ErrorResponse,
    EstimationResponse,
    FormEstimationRequest,
    PromptPreview,
    PromptVersionsResponse,
)
from app.services.errors import ServiceError
from app.services.llm_service import ContextOverrides, generate_estimation, preview_prompt, stream_estimation
from app.state.conversations import get_store

router = APIRouter(prefix="/api/v2", tags=["estimations-v2"])

_ERRORS = {
    413: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}


def _overrides(
    request: FormEstimationRequest,
    *,
    prompt_version: str | None,
) -> ContextOverrides:
    settings = get_settings()
    version = prompt_version or settings.LLM_PROMPT_VERSION
    if version not in available_versions():
        raise HTTPException(status_code=422, detail=f"No existe la versión de prompt '{version}'.")
    return ContextOverrides(
        prompt_version=version,
        form_detail_level=request.detail_level,
        output_format=request.output_format,
        project_type=request.project_type,
    )


def _remember_form(conversation, request: FormEstimationRequest, overrides: ContextOverrides) -> None:
    conversation.prompt_version = overrides.prompt_version
    conversation.project_type = request.project_type
    conversation.form_detail_level = request.detail_level
    conversation.output_format = request.output_format


def _conversation_overrides(conversation) -> ContextOverrides:
    return ContextOverrides(
        prompt_version=conversation.prompt_version,
        form_detail_level=conversation.form_detail_level,
        output_format=conversation.output_format,
        project_type=conversation.project_type,
    )


@router.get("/prompts", response_model=PromptVersionsResponse, operation_id="list_prompt_versions")
async def list_prompts() -> PromptVersionsResponse:
    settings = get_settings()
    return PromptVersionsResponse(versions=available_versions(), default=settings.LLM_PROMPT_VERSION)


@router.post(
    "/estimate",
    response_model=EstimationResponse,
    operation_id="create_estimation_v2",
    responses=_ERRORS,
)
async def estimate(
    request: FormEstimationRequest,
    prompt_version: str | None = Query(default=None),
) -> EstimationResponse:
    overrides = _overrides(request, prompt_version=prompt_version)
    result = await generate_estimation(request.description, overrides=overrides)
    return result.to_estimation_response()


@router.post(
    "/estimate/stream",
    operation_id="stream_estimate_v2",
    response_class=EventSourceResponse,
    responses=_ERRORS,
)
async def estimate_stream(
    request: FormEstimationRequest,
    prompt_version: str | None = Query(default=None),
):
    overrides = _overrides(request, prompt_version=prompt_version)
    stream = stream_estimation(request.description, overrides=overrides)
    try:
        async for piece in stream:
            if stream.from_cache:
                continue
            yield ServerSentEvent(data=piece, event="token")
        result = stream.result
        if result is None:
            yield ServerSentEvent(data={"error": "service_error", "detail": "Sin estimación."}, event="error")
            return
        payload = _estimate_event(result)
        if result.estimation is not None:
            payload["estimation_structured"] = result.estimation.model_dump()
        yield ServerSentEvent(data=payload, event="estimate")
    except ServiceError as exc:
        yield ServerSentEvent(data={"error": exc.code, "detail": exc.message}, event="error")


@router.post(
    "/estimate/preview",
    response_model=PromptPreview,
    operation_id="preview_estimation_prompt_v2",
    include_in_schema=get_settings().APP_ENV == "development",
)
async def preview_estimation(
    request: FormEstimationRequest,
    prompt_version: str | None = Query(default=None),
) -> PromptPreview:
    if get_settings().APP_ENV != "development":
        raise HTTPException(status_code=404, detail="Disponible solo en development.")
    overrides = _overrides(request, prompt_version=prompt_version)
    return preview_prompt(
        request.description,
        prompt_label=overrides.prompt_version or "v1",
        detail_level=request.detail_level,
        output_format=request.output_format,
        project_type=request.project_type,
    )


@router.post(
    "/conversations",
    response_model=ConversationTurnResponse,
    operation_id="create_conversation_v2",
    responses=_ERRORS,
)
async def create_conversation(
    request: FormEstimationRequest,
    prompt_version: str | None = Query(default=None),
) -> ConversationTurnResponse:
    overrides = _overrides(request, prompt_version=prompt_version)
    store = get_store()
    conversation = store.create()
    async with conversation.lock:
        _remember_form(conversation, request, overrides)
        result = await generate_estimation(
            request.description,
            overrides=overrides,
            session_id=conversation.id,
        )
        conversation.messages.append(
            ChatMessage(role="user", content=request.description, kind="estimation", anchor=True)
        )
        conversation.messages.append(
            ChatMessage(
                role="assistant",
                content=result.history_content,
                kind=result.kind,
                anchor=result.kind in {"estimation", "estimation_update"},
            )
        )
        store.touch(conversation)
        pairs = len(conversation.messages) // 2
        state = _state(pairs, pairs - result.dropped_turns, result.dropped_turns, result.usage.input_tokens)
        return _turn_response(conversation.id, pairs, result, state)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationTurnResponse,
    operation_id="send_conversation_message_v2",
    responses={**_ERRORS, 404: {"model": ErrorResponse}},
)
async def send_message(conversation_id: str, request: ConversationMessageRequest) -> ConversationTurnResponse:
    store = get_store()
    async with store.lock(conversation_id):
        conversation = store.get(conversation_id)
        result = await generate_estimation(
            request.message,
            conversation_history=list(conversation.messages),
            overrides=_conversation_overrides(conversation),
            session_id=conversation.id,
        )
        conversation.messages.append(ChatMessage(role="user", content=request.message, kind=result.kind))
        conversation.messages.append(
            ChatMessage(
                role="assistant",
                content=result.history_content,
                kind=result.kind,
                anchor=result.kind in {"estimation", "estimation_update"},
            )
        )
        store.touch(conversation)
        pairs = len(conversation.messages) // 2
        in_window = pairs - result.dropped_turns
        state = _state(pairs, in_window, result.dropped_turns, result.usage.input_tokens)
        return _turn_response(conversation.id, pairs, result, state)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
    operation_id="get_conversation_v2",
    responses={404: {"model": ErrorResponse}},
)
async def get_conversation(conversation_id: str) -> ConversationDetail:
    conversation = get_store().get(conversation_id)
    pairs = len(conversation.messages) // 2
    return ConversationDetail(
        conversation_id=conversation.id,
        messages=conversation.messages,
        conversation_state=_state(pairs, pairs, 0, 0),
    )


@router.delete(
    "/conversations/{conversation_id}",
    operation_id="delete_conversation_v2",
    status_code=204,
    responses={404: {"model": ErrorResponse}},
)
async def delete_conversation(conversation_id: str) -> None:
    get_store().delete(conversation_id)
