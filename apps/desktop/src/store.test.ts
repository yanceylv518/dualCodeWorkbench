import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import { settleActivity, useStore } from "./store";

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
  sendMessage: vi.fn(async () => ({
    message_id: "message-1",
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

    await vi.waitFor(() =>
      expect(useStore.getState().gitStatus).toBeNull(),
    );
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
  it("derives and persists a title from the first user message", async () => {
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

    expect(api.updateThread).toHaveBeenCalledWith(
      "workspace",
      "thread",
      "实现一个面向专业交付的任务管理功能，并补",
    );
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
    expect(
      selectedMessages().some((item) => item.id === "stream-run-2"),
    ).toBe(false);
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
