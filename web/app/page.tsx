"use client";

import { useEffect, useState } from "react";
import { EstimationView } from "@/app/EstimationView";
import { Markdown } from "@/app/Markdown";
import {
  apiBase,
  readSseStream,
  type EstimatePayload,
  type StructuredEstimation,
} from "@/lib/readEstimateStream";
import {
  DETAIL_LEVEL_OPTIONS,
  OUTPUT_FORMAT_OPTIONS,
  PROJECT_TYPE_OPTIONS,
  type DetailLevel,
  type FormValues,
  type OutputFormat,
  type ProjectType,
} from "@/lib/types";

type ContextView = { system: string; text: string; previewUnavailable: boolean };

const EMPTY_FORM: FormValues = {
  description: "",
  project_type: "web_saas",
  detail_level: "medium",
  output_format: "phases_table",
};

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

async function loadContext(
  form: FormValues,
  promptVersion: string | null,
  signal?: AbortSignal,
): Promise<ContextView> {
  const base = apiBase();
  const contextResponse = await fetch(`${base}/api/v1/context?include_text=true`, { signal });
  if (!contextResponse.ok) throw new Error("No se pudo leer el contexto.");
  const context = (await contextResponse.json()) as { text?: string | null };
  let system = "";
  let previewUnavailable = false;
  if (form.description.trim().length >= 50) {
    const params = new URLSearchParams();
    if (promptVersion) params.set("prompt_version", promptVersion);
    const query = params.toString() ? `?${params}` : "";
    const previewResponse = await fetch(`${base}/api/v2/estimate/preview${query}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
      signal,
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
  const [form, setForm] = useState<FormValues>(EMPTY_FORM);
  const [promptVersion, setPromptVersion] = useState<string | null>(null);
  const [promptVersions, setPromptVersions] = useState<string[]>([]);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [followUp, setFollowUp] = useState("");
  const [draft, setDraft] = useState("");
  const [estimate, setEstimate] = useState<EstimatePayload | null>(null);
  const [structured, setStructured] = useState<StructuredEstimation | null>(null);
  const [answer, setAnswer] = useState<string | null>(null);
  const [chosenFormat, setChosenFormat] = useState<OutputFormat>("phases_table");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [context, setContext] = useState<ContextView | null>(null);
  const [contextError, setContextError] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase()}/api/v2/prompts`)
      .then(async (response) => {
        if (!response.ok) return;
        const body = (await response.json()) as { versions: string[]; default: string };
        if (cancelled) return;
        setPromptVersions(body.versions.length ? body.versions : ["v1"]);
        // Se deja null: el servidor aplica LLM_PROMPT_VERSION. El usuario puede fijar una.
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  function resetToForm() {
    setConversationId(null);
    setFollowUp("");
    setDraft("");
    setEstimate(null);
    setStructured(null);
    setAnswer(null);
    setError("");
    setContextOpen(false);
  }

  function applyEstimate(payload: EstimatePayload, format: OutputFormat) {
    setDraft("");
    setEstimate(payload);
    if (payload.conversation_id) setConversationId(payload.conversation_id);
    if (payload.kind === "answer") {
      setAnswer(payload.message ?? "Sin respuesta.");
      return;
    }
    setAnswer(null);
    if (payload.estimation_structured) {
      setStructured(payload.estimation_structured);
      setChosenFormat(format);
    }
  }

  async function onGenerate(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setDraft("");
    setEstimate(null);
    setStructured(null);
    setAnswer(null);
    if (form.description.trim().length < 50) {
      setError("La descripción necesita al menos 50 caracteres.");
      return;
    }
    setBusy(true);
    setChosenFormat(form.output_format);
    const query = promptVersion ? `?prompt_version=${encodeURIComponent(promptVersion)}` : "";
    try {
      await readSseStream(`/api/v2/conversations/stream${query}`, form, {
        onToken: (text) => setDraft(text),
        onEstimate: (payload) => applyEstimate(payload, form.output_format),
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

  async function onFollowUp(event: React.FormEvent) {
    event.preventDefault();
    if (!conversationId || !followUp.trim()) return;
    setError("");
    setDraft("");
    setBusy(true);
    const message = followUp.trim();
    try {
      await readSseStream(`/api/v2/conversations/${conversationId}/messages/stream`, { message }, {
        onToken: (text) => setDraft(text),
        onEstimate: (payload) => {
          applyEstimate(payload, chosenFormat);
          setFollowUp("");
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
    const controller = new AbortController();
    setContextError("");
    const timer = window.setTimeout(() => {
      loadContext(form, promptVersion, controller.signal)
        .then((value) => setContext(value))
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          setContextError("No se pudo leer el contexto.");
          void error;
        });
    }, 300);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [contextOpen, form, promptVersion]);

  function promptText(): string {
    if (context?.previewUnavailable) return "La vista previa del prompt solo está disponible en development.";
    return context?.system || "Hace falta una descripción de al menos 50 caracteres para armar el prompt.";
  }

  const inConversation = Boolean(conversationId && (structured || answer || estimate));

  return (
    <main>
      <header>
        <p className="mark">Estimador</p>
        <h1>{inConversation ? "Estimación" : "Nueva estimación"}</h1>
      </header>
      <p className="lede">
        {inConversation
          ? "Ajusta o pregunta sobre la estimación. El prompt del formulario no cambia."
          : "Rellena el formulario. El backend arma el prompt; tú no lo redactas."}
      </p>

      {!inConversation ? (
        <form onSubmit={onGenerate}>
          <label>
            Descripción del proyecto
            <textarea
              value={form.description}
              onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
              placeholder="Transcripción o descripción del alcance"
              aria-label="Descripción del proyecto"
            />
          </label>
          <div className="field-row">
            <label>
              Tipo de proyecto
              <select
                value={form.project_type}
                onChange={(event) =>
                  setForm((current) => ({ ...current, project_type: event.target.value as ProjectType }))
                }
              >
                {PROJECT_TYPE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Nivel de detalle
              <select
                value={form.detail_level}
                onChange={(event) =>
                  setForm((current) => ({ ...current, detail_level: event.target.value as DetailLevel }))
                }
              >
                {DETAIL_LEVEL_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Formato de salida
              <select
                value={form.output_format}
                onChange={(event) =>
                  setForm((current) => ({ ...current, output_format: event.target.value as OutputFormat }))
                }
              >
                {OUTPUT_FORMAT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <details className="advanced" open={advancedOpen} onToggle={(event) => setAdvancedOpen((event.target as HTMLDetailsElement).open)}>
            <summary>Opciones avanzadas</summary>
            <label>
              Versión de prompt
              <select
                value={promptVersion ?? ""}
                onChange={(event) => setPromptVersion(event.target.value || null)}
                disabled={!promptVersions.length}
              >
                <option value="">Por defecto del servidor</option>
                {promptVersions.map((version) => (
                  <option key={version} value={version}>
                    {version}
                  </option>
                ))}
              </select>
            </label>
          </details>
          <div className="actions">
            <button type="submit" disabled={busy}>
              {busy ? "Generando" : "Generar estimación"}
            </button>
            <button type="button" className="ghost" onClick={() => setContextOpen((open) => !open)}>
              {contextOpen ? "Ocultar contexto" : "Ver contexto"}
            </button>
          </div>
        </form>
      ) : (
        <div className="actions sticky-actions">
          <button type="button" className="ghost" onClick={resetToForm} disabled={busy}>
            Nueva estimación
          </button>
          <button type="button" className="ghost" onClick={() => setContextOpen((open) => !open)}>
            {contextOpen ? "Ocultar contexto" : "Ver contexto"}
          </button>
        </div>
      )}

      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}

      {contextOpen ? (
        <section className="context">
          {contextError ? <p className="error">{contextError}</p> : null}
          <p className="meta">
            Versión {promptVersion ?? "por defecto"} · {form.project_type} · {form.detail_level} ·{" "}
            {form.output_format}
          </p>
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
            <dd>
              {estimate.cache_hit
                ? estimate.cache_kind === "semantic"
                  ? `caché semántica${estimate.cache_similarity != null ? ` (${estimate.cache_similarity.toFixed(2)})` : ""}`
                  : "caché exacta"
                : "modelo"}
            </dd>
          </div>
          <div>
            <dt>Prompt</dt>
            <dd>{estimate.prompt_version}</dd>
          </div>
        </dl>
      ) : null}

      <div aria-live="polite" aria-busy={busy}>
        {draft ? (
          <article className="draft">
            <Markdown>{draft}</Markdown>
          </article>
        ) : null}
        {answer ? (
          <article className="answer">
            <h2>Respuesta</h2>
            <p>{answer}</p>
          </article>
        ) : null}
        {structured && !draft ? <EstimationView estimation={structured} format={chosenFormat} /> : null}
      </div>

      {inConversation ? (
        <form className="follow-up" onSubmit={onFollowUp}>
          <label>
            Ajuste o pregunta
            <textarea
              value={followUp}
              onChange={(event) => setFollowUp(event.target.value)}
              placeholder="Por ejemplo: baja las horas de frontend o ¿quién hace el QA?"
              rows={3}
            />
          </label>
          <button type="submit" disabled={busy || !followUp.trim()}>
            {busy ? "Enviando" : "Enviar"}
          </button>
        </form>
      ) : null}
    </main>
  );
}
