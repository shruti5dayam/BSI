"""
Unit tests for the normalized-transaction SQLAlchemy model.

These tests verify:

- Authoritative table registration
- Composite workspace and transaction identity
- Required and optional database columns
- Exact financial amount precision
- UUID persistence types
- Source-lineage and organizational-context fields
- Database check constraints
- Query-supporting indexes
- Audit timestamp configuration

These are schema-contract tests. They do not connect to PostgreSQL.
"""

from collections.abc import Mapping
from typing import cast

import pytest
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
)
from sqlalchemy.sql.schema import Column, Index, Table

from bsi.infrastructure.database.base import Base
from bsi.infrastructure.database.models.transaction import (
    MONEY_PRECISION,
    MONEY_SCALE,
    TransactionRecord,
)


def _table() -> Table:
    """Return the authoritative normalized-transaction table."""

    return cast(Table, TransactionRecord.__table__)


def _column_names(table: Table) -> tuple[str, ...]:
    """Return table column names in declared schema order."""

    return tuple(column.name for column in table.columns)


def _check_constraints(
    table: Table,
) -> tuple[CheckConstraint, ...]:
    """Return all check constraints registered on a table."""

    return tuple(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    )


def _indexes_by_name(
    table: Table,
) -> Mapping[str, Index]:
    """Return table indexes keyed by their stable database names."""

    return {str(index.name): index for index in table.indexes}


@pytest.mark.unit
def test_transaction_record_inherits_shared_base() -> None:
    """The ORM model must participate in the shared metadata registry."""

    assert issubclass(TransactionRecord, Base)
    assert TransactionRecord.metadata is Base.metadata


@pytest.mark.unit
def test_transaction_table_uses_authoritative_name() -> None:
    """The normalized transaction table name must remain stable."""

    table = _table()

    assert table.name == "transactions"
    assert Base.metadata.tables["transactions"] is table


@pytest.mark.unit
def test_transaction_table_contains_expected_columns() -> None:
    """The table should persist every required transaction fact."""

    table = _table()

    assert _column_names(table) == (
        "workspace_id",
        "transaction_id",
        "transaction_date",
        "original_description",
        "normalized_description",
        "original_memo",
        "vendor_name",
        "payment",
        "deposit",
        "file_name",
        "source_row_number",
        "sheet_name",
        "source_document_id",
        "processing_run_id",
        "company_id",
        "brand_id",
        "store_id",
        "bank_account_id",
        "bank_name",
        "account_last_four",
        "created_at",
        "updated_at",
    )


@pytest.mark.unit
def test_transaction_table_uses_composite_primary_key() -> None:
    """
    Workspace and transaction identifiers must form the primary key.

    This ensures that transaction access remains tenant-scoped.
    """

    table = _table()

    primary_key_columns = tuple(column.name for column in table.primary_key.columns)

    assert primary_key_columns == (
        "workspace_id",
        "transaction_id",
    )
    assert table.primary_key.name == "pk_transactions"


@pytest.mark.unit
def test_required_columns_are_not_nullable() -> None:
    """Authoritative transaction facts must always be present."""

    table = _table()

    required_column_names = {
        "workspace_id",
        "transaction_id",
        "transaction_date",
        "original_description",
        "normalized_description",
        "payment",
        "deposit",
        "file_name",
        "source_row_number",
        "created_at",
        "updated_at",
    }

    for column_name in required_column_names:
        assert table.c[column_name].nullable is False


@pytest.mark.unit
def test_optional_columns_are_nullable() -> None:
    """Optional lineage and business context may be absent initially."""

    table = _table()

    optional_column_names = {
        "original_memo",
        "vendor_name",
        "sheet_name",
        "source_document_id",
        "processing_run_id",
        "company_id",
        "brand_id",
        "store_id",
        "bank_account_id",
        "bank_name",
        "account_last_four",
    }

    for column_name in optional_column_names:
        assert table.c[column_name].nullable is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "column_name",
    [
        "workspace_id",
        "transaction_id",
        "source_document_id",
        "processing_run_id",
        "company_id",
        "brand_id",
        "store_id",
        "bank_account_id",
    ],
)
def test_identifier_columns_use_uuid_type(
    column_name: str,
) -> None:
    """Persistent identifiers should retain native UUID semantics."""

    column = _table().c[column_name]

    assert isinstance(column.type, Uuid)
    assert column.type.as_uuid is True


@pytest.mark.unit
def test_transaction_date_uses_date_without_time() -> None:
    """Bank-statement transaction dates should not store a time."""

    transaction_date_column = _table().c.transaction_date

    assert isinstance(transaction_date_column.type, Date)
    assert not isinstance(
        transaction_date_column.type,
        DateTime,
    )


