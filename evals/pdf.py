"""PDF mínimo de una página para convertir los adjuntos de texto del golden dataset."""

from __future__ import annotations

import textwrap


def text_pdf(text: str) -> bytes:
    lines = textwrap.wrap(text, 90) or [""]
    safe = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    body = " ".join(f"({line}) Tj T*" for line in safe)
    content = f"BT /F1 10 Tf 12 TL 40 800 Td {body} ET\n".encode("latin-1", "replace")
    objects = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>endobj\n",
        f"4 0 obj<< /Length {len(content)} >>stream\n".encode() + content + b"endstream\nendobj\n",
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
    ]
    header = b"%PDF-1.4\n"
    offsets, cursor, out = [], len(header), b""
    for obj in objects:
        offsets.append(cursor)
        out += obj
        cursor += len(obj)
    xref = b"xref\n" + f"0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    xref += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    trailer = f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{cursor}\n%%EOF\n".encode()
    return header + out + xref + trailer
