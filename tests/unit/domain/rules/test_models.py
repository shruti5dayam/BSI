"""
Unit tests for core deterministic rule-domain models.

These tests verify:

- Stable rule and COA identifiers
- Draft and non-draft validation
- Rule name and description normalization
- Condition and output requirements
- Priority and version boundaries
- Effective-date behavior
- Scope metadata
- Rule completeness and evaluability
- Domain immutability
"""

from dataclasses import FrozenInstanceError
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from bsi.domain.rules.conditions import RuleCondition
from bsi.domain.rules.enums import (
    RuleConditionField,
    RuleLogic,
    RuleOperator,
    RuleStatus,
)
from bsi.domain.rules.models import (
    DEFAULT_RULE_PRIORITY,
    MAX_RULE_PRIORITY,
    MIN_RULE_PRIORITY,
    RuleDefinition,
    RuleModelValidationError,
    RuleOutput,
)
from bsi.domain.rules.scope import RuleScope


def _invalid(value: object) -> Any:
    """
    Bypass static typing for intentional runtime-validation tests.

    Production adapters should convert raw values before constructing
    domain models. These tests verify that invalid runtime values are
    rejected if they reach the domain boundary.
    """

    return cast(Any, value)


def _condition(
    value: str = "utilities",
) -> RuleCondition:
    """Create a valid reusable text condition."""

    return RuleCondition(
        field=RuleConditionField.SEARCHABLE_TEXT,
        operator=RuleOperator.CONTAINS,
        value=value,
    )


def _output() -> RuleOutput:
    """Create a valid reusable rule output."""

    return RuleOutput(coa_account_id=uuid4())


def _complete_rule(
    *,
    status: RuleStatus = RuleStatus.ACTIVE,
    priority: int = DEFAULT_RULE_PRIORITY,
    effective_from: date | None = None,
    effective_to: date | None = None,
    scope: RuleScope | None = None,
) -> RuleDefinition:
    """Create a complete valid rule for behavior-focused tests."""

    return RuleDefinition.create(
        workspace_id=uuid4(),
        name="Utility Payments",
        conditions=(_condition(),),
        output=_output(),
        status=status,
        priority=priority,
        effective_from=effective_from,
        effective_to=effective_to,
        scope=scope,
    )


def test_rule_output_accepts_coa_account_uuid() -> None:
    """RuleOutput stores the authoritative COA account identifier."""

    coa_account_id = uuid4()

    output = RuleOutput(coa_account_id=coa_account_id)

    assert output.coa_account_id == coa_account_id


@pytest.mark.parametrize(
    "invalid_identifier",
    [
        "not-a-uuid",
        123,
        True,
        None,
    ],
)
def test_rule_output_rejects_invalid_coa_identifier(
    invalid_identifier: object,
) -> None:
    """A mapping output must reference a valid COA UUID."""

    with pytest.raises(
        RuleModelValidationError,
        match="coa_account_id must be a UUID",
    ):
        RuleOutput(
            coa_account_id=_invalid(invalid_identifier),
        )


def test_create_generates_rule_identifier() -> None:
    """The factory creates a UUID when rule_id is omitted."""

    rule = RuleDefinition.create(
        workspace_id=uuid4(),
        name="Draft Rule",
    )

    assert isinstance(rule.rule_id, UUID)


def test_create_preserves_supplied_rule_identifier() -> None:
    """Adapters may provide an existing identifier during reconstruction."""

    rule_id = uuid4()

    rule = RuleDefinition.create(
        rule_id=rule_id,
        workspace_id=uuid4(),
        name="Existing Rule",
    )

    assert rule.rule_id == rule_id


def test_draft_rule_uses_expected_defaults() -> None:
    """A newly created draft uses safe deterministic defaults."""

    workspace_id = uuid4()

    rule = RuleDefinition.create(
        workspace_id=workspace_id,
        name="Draft Rule",
    )

    assert rule.workspace_id == workspace_id
    assert rule.logic is RuleLogic.ALL
    assert rule.conditions == ()
    assert rule.output is None
    assert rule.scope == RuleScope()
    assert rule.status is RuleStatus.DRAFT
    assert rule.priority == DEFAULT_RULE_PRIORITY
    assert rule.version == 1
    assert rule.effective_from is None
    assert rule.effective_to is None
    assert rule.description is None


def test_rule_normalizes_name_and_description() -> None:
    """Human-readable text is cleaned while preserving letter casing."""

    rule = RuleDefinition.create(
        workspace_id=uuid4(),
        name="  Utility   Payment   Rule ",
        description="  Maps   recurring utility payments. ",
    )

    assert rule.name == "Utility Payment Rule"
    assert rule.description == "Maps recurring utility payments."


