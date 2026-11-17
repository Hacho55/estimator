"""Del CSV a las tablas del ``REPORT.md`` (bloque 5): resumen y las tres curvas, sin gráficos.

    uv run python -m evals.stress.report evals/stress/results.csv > /tmp/tablas.md

Los párrafos de lectura no salen de aquí: los escribe una persona mirando estos números.
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from evals.stress.run import DEFAULT_OUTPUT

TOKEN_BUCKETS = (0, 5_000, 10_000, 15_000, 20_000, 30_000, 50_000)


def load(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _num(value: str | None) -> float | None:
    return None if value in (None, "") else float(value)


def percentile(values: list[float], pct: float) -> float | None:
    """Rango más cercano: con pocas muestras no inventa valores intermedios."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def _cost_mean(items: list[dict]) -> tuple[float | None, bool]:
    """Media de los costes conocidos y si faltó alguno (desconocido no es 0)."""
    known = [_num(row["cost_usd"]) for row in items if row["cost_usd"] not in (None, "")]
    return _mean(known), len(known) < len(items)


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _fmt(value: float | None, digits: int = 0, scale: float = 1.0) -> str:
    return "—" if value is None else f"{value / scale:,.{digits}f}"


def _pct(values: list[float]) -> str:
    return "—" if not values else f"{100 * statistics.fmean(values):.0f} %"


def _ok(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("status") == "200"]


def _table(header: list[str], body: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---:" if i else "---" for i in range(len(header))) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


def summary(rows: list[dict]) -> str:
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["scenario"], int(row["attachment_kb"] or 0))].append(row)
    body = []
    for (scenario, kb), items in sorted(groups.items()):
        ok = _ok(items)
        latency = [_num(row["latency_ms"]) for row in ok]
        fresh = [_num(row["latency_ms"]) for row in ok if row["cache_hit_kind"] == "none"]
        cost = [_num(row["cost_usd"]) for row in ok if row["cost_usd"] not in (None, "")]
        recall = [_num(row["drift_recall"]) for row in ok if row["drift_recall"]]
        attach = [_num(row["attachment_recall"]) for row in ok if row.get("attachment_recall")]
        body.append(
            [
                scenario,
                str(kb),
                str(len(ok)) + (f" (+{len(items) - len(ok)} err)" if len(items) > len(ok) else ""),
                _fmt(percentile(latency, 50), 1, 1000),
                _fmt(percentile(latency, 95), 1, 1000),
                _fmt(percentile(fresh, 95), 1, 1000),
                _fmt(sum(cost), 2) + ("?" if len(cost) < len(ok) else ""),
                _pct([float(row["cache_hit_kind"] == "exact") for row in ok]),
                _pct([float(row["cache_hit_kind"] == "semantic") for row in ok]),
                _fmt(_mean(recall) and 100 * _mean(recall), 0) + (" %" if recall else ""),
                _fmt(_mean(attach) and 100 * _mean(attach), 0) + (" %" if attach else ""),
                _pct([float(row["latency_ok"] == "1") for row in ok]),
            ]
        )
    header = [
        "Escenario", "KB", "n", "P50 s", "P95 s", "P95 s sin caché", "Coste USD",
        "Caché exacta", "Caché semántica", "Recall memoria", "Recall pliego", "En SLA",
    ]  # fmt: skip
    return _table(header, body)


def latency_vs_tokens(rows: list[dict]) -> str:
    fresh = [row for row in _ok(rows) if row["cache_hit_kind"] == "none" and row["concurrency"] == "1"]
    body = []
    for low, high in zip(TOKEN_BUCKETS, (*TOKEN_BUCKETS[1:], None), strict=True):
        items = [row for row in fresh if _num(row["tokens_in"]) >= low and (high is None or _num(row["tokens_in"]) < high)]
        if not items:
            continue
        latency = [_num(row["latency_ms"]) for row in items]
        out = [_num(row["tokens_out"]) for row in items]
        label = f"{low:,}–{high - 1:,}" if high else f"{low:,}+"
        body.append(
            [
                label,
                str(len(items)),
                _fmt(percentile(latency, 50), 1, 1000),
                _fmt(percentile(latency, 95), 1, 1000),
                _fmt(statistics.median(out), 0),
                _fmt(statistics.median([_num(row["estimate_ms"]) for row in items]), 1, 1000),
                _fmt(statistics.median([_num(row["extractor_ms"]) for row in items]), 1, 1000),
            ]
        )
    header = ["`tokens_in`", "n", "P50 s", "P95 s", "Mediana `tokens_out`", "Mediana estimación s", "Mediana extractor s"]
    return _table(header, body)


def _by_turn(rows: list[dict], scenarios: list[str]) -> dict[str, dict[int, list[dict]]]:
    grouped: dict[str, dict[int, list[dict]]] = {name: defaultdict(list) for name in scenarios}
    for row in _ok(rows):
        if row["scenario"] in grouped and row["attachment_kb"] in ("0", ""):
            grouped[row["scenario"]][int(row["turn_index"])].append(row)
    return grouped


