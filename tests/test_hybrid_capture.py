from inspectelement.hybrid_capture import (
    classify_probe_payload,
    map_coordinates_to_viewport,
    normalize_capture_coordinates,
    normalize_viewport_size,
    select_raw_and_refined_indices,
    select_refined_target_index,
    should_sync_navigation,
)
from inspectelement.browser_manager import BrowserManager


def test_normalize_capture_coordinates_clamps_to_viewport() -> None:
    point = normalize_capture_coordinates(-25, 9999, 400, 300)
    assert point.x == 0
    assert point.y == 299
    assert point.source_width == 400
    assert point.source_height == 300


def test_map_coordinates_to_viewport_scales_between_sizes() -> None:
    point = normalize_capture_coordinates(200, 150, 400, 300)
    mapped_x, mapped_y = map_coordinates_to_viewport(point, 800, 600)
    assert mapped_x == 400
    assert mapped_y == 300


def test_normalize_viewport_size_defaults_when_invalid() -> None:
    width, height = normalize_viewport_size("bad", None)
    assert width == 1280
    assert height == 720


def test_classify_probe_payload_detects_cross_origin_iframe() -> None:
    probe = classify_probe_payload(
        {
            "status": "cross_origin_iframe",
            "tag": "iframe",
            "cross_origin_iframe": True,
        }
    )
    assert probe.status == "cross_origin_iframe"
    assert probe.tag == "iframe"
    assert probe.cross_origin_iframe


def test_should_sync_navigation_changes_only_when_url_differs() -> None:
    assert should_sync_navigation("https://example.com", "https://example.com/a")
    assert not should_sync_navigation("https://example.com", "https://example.com")
    assert not should_sync_navigation("https://example.com", "")


def test_capture_payload_includes_scroll_and_source_url() -> None:
    manager = BrowserManager(
        on_capture=lambda _summary, _candidates: None,
        on_status=lambda _message: None,
        on_page_info=lambda _title, _url: None,
    )
    manager.capture_at_coordinates(
        120.4,
        240.7,
        viewport=(1280, 720),
        scroll=(0, 860),
        source_url="https://example.com/products",
        source_dpr=2.0,
    )

    command, payload = manager._commands.get_nowait()
    assert command == "capture_coordinates"
    assert payload["x"] == 120.4
    assert payload["y"] == 240.7
    assert payload["viewport_width"] == 1280
    assert payload["viewport_height"] == 720
    assert payload["scroll_x"] == 0
    assert payload["scroll_y"] == 860
    assert payload["source_url"] == "https://example.com/products"
    assert payload["device_pixel_ratio"] == 2.0


def test_capture_busy_flag_resets_after_exception() -> None:
    statuses: list[str] = []
    manager = BrowserManager(
        on_capture=lambda _summary, _candidates: None,
        on_status=lambda message: statuses.append(message),
        on_page_info=lambda _title, _url: None,
    )
    manager._page = object()
    manager._inspect_enabled = True

    def _boom(_payload: dict) -> None:
        raise RuntimeError("boom")

    manager._execute_capture_coordinates = _boom  # type: ignore[method-assign]
    manager._handle_capture_coordinates({"x": 10, "y": 20, "viewport_width": 800, "viewport_height": 600})

    assert manager._capture_busy is False
    assert any("Capture failed unexpectedly" in message for message in statuses)


def test_sequential_capture_requests_are_processed_multiple_times() -> None:
    manager = BrowserManager(
        on_capture=lambda _summary, _candidates: None,
        on_status=lambda _message: None,
        on_page_info=lambda _title, _url: None,
    )
    manager._page = object()
    manager._inspect_enabled = True
    seen: list[tuple[int, int]] = []

    def _capture(payload: dict) -> None:
        seen.append((int(payload["x"]), int(payload["y"])))

    manager._execute_capture_coordinates = _capture  # type: ignore[method-assign]
    manager._handle_capture_coordinates({"x": 10, "y": 20, "viewport_width": 800, "viewport_height": 600})
    manager._handle_capture_coordinates({"x": 40, "y": 50, "viewport_width": 800, "viewport_height": 600})

    assert seen == [(10, 20), (40, 50)]


def test_refinement_prefers_actionable_child_over_wrapper_div() -> None:
    nodes = [
        {
            "index": 0,
            "tag": "div",
            "class_name": "gender-modal-content-header",
            "text": "Yemek",
            "visible": True,
            "aria_hidden": False,
            "actionable": False,
            "generic_wrapper": True,
            "rect_contains": True,
        },
        {
            "index": 1,
            "tag": "a",
            "class_name": "tile-link",
            "text": "Yemek",
            "visible": True,
            "aria_hidden": False,
            "actionable": True,
            "data_testid": "menu-item-yemek",
            "rect_contains": True,
        },
    ]
    choice = select_refined_target_index(nodes)
    assert choice.index == 1
    assert "wrapper-refine" in choice.reason


def test_refinement_for_tile_icon_text_prefers_tile_link() -> None:
    nodes = [
        {
            "index": 0,
            "tag": "div",
            "class_name": "tile-container content-wrapper",
            "text": "",
            "visible": True,
            "aria_hidden": False,
            "actionable": False,
            "generic_wrapper": True,
            "rect_contains": True,
        },
        {
            "index": 1,
            "tag": "span",
            "class_name": "tile-icon",
            "text": "",
            "visible": True,
            "aria_hidden": False,
            "actionable": False,
            "clickable_ancestor": "a",
            "rect_contains": True,
        },
        {
            "index": 2,
            "tag": "a",
            "class_name": "tile-anchor",
            "text": "Yemek",
            "visible": True,
            "aria_hidden": False,
            "actionable": True,
            "aria_label": "Yemek",
            "data_testid": "tile-yemek",
            "rect_contains": True,
        },
    ]
    choice = select_refined_target_index(nodes)
    assert choice.index in {1, 2}


def test_raw_and_refined_selection_keeps_raw_node_and_refines_target() -> None:
    nodes = [
        {
            "index": 0,
            "tag": "div",
            "class_name": "modal-wrapper content",
            "text": "Yemek",
            "visible": True,
            "aria_hidden": False,
            "actionable": False,
            "generic_wrapper": True,
            "rect_contains": True,
        },
        {
            "index": 1,
            "tag": "span",
            "class_name": "tile-label",
            "text": "Yemek",
            "visible": True,
            "aria_hidden": False,
            "actionable": False,
            "clickable_ancestor": "a",
            "rect_contains": True,
        },
        {
            "index": 2,
            "tag": "a",
            "class_name": "tile-link",
            "text": "Yemek",
            "visible": True,
            "aria_hidden": False,
            "actionable": True,
            "data_testid": "tile-yemek",
            "rect_contains": True,
        },
    ]
    choice = select_raw_and_refined_indices(nodes)
    assert choice.raw_index == 0
    assert choice.refined_index in {1, 2}
    assert choice.refined_reason
