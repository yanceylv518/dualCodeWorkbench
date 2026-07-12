import { create } from "zustand";
import { demo } from "./data";
import * as api from "./api";
import type { Agent, AgentEvent, Approval, GitStatus, Message, RunState, ThreadDetails, Workspace, WorkspaceRemoteStatus } from "./types";

export type Mode = "codex" | "claude";

interface Store {
  workspaces: Workspace[];
  workspaceId: string;
  threadId: string;
  mode: Mode;
  backend: "connecting" | "online" | "offline";
  error?: string;
  socket?: WebSocket;
  pendingApproval?: Approval;
  details?: ThreadDetails;
  gitStatus?: GitStatus;
  remoteStatus?: WorkspaceRemoteStatus;
  terminal: string[];
  draftAttachments: { id: string; name: string; media_type: string }[];
  creatingThread: boolean;
  runMeta?: { branch?: string; worktree?: string };
  initialize: () => Promise<void>;
  setSelection: (workspaceId: string, threadId: string) => void;
  setMode: (mode: Mode) => void;
  addMessage: (agent: Agent, text: string) => void;
  setState: (state: RunState) => void;
  sendPrompt: (text: string) => Promise<void>;
  cancelRun: () => Promise<void>;
  upload: (file: File) => Promise<void>;
  removeAttachment: (id: string) => void;
  newThread: () => Promise<void>;
  openWorkspace: (path: string) => Promise<void>;
  decideApproval: (approved: boolean) => Promise<void>;
  gitAction: (action: "commit" | "push" | "pull", message?: string) => Promise<void>;
  saveRemote: (remote_url: string, vps_repo_path: string) => Promise<void>;
  remoteGitAction: (action: "fetch" | "pull") => Promise<void>;
  runTests: () => Promise<void>;
}

const mapThread = (state: Store, fn: (thread: Workspace["threads"][number]) => Workspace["threads"][number]) =>
  state.workspaces.map((workspace) =>
    workspace.id !== state.workspaceId
      ? workspace
      : { ...workspace, threads: workspace.threads.map((thread) => thread.id === state.threadId ? fn(thread) : thread) },
  );

