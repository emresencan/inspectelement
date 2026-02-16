from __future__ import annotations

import re

import pytest

from inspectelement.java_pom_writer import (
    SAFE_PARSE_ERROR,
    build_action_method_signature_preview,
    prepare_java_patch,
)


def _region_content(source: str, region_name: str) -> str:
    pattern = re.compile(
        rf"// region {re.escape(region_name)}\n(?P<content>.*?)// endregion {re.escape(region_name)}",
        re.DOTALL,
    )
    match = pattern.search(source)
    assert match is not None
    return match.group("content")


def _method_block(source: str, method_name: str) -> str:
    pattern = re.compile(
        rf"public\s+[A-Za-z_]\w*\s+{re.escape(method_name)}\s*\([^)]*\)\s*\{{(?P<body>.*?)\n\s*\}}",
        re.DOTALL,
    )
    match = pattern.search(source)
    assert match is not None
    return match.group("body")


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
        actions=("clickElement",),
    )

    assert result.ok
    assert result.changed
    assert result.final_locator_name == "EXISTING"
    assert "Selector already exists as EXISTING; reusing." in result.message
    assert "private final By KAYDET_BTN" not in result.updated_source
    assert "public FolderPage clickExisting()" in result.updated_source


@pytest.mark.parametrize(
    ("action", "signature", "expected_call", "expected_return", "expects_log"),
    [
        (
            "clickElement",
            "public FolderPage clickTestLocatorBtn()",
            "clickElement(TEST_LOCATOR_BTN);",
            "return this;",
            True,
        ),
        (
            "javaScriptClicker",
            "public FolderPage jsClickTestLocatorBtn()",
            "javaScriptClicker(TEST_LOCATOR_BTN);",
            "return this;",
            True,
        ),
        (
            "getText",
            "public String getTestLocatorBtnText()",
            "String text = getText(TEST_LOCATOR_BTN);",
            "return text;",
            False,
        ),
        (
            "getAttribute",
            "public String getTestLocatorBtnAttribute(String attribute)",
            "String attr = getAttribute(TEST_LOCATOR_BTN, attribute);",
            "return attr;",
            False,
        ),
        (
            "isElementDisplayed",
            "public boolean isTestLocatorBtnDisplayed(int timeoutSeconds)",
            "boolean ok = isElementDisplayed(TEST_LOCATOR_BTN, timeoutSeconds);",
            "return ok;",
            False,
        ),
        (
            "isElementEnabled",
            "public boolean isTestLocatorBtnEnabled(int timeoutSeconds)",
            "boolean ok = isElementEnabled(TEST_LOCATOR_BTN, timeoutSeconds);",
            "return ok;",
            False,
        ),
        (
            "scrollToElement",
            "public FolderPage scrollToTestLocatorBtn()",
            "scrollToElement(TEST_LOCATOR_BTN);",
            "return this;",
            True,
        ),
        (
            "javaScriptClearAndSetValue",
            "public FolderPage jsSetTestLocatorBtn(String value)",
            "javaScriptClearAndSetValue(TEST_LOCATOR_BTN, value);",
            "return this;",
            True,
        ),
        (
            "javaScriptGetInnerText",
            "public String jsGetTestLocatorBtnInnerText()",
            "String t = javaScriptGetInnerText(TEST_LOCATOR_BTN);",
            "return t;",
            False,
        ),
        (
            "javaScriptGetValue",
            "public String jsGetTestLocatorBtnValue()",
            "String v = javaScriptGetValue(TEST_LOCATOR_BTN);",
            "return v;",
            False,
        ),
    ],
)
def test_action_method_templates(
    action: str,
    signature: str,
    expected_call: str,
    expected_return: str,
    expects_log: bool,
) -> None:
    source = """public class FolderPage extends BaseLibrary {
    // region AUTO_LOCATORS
    // endregion AUTO_LOCATORS

    // region AUTO_ACTIONS
    // endregion AUTO_ACTIONS
}
"""

    result = prepare_java_patch(
        source=source,
        locator_name="TEST_LOCATOR_BTN",
        selector_type="css",
        selector_value="button[data-testid='test-locator']",
        actions=(action,),
    )

    assert result.ok
    assert result.changed
    assert signature in result.updated_source
    assert result.added_method_signatures == (signature,)
    assert expected_call in result.updated_source
    method_name = result.added_methods[0]
    block = _method_block(result.updated_source, method_name)
    assert expected_return in block
    if expects_log:
        assert "logPass(" in block
    else:
        assert "logPass(" not in block


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
        actions=("clickElement",),
    )

    assert result.ok
    assert result.changed
    assert "public FolderPage clickEvYasamTxt_2()" in result.updated_source


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
        actions=("javaScriptClearAndSetValue",),
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
        actions=("clickElement", "javaScriptClearAndSetValue"),
        log_language="EN",
    )

    assert result.ok
    assert result.changed
    assert "Clicks HOME element." in result.updated_source
    assert "Clears and sets HOME value via JavaScript." in result.updated_source
    assert "@param value value to set" in result.updated_source
    assert 'logPass("Clicked HOME element.");' in result.updated_source
    assert 'logPass("Set HOME value via JavaScript: " + value);' in result.updated_source


