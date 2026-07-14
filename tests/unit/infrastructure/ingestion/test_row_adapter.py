"""
Unit tests for the BSI bank-statement row adapter.

These tests verify:

- Default and custom bank-statement column mappings
- Pandas-style missing values
- Supported and unsupported transaction dates
- Payment and deposit adaptation
- Required-column validation
- Description and memo validation
- Amount-validation error wrapping
- Transaction, source-document, and processing-run identifiers
- Organizational and bank-account context
- Source-file and source-row lineage
"""

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pandas as pd
import pytest

from bsi.domain.transactions import (
    TransactionContext,
    TransactionDirection,
)
from bsi.infrastructure.ingestion.row_adapter import (
    BankStatementColumnMap,
    BankStatementRowAdapter,
    RowAdaptationError,
    parse_transaction_date,
)


def build_valid_row() -> dict[str, object]:
    """Return one valid DD13-style bank-statement row."""

    return {
        "Date": "07/14/2026",
        "Payee": "ORIG CO NAME:DoorDash, Inc.",
        "Memo": "Weekly settlement",
        "Payment": None,
        "Deposit": "500.00",
    }


@pytest.mark.unit
def test_default_adapter_creates_deposit_transaction() -> None:
    """A DD13-style deposit row should become a domain transaction."""

    row = build_valid_row()
    row["Payment"] = float("nan")

    adapter = BankStatementRowAdapter()

    transaction = adapter.adapt(
        row,
        file_name="bank_statement_dd13.xlsx",
        source_row_number=3,
        sheet_name="Sheet1",
    )

    assert transaction.transaction_date == date(2026, 7, 14)
    assert transaction.original_description == ("ORIG CO NAME:DoorDash, Inc.")
    assert transaction.original_memo == "Weekly settlement"
    assert transaction.direction is TransactionDirection.DEPOSIT
    assert transaction.payment == Decimal("0.00")
    assert transaction.deposit == Decimal("500.00")
    assert transaction.signed_amount == Decimal("500.00")
    assert transaction.source.file_name == "bank_statement_dd13.xlsx"
    assert transaction.source.source_row_number == 3
    assert transaction.source.sheet_name == "Sheet1"


@pytest.mark.unit
def test_adapter_creates_payment_transaction() -> None:
    """A payment row should produce a negative signed amount."""

    row = build_valid_row()
    row["Payment"] = "177.70"
    row["Deposit"] = ""

    transaction = BankStatementRowAdapter().adapt(
        row,
        file_name="bank_statement_dd13.xlsx",
        source_row_number=4,
    )

    assert transaction.direction is TransactionDirection.PAYMENT
    assert transaction.payment == Decimal("177.70")
    assert transaction.deposit == Decimal("0.00")
    assert transaction.signed_amount == Decimal("-177.70")


@pytest.mark.unit
def test_missing_optional_memo_column_is_allowed() -> None:
    """A missing optional memo column should become None."""

    row = build_valid_row()
    del row["Memo"]

    transaction = BankStatementRowAdapter().adapt(
        row,
        file_name="bank_statement.csv",
        source_row_number=2,
    )

    assert transaction.original_memo is None


@pytest.mark.unit
def test_memo_column_can_be_disabled() -> None:
    """A bank format without memo data may disable that column."""

    row: dict[str, object] = {
        "Date": "07/14/2026",
        "Payee": "Utility Payment",
        "Payment": "100.00",
        "Deposit": None,
    }

    adapter = BankStatementRowAdapter(columns=BankStatementColumnMap(memo=None))

    transaction = adapter.adapt(
        row,
        file_name="bank_statement.csv",
        source_row_number=2,
    )

    assert transaction.original_memo is None


@pytest.mark.unit
def test_custom_column_map_supports_different_bank_headers() -> None:
    """The adapter should support bank-specific source headers."""

    row: dict[str, object] = {
        "Transaction Date": "2026-07-14",
        "Description": "Payroll Processor",
        "Details": "Weekly payroll",
        "Debit": "2500.00",
        "Credit": None,
    }

    adapter = BankStatementRowAdapter(
        columns=BankStatementColumnMap(
            transaction_date="Transaction Date",
            description="Description",
            memo="Details",
            payment="Debit",
            deposit="Credit",
        )
    )

    transaction = adapter.adapt(
        row,
        file_name="custom_bank.csv",
        source_row_number=8,
    )

    assert transaction.transaction_date == date(2026, 7, 14)
    assert transaction.original_description == "Payroll Processor"
    assert transaction.original_memo == "Weekly payroll"
    assert transaction.direction is TransactionDirection.PAYMENT
    assert transaction.signed_amount == Decimal("-2500.00")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_value", "expected_date"),
    [
        (date(2026, 7, 14), date(2026, 7, 14)),
        (
            datetime(2026, 7, 14, 15, 30),
            date(2026, 7, 14),
        ),
        ("2026-07-14", date(2026, 7, 14)),
        ("2026-07-14 15:30:00", date(2026, 7, 14)),
        ("07/14/2026", date(2026, 7, 14)),
        ("07/14/26", date(2026, 7, 14)),
        ("2026/07/14", date(2026, 7, 14)),
    ],
)
def test_supported_transaction_dates_are_parsed(
    raw_value: object,
    expected_date: date,
) -> None:
    """Supported external date formats should produce Python dates."""

    assert parse_transaction_date(raw_value) == expected_date


