"""
Deterministic ranking of matched BSI rule evaluations.

This module ranks rules that already matched a normalized transaction.

Decision precedence
-------------------
1. Higher rule priority
2. Higher organizational scope specificity

Rule identifiers provide stable display ordering only. They never resolve
a business tie. Rules with equal priority and scope specificity remain
tied for later conflict detection.

This module does not:

- Evaluate rule conditions
- Modify transactions
- Silently resolve ambiguous mappings
- Persist results
- Call AI or external services
"""

from dataclasses import dataclass
from uuid import UUID

from bsi.domain.rules.evaluator import RuleEvaluation
from bsi.domain.rules.models import (
    MAX_RULE_PRIORITY,
    MIN_RULE_PRIORITY,
)


class RuleRankingError(ValueError):
    """Raised when matched rule evaluations cannot be ranked safely."""


@dataclass(frozen=True, slots=True)
class RuleRank:
    """
    Business rank assigned to one matched rule.

    Attributes
    ----------
    priority:
        Explicit rule priority. Higher values rank first.

    scope_specificity:
        Number of populated company, brand, store, and bank-account
        restrictions. More specific rules rank before broader rules when
        priority is equal.
    """

    priority: int
    scope_specificity: int

    def __post_init__(self) -> None:
        """Validate the ranking values."""

        if isinstance(self.priority, bool) or not isinstance(
            self.priority,
            int,
        ):
            raise RuleRankingError("priority must be an integer.")

        if not MIN_RULE_PRIORITY <= self.priority <= MAX_RULE_PRIORITY:
            raise RuleRankingError(
                f"priority must be between {MIN_RULE_PRIORITY} and {MAX_RULE_PRIORITY}."
            )

        if isinstance(self.scope_specificity, bool) or not isinstance(
            self.scope_specificity,
            int,
        ):
            raise RuleRankingError("scope_specificity must be an integer.")

        if not 0 <= self.scope_specificity <= 4:
            raise RuleRankingError("scope_specificity must be between 0 and 4.")

    @property
    def decision_key(self) -> tuple[int, int]:
        """
        Return the business values used to compare matched rules.

        Returns
        -------
        tuple[int, int]
            Priority followed by scope specificity.
        """

        return self.priority, self.scope_specificity


@dataclass(frozen=True, slots=True)
class RuleRanking:
    """
    Ordered matched-rule candidates for one transaction and workspace.

    Candidates are ordered by:

    - Higher priority
    - Higher scope specificity
    - Rule UUID for stable presentation only

    The UUID does not resolve a financial mapping tie.
    """

    candidates: tuple[RuleEvaluation, ...]

    def __post_init__(self) -> None:
        """Validate and deterministically order matched candidates."""

        if not isinstance(self.candidates, tuple):
            raise RuleRankingError(
                "candidates must be a tuple of RuleEvaluation objects."
            )

        if not all(
            isinstance(candidate, RuleEvaluation) for candidate in self.candidates
        ):
            raise RuleRankingError(
                "candidates must contain only RuleEvaluation objects."
            )

        if not all(candidate.matched for candidate in self.candidates):
            raise RuleRankingError(
                "candidates must contain only matched rule evaluations."
            )

        _validate_evaluation_context(self.candidates)
        _validate_unique_rules(self.candidates)

        ranked_candidates = tuple(
            sorted(
                self.candidates,
                key=_candidate_sort_key,
            )
        )

        object.__setattr__(
            self,
            "candidates",
            ranked_candidates,
        )

    @property
    def candidate_count(self) -> int:
        """Return the number of matched rule candidates."""

        return len(self.candidates)

    @property
    def has_candidates(self) -> bool:
        """Return whether at least one rule matched."""

        return bool(self.candidates)

    @property
    def transaction_id(self) -> UUID | None:
        """Return the evaluated transaction identifier when available."""

        if not self.candidates:
            return None

        return self.candidates[0].transaction_id

    @property
    def workspace_id(self) -> UUID | None:
        """Return the owning workspace identifier when available."""

        if not self.candidates:
            return None

        return self.candidates[0].rule.workspace_id

    @property
    def top_rank(self) -> RuleRank | None:
        """Return the strongest business rank when candidates exist."""

        if not self.candidates:
            return None

        return get_rule_rank(self.candidates[0])

    @property
    def top_candidates(self) -> tuple[RuleEvaluation, ...]:
        """
        Return every candidate sharing the strongest business rank.

        Multiple top candidates remain tied. Stable UUID ordering does not
        convert an ambiguous financial decision into a unique winner.
        """

        strongest_rank = self.top_rank

        if strongest_rank is None:
            return ()

        return tuple(
            candidate
            for candidate in self.candidates
            if get_rule_rank(candidate) == strongest_rank
        )

    @property
    def has_top_tie(self) -> bool:
        """Return whether multiple rules share the strongest rank."""

        return len(self.top_candidates) > 1

    @property
    def winner(self) -> RuleEvaluation | None:
        """
        Return the uniquely highest-ranked candidate.

        Returns
        -------
        RuleEvaluation | None
            The unique winner, or None when no rule matched or the top
            candidates remain tied.
        """

        strongest_candidates = self.top_candidates

        if len(strongest_candidates) != 1:
            return None

        return strongest_candidates[0]


