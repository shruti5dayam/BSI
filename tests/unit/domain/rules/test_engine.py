"""
Unit tests for deterministic BSI rule-engine orchestration.

These tests verify:

- Final decision statuses
- Unique deterministic mappings
- Unmatched transactions
- Redundant same-output mappings
- Blocking competing-output conflicts
- Evaluation, eligibility, and match counts
- Priority and scope ranking integration
- Stable evidence ordering
- Workspace isolation
- Duplicate-rule protection
- Result consistency validation
- Runtime validation and immutability
"""

from dataclasses import FrozenInstanceError
from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from bsi.domain.rules.conditions import RuleCondition
from bsi.domain.rules.conflicts import (
    RuleConflictAssessment,
    assess_rule_conflict,
)
from bsi.domain.rules.engine import (
    RuleDecisionStatus,
    RuleEngineError,
    RuleEngineResult,
    evaluate_transaction_rules,
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
from bsi.domain.rules.ranking import rank_rule_evaluations
from bsi.domain.rules.scope import RuleScope
from bsi.domain.transactions.models import (
    NormalizedTransaction,
    TransactionContext,
    TransactionSource,
)


def _invalid(value: object) -> Any:
    """
    Bypass static typing for intentional runtime-validation tests.

    Production code should provide validated domain objects. These tests
    confirm that invalid runtime values are rejected at the engine
    boundary.
    """

    return cast(Any, value)


def _transaction(
    *,
    transaction_id: UUID | None = None,
    description: str = "UTILITY PAYMENT",
    context: TransactionContext | None = None,
) -> NormalizedTransaction:
    """Create one normalized transaction for engine tests."""

    return NormalizedTransaction.create(
        transaction_id=transaction_id,
        transaction_date=date(2026, 7, 15),
        original_description=description,
        payment="125.00",
        context=context,
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
    keyword: str = "utility",
    output_account_id: UUID | None = None,
    priority: int = 100,
    status: RuleStatus = RuleStatus.ACTIVE,
    scope: RuleScope | None = None,
) -> RuleDefinition:
    """Create one complete deterministic rule."""

    resolved_account_id = (
        output_account_id if output_account_id is not None else uuid4()
    )

    return RuleDefinition.create(
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
            coa_account_id=resolved_account_id,
        ),
        priority=priority,
        status=status,
        scope=scope,
    )


def _evaluation(
    *,
    transaction: NormalizedTransaction,
    rule: RuleDefinition,
) -> RuleEvaluation:
    """Evaluate one rule for direct result-validation tests."""

    return evaluate_rule(
        rule=rule,
        transaction=transaction,
    )


def _conflict_assessment(
    evaluations: tuple[RuleEvaluation, ...],
) -> RuleConflictAssessment:
    """Build ranking and conflict evidence from evaluations."""

    return assess_rule_conflict(rank_rule_evaluations(evaluations))


def _valid_result_parts() -> tuple[
    UUID,
    NormalizedTransaction,
    RuleEvaluation,
    RuleConflictAssessment,
]:
    """Create consistent evidence for direct RuleEngineResult tests."""

    workspace_id = uuid4()
    transaction = _transaction()
    rule = _rule(
        workspace_id=workspace_id,
    )
    evaluation = _evaluation(
        transaction=transaction,
        rule=rule,
    )
    assessment = _conflict_assessment((evaluation,))

    return (
        workspace_id,
        transaction,
        evaluation,
        assessment,
    )


def test_decision_status_values_are_stable() -> None:
    """Status values may later be stored in APIs and audit records."""

    assert RuleDecisionStatus.UNMATCHED.value == "unmatched"
    assert RuleDecisionStatus.MAPPED.value == "mapped"
    assert RuleDecisionStatus.MAPPED_WITH_REVIEW.value == "mapped_with_review"
    assert RuleDecisionStatus.BLOCKED_CONFLICT.value == "blocked_conflict"


def test_unique_rule_match_produces_mapped_status() -> None:
    """One uniquely ranked matching rule creates a safe mapping."""

    workspace_id = uuid4()
    account_id = uuid4()
    transaction = _transaction()

    rule = _rule(
        workspace_id=workspace_id,
        output_account_id=account_id,
    )

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(rule,),
    )

    assert result.status is RuleDecisionStatus.MAPPED
    assert result.can_map is True
    assert result.requires_review is False
    assert result.is_conflict_blocked is False


