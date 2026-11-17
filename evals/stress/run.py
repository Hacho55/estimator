"""Runner del stress test (bloque 5): escenarios → ``POST /sessions/{id}/estimate`` → CSV.

    uv run python -m evals.stress.run --http http://localhost:8000 \\
        --scenarios growing,pivot,contradiction,attachments,burst \\
        --attachment-sizes 0,5,20,50,100 --repeats 2 --output evals/stress/results.csv

Sin ``--http`` usa ``TestClient`` en proceso (el mismo patrón que ``evals/``), con el canal
real del tier (JWT). Por cada turno lee ``observation`` de la respuesta (D17: sin parsear
logs), pide ``GET /sessions/{id}`` para la deriva y escribe una fila.

**Matriz por bloques (D20).** La literal del enunciado (escenarios × tamaños × repeats × 20
turnos) son ~900 turnos: con el modelo actual, ~25 h y ~60 USD. Por defecto:

- ``growing``, ``pivot``, ``contradiction``: 20 turnos sin adjunto, ``--repeats`` veces.
- ``attachments``: por cada tamaño, un turno con el PDF y una pregunta de seguimiento
  (¿sobrevive el pliego vía memoria?), ``--attachment-repeats`` veces.
- ``burst``: ``--burst-sessions`` primeros turnos idénticos a la vez (ráfaga).

``--matrix full`` cruza además cada escenario multi-turno con cada tamaño (el adjunto va en
el turno 1). ``--resume`` salta las unidades completas del CSV; ``--max-usd`` para la corrida.
"""

from __future__ import annotations

import argparse
import csv
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.stress.fixtures.build_pdfs import MARKERS, ensure_pdfs
from evals.stress.metrics import CostBudgetMetric, LatencyBudgetMetric, drift_for_turn
from evals.stress.scenarios import MULTI_TURN, Scenario, ScenarioTurn, get_scenario

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "results.csv"
ATTACHMENT_TRANSCRIPT = (
    "Adjunto el pliego del proyecto Faro: sistema de gestion de personal para una terminal portuaria. "
    "Estima el desarrollo teniendo en cuenta todos los requisitos obligatorios del documento."
)
ATTACHMENT_FOLLOW_UP = "Recuérdame qué requisitos obligatorios del pliego has tenido en cuenta."

OBSERVATION_FIELDS = (
    "turn_index",
    "session_id",
    "enriched_transcript_chars",
    "attachments_total_chars",
    "messages_in_window",
    "anchors_count",
    "summary_chars",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "latency_ms",
    "cache_hit_kind",
    "last_resolved_tier",
    "kind",
    "attachments_truncated",
    "dropped_turns",
    "cached_tokens",
    "estimate_ms",
    "extractor_ms",
    "tools_used",
    "review_decision",
    "actor_calls",
)
COLUMNS = (
    "tier",
    "scenario",
    "attachment_kb",
    "repeat",
    "concurrency",
    *OBSERVATION_FIELDS,
    "session_turn",
    "wall_ms",
    "latency_ok",
    "cost_ok",
    "drift_checked",
    "drift_passed",
    "drift_recall",
    "drift_ok",
    "first_fact_kept",
    "attachment_markers",
    "attachment_recall",
    "drift_failures",
    "status",
    "error",
)


@dataclass(frozen=True)
class Unit:
    """Una sesión del plan: escenario, tamaño del adjunto del turno 1 y repetición."""

    scenario: Scenario
    attachment_kb: int
    repeat: int
    concurrency: int = 1

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.scenario.name, str(self.attachment_kb), str(self.repeat))


def attachments_scenario() -> Scenario:
    """Turno 1 con el pliego; turno 2, una pregunta que solo se responde desde la memoria."""
    return Scenario(
        name="attachments",
        project_type="internal_tool",
        detail_level="medium",
        turns=(ScenarioTurn(ATTACHMENT_TRANSCRIPT), ScenarioTurn(ATTACHMENT_FOLLOW_UP)),
    )


