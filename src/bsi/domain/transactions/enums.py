"""
Enumerations for the BSI transaction domain.

This module contains framework-independent values used to describe
normalized financial transactions.

It must not depend on:

- Pandas
- FastAPI
- Streamlit
- SQLAlchemy
- PostgreSQL
"""

from enum import StrEnum


class TransactionDirection(StrEnum):
    """
    Direction of cash movement for a valid bank transaction.

    A payment represents money leaving the bank account.

    A deposit represents money entering the bank account.

    Invalid or unresolved source rows are not represented as additional
    direction values. They will be handled separately through validation
    errors and exception records.
    """

    PAYMENT = "payment"
    DEPOSIT = "deposit"

    @property
    def signed_multiplier(self) -> int:
        """
        Return the multiplier used to calculate a signed amount.

        Returns
        -------
        int
            -1 for payments because cash leaves the bank account.
             1 for deposits because cash enters the bank account.
        """

        if self is TransactionDirection.PAYMENT:
            return -1

        return 1

    @property
    def is_cash_inflow(self) -> bool:
        """
        Return whether the direction represents money entering the account.

        Returns
        -------
        bool
            True for deposits and False for payments.
        """

        return self is TransactionDirection.DEPOSIT

    @property
    def is_cash_outflow(self) -> bool:
        """
        Return whether the direction represents money leaving the account.

        Returns
        -------
        bool
            True for payments and False for deposits.
        """

        return self is TransactionDirection.PAYMENT
