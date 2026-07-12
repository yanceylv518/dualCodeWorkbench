import { useEffect, useState } from "react";
import { Bot, ChevronDown, Cloud, Code2, X } from "lucide-react";
import * as api from "./api";
import type { AgentModel, AgentModelCatalog, AgentSettings } from "./types";

export function SettingsDialog({ onClose }: { onClose: () => void }) {
  const [value, setValue] = useState<AgentSettings>();
  const [health, setHealth] = useState<Record<string, any>>();
  const [models, setModels] = useState<AgentModelCatalog>({ codex: [], claude: [] });
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => { void Promise.all([api.fetchAgentSettings(), api.fetchAgentHealth(), api.fetchAgentModels()]).then(([settings, status, catalog]) => { setValue({ ...settings, enable_real_agents: true, claude_model: settings.claude_model || "opus" }); setHealth(status); setModels(catalog); }).catch((reason) => setError(String(reason))); }, []);
  const field = (key: keyof AgentSettings, label: string, type = "text", placeholder = "") => <label className="settings-field"><span>{label}</span><input type={type} placeholder={placeholder} value={String(value?.[key] ?? "")} onChange={(event) => setValue((current) => current ? { ...current, [key]: type === "number" ? Number(event.target.value) : event.target.value } : current)}/></label>;
  const modelField = (key: "codex_model" | "claude_model", items: AgentModel[], allowAuto = true) => <label className="settings-field model-select"><span>使用模型</span><select value={value?.[key] ?? ""} onChange={(event) => setValue((current) => current ? { ...current, [key]: event.target.value } : current)}>{allowAuto && <option value="">自动（CLI 默认）</option>}{items.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>;
  const effortField = (key: "codex_reasoning_effort" | "claude_reasoning_effort", modelKey: "codex_model" | "claude_model", items: AgentModel[]) => { const selected = items.find((item) => item.id === value?.[modelKey]); const levels = selected?.reasoning_levels?.length ? selected.reasoning_levels : ["low", "medium", "high"]; const labels: Record<string, string> = { low: "轻度 · 更快", medium: "标准 · 平衡", high: "深度 · 复杂任务", xhigh: "超深度", max: "最大", ultra: "极致 · 自动委派" }; return <label className="settings-field"><span>推理强度</span><select value={value?.[key] ?? "medium"} onChange={(event) => setValue((current) => current ? { ...current, [key]: event.target.value } : current)}>{levels.map((level) => <option key={level} value={level}>{labels[level] ?? level}</option>)}</select></label>; };
  const save = async () => { if (!value) return; setSaving(true); setError(""); const sshConfigured = Boolean(value.claude_ssh_host && value.claude_ssh_username && value.claude_ssh_known_hosts); try { setValue(await api.saveAgentSettings({ ...value, enable_real_agents: true, claude_ssh_enabled: sshConfigured })); setHealth(await api.fetchAgentHealth()); } catch (reason) { setError(String(reason)); } finally { setSaving(false); } };
  return <div className="settings-backdrop"><div className="settings-dialog">
    <header className="settings-header"><div className="settings-title-icon"><Bot size={18}/></div><div><h2>Agent 与模型</h2><p>选择日常使用的模型，连接细节保持默认即可</p></div><button onClick={onClose}><X size={18}/></button></header>
    <div className="settings-content">
      <div className="agent-card-grid">
        <section className="agent-card codex"><header><div className="agent-card-icon"><Code2 size={18}/></div><div><h3>Codex</h3><p>本地编码执行</p></div><Status name="本机" value={health?.codex}/></header><div className="agent-controls">{modelField("codex_model", models.codex)}{effortField("codex_reasoning_effort", "codex_model", models.codex)}</div><small>{models.codex.length ? `已从本机加载 ${models.codex.length} 个具体模型版本` : "未发现模型缓存，使用 CLI 默认模型"}</small></section>
        <section className="agent-card claude"><header><div className="agent-card-icon"><Cloud size={18}/></div><div><h3>Claude</h3><p>远程规划与审查</p></div><Status name="VPS" value={health?.claude_ssh}/></header><div className="agent-controls">{modelField("claude_model", models.claude, false)}{effortField("claude_reasoning_effort", "claude_model", models.claude)}</div><small>{health?.claude_ssh?.healthy ? "已通过 SSH 连接 VPS" : "VPS 未连接，可在高级设置中配置"}</small></section>
      </div>
      <details className="settings-advanced"><summary><ChevronDown size={15}/><span>高级设置</span><small>CLI、VPS SSH 与测试命令</small></summary><div className="advanced-content">
        <section><h3>CLI 路径</h3><div className="settings-form-grid">{field("codex_executable", "Codex CLI")}{field("claude_executable", "本地 Claude CLI")}</div></section>
        <section><h3>VPS SSH</h3><div className="settings-form-grid">{field("claude_ssh_host", "主机或 SSH 别名")}{field("claude_ssh_username", "用户名")}{field("claude_ssh_port", "端口", "number")}{field("claude_ssh_remote_root", "远端临时目录")}{field("claude_ssh_executable", "远端 Claude 路径")}{field("claude_ssh_known_hosts", "known_hosts 路径")}<div className="full-field">{field("claude_ssh_client_key", "私钥路径（仅保存路径）")}</div></div></section>
        <section><h3>测试执行器</h3><div className="settings-form-grid">{field("test_executable", "可执行文件（留空自动检测）")}<label className="settings-field"><span>参数（每行一个）</span><textarea value={(value?.test_arguments ?? []).join("\n")} onChange={(event) => setValue((current) => current ? { ...current, test_arguments: event.target.value.split("\n").filter(Boolean) } : current)}/></label></div></section>
      </div></details>
      {error && <div className="settings-error">{error}</div>}
    </div>
    <footer className="settings-footer"><span>配置仅保存在本机</span><button onClick={onClose}>取消</button><button className="primary" disabled={!value || saving} onClick={() => void save()}>{saving ? "保存中…" : "保存并检查"}</button></footer>
  </div></div>;
}

function Status({ name, value }: { name: string; value?: Record<string, unknown> }) { const ok = Boolean(value?.healthy); return <div className={`agent-status ${ok ? "online" : "offline"}`}><i/> {name} · {ok ? "可用" : "未连接"}</div>; }
