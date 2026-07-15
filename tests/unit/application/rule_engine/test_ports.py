"""
Unit tests for BSI rule-engine application ports.

These tests verify:

- Structural protocol compatibility
- Runtime protocol inspection
- Transaction-reader behavior
- Rule-reader behavior
- Decision-writer behavior
- Workspace isolation in test adapters
- Empty repository behavior
- Interface segregation
- Combined adapter compatibility
"""

from datetime import date
from uuid import UUID, uuid4

from bsi.application.rule_engine.dto import (
    RuleEngineDecisionDTO,
)
from bsi.application.rule_engine.ports import (
    RuleDecisionWriterPort,
    RuleReaderPort,
    TransactionReaderPort,
)
from bsi.domain.rules.conditions import RuleCondition
from bsi.domain.rules.engine import evaluate_transaction_rules
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


class InMemoryTransactionReader:
    """
    Test adapter implementing TransactionReaderPort structurally.

    It stores transactions using both workspace and transaction IDs so
    tests can model tenant isolation.
    """

    def __init__(
        self,
        transactions: (
            dict[
                tuple[UUID, UUID],
                NormalizedTransaction,
            ]
            | None
        ) = None,
    ) -> None:
        """Initialize the adapter with optional transaction records."""

        self._transactions = dict(transactions) if transactions is not None else {}

    def get_by_id(
        self,
        *,
        workspace_id: UUID,
        transaction_id: UUID,
    ) -> NormalizedTransaction | None:
        """Return a transaction owned by the supplied workspace."""

        return self._transactions.get(
            (
                workspace_id,
                transaction_id,
            )
        )


class InMemoryRuleReader:
    """Test adapter implementing RuleReaderPort structurally."""

    def __init__(
        self,
        rules_by_workspace: (
            dict[
                UUID,
                tuple[RuleDefinition, ...],
            ]
            | None
        ) = None,
    ) -> None:
        """Initialize the adapter with workspace-owned rule collections."""

        self._rules_by_workspace = (
            dict(rules_by_workspace) if rules_by_workspace is not None else {}
        )

    def list_by_workspace(
        self,
        *,
        workspace_id: UUID,
    ) -> tuple[RuleDefinition, ...]:
        """Return immutable rules belonging to one workspace."""

        return self._rules_by_workspace.get(
            workspace_id,
            (),
        )


class InMemoryRuleDecisionWriter:
    """Test adapter implementing RuleDecisionWriterPort structurally."""

    def __init__(self) -> None:
        """Initialize empty decision storage."""

        self.saved_decisions: list[RuleEngineDecisionDTO] = []

    def save(
        self,
        *,
        decision: RuleEngineDecisionDTO,
    ) -> None:
        """Store one application-facing rule-engine decision."""

        self.saved_decisions.append(decision)


class CombinedRuleEngineAdapter:
    """
    Test adapter implementing all three application ports.

    Production code may use separate adapters, but one object may
    structurally satisfy multiple protocols when appropriate.
    """

    def __init__(
        self,
        *,
        transactions: dict[
            tuple[UUID, UUID],
            NormalizedTransaction,
        ],
        rules_by_workspace: dict[
            UUID,
            tuple[RuleDefinition, ...],
        ],
    ) -> None:
        """Initialize transaction, rule, and decision storage."""

        self._transactions = dict(transactions)
        self._rules_by_workspace = dict(rules_by_workspace)
        self.saved_decisions: list[RuleEngineDecisionDTO] = []

    def get_by_id(
        self,
        *,
        workspace_id: UUID,
        transaction_id: UUID,
    ) -> NormalizedTransaction | None:
        """Return a workspace-owned transaction."""

        return self._transactions.get(
            (
                workspace_id,
                transaction_id,
            )
        )

    def list_by_workspace(
        self,
        *,
        workspace_id: UUID,
    ) -> tuple[RuleDefinition, ...]:
        """Return rules belonging to one workspace."""

        return self._rules_by_workspace.get(
            workspace_id,
            (),
        )

    def save(
        self,
        *,
        decision: RuleEngineDecisionDTO,
    ) -> None:
        """Store one completed decision."""

        self.saved_decisions.append(decision)


