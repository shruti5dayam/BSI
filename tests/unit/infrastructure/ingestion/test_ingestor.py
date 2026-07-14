"""
Unit tests for the BSI bank-statement ingestion facade.

These tests verify:

- Complete CSV ingestion from uploaded bytes
- Complete XLSX ingestion from uploaded bytes
- Filesystem-path ingestion
- Valid and invalid rows are returned separately
- Header-row and worksheet lineage is preserved
- Company, store, bank, document, and run context is preserved
- Empty but structurally valid files are handled safely
- Custom bank-statement columns can be injected
- Reader errors propagate through the facade
- Ingestion-stage invariants are enforced
- Default dependencies are not shared between ingestor instances
"""

from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from bsi.domain.transactions import (
    TransactionContext,
    TransactionDirection,
)
from bsi.infrastructure.ingestion.dataframe_adapter import (
    BankStatementDataFrameAdapter,
    DataFrameAdaptationResult,
    RowAdaptationFailure,
)
from bsi.infrastructure.ingestion.file_reader import (
    BankStatementFileFormat,
    BankStatementFileReader,
    BankStatementReadError,
    BankStatementReadResult,
)
from bsi.infrastructure.ingestion.ingestor import (
    BankStatementIngestionInvariantError,
    BankStatementIngestionResult,
    BankStatementIngestor,
)
from bsi.infrastructure.ingestion.row_adapter import (
    BankStatementColumnMap,
    BankStatementRowAdapter,
)


def build_valid_csv_bytes() -> bytes:
    """Return a valid two-row DD13-style CSV bank statement."""

    return (
        b"Date,Payee,Memo,Payment,Deposit\n"
        b"07/14/2026,DoorDash Settlement,Weekly settlement,,500.00\n"
        b"07/15/2026,National DCP,Food purchases,177.70,\n"
    )


def build_partially_invalid_csv_bytes() -> bytes:
    """Return two valid rows and one financially invalid row."""

    return (
        b"Date,Payee,Memo,Payment,Deposit\n"
        b"07/14/2026,DoorDash Settlement,Weekly settlement,,500.00\n"
        b"07/15/2026,National DCP,Food purchases,177.70,\n"
        b"07/16/2026,Invalid Row,Ambiguous,100.00,50.00\n"
    )


def build_bank_dataframe() -> pd.DataFrame:
    """Return a reusable bank-statement DataFrame."""

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


def build_xlsx_bytes(
    sheets: dict[str, pd.DataFrame] | None = None,
) -> bytes:
    """Build an in-memory XLSX workbook."""

    workbook_sheets = sheets or {
        "Transactions": build_bank_dataframe(),
    }

    buffer = BytesIO()

    with pd.ExcelWriter(
        buffer,
        engine="openpyxl",
    ) as writer:
        for sheet_name, dataframe in workbook_sheets.items():
            dataframe.to_excel(
                writer,
                sheet_name=sheet_name,
                index=False,
            )

    return buffer.getvalue()


@pytest.mark.unit
def test_ingestor_processes_valid_csv_bytes() -> None:
    """A valid CSV upload should produce normalized transactions."""

    result = BankStatementIngestor().ingest_bytes(
        build_valid_csv_bytes(),
        file_name="bank_statement_dd13.csv",
    )

    assert result.file_name == "bank_statement_dd13.csv"
    assert result.file_format is BankStatementFileFormat.CSV
    assert result.sheet_name is None
    assert result.header_row_number == 1
    assert result.first_data_row_number == 2
    assert result.source_row_count == 2
    assert result.source_column_count == 5

    assert result.successful_rows == 2
    assert result.failed_rows == 0
    assert result.has_failures is False
    assert result.all_rows_succeeded is True
    assert result.success_rate == Decimal("100.00")
    assert result.is_empty is False

    first_transaction = result.transactions[0]
    second_transaction = result.transactions[1]

    assert first_transaction.direction is TransactionDirection.DEPOSIT
    assert first_transaction.signed_amount == Decimal("500.00")
    assert first_transaction.source.source_row_number == 2

    assert second_transaction.direction is TransactionDirection.PAYMENT
    assert second_transaction.signed_amount == Decimal("-177.70")
    assert second_transaction.source.source_row_number == 3


