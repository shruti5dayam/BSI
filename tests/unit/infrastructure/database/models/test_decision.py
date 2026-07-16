"""
Unit tests for the rule-engine decision SQLAlchemy model.

These tests verify:

- Decision-table registration
- Workspace-scoped transaction identity
- Required and optional columns
- UUID, boolean, integer, text, array, and JSONB types
- Decision-state database constraints
- Transaction and winning-rule foreign keys
- Tenant-scoped query indexes
- Audit timestamp configuration

These are schema-contract tests and do not require PostgreSQL.
"""

from collections.abc import Mapping
from typing import cast

import pytest
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
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.sql.schema import Column, Table

from bsi.infrastructure.database.base import Base
from bsi.infrastructure.database.models.decision import (
    RuleDecisionRecord,
)


def _table() -> Table:
    """Return the authoritative rule-decision table."""

    return cast(Table, RuleDecisionRecord.__table__)


def _column_names(table: Table) -> tuple[str, ...]:
    """Return table columns in declared schema order."""

    return tuple(column.name for column in table.columns)


def _check_constraints(
    table: Table,
) -> tuple[CheckConstraint, ...]:
    """Return every check constraint registered on a table."""

    return tuple(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    )


def _indexes_by_name(
    table: Table,
) -> Mapping[str, Index]:
    """Return indexes keyed by stable database names."""

    return {str(index.name): index for index in table.indexes}


def _foreign_keys_by_name(
    table: Table,
) -> Mapping[str, ForeignKeyConstraint]:
    """Return foreign-key constraints keyed by stable names."""

    return {
        str(constraint.name): constraint for constraint in table.foreign_key_constraints
    }


@pytest.mark.unit
def test_decision_record_inherits_shared_base() -> None:
    """The decision model must use the shared metadata registry."""

    assert issubclass(RuleDecisionRecord, Base)
    assert RuleDecisionRecord.metadata is Base.metadata


@pytest.mark.unit
def test_decision_table_uses_authoritative_name() -> None:
    """The decision table name must remain stable."""

    table = _table()

    assert table.name == "rule_decisions"
    assert Base.metadata.tables["rule_decisions"] is table


@pytest.mark.unit
def test_decision_table_contains_expected_columns() -> None:
    """The table should preserve summary and audit evidence."""

    assert _column_names(_table()) == (
        "workspace_id",
        "transaction_id",
        "status",
        "conflict_kind",
        "can_map",
        "requires_review",
        "is_conflict_blocked",
        "output_account_id",
        "winning_rule_id",
        "matched_rule_ids",
        "top_rule_ids",
        "evaluated_rule_count",
        "eligible_rule_count",
        "ineligible_rule_count",
        "matched_rule_count",
        "unmatched_eligible_rule_count",
        "decision_message",
        "evaluations",
        "created_at",
        "updated_at",
    )


@pytest.mark.unit
def test_decision_table_uses_composite_primary_key() -> None:
    """Workspace and transaction IDs must form the decision identity."""

    table = _table()

    primary_key_columns = tuple(column.name for column in table.primary_key.columns)

    assert primary_key_columns == (
        "workspace_id",
        "transaction_id",
    )
    assert table.primary_key.name == "pk_rule_decisions"


@pytest.mark.unit
def test_required_decision_columns_are_not_nullable() -> None:
    """Every authoritative decision summary field must be present."""

    table = _table()

    required_column_names = {
        "workspace_id",
        "transaction_id",
        "status",
        "conflict_kind",
        "can_map",
        "requires_review",
        "is_conflict_blocked",
        "matched_rule_ids",
        "top_rule_ids",
        "evaluated_rule_count",
        "eligible_rule_count",
        "ineligible_rule_count",
        "matched_rule_count",
        "unmatched_eligible_rule_count",
        "decision_message",
        "evaluations",
        "created_at",
        "updated_at",
    }

    for column_name in required_column_names:
        assert table.c[column_name].nullable is False


@pytest.mark.unit
def test_mapping_output_and_winning_rule_are_nullable() -> None:
    """Unmatched and conflict decisions may not contain mapping IDs."""

    table = _table()

    assert table.c.output_account_id.nullable is True
    assert table.c.winning_rule_id.nullable is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "column_name",
    [
        "workspace_id",
        "transaction_id",
        "output_account_id",
        "winning_rule_id",
    ],
)
def test_identifier_columns_use_uuid_type(
    column_name: str,
) -> None:
    """Decision identifiers should retain UUID semantics."""

    column_type = _table().c[column_name].type

    assert isinstance(column_type, Uuid)
    assert column_type.as_uuid is True


@pytest.mark.unit
def test_status_columns_use_expected_string_lengths() -> None:
    """Decision status and conflict type use bounded strings."""

    table = _table()

    assert isinstance(table.c.status.type, String)
    assert table.c.status.type.length == 32

    assert isinstance(table.c.conflict_kind.type, String)
    assert table.c.conflict_kind.type.length == 32


@pytest.mark.unit
@pytest.mark.parametrize(
    "column_name",
    [
        "can_map",
        "requires_review",
        "is_conflict_blocked",
    ],
)
def test_decision_flags_use_boolean_type(
    column_name: str,
) -> None:
    """Mapping and review flags should use database booleans."""

    column_type = _table().c[column_name].type

    assert isinstance(column_type, Boolean)


@pytest.mark.unit
@pytest.mark.parametrize(
    "column_name",
    [
        "evaluated_rule_count",
        "eligible_rule_count",
        "ineligible_rule_count",
        "matched_rule_count",
        "unmatched_eligible_rule_count",
    ],
)
def test_decision_counters_use_integer_type(
    column_name: str,
) -> None:
    """Decision counters should use integer columns."""

    column_type = _table().c[column_name].type

    assert isinstance(column_type, Integer)


