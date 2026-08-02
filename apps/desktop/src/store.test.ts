import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import { mergeCollaborationEvent, settleActivity, useStore } from "./store";
import type { AgentEvent } from "./types";
import type { CollaborationTimeline } from "./types";

vi.mock("./api", () => ({
  fetchApprovals: vi.fn(async () => []),
  fetchThreadDetails: vi.fn(async () => ({
    files: [],
    diff: "",
    tests: [],
    worktree: "",
    codex_session_id: "",
    runs: [],
  })),
  fetchGitStatus: vi.fn(async () => undefined),
  fetchWorkspaceRemote: vi.fn(async () => undefined),
  fetchExecutionJobs: vi.fn(async () => []),
  fetchCurrentCollaboration: vi.fn(async () => undefined),
  fetchCollaborationFindings: vi.fn(async () => []),
  fetchCapabilities: vi.fn(async () => ({
    smart_collaboration_enabled: false,
  })),
  sendMessage: vi.fn(async () => ({
    message_id: "message-1",
    thread_title: "实现一个面向专业交付的任务管理功能，并补",
    attachments: [],
  })),
  updateThread: vi.fn(async () => ({})),
  threadSocket: vi.fn(),
}));

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  useStore.setState({
    notifications: [],
    workspaces: [],
    workspaceId: "",
    threadId: "",
    socket: undefined,
    realtime: "disconnected",
    error: undefined,
    activeAgent: undefined,
    mode: "codex",
    smartCollaborationEnabled: false,
    collaborations: {},
  });
});

describe("collaboration event timeline", () => {
  const event = (
    type: string,
    sequence: number,
    payload: Record<string, unknown> = {},
  ): AgentEvent => ({
    type,
    thread_id: "thread",
    run_id: "run-1",
    sequence,
    payload,
  });

  it("merges stage, agent, findings, waiting and completion events", () => {
    const events = [
      event("collaboration.started", 1, {
        state: "CLARIFYING",
        round: 1,
        max_rounds: 3,
      }),
      event("collaboration.stage_changed", 2, { state: "IMPLEMENTING" }),
      event("collaboration.agent_changed", 3, { current_agent: "codex" }),
      event("collaboration.handoff_created", 4),
      event("collaboration.review_received", 5),
      event("collaboration.findings_updated", 6, {
        open_blocking_count: 2,
      }),
      event("collaboration.waiting_user", 7, {
        state: "WAITING_USER",
        reason: "请选择",
      }),
      event("collaboration.resumed", 8, { state: "FIXING" }),
      event("collaboration.completed", 9, { state: "COMPLETED" }),
    ];
    const merged = events.reduce<CollaborationTimeline | undefined>(
      (current, item) => mergeCollaborationEvent(current, item),
      undefined,
    );
    expect(merged).toMatchObject({
      runId: "run-1",
      state: "COMPLETED",
      status: "completed",
      findingsCount: 2,
      currentAgent: "codex",
      waitingReason: undefined,
      lastSequence: 9,
    });
    expect(merged?.stages.fix).toBe("completed");
  });

  it("ignores stale events and tolerates missing or unrelated events", () => {
    const latest = mergeCollaborationEvent(
      undefined,
      event("collaboration.stage_changed", 5, { state: "REVIEWING" }),
    );
    expect(
      mergeCollaborationEvent(
        latest,
        event("collaboration.stage_changed", 3, { state: "DRAFT" }),
      ),
    ).toBe(latest);
    expect(
      mergeCollaborationEvent(latest, {
        type: "run.output",
        thread_id: "thread",
        sequence: 6,
        payload: {},
      }),
    ).toBe(latest);
    expect(
      mergeCollaborationEvent(undefined, {
        type: "collaboration.stage_changed",
        thread_id: "thread",
        sequence: 1,
        payload: {},
      }),
    ).toBeUndefined();
  });
});

const connectThread = async () => {
  const socket = { close: vi.fn() } as Record<string, unknown>;
  vi.mocked(api.threadSocket).mockResolvedValue(socket as unknown as WebSocket);
  useStore.setState({
    backend: "online",
    workspaceId: "",
    threadId: "",
    workspaces: [
      {
        id: "workspace",
        name: "Project",
        path: "D:/Project",
        threads: [
          { id: "thread", title: "Task", state: "CREATED", messages: [] },
        ],
      },
    ],
  });
  useStore.getState().setSelection("workspace", "thread");
  await vi.waitFor(() => expect(socket.onmessage).toBeTypeOf("function"));
  return socket;
};

