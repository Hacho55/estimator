"""Recorre las transcripciones de data/ con cada variante de experiments/variants.toml.

Con claves de Langfuse, publica el experimento para compararlo en la UI.
Siempre escribe un informe local en experiments/results/.
--dry-run solo calcula tokens y no llama al modelo.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.context.preprocessing import build_context_block  # noqa: E402
from app.services.llm_service import ContextOverrides, generate_estimation  # noqa: E402
from app.tokens import count_tokens  # noqa: E402


def load_cases() -> list[dict]:
    cases = []
    for path in sorted((ROOT / "data" / "transcripciones").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        expected: dict = {}
        body = text
        if text.startswith("---"):
            _, front, body = text.split("---", 2)
            for line in front.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                value = value.strip()
                if value == "true":
                    parsed: object = True
                elif value == "false":
                    parsed = False
                else:
                    try:
                        parsed = int(value)
                    except ValueError:
                        parsed = value
                expected[key.strip().removeprefix("expected_")] = parsed
        cases.append({"id": path.stem, "input": body.strip(), "expected": expected})
    return cases


def load_variants() -> list[dict]:
    with (ROOT / "experiments" / "variants.toml").open("rb") as handle:
        return list(tomllib.load(handle).get("variant", []))


def overrides_from(variant: dict) -> ContextOverrides:
    return ContextOverrides(
        format=variant.get("format"),
        detail_level=variant.get("detail_level"),
        max_examples=variant.get("max_examples"),
        model=variant.get("model"),
        prompt_cache=variant.get("prompt_cache"),
    )


def dry_row(variant: dict, case: dict) -> dict:
    settings = get_settings()
    chosen = overrides_from(variant)
    block = build_context_block(
        fmt=chosen.format,
        detail_level=chosen.detail_level,
        max_examples=chosen.max_examples,
    )
    return {
        "variant": variant["name"],
        "case": case["id"],
        "context_tokens": count_tokens(block.text),
        "examples": ",".join(block.included_ids),
        "detail": block.detail_level,
        "format": block.format,
        "model": chosen.model or settings.LLM_MODEL,
        "json_valid_first_try": "",
        "warnings": "",
        "total_hours": "",
        "cost_usd": "",
        "latency_ms": "",
        "cached_tokens": "",
    }


async def live_row(variant: dict, case: dict) -> dict:
    result = await generate_estimation(case["input"], overrides=overrides_from(variant))
    hours = ""
    if result.estimation is not None:
        hours = result.estimation.total_hours  # type: ignore[attr-defined]
    return {
        "variant": variant["name"],
        "case": case["id"],
        "context_tokens": result.token_budget.tokens_of("context"),  # type: ignore[attr-defined]
        "examples": ",".join(result.context.examples),
        "detail": result.context.detail_level,
        "format": result.context.format,
        "model": result.model,
        "json_valid_first_try": int(result.repairs == 0),
        "warnings": len(result.warnings),
        "total_hours": hours,
        "cost_usd": result.cost_usd if result.cost_usd is not None else "",
        "latency_ms": round(result.latency_ms, 1),
        "cached_tokens": result.usage.cached_tokens,
    }


def write_report(rows: list[dict], name: str) -> Path:
    folder = ROOT / "experiments" / "results"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    csv_path = folder / f"{stamp}_{name}.csv"
    md_path = folder / f"{stamp}_{name}.md"
    fieldnames = list(rows[0])
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    lines = [f"# Experimento {name}", "", "| " + " | ".join(fieldnames) + " |", "| " + " | ".join("---" for _ in fieldnames) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row[field]) for field in fieldnames) + " |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


def publish_langfuse(cases: list[dict], variants: list[dict], name: str) -> None:
    settings = get_settings()
    if not settings.langfuse_enabled:
        print("Langfuse no está configurado: solo informe local.")
        return
    from langfuse import Evaluation, get_client

    langfuse = get_client()

    async def task(*, item, **kwargs):
        metadata = item["metadata"] if isinstance(item, dict) else item.metadata
        variant = metadata["variant"]
        del kwargs
        result = await generate_estimation(item["input"], overrides=overrides_from(variant))
        return {
            "warnings": len(result.warnings),
            "hours": None if result.estimation is None else result.estimation.total_hours,  # type: ignore[attr-defined]
            "first_try": result.repairs == 0,
            "kind": result.kind,
        }

    def warnings_evaluator(*, output, **kwargs):
        return Evaluation(name="warnings", value=float(output.get("warnings", 0)))

    def first_try_evaluator(*, output, **kwargs):
        return Evaluation(name="json_valid_first_try", value=1.0 if output.get("first_try") else 0.0)

    data = []
    for variant in variants:
        for case in cases:
            data.append(
                {
                    "input": case["input"],
                    "expected_output": case["expected"],
                    "metadata": {"case": case["id"], "variant": variant},
                }
            )
    result = langfuse.run_experiment(
        name=name,
        data=data,
        task=task,
        evaluators=[warnings_evaluator, first_try_evaluator],
        max_concurrency=2,
        metadata={"source": "scripts/run_experiment.py"},
    )
    print(result.format())


def main() -> int:
    parser = argparse.ArgumentParser(description="Compara variantes de contexto del estimador.")
    parser.add_argument("--dry-run", action="store_true", help="Solo presupuesto de tokens, sin LLM.")
    parser.add_argument("--name", default="contexto")
    parser.add_argument("--langfuse", action="store_true", help="Publica el experimento en Langfuse.")
    args = parser.parse_args()
    cases = load_cases()
    variants = load_variants()
    rows = []
    for variant in variants:
        for case in cases:
            if args.dry_run:
                rows.append(dry_row(variant, case))
            else:
                rows.append(asyncio.run(live_row(variant, case)))
    report = write_report(rows, args.name)
    print(f"Informe: {report}")
    if not args.dry_run:
        from app.observability import flush_langfuse

        flush_langfuse()
    if args.langfuse and not args.dry_run:
        publish_langfuse(cases, variants, args.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
