"""
Unit tests for the in-memory rule-engine repositories.

These tests verify:

- Transaction storage and retrieval
- Workspace isolation
- Idempotent replacement behavior
- Rule storage and deterministic listing
- Rule workspace ownership validation
- Decision storage and retrieval
- Latest-decision replacement
- Stable decision ordering
- Invalid adapter input handling
"""

from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from bsi.application.rule_engine.dto import RuleEngineDecisionDTO
from bsi.domain.rules.conditions import RuleCondition
from bsi.domain.rules.enums import (
    RuleConditionField,
    RuleOperator,
    RuleStatus,
)
from bsi.domain.rules.models import (
    RuleDefinition,
    RuleOutput,
)
from bsi.domain.transactions.models import (
    NormalizedTransaction,
    TransactionSource,
)
from bsi.infrastructure.rule_engine.repositories import (
    InMemoryRepositoryError,
    InMemoryRuleDecisionRepository,
    InMemoryRuleRepository,
    InMemoryTransactionRepository,
)

DEFAULT_TRANSACTION_DATE = date(2026, 7, 15)


def _invalid(value: object) -> Any:
    """
    Bypass static typing for intentional runtime-validation tests.

    These tests verify that infrastructure adapters reject invalid
    runtime values even when static typing has been bypassed.
    """

    return cast(Any, value)


def _transaction(
    *,
    transaction_id: UUID | None = None,
    description: str = "UTILITY PAYMENT",
) -> NormalizedTransaction:
    """Create one valid normalized transaction."""

    return NormalizedTransaction.create(
        transaction_id=transaction_id,
        transaction_date=DEFAULT_TRANSACTION_DATE,
        original_description=description,
        payment="125.00",
        source=TransactionSource(
            file_name="statement.xlsx",
            source_row_number=10,
        ),
    )


def _rule(
    *,
    workspace_id: UUID,
    rule_id: UUID | None = None,
    name: str = "Utility Rule",
) -> RuleDefinition:
    """Create one complete active deterministic rule."""

    return RuleDefinition.create(
        rule_id=rule_id,
        workspace_id=workspace_id,
        name=name,
        conditions=(
            RuleCondition(
                field=RuleConditionField.SEARCHABLE_TEXT,
                operator=RuleOperator.CONTAINS,
                value="utility",
            ),
        ),
        output=RuleOutput(
            coa_account_id=uuid4(),
        ),
        status=RuleStatus.ACTIVE,
    )


def _decision(
    *,
    workspace_id: UUID,
    transaction_id: UUID,
    decision_message: str = "No deterministic rule matched.",
) -> RuleEngineDecisionDTO:
    """
    Create one internally consistent unmatched decision.

    A zero-evaluation unmatched decision is the smallest valid decision
    object required for repository behavior tests.
    """

    return RuleEngineDecisionDTO(
        workspace_id=workspace_id,
        transaction_id=transaction_id,
        status="unmatched",
        conflict_kind="none",
        can_map=False,
        requires_review=False,
        is_conflict_blocked=False,
        output_account_id=None,
        winning_rule_id=None,
        matched_rule_ids=(),
        top_rule_ids=(),
        evaluated_rule_count=0,
        eligible_rule_count=0,
        ineligible_rule_count=0,
        matched_rule_count=0,
        unmatched_eligible_rule_count=0,
        decision_message=decision_message,
        evaluations=(),
    )


# ---------------------------------------------------------------------
# TRANSACTION REPOSITORY TESTS
# ---------------------------------------------------------------------


@pytest.mark.unit
def test_transaction_repository_adds_and_returns_transaction() -> None:
    """A stored transaction should be retrievable by workspace and ID."""

    repository = InMemoryTransactionRepository()
    workspace_id = uuid4()
    transaction = _transaction()

    repository.add(
        workspace_id=workspace_id,
        transaction=transaction,
    )

    stored_transaction = repository.get_by_id(
        workspace_id=workspace_id,
        transaction_id=transaction.transaction_id,
    )

    assert stored_transaction is transaction
    assert repository.contains(
        workspace_id=workspace_id,
        transaction_id=transaction.transaction_id,
    )
    assert repository.count == 1


@pytest.mark.unit
def test_transaction_repository_enforces_workspace_isolation() -> None:
    """A transaction stored in one workspace must not leak into another."""

    repository = InMemoryTransactionRepository()
    owning_workspace_id = uuid4()
    other_workspace_id = uuid4()
    transaction = _transaction()

    repository.add(
        workspace_id=owning_workspace_id,
        transaction=transaction,
    )

    result = repository.get_by_id(
        workspace_id=other_workspace_id,
        transaction_id=transaction.transaction_id,
    )

    assert result is None
    assert repository.count == 1


