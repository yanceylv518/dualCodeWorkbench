# 架构与安全边界

React/Tauri 只负责本机 UI 与受限系统桥接；FastAPI 是唯一业务入口，SQLite 保存元数据，附件按 `workspace_id/thread_id` 分目录保存。`AgentAdapter` 隔离模型实现，Mock 与真实 CLI/SSH 共用契约。Claude 默认仅收到显式引用、Diff 与测试摘要，不同步完整项目，也不拥有本机写权限。

所有删除、安装依赖、网络访问、Git 提交和推送先创建 `Approval`；所有模型调用、审批、状态迁移与文件变化写入 `AuditLog`。真实 SSH 实现必须以参数数组调用进程，拒绝 shell 字符串拼接，并使用明确的远程工作目录和允许命令列表。

## 本地 API 鉴权

桌面壳每次启动生成独立的高熵 sidecar token，通过进程环境变量交给 FastAPI sidecar，并通过只读
Tauri 命令交给当前 WebView。所有 `/api/*` HTTP 请求必须携带 `X-DualCode-Token`，WebSocket
和附件直链使用 `token` 查询参数；后端在统一 ASGI 中间件中校验。浏览器开发模式启动后端时会在
`~/.dualcode-workbench/sidecar.token` 写入仅当前用户可读的临时 token，Vite 启动时读取它。
token 不进入 SQLite、审计日志或应用日志；生产模式 token 不落盘。CORS OPTIONS 预检不访问业务
数据，因此允许无 token 通过。
