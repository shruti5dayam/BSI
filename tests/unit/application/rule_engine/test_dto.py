"""
Unit tests for application-facing BSI rule-engine DTOs.

These tests verify:

- Eligibility evidence conversion
- Condition evidence serialization
- Decimal, date, direction, text, and BETWEEN values
- Rule-evaluation conversion
- Final engine-decision conversion
- Mapping, unmatched, review, and conflict decisions
- Nested audit evidence
- Runtime validation
- DTO immutability
"""

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from bsi.application.rule_engine.dto import (
    RuleConditionEvaluationDTO,
    RuleEligibilityDTO,
    RuleEngineDecisionDTO,
    RuleEngineDTOError,
    RuleEvaluationDTO,
)
from bsi.domain.rules.conditions import (
    RuleCondition,
    RuleConditionValue,
)
from bsi.domain.rules.engine import (
    RuleDecisionStatus,
    evaluate_transaction_rules,
)
from bsi.domain.rules.enums import (
    RuleConditionField,
    RuleLogic,
    RuleOperator,
    RuleStatus,
)
from bsi.domain.rules.evaluator import (
    evaluate_condition,
    evaluate_rule,
)
from bsi.domain.rules.models import (
    RuleDefinition,
    RuleOutput,
)
from bsi.domain.transactions.enums import TransactionDirection
from bsi.domain.transactions.models import (
    NormalizedTransaction,
    TransactionSource,
)

DEFAULT_TRANSACTION_DATE = date(2026, 7, 15)


def _invalid(value: object) -> Any:
    """
    Bypass static typing for intentional runtime-validation tests.

    Application code should provide validated domain evidence. These
    tests confirm that invalid runtime values are rejected safely.
    """

    return cast(Any, value)


def _condition(
    *,
    field: RuleConditionField = RuleConditionField.SEARCHABLE_TEXT,
    operator: RuleOperator = RuleOperator.CONTAINS,
    value: RuleConditionValue = "utility",
) -> RuleCondition:
    """Create one validated deterministic rule condition."""

    return RuleCondition(
        field=field,
        operator=operator,
        value=value,
    )


def _transaction(
    *,
    transaction_id: UUID | None = None,
    transaction_date: date = DEFAULT_TRANSACTION_DATE,
    description: str = "UTILITY PAYMENT",
    payment: str | None = "125.00",
    deposit: str | None = None,
    original_memo: str | None = None,
    vendor_name: str | None = None,
) -> NormalizedTransaction:
    """Create one normalized transaction for DTO tests."""

    return NormalizedTransaction.create(
        transaction_id=transaction_id,
        transaction_date=transaction_date,
        original_description=description,
        payment=payment,
        deposit=deposit,
        original_memo=original_memo,
        vendor_name=vendor_name,
        source=TransactionSource(
            file_name="statement.xlsx",
            source_row_number=10,
        ),
    )


def _rule(
    *,
    workspace_id: UUID,
    name: str = "Utility Rule",
    rule_id: UUID | None = None,
    conditions: tuple[RuleCondition, ...] | None = None,
    output_account_id: UUID | None = None,
    status: RuleStatus = RuleStatus.ACTIVE,
    logic: RuleLogic = RuleLogic.ALL,
    priority: int = 100,
) -> RuleDefinition:
    """Create one complete deterministic rule."""

    resolved_conditions = conditions if conditions is not None else (_condition(),)

    resolved_output_account_id = (
        output_account_id if output_account_id is not None else uuid4()
    )

    return RuleDefinition.create(
        rule_id=rule_id,
        workspace_id=workspace_id,
        name=name,
        conditions=resolved_conditions,
        output=RuleOutput(
            coa_account_id=resolved_output_account_id,
        ),
        status=status,
        logic=logic,
        priority=priority,
    )


def test_eligibility_dto_converts_eligible_domain_evidence() -> None:
    """All successful eligibility checks are preserved."""

    workspace_id = uuid4()
    transaction = _transaction()

    evaluation = evaluate_rule(
        rule=_rule(workspace_id=workspace_id),
        transaction=transaction,
    )

    dto = RuleEligibilityDTO.from_domain(evaluation.eligibility)

    assert dto.status_allows_evaluation is True
    assert dto.rule_is_complete is True
    assert dto.effective_date_matches is True
    assert dto.scope_matches is True
    assert dto.is_eligible is True


