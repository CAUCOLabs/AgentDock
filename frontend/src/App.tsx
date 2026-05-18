import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { motion } from 'framer-motion'
import {
  Activity,
  Bot,
  CheckCircle2,
  Code2,
  ExternalLink,
  FileText,
  GitBranch,
  GitPullRequest,
  RefreshCw,
  Rocket,
  ShieldCheck,
  Terminal,
  Trash2,
  UploadCloud,
  XCircle,
} from 'lucide-react'
import './App.css'

type TaskStatus =
  | 'queued'
  | 'running'
  | 'waiting_for_approval'
  | 'approved'
  | 'committed'
  | 'pushed'
  | 'pull_request_opened'
  | 'merged'
  | 'rejected'
  | 'completed'
  | 'failed'
  | 'cancelled'

type AgentProvider = string
type ChangeScope = 'only_selected_file' | 'allow_related_files' | 'whole_project'
type PromptAssistantMode = 'manual' | 'openai' | 'anthropic'
type PromptAssistantProvider = 'fallback' | 'openai' | 'anthropic'
type ProviderConnectionStatus =
  | 'connected'
  | 'connecting'
  | 'disconnected'
  | 'not_configured'
  | 'coming_soon'
type ProviderReadiness = 'ready' | 'auth_required' | 'unavailable' | 'not_configured'
type ProviderPrimaryStatus =
  | 'connected'
  | 'connecting'
  | 'active'
  | 'disconnected'
  | 'ready'
  | 'unavailable'
  | 'not_configured'

type ChangedFile = {
  path: string
  status: string
  additions: number
  deletions: number
}

type RepositoryFileEntry = {
  path: string
  type: 'file' | 'directory'
  size?: number | null
  extension?: string | null
}

type RepositoryFilesResponse = {
  repo_url: string
  base_branch: string
  files: RepositoryFileEntry[]
}

type RepositoryFilePreviewResponse = {
  repo_url: string
  base_branch: string
  path: string
  size: number
  truncated: boolean
  content: string
}

type GitHubAccount = {
  github_user_id: number
  login: string
  name?: string | null
  avatar_url?: string | null
  html_url?: string | null
  scopes?: string | null
  connected_at: string
}

type GitHubInstallation = {
  installation_id: number
  account_login: string
  account_type: string
  selected_repositories: string[]
  permissions: Record<string, string>
  created_at: string
  updated_at: string
}

type GitHubRepository = {
  id: number
  name: string
  full_name: string
  html_url: string
  clone_url: string
  default_branch: string
  private: boolean
  visibility: string
  can_read: boolean
  can_push: boolean
}

type GitHubAuthStartResponse = {
  auth_url: string
  session_id: string
  state: string
}

type GitHubRepositoriesResponse = {
  repositories: GitHubRepository[]
}

type AgentProviderMetadata = {
  id: AgentProvider
  label: string
  auth_mode: string
  status: ProviderConnectionStatus
  connection: ProviderConnectionStatus
  readiness: ProviderReadiness
  primary_status: ProviderPrimaryStatus
  description: string
  supports_chatgpt_login: boolean
  supports_api_key: boolean
}

type AgentProviderAuthFlowResponse = {
  provider_id: AgentProvider
  status: 'auth_required' | 'waiting_for_auth'
  message: string
  command?: string | null
  fallback_command?: string | null
  auth_url?: string | null
  verification_url?: string | null
  device_code?: string | null
  user_code?: string | null
  instructions: string[]
  metadata: AgentProviderMetadata
}

type AgentProviderVerificationResponse = {
  provider_id: AgentProvider
  connected: boolean
  message: string
  metadata: AgentProviderMetadata
}

type TaskSummary = {
  task_id: string
  repo_url: string
  base_branch: string
  working_branch: string | null
  target_file?: string | null
  change_scope?: ChangeScope
  instruction: string
  agent: AgentProvider
  status: TaskStatus
  changed_files: ChangedFile[]
  commit_sha: string | null
  pushed: boolean
  pushed_at?: string | null
  remote_branch?: string | null
  last_push_result?: string | null
  branch_url?: string | null
  pr_url: string | null
  pr_number?: number | null
  pr_state?: string | null
  merged_at?: string | null
  merge_commit_sha?: string | null
  merged_by?: string | null
  merged_source?: string | null
  created_at: string
  updated_at: string
  error: string | null
}

type TaskCreateResponse = {
  task_id: string
  status: TaskStatus
  message: string
}

type TaskApprovalResponse = {
  task_id: string
  status: TaskStatus
  message: string
}

type TaskPullRequestResponse = {
  task_id: string
  status: TaskStatus
  message: string
  pr_url: string
  pr_number: number
  pr_state: string
}

type TaskMergePullRequestResponse = {
  task_id: string
  status: TaskStatus
  message: string
  pr_url: string
  pr_number: number
  pr_state: string
  merged_at: string
  merge_commit_sha: string
  merged_by?: string | null
  merged_source?: string | null
}

type MergeAttemptState = {
  taskId: string
  status: 'loading' | 'succeeded' | 'failed'
  message: string
}

type TaskDiffResponse = {
  task_id: string
  status: TaskStatus
  diff: string
  changed_files: ChangedFile[]
}

type WorkspaceStatusResponse = {
  task_id: string
  repo_path: string
  branch: string | null
  head_commit: string | null
  remote_url?: string | null
  has_valid_origin?: boolean
  default_branch?: string | null
  push_enabled?: boolean
  pr_creation_enabled?: boolean
  is_dirty: boolean
  has_untracked_files: boolean
  changed_files: ChangedFile[]
  commit_sha: string | null
  pushed: boolean
  pushed_at?: string | null
  remote_branch?: string | null
  last_push_result?: string | null
  branch_url?: string | null
  pr_url: string | null
  pr_number?: number | null
  pr_state?: string | null
  merged_at?: string | null
  merge_commit_sha?: string | null
  merged_by?: string | null
  merged_source?: string | null
}

type TaskLogEvent = {
  timestamp: string
  level: string
  message: string
}

type TimelineLogEvent = TaskLogEvent & {
  label: string
}

type TaskLogsResponse = {
  task_id: string
  events: TaskLogEvent[]
}

type TaskDeleteResponse = {
  task_id: string
  status: string
  message: string
}

type PromptAssistantRefineResponse = {
  provider: PromptAssistantProvider
  title: string
  recommended_scope: 'single_file' | 'related_files' | 'whole_project'
  refined_prompt: string
  safety_notes: string[]
  used_fallback: boolean
}

type PromptAssistantProviderStatus = {
  provider: PromptAssistantProvider
  connected: boolean
  label: string
  last_verified_at?: string | null
  error?: string | null
}

type CreateTaskForm = {
  repoUrl: string
  baseBranch: string
  instruction: string
  agent: AgentProvider
  targetFile: string
  changeScope: ChangeScope
}

const API_URL =
  import.meta.env.VITE_AGENTDOCK_API_URL?.replace(/\/$/, '') ??
  'http://127.0.0.1:8000'

const GITHUB_SESSION_STORAGE_KEY = 'agentdock.githubSessionId'

const initialForm: CreateTaskForm = {
  repoUrl: '',
  baseBranch: 'main',
  instruction: '',
  agent: 'codex',
  targetFile: '',
  changeScope: 'only_selected_file',
}

const promptPlaceholderExamples = [
  'Create project scaffold',
  'Build desktop app structure',
  'Implement authentication flow',
  'Refactor backend architecture',
  'Create REST API and models',
]

const scopeOptions: Array<{
  value: ChangeScope
  label: string
  description: string
  broad?: boolean
}> = [
  {
    value: 'only_selected_file',
    label: 'Single file',
    description: 'Modify only the selected file.',
  },
  {
    value: 'allow_related_files',
    label: 'Related files',
    description: 'Allow nearby modules, tests, configs, and related files to change.',
  },
  {
    value: 'whole_project',
    label: 'Whole project',
    description:
      'Codex can create, modify, and organize files across the repository to complete larger development tasks.',
    broad: true,
  },
]

const providerOptions: Array<{ label: string; value: AgentProvider }> = [
  { label: 'OpenAI Codex', value: 'codex' },
]

const promptAssistantModes: Array<{
  value: PromptAssistantMode
  label: string
}> = [
  { value: 'manual', label: 'Manual mode' },
  { value: 'openai', label: 'ChatGPT / OpenAI' },
  // Claude provider is implemented backend-side but hidden in UI until enabled later.
]

const promptTemplates = [
  {
    label: 'Fix bug',
    prompt:
      'Investigate and fix the reported bug. Keep the change minimal, avoid unrelated refactors, and explain the changed files.',
  },
  {
    label: 'Refactor',
    prompt:
      'Refactor the selected area for clarity and maintainability. Preserve behavior, avoid broad rewrites, and show the diff before approval.',
  },
  {
    label: 'Add feature',
    prompt:
      'Implement the requested feature with minimal changes. Keep the existing architecture, update related types if needed, and show the diff before approval.',
  },
  {
    label: 'Review code',
    prompt:
      'Review the repository area related to this task. Identify likely issues, make only safe improvements, and leave notes in the generated task summary.',
  },
  {
    label: 'Write tests',
    prompt:
      'Add or improve tests for the requested behavior. Keep production code changes minimal unless required.',
  },
]