def test_blank_optional_description_becomes_none() -> None:
    """Blank optional text has one consistent missing representation."""

    rule = RuleDefinition.create(
        workspace_id=uuid4(),
        name="Draft Rule",
        description="   ",
    )

    assert rule.description is None


@pytest.mark.parametrize(
    "invalid_name",
    [
        "",
        "   ",
    ],
)
def test_rule_rejects_blank_name(
    invalid_name: str,
) -> None:
    """Every rule requires a meaningful display name."""

    with pytest.raises(
        RuleModelValidationError,
        match="name cannot be empty",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name=invalid_name,
        )


def test_rule_rejects_non_string_name() -> None:
    """Raw non-text names are rejected at the domain boundary."""

    with pytest.raises(
        RuleModelValidationError,
        match="name must be a string",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name=_invalid(123),
        )


def test_rule_rejects_invalid_rule_id() -> None:
    """Rule identifiers must be UUID values."""

    with pytest.raises(
        RuleModelValidationError,
        match="rule_id must be a UUID",
    ):
        RuleDefinition(
            rule_id=_invalid("rule-1"),
            workspace_id=uuid4(),
            name="Draft Rule",
        )


def test_rule_rejects_invalid_workspace_id() -> None:
    """Workspace ownership requires a stable UUID tenant identifier."""

    with pytest.raises(
        RuleModelValidationError,
        match="workspace_id must be a UUID",
    ):
        RuleDefinition.create(
            workspace_id=_invalid("workspace-1"),
            name="Draft Rule",
        )


def test_rule_accepts_any_logic() -> None:
    """A rule may combine conditions using logical OR."""

    rule = RuleDefinition.create(
        workspace_id=uuid4(),
        name="Alternative Vendor Names",
        logic=RuleLogic.ANY,
    )

    assert rule.logic is RuleLogic.ANY


def test_rule_rejects_invalid_logic_type() -> None:
    """Raw logic strings must be converted before domain construction."""

    with pytest.raises(
        RuleModelValidationError,
        match="logic must be a RuleLogic",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name="Draft Rule",
            logic=_invalid("all"),
        )


def test_rule_rejects_invalid_status_type() -> None:
    """Raw status strings must be converted before domain construction."""

    with pytest.raises(
        RuleModelValidationError,
        match="status must be a RuleStatus",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name="Draft Rule",
            status=_invalid("active"),
        )


def test_conditions_must_be_tuple() -> None:
    """Rule conditions use an immutable collection."""

    with pytest.raises(
        RuleModelValidationError,
        match="conditions must be a tuple",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name="Draft Rule",
            conditions=_invalid([_condition()]),
        )


def test_conditions_reject_invalid_items() -> None:
    """Every tuple item must be a validated RuleCondition."""

    with pytest.raises(
        RuleModelValidationError,
        match="only RuleCondition objects",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name="Draft Rule",
            conditions=_invalid((_condition(), "invalid")),
        )


def test_conditions_reject_duplicates() -> None:
    """Duplicate conditions are rejected to keep rule logic auditable."""

    condition = _condition()

    with pytest.raises(
        RuleModelValidationError,
        match="conditions cannot contain duplicates",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name="Draft Rule",
            conditions=(condition, condition),
        )


def test_rule_accepts_multiple_unique_conditions() -> None:
    """A rule may contain multiple distinct validated conditions."""

    rule = RuleDefinition.create(
        workspace_id=uuid4(),
        name="Utility Payment Rule",
        conditions=(
            _condition("utility"),
            _condition("electric"),
        ),
    )

    assert rule.condition_count == 2


def test_rule_rejects_invalid_output_type() -> None:
    """Raw output dictionaries must be adapted before domain creation."""

    with pytest.raises(
        RuleModelValidationError,
        match="output must be a RuleOutput or None",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name="Draft Rule",
            output=_invalid({"coa_account_id": str(uuid4())}),
        )


def test_rule_rejects_invalid_scope_type() -> None:
    """Rule scope must use the validated RuleScope domain model."""

    with pytest.raises(
        RuleModelValidationError,
        match="scope must be a RuleScope",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name="Draft Rule",
            scope=_invalid({}),
        )


@pytest.mark.parametrize(
    "priority",
    [
        MIN_RULE_PRIORITY,
        DEFAULT_RULE_PRIORITY,
        MAX_RULE_PRIORITY,
    ],
)
def test_rule_accepts_priority_boundaries(
    priority: int,
) -> None:
    """Minimum, default, and maximum priorities are valid."""

    rule = RuleDefinition.create(
        workspace_id=uuid4(),
        name="Draft Rule",
        priority=priority,
    )

    assert rule.priority == priority


