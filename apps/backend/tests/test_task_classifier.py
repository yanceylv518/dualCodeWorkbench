from __future__ import annotations

import pytest

from dualcode.task_classifier import CLASSIFICATION_RULES, classify


@pytest.mark.parametrize(
    ("prompt", "category", "dual_agent"),
    [
        ("解释一下这个字段是什么", "qa", False),
        ("调整消息卡片的字体和间距", "style_fix", False),
        ("增加项目搜索功能", "feature", True),
        ("先做产品设计并明确用户流程", "product_design", True),
        ("完成系统架构迁移", "architecture", True),
        ("修复启动时报错的问题", "bugfix", True),
        ("检查鉴权安全和权限风险", "security_high_risk", True),
        ("构建最新可执行程序", "test_build", True),
    ],
)
def test_classify_covers_every_category(
    prompt: str, category: str, dual_agent: bool
) -> None:
    decision = classify(prompt)

    assert decision.category == category
    assert decision.dual_agent is dual_agent
    assert decision.label
    assert decision.primary_agent
    assert decision.process
    assert all("\n" not in reason for reason in decision.reasons)


def test_classification_is_deterministic() -> None:
    prompt = "请修复权限校验失败并补充测试"

    assert classify(prompt) == classify(prompt)


def test_unmatched_prompt_falls_back_to_feature() -> None:
    decision = classify("给项目增加一个收藏入口")

    assert decision.category == "feature"
    assert decision.dual_agent is True
    assert "回落普通功能开发" in decision.reasons[0]


def test_ordered_rule_table_uses_first_match() -> None:
    prompt = "修复权限错误并重新构建"

    assert classify(prompt).category == "security_high_risk"


def test_every_rule_signal_is_classifiable() -> None:
    for rule in CLASSIFICATION_RULES:
        for signal in rule.signals:
            assert classify(f"请处理：{signal}").category == rule.category
