"""
Mapper between transaction domain objects and SQLAlchemy records.

The domain layer uses nested, immutable objects:

- NormalizedTransaction
- TransactionAmounts
- TransactionSource
- TransactionContext

The persistence layer stores those values as flat database columns in
TransactionRecord.

This module performs only data conversion. It must not contain business
rules, database queries, commits, or transaction-processing logic.
"""

from uuid import UUID

from bsi.domain.transactions.models import (
    NormalizedTransaction,
    TransactionAmounts,
    TransactionContext,
    TransactionSource,
)
from bsi.infrastructure.database.models.transaction import TransactionRecord


def transaction_to_record(
    *,
    workspace_id: UUID,
    transaction: NormalizedTransaction,
) -> TransactionRecord:
    """
    Convert a normalized domain transaction into an ORM record.

    Parameters
    ----------
    workspace_id:
        Workspace that owns the persisted transaction.

        Workspace ownership is supplied separately because it belongs to
        the application and persistence boundary rather than the
        transaction domain object.

    transaction:
        Validated and normalized domain transaction to persist.

    Returns
    -------
    TransactionRecord
        New SQLAlchemy ORM record containing the flattened transaction
        values.

    Raises
    ------
    TypeError
        If workspace_id is not a UUID or transaction is not a
        NormalizedTransaction.
    """

    if not isinstance(workspace_id, UUID):
        raise TypeError("workspace_id must be a UUID.")

    if not isinstance(transaction, NormalizedTransaction):
        raise TypeError(
            "transaction must be a NormalizedTransaction.",
        )

    return TransactionRecord(
        workspace_id=workspace_id,
        transaction_id=transaction.transaction_id,
        transaction_date=transaction.transaction_date,
        original_description=transaction.original_description,
        normalized_description=transaction.normalized_description,
        original_memo=transaction.original_memo,
        vendor_name=transaction.vendor_name,
        payment=transaction.amounts.payment,
        deposit=transaction.amounts.deposit,
        file_name=transaction.source.file_name,
        source_row_number=transaction.source.source_row_number,
        sheet_name=transaction.source.sheet_name,
        source_document_id=transaction.source.source_document_id,
        processing_run_id=transaction.source.processing_run_id,
        company_id=transaction.context.company_id,
        brand_id=transaction.context.brand_id,
        store_id=transaction.context.store_id,
        bank_account_id=transaction.context.bank_account_id,
        bank_name=transaction.context.bank_name,
        account_last_four=transaction.context.account_last_four,
    )


def transaction_to_domain(
    record: TransactionRecord,
) -> NormalizedTransaction:
    """
    Convert an ORM transaction record into a domain transaction.

    Parameters
    ----------
    record:
        SQLAlchemy transaction record loaded from persistence.

    Returns
    -------
    NormalizedTransaction
        Immutable, framework-independent transaction domain object.

    Raises
    ------
    TypeError
        If record is not a TransactionRecord.

    Notes
    -----
    workspace_id, created_at, and updated_at are intentionally not copied
    into the domain transaction:

    - workspace_id belongs to repository/application ownership.
    - created_at and updated_at are persistence metadata.
    """

    if not isinstance(record, TransactionRecord):
        raise TypeError("record must be a TransactionRecord.")

    amounts = TransactionAmounts(
        payment=record.payment,
        deposit=record.deposit,
    )

    source = TransactionSource(
        file_name=record.file_name,
        source_row_number=record.source_row_number,
        sheet_name=record.sheet_name,
        source_document_id=record.source_document_id,
        processing_run_id=record.processing_run_id,
    )

    context = TransactionContext(
        company_id=record.company_id,
        brand_id=record.brand_id,
        store_id=record.store_id,
        bank_account_id=record.bank_account_id,
        bank_name=record.bank_name,
        account_last_four=record.account_last_four,
    )

    return NormalizedTransaction(
        transaction_id=record.transaction_id,
        transaction_date=record.transaction_date,
        original_description=record.original_description,
        normalized_description=record.normalized_description,
        amounts=amounts,
        source=source,
        original_memo=record.original_memo,
        vendor_name=record.vendor_name,
        context=context,
    )


__all__ = [
    "transaction_to_domain",
    "transaction_to_record",
]
