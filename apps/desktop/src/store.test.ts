import { describe, expect, it } from "vitest";

import { settleActivity } from "./store";

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
