# 对话体验对齐清单（UI Polish Backlog）

> 来源：2026-07-13 与 Claude App 页面表现的逐项对比分析。供 Codex 按序执行，Claude 负责逐项 review。
> 背景：功能层交互（流式、思考块、审批、断线重连）已在 REMEDIATION_BACKLOG 中补齐，
> 本清单聚焦三层剩余差距：**阅读体验精细度、消息级操作、视觉质感**。
> 执行约定沿用 `docs/REMEDIATION_BACKLOG.md` 的全部规则：一条目一 commit（编号 + 中文说明）、
> 每条附测试、禁止顺手重构、全量验证（typecheck / 严格 ESLint / vitest / 必要时 build）、
> 全部完成后停下等待 Claude review。新发现记入文末，不要扩充条目范围。

## 验证命令

```bash
corepack pnpm --filter @dualcode/desktop typecheck
corepack pnpm --filter @dualcode/desktop lint
corepack pnpm --filter @dualcode/desktop test
corepack pnpm --filter @dualcode/desktop build   # U1/U3/U5 需确认无 chunk 警告（阈值 2500kB）
```

---

### U1 代码块三件套：语法高亮、语言标签、复制按钮

- [x] 新建 `components/CodeBlock.tsx`，接管 `MarkdownMessage.tsx` 中的 `pre/code` 渲染。
- [x] 语法高亮：用 `highlight.js` **按需注册**常用语言（ts/js/tsx/python/rust/bash/json/diff/css/html/sql/yaml/markdown 即可，禁止全量引入）；未识别语言回退纯文本。高亮主题写成本项目自己的 CSS（对齐现有深色配色），不要引入整套第三方主题文件。
- [x] 语言标签渲染在代码块左上角（`.message-code` 现有 27px 顶部 padding 就是为它预留的）。
- [x] 复制按钮位于代码块右上角，hover 时显现，点击复制原始代码文本并给出瞬时反馈（按钮文案切换"已复制"约 1.5s，不要用全局通知刷屏）。
- [x] 超过约 400 行的代码块默认折叠展示前若干行 + "展开全部 (N 行)"。
- **为什么**：开发工具里代码块是 Agent 回复占比最高的内容，当前是无高亮、无复制的裸 `<pre>`，是与 Claude App 差距的第一来源。
- **验收**：组件测试覆盖高亮渲染（断言 hljs class 存在）、未知语言回退、复制按钮写入剪贴板、折叠展开；`pnpm build` 无新增 chunk 警告（highlight.js 按需注册后应远小于阈值，若接近需改用动态 import）。
- **验证结果（2026-07-14）**：新增独立 `CodeBlock`，仅从 `highlight.js/lib/core` 注册约定语言及别名，使用项目自有深色样式；未知语言回退纯文本，复制就地反馈 1.5 秒，超过 400 行默认展示前 80 行。前端 53 项、TypeScript、严格 ESLint、U1 改动文件 Prettier 与生产构建通过；主 JS 542.51kB，构建无新增 chunk 警告。

### U2 消息 hover 工具条与复制

- [x] Agent 消息（codex/claude）hover 时在消息卡右上角显示工具条：**复制**（复制 Markdown 原文）。触屏/键盘可达性：工具条按钮需可聚焦，focus 时同样显现。
- [x] 用户消息现有的两个**常驻**按钮（"编辑后重新发送"、"重试本轮"，App.tsx MessageCard）改为同样的 hover 工具条样式，行为不变。
- [x] 复制成功给瞬时反馈（同 U1 的就地反馈方式）。
- **为什么**：当前 Agent 回复完全无法一键复制，想引用审查结论只能手动拖选；用户消息的常驻文本按钮是视觉噪音。
- **验收**：组件测试覆盖 hover/focus 显现、复制内容与消息原文一致、用户消息两个动作仍可用。
- **验证结果（2026-07-14）**：Codex/Claude 消息新增右上角 hover/focus 复制工具条，复制 Markdown 原文；用户消息编辑与重试迁入同款工具条，触屏环境保持可见。抽取 `useCopyFeedback` 统一 U1/U2 的 1.5 秒就地反馈并捕获剪贴板拒绝。前端 56 项、TypeScript 与严格 ESLint 全部通过；后端测试受当前 Windows 测试临时目录 ACL 异常阻塞（代码未改动后端）。

