"""
Pandas DataFrame adapter for BSI bank-statement ingestion.

This module converts an entire bank-statement DataFrame into validated
NormalizedTransaction domain objects.

Responsibilities:

- Validate the DataFrame and its configured columns.
- Preserve original source-file row numbers.
- Process every row independently.
- Collect valid transactions.
- Collect row-level failures without stopping the entire file.
- Preserve document, processing-run, and organizational context.

This module belongs to infrastructure because it depends on Pandas and
external bank-statement formats.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import cast
from uuid import UUID

import pandas as pd

from bsi.domain.transactions import (
    NormalizedTransaction,
    TransactionContext,
)
from bsi.infrastructure.ingestion.row_adapter import (
    BankStatementRecord,
    BankStatementRowAdapter,
    RowAdaptationError,
)

_SUCCESS_RATE_QUANTUM = Decimal("0.01")
_ZERO_PERCENTAGE = Decimal("0.00")
_ONE_HUNDRED_PERCENT = Decimal("100.00")


class DataFrameAdaptationError(ValueError):
    """Raised when a complete DataFrame cannot be processed safely."""


@dataclass(frozen=True, slots=True)
class RowAdaptationFailure:
    """
    Record of one bank-statement row that could not be normalized.

    Attributes
    ----------
    file_name:
        Original uploaded source filename.

    source_row_number:
        One-based row number in the original source document.

    message:
        Human-readable validation or adaptation error.
    """

    file_name: str
    source_row_number: int
    message: str


@dataclass(frozen=True, slots=True)
class DataFrameAdaptationResult:
    """
    Result of adapting one complete bank-statement DataFrame.

    Valid transactions and failed rows are kept separately so that one
    invalid source record does not discard valid records from the same
    file.

    Attributes
    ----------
    file_name:
        Original uploaded source filename.

    transactions:
        Successfully normalized transactions.

    failures:
        Rows that could not be normalized.
    """

    file_name: str
    transactions: tuple[NormalizedTransaction, ...]
    failures: tuple[RowAdaptationFailure, ...]

    @property
    def total_rows(self) -> int:
        """Return the number of source rows that were processed."""

        return self.successful_rows + self.failed_rows

    @property
    def successful_rows(self) -> int:
        """Return the number of successfully normalized rows."""

        return len(self.transactions)

    @property
    def failed_rows(self) -> int:
        """Return the number of rows that failed normalization."""

        return len(self.failures)

    @property
    def has_failures(self) -> bool:
        """Return whether at least one source row failed."""

        return bool(self.failures)

    @property
    def all_rows_succeeded(self) -> bool:
        """
        Return whether all processed rows succeeded.

        An empty DataFrame returns False because no transactions were
        successfully processed.
        """

        return self.total_rows > 0 and not self.has_failures

    @property
    def success_rate(self) -> Decimal:
        """
        Return the successful-row percentage.

        This operational metric uses Decimal for deterministic output,
        although it is not itself a financial amount.
        """

        if self.total_rows == 0:
            return _ZERO_PERCENTAGE

        successful_percentage = (
            Decimal(self.successful_rows)
            / Decimal(self.total_rows)
            * _ONE_HUNDRED_PERCENT
        )

        return successful_percentage.quantize(
            _SUCCESS_RATE_QUANTUM,
            rounding=ROUND_HALF_UP,
        )


@dataclass(frozen=True, slots=True)
class BankStatementDataFrameAdapter:
    """
    Convert complete Pandas DataFrames into normalized transactions.

    Attributes
    ----------
    row_adapter:
        Adapter used to convert each individual source record.
    """

    row_adapter: BankStatementRowAdapter = field(
        default_factory=BankStatementRowAdapter
    )

    def adapt(
        self,
        dataframe: pd.DataFrame,
        *,
        file_name: str,
        first_data_row_number: int = 2,
        sheet_name: str | None = None,
        context: TransactionContext | None = None,
        source_document_id: UUID | None = None,
        processing_run_id: UUID | None = None,
    ) -> DataFrameAdaptationResult:
        """
        Convert an entire bank-statement DataFrame.

        Parameters
        ----------
        dataframe:
            Pandas DataFrame containing normalized bank-statement
            headers and source values.

        file_name:
            Original uploaded filename without a directory path.

        first_data_row_number:
            Original file row number represented by DataFrame position
            zero.

            Examples:

            - CSV with headers on row 1:
              first data row is 2.

            - Excel workbook loaded with ``header=1``:
              headers are on Excel row 2 and the first data row is 3.

        sheet_name:
            Excel worksheet name when applicable.

        context:
            Company, brand, store, and bank-account context shared by
            the file.

        source_document_id:
            Persistent uploaded-document identifier.

        processing_run_id:
            Processing-run identifier.

        Returns
        -------
        DataFrameAdaptationResult
            Successfully normalized transactions and row-level failures.

        Raises
        ------
        DataFrameAdaptationError
            If the DataFrame, file metadata, or source-column structure
            is invalid.
        """

        self._validate_inputs(
            dataframe=dataframe,
            file_name=file_name,
            first_data_row_number=first_data_row_number,
        )

        normalized_file_name = file_name.strip()

        transactions: list[NormalizedTransaction] = []
        failures: list[RowAdaptationFailure] = []

        for position, (_, pandas_row) in enumerate(dataframe.iterrows()):
            source_row_number = first_data_row_number + position

            record = cast(
                BankStatementRecord,
                pandas_row.to_dict(),
            )

            try:
                transaction = self.row_adapter.adapt(
                    record,
                    file_name=normalized_file_name,
                    source_row_number=source_row_number,
                    sheet_name=sheet_name,
                    context=context,
                    source_document_id=source_document_id,
                    processing_run_id=processing_run_id,
                )
            except RowAdaptationError as error:
                failures.append(
                    RowAdaptationFailure(
                        file_name=normalized_file_name,
                        source_row_number=source_row_number,
                        message=str(error),
                    )
                )
                continue

            transactions.append(transaction)

        return DataFrameAdaptationResult(
            file_name=normalized_file_name,
            transactions=tuple(transactions),
            failures=tuple(failures),
        )

    def _validate_inputs(
        self,
        *,
        dataframe: pd.DataFrame,
        file_name: str,
        first_data_row_number: int,
    ) -> None:
        """Validate file-level DataFrame adaptation inputs."""

        if not isinstance(dataframe, pd.DataFrame):
            raise DataFrameAdaptationError("dataframe must be a pandas DataFrame.")

        _validate_file_name(file_name)
        _validate_first_data_row_number(first_data_row_number)

        self._validate_dataframe_columns(dataframe)

    def _validate_dataframe_columns(
        self,
        dataframe: pd.DataFrame,
    ) -> None:
        """
        Validate required and optional source-column definitions.

        Validation occurs once per file rather than producing the same
        missing-column error for every source row.
        """

        dataframe_columns = list(dataframe.columns)

        required_columns = (
            self.row_adapter.columns.transaction_date,
            self.row_adapter.columns.description,
            self.row_adapter.columns.payment,
            self.row_adapter.columns.deposit,
        )

        missing_columns = [
            column_name
            for column_name in required_columns
            if dataframe_columns.count(column_name) == 0
        ]

        if missing_columns:
            formatted_columns = ", ".join(
                repr(column_name) for column_name in missing_columns
            )

            raise DataFrameAdaptationError(
                "DataFrame is missing required bank-statement "
                f"columns: {formatted_columns}."
            )

        duplicate_required_columns = [
            column_name
            for column_name in required_columns
            if dataframe_columns.count(column_name) > 1
        ]

        if duplicate_required_columns:
            formatted_columns = ", ".join(
                repr(column_name) for column_name in duplicate_required_columns
            )

            raise DataFrameAdaptationError(
                f"DataFrame contains duplicate required columns: {formatted_columns}."
            )

        memo_column = self.row_adapter.columns.memo

        if memo_column is not None and dataframe_columns.count(memo_column) > 1:
            raise DataFrameAdaptationError(
                f"DataFrame contains a duplicate memo column: {memo_column!r}."
            )


def _validate_file_name(file_name: str) -> None:
    """Validate source filename metadata before row processing."""

    if not isinstance(file_name, str):
        raise DataFrameAdaptationError("file_name must be a string.")

    normalized_file_name = file_name.strip()

    if not normalized_file_name:
        raise DataFrameAdaptationError("file_name cannot be empty.")

    if "/" in normalized_file_name or "\\" in normalized_file_name:
        raise DataFrameAdaptationError(
            "file_name must not contain directory-path components."
        )


def _validate_first_data_row_number(
    first_data_row_number: int,
) -> None:
    """Validate the original row number represented by DataFrame row zero."""

    if isinstance(first_data_row_number, bool) or not isinstance(
        first_data_row_number, int
    ):
        raise DataFrameAdaptationError("first_data_row_number must be an integer.")

    if first_data_row_number < 1:
        raise DataFrameAdaptationError(
            "first_data_row_number must be greater than or equal to 1."
        )
