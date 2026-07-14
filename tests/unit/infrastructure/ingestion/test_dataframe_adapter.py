"""
Unit tests for the BSI Pandas DataFrame ingestion adapter.

These tests verify:

- Successful and failed rows are collected separately
- One invalid row does not stop the remaining file
- Source-file row numbers are preserved
- DataFrame indexes do not replace original source-row numbers
- Batch success metrics are calculated deterministically
- Empty DataFrames are handled safely
- File-level column validation occurs before row processing
- Duplicate source columns are rejected
- File metadata and processing identifiers are preserved
- Custom bank-statement column mappings are supported
- Invalid adapter inputs are rejected
"""

from decimal import Decimal
from typing import cast
from uuid import uuid4

import pandas as pd
import pytest

from bsi.domain.transactions import (
    TransactionContext,
    TransactionDirection,
)
from bsi.infrastructure.ingestion.dataframe_adapter import (
    BankStatementDataFrameAdapter,
    DataFrameAdaptationError,
)
from bsi.infrastructure.ingestion.row_adapter import (
    BankStatementColumnMap,
    BankStatementRowAdapter,
)


def build_valid_dataframe() -> pd.DataFrame:
    """Create a reusable two-row DD13-style bank statement."""

    return pd.DataFrame(
        [
            {
                "Date": "07/14/2026",
                "Payee": "DoorDash Settlement",
                "Memo": "Weekly settlement",
                "Payment": None,
                "Deposit": "500.00",
            },
            {
                "Date": "07/15/2026",
                "Payee": "National DCP",
                "Memo": "Food purchases",
                "Payment": "177.70",
                "Deposit": None,
            },
        ]
    )


@pytest.mark.unit
def test_dataframe_adapter_collects_successes_and_failures() -> None:
    """Valid rows should survive when another row fails validation."""

    dataframe = build_valid_dataframe()

    invalid_row = pd.DataFrame(
        [
            {
                "Date": "07/16/2026",
                "Payee": "Invalid Row",
                "Memo": None,
                "Payment": "100.00",
                "Deposit": "50.00",
            }
        ]
    )

    dataframe = pd.concat(
        [dataframe, invalid_row],
        ignore_index=True,
    )

    result = BankStatementDataFrameAdapter().adapt(
        dataframe,
        file_name="bank_statement_dd13.xlsx",
        first_data_row_number=3,
        sheet_name="Sheet1",
    )

    assert result.file_name == "bank_statement_dd13.xlsx"
    assert result.total_rows == 3
    assert result.successful_rows == 2
    assert result.failed_rows == 1
    assert result.has_failures is True
    assert result.all_rows_succeeded is False
    assert result.success_rate == Decimal("66.67")

    assert len(result.transactions) == 2
    assert len(result.failures) == 1

    assert result.transactions[0].direction is TransactionDirection.DEPOSIT
    assert result.transactions[0].signed_amount == Decimal("500.00")

    assert result.transactions[1].direction is TransactionDirection.PAYMENT
    assert result.transactions[1].signed_amount == Decimal("-177.70")

    failure = result.failures[0]

    assert failure.file_name == "bank_statement_dd13.xlsx"
    assert failure.source_row_number == 5
    assert "cannot contain both a payment and a deposit" in failure.message


@pytest.mark.unit
def test_all_valid_rows_produce_complete_success_metrics() -> None:
    """A completely valid DataFrame should report 100 percent success."""

    result = BankStatementDataFrameAdapter().adapt(
        build_valid_dataframe(),
        file_name="bank_statement.xlsx",
    )

    assert result.total_rows == 2
    assert result.successful_rows == 2
    assert result.failed_rows == 0
    assert result.has_failures is False
    assert result.all_rows_succeeded is True
    assert result.success_rate == Decimal("100.00")


