"""
Unit tests for deterministic BSI rule evaluation.

These tests verify:

- Rule eligibility checks
- Transaction-field extraction
- Text, direction, amount, and date operators
- ALL and ANY rule logic
- Scope and effective-date filtering
- Conservative handling of missing optional fields
- Mapping output behavior
- Audit-friendly evaluation evidence
- Runtime validation and immutability
"""

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

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
from bsi.domain.rules.evaluator import (
    ConditionEvaluation,
    RuleEligibility,
    RuleEvaluation,
    RuleEvaluationError,
    evaluate_condition,
    evaluate_rule,
)
from bsi.domain.rules.models import (
    RuleDefinition,
    RuleOutput,
)
from bsi.domain.rules.scope import RuleScope
from bsi.domain.transactions.amounts import AmountInput
from bsi.domain.transactions.enums import TransactionDirection
from bsi.domain.transactions.models import (
    NormalizedTransaction,
    TransactionContext,
    TransactionSource,
)

DEFAULT_TRANSACTION_DATE = date(2026, 7, 15)


def _invalid(value: object) -> Any:
    """
    Bypass static typing for intentional runtime-validation tests.

    Production code should pass validated domain objects. Tests use this
    helper to verify runtime protection when invalid values reach the
    evaluator boundary.
    """

    return cast(Any, value)


def _condition(
    *,
    field: RuleConditionField = RuleConditionField.SEARCHABLE_TEXT,
    operator: RuleOperator = RuleOperator.CONTAINS,
    value: RuleConditionValue = "utility",
) -> RuleCondition:
    """Create one validated rule condition."""

    return RuleCondition(
        field=field,
        operator=operator,
        value=value,
    )


def _transaction(
    *,
    transaction_date: date = DEFAULT_TRANSACTION_DATE,
    original_description: str = "UTILITY PAYMENT",
    payment: AmountInput = "125.00",
    deposit: AmountInput = None,
    original_memo: str | None = None,
    vendor_name: str | None = None,
    context: TransactionContext | None = None,
    transaction_id: UUID | None = None,
) -> NormalizedTransaction:
    """Create one valid normalized transaction for evaluator tests."""

    return NormalizedTransaction.create(
        transaction_id=transaction_id,
        transaction_date=transaction_date,
        original_description=original_description,
        payment=payment,
        deposit=deposit,
        original_memo=original_memo,
        vendor_name=vendor_name,
        context=context,
        source=TransactionSource(
            file_name="statement.xlsx",
            source_row_number=10,
        ),
    )


def _complete_rule(
    *,
    conditions: tuple[RuleCondition, ...] | None = None,
    logic: RuleLogic = RuleLogic.ALL,
    status: RuleStatus = RuleStatus.ACTIVE,
    scope: RuleScope | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    output_account_id: UUID | None = None,
) -> RuleDefinition:
    """Create one complete deterministic rule."""

    resolved_conditions = conditions if conditions is not None else (_condition(),)

    resolved_output_account_id = (
        output_account_id if output_account_id is not None else uuid4()
    )

    return RuleDefinition.create(
        workspace_id=uuid4(),
        name="Evaluator Test Rule",
        logic=logic,
        conditions=resolved_conditions,
        output=RuleOutput(
            coa_account_id=resolved_output_account_id,
        ),
        scope=scope,
        status=status,
        effective_from=effective_from,
        effective_to=effective_to,
    )


def test_rule_eligibility_is_true_when_every_check_passes() -> None:
    """A rule is eligible only when every prerequisite passes."""

    eligibility = RuleEligibility(
        status_allows_evaluation=True,
        rule_is_complete=True,
        effective_date_matches=True,
        scope_matches=True,
    )

    assert eligibility.is_eligible is True


@pytest.mark.parametrize(
    "failed_field",
    [
        "status_allows_evaluation",
        "rule_is_complete",
        "effective_date_matches",
        "scope_matches",
    ],
)
def test_rule_eligibility_is_false_when_one_check_fails(
    failed_field: str,
) -> None:
    """One failed prerequisite makes the rule ineligible."""

    values = {
        "status_allows_evaluation": True,
        "rule_is_complete": True,
        "effective_date_matches": True,
        "scope_matches": True,
    }
    values[failed_field] = False

    eligibility = RuleEligibility(
        status_allows_evaluation=values["status_allows_evaluation"],
        rule_is_complete=values["rule_is_complete"],
        effective_date_matches=values["effective_date_matches"],
        scope_matches=values["scope_matches"],
    )

    assert eligibility.is_eligible is False


