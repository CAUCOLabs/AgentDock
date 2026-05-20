import { StatusBar } from "expo-status-bar";
import * as Clipboard from "expo-clipboard";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Linking,
  Pressable,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import {
  CheckCircle2,
  CircleAlert,
  ExternalLink,
  GitBranch,
  GitPullRequest,
  RefreshCw,
  Send,
  Trash2,
  UploadCloud,
  XCircle,
} from "lucide-react-native";

import {
  approveTask,
  connectAgentProvider,
  connectPromptAssistantProvider as connectPromptAssistantProviderApi,
  createPullRequest,
  createTask,
  deleteTask,
  disconnectAgentProvider,
  disconnectPromptAssistantProvider as disconnectPromptAssistantProviderApi,
  getAgentProviders,
  getHealth,
  getGitHubInstallations,
  getGitHubRepos,
  getPromptAssistantProviders,
  getRepositoryFilePreview,
  getRepositoryFiles,
  getTask,
  getTaskDiff,
  getTaskLogs,
  getTasks,
  getWorkspaceStatus,
  logout,
  mergePullRequest,
  pushTask,
  refinePromptAssistant,
  startGitHubAuth,
  subscribeToApiConnectivity,
  testPromptAssistantProvider as testPromptAssistantProviderApi,
  verifyAgentProvider,
} from "./src/api";
import { API_BASE_URL } from "./src/config";
import type {
  AgentProvider,
  AgentProviderAuthFlowResponse,
  AgentProviderMetadata,
  ProviderConnectionStatus,
  ProviderPrimaryStatus,
  ProviderReadiness,
  ChangedFile,
  TaskDiffResponse,
  ChangeScope,
  GitHubAccount,
  GitHubInstallation,
  GitHubRepository,
  TaskLogEvent,
  TaskLogsResponse,
  PromptAssistantProvider,
  PromptAssistantProviderStatus,
  PromptAssistantRefineResponse,
  PromptAssistantScope,
  RepositoryFileEntry,
  RepositoryFilePreviewResponse,
  TaskStatus,
  TaskSummary,
  WorkspaceStatusResponse,
} from "./src/types";

const templates = [
  { label: "Fix bug", value: "Fix the bug described below. Keep the change small and explain the root cause." },
  { label: "Refactor", value: "Refactor this area for clarity while preserving existing behavior." },
  { label: "Add feature", value: "Add the requested feature with focused tests and minimal scope." },
  { label: "Review code", value: "Review the code and make the safest high-impact improvements." },
  { label: "Write tests", value: "Add tests for the behavior described below without changing production logic." },
];

const promptPlaceholder =
  "Describe the task. Examples:\n- Create project scaffold\n- Build desktop app structure\n- Implement authentication flow\n- Refactor backend architecture\n- Create REST API and models";

const privacyPolicyUrl = "https://aisosu.fi/privacy-policy.html";

const scopeOptions: Array<{
  value: ChangeScope;
  label: string;
  description: string;
  broad?: boolean;
}> = [
  {
    value: "only_selected_file",
    label: "Single file",
    description: "Modify only the selected file.",
  },
  {
    value: "allow_related_files",
    label: "Related files",
    description: "Allow nearby modules, tests, configs, and related files to change.",
  },
  {
    value: "whole_project",
    label: "Whole project",
    description:
      "Codex can create, modify, and organize files across the repository to complete larger development tasks.",
    broad: true,
  },
];

const providerOptions: Array<{ label: string; value: AgentProvider }> = [
  { label: "OpenAI Codex", value: "codex" },
];

const emptyDiff: TaskDiffResponse = {
  task_id: "",
  status: "queued",
  diff: "",
  changed_files: [],
};

const emptyLogs: TaskLogsResponse = {
  task_id: "",
  events: [],
};

type ExecutionTimelineStepState = "pending" | "active" | "completed" | "failed";

type ExecutionTimelineStepId =
  | "preparing_workspace"
  | "repository_ready"
  | "running_codex"
  | "changes_generated"
  | "generating_diff"
  | "review_ready"
  | "waiting_approval"
  | "committed"
  | "pull_request_opened"
  | "merged"
  | "failed";

type ExecutionTimelineStep = {
  id: ExecutionTimelineStepId;
  label: string;
  state: ExecutionTimelineStepState;
  detail?: string | null;
  timestamp?: string | null;
};

function isolateFileDiff(diff: string, filePath: string) {
  if (!diff || !filePath) return null;

  const lines = diff.split("\n");
  let currentBlock: string[] = [];
  let currentPaths: string[] = [];

  const finishBlock = () => {
    if (currentBlock.length === 0) return null;
    return currentPaths.includes(filePath) ? currentBlock.join("\n") : null;
  };

  for (const line of lines) {
    if (line.startsWith("diff --git ")) {
      const matchedBlock = finishBlock();
      if (matchedBlock) return matchedBlock;
      currentBlock = [line];
      const match = /^diff --git a\/(.+) b\/(.+)$/.exec(line);
      currentPaths = match ? [match[1], match[2]] : [];
      continue;
    }

    if (currentBlock.length > 0) {
      currentBlock.push(line);
    }
  }

  return finishBlock();
}

type DiffLineTone = "muted" | "hunk" | "added" | "removed" | "context";

function diffLineTone(line: string): DiffLineTone {
  if (
    line.startsWith("diff --git ") ||
    line.startsWith("--- ") ||
    line.startsWith("+++ ")
  ) {
    return "muted";
  }
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "added";
  if (line.startsWith("-")) return "removed";
  return "context";
}

type ApiConnectionState = {
  status: "checking" | "online" | "offline";
  error: string | null;
};

type PromptAssistantMode = "manual" | "openai" | "anthropic";

type MergeAttemptState = {
  taskId: string;
  status: "loading" | "succeeded" | "failed";
  message: string;
};

type TimelineLogEvent = TaskLogEvent & {
  label: string;
};

const promptAssistantModes: Array<{
  value: PromptAssistantMode;
  label: string;
}> = [
  { value: "manual", label: "Manual mode" },
  { value: "openai", label: "ChatGPT / OpenAI" },
  // Claude provider is implemented backend-side but hidden in UI until enabled later.
];

const executionRuleHeadings = ["Constraints:", "Expected output:", "Safety rules:"];

function splitPromptForPreview(prompt: string) {
  const lines = prompt.split("\n");
  const firstRulesIndex = lines.findIndex((line) =>
    executionRuleHeadings.some(
      (heading) => line.trim().toLowerCase() === heading.toLowerCase()
    )
  );

  if (firstRulesIndex === -1) {
    return {
      preview: prompt.trim(),
      executionRules: "",
    };
  }

  return {
    preview: lines.slice(0, firstRulesIndex).join("\n").trim(),
    executionRules: lines.slice(firstRulesIndex).join("\n").trim(),
  };
}

function shortRepoName(repoUrl: string) {
  const trimmed = repoUrl.replace(/\/$/, "");
  const name = trimmed.split("/").slice(-2).join("/");
  return name.replace(/\.git$/, "") || repoUrl;
}

function buildGitHubBranchUrl(repoUrl: string, branch: string) {
  try {
    const url = new URL(repoUrl);
    if (url.hostname !== "github.com") return null;

    const [owner, repo] = url.pathname.replace(/^\/|\.git$/g, "").split("/");
    if (!owner || !repo) return null;

    const encodedBranch = encodeURIComponent(branch).replaceAll("%2F", "/");
    return `https://github.com/${owner}/${repo}/tree/${encodedBranch}`;
  } catch {
    return null;
  }
}

function shortSha(sha?: string | null) {
  return sha ? sha.slice(0, 8) : "None";
}

function stripHttpStatus(message: string) {
  return message.replace(/^\d{3}\s+/, "");
}

function formatDate(value: string) {
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatBytes(value: number) {
  if (value < 1024) {
    return `${value} bytes`;
  }

  const units = ["KB", "MB", "GB"];
  let size = value / 1024;
  let unitIndex = 0;

  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }

  return `${size >= 10 ? size.toFixed(0) : size.toFixed(1)} ${units[unitIndex]}`;
}

function isLiveActivityStatus(status: TaskStatus) {
  return [
    "running",
    "waiting_for_approval",
    "committed",
    "pushed",
    "pull_request_opened",
  ].includes(status);
}

