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

| 当前态 | 合法目标态 |
|---|---|
| DRAFT | CLARIFYING、READY、CANCELLED |
| CLARIFYING | READY、WAITING_USER、CANCELLED |
| READY | IMPLEMENTING、CANCELLED |
| IMPLEMENTING | VERIFYING、WAITING_APPROVAL、WAITING_USER、BLOCKED、CANCELLED |
| VERIFYING | SYNCING_REVIEW_SNAPSHOT、WAITING_APPROVAL、WAITING_USER、BLOCKED、CANCELLED |
| SYNCING_REVIEW_SNAPSHOT | REVIEWING、WAITING_APPROVAL、WAITING_USER、BLOCKED、CANCELLED |
| REVIEWING | ACCEPTED、CHANGES_REQUESTED、WAITING_APPROVAL、WAITING_USER、BLOCKED、CANCELLED |
| ACCEPTED | COMPLETED、CANCELLED |
| CHANGES_REQUESTED | FIXING、CANCELLED |
| FIXING | VERIFYING、WAITING_APPROVAL、WAITING_USER、BLOCKED、CANCELLED |
| WAITING_APPROVAL | IMPLEMENTING、VERIFYING、SYNCING_REVIEW_SNAPSHOT、REVIEWING、FIXING、CANCELLED |
| WAITING_USER | READY、FIXING、CANCELLED |
| BLOCKED | IMPLEMENTING、VERIFYING、SYNCING_REVIEW_SNAPSHOT、REVIEWING、FIXING、CANCELLED |
| COMPLETED | （终态） |
| CANCELLED | （终态） |

`DRAFT → READY` 直通仅当任务契约已满足 READY 门禁（对应 §1.1.6）。
`WAITING_APPROVAL`/`BLOCKED` 的出边表示回到挂起前状态；挂起前状态由未来
`collaboration_runs` 记录，本表只约束合法目标集合。`WAITING_USER` 的出边对应
用户三种裁决：调整范围后重入（READY）、直接整改（FIXING）、停止（CANCELLED）。
终态仅 `COMPLETED`/`CANCELLED`。

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

## C0 执行清单（交 Codex）

> 执行约定沿用 `docs/REMEDIATION_BACKLOG.md` 全部规则：一条目一 commit、附测试、
> 禁止顺手重构、全量验证、完成后勾选并填写验证结果。按 §16 约定，本阶段先只执行
> C0-1，完成后停下等 Claude review，不进入 C0-2/C0-3。

### C0-1 冻结协议与状态机（纯类型与契约测试，零行为变更）

- [x] 新增 `apps/backend/dualcode/collaboration_protocol.py`（或等价单一模块），
  用 Pydantic 固化四份规格，字段以本方案为唯一权威：
  1. `HandoffV2`：§6 交接包全部字段；`schema` 字段字面量锁定 `"handoff.v2"`，
     `purpose` 锁定 `implement|review|fix|verify` 枚举。
  2. `ReviewV1` 与 `ReviewFinding`：§6 裁决全部字段；`verdict` 锁定
     `pass|blocking|needs_user`，finding `type` 锁定六值英文枚举，`severity` 锁定
     `blocking|advisory`。禁止额外未知字段静默通过（`extra="forbid"`）。
  3. `CollaborationState` 枚举与跃迁表：§5.1 全部状态、§5.2 门禁对应的合法跃迁
     集合，形式对齐现有 `state_machine.py` 的 `TRANSITIONS` 字典 + `transition()`
     纯函数；非法跃迁抛出明确异常。
  4. 路由矩阵：§4.2 八行矩阵与 §4.3 复杂度条件固化为模块级数据结构
     （请求类别 → 主 Agent、协作者、流程），仅数据与查表函数，不实现分类器
     （TaskClassifier 属 C3）。
- [x] 在模块 docstring 或注释中写明与现有 `RunState` 的关系：`CollaborationState`
  是协作运行（未来 `collaboration_runs.state`）的独立枚举，描述跨 Agent 编排阶段；
  `RunState` 继续描述单次 AgentRun 生命周期，两者不合并、不互相转换（对应
  review 建议项二）。
- [x] 新增 `apps/backend/tests/test_collaboration_protocol.py` 契约测试，至少覆盖：
  合法 handoff.v2 / review.v1 样例 round-trip；缺必填字段、非法枚举值、未知字段
  均被拒绝；`schema` 字面量不匹配被拒绝；跃迁表全部合法路径可达 §5.1 终态、
  非法跃迁（含 `COMPLETED`/`CANCELLED` 出边）抛异常；路由矩阵查表对 §4.2 逐行
  断言、相同输入重复查表结果一致。
- [x] 零行为变更边界：不修改 scheduler、adapter、API、前端和数据库；不新增
  Alembic 迁移（`collaboration_runs` 等表属 C1/C5）；不实现解析器与编排器。
- **为什么**：C1-C6 的记忆、交接、路由和循环全部消费这四份规格；先以类型和契约
  测试冻结，后续阶段对规格的任何改动都会显式表现为本模块与测试的 diff，接受
  独立 review，避免实现过程中规格漂移。
- **验收**：新契约测试全部通过；后端全量 pytest、Ruff、桌面端 TypeScript 通过
  （前端应无 diff）；现有 Codex/Claude 单模式零回归；GitHub Actions 双平台绿。

### C0-1-R1 补全跃迁表挂起/裁决/失败/取消边（规格 + 代码 + 测试，一个 commit）

> 规格与代码是同一冻结单元，本条目 docs 与 backend 改动允许同一 commit。
> 以下跃迁表为权威规格；如需偏离，先在本条目下写明理由并等 review，再动手。

