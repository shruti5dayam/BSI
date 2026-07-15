"""
In-memory infrastructure adapters for the BSI rule-engine workflow.

These repositories provide concrete implementations of the application
ports without requiring PostgreSQL, SQLAlchemy, files, or external
services.

They are useful for:

- Unit and integration testing
- Local development
- Streamlit prototypes
- Demonstrating complete application workflows
- Validating architecture before database integration

These adapters intentionally contain no accounting or rule-engine
business logic.
"""

from threading import RLock
from uuid import UUID

from bsi.application.rule_engine.dto import RuleEngineDecisionDTO
from bsi.domain.rules.models import RuleDefinition
from bsi.domain.transactions.models import NormalizedTransaction


class InMemoryRepositoryError(ValueError):
    """Raised when an in-memory repository receives invalid data."""


type TransactionKey = tuple[UUID, UUID]
type DecisionKey = tuple[UUID, UUID]


class InMemoryTransactionRepository:
    """
    Store normalized transactions using workspace isolation.

    Transactions are keyed by:

    `(workspace_id, transaction_id)`

    The same transaction identifier may therefore exist in two separate
    workspaces without crossing tenant boundaries.
    """

    __slots__ = (
        "_lock",
        "_transactions",
    )

    def __init__(self) -> None:
        """Initialize an empty transaction repository."""

        self._transactions: dict[
            TransactionKey,
            NormalizedTransaction,
        ] = {}
        self._lock = RLock()

    def add(
        self,
        *,
        workspace_id: UUID,
        transaction: NormalizedTransaction,
    ) -> None:
        """
        Add or replace one workspace-owned transaction.

        Parameters
        ----------
        workspace_id:
            Workspace that owns the transaction.

        transaction:
            Validated normalized transaction to store.

        Notes
        -----
        Saving the same workspace and transaction identifier again
        replaces the existing record. This makes local seeding
        idempotent.
        """

        validated_workspace_id = _require_uuid(
            workspace_id,
            field_name="workspace_id",
        )
        validated_transaction = _require_transaction(transaction)

        key = (
            validated_workspace_id,
            validated_transaction.transaction_id,
        )

        with self._lock:
            self._transactions[key] = validated_transaction

    def get_by_id(
        self,
        *,
        workspace_id: UUID,
        transaction_id: UUID,
    ) -> NormalizedTransaction | None:
        """
        Return a transaction owned by the supplied workspace.

        Returns
        -------
        NormalizedTransaction | None
            Stored transaction, or None when it does not exist within
            the requested workspace.
        """

        validated_workspace_id = _require_uuid(
            workspace_id,
            field_name="workspace_id",
        )
        validated_transaction_id = _require_uuid(
            transaction_id,
            field_name="transaction_id",
        )

        key = (
            validated_workspace_id,
            validated_transaction_id,
        )

        with self._lock:
            return self._transactions.get(key)

    def contains(
        self,
        *,
        workspace_id: UUID,
        transaction_id: UUID,
    ) -> bool:
        """Return whether a workspace-owned transaction exists."""

        return (
            self.get_by_id(
                workspace_id=workspace_id,
                transaction_id=transaction_id,
            )
            is not None
        )

    @property
    def count(self) -> int:
        """Return the total number of stored transaction records."""

        with self._lock:
            return len(self._transactions)


class InMemoryRuleRepository:
    """
    Store deterministic rules by workspace and rule identifier.

    Rule listing is always returned in stable rule-ID order. Repository
    ordering does not determine the winning rule; authoritative ranking
    remains the responsibility of the domain rule engine.
    """

    __slots__ = (
        "_lock",
        "_rules_by_workspace",
    )

    def __init__(self) -> None:
        """Initialize an empty rule repository."""

        self._rules_by_workspace: dict[
            UUID,
            dict[UUID, RuleDefinition],
        ] = {}
        self._lock = RLock()

    def add(
        self,
        *,
        workspace_id: UUID,
        rule: RuleDefinition,
    ) -> None:
        """
        Add or replace one rule inside a workspace.

        Raises
        ------
        InMemoryRepositoryError
            If the rule belongs to a different workspace.
        """

        validated_workspace_id = _require_uuid(
            workspace_id,
            field_name="workspace_id",
        )
        validated_rule = _require_rule(rule)

        if validated_rule.workspace_id != validated_workspace_id:
            raise InMemoryRepositoryError(
                "rule.workspace_id must match the supplied workspace_id."
            )

        with self._lock:
            workspace_rules = self._rules_by_workspace.setdefault(
                validated_workspace_id,
                {},
            )
            workspace_rules[validated_rule.rule_id] = validated_rule

    def list_by_workspace(
        self,
        *,
        workspace_id: UUID,
    ) -> tuple[RuleDefinition, ...]:
        """
        Return all rules belonging to one workspace.

        Returns
        -------
        tuple[RuleDefinition, ...]
            Immutable rule collection ordered by rule identifier.
        """

        validated_workspace_id = _require_uuid(
            workspace_id,
            field_name="workspace_id",
        )

        with self._lock:
            workspace_rules = self._rules_by_workspace.get(
                validated_workspace_id,
                {},
            )

            return tuple(
                sorted(
                    workspace_rules.values(),
                    key=lambda rule: str(rule.rule_id),
                )
            )

    def count_by_workspace(
        self,
        *,
        workspace_id: UUID,
    ) -> int:
        """Return the number of rules stored for one workspace."""

        validated_workspace_id = _require_uuid(
            workspace_id,
            field_name="workspace_id",
        )

        with self._lock:
            return len(
                self._rules_by_workspace.get(
                    validated_workspace_id,
                    {},
                )
            )

    @property
    def count(self) -> int:
        """Return the number of rules across all workspaces."""

        with self._lock:
            return sum(
                len(workspace_rules)
                for workspace_rules in self._rules_by_workspace.values()
            )