function redactLogMessage(message: string) {
  return message
    .replace(/sk-[A-Za-z0-9_-]{8,}/g, "[REDACTED]")
    .replace(/gh[opsu]_[A-Za-z0-9_]{8,}/g, "[REDACTED]")
    .replace(/(authorization:\s*bearer\s+)[^\s]+/gi, "$1[REDACTED]")
    .replace(/((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s,;]+/gi, "$1[REDACTED]");
}

function isInternalRuntimeLog(message: string) {
  return [
    /codex_core/i,
    /rollout warning/i,
    /\brollout\b.*\b(warn|warning|metadata|id)\b/i,
    /\b(thread|session|conversation|trace|span)[_-]?id\b/i,
    /\b(thread|session|conversation|trace|span)\s*[:#]\s*[a-z0-9-]{6,}/i,
    /\bsandbox\b/i,
    /\bworkdir\b/i,
    /\bworkspace root\b/i,
    /\bworkspace snapshot failed\b/i,
    /\/(?:private\/)?tmp\//i,
    /\/var\/folders\//i,
    /\btemporary director/i,
    /\bruntime metadata\b/i,
    /\binternal metadata\b/i,
  ].some((pattern) => pattern.test(message));
}

function isRawExecutionLog(message: string) {
  return [
    /^\s*(stdout|stderr)\b/i,
    /\b(stdout|stderr)\s*[:=]/i,
    /^\s*(\$|>\s|[+])\s+\S+/,
    /\b(shell|terminal|command|exec)\b.*[:=]/i,
    /\bdiff --git\b/i,
    /^@@\s/,
    /^\s*(\+\+\+|---)\s+[ab]\//,
    /\bCodex execution\b/i,
    /\bfull terminal output\b/i,
  ].some((pattern) => pattern.test(message));
}

function timelineLabelForLog(message: string) {
  const normalized = message.toLowerCase();

  if (/(workspace|repository|repo).*(prepared|cloned|ready|created)/.test(normalized)) {
    return "Workspace prepared";
  }
  if (/(codex|agent).*(started|running|launched)/.test(normalized)) {
    return "Codex started";
  }
  if (/diff.*(generated|ready|loaded)|changes.*ready/.test(normalized)) {
    return "Diff generated";
  }
  if (/(commit|committed).*(completed|created|ready|from agentdock)|committed locally/.test(normalized)) {
    return "Commit completed";
  }
  if (/(push|pushed).*(completed|ready|to branch)|pushed /.test(normalized)) {
    return "Push completed";
  }
  if (/(pull request|pr).*(created|opened)/.test(normalized)) {
    return "PR created";
  }
  if (/(merge|merged).*(completed|pull request|at)/.test(normalized)) {
    return "Merge completed";
  }
  if (/failed|error/.test(normalized)) {
    return "Action failed";
  }
  if (/waiting.*approval|approval/.test(normalized)) {
    return "Ready for review";
  }

  return null;
}

function visibleTimelineLogs(events: TaskLogEvent[]) {
  const seen = new Set<string>();
  const timeline: TimelineLogEvent[] = [];

  for (const event of events) {
    const message = redactLogMessage(event.message);
    if (isInternalRuntimeLog(message) || isRawExecutionLog(message)) continue;

    const label = timelineLabelForLog(message);
    if (!label) continue;

    const key = `${label}:${event.level}`;
    if (seen.has(key)) continue;
    seen.add(key);
    timeline.push({ ...event, message, label });
  }

  return timeline;
}

function rawExecutionLogs(events: TaskLogEvent[]) {
  return events
    .map((event) => ({ ...event, message: redactLogMessage(event.message) }))
    .filter((event) => !isInternalRuntimeLog(event.message))
    .filter((event) => isRawExecutionLog(event.message) || !timelineLabelForLog(event.message));
}

const executionTimelineDefinitions: Array<{
  id: ExecutionTimelineStepId;
  label: string;
}> = [
  { id: "preparing_workspace", label: "Preparing workspace" },
  { id: "repository_ready", label: "Repository ready" },
  { id: "running_codex", label: "Running Codex" },
  { id: "changes_generated", label: "Changes generated" },
  { id: "generating_diff", label: "Generating diff" },
  { id: "review_ready", label: "Review ready" },
  { id: "waiting_approval", label: "Waiting approval" },
  { id: "committed", label: "Committed" },
  { id: "pull_request_opened", label: "Pull request opened" },
  { id: "merged", label: "Merged" },
  { id: "failed", label: "Failed" },
];

const completedStatuses = new Set<TaskStatus>([
  "committed",
  "pushed",
  "pull_request_opened",
  "merged",
  "completed",
]);

function firstMatchingLog(events: TaskLogEvent[], patterns: RegExp[]) {
  return (
    events
      .map((event) => ({ ...event, message: redactLogMessage(event.message) }))
      .filter((event) => !isInternalRuntimeLog(event.message))
      .find((event) => patterns.some((pattern) => pattern.test(event.message))) ?? null
  );
}

function buildExecutionTimeline(
  task: TaskSummary | null,
  events: TaskLogEvent[],
  diff?: TaskDiffResponse | null,
  workspace?: WorkspaceStatusResponse | null
): ExecutionTimelineStep[] {
  if (!task) return [];

  const logForStep: Partial<Record<ExecutionTimelineStepId, TaskLogEvent>> = {
    preparing_workspace:
      firstMatchingLog(events, [/Workspace preparation started/i, /Task created/i]) ?? undefined,
    repository_ready:
      firstMatchingLog(events, [/Workspace prepared/i, /repository ready/i]) ?? undefined,
    running_codex:
      firstMatchingLog(events, [/(codex|mock|local|openai|claude|ollama|local_model) started/i]) ?? undefined,
    changes_generated:
      firstMatchingLog(events, [/(codex|mock|local|openai|claude|ollama|local_model) completed/i, /Task completed by runner/i]) ?? undefined,
    generating_diff:
      firstMatchingLog(events, [/Diff generation started/i]) ?? undefined,
    review_ready:
      firstMatchingLog(events, [/Diff generated/i, /Diff ready for approval/i]) ?? undefined,
    waiting_approval:
      firstMatchingLog(events, [/Diff ready for approval/i, /waiting.*approval/i]) ?? undefined,
    committed:
      firstMatchingLog(events, [/Task locally committed/i, /Commit completed/i]) ?? undefined,
    pull_request_opened:
      firstMatchingLog(events, [/PR created/i, /PR creation completed/i, /pull request.*opened/i]) ?? undefined,
    merged:
      firstMatchingLog(events, [/Merge completed/i, /PR #.*merged/i]) ?? undefined,
    failed:
      firstMatchingLog(events, [/failed/i, /error/i]) ?? undefined,
  };

  const hasDiff = Boolean(diff?.diff || task.changed_files.length);
  const hasWorkspace = Boolean(workspace?.repo_path || task.working_branch);
  const hasCommit = Boolean(task.commit_sha || workspace?.commit_sha);
  const hasPullRequest = Boolean(task.pr_url || workspace?.pr_url);
  const hasMerged = task.status === "merged" || workspace?.pr_state === "merged";
  const isFailed = task.status === "failed";

  const completed = new Set<ExecutionTimelineStepId>();
  if (logForStep.repository_ready || hasWorkspace || task.status !== "queued") {
    completed.add("preparing_workspace");
  }
  if (logForStep.repository_ready || completedStatuses.has(task.status) || hasDiff) {
    completed.add("repository_ready");
  }
  if (
    logForStep.changes_generated ||
    logForStep.generating_diff ||
    hasDiff ||
    task.status === "waiting_for_approval" ||
    completedStatuses.has(task.status)
  ) {
    completed.add("running_codex");
    completed.add("changes_generated");
  }
  if (logForStep.review_ready || hasDiff || task.status === "waiting_for_approval" || completedStatuses.has(task.status)) {
    completed.add("generating_diff");
    completed.add("review_ready");
  }
  if (completedStatuses.has(task.status) || task.status === "rejected") {
    completed.add("waiting_approval");
  }
  if (hasCommit || ["pushed", "pull_request_opened", "merged", "completed"].includes(task.status)) {
    completed.add("committed");
  }
  if (hasPullRequest || task.status === "pull_request_opened" || task.status === "merged") {
    completed.add("pull_request_opened");
  }
  if (hasMerged) {
    completed.add("merged");
  }

  let activeId: ExecutionTimelineStepId | null = null;
  if (isFailed) {
    activeId = "failed";
  } else if (task.status === "merged") {
    activeId = "merged";
  } else if (task.status === "pull_request_opened") {
    activeId = "pull_request_opened";
  } else if (task.status === "committed" || task.status === "pushed" || task.status === "completed") {
    activeId = "committed";
  } else if (task.status === "waiting_for_approval") {
    activeId = "waiting_approval";
  } else if (task.status === "running") {
    if (logForStep.generating_diff && !logForStep.review_ready && !hasDiff) {
      activeId = "generating_diff";
    } else if (logForStep.changes_generated && !logForStep.generating_diff) {
      activeId = "changes_generated";
    } else if (logForStep.running_codex && !logForStep.changes_generated) {
      activeId = "running_codex";
    } else if (logForStep.repository_ready && !logForStep.running_codex) {
      activeId = "repository_ready";
    } else {
      activeId = "preparing_workspace";
    }
  } else {
    activeId = "preparing_workspace";
  }

  return executionTimelineDefinitions.map((definition) => {
    const log = logForStep[definition.id];
    const state: ExecutionTimelineStepState =
      definition.id === "failed" && isFailed
        ? "failed"
        : definition.id === activeId
          ? "active"
          : completed.has(definition.id)
            ? "completed"
            : "pending";

    return {
      ...definition,
      state,
      timestamp: log?.timestamp ?? null,
      detail:
        definition.id === "failed"
          ? task.error ?? log?.message ?? null
          : definition.id === "committed" && task.status === "pushed"
            ? "Branch pushed; pull request has not been opened yet."
            : log?.message ?? null,
    };
  });
}

function upsertPromptAssistantProvider(
  providers: PromptAssistantProviderStatus[],
  next: PromptAssistantProviderStatus
) {
  const existing = providers.filter((provider) => provider.provider !== next.provider);
  return [...existing, next].sort((left, right) => left.provider.localeCompare(right.provider));
}

function providerPrimaryStatus(
  connection: ProviderConnectionStatus,
  readiness: ProviderReadiness
): ProviderPrimaryStatus {
  if (connection === "connected") return "connected";
  if (connection === "connecting") return "connecting";
  if (readiness === "ready" || readiness === "auth_required") return "disconnected";
  if (readiness === "unavailable") return "unavailable";
  return "not_configured";
}

function isCodexAuthFlowResponse(
  response: AgentProviderMetadata | AgentProviderAuthFlowResponse
): response is AgentProviderAuthFlowResponse {
  return (
    "command" in response || "provider_id" in response
  );
}

function fallbackCodexAuthFlow(metadata: AgentProviderMetadata): AgentProviderAuthFlowResponse {
  return {
    provider_id: "codex",
    status: "auth_required",
    message: "OpenAI account connection is not enabled yet.",
    command: null,
    fallback_command: null,
    auth_url: null,
    verification_url: null,
    device_code: null,
    user_code: null,
    instructions: [
      "AgentDock does not collect your OpenAI password.",
      "Codex auth completes only after the backend accepts a callback or API key.",
    ],
    metadata,
  };
}

function statusTone(status: TaskStatus) {
  switch (status) {
    case "completed":
    case "approved":
    case "committed":
    case "pushed":
    case "pull_request_opened":
    case "merged":
      return styles.statusSuccess;
    case "waiting_for_approval":
      return styles.statusWarning;
    case "failed":
    case "rejected":
    case "cancelled":
      return styles.statusDanger;
    case "running":
      return styles.statusRunning;
    default:
      return styles.statusNeutral;
  }
}

export default function App() {
  const [repoUrl, setRepoUrl] = useState("");
  const [baseBranch, setBaseBranch] = useState("main");
  const [instruction, setInstruction] = useState("");
  const [promptAssistantMode, setPromptAssistantMode] = useState<PromptAssistantMode>("manual");
  const [assistantRoughIdea, setAssistantRoughIdea] = useState("");
  const [assistantRefinedPrompt, setAssistantRefinedPrompt] = useState("");
  const [assistantResult, setAssistantResult] = useState<PromptAssistantRefineResponse | null>(null);
  const [assistantError, setAssistantError] = useState<string | null>(null);
  const [assistantCopyStatus, setAssistantCopyStatus] = useState<"copied" | "failed" | null>(null);
  const [promptAssistantProviders, setPromptAssistantProviders] = useState<PromptAssistantProviderStatus[]>([]);
  const [assistantApiKeyProvider, setAssistantApiKeyProvider] = useState<PromptAssistantProvider | null>(null);
  const [assistantApiKey, setAssistantApiKey] = useState("");
  const [assistantProviderAction, setAssistantProviderAction] = useState<PromptAssistantProvider | null>(null);
  const [assistantProviderError, setAssistantProviderError] = useState<string | null>(null);
  const [assistantProviderNotice, setAssistantProviderNotice] = useState<string | null>(null);
  const [isGeneratingPrompt, setIsGeneratingPrompt] = useState(false);
  const [isAssistantPreviewExpanded, setIsAssistantPreviewExpanded] = useState(false);
  const [isAssistantRulesVisible, setIsAssistantRulesVisible] = useState(false);
  const [isCodexRulesVisible, setIsCodexRulesVisible] = useState(false);
  const [promptSentToCodexTask, setPromptSentToCodexTask] = useState(false);
  const [targetFile, setTargetFile] = useState("");
  const [changeScope, setChangeScope] = useState<ChangeScope>("only_selected_file");
  const [githubSessionId, setGithubSessionId] = useState<string | null>(null);
  const [githubAccount, setGithubAccount] = useState<GitHubAccount | null>(null);
  const [githubInstallation, setGithubInstallation] = useState<GitHubInstallation | null>(null);
  const [githubRepos, setGithubRepos] = useState<GitHubRepository[]>([]);
  const [agentProviders, setAgentProviders] = useState<AgentProviderMetadata[]>([]);
  const [githubRepoFilter, setGithubRepoFilter] = useState("");
  const [fileFilter, setFileFilter] = useState("");
  const [repoFiles, setRepoFiles] = useState<RepositoryFileEntry[]>([]);
  const [filePreview, setFilePreview] = useState<RepositoryFilePreviewResponse | null>(null);
  const filePreviewRequestId = useRef(0);
  const [apiConnection, setApiConnection] = useState<ApiConnectionState>({
    status: "checking",
    error: null,
  });
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selectedTask, setSelectedTask] = useState<TaskSummary | null>(null);
  const [diff, setDiff] = useState<TaskDiffResponse>(emptyDiff);
  const [logs, setLogs] = useState<TaskLogsResponse>(emptyLogs);
  const [isRawDiffExpanded, setIsRawDiffExpanded] = useState(false);
  const [selectedDiffFilePath, setSelectedDiffFilePath] = useState<string | null>(null);
  const [isRawLogsOpen, setIsRawLogsOpen] = useState(false);
  const [isPollingLogs, setIsPollingLogs] = useState(false);
  const [logsRefreshError, setLogsRefreshError] = useState<string | null>(null);
  const [logsCopyStatus, setLogsCopyStatus] = useState<"copied" | "failed" | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isLaunching, setIsLaunching] = useState(false);
  const [isLoadingTask, setIsLoadingTask] = useState(false);
  const [isActing, setIsActing] = useState(false);
  const [isLoadingFiles, setIsLoadingFiles] = useState(false);
  const [isConnectingGithub, setIsConnectingGithub] = useState(false);
  const [isLoadingGithubRepos, setIsLoadingGithubRepos] = useState(false);
  const [isManualRepoModeOpen, setIsManualRepoModeOpen] = useState(false);
  const [mergeAttempt, setMergeAttempt] = useState<MergeAttemptState | null>(null);
  const [codexCloudNotice, setCodexCloudNotice] = useState<string | null>(null);
  const [codexPendingAuth, setCodexPendingAuth] = useState(false);
  const [codexAuthFlow, setCodexAuthFlow] = useState<AgentProviderAuthFlowResponse | null>(null);
  const [codexCopyStatus, setCodexCopyStatus] = useState<"copied" | "failed" | null>(null);
  const [connectingProviderId, setConnectingProviderId] = useState<AgentProvider | null>(null);
  const [isAdvancedOpen, setIsAdvancedOpen] = useState(false);
  const [previewCopyStatus, setPreviewCopyStatus] = useState<"copied" | "failed" | null>(null);
  const [diffCopyStatus, setDiffCopyStatus] = useState<"copied" | "failed" | null>(null);

  const selectedTaskId = selectedTask?.task_id ?? null;
  const canLaunch = repoUrl.trim().length > 0 && instruction.trim().length >= 10 && !isLaunching;
  const hasCodexPrompt = instruction.trim().length > 0;
  const isTaskActive = selectedTask ? isLiveActivityStatus(selectedTask.status) : false;
  const timelineLogs = useMemo(() => visibleTimelineLogs(logs.events), [logs.events]);
  const rawLogs = useMemo(() => rawExecutionLogs(logs.events), [logs.events]);
  const taskTimelineSteps = useMemo(
    () => buildExecutionTimeline(selectedTask, logs.events, diff, workspace),
    [selectedTask, logs.events, diff, workspace]
  );
  const activePromptAssistantProviderId: PromptAssistantProvider | null =
    promptAssistantMode === "manual" ? null : promptAssistantMode;
  const activePromptAssistantProvider = useMemo(
    () =>
      activePromptAssistantProviderId === null
        ? null
        : promptAssistantProviders.find((provider) => provider.provider === activePromptAssistantProviderId) ?? null,
    [activePromptAssistantProviderId, promptAssistantProviders]
  );
  const connectedGithubLogin = githubInstallation?.account_login ?? githubAccount?.login ?? null;
  const isGithubConnected = Boolean(
    connectedGithubLogin || githubSessionId || githubInstallation || githubAccount
  );
  const selectedGithubRepo = useMemo(
    () => githubRepos.find((repo) => repo.clone_url === repoUrl) ?? null,
    [githubRepos, repoUrl]
  );
  const assistantPreview = useMemo(
    () => splitPromptForPreview(assistantRefinedPrompt),
    [assistantRefinedPrompt]
  );
  const agentProviderById = useMemo(
    () => new Map(agentProviders.map((provider) => [provider.id, provider])),
    [agentProviders]
  );
  const selectedAgentMetadata = agentProviderById.get("codex");
  const selectedTaskAgentLabel = selectedTask
    ? agentProviderById.get(selectedTask.agent)?.label ??
      providerOptions.find((provider) => provider.value === selectedTask.agent)?.label ??
      selectedTask.agent
    : null;
  const hasConnectedProvider = selectedAgentMetadata?.status === "connected";
  const codexConnection =
    codexPendingAuth || connectingProviderId === "codex"
      ? "connecting"
      : selectedAgentMetadata?.connection ?? selectedAgentMetadata?.status ?? "disconnected";
  const codexReadiness = selectedAgentMetadata?.readiness ?? "not_configured";
  const codexPrimaryStatus =
    codexConnection === "connecting"
      ? "connecting"
      : selectedAgentMetadata?.primary_status ?? providerPrimaryStatus(codexConnection, codexReadiness);
  const isCodexActive = codexPrimaryStatus === "active" || codexConnection === "connected";
  const canConnectCodex =
    codexConnection === "disconnected" && (codexReadiness === "auth_required" || codexReadiness === "ready");
  const codexConnectionLabel =
    codexConnection === "connecting" ? "Connecting" : isCodexActive ? "Connected" : "Disconnected";
  const diffSummaryFiles = useMemo(
    () =>
      selectedTask?.changed_files?.length
        ? selectedTask.changed_files
        : diff.changed_files,
    [diff.changed_files, selectedTask?.changed_files]
  );
  const diffSummaryTotals = useMemo(
    () =>
      diffSummaryFiles.reduce(
        (totals, file) => ({
          additions: totals.additions + file.additions,
          deletions: totals.deletions + file.deletions,
        }),
        { additions: 0, deletions: 0 }
      ),
    [diffSummaryFiles]
  );
  const outsideTargetFiles = useMemo(
    () =>
      diffSummaryFiles.filter(
        (file) => selectedTask?.target_file && file.path !== selectedTask.target_file
      ),
    [diffSummaryFiles, selectedTask?.target_file]
  );
  const selectedDiffFile = useMemo(
    () =>
      selectedDiffFilePath
        ? diffSummaryFiles.find((file) => file.path === selectedDiffFilePath) ?? null
        : null,
    [diffSummaryFiles, selectedDiffFilePath]
  );
  const selectedFileDiff = useMemo(
    () => (selectedDiffFile ? isolateFileDiff(diff.diff, selectedDiffFile.path) : null),
    [diff.diff, selectedDiffFile]
  );
  const showScopeWarning =
    selectedTask?.change_scope === "only_selected_file" && outsideTargetFiles.length > 0;
  const pushedBranchName =
    selectedTask?.working_branch ?? selectedTask?.remote_branch?.replace(/^origin\//, "");
  const pushedBranchUrl =
    selectedTask?.branch_url ??
    (selectedTask && pushedBranchName
      ? buildGitHubBranchUrl(selectedTask.repo_url, pushedBranchName)
      : null);
  const canCreatePullRequest =
    selectedTask?.status === "pushed" && Boolean(workspace?.pr_creation_enabled);
  const prCreationDisabled =
    selectedTask?.status === "pushed" && workspace?.pr_creation_enabled === false;
  const activeMergeAttempt =
    mergeAttempt && selectedTask?.task_id === mergeAttempt.taskId ? mergeAttempt : null;
  const isMergingPullRequest = activeMergeAttempt?.status === "loading";
  const showPrOpenPanel =
    selectedTask?.status === "pull_request_opened" &&
    activeMergeAttempt?.status !== "loading" &&
    activeMergeAttempt?.status !== "failed";
  const filteredRepoFiles = useMemo(
    () =>
      repoFiles
        .filter((entry) => entry.type === "file")
        .filter((entry) => entry.path.toLowerCase().includes(fileFilter.trim().toLowerCase()))
        .slice(0, 50),
    [fileFilter, repoFiles]
  );
  const filteredGithubRepos = useMemo(
    () =>
      githubRepos.filter((repo) =>
        repo.full_name.toLowerCase().includes(githubRepoFilter.trim().toLowerCase())
      ),
    [githubRepoFilter, githubRepos]
  );
  const showManualRepoMode = isManualRepoModeOpen;

  const latestTasks = useMemo(
    () =>
      [...tasks].sort(
        (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      ),
    [tasks]
  );

  const refreshTasks = useCallback(async () => {
    const nextTasks = await getTasks();
    setTasks(nextTasks);
    return nextTasks;
  }, []);

  const loadTaskDetail = useCallback(async (taskId: string) => {
    setIsLoadingTask(true);
    setError(null);
    try {
      const [task, nextDiff, nextLogs, nextWorkspace] = await Promise.all([
        getTask(taskId),
        getTaskDiff(taskId),
        getTaskLogs(taskId),
        getWorkspaceStatus(taskId),
      ]);
      setSelectedTask(task);
      setDiff(nextDiff);
      setLogs(nextLogs);
      setWorkspace(nextWorkspace);
      return task;
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load task details.");
      setDiff(emptyDiff);
      setLogs(emptyLogs);
      setWorkspace(null);
      return null;
    } finally {
      setIsLoadingTask(false);
    }
  }, []);

  const loadTaskLogs = useCallback(async (taskId: string, quiet = false) => {
    try {
      const nextLogs = await getTaskLogs(taskId);
      setLogs(nextLogs);
      setLogsRefreshError(null);
    } catch {
      setLogsRefreshError("Could not refresh logs.");
      if (!quiet) {
        setError("Could not refresh logs.");
      }
    }
  }, []);

  const loadPromptAssistantProviders = useCallback(async () => {
    try {
      setPromptAssistantProviders(await getPromptAssistantProviders());
    } catch {
      setPromptAssistantProviders([]);
    }
  }, []);

  const loadInitialData = useCallback(async () => {
    setIsRefreshing(true);
    setError(null);
    try {
      await getHealth();
      setApiConnection({ status: "online", error: null });
    } catch (healthError) {
      setApiConnection({
        status: "offline",
        error: healthError instanceof Error ? healthError.message : `Unable to reach ${API_BASE_URL}.`,
      });
    }

    try {
      await loadPromptAssistantProviders();
      const nextTasks = await refreshTasks();
      if (selectedTaskId && nextTasks.some((task) => task.task_id === selectedTaskId)) {
        await loadTaskDetail(selectedTaskId);
      }
    } catch (tasksError) {
      setError(tasksError instanceof Error ? tasksError.message : "Unable to fetch tasks.");
    } finally {
      setIsRefreshing(false);
    }
  }, [loadPromptAssistantProviders, loadTaskDetail, refreshTasks, selectedTaskId]);

  useEffect(() => {
    const unsubscribe = subscribeToApiConnectivity((snapshot) => {
      setApiConnection(snapshot);
    });

    return unsubscribe;
  }, []);

  useEffect(() => {
    void loadInitialData();
  }, []);

  useEffect(() => {
    if (!selectedTask?.task_id || !isLiveActivityStatus(selectedTask.status)) {
      setIsPollingLogs(false);
      return;
    }

    setIsPollingLogs(true);
    void loadTaskLogs(selectedTask.task_id, true);
    const intervalId = setInterval(() => {
      void loadTaskLogs(selectedTask.task_id, true);
    }, 1500);

    return () => {
      clearInterval(intervalId);
      setIsPollingLogs(false);
    };
  }, [loadTaskLogs, selectedTask?.task_id, selectedTask?.status]);

  useEffect(() => {
    void (async () => {
      try {
        setAgentProviders(await getAgentProviders());
      } catch {
        setAgentProviders([]);
      }
    })();
  }, []);

  async function handleConnectAgentProvider(providerId: AgentProvider, authMode?: string | null) {
    setConnectingProviderId(providerId);
    setError(null);

    try {
      const response = await connectAgentProvider(providerId, authMode);

      if (providerId === "codex") {
        const flow = isCodexAuthFlowResponse(response)
          ? response
          : fallbackCodexAuthFlow(response);
        setCodexCloudNotice(flow.message);
        setCodexAuthFlow(flow);
        setCodexPendingAuth(true);
      } else {
        setCodexCloudNotice(null);
        setCodexAuthFlow(null);
        setCodexPendingAuth(false);
      }

      setAgentProviders(await getAgentProviders());
    } catch (connectError) {
      setCodexPendingAuth(false);
      setError(connectError instanceof Error ? connectError.message : "Unable to connect provider.");
    } finally {
      setConnectingProviderId(null);
    }
  }

  async function handleDisconnectAgentProvider(providerId: AgentProvider) {
    try {
      await disconnectAgentProvider(providerId);
      if (providerId === "codex") {
        setCodexCloudNotice(null);
        setCodexAuthFlow(null);
        setCodexPendingAuth(false);
      }
      setAgentProviders(await getAgentProviders());
    } catch (disconnectError) {
      setError(disconnectError instanceof Error ? disconnectError.message : "Unable to disconnect provider.");
    }
  }

  async function handleVerifyCodexConnection(authMode?: string | null) {
    setConnectingProviderId("codex");
    setError(null);

    try {
      const result = await verifyAgentProvider(
        "codex",
        authMode ?? codexAuthFlow?.metadata.auth_mode ?? "chatgpt"
      );
      setCodexPendingAuth(false);
      setCodexCloudNotice(result.message);
      setCodexAuthFlow(null);
      setAgentProviders(await getAgentProviders());
    } catch (verifyError) {
      setCodexCloudNotice("Verification failed.");
      setError(verifyError instanceof Error ? verifyError.message : "Unable to verify Codex connection.");
      setAgentProviders(await getAgentProviders());
    } finally {
      setConnectingProviderId(null);
    }
  }

  async function handleCopyCodexCode(code: string) {
    try {
      await Clipboard.setStringAsync(code);
      setCodexCopyStatus("copied");
    } catch {
      setCodexCopyStatus("failed");
    }

    setTimeout(() => setCodexCopyStatus(null), 1600);
  }

  async function handleSavePromptAssistantKey(provider: PromptAssistantProvider) {
    if (provider === "fallback" || !assistantApiKey.trim()) return;

    setAssistantProviderAction(provider);
    setAssistantProviderError(null);
    setAssistantProviderNotice(null);

    try {
      const status = await connectPromptAssistantProviderApi(provider, assistantApiKey.trim());
      setPromptAssistantProviders((current) => upsertPromptAssistantProvider(current, status));
      setAssistantApiKey("");
      setAssistantApiKeyProvider(null);
      setAssistantProviderNotice(`${status.label} connected.`);
    } catch (connectError) {
      setAssistantProviderError(connectError instanceof Error ? connectError.message : "Unable to connect provider.");
    } finally {
      setAssistantProviderAction(null);
    }
  }

  async function handleTestPromptAssistantProvider(provider: PromptAssistantProvider) {
    if (provider === "fallback") return;

    setAssistantProviderAction(provider);
    setAssistantProviderError(null);
    setAssistantProviderNotice(null);

    try {
      const status = await testPromptAssistantProviderApi(provider);
      setPromptAssistantProviders((current) => upsertPromptAssistantProvider(current, status));
      if (status.error) {
        setAssistantProviderError(status.error);
      } else {
        setAssistantProviderNotice(`${status.label} connection verified.`);
      }
    } catch (testError) {
      setAssistantProviderError(testError instanceof Error ? testError.message : "Unable to test provider.");
    } finally {
      setAssistantProviderAction(null);
    }
  }

  async function handleDisconnectPromptAssistantProvider(provider: PromptAssistantProvider) {
    if (provider === "fallback") return;

    setAssistantProviderAction(provider);
    setAssistantProviderError(null);
    setAssistantProviderNotice(null);

    try {
      const status = await disconnectPromptAssistantProviderApi(provider);
      setPromptAssistantProviders((current) => upsertPromptAssistantProvider(current, status));
      setAssistantProviderNotice(`${status.label} disconnected.`);
    } catch (disconnectError) {
      setAssistantProviderError(disconnectError instanceof Error ? disconnectError.message : "Unable to disconnect provider.");
    } finally {
      setAssistantProviderAction(null);
    }
  }

  function assistantProvider(): PromptAssistantProvider {
    return promptAssistantMode === "manual" ? "fallback" : promptAssistantMode;
  }

  function assistantScope(): PromptAssistantScope {
    switch (changeScope) {
      case "only_selected_file":
        return "single_file";
      case "allow_related_files":
        return "related_files";
      case "whole_project":
        return "whole_project";
    }
  }

  async function handleGenerateAssistantPrompt() {
    if (!assistantRoughIdea.trim()) return;

    setIsGeneratingPrompt(true);
    setAssistantError(null);
    setAssistantResult(null);
    setPromptSentToCodexTask(false);
    setIsAssistantRulesVisible(false);
    try {
      const result = await refinePromptAssistant({
        provider: assistantProvider(),
        rough_idea: assistantRoughIdea.trim(),
        repo_name: selectedGithubRepo?.full_name || (repoUrl ? shortRepoName(repoUrl) : null),
        target_file: targetFile || null,
        change_scope: assistantScope(),
        existing_readme_excerpt: filePreview?.path.toLowerCase().includes("readme")
          ? filePreview.content
          : null,
      });
      setAssistantResult(result);
      setAssistantRefinedPrompt(result.refined_prompt);
      setIsAssistantPreviewExpanded(false);
      setIsAssistantRulesVisible(false);
    } catch (generateError) {
      setAssistantError(generateError instanceof Error ? generateError.message : "Unable to refine prompt.");
    } finally {
      setIsGeneratingPrompt(false);
    }
  }

  async function handleCopyAssistantPrompt() {
    try {
      await Clipboard.setStringAsync(assistantRefinedPrompt);
      setAssistantCopyStatus("copied");
    } catch {
      setAssistantCopyStatus("failed");
    }

    setTimeout(() => setAssistantCopyStatus(null), 1600);
  }

  async function handleLaunch() {
    if (!canLaunch) {
      setError("Enter a repository URL and at least 10 characters of instruction.");
      return;
    }

    setIsLaunching(true);
    setError(null);
    try {
      const created = await createTask({
        repo_url: repoUrl.trim(),
        base_branch: baseBranch.trim() || "main",
        instruction: instruction.trim(),
        agent: "codex",
        target_file: targetFile || null,
        change_scope: changeScope,
      }, githubSessionId);
      await refreshTasks();
      setIsRawDiffExpanded(false);
      setSelectedDiffFilePath(null);
      await loadTaskDetail(created.task_id);
      setInstruction("");
      filePreviewRequestId.current += 1;
      setTargetFile("");
      setFilePreview(null);
    } catch (launchError) {
      setError(launchError instanceof Error ? launchError.message : "Unable to launch task.");
    } finally {
      setIsLaunching(false);
    }
  }

  async function handleLoadFiles() {
    if (!repoUrl.trim()) {
      setError("Enter a repository URL before loading files.");
      return;
    }

    filePreviewRequestId.current += 1;
    setIsLoadingFiles(true);
    setError(null);
    setFilePreview(null);
    try {
      const result = await getRepositoryFiles({
        repo_url: repoUrl.trim(),
        base_branch: baseBranch.trim() || "main",
      }, githubSessionId);
      setRepoFiles(result.files);
    } catch (filesError) {
      setError(filesError instanceof Error ? filesError.message : "Unable to load repository files.");
    } finally {
      setIsLoadingFiles(false);
    }
  }

  async function handleSelectFile(path: string) {
    if (targetFile === path) {
      filePreviewRequestId.current += 1;
      setTargetFile("");
      setFilePreview(null);
      setPreviewCopyStatus(null);
      return;
    }

    const requestId = filePreviewRequestId.current + 1;
    filePreviewRequestId.current = requestId;
    setTargetFile(path);
    setError(null);
    setPreviewCopyStatus(null);
    try {
      const preview = await getRepositoryFilePreview({
        repo_url: repoUrl.trim(),
        base_branch: baseBranch.trim() || "main",
        path,
      }, githubSessionId);
      if (filePreviewRequestId.current === requestId) {
        setFilePreview(preview);
      }
    } catch (previewError) {
      if (filePreviewRequestId.current === requestId) {
        setFilePreview(null);
        setError(previewError instanceof Error ? previewError.message : "Unable to preview file.");
      }
    }
  }

  async function handleCommitOrReject(approve: boolean) {
    if (!selectedTask) {
      return;
    }

    setIsActing(true);
    setError(null);
    try {
      await approveTask(selectedTask.task_id, {
        approve,
        message: approve ? "Committed from AgentDock mobile." : "Rejected from AgentDock mobile.",
      });
      await refreshTasks();
      await loadTaskDetail(selectedTask.task_id);
    } catch (approvalError) {
      setError(approvalError instanceof Error ? approvalError.message : "Unable to update approval.");
    } finally {
      setIsActing(false);
    }
  }

  function confirmCommitOrReject(approve: boolean) {
    if (!selectedTask) {
      return;
    }

    if (approve) {
      Alert.alert(
        "Commit these changes?",
        `${diffSummaryFiles.length} changed files\n+${diffSummaryTotals.additions} additions\n-${diffSummaryTotals.deletions} deletions`,
        [
          { text: "Cancel", style: "cancel" },
          {
            text: "Commit changes",
            onPress: () => void handleCommitOrReject(true),
          },
        ]
      );
      return;
    }

    Alert.alert(
      "Reject this task?",
      "This will discard the generated changes for this task.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Reject task",
          style: "destructive",
          onPress: () => void handleCommitOrReject(false),
        },
      ]
    );
  }

  async function handlePush() {
    if (!selectedTask) {
      return;
    }

    setIsActing(true);
    setError(null);
    try {
      await pushTask(selectedTask.task_id);
      await refreshTasks();
      await loadTaskDetail(selectedTask.task_id);
    } catch (pushError) {
      setError(pushError instanceof Error ? pushError.message : "Unable to push task.");
      await refreshTasks();
      await loadTaskDetail(selectedTask.task_id);
    } finally {
      setIsActing(false);
    }
  }

  async function handleCreatePullRequest() {
    if (!selectedTask) {
      return;
    }

    setIsActing(true);
    setError(null);
    try {
      await createPullRequest(selectedTask.task_id);
      await refreshTasks();
      await loadTaskDetail(selectedTask.task_id);
    } catch (prError) {
      setError(prError instanceof Error ? prError.message : "Unable to create pull request.");
      await refreshTasks();
      await loadTaskDetail(selectedTask.task_id);
    } finally {
      setIsActing(false);
    }
  }

  async function handleMergePullRequest() {
    if (!selectedTask) {
      return;
    }

    Alert.alert(
      "Merge pull request?",
      `Merge this pull request into ${selectedTask.base_branch}?`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Merge",
          onPress: async () => {
            setIsActing(true);
            setError(null);
            setMergeAttempt({
              taskId: selectedTask.task_id,
              status: "loading",
              message: "Merging pull request...",
            });
            try {
              await mergePullRequest(selectedTask.task_id);
              await refreshTasks();
              const refreshedTask = await loadTaskDetail(selectedTask.task_id);

              if (refreshedTask?.status === "merged") {
                setMergeAttempt({
                  taskId: selectedTask.task_id,
                  status: "succeeded",
                  message: "Pull request merged",
                });
                return;
              }

              const message =
                refreshedTask?.pr_state === "open"
                  ? "Pull request is still open. Merge was not completed."
                  : "Merge failed: backend did not report the task as merged.";
              setError(message);
              setMergeAttempt({
                taskId: selectedTask.task_id,
                status: "failed",
                message,
              });
            } catch (mergeError) {
              const reason = stripHttpStatus(
                mergeError instanceof Error ? mergeError.message : "Unable to merge pull request."
              );
              const message = `Merge failed: ${reason}`;
              await refreshTasks();
              await loadTaskDetail(selectedTask.task_id);
              setMergeAttempt({
                taskId: selectedTask.task_id,
                status: "failed",
                message,
              });
              setError(message);
            } finally {
              setIsActing(false);
            }
          },
        },
      ]
    );
  }

  async function handleDelete() {
    if (!selectedTask) {
      return;
    }

    Alert.alert("Delete task?", selectedTask.task_id, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: () => {
          void (async () => {
            setIsActing(true);
            setError(null);
            try {
              await deleteTask(selectedTask.task_id);
              await refreshTasks();
              setSelectedTask(null);
              setIsRawDiffExpanded(false);
              setSelectedDiffFilePath(null);
              setDiff(emptyDiff);
              setLogs(emptyLogs);
              setWorkspace(null);
            } catch (deleteError) {
              setError(deleteError instanceof Error ? deleteError.message : "Unable to delete task.");
            } finally {
              setIsActing(false);
            }
          })();
        },
      },
    ]);
  }

  function applyTemplate(value: string) {
    const contextual = targetFile ? `${value}\n\nFocus on ${targetFile}.` : value;
    setInstruction((current) => (current.trim() ? `${current.trim()}\n\n${contextual}` : contextual));
  }

  async function handleConnectGitHub() {
    setIsConnectingGithub(true);
    setError(null);
    try {
      const result = await startGitHubAuth();
      setGithubSessionId(result.session_id);
      await Linking.openURL(result.auth_url);
    } catch (connectError) {
      setError(connectError instanceof Error ? connectError.message : "Unable to start GitHub login.");
    } finally {
      setIsConnectingGithub(false);
    }
  }

  async function handleRefreshGitHub() {
    if (!githubSessionId) {
      return;
    }

    setIsLoadingGithubRepos(true);
    setError(null);
    try {
      const installations = await getGitHubInstallations(githubSessionId);
      const repos = await getGitHubRepos(githubSessionId);
      setGithubInstallation(installations.installations[0] ?? null);
      setGithubRepos(repos.repositories);
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : "Unable to load GitHub account.");
    } finally {
      setIsLoadingGithubRepos(false);
    }
  }

  async function handleLogoutGitHub() {
    await logout(githubSessionId).catch(() => undefined);
    setGithubSessionId(null);
    setGithubAccount(null);
    setGithubInstallation(null);
    setGithubRepos([]);
  }

  function handleSelectGitHubRepo(repo: GitHubRepository) {
    filePreviewRequestId.current += 1;
    setRepoUrl(repo.clone_url);
    setBaseBranch(repo.default_branch);
    setIsManualRepoModeOpen(false);
    setTargetFile("");
    setRepoFiles([]);
    setFilePreview(null);
    setPreviewCopyStatus(null);
  }

  async function handleCopyPreview() {
    if (!filePreview) {
      return;
    }

    try {
      await Clipboard.setStringAsync(filePreview.content);
      setPreviewCopyStatus("copied");
    } catch {
      setPreviewCopyStatus("failed");
    }

    setTimeout(() => setPreviewCopyStatus(null), 1600);
  }

  async function handleCopyDiff() {
    if (!diff.diff) {
      return;
    }

    try {
      await Clipboard.setStringAsync(diff.diff);
      setDiffCopyStatus("copied");
    } catch {
      setDiffCopyStatus("failed");
    }

    setTimeout(() => setDiffCopyStatus(null), 1600);
  }

  async function handleCopyLogs() {
    const raw = rawExecutionLogs(logs.events);
    const timeline = visibleTimelineLogs(logs.events).map((event) => ({
      ...event,
      message: event.label,
    }));
    const content = (raw.length > 0 ? raw : timeline)
      .map(
        (event) =>
          `[${formatDate(event.timestamp)}] ${event.level.toUpperCase()} ${event.message}`
      )
      .join("\n");

    try {
      await Clipboard.setStringAsync(content);
      setLogsCopyStatus("copied");
    } catch {
      setLogsCopyStatus("failed");
    }

    setTimeout(() => setLogsCopyStatus(null), 1600);
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar style="light" />
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        refreshControl={
          <RefreshControl
            refreshing={isRefreshing}
            onRefresh={loadInitialData}
            tintColor="#38d9ff"
          />
        }
      >
        <View style={styles.header}>
          <View style={styles.headerText}>
            <Text style={styles.title}>AgentDock</Text>
            <Text style={styles.subtitle}>Mobile command center for AI coding agents</Text>
          </View>
          <View style={[styles.healthPill, apiConnection.status === "online" ? styles.healthOnline : styles.healthOffline]}>
            {apiConnection.status === "checking" ? (
              <ActivityIndicator size="small" color="#9eefff" />
            ) : (
              <View style={[styles.healthDot, apiConnection.status === "offline" && styles.healthDotOffline]} />
            )}
            <Text style={styles.healthText}>{apiConnection.status}</Text>
          </View>
        </View>

        {apiConnection.error || error ? (
          <View style={styles.errorCard}>
            <CircleAlert size={18} color="#ff8f9b" />
            <Text style={styles.errorText}>{apiConnection.error ?? error}</Text>
          </View>
        ) : null}

        <Section title="GitHub connection">
          <View style={styles.githubPanel}>
            <View style={styles.githubHeader}>
              <View style={styles.githubTitleRow}>
                <GitBranch size={18} color="#dcecf7" />
                <Text style={styles.githubTitle}>GitHub</Text>
              </View>
              <Text style={styles.githubSubtitle}>
                {isGithubConnected
                  ? connectedGithubLogin
                    ? `Connected as ${connectedGithubLogin}`
                    : "Connected. Load repositories to continue."
                  : "Disconnected. Connect GitHub or use a manual repository URL in Codex Task."}
              </Text>
            </View>
            <View style={styles.githubButtonRow}>
              <Pressable
                onPress={() => isGithubConnected ? void handleRefreshGitHub() : void handleConnectGitHub()}
                disabled={isConnectingGithub || isLoadingGithubRepos}
                style={({ pressed }) => [styles.smallButton, pressed && styles.pressed]}
              >
                <Text style={styles.smallButtonText}>
                  {isGithubConnected
                    ? isLoadingGithubRepos
                      ? "Loading repos"
                      : githubRepos.length > 0
                        ? "Refresh repos"
                        : "Load repositories"
                    : isConnectingGithub
                      ? "Connecting"
                      : "Connect GitHub"}
                </Text>
              </Pressable>
              {isGithubConnected ? (
                <Pressable
                  onPress={() => void handleLogoutGitHub()}
                  style={({ pressed }) => [styles.smallButton, pressed && styles.pressed]}
                >
                  <Text style={styles.smallButtonText}>Logout</Text>
                </Pressable>
              ) : null}
            </View>
            {githubRepos.length > 0 ? (
              <View style={styles.githubRepoPicker}>
                <TextInput
                  value={githubRepoFilter}
                  onChangeText={setGithubRepoFilter}
                  placeholder="Filter GitHub repositories"
                  placeholderTextColor="#6f7f92"
                  autoCapitalize="none"
                  autoCorrect={false}
                  style={styles.input}
                />
                <ScrollView
                  nestedScrollEnabled
                  keyboardShouldPersistTaps="handled"
                  style={styles.githubRepoScroller}
                  contentContainerStyle={styles.githubRepoList}
                >
                  {filteredGithubRepos.map((repo) => (
                    <Pressable
                      key={repo.id}
                      onPress={() => handleSelectGitHubRepo(repo)}
                      style={({ pressed }) => [
                        styles.githubRepoOption,
                        repoUrl === repo.clone_url && styles.fileOptionSelected,
                        pressed && styles.pressed,
                      ]}
                    >
                      <Text style={styles.fileOptionPath}>{repo.full_name}</Text>
                      <Text style={styles.fileOptionMeta}>
                        {repo.private ? "private" : "public"} · {repo.default_branch} · {repo.can_push ? "push" : "read"}
                      </Text>
                    </Pressable>
                  ))}
                  {filteredGithubRepos.length === 0 ? (
                    <Text style={styles.emptyText}>No repositories match.</Text>
                  ) : null}
                </ScrollView>
              </View>
            ) : null}
          </View>
        </Section>

        <Section title="Codex connection">
          <View style={styles.githubPanel}>
            <View style={styles.githubHeader}>
              <View style={styles.githubTitleRow}>
                <Text style={styles.githubTitle}>OpenAI Codex</Text>
              </View>
              <Text style={styles.githubSubtitle}>
                Codex executes repository changes. AgentDock uses OpenAI Codex to prepare code changes.
              </Text>
            </View>
            <View style={styles.githubButtonRow}>
              <Text
                style={[
                  styles.providerStatusBadge,
                  codexPrimaryStatus === "connecting" && styles.providerStatusConnecting,
                  codexPrimaryStatus === "active" && styles.providerStatusAvailable,
                  codexPrimaryStatus === "connected" && styles.providerStatusAvailable,
                  codexPrimaryStatus === "ready" && styles.providerStatusAvailable,
                  codexPrimaryStatus === "unavailable" && styles.providerStatusUnavailable,
                ]}
              >
                {codexConnectionLabel}
              </Text>
              {isCodexActive ? (
                <>
                  <Pressable
                    disabled={connectingProviderId === "codex"}
                    onPress={() => void handleVerifyCodexConnection(selectedAgentMetadata?.auth_mode)}
                    style={({ pressed }) => [
                      styles.smallButton,
                      connectingProviderId === "codex" && styles.disabledButton,
                      pressed && styles.pressed,
                    ]}
                  >
                    <Text style={styles.smallButtonText}>Check connection</Text>
                  </Pressable>
                  <Pressable
                    onPress={() => void handleDisconnectAgentProvider("codex")}
                    style={({ pressed }) => [styles.smallButton, pressed && styles.pressed]}
                  >
                    <Text style={styles.smallButtonText}>Disconnect</Text>
                  </Pressable>
                </>
              ) : (
                <Pressable
                  disabled={!canConnectCodex || connectingProviderId === "codex"}
                  onPress={() => void handleConnectAgentProvider("codex", "chatgpt")}
                  style={({ pressed }) => [
                    styles.smallButton,
                    (!canConnectCodex || connectingProviderId === "codex") && styles.disabledButton,
                    pressed && styles.pressed,
                  ]}
                >
                  <Text style={styles.smallButtonText}>Sign in with ChatGPT</Text>
                </Pressable>
              )}
            </View>
            {codexCloudNotice && !isCodexActive ? (
              <View style={styles.codexAuthPanel}>
                <Text style={styles.providerOptionText}>OpenAI account connection</Text>
                <Text style={styles.providerDescription}>{codexCloudNotice}</Text>
                {codexAuthFlow?.verification_url ? (
                  <Pressable
                    onPress={() => void Linking.openURL(codexAuthFlow.verification_url ?? "")}
                    style={({ pressed }) => [styles.providerActionButton, pressed && styles.pressed]}
                  >
                    <Text style={styles.providerActionText}>Open verification URL</Text>
                  </Pressable>
                ) : null}
                {codexAuthFlow?.user_code || codexAuthFlow?.device_code ? (
                  <>
                    <Text style={styles.providerDescription}>
                      {codexAuthFlow.user_code ?? codexAuthFlow.device_code}
                    </Text>
                    <Pressable
                      onPress={() =>
                        void handleCopyCodexCode(codexAuthFlow.user_code ?? codexAuthFlow.device_code ?? "")
                      }
                      style={({ pressed }) => [styles.providerActionButton, pressed && styles.pressed]}
                    >
                      <Text style={styles.providerActionText}>
                        {codexCopyStatus === "copied" ? "Copied" : "Copy code"}
                      </Text>
                    </Pressable>
                  </>
                ) : null}
                <Pressable
                  disabled={connectingProviderId === "codex"}
                  onPress={() => void handleVerifyCodexConnection("chatgpt")}
                  style={({ pressed }) => [
                    styles.providerActionButton,
                    connectingProviderId === "codex" && styles.disabledButton,
                    pressed && styles.pressed,
                  ]}
                >
                  <Text style={styles.providerActionText}>Check connection</Text>
                </Pressable>
              </View>
            ) : null}
            {!hasConnectedProvider ? (
              <Text style={styles.providerModeNote}>Connect Codex before launching tasks.</Text>
            ) : null}
          </View>
        </Section>

        <Section title="Repository Explorer">
          <Text style={styles.promptAssistantCopy}>
            Load files, select target-file context, and preview content before writing the prompt.
          </Text>
          {selectedGithubRepo ? (
            <View style={styles.selectedRepoCard}>
              <Text style={styles.label}>Selected repository</Text>
              <Text style={styles.selectedRepoTitle}>{selectedGithubRepo.full_name}</Text>
              <View style={styles.repoBadgeRow}>
                <Text style={styles.repoBadge}>{selectedGithubRepo.private ? "Private" : "Public"}</Text>
                <Text style={styles.repoBadge}>{selectedGithubRepo.default_branch}</Text>
                <Text style={styles.repoBadge}>{selectedGithubRepo.can_push ? "Read/write" : "Read only"}</Text>
              </View>
              <Text style={styles.filePath}>{selectedGithubRepo.clone_url}</Text>
            </View>
          ) : null}

          <View style={styles.manualRepoBox}>
            <Pressable
              onPress={() => setIsManualRepoModeOpen((current) => !current)}
              style={({ pressed }) => [styles.manualRepoSummary, pressed && styles.pressed]}
            >
              <View style={styles.flex}>
                <Text style={styles.label}>Advanced: use manual repository URL</Text>
                <Text style={styles.filePath}>
                  {selectedGithubRepo
                    ? "Selected GitHub repository will be used."
                    : repoUrl
                      ? shortRepoName(repoUrl)
                      : "Fallback for repositories outside the GitHub picker."}
                </Text>
              </View>
              <Text style={styles.manualRepoToggle}>
                {showManualRepoMode ? "Hide" : "Edit"}
              </Text>
            </Pressable>
            {showManualRepoMode ? (
              <>
                <TextInput
                  value={repoUrl}
                  onChangeText={setRepoUrl}
                  placeholder="https://github.com/org/repo"
                  placeholderTextColor="#6f7f92"
                  autoCapitalize="none"
                  autoCorrect={false}
                  style={styles.input}
                />
                <TextInput
                  value={baseBranch}
                  onChangeText={setBaseBranch}
                  placeholder="main"
                  placeholderTextColor="#6f7f92"
                  autoCapitalize="none"
                  autoCorrect={false}
                  style={styles.input}
                />
              </>
            ) : null}
          </View>

          {repoUrl.trim() ? (
            <View style={styles.filePicker}>
              <View style={styles.filePickerHeader}>
                <View style={styles.flex}>
                  <Text style={styles.label}>Target file</Text>
                  <Text style={styles.filePath}>
                    {targetFile ||
                      (changeScope === "whole_project"
                        ? "Optional context for whole-project tasks"
                        : "No file selected")}
                  </Text>
                </View>
                <Pressable
                  onPress={() => void handleLoadFiles()}
                  disabled={isLoadingFiles}
                  style={({ pressed }) => [styles.smallButton, pressed && styles.pressed]}
                >
                  <Text style={styles.smallButtonText}>{isLoadingFiles ? "Loading" : "Load files"}</Text>
                </Pressable>
              </View>
              {repoFiles.length > 0 ? (
                <>
                  <TextInput
                    value={fileFilter}
                    onChangeText={setFileFilter}
                    placeholder="Filter files"
                    placeholderTextColor="#6f7f92"
                    autoCapitalize="none"
                    autoCorrect={false}
                    style={styles.input}
                  />
                  <ScrollView
                    nestedScrollEnabled
                    keyboardShouldPersistTaps="handled"
                    style={styles.fileListScroller}
                    contentContainerStyle={styles.fileList}
                  >
                    {filteredRepoFiles.map((entry) => (
                      <Pressable
                        key={entry.path}
                        onPress={() => void handleSelectFile(entry.path)}
                        style={({ pressed }) => [
                          styles.fileOption,
                          targetFile === entry.path && styles.fileOptionSelected,
                          pressed && styles.pressed,
                        ]}
                      >
                        <Text style={styles.fileOptionPath}>{entry.path}</Text>
                        <Text style={styles.fileOptionMeta}>
                          {entry.extension ?? "file"}
                          {typeof entry.size === "number" ? ` · ${entry.size} bytes` : ""}
                        </Text>
                      </Pressable>
                    ))}
                  </ScrollView>
                </>
              ) : (
                <Text style={styles.emptyText}>Load files to browse and choose target-file context.</Text>
              )}
              {filePreview ? (
                <View style={styles.previewCard}>
                  <View style={styles.previewHeader}>
                    <View style={styles.previewHeaderMain}>
                      <View style={styles.flex}>
                        <Text style={styles.previewPath}>{filePreview.path}</Text>
                        <Text style={styles.previewMeta}>
                          {formatBytes(filePreview.size)}
                          {filePreview.truncated ? " · Preview truncated" : ""}
                        </Text>
                      </View>
                      <Pressable
                        onPress={() => void handleCopyPreview()}
                        style={({ pressed }) => [styles.previewCopyButton, pressed && styles.pressed]}
                      >
                        <Text style={styles.previewCopyText}>Copy</Text>
                      </Pressable>
                    </View>
                    {previewCopyStatus ? (
                      <Text style={styles.previewCopyStatus}>
                        {previewCopyStatus === "copied" ? "Copied" : "Copy failed"}
                      </Text>
                    ) : null}
                  </View>
                  <ScrollView
                    nestedScrollEnabled
                    style={styles.previewScroller}
                    contentContainerStyle={styles.previewContent}
                  >
                    <Text style={styles.previewText}>{filePreview.content}</Text>
                  </ScrollView>
                </View>
              ) : null}
            </View>
          ) : (
            <Text style={styles.emptyText}>Select a GitHub repository or enter a manual repository URL to browse files.</Text>
          )}

          <View style={styles.scopeBox}>
            <Text style={styles.label}>Change scope</Text>
            {scopeOptions.map((option) => (
              <Pressable
                key={option.value}
                onPress={() => setChangeScope(option.value)}
                style={[
                  styles.scopeOption,
                  changeScope === option.value && styles.scopeOptionSelected,
                  option.broad && changeScope === option.value && styles.scopeOptionBroadSelected,
                ]}
              >
                <Text style={styles.scopeTitle}>{option.label}</Text>
                <Text style={styles.fileOptionMeta}>{option.description}</Text>
                {option.broad ? (
                  <Text style={styles.scopeBroadNote}>
                    Broader repository access. More files may appear in diff review.
                  </Text>
                ) : null}
              </Pressable>
            ))}
          </View>
        </Section>

        <Section title="Prompt Assistant">
          <View style={styles.promptAssistantBox}>
            <View style={styles.promptAssistantHeader}>
              <Text style={styles.promptAssistantTitle}>Prompt Assistant</Text>
              <Text style={styles.promptAssistantCopy}>
                The assistant helps plan the task. Codex executes the repository changes.
              </Text>
            </View>
            <View style={styles.assistantModeGrid}>
              {promptAssistantModes.map((mode) => (
                <Pressable
                  key={mode.value}
                  onPress={() => {
                    setPromptAssistantMode(mode.value);
                    setAssistantError(null);
                    setAssistantResult(null);
                    setAssistantProviderError(null);
                    setAssistantProviderNotice(null);
                  }}
                  style={({ pressed }) => [
                    styles.assistantModeOption,
                    promptAssistantMode === mode.value && styles.assistantModeSelected,
                    pressed && styles.pressed,
                  ]}
                >
                  <View
                    style={[
                      styles.radioDot,
                      promptAssistantMode === mode.value && styles.radioDotSelected,
                    ]}
                  />
                  <Text style={styles.assistantModeText}>{mode.label}</Text>
                </Pressable>
              ))}
            </View>
            {promptAssistantMode === "manual" ? (
              <Text style={styles.promptAssistantCopy}>
                Manual mode uses a local prompt template only. Connect OpenAI for AI-assisted prompt generation.
              </Text>
            ) : (
              <View style={styles.assistantProviderCard}>
                <View style={styles.flex}>
                  <Text style={styles.providerOptionText}>
                    {activePromptAssistantProvider?.label ?? "ChatGPT / OpenAI"}
                  </Text>
                  <Text style={styles.fileOptionMeta}>
                    Status: {activePromptAssistantProvider?.connected ? "Connected" : "Not connected"}
                  </Text>
                  {activePromptAssistantProvider?.last_verified_at ? (
                    <Text style={styles.fileOptionMeta}>
                      Last verified {formatDate(activePromptAssistantProvider.last_verified_at)}
                    </Text>
                  ) : null}
                </View>
                <View style={styles.providerCardActions}>
                  <Pressable
                    onPress={() => {
                      setAssistantApiKeyProvider(activePromptAssistantProviderId);
                      setAssistantApiKey("");
                      setAssistantProviderError(null);
                      setAssistantProviderNotice(null);
                    }}
                    style={({ pressed }) => [styles.providerActionButton, pressed && styles.pressed]}
                  >
                    <Text style={styles.providerActionText}>
                      {activePromptAssistantProvider?.connected ? "Replace API key" : "Connect API key"}
                    </Text>
                  </Pressable>
                  <Pressable
                    disabled={!activePromptAssistantProvider?.connected || assistantProviderAction === activePromptAssistantProviderId}
                    onPress={() =>
                      activePromptAssistantProviderId
                        ? void handleTestPromptAssistantProvider(activePromptAssistantProviderId)
                        : undefined
                    }
                    style={({ pressed }) => [
                      styles.providerActionButton,
                      (!activePromptAssistantProvider?.connected || assistantProviderAction === activePromptAssistantProviderId) &&
                        styles.disabledButton,
                      pressed && styles.pressed,
                    ]}
                  >
                    <Text style={styles.providerActionText}>Test connection</Text>
                  </Pressable>
                  {activePromptAssistantProvider?.connected ? (
                    <Pressable
                      disabled={assistantProviderAction === activePromptAssistantProviderId}
                      onPress={() =>
                        activePromptAssistantProviderId
                          ? void handleDisconnectPromptAssistantProvider(activePromptAssistantProviderId)
                          : undefined
                      }
                      style={({ pressed }) => [
                        styles.providerActionButton,
                        assistantProviderAction === activePromptAssistantProviderId && styles.disabledButton,
                        pressed && styles.pressed,
                      ]}
                    >
                      <Text style={styles.providerActionText}>Disconnect</Text>
                    </Pressable>
                  ) : null}
                </View>
                {assistantApiKeyProvider === activePromptAssistantProviderId ? (
                  <View style={styles.assistantKeyPanel}>
                    <Text style={styles.label}>API key</Text>
                    <TextInput
                      value={assistantApiKey}
                      onChangeText={setAssistantApiKey}
                      placeholder="sk-..."
                      placeholderTextColor="#6f7f92"
                      secureTextEntry
                      autoCapitalize="none"
                      autoCorrect={false}
                      style={styles.input}
                    />
                    <View style={styles.providerCardActions}>
                      <Pressable
                        onPress={() => {
                          setAssistantApiKey("");
                          setAssistantApiKeyProvider(null);
                        }}
                        style={({ pressed }) => [styles.providerActionButton, pressed && styles.pressed]}
                      >
                        <Text style={styles.providerActionText}>Cancel</Text>
                      </Pressable>
                      <Pressable
                        disabled={!assistantApiKey.trim() || assistantProviderAction === activePromptAssistantProviderId}
                        onPress={() =>
                          activePromptAssistantProviderId
                            ? void handleSavePromptAssistantKey(activePromptAssistantProviderId)
                            : undefined
                        }
                        style={({ pressed }) => [
                          styles.providerActionButton,
                          (!assistantApiKey.trim() || assistantProviderAction === activePromptAssistantProviderId) &&
                            styles.disabledButton,
                          pressed && styles.pressed,
                        ]}
                      >
                        <Text style={styles.providerActionText}>Save key</Text>
                      </Pressable>
                    </View>
                  </View>
                ) : null}
                {assistantProviderNotice ? (
                  <Text style={styles.fileOptionMeta}>{assistantProviderNotice}</Text>
                ) : null}
                {assistantProviderError || activePromptAssistantProvider?.error ? (
                  <Text style={styles.assistantErrorText}>
                    {assistantProviderError ?? activePromptAssistantProvider?.error}
                  </Text>
                ) : null}
                {assistantResult?.used_fallback ? (
                  <Text style={styles.fileOptionMeta}>
                    Provider is not connected or failed, so AgentDock used the fallback prompt template.
                  </Text>
                ) : null}
                <Text style={styles.fileOptionMeta}>API keys are stored server-side for Prompt Assistant only.</Text>
              </View>
            )}
            <Field label="Rough idea">
              <TextInput
                value={assistantRoughIdea}
                onChangeText={(value) => {
                  setAssistantRoughIdea(value);
                  setPromptSentToCodexTask(false);
                  setIsAssistantRulesVisible(false);
                }}
                placeholder="Describe what you want Codex to do in plain language."
                placeholderTextColor="#6f7f92"
                multiline
                scrollEnabled
                textAlignVertical="top"
                style={[styles.input, styles.assistantDraftInput]}
              />
              <Text style={styles.textareaHelper}>
                Tap to edit. Scroll inside the box for long text.
              </Text>
            </Field>
            {promptSentToCodexTask ? (
              <View style={styles.assistantPreviewCard}>
                <View style={styles.assistantPreviewHeader}>
                  <Text style={styles.providerOptionText}>Prompt sent to Codex Task.</Text>
                </View>
              </View>
            ) : (
              <View style={styles.assistantPreviewCard}>
                <View style={styles.assistantPreviewHeader}>
                  <Text style={styles.providerOptionText}>Generated prompt</Text>
                  <Pressable
                    disabled={!assistantRefinedPrompt.trim()}
                    onPress={() => setIsAssistantPreviewExpanded((current) => !current)}
                    style={({ pressed }) => [
                      styles.providerActionButton,
                      !assistantRefinedPrompt.trim() && styles.disabledButton,
                      pressed && styles.pressed,
                    ]}
                  >
                    <Text style={styles.providerActionText}>
                      {isAssistantPreviewExpanded ? "Collapse" : "Expand"}
                    </Text>
                  </Pressable>
                </View>
                <Text
                  numberOfLines={isAssistantPreviewExpanded ? undefined : 4}
                  style={styles.assistantPreviewText}
                >
                  {assistantPreview.preview || "Generate a prompt to preview it here."}
                </Text>
                {assistantPreview.executionRules ? (
                  <View style={styles.executionRulesBox}>
                    <Pressable
                      onPress={() => setIsAssistantRulesVisible((current) => !current)}
                      style={({ pressed }) => [styles.executionRulesToggle, pressed && styles.pressed]}
                    >
                      <Text style={styles.executionRulesTitle}>
                        {isAssistantRulesVisible ? "Hide execution rules" : "Show execution rules"}
                      </Text>
                    </Pressable>
                    {isAssistantRulesVisible ? (
                      <Text style={styles.executionRulesText}>{assistantPreview.executionRules}</Text>
                    ) : null}
                  </View>
                ) : null}
              </View>
            )}
            <View style={styles.assistantActionRow}>
              <Pressable
                disabled={!assistantRoughIdea.trim() || isGeneratingPrompt}
                onPress={() => void handleGenerateAssistantPrompt()}
                style={({ pressed }) => [
                  styles.providerActionButton,
                  (!assistantRoughIdea.trim() || isGeneratingPrompt) && styles.disabledButton,
                  pressed && styles.pressed,
                ]}
              >
                <Text style={styles.providerActionText}>
                  {isGeneratingPrompt ? "Generating" : "Generate Prompt"}
                </Text>
              </Pressable>
              <Pressable
                disabled={!assistantRefinedPrompt.trim()}
                onPress={() => void handleCopyAssistantPrompt()}
                style={({ pressed }) => [
                  styles.providerActionButton,
                  !assistantRefinedPrompt.trim() && styles.disabledButton,
                  pressed && styles.pressed,
                ]}
              >
                <Text style={styles.providerActionText}>
                  {assistantCopyStatus === "copied" ? "Copied" : "Copy Prompt"}
                </Text>
              </Pressable>
              <Pressable
                disabled={!assistantRefinedPrompt.trim()}
                onPress={() => {
                  setInstruction(assistantRefinedPrompt);
                  setIsAssistantPreviewExpanded(false);
                  setIsAssistantRulesVisible(false);
                  setPromptSentToCodexTask(true);
                }}
                style={({ pressed }) => [
                  styles.providerActionButton,
                  !assistantRefinedPrompt.trim() && styles.disabledButton,
                  pressed && styles.pressed,
                ]}
              >
                <Text style={styles.providerActionText}>Use Prompt</Text>
              </Pressable>
            </View>
            {assistantError ? <Text style={styles.assistantErrorText}>{assistantError}</Text> : null}
            {assistantResult?.used_fallback ? (
              <Text style={styles.promptAssistantCopy}>
                Fallback prompt template used. You can still edit the prompt before launching Codex.
              </Text>
            ) : null}
          </View>
        </Section>

        <Section title="Codex Task">
          <Text style={styles.launcherGroupTitle}>Execution</Text>
          <Text style={styles.promptAssistantCopy}>
            Codex will inspect the repository and execute the requested changes.
          </Text>
          <Text style={styles.promptAssistantCopy}>
            Review or edit the final prompt before launching Codex.
          </Text>
          <Field label="Final editable Codex prompt">
            <TextInput
              value={instruction}
              onChangeText={setInstruction}
              placeholder={promptPlaceholder}
              placeholderTextColor="#6f7f92"
              multiline
              scrollEnabled
              textAlignVertical="top"
              style={[styles.input, hasCodexPrompt ? styles.promptInput : styles.promptInputCompact]}
            />
            <Text style={styles.textareaHelper}>
              Tap to edit. Scroll inside the box for long text.
            </Text>
          </Field>
          {!hasCodexPrompt ? (
            <Text style={styles.promptAssistantCopy}>
              Use a generated prompt or type a prompt above to reveal execution settings.
            </Text>
          ) : (
            <>
          <View style={styles.templateRow}>
            {templates.map((template) => (
              <Pressable
                key={template.label}
                onPress={() => applyTemplate(template.value)}
                style={({ pressed }) => [styles.templateButton, pressed && styles.pressed]}
              >
                <Text style={styles.templateText}>{template.label}</Text>
              </Pressable>
            ))}
          </View>
          <View style={styles.launchRow}>
            <Text style={styles.counter}>{instruction.length} characters</Text>
            <View style={styles.executionRulesBox}>
              <Pressable
                onPress={() => setIsCodexRulesVisible((current) => !current)}
                style={({ pressed }) => [styles.executionRulesToggle, pressed && styles.pressed]}
              >
                <Text style={styles.executionRulesTitle}>
                  {isCodexRulesVisible ? "Hide execution rules" : "Show execution rules"}
                </Text>
              </Pressable>
              {isCodexRulesVisible ? (
                <Text style={styles.executionRulesText}>
                  AgentDock creates an isolated workspace and branch. Nothing is pushed or opened as a PR unless explicitly enabled.
                </Text>
              ) : null}
            </View>
            <Pressable
              disabled={!canLaunch}
              onPress={handleLaunch}
              style={({ pressed }) => [
                styles.primaryButton,
                (!canLaunch || isLaunching) && styles.disabledButton,
                pressed && styles.pressed,
              ]}
            >
              {isLaunching ? <ActivityIndicator size="small" color="#021016" /> : <Send size={18} color="#021016" />}
              <Text style={styles.primaryButtonText}>
                {isLaunching ? "Launching..." : "Launch Codex Task"}
              </Text>
            </Pressable>
          </View>
            </>
          )}
        </Section>

        <Section title="Recent tasks" right={<RefreshCw size={18} color="#8ba9c6" />}>
          {latestTasks.length === 0 ? (
            <Text style={styles.emptyText}>No tasks yet.</Text>
          ) : (
            <View style={styles.taskList}>
              {latestTasks.map((task) => (
                <Pressable
                  key={task.task_id}
                  onPress={() => {
                    if (task.task_id !== selectedTaskId) {
                      setIsRawDiffExpanded(false);
                      setSelectedDiffFilePath(null);
                    }
                    void loadTaskDetail(task.task_id);
                  }}
                  style={({ pressed }) => [
                    styles.taskItem,
                    selectedTask?.task_id === task.task_id && styles.taskItemSelected,
                    pressed && styles.pressed,
                  ]}
                >
                  <View style={styles.taskMeta}>
                    <Text style={styles.taskId}>{task.task_id}</Text>
                    <Text style={styles.taskRepo}>{shortRepoName(task.repo_url)}</Text>
                    <Text style={styles.taskDate}>{formatDate(task.updated_at)}</Text>
                  </View>
                  <StatusBadge status={task.status} />
                </Pressable>
              ))}
            </View>
          )}
        </Section>

        <Section
          title="Review diff"
          right={
            selectedTask ? (
              <View style={styles.diffHeaderActions}>
                {diffCopyStatus ? (
                  <Text style={styles.previewCopyStatus}>
                    {diffCopyStatus === "copied" ? "Copied" : "Copy failed"}
                  </Text>
                ) : null}
                {diff.diff ? (
                  <Pressable
                    onPress={() => void handleCopyDiff()}
                    style={({ pressed }) => [styles.previewCopyButton, pressed && styles.pressed]}
                  >
                    <Text style={styles.previewCopyText}>Copy diff</Text>
                  </Pressable>
                ) : null}
              </View>
            ) : null
          }
        >
          {selectedTask ? (
            <>
              <DiffSummaryCard
                status={selectedTask.status}
                files={diffSummaryFiles}
                targetFile={selectedTask.target_file ?? null}
                selectedFilePath={selectedDiffFilePath}
                onSelectFile={setSelectedDiffFilePath}
              />
              {selectedDiffFile ? (
                <View style={styles.selectedFileDiffCard}>
                  <View style={styles.selectedFileDiffHeader}>
                    <View style={styles.selectedFileTitleBlock}>
                      <Text style={styles.diffSummaryEyebrow}>Selected file diff</Text>
                      <Text style={styles.selectedFilePath} numberOfLines={3}>
                        {selectedDiffFile.path}
                      </Text>
                    </View>
                    <Pressable
                      onPress={() => setSelectedDiffFilePath(null)}
                      style={({ pressed }) => [styles.clearSelectionButton, pressed && styles.pressed]}
                    >
                      <Text style={styles.clearSelectionText}>View all files</Text>
                    </Pressable>
                  </View>
                  <View style={styles.selectedFileMeta}>
                    <Text style={styles.fileStatus}>{selectedDiffFile.status}</Text>
                    <Text style={[styles.fileStats, styles.additionText]}>
                      +{selectedDiffFile.additions}
                    </Text>
                    <Text style={[styles.fileStats, styles.deletionText]}>
                      -{selectedDiffFile.deletions}
                    </Text>
                  </View>
                  {selectedFileDiff ? (
                    <SelectedFileDiffView diff={selectedFileDiff} />
                  ) : (
                    <Text style={styles.emptyText}>
                      Could not isolate this file diff. Open raw diff to review the full patch.
                    </Text>
                  )}
                </View>
              ) : null}
              {showScopeWarning ? (
                <Text style={styles.warningText}>
                  Scope warning: this diff includes {outsideTargetFiles.length} file
                  {outsideTargetFiles.length === 1 ? "" : "s"} outside the selected target.
                </Text>
              ) : null}
              <Pressable
                onPress={() => setIsRawDiffExpanded((expanded) => !expanded)}
                accessibilityRole="button"
                accessibilityState={{ expanded: isRawDiffExpanded }}
                style={({ pressed }) => [styles.rawDiffToggle, pressed && styles.pressed]}
              >
                <Text style={styles.rawDiffToggleText}>
                  {isRawDiffExpanded ? "Hide raw diff" : "Show raw diff"}
                </Text>
              </Pressable>
              {isRawDiffExpanded ? (
                <ScrollView horizontal style={styles.diffScroller}>
                  <Text style={styles.diffText}>{diff.diff || "No diff available."}</Text>
                </ScrollView>
              ) : null}
            </>
          ) : (
            <Text style={styles.emptyText}>Diff appears after selecting a task.</Text>
          )}
        </Section>

        <Section title="Selected task">
          {isLoadingTask ? (
            <Loader label="Loading task..." />
          ) : selectedTask ? (
            <View style={styles.detailStack}>
              <View style={styles.detailHeader}>
                <Text style={styles.taskId}>{selectedTask.task_id}</Text>
                <Text style={styles.taskRepo}>{shortRepoName(selectedTask.repo_url)}</Text>
              </View>
              <InfoRow label="Status" value={selectedTask.status.replaceAll("_", " ")} />
              <InfoRow label="Target file" value={selectedTask.target_file ?? "None"} />
              <InfoRow label="Agent" value={selectedTaskAgentLabel ?? "Unknown"} />
              {selectedTask.error ? <Text style={styles.errorText}>{selectedTask.error}</Text> : null}
              <View style={styles.actionRow}>
                {selectedTask.status === "waiting_for_approval" ? (
                  <>
                    <ActionButton
                      label="Commit"
                      icon={<CheckCircle2 size={18} color="#031014" />}
                      disabled={isActing}
                      onPress={() => confirmCommitOrReject(true)}
                      tone="approve"
                    />
                    <ActionButton
                      label="Reject"
                      icon={<XCircle size={18} color="#ffd4d8" />}
                      disabled={isActing}
                      onPress={() => confirmCommitOrReject(false)}
                      tone="reject"
                    />
                  </>
                ) : null}
                {selectedTask.status === "committed" && workspace?.push_enabled && workspace.has_valid_origin ? (
                  <ActionButton
                    label="Push"
                    icon={<UploadCloud size={18} color="#031014" />}
                    disabled={isActing}
                    onPress={handlePush}
                    tone="approve"
                  />
                ) : null}
                {selectedTask.status === "pushed" && pushedBranchUrl ? (
                  <ActionButton
                    label="Open Branch"
                    icon={<ExternalLink size={18} color="#031014" />}
                    disabled={false}
                    onPress={() => void Linking.openURL(pushedBranchUrl)}
                    tone="approve"
                  />
                ) : null}
                {canCreatePullRequest ? (
                  <ActionButton
                    label="Create Pull Request"
                    icon={<GitPullRequest size={18} color="#031014" />}
                    disabled={isActing}
                    onPress={handleCreatePullRequest}
                    tone="approve"
                  />
                ) : null}
                {selectedTask.status === "pull_request_opened" && selectedTask.pr_url ? (
                  <>
                    <ActionButton
                      label="Open Pull Request"
                      icon={<ExternalLink size={18} color="#031014" />}
                      disabled={false}
                      onPress={() => void Linking.openURL(selectedTask.pr_url ?? "")}
                      tone="approve"
                    />
                    <ActionButton
                      label={isMergingPullRequest ? "Merging pull request..." : "Merge Pull Request"}
                      icon={<GitPullRequest size={18} color="#031014" />}
                      disabled={isActing || isMergingPullRequest}
                      onPress={handleMergePullRequest}
                      tone="approve"
                    />
                  </>
                ) : null}
                {selectedTask.status === "merged" ? (
                  <>
                    {selectedTask.pr_url ? (
                      <ActionButton
                        label="Open Pull Request"
                        icon={<ExternalLink size={18} color="#031014" />}
                        disabled={false}
                        onPress={() => void Linking.openURL(selectedTask.pr_url ?? "")}
                        tone="approve"
                      />
                    ) : null}
                    <ActionButton
                      label="Open Repository"
                      icon={<ExternalLink size={18} color="#031014" />}
                      disabled={false}
                      onPress={() => void Linking.openURL(selectedTask.repo_url)}
                      tone="approve"
                    />
                  </>
                ) : null}
                <ActionButton
                  label="Delete"
                  icon={<Trash2 size={18} color="#ffd4d8" />}
                  disabled={isActing}
                  onPress={handleDelete}
                  tone="delete"
                />
              </View>
              {selectedTask.status === "committed" ? (
                <Text style={styles.emptyText}>
                  {workspace?.push_enabled
                    ? workspace.has_valid_origin
                      ? `Committed ${shortSha(selectedTask.commit_sha)}. Push when ready.`
                      : "Committed locally. Add or restore an origin remote before pushing."
                    : "Committed locally. Push is disabled because ALLOW_PUSH=false."}
                </Text>
              ) : null}
              {selectedTask.status === "pushed" ? (
                <View style={styles.detailStack}>
                  <Text style={styles.successText}>
                    Pushed {pushedBranchName ?? selectedTask.remote_branch ?? "remote branch"}
                    {selectedTask.pushed_at ? ` at ${formatDate(selectedTask.pushed_at)}` : ""}.
                  </Text>
                  {prCreationDisabled ? (
                    <Text style={styles.emptyText}>
                      Pull request creation is disabled because ALLOW_PR_CREATION=false.
                    </Text>
                  ) : null}
                </View>
              ) : null}
              {activeMergeAttempt?.status === "loading" ? (
                <View style={styles.mergeStatePanel}>
                  <Text style={styles.mergeStateTitle}>Merging pull request...</Text>
                </View>
              ) : null}
              {activeMergeAttempt?.status === "failed" ? (
                <View style={[styles.mergeStatePanel, styles.mergeStateErrorPanel]}>
                  <Text style={[styles.mergeStateTitle, styles.mergeStateErrorTitle]}>
                    {activeMergeAttempt.message}
                  </Text>
                  {selectedTask.error && selectedTask.error !== activeMergeAttempt.message ? (
                    <Text style={styles.errorText}>{selectedTask.error}</Text>
                  ) : null}
                </View>
              ) : null}
              {showPrOpenPanel ? (
                <View style={styles.detailStack}>
                  <Text style={styles.emptyText}>
                    Pull request #{selectedTask.pr_number ?? "opened"}
                    {selectedTask.pr_state ? ` is ${selectedTask.pr_state}` : ""}.
                  </Text>
                  {pushedBranchUrl ? (
                    <Pressable onPress={() => void Linking.openURL(pushedBranchUrl)}>
                      <Text style={styles.linkText}>View pushed branch</Text>
                    </Pressable>
                  ) : null}
                </View>
              ) : null}
              {selectedTask.status === "merged" ? (
                <View style={styles.mergeStatePanel}>
                  <Text style={styles.mergeStateTitle}>Pull request merged</Text>
                  {selectedTask.merge_commit_sha ? (
                    <Text style={styles.emptyText}>
                      Merge commit {shortSha(selectedTask.merge_commit_sha)}.
                    </Text>
                  ) : null}
                  {selectedTask.merged_at ? (
                    <Text style={styles.emptyText}>
                      Merged at {formatDate(selectedTask.merged_at)}.
                    </Text>
                  ) : null}
                </View>
              ) : null}
            </View>
          ) : (
            <Text style={styles.emptyText}>Select a task to inspect it.</Text>
          )}
        </Section>

        <Section title="Task timeline">
          <TaskExecutionTimeline task={selectedTask} steps={taskTimelineSteps} />
        </Section>

        <Section title="Advanced details">
          <Pressable
            onPress={() => setIsAdvancedOpen((current) => !current)}
            style={({ pressed }) => [styles.advancedToggle, pressed && styles.pressed]}
          >
            <Text style={styles.advancedToggleText}>
              {isAdvancedOpen ? "Hide technical details" : "Show technical details"}
            </Text>
            <Text style={styles.fileOptionMeta}>Logs, workspace, and git state</Text>
          </Pressable>
          {isAdvancedOpen ? (
            <View style={styles.detailStack}>
              <Text style={styles.launcherGroupTitle}>Task metadata</Text>
              {selectedTask ? (
                <View style={styles.detailStack}>
                  <InfoRow label="Branch" value={selectedTask.working_branch ?? "None"} />
                  <InfoRow label="Change scope" value={(selectedTask.change_scope ?? "only_selected_file").replaceAll("_", " ")} />
                  <InfoRow label="Commit sha" value={shortSha(selectedTask.commit_sha ?? workspace?.commit_sha)} />
                  <InfoRow label="Pushed" value={selectedTask.pushed ? "Yes" : "No"} />
                  <InfoRow label="Branch URL" value={selectedTask.branch_url ?? "None"} link={selectedTask.branch_url} />
                  <InfoRow label="PR URL" value={selectedTask.pr_url ?? "None"} link={selectedTask.pr_url} />
                  <InfoRow label="PR number" value={selectedTask.pr_number ? `#${selectedTask.pr_number}` : "None"} />
                  <InfoRow label="PR state" value={selectedTask.pr_state ?? "None"} />
                </View>
              ) : (
                <Text style={styles.emptyText}>Select a task to inspect metadata.</Text>
              )}

              <View style={styles.advancedSubheader}>
                <View style={styles.flex}>
                  <Text style={styles.launcherGroupTitle}>Activity timeline</Text>
                  {isPollingLogs ? <Text style={styles.liveIndicator}>Live</Text> : null}
                </View>
                <View style={styles.githubButtonRow}>
                  <Pressable
                    disabled={!selectedTask}
                    onPress={() => selectedTask ? void loadTaskLogs(selectedTask.task_id) : undefined}
                    style={({ pressed }) => [
                      styles.providerActionButton,
                      !selectedTask && styles.disabledButton,
                      pressed && styles.pressed,
                    ]}
                  >
                    <Text style={styles.providerActionText}>Refresh logs</Text>
                  </Pressable>
                  <Pressable
                    disabled={rawLogs.length === 0 && timelineLogs.length === 0}
                    onPress={() => void handleCopyLogs()}
                    style={({ pressed }) => [
                      styles.providerActionButton,
                      rawLogs.length === 0 && timelineLogs.length === 0 && styles.disabledButton,
                      pressed && styles.pressed,
                    ]}
                  >
                    <Text style={styles.providerActionText}>
                      {logsCopyStatus === "copied" ? "Copied" : "Copy logs"}
                    </Text>
                  </Pressable>
                </View>
              </View>
              {logsRefreshError ? <Text style={styles.assistantErrorText}>{logsRefreshError}</Text> : null}
              {selectedTask ? <LogTimeline events={timelineLogs} /> : <Text style={styles.emptyText}>Logs appear after selecting a task.</Text>}
              {selectedTask && !isTaskActive && logs.events.length > 0 ? (
                <Text style={styles.promptAssistantCopy}>
                  Polling stopped. Last activity remains visible.
                </Text>
              ) : null}
              <View style={styles.rawLogsBox}>
                <Pressable
                  onPress={() => setIsRawLogsOpen((current) => !current)}
                  style={({ pressed }) => [styles.rawLogsToggle, pressed && styles.pressed]}
                >
                  <Text style={styles.rawLogsTitle}>Raw execution logs</Text>
                  <Text style={styles.fileOptionMeta}>
                    {rawLogs.length} entries · {isRawLogsOpen ? "Hide" : "Show"}
                  </Text>
                </Pressable>
                {isRawLogsOpen ? <RawLogList events={rawLogs} /> : null}
              </View>

              <Text style={styles.launcherGroupTitle}>Workspace status</Text>
              {selectedTask && workspace ? (
                <View style={styles.detailStack}>
                  <InfoRow label="Branch" value={workspace.branch ?? "Detached"} />
                  <InfoRow label="Head" value={shortSha(workspace.head_commit)} />
                  <InfoRow label="Origin" value={workspace.has_valid_origin ? "Valid" : "Missing"} />
                  <InfoRow label="Remote" value={workspace.remote_url ?? "None"} />
                  <InfoRow label="Default branch" value={workspace.default_branch ?? "Unknown"} />
                  <InfoRow label="Push" value={workspace.push_enabled ? "Enabled" : "Disabled"} />
                  <InfoRow label="Pushed" value={workspace.pushed ? "Yes" : "No"} />
                  <InfoRow label="Remote branch" value={workspace.remote_branch ?? "None"} />
                  <InfoRow label="Pushed at" value={workspace.pushed_at ? formatDate(workspace.pushed_at) : "None"} />
                  <InfoRow label="Last push" value={workspace.last_push_result ?? "None"} />
                  <InfoRow label="PRs" value={workspace.pr_creation_enabled ? "Enabled" : "Disabled"} />
                  <InfoRow label="PR number" value={workspace.pr_number ? `#${workspace.pr_number}` : "None"} />
                  <InfoRow label="PR state" value={workspace.pr_state ?? "None"} />
                  <InfoRow label="Dirty" value={workspace.is_dirty ? "Yes" : "No"} />
                  <InfoRow label="Untracked" value={workspace.has_untracked_files ? "Yes" : "No"} />
                  <InfoRow label="Path" value={workspace.repo_path} />
                  <ChangedFiles files={workspace.changed_files} targetFile={selectedTask.target_file ?? null} />
                </View>
              ) : (
                <Text style={styles.emptyText}>Workspace status appears after selecting a task.</Text>
              )}
            </View>
          ) : null}
        </Section>

        <Section title="Legal">
          <Pressable
            onPress={() => void Linking.openURL(privacyPolicyUrl)}
            style={({ pressed }) => [styles.legalLinkCard, pressed && styles.pressed]}
          >
            <Text style={styles.legalLinkTitle}>Privacy Policy</Text>
            <Text style={[styles.legalLinkUrl, styles.linkText]} numberOfLines={2}>
              {privacyPolicyUrl}
            </Text>
          </Pressable>
        </Section>
      </ScrollView>
    </SafeAreaView>
  );
}

