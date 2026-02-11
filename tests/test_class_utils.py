from inspectelement.locator_generator import is_dynamic_class, normalize_classes


def test_normalize_classes_deduplicates_and_trims() -> None:
    assert normalize_classes([" btn ", "btn", "btn-primary", "", "  "]) == ["btn", "btn-primary"]
    assert normalize_classes("btn btn  btn-primary") == ["btn", "btn-primary"]


def test_dynamic_class_detection() -> None:
    assert is_dynamic_class("css-12ab9c")
    assert is_dynamic_class("jss123")
    assert is_dynamic_class("sc-aBc123")
    assert is_dynamic_class("a1b2c3d4e5f6")

    assert not is_dynamic_class("btn-primary")
    assert not is_dynamic_class("card")