def test_eligibility_dto_converts_ineligible_domain_evidence() -> None:
    """Failed lifecycle eligibility remains visible to the application."""

    workspace_id = uuid4()

    evaluation = evaluate_rule(
        rule=_rule(
            workspace_id=workspace_id,
            status=RuleStatus.PAUSED,
        ),
        transaction=_transaction(),
    )

    dto = RuleEligibilityDTO.from_domain(evaluation.eligibility)

    assert dto.status_allows_evaluation is False
    assert dto.rule_is_complete is True
    assert dto.is_eligible is False


def test_text_condition_evaluation_is_converted() -> None:
    """Text evidence is transferred using normalized string values."""

    condition = _condition(
        field=RuleConditionField.DESCRIPTION,
        operator=RuleOperator.CONTAINS,
        value="utility",
    )

    evaluation = evaluate_condition(
        condition=condition,
        transaction=_transaction(),
    )

    dto = RuleConditionEvaluationDTO.from_domain(evaluation)

    assert dto.field == RuleConditionField.DESCRIPTION.value
    assert dto.operator == RuleOperator.CONTAINS.value
    assert dto.expected_value == "utility"
    assert dto.actual_value == "utility payment"
    assert dto.matched is True


def test_decimal_condition_values_are_serialized_without_float_conversion() -> None:
    """Financial values retain exact Decimal precision as strings."""

    condition = _condition(
        field=RuleConditionField.ABSOLUTE_AMOUNT,
        operator=RuleOperator.GREATER_THAN,
        value=Decimal("100.00"),
    )

    evaluation = evaluate_condition(
        condition=condition,
        transaction=_transaction(
            payment="125.50",
        ),
    )

    dto = RuleConditionEvaluationDTO.from_domain(evaluation)

    assert dto.expected_value == "100.00"
    assert dto.actual_value == "125.50"
    assert dto.matched is True


def test_direction_values_are_serialized_as_enum_values() -> None:
    """Transaction directions become stable lowercase strings."""

    condition = _condition(
        field=RuleConditionField.DIRECTION,
        operator=RuleOperator.EQUALS,
        value=TransactionDirection.PAYMENT,
    )

    evaluation = evaluate_condition(
        condition=condition,
        transaction=_transaction(
            payment="125.00",
        ),
    )

    dto = RuleConditionEvaluationDTO.from_domain(evaluation)

    assert dto.expected_value == "payment"
    assert dto.actual_value == "payment"
    assert dto.matched is True


def test_date_values_are_serialized_using_iso_format() -> None:
    """Transaction dates use predictable ISO-8601 strings."""

    condition = _condition(
        field=RuleConditionField.TRANSACTION_DATE,
        operator=RuleOperator.EQUALS,
        value=date(2026, 7, 15),
    )

    evaluation = evaluate_condition(
        condition=condition,
        transaction=_transaction(
            transaction_date=date(2026, 7, 15),
        ),
    )

    dto = RuleConditionEvaluationDTO.from_domain(evaluation)

    assert dto.expected_value == "2026-07-15"
    assert dto.actual_value == "2026-07-15"
    assert dto.matched is True


def test_decimal_between_boundaries_are_serialized() -> None:
    """Financial BETWEEN conditions become two-item string tuples."""

    condition = _condition(
        field=RuleConditionField.ABSOLUTE_AMOUNT,
        operator=RuleOperator.BETWEEN,
        value=(
            Decimal("100.00"),
            Decimal("200.00"),
        ),
    )

    evaluation = evaluate_condition(
        condition=condition,
        transaction=_transaction(
            payment="125.00",
        ),
    )

    dto = RuleConditionEvaluationDTO.from_domain(evaluation)

    assert dto.expected_value == (
        "100.00",
        "200.00",
    )
    assert dto.actual_value == "125.00"
    assert dto.matched is True


def test_date_between_boundaries_are_serialized() -> None:
    """Date BETWEEN conditions become ISO-formatted tuples."""

    condition = _condition(
        field=RuleConditionField.TRANSACTION_DATE,
        operator=RuleOperator.BETWEEN,
        value=(
            date(2026, 7, 1),
            date(2026, 7, 31),
        ),
    )

    evaluation = evaluate_condition(
        condition=condition,
        transaction=_transaction(
            transaction_date=date(2026, 7, 15),
        ),
    )

    dto = RuleConditionEvaluationDTO.from_domain(evaluation)

    assert dto.expected_value == (
        "2026-07-01",
        "2026-07-31",
    )
    assert dto.actual_value == "2026-07-15"
    assert dto.matched is True