@pytest.mark.parametrize(
    "keyword",
    [
        "national dcp",
        "ach payment",
        "food supplies",
    ],
)
def test_searchable_text_matches_vendor_description_and_memo(
    keyword: str,
) -> None:
    """Searchable text combines vendor, description, and memo."""

    transaction = _transaction(
        original_description="ACH PAYMENT",
        vendor_name="National DCP",
        original_memo="Food Supplies",
    )

    condition = _condition(
        field=RuleConditionField.SEARCHABLE_TEXT,
        operator=RuleOperator.CONTAINS,
        value=keyword,
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.matched is True
    assert result.actual_value == ("national dcp | ach payment | food supplies")


@pytest.mark.parametrize(
    ("operator", "expected_value"),
    [
        (
            RuleOperator.CONTAINS,
            "dcp",
        ),
        (
            RuleOperator.NOT_CONTAINS,
            "refund",
        ),
        (
            RuleOperator.EQUALS,
            "national dcp payment",
        ),
        (
            RuleOperator.NOT_EQUALS,
            "different description",
        ),
        (
            RuleOperator.STARTS_WITH,
            "national",
        ),
        (
            RuleOperator.ENDS_WITH,
            "payment",
        ),
    ],
)
def test_description_supports_every_text_operator(
    operator: RuleOperator,
    expected_value: str,
) -> None:
    """Description conditions support every validated text operator."""

    transaction = _transaction(
        original_description="NATIONAL DCP PAYMENT",
    )

    condition = _condition(
        field=RuleConditionField.DESCRIPTION,
        operator=operator,
        value=expected_value,
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.actual_value == "national dcp payment"
    assert result.matched is True


def test_description_condition_can_fail() -> None:
    """A text condition reports false when its value is absent."""

    transaction = _transaction(
        original_description="UTILITY PAYMENT",
    )

    condition = _condition(
        field=RuleConditionField.DESCRIPTION,
        operator=RuleOperator.CONTAINS,
        value="rent",
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.actual_value == "utility payment"
    assert result.matched is False


def test_memo_is_normalized_before_evaluation() -> None:
    """Original memo text is normalized before deterministic comparison."""

    transaction = _transaction(
        original_memo="  ELECTRIC   COMPANY ",
    )

    condition = _condition(
        field=RuleConditionField.MEMO,
        operator=RuleOperator.EQUALS,
        value="electric company",
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.actual_value == "electric company"
    assert result.matched is True


def test_vendor_is_normalized_before_evaluation() -> None:
    """Vendor display text is normalized before comparison."""

    transaction = _transaction(
        vendor_name="  National   DCP ",
    )

    condition = _condition(
        field=RuleConditionField.VENDOR,
        operator=RuleOperator.EQUALS,
        value="national dcp",
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.actual_value == "national dcp"
    assert result.matched is True


@pytest.mark.parametrize(
    "field",
    [
        RuleConditionField.MEMO,
        RuleConditionField.VENDOR,
    ],
)
def test_missing_optional_text_never_creates_positive_match(
    field: RuleConditionField,
) -> None:
    """
    Missing evidence does not satisfy negative text operators.

    For example, an absent memo must not satisfy NOT_CONTAINS.
    """

    transaction = _transaction(
        original_memo=None,
        vendor_name=None,
    )

    condition = _condition(
        field=field,
        operator=RuleOperator.NOT_CONTAINS,
        value="refund",
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.actual_value is None
    assert result.matched is False


@pytest.mark.parametrize(
    ("operator", "expected_direction"),
    [
        (
            RuleOperator.EQUALS,
            TransactionDirection.PAYMENT,
        ),
        (
            RuleOperator.NOT_EQUALS,
            TransactionDirection.DEPOSIT,
        ),
    ],
)
def test_payment_direction_supports_equality_operators(
    operator: RuleOperator,
    expected_direction: TransactionDirection,
) -> None:
    """Payment transactions support equality and inequality checks."""

    transaction = _transaction(
        payment="125.00",
        deposit=None,
    )

    condition = _condition(
        field=RuleConditionField.DIRECTION,
        operator=operator,
        value=expected_direction,
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.actual_value is TransactionDirection.PAYMENT
    assert result.matched is True


def test_direction_condition_can_fail() -> None:
    """A payment does not equal the deposit direction."""

    transaction = _transaction(
        payment="125.00",
    )

    condition = _condition(
        field=RuleConditionField.DIRECTION,
        operator=RuleOperator.EQUALS,
        value=TransactionDirection.DEPOSIT,
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.matched is False


@pytest.mark.parametrize(
    ("operator", "expected_value"),
    [
        (
            RuleOperator.EQUALS,
            Decimal("125.00"),
        ),
        (
            RuleOperator.NOT_EQUALS,
            Decimal("100.00"),
        ),
        (
            RuleOperator.GREATER_THAN,
            Decimal("100.00"),
        ),
        (
            RuleOperator.GREATER_THAN_OR_EQUAL,
            Decimal("125.00"),
        ),
        (
            RuleOperator.LESS_THAN,
            Decimal("200.00"),
        ),
        (
            RuleOperator.LESS_THAN_OR_EQUAL,
            Decimal("125.00"),
        ),
    ],
)
def test_absolute_amount_supports_scalar_operators(
    operator: RuleOperator,
    expected_value: Decimal,
) -> None:
    """Absolute amounts support equality and ordered comparisons."""

    transaction = _transaction(
        payment="125.00",
    )

    condition = _condition(
        field=RuleConditionField.ABSOLUTE_AMOUNT,
        operator=operator,
        value=expected_value,
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.actual_value == Decimal("125.00")
    assert result.matched is True


@pytest.mark.parametrize(
    "boundaries",
    [
        (
            Decimal("100.00"),
            Decimal("125.00"),
        ),
        (
            Decimal("125.00"),
            Decimal("200.00"),
        ),
    ],
)
def test_amount_between_is_inclusive(
    boundaries: tuple[Decimal, Decimal],
) -> None:
    """BETWEEN includes both lower and upper amount boundaries."""

    transaction = _transaction(
        payment="125.00",
    )

    condition = _condition(
        field=RuleConditionField.ABSOLUTE_AMOUNT,
        operator=RuleOperator.BETWEEN,
        value=boundaries,
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.matched is True


def test_amount_between_can_fail() -> None:
    """An amount outside the configured range does not match."""

    transaction = _transaction(
        payment="125.00",
    )

    condition = _condition(
        field=RuleConditionField.ABSOLUTE_AMOUNT,
        operator=RuleOperator.BETWEEN,
        value=(
            Decimal("200.00"),
            Decimal("300.00"),
        ),
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.matched is False


def test_signed_amount_uses_negative_value_for_payment() -> None:
    """Payment signed amounts are negative cash movements."""

    transaction = _transaction(
        payment="125.00",
    )

    condition = _condition(
        field=RuleConditionField.SIGNED_AMOUNT,
        operator=RuleOperator.LESS_THAN,
        value=Decimal("-100.00"),
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.actual_value == Decimal("-125.00")
    assert result.matched is True


def test_signed_amount_uses_positive_value_for_deposit() -> None:
    """Deposit signed amounts are positive cash movements."""

    transaction = _transaction(
        payment=None,
        deposit="500.00",
    )

    condition = _condition(
        field=RuleConditionField.SIGNED_AMOUNT,
        operator=RuleOperator.GREATER_THAN,
        value=Decimal("0.00"),
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.actual_value == Decimal("500.00")
    assert result.matched is True


@pytest.mark.parametrize(
    ("operator", "expected_value"),
    [
        (
            RuleOperator.EQUALS,
            date(2026, 7, 15),
        ),
        (
            RuleOperator.NOT_EQUALS,
            date(2026, 7, 14),
        ),
        (
            RuleOperator.GREATER_THAN,
            date(2026, 7, 14),
        ),
        (
            RuleOperator.GREATER_THAN_OR_EQUAL,
            date(2026, 7, 15),
        ),
        (
            RuleOperator.LESS_THAN,
            date(2026, 7, 16),
        ),
        (
            RuleOperator.LESS_THAN_OR_EQUAL,
            date(2026, 7, 15),
        ),
    ],
)
def test_transaction_date_supports_scalar_operators(
    operator: RuleOperator,
    expected_value: date,
) -> None:
    """Transaction dates support equality and ordered comparisons."""

    transaction = _transaction(
        transaction_date=date(2026, 7, 15),
    )

    condition = _condition(
        field=RuleConditionField.TRANSACTION_DATE,
        operator=operator,
        value=expected_value,
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.actual_value == date(2026, 7, 15)
    assert result.matched is True


@pytest.mark.parametrize(
    "boundaries",
    [
        (
            date(2026, 7, 1),
            date(2026, 7, 15),
        ),
        (
            date(2026, 7, 15),
            date(2026, 7, 31),
        ),
    ],
)
def test_date_between_is_inclusive(
    boundaries: tuple[date, date],
) -> None:
    """BETWEEN includes both date boundaries."""

    transaction = _transaction(
        transaction_date=date(2026, 7, 15),
    )

    condition = _condition(
        field=RuleConditionField.TRANSACTION_DATE,
        operator=RuleOperator.BETWEEN,
        value=boundaries,
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.matched is True


def test_date_between_can_fail() -> None:
    """A transaction date outside the range does not match."""

    transaction = _transaction(
        transaction_date=date(2026, 7, 15),
    )

    condition = _condition(
        field=RuleConditionField.TRANSACTION_DATE,
        operator=RuleOperator.BETWEEN,
        value=(
            date(2026, 8, 1),
            date(2026, 8, 31),
        ),
    )

    result = evaluate_condition(
        condition=condition,
        transaction=transaction,
    )

    assert result.matched is False


def test_all_logic_matches_when_every_condition_matches() -> None:
    """ALL represents deterministic logical AND."""

    transaction = _transaction(
        original_description="NATIONAL DCP PAYMENT",
        payment="1250.00",
    )

    conditions = (
        _condition(
            field=RuleConditionField.SEARCHABLE_TEXT,
            operator=RuleOperator.CONTAINS,
            value="national dcp",
        ),
        _condition(
            field=RuleConditionField.DIRECTION,
            operator=RuleOperator.EQUALS,
            value=TransactionDirection.PAYMENT,
        ),
        _condition(
            field=RuleConditionField.ABSOLUTE_AMOUNT,
            operator=RuleOperator.GREATER_THAN,
            value=Decimal("1000.00"),
        ),
    )

    result = evaluate_rule(
        rule=_complete_rule(
            conditions=conditions,
            logic=RuleLogic.ALL,
        ),
        transaction=transaction,
    )

    assert result.matched is True
    assert result.evaluated_condition_count == 3
    assert result.matched_condition_count == 3
    assert result.failed_condition_count == 0


def test_all_logic_fails_when_one_condition_fails() -> None:
    """ALL fails when any individual condition fails."""

    transaction = _transaction(
        original_description="NATIONAL DCP PAYMENT",
        payment="125.00",
    )

    conditions = (
        _condition(
            field=RuleConditionField.SEARCHABLE_TEXT,
            operator=RuleOperator.CONTAINS,
            value="national dcp",
        ),
        _condition(
            field=RuleConditionField.ABSOLUTE_AMOUNT,
            operator=RuleOperator.GREATER_THAN,
            value=Decimal("1000.00"),
        ),
    )

    result = evaluate_rule(
        rule=_complete_rule(
            conditions=conditions,
            logic=RuleLogic.ALL,
        ),
        transaction=transaction,
    )

    assert result.matched is False
    assert result.matched_condition_count == 1
    assert result.failed_condition_count == 1


def test_any_logic_matches_when_one_condition_matches() -> None:
    """ANY represents deterministic logical OR."""

    transaction = _transaction(
        original_description="UTILITY PAYMENT",
    )

    conditions = (
        _condition(
            field=RuleConditionField.DESCRIPTION,
            operator=RuleOperator.CONTAINS,
            value="rent",
        ),
        _condition(
            field=RuleConditionField.DESCRIPTION,
            operator=RuleOperator.CONTAINS,
            value="utility",
        ),
    )

    result = evaluate_rule(
        rule=_complete_rule(
            conditions=conditions,
            logic=RuleLogic.ANY,
        ),
        transaction=transaction,
    )

    assert result.matched is True
    assert result.matched_condition_count == 1
    assert result.failed_condition_count == 1


def test_any_logic_fails_when_every_condition_fails() -> None:
    """ANY fails when none of its conditions match."""

    transaction = _transaction(
        original_description="UTILITY PAYMENT",
    )

    conditions = (
        _condition(
            field=RuleConditionField.DESCRIPTION,
            operator=RuleOperator.CONTAINS,
            value="rent",
        ),
        _condition(
            field=RuleConditionField.DESCRIPTION,
            operator=RuleOperator.CONTAINS,
            value="insurance",
        ),
    )

    result = evaluate_rule(
        rule=_complete_rule(
            conditions=conditions,
            logic=RuleLogic.ANY,
        ),
        transaction=transaction,
    )

    assert result.matched is False
    assert result.matched_condition_count == 0
    assert result.failed_condition_count == 2


@pytest.mark.parametrize(
    "status",
    [
        RuleStatus.DRAFT,
        RuleStatus.PENDING_APPROVAL,
        RuleStatus.PAUSED,
        RuleStatus.RETIRED,
    ],
)
def test_non_active_rule_is_ineligible_and_skips_conditions(
    status: RuleStatus,
) -> None:
    """Only ACTIVE rules evaluate transaction conditions."""

    rule = _complete_rule(
        status=status,
    )

    result = evaluate_rule(
        rule=rule,
        transaction=_transaction(),
    )

    assert result.eligibility.status_allows_evaluation is False
    assert result.eligibility.is_eligible is False
    assert result.condition_evaluations == ()
    assert result.evaluated_condition_count == 0
    assert result.matched is False


def test_incomplete_draft_rule_is_ineligible() -> None:
    """An incomplete draft cannot evaluate financial transactions."""

    rule = RuleDefinition.create(
        workspace_id=uuid4(),
        name="Incomplete Draft",
    )

    result = evaluate_rule(
        rule=rule,
        transaction=_transaction(),
    )

    assert result.eligibility.rule_is_complete is False
    assert result.eligibility.is_eligible is False
    assert result.condition_evaluations == ()
    assert result.matched is False


@pytest.mark.parametrize(
    "transaction_date",
    [
        date(2026, 6, 30),
        date(2026, 8, 1),
    ],
)
def test_rule_outside_effective_window_skips_conditions(
    transaction_date: date,
) -> None:
    """Out-of-date rules do not evaluate their conditions."""

    rule = _complete_rule(
        effective_from=date(2026, 7, 1),
        effective_to=date(2026, 7, 31),
    )

    result = evaluate_rule(
        rule=rule,
        transaction=_transaction(
            transaction_date=transaction_date,
        ),
    )

    assert result.eligibility.effective_date_matches is False
    assert result.eligibility.is_eligible is False
    assert result.condition_evaluations == ()
    assert result.matched is False


def test_scope_mismatch_skips_conditions() -> None:
    """A store-specific rule cannot evaluate another store's transaction."""

    company_id = uuid4()
    expected_store_id = uuid4()

    rule = _complete_rule(
        scope=RuleScope(
            company_id=company_id,
            store_id=expected_store_id,
        ),
    )

    transaction = _transaction(
        context=TransactionContext(
            company_id=company_id,
            store_id=uuid4(),
        )
    )

    result = evaluate_rule(
        rule=rule,
        transaction=transaction,
    )

    assert result.eligibility.scope_matches is False
    assert result.eligibility.is_eligible is False
    assert result.condition_evaluations == ()
    assert result.matched is False


def test_matching_scope_allows_condition_evaluation() -> None:
    """Matching organizational context allows evaluation to continue."""

    company_id = uuid4()
    store_id = uuid4()

    rule = _complete_rule(
        scope=RuleScope(
            company_id=company_id,
            store_id=store_id,
        ),
    )

    transaction = _transaction(
        context=TransactionContext(
            company_id=company_id,
            store_id=store_id,
        )
    )

    result = evaluate_rule(
        rule=rule,
        transaction=transaction,
    )

    assert result.eligibility.scope_matches is True
    assert result.eligibility.is_eligible is True
    assert result.evaluated_condition_count == 1


def test_global_scope_matches_any_transaction_context() -> None:
    """A global rule can evaluate a populated transaction context."""

    rule = _complete_rule(
        scope=RuleScope(),
    )

    transaction = _transaction(
        context=TransactionContext(
            company_id=uuid4(),
            brand_id=uuid4(),
            store_id=uuid4(),
            bank_account_id=uuid4(),
        )
    )

    result = evaluate_rule(
        rule=rule,
        transaction=transaction,
    )

    assert result.eligibility.scope_matches is True
    assert result.eligibility.is_eligible is True


def test_successful_rule_returns_output_account_id() -> None:
    """A matched rule exposes its deterministic COA mapping output."""

    output_account_id = uuid4()

    result = evaluate_rule(
        rule=_complete_rule(
            output_account_id=output_account_id,
        ),
        transaction=_transaction(),
    )

    assert result.matched is True
    assert result.output_account_id == output_account_id


def test_failed_rule_does_not_return_output_account_id() -> None:
    """A failed rule cannot expose a financial mapping."""

    rule = _complete_rule(
        conditions=(
            _condition(
                field=RuleConditionField.DESCRIPTION,
                operator=RuleOperator.CONTAINS,
                value="rent",
            ),
        ),
    )

    result = evaluate_rule(
        rule=rule,
        transaction=_transaction(
            original_description="UTILITY PAYMENT",
        ),
    )

    assert result.matched is False
    assert result.output_account_id is None


def test_rule_evaluation_preserves_transaction_identifier() -> None:
    """Evaluation evidence references the exact transaction inspected."""

    transaction_id = uuid4()
    transaction = _transaction(
        transaction_id=transaction_id,
    )

    result = evaluate_rule(
        rule=_complete_rule(),
        transaction=transaction,
    )

    assert result.transaction_id == transaction_id


def test_condition_evaluation_order_matches_rule_order() -> None:
    """Audit evidence preserves the configured condition order."""

    first_condition = _condition(
        field=RuleConditionField.DESCRIPTION,
        operator=RuleOperator.CONTAINS,
        value="utility",
    )
    second_condition = _condition(
        field=RuleConditionField.DIRECTION,
        operator=RuleOperator.EQUALS,
        value=TransactionDirection.PAYMENT,
    )
    third_condition = _condition(
        field=RuleConditionField.ABSOLUTE_AMOUNT,
        operator=RuleOperator.GREATER_THAN,
        value=Decimal("100.00"),
    )

    result = evaluate_rule(
        rule=_complete_rule(
            conditions=(
                first_condition,
                second_condition,
                third_condition,
            ),
        ),
        transaction=_transaction(
            payment="125.00",
        ),
    )

    assert tuple(
        evaluation.condition for evaluation in result.condition_evaluations
    ) == (
        first_condition,
        second_condition,
        third_condition,
    )


def test_evaluate_rule_rejects_invalid_rule() -> None:
    """Rule evaluation requires the authoritative rule-domain model."""

    with pytest.raises(
        RuleEvaluationError,
        match="rule must be a RuleDefinition",
    ):
        evaluate_rule(
            rule=_invalid({}),
            transaction=_transaction(),
        )


def test_evaluate_rule_rejects_invalid_transaction() -> None:
    """Rule evaluation requires a normalized transaction."""

    with pytest.raises(
        RuleEvaluationError,
        match="transaction must be a NormalizedTransaction",
    ):
        evaluate_rule(
            rule=_complete_rule(),
            transaction=_invalid({}),
        )


def test_evaluate_condition_rejects_invalid_condition() -> None:
    """Condition evaluation requires a validated RuleCondition."""

    with pytest.raises(
        RuleEvaluationError,
        match="condition must be a RuleCondition",
    ):
        evaluate_condition(
            condition=_invalid({}),
            transaction=_transaction(),
        )


def test_evaluate_condition_rejects_invalid_transaction() -> None:
    """Condition evaluation rejects arbitrary transaction values."""

    with pytest.raises(
        RuleEvaluationError,
        match="transaction must be a NormalizedTransaction",
    ):
        evaluate_condition(
            condition=_condition(),
            transaction=_invalid({}),
        )


def test_rule_eligibility_is_immutable() -> None:
    """Eligibility evidence cannot be modified after creation."""

    eligibility = RuleEligibility(
        status_allows_evaluation=True,
        rule_is_complete=True,
        effective_date_matches=True,
        scope_matches=True,
    )
    eligibility_for_mutation = cast(Any, eligibility)

    with pytest.raises(FrozenInstanceError):
        eligibility_for_mutation.scope_matches = False


def test_condition_evaluation_is_immutable() -> None:
    """Condition evidence cannot be changed after evaluation."""

    result = evaluate_condition(
        condition=_condition(),
        transaction=_transaction(),
    )
    result_for_mutation = cast(Any, result)

    with pytest.raises(FrozenInstanceError):
        result_for_mutation.matched = False


def test_rule_evaluation_is_immutable() -> None:
    """Complete rule-evaluation evidence cannot be modified."""

    result = evaluate_rule(
        rule=_complete_rule(),
        transaction=_transaction(),
    )
    result_for_mutation = cast(Any, result)

    with pytest.raises(FrozenInstanceError):
        result_for_mutation.transaction_id = uuid4()


def test_condition_result_uses_condition_evaluation_type() -> None:
    """Public condition evaluation returns the expected evidence model."""

    result = evaluate_condition(
        condition=_condition(),
        transaction=_transaction(),
    )

    assert isinstance(result, ConditionEvaluation)


def test_rule_result_uses_rule_evaluation_type() -> None:
    """Public rule evaluation returns the expected evidence model."""

    result = evaluate_rule(
        rule=_complete_rule(),
        transaction=_transaction(),
    )

    assert isinstance(result, RuleEvaluation)
