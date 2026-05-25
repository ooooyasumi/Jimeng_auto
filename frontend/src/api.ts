const BASE = "/api";

let authToken: string | null = localStorage.getItem("token");

export function setToken(token: string | null) {
  authToken = token;
  if (token) localStorage.setItem("token", token);
  else localStorage.removeItem("token");
}

export function getToken(): string | null {
  return authToken;
}

async function request(path: string, options: RequestInit = {}): Promise<any> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> || {}),
  };
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }
  const res = await fetch(`${BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    setToken(null);
    window.location.reload();
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// Auth
export async function login(password: string) {
  const data = await request("/auth/login", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
  setToken(data.token);
  return data;
}

export async function checkAuth() {
  return request("/auth/check");
}

// Tasks
export interface Reference {
  type: "image" | "video" | "audio";
  cos_url: string;
  filename: string;
}

export interface TaskParams {
  duration: number;
  ratio: string;
  model_version: string;
}

export interface Task {
  id: number;
  type: string;
  status: "pending" | "running" | "done" | "failed";
  prompt: string;
  params: TaskParams;
  references: Reference[];
  submit_id: string | null;
  submitted_at: string | null;
  result_url: string | null;
  gen_status: string | null;
  error_message: string | null;
  position: number;
  session_id: number;
  created_at: string;
  updated_at: string;
}

export interface QueueStatus {
  running: Task | null;
  pending_count: number;
  done_count: number;
  failed_count: number;
  paused: boolean;
}

export async function fetchTasks(status?: string): Promise<Task[]> {
  const params = status ? `?status=${status}` : "";
  return request(`/tasks${params}`);
}

export async function createTask(data: {
  prompt: string;
  duration: number;
  ratio: string;
  model_version: string;
  references: Reference[];
}): Promise<Task> {
  return request("/tasks", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateTask(id: number, data: any): Promise<Task> {
  return request(`/tasks/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteTask(id: number): Promise<void> {
  return request(`/tasks/${id}`, { method: "DELETE" });
}

export async function reorderTask(id: number, position: number): Promise<void> {
  return request(`/tasks/${id}/reorder`, {
    method: "PATCH",
    body: JSON.stringify({ position }),
  });
}

export async function fetchQueueStatus(): Promise<QueueStatus> {
  return request("/queue/status");
}

export async function pauseQueue(): Promise<void> {
  return request("/queue/pause", { method: "POST" });
}

export async function resumeQueue(): Promise<void> {
  return request("/queue/resume", { method: "POST" });
}

export async function fetchCredit(): Promise<{ total_credit: number }> {
  return request("/queue/credit");
}

export interface HealthStatus {
  ok: boolean;
  cli_installed: boolean;
  login_status: string;
}

export async function fetchHealth(): Promise<HealthStatus> {
  return request("/system/health");
}

export async function getPresignedUpload(filename: string): Promise<{
  upload_url: string;
  cos_url: string;
  key: string;
}> {
  return request("/upload/presign", {
    method: "POST",
    body: JSON.stringify({ filename }),
  });
}

export async function proxyUpload(file: File): Promise<{ cos_url: string; key: string }> {
  const formData = new FormData();
  formData.append("file", file);
  const headers: Record<string, string> = {};
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  const res = await fetch(`${BASE}/upload/proxy`, { method: "POST", body: formData, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}