@pytest.mark.unit
def test_ingestor_preserves_valid_rows_when_one_row_fails() -> None:
    """One invalid row must not discard valid transactions."""

    result = BankStatementIngestor().ingest_bytes(
        build_partially_invalid_csv_bytes(),
        file_name="bank_statement_dd13.csv",
    )

    assert result.source_row_count == 3
    assert result.successful_rows == 2
    assert result.failed_rows == 1
    assert result.has_failures is True
    assert result.all_rows_succeeded is False
    assert result.success_rate == Decimal("66.67")

    assert len(result.transactions) == 2
    assert len(result.failures) == 1

    failure = result.failures[0]

    assert failure.file_name == "bank_statement_dd13.csv"
    assert failure.source_row_number == 4
    assert "cannot contain both a payment and a deposit" in failure.message


@pytest.mark.unit
def test_ingestor_preserves_csv_header_row_lineage() -> None:
    """Metadata rows before the CSV header should affect source rows."""

    csv_content = (
        b"Generated for July 2026\n"
        b"Date,Payee,Memo,Payment,Deposit\n"
        b"07/14/2026,DoorDash Settlement,Weekly settlement,,500.00\n"
    )

    result = BankStatementIngestor().ingest_bytes(
        csv_content,
        file_name="bank_statement.csv",
        header_row_number=2,
    )

    assert result.header_row_number == 2
    assert result.first_data_row_number == 3
    assert result.transactions[0].source.source_row_number == 3


@pytest.mark.unit
def test_ingestor_processes_default_xlsx_sheet() -> None:
    """The first worksheet should be processed by default."""

    result = BankStatementIngestor().ingest_bytes(
        build_xlsx_bytes(),
        file_name="bank_statement.xlsx",
    )

    assert result.file_format is BankStatementFileFormat.XLSX
    assert result.sheet_name == "Transactions"
    assert result.source_row_count == 2
    assert result.successful_rows == 2

    for transaction in result.transactions:
        assert transaction.source.sheet_name == "Transactions"


@pytest.mark.unit
def test_ingestor_selects_xlsx_sheet_by_name() -> None:
    """A named transaction worksheet should be selected safely."""

    sheets = {
        "Summary": pd.DataFrame([{"Report": "July 2026"}]),
        "Bank Data": build_bank_dataframe(),
    }

    result = BankStatementIngestor().ingest_bytes(
        build_xlsx_bytes(sheets),
        file_name="bank_statement.xlsx",
        sheet_name="Bank Data",
    )

    assert result.sheet_name == "Bank Data"
    assert result.successful_rows == 2

    for transaction in result.transactions:
        assert transaction.source.sheet_name == "Bank Data"


@pytest.mark.unit
def test_ingestor_selects_xlsx_sheet_by_position() -> None:
    """A worksheet may be selected by zero-based position."""

    sheets = {
        "Summary": pd.DataFrame([{"Report": "July 2026"}]),
        "Bank Data": build_bank_dataframe(),
    }

    result = BankStatementIngestor().ingest_bytes(
        build_xlsx_bytes(sheets),
        file_name="bank_statement.xlsx",
        sheet_name=1,
    )

    assert result.sheet_name == "Bank Data"
    assert result.successful_rows == 2


@pytest.mark.unit
def test_ingestor_processes_csv_filesystem_path(
    tmp_path: Path,
) -> None:
    """Local CSV paths should use the same complete ingestion flow."""

    file_path = tmp_path / "bank_statement.csv"
    file_path.write_bytes(build_valid_csv_bytes())

    result = BankStatementIngestor().ingest_path(file_path)

    assert result.file_name == "bank_statement.csv"
    assert result.file_format is BankStatementFileFormat.CSV
    assert result.successful_rows == 2


@pytest.mark.unit
def test_ingestor_processes_xlsx_filesystem_path(
    tmp_path: Path,
) -> None:
    """Local XLSX paths should preserve worksheet metadata."""

    file_path = tmp_path / "bank_statement.xlsx"
    file_path.write_bytes(build_xlsx_bytes())

    result = BankStatementIngestor().ingest_path(file_path)

    assert result.file_format is BankStatementFileFormat.XLSX
    assert result.sheet_name == "Transactions"
    assert result.successful_rows == 2


