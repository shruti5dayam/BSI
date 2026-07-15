"""
Unit tests for deterministic rule-engine enumerations.

These tests protect enum values because they may later be persisted in
PostgreSQL, exchanged through APIs, and stored in audit records.
"""

import pytest

from bsi.domain.rules.enums import (
    RuleConditionField,
    RuleLogic,
    RuleOperator,
    RuleStatus,
)


def test_all_logic_requires_every_condition() -> None:
    """ALL represents logical AND."""

    assert RuleLogic.ALL.requires_all_conditions is True


def test_any_logic_does_not_require_every_condition() -> None:
    """ANY represents logical OR."""

    assert RuleLogic.ANY.requires_all_conditions is False


@pytest.mark.parametrize(
    ("status", "expected_is_evaluable"),
    [
        (RuleStatus.DRAFT, False),
        (RuleStatus.PENDING_APPROVAL, False),
        (RuleStatus.ACTIVE, True),
        (RuleStatus.PAUSED, False),
        (RuleStatus.RETIRED, False),
    ],
)
def test_only_active_rules_are_evaluable(
    status: RuleStatus,
    expected_is_evaluable: bool,
) -> None:
    """Only approved active rules may affect financial mappings."""

    assert status.is_evaluable is expected_is_evaluable


def test_rule_status_values_are_stable() -> None:
    """Rule statuses expose stable persistence-friendly values."""

    assert RuleStatus.DRAFT.value == "draft"
    assert RuleStatus.PENDING_APPROVAL.value == "pending_approval"
    assert RuleStatus.ACTIVE.value == "active"
    assert RuleStatus.PAUSED.value == "paused"
    assert RuleStatus.RETIRED.value == "retired"


def test_condition_fields_match_transaction_contract() -> None:
    """Condition fields map to supported NormalizedTransaction attributes."""

    assert {field.value for field in RuleConditionField} == {
        "searchable_text",
        "normalized_description",
        "original_memo",
        "vendor_name",
        "direction",
        "absolute_amount",
        "signed_amount",
        "transaction_date",
    }


def test_rule_operator_values_are_stable() -> None:
    """Rule operators expose stable machine-readable values."""

    assert {operator.value for operator in RuleOperator} == {
        "contains",
        "not_contains",
        "equals",
        "not_equals",
        "starts_with",
        "ends_with",
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
        "between",
    }