def test_unique_rule_match_returns_output_and_winner() -> None:
    """A unique mapping exposes both the account and winning rule."""

    workspace_id = uuid4()
    account_id = uuid4()
    transaction = _transaction()

    rule = _rule(
        workspace_id=workspace_id,
        output_account_id=account_id,
    )

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(rule,),
    )

    assert result.output_account_id == account_id
    assert result.winning_rule_id == rule.rule_id
    assert result.top_rule_ids == (rule.rule_id,)
    assert result.matched_rule_ids == (rule.rule_id,)


def test_unique_mapping_has_clear_decision_message() -> None:
    """Engine delegates the unique-winner audit message."""

    workspace_id = uuid4()

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=_transaction(),
        rules=(
            _rule(
                workspace_id=workspace_id,
            ),
        ),
    )

    assert result.decision_message == (
        "One uniquely ranked deterministic rule matched."
    )


def test_unmatched_transaction_produces_unmatched_status() -> None:
    """No successful rule match leaves the transaction unmapped."""

    workspace_id = uuid4()

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=_transaction(
            description="UTILITY PAYMENT",
        ),
        rules=(
            _rule(
                workspace_id=workspace_id,
                keyword="rent",
            ),
        ),
    )

    assert result.status is RuleDecisionStatus.UNMATCHED
    assert result.can_map is False
    assert result.output_account_id is None
    assert result.winning_rule_id is None
    assert result.requires_review is True
    assert result.is_conflict_blocked is False


def test_unmatched_transaction_has_clear_decision_message() -> None:
    """The result explains why no mapping was created."""

    workspace_id = uuid4()

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=_transaction(),
        rules=(
            _rule(
                workspace_id=workspace_id,
                keyword="insurance",
            ),
        ),
    )

    assert result.decision_message == (
        "No deterministic rules matched the transaction."
    )


def test_empty_rule_collection_produces_unmatched_status() -> None:
    """Running with no configured rules is valid but cannot map."""

    workspace_id = uuid4()
    transaction = _transaction()

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(),
    )

    assert result.status is RuleDecisionStatus.UNMATCHED
    assert result.evaluations == ()
    assert result.evaluated_rule_count == 0
    assert result.eligible_rule_count == 0
    assert result.ineligible_rule_count == 0
    assert result.matched_rule_count == 0
    assert result.unmatched_eligible_rule_count == 0
    assert result.matched_rule_ids == ()
    assert result.top_rule_ids == ()


def test_same_output_tie_produces_mapped_with_review() -> None:
    """Equal-rank rules sharing one output permit mapping with review."""

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

    assert result.status is RuleDecisionStatus.MAPPED_WITH_REVIEW
    assert result.can_map is True
    assert result.output_account_id == shared_account_id
    assert result.requires_review is True
    assert result.is_conflict_blocked is False


def test_same_output_tie_has_no_artificial_winner() -> None:
    """Redundant rules provide a safe output but no unique winner."""

    workspace_id = uuid4()
    shared_account_id = uuid4()
    transaction = _transaction()

    first_rule = _rule(
        workspace_id=workspace_id,
        output_account_id=shared_account_id,
    )
    second_rule = _rule(
        workspace_id=workspace_id,
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

    assert result.winning_rule_id is None
    assert set(result.top_rule_ids) == {
        first_rule.rule_id,
        second_rule.rule_id,
    }
    assert result.matched_rule_count == 2


def test_competing_output_tie_produces_blocked_status() -> None:
    """Equal-rank rules with different outputs block the mapping."""

    workspace_id = uuid4()
    transaction = _transaction()

    first_rule = _rule(
        workspace_id=workspace_id,
        name="Utility Rule",
        output_account_id=uuid4(),
    )
    second_rule = _rule(
        workspace_id=workspace_id,
        name="Repairs Rule",
        output_account_id=uuid4(),
    )

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(
            first_rule,
            second_rule,
        ),
    )

    assert result.status is RuleDecisionStatus.BLOCKED_CONFLICT
    assert result.can_map is False
    assert result.output_account_id is None
    assert result.winning_rule_id is None
    assert result.requires_review is True
    assert result.is_conflict_blocked is True


def test_competing_output_tie_has_clear_message() -> None:
    """The engine explains why accounting mapping was blocked."""

    workspace_id = uuid4()
    transaction = _transaction()

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(
            _rule(
                workspace_id=workspace_id,
                output_account_id=uuid4(),
            ),
            _rule(
                workspace_id=workspace_id,
                output_account_id=uuid4(),
            ),
        ),
    )

    assert result.decision_message == (
        "Multiple top-ranked rules produced different COA accounts; "
        "mapping is blocked pending review."
    )


