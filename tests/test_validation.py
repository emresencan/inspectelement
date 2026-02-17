from inspectelement.validation import validate_generation_request


def test_validate_blocks_when_required_context_missing() -> None:
    result = validate_generation_request(
        has_page=False,
        has_locator=True,
        element_name="KAYDET_BTN",
        actions=[],
        action_parameters={},
        has_table_root=False,
    )
    assert not result.ok
    assert result.message == "Select page before Add."


def test_validate_blocks_table_actions_without_table_root() -> None:
    result = validate_generation_request(
        has_page=True,
        has_locator=True,
        element_name="SATIR_TXT",
        actions=["tableHasAnyRow"],
        action_parameters={"timeoutSec": "10"},
        has_table_root=False,
    )
    assert not result.ok
    assert "Table root could not be detected" in result.message


def test_validate_blocks_missing_select_id() -> None:
    result = validate_generation_request(
        has_page=True,
        has_locator=True,
        element_name="CITY_SELECT",
        actions=["selectBySelectIdAuto"],
        action_parameters={"waitBeforeSelect": "false", "selectId": ""},
        has_table_root=True,
    )
    assert not result.ok
    assert result.message == "selectId is required for selected action(s)."


def test_validate_blocks_invalid_timeout_and_inner_locator() -> None:
    timeout_result = validate_generation_request(
        has_page=True,
        has_locator=True,
        element_name="ROW_TXT",
        actions=["tableHasAnyRow"],
        action_parameters={"timeoutSec": "0"},
        has_table_root=True,
    )
    assert not timeout_result.ok
    assert timeout_result.message == "timeoutSec must be a positive integer."

    missing_locator_result = validate_generation_request(
        has_page=True,
        has_locator=True,
        element_name="ROW_TXT",
        actions=["tableClickButtonInRow"],
        action_parameters={
            "timeoutSec": "10",
            "matchColumnHeader": "Durum",
            "matchText": "Aktif",
            "innerLocator": "",
        },
        has_table_root=True,
    )
    assert not missing_locator_result.ok
    assert missing_locator_result.message == "innerLocator is required for selected action(s)."

    locator_result = validate_generation_request(
        has_page=True,
        has_locator=True,
        element_name="ROW_TXT",
        actions=["tableClickButtonInRow"],
        action_parameters={
            "timeoutSec": "10",
            "matchColumnHeader": "Durum",
            "matchText": "Aktif",
            "innerLocator": "css:button",
        },
        has_table_root=True,
    )
    assert not locator_result.ok
    assert locator_result.message.startswith("innerLocator must be valid By expression")


def test_validate_success_for_valid_payload() -> None:
    result = validate_generation_request(
        has_page=True,
        has_locator=True,
        element_name="ROW_TXT",
        actions=["tableClickInRow", "selectBySelectIdAuto"],
        action_parameters={
            "timeoutSec": "10",
            "matchType": "equals",
            "matchColumnHeader": "Durum",
            "matchText": "Aktif",
            "innerLocator": 'By.cssSelector("button[title=\'Sil\']")',
            "selectId": "citySelect",
            "waitBeforeSelect": "false",
        },
        has_table_root=True,
    )
    assert result.ok
    assert result.message == "Validation successful."
