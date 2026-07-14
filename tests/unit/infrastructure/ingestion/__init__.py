"""
Public interface for BSI bank-statement ingestion infrastructure.

Other BSI modules should import approved ingestion components from this
package instead of importing directly from internal implementation files.

Preferred:

    from bsi.infrastructure.ingestion import BankStatementIngestor

Avoid:

    from bsi.infrastructure.ingestion.ingestor import (
        BankStatementIngestor,
    )
"""

from bsi.infrastructure.ingestion.dataframe_adapter import (
    BankStatementDataFrameAdapter,
    DataFrameAdaptationError,
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
    BankStatementRecord,
    BankStatementRowAdapter,
    DateInput,
    RowAdaptationError,
    parse_transaction_date,
)

__all__ = [
    "BankStatementColumnMap",
    "BankStatementDataFrameAdapter",
    "BankStatementFileFormat",
    "BankStatementFileReader",
    "BankStatementIngestionInvariantError",
    "BankStatementIngestionResult",
    "BankStatementIngestor",
    "BankStatementReadError",
    "BankStatementReadResult",
    "BankStatementRecord",
    "BankStatementRowAdapter",
    "DataFrameAdaptationError",
    "DataFrameAdaptationResult",
    "DateInput",
    "RowAdaptationError",
    "RowAdaptationFailure",
    "parse_transaction_date",
]