function Section({
  title,
  right,
  children,
}: {
  title: string;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.card}>
      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>{title}</Text>
        {right}
      </View>
      {children}
    </View>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      {children}
    </View>
  );
}

function StatusBadge({ status, compact = false }: { status: TaskStatus; compact?: boolean }) {
  return (
    <View style={[styles.statusBadge, compact && styles.statusBadgeCompact, statusTone(status)]}>
      <Text style={[styles.statusText, compact && styles.statusTextCompact]}>
        {status.replaceAll("_", " ")}
      </Text>
    </View>
  );
}

function InfoRow({ label, value, link }: { label: string; value: string; link?: string | null }) {
  return (
    <View style={styles.infoRow}>
      <Text style={styles.infoLabel}>{label}</Text>
      <Pressable disabled={!link} onPress={() => link && Linking.openURL(link)}>
        <Text style={[styles.infoValue, link && styles.linkText]} numberOfLines={2}>
          {value}
        </Text>
      </Pressable>
    </View>
  );
}

function ActionButton({
  label,
  icon,
  disabled,
  onPress,
  tone,
}: {
  label: string;
  icon: React.ReactNode;
  disabled: boolean;
  onPress: () => void;
  tone: "approve" | "reject" | "delete";
}) {
  return (
    <Pressable
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.actionButton,
        tone === "approve" ? styles.approveButton : styles.dangerButton,
        disabled && styles.disabledButton,
        pressed && styles.pressed,
      ]}
    >
      {icon}
      <Text style={[styles.actionButtonText, tone === "approve" && styles.approveButtonText]}>
        {label}
      </Text>
    </Pressable>
  );
}

