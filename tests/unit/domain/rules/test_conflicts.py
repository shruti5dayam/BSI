"""
Unit tests for deterministic BSI rule-conflict assessment.

These tests verify:

- Conflict classification
- Unique-winner behavior
- Redundant same-output rules
- Competing financial outputs
- Safe mapping decisions
- Review and blocking requirements
- Stable rule and output evidence
- Lower-ranked candidate handling
- Empty ranking behavior
- Runtime validation
- Immutable conflict evidence
"""

from dataclasses import FrozenInstanceError
from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from bsi.domain.rules.conditions import RuleCondition
from bsi.domain.rules.conflicts import (
    RuleConflictAssessment,
    RuleConflictError,
    RuleConflictKind,
    assess_rule_conflict,
)
from bsi.domain.rules.enums import (
    RuleConditionField,
    RuleOperator,
    RuleStatus,
)
from bsi.domain.rules.evaluator import (
    RuleEvaluation,
    evaluate_rule,
)
from bsi.domain.rules.models import (
    RuleDefinition,
    RuleOutput,
)
from bsi.domain.rules.ranking import (
    RuleRanking,
    rank_rule_evaluations,
)
from bsi.domain.transactions.models import (
    NormalizedTransaction,
    TransactionSource,
)


def _invalid(value: object) -> Any:
    """
    Bypass static typing for intentional runtime-validation tests.

    Production code should pass validated domain objects. These tests
    verify that invalid values are rejected if they reach this boundary.
    """

    return cast(Any, value)


def _transaction(
    *,
    transaction_id: UUID | None = None,
    description: str = "UTILITY PAYMENT",
) -> NormalizedTransaction:
    """Create one valid transaction for conflict tests."""

    return NormalizedTransaction.create(
        transaction_id=transaction_id,
        transaction_date=date(2026, 7, 15),
        original_description=description,
        payment="125.00",
        source=TransactionSource(
            file_name="statement.xlsx",
            source_row_number=10,
        ),
    )


def _evaluation(
    *,
    transaction: NormalizedTransaction,
    workspace_id: UUID,
    name: str,
    output_account_id: UUID,
    priority: int = 100,
    rule_id: UUID | None = None,
    keyword: str = "utility",
) -> RuleEvaluation:
    """Create and evaluate one active deterministic rule."""

    rule = RuleDefinition.create(
        rule_id=rule_id,
        workspace_id=workspace_id,
        name=name,
        conditions=(
            RuleCondition(
                field=RuleConditionField.SEARCHABLE_TEXT,
                operator=RuleOperator.CONTAINS,
                value=keyword,
            ),
        ),
        output=RuleOutput(
            coa_account_id=output_account_id,
        ),
        status=RuleStatus.ACTIVE,
        priority=priority,
    )

    return evaluate_rule(
        rule=rule,
        transaction=transaction,
    )


def _assessment(
    evaluations: tuple[RuleEvaluation, ...],
) -> RuleConflictAssessment:
    """Rank evaluations and create one conflict assessment."""

    ranking = rank_rule_evaluations(evaluations)

    return assess_rule_conflict(ranking)


def test_conflict_kind_values_are_stable() -> None:
    """Conflict values may later be stored in APIs and audit records."""

    assert RuleConflictKind.NONE.value == "none"
    assert RuleConflictKind.REDUNDANT_SAME_OUTPUT.value == "redundant_same_output"
    assert RuleConflictKind.COMPETING_OUTPUTS.value == "competing_outputs"


def test_empty_ranking_has_no_conflict() -> None:
    """No matched candidates means no mapping and no conflict."""

    assessment = assess_rule_conflict(rank_rule_evaluations(()))

    assert assessment.kind is RuleConflictKind.NONE
    assert assessment.has_conflict is False
    assert assessment.requires_review is False
    assert assessment.is_blocking is False
    assert assessment.can_map is False
    assert assessment.resolved_output_account_id is None
    assert assessment.winning_rule_id is None


