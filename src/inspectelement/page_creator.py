from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import unified_diff
import os
from pathlib import Path
import re
import tempfile
from typing import Sequence

from .project_discovery import ModuleInfo, PageClassInfo

DEFAULT_BASE_LIBRARY_IMPORT = "com.turkcell.common.BaseLibrary"


@dataclass(frozen=True, slots=True)
class PageCreationPreview:
    ok: bool
    target_file: Path
    class_name: str | None
    package_name: str | None
    base_library_import: str | None
    message: str
    diff_text: str
    file_content: str | None


def generate_page_creation_preview(
    module: ModuleInfo,
    existing_pages: Sequence[PageClassInfo],
    page_name_raw: str,
) -> PageCreationPreview:
    class_name, error = normalize_page_class_name(page_name_raw)
    if error:
        return PageCreationPreview(
            ok=False,
            target_file=_fallback_target(module, page_name_raw),
            class_name=None,
            package_name=None,
            base_library_import=None,
            message=error,
            diff_text="",
            file_content=None,
        )

    if module.pages_source_root is None:
        return PageCreationPreview(
            ok=False,
            target_file=_fallback_target(module, class_name),
            class_name=class_name,
            package_name=None,
            base_library_import=None,
            message="Pages source root not found for selected module.",
            diff_text="",
            file_content=None,
        )

    if any(page.class_name == class_name for page in existing_pages):
        return PageCreationPreview(
            ok=False,
            target_file=_fallback_target(module, class_name),
            class_name=class_name,
            package_name=None,
            base_library_import=None,
            message=f"{class_name} already exists.",
            diff_text="",
            file_content=None,
        )

    package_name = detect_page_package(module, existing_pages)
    base_library_import = detect_base_library_import(module, existing_pages)
    target_file = module.pages_source_root / Path(*package_name.split(".")) / f"{class_name}.java"
    if target_file.exists():
        return PageCreationPreview(
            ok=False,
            target_file=target_file,
            class_name=class_name,
            package_name=package_name,
            base_library_import=base_library_import,
            message=f"{target_file.name} already exists.",
            diff_text="",
            file_content=None,
        )

    content = build_page_template(
        package_name=package_name,
        class_name=class_name,
        base_library_import=base_library_import,
    )
    diff_text = "".join(
        unified_diff(
            [],
            content.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile=str(target_file),
            lineterm="",
        )
    )
    return PageCreationPreview(
        ok=True,
        target_file=target_file,
        class_name=class_name,
        package_name=package_name,
        base_library_import=base_library_import,
        message="Preview generated — no files written.",
        diff_text=diff_text,
        file_content=content,
    )


def apply_page_creation_preview(preview: PageCreationPreview) -> tuple[bool, str]:
    if not preview.ok or not preview.file_content:
        return False, "No page creation preview to apply."

    target = preview.target_file
    if target.exists():
        return False, f"Target file already exists: {target}"

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
        temp_path = Path(temp_name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as temp_file:
            temp_file.write(preview.file_content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        temp_path.replace(target)
    except OSError as exc:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False, f"Could not create page file: {exc}"
    return True, f"Applied. New page created at {target}"


def normalize_page_class_name(raw_value: str) -> tuple[str, str | None]:
    value = raw_value.strip()
    if not value:
        return "", "Page Name is required."
    if not re.fullmatch(r"[A-Za-z0-9]+", value):
        return "", "Page Name must contain only letters and numbers."
    if not value[:1].isalpha():
        return "", "Page Name must start with a letter."
    if not value[:1].isupper():
        return "", "Page Name must be PascalCase."

    normalized = re.sub(r"(Page)+$", "Page", value)
    return normalized, None


def detect_page_package(module: ModuleInfo, existing_pages: Sequence[PageClassInfo]) -> str:
    package_counter: Counter[str] = Counter()
    for page in existing_pages:
        package_name = _extract_package_name(page.file_path)
        if package_name:
            package_counter[package_name] += 1
    if package_counter:
        return package_counter.most_common(1)[0][0]

    source_root = module.pages_source_root
    if source_root and source_root.exists():
        java_candidates = sorted(source_root.rglob("*.java"))
        for candidate in java_candidates:
            package_name = _extract_package_name(candidate)
            if package_name:
                return package_name

    module_segment = re.sub(r"[^a-z0-9]+", "", module.name.lower()) or "module"
    return f"com.turkcell.pages.{module_segment}"


def detect_base_library_import(module: ModuleInfo, existing_pages: Sequence[PageClassInfo]) -> str:
    import_counter: Counter[str] = Counter()
    for page in existing_pages:
        import_name = _extract_base_library_import(page.file_path)
        if import_name:
            import_counter[import_name] += 1
    if import_counter:
        return import_counter.most_common(1)[0][0]

    source_root = module.pages_source_root
    if source_root and source_root.exists():
        java_candidates = sorted(source_root.rglob("*.java"))
        for candidate in java_candidates:
            import_name = _extract_base_library_import(candidate)
            if import_name:
                return import_name

    return DEFAULT_BASE_LIBRARY_IMPORT


def build_page_template(package_name: str, class_name: str, base_library_import: str) -> str:
    return (
        f"package {package_name};\n"
        "\n"
        "import org.openqa.selenium.By;\n"
        "import org.openqa.selenium.WebDriver;\n"
        f"import {base_library_import};\n"
        "\n"
        f"public class {class_name} extends BaseLibrary {{\n"
        "\n"
        "    private final By BTN_EDIT_MODE =\n"
        "        By.xpath(\"//a[contains(text(),'Edit Moda Geç')]\");\n"
        "\n"
        f"    public {class_name}(WebDriver driver) {{\n"
        "        super(driver);\n"
        "    }\n"
        "\n"
        "    // region AUTO_LOCATORS\n"
        "    // endregion AUTO_LOCATORS\n"
        "\n"
        "    // region AUTO_ACTIONS\n"
        "    // endregion AUTO_ACTIONS\n"
        "}\n"
    )


def _extract_package_name(java_file: Path) -> str | None:
    try:
        source = java_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(r"(?m)^\s*package\s+([A-Za-z0-9_.]+)\s*;\s*$", source)
    if not match:
        return None
    return match.group(1)


def _extract_base_library_import(java_file: Path) -> str | None:
    try:
        source = java_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(r"(?m)^\s*import\s+([A-Za-z0-9_.]*BaseLibrary)\s*;\s*$", source)
    if not match:
        return None
    return match.group(1)


def _fallback_target(module: ModuleInfo, class_name_or_raw: str) -> Path:
    base = module.pages_source_root or module.module_path
    cleaned = class_name_or_raw.strip() or "NewPage"
    safe_name = re.sub(r"[^A-Za-z0-9]+", "", cleaned) or "NewPage"
    return base / f"{safe_name}.java"
