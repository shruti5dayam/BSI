"""
Unit tests for transaction domain-to-persistence mapping.

These tests protect the boundary between:

- NormalizedTransaction domain objects
- TransactionRecord SQLAlchemy persistence objects

No database connection is required because these tests only verify
in-memory conversion behavior.
"""

from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from bsi.domain.transactions.models import (
    NormalizedTransaction,
    TransactionAmounts,
    TransactionContext,
    TransactionSource,
)
from bsi.infrastructure.database.mappers.transaction import (
    transaction_to_domain,
    transaction_to_record,
)
from bsi.infrastructure.database.models.transaction import TransactionRecord

WORKSPACE_ID = UUID("11111111-1111-1111-1111-111111111111")
TRANSACTION_ID = UUID("22222222-2222-2222-2222-222222222222")
SOURCE_DOCUMENT_ID = UUID("33333333-3333-3333-3333-333333333333")
PROCESSING_RUN_ID = UUID("44444444-4444-4444-4444-444444444444")
COMPANY_ID = UUID("55555555-5555-5555-5555-555555555555")
BRAND_ID = UUID("66666666-6666-6666-6666-666666666666")
STORE_ID = UUID("77777777-7777-7777-7777-777777777777")
BANK_ACCOUNT_ID = UUID("88888888-8888-8888-8888-888888888888")


def make_domain_transaction() -> NormalizedTransaction:
    """
    Create a fully populated normalized transaction for mapper tests.

    Returns
    -------
    NormalizedTransaction
        Valid domain transaction containing transaction, source, and
        business-context information.
    """

    return NormalizedTransaction(
        transaction_id=TRANSACTION_ID,
        transaction_date=date(2026, 7, 1),
        original_description="  DBMASTERFINANC ACH DEBIT  ",
        normalized_description="dbmasterfinanc ach debit",
        original_memo="  Franchise royalty payment  ",
        vendor_name="  DB Master Finance  ",
        amounts=TransactionAmounts(
            payment=Decimal("425.75"),
            deposit=Decimal("0.00"),
        ),
        source=TransactionSource(
            file_name="bank_statement_dd13.xlsx",
            source_row_number=42,
            sheet_name="Sheet1",
            source_document_id=SOURCE_DOCUMENT_ID,
            processing_run_id=PROCESSING_RUN_ID,
        ),
        context=TransactionContext(
            company_id=COMPANY_ID,
            brand_id=BRAND_ID,
            store_id=STORE_ID,
            bank_account_id=BANK_ACCOUNT_ID,
            bank_name="Chase",
            account_last_four="7871",
        ),
    )


def make_transaction_record() -> TransactionRecord:
    """
    Create a fully populated transaction persistence record.

    Returns
    -------
    TransactionRecord
        SQLAlchemy record suitable for reverse-mapping tests.
    """

    return TransactionRecord(
        workspace_id=WORKSPACE_ID,
        transaction_id=TRANSACTION_ID,
        transaction_date=date(2026, 7, 1),
        original_description="DBMASTERFINANC ACH DEBIT",
        normalized_description="dbmasterfinanc ach debit",
        original_memo="Franchise royalty payment",
        vendor_name="DB Master Finance",
        payment=Decimal("425.75"),
        deposit=Decimal("0.00"),
        file_name="bank_statement_dd13.xlsx",
        source_row_number=42,
        sheet_name="Sheet1",
        source_document_id=SOURCE_DOCUMENT_ID,
        processing_run_id=PROCESSING_RUN_ID,
        company_id=COMPANY_ID,
        brand_id=BRAND_ID,
        store_id=STORE_ID,
        bank_account_id=BANK_ACCOUNT_ID,
        bank_name="Chase",
        account_last_four="7871",
    )


def test_transaction_to_record_returns_transaction_record() -> None:
    """The forward mapper should return the expected ORM model type."""

    domain_transaction = make_domain_transaction()

    record = transaction_to_record(
        workspace_id=WORKSPACE_ID,
        transaction=domain_transaction,
    )

    assert isinstance(record, TransactionRecord)


