"""
Excel and CSV file reader for BSI bank-statement ingestion.

This module converts uploaded bank-statement file content into a Pandas
DataFrame. It does not create domain transactions or perform accounting
logic.

Responsibilities:

- Support CSV and XLSX bank-statement files.
- Read files from filesystem paths or uploaded bytes.
- Validate filenames, file extensions, file sizes, and header positions.
- Resolve Excel worksheet names safely.
- Normalize source column labels.
- Preserve metadata needed for source-row lineage.
- Convert parser errors into clear ingestion errors.
"""

from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile

import pandas as pd

_DEFAULT_MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
_SUPPORTED_FILE_EXTENSIONS = {".csv", ".xlsx"}


class BankStatementReadError(ValueError):
    """Raised when a bank-statement file cannot be read safely."""


class BankStatementFileFormat(StrEnum):
    """Supported external bank-statement file formats."""

    CSV = "csv"
    XLSX = "xlsx"


@dataclass(slots=True)
class BankStatementReadResult:
    """
    Result of reading one bank-statement file.

    Attributes
    ----------
    file_name:
        Original uploaded filename without directory components.

    file_format:
        Detected supported source format.

    dataframe:
        Parsed Pandas DataFrame containing source rows.

    header_row_number:
        One-based row number containing source column headers.

    first_data_row_number:
        One-based source row number represented by DataFrame position zero.

    sheet_name:
        Resolved Excel worksheet name. CSV files use None.
    """

    file_name: str
    file_format: BankStatementFileFormat
    dataframe: pd.DataFrame
    header_row_number: int
    first_data_row_number: int
    sheet_name: str | None

    @property
    def row_count(self) -> int:
        """Return the number of parsed data rows."""

        return len(self.dataframe)

    @property
    def column_count(self) -> int:
        """Return the number of parsed source columns."""

        return len(self.dataframe.columns)

    @property
    def is_empty(self) -> bool:
        """Return whether the file contains no data rows."""

        return bool(self.dataframe.empty)


@dataclass(frozen=True, slots=True)
class BankStatementFileReader:
    """
    Read supported bank-statement documents into Pandas DataFrames.

    Attributes
    ----------
    max_file_size_bytes:
        Maximum accepted uploaded file size.

        Set this to None to disable the size limit.
    """

    max_file_size_bytes: int | None = _DEFAULT_MAX_FILE_SIZE_BYTES

    def __post_init__(self) -> None:
        """Validate file-reader configuration."""

        if self.max_file_size_bytes is None:
            return

        if isinstance(self.max_file_size_bytes, bool) or not isinstance(
            self.max_file_size_bytes, int
        ):
            raise BankStatementReadError(
                "max_file_size_bytes must be an integer or None."
            )

        if self.max_file_size_bytes < 1:
            raise BankStatementReadError(
                "max_file_size_bytes must be greater than zero."
            )

    def read_path(
        self,
        file_path: str | Path,
        *,
        header_row_number: int = 1,
        sheet_name: str | int | None = None,
        csv_encoding: str = "utf-8-sig",
    ) -> BankStatementReadResult:
        """
        Read a bank statement from a filesystem path.

        Parameters
        ----------
        file_path:
            Path to an existing CSV or XLSX file.

        header_row_number:
            One-based source row containing column headers.

            Examples:

            - Headers on row 1: ``header_row_number=1``
            - Headers on row 2: ``header_row_number=2``

        sheet_name:
            Excel worksheet name or zero-based worksheet position.

            None selects the first worksheet. CSV files do not support
            worksheet selection.

        csv_encoding:
            Character encoding used to read CSV files.

        Returns
        -------
        BankStatementReadResult
            Parsed DataFrame and source metadata.
        """

        normalized_path = _validate_file_path(file_path)

        try:
            file_size = normalized_path.stat().st_size
        except OSError as error:
            raise BankStatementReadError(
                f"Could not inspect file {normalized_path.name!r}: {error}"
            ) from error

        self._validate_file_size(
            file_size=file_size,
            file_name=normalized_path.name,
        )

        try:
            content = normalized_path.read_bytes()
        except OSError as error:
            raise BankStatementReadError(
                f"Could not read file {normalized_path.name!r}: {error}"
            ) from error

        return self.read_bytes(
            content,
            file_name=normalized_path.name,
            header_row_number=header_row_number,
            sheet_name=sheet_name,
            csv_encoding=csv_encoding,
        )

    def read_bytes(
        self,
        content: bytes | bytearray,
        *,
        file_name: str,
        header_row_number: int = 1,
        sheet_name: str | int | None = None,
        csv_encoding: str = "utf-8-sig",
    ) -> BankStatementReadResult:
        """
        Read bank-statement content supplied as uploaded bytes.

        This method will later be used by FastAPI and Streamlit upload
        handlers.

        Parameters
        ----------
        content:
            Uploaded binary file content.

        file_name:
            Original uploaded filename.

        header_row_number:
            One-based source row containing column headers.

        sheet_name:
            Excel worksheet name or zero-based worksheet position.

        csv_encoding:
            Character encoding used to decode CSV files.

        Returns
        -------
        BankStatementReadResult
            Parsed DataFrame and source metadata.
        """

        normalized_file_name = _validate_file_name(file_name)
        normalized_content = _validate_file_content(content)
        normalized_header_row = _validate_header_row_number(header_row_number)
        normalized_encoding = _validate_csv_encoding(csv_encoding)

        self._validate_file_size(
            file_size=len(normalized_content),
            file_name=normalized_file_name,
        )

        file_format = _detect_file_format(normalized_file_name)
        pandas_header_index = normalized_header_row - 1

        if file_format is BankStatementFileFormat.CSV:
            if sheet_name is not None:
                raise BankStatementReadError(
                    "sheet_name cannot be used with a CSV file."
                )

            dataframe = _read_csv(
                normalized_content,
                header_index=pandas_header_index,
                encoding=normalized_encoding,
                file_name=normalized_file_name,
            )
            resolved_sheet_name = None
        else:
            dataframe, resolved_sheet_name = _read_excel(
                normalized_content,
                header_index=pandas_header_index,
                requested_sheet_name=sheet_name,
                file_name=normalized_file_name,
            )

        dataframe = _normalize_dataframe_columns(
            dataframe,
            file_name=normalized_file_name,
        )

        return BankStatementReadResult(
            file_name=normalized_file_name,
            file_format=file_format,
            dataframe=dataframe,
            header_row_number=normalized_header_row,
            first_data_row_number=normalized_header_row + 1,
            sheet_name=resolved_sheet_name,
        )

    def _validate_file_size(
        self,
        *,
        file_size: int,
        file_name: str,
    ) -> None:
        """Validate a source document against the configured size limit."""

        if file_size < 1:
            raise BankStatementReadError(f"File {file_name!r} is empty.")

        if (
            self.max_file_size_bytes is not None
            and file_size > self.max_file_size_bytes
        ):
            raise BankStatementReadError(
                f"File {file_name!r} exceeds the maximum permitted "
                f"size of {self.max_file_size_bytes} bytes."
            )


