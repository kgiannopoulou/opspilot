// Thin API client. The access token lives in memory only (never localStorage); the refresh token is an
// httpOnly cookie the browser sends to /api/v1/auth/* automatically. On a 401 we refresh once and retry.

const BASE = "/api/v1";

export type Severity = "SEV1" | "SEV2" | "SEV3" | "SEV4";
export type IncidentStatus = "INVESTIGATING" | "IDENTIFIED" | "MONITORING" | "RESOLVED";
export type TicketStatus = "OPEN" | "TRIAGED" | "INVESTIGATING" | "RESOLVED" | "CLOSED";
export type Priority = "P1" | "P2" | "P3" | "P4";

export interface UserRef { id: number; email: string; full_name: string }
export interface ServiceRef { id: number; name: string; display_name: string }
export interface CurrentUser extends UserRef { role: string; permissions: string[]; last_login_at: string | null }
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }

export interface IncidentSummary {
  id: number; number: string; title: string; severity: Severity; status: IncidentStatus;
  primary_service: ServiceRef | null; affected_services: ServiceRef[]; commander: UserRef | null;
  started_at: string; resolved_at: string | null; duration_minutes: number | null; ticket_count: number;
}

export interface ServiceHealth {
  name: string; display_name: string; tier: number; owner_team: string;
  status: "healthy" | "degraded" | "critical" | "unknown";
  error_rate: number | null; latency_p95_ms: number | null; latency_baseline_ms: number | null;
  as_of: string | null; active_incidents: number; error_rate_series: { ts: string; error_rate: number }[];
}

export interface DashboardSummary {
  generated_at: string;
  active_incidents: Record<Severity, number>;
  resolved_last_30d: number;
  mttr_minutes: number | null;
  mttr_minutes_previous: number | null;
  tickets: { open: number; unassigned: number; created_today: number; open_p1: number };
  incident_trend: ({ date: string } & Record<Severity, number>)[];
  ticket_volume: { date: string; count: number }[];
  services: ServiceHealth[];
  active: IncidentSummary[];
}

export interface Ticket {
  id: number; number: string; title: string; description: string; priority: Priority; status: TicketStatus;
  source: string; labels: string[]; category: TicketCategory | null; resolution: string | null; service: ServiceRef | null;
  reporter: UserRef; assignee: UserRef | null; created_at: string; updated_at: string;
  triaged_at: string | null; resolved_at: string | null; closed_at: string | null;
  allowed_transitions: TicketStatus[];
}
export type TicketCategory = "PAYMENTS" | "AUTHENTICATION" | "ORDERS" | "NOTIFICATIONS" | "DATABASE" | "PERFORMANCE" | "OTHER";
export interface Classification {
  id: number; engine: string; category: TicketCategory; priority: Priority; service: ServiceRef | null; confidence: number;
  rationale: string; signals: Record<string, unknown>;
  status: "PENDING" | "ACCEPTED" | "MODIFIED" | "REJECTED" | "SUPERSEDED";
  final_category: TicketCategory | null; final_priority: Priority | null; final_service: ServiceRef | null;
  review_note: string | null; reviewed_by: UserRef | null; reviewed_at: string | null; created_at: string;
}
export interface PatternSignal {
  ref: string; level: string; message: string; count: number; baseline_count: number; new: boolean; first_seen: string;
  sample: Record<string, unknown>;
}
export interface MetricSignal {
  ref: string; metric: string; label: string; baseline: number; current: number; change_pct: number; anomalous: boolean;
  display: string;
}
export interface ServiceSignals {
  service: string; relation: "reported" | "dependency" | "dependent" | "platform"; status: "anomalous" | "normal" | "no data";
  summary: string; patterns: PatternSignal[]; metrics: MetricSignal[];
}
export interface Correlation {
  ticket: string; anchor: string; window_start: string; window_end: string; headline: string; services: ServiceSignals[];
  suspected_incident: { number: string; title: string; services: string[] } | null;
}
export interface TicketComment { id: number; body: string; internal: boolean; author: UserRef; created_at: string }
export interface TicketEvent {
  id: number; type: string; from_value: string | null; to_value: string | null;
  data: Record<string, unknown>; actor: UserRef | null; created_at: string;
}
export interface ServiceOut {
  id: number; name: string; display_name: string; tier: number; owner_team: string; description: string;
  dependencies: string[];
}
export interface AdminUser {
  id: number; email: string; full_name: string; role: string; is_active: boolean;
  last_login_at: string | null; created_at: string;
}

