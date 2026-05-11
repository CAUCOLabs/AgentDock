import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
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

type AgentProvider = 'mock'

type ChangedFile = {
  path: string
  status: string
  additions: number
  deletions: number
}

type TaskSummary = {
  task_id: string
  repo_url: string
  base_branch: string
  working_branch: string | null
  instruction: string
  agent: AgentProvider
  status: TaskStatus
  changed_files: ChangedFile[]
  commit_sha: string | null
  pushed: boolean
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
  is_dirty: boolean
  has_untracked_files: boolean
  changed_files: ChangedFile[]
  commit_sha: string | null
  pushed: boolean
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
}

const API_URL =
  import.meta.env.VITE_AGENTDOCK_API_URL?.replace(/\/$/, '') ??
  'http://127.0.0.1:8000'

const initialForm: CreateTaskForm = {
  repoUrl: '',
  baseBranch: 'main',
  instruction: '',
  agent: 'mock',
}

function App() {
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [selectedTask, setSelectedTask] = useState<TaskSummary | null>(null)
  const [taskDiff, setTaskDiff] = useState<TaskDiffResponse | null>(null)
  const [workspaceStatus, setWorkspaceStatus] =
    useState<WorkspaceStatusResponse | null>(null)
  const [taskLogs, setTaskLogs] = useState<TaskLogEvent[]>([])
  const [form, setForm] = useState<CreateTaskForm>(initialForm)
  const [isLoading, setIsLoading] = useState(false)
  const [isCreating, setIsCreating] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const selectedTaskFromList = useMemo(
    () => tasks.find((task) => task.task_id === selectedTaskId) ?? null,
    [selectedTaskId, tasks],
  )

  useEffect(() => {
    void loadTasks()
  }, [])

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

  const activeTask = selectedTask ?? selectedTaskFromList

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">AgentDock</p>
          <h1>AgentDock</h1>
          <p className="subtitle">
            Mobile-first command center for AI coding agents
          </p>
        </div>
        <button type="button" onClick={() => void loadTasks()}>
          Refresh
        </button>
      </header>

      {(notice || error) && (
        <section className="messages" aria-live="polite">
          {notice && <p className="notice">{notice}</p>}
          {error && <p className="error">{error}</p>}
        </section>
      )}

      <section className="dashboard-grid">
        <aside className="task-dashboard" aria-label="Task dashboard">
          <div className="section-heading">
            <h2>Task Dashboard</h2>
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
                  <strong>{task.task_id}</strong>
                  <small>{task.repo_url}</small>
                </span>
                <span className={`status-pill ${task.status}`}>
                  {task.status.replaceAll('_', ' ')}
                </span>
              </button>
            ))}
          </div>
        </aside>

        <section className="task-create" aria-labelledby="create-task-title">
          <div className="section-heading">
            <h2 id="create-task-title">Create Task</h2>
          </div>

          <form onSubmit={(event) => void createTask(event)}>
            <label>
              Repository URL
              <input
                required
                type="url"
                placeholder="https://github.com/org/repo"
                value={form.repoUrl}
                onChange={(event) =>
                  setForm({ ...form, repoUrl: event.target.value })
                }
              />
            </label>

            <div className="form-pair">
              <label>
                Base Branch
                <input
                  required
                  value={form.baseBranch}
                  onChange={(event) =>
                    setForm({ ...form, baseBranch: event.target.value })
                  }
                />
              </label>

              <label>
                Agent
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
                </select>
              </label>
            </div>

            <label>
              Instruction
              <textarea
                required
                minLength={10}
                rows={5}
                placeholder="Describe the coding task for the agent."
                value={form.instruction}
                onChange={(event) =>
                  setForm({ ...form, instruction: event.target.value })
                }
              />
            </label>

            <button type="submit" disabled={isCreating}>
              {isCreating ? 'Creating Task' : 'Create Task'}
            </button>
          </form>
        </section>
      </section>

      <section className="detail-grid">
        <section className="task-details" aria-labelledby="task-details-title">
          <div className="section-heading">
            <h2 id="task-details-title">Task Details</h2>
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
                  <dd>{shortSha(activeTask.commit_sha)}</dd>
                </div>
                <div>
                  <dt>Delivery</dt>
                  <dd>
                    {activeTask.pushed ? 'Pushed' : 'Local only'}
                    {activeTask.pr_url ? `, PR opened` : ''}
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
                  Approve
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={activeTask.status !== 'waiting_for_approval'}
                  onClick={() => void approveTask(false)}
                >
                  Reject
                </button>
                <button
                  type="button"
                  className="secondary danger"
                  onClick={() => void deleteTask()}
                >
                  Delete
                </button>
              </div>
            </>
          )}
        </section>

        <section
          className="workspace-status"
          aria-labelledby="workspace-status-title"
        >
          <div className="section-heading">
            <h2 id="workspace-status-title">Workspace Status</h2>
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

      <section className="agent-log" aria-labelledby="agent-log-title">
        <div className="section-heading">
          <h2 id="agent-log-title">Agent Log</h2>
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

      <section className="diff-viewer" aria-labelledby="diff-viewer-title">
        <div className="section-heading">
          <h2 id="diff-viewer-title">Diff Viewer</h2>
          <span>{taskDiff?.changed_files.length ?? 0} changed files</span>
        </div>

        {taskDiff?.changed_files.length ? (
          <ul className="changed-files">
            {taskDiff.changed_files.map((file) => (
              <li key={`${file.status}-${file.path}`}>
                <span>{file.path}</span>
                <small>
                  {file.status} +{file.additions} -{file.deletions}
                </small>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty-state">No changed files to show.</p>
        )}

        <pre>{taskDiff?.diff || 'No diff loaded.'}</pre>
      </section>
    </main>
  )
}

function shortSha(value: string | null) {
  return value ? value.slice(0, 10) : 'None'
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
