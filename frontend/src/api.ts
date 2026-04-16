import type { Task, TaskEvent, User } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    let detail = `请求失败，状态码：${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) {
        detail = body.detail;
      }
    } catch {
      // ignore JSON parse errors on failed responses
    }
    throw new Error(detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export const api = {
  getCurrentUser: () => request<User>("/auth/me"),
  login: (payload: { username: string; password: string }) =>
    request<User>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  signup: (payload: { username: string; password: string }) =>
    request<User>("/auth/signup", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  logout: () =>
    request<{ message: string }>("/auth/logout", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  createReport: (payload: {
    company_name: string;
    focus: string;
    target_role: string;
    max_analysts: number;
  }) =>
    request<{ task: Task }>("/reports", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listTasks: () => request<{ tasks: Task[] }>("/tasks"),
  getTask: (taskId: string) => request<Task>(`/tasks/${taskId}`),
  getTaskEvents: (taskId: string) =>
    request<{ task_id: string; events: TaskEvent[] }>(`/tasks/${taskId}/events`),
  submitFeedback: (taskId: string, payload: { feedback: string }) =>
    request<{ task: Task }>(`/tasks/${taskId}/feedback`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  retryTask: (taskId: string) =>
    request<{ message: string; task_id: string }>(`/tasks/${taskId}/retry`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  buildDownloadUrl: (taskId: string, fileName: string) =>
    `${API_BASE}/tasks/${taskId}/files/${encodeURIComponent(fileName)}`,
};
