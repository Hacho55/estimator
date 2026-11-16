"""La especificación viva (docs/specs/) cita tests que existen: si un test cambia de nombre, falla aquí."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = re.compile(r"`((?:tests|evals)/[\w/]+\.py)::(\w+)`")


def _scenario_rows(text: str) -> list[str]:
    """Filas de las tablas de escenarios: sin cabecera ni separador."""
    rows = [line for line in text.splitlines() if line.startswith("|")]
    return [row for row in rows if not row.startswith("| Cuando") and not set(row) <= set("|- ")]


def test_every_spec_scenario_points_to_an_existing_test() -> None:
    problems = []
    references = 0
    for spec in sorted((ROOT / "docs" / "specs").glob("*.md")):
        if spec.name == "README.md":
            continue
        for row in _scenario_rows(spec.read_text(encoding="utf-8")):
            found = REFERENCE.findall(row)
            if not found:
                problems.append(f"{spec.name}: escenario sin test → {row[:80]}")
            for path, name in found:
                references += 1
                source = ROOT / path
                if not source.is_file() or f"def {name}(" not in source.read_text(encoding="utf-8"):
                    problems.append(f"{spec.name}: no existe {path}::{name}")
    assert references > 0
    assert problems == []
