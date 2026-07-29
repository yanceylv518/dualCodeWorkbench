# Codex × Claude 智能协作实施方案

> 状态：待实施  
> 目标版本：DualCode Workbench 0.2.x  
> 依赖文档：`ARCHITECTURE.md`、`AGENT_ADAPTERS.md`、`RELAY_LOOP_BACKLOG.md`  
> 核心目标：用户只描述目标，由工作台自动组织 Codex 与 Claude 协作；用户从人工信使和
> Agent 选择者变为关键决策者。

## 1. 产品目标与非目标

### 1.1 产品目标

1. 默认提供“智能协作”入口，不要求用户判断应该选择 Codex 还是 Claude。
2. Codex 与 Claude 共享经过验证的项目事实、任务状态、决策、风险、变更和验证证据。
3. Codex 负责本地实现、测试和构建；Claude 负责需求澄清、方案审查和独立复核。
4. 自动完成“澄清 → 实现 → 审查 → 整改 → 复验”的有限闭环。
5. 每次交接都关联精确 Git 基线、变更范围和执行证据，可审计、可恢复、可停止。
6. 简单任务只调用一个 Agent，不为协作而协作。

### 1.2 非目标

- 不让两个模型自由、无限地互相聊天。
- 不让 Claude 直接修改本地工作区。
- 不把本地项目通过 SFTP 完整上传到 VPS。
- 不自动修改用户分支、合并代码、提交或推送正式远端。
- 不共享或展示模型隐藏思维、协议签名和凭据。
- 不以长聊天记录替代结构化项目记忆。

## 2. 总体架构

```text
用户目标
   │
   ▼
CollaborationOrchestrator（确定性编排器）
   ├── TaskClassifier（任务分类与复杂度评估）
   ├── PolicyEngine（审批、安全、预算、停止条件）
   ├── MemoryService（共享项目与任务记忆）
   ├── EvidenceService（Git、测试、构建与文件证据）
   └── RelayService（影子快照与 VPS 隔离审查）
           │
           ├── Codex app-server：本地实现与验证
           └── Claude SSH CLI：远端规划与审查
```

编排器是业务状态机，不是第三个自由决策模型。第一版路由必须由可测试的确定性规则驱动；
模型只能产出建议，不能自行扩大权限、改变流程或跳过门禁。

## 3. 共享上下文记忆

### 3.1 四层记忆

| 层级 | 生命周期 | 内容 | 写入规则 |
|---|---|---|---|
| 项目记忆 | 工作区长期 | 产品目标、架构决策、项目规则、关键模块、技术债 | 用户确认或仓库证据 |
| 任务记忆 | 单个任务 | 目标、范围、验收、进度、阻塞、风险、下一步 | 编排器归并，保留来源 |
| Agent 私有会话 | Agent session | 原生多轮上下文、工具状态 | Codex/Claude 各自维护 |
| 交接包 | 单次阶段切换 | 基线、变更、证据、遗留问题、接收方职责 | 阶段结束自动生成 |

### 3.2 事实可信度

共享记忆的每个事实必须包含：

```json
{
  "kind": "requirement|decision|repository|evidence|risk|assumption",
  "content": "必须支持重启后恢复协作阶段",
  "source": "user|git|test|codex|claude|system",
  "confidence": "confirmed|verified|unverified|stale",
  "workspace_id": "...",
  "thread_id": "...",
  "commit_sha": "optional",
  "created_at": "...",
  "supersedes": "optional fact id"
}
```

可信度顺序为：用户确认 > 仓库事实 > 执行证据 > Agent 结论 > 推测。Agent 结论不能静默
覆盖用户确认或仓库事实；与当前 commit 冲突的事实自动标记为 `stale`。

### 3.3 上下文组装

每次调用 Agent 时按以下顺序组装，并受字符预算约束：

1. 当前任务目标、非目标和验收标准。
2. 与当前阶段相关的项目规则和架构决策。
3. 当前 Git 基线、本地/VPS commit 与工作区状态。
4. 最近一次交接包和未关闭问题。
5. 与本轮相关的最近对话，不发送完整历史。
6. 附件、Diff 和测试证据的显式引用。

超预算时先丢弃低可信度推测和旧对话，再压缩历史证据；用户确认的需求、未关闭阻塞和安全规则
不得被截断。

## 4. Agent 职责与自动路由

### 4.1 默认职责

**Codex**

- 本地仓库读取、修改、测试、构建和故障诊断。
- 产出文件变更、命令退出码、Diff 和构建产物等真实证据。
- 指出未覆盖场景、潜在回归和架构影响。

