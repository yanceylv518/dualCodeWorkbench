import type { AgentModelCatalog, AgentSettings, ExecutionJob, GitStatus, HandoffPackage, ProjectContract, ThreadDetails, Workspace, WorkspaceRemoteStatus } from "./types";
const API = "http://127.0.0.1:8876/api";
async function responseError(response: Response): Promise<Error> {
  const text = await response.text();
  try { const value = JSON.parse(text) as { detail?: string }; return new Error(value.detail || text); }
  catch { return new Error(text || `Request failed (${response.status})`); }
}
export async function fetchWorkspaces(): Promise<Workspace[]> {
  const r = await fetch(`${API}/workspaces`);
  if (!r.ok) throw new Error("Backend unavailable");
  const items = await r.json() as Array<{
    id: string;
    name: string;
    path: string;
    threads: Array<{
      id: string;
      title: string;
      state: Workspace["threads"][number]["state"];
      messages: Array<{ id: string; role: string; content: string; attachments?: Array<{ id: string; name: string; media_type: string; size: number }> }>;
    }>;
  }>;
  return items.map((workspace) => ({
    ...workspace,
    threads: workspace.threads.map((thread) => ({
      ...thread,
      messages: thread.messages.map((message) => ({
        id: message.id,
        agent: message.role as Workspace["threads"][number]["messages"][number]["agent"],
        text: message.content,
        attachments: message.attachments ?? [],
        time: "",
      })),
    })),
  }));
}
export const attachmentContentUrl = (workspaceId: string, threadId: string, attachmentId: string) =>
  `${API}/workspaces/${workspaceId}/threads/${threadId}/attachments/${attachmentId}/content`;
export async function createWorkspace(path: string) {
  const r = await fetch(`${API}/workspaces`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function provisionWorkspace(value: { path: string; remote_url: string; mode: "init" | "clone"; name?: string }) {
  const r = await fetch(`${API}/workspaces/provision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
  if (!r.ok) throw await responseError(r);
  return r.json();
}
export async function removeWorkspace(workspaceId: string) {
  const r = await fetch(`${API}/workspaces/${workspaceId}`, { method: "DELETE" });
  if (!r.ok) throw new Error(await r.text());
}
export async function createThread(workspaceId: string, title: string) {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function sendMessage(
  workspaceId: string,
  threadId: string,
  content: string,
  mode: string,
  attachmentIds: string[] = [],
) {
  const r = await fetch(
    `${API}/workspaces/${workspaceId}/threads/${threadId}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, mode, attachment_ids: attachmentIds }),
    },
  );
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function cancelRun(threadId: string) {
  const r = await fetch(`${API}/threads/${threadId}/cancel`, { method: "POST" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function retryMessage(workspaceId: string, threadId: string, messageId: string) {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/messages/${messageId}/retry`, { method: "POST" });
  if (!r.ok) throw await responseError(r);
  return r.json();
}
export async function undoRun(workspaceId: string, threadId: string, runId: string) {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/runs/${runId}/undo`, { method: "POST" });
  if (!r.ok) throw await responseError(r);
  return r.json();
}
export async function uploadAttachment(
  workspaceId: string,
  threadId: string,
  file: File,
) {
  const body = new FormData();
  body.append("file", file);
  const r = await fetch(
    `${API}/workspaces/${workspaceId}/threads/${threadId}/attachments`,
    { method: "POST", body },
  );
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export function threadSocket(threadId: string) {
  return new WebSocket(`ws://127.0.0.1:8876/api/ws/threads/${threadId}`);
}
export async function fetchApprovals(workspaceId: string, threadId: string) {
  const r = await fetch(
    `${API}/workspaces/${workspaceId}/threads/${threadId}/approvals`,
  );
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function fetchThreadDetails(workspaceId: string, threadId: string): Promise<ThreadDetails> {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/details`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function fetchContract(workspaceId: string, threadId: string): Promise<ProjectContract> {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/contract`);
  if (!r.ok) throw await responseError(r);
  return r.json();
}
export async function saveGovernance(workspaceId: string, value: ProjectContract["governance"]): Promise<void> {
  const r = await fetch(`${API}/workspaces/${workspaceId}/governance`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
  if (!r.ok) throw await responseError(r);
}
export async function saveTaskContract(workspaceId: string, threadId: string, value: ProjectContract["task"]): Promise<void> {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/contract`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
  if (!r.ok) throw await responseError(r);
}
export async function prepareHandoff(workspaceId: string, threadId: string, recipient: "codex" | "claude", purpose: "verify" | "review"): Promise<HandoffPackage> {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/handoffs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ recipient, purpose }) });
  if (!r.ok) throw await responseError(r);
  return r.json();
}
export async function listHandoffs(workspaceId: string, threadId: string): Promise<HandoffPackage[]> {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/handoffs`);
  if (!r.ok) throw await responseError(r);
  return r.json();
}
export async function sendHandoff(workspaceId: string, threadId: string, handoffId: string): Promise<void> {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/handoffs/${handoffId}/send`, { method: "POST" });
  if (!r.ok) throw await responseError(r);
}
export async function decideApproval(
  workspaceId: string,
  threadId: string,
  approvalId: string,
  approved: boolean,
  note = "",
  scope: "once" | "thread" = "once",
) {
  const r = await fetch(
    `${API}/workspaces/${workspaceId}/threads/${threadId}/approvals/${approvalId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved, note, scope }),
    },
  );
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function fetchAgentSettings(): Promise<AgentSettings> {
  const r = await fetch(`${API}/settings/agents`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function saveAgentSettings(
  value: AgentSettings,
): Promise<AgentSettings> {
  const r = await fetch(`${API}/settings/agents`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function fetchAgentHealth() {
  const r = await fetch(`${API}/agents/health`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function fetchAgentModels(): Promise<AgentModelCatalog> {
  const r = await fetch(`${API}/agents/models`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function fetchGitStatus(workspaceId: string): Promise<GitStatus> {
  const r = await fetch(`${API}/workspaces/${workspaceId}/git/status`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function requestGitAction(workspaceId: string, threadId: string, action: "commit" | "push" | "pull", message = "") {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/git/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, message }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function fetchWorkspaceRemote(workspaceId: string): Promise<WorkspaceRemoteStatus> {
  const r = await fetch(`${API}/workspaces/${workspaceId}/remote`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function saveWorkspaceRemote(workspaceId: string, value: { remote_url: string; vps_repo_path: string }) {
  const r = await fetch(`${API}/workspaces/${workspaceId}/remote`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function requestRemoteGitAction(workspaceId: string, threadId: string, action: "provision" | "repair_provision" | "fetch" | "pull") {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/remote/actions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function requestTestRun(workspaceId: string, threadId: string) {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/tests/run`, { method: "POST" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function fetchExecutionJobs(workspaceId: string, threadId: string): Promise<ExecutionJob[]> {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/jobs`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function retryExecutionJob(workspaceId: string, threadId: string, jobId: string): Promise<void> {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/jobs/${jobId}/retry`, { method: "POST" });
  if (!r.ok) throw new Error(await r.text());
  await r.json();
}