const emitSocketEvent = (
  socket: Record<string, unknown>,
  value: Record<string, unknown>,
) => {
  const onmessage = socket.onmessage as
    ((event: { data: string }) => void) | undefined;
  onmessage?.({ data: JSON.stringify(value) });
};

const selectedMessages = () =>
  useStore.getState().workspaces[0].threads[0].messages;

describe("notifications", () => {
  it("stacks persistent errors and dismisses one explicitly", () => {
    const store = useStore.getState();
    store.notify("error", "first");
    store.notify("error", "second");

    expect(
      useStore.getState().notifications.map((item) => item.message),
    ).toEqual(["first", "second"]);
    store.dismissNotification(useStore.getState().notifications[0].id);
    expect(
      useStore.getState().notifications.map((item) => item.message),
    ).toEqual(["second"]);
  });

  it("automatically dismisses informational notifications", () => {
    vi.useFakeTimers();
    useStore.getState().notify("info", "后台刷新失败");
    expect(useStore.getState().notifications).toHaveLength(1);

    vi.advanceTimersByTime(5000);
    expect(useStore.getState().notifications).toHaveLength(0);
  });
});

describe("composer drafts", () => {
  it("keeps an independent draft per thread", () => {
    const store = useStore.getState();
    store.setDraft("thread-a", "给任务 A 的草稿");
    store.setDraft("thread-b", "任务 B");
    store.setDraft("thread-b", "任务 B 的新草稿");

    expect(useStore.getState().drafts["thread-a"]).toBe("给任务 A 的草稿");
    expect(useStore.getState().drafts["thread-b"]).toBe("任务 B 的新草稿");
  });
});

describe("repository status", () => {
  it("marks the git status unavailable when the fetch fails", async () => {
    vi.mocked(api.fetchGitStatus).mockRejectedValueOnce(
      new Error("not a repository"),
    );
    await connectThread();

    await vi.waitFor(() => expect(useStore.getState().gitStatus).toBeNull());
  });
});

describe("message timeline", () => {
  it("timestamps realtime messages that arrive without a stream", async () => {
    const socket = await connectThread();
    emitSocketEvent(socket, {
      type: "message.created",
      thread_id: "thread",
      payload: { id: "message-9", role: "system", content: "测试完成" },
    });

    const message = selectedMessages().find((item) => item.id === "message-9");
    expect(message?.time).toBeTruthy();
  });
});

describe("terminal output", () => {
  it("caps the log at 500 lines and reports truncation once cleared", async () => {
    const socket = await connectThread();
    for (let index = 0; index < 501; index += 1)
      emitSocketEvent(socket, {
        type: "terminal.output",
        thread_id: "thread",
        payload: { text: `line-${index}` },
      });

    const state = useStore.getState();
    expect(state.terminal).toHaveLength(500);
    expect(state.terminal[0]).toBe("line-1");
    expect(state.terminalTruncated).toBe(true);

    state.clearTerminal();
    expect(useStore.getState().terminal).toHaveLength(0);
    expect(useStore.getState().terminalTruncated).toBe(false);
  });
});

