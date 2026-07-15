"""
Unit tests for deterministic BSI rule ranking.

These tests verify:

- RuleRank validation
- Priority-based ordering
- Scope-specificity ordering
- Stable candidate presentation
- Unique winners and unresolved ties
- Filtering of unmatched evaluations
- Transaction and workspace isolation
- Duplicate-rule protection
- Runtime validation
- Immutable ranking evidence
"""

from dataclasses import FrozenInstanceError
from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from bsi.domain.rules.conditions import RuleCondition
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
    DEFAULT_RULE_PRIORITY,
    MAX_RULE_PRIORITY,
    MIN_RULE_PRIORITY,
    RuleDefinition,
    RuleOutput,
)
from bsi.domain.rules.ranking import (
    RuleRank,
    RuleRanking,
    RuleRankingError,
    get_rule_rank,
    rank_rule_evaluations,
)
from bsi.domain.rules.scope import RuleScope
from bsi.domain.transactions.models import (
    NormalizedTransaction,
    TransactionContext,
    TransactionSource,
)


def _invalid(value: object) -> Any:
    """
    Bypass static typing for intentional runtime-validation tests.

    Production code should pass validated domain objects. These tests
    confirm that invalid runtime values are rejected safely.
    """

    return cast(Any, value)


def _transaction(
    *,
    transaction_id: UUID | None = None,
    context: TransactionContext | None = None,
    description: str = "UTILITY PAYMENT",
) -> NormalizedTransaction:
    """Create one normalized transaction for ranking tests."""

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


def _evaluation(
    *,
    transaction: NormalizedTransaction,
    workspace_id: UUID,
    name: str = "Utility Rule",
    rule_id: UUID | None = None,
    priority: int = DEFAULT_RULE_PRIORITY,
    scope: RuleScope | None = None,
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
            coa_account_id=uuid4(),
        ),
        scope=scope,
        status=RuleStatus.ACTIVE,
        priority=priority,
    )

    return evaluate_rule(
        rule=rule,
        transaction=transaction,
    )


def test_rule_rank_exposes_decision_key() -> None:
    """The decision key contains priority followed by specificity."""

    rank = RuleRank(
        priority=500,
        scope_specificity=3,
    )

    assert rank.decision_key == (500, 3)


@pytest.mark.parametrize(
    "priority",
    [
        MIN_RULE_PRIORITY,
        DEFAULT_RULE_PRIORITY,
        MAX_RULE_PRIORITY,
    ],
)
def test_rule_rank_accepts_priority_boundaries(
    priority: int,
) -> None:
    """Minimum, default, and maximum priorities are valid."""

    rank = RuleRank(
        priority=priority,
        scope_specificity=0,
    )

    assert rank.priority == priority


@pytest.mark.parametrize(
    "invalid_priority",
    [
        MIN_RULE_PRIORITY - 1,
        MAX_RULE_PRIORITY + 1,
    ],
)
def test_rule_rank_rejects_priority_outside_bounds(
    invalid_priority: int,
) -> None:
    """Priority must remain inside the rule-domain limits."""

    with pytest.raises(
        RuleRankingError,
        match="priority must be between",
    ):
        RuleRank(
            priority=invalid_priority,
            scope_specificity=0,
        )


@pytest.mark.parametrize(
    "invalid_priority",
    [
        True,
        100.5,
        "100",
    ],
)
def test_rule_rank_rejects_non_integer_priority(
    invalid_priority: object,
) -> None:
    """Priority must be an actual integer."""

    with pytest.raises(
        RuleRankingError,
        match="priority must be an integer",
    ):
        RuleRank(
            priority=_invalid(invalid_priority),
            scope_specificity=0,
        )


@pytest.mark.parametrize(
    "specificity",
    [
        0,
        1,
        2,
        3,
        4,
    ],
)
def test_rule_rank_accepts_scope_specificity_range(
    specificity: int,
) -> None:
    """Scope specificity may contain zero through four dimensions."""

    rank = RuleRank(
        priority=100,
        scope_specificity=specificity,
    )

    assert rank.scope_specificity == specificity


@pytest.mark.parametrize(
    "invalid_specificity",
    [
        -1,
        5,
    ],
)
def test_rule_rank_rejects_specificity_outside_bounds(
    invalid_specificity: int,
) -> None:
    """Scope specificity cannot be below zero or above four."""

    with pytest.raises(
        RuleRankingError,
        match="scope_specificity must be between 0 and 4",
    ):
        RuleRank(
            priority=100,
            scope_specificity=invalid_specificity,
        )


@pytest.mark.parametrize(
    "invalid_specificity",
    [
        True,
        1.5,
        "1",
    ],
)
def test_rule_rank_rejects_non_integer_specificity(
    invalid_specificity: object,
) -> None:
    """Scope specificity must be an integer."""

    with pytest.raises(
        RuleRankingError,
        match="scope_specificity must be an integer",
    ):
        RuleRank(
            priority=100,
            scope_specificity=_invalid(invalid_specificity),
        )


