"""Camino B: texto local con pypdf y python-docx. El archivo no sale del proceso."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from app.config import get_settings
from app.services.errors import AttachmentRejectedError, AttachmentTooLargeError

ALLOWED_SUFFIXES = {".pdf", ".docx"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def attachment_label(filename: str) -> str:
    """Nombre que entra en el prompt. Sin rutas, saltos de línea ni controles."""
    cleaned = _CONTROL.sub("", filename or "").replace("\\", "/")
    name = cleaned.split("/")[-1].strip()
    return name or "adjunto"


async def read_up_to(upload, limit: int) -> bytes:
    """Lee como mucho `limit` bytes. Si hay más, no se queda con el archivo."""
    chunks: list[bytes] = []
    total = 0
    while True:
        block = await upload.read(65536)
        if not block:
            break
        total += len(block)
        if total > limit:
            raise AttachmentTooLargeError(f"El adjunto supera el límite de {limit} bytes.")
        chunks.append(block)
    return b"".join(chunks)


def extract_text(filename: str, data: bytes) -> str:
    settings = get_settings()
    name = attachment_label(filename)
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise AttachmentRejectedError(
            f"Tipo de adjunto no admitido ({suffix or 'sin extensión'}). Solo pdf y docx."
        )
    if len(data) > settings.ATTACHMENT_MAX_BYTES:
        raise AttachmentTooLargeError(
            f"{name} supera el límite de {settings.ATTACHMENT_MAX_BYTES} bytes."
        )
    try:
        text = _read(suffix, data)
    except AttachmentRejectedError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AttachmentRejectedError(f"No se pudo leer {name}.") from exc
    cleaned = text.strip()
    if not cleaned:
        raise AttachmentRejectedError(f"{name} no contiene texto extraíble.")
    if len(cleaned) > settings.TRANSCRIPTION_MAX_CHARS:
        raise AttachmentTooLargeError(f"El texto de {name} supera el largo máximo.")
    return cleaned


def join_attachments(transcript: str, extracted: list[tuple[str, str]]) -> str:
    parts = [transcript.strip()]
    for name, text in extracted:
        parts.append(f"--- attachment: {name} ---\n{text}")
    return "\n\n".join(part for part in parts if part)


def _read(suffix: str, data: bytes) -> str:
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    from docx import Document

    document = Document(BytesIO(data))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)