@pytest.mark.unit
def test_same_transaction_id_can_exist_in_separate_workspaces() -> None:
    """Composite keys should allow the same ID in different workspaces."""

    repository = InMemoryTransactionRepository()
    transaction_id = uuid4()
    first_workspace_id = uuid4()
    second_workspace_id = uuid4()

    first_transaction = _transaction(
        transaction_id=transaction_id,
        description="FIRST WORKSPACE TRANSACTION",
    )
    second_transaction = _transaction(
        transaction_id=transaction_id,
        description="SECOND WORKSPACE TRANSACTION",
    )

    repository.add(
        workspace_id=first_workspace_id,
        transaction=first_transaction,
    )
    repository.add(
        workspace_id=second_workspace_id,
        transaction=second_transaction,
    )

    assert (
        repository.get_by_id(
            workspace_id=first_workspace_id,
            transaction_id=transaction_id,
        )
        is first_transaction
    )
    assert (
        repository.get_by_id(
            workspace_id=second_workspace_id,
            transaction_id=transaction_id,
        )
        is second_transaction
    )
    assert repository.count == 2


@pytest.mark.unit
def test_transaction_repository_replaces_existing_key() -> None:
    """Saving the same workspace and transaction ID should be idempotent."""

    repository = InMemoryTransactionRepository()
    workspace_id = uuid4()
    transaction_id = uuid4()

    original_transaction = _transaction(
        transaction_id=transaction_id,
        description="ORIGINAL DESCRIPTION",
    )
    replacement_transaction = _transaction(
        transaction_id=transaction_id,
        description="REPLACEMENT DESCRIPTION",
    )

    repository.add(
        workspace_id=workspace_id,
        transaction=original_transaction,
    )
    repository.add(
        workspace_id=workspace_id,
        transaction=replacement_transaction,
    )

    stored_transaction = repository.get_by_id(
        workspace_id=workspace_id,
        transaction_id=transaction_id,
    )

    assert stored_transaction is replacement_transaction
    assert repository.count == 1


@pytest.mark.unit
def test_transaction_repository_returns_none_for_missing_transaction() -> None:
    """Unknown transaction keys should return None."""

    repository = InMemoryTransactionRepository()

    result = repository.get_by_id(
        workspace_id=uuid4(),
        transaction_id=uuid4(),
    )

    assert result is None


@pytest.mark.unit
def test_transaction_repository_rejects_invalid_transaction() -> None:
    """The adapter should reject objects that are not domain transactions."""

    repository = InMemoryTransactionRepository()

    with pytest.raises(
        InMemoryRepositoryError,
        match="transaction must be a NormalizedTransaction",
    ):
        repository.add(
            workspace_id=uuid4(),
            transaction=_invalid("not-a-transaction"),
        )


# ---------------------------------------------------------------------
# RULE REPOSITORY TESTS
# ---------------------------------------------------------------------


@pytest.mark.unit
def test_rule_repository_adds_and_lists_workspace_rules() -> None:
    """Stored rules should be returned only for their workspace."""

    repository = InMemoryRuleRepository()
    workspace_id = uuid4()
    rule = _rule(workspace_id=workspace_id)

    repository.add(
        workspace_id=workspace_id,
        rule=rule,
    )

    rules = repository.list_by_workspace(
        workspace_id=workspace_id,
    )

    assert rules == (rule,)
    assert (
        repository.count_by_workspace(
            workspace_id=workspace_id,
        )
        == 1
    )
    assert repository.count == 1


@pytest.mark.unit
def test_rule_repository_returns_rules_in_stable_id_order() -> None:
    """Repository output should not depend on insertion order."""

    repository = InMemoryRuleRepository()
    workspace_id = uuid4()

    first_rule_id = UUID("00000000-0000-0000-0000-000000000001")
    second_rule_id = UUID("00000000-0000-0000-0000-000000000002")

    second_rule = _rule(
        workspace_id=workspace_id,
        rule_id=second_rule_id,
        name="Second Rule",
    )
    first_rule = _rule(
        workspace_id=workspace_id,
        rule_id=first_rule_id,
        name="First Rule",
    )

    repository.add(
        workspace_id=workspace_id,
        rule=second_rule,
    )
    repository.add(
        workspace_id=workspace_id,
        rule=first_rule,
    )

    rules = repository.list_by_workspace(
        workspace_id=workspace_id,
    )

    assert tuple(rule.rule_id for rule in rules) == (
        first_rule_id,
        second_rule_id,
    )


@pytest.mark.unit
def test_rule_repository_enforces_workspace_isolation() -> None:
    """Rules from one workspace must not appear in another workspace."""

    repository = InMemoryRuleRepository()
    first_workspace_id = uuid4()
    second_workspace_id = uuid4()

    first_rule = _rule(
        workspace_id=first_workspace_id,
        name="First Workspace Rule",
    )
    second_rule = _rule(
        workspace_id=second_workspace_id,
        name="Second Workspace Rule",
    )

    repository.add(
        workspace_id=first_workspace_id,
        rule=first_rule,
    )
    repository.add(
        workspace_id=second_workspace_id,
        rule=second_rule,
    )

    assert repository.list_by_workspace(
        workspace_id=first_workspace_id,
    ) == (first_rule,)
    assert repository.list_by_workspace(
        workspace_id=second_workspace_id,
    ) == (second_rule,)
    assert repository.count == 2


