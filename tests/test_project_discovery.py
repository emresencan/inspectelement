from pathlib import Path

from inspectelement.project_discovery import ModuleInfo, discover_modules, discover_page_classes


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_discover_modules_from_project_root(tmp_path: Path) -> None:
    root = tmp_path / "AUTOMATION-SUITE"
    _write(root / "modules" / "apps" / "incentra" / "incentra-pages" / "src" / "main" / "java" / "a" / "Placeholder.java", "")
    (root / "modules" / "apps" / "fox").mkdir(parents=True)

    modules = discover_modules(root)

    assert [module.name for module in modules] == ["fox", "incentra"]
    incentra = next(module for module in modules if module.name == "incentra")
    assert incentra.pages_module_path == root / "modules" / "apps" / "incentra" / "incentra-pages"
    assert incentra.pages_source_root == root / "modules" / "apps" / "incentra" / "incentra-pages" / "src" / "main" / "java"


def test_discover_modules_uses_fallback_pages_module(tmp_path: Path) -> None:
    root = tmp_path / "AUTOMATION-SUITE"
    pages_root = root / "modules" / "apps" / "custom" / "legacy-pages" / "src" / "main" / "java"
    pages_root.mkdir(parents=True)

    modules = discover_modules(root)

    assert len(modules) == 1
    assert modules[0].name == "custom"
    assert modules[0].pages_source_root == pages_root


def test_discover_page_classes_from_filename_and_base_library(tmp_path: Path) -> None:
    pages_source_root = tmp_path / "src" / "main" / "java"
    _write(
        pages_source_root / "com" / "turkcell" / "pages" / "incentra" / "FolderPage.java",
        "public class FolderPage { }",
    )
    _write(
        pages_source_root / "com" / "turkcell" / "pages" / "incentra" / "RuleScreen.java",
        "public class RuleScreen extends BaseLibrary { }",
    )
    _write(
        pages_source_root / "com" / "turkcell" / "pages" / "incentra" / "Helper.java",
        "public class Helper { }",
    )

    module = ModuleInfo(
        name="incentra",
        module_path=tmp_path,
        pages_module_path=tmp_path,
        pages_source_root=pages_source_root,
    )

    pages = discover_page_classes(module)

    assert [page.class_name for page in pages] == ["FolderPage", "RuleScreen"]
    assert pages[0].relative_path.endswith("FolderPage.java")
    assert pages[1].relative_path.endswith("RuleScreen.java")


def test_discover_page_classes_empty_when_source_missing(tmp_path: Path) -> None:
    module = ModuleInfo(
        name="incentra",
        module_path=tmp_path,
        pages_module_path=tmp_path,
        pages_source_root=tmp_path / "does-not-exist",
    )

    assert discover_page_classes(module) == []
