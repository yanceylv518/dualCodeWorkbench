# DualCode Workbench 项目状态

更新时间：2026-07-12

## 当前阶段

项目处于“可交付 MVP 联调与验收”阶段。桌面端、冻结后端和真实 Agent 适配骨架已经可运行，但尚未完成正式安装包与全量真实场景验收。

## 已完成

- Tauri 2 + React + TypeScript + Vite + Zustand + Tailwind 风格桌面界面。
- 可拉伸、隐藏的左右面板，自定义窗口标题栏和深色开发工具主题。
- FastAPI + SQLite + SQLAlchemy，本地 sidecar 随桌面应用启动和回收。
- Workspace / Thread / Message / AgentRun / Attachment / FileChange / TestRun / Approval / Audit 数据层。
- WebSocket 消息、状态、终端、工具事件和测试结果推送。
- Codex 与 Claude 独立多轮会话；不是固定自动工作流。
- 本地 Codex CLI、VPS Claude SSH/SFTP 真实适配器及 MockAdapter。
- Codex/Claude 模型和推理强度设置，Claude 默认 Opus 4.8 展示选项。
- 图片选择、拖放、剪贴板粘贴、类型/大小校验、图片重编码清理元数据。
- Git 状态、Diff、提交、推送、fast-forward 拉取和审批。
- VPS 仓库 URL/路径配置、状态对比、fetch/pull 和审批。
- 独立测试执行器、实时输出、结果持久化和审计。
- Windows 后台子进程无控制台窗口。
- 新建任务互斥与空草稿复用，避免重复空任务。
- Agent 工具事件聚合显示，详细输出进入终端页。

## 已验证

- 后端单元测试：25 项通过。
- 前端 TypeScript 类型检查通过。
- Vite 与 Tauri release 构建通过。
- Release EXE：`apps/desktop/src-tauri/target/release/dualcode-workbench.exe`。
- Git 交接 E2E：隔离本地 `ecsMonitor` 副本从 `b7aa338` fast-forward 到 VPS/远端的 `4cd38a0`，同一仓库、同一提交且工作区干净。

## 当前正在验证

- 真实 Codex CLI 在 Windows sidecar 下的仓库读取、多轮续接和流式输出。
- 工具活动摘要的最终视觉和完成状态。
- 真实 VPS Claude 多轮会话、图片附件和远端仓库上下文。

## 已知缺口

1. 审批等待协程目前以内存为主。后端在审批期间重启时，数据库会记录决定，但原执行任务不会自动恢复；需要实现持久化作业恢复器。
2. 尚未完成 Codex 第二轮续接、Claude 第二轮续接、双 Agent 图片分析的完整人工验收。
3. 尚未构建和验证 Windows 安装程序、全新机器首次启动及卸载流程。
4. README 的部分早期 Mock 描述需要继续与当前真实功能对齐。
5. 需要清理旧演示数据、重复历史空任务和早期乱码数据。
6. Monaco 体积较大，生产包仍有前端 chunk 体积警告，可后续懒加载。
7. 已准备首次 Git 基线提交；远程仓库地址仍需配置。

## 下一步顺序

1. 完成真实 Codex 只读、多轮、停止和失败恢复验收。
2. 完成 VPS Claude 只读、多轮、图片和远端仓库验收。
3. 实现审批任务持久化恢复，避免后端重启丢失待执行工作。
4. 完成附件、测试、Diff 和审计的端到端冒烟测试。
5. 清理演示数据并更新 README。
6. 配置远程仓库并推送 Git 基线提交。
7. 构建 Windows 安装包并执行全新安装验收。

## 安全不变量

- 不读取或上传凭据文件。
- Claude 默认不获得本机写权限。
- 不把本地项目完整同步到 VPS。
- SSH 命令不拼接不可信输入。
- 模型调用、审批、Git 和文件变化必须留审计记录。
- 不自动合并或静默推送代码。
