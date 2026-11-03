"""Ensambla el prompt: orden, segmentos de caché e historial.

La redacción vive en ``app/prompts/estimation/<versión>/``. Aquí solo se elige
la versión, se cortan los segmentos para la caché del prefijo y se une el
historial de la conversación.
"""

from __future__ import annotations

import hashlib

from app.config import derive_provider, get_settings
from app.prompts.loader import render_estimation_prompt, render_reminder, render_repair
from app.schemas.context import ContextBlock
from app.schemas.conversation import ChatMessage
from app.schemas.estimation import (
    DetailLevel,
    OutputFormat,
    PromptMessage,
    PromptPreview,
    PromptRenderRequest,
    TurnOutput,
)
from app.tokens import build_token_budget

DEFAULT_PROMPT_VERSION = "v1"
DEFAULT_DETAIL_LEVEL = DetailLevel.MEDIUM
DEFAULT_OUTPUT_FORMAT = OutputFormat.PHASES_TABLE


def prompt_version(system_text: str, version: str = DEFAULT_PROMPT_VERSION) -> str:
    digest = hashlib.sha256(system_text.encode("utf-8")).hexdigest()[:8]
    return f"{version}-{digest}"


def split_system_segments(system_text: str) -> list[dict[str, str]]:
    """Parte el system renderizado en static / examples / restrictions."""
    if "<examples>" not in system_text or "</examples>" not in system_text:
        raise ValueError("El system prompt no contiene el bloque <examples>.")
    before, rest = system_text.split("<examples>", 1)
    examples_body, after = rest.split("</examples>", 1)
    examples = f"<examples>{examples_body}</examples>"
    restrictions = after.strip()
    if not restrictions.startswith("<constraints>"):
        raise ValueError("El system prompt no deja <constraints> tras los ejemplos.")
    return [
        {"segment": "static", "text": before.rstrip()},
        {"segment": "examples", "text": examples},
        {"segment": "restrictions", "text": restrictions},
    ]


def _render_request(
    description: str,
    *,
    detail_level: DetailLevel = DEFAULT_DETAIL_LEVEL,
    output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT,
    project_type=None,
) -> PromptRenderRequest:
    return PromptRenderRequest(
        description=description,
        project_type=project_type,
        detail_level=detail_level,
        output_format=output_format,
    )


def _history_message(message: ChatMessage, *, remind: bool, version: str) -> dict:
    if message.role == "user" and remind:
        reminder = render_reminder(version)
        content = f"<reminder>\n{reminder}\n</reminder>\n<user_message>\n{message.content}\n</user_message>"
    else:
        content = message.content
    return {"role": message.role, "content": content}


def build_messages(
    transcription: str,
    context_block: ContextBlock,
    conversation_history: list[ChatMessage] | None = None,
    *,
    prompt_label: str = DEFAULT_PROMPT_VERSION,
    detail_level: DetailLevel = DEFAULT_DETAIL_LEVEL,
    output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT,
    project_type=None,
) -> list[dict]:
    request = _render_request(
        transcription,
        detail_level=detail_level,
        output_format=output_format,
        project_type=project_type,
    )
    system, user = render_estimation_prompt(
        request,
        prompt_label,
        rates=context_block.static_text,
        examples=context_block.examples_text,
    )
    segments = split_system_segments(system)
    messages: list[dict] = [
        {"role": "system", "content": system, "segments": segments},
    ]
    history = conversation_history or []
    if history:
        for index, message in enumerate(history):
            is_last_user = message.role == "user" and index == len(history) - 1
            messages.append(_history_message(message, remind=is_last_user, version=prompt_label))
        reminder = render_reminder(prompt_label)
        messages.append(
            {
                "role": "user",
                "content": (
                    f"<reminder>\n{reminder}\n</reminder>\n"
                    f"<user_message>\n{transcription}\n</user_message>"
                ),
            }
        )
    else:
        messages.append({"role": "user", "content": user})
    return ensure_alternation(messages)


