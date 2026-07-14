"""
Unit tests for the BSI Excel and CSV bank-statement file reader.

These tests verify:

- CSV files can be read from bytes and filesystem paths.
- XLSX files can be read from bytes and filesystem paths.
- Excel worksheets can be selected by name or position.
- Header-row and first-data-row lineage is preserved.
- Uploaded filenames and content are validated.
- File-size limits are enforced.
- CSV encodings are supported.
- Source column labels are normalized.
- Duplicate normalized columns are rejected.
- Invalid and corrupted files produce ingestion-specific errors.
"""

from io import BytesIO
from pathlib import Path
from typing import cast

import pandas as pd
import pytest

from bsi.infrastructure.ingestion.file_reader import (
    BankStatementFileFormat,
    BankStatementFileReader,
    BankStatementReadError,
)


def build_csv_bytes() -> bytes:
    """Return a valid DD13-style CSV bank statement."""

    return (
        b"Date,Payee,Memo,Payment,Deposit\n"
        b"07/14/2026,DoorDash Settlement,Weekly settlement,,500.00\n"
        b"07/15/2026,National DCP,Food purchases,177.70,\n"
    )


def build_bank_dataframe() -> pd.DataFrame:
    """Return a reusable two-row bank-statement DataFrame."""

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
    """Build an in-memory XLSX workbook for file-reader tests."""

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
def test_reader_reads_csv_bytes() -> None:
    """CSV upload bytes should produce a DataFrame and source metadata."""

    result = BankStatementFileReader().read_bytes(
        build_csv_bytes(),
        file_name="bank_statement_dd13.csv",
    )

    assert result.file_name == "bank_statement_dd13.csv"
    assert result.file_format is BankStatementFileFormat.CSV
    assert result.row_count == 2
    assert result.column_count == 5
    assert result.header_row_number == 1
    assert result.first_data_row_number == 2
    assert result.sheet_name is None
    assert result.is_empty is False

    assert list(result.dataframe.columns) == [
        "Date",
        "Payee",
        "Memo",
        "Payment",
        "Deposit",
    ]

    assert result.dataframe.iloc[0]["Payee"] == "DoorDash Settlement"


@pytest.mark.unit
def test_reader_accepts_bytearray_content() -> None:
    """Mutable bytearray uploads should be converted into immutable bytes."""

    result = BankStatementFileReader().read_bytes(
        bytearray(build_csv_bytes()),
        file_name="bank_statement.csv",
    )

    assert result.row_count == 2
    assert result.file_format is BankStatementFileFormat.CSV


@pytest.mark.unit
def test_csv_header_row_number_is_preserved() -> None:
    """Metadata rows before the CSV header should be skipped safely."""

    csv_content = (
        b"Generated bank statement\n"
        b"Date,Payee,Memo,Payment,Deposit\n"
        b"07/14/2026,DoorDash Settlement,Weekly settlement,,500.00\n"
    )

    result = BankStatementFileReader().read_bytes(
        csv_content,
        file_name="bank_statement.csv",
        header_row_number=2,
    )

    assert result.row_count == 1
    assert result.header_row_number == 2
    assert result.first_data_row_number == 3
    assert list(result.dataframe.columns) == [
        "Date",
        "Payee",
        "Memo",
        "Payment",
        "Deposit",
    ]


@pytest.mark.unit
def test_csv_column_whitespace_is_removed() -> None:
    """External header whitespace should not enter later processing."""

    csv_content = (
        b" Date , Payee , Memo , Payment , Deposit \n"
        b"07/14/2026,DoorDash Settlement,Weekly settlement,,500.00\n"
    )

    result = BankStatementFileReader().read_bytes(
        csv_content,
        file_name="bank_statement.csv",
    )

    assert list(result.dataframe.columns) == [
        "Date",
        "Payee",
        "Memo",
        "Payment",
        "Deposit",
    ]


@pytest.mark.unit
def test_duplicate_columns_after_normalization_are_rejected() -> None:
    """Whitespace normalization must not create ambiguous field names."""

    csv_content = (
        b"Date, Payee ,Payee,Payment,Deposit\n"
        b"07/14/2026,DoorDash Settlement,Duplicate,100.00,\n"
    )

    with pytest.raises(
        BankStatementReadError,
        match="duplicate columns after normalization",
    ):
        BankStatementFileReader().read_bytes(
            csv_content,
            file_name="bank_statement.csv",
        )


@pytest.mark.unit
def test_csv_custom_encoding_is_supported() -> None:
    """Banks exporting non-UTF-8 CSV files can specify their encoding."""

    csv_content = (
        "Date,Payee,Memo,Payment,Deposit\n07/14/2026,Café Vendor,Expense,25.00,\n"
    ).encode("latin-1")

    result = BankStatementFileReader().read_bytes(
        csv_content,
        file_name="bank_statement.csv",
        csv_encoding="latin-1",
    )

    assert result.dataframe.iloc[0]["Payee"] == "Café Vendor"


