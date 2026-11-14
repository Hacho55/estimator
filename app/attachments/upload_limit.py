"""Tope del cuerpo multipart antes de que Starlette lo guarde en disco temporal.

Los límites por archivo de `extractor.py` actúan cuando el handler ya tiene los
`UploadFile`; para entonces el cuerpo entero se ha recibido. Este middleware
corta mientras llega.
"""

from __future__ import annotations

import json

from app.config import get_settings

# Margen para el texto del formulario y las cabeceras de cada parte.
_FORM_OVERHEAD_BYTES = 64_000


def max_upload_bytes() -> int:
    settings = get_settings()
    return (
        settings.ATTACHMENT_MAX_FILES * settings.ATTACHMENT_MAX_BYTES
        + settings.TRANSCRIPTION_MAX_CHARS * 4  # UTF-8: hasta 4 bytes por carácter.
        + _FORM_OVERHEAD_BYTES
    )


class UploadSizeLimit:
    """ASGI puro. Solo mira los POST bajo `path_prefix`."""

    def __init__(self, app, *, path_prefix: str = "/sessions/") -> None:
        self.app = app
        self.path_prefix = path_prefix

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].startswith(self.path_prefix):
            await self.app(scope, receive, send)
            return

        limit = max_upload_bytes()
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _reject(send, limit)
            return

        received = 0
        exceeded = False
        started = False

        async def limited_receive():
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # Se deja de leer; el parser falla y su respuesta se cambia por el 413.
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message) -> None:
            nonlocal started
            if exceeded:
                if message["type"] == "http.response.start" and not started:
                    started = True
                    await _reject(send, limit)
                return
            started = started or message["type"] == "http.response.start"
            await send(message)

        await self.app(scope, limited_receive, guarded_send)


async def _reject(send, limit: int) -> None:
    body = json.dumps(
        {"error": "attachment_too_large", "detail": f"La petición supera el límite de {limit} bytes."},
        ensure_ascii=False,
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
