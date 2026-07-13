import { useEffect, useState } from "react";
import { Check, LoaderCircle, Save, ShieldCheck } from "lucide-react";
import * as api from "./api";
import type { ProjectContract } from "./types";
import "./contract.css";

const lines = (value: string) => value.split("\n").map((item) => item.trim()).filter(Boolean);

export function ContractPanel({ workspaceId, threadId }: { workspaceId: string; threadId: string }) {
  const [value, setValue] = useState<ProjectContract>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const load = async () => { setLoading(true); setError(""); try { setValue(await api.fetchContract(workspaceId, threadId)); } catch (reason) { setError(String(reason)); } finally { setLoading(false); } };
  useEffect(() => { if (workspaceId && threadId) void load(); }, [workspaceId, threadId]);
  const save = async () => { if (!value) return; setSaving(true); setError(""); try { await api.saveGovernance(workspaceId, value.governance); await api.saveTaskContract(workspaceId, threadId, value.task); await load(); } catch (reason) { setError(String(reason)); } finally { setSaving(false); } };
  const listField = (key: keyof Pick<ProjectContract["task"], "non_goals" | "acceptance" | "constraints" | "risks">, label: string) => <label><span>{label}</span><textarea value={value?.task[key].join("\n") ?? ""} onChange={(event) => setValue((current) => current ? { ...current, task: { ...current.task, [key]: lines(event.target.value) } } : current)} placeholder="每行一项"/></label>;
  if (loading) return <div className="contract-loading"><LoaderCircle className="spin"/><span>正在加载项目规则与任务契约…</span></div>;
  if (!value) return <div className="panel-empty"><ShieldCheck/><strong>契约加载失败</strong><span>{error}</span><button onClick={() => void load()}>重试</button></div>;
  return <div className="contract-panel">
    <header><div><strong>规则与交付契约</strong><span>这些约束会自动提供给 Codex 和 Claude。</span></div><button disabled={saving} onClick={() => void save()}>{saving ? <LoaderCircle className="spin" size={13}/> : <Save size={13}/>}保存契约</button></header>
    <section className={`contract-gate ${value.gate.ready_for_implementation ? "ready" : "missing"}`}><Check size={14}/><div><strong>{value.gate.ready_for_implementation ? "已具备正式实施条件" : "实施契约尚不完整"}</strong><span>{value.gate.ready_for_implementation ? "产品目标、任务目标、验收标准和产品级原则已定义。" : `缺少：${value.gate.missing.join("、")}`}</span></div></section>
    <section><h3>项目治理</h3><label><span>产品目标</span><textarea value={value.governance.product_goal} onChange={(event) => setValue({ ...value, governance: { ...value.governance, product_goal: event.target.value } })}/></label><label><span>产品边界</span><textarea value={value.governance.product_boundary} onChange={(event) => setValue({ ...value, governance: { ...value.governance, product_boundary: event.target.value } })}/></label><label><span>项目规则（每行一项）</span><textarea value={value.governance.rules.join("\n")} onChange={(event) => setValue({ ...value, governance: { ...value.governance, rules: lines(event.target.value) } })}/></label><label><span>必备交付物（每行一项）</span><textarea value={value.governance.deliverables.join("\n")} onChange={(event) => setValue({ ...value, governance: { ...value.governance, deliverables: lines(event.target.value) } })}/></label></section>
    <section><h3>当前任务契约</h3><label><span>任务目标</span><textarea value={value.task.goal} onChange={(event) => setValue({ ...value, task: { ...value.task, goal: event.target.value } })}/></label><label><span>阶段</span><select value={value.task.status} onChange={(event) => setValue({ ...value, task: { ...value.task, status: event.target.value as ProjectContract["task"]["status"] } })}>{["DRAFT","CLARIFYING","READY","IMPLEMENTING","REVIEWING","CONDITIONAL_PASS","PASSED","BLOCKED"].map((item) => <option key={item}>{item}</option>)}</select></label>{listField("non_goals", "明确不做")}{listField("acceptance", "验收标准")}{listField("constraints", "任务约束")}{listField("risks", "已知风险")}</section>
    {error && <div className="settings-error">{error}</div>}
  </div>;
}
