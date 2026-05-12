export type TaskStatus =
  | "queued"
  | "running"
  | "waiting_for_approval"
  | "approved"
  | "rejected"
  | "completed"
  | "failed"
  | "cancelled";

export type AgentProvider = "mock" | "local" | "openai";

export type ChangedFile = {
  path: string;
  status: string;
  additions: number;
  deletions: number;
};

export type TaskSummary = {
  task_id: string;
  repo_url: string;
  base_branch: string;
  working_branch: string | null;
  instruction: string;
  agent: AgentProvider;
  status: TaskStatus;
  changed_files: ChangedFile[];
  commit_sha: string | null;
  pushed: boolean;
  pr_url: string | null;
  created_at: string;
  updated_at: string;
  error: string | null;
};

export type TaskDiffResponse = {
  task_id: string;
  status: TaskStatus;
  diff: string;
  changed_files: ChangedFile[];
};

export type WorkspaceStatusResponse = {
  task_id: string;
  repo_path: string;
  branch: string | null;
  head_commit: string | null;
  remote_url?: string | null;
  has_valid_origin?: boolean;
  default_branch?: string | null;
  push_enabled?: boolean;
  pr_creation_enabled?: boolean;
  is_dirty: boolean;
  has_untracked_files: boolean;
  changed_files: ChangedFile[];
  commit_sha: string | null;
  pushed: boolean;
  pr_url: string | null;
};

export type TaskLogEvent = {
  timestamp: string;
  level: string;
  message: string;
};

export type TaskLogsResponse = {
  task_id: string;
  events: TaskLogEvent[];
};

export type TaskCreateRequest = {
  repo_url: string;
  base_branch: string;
  instruction: string;
  agent: AgentProvider;
};

export type TaskCreateResponse = {
  task_id: string;
  status: TaskStatus;
  message: string;
};

export type TaskApprovalRequest = {
  approve: boolean;
  message?: string | null;
};

export type TaskApprovalResponse = {
  task_id: string;
  status: TaskStatus;
  message: string;
};
