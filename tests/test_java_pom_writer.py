from __future__ import annotations

import re

from inspectelement.java_pom_writer import SAFE_PARSE_ERROR, prepare_java_patch


def _region_content(source: str, region_name: str) -> str:
    pattern = re.compile(
        rf"// region {re.escape(region_name)}\n(?P<content>.*?)// endregion {re.escape(region_name)}",
        re.DOTALL,
    )
    match = pattern.search(source)
    assert match is not None
    return match.group("content")


def test_marker_insertion_with_constructor_when_markers_missing() -> None:
    source = """public class FolderPage extends BaseLibrary {
    public FolderPage(WebDriver driver) {
        super(driver);
    }

    public FolderPage openTab() {
        return this;
    }
}
"""

    result = prepare_java_patch(
        source=source,
        locator_name="TAB_DOCUMENT_BILGI",
        selector_type="css",
        selector_value="button[data-tab='doc']",
        actions=(),
    )

    assert result.ok
    assert result.changed
    assert "// region AUTO_LOCATORS" in result.updated_source
    assert "// region AUTO_ACTIONS" in result.updated_source

    constructor_end_index = result.updated_source.index("    }\n")
    locators_index = result.updated_source.index("// region AUTO_LOCATORS")
    actions_index = result.updated_source.index("// region AUTO_ACTIONS")
    final_brace_index = result.updated_source.rfind("}")

    assert constructor_end_index < locators_index
    assert actions_index < final_brace_index


def test_marker_insertion_without_constructor() -> None:
    source = """public class FolderPage extends BaseLibrary {
    private int counter;
}
"""

    result = prepare_java_patch(
        source=source,
        locator_name="COUNTER_TXT",
        selector_type="xpath",
        selector_value="//span[@id='counter']",
        actions=(),
    )

    assert result.ok
    assert result.changed
    assert "// region AUTO_LOCATORS" in result.updated_source
    assert "// region AUTO_ACTIONS" in result.updated_source


def test_locator_insertion_when_markers_exist() -> None:
    source = """public class FolderPage extends BaseLibrary {
    // region AUTO_LOCATORS
    // endregion AUTO_LOCATORS

    // region AUTO_ACTIONS
    // endregion AUTO_ACTIONS
}
"""

    result = prepare_java_patch(
        source=source,
        locator_name="SQL_CALCULATION_TEXT_AREA",
        selector_type="css",
        selector_value="textarea[name='sqlCalc']",
        actions=(),
    )

    assert result.ok
    assert result.changed
    locators = _region_content(result.updated_source, "AUTO_LOCATORS")
    assert "private final By SQL_CALCULATION_TEXT_AREA = By.cssSelector(\"textarea[name='sqlCalc']\");" in locators


def test_locator_name_collision_adds_suffix() -> None:
    source = """public class FolderPage extends BaseLibrary {
    // region AUTO_LOCATORS
        private final By SQL_CALCULATION_TEXT_AREA = By.cssSelector("textarea[name='other']");
    // endregion AUTO_LOCATORS

    // region AUTO_ACTIONS
    // endregion AUTO_ACTIONS
}
"""

    result = prepare_java_patch(
        source=source,
        locator_name="SQL_CALCULATION_TEXT_AREA",
        selector_type="css",
        selector_value="textarea[name='sqlCalc']",
        actions=(),
    )

    assert result.ok
    assert result.changed
    assert result.final_locator_name == "SQL_CALCULATION_TEXT_AREA_2"
    assert "private final By SQL_CALCULATION_TEXT_AREA_2 = By.cssSelector(\"textarea[name='sqlCalc']\");" in result.updated_source


def test_duplicate_selector_reuses_existing_constant_and_generates_method() -> None:
    source = """public class FolderPage extends BaseLibrary {
    private final By EXISTING = By.xpath("//button[normalize-space()='Kaydet']");

    // region AUTO_LOCATORS
    // endregion AUTO_LOCATORS

    // region AUTO_ACTIONS
    // endregion AUTO_ACTIONS
}
"""

    result = prepare_java_patch(
        source=source,
        locator_name="KAYDET_BTN",
        selector_type="xpath",
        selector_value="//button[normalize-space()='Kaydet']",
        actions=("click",),
    )

    assert result.ok
    assert result.changed
    assert result.final_locator_name == "EXISTING"
    assert "Selector already exists as EXISTING; reusing." in result.message
    assert "private final By KAYDET_BTN" not in result.updated_source
    assert "public FolderPage clickExisting()" in result.updated_source


