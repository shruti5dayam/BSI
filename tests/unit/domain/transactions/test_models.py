"""
Unit tests for normalized BSI transaction domain models.

These tests verify:

- Search-text normalization
- Source-file and source-row lineage
- Organizational and bank-account context
- Transaction construction
- Payment and deposit properties
- Signed and net amounts
- Searchable transaction text
- Input validation
- Immutability
"""

from dataclasses import FrozenInstanceError
from datetime import date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest

from bsi.domain.transactions.amounts import TransactionAmounts
from bsi.domain.transactions.enums import TransactionDirection
from bsi.domain.transactions.models import (
    NormalizedTransaction,
    TransactionContext,
    TransactionSource,
    TransactionValidationError,
    normalize_search_text,
)


def build_source() -> TransactionSource:
    """Create reusable valid source lineage for transaction tests."""

    return TransactionSource(
        file_name="bank_statement_dd13.xlsx",
        source_row_number=3,
        sheet_name="Sheet1",
    )


@pytest.mark.unit
def test_search_text_is_normalized() -> None:
    """Search text should be trimmed, case-folded, and space-normalized."""

    result = normalize_search_text("  ORIG   CO NAME:DoorDash, Inc.  ")

    assert result == "orig co name:doordash, inc."


@pytest.mark.unit
def test_search_text_preserves_meaningful_punctuation() -> None:
    """Text normalization should not remove useful punctuation."""

    result = normalize_search_text("CHASE-1234 / Vendor #55")

    assert result == "chase-1234 / vendor #55"


@pytest.mark.unit
def test_search_text_requires_string() -> None:
    """Non-string search values should be rejected."""

    invalid_value = cast(str, 123)

    with pytest.raises(
        TypeError,
        match="Search text must be a string",
    ):
        normalize_search_text(invalid_value)


@pytest.mark.unit
def test_transaction_source_normalizes_values() -> None:
    """Source lineage should clean filename and worksheet text."""

    source = TransactionSource(
        file_name="  bank_statement.xlsx  ",
        source_row_number=5,
        sheet_name="  Bank Data  ",
    )

    assert source.file_name == "bank_statement.xlsx"
    assert source.source_row_number == 5
    assert source.sheet_name == "Bank Data"


@pytest.mark.unit
def test_blank_sheet_name_becomes_none() -> None:
    """Blank optional worksheet names should use one missing-value form."""

    source = TransactionSource(
        file_name="bank_statement.csv",
        source_row_number=2,
        sheet_name="   ",
    )

    assert source.sheet_name is None


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
def test_invalid_source_file_name_is_rejected(
    invalid_file_name: str,
) -> None:
    """Filenames must be present and must not contain directory paths."""

    with pytest.raises(TransactionValidationError):
        TransactionSource(
            file_name=invalid_file_name,
            source_row_number=1,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_row_number",
    [
        0,
        -1,
    ],
)
def test_non_positive_source_row_number_is_rejected(
    invalid_row_number: int,
) -> None:
    """Source rows must use positive one-based numbering."""

    with pytest.raises(
        TransactionValidationError,
        match="greater than or equal to 1",
    ):
        TransactionSource(
            file_name="bank_statement.xlsx",
            source_row_number=invalid_row_number,
        )


@pytest.mark.unit
def test_boolean_source_row_number_is_rejected() -> None:
    """Boolean values must not be accepted as row numbers."""

    with pytest.raises(
        TransactionValidationError,
        match="must be an integer",
    ):
        TransactionSource(
            file_name="bank_statement.xlsx",
            source_row_number=True,
        )


@pytest.mark.unit
def test_source_document_and_processing_run_ids_are_preserved() -> None:
    """Source lineage should preserve valid UUID relationships."""

    source_document_id = uuid4()
    processing_run_id = uuid4()

    source = TransactionSource(
        file_name="bank_statement.xlsx",
        source_row_number=10,
        source_document_id=source_document_id,
        processing_run_id=processing_run_id,
    )

    assert source.source_document_id == source_document_id
    assert source.processing_run_id == processing_run_id


@pytest.mark.unit
def test_invalid_source_document_id_is_rejected() -> None:
    """Source-document identifiers must be UUID objects."""

    invalid_id = cast(UUID, "document-123")

    with pytest.raises(
        TransactionValidationError,
        match="source_document_id must be a UUID or None",
    ):
        TransactionSource(
            file_name="bank_statement.xlsx",
            source_row_number=1,
            source_document_id=invalid_id,
        )


