import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CollaborationTimelineCard } from "./CollaborationTimelineCard";
import type { CollaborationTimeline } from "../types";

afterEach(cleanup);

const timeline = (
  overrides: Partial<CollaborationTimeline> = {},
): CollaborationTimeline => ({
  runId: "run-1",
  state: "REVIEWING",
  round: 2,
  maxRounds: 3,
  currentAgent: "claude",
  findingsCount: 1,
  status: "running",
  stages: {
    clarify: "completed",
    implement: "completed",
    verify: "completed",
    review: "running",
    fix: "pending",
  },
  updatedAt: 1,
  ...overrides,
});

describe("CollaborationTimelineCard", () => {
  it("renders the five stages and allows stopping a running collaboration", () => {
    const act = vi.fn(async () => undefined);
    render(<CollaborationTimelineCard timeline={timeline()} act={act} />);
    for (const label of ["澄清", "实现", "验证", "审查", "整改"])
      expect(screen.getByText(label)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "停止" }));
    expect(act).toHaveBeenCalledWith("cancel", "");
  });

  it("offers all WAITING_USER decisions with an optional note", () => {
    const act = vi.fn(async () => undefined);
    render(
      <CollaborationTimelineCard
        timeline={timeline({
          state: "WAITING_USER",
          status: "waiting",
          waitingReason: "请选择下一步",
        })}
        act={act}
      />,
    );
    fireEvent.change(screen.getByLabelText("协作调整说明"), {
      target: { value: "缩小范围" },
    });
    fireEvent.click(screen.getByRole("button", { name: "调整后重入" }));
    expect(act).toHaveBeenCalledWith("reenter", "缩小范围");
    expect(screen.getByRole("button", { name: "直接整改" })).toBeTruthy();
    expect(screen.getByText("请选择下一步")).toBeTruthy();
  });

  it("offers resume and cancel while blocked", () => {
    const act = vi.fn(async () => undefined);
    render(
      <CollaborationTimelineCard
        timeline={timeline({ state: "BLOCKED", status: "waiting" })}
        act={act}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "恢复" }));
    expect(act).toHaveBeenCalledWith("resume", "");
    expect(screen.getByRole("button", { name: "取消" })).toBeTruthy();
  });

  it("degrades safely for an unknown state with missing stage entries", () => {
    render(
      <CollaborationTimelineCard
        timeline={timeline({ state: "FUTURE_STATE", stages: {} })}
        act={vi.fn(async () => undefined)}
      />,
    );
    expect(screen.getByText(/FUTURE_STATE/)).toBeTruthy();
    expect(screen.getByText("澄清")).toBeTruthy();
  });
});
