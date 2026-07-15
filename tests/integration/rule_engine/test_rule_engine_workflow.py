"""
Integration tests for the deterministic rule-engine workflow.

These tests connect the real application service with the real
in-memory infrastructure adapters.

They verify:

- Successful deterministic GL mapping
- Unmatched transaction handling
- Workspace isolation
- Reprocessing and latest-decision replacement
- Persistence of application decisions

Unlike unit tests, these tests exercise multiple architectural layers
together.
"""

from datetime import date
from uuid import UUID, uuid4

import pytest

from bsi.application.rule_engine.commands import (
    EvaluateTransactionRulesCommand,
)
from bsi.application.rule_engine.service import (
    RuleEngineApplicationService,
    TransactionNotFoundError,
)
from bsi.domain.rules.conditions import RuleCondition
from bsi.domain.rules.engine import RuleDecisionStatus
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
    InMemoryRuleDecisionRepository,
    InMemoryRuleRepository,
    InMemoryTransactionRepository,
)

DEFAULT_TRANSACTION_DATE = date(2026, 7, 15)


def _transaction(
    *,
    transaction_id: UUID | None = None,
    description: str = "UTILITY PAYMENT",
) -> NormalizedTransaction:
    """Create one valid normalized bank transaction."""

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
    keyword: str = "utility",
    output_account_id: UUID | None = None,
    rule_id: UUID | None = None,
    priority: int = 100,
) -> RuleDefinition:
    """Create one complete active deterministic rule."""

    resolved_output_account_id = (
        output_account_id if output_account_id is not None else uuid4()
    )

    return RuleDefinition.create(
        rule_id=rule_id,
        workspace_id=workspace_id,
        name=f"{keyword.title()} Mapping Rule",
        conditions=(
            RuleCondition(
                field=RuleConditionField.SEARCHABLE_TEXT,
                operator=RuleOperator.CONTAINS,
                value=keyword,
            ),
        ),
        output=RuleOutput(
            coa_account_id=resolved_output_account_id,
        ),
        status=RuleStatus.ACTIVE,
        priority=priority,
    )


def _service(
    *,
    transaction_repository: InMemoryTransactionRepository,
    rule_repository: InMemoryRuleRepository,
    decision_repository: InMemoryRuleDecisionRepository,
) -> RuleEngineApplicationService:
    """Build the real application service with in-memory adapters."""

    return RuleEngineApplicationService(
        transaction_reader=transaction_repository,
        rule_reader=rule_repository,
        decision_writer=decision_repository,
    )


@pytest.mark.integration
def test_matching_transaction_is_mapped_and_persisted() -> None:
    """
    A matching transaction should produce and persist a GL mapping.

    This is the main successful vertical slice:

    transaction repository
        → application service
        → domain rule engine
        → decision repository
    """

    workspace_id = uuid4()
    output_account_id = uuid4()

    transaction_repository = InMemoryTransactionRepository()
    rule_repository = InMemoryRuleRepository()
    decision_repository = InMemoryRuleDecisionRepository()

    transaction = _transaction(
        description="MONTHLY UTILITY PAYMENT",
    )
    rule = _rule(
        workspace_id=workspace_id,
        keyword="utility",
        output_account_id=output_account_id,
    )

    transaction_repository.add(
        workspace_id=workspace_id,
        transaction=transaction,
    )
    rule_repository.add(
        workspace_id=workspace_id,
        rule=rule,
    )

    service = _service(
        transaction_repository=transaction_repository,
        rule_repository=rule_repository,
        decision_repository=decision_repository,
    )

    decision = service.execute(
        EvaluateTransactionRulesCommand(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
        )
    )

    stored_decision = decision_repository.get_by_transaction(
        workspace_id=workspace_id,
        transaction_id=transaction.transaction_id,
    )

    assert decision.status == RuleDecisionStatus.MAPPED.value
    assert decision.can_map is True
    assert decision.requires_review is False
    assert decision.output_account_id == output_account_id
    assert decision.winning_rule_id == rule.rule_id
    assert decision.matched_rule_ids == (rule.rule_id,)
    assert decision.evaluated_rule_count == 1
    assert decision.matched_rule_count == 1

    assert stored_decision is decision
    assert decision_repository.count == 1