- [x] 方案 §5.1 重写为显式跃迁表（Markdown 表：当前态 → 合法目标态集合），
  放弃 ASCII 示意图作为规格载体，按以下定义：

  | 当前态 | 合法目标态 |
  |---|---|
  | DRAFT | CLARIFYING、READY、CANCELLED |
  | CLARIFYING | READY、WAITING_USER、CANCELLED |
  | READY | IMPLEMENTING、CANCELLED |
  | IMPLEMENTING | VERIFYING、WAITING_APPROVAL、WAITING_USER、BLOCKED、CANCELLED |
  | VERIFYING | SYNCING_REVIEW_SNAPSHOT、WAITING_APPROVAL、WAITING_USER、BLOCKED、CANCELLED |
  | SYNCING_REVIEW_SNAPSHOT | REVIEWING、WAITING_APPROVAL、WAITING_USER、BLOCKED、CANCELLED |
  | REVIEWING | ACCEPTED、CHANGES_REQUESTED、WAITING_APPROVAL、WAITING_USER、BLOCKED、CANCELLED |
  | ACCEPTED | COMPLETED、CANCELLED |
  | CHANGES_REQUESTED | FIXING、CANCELLED |
  | FIXING | VERIFYING、WAITING_APPROVAL、WAITING_USER、BLOCKED、CANCELLED |
  | WAITING_APPROVAL | IMPLEMENTING、VERIFYING、SYNCING_REVIEW_SNAPSHOT、REVIEWING、FIXING、CANCELLED |
  | WAITING_USER | READY、FIXING、CANCELLED |
  | BLOCKED | IMPLEMENTING、VERIFYING、SYNCING_REVIEW_SNAPSHOT、REVIEWING、FIXING、CANCELLED |
  | COMPLETED | （终态） |
  | CANCELLED | （终态） |

  配套语义随表写入 §5.1：`DRAFT → READY` 直通仅当任务契约已满足 READY 门禁
  （对应 §1.1.6）；`WAITING_APPROVAL`/`BLOCKED` 的出边语义是「回到挂起前状态」，
  挂起前状态由未来 `collaboration_runs` 记录，表只约束合法集合；`WAITING_USER`
  出边对应用户三种裁决：调整范围后重入（READY）、直接整改（FIXING）、停止
  （CANCELLED）；终态仅 `COMPLETED`/`CANCELLED`。
- [x] `collaboration_protocol.py` 的 `COLLABORATION_TRANSITIONS` 按新表逐边对齐；
  `transition()` 行为不变。
- [x] `test_collaboration_protocol.py`：非终态覆盖断言更新为对新表逐边断言
  （每个非终态列出完整目标集合，直接与上表对照）；保留全状态可达性测试与
  成功/整改路径测试；非法跃迁参数化补充挂起态误入终态（如
  `WAITING_APPROVAL → COMPLETED`）与终态出边用例。
- **为什么**：见 C0-1 Review 返工项 C0-1-R1——原表把挂起三态冻结为死胡同，
  审批与取消边覆盖不足，与 §5.3、§9.2、§11 矛盾；C5 编排器实现前必须以
  无矛盾的表为契约。
- **验收**：契约测试与新表逐边一致并全绿；后端全量 pytest、Ruff 通过；
  仍零运行时接线；GitHub Actions 双平台绿。

### C0-2 统一 evidence 投影（纯函数 + 契约测试，零行为变更）

- [x] 新增 `apps/backend/dualcode/evidence.py`：定义 `EvidenceItem`
  （继承/复用 `collaboration_protocol.StrictModel` 的严格配置）：
  - `kind: Literal["agent_run", "handoff", "test", "file_change"]`
  - `source_id`、`thread_id`：指回源记录，不复制大体量内容。
  - `summary: str`：单行人读摘要，超过 200 字符截断并以 `…` 结尾。
  - 按 kind 使用的可选字段：`command`/`exit_code`（test）、`path`（file_change）、
    `agent`/`status`（agent_run 与 handoff）；与 kind 不符的字段保持 `None`。
- [x] 同模块实现四个纯投影函数，输入为对应 ORM 行对象、输出 `EvidenceItem`，
  不做任何数据库查询或写入：
  - `from_test_run(TestRun)`：summary 形如 `"{command} → exit {exit_code}"`；
    `output` 不进入投影（凭 `source_id` 回查），只用于截断后的失败摘要可选补充。
  - `from_file_change(FileChange)`：summary 为路径；`diff` 全文禁止进入投影。
  - `from_agent_run(AgentRun)`：summary 含 agent 与终态；`output`/`before_diff`/
    `after_diff` 全文禁止进入投影。
  - `from_handoff(HandoffPackage)`：summary 含 recipient、purpose 与 status；
    `payload` 全文禁止进入投影。
- [x] 大字段禁入是硬约束：投影结果任何字段不得包含 `diff`、`output`、`payload`
  原文（防止后续上下文组装与审计误携带大体量或敏感内容），契约测试用含标记
  字符串的行对象断言标记不出现在投影 JSON 中。
- [x] 新增 `apps/backend/tests/test_evidence.py`：四类投影逐字段断言、200 字符
  截断断言、大字段禁入断言、`EvidenceItem` 拒绝未知字段断言。
- [x] 零行为变更边界：不修改现有 API、scheduler、前端和数据库；除测试外
  不新增任何 import 该模块的运行时代码（EvidenceService 的查询与归并属 C1/C2）。
- **为什么**：C1 记忆事实生成与 C2 交接编译都要消费同一份证据形状；先以纯函数
  冻结「哪些字段可进入投影、哪些必须留在源记录」，避免后续实现各自为政或把
  大体量/敏感内容带进上下文与审计。
- **验收**：新契约测试全绿；后端全量 pytest、Ruff、桌面端 TypeScript 通过
  （前端应无 diff）；仍零运行时接线；GitHub Actions 双平台绿。完成后停下等
  Claude review，不进入 C0-3。

