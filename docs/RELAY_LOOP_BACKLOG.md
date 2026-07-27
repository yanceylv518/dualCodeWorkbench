# 协作闭环清单（Relay Loop Backlog）

> 目标：解决「离真正可用还有差距」的两个核心问题——
> **T 系列**：思考过程先行流式展示（对齐 Codex CLI / Claude App 的「先思考、后结果」体验）；
> **R 系列**：Codex 实现 → Claude 审查 → 发现自动回注修复的**自动接力循环**，
> 用户从信使变成裁判，只在关键节点介入。
> 执行约定沿用 `docs/REMEDIATION_BACKLOG.md` 全部规则（一条目一 commit、附测试、
> 禁止顺手重构、全量验证、每 Phase 完成后停下等 Claude review）。
>
> 决策背景（2026-07-19，用户确认）：协作形态选定「自动接力循环」；Claude 必须保持
> 在 VPS 运行（硬约束），因此同步机制必须替代手动 commit/push，不能改为本地直读。

## 保护条款

1. **影子同步只触碰专用 ref**：自动同步只推送 `refs/dualcode/relay/<workspace_id>/<thread_id>`，
   永不改写用户分支、main 或 origin 的常规分支；每个任务首次同步前需要一次显式
   「允许本任务自动同步」授权（复用既有 thread scope 审批机制），每次同步留审计。
2. **不自动合并、不静默推送**：接力循环产出的正式 commit/push 仍走既有审批流程，
   循环本身无权发起。影子快照是临时评审载体，任务结束后清理。
3. **VPS 审查隔离**：Claude 审查在 VPS 上以独立 `git worktree` 检出影子 ref，
   不改动 VPS 主仓库的工作区与 HEAD。
4. **循环有上限、可打断**：轮次上限默认 3（可配置）；任何一轮出现审批请求即挂起循环
   等待用户；用户可随时停止；上限到达后停在「等待用户裁决」而不是静默继续。
5. **安全不变量修订**：原「不把本地项目完整同步到 VPS」修订为「只经 Git 影子 ref 同步
   任务相关提交内容，不经 SFTP 上传工作区文件」；凭据防护、known_hosts 校验、
   参数化命令等其余不变量一律不放宽。
6. 思考文本只展示模型明文输出的 thinking/reasoning 内容；`redacted_thinking`、
   signature 等加密或协议字段不展示、不落库正文。

---

## Phase T：思考过程先行展示

### T1 Claude thinking 块接入（root cause 修复）

**现状**：`claude_stream.py` 的 `feed()` 只处理 `text` / `tool_use` / `tool_result`，
`thinking` 块无分支、被静默丢弃——模型思考内容永远不可见。

- [x] `ClaudeStreamParser` 为 `thinking` 块产出 reasoning 类活动事件（对齐 Codex 的
  `activity.delta` / reasoning item 语义），进入前端既有 A2 思考态 UI；
  `redacted_thinking` 忽略并计入终端诊断行，不进思考区。
- [x] SSH 与本地 CLI 两条路径共用该解析器，行为一致（现状已共用，补测试锁定）。
- **验收**：协议测试覆盖「thinking 块 → reasoning 事件、text 块 → DELTA、
  redacted_thinking 不泄漏」；真实 VPS 会话可看到「正在思考…」态出现思考文本。
- **验证结果（2026-07-19）**：协议测试已覆盖 thinking、text 与 redacted thinking；
  本地 CLI/VPS SSH 两条适配路径均锁定 reasoning 事件行为。后端 116 项、前端 75 项、
  TypeScript 与 Ruff 全部通过；真实 VPS 会话显示由安装包验收确认。

### T2 Claude 逐 token 流式（--include-partial-messages）

**现状**：CLI 调用（`cli_adapters.py` 与 `ssh_adapter.py`）缺 `--include-partial-messages`，
stream-json 按整条 assistant 消息吐出，思考与正文都是「憋完一大块才出现」。

- [ ] 两条调用路径增加 `--include-partial-messages`，解析 `stream_event` 包络中的
  `content_block_delta`（`thinking_delta` → 思考增量，`text_delta` → 正文增量），
  实现思考先流出、正文后流出的时序。
- [ ] 兼容降级：CLI 版本不支持该 flag 或未出现 partial 事件时，回退现有整块解析，
  不得报错或重复输出（partial 与整条 assistant 消息并存时需去重，方案动手前写在本条目下）。
- **验收**：协议测试覆盖 partial 事件序列（thinking→text→result）与去重；
  真实 VPS 会话目视确认思考逐行流出后正文再开始。

