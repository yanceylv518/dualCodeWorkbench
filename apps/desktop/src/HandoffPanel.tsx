import { useCallback, useEffect, useState } from "react";
import { Check, Code2, LoaderCircle, RefreshCw, Send } from "lucide-react";
import * as api from "./api";
import type { HandoffPackage } from "./types";
import "./handoff.css";

export function HandoffPanel({
  workspaceId,
  threadId,
}: {
  workspaceId: string;
  threadId: string;
}) {
  const [items, setItems] = useState<HandoffPackage[]>([]);
  const [selected, setSelected] = useState<HandoffPackage>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const values = await api.listHandoffs(workspaceId, threadId);
      setItems(values);
      if (values[0]) setSelected((current) => current ?? values[0]);
    } catch (reason) {
      setError(String(reason));
    }
  }, [threadId, workspaceId]);
  useEffect(() => {
    if (workspaceId && threadId) void load();
  }, [workspaceId, threadId, load]);
  const prepare = async (
    recipient: "codex" | "claude",
    purpose: "verify" | "review",
  ) => {
    setBusy(true);
    setError("");
    try {
      const value = await api.prepareHandoff(
        workspaceId,
        threadId,
        recipient,
        purpose,
      );
      setSelected(value);
      setItems((current) => [value, ...current]);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };
  const send = async () => {
    if (!selected || selected.status !== "PREPARED") return;
    setBusy(true);
    setError("");
    try {
      await api.sendHandoff(workspaceId, threadId, selected.id);
      setSelected({ ...selected, status: "SENT" });
      setItems((current) =>
        current.map((item) =>
          item.id === selected.id ? { ...item, status: "SENT" } : item,
        ),
      );
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="handoff-panel">
      <header>
        <div>
          <strong>结构化交接</strong>
          <span>只发送契约、仓库证据、Diff、测试和风险。</span>
        </div>
        <button title="刷新" onClick={() => void load()}>
          <RefreshCw size={13} />
        </button>
      </header>
      <div className="handoff-actions">
        <button disabled={busy} onClick={() => void prepare("codex", "verify")}>
          <Code2 size={13} />让 Codex 验证方案
        </button>
        <button
          disabled={busy}
          onClick={() => void prepare("claude", "review")}
        >
          <Send size={13} />
          准备 Claude 审查包
        </button>
      </div>
      {busy && (
        <div className="handoff-busy">
          <LoaderCircle className="spin" size={14} />
          正在生成或发送交接包…
        </div>
      )}
      {selected ? (
        <section className="handoff-preview">
          <header>
            <div>
              <strong>
                {selected.recipient === "claude"
                  ? "交给 Claude 独立审查"
                  : "交给 Codex 仓库验证"}
              </strong>
              <span>
                {selected.status === "SENT" ? "已发送" : "等待用户确认"}
              </span>
            </div>
            {selected.status === "PREPARED" ? (
              <button disabled={busy} onClick={() => void send()}>
                <Send size={12} />
                确认发送
              </button>
            ) : (
              <Check size={16} />
            )}
          </header>
          <dl>
            <dt>目标</dt>
            <dd>{String(selected.payload.contract.task_goal || "尚未定义")}</dd>
            <dt>仓库</dt>
            <dd>
              {selected.payload.repository.branch || "未检测"} ·{" "}
              {selected.payload.repository.head || "尚无提交"}
            </dd>
            <dt>修改文件</dt>
            <dd>
              {selected.payload.repository.changed_files.length
                ? selected.payload.repository.changed_files.join("、")
                : "无"}
            </dd>
            <dt>Diff</dt>
            <dd>
              {selected.payload.diff
                ? `${selected.payload.diff.length} 字符`
                : "无"}
            </dd>
            <dt>测试证据</dt>
            <dd>
              {selected.payload.tests.length
                ? `${selected.payload.tests.length} 条`
                : "无"}
            </dd>
          </dl>
          <details>
            <summary>查看交接内容</summary>
            <pre>{JSON.stringify(selected.payload, null, 2)}</pre>
          </details>
        </section>
      ) : (
        <div className="panel-empty">
          <Send size={22} />
          <strong>尚未准备交接包</strong>
          <span>先完善契约，再选择 Codex 验证或 Claude 审查。</span>
        </div>
      )}
      {items.length > 1 && (
        <section className="handoff-history">
          <strong>历史交接</strong>
          {items.map((item) => (
            <button key={item.id} onClick={() => setSelected(item)}>
              <span>
                {item.recipient === "claude" ? "Claude 审查" : "Codex 验证"}
              </span>
              <small>{item.status}</small>
            </button>
          ))}
        </section>
      )}
      {error && <div className="settings-error">{error}</div>}
    </div>
  );
}