describe("thread management", () => {
  it("refreshes smart collaboration capability and leaves smart mode when disabled", async () => {
    vi.mocked(api.fetchCapabilities).mockResolvedValue({
      smart_collaboration_enabled: true,
    });
    await useStore.getState().refreshCapabilities();
    expect(useStore.getState().smartCollaborationEnabled).toBe(true);

    useStore.setState({ mode: "smart" });
    vi.mocked(api.fetchCapabilities).mockResolvedValue({
      smart_collaboration_enabled: false,
    });
    await useStore.getState().refreshCapabilities();

    expect(useStore.getState().smartCollaborationEnabled).toBe(false);
    expect(useStore.getState().mode).toBe("codex");
  });

  it("sends the selected smart collaboration mode", async () => {
    useStore.setState({
      workspaceId: "workspace",
      threadId: "thread",
      mode: "smart",
      smartCollaborationEnabled: true,
      workspaces: [
        {
          id: "workspace",
          name: "Project",
          path: "D:/Project",
          threads: [
            { id: "thread", title: "Task", state: "CREATED", messages: [] },
          ],
        },
      ],
    });

    await useStore.getState().sendPrompt("实现并审查这一功能");
    expect(api.sendMessage).toHaveBeenCalledWith(
      "workspace",
      "thread",
      "实现并审查这一功能",
      "smart",
      [],
    );
  });

  it("uses the title derived by the backend from the first user message", async () => {
    useStore.setState({
      workspaceId: "workspace",
      threadId: "thread",
      mode: "codex",
      workspaces: [
        {
          id: "workspace",
          name: "Project",
          path: "D:/Project",
          threads: [
            {
              id: "thread",
              title: "新开发任务",
              state: "CREATED",
              messages: [],
            },
          ],
        },
      ],
    });

    await useStore
      .getState()
      .sendPrompt("实现一个面向专业交付的任务管理功能，并补齐测试");

    expect(api.updateThread).not.toHaveBeenCalled();
    expect(useStore.getState().workspaces[0].threads[0].title).toBe(
      "实现一个面向专业交付的任务管理功能，并补",
    );
  });
});

describe("activity terminal states", () => {
  it("stops every running step when a run fails", () => {
    const result = settleActivity(
      {
        runId: "run-1",
        agent: "codex",
        status: "running",
        steps: [
          {
            id: "done",
            kind: "command",
            label: "执行命令",
            status: "completed",
          },
          {
            id: "thinking",
            kind: "tool",
            label: "思考摘要",
            status: "running",
          },
        ],
      },
      "failed",
      "Agent 运行失败",
    );

    expect(result.status).toBe("failed");
    expect(result.steps.map((step) => step.status)).toEqual([
      "completed",
      "failed",
    ]);
    expect(result.completedAt).toBeTypeOf("number");
  });
});

describe("thread realtime event merging", () => {
  it("merges agent deltas into one streaming placeholder", async () => {
    vi.useFakeTimers();
    const socket = await connectThread();

    emitSocketEvent(socket, {
      type: "agent.delta",
      run_id: "run-1",
      payload: { agent: "codex", text: "第一段" },
    });
    emitSocketEvent(socket, {
      type: "agent.delta",
      run_id: "run-1",
      payload: { agent: "codex", text: "第二段" },
    });
    vi.advanceTimersByTime(400);

    expect(selectedMessages()).toMatchObject([
      { id: "stream-run-1", agent: "codex", text: "第一段第二段" },
    ]);
  });

  it("releases buffered deltas at a steady pace instead of in bursts", async () => {
    vi.useFakeTimers();
    const socket = await connectThread();

    emitSocketEvent(socket, {
      type: "agent.delta",
      run_id: "run-1",
      payload: { agent: "codex", text: "字".repeat(400) },
    });
    vi.advanceTimersByTime(40);
    const first = selectedMessages()[0]?.text.length ?? 0;
    expect(first).toBeGreaterThan(0);
    expect(first).toBeLessThan(400);

    vi.advanceTimersByTime(40);
    const second = selectedMessages()[0].text.length;
    expect(second).toBeGreaterThan(first);

    vi.advanceTimersByTime(2000);
    expect(selectedMessages()[0].text.length).toBe(400);
  });

  it("replaces the stream placeholder with the persisted message", async () => {
    vi.useFakeTimers();
    const socket = await connectThread();
    emitSocketEvent(socket, {
      type: "agent.delta",
      run_id: "run-2",
      payload: { agent: "codex", text: "草稿" },
    });
    vi.advanceTimersByTime(40);

    emitSocketEvent(socket, {
      type: "message.created",
      run_id: "run-2",
      payload: {
        id: "message-final",
        role: "codex",
        content: "最终回答",
        attachments: [],
      },
    });

    expect(selectedMessages()).toHaveLength(1);
    expect(selectedMessages()[0]).toMatchObject({
      id: "message-final",
      text: "最终回答",
    });
  });

  it("finalizes with the authoritative content even while deltas are buffered", async () => {
    vi.useFakeTimers();
    const socket = await connectThread();
    emitSocketEvent(socket, {
      type: "agent.delta",
      run_id: "run-2",
      payload: { agent: "codex", text: "很长的中间内容".repeat(100) },
    });

    emitSocketEvent(socket, {
      type: "message.created",
      run_id: "run-2",
      payload: {
        id: "message-final",
        role: "codex",
        content: "最终回答",
        attachments: [],
      },
    });
    vi.advanceTimersByTime(2000);

    const finals = selectedMessages().filter(
      (item) => item.id === "message-final",
    );
    expect(finals).toHaveLength(1);
    expect(finals[0].text).toBe("最终回答");
    expect(selectedMessages().some((item) => item.id === "stream-run-2")).toBe(
      false,
    );
  });

  it("merges tool progress into one activity timeline", async () => {
    const socket = await connectThread();
    emitSocketEvent(socket, {
      type: "agent.tool",
      run_id: "run-3",
      payload: {
        agent: "codex",
        event: "item/started",
        item: { id: "command-1", type: "command_execution", command: "pytest" },
      },
    });
    emitSocketEvent(socket, {
      type: "agent.tool",
      run_id: "run-3",
      payload: {
        agent: "codex",
        event: "item/completed",
        item: {
          id: "command-1",
          type: "command_execution",
          command: "pytest",
          exit_code: 0,
        },
      },
    });

    const activity = selectedMessages()[0].activity;
    expect(activity?.steps).toHaveLength(1);
    expect(activity?.steps[0]).toMatchObject({
      id: "command-1",
      kind: "command",
      status: "completed",
    });
  });

  it("settles a tool row when item updated carries a terminal status", async () => {
    const socket = await connectThread();
    emitSocketEvent(socket, {
      type: "agent.tool",
      run_id: "run-updated-terminal",
      payload: {
        agent: "codex",
        event: "item/started",
        item: {
          id: "command-1",
          type: "commandExecution",
          command: "rg docs",
        },
      },
    });
    emitSocketEvent(socket, {
      type: "agent.tool",
      run_id: "run-updated-terminal",
      payload: {
        agent: "codex",
        event: "item/updated",
        item: {
          id: "command-1",
          type: "commandExecution",
          command: "rg docs",
          status: "completed",
        },
      },
    });

    expect(selectedMessages()[0].activity?.steps[0].status).toBe("completed");
    expect(selectedMessages()[0].activity?.steps[0].completedAt).toBeTypeOf(
      "number",
    );
  });

  it("merges Claude tool results into the named tool row", async () => {
    const socket = await connectThread();
    emitSocketEvent(socket, {
      type: "agent.tool",
      run_id: "run-claude-tool",
      payload: {
        agent: "claude",
        event: "item/started",
        item: {
          id: "tool-read-1",
          type: "tool_use",
          name: "Read",
          input: { file_path: "/home/user/work/README.md" },
        },
      },
    });
    emitSocketEvent(socket, {
      type: "agent.tool",
      run_id: "run-claude-tool",
      payload: {
        agent: "claude",
        event: "item/completed",
        item: {
          id: "tool-read-1",
          type: "tool_result",
          status: "completed",
          result: "contents",
        },
      },
    });

    const steps = selectedMessages()[0].activity?.steps;
    expect(steps).toHaveLength(1);
    expect(steps?.[0]).toMatchObject({
      id: "tool-read-1",
      label: "读取文件",
      status: "completed",
    });
    expect(steps?.[0].detail).toContain("README.md");
  });

  it("preserves complete command input for the expandable tool row", async () => {
    const socket = await connectThread();
    const command = `powershell -Command ${"Get-ChildItem ".repeat(20)}`;
    emitSocketEvent(socket, {
      type: "agent.tool",
      run_id: "run-long-command",
      payload: {
        agent: "codex",
        event: "item/started",
        item: { id: "command-long", type: "command_execution", command },
      },
    });

    expect(selectedMessages()[0].activity?.steps[0].detail).toBe(command);
  });

  it("streams reasoning deltas into one untruncated thinking step", async () => {
    const socket = await connectThread();
    const first = "分".repeat(150);
    const second = "析".repeat(150);
    emitSocketEvent(socket, {
      type: "agent.tool",
      run_id: "run-5",
      payload: {
        agent: "codex",
        event: "delta",
        item: { id: "reasoning-1", type: "reasoning", text: first },
      },
    });
    emitSocketEvent(socket, {
      type: "agent.tool",
      run_id: "run-5",
      payload: {
        agent: "codex",
        event: "delta",
        item: { id: "reasoning-1", type: "reasoning", text: second },
      },
    });

    const step = selectedMessages()[0].activity?.steps[0];
    expect(step?.kind).toBe("reasoning");
    expect(step?.detail).toBe(`${first}${second}`);
    expect(step?.detail?.length).toBe(300);
  });

  it("stamps reasoning steps with start and completion times", async () => {
    const socket = await connectThread();
    emitSocketEvent(socket, {
      type: "agent.tool",
      run_id: "run-6",
      payload: {
        agent: "codex",
        event: "item/started",
        item: { id: "reasoning-1", type: "reasoning", text: "开始分析" },
      },
    });
    const started = selectedMessages()[0].activity?.steps[0];
    expect(started?.startedAt).toBeTypeOf("number");
    expect(started?.completedAt).toBeUndefined();

    emitSocketEvent(socket, {
      type: "agent.tool",
      run_id: "run-6",
      payload: {
        agent: "codex",
        event: "item/completed",
        item: { id: "reasoning-1", type: "reasoning", text: "reasoning" },
      },
    });
    const completed = selectedMessages()[0].activity?.steps[0];
    expect(completed?.status).toBe("completed");
    expect(completed?.completedAt).toBeTypeOf("number");
    expect(completed!.completedAt!).toBeGreaterThanOrEqual(
      completed!.startedAt!,
    );
    expect(completed?.detail).toBe("开始分析");
  });

  it("settles running activity when an error arrives", async () => {
    const socket = await connectThread();
    emitSocketEvent(socket, {
      type: "run.state_changed",
      run_id: "run-4",
      payload: { state: "IMPLEMENTING", agent: "codex" },
    });
    emitSocketEvent(socket, {
      type: "agent.tool",
      run_id: "run-4",
      payload: {
        agent: "codex",
        event: "item/started",
        item: { id: "step-1", type: "reasoning", text: "处理中" },
      },
    });

    emitSocketEvent(socket, {
      type: "error",
      run_id: "run-4",
      payload: { message: "运行失败" },
    });

    const activity = selectedMessages().find((item) => item.activity)?.activity;
    expect(useStore.getState().error).toBe("运行失败");
    expect(activity?.status).toBe("failed");
    expect(activity?.steps[0].status).toBe("failed");
    expect(activity?.completedAt).toBeTypeOf("number");
  });
});

