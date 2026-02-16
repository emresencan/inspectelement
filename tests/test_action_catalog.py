from inspectelement.action_catalog import (
    ACTION_PRESETS,
    add_action_by_trigger,
    build_signature_previews,
    filter_action_specs,
    has_combo_actions,
    has_table_actions,
    normalize_selected_actions,
    required_parameter_keys,
    return_kind_badge,
    should_add_action_from_trigger,
)


def test_build_signature_previews_returns_expected_signatures_and_types() -> None:
    previews = build_signature_previews(
        page_class_name="HomePage",
        locator_name="YEMEK_LNK",
        selected_actions=["javaScriptClicker", "getText", "isElementDisplayed"],
    )

    signatures = [item.signature for item in previews]
    assert signatures == [
        "public HomePage jsClickYemekLnk()",
        "public String getYemekLnkText()",
        "public boolean isYemekLnkDisplayed(int timeoutSeconds)",
    ]
    assert [item.return_kind for item in previews] == ["fluent", "string", "boolean"]


def test_filter_action_specs_by_query_and_category() -> None:
    filtered = filter_action_specs(search_text="attribute", category="Read")
    assert [item.key for item in filtered] == ["getAttribute"]
    filtered_table = filter_action_specs(search_text="row", category="Table", include_advanced=False)
    assert "tableAssertRowExists" in [item.key for item in filtered_table]
    filtered_advanced = filter_action_specs(search_text="radio", category="Table", include_advanced=True)
    assert "tableClickRadioInRow" in [item.key for item in filtered_advanced]


def test_normalize_selected_actions_deduplicates_and_skips_unknown() -> None:
    assert normalize_selected_actions(["clickElement", "unknown", "clickElement", "getText"]) == [
        "clickElement",
        "getText",
    ]


def test_action_category_helpers_and_required_parameters() -> None:
    selected = ["tableAssertRowExists", "tableHasAnyRow", "selectBySelectIdAuto"]
    assert has_table_actions(selected)
    assert has_combo_actions(selected)
    params = required_parameter_keys(selected)
    assert "timeoutSec" in params
    assert "matchType" in params
    assert "columnHeader" in params
    assert "expectedText" in params
    assert "selectId" in params


def test_build_signature_previews_with_table_locator_name() -> None:
    previews = build_signature_previews(
        page_class_name="OrderPage",
        locator_name="ROW_TXT",
        selected_actions=["tableHasAnyRow"],
        table_locator_name="ORDERS_TABLE",
        action_parameters={"timeoutSec": "10"},
    )
    assert previews[0].signature == "public boolean hasOrdersTableAnyRow(int timeoutSec)"


def test_return_kind_badges() -> None:
    assert return_kind_badge("fluent") == "Fluent"
    assert return_kind_badge("string") == "Returns String"
    assert return_kind_badge("boolean") == "Returns boolean"


def test_table_common_preset_includes_extended_actions() -> None:
    table_common = ACTION_PRESETS["Table Common"]
    assert "tableAssertRowExists" in table_common
    assert "tableAssertHasAnyRow" in table_common
    assert "tableAssertColumnTextEquals" in table_common
    assert "tableGetColumnText" in table_common
    assert "tableClickInFirstRow" in table_common
    assert "tableClickRadioInRow" in table_common
    assert "tableClickLink" in table_common


def test_hover_or_mouse_move_does_not_add_action() -> None:
    selected = ["clickElement"]
    hovered = add_action_by_trigger(selected, "getText", trigger="hover")
    moved = add_action_by_trigger(selected, "getText", trigger="mouse_move")
    assert hovered == ["clickElement"]
    assert moved == ["clickElement"]


def test_explicit_action_add_trigger_changes_selected_actions() -> None:
    selected = ["clickElement"]
    added = add_action_by_trigger(selected, "getText", trigger="button_click")
    assert added == ["clickElement", "getText"]
    assert should_add_action_from_trigger("button_click") is True
    assert should_add_action_from_trigger("hover") is False
