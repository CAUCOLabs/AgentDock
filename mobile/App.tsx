import { StatusBar } from "expo-status-bar";
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
  createTask,
  deleteTask,
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
} from "./src/api";
import { API_BASE_URL } from "./src/config";
import type {
  TaskDiffResponse,
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
  const [targetFile, setTargetFile] = useState("");
  const [githubSessionId, setGithubSessionId] = useState<string | null>(null);
  const [githubAccount, setGithubAccount] = useState<GitHubAccount | null>(null);
  const [githubInstallation, setGithubInstallation] = useState<GitHubInstallation | null>(null);
  const [githubRepos, setGithubRepos] = useState<GitHubRepository[]>([]);
  const [fileFilter, setFileFilter] = useState("");
  const [repoFiles, setRepoFiles] = useState<RepositoryFileEntry[]>([]);
  const [filePreview, setFilePreview] = useState<RepositoryFilePreviewResponse | null>(null);
  const [health, setHealth] = useState<"checking" | "online" | "offline">("checking");
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

  const selectedTaskId = selectedTask?.task_id ?? null;
  const canLaunch = repoUrl.trim().length > 0 && instruction.trim().length >= 10 && !isLaunching;
  const connectedGithubLogin = githubInstallation?.account_login ?? githubAccount?.login ?? null;
  const filteredRepoFiles = useMemo(
    () =>
      repoFiles
        .filter((entry) => entry.type === "file")
        .filter((entry) => entry.path.toLowerCase().includes(fileFilter.trim().toLowerCase()))
        .slice(0, 50),
    [fileFilter, repoFiles]
  );

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
      setHealth("online");
    } catch (healthError) {
      setHealth("offline");
      setError(healthError instanceof Error ? healthError.message : `Unable to reach ${API_BASE_URL}.`);
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
    void loadInitialData();
  }, []);

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
        agent: "mock",
        target_file: targetFile || null,
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
    setInstruction((current) => (current.trim() ? `${current.trim()}\n\n${value}` : value));
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
    setTargetFile("");
    setRepoFiles([]);
    setFilePreview(null);
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
          <View style={[styles.healthPill, health === "online" ? styles.healthOnline : styles.healthOffline]}>
            {health === "checking" ? (
              <ActivityIndicator size="small" color="#9eefff" />
            ) : (
              <View style={styles.healthDot} />
            )}
            <Text style={styles.healthText}>{health}</Text>
          </View>
        </View>

        {error ? (
          <View style={styles.errorCard}>
            <CircleAlert size={18} color="#ff8f9b" />
            <Text style={styles.errorText}>{error}</Text>
          </View>
        ) : null}

        <Section title="Prompt Launcher">
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
              <View style={styles.githubRepoList}>
                {githubRepos.slice(0, 50).map((repo) => (
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
              </View>
            ) : null}
          </View>

          <Field label="Repository URL">
            <TextInput
              value={repoUrl}
              onChangeText={setRepoUrl}
              placeholder="https://github.com/org/repo"
              placeholderTextColor="#6f7f92"
              autoCapitalize="none"
              autoCorrect={false}
              style={styles.input}
            />
          </Field>
          <View style={styles.row}>
            <View style={styles.flex}>
              <Field label="Base branch">
                <TextInput
                  value={baseBranch}
                  onChangeText={setBaseBranch}
                  placeholder="main"
                  placeholderTextColor="#6f7f92"
                  autoCapitalize="none"
                  autoCorrect={false}
                  style={styles.input}
                />
              </Field>
            </View>
            <View style={styles.agentBox}>
              <Text style={styles.label}>Agent</Text>
              <Text style={styles.agentValue}>mock</Text>
            </View>
          </View>
          <View style={styles.filePicker}>
            <View style={styles.filePickerHeader}>
              <View style={styles.flex}>
                <Text style={styles.label}>Target file</Text>
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
                <View style={styles.fileList}>
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
                </View>
              </>
            ) : null}
            {filePreview ? (
              <ScrollView horizontal style={styles.previewScroller}>
                <Text style={styles.previewText}>{filePreview.content}</Text>
              </ScrollView>
            ) : null}
          </View>
          <Field label="Prompt">
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
                <View>
                  <Text style={styles.taskId}>{selectedTask.task_id}</Text>
                  <Text style={styles.taskRepo}>{shortRepoName(selectedTask.repo_url)}</Text>
                </View>
                <StatusBadge status={selectedTask.status} />
              </View>
              <InfoRow label="Branch" value={selectedTask.working_branch ?? workspace?.branch ?? "None"} />
              <InfoRow label="Target file" value={selectedTask.target_file ?? "None"} />
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

        <Section title="Diff viewer">
          {selectedTask ? (
            <>
              <ChangedFiles files={diff.changed_files} targetFile={selectedTask.target_file ?? null} />
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

function StatusBadge({ status }: { status: TaskStatus }) {
  return (
    <View style={[styles.statusBadge, statusTone(status)]}>
      <Text style={styles.statusText}>{status.replaceAll("_", " ")}</Text>
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
            {targetFile === file.path ? " · target" : ""}
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
    gap: 4,
  },
  taskId: {
    color: "#eef9ff",
    fontSize: 14,
    fontWeight: "800",
  },
  taskRepo: {
    color: "#9db5ca",
    fontSize: 13,
  },
  taskDate: {
    color: "#64788c",
    fontSize: 12,
  },
  statusBadge: {
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderWidth: 1,
    maxWidth: 148,
  },
  statusText: {
    color: "#effbff",
    fontSize: 11,
    fontWeight: "800",
    textTransform: "uppercase",
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
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 12,
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
  githubRepoList: {
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
  previewScroller: {
    maxHeight: 180,
    borderRadius: 8,
    backgroundColor: "#02060b",
    borderWidth: 1,
    borderColor: "rgba(91, 129, 161, 0.22)",
  },
  previewText: {
    minWidth: 520,
    color: "#c8e4f4",
    fontFamily: "Courier",
    fontSize: 12,
    lineHeight: 18,
    padding: 12,
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
