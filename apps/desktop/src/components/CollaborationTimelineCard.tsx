import { useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronUp,
  Circle,
  LoaderCircle,
} from "lucide-react";
import type { CollaborationTimeline } from "../types";
import "./collaboration-timeline.css";

const stages = [
  ["clarify", "澄清"],
  ["implement", "实现"],
  ["verify", "验证"],
  ["review", "审查"],
  ["fix", "整改"],
] as const;

interface Props {
  timeline: CollaborationTimeline;
  act: (
    action: "reenter" | "fix" | "resume" | "cancel",
    note?: string,
  ) => Promise<void>;
}

export function CollaborationTimelineCard({ timeline, act }: Props) {
  const [expanded, setExpanded] = useState(
    timeline.status === "running" || timeline.status === "waiting",
  );
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (action: "reenter" | "fix" | "resume" | "cancel") => {
    if (busy) return;
    setBusy(true);
    try {
      await act(action, note.trim());
      setNote("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="collaboration-timeline" aria-label="智能协作进度">
      <button
        className="collaboration-timeline__header"
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        <span>
          智能协作 · 第 {timeline.round}/{timeline.maxRounds} 轮
        </span>
        <span className="collaboration-timeline__summary">
          {timeline.findingsCount > 0 && (
            <span className="collaboration-timeline__badge">
              {timeline.findingsCount} 个待处理
            </span>
          )}
          {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
        </span>
      </button>
      {expanded && (
        <div className="collaboration-timeline__body">
          <ol className="collaboration-timeline__stages">
            {stages.map(([key, label]) => {
              const status = timeline.stages[key] ?? "pending";
              return (
                <li key={key} data-status={status}>
                  {status === "completed" ? (
                    <Check size={15} />
                  ) : status === "running" ? (
                    <LoaderCircle className="spin" size={15} />
                  ) : (
                    <Circle size={12} />
                  )}
                  <span>{label}</span>
                </li>
              );
            })}
          </ol>
          <div className="collaboration-timeline__state">
            当前状态：{timeline.state}
            {timeline.currentAgent && ` · ${timeline.currentAgent}`}
          </div>
          {timeline.waitingReason && (
            <p className="collaboration-timeline__reason">
              {timeline.waitingReason}
            </p>
          )}
          {timeline.state === "WAITING_USER" && (
            <div className="collaboration-timeline__intervention">
              <p className="collaboration-timeline__guidance">
                不用填写固定格式。需要调整时直接说希望改什么；没有补充可直接继续，工具会自动维护任务契约。
              </p>
              <input
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="例如：只做现状分析，不修改代码"
                aria-label="协作调整说明"
              />
              <div>
                <button disabled={busy} onClick={() => void submit("reenter")}>
                  补充后继续
                </button>
                <button disabled={busy} onClick={() => void submit("fix")}>
                  直接整改
                </button>
                <button disabled={busy} onClick={() => void submit("cancel")}>
                  停止
                </button>
              </div>
            </div>
          )}
          {timeline.state === "BLOCKED" && (
            <div className="collaboration-timeline__actions">
              <button disabled={busy} onClick={() => void submit("resume")}>
                恢复
              </button>
              <button disabled={busy} onClick={() => void submit("cancel")}>
                取消
              </button>
            </div>
          )}
          {timeline.status === "running" && (
            <div className="collaboration-timeline__actions">
              <button disabled={busy} onClick={() => void submit("cancel")}>
                停止
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
