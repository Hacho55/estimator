"""Genera los adjuntos del stress test: ``attach_{5,20,50,100}kb.pdf`` (bloque 3).

El tamaño es de **texto** (1 KB = 1.000 caracteres), que es lo que entra al contexto; el
PDF pesa algo más. Sin adjunto (0 KB) no hace falta fichero.

Cada documento es un pliego ficticio con relleno de contexto y **tres requisitos marcados**
al 10 %, 50 % y 90 % del texto. Cada uno nombra algo inconfundible (``Kronos``,
``Zephyr``, ``Talara``) para medir el recall por posición: si el del 90 % del de 100 KB no
aparece, lo cortó el truncado a ``ATTACHMENT_MAX_CHARS``, no el modelo.

Determinista y solo ASCII (Helvetica sin tabla de codificación). Los PDFs no se versionan:
el runner los regenera.

    uv run python -m evals.stress.fixtures.build_pdfs
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evals.pdf import text_pdf

FIXTURES_DIR = Path(__file__).resolve().parent
SIZES_KB = (5, 20, 50, 100)


@dataclass(frozen=True)
class Marker:
    token: str  # Lo que busca la métrica (sin distinguir mayúsculas).
    position: float  # Fracción del texto donde aparece.
    sentence: str


MARKERS = (
    Marker(
        "Kronos",
        0.10,
        "REQUISITO OBLIGATORIO R-01: integracion bidireccional con el sistema de fichaje Kronos, sincronizando turnos y ausencias cada 15 minutos.",
    ),
    Marker(
        "Zephyr",
        0.50,
        "REQUISITO OBLIGATORIO R-02: exportacion diaria de todos los movimientos al formato Zephyr-XML que exige el auditor externo.",
    ),
    Marker(
        "Talara",
        0.90,
        "REQUISITO OBLIGATORIO R-03: portal de consulta de solo lectura para la autoridad portuaria de Talara, con acceso por certificado.",
    ),
)

_FILLER = (
    "Seccion {n}. Contexto de la organizacion: la empresa opera {n} centros logisticos y mantiene "
    "procesos manuales heredados que este proyecto pretende ordenar sin cambiar la operativa diaria. "
    "Los responsables de area han revisado este apartado y no anaden requisitos funcionales nuevos; "
    "se describe solo para situar al equipo proveedor. Las condiciones generales de contratacion, el "
    "glosario de terminos y los plazos de pago se rigen por el acuerdo marco vigente. "
)


def build_text(size_kb: int) -> str:
    """Texto de ``size_kb`` × 1.000 caracteres con los marcadores en su posición."""
    target = size_kb * 1000
    filler = ""
    section = 1
    while len(filler) < target:
        filler += _FILLER.format(n=section) + "\n"
        section += 1
    text = filler[:target]
    # De atrás adelante para que insertar no desplace las posiciones pendientes.
    for marker in sorted(MARKERS, key=lambda item: item.position, reverse=True):
        cut = text.rfind("\n", 0, int(len(text) * marker.position)) + 1
        text = text[:cut] + marker.sentence + "\n" + text[cut:]
    return text


def path_for(size_kb: int) -> Path:
    return FIXTURES_DIR / f"attach_{size_kb}kb.pdf"


def ensure_pdfs(sizes: tuple[int, ...] = SIZES_KB) -> dict[int, Path]:
    """Crea los PDFs que falten (o difieran) y devuelve su ruta por tamaño."""
    paths = {}
    for size in sizes:
        if size <= 0:
            continue
        path = path_for(size)
        data = text_pdf(build_text(size))
        if not path.is_file() or path.read_bytes() != data:
            path.write_bytes(data)
        paths[size] = path
    return paths


def main() -> None:
    for size, path in ensure_pdfs().items():
        print(f"{size:>4} KB de texto → {path.name} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
