"""
Conflict assessment for deterministic BSI rule matches.

This module inspects the strongest candidates produced by the ranking
layer and determines whether the transaction can be mapped safely.

Conflict behavior
-----------------
- No matched candidates:
    No mapping and no conflict.

- One uniquely ranked candidate:
    Use its deterministic COA output.

- Multiple top-ranked candidates with the same COA output:
    The financial mapping is safe, but redundant rules require review.

- Multiple top-ranked candidates with different COA outputs:
    The mapping is ambiguous and must be blocked for human review.

Lower-ranked rules do not create a decision conflict because they have
already lost through explicit priority or scope specificity.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from bsi.domain.rules.evaluator import RuleEvaluation
from bsi.domain.rules.ranking import RuleRanking


class RuleConflictError(ValueError):
    """Raised when rule-conflict evidence cannot be assessed safely."""


class RuleConflictKind(StrEnum):
    """
    Classification of conflict among the strongest rule candidates.

    NONE
        No top-rank conflict exists.

    REDUNDANT_SAME_OUTPUT
        Multiple top-ranked rules map to the same COA account.

        The transaction may still be mapped because the financial output
        is identical, but the overlapping rules should be reviewed.

    COMPETING_OUTPUTS
        Multiple top-ranked rules map to different COA accounts.

        The transaction must remain unmapped until reviewed.
    """

    NONE = "none"
    REDUNDANT_SAME_OUTPUT = "redundant_same_output"
    COMPETING_OUTPUTS = "competing_outputs"


@dataclass(frozen=True, slots=True)
class RuleConflictAssessment:
    """
    Immutable conflict assessment for one ranked transaction decision.

    Attributes
    ----------
    ranking:
        Ranked matched-rule candidates for one transaction and workspace.

    Notes
    -----
    Conflict information is calculated from the ranking instead of being
    stored separately. This prevents the conflict type, candidate rules,
    and mapping output from becoming inconsistent.
    """

    ranking: RuleRanking

    def __post_init__(self) -> None:
        """Validate the supplied ranking evidence."""

        if not isinstance(self.ranking, RuleRanking):
            raise RuleConflictError(
                "ranking must be a RuleRanking."
            )

    @property
    def transaction_id(self) -> UUID | None:
        """Return the transaction identifier when candidates exist."""

        return self.ranking.transaction_id

    @property
    def workspace_id(self) -> UUID | None:
        """Return the owning workspace identifier when candidates exist."""

        return self.ranking.workspace_id

    @property
    def matched_candidate_count(self) -> int:
        """Return the total number of matched rule candidates."""

        return self.ranking.candidate_count

    @property
    def top_candidates(self) -> tuple[RuleEvaluation, ...]:
        """Return every candidate sharing the strongest business rank."""

        return self.ranking.top_candidates

    @property
    def top_candidate_count(self) -> int:
        """Return the number of strongest candidates."""

        return len(self.top_candidates)

    @property
    def top_rule_ids(self) -> tuple[UUID, ...]:
        """Return top-ranked rule identifiers in stable ranking order."""

        return tuple(
            candidate.rule.rule_id
            for candidate in self.top_candidates
        )

    @property
    def output_account_ids(self) -> tuple[UUID, ...]:
        """
        Return unique top-candidate COA outputs in stable UUID order.

        Returns
        -------
        tuple[UUID, ...]
            Unique output identifiers belonging only to the strongest
            candidates.
        """

        account_ids = {
            _require_output_account_id(candidate)
            for candidate in self.top_candidates
        }

        return tuple(
            sorted(
                account_ids,
                key=str,
            )
        )

    @property
    def unique_output_count(self) -> int:
        """Return the number of different top-ranked mapping outputs."""

        return len(self.output_account_ids)

    @property
    def kind(self) -> RuleConflictKind:
        """Classify conflict among the strongest candidates."""

        if self.top_candidate_count <= 1:
            return RuleConflictKind.NONE

        if self.unique_output_count == 1:
            return RuleConflictKind.REDUNDANT_SAME_OUTPUT

        return RuleConflictKind.COMPETING_OUTPUTS

    @property
    def has_conflict(self) -> bool:
        """Return whether multiple candidates share the strongest rank."""

        return self.kind is not RuleConflictKind.NONE

    @property
    def requires_review(self) -> bool:
        """
        Return whether rule configuration should be reviewed.

        Both redundant and competing top-ranked rules require review.
        Only competing outputs block the financial mapping.
        """

        return self.has_conflict

    @property
    def is_blocking(self) -> bool:
        """Return whether the conflict prevents deterministic mapping."""

        return self.kind is RuleConflictKind.COMPETING_OUTPUTS

    @property
    def winning_rule_id(self) -> UUID | None:
        """
        Return the uniquely winning rule identifier.

        Same-output ties do not receive an artificial winning rule,
        even though their common financial output may be used.
        """

        winner = self.ranking.winner

        if winner is None:
            return None

        return winner.rule.rule_id

    @property
    def resolved_output_account_id(self) -> UUID | None:
        """
        Return the safe deterministic COA output.

        Returns
        -------
        UUID | None
            Unique winner output, or the shared output of redundant
            top-ranked rules.

            None is returned when no rules matched or when top-ranked
            rules produce competing outputs.
        """

        winner = self.ranking.winner

        if winner is not None:
            return _require_output_account_id(winner)

        if self.kind is RuleConflictKind.REDUNDANT_SAME_OUTPUT:
            return self.output_account_ids[0]

        return None

    @property
    def can_map(self) -> bool:
        """Return whether a safe deterministic output is available."""

        return self.resolved_output_account_id is not None

    @property
    def decision_message(self) -> str:
        """Return a concise audit-friendly explanation."""

        if self.matched_candidate_count == 0:
            return "No deterministic rules matched the transaction."

        if self.kind is RuleConflictKind.NONE:
            return "One uniquely ranked deterministic rule matched."

        if self.kind is RuleConflictKind.REDUNDANT_SAME_OUTPUT:
            return (
                "Multiple top-ranked rules matched the same COA "
                "account; mapping is allowed with rule review required."
            )

        return (
            "Multiple top-ranked rules produced different COA accounts; "
            "mapping is blocked pending review."
        )


def assess_rule_conflict(
    ranking: RuleRanking,
) -> RuleConflictAssessment:
    """
    Assess conflicts among ranked rule candidates.

    Parameters
    ----------
    ranking:
        Matched rules already ordered by the deterministic ranking layer.

    Returns
    -------
    RuleConflictAssessment
        Conflict kind, review requirement, blocking status, and safe
        output account when one exists.

    Raises
    ------
    RuleConflictError
        If ranking is not a RuleRanking object.
    """

    if not isinstance(ranking, RuleRanking):
        raise RuleConflictError(
            "ranking must be a RuleRanking."
        )

    return RuleConflictAssessment(
        ranking=ranking,
    )


def _require_output_account_id(
    evaluation: RuleEvaluation,
) -> UUID:
    """
    Return the mapping output of a matched candidate.

    A matched RuleEvaluation should always have a complete RuleDefinition
    and therefore a COA output. This defensive check protects the
    conflict layer if an inconsistent object is ever introduced.
    """

    output_account_id = evaluation.output_account_id

    if output_account_id is None:
        raise RuleConflictError(
            "Matched rule candidates must contain a COA output."
        )

    return output_account_id