"""
Enumerations for the deterministic BSI rule-engine domain.

This module defines the controlled vocabulary used by financial
transaction-mapping rules.

The rule domain must remain independent of:

- Pandas
- FastAPI
- Streamlit
- SQLAlchemy
- PostgreSQL
- LLM providers
"""

from enum import StrEnum


class RuleLogic(StrEnum):
    """
    Define how multiple conditions inside one rule are combined.

    ALL
        Every condition must match. This represents logical AND.

    ANY
        At least one condition must match. This represents logical OR.
    """

    ALL = "all"
    ANY = "any"

    @property
    def requires_all_conditions(self) -> bool:
        """
        Return whether every rule condition must match.

        Returns
        -------
        bool
            True for ALL logic and False for ANY logic.
        """

        return self is RuleLogic.ALL


class RuleStatus(StrEnum):
    """
    Lifecycle status of a deterministic financial mapping rule.

    Only ACTIVE rules may participate in transaction evaluation.
    """

    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"

    @property
    def is_evaluable(self) -> bool:
        """
        Return whether the rule may evaluate transactions.

        Returns
        -------
        bool
            True only when the rule is active.
        """

        return self is RuleStatus.ACTIVE


class RuleConditionField(StrEnum):
    """
    Supported transaction attributes that a rule condition may inspect.

    Organizational scope fields such as company, brand, store, and bank
    account are intentionally excluded. Those restrictions belong to a
    separate RuleScope domain object.
    """

    SEARCHABLE_TEXT = "searchable_text"
    DESCRIPTION = "normalized_description"
    MEMO = "original_memo"
    VENDOR = "vendor_name"
    DIRECTION = "direction"
    ABSOLUTE_AMOUNT = "absolute_amount"
    SIGNED_AMOUNT = "signed_amount"
    TRANSACTION_DATE = "transaction_date"


class RuleOperator(StrEnum):
    """
    Supported deterministic comparison operators.

    Operator compatibility with field types will be validated by the
    RuleCondition domain model rather than by this enumeration.
    """

    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    BETWEEN = "between"
