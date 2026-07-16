"""
SQLAlchemy persistence model for normalized bank transactions.

This model stores the validated transaction facts produced by the BSI
ingestion and normalization pipeline.

The ORM model belongs to the infrastructure layer. It must not contain:

- Rule evaluation logic
- GL mapping logic
- Reporting classifications
- Streamlit or FastAPI behavior
- AI recommendations

Repository adapters will convert between this persistence model and the
framework-independent ``NormalizedTransaction`` domain object.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from bsi.infrastructure.database.base import Base

MONEY_PRECISION: Final = 19
"""Total number of digits supported by persisted monetary values."""

MONEY_SCALE: Final = 2
"""Number of digits stored after the decimal point."""


class TransactionRecord(Base):
    """
    Persist one workspace-owned normalized bank transaction.

    The composite primary key consists of:

    ``workspace_id`` + ``transaction_id``

    This preserves tenant isolation and allows repository queries to
    require the workspace boundary when loading a transaction.

    Notes
    -----
    ``workspace_id`` is an application and persistence ownership field.
    It is not embedded inside ``NormalizedTransaction`` because the
    application command and repository ports carry the workspace
    boundary separately.

    Payment and deposit are stored separately because that is how bank
    statements represent cash direction. Direction and signed amount
    remain derived values rather than duplicated database fields.
    """

    __tablename__ = "transactions"

    __table_args__ = (
        CheckConstraint(
            "payment >= 0",
            name="payment_non_negative",
        ),
        CheckConstraint(
            "deposit >= 0",
            name="deposit_non_negative",
        ),
        CheckConstraint(
            ("(payment > 0 AND deposit = 0) OR (deposit > 0 AND payment = 0)"),
            name="exactly_one_positive_amount",
        ),
        CheckConstraint(
            "source_row_number >= 1",
            name="positive_source_row_number",
        ),
        CheckConstraint(
            "length(trim(original_description)) > 0",
            name="original_description_not_blank",
        ),
        CheckConstraint(
            "length(trim(normalized_description)) > 0",
            name="normalized_description_not_blank",
        ),
        CheckConstraint(
            "length(trim(file_name)) > 0",
            name="file_name_not_blank",
        ),
        CheckConstraint(
            ("account_last_four IS NULL OR length(account_last_four) = 4"),
            name="account_last_four_length",
        ),
        Index(
            "ix_transactions_workspace_id_transaction_date",
            "workspace_id",
            "transaction_date",
        ),
        Index(
            ("ix_transactions_workspace_id_bank_account_id_transaction_date"),
            "workspace_id",
            "bank_account_id",
            "transaction_date",
        ),
        Index(
            "ix_transactions_workspace_id_processing_run_id",
            "workspace_id",
            "processing_run_id",
        ),
        Index(
            "ix_transactions_workspace_id_source_document_id",
            "workspace_id",
            "source_document_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    )

    transaction_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    )

    transaction_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    original_description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    normalized_description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    original_memo: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    vendor_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    payment: Mapped[Decimal] = mapped_column(
        Numeric(
            precision=MONEY_PRECISION,
            scale=MONEY_SCALE,
            asdecimal=True,
        ),
        nullable=False,
    )

    deposit: Mapped[Decimal] = mapped_column(
        Numeric(
            precision=MONEY_PRECISION,
            scale=MONEY_SCALE,
            asdecimal=True,
        ),
        nullable=False,
    )

    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    source_row_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    sheet_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    source_document_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )

    processing_run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )

    company_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )

    brand_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )

    store_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )

    bank_account_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )

    bank_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    account_last_four: Mapped[str | None] = mapped_column(
        String(4),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = [
    "MONEY_PRECISION",
    "MONEY_SCALE",
    "TransactionRecord",
]
