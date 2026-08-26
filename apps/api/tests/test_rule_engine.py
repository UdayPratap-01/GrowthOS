"""Tests for safe optimization rule evaluation."""

from __future__ import annotations

import pytest

from app.automation.rule_engine import evaluate_condition, evaluate_rule_conditions


def test_cpl_gt_triggers():
    match = evaluate_condition({"metric": "cpl", "operator": "gt", "value": 50}, {"cpl": 72})
    assert match.matched is True
    assert match.observed == 72


def test_missing_metric_is_insufficient_data():
    match = evaluate_condition({"metric": "ctr", "operator": "lt", "value": 1}, {})
    assert match.matched is False
    assert "INSUFFICIENT_DATA" in match.reason


def test_legacy_cpl_rule_still_works():
    match = evaluate_rule_conditions(
        {"target_cpl": 40, "cpl_multiplier": 1.3, "minimum_spend": 100, "minimum_conversions": 2},
        {"cpl": 60, "spend": 500, "conversions": 3},
    )
    assert match.matched is True


def test_structured_conditions_and():
    match = evaluate_rule_conditions(
        [
            {"metric": "cpl", "operator": "gt", "value": 40},
            {"metric": "spend", "operator": "gt", "value": 100},
        ],
        {"cpl": 55, "spend": 200},
    )
    assert match.matched is True


@pytest.mark.parametrize("operator", ["gt", "gte", "lt", "lte", "eq"])
def test_allowed_operators(operator: str):
    metrics = {"spend": 10}
    cond = {"metric": "spend", "operator": operator, "value": 10 if operator in {"gte", "lte", "eq"} else 5}
    result = evaluate_condition(cond, metrics)
    assert result.reason