@pytest.mark.unit
def test_empty_dataframe_produces_safe_zero_metrics() -> None:
    """An empty but structurally valid DataFrame should not fail."""

    dataframe = pd.DataFrame(
        columns=[
            "Date",
            "Payee",
            "Memo",
            "Payment",
            "Deposit",
        ]
    )

    result = BankStatementDataFrameAdapter().adapt(
        dataframe,
        file_name="empty_statement.xlsx",
    )

    assert result.total_rows == 0
    assert result.successful_rows == 0
    assert result.failed_rows == 0
    assert result.has_failures is False
    assert result.all_rows_succeeded is False
    assert result.success_rate == Decimal("0.00")
    assert result.transactions == ()
    assert result.failures == ()


@pytest.mark.unit
def test_all_failed_rows_produce_zero_success_rate() -> None:
    """A file containing only invalid rows should report zero success."""

    dataframe = pd.DataFrame(
        [
            {
                "Date": "07/14/2026",
                "Payee": "Invalid Row 1",
                "Memo": None,
                "Payment": "100.00",
                "Deposit": "50.00",
            },
            {
                "Date": "07/15/2026",
                "Payee": "Invalid Row 2",
                "Memo": None,
                "Payment": None,
                "Deposit": None,
            },
        ]
    )

    result = BankStatementDataFrameAdapter().adapt(
        dataframe,
        file_name="invalid_statement.xlsx",
    )

    assert result.total_rows == 2
    assert result.successful_rows == 0
    assert result.failed_rows == 2
    assert result.success_rate == Decimal("0.00")
    assert result.all_rows_succeeded is False


@pytest.mark.unit
def test_source_row_numbers_use_dataframe_position() -> None:
    """Pandas index labels must not replace original file row numbers."""

    dataframe = build_valid_dataframe()
    dataframe.index = [100, 500]

    result = BankStatementDataFrameAdapter().adapt(
        dataframe,
        file_name="bank_statement.xlsx",
        first_data_row_number=7,
    )

    source_row_numbers = [
        transaction.source.source_row_number for transaction in result.transactions
    ]

    assert source_row_numbers == [7, 8]


@pytest.mark.unit
def test_default_first_data_row_number_is_two() -> None:
    """CSV-style data should default to source row two."""

    result = BankStatementDataFrameAdapter().adapt(
        build_valid_dataframe(),
        file_name="bank_statement.csv",
    )

    assert result.transactions[0].source.source_row_number == 2
    assert result.transactions[1].source.source_row_number == 3


@pytest.mark.unit
def test_excel_header_offset_can_be_preserved() -> None:
    """Excel files loaded with header=1 should begin data at row three."""

    result = BankStatementDataFrameAdapter().adapt(
        build_valid_dataframe(),
        file_name="bank_statement.xlsx",
        first_data_row_number=3,
    )

    assert result.transactions[0].source.source_row_number == 3
    assert result.transactions[1].source.source_row_number == 4


@pytest.mark.unit
def test_file_name_is_normalized() -> None:
    """Leading and trailing filename whitespace should be removed."""

    result = BankStatementDataFrameAdapter().adapt(
        build_valid_dataframe(),
        file_name="  bank_statement.xlsx  ",
    )

    assert result.file_name == "bank_statement.xlsx"
    assert result.transactions[0].source.file_name == "bank_statement.xlsx"


@pytest.mark.unit
def test_shared_context_is_preserved_for_every_transaction() -> None:
    """File-level organizational context should enter every valid row."""

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

    result = BankStatementDataFrameAdapter().adapt(
        build_valid_dataframe(),
        file_name="bank_statement.xlsx",
        context=context,
    )

    for transaction in result.transactions:
        assert transaction.context.company_id == company_id
        assert transaction.context.store_id == store_id
        assert transaction.context.bank_account_id == bank_account_id
        assert transaction.context.bank_name == "Chase"
        assert transaction.context.account_last_four == "0750"


@pytest.mark.unit
def test_processing_lineage_is_preserved_for_every_transaction() -> None:
    """Document and processing-run IDs should be retained."""

    source_document_id = uuid4()
    processing_run_id = uuid4()

    result = BankStatementDataFrameAdapter().adapt(
        build_valid_dataframe(),
        file_name="bank_statement.xlsx",
        source_document_id=source_document_id,
        processing_run_id=processing_run_id,
    )

    for transaction in result.transactions:
        assert transaction.source.source_document_id == source_document_id
        assert transaction.source.processing_run_id == processing_run_id


