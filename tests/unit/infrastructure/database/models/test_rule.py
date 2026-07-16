"""
Unit tests for deterministic-rule SQLAlchemy persistence models.

These tests verify:

- Rule-definition and condition table registration
- Workspace-scoped composite identities
- Required and optional columns
- UUID, date, text, integer, and numeric database types
- Financial precision for amount conditions
- Rule and condition check constraints
- Tenant-scoped query indexes
- Parent-child foreign-key behavior
- Audit timestamp configuration

These are schema-contract tests and do not require PostgreSQL.
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
from sqlalchemy.sql.schema import (
    Column,
    ForeignKeyConstraint,
    Index,
    Table,
)

from bsi.infrastructure.database.base import Base
from bsi.infrastructure.database.models.rule import (
    RULE_AMOUNT_PRECISION,
    RULE_AMOUNT_SCALE,
    RuleConditionRecord,
    RuleRecord,
)


def _rule_table() -> Table:
    """Return the authoritative rule-definition table."""

    return cast(Table, RuleRecord.__table__)


def _condition_table() -> Table:
    """Return the authoritative rule-condition table."""

    return cast(Table, RuleConditionRecord.__table__)


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


@pytest.mark.unit
def test_rule_models_inherit_shared_base() -> None:
    """Both ORM models must use the authoritative metadata registry."""

    assert issubclass(RuleRecord, Base)
    assert issubclass(RuleConditionRecord, Base)

    assert RuleRecord.metadata is Base.metadata
    assert RuleConditionRecord.metadata is Base.metadata


@pytest.mark.unit
def test_rule_tables_use_authoritative_names() -> None:
    """Rule table names must remain stable for migrations and queries."""

    rule_table = _rule_table()
    condition_table = _condition_table()

    assert rule_table.name == "rule_definitions"
    assert condition_table.name == "rule_conditions"

    assert Base.metadata.tables["rule_definitions"] is rule_table
    assert Base.metadata.tables["rule_conditions"] is condition_table


@pytest.mark.unit
def test_rule_definition_contains_expected_columns() -> None:
    """The rule table should persist all rule-definition facts."""

    assert _column_names(_rule_table()) == (
        "workspace_id",
        "rule_id",
        "name",
        "description",
        "logic",
        "status",
        "priority",
        "version",
        "effective_from",
        "effective_to",
        "output_coa_account_id",
        "company_id",
        "brand_id",
        "store_id",
        "bank_account_id",
        "created_at",
        "updated_at",
    )


@pytest.mark.unit
def test_rule_condition_contains_expected_columns() -> None:
    """The condition table should preserve typed condition values."""

    assert _column_names(_condition_table()) == (
        "workspace_id",
        "rule_id",
        "condition_order",
        "field_name",
        "operator_name",
        "value_type",
        "text_value",
        "direction_value",
        "decimal_value",
        "date_value",
        "decimal_lower_value",
        "decimal_upper_value",
        "date_lower_value",
        "date_upper_value",
    )


@pytest.mark.unit
def test_rule_definition_uses_composite_primary_key() -> None:
    """Workspace and rule identifiers must form the rule identity."""

    table = _rule_table()

    primary_key_columns = tuple(column.name for column in table.primary_key.columns)

    assert primary_key_columns == (
        "workspace_id",
        "rule_id",
    )
    assert table.primary_key.name == "pk_rule_definitions"


@pytest.mark.unit
def test_rule_condition_uses_composite_primary_key() -> None:
    """Condition order must be unique within each workspace rule."""

    table = _condition_table()

    primary_key_columns = tuple(column.name for column in table.primary_key.columns)

    assert primary_key_columns == (
        "workspace_id",
        "rule_id",
        "condition_order",
    )
    assert table.primary_key.name == "pk_rule_conditions"


@pytest.mark.unit
def test_required_rule_columns_are_not_nullable() -> None:
    """Authoritative rule-definition fields must always be present."""

    table = _rule_table()

    required_column_names = {
        "workspace_id",
        "rule_id",
        "name",
        "logic",
        "status",
        "priority",
        "version",
        "created_at",
        "updated_at",
    }

    for column_name in required_column_names:
        assert table.c[column_name].nullable is False


@pytest.mark.unit
def test_optional_rule_columns_are_nullable() -> None:
    """Draft outputs, effective dates, descriptions, and scope may be absent."""

    table = _rule_table()

    optional_column_names = {
        "description",
        "effective_from",
        "effective_to",
        "output_coa_account_id",
        "company_id",
        "brand_id",
        "store_id",
        "bank_account_id",
    }

    for column_name in optional_column_names:
        assert table.c[column_name].nullable is True


@pytest.mark.unit
def test_required_condition_columns_are_not_nullable() -> None:
    """Condition identity and type information must always be present."""

    table = _condition_table()

    required_column_names = {
        "workspace_id",
        "rule_id",
        "condition_order",
        "field_name",
        "operator_name",
        "value_type",
    }

    for column_name in required_column_names:
        assert table.c[column_name].nullable is False


@pytest.mark.unit
def test_typed_condition_value_columns_are_nullable() -> None:
    """
    Individual typed value columns are nullable.

    The valid-value-shape constraint determines which exact typed
    column or column pair must be populated for each condition.
    """

    table = _condition_table()

    optional_value_columns = {
        "text_value",
        "direction_value",
        "decimal_value",
        "date_value",
        "decimal_lower_value",
        "decimal_upper_value",
        "date_lower_value",
        "date_upper_value",
    }

    for column_name in optional_value_columns:
        assert table.c[column_name].nullable is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "column_name",
    [
        "workspace_id",
        "rule_id",
        "output_coa_account_id",
        "company_id",
        "brand_id",
        "store_id",
        "bank_account_id",
    ],
)
def test_rule_identifier_columns_use_uuid_type(
    column_name: str,
) -> None:
    """Rule ownership, output, and scope identifiers use UUID semantics."""

    column_type = _rule_table().c[column_name].type

    assert isinstance(column_type, Uuid)
    assert column_type.as_uuid is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "column_name",
    [
        "workspace_id",
        "rule_id",
    ],
)
def test_condition_identifier_columns_use_uuid_type(
    column_name: str,
) -> None:
    """Condition ownership identifiers must retain UUID semantics."""

    column_type = _condition_table().c[column_name].type

    assert isinstance(column_type, Uuid)
    assert column_type.as_uuid is True


@pytest.mark.unit
def test_rule_text_columns_use_expected_types_and_lengths() -> None:
    """Rule display and controlled-value fields use appropriate types."""

    table = _rule_table()

    assert isinstance(table.c.name.type, String)
    assert table.c.name.type.length == 255

    assert isinstance(table.c.description.type, Text)

    assert isinstance(table.c.logic.type, String)
    assert table.c.logic.type.length == 8

    assert isinstance(table.c.status.type, String)
    assert table.c.status.type.length == 32


@pytest.mark.unit
def test_condition_text_columns_use_expected_types_and_lengths() -> None:
    """Condition metadata and typed text values use appropriate types."""

    table = _condition_table()

    expected_lengths = {
        "field_name": 64,
        "operator_name": 64,
        "value_type": 32,
        "direction_value": 16,
    }

    for column_name, expected_length in expected_lengths.items():
        column_type = table.c[column_name].type

        assert isinstance(column_type, String)
        assert column_type.length == expected_length

    assert isinstance(table.c.text_value.type, Text)


@pytest.mark.unit
def test_rule_effective_dates_use_date_without_time() -> None:
    """Rule effective boundaries should not store time components."""

    table = _rule_table()

    for column_name in (
        "effective_from",
        "effective_to",
    ):
        column_type = table.c[column_name].type

        assert isinstance(column_type, Date)
        assert not isinstance(column_type, DateTime)


@pytest.mark.unit
def test_condition_date_values_use_date_without_time() -> None:
    """Scalar and range condition dates should use database DATE."""

    table = _condition_table()

    for column_name in (
        "date_value",
        "date_lower_value",
        "date_upper_value",
    ):
        column_type = table.c[column_name].type

        assert isinstance(column_type, Date)
        assert not isinstance(column_type, DateTime)


@pytest.mark.unit
@pytest.mark.parametrize(
    "column_name",
    [
        "decimal_value",
        "decimal_lower_value",
        "decimal_upper_value",
    ],
)
def test_condition_amount_columns_use_exact_precision(
    column_name: str,
) -> None:
    """Condition amounts must use exact fixed-point numeric storage."""

    column_type = _condition_table().c[column_name].type

    assert isinstance(column_type, Numeric)
    assert column_type.precision == RULE_AMOUNT_PRECISION
    assert column_type.scale == RULE_AMOUNT_SCALE
    assert column_type.asdecimal is True


@pytest.mark.unit
def test_integer_columns_use_integer_type() -> None:
    """Priority, version, and condition order should use integers."""

    rule_table = _rule_table()
    condition_table = _condition_table()

    assert isinstance(rule_table.c.priority.type, Integer)
    assert isinstance(rule_table.c.version.type, Integer)
    assert isinstance(condition_table.c.condition_order.type, Integer)


@pytest.mark.unit
def test_rule_definition_check_constraints_are_registered() -> None:
    """Database constraints should protect rule lifecycle invariants."""

    constraint_names = {
        constraint.name for constraint in _check_constraints(_rule_table())
    }

    assert constraint_names == {
        "ck_rule_definitions_name_not_blank",
        "ck_rule_definitions_non_draft_requires_output",
        "ck_rule_definitions_positive_version",
        "ck_rule_definitions_priority_in_range",
        "ck_rule_definitions_valid_effective_date_range",
        "ck_rule_definitions_valid_logic",
        "ck_rule_definitions_valid_status",
    }


@pytest.mark.unit
def test_rule_condition_check_constraints_are_registered() -> None:
    """Condition constraints should protect field/operator/value integrity."""

    constraint_names = {
        constraint.name for constraint in _check_constraints(_condition_table())
    }

    assert constraint_names == {
        "ck_rule_conditions_absolute_amount_non_negative",
        "ck_rule_conditions_compatible_field_operator_value",
        "ck_rule_conditions_non_negative_condition_order",
        "ck_rule_conditions_text_value_not_blank",
        "ck_rule_conditions_valid_date_range",
        "ck_rule_conditions_valid_decimal_range",
        "ck_rule_conditions_valid_direction_value",
        "ck_rule_conditions_valid_field_name",
        "ck_rule_conditions_valid_operator_name",
        "ck_rule_conditions_valid_value_shape",
        "ck_rule_conditions_valid_value_type",
    }


@pytest.mark.unit
def test_rule_definition_indexes_are_registered() -> None:
    """Rule lookup indexes should support status, dates, and scope."""

    indexes = _indexes_by_name(_rule_table())

    assert set(indexes) == {
        (
            "ix_rule_definitions_workspace_id_"
            "company_id_brand_id_store_id_bank_account_id"
        ),
        ("ix_rule_definitions_workspace_id_effective_from_effective_to"),
        "ix_rule_definitions_workspace_id_status_priority",
    }


@pytest.mark.unit
def test_rule_definition_indexes_use_expected_column_order() -> None:
    """Every composite index should begin with workspace ownership."""

    indexes = _indexes_by_name(_rule_table())

    expected_columns = {
        "ix_rule_definitions_workspace_id_status_priority": (
            "workspace_id",
            "status",
            "priority",
        ),
        ("ix_rule_definitions_workspace_id_effective_from_effective_to"): (
            "workspace_id",
            "effective_from",
            "effective_to",
        ),
        (
            "ix_rule_definitions_workspace_id_"
            "company_id_brand_id_store_id_bank_account_id"
        ): (
            "workspace_id",
            "company_id",
            "brand_id",
            "store_id",
            "bank_account_id",
        ),
    }

    for index_name, expected_index_columns in expected_columns.items():
        actual_columns = tuple(column.name for column in indexes[index_name].columns)

        assert actual_columns == expected_index_columns


@pytest.mark.unit
def test_rule_condition_index_is_registered() -> None:
    """Condition searches should use tenant, field, and operator."""

    indexes = _indexes_by_name(_condition_table())

    assert set(indexes) == {
        ("ix_rule_conditions_workspace_id_field_name_operator_name")
    }


@pytest.mark.unit
def test_rule_condition_index_uses_expected_column_order() -> None:
    """The condition index must begin with the workspace boundary."""

    indexes = _indexes_by_name(_condition_table())

    index = indexes[("ix_rule_conditions_workspace_id_field_name_operator_name")]

    actual_columns = tuple(column.name for column in index.columns)

    assert actual_columns == (
        "workspace_id",
        "field_name",
        "operator_name",
    )


@pytest.mark.unit
def test_rule_condition_has_cascading_composite_foreign_key() -> None:
    """Deleting a rule should remove its persisted child conditions."""

    foreign_key_constraints = tuple(
        constraint
        for constraint in _condition_table().constraints
        if isinstance(constraint, ForeignKeyConstraint)
    )

    assert len(foreign_key_constraints) == 1

    foreign_key = foreign_key_constraints[0]

    assert foreign_key.name == "fk_rule_conditions_workspace_id_rule_definitions"
    assert foreign_key.ondelete == "CASCADE"

    local_columns = tuple(element.parent.name for element in foreign_key.elements)
    referenced_columns = tuple(
        element.target_fullname for element in foreign_key.elements
    )

    assert local_columns == (
        "workspace_id",
        "rule_id",
    )
    assert referenced_columns == (
        "rule_definitions.workspace_id",
        "rule_definitions.rule_id",
    )


@pytest.mark.unit
def test_rule_audit_columns_use_timezone_aware_timestamps() -> None:
    """Rule creation and update timestamps should preserve timezone."""

    table = _rule_table()

    for column_name in (
        "created_at",
        "updated_at",
    ):
        column_type = table.c[column_name].type

        assert isinstance(column_type, DateTime)
        assert column_type.timezone is True


@pytest.mark.unit
def test_rule_created_at_has_server_default() -> None:
    """The database should assign rule creation timestamps."""

    created_at_column: Column[object] = _rule_table().c.created_at

    assert created_at_column.server_default is not None


@pytest.mark.unit
def test_rule_updated_at_has_default_and_update_rule() -> None:
    """Rule update timestamps require insert and update behavior."""

    updated_at_column: Column[object] = _rule_table().c.updated_at

    assert updated_at_column.server_default is not None
    assert updated_at_column.onupdate is not None
