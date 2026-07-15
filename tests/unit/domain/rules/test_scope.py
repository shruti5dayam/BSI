"""
Unit tests for deterministic rule scope.

These tests verify that RuleScope:

- Accepts valid UUID restrictions
- Represents global and specific rules correctly
- Matches transaction context using AND logic
- Rejects invalid identifiers and context objects
- Produces stable audit-friendly scope metadata
- Remains immutable after construction
"""

from dataclasses import FrozenInstanceError
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from bsi.domain.rules.scope import (
    RuleScope,
    RuleScopeValidationError,
)
from bsi.domain.transactions.models import TransactionContext


def _invalid(value: object) -> Any:
    """
    Bypass static typing for intentional runtime-validation tests.

    Production code should pass UUID or None. Tests use this helper to
    confirm that invalid data from APIs, files, or databases is rejected.
    """

    return cast(Any, value)


def test_empty_scope_is_global() -> None:
    """A scope with no restrictions represents a global rule."""

    scope = RuleScope()

    assert scope.is_global is True
    assert scope.specificity == 0
    assert scope.active_dimensions == ()


def test_global_scope_matches_empty_transaction_context() -> None:
    """A global scope matches a transaction without optional context."""

    scope = RuleScope()
    context = TransactionContext()

    assert scope.matches(context) is True


def test_global_scope_matches_populated_transaction_context() -> None:
    """A global scope matches transactions from any organizational context."""

    context = TransactionContext(
        company_id=uuid4(),
        brand_id=uuid4(),
        store_id=uuid4(),
        bank_account_id=uuid4(),
    )

    assert RuleScope().matches(context) is True


@pytest.mark.parametrize(
    "field_name",
    [
        "company_id",
        "brand_id",
        "store_id",
        "bank_account_id",
    ],
)
def test_scope_accepts_each_uuid_dimension(
    field_name: str,
) -> None:
    """Each supported scope dimension accepts a UUID."""

    identifier = uuid4()

    scope = RuleScope(
        **{
            field_name: identifier,
        }
    )

    assert getattr(scope, field_name) == identifier
    assert scope.specificity == 1
    assert scope.is_global is False
    assert scope.active_dimensions == (field_name,)


def test_fully_populated_scope_reports_all_dimensions() -> None:
    """Scope metadata uses a stable dimension order for audit evidence."""

    scope = RuleScope(
        company_id=uuid4(),
        brand_id=uuid4(),
        store_id=uuid4(),
        bank_account_id=uuid4(),
    )

    assert scope.specificity == 4
    assert scope.active_dimensions == (
        "company_id",
        "brand_id",
        "store_id",
        "bank_account_id",
    )


def test_scope_matches_when_every_restriction_matches() -> None:
    """All populated scope dimensions must match the transaction context."""

    company_id = uuid4()
    brand_id = uuid4()
    store_id = uuid4()
    bank_account_id = uuid4()

    scope = RuleScope(
        company_id=company_id,
        brand_id=brand_id,
        store_id=store_id,
        bank_account_id=bank_account_id,
    )

    context = TransactionContext(
        company_id=company_id,
        brand_id=brand_id,
        store_id=store_id,
        bank_account_id=bank_account_id,
    )

    assert scope.matches(context) is True


@pytest.mark.parametrize(
    "mismatched_field",
    [
        "company_id",
        "brand_id",
        "store_id",
        "bank_account_id",
    ],
)
def test_scope_rejects_context_when_one_dimension_differs(
    mismatched_field: str,
) -> None:
    """One mismatch is enough to make the complete scope ineligible."""

    identifiers: dict[str, UUID] = {
        "company_id": uuid4(),
        "brand_id": uuid4(),
        "store_id": uuid4(),
        "bank_account_id": uuid4(),
    }

    scope = RuleScope(**identifiers)

    context_values = dict(identifiers)
    context_values[mismatched_field] = uuid4()

    context = TransactionContext(
        company_id=context_values["company_id"],
        brand_id=context_values["brand_id"],
        store_id=context_values["store_id"],
        bank_account_id=context_values["bank_account_id"],
    )

    assert scope.matches(context) is False


@pytest.mark.parametrize(
    "missing_field",
    [
        "company_id",
        "brand_id",
        "store_id",
        "bank_account_id",
    ],
)
def test_scope_rejects_context_when_required_dimension_is_missing(
    missing_field: str,
) -> None:
    """A missing transaction value does not satisfy a populated scope."""

    identifiers: dict[str, UUID] = {
        "company_id": uuid4(),
        "brand_id": uuid4(),
        "store_id": uuid4(),
        "bank_account_id": uuid4(),
    }

    scope = RuleScope(**identifiers)

    context_values: dict[str, UUID | None] = dict(identifiers)
    context_values[missing_field] = None

    context = TransactionContext(
        company_id=context_values["company_id"],
        brand_id=context_values["brand_id"],
        store_id=context_values["store_id"],
        bank_account_id=context_values["bank_account_id"],
    )

    assert scope.matches(context) is False


def test_unrestricted_dimensions_do_not_affect_matching() -> None:
    """Only populated scope dimensions participate in comparison."""

    company_id = uuid4()

    scope = RuleScope(company_id=company_id)

    context = TransactionContext(
        company_id=company_id,
        brand_id=uuid4(),
        store_id=uuid4(),
        bank_account_id=uuid4(),
    )

    assert scope.matches(context) is True


def test_store_scope_matches_same_store_without_other_context() -> None:
    """A store-only rule does not require company or brand restrictions."""

    store_id = uuid4()

    scope = RuleScope(store_id=store_id)
    context = TransactionContext(store_id=store_id)

    assert scope.matches(context) is True


@pytest.mark.parametrize(
    "field_name",
    [
        "company_id",
        "brand_id",
        "store_id",
        "bank_account_id",
    ],
)
@pytest.mark.parametrize(
    "invalid_value",
    [
        "not-a-uuid",
        123,
        True,
    ],
)
def test_scope_rejects_invalid_identifier_types(
    field_name: str,
    invalid_value: object,
) -> None:
    """Scope identifiers must be UUID values or None."""

    with pytest.raises(
        RuleScopeValidationError,
        match=rf"{field_name} must be a UUID or None",
    ):
        RuleScope(
            **{
                field_name: _invalid(invalid_value),
            }
        )


def test_scope_accepts_none_for_every_dimension() -> None:
    """Explicit None values are equivalent to an empty global scope."""

    scope = RuleScope(
        company_id=None,
        brand_id=None,
        store_id=None,
        bank_account_id=None,
    )

    assert scope.is_global is True
    assert scope.specificity == 0


def test_matches_rejects_invalid_transaction_context() -> None:
    """Scope evaluation requires the authoritative TransactionContext type."""

    scope = RuleScope()

    with pytest.raises(
        RuleScopeValidationError,
        match="transaction_context must be a TransactionContext",
    ):
        scope.matches(_invalid({}))


def test_scope_is_immutable() -> None:
    """A validated scope cannot change after rule construction."""

    scope = RuleScope(company_id=uuid4())
    scope_for_mutation = cast(Any, scope)

    with pytest.raises(FrozenInstanceError):
        scope_for_mutation.company_id = uuid4()
