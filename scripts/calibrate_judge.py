"""Calibra el juez de las evals contra veredictos humanos (fase 4, 4D).

Pasa GEval por cada salida de evals/judge_calibration.yaml, compara su nota con el veredicto
humano y muestra el acuerdo para varios umbrales. El umbral elegido se anota en docs/evals.md
y se usa con EVAL_JUDGE_THRESHOLD.

    uv run python scripts/calibrate_judge.py
    EVAL_JUDGE_MODEL=openrouter/openai/gpt-4.1 uv run python scripts/calibrate_judge.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

import yaml  # noqa: E402
from deepeval.metrics import GEval  # noqa: E402
from deepeval.test_case import LLMTestCase, SingleTurnParams  # noqa: E402

from evals.criteria import SCOPE_COHERENCE, TIER_FIT  # noqa: E402
from evals.judge_model import EstimatorJudge  # noqa: E402

THRESHOLDS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]


def criteria(metric: str) -> str:
    if metric == "ScopeCoherence":
        return SCOPE_COHERENCE
    return TIER_FIT[metric.split(":", 1)[1]]


def main() -> int:
    items = yaml.safe_load((ROOT / "evals" / "judge_calibration.yaml").read_text(encoding="utf-8"))["items"]
    judge = EstimatorJudge()
    params = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT]
    scored = []
    for item in items:
        metric = GEval(
            name=item["metric"],
            criteria=criteria(item["metric"]),
            evaluation_params=params,
            model=judge,
            async_mode=False,
        )
        metric.measure(LLMTestCase(input=item["input"], actual_output=item["output"]))
        score = float(metric.score or 0)
        scored.append((item, score))
        print(f"{item['id']:32} humano={item['human']:4} juez={score:.2f}")
    print(f"\nJuez: {judge.get_model_name()}")
    best: tuple[float, int] | None = None
    for threshold in THRESHOLDS:
        agree = sum((score >= threshold) == (item["human"] == "pass") for item, score in scored)
        print(f"umbral {threshold:.1f}: acuerdo {agree}/{len(scored)}")
        if best is None or agree > best[1]:
            best = (threshold, agree)
    assert best is not None
    print(f"\nUmbral con más acuerdo: {best[0]:.1f} ({best[1]}/{len(scored)}). Anótalo en docs/evals.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
