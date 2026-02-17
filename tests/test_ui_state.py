from __future__ import annotations

from pathlib import Path

from inspectelement.ui_state import (
    WorkspaceState,
    can_enable_inspect_toggle,
    can_enable_new_page_button,
    compute_workspace_button_state,
    load_workspace_state,
    save_workspace_state,
)


def test_load_workspace_state_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_workspace_state(tmp_path / "missing.json") is None


def test_save_and_load_workspace_state_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    state = WorkspaceState(
        project_root="/tmp/project",
        module_name="sample-module",
        page_class_name="LoginPage",
        url="https://example.com/login",
        inspect_enabled=True,
    )

    ok, message = save_workspace_state(state, target)
    assert ok is True
    assert message is None

    loaded = load_workspace_state(target)
    assert loaded == state


def test_compute_workspace_button_state_requires_preview_inputs() -> None:
    state = compute_workspace_button_state(
        has_page=False,
        has_locator=True,
        has_name=True,
        has_pending_preview=False,
    )

    assert state.can_preview is False
    assert state.can_validate is False
    assert state.can_apply is False
    assert state.can_cancel_preview is False


def test_compute_workspace_button_state_with_pending_preview() -> None:
    state = compute_workspace_button_state(
        has_page=True,
        has_locator=True,
        has_name=True,
        has_pending_preview=True,
    )

    assert state.can_preview is True
    assert state.can_validate is True
    assert state.can_apply is True
    assert state.can_cancel_preview is True


def test_can_enable_new_page_button_requires_project_and_module() -> None:
    assert (
        can_enable_new_page_button(
            has_project_root=True,
            has_module=True,
            has_pages_source_root=True,
        )
        is True
    )
    assert (
        can_enable_new_page_button(
            has_project_root=True,
            has_module=True,
            has_pages_source_root=False,
        )
        is True
    )
    assert (
        can_enable_new_page_button(
            has_project_root=False,
            has_module=True,
            has_pages_source_root=True,
        )
        is False
    )
    assert (
        can_enable_new_page_button(
            has_project_root=True,
            has_module=False,
            has_pages_source_root=True,
        )
        is False
    )


def test_can_enable_inspect_toggle_requires_launch_and_embedded_browser() -> None:
    assert can_enable_inspect_toggle(has_launched_page=True, has_embedded_browser=True) is True
    assert can_enable_inspect_toggle(has_launched_page=False, has_embedded_browser=True) is False
    assert can_enable_inspect_toggle(has_launched_page=True, has_embedded_browser=False) is False
