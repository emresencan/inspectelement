from inspectelement.browser_manager import BrowserManager
from inspectelement.models import LocatorCandidate


def _manager() -> BrowserManager:
    return BrowserManager(
        on_capture=lambda _summary, _candidates: None,
        on_status=lambda _message: None,
        on_page_info=lambda _title, _url: None,
    )


def test_default_validation_marks_non_unique_candidate_as_invalid() -> None:
    manager = _manager()
    candidates = [
        LocatorCandidate(locator_type="CSS", locator='button[data-testid="save"]', rule="stable_attr:data-testid", uniqueness_count=1),
        LocatorCandidate(locator_type="XPath", locator="//div[contains(@class,'wrapper')]", rule="xpath_fallback", uniqueness_count=5),
    ]
    validated = manager._validate_candidates_for_display(candidates)
    assert validated[0].metadata.get("display_valid") is True
    assert validated[1].metadata.get("display_valid") is False
    assert validated[1].metadata.get("display_validation_reason") == "not-unique"


def test_union_xpath_with_index_kept_visible_when_multi_match() -> None:
    manager = _manager()
    candidates = [
        LocatorCandidate(
            locator_type="XPath",
            locator="(//*[self::button or self::a or self::span][normalize-space(.)='Onayla'])[2]",
            rule="xpath_text_clickable_union",
            uniqueness_count=3,
            metadata={"uses_index": True},
        ),
    ]
    validated = manager._validate_candidates_for_display(candidates)
    assert validated[0].metadata.get("display_valid") is True

