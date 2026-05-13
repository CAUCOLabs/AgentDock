import { API_BASE_URL } from "./config";
import type {
  TaskApprovalRequest,
  TaskApprovalResponse,
  TaskCreateRequest,
  TaskCreateResponse,
  TaskDiffResponse,
  GitHubAccount,
  GitHubAuthStartResponse,
  GitHubRepositoriesResponse,
  TaskLogsResponse,
  TaskSummary,
  RepositoryFilePreviewResponse,
  RepositoryFilesResponse,
  WorkspaceStatusResponse,
} from "./types";

type AgentDockRequestInit = RequestInit & {
  sessionId?: string | null;
};

type HealthResponse = {
  status?: string;
  message?: string;
  [key: string]: unknown;
};

async function request<T>(path: string, init?: AgentDockRequestInit): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  const { sessionId, ...requestInit } = init ?? {};

  let response: Response;
  try {
    response = await fetch(url, {
      ...requestInit,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...(sessionId ? { "X-AgentDock-Session": sessionId } : {}),
        ...requestInit.headers,
      },
    });
  } catch (error) {
    throw new Error(
      `Unable to reach AgentDock API at ${API_BASE_URL}. For Android emulator use http://10.0.2.2:8000. For a physical Android device, set EXPO_PUBLIC_AGENTDOCK_API_URL to your Mac LAN IP, for example http://192.168.0.185:8000.`
    );
  }

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const detail =
      typeof data?.detail === "string"
        ? data.detail
        : typeof data?.detail?.error === "string"
          ? data.detail.error
          : typeof data?.error === "string"
            ? data.error
            : response.statusText;
    throw new Error(`${response.status} ${detail}`);
  }

  return data as T;
}

export function getHealth() {
  return request<HealthResponse>("/health");
}

export function getTasks() {
  return request<TaskSummary[]>("/tasks");
}

export function createTask(payload: TaskCreateRequest, sessionId?: string | null) {
  return request<TaskCreateResponse>("/tasks", {
    method: "POST",
    sessionId,
    body: JSON.stringify(payload),
  });
}

export function getRepositoryFiles(
  payload: { repo_url: string; base_branch: string },
  sessionId?: string | null
) {
  return request<RepositoryFilesResponse>("/repositories/files", {
    method: "POST",
    sessionId,
    body: JSON.stringify(payload),
  });
}

export function getRepositoryFilePreview(payload: {
  repo_url: string;
  base_branch: string;
  path: string;
}, sessionId?: string | null) {
  return request<RepositoryFilePreviewResponse>("/repositories/file-preview", {
    method: "POST",
    sessionId,
    body: JSON.stringify(payload),
  });
}

export function startGitHubAuth() {
  return request<GitHubAuthStartResponse>("/auth/github/start", {
    method: "POST",
  });
}

export function getGitHubMe(sessionId: string) {
  return request<GitHubAccount>("/auth/github/me", { sessionId });
}

export function getGitHubRepos(sessionId: string) {
  return request<GitHubRepositoriesResponse>("/auth/github/repos", { sessionId });
}

export function logout(sessionId?: string | null) {
  return request<{ status: string; message: string }>("/auth/logout", {
    method: "POST",
    sessionId,
  });
}

export function getTask(taskId: string) {
  return request<TaskSummary>(`/tasks/${taskId}`);
}

export function getTaskDiff(taskId: string) {
  return request<TaskDiffResponse>(`/tasks/${taskId}/diff`);
}

export function getWorkspaceStatus(taskId: string) {
  return request<WorkspaceStatusResponse>(`/tasks/${taskId}/workspace-status`);
}

export function getTaskLogs(taskId: string) {
  return request<TaskLogsResponse>(`/tasks/${taskId}/logs`);
}

export function approveTask(taskId: string, payload: TaskApprovalRequest) {
  return request<TaskApprovalResponse>(`/tasks/${taskId}/approval`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteTask(taskId: string) {
  return request<{ task_id: string; status: string; message: string }>(
    `/tasks/${taskId}`,
    { method: "DELETE" }
  );
}