function App() {
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [selectedTask, setSelectedTask] = useState<TaskSummary | null>(null)
  const [taskDiff, setTaskDiff] = useState<TaskDiffResponse | null>(null)
  const [workspaceStatus, setWorkspaceStatus] =
    useState<WorkspaceStatusResponse | null>(null)
  const [taskLogs, setTaskLogs] = useState<TaskLogEvent[]>([])
  const [isPollingLogs, setIsPollingLogs] = useState(false)
  const [logsRefreshError, setLogsRefreshError] = useState<string | null>(null)
  const [logsCopyStatus, setLogsCopyStatus] = useState<'copied' | 'failed' | null>(
    null,
  )
  const [form, setForm] = useState<CreateTaskForm>(initialForm)
  const [promptAssistantMode, setPromptAssistantMode] =
    useState<PromptAssistantMode>('manual')
  const [assistantRoughIdea, setAssistantRoughIdea] = useState('')
  const [assistantRefinedPrompt, setAssistantRefinedPrompt] = useState('')
  const [assistantResult, setAssistantResult] =
    useState<PromptAssistantRefineResponse | null>(null)
  const [assistantError, setAssistantError] = useState<string | null>(null)
  const [assistantCopyStatus, setAssistantCopyStatus] = useState<
    'copied' | 'failed' | null
  >(null)
  const [promptAssistantProviders, setPromptAssistantProviders] = useState<
    PromptAssistantProviderStatus[]
  >([])
  const [assistantApiKeyProvider, setAssistantApiKeyProvider] =
    useState<PromptAssistantProvider | null>(null)
  const [assistantApiKey, setAssistantApiKey] = useState('')
  const [assistantProviderAction, setAssistantProviderAction] =
    useState<PromptAssistantProvider | null>(null)
  const [assistantProviderError, setAssistantProviderError] = useState<string | null>(
    null,
  )
  const [assistantProviderNotice, setAssistantProviderNotice] = useState<string | null>(
    null,
  )
  const [isGeneratingPrompt, setIsGeneratingPrompt] = useState(false)
  const [isAssistantPreviewExpanded, setIsAssistantPreviewExpanded] = useState(false)
  const [promptSentToCodexTask, setPromptSentToCodexTask] = useState(false)
  const [githubSessionId, setGithubSessionId] = useState<string | null>(() =>
    localStorage.getItem(GITHUB_SESSION_STORAGE_KEY),
  )
  const [githubAccount, setGithubAccount] = useState<GitHubAccount | null>(null)
  const [githubInstallation, setGithubInstallation] =
    useState<GitHubInstallation | null>(null)
  const [githubRepos, setGithubRepos] = useState<GitHubRepository[]>([])
  const [githubRepoFilter, setGithubRepoFilter] = useState('')
  const [agentProviders, setAgentProviders] = useState<AgentProviderMetadata[]>([])
  const [codexCloudNotice, setCodexCloudNotice] = useState<string | null>(null)
  const [codexPendingAuth, setCodexPendingAuth] = useState(false)
  const [codexAuthFlow, setCodexAuthFlow] =
    useState<AgentProviderAuthFlowResponse | null>(null)
  const [codexCopyStatus, setCodexCopyStatus] = useState<'copied' | 'failed' | null>(
    null,
  )
  const [connectingProviderId, setConnectingProviderId] =
    useState<AgentProvider | null>(null)
  const [repoFiles, setRepoFiles] = useState<RepositoryFileEntry[]>([])
  const [fileFilter, setFileFilter] = useState('')
  const [filePreview, setFilePreview] =
    useState<RepositoryFilePreviewResponse | null>(null)
  const [previewCopyStatus, setPreviewCopyStatus] = useState<
    'copied' | 'failed' | null
  >(null)
  const [diffCopyStatus, setDiffCopyStatus] = useState<
    'copied' | 'failed' | null
  >(null)
  const [isLoadingFiles, setIsLoadingFiles] = useState(false)
  const [isConnectingGithub, setIsConnectingGithub] = useState(false)
  const [isLoadingGithubRepos, setIsLoadingGithubRepos] = useState(false)
  const [isLoadingPreview, setIsLoadingPreview] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [isCreating, setIsCreating] = useState(false)
  const [mergeAttempt, setMergeAttempt] = useState<MergeAttemptState | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [apiConnectionError, setApiConnectionError] = useState<string | null>(
    null,
  )
  const connectedGithubLogin =
    githubInstallation?.account_login ?? githubAccount?.login ?? null
  const isGithubConnected = Boolean(
    connectedGithubLogin || githubSessionId || githubInstallation || githubAccount,
  )

  const selectedTaskFromList = useMemo(
    () => tasks.find((task) => task.task_id === selectedTaskId) ?? null,
    [selectedTaskId, tasks],
  )
  const activeTask = selectedTask ?? selectedTaskFromList
  const agentProviderById = useMemo(
    () =>
      new Map(
        agentProviders.map((provider) => [provider.id, provider]),
      ),
    [agentProviders],
  )
  const codexProvider = agentProviderById.get('codex')
  const activeAgentLabel = activeTask
    ? agentProviderById.get(activeTask.agent)?.label ??
      providerOptions.find((provider) => provider.value === activeTask.agent)?.label ??
      activeTask.agent
    : null
  const hasConnectedProvider = codexProvider?.status === 'connected'
  const activePromptAssistantProviderId: PromptAssistantProvider | null =
    promptAssistantMode === 'manual' ? null : promptAssistantMode
  const activePromptAssistantProvider = useMemo(
    () =>
      activePromptAssistantProviderId === null
        ? null
        : promptAssistantProviders.find(
            (provider) => provider.provider === activePromptAssistantProviderId,
          ) ?? null,
    [activePromptAssistantProviderId, promptAssistantProviders],
  )

  const filteredRepoFiles = useMemo(
    () =>
      repoFiles
        .filter((entry) => entry.type === 'file')
        .filter((entry) =>
          entry.path.toLowerCase().includes(fileFilter.trim().toLowerCase()),
        )
        .slice(0, 80),
    [fileFilter, repoFiles],
  )

  const filteredGithubRepos = useMemo(
    () =>
      githubRepos.filter((repo) =>
        repo.full_name
          .toLowerCase()
          .includes(githubRepoFilter.trim().toLowerCase()),
      ),
    [githubRepoFilter, githubRepos],
  )
  const selectedGithubRepo = useMemo(
    () => githubRepos.find((repo) => repo.clone_url === form.repoUrl) ?? null,
    [form.repoUrl, githubRepos],
  )
  const hasCodexPrompt = form.instruction.trim().length > 0
  const isTaskActive = activeTask ? isLiveActivityStatus(activeTask.status) : false
  const timelineLogs = useMemo(() => visibleTimelineLogs(taskLogs), [taskLogs])
  const rawLogs = useMemo(() => rawExecutionLogs(taskLogs), [taskLogs])
  const activeTaskOutsideTargetFiles = useMemo(
    () =>
      (taskDiff?.changed_files ?? []).filter(
        (file) => activeTask?.target_file && file.path !== activeTask.target_file,
      ),
    [activeTask?.target_file, taskDiff?.changed_files],
  )
  const showScopeWarning =
    activeTask?.change_scope === 'only_selected_file' &&
    activeTaskOutsideTargetFiles.length > 0
  const codexConnection =
    codexPendingAuth || connectingProviderId === 'codex'
      ? 'connecting'
      : codexProvider?.connection ?? codexProvider?.status ?? 'disconnected'
  const codexReadiness = codexProvider?.readiness ?? 'not_configured'
  const codexPrimaryStatus =
    codexConnection === 'connecting'
      ? 'connecting'
      : codexProvider?.primary_status ??
        providerPrimaryStatus(codexConnection, codexReadiness)
  const isCodexActive =
    codexPrimaryStatus === 'active' || codexConnection === 'connected'
  const canConnectCodex =
    codexConnection === 'disconnected' &&
    (codexReadiness === 'auth_required' || codexReadiness === 'ready')
  const codexConnectionLabel =
    codexConnection === 'connecting'
      ? 'Connecting'
      : isCodexActive
        ? 'Connected'
        : 'Disconnected'

  useEffect(() => {
    void loadTasks()
    void loadAgentProviders()
    void loadPromptAssistantProviders()
  }, [])

  useEffect(() => {
    if (githubSessionId) {
      localStorage.setItem(GITHUB_SESSION_STORAGE_KEY, githubSessionId)
      void loadGithubInstallation(githubSessionId)
    } else {
      localStorage.removeItem(GITHUB_SESSION_STORAGE_KEY)
      setGithubAccount(null)
      setGithubInstallation(null)
      setGithubRepos([])
    }
  }, [githubSessionId])

  useEffect(() => {
    if (!selectedTaskId) {
      setSelectedTask(null)
      setTaskDiff(null)
      setWorkspaceStatus(null)
      setTaskLogs([])
      return
    }

    void loadTaskBundle(selectedTaskId)
  }, [selectedTaskId])

  useEffect(() => {
    if (!activeTask?.task_id || !isLiveActivityStatus(activeTask.status)) {
      setIsPollingLogs(false)
      return
    }

    setIsPollingLogs(true)
    void loadTaskLogs(activeTask.task_id, true)
    const intervalId = window.setInterval(() => {
      void loadTaskLogs(activeTask.task_id, true)
    }, 1500)

    return () => {
      window.clearInterval(intervalId)
      setIsPollingLogs(false)
    }
  }, [activeTask?.task_id, activeTask?.status])

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    let response: Response

    try {
      response = await fetch(`${API_URL}${path}`, {
        headers: {
          'Content-Type': 'application/json',
          ...(githubSessionId ? { 'X-AgentDock-Session': githubSessionId } : {}),
          ...init?.headers,
        },
        ...init,
      })
    } catch {
      const message = `Unable to reach AgentDock API at ${API_URL}.`
      setApiConnectionError(message)
      throw new Error(message)
    }

    setApiConnectionError(null)

    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`

      try {
        const body = (await response.json()) as { detail?: unknown }
        detail =
          typeof body.detail === 'string'
            ? body.detail
            : typeof body.detail === 'object' &&
                body.detail !== null &&
                'error' in body.detail &&
                typeof body.detail.error === 'string'
              ? body.detail.error
            : JSON.stringify(body.detail ?? body)
      } catch {
        detail = await response.text()
      }

      throw new Error(detail)
    }

    return response.json() as Promise<T>
  }

  async function loadTasks() {
    setIsLoading(true)
    setError(null)

    try {
      const nextTasks = await request<TaskSummary[]>('/tasks')
      setTasks(nextTasks)
      setSelectedTaskId((current) => current ?? nextTasks[0]?.task_id ?? null)
    } catch (err) {
      setError(formatError(err))
    } finally {
      setIsLoading(false)
    }
  }

  async function loadAgentProviders() {
    try {
      setAgentProviders(await request<AgentProviderMetadata[]>('/agent-providers'))
    } catch {
      setAgentProviders([])
    }
  }

  async function loadPromptAssistantProviders() {
    try {
      setPromptAssistantProviders(
        await request<PromptAssistantProviderStatus[]>(
          '/prompt-assistant/providers',
        ),
      )
    } catch {
      setPromptAssistantProviders([])
    }
  }

  async function savePromptAssistantKey(provider: PromptAssistantProvider) {
    if (provider === 'fallback' || !assistantApiKey.trim()) return

    setAssistantProviderAction(provider)
    setAssistantProviderError(null)
    setAssistantProviderNotice(null)

    try {
      const status = await request<PromptAssistantProviderStatus>(
        '/prompt-assistant/providers/connect',
        {
          method: 'POST',
          body: JSON.stringify({
            provider,
            api_key: assistantApiKey.trim(),
          }),
        },
      )
      setPromptAssistantProviders((current) =>
        upsertPromptAssistantProvider(current, status),
      )
      setAssistantApiKey('')
      setAssistantApiKeyProvider(null)
      setAssistantProviderNotice(`${status.label} connected.`)
    } catch (err) {
      setAssistantProviderError(formatError(err))
    } finally {
      setAssistantProviderAction(null)
    }
  }

  async function testPromptAssistantProvider(provider: PromptAssistantProvider) {
    if (provider === 'fallback') return

    setAssistantProviderAction(provider)
    setAssistantProviderError(null)
    setAssistantProviderNotice(null)

    try {
      const status = await request<PromptAssistantProviderStatus>(
        '/prompt-assistant/providers/test',
        {
          method: 'POST',
          body: JSON.stringify({ provider }),
        },
      )
      setPromptAssistantProviders((current) =>
        upsertPromptAssistantProvider(current, status),
      )
      if (status.error) {
        setAssistantProviderError(status.error)
      } else {
        setAssistantProviderNotice(`${status.label} connection verified.`)
      }
    } catch (err) {
      setAssistantProviderError(formatError(err))
    } finally {
      setAssistantProviderAction(null)
    }
  }

  async function disconnectPromptAssistantProvider(provider: PromptAssistantProvider) {
    if (provider === 'fallback') return

    setAssistantProviderAction(provider)
    setAssistantProviderError(null)
    setAssistantProviderNotice(null)

    try {
      const status = await request<PromptAssistantProviderStatus>(
        '/prompt-assistant/providers/disconnect',
        {
          method: 'POST',
          body: JSON.stringify({ provider }),
        },
      )
      setPromptAssistantProviders((current) =>
        upsertPromptAssistantProvider(current, status),
      )
      setAssistantProviderNotice(`${status.label} disconnected.`)
    } catch (err) {
      setAssistantProviderError(formatError(err))
    } finally {
      setAssistantProviderAction(null)
    }
  }

  async function connectAgentProvider(
    providerId: AgentProvider,
    authMode?: string | null,
  ) {
    setConnectingProviderId(providerId)
    setError(null)

    try {
      const response = await request<
        AgentProviderMetadata | AgentProviderAuthFlowResponse
      >('/agent-providers/connect', {
        method: 'POST',
        body: JSON.stringify({ provider_id: providerId, auth_mode: authMode ?? null }),
      })

      if (providerId === 'codex') {
        const flow = isCodexAuthFlowResponse(response)
          ? response
          : fallbackCodexAuthFlow(response)
        setForm((current) => ({ ...current, agent: 'codex' }))
        setCodexCloudNotice(flow.message)
        setCodexAuthFlow(flow)
        setCodexPendingAuth(true)
      } else {
        setCodexCloudNotice(null)
        setCodexAuthFlow(null)
        setCodexPendingAuth(false)
      }

      await loadAgentProviders()
    } catch (err) {
      setCodexPendingAuth(false)
      setError(formatError(err))
    } finally {
      setConnectingProviderId(null)
    }
  }

  async function disconnectAgentProvider(providerId: AgentProvider) {
    await request<AgentProviderMetadata>('/agent-providers/disconnect', {
      method: 'POST',
      body: JSON.stringify({ provider_id: providerId }),
    })
    if (providerId === 'codex') {
      setCodexCloudNotice(null)
      setCodexAuthFlow(null)
      setCodexPendingAuth(false)
    }
    await loadAgentProviders()
  }

  async function verifyCodexConnection(authMode?: string | null) {
    setConnectingProviderId('codex')
    setError(null)
    setNotice(null)

    try {
      const result = await request<AgentProviderVerificationResponse>(
        '/agent-providers/verify',
        {
          method: 'POST',
          body: JSON.stringify({
            provider_id: 'codex',
            auth_mode: authMode ?? codexAuthFlow?.metadata.auth_mode ?? 'chatgpt',
          }),
        },
      )
      setCodexPendingAuth(false)
      setCodexCloudNotice(result.message)
      setCodexAuthFlow(null)
      setNotice(result.message)
      await loadAgentProviders()
    } catch (err) {
      setCodexCloudNotice('Verification failed.')
      setError(formatError(err))
      await loadAgentProviders()
    } finally {
      setConnectingProviderId(null)
    }
  }

  async function copyCodexCode(code: string) {
    try {
      await navigator.clipboard.writeText(code)
      setCodexCopyStatus('copied')
    } catch {
      setCodexCopyStatus('failed')
    }
    window.setTimeout(() => setCodexCopyStatus(null), 1600)
  }

  async function loadTaskBundle(taskId: string): Promise<TaskSummary | null> {
    setError(null)

    try {
      const task = await request<TaskSummary>(`/tasks/${taskId}`)
      const [diffResult, statusResult, logsResult] = await Promise.allSettled([
        request<TaskDiffResponse>(`/tasks/${taskId}/diff`),
        request<WorkspaceStatusResponse>(`/tasks/${taskId}/workspace-status`),
        request<TaskLogsResponse>(`/tasks/${taskId}/logs`),
      ])

      setSelectedTask(task)
      setTaskDiff(diffResult.status === 'fulfilled' ? diffResult.value : null)
      setWorkspaceStatus(
        statusResult.status === 'fulfilled' ? statusResult.value : null,
      )
      setTaskLogs(
        logsResult.status === 'fulfilled' ? logsResult.value.events : [],
      )

      const failedResult = [diffResult, statusResult, logsResult].find(
        (result) => result.status === 'rejected',
      )

      if (failedResult?.status === 'rejected') {
        setError(formatError(failedResult.reason))
      }

      return task
    } catch (err) {
      setError(formatError(err))
      setSelectedTask(selectedTaskFromList)
      setTaskDiff(null)
      setWorkspaceStatus(null)
      setTaskLogs([])
      return null
    }
  }

  async function loadTaskDiff(taskId: string) {
    setError(null)

    try {
      setTaskDiff(await request<TaskDiffResponse>(`/tasks/${taskId}/diff`))
    } catch (err) {
      setError(formatError(err))
    }
  }

  async function loadTaskLogs(taskId: string, quiet = false) {
    if (!quiet) {
      setError(null)
    }

    try {
      const result = await request<TaskLogsResponse>(`/tasks/${taskId}/logs`)
      setTaskLogs(result.events)
      setLogsRefreshError(null)
    } catch (err) {
      setLogsRefreshError('Could not refresh logs.')
      if (!quiet) {
        setError(formatError(err))
      }
    }
  }

  async function copyTaskLogs() {
    const raw = rawExecutionLogs(taskLogs)
    const timeline = visibleTimelineLogs(taskLogs).map((event) => ({
      ...event,
      message: event.label,
    }))
    const content = (raw.length > 0 ? raw : timeline)
      .map(
        (event) =>
          `[${formatTimestamp(event.timestamp)}] ${event.level.toUpperCase()} ${event.message}`,
      )
      .join('\n')

    if (!content || !navigator.clipboard?.writeText) {
      setLogsCopyStatus('failed')
      return
    }

    try {
      await navigator.clipboard.writeText(content)
      setLogsCopyStatus('copied')
    } catch {
      setLogsCopyStatus('failed')
    }

    window.setTimeout(() => setLogsCopyStatus(null), 1600)
  }

  async function loadWorkspaceStatus(taskId: string) {
    setError(null)

    try {
      setWorkspaceStatus(
        await request<WorkspaceStatusResponse>(
          `/tasks/${taskId}/workspace-status`,
        ),
      )
    } catch (err) {
      setError(formatError(err))
    }
  }

  async function createTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setIsCreating(true)
    setNotice(null)
    setError(null)

    try {
      const created = await request<TaskCreateResponse>('/tasks', {
        method: 'POST',
        body: JSON.stringify({
          repo_url: form.repoUrl,
          base_branch: form.baseBranch,
          instruction: form.instruction,
          agent: 'codex',
          target_file: form.targetFile || null,
          change_scope: form.changeScope,
        }),
      })

      setNotice(created.message)
      setForm(initialForm)
      await loadTasks()
      setSelectedTaskId(created.task_id)
      await loadTaskBundle(created.task_id)
    } catch (err) {
      setError(formatError(err))
    } finally {
      setIsCreating(false)
    }
  }

  async function connectGithub() {
    setIsConnectingGithub(true)
    setNotice(null)
    setError(null)

    try {
      const result = await request<GitHubAuthStartResponse>(
        '/auth/github-app/start',
      )
      setGithubSessionId(result.session_id)
      window.open(result.auth_url, '_blank', 'noopener,noreferrer')
      setNotice('GitHub login opened. Return here after authorizing.')
      window.setTimeout(() => void loadGithubInstallation(result.session_id), 2500)
    } catch (err) {
      setError(formatError(err))
    } finally {
      setIsConnectingGithub(false)
    }
  }

  async function loadGithubInstallation(sessionId = githubSessionId) {
    if (!sessionId) return

    try {
      const installation = await request<GitHubInstallation>(
        '/auth/github-app/me',
        { headers: { 'X-AgentDock-Session': sessionId } },
      )
      setGithubInstallation(installation)
      await loadGithubRepos(sessionId)
    } catch {
      setGithubInstallation(null)
    }
  }

  async function loadGithubRepos(sessionId = githubSessionId) {
    if (!sessionId) return

    setIsLoadingGithubRepos(true)

    try {
      const result = await request<GitHubRepositoriesResponse>(
        '/auth/github-app/repos',
        { headers: { 'X-AgentDock-Session': sessionId } },
      )
      setGithubRepos(result.repositories)
    } catch (err) {
      setError(formatError(err))
    } finally {
      setIsLoadingGithubRepos(false)
    }
  }

  async function logoutGithub() {
    if (githubSessionId) {
      await request('/auth/logout', { method: 'POST' }).catch(() => undefined)
    }
    setGithubSessionId(null)
  }

  function selectGithubRepo(repo: GitHubRepository) {
    setForm((current) => ({
      ...current,
      repoUrl: repo.clone_url,
      baseBranch: repo.default_branch,
      targetFile: '',
    }))
    setRepoFiles([])
    setFilePreview(null)
    setPreviewCopyStatus(null)
    setNotice(`Selected ${repo.full_name}. Load files to continue.`)
  }

  async function loadRepositoryFiles() {
    if (!form.repoUrl.trim()) {
      setError('Enter a repository URL before loading files.')
      return
    }

    setIsLoadingFiles(true)
    setError(null)
    setFilePreview(null)
    setPreviewCopyStatus(null)

    try {
      const result = await request<RepositoryFilesResponse>('/repositories/files', {
        method: 'POST',
        body: JSON.stringify({
          repo_url: form.repoUrl,
          base_branch: form.baseBranch || 'main',
        }),
      })

      setRepoFiles(result.files)
    } catch (err) {
      setError(formatError(err))
    } finally {
      setIsLoadingFiles(false)
    }
  }

  async function selectTargetFile(path: string) {
    setForm((current) => ({ ...current, targetFile: path }))
    setIsLoadingPreview(true)
    setError(null)
    setPreviewCopyStatus(null)

    try {
      setFilePreview(
        await request<RepositoryFilePreviewResponse>(
          '/repositories/file-preview',
          {
            method: 'POST',
            body: JSON.stringify({
              repo_url: form.repoUrl,
              base_branch: form.baseBranch || 'main',
              path,
            }),
          },
        ),
      )
    } catch (err) {
      setFilePreview(null)
      setError(formatError(err))
    } finally {
      setIsLoadingPreview(false)
    }
  }

  async function copyFilePreview() {
    if (!filePreview || !navigator.clipboard?.writeText) {
      setPreviewCopyStatus('failed')
      window.setTimeout(() => setPreviewCopyStatus(null), 1600)
      return
    }

    try {
      await navigator.clipboard.writeText(filePreview.content)
      setPreviewCopyStatus('copied')
    } catch {
      setPreviewCopyStatus('failed')
    }

    window.setTimeout(() => setPreviewCopyStatus(null), 1600)
  }

  async function copyTaskDiff() {
    if (!taskDiff?.diff || !navigator.clipboard?.writeText) {
      setDiffCopyStatus('failed')
      window.setTimeout(() => setDiffCopyStatus(null), 1600)
      return
    }

    try {
      await navigator.clipboard.writeText(taskDiff.diff)
      setDiffCopyStatus('copied')
    } catch {
      setDiffCopyStatus('failed')
    }

    window.setTimeout(() => setDiffCopyStatus(null), 1600)
  }

  async function commitOrRejectTask(approve: boolean) {
    if (!selectedTaskId) return

    setNotice(null)
    setError(null)

    try {
      const result = await request<TaskApprovalResponse>(
        `/tasks/${selectedTaskId}/approval`,
        {
          method: 'POST',
          body: JSON.stringify({ approve }),
        },
      )

      setNotice(result.message)
      await loadTasks()
      await loadTaskBundle(selectedTaskId)
    } catch (err) {
      setError(formatError(err))
    }
  }

  async function pushTask() {
    if (!selectedTaskId) return

    setNotice(null)
    setError(null)

    try {
      const result = await request<TaskApprovalResponse>(
        `/tasks/${selectedTaskId}/push`,
        {
          method: 'POST',
        },
      )

      setNotice(result.message)
      await loadTasks()
      await loadTaskBundle(selectedTaskId)
    } catch (err) {
      setError(formatError(err))
      await loadTasks()
      await loadTaskBundle(selectedTaskId)
    }
  }

  async function createPullRequest() {
    if (!selectedTaskId) return

    setNotice(null)
    setError(null)

    try {
      const result = await request<TaskPullRequestResponse>(
        `/tasks/${selectedTaskId}/pull-request`,
        {
          method: 'POST',
        },
      )

      setNotice(result.message)
      await loadTasks()
      await loadTaskBundle(selectedTaskId)
    } catch (err) {
      setError(formatError(err))
      await loadTasks()
      await loadTaskBundle(selectedTaskId)
    }
  }

  async function mergePullRequest() {
    if (!selectedTaskId || !activeTask) return

    const shouldMerge = window.confirm(
      `Merge this pull request into ${activeTask.base_branch}?`,
    )

    if (!shouldMerge) return

    setNotice(null)
    setError(null)
    setMergeAttempt({
      taskId: selectedTaskId,
      status: 'loading',
      message: 'Merging pull request...',
    })

    try {
      await request<TaskMergePullRequestResponse>(
        `/tasks/${selectedTaskId}/merge-pull-request`,
        {
          method: 'POST',
        },
      )

      await loadTasks()
      const refreshedTask = await loadTaskBundle(selectedTaskId)

      if (refreshedTask?.status === 'merged') {
        setMergeAttempt({
          taskId: selectedTaskId,
          status: 'succeeded',
          message: 'Pull request merged',
        })
        return
      }

      const message =
        refreshedTask?.pr_state === 'open'
          ? 'Pull request is still open. Merge was not completed.'
          : 'Merge failed: backend did not report the task as merged.'
      setMergeAttempt({
        taskId: selectedTaskId,
        status: 'failed',
        message,
      })
      setError(message)
    } catch (err) {
      const reason = formatError(err)
      const message = `Merge failed: ${reason}`
      await loadTasks()
      await loadTaskBundle(selectedTaskId)
      setMergeAttempt({
        taskId: selectedTaskId,
        status: 'failed',
        message,
      })
      setError(message)
    }
  }

  async function deleteTask() {
    if (!selectedTaskId) return

    setNotice(null)
    setError(null)

    try {
      const result = await request<TaskDeleteResponse>(
        `/tasks/${selectedTaskId}`,
        {
          method: 'DELETE',
        },
      )

      setNotice(result.message)
      setSelectedTaskId(null)
      setSelectedTask(null)
      setTaskDiff(null)
      setWorkspaceStatus(null)
      setTaskLogs([])
      await loadTasks()
    } catch (err) {
      setError(formatError(err))
    }
  }

  function applyPromptTemplate(prompt: string) {
    setForm((current) => ({
      ...current,
      instruction: prompt,
    }))
  }

  function assistantProvider(): PromptAssistantProvider {
    return promptAssistantMode === 'manual' ? 'fallback' : promptAssistantMode
  }

  function assistantChangeScope() {
    switch (form.changeScope) {
      case 'only_selected_file':
        return 'single_file'
      case 'allow_related_files':
        return 'related_files'
      case 'whole_project':
        return 'whole_project'
    }
  }

  async function generateAssistantPrompt() {
    if (!assistantRoughIdea.trim()) return

    setIsGeneratingPrompt(true)
    setAssistantError(null)
    setAssistantResult(null)
    setPromptSentToCodexTask(false)

    try {
      const result = await request<PromptAssistantRefineResponse>(
        '/prompt-assistant/refine',
        {
          method: 'POST',
          body: JSON.stringify({
            provider: assistantProvider(),
            rough_idea: assistantRoughIdea.trim(),
            repo_name:
              selectedGithubRepo?.full_name ||
              (form.repoUrl ? shortRepoName(form.repoUrl) : undefined),
            target_file: form.targetFile || null,
            change_scope: assistantChangeScope(),
            existing_readme_excerpt:
              filePreview?.path.toLowerCase().includes('readme')
                ? filePreview.content
                : null,
          }),
        },
      )
      setAssistantResult(result)
      setAssistantRefinedPrompt(result.refined_prompt)
      setIsAssistantPreviewExpanded(false)
    } catch (err) {
      setAssistantError(formatError(err))
    } finally {
      setIsGeneratingPrompt(false)
    }
  }

  async function copyAssistantPrompt() {
    if (!assistantRefinedPrompt || !navigator.clipboard?.writeText) {
      setAssistantCopyStatus('failed')
      return
    }

    try {
      await navigator.clipboard.writeText(assistantRefinedPrompt)
      setAssistantCopyStatus('copied')
    } catch {
      setAssistantCopyStatus('failed')
    }

    setTimeout(() => setAssistantCopyStatus(null), 1600)
  }

  const pushedBranchName = activeTask?.working_branch ?? activeTask?.remote_branch?.replace(/^origin\//, '')
  const pushedBranchUrl =
    activeTask?.branch_url ??
    (activeTask && pushedBranchName
      ? buildGitHubBranchUrl(activeTask.repo_url, pushedBranchName)
      : null)
  const canCreatePullRequest =
    activeTask?.status === 'pushed' && Boolean(workspaceStatus?.pr_creation_enabled)
  const prCreationDisabled =
    activeTask?.status === 'pushed' && workspaceStatus?.pr_creation_enabled === false
  const activeMergeAttempt =
    mergeAttempt && activeTask?.task_id === mergeAttempt.taskId ? mergeAttempt : null
  const isMergingPullRequest = activeMergeAttempt?.status === 'loading'
  const showPrOpenPanel =
    activeTask?.status === 'pull_request_opened' &&
    activeMergeAttempt?.status !== 'loading' &&
    activeMergeAttempt?.status !== 'failed'

  return (
    <main className="app-shell">
      <motion.header
        className="app-header"
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45 }}
      >
        <div className="hero-actions">
          <button
            type="button"
            className="refresh-button"
            onClick={() => void loadTasks()}
          >
            <RefreshCw size={16} />
            Refresh
          </button>
        </div>
        <div className="hero-copy">
          <p className="eyebrow">
            <Bot size={15} />
            AgentDock
          </p>
          <h1>AgentDock</h1>
          <p className="subtitle">
            Mobile-first command center for AI coding agents
          </p>
          <p className="hero-description">
            Launch coding agents into safe branches, review diffs, approve
            commits, and optionally push or open PRs - all from a lightweight
            control layer.
          </p>
          <div className="hero-pills" aria-label="AgentDock capabilities">
            <span>
              <Terminal size={14} />
              Local-first
            </span>
            <span>
              <GitBranch size={14} />
              Safe branches
            </span>
            <span>
              <ShieldCheck size={14} />
              Human approval
            </span>
            <span>
              <Code2 size={14} />
              GitHub provider
            </span>
          </div>
        </div>
        <div className="hero-console" aria-hidden="true">
          <div className="console-bar">
            <span></span>
            <span></span>
            <span></span>
            <strong>agentdock/run</strong>
          </div>
          <div className="console-body">
            <p>
              <span>$</span> launch agent --branch safe
            </p>
            <p className="console-ok">workspace cloned</p>
            <p className="console-ok">diff ready for approval</p>
            <p className="console-muted">awaiting human review...</p>
          </div>
        </div>
      </motion.header>

      {(notice || apiConnectionError || error) && (
        <section className="messages" aria-live="polite">
          {notice && <p className="notice">{notice}</p>}
          {(apiConnectionError || error) && (
            <p className="error">{apiConnectionError ?? error}</p>
          )}
        </section>
      )}

      <section className="dashboard-grid">
        <aside className="task-dashboard glass-card" aria-label="Task dashboard">
          <div className="section-heading">
            <h2>
              <Activity size={17} />
              Task Dashboard
            </h2>
            <span>{isLoading ? 'Loading' : `${tasks.length} tasks`}</span>
          </div>

          <div className="task-list">
            {tasks.length === 0 && (
              <p className="empty-state">No tasks have been created yet.</p>
            )}

            {tasks.map((task) => (
              <button
                type="button"
                className={
                  task.task_id === activeTask?.task_id
                    ? 'task-row selected'
                    : 'task-row'
                }
                key={task.task_id}
                onClick={() => setSelectedTaskId(task.task_id)}
              >
                <span className="task-row-main">
                  <strong>{shortRepoName(task.repo_url)}</strong>
                  <small>{task.task_id}</small>
                </span>
                <span className={`status-pill ${task.status}`}>
                  {task.status.replaceAll('_', ' ')}
                </span>
              </button>
            ))}
          </div>
        </aside>

        <div className="workflow-column">
        <section className="github-connect glass-card" aria-labelledby="github-connect-title">
          <div>
            <strong id="github-connect-title">
              <GitBranch size={17} />
              GitHub connection
            </strong>
            <span>
              {isGithubConnected
                ? connectedGithubLogin
                  ? `Connected as ${connectedGithubLogin}`
                  : 'Connected. Load repositories to continue.'
                : 'Disconnected. Connect GitHub or use a manual repository URL in Codex Task.'}
            </span>
          </div>

          <div className="github-actions">
            <button
              type="button"
              className="mini-action"
              onClick={() =>
                isGithubConnected
                  ? void loadGithubRepos()
                  : void connectGithub()
              }
              disabled={isConnectingGithub || isLoadingGithubRepos}
            >
              {isGithubConnected
                ? isLoadingGithubRepos
                  ? 'Loading repos'
                  : githubRepos.length > 0
                    ? 'Refresh repos'
                    : 'Load repositories'
                : isConnectingGithub
                  ? 'Connecting'
                  : 'Connect GitHub'}
            </button>
            {isGithubConnected && (
              <button
                type="button"
                className="mini-action secondary"
                onClick={() => void logoutGithub()}
              >
                Logout
              </button>
            )}
          </div>

          {githubRepos.length > 0 && (
            <div className="github-repo-picker">
              <input
                value={githubRepoFilter}
                onChange={(event) => setGithubRepoFilter(event.target.value)}
                placeholder="Filter GitHub repositories"
              />
              <div className="github-repos">
                {filteredGithubRepos.map((repo) => (
                  <button
                    type="button"
                    key={repo.id}
                    className={
                      form.repoUrl === repo.clone_url
                        ? 'github-repo selected'
                        : 'github-repo'
                    }
                    onClick={() => selectGithubRepo(repo)}
                  >
                    <span>{repo.full_name}</span>
                    <small>
                      {repo.private ? 'private' : 'public'} ·{' '}
                      {repo.default_branch}
                      {' · '}
                      {repo.can_push ? 'push' : 'read'}
                    </small>
                  </button>
                ))}
                {filteredGithubRepos.length === 0 && (
                  <p className="empty-state">No repositories match.</p>
                )}
              </div>
            </div>
          )}
        </section>

        <section className="github-connect glass-card" aria-labelledby="codex-connect-title">
          <div>
            <strong id="codex-connect-title">
              <Terminal size={17} />
              OpenAI Codex
            </strong>
            <span>
              Codex executes repository changes. AgentDock uses OpenAI Codex to prepare code changes.
            </span>
          </div>

          <div className="github-actions">
            <small className={`provider-status-badge ${codexPrimaryStatus}`}>
              {codexConnectionLabel}
            </small>
            {isCodexActive ? (
              <>
                <button
                  type="button"
                  className="mini-action secondary"
                  disabled={connectingProviderId === 'codex'}
                  onClick={() => void verifyCodexConnection(codexProvider?.auth_mode)}
                >
                  Check connection
                </button>
                <button
                  type="button"
                  className="mini-action"
                  onClick={() => void disconnectAgentProvider('codex')}
                >
                  Disconnect
                </button>
              </>
            ) : (
              <button
                type="button"
                className="mini-action"
                disabled={!canConnectCodex || connectingProviderId === 'codex'}
                onClick={() => void connectAgentProvider('codex', 'chatgpt')}
              >
                Sign in with ChatGPT
              </button>
            )}
          </div>

          {codexCloudNotice && !isCodexActive && (
            <div className="codex-auth-panel">
              <strong>OpenAI account connection</strong>
              <p>{codexCloudNotice}</p>
              {codexAuthFlow?.verification_url && (
                <a
                  href={codexAuthFlow.verification_url}
                  target="_blank"
                  rel="noreferrer"
                  className="mini-action"
                >
                  Open verification URL
                </a>
              )}
              {(codexAuthFlow?.user_code || codexAuthFlow?.device_code) && (
                <button
                  type="button"
                  className="mini-action secondary"
                  onClick={() =>
                    void copyCodexCode(
                      codexAuthFlow.user_code ?? codexAuthFlow.device_code ?? '',
                    )
                  }
                >
                  {codexCopyStatus === 'copied' ? 'Copied' : 'Copy code'}
                </button>
              )}
              {(codexAuthFlow?.user_code || codexAuthFlow?.device_code) && (
                <code>{codexAuthFlow.user_code ?? codexAuthFlow.device_code}</code>
              )}
              <button
                type="button"
                className="mini-action"
                disabled={connectingProviderId === 'codex'}
                onClick={() => void verifyCodexConnection('chatgpt')}
              >
                Check connection
              </button>
            </div>
          )}

          {!hasConnectedProvider && (
            <p className="provider-mode-note">Connect Codex before launching tasks.</p>
          )}
        </section>

        <section className="task-create glass-card" aria-labelledby="repository-explorer-title">
          <div className="launcher-heading">
            <div>
              <h2 id="repository-explorer-title">
                <FileText size={20} />
                Repository Explorer
              </h2>
              <p>
                Select a repository, load files, choose a target file, and preview content before writing the prompt.
              </p>
            </div>
          </div>

          <div className="selected-repo-card">
            <strong>Repository target</strong>
            <small>
              {selectedGithubRepo
                ? selectedGithubRepo.full_name
                : form.repoUrl || 'No repository selected'}
            </small>
            {selectedGithubRepo && (
              <div className="badge-row">
                <span className="meta-badge">
                  {selectedGithubRepo.private ? 'Private' : 'Public'}
                </span>
                <span className="meta-badge">
                  {selectedGithubRepo.default_branch}
                </span>
                <span className={selectedGithubRepo.can_push ? 'meta-badge ok' : 'meta-badge'}>
                  {selectedGithubRepo.can_push ? 'Read/write' : 'Read only'}
                </span>
              </div>
            )}
          </div>

          {githubRepos.length > 0 && (
            <details className="repo-select-panel" open={!selectedGithubRepo}>
              <summary>Choose from connected repositories</summary>
              <div className="github-repo-picker">
                <input
                  value={githubRepoFilter}
                  onChange={(event) => setGithubRepoFilter(event.target.value)}
                  placeholder="Filter GitHub repositories"
                />
                <div className="github-repos">
                  {filteredGithubRepos.map((repo) => (
                    <button
                      type="button"
                      key={repo.id}
                      className={
                        form.repoUrl === repo.clone_url
                          ? 'github-repo selected'
                          : 'github-repo'
                      }
                      onClick={() => selectGithubRepo(repo)}
                    >
                      <span>{repo.full_name}</span>
                      <small>
                        {repo.private ? 'private' : 'public'} ·{' '}
                        {repo.default_branch}
                        {' · '}
                        {repo.can_push ? 'push' : 'read'}
                      </small>
                    </button>
                  ))}
                  {filteredGithubRepos.length === 0 && (
                    <p className="empty-state">No repositories match.</p>
                  )}
                </div>
              </div>
            </details>
          )}

          <details className="manual-repo-panel">
            <summary>Advanced: use manual repository URL</summary>
            <div className="form-pair">
              <label>
                Repository URL
                <input
                  type="url"
                  placeholder="https://github.com/org/repo"
                  value={form.repoUrl}
                  onChange={(event) =>
                    setForm({ ...form, repoUrl: event.target.value })
                  }
                />
              </label>
              <label>
                Base Branch
                <input
                  value={form.baseBranch}
                  onChange={(event) =>
                    setForm({ ...form, baseBranch: event.target.value })
                  }
                />
              </label>
            </div>
          </details>

          {form.repoUrl ? (
            <div className="repository-explorer">
              <div className="launcher-step">
                <span>1</span>
                <div>
                  <strong>Target file</strong>
                  <small>
                    {form.targetFile ||
                      (form.changeScope === 'whole_project'
                        ? 'Optional context for whole-project tasks'
                        : 'Select a target file')}
                  </small>
                </div>
                <button
                  type="button"
                  className="mini-action"
                  disabled={isLoadingFiles || !form.repoUrl}
                  onClick={() => void loadRepositoryFiles()}
                >
                  {isLoadingFiles ? 'Loading files' : 'Load files'}
                </button>
              </div>

              {repoFiles.length > 0 ? (
                <div className="explorer-grid">
                  <div className="explorer-list">
                    <input
                      value={fileFilter}
                      onChange={(event) => setFileFilter(event.target.value)}
                      placeholder="Search files by path"
                    />
                    <div className="file-list">
                      {filteredRepoFiles.map((entry) => (
                        <button
                          type="button"
                          key={entry.path}
                          className={
                            form.targetFile === entry.path
                              ? 'file-option selected'
                              : 'file-option'
                          }
                          onClick={() => void selectTargetFile(entry.path)}
                        >
                          <span>{entry.path}</span>
                          <small>
                            {entry.extension ?? 'file'}
                            {typeof entry.size === 'number'
                              ? ` · ${entry.size} bytes`
                              : ''}
                          </small>
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="explorer-preview">
                    {isLoadingPreview && <p className="empty-state">Loading preview.</p>}
                    {filePreview ? (
                      <>
                        <div className="file-preview-header">
                          <div className="file-preview-title">
                            <div>
                              <strong>{filePreview.path}</strong>
                              <small>
                                {formatBytes(filePreview.size)}
                                {filePreview.truncated ? ' · Preview truncated' : ''}
                              </small>
                            </div>
                            <button
                              type="button"
                              className="preview-copy-button"
                              onClick={() => void copyFilePreview()}
                            >
                              Copy
                            </button>
                          </div>
                          {previewCopyStatus && (
                            <small className="preview-copy-status">
                              {previewCopyStatus === 'copied'
                                ? 'Copied'
                                : 'Copy failed'}
                            </small>
                          )}
                        </div>
                        <pre className="file-preview">{filePreview.content}</pre>
                      </>
                    ) : (
                      <p className="empty-state">A read-only preview appears here after selecting a file.</p>
                    )}
                  </div>
                </div>
              ) : (
                <p className="empty-state">Load files to browse and choose target-file context.</p>
              )}
            </div>
          ) : (
            <p className="empty-state">Select a GitHub repository or enter a manual repository URL to browse files.</p>
          )}

          <div className="scope-options">
            {scopeOptions.map((option) => (
              <label
                key={option.value}
                className={[
                  'scope-option',
                  form.changeScope === option.value ? 'selected' : '',
                  option.broad ? 'broad' : '',
                ].filter(Boolean).join(' ')}
              >
                <input
                  type="radio"
                  name="changeScope"
                  checked={form.changeScope === option.value}
                  onChange={() =>
                    setForm({ ...form, changeScope: option.value })
                  }
                />
                <span>{option.label}</span>
                <small>{option.description}</small>
                {option.broad && (
                  <em>
                    Broader repository access. More files may appear in diff review.
                  </em>
                )}
              </label>
            ))}
          </div>
        </section>

        <section className="task-create prompt-launcher glass-card" aria-labelledby="create-task-title">
          <div className="launcher-heading">
            <div>
              <h2 id="create-task-title">
                <Rocket size={20} />
                Prompt Assistant and Codex Task
              </h2>
              <p>
                Plan the prompt first, then launch Codex execution when the task is ready.
              </p>
            </div>
          </div>

          <form onSubmit={(event) => void createTask(event)}>
            <div className="launcher-panel prompt-assistant" aria-labelledby="prompt-assistant-title">
                <div className="prompt-assistant-header">
                  <div>
                    <h3 id="prompt-assistant-title">
                      <Bot size={16} />
                      Prompt Assistant
                    </h3>
                    <p>
                      The assistant helps plan the task. Codex executes the repository changes.
                    </p>
                  </div>
                </div>

                <div className="assistant-mode-grid" role="radiogroup" aria-label="Prompt assistant mode">
                  {promptAssistantModes.map((mode) => (
                    <label
                      key={mode.value}
                      className={[
                        'assistant-mode-option',
                        promptAssistantMode === mode.value ? 'selected' : '',
                      ].filter(Boolean).join(' ')}
                    >
                      <input
                        type="radio"
                        name="promptAssistantMode"
                        checked={promptAssistantMode === mode.value}
                        onChange={() => {
                          setPromptAssistantMode(mode.value)
                          setAssistantError(null)
                          setAssistantResult(null)
                          setAssistantProviderError(null)
                          setAssistantProviderNotice(null)
                        }}
                      />
                      <span>{mode.label}</span>
                    </label>
                  ))}
                </div>

                {promptAssistantMode === 'manual' ? (
                  <p className="assistant-mode-note">
                    Manual mode uses a local prompt template only. Connect OpenAI for AI-assisted prompt generation.
                  </p>
                ) : (
                  <div className="assistant-provider-card">
                    <div>
                      <strong>
                        {activePromptAssistantProvider?.label ??
                          'ChatGPT / OpenAI'}
                      </strong>
                      <span>
                        Status:{' '}
                        {activePromptAssistantProvider?.connected
                          ? 'Connected'
                          : 'Not connected'}
                      </span>
                      {activePromptAssistantProvider?.last_verified_at && (
                        <small>
                          Last verified{' '}
                          {formatTimestamp(activePromptAssistantProvider.last_verified_at)}
                        </small>
                      )}
                    </div>
                    <div className="provider-card-actions">
                      <button
                        type="button"
                        className="mini-action secondary"
                        onClick={() => {
                          setAssistantApiKeyProvider(activePromptAssistantProviderId)
                          setAssistantApiKey('')
                          setAssistantProviderError(null)
                          setAssistantProviderNotice(null)
                        }}
                      >
                        {activePromptAssistantProvider?.connected
                          ? 'Replace API key'
                          : 'Connect API key'}
                      </button>
                      <button
                        type="button"
                        className="mini-action secondary"
                        disabled={
                          !activePromptAssistantProvider?.connected ||
                          assistantProviderAction === activePromptAssistantProviderId
                        }
                        onClick={() =>
                          activePromptAssistantProviderId &&
                          void testPromptAssistantProvider(activePromptAssistantProviderId)
                        }
                      >
                        Test connection
                      </button>
                      {activePromptAssistantProvider?.connected && (
                        <button
                          type="button"
                          className="mini-action"
                          disabled={assistantProviderAction === activePromptAssistantProviderId}
                          onClick={() =>
                            activePromptAssistantProviderId &&
                            void disconnectPromptAssistantProvider(
                              activePromptAssistantProviderId,
                            )
                          }
                        >
                          Disconnect
                        </button>
                      )}
                    </div>
                    {assistantApiKeyProvider === activePromptAssistantProviderId && (
                      <div className="assistant-key-panel">
                        <label>
                          API key
                          <input
                            type="password"
                            value={assistantApiKey}
                            onChange={(event) => setAssistantApiKey(event.target.value)}
                            autoComplete="off"
                            spellCheck={false}
                            placeholder="sk-..."
                          />
                        </label>
                        <div className="provider-card-actions">
                          <button
                            type="button"
                            className="mini-action secondary"
                            onClick={() => {
                              setAssistantApiKey('')
                              setAssistantApiKeyProvider(null)
                            }}
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            className="mini-action"
                            disabled={
                              !assistantApiKey.trim() ||
                              assistantProviderAction === activePromptAssistantProviderId
                            }
                            onClick={() =>
                              activePromptAssistantProviderId &&
                              void savePromptAssistantKey(activePromptAssistantProviderId)
                            }
                          >
                            Save key
                          </button>
                        </div>
                      </div>
                    )}
                    {assistantProviderNotice && (
                      <small>{assistantProviderNotice}</small>
                    )}
                    {(assistantProviderError || activePromptAssistantProvider?.error) && (
                      <small className="assistant-error">
                        {assistantProviderError ?? activePromptAssistantProvider?.error}
                      </small>
                    )}
                    {assistantResult?.used_fallback && (
                      <small>
                        Provider is not connected or failed, so AgentDock used the fallback prompt template.
                      </small>
                    )}
                    <small>
                      API keys are stored server-side for Prompt Assistant only.
                    </small>
                  </div>
                )}

                <div className="assistant-draft-grid">
                  <label>
                    Rough idea
                    <textarea
                      rows={4}
                      value={assistantRoughIdea}
                      onChange={(event) => {
                        setAssistantRoughIdea(event.target.value)
                        setPromptSentToCodexTask(false)
                      }}
                      placeholder="Describe what you want Codex to do in plain language."
                    />
                  </label>
                </div>
                {promptSentToCodexTask ? (
                  <div className="assistant-preview-card">
                    <div className="assistant-preview-header">
                      <strong>Prompt sent to Codex Task.</strong>
                    </div>
                    <p className="assistant-preview-text">
                      Review or edit the final prompt below before launching Codex.
                    </p>
                  </div>
                ) : (
                  <div className="assistant-preview-card">
                    <div className="assistant-preview-header">
                      <strong>Generated prompt</strong>
                      <button
                        type="button"
                        className="mini-action secondary"
                        disabled={!assistantRefinedPrompt.trim()}
                        onClick={() =>
                          setIsAssistantPreviewExpanded((current) => !current)
                        }
                      >
                        {isAssistantPreviewExpanded ? 'Collapse' : 'Expand'}
                      </button>
                    </div>
                    <p
                      className={[
                        'assistant-preview-text',
                        isAssistantPreviewExpanded ? 'expanded' : '',
                      ].filter(Boolean).join(' ')}
                    >
                      {assistantRefinedPrompt ||
                        'Generate a prompt to preview it here.'}
                    </p>
                  </div>
                )}

                <div className="assistant-actions">
                  <button
                    type="button"
                    className="mini-action"
                    disabled={!assistantRoughIdea.trim() || isGeneratingPrompt}
                    onClick={() => void generateAssistantPrompt()}
                  >
                    {isGeneratingPrompt ? 'Generating' : 'Generate Prompt'}
                  </button>
                  <button
                    type="button"
                    className="mini-action secondary"
                    disabled={!assistantRefinedPrompt.trim()}
                    onClick={() => void copyAssistantPrompt()}
                  >
                    {assistantCopyStatus === 'copied' ? 'Copied' : 'Copy Prompt'}
                  </button>
                  <button
                    type="button"
                    className="mini-action secondary"
                    disabled={!assistantRefinedPrompt.trim()}
                    onClick={() => {
                      setForm((current) => ({
                        ...current,
                        instruction: assistantRefinedPrompt,
                      }))
                      setIsAssistantPreviewExpanded(false)
                      setPromptSentToCodexTask(true)
                    }}
                  >
                    Use Prompt
                  </button>
                </div>
                {assistantError && <p className="assistant-error">{assistantError}</p>}
                {assistantResult?.used_fallback && (
                  <p className="assistant-mode-note">
                    Fallback prompt template used. You can still edit the prompt before launching Codex.
                  </p>
                )}
            </div>

            <div className="launcher-panel codex-task-section">
              <div className="launcher-step">
                <span>3</span>
                <div>
                  <strong>Codex Task</strong>
                  <small>Execution</small>
                </div>
              </div>
              <p className="provider-mode-note">
                Codex will inspect the repository and execute the requested changes.
              </p>

              <label className="prompt-field">
                Final editable Codex prompt
                <textarea
                  required
                  minLength={10}
                  rows={hasCodexPrompt ? 6 : 4}
                  placeholder={`Describe the task. Examples:\n- ${promptPlaceholderExamples.join('\n- ')}`}
                  value={form.instruction}
                  onChange={(event) =>
                    setForm({ ...form, instruction: event.target.value })
                  }
                />
                <span className="character-count">
                  {form.instruction.length} characters
                </span>
              </label>
              {hasCodexPrompt && (
                <p className="provider-mode-note">
                  Review or edit the final prompt below before launching Codex.
                </p>
              )}

              {hasCodexPrompt ? (
                <>
                  <div className="template-strip" aria-label="Quick prompt templates">
                    {promptTemplates.map((template) => (
                      <button
                        type="button"
                        className="template-button"
                        key={template.label}
                        onClick={() => applyPromptTemplate(
                          form.targetFile
                            ? `${template.prompt}\n\nFocus on ${form.targetFile}.`
                            : template.prompt,
                        )}
                      >
                        {template.label}
                      </button>
                    ))}
                  </div>

                </>
              ) : (
                <p className="assistant-mode-note">
                  Use a generated prompt or type a prompt above to reveal execution settings.
                </p>
              )}
            </div>

            {hasCodexPrompt && (
              <>
                <div className="safety-note">
                  Safety: AgentDock creates an isolated workspace and branch.
                  Nothing is pushed or opened as a PR unless explicitly enabled.
                </div>

                <button type="submit" disabled={isCreating}>
                  <Rocket size={17} />
                  {isCreating ? 'Launching Codex Task' : 'Launch Codex Task'}
                </button>
              </>
            )}
          </form>
        </section>
        </div>
      </section>

      <section className="diff-viewer glass-card primary-review" aria-labelledby="diff-viewer-title">
        <div className="section-heading">
          <h2 id="diff-viewer-title">
            <FileText size={17} />
            Review Diff
          </h2>
          <div className="section-actions">
            <span>{taskDiff?.changed_files.length ?? 0} changed files</span>
            {taskDiff?.diff && (
              <button
                type="button"
                className="mini-action"
                onClick={() => void copyTaskDiff()}
              >
                Copy diff
              </button>
            )}
            {diffCopyStatus && (
              <small className="preview-copy-status">
                {diffCopyStatus === 'copied' ? 'Copied' : 'Copy failed'}
              </small>
            )}
            {activeTask && (
              <button
                type="button"
                className="mini-action"
                onClick={() => void loadTaskDiff(activeTask.task_id)}
              >
                Refresh diff
              </button>
            )}
          </div>
        </div>

        {taskDiff?.changed_files.length ? (
          <ul className="changed-files changed-files-summary">
            {taskDiff.changed_files.map((file) => (
              <li key={`${file.status}-${file.path}`}>
                <span>
                  {file.path}
                  {activeTask?.target_file === file.path ? (
                    <strong className="target-match">Target</strong>
                  ) : activeTask?.target_file ? (
                    <strong className="outside-target">Outside target</strong>
                  ) : null}
                </span>
                <small>
                  {file.status} +{file.additions} -{file.deletions}
                </small>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty-state">No changed files to show.</p>
        )}
        {showScopeWarning && (
          <p className="scope-warning">
            This task was scoped to the selected file, but the diff includes{' '}
            {activeTaskOutsideTargetFiles.length} outside file
            {activeTaskOutsideTargetFiles.length === 1 ? '' : 's'}.
          </p>
        )}

        <pre>{taskDiff?.diff || 'No diff loaded.'}</pre>
      </section>

      <section className="detail-grid result-grid">
        <section className="task-details glass-card" aria-labelledby="task-details-title">
          <div className="section-heading">
            <h2 id="task-details-title">
              <Code2 size={17} />
              Selected Task
            </h2>
            {activeTask && (
              <span className={`status-pill ${activeTask.status}`}>
                {activeTask.status.replaceAll('_', ' ')}
              </span>
            )}
          </div>

          {!activeTask && <p className="empty-state">Select a task to inspect.</p>}

          {activeTask && (
            <>
              <dl className="metadata-grid task-summary-grid">
                <div>
                  <dt>Task</dt>
                  <dd>{activeTask.task_id}</dd>
                </div>
                <div>
                  <dt>Repository</dt>
                  <dd>{shortRepoName(activeTask.repo_url)}</dd>
                </div>
                <div>
                  <dt>Status</dt>
                  <dd>{activeTask.status.replaceAll('_', ' ')}</dd>
                </div>
                <div>
                  <dt>Target File</dt>
                  <dd>{activeTask.target_file ?? 'None'}</dd>
                </div>
                <div>
                  <dt>Agent</dt>
                  <dd>{activeAgentLabel}</dd>
                </div>
              </dl>

              <div className="instruction-block">
                <h3>Prompt</h3>
                <p>{activeTask.instruction}</p>
              </div>

              <div className="actions">
                {activeTask.status === 'waiting_for_approval' && (
                  <>
                    <button
                      type="button"
                      onClick={() => void commitOrRejectTask(true)}
                    >
                      <CheckCircle2 size={16} />
                      Commit
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => void commitOrRejectTask(false)}
                    >
                      <XCircle size={16} />
                      Reject
                    </button>
                  </>
                )}
                {activeTask.status === 'committed' &&
                  workspaceStatus?.push_enabled &&
                  workspaceStatus.has_valid_origin && (
                    <button
                      type="button"
                      onClick={() => void pushTask()}
                    >
                      <UploadCloud size={16} />
                      Push
                    </button>
                  )}
                {activeTask.status === 'pushed' && pushedBranchUrl && (
                  <a
                    className="action-link secondary"
                    href={pushedBranchUrl}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <ExternalLink size={16} />
                    Open Branch
                  </a>
                )}
                {canCreatePullRequest && (
                  <button
                    type="button"
                    onClick={() => void createPullRequest()}
                  >
                    <GitPullRequest size={16} />
                    Create Pull Request
                  </button>
                )}
                {activeTask.status === 'pull_request_opened' && activeTask.pr_url && (
                  <>
                    <a
                      className="action-link secondary"
                      href={activeTask.pr_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <ExternalLink size={16} />
                      Open Pull Request
                    </a>
                    <button
                      type="button"
                      disabled={isMergingPullRequest}
                      onClick={() => void mergePullRequest()}
                    >
                      <GitPullRequest size={16} />
                      {isMergingPullRequest ? 'Merging pull request...' : 'Merge Pull Request'}
                    </button>
                  </>
                )}
                {activeTask.status === 'merged' && (
                  <>
                    {activeTask.pr_url && (
                      <a
                        className="action-link secondary"
                        href={activeTask.pr_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <ExternalLink size={16} />
                        Open Pull Request
                      </a>
                    )}
                    <a
                      className="action-link secondary"
                      href={activeTask.repo_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <ExternalLink size={16} />
                      Open Repository
                    </a>
                  </>
                )}
                <button
                  type="button"
                  className="secondary danger"
                  onClick={() => void deleteTask()}
                >
                  <Trash2 size={16} />
                  Delete
                </button>
              </div>
              {activeTask.status === 'committed' && (
                <p className="empty-state">
                  {workspaceStatus?.push_enabled
                    ? workspaceStatus.has_valid_origin
                      ? `Committed ${shortSha(activeTask.commit_sha)}. Push when ready.`
                      : 'Committed locally. Add or restore an origin remote before pushing.'
                    : 'Committed locally. Push is disabled because ALLOW_PUSH=false.'}
                </p>
              )}
              {activeTask.status === 'pushed' && (
                <div className="push-result-panel">
                  <p className="console-ok">
                    Pushed {pushedBranchName ?? activeTask.remote_branch ?? 'remote branch'}
                    {activeTask.pushed_at ? ` at ${formatTimestamp(activeTask.pushed_at)}` : ''}.
                  </p>
                  {prCreationDisabled && (
                    <p className="empty-state">
                      Pull request creation is disabled because ALLOW_PR_CREATION=false.
                    </p>
                  )}
                  {activeTask.error && (
                    <p className="error">{activeTask.error}</p>
                  )}
                </div>
              )}
              {activeMergeAttempt?.status === 'loading' && (
                <div className="push-result-panel merge-state-panel">
                  <h3>Merging pull request...</h3>
                </div>
              )}
              {activeMergeAttempt?.status === 'failed' && (
                <div className="push-result-panel merge-state-panel merge-state-panel-error">
                  <h3>{activeMergeAttempt.message}</h3>
                  {activeTask.error && activeTask.error !== activeMergeAttempt.message && (
                    <p className="error">{activeTask.error}</p>
                  )}
                </div>
              )}
              {showPrOpenPanel && (
                <div className="push-result-panel">
                  <p className="empty-state">
                    Pull request #{activeTask.pr_number ?? 'opened'}
                    {activeTask.pr_state ? ` is ${activeTask.pr_state}` : ''}.
                  </p>
                  {pushedBranchUrl && (
                    <a
                      className="text-link"
                      href={pushedBranchUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      View pushed branch
                    </a>
                  )}
                </div>
              )}
              {activeTask.status === 'merged' && (
                <div className="push-result-panel merge-state-panel">
                  <h3>Pull request merged</h3>
                  {activeTask.merge_commit_sha && (
                    <p className="empty-state">
                      Merge commit {shortSha(activeTask.merge_commit_sha)}.
                    </p>
                  )}
                  {activeTask.merged_at && (
                    <p className="empty-state">
                      Merged at {formatTimestamp(activeTask.merged_at)}.
                    </p>
                  )}
                </div>
              )}
            </>
          )}
        </section>

        <details className="advanced-details glass-card">
          <summary>
            <span>
              <Terminal size={17} />
              Advanced details
            </span>
            <small>Logs, workspace, and git metadata</small>
          </summary>

          <div className="advanced-grid">
            <section aria-labelledby="task-metadata-title">
              <div className="section-heading compact-heading">
                <h3 id="task-metadata-title">
                  <Code2 size={16} />
                  Task metadata
                </h3>
              </div>

              {activeTask ? (
                <dl className="metadata-grid">
                  <div>
                    <dt>Working branch</dt>
                    <dd>{activeTask.working_branch ?? 'Not assigned'}</dd>
                  </div>
                  <div>
                    <dt>Commit</dt>
                    <dd className="metric-value">{shortSha(activeTask.commit_sha)}</dd>
                  </div>
                  <div>
                    <dt>Change scope</dt>
                    <dd>{(activeTask.change_scope ?? 'only_selected_file').replaceAll('_', ' ')}</dd>
                  </div>
                  <div>
                    <dt>Pushed</dt>
                    <dd>{activeTask.pushed ? 'Yes' : 'No'}</dd>
                  </div>
                  <div>
                    <dt>Branch URL</dt>
                    <dd>
                      {activeTask.branch_url ? (
                        <a href={activeTask.branch_url} target="_blank" rel="noreferrer">
                          {activeTask.branch_url}
                        </a>
                      ) : (
                        'None'
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>PR URL</dt>
                    <dd>{activeTask.pr_url ?? 'None'}</dd>
                  </div>
                  <div>
                    <dt>PR number</dt>
                    <dd>{activeTask.pr_number ? `#${activeTask.pr_number}` : 'None'}</dd>
                  </div>
                  <div>
                    <dt>PR state</dt>
                    <dd>{activeTask.pr_state ?? 'None'}</dd>
                  </div>
                </dl>
              ) : (
                <p className="empty-state">Select a task to inspect metadata.</p>
              )}
            </section>

            <section aria-labelledby="workspace-status-title">
              <div className="section-heading compact-heading">
                <h3 id="workspace-status-title">
                  <GitBranch size={16} />
                  Workspace
                </h3>
                {activeTask && (
                  <button
                    type="button"
                    className="mini-action"
                    onClick={() => void loadWorkspaceStatus(activeTask.task_id)}
                  >
                    Refresh
                  </button>
                )}
              </div>

              {!workspaceStatus && (
                <p className="empty-state">Workspace status is not available.</p>
              )}

              {workspaceStatus && (
                <dl className="metadata-grid">
                  <div>
                    <dt>Branch</dt>
                    <dd>{workspaceStatus.branch ?? 'Detached'}</dd>
                  </div>
                  <div>
                    <dt>Head</dt>
                    <dd className="metric-value">{shortSha(workspaceStatus.head_commit)}</dd>
                  </div>
                  <div>
                    <dt>Origin</dt>
                    <dd>{workspaceStatus.has_valid_origin ? 'Valid' : 'Missing'}</dd>
                  </div>
                  <div>
                    <dt>Remote</dt>
                    <dd>{workspaceStatus.remote_url ?? 'None'}</dd>
                  </div>
                  <div>
                    <dt>Default</dt>
                    <dd>{workspaceStatus.default_branch ?? 'Unknown'}</dd>
                  </div>
                  <div>
                    <dt>Push</dt>
                    <dd>{workspaceStatus.push_enabled ? 'Enabled' : 'Disabled'}</dd>
                  </div>
                  <div>
                    <dt>Pushed</dt>
                    <dd>{workspaceStatus.pushed ? 'Yes' : 'No'}</dd>
                  </div>
                  <div>
                    <dt>Remote branch</dt>
                    <dd>{workspaceStatus.remote_branch ?? 'None'}</dd>
                  </div>
                  <div>
                    <dt>Pushed at</dt>
                    <dd>{workspaceStatus.pushed_at ? formatTimestamp(workspaceStatus.pushed_at) : 'None'}</dd>
                  </div>
                  <div>
                    <dt>Last push</dt>
                    <dd>{workspaceStatus.last_push_result ?? 'None'}</dd>
                  </div>
                  <div>
                    <dt>PRs</dt>
                    <dd>{workspaceStatus.pr_creation_enabled ? 'Enabled' : 'Disabled'}</dd>
                  </div>
                  <div>
                    <dt>PR number</dt>
                    <dd>{workspaceStatus.pr_number ? `#${workspaceStatus.pr_number}` : 'None'}</dd>
                  </div>
                  <div>
                    <dt>PR state</dt>
                    <dd>{workspaceStatus.pr_state ?? 'None'}</dd>
                  </div>
                  <div>
                    <dt>Dirty</dt>
                    <dd>{workspaceStatus.is_dirty ? 'Yes' : 'No'}</dd>
                  </div>
                  <div>
                    <dt>Untracked</dt>
                    <dd>{workspaceStatus.has_untracked_files ? 'Yes' : 'No'}</dd>
                  </div>
                </dl>
              )}
            </section>

            <section aria-labelledby="agent-log-title">
              <div className="section-heading compact-heading">
                <h3 id="agent-log-title">
                  <Terminal size={16} />
                  Activity timeline
                </h3>
                <div className="section-actions">
                  {isPollingLogs && (
                    <span className="live-indicator">
                      <span />
                      Live
                    </span>
                  )}
                  <span>{timelineLogs.length} events</span>
                  {activeTask && (
                    <button
                      type="button"
                      className="mini-action"
                      onClick={() => void loadTaskLogs(activeTask.task_id)}
                    >
                      Refresh logs
                    </button>
                  )}
                  <button
                    type="button"
                    className="mini-action secondary"
                    disabled={rawLogs.length === 0 && timelineLogs.length === 0}
                    onClick={() => void copyTaskLogs()}
                  >
                    {logsCopyStatus === 'copied' ? 'Copied' : 'Copy logs'}
                  </button>
                </div>
              </div>

              {logsRefreshError && <p className="assistant-error">{logsRefreshError}</p>}
              {timelineLogs.length === 0 ? (
                <p className="empty-state">No task events to show.</p>
              ) : (
                <ol className="log-list">
                  {timelineLogs.map((event) => (
                    <li key={`${event.timestamp}-${event.label}`}>
                      <time>{formatTimestamp(event.timestamp)}</time>
                      <span className={`log-level ${event.level}`}>
                        {event.level}
                      </span>
                      <p>{event.label}</p>
                    </li>
                  ))}
                </ol>
              )}
              {activeTask && !isTaskActive && taskLogs.length > 0 && (
                <p className="provider-mode-note">
                  Polling stopped. Last activity remains visible.
                </p>
              )}
              <details className="raw-execution-logs">
                <summary>
                  <span>Raw execution logs</span>
                  <small>{rawLogs.length} entries</small>
                </summary>
                {rawLogs.length === 0 ? (
                  <p className="empty-state">No raw execution output to show.</p>
                ) : (
                  <ol className="log-list raw-log-list">
                    {rawLogs.map((event) => (
                      <li key={`${event.timestamp}-${event.message}`}>
                        <time>{formatTimestamp(event.timestamp)}</time>
                        <span className={`log-level ${event.level}`}>
                          {event.level}
                        </span>
                        <p>{event.message}</p>
                      </li>
                    ))}
                  </ol>
                )}
              </details>
            </section>
          </div>
        </details>
      </section>
    </main>
  )
}