def test_empty_ranking_has_no_transaction_or_workspace() -> None:
    """Empty rankings cannot expose transaction or tenant identifiers."""

    assessment = assess_rule_conflict(rank_rule_evaluations(()))

    assert assessment.transaction_id is None
    assert assessment.workspace_id is None
    assert assessment.top_candidates == ()
    assert assessment.top_rule_ids == ()
    assert assessment.output_account_ids == ()


def test_empty_ranking_decision_message_is_clear() -> None:
    """The audit message explains that no rule matched."""

    assessment = assess_rule_conflict(rank_rule_evaluations(()))

    assert assessment.decision_message == (
        "No deterministic rules matched the transaction."
    )


def test_unique_candidate_has_no_conflict() -> None:
    """One matched and top-ranked rule produces no conflict."""

    workspace_id = uuid4()
    account_id = uuid4()
    transaction = _transaction()

    evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Utility Rule",
        output_account_id=account_id,
    )

    assessment = _assessment((evaluation,))

    assert assessment.kind is RuleConflictKind.NONE
    assert assessment.has_conflict is False
    assert assessment.requires_review is False
    assert assessment.is_blocking is False


def test_unique_candidate_can_map() -> None:
    """A uniquely ranked match exposes its deterministic output."""

    workspace_id = uuid4()
    account_id = uuid4()
    transaction = _transaction()

    evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Utility Rule",
        output_account_id=account_id,
    )

    assessment = _assessment((evaluation,))

    assert assessment.can_map is True
    assert assessment.resolved_output_account_id == account_id
    assert assessment.winning_rule_id == evaluation.rule.rule_id


def test_unique_candidate_exposes_expected_evidence() -> None:
    """Conflict evidence preserves candidate and context identifiers."""

    transaction_id = uuid4()
    workspace_id = uuid4()
    account_id = uuid4()

    transaction = _transaction(
        transaction_id=transaction_id,
    )

    evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Utility Rule",
        output_account_id=account_id,
    )

    assessment = _assessment((evaluation,))

    assert assessment.transaction_id == transaction_id
    assert assessment.workspace_id == workspace_id
    assert assessment.matched_candidate_count == 1
    assert assessment.top_candidate_count == 1
    assert assessment.top_candidates == (evaluation,)
    assert assessment.top_rule_ids == (evaluation.rule.rule_id,)
    assert assessment.output_account_ids == (account_id,)
    assert assessment.unique_output_count == 1


def test_unique_candidate_decision_message_is_clear() -> None:
    """The audit message identifies a unique deterministic winner."""

    workspace_id = uuid4()
    transaction = _transaction()

    evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Utility Rule",
        output_account_id=uuid4(),
    )

    assessment = _assessment((evaluation,))

    assert assessment.decision_message == (
        "One uniquely ranked deterministic rule matched."
    )


def test_same_output_tie_is_redundant_conflict() -> None:
    """Equal-rank rules with one output are redundant, not ambiguous."""

    workspace_id = uuid4()
    shared_account_id = uuid4()
    transaction = _transaction()

    first_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Utility Rule A",
        output_account_id=shared_account_id,
    )
    second_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Utility Rule B",
        output_account_id=shared_account_id,
    )

    assessment = _assessment(
        (
            first_evaluation,
            second_evaluation,
        )
    )

    assert assessment.kind is RuleConflictKind.REDUNDANT_SAME_OUTPUT
    assert assessment.has_conflict is True
    assert assessment.requires_review is True
    assert assessment.is_blocking is False


def test_same_output_tie_allows_financial_mapping() -> None:
    """Identical top outputs remain safe for deterministic mapping."""

    workspace_id = uuid4()
    shared_account_id = uuid4()
    transaction = _transaction()

    first_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Utility Rule A",
        output_account_id=shared_account_id,
    )
    second_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Utility Rule B",
        output_account_id=shared_account_id,
    )

    assessment = _assessment(
        (
            first_evaluation,
            second_evaluation,
        )
    )

    assert assessment.can_map is True
    assert assessment.resolved_output_account_id == shared_account_id
    assert assessment.winning_rule_id is None