class TransactionReaderOnly:
    """Object implementing only the transaction-reader interface."""

    def get_by_id(
        self,
        *,
        workspace_id: UUID,
        transaction_id: UUID,
    ) -> NormalizedTransaction | None:
        """Return no transaction."""

        del workspace_id
        del transaction_id

        return None


class EmptyObject:
    """Object that implements none of the rule-engine ports."""


def _transaction(
    *,
    transaction_id: UUID | None = None,
) -> NormalizedTransaction:
    """Create one normalized transaction for port tests."""

    return NormalizedTransaction.create(
        transaction_id=transaction_id,
        transaction_date=date(2026, 7, 15),
        original_description="UTILITY PAYMENT",
        payment="125.00",
        source=TransactionSource(
            file_name="statement.xlsx",
            source_row_number=10,
        ),
    )


def _rule(
    *,
    workspace_id: UUID,
) -> RuleDefinition:
    """Create one active deterministic rule."""

    return RuleDefinition.create(
        workspace_id=workspace_id,
        name="Utility Rule",
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
    transaction: NormalizedTransaction,
    rules: tuple[RuleDefinition, ...],
) -> RuleEngineDecisionDTO:
    """Create one application-facing engine decision."""

    domain_result = evaluate_transaction_rules(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=rules,
    )

    return RuleEngineDecisionDTO.from_domain(domain_result)


def test_transaction_adapter_satisfies_reader_port() -> None:
    """Compatible objects satisfy the reader protocol structurally."""

    adapter = InMemoryTransactionReader()

    assert isinstance(
        adapter,
        TransactionReaderPort,
    )


def test_rule_adapter_satisfies_reader_port() -> None:
    """Compatible rule repositories satisfy RuleReaderPort."""

    adapter = InMemoryRuleReader()

    assert isinstance(
        adapter,
        RuleReaderPort,
    )


def test_decision_adapter_satisfies_writer_port() -> None:
    """Compatible persistence adapters satisfy the writer protocol."""

    adapter = InMemoryRuleDecisionWriter()

    assert isinstance(
        adapter,
        RuleDecisionWriterPort,
    )


def test_transaction_reader_returns_stored_transaction() -> None:
    """The reader receives workspace and transaction identifiers."""

    workspace_id = uuid4()
    transaction = _transaction()

    adapter = InMemoryTransactionReader(
        {
            (
                workspace_id,
                transaction.transaction_id,
            ): transaction,
        }
    )

    result = adapter.get_by_id(
        workspace_id=workspace_id,
        transaction_id=transaction.transaction_id,
    )

    assert result is transaction


def test_transaction_reader_returns_none_when_missing() -> None:
    """A missing transaction is represented by None."""

    adapter = InMemoryTransactionReader()

    result = adapter.get_by_id(
        workspace_id=uuid4(),
        transaction_id=uuid4(),
    )

    assert result is None


def test_transaction_reader_enforces_workspace_boundary() -> None:
    """A transaction cannot be loaded through another workspace."""

    owning_workspace_id = uuid4()
    foreign_workspace_id = uuid4()
    transaction = _transaction()

    adapter = InMemoryTransactionReader(
        {
            (
                owning_workspace_id,
                transaction.transaction_id,
            ): transaction,
        }
    )

    result = adapter.get_by_id(
        workspace_id=foreign_workspace_id,
        transaction_id=transaction.transaction_id,
    )

    assert result is None


def test_rule_reader_returns_workspace_rules() -> None:
    """The rule reader returns the configured immutable collection."""

    workspace_id = uuid4()
    rule = _rule(
        workspace_id=workspace_id,
    )

    adapter = InMemoryRuleReader(
        {
            workspace_id: (rule,),
        }
    )

    result = adapter.list_by_workspace(
        workspace_id=workspace_id,
    )

    assert result == (rule,)
    assert isinstance(result, tuple)


def test_rule_reader_returns_empty_tuple_when_no_rules_exist() -> None:
    """No configured rules are represented by an empty tuple."""

    adapter = InMemoryRuleReader()

    result = adapter.list_by_workspace(
        workspace_id=uuid4(),
    )

    assert result == ()


