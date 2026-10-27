"""Sesión 3 · chainlit: la misma conversación que Streamlit, en otra cara."""

from __future__ import annotations

import chainlit as cl

from app.schemas.conversation import ChatMessage
from app.services.errors import ServiceError
from app.services.llm_service import get_context_info, preview_prompt, stream_estimation
from streamlit_app import visible_text

_SAMPLE = "Transcripción de ejemplo para mostrar el prompt del sistema, sin llamar al modelo ahora."


async def drive_estimation(transcription: str, history: list[ChatMessage], on_token) -> object:
    """Sesión 3 · chainlit: entrega trozos salvo cuando la respuesta sale de caché."""
    stream = stream_estimation(transcription, history or None)
    async for piece in stream:
        if stream.from_cache:
            continue
        await on_token(piece)
    return stream.result


def _metrics(result) -> str:
    origin = "caché" if result.cache_hit else "modelo"
    return (
        f"Modelo: {result.model}\n"
        f"Tokens de entrada: {result.usage.input_tokens}\n"
        f"Tokens de salida: {result.usage.output_tokens}\n"
        f"Latencia: {round(result.latency_ms, 2)} ms\n"
        f"Origen: {origin}"
    )


async def _show_sidebar(transcription: str, result) -> None:
    preview = preview_prompt(transcription or _SAMPLE)
    system = next((message.content for message in preview.messages if message.role == "system"), "")
    context = get_context_info(include_text=True)
    await cl.ElementSidebar.set_title("Contexto")
    await cl.ElementSidebar.set_elements(
        [
            cl.Text(name="Última llamada", content=_metrics(result), display="side"),
            cl.Text(name="System prompt", content=system, display="side"),
            cl.Text(name="Contexto inyectado", content=context.text or "", display="side"),
        ]
    )


@cl.on_chat_start
async def start() -> None:
    cl.user_session.set("history", [])


@cl.on_message
async def on_message(message: cl.Message) -> None:
    history: list[ChatMessage] = cl.user_session.get("history") or []
    text = message.content or ""
    if not history and len(text.strip()) < 50:
        await cl.Message(content="La primera transcripción necesita al menos 50 caracteres.").send()
        return

    outgoing = cl.Message(content="")
    started = False

    async def on_token(piece: str) -> None:
        nonlocal started
        if not started:
            await outgoing.send()
            started = True
        await outgoing.stream_token(piece)

    try:
        result = await drive_estimation(text, history, on_token)
    except ServiceError as exc:
        await cl.Message(content=exc.message).send()
        return

    final = visible_text(result)
    if result.cache_hit or not started:
        await cl.Message(content=final).send()
    else:
        outgoing.content = final
        await outgoing.update()

    history.append(ChatMessage(role="user", content=text))
    history.append(ChatMessage(role="assistant", content=final))
    cl.user_session.set("history", history)
    await _show_sidebar(text, result)
