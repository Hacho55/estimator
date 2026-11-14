"""Camino B: texto local con pypdf y python-docx. El archivo no sale del proceso."""

from __future__ import annotations

import re
import time
import zipfile
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ExtractedAttachment:
    """Texto de un adjunto y lo que costó obtenerlo (regla 3: latencia visible)."""

    name: str
    text: str
    pages: int
    chars: int  # Contenido del documento, sin marcas de página.
    truncated: bool
    ms: float

    def reference(self) -> str:
        """Lo que guarda el historial en lugar del texto (D2: un adjunto se paga una vez)."""
        cut = ", truncado" if self.truncated else ""
        return f"[adjunto: {self.name}, {self.chars} caracteres{cut}]"


def extract_text(filename: str, data: bytes, *, max_chars: int | None = None) -> ExtractedAttachment:
    """Extrae el texto. Si pasa de ``max_chars`` se trunca y se marca (D3), sin leer el resto."""
    settings = get_settings()
    started = time.perf_counter()
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
    limit = settings.ATTACHMENT_MAX_CHARS if max_chars is None else max_chars
    budget = _TextBudget(limit)
    try:
        pages = _read(suffix, data, budget)
    except (AttachmentRejectedError, AttachmentTooLargeError):
        raise
    except Exception as exc:  # noqa: BLE001
        raise AttachmentRejectedError(f"No se pudo leer {name}.") from exc
    text = budget.text().strip()
    if budget.content == 0 and not budget.truncated:
        # Un PDF escaneado o de solo imágenes: sin OCR (camino B) no hay nada que leer.
        raise AttachmentRejectedError(f"{name} no contiene texto extraíble (¿es un escaneo?).")
    return ExtractedAttachment(
        name=name,
        text=text,
        pages=pages,
        chars=budget.content,
        truncated=budget.truncated,
        ms=round((time.perf_counter() - started) * 1000, 2),
    )


class _TextBudget:
    """Acumula texto hasta ``max_chars``. Al llenarse, marca ``truncated`` y deja de guardar."""

    def __init__(self, max_chars: int) -> None:
        self.max_chars = max(0, max_chars)
        self.parts: list[str] = []
        self.size = 0
        self.content = 0  # Caracteres del documento, sin las marcas que añade el sistema.
        self.truncated = False

    @property
    def full(self) -> bool:
        return self.truncated

    def add(self, text: str, *, marker: str = "") -> None:
        """Añade ``text``; ``marker`` (por ejemplo la página) no cuenta como contenido."""
        if self.truncated:
            return
        whole = f"{marker}\n{text}" if marker else text
        room = self.max_chars - self.size
        piece = whole if len(whole) + 1 <= room else whole[: max(0, room - 1)]
        if piece:
            self.parts.append(piece)
            self.size += len(piece) + 1
            self.content += len(piece[len(marker) + 1 :].strip() if marker else piece.strip())
        if piece != whole:
            self.truncated = True

    def text(self) -> str:
        return "\n".join(self.parts)


def _read(suffix: str, data: bytes, budget: _TextBudget) -> int:
    """Rellena ``budget`` y devuelve el número de páginas (PDF) o 0 (Word)."""
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        pages = len(reader.pages)
        for number, page in enumerate(reader.pages, start=1):
            if budget.full:
                break  # Truncado: el resto del PDF no se extrae.
            # Marca de página: el modelo puede citar de dónde sale cada dato.
            budget.add(page.extract_text() or "", marker=f"--- página {number} ---")
        return pages
    _require_bounded_docx(data)
    from docx import Document

    _walk_docx(Document(BytesIO(data)), budget)
    return 0


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
        if budget.full:
            return  # Truncado: el resto del documento no se recorre.
        if isinstance(item, Table):
            for row in item.rows:
                if budget.full:
                    return
                seen = set()
                for cell in row.cells:
                    if budget.full:
                        return
                    # Las celdas combinadas se repiten en row.cells.
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    _walk_docx(cell, budget)
        else:
            budget.add(item.text)
