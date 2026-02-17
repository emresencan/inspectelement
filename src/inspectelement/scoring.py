from __future__ import annotations

from dataclasses import replace
import re
from typing import Iterable

from .models import ConfidenceLevel, LocatorCandidate, ScoreBreakdown
from .selector_rules import is_forbidden_locator, is_index_based_xpath

BASE_RULE_SCORES: dict[str, float] = {
    "custom_override": 220.0,
    "stable_attr:id": 112.0,
    "stable_attr:data-testid": 102.0,
    "stable_attr:data-test": 100.0,
    "stable_attr:data-qa": 98.0,
    "stable_attr:data-cy": 98.0,
    "stable_attr:data-e2e": 98.0,
    "stable_attr:name": 92.0,
    "stable_attr:aria-label": 84.0,
    "stable_attr:role": 82.0,
    "stable_attr:title": 80.0,
    "placeholder": 76.0,
    "label_assoc": 72.0,
    "text_xpath": 68.0,
    "xpath_text": 68.0,
    "ancestor": 54.0,
    "meaningful_class": 46.0,
    "nth_fallback": 8.0,
}

STRATEGY_BASE_BONUS: dict[str, float] = {
    "id": 24.0,
    "data_attr": 18.0,
    "name": 16.0,
    "accessibility": 12.0,
    "placeholder": 10.0,
    "label_relation": 8.0,
    "text_xpath": 6.0,
    "ancestor": 0.0,
    "class": -8.0,
    "fallback": -20.0,
}


def _base_stability(rule: str) -> float:
    if rule in BASE_RULE_SCORES:
        return BASE_RULE_SCORES[rule]
    prefix = rule.split(":", 1)[0]
    if prefix in BASE_RULE_SCORES:
        return BASE_RULE_SCORES[prefix]
    if rule.startswith("stable_attr:"):
        return 84.0
    return 40.0


def _uniqueness_score(count: int) -> float:
    if count == 1:
        return 120.0
    if count <= 0:
        return -130.0
    return max(-110.0, 24.0 - (count - 1) * 14.0)


