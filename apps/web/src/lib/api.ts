// Typed client for the Torqrun REST API (/api/v1). Mirrors apps/api/src/torqrun_api/schemas.py.

export type DatabaseStatus = "ok" | "unreachable" | "migrations_pending";

export interface DatabaseCheck {
  status: DatabaseStatus;
  schema_revision: string | null;
  expected_revision: string | null;
}

export interface LicenseSummary {
  customer: string;
  plan: "team" | "enterprise";
  seats: number;
  expires_at: string;
  state: "active" | "grace" | "expired";
}

export interface SystemInfo {
  name: "torqrun";
  version: string;
  edition: "community" | "team" | "enterprise";
  features: string[];
  license: LicenseSummary | null;
  environment: string;
  database: DatabaseCheck;
}

export type RunStatus =
  | "QUEUED"
  | "DISPATCHED"
  | "STARTING"
  | "RUNNING"
  | "RETRY_WAIT"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCEL_REQUESTED"
  | "CANCELLED"
  | "TIMED_OUT"
  | "LOST";

export const TERMINAL_STATUSES: ReadonlySet<RunStatus> = new Set([
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
  "TIMED_OUT",
  "LOST",
]);

export interface RetrySpec {
  max_attempts: number;
  backoff_seconds: number;
  backoff_factor: number;
  max_backoff_seconds: number;
  retry_on_timeout: boolean;
}

export interface JobSpec {
  runtime: "python" | "shell";
  script: string;
  args: string[];
  env: Record<string, string>;
  timeout_seconds: number;
  queue: string;
  priority: number;
  retry: RetrySpec;
  interrupt_policy: "fail" | "retry";
  max_concurrent: number | null;
  /** Environment variable -> secret name. */
  secrets: Record<string, string>;
  executor: "process" | "docker";
  container: ContainerSpec | null;
}

export interface ContainerSpec {
  image: string;
  network: "bridge" | "none";
  memory_mb: number | null;
  cpus: number | null;
  pull: "missing" | "always" | "never";
}

export interface Artifact {
  id: string;
  attempt_no: number;
  name: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
}

export type NotificationKind = "webhook" | "slack" | "teams" | "email";
export type NotificationEvent = "run.failed" | "run.succeeded" | "workflow.failed" | "workflow.succeeded" | "agent.offline";

export interface NotificationChannel {
  id: string;
  name: string;
  kind: NotificationKind;
  events: NotificationEvent[];
  queues: string[];
  enabled: boolean;
  target_hint: string;
  readable: boolean;
  created_by: string;
  created_at: string;
  last_delivery_status: "pending" | "sending" | "sent" | "failed" | null;
  last_delivery_at: string | null;
}

export interface NotificationDelivery {
  id: string;
  event: string;
  status: "pending" | "sending" | "sent" | "failed";
  attempts: number;
  last_error: string | null;
  created_at: string;
  sent_at: string | null;
  next_attempt_at: string;
}

export interface ChannelInput {
  name: string;
  kind?: NotificationKind;
  events: NotificationEvent[];
  queues: string[];
  enabled: boolean;
  url?: string;
  recipients?: string[];
}

export interface RunBrief {
  id: string;
  status: RunStatus;
  created_at: string;
  finished_at: string | null;
}

export interface Job {
  id: string;
  name: string;
  description: string;
  current_version: number;
  spec: JobSpec;
  created_at: string;
  updated_at: string;
  last_run: RunBrief | null;
  /** Newest first, up to 12. */
  recent_runs: RunBrief[];
}

export interface Run {
  id: string;
  job_id: string;
  job_name: string;
  job_version: number;
  trigger: string;
  status: RunStatus;
  queue: string;
  current_attempt: number;
  max_attempts: number;
  next_attempt_at: string | null;
  rerun_of: string | null;
  schedule_id?: string | null;
  scheduled_for?: string | null;
  exit_code: number | null;
  error_summary: string | null;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
}

