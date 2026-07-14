"""
High-level bank-statement ingestion facade for BSI.

This module coordinates the infrastructure components responsible for:

- Reading CSV or XLSX source documents
- Creating Pandas DataFrames
- Converting DataFrame rows into normalized domain transactions
- Preserving file, worksheet, document, processing-run, and row lineage
- Returning successful transactions and row-level failures together

This facade does not apply financial rules, perform GL mapping, generate
reports, or persist records. Those responsibilities belong to later
application and domain services.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from bsi.domain.transactions import (
    NormalizedTransaction,
    TransactionContext,
)
from bsi.infrastructure.ingestion.dataframe_adapter import (
    BankStatementDataFrameAdapter,
    DataFrameAdaptationResult,
    RowAdaptationFailure,
)
from bsi.infrastructure.ingestion.file_reader import (
    BankStatementFileFormat,
    BankStatementFileReader,
    BankStatementReadResult,
)


class BankStatementIngestionInvariantError(RuntimeError):
    """
    Raised when internal ingestion results contradict each other.

    This indicates a programming or integration defect rather than an
    invalid user-uploaded row.
    """


@dataclass(frozen=True, slots=True)
class BankStatementIngestionResult:
    """
    Complete result of reading and normalizing one bank statement.

    Attributes
    ----------
    read_result:
        File-reading result containing source metadata and the parsed
        Pandas DataFrame.

    adaptation_result:
        DataFrame-adaptation result containing normalized transactions
        and row-level failures.
    """

    read_result: BankStatementReadResult
    adaptation_result: DataFrameAdaptationResult

    def __post_init__(self) -> None:
        """Verify that both ingestion stages describe the same file."""

        if self.read_result.file_name != self.adaptation_result.file_name:
            raise BankStatementIngestionInvariantError(
                "File-reader and DataFrame-adapter results contain different filenames."
            )

        if self.read_result.row_count != self.adaptation_result.total_rows:
            raise BankStatementIngestionInvariantError(
                "File-reader row count does not match the number of "
                "successfully or unsuccessfully adapted rows."
            )

    @property
    def file_name(self) -> str:
        """Return the normalized uploaded filename."""

        return self.read_result.file_name

    @property
    def file_format(self) -> BankStatementFileFormat:
        """Return the detected external file format."""

        return self.read_result.file_format

    @property
    def sheet_name(self) -> str | None:
        """Return the resolved Excel worksheet name."""

        return self.read_result.sheet_name

    @property
    def header_row_number(self) -> int:
        """Return the one-based source header-row number."""

        return self.read_result.header_row_number

    @property
    def first_data_row_number(self) -> int:
        """Return the one-based row number of the first source record."""

        return self.read_result.first_data_row_number

    @property
    def source_row_count(self) -> int:
        """Return the number of rows parsed from the source file."""

        return self.read_result.row_count

    @property
    def source_column_count(self) -> int:
        """Return the number of columns parsed from the source file."""

        return self.read_result.column_count

    @property
    def transactions(self) -> tuple[NormalizedTransaction, ...]:
        """Return successfully normalized transactions."""

        return self.adaptation_result.transactions

    @property
    def failures(self) -> tuple[RowAdaptationFailure, ...]:
        """Return rows that could not be normalized."""

        return self.adaptation_result.failures

    @property
    def successful_rows(self) -> int:
        """Return the number of normalized transactions."""

        return self.adaptation_result.successful_rows

    @property
    def failed_rows(self) -> int:
        """Return the number of row-level failures."""

        return self.adaptation_result.failed_rows

    @property
    def has_failures(self) -> bool:
        """Return whether at least one source row failed."""

        return self.adaptation_result.has_failures

    @property
    def all_rows_succeeded(self) -> bool:
        """Return whether every non-empty source row succeeded."""

        return self.adaptation_result.all_rows_succeeded

    @property
    def success_rate(self) -> Decimal:
        """Return the successful-row percentage."""

        return self.adaptation_result.success_rate

    @property
    def is_empty(self) -> bool:
        """Return whether the source file contains no data rows."""

        return self.read_result.is_empty


@dataclass(frozen=True, slots=True)
class BankStatementIngestor:
    """
    Read and normalize complete bank-statement documents.

    Dependencies are injected so they can be configured or replaced
    during testing.

    Attributes
    ----------
    file_reader:
        Infrastructure component responsible for CSV and XLSX parsing.

    dataframe_adapter:
        Infrastructure component responsible for converting DataFrame
        rows into transaction-domain objects.
    """

    file_reader: BankStatementFileReader = field(
        default_factory=BankStatementFileReader
    )
    dataframe_adapter: BankStatementDataFrameAdapter = field(
        default_factory=BankStatementDataFrameAdapter
    )

    def ingest_bytes(
        self,
        content: bytes | bytearray,
        *,
        file_name: str,
        header_row_number: int = 1,
        sheet_name: str | int | None = None,
        csv_encoding: str = "utf-8-sig",
        context: TransactionContext | None = None,
        source_document_id: UUID | None = None,
        processing_run_id: UUID | None = None,
    ) -> BankStatementIngestionResult:
        """
        Read and normalize an uploaded bank statement.

        Parameters
        ----------
        content:
            Uploaded CSV or XLSX binary content.

        file_name:
            Original uploaded filename.

        header_row_number:
            One-based row containing source column headers.

        sheet_name:
            Excel worksheet name or zero-based worksheet position.

        csv_encoding:
            Character encoding used for CSV files.

        context:
            Company, brand, store, and bank-account context shared by
            all source rows.

        source_document_id:
            Persistent identifier for the uploaded document.

        processing_run_id:
            Identifier for the current processing run.

        Returns
        -------
        BankStatementIngestionResult
            File metadata, normalized transactions, row failures, and
            ingestion metrics.
        """

        read_result = self.file_reader.read_bytes(
            content,
            file_name=file_name,
            header_row_number=header_row_number,
            sheet_name=sheet_name,
            csv_encoding=csv_encoding,
        )

        return self._adapt_read_result(
            read_result,
            context=context,
            source_document_id=source_document_id,
            processing_run_id=processing_run_id,
        )

    def ingest_path(
        self,
        file_path: str | Path,
        *,
        header_row_number: int = 1,
        sheet_name: str | int | None = None,
        csv_encoding: str = "utf-8-sig",
        context: TransactionContext | None = None,
        source_document_id: UUID | None = None,
        processing_run_id: UUID | None = None,
    ) -> BankStatementIngestionResult:
        """
        Read and normalize a bank statement from a filesystem path.

        This method is useful for command-line processing, local
        development, migration scripts, and batch jobs.
        """

        read_result = self.file_reader.read_path(
            file_path,
            header_row_number=header_row_number,
            sheet_name=sheet_name,
            csv_encoding=csv_encoding,
        )

        return self._adapt_read_result(
            read_result,
            context=context,
            source_document_id=source_document_id,
            processing_run_id=processing_run_id,
        )

    def _adapt_read_result(
        self,
        read_result: BankStatementReadResult,
        *,
        context: TransactionContext | None,
        source_document_id: UUID | None,
        processing_run_id: UUID | None,
    ) -> BankStatementIngestionResult:
        """Convert a parsed DataFrame into transaction-domain objects."""

        adaptation_result = self.dataframe_adapter.adapt(
            read_result.dataframe,
            file_name=read_result.file_name,
            first_data_row_number=(read_result.first_data_row_number),
            sheet_name=read_result.sheet_name,
            context=context,
            source_document_id=source_document_id,
            processing_run_id=processing_run_id,
        )

        return BankStatementIngestionResult(
            read_result=read_result,
            adaptation_result=adaptation_result,
        )