def cost_vs_turn(rows: list[dict], scenarios: list[str]) -> str:
    """Coste acumulado medio por repetición y el coste del turno N frente al del turno 1."""
    grouped = _by_turn(rows, scenarios)
    turns = sorted({turn for per_turn in grouped.values() for turn in per_turn})
    body = []
    running = {name: 0.0 for name in scenarios}
    first = {name: _cost_mean(grouped[name].get(1, []))[0] for name in scenarios}
    incomplete = {name: False for name in scenarios}
    for turn in turns:
        cells = [str(turn)]
        for name in scenarios:
            mean, missing = _cost_mean(grouped[name].get(turn, []))
            incomplete[name] |= missing
            if mean is None:
                cells += ["?", "—"]
                continue
            running[name] += mean
            ratio = mean / first[name] if first[name] else None
            mark = "?" if incomplete[name] else ""  # Algún turno hasta aquí sin precio: acumulado incompleto.
            cells += [_fmt(running[name], 3) + mark, _fmt(ratio, 1) + "×" if ratio else "—"]
        body.append(cells)
    header = ["Turno", *[f"{name} {label}" for name in scenarios for label in ("acum. USD", "vs turno 1")]]
    return _table(header, body)


def drift_vs_turn(rows: list[dict], scenarios: list[str]) -> str:
    grouped = _by_turn(rows, scenarios)
    turns = sorted({turn for per_turn in grouped.values() for turn in per_turn})
    body = []
    for turn in turns:
        cells = [str(turn)]
        for name in scenarios:
            items = grouped[name].get(turn, [])
            recall = [_num(row["drift_recall"]) for row in items if row["drift_recall"]]
            kept = [float(row["first_fact_kept"] == "1") for row in items if row["first_fact_kept"]]
            facts = items[0]["anchors_count"] if items else "—"
            cells += [_fmt(_mean(recall) and 100 * _mean(recall), 0) + (" %" if recall else ""), _pct(kept), facts]
        body.append(cells)
    header = ["Turno N", *[f"{name} {label}" for name in scenarios for label in ("recall", "nombre", "hechos")]]
    return _table(header, body)


def attachments(rows: list[dict]) -> str:
    groups: dict[int, list[dict]] = defaultdict(list)
    for row in _ok(rows):
        if row["scenario"] == "attachments":
            groups[int(row["attachment_kb"])].append(row)
    body = []
    for kb, items in sorted(groups.items()):
        first = [row for row in items if row["turn_index"] == "1"]
        second = [row for row in items if row["turn_index"] == "2"]
        body.append(
            [
                str(kb),
                _fmt(_mean([_num(row["attachments_total_chars"]) for row in first]), 0),
                _pct([float(row["attachments_truncated"] == "True") for row in first]),
                _fmt(_mean([_num(row["tokens_in"]) for row in first]), 0),
                _fmt(percentile([_num(row["latency_ms"]) for row in first], 50), 1, 1000),
                _fmt(_cost_mean(first)[0], 3) + ("?" if _cost_mean(first)[1] else ""),
                _pct([_num(row["attachment_recall"]) for row in first if row["attachment_recall"]]),
                _pct([_num(row["attachment_recall"]) for row in second if row["attachment_recall"]]),
            ]
        )
    header = ["KB", "Caracteres extraídos", "Truncado", "`tokens_in` turno 1", "P50 s", "USD turno 1", "Recall turno 1", "Recall turno 2"]
    return _table(header, body)


def burst(rows: list[dict]) -> str:
    sequential = [row for row in _ok(rows) if row["turn_index"] == "1" and row["concurrency"] == "1" and row["attachment_kb"] == "0"]
    parallel = [row for row in _ok(rows) if row["scenario"] == "burst"]
    body = []
    for label, items in (("Primer turno, en serie", sequential), (f"Ráfaga ({len(parallel)} a la vez)", parallel)):
        latency = [_num(row["latency_ms"]) for row in items]
        wall = [_num(row["wall_ms"]) for row in items]
        body.append(
            [
                label,
                str(len(items)),
                _fmt(percentile(latency, 50), 1, 1000),
                _fmt(percentile(latency, 95), 1, 1000),
                _fmt(percentile(wall, 95), 1, 1000),
                _pct([float(row["cache_hit_kind"] != "none") for row in items]),
            ]
        )
    return _table(["Carga", "n", "P50 s", "P95 s", "P95 s de reloj", "Aciertos de caché"], body)


def render(rows: list[dict]) -> str:
    multi = [name for name in ("growing", "pivot", "contradiction") if any(row["scenario"] == name for row in rows)]
    errors = [row for row in rows if row.get("status") != "200"]
    # El gasto cuenta toda fila con observación, también las inválidas (tier_mismatch): se pagaron.
    paid = [row for row in rows if row.get("tokens_in") not in (None, "")]
    total = sum(_num(row["cost_usd"]) or 0 for row in paid)
    unknown = sum(1 for row in paid if row["cost_usd"] in (None, ""))
    cost = f"{total:.2f} USD" + (f" conocidos + {unknown} turnos sin precio" if unknown else "")
    parts = [
        "`?`: algún turno sin precio conocido en ese agregado.",
        f"Filas: {len(rows)} ({len(errors)} con error). Coste total: {cost}.",
        "## Resumen por escenario y tamaño\n\n" + summary(rows),
        "## Curva 1 · latencia frente a `tokens_in` (sin aciertos de caché ni ráfaga)\n\n" + latency_vs_tokens(rows),
        "## Curva 2 · coste acumulado frente al turno (sin adjunto, media de repeticiones)\n\n" + cost_vs_turn(rows, multi),
        "## Curva 3 · deriva de memoria frente a N\n\n" + drift_vs_turn(rows, multi),
        "## Adjuntos\n\n" + attachments(rows),
        "## Ráfaga\n\n" + burst(rows),
    ]
    return "\n\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    path = Path(args[0]) if args else DEFAULT_OUTPUT
    sys.stdout.write(render(load(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