def test_higher_priority_rule_wins_engine_decision() -> None:
    """Engine integration preserves priority-based ranking."""

    workspace_id = uuid4()
    transaction = _transaction()

    lower_account_id = uuid4()
    higher_account_id = uuid4()

    lower_rule = _rule(
        workspace_id=workspace_id,
        name="Lower Priority",
        output_account_id=lower_account_id,
        priority=100,
    )
    higher_rule = _rule(
        workspace_id=workspace_id,
        name="Higher Priority",
        output_account_id=higher_account_id,
        priority=500,
    )

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(
            lower_rule,
            higher_rule,
        ),
    )

    assert result.status is RuleDecisionStatus.MAPPED
    assert result.output_account_id == higher_account_id
    assert result.winning_rule_id == higher_rule.rule_id
    assert result.matched_rule_ids == (
        higher_rule.rule_id,
        lower_rule.rule_id,
    )


def test_more_specific_rule_wins_when_priorities_are_equal() -> None:
    """Engine integration preserves scope-specificity ranking."""

    workspace_id = uuid4()
    company_id = uuid4()
    store_id = uuid4()

    transaction = _transaction(
        context=TransactionContext(
            company_id=company_id,
            store_id=store_id,
        )
    )

    global_account_id = uuid4()
    store_account_id = uuid4()

    global_rule = _rule(
        workspace_id=workspace_id,
        name="Global Rule",
        output_account_id=global_account_id,
        priority=100,
        scope=RuleScope(),
    )
    store_rule = _rule(
        workspace_id=workspace_id,
        name="Store Rule",
        output_account_id=store_account_id,
        priority=100,
        scope=RuleScope(
            company_id=company_id,
            store_id=store_id,
        ),
    )

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(
            global_rule,
            store_rule,
        ),
    )

    assert result.status is RuleDecisionStatus.MAPPED
    assert result.output_account_id == store_account_id
    assert result.winning_rule_id == store_rule.rule_id
    assert result.matched_rule_ids == (
        store_rule.rule_id,
        global_rule.rule_id,
    )


def test_lower_ranked_different_output_does_not_block_mapping() -> None:
    """Only top-ranked competing outputs create a blocking conflict."""

    workspace_id = uuid4()
    transaction = _transaction()

    winning_account_id = uuid4()

    high_priority_rule = _rule(
        workspace_id=workspace_id,
        name="High Priority Rule",
        output_account_id=winning_account_id,
        priority=500,
    )
    low_priority_rule = _rule(
        workspace_id=workspace_id,
        name="Low Priority Rule",
        output_account_id=uuid4(),
        priority=100,
    )

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(
            low_priority_rule,
            high_priority_rule,
        ),
    )

    assert result.status is RuleDecisionStatus.MAPPED
    assert result.output_account_id == winning_account_id
    assert result.is_conflict_blocked is False


def test_engine_records_every_rule_evaluation() -> None:
    """Matched, unmatched, and ineligible rules remain auditable."""

    workspace_id = uuid4()
    transaction = _transaction()

    matched_rule = _rule(
        workspace_id=workspace_id,
        name="Matched Active Rule",
        keyword="utility",
    )
    unmatched_rule = _rule(
        workspace_id=workspace_id,
        name="Unmatched Active Rule",
        keyword="rent",
    )
    paused_rule = _rule(
        workspace_id=workspace_id,
        name="Paused Rule",
        keyword="utility",
        status=RuleStatus.PAUSED,
    )
    incomplete_draft = RuleDefinition.create(
        workspace_id=workspace_id,
        name="Incomplete Draft",
    )

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(
            matched_rule,
            unmatched_rule,
            paused_rule,
            incomplete_draft,
        ),
    )

    assert result.evaluated_rule_count == 4
    assert result.eligible_rule_count == 2
    assert result.ineligible_rule_count == 2
    assert result.matched_rule_count == 1
    assert result.unmatched_eligible_rule_count == 1


def test_engine_evaluations_use_stable_rule_identifier_order() -> None:
    """Evaluation evidence is stable regardless of input ordering."""

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

    assert tuple(evaluation.rule.rule_id for evaluation in result.evaluations) == (
        first_rule_id,
        second_rule_id,
    )


