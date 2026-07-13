# DualCode Workbench 接手指南

## 产品边界

DualCode Workbench 是本地双 Agent 开发工作台，不是自动流水线、SaaS 后台或完整 IDE。

- Codex：在本地 Git 项目中多轮对话和执行。
- Claude：通过 VPS SSH 在远端仓库中多轮规划、分析和审查。
- 两者通过用户现有的 Git commit / push / pull 方式交接。
- 删除、安装、联网、提交、推送、拉取和写入等敏感操作必须经过应用审批。

## 新会话开始前

1. 完整阅读 `docs/PROJECT_STATUS.md`。
2. 按需阅读 `docs/ARCHITECTURE.md` 和 `docs/AGENT_ADAPTERS.md`。
3. 运行 `git status --short`，不要覆盖用户修改。
4. 修改前先运行相关测试；修改后至少运行类型检查和后端测试。
5. 不读取或上传 `.env`、`*.pem`、`*.key`、SSH 私钥或凭据文件。

## 常用验证命令

```powershell
corepack pnpm --filter @dualcode/desktop typecheck
apps\backend\.venv\Scripts\python.exe -m pytest apps\backend\tests -q
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\build_sidecar.ps1
$env:PATH="$HOME\.cargo\bin;$env:PATH"
corepack pnpm --filter @dualcode/desktop tauri build --no-bundle
```

## 关键实现约定

- 后端端口：`127.0.0.1:8876`；前端开发端口：`127.0.0.1:1420`。
- Windows 后台子进程必须使用 `subprocess.CREATE_NO_WINDOW`。
- Agent 工具事件不得逐条刷入对话，应聚合为可折叠活动摘要；原始输出进入终端面板。
- 附件二进制存应用数据目录，SQLite 只存元数据。
- VPS 只使用参数化 SSH/SFTP；不得完整同步本地项目。
- Git 拉取只允许 fast-forward；MVP 不自动合并。

## 当前进度

以 `docs/PROJECT_STATUS.md` 为唯一进度事实来源。完成重要功能、验证或发现阻塞后，同步更新该文件。