@pytest.mark.unit
def test_sheet_name_is_preserved_for_every_transaction() -> None:
    """Excel worksheet lineage should remain available."""

    result = BankStatementDataFrameAdapter().adapt(
        build_valid_dataframe(),
        file_name="bank_statement.xlsx",
        sheet_name="Bank Data",
    )

    for transaction in result.transactions:
        assert transaction.source.sheet_name == "Bank Data"


@pytest.mark.unit
def test_invalid_row_does_not_stop_later_valid_row() -> None:
    """Processing must continue after a row-level failure."""

    dataframe = pd.DataFrame(
        [
            {
                "Date": "07/14/2026",
                "Payee": "Invalid Row",
                "Memo": None,
                "Payment": "100.00",
                "Deposit": "50.00",
            },
            {
                "Date": "07/15/2026",
                "Payee": "Valid Deposit",
                "Memo": None,
                "Payment": None,
                "Deposit": "250.00",
            },
        ]
    )

    result = BankStatementDataFrameAdapter().adapt(
        dataframe,
        file_name="bank_statement.xlsx",
        first_data_row_number=10,
    )

    assert result.failed_rows == 1
    assert result.successful_rows == 1
    assert result.failures[0].source_row_number == 10
    assert result.transactions[0].source.source_row_number == 11
    assert result.transactions[0].signed_amount == Decimal("250.00")


@pytest.mark.unit
def test_optional_memo_column_may_be_absent() -> None:
    """A missing optional memo column should not reject the file."""

    dataframe = build_valid_dataframe().drop(columns=["Memo"])

    result = BankStatementDataFrameAdapter().adapt(
        dataframe,
        file_name="bank_statement.csv",
    )

    assert result.successful_rows == 2

    for transaction in result.transactions:
        assert transaction.original_memo is None


@pytest.mark.unit
def test_custom_column_mapping_is_supported() -> None:
    """A custom bank format should work through its configured adapter."""

    dataframe = pd.DataFrame(
        [
            {
                "Transaction Date": "2026-07-14",
                "Description": "Payroll Processor",
                "Details": "Weekly payroll",
                "Debit": "2500.00",
                "Credit": None,
            }
        ]
    )

    row_adapter = BankStatementRowAdapter(
        columns=BankStatementColumnMap(
            transaction_date="Transaction Date",
            description="Description",
            memo="Details",
            payment="Debit",
            deposit="Credit",
        )
    )

    adapter = BankStatementDataFrameAdapter(row_adapter=row_adapter)

    result = adapter.adapt(
        dataframe,
        file_name="custom_bank.csv",
    )

    transaction = result.transactions[0]

    assert transaction.original_description == "Payroll Processor"
    assert transaction.original_memo == "Weekly payroll"
    assert transaction.direction is TransactionDirection.PAYMENT
    assert transaction.signed_amount == Decimal("-2500.00")


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
def test_missing_required_dataframe_column_is_rejected(
    missing_column: str,
) -> None:
    """Missing required headers should fail once at file level."""

    dataframe = build_valid_dataframe().drop(columns=[missing_column])

    with pytest.raises(
        DataFrameAdaptationError,
        match="missing required bank-statement columns",
    ):
        BankStatementDataFrameAdapter().adapt(
            dataframe,
            file_name="bank_statement.xlsx",
        )


@pytest.mark.unit
def test_duplicate_required_dataframe_column_is_rejected() -> None:
    """Duplicate required headers create an ambiguous source schema."""

    dataframe = pd.DataFrame(
        [
            [
                "07/14/2026",
                "Vendor",
                None,
                "100.00",
                "200.00",
                None,
            ]
        ],
        columns=[
            "Date",
            "Payee",
            "Memo",
            "Payment",
            "Payment",
            "Deposit",
        ],
    )

    with pytest.raises(
        DataFrameAdaptationError,
        match="duplicate required columns",
    ):
        BankStatementDataFrameAdapter().adapt(
            dataframe,
            file_name="bank_statement.xlsx",
        )


