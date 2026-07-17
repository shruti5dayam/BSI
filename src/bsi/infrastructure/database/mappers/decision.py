"""
Persistence mapper for deterministic rule-engine decisions.

The application layer produces a RuleEngineDecisionDTO containing:

- Decision summary fields
- Matched and top-ranked rule identifiers
- Rule-level evaluation evidence
- Condition-level evaluation evidence

PostgreSQL stores:

- Frequently queried summary fields in relational columns
- UUID collections in PostgreSQL ARRAY columns
- Nested immutable audit evidence in a JSONB column

This module converts between those two representations. It does not:

- Evaluate rules
- Rank rule matches
- Resolve conflicts
- Query or commit database sessions
- Call AI services
"""

from collections.abc import Mapping
from uuid import UUID

from bsi.application.rule_engine.dto import (
    RuleConditionEvaluationDTO,
    RuleEligibilityDTO,
    RuleEngineDecisionDTO,
    RuleEvaluationDTO,
)
from bsi.domain.rules.conflicts import RuleConflictKind
from bsi.domain.rules.engine import RuleDecisionStatus
from bsi.infrastructure.database.models.decision import (
    RuleDecisionRecord,
    RuleEvaluationEvidence,
)


class DecisionMapperError(ValueError):
    """
    Raised when decision persistence data cannot be converted safely.

    The error protects the application from malformed JSONB evidence,
    inconsistent counts, unsupported statuses, and invalid decision
    states.
    """


type EvidenceValue = str | tuple[str, str] | None

_EVIDENCE_SCHEMA_VERSION = 1


def decision_to_record(
    decision: RuleEngineDecisionDTO,
) -> RuleDecisionRecord:
    """
    Convert an application rule-engine decision into a database record.

    Parameters
    ----------
    decision:
        Completed application-facing deterministic decision.

    Returns
    -------
    RuleDecisionRecord
        SQLAlchemy record ready to be persisted.

    Raises
    ------
    TypeError
        If decision is not a RuleEngineDecisionDTO.

    DecisionMapperError
        If decision fields or nested evidence are inconsistent.
    """

    if not isinstance(decision, RuleEngineDecisionDTO):
        raise TypeError(
            "decision must be a RuleEngineDecisionDTO.",
        )

    _validate_decision(decision)

    return RuleDecisionRecord(
        workspace_id=decision.workspace_id,
        transaction_id=decision.transaction_id,
        status=decision.status,
        conflict_kind=decision.conflict_kind,
        can_map=decision.can_map,
        requires_review=decision.requires_review,
        is_conflict_blocked=decision.is_conflict_blocked,
        output_account_id=decision.output_account_id,
        winning_rule_id=decision.winning_rule_id,
        matched_rule_ids=list(decision.matched_rule_ids),
        top_rule_ids=list(decision.top_rule_ids),
        evaluated_rule_count=decision.evaluated_rule_count,
        eligible_rule_count=decision.eligible_rule_count,
        ineligible_rule_count=decision.ineligible_rule_count,
        matched_rule_count=decision.matched_rule_count,
        unmatched_eligible_rule_count=(decision.unmatched_eligible_rule_count),
        decision_message=decision.decision_message,
        evaluations=[
            _evaluation_to_evidence(evaluation) for evaluation in decision.evaluations
        ],
    )


