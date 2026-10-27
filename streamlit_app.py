"""Sesión 3 · streamlit: chat sobre el mismo estimador. La clave sale de .env."""

from __future__ import annotations

import asyncio

import streamlit as st

from app.config import get_settings
from app.schemas.conversation import ChatMessage
from app.services.errors import ServiceError
from app.services.llm_client import client, reset_client
from app.services.llm_service import get_context_info, preview_prompt, stream_estimation

_SAMPLE = "Transcripción de ejemplo para mostrar el prompt del sistema, sin llamar al modelo ahora."


def input_problem(text: str, *, first: bool) -> str | None:
    """Sesión 3 · la misma longitud que exige la API, también fuera de HTTP."""
    limit = get_settings().TRANSCRIPTION_MAX_CHARS
    if len(text) > limit:
        return f"El texto supera los {limit} caracteres."
    if first and len(text.strip()) < 50:
        return "La primera transcripción necesita al menos 50 caracteres."
    return None


def remembered_turn(*, role: str, display: str, model_content: str, kind: str | None, anchor: bool) -> dict:
    return {
        "role": role,
        "content": display,
        "model_content": model_content,
        "kind": kind,
        "anchor": anchor,
    }


def turn_for_model(item: dict) -> ChatMessage:
    return ChatMessage(
        role=item["role"],
        content=item.get("model_content", item["content"]),
        kind=item.get("kind"),
        anchor=bool(item.get("anchor", False)),
    )


def _close_loop(loop: asyncio.AbstractEventLoop) -> None:
    try:
        loop.run_until_complete(client.close())
    except Exception:
        pass
    loop.close()
    reset_client()


def open_reply(transcription: str, history: list[ChatMessage]):
    """Sesión 3 · streamlit: un bucle nuevo por mensaje.

    Si la respuesta sale de caché, no hay generador: el texto completo ya está en el resultado.
    """
    reset_client()
    stream = stream_estimation(transcription, history or None)
    loop = asyncio.new_event_loop()
    iterator = stream.__aiter__()
    try:
        first = loop.run_until_complete(iterator.__anext__())
    except StopAsyncIteration:
        _close_loop(loop)
        return None, stream
    except Exception:
        _close_loop(loop)
        raise

    if stream.from_cache:
        try:
            while True:
                loop.run_until_complete(iterator.__anext__())
        except StopAsyncIteration:
            pass
        finally:
            _close_loop(loop)
        return None, stream

    def generate():
        try:
            yield first
            while True:
                yield loop.run_until_complete(iterator.__anext__())
        except StopAsyncIteration:
            return
        finally:
            _close_loop(loop)

    return generate(), stream


def visible_text(result) -> str:
    if result is None:
        return ""
    return result.estimation_markdown or result.message or ""


def plain_content(content: str | list) -> str:
    """Sesión 3 · el prompt con caché llega en bloques. El panel lo muestra como texto."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def _sidebar() -> None:
    last_user = next(
        (item["content"] for item in reversed(st.session_state.messages) if item["role"] == "user"),
        _SAMPLE,
    )
    preview = preview_prompt(last_user)
    system = plain_content(next((message.content for message in preview.messages if message.role == "system"), ""))
    context = get_context_info(include_text=True)
    with st.sidebar:
        if st.button("Nueva conversación", disabled=not st.session_state.messages):
            st.session_state.messages = []
            st.session_state.last = None
            st.rerun()
        st.header("Contexto")
        st.caption("Solo lectura. Es el mismo prompt que usa la API.")
        st.text_area("System prompt", system, height=220, disabled=True)
        st.text_area("Contexto inyectado", context.text or "", height=220, disabled=True)
        st.header("Última llamada")
        last = st.session_state.last
        if last is None:
            st.caption("Todavía no hay una estimación.")
            return
        st.write(f"Modelo: {last['model']}")
        st.write(f"Tokens de entrada: {last['input_tokens']}")
        st.write(f"Tokens de salida: {last['output_tokens']}")
        st.write(f"Latencia: {last['latency_ms']} ms")
        origin = "caché" if last["cache_hit"] else "modelo"
        st.write(f"Origen: {origin}")


def _remember(result) -> None:
    st.session_state.last = {
        "model": result.model,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "latency_ms": round(result.latency_ms, 2),
        "cache_hit": result.cache_hit,
    }


def main() -> None:
    st.set_page_config(page_title="Estimador CAG", layout="wide")
    st.title("Estimador CAG")
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "last" not in st.session_state:
        st.session_state.last = None

    _sidebar()
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("Pega la transcripción o pide un ajuste")
    if not prompt:
        return
    problem = input_problem(prompt, first=not st.session_state.messages)
    if problem:
        st.warning(problem)
        return

    prior = [turn_for_model(item) for item in st.session_state.messages]
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        try:
            generator, stream = open_reply(prompt, prior)
            if generator is None:
                text = visible_text(stream.result)
                st.markdown(text)
            else:
                live = st.empty()
                shown = ""
                for snapshot in generator:
                    shown = snapshot
                    live.markdown(shown)
                text = visible_text(stream.result) or shown
        except ServiceError as exc:
            st.error(exc.message)
            return
    first_turn = not prior
    st.session_state.messages.append(
        remembered_turn(
            role="user",
            display=prompt,
            model_content=prompt,
            kind="estimation" if first_turn else None,
            anchor=first_turn,
        )
    )
    st.session_state.messages.append(
        remembered_turn(
            role="assistant",
            display=text,
            model_content=stream.result.history_content if stream.result else text,
            kind=stream.result.kind if stream.result else None,
            anchor=bool(stream.result and stream.result.kind in {"estimation", "estimation_update"}),
        )
    )
    _remember(stream.result)
    st.rerun()


if __name__ == "__main__":
    main()