**Claude**

- 需求澄清、场景访谈、方案设计和独立审查。
- 对照验收标准检查遗漏、部分实现、回归、架构违规和证据缺口。
- 默认只读，不直接修改本地或 VPS 主工作区。

### 4.2 路由矩阵

| 请求类别 | 主 Agent | 协作者 | 默认流程 |
|---|---|---|---|
| 简单问答、解释 | 最匹配单 Agent | 无 | 直接完成 |
| 小型样式或单点修复 | Codex | 按风险决定 | 实现 → 验证 |
| 普通功能开发 | Codex | Claude | 实现 → 审查 → 必要整改 |
| 需求不清或产品设计 | Claude | Codex 可实现性复核 | 澄清 → 确认 → 实现 |
| 架构迁移 | Claude | Codex | 方案 → 可行性 → 实现 → 审查 |
| Bug 与故障恢复 | Codex | Claude | 根因 → 修复 → 回归审查 |
| 安全、高风险、数据迁移 | Claude 先审 | Codex 后执行 | 风险审查 → 用户批准 → 实现 |
| 测试、构建、打包 | Codex | Claude 可验收 | 执行 → 证据归档 |

### 4.3 复杂度判定

以下任一条件触发双 Agent：

- 修改跨越两个以上领域或五个以上文件。
- 涉及数据库迁移、权限、安全、Git 副作用或远程执行。
- 修改架构边界、公共协议或交付规则。
- 用户明确要求方案审查、专业交付或完整产品实现。
- 同类问题已返工一次。

只涉及解释、只读查询、文案或低风险局部样式时默认单 Agent。

## 5. 协作状态机

### 5.1 状态

```text
DRAFT
  → CLARIFYING
  → READY
  → IMPLEMENTING
  → VERIFYING
  → SYNCING_REVIEW_SNAPSHOT
  → REVIEWING
  ├── ACCEPTED → COMPLETED
  ├── CHANGES_REQUESTED → FIXING → VERIFYING
  ├── WAITING_APPROVAL
  ├── WAITING_USER
  ├── BLOCKED
  └── CANCELLED
```

### 5.2 阶段门禁

- `READY`：目标、范围和验收标准完整。
- `IMPLEMENTING`：编排器已选定 Codex，工作区可信且无冲突运行。
- `VERIFYING`：本轮产生了可验证变更；执行与变更类型匹配的测试。
- `REVIEWING`：影子快照已生成，Claude 拿到精确 `base_sha..snapshot_sha`。
- `ACCEPTED`：无 blocking finding，且必要测试证据完整。
- `COMPLETED`：只表示任务目标完成；不代表已正式 commit/push。

### 5.3 自动停止条件

1. Claude 审查通过且验收证据完整。
2. 同一任务自动整改最多两轮，默认总协作轮次最多三轮。
3. 两个 Agent 对需求或架构产生冲突，转 `WAITING_USER`。
4. 需要扩大范围、删除数据、安装、联网、正式 Git 写操作或改变技术框架时等待审批。
5. 连续两轮没有产生新 Diff、新证据或 finding 状态变化时停止。
6. 达到用户配置的时间、调用次数或成本预算时停止。
7. Agent 失活、VPS 不可达或凭据失效时进入可恢复失败，不无限重试。

## 6. 结构化交接协议

交接包扩展现有 `HandoffPackage`，版本化为 `handoff.v2`：

```json
{
  "schema": "handoff.v2",
  "purpose": "implement|review|fix|verify",
  "sender": "codex",
  "recipient": "claude",
  "task": {
    "goal": "...",
    "non_goals": [],
    "acceptance": [],
    "constraints": []
  },
  "repository": {
    "base_sha": "...",
    "snapshot_sha": "...",
    "branch": "main",
    "changed_files": [],
    "diff_stats": {}
  },
  "claims": [],
  "evidence": [
    {"type": "test", "command": "...", "exit_code": 0, "summary": "..."}
  ],
  "open_findings": [],
  "risks": [],
  "requested_action": "审查实现和验证证据"
}
```

Claude 的机器可读裁决：

```json
{
  "schema": "review.v1",
  "verdict": "pass|blocking|needs_user",
  "summary": "...",
  "findings": [
    {
      "id": "...",
      "type": "missing|partial|regression|risk|architecture|evidence",
      "severity": "blocking|advisory",
      "file": "optional",
      "line": "optional",
      "description": "...",
      "acceptance": "如何证明已修复"
    }
  ]
}
```

解析失败不得猜测裁决，必须保留原文并进入 `WAITING_USER`。

