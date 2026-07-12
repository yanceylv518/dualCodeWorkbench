export type Agent = "user" | "codex" | "claude" | "system";
export type RunState =
  | "CREATED"
  | "PLANNING"
  | "WAITING_APPROVAL"
  | "IMPLEMENTING"
  | "TESTING"
  | "REVIEWING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "FALLBACK_TO_CODEX";
export interface Message {
  id: string;
  agent: Agent;
  text: string;
  time: string;
  attachment?: string;
  activity?: { agent: string; count: number; completed: boolean };
}
export interface Thread {
  id: string;
  title: string;
  state: RunState;
  messages: Message[];
}
export interface Workspace {
  id: string;
  name: string;
  path: string;
  threads: Thread[];
}
export interface AgentEvent {
  type: string;
  thread_id: string;
  run_id?: string;
  sequence: number;
  payload: Record<string, unknown>;
}
export interface Approval {
  id: string;
  action: string;
  reason: string;
  status: string;
}
export interface ThreadDetails {
  files: { path: string }[];
  diff: string;
  tests: { command: string; output: string; exit_code: number }[];
  worktree: string;
  codex_session_id: string;
}
export interface AgentSettings {
  enable_real_agents: boolean;
  codex_executable: string;
  codex_model: string;
  codex_reasoning_effort: string;
  claude_executable: string;
  claude_model: string;
  claude_reasoning_effort: string;
  claude_ssh_enabled: boolean;
  claude_ssh_host: string;
  claude_ssh_username: string;
  claude_ssh_port: number;
  claude_ssh_known_hosts: string;
  claude_ssh_client_key: string;
  claude_ssh_remote_root: string;
  claude_ssh_executable: string;
  test_executable: string;
  test_arguments: string[];
}
export interface AgentModel { id: string; label: string; description: string; default_reasoning?: string; reasoning_levels?: string[] }
export interface AgentModelCatalog { codex: AgentModel[]; claude: AgentModel[] }
export interface GitStatus {
  branch: string;
  head: string;
  remote: string;
  upstream: string;
  ahead: number;
  behind: number;
  changes: string[];
  commits: { sha: string; author: string; subject: string; date: string }[];
}
export interface WorkspaceRemoteStatus {
  settings: { remote_url: string; vps_repo_path: string };
  local: GitStatus;
  vps?: { branch: string; head: string; remote: string };
  same_remote: boolean;
  same_commit: boolean;
  error?: string;
}
