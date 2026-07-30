import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import { HandoffPanel } from "./HandoffPanel";

vi.mock("./api", () => ({
  listHandoffs: vi.fn(),
  prepareHandoff: vi.fn(),
  sendHandoff: vi.fn(),
  fetchCurrentCollaboration: vi.fn(),
  fetchCollaborationFindings: vi.fn(),
}));

const baseHandoff = {
  id: "handoff-v2",
  recipient: "claude" as const,
  purpose: "review" as const,
  status: "PREPARED" as const,
  payload: {
    schema: "handoff.v2" as const,
    purpose: "review" as const,
    sender: "codex",
    recipient: "claude",
    task: {
      goal: "实现协作视图",
      non_goals: ["不修改协议"],
      acceptance: ["组件测试通过"],
      constraints: ["保持 legacy 路径"],
    },
    repository: {
      base_sha: "a".repeat(40),
      snapshot_sha: "b".repeat(40),
      branch: "main",
      changed_files: ["src/HandoffPanel.tsx"],
      diff_stats: { files: 1, additions: 42, deletions: 3 },
    },
    claims: [],
    evidence: [
      {
        type: "test",
        command: "pnpm test",
        exit_code: 0,
        summary: "78 tests passed",
      },
    ],
    open_findings: ["补充空态"],
    risks: ["事件可能乱序"],
    requested_action: "独立审查",
  },
};

describe("HandoffPanel C6 views", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.fetchCurrentCollaboration).mockResolvedValue(undefined);
    vi.mocked(api.fetchCollaborationFindings).mockResolvedValue([]);
  });
  afterEach(cleanup);

  it("renders handoff.v2 as structured evidence instead of raw JSON", async () => {
    vi.mocked(api.listHandoffs).mockResolvedValue([baseHandoff]);

    render(<HandoffPanel workspaceId="workspace" threadId="thread" />);

    expect(await screen.findByText("实现协作视图")).toBeTruthy();
    expect(screen.getByText("aaaaaaaaaa").parentElement).toHaveProperty(
      "title",
      "a".repeat(40),
    );
    expect(screen.getByText("78 tests passed")).toBeTruthy();
    expect(screen.getByText("补充空态")).toBeTruthy();
    expect(screen.getByText("事件可能乱序")).toBeTruthy();
    expect(screen.queryByText(/"schema":/)).toBeNull();
  });

  it("shows findings with severity, location, acceptance and state", async () => {
    vi.mocked(api.listHandoffs).mockResolvedValue([]);
    vi.mocked(api.fetchCurrentCollaboration).mockResolvedValue({
      id: "run-1",
      workspace_id: "workspace",
      thread_id: "thread",
      mode: "smart",
      state: "WAITING_USER",
      current_agent: "claude",
      round: 2,
      max_rounds: 3,
    });
    vi.mocked(api.fetchCollaborationFindings).mockResolvedValue([
      {
        id: "finding-1",
        collaboration_run_id: "run-1",
        round: 2,
        type: "architecture",
        severity: "blocking",
        status: "open",
        file: "src/App.tsx",
        line: "42",
        description: "缺少恢复入口",
        acceptance: "阻塞状态可恢复",
      },
    ]);

    render(<HandoffPanel workspaceId="workspace" threadId="thread" />);

    expect(await screen.findByText("缺少恢复入口")).toBeTruthy();
    expect(screen.getByText("阻断")).toBeTruthy();
    expect(screen.getByText("src/App.tsx:42")).toBeTruthy();
    expect(screen.getByText("验收：阻塞状态可恢复")).toBeTruthy();
    expect(screen.getByText("WAITING_USER · 第 2 轮")).toBeTruthy();
  });

  it("renders an explicit findings empty state", async () => {
    vi.mocked(api.listHandoffs).mockResolvedValue([]);

    render(<HandoffPanel workspaceId="workspace" threadId="thread" />);

    expect(await screen.findByText("当前协作运行没有审查问题")).toBeTruthy();
  });
});
