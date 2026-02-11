from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from .models import LocatorCandidate, ScoreBreakdown

BASE_RULE_SCORES: dict[str, float] = {
    "custom_override": 320.0,
    # 1) ID FIRST
    "stable_attr:id": 110.0,  # stabil id (en üst)
    "stable_attr:id_partial": 98.0,  # id dinamikse partial match
    # 2) QA / test attrs
    "stable_attr:data-testid": 92.0,
    "stable_attr:data-test": 90.0,
    "stable_attr:data-qa": 90.0,
    "stable_attr:data-cy": 90.0,  # eklediysen
    "stable_attr:data-e2e": 90.0,  # eklediysen
    # 3) Form / a11y
    "stable_attr:name": 86.0,
    "stable_attr:aria-label": 84.0,
    # 4) Input helpers
    "label_assoc": 78.0,
    "placeholder": 74.0,
    # 5) Structure / semantics
    "ancestor": 66.0,
    "meaningful_class": 54.0,
    "text_role": 52.0,
    "xpath_text": 40.0,
    # 6) Last resort
    "nth_fallback": 10.0,
}


def _base_stability(rule: str) -> float:
    if rule in BASE_RULE_SCORES:
        return BASE_RULE_SCORES[rule]
    prefix = rule.split(":", 1)[0]
    if prefix in BASE_RULE_SCORES:
        return BASE_RULE_SCORES[prefix]
    if rule.startswith("stable_attr:"):
        return 80.0
    return 40.0


def _uniqueness_score(count: int) -> float:
    if count == 1:
        return 120.0
    if count <= 0:
        return -120.0
    return max(-100.0, 30.0 - (count - 1) * 12.0)


def score_candidate(
    candidate: LocatorCandidate, learning_weights: dict[str, float]
) -> LocatorCandidate:
    uniqueness = _uniqueness_score(candidate.uniqueness_count)
    stability = _base_stability(candidate.rule)
    length_penalty = min(24.0, len(candidate.locator) / 8.0)

    dynamic_penalty = 0.0
    if candidate.metadata.get("uses_nth"):
        dynamic_penalty += 80.0
    if candidate.metadata.get("dynamic_class_count", 0):
        dynamic_penalty += 10.0 + 2.0 * float(
            candidate.metadata.get("dynamic_class_count", 0)
        )
    depth = candidate.locator.count(">") if candidate.locator_type == "CSS" else 0
    nth_count = candidate.locator.count("nth-of-type(")
    depth_penalty = float(depth * 12)
    nth_penalty = float(nth_count * 18)
    dynamic_penalty += depth_penalty + nth_penalty

    learning_adjustment = float(learning_weights.get(candidate.rule, 0.0))
    learning_adjustment += float(
        learning_weights.get(candidate.rule.split(":", 1)[0], 0.0)
    )

    total = (
        uniqueness + stability - length_penalty - dynamic_penalty + learning_adjustment
    )
    if candidate.rule == "nth_fallback":
        total = min(total, 45.0)
    breakdown = ScoreBreakdown(
        uniqueness=round(uniqueness, 2),
        stability=round(stability, 2),
        length_penalty=round(length_penalty, 2),
        dynamic_penalty=round(dynamic_penalty, 2),
        learning_adjustment=round(learning_adjustment, 2),
        total=round(total, 2),
    )
    metadata = dict(candidate.metadata)
    metadata["depth"] = depth
    metadata["nth_count"] = nth_count
    metadata["depth_penalty"] = round(depth_penalty, 2)
    metadata["nth_penalty"] = round(nth_penalty, 2)
    return replace(candidate, score=breakdown.total, breakdown=breakdown, metadata=metadata)


def score_candidates(
    candidates: Iterable[LocatorCandidate],
    learning_weights: dict[str, float] | None = None,
) -> list[LocatorCandidate]:
    weights = learning_weights or {}
    scored = [score_candidate(candidate, weights) for candidate in candidates]
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored
