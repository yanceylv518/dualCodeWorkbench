import { useEffect, useMemo, useRef, useState } from "react";
import Editor from "@monaco-editor/react";
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { open } from "@tauri-apps/plugin-dialog";
import {
  AlertTriangle, Bot, Check, ChevronDown, Circle, Clock3, Cloud, Code2, FileCode2,
  FolderGit2, GitBranch, ImagePlus, LoaderCircle, MessageSquarePlus, MoreHorizontal,
  Paperclip, Play, Plus, Search, Settings2, ShieldCheck, Square, SquareTerminal,
  TestTube2, X, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen,
  Minus, Maximize2,
} from "lucide-react";
import { SettingsDialog } from "./SettingsDialog";
import { useStore, type Mode } from "./store";
import type { Agent, GitStatus, Message, RunState, Thread, WorkspaceRemoteStatus } from "./types";

const stateLabel: Record<RunState, string> = { CREATED: "空闲", PLANNING: "Claude 回复中", WAITING_APPROVAL: "等待授权", IMPLEMENTING: "Codex 回复中", TESTING: "测试运行中", REVIEWING: "Claude 回复中", COMPLETED: "已完成", FAILED: "失败", CANCELLED: "已取消", FALLBACK_TO_CODEX: "Codex 回复中" };
const modeLabel: Record<Mode, string> = { codex: "发送给 Codex", claude: "发送给 Claude" };
const activeStates = new Set<RunState>(["PLANNING", "WAITING_APPROVAL", "IMPLEMENTING", "TESTING", "REVIEWING", "FALLBACK_TO_CODEX"]);

