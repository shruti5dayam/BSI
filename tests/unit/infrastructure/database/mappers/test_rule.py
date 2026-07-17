"""
Unit tests for rule persistence mappers.

These tests verify conversion between:

- RuleDefinition and RuleRecord
- RuleCondition and RuleConditionRecord

The mapper must preserve:

- Workspace ownership
- Rule identity and lifecycle
- Ordered conditions
- Typed scalar and range values
- Organizational scope
- COA mapping output
- Effective dates
- Persistence validation errors
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from bsi.domain.rules.conditions import RuleCondition
from bsi.domain.rules.enums import (
    RuleConditionField,
    RuleLogic,
    RuleOperator,
    RuleStatus,
)
from bsi.domain.rules.models import (
    RuleDefinition,
    RuleOutput,
)
from bsi.domain.rules.scope import RuleScope
from bsi.domain.transactions.enums import TransactionDirection
from bsi.infrastructure.database.mappers.rule import (
    RuleMapperError,
    condition_to_domain,
    condition_to_record,
    rule_to_domain,
    rule_to_record,
)
from bsi.infrastructure.database.models.rule import (
    RuleConditionRecord,
    RuleRecord,
)

WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
RULE_ID = UUID("22222222-2222-4222-8222-222222222222")
COA_ACCOUNT_ID = UUID("33333333-3333-4333-8333-333333333333")
COMPANY_ID = UUID("44444444-4444-4444-8444-444444444444")
BRAND_ID = UUID("55555555-5555-4555-8555-555555555555")
STORE_ID = UUID("66666666-6666-4666-8666-666666666666")
BANK_ACCOUNT_ID = UUID("77777777-7777-4777-8777-777777777777")


def _make_text_condition() -> RuleCondition:
    """Create a valid normalized text condition."""

    return RuleCondition(
        field=RuleConditionField.SEARCHABLE_TEXT,
        operator=RuleOperator.CONTAINS,
        value="door dash",
    )


def _make_direction_condition() -> RuleCondition:
    """Create a valid transaction-direction condition."""

    return RuleCondition(
        field=RuleConditionField.DIRECTION,
        operator=RuleOperator.EQUALS,
        value=TransactionDirection.DEPOSIT,
    )


def _make_decimal_condition() -> RuleCondition:
    """Create a valid scalar Decimal condition."""

    return RuleCondition(
        field=RuleConditionField.ABSOLUTE_AMOUNT,
        operator=RuleOperator.GREATER_THAN_OR_EQUAL,
        value=Decimal("100.00"),
    )


def _make_date_condition() -> RuleCondition:
    """Create a valid scalar date condition."""

    return RuleCondition(
        field=RuleConditionField.TRANSACTION_DATE,
        operator=RuleOperator.GREATER_THAN_OR_EQUAL,
        value=date(2026, 1, 1),
    )


def _make_decimal_range_condition() -> RuleCondition:
    """Create a valid inclusive Decimal range condition."""

    return RuleCondition(
        field=RuleConditionField.ABSOLUTE_AMOUNT,
        operator=RuleOperator.BETWEEN,
        value=(
            Decimal("100.00"),
            Decimal("500.00"),
        ),
    )


def _make_date_range_condition() -> RuleCondition:
    """Create a valid inclusive date range condition."""

    return RuleCondition(
        field=RuleConditionField.TRANSACTION_DATE,
        operator=RuleOperator.BETWEEN,
        value=(
            date(2026, 1, 1),
            date(2026, 12, 31),
        ),
    )


def _make_active_rule() -> RuleDefinition:
    """Create a complete active rule containing every value category."""

    return RuleDefinition(
        rule_id=RULE_ID,
        workspace_id=WORKSPACE_ID,
        name="DD13 DoorDash Deposits",
        logic=RuleLogic.ALL,
        conditions=(
            _make_text_condition(),
            _make_direction_condition(),
            _make_decimal_condition(),
            _make_date_range_condition(),
        ),
        output=RuleOutput(
            coa_account_id=COA_ACCOUNT_ID,
        ),
        scope=RuleScope(
            company_id=COMPANY_ID,
            brand_id=BRAND_ID,
            store_id=STORE_ID,
            bank_account_id=BANK_ACCOUNT_ID,
        ),
        status=RuleStatus.ACTIVE,
        priority=250,
        version=3,
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
        description="Maps approved DoorDash deposits.",
    )


def _make_text_condition_record() -> RuleConditionRecord:
    """Create a valid text condition persistence record."""

    return condition_to_record(
        workspace_id=WORKSPACE_ID,
        rule_id=RULE_ID,
        condition_order=0,
        condition=_make_text_condition(),
    )


def test_rule_to_record_maps_top_level_fields() -> None:
    """Rule-to-record conversion must preserve all rule metadata."""

    rule = _make_active_rule()

    record = rule_to_record(rule)

    assert isinstance(record, RuleRecord)
    assert record.workspace_id == WORKSPACE_ID
    assert record.rule_id == RULE_ID
    assert record.name == "DD13 DoorDash Deposits"
    assert record.description == "Maps approved DoorDash deposits."
    assert record.logic == RuleLogic.ALL.value
    assert record.status == RuleStatus.ACTIVE.value
    assert record.priority == 250
    assert record.version == 3
    assert record.effective_from == date(2026, 1, 1)
    assert record.effective_to == date(2026, 12, 31)
    assert record.output_coa_account_id == COA_ACCOUNT_ID
    assert record.company_id == COMPANY_ID
    assert record.brand_id == BRAND_ID
    assert record.store_id == STORE_ID
    assert record.bank_account_id == BANK_ACCOUNT_ID


def test_rule_to_record_preserves_condition_order() -> None:
    """Conditions must be persisted using zero-based tuple order."""

    rule = _make_active_rule()

    record = rule_to_record(rule)

    assert len(record.conditions) == 4
    assert [condition.condition_order for condition in record.conditions] == [
        0,
        1,
        2,
        3,
    ]

    assert [condition.value_type for condition in record.conditions] == [
        "text",
        "direction",
        "decimal",
        "date_range",
    ]


def test_rule_to_record_allows_draft_without_output() -> None:
    """Draft rules may be persisted without conditions or a COA output."""

    rule = RuleDefinition(
        rule_id=RULE_ID,
        workspace_id=WORKSPACE_ID,
        name="Draft Rent Rule",
        status=RuleStatus.DRAFT,
    )

    record = rule_to_record(rule)

    assert record.status == RuleStatus.DRAFT.value
    assert record.output_coa_account_id is None
    assert record.conditions == []


def test_condition_to_record_maps_text_value() -> None:
    """Text conditions must populate only the text value column."""

    record = condition_to_record(
        workspace_id=WORKSPACE_ID,
        rule_id=RULE_ID,
        condition_order=0,
        condition=_make_text_condition(),
    )

    assert record.field_name == "searchable_text"
    assert record.operator_name == "contains"
    assert record.value_type == "text"
    assert record.text_value == "door dash"

    assert record.direction_value is None
    assert record.decimal_value is None
    assert record.date_value is None
    assert record.decimal_lower_value is None
    assert record.decimal_upper_value is None
    assert record.date_lower_value is None
    assert record.date_upper_value is None


def test_condition_to_record_maps_direction_value() -> None:
    """Direction conditions must populate only direction_value."""

    record = condition_to_record(
        workspace_id=WORKSPACE_ID,
        rule_id=RULE_ID,
        condition_order=1,
        condition=_make_direction_condition(),
    )

    assert record.value_type == "direction"
    assert record.direction_value == "deposit"

    assert record.text_value is None
    assert record.decimal_value is None
    assert record.date_value is None
    assert record.decimal_lower_value is None
    assert record.decimal_upper_value is None
    assert record.date_lower_value is None
    assert record.date_upper_value is None


def test_condition_to_record_maps_decimal_value() -> None:
    """Scalar amount conditions must populate only decimal_value."""

    record = condition_to_record(
        workspace_id=WORKSPACE_ID,
        rule_id=RULE_ID,
        condition_order=2,
        condition=_make_decimal_condition(),
    )

    assert record.value_type == "decimal"
    assert record.decimal_value == Decimal("100.00")

    assert record.text_value is None
    assert record.direction_value is None
    assert record.date_value is None
    assert record.decimal_lower_value is None
    assert record.decimal_upper_value is None
    assert record.date_lower_value is None
    assert record.date_upper_value is None


def test_condition_to_record_maps_date_value() -> None:
    """Scalar transaction-date conditions must populate only date_value."""

    record = condition_to_record(
        workspace_id=WORKSPACE_ID,
        rule_id=RULE_ID,
        condition_order=3,
        condition=_make_date_condition(),
    )

    assert record.value_type == "date"
    assert record.date_value == date(2026, 1, 1)

    assert record.text_value is None
    assert record.direction_value is None
    assert record.decimal_value is None
    assert record.decimal_lower_value is None
    assert record.decimal_upper_value is None
    assert record.date_lower_value is None
    assert record.date_upper_value is None


def test_condition_to_record_maps_decimal_range() -> None:
    """Amount BETWEEN conditions must populate both Decimal boundaries."""

    record = condition_to_record(
        workspace_id=WORKSPACE_ID,
        rule_id=RULE_ID,
        condition_order=4,
        condition=_make_decimal_range_condition(),
    )

    assert record.value_type == "decimal_range"
    assert record.decimal_lower_value == Decimal("100.00")
    assert record.decimal_upper_value == Decimal("500.00")

    assert record.text_value is None
    assert record.direction_value is None
    assert record.decimal_value is None
    assert record.date_value is None
    assert record.date_lower_value is None
    assert record.date_upper_value is None


def test_condition_to_record_maps_date_range() -> None:
    """Date BETWEEN conditions must populate both date boundaries."""

    record = condition_to_record(
        workspace_id=WORKSPACE_ID,
        rule_id=RULE_ID,
        condition_order=5,
        condition=_make_date_range_condition(),
    )

    assert record.value_type == "date_range"
    assert record.date_lower_value == date(2026, 1, 1)
    assert record.date_upper_value == date(2026, 12, 31)

    assert record.text_value is None
    assert record.direction_value is None
    assert record.decimal_value is None
    assert record.date_value is None
    assert record.decimal_lower_value is None
    assert record.decimal_upper_value is None


def test_condition_to_domain_reconstructs_all_value_types() -> None:
    """Every supported persistence value type must reconstruct correctly."""

    conditions = (
        _make_text_condition(),
        _make_direction_condition(),
        _make_decimal_condition(),
        _make_date_condition(),
        _make_decimal_range_condition(),
        _make_date_range_condition(),
    )

    for condition_order, expected_condition in enumerate(conditions):
        record = condition_to_record(
            workspace_id=WORKSPACE_ID,
            rule_id=RULE_ID,
            condition_order=condition_order,
            condition=expected_condition,
        )

        restored_condition = condition_to_domain(record)

        assert restored_condition == expected_condition


def test_rule_to_domain_sorts_conditions_by_condition_order() -> None:
    """The mapper must restore condition tuple order from persisted order."""

    original_rule = _make_active_rule()
    record = rule_to_record(original_rule)

    record.conditions = list(reversed(record.conditions))

    restored_rule = rule_to_domain(record)

    assert restored_rule.conditions == original_rule.conditions


def test_rule_round_trip_preserves_domain_object() -> None:
    """Domain-to-record-to-domain conversion must not lose rule data."""

    original_rule = _make_active_rule()

    restored_rule = rule_to_domain(
        rule_to_record(original_rule),
    )

    assert restored_rule == original_rule


def test_rule_to_record_rejects_invalid_input_type() -> None:
    """rule_to_record must reject values outside the rule domain."""

    invalid_rule: Any = object()

    with pytest.raises(
        TypeError,
        match="rule must be a RuleDefinition",
    ):
        rule_to_record(invalid_rule)


def test_condition_to_record_rejects_negative_order() -> None:
    """Persistence condition order cannot be negative."""

    with pytest.raises(
        ValueError,
        match="condition_order cannot be negative",
    ):
        condition_to_record(
            workspace_id=WORKSPACE_ID,
            rule_id=RULE_ID,
            condition_order=-1,
            condition=_make_text_condition(),
        )


def test_condition_to_record_rejects_boolean_order() -> None:
    """Boolean values must not be accepted as integer condition orders."""

    with pytest.raises(
        TypeError,
        match="condition_order must be an integer",
    ):
        condition_to_record(
            workspace_id=WORKSPACE_ID,
            rule_id=RULE_ID,
            condition_order=True,
            condition=_make_text_condition(),
        )


def test_rule_to_domain_rejects_invalid_input_type() -> None:
    """rule_to_domain must accept only RuleRecord objects."""

    invalid_record: Any = object()

    with pytest.raises(
        TypeError,
        match="record must be a RuleRecord",
    ):
        rule_to_domain(invalid_record)


def test_condition_to_domain_rejects_invalid_input_type() -> None:
    """condition_to_domain must accept only RuleConditionRecord objects."""

    invalid_record: Any = object()

    with pytest.raises(
        TypeError,
        match="record must be a RuleConditionRecord",
    ):
        condition_to_domain(invalid_record)


def test_condition_to_domain_rejects_unknown_field() -> None:
    """Unsupported persisted condition fields must be rejected."""

    record = _make_text_condition_record()
    record.field_name = "unsupported_field"

    with pytest.raises(
        RuleMapperError,
        match="Unsupported persisted condition field",
    ):
        condition_to_domain(record)


def test_condition_to_domain_rejects_unknown_operator() -> None:
    """Unsupported persisted rule operators must be rejected."""

    record = _make_text_condition_record()
    record.operator_name = "unsupported_operator"

    with pytest.raises(
        RuleMapperError,
        match="Unsupported persisted rule operator",
    ):
        condition_to_domain(record)


def test_condition_to_domain_rejects_unknown_value_type() -> None:
    """Unsupported persistence value categories must be rejected."""

    record = _make_text_condition_record()
    record.value_type = "unsupported_type"

    with pytest.raises(
        RuleMapperError,
        match="Unsupported persisted condition value_type",
    ):
        condition_to_domain(record)


def test_condition_to_domain_rejects_missing_typed_value() -> None:
    """A value_type must have its corresponding typed column populated."""

    record = _make_text_condition_record()
    record.text_value = None

    with pytest.raises(
        RuleMapperError,
        match="text_value is required",
    ):
        condition_to_domain(record)


def test_condition_to_domain_rejects_unknown_direction() -> None:
    """Unknown persisted transaction directions must be rejected."""

    record = condition_to_record(
        workspace_id=WORKSPACE_ID,
        rule_id=RULE_ID,
        condition_order=0,
        condition=_make_direction_condition(),
    )
    record.direction_value = "unsupported_direction"

    with pytest.raises(
        RuleMapperError,
        match="Persisted direction_value is not supported",
    ):
        condition_to_domain(record)


def test_rule_to_domain_rejects_unknown_logic() -> None:
    """Unsupported persisted rule logic must be rejected."""

    record = rule_to_record(_make_active_rule())
    record.logic = "unsupported_logic"

    with pytest.raises(
        RuleMapperError,
        match="Unsupported persisted rule logic",
    ):
        rule_to_domain(record)


def test_rule_to_domain_rejects_unknown_status() -> None:
    """Unsupported persisted rule lifecycle status must be rejected."""

    record = rule_to_record(_make_active_rule())
    record.status = "unsupported_status"

    with pytest.raises(
        RuleMapperError,
        match="Unsupported persisted rule status",
    ):
        rule_to_domain(record)


def test_rule_to_domain_rejects_condition_workspace_mismatch() -> None:
    """A child condition cannot belong to another workspace."""

    record = rule_to_record(_make_active_rule())
    record.conditions[0].workspace_id = uuid4()

    with pytest.raises(
        RuleMapperError,
        match="workspace_id does not match",
    ):
        rule_to_domain(record)


def test_rule_to_domain_rejects_condition_rule_mismatch() -> None:
    """A child condition cannot belong to another parent rule."""

    record = rule_to_record(_make_active_rule())
    record.conditions[0].rule_id = uuid4()

    with pytest.raises(
        RuleMapperError,
        match="rule_id does not match",
    ):
        rule_to_domain(record)