@pytest.mark.unit
def test_ingestor_preserves_transaction_context() -> None:
    """Shared organizational and bank context should enter each row."""

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

    result = BankStatementIngestor().ingest_bytes(
        build_valid_csv_bytes(),
        file_name="bank_statement.csv",
        context=context,
    )

    for transaction in result.transactions:
        assert transaction.context.company_id == company_id
        assert transaction.context.store_id == store_id
        assert transaction.context.bank_account_id == bank_account_id
        assert transaction.context.bank_name == "Chase"
        assert transaction.context.account_last_four == "0750"


@pytest.mark.unit
def test_ingestor_preserves_document_and_run_lineage() -> None:
    """Document and processing-run IDs should enter every transaction."""

    source_document_id = uuid4()
    processing_run_id = uuid4()

    result = BankStatementIngestor().ingest_bytes(
        build_valid_csv_bytes(),
        file_name="bank_statement.csv",
        source_document_id=source_document_id,
        processing_run_id=processing_run_id,
    )

    for transaction in result.transactions:
        assert transaction.source.source_document_id == source_document_id
        assert transaction.source.processing_run_id == processing_run_id


@pytest.mark.unit
def test_ingestor_handles_empty_structurally_valid_csv() -> None:
    """A CSV containing headers but no rows should be reported safely."""

    csv_content = b"Date,Payee,Memo,Payment,Deposit\n"

    result = BankStatementIngestor().ingest_bytes(
        csv_content,
        file_name="empty_statement.csv",
    )

    assert result.source_row_count == 0
    assert result.successful_rows == 0
    assert result.failed_rows == 0
    assert result.success_rate == Decimal("0.00")
    assert result.has_failures is False
    assert result.all_rows_succeeded is False
    assert result.is_empty is True
    assert result.transactions == ()
    assert result.failures == ()