## 7. Git 与 VPS 交接

沿用 `RELAY_LOOP_BACKLOG.md` R0/R1 的影子同步设计：

1. 使用临时 index 和 `git commit-tree` 生成工作区快照，不改变用户 HEAD、index 和工作树。
2. 凭据防护在快照前执行，敏感文件不进入快照。
3. 只更新专用 `refs/dualcode/relay/<workspace>/<thread>`，不更新用户分支。
4. VPS 使用隔离 `git worktree` 检出快照，Claude 在其中只读审查。
5. 任务结束清理影子 ref 与临时 worktree。
6. 正式 commit、push、pull、merge 继续走应用审批；智能协作无权绕过。

## 8. 数据模型与迁移

### 8.1 新增表

**collaboration_runs**

- `id`、`workspace_id`、`thread_id`
- `mode`：`smart|codex_only|claude_only`
- `state`、`current_agent`、`round`
- `max_rounds`、`budget_json`
- `base_sha`、`snapshot_sha`
- `error`、`created_at`、`updated_at`、`completed_at`

**memory_facts**

- `id`、`workspace_id`、`thread_id`（项目事实可为空）
- `kind`、`content_json`
- `source`、`confidence`
- `commit_sha`、`supersedes_id`
- `created_at`、`invalidated_at`

**review_findings**

- `id`、`collaboration_run_id`、`round`
- `type`、`severity`、`status`
- `file`、`line`、`description`、`acceptance`
- `source_handoff_id`、`resolved_by_snapshot_sha`

### 8.2 复用现有表

- `TaskContract`：任务目标、范围、验收和风险的权威入口。
- `HandoffPackage`：升级 payload schema，不另建重复交接表。
- `AgentSession` / `AgentRun`：保留 Agent 私有会话和每轮执行。
- `ExecutionJob`：承载可恢复副作用，不把副作用塞进编排状态机事务。
- `Approval` / `AuditLog`：继续作为授权与审计事实来源。
- `FileChange` / `TestRun`：作为 EvidenceService 的底层证据。

每个迁移必须通过 Alembic；禁止运行时手写补字段。

## 9. 后端服务与接口

### 9.1 新服务

- `CollaborationOrchestrator`：状态机推进、阶段门禁、暂停与恢复。
- `TaskClassifier`：确定性路由和复杂度判定。
- `MemoryService`：事实写入、冲突检测、失效和上下文组装。
- `HandoffCompiler`：生成 `handoff.v2` 与 Codex 整改提示。
- `ReviewParser`：解析并校验 `review.v1`。
- `EvidenceService`：归并 Git、FileChange、TestRun、构建和命令证据。
- `RelayService`：影子快照、ref 推送、VPS worktree 生命周期。

### 9.2 API

```text
POST   /api/workspaces/{workspace_id}/threads/{thread_id}/collaboration-runs
GET    /api/workspaces/{workspace_id}/threads/{thread_id}/collaboration-runs/current
GET    /api/workspaces/{workspace_id}/threads/{thread_id}/memory
GET    /api/workspaces/{workspace_id}/threads/{thread_id}/handoffs
POST   /api/collaboration-runs/{id}/pause
POST   /api/collaboration-runs/{id}/resume
POST   /api/collaboration-runs/{id}/cancel
POST   /api/collaboration-runs/{id}/decisions
GET    /api/collaboration-runs/{id}/findings
```

创建请求只接收用户目标和可选模式；前端不能直接指定内部状态跃迁。
`/api/collaboration-runs/{id}/...` 操作必须通过运行记录反查并校验当前 workspace/thread
归属，继续使用现有 sidecar token 鉴权与工作区访问边界，不允许凭 run id 跨工作区访问。

### 9.3 WebSocket 事件

```text
collaboration.started
collaboration.stage_changed
collaboration.agent_changed
collaboration.handoff_prepared
collaboration.review_completed
collaboration.findings_updated
collaboration.waiting_user
collaboration.completed
collaboration.failed
```

事件只携带摘要和 ID；原始 Agent 输出继续进入现有终端/日志通道。

## 10. 前端体验

### 10.1 发送入口

默认模式改为：

```text
智能协作
```

高级菜单保留“仅 Codex”“仅 Claude”，但不作为普通用户的首要选择。发送后输入框展示当前阶段，
而不是要求用户手工切换 Agent。

### 10.2 统一协作时间线

消息流只显示一套过程区：

```text
✓ 已澄清需求          Claude
✓ 已完成实现          Codex
✓ 测试通过            78 项
● 正在审查            Claude
○ 等待整改
```

