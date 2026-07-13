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
  useStore.setState({ notifications: [] });
});

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