def record_to_decision(
    record: RuleDecisionRecord,
) -> RuleEngineDecisionDTO:
    """
    Convert a database record into an application decision DTO.

    Parameters
    ----------
    record:
        SQLAlchemy decision record loaded from PostgreSQL.

    Returns
    -------
    RuleEngineDecisionDTO
        Immutable application-facing decision.

    Raises
    ------
    TypeError
        If record is not a RuleDecisionRecord.

    DecisionMapperError
        If relational columns or JSONB evidence are malformed.
    """

    if not isinstance(record, RuleDecisionRecord):
        raise TypeError(
            "record must be a RuleDecisionRecord.",
        )

    if not isinstance(record.matched_rule_ids, list):
        raise DecisionMapperError(
            "matched_rule_ids must be stored as a list.",
        )

    if not isinstance(record.top_rule_ids, list):
        raise DecisionMapperError(
            "top_rule_ids must be stored as a list.",
        )

    if not isinstance(record.evaluations, list):
        raise DecisionMapperError(
            "evaluations must be stored as a list.",
        )

    evaluations = tuple(
        _evidence_to_evaluation(evidence) for evidence in record.evaluations
    )

    decision = RuleEngineDecisionDTO(
        workspace_id=record.workspace_id,
        transaction_id=record.transaction_id,
        status=record.status,
        conflict_kind=record.conflict_kind,
        can_map=record.can_map,
        requires_review=record.requires_review,
        is_conflict_blocked=record.is_conflict_blocked,
        output_account_id=record.output_account_id,
        winning_rule_id=record.winning_rule_id,
        matched_rule_ids=tuple(record.matched_rule_ids),
        top_rule_ids=tuple(record.top_rule_ids),
        evaluated_rule_count=record.evaluated_rule_count,
        eligible_rule_count=record.eligible_rule_count,
        ineligible_rule_count=record.ineligible_rule_count,
        matched_rule_count=record.matched_rule_count,
        unmatched_eligible_rule_count=(record.unmatched_eligible_rule_count),
        decision_message=record.decision_message,
        evaluations=evaluations,
    )

    _validate_decision(decision)

    return decision


def _evaluation_to_evidence(
    evaluation: RuleEvaluationDTO,
) -> RuleEvaluationEvidence:
    """Convert one rule evaluation DTO into JSON-compatible evidence."""

    _validate_evaluation(evaluation)

    return {
        "schema_version": _EVIDENCE_SCHEMA_VERSION,
        "rule_id": str(evaluation.rule_id),
        "workspace_id": str(evaluation.workspace_id),
        "rule_name": evaluation.rule_name,
        "rule_status": evaluation.rule_status,
        "rule_logic": evaluation.rule_logic,
        "priority": evaluation.priority,
        "scope_specificity": evaluation.scope_specificity,
        "configured_output_account_id": (
            str(evaluation.configured_output_account_id)
            if evaluation.configured_output_account_id is not None
            else None
        ),
        "matched_output_account_id": (
            str(evaluation.matched_output_account_id)
            if evaluation.matched_output_account_id is not None
            else None
        ),
        "eligibility": {
            "status_allows_evaluation": (
                evaluation.eligibility.status_allows_evaluation
            ),
            "rule_is_complete": (evaluation.eligibility.rule_is_complete),
            "effective_date_matches": (evaluation.eligibility.effective_date_matches),
            "scope_matches": evaluation.eligibility.scope_matches,
            "is_eligible": evaluation.eligibility.is_eligible,
        },
        "conditions": [
            _condition_to_evidence(condition) for condition in evaluation.conditions
        ],
        "evaluated_condition_count": (evaluation.evaluated_condition_count),
        "matched_condition_count": evaluation.matched_condition_count,
        "failed_condition_count": evaluation.failed_condition_count,
        "matched": evaluation.matched,
    }


def _condition_to_evidence(
    condition: RuleConditionEvaluationDTO,
) -> dict[str, object]:
    """Convert one condition evaluation into JSON-compatible evidence."""

    if not isinstance(condition, RuleConditionEvaluationDTO):
        raise DecisionMapperError(
            "conditions must contain RuleConditionEvaluationDTO objects."
        )

    if not condition.field.strip():
        raise DecisionMapperError(
            "condition field cannot be blank.",
        )

    if not condition.operator.strip():
        raise DecisionMapperError(
            "condition operator cannot be blank.",
        )

    return {
        "field": condition.field,
        "operator": condition.operator,
        "expected_value": _serialize_evidence_value(
            condition.expected_value,
        ),
        "actual_value": _serialize_evidence_value(
            condition.actual_value,
        ),
        "matched": condition.matched,
    }


