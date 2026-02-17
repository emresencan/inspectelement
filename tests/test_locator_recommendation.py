from inspectelement.locator_recommendation import recommend_locator_candidates, score_locator_for_write
from inspectelement.models import LocatorCandidate


def _candidate(locator_type: str, locator: str, rule: str = "rule", uniqueness_count: int = 1) -> LocatorCandidate:
    return LocatorCandidate(
        locator_type=locator_type,
        locator=locator,
        rule=rule,
        uniqueness_count=uniqueness_count,
    )


def test_recommendation_prefers_stable_test_attributes() -> None:
    candidates = [
        _candidate("XPath", "/html/body/div[4]/button[1]", uniqueness_count=1),
        _candidate("CSS", 'button[data-testid="save"]', uniqueness_count=1),
    ]

    ordered = recommend_locator_candidates(candidates)

    assert ordered[0].locator == 'button[data-testid="save"]'
    assert ordered[0].metadata["write_recommendation_label"] == "Recommended"


def test_recommendation_marks_risky_absolute_xpath() -> None:
    candidate = _candidate("XPath", "/html/body/main/div[5]/ul/li[2]/button[1]", uniqueness_count=1)

    score, _reasons, risky = score_locator_for_write(candidate)

    assert risky
    assert score < 35


def test_recommendation_penalizes_dynamic_id_vs_name_selector() -> None:
    candidates = [
        _candidate("CSS", '#user_198273645', uniqueness_count=1),
        _candidate("CSS", 'input[name="email"]', uniqueness_count=1),
    ]

    ordered = recommend_locator_candidates(candidates)

    assert ordered[0].locator == 'input[name="email"]'
    assert ordered[1].metadata["write_recommendation_label"] in {"Risky", ""}


def test_recommendation_prefers_selenium_by_id_when_id_exists() -> None:
    candidates = [
        _candidate("CSS", "#loginButton", rule="stable_attr:id", uniqueness_count=1),
        _candidate("XPath", "//*[@id='loginButton']", rule="stable_attr:id", uniqueness_count=1),
        _candidate("Selenium", 'By.id("loginButton")', rule="stable_attr:id", uniqueness_count=1),
    ]

    ordered = recommend_locator_candidates(candidates)
    assert ordered[0].locator == 'By.id("loginButton")'