function shortSha(value: string | null) {
  return value ? value.slice(0, 10) : 'None'
}

function shortRepoName(repoUrl: string) {
  try {
    const url = new URL(repoUrl)
    return url.pathname.replace(/^\/|\.git$/g, '') || repoUrl
  } catch {
    return repoUrl
  }
}

function buildGitHubBranchUrl(repoUrl: string, branch: string) {
  try {
    const url = new URL(repoUrl)
    if (url.hostname !== 'github.com') return null

    const [owner, repo] = url.pathname.replace(/^\/|\.git$/g, '').split('/')
    if (!owner || !repo) return null

    const encodedBranch = encodeURIComponent(branch).replaceAll('%2F', '/')
    return `https://github.com/${owner}/${repo}/tree/${encodedBranch}`
  } catch {
    return null
  }
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

function formatBytes(value: number) {
  if (value < 1024) {
    return `${value} bytes`
  }

  const units = ['KB', 'MB', 'GB']
  let size = value / 1024
  let unitIndex = 0

  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }

  return `${size >= 10 ? size.toFixed(0) : size.toFixed(1)} ${units[unitIndex]}`
}

function providerPrimaryStatus(
  connection: ProviderConnectionStatus,
  readiness: ProviderReadiness,
): ProviderPrimaryStatus {
  if (connection === 'connected') return 'connected'
  if (readiness === 'ready' || readiness === 'auth_required') return 'disconnected'
  if (readiness === 'unavailable') return 'unavailable'
  return 'not_configured'
}

