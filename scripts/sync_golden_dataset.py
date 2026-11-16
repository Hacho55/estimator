"""Sube el golden dataset a un dataset de Langfuse (fase 4, 4G).

El YAML del repo (evals/golden_dataset.yaml) es la fuente de verdad: este script lo copia
a Langfuse para ver las ejecuciones de experimentos contra los mismos casos en la UI. Cada
item usa el id del caso, así que volver a ejecutarlo actualiza en vez de duplicar.

    uv run python scripts/sync_golden_dataset.py
    uv run python scripts/sync_golden_dataset.py --name estimador-golden-v2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from evals.dataset import load_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Sincroniza el golden dataset con Langfuse.")
    parser.add_argument("--name", default="estimador-golden")
    args = parser.parse_args()
    if not get_settings().langfuse_enabled:
        print("Langfuse no está configurado (LANGFUSE_PUBLIC_KEY y LANGFUSE_SECRET_KEY).")
        return 1
    from langfuse import get_client

    langfuse = get_client()
    cases = load_dataset()
    langfuse.create_dataset(
        name=args.name,
        description="Golden dataset del estimador (evals/golden_dataset.yaml).",
        metadata={"source": "evals/golden_dataset.yaml"},
    )
    for case in cases:
        langfuse.create_dataset_item(
            dataset_name=args.name,
            id=f"{args.name}-{case.id}",
            input={"transcript": case.transcript, "turns": case.turns, "attachments": [a.model_dump() for a in case.attachments]},
            expected_output=case.expected.model_dump(),
            metadata={"category": case.category, "tiers": case.tiers, "reviewed": case.reviewed, "notes": case.notes},
        )
    langfuse.flush()
    print(f"{len(cases)} casos en el dataset «{args.name}».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
