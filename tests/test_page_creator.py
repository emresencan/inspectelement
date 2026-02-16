from pathlib import Path

from inspectelement.page_creator import (
    apply_page_creation_preview,
    build_page_template,
    detect_base_library_import,
    detect_page_package,
    generate_page_creation_preview,
    normalize_page_class_name,
)
from inspectelement.project_discovery import ModuleInfo, PageClassInfo


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_normalize_page_name_validation() -> None:
    assert normalize_page_class_name("")[1] == "Page Name is required."
    assert normalize_page_class_name("foo-page")[1] == "Page Name must contain only letters and numbers."
    assert normalize_page_class_name("fooPage")[1] == "Page Name must be PascalCase."
    assert normalize_page_class_name("OrdersPagePage")[0] == "OrdersPage"


def test_detect_package_and_base_library_from_existing_page(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "main" / "java"
    page_file = source_root / "com" / "turkcell" / "pages" / "incentra" / "OrdersPage.java"
    _write(
        page_file,
        """package com.turkcell.pages.incentra;

import com.turkcell.common.BaseLibrary;
public class OrdersPage extends BaseLibrary {}
""",
    )
    module = ModuleInfo(
        name="incentra",
        module_path=tmp_path,
        pages_module_path=tmp_path,
        pages_source_root=source_root,
    )
    pages = [PageClassInfo(class_name="OrdersPage", file_path=page_file, relative_path="com/turkcell/pages/incentra/OrdersPage.java")]
    assert detect_page_package(module, pages) == "com.turkcell.pages.incentra"
    assert detect_base_library_import(module, pages) == "com.turkcell.common.BaseLibrary"


def test_template_contains_expected_standard() -> None:
    template = build_page_template(
        package_name="com.turkcell.pages.incentra",
        class_name="NewPageName",
        base_library_import="com.turkcell.common.BaseLibrary",
    )
    assert "package com.turkcell.pages.incentra;" in template
    assert "import org.openqa.selenium.By;" in template
    assert "import org.openqa.selenium.WebDriver;" in template
    assert "import com.turkcell.common.BaseLibrary;" in template
    assert "public class NewPageName extends BaseLibrary {" in template
    assert "private final By BTN_EDIT_MODE =" in template
    assert 'By.xpath("//a[contains(text(),\'Edit Moda Geç\')]");' in template
    assert "public NewPageName(WebDriver driver) {" in template
    assert "// region AUTO_LOCATORS" in template
    assert "// region AUTO_ACTIONS" in template


def test_page_creation_preview_and_apply(tmp_path: Path) -> None:
    source_root = tmp_path / "modules" / "apps" / "incentra" / "incentra-pages" / "src" / "main" / "java"
    existing = source_root / "com" / "turkcell" / "pages" / "incentra" / "DashboardPage.java"
    _write(
        existing,
        """package com.turkcell.pages.incentra;
import com.turkcell.common.BaseLibrary;
public class DashboardPage extends BaseLibrary {}
""",
    )
    module = ModuleInfo(
        name="incentra",
        module_path=tmp_path / "modules" / "apps" / "incentra",
        pages_module_path=tmp_path / "modules" / "apps" / "incentra" / "incentra-pages",
        pages_source_root=source_root,
    )
    pages = [PageClassInfo(class_name="DashboardPage", file_path=existing, relative_path="com/turkcell/pages/incentra/DashboardPage.java")]
    preview = generate_page_creation_preview(module, pages, "OrdersPage")
    assert preview.ok
    assert preview.file_content is not None
    assert preview.target_file.name == "OrdersPage.java"
    assert "Preview generated — no files written." in preview.message
    assert "--- /dev/null" in preview.diff_text

    applied, message = apply_page_creation_preview(preview)
    assert applied
    assert "Applied. New page created at" in message
    assert preview.target_file.exists()
    assert "public class OrdersPage extends BaseLibrary {" in preview.target_file.read_text(encoding="utf-8")


def test_page_creation_blocks_existing_page(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "main" / "java"
    existing = source_root / "com" / "turkcell" / "pages" / "incentra" / "OrdersPage.java"
    _write(
        existing,
        """package com.turkcell.pages.incentra;
import com.turkcell.common.BaseLibrary;
public class OrdersPage extends BaseLibrary {}
""",
    )
    module = ModuleInfo(
        name="incentra",
        module_path=tmp_path,
        pages_module_path=tmp_path,
        pages_source_root=source_root,
    )
    pages = [PageClassInfo(class_name="OrdersPage", file_path=existing, relative_path="com/turkcell/pages/incentra/OrdersPage.java")]
    preview = generate_page_creation_preview(module, pages, "OrdersPage")
    assert not preview.ok
    assert preview.message == "OrdersPage already exists."


def test_page_creation_preview_uses_pages_module_when_source_root_missing(tmp_path: Path) -> None:
    pages_module = tmp_path / "modules" / "apps" / "incentra" / "incentra-pages"
    module = ModuleInfo(
        name="incentra",
        module_path=tmp_path / "modules" / "apps" / "incentra",
        pages_module_path=pages_module,
        pages_source_root=None,
    )
    preview = generate_page_creation_preview(module, [], "LoginPage")
    assert preview.ok
    assert preview.target_file == (
        pages_module
        / "src"
        / "main"
        / "java"
        / "com"
        / "turkcell"
        / "pages"
        / "incentra"
        / "LoginPage.java"
    )
