# DualCode Workbench

> 新会话或新项目接手时，请先阅读 [AGENTS.md](AGENTS.md) 和
> [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)。后者记录当前完成度、验证结果、
> 已知缺口与下一步，是项目进度的唯一事实来源。

DualCode Workbench 是一个本地 AI 协作开发工作台 MVP：Claude 负责规划和审查，Codex 负责本地实现，过程包含审批、测试、Diff 和审计。它不是 SaaS 后台，也不试图替代完整 IDE。

## 当前演示能力

- 多工作区与任务会话切换、创建新任务
- User / Codex / Claude / System 对话，以及六阶段协作状态轨迹
- 图片选择、拖拽入口，附件元数据与隔离存储 API
- 计划、引用/修改文件、Monaco Diff、测试、分支/worktree 与后台任务面板
- FastAPI + SQLite/SQLAlchemy 数据模型、WebSocket、审批与审计模型
- `MockCodexAdapter` / `MockClaudeAdapter`，以及真实 CLI/SSH 占位适配器
- Tauri 2 桌面壳源码；前端可在缺少 Rust/Python 时独立演示
- 统一结构化 Agent 事件、WebSocket 调度器与 SQLite 持久化 Mock 协作流程
- Adapter 图片能力声明；本地 CLI 使用受控路径，远程 SSH 预留 SFTP 隔离上传

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
uvicorn dualcode.main:app --host 127.0.0.1 --port 8765 --reload
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
pnpm typecheck
pnpm test
pnpm build
pnpm test:e2e
cd apps/backend; pytest; ruff check .
```

## 架构

前端位于 `apps/desktop`，Tauri 壳位于其 `src-tauri`；本地 API 位于 `apps/backend/dualcode`。核心层级为 Workspace → Thread → Message / AgentRun / Attachment / FileChange / TestRun / Approval。附件二进制只落应用数据目录，数据库仅存元数据。详细边界见 `docs/ARCHITECTURE.md`。

后端启动后，前端会从 `/api/workspaces` 加载数据，并连接 `/api/ws/threads/{thread_id}`。如果后端不可用，界面自动进入离线演示模式。生产接入应由 Tauri sidecar 自动启动后端。

## 配置与真实适配器

MVP 不需要生产凭据。后端默认数据目录为 `~/.dualcode-workbench`。接入 Codex 时实现 `CodexCliAdapter`，使用异步 subprocess 参数数组并把输出映射为统一流事件；接入 Claude 时实现 `ClaudeSshAdapter`，使用 SSH/SFTP 库的参数化 API，仅发送明确引用的文件片段、Diff 和测试摘要。不得读取 `.env`、`*.pem`、`*.key`、SSH 私钥或凭据文件。

真实适配器接入前还应补齐：密钥代理/known_hosts 校验、EXIF 解码重编码、附件病毒扫描、完整审计查询、Git worktree 生命周期和人工审批 UI。MVP 不会自动合并、提交或推送。
