"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { type EstimatePayload, readEstimateStream } from "@/lib/readEstimateStream";

export default function Page() {
  const [transcription, setTranscription] = useState("");
  const [draft, setDraft] = useState("");
  const [estimate, setEstimate] = useState<EstimatePayload | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

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
        onToken: (text) => setDraft((current) => current + text),
        onEstimate: (payload) => {
          setDraft("");
          setEstimate(payload);
        },
        onError: (detail) => setError(detail),
      });
    } catch {
      setError("No se pudo leer la respuesta.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1>Estimador</h1>
      <p className="lede">Pega la transcripción. La estimación aparece mientras se escribe y, al cerrar, queda el texto ya validado.</p>
      <form onSubmit={onSubmit}>
        <textarea
          value={transcription}
          onChange={(event) => setTranscription(event.target.value)}
          placeholder="Transcripción de la reunión"
          aria-label="Transcripción"
        />
        <button type="submit" disabled={busy}>
          {busy ? "Estimando" : "Estimar"}
        </button>
      </form>
      {error ? <p className="error">{error}</p> : null}
      {draft ? <pre className="draft">{draft}</pre> : null}
      {estimate ? (
        <>
          <p className="meta">
            {estimate.model} · {estimate.input_tokens} tokens de entrada · {estimate.output_tokens} de salida ·{" "}
            {Math.round(estimate.latency_ms)} ms · {estimate.cache_hit ? "caché" : "modelo"}
          </p>
          <article className="estimate">
            <ReactMarkdown>{estimate.estimation ?? ""}</ReactMarkdown>
          </article>
        </>
      ) : null}
    </main>
  );
}
