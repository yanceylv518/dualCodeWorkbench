# 产品化整改清单（Remediation Backlog）

> 本文档是 2026-07-13 全量代码审查的产出，供 Codex 按序执行，Claude 负责逐项 review。
> 执行者请先完整阅读「执行约定」，每完成一个条目就勾选对应复选框并填写验证结果。
> 本文档只描述「改什么、为什么、验收标准」；具体实现方案由执行者提出，重大架构调整需先在条目下写出方案再动手。

## 执行约定

1. **一个条目 = 一个独立 commit**（P0-1 这类跨文件条目可拆多个 commit，但不允许一个 commit 混多个条目）。commit message 格式：`fix(backlog): P1-3 审批卡可见性` 这样带条目编号。
2. **每个条目完成后必须跑通并记录**：
   - 后端改动：`python -m pytest apps/backend/tests -q` 全绿；
   - 前端改动：`corepack pnpm --filter @dualcode/desktop typecheck` 与 `corepack pnpm --filter @dualcode/desktop test` 全绿；
   - 涉及行为变化的条目必须**新增或修改测试**覆盖该行为，不允许只改实现。
3. **禁止顺手重构**：条目未提及的代码不要动；发现新问题记到本文档末尾「执行中新发现」一节，不要直接修。
4. **不降低安全不变量**（见 PROJECT_STATUS.md 末尾）：审批、审计、known_hosts 校验、参数化命令调用等一律不得放宽。
5. 完成一个 Phase 后停下，等待 Claude review 通过再进入下一个 Phase。review 意见以行内批注或本文档追加「Review 记录」的形式给出。
6. 用户可见文案统一使用中文（错误提示、系统消息、状态徽标）；日志与审计明细可保留英文。

## 验证命令速查

```bash
# 后端
cd apps/backend && python -m pytest tests -q
# 前端
corepack pnpm --filter @dualcode/desktop typecheck
corepack pnpm --filter @dualcode/desktop test
# 静态检查（P0-4 完成后生效）
ruff check apps/backend
corepack pnpm --filter @dualcode/desktop lint
```

---

## Phase 0：清除 demo 遗留与工程基础（P0）

### P0-1 删除旧演示流水线与假数据路径
- [x] `apps/backend/dualcode/schemas.py:7`：`MessageCreate.mode` 收窄为 `^(codex|claude)$`，默认值改为 `codex`；`auto` 与 `collaboration` 一并移除。
- [x] `apps/backend/dualcode/scheduler.py:128-372`：删除 `_execute` 中的编排流水线分支（PLANNING→IMPLEMENTING→TESTING→REVIEWING 那条），包括 `diff = "Mock diff"`（约 :284）和伪造的 `TestRun(command="pytest", output="12 passed")`（约 :322）。`_execute` 只保留对 `_execute_chat` 的分发或直接内联。
- [x] 同步清理不再被引用的 `state_machine.transition` 调用链、`GitService.create_worktree` 若仅被该流水线使用则保留代码但移除死引用（先确认 `git_service.py` 其他调用方）。
- [x] 更新受影响的后端测试；新增一条 API 测试：POST message 传 `mode=collaboration` 必须返回 422。
- **为什么**：该路径可从 API 直达并会写入伪造测试记录，是最大的 demo 残留。
- **验收**：全量后端测试通过；代码中不再出现 `"Mock diff"`、`"12 passed"` 字面量。
- **验证结果（2026-07-13）**：后端全量 68 项通过；生产代码中已无 `Mock diff`、`12 passed` 及旧编排入口。

### P0-2 删除前端演示夹具与 sample 分支
- [x] 删除 `apps/desktop/src/data.ts`。
- [x] 删除 `App.tsx:47` 的 `sample` 判定（魔法路径 `D:/Projects/dualcode`）及其贯穿 `Composer`、sample-banner（App.tsx:128）的所有传参与分支。
- [x] `store.ts:177-181`：移除后端离线时**以 Claude 身份伪造回复**的降级演示逻辑；离线时禁用 composer 并显示明确的离线横幅（复用 `backend === "offline"` 状态），`upload` 的离线分支（store.ts:213）同样移除。
- [x] `App.tsx:156` `BackendBadge`：文案改中文，去掉 "Offline Demo"，改为「后端离线」并给出重试入口（调用 `initialize`）。
- **验收**：前端 typecheck/test 通过；全局搜索 `D:/Projects/dualcode`、`降级演示`、`Offline Demo` 无结果。
- **验证结果（2026-07-13）**：TypeScript 类型检查通过；前端 7 项组件测试通过；指定演示字符串全局搜索无结果。

