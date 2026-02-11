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


def test_unique_xpath_text_ranks_above_unique_nth_fallback() -> None:
    candidates = [
        LocatorCandidate(
            locator_type="XPath",
            locator="//button[normalize-space()='Save']",
            rule="xpath_text",
            uniqueness_count=1,
        ),
        LocatorCandidate(
            locator_type="CSS",
            locator="main > div:nth-of-type(3) > section:nth-of-type(2) > button:nth-of-type(1)",
            rule="nth_fallback",
            uniqueness_count=1,
            metadata={"uses_nth": True},
        ),
    ]

    scored = score_candidates(candidates)
    assert scored[0].rule == "xpath_text"
    assert scored[1].rule == "nth_fallback"


def test_unique_nth_fallback_is_not_top3_when_stable_or_text_candidates_exist() -> None:
    candidates = [
        LocatorCandidate(
            locator_type="CSS",
            locator='button[data-testid="save"]',
            rule="stable_attr:data-testid",
            uniqueness_count=1,
        ),
        LocatorCandidate(
            locator_type="Playwright",
            locator='page.get_by_label("Email", exact=True)',
            rule="label_assoc",
            uniqueness_count=1,
        ),
        LocatorCandidate(
            locator_type="XPath",
            locator="//button[normalize-space()='Save']",
            rule="xpath_text",
            uniqueness_count=1,
        ),
        LocatorCandidate(
            locator_type="CSS",
            locator="html > body > main > div:nth-of-type(5) > ul > li:nth-of-type(2) > button:nth-of-type(1)",
            rule="nth_fallback",
            uniqueness_count=1,
            metadata={"uses_nth": True},
        ),
    ]

    scored = score_candidates(candidates)
    top3_rules = [item.rule for item in scored[:3]]
    assert "nth_fallback" not in top3_rules
