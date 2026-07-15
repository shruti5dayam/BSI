"""
Deterministic rule-engine orchestration for BSI.

This module coordinates the complete transaction-mapping decision:

1. Validate the workspace, transaction, and rule collection.
2. Evaluate every rule against the normalized transaction.
3. Rank successful rule matches.
4. Assess top-ranked rule conflicts.
5. Return one immutable, audit-friendly engine result.

The engine remains framework independent. It does not:

- Read Excel or CSV files
- Query databases
- Modify transactions
- Resolve Chart of Accounts details
- Persist mapping results
- Call LLMs, embeddings, or external services
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from bsi.domain.rules.conflicts import (
    RuleConflictAssessment,
    RuleConflictKind,
    assess_rule_conflict,
)
from bsi.domain.rules.evaluator import (
    RuleEvaluation,
    evaluate_rule,
)
from bsi.domain.rules.models import RuleDefinition
from bsi.domain.rules.ranking import rank_rule_evaluations
from bsi.domain.transactions.models import NormalizedTransaction


class RuleEngineError(ValueError):
    """Raised when the deterministic rule engine cannot run safely."""


class RuleDecisionStatus(StrEnum):
    """
    Final status of one deterministic transaction-mapping decision.

    UNMATCHED
        No deterministic rule matched. Human review is required.

    MAPPED
        One uniquely ranked rule produced a safe mapping.

    MAPPED_WITH_REVIEW
        Multiple top-ranked rules produced the same COA output. Mapping
        is safe, but the redundant rule configuration requires review.

    BLOCKED_CONFLICT
        Multiple top-ranked rules produced different COA outputs.
        Mapping is blocked until a human resolves the conflict.
    """

    UNMATCHED = "unmatched"
    MAPPED = "mapped"
    MAPPED_WITH_REVIEW = "mapped_with_review"
    BLOCKED_CONFLICT = "blocked_conflict"


@dataclass(frozen=True, slots=True)
class RuleEngineResult:
    """
    Immutable final result for one transaction and workspace.

    Attributes
    ----------
    workspace_id:
        Tenant boundary within which the rules were evaluated.

    transaction_id:
        Normalized transaction that received the mapping decision.

    evaluations:
        Evaluation evidence for every supplied rule.

        Evaluations are stored in stable rule-identifier order.

    conflict_assessment:
        Ranking and conflict evidence derived from successful matches.
    """

    workspace_id: UUID
    transaction_id: UUID
    evaluations: tuple[RuleEvaluation, ...]
    conflict_assessment: RuleConflictAssessment

    def __post_init__(self) -> None:
        """Validate consistency across all engine evidence."""

        if not isinstance(self.workspace_id, UUID):
            raise RuleEngineError("workspace_id must be a UUID.")

        if not isinstance(self.transaction_id, UUID):
            raise RuleEngineError("transaction_id must be a UUID.")

        if not isinstance(self.evaluations, tuple):
            raise RuleEngineError(
                "evaluations must be a tuple of RuleEvaluation objects."
            )

        if not all(
            isinstance(evaluation, RuleEvaluation) for evaluation in self.evaluations
        ):
            raise RuleEngineError(
                "evaluations must contain only RuleEvaluation objects."
            )

        if not isinstance(
            self.conflict_assessment,
            RuleConflictAssessment,
        ):
            raise RuleEngineError(
                "conflict_assessment must be a RuleConflictAssessment."
            )

        _validate_result_evaluation_context(
            workspace_id=self.workspace_id,
            transaction_id=self.transaction_id,
            evaluations=self.evaluations,
        )

        _validate_result_conflict_consistency(
            evaluations=self.evaluations,
            conflict_assessment=self.conflict_assessment,
        )

    @property
    def status(self) -> RuleDecisionStatus:
        """Return the final deterministic mapping status."""

        if self.conflict_assessment.kind is RuleConflictKind.COMPETING_OUTPUTS:
            return RuleDecisionStatus.BLOCKED_CONFLICT

        if self.conflict_assessment.kind is RuleConflictKind.REDUNDANT_SAME_OUTPUT:
            return RuleDecisionStatus.MAPPED_WITH_REVIEW

        if self.conflict_assessment.can_map:
            return RuleDecisionStatus.MAPPED

        return RuleDecisionStatus.UNMATCHED

    @property
    def output_account_id(self) -> UUID | None:
        """Return the safe COA mapping output when one exists."""

        return self.conflict_assessment.resolved_output_account_id

    @property
    def winning_rule_id(self) -> UUID | None:
        """
        Return the uniquely winning rule identifier.

        Same-output ties have a safe output but no artificial winner.
        """

        return self.conflict_assessment.winning_rule_id

    @property
    def top_rule_ids(self) -> tuple[UUID, ...]:
        """Return top-ranked rule identifiers in stable order."""

        return self.conflict_assessment.top_rule_ids

    @property
    def matched_rule_ids(self) -> tuple[UUID, ...]:
        """Return every matched rule identifier in ranking order."""

        return tuple(
            candidate.rule.rule_id
            for candidate in self.conflict_assessment.ranking.candidates
        )

    @property
    def evaluated_rule_count(self) -> int:
        """Return the number of rules considered by the engine."""

        return len(self.evaluations)

    @property
    def eligible_rule_count(self) -> int:
        """Return the number of rules eligible for condition evaluation."""

        return sum(
            evaluation.eligibility.is_eligible for evaluation in self.evaluations
        )

    @property
    def ineligible_rule_count(self) -> int:
        """Return the number of rules rejected before condition evaluation."""

        return sum(
            not evaluation.eligibility.is_eligible for evaluation in self.evaluations
        )

    @property
    def matched_rule_count(self) -> int:
        """Return the number of successful rule matches."""

        return self.conflict_assessment.matched_candidate_count

    @property
    def unmatched_eligible_rule_count(self) -> int:
        """Return eligible rules whose conditions did not match."""

        return sum(
            evaluation.eligibility.is_eligible and not evaluation.matched
            for evaluation in self.evaluations
        )

    @property
    def can_map(self) -> bool:
        """Return whether a safe deterministic COA output exists."""

        return self.output_account_id is not None

    @property
    def requires_review(self) -> bool:
        """
        Return whether the transaction or rule configuration needs review.

        Review is required for:

        - Unmatched transactions
        - Redundant same-output rules
        - Competing-output conflicts
        """

        return self.status is not RuleDecisionStatus.MAPPED

    @property
    def is_conflict_blocked(self) -> bool:
        """Return whether competing outputs prevent mapping."""

        return self.status is RuleDecisionStatus.BLOCKED_CONFLICT

    @property
    def decision_message(self) -> str:
        """Return the audit-friendly decision explanation."""

        return self.conflict_assessment.decision_message


def evaluate_transaction_rules(
    *,
    workspace_id: UUID,
    transaction: NormalizedTransaction,
    rules: tuple[RuleDefinition, ...],
) -> RuleEngineResult:
    """
    Run the deterministic rule engine for one transaction.

    Parameters
    ----------
    workspace_id:
        Tenant boundary owning the transaction and supplied rules.

    transaction:
        Immutable normalized bank transaction.

    rules:
        Immutable rule collection belonging to the same workspace.

        Rules may be active, inactive, complete, incomplete, matched, or
        unmatched. The evaluator records the result for every rule.

    Returns
    -------
    RuleEngineResult
        Complete evaluation, ranking, conflict, and mapping evidence.

    Raises
    ------
    RuleEngineError
        If inputs are invalid, rules cross workspace boundaries, or the
        same rule identifier appears more than once.
    """

    _validate_engine_inputs(
        workspace_id=workspace_id,
        transaction=transaction,
        rules=rules,
    )

    ordered_rules = tuple(
        sorted(
            rules,
            key=lambda rule: str(rule.rule_id),
        )
    )

    evaluations = tuple(
        evaluate_rule(
            rule=rule,
            transaction=transaction,
        )
        for rule in ordered_rules
    )

    ranking = rank_rule_evaluations(evaluations)
    conflict_assessment = assess_rule_conflict(ranking)

    return RuleEngineResult(
        workspace_id=workspace_id,
        transaction_id=transaction.transaction_id,
        evaluations=evaluations,
        conflict_assessment=conflict_assessment,
    )


def _validate_engine_inputs(
    *,
    workspace_id: UUID,
    transaction: NormalizedTransaction,
    rules: tuple[RuleDefinition, ...],
) -> None:
    """Validate the public rule-engine input boundary."""

    if not isinstance(workspace_id, UUID):
        raise RuleEngineError("workspace_id must be a UUID.")

    if not isinstance(transaction, NormalizedTransaction):
        raise RuleEngineError("transaction must be a NormalizedTransaction.")

    if not isinstance(rules, tuple):
        raise RuleEngineError("rules must be a tuple of RuleDefinition objects.")

    if not all(isinstance(rule, RuleDefinition) for rule in rules):
        raise RuleEngineError("rules must contain only RuleDefinition objects.")

    mismatched_workspace_rule_ids = tuple(
        rule.rule_id for rule in rules if rule.workspace_id != workspace_id
    )

    if mismatched_workspace_rule_ids:
        raise RuleEngineError("Every rule must belong to the supplied workspace.")

    rule_ids = [rule.rule_id for rule in rules]

    if len(rule_ids) != len(set(rule_ids)):
        raise RuleEngineError("rules cannot contain duplicate rule identifiers.")


def _validate_result_evaluation_context(
    *,
    workspace_id: UUID,
    transaction_id: UUID,
    evaluations: tuple[RuleEvaluation, ...],
) -> None:
    """Validate transaction and workspace lineage in engine evidence."""

    if any(evaluation.transaction_id != transaction_id for evaluation in evaluations):
        raise RuleEngineError("Every evaluation must belong to the result transaction.")

    if any(evaluation.rule.workspace_id != workspace_id for evaluation in evaluations):
        raise RuleEngineError("Every evaluation must belong to the result workspace.")

    rule_ids = [evaluation.rule.rule_id for evaluation in evaluations]

    if len(rule_ids) != len(set(rule_ids)):
        raise RuleEngineError("evaluations cannot contain duplicate rule identifiers.")


def _validate_result_conflict_consistency(
    *,
    evaluations: tuple[RuleEvaluation, ...],
    conflict_assessment: RuleConflictAssessment,
) -> None:
    """Ensure conflict evidence represents the matched evaluations."""

    evaluated_matched_rule_ids = {
        evaluation.rule.rule_id for evaluation in evaluations if evaluation.matched
    }

    ranked_rule_ids = {
        candidate.rule.rule_id for candidate in conflict_assessment.ranking.candidates
    }

    if evaluated_matched_rule_ids != ranked_rule_ids:
        raise RuleEngineError(
            "Conflict assessment must represent every matched evaluation."
        )
