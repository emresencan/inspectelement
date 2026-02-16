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

    if any(attr in lowered for attr in ("data-testid", "data-test", "data-qa", "data-cy")):
        score += 55
        reasons.append("stable:test-attribute")

    if candidate.locator_type == "Selenium" and locator.startswith("By.ID("):
        score += 48
        reasons.append("stable:selenium-id")
    if candidate.locator_type == "Selenium" and locator.startswith("By.NAME("):
        score += 40
        reasons.append("stable:selenium-name")

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
        score -= 4
        reasons.append("xpath:base-penalty")
        if "normalize-space" in lowered or "text()" in lowered:
            score += 14
            reasons.append("xpath:text")
            if candidate.uniqueness_count == 1 and 2 <= len(_extract_text_literal(locator)) <= 64:
                score += 12
                reasons.append("xpath:text-unique")
        if candidate.metadata.get("modal_safe"):
            score += 8
            reasons.append("xpath:modal-safe")
        if candidate.rule in {"xpath_fallback", "nth_fallback"}:
            score -= 36
            reasons.append("penalty:fallback")
        if "//*" in lowered and not any(token in lowered for token in ("@id", "@name", "@data-", "text()", "normalize-space")):
            score -= 28
            reasons.append("penalty:generic-any-node")
        if any(token in lowered for token in ("container", "wrapper", "header", "content")) and candidate.rule not in {
            "xpath_modal_text",
            "xpath_following_sibling",
        }:
            score -= 24
            reasons.append("penalty:wrapper-context")

    if _is_absolute_xpath(lowered):
        score -= 60
        reasons.append("penalty:absolute-xpath")

    if _is_index_based(lowered):
        if candidate.metadata.get("modal_safe"):
            score -= 10
            reasons.append("penalty:index-soft-modal")
        else:
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
    if candidate.rule == "xpath_fallback":
        score -= 42
        reasons.append("penalty:xpath-fallback")
    if any(token in lowered for token in ("modals", "modal", "container", "wrapper", "header", "content")):
        if candidate.rule in {"xpath_ancestor_context", "xpath_fallback"}:
            score -= 34
            reasons.append("penalty:wrapper-xpath")

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
    index_risky = _is_index_based(lowered) and not candidate.metadata.get("modal_safe")
    risky = bounded < 35 or _is_absolute_xpath(lowered) or index_risky or "nth-of-type" in lowered
    return bounded, tuple(reasons), risky


def _extract_text_literal(locator: str) -> str:
    match = re.search(r"normalize-space\\([^\\)]*\\)\\s*=\\s*['\\\"]([^'\\\"]+)['\\\"]", locator)
    if match:
        return match.group(1).strip()
    match = re.search(r"text\\(\\)\\s*=\\s*['\\\"]([^'\\\"]+)['\\\"]", locator)
    if match:
        return match.group(1).strip()
    return ""


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