export type EvidenceKind =
  | "LOG_PATTERN" | "METRIC_DELTA" | "DEPENDENCY_HEALTH" | "SIMILAR_INCIDENT" | "DOCUMENT" | "CHANGE_EVENT";
export type EvidenceStance = "SUPPORTS" | "CONTRADICTS" | "CONTEXT";

export interface Evidence {
  id: number; position: number; ref: string; kind: EvidenceKind; stance: EvidenceStance; summary: string; weight: number;
  data: Record<string, unknown>;
  related_incident: { id: number; number: string; title: string } | null;
  document: { id: number; slug: string; title: string; category: string } | null;
}
export interface QueryRun {
  id: number; sql: string; edited: boolean; status: "SUCCEEDED" | "FAILED" | "REJECTED"; columns: string[];
  rows: unknown[][]; row_count: number; truncated: boolean; duration_ms: number | null; error: string | null;
  executed_by: UserRef | null; executed_at: string;
}
export interface InvestigationQuery {
  id: number; position: number; title: string; purpose: string; sql: string; safe_sql: string | null; source: string;
  risk: "READ_ONLY" | "BLOCKED"; violations: string[]; tables: string[]; last_run: QueryRun | null;
}
export interface SqlVerdict {
  ok: boolean; risk: "READ_ONLY" | "BLOCKED"; safe_sql: string | null; violations: string[]; tables: string[];
}
export interface GroundingCheck {
  passed: boolean; cited: string[]; unknown_refs: string[]; uncited_sentences: string[]; unsupported_numbers: string[];
}
export interface Grounding extends Partial<GroundingCheck> {
  rejected_llm_draft?: GroundingCheck; llm_error?: string;
}
export interface Investigation {
  id: number; status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED"; engine: string; hypothesis_key: string | null;
  root_cause: string | null; narrative: string | null; search_query: string | null;
  confidence: number | null; recommended_steps: string[]; suggested_sql: string | null;
  window_start: string; window_end: string; error: string | null; grounding: Grounding; duration_ms: number | null;
  created_at: string; completed_at: string | null; requested_by: UserRef | null; evidence: Evidence[];
  queries: InvestigationQuery[]; attempts: number; started_at: string | null;
  progress: { key: string; label: string; status: "pending" | "running" | "done" | "failed"; started_at: string | null; finished_at: string | null }[];
}
export interface LinkedTicket {
  number: string; title: string; priority: Priority; status: TicketStatus; assignee: UserRef | null; created_at: string;
}
export interface IncidentDetail extends IncidentSummary {
  summary: string; root_cause: string | null; signature: string | null; detected_at: string | null;
  tickets: LinkedTicket[]; allowed_transitions: IncidentStatus[]; latest_investigation: Investigation | null;
}
export interface IncidentTelemetry {
  window_start: string; window_end: string; incident_start: string;
  series: { service: string; metric: string; points: { ts: string; value: number }[] }[];
}
export interface LogLine { ts: string; service: string; level: string; message: string; attributes: Record<string, unknown> }
export interface KnowledgeDoc {
  id: number; slug: string; title: string; category: "runbook" | "troubleshooting" | "architecture" | "process";
  service: ServiceRef | null; tags: string[]; updated_at: string; content?: string;
}

export interface SearchHit {
  chunk_id: number; slug: string; title: string; category: string; section: string; heading: string; content: string;
  similarity: number; score: number;
}
export interface SearchResults { query: string; embedding_model: string; hits: SearchHit[] }

export interface BackgroundJob {
  id: string; type: string; status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "RETRYING" | "DEAD"; entity_type: string;
  entity_id: string; payload: Record<string, unknown>; attempts: number; error: string | null; worker: string | null;
  enqueued_at: string; started_at: string | null; finished_at: string | null;
}
export interface JobsOverview {
  queue: { available: boolean; waiting: number; in_progress: number; delayed: number; dead: number };
  by_status: Record<string, number>; recent: BackgroundJob[];
}

