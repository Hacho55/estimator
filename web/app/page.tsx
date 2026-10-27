"use client";

import { useEffect, useState } from "react";
import { Markdown } from "@/app/Markdown";
import { apiBase, type EstimatePayload, readEstimateStream } from "@/lib/readEstimateStream";

type ContextView = { system: string; text: string; previewUnavailable: boolean };

function plainContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((block) => {
      if (typeof block === "string") return block;
      if (block && typeof block === "object" && "text" in block && typeof block.text === "string") return block.text;
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

async function loadContext(transcription: string): Promise<ContextView> {
  const base = apiBase();
  const contextResponse = await fetch(`${base}/api/v1/context?include_text=true`);
  if (!contextResponse.ok) throw new Error("No se pudo leer el contexto.");
  const context = (await contextResponse.json()) as { text?: string | null };
  let system = "";
  let previewUnavailable = false;
  if (transcription.trim().length >= 50) {
    const previewResponse = await fetch(`${base}/api/v1/estimate/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transcription }),
    });
    if (previewResponse.status === 404) previewUnavailable = true;
    else if (previewResponse.ok) {
      const preview = (await previewResponse.json()) as { messages?: { role: string; content: unknown }[] };
      const message = preview.messages?.find((item) => item.role === "system");
      system = plainContent(message?.content);
    }
  }
  return { system, text: context.text ?? "", previewUnavailable };
}

export default function Page() {
  const [transcription, setTranscription] = useState("");
  const [draft, setDraft] = useState("");
  const [estimate, setEstimate] = useState<EstimatePayload | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [context, setContext] = useState<ContextView | null>(null);
  const [contextError, setContextError] = useState("");

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setEstimate(null);
    setDraft("");
    if (transcription.trim().length < 50) {
      setError("La transcripción necesita al menos 50 caracteres.");
      return;
    }
    setBusy(true);
    try {
      await readEstimateStream(transcription, {
        onToken: (text) => setDraft(text),
        onEstimate: (payload) => {
          setDraft("");
          setEstimate(payload);
        },
        onError: (detail) => {
          setDraft("");
          setError(detail);
        },
      });
    } catch {
      setError("No se pudo conectar con la API en el puerto 8000.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!contextOpen) return;
    let cancelled = false;
    setContextError("");
    loadContext(transcription)
      .then((value) => {
        if (!cancelled) setContext(value);
      })
      .catch(() => {
        if (!cancelled) setContextError("No se pudo leer el contexto.");
      });
    return () => {
      cancelled = true;
    };
  }, [contextOpen, transcription]);

  function promptText(): string {
    if (context?.previewUnavailable) return "La vista previa del prompt solo está disponible en development.";
    return context?.system || "Hace falta una transcripción de al menos 50 caracteres para armar el prompt.";
  }

  return (
    <main>
      <header>
        <p className="mark">Estimador</p>
        <h1>Estimación a partir de la transcripción</h1>
      </header>
      <p className="lede">Pega la transcripción. La estimación aparece mientras se escribe y, al cerrar, queda el texto ya validado.</p>
      <form onSubmit={onSubmit}>
        <textarea
          value={transcription}
          onChange={(event) => setTranscription(event.target.value)}
          placeholder="Transcripción de la reunión"
          aria-label="Transcripción"
        />
        <div className="actions">
          <button type="submit" disabled={busy}>
            {busy ? "Estimando" : "Estimar"}
          </button>
          <button type="button" className="ghost" onClick={() => setContextOpen((open) => !open)}>
            {contextOpen ? "Ocultar contexto" : "Ver contexto"}
          </button>
        </div>
      </form>
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      {contextOpen ? (
        <section className="context">
          {contextError ? <p className="error">{contextError}</p> : null}
          <h2>System prompt</h2>
          <pre>{promptText()}</pre>
          <h2>Contexto inyectado</h2>
          <pre>{context?.text || "Cargando el contexto."}</pre>
        </section>
      ) : null}
      {estimate ? (
        <dl className="stats">
          <div>
            <dt>Modelo</dt>
            <dd>{estimate.model}</dd>
          </div>
          <div>
            <dt>Tokens de entrada</dt>
            <dd>{estimate.input_tokens}</dd>
          </div>
          <div>
            <dt>Tokens de salida</dt>
            <dd>{estimate.output_tokens}</dd>
          </div>
          <div>
            <dt>Latencia</dt>
            <dd>{Math.round(estimate.latency_ms)} ms</dd>
          </div>
          <div>
            <dt>Origen</dt>
            <dd>{estimate.cache_hit ? "caché" : "modelo"}</dd>
          </div>
        </dl>
      ) : null}
      <div aria-live="polite" aria-busy={busy}>
        {draft ? (
          <article className="draft">
            <Markdown>{draft}</Markdown>
          </article>
        ) : null}
        {estimate ? (
          <article className="estimate">
            <Markdown>{estimate.estimation ?? ""}</Markdown>
          </article>
        ) : null}
      </div>
    </main>
  );
}
