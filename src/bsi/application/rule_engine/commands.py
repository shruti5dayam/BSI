"""
Application commands for deterministic BSI rule-engine workflows.

A command is an immutable request to perform one application use case.

Commands contain identifiers and user intent. They do not:

- Load database records
- Evaluate rules
- Make accounting decisions
- Persist results
- Contain Streamlit or FastAPI logic

The application service receives a validated command and coordinates the
required ports and domain services.
"""

from dataclasses import dataclass
from uuid import UUID


class RuleEngineCommandError(ValueError):
    """Raised when a rule-engine application command is invalid."""


@dataclass(frozen=True, slots=True)
class EvaluateTransactionRulesCommand:
    """
    Request to evaluate all workspace rules for one transaction.

    Attributes
    ----------
    workspace_id:
        Tenant boundary owning the transaction and rule definitions.

    transaction_id:
        Normalized transaction that should be evaluated.

    Notes
    -----
    Only identifiers are carried in the command.

    The application service is responsible for loading the actual
    NormalizedTransaction and RuleDefinition objects through repository
    ports. This prevents UI and API layers from constructing or trusting
    authoritative domain objects directly.
    """

    workspace_id: UUID
    transaction_id: UUID

    def __post_init__(self) -> None:
        """Validate command identifiers."""

        if not isinstance(self.workspace_id, UUID):
            raise RuleEngineCommandError("workspace_id must be a UUID.")

        if not isinstance(self.transaction_id, UUID):
            raise RuleEngineCommandError("transaction_id must be a UUID.")