### T3 Codex reasoning 摘要核查与开启

**现状**：`codex_app_server.py` 已解析 `item/reasoning/*` 增量，但 turn 配置仅传
`model`/`effort`，未显式开启 reasoning summary，疑似导致思考事件实际很少到达。

- [ ] 核查 codex app-server 协议中 reasoning summary 的开关字段（如
  `summary`/`model_reasoning_summary`），在 turn 或会话配置中显式开启；
  确认 `item/reasoning/*` 增量在真实会话中到达并进入思考态 UI。
- [ ] 若协议不支持或仅提供终态摘要，把实际能力与限制记录到本条目验证结果，
  前端按「已思考 X 秒 + 摘要」降级展示，不伪造流式。
- **验收**：真实 Codex 会话录屏/截图证据：思考态先于正文出现；协议测试覆盖开启后的事件流。

---

## Phase R0：影子同步通道（本地 → VPS，替代手动 push）

### R0-1 本地影子快照生成

- [ ] 不触碰用户 index 与工作树：用临时 index（`GIT_INDEX_FILE` + `git add -A` +
  `git commit-tree`）把当前工作区全部变更（含未提交、未暂存）固化为快照 commit，
  父指向当前 HEAD；记录 `base_sha` 与 `snapshot_sha` 到接力轮次记录。
- [ ] 凭据防护规则（security.py 现有 glob 列表）在快照阶段生效：命中规则的文件
  不进入快照并在轮次记录中列出被排除项。
- **验收**：单元测试覆盖「脏工作树快照后用户工作树/index/HEAD 零变化」「敏感文件被排除」。

### R0-2 VPS 直连推送影子 ref

- [ ] 经既有 SSH 通道以 `ssh://` remote（或等价参数化 `git push` 命令）把快照推送到
  VPS 仓库的 `refs/dualcode/relay/<workspace_id>/<thread_id>`；known_hosts 校验、
  参数化调用沿用现有 SSH 安全机制，不新增 shell 拼接。
- [ ] 推送失败（网络、权限、非快进）给出中文原因并允许该轮显式重试；不自动改推 origin。
- **验收**：集成测试（本地裸仓模拟 VPS）覆盖成功推送、非快进覆盖同 ref、失败重试。

### R0-3 每任务授权与影子 ref 清理

- [ ] 任务首次自动同步前弹出一次审批：「允许本任务自动同步影子快照到 VPS？」，
  批准后本任务内后续轮次复用（重启后经审计恢复，语义同现有「允许本任务」）。
- [ ] 任务删除或接力结束时删除本地与 VPS 侧影子 ref；清理失败仅告警不阻塞。
- **验收**：审批/审计测试覆盖首次授权、复用、重启恢复；清理有测试。

## Phase R1：Claude 审查协议

### R1-1 VPS 隔离 worktree 审查执行

- [ ] Claude 审查轮在 VPS 上执行：`git fetch` 影子 ref → 在临时路径
  `git worktree add --detach` 检出 `snapshot_sha` → Claude 以该目录为工作目录审查 →
  轮次结束移除 worktree。VPS 主仓库工作区与 HEAD 全程不动。
- [ ] 审查输入复用既有交接包结构（契约摘要、`base_sha..snapshot_sha` 变更文件与 Diff
  统计、测试证据），不复制聊天全文。
- **验收**：SSH 命令序列有协议测试；VPS 主仓库状态不变有集成断言。

### R1-2 机器可读裁决

- [ ] Claude 审查提示词在自然语言结论后强制输出固定 JSON 块：
  `{"verdict": "pass" | "blocking", "findings": [{"type": "未实现|部分实现|回归|潜在问题|架构违规|证据缺口", "severity": "blocking|advisory", "file": "...", "desc": "...", "suggestion": "..."}]}`。
- [ ] 后端容错解析（围栏内 JSON 提取、字段缺省），解析失败时该轮降级为人工模式：
  展示原文并停下等用户，不猜测 verdict。
- **验收**：解析单元测试覆盖正常、字段缺省、无 JSON、非法 JSON 四类输入。

## Phase R2：编排循环状态机

### R2-1 relay 执行模式与持久化状态机

- [ ] `MessageCreate.mode` 增加 `relay`；新增 `RelayRun` 持久化记录（轮次、阶段
  `implementing→syncing→reviewing→fixing`、状态、每轮 verdict、错误）；
  崩溃重启后恢复到明确的可续/已中断状态，不自动重放副作用（对齐 ExecutionJob 语义）。