展开后查看每个 Agent 的活动摘要、交接包、findings 和验证证据；原始命令进入运行日志。

### 10.3 用户只在关键节点介入

- 需求或架构存在冲突。
- 需要扩大范围或改变验收标准。
- 安全审批和正式 Git 副作用。
- 达到返工上限或预算。
- Agent 无法得出可靠结论。

不为正常的 Codex → Claude 切换弹窗，也不让用户复制提示词。

## 11. 安全、审批与审计

1. 智能协作不能扩大 Codex/Claude 当前权限配置。
2. 只读分类、记忆读取和状态推进无需审批。
3. 删除、安装、联网、正式 Git 写操作和非工作区写入继续审批。
4. 影子同步首次按任务审批，后续仅复用该任务 scope。
5. 所有状态跃迁、路由决定、记忆变更、交接、裁决和停止原因写入审计。
6. 审计记录禁止包含凭据、完整 prompt、隐藏思维和附件二进制。
7. Claude VPS 仍强制 known_hosts、参数化 SSH 和隔离工作目录。

## 12. 实施阶段

### Phase C0：规格冻结与观测基础

- 冻结 `handoff.v2`、`review.v1`、状态机和路由矩阵。
- 为现有 AgentRun、Handoff、TestRun、FileChange 建立统一 evidence 投影。
- 增加状态跃迁和路由审计，不改变现有 UI。
- **验收**：schema 契约测试；现有 Codex/Claude 单模式零回归。

### Phase C1：共享任务记忆

- 新增 `memory_facts` 与 `MemoryService`。
- 从 TaskContract、Git、测试和审计生成带来源的事实。
- 上下文组装按预算注入两个 Agent。
- **验收**：同任务切换 Agent 后能回答已确认目标、当前 commit、未关闭风险；陈旧事实可失效。

### Phase C2：结构化交接与审查

- 升级 HandoffPackage，完成 ReviewParser 和 findings 持久化。
- Claude 仅针对交接快照审查，返回机器可读裁决。
- **验收**：正常、缺字段、非法 JSON、无 JSON 均有确定行为。

### Phase C3：确定性智能路由

- 新增 `smart` 模式、TaskClassifier 和路由原因展示。
- 简单任务保持单 Agent；复杂任务自动创建审查阶段。
- **验收**：路由矩阵表驱动测试；相同输入和状态得到相同路由。

### Phase C4：影子 Git 与 VPS 隔离审查

- 实施 `RELAY_LOOP_BACKLOG.md` R0-1、R0-2、R0-3、R1-1；R1-2 已由 C2
  `review.v1` 取代，不再实施。
- 本地脏工作树快照、专用 ref、VPS worktree 和清理闭环。
- **验收**：用户 HEAD/index/worktree 零变化；VPS 主仓状态零变化；敏感文件被排除。

### Phase C5：自动整改循环

- 实现完整 CollaborationOrchestrator 状态机。
- blocking findings 自动编译给 Codex；修复后重新验证和审查。
- 加入轮次、无进展、预算和审批暂停条件。
- **验收**：通过、一次整改、达到上限、等待审批、用户取消、重启恢复六条 E2E。

### Phase C6：统一协作 UI 与产品化验收

- 智能协作设为默认入口；加入统一阶段时间线、findings 和证据视图。
- 完成安装包、真实 Codex、真实 VPS Claude、网络分区和恢复验收。
- **验收**：用户无需手选 Agent 完成一项真实跨文件功能开发和独立审查。

## 13. 测试策略

### 单元测试

- 路由矩阵、状态机、停止条件、上下文预算、事实冲突。
- Handoff 编译、Review 解析、finding 生命周期。
- 影子 ref 名称、凭据排除、提示词边界。

### 集成测试

- SQLite 迁移与重启恢复。
- 本地裸仓模拟 VPS 的快照推送和隔离 worktree。
- 审批暂停后恢复，不重复执行副作用。
- Codex/Claude adapter 失败、超时和会话失效降级。

### 端到端测试

1. 简单修复只调用 Codex。
2. 普通功能由 Codex 实现、Claude 通过。
3. Claude 提出 blocking finding，Codex 修复后通过。
4. 两轮无进展进入用户裁决。
5. 应用重启后恢复协作阶段。
6. VPS 断网、凭据失效和影子 ref 冲突得到明确反馈。

## 14. 发布与回滚

