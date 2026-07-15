"""
Unit tests for the BSI rule-engine application service.

These tests verify:

- Dependency-port validation
- Command validation
- Transaction and rule loading
- Successful application orchestration
- Unmatched and conflict decisions
- Repository contract protection
- Workspace isolation
- Persistence behavior
- Failure propagation
- Execution ordering
- Service immutability
"""

from dataclasses import FrozenInstanceError
from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from bsi.application.rule_engine.commands import (
    EvaluateTransactionRulesCommand,
)
from bsi.application.rule_engine.dto import RuleEngineDecisionDTO
from bsi.application.rule_engine.service import (
    RuleEngineApplicationService,
    RuleEngineRepositoryContractError,
    RuleEngineServiceError,
    TransactionNotFoundError,
)
from bsi.domain.rules.conditions import RuleCondition
from bsi.domain.rules.engine import (
    RuleDecisionStatus,
    RuleEngineError,
)
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


def _invalid(value: object) -> Any:
    """
    Bypass static typing for intentional runtime-validation tests.

    Production code should provide objects satisfying the application
    ports. Tests use this helper to verify runtime protection.
    """

    return cast(Any, value)


class RecordingTransactionReader:
    """In-memory transaction reader recording every invocation."""

    def __init__(
        self,
        *,
        transaction: NormalizedTransaction | None,
        events: list[str] | None = None,
    ) -> None:
        """Initialize the reader with one optional transaction."""

        self.transaction = transaction
        self.calls: list[tuple[UUID, UUID]] = []
        self.events = events if events is not None else []

    def get_by_id(
        self,
        *,
        workspace_id: UUID,
        transaction_id: UUID,
    ) -> NormalizedTransaction | None:
        """Return the configured transaction and record the request."""

        self.calls.append(
            (
                workspace_id,
                transaction_id,
            )
        )
        self.events.append("transaction")

        return self.transaction


class RecordingRuleReader:
    """In-memory rule reader recording workspace requests."""

    def __init__(
        self,
        *,
        rules: tuple[RuleDefinition, ...] = (),
        events: list[str] | None = None,
    ) -> None:
        """Initialize the reader with an immutable rule collection."""

        self.rules = rules
        self.calls: list[UUID] = []
        self.events = events if events is not None else []

    def list_by_workspace(
        self,
        *,
        workspace_id: UUID,
    ) -> tuple[RuleDefinition, ...]:
        """Return configured rules and record the workspace request."""

        self.calls.append(workspace_id)
        self.events.append("rules")

        return self.rules


class RecordingDecisionWriter:
    """In-memory writer recording completed application decisions."""

    def __init__(
        self,
        *,
        events: list[str] | None = None,
    ) -> None:
        """Initialize empty decision storage."""

        self.saved_decisions: list[RuleEngineDecisionDTO] = []
        self.events = events if events is not None else []

    def save(
        self,
        *,
        decision: RuleEngineDecisionDTO,
    ) -> None:
        """Store one decision and record the operation."""

        self.events.append("save")
        self.saved_decisions.append(decision)


class FailingDecisionWriter:
    """Writer simulating an infrastructure persistence failure."""

    def save(
        self,
        *,
        decision: RuleEngineDecisionDTO,
    ) -> None:
        """Raise an infrastructure failure instead of persisting."""

        del decision

        raise RuntimeError("Database unavailable.")


class InvalidTransactionResultReader:
    """Reader violating the transaction-reader return contract."""

    def get_by_id(
        self,
        *,
        workspace_id: UUID,
        transaction_id: UUID,
    ) -> object:
        """Return an invalid object instead of a transaction."""

        del workspace_id
        del transaction_id

        return {}


class InvalidRuleCollectionReader:
    """Rule reader returning a mutable list instead of a tuple."""

    def list_by_workspace(
        self,
        *,
        workspace_id: UUID,
    ) -> object:
        """Return an invalid mutable collection."""

        del workspace_id

        return []


