"""Camino B: texto local con pypdf y python-docx. El archivo no sale del proceso."""

from __future__ import annotations

import re
import zipfile
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
        text = _read(suffix, data, max_chars=settings.TRANSCRIPTION_MAX_CHARS)
    except (AttachmentRejectedError, AttachmentTooLargeError):
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


class _TextBudget:
    """Corta la extracción en cuanto el texto pasa del máximo, sin leer el resto."""

    def __init__(self, max_chars: int) -> None:
        self.max_chars = max_chars
        self.parts: list[str] = []
        self.size = 0

    def add(self, text: str) -> None:
        self.size += len(text) + 1
        if self.size > self.max_chars:
            raise AttachmentTooLargeError("El texto del adjunto supera el largo máximo.")
        self.parts.append(text)

    def text(self) -> str:
        return "\n".join(self.parts)


def _read(suffix: str, data: bytes, *, max_chars: int) -> str:
    budget = _TextBudget(max_chars)
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        for page in reader.pages:
            budget.add(page.extract_text() or "")
        return budget.text()
    _require_bounded_docx(data)
    from docx import Document

    _walk_docx(Document(BytesIO(data)), budget)
    return budget.text()


def _require_bounded_docx(data: bytes) -> None:
    """Un DOCX es un zip: se mira el tamaño descomprimido antes de abrirlo."""
    limit = get_settings().ATTACHMENT_MAX_EXPANDED_BYTES
    with zipfile.ZipFile(BytesIO(data)) as package:
        expanded = sum(info.file_size for info in package.infolist())
    if expanded > limit:
        raise AttachmentTooLargeError(f"El adjunto descomprimido supera {limit} bytes.")


def _walk_docx(container, budget: _TextBudget) -> None:
    """Párrafos y tablas en el orden del documento, también tablas anidadas."""
    from docx.table import Table

    for item in container.iter_inner_content():
        if isinstance(item, Table):
            for row in item.rows:
                seen = set()
                for cell in row.cells:
                    # Las celdas combinadas se repiten en row.cells.
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    _walk_docx(cell, budget)
        else:
            budget.add(item.text)
