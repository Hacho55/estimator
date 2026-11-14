"use client";

import { useEffect, useState } from "react";
import { MemoryPanel } from "@/app/MemoryPanel";
import { ReviewPanel } from "@/app/ReviewPanel";
import { UserBar } from "@/app/UserBar";
import { TierView } from "@/app/views/TierView";
import type { Login } from "@/lib/auth";
import { apiBase } from "@/lib/readEstimateStream";
import {
  ApiError,
  EMPTY_METADATA,
  createSession,
  estimateTurn,
  getSession,
  type MemoryChange,
  type ProjectMetadata,
  type SessionEstimate,
} from "@/lib/session";
import { DETAIL_LEVEL_OPTIONS, PROJECT_TYPE_OPTIONS, type DetailLevel, type FormValues, type ProjectType } from "@/lib/types";

type ContextView = { system: string; text: string; previewUnavailable: boolean };

const EMPTY_FORM: FormValues = {
  description: "",
  project_type: "web_saas",
  detail_level: "medium",
  output_format: "phases_table",
};

const ACCEPT = ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

function plainContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((block) => (block && typeof block === "object" && "text" in block && typeof block.text === "string" ? block.text : ""))
    .filter(Boolean)
    .join("\n");
}

// Prompt base: la plantilla genérica v4 sin tier, sin historial, memoria ni adjuntos.
async function loadContext(form: FormValues, signal?: AbortSignal): Promise<ContextView> {
  const base = apiBase();
  const contextResponse = await fetch(`${base}/api/v1/context?include_text=true`, { signal });
  if (!contextResponse.ok) throw new Error("No se pudo leer el contexto.");
  const context = (await contextResponse.json()) as { text?: string | null };
  let system = "";
  let previewUnavailable = false;
  if (form.description.trim().length >= 50) {
    const previewResponse = await fetch(`${base}/api/v2/estimate/preview?prompt_version=v4`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
      signal,
    });
    if (previewResponse.status === 404) previewUnavailable = true;
    else if (previewResponse.ok) {
      const preview = (await previewResponse.json()) as { messages?: { role: string; content: unknown }[] };
      system = plainContent(preview.messages?.find((item) => item.role === "system")?.content);
    }
  }
  return { system, text: context.text ?? "", previewUnavailable };
}

