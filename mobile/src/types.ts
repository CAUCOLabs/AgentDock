export type TaskStatus =
  | "queued"
  | "running"
  | "waiting_for_approval"
  | "approved"
  | "committed"
  | "pushed"
  | "pull_request_opened"
  | "merged"
  | "rejected"
  | "completed"
  | "failed"
  | "cancelled";

export type AgentProvider = string;
export type ChangeScope = "only_selected_file" | "allow_related_files";
export type ProviderStatus =
  | "available"
  | "not_configured"
  | "coming_soon"
  | "local_required";
export type ProviderConnectionStatus =
  | "connected"
  | "connecting"
  | "disconnected"
  | "not_configured"
  | "coming_soon";
export type ProviderReadiness = "ready" | "auth_required" | "unavailable" | "not_configured";
export type ProviderPrimaryStatus =
  | "connected"
  | "connecting"
  | "active"
  | "disconnected"
  | "ready"
  | "unavailable"
  | "not_configured";

export type AgentProviderStatus = {
  id: AgentProvider | "development";
  label: string;
  status: ProviderStatus;
  description: string;
  required_config: string[];
};

export type AgentProviderMetadata = {
  id: AgentProvider;
  label: string;
  auth_mode: string;
  status: ProviderConnectionStatus;
  connection: ProviderConnectionStatus;
  readiness: ProviderReadiness;
  primary_status: ProviderPrimaryStatus;
  description: string;
  supports_chatgpt_login: boolean;
  supports_api_key: boolean;
};

export type AgentProviderAuthFlowResponse = {
  provider_id: AgentProvider;
  status: "auth_required" | "waiting_for_auth";
  message: string;
  command?: string | null;
  fallback_command?: string | null;
  auth_url?: string | null;
  verification_url?: string | null;
  device_code?: string | null;
  user_code?: string | null;
  instructions: string[];
  metadata: AgentProviderMetadata;
};

export type AgentProviderVerificationResponse = {
  provider_id: AgentProvider;
  connected: boolean;
  message: string;
  metadata: AgentProviderMetadata;
};

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
  target_file?: string | null;
  change_scope?: ChangeScope;
  instruction: string;
  agent: AgentProvider;
  status: TaskStatus;
  changed_files: ChangedFile[];
  commit_sha: string | null;
  pushed: boolean;
  pushed_at?: string | null;
  remote_branch?: string | null;
  last_push_result?: string | null;
  branch_url?: string | null;
  pr_url: string | null;
  pr_number?: number | null;
  pr_state?: string | null;
  merged_at?: string | null;
  merge_commit_sha?: string | null;
  merged_by?: string | null;
  merged_source?: string | null;
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
  pushed_at?: string | null;
  remote_branch?: string | null;
  last_push_result?: string | null;
  branch_url?: string | null;
  pr_url: string | null;
  pr_number?: number | null;
  pr_state?: string | null;
  merged_at?: string | null;
  merge_commit_sha?: string | null;
  merged_by?: string | null;
  merged_source?: string | null;
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
  target_file?: string | null;
  change_scope?: ChangeScope;
};

export type RepositoryFileEntry = {
  path: string;
  type: "file" | "directory";
  size?: number | null;
  extension?: string | null;
};

export type RepositoryFilesResponse = {
  repo_url: string;
  base_branch: string;
  files: RepositoryFileEntry[];
};

export type RepositoryFilePreviewResponse = {
  repo_url: string;
  base_branch: string;
  path: string;
  size: number;
  truncated: boolean;
  content: string;
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

export type TaskPullRequestResponse = {
  task_id: string;
  status: TaskStatus;
  message: string;
  pr_url: string;
  pr_number: number;
  pr_state: string;
};

export type TaskMergePullRequestResponse = {
  task_id: string;
  status: TaskStatus;
  message: string;
  pr_url: string;
  pr_number: number;
  pr_state: string;
  merged_at: string;
  merge_commit_sha: string;
  merged_by?: string | null;
  merged_source?: string | null;
};

export type GitHubAuthStartResponse = {
  auth_url: string;
  session_id: string;
  state: string;
};

export type GitHubAccount = {
  github_user_id: number;
  login: string;
  name?: string | null;
  avatar_url?: string | null;
  html_url?: string | null;
  scopes?: string | null;
  connected_at: string;
};

export type GitHubInstallation = {
  installation_id: number;
  account_login: string;
  account_type: string;
  selected_repositories: string[];
  permissions: Record<string, string>;
  created_at: string;
  updated_at: string;
};

export type GitHubRepository = {
  id: number;
  name: string;
  full_name: string;
  html_url: string;
  clone_url: string;
  default_branch: string;
  private: boolean;
  visibility: string;
  can_read: boolean;
  can_push: boolean;
};

export type GitHubRepositoriesResponse = {
  repositories: GitHubRepository[];
};

export type GitHubInstallationsResponse = {
  installations: GitHubInstallation[];
};
