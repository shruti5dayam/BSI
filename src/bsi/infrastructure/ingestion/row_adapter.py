"""
Bank-statement row adapter for BSI.

This module converts one external Excel or CSV record into a validated
NormalizedTransaction domain object.

Responsibilities:

- Define expected bank-statement column names.
- Check that required columns exist.
- Handle blank and Pandas-style missing values.
- Parse supported bank-statement dates.
- Convert spreadsheet numeric types into supported amount inputs.
- Preserve source-file and source-row lineage.
- Translate domain validation failures into row-level ingestion errors.

This module may depend on Pandas because it belongs to infrastructure.
Financial rules and calculations remain inside the domain layer.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from numbers import Integral, Real
from typing import cast
from uuid import UUID

import pandas as pd

from bsi.domain.transactions import (
    AmountInput,
    AmountValidationError,
    NormalizedTransaction,
    TransactionContext,
    TransactionSource,
    TransactionValidationError,
)

type BankStatementRecord = Mapping[str, object]
type DateInput = date | datetime | str


class RowAdaptationError(ValueError):
    """Raised when an external bank-statement row cannot be normalized."""


@dataclass(frozen=True, slots=True)
class BankStatementColumnMap:
    """
    Column names expected in an external bank statement.

    Different banks may use different headers. An adapter instance can
    therefore receive a custom column map without changing the domain.

    Attributes
    ----------
    transaction_date:
        Column containing the bank transaction date.

    description:
        Column containing the main transaction description or payee.

    memo:
        Optional column containing supporting memo information.

    payment:
        Column containing money leaving the bank account.

    deposit:
        Column containing money entering the bank account.
    """

    transaction_date: str = "Date"
    description: str = "Payee"
    memo: str | None = "Memo"
    payment: str = "Payment"
    deposit: str = "Deposit"

    def __post_init__(self) -> None:
        """Validate configured bank-statement column names."""

        required_column_names = {
            "transaction_date": self.transaction_date,
            "description": self.description,
            "payment": self.payment,
            "deposit": self.deposit,
        }

        normalized_required_names: list[str] = []

        for field_name, column_name in required_column_names.items():
            normalized_name = _validate_column_name(
                column_name,
                field_name=field_name,
            )
            normalized_required_names.append(normalized_name)
            object.__setattr__(
                self,
                field_name,
                normalized_name,
            )

        if len(set(normalized_required_names)) != len(normalized_required_names):
            raise RowAdaptationError(
                "Required bank-statement columns must use unique names."
            )

        if self.memo is not None:
            normalized_memo_name = _validate_column_name(
                self.memo,
                field_name="memo",
            )

            if normalized_memo_name in normalized_required_names:
                raise RowAdaptationError(
                    "The memo column must not duplicate a required column."
                )

            object.__setattr__(
                self,
                "memo",
                normalized_memo_name,
            )


@dataclass(frozen=True, slots=True)
class BankStatementRowAdapter:
    """
    Convert external bank-statement records into domain transactions.

    Attributes
    ----------
    columns:
        Mapping between BSI fields and external bank-statement headers.
    """

    columns: BankStatementColumnMap = field(default_factory=BankStatementColumnMap)

    def adapt(
        self,
        row: BankStatementRecord,
        *,
        file_name: str,
        source_row_number: int,
        sheet_name: str | None = None,
        context: TransactionContext | None = None,
        source_document_id: UUID | None = None,
        processing_run_id: UUID | None = None,
        transaction_id: UUID | None = None,
        vendor_name: str | None = None,
        normalized_description: str | None = None,
    ) -> NormalizedTransaction:
        """
        Convert one bank-statement record into a normalized transaction.

        Parameters
        ----------
        row:
            Mapping containing one external bank-statement record.

            A dictionary produced from:

                dataframe.to_dict(orient="records")

            is a suitable input.

        file_name:
            Original uploaded filename.

        source_row_number:
            One-based row number from the original source file.

        sheet_name:
            Excel worksheet name when applicable.

        context:
            Previously resolved company, brand, store, and bank-account
            context.

        source_document_id:
            Persistent identifier for the uploaded source document.

        processing_run_id:
            Identifier for the processing run.

        transaction_id:
            Optional predefined transaction UUID.

        vendor_name:
            Optional normalized or reviewed vendor name.

        normalized_description:
            Optional description previously normalized by an approved
            ingestion process.

        Returns
        -------
        NormalizedTransaction
            Immutable, validated transaction-domain object.

        Raises
        ------
        RowAdaptationError
            If columns are missing or the row contains invalid values.
        """

        if not isinstance(row, Mapping):
            raise RowAdaptationError(
                "Bank-statement row must be a mapping of columns to values."
            )

        try:
            raw_date = _read_required_column(
                row,
                self.columns.transaction_date,
            )
            raw_description = _read_required_column(
                row,
                self.columns.description,
            )
            raw_payment = _read_required_column(
                row,
                self.columns.payment,
            )
            raw_deposit = _read_required_column(
                row,
                self.columns.deposit,
            )

            raw_memo = _read_optional_column(
                row,
                self.columns.memo,
            )

            transaction_date = parse_transaction_date(
                raw_date,
                field_name=self.columns.transaction_date,
            )

            original_description = _as_required_text(
                raw_description,
                field_name=self.columns.description,
            )

            original_memo = _as_optional_text(
                raw_memo,
                field_name=self.columns.memo or "memo",
            )

            payment = _as_amount_input(
                raw_payment,
                field_name=self.columns.payment,
            )

            deposit = _as_amount_input(
                raw_deposit,
                field_name=self.columns.deposit,
            )

            source = TransactionSource(
                file_name=file_name,
                source_row_number=source_row_number,
                sheet_name=sheet_name,
                source_document_id=source_document_id,
                processing_run_id=processing_run_id,
            )

            return NormalizedTransaction.create(
                transaction_id=transaction_id,
                transaction_date=transaction_date,
                original_description=original_description,
                original_memo=original_memo,
                normalized_description=normalized_description,
                vendor_name=vendor_name,
                payment=payment,
                deposit=deposit,
                source=source,
                context=context,
            )

        except (
            AmountValidationError,
            RowAdaptationError,
            TransactionValidationError,
        ) as error:
            raise RowAdaptationError(
                f"Could not normalize {file_name} row {source_row_number}: {error}"
            ) from error


def parse_transaction_date(
    value: object,
    *,
    field_name: str = "transaction_date",
) -> date:
    """
    Convert an external date value into a Python date.

    Supported inputs:

    - datetime.datetime
    - datetime.date
    - ISO date strings such as 2026-07-14
    - ISO datetime strings such as 2026-07-14 00:00:00
    - US date strings such as 07/14/2026
    - Short US date strings such as 07/14/26
    - Year-first slash dates such as 2026/07/14

    Day-first formats are intentionally not guessed because values such as
    07/08/2026 are ambiguous.

    Parameters
    ----------
    value:
        Raw external date value.

    field_name:
        Source column name used in validation errors.

    Returns
    -------
    date
        Date without a time component.

    Raises
    ------
    RowAdaptationError
        If the value is missing or uses an unsupported format.
    """

    if _is_missing_value(value):
        raise RowAdaptationError(f"{field_name} cannot be empty.")

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if not isinstance(value, str):
        raise RowAdaptationError(f"{field_name} must contain a date or date string.")

    normalized_value = value.strip()

    try:
        return datetime.fromisoformat(normalized_value).date()
    except ValueError:
        pass

    supported_formats = (
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y/%m/%d",
    )

    for date_format in supported_formats:
        try:
            return datetime.strptime(
                normalized_value,
                date_format,
            ).date()
        except ValueError:
            continue

    raise RowAdaptationError(
        f"{field_name} contains an unsupported date value: {normalized_value!r}."
    )


def _validate_column_name(
    value: str,
    *,
    field_name: str,
) -> str:
    """Validate and normalize one configured source-column name."""

    if not isinstance(value, str):
        raise RowAdaptationError(f"{field_name} column name must be a string.")

    normalized_value = value.strip()

    if not normalized_value:
        raise RowAdaptationError(f"{field_name} column name cannot be empty.")

    return normalized_value


def _read_required_column(
    row: BankStatementRecord,
    column_name: str,
) -> object:
    """Return one required source value or raise a clear error."""

    if column_name not in row:
        raise RowAdaptationError(f"Required column is missing: {column_name!r}.")

    return row[column_name]


def _read_optional_column(
    row: BankStatementRecord,
    column_name: str | None,
) -> object:
    """Return an optional source value when its column is configured."""

    if column_name is None:
        return None

    return row.get(column_name)


def _is_missing_value(value: object) -> bool:
    """
    Detect blank external scalar values.

    Pandas and spreadsheet readers may represent missing cells as:

    - None
    - NaN
    - NaT
    - pandas.NA
    - Blank strings
    """

    if value is None:
        return True

    if isinstance(value, str):
        return not value.strip()

    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _as_required_text(
    value: object,
    *,
    field_name: str,
) -> str:
    """Convert one required external value into text."""

    if _is_missing_value(value):
        raise RowAdaptationError(f"{field_name} cannot be empty.")

    if not isinstance(value, str):
        raise RowAdaptationError(f"{field_name} must contain text.")

    return value


def _as_optional_text(
    value: object,
    *,
    field_name: str,
) -> str | None:
    """Convert one optional external value into text or None."""

    if _is_missing_value(value):
        return None

    if not isinstance(value, str):
        raise RowAdaptationError(f"{field_name} must contain text or be empty.")

    return value


def _as_amount_input(
    value: object,
    *,
    field_name: str,
) -> AmountInput:
    """
    Convert external spreadsheet scalar types into domain amount inputs.

    NumPy integer and floating-point values implement the Integral or Real
    interfaces but may not be built-in Python int or float instances.
    They are converted before entering the domain.
    """

    if _is_missing_value(value):
        return None

    if isinstance(value, (Decimal, str)):
        return value

    if isinstance(value, bool):
        return value

    if isinstance(value, Integral):
        return int(value)

    if isinstance(value, Real):
        return float(value)

    supported_value = cast(
        AmountInput,
        value,
    )

    if isinstance(
        supported_value,
        (Decimal, int, float, str),
    ):
        return supported_value

    raise RowAdaptationError(
        f"{field_name} contains an unsupported amount type: {type(value).__name__}."
    )