@pytest.mark.unit
def test_transaction_context_normalizes_bank_information() -> None:
    """Bank display information should be cleaned and preserved."""

    context = TransactionContext(
        bank_name="  Chase Bank  ",
        account_last_four=" 0750 ",
    )

    assert context.bank_name == "Chase Bank"
    assert context.account_last_four == "0750"


@pytest.mark.unit
def test_blank_optional_context_values_become_none() -> None:
    """Blank optional context values should become None."""

    context = TransactionContext(
        bank_name="   ",
        account_last_four="   ",
    )

    assert context.bank_name is None
    assert context.account_last_four is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_last_four",
    [
        "750",
        "07500",
        "07A0",
        "12-4",
    ],
)
def test_invalid_account_last_four_is_rejected(
    invalid_last_four: str,
) -> None:
    """Masked bank-account values must contain exactly four digits."""

    with pytest.raises(
        TransactionValidationError,
        match="exactly four digits",
    ):
        TransactionContext(
            account_last_four=invalid_last_four,
        )


@pytest.mark.unit
def test_context_preserves_organizational_ids() -> None:
    """Valid company, brand, store, and bank-account IDs should persist."""

    company_id = uuid4()
    brand_id = uuid4()
    store_id = uuid4()
    bank_account_id = uuid4()

    context = TransactionContext(
        company_id=company_id,
        brand_id=brand_id,
        store_id=store_id,
        bank_account_id=bank_account_id,
    )

    assert context.company_id == company_id
    assert context.brand_id == brand_id
    assert context.store_id == store_id
    assert context.bank_account_id == bank_account_id


@pytest.mark.unit
def test_invalid_context_uuid_is_rejected() -> None:
    """Organizational identifiers must use UUID values."""

    invalid_id = cast(UUID, "store-13")

    with pytest.raises(
        TransactionValidationError,
        match="store_id must be a UUID or None",
    ):
        TransactionContext(store_id=invalid_id)


@pytest.mark.unit
def test_payment_transaction_is_created() -> None:
    """The factory should create a normalized payment transaction."""

    transaction_id = uuid4()

    transaction = NormalizedTransaction.create(
        transaction_id=transaction_id,
        transaction_date=date(2026, 7, 1),
        original_description="  NATIONAL   DCP  ",
        original_memo=" Food distributor payment ",
        payment="177.70",
        deposit=None,
        vendor_name=" National DCP ",
        source=build_source(),
    )

    assert transaction.transaction_id == transaction_id
    assert transaction.transaction_date == date(2026, 7, 1)
    assert transaction.original_description == "NATIONAL DCP"
    assert transaction.normalized_description == "national dcp"
    assert transaction.original_memo == "Food distributor payment"
    assert transaction.vendor_name == "National DCP"
    assert transaction.payment == Decimal("177.70")
    assert transaction.deposit == Decimal("0.00")
    assert transaction.direction is TransactionDirection.PAYMENT
    assert transaction.absolute_amount == Decimal("177.70")
    assert transaction.signed_amount == Decimal("-177.70")
    assert transaction.net_amount == Decimal("-177.70")


@pytest.mark.unit
def test_deposit_transaction_is_created() -> None:
    """The factory should create a normalized deposit transaction."""

    transaction = NormalizedTransaction.create(
        transaction_date=date(2026, 7, 2),
        original_description="DoorDash Settlement",
        payment=None,
        deposit="500.00",
        source=build_source(),
    )

    assert transaction.direction is TransactionDirection.DEPOSIT
    assert transaction.payment == Decimal("0.00")
    assert transaction.deposit == Decimal("500.00")
    assert transaction.signed_amount == Decimal("500.00")
    assert transaction.net_amount == Decimal("500.00")


@pytest.mark.unit
def test_factory_generates_transaction_id_when_missing() -> None:
    """A new UUID should be generated when ingestion supplies no ID."""

    transaction = NormalizedTransaction.create(
        transaction_date=date(2026, 7, 3),
        original_description="Utility Payment",
        payment="100.00",
        source=build_source(),
    )

    assert isinstance(transaction.transaction_id, UUID)