def test_build_action_method_signature_preview() -> None:
    assert (
        build_action_method_signature_preview(
            page_class_name="FolderPage",
            locator_name="EV_YASAM_TXT",
            action="javaScriptClicker",
        )
        == "public FolderPage jsClickEvYasamTxt()"
    )
    assert (
        build_action_method_signature_preview(
            page_class_name="FolderPage",
            locator_name="EV_YASAM_TXT",
            action="getAttribute",
        )
        == "public String getEvYasamTxtAttribute(String attribute)"
    )


def test_table_required_actions_generate_expected_calls_and_imports() -> None:
    source = """package com.turkcell.pages;

public class FolderPage extends BaseLibrary {
    // region AUTO_LOCATORS
    // endregion AUTO_LOCATORS

    // region AUTO_ACTIONS
    // endregion AUTO_ACTIONS
}
"""
    result = prepare_java_patch(
        source=source,
        locator_name="ROW_CELL_TXT",
        selector_type="css",
        selector_value="td.cell",
        actions=("tableAssertRowExists", "tableHasAnyRow", "tableAssertHasAnyRow", "tableFilter"),
        table_root_selector_type="id",
        table_root_selector_value="ordersGrid",
        table_root_locator_name="ORDERS_TABLE",
    )
    assert result.ok
    assert result.changed
    assert "import java.time.Duration;" in result.updated_source
    assert "import com.turkcell.common.components.table.HtmlTableVerifier;" in result.updated_source
    assert "private final By ORDERS_TABLE = By.id(\"ordersGrid\");" in result.updated_source
    assert "public FolderPage assertOrdersTableRowExists(String columnHeader, String expectedText, int timeoutSec)" in result.updated_source
    assert ".whereEquals(columnHeader, expectedText)" in result.updated_source
    assert "public boolean hasOrdersTableAnyRow(int timeoutSec)" in result.updated_source
    assert ".hasAnyRow();" in result.updated_source
    assert "public FolderPage assertOrdersTableHasAnyRow(int timeoutSec)" in result.updated_source
    assert ".assertHasAnyRow();" in result.updated_source
    assert "public FolderPage filterOrdersTable(String columnHeader, String filterText, int timeoutSec)" in result.updated_source
    assert ".filter(columnHeader, filterText);" in result.updated_source


def test_select_action_generation_uses_universal_select_helper() -> None:
    source = """package com.turkcell.pages;

public class FolderPage extends BaseLibrary {
    // region AUTO_LOCATORS
    // endregion AUTO_LOCATORS

    // region AUTO_ACTIONS
    // endregion AUTO_ACTIONS
}
"""
    result = prepare_java_patch(
        source=source,
        locator_name="CITY_SELECT",
        selector_type="id",
        selector_value="citySelect",
        actions=("selectBySelectIdAuto", "selectByLabel"),
        action_parameters={"waitBeforeSelect": "true", "selectId": "citySelect"},
    )
    assert result.ok
    assert result.changed
    assert "import com.turkcell.common.components.selectHelper.UniversalSelectHelper;" in result.updated_source
    assert "public FolderPage selectCitySelect(String optionText)" in result.updated_source
    assert ".withWaitBeforeSelect(true)" in result.updated_source
    assert '.selectBySelectIdAuto("citySelect", optionText);' in result.updated_source
    assert "public FolderPage selectCitySelectByLabel(String labelText, String optionText)" in result.updated_source
    assert ".selectByLabel(labelText, optionText);" in result.updated_source


def test_select_by_select_id_auto_requires_select_id_when_not_id_selector() -> None:
    source = """public class FolderPage extends BaseLibrary {
    // region AUTO_LOCATORS
    // endregion AUTO_LOCATORS

    // region AUTO_ACTIONS
    // endregion AUTO_ACTIONS
}
"""
    result = prepare_java_patch(
        source=source,
        locator_name="CITY_SELECT",
        selector_type="css",
        selector_value="div.city-select",
        actions=("selectBySelectIdAuto",),
        action_parameters={"waitBeforeSelect": "false"},
    )
    assert not result.ok
    assert result.message == "Select Id is required for selectBySelectIdAuto."


def test_signature_preview_uses_table_locator_name_for_table_actions() -> None:
    signature = build_action_method_signature_preview(
        page_class_name="FolderPage",
        locator_name="EV_YASAM_TXT",
        action="tableHasAnyRow",
        table_locator_name="ORDERS_TABLE",
    )
    assert signature == "public boolean hasOrdersTableAnyRow(int timeoutSec)"