### C0-3 状态跃迁与路由审计构建器（纯构建器 + 契约测试，零行为变更）

> 编排器（C5）与分类器（C3）尚不存在，本条目只冻结审计事件的形状与合法性校验，
> 不产生任何运行时审计写入；C3/C5 落地时直接调用本模块，不得另造事件格式。

- [x] 新增 `apps/backend/dualcode/collaboration_audit.py`：
  - 事件名常量：`EVENT_STATE_TRANSITION = "collaboration.state_transition"`、
    `EVENT_ROUTING_DECISION = "collaboration.routing_decision"`。
  - 严格 detail 模型（复用 `collaboration_protocol.StrictModel` 配置）：
    `StateTransitionDetail(run_id, from_state: CollaborationState,
    to_state: CollaborationState, round: int, reason: str)`；
    `RoutingDecisionDetail(category, primary_agent, collaborator, reason)`。
  - 两个纯构建器，返回未入库的 `AuditLog` 行对象（调用方负责 `db.add`）：
    `build_state_transition_audit(workspace_id, thread_id, detail) -> AuditLog`、
    `build_routing_decision_audit(workspace_id, thread_id, detail) -> AuditLog`；
    `detail` 序列化为 JSON 存入 `AuditLog.detail`，`event` 用上述常量。
- [x] 合法性校验内置于构建器：`build_state_transition_audit` 对
  `from_state → to_state` 按 `COLLABORATION_TRANSITIONS` 校验，非法跃迁抛
  `ValueError` 拒绝构建（防止未来编排器把非法跃迁写成看似正常的审计）；
  `build_routing_decision_audit` 对 `category` 按 `ROUTING_MATRIX` 校验，
  未知类别拒绝构建。
- [x] `reason` 与所有字符串字段沿用 evidence 的单行化 + 200 字符截断规则；
  允许把 `evidence.py` 的 `_summary` 提升为模块公开函数（如
  `summarize_single_line`）供两处复用，除改名导出外不得顺手重构 evidence。
  detail 为严格模型意味着审计明细只含声明字段——凭据、完整 prompt、隐藏思维
  与附件内容在类型层面无处容身（对应 §11.6）。
- [x] 新增 `apps/backend/tests/test_collaboration_audit.py`：两类构建器的
  event/workspace/thread/detail JSON 逐字段断言与 detail round-trip；非法跃迁
  拒绝构建；未知路由类别拒绝构建；`reason` 超长截断；detail 模型拒绝未知字段。
- [x] 零行为变更边界：不修改现有 API、scheduler、前端和数据库 schema
  （`AuditLog` 表结构不变）；除测试外不新增任何 import 本模块的运行时代码。
- **为什么**：§11.5 要求所有状态跃迁与路由决定写审计；先冻结事件名与 detail
  形状，C3/C5 的审计写入就只是「构建 + db.add」，格式不会随实现漂移，审计
  消费方（诊断、恢复中心）也可以提前依赖稳定事件名。
- **验收**：新契约测试全绿；后端全量 pytest、Ruff、桌面端 TypeScript 通过
  （前端应无 diff）；仍零运行时接线；GitHub Actions 双平台绿。完成后停下等
  Claude review；C0-3 通过后进行 C0 阶段整体验收。

---

## C1 执行清单（交 Codex）

> 执行约定沿用 `docs/REMEDIATION_BACKLOG.md` 全部规则。C1-1 → C1-2 → C1-3 按序
> 执行、一条目一 commit；三条全部完成后停下等 Claude review，不进入 C2。
> C1 是首个含 Alembic 迁移与运行时接线的阶段：迁移只新增表；运行时注入必须由
> 新功能开关守门，开关默认关闭时现有行为零变化。

### C1-1 `memory_facts` 表与事实契约（迁移 + 模型 + 类型冻结）

- [x] `collaboration_protocol.py` 冻结事实枚举与内容契约（属受控协议变更）：
  `FactKind = Literal["requirement", "decision", "repository", "evidence",
  "risk", "assumption"]`、`FactSource = Literal["user", "git", "test", "codex",
  "claude", "system"]`、`FactConfidence = Literal["confirmed", "verified",
  "unverified", "stale"]`；`MemoryFactContent(StrictModel)` 至少含
  `content: str`（经 `summarize_single_line` 治理，上限放宽为 500 字符以容纳
  验收标准类内容——在常量旁注明与 evidence 200 字符的差异原因）。
  可信度排序常量：`confirmed > verified > unverified > stale`。
- [x] `models.py` 新增 `MemoryFact` ORM：按 §8.1 全部列；`thread_id` 可空
  （项目级事实）、`supersedes_id` 自引用外键、`invalidated_at` 可空、
  `created_at` 用现有 `UTCDateTime`/`utc_now`；`workspace_id`、`thread_id`、
  `confidence` 建索引。
- [x] Alembic 迁移仅新增 `memory_facts` 表；全新库与既有数据库升级均通过
  （沿用 P3-1 的迁移测试模式）。
- **验收**：迁移双向可用（升级保数据）；契约测试覆盖枚举拒绝非法值、content
  治理与 500 截断。
  - 2026-07-29：专项 11 项、后端全量 169 项、Ruff 与桌面端 TypeScript
    通过；升级/降级、索引、既有数据保留、非法枚举及 500 字符治理均已覆盖。

### C1-2 MemoryService：事实生成、覆盖规则、失效与审计