def test_get_rule_rank_uses_rule_priority_and_scope() -> None:
    """Rule rank comes from configured priority and scope specificity."""

    company_id = uuid4()
    store_id = uuid4()
    transaction = _transaction(
        context=TransactionContext(
            company_id=company_id,
            store_id=store_id,
        )
    )

    evaluation = _evaluation(
        transaction=transaction,
        workspace_id=uuid4(),
        priority=450,
        scope=RuleScope(
            company_id=company_id,
            store_id=store_id,
        ),
    )

    rank = get_rule_rank(evaluation)

    assert rank.priority == 450
    assert rank.scope_specificity == 2
    assert rank.decision_key == (450, 2)


def test_higher_priority_rule_ranks_first() -> None:
    """Explicit priority is the strongest ranking criterion."""

    workspace_id = uuid4()
    transaction = _transaction()

    lower_priority = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Lower Priority",
        priority=100,
    )
    higher_priority = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Higher Priority",
        priority=500,
    )

    ranking = rank_rule_evaluations(
        (
            lower_priority,
            higher_priority,
        )
    )

    assert tuple(candidate.rule.name for candidate in ranking.candidates) == (
        "Higher Priority",
        "Lower Priority",
    )

    assert ranking.winner is higher_priority


def test_more_specific_scope_ranks_first_when_priority_is_equal() -> None:
    """Narrower scope wins when explicit priorities are equal."""

    workspace_id = uuid4()
    company_id = uuid4()
    store_id = uuid4()

    transaction = _transaction(
        context=TransactionContext(
            company_id=company_id,
            store_id=store_id,
        )
    )

    global_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Global Rule",
        priority=100,
        scope=RuleScope(),
    )
    company_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Company Rule",
        priority=100,
        scope=RuleScope(
            company_id=company_id,
        ),
    )
    store_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Store Rule",
        priority=100,
        scope=RuleScope(
            company_id=company_id,
            store_id=store_id,
        ),
    )

    ranking = rank_rule_evaluations(
        (
            global_evaluation,
            company_evaluation,
            store_evaluation,
        )
    )

    assert tuple(candidate.rule.name for candidate in ranking.candidates) == (
        "Store Rule",
        "Company Rule",
        "Global Rule",
    )

    assert ranking.winner is store_evaluation


def test_priority_ranks_before_scope_specificity() -> None:
    """A high-priority global rule outranks a low-priority store rule."""

    workspace_id = uuid4()
    company_id = uuid4()
    store_id = uuid4()

    transaction = _transaction(
        context=TransactionContext(
            company_id=company_id,
            store_id=store_id,
        )
    )

    high_priority_global = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="High Priority Global",
        priority=500,
        scope=RuleScope(),
    )
    low_priority_store = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Low Priority Store",
        priority=100,
        scope=RuleScope(
            company_id=company_id,
            store_id=store_id,
        ),
    )

    ranking = rank_rule_evaluations(
        (
            low_priority_store,
            high_priority_global,
        )
    )

    assert ranking.candidates[0] is high_priority_global
    assert ranking.winner is high_priority_global


def test_equal_business_rank_remains_tied() -> None:
    """UUID ordering must not silently resolve a financial ambiguity."""

    workspace_id = uuid4()
    transaction = _transaction()

    first_rule_id = UUID(int=1)
    second_rule_id = UUID(int=2)

    second_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Second Rule",
        rule_id=second_rule_id,
        priority=100,
    )
    first_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="First Rule",
        rule_id=first_rule_id,
        priority=100,
    )

    ranking = rank_rule_evaluations(
        (
            second_evaluation,
            first_evaluation,
        )
    )

    assert tuple(candidate.rule.rule_id for candidate in ranking.candidates) == (
        first_rule_id,
        second_rule_id,
    )

    assert ranking.has_top_tie is True
    assert ranking.winner is None
    assert ranking.top_candidates == (
        first_evaluation,
        second_evaluation,
    )


def test_lower_ranked_candidate_does_not_create_top_tie() -> None:
    """Only candidates sharing the strongest rank create a top tie."""

    workspace_id = uuid4()
    transaction = _transaction()

    top_candidate = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Top Candidate",
        priority=500,
    )
    lower_candidate = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Lower Candidate",
        priority=100,
    )

    ranking = rank_rule_evaluations(
        (
            lower_candidate,
            top_candidate,
        )
    )

    assert ranking.top_candidates == (top_candidate,)
    assert ranking.has_top_tie is False
    assert ranking.winner is top_candidate


def test_unmatched_evaluations_are_filtered_out() -> None:
    """Only successful rule evaluations become ranked candidates."""

    workspace_id = uuid4()
    transaction = _transaction()

    matched_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Matched Rule",
        keyword="utility",
    )
    unmatched_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        name="Unmatched Rule",
        keyword="rent",
    )

    ranking = rank_rule_evaluations(
        (
            unmatched_evaluation,
            matched_evaluation,
        )
    )

    assert ranking.candidates == (matched_evaluation,)
    assert ranking.candidate_count == 1
    assert ranking.winner is matched_evaluation