def test_result_preserves_workspace_and_transaction_identifiers() -> None:
    """Final evidence retains tenant and transaction lineage."""

    workspace_id = uuid4()
    transaction_id = uuid4()

    transaction = _transaction(
        transaction_id=transaction_id,
    )

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(
            _rule(
                workspace_id=workspace_id,
            ),
        ),
    )

    assert result.workspace_id == workspace_id
    assert result.transaction_id == transaction_id


def test_engine_returns_rule_engine_result() -> None:
    """The public engine function returns the domain result model."""

    workspace_id = uuid4()

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=_transaction(),
        rules=(),
    )

    assert isinstance(result, RuleEngineResult)


def test_engine_rejects_invalid_workspace_id() -> None:
    """The engine requires a UUID tenant boundary."""

    with pytest.raises(
        RuleEngineError,
        match="workspace_id must be a UUID",
    ):
        evaluate_transaction_rules(
            workspace_id=_invalid("workspace"),
            transaction=_transaction(),
            rules=(),
        )


def test_engine_rejects_invalid_transaction() -> None:
    """Only normalized transactions may enter the rule engine."""

    with pytest.raises(
        RuleEngineError,
        match="transaction must be a NormalizedTransaction",
    ):
        evaluate_transaction_rules(
            workspace_id=uuid4(),
            transaction=_invalid({}),
            rules=(),
        )


def test_engine_rejects_non_tuple_rules() -> None:
    """Rule collections must use immutable tuples."""

    with pytest.raises(
        RuleEngineError,
        match="rules must be a tuple",
    ):
        evaluate_transaction_rules(
            workspace_id=uuid4(),
            transaction=_transaction(),
            rules=_invalid([]),
        )


def test_engine_rejects_invalid_rule_items() -> None:
    """Every supplied item must be a RuleDefinition."""

    with pytest.raises(
        RuleEngineError,
        match="only RuleDefinition objects",
    ):
        evaluate_transaction_rules(
            workspace_id=uuid4(),
            transaction=_transaction(),
            rules=_invalid(({},)),
        )


def test_engine_rejects_cross_workspace_rules() -> None:
    """Rules from another tenant cannot enter the decision."""

    requested_workspace_id = uuid4()
    foreign_workspace_id = uuid4()

    foreign_rule = _rule(
        workspace_id=foreign_workspace_id,
    )

    with pytest.raises(
        RuleEngineError,
        match="Every rule must belong to the supplied workspace",
    ):
        evaluate_transaction_rules(
            workspace_id=requested_workspace_id,
            transaction=_transaction(),
            rules=(foreign_rule,),
        )


def test_engine_rejects_duplicate_rule_identifiers() -> None:
    """The same rule identifier cannot be evaluated twice."""

    workspace_id = uuid4()
    duplicated_rule_id = uuid4()

    first_rule = _rule(
        workspace_id=workspace_id,
        name="First Rule",
        rule_id=duplicated_rule_id,
    )
    second_rule = _rule(
        workspace_id=workspace_id,
        name="Duplicate Rule",
        rule_id=duplicated_rule_id,
    )

    with pytest.raises(
        RuleEngineError,
        match="duplicate rule identifiers",
    ):
        evaluate_transaction_rules(
            workspace_id=workspace_id,
            transaction=_transaction(),
            rules=(
                first_rule,
                second_rule,
            ),
        )


def test_result_rejects_invalid_workspace_id() -> None:
    """Direct result construction validates tenant identity."""

    (
        _,
        transaction,
        evaluation,
        assessment,
    ) = _valid_result_parts()

    with pytest.raises(
        RuleEngineError,
        match="workspace_id must be a UUID",
    ):
        RuleEngineResult(
            workspace_id=_invalid("workspace"),
            transaction_id=transaction.transaction_id,
            evaluations=(evaluation,),
            conflict_assessment=assessment,
        )


def test_result_rejects_invalid_transaction_id() -> None:
    """Direct result construction requires a UUID transaction ID."""

    (
        workspace_id,
        _,
        evaluation,
        assessment,
    ) = _valid_result_parts()

    with pytest.raises(
        RuleEngineError,
        match="transaction_id must be a UUID",
    ):
        RuleEngineResult(
            workspace_id=workspace_id,
            transaction_id=_invalid("transaction"),
            evaluations=(evaluation,),
            conflict_assessment=assessment,
        )