export default function App() {
  const store = useStore();
  const workspace = store.workspaces.find((item) => item.id === store.workspaceId);
  const thread = workspace?.threads.find((item) => item.id === store.threadId);
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [rightTab, setRightTab] = useState<"context" | "diff" | "terminal">("context");
  const [showSettings, setShowSettings] = useState(false);
  const [leftWidth, setLeftWidth] = useState(258);
  const [rightWidth, setRightWidth] = useState(360);
  const [leftHidden, setLeftHidden] = useState(false);
  const [rightHidden, setRightHidden] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const sample = workspace?.path.replaceAll("\\", "/") === "D:/Projects/dualcode";
  const activeTasks = useMemo(() => store.workspaces.flatMap((item) => item.threads.map((task) => ({ workspace: item.name, task }))).filter(({ task }) => activeStates.has(task.state)), [store.workspaces]);
  const filteredWorkspaces = useMemo(() => store.workspaces.map((item) => ({ ...item, threads: item.threads.filter((task) => !query || `${item.name} ${task.title}`.toLowerCase().includes(query.toLowerCase())) })).filter((item) => item.threads.length || item.name.toLowerCase().includes(query.toLowerCase())), [store.workspaces, query]);

  const openWorkspace = async () => {
    const selected = isTauri()
      ? await open({ directory: true, multiple: false, title: "选择本地 Git 仓库" })
      : window.prompt("输入本地 Git 仓库的绝对路径");
    if (typeof selected === "string" && selected.trim()) await store.openWorkspace(selected.trim());
  };
  const run = () => {
    if (sample) { void openWorkspace(); return; }
    if (!text.trim()) return;
    void store.sendPrompt(text.trim());
    setText("");
  };
  const resize = (side: "left" | "right", start: React.PointerEvent<HTMLDivElement>) => {
    start.currentTarget.setPointerCapture(start.pointerId);
    const originX = start.clientX;
    const originWidth = side === "left" ? leftWidth : rightWidth;
    const move = (event: PointerEvent) => {
      const delta = event.clientX - originX;
      if (side === "left") setLeftWidth(Math.min(420, Math.max(190, originWidth + delta)));
      else setRightWidth(Math.min(560, Math.max(280, originWidth - delta)));
    };
    const stop = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };
  const dragWindow = (event: React.MouseEvent<HTMLElement>) => {
    if (!isTauri() || (event.target as HTMLElement).closest("button, input, select")) return;
    if (event.detail === 2) void getCurrentWindow().toggleMaximize();
    else void getCurrentWindow().startDragging();
  };

  return <div className="workbench-shell">
    <header className="titlebar" data-tauri-drag-region onMouseDown={dragWindow}>
      <div className="brand-mark"><Code2 size={15}/></div>
      <div><div className="brand-title">DualCode Workbench</div><div className="brand-subtitle">LOCAL AI DEVELOPMENT</div></div>
      <div className="title-context"><span>{workspace?.name ?? "未打开项目"}</span>{thread && <><span className="crumb">/</span><span>{thread.title}</span></>}</div>
      <div className="layout-controls"><button className="icon-button" onClick={() => setLeftHidden((value) => !value)} title={leftHidden ? "显示项目栏" : "隐藏项目栏"}>{leftHidden ? <PanelLeftOpen size={16}/> : <PanelLeftClose size={16}/>}</button><button className="icon-button" onClick={() => setRightHidden((value) => !value)} title={rightHidden ? "显示检查器" : "隐藏检查器"}>{rightHidden ? <PanelRightOpen size={16}/> : <PanelRightClose size={16}/>}</button><button className="icon-button" onClick={() => setShowSettings(true)} title="Agent 与连接设置"><Settings2 size={16}/></button></div>
      <BackendBadge status={store.backend}/>
      {isTauri() && <div className="window-controls"><button onClick={() => void getCurrentWindow().minimize()} title="最小化"><Minus size={15}/></button><button onClick={() => void getCurrentWindow().toggleMaximize()} title="最大化"><Maximize2 size={13}/></button><button className="close" onClick={() => void getCurrentWindow().close()} title="关闭"><X size={15}/></button></div>}
    </header>

    <main className="workspace-grid" style={{ gridTemplateColumns: `${leftHidden ? 0 : leftWidth}px ${leftHidden ? 0 : 4}px minmax(420px,1fr) ${rightHidden ? 0 : 4}px ${rightHidden ? 0 : rightWidth}px` }}>
      <aside className={`project-rail ${leftHidden ? "panel-hidden" : ""}`}>
        <button className="open-project" onClick={() => void openWorkspace()}><FolderGit2 size={15}/><span>打开本地项目</span><kbd>Ctrl+O</kbd></button>
        <label className="search-box"><Search size={13}/><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索项目和任务"/>{query && <button onClick={() => setQuery("")}><X size={12}/></button>}</label>
        <div className="project-list">
          {filteredWorkspaces.map((item) => <section key={item.id} className="project-group">
            <div className="project-heading"><ChevronDown size={13}/><div className="min-w-0"><strong>{item.name}</strong><small title={item.path}>{item.path}</small></div><button><MoreHorizontal size={14}/></button></div>
            <div className="thread-list">{item.threads.map((task) => <ThreadButton key={task.id} task={task} active={task.id === store.threadId} onClick={() => store.setSelection(item.id, task.id)}/>)}</div>
            {item.id === store.workspaceId && <button className="new-thread" disabled={store.creatingThread} onClick={() => void store.newThread()}><MessageSquarePlus size={13}/>{store.creatingThread ? "创建中…" : "新建任务"}</button>}
          </section>)}
        </div>
        <div className="rail-footer"><GitBranch size={13}/><div><span>本地优先</span><small>不会同步完整项目到 VPS</small></div></div>
      </aside>
      <div className={`resize-handle left ${leftHidden ? "panel-hidden" : ""}`} onPointerDown={(event) => resize("left", event)} onDoubleClick={() => setLeftWidth(258)}/>

      <section className="conversation-pane">
        {!thread ? <EmptyState onOpen={() => void openWorkspace()}/> : <>
          <TaskHeader thread={thread}/>
          {sample && <div className="sample-banner"><AlertTriangle size={16}/><div><strong>这是只读示例</strong><span>示例路径不存在，请打开真实 Git 仓库后开始任务。</span></div><button onClick={() => void openWorkspace()}>选择项目</button></div>}
          <div className="message-stream">
            {thread.messages.length ? thread.messages.map((message) => <MessageCard key={message.id} message={message}/>) : <div className="new-task-empty"><Bot size={28}/><strong>从一个清晰目标开始</strong><span>描述要修改的内容、验收标准和限制。Claude 会先规划，Codex 在隔离 worktree 中执行。</span></div>}
          </div>
          <Composer text={text} setText={setText} mode={store.mode} setMode={store.setMode} run={run} cancel={store.cancelRun} disabled={activeStates.has(thread.state)} sample={sample} fileInput={fileInput} upload={store.upload} attachments={store.draftAttachments} removeAttachment={store.removeAttachment}/>
        </>}
      </section>
      <div className={`resize-handle right ${rightHidden ? "panel-hidden" : ""}`} onPointerDown={(event) => resize("right", event)} onDoubleClick={() => setRightWidth(360)}/>

      <aside className={`inspector-pane ${rightHidden ? "panel-hidden" : ""}`}>
        <div className="inspector-tabs"><button className={rightTab === "context" ? "active" : ""} onClick={() => setRightTab("context")}>上下文</button><button className={rightTab === "diff" ? "active" : ""} onClick={() => setRightTab("diff")}>Diff</button><button className={rightTab === "terminal" ? "active" : ""} onClick={() => setRightTab("terminal")}>终端</button></div>
        {store.pendingApproval && <ApprovalCard action={store.pendingApproval.action} reason={store.pendingApproval.reason} decide={store.decideApproval}/>}
        {rightTab === "context" && <ContextPanel details={store.details} git={store.gitStatus} remote={store.remoteStatus} action={store.gitAction} saveRemote={store.saveRemote} remoteAction={store.remoteGitAction} runTests={store.runTests}/>}
        {rightTab === "diff" && <DiffPanel diff={store.details?.diff ?? ""}/>}
        {rightTab === "terminal" && <TerminalPanel lines={store.terminal}/>}
      </aside>
    </main>

    <footer className="activity-bar"><div className="activity-label"><SquareTerminal size={13}/><strong>后台任务</strong><span>{activeTasks.length ? `${activeTasks.length} 个运行中` : "无运行任务"}</span></div><div className="activity-tasks">{activeTasks.slice(0, 3).map(({ workspace: name, task }) => <button key={task.id}><LoaderCircle size={11} className="spin"/>{name} · {task.title}<small>{stateLabel[task.state]}</small></button>)}</div><div className="security-note"><ShieldCheck size={12}/>审批与变更已审计</div></footer>
    {store.error && <button className="error-toast" onClick={() => useStore.setState({ error: undefined })}><AlertTriangle size={14}/>{store.error}<X size={13}/></button>}
    {showSettings && <SettingsDialog onClose={() => setShowSettings(false)}/>}
  </div>;
}

