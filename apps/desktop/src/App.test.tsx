import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { useStore } from "./store";

afterEach(() => {
  cleanup();
  useStore.setState({
    backend: "connecting",
    mode: "codex",
    activeAgent: undefined,
    pendingApproval: undefined,
    realtime: "disconnected",
    workspaces: [],
    workspaceId: "",
    threadId: "",
    drafts: {},
  });
});

const singleTaskState = (state: "CREATED" | "IMPLEMENTING") => ({
  backend: "online" as const,
  workspaceId: "workspace-1",
  threadId: "thread-1",
  workspaces: [
    {
      id: "workspace-1",
      name: "Project",
      path: "D:/Project",
      threads: [{ id: "thread-1", title: "Task", state, messages: [] }],
    },
  ],
});

describe("workbench", () => {
  it("renders stream placeholders as plain text and persisted messages as Markdown", () => {
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    const base = singleTaskState("IMPLEMENTING");
    useStore.setState({
      ...base,
      workspaces: [
        {
          ...base.workspaces[0],
          threads: [
            {
              ...base.workspaces[0].threads[0],
              messages: [
                {
                  id: "stream-run-1",
                  agent: "codex",
                  text: "## 尚未完成\n\n- 流式内容",
                  time: "",
                },
              ],
            },
          ],
        },
      ],
    });

    const { container } = render(<App />);
    expect(screen.getByLabelText("正在生成回复").textContent).toContain(
      "## 尚未完成",
    );
    expect(container.querySelector(".message-content h2")).toBeNull();
    expect(container.querySelector(".streaming-message")).toBeTruthy();

    act(() => {
      useStore.setState((state) => ({
        workspaces: state.workspaces.map((workspace) => ({
          ...workspace,
          threads: workspace.threads.map((thread) => ({
            ...thread,
            messages: thread.messages.map((message) => ({
              ...message,
              id: "message-final",
            })),
          })),
        })),
      }));
    });

    expect(
      screen.getByRole("heading", { name: "尚未完成", level: 2 }),
    ).toBeTruthy();
    expect(container.querySelector(".streaming-message")).toBeNull();
  });

  it("shows a focusable message toolbar and copies the exact agent Markdown", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const base = singleTaskState("CREATED");
    useStore.setState({
      ...base,
      workspaces: [
        {
          ...base.workspaces[0],
          threads: [
            {
              ...base.workspaces[0].threads[0],
              messages: [
                {
                  id: "agent-message",
                  agent: "codex",
                  text: "## 结论\n\n- 保留 **Markdown**",
                  time: "10:00",
                },
              ],
            },
          ],
        },
      ],
    });

    const { container } = render(<App />);
    const card = container.querySelector(".message-card.codex")!;
    fireEvent.mouseEnter(card);
    const toolbar = screen.getByRole("toolbar", { name: "消息操作" });
    const copy = screen.getByRole("button", { name: "复制" });
    copy.focus();
    expect(toolbar.contains(document.activeElement)).toBe(true);
    fireEvent.click(copy);

    await vi.waitFor(() =>
      expect(writeText).toHaveBeenCalledWith("## 结论\n\n- 保留 **Markdown**"),
    );
    expect(await screen.findByRole("button", { name: "已复制" })).toBeTruthy();
  });

  it("keeps the user edit and retry actions in the message toolbar", async () => {
    const retryMessage = vi.fn(async () => undefined);
    const base = singleTaskState("CREATED");
    useStore.setState({
      ...base,
      retryMessage,
      workspaces: [
        {
          ...base.workspaces[0],
          threads: [
            {
              ...base.workspaces[0].threads[0],
              messages: [
                {
                  id: "user-message",
                  agent: "user",
                  text: "需要继续完善交互",
                  time: "10:01",
                },
              ],
            },
          ],
        },
      ],
    });

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "编辑后重新发送" }));
    expect(useStore.getState().drafts["thread-1"]).toBe("需要继续完善交互");
    fireEvent.click(screen.getByRole("button", { name: "重试本轮" }));
    await vi.waitFor(() =>
      expect(retryMessage).toHaveBeenCalledWith("user-message"),
    );
  });

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
    const composer =
      screen.getByPlaceholderText("输入消息；可以拖入文件或粘贴截图…");
    fireEvent.change(composer, { target: { value: "中文" } });
    fireEvent.keyDown(composer, { key: "Enter", isComposing: true });

    expect(sendPrompt).not.toHaveBeenCalled();
  });

  it("keeps the composer editable while a run is active and blocks Enter", () => {
    const sendPrompt = vi.fn(async () => undefined);
    useStore.setState({ ...singleTaskState("IMPLEMENTING"), sendPrompt });

    render(<App />);
    const composer = screen.getByPlaceholderText<HTMLTextAreaElement>(
      "Agent 处理中；可以先起草下一条消息…",
    );
    expect(composer.disabled).toBe(false);
    fireEvent.change(composer, { target: { value: "下一条草稿" } });
    expect(useStore.getState().drafts["thread-1"]).toBe("下一条草稿");
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(sendPrompt).not.toHaveBeenCalled();
    const stop = screen.getByRole("button", {
      name: /停止/,
    }) as HTMLButtonElement;
    expect(stop.disabled).toBe(false);
  });

  it("disables send until the draft has content", () => {
    useStore.setState(singleTaskState("CREATED"));

    render(<App />);
    const send = screen.getByRole("button", {
      name: /发送/,
    }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.change(
      screen.getByPlaceholderText("输入消息；可以拖入文件或粘贴截图…"),
      { target: { value: "开始" } },
    );
    expect(send.disabled).toBe(false);
  });

  it("renders streaming reasoning as a live thinking block", () => {
    const base = singleTaskState("IMPLEMENTING");
    useStore.setState({
      ...base,
      workspaces: [
        {
          ...base.workspaces[0],
          threads: [
            {
              ...base.workspaces[0].threads[0],
              messages: [
                {
                  id: "activity-run-1",
                  agent: "system",
                  text: "",
                  time: "",
                  activity: {
                    runId: "run-1",
                    agent: "codex",
                    status: "running",
                    startedAt: Date.now(),
                    steps: [
                      {
                        id: "reasoning-1",
                        kind: "reasoning",
                        label: "思考",
                        detail: "正在梳理仓库结构，准备定位需要修改的模块",
                        status: "running",
                      },
                    ],
                  },
                },
              ],
            },
          ],
        },
      ],
    });

    render(<App />);
    expect(screen.getByText("正在思考")).toBeTruthy();
    expect(
      screen.getByText("正在梳理仓库结构，准备定位需要修改的模块"),
    ).toBeTruthy();
  });

  it("supports inline task rename and explicit delete confirmation", () => {
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
            { id: "thread-1", title: "Task", state: "CREATED", messages: [] },
          ],
        },
      ],
    });

    render(<App />);
    const taskName = screen
      .getAllByText("Task")
      .find((element) => element.tagName === "STRONG");
    expect(taskName).toBeTruthy();
    fireEvent.doubleClick(taskName!);
    expect(screen.getByDisplayValue("Task")).toBeTruthy();
    fireEvent.keyDown(screen.getByDisplayValue("Task"), { key: "Escape" });

    fireEvent.click(screen.getByRole("button", { name: "管理任务 Task" }));
    fireEvent.click(screen.getByRole("button", { name: "删除任务" }));
    expect(screen.getByRole("button", { name: "确认删除任务" })).toBeTruthy();
  });

  it("shows an empty search state and opens a task from the activity bar", () => {
    const setSelection = vi.fn();
    useStore.setState({
      backend: "online",
      workspaceId: "workspace-1",
      threadId: "thread-1",
      setSelection,
      workspaces: [
        {
          id: "workspace-1",
          name: "Project",
          path: "D:/Project",
          threads: [
            {
              id: "thread-1",
              title: "Running task",
              state: "IMPLEMENTING",
              messages: [],
            },
          ],
        },
      ],
    });

    render(<App />);
    fireEvent.click(
      screen.getByRole("button", { name: /Project · Running task/ }),
    );
    expect(setSelection).toHaveBeenCalledWith("workspace-1", "thread-1");
    expect(screen.getByText("状态以进入任务后为准")).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("搜索项目和任务"), {
      target: { value: "missing" },
    });
    expect(screen.getByText("没有匹配的项目或任务")).toBeTruthy();
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
              messages: [
                { id: "one", agent: "user", text: "旧消息", time: "" },
              ],
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

  it("opens a project with the Ctrl+O shortcut", async () => {
    const openWorkspace = vi.fn(async () => undefined);
    useStore.setState({ backend: "online", openWorkspace });
    render(<App />);

    fireEvent.keyDown(window, { key: "o", ctrlKey: true });
    const path =
      await screen.findByPlaceholderText("输入本地 Git 仓库的绝对路径");
    fireEvent.change(path, { target: { value: "D:/Project" } });
    fireEvent.click(screen.getByRole("button", { name: "打开" }));

    await vi.waitFor(() =>
      expect(openWorkspace).toHaveBeenCalledWith("D:/Project"),
    );
  });

  it("keeps the running agent name when the send target changes", () => {
    useStore.setState({
      backend: "online",
      mode: "codex",
      activeAgent: "codex",
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
              state: "IMPLEMENTING",
              messages: [],
            },
          ],
        },
      ],
    });

    const { container } = render(<App />);
    act(() => {
      useStore.getState().setMode("claude");
    });

    expect(
      container.querySelector(".processing-card strong")?.textContent,
    ).toContain("Codex 正在处理");
    expect(
      container.querySelector(".processing-card strong")?.textContent,
    ).not.toContain("Claude 正在处理");
  });
});