def _validate_file_path(file_path: str | Path) -> Path:
    """Validate and normalize a filesystem source path."""

    if isinstance(file_path, str) and not file_path.strip():
        raise BankStatementReadError("file_path cannot be empty.")

    if not isinstance(file_path, (str, Path)):
        raise BankStatementReadError("file_path must be a string or pathlib.Path.")

    normalized_path = Path(file_path).expanduser()

    if not normalized_path.exists():
        raise BankStatementReadError(f"File does not exist: {normalized_path}.")

    if not normalized_path.is_file():
        raise BankStatementReadError(f"Path is not a file: {normalized_path}.")

    return normalized_path


def _validate_file_name(file_name: str) -> str:
    """Validate an uploaded filename without allowing path components."""

    if not isinstance(file_name, str):
        raise BankStatementReadError("file_name must be a string.")

    normalized_file_name = file_name.strip()

    if not normalized_file_name:
        raise BankStatementReadError("file_name cannot be empty.")

    if "/" in normalized_file_name or "\\" in normalized_file_name:
        raise BankStatementReadError(
            "file_name must not contain directory-path components."
        )

    return normalized_file_name


def _validate_file_content(
    content: bytes | bytearray,
) -> bytes:
    """Validate uploaded binary content."""

    if not isinstance(content, (bytes, bytearray)):
        raise BankStatementReadError("content must be bytes or bytearray.")

    normalized_content = bytes(content)

    if not normalized_content:
        raise BankStatementReadError("Uploaded file content cannot be empty.")

    return normalized_content


def _validate_header_row_number(
    header_row_number: int,
) -> int:
    """Validate a one-based source header row number."""

    if isinstance(header_row_number, bool) or not isinstance(header_row_number, int):
        raise BankStatementReadError("header_row_number must be an integer.")

    if header_row_number < 1:
        raise BankStatementReadError(
            "header_row_number must be greater than or equal to 1."
        )

    return header_row_number


def _validate_csv_encoding(csv_encoding: str) -> str:
    """Validate CSV character-encoding configuration."""

    if not isinstance(csv_encoding, str):
        raise BankStatementReadError("csv_encoding must be a string.")

    normalized_encoding = csv_encoding.strip()

    if not normalized_encoding:
        raise BankStatementReadError("csv_encoding cannot be empty.")

    return normalized_encoding