### U3 流式渲染策略：streaming 期间纯文本，完成后切 Markdown

- [x] `MessageCard`（App.tsx）：当消息 id 为 `stream-` 前缀（进行中的流式占位，见 store.ts `stream-${run_id}`）时，渲染**纯文本 + 尾部光标**（复用 `.thinking-block` 的光标动画样式，pre-wrap），不经过 ReactMarkdown。
- [x] `message.created` 将占位替换为持久消息后（id 更换），自动切换为 MarkdownMessage 渲染，无需额外状态。
- [x] 保持"接近底部才跟随滚动"的现有行为不受影响。
- **为什么**：当前每个 delta 都把整条消息全量重新过 ReactMarkdown，长回复后期每个 chunk 都触发完整解析＋重排，产生卡顿；未闭合的 Markdown（写到一半的代码围栏）还会引起样式跳变。Claude App 的流畅感来自流式期间不做重排版。
- **验收**：组件测试断言 `stream-` 消息渲染为纯文本（无 markdown 元素）、持久化后同样内容渲染出 markdown 元素；手工验证一轮真实长回复无闪烁。
- **验证结果（2026-07-14）**：`stream-*` 消息改为 `pre-wrap` 纯文本并复用思考光标动画；持久消息 ID 替换后自然切回 Markdown，无新增状态。组件回归覆盖同一正文从纯文本到 Markdown 标题的切换，既有“用户上翻后不强制吸底”测试保持通过。前端 57 项、TypeScript、严格 ESLint及生产构建通过；主 JS 543.58kB，无新增 chunk 警告。真实长回复的最终视觉流畅度留待安装包人工验收。

### U4 思考完成态胶囊："已思考 X 秒"

- [ ] store（store.ts `toolStep` / delta 合并处）为 reasoning 步骤记录 `startedAt`，在步骤离开 running 状态时记录 `completedAt`（`ActivityStep` 增加这两个可选字段）。
- [ ] `ActivityCard`（App.tsx）：思考块结束后收起为一行"已思考 X 秒"胶囊（样式对齐 Claude App 的 thought pill），点击展开回看完整思考文本；运行中保持现有流式展示。
- [ ] 活动卡整体收起逻辑不变（终态默认折叠）。
- **为什么**：当前思考结束后过程混在活动时间线里，没有独立的时长入口；"已思考 X 秒"是 Claude App 标志性的完成态。
- **验收**：store 测试覆盖 reasoning 步骤时间戳记录；组件测试覆盖完成态胶囊显示时长、点击展开显示全文。

### U5 视觉降噪：边框减量、字号、圆角与过渡 token

- [ ] 消息正文字号 `--text-lg` 14px → 新增 `--text-reading: 15px` 用于 `.message-content`（其余 UI 字号不动）。
- [ ] 削减 1px 边框的使用：消息区、检查器分区、卡片等改为**背景明度分层**（建立 2-3 级 surface 变量，如 `--surface-0/1/2`），仅保留必要的输入框和交互边框。逐块替换，禁止一次性全局查换。
- [ ] 统一圆角 token：`--radius-sm: 6px / --radius-md: 10px / --radius-lg: 14px`，替换现有 4-16px 混用值。
- [ ] 建立 `--transition-fast: 150ms ease` 并应用到：消息浮现（新消息淡入+2px 上移）、右侧面板 tab 切换、hover 态。`prefers-reduced-motion` 时禁用位移动画。
- **为什么**：当前是"冷色+满屏 1px 边框"的调试面板质感；Claude App 靠背景分层和一致的圆角/动效尺度形成产品感。
- **验收**：现有字号策略测试仍通过（15px 合法）；无 <11px 回归；截图前后对比由用户确认（本条目完成后暂停，等用户看过再继续 U6）。
- **注意**：本条纯 CSS 但涉及面广，**单独一个 commit**，方便整体回滚。

### U6 细节小件

- [ ] 附件托盘（Composer 的 `.attachment-chip`）：图片附件显示缩略图预览（复用 `attachmentContentUrl`），非图片保持现有图标。
- [ ] "回到最新"悬浮按钮：用户上翻期间有新消息到达时显示计数（"回到最新 · N 条新消息"），回底后清零。
- [ ] 消息时间戳改为 hover 显现（布局占位保留，避免抖动）。
- **验收**：组件测试覆盖缩略图渲染、新消息计数增减、时间戳 hover 显隐。

---