def test_rule_reader_does_not_return_foreign_workspace_rules() -> None:
    """Workspace rule collections remain tenant isolated."""

    owning_workspace_id = uuid4()
    foreign_workspace_id = uuid4()

    rule = _rule(
        workspace_id=owning_workspace_id,
    )

    adapter = InMemoryRuleReader(
        {
            owning_workspace_id: (rule,),
        }
    )

    result = adapter.list_by_workspace(
        workspace_id=foreign_workspace_id,
    )

    assert result == ()


def test_decision_writer_stores_application_dto() -> None:
    """The writer receives the application-facing decision DTO."""

    workspace_id = uuid4()
    transaction = _transaction()
    rule = _rule(
        workspace_id=workspace_id,
    )

    decision = _decision(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=(rule,),
    )

    adapter = InMemoryRuleDecisionWriter()

    adapter.save(
        decision=decision,
    )

    assert adapter.saved_decisions == [
        decision,
    ]


def test_decision_writer_preserves_multiple_decisions() -> None:
    """The test writer stores decisions in invocation order."""

    first_workspace_id = uuid4()
    second_workspace_id = uuid4()

    first_transaction = _transaction()
    second_transaction = _transaction()

    first_decision = _decision(
        workspace_id=first_workspace_id,
        transaction=first_transaction,
        rules=(),
    )
    second_decision = _decision(
        workspace_id=second_workspace_id,
        transaction=second_transaction,
        rules=(),
    )

    adapter = InMemoryRuleDecisionWriter()

    adapter.save(
        decision=first_decision,
    )
    adapter.save(
        decision=second_decision,
    )

    assert adapter.saved_decisions == [
        first_decision,
        second_decision,
    ]


def test_reader_only_object_does_not_satisfy_other_ports() -> None:
    """Interface segregation keeps unrelated capabilities separate."""

    adapter = TransactionReaderOnly()

    assert isinstance(
        adapter,
        TransactionReaderPort,
    )
    assert not isinstance(
        adapter,
        RuleReaderPort,
    )
    assert not isinstance(
        adapter,
        RuleDecisionWriterPort,
    )


def test_empty_object_satisfies_no_ports() -> None:
    """Objects without required methods fail runtime protocol checks."""

    adapter = EmptyObject()

    assert not isinstance(
        adapter,
        TransactionReaderPort,
    )
    assert not isinstance(
        adapter,
        RuleReaderPort,
    )
    assert not isinstance(
        adapter,
        RuleDecisionWriterPort,
    )


def test_combined_adapter_satisfies_every_port() -> None:
    """One adapter may structurally implement multiple interfaces."""

    adapter = CombinedRuleEngineAdapter(
        transactions={},
        rules_by_workspace={},
    )

    assert isinstance(
        adapter,
        TransactionReaderPort,
    )
    assert isinstance(
        adapter,
        RuleReaderPort,
    )
    assert isinstance(
        adapter,
        RuleDecisionWriterPort,
    )


def test_combined_adapter_supports_complete_port_workflow() -> None:
    """The three port operations can cooperate without UI dependencies."""

    workspace_id = uuid4()
    transaction = _transaction()
    rule = _rule(
        workspace_id=workspace_id,
    )

    adapter = CombinedRuleEngineAdapter(
        transactions={
            (
                workspace_id,
                transaction.transaction_id,
            ): transaction,
        },
        rules_by_workspace={
            workspace_id: (rule,),
        },
    )

    loaded_transaction = adapter.get_by_id(
        workspace_id=workspace_id,
        transaction_id=transaction.transaction_id,
    )

    assert loaded_transaction is transaction

    loaded_rules = adapter.list_by_workspace(
        workspace_id=workspace_id,
    )

    decision = _decision(
        workspace_id=workspace_id,
        transaction=loaded_transaction,
        rules=loaded_rules,
    )

    adapter.save(
        decision=decision,
    )

    assert adapter.saved_decisions == [
        decision,
    ]
    assert decision.can_map is True
