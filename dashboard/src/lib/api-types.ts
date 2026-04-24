/**
 * Hand-curated TypeScript types matching the brain's REST shapes.
 *
 * These mirror the Pydantic response models in
 * `packages/z4j-brain/backend/src/z4j_brain/api/*.py`. We keep
 * them hand-written for v1 - `openapi-typescript` codegen lands
 * in Phase 1.1 once the schema settles. Hand-writing is fine
 * because the surface is small (~25 endpoints) and ANY drift
 * between brain + dashboard is caught immediately by TypeScript
 * at the call site.
 *
 * Naming convention: `*Public` matches the brain's response model
 * class name. Request payloads are `*Request`.
 */

// ---------------------------------------------------------------------------
// Auth + setup
// ---------------------------------------------------------------------------

/** One of the current user's memberships - the three-field shape
 *  used for the project switcher. Mirrors brain-side
 *  ``api.auth.UserMembershipSummary``. Distinct from the full
 *  ``MembershipPublic`` resource used by the memberships CRUD
 *  endpoints - that one carries ``id`` / ``user_email`` /
 *  ``created_at`` fields the switcher doesn't need. */
export interface UserMembershipSummary {
  project_id: string;
  project_slug: string;
  role: "viewer" | "operator" | "admin";
}

export interface UserPublic {
  id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  display_name: string | null;
  is_admin: boolean;
  timezone: string;
  created_at: string;
}

export interface UserMePublic extends UserPublic {
  memberships: UserMembershipSummary[];
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  user: UserPublic;
}

export interface SetupStatusResponse {
  first_boot: boolean;
}

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