class InvalidRuleItemReader:
    """Rule reader returning a tuple containing an invalid item."""

    def list_by_workspace(
        self,
        *,
        workspace_id: UUID,
    ) -> object:
        """Return an invalid rule item."""

        del workspace_id

        return ({},)


class EmptyObject:
    """Object implementing none of the required application ports."""


def _transaction(
    *,
    transaction_id: UUID | None = None,
    description: str = "UTILITY PAYMENT",
) -> NormalizedTransaction:
    """Create one normalized transaction for service tests."""

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


def _rule(
    *,
    workspace_id: UUID,
    name: str = "Utility Rule",
    keyword: str = "utility",
    output_account_id: UUID | None = None,
    priority: int = 100,
    rule_id: UUID | None = None,
) -> RuleDefinition:
    """Create one active deterministic rule."""

    resolved_output_account_id = (
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
            coa_account_id=resolved_output_account_id,
        ),
        status=RuleStatus.ACTIVE,
        priority=priority,
    )


def _service(
    *,
    transaction_reader: object,
    rule_reader: object,
    decision_writer: object,
) -> RuleEngineApplicationService:
    """Construct the service while allowing invalid dependency tests."""

    return RuleEngineApplicationService(
        transaction_reader=_invalid(transaction_reader),
        rule_reader=_invalid(rule_reader),
        decision_writer=_invalid(decision_writer),
    )


def test_service_accepts_compatible_dependencies() -> None:
    """Structurally compatible adapters can construct the service."""

    service = RuleEngineApplicationService(
        transaction_reader=RecordingTransactionReader(
            transaction=None,
        ),
        rule_reader=RecordingRuleReader(),
        decision_writer=RecordingDecisionWriter(),
    )

    assert isinstance(
        service,
        RuleEngineApplicationService,
    )


def test_service_rejects_invalid_transaction_reader() -> None:
    """The transaction dependency must satisfy its application port."""

    with pytest.raises(
        RuleEngineServiceError,
        match="transaction_reader must implement TransactionReaderPort",
    ):
        _service(
            transaction_reader=EmptyObject(),
            rule_reader=RecordingRuleReader(),
            decision_writer=RecordingDecisionWriter(),
        )


def test_service_rejects_invalid_rule_reader() -> None:
    """The rule dependency must satisfy RuleReaderPort."""

    with pytest.raises(
        RuleEngineServiceError,
        match="rule_reader must implement RuleReaderPort",
    ):
        _service(
            transaction_reader=RecordingTransactionReader(
                transaction=None,
            ),
            rule_reader=EmptyObject(),
            decision_writer=RecordingDecisionWriter(),
        )


def test_service_rejects_invalid_decision_writer() -> None:
    """The writer dependency must satisfy RuleDecisionWriterPort."""

    with pytest.raises(
        RuleEngineServiceError,
        match="decision_writer must implement RuleDecisionWriterPort",
    ):
        _service(
            transaction_reader=RecordingTransactionReader(
                transaction=None,
            ),
            rule_reader=RecordingRuleReader(),
            decision_writer=EmptyObject(),
        )


def test_execute_rejects_invalid_command() -> None:
    """The service accepts only the authoritative application command."""

    service = RuleEngineApplicationService(
        transaction_reader=RecordingTransactionReader(
            transaction=None,
        ),
        rule_reader=RecordingRuleReader(),
        decision_writer=RecordingDecisionWriter(),
    )

    with pytest.raises(
        RuleEngineServiceError,
        match="command must be an EvaluateTransactionRulesCommand",
    ):
        service.execute(
            _invalid({}),
        )