@pytest.mark.unit
@pytest.mark.parametrize(
    "missing_value",
    [
        None,
        "",
        "   ",
        pd.NA,
        pd.NaT,
    ],
)
def test_missing_transaction_date_is_rejected(
    missing_value: object,
) -> None:
    """Missing source dates must not enter the transaction domain."""

    with pytest.raises(
        RowAdaptationError,
        match="transaction_date cannot be empty",
    ):
        parse_transaction_date(missing_value)


@pytest.mark.unit
def test_ambiguous_day_first_date_is_not_guessed() -> None:
    """Unsupported day-first dates should require explicit configuration."""

    with pytest.raises(
        RowAdaptationError,
        match="unsupported date value",
    ):
        parse_transaction_date("14/07/2026")


@pytest.mark.unit
def test_non_date_object_is_rejected() -> None:
    """Unexpected date types should produce a clear error."""

    with pytest.raises(
        RowAdaptationError,
        match="must contain a date or date string",
    ):
        parse_transaction_date(20260714)


@pytest.mark.unit
@pytest.mark.parametrize(
    "missing_column",
    [
        "Date",
        "Payee",
        "Payment",
        "Deposit",
    ],
)
def test_missing_required_column_is_rejected(
    missing_column: str,
) -> None:
    """Every configured required column must exist in the source row."""

    row = build_valid_row()
    del row[missing_column]

    with pytest.raises(
        RowAdaptationError,
        match=(
            r"Could not normalize bank_statement\.xlsx row 3.*"
            r"Required column is missing"
        ),
    ):
        BankStatementRowAdapter().adapt(
            row,
            file_name="bank_statement.xlsx",
            source_row_number=3,
        )


@pytest.mark.unit
def test_blank_description_is_rejected_with_row_context() -> None:
    """A blank payee should produce a traceable row-level error."""

    row = build_valid_row()
    row["Payee"] = "   "

    with pytest.raises(
        RowAdaptationError,
        match=(
            r"Could not normalize bank_statement\.xlsx row 15.*"
            r"Payee cannot be empty"
        ),
    ):
        BankStatementRowAdapter().adapt(
            row,
            file_name="bank_statement.xlsx",
            source_row_number=15,
        )


@pytest.mark.unit
def test_non_string_description_is_rejected() -> None:
    """Transaction descriptions must contain text."""

    row = build_valid_row()
    row["Payee"] = 12345

    with pytest.raises(
        RowAdaptationError,
        match="Payee must contain text",
    ):
        BankStatementRowAdapter().adapt(
            row,
            file_name="bank_statement.xlsx",
            source_row_number=3,
        )


@pytest.mark.unit
def test_both_payment_and_deposit_are_rejected() -> None:
    """The adapter must not guess the direction of an ambiguous row."""

    row = build_valid_row()
    row["Payment"] = "100.00"
    row["Deposit"] = "50.00"

    with pytest.raises(
        RowAdaptationError,
        match="cannot contain both a payment and a deposit",
    ):
        BankStatementRowAdapter().adapt(
            row,
            file_name="bank_statement.xlsx",
            source_row_number=3,
        )


@pytest.mark.unit
def test_row_without_payment_or_deposit_is_rejected() -> None:
    """A row without cash movement is not a valid transaction."""

    row = build_valid_row()
    row["Payment"] = pd.NA
    row["Deposit"] = float("nan")

    with pytest.raises(
        RowAdaptationError,
        match="must contain either a payment or a deposit",
    ):
        BankStatementRowAdapter().adapt(
            row,
            file_name="bank_statement.xlsx",
            source_row_number=3,
        )


@pytest.mark.unit
def test_invalid_money_text_is_rejected() -> None:
    """Invalid spreadsheet money must produce a row-level error."""

    row = build_valid_row()
    row["Deposit"] = "five hundred"

    with pytest.raises(
        RowAdaptationError,
        match="deposit contains an invalid monetary value",
    ):
        BankStatementRowAdapter().adapt(
            row,
            file_name="bank_statement.xlsx",
            source_row_number=3,
        )


@pytest.mark.unit
def test_boolean_amount_is_rejected() -> None:
    """Boolean spreadsheet values must not become monetary amounts."""

    row = build_valid_row()
    row["Deposit"] = True

    with pytest.raises(
        RowAdaptationError,
        match="deposit cannot be a boolean value",
    ):
        BankStatementRowAdapter().adapt(
            row,
            file_name="bank_statement.xlsx",
            source_row_number=3,
        )


