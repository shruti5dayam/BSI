"""
Unit tests for the BSI SQLAlchemy declarative foundation.

These tests verify:

- The shared SQLAlchemy declarative base
- The authoritative metadata registry
- Primary-key naming
- Unique-constraint naming
- Index naming
- Foreign-key naming
- Check-constraint naming

Stable database object names are important for predictable Alembic
migrations and production schema maintenance.
"""

import pytest
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase

from bsi.infrastructure.database.base import (
    CONSTRAINT_NAMING_CONVENTION,
    Base,
)


def _metadata() -> MetaData:
    """
    Create isolated metadata using the production naming convention.

    Tests use separate metadata instead of adding temporary tables to
    ``Base.metadata``. This prevents test-only tables from polluting the
    authoritative application schema registry.
    """

    return MetaData(
        naming_convention=CONSTRAINT_NAMING_CONVENTION,
    )


@pytest.mark.unit
def test_base_is_sqlalchemy_declarative_base() -> None:
    """All ORM models can inherit from the shared declarative base."""

    assert issubclass(Base, DeclarativeBase)


@pytest.mark.unit
def test_base_uses_authoritative_naming_convention() -> None:
    """The shared metadata registry must use the configured convention."""

    assert Base.metadata.naming_convention == CONSTRAINT_NAMING_CONVENTION


@pytest.mark.unit
def test_primary_key_receives_stable_name() -> None:
    """Primary keys should use the table-based naming convention."""

    metadata = _metadata()

    table = Table(
        "test_accounts",
        metadata,
        Column(
            "id",
            Integer,
            primary_key=True,
        ),
    )

    assert table.primary_key.name == "pk_test_accounts"


@pytest.mark.unit
def test_unique_constraint_receives_stable_name() -> None:
    """Unique constraints should include the table and first column."""

    metadata = _metadata()

    table = Table(
        "test_workspaces",
        metadata,
        Column(
            "id",
            Integer,
            primary_key=True,
        ),
        Column(
            "workspace_code",
            String(50),
            unique=True,
        ),
    )

    unique_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]

    assert len(unique_constraints) == 1
    assert unique_constraints[0].name == "uq_test_workspaces_workspace_code"


@pytest.mark.unit
def test_index_receives_stable_name() -> None:
    """Indexes should include their table and first indexed column."""

    metadata = _metadata()

    table = Table(
        "test_transactions",
        metadata,
        Column(
            "id",
            Integer,
            primary_key=True,
        ),
        Column(
            "transaction_reference",
            String(100),
            index=True,
        ),
    )

    index_names = {index.name for index in table.indexes}

    assert index_names == {"ix_test_transactions_transaction_reference"}


@pytest.mark.unit
def test_foreign_key_receives_stable_name() -> None:
    """Foreign keys should identify both local and referenced tables."""

    metadata = _metadata()

    Table(
        "test_workspaces",
        metadata,
        Column(
            "id",
            Integer,
            primary_key=True,
        ),
    )

    child_table = Table(
        "test_transactions",
        metadata,
        Column(
            "id",
            Integer,
            primary_key=True,
        ),
        Column(
            "workspace_id",
            Integer,
            ForeignKey("test_workspaces.id"),
            nullable=False,
        ),
    )

    foreign_key = next(iter(child_table.c.workspace_id.foreign_keys))

    foreign_key_constraint = foreign_key.constraint

    assert foreign_key_constraint is not None
    assert foreign_key_constraint.name == (
        "fk_test_transactions_workspace_id_test_workspaces"
    )


@pytest.mark.unit
def test_named_check_constraint_receives_stable_name() -> None:
    """Named checks should combine table and business constraint names."""

    metadata = _metadata()

    table = Table(
        "test_rules",
        metadata,
        Column(
            "id",
            Integer,
            primary_key=True,
        ),
        Column(
            "version",
            Integer,
            nullable=False,
        ),
        CheckConstraint(
            "version > 0",
            name="positive_version",
        ),
    )

    check_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    ]

    assert len(check_constraints) == 1
    assert check_constraints[0].name == "ck_test_rules_positive_version"
