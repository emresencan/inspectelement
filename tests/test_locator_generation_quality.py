from inspectelement.locator_generator import (
    _build_clickable_union_xpath_draft,
    _select_candidates_by_priority,
    build_candidate_drafts_from_summary,
)
from inspectelement.models import ElementSummary, LocatorCandidate


def _summary(
    *,
    tag: str,
    text: str | None,
    attributes: dict[str, str],
    classes: list[str] | None = None,
    ancestry: list[dict[str, str]] | None = None,
    sibling_label_text: str | None = None,
    outer_html: str | None = None,
) -> ElementSummary:
    return ElementSummary(
        tag=tag,
        id=attributes.get("id"),
        classes=classes or [],
        name=attributes.get("name"),
        role=attributes.get("role"),
        text=text,
        placeholder=attributes.get("placeholder"),
        aria_label=attributes.get("aria-label"),
        label_text=None,
        outer_html=outer_html,
        sibling_label_text=sibling_label_text,
        attributes=attributes,
        ancestry=ancestry or [],
    )


def test_rich_locator_generation_includes_id_attr_and_text_variants() -> None:
    summary = _summary(
        tag="span",
        text="Sorgula",
        attributes={
            "id": "searchBtn",
            "data-testid": "search-button",
            "role": "button",
            "name": "searchAction",
        },
        classes=["ant-btn", "ant-btn-primary"],
    )

    drafts = build_candidate_drafts_from_summary(summary)
    locators = {draft.locator for draft in drafts}

    assert 'By.ID("searchBtn")' in locators
    assert 'By.NAME("searchAction")' in locators
    assert '[data-testid="search-button"]' in locators
    assert "//span[text()='Sorgula']" in locators
    assert "//span[normalize-space(.)='Sorgula']" in locators
    assert "(//*[self::button or self::span or self::a][normalize-space(text())='Sorgula'])[1]" in locators
    assert len(drafts) >= 8


def test_modal_context_generates_ant_modal_safe_patterns() -> None:
    summary = _summary(
        tag="span",
        text="Tamam",
        attributes={"class": "ant-btn"},
        classes=["ant-btn"],
        ancestry=[
            {"tag": "span", "class": "ant-btn"},
            {"tag": "button", "class": "ant-btn ant-btn-primary"},
            {"tag": "div", "class": "ant-modal-wrap"},
        ],
        outer_html="<div class='ant-modal'><button><span>Tamam</span></button></div>",
    )

    drafts = build_candidate_drafts_from_summary(summary)
    locators = {draft.locator for draft in drafts}

    assert (
        "(//div[contains(@class,'ant-modal')][not(@aria-hidden='true')]//span[normalize-space(.)='Tamam'])[1]"
        in locators
    )
    assert (
        "(//div[contains(@class,'ant-modal')][not(@aria-hidden='true')]"
        "//button[.//span[normalize-space(.)='Tamam']])[1]"
        in locators
    )


def test_following_sibling_pattern_generated_for_label_value_blocks() -> None:
    summary = _summary(
        tag="div",
        text="05320000000",
        attributes={},
        classes=["list-item-value"],
        sibling_label_text="Varsayılan GSM",
    )

    drafts = build_candidate_drafts_from_summary(summary)
    locators = {draft.locator for draft in drafts}
    assert (
        "//div[text()='Varsayılan GSM']/following-sibling::div[contains(@class,'list-item-value')]"
        in locators
    )


def test_data_cy_candidate_is_generated() -> None:
    summary = _summary(
        tag="button",
        text="Devam Et",
        attributes={"data-cy": "continue-button", "role": "button"},
        classes=["cta-button"],
    )
    drafts = build_candidate_drafts_from_summary(summary)
    locators = {draft.locator for draft in drafts}
    assert 'button[data-cy="continue-button"]' in locators
    assert '[data-cy="continue-button"]' in locators


def test_label_contains_pattern_is_generated() -> None:
    summary = _summary(
        tag="label",
        text="Tak-Çalıştır – TIM Teslim",
        attributes={"class": "ant-radio-wrapper"},
        classes=["ant-radio-wrapper"],
    )
    drafts = build_candidate_drafts_from_summary(summary)
    locators = {draft.locator for draft in drafts}
    assert ".//label[contains(normalize-space(.),'Tak-Çalıştır – TIM Teslim')]" in locators


class _FakeLocator:
    def __init__(self, count: int) -> None:
        self._count = count

    def count(self) -> int:
        return self._count


class _FakePageForUnion:
    def __init__(self, count: int) -> None:
        self._count = count

    def locator(self, _selector: str) -> _FakeLocator:
        return _FakeLocator(self._count)


class _FakeElementForUnion:
    def __init__(self, index: int) -> None:
        self._index = index

    def evaluate(self, _script: str, _text: str) -> int:
        return self._index


def test_clickable_union_xpath_uses_index_when_not_unique() -> None:
    summary = _summary(tag="span", text="Onayla", attributes={})
    draft = _build_clickable_union_xpath_draft(
        page=_FakePageForUnion(count=3),  # type: ignore[arg-type]
        element=_FakeElementForUnion(index=2),  # type: ignore[arg-type]
        summary=summary,
    )
    assert draft is not None
    assert draft.locator == "(//*[self::button or self::a or self::span][normalize-space(.)='Onayla'])[2]"


def test_priority_selector_prefers_mixed_id_name_css_before_xpath_spam() -> None:
    candidates = [
        LocatorCandidate(locator_type="XPath", locator="//div[contains(@class,'wrapper')]", rule="xpath_fallback", uniqueness_count=1, score=20),
        LocatorCandidate(locator_type="Selenium", locator='By.ID("ctaButton")', rule="stable_attr:id", uniqueness_count=1, score=95),
        LocatorCandidate(locator_type="Selenium", locator='By.NAME("email")', rule="stable_attr:name", uniqueness_count=1, score=90),
        LocatorCandidate(locator_type="CSS", locator='button[data-testid="save"]', rule="stable_attr:data-testid", uniqueness_count=1, score=92),
        LocatorCandidate(locator_type="XPath", locator="//span[normalize-space(.)='Kaydet']", rule="xpath_text", uniqueness_count=1, score=80),
    ]
    ordered = _select_candidates_by_priority(candidates, limit=4)
    locators = [item.locator for item in ordered]
    assert locators[0] == 'By.ID("ctaButton")'
    assert 'By.NAME("email")' in locators[:3]
    assert 'button[data-testid="save"]' in locators[:3]