def plan_units(
    scenarios: list[str],
    sizes: list[int],
    *,
    repeats: int,
    attachment_repeats: int,
    burst_sessions: int,
    matrix: str = "block",
) -> list[Unit]:
    units: list[Unit] = []
    for name in scenarios:
        if name in MULTI_TURN:
            scenario = get_scenario(name)
            for size in sizes if matrix == "full" else [0]:
                units += [Unit(scenario, size, repeat) for repeat in range(1, repeats + 1)]
        elif name == "attachments":
            scenario = attachments_scenario()
            for size in sizes:
                units += [Unit(scenario, size, repeat) for repeat in range(1, attachment_repeats + 1)]
        elif name == "burst":
            scenario = get_scenario("burst")
            units += [Unit(scenario, 0, repeat, concurrency=burst_sessions) for repeat in range(1, burst_sessions + 1)]
        else:
            get_scenario(name)  # Lanza el error con los nombres válidos.
    return units


# --- Transporte ------------------------------------------------------------------


@contextmanager
def open_client(http_base_url: str | None) -> Iterator[Any]:
    """``--http``: httpx contra un servidor. Sin él: ``TestClient`` en proceso."""
    if http_base_url:
        import httpx

        with httpx.Client(base_url=http_base_url, timeout=600.0) as client:
            yield client
        return
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client


def auth_headers(tier: str, secret: str | None) -> dict[str, str]:
    if not secret:
        return {}
    from evals.harness import token_for

    return {"Authorization": f"Bearer {token_for(tier, secret, user='stress')}"}


# --- Una sesión -------------------------------------------------------------------


@dataclass
class Settings:
    tier: str
    secret: str | None
    latency: LatencyBudgetMetric
    cost: CostBudgetMetric
    pdfs: dict[int, Path]


class StopRun(Exception):
    """El tope de gasto o un coste desconocido paran la corrida antes del siguiente turno."""


def run_unit(client, unit: Unit, settings: Settings, sink: CsvSink) -> int:
    """Recorre los turnos de una sesión. Cada turno se escribe y se cuenta en cuanto termina.

    Un 5xx del servidor queda en su fila y la sesión sigue (es una medición del sistema). Un
    4xx, un fallo de transporte o un tier distinto del pedido quedan en su fila y la cortan.
    Antes de cada turno se comprueba el presupuesto: nunca se paga un turno de más que ya
    se sabía que sobraba.
    """
    headers = auth_headers(settings.tier, settings.secret)
    written = 0
    try:
        created = client.post("/sessions", headers=headers)
    except Exception as exc:  # noqa: BLE001 - un fallo de transporte es una fila, no un traceback.
        sink.write([_error_row(unit, settings, 1, "transport", repr(exc))])
        return 1
    if created.status_code != 200:
        sink.write([_error_row(unit, settings, 1, str(created.status_code), created.text)])
        return 1
    session_id = created.json()["session_id"]
    for index, turn in enumerate(unit.scenario.turns, start=1):
        sink.check_budget()
        files = None
        if index == 1 and unit.attachment_kb > 0:
            data = settings.pdfs[unit.attachment_kb].read_bytes()
            files = [("attachments", (f"pliego_{unit.attachment_kb}kb.pdf", data, "application/pdf"))]
        form = {"transcript": turn.transcript}
        if index == 1:
            form |= {"project_type": unit.scenario.project_type, "detail_level": unit.scenario.detail_level}
        started = time.perf_counter()
        try:
            response = client.post(f"/sessions/{session_id}/estimate", data=form, files=files, headers=headers)
        except Exception as exc:  # noqa: BLE001
            sink.write([_error_row(unit, settings, index, "transport", repr(exc), session_id=session_id)])
            return written + 1
        wall_ms = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            sink.write([_error_row(unit, settings, index, str(response.status_code), response.text, session_id=session_id)])
            written += 1
            if response.status_code >= 500:
                # Fallo del sistema (p. ej. salida truncada): es una medición. El turno no entra en
                # el historial y la sesión sigue, como seguiría un usuario.
                continue
            return written
        body = response.json()
        snapshot, snapshot_error = _snapshot(client, session_id, headers)
        row = _row(unit, index, body, snapshot, wall_ms, settings)
        if snapshot_error:
            row["error"] = snapshot_error
        resolved = (body.get("observation") or {}).get("last_resolved_tier")
        if resolved != settings.tier:
            # Medir otro tier invalida la fila: el servidor no está en modo JWT o ignora el token.
            row["status"], row["error"] = "tier_mismatch", f"pedido {settings.tier}, resuelto {resolved}"
            sink.write([row])
            return written + 1
        sink.write([row])
        written += 1
    return written