### P0-3 重写 E2E 冒烟测试
- [x] `tests/e2e/workbench.spec.ts` 当前断言的是已删除的演示 UI，必然失败。重写为最小真实冒烟：启动前端（后端 mock 或真实 sidecar 任选，写清前置条件），断言空状态「打开本地代码项目」渲染、设置对话框可打开可关闭。
- [x] 把 e2e 纳入 `tests/e2e/package.json` 可一键运行，并在 README「检查与测试」补充命令。
- **验收**：`pnpm --dir tests/e2e test`（或等价命令）本机可跑通。
- **验证结果（2026-07-13）**：`corepack pnpm --dir tests/e2e test` 通过（1 项）；测试使用独立 1421 端口自行启动前端，后端可不启动。

### P0-4 建立 CI 与前端 lint
- [x] 新增 `.github/workflows/ci.yml`：matrix 覆盖 ubuntu-latest 与 windows-latest；任务包含后端 pytest、`ruff check`、前端 typecheck、前端 vitest。Tauri 打包不进 CI（保留本地脚本）。
- [x] 前端引入 ESLint（typescript-eslint + react-hooks 插件）与 Prettier 强制格式化：新增 `lint` script，`prettier --write` 全量格式化一次 `apps/desktop/src`（**单独一个 commit，只做格式化，不夹带逻辑改动**）。
- [x] `pyproject.toml` 确认 ruff 配置存在并在 CI 中执行。
- **验收**：CI 在 PR 上全绿；`App.tsx` 等文件不再存在超长单行组件（Prettier 默认 printWidth 即可）。
- **验证结果（2026-07-13）**：格式化已用独立提交 `3152c80` 完成；本地等价 CI 验证为 Ruff 通过、后端 68 项、前端 7 项、ESLint、Prettier 和类型检查通过。GitHub 双平台 workflow 将在推送后执行。

### P0-5 本地 API 增加 sidecar token 鉴权
**实现方案（动手前记录）**：Tauri 壳每次启动使用 UUID v4 组合生成高熵会话 token，通过
`DUALCODE_SIDECAR_TOKEN` 环境变量只传给 sidecar，并以只读 `invoke` 命令提供给 WebView；后端在
ASGI 最外层中间件统一校验 `/api/*` 的 HTTP 请求头和 WebSocket 查询参数，避免各路由遗漏。
浏览器开发模式由后端启动时在 `~/.dualcode-workbench/sidecar.token` 原子写入仅当前用户可读的
临时 token，Vite 启动时读取并注入；生产模式不落盘。前端 `api.ts` 以单一鉴权 fetch 包装器和
WebSocket URL 工厂附带 token，附件 URL 同样携带查询参数。CORS 预检 OPTIONS 不访问业务数据，
允许无 token 通过，以保证自定义请求头可在浏览器中使用。
- [x] 后端启动时生成一次性随机 token；所有 `/api/*`（含 WebSocket）校验 `X-DualCode-Token` 头或 `?token=` 查询参数，缺失/错误返回 401。
- [x] Tauri 壳（`src-tauri/src/lib.rs`）启动 sidecar 时传入/读取 token 并注入前端（环境变量→`invoke` 或启动参数，方案自定，写明理由）；浏览器开发模式支持从 `~/.dualcode-workbench` 下的 token 文件读取。
- [x] `apps/desktop/src/api.ts` 统一附带 token。
- [x] 新增后端测试：无 token 请求 401；错误 token 401；正确 token 200。
- **为什么**：当前任何本机进程都能调审批接口、读附件，审批体系形同虚设。
- **验收**：测试通过；`docs/ARCHITECTURE.md` 补一段 token 机制说明。
- **验证结果（2026-07-13）**：后端全量 70 项通过；前端 7 项、类型检查与 ESLint 通过；Rust `cargo check` 通过。缺失、错误、正确请求头及查询参数均有自动化覆盖，架构文档已补充 token 生命周期与传递边界。