@pytest.mark.unit
def test_csv_does_not_accept_sheet_name() -> None:
    """Worksheet configuration is invalid for CSV files."""

    with pytest.raises(
        BankStatementReadError,
        match="sheet_name cannot be used with a CSV file",
    ):
        BankStatementFileReader().read_bytes(
            build_csv_bytes(),
            file_name="bank_statement.csv",
            sheet_name="Transactions",
        )


@pytest.mark.unit
def test_reader_reads_default_xlsx_sheet() -> None:
    """The first XLSX worksheet should be selected by default."""

    result = BankStatementFileReader().read_bytes(
        build_xlsx_bytes(),
        file_name="bank_statement.xlsx",
    )

    assert result.file_format is BankStatementFileFormat.XLSX
    assert result.sheet_name == "Transactions"
    assert result.row_count == 2
    assert result.column_count == 5
    assert result.header_row_number == 1
    assert result.first_data_row_number == 2
    assert result.is_empty is False


@pytest.mark.unit
def test_xlsx_sheet_can_be_selected_by_name() -> None:
    """A worksheet should be selectable by its exact name."""

    sheets = {
        "Summary": pd.DataFrame([{"Report": "July 2026"}]),
        "Bank Data": build_bank_dataframe(),
    }

    result = BankStatementFileReader().read_bytes(
        build_xlsx_bytes(sheets),
        file_name="bank_statement.xlsx",
        sheet_name="Bank Data",
    )

    assert result.sheet_name == "Bank Data"
    assert result.row_count == 2
    assert "Payee" in result.dataframe.columns


@pytest.mark.unit
def test_xlsx_sheet_can_be_selected_by_position() -> None:
    """A worksheet should be selectable by zero-based position."""

    sheets = {
        "Summary": pd.DataFrame([{"Report": "July 2026"}]),
        "Bank Data": build_bank_dataframe(),
    }

    result = BankStatementFileReader().read_bytes(
        build_xlsx_bytes(sheets),
        file_name="bank_statement.xlsx",
        sheet_name=1,
    )

    assert result.sheet_name == "Bank Data"
    assert result.row_count == 2


@pytest.mark.unit
def test_missing_xlsx_sheet_is_rejected() -> None:
    """A requested worksheet must exist in the workbook."""

    with pytest.raises(
        BankStatementReadError,
        match="Worksheet 'Missing' was not found",
    ):
        BankStatementFileReader().read_bytes(
            build_xlsx_bytes(),
            file_name="bank_statement.xlsx",
            sheet_name="Missing",
        )


@pytest.mark.unit
def test_blank_xlsx_sheet_name_is_rejected() -> None:
    """Blank worksheet names should not be silently interpreted."""

    with pytest.raises(
        BankStatementReadError,
        match="sheet_name cannot be empty",
    ):
        BankStatementFileReader().read_bytes(
            build_xlsx_bytes(),
            file_name="bank_statement.xlsx",
            sheet_name="   ",
        )


@pytest.mark.unit
def test_xlsx_sheet_position_outside_range_is_rejected() -> None:
    """Worksheet positions must be inside the workbook range."""

    with pytest.raises(
        BankStatementReadError,
        match="outside the available range",
    ):
        BankStatementFileReader().read_bytes(
            build_xlsx_bytes(),
            file_name="bank_statement.xlsx",
            sheet_name=4,
        )


@pytest.mark.unit
def test_boolean_xlsx_sheet_position_is_rejected() -> None:
    """Boolean values must not be treated as worksheet positions."""

    with pytest.raises(
        BankStatementReadError,
        match="sheet_name must be a string, integer, or None",
    ):
        BankStatementFileReader().read_bytes(
            build_xlsx_bytes(),
            file_name="bank_statement.xlsx",
            sheet_name=True,
        )


@pytest.mark.unit
def test_corrupted_xlsx_file_is_rejected() -> None:
    """Invalid ZIP-based workbook content should produce a clear error."""

    with pytest.raises(
        BankStatementReadError,
        match="Could not parse XLSX file",
    ):
        BankStatementFileReader().read_bytes(
            b"This is not a valid XLSX workbook.",
            file_name="bank_statement.xlsx",
        )


@pytest.mark.unit
def test_empty_xlsx_dataframe_is_reported_as_empty() -> None:
    """A valid workbook with headers but no records is structurally valid."""

    empty_dataframe = pd.DataFrame(
        columns=[
            "Date",
            "Payee",
            "Memo",
            "Payment",
            "Deposit",
        ]
    )

    result = BankStatementFileReader().read_bytes(
        build_xlsx_bytes({"Transactions": empty_dataframe}),
        file_name="empty_statement.xlsx",
    )

    assert result.row_count == 0
    assert result.column_count == 5
    assert result.is_empty is True


