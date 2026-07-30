import type {
  Catalog,
  DatasetInfo,
  DatasetScenario,
  Job,
  JobDiagnostics,
  OllamaSettings,
  PlanPreview,
  QuestionRow,
  ResultSummary,
  SystemSnapshot,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `İstek başarısız (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  catalog: () => request<Catalog>("/api/catalog"),
  system: () => request<SystemSnapshot>("/api/system"),
  ollama: () => request<OllamaSettings>("/api/settings/ollama"),
  testOllama: (url: string) =>
    request<{ online: boolean; url: string; models: string[] }>(
      "/api/settings/ollama/test",
      { method: "POST", body: JSON.stringify({ url }) },
    ),
  saveOllama: (url: string, model: string | null) =>
    request<{ saved: boolean }>("/api/settings/ollama", {
      method: "PUT",
      body: JSON.stringify({ url, model }),
    }),
  deleteOllama: () =>
    request<{ saved: boolean }>("/api/settings/ollama", { method: "DELETE" }),
  datasets: () => request<DatasetInfo[]>("/api/datasets"),
  dataset: (id: string) =>
    request<DatasetInfo>(`/api/datasets/${encodeURIComponent(id)}`),
  datasetScenarios: (id: string, offset = 0) =>
    request<{ total: number; items: DatasetScenario[] }>(
      `/api/datasets/${encodeURIComponent(id)}/scenarios?offset=${offset}&limit=10`,
    ),
  syncDataset: (id: string) =>
    request<{ id: string; status: string }>(
      `/api/datasets/${encodeURIComponent(id)}/sync`,
      { method: "POST", body: JSON.stringify({ confirm: true }) },
    ),
  datasetOperation: (id: string) =>
    request<{ id: string; status: string; progress: number; error: string | null }>(
      `/api/dataset-operations/${encodeURIComponent(id)}`,
    ),
  jobs: () => request<Job[]>("/api/jobs"),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  preview: (body: Record<string, unknown>) =>
    request<PlanPreview>("/api/plans/preview", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  create: (body: Record<string, unknown>) =>
    request<Job>("/api/jobs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  control: (id: string, action: "pause" | "cancel") =>
    request<{ accepted: boolean }>(`/api/jobs/${id}/control`, {
      method: "POST",
      body: JSON.stringify({ action }),
    }),
  resume: (id: string) =>
    request<{ accepted: boolean }>(`/api/jobs/${id}/resume`, {
      method: "POST",
    }),
  retry: (id: string) =>
    request<{ accepted: boolean }>(`/api/jobs/${id}/retry`, {
      method: "POST",
    }),
  extendTime: (id: string, minutes: number | null, removeLimit = false) =>
    request<{ accepted: boolean; time_limit_minutes: number | null }>(
      `/api/jobs/${id}/extend-time`,
      {
        method: "POST",
        body: JSON.stringify({ minutes, remove_limit: removeLimit }),
      },
    ),
  progress: (id: string) =>
    request<{
      progress: number;
      eta_seconds: number | null;
      active_elapsed_seconds: number;
      time_budget_remaining_seconds: number | null;
      snapshot: Job["progress_snapshot"];
      provisional_result: ResultSummary | null;
    }>(`/api/jobs/${id}/progress`),
  diagnostics: (id: string) =>
    request<JobDiagnostics>(`/api/jobs/${encodeURIComponent(id)}/diagnostics`),
  questions: (id: string, client: string) =>
    request<{ total: number; items: QuestionRow[] }>(
      `/api/jobs/${id}/questions?client=${encodeURIComponent(client)}&limit=200`,
    ),
  exportUrl: (id: string, format: "md" | "json" | "csv") =>
    `/api/jobs/${encodeURIComponent(id)}/export?format=${format}`,
};
