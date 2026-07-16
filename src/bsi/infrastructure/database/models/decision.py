"""
SQLAlchemy persistence model for deterministic rule-engine decisions.

This module stores the latest authoritative deterministic decision for
one workspace-owned transaction.

The model separates:

- Frequently queried decision-summary columns
- UUID collections for matched and top-ranked rules
- Nested JSONB evaluation evidence for audit review

Rule evaluation, ranking, conflict detection, and accounting decisions
remain responsibilities of the framework-independent domain layer.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bsi.infrastructure.database.base import Base
from bsi.infrastructure.database.models.rule import RuleRecord
from bsi.infrastructure.database.models.transaction import (
    TransactionRecord,
)

type RuleEvaluationEvidence = dict[str, object]
"""One JSON-compatible serialized rule evaluation."""


class RuleDecisionRecord(Base):
    """
    Persist the latest deterministic decision for one transaction.

    The composite primary key is:

    ``workspace_id`` + ``transaction_id``

    This matches the current application writer contract, where
    reprocessing replaces the latest decision for the transaction.

    Notes
    -----
    The nested evaluation evidence is stored as an immutable JSONB
    snapshot because it represents what the engine observed during that
    execution. It is not used as the authoritative rule definition.

    A future processing-run and decision-history model may preserve
    multiple historical decision versions while this table continues to
    provide the latest transaction decision.
    """

    __tablename__ = "rule_decisions"

    __table_args__ = (
        ForeignKeyConstraint(
            [
                "workspace_id",
                "transaction_id",
            ],
            [
                (f"{TransactionRecord.__tablename__}.workspace_id"),
                (f"{TransactionRecord.__tablename__}.transaction_id"),
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "winning_rule_id",
            ],
            [
                f"{RuleRecord.__tablename__}.workspace_id",
                f"{RuleRecord.__tablename__}.rule_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            (
                "status IN ("
                "'unmatched', "
                "'mapped', "
                "'mapped_with_review', "
                "'blocked_conflict'"
                ")"
            ),
            name="valid_status",
        ),
        CheckConstraint(
            ("conflict_kind IN ('none', 'redundant_same_output', 'competing_outputs')"),
            name="valid_conflict_kind",
        ),
        CheckConstraint(
            "length(trim(decision_message)) > 0",
            name="decision_message_not_blank",
        ),
        CheckConstraint(
            (
                "evaluated_rule_count >= 0 "
                "AND eligible_rule_count >= 0 "
                "AND ineligible_rule_count >= 0 "
                "AND matched_rule_count >= 0 "
                "AND unmatched_eligible_rule_count >= 0"
            ),
            name="non_negative_counts",
        ),
        CheckConstraint(
            ("evaluated_rule_count = eligible_rule_count + ineligible_rule_count"),
            name="evaluated_count_consistent",
        ),
        CheckConstraint(
            (
                "eligible_rule_count = "
                "matched_rule_count + "
                "unmatched_eligible_rule_count"
            ),
            name="eligible_count_consistent",
        ),
        CheckConstraint(
            ("cardinality(matched_rule_ids) = matched_rule_count"),
            name="matched_rule_ids_count_consistent",
        ),
        CheckConstraint(
            (
                "jsonb_typeof(evaluations) = 'array' "
                "AND jsonb_array_length(evaluations) = "
                "evaluated_rule_count"
            ),
            name="evaluation_evidence_count_consistent",
        ),
        CheckConstraint(
            "top_rule_ids <@ matched_rule_ids",
            name="top_rules_are_matched_rules",
        ),
        CheckConstraint(
            (
                "("
                "status = 'unmatched' "
                "AND conflict_kind = 'none' "
                "AND can_map = FALSE "
                "AND requires_review = TRUE "
                "AND is_conflict_blocked = FALSE "
                "AND output_account_id IS NULL "
                "AND winning_rule_id IS NULL"
                ") OR ("
                "status = 'mapped' "
                "AND conflict_kind = 'none' "
                "AND can_map = TRUE "
                "AND requires_review = FALSE "
                "AND is_conflict_blocked = FALSE "
                "AND output_account_id IS NOT NULL "
                "AND winning_rule_id IS NOT NULL"
                ") OR ("
                "status = 'mapped_with_review' "
                "AND conflict_kind = 'redundant_same_output' "
                "AND can_map = TRUE "
                "AND requires_review = TRUE "
                "AND is_conflict_blocked = FALSE "
                "AND output_account_id IS NOT NULL "
                "AND winning_rule_id IS NULL"
                ") OR ("
                "status = 'blocked_conflict' "
                "AND conflict_kind = 'competing_outputs' "
                "AND can_map = FALSE "
                "AND requires_review = TRUE "
                "AND is_conflict_blocked = TRUE "
                "AND output_account_id IS NULL "
                "AND winning_rule_id IS NULL"
                ")"
            ),
            name="decision_state_consistent",
        ),
        Index(
            ("ix_rule_decisions_workspace_id_status_requires_review"),
            "workspace_id",
            "status",
            "requires_review",
        ),
        Index(
            ("ix_rule_decisions_workspace_id_output_account_id"),
            "workspace_id",
            "output_account_id",
        ),
        Index(
            ("ix_rule_decisions_workspace_id_winning_rule_id"),
            "workspace_id",
            "winning_rule_id",
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

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    conflict_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    can_map: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    requires_review: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    is_conflict_blocked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    output_account_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )

    winning_rule_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )

    matched_rule_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(
            Uuid(as_uuid=True),
        ),
        nullable=False,
    )

    top_rule_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(
            Uuid(as_uuid=True),
        ),
        nullable=False,
    )

    evaluated_rule_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    eligible_rule_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    ineligible_rule_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    matched_rule_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    unmatched_eligible_rule_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    decision_message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    evaluations: Mapped[list[RuleEvaluationEvidence]] = mapped_column(
        JSONB,
        nullable=False,
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
    "RuleDecisionRecord",
    "RuleEvaluationEvidence",
]