def _evidence_to_evaluation(
    evidence: RuleEvaluationEvidence,
) -> RuleEvaluationDTO:
    """Reconstruct one rule evaluation DTO from JSONB evidence."""

    evidence_mapping = _require_mapping(
        evidence,
        field_name="evaluation",
    )

    schema_version = _require_non_negative_int(
        evidence_mapping,
        key="schema_version",
    )

    if schema_version != _EVIDENCE_SCHEMA_VERSION:
        raise DecisionMapperError(
            f"Unsupported rule evaluation evidence schema version: {schema_version}."
        )

    eligibility_mapping = _require_mapping(
        _require_field(
            evidence_mapping,
            key="eligibility",
        ),
        field_name="eligibility",
    )

    conditions_value = _require_field(
        evidence_mapping,
        key="conditions",
    )

    if not isinstance(conditions_value, list):
        raise DecisionMapperError(
            "conditions must be stored as a list.",
        )

    evaluation = RuleEvaluationDTO(
        rule_id=_require_uuid(
            evidence_mapping,
            key="rule_id",
        ),
        workspace_id=_require_uuid(
            evidence_mapping,
            key="workspace_id",
        ),
        rule_name=_require_non_blank_string(
            evidence_mapping,
            key="rule_name",
        ),
        rule_status=_require_non_blank_string(
            evidence_mapping,
            key="rule_status",
        ),
        rule_logic=_require_non_blank_string(
            evidence_mapping,
            key="rule_logic",
        ),
        priority=_require_non_negative_int(
            evidence_mapping,
            key="priority",
        ),
        scope_specificity=_require_non_negative_int(
            evidence_mapping,
            key="scope_specificity",
        ),
        configured_output_account_id=_optional_uuid(
            evidence_mapping,
            key="configured_output_account_id",
        ),
        matched_output_account_id=_optional_uuid(
            evidence_mapping,
            key="matched_output_account_id",
        ),
        eligibility=RuleEligibilityDTO(
            status_allows_evaluation=_require_bool(
                eligibility_mapping,
                key="status_allows_evaluation",
            ),
            rule_is_complete=_require_bool(
                eligibility_mapping,
                key="rule_is_complete",
            ),
            effective_date_matches=_require_bool(
                eligibility_mapping,
                key="effective_date_matches",
            ),
            scope_matches=_require_bool(
                eligibility_mapping,
                key="scope_matches",
            ),
            is_eligible=_require_bool(
                eligibility_mapping,
                key="is_eligible",
            ),
        ),
        conditions=tuple(
            _evidence_to_condition(condition_value)
            for condition_value in conditions_value
        ),
        evaluated_condition_count=_require_non_negative_int(
            evidence_mapping,
            key="evaluated_condition_count",
        ),
        matched_condition_count=_require_non_negative_int(
            evidence_mapping,
            key="matched_condition_count",
        ),
        failed_condition_count=_require_non_negative_int(
            evidence_mapping,
            key="failed_condition_count",
        ),
        matched=_require_bool(
            evidence_mapping,
            key="matched",
        ),
    )

    _validate_evaluation(evaluation)

    return evaluation


def _evidence_to_condition(
    evidence: object,
) -> RuleConditionEvaluationDTO:
    """Reconstruct one condition evaluation from JSONB evidence."""

    evidence_mapping = _require_mapping(
        evidence,
        field_name="condition",
    )

    return RuleConditionEvaluationDTO(
        field=_require_non_blank_string(
            evidence_mapping,
            key="field",
        ),
        operator=_require_non_blank_string(
            evidence_mapping,
            key="operator",
        ),
        expected_value=_deserialize_evidence_value(
            _require_field(
                evidence_mapping,
                key="expected_value",
            ),
            field_name="expected_value",
        ),
        actual_value=_deserialize_evidence_value(
            _require_field(
                evidence_mapping,
                key="actual_value",
            ),
            field_name="actual_value",
        ),
        matched=_require_bool(
            evidence_mapping,
            key="matched",
        ),
    )


def _serialize_evidence_value(
    value: EvidenceValue,
) -> object:
    """Convert DTO evidence into a JSON-compatible scalar or list."""

    if value is None:
        return None

    if isinstance(value, str):
        return value

    if isinstance(value, tuple) and len(value) == 2:
        lower_value, upper_value = value

        if not isinstance(lower_value, str) or not isinstance(
            upper_value,
            str,
        ):
            raise DecisionMapperError(
                "Range evidence must contain two strings.",
            )

        return [
            lower_value,
            upper_value,
        ]

    raise DecisionMapperError(
        "Evidence values must be strings, two-item string tuples, or None."
    )


def _deserialize_evidence_value(
    value: object,
    *,
    field_name: str,
) -> EvidenceValue:
    """Convert JSON-compatible evidence back into DTO evidence."""

    if value is None:
        return None

    if isinstance(value, str):
        return value

    if isinstance(value, list) and len(value) == 2:
        lower_value, upper_value = value

        if isinstance(lower_value, str) and isinstance(
            upper_value,
            str,
        ):
            return (
                lower_value,
                upper_value,
            )

    raise DecisionMapperError(
        f"{field_name} must be a string, two-item string list, or None."
    )


