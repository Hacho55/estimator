#!/usr/bin/env bash
# Prueba local contra el LLM real. No se ejecuta en CI.
# Uso: ./scripts/smoke_test.sh [base_url]
set -euo pipefail

BASE="${1:-http://127.0.0.1:8000}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TRANSCRIPT="$(cat "$ROOT/data/transcripcion_reunion.md")"

echo "== health =="
curl --fail --silent "$BASE/health"
echo

echo "== context =="
curl --fail --silent "$BASE/api/v1/context" | python3 -m json.tool | head -n 40
echo

post_estimate() {
  python3 - "$BASE" "$TRANSCRIPT" << 'PY'
import json, sys, urllib.request
base, transcript = sys.argv[1], sys.argv[2]
body = json.dumps({"transcription": transcript}).encode()
request = urllib.request.Request(
    f"{base}/api/v1/estimate",
    data=body,
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=120) as response:
    payload = json.load(response)
usage = payload["usage"]
print(
    f"model={payload['model']} provider={payload['provider']} "
    f"prompt={payload['prompt_version']} warnings={len(payload['warnings'])} "
    f"in={usage['input_tokens']} cached={usage['cached_tokens']} "
    f"cost={payload['cost_usd']} latency_ms={payload['latency_ms']:.0f} "
    f"trace={payload.get('trace_id')}"
)
PY
}

echo "== estimate 1 =="
post_estimate
echo "== estimate 2 (debería reutilizar caché) =="
post_estimate

echo "== conversación =="
python3 - "$BASE" "$TRANSCRIPT" << 'PY'
import json, sys, urllib.request

base, transcript = sys.argv[1], sys.argv[2]

def post(path, payload):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)

first = post("/api/v1/conversations", {"transcription": transcript})
print(f"turno {first['turn']} kind={first['kind']} near_limit={first['conversation_state']['near_limit']}")
second = post(
    f"/api/v1/conversations/{first['conversation_id']}/messages",
    {"message": "Sube el diseño a 60 horas y añade una fase de testing."},
)
print(f"turno {second['turn']} kind={second['kind']} cached={second['usage']['cached_tokens']}")
third = post(
    f"/api/v1/conversations/{first['conversation_id']}/messages",
    {"message": "¿Cuál sería el equipo ideal para esto?"},
)
print(f"turno {third['turn']} kind={third['kind']} message={third.get('message')}")
PY
