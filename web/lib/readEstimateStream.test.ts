import assert from "node:assert/strict";
import test from "node:test";
import { errorDetail, takeEvents } from "./readEstimateStream.ts";

test("splits frames and keeps an incomplete tail", () => {
  const raw = 'event: token\ndata: "## Hola"\n\nevent: esti';
  const parsed = takeEvents(raw);
  assert.equal(parsed.events.length, 1);
  assert.equal(parsed.events[0].event, "token");
  assert.equal(parsed.events[0].data, "## Hola");
  assert.equal(parsed.rest, "event: esti");
});

test("reads a validation error", () => {
  assert.equal(errorDetail({ detail: [{ msg: "String should have at least 50 characters" }] }), "String should have at least 50 characters");
  assert.equal(errorDetail({ detail: "El modelo no respondió a tiempo." }), "El modelo no respondió a tiempo.");
});

test("parses a conversation estimate frame with structured payload", () => {
  const frame =
    'event: estimate\ndata: {"estimation":"## Ok","model":"m","provider":"p","input_tokens":1,"output_tokens":2,"latency_ms":3,"finish_reason":"stop","cache_hit":false,"prompt_version":"v1-abc","conversation_id":"c1","turn":1,"kind":"estimation","estimation_structured":{"project_title":"Demo","phases":[]}}\n\n';
  const parsed = takeEvents(frame);
  assert.equal(parsed.events.length, 1);
  assert.equal(parsed.events[0].event, "estimate");
  const data = parsed.events[0].data as { conversation_id: string; estimation_structured: { project_title: string } };
  assert.equal(data.conversation_id, "c1");
  assert.equal(data.estimation_structured.project_title, "Demo");
});
