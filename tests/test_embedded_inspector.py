from inspectelement.embedded_inspector import build_fallback_locator_payload


def _locators(payload: list[dict[str, object]]) -> set[str]:
    return {str(item.get("locator") or "") for item in payload}


def test_fallback_locator_payload_contains_rich_candidates() -> None:
    summary = {
        "tag": "span",
        "id": "queryButton",
        "classes": ["ant-btn", "btn-primary"],
        "text": "Sorgula",
        "attributes": {
            "id": "queryButton",
            "data-testid": "query-cta",
            "name": "queryButton",
            "aria-label": "Sorgula",
            "placeholder": "",
        },
        "ancestry": [
            {"tag": "span", "class": "ant-btn"},
            {"tag": "button", "class": "ant-btn ant-btn-primary"},
            {"tag": "div", "class": "ant-modal ant-modal-open"},
        ],
    }

    payload = build_fallback_locator_payload(summary)
    locators = _locators(payload)

    assert any(locator.startswith("By.id(") for locator in locators)
    assert 'span[data-testid="query-cta"]' in locators
    assert "//span[text()='Sorgula']" in locators
    assert "//span[normalize-space(.)='Sorgula']" in locators
    assert "(//*[self::button or self::span or self::a][normalize-space(text())='Sorgula'])[2]" in locators
    assert (
        "(//div[contains(@class,'ant-modal')][not(@aria-hidden='true')]//button"
        "[.//span[normalize-space(.)='Sorgula'] and not(contains(@class,'is-hidden'))])[1]"
    ) in locators
    assert len(payload) >= 8


def test_fallback_locator_payload_contains_following_sibling_pattern() -> None:
    summary = {
        "tag": "div",
        "text": "Varsayılan GSM",
        "classes": ["list-item-title"],
        "attributes": {},
        "ancestry": [{"tag": "div", "class": "list-item"}],
    }

    payload = build_fallback_locator_payload(summary)
    locators = _locators(payload)
    assert (
        "//div[normalize-space(.)='Varsayılan GSM']/following-sibling::div[contains(@class,'list-item-value')]"
        in locators
    )