- 使用 `smart_collaboration_enabled` 功能开关，首版默认关闭，仅验收工作区开启。
- 保留“仅 Codex”“仅 Claude”作为故障降级路径。
- 数据库迁移只新增表和字段，不破坏旧单 Agent 会话。
- 任一阶段失败时可关闭智能协作，已有消息、Agent session 和 Git 工作区保持可用。
- C5 完成前不把智能协作设为默认；C6 真实验收通过后再切换默认入口。

## 15. 完成定义

只有同时满足以下条件，智能协作才可标记为可交付：

1. 用户仅输入目标即可完成至少一个真实跨文件开发任务。
2. Codex 和 Claude 均获得同一任务的结构化共享上下文。
3. Claude 审查的是精确 Codex 快照，不是陈旧 VPS 工作区。
4. 自动整改循环有明确轮次上限、停止原因和恢复路径。
5. 所有结论能追溯到用户确认、Git、测试或 Agent 裁决。
6. 未发生静默 commit、push、merge、删除、安装或权限扩大。
7. 应用重启、Agent 失活和 VPS 断连均不会丢失任务状态或重复副作用。
8. Windows/Linux CI、真实 Codex、真实 VPS Claude 和安装包验收全部通过。

## 16. 建议立即执行的第一项

从 **C0-1：冻结协议与状态机** 开始，不直接编写自动循环。先把 `handoff.v2`、
`review.v1`、状态跃迁表和路由矩阵固化为后端类型与契约测试；完成后停下进行独立 review，
再进入共享记忆实现。

---

## Review 记录

### 方案验收（2026-07-28，Claude）

**结论：有条件通过。方案方向、分阶段拆分和安全边界成立；修复 C-R1、C-R2 后方可进入 C0 实施。**

逐项核查（对照仓库事实）：

- 复用声明属实 ✓：`TaskContract`、`HandoffPackage`、`AgentSession`、`AgentRun`、
  `ExecutionJob`、`Approval`、`AuditLog`、`FileChange`、`TestRun` 全部存在于
  `models.py:98-219`；`HandoffPackage.payload` 为 JSON 文本字段，升级 `handoff.v2`
  无需另建表。Alembic 已接管 schema（P3-1），迁移约束可执行。
- 上下文预算机制已有落点 ✓：`context_budget.py` 已实现 60k 对话 / 20k 契约预算与
  截断标记（P3-4），§3.3 的组装顺序与"安全规则不得截断"为增量约束，非重建。
- 影子 Git 设计一致 ✓：§7 与 `RELAY_LOOP_BACKLOG.md` 保护条款 1-5 逐条对应
  （专用 ref、凭据防护先行、VPS 隔离 worktree、清理、正式 Git 走审批）。
- 安全与审批边界 ✓：§11 未扩大任何现有权限；功能开关默认关闭、保留单 Agent
  降级路径（§14）符合现有发布纪律。
- 停止条件完备 ✓：轮次上限、无进展检测、冲突转人工、预算与失活降级（§5.3）
  覆盖 RELAY 保护条款 4 并有扩展。

**返工项（进入 C0 前必须关闭）：**

- **C-R1｜与 `RELAY_LOOP_BACKLOG.md` R 系列的规格归属未冻结，存在两套冲突协议。**
  方案自称"不引入另一套冲突的协作机制"，但事实上：
  1. R2-1 定义 `RelayRun` 表 + `MessageCreate.mode` 增加 `relay`；本方案 §8.1 定义
     `collaboration_runs` + `mode: smart`——同一循环两套持久化与模式枚举。
  2. R1-2 的裁决 JSON（`verdict: pass|blocking`、中文 finding type、`suggestion` 字段）
     与 §6 `review.v1`（新增 `needs_user`、英文 type、`severity`+`acceptance`）字段级不兼容；
     而 C4 写"实施 R0、R1"，字面包含 R1-2，将与 C2 冻结的 `review.v1` 直接冲突。
  修复：在两份文档中显式声明取代关系（建议：R0-1/R0-2/R0-3/R1-1 由 C4 原样实施；
  R1-2 由 C2 `review.v1` 取代；R2-1/R2-2/R2-3 由 C5 取代；R3-1/R3-2 由 C6 取代），
  执行者只面对一份权威规格。此项不关闭，C0"规格冻结"无法成立。
- **C-R2｜上一轮 review 返工项 T1-R1 仍未关闭。** `claude_stream.py:65` 仍为
  `claude-reasoning-{block_index}` 回退 ID，`test_claude_stream.py:111` 仍断言
  `claude-reasoning-0`，无任何提交处理 T1-R1。按执行约定，先前 review 的返工项须在
  进入新阶段前关闭；且本方案 C6 的统一协作时间线直接依赖活动时间线正确性。
  应将 T1-R1（含其对 T2 partial 去重 ID 语义的约束）列为 C0 前置项。