function Loader({ label }: { label: string }) {
  return (
    <View style={styles.loader}>
      <ActivityIndicator color="#38d9ff" />
      <Text style={styles.emptyText}>{label}</Text>
    </View>
  );
}

function SelectedFileDiffView({ diff }: { diff: string }) {
  return (
    <View style={styles.selectedFileDiffBox}>
      <View style={styles.diffLegend}>
        <Text style={[styles.diffLegendText, styles.additionText]}>+ added</Text>
        <Text style={[styles.diffLegendText, styles.deletionText]}>- removed</Text>
      </View>
      <ScrollView style={styles.selectedFileDiffScroller}>
        <View style={styles.selectedFileDiffLines}>
          {diff.split("\n").map((line, index) => {
            const tone = diffLineTone(line);
            return (
              <Text
                key={`${index}-${line}`}
                style={[
                  styles.selectedDiffLine,
                  tone === "muted" && styles.selectedDiffLineMuted,
                  tone === "hunk" && styles.selectedDiffLineHunk,
                  tone === "added" && styles.selectedDiffLineAdded,
                  tone === "removed" && styles.selectedDiffLineRemoved,
                ]}
              >
                {line || " "}
              </Text>
            );
          })}
        </View>
      </ScrollView>
    </View>
  );
}

function DiffSummaryCard({
  status,
  files,
  targetFile,
  selectedFilePath,
  onSelectFile,
}: {
  status: TaskStatus;
  files: ChangedFile[];
  targetFile?: string | null;
  selectedFilePath?: string | null;
  onSelectFile?: (path: string) => void;
}) {
  const totals = files.reduce(
    (accumulator, file) => ({
      additions: accumulator.additions + file.additions,
      deletions: accumulator.deletions + file.deletions,
    }),
    { additions: 0, deletions: 0 }
  );

  return (
    <View style={styles.diffSummaryCard}>
      <View style={styles.diffSummaryTopline}>
        <View style={styles.diffSummaryTitleBlock}>
          <Text style={styles.diffSummaryEyebrow}>Diff summary</Text>
          <Text style={styles.diffSummaryHelper}>Review the generated changes before committing.</Text>
        </View>
        <StatusBadge status={status} compact />
      </View>

      <View style={styles.diffSummaryMetrics}>
        <View style={styles.diffSummaryMetric}>
          <Text style={styles.diffSummaryMetricLabel}>Files</Text>
          <Text style={styles.diffSummaryMetricValue}>{files.length}</Text>
        </View>
        <View style={styles.diffSummaryMetric}>
          <Text style={styles.diffSummaryMetricLabel}>Additions</Text>
          <Text style={[styles.diffSummaryMetricValue, styles.additionText]}>+{totals.additions}</Text>
        </View>
        <View style={styles.diffSummaryMetric}>
          <Text style={styles.diffSummaryMetricLabel}>Deletions</Text>
          <Text style={[styles.diffSummaryMetricValue, styles.deletionText]}>-{totals.deletions}</Text>
        </View>
      </View>

      <ChangedFiles
        files={files}
        targetFile={targetFile}
        compact
        selectedFilePath={selectedFilePath}
        onSelectFile={onSelectFile}
      />
    </View>
  );
}

