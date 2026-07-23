export type JobStatus =
  | "queued"
  | "running"
  | "paused"
  | "cancelled"
  | "failed"
  | "completed";

export interface ClientInfo {
  id: string;
  name: string;
  available: boolean;
  reason: string | null;
  quality_mode: boolean;
  native_mode: boolean;
}

export interface DatasetInfo {
  id: string;
  name: string;
  group: string;
  config: string;
  purpose: string;
  recommended_profiles: string[];
  sync_target: string | null;
  estimated_download?: string;
  version: string;
  ready: boolean;
  file_present: boolean;
  checksum_valid: boolean;
  file_size_bytes: number | null;
  license: string;
  redistribution: string;
  designation: string;
  source: { url: string; revision: string; split: string };
  checksum: string;
  counts: {
    scenarios: number;
    contexts: number;
    questions: number;
    categories: Record<string, number>;
  };
  categories: Record<string, number>;
  isolation: string;
  ingest_mode: string;
  metrics: { supported: string[]; unsupported: string[] };
}

export interface Catalog {
  clients: ClientInfo[];
  datasets: DatasetInfo[];
  profiles: Array<{
    id: string;
    name: string;
    description: string;
    available: boolean;
  }>;
}

export interface SystemSnapshot {
  cpu_percent: number;
  memory_percent: number;
  disk_percent: number;
  ollama: {
    online: boolean;
    model: string | null;
    latency_ms: number | null;
    url?: string;
    models?: string[];
  };
  gpu: null | {
    utilization_percent: number;
    memory_used_mb: number;
    memory_total_mb: number;
  };
}

export interface ResultSummary {
  schema_version: number;
  job_id: string;
  profile: string;
  verified: boolean;
  total_rows: number;
  systems: Record<
    string,
    {
      questions: number;
      valid: boolean;
      infrastructure_errors: number;
      accuracy: number;
      hit_at_1: number;
      hit_at_3: number;
      hit_at_5: number;
      mrr: number;
      ndcg: number;
      exact_match: number | null;
      token_f1: number | null;
      avg_retrieval_ms: number | null;
      avg_generation_ms: number | null;
    }
  >;
  provisional?: boolean;
}

export interface Job {
  id: string;
  name: string;
  profile: "quality" | "native" | "capacity";
  status: JobStatus;
  created_at: string;
  updated_at: string;
  progress: number;
  eta_seconds: number | null;
  eta_confidence: "düşük" | "orta" | "yüksek";
  current_task: string | null;
  archived: boolean;
  error: string | null;
  result: ResultSummary | null;
  provisional_result: ResultSummary | null;
  queue_position: number | null;
  started_at: string | null;
  active_elapsed_seconds: number;
  time_limit_minutes: number | null;
  pause_reason: string | null;
  progress_snapshot: ProgressSnapshot | null;
  plan?: {
    clients: string[];
    shards: Array<{
      id: string;
      questions: number;
      contexts: number;
      scenarios: number;
    }>;
    tasks: Array<{
      id: string;
      client: string;
      shard_id: string;
      status: string;
    }>;
    request?: Record<string, unknown>;
  };
}

export interface ProgressSnapshot {
  task_id: string;
  client: string;
  shard_id: string;
  task_index: number;
  task_total: number;
  task_progress: number;
  phase: string;
  status: string;
  scenario_index: number;
  scenario_total: number;
  context_index: number | null;
  context_total: number | null;
  question_index: number;
  question_total: number;
  question_id: string | null;
  elapsed_active_seconds: number;
}

export interface PlanPreview {
  ready: boolean;
  blockers: string[];
  tasks: number;
  clients: string[];
  dataset: {
    name: string;
    version: string;
    scenarios: number;
    questions: number;
    contexts: number;
  };
  shards: Array<{
    index: number;
    scenarios: number;
    questions: number;
    contexts: number;
    scenario_ids?: string[];
    estimated_seconds?: number | null;
  }>;
  shard_mode: string;
  target_shard_minutes: number;
  estimated_total_seconds: number | null;
  eta_confidence: string;
  requires_ollama: boolean;
  generator_model: string | null;
}

export interface QuestionRow {
  client: string;
  shard_id: string;
  scenario_id: string;
  question_id: string;
  query: string | null;
  ground_truth: string;
  reference_answers: string[];
  actual_answer: string;
  expected_context_ids: string[];
  retrieved_context_ids: string[];
  score: number;
  is_correct: boolean;
  failure_attribution: string;
}

export interface OllamaSettings {
  url: string | null;
  model: string | null;
  source: "saved" | "environment";
  online: boolean;
  models: string[];
  error: string | null;
}

export interface DatasetScenario {
  id: string;
  contexts: Array<{ id: string; text: string }>;
  questions: Array<{
    id: string;
    query: string;
    ground_truth: string;
    reference_answers: string[];
    supporting_context_ids: string[];
    category: string | null;
  }>;
}

export interface JobDiagnostics {
  job_id: string;
  status: JobStatus;
  summary: string | null;
  failed_tasks: Array<{
    id: string;
    client: string;
    shard_id: string;
    attempt: number;
    root_error: string;
    resolution: string;
    traceback: string;
    logs: { stdout?: string; stderr?: string };
  }>;
  checks: Array<{
    status: "passed" | "warning" | "failed" | "info";
    label: string;
    detail: string;
  }>;
  recent_events: Array<Record<string, unknown>>;
  artifacts: {
    root: string;
    plan: string;
    events: string;
    logs: string;
  };
}