**建议项（不阻塞，实施对应阶段时处理）：**

- §9.2 API 路径与现有实现不一致：现有交接路由为
  `/workspaces/{workspace_id}/threads/{thread_id}/handoffs`（`api_collaboration.py:99`），
  方案写 `/threads/{id}/...`。应统一为带 workspace 前缀的现有形态，避免双路由并存。
- §5.1 新状态机与现有 `RunState`（`state_machine.py`，`AgentRun` 在用）关系未说明；
  两者存在同名状态（`IMPLEMENTING`、`REVIEWING`、`CANCELLED`）。C0 冻结时应明确
  `collaboration_runs.state` 为独立枚举及其与每轮 `AgentRun.state` 的层级关系。
- §4.3 复杂度判定含"五个以上文件"，但文件数只能在实现后得知。C3 需明确判定时机：
  事前按请求分类路由，实现后按实际 Diff 升级为需审查——否则"相同输入得到相同路由"
  的表驱动测试无法定义。

---

## 返工项执行清单（交 Codex，进入 C0 前完成）

> 执行约定沿用 `docs/REMEDIATION_BACKLOG.md` 全部规则：一条目一 commit、附测试、
> 禁止顺手重构、全量验证、完成后勾选并填写验证结果，全部完成后停下等 Claude review。

### C-R1 冻结 R 系列与 C 系列的规格归属（纯文档修订）

- [x] `docs/RELAY_LOOP_BACKLOG.md`：为下列条目逐条标注取代关系，保留原文供追溯，
  条目标题后加「（已由 `SMART_COLLABORATION_IMPLEMENTATION_PLAN.md` 取代）」并注明对应阶段：
  - R1-2 机器可读裁决 → 由 **C2 `review.v1`** 取代（`needs_user` 裁决、英文 finding type、
    `severity` + `acceptance` 字段为准；R1-2 的中文 type 与 `suggestion` 字段作废）。
  - R2-1 relay 执行模式与 `RelayRun` → 由 **C5 `collaboration_runs`** 取代
    （`MessageCreate.mode` 不新增 `relay`，智能协作模式统一为 `smart`）。
  - R2-2 发现 → 修复指令编译 → 由 **C5 HandoffCompiler** 取代。
  - R2-3 挂起、介入与上限 → 由 **C5 停止条件（方案 §5.3）** 取代。
  - R3-1 交接携带决策上下文 → 由 **C1 MemoryService + C2 handoff.v2** 取代。
  - R3-2 接力进度卡 → 由 **C6 统一协作时间线** 取代。
  - R0-1、R0-2、R0-3、R1-1 保持有效，由 **C4** 原样实施；T2、T3 保持有效，独立于 C 系列。
- [x] 本方案 §12 Phase C4 的「实施 `RELAY_LOOP_BACKLOG.md` R0、R1」改为
  「实施 R0-1、R0-2、R0-3、R1-1（R1-2 已由 C2 取代）」。
- [x] 本方案 §9.2 API 路径统一为现有形态：
  `/api/workspaces/{workspace_id}/threads/{thread_id}/...`（对照 `api_collaboration.py:99`），
  `collaboration-runs` 子资源操作挂 `/api/collaboration-runs/{id}/...` 需同步说明鉴权与归属校验。
- **为什么**：两份文档当前对同一循环存在两套持久化（`RelayRun` vs `collaboration_runs`）、
  两套模式枚举（`relay` vs `smart`）和两套裁决 schema，执行者无法确定权威规格，
  C0「规格冻结」无法成立。
- **验收**：两份文档交叉引用一致；全文搜索不再存在未标注取代关系的冲突条目；
  `RelayRun`、`mode=relay` 仅出现在已标注作废的原文中。
- **验证结果（2026-07-29）**：R0-1/R0-2/R0-3/R1-1 明确保留并归属 C4；
  R1-2、R2-1/R2-2/R2-3、R3-1/R3-2 均已逐条标记取代关系；C4 范围和 API
  路径已同步修正，`RelayRun` 与 `mode=relay` 只保留在明确作废的追溯原文中。

### C-R2 关闭 T1-R1：reasoning 回退 ID 跨消息碰撞（代码 + 测试）

- [x] `apps/backend/dualcode/claude_stream.py`：`ClaudeStreamParser` 维护 assistant
  消息序号（每收到一条 `type=assistant` 消息自增），thinking 块回退 ID 由
  `claude-reasoning-{block_index}` 改为 `claude-reasoning-{message_seq}-{block_index}`；
  块自带 `id` 时仍优先使用原生 id。
