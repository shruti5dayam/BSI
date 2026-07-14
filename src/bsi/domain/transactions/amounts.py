"""
Financial amount handling for the BSI transaction domain.

This module provides:

- Safe conversion of raw bank-statement amounts to Decimal.
- Explicit two-decimal financial rounding.
- Validation of payment and deposit values.
- Transaction-direction resolution.
- Signed-amount calculation.

The module is framework independent. It must not depend on Pandas,
FastAPI, Streamlit, SQLAlchemy, or PostgreSQL.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Self

from bsi.domain.transactions.enums import TransactionDirection

type AmountInput = Decimal | int | float | str | None


MONEY_QUANTUM = Decimal("0.01")
ZERO_MONEY = Decimal("0.00")

_CURRENCY_SYMBOLS = "$£€₹"


class AmountValidationError(ValueError):
    """Raised when a financial amount cannot enter the BSI domain."""


def parse_money(
    value: AmountInput,
    *,
    field_name: str = "amount",
) -> Decimal:
    """
    Convert a raw monetary value into a two-decimal Decimal.

    Blank strings and None are treated as zero because bank statements
    commonly leave the unused payment or deposit column empty.

    Parameters
    ----------
    value:
        Raw monetary value from an ingestion adapter.

        Supported values are:

        - Decimal
        - int
        - float
        - str
        - None

    field_name:
        Human-readable field name used in validation errors.

    Returns
    -------
    Decimal
        Valid finite monetary value rounded to two decimal places.

    Raises
    ------
    AmountValidationError
        If the value is boolean, unsupported, invalid, NaN, or infinite.

    Examples
    --------
    >>> parse_money("1,250.50")
    Decimal('1250.50')

    >>> parse_money("$25.126")
    Decimal('25.13')

    >>> parse_money(None)
    Decimal('0.00')
    """

    if isinstance(value, bool):
        raise AmountValidationError(f"{field_name} cannot be a boolean value.")

    decimal_value: Decimal

    if value is None:
        decimal_value = ZERO_MONEY

    elif isinstance(value, Decimal):
        decimal_value = value

    elif isinstance(value, int):
        decimal_value = Decimal(value)

    elif isinstance(value, float):
        decimal_value = _parse_float(
            value,
            field_name=field_name,
        )

    elif isinstance(value, str):
        decimal_value = _parse_money_string(
            value,
            field_name=field_name,
        )

    else:
        raise AmountValidationError(
            f"{field_name} has an unsupported value type: {type(value).__name__}."
        )

    if not decimal_value.is_finite():
        raise AmountValidationError(f"{field_name} must be a finite monetary value.")

    return decimal_value.quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _parse_float(
    value: float,
    *,
    field_name: str,
) -> Decimal:
    """
    Convert a finite float through its string representation.

    Float support exists only for ingestion compatibility because Pandas
    and spreadsheet readers may produce float values. BSI does not use
    float for authoritative financial calculations.
    """

    try:
        decimal_value = Decimal(str(value))
    except InvalidOperation as error:
        raise AmountValidationError(
            f"{field_name} contains an invalid floating-point value."
        ) from error

    return decimal_value


def _parse_money_string(
    value: str,
    *,
    field_name: str,
) -> Decimal:
    """
    Parse a formatted monetary string.

    Supported formatting includes:

    - Thousands separators: 1,250.00
    - Currency symbols: $100.00
    - Negative signs: -100.00
    - Accounting parentheses: (100.00)
    - Blank strings, which represent zero
    """

    normalized_value = value.strip()

    if not normalized_value:
        return ZERO_MONEY

    is_parenthesized_negative = normalized_value.startswith(
        "("
    ) and normalized_value.endswith(")")

    if is_parenthesized_negative:
        normalized_value = normalized_value[1:-1].strip()

    normalized_value = normalized_value.replace(",", "")

    for currency_symbol in _CURRENCY_SYMBOLS:
        normalized_value = normalized_value.replace(
            currency_symbol,
            "",
        )

    normalized_value = normalized_value.strip()

    if not normalized_value:
        raise AmountValidationError(f"{field_name} does not contain a monetary value.")

    if is_parenthesized_negative:
        normalized_value = f"-{normalized_value}"

    try:
        return Decimal(normalized_value)
    except InvalidOperation as error:
        raise AmountValidationError(
            f"{field_name} contains an invalid monetary value."
        ) from error


@dataclass(frozen=True, slots=True)
class TransactionAmounts:
    """
    Validated payment and deposit values for one bank transaction.

    A valid transaction must contain exactly one positive cash amount:

    - Payment greater than zero and deposit equal to zero, or
    - Deposit greater than zero and payment equal to zero.

    Attributes
    ----------
    payment:
        Positive amount leaving the bank account, or zero.

    deposit:
        Positive amount entering the bank account, or zero.
    """

    payment: Decimal
    deposit: Decimal

    def __post_init__(self) -> None:
        """Validate and normalize amounts after object construction."""

        if not isinstance(self.payment, Decimal):
            raise TypeError(
                "payment must be a Decimal. Use "
                "TransactionAmounts.from_raw() for raw values."
            )

        if not isinstance(self.deposit, Decimal):
            raise TypeError(
                "deposit must be a Decimal. Use "
                "TransactionAmounts.from_raw() for raw values."
            )

        normalized_payment = parse_money(
            self.payment,
            field_name="payment",
        )
        normalized_deposit = parse_money(
            self.deposit,
            field_name="deposit",
        )

        if normalized_payment < ZERO_MONEY:
            raise AmountValidationError("payment cannot be negative.")

        if normalized_deposit < ZERO_MONEY:
            raise AmountValidationError("deposit cannot be negative.")

        has_payment = normalized_payment > ZERO_MONEY
        has_deposit = normalized_deposit > ZERO_MONEY

        if has_payment and has_deposit:
            raise AmountValidationError(
                "A transaction cannot contain both a payment and a deposit."
            )

        if not has_payment and not has_deposit:
            raise AmountValidationError(
                "A transaction must contain either a payment "
                "or a deposit greater than zero."
            )

        object.__setattr__(
            self,
            "payment",
            normalized_payment,
        )
        object.__setattr__(
            self,
            "deposit",
            normalized_deposit,
        )

    @classmethod
    def from_raw(
        cls,
        *,
        payment: AmountInput = None,
        deposit: AmountInput = None,
    ) -> Self:
        """
        Create validated transaction amounts from raw source values.

        Parameters
        ----------
        payment:
            Raw payment-column value.

        deposit:
            Raw deposit-column value.

        Returns
        -------
        TransactionAmounts
            Immutable validated transaction amount object.
        """

        return cls(
            payment=parse_money(
                payment,
                field_name="payment",
            ),
            deposit=parse_money(
                deposit,
                field_name="deposit",
            ),
        )

    @property
    def direction(self) -> TransactionDirection:
        """
        Return the transaction's bank cash direction.

        Returns
        -------
        TransactionDirection
            PAYMENT when payment is positive; otherwise DEPOSIT.
        """

        if self.payment > ZERO_MONEY:
            return TransactionDirection.PAYMENT

        return TransactionDirection.DEPOSIT

    @property
    def absolute_amount(self) -> Decimal:
        """
        Return the positive transaction amount.

        Returns
        -------
        Decimal
            Payment amount or deposit amount, whichever is positive.
        """

        if self.direction is TransactionDirection.PAYMENT:
            return self.payment

        return self.deposit

    @property
    def signed_amount(self) -> Decimal:
        """
        Return the amount using the bank-account cash sign convention.

        Payments are negative because cash leaves the bank account.
        Deposits are positive because cash enters the bank account.

        Returns
        -------
        Decimal
            Negative payment or positive deposit.
        """

        return self.absolute_amount * self.direction.signed_multiplier
