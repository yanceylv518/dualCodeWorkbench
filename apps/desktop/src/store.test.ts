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
  threadSocket: vi.fn(),
}));

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
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