- [x] `apps/backend/tests/test_claude_stream.py`：更新现有断言（`:111` 的
  `claude-reasoning-0`），并新增协议测试：「两条 assistant 消息各含一个 thinking 块 →
  产出两个不同的 reasoning ID」，断言两个 ID 互不相等且各自稳定。
- [x] 本地 CLI 与 VPS SSH 两条路径共用该解析器，`test_cli_adapters.py` /
  `test_ssh_adapter.py` 中如有引用旧 ID 的断言一并更新。
- [x] 完成后在 `docs/RELAY_LOOP_BACKLOG.md` T1 Review 记录的 T1-R1 条目下补验证结果。
- **为什么**：VPS 路径默认开启工具，一轮多条 assistant 消息是常态，每条消息首个
  thinking 块共享 `claude-reasoning-0`，前端 store 对同 ID `delta` 直接字符串拼接，
  思考段会无分隔拼接并破坏活动时间线顺序（详见 T1 Review）。
- **约束**：T2 的 partial 事件去重必须沿用同一 ID 语义（`message_seq` 维度），
  实施 T2 时不得再次变更 ID 形态。
- **验收**：后端全量 pytest 与 Ruff 通过；协议测试覆盖跨消息 ID 唯一性。

### C-R3 修复 `test_cli_adapters` 夹具与 tool_use 归并逻辑的失配（测试修正）

- [x] `apps/backend/tests/test_cli_adapters.py::test_claude_exposes_normalized_stream_events`：
  协议夹具的 `tool_use` 块补上 `id` 字段（如 `"id":"toolu-1"`），对齐真实 Claude
  stream-json 协议（`tool_use` 必含 `id`）；断言可同步补一条
  `events[2].item["id"] == "toolu-1"`，锁定归并所需的 ID 透传。
- [x] 不修改 `claude_stream.py` 的「无 `id`/`name` 即跳过」解析逻辑；如认为需要
  无 ID 降级路径，先在本条目下写明方案并等 review，再动手。
- **为什么**：`a1d618d` 为 `tool_use`/`tool_result` 按 ID 归并引入跳过逻辑时未同步
  更新该测试，夹具缺 `id` 导致事件被丢弃、四事件序列断言失败；当时 Windows 全量
  pytest 受既有 ACL 故障阻塞未能发现，Linux 全量复验暴露（123 通过 / 1 失败，
  详见下方返工复验记录）。
- **验收**：Linux 全量 pytest 全绿；Ruff 通过；GitHub Actions 双平台 CI 绿。
- **验证结果（2026-07-29）**：测试夹具已补真实协议必填的 `tool_use.id`，并增加
  ID 透传断言；未修改 `claude_stream.py`。本地后端全量 124 项、Ruff 与桌面端
  TypeScript 检查通过，GitHub Actions 双平台结果待本提交推送后确认。

### C-R4 恢复 CI 双平台绿：钉住 Ruff 版本（工具链修复）

- [x] `apps/backend/pyproject.toml`：dev 依赖 `ruff>=0.9,<1` 收紧为与仓库当前验证
  基线一致的 `ruff>=0.15,<0.16`；CI 与本地从同一约束安装，消除静态检查版本漂移。
- [x] 升级到 Ruff 0.16 作为独立后续任务另行排期：届时运行 `ruff check --fix` 处理
  92 项中可自动修复的 37 项、逐条评审其余项，并在同一 commit 内更新版本约束；
  本条目不做该升级，禁止顺手改动任何被 0.16 新规则命中的代码。
- **为什么**：CI 通过 `pip install -e "./apps/backend[dev]"` 安装 Ruff，未钉上界的
  `>=0.9,<1` 在 0.16.0 发布后自动升级，新版本收紧导入分组等规则报出 92 个错误
  （本地为 0.15.21，全绿）。双平台 CI 自 2026-07-27 起全红（最后一次绿为 7-19 的
  `1068cd5`），期间所有提交声明的「由 CI 补验」实际均未生效；该阻塞与 C-R3 的
  测试修正无关，但挡住其「CI 双平台绿」验收标准，也会挡住 C0 起所有阶段验收。
- **验收**：GitHub Actions 双平台绿（该次运行需完整跑过 ruff、双端测试与构建步骤，
  不允许仅 ruff 步骤通过）；本地 `ruff --version` 与 CI 安装版本同为 0.15.x。
