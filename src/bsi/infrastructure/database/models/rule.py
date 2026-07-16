"""
SQLAlchemy persistence models for deterministic BSI rules.

This module stores:

- Rule definitions and lifecycle information
- Organizational rule scope
- COA mapping output
- Ordered rule conditions
- Typed scalar and range condition values

The ORM models belong to the infrastructure layer. Deterministic rule
validation, evaluation, ranking, and conflict detection remain inside
the domain layer.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bsi.infrastructure.database.base import Base

RULE_AMOUNT_PRECISION: Final = 19
"""Total digits supported by persisted rule amount values."""

RULE_AMOUNT_SCALE: Final = 2
"""Digits stored after the decimal point for rule amount values."""


class RuleRecord(Base):
    """
    Persist one workspace-owned deterministic rule definition.

    The composite primary key is:

    ``workspace_id`` + ``rule_id``

    This ensures every rule lookup remains tenant-scoped.

    Rule conditions are stored separately because they are ordered,
    typed, auditable objects rather than an unstructured JSON document.
    """

    __tablename__ = "rule_definitions"

    __table_args__ = (
        CheckConstraint(
            "length(trim(name)) > 0",
            name="name_not_blank",
        ),
        CheckConstraint(
            "logic IN ('all', 'any')",
            name="valid_logic",
        ),
        CheckConstraint(
            ("status IN ('draft', 'pending_approval', 'active', 'paused', 'retired')"),
            name="valid_status",
        ),
        CheckConstraint(
            "priority >= 0 AND priority <= 10000",
            name="priority_in_range",
        ),
        CheckConstraint(
            "version >= 1",
            name="positive_version",
        ),
        CheckConstraint(
            (
                "effective_from IS NULL "
                "OR effective_to IS NULL "
                "OR effective_from <= effective_to"
            ),
            name="valid_effective_date_range",
        ),
        CheckConstraint(
            ("status = 'draft' OR output_coa_account_id IS NOT NULL"),
            name="non_draft_requires_output",
        ),
        Index(
            "ix_rule_definitions_workspace_id_status_priority",
            "workspace_id",
            "status",
            "priority",
        ),
        Index(
            ("ix_rule_definitions_workspace_id_effective_from_effective_to"),
            "workspace_id",
            "effective_from",
            "effective_to",
        ),
        Index(
            (
                "ix_rule_definitions_workspace_id_"
                "company_id_brand_id_store_id_bank_account_id"
            ),
            "workspace_id",
            "company_id",
            "brand_id",
            "store_id",
            "bank_account_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    )

    rule_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    logic: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    effective_from: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    effective_to: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    output_coa_account_id: Mapped[UUID | None] = mapped_column(
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

    conditions: Mapped[list["RuleConditionRecord"]] = relationship(
        back_populates="rule",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RuleConditionRecord.condition_order",
    )


class RuleConditionRecord(Base):
    """
    Persist one ordered condition belonging to a rule definition.

    Condition order is zero-based to match Python tuple indexing:

    ``0, 1, 2, ...``

    Condition values are stored in typed columns rather than one generic
    JSON value. This preserves Decimal precision, date semantics, search
    capability, and database-level validation.
    """

    __tablename__ = "rule_conditions"

    __table_args__ = (
        ForeignKeyConstraint(
            [
                "workspace_id",
                "rule_id",
            ],
            [
                "rule_definitions.workspace_id",
                "rule_definitions.rule_id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "condition_order >= 0",
            name="non_negative_condition_order",
        ),
        CheckConstraint(
            (
                "field_name IN ("
                "'searchable_text', "
                "'normalized_description', "
                "'original_memo', "
                "'vendor_name', "
                "'direction', "
                "'absolute_amount', "
                "'signed_amount', "
                "'transaction_date'"
                ")"
            ),
            name="valid_field_name",
        ),
        CheckConstraint(
            (
                "operator_name IN ("
                "'contains', "
                "'not_contains', "
                "'equals', "
                "'not_equals', "
                "'starts_with', "
                "'ends_with', "
                "'greater_than', "
                "'greater_than_or_equal', "
                "'less_than', "
                "'less_than_or_equal', "
                "'between'"
                ")"
            ),
            name="valid_operator_name",
        ),
        CheckConstraint(
            (
                "value_type IN ("
                "'text', "
                "'direction', "
                "'decimal', "
                "'date', "
                "'decimal_range', "
                "'date_range'"
                ")"
            ),
            name="valid_value_type",
        ),
        CheckConstraint(
            ("direction_value IS NULL OR direction_value IN ('payment', 'deposit')"),
            name="valid_direction_value",
        ),
        CheckConstraint(
            ("text_value IS NULL OR length(trim(text_value)) > 0"),
            name="text_value_not_blank",
        ),
        CheckConstraint(
            (
                "decimal_lower_value IS NULL "
                "OR decimal_upper_value IS NULL "
                "OR decimal_lower_value <= decimal_upper_value"
            ),
            name="valid_decimal_range",
        ),
        CheckConstraint(
            (
                "date_lower_value IS NULL "
                "OR date_upper_value IS NULL "
                "OR date_lower_value <= date_upper_value"
            ),
            name="valid_date_range",
        ),
        CheckConstraint(
            (
                "field_name <> 'absolute_amount' "
                "OR ("
                "(decimal_value IS NULL OR decimal_value >= 0) "
                "AND ("
                "decimal_lower_value IS NULL "
                "OR decimal_lower_value >= 0"
                ") "
                "AND ("
                "decimal_upper_value IS NULL "
                "OR decimal_upper_value >= 0"
                ")"
                ")"
            ),
            name="absolute_amount_non_negative",
        ),
        CheckConstraint(
            (
                "("
                "value_type = 'text' "
                "AND text_value IS NOT NULL "
                "AND direction_value IS NULL "
                "AND decimal_value IS NULL "
                "AND date_value IS NULL "
                "AND decimal_lower_value IS NULL "
                "AND decimal_upper_value IS NULL "
                "AND date_lower_value IS NULL "
                "AND date_upper_value IS NULL"
                ") OR ("
                "value_type = 'direction' "
                "AND text_value IS NULL "
                "AND direction_value IS NOT NULL "
                "AND decimal_value IS NULL "
                "AND date_value IS NULL "
                "AND decimal_lower_value IS NULL "
                "AND decimal_upper_value IS NULL "
                "AND date_lower_value IS NULL "
                "AND date_upper_value IS NULL"
                ") OR ("
                "value_type = 'decimal' "
                "AND text_value IS NULL "
                "AND direction_value IS NULL "
                "AND decimal_value IS NOT NULL "
                "AND date_value IS NULL "
                "AND decimal_lower_value IS NULL "
                "AND decimal_upper_value IS NULL "
                "AND date_lower_value IS NULL "
                "AND date_upper_value IS NULL"
                ") OR ("
                "value_type = 'date' "
                "AND text_value IS NULL "
                "AND direction_value IS NULL "
                "AND decimal_value IS NULL "
                "AND date_value IS NOT NULL "
                "AND decimal_lower_value IS NULL "
                "AND decimal_upper_value IS NULL "
                "AND date_lower_value IS NULL "
                "AND date_upper_value IS NULL"
                ") OR ("
                "value_type = 'decimal_range' "
                "AND text_value IS NULL "
                "AND direction_value IS NULL "
                "AND decimal_value IS NULL "
                "AND date_value IS NULL "
                "AND decimal_lower_value IS NOT NULL "
                "AND decimal_upper_value IS NOT NULL "
                "AND date_lower_value IS NULL "
                "AND date_upper_value IS NULL"
                ") OR ("
                "value_type = 'date_range' "
                "AND text_value IS NULL "
                "AND direction_value IS NULL "
                "AND decimal_value IS NULL "
                "AND date_value IS NULL "
                "AND decimal_lower_value IS NULL "
                "AND decimal_upper_value IS NULL "
                "AND date_lower_value IS NOT NULL "
                "AND date_upper_value IS NOT NULL"
                ")"
            ),
            name="valid_value_shape",
        ),
        CheckConstraint(
            (
                "("
                "field_name IN ("
                "'searchable_text', "
                "'normalized_description', "
                "'original_memo', "
                "'vendor_name'"
                ") "
                "AND operator_name IN ("
                "'contains', "
                "'not_contains', "
                "'equals', "
                "'not_equals', "
                "'starts_with', "
                "'ends_with'"
                ") "
                "AND value_type = 'text'"
                ") OR ("
                "field_name = 'direction' "
                "AND operator_name IN ('equals', 'not_equals') "
                "AND value_type = 'direction'"
                ") OR ("
                "field_name IN ('absolute_amount', 'signed_amount') "
                "AND operator_name IN ("
                "'equals', "
                "'not_equals', "
                "'greater_than', "
                "'greater_than_or_equal', "
                "'less_than', "
                "'less_than_or_equal', "
                "'between'"
                ") "
                "AND ("
                "("
                "operator_name = 'between' "
                "AND value_type = 'decimal_range'"
                ") OR ("
                "operator_name <> 'between' "
                "AND value_type = 'decimal'"
                ")"
                ")"
                ") OR ("
                "field_name = 'transaction_date' "
                "AND operator_name IN ("
                "'equals', "
                "'not_equals', "
                "'greater_than', "
                "'greater_than_or_equal', "
                "'less_than', "
                "'less_than_or_equal', "
                "'between'"
                ") "
                "AND ("
                "("
                "operator_name = 'between' "
                "AND value_type = 'date_range'"
                ") OR ("
                "operator_name <> 'between' "
                "AND value_type = 'date'"
                ")"
                ")"
                ")"
            ),
            name="compatible_field_operator_value",
        ),
        Index(
            ("ix_rule_conditions_workspace_id_field_name_operator_name"),
            "workspace_id",
            "field_name",
            "operator_name",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    )

    rule_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    )

    condition_order: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        nullable=False,
    )

    field_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    operator_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    value_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    text_value: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    direction_value: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
    )

    decimal_value: Mapped[Decimal | None] = mapped_column(
        Numeric(
            precision=RULE_AMOUNT_PRECISION,
            scale=RULE_AMOUNT_SCALE,
            asdecimal=True,
        ),
        nullable=True,
    )

    date_value: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    decimal_lower_value: Mapped[Decimal | None] = mapped_column(
        Numeric(
            precision=RULE_AMOUNT_PRECISION,
            scale=RULE_AMOUNT_SCALE,
            asdecimal=True,
        ),
        nullable=True,
    )

    decimal_upper_value: Mapped[Decimal | None] = mapped_column(
        Numeric(
            precision=RULE_AMOUNT_PRECISION,
            scale=RULE_AMOUNT_SCALE,
            asdecimal=True,
        ),
        nullable=True,
    )

    date_lower_value: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    date_upper_value: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    rule: Mapped[RuleRecord] = relationship(
        back_populates="conditions",
    )


__all__ = [
    "RULE_AMOUNT_PRECISION",
    "RULE_AMOUNT_SCALE",
    "RuleConditionRecord",
    "RuleRecord",
]
