import type { AgentModelCatalog, AgentSettings, GitStatus, ThreadDetails, Workspace, WorkspaceRemoteStatus } from "./types";
const API = "http://127.0.0.1:8765/api";
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
      messages: Array<{ id: string; role: string; content: string }>;
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
        time: "",
      })),
    })),
  }));
}
export async function createWorkspace(path: string) {
  const r = await fetch(`${API}/workspaces`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
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
  return new WebSocket(`ws://127.0.0.1:8765/api/ws/threads/${threadId}`);
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
export async function decideApproval(
  workspaceId: string,
  threadId: string,
  approvalId: string,
  approved: boolean,
  note = "",
) {
  const r = await fetch(
    `${API}/workspaces/${workspaceId}/threads/${threadId}/approvals/${approvalId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved, note }),
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
export async function requestRemoteGitAction(workspaceId: string, threadId: string, action: "fetch" | "pull") {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/remote/actions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
export async function requestTestRun(workspaceId: string, threadId: string) {
  const r = await fetch(`${API}/workspaces/${workspaceId}/threads/${threadId}/tests/run`, { method: "POST" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