@pytest.mark.parametrize(
    "invalid_priority",
    [
        MIN_RULE_PRIORITY - 1,
        MAX_RULE_PRIORITY + 1,
    ],
)
def test_rule_rejects_priority_outside_bounds(
    invalid_priority: int,
) -> None:
    """Rule priority must remain inside the controlled range."""

    with pytest.raises(
        RuleModelValidationError,
        match="priority must be between",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name="Draft Rule",
            priority=invalid_priority,
        )


@pytest.mark.parametrize(
    "invalid_priority",
    [
        True,
        100.5,
        "100",
    ],
)
def test_rule_rejects_non_integer_priority(
    invalid_priority: object,
) -> None:
    """Boolean, float, and string priorities are invalid."""

    with pytest.raises(
        RuleModelValidationError,
        match="priority must be an integer",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name="Draft Rule",
            priority=_invalid(invalid_priority),
        )


def test_rule_accepts_positive_version() -> None:
    """Rule versions begin at one and increase through audit history."""

    rule = RuleDefinition.create(
        workspace_id=uuid4(),
        name="Draft Rule",
        version=3,
    )

    assert rule.version == 3


@pytest.mark.parametrize(
    "invalid_version",
    [
        0,
        -1,
    ],
)
def test_rule_rejects_non_positive_version(
    invalid_version: int,
) -> None:
    """Rule version must be greater than or equal to one."""

    with pytest.raises(
        RuleModelValidationError,
        match="version must be greater than or equal to 1",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name="Draft Rule",
            version=invalid_version,
        )


@pytest.mark.parametrize(
    "invalid_version",
    [
        True,
        1.5,
        "1",
    ],
)
def test_rule_rejects_non_integer_version(
    invalid_version: object,
) -> None:
    """Rule versions must be actual integers."""

    with pytest.raises(
        RuleModelValidationError,
        match="version must be an integer",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name="Draft Rule",
            version=_invalid(invalid_version),
        )


def test_draft_rule_may_be_incomplete() -> None:
    """Users may save draft rules before configuring all fields."""

    rule = RuleDefinition.create(
        workspace_id=uuid4(),
        name="Incomplete Draft",
    )

    assert rule.is_complete is False
    assert rule.is_evaluable_on(date(2026, 7, 15)) is False


@pytest.mark.parametrize(
    "status",
    [
        RuleStatus.PENDING_APPROVAL,
        RuleStatus.ACTIVE,
        RuleStatus.PAUSED,
        RuleStatus.RETIRED,
    ],
)
def test_non_draft_rule_requires_conditions(
    status: RuleStatus,
) -> None:
    """Only drafts may exist without conditions."""

    with pytest.raises(
        RuleModelValidationError,
        match="Non-draft rules must contain at least one condition",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name="Incomplete Rule",
            output=_output(),
            status=status,
        )


@pytest.mark.parametrize(
    "status",
    [
        RuleStatus.PENDING_APPROVAL,
        RuleStatus.ACTIVE,
        RuleStatus.PAUSED,
        RuleStatus.RETIRED,
    ],
)
def test_non_draft_rule_requires_output(
    status: RuleStatus,
) -> None:
    """Only drafts may exist without a COA mapping output."""

    with pytest.raises(
        RuleModelValidationError,
        match="Non-draft rules must contain a mapping output",
    ):
        RuleDefinition.create(
            workspace_id=uuid4(),
            name="Incomplete Rule",
            conditions=(_condition(),),
            status=status,
        )


def test_complete_rule_reports_mapping_metadata() -> None:
    """Convenience properties expose stable rule metadata."""

    output = _output()
    scope = RuleScope(
        company_id=uuid4(),
        store_id=uuid4(),
    )

    rule = RuleDefinition.create(
        workspace_id=uuid4(),
        name="Store Utility Rule",
        conditions=(
            _condition("utility"),
            _condition("electric"),
        ),
        output=output,
        scope=scope,
        status=RuleStatus.ACTIVE,
    )

    assert rule.is_complete is True
    assert rule.condition_count == 2
    assert rule.scope_specificity == 2
    assert rule.output_account_id == output.coa_account_id


def test_incomplete_rule_has_no_output_account_id() -> None:
    """Drafts without mapping outputs expose no COA identifier."""

    rule = RuleDefinition.create(
        workspace_id=uuid4(),
        name="Draft Rule",
    )

    assert rule.output_account_id is None


