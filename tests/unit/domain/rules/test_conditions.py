"""
Unit tests for validated deterministic rule conditions.

These tests verify that RuleCondition:

- Accepts valid field, operator, and value combinations
- Normalizes text and financial amounts
- Rejects invalid field/operator combinations
- Protects Decimal-based financial precision
- Validates date and amount ranges
- Remains immutable after construction
"""

from dataclasses import FrozenInstanceError
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

import pytest

from bsi.domain.rules.conditions import (
    RuleCondition,
    RuleConditionValidationError,
)
from bsi.domain.rules.enums import (
    RuleConditionField,
    RuleOperator,
)
from bsi.domain.transactions.enums import TransactionDirection


def _invalid(value: object) -> Any:
    """
    Return a deliberately invalid value for runtime validation tests.

    The cast allows tests to bypass static type checking so the domain
    model's runtime validation can be tested intentionally.
    """

    return cast(Any, value)


@pytest.mark.parametrize(
    "field",
    [
        RuleConditionField.SEARCHABLE_TEXT,
        RuleConditionField.DESCRIPTION,
        RuleConditionField.MEMO,
        RuleConditionField.VENDOR,
    ],
)
def test_text_condition_normalizes_whitespace_and_case(
    field: RuleConditionField,
) -> None:
    """Text condition values use the same normalization as transactions."""

    condition = RuleCondition(
        field=field,
        operator=RuleOperator.CONTAINS,
        value="  NATIONAL   DCP ",
    )

    assert condition.value == "national dcp"


@pytest.mark.parametrize(
    "operator",
    [
        RuleOperator.CONTAINS,
        RuleOperator.NOT_CONTAINS,
        RuleOperator.EQUALS,
        RuleOperator.NOT_EQUALS,
        RuleOperator.STARTS_WITH,
        RuleOperator.ENDS_WITH,
    ],
)
def test_text_fields_accept_supported_text_operators(
    operator: RuleOperator,
) -> None:
    """Text fields support deterministic text comparison operators."""

    condition = RuleCondition(
        field=RuleConditionField.SEARCHABLE_TEXT,
        operator=operator,
        value="door dash",
    )

    assert condition.operator is operator
    assert condition.value == "door dash"


def test_text_condition_rejects_empty_value() -> None:
    """Blank text cannot become a meaningful rule condition."""

    with pytest.raises(
        RuleConditionValidationError,
        match="cannot use an empty condition value",
    ):
        RuleCondition(
            field=RuleConditionField.DESCRIPTION,
            operator=RuleOperator.CONTAINS,
            value="   ",
        )


def test_text_condition_rejects_non_string_value() -> None:
    """Text fields reject numbers and other non-string values."""

    with pytest.raises(
        RuleConditionValidationError,
        match="requires a string value",
    ):
        RuleCondition(
            field=RuleConditionField.VENDOR,
            operator=RuleOperator.EQUALS,
            value=_invalid(100),
        )


def test_text_condition_rejects_transaction_direction_as_text() -> None:
    """
    TransactionDirection is a StrEnum but must not enter text conditions.

    Direction values belong only to the dedicated DIRECTION field.
    """

    with pytest.raises(
        RuleConditionValidationError,
        match="requires a string value",
    ):
        RuleCondition(
            field=RuleConditionField.SEARCHABLE_TEXT,
            operator=RuleOperator.EQUALS,
            value=TransactionDirection.PAYMENT,
        )


@pytest.mark.parametrize(
    "direction",
    [
        TransactionDirection.PAYMENT,
        TransactionDirection.DEPOSIT,
    ],
)
def test_direction_condition_accepts_transaction_direction(
    direction: TransactionDirection,
) -> None:
    """Direction conditions accept the transaction-domain direction enum."""

    condition = RuleCondition(
        field=RuleConditionField.DIRECTION,
        operator=RuleOperator.EQUALS,
        value=direction,
    )

    assert condition.value is direction


def test_direction_condition_rejects_plain_string() -> None:
    """A raw string must be converted by an adapter before domain creation."""

    with pytest.raises(
        RuleConditionValidationError,
        match="Direction conditions require TransactionDirection",
    ):
        RuleCondition(
            field=RuleConditionField.DIRECTION,
            operator=RuleOperator.EQUALS,
            value=_invalid("payment"),
        )


def test_direction_condition_rejects_ordered_operator() -> None:
    """Direction cannot use greater-than or similar ordered comparisons."""

    with pytest.raises(
        RuleConditionValidationError,
        match="is not supported",
    ):
        RuleCondition(
            field=RuleConditionField.DIRECTION,
            operator=RuleOperator.GREATER_THAN,
            value=TransactionDirection.PAYMENT,
        )


def test_amount_condition_normalizes_decimal_precision() -> None:
    """Financial condition values are rounded to two decimal places."""

    condition = RuleCondition(
        field=RuleConditionField.ABSOLUTE_AMOUNT,
        operator=RuleOperator.EQUALS,
        value=Decimal("100.126"),
    )

    assert condition.value == Decimal("100.13")