def test_same_output_tie_reports_top_candidates() -> None:
    """Every equally ranked redundant rule remains visible for review."""

    workspace_id = uuid4()
    shared_account_id = uuid4()
    transaction = _transaction()

    first_rule_id = UUID(int=1)
    second_rule_id = UUID(int=2)

    second_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Second Rule",
        rule_id=second_rule_id,
        output_account_id=shared_account_id,
    )
    first_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="First Rule",
        rule_id=first_rule_id,
        output_account_id=shared_account_id,
    )

    assessment = _assessment(
        (
            second_evaluation,
            first_evaluation,
        )
    )

    assert assessment.top_candidate_count == 2
    assert assessment.top_rule_ids == (
        first_rule_id,
        second_rule_id,
    )
    assert assessment.output_account_ids == (shared_account_id,)
    assert assessment.unique_output_count == 1


def test_same_output_tie_decision_message_is_clear() -> None:
    """The message permits mapping but requires rule review."""

    workspace_id = uuid4()
    shared_account_id = uuid4()
    transaction = _transaction()

    assessment = _assessment(
        (
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Utility Rule A",
                output_account_id=shared_account_id,
            ),
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Utility Rule B",
                output_account_id=shared_account_id,
            ),
        )
    )

    assert assessment.decision_message == (
        "Multiple top-ranked rules matched the same COA "
        "account; mapping is allowed with rule review required."
    )


def test_competing_outputs_are_blocking_conflict() -> None:
    """Equal-rank rules with different outputs create ambiguity."""

    workspace_id = uuid4()
    transaction = _transaction()

    utility_account_id = uuid4()
    repairs_account_id = uuid4()

    assessment = _assessment(
        (
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Utility Rule",
                output_account_id=utility_account_id,
            ),
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Repairs Rule",
                output_account_id=repairs_account_id,
            ),
        )
    )

    assert assessment.kind is RuleConflictKind.COMPETING_OUTPUTS
    assert assessment.has_conflict is True
    assert assessment.requires_review is True
    assert assessment.is_blocking is True


def test_competing_outputs_cannot_map() -> None:
    """Ambiguous outputs must not produce an accounting mapping."""

    workspace_id = uuid4()
    transaction = _transaction()

    assessment = _assessment(
        (
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Utility Rule",
                output_account_id=uuid4(),
            ),
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Repairs Rule",
                output_account_id=uuid4(),
            ),
        )
    )

    assert assessment.can_map is False
    assert assessment.resolved_output_account_id is None
    assert assessment.winning_rule_id is None


def test_competing_outputs_are_reported_in_stable_order() -> None:
    """Unique output identifiers use stable UUID ordering."""

    workspace_id = uuid4()
    transaction = _transaction()

    first_account_id = UUID(int=1)
    second_account_id = UUID(int=2)

    assessment = _assessment(
        (
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Second Output Rule",
                output_account_id=second_account_id,
            ),
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="First Output Rule",
                output_account_id=first_account_id,
            ),
        )
    )

    assert assessment.output_account_ids == (
        first_account_id,
        second_account_id,
    )
    assert assessment.unique_output_count == 2


def test_competing_outputs_decision_message_is_clear() -> None:
    """The message explains why mapping was blocked."""

    workspace_id = uuid4()
    transaction = _transaction()

    assessment = _assessment(
        (
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Utility Rule",
                output_account_id=uuid4(),
            ),
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Repairs Rule",
                output_account_id=uuid4(),
            ),
        )
    )

    assert assessment.decision_message == (
        "Multiple top-ranked rules produced different COA accounts; "
        "mapping is blocked pending review."
    )