def test_effective_date_boundaries_are_inclusive() -> None:
    """A rule applies on both configured boundary dates."""

    rule = _complete_rule(
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
    )

    assert rule.is_effective_on(date(2026, 1, 1)) is True
    assert rule.is_effective_on(date(2026, 12, 31)) is True


def test_rule_is_not_effective_before_start_date() -> None:
    """Transactions before effective_from are excluded."""

    rule = _complete_rule(
        effective_from=date(2026, 7, 1),
    )

    assert rule.is_effective_on(date(2026, 6, 30)) is False


def test_rule_is_not_effective_after_end_date() -> None:
    """Transactions after effective_to are excluded."""

    rule = _complete_rule(
        effective_to=date(2026, 7, 31),
    )

    assert rule.is_effective_on(date(2026, 8, 1)) is False


def test_rule_without_effective_dates_is_always_date_eligible() -> None:
    """An open rule has no transaction-date restriction."""

    rule = _complete_rule()

    assert rule.is_effective_on(date(2000, 1, 1)) is True
    assert rule.is_effective_on(date(2100, 12, 31)) is True


def test_rule_rejects_reversed_effective_window() -> None:
    """The start date cannot be later than the end date."""

    with pytest.raises(
        RuleModelValidationError,
        match="effective_from cannot be later than effective_to",
    ):
        _complete_rule(
            effective_from=date(2026, 12, 31),
            effective_to=date(2026, 1, 1),
        )


def test_rule_accepts_equal_effective_boundaries() -> None:
    """A rule may be effective for exactly one transaction date."""

    boundary = date(2026, 7, 15)

    rule = _complete_rule(
        effective_from=boundary,
        effective_to=boundary,
    )

    assert rule.is_effective_on(boundary) is True


@pytest.mark.parametrize(
    "field_name",
    [
        "effective_from",
        "effective_to",
    ],
)
@pytest.mark.parametrize(
    "invalid_date",
    [
        datetime(2026, 7, 15, 10, 30),
        "2026-07-15",
    ],
)
def test_rule_rejects_invalid_effective_date_values(
    field_name: str,
    invalid_date: object,
) -> None:
    """Effective dates must be date objects without timestamps."""

    arguments: dict[str, Any] = {
        "workspace_id": uuid4(),
        "name": "Draft Rule",
        field_name: invalid_date,
    }

    with pytest.raises(
        RuleModelValidationError,
        match=rf"{field_name} must be a date without time or None",
    ):
        RuleDefinition.create(**arguments)


@pytest.mark.parametrize(
    "invalid_transaction_date",
    [
        datetime(2026, 7, 15, 10, 30),
        "2026-07-15",
    ],
)
def test_is_effective_on_rejects_invalid_date(
    invalid_transaction_date: object,
) -> None:
    """Date evaluation rejects timestamps and raw strings."""

    rule = _complete_rule()

    with pytest.raises(
        RuleModelValidationError,
        match="transaction_date must be a date without time",
    ):
        rule.is_effective_on(_invalid(invalid_transaction_date))


def test_active_complete_rule_is_evaluable_inside_window() -> None:
    """Only a complete active rule inside its date window may evaluate."""

    rule = _complete_rule(
        status=RuleStatus.ACTIVE,
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
    )

    assert rule.is_evaluable_on(date(2026, 7, 15)) is True


@pytest.mark.parametrize(
    "status",
    [
        RuleStatus.DRAFT,
        RuleStatus.PENDING_APPROVAL,
        RuleStatus.PAUSED,
        RuleStatus.RETIRED,
    ],
)
def test_non_active_rule_is_not_evaluable(
    status: RuleStatus,
) -> None:
    """Complete rules outside ACTIVE status cannot affect mappings."""

    rule = _complete_rule(status=status)

    assert rule.is_evaluable_on(date(2026, 7, 15)) is False


def test_active_rule_outside_effective_window_is_not_evaluable() -> None:
    """Active status cannot override the effective-date restriction."""

    rule = _complete_rule(
        effective_from=date(2026, 8, 1),
    )

    assert rule.is_evaluable_on(date(2026, 7, 15)) is False


def test_rule_is_immutable() -> None:
    """A validated rule definition cannot be modified in place."""

    rule = _complete_rule()
    rule_for_mutation = cast(Any, rule)

    with pytest.raises(FrozenInstanceError):
        rule_for_mutation.priority = 999


def test_rule_output_is_immutable() -> None:
    """A validated mapping output cannot be changed in place."""

    output = _output()
    output_for_mutation = cast(Any, output)

    with pytest.raises(FrozenInstanceError):
        output_for_mutation.coa_account_id = uuid4()
