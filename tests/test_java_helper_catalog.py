from pathlib import Path

import pytest

from inspectelement.java_helper_catalog import (
    build_select_catalog_markdown,
    build_table_catalog_markdown,
    extract_java_methods,
    extract_java_methods_from_file,
)


def test_extract_java_methods_from_interface_and_class_signatures() -> None:
    source = """
    public interface DemoVerifier {
        DemoVerifier inTable(By tableLocator);
        boolean hasAnyRow();
        String getColumnText(String columnHeader);
    }
    """

    methods = extract_java_methods(source, owner_name="DemoVerifier")
    names = [method.name for method in methods]
    assert "inTable" in names
    assert "hasAnyRow" in names
    assert "getColumnText" in names


def test_catalog_markdown_contains_expected_headers() -> None:
    source = """
    public class UniversalSelectHelper {
        public UniversalSelectHelper withWaitBeforeSelect(boolean enabled) { return this; }
        public void selectByLabel(String labelText, String optionText) {}
    }
    """
    methods = extract_java_methods(source, owner_name="UniversalSelectHelper", require_public=True)
    markdown = build_select_catalog_markdown(methods)
    assert "Select Actions Catalog" in markdown
    assert "withWaitBeforeSelect" in markdown
    assert "selectByLabel" in markdown


def test_extract_java_methods_from_real_helper_files_if_available() -> None:
    table_verifier = Path(
        "/Users/emresencan/automation-suite/modules/components-common/src/main/java/"
        "com/turkcell/common/components/table/TableVerifier.java"
    )
    html_table_verifier = Path(
        "/Users/emresencan/automation-suite/modules/components-common/src/main/java/"
        "com/turkcell/common/components/table/HtmlTableVerifier.java"
    )
    universal_select_helper = Path(
        "/Users/emresencan/automation-suite/modules/components-common/src/main/java/"
        "com/turkcell/common/components/selectHelper/UniversalSelectHelper.java"
    )
    if not (table_verifier.exists() and html_table_verifier.exists() and universal_select_helper.exists()):
        pytest.skip("Local helper files are not available in this environment.")

    table_methods = extract_java_methods_from_file(table_verifier, owner_name="TableVerifier")
    html_methods = extract_java_methods_from_file(html_table_verifier, owner_name="HtmlTableVerifier", require_public=True)
    select_methods = extract_java_methods_from_file(
        universal_select_helper,
        owner_name="UniversalSelectHelper",
        require_public=True,
    )

    assert any(method.name == "assertRowExists" for method in table_methods)
    assert any(method.name == "hasAnyRow" for method in html_methods)
    assert any(method.name == "selectBySelectIdAuto" for method in select_methods)

    table_markdown = build_table_catalog_markdown(table_methods, html_methods)
    select_markdown = build_select_catalog_markdown(select_methods)
    assert "Table Actions Catalog" in table_markdown
    assert "Select Actions Catalog" in select_markdown
