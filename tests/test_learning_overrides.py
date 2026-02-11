from inspectelement.learning_store import LearningStore


def test_override_save_and_load(tmp_path) -> None:
    store = LearningStore(base_dir=tmp_path)

    store.save_override(
        hostname="example.com",
        element_signature="tag=button|name=save",
        locator_type="XPath",
        locator="//button[normalize-space()='Save']",
    )

    override = store.get_override("example.com", "tag=button|name=save")
    assert override is not None
    assert override.hostname == "example.com"
    assert override.locator_type == "XPath"
    assert override.locator == "//button[normalize-space()='Save']"


def test_clear_overrides_removes_saved_items(tmp_path) -> None:
    store = LearningStore(base_dir=tmp_path)
    signature = "tag=input|name=email"

    store.save_override(
        hostname="example.com",
        element_signature=signature,
        locator_type="CSS",
        locator="input[name='email']",
    )
    assert store.get_override("example.com", signature) is not None

    store.clear_overrides()

    assert store.get_override("example.com", signature) is None
