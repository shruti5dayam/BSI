"""
Organizational scope for deterministic BSI mapping rules.

A rule scope limits where a rule may be applied. Supported dimensions are:

- Company
- Brand
- Store
- Bank account

The scope remains framework independent and does not depend on databases,
APIs, spreadsheets, Pandas, or user-interface code.
"""

from dataclasses import dataclass
from uuid import UUID

from bsi.domain.transactions.models import TransactionContext


class RuleScopeValidationError(ValueError):
    """Raised when rule-scope data is invalid."""


def _validate_optional_uuid(
    value: UUID | None,
    *,
    field_name: str,
) -> None:
    """Validate an optional UUID scope identifier."""

    if value is not None and not isinstance(value, UUID):
        raise RuleScopeValidationError(f"{field_name} must be a UUID or None.")


@dataclass(frozen=True, slots=True)
class RuleScope:
    """
    Immutable organizational scope for one mapping rule.

    Every populated scope dimension must match the corresponding
    transaction context value.

    An entirely empty scope represents a global rule within the rule's
    workspace.

    Attributes
    ----------
    company_id:
        Optional company restriction.

    brand_id:
        Optional brand restriction.

    store_id:
        Optional store or location restriction.

    bank_account_id:
        Optional internal bank-account restriction.
    """

    company_id: UUID | None = None
    brand_id: UUID | None = None
    store_id: UUID | None = None
    bank_account_id: UUID | None = None

    def __post_init__(self) -> None:
        """Validate every optional scope identifier."""

        _validate_optional_uuid(
            self.company_id,
            field_name="company_id",
        )
        _validate_optional_uuid(
            self.brand_id,
            field_name="brand_id",
        )
        _validate_optional_uuid(
            self.store_id,
            field_name="store_id",
        )
        _validate_optional_uuid(
            self.bank_account_id,
            field_name="bank_account_id",
        )

    @property
    def is_global(self) -> bool:
        """
        Return whether the scope has no organizational restrictions.

        Returns
        -------
        bool
            True when every scope identifier is None.
        """

        return self.specificity == 0

    @property
    def specificity(self) -> int:
        """
        Return the number of populated scope dimensions.

        A higher number represents a more narrowly targeted rule.

        This value may later contribute to deterministic rule ranking.
        Equal specificity does not automatically resolve a tie; the
        ranking layer must still detect ambiguity.

        Returns
        -------
        int
            Integer from zero through four.
        """

        return sum(
            scope_value is not None
            for scope_value in (
                self.company_id,
                self.brand_id,
                self.store_id,
                self.bank_account_id,
            )
        )

    @property
    def active_dimensions(self) -> tuple[str, ...]:
        """
        Return the names of scope dimensions currently in use.

        This is useful for audit evidence and human-readable explanations.

        Returns
        -------
        tuple[str, ...]
            Populated scope-field names in stable order.
        """

        dimensions: list[str] = []

        if self.company_id is not None:
            dimensions.append("company_id")

        if self.brand_id is not None:
            dimensions.append("brand_id")

        if self.store_id is not None:
            dimensions.append("store_id")

        if self.bank_account_id is not None:
            dimensions.append("bank_account_id")

        return tuple(dimensions)

    def matches(self, transaction_context: TransactionContext) -> bool:
        """
        Return whether the transaction satisfies every scope restriction.

        Parameters
        ----------
        transaction_context:
            Company, brand, store, and bank-account context belonging to
            the normalized transaction.

        Returns
        -------
        bool
            True when every populated scope value matches.

        Raises
        ------
        RuleScopeValidationError
            If transaction_context is not a TransactionContext object.
        """

        if not isinstance(transaction_context, TransactionContext):
            raise RuleScopeValidationError(
                "transaction_context must be a TransactionContext."
            )

        comparisons = (
            (
                self.company_id,
                transaction_context.company_id,
            ),
            (
                self.brand_id,
                transaction_context.brand_id,
            ),
            (
                self.store_id,
                transaction_context.store_id,
            ),
            (
                self.bank_account_id,
                transaction_context.bank_account_id,
            ),
        )

        return all(
            scope_value is None or scope_value == context_value
            for scope_value, context_value in comparisons
        )