def _detect_file_format(
    file_name: str,
) -> BankStatementFileFormat:
    """Detect a supported file format from its filename extension."""

    extension = Path(file_name).suffix.lower()

    if extension not in _SUPPORTED_FILE_EXTENSIONS:
        supported_extensions = ", ".join(sorted(_SUPPORTED_FILE_EXTENSIONS))

        raise BankStatementReadError(
            f"Unsupported file extension {extension or '<none>'!r}. "
            f"Supported extensions are: {supported_extensions}."
        )

    if extension == ".csv":
        return BankStatementFileFormat.CSV

    return BankStatementFileFormat.XLSX


def _read_csv(
    content: bytes,
    *,
    header_index: int,
    encoding: str,
    file_name: str,
) -> pd.DataFrame:
    """Read CSV content into a DataFrame."""

    buffer = BytesIO(content)

    try:
        return pd.read_csv(
            buffer,
            header=header_index,
            encoding=encoding,
            dtype=object,
        )
    except (
        UnicodeDecodeError,
        pd.errors.EmptyDataError,
        pd.errors.ParserError,
        ValueError,
    ) as error:
        raise BankStatementReadError(
            f"Could not parse CSV file {file_name!r}: {error}"
        ) from error


def _read_excel(
    content: bytes,
    *,
    header_index: int,
    requested_sheet_name: str | int | None,
    file_name: str,
) -> tuple[pd.DataFrame, str]:
    """Read XLSX content and resolve the selected worksheet."""

    buffer = BytesIO(content)

    try:
        with pd.ExcelFile(
            buffer,
            engine="openpyxl",
        ) as workbook:
            resolved_sheet_name = _resolve_sheet_name(
                workbook.sheet_names,
                requested_sheet_name=requested_sheet_name,
            )

            dataframe = pd.read_excel(
                workbook,
                sheet_name=resolved_sheet_name,
                header=header_index,
                dtype=object,
            )
    except BankStatementReadError:
        raise
    except (
        BadZipFile,
        ImportError,
        OSError,
        ValueError,
    ) as error:
        raise BankStatementReadError(
            f"Could not parse XLSX file {file_name!r}: {error}"
        ) from error

    return dataframe, resolved_sheet_name


def _resolve_sheet_name(
    available_sheet_names: list[str],
    *,
    requested_sheet_name: str | int | None,
) -> str:
    """Resolve an Excel worksheet from a name or zero-based position."""

    if not available_sheet_names:
        raise BankStatementReadError(
            "The XLSX workbook does not contain any worksheets."
        )

    if requested_sheet_name is None:
        return available_sheet_names[0]

    if isinstance(requested_sheet_name, bool):
        raise BankStatementReadError("sheet_name must be a string, integer, or None.")

    if isinstance(requested_sheet_name, int):
        if not 0 <= requested_sheet_name < len(available_sheet_names):
            raise BankStatementReadError(
                f"Worksheet position {requested_sheet_name} is outside "
                f"the available range 0 to "
                f"{len(available_sheet_names) - 1}."
            )

        return available_sheet_names[requested_sheet_name]

    if not isinstance(requested_sheet_name, str):
        raise BankStatementReadError("sheet_name must be a string, integer, or None.")

    normalized_sheet_name = requested_sheet_name.strip()

    if not normalized_sheet_name:
        raise BankStatementReadError("sheet_name cannot be empty.")

    if normalized_sheet_name not in available_sheet_names:
        available_names = ", ".join(repr(name) for name in available_sheet_names)

        raise BankStatementReadError(
            f"Worksheet {normalized_sheet_name!r} was not found. "
            f"Available worksheets: {available_names}."
        )

    return normalized_sheet_name


def _normalize_dataframe_columns(
    dataframe: pd.DataFrame,
    *,
    file_name: str,
) -> pd.DataFrame:
    """
    Normalize source column labels without modifying row values.

    Column whitespace is removed so headers such as ``" Payment "`` become
    ``"Payment"``. Duplicate names created by normalization are rejected
    because they make financial-field selection ambiguous.
    """

    normalized_dataframe = dataframe.copy()
    normalized_columns: list[str] = []

    for raw_column_name in normalized_dataframe.columns:
        normalized_column_name = str(raw_column_name).strip()

        if not normalized_column_name:
            raise BankStatementReadError(
                f"File {file_name!r} contains an empty column name."
            )

        normalized_columns.append(normalized_column_name)

    duplicate_columns = sorted(
        {
            column_name
            for column_name in normalized_columns
            if normalized_columns.count(column_name) > 1
        }
    )

    if duplicate_columns:
        formatted_columns = ", ".join(
            repr(column_name) for column_name in duplicate_columns
        )

        raise BankStatementReadError(
            f"File {file_name!r} contains duplicate columns after "
            f"normalization: {formatted_columns}."
        )

    normalized_dataframe.columns = normalized_columns

    return normalized_dataframe
