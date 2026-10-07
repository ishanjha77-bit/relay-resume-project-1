// Shapes of the platform API (Spring Boot, snake_case JSON). See
// apps/platform-api and contracts/schemas for the source of truth.

export type Severity = "critical" | "warning" | "info"

export type IncidentStatus = "OPEN" | "INVESTIGATING" | "DIAGNOSED" | "AWAITING_APPROVAL" | "RESOLVED" | "FAILED"

export interface IncidentSummary {
  id: string
  key: string
  title: string
  service: string | null
  severity: Severity
  status: IncidentStatus
  opened_at: string
  resolved_at: string | null
  root_cause_category: string | null
  root_cause_service: string | null
  cost_usd: number
  updated_at: string
}

export interface IncidentPage {
  items: IncidentSummary[]
  next_cursor: string | null
}

export interface IncidentAlert {
  fingerprint: string
  name: string
  service: string | null
  severity: string
  status: "firing" | "resolved"
  summary: string | null
  starts_at: string | null
  ends_at: string | null
}

export interface Citation {
  evidence_id: string
  quote: string
  shows: string
}

export interface Hypothesis {
  run_id: string
  rank: number
  category: string
  service: string
  component: string
  summary: string
  confidence: number
  evidence: Citation[]
  suggested_fix: string
  verdict: string
  /** What the investigator stated, when the reviewer lowered it. */
  original_confidence?: number | null
  /** The reviewer agent's verdict on this hypothesis. */
  review?: { verdict: "supported" | "weak" | "unsupported"; reason: string; model: string } | null
}

export interface TimelineEvent {
  id: number
  at: string
  kind: string
  message: string
  data: Record<string, unknown> | null
}

export type ApprovalStatus = "PENDING" | "APPROVED" | "REJECTED" | "EXECUTED" | "FAILED"

/** Something an agent asked to do, and what became of it (platform-api approvals). */
export interface Approval {
  id: string
  incident_id: string
  run_id: string
  kind: string
  title: string
  risk: "low" | "medium" | "high"
  rationale: string
  diff: string
  /** The exact JSON text that runs if approved; the approval token is bound to it. */
  action: string
  action_sha256: string
  requested_by_agent: string
  requested_at: string
  status: ApprovalStatus
  decided_by: string | null
  decision_reason: string | null
  decided_at: string | null
  result: { pull_request?: number; url?: string; branch?: string; [key: string]: unknown } | null
  error: string | null
}

/** A blameless postmortem, drafted by the postmortem writer when the incident was resolved. */
export interface PostmortemDocument {
  title: string
  summary: string
  impact: string
  detection: string
  root_cause: string
  resolution: string
  timeline: { at: string; event: string }[]
  action_items: { item: string; owner?: string | null; priority: "high" | "medium" | "low" }[]
  lessons: string[]
}

export interface Postmortem {
  incident_id: string
  run_id: string
  title: string
  document: PostmortemDocument
  markdown: string
  model: string
  written_at: string
}

/** A runbook or postmortem the alerts resemble, with its best-matching passage. */
export interface RelatedDoc {
  doc_id: string
  kind: "runbook" | "postmortem"
  title: string
  section: string
  score: number
  text: string
}

/** One caller -> callee pair of the service graph, from traces. */
export interface DependencyEdge {
  from: string
  to: string
  rps: number | null
  failed_ratio: number | null
  p95_s?: number | null
}

/** The triage agent's first look at the incident (null fields: the model gave no assessment). */
export interface Triage {
  run_id: string
  severity: Severity | null
  service: string | null
  summary: string | null
  leads: string[]
  query: string
  related: RelatedDoc[]
  model: string | null
  errors: string[]
  /** The service graph around the alerting services, and its blast radius. */
  dependencies?: DependencyEdge[]
  upstream?: string[]
  downstream?: string[]
}

export type Vote = "up" | "down" | "none"

/** Votes on one thing, and the signed-in user's own: 1 up, -1 down, 0 none. */
export interface Votes {
  up: number
  down: number
  mine: -1 | 0 | 1
}

/** The learning loop: documents by doc_id, hypotheses by "category:service". */
export interface Feedback {
  documents: Record<string, Votes>
  hypotheses: Record<string, Votes>
}

/** One vote: on a related document, or on a hypothesis of a run. */
export type FeedbackVote =
  { document: string; vote: Vote } | { hypothesis: { run_id: string; category: string; service: string }; vote: Vote }

export interface IncidentDetail {
  incident: IncidentSummary
  summary: string | null
  run_id: string | null
  triage?: Triage | null
  alerts: IncidentAlert[]
  hypotheses: Hypothesis[]
  approvals: Approval[]
  postmortem?: Postmortem | null
  timeline: TimelineEvent[]
  feedback?: Feedback
}

export type StepKind =
  | "triage.completed"
  | "run.started"
  | "llm.completed"
  | "agent.progress"
  | "tool.called"
  | "budget.exhausted"
  | "investigation.concluded"
  | "run.failed"
  | "approval.requested"
  | "action.executed"
  | "action.failed"
  | "review.completed"
  | "run.finished"
  | "postmortem.written"

export interface AgentStep {
  run_id: string
  seq: number
  agent: string
  kind: StepKind | string
  tool: string | null
  evidence_id: string | null
  input: Record<string, unknown> | null
  output: Record<string, unknown> | null
  tokens_in: number | null
  tokens_out: number | null
  cache_read_tokens: number | null
  cost_usd: number | null
  latency_ms: number | null
  created_at: string
}

export interface User {
  username: string
  display_name: string
  roles: string[]
}

export interface TokenResponse extends User {
  access_token: string
  token_type: string
  expires_in: number
}

export interface EvalRun {
  id: string
  batch: string
  scenario: string
  category: string
  status: "scored" | "agent_failed" | "no_alert" | "timeout"
  correct: boolean | null
  correct_top3: boolean | null
  top_category: string | null
  top_service: string | null
  confidence: number | null
  fix_score: number | null
  citations_verified: number | null
  injection_ok: boolean | null
  steps: number | null
  llm_calls: number | null
  prompt_tokens: number | null
  output_tokens: number | null
  cache_read_tokens: number | null
  cost_usd: number | null
  seconds: number | null
  agent_seconds: number | null
  model: string | null
  incident_id: string | null
  run_id: string | null
  run_at: string
  details: Record<string, unknown>
}

export interface EvalBatch {
  batch: string
  runs: number
  answered: number
  accuracy: number | null
  accuracy_top3: number | null
  median_seconds: number | null
  median_agent_seconds: number | null
  mean_steps: number | null
  mean_llm_calls: number | null
  mean_cost_usd: number | null
  total_cost_usd: number | null
  total_tokens: number | null
  models: string | null
  started_at: string
  finished_at: string
}

/** Live messages on /topic/incidents and /topic/incidents/{id}. */
export type LiveMessage = { type: "incident"; incident: IncidentSummary } | { type: "step"; step: AgentStep }
