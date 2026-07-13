"""
Unit tests for transaction-domain enumerations.

These tests verify:

- Payment and deposit string values
- Signed-amount multipliers
- Cash inflow and cash outflow flags
- Conversion from valid source strings
- Rejection of unsupported direction values
"""

import pytest

from bsi.domain.transactions.enums import TransactionDirection


@pytest.mark.unit
@pytest.mark.parametrize(
    ("direction", "expected_multiplier"),
    [
        (TransactionDirection.PAYMENT, -1),
        (TransactionDirection.DEPOSIT, 1),
    ],
)
def test_direction_has_correct_signed_multiplier(
    direction: TransactionDirection,
    expected_multiplier: int,
) -> None:
    """Each cash direction should have the correct financial sign."""

    assert direction.signed_multiplier == expected_multiplier


@pytest.mark.unit
def test_payment_is_cash_outflow() -> None:
    """A payment represents money leaving the bank account."""

    direction = TransactionDirection.PAYMENT

    assert direction.is_cash_outflow is True
    assert direction.is_cash_inflow is False


@pytest.mark.unit
def test_deposit_is_cash_inflow() -> None:
    """A deposit represents money entering the bank account."""

    direction = TransactionDirection.DEPOSIT

    assert direction.is_cash_inflow is True
    assert direction.is_cash_outflow is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_value", "expected_direction"),
    [
        ("payment", TransactionDirection.PAYMENT),
        ("deposit", TransactionDirection.DEPOSIT),
    ],
)
def test_direction_can_be_created_from_valid_string(
    raw_value: str,
    expected_direction: TransactionDirection,
) -> None:
    """Valid normalized strings should create domain enum values."""

    direction = TransactionDirection(raw_value)

    assert direction is expected_direction


@pytest.mark.unit
def test_invalid_direction_string_is_rejected() -> None:
    """Unsupported financial directions must not enter the domain."""

    with pytest.raises(
        ValueError,
        match="'transfer' is not a valid TransactionDirection",
    ):
        TransactionDirection("transfer")


@pytest.mark.unit
def test_direction_serializes_as_lowercase_string() -> None:
    """Direction values should serialize consistently for APIs and storage."""

    assert str(TransactionDirection.PAYMENT) == "payment"
    assert str(TransactionDirection.DEPOSIT) == "deposit"