export const useStore = create<Store>((set, get) => ({
  workspaces: demo,
  workspaceId: "w1",
  threadId: "t1",
  mode: "codex",
  backend: "connecting",
  terminal: [],
  draftAttachments: [],
  creatingThread: false,
  initialize: async () => {
    set({ backend: "connecting", error: undefined });
    for (let attempt = 0; attempt < 60; attempt += 1) {
      try {
        const workspaces = await api.fetchWorkspaces();
        const workspaceId = workspaces[0]?.id ?? "";
        const threadId = workspaces[0]?.threads[0]?.id ?? "";
        set({ workspaces, workspaceId, threadId, backend: "online", error: undefined });
        get().setSelection(workspaceId, threadId);
        return;
      } catch {
        await new Promise((resolve) => window.setTimeout(resolve, Math.min(250 + attempt * 150, 1500)));
      }
    }
    set({ backend: "offline", error: "本地后端启动超时，请重启应用或检查 8765 端口。" });
  },
  setSelection: (workspaceId, threadId) => {
    get().socket?.close();
    set({ workspaceId, threadId, pendingApproval: undefined, details: undefined, gitStatus: undefined, remoteStatus: undefined, terminal: [], runMeta: undefined });
    if (get().backend !== "online" || !threadId) return;
    void api.fetchApprovals(workspaceId, threadId).then((items) => set({ pendingApproval: items[0] })).catch(() => undefined);
    const refreshDetails = () => void api.fetchThreadDetails(workspaceId, threadId).then((details) => set({ details })).catch(() => undefined);
    refreshDetails();
    void api.fetchGitStatus(workspaceId).then((gitStatus) => set({ gitStatus })).catch(() => undefined);
    void api.fetchWorkspaceRemote(workspaceId).then((remoteStatus) => set({ remoteStatus })).catch(() => undefined);
    const socket = api.threadSocket(threadId);
    socket.onmessage = (event) => {
      const data = JSON.parse(event.data) as AgentEvent;
      const payload = data.payload;
      if (data.type === "agent.delta" && payload.agent && payload.text && data.run_id) set((state) => ({ workspaces: mapThread(state, (thread) => { const id = `stream-${data.run_id}`; const existing = thread.messages.find((item) => item.id === id); return { ...thread, messages: existing ? thread.messages.map((item) => item.id === id ? { ...item, text: item.text + String(payload.text) } : item) : [...thread.messages, { id, agent: payload.agent as Agent, text: String(payload.text), time: "" }] }; }) }));
      if (data.type === "message.created" && data.run_id) set((state) => ({ workspaces: mapThread(state, (thread) => ({ ...thread, messages: thread.messages.map((item) => item.id === `activity-${data.run_id}` && item.activity ? { ...item, activity: { ...item.activity, completed: true } } : item) })) }));
      if (data.type === "message.created" && payload.role && payload.content) set((state) => ({ workspaces: mapThread(state, (thread) => { const streamId = data.run_id ? `stream-${data.run_id}` : ""; const hasStream = thread.messages.some((item) => item.id === streamId); return { ...thread, messages: hasStream ? thread.messages.map((item) => item.id === streamId ? { ...item, id: String(payload.id ?? crypto.randomUUID()), text: String(payload.content), time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) } : item) : [...thread.messages, { id: String(payload.id ?? crypto.randomUUID()), agent: payload.role as Agent, text: String(payload.content), time: "" }] }; }) }));
      if (data.type === "agent.tool" && payload.item) {
        const item = payload.item as Record<string, unknown>;
        const label = `${String(payload.agent)} · ${String(item.type ?? "tool")}`;
        set((state) => {
          const activityId = `activity-${data.run_id ?? "current"}`;
          const workspace = state.workspaces.find((entry) => entry.id === state.workspaceId);
          const thread = workspace?.threads.find((entry) => entry.id === state.threadId);
          const current = thread?.messages.find((entry) => entry.id === activityId);
          return {
            terminal: state.terminal.at(-1) === label ? state.terminal : [...state.terminal, label].slice(-500),
            workspaces: mapThread(state, (entry) => ({ ...entry, messages: current
              ? entry.messages.map((message) => message.id === activityId ? { ...message, activity: { agent: String(payload.agent), count: (message.activity?.count ?? 0) + 1, completed: false } } : message)
              : [...entry.messages, { id: activityId, agent: "system", text: "", time: "", activity: { agent: String(payload.agent), count: 1, completed: false } }] })),
          };
        });
      }
      if (data.type === "run.state_changed" && payload.state) get().setState(payload.state as RunState);
      if (data.type === "test.result") get().addMessage("system", `测试结果：\n${String(payload.output)}`);
      if (data.type === "terminal.output" && payload.text) set((state) => ({ terminal: [...state.terminal, String(payload.text)].slice(-500) }));
      if (data.type === "run.output" && payload.kind === "git") set({ runMeta: { branch: String(payload.branch ?? ""), worktree: String(payload.worktree ?? "") } });
      if (["test.result", "run.completed", "run.output"].includes(data.type)) { refreshDetails(); void api.fetchGitStatus(workspaceId).then((gitStatus) => set({ gitStatus })).catch(() => undefined); void api.fetchWorkspaceRemote(workspaceId).then((remoteStatus) => set({ remoteStatus })).catch(() => undefined); }
      if (data.type === "error") set({ error: String(payload.message) });
      if (data.type === "approval.required" && payload.id) set({ pendingApproval: { id: String(payload.id), action: String(payload.action), reason: String(payload.reason), status: "PENDING" } });
      if (data.type === "approval.decided") set({ pendingApproval: undefined });
    };
    socket.onerror = () => set({ error: "实时连接已中断。" });
    set({ socket });
  },
  setMode: (mode) => set({ mode }),
  addMessage: (agent, text) => set((state) => ({
    workspaces: mapThread(state, (thread) => ({ ...thread, messages: [...thread.messages, { id: crypto.randomUUID(), agent, text, time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) } as Message] })),
  })),
  setState: (state) => set((current) => ({ workspaces: mapThread(current, (thread) => ({ ...thread, state })) })),
  sendPrompt: async (text) => {
    const state = get();
    if (state.backend === "offline") {
      state.addMessage("user", text);
      state.setState("PLANNING");
      setTimeout(() => state.addMessage("claude", "后端未启动：这是本地降级演示。"), 300);
      return;
    }
    try { await api.sendMessage(state.workspaceId, state.threadId, text, state.mode, state.draftAttachments.map((item) => item.id)); set({ draftAttachments: [] }); }
    catch (error) { set({ error: String(error) }); }
  },
  cancelRun: async () => {
    const state = get();
    try { await api.cancelRun(state.threadId); set({ pendingApproval: undefined }); }
    catch (error) { set({ error: String(error) }); }
  },
  upload: async (file) => {
    const state = get();
    if (state.backend === "offline") { state.addMessage("user", `已选择图片：${file.name}`); return; }
    try { const item = await api.uploadAttachment(state.workspaceId, state.threadId, file); set((current) => ({ draftAttachments: [...current.draftAttachments, item].slice(-8) })); }
    catch (error) { set({ error: String(error) }); }
  },
  removeAttachment: (id) => set((state) => ({ draftAttachments: state.draftAttachments.filter((item) => item.id !== id) })),
  newThread: async () => {
    const state = get();
    if (state.backend === "offline" || state.creatingThread || !state.workspaceId) return;
    set({ creatingThread: true });
    try {
      const item = await api.createThread(state.workspaceId, "新开发任务");
      const workspaces = await api.fetchWorkspaces();
      set({ workspaces });
      get().setSelection(state.workspaceId, item.id);
    } catch (error) {
      set({ error: String(error) });
    } finally {
      set({ creatingThread: false });
    }
  },
  openWorkspace: async (path) => {
    await api.createWorkspace(path);
    const workspaces = await api.fetchWorkspaces();
    const workspace = workspaces.find((item) => item.path.toLowerCase() === path.toLowerCase()) ?? workspaces.at(-1);
    if (!workspace) return;
    set({ workspaces });
    get().setSelection(workspace.id, workspace.threads[0]?.id ?? "");
  },
  decideApproval: async (approved) => {
    const state = get();
    if (!state.pendingApproval) return;
    try { await api.decideApproval(state.workspaceId, state.threadId, state.pendingApproval.id, approved); set({ pendingApproval: undefined }); }
    catch (error) { set({ error: String(error) }); }
  },
  gitAction: async (action, message = "") => {
    const state = get();
    if (state.pendingApproval) { set({ error: "请先处理当前待审批操作" }); return; }
    try { await api.requestGitAction(state.workspaceId, state.threadId, action, message); }
    catch (error) { set({ error: String(error) }); }
  },
  saveRemote: async (remote_url, vps_repo_path) => {
    const state = get();
    try { await api.saveWorkspaceRemote(state.workspaceId, { remote_url, vps_repo_path }); set({ remoteStatus: await api.fetchWorkspaceRemote(state.workspaceId) }); }
    catch (error) { set({ error: String(error) }); }
  },
  remoteGitAction: async (action) => {
    const state = get();
    if (state.pendingApproval) { set({ error: "请先处理当前待审批操作" }); return; }
    try { await api.requestRemoteGitAction(state.workspaceId, state.threadId, action); }
    catch (error) { set({ error: String(error) }); }
  },
  runTests: async () => {
    const state = get();
    if (state.pendingApproval) { set({ error: "请先处理当前待审批操作" }); return; }
    try { await api.requestTestRun(state.workspaceId, state.threadId); }
    catch (error) { set({ error: String(error) }); }
  },
}));
