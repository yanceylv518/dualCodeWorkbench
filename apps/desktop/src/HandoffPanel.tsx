import { useCallback, useEffect, useState } from "react";
import { Check, Code2, LoaderCircle, RefreshCw, Send } from "lucide-react";
import * as api from "./api";
import type { HandoffPackage } from "./types";
import "./handoff.css";

const handoffStatusLabel = { PREPARED: "待确认", SENT: "已发送" } as const;
const contractStatusLabel: Record<string, string> = {
  DRAFT: "草稿",
  CLARIFYING: "澄清中",
  READY: "可实施",
  IMPLEMENTING: "实施中",
  REVIEWING: "审查中",
  CONDITIONAL_PASS: "有条件通过",
  PASSED: "已通过",
  BLOCKED: "已阻塞",
};

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}

export function summarizeDiff(diff: string, fallbackFiles: string[]) {
  const headers = diff.match(/^diff --git /gm)?.length ?? 0;
  const lines = diff.split("\n");
  return {
    files: headers || fallbackFiles.length,
    additions: lines.filter(
      (line) => line.startsWith("+") && !line.startsWith("+++"),
    ).length,
    deletions: lines.filter(
      (line) => line.startsWith("-") && !line.startsWith("---"),
    ).length,
  };
}

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
  const contract = selected?.payload.contract;
  const diffSummary = selected
    ? summarizeDiff(
        selected.payload.diff,
        selected.payload.repository.changed_files,
      )
    : undefined;
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
              <span
                className={`handoff-status ${selected.status.toLowerCase()}`}
              >
                {handoffStatusLabel[selected.status]}
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
          <section className="handoff-section">
            <header>
              <strong>契约摘要</strong>
              <span className="handoff-status contract">
                {contractStatusLabel[String(contract?.status)] || "未定义"}
              </span>
            </header>
            <dl>
              <dt>产品目标</dt>
              <dd>{String(contract?.product_goal || "尚未定义")}</dd>
              <dt>任务目标</dt>
              <dd>{String(contract?.task_goal || "尚未定义")}</dd>
              <dt>验收标准</dt>
              <dd>
                {stringList(contract?.acceptance).join("；") || "尚未定义"}
              </dd>
              <dt>已知风险</dt>
              <dd>{stringList(contract?.known_risks).join("；") || "无"}</dd>
            </dl>
          </section>
          <section className="handoff-section">
            <header>
              <strong>仓库基线</strong>
            </header>
            <dl>
              <dt>分支 / HEAD</dt>
              <dd>
                {selected.payload.repository.branch || "未检测"} ·{" "}
                {selected.payload.repository.head || "尚无提交"}
              </dd>
              <dt>上游</dt>
              <dd>{selected.payload.repository.upstream || "未关联"}</dd>
            </dl>
          </section>
          <section className="handoff-section">
            <header>
              <strong>修改内容</strong>
              <span>
                {diffSummary?.files ?? 0} 个文件、+{diffSummary?.additions ?? 0}
                /-{diffSummary?.deletions ?? 0} 行
              </span>
            </header>
            {selected.payload.repository.changed_files.length ? (
              <ul className="handoff-files">
                {selected.payload.repository.changed_files.map((file) => (
                  <li key={file}>{file}</li>
                ))}
              </ul>
            ) : (
              <p className="handoff-empty">没有记录到修改文件</p>
            )}
            {selected.payload.diff && (
              <details>
                <summary>展开 Diff 预览</summary>
                <pre>{selected.payload.diff}</pre>
              </details>
            )}
          </section>
          <section className="handoff-section">
            <header>
              <strong>测试证据</strong>
              <span>{selected.payload.tests.length} 条</span>
            </header>
            {selected.payload.tests.length ? (
              selected.payload.tests.map((test, index) => (
                <details
                  className="handoff-test"
                  key={`${test.command}-${index}`}
                >
                  <summary>
                    <span
                      className={`test-result ${test.exit_code === 0 ? "passed" : "failed"}`}
                    >
                      {test.exit_code === 0 ? "通过" : "失败"}
                    </span>
                    {test.command}
                  </summary>
                  <pre>{test.output || "无输出"}</pre>
                </details>
              ))
            ) : (
              <p className="handoff-empty">尚无测试证据</p>
            )}
          </section>
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
              <small>{handoffStatusLabel[item.status]}</small>
            </button>
          ))}
        </section>
      )}
      {error && <div className="settings-error">{error}</div>}
    </div>
  );
}
