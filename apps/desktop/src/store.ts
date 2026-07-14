import { create } from "zustand";
import * as api from "./api";
import type {
  ActivityStep,
  Agent,
  AgentEvent,
  Approval,
  ExecutionJob,
  GitStatus,
  Message,
  RunState,
  ThreadDetails,
  Workspace,
  WorkspaceRemoteStatus,
} from "./types";

export type Mode = "codex" | "claude";
export interface Notification {
  id: string;
  level: "error" | "info";
  message: string;
}
let notificationSequence = 0;
const activeStatesForStore = new Set<RunState>([
  "PLANNING",
  "WAITING_APPROVAL",
  "IMPLEMENTING",
  "TESTING",
  "REVIEWING",
  "FALLBACK_TO_CODEX",
]);

export const settleActivity = (
  activity: NonNullable<Message["activity"]>,
  status: "failed" | "cancelled",
  error?: string,
): NonNullable<Message["activity"]> => ({
  ...activity,
  status,
  error,
  completedAt: Date.now(),
  steps: activity.steps.map((step) =>
    step.status === "running"
      ? { ...step, status: "failed" as const, completedAt: Date.now() }
      : step,
  ),
});

interface Store {
  workspaces: Workspace[];
  workspaceId: string;
  threadId: string;
  mode: Mode;
  activeAgent?: Mode;
  backend: "connecting" | "online" | "offline";
  realtime: "disconnected" | "connecting" | "connected" | "reconnecting";
  error?: string;
  notifications: Notification[];
  socket?: WebSocket;
  pendingApproval?: Approval;
  details?: ThreadDetails;
  /** undefined = 正在读取；null = 读取失败或不可用。 */
  gitStatus?: GitStatus | null;
  remoteStatus?: WorkspaceRemoteStatus;
  executionJobs: ExecutionJob[];
  retryingJobId?: string;
  terminal: string[];
  terminalTruncated: boolean;
  clearTerminal: () => void;
  draftAttachments: {
    id: string;
    name: string;
    media_type: string;
    size: number;
  }[];
  drafts: Record<string, string>;
  setDraft: (threadId: string, text: string) => void;
  creatingThread: boolean;
  notify: (
    level: Notification["level"],
    message: string,
    timeoutMs?: number,
  ) => void;
  dismissNotification: (id: string) => void;
  runMeta?: { branch?: string; worktree?: string };
  initialize: () => Promise<void>;
  setSelection: (workspaceId: string, threadId: string) => void;
  setMode: (mode: Mode) => void;
  addMessage: (agent: Agent, text: string) => void;
  setState: (state: RunState) => void;
  sendPrompt: (text: string) => Promise<void>;
  cancelRun: () => Promise<void>;
  retryMessage: (messageId: string, content?: string) => Promise<void>;
  undoRun: (runId: string) => Promise<void>;
  upload: (file: File) => Promise<void>;
  removeAttachment: (id: string) => void;
  newThread: () => Promise<void>;
  renameThread: (threadId: string, title: string) => Promise<void>;
  removeThread: (threadId: string) => Promise<void>;
  openWorkspace: (path: string) => Promise<void>;
  provisionWorkspace: (value: {
    path: string;
    remote_url: string;
    mode: "init" | "clone";
    name?: string;
  }) => Promise<void>;
  removeWorkspace: (workspaceId: string) => Promise<void>;
  decideApproval: (
    approved: boolean,
    scope?: "once" | "thread",
  ) => Promise<void>;
  gitAction: (
    action: "commit" | "push" | "pull",
    message?: string,
  ) => Promise<void>;
  saveRemote: (remote_url: string, vps_repo_path: string) => Promise<void>;
  refreshRemote: () => Promise<void>;
  remoteGitAction: (
    action: "provision" | "repair_provision" | "fetch" | "pull",
  ) => Promise<void>;
  runTests: () => Promise<void>;
  refreshExecutionJobs: () => Promise<void>;
  retryExecutionJob: (jobId: string) => Promise<void>;
}

