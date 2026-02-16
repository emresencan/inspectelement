from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ModuleInfo:
    name: str
    module_path: Path
    pages_module_path: Path | None
    pages_source_root: Path | None


@dataclass(frozen=True, slots=True)
class PageClassInfo:
    class_name: str
    file_path: Path
    relative_path: str


def discover_modules(project_root: Path) -> list[ModuleInfo]:
    apps_root = project_root / "modules" / "apps"
    if not apps_root.is_dir():
        return []

    modules: list[ModuleInfo] = []
    for module_path in sorted((path for path in apps_root.iterdir() if path.is_dir()), key=lambda item: item.name.lower()):
        pages_module_path = _resolve_pages_module_path(module_path)
        pages_source_root = _resolve_pages_source_root(pages_module_path)
        modules.append(
            ModuleInfo(
                name=module_path.name,
                module_path=module_path,
                pages_module_path=pages_module_path,
                pages_source_root=pages_source_root,
            )
        )
    return modules


def discover_module(project_root: Path, module_name: str) -> ModuleInfo | None:
    normalized = module_name.strip()
    if not normalized:
        return None

    for module in discover_modules(project_root):
        if module.name == normalized:
            return module
    return None


def discover_page_classes(module: ModuleInfo) -> list[PageClassInfo]:
    if not module.pages_source_root or not module.pages_source_root.is_dir():
        return []

    pages: list[PageClassInfo] = []
    for java_file in sorted(module.pages_source_root.rglob("*.java")):
        if not _is_page_class(java_file):
            continue
        relative_path = java_file.relative_to(module.pages_source_root).as_posix()
        pages.append(
            PageClassInfo(
                class_name=java_file.stem,
                file_path=java_file,
                relative_path=relative_path,
            )
        )

    pages.sort(key=lambda item: (item.class_name.lower(), item.relative_path.lower()))
    return pages


def _resolve_pages_module_path(module_path: Path) -> Path | None:
    preferred = module_path / f"{module_path.name}-pages"
    if preferred.is_dir():
        return preferred

    alternatives = sorted(
        (child for child in module_path.iterdir() if child.is_dir() and child.name.endswith("-pages")),
        key=lambda item: item.name.lower(),
    )
    if alternatives:
        return alternatives[0]
    return None


def _resolve_pages_source_root(pages_module_path: Path | None) -> Path | None:
    if not pages_module_path:
        return None

    source_root = pages_module_path / "src" / "main" / "java"
    if source_root.is_dir():
        return source_root
    return None


def _is_page_class(java_file: Path) -> bool:
    if java_file.name.endswith("Page.java"):
        return True

    try:
        source = java_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "extends BaseLibrary" in source