export interface ActivityItem {
  id: number; occurred_at: string; action: string; entity_type: string; entity_id: string; actor: UserRef | null;
  description: string;
}
export interface AuditEvent extends ActivityItem {
  changes: Record<string, unknown>; ip_address: string | null; chain_seq: number | null; prev_hash: string | null;
  hash: string | null;
}
export interface ChainVerification {
  valid: boolean; rows: number; head_seq: number | null; head_hash: string | null;
  breaks: { chain_seq: number; audit_id: number; reason: string }[]; verified_at: string;
}

export type FeedbackReason =
  | "WRONG_ROOT_CAUSE" | "MISSING_EVIDENCE" | "IRRELEVANT_DOCUMENTS" | "UNHELPFUL_STEPS" | "WRONG_QUERY" | "UNSUPPORTED_CLAIM" | "OTHER";
export interface Feedback {
  id: number; investigation_id: number; rating: 1 | -1; reason: FeedbackReason | null; comment: string | null; user: UserRef;
  created_at: string; updated_at: string;
}
export interface FeedbackSummary { mine: Feedback | null; useful: number; incorrect: number; reasons: Record<string, number> }
type RateRow = { useful: number; incorrect: number; useful_rate: number };
export interface AIQualityReport {
  investigations: {
    completed: number; failed: number; rated: number; votes: number; useful: number; incorrect: number; useful_rate: number | null;
    reasons: Record<string, number>; by_engine: Record<string, RateRow>; by_hypothesis: Record<string, RateRow>;
    grounding_passed: number; llm_drafts: number; llm_drafts_rejected: number;
  };
  triage: { total: number; by_status: Record<string, number>; acceptance_rate: number | null; corrections: Record<string, number> };
  recent_negative: { incident: string; investigation_id: number; hypothesis: string | null; reason: string | null; comment: string | null; user: string; at: string }[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

let accessToken: string | null = null;
let refreshInFlight: Promise<CurrentUser | null> | null = null;
let onSessionExpired: (() => void) | null = null;

export function setSessionExpiredHandler(handler: () => void) { onSessionExpired = handler; }

interface TokenResponse { access_token: string; expires_in: number; user: CurrentUser }

export async function login(email: string, password: string): Promise<CurrentUser> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  const body = (await res.json()) as TokenResponse;
  accessToken = body.access_token;
  return body.user;
}

/** Exchange the refresh cookie for a new access token. Concurrent callers share one request. */
export function refreshSession(): Promise<CurrentUser | null> {
  refreshInFlight ??= (async () => {
    try {
      const res = await fetch(`${BASE}/auth/refresh`, { method: "POST" });
      if (!res.ok) { accessToken = null; return null; }
      const body = (await res.json()) as TokenResponse;
      accessToken = body.access_token;
      return body.user;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

export async function logout(): Promise<void> {
  await fetch(`${BASE}/auth/logout`, { method: "POST" }).catch(() => undefined);
  accessToken = null;
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((d: { msg: string }) => d.msg).join("; ");
  } catch { /* not JSON */ }
  return `${res.status} ${res.statusText}`;
}

export async function api<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (res.status === 401 && retry) {
    const user = await refreshSession();
    if (user) return api<T>(path, init, false);
    onSessionExpired?.();
  }
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return (res.status === 204 ? undefined : await res.json()) as T;
}

/** Save an authenticated GET response as a file (the bearer token cannot ride on a plain link). */
export async function download(path: string, filename: string): Promise<void> {
  const res = await fetch(`${BASE}${path}`, { headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {} });
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  const url = URL.createObjectURL(await res.blob());
  Object.assign(document.createElement("a"), { href: url, download: filename }).click();
  URL.revokeObjectURL(url);
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = (path: string) => api<void>(path, { method: "DELETE" });

export function query(params: Record<string, string | number | boolean | string[] | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => q.append(key, v));
    else q.set(key, String(value));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}
