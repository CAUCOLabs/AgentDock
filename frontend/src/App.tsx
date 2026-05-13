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

type AgentProvider = 'mock' | 'local' | 'openai'
type ChangeScope = 'only_selected_file' | 'allow_related_files'

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
  agent: 'mock',
  targetFile: '',
  changeScope: 'only_selected_file',
}

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
  const [repoFiles, setRepoFiles] = useState<RepositoryFileEntry[]>([])
  const [fileFilter, setFileFilter] = useState('')
  const [filePreview, setFilePreview] =
    useState<RepositoryFilePreviewResponse | null>(null)
  const [isLoadingFiles, setIsLoadingFiles] = useState(false)
  const [isConnectingGithub, setIsConnectingGithub] = useState(false)
  const [isLoadingGithubRepos, setIsLoadingGithubRepos] = useState(false)
  const [isLoadingPreview, setIsLoadingPreview] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [isCreating, setIsCreating] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const connectedGithubLogin =
    githubInstallation?.account_login ?? githubAccount?.login ?? null

  const selectedTaskFromList = useMemo(
    () => tasks.find((task) => task.task_id === selectedTaskId) ?? null,
    [selectedTaskId, tasks],
  )
  const activeTask = selectedTask ?? selectedTaskFromList

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
    const response = await fetch(`${API_URL}${path}`, {
      headers: {
        'Content-Type': 'application/json',
        ...(githubSessionId ? { 'X-AgentDock-Session': githubSessionId } : {}),
        ...init?.headers,
      },
      ...init,
    })

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

      {(notice || error) && (
        <section className="messages" aria-live="polite">
          {notice && <p className="notice">{notice}</p>}
          {error && <p className="error">{error}</p>}
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
                  <strong>GitHub connection</strong>
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
                  <strong>Selected repository</strong>
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
                  <small>{selectedGithubRepo.clone_url}</small>
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
                <summary>Manual repository URL</summary>
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
                <span>3</span>
                <div>
                  <strong>Repository Explorer</strong>
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
                    <strong>{form.targetFile || 'No target selected'}</strong>
                    {isLoadingPreview && <p className="empty-state">Loading preview.</p>}
                    {filePreview ? (
                      <pre className="file-preview">{filePreview.content}</pre>
                    ) : (
                      <p className="empty-state">A read-only preview appears here after selecting a file.</p>
                    )}
                  </div>
                </div>
              )}
            </div>

            <div className="launcher-panel">
              <div className="launcher-step">
                <span>4</span>
                <div>
                  <strong>Change scope</strong>
                  <small>Choose how tightly the agent should stay near the target file.</small>
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
              <div className="runner-row">
                <span>Runner: {form.agent}</span>
                <select
                  value={form.agent}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      agent: event.target.value as AgentProvider,
                    })
                  }
                >
                  <option value="mock">Mock</option>
                  <option value="local">Local command</option>
                  <option value="openai">OpenAI</option>
                </select>
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

      <section className="detail-grid">
        <section className="task-details glass-card" aria-labelledby="task-details-title">
          <div className="section-heading">
            <h2 id="task-details-title">
              <Code2 size={17} />
              Task Details
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
              <dl className="metadata-grid">
                <div>
                  <dt>Task</dt>
                  <dd>{activeTask.task_id}</dd>
                </div>
                <div>
                  <dt>Working Branch</dt>
                  <dd>{activeTask.working_branch ?? 'Not assigned'}</dd>
                </div>
                <div>
                  <dt>Commit</dt>
                  <dd className="metric-value">{shortSha(activeTask.commit_sha)}</dd>
                </div>
                <div>
                  <dt>Repository</dt>
                  <dd>{shortRepoName(activeTask.repo_url)}</dd>
                </div>
                <div>
                  <dt>Target File</dt>
                  <dd>{activeTask.target_file ?? 'None'}</dd>
                </div>
                <div>
                  <dt>Change Scope</dt>
                  <dd>{(activeTask.change_scope ?? 'only_selected_file').replaceAll('_', ' ')}</dd>
                </div>
                <div>
                  <dt>Delivery</dt>
                  <dd className="badge-row">
                    <span className={activeTask.pushed ? 'meta-badge ok' : 'meta-badge'}>
                      {activeTask.pushed ? 'Pushed' : 'Local only'}
                    </span>
                    <span className={activeTask.pr_url ? 'meta-badge ok' : 'meta-badge'}>
                      {activeTask.pr_url ? 'PR opened' : 'No PR'}
                    </span>
                  </dd>
                </div>
              </dl>

              <div className="instruction-block">
                <h3>Instruction</h3>
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

        <section
          className="workspace-status glass-card"
          aria-labelledby="workspace-status-title"
        >
          <div className="section-heading">
            <h2 id="workspace-status-title">
              <GitBranch size={17} />
              Workspace Status
            </h2>
            {activeTask && (
              <button
                type="button"
                className="mini-action"
                onClick={() => void loadWorkspaceStatus(activeTask.task_id)}
              >
                View workspace status
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
                <dd>{shortSha(workspaceStatus.head_commit)}</dd>
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
      </section>

      <section className="agent-log glass-card" aria-labelledby="agent-log-title">
        <div className="section-heading">
          <h2 id="agent-log-title">
            <Terminal size={17} />
            Agent Log
          </h2>
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

      <section className="diff-viewer glass-card" aria-labelledby="diff-viewer-title">
        <div className="section-heading">
          <h2 id="diff-viewer-title">
            <FileText size={17} />
            Diff Viewer
          </h2>
          <div className="section-actions">
            <span>{taskDiff?.changed_files.length ?? 0} changed files</span>
            {activeTask && (
              <button
                type="button"
                className="mini-action"
                onClick={() => void loadTaskDiff(activeTask.task_id)}
              >
                View diff
              </button>
            )}
          </div>
        </div>

        {taskDiff?.changed_files.length ? (
          <ul className="changed-files">
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

function formatError(error: unknown) {
  return error instanceof Error ? error.message : 'Unexpected error'
}

export default App
