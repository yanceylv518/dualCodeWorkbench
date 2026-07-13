import {
  AlertTriangle,
  Check,
  GitCompareArrows,
  ShieldAlert,
} from "lucide-react";
import type { ExecutionJob } from "./types";

type Snapshot = Record<string, unknown>;
const valueText = (value: unknown) =>
  typeof value === "string" && value.trim() ? value.trim() : undefined;

export function safeGitReference(value: unknown): string | undefined {
  const raw = valueText(value);
  if (!raw) return undefined;
  if (/^[0-9a-f]{7,64}$/i.test(raw)) return raw.slice(0, 10);
  if (
    /^[A-Za-z]:[\\/]/.test(raw) ||
    raw.startsWith("\\\\") ||
    raw.startsWith("/")
  ) {
    const tail = raw.replaceAll("\\", "/").split("/").filter(Boolean).at(-1);
    return tail ? `…/${tail.slice(0, 48)}` : "已隐藏";
  }
  if (/^[\w./-]{1,120}$/.test(raw) && !/^[A-Za-z]:[\\/]/.test(raw))
    return raw.slice(0, 72);
  try {
    const url = new URL(raw.replace(/^git@([^:]+):/, "ssh://$1/"));
    const repository = url.pathname
      .split("/")
      .filter(Boolean)
      .at(-1)
      ?.replace(/\.git$/, "");
    return repository ? `${url.hostname}/…/${repository}` : url.hostname;
  } catch {
    const tail = raw.replaceAll("\\", "/").split("/").filter(Boolean).at(-1);
    return tail ? `…/${tail.slice(0, 48)}` : "已隐藏";
  }
}

function fields(snapshot?: Snapshot): [string, string][] {
  if (!snapshot) return [];
  return [
    ["提交", safeGitReference(snapshot.head)],
    ["分支", safeGitReference(snapshot.branch)],
    ["上游", safeGitReference(snapshot.upstream)],
    ["远端", safeGitReference(snapshot.remote)],
    [
      "同步",
      typeof snapshot.ahead === "number" || typeof snapshot.behind === "number"
        ? `领先 ${Number(snapshot.ahead ?? 0)} · 落后 ${Number(snapshot.behind ?? 0)}`
        : undefined,
    ],
  ].filter((row): row is [string, string] => Boolean(row[1]));
}

export function ExecutionEvidence({ job }: { job: ExecutionJob }) {
  const { before, after } = job.evidence ?? {};
  const verified = after?.verified === true;
  const unknown = job.status === "INTERRUPTED" && !verified;
  if (!before && !after)
    return (
      <div className="evidence-empty">
        <ShieldAlert size={13} />
        尚未记录可核验的 Git 状态；重试前请手动确认本地与远端。
      </div>
    );
  return (
    <section
      className={`execution-evidence ${unknown ? "unknown" : verified ? "verified" : ""}`}
    >
      <header>
        <GitCompareArrows size={13} />
        <strong>执行证据</strong>
        <span>
          {verified ? (
            <>
              <Check size={11} />
              已核验
            </>
          ) : unknown ? (
            <>
              <AlertTriangle size={11} />
              结果未知
            </>
          ) : (
            "待核验"
          )}
        </span>
      </header>
      <div className="evidence-snapshots">
        {(
          [
            ["执行前", before],
            ["执行后", after],
          ] as const
        ).map(([label, snapshot]) => {
          const rows = fields(snapshot);
          return (
            <div className="evidence-snapshot" key={label}>
              <small>{label}</small>
              {rows.length ? (
                <dl>
                  {rows.map(([name, value]) => (
                    <div key={name}>
                      <dt>{name}</dt>
                      <dd title={value}>{value}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p>没有可靠快照</p>
              )}
            </div>
          );
        })}
      </div>
      <p className="evidence-guidance">
        {verified
          ? "应用已在操作返回后重新读取仓库状态，可据此确认完成结果。"
          : unknown
            ? "操作可能已经生效。请先核对提交、分支和远端，再决定是否重试，避免重复提交或推送。"
            : "当前证据不足以确认外部副作用，请先检查仓库状态。"}
      </p>
    </section>
  );
}