function ChangedFiles({
  files,
  targetFile,
  compact = false,
  selectedFilePath,
  onSelectFile,
}: {
  files: ChangedFile[];
  targetFile?: string | null;
  compact?: boolean;
  selectedFilePath?: string | null;
  onSelectFile?: (path: string) => void;
}) {
  if (files.length === 0) {
    return <Text style={styles.emptyText}>No changed files.</Text>;
  }

  return (
    <View style={styles.fileList}>
      {files.slice(0, 8).map((file) => (
        <Pressable
          key={`${file.status}-${file.path}`}
          disabled={!onSelectFile}
          onPress={() => onSelectFile?.(file.path)}
          style={({ pressed }) => [
            styles.filePill,
            compact && styles.filePillCompact,
            selectedFilePath === file.path && styles.filePillSelected,
            pressed && styles.pressed,
          ]}
        >
          <GitBranch size={14} color="#8bdcff" />
          <Text style={styles.fileText} numberOfLines={compact ? 2 : 1}>
            {file.path}
            {targetFile === file.path ? " · target" : targetFile ? " · outside target" : ""}
          </Text>
          <View style={styles.fileStatsGroup}>
            <Text style={styles.fileStatus}>{file.status}</Text>
            <Text style={[styles.fileStats, styles.additionText]}>+{file.additions}</Text>
            <Text style={[styles.fileStats, styles.deletionText]}>-{file.deletions}</Text>
          </View>
        </Pressable>
      ))}
      {files.length > 8 ? <Text style={styles.emptyText}>+{files.length - 8} more files</Text> : null}
    </View>
  );
}

