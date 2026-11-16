"""Recorre las transcripciones de data/ con cada variante de experiments/variants.toml.

Con claves de Langfuse, publica el experimento para compararlo en la UI.
Siempre escribe un informe local en experiments/results/.
--dry-run solo calcula tokens y no llama al modelo.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
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
    """Los casos de un turno del golden dataset (evals/golden_dataset.yaml): una sola fuente."""
    from evals.dataset import load_dataset

    cases = []
    for case in load_dataset():
        if case.turns or case.attachments:
            continue  # Sesiones y adjuntos se miden en evals/, que pasa por la API.
        expected = case.expected
        hours = expected.hours_range or (None, None)
        cases.append(
            {
                "id": case.id,
                "input": case.transcript,
                "project_type": case.project_type,
                "expected": {"estimable": expected.estimable, "hours_min": hours[0], "hours_max": hours[1]},
                "properties": expected.as_properties(),
            }
        )
    return cases


def load_variants() -> list[dict]:
    with (ROOT / "experiments" / "variants.toml").open("rb") as handle:
        return list(tomllib.load(handle).get("variant", []))


def overrides_from(variant: dict, case: dict | None = None) -> ContextOverrides:
    """``tier`` y ``review`` (none | acb) sirven para medir si la revisión compensa por perfil (5F)."""
    tier = variant.get("tier")
    return ContextOverrides(
        format=variant.get("format"),
        detail_level=variant.get("detail_level"),
        max_examples=variant.get("max_examples"),
        model=variant.get("model"),
        prompt_cache=variant.get("prompt_cache"),
        prompt_version=variant.get("prompt_version") or ("v4" if tier else None),
        tier=tier,
        review=variant.get("review"),
        project_type=(case or {}).get("project_type") if tier else None,
    )


def _properties(result, case: dict) -> tuple[int, int]:
    """Propiedades que pasan sobre la vista del tier (las mismas que evals/ y Langfuse)."""
    if getattr(result, "view", None) is None or not getattr(result, "tier", None):
        return 0, 0
    from app.evaluation.properties import Subject, run_properties

    subject = Subject(tier=result.tier, view=result.view, estimation=result.estimation, expected=case["properties"])
    checks = run_properties(subject, offline=True)
    return sum(item.passed for item in checks), len(checks)


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
        "tier": variant.get("tier", ""),
        "review": variant.get("review", ""),
        "json_valid_first_try": "",
        "warnings": "",
        "total_hours": "",
        "properties": "",
        "review_decision": "",
        "actor_calls": "",
        "cost_usd": "",
        "latency_ms": "",
        "cached_tokens": "",
    }


async def live_row(variant: dict, case: dict) -> dict:
    from app.state.response_cache import clear_response_cache

    clear_response_cache()  # Un experimento mide el modelo, no la caché.
    result = await generate_estimation(case["input"], overrides=overrides_from(variant, case))
    hours = ""
    if result.estimation is not None:
        hours = result.estimation.total_hours  # type: ignore[attr-defined]
    passed, total = _properties(result, case)
    review = result.review
    return {
        "variant": variant["name"],
        "case": case["id"],
        "context_tokens": result.token_budget.tokens_of("context"),  # type: ignore[attr-defined]
        "examples": ",".join(result.context.examples),
        "detail": result.context.detail_level,
        "format": result.context.format,
        "model": result.model,
        "tier": variant.get("tier", ""),
        "review": variant.get("review", ""),
        "json_valid_first_try": int(result.repairs == 0),
        "warnings": len(result.warnings),
        "total_hours": hours,
        "properties": f"{passed}/{total}" if total else "",
        "review_decision": review.decision if review else "",
        "actor_calls": review.actor_calls if review else "",
        "cost_usd": result.cost_usd if result.cost_usd is not None else "",
        "latency_ms": round(result.latency_ms, 1),
        "cached_tokens": result.usage.cached_tokens,
    }


def write_report(rows: list[dict], name: str) -> Path:
    if not rows:
        raise ValueError("No hay filas: ningún caso o variante coincide con los filtros.")
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
    """Observabilidad: sube el experimento a Langfuse. El informe local ya está escrito."""
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
        case = metadata["case"]
        result = await generate_estimation(item["input"], overrides=overrides_from(variant, case))
        passed, total = _properties(result, case)
        return {
            "warnings": len(result.warnings),
            "hours": None if result.estimation is None else result.estimation.total_hours,  # type: ignore[attr-defined]
            "first_try": result.repairs == 0,
            "kind": result.kind,
            "properties_ratio": passed / total if total else None,
            "review_decision": result.review.decision if result.review else None,
            "cost_usd": result.cost_usd,
        }

    def warnings_evaluator(*, output, **kwargs):
        return Evaluation(name="warnings", value=float(output.get("warnings", 0)))

    def first_try_evaluator(*, output, **kwargs):
        return Evaluation(name="json_valid_first_try", value=1.0 if output.get("first_try") else 0.0)

    def properties_evaluator(*, output, **kwargs):
        # Las mismas propiedades que la suite evals/: una definición, varios usos (fase 4).
        ratio = output.get("properties_ratio")
        return Evaluation(name="properties_passed", value=float(ratio) if ratio is not None else 0.0)

    data = []
    for variant in variants:
        for case in cases:
            data.append(
                {
                    "input": case["input"],
                    "expected_output": case["expected"],
                    "metadata": {"case": case, "variant": variant},
                }
            )
    result = langfuse.run_experiment(
        name=name,
        data=data,
        task=task,
        evaluators=[warnings_evaluator, first_try_evaluator, properties_evaluator],
        max_concurrency=2,
        metadata={"source": "scripts/run_experiment.py"},
    )
    print(result.format())


async def collect_live_rows(variants: list[dict], cases: list[dict]) -> list[dict]:
    """Un solo bucle. Otro asyncio.run deja el cliente HTTP sobre un bucle ya cerrado."""
    from app.services import llm_client

    rows = []
    try:
        for variant in variants:
            for case in cases:
                print(f"{variant['name']} · {case['id']}", flush=True)
                rows.append(await live_row(variant, case))
        return rows
    finally:
        await llm_client.client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Compara variantes de contexto del estimador.")
    parser.add_argument("--dry-run", action="store_true", help="Solo presupuesto de tokens, sin LLM.")
    parser.add_argument("--name", default="contexto")
    parser.add_argument("--langfuse", action="store_true", help="Publica el experimento en Langfuse.")
    parser.add_argument("--variant", default="", help="Solo las variantes cuyo nombre empieza así (p. ej. tier-).")
    parser.add_argument("--case", default="", help="Solo los casos cuyo id empieza así.")
    args = parser.parse_args()
    cases = [case for case in load_cases() if case["id"].startswith(args.case)]
    variants = [variant for variant in load_variants() if variant["name"].startswith(args.variant)]
    if not cases or not variants:
        print(f"Nada que ejecutar: {len(cases)} casos y {len(variants)} variantes con esos filtros.", file=sys.stderr)
        return 2
    if not args.dry_run:
        # La caché semántica serviría respuestas de otra variante parecida: fuera durante el experimento.
        os.environ["SEMANTIC_CACHE_MODE"] = "off"
        get_settings.cache_clear()
    if args.dry_run:
        rows = [dry_row(variant, case) for variant in variants for case in cases]
    else:
        rows = asyncio.run(collect_live_rows(variants, cases))
    report = write_report(rows, args.name)
    print(f"Informe: {report}")
    if not args.dry_run:
        from app.observability import flush_langfuse

        flush_langfuse()
    if args.langfuse and not args.dry_run:
        from app.services.llm_client import reset_client

        reset_client()
        publish_langfuse(cases, variants, args.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