def test_amount_condition_rejects_float() -> None:
    """Authoritative rule amounts must use Decimal rather than float."""

    with pytest.raises(
        RuleConditionValidationError,
        match="requires a Decimal value",
    ):
        RuleCondition(
            field=RuleConditionField.ABSOLUTE_AMOUNT,
            operator=RuleOperator.EQUALS,
            value=_invalid(100.25),
        )


def test_absolute_amount_condition_rejects_negative_value() -> None:
    """Absolute transaction amounts cannot be negative."""

    with pytest.raises(
        RuleConditionValidationError,
        match="cannot use negative values",
    ):
        RuleCondition(
            field=RuleConditionField.ABSOLUTE_AMOUNT,
            operator=RuleOperator.EQUALS,
            value=Decimal("-10.00"),
        )


def test_signed_amount_condition_accepts_negative_value() -> None:
    """Signed amounts may be negative because payments are cash outflows."""

    condition = RuleCondition(
        field=RuleConditionField.SIGNED_AMOUNT,
        operator=RuleOperator.LESS_THAN,
        value=Decimal("-100.00"),
    )

    assert condition.value == Decimal("-100.00")


@pytest.mark.parametrize(
    "operator",
    [
        RuleOperator.EQUALS,
        RuleOperator.NOT_EQUALS,
        RuleOperator.GREATER_THAN,
        RuleOperator.GREATER_THAN_OR_EQUAL,
        RuleOperator.LESS_THAN,
        RuleOperator.LESS_THAN_OR_EQUAL,
    ],
)
def test_amount_fields_accept_ordered_scalar_operators(
    operator: RuleOperator,
) -> None:
    """Amount fields accept equality and ordered scalar comparisons."""

    condition = RuleCondition(
        field=RuleConditionField.ABSOLUTE_AMOUNT,
        operator=operator,
        value=Decimal("250.00"),
    )

    assert condition.operator is operator
    assert condition.value == Decimal("250.00")


def test_amount_field_rejects_text_operator() -> None:
    """Amount fields cannot use text-search operators."""

    with pytest.raises(
        RuleConditionValidationError,
        match="is not supported",
    ):
        RuleCondition(
            field=RuleConditionField.ABSOLUTE_AMOUNT,
            operator=RuleOperator.CONTAINS,
            value=Decimal("100.00"),
        )


def test_date_condition_accepts_date_without_time() -> None:
    """Transaction-date conditions use date values without timestamps."""

    expected_date = date(2026, 7, 15)

    condition = RuleCondition(
        field=RuleConditionField.TRANSACTION_DATE,
        operator=RuleOperator.EQUALS,
        value=expected_date,
    )

    assert condition.value == expected_date


def test_date_condition_rejects_datetime() -> None:
    """Datetime values are rejected to prevent hidden time comparisons."""

    with pytest.raises(
        RuleConditionValidationError,
        match="require a date without time",
    ):
        RuleCondition(
            field=RuleConditionField.TRANSACTION_DATE,
            operator=RuleOperator.EQUALS,
            value=_invalid(datetime(2026, 7, 15, 10, 30)),
        )


def test_date_condition_rejects_string() -> None:
    """String dates must be parsed by an adapter before domain creation."""

    with pytest.raises(
        RuleConditionValidationError,
        match="require a date without time",
    ):
        RuleCondition(
            field=RuleConditionField.TRANSACTION_DATE,
            operator=RuleOperator.EQUALS,
            value=_invalid("2026-07-15"),
        )


def test_amount_between_normalizes_both_boundaries() -> None:
    """BETWEEN normalizes both amount boundaries to financial precision."""

    condition = RuleCondition(
        field=RuleConditionField.ABSOLUTE_AMOUNT,
        operator=RuleOperator.BETWEEN,
        value=(Decimal("100"), Decimal("500.126")),
    )

    assert condition.value == (
        Decimal("100.00"),
        Decimal("500.13"),
    )
    assert condition.is_range_condition is True


def test_amount_between_accepts_equal_boundaries() -> None:
    """A one-value inclusive range is valid."""

    condition = RuleCondition(
        field=RuleConditionField.ABSOLUTE_AMOUNT,
        operator=RuleOperator.BETWEEN,
        value=(Decimal("100.00"), Decimal("100.00")),
    )

    assert condition.value == (
        Decimal("100.00"),
        Decimal("100.00"),
    )


def test_amount_between_rejects_reversed_boundaries() -> None:
    """The lower amount boundary cannot exceed the upper boundary."""

    with pytest.raises(
        RuleConditionValidationError,
        match="lower amount cannot exceed upper amount",
    ):
        RuleCondition(
            field=RuleConditionField.ABSOLUTE_AMOUNT,
            operator=RuleOperator.BETWEEN,
            value=(Decimal("500.00"), Decimal("100.00")),
        )