@pytest.mark.unit
def test_ingestor_supports_custom_bank_columns() -> None:
    """Injected adapters should support different bank export formats."""

    csv_content = (
        b"Transaction Date,Description,Details,Debit,Credit\n"
        b"2026-07-14,Payroll Processor,Weekly payroll,2500.00,\n"
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

    dataframe_adapter = BankStatementDataFrameAdapter(row_adapter=row_adapter)

    ingestor = BankStatementIngestor(dataframe_adapter=dataframe_adapter)

    result = ingestor.ingest_bytes(
        csv_content,
        file_name="custom_bank.csv",
    )

    transaction = result.transactions[0]

    assert result.successful_rows == 1
    assert transaction.original_description == "Payroll Processor"
    assert transaction.original_memo == "Weekly payroll"
    assert transaction.direction is TransactionDirection.PAYMENT
    assert transaction.signed_amount == Decimal("-2500.00")


@pytest.mark.unit
def test_file_reader_errors_propagate_through_ingestor() -> None:
    """Unsupported uploads should retain the reader's clear error."""

    with pytest.raises(
        BankStatementReadError,
        match="Unsupported file extension",
    ):
        BankStatementIngestor().ingest_bytes(
            build_valid_csv_bytes(),
            file_name="bank_statement.pdf",
        )


@pytest.mark.unit
def test_missing_required_columns_propagate_from_adapter() -> None:
    """A structurally incomplete file should fail at file level."""

    csv_content = b"Date,Payee,Deposit\n07/14/2026,DoorDash Settlement,500.00\n"

    with pytest.raises(
        ValueError,
        match="missing required bank-statement columns",
    ):
        BankStatementIngestor().ingest_bytes(
            csv_content,
            file_name="bank_statement.csv",
        )


@pytest.mark.unit
def test_ingestion_result_exposes_stage_results() -> None:
    """Callers should retain access to both ingestion stages."""

    result = BankStatementIngestor().ingest_bytes(
        build_valid_csv_bytes(),
        file_name="bank_statement.csv",
    )

    assert isinstance(
        result.read_result,
        BankStatementReadResult,
    )
    assert isinstance(
        result.adaptation_result,
        DataFrameAdaptationResult,
    )
    assert result.read_result.file_name == result.adaptation_result.file_name


@pytest.mark.unit
def test_ingestion_result_rejects_filename_mismatch() -> None:
    """Both stages must describe the same uploaded document."""

    read_result = BankStatementReadResult(
        file_name="first.csv",
        file_format=BankStatementFileFormat.CSV,
        dataframe=pd.DataFrame(),
        header_row_number=1,
        first_data_row_number=2,
        sheet_name=None,
    )

    adaptation_result = DataFrameAdaptationResult(
        file_name="second.csv",
        transactions=(),
        failures=(),
    )

    with pytest.raises(
        BankStatementIngestionInvariantError,
        match="different filenames",
    ):
        BankStatementIngestionResult(
            read_result=read_result,
            adaptation_result=adaptation_result,
        )


@pytest.mark.unit
def test_ingestion_result_rejects_row_count_mismatch() -> None:
    """Reader and adapter row totals must remain consistent."""

    read_result = BankStatementReadResult(
        file_name="bank_statement.csv",
        file_format=BankStatementFileFormat.CSV,
        dataframe=pd.DataFrame([{"Payee": "One source row"}]),
        header_row_number=1,
        first_data_row_number=2,
        sheet_name=None,
    )

    adaptation_result = DataFrameAdaptationResult(
        file_name="bank_statement.csv",
        transactions=(),
        failures=(),
    )

    with pytest.raises(
        BankStatementIngestionInvariantError,
        match="row count does not match",
    ):
        BankStatementIngestionResult(
            read_result=read_result,
            adaptation_result=adaptation_result,
        )


@pytest.mark.unit
def test_matching_manual_ingestion_results_are_accepted() -> None:
    """Consistent stage results should create a valid facade result."""

    read_result = BankStatementReadResult(
        file_name="bank_statement.csv",
        file_format=BankStatementFileFormat.CSV,
        dataframe=pd.DataFrame([{"Payee": "Invalid source row"}]),
        header_row_number=1,
        first_data_row_number=2,
        sheet_name=None,
    )

    failure = RowAdaptationFailure(
        file_name="bank_statement.csv",
        source_row_number=2,
        message="Invalid row.",
    )

    adaptation_result = DataFrameAdaptationResult(
        file_name="bank_statement.csv",
        transactions=(),
        failures=(failure,),
    )

    result = BankStatementIngestionResult(
        read_result=read_result,
        adaptation_result=adaptation_result,
    )

    assert result.source_row_count == 1
    assert result.successful_rows == 0
    assert result.failed_rows == 1
    assert result.has_failures is True


@pytest.mark.unit
def test_injected_file_reader_configuration_is_used() -> None:
    """The facade should respect injected file-size restrictions."""

    ingestor = BankStatementIngestor(
        file_reader=BankStatementFileReader(max_file_size_bytes=10)
    )

    with pytest.raises(
        BankStatementReadError,
        match="exceeds the maximum permitted size",
    ):
        ingestor.ingest_bytes(
            build_valid_csv_bytes(),
            file_name="bank_statement.csv",
        )


@pytest.mark.unit
def test_default_ingestor_dependencies_are_not_shared() -> None:
    """Each ingestor should own separate default dependency objects."""

    first_ingestor = BankStatementIngestor()
    second_ingestor = BankStatementIngestor()

    assert first_ingestor.file_reader == second_ingestor.file_reader
    assert first_ingestor.file_reader is not second_ingestor.file_reader

    assert first_ingestor.dataframe_adapter == second_ingestor.dataframe_adapter
    assert first_ingestor.dataframe_adapter is not second_ingestor.dataframe_adapter


@pytest.mark.unit
def test_ingestor_accepts_bytearray_upload() -> None:
    """Mutable upload buffers should be accepted by the facade."""

    result = BankStatementIngestor().ingest_bytes(
        bytearray(build_valid_csv_bytes()),
        file_name="bank_statement.csv",
    )

    assert result.successful_rows == 2
    assert result.file_format is BankStatementFileFormat.CSV


@pytest.mark.unit
def test_ingestor_normalizes_uploaded_filename() -> None:
    """Leading and trailing uploaded filename whitespace is removed."""

    result = BankStatementIngestor().ingest_bytes(
        build_valid_csv_bytes(),
        file_name="  bank_statement.csv  ",
    )

    assert result.file_name == "bank_statement.csv"

    for transaction in result.transactions:
        assert transaction.source.file_name == "bank_statement.csv"