def test_transaction_to_record_maps_workspace_ownership() -> None:
    """Workspace ownership should be supplied by the repository boundary."""

    record = transaction_to_record(
        workspace_id=WORKSPACE_ID,
        transaction=make_domain_transaction(),
    )

    assert record.workspace_id == WORKSPACE_ID


def test_transaction_to_record_maps_transaction_fields() -> None:
    """Top-level transaction facts should map to flat ORM columns."""

    transaction = make_domain_transaction()

    record = transaction_to_record(
        workspace_id=WORKSPACE_ID,
        transaction=transaction,
    )

    assert record.transaction_id == transaction.transaction_id
    assert record.transaction_date == transaction.transaction_date
    assert record.original_description == transaction.original_description
    assert record.normalized_description == transaction.normalized_description
    assert record.original_memo == transaction.original_memo
    assert record.vendor_name == transaction.vendor_name


def test_transaction_to_record_maps_amount_fields() -> None:
    """Nested payment and deposit values should map correctly."""

    transaction = make_domain_transaction()

    record = transaction_to_record(
        workspace_id=WORKSPACE_ID,
        transaction=transaction,
    )

    assert record.payment == transaction.amounts.payment
    assert record.deposit == transaction.amounts.deposit


def test_transaction_to_record_maps_source_lineage() -> None:
    """Source-document lineage should be flattened into ORM columns."""

    transaction = make_domain_transaction()

    record = transaction_to_record(
        workspace_id=WORKSPACE_ID,
        transaction=transaction,
    )

    assert record.file_name == transaction.source.file_name
    assert record.source_row_number == transaction.source.source_row_number
    assert record.sheet_name == transaction.source.sheet_name
    assert record.source_document_id == transaction.source.source_document_id
    assert record.processing_run_id == transaction.source.processing_run_id


def test_transaction_to_record_maps_business_context() -> None:
    """Company, brand, store, and bank context should map correctly."""

    transaction = make_domain_transaction()

    record = transaction_to_record(
        workspace_id=WORKSPACE_ID,
        transaction=transaction,
    )

    assert record.company_id == transaction.context.company_id
    assert record.brand_id == transaction.context.brand_id
    assert record.store_id == transaction.context.store_id
    assert record.bank_account_id == transaction.context.bank_account_id
    assert record.bank_name == transaction.context.bank_name
    assert record.account_last_four == transaction.context.account_last_four


def test_transaction_to_domain_returns_normalized_transaction() -> None:
    """The reverse mapper should return the expected domain type."""

    domain_transaction = transaction_to_domain(
        make_transaction_record(),
    )

    assert isinstance(domain_transaction, NormalizedTransaction)


def test_transaction_to_domain_reconstructs_transaction_fields() -> None:
    """Flat ORM transaction fields should rebuild the domain object."""

    record = make_transaction_record()

    transaction = transaction_to_domain(record)

    assert transaction.transaction_id == record.transaction_id
    assert transaction.transaction_date == record.transaction_date
    assert transaction.original_description == record.original_description
    assert transaction.normalized_description == record.normalized_description
    assert transaction.original_memo == record.original_memo
    assert transaction.vendor_name == record.vendor_name


def test_transaction_to_domain_reconstructs_amounts() -> None:
    """Payment and deposit columns should rebuild TransactionAmounts."""

    record = make_transaction_record()

    transaction = transaction_to_domain(record)

    assert transaction.amounts == TransactionAmounts(
        payment=record.payment,
        deposit=record.deposit,
    )


def test_transaction_to_domain_reconstructs_source() -> None:
    """Source columns should rebuild TransactionSource."""

    record = make_transaction_record()

    transaction = transaction_to_domain(record)

    assert transaction.source == TransactionSource(
        file_name=record.file_name,
        source_row_number=record.source_row_number,
        sheet_name=record.sheet_name,
        source_document_id=record.source_document_id,
        processing_run_id=record.processing_run_id,
    )


