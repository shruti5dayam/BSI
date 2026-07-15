"""
SQLAlchemy declarative foundation for BSI persistence models.

This module defines the authoritative SQLAlchemy declarative base and
database-constraint naming convention used by all BSI ORM models.

Keeping one shared base is important because:

- Alembic needs one metadata collection for migration generation.
- All database tables must use consistent constraint names.
- PostgreSQL schema changes must remain predictable and reviewable.
- ORM models should not create independent metadata registries.
"""

from typing import Final

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

CONSTRAINT_NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": ("fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"),
    "pk": "pk_%(table_name)s",
}
"""
Stable naming rules for indexes and database constraints.

Examples
--------
Primary key:
    pk_transactions

Foreign key:
    fk_transactions_workspace_id_workspaces

Unique constraint:
    uq_transactions_transaction_id

Named check constraint:
    ck_rules_positive_version
"""


class Base(DeclarativeBase):
    """
    Shared declarative base for every BSI SQLAlchemy ORM model.

    All infrastructure database models must inherit from this class.

    Domain objects such as ``NormalizedTransaction`` and
    ``RuleDefinition`` must never inherit from this base because the
    domain layer remains independent of SQLAlchemy and PostgreSQL.
    """

    metadata = MetaData(
        naming_convention=CONSTRAINT_NAMING_CONVENTION,
    )


__all__ = [
    "CONSTRAINT_NAMING_CONVENTION",
    "Base",
]