def ensure_alternation(messages: list[dict]) -> list[dict]:
    """Algunos proveedores rechazan dos mensajes seguidos del mismo rol."""
    merged: list[dict] = []
    for message in messages:
        if (
            merged
            and message["role"] != "system"
            and merged[-1]["role"] == message["role"]
            and isinstance(merged[-1]["content"], str)
            and isinstance(message["content"], str)
        ):
            merged[-1]["content"] = f"{merged[-1]['content']}\n\n{message['content']}"
            continue
        merged.append(dict(message))
    return merged


def apply_cache_strategy(
    messages: list[dict],
    provider: str,
    context_block: ContextBlock,
    prompt_cache: str | None = None,
) -> list[dict]:
    mode = prompt_cache or get_settings().LLM_PROMPT_CACHE
    prepared = [dict(message) for message in messages]
    system = prepared[0]
    segments = system.pop("segments", None)
    if segments is None:
        return prepared

    use_explicit = mode == "auto" and provider == "anthropic"
    if not use_explicit:
        system["content"] = "\n\n".join(segment["text"] for segment in segments)
        return prepared

    blocks = [{"type": "text", "text": segment["text"]} for segment in segments]
    if context_block.examples_are_static:
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
    else:
        blocks[0]["cache_control"] = {"type": "ephemeral"}
    system["content"] = blocks

    if len(prepared) > 2:
        previous = prepared[-2]
        if isinstance(previous["content"], str):
            previous["content"] = [
                {
                    "type": "text",
                    "text": previous["content"],
                    "cache_control": {"type": "ephemeral"},
                }
            ]
    return prepared


def build_response_format() -> dict:
    if get_settings().LLM_RESPONSE_FORMAT == "json_object":
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "turn_output",
            "schema": TurnOutput.model_json_schema(),
            "strict": False,
        },
    }


def system_text_of(messages: list[dict]) -> str:
    content = messages[0]["content"]
    if isinstance(content, str):
        return content
    return "\n\n".join(block.get("text", "") for block in content)


def cacheable_text(messages: list[dict], context_block: ContextBlock) -> str:
    content = messages[0]["content"]
    if isinstance(content, str):
        if context_block.examples_are_static:
            return content
        return content.split("<examples>", 1)[0]
    if context_block.examples_are_static:
        return "\n\n".join(block.get("text", "") for block in content)
    return content[0].get("text", "") if content else ""


def repair_instruction(error: str, version: str = DEFAULT_PROMPT_VERSION) -> str:
    return render_repair(version, error=error)


def build_preview(
    transcription: str,
    context_block: ContextBlock | None = None,
    *,
    prompt_label: str = DEFAULT_PROMPT_VERSION,
    detail_level: DetailLevel = DEFAULT_DETAIL_LEVEL,
    output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT,
    project_type=None,
) -> PromptPreview:
    from app.context.preprocessing import get_context_block

    block = context_block or get_context_block(transcription)
    settings = get_settings()
    provider = derive_provider(settings.LLM_MODEL)
    messages = apply_cache_strategy(
        build_messages(
            transcription,
            block,
            prompt_label=prompt_label,
            detail_level=detail_level,
            output_format=output_format,
            project_type=project_type,
        ),
        provider,
        block,
        settings.LLM_PROMPT_CACHE,
    )
    prefix = system_text_of(messages)
    budget = build_token_budget(
        instructions=prefix.split("<examples>", 1)[0],
        context=block.examples_text,
        transcription=transcription,
        history="",
        reserved_output=settings.LLM_MAX_TOKENS,
        safety_margin=settings.CONTEXT_SAFETY_MARGIN_TOKENS,
        window=settings.MODEL_CONTEXT_WINDOW,
        model=settings.LLM_MODEL,
        cacheable=cacheable_text(messages, block),
    )
    visible = []
    for message in messages:
        content = message["content"]
        visible.append(PromptMessage(role=message["role"], content=content))
    versioned = prompt_version(
        prefix if block.examples_are_static else cacheable_text(messages, block),
        prompt_label,
    )
    return PromptPreview(
        messages=visible,
        prompt_version=versioned,
        token_budget=budget,
    )