def test_missing_optional_actual_value_becomes_none() -> None:
    """Missing memo or vendor evidence remains explicitly absent."""

    condition = _condition(
        field=RuleConditionField.MEMO,
        operator=RuleOperator.NOT_CONTAINS,
        value="refund",
    )

    evaluation = evaluate_condition(
        condition=condition,
        transaction=_transaction(
            original_memo=None,
        ),
    )

    dto = RuleConditionEvaluationDTO.from_domain(evaluation)

    assert dto.expected_value == "refund"
    assert dto.actual_value is None
    assert dto.matched is False


def test_rule_evaluation_dto_preserves_rule_configuration() -> None:
    """Rule identity, status, logic, priority, and output are preserved."""

    workspace_id = uuid4()
    rule_id = uuid4()
    output_account_id = uuid4()

    rule = _rule(
        workspace_id=workspace_id,
        rule_id=rule_id,
        name="Utility Payment Rule",
        output_account_id=output_account_id,
        priority=450,
    )

    evaluation = evaluate_rule(
        rule=rule,
        transaction=_transaction(),
    )

    dto = RuleEvaluationDTO.from_domain(evaluation)

    assert dto.rule_id == rule_id
    assert dto.workspace_id == workspace_id
    assert dto.rule_name == "Utility Payment Rule"
    assert dto.rule_status == RuleStatus.ACTIVE.value
    assert dto.rule_logic == RuleLogic.ALL.value
    assert dto.priority == 450
    assert dto.scope_specificity == 0
    assert dto.configured_output_account_id == output_account_id


def test_matched_rule_evaluation_dto_contains_output_and_counts() -> None:
    """Successful evaluation evidence includes its mapping output."""

    workspace_id = uuid4()
    output_account_id = uuid4()

    rule = _rule(
        workspace_id=workspace_id,
        output_account_id=output_account_id,
        conditions=(
            _condition(
                field=RuleConditionField.DESCRIPTION,
                operator=RuleOperator.CONTAINS,
                value="utility",
            ),
            _condition(
                field=RuleConditionField.DIRECTION,
                operator=RuleOperator.EQUALS,
                value=TransactionDirection.PAYMENT,
            ),
        ),
    )

    evaluation = evaluate_rule(
        rule=rule,
        transaction=_transaction(),
    )

    dto = RuleEvaluationDTO.from_domain(evaluation)

    assert dto.matched is True
    assert dto.matched_output_account_id == output_account_id
    assert dto.evaluated_condition_count == 2
    assert dto.matched_condition_count == 2
    assert dto.failed_condition_count == 0
    assert len(dto.conditions) == 2
    assert dto.eligibility.is_eligible is True


def test_unmatched_rule_dto_hides_matched_output() -> None:
    """Configured output remains visible, but no matched output is emitted."""

    workspace_id = uuid4()
    output_account_id = uuid4()

    evaluation = evaluate_rule(
        rule=_rule(
            workspace_id=workspace_id,
            output_account_id=output_account_id,
            conditions=(
                _condition(
                    field=RuleConditionField.DESCRIPTION,
                    operator=RuleOperator.CONTAINS,
                    value="rent",
                ),
            ),
        ),
        transaction=_transaction(
            description="UTILITY PAYMENT",
        ),
    )

    dto = RuleEvaluationDTO.from_domain(evaluation)

    assert dto.configured_output_account_id == output_account_id
    assert dto.matched_output_account_id is None
    assert dto.matched is False
    assert dto.evaluated_condition_count == 1
    assert dto.matched_condition_count == 0
    assert dto.failed_condition_count == 1


def test_ineligible_rule_dto_has_no_condition_evidence() -> None:
    """Conditions skipped by the evaluator remain absent from the DTO."""

    workspace_id = uuid4()

    evaluation = evaluate_rule(
        rule=_rule(
            workspace_id=workspace_id,
            status=RuleStatus.PAUSED,
        ),
        transaction=_transaction(),
    )

    dto = RuleEvaluationDTO.from_domain(evaluation)

    assert dto.eligibility.is_eligible is False
    assert dto.conditions == ()
    assert dto.evaluated_condition_count == 0
    assert dto.matched_condition_count == 0
    assert dto.failed_condition_count == 0
    assert dto.matched is False


