"""
Normalized transaction models for BSI — Bank Statement Intelligence.

This module defines framework-independent domain objects representing:

- Original bank transaction information
- Normalized searchable information
- Financial payment and deposit amounts
- Transaction identity
- Company, store, and bank-account context
- Source-document and source-row lineage

The models in this module must not depend on Pandas, FastAPI,
Streamlit, SQLAlchemy, or PostgreSQL.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Self
from uuid import UUID, uuid4

from bsi.domain.transactions.amounts import AmountInput, TransactionAmounts
from bsi.domain.transactions.enums import TransactionDirection


class TransactionValidationError(ValueError):
    """Raised when transaction data cannot enter the BSI domain."""


def normalize_search_text(value: str) -> str:
    """
    Normalize text for deterministic searching and rule matching.

    Normalization performs the following operations:

    - Removes leading and trailing whitespace
    - Converts repeated internal whitespace to one space
    - Uses Unicode-aware lowercase normalization through casefold()

    Parameters
    ----------
    value:
        Text to normalize.

    Returns
    -------
    str
        Normalized lowercase searchable text.

    Raises
    ------
    TypeError
        If the supplied value is not a string.

    Examples
    --------
    >>> normalize_search_text("  ORIG   CO NAME:DoorDash, Inc.  ")
    'orig co name:doordash, inc.'
    """

    if not isinstance(value, str):
        raise TypeError("Search text must be a string.")

    return " ".join(value.strip().casefold().split())


def _clean_required_text(
    value: str,
    *,
    field_name: str,
) -> str:
    """
    Clean a required human-readable text field.

    Unlike searchable text normalization, this function preserves the
    original letter casing for display and audit purposes.
    """

    if not isinstance(value, str):
        raise TransactionValidationError(f"{field_name} must be a string.")

    cleaned_value = " ".join(value.strip().split())

    if not cleaned_value:
        raise TransactionValidationError(f"{field_name} cannot be empty.")

    return cleaned_value


def _clean_optional_text(
    value: str | None,
    *,
    field_name: str,
) -> str | None:
    """
    Clean optional display text.

    Blank optional values are converted to None so that BSI does not
    maintain multiple representations of missing data.
    """

    if value is None:
        return None

    if not isinstance(value, str):
        raise TransactionValidationError(f"{field_name} must be a string or None.")

    cleaned_value = " ".join(value.strip().split())

    if not cleaned_value:
        return None

    return cleaned_value


def _validate_optional_uuid(
    value: UUID | None,
    *,
    field_name: str,
) -> None:
    """Validate an optional UUID domain identifier."""

    if value is not None and not isinstance(value, UUID):
        raise TransactionValidationError(f"{field_name} must be a UUID or None.")


@dataclass(frozen=True, slots=True)
class TransactionSource:
    """
    Source lineage for one normalized bank transaction.

    This object allows a financial result to be traced back to the
    original uploaded document and source row.

    Attributes
    ----------
    file_name:
        Original uploaded filename without a directory path.

    source_row_number:
        Row number in the source file. Row numbering starts at 1.

    sheet_name:
        Excel worksheet name when applicable.

    source_document_id:
        Persistent identifier of the uploaded document.

    processing_run_id:
        Identifier of the processing run that created the transaction.
    """

    file_name: str
    source_row_number: int
    sheet_name: str | None = None
    source_document_id: UUID | None = None
    processing_run_id: UUID | None = None

    def __post_init__(self) -> None:
        """Validate and normalize source-lineage information."""

        normalized_file_name = _clean_required_text(
            self.file_name,
            field_name="file_name",
        )

        if "/" in normalized_file_name or "\\" in normalized_file_name:
            raise TransactionValidationError(
                "file_name must not contain directory-path components."
            )

        if isinstance(self.source_row_number, bool) or not isinstance(
            self.source_row_number, int
        ):
            raise TransactionValidationError("source_row_number must be an integer.")

        if self.source_row_number < 1:
            raise TransactionValidationError(
                "source_row_number must be greater than or equal to 1."
            )

        normalized_sheet_name = _clean_optional_text(
            self.sheet_name,
            field_name="sheet_name",
        )

        _validate_optional_uuid(
            self.source_document_id,
            field_name="source_document_id",
        )
        _validate_optional_uuid(
            self.processing_run_id,
            field_name="processing_run_id",
        )

        object.__setattr__(
            self,
            "file_name",
            normalized_file_name,
        )
        object.__setattr__(
            self,
            "sheet_name",
            normalized_sheet_name,
        )


@dataclass(frozen=True, slots=True)
class TransactionContext:
    """
    Optional organizational and bank-account context.

    UUID identifiers are used instead of business names as authoritative
    relationships. Display names may change, but internal identifiers
    remain stable.

    Attributes
    ----------
    company_id:
        Company that owns the transaction.

    brand_id:
        Restaurant brand associated with the transaction.

    store_id:
        Store or location associated with the transaction.

    bank_account_id:
        Internal BSI bank-account identifier.

    bank_name:
        Display name of the financial institution.

    account_last_four:
        Last four digits of the bank account for safe display.
    """

    company_id: UUID | None = None
    brand_id: UUID | None = None
    store_id: UUID | None = None
    bank_account_id: UUID | None = None
    bank_name: str | None = None
    account_last_four: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalize transaction context."""

        _validate_optional_uuid(
            self.company_id,
            field_name="company_id",
        )
        _validate_optional_uuid(
            self.brand_id,
            field_name="brand_id",
        )
        _validate_optional_uuid(
            self.store_id,
            field_name="store_id",
        )
        _validate_optional_uuid(
            self.bank_account_id,
            field_name="bank_account_id",
        )

        normalized_bank_name = _clean_optional_text(
            self.bank_name,
            field_name="bank_name",
        )

        normalized_last_four = _clean_optional_text(
            self.account_last_four,
            field_name="account_last_four",
        )

        if normalized_last_four is not None and (
            len(normalized_last_four) != 4 or not normalized_last_four.isdigit()
        ):
            raise TransactionValidationError(
                "account_last_four must contain exactly four digits."
            )

        object.__setattr__(
            self,
            "bank_name",
            normalized_bank_name,
        )
        object.__setattr__(
            self,
            "account_last_four",
            normalized_last_four,
        )