- **验收**：状态机单元测试覆盖正常流转、每阶段失败、重启恢复。

### R2-2 发现 → 修复指令编译

- [ ] `verdict=blocking` 时把 findings 结构化编译为 Codex 下一轮提示词（按 severity
  排序、带文件定位与建议），并写入任务契约「已知问题」；`verdict=pass` 时生成
  收官系统消息汇总各轮结论。
- [ ] Claude 的 findings 同时作为下轮审查的「上轮遗留」输入，避免重复报告已修复项。
- **验收**：编译器单元测试；契约写入有 API 测试。

### R2-3 挂起、介入与上限

- [ ] Codex 轮内出现任何审批请求 → 循环挂起，审批卡照常进入消息流，用户处理后循环续跑。
- [ ] 用户可随时停止接力（复用现有取消链路），停止后状态落在明确轮次边界。
- [ ] 轮次达到上限仍 blocking → 停在「等待用户裁决」，展示未解决 findings。
- **验收**：挂起/续跑/停止/上限四条路径均有测试。

## Phase R3：上下文增强与 UI

### R3-1 交接携带决策上下文

- [ ] Codex 轮结束后从活动时间线提取本轮决策要点（执行过的关键命令、失败重试、
  文件级变更意图）附入审查输入；体量计入现有 20k 契约预算并截断标注。
- **验收**：提取纯函数单元测试；超预算截断测试。

### R3-2 接力进度卡

- [ ] 消息流内新增接力进度卡：轮次时间线（实现→同步→审查→修复）、当前阶段动效、
  每轮 verdict 徽标、可展开 findings 列表、停止按钮；复用 A3/A6 的行样式与 token。
- **验收**：组件测试覆盖各阶段渲染、findings 展开、停止交互。

---

## 验证命令

沿用 `docs/REMEDIATION_BACKLOG.md` 速查；R0/R1 的 Git 集成测试允许使用本地裸仓库
模拟 VPS 端，真实 VPS 验收在每 Phase review 时由 Claude 在 Linux 侧补验。

## 执行中新发现

（执行者在此追加，不要直接修改上文条目范围）

## Review 记录

### T1 Review（2026-07-19，Claude）

**结论：有条件通过。核心实现正确，修复下方 T1-R1 后 T1 关闭；T2 可并行开始。**

逐项核查（Linux 独立复验）：

- 事件语义对齐 ✓：thinking 块产出 `TOOL_EVENT(event="delta", item={id, type:"reasoning", text})`，
  与 Codex `activity.delta` 的输出形状逐字段一致（`codex_app_server.py:224`）；经 scheduler
  统一转发后命中前端 `toolStep` 的 reasoning 分支（`store.ts:145`），A2 思考态 UI 复用成立。
- 脱敏 ✓：`redacted_thinking` 输出固定文本「[Claude redacted thinking omitted]」进终端诊断，
  `data` 不读取；`thinking` 块仅取 `thinking` 字段，`signature` 不泄漏。测试显式断言
  secret 不在输出中。
- 双路径锁定 ✓：`test_cli_adapters.py` 与 `test_ssh_adapter.py` 均补 thinking → reasoning
  事件断言；新增协议专项 `test_claude_stream.py` 7 项覆盖规格要求的三类输入。
- 验证 ✓：本地后端 116 项、Ruff 通过；CI run `29667473202` 双平台绿（含前端 75 项、
  TypeScript、严格 ESLint）。真实 VPS 会话目视效果按验证结果所记留待安装包验收。

**返工项（归属 T1）：**

- **T1-R1｜reasoning 回退 ID 跨消息碰撞，思考段会被无分隔拼接。** thinking 块无原生
  `id`，回退值 `claude-reasoning-{block_index}` 只含块下标；VPS 路径默认开启工具
  （`ssh_adapter.py:142` `--tools Read`），一轮含多条 assistant 消息是常态，每条消息的
  首个 thinking 块都会得到相同的 `claude-reasoning-0`。而 store 对 `event="delta"` 的
  同 ID 步骤是直接字符串拼接（`store.ts` 归并分支），后到的思考段会无分隔地拼进
  先前条目并破坏活动时间线顺序。修复：`ClaudeStreamParser` 维护 assistant 消息序号，
  回退 ID 改为 `claude-reasoning-{message_seq}-{block_index}`；补一条「两条 assistant
  消息各含 thinking → 产出两个不同 ID」的协议测试。注意 T2 的 partial 去重方案需
  沿用同一 ID 语义，避免返工两次。