def test_mapped_engine_result_converts_to_decision_dto() -> None:
    """A unique domain mapping becomes a mapped application decision."""

    workspace_id = uuid4()
    account_id = uuid4()
    rule = _rule(
        workspace_id=workspace_id,
        output_account_id=account_id,
    )
    transaction = _transaction()

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(rule,),
    )

    dto = RuleEngineDecisionDTO.from_domain(result)

    assert dto.workspace_id == workspace_id
    assert dto.transaction_id == transaction.transaction_id
    assert dto.status == RuleDecisionStatus.MAPPED.value
    assert dto.conflict_kind == "none"
    assert dto.can_map is True
    assert dto.requires_review is False
    assert dto.is_conflict_blocked is False
    assert dto.output_account_id == account_id
    assert dto.winning_rule_id == rule.rule_id
    assert dto.matched_rule_ids == (rule.rule_id,)
    assert dto.top_rule_ids == (rule.rule_id,)


def test_unmatched_engine_result_converts_to_decision_dto() -> None:
    """An unmatched domain result remains reviewable and unmapped."""

    workspace_id = uuid4()

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=_transaction(),
        rules=(
            _rule(
                workspace_id=workspace_id,
                conditions=(
                    _condition(
                        field=RuleConditionField.DESCRIPTION,
                        operator=RuleOperator.CONTAINS,
                        value="rent",
                    ),
                ),
            ),
        ),
    )

    dto = RuleEngineDecisionDTO.from_domain(result)

    assert dto.status == RuleDecisionStatus.UNMATCHED.value
    assert dto.can_map is False
    assert dto.requires_review is True
    assert dto.is_conflict_blocked is False
    assert dto.output_account_id is None
    assert dto.winning_rule_id is None
    assert dto.matched_rule_count == 0


def test_same_output_tie_converts_to_mapped_with_review() -> None:
    """Redundant rules retain a safe output and review requirement."""

    workspace_id = uuid4()
    shared_account_id = uuid4()
    transaction = _transaction()

    first_rule = _rule(
        workspace_id=workspace_id,
        name="Utility Rule A",
        output_account_id=shared_account_id,
    )
    second_rule = _rule(
        workspace_id=workspace_id,
        name="Utility Rule B",
        output_account_id=shared_account_id,
    )

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(
            first_rule,
            second_rule,
        ),
    )

    dto = RuleEngineDecisionDTO.from_domain(result)

    assert dto.status == RuleDecisionStatus.MAPPED_WITH_REVIEW.value
    assert dto.conflict_kind == "redundant_same_output"
    assert dto.can_map is True
    assert dto.requires_review is True
    assert dto.is_conflict_blocked is False
    assert dto.output_account_id == shared_account_id
    assert dto.winning_rule_id is None
    assert dto.matched_rule_count == 2


def test_competing_outputs_convert_to_blocked_decision() -> None:
    """Different top-ranked outputs remain blocked in the application DTO."""

    workspace_id = uuid4()
    transaction = _transaction()

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(
            _rule(
                workspace_id=workspace_id,
                name="Utility Rule",
                output_account_id=uuid4(),
            ),
            _rule(
                workspace_id=workspace_id,
                name="Repairs Rule",
                output_account_id=uuid4(),
            ),
        ),
    )

    dto = RuleEngineDecisionDTO.from_domain(result)

    assert dto.status == RuleDecisionStatus.BLOCKED_CONFLICT.value
    assert dto.conflict_kind == "competing_outputs"
    assert dto.can_map is False
    assert dto.requires_review is True
    assert dto.is_conflict_blocked is True
    assert dto.output_account_id is None
    assert dto.winning_rule_id is None


def test_decision_dto_preserves_engine_counts_and_message() -> None:
    """Application decisions retain complete engine summary evidence."""

    workspace_id = uuid4()
    transaction = _transaction()

    matched_rule = _rule(
        workspace_id=workspace_id,
        name="Matched Rule",
    )
    unmatched_rule = _rule(
        workspace_id=workspace_id,
        name="Unmatched Rule",
        conditions=(
            _condition(
                field=RuleConditionField.DESCRIPTION,
                operator=RuleOperator.CONTAINS,
                value="rent",
            ),
        ),
    )
    paused_rule = _rule(
        workspace_id=workspace_id,
        name="Paused Rule",
        status=RuleStatus.PAUSED,
    )

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(
            matched_rule,
            unmatched_rule,
            paused_rule,
        ),
    )

    dto = RuleEngineDecisionDTO.from_domain(result)

    assert dto.evaluated_rule_count == 3
    assert dto.eligible_rule_count == 2
    assert dto.ineligible_rule_count == 1
    assert dto.matched_rule_count == 1
    assert dto.unmatched_eligible_rule_count == 1
    assert dto.decision_message == ("One uniquely ranked deterministic rule matched.")
    assert len(dto.evaluations) == 3