### P0-6 凭据防护由黑名单改为分层策略
- [ ] `apps/backend/dualcode/security.py`：扩充规则至少覆盖 `.env*`、`*.pem/.key/.p12/.pfx`、`id_rsa*/id_ed25519*/id_ecdsa*`、`credentials.json`、`.npmrc`、`.netrc`、`*.keystore`；用可维护的规则列表（glob + 说明）替代散落条件。
- [ ] 补足单元测试：上述每类至少一条正例一条反例。
- **验收**：`test_security.py` 覆盖新规则并通过。

---

## Phase 1：前端高严重度交互缺陷（P1）

### P1-1 中文输入法 Enter 误发送
- [ ] `App.tsx` Composer 的 `onKeyDown`：`event.nativeEvent.isComposing` 为 true 时不发送。
- [ ] 新增组件测试：模拟 composing 状态下 Enter 不触发 `run`。
- **验收**：拼音候选态按 Enter 不再发出消息。

### P1-2 列表输入框无法换行
- [ ] `ContractPanel.tsx:7,17` 与 `SettingsDialog.tsx:35`：受控 textarea 不得在 `onChange` 里做 `split/filter/join` 回写。改为：textarea 持有原始字符串 state，仅在保存（或 blur）时解析为行数组。
- [ ] 新增组件测试：输入含空行的多行文本，光标行为正常，保存后得到过滤后的数组。
- **为什么**：现状按 Enter 产生的空行被立即过滤，光标弹回，多行输入事实上不可用。

### P1-3 审批卡可见性
- [ ] 审批请求（`store.pendingApproval`）必须在右侧面板隐藏或窄屏（<960px，`index.css:15` 会 `display:none` 检查器）时依然可见可操作。方案建议：审批卡移入对话流（ProcessingCard 位置）或改为居中模态；右侧面板保留只读展示。方案先写在本条目下再实施。
- [ ] 新增组件测试：`rightHidden` 状态下出现 approval 时仍能渲染审批操作按钮。
- **为什么**：现状面板被藏起时运行永久挂起，且用户无从发现。

### P1-4 WebSocket 断线重连
- [ ] `store.ts:110-168`：为线程 socket 增加 `onclose` 处理与指数退避重连（上限约 30s），重连成功后调用现有的 `refreshDetails`/`fetchApprovals`/`refreshExecutionJobs` 补齐丢失事件；连接状态反映到 UI（复用 backend badge 或新增连接指示）。
- [ ] `onmessage` 的 `JSON.parse` 包 try/catch，坏帧忽略并 console.warn。
- [ ] 新增 store 测试：模拟 close 事件触发重连调度。

### P1-5 消息流滚动策略
- [ ] `App.tsx:58-63`：改为「仅当用户位于底部附近（阈值 ~80px）时跟随滚动」；用户上翻后出现「回到最新」悬浮按钮，点击回底。
- [ ] 新增组件测试覆盖"上翻时不强制吸底"。

### P1-6 假快捷键与菜单收起
- [ ] 实现真实的 Ctrl+O（打开项目）全局快捷键，或删掉 `App.tsx:112` 的 `<kbd>Ctrl+O</kbd>` 标识（推荐实现）。
- [ ] `projectMenu`（App.tsx:116）：点击菜单外任意处与按 Esc 收起。

### P1-7 ProcessingCard 显示运行中的 agent
- [ ] `App.tsx:131` 不再传 `store.mode`，改为从当前 run 事件（`run.state_changed` 的 `payload.agent`，store 已接收）记录"正在运行的 agent"并传入。
- [ ] 测试：运行中切换发送目标下拉，处理卡名字不变。

---

## Phase 2：前端设计与信息架构（P2）

