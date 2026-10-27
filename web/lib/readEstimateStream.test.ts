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