def _validate_decision(
    decision: RuleEngineDecisionDTO,
) -> None:
    """Validate top-level decision and evidence consistency."""

    if not isinstance(decision.workspace_id, UUID):
        raise DecisionMapperError(
            "workspace_id must be a UUID.",
        )

    if not isinstance(decision.transaction_id, UUID):
        raise DecisionMapperError(
            "transaction_id must be a UUID.",
        )

    try:
        RuleDecisionStatus(decision.status)
    except ValueError as error:
        raise DecisionMapperError(
            f"Unsupported decision status: {decision.status!r}."
        ) from error

    try:
        RuleConflictKind(decision.conflict_kind)
    except ValueError as error:
        raise DecisionMapperError(
            f"Unsupported conflict kind: {decision.conflict_kind!r}."
        ) from error

    if not isinstance(decision.matched_rule_ids, tuple):
        raise DecisionMapperError(
            "matched_rule_ids must be a tuple.",
        )

    if not isinstance(decision.top_rule_ids, tuple):
        raise DecisionMapperError(
            "top_rule_ids must be a tuple.",
        )

    if not isinstance(decision.evaluations, tuple):
        raise DecisionMapperError(
            "evaluations must be a tuple.",
        )

    if not all(isinstance(rule_id, UUID) for rule_id in decision.matched_rule_ids):
        raise DecisionMapperError(
            "matched_rule_ids must contain only UUID values.",
        )

    if not all(isinstance(rule_id, UUID) for rule_id in decision.top_rule_ids):
        raise DecisionMapperError(
            "top_rule_ids must contain only UUID values.",
        )

    if len(set(decision.matched_rule_ids)) != len(decision.matched_rule_ids):
        raise DecisionMapperError(
            "matched_rule_ids cannot contain duplicates.",
        )

    if len(set(decision.top_rule_ids)) != len(decision.top_rule_ids):
        raise DecisionMapperError(
            "top_rule_ids cannot contain duplicates.",
        )

    if not set(decision.top_rule_ids).issubset(set(decision.matched_rule_ids)):
        raise DecisionMapperError(
            "top_rule_ids must be a subset of matched_rule_ids.",
        )

    _validate_non_negative_count(
        decision.evaluated_rule_count,
        field_name="evaluated_rule_count",
    )
    _validate_non_negative_count(
        decision.eligible_rule_count,
        field_name="eligible_rule_count",
    )
    _validate_non_negative_count(
        decision.ineligible_rule_count,
        field_name="ineligible_rule_count",
    )
    _validate_non_negative_count(
        decision.matched_rule_count,
        field_name="matched_rule_count",
    )
    _validate_non_negative_count(
        decision.unmatched_eligible_rule_count,
        field_name="unmatched_eligible_rule_count",
    )

    if (
        decision.evaluated_rule_count
        != decision.eligible_rule_count + decision.ineligible_rule_count
    ):
        raise DecisionMapperError(
            "evaluated_rule_count must equal eligible_rule_count plus "
            "ineligible_rule_count."
        )

    if (
        decision.eligible_rule_count
        != decision.matched_rule_count + decision.unmatched_eligible_rule_count
    ):
        raise DecisionMapperError(
            "eligible_rule_count must equal matched_rule_count plus "
            "unmatched_eligible_rule_count."
        )

    if len(decision.matched_rule_ids) != decision.matched_rule_count:
        raise DecisionMapperError(
            "matched_rule_ids count does not match matched_rule_count."
        )

    if len(decision.evaluations) != decision.evaluated_rule_count:
        raise DecisionMapperError(
            "evaluations count does not match evaluated_rule_count."
        )

    if not decision.decision_message.strip():
        raise DecisionMapperError(
            "decision_message cannot be blank.",
        )

    for evaluation in decision.evaluations:
        _validate_evaluation(evaluation)

        if evaluation.workspace_id != decision.workspace_id:
            raise DecisionMapperError(
                "Evaluation workspace_id does not match the decision workspace_id."
            )

    matched_evaluation_rule_ids = {
        evaluation.rule_id for evaluation in decision.evaluations if evaluation.matched
    }

    if matched_evaluation_rule_ids != set(decision.matched_rule_ids):
        raise DecisionMapperError(
            "matched_rule_ids do not match the successful evaluations."
        )

    _validate_decision_state(decision)