def _snapshot(client, session_id: str, headers: dict) -> tuple[dict | None, str]:
    """``GET /sessions/{id}`` con un reintento. Sin snapshot no hay deriva: se marca, no se inventa."""
    last = ""
    for _ in range(2):
        try:
            response = client.get(f"/sessions/{session_id}", headers=headers)
        except Exception as exc:  # noqa: BLE001
            last = f"snapshot: {exc!r}"
            continue
        if response.status_code == 200:
            return response.json(), ""
        last = f"snapshot: {response.status_code}"
    return None, last


def _answer_text(body: dict) -> str:
    return " ".join(str(body.get(key) or "") for key in ("estimation", "message", "estimation_structured"))


def _row(unit: Unit, index: int, body: dict, snapshot: dict | None, wall_ms: float, settings: Settings) -> dict:
    observation = body.get("observation") or {}
    answer = _answer_text(body)
    row: dict[str, Any] = {
        "tier": settings.tier,
        "scenario": unit.scenario.name,
        "attachment_kb": unit.attachment_kb,
        "repeat": unit.repeat,
        "concurrency": unit.concurrency,
        **{name: observation.get(name) for name in OBSERVATION_FIELDS},
        "wall_ms": round(wall_ms),
        "latency_ok": int(settings.latency.evaluate(observation).passed),
        "cost_ok": int(settings.cost.evaluate(observation).passed),
        "status": 200,
        "error": "",
    }
    row["tools_used"] = ",".join(observation.get("tools_used") or [])
    # turn_index es el turno del escenario; el servidor no cuenta los turnos que fallaron.
    row["turn_index"] = index
    row["session_turn"] = observation.get("turn_index")
    if snapshot is not None:
        drift = drift_for_turn(unit.scenario, index, snapshot, answer)
        row |= {
            "drift_checked": drift.checked,
            "drift_passed": drift.passed,
            "drift_recall": "" if drift.recall is None else round(drift.recall, 3),
            "drift_ok": "" if drift.checked == 0 else int(drift.passed == drift.checked),
            "first_fact_kept": "" if drift.first_fact_kept is None else int(drift.first_fact_kept),
            "drift_failures": "; ".join(drift.failures),
        }
    if unit.attachment_kb > 0 and snapshot is not None:
        # Recall del pliego: en la respuesta o en la memoria (en el turno 2 solo queda la memoria).
        memory = str((snapshot or {}).get("project_metadata") or "")
        haystack = (answer + " " + memory).lower()
        found = [marker.token for marker in MARKERS if marker.token.lower() in haystack]
        row["attachment_markers"] = ",".join(found)
        row["attachment_recall"] = round(len(found) / len(MARKERS), 3)
    return row


def _error_row(unit: Unit, settings: Settings, index: int, status: str, text: str, *, session_id: str = "") -> dict:
    return {
        "tier": settings.tier,
        "scenario": unit.scenario.name,
        "attachment_kb": unit.attachment_kb,
        "repeat": unit.repeat,
        "concurrency": unit.concurrency,
        "turn_index": index,
        "session_id": session_id,
        "status": status,
        "error": text[:300].replace("\n", " "),
    }


# --- CSV, gasto y orquestación ------------------------------------------------------


def _measured(row: dict) -> bool:
    status = str(row.get("status"))
    return (status == "200" and not row.get("error")) or status.startswith("5")


def _unit_key(row: dict) -> tuple[str, str, str, str]:
    return (row["scenario"], str(row["attachment_kb"]), str(row["repeat"]), str(row["concurrency"]))


