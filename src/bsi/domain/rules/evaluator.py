"""
Deterministic condition and rule evaluation for BSI.

This module evaluates validated RuleCondition and RuleDefinition objects
against immutable NormalizedTransaction objects.

Responsibilities
----------------
- Read supported transaction fields
- Evaluate text, direction, amount, and date conditions
- Apply ALL or ANY rule logic
- Check lifecycle, completeness, date, and scope eligibility
- Return immutable audit-friendly evaluation evidence

This module does not:

- Select the winning rule
- Resolve conflicts between multiple rules
- Modify transactions
- Persist results
- Call AI or external services
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from bsi.domain.rules.conditions import RuleCondition
from bsi.domain.rules.enums import (
    RuleConditionField,
    RuleLogic,
    RuleOperator,
)
from bsi.domain.rules.models import RuleDefinition
from bsi.domain.transactions.enums import TransactionDirection
from bsi.domain.transactions.models import (
    NormalizedTransaction,
    normalize_search_text,
)


class RuleEvaluationError(ValueError):
    """Raised when deterministic rule evaluation cannot be completed."""


type ConditionActualValue = str | Decimal | date | TransactionDirection | None


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


@dataclass(frozen=True, slots=True)
class RuleEligibility:
    """
    Eligibility checks performed before rule-condition evaluation.

    Attributes
    ----------
    status_allows_evaluation:
        True only when the rule status is ACTIVE.

    rule_is_complete:
        True when the rule has conditions and a mapping output.

    effective_date_matches:
        True when the transaction date is inside the rule's inclusive
        effective-date window.

    scope_matches:
        True when the transaction context satisfies every populated
        company, brand, store, and bank-account restriction.
    """

    status_allows_evaluation: bool
    rule_is_complete: bool
    effective_date_matches: bool
    scope_matches: bool

    @property
    def is_eligible(self) -> bool:
        """
        Return whether every prerequisite allows condition evaluation.

        Returns
        -------
        bool
            True only when all eligibility checks pass.
        """

        return (
            self.status_allows_evaluation
            and self.rule_is_complete
            and self.effective_date_matches
            and self.scope_matches
        )


@dataclass(frozen=True, slots=True)
class ConditionEvaluation:
    """
    Immutable evidence for one condition evaluated against a transaction.

    Attributes
    ----------
    condition:
        Validated condition that was evaluated.

    actual_value:
        Transaction value inspected by the condition.

        None means the optional transaction field was unavailable.

    matched:
        Whether the actual value satisfied the condition.
    """

    condition: RuleCondition
    actual_value: ConditionActualValue
    matched: bool


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    """
    Immutable result of evaluating one rule against one transaction.

    Attributes
    ----------
    rule:
        Rule that was considered.

    transaction_id:
        Identifier of the transaction that was evaluated.

    eligibility:
        Lifecycle, completeness, effective-date, and scope evidence.

    condition_evaluations:
        Result for every condition.

        This tuple is empty when the rule was not eligible. Conditions
        are not evaluated for inactive, incomplete, out-of-date, or
        out-of-scope rules.
    """

    rule: RuleDefinition
    transaction_id: UUID
    eligibility: RuleEligibility
    condition_evaluations: tuple[ConditionEvaluation, ...]

    @property
    def matched(self) -> bool:
        """
        Return whether the eligible rule matched the transaction.

        ALL logic requires every condition to match.

        ANY logic requires at least one condition to match.
        """

        if not self.eligibility.is_eligible:
            return False

        if self.rule.logic is RuleLogic.ALL:
            return all(result.matched for result in self.condition_evaluations)

        return any(result.matched for result in self.condition_evaluations)

    @property
    def matched_condition_count(self) -> int:
        """Return the number of successful condition evaluations."""

        return sum(result.matched for result in self.condition_evaluations)

    @property
    def failed_condition_count(self) -> int:
        """Return the number of unsuccessful condition evaluations."""

        return sum(not result.matched for result in self.condition_evaluations)

    @property
    def evaluated_condition_count(self) -> int:
        """Return the number of conditions actually evaluated."""

        return len(self.condition_evaluations)

    @property
    def output_account_id(self) -> UUID | None:
        """
        Return the mapped COA account only when the rule matched.

        Returns
        -------
        UUID | None
            COA account identifier for a successful match, otherwise None.
        """

        if not self.matched:
            return None

        return self.rule.output_account_id


def evaluate_rule(
    *,
    rule: RuleDefinition,
    transaction: NormalizedTransaction,
) -> RuleEvaluation:
    """
    Evaluate one deterministic rule against one normalized transaction.

    Parameters
    ----------
    rule:
        Validated rule definition.

    transaction:
        Immutable normalized bank transaction.

    Returns
    -------
    RuleEvaluation
        Eligibility evidence, condition evidence, and final match result.

    Raises
    ------
    RuleEvaluationError
        If rule or transaction is not the expected domain type.
    """

    if not isinstance(rule, RuleDefinition):
        raise RuleEvaluationError("rule must be a RuleDefinition.")

    if not isinstance(transaction, NormalizedTransaction):
        raise RuleEvaluationError("transaction must be a NormalizedTransaction.")

    eligibility = RuleEligibility(
        status_allows_evaluation=rule.status.is_evaluable,
        rule_is_complete=rule.is_complete,
        effective_date_matches=rule.is_effective_on(transaction.transaction_date),
        scope_matches=rule.scope.matches(transaction.context),
    )

    if not eligibility.is_eligible:
        return RuleEvaluation(
            rule=rule,
            transaction_id=transaction.transaction_id,
            eligibility=eligibility,
            condition_evaluations=(),
        )

    condition_evaluations = tuple(
        evaluate_condition(
            condition=condition,
            transaction=transaction,
        )
        for condition in rule.conditions
    )

    return RuleEvaluation(
        rule=rule,
        transaction_id=transaction.transaction_id,
        eligibility=eligibility,
        condition_evaluations=condition_evaluations,
    )


def evaluate_condition(
    *,
    condition: RuleCondition,
    transaction: NormalizedTransaction,
) -> ConditionEvaluation:
    """
    Evaluate one validated condition against one transaction.

    Optional text fields that are missing do not match any operator,
    including negative operators such as NOT_CONTAINS.

    This conservative rule prevents a transaction from being mapped only
    because optional evidence was absent.

    Parameters
    ----------
    condition:
        Validated rule condition.

    transaction:
        Normalized transaction being inspected.

    Returns
    -------
    ConditionEvaluation
        Actual transaction value and deterministic match result.

    Raises
    ------
    RuleEvaluationError
        If either argument is not the expected domain type.
    """

    if not isinstance(condition, RuleCondition):
        raise RuleEvaluationError("condition must be a RuleCondition.")

    if not isinstance(transaction, NormalizedTransaction):
        raise RuleEvaluationError("transaction must be a NormalizedTransaction.")

    actual_value = _extract_actual_value(
        transaction=transaction,
        field=condition.field,
    )

    if actual_value is None:
        return ConditionEvaluation(
            condition=condition,
            actual_value=None,
            matched=False,
        )

    matched = _compare_condition(
        condition=condition,
        actual_value=actual_value,
    )

    return ConditionEvaluation(
        condition=condition,
        actual_value=actual_value,
        matched=matched,
    )


def _extract_actual_value(
    *,
    transaction: NormalizedTransaction,
    field: RuleConditionField,
) -> ConditionActualValue:
    """Extract and normalize the requested transaction attribute."""

    if field is RuleConditionField.SEARCHABLE_TEXT:
        return transaction.searchable_text

    if field is RuleConditionField.DESCRIPTION:
        return transaction.normalized_description

    if field is RuleConditionField.MEMO:
        if transaction.original_memo is None:
            return None

        return normalize_search_text(transaction.original_memo)

    if field is RuleConditionField.VENDOR:
        if transaction.vendor_name is None:
            return None

        return normalize_search_text(transaction.vendor_name)

    if field is RuleConditionField.DIRECTION:
        return transaction.direction

    if field is RuleConditionField.ABSOLUTE_AMOUNT:
        return transaction.absolute_amount

    if field is RuleConditionField.SIGNED_AMOUNT:
        return transaction.signed_amount

    if field is RuleConditionField.TRANSACTION_DATE:
        return transaction.transaction_date

    raise RuleEvaluationError(f"Unsupported transaction field: {field.value}.")


def _compare_condition(
    *,
    condition: RuleCondition,
    actual_value: ConditionActualValue,
) -> bool:
    """Dispatch comparison using the condition field type."""

    if condition.field in _TEXT_FIELDS:
        if not isinstance(actual_value, str) or isinstance(
            actual_value,
            TransactionDirection,
        ):
            raise RuleEvaluationError(
                f"Field '{condition.field.value}' did not produce text."
            )

        return _evaluate_text_condition(
            actual_value=actual_value,
            condition=condition,
        )

    if condition.field is RuleConditionField.DIRECTION:
        if not isinstance(actual_value, TransactionDirection):
            raise RuleEvaluationError(
                "Direction field did not produce TransactionDirection."
            )

        return _evaluate_direction_condition(
            actual_value=actual_value,
            condition=condition,
        )

    if condition.field in _AMOUNT_FIELDS:
        if not isinstance(actual_value, Decimal):
            raise RuleEvaluationError(
                f"Field '{condition.field.value}' did not produce Decimal."
            )

        return _evaluate_amount_condition(
            actual_value=actual_value,
            condition=condition,
        )

    if condition.field is RuleConditionField.TRANSACTION_DATE:
        if not isinstance(actual_value, date):
            raise RuleEvaluationError("Transaction-date field did not produce date.")

        return _evaluate_date_condition(
            actual_value=actual_value,
            condition=condition,
        )

    raise RuleEvaluationError(
        f"Unsupported transaction field: {condition.field.value}."
    )


def _evaluate_text_condition(
    *,
    actual_value: str,
    condition: RuleCondition,
) -> bool:
    """Evaluate a normalized text condition."""

    expected_value = condition.value

    if not isinstance(expected_value, str) or isinstance(
        expected_value,
        TransactionDirection,
    ):
        raise RuleEvaluationError("Text condition did not contain a string value.")

    if condition.operator is RuleOperator.CONTAINS:
        return expected_value in actual_value

    if condition.operator is RuleOperator.NOT_CONTAINS:
        return expected_value not in actual_value

    if condition.operator is RuleOperator.EQUALS:
        return actual_value == expected_value

    if condition.operator is RuleOperator.NOT_EQUALS:
        return actual_value != expected_value

    if condition.operator is RuleOperator.STARTS_WITH:
        return actual_value.startswith(expected_value)

    if condition.operator is RuleOperator.ENDS_WITH:
        return actual_value.endswith(expected_value)

    raise RuleEvaluationError(f"Unsupported text operator: {condition.operator.value}.")


def _evaluate_direction_condition(
    *,
    actual_value: TransactionDirection,
    condition: RuleCondition,
) -> bool:
    """Evaluate a transaction-direction condition."""

    expected_value = condition.value

    if not isinstance(expected_value, TransactionDirection):
        raise RuleEvaluationError(
            "Direction condition did not contain TransactionDirection."
        )

    if condition.operator is RuleOperator.EQUALS:
        return actual_value is expected_value

    if condition.operator is RuleOperator.NOT_EQUALS:
        return actual_value is not expected_value

    raise RuleEvaluationError(
        f"Unsupported direction operator: {condition.operator.value}."
    )


def _evaluate_amount_condition(
    *,
    actual_value: Decimal,
    condition: RuleCondition,
) -> bool:
    """Evaluate a Decimal financial-amount condition."""

    expected_value = condition.value

    if condition.operator is RuleOperator.BETWEEN:
        if not isinstance(expected_value, tuple) or len(expected_value) != 2:
            raise RuleEvaluationError(
                "Amount BETWEEN condition requires two Decimal boundaries."
            )

        lower_value, upper_value = expected_value

        if not isinstance(lower_value, Decimal) or not isinstance(
            upper_value,
            Decimal,
        ):
            raise RuleEvaluationError(
                "Amount BETWEEN condition requires two Decimal boundaries."
            )

        return lower_value <= actual_value <= upper_value

    if not isinstance(expected_value, Decimal):
        raise RuleEvaluationError("Amount condition did not contain a Decimal value.")

    if condition.operator is RuleOperator.EQUALS:
        return actual_value == expected_value

    if condition.operator is RuleOperator.NOT_EQUALS:
        return actual_value != expected_value

    if condition.operator is RuleOperator.GREATER_THAN:
        return actual_value > expected_value

    if condition.operator is RuleOperator.GREATER_THAN_OR_EQUAL:
        return actual_value >= expected_value

    if condition.operator is RuleOperator.LESS_THAN:
        return actual_value < expected_value

    if condition.operator is RuleOperator.LESS_THAN_OR_EQUAL:
        return actual_value <= expected_value

    raise RuleEvaluationError(
        f"Unsupported amount operator: {condition.operator.value}."
    )


def _evaluate_date_condition(
    *,
    actual_value: date,
    condition: RuleCondition,
) -> bool:
    """Evaluate a transaction-date condition."""

    expected_value = condition.value

    if condition.operator is RuleOperator.BETWEEN:
        if not isinstance(expected_value, tuple) or len(expected_value) != 2:
            raise RuleEvaluationError(
                "Date BETWEEN condition requires two date boundaries."
            )

        lower_value, upper_value = expected_value

        if isinstance(lower_value, datetime) or isinstance(
            upper_value,
            datetime,
        ):
            raise RuleEvaluationError(
                "Date BETWEEN condition requires two date boundaries."
            )

        if not isinstance(lower_value, date) or not isinstance(
            upper_value,
            date,
        ):
            raise RuleEvaluationError(
                "Date BETWEEN condition requires two date boundaries."
            )

        return lower_value <= actual_value <= upper_value

    if isinstance(expected_value, datetime) or not isinstance(
        expected_value,
        date,
    ):
        raise RuleEvaluationError("Date condition did not contain a date value.")

    if condition.operator is RuleOperator.EQUALS:
        return actual_value == expected_value

    if condition.operator is RuleOperator.NOT_EQUALS:
        return actual_value != expected_value

    if condition.operator is RuleOperator.GREATER_THAN:
        return actual_value > expected_value

    if condition.operator is RuleOperator.GREATER_THAN_OR_EQUAL:
        return actual_value >= expected_value

    if condition.operator is RuleOperator.LESS_THAN:
        return actual_value < expected_value

    if condition.operator is RuleOperator.LESS_THAN_OR_EQUAL:
        return actual_value <= expected_value

    raise RuleEvaluationError(f"Unsupported date operator: {condition.operator.value}.")
