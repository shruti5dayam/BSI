"""
Unit tests for BSI transaction amount handling.

These tests verify:

- Conversion of raw values into Decimal
- Financial rounding
- Currency and accounting-number formatting
- Rejection of invalid and non-finite values
- Payment and deposit validation
- Transaction direction
- Absolute and signed amounts
- Immutability of validated financial values
"""

from dataclasses import FrozenInstanceError
from decimal import Decimal
from typing import cast

import pytest

from bsi.domain.transactions.amounts import (
    AmountInput,
    AmountValidationError,
    TransactionAmounts,
    parse_money,
)
from bsi.domain.transactions.enums import TransactionDirection


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_value", "expected_value"),
    [
        (None, Decimal("0.00")),
        ("", Decimal("0.00")),
        ("   ", Decimal("0.00")),
        (0, Decimal("0.00")),
        (100, Decimal("100.00")),
        (Decimal("25.50"), Decimal("25.50")),
        (10.5, Decimal("10.50")),
        ("1250.50", Decimal("1250.50")),
        ("1,250.50", Decimal("1250.50")),
        ("$1,250.50", Decimal("1250.50")),
        ("£100.00", Decimal("100.00")),
        ("€100.00", Decimal("100.00")),
        ("₹100.00", Decimal("100.00")),
        ("-100.00", Decimal("-100.00")),
        ("(100.00)", Decimal("-100.00")),
    ],
)
def test_parse_money_converts_supported_values(
    raw_value: AmountInput,
    expected_value: Decimal,
) -> None:
    """Supported source values should become two-decimal Decimals."""

    assert parse_money(raw_value) == expected_value


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_value", "expected_value"),
    [
        ("10.124", Decimal("10.12")),
        ("10.125", Decimal("10.13")),
        ("10.126", Decimal("10.13")),
        ("-10.125", Decimal("-10.13")),
        (1.005, Decimal("1.01")),
    ],
)
def test_parse_money_uses_half_up_financial_rounding(
    raw_value: AmountInput,
    expected_value: Decimal,
) -> None:
    """Amounts should use explicit ROUND_HALF_UP behavior."""

    assert parse_money(raw_value) == expected_value


@pytest.mark.unit
def test_parse_money_uses_field_name_in_validation_error() -> None:
    """Error messages should identify the source field."""

    with pytest.raises(
        AmountValidationError,
        match="deposit contains an invalid monetary value",
    ):
        parse_money(
            "not-money",
            field_name="deposit",
        )


@pytest.mark.unit
def test_boolean_amount_is_rejected() -> None:
    """Boolean values must not be interpreted as integers."""

    with pytest.raises(
        AmountValidationError,
        match="amount cannot be a boolean value",
    ):
        parse_money(True)


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_value",
    [
        "abc",
        "$",
        "1,2,3x",
    ],
)
def test_invalid_money_string_is_rejected(
    invalid_value: str,
) -> None:
    """Invalid monetary text must not enter the domain."""

    with pytest.raises(AmountValidationError):
        parse_money(invalid_value)


@pytest.mark.unit
@pytest.mark.parametrize(
    "non_finite_value",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        float("nan"),
        float("inf"),
        float("-inf"),
        "NaN",
        "Infinity",
        "-Infinity",
    ],
)
def test_non_finite_money_is_rejected(
    non_finite_value: AmountInput,
) -> None:
    """NaN and infinite values are invalid financial amounts."""

    with pytest.raises(
        AmountValidationError,
        match="must be a finite monetary value",
    ):
        parse_money(non_finite_value)


@pytest.mark.unit
def test_unsupported_amount_type_is_rejected() -> None:
    """Unsupported source types should produce a clear validation error."""

    unsupported_value = cast(AmountInput, ["100.00"])

    with pytest.raises(
        AmountValidationError,
        match="unsupported value type",
    ):
        parse_money(unsupported_value)


@pytest.mark.unit
def test_payment_transaction_is_created_from_raw_values() -> None:
    """A positive payment and blank deposit should form a payment."""

    amounts = TransactionAmounts.from_raw(
        payment="177.70",
        deposit=None,
    )

    assert amounts.payment == Decimal("177.70")
    assert amounts.deposit == Decimal("0.00")
    assert amounts.direction is TransactionDirection.PAYMENT
    assert amounts.absolute_amount == Decimal("177.70")
    assert amounts.signed_amount == Decimal("-177.70")


@pytest.mark.unit
def test_deposit_transaction_is_created_from_raw_values() -> None:
    """A blank payment and positive deposit should form a deposit."""

    amounts = TransactionAmounts.from_raw(
        payment="",
        deposit="500.00",
    )

    assert amounts.payment == Decimal("0.00")
    assert amounts.deposit == Decimal("500.00")
    assert amounts.direction is TransactionDirection.DEPOSIT
    assert amounts.absolute_amount == Decimal("500.00")
    assert amounts.signed_amount == Decimal("500.00")


@pytest.mark.unit
def test_direct_decimal_construction_normalizes_values() -> None:
    """Direct Decimal values should be normalized to two decimal places."""

    amounts = TransactionAmounts(
        payment=Decimal("10.125"),
        deposit=Decimal("0"),
    )

    assert amounts.payment == Decimal("10.13")
    assert amounts.deposit == Decimal("0.00")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payment", "deposit", "expected_message"),
    [
        (
            "-10.00",
            None,
            "payment cannot be negative",
        ),
        (
            None,
            "-10.00",
            "deposit cannot be negative",
        ),
        (
            "100.00",
            "50.00",
            "cannot contain both a payment and a deposit",
        ),
        (
            None,
            None,
            "must contain either a payment or a deposit",
        ),
        (
            "0.00",
            "0.00",
            "must contain either a payment or a deposit",
        ),
    ],
)
def test_invalid_transaction_amount_combinations_are_rejected(
    payment: AmountInput,
    deposit: AmountInput,
    expected_message: str,
) -> None:
    """Invalid payment/deposit combinations must not enter the domain."""

    with pytest.raises(
        AmountValidationError,
        match=expected_message,
    ):
        TransactionAmounts.from_raw(
            payment=payment,
            deposit=deposit,
        )


@pytest.mark.unit
def test_direct_constructor_requires_decimal_payment() -> None:
    """Raw strings must enter through from_raw rather than the constructor."""

    invalid_payment = cast(Decimal, "100.00")

    with pytest.raises(
        TypeError,
        match="payment must be a Decimal",
    ):
        TransactionAmounts(
            payment=invalid_payment,
            deposit=Decimal("0.00"),
        )


@pytest.mark.unit
def test_direct_constructor_requires_decimal_deposit() -> None:
    """Raw strings must enter through from_raw rather than the constructor."""

    invalid_deposit = cast(Decimal, "100.00")

    with pytest.raises(
        TypeError,
        match="deposit must be a Decimal",
    ):
        TransactionAmounts(
            payment=Decimal("0.00"),
            deposit=invalid_deposit,
        )


@pytest.mark.unit
def test_transaction_amounts_are_immutable() -> None:
    """Validated financial amounts must not change after construction."""

    amounts = TransactionAmounts.from_raw(
        payment="100.00",
        deposit=None,
    )

    attribute_name = "payment"

    with pytest.raises(FrozenInstanceError):
        setattr(
            amounts,
            attribute_name,
            Decimal("200.00"),
        )
