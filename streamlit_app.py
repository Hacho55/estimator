"""Sesión 3 · streamlit: chat sobre el mismo estimador. La clave sale de .env."""

from __future__ import annotations

import asyncio

import streamlit as st

from app.schemas.conversation import ChatMessage
from app.services.errors import ServiceError
from app.services.llm_client import reset_client
from app.services.llm_service import get_context_info, preview_prompt, stream_estimation

_SAMPLE = "Transcripción de ejemplo para mostrar el prompt del sistema, sin llamar al modelo ahora."


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
        loop.close()
        return None, stream
    except Exception:
        loop.close()
        raise

    if stream.from_cache:
        try:
            while True:
                loop.run_until_complete(iterator.__anext__())
        except StopAsyncIteration:
            pass
        finally:
            loop.close()
        return None, stream

    def generate():
        try:
            yield first
            while True:
                yield loop.run_until_complete(iterator.__anext__())
        except StopAsyncIteration:
            return
        finally:
            loop.close()

    return generate(), stream


def visible_text(result) -> str:
    if result is None:
        return ""
    return result.estimation_markdown or result.message or ""


def _sidebar() -> None:
    last_user = next(
        (item["content"] for item in reversed(st.session_state.messages) if item["role"] == "user"),
        _SAMPLE,
    )
    preview = preview_prompt(last_user)
    system = next((message.content for message in preview.messages if message.role == "system"), "")
    context = get_context_info(include_text=True)
    with st.sidebar:
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
    if not st.session_state.messages and len(prompt.strip()) < 50:
        st.warning("La primera transcripción necesita al menos 50 caracteres.")
        return

    prior = [ChatMessage(role=item["role"], content=item["content"]) for item in st.session_state.messages]
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        try:
            generator, stream = open_reply(prompt, prior)
            if generator is None:
                text = visible_text(stream.result)
                st.markdown(text)
            else:
                st.write_stream(generator)
                text = visible_text(stream.result)
        except ServiceError as exc:
            st.error(exc.message)
            return
    st.session_state.messages.append({"role": "assistant", "content": text})
    _remember(stream.result)
    st.rerun()


if __name__ == "__main__":
    main()
