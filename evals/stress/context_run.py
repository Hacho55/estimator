"""Segunda corrida del stress test: llevar el contexto al límite (plan, §6, bloques B y C).

    uv run python -m evals.stress.context_run --block B --variant B1   # pliegos grandes, guarda tal cual
    uv run python -m evals.stress.context_run --block B --variant B2   # ventana configurada sin tope
    uv run python -m evals.stress.context_run --block C                # corpus del CAG con aguja
    uv run python -m evals.stress.context_run --block C --dry-run      # tokens por N, sin modelo

Una variante por proceso: los topes se fijan con variables de entorno antes de leer la
configuración (D24) y no tocan ``.env`` ni los valores por defecto. Siempre en proceso.

- **B** pasa por ``POST /sessions/{id}/estimate`` (los adjuntos solo existen ahí) con los PDFs de
  marcadores densos (D27). El recall se mide sobre la respuesta del actor, con denominador en
  los marcadores que el actor pudo ver; la memoria, aparte, sobre los que ve el extractor.
- **C** llama a ``generate_estimation`` con el corpus sintético (D25, D26), sin herramientas,
  en frío y en caliente (D28). Antes, un control sin aguja en N = 5 fija las horas de la aguja.

Cada llamada borra la caché de respuestas; una fila con acierto de caché se invalida.
``--max-usd`` para antes de la siguiente llamada.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import secrets
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "results_context.csv"

# Presets de la segunda corrida (D24). Sin reintentos: cada fila es un intento y su latencia.
_COMMON = {"LLM_TIMEOUT_SECONDS": "300", "LLM_MAX_RETRIES": "0", "SEMANTIC_CACHE_MODE": "off"}
PRESETS: dict[str, dict[str, str]] = {
    "B1": {**_COMMON, "LLM_MAX_TOKENS": "16000", "ATTACHMENT_MAX_CHARS": "1000000"},
    "B2": {**_COMMON, "LLM_MAX_TOKENS": "16000", "ATTACHMENT_MAX_CHARS": "1000000", "MODEL_CONTEXT_WINDOW": "1000000"},
    "C": {
        **_COMMON,
        "LLM_MAX_TOKENS": "16000",
        "MODEL_CONTEXT_WINDOW": "1000000",
        "BUSINESS_BACKEND_URL": "",  # Sin herramientas: el corpus es la única referencia.
        "TOOLS_WEB_SEARCH": "false",
    },
}

B_TRANSCRIPT = (
    "Adjunto el pliego del proyecto Faro: sistema de gestion de personal para una terminal portuaria. "
    "Estima el desarrollo y enumera en los supuestos, por su nombre, todos los requisitos obligatorios "
    "del pliego que has tenido en cuenta."
)

COLUMNS = (
    "block", "variant", "mode", "size", "position", "temperature", "repeat",
    "status", "error_code", "rejection_origin", "error",
    "actor_input_tokens", "tokens_in", "tokens_out", "cached_tokens", "cache_creation_tokens", "cache_state",
    "cost_usd", "latency_ms", "wall_ms", "cache_hit",
    "attachment_chars", "attachment_truncated", "markers_visible", "markers_found", "answer_recall",
    "memory_visible", "memory_found", "memory_recall", "missing_markers",
    "context_tokens", "needle_included", "needle_hours", "total_hours", "needle_cited", "hours_shift", "nonce",
)  # fmt: skip


class StopRun(Exception):
    pass


class Ledger:
    """CSV en append y gasto acumulado. ``done`` deja reanudar sin repetir llamadas pagadas."""

    def __init__(self, path: Path, *, resume: bool, max_usd: float) -> None:
        self.path = path
        self.max_usd = max_usd
        path.parent.mkdir(parents=True, exist_ok=True)  # Antes de pagar nada.
        if not resume and path.exists() and path.stat().st_size > 0:
            # B1, B2 y C comparten salida: sin --resume no se borra lo ya pagado.
            raise SystemExit(f"{path} ya tiene filas: usa --resume para añadir o --output para otra salida.")
        rows = _read(path) if resume else []
        self.rows = rows
        self.spent = sum(float(row["cost_usd"]) for row in rows if row.get("cost_usd"))
        self.unknown = sum(1 for row in rows if not row.get("cost_usd") and _maybe_paid(row))
        self.done = {_key(row) for row in rows}

    def write(self, row: dict) -> None:
        exists = self.path.is_file() and self.path.stat().st_size > 0
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
            if not exists:
                writer.writeheader()
            writer.writerow(row)
        self.rows.append({key: "" if row.get(key) is None else str(row.get(key)) for key in COLUMNS})
        self.done.add(_key(row))
        if row.get("cost_usd") not in (None, ""):
            self.spent += float(row["cost_usd"])
        elif _maybe_paid(row):
            self.unknown += 1

    def check(self) -> None:
        if self.spent >= self.max_usd:
            raise StopRun(f"Tope de gasto alcanzado ({self.spent:.2f} USD ≥ {self.max_usd}).")


def _maybe_paid(row: dict) -> bool:
    """Toda llamada que llegó al modelo pudo cobrarse; un rechazo local (413) o de cliente, no.

    Sin precio, se cuenta como desconocido (se informa al final; la segunda corrida lo acepta,
    como ``--allow-unknown-cost`` en la primera).
    """
    return row.get("rejection_origin") not in ("local", "client")


def _key(row: dict) -> tuple[str, ...]:
    return tuple(str(row.get(name) or "") for name in ("block", "variant", "mode", "size", "position", "temperature", "repeat"))


def _read(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def apply_preset(variant: str) -> None:
    for key, value in PRESETS[variant].items():
        os.environ[key] = value
    from app.config import get_settings

    get_settings.cache_clear()


def rejection_origin(status: int | str, code: str) -> str:
    if str(status) == "200":
        return ""
    if code == "context_budget_exceeded":
        return "local"
    if code == "llm_timeout":
        return "timeout"
    if code == "llm_output_truncated":
        return "truncated"
    if code == "invalid_llm_output":
        return "invalid_output"  # El modelo respondió, pero su salida no valió: local, no del proveedor.
    if code == "llm_unavailable":
        return "provider"
    return "client" if str(status).startswith("4") else "unknown"


def _error_code(text: str) -> str:
    try:
        return str(json.loads(text).get("error") or "")
    except (ValueError, AttributeError):
        return ""


def cache_state(temperature: str, cached: int, created: int | None) -> str:
    """D28: frío sin tokens cacheados; caliente con ellos. Si no cuadra, la muestra es parcial."""
    if temperature == "cold":
        return "cold" if cached == 0 else "partial"
    if temperature == "warm":
        return "warm" if cached > 0 else "partial"
    return ""


# --- Bloque B -----------------------------------------------------------------------


def visible_markers(pdf: Path, cap: int, extractor_chars: int) -> tuple[list[str], list[str]]:
    """Marcadores que sobreviven a la extracción y al truncado (actor) y los que ve el extractor."""
    from app.attachments.extractor import extract_text
    from evals.stress.fixtures.build_pdfs import DENSE_MARKERS

    item = extract_text(pdf.name, pdf.read_bytes(), max_chars=cap)
    text = item.text.lower()
    actor = [marker.token for marker in DENSE_MARKERS if marker.token.lower() in text]
    memory = [marker.token for marker in DENSE_MARKERS if marker.token.lower() in text[:extractor_chars]]
    return actor, memory


def run_b(client, variant: str, sizes: list[int], repeats: int, ledger: Ledger, headers: dict, log: Callable[[str], None]) -> None:
    from app.config import get_settings
    from app.state.response_cache import clear_response_cache
    from evals.stress.fixtures.build_pdfs import ensure_pdfs

    settings = get_settings()
    pdfs = ensure_pdfs(tuple(sizes), dense=True)
    for size in sizes:
        actor_visible, memory_visible = visible_markers(pdfs[size], settings.ATTACHMENT_MAX_CHARS, settings.METADATA_ATTACHMENT_CHARS)
        for repeat in range(1, repeats + 1):
            base = {"block": "B", "variant": variant, "mode": "attachment", "size": size, "position": "", "temperature": "", "repeat": repeat}
            if _key(base) in ledger.done:
                continue
            ledger.check()
            clear_response_cache()
            session = client.post("/sessions", headers=headers).json()["session_id"]
            files = [("attachments", (f"pliego_{size}kb.pdf", pdfs[size].read_bytes(), "application/pdf"))]
            form = {"transcript": B_TRANSCRIPT, "project_type": "internal_tool", "detail_level": "medium"}
            started = time.perf_counter()
            try:
                response = client.post(f"/sessions/{session}/estimate", data=form, files=files, headers=headers)
            except Exception as exc:  # noqa: BLE001 - un fallo de transporte es una fila.
                ledger.write(base | {"status": "transport", "rejection_origin": "transport", "error": repr(exc)[:300]})
                continue
            wall = (time.perf_counter() - started) * 1000
            row = base | {
                "wall_ms": round(wall),
                "status": response.status_code,
                "markers_visible": len(actor_visible),
                "memory_visible": len(memory_visible),
            }
            if response.status_code != 200:
                code = _error_code(response.text)
                estimate = re.search(r"Entrada aproximada: (\d+)", response.text)
                row |= {
                    "error_code": code,
                    "rejection_origin": rejection_origin(response.status_code, code),
                    "error": response.text[:300].replace("\n", " "),
                    "actor_input_tokens": estimate.group(1) if estimate else "",
                }
                ledger.write(row)
                log(f"B {variant} {size} KB r{repeat}: {response.status_code} {code}, {wall / 1000:.0f} s")
                continue
            body = response.json()
            observation = body.get("observation") or {}
            snapshot_response = client.get(f"/sessions/{session}", headers=headers)
            snapshot = snapshot_response.json() if snapshot_response.status_code == 200 else None
            answer = json.dumps({key: body.get(key) for key in ("estimation", "message", "estimation_structured")}, ensure_ascii=False).lower()
            found = [token for token in actor_visible if token.lower() in answer]
            memory = json.dumps((snapshot or {}).get("project_metadata") or {}, ensure_ascii=False).lower()
            in_memory = [token for token in memory_visible if token.lower() in memory] if snapshot else []
            row |= _observed(observation) | {
                "attachment_chars": observation.get("attachments_total_chars"),
                "attachment_truncated": observation.get("attachments_truncated"),
                "markers_found": len(found),
                "answer_recall": round(len(found) / len(actor_visible), 3) if actor_visible else "",
                "memory_found": len(in_memory) if snapshot else "",
                "memory_recall": round(len(in_memory) / len(memory_visible), 3) if memory_visible and snapshot else "",
                "missing_markers": ",".join(token for token in actor_visible if token not in found),
            }
            if snapshot is None:
                row["error"] = f"snapshot: {snapshot_response.status_code}"
            if row["cache_hit"]:
                row["status"], row["error"] = "cache_hit", "acierto de caché: no mide el actor"
            ledger.write(row)
            log(
                f"B {variant} {size} KB r{repeat}: {row['actor_input_tokens']} tokens, {row['latency_ms']} ms, "
                f"{row['cost_usd']} USD, recall {row['answer_recall']}; acumulado {ledger.spent:.2f} USD"
            )


def _observed(observation: dict) -> dict:
    return {
        "actor_input_tokens": observation.get("actor_input_tokens"),
        "tokens_in": observation.get("tokens_in"),
        "tokens_out": observation.get("tokens_out"),
        "cached_tokens": observation.get("cached_tokens"),
        "cache_creation_tokens": observation.get("cache_creation_tokens"),
        "cost_usd": observation.get("cost_usd"),
        "latency_ms": round(observation.get("latency_ms") or 0),
        "cache_hit": int(observation.get("cache_hit_kind") not in (None, "none")),
    }


# --- Bloque C -----------------------------------------------------------------------


def total_hours(result) -> float | None:
    estimation = getattr(result, "estimation", None)
    work = getattr(estimation, "work", None)
    if work:
        return float(sum(item.hours for item in work))
    hours = getattr(estimation, "total_hours", None)
    return float(hours) if hours is not None else None


def _answer(result) -> str:
    parts = []
    for item in (getattr(result, "estimation", None), getattr(result, "view", None)):
        if item is not None and hasattr(item, "model_dump_json"):
            parts.append(item.model_dump_json())
    parts.append(str(getattr(result, "message", "") or ""))
    return " ".join(parts).lower()


async def _call_c(items: list[dict], free: bool):
    from app.context.preprocessing import load_estimates
    from app.services.llm_service import ContextOverrides, generate_estimation
    from app.state.response_cache import clear_response_cache
    from evals.stress.fixtures.build_corpus import QUERY_TRANSCRIPT

    clear_response_cache()
    overrides = ContextOverrides(
        tier="developer",
        prompt_version="v4",
        project_type="internal_tool",
        estimates=load_estimates(items),
        max_examples=10_000 if free else None,
        context_max_tokens=10_000_000 if free else None,
    )
    return await generate_estimation(QUERY_TRANSCRIPT, overrides=overrides)


def corpus_plan(sizes: list[int], positions: list[str], capped_sizes: list[int], extra: list[int]) -> list[dict]:
    """Llamadas del bloque C en orden. ``needle_hours`` se rellena tras el control."""
    plan = [{"mode": "control", "size": 5, "position": "", "temperature": "cold", "repeat": repeat} for repeat in (1, 2, 3)]
    for size in capped_sizes:
        plan += [{"mode": "capped", "size": size, "position": position, "temperature": "cold", "repeat": 1} for position in positions]
    for size in sizes:
        plan.append({"mode": "free_control", "size": size, "position": "", "temperature": "cold", "repeat": 1})
        for position in positions:
            plan += [{"mode": "free", "size": size, "position": position, "temperature": temp, "repeat": 1} for temp in ("cold", "warm")]
    for size in extra:
        for position in positions:
            plan += [{"mode": "free", "size": size, "position": position, "temperature": "cold", "repeat": repeat} for repeat in (2, 3)]
    return plan


def run_c(plan: list[dict], ledger: Ledger, log: Callable[[str], None]) -> None:
    # Un solo event loop para toda la corrida: el cliente del modelo se crea una vez y queda
    # atado a su loop; ``asyncio.run`` por llamada lo cerraría tras la primera.
    with asyncio.Runner() as runner:
        _run_c(plan, ledger, log, runner)


def _run_c(plan: list[dict], ledger: Ledger, log: Callable[[str], None], runner: asyncio.Runner) -> None:
    from app.context.preprocessing import build_context_block, load_estimates
    from app.services.errors import ServiceError
    from app.tokens import count_tokens
    from evals.stress.fixtures.build_corpus import NEEDLE_TOKEN, corpus

    nonce = {  # Al reanudar, la muestra caliente reutiliza el corpus exacto de su fría.
        (row["mode"], int(row["size"]), row["position"], int(row["repeat"])): row["nonce"]
        for row in ledger.rows
        if row.get("block") == "C" and row.get("temperature") == "cold" and row.get("nonce")
    }
    for step in plan:
        base = {"block": "C", "variant": "C", **step}
        control_hours = _control_hours(ledger.rows)
        needle_hours = None if step["mode"] in ("control", "free_control") else _needle_hours(control_hours)
        if _key(base) in ledger.done:
            continue
        if step["mode"] not in ("control",) and control_hours is None:
            raise StopRun("El control sin aguja no dio horas: no se puede calibrar la aguja (D26).")
        ledger.check()
        pair = (step["mode"], step["size"], step["position"], step["repeat"])
        if step["temperature"] == "cold" or pair not in nonce:
            nonce[pair] = secrets.token_hex(4)  # Prefijo nuevo: el proveedor no puede tenerlo cacheado.
            # Una caliente sin su fría (reanudación sin nonce) queda como "partial" por cache_state.
        items = corpus(step["size"], needle_hours=needle_hours, position=step["position"] or "middle", nonce=nonce[pair])
        free = step["mode"] in ("free", "free_control")
        block = build_context_block(load_estimates(items), max_examples=10_000 if free else None, max_tokens=10_000_000 if free else None)
        row = base | {
            "context_tokens": count_tokens(block.examples_text),
            "needle_included": int(NEEDLE_TOKEN in block.examples_text) if needle_hours else "",
            "needle_hours": needle_hours or "",
            "nonce": nonce[pair],
        }
        started = time.perf_counter()
        try:
            result = runner.run(_call_c(items, free))
        except ServiceError as exc:
            spent = getattr(exc, "spent_usage", None)
            if spent is not None:  # Salida truncada o reparación fallida: se pagó y se sabe cuánto.
                row |= {"tokens_in": spent.input_tokens, "tokens_out": spent.output_tokens, "cached_tokens": spent.cached_tokens}
            row["cost_usd"] = getattr(exc, "spent_cost", None)
            row |= {
                "status": exc.status_code,
                "error_code": exc.code,
                "rejection_origin": rejection_origin(exc.status_code, exc.code),
                "error": str(exc)[:300],
            }
            row["wall_ms"] = round((time.perf_counter() - started) * 1000)
            ledger.write(row)
            log(f"C {step}: {exc.status_code} {exc.code}")
            continue
        usage = result.usage
        hours = total_hours(result)
        answer = _answer(result)
        row |= {
            "status": 200,
            "wall_ms": round((time.perf_counter() - started) * 1000),
            "actor_input_tokens": getattr(result.token_budget, "total_input", ""),
            "tokens_in": usage.input_tokens,
            "tokens_out": usage.output_tokens,
            "cached_tokens": usage.cached_tokens,
            "cache_creation_tokens": usage.cache_creation_tokens,
            "cache_state": cache_state(step["temperature"], usage.cached_tokens, usage.cache_creation_tokens),
            "cost_usd": result.cost_usd,
            "latency_ms": round(result.latency_ms),
            "cache_hit": int(bool(result.cache_hit)),
            "total_hours": hours if hours is not None else "",
            "needle_cited": int(NEEDLE_TOKEN.lower() in answer) if needle_hours else "",
            "hours_shift": _shift(hours, _paired_control(ledger.rows, step)) if needle_hours else "",
        }
        if result.cache_hit:
            row["status"], row["error"] = "cache_hit", "acierto de caché: no mide el actor"
        ledger.write(row)
        log(
            f"C {step['mode']} N={step['size']} {step['position'] or '-'} {step['temperature']} r{step['repeat']}: "
            f"{row['actor_input_tokens']} tokens, {row['latency_ms']} ms, {row['cost_usd']} USD, horas {hours}, "
            f"cita {row['needle_cited']}, caché {row['cached_tokens']}; acumulado {ledger.spent:.2f} USD"
        )


def _paired_control(rows: list[dict], step: dict) -> float | None:
    """Horas del control sin aguja con el mismo corpus: solo existe en el modo sin topes."""
    if step["mode"] != "free":
        return None
    for row in rows:
        if row.get("mode") == "free_control" and row.get("size") == str(step["size"]) and row.get("status") == "200" and row.get("total_hours"):
            return float(row["total_hours"])
    return None


def _shift(hours: float | None, control: float | None) -> float | str:
    """Desplazamiento relativo frente al control emparejado: influencia, no prueba de recuperación."""
    if hours is None or not control:
        return ""
    return round((hours - control) / control, 3)


def _control_hours(rows: list[dict]) -> float | None:
    hours = [float(row["total_hours"]) for row in rows if row.get("mode") == "control" and row.get("status") == "200" and row.get("total_hours")]
    return statistics.median(hours) if len(hours) == 3 else None


def _needle_hours(control: float | None) -> int | None:
    return None if control is None else int(round(2 * control / 5) * 5)


def dry_run(sizes: list[int]) -> None:
    from app.config import get_settings
    from app.context.preprocessing import build_context_block, load_estimates
    from app.tokens import count_tokens
    from evals.stress.fixtures.build_corpus import NEEDLE_TOKEN, corpus

    settings = get_settings()
    print(f"Ventana configurada {settings.MODEL_CONTEXT_WINDOW:,}, salida reservada {settings.LLM_MAX_TOKENS:,}")
    for size in sizes:
        cells = []
        for position in ("start", "middle", "end"):
            items = load_estimates(corpus(size, needle_hours=500, position=position))
            capped = build_context_block(items)
            cells.append(f"{position}: {'dentro' if NEEDLE_TOKEN in capped.examples_text else 'fuera'}")
        free = build_context_block(load_estimates(corpus(size, needle_hours=500)), max_examples=10_000, max_tokens=10_000_000)
        print(f"N={size:>4}: {count_tokens(free.examples_text):>7,} tokens sin topes; aguja con topes → {', '.join(cells)}")


# --- CLI ----------------------------------------------------------------------------


def _ints(text: str) -> list[int]:
    return [int(item) for item in text.split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--block", choices=("B", "C"), required=True)
    parser.add_argument("--variant", choices=("B1", "B2", "C"), help="Preset de topes. Por defecto, B1 en B y C en C.")
    parser.add_argument("--sizes", help="KB en B (defecto 50..300); N en C (defecto 5,25,50,100,200,400).")
    parser.add_argument("--repeats", type=int, default=3, help="Repeticiones en B.")
    parser.add_argument("--positions", default="start,middle,end")
    parser.add_argument("--capped-sizes", default="50,200", help="C: N del modo con los topes por defecto.")
    parser.add_argument("--extra-reps", default="", help="C: N con dos repeticiones frías más (deterioro).")
    parser.add_argument("--max-usd", type=float, default=30.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="C: tokens por N y si entra la aguja, sin modelo.")
    args = parser.parse_args(argv)

    variant = args.variant or ("B1" if args.block == "B" else "C")
    if (args.block == "C") != (variant == "C"):
        raise SystemExit("El bloque C usa la variante C; el B, B1 o B2.")
    apply_preset(variant)
    if args.block == "C" and args.dry_run:
        dry_run(_ints(args.sizes or "5,25,50,100,200,400"))
        return 0
    ledger = Ledger(args.output, resume=args.resume, max_usd=args.max_usd)
    try:
        if args.block == "B":
            os.environ["AUTH_MODE"] = "jwt"
            os.environ["AI_SERVICE_SECRET"] = secrets.token_hex(32)
            from app.config import get_settings
            from evals.stress.run import auth_headers, open_client

            get_settings.cache_clear()
            headers = auth_headers("developer", get_settings().AI_SERVICE_SECRET.get_secret_value())
            with open_client(None) as client:
                run_b(client, variant, _ints(args.sizes or "50,100,150,200,250,300"), args.repeats, ledger, headers, print)
        else:
            plan = corpus_plan(
                _ints(args.sizes or "5,25,50,100,200,400"),
                [item for item in args.positions.split(",") if item],
                _ints(args.capped_sizes),
                _ints(args.extra_reps),
            )
            run_c(plan, ledger, print)
    except StopRun as exc:
        print(f"{exc} Se para; --resume para seguir.")
    print(f"Gasto conocido: {ledger.spent:.2f} USD; llamadas sin precio: {ledger.unknown}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