def test_no_matches_produces_empty_ranking() -> None:
    """A transaction with no matching rules has no winner or rank."""

    workspace_id = uuid4()
    transaction = _transaction(
        description="UTILITY PAYMENT",
    )

    unmatched_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        keyword="rent",
    )

    ranking = rank_rule_evaluations((unmatched_evaluation,))

    assert ranking.candidates == ()
    assert ranking.candidate_count == 0
    assert ranking.has_candidates is False
    assert ranking.transaction_id is None
    assert ranking.workspace_id is None
    assert ranking.top_rank is None
    assert ranking.top_candidates == ()
    assert ranking.has_top_tie is False
    assert ranking.winner is None


def test_empty_evaluation_collection_is_valid() -> None:
    """An empty evaluation collection represents no candidate rules."""

    ranking = rank_rule_evaluations(())

    assert ranking.candidates == ()
    assert ranking.has_candidates is False


def test_ranking_exposes_transaction_and_workspace_ids() -> None:
    """Ranking evidence preserves transaction and tenant ownership."""

    transaction_id = uuid4()
    workspace_id = uuid4()
    transaction = _transaction(
        transaction_id=transaction_id,
    )

    evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
    )

    ranking = rank_rule_evaluations((evaluation,))

    assert ranking.transaction_id == transaction_id
    assert ranking.workspace_id == workspace_id


def test_rank_rule_evaluations_rejects_non_tuple_collection() -> None:
    """Evaluation collections use immutable tuples."""

    with pytest.raises(
        RuleRankingError,
        match="evaluations must be a tuple",
    ):
        rank_rule_evaluations(
            _invalid([]),
        )


def test_rank_rule_evaluations_rejects_invalid_items() -> None:
    """Every collection item must be a RuleEvaluation."""

    with pytest.raises(
        RuleRankingError,
        match="only RuleEvaluation objects",
    ):
        rank_rule_evaluations(
            _invalid(({},)),
        )


def test_direct_ranking_rejects_unmatched_candidates() -> None:
    """RuleRanking itself accepts matched candidates only."""

    transaction = _transaction()

    unmatched_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=uuid4(),
        keyword="rent",
    )

    assert unmatched_evaluation.matched is False

    with pytest.raises(
        RuleRankingError,
        match="only matched rule evaluations",
    ):
        RuleRanking(candidates=(unmatched_evaluation,))


def test_evaluations_from_multiple_transactions_are_rejected() -> None:
    """One ranking decision cannot combine different transactions."""

    workspace_id = uuid4()

    first_evaluation = _evaluation(
        transaction=_transaction(),
        workspace_id=workspace_id,
        name="First Rule",
    )
    second_evaluation = _evaluation(
        transaction=_transaction(),
        workspace_id=workspace_id,
        name="Second Rule",
    )

    with pytest.raises(
        RuleRankingError,
        match="evaluations must belong to one transaction",
    ):
        rank_rule_evaluations(
            (
                first_evaluation,
                second_evaluation,
            )
        )


def test_evaluations_from_multiple_workspaces_are_rejected() -> None:
    """Tenant-owned rule evaluations cannot be ranked together."""

    transaction = _transaction()

    first_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=uuid4(),
        name="First Workspace Rule",
    )
    second_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=uuid4(),
        name="Second Workspace Rule",
    )

    with pytest.raises(
        RuleRankingError,
        match="evaluations must belong to one workspace",
    ):
        rank_rule_evaluations(
            (
                first_evaluation,
                second_evaluation,
            )
        )


def test_duplicate_rule_identifiers_are_rejected() -> None:
    """The same rule version cannot enter one decision twice."""

    workspace_id = uuid4()
    rule_id = uuid4()
    transaction = _transaction()

    first_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        rule_id=rule_id,
        name="First Evaluation",
    )
    second_evaluation = _evaluation(
        transaction=transaction,
        workspace_id=workspace_id,
        rule_id=rule_id,
        name="Duplicate Evaluation",
    )

    with pytest.raises(
        RuleRankingError,
        match="duplicate rule identifiers",
    ):
        rank_rule_evaluations(
            (
                first_evaluation,
                second_evaluation,
            )
        )


def test_get_rule_rank_rejects_invalid_evaluation() -> None:
    """Rank extraction requires RuleEvaluation evidence."""

    with pytest.raises(
        RuleRankingError,
        match="evaluation must be a RuleEvaluation",
    ):
        get_rule_rank(
            _invalid({}),
        )


def test_rule_rank_is_immutable() -> None:
    """Business-ranking values cannot be changed after construction."""

    rank = RuleRank(
        priority=100,
        scope_specificity=2,
    )
    rank_for_mutation = cast(Any, rank)

    with pytest.raises(FrozenInstanceError):
        rank_for_mutation.priority = 500


def test_rule_ranking_is_immutable() -> None:
    """The ordered candidate collection cannot be replaced."""

    transaction = _transaction()
    evaluation = _evaluation(
        transaction=transaction,
        workspace_id=uuid4(),
    )

    ranking = rank_rule_evaluations((evaluation,))
    ranking_for_mutation = cast(Any, ranking)

    with pytest.raises(FrozenInstanceError):
        ranking_for_mutation.candidates = ()