const mapThread = (
  state: Store,
  fn: (thread: Workspace["threads"][number]) => Workspace["threads"][number],
) =>
  state.workspaces.map((workspace) =>
    workspace.id !== state.workspaceId
      ? workspace
      : {
          ...workspace,
          threads: workspace.threads.map((thread) =>
            thread.id === state.threadId ? fn(thread) : thread,
          ),
        },
  );

const toolStep = (
  item: Record<string, unknown>,
  event: unknown,
): ActivityStep => {
  const reasoning = item.type === "reasoning";
  const kind = reasoning
    ? "reasoning"
    : item.type === "command_execution" || item.type === "commandExecution"
      ? "command"
      : item.type === "file_change" || item.type === "fileChange"
        ? "file"
        : "tool";
  const raw = String(
    item.text ??
      item.command ??
      item.path ??
      item.name ??
      item.query ??
      item.tool ??
      item.type ??
      "工具操作",
  );
  const failed = item.status === "failed" || Number(item.exit_code ?? 0) !== 0;
  return {
    id: String(item.id ?? `${kind}-${raw}`),
    kind,
    label: reasoning
      ? "思考"
      : kind === "command"
        ? "执行命令"
        : kind === "file"
          ? "修改文件"
          : item.type === "web_search"
            ? "搜索资料"
            : "调用工具",
    // 思考文本需要完整流式展示，不做单块截断；工具详情保持单行摘要。
    detail: reasoning || raw.length <= 180 ? raw : `${raw.slice(0, 177)}…`,
    status: failed
      ? "failed"
      : String(event).includes("completed")
        ? "completed"
        : "running",
    startedAt: Date.now(),
  };
};

/** 合并新旧步骤：保留最早的开始时间，并在离开 running 时记录结束时间。 */
const stampStep = (
  previous: ActivityStep,
  next: ActivityStep,
): ActivityStep => ({
  ...next,
  startedAt: previous.startedAt ?? next.startedAt,
  completedAt:
    next.status !== "running"
      ? (previous.completedAt ?? Date.now())
      : undefined,
});