- [x] 新增 `apps/backend/dualcode/memory_service.py`：
  - `record_fact(...)`：写入事实；`supersedes_id` 指定时执行覆盖规则——新事实
    可信度必须 ≥ 被覆盖事实（按 C1-1 排序），Agent 来源（codex/claude）的
    `unverified` 事实不得覆盖 `confirmed`/`verified`，违规抛 `ValueError`；
    被覆盖事实置 `invalidated_at`。
  - `mark_stale_for_commit(thread_id, current_sha)`：`repository` 类且
    `commit_sha` 与当前不符的未失效事实批量置 `confidence="stale"`（§3.2）。
  - `snapshot_thread_facts(db, workspace, thread)`：从现有数据生成带来源事实——
    TaskContract 的 goal/acceptance/risks → `requirement`/`risk`
    （source=user, confirmed）；当前 Git commit → `repository`
    （source=git, verified）；TestRun → `evidence`（source=test, verified，
    复用 C0-2 `from_test_run` 投影的 summary，不落大字段）；幂等：同内容同
    commit 的事实不重复写入。
  - 审计（§11.5）：`collaboration_audit.py` 增加
    `EVENT_MEMORY_CHANGE = "collaboration.memory_change"` 与
    `MemoryChangeDetail(fact_id, kind, action: Literal["created", "superseded",
    "invalidated"], source, confidence)` 构建器（属受控变更）；MemoryService
    每次写入/覆盖/失效均 `db.add` 对应审计行。
- **验收**：服务测试覆盖写入、合法与非法覆盖、stale 批量失效、快照生成幂等、
  审计行逐字段；大字段（契约全文、diff、测试 output）不进入事实内容。
  - 2026-07-29：专项 12 项通过；写入、可信度单调覆盖、旧 commit stale、
    快照幂等和逐字段审计均已覆盖，测试 output 与未投影契约字段不会进入事实。

### C1-3 上下文组装注入（预算 + 功能开关）

- [x] `config.py` 新增 `smart_collaboration_enabled: bool = False`（§14 开关，
  环境变量可开）；`context_budget.py` 新增 `MEMORY_CHAR_BUDGET = 8_000` 与
  `MEMORY_TRUNCATION_MARKER = "【共享记忆已截断】"`。
- [x] 新增纯函数 `build_memory_section(facts, budget)`（放 `context_budget.py`
  或 memory_service，二选一并保持单一职责）：按 §3.3 顺序渲染——目标/验收 →
  决策与规则 → 仓库基线 → 未关闭风险/阻塞 → 其余；超预算按可信度从低到高
  丢弃（`stale`/`unverified` 先弃），`confirmed` 事实不得截断，溢出时插入
  截断标记。
- [x] scheduler 接线：开关开启时，Codex 与 Claude 的每轮上下文在现有契约段
  之后附加记忆段（两条路径同一实现）；开关关闭（默认）时不查询、不注入，
  现有 prompt 逐字节不变。
- **验收**：开关关闭时现有全部测试与 prompt 快照零变化；开关开启的集成测试
  覆盖 §12 C1 验收场景——同任务先后调用两个 Agent，上下文均含已确认目标、
  当前 commit 与未关闭风险；预算截断与 confirmed 保全有专项测试。
  - 2026-07-29：专项 12 项通过；默认关闭路径已锁定为零数据库访问，环境变量
    可开启；Codex/Claude 共用同一记忆组装入口，目标、commit、风险及预算策略
    均有覆盖。

**C1 阶段验收**：后端全量 pytest（含迁移、服务、注入专项）、Ruff、桌面端
TypeScript 通过（前端应无 diff）；GitHub Actions 双平台绿；开关默认关闭下
现有 Codex/Claude 单模式零回归。

---

## C2 执行清单（交 Codex）

> 执行约定沿用 `docs/REMEDIATION_BACKLOG.md` 全部规则。C2-1 → C2-2 → C2-3 按序
> 执行、一条目一 commit；三条全部完成后停下等 Claude review，不进入 C3。
> 全程受 `smart_collaboration_enabled` 守门：开关关闭时现有交接 API 的 payload
> 与提示词逐字节不变；v2 交接预览的前端渲染属 C6，本阶段前端应无 diff。

### C2-1 HandoffCompiler：编译并存储 `handoff.v2`

- [x] 新增 `apps/backend/dualcode/handoff_compiler.py`：
  `compile_handoff_v2(db, workspace, thread, *, purpose, sender, recipient)
  -> HandoffV2`，从真实数据构建并经冻结模型校验：
  - `task`：TaskContract 的 goal/non_goals/acceptance/constraints。
  - `repository`：`base_sha` = 当前 HEAD；**C4 影子快照实施前
    `snapshot_sha` 取值等于 `base_sha`，在字段旁注释说明**；`branch` 取现状；
    `changed_files` 来自 FileChange 路径；`diff_stats` 定义为
    `{"files": 变更文件数}`（后续扩展属受控协议变更）。
  - `evidence`：TestRun 经 C0-2 `from_test_run` 投影为
    `{type: "test", command, exit_code, summary}`；`output` 全文禁入。
  - `claims` 留空列表（Codex 主张在 C5 由编排器填入）；`risks` 取契约
    known_risks；`open_findings` 取该交接前未解决 finding 描述（C2-3 落地前
    可先留空列表并注明）。
- [x] `api_collaboration.py` 的 `prepare_handoff`：开关开启时 payload 存
  `compile_handoff_v2(...).model_dump(by_alias=True)`；开关关闭时走现有
  `_handoff_payload` 且输出逐字节不变。
- **验收**：编译器单元测试覆盖字段来源逐项断言、大字段（diff、output）禁入、
  模型校验失败传播；API 测试覆盖开关两态的 payload 形状。
  - 2026-07-29：专项 14 项通过；真实契约、Git、FileChange 与 TestRun 来源逐项
    覆盖，diff/output 禁入，关闭态保留 legacy payload，开启态持久化 handoff.v2。

### C2-2 ReviewParser：`review.v1` 确定性解析