class CsvSink:
    """CSV en append, una fila por turno, y el gasto de la corrida.

    El gasto cuenta **todo turno pagado**, también los de sesiones que luego se repiten: al
    reanudar, las filas de sesiones incompletas se mueven a ``<salida>.discarded.csv`` (no se
    pierden) y siguen sumando. Un turno con coste desconocido para la corrida salvo
    ``allow_unknown_cost``: sin precio no se puede saber si se pasó el tope.
    """

    def __init__(
        self,
        path: Path,
        *,
        resume: bool,
        tier: str | None = None,
        max_usd: float = float("inf"),
        allow_unknown_cost: bool = False,
    ) -> None:
        self.path = path
        self.discarded_path = path.with_suffix(".discarded.csv")
        self.lock = threading.Lock()
        self.max_usd = max_usd
        self.allow_unknown_cost = allow_unknown_cost
        self.done: set[tuple[str, str, str, str]] = set()
        self.spent = 0.0
        self.unknown_cost = 0
        self.written = 0  # Filas nuevas de esta corrida.
        rows = _read_rows(path) if resume else []
        if resume:
            other = {row.get("tier") for row in rows} - {tier}
            if rows and other:
                raise SystemExit(f"{path} tiene filas de otro tier ({', '.join(sorted(map(str, other)))}); usa otra salida.")
            expected = _expected_turns()
            by_unit: dict[tuple[str, str, str, str], list[dict]] = {}
            for row in rows:
                by_unit.setdefault(_unit_key(row), []).append(row)
            self.done = {
                key
                for key, items in by_unit.items()
                # Completa = todos los turnos medidos: 200 sin error de snapshot, o un 5xx del sistema.
                if all(_measured(item) for item in items) and len(items) == expected.get(key[0], -1)
            }
            dropped = [row for row in rows if _unit_key(row) not in self.done]
            if dropped:
                _append(self.discarded_path, dropped)
            rows = [row for row in rows if _unit_key(row) in self.done]
            for row in [*rows, *_read_rows(self.discarded_path)]:
                self._account(row)
        elif self.discarded_path.exists():
            self.discarded_path.unlink()  # Corrida nueva: el registro de descartes empieza con ella.
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def _account(self, row: dict) -> None:
        status = str(row.get("status"))
        if status != "200" and not row.get("tokens_in"):
            # Sin sesión o rechazo 4xx: no llegó al modelo. Otro fallo (5xx, transporte) pudo
            # pagarse sin que se sepa cuánto: cuenta como coste desconocido.
            if not row.get("session_id") or status.startswith("4"):
                return
            self.unknown_cost += 1
            return
        cost = row.get("cost_usd")
        if cost in (None, ""):
            self.unknown_cost += 1
        else:
            self.spent += float(cost)

    def write(self, rows: list[dict]) -> None:
        with self.lock:
            _append(self.path, rows)
            self.written += len(rows)
            for row in rows:
                self._account(row)

    def check_budget(self) -> None:
        with self.lock:
            if self.spent >= self.max_usd:
                raise StopRun(f"Tope de gasto alcanzado ({self.spent:.2f} USD ≥ {self.max_usd}).")
            if self.unknown_cost and not self.allow_unknown_cost:
                raise StopRun(f"{self.unknown_cost} turnos sin coste conocido: no se puede vigilar el tope. Usa --allow-unknown-cost para seguir.")


def _append(path: Path, rows: list[dict]) -> None:
    exists = path.is_file() and path.stat().st_size > 0
    if exists:
        with path.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
        if tuple(header) != COLUMNS:
            # Cabecera de otra versión del runner: se reescribe por nombre de columna, sin desplazar campos.
            rows = [*_read_rows(path), *rows]
            exists = False
            path.unlink()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def _read_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _expected_turns() -> dict[str, int]:
    turns = {name: len(get_scenario(name).turns) for name in (*MULTI_TURN, "burst")}
    turns["attachments"] = len(attachments_scenario().turns)
    return turns