@pytest.mark.unit
def test_decision_message_uses_unbounded_text() -> None:
    """Audit explanations should not be truncated."""

    assert isinstance(
        _table().c.decision_message.type,
        Text,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "column_name",
    [
        "matched_rule_ids",
        "top_rule_ids",
    ],
)
def test_rule_id_collections_use_postgresql_uuid_arrays(
    column_name: str,
) -> None:
    """Rule-ID collections should use PostgreSQL UUID arrays."""

    column_type = _table().c[column_name].type

    assert isinstance(column_type, ARRAY)
    assert isinstance(column_type.item_type, Uuid)
    assert column_type.item_type.as_uuid is True


@pytest.mark.unit
def test_evaluations_use_postgresql_jsonb() -> None:
    """Nested immutable evaluation evidence should use JSONB."""

    column_type = _table().c.evaluations.type

    assert isinstance(column_type, JSONB)


@pytest.mark.unit
def test_decision_check_constraints_are_registered() -> None:
    """Database constraints should protect decision consistency."""

    constraint_names = {constraint.name for constraint in _check_constraints(_table())}

    assert constraint_names == {
        "ck_rule_decisions_decision_message_not_blank",
        "ck_rule_decisions_decision_state_consistent",
        "ck_rule_decisions_eligible_count_consistent",
        "ck_rule_decisions_evaluated_count_consistent",
        "ck_rule_decisions_evaluation_evidence_count_consistent",
        "ck_rule_decisions_matched_rule_ids_count_consistent",
        "ck_rule_decisions_non_negative_counts",
        "ck_rule_decisions_top_rules_are_matched_rules",
        "ck_rule_decisions_valid_conflict_kind",
        "ck_rule_decisions_valid_status",
    }


@pytest.mark.unit
def test_decision_indexes_are_registered() -> None:
    """Decision queries should support review and mapping workflows."""

    indexes = _indexes_by_name(_table())

    assert set(indexes) == {
        "ix_rule_decisions_workspace_id_output_account_id",
        "ix_rule_decisions_workspace_id_status_requires_review",
        "ix_rule_decisions_workspace_id_winning_rule_id",
    }


@pytest.mark.unit
def test_decision_indexes_use_expected_column_order() -> None:
    """Every composite index should begin with workspace ownership."""

    indexes = _indexes_by_name(_table())

    expected_columns = {
        "ix_rule_decisions_workspace_id_status_requires_review": (
            "workspace_id",
            "status",
            "requires_review",
        ),
        "ix_rule_decisions_workspace_id_output_account_id": (
            "workspace_id",
            "output_account_id",
        ),
        "ix_rule_decisions_workspace_id_winning_rule_id": (
            "workspace_id",
            "winning_rule_id",
        ),
    }

    for index_name, expected_index_columns in expected_columns.items():
        actual_columns = tuple(column.name for column in indexes[index_name].columns)

        assert actual_columns == expected_index_columns


@pytest.mark.unit
def test_decision_foreign_keys_are_registered() -> None:
    """The decision should reference its transaction and winning rule."""

    foreign_keys = _foreign_keys_by_name(_table())

    assert set(foreign_keys) == {
        "fk_rule_decisions_workspace_id_rule_definitions",
        "fk_rule_decisions_workspace_id_transactions",
    }


@pytest.mark.unit
def test_decision_has_cascading_transaction_foreign_key() -> None:
    """Deleting a transaction should remove its latest decision."""

    foreign_key = _foreign_keys_by_name(_table())[
        "fk_rule_decisions_workspace_id_transactions"
    ]

    assert foreign_key.ondelete == "CASCADE"

    local_columns = tuple(element.parent.name for element in foreign_key.elements)
    referenced_columns = tuple(
        element.target_fullname for element in foreign_key.elements
    )

    assert local_columns == (
        "workspace_id",
        "transaction_id",
    )
    assert referenced_columns == (
        "transactions.workspace_id",
        "transactions.transaction_id",
    )


@pytest.mark.unit
def test_decision_has_restricted_winning_rule_foreign_key() -> None:
    """A winning rule should not be deleted while referenced."""

    foreign_key = _foreign_keys_by_name(_table())[
        "fk_rule_decisions_workspace_id_rule_definitions"
    ]

    assert foreign_key.ondelete == "RESTRICT"

    local_columns = tuple(element.parent.name for element in foreign_key.elements)
    referenced_columns = tuple(
        element.target_fullname for element in foreign_key.elements
    )

    assert local_columns == (
        "workspace_id",
        "winning_rule_id",
    )
    assert referenced_columns == (
        "rule_definitions.workspace_id",
        "rule_definitions.rule_id",
    )


@pytest.mark.unit
def test_decision_audit_columns_use_timezone_aware_timestamps() -> None:
    """Decision timestamps should retain timezone context."""

    table = _table()

    for column_name in (
        "created_at",
        "updated_at",
    ):
        column_type = table.c[column_name].type

        assert isinstance(column_type, DateTime)
        assert column_type.timezone is True


@pytest.mark.unit
def test_decision_created_at_has_server_default() -> None:
    """The database should assign the decision creation timestamp."""

    created_at_column: Column[object] = _table().c.created_at

    assert created_at_column.server_default is not None


@pytest.mark.unit
def test_decision_updated_at_has_default_and_update_rule() -> None:
    """Updated decisions require insert and update timestamp behavior."""

    updated_at_column: Column[object] = _table().c.updated_at

    assert updated_at_column.server_default is not None
    assert updated_at_column.onupdate is not None
