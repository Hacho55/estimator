"""PDF mínimo de texto, sin dependencias: una o varias páginas A4 con Helvetica.

Lo usan el golden dataset (adjuntos cortos) y el stress test (adjuntos de hasta 100 KB de
texto). Determinista: el mismo texto da los mismos bytes.
"""

from __future__ import annotations

import textwrap

LINE_WIDTH = 90
LINES_PER_PAGE = 64


def text_pdf(text: str) -> bytes:
    lines = [line for paragraph in text.split("\n") for line in (textwrap.wrap(paragraph, LINE_WIDTH) or [""])]
    pages = [lines[start : start + LINES_PER_PAGE] for start in range(0, len(lines), LINES_PER_PAGE)] or [[""]]
    font_id = 3 + 2 * len(pages)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # Páginas: se rellena cuando se conocen los ids.
    ]
    page_ids = []
    for page_lines in pages:
        page_id, content_id = len(objects) + 1, len(objects) + 2
        page_ids.append(page_id)
        safe = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in page_lines]
        body = " ".join(f"({line}) Tj T*" for line in safe)
        content = f"BT /F1 10 Tf 12 TL 40 800 Td {body} ET\n".encode("latin-1", "replace")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objects.append(f"<< /Length {len(content)} >>stream\n".encode() + content + b"endstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()

    header = b"%PDF-1.4\n"
    offsets, cursor, out = [], len(header), b""
    for number, payload in enumerate(objects, start=1):
        obj = f"{number} 0 obj".encode() + payload + b"endobj\n"
        offsets.append(cursor)
        out += obj
        cursor += len(obj)
    xref = b"xref\n" + f"0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    xref += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    trailer = f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{cursor}\n%%EOF\n".encode()
    return header + out + xref + trailer