def test_action_method_insertion_click_and_sendkeys() -> None:
    source = """public class FolderPage extends BaseLibrary {
    // region AUTO_LOCATORS
    // endregion AUTO_LOCATORS

    // region AUTO_ACTIONS
    // endregion AUTO_ACTIONS
}
"""

    result = prepare_java_patch(
        source=source,
        locator_name="EV_YASAM_TXT",
        selector_type="css",
        selector_value="input[name='evYasam']",
        actions=("click", "sendKeys"),
    )

    assert result.ok
    assert result.changed
    assert "public FolderPage clickEvYasamTxt()" in result.updated_source
    assert "public FolderPage inputEvYasamTxt(String value)" in result.updated_source
    assert "/**" in result.updated_source
    assert "EV YASAM alanına tıklanır." in result.updated_source
    assert "@param value yazılacak değer" in result.updated_source
    assert 'logPass("EV YASAM alanına tıklandı.");' in result.updated_source
    assert 'logPass("EV YASAM alanına değer yazıldı: " + value);' in result.updated_source
    actions_region = _region_content(result.updated_source, "AUTO_ACTIONS")
    assert "return this;" in actions_region


def test_method_name_collision_adds_suffix() -> None:
    source = """public class FolderPage extends BaseLibrary {
    // region AUTO_LOCATORS
    // endregion AUTO_LOCATORS

    // region AUTO_ACTIONS
        public FolderPage clickEvYasamTxt() {
            return this;
        }
    // endregion AUTO_ACTIONS
}
"""

    result = prepare_java_patch(
        source=source,
        locator_name="EV_YASAM_TXT",
        selector_type="css",
        selector_value="input[name='evYasam']",
        actions=("click",),
    )

    assert result.ok
    assert result.changed
    assert "public FolderPage clickEvYasamTxt2()" in result.updated_source


def test_name_exists_shows_suffix_message() -> None:
    source = """public class FolderPage extends BaseLibrary {
    private final By EV_YASAM_TXT = By.cssSelector("button[data-testid='home-old']");

    // region AUTO_LOCATORS
    // endregion AUTO_LOCATORS

    // region AUTO_ACTIONS
    // endregion AUTO_ACTIONS
}
"""

    result = prepare_java_patch(
        source=source,
        locator_name="EV_YASAM_TXT",
        selector_type="css",
        selector_value="button[data-testid='home-new']",
        actions=(),
    )

    assert result.ok
    assert result.changed
    assert result.final_locator_name == "EV_YASAM_TXT_2"
    assert "Name exists; using EV_YASAM_TXT_2" in result.message


def test_uncertain_parse_missing_class_closing_brace() -> None:
    source = """public class FolderPage extends BaseLibrary {
    public FolderPage(WebDriver driver) {
        super(driver);
    }
"""

    result = prepare_java_patch(
        source=source,
        locator_name="KURAL_ADI_TXT",
        selector_type="css",
        selector_value="input[name='kural']",
        actions=("sendKeys",),
    )

    assert not result.ok
    assert not result.changed
    assert result.message == SAFE_PARSE_ERROR


def test_action_method_insertion_english_logs_and_javadocs() -> None:
    source = """public class FolderPage extends BaseLibrary {
    // region AUTO_LOCATORS
    // endregion AUTO_LOCATORS

    // region AUTO_ACTIONS
    // endregion AUTO_ACTIONS
}
"""

    result = prepare_java_patch(
        source=source,
        locator_name="HOME_BTN",
        selector_type="xpath",
        selector_value="//button[normalize-space()='Home']",
        actions=("click", "sendKeys"),
        log_language="EN",
    )

    assert result.ok
    assert result.changed
    assert "Clicks HOME element." in result.updated_source
    assert "Types a value into HOME element." in result.updated_source
    assert "@param value value to type" in result.updated_source
    assert 'logPass("Clicked HOME element.");' in result.updated_source
    assert 'logPass("Entered value into HOME element: " + value);' in result.updated_source