@dataclass(frozen=True, slots=True)
class NormalizedTransaction:
    """
    Immutable normalized bank transaction.

    This object represents source transaction facts. It does not contain
    rule matches, GL mappings, COA mappings, or report classifications.
    Those results belong to separate domain objects.

    Attributes
    ----------
    transaction_id:
        Internal unique transaction identifier.

    transaction_date:
        Date shown on the bank statement.

    original_description:
        Original description preserved for audit review.

    normalized_description:
        Lowercase standardized description used for deterministic search.

    amounts:
        Validated payment and deposit amount object.

    source:
        Original source-document and row lineage.

    original_memo:
        Original memo from the bank statement.

    vendor_name:
        Normalized or reviewed vendor display name.

    context:
        Company, brand, store, and bank-account context.
    """

    transaction_id: UUID
    transaction_date: date
    original_description: str
    normalized_description: str
    amounts: TransactionAmounts
    source: TransactionSource
    original_memo: str | None = None
    vendor_name: str | None = None
    context: TransactionContext = field(default_factory=TransactionContext)

    def __post_init__(self) -> None:
        """Validate and normalize the complete transaction."""

        if not isinstance(self.transaction_id, UUID):
            raise TransactionValidationError("transaction_id must be a UUID.")

        if isinstance(self.transaction_date, datetime) or not isinstance(
            self.transaction_date,
            date,
        ):
            raise TransactionValidationError(
                "transaction_date must be a date without a time component."
            )

        if not isinstance(self.amounts, TransactionAmounts):
            raise TransactionValidationError(
                "amounts must be a TransactionAmounts object."
            )

        if not isinstance(self.source, TransactionSource):
            raise TransactionValidationError(
                "source must be a TransactionSource object."
            )

        if not isinstance(self.context, TransactionContext):
            raise TransactionValidationError(
                "context must be a TransactionContext object."
            )

        normalized_original_description = _clean_required_text(
            self.original_description,
            field_name="original_description",
        )

        normalized_search_description = normalize_search_text(
            self.normalized_description
        )

        if not normalized_search_description:
            raise TransactionValidationError("normalized_description cannot be empty.")

        normalized_original_memo = _clean_optional_text(
            self.original_memo,
            field_name="original_memo",
        )

        normalized_vendor_name = _clean_optional_text(
            self.vendor_name,
            field_name="vendor_name",
        )

        object.__setattr__(
            self,
            "original_description",
            normalized_original_description,
        )
        object.__setattr__(
            self,
            "normalized_description",
            normalized_search_description,
        )
        object.__setattr__(
            self,
            "original_memo",
            normalized_original_memo,
        )
        object.__setattr__(
            self,
            "vendor_name",
            normalized_vendor_name,
        )

    @classmethod
    def create(
        cls,
        *,
        transaction_date: date,
        original_description: str,
        source: TransactionSource,
        payment: AmountInput = None,
        deposit: AmountInput = None,
        original_memo: str | None = None,
        normalized_description: str | None = None,
        vendor_name: str | None = None,
        context: TransactionContext | None = None,
        transaction_id: UUID | None = None,
    ) -> Self:
        """
        Create a normalized transaction from ingestion-layer values.

        Parameters
        ----------
        transaction_date:
            Date found in the bank statement.

        original_description:
            Original bank transaction description.

        source:
            Source file and source-row lineage.

        payment:
            Raw payment value.

        deposit:
            Raw deposit value.

        original_memo:
            Original bank-statement memo.

        normalized_description:
            Optional pre-normalized description. When omitted, BSI
            normalizes the original description.

        vendor_name:
            Optional normalized or reviewed vendor name.

        context:
            Optional company, store, and bank-account context.

        transaction_id:
            Optional predefined UUID. A new UUID is generated when omitted.

        Returns
        -------
        NormalizedTransaction
            Immutable validated transaction.
        """

        description_for_search = (
            original_description
            if normalized_description is None
            else normalized_description
        )

        resolved_transaction_id = (
            transaction_id if transaction_id is not None else uuid4()
        )

        resolved_context = context if context is not None else TransactionContext()

        return cls(
            transaction_id=resolved_transaction_id,
            transaction_date=transaction_date,
            original_description=original_description,
            normalized_description=normalize_search_text(description_for_search),
            amounts=TransactionAmounts.from_raw(
                payment=payment,
                deposit=deposit,
            ),
            source=source,
            original_memo=original_memo,
            vendor_name=vendor_name,
            context=resolved_context,
        )

    @property
    def payment(self) -> Decimal:
        """Return the normalized payment amount."""

        return self.amounts.payment

    @property
    def deposit(self) -> Decimal:
        """Return the normalized deposit amount."""

        return self.amounts.deposit

    @property
    def direction(self) -> TransactionDirection:
        """Return whether the transaction is a payment or deposit."""

        return self.amounts.direction

    @property
    def absolute_amount(self) -> Decimal:
        """Return the positive transaction amount."""

        return self.amounts.absolute_amount

    @property
    def signed_amount(self) -> Decimal:
        """Return negative payment or positive deposit."""

        return self.amounts.signed_amount

    @property
    def net_amount(self) -> Decimal:
        """
        Return the signed bank-account movement.

        This is an explicit alias for signed_amount because the BRD refers
        to the normalized field as net amount.
        """

        return self.signed_amount

    @property
    def searchable_text(self) -> str:
        """
        Build deterministic text used for future rule evaluation.

        Vendor, description, and memo are included when available.
        Repeated values are removed while preserving their order.
        """

        searchable_parts: list[str] = []

        if self.vendor_name is not None:
            searchable_parts.append(normalize_search_text(self.vendor_name))

        searchable_parts.append(self.normalized_description)

        if self.original_memo is not None:
            searchable_parts.append(normalize_search_text(self.original_memo))

        unique_parts = tuple(dict.fromkeys(searchable_parts))

        return " | ".join(unique_parts)