def test_decision_dto_preserves_stable_evaluation_order() -> None:
    """Nested evaluations follow the engine's stable rule-ID ordering."""

    workspace_id = uuid4()
    transaction = _transaction()

    first_rule_id = UUID(int=1)
    second_rule_id = UUID(int=2)

    second_rule = _rule(
        workspace_id=workspace_id,
        name="Second Rule",
        rule_id=second_rule_id,
    )
    first_rule = _rule(
        workspace_id=workspace_id,
        name="First Rule",
        rule_id=first_rule_id,
    )

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(
            second_rule,
            first_rule,
        ),
    )

    dto = RuleEngineDecisionDTO.from_domain(result)

    assert tuple(evaluation.rule_id for evaluation in dto.evaluations) == (
        first_rule_id,
        second_rule_id,
    )


def test_empty_rule_result_converts_successfully() -> None:
    """No configured rules still produces a valid decision DTO."""

    workspace_id = uuid4()
    transaction = _transaction()

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(),
    )

    dto = RuleEngineDecisionDTO.from_domain(result)

    assert dto.status == RuleDecisionStatus.UNMATCHED.value
    assert dto.evaluations == ()
    assert dto.matched_rule_ids == ()
    assert dto.top_rule_ids == ()
    assert dto.evaluated_rule_count == 0


def test_eligibility_dto_rejects_invalid_domain_value() -> None:
    """Eligibility conversion requires RuleEligibility evidence."""

    with pytest.raises(
        RuleEngineDTOError,
        match="eligibility must be a RuleEligibility",
    ):
        RuleEligibilityDTO.from_domain(
            _invalid({}),
        )


def test_condition_dto_rejects_invalid_domain_value() -> None:
    """Condition conversion requires ConditionEvaluation evidence."""

    with pytest.raises(
        RuleEngineDTOError,
        match="evaluation must be a ConditionEvaluation",
    ):
        RuleConditionEvaluationDTO.from_domain(
            _invalid({}),
        )


def test_rule_evaluation_dto_rejects_invalid_domain_value() -> None:
    """Rule conversion requires RuleEvaluation evidence."""

    with pytest.raises(
        RuleEngineDTOError,
        match="evaluation must be a RuleEvaluation",
    ):
        RuleEvaluationDTO.from_domain(
            _invalid({}),
        )


def test_decision_dto_rejects_invalid_domain_result() -> None:
    """Decision conversion requires RuleEngineResult evidence."""

    with pytest.raises(
        RuleEngineDTOError,
        match="result must be a RuleEngineResult",
    ):
        RuleEngineDecisionDTO.from_domain(
            _invalid({}),
        )


def test_eligibility_dto_is_immutable() -> None:
    """Application eligibility evidence cannot be modified."""

    workspace_id = uuid4()

    evaluation = evaluate_rule(
        rule=_rule(workspace_id=workspace_id),
        transaction=_transaction(),
    )

    dto = RuleEligibilityDTO.from_domain(evaluation.eligibility)
    dto_for_mutation = cast(Any, dto)

    with pytest.raises(FrozenInstanceError):
        dto_for_mutation.is_eligible = False


def test_condition_dto_is_immutable() -> None:
    """Application condition evidence cannot be modified."""

    evaluation = evaluate_condition(
        condition=_condition(),
        transaction=_transaction(),
    )

    dto = RuleConditionEvaluationDTO.from_domain(evaluation)
    dto_for_mutation = cast(Any, dto)

    with pytest.raises(FrozenInstanceError):
        dto_for_mutation.matched = False


def test_rule_evaluation_dto_is_immutable() -> None:
    """Application rule evidence cannot be modified."""

    workspace_id = uuid4()

    evaluation = evaluate_rule(
        rule=_rule(workspace_id=workspace_id),
        transaction=_transaction(),
    )

    dto = RuleEvaluationDTO.from_domain(evaluation)
    dto_for_mutation = cast(Any, dto)

    with pytest.raises(FrozenInstanceError):
        dto_for_mutation.priority = 500


def test_decision_dto_is_immutable() -> None:
    """Final application decision evidence cannot be modified."""

    workspace_id = uuid4()

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=_transaction(),
        rules=(),
    )

    dto = RuleEngineDecisionDTO.from_domain(result)
    dto_for_mutation = cast(Any, dto)

    with pytest.raises(FrozenInstanceError):
        dto_for_mutation.status = "mapped"