## 明确不做（本清单范围外）

- Light 主题（此前已列入 REMEDIATION_BACKLOG 的范围外清单，如需翻案由用户单独决定）。
- 虚拟滚动 / 消息分页（本地场景消息量有限，收益不足）。
- Artifacts 式独立内容面板（属新功能，非体验对齐）。
- 更换字体（涉及打包体积与中文渲染验证，单独评估）。

## 执行中新发现

（执行者在此追加，不要直接修改上文条目范围）

## Review 记录

### U1 Review（2026-07-14，Claude）

**结论：U1 通过，继续 U2。**

- 实现核查：`highlight.js/lib/core` + 13 种语言按需注册（含别名映射）✓；高亮输出经 hljs 转义后注入，
  无 XSS 面 ✓；未知语言回退纯文本、标签显示 "text" ✓；复制按钮复制**完整**原文（非折叠可见部分）、
  就地反馈 1.5s、卸载清理计时器 ✓；>400 行折叠展示前 80 行 + 「展开全部（N 行）」✓；高亮主题为
  项目自有深色 CSS，未引入第三方主题文件 ✓。
- 验证：本地 53 项前端测试（5 项 CodeBlock 专项覆盖 hljs class、回退、剪贴板、折叠、禁 raw HTML）、
  TypeScript、严格 ESLint、生产构建（主 JS 542kB，无 chunk 警告）全部通过；CI run `29287842255` 双平台绿。
- 顺带审查了清单外提交 `5b37238`（消息垂直节奏修复）：属可见 bug 修复（用户消息操作栏透明占位导致
  轮次间距不均），带样式策略回归测试并记录于 PROJECT_STATUS，予以接受；不计入清单条目。
- **给 U2 的备注**：`CodeBlock.copyCode` 未捕获剪贴板写入失败（权限受限时为未处理的 Promise 拒绝）。
  U2 也要做消息复制，请抽一个带 try/catch 的共享复制工具函数，并回头让 CodeBlock 复用。

### U2 Review（2026-07-14，Claude）

**结论：U2 通过，继续 U3。U1 的备注已闭环。**

- 实现核查：Agent 消息右上角 `role="toolbar"` 工具条，hover 与 `:focus-within` 双通道显现，
  键盘聚焦按钮即可见 ✓；复制的是 Markdown 原文（测试断言逐字符一致）✓；用户消息的编辑/重试
  迁入同款工具条、行为不变 ✓；触屏（`hover: none`）降级为常显、`prefers-reduced-motion` 移除
  位移动画，可达性处理超出条目要求 ✓。
- U1 备注闭环：抽取 `hooks/useCopyFeedback`（try/catch + failed 态 + 计时器清理），
  MessageCard 与 CodeBlock 均已复用 ✓。
- 验证：本地前端 56 项（新增 3 项 U2 专项）、TypeScript、严格 ESLint 通过；CI run `29288808636`
  双平台绿。执行者报告其 Windows 环境后端测试被临时目录 ACL 阻塞——U2 未触碰后端，且我在 Linux
  上补跑后端 103 项全绿 + CI Windows 作业含 pytest 亦绿，确认属其本机环境问题，不影响验收。

### U3 Review（2026-07-14，Claude）

**结论：U3 通过，继续 U4。**

- 实现核查：`stream-` 前缀消息渲染为 `pre-wrap` 纯文本 + 复用思考光标动画，带 `aria-label`
  「正在生成回复」✓；持久化时 id 替换自然切回 MarkdownMessage，零新增状态 ✓；实现只有一个
  三元分支 + 两条 CSS 规则，改动面与条目规格完全一致 ✓；「接近底部才跟随」逻辑未触碰，
  既有滚动测试保持通过 ✓。
- 测试核查：专项测试用含 `## 标题` 的同一正文验证两态——流式期无 `<h2>`、原文可见；id 替换后
  渲染出 level-2 heading 且 `.streaming-message` 消失。正是验收要求的断言方式。
- 验证：本地前端 57 项、TypeScript、严格 ESLint、生产构建（主 JS 543.5kB，无 chunk 警告）
  全部通过；CI run `29289426549` 双平台绿。
- 备注（不阻塞）：流式期间消息工具条的「复制」会复制到当前已生成的部分内容，属合理行为；
  真实长回复的最终流畅度按条目约定留待人工验收，建议用户在下一次真实对话中感受对比。
