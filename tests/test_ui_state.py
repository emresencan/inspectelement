from pathlib import Path

from inspectelement.ui_state import (
    WorkspaceConfig,
    can_enable_inspect,
    can_enable_new_page,
    compute_enable_state,
    load_workspace_config,
    save_workspace_config,
)


def test_workspace_config_roundtrip(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    original = WorkspaceConfig(
        project_root="/tmp/automation-suite",
        module_name="incentra",
        page_class="DashboardPage",
        url="https://example.org",
        inspect_enabled=True,
    )
    save_workspace_config(config_path, original)
    loaded = load_workspace_config(config_path)
    assert loaded == original


def test_workspace_config_load_fallbacks(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    assert load_workspace_config(config_path) == WorkspaceConfig()
    config_path.write_text("{invalid", encoding="utf-8")
    assert load_workspace_config(config_path) == WorkspaceConfig()


def test_compute_enable_state_rules() -> None:
    disabled = compute_enable_state(
        has_page=False,
        has_locator=True,
        has_element_name=True,
        validation_ok=True,
        has_preview=True,
    )
    assert not disabled.can_preview
    assert disabled.can_apply
    assert disabled.can_cancel_preview

    preview_ready = compute_enable_state(
        has_page=True,
        has_locator=True,
        has_element_name=True,
        validation_ok=False,
        has_preview=True,
    )
    assert preview_ready.can_preview
    assert not preview_ready.can_apply
    assert preview_ready.can_cancel_preview


def test_can_enable_new_page_requires_project_and_module() -> None:
    assert not can_enable_new_page(has_project_root=False, has_module=False)
    assert not can_enable_new_page(has_project_root=True, has_module=False)
    assert not can_enable_new_page(has_project_root=False, has_module=True)
    assert can_enable_new_page(has_project_root=True, has_module=True)


def test_can_enable_inspect_requires_launch() -> None:
    assert not can_enable_inspect(has_launched_page=False)
    assert can_enable_inspect(has_launched_page=True)
