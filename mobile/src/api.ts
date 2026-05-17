import { API_BASE_URL } from "./config";
import type {
  AgentProviderStatus,
  AgentProviderAuthFlowResponse,
  AgentProviderMetadata,
  AgentProviderVerificationResponse,
  PromptAssistantRefineRequest,
  PromptAssistantRefineResponse,
  PromptAssistantProvider,
  PromptAssistantProviderStatus,
  TaskApprovalRequest,
  TaskApprovalResponse,
  TaskMergePullRequestResponse,
  TaskPullRequestResponse,
  TaskCreateRequest,
  TaskCreateResponse,
  TaskDiffResponse,
  GitHubAuthStartResponse,
  GitHubInstallation,
  GitHubInstallationsResponse,
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

export type ApiConnectivitySnapshot = {
  status: "online" | "offline";
  error: string | null;
};

let connectivityListener: ((snapshot: ApiConnectivitySnapshot) => void) | null = null;

export function subscribeToApiConnectivity(
  listener: (snapshot: ApiConnectivitySnapshot) => void
) {
  connectivityListener = listener;
  return () => {
    if (connectivityListener === listener) {
      connectivityListener = null;
    }
  };
}

function reportApiConnectivity(snapshot: ApiConnectivitySnapshot) {
  connectivityListener?.(snapshot);
}

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
    const message = `Unable to reach AgentDock API at ${API_BASE_URL}. For Android emulator use http://10.0.2.2:8000. For a physical Android device, set EXPO_PUBLIC_AGENTDOCK_API_URL to your Mac LAN IP, for example http://192.168.0.185:8000.`;
    reportApiConnectivity({ status: "offline", error: message });
    throw new Error(message);
  }

  reportApiConnectivity({ status: "online", error: null });

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

export function getAgentProviderStatuses() {
  return request<AgentProviderStatus[]>("/agent-providers/status");
}

export function getAgentProviders() {
  return request<AgentProviderMetadata[]>("/agent-providers");
}

export function connectAgentProvider(providerId: string, authMode?: string | null) {
  return request<AgentProviderMetadata | AgentProviderAuthFlowResponse>("/agent-providers/connect", {
    method: "POST",
    body: JSON.stringify({ provider_id: providerId, auth_mode: authMode ?? null }),
  });
}

export function verifyAgentProvider(providerId: string, authMode?: string | null) {
  return request<AgentProviderVerificationResponse>("/agent-providers/verify", {
    method: "POST",
    body: JSON.stringify({ provider_id: providerId, auth_mode: authMode ?? null }),
  });
}

export function connectAgentProviderApiKey(providerId: string, apiKey: string) {
  return request<AgentProviderMetadata>("/agent-providers/api-key", {
    method: "POST",
    body: JSON.stringify({ provider_id: providerId, api_key: apiKey }),
  });
}

export function disconnectAgentProvider(providerId: string) {
  return request<AgentProviderMetadata>("/agent-providers/disconnect", {
    method: "POST",
    body: JSON.stringify({ provider_id: providerId }),
  });
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

export function refinePromptAssistant(payload: PromptAssistantRefineRequest) {
  return request<PromptAssistantRefineResponse>("/prompt-assistant/refine", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getPromptAssistantProviders() {
  return request<PromptAssistantProviderStatus[]>("/prompt-assistant/providers");
}

export function connectPromptAssistantProvider(provider: PromptAssistantProvider, apiKey: string) {
  return request<PromptAssistantProviderStatus>("/prompt-assistant/providers/connect", {
    method: "POST",
    body: JSON.stringify({ provider, api_key: apiKey }),
  });
}

export function testPromptAssistantProvider(provider: PromptAssistantProvider) {
  return request<PromptAssistantProviderStatus>("/prompt-assistant/providers/test", {
    method: "POST",
    body: JSON.stringify({ provider }),
  });
}

export function disconnectPromptAssistantProvider(provider: PromptAssistantProvider) {
  return request<PromptAssistantProviderStatus>("/prompt-assistant/providers/disconnect", {
    method: "POST",
    body: JSON.stringify({ provider }),
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
  return request<GitHubAuthStartResponse>("/auth/github-app/start");
}

export function getGitHubMe(sessionId: string) {
  return request<GitHubInstallation>("/auth/github-app/me", { sessionId });
}

export function getGitHubRepos(sessionId: string) {
  return request<GitHubRepositoriesResponse>("/auth/github-app/repos", { sessionId });
}

export function getGitHubInstallations(sessionId: string) {
  return request<GitHubInstallationsResponse>("/auth/github-app/installations", {
    sessionId,
  });
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

export function pushTask(taskId: string) {
  return request<TaskApprovalResponse>(`/tasks/${taskId}/push`, {
    method: "POST",
  });
}

export function createPullRequest(taskId: string) {
  return request<TaskPullRequestResponse>(`/tasks/${taskId}/pull-request`, {
    method: "POST",
  });
}

export function mergePullRequest(taskId: string) {
  return request<TaskMergePullRequestResponse>(`/tasks/${taskId}/merge-pull-request`, {
    method: "POST",
  });
}

export function deleteTask(taskId: string) {
  return request<{ task_id: string; status: string; message: string }>(
    `/tasks/${taskId}`,
    { method: "DELETE" }
  );
}
