import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ContractPanel } from "./ContractPanel";
import { HandoffPanel } from "./HandoffPanel";
import * as api from "./api";

vi.mock("./api", () => ({
  fetchContract: vi.fn(),
  saveGovernance: vi.fn(),
  saveTaskContract: vi.fn(),
  listHandoffs: vi.fn(),
  prepareHandoff: vi.fn(),
  sendHandoff: vi.fn(),
}));

const contract = {
  governance: {
    product_goal: "交付产品",
    product_boundary: "本地工作台",
    rules: ["禁止临时方案"],
    deliverables: ["测试报告"],
  },
  task: {
    goal: "完成验收",
    non_goals: [],
    acceptance: ["测试通过"],
    constraints: [],
    risks: [],
    status: "READY" as const,
  },
  gate: { ready_for_implementation: true, missing: [] },
};

const handoff = {
  id: "handoff-1",
  recipient: "claude" as const,
  purpose: "review" as const,
  status: "PREPARED" as const,
  payload: {
    contract: { task_goal: "完成验收" },
    repository: {
      branch: "main",
      head: "abc123",
      upstream: "origin/main",
      changed_files: ["src/App.tsx"],
    },
    diff: [
      "diff --git a/src/App.tsx b/src/App.tsx",
      "--- a/src/App.tsx",
      "+++ b/src/App.tsx",
      "-old",
      "+new",
      "+test",
    ].join("\n"),
    tests: [{ command: "pnpm test", exit_code: 0, output: "passed" }],
  },
};

describe("inspector panels", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(cleanup);

  it("loads and saves the project contract", async () => {
    vi.mocked(api.fetchContract).mockResolvedValue(contract);
    vi.mocked(api.saveGovernance).mockResolvedValue(undefined);
    vi.mocked(api.saveTaskContract).mockResolvedValue(undefined);

    render(<ContractPanel workspaceId="workspace" threadId="thread" />);
    expect(await screen.findByText("已具备正式实施条件")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "保存契约" }));

    await waitFor(() =>
      expect(api.saveGovernance).toHaveBeenCalledWith(
        "workspace",
        contract.governance,
      ),
    );
    expect(api.saveTaskContract).toHaveBeenCalledWith(
      "workspace",
      "thread",
      contract.task,
    );
  });

  it("keeps empty list lines while editing and filters them on save", async () => {
    vi.mocked(api.fetchContract).mockResolvedValue(contract);
    vi.mocked(api.saveGovernance).mockResolvedValue(undefined);
    vi.mocked(api.saveTaskContract).mockResolvedValue(undefined);

    render(<ContractPanel workspaceId="workspace" threadId="thread" />);
    const rules = await screen.findByDisplayValue("禁止临时方案");
    fireEvent.change(rules, { target: { value: "规则一\n\n规则二" } });
    expect(rules).toHaveProperty("value", "规则一\n\n规则二");
    fireEvent.click(screen.getByRole("button", { name: "保存契约" }));

    await waitFor(() =>
      expect(api.saveGovernance).toHaveBeenCalledWith("workspace", {
        ...contract.governance,
        rules: ["规则一", "规则二"],
      }),
    );
  });

  it("prepares and explicitly sends a Claude review handoff", async () => {
    vi.mocked(api.listHandoffs).mockResolvedValue([]);
    vi.mocked(api.prepareHandoff).mockResolvedValue(handoff);
    vi.mocked(api.sendHandoff).mockResolvedValue(undefined);

    render(<HandoffPanel workspaceId="workspace" threadId="thread" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "准备 Claude 审查包" }),
    );
    expect(await screen.findByText("交给 Claude 独立审查")).toBeTruthy();
    expect(screen.getByText("1 个文件、+2/-1 行")).toBeTruthy();
    expect(screen.getByText("测试证据")).toBeTruthy();
    expect(screen.queryByText(/"contract":/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "确认发送" }));

    await waitFor(() =>
      expect(api.sendHandoff).toHaveBeenCalledWith(
        "workspace",
        "thread",
        "handoff-1",
      ),
    );
    expect(await screen.findByText("已发送")).toBeTruthy();
  });
});