function BackendBadge({ status }: { status: "connecting" | "online" | "offline" }) { return <div className={`backend-badge ${status}`}><span/>{status === "online" ? "Backend Online" : status === "connecting" ? "Backend Starting" : "Offline Demo"}</div>; }
function ThreadButton({ task, active, onClick }: { task: Thread; active: boolean; onClick: () => void }) { return <button className={`thread-button ${active ? "active" : ""}`} onClick={onClick}><span className={`state-dot ${task.state.toLowerCase()}`}/><div><strong>{task.title}</strong><small>{stateLabel[task.state]}</small></div>{activeStates.has(task.state) && <LoaderCircle size={12} className="spin"/>}</button>; }
function TaskHeader({ thread }: { thread: Thread }) {
  const running = activeStates.has(thread.state);
  return <div className="task-header compact"><div className="task-title-row"><div><small>开发会话</small><h1>{thread.title}</h1></div><span className={`task-state ${thread.state.toLowerCase()}`}>{running && <LoaderCircle size={12} className="spin"/>}{stateLabel[thread.state]}</span></div></div>;
}
function MessageCard({ message }: { message: Message }) {
  const { agent, time, text } = message;
  const names: Record<Agent, string> = { user: "你", codex: "Codex", claude: "Claude", system: "System" };
  if (message.activity) return <details className="agent-activity"><summary>{message.activity.completed ? "已处理" : "正在处理"} · {message.activity.agent} 执行了 {message.activity.count} 项操作</summary><p>具体命令与输出可在右侧“终端”中查看。</p></details>;
  if (agent === "system") return <article className="system-event"><span><SquareTerminal size={12}/></span><div><strong>System</strong><p>{text}</p></div>{time && <time>{time}</time>}</article>;
  return <article className={`message-card ${agent}`}>
    {agent !== "user" && <div className="message-avatar">{agent === "codex" ? <Code2 size={14}/> : "C"}</div>}
    <div className="message-body">{agent !== "user" && <header><strong>{names[agent]}</strong>{time && <time>{time}</time>}<span>{agent === "codex" ? "本地执行" : "远程规划 / 审查"}</span></header>}<div className="message-content">{text}</div></div>
  </article>;
}
function Composer({ text, setText, mode, setMode, run, cancel, disabled, sample, fileInput, upload, attachments, removeAttachment }: { text: string; setText: (value: string) => void; mode: Mode; setMode: (mode: Mode) => void; run: () => void; cancel: () => Promise<void>; disabled: boolean; sample: boolean; fileInput: React.RefObject<HTMLInputElement | null>; upload: (file: File) => Promise<void>; attachments: { id: string; name: string; media_type: string }[]; removeAttachment: (id: string) => void }) { return <div className="composer-wrap"><div className="composer" onDrop={(event) => { event.preventDefault(); for (const file of Array.from(event.dataTransfer.files).slice(0, 8 - attachments.length)) void upload(file); }} onDragOver={(event) => event.preventDefault()}>{attachments.length > 0 && <div className="attachment-tray">{attachments.map((item) => <div className="attachment-chip" key={item.id}><ImagePlus size={13}/><span>{item.name}</span><button onClick={() => removeAttachment(item.id)}><X size={12}/></button></div>)}</div>}<textarea value={text} disabled={disabled && !sample} onChange={(event) => setText(event.target.value)} onPaste={(event) => { const files = Array.from(event.clipboardData.files); for (const file of files.slice(0, 8 - attachments.length)) void upload(file); }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); run(); } }} placeholder={disabled ? "Agent 正在处理本轮请求…" : "输入消息；可以拖入文件或粘贴截图…"}/><div className="composer-tools"><input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp,text/plain" multiple hidden onChange={(event) => { for (const file of Array.from(event.target.files ?? []).slice(0, 8 - attachments.length)) void upload(file); event.target.value = ""; }}/><button title="添加图片" onClick={() => fileInput.current?.click()}><ImagePlus size={15}/></button><button title="添加附件" onClick={() => fileInput.current?.click()}><Paperclip size={15}/></button><span className="composer-hint">Enter 发送 · Shift+Enter 换行</span><select disabled={disabled && !sample} value={mode} onChange={(event) => setMode(event.target.value as Mode)}>{Object.entries(modeLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><button className={`run-button ${disabled && !sample ? "stop" : ""}`} onClick={() => disabled && !sample ? void cancel() : run()}>{disabled && !sample ? <Square size={13}/> : <Play size={13}/>} {disabled && !sample ? "停止" : "发送"}</button></div></div></div>; }
function ApprovalCard({ action, reason, decide }: { action: string; reason: string; decide: (approved: boolean) => Promise<void> }) { const labels: Record<string, string> = { network_access: "发送给 Claude", edit_files: "允许 Codex 本轮操作", remote_edit_files: "允许 Claude 远端操作", run_test: "运行本地测试", git_commit: "Git 提交", git_push: "推送远程", git_pull: "拉取远程代码", remote_git_fetch: "VPS 获取远程状态", remote_git_pull: "VPS 拉取远程代码" }; return <section className="approval-card"><header><ShieldCheck size={16}/><div><small>本轮操作需要授权</small><strong>{labels[action] ?? action}</strong></div></header><p>{reason}</p><div><button onClick={() => void decide(false)}>取消本轮</button><button className="approve" onClick={() => void decide(true)}>允许一次</button></div></section>; }
function ContextPanel({ details, git, remote, action, saveRemote, remoteAction, runTests }: { details?: ReturnType<typeof useStore.getState>["details"]; git?: GitStatus; remote?: WorkspaceRemoteStatus; action: (kind: "commit" | "push" | "pull", message?: string) => Promise<void>; saveRemote: (url: string, path: string) => Promise<void>; remoteAction: (action: "fetch" | "pull") => Promise<void>; runTests: () => Promise<void> }) { const test = details?.tests.at(-1); const [commitMessage, setCommitMessage] = useState(""); return <div className="inspector-scroll">
  <InspectorSection title="仓库状态" icon={<GitBranch size={13}/>}><dl className="git-meta"><dt>分支</dt><dd>{git?.branch || "未检测"}</dd><dt>HEAD</dt><dd>{git?.head || "—"}</dd><dt>上游</dt><dd title={git?.upstream}>{git?.upstream || "未设置"}</dd><dt>同步</dt><dd>{git?.upstream ? `领先 ${git.ahead} · 落后 ${git.behind}` : "无远程跟踪"}</dd></dl></InspectorSection>
  <RemoteRepository remote={remote} save={saveRemote} action={remoteAction}/>
  <InspectorSection title="Git 操作" icon={<GitBranch size={13}/>}><div className="git-actions"><input value={commitMessage} onChange={(event) => setCommitMessage(event.target.value)} placeholder="提交说明…"/><button disabled={!git?.changes.length || !commitMessage.trim()} onClick={() => void action("commit", commitMessage.trim()).then(() => setCommitMessage(""))}>提交</button><button disabled={!git?.remote || !git?.behind} onClick={() => void action("pull")}>拉取</button><button disabled={!git?.remote} onClick={() => void action("push")}>推送</button></div><small className="git-action-note">所有操作都会先请求审批；拉取仅允许 fast-forward。</small></InspectorSection>
  <InspectorSection title="未提交变更" icon={<FileCode2 size={13}/>} badge={git?.changes.length ? String(git.changes.length) : undefined}>{git?.changes.length ? git.changes.map((item) => <div className="file-row" key={item}><FileCode2 size={12}/><span>{item.slice(3)}</span><b>{item.slice(0,2).trim() || "M"}</b></div>) : <EmptyInline text="工作区干净"/>}</InspectorSection>
  <InspectorSection title="最近提交" icon={<Clock3 size={13}/>}>{git?.commits.length ? <div className="commit-list">{git.commits.map((item) => <div className="commit-row" key={item.sha}><code>{item.sha}</code><div><strong>{item.subject}</strong><small>{item.author}</small></div></div>)}</div> : <EmptyInline text="暂无提交记录"/>}</InspectorSection>
  <InspectorSection title="测试" icon={<TestTube2 size={13}/>}><div className="test-actions"><button onClick={() => void runTests()}><Play size={11}/>运行测试</button></div><div className={`test-result ${test ? test.exit_code === 0 ? "pass" : "fail" : ""}`}>{test ? <><div>{test.exit_code === 0 ? <Check size={13}/> : <X size={13}/>}<strong>{test.exit_code === 0 ? "测试通过" : "测试失败"}</strong></div><pre>{test.output.trim()}</pre></> : <EmptyInline text="尚未运行测试"/>}</div></InspectorSection>
</div>; }
function RemoteRepository({ remote, save, action }: { remote?: WorkspaceRemoteStatus; save: (url: string, path: string) => Promise<void>; action: (kind: "fetch" | "pull") => Promise<void> }) { const [url, setUrl] = useState(""); const [path, setPath] = useState(""); useEffect(() => { setUrl(remote?.settings.remote_url ?? remote?.local.remote ?? ""); setPath(remote?.settings.vps_repo_path ?? ""); }, [remote?.settings.remote_url, remote?.settings.vps_repo_path, remote?.local.remote]); return <InspectorSection title="VPS 仓库" icon={<Cloud size={13}/>}><div className="remote-repo-form"><label>远程 URL<input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="git@github.com:owner/repo.git"/></label><label>VPS 路径<input value={path} onChange={(event) => setPath(event.target.value)} placeholder="/home/user/repos/project"/></label><button disabled={!path.trim()} onClick={() => void save(url.trim(), path.trim())}>保存并检测</button></div>{remote?.vps ? <><dl className="git-meta remote-meta"><dt>VPS 分支</dt><dd>{remote.vps.branch}</dd><dt>VPS HEAD</dt><dd>{remote.vps.head.slice(0,10)}</dd><dt>同一仓库</dt><dd className={remote.same_remote ? "sync-ok" : "sync-bad"}>{remote.same_remote ? "是" : "否"}</dd><dt>同一提交</dt><dd className={remote.same_commit ? "sync-ok" : "sync-warn"}>{remote.same_commit ? "是" : "否"}</dd></dl><div className="remote-actions"><button onClick={() => void action("fetch")}>VPS 获取</button><button onClick={() => void action("pull")}>VPS 拉取</button></div></> : <div className="empty-inline">{remote?.error || "尚未检测 VPS 仓库"}</div>}</InspectorSection>; }
function DiffPanel({ diff }: { diff: string }) { return <div className="diff-panel">{diff ? <Editor height="100%" language="diff" theme="vs-dark" options={{ readOnly: true, minimap: { enabled: false }, fontSize: 12, scrollBeyondLastLine: false, wordWrap: "on", padding: { top: 12 } }} value={diff}/> : <div className="panel-empty"><Code2 size={22}/><strong>暂无 Diff</strong><span>Codex 修改文件后，这里会显示真实 Git Diff。</span></div>}</div>; }
function TerminalPanel({ lines }: { lines: string[] }) { return <div className="terminal-panel">{lines.length ? <pre>{lines.join("\n")}</pre> : <div className="panel-empty"><SquareTerminal size={22}/><strong>终端等待中</strong><span>测试和本地进程输出会实时显示在这里。</span></div>}</div>; }
function InspectorSection({ title, icon, badge, children }: { title: string; icon: React.ReactNode; badge?: string; children: React.ReactNode }) { return <section className="inspector-section"><header>{icon}<strong>{title}</strong>{badge && <span>{badge}</span>}</header>{children}</section>; }
function EmptyInline({ text }: { text: string }) { return <div className="empty-inline"><Circle size={8}/>{text}</div>; }
function EmptyState({ onOpen }: { onOpen: () => void }) { return <div className="full-empty"><div><FolderGit2 size={30}/></div><h2>打开本地代码项目</h2><p>DualCode 会在隔离 worktree 中让 Codex 执行，并由远程 Claude 规划和审查。</p><button onClick={onOpen}><FolderGit2 size={14}/>选择 Git 仓库</button><small>项目内容默认不会同步到 VPS</small></div>; }
