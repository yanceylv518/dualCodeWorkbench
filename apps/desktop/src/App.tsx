import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { open } from "@tauri-apps/plugin-dialog";
import {
  AlertTriangle,
  ArrowUp,
  Bot,
  Check,
  Copy,
  ChevronDown,
  Circle,
  Code2,
  FolderGit2,
  GitBranch,
  ImagePlus,
  LoaderCircle,
  MessageSquarePlus,
  MoreHorizontal,
  Pencil,
  Play,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Square,
  SquareTerminal,
  TestTube2,
  X,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Minus,
  Maximize2,
  RefreshCw,
  RotateCcw,
} from "lucide-react";
import { MarkdownMessage } from "./components/MarkdownMessage";
import { DiffPanel } from "./components/DiffPanel";
import { ConfirmDialog, InputDialog } from "./components/dialogs";
import { SettingsDialog } from "./SettingsDialog";
import { ProjectDialog } from "./ProjectDialog";
import { ExecutionEvidence } from "./ExecutionEvidence";
import { ContractPanel } from "./ContractPanel";
import { HandoffPanel } from "./HandoffPanel";
import { RemoteRepository } from "./components/RemoteRepository";
import "./recovery.css";
import "./message-actions.css";
import { useCopyFeedback } from "./hooks/useCopyFeedback";
import { useStore, type Mode } from "./store";
import * as api from "./api";
import type {
  Agent,
  AgentSettings,
  ExecutionJob,
  GitStatus,
  Message,
  RunState,
  Thread,
  WorkspaceRemoteStatus,
} from "./types";

const stateLabel: Record<RunState, string> = {
  CREATED: "空闲",
  PLANNING: "Claude 回复中",
  WAITING_APPROVAL: "等待授权",
  IMPLEMENTING: "Codex 回复中",
  TESTING: "测试运行中",
  REVIEWING: "Claude 回复中",
  COMPLETED: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
  FALLBACK_TO_CODEX: "Codex 回复中",
};
const modeLabel: Record<Mode, string> = {
  codex: "发送给 Codex",
  claude: "发送给 Claude",
};
const activeStates = new Set<RunState>([
  "PLANNING",
  "WAITING_APPROVAL",
  "IMPLEMENTING",
  "TESTING",
  "REVIEWING",
  "FALLBACK_TO_CODEX",
]);