function TaskExecutionTimeline({
  task,
  steps,
}: {
  task: TaskSummary | null;
  steps: ExecutionTimelineStep[];
}) {
  if (!task) {
    return <Text style={styles.emptyText}>Select a task to inspect its execution pipeline.</Text>;
  }

  return (
    <View style={styles.executionTimeline}>
      <View style={styles.executionTimelineHeader}>
        <StatusBadge status={task.status} compact />
      </View>
      {steps.map((step, index) => (
        <View key={step.id} style={styles.executionStep}>
          <View style={styles.executionRail}>
            <View
              style={[
                styles.executionMarker,
                step.state === "completed" && styles.executionMarkerCompleted,
                step.state === "active" && styles.executionMarkerActive,
                step.state === "failed" && styles.executionMarkerFailed,
              ]}
            >
              {step.state === "completed" ? (
                <CheckCircle2 size={13} color="#5eead4" />
              ) : step.state === "failed" ? (
                <XCircle size={13} color="#fca5a5" />
              ) : (
                <View style={styles.executionMarkerDot} />
              )}
            </View>
            {index < steps.length - 1 ? (
              <View
                style={[
                  styles.executionConnector,
                  (step.state === "completed" || step.state === "active") &&
                    styles.executionConnectorCompleted,
                ]}
              />
            ) : null}
          </View>
          <View style={styles.executionStepBody}>
            <View style={styles.executionStepTopline}>
              <Text
                style={[
                  styles.executionStepTitle,
                  step.state === "pending" && styles.executionStepPendingTitle,
                ]}
              >
                {step.label}
              </Text>
              <Text
                style={[
                  styles.executionStepState,
                  step.state === "active" && styles.executionStepStateActive,
                  step.state === "failed" && styles.executionStepStateFailed,
                ]}
              >
                {step.state}
              </Text>
            </View>
            {step.detail || step.timestamp ? (
              <Text style={styles.executionStepDetail}>
                {step.detail}
                {step.timestamp ? `${step.detail ? " · " : ""}${formatDate(step.timestamp)}` : ""}
              </Text>
            ) : null}
          </View>
        </View>
      ))}
    </View>
  );
}