def test_transaction_to_domain_reconstructs_context() -> None:
    """Scope columns should rebuild TransactionContext."""

    record = make_transaction_record()

    transaction = transaction_to_domain(record)

    assert transaction.context == TransactionContext(
        company_id=record.company_id,
        brand_id=record.brand_id,
        store_id=record.store_id,
        bank_account_id=record.bank_account_id,
        bank_name=record.bank_name,
        account_last_four=record.account_last_four,
    )


def test_transaction_round_trip_preserves_domain_object() -> None:
    """
    Mapping to persistence and back should preserve the domain value.

    This is the most important mapper contract test.
    """

    original_transaction = make_domain_transaction()

    record = transaction_to_record(
        workspace_id=WORKSPACE_ID,
        transaction=original_transaction,
    )

    reconstructed_transaction = transaction_to_domain(record)

    assert reconstructed_transaction == original_transaction


def test_transaction_to_record_preserves_optional_none_values() -> None:
    """Optional source and context values should remain null."""

    transaction = NormalizedTransaction(
        transaction_id=TRANSACTION_ID,
        transaction_date=date(2026, 7, 2),
        original_description="BANK SERVICE FEE",
        normalized_description="bank service fee",
        amounts=TransactionAmounts(
            payment=Decimal("15.00"),
            deposit=Decimal("0.00"),
        ),
        source=TransactionSource(
            file_name="bank_statement.xlsx",
            source_row_number=5,
        ),
    )

    record = transaction_to_record(
        workspace_id=WORKSPACE_ID,
        transaction=transaction,
    )

    assert record.original_memo is None
    assert record.vendor_name is None
    assert record.sheet_name is None
    assert record.source_document_id is None
    assert record.processing_run_id is None
    assert record.company_id is None
    assert record.brand_id is None
    assert record.store_id is None
    assert record.bank_account_id is None
    assert record.bank_name is None
    assert record.account_last_four is None


def test_transaction_to_domain_preserves_optional_none_values() -> None:
    """Null persistence values should remain optional domain values."""

    record = TransactionRecord(
        workspace_id=WORKSPACE_ID,
        transaction_id=TRANSACTION_ID,
        transaction_date=date(2026, 7, 2),
        original_description="BANK SERVICE FEE",
        normalized_description="bank service fee",
        original_memo=None,
        vendor_name=None,
        payment=Decimal("15.00"),
        deposit=Decimal("0.00"),
        file_name="bank_statement.xlsx",
        source_row_number=5,
        sheet_name=None,
        source_document_id=None,
        processing_run_id=None,
        company_id=None,
        brand_id=None,
        store_id=None,
        bank_account_id=None,
        bank_name=None,
        account_last_four=None,
    )

    transaction = transaction_to_domain(record)

    assert transaction.original_memo is None
    assert transaction.vendor_name is None
    assert transaction.source.sheet_name is None
    assert transaction.source.source_document_id is None
    assert transaction.source.processing_run_id is None
    assert transaction.context == TransactionContext()


def test_transaction_to_record_rejects_invalid_workspace_id() -> None:
    """The forward mapper should enforce UUID workspace ownership."""

    invalid_workspace_id = cast(UUID, "not-a-uuid")

    with pytest.raises(
        TypeError,
        match="workspace_id must be a UUID",
    ):
        transaction_to_record(
            workspace_id=invalid_workspace_id,
            transaction=make_domain_transaction(),
        )


def test_transaction_to_record_rejects_invalid_transaction() -> None:
    """The forward mapper should reject non-domain objects."""

    invalid_transaction = cast(
        NormalizedTransaction,
        object(),
    )

    with pytest.raises(
        TypeError,
        match="transaction must be a NormalizedTransaction",
    ):
        transaction_to_record(
            workspace_id=WORKSPACE_ID,
            transaction=invalid_transaction,
        )


def test_transaction_to_domain_rejects_invalid_record() -> None:
    """The reverse mapper should reject non-ORM objects."""

    invalid_record = cast(
        TransactionRecord,
        object(),
    )

    with pytest.raises(
        TypeError,
        match="record must be a TransactionRecord",
    ):
        transaction_to_domain(invalid_record)