export interface Attempt {
  attempt_no: number;
  status: RunStatus;
  agent_id: string | null;
  agent_name: string | null;
  dispatched_at: string | null;
  acked_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  pid: number | null;
  error_summary: string | null;
  log_bytes: number;
  last_log_seq: number | null;
}

export interface RunEvent {
  attempt_no: number | null;
  from_status: RunStatus | null;
  to_status: RunStatus;
  reason: string;
  actor: string;
  at: string;
}

export interface RunDetail extends Run {
  spec: JobSpec;
  attempts: Attempt[];
  events: RunEvent[];
}

export interface LogChunk {
  seq: number;
  stream: "stdout" | "stderr" | "system";
  ts: string;
  data: string;
}

export interface SystemStats {
  os_pretty?: string;
  kernel?: string;
  python_version?: string;
  cpu_count?: number;
  load_1m?: number;
  load_5m?: number;
  load_15m?: number;
  mem_total_bytes?: number;
  mem_available_bytes?: number;
  disk_total_bytes?: number;
  disk_free_bytes?: number;
  uptime_seconds?: number;
}

export interface AgentActiveRun {
  run_id: string;
  job_id: string;
  job_name: string;
  status: RunStatus;
  started_at: string | null;
}

export interface Agent {
  id: string;
  name: string;
  hostname: string;
  os: string;
  arch: string;
  agent_version: string;
  status: string;
  connected: boolean;
  max_slots: number;
  running: number;
  queues: string[];
  tags: string[];
  capabilities: string[];
  last_seen_at: string | null;
  created_at: string;
  system: SystemStats | null;
  active_runs: AgentActiveRun[];
}

export interface Queue {
  name: string;
  max_concurrency: number | null;
  paused: boolean;
  queued: number;
  running: number;
  agents_online: number;
}

export interface EnrollmentToken {
  id: string;
  prefix: string;
  description: string;
  state: "active" | "expired" | "used" | "revoked";
  uses: number;
  max_uses: number | null;
  queues: string[];
  tags: string[];
  expires_at: string | null;
  created_at: string;
  created_by: string;
}

export interface CreatedEnrollmentToken extends EnrollmentToken {
  token: string;
  install_command: string;
}

export interface ScheduleDefinition {
  kind: "cron" | "interval";
  cron: string | null;
  interval_seconds: number | null;
  timezone: string;
}

export interface ScheduleInput extends ScheduleDefinition {
  name: string;
  job_id: string | null;
  workflow_id?: string | null;
  misfire_policy: "skip" | "run_once" | "run_all";
  misfire_grace_seconds: number;
  max_catchup: number;
  overlap_policy: "allow" | "skip";
  enabled: boolean;
}

export interface Schedule extends ScheduleInput {
  id: string;
  target: "job" | "workflow";
  /** Name of the target job or workflow. */
  job_name: string;
  next_fire_at: string | null;
  upcoming: string[];
  last_fired_at: string | null;
  last_run_id: string | null;
  last_run_status: RunStatus | null;
  skipped_count: number;
  last_skipped_at: string | null;
  last_skip_reason: string | null;
  created_at: string;
}

export interface WorkflowTask {
  key: string;
  job: string;
  depends_on: string[];
  trigger_rule: "all_success" | "all_done" | "one_failed";
}

export type TaskState = "PENDING" | "ACTIVE" | "SUCCEEDED" | "FAILED" | "SKIPPED";

export interface Workflow {
  id: string;
  name: string;
  description: string;
  current_version: number;
  source: string;
  tasks: WorkflowTask[];
  layers: string[][];
  created_at: string;
  updated_at: string;
  last_run: { id: string; status: WorkflowRunStatus; created_at: string; finished_at: string | null } | null;
}

export type WorkflowRunStatus = "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";

export interface WorkflowRun {
  id: string;
  workflow_id: string;
  workflow_name: string;
  version: number;
  status: WorkflowRunStatus;
  trigger: string;
  rerun_of: string | null;
  schedule_id: string | null;
  scheduled_for: string | null;
  started_at: string;
  finished_at: string | null;
  duration_seconds: number | null;
  counts: Partial<Record<TaskState, number>>;
}