- [x] 新增 `apps/backend/dualcode/review_parser.py`：
  `parse_review(text: str) -> ReviewParseResult`。`ReviewParseResult` 为严格模型：
  `outcome: Literal["parsed", "no_json", "invalid_json", "schema_mismatch"]`、
  `review: ReviewV1 | None`（仅 parsed 非空）、`raw_text: str`（无论成败完整
  保留原文，供 C5 在解析失败时进入 `WAITING_USER` 展示）、
  `error: str | None`（截断为单行 200 字符）。
- [x] 提取规则确定性固定：优先扫描 ```json 围栏块，无围栏时扫描裸 JSON 对象；
  存在多个候选时取**最后一个**能通过 `ReviewV1` 校验的候选（结论惯例在文末），
  全部候选都校验失败时按最后一个候选的失败类别归类；禁止任何形式的裁决猜测
  或字段补全（§6：解析失败不得猜测裁决）。
- **验收**：§12 C2 四类输入——正常、缺字段（`schema_mismatch`）、非法 JSON
  （`invalid_json`）、无 JSON（`no_json`）——各有确定行为断言；另覆盖多候选
  取末、围栏与裸 JSON、原文保留逐字节断言。
  - 2026-07-29：专项 11 项通过；四类 outcome、围栏优先、裸对象、多候选末个
    有效裁决、末失败分类、原文逐字节保留与 200 字符错误治理均已覆盖。

### C2-3 findings 持久化（迁移 + 服务 + 审计）

- [x] `collaboration_protocol.py` 冻结 `FindingStatus = Literal["open",
  "resolved"]`（受控协议变更）。
- [x] `models.py` 新增 `CollaborationRun` 与 `ReviewFinding` ORM，按 §8.1 列
  定义；`ReviewFinding.collaboration_run_id` 可空（C5 前的裁决仅关联
  `source_handoff_id`）、`resolved_by_snapshot_sha` 可空。
- [x] Alembic 迁移 `0004`：同时新增 `collaboration_runs` 与 `review_findings`
  两表——**`collaboration_runs` 提前建表仅作为外键目标，C5 前无任何写入方**
  （SQLite 事后加外键需重建表，故一次建齐；在迁移 docstring 写明理由）；
  沿用防重入护栏与 downgrade；迁移测试覆盖升级保数据与降级。
- [x] 新增 `apps/backend/dualcode/review_findings.py`（或并入 review_parser，
  二选一保持单一职责）：`persist_review_findings(db, *, workspace_id, thread_id,
  source_handoff_id, review: ReviewV1, collaboration_run_id=None, round=1)
  -> list[ReviewFinding]`——逐条落库（status="open"），并经
  `collaboration_audit.py` 新增的 `EVENT_REVIEW_VERDICT =
  "collaboration.review_verdict"` 与 `ReviewVerdictDetail(handoff_id, verdict,
  blocking_count, advisory_count)` 构建器写一条裁决审计（受控变更；finding
  描述全文不入审计）。
- **验收**：持久化测试覆盖逐字段落库、audit 行断言、finding 描述不入审计
  detail；迁移测试全绿。
  - 2026-07-29：专项 16 项通过；0004 双表升级/降级与既有数据保留、FindingStatus
    冻结、逐字段落库、blocking/advisory 计数审计及描述禁入均已覆盖；新交接会
    携带此前未解决 finding 描述。

**C2 阶段验收**：后端全量 pytest、Ruff、桌面端 TypeScript 通过（前端应无
diff）；GitHub Actions 双平台绿；开关默认关闭下现有交接 API payload 与提示词
逐字节不变（有回归断言）。完成后停下等 Claude review。

### C2-R1 finding 主键回落 uid（代码 + 测试，一个 commit）

- [x] `apps/backend/dualcode/review_findings.py`：删除 `ReviewFindingRecord(...)`
  构造中的 `id=finding.id` 一行，主键回落 `models.py` 的 `uid` 默认生成；
  审查方自拟编号不落库（§8.1 无对应列，未来如需要属受控 schema 变更），
  `description`/`acceptance` 已保留实质内容，不做其他改动。
- [x] `apps/backend/tests/test_review_findings.py`：新增测试——同一
  workspace/thread 下两次 `persist_review_findings`（不同 `source_handoff_id`），
  两份 `ReviewV1` 各含 `id="F-1"` 的 finding，断言两次均持久化成功、共 2 条
  记录、主键互不相同且互不覆盖；如现有测试断言了 `record.id == "F-1"` 一并更新。
- **为什么**：见 C2 Review 返工项 C2-R1——审查方跨轮次复用 `F-1` 类编号是
  常态，现实现第二次持久化即主键 IntegrityError，C5 自动整改循环第二轮必然
  崩溃。
- **验收**：新增碰撞测试与后端全量 pytest、Ruff 通过；GitHub Actions 双平台绿。
  - 2026-07-29：同一任务两轮审查复用 `F-1` 的专项测试通过，两条记录使用独立
    uid 且描述、轮次与 source handoff 互不覆盖；审查方编号不再作为数据库主键。
- **验证结果（2026-07-29）**：新增两类稳定事件名、严格 detail 模型及返回未入库
  `AuditLog` 的纯构建器；构建前复用冻结的跃迁与路由查表校验。evidence 摘要函数
  仅提升为公开复用点，detail 全部字符串统一单行化并限制为最多 200 字符。
  专项测试 7 项、后端全量 161 项、Ruff 与桌面端 TypeScript 通过；除测试外无
  运行时代码导入审计构建器，数据库 schema、API、scheduler 与前端均无改动。
  GitHub Actions 双平台结果待本提交推送后由 Claude review 确认。
- **验证结果（2026-07-29）**：新增严格 `EvidenceItem` 与四个纯 ORM 投影函数，
  摘要统一压成单行并限制为最多 200 字符；模型拒绝未知字段和 kind 字段错配。
  四类含标记大字段的源对象经投影后，序列化结果均不含 `output`、`diff` 或
  `payload` 原文。专项测试 8 项、后端全量 154 项、Ruff 与桌面端 TypeScript
  通过；除测试外无任何模块导入 evidence，仍为零运行时接线。GitHub Actions
  双平台结果待本提交推送后由 Claude review 确认。
- **验证结果（2026-07-29）**：§5.1 已用权威 Markdown 跃迁表取代示意图并补齐
  直通、挂起恢复、用户裁决与终态语义；后端常量逐边对齐，测试以完整目标集合
  直接断言每个非终态，并覆盖 `WAITING_APPROVAL → COMPLETED` 及两个终态出边非法。
  契约测试 22 项、后端全量 146 项、Ruff 与桌面端 TypeScript 通过；仍无运行时接线。
  GitHub Actions 双平台结果待本提交推送后由 Claude review 确认。

**Codex 执行结果（2026-07-29）**

- 已新增严格 Pydantic 协议、独立协作状态机、八行路由矩阵与复杂度条件；未接入任何
  scheduler、adapter、API、前端、数据库或运行时行为。
- 新契约测试 21 项通过；后端全量 145 项通过；Ruff 与桌面端 TypeScript 通过。
- 首次沙箱内全量测试受 Windows 临时目录 ACL 阻断，改为独立临时目录并在批准的
  非沙箱测试进程中复验通过。GitHub Actions 双平台结果留待本提交推送后独立 review。
- 按约定停在 C0-1，不进入 C0-2/C0-3，等待 Claude review。

## Review 记录

### C2-R1 复验（2026-07-29，Claude）

**结论：C2-R1 关闭，C2 阶段正式关闭，可进入 C3。**

- 修复与清单逐字一致：`review_findings.py` 仅删除 `id=finding.id` 一行，
  主键回落 `uid` 默认生成，无其他改动。
- 碰撞测试到位：两次持久化（不同 `source_handoff_id`）各含 `id="F-1"` 的
  finding，断言共 2 条记录、主键互不相同、描述与交接归属正确；原有测试补充
  `record.id != "finding-1"` 断言锁定审查方编号不再入主键。
- 独立复验：后端全量 198 项、Ruff 通过；CI 双平台绿（`a0c9c61`）。

### C2 阶段 Review（2026-07-29，Claude）

**结论：有条件通过。C2-1、C2-2 合格；C2-3 存在一处必须返工的持久化缺陷
（C2-R1），修复后 C2 关闭。**

逐项核查（独立复验）：

- C2-1 ✓：`compile_handoff_v2` 字段来源与清单逐项一致——`snapshot_sha` 暂取
  `base_sha` 并有注释、`diff_stats={"files": N}`、TestRun 走 C0-2 投影且
  `output` 禁入、`open_findings` 直接联查未解决 finding（C2-3 同阶段落地，
  未用留空过渡，合理）；`prepare_handoff` 开关关闭走原 `_handoff_payload`
  逐字节不变、开启存 v2（API 测试覆盖两态形状断言）。
- C2-2 ✓：四类 outcome 确定性齐备；围栏优先、裸 JSON 用带字符串/转义感知的
  括号扫描器提取；多候选取最后一个通过校验者、全败按最后候选归类；原文完整
  保留、error 单行 200 截断、`parsed ⇔ review 非空` 有模型级校验。
- C2-3 结构 ✓：`CollaborationRun`/`ReviewFinding` ORM 与 §8.1 逐列一致，
  0004 迁移两表一次建齐（SQLite 外键约束理由成立）；`EVENT_REVIEW_VERDICT`
  审计只记 verdict 与数量、finding 描述不入 detail。
- 复验数据：后端全量 197 项（新增 16 项）、Ruff 通过；CI 双平台绿（`a9bf476`）。

**返工项（归属 C2-3）：**

- **C2-R1｜审查方提供的 `finding.id` 被直接用作 `review_findings` 主键，
  跨审查必然主键冲突。** `review_findings.py:24` 以 `id=finding.id` 覆盖
  `uid` 默认值；`review.v1` 的 finding id 是审查方自拟编号（方案示例即
  `"F-1"`），不同轮次/不同交接的审查复用相同编号是常态。失败场景：同一任务
  两轮审查各含 `id="F-1"` 的 finding，第二次 `persist_review_findings` 触发
  主键 IntegrityError，C5 自动整改循环第二轮即崩溃。修复：删除 `id=finding.id`
  一行，主键回落 `uid` 默认生成；审查方编号不落库（§8.1 无对应列，如未来
  需要属受控 schema 变更），description/acceptance 已保留实质内容；补一条
  「两次持久化各含相同 reviewer 编号的 finding 均成功且互不覆盖」的测试。

**建议项（不阻塞，记入 C4 前提）：**

- `base_sha` 复用 `repository_status` 的 `--short=10` 短 SHA，空仓库时为空
  字符串。C4 影子 ref 与 `resolved_by_snapshot_sha` 需要全长 SHA 精确锚定，
  实施 C4 时应改为 `rev-parse HEAD` 全长输出（属受控协议语义收紧），本阶段
  不动。

### C1 阶段 Review（2026-07-29，Claude）

**结论：C1-1/C1-2/C1-3 全部通过，无返工项。C1 阶段关闭，可进入 C2。**

逐项核查（独立复验）：

- C1-1 ✓：三组事实枚举、`FACT_CONFIDENCE_RANK`、`MemoryFactContent`（500 字符，
  常量旁注明与 evidence 200 的差异原因）冻结进协议模块；`MemoryFact` ORM 与
  §8.1 逐列一致（`thread_id` 可空、自引用 `supersedes_id`、三个索引）；迁移
  `0003_memory_facts` 仅新增表、含已存在防重入护栏与 downgrade；迁移测试覆盖
  全新库、降级不触数据、补丁前/后升级保数据四条。
- C1-2 ✓：覆盖规则双重校验（可信度不降级 + Agent unverified 不得覆盖
  confirmed/verified，后者为显式冗余护栏，接受）；`mark_stale_for_commit` 仅
  批量置 stale 不设 `invalidated_at`（stale 事实仍可注入并按最低优先丢弃，
  语义正确）；快照生成幂等（同内容同 commit 不重写），TestRun 复用 C0-2 投影
  summary，大字段禁入有测试；每次写入/覆盖/失效均产审计行，
  `EVENT_MEMORY_CHANGE` 与 `MemoryChangeDetail` 按 C0-3 模式扩展并复用冻结枚举。
- C1-3 ✓：`smart_collaboration_enabled` 默认 False，测试断言默认值与环境变量
  开启两个方向；**开关关闭时 `_shared_memory_prompt` 直接返回空串、不发起任何
  查询**（有专项测试），prompt 逐字节不变；开启时记忆段插在契约段与对话之间
  （§3.3 顺序），Codex/Claude 共用 `_execute_chat` 单一 prompt 构建点，两 Agent
  天然一致；预算丢弃从低可信度开始、confirmed 永不截断（超预算时保全并加
  截断标记，符合"不得被截断"约束）均有专项测试。
- §12 C1 验收场景 ✓：开启态集成测试断言上下文含已确认目标、当前 commit 与
  未关闭风险。
- 复验数据：后端全量 181 项（新增 20 项）、Ruff 通过；CI 双平台绿（`cd19801`）。

**记录两处已接受的实现选择：**

- stale 标记的审计 action 复用 `invalidated`（枚举未扩展）；若后续需要区分，
  属受控协议变更，届时再议。
- `summarize_single_line` 增加 `max_length` 参数以支撑 500 字符事实内容，
  默认值不变，evidence 现有行为无回归。

### C0-3 Review 与 C0 阶段整体验收（2026-07-29，Claude）

**结论：C0-3 通过，无返工项。C0 阶段（C0-1/C0-2/C0-3）整体关闭，可进入 C1。**

C0-3 逐项核查：

- 事件常量、`StateTransitionDetail`/`RoutingDecisionDetail` 严格模型、两个纯构建器
  与清单规格一致；构建器返回未入库 `AuditLog`，跃迁经 `transition()`、路由类别经
  `route_for()` 前置校验，非法输入拒绝构建。
- 字符串治理超出最低要求：detail 的全部字符串字段（不止 `reason`）经
  `field_validator` 统一单行化 + 200 字符截断；`evidence.py` 仅做 `_summary →
  summarize_single_line` 改名导出，无顺手重构。
- 测试覆盖两类构建器逐字段与 round-trip、非法跃迁拒绝、未知路由类别拒绝、
  截断规则、未知字段拒绝（7 项）。
- 独立复验：零运行时接线；后端全量 161 项、Ruff 通过；CI 双平台绿（`c99cd5b`）。

C0 阶段整体验收（对照 §12 C0 验收标准）：

- **schema 契约测试** ✓：`handoff.v2`、`review.v1`、协作状态机（含权威跃迁表）、
  路由矩阵、evidence 投影、审计事件形状全部冻结为类型 + 契约测试，合计新增
  37 项（后端 124 → 161）。
- **现有单模式零回归** ✓：三个条目全程零运行时接线（构造性保证），现有
  scheduler、adapter、API、前端、数据库 schema 无一行为变更；全量后端与 CI
  双平台门禁在每个条目关闭时均为绿。
- 规格进入受控变更状态：C1-C6 对协议、投影或审计形状的任何改动都将显式表现为
  `collaboration_protocol.py`/`evidence.py`/`collaboration_audit.py` 及其测试的
  diff，逐条接受 review。

### C0-2 Review（2026-07-29，Claude）

**结论：通过，无返工项。C0-2 关闭，可进入 C0-3。**

- `EvidenceItem` 与清单规格一致：四值 kind、`source_id`/`thread_id` 回指、
  200 字符单行截断（含省略号恰为 200）、严格模型拒绝未知字段；并超出规格
  增加了 kind 与可选字段的匹配校验（如 `file_change` 携带 `command` 直接拒绝），
  属正向收紧，接受。
- 四个纯投影函数无数据库访问；大字段禁入硬约束成立——测试用标记字符串对
  `output`、`diff`、`before_diff`/`after_diff`、`payload` 逐一断言不出现在投影
  JSON 中。
- 记录一处已接受的规格解释：handoff 投影的可选字段仅保留 `status`，
  `recipient`/`purpose` 收进 summary（清单原文「`agent`/`status`（agent_run 与
  handoff）」存在歧义）；C2 编译交接时直接读 `HandoffPackage` 源记录，不受影响。
- 独立复验：零运行时接线；后端全量 154 项（新增 8 项）、Ruff 通过；
  CI 双平台绿（`4857e0e`）。

### C0-1-R1 复验（2026-07-29，Claude）

**结论：C0-1-R1 关闭，C0-1 正式关闭。协议冻结完成，规格进入受控变更状态；
可进入 C0-2。**

- 跃迁表逐边一致 ✓：`COLLABORATION_TRANSITIONS` 与 §5.1 定稿表 13 个非终态的
  目标集合完全一致（含审批挂起边、用户裁决边、可恢复失败边、全员取消边与
  `DRAFT → READY` 直通）；`COMPLETED`/`CANCELLED` 无出边。§5.1 已用 Markdown
  表替换 ASCII 示意图，直通门禁、挂起恢复与三种用户裁决的语义随表写入。
- 测试对齐 ✓：原「非终态覆盖」断言替换为对权威表的逐边全量断言
  （`test_transition_table_matches_authoritative_specification`）；非法跃迁
  参数化补充挂起态误入终态与终态出边；全状态可达性与成功/整改路径测试保留。
- 独立复验 ✓：仍零运行时接线；后端全量 146 项、Ruff 通过；CI 双平台绿
  （run 对应 `534639c`）。

### C0-1 Review（2026-07-29，Claude）

**结论：有条件通过。协议模型、路由矩阵与契约测试合格；跃迁表按 §5.1 示意图逐字
转写，但该图相对 §5.3/§9.2/§11 欠规格，冻结出的状态机与同一文档矛盾。修复
C0-1-R1 后 C0-1 关闭。这正是冻结阶段 review 要暴露的问题，不属执行偏差。**

逐项核查（独立复验）：

- `HandoffV2`/`ReviewV1` ✓：字段与 §6 逐一对应，`schema` 字面量、`purpose`/
  `verdict`/finding `type`/`severity` 枚举、`extra="forbid"` 全部锁定；拒绝矩阵
  测试覆盖缺字段、非法枚举、未知字段、schema 不匹配十种变体。
- 路由矩阵 ✓：八行与 §4.2 逐字一致，复杂度条件五条冻结，未知类别抛异常，
  重复查表结果一致。
- `RunState` 关系 ✓：模块 docstring 写明两枚举不合并、不转换（关闭 review 建议项二）。
- 零行为变更 ✓：全仓无运行时 import，仅测试引用；后端 145 项、Ruff、CI 双平台绿。

**返工项（归属 C0-1）：**

- **C0-1-R1｜跃迁表把挂起/裁决/阻塞态冻结成了死胡同，且取消与审批边覆盖不足，
  与 §5.3、§9.2、§11 矛盾。** 现状（`collaboration_protocol.py:97`）：
  `WAITING_APPROVAL`、`WAITING_USER`、`BLOCKED` 无任何出边，成为事实终态；三者
  及 `CANCELLED` 仅能从 `REVIEWING` 进入。矛盾点：
  1. §5.3.4 审批可发生在实现期间（安装、联网、Git 副作用），§11.4 影子同步首次
     审批发生在 `SYNCING_REVIEW_SNAPSHOT`——但表中 `IMPLEMENTING`/`VERIFYING`/
     `SYNCING_REVIEW_SNAPSHOT` 均无法进入 `WAITING_APPROVAL`。
  2. §9.2 提供 resume/decisions API、RELAY 保护条款 4 规定「用户处理后循环续跑」
     ——但挂起三态无出边，恢复语义在冻结契约中不存在。
  3. 「用户可随时停止」（§9.2 cancel、RELAY 条款 4）——但 `IMPLEMENTING → CANCELLED`
     等均为非法跃迁。
  4. `test_collaboration_protocol.py:143` 将上述缺边断言为预期，把矛盾固化进契约。
  修复顺序：**先改规格，再对齐代码**。§5.1 重写为显式跃迁表（放弃 ASCII 示意图
  作为规格载体），建议按三类横切边补全：审批挂起边（`IMPLEMENTING`/`VERIFYING`/
  `SYNCING_REVIEW_SNAPSHOT`/`REVIEWING` ⇄ `WAITING_APPROVAL`，出边回到挂起前状态，
  挂起前状态记录在运行记录上）；用户裁决边（冲突/上限/无进展/预算进入
  `WAITING_USER`，出边为继续目标态或 `CANCELLED`）；可恢复失败边（运行态 ⇄
  `BLOCKED`，出边为恢复原态或 `CANCELLED`）；另全部非终态可进入 `CANCELLED`，
  终态仅 `COMPLETED`/`CANCELLED`。同时显式决定 `DRAFT → READY` 直通是否允许
  （建议：契约已满足 READY 门禁时允许跳过 `CLARIFYING`，对应 §1.1.6「简单任务
  不为协作而协作」）。代码与测试随新表对齐，可达性测试保留，「非终态全覆盖」
  断言改为对新表逐边断言。

**建议项（不阻塞）：**

- `ReviewFinding.line` 冻结为 `str | None`：可表达行区间（如 `"12-40"`），但消费方
  不能按整数处理；在 C2 ReviewParser 落地时于字段 docstring 写明格式约定。
- 路由矩阵以中文叙述串作字典键：作为 §4.2 的忠实数据冻结可接受；C3 实现
  TaskClassifier 时应引入稳定 slug 枚举作为键、中文串降为展示标签，届时该变更
  会显式表现为本模块 diff，接受 review。

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

### C-R3 / C-R4 关闭复验（2026-07-29，Claude）

**结论：C-R3、C-R4 均关闭。全部进入 C0 前返工项（C-R1 至 C-R4）已清零，
C0-1 正式解锁。**

- C-R4 ✓：`a56336c` 将 dev 依赖收紧为 `ruff>=0.15,<0.16`，与规格一致；
  CI 双平台实际安装 0.15.22，本地 0.15.21，同一 0.15.x 基线，漂移消除。
  Ruff 0.16 升级按约定未在本条目内进行，留作独立任务。
- 附带格式修复合规 ✓：`2eaf972` 为 CI 恢复完整流水线后暴露的
  `App.tsx`/`App.test.tsx`/`store.ts` 既有 Prettier 漂移（7-27 批次提交的 CI
  从未跑到前端步骤，故未被发现）。逐行核查确认为纯机械格式化，无业务逻辑变更，
  独立成 commit 且在文档中如实记录，符合执行约定。
- CI 验收标准满足 ✓：run `30413530551` 与 `30413795340` 双平台完整通过全部步骤
  （Ruff、后端 pytest 124 项、TypeScript、ESLint、Prettier 检查、前端 78 项），
  非仅 Ruff 步骤通过。C-R3 的「CI 双平台绿」验收项凭同批运行一并关闭。
- 本地独立复验 ✓：后端全量 124 项、Ruff 通过。

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