def test_successful_execution_returns_mapped_decision() -> None:
    """The service coordinates a complete deterministic mapping."""

    workspace_id = uuid4()
    account_id = uuid4()
    transaction = _transaction()

    rule = _rule(
        workspace_id=workspace_id,
        output_account_id=account_id,
    )

    writer = RecordingDecisionWriter()

    service = RuleEngineApplicationService(
        transaction_reader=RecordingTransactionReader(
            transaction=transaction,
        ),
        rule_reader=RecordingRuleReader(
            rules=(rule,),
        ),
        decision_writer=writer,
    )

    decision = service.execute(
        EvaluateTransactionRulesCommand(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
        )
    )

    assert decision.status == RuleDecisionStatus.MAPPED.value
    assert decision.can_map is True
    assert decision.output_account_id == account_id
    assert decision.winning_rule_id == rule.rule_id
    assert decision.requires_review is False


def test_service_passes_command_ids_to_transaction_reader() -> None:
    """Workspace and transaction IDs are forwarded unchanged."""

    workspace_id = uuid4()
    transaction = _transaction()

    transaction_reader = RecordingTransactionReader(
        transaction=transaction,
    )

    service = RuleEngineApplicationService(
        transaction_reader=transaction_reader,
        rule_reader=RecordingRuleReader(),
        decision_writer=RecordingDecisionWriter(),
    )

    service.execute(
        EvaluateTransactionRulesCommand(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
        )
    )

    assert transaction_reader.calls == [
        (
            workspace_id,
            transaction.transaction_id,
        )
    ]


def test_service_passes_workspace_id_to_rule_reader() -> None:
    """Rules are loaded using the command workspace boundary."""

    workspace_id = uuid4()
    transaction = _transaction()

    rule_reader = RecordingRuleReader()

    service = RuleEngineApplicationService(
        transaction_reader=RecordingTransactionReader(
            transaction=transaction,
        ),
        rule_reader=rule_reader,
        decision_writer=RecordingDecisionWriter(),
    )

    service.execute(
        EvaluateTransactionRulesCommand(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
        )
    )

    assert rule_reader.calls == [
        workspace_id,
    ]


def test_service_executes_operations_in_expected_order() -> None:
    """Loading must occur before evaluation-result persistence."""

    workspace_id = uuid4()
    transaction = _transaction()
    events: list[str] = []

    service = RuleEngineApplicationService(
        transaction_reader=RecordingTransactionReader(
            transaction=transaction,
            events=events,
        ),
        rule_reader=RecordingRuleReader(
            events=events,
        ),
        decision_writer=RecordingDecisionWriter(
            events=events,
        ),
    )

    service.execute(
        EvaluateTransactionRulesCommand(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
        )
    )

    assert events == [
        "transaction",
        "rules",
        "save",
    ]


def test_returned_decision_is_the_saved_decision() -> None:
    """The exact DTO returned to the caller is persisted."""

    workspace_id = uuid4()
    transaction = _transaction()
    writer = RecordingDecisionWriter()

    service = RuleEngineApplicationService(
        transaction_reader=RecordingTransactionReader(
            transaction=transaction,
        ),
        rule_reader=RecordingRuleReader(),
        decision_writer=writer,
    )

    decision = service.execute(
        EvaluateTransactionRulesCommand(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
        )
    )

    assert writer.saved_decisions == [
        decision,
    ]
    assert writer.saved_decisions[0] is decision


def test_missing_transaction_raises_not_found_error() -> None:
    """A missing workspace-owned transaction stops the workflow."""

    workspace_id = uuid4()
    transaction_id = uuid4()

    rule_reader = RecordingRuleReader()
    writer = RecordingDecisionWriter()

    service = RuleEngineApplicationService(
        transaction_reader=RecordingTransactionReader(
            transaction=None,
        ),
        rule_reader=rule_reader,
        decision_writer=writer,
    )

    with pytest.raises(
        TransactionNotFoundError,
        match="Transaction was not found in the supplied workspace",
    ):
        service.execute(
            EvaluateTransactionRulesCommand(
                workspace_id=workspace_id,
                transaction_id=transaction_id,
            )
        )

    assert rule_reader.calls == []
    assert writer.saved_decisions == []


