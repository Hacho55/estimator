"""Tablas de la segunda corrida (plan §6): bloques A, B y C, y la viabilidad de cada punto.

    uv run python -m evals.stress.context_report

Lee ``results_context_a.csv`` (A, por sesiones) y ``results_context.csv`` (B y C). Los
umbrales de viabilidad se fijaron antes de correr: latencia ≤ 25 s y coste ≤ 0,05 USD en la
mediana (en C, en caliente), recuperación ≥ 80 % en B o la aguja citada en ≥ 2/3 en C, y
ningún fallo duro.
"""

from __future__ import annotations

import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from evals.stress.fixtures.build_pdfs import DENSE_MARKERS
from evals.stress.report import _fmt, _table, drift_causes, load

HERE = Path(__file__).resolve().parent
LATENCY_S, COST_USD, RECALL_B, CITED_C = 25.0, 0.05, 0.8, 2 / 3


def _num(value) -> float | None:
    return None if value in (None, "") else float(value)


def _median(values: list[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return statistics.median(known) if known else None


def _mean(values: list[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return statistics.fmean(known) if known else None


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.0f} %"


def _ok(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("status") == "200"]


# --- A ------------------------------------------------------------------------------


def block_a(rows: list[dict]) -> str:
    body = []
    for variant in sorted({row["variant"] for row in rows}, key=lambda name: int(name[1:]) if name[1:].isdigit() else 0):
        items = [row for row in rows if row["variant"] == variant]
        ok = _ok(items)
        failed = Counter(_code(row) for row in items if row["status"] != "200")
        last = [row for row in ok if row["turn_index"] == "20"]
        body.append(
            [
                variant,
                str(len(items)),
                ", ".join(f"{count} {code}" for code, count in failed.most_common()) or "0",
                _fmt(_median([_num(row["latency_ms"]) for row in ok]), 1, 1000),
                _fmt(max((_num(row["tokens_out"]) or 0 for row in ok), default=None), 0),
                _fmt(sum(_num(row["cost_usd"]) or 0 for row in ok), 2),
                _pct(_mean([_num(row["drift_recall"]) for row in ok])),
                _pct(_num(last[0]["drift_recall"])) if last else ("falló" if any(row["turn_index"] == "20" for row in items) else "—"),
            ]
        )
    header = ["Variante", "Turnos", "Fallos", "P50 s", "Máx. `tokens_out`", "USD conocidos", "Recall medio", "Recall en el turno 20"]
    causes = {
        variant: drift_causes([row for row in rows if row["variant"] == variant], ["pivot"]) for variant in sorted({row["variant"] for row in rows})
    }
    parts = [_table(header, body)]
    parts += [f"Causa de la deriva, {variant}:\n\n{text}" for variant, text in causes.items()]
    return "\n\n".join(parts)


def _code(row: dict) -> str:
    text = row.get("error") or ""
    return text.split('"error":"', 1)[1].split('"', 1)[0] if '"error":"' in text else str(row.get("status"))


# --- B ------------------------------------------------------------------------------


def block_b(rows: list[dict]) -> str:
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in rows:
        if row["block"] == "B":
            groups[(row["variant"], int(row["size"]))].append(row)
    body = []
    for (variant, size), items in sorted(groups.items()):
        ok = _ok(items)
        outcomes = Counter(row["rejection_origin"] or "200" for row in items)
        tokens = _median([_num(row["actor_input_tokens"]) for row in items])
        body.append(
            [
                variant,
                str(size),
                ", ".join(f"{count} {name}" for name, count in outcomes.most_common()),
                _fmt(tokens, 0),
                _fmt(_median([_num(row["latency_ms"]) for row in ok]), 1, 1000),
                _fmt(_median([_num(row["cost_usd"]) for row in ok]), 3),
                f"{_fmt(_median([_num(row['markers_visible']) for row in items]), 0)} / 9",
                _pct(_mean([_num(row["answer_recall"]) for row in ok])),
                _pct(_mean([_num(row["memory_recall"]) for row in ok])),
                _viable_b(items),
            ]
        )
    header = [
        "Variante",
        "KB",
        "Resultado",
        "Tokens del actor",
        "P50 s",
        "USD (mediana)",
        "Visibles",
        "Recall respuesta",
        "Recall memoria",
        "¿Viable?",
    ]
    return _table(header, body) + "\n\n" + _positions_b(rows)


def _viable_b(items: list[dict]) -> str:
    ok = _ok(items)
    if len(ok) < len(items):
        return "no: fallo duro"
    return _verdict(
        _median([_num(row["latency_ms"]) for row in ok]),
        _median([_num(row["cost_usd"]) for row in ok]),
        _mean([_num(row["answer_recall"]) for row in ok]),
        RECALL_B,
    )


def _positions_b(rows: list[dict]) -> str:
    """Recall por posición del marcador. El truncado conserva el principio: visibles = los k primeros."""
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in _ok(rows):
        if row["block"] == "B":
            groups[(row["variant"], int(row["size"]))].append(row)
    body = []
    for (variant, size), items in sorted(groups.items()):
        cells = [variant, str(size)]
        for index, marker in enumerate(DENSE_MARKERS):
            seen = [row for row in items if int(row["markers_visible"] or 0) > index]
            found = [row for row in seen if marker.token not in (row["missing_markers"] or "").split(",")]
            cells.append("—" if not seen else f"{len(found)}/{len(seen)}")
        body.append(cells)
    header = ["Variante", "KB", *[f"{int(marker.position * 100)} %" for marker in DENSE_MARKERS]]
    return "Requisitos citados por posición en el pliego (citados / visibles):\n\n" + _table(header, body)


# --- C ------------------------------------------------------------------------------


def block_c(rows: list[dict]) -> str:
    c_rows = [row for row in rows if row["block"] == "C"]
    control = [row for row in _ok(c_rows) if row["mode"] == "control"]
    lines = [
        f"Control sin aguja (N = 5, 3 repeticiones): {', '.join(row['total_hours'] for row in control)} horas. "
        f"Aguja: {next((row['needle_hours'] for row in c_rows if row['needle_hours']), '—')} horas."
    ]
    capped = [row for row in c_rows if row["mode"] == "capped"]
    body = [
        [
            row["size"],
            row["position"],
            "sí" if row["needle_included"] == "1" else "no",
            "sí" if row["needle_cited"] == "1" else ("—" if row["status"] != "200" else "no"),
            row["total_hours"] or row["status"],
            _fmt(_num(row["context_tokens"]), 0),
        ]
        for row in sorted(capped, key=lambda item: (int(item["size"]), item["position"]))
    ]
    lines.append(
        "Modo **tal cual** (topes por defecto, 7 ejemplos por orden de la lista):\n\n"
        + _table(["N", "Aguja", "¿Entra?", "¿Citada?", "Horas", "Tokens de contexto"], body)
    )
    lines.append("Modo **sin topes** (todo el corpus en el prompt):\n\n" + _free_table(c_rows))
    return "\n\n".join(lines)


def _free_table(rows: list[dict]) -> str:
    groups: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        if row["mode"] in ("free", "free_control"):
            groups[int(row["size"])].append(row)
    body = []
    base = None
    for size, items in sorted(groups.items()):
        needle_rows = [row for row in items if row["mode"] == "free"]
        cold = [row for row in _ok(needle_rows) if row["temperature"] == "cold" and row["cache_state"] == "cold"]
        # Calientes: todas las planificadas como tales (lo que se paga de verdad) y, aparte, las que acertaron.
        warm = [row for row in _ok(needle_rows) if row["temperature"] == "warm"]
        warm_hit = [row for row in warm if row["cache_state"] == "warm"]
        cited = [_num(row["needle_cited"]) for row in _ok(needle_rows)]
        failures = Counter(row["rejection_origin"] for row in items if row["status"] != "200")
        cold_cost = _median([_num(row["cost_usd"]) for row in cold])
        base = base or cold_cost  # El N más pequeño es la línea base del coste.
        body.append(
            [
                str(size),
                _fmt(_median([_num(row["context_tokens"]) for row in items]), 0),
                _fmt(_median([_num(row["actor_input_tokens"]) for row in items]), 0),
                _fmt(_median([_num(row["latency_ms"]) for row in cold]), 1, 1000),
                _fmt(_median([_num(row["latency_ms"]) for row in warm]), 1, 1000),
                _fmt(cold_cost, 3),
                _fmt(cold_cost / base, 1) + "×" if cold_cost and base else "—",
                _fmt(_median([_num(row["cost_usd"]) for row in warm]), 3),
                f"{len(warm_hit)}/{len(warm)}" if warm else "—",
                _fmt(_median([_num(row["cost_usd"]) for row in warm_hit]), 3),
                f"{sum(1 for value in cited if value)}/{len(cited)}" if cited else "—",
                _pct(_mean([_num(row["hours_shift"]) for row in _ok(needle_rows)])),
                ", ".join(f"{count} {name}" for name, count in failures.items()) or "0",
                _viable_c(items),
            ]
        )
    header = [
        "N", "Tokens de contexto", "Tokens del actor", "P50 s frío", "P50 s caliente", "USD frío", "Frío vs N mínimo",
        "USD caliente", "Aciertos de caché", "USD con acierto",
        "Aguja citada", "Horas vs control", "Fallos", "¿Viable?",
    ]  # fmt: skip
    return _table(header, body) + "\n\n" + _positions_c(rows)


def _viable_c(items: list[dict]) -> str:
    needle_rows = [row for row in items if row["mode"] == "free"]
    if any(row["status"] != "200" for row in items):
        return "no: fallo duro"
    warm = [row for row in needle_rows if row["temperature"] == "warm"]
    cited = [_num(row["needle_cited"]) or 0 for row in needle_rows]
    return _verdict(
        _median([_num(row["latency_ms"]) for row in warm]),
        _median([_num(row["cost_usd"]) for row in warm]),
        sum(cited) / len(cited) if cited else None,
        CITED_C,
    )


def _verdict(latency_ms: float | None, cost: float | None, recall: float | None, threshold: float) -> str:
    """Sin muestras de alguna condición no se declara viable: falta evidencia, no es cero."""
    if latency_ms is None or cost is None or recall is None:
        return "datos insuficientes"
    reasons = [
        name
        for name, failed in (("latencia", latency_ms / 1000 > LATENCY_S), ("coste", cost > COST_USD), ("recuperación", recall < threshold))
        if failed
    ]
    return "sí" if not reasons else "no: " + ", ".join(reasons)


def _positions_c(rows: list[dict]) -> str:
    groups: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for row in _ok(rows):
        if row["block"] == "C" and row["mode"] == "free":
            groups[(int(row["size"]), row["position"])].append(row)
    sizes = sorted({size for size, _ in groups})
    body = []
    for size in sizes:
        cells = [str(size)]
        for position in ("start", "middle", "end"):
            items = groups.get((size, position), [])
            cited = sum(1 for row in items if row["needle_cited"] == "1")
            cells.append(f"{cited}/{len(items)}" if items else "—")
        body.append(cells)
    return "Aguja citada por posición (sin topes, frío y caliente juntos):\n\n" + _table(["N", "Principio", "Medio", "Final"], body)


def render(a_rows: list[dict], bc_rows: list[dict]) -> str:
    parts = []
    if a_rows:
        parts.append("### A · Atribución del truncado (`pivot`, 8 k frente a 16 k de salida)\n\n" + block_a(a_rows))
    if any(row["block"] == "B" for row in bc_rows):
        parts.append("### B · Contexto de entrada (pliegos de 50 a 1.000 KB)\n\n" + block_b(bc_rows))
    if any(row["block"] == "C" for row in bc_rows):
        parts.append("### C · Corpus del CAG (aguja entre N estimaciones)\n\n" + block_c(bc_rows))
    known = sum(_num(row["cost_usd"]) or 0 for row in [*a_rows, *bc_rows])
    unknown = sum(1 for row in a_rows if row["status"].startswith("5")) + sum(
        1 for row in bc_rows if not row.get("cost_usd") and row.get("rejection_origin") not in ("local", "client")
    )
    parts.append(f"Gasto conocido de la segunda corrida: {known:.2f} USD; llamadas pagadas sin precio: {unknown}.")
    return "\n\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    a_path = Path(args[0]) if args else HERE / "results_context_a.csv"
    bc_path = Path(args[1]) if len(args) > 1 else HERE / "results_context.csv"
    a_rows = load(a_path) if a_path.is_file() else []
    bc_rows = load(bc_path) if bc_path.is_file() else []
    sys.stdout.write(render(a_rows, bc_rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
