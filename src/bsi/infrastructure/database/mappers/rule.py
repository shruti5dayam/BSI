"""
Mapper between deterministic rule domain objects and SQLAlchemy records.

The domain layer represents one rule as a nested immutable structure:

- RuleDefinition
- RuleOutput
- RuleScope
- tuple[RuleCondition, ...]

The persistence layer stores that structure across:

- RuleRecord
- RuleConditionRecord

This module converts between those representations. It must not:

- Evaluate rules
- Rank matching rules
- Resolve conflicts
- Query or commit database sessions
- Call AI services
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from bsi.domain.rules.conditions import (
    RuleCondition,
    RuleConditionValue,
)
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
from bsi.infrastructure.database.models.rule import (
    RuleConditionRecord,
    RuleRecord,
)


class RuleMapperError(ValueError):
    """
    Raised when persisted rule data cannot be reconstructed safely.

    Domain validation protects newly created rules. This exception
    protects the application when database records are incomplete,
    inconsistent, or contain unsupported values.
    """


@dataclass(frozen=True, slots=True)
class _ConditionColumns:
    """Typed database-column values for one rule condition."""

    value_type: str
    text_value: str | None = None
    direction_value: str | None = None
    decimal_value: Decimal | None = None
    date_value: date | None = None
    decimal_lower_value: Decimal | None = None
    decimal_upper_value: Decimal | None = None
    date_lower_value: date | None = None
    date_upper_value: date | None = None


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


def rule_to_record(rule: RuleDefinition) -> RuleRecord:
    """
    Convert a rule domain object into an SQLAlchemy record.

    Parameters
    ----------
    rule:
        Validated deterministic rule to persist.

    Returns
    -------
    RuleRecord
        SQLAlchemy rule record containing ordered condition records.

    Raises
    ------
    TypeError
        If rule is not a RuleDefinition.
    """

    if not isinstance(rule, RuleDefinition):
        raise TypeError("rule must be a RuleDefinition.")

    output_coa_account_id = (
        rule.output.coa_account_id if rule.output is not None else None
    )

    record = RuleRecord(
        workspace_id=rule.workspace_id,
        rule_id=rule.rule_id,
        name=rule.name,
        description=rule.description,
        logic=rule.logic.value,
        status=rule.status.value,
        priority=rule.priority,
        version=rule.version,
        effective_from=rule.effective_from,
        effective_to=rule.effective_to,
        output_coa_account_id=output_coa_account_id,
        company_id=rule.scope.company_id,
        brand_id=rule.scope.brand_id,
        store_id=rule.scope.store_id,
        bank_account_id=rule.scope.bank_account_id,
    )

    record.conditions = [
        condition_to_record(
            workspace_id=rule.workspace_id,
            rule_id=rule.rule_id,
            condition_order=condition_order,
            condition=condition,
        )
        for condition_order, condition in enumerate(rule.conditions)
    ]

    return record


def condition_to_record(
    *,
    workspace_id: UUID,
    rule_id: UUID,
    condition_order: int,
    condition: RuleCondition,
) -> RuleConditionRecord:
    """
    Convert one domain rule condition into a persistence record.

    Parameters
    ----------
    workspace_id:
        Workspace that owns the parent rule.

    rule_id:
        Identifier of the parent rule.

    condition_order:
        Zero-based position of the condition inside the rule.

    condition:
        Validated domain condition to persist.

    Returns
    -------
    RuleConditionRecord
        Typed SQLAlchemy condition record.

    Raises
    ------
    TypeError
        If identifiers, order, or condition have invalid runtime types.

    ValueError
        If condition_order is negative.
    """

    if not isinstance(workspace_id, UUID):
        raise TypeError("workspace_id must be a UUID.")

    if not isinstance(rule_id, UUID):
        raise TypeError("rule_id must be a UUID.")

    if isinstance(condition_order, bool) or not isinstance(
        condition_order,
        int,
    ):
        raise TypeError("condition_order must be an integer.")

    if condition_order < 0:
        raise ValueError("condition_order cannot be negative.")

    if not isinstance(condition, RuleCondition):
        raise TypeError("condition must be a RuleCondition.")

    columns = _condition_to_columns(condition)

    return RuleConditionRecord(
        workspace_id=workspace_id,
        rule_id=rule_id,
        condition_order=condition_order,
        field_name=condition.field.value,
        operator_name=condition.operator.value,
        value_type=columns.value_type,
        text_value=columns.text_value,
        direction_value=columns.direction_value,
        decimal_value=columns.decimal_value,
        date_value=columns.date_value,
        decimal_lower_value=columns.decimal_lower_value,
        decimal_upper_value=columns.decimal_upper_value,
        date_lower_value=columns.date_lower_value,
        date_upper_value=columns.date_upper_value,
    )


def rule_to_domain(record: RuleRecord) -> RuleDefinition:
    """
    Convert an SQLAlchemy rule record into a domain rule.

    Parameters
    ----------
    record:
        Rule record loaded from persistence.

    Returns
    -------
    RuleDefinition
        Immutable framework-independent rule definition.

    Raises
    ------
    TypeError
        If record is not a RuleRecord.

    RuleMapperError
        If child conditions do not belong to the same workspace and rule,
        or persisted enum/value data is invalid.

    Notes
    -----
    Repositories should load ``record.conditions`` before calling this
    mapper. The mapper sorts condition records by ``condition_order`` to
    reconstruct the original tuple order.
    """

    if not isinstance(record, RuleRecord):
        raise TypeError("record must be a RuleRecord.")

    logic = _parse_rule_logic(record.logic)
    status = _parse_rule_status(record.status)

    output = (
        RuleOutput(
            coa_account_id=record.output_coa_account_id,
        )
        if record.output_coa_account_id is not None
        else None
    )

    scope = RuleScope(
        company_id=record.company_id,
        brand_id=record.brand_id,
        store_id=record.store_id,
        bank_account_id=record.bank_account_id,
    )

    ordered_condition_records = sorted(
        record.conditions,
        key=lambda condition_record: condition_record.condition_order,
    )

    conditions: list[RuleCondition] = []

    for condition_record in ordered_condition_records:
        _validate_condition_ownership(
            parent_record=record,
            condition_record=condition_record,
        )
        conditions.append(condition_to_domain(condition_record))

    return RuleDefinition(
        rule_id=record.rule_id,
        workspace_id=record.workspace_id,
        name=record.name,
        logic=logic,
        conditions=tuple(conditions),
        output=output,
        scope=scope,
        status=status,
        priority=record.priority,
        version=record.version,
        effective_from=record.effective_from,
        effective_to=record.effective_to,
        description=record.description,
    )


def condition_to_domain(
    record: RuleConditionRecord,
) -> RuleCondition:
    """
    Convert one persistence condition into a domain condition.

    Parameters
    ----------
    record:
        SQLAlchemy rule-condition record.

    Returns
    -------
    RuleCondition
        Validated immutable domain condition.

    Raises
    ------
    TypeError
        If record is not a RuleConditionRecord.

    RuleMapperError
        If enum values or typed value columns are invalid.
    """

    if not isinstance(record, RuleConditionRecord):
        raise TypeError(
            "record must be a RuleConditionRecord.",
        )

    field = _parse_condition_field(record.field_name)
    operator = _parse_rule_operator(record.operator_name)
    value = _condition_value_from_record(record)

    return RuleCondition(
        field=field,
        operator=operator,
        value=value,
    )


def _condition_to_columns(
    condition: RuleCondition,
) -> _ConditionColumns:
    """Serialize one domain condition into typed persistence columns."""

    value = condition.value

    if condition.field in _TEXT_FIELDS:
        if not isinstance(value, str) or isinstance(
            value,
            TransactionDirection,
        ):
            raise RuleMapperError("Text condition did not contain a string value.")

        return _ConditionColumns(
            value_type="text",
            text_value=value,
        )

    if condition.field is RuleConditionField.DIRECTION:
        if not isinstance(value, TransactionDirection):
            raise RuleMapperError(
                "Direction condition did not contain TransactionDirection."
            )

        return _ConditionColumns(
            value_type="direction",
            direction_value=value.value,
        )

    if condition.field in _AMOUNT_FIELDS:
        return _amount_condition_to_columns(condition)

    if condition.field is RuleConditionField.TRANSACTION_DATE:
        return _date_condition_to_columns(condition)

    raise RuleMapperError(f"Unsupported rule condition field: {condition.field.value}.")


def _amount_condition_to_columns(
    condition: RuleCondition,
) -> _ConditionColumns:
    """Serialize a scalar or range Decimal condition."""

    value = condition.value

    if condition.operator is RuleOperator.BETWEEN:
        if not isinstance(value, tuple) or len(value) != 2:
            raise RuleMapperError(
                "Amount BETWEEN condition requires two Decimal values."
            )

        lower_value, upper_value = value

        if not isinstance(lower_value, Decimal) or not isinstance(
            upper_value,
            Decimal,
        ):
            raise RuleMapperError(
                "Amount BETWEEN condition requires two Decimal values."
            )

        return _ConditionColumns(
            value_type="decimal_range",
            decimal_lower_value=lower_value,
            decimal_upper_value=upper_value,
        )

    if not isinstance(value, Decimal):
        raise RuleMapperError("Amount condition did not contain a Decimal value.")

    return _ConditionColumns(
        value_type="decimal",
        decimal_value=value,
    )


def _date_condition_to_columns(
    condition: RuleCondition,
) -> _ConditionColumns:
    """Serialize a scalar or range date condition."""

    value = condition.value

    if condition.operator is RuleOperator.BETWEEN:
        if not isinstance(value, tuple) or len(value) != 2:
            raise RuleMapperError("Date BETWEEN condition requires two date values.")

        lower_value, upper_value = value

        if (
            isinstance(lower_value, datetime)
            or isinstance(upper_value, datetime)
            or not isinstance(lower_value, date)
            or not isinstance(upper_value, date)
        ):
            raise RuleMapperError("Date BETWEEN condition requires two date values.")

        return _ConditionColumns(
            value_type="date_range",
            date_lower_value=lower_value,
            date_upper_value=upper_value,
        )

    if isinstance(value, datetime) or not isinstance(value, date):
        raise RuleMapperError("Date condition did not contain a date value.")

    return _ConditionColumns(
        value_type="date",
        date_value=value,
    )


def _condition_value_from_record(
    record: RuleConditionRecord,
) -> RuleConditionValue:
    """Reconstruct a typed domain value from persistence columns."""

    if record.value_type == "text":
        return _require_value(
            record.text_value,
            column_name="text_value",
            value_type=record.value_type,
        )

    if record.value_type == "direction":
        direction_value = _require_value(
            record.direction_value,
            column_name="direction_value",
            value_type=record.value_type,
        )

        try:
            return TransactionDirection(direction_value)
        except ValueError as error:
            raise RuleMapperError(
                f"Persisted direction_value is not supported: {direction_value!r}."
            ) from error

    if record.value_type == "decimal":
        return _require_value(
            record.decimal_value,
            column_name="decimal_value",
            value_type=record.value_type,
        )

    if record.value_type == "date":
        return _require_value(
            record.date_value,
            column_name="date_value",
            value_type=record.value_type,
        )

    if record.value_type == "decimal_range":
        decimal_lower_value = _require_value(
            record.decimal_lower_value,
            column_name="decimal_lower_value",
            value_type=record.value_type,
        )
        decimal_upper_value = _require_value(
            record.decimal_upper_value,
            column_name="decimal_upper_value",
            value_type=record.value_type,
        )

        return (
            decimal_lower_value,
            decimal_upper_value,
        )

    if record.value_type == "date_range":
        date_lower_value = _require_value(
            record.date_lower_value,
            column_name="date_lower_value",
            value_type=record.value_type,
        )
        date_upper_value = _require_value(
            record.date_upper_value,
            column_name="date_upper_value",
            value_type=record.value_type,
        )

        return (
            date_lower_value,
            date_upper_value,
        )

    raise RuleMapperError(
        f"Unsupported persisted condition value_type: {record.value_type!r}."
    )


def _require_value[T](
    value: T | None,
    *,
    column_name: str,
    value_type: str,
) -> T:
    """Return a required typed value or raise a persistence error."""

    if value is None:
        raise RuleMapperError(
            f"{column_name} is required when value_type is {value_type!r}."
        )

    return value


def _parse_rule_logic(value: str) -> RuleLogic:
    """Convert a persisted rule-logic string into its domain enum."""

    try:
        return RuleLogic(value)
    except ValueError as error:
        raise RuleMapperError(
            f"Unsupported persisted rule logic: {value!r}."
        ) from error


def _parse_rule_status(value: str) -> RuleStatus:
    """Convert a persisted lifecycle string into its domain enum."""

    try:
        return RuleStatus(value)
    except ValueError as error:
        raise RuleMapperError(
            f"Unsupported persisted rule status: {value!r}."
        ) from error


def _parse_condition_field(
    value: str,
) -> RuleConditionField:
    """Convert a persisted field string into its domain enum."""

    try:
        return RuleConditionField(value)
    except ValueError as error:
        raise RuleMapperError(
            f"Unsupported persisted condition field: {value!r}."
        ) from error


def _parse_rule_operator(value: str) -> RuleOperator:
    """Convert a persisted operator string into its domain enum."""

    try:
        return RuleOperator(value)
    except ValueError as error:
        raise RuleMapperError(
            f"Unsupported persisted rule operator: {value!r}."
        ) from error


def _validate_condition_ownership(
    *,
    parent_record: RuleRecord,
    condition_record: RuleConditionRecord,
) -> None:
    """
    Confirm that a child condition belongs to its parent rule.

    This prevents cross-workspace or cross-rule data from being
    reconstructed as one domain rule.
    """

    if condition_record.workspace_id != parent_record.workspace_id:
        raise RuleMapperError(
            "Rule condition workspace_id does not match the parent rule."
        )

    if condition_record.rule_id != parent_record.rule_id:
        raise RuleMapperError("Rule condition rule_id does not match the parent rule.")


__all__ = [
    "RuleMapperError",
    "condition_to_domain",
    "condition_to_record",
    "rule_to_domain",
    "rule_to_record",
]