def test_invalid_transaction_repository_result_is_rejected() -> None:
    """Invalid transaction adapter results cannot reach the domain engine."""

    workspace_id = uuid4()
    transaction_id = uuid4()

    rule_reader = RecordingRuleReader()
    writer = RecordingDecisionWriter()

    service = _service(
        transaction_reader=InvalidTransactionResultReader(),
        rule_reader=rule_reader,
        decision_writer=writer,
    )

    with pytest.raises(
        RuleEngineRepositoryContractError,
        match=("TransactionReaderPort must return a NormalizedTransaction or None"),
    ):
        service.execute(
            EvaluateTransactionRulesCommand(
                workspace_id=workspace_id,
                transaction_id=transaction_id,
            )
        )

    assert rule_reader.calls == []
    assert writer.saved_decisions == []


def test_mutable_rule_collection_is_rejected() -> None:
    """Rule repositories must return immutable tuples."""

    workspace_id = uuid4()
    transaction = _transaction()
    writer = RecordingDecisionWriter()

    service = _service(
        transaction_reader=RecordingTransactionReader(
            transaction=transaction,
        ),
        rule_reader=InvalidRuleCollectionReader(),
        decision_writer=writer,
    )

    with pytest.raises(
        RuleEngineRepositoryContractError,
        match="RuleReaderPort must return a tuple",
    ):
        service.execute(
            EvaluateTransactionRulesCommand(
                workspace_id=workspace_id,
                transaction_id=transaction.transaction_id,
            )
        )

    assert writer.saved_decisions == []


def test_invalid_rule_item_is_rejected() -> None:
    """Every repository rule must be a RuleDefinition."""

    workspace_id = uuid4()
    transaction = _transaction()
    writer = RecordingDecisionWriter()

    service = _service(
        transaction_reader=RecordingTransactionReader(
            transaction=transaction,
        ),
        rule_reader=InvalidRuleItemReader(),
        decision_writer=writer,
    )

    with pytest.raises(
        RuleEngineRepositoryContractError,
        match="must return only RuleDefinition objects",
    ):
        service.execute(
            EvaluateTransactionRulesCommand(
                workspace_id=workspace_id,
                transaction_id=transaction.transaction_id,
            )
        )

    assert writer.saved_decisions == []


def test_empty_rule_collection_produces_saved_unmatched_decision() -> None:
    """No configured rules is valid and produces review evidence."""

    workspace_id = uuid4()
    transaction = _transaction()
    writer = RecordingDecisionWriter()

    service = RuleEngineApplicationService(
        transaction_reader=RecordingTransactionReader(
            transaction=transaction,
        ),
        rule_reader=RecordingRuleReader(
            rules=(),
        ),
        decision_writer=writer,
    )

    decision = service.execute(
        EvaluateTransactionRulesCommand(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
        )
    )

    assert decision.status == RuleDecisionStatus.UNMATCHED.value
    assert decision.can_map is False
    assert decision.requires_review is True
    assert decision.evaluated_rule_count == 0
    assert writer.saved_decisions == [
        decision,
    ]


def test_same_output_rules_produce_mapped_with_review() -> None:
    """Redundant same-output rules remain safely mappable."""

    workspace_id = uuid4()
    transaction = _transaction()
    shared_account_id = uuid4()

    service = RuleEngineApplicationService(
        transaction_reader=RecordingTransactionReader(
            transaction=transaction,
        ),
        rule_reader=RecordingRuleReader(
            rules=(
                _rule(
                    workspace_id=workspace_id,
                    name="Utility Rule A",
                    output_account_id=shared_account_id,
                ),
                _rule(
                    workspace_id=workspace_id,
                    name="Utility Rule B",
                    output_account_id=shared_account_id,
                ),
            ),
        ),
        decision_writer=RecordingDecisionWriter(),
    )

    decision = service.execute(
        EvaluateTransactionRulesCommand(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
        )
    )

    assert decision.status == RuleDecisionStatus.MAPPED_WITH_REVIEW.value
    assert decision.can_map is True
    assert decision.output_account_id == shared_account_id
    assert decision.winning_rule_id is None
    assert decision.requires_review is True


