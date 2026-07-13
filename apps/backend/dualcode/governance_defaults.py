"""Product-grade project governance defaults shared by API workflows."""

PRODUCT_RULE = (
    "所有实现必须面向可长期交付和维护的正式产品；禁止为当前功能采用 Demo 式、"
    "临时性、模拟或不可持续的技术架构。"
)

DEFAULT_PROJECT_RULES = [
    PRODUCT_RULE,
    "需求未明确前，先确认目标用户、核心场景、范围、非目标、约束与验收标准；不得提前锁定技术框架。",
    "架构决策必须说明系统边界、依赖、数据模型、错误处理、迁移兼容策略和备选方案，不得为当前功能破坏长期结构。",
    "只修改当前任务必要范围并复用现有约定；禁止隐藏副作用、伪造状态，或让演示数据进入正式产品路径。",
    "所有外部输入、文件路径、命令参数和远程数据必须校验；凭据不得读取、记录、上传或写入仓库。",
    "删除、安装、联网、提交、推送、拉取和写入等敏感操作必须经过应用审批，并保留可追溯的审计记录。",
    "Git 拉取只允许 fast-forward；不得自动合并、强制推送或覆盖用户未提交的修改。",
    "实现前运行相关基线测试；完成后至少运行类型检查及相关单元或集成测试，并明确报告未验证项。",
    "运行、等待、失败、取消、恢复和重试必须具有明确且可持久化的状态与用户反馈；不得静默失败或无限等待。",
    "每项功能必须包含真实数据路径、加载/空/错误状态、恢复方案和必要测试；不得以 Mock 或硬编码冒充完成。",
    "Codex 编码完成时必须主动指出未覆盖范围、潜在问题、技术债和后续验证，不得只报告成功。",
    "重要需求、架构、规则或交付状态发生变化时，必须同步项目文档和唯一进度事实来源。",
]

LEGACY_DELIVERABLES = [
    "产品目标与边界",
    "任务契约与验收标准",
    "技术方案与架构决策",
    "代码变更与测试证据",
    "风险、已知问题与交付报告",
]

DEFAULT_DELIVERABLES = [
    "产品目标、范围、非目标、约束与验收标准",
    "架构说明、关键决策记录与数据模型",
    "可运行的正式产品代码及必要的配置或数据迁移",
    "自动化测试、类型检查与人工验收证据",
    "权限、安全、故障处理与恢复说明",
    "变更摘要、已知风险、未完成项与后续建议",
    "可重复构建的发布产物与运行说明",
]


def recommended_rules(stored: list[str]) -> list[str]:
    """Upgrade only the former system seed; never overwrite user rules."""
    return list(DEFAULT_PROJECT_RULES) if stored == [PRODUCT_RULE] else stored


def recommended_deliverables(stored: list[str]) -> list[str]:
    """Upgrade only the former system seed; never overwrite user deliverables."""
    return list(DEFAULT_DELIVERABLES) if stored == LEGACY_DELIVERABLES else stored