function isLiveActivityStatus(status: TaskStatus) {
  return [
    'running',
    'waiting_for_approval',
    'committed',
    'pushed',
    'pull_request_opened',
  ].includes(status)
}

function redactLogMessage(message: string) {
  return message
    .replace(/sk-[A-Za-z0-9_-]{8,}/g, '[REDACTED]')
    .replace(/gh[opsu]_[A-Za-z0-9_]{8,}/g, '[REDACTED]')
    .replace(/(authorization:\s*bearer\s+)[^\s]+/gi, '$1[REDACTED]')
    .replace(/((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s,;]+/gi, '$1[REDACTED]')
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
  ].some((pattern) => pattern.test(message))
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
  ].some((pattern) => pattern.test(message))
}

function timelineLabelForLog(message: string) {
  const normalized = message.toLowerCase()

  if (/(workspace|repository|repo).*(prepared|cloned|ready|created)/.test(normalized)) {
    return 'Workspace prepared'
  }
  if (/(codex|agent).*(started|running|launched)/.test(normalized)) {
    return 'Codex started'
  }
  if (/diff.*(generated|ready|loaded)|changes.*ready/.test(normalized)) {
    return 'Diff generated'
  }
  if (/(commit|committed).*(completed|created|ready|from agentdock)|committed locally/.test(normalized)) {
    return 'Commit completed'
  }
  if (/(push|pushed).*(completed|ready|to branch)|pushed /.test(normalized)) {
    return 'Push completed'
  }
  if (/(pull request|pr).*(created|opened)/.test(normalized)) {
    return 'PR created'
  }
  if (/(merge|merged).*(completed|pull request|at)/.test(normalized)) {
    return 'Merge completed'
  }
  if (/failed|error/.test(normalized)) {
    return 'Action failed'
  }
  if (/waiting.*approval|approval/.test(normalized)) {
    return 'Ready for review'
  }

  return null
}

