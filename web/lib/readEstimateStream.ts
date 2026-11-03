/** Lee un POST SSE. Cada token trae el markdown visible hasta ese momento. */

export type PhaseSummary = {
  phase: string;
  hours: number;
  cost_eur: number;
  weeks: number;
};

export type DraftTask = {
  name: string;
  phase: string;
  role: string;
  hours: number;
  rate_eur: number;
  cost_eur: number;
};

export type PhaseAssumptions = {
  phase: string;
  assumptions: string[];
};

export type StructuredEstimation = {
  project_title: string;
  summary: string;
  is_estimable: boolean;
  not_estimable_reason: string | null;
  tasks: DraftTask[];
  total_hours: number;
  total_cost_eur: number;
  team: { role: string; count: number; dedication: number }[];
  duration_weeks_min: number;
  duration_weeks_max: number;
  assumptions: string[];
  risks: string[];
  phase_assumptions?: PhaseAssumptions[];
  phases?: PhaseSummary[];
};

export type EstimatePayload = {
  estimation: string | null;
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  finish_reason: string | null;
  cache_hit: boolean;
  cache_kind?: string | null;
  cache_similarity?: number | null;
  prompt_version: string;
  conversation_id?: string;
  turn?: number;
  kind?: "estimation" | "estimation_update" | "answer";
  message?: string | null;
  estimation_structured?: StructuredEstimation | null;
  conversation_state?: {
    turns_total: number;
    turns_in_window: number;
    turns_dropped: number;
    approx_input_tokens: number;
    near_limit: boolean;
  };
};

export type StreamHandlers = {
  onToken: (text: string) => void;
  onEstimate: (estimate: EstimatePayload) => void;
  onError: (detail: string) => void;
};

export function takeEvents(buffer: string): { events: { event: string; data: unknown }[]; rest: string } {
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events = blocks.flatMap((block) => {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length === 0) return [];
    return [{ event, data: JSON.parse(dataLines.join("\n")) as unknown }];
  });
  return { events, rest };
}

export function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
}

export function errorDetail(body: unknown): string {
  if (!body || typeof body !== "object") return "La API no pudo abrir el stream.";
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item && typeof item.msg === "string") return item.msg;
        return "";
      })
      .filter(Boolean);
    if (messages.length > 0) return messages.join(" ");
  }
  return "La API no pudo abrir el stream.";
}

export async function readSseStream(
  path: string,
  body: unknown,
  handlers: StreamHandlers,
): Promise<void> {
  const base = apiBase();
  const response = await fetch(`${base}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    let detail = "La API no pudo abrir el stream.";
    try {
      detail = errorDetail(await response.json());
    } catch {
      detail = "La API no pudo abrir el stream.";
    }
    handlers.onError(detail);
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let settled = false;
  const wrapped: StreamHandlers = {
    onToken: handlers.onToken,
    onEstimate: (estimate) => {
      settled = true;
      handlers.onEstimate(estimate);
    },
    onError: (detail) => {
      settled = true;
      handlers.onError(detail);
    },
  };
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const parsed = takeEvents(buffer);
    buffer = parsed.rest;
    for (const item of parsed.events) dispatch(item, wrapped);
    if (done) break;
  }
  const tail = takeEvents(`${buffer}\n\n`);
  for (const item of tail.events) dispatch(item, wrapped);
  if (!settled) handlers.onError("La respuesta se cortó antes de la estimación.");
}

/** @deprecated Prefer readSseStream with /api/v2. Kept for callers that still hit v1. */
export async function readEstimateStream(transcription: string, handlers: StreamHandlers): Promise<void> {
  return readSseStream("/api/v1/estimate/stream", { transcription }, handlers);
}

function dispatch(item: { event: string; data: unknown }, handlers: StreamHandlers): void {
  if (item.event === "token" && typeof item.data === "string") handlers.onToken(item.data);
  else if (item.event === "estimate") handlers.onEstimate(item.data as EstimatePayload);
  else if (item.event === "error") {
    const detail = (item.data as { detail?: string }).detail ?? "La estimación falló.";
    handlers.onError(detail);
  }
}