def test_absolute_amount_between_rejects_negative_boundary() -> None:
    """Absolute-amount ranges cannot contain negative boundaries."""

    with pytest.raises(
        RuleConditionValidationError,
        match="cannot use negative values",
    ):
        RuleCondition(
            field=RuleConditionField.ABSOLUTE_AMOUNT,
            operator=RuleOperator.BETWEEN,
            value=(Decimal("-1.00"), Decimal("100.00")),
        )


def test_signed_amount_between_accepts_negative_boundaries() -> None:
    """Signed-amount ranges can represent payment values."""

    condition = RuleCondition(
        field=RuleConditionField.SIGNED_AMOUNT,
        operator=RuleOperator.BETWEEN,
        value=(Decimal("-500.00"), Decimal("-100.00")),
    )

    assert condition.value == (
        Decimal("-500.00"),
        Decimal("-100.00"),
    )


def test_date_between_accepts_ordered_boundaries() -> None:
    """Date ranges accept chronological inclusive boundaries."""

    lower_date = date(2026, 1, 1)
    upper_date = date(2026, 12, 31)

    condition = RuleCondition(
        field=RuleConditionField.TRANSACTION_DATE,
        operator=RuleOperator.BETWEEN,
        value=(lower_date, upper_date),
    )

    assert condition.value == (lower_date, upper_date)
    assert condition.is_range_condition is True


def test_date_between_rejects_reversed_boundaries() -> None:
    """The lower date cannot be later than the upper date."""

    with pytest.raises(
        RuleConditionValidationError,
        match="lower date cannot exceed upper date",
    ):
        RuleCondition(
            field=RuleConditionField.TRANSACTION_DATE,
            operator=RuleOperator.BETWEEN,
            value=(date(2026, 12, 31), date(2026, 1, 1)),
        )


@pytest.mark.parametrize(
    "invalid_range",
    [
        (Decimal("100.00"),),
        (
            Decimal("100.00"),
            Decimal("200.00"),
            Decimal("300.00"),
        ),
        [Decimal("100.00"), Decimal("200.00")],
    ],
)
def test_between_requires_exactly_two_item_tuple(
    invalid_range: object,
) -> None:
    """BETWEEN requires an immutable pair of boundaries."""

    with pytest.raises(
        RuleConditionValidationError,
        match="tuple containing exactly two values",
    ):
        RuleCondition(
            field=RuleConditionField.ABSOLUTE_AMOUNT,
            operator=RuleOperator.BETWEEN,
            value=_invalid(invalid_range),
        )


def test_scalar_operator_rejects_tuple_value() -> None:
    """Non-BETWEEN operators accept one scalar value only."""

    with pytest.raises(
        RuleConditionValidationError,
        match="requires one scalar value",
    ):
        RuleCondition(
            field=RuleConditionField.ABSOLUTE_AMOUNT,
            operator=RuleOperator.EQUALS,
            value=_invalid(
                (
                    Decimal("100.00"),
                    Decimal("200.00"),
                )
            ),
        )


def test_text_field_rejects_between_operator() -> None:
    """Text ranges are intentionally unsupported."""

    with pytest.raises(
        RuleConditionValidationError,
        match="is not supported",
    ):
        RuleCondition(
            field=RuleConditionField.SEARCHABLE_TEXT,
            operator=RuleOperator.BETWEEN,
            value=_invalid(("a", "z")),
        )


def test_scalar_condition_is_not_range_condition() -> None:
    """A normal scalar comparison is not identified as a range."""

    condition = RuleCondition(
        field=RuleConditionField.DESCRIPTION,
        operator=RuleOperator.CONTAINS,
        value="rent",
    )

    assert condition.is_range_condition is False


def test_condition_rejects_invalid_field_type() -> None:
    """Raw field strings must be converted before domain construction."""

    with pytest.raises(
        RuleConditionValidationError,
        match="field must be a RuleConditionField",
    ):
        RuleCondition(
            field=_invalid("searchable_text"),
            operator=RuleOperator.CONTAINS,
            value="rent",
        )


def test_condition_rejects_invalid_operator_type() -> None:
    """Raw operator strings must be converted before domain construction."""

    with pytest.raises(
        RuleConditionValidationError,
        match="operator must be a RuleOperator",
    ):
        RuleCondition(
            field=RuleConditionField.SEARCHABLE_TEXT,
            operator=_invalid("contains"),
            value="rent",
        )


def test_condition_is_immutable() -> None:
    """Validated rule conditions cannot be modified after construction."""

    condition = RuleCondition(
        field=RuleConditionField.DESCRIPTION,
        operator=RuleOperator.CONTAINS,
        value="utilities",
    )

    condition_for_mutation = cast(Any, condition)

    with pytest.raises(FrozenInstanceError):
        condition_for_mutation.value = "rent"
