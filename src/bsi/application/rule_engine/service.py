"""
Application service for deterministic BSI rule-engine execution.

This module coordinates one complete application use case:

1. Receive a validated command.
2. Load the workspace-owned transaction.
3. Load rules belonging to the workspace.
4. Execute the deterministic domain rule engine.
5. Convert the domain result into an application-facing DTO.
6. Persist the completed decision.
7. Return the decision to the caller.

The service does not contain:

- Rule-matching logic
- Rule-ranking logic
- Conflict-resolution logic
- SQLAlchemy queries
- Streamlit rendering
- FastAPI request handling
- AI or embedding behavior
"""

from dataclasses import dataclass

from bsi.application.rule_engine.commands import (
    EvaluateTransactionRulesCommand,
)
from bsi.application.rule_engine.dto import RuleEngineDecisionDTO
from bsi.application.rule_engine.ports import (
    RuleDecisionWriterPort,
    RuleReaderPort,
    TransactionReaderPort,
)
from bsi.domain.rules.engine import evaluate_transaction_rules
from bsi.domain.rules.models import RuleDefinition
from bsi.domain.transactions.models import NormalizedTransaction


class RuleEngineServiceError(ValueError):
    """Base error for rule-engine application-service failures."""


class TransactionNotFoundError(RuleEngineServiceError):
    """Raised when a transaction cannot be found in the workspace."""


class RuleEngineRepositoryContractError(RuleEngineServiceError):
    """Raised when a repository violates its application port contract."""


@dataclass(frozen=True, slots=True)
class RuleEngineApplicationService:
    """
    Coordinate deterministic rule evaluation for one transaction.

    Attributes
    ----------
    transaction_reader:
        Port used to load a normalized transaction within a workspace.

    rule_reader:
        Port used to load deterministic rules for the workspace.

    decision_writer:
        Port used to persist the completed application decision.
    """

    transaction_reader: TransactionReaderPort
    rule_reader: RuleReaderPort
    decision_writer: RuleDecisionWriterPort

    def __post_init__(self) -> None:
        """Validate that dependencies satisfy their required ports."""

        if not isinstance(
            self.transaction_reader,
            TransactionReaderPort,
        ):
            raise RuleEngineServiceError(
                "transaction_reader must implement TransactionReaderPort."
            )

        if not isinstance(
            self.rule_reader,
            RuleReaderPort,
        ):
            raise RuleEngineServiceError("rule_reader must implement RuleReaderPort.")

        if not isinstance(
            self.decision_writer,
            RuleDecisionWriterPort,
        ):
            raise RuleEngineServiceError(
                "decision_writer must implement RuleDecisionWriterPort."
            )

    def execute(
        self,
        command: EvaluateTransactionRulesCommand,
    ) -> RuleEngineDecisionDTO:
        """
        Execute the rule-engine use case for one transaction.

        Parameters
        ----------
        command:
            Validated workspace and transaction identifiers.

        Returns
        -------
        RuleEngineDecisionDTO
            Completed deterministic mapping decision and audit evidence.

        Raises
        ------
        RuleEngineServiceError
            If the command or injected dependencies are invalid.

        TransactionNotFoundError
            If the transaction is not found in the requested workspace.

        RuleEngineRepositoryContractError
            If an adapter returns data that violates its port contract.

        Notes
        -----
        Exceptions raised by the persistence adapter are intentionally
        allowed to propagate. Infrastructure layers should preserve their
        original failure details rather than being silently converted into
        a successful application result.
        """

        if not isinstance(
            command,
            EvaluateTransactionRulesCommand,
        ):
            raise RuleEngineServiceError(
                "command must be an EvaluateTransactionRulesCommand."
            )

        transaction = self.transaction_reader.get_by_id(
            workspace_id=command.workspace_id,
            transaction_id=command.transaction_id,
        )

        if transaction is None:
            raise TransactionNotFoundError(
                "Transaction was not found in the supplied workspace."
            )

        if not isinstance(transaction, NormalizedTransaction):
            raise RuleEngineRepositoryContractError(
                "TransactionReaderPort must return a NormalizedTransaction or None."
            )

        rules = self.rule_reader.list_by_workspace(
            workspace_id=command.workspace_id,
        )

        _validate_loaded_rules(rules)

        domain_result = evaluate_transaction_rules(
            workspace_id=command.workspace_id,
            transaction=transaction,
            rules=rules,
        )

        decision = RuleEngineDecisionDTO.from_domain(domain_result)

        self.decision_writer.save(
            decision=decision,
        )

        return decision


def _validate_loaded_rules(
    rules: object,
) -> None:
    """
    Validate rules returned by the repository adapter.

    Parameters
    ----------
    rules:
        Runtime value returned through RuleReaderPort.

    Raises
    ------
    RuleEngineRepositoryContractError
        If the value is not an immutable tuple containing only
        RuleDefinition objects.
    """

    if not isinstance(rules, tuple):
        raise RuleEngineRepositoryContractError(
            "RuleReaderPort must return a tuple of RuleDefinition objects."
        )

    if not all(isinstance(rule, RuleDefinition) for rule in rules):
        raise RuleEngineRepositoryContractError(
            "RuleReaderPort must return only RuleDefinition objects."
        )
