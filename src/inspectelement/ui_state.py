from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import tempfile

CONFIG_DIR = Path.home() / ".inspectelement"
CONFIG_PATH = CONFIG_DIR / "config.json"


@dataclass(slots=True)
class WorkspaceState:
    project_root: str = ""
    module_name: str = ""
    page_class_name: str = ""
    url: str = "https://example.com"
    inspect_enabled: bool = False


@dataclass(frozen=True, slots=True)
class WorkspaceButtonState:
    can_preview: bool
    can_validate: bool
    can_apply: bool
    can_cancel_preview: bool


def load_workspace_state(config_path: Path | None = None) -> WorkspaceState | None:
    path = config_path or CONFIG_PATH
    if not path.exists() or not path.is_file():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None

    if not isinstance(payload, dict):
        return None

    return WorkspaceState(
        project_root=str(payload.get("project_root", "") or ""),
        module_name=str(payload.get("module_name", "") or ""),
        page_class_name=str(payload.get("page_class_name", "") or ""),
        url=str(payload.get("url", "https://example.com") or "https://example.com"),
        inspect_enabled=bool(payload.get("inspect_enabled", False)),
    )


def save_workspace_state(state: WorkspaceState, config_path: Path | None = None) -> tuple[bool, str | None]:
    path = config_path or CONFIG_PATH

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Could not create config folder: {exc}"

    payload = json.dumps(asdict(state), ensure_ascii=True, indent=2, sort_keys=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            temp_path = Path(handle.name)

        if temp_path is None:
            return False, "Could not create temporary config file."
        temp_path.replace(path)
    except OSError as exc:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False, f"Could not write workspace state: {exc}"

    return True, None


def compute_workspace_button_state(
    *,
    has_page: bool,
    has_locator: bool,
    has_name: bool,
    has_pending_preview: bool,
) -> WorkspaceButtonState:
    can_preview = has_page and has_locator and has_name and not has_pending_preview
    can_validate = has_page and has_locator and has_name and not has_pending_preview
    can_apply = has_pending_preview
    can_cancel_preview = has_pending_preview

    return WorkspaceButtonState(
        can_preview=can_preview,
        can_validate=can_validate,
        can_apply=can_apply,
        can_cancel_preview=can_cancel_preview,
    )
