"""Cliente HTTP compartido por las herramientas. Los tests lo sustituyen por un transporte falso."""

from __future__ import annotations

import httpx

from app.config import get_settings

# Los tests asignan aquí un httpx.MockTransport. En producción, None (red real).
transport: httpx.AsyncBaseTransport | None = None


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=get_settings().TOOL_TIMEOUT_SECONDS, transport=transport)