export const useStore = create<Store>((set, get) => ({
  workspaces: [],
  workspaceId: "",
  threadId: "",
  mode: "codex",
  backend: "connecting",
  realtime: "disconnected",
  terminal: [],
  terminalTruncated: false,
  clearTerminal: () => set({ terminal: [], terminalTruncated: false }),
  notifications: [],
  executionJobs: [],
  draftAttachments: [],
  drafts: {},
  setDraft: (threadId, text) =>
    set((state) => ({ drafts: { ...state.drafts, [threadId]: text } })),
  creatingThread: false,
  notify: (level, message, timeoutMs = level === "info" ? 5000 : undefined) => {
    const id = `notification-${Date.now()}-${notificationSequence++}`;
    set((state) => ({
      notifications: [...state.notifications, { id, level, message }],
    }));
    if (timeoutMs)
      window.setTimeout(() => get().dismissNotification(id), timeoutMs);
  },
  dismissNotification: (id) =>
    set((state) => ({
      notifications: state.notifications.filter((item) => item.id !== id),
    })),
  initialize: async () => {
    set({ backend: "connecting" });
    for (let attempt = 0; attempt < 60; attempt += 1) {
      try {
        const workspaces = await api.fetchWorkspaces();
        const workspaceId = workspaces[0]?.id ?? "";
        const threadId = workspaces[0]?.threads[0]?.id ?? "";
        set({
          workspaces,
          workspaceId,
          threadId,
          backend: "online",
        });
        get().setSelection(workspaceId, threadId);
        return;
      } catch {
        await new Promise((resolve) =>
          window.setTimeout(resolve, Math.min(250 + attempt * 150, 1500)),
        );
      }
    }
    set({ backend: "offline" });
    get().notify("error", "本地后端启动超时，请重启应用或检查 8876 端口。");
  },
  setSelection: (workspaceId, threadId) => {
    get().socket?.close();
    set({
      workspaceId,
      threadId,
      pendingApproval: undefined,
      details: undefined,
      gitStatus: undefined,
      remoteStatus: undefined,
      executionJobs: [],
      terminal: [],
      terminalTruncated: false,
      runMeta: undefined,
      activeAgent: undefined,
      socket: undefined,
      realtime: threadId ? "connecting" : "disconnected",
    });
    if (get().backend !== "online" || !threadId) return;
    void api
      .fetchApprovals(workspaceId, threadId)
      .then((items) => set({ pendingApproval: items[0] }))
      .catch(() => undefined);
    const refreshDetails = () =>
      void api
        .fetchThreadDetails(workspaceId, threadId)
        .then((details) => set({ details }))
        .catch(() => undefined);
    refreshDetails();
    void api
      .fetchGitStatus(workspaceId)
      .then((gitStatus) => set({ gitStatus }))
      .catch(() => {
        if (get().workspaceId === workspaceId) set({ gitStatus: null });
      });
    void api
      .fetchWorkspaceRemote(workspaceId)
      .then((remoteStatus) => set({ remoteStatus }))
      .catch(() => undefined);
    void get().refreshExecutionJobs();
    let reconnectAttempt = 0;
    let reconnectTimer: number | undefined;
    // Agent 输出的 chunk 是突发到达的；缓冲后按固定节拍释放，
    // 让回复以稳定的打字节奏出现而不是一坨一坨跳动。
    const streamBuffers = new Map<
      string,
      { agent: Agent; target: string; shown: number }
    >();
    let streamTimer: number | undefined;
    const stopStreamTimer = () => {
      if (streamTimer !== undefined) window.clearInterval(streamTimer);
      streamTimer = undefined;
    };
    const drainStreamBuffers = () => {
      if (get().workspaceId !== workspaceId || get().threadId !== threadId) {
        streamBuffers.clear();
        stopStreamTimer();
        return;
      }
      const updates = new Map<string, { agent: Agent; text: string }>();
      for (const [id, buffer] of streamBuffers) {
        if (buffer.shown >= buffer.target.length) continue;
        const backlog = buffer.target.length - buffer.shown;
        buffer.shown = Math.min(
          buffer.target.length,
          buffer.shown + Math.max(16, Math.ceil(backlog * 0.3)),
        );
        updates.set(id, {
          agent: buffer.agent,
          text: buffer.target.slice(0, buffer.shown),
        });
      }
      if (updates.size)
        set((state) => ({
          workspaces: mapThread(state, (thread) => {
            let messages = thread.messages;
            for (const [id, update] of updates) {
              messages = messages.some((item) => item.id === id)
                ? messages.map((item) =>
                    item.id === id ? { ...item, text: update.text } : item,
                  )
                : [
                    ...messages,
                    { id, agent: update.agent, text: update.text, time: "" },
                  ];
            }
            return { ...thread, messages };
          }),
        }));
      if (
        [...streamBuffers.values()].every(
          (buffer) => buffer.shown >= buffer.target.length,
        )
      )
        stopStreamTimer();
    };
    const connect = () => {
      if (
        get().workspaceId !== workspaceId ||
        get().threadId !== threadId ||
        get().backend !== "online"
      )
        return;
      set({ realtime: reconnectAttempt ? "reconnecting" : "connecting" });
      void api
        .threadSocket(threadId)
        .then((socket) => {
          if (
            get().workspaceId !== workspaceId ||
            get().threadId !== threadId
          ) {
            socket.close();
            return;
          }
          socket.onopen = () => {
            reconnectAttempt = 0;
            set({ socket, realtime: "connected" });
            refreshDetails();
            void api
              .fetchApprovals(workspaceId, threadId)
              .then((items) => set({ pendingApproval: items[0] }))
              .catch(() => undefined);
            void get().refreshExecutionJobs();
          };
          socket.onmessage = (event) => {
            let data: AgentEvent;
            try {
              data = JSON.parse(event.data) as AgentEvent;
            } catch (error) {
              console.warn("忽略无法解析的实时消息", error);
              return;
            }
            const payload = data.payload;
            if (
              data.type === "agent.delta" &&
              payload.agent &&
              payload.text &&
              data.run_id
            ) {
              const id = `stream-${data.run_id}`;
              const buffer = streamBuffers.get(id) ?? {
                agent: payload.agent as Agent,
                target: "",
                shown: 0,
              };
              buffer.target += String(payload.text);
              streamBuffers.set(id, buffer);
              if (streamTimer === undefined)
                streamTimer = window.setInterval(drainStreamBuffers, 40);
            }
            if (data.type === "message.created" && data.run_id)
              set((state) => ({
                workspaces: mapThread(state, (thread) => {
                  const activityId = `activity-${data.run_id}`;
                  const streamId = `stream-${data.run_id}`;
                  const activity = thread.messages.find(
                    (item) => item.id === activityId,
                  );
                  if (!activity?.activity) return thread;
                  const completed = {
                    ...activity,
                    activity: {
                      ...activity.activity,
                      status: "completed" as const,
                      completedAt: Date.now(),
                      steps: activity.activity.steps.map((step) =>
                        step.status === "running"
                          ? {
                              ...step,
                              status: "completed" as const,
                              completedAt: Date.now(),
                            }
                          : step,
                      ),
                    },
                  };
                  const withoutActivity = thread.messages.filter(
                    (item) => item.id !== activityId,
                  );
                  const responseIndex = withoutActivity.findIndex(
                    (item) => item.id === streamId,
                  );
                  if (responseIndex < 0)
                    return {
                      ...thread,
                      messages: [...withoutActivity, completed],
                    };
                  return {
                    ...thread,
                    messages: [
                      ...withoutActivity.slice(0, responseIndex + 1),
                      completed,
                      ...withoutActivity.slice(responseIndex + 1),
                    ],
                  };
                }),
              }));
            if (
              data.type === "message.created" &&
              payload.role &&
              payload.content !== undefined
            ) {
              // 持久化消息是权威全文，丢弃该流的未释放缓冲，立即收尾。
              if (data.run_id) streamBuffers.delete(`stream-${data.run_id}`);
              set((state) => ({
                workspaces: mapThread(state, (thread) => {
                  const streamId = data.run_id ? `stream-${data.run_id}` : "";
                  const hasStream = thread.messages.some(
                    (item) => item.id === streamId,
                  );
                  const attachments = Array.isArray(payload.attachments)
                    ? (payload.attachments as Message["attachments"])
                    : [];
                  return {
                    ...thread,
                    messages: hasStream
                      ? thread.messages.map((item) =>
                          item.id === streamId
                            ? {
                                ...item,
                                id: String(payload.id ?? crypto.randomUUID()),
                                text: String(payload.content),
                                attachments,
                                time: new Date().toLocaleTimeString("zh-CN", {
                                  hour: "2-digit",
                                  minute: "2-digit",
                                }),
                              }
                            : item,
                        )
                      : [
                          ...thread.messages,
                          {
                            id: String(payload.id ?? crypto.randomUUID()),
                            agent: payload.role as Agent,
                            text: String(payload.content),
                            attachments,
                            time: new Date().toLocaleTimeString("zh-CN", {
                              hour: "2-digit",
                              minute: "2-digit",
                            }),
                          },
                        ],
                  };
                }),
              }));
            }
            if (
              data.type === "agent.tool" &&
              payload.item &&
              ![
                "userMessage",
                "agentMessage",
                "user_message",
                "agent_message",
              ].includes(String((payload.item as Record<string, unknown>).type))
            ) {
              const item = payload.item as Record<string, unknown>;
              const step = toolStep(item, payload.event);
              set((state) => {
                const activityId = `activity-${data.run_id ?? "current"}`;
                const workspace = state.workspaces.find(
                  (entry) => entry.id === state.workspaceId,
                );
                const thread = workspace?.threads.find(
                  (entry) => entry.id === state.threadId,
                );
                const current = thread?.messages.find(
                  (entry) => entry.id === activityId,
                );
                return {
                  workspaces: mapThread(state, (entry) => ({
                    ...entry,
                    messages: current
                      ? entry.messages.map((message) =>
                          message.id === activityId && message.activity
                            ? {
                                ...message,
                                activity: {
                                  ...message.activity,
                                  status: "running",
                                  steps: message.activity.steps.some(
                                    (value) => value.id === step.id,
                                  )
                                    ? message.activity.steps.map((value) => {
                                        if (value.id !== step.id) return value;
                                        if (payload.event === "delta") {
                                          const merged = `${value.detail ?? ""}${step.detail ?? ""}`;
                                          return stampStep(value, {
                                            ...step,
                                            detail:
                                              merged.length > 6000
                                                ? `…${merged.slice(-6000)}`
                                                : merged,
                                          });
                                        }
                                        return stampStep(value, {
                                          ...step,
                                          detail:
                                            step.detail === "reasoning"
                                              ? value.detail
                                              : step.detail,
                                        });
                                      })
                                    : [...message.activity.steps, step],
                                },
                              }
                            : message,
                        )
                      : [
                          ...entry.messages,
                          {
                            id: activityId,
                            agent: "system",
                            text: "",
                            time: "",
                            activity: {
                              runId: String(data.run_id ?? "current"),
                              agent: String(payload.agent),
                              status: "running",
                              steps: [step],
                              startedAt: Date.now(),
                            },
                          },
                        ],
                  })),
                };
              });
            }
            if (data.type === "run.state_changed" && payload.state) {
              if (
                activeStatesForStore.has(payload.state as RunState) &&
                (payload.agent === "codex" || payload.agent === "claude")
              )
                set({ activeAgent: payload.agent });
              get().setState(payload.state as RunState);
              if (
                data.run_id &&
                activeStatesForStore.has(payload.state as RunState)
              )
                set((state) => {
                  const activityId = `activity-${data.run_id}`;
                  return {
                    workspaces: mapThread(state, (thread) =>
                      thread.messages.some((item) => item.id === activityId)
                        ? thread
                        : {
                            ...thread,
                            messages: [
                              ...thread.messages,
                              {
                                id: activityId,
                                agent: "system",
                                text: "",
                                time: "",
                                activity: {
                                  runId: String(data.run_id),
                                  agent: String(payload.agent ?? state.mode),
                                  status: "running",
                                  steps: [],
                                  startedAt: Date.now(),
                                },
                              },
                            ],
                          },
                    ),
                  };
                });
            }
            if (data.type === "test.result")
              get().addMessage(
                "system",
                `测试结果：\n${String(payload.output)}`,
              );
            if (data.type === "terminal.output" && payload.text)
              set((state) => {
                const appended = [...state.terminal, String(payload.text)];
                return {
                  terminal: appended.slice(-500),
                  terminalTruncated:
                    state.terminalTruncated || appended.length > 500,
                };
              });
            if (data.type === "run.output" && payload.kind === "git")
              set({
                runMeta: {
                  branch: String(payload.branch ?? ""),
                  worktree: String(payload.worktree ?? ""),
                },
              });
            if (
              ["test.result", "run.completed", "run.output"].includes(data.type)
            ) {
              refreshDetails();
              void api
                .fetchGitStatus(workspaceId)
                .then((gitStatus) => set({ gitStatus }))
                .catch(() => {
                  if (get().workspaceId === workspaceId)
                    set({ gitStatus: null });
                });
              void api
                .fetchWorkspaceRemote(workspaceId)
                .then((remoteStatus) => set({ remoteStatus }))
                .catch(() => undefined);
            }
            if (
              data.type.startsWith("execution.") ||
              data.type === "approval.decided" ||
              ((data.type === "run.output" || data.type === "error") &&
                payload.job_id)
            )
              void get().refreshExecutionJobs();
            if (data.type === "error")
              set((state) => {
                const message = String(
                  payload.message || "Agent 运行失败，请重试本轮任务。",
                );
                const activityId = `activity-${data.run_id ?? "current"}`;
                return {
                  error: message,
                  workspaces: mapThread(state, (thread) => ({
                    ...thread,
                    messages: thread.messages.map((entry) =>
                      entry.id === activityId && entry.activity
                        ? {
                            ...entry,
                            activity: settleActivity(
                              entry.activity,
                              "failed",
                              message,
                            ),
                          }
                        : entry,
                    ),
                  })),
                };
              });
            if (data.type === "approval.required" && payload.id)
              set({
                pendingApproval: {
                  id: String(payload.id),
                  action: String(payload.action),
                  reason: String(payload.reason),
                  status: "PENDING",
                },
              });
            if (data.type === "approval.decided")
              set({ pendingApproval: undefined });
          };
          socket.onerror = () => set({ realtime: "reconnecting" });
          socket.onclose = () => {
            if (
              get().workspaceId !== workspaceId ||
              get().threadId !== threadId ||
              get().backend !== "online"
            )
              return;
            set({ socket: undefined, realtime: "reconnecting" });
            const delay = Math.min(1000 * 2 ** reconnectAttempt, 30_000);
            reconnectAttempt += 1;
            if (reconnectTimer !== undefined)
              window.clearTimeout(reconnectTimer);
            reconnectTimer = window.setTimeout(connect, delay);
          };
          set({ socket });
        })
        .catch(() => {
          if (get().workspaceId !== workspaceId || get().threadId !== threadId)
            return;
          set({ realtime: "reconnecting" });
          const delay = Math.min(1000 * 2 ** reconnectAttempt, 30_000);
          reconnectAttempt += 1;
          reconnectTimer = window.setTimeout(connect, delay);
        });
    };
    connect();
  },
  setMode: (mode) => set({ mode }),
  addMessage: (agent, text) =>
    set((state) => ({
      workspaces: mapThread(state, (thread) => ({
        ...thread,
        messages: [
          ...thread.messages,
          {
            id: crypto.randomUUID(),
            agent,
            text,
            time: new Date().toLocaleTimeString("zh-CN", {
              hour: "2-digit",
              minute: "2-digit",
            }),
          } as Message,
        ],
      })),
    })),
  setState: (state) =>
    set((current) => ({
      activeAgent: activeStatesForStore.has(state)
        ? current.activeAgent
        : undefined,
      workspaces: mapThread(current, (thread) => ({ ...thread, state })),
    })),
  sendPrompt: async (text) => {
    const state = get();
    const selectedThread = state.workspaces
      .find((workspace) => workspace.id === state.workspaceId)
      ?.threads.find((thread) => thread.id === state.threadId);
    const autoTitle =
      selectedThread?.title === "新开发任务" &&
      !selectedThread.messages.some((message) => message.agent === "user")
        ? text.trim().replace(/\s+/g, " ").slice(0, 20)
        : "";
    try {
      const draft = state.draftAttachments;
      const result = (await api.sendMessage(
        state.workspaceId,
        state.threadId,
        text,
        state.mode,
        draft.map((item) => item.id),
      )) as { message_id: string; attachments?: Message["attachments"] };
      set((current) => ({
        activeAgent: state.mode,
        draftAttachments: [],
        workspaces: mapThread(current, (thread) => {
          const title = autoTitle || thread.title;
          if (thread.messages.some((item) => item.id === result.message_id))
            return {
              ...thread,
              title,
              messages: thread.messages.map((item) =>
                item.id === result.message_id
                  ? { ...item, attachments: result.attachments ?? draft }
                  : item,
              ),
            };
          return {
            ...thread,
            title,
            messages: [
              ...thread.messages,
              {
                id: result.message_id,
                agent: "user",
                text,
                time: "",
                attachments: result.attachments ?? draft,
              },
            ],
          };
        }),
      }));
      if (autoTitle) {
        try {
          await api.updateThread(state.workspaceId, state.threadId, autoTitle);
        } catch (error) {
          get().notify("info", `任务标题保存失败：${String(error)}`);
        }
      }
    } catch (error) {
      get().notify("error", String(error));
      throw error;
    }
  },
  cancelRun: async () => {
    const state = get();
    try {
      await api.cancelRun(state.threadId);
      state.setState("CREATED");
      set({ pendingApproval: undefined });
    } catch (error) {
      get().notify("error", String(error));
    }
  },
  retryMessage: async (messageId, content) => {
    const state = get();
    try {
      await api.retryMessage(
        state.workspaceId,
        state.threadId,
        messageId,
        content,
      );
      if (content !== undefined) {
        set((current) => ({
          workspaces: current.workspaces.map((workspace) =>
            workspace.id !== state.workspaceId
              ? workspace
              : {
                  ...workspace,
                  threads: workspace.threads.map((thread) =>
                    thread.id !== state.threadId
                      ? thread
                      : {
                          ...thread,
                          messages: thread.messages.map((message) =>
                            message.id === messageId
                              ? { ...message, text: content }
                              : message,
                          ),
                        },
                  ),
                },
          ),
        }));
      }
    } catch (error) {
      get().notify("error", String(error));
      throw error;
    }
  },
  undoRun: async (runId) => {
    const state = get();
    try {
      await api.undoRun(state.workspaceId, state.threadId, runId);
    } catch (error) {
      get().notify("error", String(error));
    }
  },
  upload: async (file) => {
    const state = get();
    try {
      const item = await api.uploadAttachment(
        state.workspaceId,
        state.threadId,
        file,
      );
      set((current) => ({
        draftAttachments: [...current.draftAttachments, item].slice(-8),
      }));
    } catch (error) {
      get().notify("error", String(error));
    }
  },
  removeAttachment: (id) =>
    set((state) => ({
      draftAttachments: state.draftAttachments.filter((item) => item.id !== id),
    })),
  newThread: async () => {
    const state = get();
    if (
      state.backend === "offline" ||
      state.creatingThread ||
      !state.workspaceId
    )
      return;
    set({ creatingThread: true });
    try {
      const item = await api.createThread(state.workspaceId, "新开发任务");
      const workspaces = await api.fetchWorkspaces();
      set({ workspaces });
      get().setSelection(state.workspaceId, item.id);
    } catch (error) {
      get().notify("error", String(error));
    } finally {
      set({ creatingThread: false });
    }
  },
  renameThread: async (threadId, title) => {
    const normalized = title.trim().slice(0, 200);
    if (!normalized) return;
    const { workspaceId } = get();
    try {
      await api.updateThread(workspaceId, threadId, normalized);
      set((state) => ({
        workspaces: state.workspaces.map((workspace) =>
          workspace.id !== workspaceId
            ? workspace
            : {
                ...workspace,
                threads: workspace.threads.map((thread) =>
                  thread.id === threadId
                    ? { ...thread, title: normalized }
                    : thread,
                ),
              },
        ),
      }));
    } catch (error) {
      get().notify("error", `任务重命名失败：${String(error)}`);
      throw error;
    }
  },
  removeThread: async (threadId) => {
    const { workspaceId } = get();
    try {
      await api.removeThread(workspaceId, threadId);
      const workspaces = await api.fetchWorkspaces();
      const workspace = workspaces.find((item) => item.id === workspaceId);
      const nextThreadId = workspace?.threads[0]?.id ?? "";
      set({ workspaces, threadId: nextThreadId });
      if (nextThreadId) get().setSelection(workspaceId, nextThreadId);
    } catch (error) {
      get().notify("error", `删除任务失败：${String(error)}`);
      throw error;
    }
  },
  openWorkspace: async (path) => {
    await api.createWorkspace(path);
    const workspaces = await api.fetchWorkspaces();
    const workspace =
      workspaces.find(
        (item) => item.path.toLowerCase() === path.toLowerCase(),
      ) ?? workspaces.at(-1);
    if (!workspace) return;
    set({ workspaces });
    get().setSelection(workspace.id, workspace.threads[0]?.id ?? "");
  },
  provisionWorkspace: async (value) => {
    const created = await api.provisionWorkspace(value);
    const workspaces = await api.fetchWorkspaces();
    set({ workspaces });
    get().setSelection(created.id, created.threads[0]?.id ?? "");
  },
  removeWorkspace: async (workspaceId) => {
    try {
      get().socket?.close();
      await api.removeWorkspace(workspaceId);
      const workspaces = await api.fetchWorkspaces();
      const next = workspaces[0];
      set({
        workspaces,
        workspaceId: next?.id ?? "",
        threadId: next?.threads[0]?.id ?? "",
        details: undefined,
        gitStatus: undefined,
        remoteStatus: undefined,
      });
      if (next) get().setSelection(next.id, next.threads[0]?.id ?? "");
    } catch (error) {
      get().notify("error", String(error));
      throw error;
    }
  },
  decideApproval: async (approved, scope = "once") => {
    const state = get();
    if (!state.pendingApproval) return;
    try {
      await api.decideApproval(
        state.workspaceId,
        state.threadId,
        state.pendingApproval.id,
        approved,
        "",
        scope,
      );
      set({ pendingApproval: undefined });
      await get().refreshExecutionJobs();
    } catch (error) {
      get().notify("error", String(error));
    }
  },
  gitAction: async (action, message = "") => {
    const state = get();
    if (state.pendingApproval) {
      get().notify("error", "请先处理当前待审批操作");
      return;
    }
    try {
      await api.requestGitAction(
        state.workspaceId,
        state.threadId,
        action,
        message,
      );
    } catch (error) {
      get().notify("error", String(error));
    }
  },
  saveRemote: async (remote_url, vps_repo_path) => {
    const state = get();
    try {
      await api.saveWorkspaceRemote(state.workspaceId, {
        remote_url,
        vps_repo_path,
      });
      set({ remoteStatus: await api.fetchWorkspaceRemote(state.workspaceId) });
    } catch (error) {
      get().notify("error", String(error));
      throw error;
    }
  },
  refreshRemote: async () => {
    const { workspaceId, backend } = get();
    if (backend !== "online" || !workspaceId) return;
    try {
      set({
        remoteStatus: await api.fetchWorkspaceRemote(workspaceId),
      });
    } catch (error) {
      get().notify("info", `VPS 状态刷新失败：${String(error)}`);
      throw error;
    }
  },
  remoteGitAction: async (action) => {
    const state = get();
    const replacesLegacyCloneApproval =
      action === "provision" &&
      state.pendingApproval?.action === "remote_git_provision";
    if (state.pendingApproval && !replacesLegacyCloneApproval) {
      const error = new Error("请先处理当前待审批操作");
      get().notify("error", error.message);
      throw error;
    }
    try {
      await api.requestRemoteGitAction(
        state.workspaceId,
        state.threadId,
        action,
      );
      const executionJobs = await api.fetchExecutionJobs(
        state.workspaceId,
        state.threadId,
      );
      set({
        executionJobs,
        ...(replacesLegacyCloneApproval ? { pendingApproval: undefined } : {}),
      });
    } catch (error) {
      get().notify("error", String(error));
      throw error;
    }
  },
  runTests: async () => {
    const state = get();
    if (state.pendingApproval) {
      get().notify("error", "请先处理当前待审批操作");
      return;
    }
    try {
      await api.requestTestRun(state.workspaceId, state.threadId);
    } catch (error) {
      get().notify("error", String(error));
    }
  },
  refreshExecutionJobs: async () => {
    const { workspaceId, threadId, backend } = get();
    if (backend !== "online" || !workspaceId || !threadId) return;
    try {
      set({
        executionJobs: await api.fetchExecutionJobs(workspaceId, threadId),
      });
    } catch (error) {
      get().notify("info", `无法刷新恢复任务：${String(error)}`);
    }
  },
  retryExecutionJob: async (jobId) => {
    const { workspaceId, threadId, retryingJobId } = get();
    if (retryingJobId) return;
    set({ retryingJobId: jobId });
    try {
      await api.retryExecutionJob(workspaceId, threadId, jobId);
      await get().refreshExecutionJobs();
    } catch (error) {
      get().notify("error", `重试任务失败：${String(error)}`);
    } finally {
      set({ retryingJobId: undefined });
    }
  },
}));
