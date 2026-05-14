import { StatusBar } from "expo-status-bar";
import * as Clipboard from "expo-clipboard";
import { useCallback, useEffect, useMemo, useState } from "react";
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
  GitBranch,
  RefreshCw,
  Send,
  Trash2,
  XCircle,
} from "lucide-react-native";

import {
  approveTask,
  connectAgentProvider,
  createTask,
  deleteTask,
  disconnectAgentProvider,
  getAgentProviders,
  getHealth,
  getGitHubInstallations,
  getGitHubRepos,
  getRepositoryFilePreview,
  getRepositoryFiles,
  getTask,
  getTaskDiff,
  getTaskLogs,
  getTasks,
  getWorkspaceStatus,
  logout,
  startGitHubAuth,
  subscribeToApiConnectivity,
} from "./src/api";
import { API_BASE_URL } from "./src/config";
import type {
  AgentProvider,
  AgentProviderMetadata,
  ProviderConnectionStatus,
  TaskDiffResponse,
  ChangeScope,
  GitHubAccount,
  GitHubInstallation,
  GitHubRepository,
  TaskLogEvent,
  TaskLogsResponse,
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

const providerOptions: Array<{ label: string; value: AgentProvider }> = [
  { label: "Codex", value: "codex" },
  { label: "Claude", value: "claude" },
  { label: "Ollama", value: "ollama" },
  { label: "Local model", value: "local_model" },
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

type ApiConnectionState = {
  status: "checking" | "online" | "offline";
  error: string | null;
};

function shortRepoName(repoUrl: string) {
  const trimmed = repoUrl.replace(/\/$/, "");
  const name = trimmed.split("/").slice(-2).join("/");
  return name.replace(/\.git$/, "") || repoUrl;
}

function shortSha(sha?: string | null) {
  return sha ? sha.slice(0, 8) : "None";
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

function formatConnectionStatus(status?: ProviderConnectionStatus) {
  switch (status) {
    case "connected":
      return "Connected";
    case "disconnected":
      return "Disconnected";
    case "not_configured":
      return "Not configured";
    case "coming_soon":
      return "Coming soon";
    default:
      return "Unknown";
  }
}

function providerConnectionMessage(provider: AgentProviderMetadata) {
  switch (provider.status) {
    case "connected":
      return `Connected. ${provider.description}`;
    case "disconnected":
      return `Disconnected. ${provider.description}`;
    case "not_configured":
      return `Not configured. ${provider.description}`;
    case "coming_soon":
      return `Coming soon. ${provider.description}`;
    default:
      return provider.description;
  }
}

function statusTone(status: TaskStatus) {
  switch (status) {
    case "completed":
    case "approved":
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
  const [agentProvider, setAgentProvider] = useState<AgentProvider>("codex");
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
  const [apiConnection, setApiConnection] = useState<ApiConnectionState>({
    status: "checking",
    error: null,
  });
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selectedTask, setSelectedTask] = useState<TaskSummary | null>(null);
  const [diff, setDiff] = useState<TaskDiffResponse>(emptyDiff);
  const [logs, setLogs] = useState<TaskLogsResponse>(emptyLogs);
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
  const [previewCopyStatus, setPreviewCopyStatus] = useState<"copied" | "failed" | null>(null);
  const [diffCopyStatus, setDiffCopyStatus] = useState<"copied" | "failed" | null>(null);

  const selectedTaskId = selectedTask?.task_id ?? null;
  const canLaunch = repoUrl.trim().length > 0 && instruction.trim().length >= 10 && !isLaunching;
  const connectedGithubLogin = githubInstallation?.account_login ?? githubAccount?.login ?? null;
  const selectedGithubRepo = useMemo(
    () => githubRepos.find((repo) => repo.clone_url === repoUrl) ?? null,
    [githubRepos, repoUrl]
  );
  const agentProviderById = useMemo(
    () => new Map(agentProviders.map((provider) => [provider.id, provider])),
    [agentProviders]
  );
  const selectedAgentMetadata = agentProviderById.get(agentProvider);
  const hasConnectedProvider = agentProviders.some((provider) => provider.status === "connected");
  const outsideTargetFiles = useMemo(
    () =>
      diff.changed_files.filter(
        (file) => selectedTask?.target_file && file.path !== selectedTask.target_file
      ),
    [diff.changed_files, selectedTask?.target_file]
  );
  const showScopeWarning =
    selectedTask?.change_scope === "only_selected_file" && outsideTargetFiles.length > 0;
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
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load task details.");
      setDiff(emptyDiff);
      setLogs(emptyLogs);
      setWorkspace(null);
    } finally {
      setIsLoadingTask(false);
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
      const nextTasks = await refreshTasks();
      if (selectedTaskId && nextTasks.some((task) => task.task_id === selectedTaskId)) {
        await loadTaskDetail(selectedTaskId);
      }
    } catch (tasksError) {
      setError(tasksError instanceof Error ? tasksError.message : "Unable to fetch tasks.");
    } finally {
      setIsRefreshing(false);
    }
  }, [loadTaskDetail, refreshTasks, selectedTaskId]);

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
    void (async () => {
      try {
        setAgentProviders(await getAgentProviders());
      } catch {
        setAgentProviders([]);
      }
    })();
  }, []);

  async function handleConnectAgentProvider(providerId: AgentProvider) {
    try {
      await connectAgentProvider(providerId);
      setAgentProviders(await getAgentProviders());
    } catch (connectError) {
      setError(connectError instanceof Error ? connectError.message : "Unable to connect provider.");
    }
  }

  async function handleDisconnectAgentProvider(providerId: AgentProvider) {
    try {
      await disconnectAgentProvider(providerId);
      setAgentProviders(await getAgentProviders());
    } catch (disconnectError) {
      setError(disconnectError instanceof Error ? disconnectError.message : "Unable to disconnect provider.");
    }
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
        agent: agentProvider,
        target_file: targetFile || null,
        change_scope: changeScope,
      }, githubSessionId);
      await refreshTasks();
      await loadTaskDetail(created.task_id);
      setInstruction("");
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
    setTargetFile(path);
    setError(null);
    setPreviewCopyStatus(null);
    try {
      setFilePreview(
        await getRepositoryFilePreview({
          repo_url: repoUrl.trim(),
          base_branch: baseBranch.trim() || "main",
          path,
        }, githubSessionId)
      );
    } catch (previewError) {
      setFilePreview(null);
      setError(previewError instanceof Error ? previewError.message : "Unable to preview file.");
    }
  }

  async function handleApproval(approve: boolean) {
    if (!selectedTask) {
      return;
    }

    setIsActing(true);
    setError(null);
    try {
      await approveTask(selectedTask.task_id, {
        approve,
        message: approve ? "Approved from AgentDock mobile." : "Rejected from AgentDock mobile.",
      });
      await refreshTasks();
      await loadTaskDetail(selectedTask.task_id);
    } catch (approvalError) {
      setError(approvalError instanceof Error ? approvalError.message : "Unable to update approval.");
    } finally {
      setIsActing(false);
    }
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

        <Section title="Prompt Launcher">
          <Text style={styles.launcherGroupTitle}>Connections</Text>
          <View style={styles.githubPanel}>
            <View style={styles.githubHeader}>
              <View style={styles.githubTitleRow}>
                <GitBranch size={18} color="#dcecf7" />
                <Text style={styles.githubTitle}>GitHub</Text>
              </View>
              <Text style={styles.githubSubtitle}>
                {connectedGithubLogin
                  ? `Connected as ${connectedGithubLogin}`
                  : "Manual repository URL mode remains available."}
              </Text>
            </View>
            <View style={styles.githubButtonRow}>
              <Pressable
                onPress={() => connectedGithubLogin ? void handleRefreshGitHub() : void handleConnectGitHub()}
                disabled={isConnectingGithub || isLoadingGithubRepos}
                style={({ pressed }) => [styles.smallButton, pressed && styles.pressed]}
              >
                <Text style={styles.smallButtonText}>
                  {connectedGithubLogin
                    ? isLoadingGithubRepos
                      ? "Loading repos"
                      : "Refresh repos"
                    : isConnectingGithub
                      ? "Connecting"
                      : "Connect GitHub"}
                </Text>
              </Pressable>
              {githubSessionId ? (
                <>
                  <Pressable
                    onPress={() => void handleRefreshGitHub()}
                    disabled={isLoadingGithubRepos}
                    style={({ pressed }) => [styles.smallButton, pressed && styles.pressed]}
                  >
                    <Text style={styles.smallButtonText}>Check status</Text>
                  </Pressable>
                  <Pressable
                    onPress={() => void handleLogoutGitHub()}
                    style={({ pressed }) => [styles.smallButton, pressed && styles.pressed]}
                  >
                    <Text style={styles.smallButtonText}>Logout</Text>
                  </Pressable>
                </>
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

          <Text style={styles.launcherGroupTitle}>AI Agent</Text>
          <View style={styles.agentProviderBox}>
            <Text style={styles.fileOptionMeta}>Choose the agent profile for this task.</Text>
            <View style={styles.providerGrid}>
              {providerOptions.map((provider) => {
                const metadata = agentProviderById.get(provider.value);
                const status = metadata?.status ?? "disconnected";
                const isConnected = status === "connected";
                const canConnect = status === "disconnected";

                return (
                  <View
                    key={provider.value}
                    style={[
                      styles.providerOption,
                      agentProvider === provider.value && styles.providerOptionSelected,
                    ]}
                  >
                    <Pressable
                      onPress={() => setAgentProvider(provider.value)}
                      style={({ pressed }) => [styles.providerSelectArea, pressed && styles.pressed]}
                    >
                      <Text style={styles.providerOptionText}>{metadata?.label ?? provider.label}</Text>
                      <Text
                        style={[
                          styles.providerStatusBadge,
                          isConnected && styles.providerStatusAvailable,
                        ]}
                      >
                        {formatConnectionStatus(status)}
                      </Text>
                      <Text style={styles.providerDescription}>
                        {metadata?.description ?? "Provider metadata is loading."}
                      </Text>
                    </Pressable>
                    {isConnected ? (
                      <Pressable
                        onPress={() => void handleDisconnectAgentProvider(provider.value)}
                        style={({ pressed }) => [styles.providerActionButton, pressed && styles.pressed]}
                      >
                        <Text style={styles.providerActionText}>Disconnect</Text>
                      </Pressable>
                    ) : (
                      <Pressable
                        disabled={!canConnect}
                        onPress={() => void handleConnectAgentProvider(provider.value)}
                        style={({ pressed }) => [
                          styles.providerActionButton,
                          !canConnect && styles.disabledButton,
                          pressed && styles.pressed,
                        ]}
                      >
                        <Text style={styles.providerActionText}>Connect</Text>
                      </Pressable>
                    )}
                  </View>
                );
              })}
            </View>
            <Text style={styles.providerModeNote}>
              {selectedAgentMetadata
                ? providerConnectionMessage(selectedAgentMetadata)
                : "Provider setup state is loading."}
            </Text>
            {!hasConnectedProvider ? (
              <Text style={styles.providerModeNote}>
                Development agent active: tasks currently run using the local development agent.
              </Text>
            ) : null}
          </View>

          <Text style={styles.launcherGroupTitle}>Repository</Text>
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

          <Text style={styles.launcherGroupTitle}>Target Selection</Text>
          <View style={styles.filePicker}>
            <View style={styles.filePickerHeader}>
              <View style={styles.flex}>
                <Text style={styles.label}>Repository Explorer</Text>
                <Text style={styles.filePath}>{targetFile || "No file selected"}</Text>
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
            ) : null}
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

          <Text style={styles.launcherGroupTitle}>Review & Approval</Text>
          <View style={styles.scopeBox}>
            <Text style={styles.label}>Change scope</Text>
            <Pressable
              onPress={() => setChangeScope("only_selected_file")}
              style={[
                styles.scopeOption,
                changeScope === "only_selected_file" && styles.scopeOptionSelected,
              ]}
            >
              <Text style={styles.scopeTitle}>Only selected file</Text>
              <Text style={styles.fileOptionMeta}>Safest default. Related edits should be rare.</Text>
            </Pressable>
            <Pressable
              onPress={() => setChangeScope("allow_related_files")}
              style={[
                styles.scopeOption,
                changeScope === "allow_related_files" && styles.scopeOptionSelected,
              ]}
            >
              <Text style={styles.scopeTitle}>Allow related files</Text>
              <Text style={styles.fileOptionMeta}>Use when tests, types, or adjacent modules may need edits.</Text>
            </Pressable>
          </View>

          <Text style={styles.launcherGroupTitle}>Task Prompt</Text>
          <Field label="What should the agent do?">
            <TextInput
              value={instruction}
              onChangeText={setInstruction}
              placeholder="Describe the coding task..."
              placeholderTextColor="#6f7f92"
              multiline
              textAlignVertical="top"
              style={[styles.input, styles.promptInput]}
            />
          </Field>
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
                {isLaunching ? "Launching..." : "Launch Agent Task"}
              </Text>
            </Pressable>
          </View>
        </Section>

        <Section title="Recent tasks" right={<RefreshCw size={18} color="#8ba9c6" />}>
          {latestTasks.length === 0 ? (
            <Text style={styles.emptyText}>No tasks yet.</Text>
          ) : (
            <View style={styles.taskList}>
              {latestTasks.map((task) => (
                <Pressable
                  key={task.task_id}
                  onPress={() => loadTaskDetail(task.task_id)}
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

        <Section title="Selected task detail">
          {isLoadingTask ? (
            <Loader label="Loading task details..." />
          ) : selectedTask ? (
            <View style={styles.detailStack}>
              <View style={styles.detailHeader}>
                <Text style={styles.taskId}>{selectedTask.task_id}</Text>
                <Text style={styles.taskRepo}>{shortRepoName(selectedTask.repo_url)}</Text>
              </View>
              <InfoRow label="Status" value={selectedTask.status.replaceAll("_", " ")} />
              <InfoRow label="Branch" value={selectedTask.working_branch ?? workspace?.branch ?? "None"} />
              <InfoRow label="Target file" value={selectedTask.target_file ?? "None"} />
              <InfoRow label="Change scope" value={(selectedTask.change_scope ?? "only_selected_file").replaceAll("_", " ")} />
              <InfoRow label="Commit sha" value={shortSha(selectedTask.commit_sha ?? workspace?.commit_sha)} />
              <InfoRow label="Pushed" value={selectedTask.pushed ? "Yes" : "No"} />
              <InfoRow label="PR URL" value={selectedTask.pr_url ?? "None"} link={selectedTask.pr_url} />
              {selectedTask.error ? <Text style={styles.errorText}>{selectedTask.error}</Text> : null}
              <View style={styles.actionRow}>
                <ActionButton
                  label="Approve"
                  icon={<CheckCircle2 size={18} color="#031014" />}
                  disabled={isActing}
                  onPress={() => handleApproval(true)}
                  tone="approve"
                />
                <ActionButton
                  label="Reject"
                  icon={<XCircle size={18} color="#ffd4d8" />}
                  disabled={isActing}
                  onPress={() => handleApproval(false)}
                  tone="reject"
                />
                <ActionButton
                  label="Delete"
                  icon={<Trash2 size={18} color="#ffd4d8" />}
                  disabled={isActing}
                  onPress={handleDelete}
                  tone="delete"
                />
              </View>
            </View>
          ) : (
            <Text style={styles.emptyText}>Select a task to inspect it.</Text>
          )}
        </Section>

        <Section
          title="Diff viewer"
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
              <ChangedFiles files={diff.changed_files} targetFile={selectedTask.target_file ?? null} />
              {showScopeWarning ? (
                <Text style={styles.warningText}>
                  Scope warning: this diff includes {outsideTargetFiles.length} file
                  {outsideTargetFiles.length === 1 ? "" : "s"} outside the selected target.
                </Text>
              ) : null}
              <ScrollView horizontal style={styles.diffScroller}>
                <Text style={styles.diffText}>{diff.diff || "No diff available."}</Text>
              </ScrollView>
            </>
          ) : (
            <Text style={styles.emptyText}>Diff appears after selecting a task.</Text>
          )}
        </Section>

        <Section title="Agent logs">
          {selectedTask ? <LogTimeline events={logs.events} /> : <Text style={styles.emptyText}>Logs appear after selecting a task.</Text>}
        </Section>

        <Section title="Workspace status">
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
              <InfoRow label="Dirty" value={workspace.is_dirty ? "Yes" : "No"} />
              <InfoRow label="Untracked" value={workspace.has_untracked_files ? "Yes" : "No"} />
              <InfoRow label="Path" value={workspace.repo_path} />
              <ChangedFiles files={workspace.changed_files} targetFile={selectedTask.target_file ?? null} />
            </View>
          ) : (
            <Text style={styles.emptyText}>Workspace status appears after selecting a task.</Text>
          )}
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

function ChangedFiles({
  files,
  targetFile,
}: {
  files: { path: string; status: string; additions: number; deletions: number }[];
  targetFile?: string | null;
}) {
  if (files.length === 0) {
    return <Text style={styles.emptyText}>No changed files.</Text>;
  }

  return (
    <View style={styles.fileList}>
      {files.slice(0, 8).map((file) => (
        <View key={`${file.status}-${file.path}`} style={styles.filePill}>
          <GitBranch size={14} color="#8bdcff" />
          <Text style={styles.fileText} numberOfLines={1}>
            {file.status} {file.path}
            {targetFile === file.path ? " · target" : targetFile ? " · outside target" : ""}
          </Text>
          <Text style={styles.fileStats}>
            +{file.additions} -{file.deletions}
          </Text>
        </View>
      ))}
      {files.length > 8 ? <Text style={styles.emptyText}>+{files.length - 8} more files</Text> : null}
    </View>
  );
}

function LogTimeline({ events }: { events: TaskLogEvent[] }) {
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
            <Text style={styles.logMessage}>{event.message}</Text>
          </View>
        </View>
      ))}
    </View>
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
    minHeight: 148,
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
  scopeTitle: {
    color: "#edf8ff",
    fontSize: 13,
    fontWeight: "800",
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
    backgroundColor: "rgba(45, 106, 150, 0.13)",
    borderWidth: 1,
    borderColor: "rgba(90, 177, 230, 0.18)",
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  fileText: {
    flex: 1,
    color: "#cfe9f9",
    fontSize: 12,
  },
  fileStats: {
    color: "#7deccf",
    fontSize: 12,
    fontWeight: "800",
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
  loader: {
    minHeight: 80,
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
  },
});
