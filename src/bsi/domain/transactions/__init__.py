"""
Public interface for the BSI transaction domain.

Other BSI modules should import approved transaction-domain objects from
this package rather than depending directly on internal module paths.

Preferred:

    from bsi.domain.transactions import NormalizedTransaction

Avoid:

    from bsi.domain.transactions.models import NormalizedTransaction
"""

from bsi.domain.transactions.amounts import (
    AmountInput,
    AmountValidationError,
    TransactionAmounts,
    parse_money,
)
from bsi.domain.transactions.enums import TransactionDirection
from bsi.domain.transactions.models import (
    NormalizedTransaction,
    TransactionContext,
    TransactionSource,
    TransactionValidationError,
    normalize_search_text,
)

__all__ = [
    "AmountInput",
    "AmountValidationError",
    "NormalizedTransaction",
    "TransactionAmounts",
    "TransactionContext",
    "TransactionDirection",
    "TransactionSource",
    "TransactionValidationError",
    "normalize_search_text",
    "parse_money",
]
