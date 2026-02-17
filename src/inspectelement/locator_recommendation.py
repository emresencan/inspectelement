from __future__ import annotations

import re

from .models import LocatorCandidate
from .selector_rules import is_absolute_xpath, is_forbidden_locator, is_index_based_xpath


def recommend_locator_candidates(candidates: list[LocatorCandidate]) -> list[LocatorCandidate]:
    scored_rows: list[tuple[LocatorCandidate, float, bool, tuple[str, ...]]] = []
    for candidate in candidates:
        score, reasons, risky = score_locator_for_write(candidate)
        scored_rows.append((candidate, score, risky, reasons))

    scored_rows.sort(key=lambda row: row[1], reverse=True)

    ordered: list[LocatorCandidate] = []
    for index, (candidate, score, risky, reasons) in enumerate(scored_rows):
        label = ""
        if index == 0:
            label = "Recommended"
        elif risky:
            label = "Risky"

        candidate.metadata["write_recommendation_score"] = score
        candidate.metadata["write_recommendation_label"] = label
        candidate.metadata["write_recommendation_risky"] = risky
        candidate.metadata["write_recommendation_reasons"] = list(reasons)
        ordered.append(candidate)

    return ordered


def score_locator_for_write(candidate: LocatorCandidate) -> tuple[float, tuple[str, ...], bool]:
    locator = candidate.locator or ""
    lowered = locator.lower()

    base_score = float(candidate.score) if candidate.score else 50.0
    score = base_score
    reasons: list[str] = []

    strategy = str(candidate.metadata.get("strategy_type") or candidate.strategy_type or "").strip().lower()
    confidence = str(candidate.metadata.get("confidence") or candidate.confidence or "LOW").strip().upper()

    if strategy == "id":
        score += 10
        reasons.append("strategy:id")
    elif strategy == "data_attr":
        score += 8
        reasons.append("strategy:data-attr")
    elif strategy == "name":
        score += 7
        reasons.append("strategy:name")
    elif strategy == "accessibility":
        score += 5
        reasons.append("strategy:a11y")
    elif strategy == "text_xpath":
        score += 3
        reasons.append("strategy:text-xpath")

    if confidence == "HIGH":
        score += 10
        reasons.append("confidence:high")
    elif confidence == "MEDIUM":
        score += 4
        reasons.append("confidence:medium")

    if candidate.uniqueness_count == 1:
        score += 10
        reasons.append("uniqueness:1")
    elif candidate.uniqueness_count > 1:
        score -= min(35, (candidate.uniqueness_count - 1) * 8)
        reasons.append("penalty:not-unique")
    else:
        score -= 20
        reasons.append("penalty:no-match")

    if is_forbidden_locator(locator, candidate.locator_type):
        score -= 45
        reasons.append("penalty:forbidden-pattern")

    if candidate.locator_type == "XPath":
        if is_absolute_xpath(locator):
            score -= 55
            reasons.append("penalty:absolute-xpath")
        if is_index_based_xpath(locator):
            score -= 38
            reasons.append("penalty:index")
        if "normalize-space" in lowered:
            score += 5
            reasons.append("xpath:normalize-space")

    if candidate.locator_type == "Selenium" and "by.id(" in lowered:
        score += 6
        reasons.append("selenium:by-id")

    if candidate.metadata.get("prefix_salvaged"):
        score -= 10
        reasons.append("penalty:prefix-salvage")
    if candidate.metadata.get("dynamic_detected"):
        score -= 8
        reasons.append("penalty:dynamic-attribute")

    if _looks_dynamic_id(locator):
        score -= 22
        reasons.append("penalty:dynamic-id")

    bounded = max(0.0, min(100.0, score))
    risky = (
        bounded < 35
        or candidate.uniqueness_count != 1
        or is_forbidden_locator(locator, candidate.locator_type)
        or (candidate.locator_type == "XPath" and (is_absolute_xpath(locator) or is_index_based_xpath(locator)))
    )
    return bounded, tuple(reasons), risky


def _looks_dynamic_id(locator: str) -> bool:
    parts = re.findall(r"id\s*[\^$*]?=\s*\"([^\"]+)\"|id\s*[\^$*]?=\s*'([^']+)'|#([A-Za-z0-9_:-]+)", locator)
    tokens = [next((piece for piece in group if piece), "") for group in parts]
    for token in tokens:
        cleaned = token.strip()
        if not cleaned:
            continue
        if re.search(r"[A-Za-z]{1,4}\d{4,}$", cleaned):
            return True
        if re.search(r"[_:-]\d{4,}$", cleaned):
            return True
        if re.fullmatch(r"[0-9]+", cleaned):
            return True
        if re.fullmatch(r"[a-f0-9]{16,}", cleaned, flags=re.IGNORECASE):
            return True
    return False