@pytest.mark.integration
def test_non_matching_transaction_is_saved_as_unmatched() -> None:
    """A transaction with no matching rule should remain auditable."""

    workspace_id = uuid4()

    transaction_repository = InMemoryTransactionRepository()
    rule_repository = InMemoryRuleRepository()
    decision_repository = InMemoryRuleDecisionRepository()

    transaction = _transaction(
        description="MONTHLY RENT PAYMENT",
    )
    rule = _rule(
        workspace_id=workspace_id,
        keyword="utility",
    )

    transaction_repository.add(
        workspace_id=workspace_id,
        transaction=transaction,
    )
    rule_repository.add(
        workspace_id=workspace_id,
        rule=rule,
    )

    service = _service(
        transaction_repository=transaction_repository,
        rule_repository=rule_repository,
        decision_repository=decision_repository,
    )

    decision = service.execute(
        EvaluateTransactionRulesCommand(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
        )
    )

    stored_decision = decision_repository.get_by_transaction(
        workspace_id=workspace_id,
        transaction_id=transaction.transaction_id,
    )

    assert decision.status == RuleDecisionStatus.UNMATCHED.value
    assert decision.can_map is False
    assert decision.output_account_id is None
    assert decision.winning_rule_id is None
    assert decision.matched_rule_ids == ()
    assert decision.evaluated_rule_count == 1
    assert decision.matched_rule_count == 0

    assert stored_decision is decision
    assert decision_repository.count == 1


@pytest.mark.integration
def test_transaction_cannot_be_processed_from_another_workspace() -> None:
    """Tenant isolation must block cross-workspace transaction access."""

    owning_workspace_id = uuid4()
    other_workspace_id = uuid4()

    transaction_repository = InMemoryTransactionRepository()
    rule_repository = InMemoryRuleRepository()
    decision_repository = InMemoryRuleDecisionRepository()

    transaction = _transaction()

    transaction_repository.add(
        workspace_id=owning_workspace_id,
        transaction=transaction,
    )

    service = _service(
        transaction_repository=transaction_repository,
        rule_repository=rule_repository,
        decision_repository=decision_repository,
    )

    with pytest.raises(
        TransactionNotFoundError,
        match="Transaction was not found",
    ):
        service.execute(
            EvaluateTransactionRulesCommand(
                workspace_id=other_workspace_id,
                transaction_id=transaction.transaction_id,
            )
        )

    assert decision_repository.count == 0


@pytest.mark.integration
def test_reprocessing_replaces_latest_transaction_decision() -> None:
    """
    Reprocessing should replace the current in-memory decision.

    First execution:
        no rules → unmatched

    Second execution:
        matching rule added → mapped

    The repository should retain one latest decision for the transaction.
    """

    workspace_id = uuid4()
    output_account_id = uuid4()

    transaction_repository = InMemoryTransactionRepository()
    rule_repository = InMemoryRuleRepository()
    decision_repository = InMemoryRuleDecisionRepository()

    transaction = _transaction(
        description="UTILITY PAYMENT",
    )

    transaction_repository.add(
        workspace_id=workspace_id,
        transaction=transaction,
    )

    service = _service(
        transaction_repository=transaction_repository,
        rule_repository=rule_repository,
        decision_repository=decision_repository,
    )

    command = EvaluateTransactionRulesCommand(
        workspace_id=workspace_id,
        transaction_id=transaction.transaction_id,
    )

    first_decision = service.execute(command)

    assert first_decision.status == RuleDecisionStatus.UNMATCHED.value
    assert decision_repository.count == 1

    rule = _rule(
        workspace_id=workspace_id,
        keyword="utility",
        output_account_id=output_account_id,
    )

    rule_repository.add(
        workspace_id=workspace_id,
        rule=rule,
    )

    second_decision = service.execute(command)

    stored_decision = decision_repository.get_by_transaction(
        workspace_id=workspace_id,
        transaction_id=transaction.transaction_id,
    )

    assert second_decision.status == RuleDecisionStatus.MAPPED.value
    assert second_decision.output_account_id == output_account_id
    assert second_decision.winning_rule_id == rule.rule_id

    assert stored_decision is second_decision
    assert stored_decision is not first_decision
    assert decision_repository.count == 1