export interface WorkflowTaskRun extends WorkflowTask {
  state: TaskState;
  run_id: string | null;
  run_status: RunStatus | null;
  reused: boolean;
  note: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
}

export interface WorkflowRunDetail extends WorkflowRun {
  tasks: WorkflowTaskRun[];
  layers: string[][];
}

export interface WorkflowValidation {
  ok: boolean;
  error: string | null;
  tasks: WorkflowTask[];
  layers: string[][];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// Auth & administration --------------------------------------------------------------

export type Role = "viewer" | "operator" | "admin";
export const ROLE_RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };

export interface Me {
  id: string;
  email: string;
  name: string;
  role: Role;
  via: "session" | "token";
}

export interface AuthStatus {
  setup_required: boolean;
  user: Me | null;
}

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
  disabled: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface ApiToken {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
}

export interface Secret {
  id: string;
  name: string;
  description: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  readable: boolean;
}

export interface AuditEvent {
  id: number;
  at: string;
  actor: string;
  action: string;
  target_type: string;
  target_id: string;
  status_code: number;
  ip: string;
  request_id: string;
}

/** Fired when the API says the session is gone; the auth gate shows the sign-in page. */
export const UNAUTHORIZED_EVENT = "torqrun:unauthorized";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ValidationIssue {
  loc: (string | number)[];
  msg: string;
}

function describeDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return (detail as ValidationIssue[])
      .map((d) => `${d.loc.filter((p) => p !== "body").join(".")}: ${d.msg}`)
      .join("; ");
  }
  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      // The custom header is the API's CSRF guard: only same-origin scripts can send it.
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Torqrun-Client": "web",
        ...init?.headers,
      },
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("Cannot reach the Torqrun API.", null);
  }
  if (response.status === 401 && !path.startsWith("/api/v1/auth/")) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
  if (!response.ok) {
    let message = `API responded with HTTP ${response.status}.`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      message = describeDetail(body.detail) ?? message;
    } catch {
      // non-JSON error body: keep the generic message
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });

export const api = {
  systemInfo: (signal?: AbortSignal) => request<SystemInfo>("/api/v1/system/info", { signal }),

  listJobs: (params: { q?: string; limit?: number; offset?: number } = {}) =>
    request<Page<Job>>(`/api/v1/jobs?${query(params)}`),
  getJob: (id: string) => request<Job>(`/api/v1/jobs/${id}`),
  createJob: (body: { name: string; description: string; spec: Partial<JobSpec> }) =>
    request<Job>("/api/v1/jobs", { method: "POST", ...json(body) }),
  updateJob: (id: string, body: { description: string; spec: Partial<JobSpec> }) =>
    request<Job>(`/api/v1/jobs/${id}`, { method: "PUT", ...json(body) }),
  /** A fresh idempotency key per user action makes double-clicks and network retries safe. */
  triggerRun: (jobId: string, key: string = crypto.randomUUID()) =>
    request<Run>(`/api/v1/jobs/${jobId}/runs`, { method: "POST", headers: { "Idempotency-Key": key } }),
  cancelRun: (id: string) => request<Run>(`/api/v1/runs/${id}/cancel`, { method: "POST" }),
  retryRun: (id: string, key: string = crypto.randomUUID()) =>
    request<Run>(`/api/v1/runs/${id}/retry`, { method: "POST", headers: { "Idempotency-Key": key } }),
  rerunRun: (id: string, key: string = crypto.randomUUID()) =>
    request<Run>(`/api/v1/runs/${id}/rerun`, { method: "POST", headers: { "Idempotency-Key": key } }),

  listRuns: (
    params: { job_id?: string; agent_id?: string; status?: RunStatus; limit?: number; offset?: number } = {},
  ) =>
    request<Page<Run>>(`/api/v1/runs?${query(params)}`),
  getRun: (id: string) => request<RunDetail>(`/api/v1/runs/${id}`),
  runStreamUrl: (id: string, attempt?: number) => `/api/v1/runs/${id}/stream${attempt ? `?attempt=${attempt}` : ""}`,
  logDownloadUrl: (id: string, attempt?: number) =>
    `/api/v1/runs/${id}/logs/download${attempt ? `?attempt=${attempt}` : ""}`,

  listAgents: () => request<Agent[]>("/api/v1/agents"),
  getAgent: (id: string) => request<Agent>(`/api/v1/agents/${id}`),
  agentAction: (id: string, action: "drain" | "resume" | "revoke") =>
    request<{ id: string; status: string }>(`/api/v1/agents/${id}/${action}`, { method: "POST" }),

  listEnrollmentTokens: () => request<EnrollmentToken[]>("/api/v1/enrollment-tokens"),
  createEnrollmentToken: (body: {
    description: string;
    ttl_minutes: number;
    max_uses: number | null;
    queues: string[];
    tags: string[];
  }) => request<CreatedEnrollmentToken>("/api/v1/enrollment-tokens", { method: "POST", ...json(body) }),
  revokeEnrollmentToken: (id: string) => request<void>(`/api/v1/enrollment-tokens/${id}`, { method: "DELETE" }),

  listSchedules: (filter: { job_id?: string; workflow_id?: string } = {}) =>
    request<Schedule[]>(`/api/v1/schedules?${query(filter)}`),
  createSchedule: (body: ScheduleInput) => request<Schedule>("/api/v1/schedules", { method: "POST", ...json(body) }),
  updateSchedule: (id: string, body: ScheduleInput) =>
    request<Schedule>(`/api/v1/schedules/${id}`, { method: "PUT", ...json(body) }),
  scheduleAction: (id: string, action: "pause" | "resume") =>
    request<Schedule>(`/api/v1/schedules/${id}/${action}`, { method: "POST" }),
  deleteSchedule: (id: string) => request<void>(`/api/v1/schedules/${id}`, { method: "DELETE" }),
  listTimezones: () => request<string[]>("/api/v1/schedules/timezones"),
  previewSchedule: (body: ScheduleDefinition & { count?: number }) =>
    request<{ next: string[] }>("/api/v1/schedules/preview", { method: "POST", ...json(body) }),

  listWorkflows: () => request<Workflow[]>("/api/v1/workflows"),
  getWorkflow: (id: string) => request<Workflow>(`/api/v1/workflows/${id}`),
  validateWorkflow: (source: string) =>
    request<WorkflowValidation>("/api/v1/workflows/validate", { method: "POST", ...json({ source }) }),
  createWorkflow: (body: { name: string; description: string; source: string }) =>
    request<Workflow>("/api/v1/workflows", { method: "POST", ...json(body) }),
  updateWorkflow: (id: string, body: { description: string; source: string }) =>
    request<Workflow>(`/api/v1/workflows/${id}`, { method: "PUT", ...json(body) }),
  runWorkflow: (id: string, key: string = crypto.randomUUID()) =>
    request<WorkflowRun>(`/api/v1/workflows/${id}/runs`, { method: "POST", headers: { "Idempotency-Key": key } }),
  listWorkflowRuns: (params: { workflow_id?: string; limit?: number; offset?: number } = {}) =>
    request<Page<WorkflowRun>>(`/api/v1/workflow-runs?${query(params)}`),
  getWorkflowRun: (id: string) => request<WorkflowRunDetail>(`/api/v1/workflow-runs/${id}`),
  cancelWorkflowRun: (id: string) => request<WorkflowRun>(`/api/v1/workflow-runs/${id}/cancel`, { method: "POST" }),
  rerunWorkflowRun: (id: string, mode: "failed" | "all") =>
    request<WorkflowRun>(`/api/v1/workflow-runs/${id}/rerun?mode=${mode}`, { method: "POST" }),

  authStatus: () => request<AuthStatus>("/api/v1/auth/status"),
  login: (body: { email: string; password: string }) => request<Me>("/api/v1/auth/login", { method: "POST", ...json(body) }),
  setup: (body: { email: string; password: string; name: string }) =>
    request<Me>("/api/v1/auth/setup", { method: "POST", ...json(body) }),
  logout: () => request<void>("/api/v1/auth/logout", { method: "POST" }),
  changePassword: (body: { current_password: string; new_password: string }) =>
    request<void>("/api/v1/auth/password", { method: "POST", ...json(body) }),
  listTokens: () => request<ApiToken[]>("/api/v1/auth/tokens"),
  createToken: (body: { name: string; expires_in_days: number | null }) =>
    request<ApiToken & { token: string }>("/api/v1/auth/tokens", { method: "POST", ...json(body) }),
  revokeToken: (id: string) => request<void>(`/api/v1/auth/tokens/${id}`, { method: "DELETE" }),

  listUsers: () => request<User[]>("/api/v1/users"),
  createUser: (body: { email: string; name: string; role: Role; password: string }) =>
    request<User>("/api/v1/users", { method: "POST", ...json(body) }),
  updateUser: (id: string, body: Partial<{ name: string; role: Role; disabled: boolean; password: string }>) =>
    request<User>(`/api/v1/users/${id}`, { method: "PATCH", ...json(body) }),
  deleteUser: (id: string) => request<void>(`/api/v1/users/${id}`, { method: "DELETE" }),

  listSecrets: () => request<Secret[]>("/api/v1/secrets"),
  createSecret: (body: { name: string; value: string; description: string }) =>
    request<Secret>("/api/v1/secrets", { method: "POST", ...json(body) }),
  updateSecret: (name: string, body: { value?: string; description?: string }) =>
    request<Secret>(`/api/v1/secrets/${encodeURIComponent(name)}`, { method: "PUT", ...json(body) }),
  deleteSecret: (name: string) => request<void>(`/api/v1/secrets/${encodeURIComponent(name)}`, { method: "DELETE" }),

  listArtifacts: (runId: string) => request<Artifact[]>(`/api/v1/runs/${runId}/artifacts`),
  artifactUrl: (runId: string, id: string) => `/api/v1/runs/${runId}/artifacts/${id}/download`,

  listChannels: () => request<NotificationChannel[]>("/api/v1/notification-channels"),
  createChannel: (body: ChannelInput) =>
    request<NotificationChannel & { signing_secret: string | null }>("/api/v1/notification-channels", { method: "POST", ...json(body) }),
  updateChannel: (id: string, body: ChannelInput) =>
    request<NotificationChannel>(`/api/v1/notification-channels/${id}`, { method: "PUT", ...json(body) }),
  deleteChannel: (id: string) => request<void>(`/api/v1/notification-channels/${id}`, { method: "DELETE" }),
  testChannel: (id: string) => request<{ status: string }>(`/api/v1/notification-channels/${id}/test`, { method: "POST" }),
  rotateSigningSecret: (id: string) =>
    request<NotificationChannel & { signing_secret: string | null }>(`/api/v1/notification-channels/${id}/rotate-signing-secret`, { method: "POST" }),
  listDeliveries: (id: string) => request<NotificationDelivery[]>(`/api/v1/notification-channels/${id}/deliveries`),
  retryDelivery: (id: string) => request<{ status: string }>(`/api/v1/notification-deliveries/${id}/retry`, { method: "POST" }),

  listAudit: (params: { actor?: string; before?: number; limit?: number } = {}) =>
    request<{ items: AuditEvent[]; next_before: number | null }>(`/api/v1/audit?${query(params)}`),

  listQueues: () => request<Queue[]>("/api/v1/queues"),
  updateQueue: (name: string, body: { max_concurrency: number | null; paused: boolean }) =>
    request<Queue>(`/api/v1/queues/${name}`, { method: "PUT", ...json(body) }),
};

function query(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") q.set(key, String(value));
  }
  return q.toString();
}

