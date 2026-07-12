# 架构与安全边界

React/Tauri 只负责本机 UI 与受限系统桥接；FastAPI 是唯一业务入口，SQLite 保存元数据，附件按 `workspace_id/thread_id` 分目录保存。`AgentAdapter` 隔离模型实现，Mock 与真实 CLI/SSH 共用契约。Claude 默认仅收到显式引用、Diff 与测试摘要，不同步完整项目，也不拥有本机写权限。

所有删除、安装依赖、网络访问、Git 提交和推送先创建 `Approval`；所有模型调用、审批、状态迁移与文件变化写入 `AuditLog`。真实 SSH 实现必须以参数数组调用进程，拒绝 shell 字符串拼接，并使用明确的远程工作目录和允许命令列表。
