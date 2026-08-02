"""Deterministic, side-effect-free task classification for smart collaboration."""

from __future__ import annotations

from typing import NamedTuple

from .collaboration_protocol import (
    RequestCategory,
    StrictModel,
    route_for,
)
from .evidence import summarize_single_line


class ClassificationRule(NamedTuple):
    category: RequestCategory
    signals: tuple[str, ...]


# Order is part of the routing contract: the first matching rule wins.
CLASSIFICATION_RULES: tuple[ClassificationRule, ...] = (
    ClassificationRule(
        "security_high_risk",
        ("安全", "权限", "鉴权", "凭据", "密钥", "数据迁移", "高风险"),
    ),
    ClassificationRule(
        "architecture",
        ("架构", "重构", "迁移框架", "技术选型", "模块拆分", "协议变更"),
    ),
    ClassificationRule(
        "bugfix",
        ("bug", "报错", "错误", "故障", "崩溃", "失败", "修复", "无法"),
    ),
    ClassificationRule(
        "test_build",
        ("测试", "构建", "打包", "安装包", "可执行程序", "发布"),
    ),
    ClassificationRule(
        "product_design",
        ("需求不清", "产品设计", "交互设计", "用户流程", "验收标准", "方案设计"),
    ),
    ClassificationRule(
        "style_fix",
        ("样式", "字体", "间距", "颜色", "边框", "对齐", "布局"),
    ),
    ClassificationRule(
        "qa",
        (
            "解释",
            "是什么",
            "为什么",
            "怎么理解",
            "如何理解",
            "介绍一下",
            "只做分析",
            "不修改代码",
            "阅读项目",
            "查看项目",
            "项目现状",
            "给出建议",
            "继续",
            "接下来",
            "然后呢",
        ),
    ),
)

SINGLE_AGENT_CATEGORIES: frozenset[RequestCategory] = frozenset({"qa", "style_fix"})


class RoutingDecision(StrictModel):
    category: RequestCategory
    primary_agent: str
    collaborator: str
    process: str
    label: str
    dual_agent: bool
    reasons: list[str]


def classify(prompt: str) -> RoutingDecision:
    """Classify a prompt using the frozen ordered rule table."""

    normalized_prompt = prompt.casefold()
    category: RequestCategory = "feature"
    matched_signal: str | None = None

    for classification_rule in CLASSIFICATION_RULES:
        matched_signal = next(
            (
                signal
                for signal in classification_rule.signals
                if signal.casefold() in normalized_prompt
            ),
            None,
        )
        if matched_signal is not None:
            category = classification_rule.category
            break

    routing_rule = route_for(category)
    dual_agent = category not in SINGLE_AGENT_CATEGORIES
    if matched_signal is None:
        classification_reason = "未命中专项规则，保守回落普通功能开发"
    else:
        classification_reason = (
            f"命中有序分类规则 {category}，信号：{matched_signal}"
        )

    collaboration_reason = (
        "协作判定：路由矩阵协作者列要求双 Agent，事后 Diff 按 §4.3 复核"
        if dual_agent
        else f"协作判定：{routing_rule.label} 默认单 Agent"
    )

    return RoutingDecision(
        category=category,
        primary_agent=routing_rule.primary_agent,
        collaborator=routing_rule.collaborator,
        process=routing_rule.process,
        label=routing_rule.label,
        dual_agent=dual_agent,
        reasons=[
            summarize_single_line(classification_reason),
            summarize_single_line(collaboration_reason),
        ],
    )
