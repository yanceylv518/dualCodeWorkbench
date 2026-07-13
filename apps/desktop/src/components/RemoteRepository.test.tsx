import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useStore } from "../store";
import type { ExecutionJob, WorkspaceRemoteStatus } from "../types";
import { deriveRemoteFeedback, RemoteRepository } from "./RemoteRepository";

afterEach(cleanup);

const remote: WorkspaceRemoteStatus = {
  settings: {
    remote_url: "git@github.com:owner/repo.git",
    vps_repo_path: "/home/user/work/repo",
  },
  local: {
    branch: "main",
    head: "1234567890abcdef",
    remote: "git@github.com:owner/repo.git",
    upstream: "origin/main",
    ahead: 0,
    behind: 0,
    changes: [],
    commits: [],
  },
  same_remote: true,
  same_commit: true,
};

function job(
  status: ExecutionJob["status"],
  last_error?: string,
): ExecutionJob {
  return {
    id: `job-${status}`,
    kind: "remote_git",
    payload: { action: "provision" },
    status,
    attempts: 1,
    last_error,
    created_at: "2026-07-13T00:00:00Z",
    updated_at: "2026-07-13T00:00:00Z",
  };
}

function renderRepository(status?: ExecutionJob["status"], lastError?: string) {
  useStore.setState({ refreshRemote: vi.fn(async () => undefined) });
  const action = vi.fn(async () => undefined);
  render(
    <RemoteRepository
      remote={remote}
      jobs={status ? [job(status, lastError)] : []}
      save={vi.fn(async () => undefined)}
      action={action}
    />,
  );
  return action;
}

describe("RemoteRepository", () => {
  it("prefers verified repository state over a stale failed clone job", () => {
    const readyRemote: WorkspaceRemoteStatus = {
      ...remote,
      vps: {
        branch: "main",
        head: "",
        remote: "git@github.com:owner/repo.git",
      },
    };

    const feedback = deriveRemoteFeedback(
      readyRemote,
      job("FAILED", "VPS repository has not been cloned yet"),
    );

    expect(feedback).toEqual({
      message: "VPS 空仓库已就绪，等待首次提交。",
      tone: "ready",
    });
  });

  it("shows a single running state while cloning", () => {
    renderRepository("RUNNING");
    expect(screen.getByRole("status").textContent).toContain("正在克隆到 VPS");
    expect(screen.getByRole("button", { name: "正在克隆…" })).toHaveProperty(
      "disabled",
      true,
    );
  });

  it("offers repair retry for a failed non-empty target", () => {
    const action = renderRepository(
      "FAILED",
      "already exists and is not an empty directory",
    );
    fireEvent.click(screen.getByRole("button", { name: "清理残留并重新克隆" }));
    expect(action).toHaveBeenCalledWith("repair_provision");
  });

  it("renders a guarded ready state for an empty remote repository", () => {
    renderRepository();
    expect(screen.getByText("仓库配置已保存")).toBeTruthy();
    expect(screen.getByText("VPS 目录尚未检测为有效 Git 仓库")).toBeTruthy();

    cleanup();
    render(
      <RemoteRepository
        remote={{
          ...remote,
          vps: { branch: "main", head: "", remote: remote.settings.remote_url },
        }}
        jobs={[]}
        save={vi.fn(async () => undefined)}
        action={vi.fn(async () => undefined)}
      />,
    );
    expect(screen.getByText("尚无提交")).toBeTruthy();
    expect(screen.getByRole("button", { name: "拉取更新" })).toHaveProperty(
      "disabled",
      true,
    );
  });
});