### P2-1 字号与对比度体系
- [ ] 建立 CSS 变量字号 token（如 `--text-xs:11px / --text-sm:12px / --text-base:13px / --text-lg:14px`），全局替换现有 8/9/10px：正文 ≥13px，辅助文本 ≥11px，禁止 <11px。
- [ ] 低对比灰字（#556273 一档）整体提亮一级；改动集中在 `index.css` 与各 `*.css`。
- [ ] 所有可聚焦元素补 `:focus-visible` 可见样式（统一 outline token）；移除输入框裸 `outline:0` 而无替代的写法。
- **验收**：抽查主要界面无 <11px 文本；键盘 Tab 遍历时焦点始终可见。

### P2-2 Markdown 渲染器替换
- [ ] 用 `react-markdown`（+ `remark-gfm`，禁 raw HTML）替换 `App.tsx:208-219` 的手写 `FormattedMessage`；代码块用轻量高亮（如 `highlight.js` 按需引入或延续现有 pre 样式），支持标题/链接（`target=_blank rel=noreferrer`）/有序无序列表/表格。
- [ ] 保留现有消息气泡样式，新增 markdown 元素样式。
- [ ] 组件测试：列表、链接、代码块、表格各一条渲染断言。

### P2-3 Diff 面板文件导航 + Monaco 懒加载
- [ ] 按 `details.changes`（后端已有 changed_files）拆分 diff：文件列表 + 单文件视图，或逐文件可折叠分组；保留只读。
- [ ] Monaco 改为动态 `import()` 懒加载，消除生产构建 chunk 警告（PROJECT_STATUS 已知缺口 4）。
- **验收**：`pnpm build` 无 chunk 大小警告；多文件修改时可按文件查看。

### P2-4 交接面板结构化呈现
- [ ] `HandoffPanel.tsx:16`：去掉裸 `JSON.stringify` 预览，按 payload 结构分区展示（契约摘要 / 仓库基线 / 修改文件列表 / Diff 预览（可折叠、等宽） / 测试证据）；状态枚举翻译为中文徽标；"Diff n 字符"改为"n 个文件、+x/-y 行"（前端可从 diff 文本统计）。

### P2-5 错误通知机制
- [ ] `store.error` 单字符串改为通知队列（id、级别 error/info、文案、可选自动消失时长）；错误默认不自动消失、信息类 5s 自动消失；可堆叠展示（右下角）。
- [ ] 后台轮询失败（如"无法刷新恢复任务"）降级为 info 且自动消失，不打断用户。
- [ ] 相应更新 `store.test.ts`。

### P2-6 任务管理补全
- [ ] 线程重命名：任务标题处双击或菜单进入行内编辑，落库（后端补 `PATCH /threads/{id}` 或等价接口 + 测试）。
- [ ] 首轮用户消息发送后，若标题仍为默认「新开发任务」，自动以消息前 ~20 字生成标题（纯本地截断即可，不调模型）。
- [ ] 线程删除：菜单入口 + 确认（应用内确认样式，见 P2-8），后端级联清理该线程数据并审计。

### P2-7 侧栏与底栏交互修正
- [ ] `App.tsx:149` 底部"后台任务"按钮 onClick 跳转到对应 workspace/thread。
- [ ] 明确其数据来源为最近一次 `fetchWorkspaces` 快照，展示处加"状态以进入任务后为准"的弱提示；（真正的多线程实时状态属架构改动，记入「不做」）。
- [ ] 搜索无结果时显示空状态文案。

### P2-8 对话框行为统一
- [ ] SettingsDialog、ProjectDialog：支持 Esc 关闭、初始 autoFocus、简单焦点圈定（Tab 不逃出对话框）；与图片灯箱行为对齐。
- [ ] 替换全部 `window.confirm/prompt`（App.tsx:116 移除项目、SettingsDialog.tsx:23 完全访问确认、非 Tauri 的路径 prompt）为应用内确认对话框组件（新建一个通用 `ConfirmDialog`）。
- [ ] 完全访问确认文案去掉硬编码"Windows 用户"，按平台中性描述。