- **验证结果（2026-07-29）**：dev 依赖已钉为 `ruff>=0.15,<0.16`，本地使用
  Ruff 0.15.21；未修改任何被 Ruff 0.16 新规则命中的代码。后端 124 项、
  TypeScript、ESLint 和前端 78 项通过；Windows 工作树 Prettier 检查受既有 CRLF
  差异影响，GitHub Actions 双平台结果在本提交推送后确认。
  首次 CI run `30413166268` 确认 Windows/Ubuntu 均安装 Ruff 0.15.22，且 Ruff、
  后端 124 项、TypeScript、ESLint 全绿；随后暴露 `App.tsx`、`App.test.tsx`、
  `store.ts` 三个既有 Prettier 漂移，已按 CI 输出机械格式化，未改变业务逻辑。
  CI run `30413530551` 最终确认 Windows/Ubuntu 全步骤通过，C-R3 与 C-R4 正式关闭。

### 返工复验（2026-07-29，Claude）

**结论：C-R1、C-R2 均关闭，可进入 C0-1。全量复验暴露一项既有回归，立为 C-R3。**

- C-R1 ✓：R1-2/R2-1/R2-2/R2-3/R3-1/R3-2 已逐条标注取代关系并保留原文；R0 系列与
  R1-1 归属 C4、T2/T3 独立有效的声明就位；§12 C4 措辞与 §9.2 API 路径（含
  `/api/collaboration-runs/{id}/...` 反查归属校验说明）均已修正。全文残留的
  `RelayRun`、`mode=relay` 仅出现在 review 记录与已作废原文中，验收标准满足。
- C-R2 ✓：`ClaudeStreamParser` 新增 `assistant_message_seq`，仅在 `assistant` 消息
  自增；回退 ID 为 `claude-reasoning-{seq}-{block_index}`，原生块 `id` 仍优先。
  新增跨消息唯一性与原生 ID 优先级两条协议测试，旧断言同步更新；
  `test_cli_adapters.py` / `test_ssh_adapter.py` 经查无旧 ID 断言，无需改动。
  独立复验：Claude stream 专项 10 项、Ruff 通过；T1-R1 验证结果已回填，T1 关闭。

**新发现（不归属本次返工，立为 C-R3）：**

- **C-R3｜`test_cli_adapters.py::test_claude_exposes_normalized_stream_events`
  在 Linux 全量 pytest 下失败（123 通过 / 1 失败）。** 根因：`a1d618d`
  （Claude 过程区对齐 Codex）为 `tool_use` 归并引入「无 `id` 或无 `name` 即跳过」
  逻辑，但未同步更新该测试——其夹具的 `tool_use` 块缺少 `id` 字段，事件被丢弃，
  断言的四事件序列缺第三项。当时验证仅跑了 Claude stream 专项 8 项（Windows 全量
  pytest 受既有 ACL 故障阻塞，声明由 CI 补验），故漏检。修复方向：真实 Claude
  stream-json 协议中 `tool_use` 块必含 `id`，应为测试夹具补上 `id` 字段以对齐
  真实协议（而非放宽解析器）；如执行者认为需要无 ID 降级路径，先在本条目下写明
  方案再动手。**验收**：Linux 全量 pytest 全绿；并确认 CI 双平台绿。

### C-R3 复验（2026-07-29，Claude）

**结论：代码修正通过；「CI 双平台绿」验收项被无关的既有 CI 阻塞卡住，立为 C-R4，
C-R3 待 C-R4 关闭后凭同一次绿色 CI 运行正式关闭。**

- 修正内容 ✓：`2f7cf49` 夹具补 `"id":"toolu-1"` 并新增 `events[2].item["id"]`
  透传断言，与清单规格逐字一致；`claude_stream.py` 未改动，边界约束遵守。
- 独立复验 ✓：Linux 全量 pytest 124 项全绿，Ruff（0.15.21）通过。
- CI ✗（无关阻塞）：双平台在 `ruff check` 步骤失败，根因是 dev 依赖未钉上界，
  CI 安装到新发布的 Ruff 0.16.0 触发 92 项新规则报错；纯文档提交同样失败，
  证实与 C-R3 改动无关。CI 自 7-27 起全红，详见 C-R4。
- **验证结果（2026-07-29）**：parser 已按 assistant 消息序号生成稳定回退 ID，并保留
  原生 block id 优先级；Claude stream 专项 10 项和 Ruff 通过。后端全量为 123
  通过、1 个既有失败：`test_claude_exposes_normalized_stream_events` 的旧夹具构造了
  无 `id` 的 `tool_use`，与本条 reasoning ID 修复无关，留待对应 adapter 条目处理。