class InMemoryRuleDecisionRepository:
    """
    Store the latest rule-engine decision for each transaction.

    Decisions are keyed by:

    `(workspace_id, transaction_id)`

    Re-saving a decision replaces the previous latest decision. A future
    PostgreSQL adapter may preserve full version history and audit
    lineage while implementing the same application writer port.
    """

    __slots__ = (
        "_decisions",
        "_lock",
    )

    def __init__(self) -> None:
        """Initialize an empty decision repository."""

        self._decisions: dict[
            DecisionKey,
            RuleEngineDecisionDTO,
        ] = {}
        self._lock = RLock()

    def save(
        self,
        *,
        decision: RuleEngineDecisionDTO,
    ) -> None:
        """
        Save the latest decision for one workspace transaction.

        Existing data using the same workspace and transaction key is
        replaced, making repeated execution idempotent in this adapter.
        """

        validated_decision = _require_decision(decision)

        key = (
            validated_decision.workspace_id,
            validated_decision.transaction_id,
        )

        with self._lock:
            self._decisions[key] = validated_decision

    def get_by_transaction(
        self,
        *,
        workspace_id: UUID,
        transaction_id: UUID,
    ) -> RuleEngineDecisionDTO | None:
        """
        Return the latest decision for a workspace-owned transaction.

        Returns
        -------
        RuleEngineDecisionDTO | None
            Latest stored decision, or None when none exists.
        """

        validated_workspace_id = _require_uuid(
            workspace_id,
            field_name="workspace_id",
        )
        validated_transaction_id = _require_uuid(
            transaction_id,
            field_name="transaction_id",
        )

        key = (
            validated_workspace_id,
            validated_transaction_id,
        )

        with self._lock:
            return self._decisions.get(key)

    def list_by_workspace(
        self,
        *,
        workspace_id: UUID,
    ) -> tuple[RuleEngineDecisionDTO, ...]:
        """
        Return workspace decisions in stable transaction-ID order.
        """

        validated_workspace_id = _require_uuid(
            workspace_id,
            field_name="workspace_id",
        )

        with self._lock:
            decisions = (
                decision
                for (
                    decision_workspace_id,
                    _,
                ), decision in self._decisions.items()
                if decision_workspace_id == validated_workspace_id
            )

            return tuple(
                sorted(
                    decisions,
                    key=lambda decision: str(decision.transaction_id),
                )
            )

    def count_by_workspace(
        self,
        *,
        workspace_id: UUID,
    ) -> int:
        """Return the number of saved decisions for one workspace."""

        return len(
            self.list_by_workspace(
                workspace_id=workspace_id,
            )
        )

    @property
    def count(self) -> int:
        """Return the number of latest decisions across all workspaces."""

        with self._lock:
            return len(self._decisions)


def _require_uuid(
    value: object,
    *,
    field_name: str,
) -> UUID:
    """Validate and return one UUID value."""

    if not isinstance(value, UUID):
        raise InMemoryRepositoryError(f"{field_name} must be a UUID.")

    return value


def _require_transaction(
    transaction: object,
) -> NormalizedTransaction:
    """Validate and return one normalized transaction."""

    if not isinstance(transaction, NormalizedTransaction):
        raise InMemoryRepositoryError("transaction must be a NormalizedTransaction.")

    return transaction


def _require_rule(
    rule: object,
) -> RuleDefinition:
    """Validate and return one deterministic rule definition."""

    if not isinstance(rule, RuleDefinition):
        raise InMemoryRepositoryError("rule must be a RuleDefinition.")

    return rule


def _require_decision(
    decision: object,
) -> RuleEngineDecisionDTO:
    """Validate and return one application rule-engine decision."""

    if not isinstance(decision, RuleEngineDecisionDTO):
        raise InMemoryRepositoryError("decision must be a RuleEngineDecisionDTO.")

    return decision
