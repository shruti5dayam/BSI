"""
Core models for the deterministic BSI rule-engine domain.

This module defines immutable financial mapping rules and their outputs.

A complete rule combines:

- Workspace ownership
- Lifecycle status
- ALL or ANY condition logic
- Validated rule conditions
- Organizational scope
- Chart of Accounts output
- Deterministic priority
- Version and effective dates

The models remain independent of databases, APIs, spreadsheets, Pandas,
user interfaces, and AI providers.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Self
from uuid import UUID, uuid4

from bsi.domain.rules.conditions import RuleCondition
from bsi.domain.rules.enums import RuleLogic, RuleStatus
from bsi.domain.rules.scope import RuleScope

DEFAULT_RULE_PRIORITY = 100
MIN_RULE_PRIORITY = 0
MAX_RULE_PRIORITY = 10_000


class RuleModelValidationError(ValueError):
    """Raised when a rule-domain model is invalid."""


def _validate_uuid(
    value: UUID,
    *,
    field_name: str,
) -> None:
    """Validate a required UUID identifier."""

    if not isinstance(value, UUID):
        raise RuleModelValidationError(f"{field_name} must be a UUID.")


def _clean_required_text(
    value: str,
    *,
    field_name: str,
) -> str:
    """Normalize a required human-readable rule field."""

    if not isinstance(value, str):
        raise RuleModelValidationError(f"{field_name} must be a string.")

    cleaned_value = " ".join(value.strip().split())

    if not cleaned_value:
        raise RuleModelValidationError(f"{field_name} cannot be empty.")

    return cleaned_value


def _clean_optional_text(
    value: str | None,
    *,
    field_name: str,
) -> str | None:
    """Normalize optional rule text and convert blanks to None."""

    if value is None:
        return None

    if not isinstance(value, str):
        raise RuleModelValidationError(f"{field_name} must be a string or None.")

    cleaned_value = " ".join(value.strip().split())

    if not cleaned_value:
        return None

    return cleaned_value


def _validate_optional_date(
    value: date | None,
    *,
    field_name: str,
) -> None:
    """Validate an optional date without accepting datetime values."""

    if value is None:
        return

    if isinstance(value, datetime) or not isinstance(value, date):
        raise RuleModelValidationError(
            f"{field_name} must be a date without time or None."
        )


@dataclass(frozen=True, slots=True)
class RuleOutput:
    """
    Deterministic mapping produced by a successful rule match.

    Attributes
    ----------
    coa_account_id:
        Stable identifier of the Chart of Accounts entry assigned by
        this rule.

    Notes
    -----
    Account names and account numbers are not copied into this model.
    They should be resolved from the authoritative COA domain using
    coa_account_id.
    """

    coa_account_id: UUID

    def __post_init__(self) -> None:
        """Validate the authoritative COA account identifier."""

        _validate_uuid(
            self.coa_account_id,
            field_name="coa_account_id",
        )


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    """
    Immutable deterministic financial mapping rule.

    Attributes
    ----------
    rule_id:
        Stable identifier of this rule version.

    workspace_id:
        Tenant boundary that owns the rule.

    name:
        Human-readable rule name.

    logic:
        ALL for logical AND or ANY for logical OR.

    conditions:
        Immutable collection of validated rule conditions.

    output:
        COA mapping produced when the rule matches.

        Draft rules may temporarily omit an output. Every non-draft rule
        must contain an output.

    scope:
        Optional company, brand, store, and bank-account restrictions.

    status:
        Rule lifecycle status. Only ACTIVE rules may evaluate
        transactions.

    priority:
        Deterministic precedence value.

        Higher values represent stronger priority. Rule ranking will
        compare higher priority values before lower values.

    version:
        Positive version number for audit history.

    effective_from:
        Optional first transaction date on which the rule applies.

    effective_to:
        Optional last transaction date on which the rule applies.

    description:
        Optional human-readable explanation of the rule.
    """

    rule_id: UUID
    workspace_id: UUID
    name: str
    logic: RuleLogic = RuleLogic.ALL
    conditions: tuple[RuleCondition, ...] = ()
    output: RuleOutput | None = None
    scope: RuleScope = field(default_factory=RuleScope)
    status: RuleStatus = RuleStatus.DRAFT
    priority: int = DEFAULT_RULE_PRIORITY
    version: int = 1
    effective_from: date | None = None
    effective_to: date | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalize the complete rule definition."""

        _validate_uuid(
            self.rule_id,
            field_name="rule_id",
        )
        _validate_uuid(
            self.workspace_id,
            field_name="workspace_id",
        )

        normalized_name = _clean_required_text(
            self.name,
            field_name="name",
        )
        normalized_description = _clean_optional_text(
            self.description,
            field_name="description",
        )

        if not isinstance(self.logic, RuleLogic):
            raise RuleModelValidationError("logic must be a RuleLogic.")

        if not isinstance(self.status, RuleStatus):
            raise RuleModelValidationError("status must be a RuleStatus.")

        if not isinstance(self.conditions, tuple):
            raise RuleModelValidationError(
                "conditions must be a tuple of RuleCondition objects."
            )

        if not all(
            isinstance(condition, RuleCondition) for condition in self.conditions
        ):
            raise RuleModelValidationError(
                "conditions must contain only RuleCondition objects."
            )

        if len(set(self.conditions)) != len(self.conditions):
            raise RuleModelValidationError("conditions cannot contain duplicates.")

        if self.output is not None and not isinstance(
            self.output,
            RuleOutput,
        ):
            raise RuleModelValidationError("output must be a RuleOutput or None.")

        if not isinstance(self.scope, RuleScope):
            raise RuleModelValidationError("scope must be a RuleScope.")

        if isinstance(self.priority, bool) or not isinstance(
            self.priority,
            int,
        ):
            raise RuleModelValidationError("priority must be an integer.")

        if not MIN_RULE_PRIORITY <= self.priority <= MAX_RULE_PRIORITY:
            raise RuleModelValidationError(
                f"priority must be between {MIN_RULE_PRIORITY} and {MAX_RULE_PRIORITY}."
            )

        if isinstance(self.version, bool) or not isinstance(
            self.version,
            int,
        ):
            raise RuleModelValidationError("version must be an integer.")

        if self.version < 1:
            raise RuleModelValidationError(
                "version must be greater than or equal to 1."
            )

        _validate_optional_date(
            self.effective_from,
            field_name="effective_from",
        )
        _validate_optional_date(
            self.effective_to,
            field_name="effective_to",
        )

        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_from > self.effective_to
        ):
            raise RuleModelValidationError(
                "effective_from cannot be later than effective_to."
            )

        if self.status is not RuleStatus.DRAFT and not self.conditions:
            raise RuleModelValidationError(
                "Non-draft rules must contain at least one condition."
            )

        if self.status is not RuleStatus.DRAFT and self.output is None:
            raise RuleModelValidationError(
                "Non-draft rules must contain a mapping output."
            )

        object.__setattr__(
            self,
            "name",
            normalized_name,
        )
        object.__setattr__(
            self,
            "description",
            normalized_description,
        )

    @classmethod
    def create(
        cls,
        *,
        workspace_id: UUID,
        name: str,
        logic: RuleLogic = RuleLogic.ALL,
        conditions: tuple[RuleCondition, ...] = (),
        output: RuleOutput | None = None,
        scope: RuleScope | None = None,
        status: RuleStatus = RuleStatus.DRAFT,
        priority: int = DEFAULT_RULE_PRIORITY,
        version: int = 1,
        effective_from: date | None = None,
        effective_to: date | None = None,
        description: str | None = None,
        rule_id: UUID | None = None,
    ) -> Self:
        """
        Create a validated rule with an optional generated identifier.

        Parameters
        ----------
        workspace_id:
            Workspace that owns the rule.

        name:
            Human-readable rule name.

        logic:
            ALL or ANY condition logic.

        conditions:
            Immutable rule-condition collection.

        output:
            Optional COA mapping. Draft rules may omit this.

        scope:
            Optional organizational scope. An empty scope is used when
            omitted.

        status:
            Rule lifecycle status.

        priority:
            Deterministic precedence value. Higher values rank first.

        version:
            Positive audit version number.

        effective_from:
            Optional first applicable transaction date.

        effective_to:
            Optional last applicable transaction date.

        description:
            Optional rule explanation.

        rule_id:
            Optional predefined identifier. A UUID is generated when
            omitted.

        Returns
        -------
        RuleDefinition
            Immutable validated rule.
        """

        resolved_rule_id = rule_id if rule_id is not None else uuid4()
        resolved_scope = scope if scope is not None else RuleScope()

        return cls(
            rule_id=resolved_rule_id,
            workspace_id=workspace_id,
            name=name,
            logic=logic,
            conditions=conditions,
            output=output,
            scope=resolved_scope,
            status=status,
            priority=priority,
            version=version,
            effective_from=effective_from,
            effective_to=effective_to,
            description=description,
        )

    @property
    def is_complete(self) -> bool:
        """
        Return whether the rule has conditions and a mapping output.

        Draft rules may be incomplete. Approval and activation require
        complete rules.
        """

        return bool(self.conditions) and self.output is not None

    @property
    def condition_count(self) -> int:
        """Return the number of conditions in the rule."""

        return len(self.conditions)

    @property
    def scope_specificity(self) -> int:
        """Return the number of populated scope restrictions."""

        return self.scope.specificity

    @property
    def output_account_id(self) -> UUID | None:
        """Return the mapped COA identifier when an output exists."""

        if self.output is None:
            return None

        return self.output.coa_account_id

    def is_effective_on(
        self,
        transaction_date: date,
    ) -> bool:
        """
        Return whether the date falls within the rule's effective window.

        Both effective boundaries are inclusive.

        Parameters
        ----------
        transaction_date:
            Transaction date to evaluate.

        Returns
        -------
        bool
            True when the date is inside the configured date window.

        Raises
        ------
        RuleModelValidationError
            If transaction_date is not a date without time.
        """

        if isinstance(transaction_date, datetime) or not isinstance(
            transaction_date,
            date,
        ):
            raise RuleModelValidationError(
                "transaction_date must be a date without time."
            )

        if self.effective_from is not None and transaction_date < self.effective_from:
            return False

        if self.effective_to is not None and transaction_date > self.effective_to:
            return False

        return self.effective_to is None or transaction_date <= self.effective_to

    def is_evaluable_on(
        self,
        transaction_date: date,
    ) -> bool:
        """
        Return whether this rule may evaluate a transaction on the date.

        A rule is evaluable only when:

        - Its lifecycle status is ACTIVE
        - It has conditions and an output
        - The transaction date is inside its effective window
        """

        return (
            self.status.is_evaluable
            and self.is_complete
            and self.is_effective_on(transaction_date)
        )