@pytest.mark.unit
def test_reader_reads_csv_from_path(
    tmp_path: Path,
) -> None:
    """Filesystem paths should use the same uploaded-file reader."""

    file_path = tmp_path / "bank_statement.csv"
    file_path.write_bytes(build_csv_bytes())

    result = BankStatementFileReader().read_path(file_path)

    assert result.file_name == "bank_statement.csv"
    assert result.file_format is BankStatementFileFormat.CSV
    assert result.row_count == 2


@pytest.mark.unit
def test_reader_reads_xlsx_from_path(
    tmp_path: Path,
) -> None:
    """XLSX filesystem paths should preserve worksheet metadata."""

    file_path = tmp_path / "bank_statement.xlsx"
    file_path.write_bytes(build_xlsx_bytes())

    result = BankStatementFileReader().read_path(file_path)

    assert result.file_name == "bank_statement.xlsx"
    assert result.file_format is BankStatementFileFormat.XLSX
    assert result.sheet_name == "Transactions"
    assert result.row_count == 2


@pytest.mark.unit
def test_uppercase_extension_is_supported(
    tmp_path: Path,
) -> None:
    """File-extension detection should be case-insensitive."""

    file_path = tmp_path / "BANK_STATEMENT.CSV"
    file_path.write_bytes(build_csv_bytes())

    result = BankStatementFileReader().read_path(file_path)

    assert result.file_format is BankStatementFileFormat.CSV


@pytest.mark.unit
def test_missing_file_path_is_rejected(
    tmp_path: Path,
) -> None:
    """A path must point to an existing file."""

    missing_path = tmp_path / "missing.csv"

    with pytest.raises(
        BankStatementReadError,
        match="File does not exist",
    ):
        BankStatementFileReader().read_path(missing_path)


@pytest.mark.unit
def test_directory_path_is_rejected(
    tmp_path: Path,
) -> None:
    """A directory cannot be processed as a bank statement."""

    with pytest.raises(
        BankStatementReadError,
        match="Path is not a file",
    ):
        BankStatementFileReader().read_path(tmp_path)


@pytest.mark.unit
def test_blank_file_path_is_rejected() -> None:
    """Blank filesystem paths should produce a configuration error."""

    with pytest.raises(
        BankStatementReadError,
        match="file_path cannot be empty",
    ):
        BankStatementFileReader().read_path("   ")


@pytest.mark.unit
def test_invalid_file_path_type_is_rejected() -> None:
    """Filesystem paths must be strings or pathlib Path objects."""

    invalid_path = cast(str, 123)

    with pytest.raises(
        BankStatementReadError,
        match=r"file_path must be a string or pathlib\.Path",
    ):
        BankStatementFileReader().read_path(invalid_path)


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_file_name",
    [
        "",
        "   ",
        "folder/bank_statement.csv",
        "folder\\bank_statement.csv",
    ],
)
def test_invalid_uploaded_file_name_is_rejected(
    invalid_file_name: str,
) -> None:
    """Uploaded filenames must not contain directory components."""

    with pytest.raises(BankStatementReadError):
        BankStatementFileReader().read_bytes(
            build_csv_bytes(),
            file_name=invalid_file_name,
        )


@pytest.mark.unit
def test_non_string_uploaded_file_name_is_rejected() -> None:
    """Uploaded filename metadata must be text."""

    invalid_file_name = cast(str, 123)

    with pytest.raises(
        BankStatementReadError,
        match="file_name must be a string",
    ):
        BankStatementFileReader().read_bytes(
            build_csv_bytes(),
            file_name=invalid_file_name,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "unsupported_file_name",
    [
        "bank_statement.xls",
        "bank_statement.pdf",
        "bank_statement.txt",
        "bank_statement",
    ],
)
def test_unsupported_file_extension_is_rejected(
    unsupported_file_name: str,
) -> None:
    """Only explicitly supported financial source formats are accepted."""

    with pytest.raises(
        BankStatementReadError,
        match="Unsupported file extension",
    ):
        BankStatementFileReader().read_bytes(
            build_csv_bytes(),
            file_name=unsupported_file_name,
        )


@pytest.mark.unit
def test_empty_uploaded_content_is_rejected() -> None:
    """A zero-byte uploaded document cannot contain transactions."""

    with pytest.raises(
        BankStatementReadError,
        match="Uploaded file content cannot be empty",
    ):
        BankStatementFileReader().read_bytes(
            b"",
            file_name="bank_statement.csv",
        )


