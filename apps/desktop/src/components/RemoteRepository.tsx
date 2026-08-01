import { useEffect, useState } from "react";
import {
  Check,
  ChevronDown,
  Cloud,
  Copy,
  KeyRound,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";

import * as api from "../api";
import { useStore } from "../store";
import type {
  ExecutionJob,
  RepositoryAccessStatus,
  WorkspaceRemoteStatus,
} from "../types";

type RemoteAction =
  "provision" | "repair_provision" | "fetch" | "pull" | "generate_access_key";
type BusyAction = "save" | "refresh" | RemoteAction;

function cloneJobFrom(jobs: ExecutionJob[]) {
  return jobs.find(
    (job) =>
      job.kind === "remote_git" &&
      (job.payload.action === "provision" ||
        job.payload.action === "repair_provision"),
  );
}

export function deriveRemoteFeedback(
  remote: WorkspaceRemoteStatus | undefined,
  cloneJob: ExecutionJob | undefined,
  busy?: BusyAction,
) {
  if (busy === "save")
    return { message: "正在保存并检测 VPS 仓库…", tone: "running" };
  if (busy === "refresh")
    return { message: "正在刷新 VPS 仓库状态…", tone: "running" };
  if (busy === "provision" || busy === "repair_provision")
    return { message: "正在提交克隆任务…", tone: "running" };
  // A verified repository is the current source of truth. Historical failed
  // clone jobs remain auditable, but must not override a later successful
  // status refresh or an empty repository that is ready for its first commit.
  if (remote?.vps)
    return {
      message: remote.vps.head
        ? "VPS 仓库已就绪，状态已刷新。"
        : "VPS 空仓库已就绪，等待首次提交。",
      tone: "ready",
    };
  if (cloneJob?.status === "WAITING_APPROVAL")
    return {
      message: "等待确认清理无效残留目录；批准后将自动重新克隆。",
      tone: "running",
    };
  if (cloneJob?.status === "READY" || cloneJob?.status === "RUNNING")
    return { message: "正在克隆到 VPS，请稍候…", tone: "running" };
  if (cloneJob?.status === "FAILED" || cloneJob?.status === "INTERRUPTED")
    return {
      message: `克隆失败：${cloneJob.last_error || "请查看运行日志后重试"}`,
      tone: "failed",
    };
  if (remote?.error)
    return { message: `当前检测失败：${remote.error}`, tone: "failed" };
  return undefined;
}

export function RemoteRepository({
  remote,
  jobs,
  save,
  action,
}: {
  remote?: WorkspaceRemoteStatus;
  jobs: ExecutionJob[];
  save: (url: string, path: string) => Promise<void>;
  action: (kind: RemoteAction) => Promise<void>;
}) {
  const refreshRemote = useStore((state) => state.refreshRemote);
  const workspaceId = useStore((state) => state.workspaceId);
  const notify = useStore((state) => state.notify);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<BusyAction>();
  const [editing, setEditing] = useState(false);
  const [accessOpen, setAccessOpen] = useState(false);
  const [access, setAccess] = useState<RepositoryAccessStatus>();
  const [accessLoading, setAccessLoading] = useState(false);
  const cloneJob = cloneJobFrom(jobs);
  const keyJob = jobs.find(
    (job) =>
      job.kind === "remote_git" && job.payload.action === "generate_access_key",
  );
  const cloneRunning = ["WAITING_APPROVAL", "READY", "RUNNING"].includes(
    cloneJob?.status ?? "",
  );
  const cloneFailed = ["FAILED", "INTERRUPTED"].includes(
    cloneJob?.status ?? "",
  );
  const needsRepair =
    cloneFailed &&
    !remote?.vps &&
    cloneJob?.last_error?.includes(
      "already exists and is not an empty directory",
    );
  const configured = Boolean(
    remote?.settings.remote_url && remote?.settings.vps_repo_path,
  );
  const feedback = deriveRemoteFeedback(remote, cloneJob, busy);

  useEffect(() => {
    setUrl(remote?.settings.remote_url ?? remote?.local.remote ?? "");
  }, [remote?.settings.remote_url, remote?.local.remote]);

  const run = async (kind: RemoteAction) => {
    setBusy(kind);
    try {
      await action(kind);
    } catch (error) {
      notify("error", `VPS 仓库操作失败：${String(error)}`);
    } finally {
      setBusy(undefined);
    }
  };
  const refresh = async () => {
    setBusy("refresh");
    try {
      await refreshRemote();
    } catch (error) {
      notify("error", `刷新 VPS 仓库失败：${String(error)}`);
    } finally {
      setBusy(undefined);
    }
  };
  const persist = async () => {
    setBusy("save");
    try {
      await save(url.trim(), "");
      setEditing(false);
    } catch (error) {
      notify("error", `保存 VPS 仓库配置失败：${String(error)}`);
    } finally {
      setBusy(undefined);
    }
  };
  const checkAccess = async () => {
    if (!workspaceId) return;
    setAccessLoading(true);
    try {
      setAccess(await api.fetchRepositoryAccess(workspaceId));
    } catch (error) {
      notify("error", `仓库访问检测失败：${String(error)}`);
    } finally {
      setAccessLoading(false);
    }
  };
  const toggleAccess = () => {
    const next = !accessOpen;
    setAccessOpen(next);
    if (next && !access) void checkAccess();
  };
  useEffect(() => {
    if (accessOpen && keyJob?.status === "SUCCEEDED") void checkAccess();
    // Refresh once when the background key-generation job reaches success.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessOpen, keyJob?.id, keyJob?.status]);

  return (
    <section className="inspector-section">
      <header>
        <Cloud size={13} />
        <strong>VPS 仓库</strong>
      </header>
      {configured && !editing ? (
        <div className="remote-config-summary">
          <div className="remote-config-heading">
            <div>
              <Check size={13} />
              <strong>仓库配置已保存</strong>
            </div>
            <button onClick={() => setEditing(true)}>修改配置</button>
          </div>
          <dl>
            <dt>远程仓库</dt>
            <dd title={remote?.settings.remote_url}>
              {remote?.settings.remote_url}
            </dd>
            <dt>VPS 目录</dt>
            <dd title={remote?.settings.vps_repo_path}>
              {remote?.settings.vps_repo_path}
            </dd>
          </dl>
        </div>
      ) : (
        <div className="remote-repo-form">
          <label>
            远程 URL
            <input
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="git@github.com:owner/repo.git"
            />
          </label>
          {remote?.settings.vps_repo_path && (
            <div className="derived-vps-path">
              <span>自动项目目录</span>
              <code>{remote.settings.vps_repo_path}</code>
            </div>
          )}
          <button
            disabled={!url.trim() || Boolean(busy) || cloneRunning}
            onClick={() => void persist()}
          >
            {busy === "save" ? "保存并检测中…" : "保存并检测"}
          </button>
        </div>
      )}

      {feedback && (
        <div className={`remote-feedback ${feedback.tone}`} role="status">
          {feedback.tone === "running" && (
            <LoaderCircle size={12} className="spin" />
          )}
          {feedback.message}
        </div>
      )}

      {configured && (
        <div className="repository-access">
          <button className="repository-access-toggle" onClick={toggleAccess}>
            <span>
              <KeyRound size={13} />
              <strong>仓库访问配置</strong>
            </span>
            <ChevronDown size={13} className={accessOpen ? "expanded" : ""} />
          </button>
          {accessOpen && (
            <div className="repository-access-body">
              <p>分别验证本机与 VPS 对当前仓库的读取权限，不会写入仓库。</p>
              {accessLoading && !access ? (
                <div className="inspector-loading">
                  <LoaderCircle size={13} className="spin" />
                  正在检测仓库访问权限…
                </div>
              ) : access ? (
                <>
                  {[access.local, access.vps].map((item) => (
                    <div
                      className={`access-environment ${item.state}`}
                      key={item.environment}
                    >
                      <div>
                        <strong>
                          {item.environment === "local" ? "本机" : "VPS"}
                        </strong>
                        <span>{item.transport.toUpperCase()}</span>
                      </div>
                      <span>
                        {item.read_access ? "读取已验证" : item.summary}
                      </span>
                    </div>
                  ))}
                  {access.key.public_key ? (
                    <div className="repository-key">
                      <strong>VPS 项目专用公钥</strong>
                      <small>{access.key.fingerprint}</small>
                      <textarea
                        readOnly
                        value={access.key.public_key}
                        aria-label="VPS Git public key"
                      />
                      <button
                        onClick={() =>
                          void navigator.clipboard.writeText(
                            access.key.public_key ?? "",
                          )
                        }
                      >
                        <Copy size={12} />
                        复制公钥
                      </button>
                      <span>
                        将公钥添加到 GitHub 仓库 Deploy
                        keys（只需读取权限），然后重新检测。
                      </span>
                    </div>
                  ) : access.vps.transport === "ssh" &&
                    (access.vps.error_code === "SSH_KEY_NOT_AUTHORIZED" ||
                      access.vps.state === "action_required") ? (
                    <button
                      className="generate-access-key"
                      onClick={() => void run("generate_access_key")}
                      disabled={Boolean(busy)}
                    >
                      <KeyRound size={12} />
                      生成 VPS 项目专用密钥
                    </button>
                  ) : null}
                  <button
                    className="access-recheck"
                    onClick={() => void checkAccess()}
                    disabled={accessLoading}
                  >
                    <RefreshCw
                      size={12}
                      className={accessLoading ? "spin" : ""}
                    />
                    重新检测
                  </button>
                </>
              ) : null}
            </div>
          )}
        </div>
      )}

      {remote?.vps ? (
        <>
          <dl className="git-meta remote-meta">
            <dt>VPS 分支</dt>
            <dd>{remote.vps.branch || "等待首次提交"}</dd>
            <dt>VPS HEAD</dt>
            <dd>
              {remote.vps.head ? remote.vps.head.slice(0, 10) : "尚无提交"}
            </dd>
            <dt>同一仓库</dt>
            <dd className={remote.same_remote ? "sync-ok" : "sync-bad"}>
              {remote.same_remote ? "是" : "否"}
            </dd>
            <dt>同一提交</dt>
            <dd className={remote.same_commit ? "sync-ok" : "sync-warn"}>
              {remote.same_commit ? "是" : "等待首次提交"}
            </dd>
          </dl>
          <div className="remote-actions">
            <button disabled={Boolean(busy)} onClick={() => void refresh()}>
              {busy === "refresh" ? "刷新中…" : "刷新状态"}
            </button>
            <button
              disabled={Boolean(busy) || !remote.vps.head}
              onClick={() => void run("pull")}
            >
              拉取更新
            </button>
          </div>
          <small className="git-action-note">
            刷新状态为只读操作，无需审批；拉取更新会修改 VPS 工作区，需要审批。
          </small>
        </>
      ) : (
        <>
          <div className="empty-inline">
            {remote?.state === "ambiguous"
              ? "发现多个匹配仓库，请修改配置"
              : "尚未在 VPS 项目根目录中识别到对应 Git 仓库"}
          </div>
          {configured && (
            <div className="remote-actions">
              <button disabled={Boolean(busy)} onClick={() => void refresh()}>
                {busy === "refresh" ? "刷新中…" : "刷新状态"}
              </button>
              <button
                disabled={Boolean(busy) || cloneRunning}
                onClick={() =>
                  void run(needsRepair ? "repair_provision" : "provision")
                }
              >
                {cloneRunning ||
                busy === "provision" ||
                busy === "repair_provision"
                  ? "正在克隆…"
                  : needsRepair
                    ? "清理残留并重新克隆"
                    : cloneFailed
                      ? "重新克隆"
                      : "克隆到 VPS"}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
