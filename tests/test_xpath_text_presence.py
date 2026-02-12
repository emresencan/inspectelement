from inspectelement.locator_generator import _ensure_xpath_text_in_results
from inspectelement.models import ElementSummary, LocatorCandidate


def _summary_with_text(text: str) -> ElementSummary:
    return ElementSummary(
        tag="button",
        id=None,
        classes=[],
        name=None,
        role="button",
        text=text,
        placeholder=None,
        aria_label=None,
        label_text=None,
        attributes={},
    )


def test_xpath_text_forced_into_top_list_when_text_exists() -> None:
    summary = _summary_with_text("Bilet yonetimi")
    scored = [
        LocatorCandidate("CSS", "#a", "stable_attr:id", 1, score=100),
        LocatorCandidate("CSS", "[data-testid='x']", "stable_attr:data-testid", 1, score=95),
        LocatorCandidate("Playwright", "page.get_by_role('tab', name='Bilet yonetimi')", "text_role", 1, score=90),
        LocatorCandidate("CSS", "button.primary", "meaningful_class", 1, score=85),
        LocatorCandidate("CSS", "button[data-value='manageBooking']", "stable_attr:name", 1, score=80),
        LocatorCandidate("XPath", "//button[normalize-space()='Bilet yonetimi']", "xpath_text", 1, score=40),
    ]

    result = _ensure_xpath_text_in_results(scored, summary, limit=5)

    assert len(result) == 5
    assert any(item.rule == "xpath_text" and item.locator_type == "XPath" for item in result)


def test_xpath_text_not_forced_when_no_text() -> None:
    summary = _summary_with_text("")
    scored = [
        LocatorCandidate("CSS", "#a", "stable_attr:id", 1, score=100),
        LocatorCandidate("CSS", "#b", "stable_attr:id", 1, score=99),
    ]

    result = _ensure_xpath_text_in_results(scored, summary, limit=2)

    assert result == scored