def _validate_evaluation(
    evaluation: RuleEvaluationDTO,
) -> None:
    """Validate one rule-evaluation DTO."""

    if not isinstance(evaluation, RuleEvaluationDTO):
        raise DecisionMapperError(
            "evaluations must contain RuleEvaluationDTO objects.",
        )

    if not isinstance(evaluation.rule_id, UUID):
        raise DecisionMapperError(
            "evaluation rule_id must be a UUID.",
        )

    if not isinstance(evaluation.workspace_id, UUID):
        raise DecisionMapperError(
            "evaluation workspace_id must be a UUID.",
        )

    if not evaluation.rule_name.strip():
        raise DecisionMapperError(
            "evaluation rule_name cannot be blank.",
        )

    if not isinstance(evaluation.eligibility, RuleEligibilityDTO):
        raise DecisionMapperError(
            "evaluation eligibility must be a RuleEligibilityDTO.",
        )

    if not isinstance(evaluation.conditions, tuple):
        raise DecisionMapperError(
            "evaluation conditions must be a tuple.",
        )

    if not all(
        isinstance(condition, RuleConditionEvaluationDTO)
        for condition in evaluation.conditions
    ):
        raise DecisionMapperError(
            "evaluation conditions contain an invalid object.",
        )

    _validate_non_negative_count(
        evaluation.priority,
        field_name="priority",
    )
    _validate_non_negative_count(
        evaluation.scope_specificity,
        field_name="scope_specificity",
    )
    _validate_non_negative_count(
        evaluation.evaluated_condition_count,
        field_name="evaluated_condition_count",
    )
    _validate_non_negative_count(
        evaluation.matched_condition_count,
        field_name="matched_condition_count",
    )
    _validate_non_negative_count(
        evaluation.failed_condition_count,
        field_name="failed_condition_count",
    )

    if evaluation.evaluated_condition_count != len(evaluation.conditions):
        raise DecisionMapperError(
            "evaluated_condition_count does not match conditions length."
        )

    if (
        evaluation.evaluated_condition_count
        != evaluation.matched_condition_count + evaluation.failed_condition_count
    ):
        raise DecisionMapperError(
            "evaluated condition counts are inconsistent.",
        )

    actual_matched_count = sum(condition.matched for condition in evaluation.conditions)

    if actual_matched_count != evaluation.matched_condition_count:
        raise DecisionMapperError(
            "matched_condition_count does not match condition evidence."
        )

    expected_eligibility = (
        evaluation.eligibility.status_allows_evaluation
        and evaluation.eligibility.rule_is_complete
        and evaluation.eligibility.effective_date_matches
        and evaluation.eligibility.scope_matches
    )

    if evaluation.eligibility.is_eligible != expected_eligibility:
        raise DecisionMapperError(
            "Eligibility summary is inconsistent.",
        )

    if not evaluation.eligibility.is_eligible and evaluation.conditions:
        raise DecisionMapperError(
            "Ineligible rules cannot contain evaluated conditions.",
        )

    if evaluation.matched:
        if not evaluation.eligibility.is_eligible:
            raise DecisionMapperError(
                "An ineligible rule cannot be marked as matched.",
            )

        if evaluation.configured_output_account_id is None:
            raise DecisionMapperError(
                "A matched rule requires a configured output account."
            )

        if (
            evaluation.matched_output_account_id
            != evaluation.configured_output_account_id
        ):
            raise DecisionMapperError(
                "Matched output must equal the configured output account."
            )
    elif evaluation.matched_output_account_id is not None:
        raise DecisionMapperError(
            "An unmatched rule cannot have a matched output account."
        )