### P2-9 Composer 与附件打磨
- [ ] textarea 自动增高（上限约 40vh），保留 Shift+Enter 换行。
- [ ] 合并重复的"添加图片/添加附件"两个按钮为一个；超过 8 个附件时给出明确提示（用 P2-5 通知）而非静默丢弃；拖放悬停时 composer 显示高亮态。
- [ ] 图片灯箱：放大后支持拖拽平移；`onWheel` 的 `preventDefault` 改为在非 passive 原生监听上处理（ref + addEventListener）。

### P2-10 RemoteRepository 收敛为单一渲染路径
- [ ] `App.tsx:227-263`：合并"已配置只读摘要"与"编辑中"两条分支中重复的 VPS 状态区；删除编辑分支残留的旧「VPS 获取/VPS 拉取」按钮（与已确认的交互规范冲突）；`head.slice(0,10)` 前判空。
- [ ] feedback 文案改为单一来源（由 job/remote 状态派生的纯函数），移除 useEffect 与手动 setFeedback 的竞争（App.tsx:237-247）。
- [ ] 该组件从 App.tsx 拆出独立文件并补组件测试（克隆运行中、失败重试、就绪三态）。

### P2-11 文案统一为中文
- [ ] 全局排查用户可见英文串：`scheduler.py`（"Network access was rejected by the user." 等系统消息）、`main.py`（"Sidecar restarted before this Agent turn completed"）、BackendBadge（P0-2 已含）、brand-subtitle 保留品牌英文可接受。
- [ ] 错误信息保留技术细节但外层包中文说明。

---

## Phase 3：后端结构与可维护性（P3）

### P3-1 引入 Alembic 迁移
- [ ] 用 Alembic 接管 schema：以当前 models 生成 baseline revision；`main.py:16-27` 的手写 `PRAGMA/ALTER TABLE` 补丁转成迁移脚本，启动时执行 `upgrade head`。
- [ ] 保证旧数据目录（含手写补丁前后的库）可平滑升级，写一条针对旧库文件的升级测试。

### P3-2 拆分 api.py
- [ ] `api.py`（1385 行）按域拆分为多个 router 模块（建议：workspaces / threads+messages / git / remote_git / jobs / approvals / handoffs / contracts / settings / attachments / ws），行为零变化，测试全绿证明。
- [ ] 交接 prompt 模板（api.py:768 附近）移到独立模块。

### P3-3 协议解析下沉到 adapter
- [ ] `scheduler.py:553-596` `_stream_agent` 中 Codex/Claude 的 JSON 事件解析分别移入 `CodexAppServerAdapter` 与 Claude adapter，adapter 对外产出统一事件类型（delta / tool_event / terminal / final）；scheduler 只做转发与持久化。
- [ ] 现有 `test_agent_resilience.py`、协议测试相应迁移，不减覆盖。

### P3-4 上下文预算
- [ ] `scheduler.py:429-432`：transcript 由"最近 20 条"改为按字符预算（如 60k 字符）从新到旧装载，超出时头部插入「较早对话已截断」标记；契约 JSON 超限同样截断并标注。
- [ ] 单元测试覆盖截断行为。

### P3-5 前端 store 测试补齐
- [ ] `store.ts` 的 WebSocket 事件归并逻辑（`agent.delta` 流式合并、`message.created` 替换 stream 占位、`agent.tool` 活动时间线、error settle）每类至少一条测试；P1-4 重连测试也归入此处。

---

## 明确不做（本轮范围外）

- MSI/WiX 构建环境修复、NSIS 全新机器安装验收（环境依赖，人工处理）。
- 多线程并发实时状态推送（需要 WS 架构调整，单独立项）。
- i18n 框架（本轮只做中文统一，不引入 i18n 库）。
- 浅色主题。
- 真实故障注入验收（网络分区、凭据失效）——保持在 PROJECT_STATUS 的人工验收清单中。

## 执行中新发现

（执行者在此追加，不要直接修改上文条目范围）

## Review 记录

（Claude 每个 Phase review 后在此追加结论：通过 / 需返工条目及原因）
