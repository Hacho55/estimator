// Contrato de /sessions (sesión 5): vista por tier, memoria, revisión, herramientas y adjuntos.
import { apiBase } from "@/lib/readEstimateStream";

export type Tier = "developer" | "pm" | "executive";

export type ProjectMetadata = {
  project_name: string | null;
  assumed_team_size: number | null;
  mentioned_technologies: string[];
  agreed_scope: string | null;
  explicit_constraints: string[];
  rejected_options: string[];
};

export const EMPTY_METADATA: ProjectMetadata = {
  project_name: null,
  assumed_team_size: null,
  mentioned_technologies: [],
  agreed_scope: null,
  explicit_constraints: [],
  rejected_options: [],
};

export type MemoryChange = {
  turn: number;
  field: string;
  before: unknown;
  after: unknown;
  evidence: string | null;
};

export type Range = { min: number; max: number };
export type TeamMember = { role: string; count: number; dedication: number };
export type WorkLine = { name: string; role: string; hours: number; rate_eur: number; cost_eur: number };

type ViewBase = {
  project_title: string;
  summary: string;
  is_estimable: boolean;
  not_estimable_reason: string | null;
};

export type DeveloperEstimate = ViewBase & {
  tier: "developer";
  components: {
    name: string;
    hours: number;
    cost_eur: number;
    work: WorkLine[];
    technical_risks: string[];
    stack_assumptions: string[];
  }[];
  uncertainty_drivers: string[];
  total_hours: number;
  total_hours_range: Range;
  total_cost_eur: number;
  team: TeamMember[];
  assumptions: string[];
  risks: string[];
};

export type PmEstimate = ViewBase & {
  tier: "pm";
  phases: { name: string; hours: number; cost_eur: number; weeks: number }[];
  milestones: { name: string; phase: string; deliverable: string; cumulative_cost_eur: number }[];
  team_composition: TeamMember[];
  duration_weeks_range: Range;
  total_hours: number;
  total_cost_eur: number;
  blockers: string[];
  risks: string[];
  assumptions: string[];
};

export type ExecutiveEstimate = ViewBase & {
  tier: "executive";
  headline_cost_range: Range;
  headline_duration_range: Range;
  confidence_level: "low" | "medium" | "high";
  confidence_reason: string;
  top_three_risks: string[];
  go_no_go_recommendation: string;
};

export type TierEstimate = DeveloperEstimate | PmEstimate | ExecutiveEstimate;

export type ReviewIssue = {
  source: "critic" | "property";
  category: string;
  severity: "critical" | "major" | "minor";
  field_path: string;
  description: string;
  suggested_fix: string | null;
};

export type ReviewTrace = {
  decision: "accept" | "iterate" | "synthesize";
  iterations: { iteration: number; verdict: string; decision: string; issues: ReviewIssue[]; critic_ok: boolean }[];
  actor_calls: number;
  open_issues: ReviewIssue[];
  review_ms: number;
};

export type SessionEstimate = {
  session_id: string;
  tier: Tier | null;
  project_metadata: ProjectMetadata;
  memory_changes: MemoryChange[];
  kind: "estimation" | "estimation_update" | "answer";
  message: string | null;
  estimation: string | null;
  estimation_structured: TierEstimate | null;
  warnings: { code: string; severity: string; message: string }[];
  attachments: { name: string; pages: number; chars: number; truncated: boolean; ms: number }[];
  review: ReviewTrace | null;
  tools: { tool: string; ok: boolean; ms: number; error: string | null }[];
  model: string;
  prompt_version: string;
  usage: { input_tokens: number; output_tokens: number };
  cost_usd: number | null;
  latency_ms: number;
  cache_hit: boolean;
  cache_kind: string | null;
};

export type SessionState = {
  session_id: string;
  status: "active" | "archived";
  turns: number;
  tier: Tier | null;
  project_metadata: ProjectMetadata;
  memory_log: MemoryChange[];
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null,
  ) {
    super(message);
  }
}

function headers(token: string | null, json = false): HeadersInit {
  const result: Record<string, string> = {};
  if (token) result.Authorization = `Bearer ${token}`;
  if (json) result["Content-Type"] = "application/json";
  return result;
}

async function parse<T>(response: Response): Promise<T> {
  const body = (await response.json().catch(() => ({}))) as { detail?: string; error?: string };
  if (!response.ok) {
    throw new ApiError(body.detail || `Error ${response.status}`, response.status, body.error ?? null);
  }
  return body as T;
}

export async function createSession(
  token: string | null,
  options: { previousSessionId?: string | null; inheritMemory?: boolean; signal?: AbortSignal } = {},
): Promise<{ session_id: string; inherited_from: string | null }> {
  const body = options.previousSessionId
    ? JSON.stringify({ previous_session_id: options.previousSessionId, inherit_memory: Boolean(options.inheritMemory) })
    : undefined;
  const response = await fetch(`${apiBase()}/sessions`, {
    method: "POST",
    headers: headers(token, Boolean(body)),
    body,
    signal: options.signal,
  });
  return parse(response);
}

export async function estimateTurn(token: string | null, sessionId: string, form: FormData): Promise<SessionEstimate> {
  const response = await fetch(`${apiBase()}/sessions/${sessionId}/estimate`, {
    method: "POST",
    headers: headers(token),
    body: form,
  });
  return parse(response);
}

export async function getSession(token: string | null, sessionId: string): Promise<SessionState> {
  const response = await fetch(`${apiBase()}/sessions/${sessionId}`, { headers: headers(token) });
  return parse(response);
}