describe("thread realtime connection", () => {
  it("ignores errors from a socket after its task is no longer selected", async () => {
    const sockets: Array<Record<string, unknown>> = [];
    vi.mocked(api.threadSocket).mockImplementation(async () => {
      const socket = { close: vi.fn() };
      sockets.push(socket);
      return socket as unknown as WebSocket;
    });
    useStore.setState({
      backend: "online",
      workspaceId: "",
      threadId: "",
      workspaces: [
        {
          id: "workspace",
          name: "Project",
          path: "D:/Project",
          threads: [
            { id: "thread", title: "Task", state: "CREATED", messages: [] },
          ],
        },
      ],
    });

    useStore.getState().setSelection("workspace", "thread");
    await vi.waitFor(() => expect(sockets).toHaveLength(1));
    useStore.getState().setSelection("", "");
    (sockets[0].onerror as (() => void) | undefined)?.();

    expect(useStore.getState().realtime).toBe("disconnected");
  });

  it("schedules a reconnect after the socket closes", async () => {
    vi.useFakeTimers();
    const sockets: Array<Record<string, unknown>> = [];
    vi.mocked(api.threadSocket).mockImplementation(async () => {
      const socket = { close: vi.fn() };
      sockets.push(socket);
      return socket as unknown as WebSocket;
    });
    useStore.setState({
      backend: "online",
      workspaceId: "",
      threadId: "",
      workspaces: [
        {
          id: "workspace",
          name: "Project",
          path: "D:/Project",
          threads: [
            { id: "thread", title: "Task", state: "CREATED", messages: [] },
          ],
        },
      ],
    });

    useStore.getState().setSelection("workspace", "thread");
    await vi.waitFor(() => expect(sockets).toHaveLength(1));
    (sockets[0].onclose as (() => void) | undefined)?.();
    expect(useStore.getState().realtime).toBe("reconnecting");

    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => expect(sockets).toHaveLength(2));
  });
});
