import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import App from "./App";
import { useStore } from "./store";

afterEach(() => {
  cleanup();
  useStore.setState({
    backend: "connecting",
    workspaces: [],
    workspaceId: "",
    threadId: "",
  });
});

describe("workbench", () => {
  it("renders the compact inspector navigation", () => {
    render(<App />);

    expect(screen.getAllByText("DualCode Workbench").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "状态" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "规则" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "交接" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "仓库" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "变更" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "运行日志" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "恢复" })).toBeNull();
  });

  it("shows a real offline state and disables message actions", () => {
    useStore.setState({
      backend: "offline",
      workspaceId: "workspace-1",
      threadId: "thread-1",
      workspaces: [
        {
          id: "workspace-1",
          name: "Project",
          path: "D:/Project",
          threads: [
            { id: "thread-1", title: "Task", state: "CREATED", messages: [] },
          ],
        },
      ],
    });

    render(<App />);

    expect(
      screen.getByText("当前无法发送消息或上传附件，请重试连接。"),
    ).toBeTruthy();
    expect(screen.getByPlaceholderText("后端离线，请重试连接…")).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByRole("button", { name: "发送" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(
      screen.getAllByRole("button", { name: /重试/ }).length,
    ).toBeGreaterThan(0);
  });
});