def run_units(client, units: list[Unit], settings: Settings, sink: CsvSink, *, log: Callable[[str], None] = print) -> int:
    """Ejecuta las unidades pendientes. Devuelve las filas escritas.

    La ráfaga comprueba el tope antes de lanzarse; sus N turnos van a la vez, así que puede
    pasarse como mucho en N turnos (acotado por ``--burst-sessions``).
    """
    pending = [unit for unit in units if (unit.scenario.name, str(unit.attachment_kb), str(unit.repeat), str(unit.concurrency)) not in sink.done]
    before = sink.written
    sequential = [unit for unit in pending if unit.concurrency == 1]
    burst = [unit for unit in pending if unit.concurrency > 1]
    try:
        for number, unit in enumerate(sequential, start=1):
            started = time.perf_counter()
            rows = run_unit(client, unit, settings, sink)
            log(
                f"[{number}/{len(sequential)}] {unit.scenario.name} {unit.attachment_kb} KB r{unit.repeat}: "
                f"{rows} filas, {time.perf_counter() - started:.0f} s, acumulado {sink.spent:.2f} USD"
            )
        if burst:
            sink.check_budget()
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=burst[0].concurrency) as pool:
                futures = [pool.submit(run_unit, client, unit, settings, sink) for unit in burst]
                for future in as_completed(futures):
                    future.result()
            log(f"[ráfaga] {len(burst)} sesiones a la vez: {time.perf_counter() - started:.0f} s, acumulado {sink.spent:.2f} USD")
    except StopRun as exc:
        log(f"{exc} Se para; --resume para seguir.")
    return sink.written - before


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--http", help="URL del estimador. Sin ella, TestClient en proceso.")
    parser.add_argument("--scenarios", default="growing,pivot,contradiction,attachments,burst")
    parser.add_argument("--attachment-sizes", default="0,5,20,50,100")
    parser.add_argument("--repeats", type=int, default=2, help="Repeticiones de cada escenario multi-turno.")
    parser.add_argument("--attachment-repeats", type=int, default=3)
    parser.add_argument("--burst-sessions", type=int, default=10)
    parser.add_argument("--matrix", choices=("block", "full"), default="block")
    parser.add_argument("--tier", default="developer", choices=("developer", "pm", "executive"))
    parser.add_argument("--latency-budget-ms", type=float, default=25_000)
    parser.add_argument("--cost-budget-usd", type=float, default=0.05)
    parser.add_argument("--max-usd", type=float, default=15.0, help="Para antes del siguiente turno al llegar aquí.")
    parser.add_argument("--allow-unknown-cost", action="store_true", help="Sigue aunque un turno no tenga precio.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume", action="store_true", help="Conserva las unidades completas del CSV.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.http:
        # En proceso: el canal real del tier con un secreto propio, siempre (con AUTH_MODE=off el
        # token se ignoraría y se mediría DEFAULT_TIER). Las cachés quedan como en la app (D22).
        os.environ["AUTH_MODE"] = "jwt"
        os.environ["AI_SERVICE_SECRET"] = secrets.token_hex(32)
    from app.config import get_settings

    get_settings.cache_clear()
    app_settings = get_settings()
    # En proceso se firma con el secreto recién generado; STRESS_TOKEN_SECRET solo vale contra --http.
    override = os.environ.get("STRESS_TOKEN_SECRET") if args.http else None
    secret = override or app_settings.AI_SERVICE_SECRET.get_secret_value() or None
    sizes = [int(item) for item in args.attachment_sizes.split(",") if item.strip()]
    units = plan_units(
        [item.strip() for item in args.scenarios.split(",") if item.strip()],
        sizes,
        repeats=args.repeats,
        attachment_repeats=args.attachment_repeats,
        burst_sessions=args.burst_sessions,
        matrix=args.matrix,
    )
    settings = Settings(
        tier=args.tier,
        secret=secret,
        latency=LatencyBudgetMetric(args.latency_budget_ms),
        cost=CostBudgetMetric(args.cost_budget_usd),
        pdfs=ensure_pdfs(tuple(size for size in sizes if size > 0)),
    )
    turns = sum(len(unit.scenario.turns) for unit in units)
    print(f"{len(units)} sesiones, {turns} turnos previstos, tier {args.tier}, salida {args.output}")
    sink = CsvSink(args.output, resume=args.resume, tier=args.tier, max_usd=args.max_usd, allow_unknown_cost=args.allow_unknown_cost)
    if sink.done:
        print(f"Reanudando: {len(sink.done)} sesiones ya completas; gastado {sink.spent:.2f} USD.")
    with open_client(args.http) as client:
        written = run_units(client, units, settings, sink)
    print(f"Filas nuevas: {written}. Gasto total: {sink.spent:.2f} USD; turnos sin precio: {sink.unknown_cost}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
