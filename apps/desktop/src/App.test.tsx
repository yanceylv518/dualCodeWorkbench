import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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

  it("does not send while the Chinese input method is composing", () => {
    const sendPrompt = vi.fn(async () => undefined);
    useStore.setState({
      backend: "online",
      workspaceId: "workspace-1",
      threadId: "thread-1",
      sendPrompt,
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
    const composer = screen.getByPlaceholderText(
      "输入消息；可以拖入文件或粘贴截图…",
    );
    fireEvent.change(composer, { target: { value: "中文" } });
    fireEvent.keyDown(composer, { key: "Enter", isComposing: true });

    expect(sendPrompt).not.toHaveBeenCalled();
  });

  it("keeps approval actions visible when the inspector is hidden", () => {
    useStore.setState({
      backend: "online",
      workspaceId: "workspace-1",
      threadId: "thread-1",
      pendingApproval: {
        id: "approval-1",
        action: "codex_command",
        reason: "需要执行检查命令",
        status: "PENDING",
      },
      workspaces: [
        {
          id: "workspace-1",
          name: "Project",
          path: "D:/Project",
          threads: [
            {
              id: "thread-1",
              title: "Task",
              state: "WAITING_APPROVAL",
              messages: [],
            },
          ],
        },
      ],
    });

    render(<App />);
    fireEvent.click(screen.getByTitle("隐藏检查器"));

    expect(screen.getByRole("button", { name: "允许一次" })).toBeTruthy();
    expect(screen.getByText("需要执行检查命令")).toBeTruthy();
  });

  it("does not force the message stream to the bottom after the user scrolls up", () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    useStore.setState({
      backend: "online",
      workspaceId: "workspace-1",
      threadId: "thread-1",
      workspaces: [
        {
          id: "workspace-1",
          name: "Project",
          path: "D:/Project",
          threads: [
            {
              id: "thread-1",
              title: "Task",
              state: "CREATED",
              messages: [{ id: "one", agent: "user", text: "旧消息", time: "" }],
            },
          ],
        },
      ],
    });

    const { container } = render(<App />);
    const stream = container.querySelector(".message-stream") as HTMLDivElement;
    Object.defineProperties(stream, {
      scrollHeight: { configurable: true, value: 1000 },
      clientHeight: { configurable: true, value: 400 },
      scrollTop: { configurable: true, value: 100 },
    });
    fireEvent.scroll(stream);
    expect(screen.getByRole("button", { name: "回到最新" })).toBeTruthy();
    scrollIntoView.mockClear();

    act(() => {
      useStore.setState((state) => ({
        workspaces: state.workspaces.map((workspace) => ({
          ...workspace,
          threads: workspace.threads.map((thread) => ({
            ...thread,
            messages: [
              ...thread.messages,
              { id: "two", agent: "codex", text: "新消息", time: "" },
            ],
          })),
        })),
      }));
    });

    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});