def _validate_decision_state(
    decision: RuleEngineDecisionDTO,
) -> None:
    """Validate the status-specific decision state."""

    if decision.status == "unmatched":
        valid = (
            decision.conflict_kind == "none"
            and not decision.can_map
            and decision.requires_review
            and not decision.is_conflict_blocked
            and decision.output_account_id is None
            and decision.winning_rule_id is None
        )
    elif decision.status == "mapped":
        valid = (
            decision.conflict_kind == "none"
            and decision.can_map
            and not decision.requires_review
            and not decision.is_conflict_blocked
            and decision.output_account_id is not None
            and decision.winning_rule_id is not None
        )
    elif decision.status == "mapped_with_review":
        valid = (
            decision.conflict_kind == "redundant_same_output"
            and decision.can_map
            and decision.requires_review
            and not decision.is_conflict_blocked
            and decision.output_account_id is not None
            and decision.winning_rule_id is None
        )
    else:
        valid = (
            decision.status == "blocked_conflict"
            and decision.conflict_kind == "competing_outputs"
            and not decision.can_map
            and decision.requires_review
            and decision.is_conflict_blocked
            and decision.output_account_id is None
            and decision.winning_rule_id is None
        )

    if not valid:
        raise DecisionMapperError(
            "Decision fields are inconsistent with the decision status."
        )

    if (
        decision.winning_rule_id is not None
        and decision.winning_rule_id not in decision.top_rule_ids
    ):
        raise DecisionMapperError(
            "winning_rule_id must be included in top_rule_ids.",
        )


def _require_field(
    mapping: Mapping[str, object],
    *,
    key: str,
) -> object:
    """Return a required mapping value."""

    if key not in mapping:
        raise DecisionMapperError(
            f"Required evidence field is missing: {key!r}.",
        )

    return mapping[key]


def _require_mapping(
    value: object,
    *,
    field_name: str,
) -> Mapping[str, object]:
    """Return a required string-keyed evidence mapping."""

    if not isinstance(value, Mapping):
        raise DecisionMapperError(
            f"{field_name} must be stored as an object.",
        )

    if not all(isinstance(key, str) for key in value):
        raise DecisionMapperError(
            f"{field_name} must contain only string keys.",
        )

    return value


def _require_non_blank_string(
    mapping: Mapping[str, object],
    *,
    key: str,
) -> str:
    """Return a required nonblank string field."""

    value = _require_field(
        mapping,
        key=key,
    )

    if not isinstance(value, str) or not value.strip():
        raise DecisionMapperError(
            f"{key} must be a nonblank string.",
        )

    return value


def _require_bool(
    mapping: Mapping[str, object],
    *,
    key: str,
) -> bool:
    """Return a required Boolean field."""

    value = _require_field(
        mapping,
        key=key,
    )

    if not isinstance(value, bool):
        raise DecisionMapperError(
            f"{key} must be a Boolean.",
        )

    return value


def _require_non_negative_int(
    mapping: Mapping[str, object],
    *,
    key: str,
) -> int:
    """Return a required nonnegative integer field."""

    value = _require_field(
        mapping,
        key=key,
    )

    if isinstance(value, bool) or not isinstance(value, int):
        raise DecisionMapperError(
            f"{key} must be an integer.",
        )

    if value < 0:
        raise DecisionMapperError(
            f"{key} cannot be negative.",
        )

    return value


def _validate_non_negative_count(
    value: object,
    *,
    field_name: str,
) -> None:
    """Validate a nonnegative integer while rejecting Boolean values."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise DecisionMapperError(
            f"{field_name} must be an integer.",
        )

    if value < 0:
        raise DecisionMapperError(
            f"{field_name} cannot be negative.",
        )


def _require_uuid(
    mapping: Mapping[str, object],
    *,
    key: str,
) -> UUID:
    """Return a required UUID evidence field."""

    value = _require_field(
        mapping,
        key=key,
    )

    parsed_value = _parse_uuid(
        value,
        field_name=key,
    )

    if parsed_value is None:
        raise DecisionMapperError(
            f"{key} cannot be None.",
        )

    return parsed_value


def _optional_uuid(
    mapping: Mapping[str, object],
    *,
    key: str,
) -> UUID | None:
    """Return an optional UUID evidence field."""

    value = _require_field(
        mapping,
        key=key,
    )

    return _parse_uuid(
        value,
        field_name=key,
    )


def _parse_uuid(
    value: object,
    *,
    field_name: str,
) -> UUID | None:
    """Convert a UUID or UUID string into a UUID object."""

    if value is None:
        return None

    if isinstance(value, UUID):
        return value

    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as error:
            raise DecisionMapperError(
                f"{field_name} contains an invalid UUID.",
            ) from error

    raise DecisionMapperError(
        f"{field_name} must be a UUID string or None.",
    )


__all__ = [
    "DecisionMapperError",
    "decision_to_record",
    "record_to_decision",
]