@pytest.mark.unit
def test_rule_repository_replaces_existing_rule_id() -> None:
    """Saving the same workspace and rule ID should replace that rule."""

    repository = InMemoryRuleRepository()
    workspace_id = uuid4()
    rule_id = uuid4()

    original_rule = _rule(
        workspace_id=workspace_id,
        rule_id=rule_id,
        name="Original Rule",
    )
    replacement_rule = _rule(
        workspace_id=workspace_id,
        rule_id=rule_id,
        name="Replacement Rule",
    )

    repository.add(
        workspace_id=workspace_id,
        rule=original_rule,
    )
    repository.add(
        workspace_id=workspace_id,
        rule=replacement_rule,
    )

    rules = repository.list_by_workspace(
        workspace_id=workspace_id,
    )

    assert rules == (replacement_rule,)
    assert repository.count == 1


@pytest.mark.unit
def test_rule_repository_rejects_mismatched_workspace() -> None:
    """Supplied workspace and rule ownership must agree."""

    repository = InMemoryRuleRepository()

    rule_workspace_id = uuid4()
    supplied_workspace_id = uuid4()
    rule = _rule(
        workspace_id=rule_workspace_id,
    )

    with pytest.raises(
        InMemoryRepositoryError,
        match=r"rule\.workspace_id must match the supplied workspace_id",
    ):
        repository.add(
            workspace_id=supplied_workspace_id,
            rule=rule,
        )


# ---------------------------------------------------------------------
# DECISION REPOSITORY TESTS
# ---------------------------------------------------------------------


@pytest.mark.unit
def test_decision_repository_saves_and_returns_decision() -> None:
    """A saved decision should be retrievable by its composite key."""

    repository = InMemoryRuleDecisionRepository()
    workspace_id = uuid4()
    transaction_id = uuid4()

    decision = _decision(
        workspace_id=workspace_id,
        transaction_id=transaction_id,
    )

    repository.save(decision=decision)

    stored_decision = repository.get_by_transaction(
        workspace_id=workspace_id,
        transaction_id=transaction_id,
    )

    assert stored_decision is decision
    assert (
        repository.count_by_workspace(
            workspace_id=workspace_id,
        )
        == 1
    )
    assert repository.count == 1


@pytest.mark.unit
def test_decision_repository_enforces_workspace_isolation() -> None:
    """A saved decision must not be accessible from another workspace."""

    repository = InMemoryRuleDecisionRepository()
    owning_workspace_id = uuid4()
    other_workspace_id = uuid4()
    transaction_id = uuid4()

    decision = _decision(
        workspace_id=owning_workspace_id,
        transaction_id=transaction_id,
    )
    repository.save(decision=decision)

    result = repository.get_by_transaction(
        workspace_id=other_workspace_id,
        transaction_id=transaction_id,
    )

    assert result is None


@pytest.mark.unit
def test_decision_repository_replaces_latest_decision() -> None:
    """Reprocessing should replace the latest decision for the same key."""

    repository = InMemoryRuleDecisionRepository()
    workspace_id = uuid4()
    transaction_id = uuid4()

    original_decision = _decision(
        workspace_id=workspace_id,
        transaction_id=transaction_id,
        decision_message="Original decision.",
    )
    replacement_decision = _decision(
        workspace_id=workspace_id,
        transaction_id=transaction_id,
        decision_message="Replacement decision.",
    )

    repository.save(decision=original_decision)
    repository.save(decision=replacement_decision)

    stored_decision = repository.get_by_transaction(
        workspace_id=workspace_id,
        transaction_id=transaction_id,
    )

    assert stored_decision is replacement_decision
    assert repository.count == 1


@pytest.mark.unit
def test_decision_repository_lists_in_stable_transaction_order() -> None:
    """Workspace decisions should use predictable transaction-ID order."""

    repository = InMemoryRuleDecisionRepository()
    workspace_id = uuid4()

    first_transaction_id = UUID("00000000-0000-0000-0000-000000000001")
    second_transaction_id = UUID("00000000-0000-0000-0000-000000000002")

    second_decision = _decision(
        workspace_id=workspace_id,
        transaction_id=second_transaction_id,
    )
    first_decision = _decision(
        workspace_id=workspace_id,
        transaction_id=first_transaction_id,
    )

    repository.save(decision=second_decision)
    repository.save(decision=first_decision)

    decisions = repository.list_by_workspace(
        workspace_id=workspace_id,
    )

    assert tuple(decision.transaction_id for decision in decisions) == (
        first_transaction_id,
        second_transaction_id,
    )


@pytest.mark.unit
def test_decision_repository_rejects_invalid_decision() -> None:
    """The adapter should reject objects that are not decision DTOs."""

    repository = InMemoryRuleDecisionRepository()

    with pytest.raises(
        InMemoryRepositoryError,
        match="decision must be a RuleEngineDecisionDTO",
    ):
        repository.save(
            decision=_invalid("not-a-decision"),
        )
