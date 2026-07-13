# DualCode Workbench

> 新会话或新项目接手时，请先阅读 [AGENTS.md](AGENTS.md) 和
> [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)。后者记录当前完成度、验证结果、
> 已知缺口与下一步，是项目进度的唯一事实来源。

DualCode Workbench 是一个本地双 Agent 开发工作台 MVP。Codex 在本地 Git 项目中对话和执行；Claude 通过 VPS SSH 在远端仓库中规划、分析和审查。两者保持独立的多轮会话，并通过用户现有的 Git commit / push / pull 方式交接。它不是自动流水线、SaaS 后台或完整 IDE。

## 当前演示能力

- 多工作区与任务会话切换、创建新任务
- User / Codex / Claude / System 对话，以及独立的多轮 Agent 会话
- 本地 Codex CLI 与 VPS Claude SSH/SFTP 真实适配器；保留 MockAdapter 用于开发测试
- 图片选择、拖放和剪贴板粘贴，以及类型/大小校验、重编码和隔离存储
- FastAPI + SQLite/SQLAlchemy 数据层与 WebSocket 实时消息、状态、终端和测试推送
- Git 状态、Diff、提交、推送及 fast-forward-only 拉取；敏感操作进入审批和审计
- VPS 仓库配置、状态对比、fetch 与 fast-forward-only pull
- 独立测试执行器、实时输出、结果持久化，以及聚合的 Agent 工具活动摘要
- Tauri 2 桌面应用与冻结 FastAPI sidecar；Windows 后台子进程不显示控制台窗口

## 环境与安装

推荐 Node 20+、pnpm 10+、Rust stable/Cargo、Python 3.12、Git。Windows 还需 Microsoft C++ Build Tools 与 WebView2。

```powershell
corepack pnpm install --ignore-scripts
corepack pnpm dev
```

浏览器打开 `http://127.0.0.1:1420`。运行后端：

```powershell
cd apps/backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn dualcode.main:app --host 127.0.0.1 --port 8876 --reload
```

安装 Rust 后运行桌面壳：

```powershell
corepack pnpm --filter @dualcode/desktop tauri dev
```

打包独立后端和 Windows 安装包：

```powershell
powershell.exe -ExecutionPolicy Bypass -File tools\build_sidecar.ps1
corepack pnpm --filter @dualcode/desktop tauri build
```

Tauri 启动时会自动运行冻结的 FastAPI sidecar，应用正常退出或父进程异常终止时都会回收后端进程。目标机器不需要单独安装 Python。

## 检查与测试

```powershell
corepack pnpm --filter @dualcode/desktop typecheck
apps\backend\.venv\Scripts\python.exe -m pytest apps\backend\tests -q
corepack pnpm --dir tests/e2e test
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\build_sidecar.ps1
$env:PATH="$HOME\.cargo\bin;$env:PATH"
corepack pnpm --filter @dualcode/desktop tauri build --no-bundle
```

E2E 冒烟测试会自行启动前端开发服务器，验证无项目时的真实空状态和设置对话框；不需要演示数据，后端可以不启动。

## 架构

前端位于 `apps/desktop`，Tauri 壳位于其 `src-tauri`；本地 API 位于 `apps/backend/dualcode`。核心层级为 Workspace → Thread → Message / AgentRun / Attachment / FileChange / TestRun / Approval。附件二进制只落应用数据目录，数据库仅存元数据。详细边界见 `docs/ARCHITECTURE.md`。

后端启动后，前端会从 `/api/workspaces` 加载数据，并连接 `/api/ws/threads/{thread_id}`。Tauri 应用负责启动和回收冻结的后端 sidecar；浏览器开发模式需要单独启动后端。

## 配置与真实适配器

后端默认数据目录为 `~/.dualcode-workbench`。真实 Agent 默认关闭，需要显式配置可执行文件和 SSH 参数；具体方式见 [docs/AGENT_ADAPTERS.md](docs/AGENT_ADAPTERS.md)。Claude SSH 强制校验 known_hosts，只上传显式附件，不同步完整本地项目；本地与远端命令均使用参数化调用。

应用不会读取或上传 `.env`、`*.pem`、`*.key`、SSH 私钥或凭据文件。删除、安装、联网、Git 提交、推送、拉取和写入等敏感操作必须审批，并留下审计记录。MVP 不自动合并或静默推送代码。