export interface ProjectPublic {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  environment: string;
  timezone: string;
  retention_days: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

export type AgentState = "online" | "offline" | "unknown";

export interface AgentPublic {
  id: string;
  project_id: string;
  name: string;
  state: AgentState;
  protocol_version: string;
  framework_adapter: string;
  engine_adapters: string[];
  scheduler_adapters: string[];
  capabilities: Record<string, unknown>;
  last_seen_at: string | null;
  last_connect_at: string | null;
  created_at: string;
  /** True when the agent has connected at least once and its
   *  advertised protocol_version is older than the brain's
   *  CURRENT_PROTOCOL. */
  is_outdated: boolean;
  /** Operator-supplied host label sent by the agent in the hello
   *  frame's `host.name` field (from `Z4J_AGENT_NAME` / settings.Z4J
   *  `agent_name`). Distinct from `name` (set at mint time). Useful
   *  when one agent token is shared across multiple workers and you
   *  want per-instance labels. Null if the agent never set it. */
  host_name?: string | null;
}

export interface CreateAgentRequest {
  name: string;
}

export interface CreateAgentResponse {
  agent: AgentPublic;
  /** Plaintext token. Returned ONCE and never persisted again. */
  token: string;
  /**
   * Per-project signing secret (urlsafe-base64 of 32 raw bytes).
   * Returned ONCE alongside the token; the agent refuses to start
   * without it. The brain re-derives this value on every frame
   * from the master secret + project_id, so it is not stored
   * anywhere recoverable.
   */
  hmac_secret: string;
}

// ---------------------------------------------------------------------------
// Tasks
// ---------------------------------------------------------------------------

export type TaskState =
  | "pending"
  | "received"
  | "started"
  | "success"
  | "failure"
  | "retry"
  | "revoked"
  | "rejected"
  | "unknown";

export type TaskPriority = "critical" | "high" | "normal" | "low";

export interface TaskPublic {
  id: string;
  project_id: string;
  engine: string;
  task_id: string;
  name: string;
  queue: string | null;
  state: TaskState;
  priority: TaskPriority;
  args: unknown | null;
  kwargs: unknown | null;
  result: unknown | null;
  exception: string | null;
  traceback: string | null;
  retry_count: number;
  eta: string | null;
  received_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  runtime_ms: number | null;
  worker_name: string | null;
  parent_task_id: string | null;
  root_task_id: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export interface TaskListResponse {
  items: TaskPublic[];
  next_cursor: string | null;
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

export interface EventPublic {
  id: string;
  project_id: string;
  agent_id: string;
  engine: string;
  task_id: string;
  kind: string;
  occurred_at: string;
  payload: Record<string, unknown>;
}

export interface EventListResponse {
  items: EventPublic[];
  next_cursor: string | null;
}

// ---------------------------------------------------------------------------
// Workers
// ---------------------------------------------------------------------------

export type WorkerState = "online" | "offline" | "draining" | "unknown";

export interface WorkerPublic {
  id: string;
  project_id: string;
  engine: string;
  name: string;
  hostname: string | null;
  pid: number | null;
  concurrency: number | null;
  queues: string[];
  state: WorkerState;
  last_heartbeat: string | null;
  load_average: number[] | null;
  active_tasks: number;
  processed: number;
  failed: number;
  succeeded: number;
  retried: number;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Queues
// ---------------------------------------------------------------------------

export interface QueuePublic {
  id: string;
  project_id: string;
  name: string;
  engine: string;
  broker_type: string | null;
  broker_url_hint: string | null;
  last_seen_at: string | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Schedules
// ---------------------------------------------------------------------------

export type ScheduleKind = "cron" | "interval" | "solar" | "clocked";

export interface SchedulePublic {
  id: string;
  project_id: string;
  engine: string;
  scheduler: string;
  name: string;
  task_name: string;
  kind: ScheduleKind;
  expression: string;
  timezone: string;
  queue: string | null;
  args: unknown;
  kwargs: unknown;
  priority: TaskPriority;
  is_enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  total_runs: number;
  external_id: string | null;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

export type CommandStatus =
  | "pending"
  | "dispatched"
  | "completed"
  | "failed"
  | "timeout"
  | "cancelled";

export interface CommandPublic {
  id: string;
  project_id: string;
  agent_id: string | null;
  issued_by: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  payload: Record<string, unknown>;
  status: CommandStatus;
  result: unknown | null;
  error: string | null;
  issued_at: string;
  dispatched_at: string | null;
  completed_at: string | null;
  timeout_at: string;
}

export interface CommandListResponse {
  items: CommandPublic[];
  next_cursor: string | null;
}

export interface RetryTaskRequest {
  agent_id: string;
  engine: string;
  task_id: string;
  override_args?: unknown[] | null;
  override_kwargs?: Record<string, unknown> | null;
  eta_seconds?: number | null;
  idempotency_key?: string | null;
}

export interface CancelTaskRequest {
  agent_id: string;
  engine: string;
  task_id: string;
  idempotency_key?: string | null;
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

export interface AuditLogPublic {
  id: string;
  project_id: string | null;
  user_id: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  result: string;
  outcome: string | null;
  event_id: string | null;
  metadata: Record<string, unknown>;
  source_ip: string | null;
  user_agent: string | null;
  occurred_at: string;
}

export interface AuditLogListResponse {
  items: AuditLogPublic[];
  next_cursor: string | null;
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------

export interface TaskStateCounts {
  pending: number;
  received: number;
  started: number;
  success: number;
  failure: number;
  retry: number;
  revoked: number;
  rejected: number;
  unknown: number;
}

export interface StatsResponse {
  tasks_by_state: TaskStateCounts;
  tasks_total: number;
  tasks_failed_24h: number;
  tasks_succeeded_24h: number;
  failure_rate_24h: number;
  agents_online: number;
  agents_offline: number;
  workers_online: number;
  workers_offline: number;
  commands_pending: number;
  commands_completed_24h: number;
  commands_failed_24h: number;
  commands_timeout_24h: number;
  queue_depths: QueueHealth[];
  system_health: SystemHealth | null;
}

export interface QueueHealth {
  name: string;
  engine: string;
  pending_count: number;
  broker_type: string | null;
  last_seen_at: string | null;
}

export interface SystemHealth {
  status: "healthy" | "degraded" | "critical";
  agents_all_online: boolean;
  queue_depth_ok: boolean;
  failure_rate_ok: boolean;
  brain_db_ok: boolean;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export interface ErrorEnvelope {
  error: string;
  message: string;
  request_id: string | null;
  details: Record<string, unknown>;
}