def test_three_top_rules_with_same_output_remain_redundant() -> None:
    """Any number of top rules sharing one output permits mapping."""

    workspace_id = uuid4()
    transaction = _transaction()
    shared_account_id = uuid4()

    assessment = _assessment(
        (
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Rule A",
                output_account_id=shared_account_id,
            ),
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Rule B",
                output_account_id=shared_account_id,
            ),
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Rule C",
                output_account_id=shared_account_id,
            ),
        )
    )

    assert assessment.top_candidate_count == 3
    assert assessment.unique_output_count == 1
    assert assessment.kind is RuleConflictKind.REDUNDANT_SAME_OUTPUT
    assert assessment.can_map is True


def test_three_top_rules_with_multiple_outputs_are_competing() -> None:
    """One differing top output is enough to block mapping."""

    workspace_id = uuid4()
    transaction = _transaction()

    shared_account_id = uuid4()
    competing_account_id = uuid4()

    assessment = _assessment(
        (
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Rule A",
                output_account_id=shared_account_id,
            ),
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Rule B",
                output_account_id=shared_account_id,
            ),
            _evaluation(
                transaction=transaction,
                workspace_id=workspace_id,
                name="Rule C",
                output_account_id=competing_account_id,
            ),
        )
    )

    assert assessment.top_candidate_count == 3
    assert assessment.unique_output_count == 2
    assert assessment.kind is RuleConflictKind.COMPETING_OUTPUTS
    assert assessment.is_blocking is True


def test_lower_ranked_different_output_does_not_create_conflict() -> None:
    """A rule that lost by priority cannot create a decision conflict."""

    workspace_id = uuid4()
    transaction = _transaction()

    winning_account_id = uuid4()

    high_priority = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="High Priority Rule",
        output_account_id=winning_account_id,
        priority=500,
    )
    low_priority = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Low Priority Rule",
        output_account_id=uuid4(),
        priority=100,
    )

    assessment = _assessment(
        (
            low_priority,
            high_priority,
        )
    )

    assert assessment.matched_candidate_count == 2
    assert assessment.top_candidate_count == 1
    assert assessment.kind is RuleConflictKind.NONE
    assert assessment.winning_rule_id == high_priority.rule.rule_id
    assert assessment.resolved_output_account_id == winning_account_id


def test_unmatched_rule_does_not_enter_conflict_assessment() -> None:
    """Failed rule evaluations are excluded before conflict analysis."""

    workspace_id = uuid4()
    transaction = _transaction()

    matched_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Matched Rule",
        output_account_id=uuid4(),
        keyword="utility",
    )
    unmatched_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Unmatched Rule",
        output_account_id=uuid4(),
        keyword="rent",
    )

    assessment = _assessment(
        (
            unmatched_evaluation,
            matched_evaluation,
        )
    )

    assert assessment.matched_candidate_count == 1
    assert assessment.top_candidates == (matched_evaluation,)
    assert assessment.kind is RuleConflictKind.NONE


def test_assessment_accepts_direct_valid_ranking() -> None:
    """RuleConflictAssessment accepts validated ranking evidence."""

    ranking = RuleRanking(
        candidates=(),
    )

    assessment = RuleConflictAssessment(
        ranking=ranking,
    )

    assert assessment.ranking is ranking


def test_assessment_rejects_invalid_ranking() -> None:
    """Conflict assessment requires the authoritative ranking model."""

    with pytest.raises(
        RuleConflictError,
        match="ranking must be a RuleRanking",
    ):
        RuleConflictAssessment(
            ranking=_invalid({}),
        )


def test_assess_rule_conflict_rejects_invalid_ranking() -> None:
    """The public factory validates its input."""

    with pytest.raises(
        RuleConflictError,
        match="ranking must be a RuleRanking",
    ):
        assess_rule_conflict(
            _invalid({}),
        )


def test_conflict_assessment_is_immutable() -> None:
    """Conflict evidence cannot be replaced after construction."""

    assessment = assess_rule_conflict(rank_rule_evaluations(()))
    assessment_for_mutation = cast(Any, assessment)

    with pytest.raises(FrozenInstanceError):
        assessment_for_mutation.ranking = RuleRanking(
            candidates=(),
        )
