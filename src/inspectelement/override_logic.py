from __future__ import annotations

from .models import LocatorCandidate, OverrideEntry
from .scoring import score_candidate


def build_override_candidate(
    override: OverrideEntry,
    uniqueness_count: int,
    learning_weights: dict[str, float] | None = None,
) -> LocatorCandidate:
    candidate = LocatorCandidate(
        locator_type=override.locator_type,
        locator=override.locator,
        rule="custom_override",
        uniqueness_count=uniqueness_count,
        metadata={"is_override": True},
    )
    return score_candidate(candidate, learning_weights or {})


def inject_override_candidate(
    candidates: list[LocatorCandidate],
    override_candidate: LocatorCandidate,
    limit: int,
) -> list[LocatorCandidate]:
    if limit <= 0:
        return []

    merged: list[LocatorCandidate] = [override_candidate]
    seen: set[tuple[str, str]] = {(override_candidate.locator_type, override_candidate.locator)}

    for candidate in candidates:
        key = (candidate.locator_type, candidate.locator)
        if key in seen:
            continue
        merged.append(candidate)
        seen.add(key)

    return merged[:limit]