export default function App() {
  const store = useStore();
  const workspace = store.workspaces.find(
    (item) => item.id === store.workspaceId,
  );
  const thread = workspace?.threads.find((item) => item.id === store.threadId);
  const text = store.drafts[store.threadId] ?? "";
  const setText = (value: string) => store.setDraft(store.threadId, value);
  const [query, setQuery] = useState("");
  const [rightTab, setRightTab] = useState<
    "status" | "contract" | "handoff" | "recovery"
  >("status");
  const [statusTab, setStatusTab] = useState<"repository" | "diff" | "logs">(
    "repository",
  );
  const [showSettings, setShowSettings] = useState(false);
  const [showProjectDialog, setShowProjectDialog] = useState(false);
  const [showPathDialog, setShowPathDialog] = useState(false);
  const [removeProject, setRemoveProject] = useState<{
    id: string;
    name: string;
  }>();
  const [projectMenu, setProjectMenu] = useState<string>();
  const [settingsTarget, setSettingsTarget] = useState<"general" | "tests">(
    "general",
  );
  const [agentSettings, setAgentSettings] = useState<AgentSettings>();
  const [leftWidth, setLeftWidth] = useState(258);
  const [rightWidth, setRightWidth] = useState(360);
  const [leftHidden, setLeftHidden] = useState(false);
  const [rightHidden, setRightHidden] = useState(false);
  const openWorkspaceInStore = store.openWorkspace;
  const fileInput = useRef<HTMLInputElement>(null);
  const messageStream = useRef<HTMLDivElement>(null);
  const messageEnd = useRef<HTMLDivElement>(null);
  const [followingLatest, setFollowingLatest] = useState(true);
  const activeTasks = useMemo(
    () =>
      store.workspaces
        .flatMap((item) =>
          item.threads.map((task) => ({
            workspaceId: item.id,
            workspace: item.name,
            task,
          })),
        )
        .filter(({ task }) => activeStates.has(task.state)),
    [store.workspaces],
  );
  const hasRecovery =
    store.executionJobs.some(
      (job) => job.status === "FAILED" || job.status === "INTERRUPTED",
    ) ||
    Boolean(
      store.details?.runs.some(
        (run) =>
          run.state === "FAILED" || run.state === "CANCELLED" || run.can_undo,
      ),
    );
  const filteredWorkspaces = useMemo(
    () =>
      store.workspaces
        .map((item) => ({
          ...item,
          threads: item.threads.filter(
            (task) =>
              !query ||
              `${item.name} ${task.title}`
                .toLowerCase()
                .includes(query.toLowerCase()),
          ),
        }))
        .filter(
          (item) =>
            item.threads.length ||
            item.name.toLowerCase().includes(query.toLowerCase()),
        ),
    [store.workspaces, query],
  );
  useEffect(() => {
    if (store.backend === "online" && !showSettings)
      void api
        .fetchAgentSettings()
        .then(setAgentSettings)
        .catch(() => undefined);
  }, [store.backend, showSettings]);
  useEffect(() => {
    if (rightTab === "recovery" && !hasRecovery) setRightTab("status");
  }, [hasRecovery, rightTab]);
  const latestMessageLength = thread?.messages.at(-1)?.text.length ?? 0;
  const selectedThreadId = thread?.id;
  useEffect(() => {
    if (!selectedThreadId || !followingLatest) return;
    // 跟随滚动必须用即时定位：smooth 动画会被下一个 delta 重启（闪烁），
    // 且动画途中 onScroll 读到 >80px 的距离会把跟随状态误判成 false。
    const frame = window.requestAnimationFrame(() =>
      messageEnd.current?.scrollIntoView({ block: "end", behavior: "auto" }),
    );
    return () => window.cancelAnimationFrame(frame);
  }, [
    selectedThreadId,
    thread?.messages.length,
    latestMessageLength,
    thread?.state,
    followingLatest,
  ]);
  useEffect(() => setFollowingLatest(true), [selectedThreadId]);
  const [missedMessages, setMissedMessages] = useState(0);
  const seenMessageCount = useRef(0);
  const currentMessageCount = thread?.messages.length ?? 0;
  useEffect(() => {
    // 先取差值再改写 ref：函数式更新的执行晚于下一行的 ref 赋值。
    const delta = currentMessageCount - seenMessageCount.current;
    if (!followingLatest && delta > 0)
      setMissedMessages((count) => count + delta);
    seenMessageCount.current = currentMessageCount;
  }, [currentMessageCount, followingLatest]);
  useEffect(() => {
    if (followingLatest) setMissedMessages(0);
  }, [followingLatest]);
  useEffect(() => {
    setMissedMessages(0);
    seenMessageCount.current = thread?.messages.length ?? 0;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅在切换任务时重置
  }, [selectedThreadId]);
  const scrollToLatest = () => {
    setFollowingLatest(true);
    messageEnd.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  };

  const openWorkspace = useCallback(async () => {
    if (!isTauri()) {
      setShowPathDialog(true);
      return;
    }
    const selected = await open({
      directory: true,
      multiple: false,
      title: "选择本地 Git 仓库",
    });
    if (typeof selected === "string" && selected.trim())
      await openWorkspaceInStore(selected.trim());
  }, [openWorkspaceInStore]);
  const chooseDirectory = async () => {
    if (!isTauri()) return undefined;
    const selected = await open({
      directory: true,
      multiple: false,
      title: "选择本地空目录",
    });
    return typeof selected === "string" ? selected : undefined;
  };
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      const target = event.target;
      const editing =
        target instanceof HTMLElement &&
        target.matches("input, textarea, select, [contenteditable=true]");
      if (
        !editing &&
        (event.ctrlKey || event.metaKey) &&
        event.key.toLowerCase() === "o"
      ) {
        event.preventDefault();
        void openWorkspace();
      }
      if (event.key === "Escape") setProjectMenu(undefined);
    };
    const pointerdown = (event: PointerEvent) => {
      if (!projectMenu) return;
      const target = event.target as HTMLElement | null;
      if (!target?.closest(".project-menu, [data-project-menu-trigger]"))
        setProjectMenu(undefined);
    };
    window.addEventListener("keydown", keydown);
    window.addEventListener("pointerdown", pointerdown);
    return () => {
      window.removeEventListener("keydown", keydown);
      window.removeEventListener("pointerdown", pointerdown);
    };
  }, [projectMenu, openWorkspace]);
  const run = () => {
    if (store.backend !== "online") return;
    if (!text.trim() && store.draftAttachments.length === 0) return;
    void store.sendPrompt(text.trim());
    setText("");
    // 发送即回到消息尾部并恢复跟随，确保能立刻看到本轮的处理过程。
    scrollToLatest();
  };
  const resize = (
    side: "left" | "right",
    start: React.PointerEvent<HTMLDivElement>,
  ) => {
    start.currentTarget.setPointerCapture(start.pointerId);
    const originX = start.clientX;
    const originWidth = side === "left" ? leftWidth : rightWidth;
    const move = (event: PointerEvent) => {
      const delta = event.clientX - originX;
      if (side === "left")
        setLeftWidth(Math.min(420, Math.max(190, originWidth + delta)));
      else setRightWidth(Math.min(560, Math.max(280, originWidth - delta)));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };
  const dragWindow = (event: React.MouseEvent<HTMLElement>) => {
    if (
      !isTauri() ||
      (event.target as HTMLElement).closest("button, input, select")
    )
      return;
    if (event.detail === 2) void getCurrentWindow().toggleMaximize();
    else void getCurrentWindow().startDragging();
  };

  return (
    <div className="workbench-shell">
      <header
        className="titlebar"
        data-tauri-drag-region
        onMouseDown={dragWindow}
      >
        <div className="brand-mark">
          <Code2 size={15} />
        </div>
        <div>
          <div className="brand-title">DualCode Workbench</div>
          <div className="brand-subtitle">LOCAL AI DEVELOPMENT</div>
        </div>
        <div className="title-context">
          <span>{workspace?.name ?? "未打开项目"}</span>
          {thread && (
            <>
              <span className="crumb">/</span>
              <span>{thread.title}</span>
            </>
          )}
        </div>
        <div className="layout-controls">
          <button
            className="icon-button"
            onClick={() => setLeftHidden((value) => !value)}
            title={leftHidden ? "显示项目栏" : "隐藏项目栏"}
          >
            {leftHidden ? (
              <PanelLeftOpen size={16} />
            ) : (
              <PanelLeftClose size={16} />
            )}
          </button>
          <button
            className="icon-button"
            onClick={() => setRightHidden((value) => !value)}
            title={rightHidden ? "显示检查器" : "隐藏检查器"}
          >
            {rightHidden ? (
              <PanelRightOpen size={16} />
            ) : (
              <PanelRightClose size={16} />
            )}
          </button>
          <button
            className="icon-button"
            onClick={() => setShowSettings(true)}
            title="Agent 与连接设置"
          >
            <Settings2 size={16} />
          </button>
        </div>
        <BackendBadge
          status={store.backend}
          realtime={store.realtime}
          retry={store.initialize}
        />
        {isTauri() && (
          <div className="window-controls">
            <button
              onClick={() => void getCurrentWindow().minimize()}
              title="最小化"
            >
              <Minus size={15} />
            </button>
            <button
              onClick={() => void getCurrentWindow().toggleMaximize()}
              title="最大化"
            >
              <Maximize2 size={13} />
            </button>
            <button
              className="close"
              onClick={() => void getCurrentWindow().close()}
              title="关闭"
            >
              <X size={15} />
            </button>
          </div>
        )}
      </header>

      <main
        className="workspace-grid"
        style={{
          gridTemplateColumns: `${leftHidden ? 0 : leftWidth}px ${leftHidden ? 0 : 4}px minmax(420px,1fr) ${rightHidden ? 0 : 4}px ${rightHidden ? 0 : rightWidth}px`,
        }}
      >
        <aside className={`project-rail ${leftHidden ? "panel-hidden" : ""}`}>
          <div className="project-entry-actions">
            <button
              className="open-project"
              onClick={() => void openWorkspace()}
            >
              <FolderGit2 size={15} />
              <span>打开本地项目</span>
              <kbd>Ctrl+O</kbd>
            </button>
            <button
              className="create-project"
              onClick={() => setShowProjectDialog(true)}
            >
              <Plus size={14} />
              创建 / 克隆
            </button>
          </div>
          <label className="search-box">
            <Search size={13} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索项目和任务"
            />
            {query && (
              <button onClick={() => setQuery("")}>
                <X size={12} />
              </button>
            )}
          </label>
          <div className="project-list">
            {query && filteredWorkspaces.length === 0 && (
              <div className="search-empty">没有匹配的项目或任务</div>
            )}
            {filteredWorkspaces.map((item) => (
              <section key={item.id} className="project-group">
                <div className="project-heading">
                  <ChevronDown size={13} />
                  <div className="min-w-0">
                    <strong>{item.name}</strong>
                    <small title={item.path}>{item.path}</small>
                  </div>
                  <button
                    data-project-menu-trigger
                    onClick={() =>
                      setProjectMenu((current) =>
                        current === item.id ? undefined : item.id,
                      )
                    }
                  >
                    <MoreHorizontal size={14} />
                  </button>
                  {projectMenu === item.id && (
                    <div className="project-menu">
                      <button
                        onClick={() => {
                          setProjectMenu(undefined);
                          setRemoveProject({ id: item.id, name: item.name });
                        }}
                      >
                        从工作台移除<small>保留本地文件</small>
                      </button>
                    </div>
                  )}
                </div>
                <div className="thread-list">
                  {item.threads.map((task) => (
                    <ThreadButton
                      key={task.id}
                      task={task}
                      active={task.id === store.threadId}
                      onClick={() => store.setSelection(item.id, task.id)}
                      rename={(title) => store.renameThread(task.id, title)}
                      remove={() => store.removeThread(task.id)}
                    />
                  ))}
                </div>
                {item.id === store.workspaceId && (
                  <button
                    className="new-thread"
                    disabled={store.creatingThread}
                    onClick={() => void store.newThread()}
                  >
                    <MessageSquarePlus size={13} />
                    {store.creatingThread ? "创建中…" : "新建任务"}
                  </button>
                )}
              </section>
            ))}
          </div>
          <div className="rail-footer">
            <GitBranch size={13} />
            <div>
              <span>本地优先</span>
              <small>不会同步完整项目到 VPS</small>
            </div>
          </div>
        </aside>
        <div
          className={`resize-handle left ${leftHidden ? "panel-hidden" : ""}`}
          onPointerDown={(event) => resize("left", event)}
          onDoubleClick={() => setLeftWidth(258)}
        />

        <section className="conversation-pane">
          {!thread ? (
            <EmptyState onOpen={() => void openWorkspace()} />
          ) : (
            <>
              <TaskHeader thread={thread} />
              {store.backend === "offline" && (
                <div className="backend-offline-banner">
                  <AlertTriangle size={16} />
                  <div>
                    <strong>后端离线</strong>
                    <span>当前无法发送消息或上传附件，请重试连接。</span>
                  </div>
                  <button onClick={() => void store.initialize()}>
                    重试连接
                  </button>
                </div>
              )}
              <div
                className="message-stream"
                ref={messageStream}
                onScroll={(event) => {
                  const target = event.currentTarget;
                  setFollowingLatest(
                    target.scrollHeight -
                      target.scrollTop -
                      target.clientHeight <=
                      80,
                  );
                }}
              >
                {thread.messages.length ? (
                  thread.messages.map((message, messageIndex) => (
                    <MessageCard
                      key={message.id}
                      message={message}
                      retryMessageId={findPreviousUserMessageId(
                        thread.messages,
                        messageIndex,
                      )}
                      retry={store.retryMessage}
                      openRunLogs={() => {
                        setRightHidden(false);
                        setRightTab("status");
                        setStatusTab("logs");
                      }}
                    />
                  ))
                ) : (
                  <div className="new-task-empty">
                    <Bot size={28} />
                    <strong>从产品目标开始</strong>
                    <span>
                      描述目标用户、核心场景、业务需求和约束。先澄清需求与验收标准，再讨论技术方案和实现框架。
                    </span>
                  </div>
                )}
                {activeStates.has(thread.state) && (
                  <ProcessingCard
                    state={thread.state}
                    agent={store.activeAgent ?? store.mode}
                    waitingApproval={Boolean(store.pendingApproval)}
                  />
                )}
                {store.pendingApproval && (
                  <ApprovalCard
                    action={store.pendingApproval.action}
                    reason={store.pendingApproval.reason}
                    decide={store.decideApproval}
                  />
                )}
                <div className="message-end" ref={messageEnd} />
                {!followingLatest && (
                  <button className="back-to-latest" onClick={scrollToLatest}>
                    回到最新
                    {missedMessages > 0 && ` · ${missedMessages} 条新消息`}
                  </button>
                )}
              </div>
              <Composer
                text={text}
                setText={setText}
                mode={store.mode}
                setMode={store.setMode}
                run={run}
                cancel={store.cancelRun}
                running={activeStates.has(thread.state)}
                offline={store.backend === "offline"}
                fileInput={fileInput}
                upload={store.upload}
                attachments={store.draftAttachments}
                removeAttachment={store.removeAttachment}
                notify={store.notify}
              />
            </>
          )}
        </section>
        <div
          className={`resize-handle right ${rightHidden ? "panel-hidden" : ""}`}
          onPointerDown={(event) => resize("right", event)}
          onDoubleClick={() => setRightWidth(360)}
        />

        <aside
          className={`inspector-pane ${rightHidden ? "panel-hidden" : ""}`}
        >
          <div className="inspector-tabs">
            <button
              className={rightTab === "status" ? "active" : ""}
              onClick={() => setRightTab("status")}
            >
              状态
            </button>
            <button
              className={rightTab === "contract" ? "active" : ""}
              onClick={() => setRightTab("contract")}
            >
              规则
            </button>
            <button
              className={rightTab === "handoff" ? "active" : ""}
              onClick={() => setRightTab("handoff")}
            >
              交接
            </button>
            {hasRecovery && (
              <button
                className={`event-tab ${rightTab === "recovery" ? "active" : ""}`}
                onClick={() => setRightTab("recovery")}
              >
                恢复
                <i className="tab-alert" />
              </button>
            )}
          </div>
          {rightTab === "status" && (
            <>
              <div className="inspector-subtabs">
                <button
                  className={statusTab === "repository" ? "active" : ""}
                  onClick={() => setStatusTab("repository")}
                >
                  仓库
                </button>
                <button
                  className={statusTab === "diff" ? "active" : ""}
                  onClick={() => setStatusTab("diff")}
                >
                  变更
                </button>
                <button
                  className={statusTab === "logs" ? "active" : ""}
                  onClick={() => setStatusTab("logs")}
                >
                  运行日志
                </button>
              </div>
              {statusTab === "repository" && (
                <ContextPanel
                  details={store.details}
                  git={store.gitStatus}
                  remote={store.remoteStatus}
                  remoteJobs={store.executionJobs}
                  agentSettings={agentSettings}
                  openSettings={() => {
                    setSettingsTarget("tests");
                    setShowSettings(true);
                  }}
                  saveRemote={store.saveRemote}
                  remoteAction={store.remoteGitAction}
                  runTests={store.runTests}
                />
              )}{" "}
              {statusTab === "diff" && (
                <DiffPanel diff={store.details?.diff ?? ""} />
              )}{" "}
              {statusTab === "logs" && (
                <TerminalPanel
                  lines={store.terminal}
                  truncated={store.terminalTruncated}
                  clear={store.clearTerminal}
                  notify={store.notify}
                />
              )}
            </>
          )}
          {rightTab === "contract" && (
            <ContractPanel
              workspaceId={store.workspaceId}
              threadId={store.threadId}
            />
          )}
          {rightTab === "handoff" && (
            <HandoffPanel
              workspaceId={store.workspaceId}
              threadId={store.threadId}
            />
          )}
          {rightTab === "recovery" && (
            <RecoveryPanel
              jobs={store.executionJobs}
              runs={store.details?.runs ?? []}
              retryingJobId={store.retryingJobId}
              refresh={store.refreshExecutionJobs}
              retry={store.retryExecutionJob}
              undo={store.undoRun}
            />
          )}
        </aside>
      </main>

      <footer className="activity-bar">
        <div className="activity-label">
          <SquareTerminal size={13} />
          <strong>后台任务</strong>
          <span>
            {activeTasks.length
              ? `${activeTasks.length} 个运行中`
              : "无运行任务"}
          </span>
          <small className="activity-snapshot-note">状态以进入任务后为准</small>
        </div>
        <div className="activity-tasks">
          {activeTasks
            .slice(0, 3)
            .map(({ workspaceId, workspace: name, task }) => (
              <button
                key={task.id}
                onClick={() => store.setSelection(workspaceId, task.id)}
              >
                <LoaderCircle size={11} className="spin" />
                {name} · {task.title}
                <small>{stateLabel[task.state]}</small>
              </button>
            ))}
        </div>
        <div className="security-note">
          <ShieldCheck size={12} />
          审批与变更已审计
        </div>
      </footer>
      {store.notifications.length > 0 && (
        <div className="notification-stack" aria-live="polite">
          {store.notifications.map((notification) => (
            <div
              className={`notification-toast ${notification.level}`}
              key={notification.id}
              role={notification.level === "error" ? "alert" : "status"}
            >
              {notification.level === "error" ? (
                <AlertTriangle size={14} />
              ) : (
                <Circle size={11} />
              )}
              <span>{notification.message}</span>
              <button
                aria-label="关闭通知"
                onClick={() => store.dismissNotification(notification.id)}
              >
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
      {showSettings && (
        <SettingsDialog
          target={settingsTarget}
          onClose={() => setShowSettings(false)}
        />
      )}
      {showProjectDialog && (
        <ProjectDialog
          chooseDirectory={chooseDirectory}
          submit={store.provisionWorkspace}
          close={() => setShowProjectDialog(false)}
        />
      )}
      {showPathDialog && (
        <InputDialog
          title="打开本地 Git 仓库"
          placeholder="输入本地 Git 仓库的绝对路径"
          onClose={() => setShowPathDialog(false)}
          onSubmit={(path) => {
            setShowPathDialog(false);
            void openWorkspaceInStore(path);
          }}
        />
      )}
      {removeProject && (
        <ConfirmDialog
          title="从工作台移除项目"
          message={`确认移除“${removeProject.name}”？本地仓库文件不会被删除。`}
          confirmLabel="确认移除"
          danger
          onClose={() => setRemoveProject(undefined)}
          onConfirm={() => {
            const id = removeProject.id;
            setRemoveProject(undefined);
            void store.removeWorkspace(id);
          }}
        />
      )}
    </div>
  );
}

function BackendBadge({
  status,
  realtime,
  retry,
}: {
  status: "connecting" | "online" | "offline";
  realtime: ReturnType<typeof useStore.getState>["realtime"];
  retry: () => Promise<void>;
}) {
  return (
    <div className={`backend-badge ${status}`}>
      <span />
      {status === "online" ? (
        realtime === "reconnecting" ? (
          "实时连接重连中"
        ) : realtime === "connecting" ? (
          "实时连接中"
        ) : (
          "后端在线"
        )
      ) : status === "connecting" ? (
        "后端启动中"
      ) : (
        <button onClick={() => void retry()}>后端离线 · 重试</button>
      )}
    </div>
  );
}
function ProcessingCard({
  state,
  agent,
  waitingApproval,
}: {
  state: RunState;
  agent: Mode;
  waitingApproval: boolean;
}) {
  const name = agent === "codex" ? "Codex" : "Claude";
  const title = waitingApproval
    ? "等待你的授权"
    : state === "PLANNING"
      ? `${name} 正在思考`
      : state === "TESTING"
        ? "正在运行测试"
        : `${name} 正在处理`;
  const detail = waitingApproval
    ? "本轮需要执行受保护操作，处理审批后将继续。"
    : state === "PLANNING"
      ? "正在理解需求、读取上下文并组织回复。"
      : state === "TESTING"
        ? "正在执行已批准的测试命令并收集结果。"
        : "正在读取项目、调用工具并整理结果。";
  return (
    <div className={`processing-card ${waitingApproval ? "waiting" : ""}`}>
      <div className="processing-orb">
        <LoaderCircle size={15} className={waitingApproval ? "" : "spin"} />
      </div>
      <div>
        <strong>
          {title}
          <span className="thinking-dots">
            <i />
            <i />
            <i />
          </span>
        </strong>
        <small>{detail}</small>
      </div>
    </div>
  );
}
function ThreadButton({
  task,
  active,
  onClick,
  rename,
  remove,
}: {
  task: Thread;
  active: boolean;
  onClick: () => void;
  rename: (title: string) => Promise<void>;
  remove: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(task.title);
  const [menu, setMenu] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const save = async () => {
    const normalized = title.trim();
    if (normalized && normalized !== task.title) await rename(normalized);
    else setTitle(task.title);
    setEditing(false);
  };
  return (
    <div
      className={`thread-button ${active ? "active" : ""}`}
      onClick={onClick}
    >
      <span className={`state-dot ${task.state.toLowerCase()}`} />
      <div>
        {editing ? (
          <input
            autoFocus
            value={title}
            onClick={(event) => event.stopPropagation()}
            onChange={(event) => setTitle(event.target.value)}
            onBlur={() => void save()}
            onKeyDown={(event) => {
              if (event.key === "Enter") void save();
              if (event.key === "Escape") {
                setTitle(task.title);
                setEditing(false);
              }
            }}
          />
        ) : (
          <strong onDoubleClick={() => setEditing(true)}>{task.title}</strong>
        )}
        <small>{stateLabel[task.state]}</small>
      </div>
      {activeStates.has(task.state) ? (
        <LoaderCircle size={12} className="spin" />
      ) : (
        <button
          className="thread-menu-trigger"
          aria-label={`管理任务 ${task.title}`}
          onClick={(event) => {
            event.stopPropagation();
            setMenu((value) => !value);
            setConfirmingDelete(false);
          }}
        >
          <MoreHorizontal size={13} />
        </button>
      )}
      {menu && (
        <div
          className="thread-menu"
          onClick={(event) => event.stopPropagation()}
        >
          <button
            onClick={() => {
              setMenu(false);
              setEditing(true);
            }}
          >
            重命名
          </button>
          <button
            className={confirmingDelete ? "danger" : ""}
            onClick={() => {
              if (!confirmingDelete) return setConfirmingDelete(true);
              setMenu(false);
              void remove();
            }}
          >
            {confirmingDelete ? "确认删除任务" : "删除任务"}
          </button>
        </div>
      )}
    </div>
  );
}
function TaskHeader({ thread }: { thread: Thread }) {
  const running = activeStates.has(thread.state);
  return (
    <div className="task-header compact">
      <div className="task-title-row">
        <div>
          <small>开发会话</small>
          <h1>{thread.title}</h1>
        </div>
        <span className={`task-state ${thread.state.toLowerCase()}`}>
          {running && <LoaderCircle size={12} className="spin" />}
          {stateLabel[thread.state]}
        </span>
      </div>
    </div>
  );
}
function thoughtDuration(step: {
  startedAt?: number;
  completedAt?: number;
}): string {
  if (!step.startedAt || !step.completedAt) return "";
  const seconds = Math.max(
    1,
    Math.round((step.completedAt - step.startedAt) / 1000),
  );
  return ` ${seconds} 秒`;
}
function ActivityCard({
  activity,
  openRunLogs,
}: {
  activity: NonNullable<Message["activity"]>;
  openRunLogs: () => void;
}) {
  const failedSteps = activity.steps.filter((step) => step.status === "failed");
  const syntheticFailure =
    activity.error && failedSteps.length === 0
      ? {
          id: `failure-${activity.runId}`,
          kind: "tool" as const,
          label: "执行失败",
          status: "failed" as const,
          detail: undefined,
        }
      : undefined;
  const steps = syntheticFailure
    ? [...activity.steps, syntheticFailure]
    : activity.steps;
  const errorStepId = syntheticFailure?.id ?? failedSteps.at(-1)?.id;
  if (steps.length === 0) return null;
  return (
    <section
      className={`agent-activity ${activity.status}`}
      aria-label="执行活动"
    >
      {steps.map((step, index) =>
        step.kind === "reasoning" ? (
          step.detail &&
          step.detail !== "reasoning" &&
          (step.status === "running" ? (
            <div
              className="thinking-block running"
              key={step.id}
              aria-label="思考过程"
            >
              <header>
                <span className="thinking-pulse" aria-hidden="true" />
                正在思考…
              </header>
              <p>{step.detail}</p>
            </div>
          ) : (
            <details className="thought-pill" key={step.id}>
              <summary>
                <span>已思考{thoughtDuration(step)}</span>
                <ChevronDown size={12} />
              </summary>
              <p>{step.detail}</p>
            </details>
          ))
        ) : (
          <details
            className={`tool-activity-row ${step.status}`}
            key={step.id}
            data-order={index}
          >
            <summary>
              <span className="tool-activity-icon">
                {step.kind === "command" ? (
                  <SquareTerminal size={14} />
                ) : step.kind === "file" ? (
                  <Pencil size={14} />
                ) : (
                  <Settings2 size={14} />
                )}
              </span>
              <span className="tool-activity-title">
                <strong>{step.label}</strong>
                {step.id === errorStepId && activity.error && (
                  <small>{activity.error}</small>
                )}
              </span>
              <span className="tool-activity-status" aria-label={step.status}>
                {step.status === "running" ? (
                  <LoaderCircle size={14} className="spin" />
                ) : step.status === "completed" ? (
                  <Check size={14} />
                ) : (
                  <X size={14} />
                )}
              </span>
              <ChevronDown size={13} className="tool-activity-chevron" />
            </summary>
            <div className="tool-activity-detail">
              {step.detail && step.detail !== "reasoning" && (
                <div>
                  <small>{step.kind === "command" ? "命令" : "详情"}</small>
                  <pre>{step.detail}</pre>
                </div>
              )}
              <button type="button" onClick={openRunLogs}>
                <SquareTerminal size={13} />
                查看运行日志
              </button>
            </div>
          </details>
        ),
      )}
    </section>
  );
}
function MessageCard({
  message,
  retryMessageId,
  retry,
  openRunLogs,
}: {
  message: Message;
  retryMessageId?: string;
  retry: (id: string, content?: string) => Promise<void>;
  openRunLogs: () => void;
}) {
  const { agent, time, text } = message;
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(text);
  const [savingEdit, setSavingEdit] = useState(false);
  const { copyState, copyText } = useCopyFeedback();
  const isStreaming = message.id.startsWith("stream-");
  const workspaceId = useStore((state) => state.workspaceId);
  const threadId = useStore((state) => state.threadId);
  const names: Record<Agent, string> = {
    user: "你",
    codex: "Codex",
    claude: "Claude",
    system: "System",
  };
  if (message.activity) {
    return (
      <ActivityCard activity={message.activity} openRunLogs={openRunLogs} />
    );
  }
  if (agent === "system")
    return (
      <article className="system-event">
        <span>
          <SquareTerminal size={12} />
        </span>
        <div>
          <strong>System</strong>
          <p>{text}</p>
        </div>
        {time && <time>{time}</time>}
      </article>
    );
  return (
    <article className={`message-card ${agent}`}>
      {agent !== "user" && (
        <div className="message-avatar">
          {agent === "codex" ? <Code2 size={14} /> : "C"}
        </div>
      )}
      <div className="message-body">
        {agent === "user" && (
          <div className="message-actions" role="toolbar" aria-label="消息操作">
            <>
              <button
                type="button"
                onClick={() => {
                  setEditText(text);
                  setEditing(true);
                }}
              >
                <Pencil size={13} />
                编辑
              </button>
              <button type="button" onClick={() => void retry(message.id)}>
                <RotateCcw size={13} />
                重试本轮
              </button>
            </>
          </div>
        )}
        {agent !== "user" && (
          <header>
            <strong>{names[agent]}</strong>
            {time && <time>{time}</time>}
            <span>{agent === "codex" ? "本地执行" : "远程规划 / 审查"}</span>
          </header>
        )}
        <div className="message-content">
          {message.attachments?.length ? (
            <div className="message-attachments">
              {message.attachments.map((item) => {
                const url = api.attachmentContentUrl(
                  workspaceId,
                  threadId,
                  item.id,
                );
                return item.media_type.startsWith("image/") ? (
                  <ImageAttachment key={item.id} url={url} name={item.name} />
                ) : (
                  <a key={item.id} href={url} target="_blank" rel="noreferrer">
                    {item.name}
                  </a>
                );
              })}
            </div>
          ) : null}
          {agent === "user" && editing ? (
            <div className="message-inline-edit">
              <textarea
                aria-label="编辑消息"
                autoFocus
                value={editText}
                onChange={(event) => setEditText(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    setEditText(text);
                    setEditing(false);
                  }
                }}
              />
              <div className="message-inline-edit-actions">
                <button
                  type="button"
                  disabled={savingEdit}
                  onClick={() => {
                    setEditText(text);
                    setEditing(false);
                  }}
                >
                  <X size={13} />
                  取消
                </button>
                <button
                  type="button"
                  disabled={savingEdit || !editText.trim()}
                  onClick={() => {
                    setSavingEdit(true);
                    void retry(message.id, editText.trim())
                      .then(() => setEditing(false))
                      .catch(() => undefined)
                      .finally(() => setSavingEdit(false));
                  }}
                >
                  {savingEdit ? (
                    <LoaderCircle className="spin" size={13} />
                  ) : (
                    <Check size={13} />
                  )}
                  {savingEdit ? "保存中" : "保存并重发"}
                </button>
              </div>
            </div>
          ) : isStreaming ? (
            <p className="streaming-message" aria-label="正在生成回复">
              {text}
            </p>
          ) : agent === "user" ? (
            <p className="plain-text">{text}</p>
          ) : (
            <MarkdownMessage text={text} />
          )}
        </div>
        {agent !== "user" && !isStreaming && (
          <div
            className="assistant-message-actions"
            role="toolbar"
            aria-label="助手消息操作"
          >
            <button
              type="button"
              aria-label={
                copyState === "copied"
                  ? "消息已复制"
                  : copyState === "failed"
                    ? "复制消息失败"
                    : "复制消息"
              }
              title={copyState === "failed" ? "复制失败" : "复制 Markdown 原文"}
              onClick={() => void copyText(text)}
            >
              {copyState === "copied" ? (
                <Check size={16} />
              ) : (
                <Copy size={16} />
              )}
            </button>
            <button
              type="button"
              aria-label="重试本轮"
              title="重试本轮"
              disabled={!retryMessageId}
              onClick={() => retryMessageId && void retry(retryMessageId)}
            >
              <RotateCcw size={16} />
            </button>
          </div>
        )}
      </div>
    </article>
  );
}

function findPreviousUserMessageId(messages: Message[], fromIndex: number) {
  for (let index = fromIndex - 1; index >= 0; index -= 1) {
    if (messages[index].agent === "user") return messages[index].id;
  }
  return undefined;
}
export function ImageAttachment({ url, name }: { url: string; name: string }) {
  const [openPreview, setOpenPreview] = useState(false);
  const [scale, setScale] = useState(1);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const canvasRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<
    | {
        pointerId: number;
        x: number;
        y: number;
        originX: number;
        originY: number;
      }
    | undefined
  >(undefined);
  useEffect(() => {
    if (!openPreview) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenPreview(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [openPreview]);
  const zoom = (next: number) => setScale(Math.min(4, Math.max(0.25, next)));
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !openPreview) return;
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      setScale((current) =>
        Math.min(
          4,
          Math.max(0.25, current + (event.deltaY < 0 ? 0.15 : -0.15)),
        ),
      );
    };
    canvas.addEventListener("wheel", handleWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", handleWheel);
  }, [openPreview]);
  const resetView = () => {
    setScale(1);
    setPosition({ x: 0, y: 0 });
  };
  return (
    <>
      <button
        className="message-image"
        type="button"
        onClick={() => {
          resetView();
          setOpenPreview(true);
        }}
        aria-label={`预览图片 ${name}`}
      >
        <img src={url} alt={name} />
        <span>{name}</span>
      </button>
      {openPreview && (
        <div
          className="image-preview"
          role="dialog"
          aria-modal="true"
          aria-label={`图片预览 ${name}`}
          onMouseDown={() => setOpenPreview(false)}
        >
          <div
            className="image-preview-toolbar"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <strong title={name}>{name}</strong>
            <span>{Math.round(scale * 100)}%</span>
            <button onClick={() => zoom(scale - 0.25)}>缩小</button>
            <button onClick={resetView}>实际大小</button>
            <button onClick={() => zoom(scale + 0.25)}>放大</button>
            <a href={url} target="_blank" rel="noreferrer">
              打开原图
            </a>
            <button
              className="preview-close"
              onClick={() => setOpenPreview(false)}
              aria-label="关闭图片预览"
            >
              <X size={16} />
            </button>
          </div>
          <div
            ref={canvasRef}
            className="image-preview-canvas"
            onMouseDown={(event) => event.stopPropagation()}
            onPointerDown={(event) => {
              event.stopPropagation();
              event.currentTarget.setPointerCapture(event.pointerId);
              dragRef.current = {
                pointerId: event.pointerId,
                x: event.clientX,
                y: event.clientY,
                originX: position.x,
                originY: position.y,
              };
            }}
            onPointerMove={(event) => {
              const drag = dragRef.current;
              if (!drag || drag.pointerId !== event.pointerId) return;
              setPosition({
                x: drag.originX + event.clientX - drag.x,
                y: drag.originY + event.clientY - drag.y,
              });
            }}
            onPointerUp={(event) => {
              if (dragRef.current?.pointerId === event.pointerId) {
                dragRef.current = undefined;
                event.currentTarget.releasePointerCapture(event.pointerId);
              }
            }}
          >
            <img
              src={url}
              alt={name}
              draggable={false}
              style={{
                transform: `translate(${position.x}px, ${position.y}px) scale(${scale})`,
              }}
            />
          </div>
        </div>
      )}
    </>
  );
}
export function Composer({
  text,
  setText,
  mode,
  setMode,
  run,
  cancel,
  running,
  offline,
  fileInput,
  upload,
  attachments,
  removeAttachment,
  notify,
}: {
  text: string;
  setText: (value: string) => void;
  mode: Mode;
  setMode: (mode: Mode) => void;
  run: () => void;
  cancel: () => Promise<void>;
  running: boolean;
  offline: boolean;
  fileInput: React.RefObject<HTMLInputElement | null>;
  upload: (file: File) => Promise<void>;
  attachments: { id: string; name: string; media_type: string }[];
  removeAttachment: (id: string) => void;
  notify: (level: "info" | "error", message: string) => void;
}) {
  const emptyDraft = !text.trim() && attachments.length === 0;
  const composerWorkspaceId = useStore((state) => state.workspaceId);
  const composerThreadId = useStore((state) => state.threadId);
  const [dragging, setDragging] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    const maxHeight = window.innerHeight * 0.4;
    textarea.style.height = `${Math.min(textarea.scrollHeight, maxHeight)}px`;
    textarea.style.overflowY =
      textarea.scrollHeight > maxHeight ? "auto" : "hidden";
  }, [text]);
  const addFiles = (files: File[]) => {
    if (offline || files.length === 0) return;
    const remaining = Math.max(0, 8 - attachments.length);
    if (files.length > remaining) notify("error", "每条消息最多添加 8 个附件");
    for (const file of files.slice(0, remaining)) void upload(file);
  };
  return (
    <div className="composer-wrap">
      <div
        className={`composer ${dragging ? "dragging" : ""}`}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          addFiles(Array.from(event.dataTransfer.files));
        }}
        onDragEnter={(event) => {
          event.preventDefault();
          if (!offline) setDragging(true);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node))
            setDragging(false);
        }}
        onDragOver={(event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = "copy";
        }}
      >
        {attachments.length > 0 && (
          <div className="attachment-tray">
            {attachments.map((item) => (
              <div className="attachment-chip" key={item.id}>
                {item.media_type.startsWith("image/") ? (
                  <img
                    className="attachment-thumb"
                    src={api.attachmentContentUrl(
                      composerWorkspaceId,
                      composerThreadId,
                      item.id,
                    )}
                    alt={item.name}
                  />
                ) : (
                  <ImagePlus size={13} />
                )}
                <span>{item.name}</span>
                <button
                  aria-label={`移除附件 ${item.name}`}
                  onClick={() => removeAttachment(item.id)}
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
        )}
        <textarea
          ref={textareaRef}
          title="Enter 发送，Shift+Enter 换行"
          value={text}
          disabled={offline}
          onChange={(event) => setText(event.target.value)}
          onPaste={(event) => {
            if (offline) return;
            const files = Array.from(event.clipboardData.files);
            addFiles(files);
          }}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return;
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (!running) run();
            }
          }}
          placeholder={
            offline
              ? "后端离线，请重试连接…"
              : running
                ? "Agent 处理中；可以先起草下一条消息…"
                : "输入消息；可以拖入文件或粘贴截图…"
          }
        />
        <div className="composer-tools">
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg,image/webp,text/plain"
            multiple
            hidden
            onChange={(event) => {
              addFiles(Array.from(event.target.files ?? []));
              event.target.value = "";
            }}
          />
          <button
            disabled={offline}
            title="添加附件"
            aria-label="添加附件"
            onClick={() => fileInput.current?.click()}
          >
            <Plus size={16} />
          </button>
          <select
            aria-label="选择 Agent"
            title="选择 Agent"
            disabled={offline}
            value={mode}
            onChange={(event) => setMode(event.target.value as Mode)}
          >
            {Object.entries(modeLabel).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <button
            disabled={offline || (!running && emptyDraft)}
            className={`run-button ${running ? "stop" : ""}`}
            aria-label={running ? "停止" : "发送"}
            title={running ? "停止当前任务" : "发送消息"}
            onClick={() => (running ? void cancel() : run())}
          >
            {running ? <Square size={14} /> : <ArrowUp size={17} />}
          </button>
        </div>
      </div>
    </div>
  );
}
function ApprovalCard({
  action,
  reason,
  decide,
}: {
  action: string;
  reason: string;
  decide: (approved: boolean, scope?: "once" | "thread") => Promise<void>;
}) {
  const labels: Record<string, string> = {
    network_access: "发送给 Claude",
    edit_files: "允许 Codex 本轮操作",
    codex_command: "允许 Codex 执行命令",
    codex_file_change: "允许 Codex 修改文件",
    codex_permissions: "允许 Codex 扩大权限",
    undo_codex_run: "撤销 Codex 本轮修改",
    remote_edit_files: "允许 Claude 远端操作",
    run_test: "运行本地测试",
    git_commit: "Git 提交",
    git_push: "推送远程",
    git_pull: "拉取远程代码",
    remote_git_fetch: "VPS 获取远程状态",
    remote_git_pull: "VPS 拉取远程代码",
  };
  const scoped = action === "edit_files" || action === "remote_edit_files";
  return (
    <section className="approval-card">
      <header>
        <ShieldCheck size={16} />
        <div>
          <small>本轮操作需要授权</small>
          <strong>{labels[action] ?? action}</strong>
        </div>
      </header>
      <p>{reason}</p>
      <div>
        <button onClick={() => void decide(false)}>取消本轮</button>
        <button onClick={() => void decide(true, "once")}>允许一次</button>
        {scoped && (
          <button
            className="approve"
            onClick={() => void decide(true, "thread")}
          >
            允许本任务
          </button>
        )}
      </div>
    </section>
  );
}
function ContextPanel({
  details,
  git,
  remote,
  remoteJobs,
  agentSettings,
  openSettings,
  saveRemote,
  remoteAction,
  runTests,
}: {
  details?: ReturnType<typeof useStore.getState>["details"];
  git?: GitStatus | null;
  remote?: WorkspaceRemoteStatus;
  remoteJobs: ExecutionJob[];
  agentSettings?: AgentSettings;
  openSettings: () => void;
  saveRemote: (url: string, path: string) => Promise<void>;
  remoteAction: (
    action: "provision" | "repair_provision" | "fetch" | "pull",
  ) => Promise<void>;
  runTests: () => Promise<void>;
}) {
  const test = details?.tests.at(-1);
  const testConfigured = Boolean(agentSettings?.test_executable);
  const testCommand = testConfigured
    ? [
        agentSettings?.test_executable,
        ...(agentSettings?.test_arguments ?? []),
      ].join(" ")
    : "";
  return (
    <div className="inspector-scroll">
      <InspectorSection title="仓库状态" icon={<GitBranch size={13} />}>
        {git === undefined ? (
          <div className="inspector-loading">
            <LoaderCircle size={13} className="spin" />
            正在读取仓库状态…
          </div>
        ) : git === null ? (
          <div className="empty-inline">
            无法读取仓库状态，请确认项目目录仍是有效的 Git 仓库
          </div>
        ) : (
          <>
            {!git.head && (
              <div className="empty-repository">
                <strong>远程仓库为空</strong>
                <span>
                  origin 已关联，但还没有首次提交。现在可以直接描述项目需求，让
                  Codex 创建初始文件。
                </span>
                <small>
                  文件生成完成后，Codex 会提出提交或推送操作，由你审批确认。
                </small>
              </div>
            )}
            <dl className="git-meta">
              <dt>分支</dt>
              <dd>{git.branch || "未检测"}</dd>
              <dt>HEAD</dt>
              <dd>{git.head || "尚无提交"}</dd>
              <dt>上游</dt>
              <dd title={git.upstream}>
                {git.upstream || (git.remote ? "等待首次推送" : "未设置")}
              </dd>
              <dt>同步</dt>
              <dd>
                {git.upstream
                  ? `领先 ${git.ahead} · 落后 ${git.behind}`
                  : git.remote
                    ? "远程已关联"
                    : "无远程跟踪"}
              </dd>
            </dl>
          </>
        )}
      </InspectorSection>
      <RemoteRepository
        remote={remote}
        jobs={remoteJobs}
        save={saveRemote}
        action={remoteAction}
      />
      <InspectorSection title="测试" icon={<TestTube2 size={13} />}>
        <div className="test-command">
          {testConfigured ? (
            <>
              <small>将执行</small>
              <code title={testCommand}>{testCommand}</code>
              <span>运行前会请求一次审批</span>
            </>
          ) : (
            <>
              <strong>尚未配置测试命令</strong>
              <span>配置当前项目使用的 pytest、pnpm test 等命令。</span>
            </>
          )}
        </div>
        <div className="test-actions">
          {testConfigured ? (
            <button onClick={() => void runTests()}>
              <Play size={11} />
              运行测试
            </button>
          ) : (
            <button onClick={openSettings}>
              <Settings2 size={11} />
              配置测试命令
            </button>
          )}
        </div>
        <div
          className={`test-result ${test ? (test.exit_code === 0 ? "pass" : "fail") : ""}`}
        >
          {test ? (
            <>
              <div>
                {test.exit_code === 0 ? <Check size={13} /> : <X size={13} />}
                <strong>
                  {test.exit_code === 0 ? "测试通过" : "测试失败"}
                </strong>
              </div>
              <pre>{test.output.trim()}</pre>
            </>
          ) : (
            <EmptyInline text="尚未运行测试" />
          )}
        </div>
      </InspectorSection>
    </div>
  );
}
function TerminalPanel({
  lines,
  truncated,
  clear,
  notify,
}: {
  lines: string[];
  truncated: boolean;
  clear: () => void;
  notify: (level: "info" | "error", message: string) => void;
}) {
  const output = useRef<HTMLPreElement>(null);
  const [following, setFollowing] = useState(true);
  useEffect(() => {
    const element = output.current;
    if (element && following) element.scrollTop = element.scrollHeight;
  }, [lines, following]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      notify("info", "运行日志已复制到剪贴板");
    } catch {
      notify("error", "复制失败，请检查剪贴板权限");
    }
  };
  return (
    <div className="terminal-panel">
      {lines.length ? (
        <>
          <div className="terminal-toolbar">
            {truncated && <span>仅保留最近 500 行</span>}
            <button onClick={() => void copy()}>复制</button>
            <button onClick={clear}>清空</button>
          </div>
          <pre
            ref={output}
            onScroll={(event) => {
              const target = event.currentTarget;
              setFollowing(
                target.scrollHeight - target.scrollTop - target.clientHeight <=
                  40,
              );
            }}
          >
            {lines.join("\n")}
          </pre>
        </>
      ) : (
        <div className="panel-empty">
          <SquareTerminal size={22} />
          <strong>终端等待中</strong>
          <span>测试和本地进程输出会实时显示在这里。</span>
        </div>
      )}
    </div>
  );
}
export function RecoveryPanel({
  jobs,
  runs = [],
  retryingJobId,
  refresh,
  retry,
  undo = async () => undefined,
}: {
  jobs: ExecutionJob[];
  runs?: NonNullable<ReturnType<typeof useStore.getState>["details"]>["runs"];
  retryingJobId?: string;
  refresh: () => Promise<void>;
  retry: (id: string) => Promise<void>;
  undo?: (id: string) => Promise<void>;
}) {
  const visible = jobs.filter(
    (job) => job.status === "FAILED" || job.status === "INTERRUPTED",
  );
  const failedRuns = runs.filter(
    (run) =>
      run.state === "FAILED" || run.state === "CANCELLED" || run.can_undo,
  );
  const label = (job: ExecutionJob) =>
    job.kind === "git_action"
      ? `Git ${String(job.payload.action ?? "操作")}`
      : job.kind === "remote_git"
        ? `VPS ${String(job.payload.action ?? "操作")}`
        : job.kind === "test_run"
          ? "运行测试"
          : job.kind;
  return (
    <div className="recovery-panel">
      <header className="recovery-heading">
        <div>
          <strong>任务恢复中心</strong>
          <span>中断和失败的操作不会自动重放；仓库现有修改会保留。</span>
        </div>
        <button title="刷新任务" onClick={() => void refresh()}>
          <RefreshCw size={13} />
        </button>
      </header>
      {visible.length || failedRuns.length ? (
        <div className="recovery-list">
          {failedRuns.map((run) => (
            <article className="recovery-job interrupted" key={run.id}>
              <header>
                <span>
                  <AlertTriangle size={14} />
                </span>
                <div>
                  <strong>
                    {run.state === "COMPLETED"
                      ? "Codex 本轮修改"
                      : run.agent === "codex"
                        ? "Codex 轮次中断"
                        : "Agent 轮次中断"}
                  </strong>
                  <small>
                    {run.state === "FAILED"
                      ? "运行失败"
                      : run.state === "COMPLETED"
                        ? "可恢复到本轮开始前"
                        : "已停止或在重启时中断"}
                  </small>
                </div>
              </header>
              <p>
                {run.output ||
                  "本轮没有完整结束。请检查 Diff 和终端，再决定重新发送或继续。"}
              </p>
              {run.can_undo && (
                <div className="recovery-meta">
                  <time>执行前会校验当前 Diff</time>
                  <button onClick={() => void undo(run.id)}>
                    <RotateCcw size={12} />
                    撤销本轮修改
                  </button>
                </div>
              )}
            </article>
          ))}
          {visible.map((job) => (
            <article
              className={`recovery-job ${job.status.toLowerCase()}`}
              key={job.id}
            >
              <header>
                <span>
                  <AlertTriangle size={14} />
                </span>
                <div>
                  <strong>{label(job)}</strong>
                  <small>
                    {job.status === "INTERRUPTED"
                      ? "后端重启时中断"
                      : "执行失败"}{" "}
                    · 已尝试 {job.attempts} 次
                  </small>
                </div>
              </header>
              <p>执行未完成。请先检查当前状态和终端输出，再决定是否重试。</p>
              {(job.kind === "git_action" || job.kind === "remote_git") && (
                <ExecutionEvidence job={job} />
              )}
              <div className="recovery-meta">
                <time>{new Date(job.updated_at).toLocaleString("zh-CN")}</time>
                <button
                  disabled={Boolean(retryingJobId)}
                  onClick={() => void retry(job.id)}
                >
                  {retryingJobId === job.id ? (
                    <LoaderCircle size={12} className="spin" />
                  ) : (
                    <RotateCcw size={12} />
                  )}
                  显式重试
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="panel-empty">
          <Check size={22} />
          <strong>没有待恢复任务</strong>
          <span>所有持久化操作均已完成，或正在等待审批与执行。</span>
        </div>
      )}
    </div>
  );
}
function InspectorSection({
  title,
  icon,
  badge,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  badge?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="inspector-section">
      <header>
        {icon}
        <strong>{title}</strong>
        {badge && <span>{badge}</span>}
      </header>
      {children}
    </section>
  );
}
function EmptyInline({ text }: { text: string }) {
  return (
    <div className="empty-inline">
      <Circle size={8} />
      {text}
    </div>
  );
}
function EmptyState({ onOpen }: { onOpen: () => void }) {
  return (
    <div className="full-empty">
      <div>
        <FolderGit2 size={30} />
      </div>
      <h2>打开本地代码项目</h2>
      <p>
        DualCode 会在隔离 worktree 中让 Codex 执行，并由远程 Claude 规划和审查。
      </p>
      <button onClick={onOpen}>
        <FolderGit2 size={14} />
        选择 Git 仓库
      </button>
      <small>项目内容默认不会同步到 VPS</small>
    </div>
  );
}