function visibleTimelineLogs(events: TaskLogEvent[]) {
  const seen = new Set<string>()
  const timeline: TimelineLogEvent[] = []

  for (const event of events) {
    const message = redactLogMessage(event.message)
    if (isInternalRuntimeLog(message) || isRawExecutionLog(message)) continue

    const label = timelineLabelForLog(message)
    if (!label) continue

    const key = `${label}:${event.level}`
    if (seen.has(key)) continue
    seen.add(key)
    timeline.push({ ...event, message, label })
  }

  return timeline
}

function rawExecutionLogs(events: TaskLogEvent[]) {
  return events
    .map((event) => ({ ...event, message: redactLogMessage(event.message) }))
    .filter((event) => !isInternalRuntimeLog(event.message))
    .filter((event) => isRawExecutionLog(event.message) || !timelineLabelForLog(event.message))
}

function upsertPromptAssistantProvider(
  providers: PromptAssistantProviderStatus[],
  next: PromptAssistantProviderStatus,
) {
  const existing = providers.filter((provider) => provider.provider !== next.provider)
  return [...existing, next].sort((left, right) =>
    left.provider.localeCompare(right.provider),
  )
}

function isCodexAuthFlowResponse(
  response: AgentProviderMetadata | AgentProviderAuthFlowResponse,
): response is AgentProviderAuthFlowResponse {
  return (
    'command' in response || 'provider_id' in response
  )
}

function fallbackCodexAuthFlow(
  metadata: AgentProviderMetadata,
): AgentProviderAuthFlowResponse {
  return {
    provider_id: 'codex',
    status: 'auth_required',
    message: 'OpenAI account connection is not enabled yet.',
    command: null,
    fallback_command: null,
    auth_url: null,
    verification_url: null,
    device_code: null,
    user_code: null,
    instructions: [
      'AgentDock does not collect your OpenAI password.',
      'Codex auth completes only after the backend accepts a callback or API key.',
    ],
    metadata,
  }
}

function formatError(error: unknown) {
  return error instanceof Error ? error.message : 'Unexpected error'
}

export default App
