"""
Validated conditions for the deterministic BSI rule-engine domain.

This module defines one immutable condition that can evaluate a supported
attribute of a normalized transaction.

Condition construction validates:

- The selected transaction field
- The selected comparison operator
- Operator compatibility with the field
- The condition value type
- Text normalization
- Financial amount precision
- Date and range boundaries

Actual transaction evaluation belongs to a separate evaluator module.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from bsi.domain.rules.enums import (
    RuleConditionField,
    RuleOperator,
)
from bsi.domain.transactions.amounts import parse_money
from bsi.domain.transactions.enums import TransactionDirection
from bsi.domain.transactions.models import normalize_search_text


class RuleConditionValidationError(ValueError):
    """Raised when a rule condition is not valid."""


type RuleConditionScalarValue = str | Decimal | date | TransactionDirection

type RuleConditionRangeValue = tuple[Decimal, Decimal] | tuple[date, date]

type RuleConditionValue = RuleConditionScalarValue | RuleConditionRangeValue


_TEXT_FIELDS = frozenset(
    {
        RuleConditionField.SEARCHABLE_TEXT,
        RuleConditionField.DESCRIPTION,
        RuleConditionField.MEMO,
        RuleConditionField.VENDOR,
    }
)

_AMOUNT_FIELDS = frozenset(
    {
        RuleConditionField.ABSOLUTE_AMOUNT,
        RuleConditionField.SIGNED_AMOUNT,
    }
)

_TEXT_OPERATORS = frozenset(
    {
        RuleOperator.CONTAINS,
        RuleOperator.NOT_CONTAINS,
        RuleOperator.EQUALS,
        RuleOperator.NOT_EQUALS,
        RuleOperator.STARTS_WITH,
        RuleOperator.ENDS_WITH,
    }
)

_EQUALITY_OPERATORS = frozenset(
    {
        RuleOperator.EQUALS,
        RuleOperator.NOT_EQUALS,
    }
)

_ORDERED_OPERATORS = frozenset(
    {
        RuleOperator.EQUALS,
        RuleOperator.NOT_EQUALS,
        RuleOperator.GREATER_THAN,
        RuleOperator.GREATER_THAN_OR_EQUAL,
        RuleOperator.LESS_THAN,
        RuleOperator.LESS_THAN_OR_EQUAL,
        RuleOperator.BETWEEN,
    }
)

_ALLOWED_OPERATORS_BY_FIELD: dict[
    RuleConditionField,
    frozenset[RuleOperator],
] = {
    RuleConditionField.SEARCHABLE_TEXT: _TEXT_OPERATORS,
    RuleConditionField.DESCRIPTION: _TEXT_OPERATORS,
    RuleConditionField.MEMO: _TEXT_OPERATORS,
    RuleConditionField.VENDOR: _TEXT_OPERATORS,
    RuleConditionField.DIRECTION: _EQUALITY_OPERATORS,
    RuleConditionField.ABSOLUTE_AMOUNT: _ORDERED_OPERATORS,
    RuleConditionField.SIGNED_AMOUNT: _ORDERED_OPERATORS,
    RuleConditionField.TRANSACTION_DATE: _ORDERED_OPERATORS,
}


@dataclass(frozen=True, slots=True)
class RuleCondition:
    """
    One validated deterministic transaction condition.

    Attributes
    ----------
    field:
        Supported normalized-transaction attribute to inspect.

    operator:
        Deterministic comparison operation.

    value:
        Expected scalar value or inclusive range boundary.

        Text conditions require a non-empty string.

        Direction conditions require TransactionDirection.

        Amount conditions require Decimal.

        Date conditions require datetime.date without a time component.

        BETWEEN requires a two-item Decimal or date tuple.
    """

    field: RuleConditionField
    operator: RuleOperator
    value: RuleConditionValue

    def __post_init__(self) -> None:
        """Validate and normalize the complete condition."""

        if not isinstance(self.field, RuleConditionField):
            raise RuleConditionValidationError("field must be a RuleConditionField.")

        if not isinstance(self.operator, RuleOperator):
            raise RuleConditionValidationError("operator must be a RuleOperator.")

        allowed_operators = _ALLOWED_OPERATORS_BY_FIELD[self.field]

        if self.operator not in allowed_operators:
            raise RuleConditionValidationError(
                f"Operator '{self.operator.value}' is not supported "
                f"for field '{self.field.value}'."
            )

        normalized_value = _normalize_condition_value(
            field=self.field,
            operator=self.operator,
            value=self.value,
        )

        object.__setattr__(
            self,
            "value",
            normalized_value,
        )

    @property
    def is_range_condition(self) -> bool:
        """
        Return whether the condition uses inclusive range boundaries.

        Returns
        -------
        bool
            True when the operator is BETWEEN.
        """

        return self.operator is RuleOperator.BETWEEN


def _normalize_condition_value(
    *,
    field: RuleConditionField,
    operator: RuleOperator,
    value: object,
) -> RuleConditionValue:
    """Normalize a scalar or range condition value."""

    if operator is RuleOperator.BETWEEN:
        return _normalize_range_value(
            field=field,
            value=value,
        )

    if isinstance(value, tuple):
        raise RuleConditionValidationError(
            f"Operator '{operator.value}' requires one scalar value."
        )

    return _normalize_scalar_value(
        field=field,
        value=value,
    )


def _normalize_scalar_value(
    *,
    field: RuleConditionField,
    value: object,
) -> RuleConditionScalarValue:
    """Normalize a condition value according to its transaction field."""

    if field in _TEXT_FIELDS:
        return _normalize_text_value(
            field=field,
            value=value,
        )

    if field is RuleConditionField.DIRECTION:
        return _normalize_direction_value(value)

    if field in _AMOUNT_FIELDS:
        return _normalize_amount_value(
            field=field,
            value=value,
        )

    if field is RuleConditionField.TRANSACTION_DATE:
        return _normalize_date_value(value)

    raise RuleConditionValidationError(
        f"Unsupported rule condition field: {field.value}."
    )


def _normalize_text_value(
    *,
    field: RuleConditionField,
    value: object,
) -> str:
    """Normalize a non-empty text condition value."""

    if not isinstance(value, str) or isinstance(
        value,
        TransactionDirection,
    ):
        raise RuleConditionValidationError(
            f"Field '{field.value}' requires a string value."
        )

    normalized_value = normalize_search_text(value)

    if not normalized_value:
        raise RuleConditionValidationError(
            f"Field '{field.value}' cannot use an empty condition value."
        )

    return normalized_value


def _normalize_direction_value(
    value: object,
) -> TransactionDirection:
    """Validate a transaction-direction condition value."""

    if not isinstance(value, TransactionDirection):
        raise RuleConditionValidationError(
            "Direction conditions require TransactionDirection."
        )

    return value


def _normalize_amount_value(
    *,
    field: RuleConditionField,
    value: object,
) -> Decimal:
    """Validate and normalize a monetary condition value."""

    if not isinstance(value, Decimal):
        raise RuleConditionValidationError(
            f"Field '{field.value}' requires a Decimal value."
        )

    normalized_value = parse_money(
        value,
        field_name=f"{field.value} condition value",
    )

    if field is RuleConditionField.ABSOLUTE_AMOUNT and normalized_value < Decimal(
        "0.00"
    ):
        raise RuleConditionValidationError(
            "absolute_amount conditions cannot use negative values."
        )

    return normalized_value


def _normalize_date_value(value: object) -> date:
    """Validate a date condition without accepting datetime values."""

    if isinstance(value, datetime) or not isinstance(value, date):
        raise RuleConditionValidationError(
            "Transaction-date conditions require a date without time."
        )

    return value


def _normalize_range_value(
    *,
    field: RuleConditionField,
    value: object,
) -> RuleConditionRangeValue:
    """Validate and normalize inclusive BETWEEN boundaries."""

    if not isinstance(value, tuple) or len(value) != 2:
        raise RuleConditionValidationError(
            "BETWEEN requires a tuple containing exactly two values."
        )

    lower_value, upper_value = value

    if field in _AMOUNT_FIELDS:
        normalized_lower = _normalize_amount_value(
            field=field,
            value=lower_value,
        )
        normalized_upper = _normalize_amount_value(
            field=field,
            value=upper_value,
        )

        if normalized_lower > normalized_upper:
            raise RuleConditionValidationError(
                "BETWEEN lower amount cannot exceed upper amount."
            )

        return normalized_lower, normalized_upper

    if field is RuleConditionField.TRANSACTION_DATE:
        normalized_lower_date = _normalize_date_value(lower_value)
        normalized_upper_date = _normalize_date_value(upper_value)

        if normalized_lower_date > normalized_upper_date:
            raise RuleConditionValidationError(
                "BETWEEN lower date cannot exceed upper date."
            )

        return normalized_lower_date, normalized_upper_date

    raise RuleConditionValidationError(
        f"Field '{field.value}' does not support BETWEEN."
    )
