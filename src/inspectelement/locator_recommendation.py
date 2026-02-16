from __future__ import annotations

import re

from .models import LocatorCandidate


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

    score = 50.0
    reasons: list[str] = []

    if any(attr in lowered for attr in ("data-testid", "data-test", "data-qa")):
        score += 55
        reasons.append("stable:test-attribute")

    if _contains_id_pattern(lowered):
        score += 35
        reasons.append("stable:id")
        if _looks_dynamic_id(locator):
            score -= 35
            reasons.append("penalty:dynamic-id")

    if any(token in lowered for token in ("[name=", "@name", "aria-label", "placeholder")):
        score += 28
        reasons.append("stable:name-aria-placeholder")

    if candidate.locator_type == "CSS" and "[" in locator and "]" in locator:
        score += 12
        reasons.append("css:attribute")

    if candidate.locator_type == "XPath":
        score -= 8
        reasons.append("xpath:base-penalty")
        if "normalize-space" in lowered or "text()" in lowered:
            score += 8
            reasons.append("xpath:text")

    if _is_absolute_xpath(lowered):
        score -= 60
        reasons.append("penalty:absolute-xpath")

    if _is_index_based(lowered):
        score -= 45
        reasons.append("penalty:index")

    if "nth-of-type" in lowered:
        score -= 35
        reasons.append("penalty:nth")

    if len(locator) > 120:
        score -= 20
        reasons.append("penalty:length>120")
    if len(locator) > 200:
        score -= 20
        reasons.append("penalty:length>200")

    if candidate.locator_type == "Playwright":
        if "get_by_test_id" in lowered:
            score += 50
            reasons.append("playwright:testid")
        elif "get_by_label" in lowered or "get_by_placeholder" in lowered:
            score += 28
            reasons.append("playwright:label-placeholder")

    if candidate.uniqueness_count == 1:
        score += 12
        reasons.append("uniqueness:1")
    elif candidate.uniqueness_count > 1:
        score -= min(25, (candidate.uniqueness_count - 1) * 6)
        reasons.append("penalty:not-unique")

    bounded = max(0.0, min(100.0, score))
    risky = bounded < 35 or _is_absolute_xpath(lowered) or _is_index_based(lowered) or "nth-of-type" in lowered
    return bounded, tuple(reasons), risky


def _contains_id_pattern(lowered_locator: str) -> bool:
    return "#" in lowered_locator or "[id=" in lowered_locator or "@id" in lowered_locator or "by.id" in lowered_locator


def _is_absolute_xpath(lowered_locator: str) -> bool:
    return lowered_locator.startswith("/html") or lowered_locator.startswith("/body")


def _is_index_based(lowered_locator: str) -> bool:
    return bool(re.search(r"\[\d+\]", lowered_locator))


def _looks_dynamic_id(locator: str) -> bool:
    parts = re.findall(r"id\s*[\^$*]?=\s*\"([^\"]+)\"|id\s*[\^$*]?=\s*'([^']+)'|#([A-Za-z0-9_:-]+)", locator)
    tokens = [next((piece for piece in group if piece), "") for group in parts]
    for token in tokens:
        cleaned = token.strip()
        if not cleaned:
            continue
        if re.search(r"[A-Za-z]{1,4}\d{4,}$", cleaned):
            return True
        if re.fullmatch(r"[0-9]+", cleaned):
            return True
        if re.fullmatch(r"[a-f0-9]{16,}", cleaned, flags=re.IGNORECASE):
            return True
    return False
