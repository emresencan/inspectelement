from inspectelement.hybrid_capture import map_hover_box_to_overlay


def test_hover_box_maps_and_clamps_inside_viewport() -> None:
    box = map_hover_box_to_overlay(
        {"left": -25, "top": 10, "width": 700, "height": 500},
        viewport_width=640,
        viewport_height=360,
    )
    assert box is not None
    assert box.left == 0
    assert box.top == 10
    assert box.width == 640
    assert box.height == 350


def test_hover_box_returns_none_for_invalid_payload() -> None:
    assert map_hover_box_to_overlay(None, 1200, 800) is None
    assert map_hover_box_to_overlay({"left": "bad"}, 1200, 800) is None
    assert map_hover_box_to_overlay({"left": 10, "top": 10, "width": 0, "height": 20}, 1200, 800) is None


def test_hover_box_keeps_raw_element_identity_fields() -> None:
    box = map_hover_box_to_overlay(
        {"left": 20, "top": 40, "width": 120, "height": 24, "tag": "span", "id": "title", "class_name": "hero-text"},
        viewport_width=500,
        viewport_height=400,
    )
    assert box is not None
    assert box.tag == "span"
    assert box.element_id == "title"
    assert box.class_name == "hero-text"