@pytest.mark.unit
def test_non_binary_uploaded_content_is_rejected() -> None:
    """Uploaded file content must be represented as bytes."""

    invalid_content = cast(bytes, "not binary content")

    with pytest.raises(
        BankStatementReadError,
        match="content must be bytes or bytearray",
    ):
        BankStatementFileReader().read_bytes(
            invalid_content,
            file_name="bank_statement.csv",
        )


@pytest.mark.unit
def test_whitespace_only_csv_is_rejected() -> None:
    """A non-zero file without CSV data should fail parsing."""

    with pytest.raises(
        BankStatementReadError,
        match="Could not parse CSV file",
    ):
        BankStatementFileReader().read_bytes(
            b"   \n",
            file_name="bank_statement.csv",
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_header_row_number",
    [
        0,
        -1,
    ],
)
def test_non_positive_header_row_number_is_rejected(
    invalid_header_row_number: int,
) -> None:
    """Source header rows use one-based positive numbering."""

    with pytest.raises(
        BankStatementReadError,
        match="greater than or equal to 1",
    ):
        BankStatementFileReader().read_bytes(
            build_csv_bytes(),
            file_name="bank_statement.csv",
            header_row_number=invalid_header_row_number,
        )


@pytest.mark.unit
def test_boolean_header_row_number_is_rejected() -> None:
    """Boolean values must not be treated as header-row integers."""

    with pytest.raises(
        BankStatementReadError,
        match="header_row_number must be an integer",
    ):
        BankStatementFileReader().read_bytes(
            build_csv_bytes(),
            file_name="bank_statement.csv",
            header_row_number=True,
        )


@pytest.mark.unit
def test_non_integer_header_row_number_is_rejected() -> None:
    """Fractional row positions are not valid source metadata."""

    invalid_header_row_number = cast(int, 1.5)

    with pytest.raises(
        BankStatementReadError,
        match="header_row_number must be an integer",
    ):
        BankStatementFileReader().read_bytes(
            build_csv_bytes(),
            file_name="bank_statement.csv",
            header_row_number=invalid_header_row_number,
        )


@pytest.mark.unit
def test_blank_csv_encoding_is_rejected() -> None:
    """CSV encoding configuration cannot be blank."""

    with pytest.raises(
        BankStatementReadError,
        match="csv_encoding cannot be empty",
    ):
        BankStatementFileReader().read_bytes(
            build_csv_bytes(),
            file_name="bank_statement.csv",
            csv_encoding="   ",
        )


@pytest.mark.unit
def test_non_string_csv_encoding_is_rejected() -> None:
    """CSV encoding configuration must contain text."""

    invalid_encoding = cast(str, 123)

    with pytest.raises(
        BankStatementReadError,
        match="csv_encoding must be a string",
    ):
        BankStatementFileReader().read_bytes(
            build_csv_bytes(),
            file_name="bank_statement.csv",
            csv_encoding=invalid_encoding,
        )


@pytest.mark.unit
def test_file_size_limit_is_enforced() -> None:
    """Uploaded files larger than the configured limit must be rejected."""

    reader = BankStatementFileReader(max_file_size_bytes=10)

    with pytest.raises(
        BankStatementReadError,
        match="exceeds the maximum permitted size",
    ):
        reader.read_bytes(
            build_csv_bytes(),
            file_name="bank_statement.csv",
        )


@pytest.mark.unit
def test_file_size_limit_can_be_disabled() -> None:
    """None disables the configurable file-size limit."""

    reader = BankStatementFileReader(max_file_size_bytes=None)

    result = reader.read_bytes(
        build_csv_bytes(),
        file_name="bank_statement.csv",
    )

    assert result.row_count == 2


@pytest.mark.unit
def test_boolean_maximum_file_size_is_rejected() -> None:
    """Boolean values must not be treated as byte limits."""

    with pytest.raises(
        BankStatementReadError,
        match="max_file_size_bytes must be an integer or None",
    ):
        BankStatementFileReader(max_file_size_bytes=True)


@pytest.mark.unit
def test_non_integer_maximum_file_size_is_rejected() -> None:
    """File-size limits must be represented by whole bytes."""

    invalid_size = cast(int, 10.5)

    with pytest.raises(
        BankStatementReadError,
        match="max_file_size_bytes must be an integer or None",
    ):
        BankStatementFileReader(max_file_size_bytes=invalid_size)


@pytest.mark.unit
def test_non_positive_maximum_file_size_is_rejected() -> None:
    """Configured maximum file size must be greater than zero."""

    with pytest.raises(
        BankStatementReadError,
        match="max_file_size_bytes must be greater than zero",
    ):
        BankStatementFileReader(max_file_size_bytes=0)
