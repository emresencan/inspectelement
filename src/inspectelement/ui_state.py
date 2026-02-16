from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path


@dataclass(slots=True)
class WorkspaceConfig:
    project_root: str = ""
    module_name: str = ""
    page_class: str = ""
    url: str = "https://example.com"
    inspect_enabled: bool = False


def load_workspace_config(path: Path) -> WorkspaceConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return WorkspaceConfig()
    except json.JSONDecodeError:
        return WorkspaceConfig()

    if not isinstance(payload, dict):
        return WorkspaceConfig()

    return WorkspaceConfig(
        project_root=str(payload.get("project_root", "") or ""),
        module_name=str(payload.get("module_name", "") or ""),
        page_class=str(payload.get("page_class", "") or ""),
        url=str(payload.get("url", "https://example.com") or "https://example.com"),
        inspect_enabled=bool(payload.get("inspect_enabled", False)),
    )


def save_workspace_config(path: Path, config: WorkspaceConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(config)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class WorkspaceEnableState:
    can_preview: bool
    can_apply: bool
    can_cancel_preview: bool


def compute_enable_state(
    *,
    has_page: bool,
    has_locator: bool,
    has_element_name: bool,
    validation_ok: bool,
    has_preview: bool,
) -> WorkspaceEnableState:
    can_preview = has_page and has_locator and has_element_name
    can_apply = has_preview and validation_ok
    can_cancel = has_preview
    return WorkspaceEnableState(
        can_preview=can_preview,
        can_apply=can_apply,
        can_cancel_preview=can_cancel,
    )


def can_enable_new_page(*, has_project_root: bool, has_module: bool) -> bool:
    return bool(has_project_root and has_module)


def can_enable_inspect(*, has_launched_page: bool) -> bool:
    return bool(has_launched_page)
