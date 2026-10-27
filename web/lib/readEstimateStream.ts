/** Sesión 3 · front: lee el POST SSE. El navegador no usa EventSource porque el cuerpo no cabe en la URL. */

export type EstimatePayload = {
  estimation: string;
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  finish_reason: string | null;
  cache_hit: boolean;
  prompt_version: string;
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

export async function readEstimateStream(transcription: string, handlers: StreamHandlers): Promise<void> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  const response = await fetch(`${base}/api/v1/estimate/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transcription }),
  });
  if (!response.ok || !response.body) {
    handlers.onError("La API no pudo abrir el stream.");
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const parsed = takeEvents(buffer);
    buffer = parsed.rest;
    for (const item of parsed.events) dispatch(item, handlers);
    if (done) break;
  }
  const tail = takeEvents(`${buffer}\n\n`);
  for (const item of tail.events) dispatch(item, handlers);
}

function dispatch(item: { event: string; data: unknown }, handlers: StreamHandlers): void {
  if (item.event === "token" && typeof item.data === "string") handlers.onToken(item.data);
  else if (item.event === "estimate") handlers.onEstimate(item.data as EstimatePayload);
  else if (item.event === "error") {
    const detail = (item.data as { detail?: string }).detail ?? "La estimación falló.";
    handlers.onError(detail);
  }
}