def test_table_actions_support_contains_chain_and_radio_click() -> None:
    source = """public class FolderPage extends BaseLibrary {
    // region AUTO_LOCATORS
    // endregion AUTO_LOCATORS

    // region AUTO_ACTIONS
    // endregion AUTO_ACTIONS
}
"""
    result = prepare_java_patch(
        source=source,
        locator_name="ROW_CELL_TXT",
        selector_type="css",
        selector_value="td.cell",
        actions=("tableAssertRowExists", "tableClickRadioInRow"),
        table_root_selector_type="id",
        table_root_selector_value="ordersGrid",
        table_root_locator_name="ORDERS_TABLE",
        action_parameters={"matchType": "contains"},
    )
    assert result.ok
    assert result.changed
    assert (
        "public FolderPage clickOrdersTableRadioInRow(String matchColumnHeader, String matchText, int timeoutSec)"
        in result.updated_source
    )
    assert ".whereContains(columnHeader, expectedText)" in result.updated_source
    assert ".whereContains(matchColumnHeader, matchText)" in result.updated_source
    assert ".assertRowExists()" in result.updated_source
    assert ".clickRadioInRow();" in result.updated_source


def test_table_predicate_and_all_equals_actions_generate_expected_signatures() -> None:
    source = """package com.turkcell.pages;

public class FolderPage extends BaseLibrary {
    // region AUTO_LOCATORS
    // endregion AUTO_LOCATORS

    // region AUTO_ACTIONS
    // endregion AUTO_ACTIONS
}
"""
    result = prepare_java_patch(
        source=source,
        locator_name="ROW_CELL_TXT",
        selector_type="css",
        selector_value="td.cell",
        actions=("tableAssertRowMatches", "tableAssertRowAllEquals"),
        table_root_selector_type="id",
        table_root_selector_value="ordersGrid",
        table_root_locator_name="ORDERS_TABLE",
    )
    assert result.ok
    assert result.changed
    assert "import java.util.Map;" in result.updated_source
    assert "import java.util.function.Predicate;" in result.updated_source
    assert (
        "public FolderPage assertOrdersTableRowMatches(String columnHeader, Predicate<String> predicate, int timeoutSec)"
        in result.updated_source
    )
    assert ".whereMatches(columnHeader, predicate)" in result.updated_source
    assert (
        "public FolderPage assertOrdersTableRowAllEquals(Map<String, String> columnToExpectedText, int timeoutSec)"
        in result.updated_source
    )
    assert ".whereAllEquals(columnToExpectedText)" in result.updated_source
    assert result.added_method_signatures == (
        "public FolderPage assertOrdersTableRowMatches(String columnHeader, Predicate<String> predicate, int timeoutSec)",
        "public FolderPage assertOrdersTableRowAllEquals(Map<String, String> columnToExpectedText, int timeoutSec)",
    )


def test_import_injection_keeps_static_imports_and_avoids_duplicates() -> None:
    source = """package com.turkcell.pages;

import com.turkcell.common.components.table.HtmlTableVerifier;
import static org.junit.Assert.assertTrue;
import com.turkcell.common.components.table.HtmlTableVerifier;

public class FolderPage extends BaseLibrary {
    // region AUTO_LOCATORS
    // endregion AUTO_LOCATORS

    // region AUTO_ACTIONS
    // endregion AUTO_ACTIONS
}
"""
    result = prepare_java_patch(
        source=source,
        locator_name="ROW_TXT",
        selector_type="css",
        selector_value="td.row",
        actions=("tableHasAnyRow",),
        table_root_selector_type="id",
        table_root_selector_value="ordersGrid",
        table_root_locator_name="ORDERS_TABLE",
    )
    assert result.ok
    assert result.changed
    assert result.updated_source.count("import com.turkcell.common.components.table.HtmlTableVerifier;") == 1
    assert "import static org.junit.Assert.assertTrue;" in result.updated_source
    assert "import java.time.Duration;" in result.updated_source


def test_style_alignment_preserves_crlf_line_endings() -> None:
    source = (
        "package com.turkcell.pages;\r\n\r\n"
        "public class FolderPage extends BaseLibrary {\r\n"
        "    // region AUTO_LOCATORS\r\n"
        "    // endregion AUTO_LOCATORS\r\n\r\n"
        "    // region AUTO_ACTIONS\r\n"
        "    // endregion AUTO_ACTIONS\r\n"
        "}\r\n"
    )
    result = prepare_java_patch(
        source=source,
        locator_name="HOME_BTN",
        selector_type="xpath",
        selector_value="//button[normalize-space()='Home']",
        actions=("clickElement",),
    )
    assert result.ok
    assert result.changed
    assert "\r\n" in result.updated_source
    assert "\r\nprivate final By" not in result.updated_source  # keeps class indentation
