import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { motion } from 'framer-motion'
import {
  Activity,
  Bot,
  CheckCircle2,
  Code2,
  FileText,
  GitBranch,
  RefreshCw,
  Rocket,
  ShieldCheck,
  Terminal,
  Trash2,
  XCircle,
} from 'lucide-react'
import './App.css'

type TaskStatus =
  | 'queued'
  | 'running'
  | 'waiting_for_approval'
  | 'approved'
  | 'rejected'
  | 'completed'
  | 'failed'
  | 'cancelled'

type AgentProvider =
  | 'codex'
  | 'claude'
  | 'ollama'
  | 'local_model'
  | 'mock'
  | 'local'
  | 'openai'
type ChangeScope = 'only_selected_file' | 'allow_related_files'
type ProviderConnectionStatus =
  | 'connected'
  | 'disconnected'
  | 'not_configured'
  | 'coming_soon'

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
  description: string
  supports_chatgpt_login: boolean
  supports_api_key: boolean
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
  pr_url: string | null
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
  pr_url: string | null
}

type TaskLogEvent = {
  timestamp: string
  level: string
  message: string
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

const providerOptions: Array<{ label: string; value: AgentProvider }> = [
  { label: 'Codex', value: 'codex' },
  { label: 'Claude', value: 'claude' },
  { label: 'Ollama', value: 'ollama' },
  { label: 'Local model', value: 'local_model' },
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
  const [form, setForm] = useState<CreateTaskForm>(initialForm)
  const [githubSessionId, setGithubSessionId] = useState<string | null>(() =>
    localStorage.getItem(GITHUB_SESSION_STORAGE_KEY),
  )
  const [githubAccount, setGithubAccount] = useState<GitHubAccount | null>(null)
  const [githubInstallation, setGithubInstallation] =
    useState<GitHubInstallation | null>(null)
  const [githubRepos, setGithubRepos] = useState<GitHubRepository[]>([])
  const [githubRepoFilter, setGithubRepoFilter] = useState('')
  const [agentProviders, setAgentProviders] = useState<AgentProviderMetadata[]>([])
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
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [apiConnectionError, setApiConnectionError] = useState<string | null>(
    null,
  )
  const connectedGithubLogin =
    githubInstallation?.account_login ?? githubAccount?.login ?? null

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
  const selectedAgentProvider = agentProviderById.get(form.agent)
  const activeAgentLabel = activeTask
    ? agentProviderById.get(activeTask.agent)?.label ??
      providerOptions.find((provider) => provider.value === activeTask.agent)?.label ??
      activeTask.agent
    : null
  const hasConnectedProvider = agentProviders.some(
    (provider) => provider.status === 'connected',
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

  useEffect(() => {
    void loadTasks()
    void loadAgentProviders()
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

  async function connectAgentProvider(providerId: AgentProvider) {
    await request<AgentProviderMetadata>('/agent-providers/connect', {
      method: 'POST',
      body: JSON.stringify({ provider_id: providerId }),
    })
    await loadAgentProviders()
  }

  async function disconnectAgentProvider(providerId: AgentProvider) {
    await request<AgentProviderMetadata>('/agent-providers/disconnect', {
      method: 'POST',
      body: JSON.stringify({ provider_id: providerId }),
    })
    await loadAgentProviders()
  }

  async function loadTaskBundle(taskId: string) {
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
    } catch (err) {
      setError(formatError(err))
      setSelectedTask(selectedTaskFromList)
      setTaskDiff(null)
      setWorkspaceStatus(null)
      setTaskLogs([])
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
          agent: form.agent,
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

  async function approveTask(approve: boolean) {
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

        <section className="task-create prompt-launcher glass-card" aria-labelledby="create-task-title">
          <div className="launcher-heading">
            <div>
              <h2 id="create-task-title">
                <Rocket size={20} />
                Prompt Launcher
              </h2>
              <p>
                Paste a prompt from ChatGPT, Claude, Codex, or your own
                workflow. AgentDock will run it in a safe branch and wait for
                approval.
              </p>
            </div>
          </div>

          <form onSubmit={(event) => void createTask(event)}>
            <div className="launcher-panel">
              <div className="launcher-step">
                <span>1</span>
                <div>
                  <strong>Connections</strong>
                  <small>
                    {connectedGithubLogin
                      ? `Connected as ${connectedGithubLogin}`
                      : 'Connect GitHub or use a manual repository URL.'}
                  </small>
                </div>
              </div>
              <div>
                <div className="github-actions">
                  <button
                    type="button"
                    className="mini-action"
                    onClick={() =>
                      connectedGithubLogin
                        ? void loadGithubRepos()
                        : void connectGithub()
                    }
                    disabled={isConnectingGithub || isLoadingGithubRepos}
                  >
                    {connectedGithubLogin
                      ? isLoadingGithubRepos
                        ? 'Loading repos'
                        : 'Refresh repos'
                      : isConnectingGithub
                        ? 'Connecting'
                        : 'Connect GitHub'}
                  </button>
                  {connectedGithubLogin && (
                    <button
                      type="button"
                      className="mini-action"
                      onClick={() => void logoutGithub()}
                    >
                      Logout
                    </button>
                  )}
                </div>
              </div>
            </div>

            <div className="launcher-panel">
              <div className="launcher-step">
                <span>2</span>
                <div>
                  <strong>AI Agent</strong>
                  <small>Select the agent profile for this task.</small>
                </div>
              </div>
              <div className="provider-options">
                {providerOptions.map((provider) => {
                  const metadata = agentProviderById.get(provider.value)
                  const status = metadata?.status ?? 'disconnected'
                  const isConnected = status === 'connected'
                  const canConnect = status === 'disconnected'

                  return (
                  <div
                    key={provider.value}
                    className={
                      form.agent === provider.value
                        ? 'provider-option selected'
                        : 'provider-option'
                    }
                  >
                    <input
                      type="radio"
                      name="agentProvider"
                      checked={form.agent === provider.value}
                      onChange={() =>
                        setForm({ ...form, agent: provider.value })
                      }
                    />
                    <span>{metadata?.label ?? provider.label}</span>
                    <small className={`provider-status-badge ${status}`}>
                      {formatConnectionStatus(status)}
                    </small>
                    <small>{metadata?.description ?? 'Provider metadata is loading.'}</small>
                    <div className="provider-card-actions">
                      {isConnected ? (
                        <button
                          type="button"
                          className="mini-action"
                          onClick={() => void disconnectAgentProvider(provider.value)}
                        >
                          Disconnect
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="mini-action"
                          disabled={!canConnect}
                          onClick={() => void connectAgentProvider(provider.value)}
                        >
                          Connect
                        </button>
                      )}
                    </div>
                  </div>
                  )
                })}
              </div>
              <p className="provider-mode-note">
                {selectedAgentProvider
                  ? providerConnectionMessage(selectedAgentProvider)
                  : 'Provider setup state is loading.'}
              </p>
              {!hasConnectedProvider && (
                <p className="provider-mode-note">
                  Development agent active: tasks currently run using the local development agent.
                </p>
              )}
            </div>

            <div className="launcher-panel">
              <div className="launcher-step">
                <span>3</span>
                <div>
                  <strong>Repository</strong>
                  <small>
                    {selectedGithubRepo
                      ? selectedGithubRepo.full_name
                      : form.repoUrl || 'No repository selected'}
                  </small>
                </div>
              </div>

              {selectedGithubRepo && (
                <div className="selected-repo-card">
                  <strong>{selectedGithubRepo.full_name}</strong>
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
                  <small>{selectedGithubRepo.html_url}</small>
                </div>
              )}

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
            </div>

            <div className="launcher-panel repository-explorer">
              <div className="launcher-step">
                <span>4</span>
                <div>
                  <strong>Target Selection</strong>
                  <small>{form.targetFile || 'Select a target file'}</small>
                </div>
                <button
                  type="button"
                  className="mini-action"
                  disabled={isLoadingFiles || !form.repoUrl}
                  onClick={() => void loadRepositoryFiles()}
                >
                  {isLoadingFiles ? 'Loading files' : 'Browse files'}
                </button>
              </div>

              {repoFiles.length > 0 && (
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
              )}
            </div>

            <div className="launcher-panel">
              <div className="launcher-step">
                <span>5</span>
                <div>
                  <strong>Review & Approval</strong>
                  <small>Choose how tightly the task should stay near the target file.</small>
                </div>
              </div>
              <div className="scope-options">
                <label className={form.changeScope === 'only_selected_file' ? 'scope-option selected' : 'scope-option'}>
                  <input
                    type="radio"
                    name="changeScope"
                    checked={form.changeScope === 'only_selected_file'}
                    onChange={() =>
                      setForm({ ...form, changeScope: 'only_selected_file' })
                    }
                  />
                  <span>Only selected file</span>
                  <small>Safest default. Other files should only change if unavoidable.</small>
                </label>
                <label className={form.changeScope === 'allow_related_files' ? 'scope-option selected' : 'scope-option'}>
                  <input
                    type="radio"
                    name="changeScope"
                    checked={form.changeScope === 'allow_related_files'}
                    onChange={() =>
                      setForm({ ...form, changeScope: 'allow_related_files' })
                    }
                  />
                  <span>Allow related files</span>
                  <small>Use when tests, types, or neighboring modules may need edits.</small>
                </label>
              </div>
            </div>

            <div className="launcher-panel">
              <div className="launcher-step">
                <span>6</span>
                <div>
                  <strong>Task Prompt</strong>
                  <small>Describe the code change the agent should prepare.</small>
                </div>
              </div>

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

              <label className="prompt-field">
                What should the agent do?
                <textarea
                  required
                  minLength={10}
                  rows={7}
                  placeholder="Paste your coding prompt here. Example: Fix the indexing issue, keep the change minimal, run tests, and show the diff before approval."
                  value={form.instruction}
                  onChange={(event) =>
                    setForm({ ...form, instruction: event.target.value })
                  }
                />
                <span className="character-count">
                  {form.instruction.length} characters
                </span>
              </label>
            </div>

            <div className="safety-note">
              Safety: AgentDock creates an isolated workspace and branch.
              Nothing is pushed or opened as a PR unless explicitly enabled.
            </div>

            <button type="submit" disabled={isCreating}>
              <Rocket size={17} />
              {isCreating ? 'Launching Agent Task' : 'Launch Agent Task'}
            </button>
          </form>
        </section>
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
                <button
                  type="button"
                  disabled={activeTask.status !== 'waiting_for_approval'}
                  onClick={() => void approveTask(true)}
                >
                  <CheckCircle2 size={16} />
                  Approve
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={activeTask.status !== 'waiting_for_approval'}
                  onClick={() => void approveTask(false)}
                >
                  <XCircle size={16} />
                  Reject
                </button>
                <button
                  type="button"
                  className="secondary danger"
                  onClick={() => void deleteTask()}
                >
                  <Trash2 size={16} />
                  Delete
                </button>
              </div>
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
                    <dt>PR URL</dt>
                    <dd>{activeTask.pr_url ?? 'None'}</dd>
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
                  Agent log
                </h3>
                <span>{taskLogs.length} events</span>
              </div>

              {taskLogs.length === 0 ? (
                <p className="empty-state">No task events to show.</p>
              ) : (
                <ol className="log-list">
                  {taskLogs.map((event) => (
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

function formatConnectionStatus(status?: ProviderConnectionStatus) {
  switch (status) {
    case 'connected':
      return 'Connected'
    case 'disconnected':
      return 'Disconnected'
    case 'not_configured':
      return 'Not configured'
    case 'coming_soon':
      return 'Coming soon'
    default:
      return 'Unknown'
  }
}

function providerConnectionMessage(provider: AgentProviderMetadata) {
  switch (provider.status) {
    case 'connected':
      return `Connected. ${provider.description}`
    case 'disconnected':
      return `Disconnected. ${provider.description}`
    case 'not_configured':
      return `Not configured. ${provider.description}`
    case 'coming_soon':
      return `Coming soon. ${provider.description}`
    default:
      return provider.description
  }
}

function formatError(error: unknown) {
  return error instanceof Error ? error.message : 'Unexpected error'
}

export default App
