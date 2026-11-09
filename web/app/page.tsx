"use client";

import { useEffect, useState } from "react";
import { EstimationView } from "@/app/EstimationView";
import {
  apiBase,
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

type ProjectMetadata = {
  project_name: string | null;
  assumed_team_size: number | null;
  mentioned_technologies: string[];
  agreed_scope: string | null;
};

type SessionEstimate = {
  session_id: string;
  project_metadata: ProjectMetadata;
  kind: EstimatePayload["kind"];
  message?: string | null;
  estimation: string | null;
  estimation_structured?: StructuredEstimation | null;
  model: string;
  provider: string;
  prompt_version: string;
  usage: { input_tokens: number; output_tokens: number };
  latency_ms: number;
  cache_hit: boolean;
  cache_kind?: string | null;
  cache_similarity?: number | null;
};

const EMPTY_FORM: FormValues = {
  description: "",
  project_type: "web_saas",
  detail_level: "medium",
  output_format: "phases_table",
};

const EMPTY_METADATA: ProjectMetadata = {
  project_name: null,
  assumed_team_size: null,
  mentioned_technologies: [],
  agreed_scope: null,
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

function toPayload(body: SessionEstimate): EstimatePayload {
  return {
    estimation: body.estimation,
    model: body.model,
    provider: body.provider,
    input_tokens: body.usage.input_tokens,
    output_tokens: body.usage.output_tokens,
    latency_ms: body.latency_ms,
    finish_reason: null,
    cache_hit: body.cache_hit,
    cache_kind: body.cache_kind,
    cache_similarity: body.cache_similarity,
    prompt_version: body.prompt_version,
    conversation_id: body.session_id,
    kind: body.kind,
    message: body.message,
    estimation_structured: body.estimation_structured,
  };
}

async function loadContext(form: FormValues, signal?: AbortSignal): Promise<ContextView> {
  const base = apiBase();
  const contextResponse = await fetch(`${base}/api/v1/context?include_text=true`, { signal });
  if (!contextResponse.ok) throw new Error("No se pudo leer el contexto.");
  const context = (await contextResponse.json()) as { text?: string | null };
  let system = "";
  let previewUnavailable = false;
  if (form.description.trim().length >= 50) {
    const previewResponse = await fetch(`${base}/api/v2/estimate/preview?prompt_version=v3`, {
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
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [metadata, setMetadata] = useState<ProjectMetadata>(EMPTY_METADATA);
  const [files, setFiles] = useState<File[]>([]);
  const [fileKey, setFileKey] = useState(0);
  const [followUp, setFollowUp] = useState("");
  const [estimate, setEstimate] = useState<EstimatePayload | null>(null);
  const [structured, setStructured] = useState<StructuredEstimation | null>(null);
  const [answer, setAnswer] = useState<string | null>(null);
  const [chosenFormat, setChosenFormat] = useState<OutputFormat>("phases_table");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [context, setContext] = useState<ContextView | null>(null);
  const [contextError, setContextError] = useState("");

  async function openSession() {
    const response = await fetch(`${apiBase()}/sessions`, { method: "POST" });
    if (!response.ok) throw new Error("session");
    const body = (await response.json()) as { session_id: string };
    setSessionId(body.session_id);
    setMetadata(EMPTY_METADATA);
  }

  useEffect(() => {
    let cancelled = false;
    openSession().catch(() => {
      if (!cancelled) setError("No se pudo abrir la sesión.");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function resetToForm() {
    setSessionId(null);
    setFollowUp("");
    setFiles([]);
    setEstimate(null);
    setStructured(null);
    setAnswer(null);
    setError("");
    setContextOpen(false);
    setForm(EMPTY_FORM);
    setFileKey((current) => current + 1);
    try {
      await openSession();
    } catch {
      setError("No se pudo abrir la sesión.");
    }
  }

  function applyEstimate(payload: EstimatePayload, format: OutputFormat) {
    setEstimate(payload);
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

  async function postEstimate(transcript: string): Promise<boolean> {
    if (!sessionId) {
      setError("No hay sesión. Recarga la página.");
      return false;
    }
    setBusy(true);
    setError("");
    const body = new FormData();
    body.set("transcript", transcript);
    body.set("project_type", form.project_type);
    body.set("detail_level", form.detail_level);
    body.set("output_format", form.output_format);
    for (const file of files) body.append("attachments", file);
    try {
      const response = await fetch(`${apiBase()}/sessions/${sessionId}/estimate`, { method: "POST", body });
      const payload = (await response.json()) as SessionEstimate & { detail?: string };
      if (!response.ok) {
        setError(payload.detail || "La estimación no se pudo generar.");
        return false;
      }
      setMetadata(payload.project_metadata);
      setFiles([]);
      setFileKey((current) => current + 1);
      applyEstimate(toPayload(payload), form.output_format);
      return true;
    } catch {
      setError("No se pudo conectar con la API en el puerto 8000.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function onGenerate(event: React.FormEvent) {
    event.preventDefault();
    if (form.description.trim().length < 50) {
      setError("La descripción necesita al menos 50 caracteres.");
      return;
    }
    setChosenFormat(form.output_format);
    await postEstimate(form.description.trim());
  }

  async function onFollowUp(event: React.FormEvent) {
    event.preventDefault();
    if (!followUp.trim()) return;
    const message = followUp.trim();
    const ok = await postEstimate(message);
    if (ok) setFollowUp("");
  }

  useEffect(() => {
    if (!contextOpen) return;
    const controller = new AbortController();
    setContextError("");
    const timer = window.setTimeout(() => {
      loadContext(form, controller.signal)
        .then((value) => setContext(value))
        .catch((loadError: unknown) => {
          if (controller.signal.aborted) return;
          setContextError("No se pudo leer el contexto.");
          void loadError;
        });
    }, 300);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [contextOpen, form]);

  function promptText(): string {
    if (context?.previewUnavailable) return "La vista previa del prompt solo está disponible en development.";
    return context?.system || "Hace falta una descripción de al menos 50 caracteres para armar el prompt.";
  }

  const inConversation = Boolean(sessionId && (structured || answer || estimate));

  return (
    <main>
      <header>
        <p className="mark">Estimador</p>
        <h1>{inConversation ? "Estimación" : "Nueva estimación"}</h1>
      </header>
      <p className="lede">
        {inConversation
          ? "Ajusta o pregunta sobre la estimación. Los hechos del proyecto se actualizan en el panel."
          : "Rellena el formulario y, si hace falta, adjunta PDF o Word. El backend arma el prompt."}
      </p>

      <div className="workspace">
        <div>
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
              <label>
                Adjuntos
                <input
                  key={fileKey}
                  type="file"
                  multiple
                  accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  aria-label="Adjuntos"
                  onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
                />
              </label>
              {files.length ? <p className="meta">{files.map((file) => file.name).join(", ")}</p> : null}
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
              <div className="actions">
                <button type="submit" disabled={busy || !sessionId}>
                  {busy ? "Generando" : "Generar estimación"}
                </button>
                <button type="button" className="ghost" onClick={resetToForm} disabled={busy}>
                  Nueva conversación
                </button>
                <button type="button" className="ghost" onClick={() => setContextOpen((open) => !open)}>
                  {contextOpen ? "Ocultar prompt base" : "Ver prompt base"}
                </button>
              </div>
            </form>
          ) : (
            <div className="actions sticky-actions">
              <button type="button" className="ghost" onClick={resetToForm} disabled={busy}>
                Nueva conversación
              </button>
              <button type="button" className="ghost" onClick={() => setContextOpen((open) => !open)}>
                {contextOpen ? "Ocultar prompt base" : "Ver prompt base"}
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
                Prompt base v3, sin historial, metadatos ni adjuntos de la sesión · {form.project_type} ·{" "}
                {form.detail_level} · {form.output_format}
              </p>
              <h2>System prompt base</h2>
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
            {answer ? (
              <article className="answer">
                <h2>Respuesta</h2>
                <p>{answer}</p>
              </article>
            ) : null}
            {structured ? <EstimationView estimation={structured} format={chosenFormat} /> : null}
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
              <label>
                Adjuntos
                <input
                  key={`follow-${fileKey}`}
                  type="file"
                  multiple
                  accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  aria-label="Adjuntos del ajuste"
                  onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
                />
              </label>
              <button type="submit" disabled={busy || !followUp.trim()}>
                {busy ? "Enviando" : "Enviar"}
              </button>
            </form>
          ) : null}
        </div>

        <aside className="metadata" aria-label="project_metadata">
          <h2>project_metadata</h2>
          <p className="meta">{sessionId ? `sesión ${sessionId}` : "abriendo sesión"}</p>
          <dl>
            <div>
              <dt>Nombre</dt>
              <dd>{metadata.project_name || "—"}</dd>
            </div>
            <div>
              <dt>Equipo asumido</dt>
              <dd>{metadata.assumed_team_size ?? "—"}</dd>
            </div>
            <div>
              <dt>Tecnologías</dt>
              <dd>{metadata.mentioned_technologies.length ? metadata.mentioned_technologies.join(", ") : "—"}</dd>
            </div>
            <div>
              <dt>Alcance acordado</dt>
              <dd>{metadata.agreed_scope || "—"}</dd>
            </div>
          </dl>
        </aside>
      </div>
    </main>
  );
}
