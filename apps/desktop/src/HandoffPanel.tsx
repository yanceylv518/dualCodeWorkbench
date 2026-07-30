import { useCallback, useEffect, useState } from "react";
import { Check, Code2, LoaderCircle, RefreshCw, Send } from "lucide-react";
import * as api from "./api";
import type {
  CollaborationFinding,
  CollaborationRun,
  HandoffPackage,
} from "./types";
import "./handoff.css";

const handoffStatusLabel = { PREPARED: "待确认", SENT: "已发送" } as const;

type V2Payload = Extract<HandoffPackage["payload"], { schema: "handoff.v2" }>;
type LegacyPayload = Exclude<
  HandoffPackage["payload"],
  { schema: "handoff.v2" }
>;

function isV2(payload: HandoffPackage["payload"]): payload is V2Payload {
  return payload.schema === "handoff.v2";
}

function shortSha(value: string) {
  return value ? value.slice(0, 10) : "尚无提交";
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

function StringList({
  values,
  empty = "无",
}: {
  values: string[];
  empty?: string;
}) {
  return values.length ? (
    <ul className="handoff-list">
      {values.map((value) => (
        <li key={value}>{value}</li>
      ))}
    </ul>
  ) : (
    <span className="handoff-empty">{empty}</span>
  );
}

function V2Preview({ payload }: { payload: V2Payload }) {
  const stats = payload.repository.diff_stats;
  return (
    <>
      <section className="handoff-section">
        <header>
          <strong>任务契约</strong>
          <span>handoff.v2</span>
        </header>
        <dl>
          <dt>目标</dt>
          <dd>{payload.task.goal}</dd>
          <dt>非目标</dt>
          <dd>
            <StringList values={payload.task.non_goals} />
          </dd>
          <dt>验收标准</dt>
          <dd>
            <StringList values={payload.task.acceptance} />
          </dd>
          <dt>约束</dt>
          <dd>
            <StringList values={payload.task.constraints} />
          </dd>
        </dl>
      </section>
      <section className="handoff-section">
        <header>
          <strong>仓库基线</strong>
        </header>
        <dl>
          <dt>分支</dt>
          <dd>{payload.repository.branch || "未检测"}</dd>
          <dt>基础快照</dt>
          <dd title={payload.repository.base_sha}>
            <code>{shortSha(payload.repository.base_sha)}</code>
          </dd>
          <dt>审查快照</dt>
          <dd title={payload.repository.snapshot_sha}>
            <code>{shortSha(payload.repository.snapshot_sha)}</code>
          </dd>
          <dt>Diff 统计</dt>
          <dd>
            {Object.entries(stats)
              .map(([key, value]) => `${key} ${value}`)
              .join(" · ") || "无"}
          </dd>
        </dl>
        <StringList
          values={payload.repository.changed_files}
          empty="没有记录到修改文件"
        />
      </section>
      <section className="handoff-section">
        <header>
          <strong>测试证据</strong>
          <span>{payload.evidence.length} 条</span>
        </header>
        {payload.evidence.length ? (
          payload.evidence.map((item, index) => (
            <article
              className="handoff-evidence"
              key={`${item.command}-${index}`}
            >
              <span
                className={`test-result ${item.exit_code === 0 ? "passed" : "failed"}`}
              >
                {item.exit_code === 0 ? "通过" : `退出 ${item.exit_code}`}
              </span>
              <code>{item.command}</code>
              <p>{item.summary}</p>
            </article>
          ))
        ) : (
          <p className="handoff-empty">尚无测试证据</p>
        )}
      </section>
      <section className="handoff-section">
        <header>
          <strong>未关闭 Findings</strong>
        </header>
        <StringList
          values={payload.open_findings}
          empty="当前没有未关闭 finding"
        />
      </section>
      <section className="handoff-section">
        <header>
          <strong>风险</strong>
        </header>
        <StringList values={payload.risks} empty="未记录风险" />
      </section>
    </>
  );
}

function LegacyPreview({ payload }: { payload: LegacyPayload }) {
  const contract = payload.contract;
  const summary = summarizeDiff(payload.diff, payload.repository.changed_files);
  return (
    <>
      <section className="handoff-section">
        <header>
          <strong>契约摘要</strong>
        </header>
        <dl>
          <dt>任务目标</dt>
          <dd>{String(contract.task_goal || "尚未定义")}</dd>
          <dt>验收标准</dt>
          <dd>
            {Array.isArray(contract.acceptance)
              ? contract.acceptance.join("；")
              : "尚未定义"}
          </dd>
        </dl>
      </section>
      <section className="handoff-section">
        <header>
          <strong>仓库基线</strong>
        </header>
        <dl>
          <dt>分支 / HEAD</dt>
          <dd>
            {payload.repository.branch || "未检测"} ·{" "}
            {payload.repository.head || "尚无提交"}
          </dd>
          <dt>上游</dt>
          <dd>{payload.repository.upstream || "未关联"}</dd>
        </dl>
      </section>
      <section className="handoff-section">
        <header>
          <strong>修改内容</strong>
          <span>
            {summary.files} 个文件，+{summary.additions}/-{summary.deletions} 行
          </span>
        </header>
        <StringList
          values={payload.repository.changed_files}
          empty="没有记录到修改文件"
        />
        {payload.diff && (
          <details>
            <summary>展开 Diff 预览</summary>
            <pre>{payload.diff}</pre>
          </details>
        )}
      </section>
      <section className="handoff-section">
        <header>
          <strong>测试证据</strong>
          <span>{payload.tests.length} 条</span>
        </header>
        {payload.tests.length ? (
          payload.tests.map((test, index) => (
            <details className="handoff-test" key={`${test.command}-${index}`}>
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
    </>
  );
}

function FindingsView({
  run,
  findings,
}: {
  run?: CollaborationRun;
  findings: CollaborationFinding[];
}) {
  return (
    <section className="handoff-findings" aria-label="协作审查问题">
      <header>
        <strong>协作 Findings</strong>
        {run && (
          <span>
            {run.state} · 第 {run.round} 轮
          </span>
        )}
      </header>
      {!run || !findings.length ? (
        <p className="handoff-empty">当前协作运行没有审查问题</p>
      ) : (
        findings.map((finding) => (
          <article key={finding.id} className={`finding ${finding.status}`}>
            <header>
              <span className={`finding-severity ${finding.severity}`}>
                {finding.severity === "blocking" ? "阻断" : "建议"}
              </span>
              <span className="finding-type">{finding.type}</span>
              <span>{finding.status === "open" ? "未解决" : "已解决"}</span>
            </header>
            {(finding.file || finding.line) && (
              <code>
                {finding.file || "未指定文件"}
                {finding.line ? `:${finding.line}` : ""}
              </code>
            )}
            <p>{finding.description}</p>
            <small>验收：{finding.acceptance}</small>
          </article>
        ))
      )}
    </section>
  );
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
  const [run, setRun] = useState<CollaborationRun>();
  const [findings, setFindings] = useState<CollaborationFinding[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [values, current] = await Promise.all([
        api.listHandoffs(workspaceId, threadId),
        api.fetchCurrentCollaboration(workspaceId, threadId),
      ]);
      setItems(values);
      if (values[0]) setSelected((value) => value ?? values[0]);
      setRun(current);
      setFindings(
        current
          ? await api.fetchCollaborationFindings(
              workspaceId,
              threadId,
              current.id,
            )
          : [],
      );
      setError("");
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
          <span>查看任务契约、仓库快照、测试证据与独立审查问题。</span>
        </div>
        <button title="刷新" aria-label="刷新交接" onClick={() => void load()}>
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
          {isV2(selected.payload) ? (
            <V2Preview payload={selected.payload} />
          ) : (
            <LegacyPreview payload={selected.payload} />
          )}
        </section>
      ) : (
        <div className="panel-empty">
          <Send size={22} />
          <strong>尚未准备交接包</strong>
          <span>先完善契约，再选择 Codex 验证或 Claude 审查。</span>
        </div>
      )}
      <FindingsView run={run} findings={findings} />
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
