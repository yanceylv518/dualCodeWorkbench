# 真实 Agent 适配器

真实本地 CLI 默认关闭。确认工作区和审批策略后，通过环境变量启用：

```powershell
$env:ENABLE_REAL_AGENTS="true"
$env:CODEX_EXECUTABLE="C:\path\to\codex.exe"
$env:CLAUDE_EXECUTABLE="C:\Users\your-name\.local\bin\claude.exe"
```

启动前访问 `/api/agents/health` 检查 CLI 是否可执行及视觉能力。Codex App 内置在 WindowsApps 下的二进制可能受 ACL 保护，推荐配置独立安装、当前用户可执行的 Codex CLI；不要修改 WindowsApps 权限。

`CodexCliAdapter` 使用参数数组启动 `codex exec --json`，prompt 通过 stdin 发送，图片通过重复 `--image` 参数传递。`ClaudeCliAdapter` 固定使用 `--permission-mode plan --tools ""`，只用于规划和审查。两者只继承最小 Windows 环境变量，不会主动读取或传递 `.env` 和密钥文件。

## Claude SSH/SFTP

远程模式只在同时配置 host、username 和 known_hosts 后可用：

```powershell
$env:CLAUDE_SSH_HOST="vps.example.com"
$env:CLAUDE_SSH_USERNAME="dualcode"
$env:CLAUDE_SSH_PORT="22"
$env:CLAUDE_SSH_KNOWN_HOSTS="C:\Users\you\.ssh\known_hosts"
$env:CLAUDE_SSH_CLIENT_KEY="C:\Users\you\.ssh\dualcode_ed25519"
$env:CLAUDE_SSH_REMOTE_ROOT="/tmp/dualcode-workbench"
```

建议使用专用低权限账号和专用密钥。适配器禁用密码与 agent forwarding，强制校验 known_hosts；每个 Thread/Run 使用独立远端目录，只上传显式附件并重命名，Claude 仅开放 Read 工具。任务完成后删除远端临时目录。应用不会上传完整项目、`.git`、`.env` 或密钥文件。