export default function Page() {
  const [auth, setAuth] = useState<Login | null>(null);
  const [form, setForm] = useState<FormValues>(EMPTY_FORM);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [metadata, setMetadata] = useState<ProjectMetadata>(EMPTY_METADATA);
  const [memoryLog, setMemoryLog] = useState<MemoryChange[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [fileKey, setFileKey] = useState(0);
  const [followUp, setFollowUp] = useState("");
  const [result, setResult] = useState<SessionEstimate | null>(null);
  const [error, setError] = useState("");
  const [archived, setArchived] = useState(false);
  const [busy, setBusy] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [context, setContext] = useState<ContextView | null>(null);
  const [contextError, setContextError] = useState("");

  const token = auth?.token ?? null;
  const userId = auth?.user.id ?? null;

  // Otra persona, otra sesión: cada sesión pertenece al usuario del token que la creó.
  useEffect(() => {
    clearConversation();
    setSessionId(null);
  }, [userId]);

  function clearConversation() {
    setFollowUp("");
    setFiles([]);
    setResult(null);
    setError("");
    setArchived(false);
    setMetadata(EMPTY_METADATA);
    setMemoryLog([]);
    setFileKey((current) => current + 1);
  }

  // Reset (política 3) o reanudar con la memoria heredada (política 2): la anterior queda archivada.
  async function restart(inheritMemory: boolean) {
    const previous = sessionId;
    clearConversation();
    setForm(EMPTY_FORM);
    setSessionId(null);
    if (!previous) return;
    try {
      const created = await createSession(token, { previousSessionId: previous, inheritMemory });
      setSessionId(created.session_id);
      if (inheritMemory) {
        const state = await getSession(token, created.session_id);
        setMetadata(state.project_metadata);
        setMemoryLog(state.memory_log);
      }
    } catch {
      setError("No se pudo abrir la sesión nueva.");
    }
  }

  async function postEstimate(transcript: string): Promise<boolean> {
    setBusy(true);
    setError("");
    try {
      let id = sessionId;
      if (!id) {
        id = (await createSession(token)).session_id;
        setSessionId(id);
      }
      const body = new FormData();
      body.set("transcript", transcript);
      body.set("project_type", form.project_type);
      body.set("detail_level", form.detail_level);
      for (const file of files) body.append("attachments", file);
      const payload = await estimateTurn(token, id, body);
      setResult(payload);
      setMetadata(payload.project_metadata);
      setFiles([]);
      setFileKey((current) => current + 1);
      getSession(token, id)
        .then((state) => setMemoryLog(state.memory_log))
        .catch(() => setMemoryLog((current) => [...current, ...payload.memory_changes]));
      return true;
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === "session_archived") setArchived(true);
      setError(failure instanceof Error ? failure.message : "No se pudo conectar con la API en el puerto 8000.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function onGenerate(event: React.FormEvent) {
    event.preventDefault();
    if (form.description.trim().length < 50 && files.length === 0) {
      setError("La descripción necesita al menos 50 caracteres o un adjunto.");
      return;
    }
    await postEstimate(form.description.trim());
  }

  async function onFollowUp(event: React.FormEvent) {
    event.preventDefault();
    if (!followUp.trim() && files.length === 0) return;
    if (await postEstimate(followUp.trim())) setFollowUp("");
  }

  useEffect(() => {
    if (!contextOpen) return;
    const controller = new AbortController();
    setContextError("");
    const timer = window.setTimeout(() => {
      loadContext(form, controller.signal)
        .then(setContext)
        .catch(() => {
          if (!controller.signal.aborted) setContextError("No se pudo leer el contexto.");
        });
    }, 300);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [contextOpen, form]);

  const inConversation = Boolean(sessionId && result);
  const fileInput = (label: string) => (
    <label>
      {label}
      <input
        key={`${label}-${fileKey}`}
        type="file"
        multiple
        accept={ACCEPT}
        aria-label={label}
        onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
      />
    </label>
  );

  return (
    <main>
      <header>
        <p className="mark">Estimador</p>
        <h1>{inConversation ? "Estimación" : "Nueva estimación"}</h1>
      </header>
      <UserBar auth={auth} onAuth={setAuth} />
      <p className="lede">
        {inConversation
          ? "Ajusta o pregunta sobre la estimación. La memoria del proyecto se actualiza en el panel."
          : "Describe el proyecto y, si hace falta, adjunta PDF o Word. La forma de la respuesta depende de tu perfil."}
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
              {fileInput("Adjuntos")}
              {files.length ? <p className="meta">{files.map((file) => file.name).join(", ")}</p> : null}
              <div className="field-row">
                <label>
                  Tipo de proyecto
                  <select
                    value={form.project_type}
                    onChange={(event) => setForm((current) => ({ ...current, project_type: event.target.value as ProjectType }))}
                  >
                    {PROJECT_TYPE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Profundidad
                  <select
                    value={form.detail_level}
                    onChange={(event) => setForm((current) => ({ ...current, detail_level: event.target.value as DetailLevel }))}
                  >
                    {DETAIL_LEVEL_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="actions">
                <button type="submit" disabled={busy}>
                  {busy ? "Generando" : "Generar estimación"}
                </button>
                <button type="button" className="ghost" onClick={() => setContextOpen((open) => !open)}>
                  {contextOpen ? "Ocultar prompt base" : "Ver prompt base"}
                </button>
              </div>
            </form>
          ) : (
            <div className="actions sticky-actions">
              <button type="button" className="ghost" onClick={() => restart(false)} disabled={busy}>
                Empezar de cero
              </button>
              <button type="button" className="ghost" onClick={() => restart(true)} disabled={busy}>
                Nueva con lo aprendido
              </button>
              <button type="button" className="ghost" onClick={() => setContextOpen((open) => !open)}>
                {contextOpen ? "Ocultar prompt base" : "Ver prompt base"}
              </button>
            </div>
          )}

          {error ? (
            <div className="error" role="alert">
              <p>{error}</p>
              {archived ? (
                <div className="actions">
                  <button type="button" onClick={() => restart(true)}>
                    Seguir con la memoria
                  </button>
                  <button type="button" className="ghost" onClick={() => restart(false)}>
                    Empezar de cero
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}

          {contextOpen ? (
            <section className="context">
              {contextError ? <p className="error">{contextError}</p> : null}
              <p className="meta">Plantilla genérica v4, sin tier, historial, memoria ni adjuntos.</p>
              <h2>System prompt base</h2>
              <pre>
                {context?.previewUnavailable
                  ? "La vista previa del prompt solo está disponible en development."
                  : context?.system || "Hace falta una descripción de al menos 50 caracteres para armar el prompt."}
              </pre>
              <h2>Contexto inyectado</h2>
              <pre>{context?.text || "Cargando el contexto."}</pre>
            </section>
          ) : null}

          {result ? (
            <dl className="stats">
              <div>
                <dt>Perfil</dt>
                <dd>{result.tier ?? "—"}</dd>
              </div>
              <div>
                <dt>Modelo</dt>
                <dd>{result.model}</dd>
              </div>
              <div>
                <dt>Tokens</dt>
                <dd>
                  {result.usage.input_tokens} / {result.usage.output_tokens}
                </dd>
              </div>
              <div>
                <dt>Latencia</dt>
                <dd>{Math.round(result.latency_ms)} ms</dd>
              </div>
              <div>
                <dt>Origen</dt>
                <dd>{result.cache_hit ? `caché ${result.cache_kind === "semantic" ? "semántica" : "exacta"}` : "modelo"}</dd>
              </div>
              <div>
                <dt>Prompt</dt>
                <dd>{result.prompt_version}</dd>
              </div>
              {result.tools.length ? (
                <div>
                  <dt>Herramientas</dt>
                  <dd>{result.tools.map((tool) => `${tool.tool}${tool.ok ? "" : " (falló)"}`).join(", ")}</dd>
                </div>
              ) : null}
              {result.attachments.length ? (
                <div>
                  <dt>Adjuntos</dt>
                  <dd>
                    {result.attachments
                      .map((item) => `${item.name} (${item.chars} car.${item.truncated ? ", truncado" : ""})`)
                      .join(", ")}
                  </dd>
                </div>
              ) : null}
            </dl>
          ) : null}

          {result?.warnings.length ? (
            <ul className="warnings">
              {result.warnings.map((warning, index) => (
                <li key={`${warning.code}-${index}`}>{warning.message}</li>
              ))}
            </ul>
          ) : null}

          <div aria-live="polite" aria-busy={busy}>
            {result?.kind === "answer" ? (
              <article className="answer">
                <h2>Respuesta</h2>
                <p>{result.message}</p>
              </article>
            ) : null}
            {result?.review ? <ReviewPanel review={result.review} /> : null}
            {result?.estimation_structured ? <TierView view={result.estimation_structured} /> : null}
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
              {fileInput("Adjuntos del ajuste")}
              <button type="submit" disabled={busy || (!followUp.trim() && files.length === 0)}>
                {busy ? "Enviando" : "Enviar"}
              </button>
            </form>
          ) : null}
        </div>

        <MemoryPanel sessionId={sessionId} metadata={metadata} log={memoryLog} tier={result?.tier ?? auth?.user.tier ?? null} />
      </div>
    </main>
  );
}
