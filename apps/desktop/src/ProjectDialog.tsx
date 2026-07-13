import { useRef, useState } from "react";
import { FolderGit2, GitFork, LoaderCircle, X } from "lucide-react";

export function ProjectDialog({
  chooseDirectory,
  submit,
  close,
}: {
  chooseDirectory: () => Promise<string | undefined>;
  submit: (value: {
    path: string;
    remote_url: string;
    mode: "init" | "clone";
    name?: string;
  }) => Promise<void>;
  close: () => void;
}) {
  const [mode, setMode] = useState<"init" | "clone">("clone");
  const [path, setPath] = useState("");
  const [remote, setRemote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submitting = useRef(false);
  const run = async () => {
    if (
      submitting.current ||
      !path.trim() ||
      (mode === "clone" && !remote.trim())
    )
      return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      await submit({ path: path.trim(), remote_url: remote.trim(), mode });
      close();
    } catch (reason) {
      setError(String(reason));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };
  return (
    <div className="settings-backdrop">
      <div className="project-dialog">
        <header>
          <div>
            <FolderGit2 size={18} />
          </div>
          <span>
            <strong>创建或克隆项目</strong>
            <small>准备本地 Git 仓库并加入工作台</small>
          </span>
          <button onClick={close}>
            <X size={17} />
          </button>
        </header>
        <main>
          <div className="project-mode">
            <button
              className={mode === "clone" ? "active" : ""}
              onClick={() => setMode("clone")}
            >
              <GitFork size={15} />
              <strong>克隆远程仓库</strong>
              <small>远程已有代码时使用</small>
            </button>
            <button
              className={mode === "init" ? "active" : ""}
              onClick={() => setMode("init")}
            >
              <FolderGit2 size={15} />
              <strong>创建新项目</strong>
              <small>初始化空仓库并可关联远程</small>
            </button>
          </div>
          <label className="settings-field">
            <span>
              {mode === "clone" ? "远程仓库 URL" : "远程仓库 URL（可选）"}
            </span>
            <input
              value={remote}
              onChange={(event) => setRemote(event.target.value)}
              placeholder="git@github.com:owner/repository.git"
            />
          </label>
          <label className="settings-field">
            <span>本地空目录</span>
            <div className="path-picker">
              <input
                value={path}
                onChange={(event) => setPath(event.target.value)}
                placeholder="选择或输入一个空目录"
              />
              <button
                onClick={async () => {
                  const selected = await chooseDirectory();
                  if (selected) setPath(selected);
                }}
              >
                选择…
              </button>
            </div>
          </label>
          <p className="project-operation">
            确认后将执行：
            {mode === "clone"
              ? "git clone；若远程为空则创建中性 README 基线。"
              : `git init -b main${remote.trim() ? "，添加 origin，并创建中性 README 基线。" : "，并创建中性 README 基线。"}`}
            会生成首次本地基线提交，但不会自动推送，也不会预选技术框架。
          </p>
          {error && <div className="settings-error">{error}</div>}
        </main>
        <footer>
          <button onClick={close}>取消</button>
          <button
            className="primary"
            disabled={
              busy || !path.trim() || (mode === "clone" && !remote.trim())
            }
            onClick={() => void run()}
          >
            {busy && <LoaderCircle size={12} className="spin" />}
            {busy ? "处理中…" : "确认并执行"}
          </button>
        </footer>
      </div>
    </div>
  );
}
