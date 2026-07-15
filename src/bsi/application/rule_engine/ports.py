"""
Application ports for deterministic BSI rule-engine orchestration.

A port is an interface describing what the application layer needs from
external infrastructure.

Concrete adapters may later implement these ports using:

- PostgreSQL and SQLAlchemy
- In-memory repositories for tests
- Excel or CSV sources during migration
- REST APIs
- Background-processing infrastructure

The application service depends on these interfaces instead of depending
directly on databases, files, Streamlit, or FastAPI.
"""

from typing import Protocol, runtime_checkable
from uuid import UUID

from bsi.application.rule_engine.dto import RuleEngineDecisionDTO
from bsi.domain.rules.models import RuleDefinition
from bsi.domain.transactions.models import NormalizedTransaction


@runtime_checkable
class TransactionReaderPort(Protocol):
    """
    Read normalized transactions within a workspace boundary.

    Implementations must enforce tenant isolation using `workspace_id`.
    Returning `None` means the requested transaction was not found in
    that workspace.
    """

    def get_by_id(
        self,
        *,
        workspace_id: UUID,
        transaction_id: UUID,
    ) -> NormalizedTransaction | None:
        """
        Load one normalized transaction.

        Parameters
        ----------
        workspace_id:
            Workspace that must own the transaction.

        transaction_id:
            Identifier of the normalized transaction.

        Returns
        -------
        NormalizedTransaction | None
            The transaction when found, otherwise None.
        """

        ...


@runtime_checkable
class RuleReaderPort(Protocol):
    """
    Read deterministic rules belonging to one workspace.

    The adapter loads rule definitions, but it must not decide which rule
    wins. Lifecycle, scope, date, condition, ranking, and conflict logic
    remain responsibilities of the domain engine.
    """

    def list_by_workspace(
        self,
        *,
        workspace_id: UUID,
    ) -> tuple[RuleDefinition, ...]:
        """
        Load rules available to one workspace.

        Parameters
        ----------
        workspace_id:
            Workspace whose rules should be loaded.

        Returns
        -------
        tuple[RuleDefinition, ...]
            Immutable collection of workspace-owned rules.

            Returning an empty tuple means no rules are configured.
        """

        ...


@runtime_checkable
class RuleDecisionWriterPort(Protocol):
    """
    Persist completed application-facing rule-engine decisions.

    Implementations should preserve the decision summary and nested audit
    evidence. Saving the same workspace and transaction decision more
    than once should be idempotent or create explicit decision versions,
    depending on the persistence strategy.
    """

    def save(
        self,
        *,
        decision: RuleEngineDecisionDTO,
    ) -> None:
        """
        Persist one completed rule-engine decision.

        Parameters
        ----------
        decision:
            Application-facing deterministic mapping decision.

        Returns
        -------
        None
            Persistence success is represented by normal completion.
            Infrastructure failures should raise an adapter-specific
            exception.
        """

        ...
