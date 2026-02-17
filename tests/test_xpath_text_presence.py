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
        LocatorCandidate(
            "XPath",
            "//*[@aria-label='Onayla']",
            "xpath_text",
            1,
            score=50,
            metadata={"strategy_type": "text_xpath"},
        ),
    ]

    result = _ensure_xpath_text_in_results(scored, summary, limit=2)

    assert len(result) == 2
    assert any(item.rule == "xpath_text" and item.locator_type == "XPath" for item in result)


def test_xpath_text_keeps_exactly_one_when_multiple_present() -> None:
    summary = _summary_with_text("Kaydet")
    scored = [
        LocatorCandidate("CSS", "[data-testid='save']", "stable_attr:data-testid", 1, score=100),
        LocatorCandidate("XPath", "//button[normalize-space()='Kaydet']", "xpath_text", 1, score=98),
        LocatorCandidate("XPath", "//*[self::button or self::a][normalize-space()='Kaydet']", "xpath_text", 1, score=97),
        LocatorCandidate("Selenium", 'By.id(\"saveBtn\")', "stable_attr:id", 1, score=95),
    ]

    result = _ensure_xpath_text_in_results(scored, summary, limit=4)

    assert len(result) == 3
    assert sum(1 for item in result if item.rule == "xpath_text" and item.locator_type == "XPath") == 1