def test_result_rejects_non_tuple_evaluations() -> None:
    """Result evidence must use an immutable tuple."""

    (
        workspace_id,
        transaction,
        evaluation,
        assessment,
    ) = _valid_result_parts()

    with pytest.raises(
        RuleEngineError,
        match="evaluations must be a tuple",
    ):
        RuleEngineResult(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
            evaluations=_invalid([evaluation]),
            conflict_assessment=assessment,
        )


def test_result_rejects_invalid_evaluation_items() -> None:
    """Every result evaluation must use RuleEvaluation evidence."""

    (
        workspace_id,
        transaction,
        _,
        assessment,
    ) = _valid_result_parts()

    with pytest.raises(
        RuleEngineError,
        match="only RuleEvaluation objects",
    ):
        RuleEngineResult(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
            evaluations=_invalid(({},)),
            conflict_assessment=assessment,
        )


def test_result_rejects_invalid_conflict_assessment() -> None:
    """Final results require authoritative conflict evidence."""

    (
        workspace_id,
        transaction,
        evaluation,
        _,
    ) = _valid_result_parts()

    with pytest.raises(
        RuleEngineError,
        match="conflict_assessment must be a RuleConflictAssessment",
    ):
        RuleEngineResult(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
            evaluations=(evaluation,),
            conflict_assessment=_invalid({}),
        )


def test_result_rejects_evaluation_from_different_transaction() -> None:
    """Evaluation evidence must belong to the result transaction."""

    workspace_id = uuid4()

    result_transaction = _transaction()
    foreign_transaction = _transaction()

    rule = _rule(
        workspace_id=workspace_id,
    )
    foreign_evaluation = _evaluation(
        transaction=foreign_transaction,
        rule=rule,
    )
    assessment = _conflict_assessment((foreign_evaluation,))

    with pytest.raises(
        RuleEngineError,
        match="Every evaluation must belong to the result transaction",
    ):
        RuleEngineResult(
            workspace_id=workspace_id,
            transaction_id=result_transaction.transaction_id,
            evaluations=(foreign_evaluation,),
            conflict_assessment=assessment,
        )


def test_result_rejects_evaluation_from_different_workspace() -> None:
    """Evaluation rules must belong to the result workspace."""

    result_workspace_id = uuid4()
    foreign_workspace_id = uuid4()
    transaction = _transaction()

    foreign_rule = _rule(
        workspace_id=foreign_workspace_id,
    )
    foreign_evaluation = _evaluation(
        transaction=transaction,
        rule=foreign_rule,
    )
    assessment = _conflict_assessment((foreign_evaluation,))

    with pytest.raises(
        RuleEngineError,
        match="Every evaluation must belong to the result workspace",
    ):
        RuleEngineResult(
            workspace_id=result_workspace_id,
            transaction_id=transaction.transaction_id,
            evaluations=(foreign_evaluation,),
            conflict_assessment=assessment,
        )


def test_result_rejects_duplicate_evaluation_identifiers() -> None:
    """One rule cannot appear twice in final evaluation evidence."""

    (
        workspace_id,
        transaction,
        evaluation,
        assessment,
    ) = _valid_result_parts()

    with pytest.raises(
        RuleEngineError,
        match="duplicate rule identifiers",
    ):
        RuleEngineResult(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
            evaluations=(
                evaluation,
                evaluation,
            ),
            conflict_assessment=assessment,
        )


def test_result_rejects_inconsistent_conflict_evidence() -> None:
    """Conflict evidence must represent every matched evaluation."""

    workspace_id = uuid4()
    transaction = _transaction()

    first_rule = _rule(
        workspace_id=workspace_id,
        name="First Rule",
    )
    second_rule = _rule(
        workspace_id=workspace_id,
        name="Second Rule",
    )

    first_evaluation = _evaluation(
        transaction=transaction,
        rule=first_rule,
    )
    second_evaluation = _evaluation(
        transaction=transaction,
        rule=second_rule,
    )

    incorrect_assessment = _conflict_assessment((second_evaluation,))

    with pytest.raises(
        RuleEngineError,
        match="must represent every matched evaluation",
    ):
        RuleEngineResult(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
            evaluations=(first_evaluation,),
            conflict_assessment=incorrect_assessment,
        )


def test_rule_engine_result_is_immutable() -> None:
    """Final mapping evidence cannot be modified after creation."""

    workspace_id = uuid4()

    result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=_transaction(),
        rules=(),
    )
    result_for_mutation = cast(Any, result)

    with pytest.raises(FrozenInstanceError):
        result_for_mutation.workspace_id = uuid4()
