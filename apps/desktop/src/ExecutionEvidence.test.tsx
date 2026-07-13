import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ExecutionEvidence, safeGitReference } from "./ExecutionEvidence";
import type { ExecutionJob } from "./types";

const job = (evidence: ExecutionJob["evidence"]): ExecutionJob => ({ id: "1", kind: "git_action", payload: { action: "push" }, status: "INTERRUPTED", attempts: 1, evidence, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" });

describe("execution evidence", () => {
  it("summarizes refs without exposing credentials or full paths", () => {
    expect(safeGitReference("https://secret@example.com/team/private-repo.git")).toBe("example.com/…/private-repo");
    expect(safeGitReference("C:\\Users\\name\\project")).toBe("…/project");
    expect(safeGitReference("/home/name/private-project")).toBe("…/private-project");
    expect(safeGitReference("1234567890abcdef")).toBe("1234567890");
  });
  it("explains an unknown outcome and provides before/after evidence", () => {
    render(<ExecutionEvidence job={job({ before: { head: "1234567890abcdef", branch: "main", remote: "git@example.com:team/repo.git" } })}/>);
    expect(screen.getByText("结果未知")).toBeTruthy();
    expect(screen.getByText("执行前")).toBeTruthy();
    expect(screen.getByText("执行后")).toBeTruthy();
    expect(screen.getByText(/避免重复提交或推送/)).toBeTruthy();
    expect(screen.queryByText(/team\/repo\.git/)).toBeNull();
  });
});
