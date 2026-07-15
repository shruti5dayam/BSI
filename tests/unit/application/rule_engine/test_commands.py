"""
Unit tests for BSI rule-engine application commands.

These tests verify:

- Valid workspace and transaction identifiers
- Rejection of invalid identifiers
- Boolean and string protection
- Command immutability
- Slot-based attribute storage
"""

from dataclasses import FrozenInstanceError
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from bsi.application.rule_engine.commands import (
    EvaluateTransactionRulesCommand,
    RuleEngineCommandError,
)


def _invalid(value: object) -> Any:
    """
    Bypass static typing for intentional runtime-validation tests.

    Production callers should provide UUID values. These tests verify
    protection when invalid runtime values reach the command boundary.
    """

    return cast(Any, value)


def test_command_preserves_valid_identifiers() -> None:
    """A valid command should preserve its workspace and transaction IDs."""

    workspace_id = uuid4()
    transaction_id = uuid4()

    command = EvaluateTransactionRulesCommand(
        workspace_id=workspace_id,
        transaction_id=transaction_id,
    )

    assert command.workspace_id == workspace_id
    assert command.transaction_id == transaction_id


def test_command_identifiers_are_uuid_objects() -> None:
    """Both command identifiers remain strongly typed UUID values."""

    command = EvaluateTransactionRulesCommand(
        workspace_id=uuid4(),
        transaction_id=uuid4(),
    )

    assert isinstance(command.workspace_id, UUID)
    assert isinstance(command.transaction_id, UUID)


@pytest.mark.parametrize(
    "invalid_workspace_id",
    [
        "workspace-123",
        123,
        None,
        True,
    ],
)
def test_command_rejects_invalid_workspace_id(
    invalid_workspace_id: object,
) -> None:
    """Workspace identity must use a UUID tenant boundary."""

    with pytest.raises(
        RuleEngineCommandError,
        match="workspace_id must be a UUID",
    ):
        EvaluateTransactionRulesCommand(
            workspace_id=_invalid(invalid_workspace_id),
            transaction_id=uuid4(),
        )


@pytest.mark.parametrize(
    "invalid_transaction_id",
    [
        "transaction-123",
        123,
        None,
        False,
    ],
)
def test_command_rejects_invalid_transaction_id(
    invalid_transaction_id: object,
) -> None:
    """Transaction identity must use a UUID."""

    with pytest.raises(
        RuleEngineCommandError,
        match="transaction_id must be a UUID",
    ):
        EvaluateTransactionRulesCommand(
            workspace_id=uuid4(),
            transaction_id=_invalid(invalid_transaction_id),
        )


def test_workspace_validation_occurs_before_transaction_validation() -> None:
    """Validation should fail first at the invalid workspace boundary."""

    with pytest.raises(
        RuleEngineCommandError,
        match="workspace_id must be a UUID",
    ):
        EvaluateTransactionRulesCommand(
            workspace_id=_invalid("invalid-workspace"),
            transaction_id=_invalid("invalid-transaction"),
        )


def test_command_is_immutable() -> None:
    """Validated command identifiers cannot change after construction."""

    command = EvaluateTransactionRulesCommand(
        workspace_id=uuid4(),
        transaction_id=uuid4(),
    )
    command_for_mutation = cast(Any, command)

    with pytest.raises(FrozenInstanceError):
        command_for_mutation.transaction_id = uuid4()


def test_command_uses_slots() -> None:
    """Slot-based commands do not expose a mutable instance dictionary."""

    command = EvaluateTransactionRulesCommand(
        workspace_id=uuid4(),
        transaction_id=uuid4(),
    )

    assert not hasattr(command, "__dict__")