@pytest.mark.unit
def test_custom_normalized_description_is_used() -> None:
    """Ingestion may provide an explicitly normalized description."""

    transaction = NormalizedTransaction.create(
        transaction_date=date(2026, 7, 3),
        original_description="ORIG CO NAME: DD INC",
        normalized_description="  DoorDash   Incorporated ",
        deposit="100.00",
        source=build_source(),
    )

    assert transaction.original_description == "ORIG CO NAME: DD INC"
    assert transaction.normalized_description == "doordash incorporated"


@pytest.mark.unit
def test_default_context_is_created() -> None:
    """Transactions without resolved metadata should receive empty context."""

    transaction = NormalizedTransaction.create(
        transaction_date=date(2026, 7, 3),
        original_description="Unknown Vendor",
        payment="25.00",
        source=build_source(),
    )

    assert transaction.context == TransactionContext()


@pytest.mark.unit
def test_searchable_text_combines_vendor_description_and_memo() -> None:
    """Rule-search text should include all available transaction evidence."""

    transaction = NormalizedTransaction.create(
        transaction_date=date(2026, 7, 4),
        original_description="ORIG CO NAME:DoorDash, Inc.",
        original_memo="Weekly settlement",
        vendor_name="DoorDash",
        deposit="500.00",
        source=build_source(),
    )

    assert transaction.searchable_text == (
        "doordash | orig co name:doordash, inc. | weekly settlement"
    )


@pytest.mark.unit
def test_searchable_text_removes_duplicate_parts() -> None:
    """Repeated searchable values should appear only once."""

    transaction = NormalizedTransaction.create(
        transaction_date=date(2026, 7, 4),
        original_description="DoorDash",
        original_memo="DOORDASH",
        vendor_name="doordash",
        deposit="500.00",
        source=build_source(),
    )

    assert transaction.searchable_text == "doordash"


@pytest.mark.unit
def test_datetime_is_rejected_as_transaction_date() -> None:
    """A bank transaction date must not contain an implicit time value."""

    with pytest.raises(
        TransactionValidationError,
        match="must be a date without a time component",
    ):
        NormalizedTransaction.create(
            transaction_date=datetime(2026, 7, 1, 12, 30),
            original_description="Vendor Payment",
            payment="10.00",
            source=build_source(),
        )


@pytest.mark.unit
def test_empty_original_description_is_rejected() -> None:
    """Every normalized transaction requires an original description."""

    with pytest.raises(
        TransactionValidationError,
        match="original_description cannot be empty",
    ):
        NormalizedTransaction.create(
            transaction_date=date(2026, 7, 1),
            original_description="   ",
            payment="10.00",
            source=build_source(),
        )


@pytest.mark.unit
def test_direct_constructor_requires_transaction_amounts() -> None:
    """Direct construction must receive a validated amount value object."""

    invalid_amounts = cast(
        TransactionAmounts,
        Decimal("100.00"),
    )

    with pytest.raises(
        TransactionValidationError,
        match="amounts must be a TransactionAmounts object",
    ):
        NormalizedTransaction(
            transaction_id=uuid4(),
            transaction_date=date(2026, 7, 1),
            original_description="Vendor",
            normalized_description="vendor",
            amounts=invalid_amounts,
            source=build_source(),
        )


@pytest.mark.unit
def test_direct_constructor_requires_transaction_source() -> None:
    """Direct construction must receive valid source lineage."""

    invalid_source = cast(
        TransactionSource,
        "bank_statement.xlsx",
    )

    with pytest.raises(
        TransactionValidationError,
        match="source must be a TransactionSource object",
    ):
        NormalizedTransaction(
            transaction_id=uuid4(),
            transaction_date=date(2026, 7, 1),
            original_description="Vendor",
            normalized_description="vendor",
            amounts=TransactionAmounts.from_raw(payment="100.00"),
            source=invalid_source,
        )


@pytest.mark.unit
def test_transaction_is_immutable() -> None:
    """Source transaction facts must not change after validation."""

    transaction = NormalizedTransaction.create(
        transaction_date=date(2026, 7, 1),
        original_description="Vendor Payment",
        payment="100.00",
        source=build_source(),
    )

    attribute_name = "original_description"

    with pytest.raises(FrozenInstanceError):
        setattr(
            transaction,
            attribute_name,
            "Changed Description",
        )