@pytest.mark.unit
def test_description_columns_use_unbounded_text() -> None:
    """Descriptions and memos should not be truncated to short lengths."""

    table = _table()

    assert isinstance(
        table.c.original_description.type,
        Text,
    )
    assert isinstance(
        table.c.normalized_description.type,
        Text,
    )
    assert isinstance(
        table.c.original_memo.type,
        Text,
    )


@pytest.mark.unit
def test_financial_columns_use_exact_decimal_precision() -> None:
    """
    Payment and deposit must use exact fixed-point database values.

    Floating-point database types are inappropriate for authoritative
    financial calculations.
    """

    table = _table()

    for column_name in (
        "payment",
        "deposit",
    ):
        column_type = table.c[column_name].type

        assert isinstance(column_type, Numeric)
        assert column_type.precision == MONEY_PRECISION
        assert column_type.scale == MONEY_SCALE
        assert column_type.asdecimal is True


@pytest.mark.unit
def test_source_row_number_uses_integer_type() -> None:
    """Source-file row lineage should use an integer column."""

    source_row_column = _table().c.source_row_number

    assert isinstance(source_row_column.type, Integer)


@pytest.mark.unit
def test_bounded_text_columns_use_expected_lengths() -> None:
    """Display and source fields should use appropriate size limits."""

    table = _table()

    expected_lengths = {
        "vendor_name": 255,
        "file_name": 255,
        "sheet_name": 255,
        "bank_name": 255,
        "account_last_four": 4,
    }

    for column_name, expected_length in expected_lengths.items():
        column_type = table.c[column_name].type

        assert isinstance(column_type, String)
        assert column_type.length == expected_length


@pytest.mark.unit
def test_transaction_check_constraints_are_registered() -> None:
    """Database constraints should protect core transaction invariants."""

    constraint_names = {constraint.name for constraint in _check_constraints(_table())}

    assert constraint_names == {
        "ck_transactions_account_last_four_length",
        "ck_transactions_deposit_non_negative",
        "ck_transactions_exactly_one_positive_amount",
        "ck_transactions_file_name_not_blank",
        "ck_transactions_normalized_description_not_blank",
        "ck_transactions_original_description_not_blank",
        "ck_transactions_payment_non_negative",
        "ck_transactions_positive_source_row_number",
    }


@pytest.mark.unit
def test_transaction_indexes_are_registered() -> None:
    """Expected tenant-scoped query indexes should remain available."""

    indexes = _indexes_by_name(_table())

    assert set(indexes) == {
        ("ix_transactions_workspace_id_bank_account_id_transaction_date"),
        "ix_transactions_workspace_id_processing_run_id",
        "ix_transactions_workspace_id_source_document_id",
        "ix_transactions_workspace_id_transaction_date",
    }


@pytest.mark.unit
def test_transaction_indexes_use_expected_column_order() -> None:
    """
    Composite index order should begin with the workspace boundary.

    PostgreSQL can then filter by tenant before applying the remaining
    account, date, document, or processing-run criteria.
    """

    indexes = _indexes_by_name(_table())

    expected_index_columns = {
        "ix_transactions_workspace_id_transaction_date": (
            "workspace_id",
            "transaction_date",
        ),
        ("ix_transactions_workspace_id_bank_account_id_transaction_date"): (
            "workspace_id",
            "bank_account_id",
            "transaction_date",
        ),
        "ix_transactions_workspace_id_processing_run_id": (
            "workspace_id",
            "processing_run_id",
        ),
        "ix_transactions_workspace_id_source_document_id": (
            "workspace_id",
            "source_document_id",
        ),
    }

    for index_name, expected_columns in expected_index_columns.items():
        actual_columns = tuple(column.name for column in indexes[index_name].columns)

        assert actual_columns == expected_columns


@pytest.mark.unit
def test_audit_columns_use_timezone_aware_timestamps() -> None:
    """Created and updated timestamps should preserve timezone context."""

    table = _table()

    for column_name in (
        "created_at",
        "updated_at",
    ):
        column_type = table.c[column_name].type

        assert isinstance(column_type, DateTime)
        assert column_type.timezone is True


@pytest.mark.unit
def test_created_at_has_server_default() -> None:
    """PostgreSQL should assign creation time during insertion."""

    created_at_column: Column[object] = _table().c.created_at

    assert created_at_column.server_default is not None


@pytest.mark.unit
def test_updated_at_has_server_default_and_update_rule() -> None:
    """The update timestamp requires insert and update behavior."""

    updated_at_column: Column[object] = _table().c.updated_at

    assert updated_at_column.server_default is not None
    assert updated_at_column.onupdate is not None