@pytest.mark.unit
def test_unsupported_amount_type_is_rejected() -> None:
    """Collections and other non-scalar values are invalid amounts."""

    row = build_valid_row()
    row["Deposit"] = ["500.00"]

    with pytest.raises(
        RowAdaptationError,
        match="Deposit contains an unsupported amount type",
    ):
        BankStatementRowAdapter().adapt(
            row,
            file_name="bank_statement.xlsx",
            source_row_number=3,
        )


@pytest.mark.unit
def test_source_and_processing_identifiers_are_preserved() -> None:
    """The transaction must retain complete processing lineage."""

    source_document_id = uuid4()
    processing_run_id = uuid4()

    transaction = BankStatementRowAdapter().adapt(
        build_valid_row(),
        file_name="bank_statement.xlsx",
        source_row_number=12,
        source_document_id=source_document_id,
        processing_run_id=processing_run_id,
    )

    assert transaction.source.source_document_id == source_document_id
    assert transaction.source.processing_run_id == processing_run_id


@pytest.mark.unit
def test_transaction_id_and_context_are_preserved() -> None:
    """Previously resolved identifiers should enter the domain unchanged."""

    transaction_id = uuid4()
    company_id = uuid4()
    store_id = uuid4()
    bank_account_id = uuid4()

    context = TransactionContext(
        company_id=company_id,
        store_id=store_id,
        bank_account_id=bank_account_id,
        bank_name="Chase",
        account_last_four="0750",
    )

    transaction = BankStatementRowAdapter().adapt(
        build_valid_row(),
        file_name="bank_statement.xlsx",
        source_row_number=3,
        transaction_id=transaction_id,
        context=context,
    )

    assert transaction.transaction_id == transaction_id
    assert transaction.context.company_id == company_id
    assert transaction.context.store_id == store_id
    assert transaction.context.bank_account_id == bank_account_id
    assert transaction.context.bank_name == "Chase"
    assert transaction.context.account_last_four == "0750"


@pytest.mark.unit
def test_invalid_source_row_number_is_wrapped() -> None:
    """Source-lineage failures should identify the file and source row."""

    with pytest.raises(
        RowAdaptationError,
        match=(
            r"Could not normalize bank_statement\.xlsx row 0.*"
            r"source_row_number must be greater than or equal to 1"
        ),
    ):
        BankStatementRowAdapter().adapt(
            build_valid_row(),
            file_name="bank_statement.xlsx",
            source_row_number=0,
        )


@pytest.mark.unit
def test_row_must_be_mapping() -> None:
    """A list or other sequence is not a valid bank-statement record."""

    invalid_row = cast(
        Mapping[str, object],
        ["not", "a", "mapping"],
    )

    with pytest.raises(
        RowAdaptationError,
        match="must be a mapping",
    ):
        BankStatementRowAdapter().adapt(
            invalid_row,
            file_name="bank_statement.xlsx",
            source_row_number=3,
        )


@pytest.mark.unit
def test_column_map_normalizes_header_names() -> None:
    """Configured external header names should be trimmed."""

    columns = BankStatementColumnMap(
        transaction_date="  Date  ",
        description="  Payee  ",
        memo="  Memo  ",
        payment="  Payment  ",
        deposit="  Deposit  ",
    )

    assert columns.transaction_date == "Date"
    assert columns.description == "Payee"
    assert columns.memo == "Memo"
    assert columns.payment == "Payment"
    assert columns.deposit == "Deposit"


@pytest.mark.unit
def test_duplicate_required_column_names_are_rejected() -> None:
    """Two required transaction fields cannot use the same source header."""

    with pytest.raises(
        RowAdaptationError,
        match="must use unique names",
    ):
        BankStatementColumnMap(
            transaction_date="Date",
            description="Date",
        )


@pytest.mark.unit
def test_memo_cannot_duplicate_required_column() -> None:
    """The memo header must not reuse a required source header."""

    with pytest.raises(
        RowAdaptationError,
        match="memo column must not duplicate",
    ):
        BankStatementColumnMap(memo="Payee")


@pytest.mark.unit
def test_blank_required_column_name_is_rejected() -> None:
    """Column-map configuration cannot contain blank required names."""

    with pytest.raises(
        RowAdaptationError,
        match="description column name cannot be empty",
    ):
        BankStatementColumnMap(description="   ")


@pytest.mark.unit
def test_adapter_uses_separate_default_column_map_instances() -> None:
    """Each adapter should receive its own default column-map object."""

    first_adapter = BankStatementRowAdapter()
    second_adapter = BankStatementRowAdapter()

    assert first_adapter.columns == second_adapter.columns
    assert first_adapter.columns is not second_adapter.columns