def test_competing_outputs_produce_blocked_decision() -> None:
    """Different top outputs remain blocked through the service."""

    workspace_id = uuid4()
    transaction = _transaction()

    service = RuleEngineApplicationService(
        transaction_reader=RecordingTransactionReader(
            transaction=transaction,
        ),
        rule_reader=RecordingRuleReader(
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
        ),
        decision_writer=RecordingDecisionWriter(),
    )

    decision = service.execute(
        EvaluateTransactionRulesCommand(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
        )
    )

    assert decision.status == RuleDecisionStatus.BLOCKED_CONFLICT.value
    assert decision.can_map is False
    assert decision.output_account_id is None
    assert decision.is_conflict_blocked is True
    assert decision.requires_review is True


def test_cross_workspace_rule_error_propagates_without_persistence() -> None:
    """Foreign-workspace rules are rejected by the domain engine."""

    requested_workspace_id = uuid4()
    foreign_workspace_id = uuid4()
    transaction = _transaction()
    writer = RecordingDecisionWriter()

    foreign_rule = _rule(
        workspace_id=foreign_workspace_id,
    )

    service = RuleEngineApplicationService(
        transaction_reader=RecordingTransactionReader(
            transaction=transaction,
        ),
        rule_reader=RecordingRuleReader(
            rules=(foreign_rule,),
        ),
        decision_writer=writer,
    )

    with pytest.raises(
        RuleEngineError,
        match="Every rule must belong to the supplied workspace",
    ):
        service.execute(
            EvaluateTransactionRulesCommand(
                workspace_id=requested_workspace_id,
                transaction_id=transaction.transaction_id,
            )
        )

    assert writer.saved_decisions == []


def test_writer_failure_propagates_to_the_caller() -> None:
    """Persistence failures must not be disguised as success."""

    workspace_id = uuid4()
    transaction = _transaction()

    service = RuleEngineApplicationService(
        transaction_reader=RecordingTransactionReader(
            transaction=transaction,
        ),
        rule_reader=RecordingRuleReader(),
        decision_writer=FailingDecisionWriter(),
    )

    with pytest.raises(
        RuntimeError,
        match="Database unavailable",
    ):
        service.execute(
            EvaluateTransactionRulesCommand(
                workspace_id=workspace_id,
                transaction_id=transaction.transaction_id,
            )
        )


def test_service_preserves_nested_evaluation_evidence() -> None:
    """The returned DTO contains the complete rule audit trail."""

    workspace_id = uuid4()
    transaction = _transaction()

    matched_rule = _rule(
        workspace_id=workspace_id,
        name="Matched Rule",
        keyword="utility",
    )
    unmatched_rule = _rule(
        workspace_id=workspace_id,
        name="Unmatched Rule",
        keyword="rent",
    )

    service = RuleEngineApplicationService(
        transaction_reader=RecordingTransactionReader(
            transaction=transaction,
        ),
        rule_reader=RecordingRuleReader(
            rules=(
                matched_rule,
                unmatched_rule,
            ),
        ),
        decision_writer=RecordingDecisionWriter(),
    )

    decision = service.execute(
        EvaluateTransactionRulesCommand(
            workspace_id=workspace_id,
            transaction_id=transaction.transaction_id,
        )
    )

    assert decision.evaluated_rule_count == 2
    assert decision.eligible_rule_count == 2
    assert decision.matched_rule_count == 1
    assert decision.unmatched_eligible_rule_count == 1
    assert len(decision.evaluations) == 2


def test_application_service_is_immutable() -> None:
    """Injected dependencies cannot be replaced after construction."""

    service = RuleEngineApplicationService(
        transaction_reader=RecordingTransactionReader(
            transaction=None,
        ),
        rule_reader=RecordingRuleReader(),
        decision_writer=RecordingDecisionWriter(),
    )
    service_for_mutation = cast(Any, service)

    with pytest.raises(FrozenInstanceError):
        service_for_mutation.rule_reader = RecordingRuleReader()
