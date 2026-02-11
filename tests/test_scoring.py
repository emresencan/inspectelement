from inspectelement.models import LocatorCandidate
from inspectelement.scoring import score_candidates


def test_scoring_prefers_unique_stable_attr() -> None:
    candidates = [
        LocatorCandidate(
            locator_type="CSS",
            locator='button[data-testid="save"]',
            rule="stable_attr:data-testid",
            uniqueness_count=1,
        ),
        LocatorCandidate(
            locator_type="CSS",
            locator="button.btn-primary",
            rule="meaningful_class",
            uniqueness_count=3,
        ),
        LocatorCandidate(
            locator_type="CSS",
            locator="html > body > div:nth-of-type(4) > button:nth-of-type(2)",
            rule="nth_fallback",
            uniqueness_count=1,
            metadata={"uses_nth": True},
        ),
    ]

    scored = score_candidates(candidates)
    assert scored[0].rule == "stable_attr:data-testid"
    assert scored[-1].rule == "nth_fallback"
