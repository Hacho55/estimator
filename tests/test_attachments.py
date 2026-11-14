"""El camino B lee PDF y Word en local."""

from io import BytesIO

import pytest
from docx import Document

from app.attachments.extractor import attachment_label, extract_text, read_up_to
from app.config import get_settings
from app.services.errors import AttachmentRejectedError, AttachmentTooLargeError
from tests.test_sessions import _pdf


def test_extracts_pdf_and_docx() -> None:
    pdf = extract_text("nota.pdf", _pdf("TEXTO-PDF"))
    assert "TEXTO-PDF" in pdf.text
    assert pdf.text.startswith("--- página 1 ---")
    assert pdf.pages == 1 and pdf.chars == len("TEXTO-PDF") and not pdf.truncated  # Sin la marca.

    buffer = BytesIO()
    document = Document()
    document.add_paragraph("TEXTO-DOCX")
    document.save(buffer)
    docx = extract_text("nota.docx", buffer.getvalue())
    assert "TEXTO-DOCX" in docx.text


class _Chunked:
    def __init__(self, data: bytes, size: int = 8) -> None:
        self._data = data
        self._size = size
        self._index = 0

    async def read(self, _n: int = -1) -> bytes:
        if self._index >= len(self._data):
            return b""
        part = self._data[self._index : self._index + self._size]
        self._index += len(part)
        return part


async def test_read_stops_past_the_byte_limit() -> None:
    with pytest.raises(AttachmentTooLargeError):
        await read_up_to(_Chunked(b"x" * 50), 20)


def test_long_text_is_truncated_and_flagged(monkeypatch) -> None:
    monkeypatch.setenv("ATTACHMENT_MAX_CHARS", "80")
    get_settings.cache_clear()
    item = extract_text("nota.pdf", _pdf("TEXTO " * 40))
    assert item.truncated and item.chars <= 80
    assert item.reference() == f"[adjunto: nota.pdf, {item.chars} caracteres, truncado]"


def test_rejects_unknown_type() -> None:
    with pytest.raises(AttachmentRejectedError):
        extract_text("notas.txt", b"hola")


def test_label_strips_path_and_control_characters() -> None:
    assert attachment_label("..\\\\notas\\informe.docx") == "informe.docx"
    assert "\n" not in attachment_label("nota.pdf\r\n---")
    assert attachment_label("nota.pdf\r\n---") == "nota.pdf---"


def _docx(build) -> bytes:
    buffer = BytesIO()
    document = Document()
    build(document)
    document.save(buffer)
    return buffer.getvalue()


def test_docx_reads_tables_in_document_order() -> None:
    def build(document) -> None:
        document.add_paragraph("INTRO")
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "REQ-PAGOS"
        table.cell(1, 1).text = "REQ-ERP"
        table.cell(1, 0).add_table(rows=1, cols=1).cell(0, 0).text = "REQ-ANIDADO"
        document.add_paragraph("CIERRE")

    text = extract_text("req.docx", _docx(build)).text
    order = [text.index(token) for token in ("INTRO", "REQ-PAGOS", "REQ-ANIDADO", "REQ-ERP", "CIERRE")]
    assert order == sorted(order)


def test_docx_rejects_large_expansion(monkeypatch) -> None:
    monkeypatch.setenv("ATTACHMENT_MAX_EXPANDED_BYTES", "1000")
    get_settings.cache_clear()
    data = _docx(lambda document: document.add_paragraph("A" * 5000))
    with pytest.raises(AttachmentTooLargeError):
        extract_text("bomba.docx", data)


def test_pdf_without_text_is_rejected_despite_page_markers() -> None:
    with pytest.raises(AttachmentRejectedError, match="no contiene texto"):
        extract_text("escaneo.pdf", _pdf(""))


def test_docx_walk_stops_once_truncated(monkeypatch) -> None:
    monkeypatch.setenv("ATTACHMENT_MAX_CHARS", "30")
    get_settings.cache_clear()

    def build(document) -> None:
        document.add_paragraph("PRIMERO " * 5)
        table = document.add_table(rows=1, cols=1)
        table.cell(0, 0).text = "TABLA-QUE-NO-DEBE-LEERSE"

    item = extract_text("largo.docx", _docx(build))
    assert item.truncated and "TABLA" not in item.text