@pytest.mark.unit
def test_duplicate_memo_dataframe_column_is_rejected() -> None:
    """Duplicate optional memo headers should also be rejected."""

    dataframe = pd.DataFrame(
        [
            [
                "07/14/2026",
                "Vendor",
                "Memo one",
                "Memo two",
                "100.00",
                None,
            ]
        ],
        columns=[
            "Date",
            "Payee",
            "Memo",
            "Memo",
            "Payment",
            "Deposit",
        ],
    )

    with pytest.raises(
        DataFrameAdaptationError,
        match="duplicate memo column",
    ):
        BankStatementDataFrameAdapter().adapt(
            dataframe,
            file_name="bank_statement.xlsx",
        )


@pytest.mark.unit
def test_non_dataframe_input_is_rejected() -> None:
    """Sequences and mappings are not complete Pandas DataFrames."""

    invalid_dataframe = cast(
        pd.DataFrame,
        [{"Date": "07/14/2026"}],
    )

    with pytest.raises(
        DataFrameAdaptationError,
        match="must be a pandas DataFrame",
    ):
        BankStatementDataFrameAdapter().adapt(
            invalid_dataframe,
            file_name="bank_statement.xlsx",
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_file_name",
    [
        "",
        "   ",
        "folder/bank_statement.xlsx",
        "folder\\bank_statement.xlsx",
    ],
)
def test_invalid_file_name_is_rejected(
    invalid_file_name: str,
) -> None:
    """File metadata must contain only a safe filename."""

    with pytest.raises(DataFrameAdaptationError):
        BankStatementDataFrameAdapter().adapt(
            build_valid_dataframe(),
            file_name=invalid_file_name,
        )


@pytest.mark.unit
def test_non_string_file_name_is_rejected() -> None:
    """Filename metadata must be represented as text."""

    invalid_file_name = cast(str, 123)

    with pytest.raises(
        DataFrameAdaptationError,
        match="file_name must be a string",
    ):
        BankStatementDataFrameAdapter().adapt(
            build_valid_dataframe(),
            file_name=invalid_file_name,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_row_number",
    [
        0,
        -1,
    ],
)
def test_non_positive_first_data_row_number_is_rejected(
    invalid_row_number: int,
) -> None:
    """Original source-row numbering must begin at one or later."""

    with pytest.raises(
        DataFrameAdaptationError,
        match="greater than or equal to 1",
    ):
        BankStatementDataFrameAdapter().adapt(
            build_valid_dataframe(),
            file_name="bank_statement.xlsx",
            first_data_row_number=invalid_row_number,
        )


@pytest.mark.unit
def test_boolean_first_data_row_number_is_rejected() -> None:
    """Boolean values must not be treated as integer row numbers."""

    with pytest.raises(
        DataFrameAdaptationError,
        match="must be an integer",
    ):
        BankStatementDataFrameAdapter().adapt(
            build_valid_dataframe(),
            file_name="bank_statement.xlsx",
            first_data_row_number=True,
        )


@pytest.mark.unit
def test_non_integer_first_data_row_number_is_rejected() -> None:
    """Floating-point source-row positions are invalid."""

    invalid_row_number = cast(int, 2.5)

    with pytest.raises(
        DataFrameAdaptationError,
        match="must be an integer",
    ):
        BankStatementDataFrameAdapter().adapt(
            build_valid_dataframe(),
            file_name="bank_statement.xlsx",
            first_data_row_number=invalid_row_number,
        )


@pytest.mark.unit
def test_default_row_adapters_are_not_shared() -> None:
    """Each DataFrame adapter should own a separate row adapter."""

    first_adapter = BankStatementDataFrameAdapter()
    second_adapter = BankStatementDataFrameAdapter()

    assert first_adapter.row_adapter == second_adapter.row_adapter
    assert first_adapter.row_adapter is not second_adapter.row_adapter
