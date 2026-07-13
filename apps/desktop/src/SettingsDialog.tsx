import { useEffect, useRef, useState } from "react";
import {
  Bot,
  ChevronDown,
  Cloud,
  Code2,
  LoaderCircle,
  RefreshCw,
  X,
} from "lucide-react";
import * as api from "./api";
import type { AgentModel, AgentModelCatalog, AgentSettings } from "./types";

const lines = (value: string) =>
  value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);

export function SettingsDialog({
  onClose,
  target = "general",
}: {
  onClose: () => void;
  target?: "general" | "tests";
}) {
  const [value, setValue] = useState<AgentSettings>();
  const [health, setHealth] = useState<Record<string, any>>();
  const [models, setModels] = useState<AgentModelCatalog>({
    codex: [],
    claude: [],
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [testArgumentsText, setTestArgumentsText] = useState("");
  const testSection = useRef<HTMLElement>(null);
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [settings, status, catalog] = await Promise.all([
        api.fetchAgentSettings(),
        api.fetchAgentHealth(),
        api.fetchAgentModels(),
      ]);
      setValue({
        ...settings,
        enable_real_agents: true,
        claude_model: settings.claude_model || "opus",
      });
      setTestArgumentsText(settings.test_arguments.join("\n"));
      setHealth(status);
      setModels(catalog);
    } catch (reason) {
      setError(`加载配置失败：${String(reason)}`);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  useEffect(() => {
    if (target === "tests" && value)
      window.setTimeout(
        () =>
          testSection.current?.scrollIntoView({
            behavior: "smooth",
            block: "center",
          }),
        50,
      );
  }, [target, value]);
  const changed = () => setSaved(false);
  const field = (
    key: keyof AgentSettings,
    label: string,
    type = "text",
    placeholder = "",
  ) => (
    <label className="settings-field">
      <span>{label}</span>
      <input
        type={type}
        placeholder={placeholder}
        value={String(value?.[key] ?? "")}
        onChange={(event) => {
          changed();
          setValue((current) =>
            current
              ? {
                  ...current,
                  [key]:
                    type === "number"
                      ? Number(event.target.value)
                      : event.target.value,
                }
              : current,
          );
        }}
      />
    </label>
  );
  const modelField = (
    key: "codex_model" | "claude_model",
    items: AgentModel[],
    allowAuto = true,
  ) => (
    <label className="settings-field model-select">
      <span>使用模型</span>
      <select
        value={value?.[key] ?? ""}
        onChange={(event) => {
          changed();
          setValue((current) =>
            current ? { ...current, [key]: event.target.value } : current,
          );
        }}
      >
        {allowAuto && <option value="">自动（CLI 默认）</option>}
        {items.map((item) => (
          <option key={item.id} value={item.id}>
            {item.label}
          </option>
        ))}
      </select>
    </label>
  );
  const effortField = (
    key: "codex_reasoning_effort" | "claude_reasoning_effort",
    modelKey: "codex_model" | "claude_model",
    items: AgentModel[],
  ) => {
    const selected = items.find((item) => item.id === value?.[modelKey]);
    const levels = selected?.reasoning_levels?.length
      ? selected.reasoning_levels
      : ["low", "medium", "high"];
    const labels: Record<string, string> = {
      low: "轻度 · 更快",
      medium: "标准 · 平衡",
      high: "深度 · 复杂任务",
      xhigh: "超深度",
      max: "最大",
      ultra: "极致 · 自动委派",
    };
    return (
      <label className="settings-field">
        <span>推理强度</span>
        <select
          value={value?.[key] ?? "medium"}
          onChange={(event) => {
            changed();
            setValue((current) =>
              current ? { ...current, [key]: event.target.value } : current,
            );
          }}
        >
          {levels.map((level) => (
            <option key={level} value={level}>
              {labels[level] ?? level}
            </option>
          ))}
        </select>
      </label>
    );
  };
  const permissionField = () => (
    <label className="settings-field permission-mode">
      <span>执行权限</span>
      <select
        value={value?.codex_permission_mode ?? "safe"}
        onChange={(event) => {
          changed();
          setValue((current) =>
            current
              ? {
                  ...current,
                  codex_permission_mode: event.target
                    .value as AgentSettings["codex_permission_mode"],
                }
              : current,
          );
        }}
      >
        <option value="safe">安全审批（推荐）</option>
        <option value="workspace_auto">工作区自动开发</option>
        <option value="full_access">完全访问（最高权限）</option>
      </select>
    </label>
  );
  const save = async () => {
    if (!value) return;
    if (
      value.codex_permission_mode === "full_access" &&
      !window.confirm(
        "完全访问会让 Codex 无需审批即可访问当前 Windows 用户可访问的文件、执行命令和联网操作。确认启用吗？",
      )
    )
      return;
    setSaving(true);
    setSaved(false);
    setError("");
    const sshConfigured = Boolean(
      value.claude_ssh_host &&
      value.claude_ssh_username &&
      value.claude_ssh_known_hosts,
    );
    try {
      const persisted = await api.saveAgentSettings({
        ...value,
        test_arguments: lines(testArgumentsText),
        enable_real_agents: true,
        claude_ssh_enabled: sshConfigured,
      });
      setValue(persisted);
      setSaved(true);
      try {
        setHealth(await api.fetchAgentHealth());
      } catch (reason) {
        setError(`配置已保存，但连接检查未完成：${String(reason)}`);
      }
    } catch (reason) {
      setError(`保存配置失败：${String(reason)}`);
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="settings-backdrop">
      <div className="settings-dialog">
        <header className="settings-header">
          <div className="settings-title-icon">
            <Bot size={18} />
          </div>
          <div>
            <h2>Agent 与模型</h2>
            <p>选择日常使用的模型，连接细节保持默认即可</p>
          </div>
          <button onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        <div className="settings-content">
          {loading ? (
            <div className="settings-loading">
              <LoaderCircle className="spin" size={24} />
              <strong>正在加载模型与连接状态…</strong>
              <span>正在读取本机 CLI、模型目录和 VPS 健康状态</span>
            </div>
          ) : (
            <>
              <div className="agent-card-grid">
                <section className="agent-card codex">
                  <header>
                    <div className="agent-card-icon">
                      <Code2 size={18} />
                    </div>
                    <div>
                      <h3>Codex</h3>
                      <p>本地编码执行</p>
                    </div>
                    <Status name="本机" value={health?.codex} />
                  </header>
                  <div className="agent-controls">
                    {modelField("codex_model", models.codex)}
                    {effortField(
                      "codex_reasoning_effort",
                      "codex_model",
                      models.codex,
                    )}
                    {permissionField()}
                  </div>
                  <small>
                    {value?.codex_permission_mode === "full_access"
                      ? "最高权限：不使用沙箱且不再请求 Codex 原生审批"
                      : value?.codex_permission_mode === "workspace_auto"
                        ? "自动允许工作区内操作，仍禁止默认联网和项目外写入"
                        : models.codex.length
                          ? `已从本机加载 ${models.codex.length} 个具体模型版本`
                          : "未发现模型缓存，使用 CLI 默认模型"}
                  </small>
                </section>
                <section className="agent-card claude">
                  <header>
                    <div className="agent-card-icon">
                      <Cloud size={18} />
                    </div>
                    <div>
                      <h3>Claude</h3>
                      <p>远程规划与审查</p>
                    </div>
                    <Status name="VPS" value={health?.claude_ssh} />
                  </header>
                  <div className="agent-controls">
                    {modelField("claude_model", models.claude, false)}
                    {effortField(
                      "claude_reasoning_effort",
                      "claude_model",
                      models.claude,
                    )}
                  </div>
                  <small>
                    {health?.claude_ssh?.healthy
                      ? "已通过 SSH 连接 VPS"
                      : "VPS 未连接，可在高级设置中配置"}
                  </small>
                </section>
              </div>
              <details
                className="settings-advanced"
                open={target === "tests" ? true : undefined}
              >
                <summary>
                  <ChevronDown size={15} />
                  <span>高级设置</span>
                  <small>CLI、VPS SSH 与测试命令</small>
                </summary>
                <div className="advanced-content">
                  <section>
                    <h3>CLI 路径</h3>
                    <div className="settings-form-grid">
                      {field("codex_executable", "Codex CLI")}
                    </div>
                  </section>
                  <section>
                    <h3>VPS SSH</h3>
                    <p className="settings-section-help">
                      项目目录会根据远程仓库名在 VPS
                      项目根目录下自动生成；运行数据与项目源码分开保存，不会完整上传本地项目。
                    </p>
                    <div className="settings-form-grid">
                      {field("claude_ssh_host", "主机或 SSH 别名")}
                      {field("claude_ssh_username", "用户名")}
                      {field("claude_ssh_port", "端口", "number")}
                      <div className="full-field">
                        {field(
                          "claude_ssh_projects_root",
                          "VPS 项目根目录",
                          "text",
                          "/home/yancey/work",
                        )}
                      </div>
                      <div className="full-field">
                        {field(
                          "claude_ssh_remote_root",
                          "远端运行根目录（可选）",
                          "text",
                          "留空时自动使用 ~/.dualcode",
                        )}
                      </div>
                      {field("claude_ssh_executable", "远端 Claude 路径")}
                      {field("claude_ssh_known_hosts", "known_hosts 路径")}
                      <div className="full-field">
                        {field(
                          "claude_ssh_client_key",
                          "私钥路径（仅保存路径）",
                        )}
                      </div>
                    </div>
                  </section>
                  <section
                    ref={testSection}
                    className={target === "tests" ? "settings-target" : ""}
                  >
                    <h3>测试执行器</h3>
                    <p className="settings-section-help">
                      配置当前项目实际使用的测试命令。可执行文件与参数分开填写。
                    </p>
                    <div className="settings-form-grid">
                      {field(
                        "test_executable",
                        "测试可执行文件",
                        "text",
                        "例如 pytest 或 corepack",
                      )}
                      <label className="settings-field">
                        <span>参数（每行一个）</span>
                        <textarea
                          placeholder={
                            "例如：\npnpm\n--filter\n@dualcode/desktop\ntest"
                          }
                          value={testArgumentsText}
                          onChange={(event) => {
                            changed();
                            setTestArgumentsText(event.target.value);
                          }}
                        />
                      </label>
                    </div>
                  </section>
                </div>
              </details>
            </>
          )}
          {error && (
            <div className="settings-error settings-load-error">
              <span>{error}</span>
              {!value && (
                <button onClick={() => void load()}>
                  <RefreshCw size={12} />
                  重试
                </button>
              )}
            </div>
          )}
        </div>
        <footer className="settings-footer">
          <span className={saved ? "settings-saved" : ""}>
            {loading
              ? "正在检查本地环境"
              : saved
                ? "✓ 配置已保存，连接状态已刷新"
                : "配置仅保存在本机"}
          </span>
          <button onClick={onClose}>{saved ? "完成" : "取消"}</button>
          <button
            className="primary"
            disabled={loading || !value || saving || saved}
            onClick={() => void save()}
          >
            {loading
              ? "加载中…"
              : saving
                ? "保存中…"
                : saved
                  ? "已保存"
                  : "保存并检查"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function Status({
  name,
  value,
}: {
  name: string;
  value?: Record<string, unknown>;
}) {
  const ok = Boolean(value?.healthy);
  return (
    <div className={`agent-status ${ok ? "online" : "offline"}`}>
      <i /> {name} · {ok ? "可用" : "未连接"}
    </div>
  );
}