function LogTimeline({ events }: { events: TimelineLogEvent[] }) {
  if (events.length === 0) {
    return <Text style={styles.emptyText}>No log events.</Text>;
  }

  return (
    <View style={styles.logList}>
      {events.map((event, index) => (
        <View key={`${event.timestamp}-${index}`} style={styles.logItem}>
          <View style={styles.logRail}>
            <View style={[styles.logDot, event.level === "error" && styles.logDotError]} />
          </View>
          <View style={styles.logBody}>
            <Text style={styles.logMeta}>
              {event.level.toUpperCase()} · {formatDate(event.timestamp)}
            </Text>
            <Text style={styles.logMessage}>{event.label}</Text>
          </View>
        </View>
      ))}
    </View>
  );
}

function RawLogList({ events }: { events: TaskLogEvent[] }) {
  if (events.length === 0) {
    return <Text style={styles.emptyText}>No raw execution output to show.</Text>;
  }

  return (
    <ScrollView nestedScrollEnabled style={styles.rawLogScroller}>
      <View style={styles.rawLogList}>
        {events.map((event, index) => (
          <View key={`${event.timestamp}-${index}`} style={styles.rawLogItem}>
            <Text style={styles.logMeta}>
              {event.level.toUpperCase()} · {formatDate(event.timestamp)}
            </Text>
            <Text style={styles.rawLogMessage}>{event.message}</Text>
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: "#05080d",
  },
  content: {
    padding: 18,
    paddingBottom: 36,
    gap: 16,
  },
  header: {
    minHeight: 112,
    borderRadius: 8,
    padding: 20,
    backgroundColor: "rgba(9, 18, 30, 0.92)",
    borderWidth: 1,
    borderColor: "rgba(70, 211, 255, 0.18)",
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 14,
  },
  headerText: {
    flex: 1,
    gap: 6,
  },
  title: {
    color: "#f4fbff",
    fontSize: 34,
    fontWeight: "800",
  },
  subtitle: {
    color: "#91a8bd",
    fontSize: 15,
    lineHeight: 21,
  },
  healthPill: {
    minHeight: 34,
    paddingHorizontal: 12,
    borderRadius: 999,
    borderWidth: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 7,
  },
  healthOnline: {
    backgroundColor: "rgba(43, 232, 194, 0.12)",
    borderColor: "rgba(43, 232, 194, 0.42)",
  },
  healthOffline: {
    backgroundColor: "rgba(255, 92, 118, 0.12)",
    borderColor: "rgba(255, 92, 118, 0.42)",
  },
  healthDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: "#45f0c8",
  },
  healthDotOffline: {
    backgroundColor: "#ff5c76",
  },
  healthText: {
    color: "#d8f9ff",
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
  },
  card: {
    borderRadius: 8,
    padding: 16,
    gap: 14,
    backgroundColor: "rgba(12, 19, 31, 0.82)",
    borderWidth: 1,
    borderColor: "rgba(116, 166, 210, 0.18)",
  },
  sectionHeader: {
    minHeight: 30,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  sectionTitle: {
    color: "#edf8ff",
    fontSize: 18,
    fontWeight: "800",
  },
  field: {
    gap: 8,
  },
  label: {
    color: "#8ea7bd",
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
  },
  launcherGroupTitle: {
    color: "#7ee5ff",
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0,
    textTransform: "uppercase",
  },
  input: {
    minHeight: 52,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "rgba(87, 133, 170, 0.36)",
    backgroundColor: "rgba(2, 8, 15, 0.76)",
    color: "#f3fbff",
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 16,
  },
  promptInput: {
    height: 148,
    maxHeight: 148,
    lineHeight: 22,
  },
  promptInputCompact: {
    height: 104,
    maxHeight: 104,
    lineHeight: 22,
  },
  row: {
    flexDirection: "row",
    gap: 12,
    alignItems: "flex-end",
  },
  flex: {
    flex: 1,
  },
  agentBox: {
    minWidth: 112,
    minHeight: 78,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(20, 42, 62, 0.68)",
    borderWidth: 1,
    borderColor: "rgba(69, 210, 255, 0.24)",
    justifyContent: "space-between",
  },
  agentValue: {
    color: "#7ee5ff",
    fontSize: 18,
    fontWeight: "800",
  },
  templateRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  templateButton: {
    minHeight: 40,
    paddingHorizontal: 13,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "rgba(90, 177, 230, 0.3)",
    backgroundColor: "rgba(45, 106, 150, 0.16)",
    justifyContent: "center",
  },
  templateText: {
    color: "#b9ddf8",
    fontSize: 13,
    fontWeight: "700",
  },
  launchRow: {
    gap: 12,
  },
  counter: {
    color: "#71869b",
    fontSize: 12,
  },
  primaryButton: {
    minHeight: 54,
    borderRadius: 8,
    backgroundColor: "#38d9ff",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
  },
  primaryButtonText: {
    color: "#021016",
    fontSize: 16,
    fontWeight: "900",
  },
  disabledButton: {
    opacity: 0.5,
  },
  pressed: {
    opacity: 0.78,
  },
  errorCard: {
    borderRadius: 8,
    padding: 13,
    backgroundColor: "rgba(91, 25, 39, 0.5)",
    borderWidth: 1,
    borderColor: "rgba(255, 92, 118, 0.35)",
    flexDirection: "row",
    gap: 10,
  },
  errorText: {
    flex: 1,
    color: "#ffb8c0",
    fontSize: 13,
    lineHeight: 19,
  },
  emptyText: {
    color: "#8095aa",
    fontSize: 14,
    lineHeight: 20,
  },
  successText: {
    color: "#95e6bd",
    fontSize: 14,
    lineHeight: 20,
  },
  mergeStatePanel: {
    gap: 8,
    padding: 14,
    borderRadius: 8,
    backgroundColor: "rgba(31, 111, 84, 0.18)",
    borderWidth: 1,
    borderColor: "rgba(70, 201, 139, 0.26)",
  },
  mergeStateTitle: {
    color: "#baf7d0",
    fontSize: 18,
    fontWeight: "700",
    lineHeight: 24,
  },
  mergeStateErrorPanel: {
    backgroundColor: "rgba(91, 25, 39, 0.38)",
    borderColor: "rgba(255, 92, 118, 0.35)",
  },
  mergeStateErrorTitle: {
    color: "#ffb8c0",
  },
  taskList: {
    gap: 10,
  },
  taskItem: {
    minHeight: 78,
    borderRadius: 8,
    padding: 13,
    backgroundColor: "rgba(4, 12, 22, 0.62)",
    borderWidth: 1,
    borderColor: "rgba(102, 137, 169, 0.2)",
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
  },
  taskItemSelected: {
    borderColor: "rgba(56, 217, 255, 0.78)",
    backgroundColor: "rgba(18, 54, 77, 0.62)",
  },
  taskMeta: {
    flex: 1,
    minWidth: 0,
    gap: 4,
  },
  taskId: {
    color: "#eef9ff",
    fontSize: 14,
    fontWeight: "800",
    flexShrink: 1,
    flexWrap: "wrap",
  },
  taskRepo: {
    color: "#9db5ca",
    fontSize: 13,
    flexShrink: 1,
    flexWrap: "wrap",
  },
  taskDate: {
    color: "#64788c",
    fontSize: 12,
  },
  statusBadge: {
    alignSelf: "flex-start",
    flexShrink: 1,
    borderRadius: 999,
    paddingHorizontal: 9,
    paddingVertical: 7,
    borderWidth: 1,
    maxWidth: 132,
  },
  statusBadgeCompact: {
    paddingHorizontal: 6,
    paddingVertical: 5,
    maxWidth: 98,
  },
  statusText: {
    color: "#effbff",
    fontSize: 11,
    lineHeight: 15,
    fontWeight: "800",
    textTransform: "uppercase",
    flexShrink: 1,
    flexWrap: "wrap",
    textAlign: "center",
  },
  statusTextCompact: {
    fontSize: 10,
    lineHeight: 13,
  },
  statusSuccess: {
    backgroundColor: "rgba(49, 222, 158, 0.14)",
    borderColor: "rgba(49, 222, 158, 0.42)",
  },
  statusWarning: {
    backgroundColor: "rgba(255, 198, 84, 0.14)",
    borderColor: "rgba(255, 198, 84, 0.44)",
  },
  statusDanger: {
    backgroundColor: "rgba(255, 92, 118, 0.14)",
    borderColor: "rgba(255, 92, 118, 0.45)",
  },
  statusRunning: {
    backgroundColor: "rgba(56, 217, 255, 0.14)",
    borderColor: "rgba(56, 217, 255, 0.45)",
  },
  statusNeutral: {
    backgroundColor: "rgba(145, 168, 189, 0.14)",
    borderColor: "rgba(145, 168, 189, 0.35)",
  },
  detailStack: {
    gap: 12,
  },
  advancedToggle: {
    minHeight: 52,
    gap: 4,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(8, 18, 30, 0.32)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.14)",
  },
  advancedToggleText: {
    color: "#edf8ff",
    fontSize: 14,
    fontWeight: "800",
  },
  advancedSubheader: {
    gap: 8,
  },
  detailHeader: {
    width: "100%",
    flexDirection: "column",
    alignItems: "flex-start",
    gap: 8,
    paddingRight: 12,
  },
  infoRow: {
    minHeight: 36,
    flexDirection: "row",
    gap: 12,
    justifyContent: "space-between",
    alignItems: "center",
    borderBottomWidth: 1,
    borderBottomColor: "rgba(124, 157, 188, 0.12)",
  },
  infoLabel: {
    color: "#8299ae",
    fontSize: 13,
    fontWeight: "700",
  },
  infoValue: {
    maxWidth: 220,
    color: "#dcecf7",
    fontSize: 13,
    textAlign: "right",
  },
  linkText: {
    color: "#58dfff",
  },
  legalLinkCard: {
    gap: 4,
    minHeight: 56,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(8, 18, 30, 0.32)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.14)",
    justifyContent: "center",
  },
  legalLinkTitle: {
    color: "#edf8ff",
    fontSize: 14,
    fontWeight: "800",
  },
  legalLinkUrl: {
    fontSize: 13,
    lineHeight: 18,
  },
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  actionButton: {
    minHeight: 48,
    borderRadius: 8,
    paddingHorizontal: 14,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    flexGrow: 1,
  },
  approveButton: {
    backgroundColor: "#41e6bd",
  },
  dangerButton: {
    backgroundColor: "rgba(255, 92, 118, 0.14)",
    borderWidth: 1,
    borderColor: "rgba(255, 92, 118, 0.4)",
  },
  actionButtonText: {
    color: "#ffd4d8",
    fontSize: 14,
    fontWeight: "900",
  },
  approveButtonText: {
    color: "#031014",
  },
  diffScroller: {
    maxHeight: 360,
    borderRadius: 8,
    backgroundColor: "#02060b",
    borderWidth: 1,
    borderColor: "rgba(91, 129, 161, 0.22)",
  },
  diffHeaderActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  diffText: {
    minWidth: 620,
    color: "#c8e4f4",
    fontFamily: "Courier",
    fontSize: 12,
    lineHeight: 18,
    padding: 12,
  },
  rawDiffToggle: {
    minHeight: 42,
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(65, 230, 189, 0.12)",
    borderWidth: 1,
    borderColor: "rgba(65, 230, 189, 0.28)",
  },
  rawDiffToggleText: {
    color: "#d8f9ff",
    fontSize: 13,
    fontWeight: "900",
  },
  selectedFileDiffCard: {
    gap: 12,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(8, 18, 30, 0.42)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.16)",
  },
  selectedFileDiffHeader: {
    flexDirection: "row",
    gap: 10,
    alignItems: "flex-start",
    justifyContent: "space-between",
  },
  selectedFileTitleBlock: {
    flex: 1,
    gap: 3,
  },
  selectedFilePath: {
    color: "#edf8ff",
    fontSize: 13,
    lineHeight: 18,
    fontWeight: "800",
  },
  selectedFileMeta: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    alignItems: "center",
  },
  selectedFileDiffBox: {
    maxHeight: 320,
    borderRadius: 8,
    overflow: "hidden",
    backgroundColor: "#02060b",
    borderWidth: 1,
    borderColor: "rgba(91, 129, 161, 0.22)",
  },
  diffLegend: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    paddingHorizontal: 12,
    paddingVertical: 9,
    backgroundColor: "rgba(2, 6, 11, 0.96)",
    borderBottomWidth: 1,
    borderBottomColor: "rgba(91, 129, 161, 0.22)",
  },
  diffLegendText: {
    fontFamily: "Courier",
    fontSize: 11,
    fontWeight: "800",
  },
  selectedFileDiffScroller: {
    maxHeight: 278,
  },
  selectedFileDiffLines: {
    paddingVertical: 8,
  },
  selectedDiffLine: {
    paddingHorizontal: 12,
    paddingVertical: 2,
    color: "#c8e4f4",
    fontFamily: "Courier",
    fontSize: 12,
    lineHeight: 18,
    flexWrap: "wrap",
  },
  selectedDiffLineMuted: {
    color: "#6f8298",
  },
  selectedDiffLineHunk: {
    color: "#a7c7ff",
    backgroundColor: "rgba(37, 99, 235, 0.16)",
  },
  selectedDiffLineAdded: {
    color: "#bbf7d0",
    backgroundColor: "rgba(22, 101, 52, 0.24)",
  },
  selectedDiffLineRemoved: {
    color: "#fecaca",
    backgroundColor: "rgba(127, 29, 29, 0.22)",
  },
  clearSelectionButton: {
    minHeight: 34,
    borderRadius: 8,
    paddingHorizontal: 10,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(148, 163, 184, 0.08)",
    borderWidth: 1,
    borderColor: "rgba(148, 163, 184, 0.18)",
  },
  clearSelectionText: {
    color: "#d8f9ff",
    fontSize: 12,
    fontWeight: "900",
  },
  diffSummaryCard: {
    gap: 12,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(3, 12, 23, 0.58)",
    borderWidth: 1,
    borderColor: "rgba(65, 230, 189, 0.2)",
  },
  diffSummaryTopline: {
    flexDirection: "row",
    gap: 10,
    alignItems: "flex-start",
    justifyContent: "space-between",
  },
  diffSummaryTitleBlock: {
    flex: 1,
    gap: 3,
  },
  diffSummaryEyebrow: {
    color: "#8bdcff",
    fontSize: 11,
    fontWeight: "900",
    textTransform: "uppercase",
  },
  diffSummaryHelper: {
    color: "#9db5ca",
    fontSize: 12,
    lineHeight: 17,
  },
  diffSummaryMetrics: {
    flexDirection: "row",
    gap: 8,
  },
  diffSummaryMetric: {
    flex: 1,
    minWidth: 0,
    gap: 3,
    padding: 10,
    borderRadius: 8,
    backgroundColor: "rgba(15, 28, 42, 0.62)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.12)",
  },
  diffSummaryMetricLabel: {
    color: "#8299ae",
    fontSize: 10,
    fontWeight: "900",
    textTransform: "uppercase",
  },
  diffSummaryMetricValue: {
    color: "#edf8ff",
    fontSize: 18,
    fontWeight: "900",
  },
  fileList: {
    gap: 8,
  },
  githubPanel: {
    gap: 10,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(8, 18, 30, 0.42)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.16)",
  },
  githubHeader: {
    gap: 4,
  },
  githubTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  githubTitle: {
    color: "#edf8ff",
    fontSize: 14,
    fontWeight: "800",
  },
  githubSubtitle: {
    color: "#8ea7bd",
    fontSize: 12,
    lineHeight: 18,
  },
  githubButtonRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  githubRepoPicker: {
    gap: 8,
  },
  githubRepoScroller: {
    maxHeight: 260,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "rgba(90, 177, 230, 0.18)",
  },
  githubRepoList: {
    padding: 8,
    gap: 8,
  },
  githubRepoOption: {
    gap: 3,
    minHeight: 44,
    borderRadius: 8,
    padding: 10,
    backgroundColor: "rgba(45, 106, 150, 0.13)",
    borderWidth: 1,
    borderColor: "rgba(90, 177, 230, 0.18)",
  },
  agentProviderBox: {
    gap: 10,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(8, 18, 30, 0.42)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.16)",
  },
  providerGrid: {
    gap: 8,
  },
  providerSelectArea: {
    gap: 8,
  },
  providerOption: {
    minHeight: 48,
    gap: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    backgroundColor: "rgba(45, 106, 150, 0.13)",
    borderWidth: 1,
    borderColor: "rgba(90, 177, 230, 0.18)",
    justifyContent: "center",
  },
  providerOptionSelected: {
    backgroundColor: "rgba(65, 230, 189, 0.18)",
    borderColor: "rgba(65, 230, 189, 0.38)",
  },
  providerOptionText: {
    color: "#edf8ff",
    fontSize: 13,
    fontWeight: "800",
  },
  providerStatusBadge: {
    color: "#fde68a",
    fontSize: 10,
    fontWeight: "900",
    textTransform: "uppercase",
  },
  providerStatusAvailable: {
    color: "#7ee5bf",
  },
  providerStatusConnecting: {
    color: "#58dfff",
  },
  providerStatusUnavailable: {
    color: "#ffb8c0",
  },
  providerDescription: {
    color: "#8ea7bd",
    fontSize: 12,
    lineHeight: 18,
  },
  providerActionButton: {
    alignSelf: "flex-start",
    minHeight: 34,
    paddingHorizontal: 10,
    borderRadius: 8,
    backgroundColor: "rgba(56, 217, 255, 0.12)",
    borderWidth: 1,
    borderColor: "rgba(56, 217, 255, 0.28)",
    justifyContent: "center",
  },
  providerCardActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  providerActionText: {
    color: "#9eefff",
    fontSize: 12,
    fontWeight: "800",
  },
  providerModeNote: {
    color: "#8ea7bd",
    fontSize: 12,
    lineHeight: 18,
  },
  promptAssistantBox: {
    gap: 12,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(8, 18, 30, 0.42)",
    borderWidth: 1,
    borderColor: "rgba(90, 177, 230, 0.2)",
  },
  promptAssistantHeader: {
    gap: 4,
  },
  promptAssistantTitle: {
    color: "#edf8ff",
    fontSize: 15,
    fontWeight: "800",
  },
  promptAssistantCopy: {
    color: "#8ea7bd",
    fontSize: 12,
    lineHeight: 18,
  },
  assistantModeGrid: {
    gap: 8,
  },
  assistantModeOption: {
    minHeight: 42,
    paddingHorizontal: 11,
    paddingVertical: 8,
    borderRadius: 8,
    backgroundColor: "rgba(45, 106, 150, 0.13)",
    borderWidth: 1,
    borderColor: "rgba(90, 177, 230, 0.18)",
    flexDirection: "row",
    alignItems: "center",
    gap: 9,
  },
  assistantModeSelected: {
    backgroundColor: "rgba(65, 230, 189, 0.18)",
    borderColor: "rgba(65, 230, 189, 0.38)",
  },
  assistantModeText: {
    color: "#edf8ff",
    fontSize: 13,
    fontWeight: "800",
  },
  radioDot: {
    width: 13,
    height: 13,
    borderRadius: 7,
    borderWidth: 1,
    borderColor: "rgba(142, 167, 189, 0.8)",
  },
  radioDotSelected: {
    backgroundColor: "#41e6bd",
    borderColor: "#41e6bd",
  },
  assistantProviderCard: {
    gap: 10,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(45, 106, 150, 0.13)",
    borderWidth: 1,
    borderColor: "rgba(90, 177, 230, 0.18)",
  },
  assistantKeyPanel: {
    gap: 8,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(2, 8, 15, 0.52)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.16)",
  },
  assistantDraftInput: {
    height: 112,
    maxHeight: 112,
    lineHeight: 21,
  },
  textareaHelper: {
    color: "#8ea7bd",
    fontSize: 12,
    fontWeight: "600",
    lineHeight: 17,
  },
  assistantPreviewCard: {
    gap: 8,
    padding: 11,
    borderRadius: 8,
    backgroundColor: "rgba(45, 106, 150, 0.13)",
    borderWidth: 1,
    borderColor: "rgba(90, 177, 230, 0.18)",
  },
  assistantPreviewHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
  },
  assistantPreviewText: {
    minHeight: 66,
    color: "#dcecf7",
    fontFamily: "Courier",
    fontSize: 12,
    lineHeight: 18,
  },
  executionRulesBox: {
    gap: 8,
    padding: 10,
    borderRadius: 8,
    backgroundColor: "rgba(2, 8, 15, 0.38)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.16)",
  },
  executionRulesToggle: {
    minHeight: 34,
    justifyContent: "center",
  },
  executionRulesTitle: {
    color: "#edf8ff",
    fontSize: 12,
    fontWeight: "900",
  },
  executionRulesText: {
    color: "#dcecf7",
    fontFamily: "Courier",
    fontSize: 12,
    lineHeight: 18,
  },
  assistantActionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  assistantErrorText: {
    color: "#ffb8c0",
    fontSize: 12,
    lineHeight: 18,
  },
  liveIndicator: {
    color: "#95e6bd",
    fontSize: 12,
    fontWeight: "900",
    textTransform: "uppercase",
  },
  liveActionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  codexAuthPanel: {
    gap: 10,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(8, 18, 30, 0.52)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.2)",
  },
  commandText: {
    color: "#edf8ff",
    fontFamily: "Courier",
    fontSize: 12,
    lineHeight: 18,
    padding: 8,
    borderRadius: 6,
    backgroundColor: "rgba(2, 6, 23, 0.64)",
  },
  commandLabel: {
    color: "#edf8ff",
    fontSize: 11,
    fontWeight: "900",
    textTransform: "uppercase",
  },
  selectedRepoCard: {
    gap: 8,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(22, 70, 70, 0.34)",
    borderWidth: 1,
    borderColor: "rgba(65, 230, 189, 0.24)",
  },
  selectedRepoTitle: {
    color: "#edf8ff",
    fontSize: 15,
    fontWeight: "800",
  },
  repoBadgeRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 7,
  },
  repoBadge: {
    color: "#dcecf7",
    fontSize: 11,
    fontWeight: "800",
    paddingHorizontal: 8,
    paddingVertical: 5,
    borderRadius: 999,
    backgroundColor: "rgba(124, 157, 188, 0.14)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.18)",
  },
  manualRepoBox: {
    gap: 8,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(8, 18, 30, 0.28)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.12)",
  },
  manualRepoSummary: {
    minHeight: 42,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  manualRepoToggle: {
    color: "#7ee5ff",
    fontSize: 12,
    fontWeight: "800",
    textTransform: "uppercase",
  },
  filePicker: {
    gap: 10,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(8, 18, 30, 0.42)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.16)",
  },
  filePickerHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  filePath: {
    color: "#cfe9f9",
    fontSize: 12,
  },
  fileListScroller: {
    maxHeight: 260,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "rgba(90, 177, 230, 0.18)",
  },
  scopeBox: {
    gap: 8,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(8, 18, 30, 0.42)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.16)",
  },
  scopeOption: {
    gap: 4,
    padding: 11,
    borderRadius: 8,
    backgroundColor: "rgba(45, 106, 150, 0.13)",
    borderWidth: 1,
    borderColor: "rgba(90, 177, 230, 0.18)",
  },
  scopeOptionSelected: {
    backgroundColor: "rgba(65, 230, 189, 0.18)",
    borderColor: "rgba(65, 230, 189, 0.38)",
  },
  scopeOptionBroadSelected: {
    backgroundColor: "rgba(245, 158, 11, 0.1)",
    borderColor: "rgba(245, 158, 11, 0.38)",
  },
  scopeTitle: {
    color: "#edf8ff",
    fontSize: 13,
    fontWeight: "800",
  },
  scopeBroadNote: {
    color: "#fde68a",
    fontSize: 12,
    lineHeight: 17,
    padding: 8,
    borderRadius: 8,
    backgroundColor: "rgba(245, 158, 11, 0.1)",
    borderWidth: 1,
    borderColor: "rgba(245, 158, 11, 0.2)",
  },
  warningText: {
    color: "#fde68a",
    fontSize: 13,
    lineHeight: 19,
    padding: 10,
    borderRadius: 8,
    backgroundColor: "rgba(180, 83, 9, 0.16)",
    borderWidth: 1,
    borderColor: "rgba(245, 158, 11, 0.28)",
  },
  smallButton: {
    minHeight: 36,
    borderRadius: 8,
    paddingHorizontal: 10,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(88, 223, 255, 0.14)",
    borderWidth: 1,
    borderColor: "rgba(88, 223, 255, 0.26)",
  },
  smallButtonText: {
    color: "#dcecf7",
    fontSize: 12,
    fontWeight: "800",
  },
  fileOption: {
    gap: 3,
    minHeight: 44,
    borderRadius: 8,
    padding: 10,
    backgroundColor: "rgba(45, 106, 150, 0.13)",
    borderWidth: 1,
    borderColor: "rgba(90, 177, 230, 0.18)",
  },
  fileOptionSelected: {
    backgroundColor: "rgba(65, 230, 189, 0.18)",
    borderColor: "rgba(65, 230, 189, 0.38)",
  },
  fileOptionPath: {
    color: "#cfe9f9",
    fontSize: 12,
    fontWeight: "700",
  },
  fileOptionMeta: {
    color: "#8299ae",
    fontSize: 11,
  },
  previewCard: {
    gap: 8,
    borderRadius: 8,
    backgroundColor: "#02060b",
    borderWidth: 1,
    borderColor: "rgba(91, 129, 161, 0.22)",
    overflow: "hidden",
  },
  previewHeader: {
    gap: 3,
    paddingHorizontal: 12,
    paddingTop: 10,
    paddingBottom: 8,
    borderBottomWidth: 1,
    borderBottomColor: "rgba(91, 129, 161, 0.18)",
  },
  previewHeaderMain: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  previewPath: {
    color: "#dff5ff",
    fontSize: 12,
    fontWeight: "800",
  },
  previewMeta: {
    color: "#8299ae",
    fontSize: 11,
  },
  previewCopyButton: {
    minHeight: 32,
    paddingHorizontal: 10,
    borderRadius: 8,
    backgroundColor: "rgba(56, 217, 255, 0.12)",
    borderWidth: 1,
    borderColor: "rgba(56, 217, 255, 0.28)",
    justifyContent: "center",
  },
  previewCopyText: {
    color: "#9eefff",
    fontSize: 12,
    fontWeight: "800",
  },
  previewCopyStatus: {
    color: "#9eefff",
    fontSize: 11,
    fontWeight: "700",
  },
  previewScroller: {
    maxHeight: 220,
  },
  previewContent: {
    padding: 12,
  },
  previewText: {
    color: "#c8e4f4",
    fontFamily: "Courier",
    fontSize: 12,
    lineHeight: 18,
  },
  filePill: {
    minHeight: 38,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    backgroundColor: "rgba(45, 106, 150, 0.13)",
    borderWidth: 1,
    borderColor: "rgba(90, 177, 230, 0.18)",
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  filePillCompact: {
    minHeight: 42,
    alignItems: "flex-start",
  },
  filePillSelected: {
    backgroundColor: "rgba(65, 230, 189, 0.14)",
    borderColor: "rgba(65, 230, 189, 0.44)",
  },
  fileText: {
    flex: 1,
    color: "#cfe9f9",
    fontSize: 12,
    lineHeight: 17,
  },
  fileStatsGroup: {
    alignItems: "flex-end",
    gap: 2,
  },
  fileStatus: {
    color: "#9db5ca",
    fontSize: 10,
    fontWeight: "900",
    textTransform: "uppercase",
  },
  fileStats: {
    fontSize: 12,
    fontWeight: "800",
  },
  additionText: {
    color: "#7deccf",
  },
  deletionText: {
    color: "#ff9dac",
  },
  executionTimeline: {
    gap: 0,
  },
  executionTimelineHeader: {
    alignItems: "flex-start",
    marginBottom: 10,
  },
  executionStep: {
    flexDirection: "row",
    gap: 10,
    minHeight: 46,
  },
  executionRail: {
    width: 26,
    alignItems: "center",
  },
  executionMarker: {
    width: 26,
    height: 26,
    borderRadius: 13,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(15, 28, 42, 0.96)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.24)",
  },
  executionMarkerCompleted: {
    borderColor: "rgba(65, 230, 189, 0.52)",
  },
  executionMarkerActive: {
    borderColor: "rgba(251, 191, 36, 0.68)",
    backgroundColor: "rgba(251, 191, 36, 0.12)",
  },
  executionMarkerFailed: {
    borderColor: "rgba(248, 113, 113, 0.68)",
    backgroundColor: "rgba(248, 113, 113, 0.12)",
  },
  executionMarkerDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: "rgba(157, 181, 202, 0.78)",
  },
  executionConnector: {
    flex: 1,
    width: 2,
    backgroundColor: "rgba(124, 157, 188, 0.18)",
  },
  executionConnectorCompleted: {
    backgroundColor: "rgba(65, 230, 189, 0.48)",
  },
  executionStepBody: {
    flex: 1,
    minWidth: 0,
    paddingBottom: 14,
  },
  executionStepTopline: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    alignItems: "center",
    justifyContent: "space-between",
  },
  executionStepTitle: {
    color: "#edf8ff",
    fontSize: 14,
    fontWeight: "900",
  },
  executionStepPendingTitle: {
    color: "#8299ae",
  },
  executionStepState: {
    color: "#8299ae",
    fontSize: 10,
    fontWeight: "900",
    textTransform: "uppercase",
  },
  executionStepStateActive: {
    color: "#fef3c7",
  },
  executionStepStateFailed: {
    color: "#fca5a5",
  },
  executionStepDetail: {
    marginTop: 4,
    color: "#9db5ca",
    fontSize: 12,
    lineHeight: 17,
  },
  logList: {
    gap: 0,
  },
  logItem: {
    flexDirection: "row",
    gap: 10,
  },
  logRail: {
    width: 18,
    alignItems: "center",
    paddingTop: 5,
    backgroundColor: "transparent",
  },
  logDot: {
    width: 9,
    height: 9,
    borderRadius: 5,
    backgroundColor: "#38d9ff",
  },
  logDotError: {
    backgroundColor: "#ff5c76",
  },
  logBody: {
    flex: 1,
    paddingBottom: 14,
  },
  logMeta: {
    color: "#70869b",
    fontSize: 11,
    fontWeight: "800",
  },
  logMessage: {
    color: "#d7e9f5",
    fontSize: 13,
    lineHeight: 18,
    marginTop: 3,
  },
  rawLogsBox: {
    gap: 8,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(8, 18, 30, 0.32)",
    borderWidth: 1,
    borderColor: "rgba(124, 157, 188, 0.14)",
  },
  rawLogsToggle: {
    gap: 3,
  },
  rawLogsTitle: {
    color: "#edf8ff",
    fontSize: 14,
    fontWeight: "800",
  },
  rawLogScroller: {
    maxHeight: 260,
    borderRadius: 8,
    backgroundColor: "#02060b",
    borderWidth: 1,
    borderColor: "rgba(91, 129, 161, 0.22)",
  },
  rawLogList: {
    gap: 8,
    padding: 10,
  },
  rawLogItem: {
    gap: 5,
    paddingBottom: 8,
    borderBottomWidth: 1,
    borderBottomColor: "rgba(124, 157, 188, 0.12)",
  },
  rawLogMessage: {
    color: "#9db5ca",
    fontFamily: "Courier",
    fontSize: 12,
    lineHeight: 18,
  },
  loader: {
    minHeight: 80,
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
  },
});