def score_candidate(
    candidate: LocatorCandidate, learning_weights: dict[str, float]
) -> LocatorCandidate:
    strategy = _strategy_type(candidate)

    uniqueness = _uniqueness_score(candidate.uniqueness_count)
    stability = _base_stability(candidate.rule) + STRATEGY_BASE_BONUS.get(strategy, 0.0)

    locator_length = len(candidate.locator)
    simplicity = max(0.0, 28.0 - locator_length / 9.0)
    stability += simplicity

    length_penalty = max(0.0, (locator_length - 80) / 4.0)

    dynamic_penalty = 0.0
    if candidate.metadata.get("uses_nth"):
        dynamic_penalty += 85.0
    if _looks_dynamic_class_locator(candidate.locator):
        dynamic_penalty += 20.0
    if is_forbidden_locator(candidate.locator, candidate.locator_type):
        dynamic_penalty += 90.0
    if candidate.locator_type == "XPath" and is_index_based_xpath(candidate.locator):
        dynamic_penalty += 45.0

    if candidate.metadata.get("stable") is False:
        dynamic_penalty += 50.0
    elif candidate.metadata.get("stable") is True:
        stability += 12.0

    entropy_value = float(candidate.metadata.get("stability_entropy", 0.0) or 0.0)
    digit_value = float(candidate.metadata.get("stability_digit_ratio", 0.0) or 0.0)
    stability_score = float(candidate.metadata.get("stability_score", 0.0) or 0.0)
    salvage_penalty = float(candidate.metadata.get("salvage_penalty", 0.0) or 0.0)
    if stability_score > 0:
        # Convert attribute stability into bounded positive adjustment.
        stability += (stability_score - 50.0) * 0.25
    if entropy_value >= 3.9:
        dynamic_penalty += 30.0
    elif entropy_value >= 3.3:
        dynamic_penalty += 12.0
    if digit_value > 0.4:
        dynamic_penalty += 26.0
    elif digit_value > 0.25:
        dynamic_penalty += 9.0
    if candidate.metadata.get("dynamic_detected"):
        dynamic_penalty += 16.0
    if candidate.metadata.get("prefix_salvaged"):
        dynamic_penalty += salvage_penalty or 14.0
        stability -= 6.0
    if candidate.metadata.get("generic_penalty"):
        dynamic_penalty += float(candidate.metadata.get("generic_penalty", 0.0))

    if strategy in {"id", "name", "data_attr", "accessibility", "placeholder", "label_relation"}:
        if candidate.uniqueness_count != 1:
            dynamic_penalty += 40.0

    if strategy == "text_xpath":
        if candidate.uniqueness_count == 1:
            stability += 10.0
        elif candidate.uniqueness_count > 1:
            dynamic_penalty += 14.0

    learning_adjustment = float(learning_weights.get(candidate.rule, 0.0))
    learning_adjustment += float(learning_weights.get(candidate.rule.split(":", 1)[0], 0.0))

    total = uniqueness + stability - length_penalty - dynamic_penalty + learning_adjustment
    if strategy == "fallback":
        total = min(total, 35.0)

    confidence = _confidence_from_score(total)

    breakdown = ScoreBreakdown(
        uniqueness=round(uniqueness, 2),
        stability=round(stability, 2),
        length_penalty=round(length_penalty, 2),
        dynamic_penalty=round(dynamic_penalty, 2),
        learning_adjustment=round(learning_adjustment, 2),
        total=round(total, 2),
    )

    metadata = dict(candidate.metadata)
    metadata["strategy_type"] = strategy
    metadata["simplicity"] = round(simplicity, 2)
    metadata["confidence"] = confidence
    metadata["stable"] = bool(metadata.get("stable", True))

    return replace(
        candidate,
        score=breakdown.total,
        breakdown=breakdown,
        metadata=metadata,
        confidence=confidence,
        strategy_type=strategy,
    )


def score_candidates(
    candidates: Iterable[LocatorCandidate],
    learning_weights: dict[str, float] | None = None,
) -> list[LocatorCandidate]:
    weights = learning_weights or {}
    scored = [score_candidate(candidate, weights) for candidate in candidates]

    scored.sort(
        key=lambda item: (
            -float(item.score),
            -float(item.breakdown.stability if item.breakdown else 0.0),
            -float(item.metadata.get("simplicity", 0.0)),
            len(item.locator),
            item.locator,
        )
    )
    return scored


def _strategy_type(candidate: LocatorCandidate) -> str:
    raw = str(candidate.metadata.get("strategy_type") or "").strip().lower()
    if raw:
        return raw

    rule = candidate.rule.strip().lower()
    if rule.startswith("stable_attr:id"):
        return "id"
    if rule.startswith("stable_attr:data-"):
        return "data_attr"
    if rule.startswith("stable_attr:name"):
        return "name"
    if rule.startswith("stable_attr:aria") or rule.startswith("stable_attr:role") or rule.startswith("stable_attr:title"):
        return "accessibility"
    if rule in {"placeholder", "label_assoc"}:
        return "placeholder" if rule == "placeholder" else "label_relation"
    if "text" in rule:
        return "text_xpath"
    if rule == "ancestor":
        return "ancestor"
    if rule == "meaningful_class":
        return "class"
    if rule == "nth_fallback":
        return "fallback"
    if rule == "custom_override":
        return "id"
    return "fallback"


def _confidence_from_score(score: float) -> ConfidenceLevel:
    if score >= 220:
        return "HIGH"
    if score >= 150:
        return "MEDIUM"
    return "LOW"


def _looks_dynamic_class_locator(locator: str) -> bool:
    lowered = locator.lower()
    patterns = (
        r"\.[a-f0-9]{8,}",
        r"\.css-[a-z0-9_-]{4,}",
        r"\.jss\d+",
        r"\.sc-[a-z0-9]+",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)
