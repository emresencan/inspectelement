from inspectelement.table_root_detection import detect_table_root_from_ancestry


def test_detect_table_root_prefers_id() -> None:
    ancestry = [
        {"tag": "span", "id": "", "role": "", "class": "cell-value", "nth": "1"},
        {"tag": "div", "id": "ordersTable", "role": "grid", "class": "ag-theme-alpine", "nth": "2"},
    ]

    candidate = detect_table_root_from_ancestry(ancestry)
    assert candidate is not None
    assert candidate.selector_type == "id"
    assert candidate.selector_value == "ordersTable"
    assert candidate.locator_name_hint == "ORDERSTABLE_TABLE"


def test_detect_table_root_prefers_stable_data_attr_when_no_id() -> None:
    ancestry = [
        {"tag": "button", "id": "", "role": "", "class": "btn", "nth": "1"},
        {"tag": "div", "id": "", "role": "table", "class": "data-grid", "nth": "2", "data-testid": "orders-grid"},
    ]

    candidate = detect_table_root_from_ancestry(ancestry)
    assert candidate is not None
    assert candidate.selector_type == "css"
    assert candidate.selector_value == "div[data-testid='orders-grid']"
    assert candidate.reason == "data-testid"


def test_detect_table_root_fallback_xpath_for_class_only() -> None:
    ancestry = [
        {"tag": "a", "id": "", "role": "", "class": "row-link", "nth": "1"},
        {"tag": "div", "id": "", "role": "", "class": "results-grid", "nth": "2"},
        {"tag": "section", "id": "", "role": "", "class": "", "nth": "1"},
    ]

    candidate = detect_table_root_from_ancestry(ancestry)
    assert candidate is not None
    assert candidate.selector_type in {"css", "xpath"}
    assert candidate.locator_name_hint.endswith("_TABLE")


def test_detect_table_root_returns_none_when_no_table_like_ancestor() -> None:
    ancestry = [
        {"tag": "span", "id": "", "role": "", "class": "title", "nth": "1"},
        {"tag": "div", "id": "", "role": "", "class": "content", "nth": "2"},
    ]

    candidate = detect_table_root_from_ancestry(ancestry)
    assert candidate is None