def rank_rule_evaluations(
    evaluations: tuple[RuleEvaluation, ...],
) -> RuleRanking:
    """
    Filter and rank successful evaluations for one transaction.

    Parameters
    ----------
    evaluations:
        Rule evaluations produced for one transaction within one
        workspace. Eligible failures and ineligible rules are accepted
        but excluded from the ranked candidate collection.

    Returns
    -------
    RuleRanking
        Deterministically ordered matched candidates.

    Raises
    ------
    RuleRankingError
        If the collection is invalid, contains duplicate rule
        evaluations, combines transactions, or combines workspaces.
    """

    if not isinstance(evaluations, tuple):
        raise RuleRankingError("evaluations must be a tuple of RuleEvaluation objects.")

    if not all(isinstance(evaluation, RuleEvaluation) for evaluation in evaluations):
        raise RuleRankingError("evaluations must contain only RuleEvaluation objects.")

    _validate_evaluation_context(evaluations)
    _validate_unique_rules(evaluations)

    matched_evaluations = tuple(
        evaluation for evaluation in evaluations if evaluation.matched
    )

    return RuleRanking(
        candidates=matched_evaluations,
    )


def get_rule_rank(
    evaluation: RuleEvaluation,
) -> RuleRank:
    """
    Build the business rank for one rule evaluation.

    Parameters
    ----------
    evaluation:
        Rule evaluation whose configured priority and scope specificity
        should be represented.

    Returns
    -------
    RuleRank
        Immutable business-ranking values.
    """

    if not isinstance(evaluation, RuleEvaluation):
        raise RuleRankingError("evaluation must be a RuleEvaluation.")

    return RuleRank(
        priority=evaluation.rule.priority,
        scope_specificity=evaluation.rule.scope_specificity,
    )


def _candidate_sort_key(
    evaluation: RuleEvaluation,
) -> tuple[int, int, str]:
    """
    Return deterministic candidate ordering.

    Negative numeric values create descending priority and specificity.
    The UUID creates stable output order but is not part of RuleRank.
    """

    rank = get_rule_rank(evaluation)

    return (
        -rank.priority,
        -rank.scope_specificity,
        str(evaluation.rule.rule_id),
    )


def _validate_evaluation_context(
    evaluations: tuple[RuleEvaluation, ...],
) -> None:
    """Ensure evaluations belong to one transaction and workspace."""

    if not evaluations:
        return

    transaction_ids = {evaluation.transaction_id for evaluation in evaluations}

    if len(transaction_ids) != 1:
        raise RuleRankingError("evaluations must belong to one transaction.")

    workspace_ids = {evaluation.rule.workspace_id for evaluation in evaluations}

    if len(workspace_ids) != 1:
        raise RuleRankingError("evaluations must belong to one workspace.")


def _validate_unique_rules(
    evaluations: tuple[RuleEvaluation, ...],
) -> None:
    """Reject duplicate evaluations of the same rule version."""

    rule_ids = [evaluation.rule.rule_id for evaluation in evaluations]

    if len(rule_ids) != len(set(rule_ids)):
        raise RuleRankingError("evaluations cannot contain duplicate rule identifiers.")
