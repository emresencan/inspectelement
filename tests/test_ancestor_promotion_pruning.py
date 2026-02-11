from inspectelement.locator_generator import (
    _build_promoted_clickable_ancestor_drafts,
    _build_stable_attr_drafts,
    _prune_descendant_css_locator,
)


class FakePage:
    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts

    def query_selector_all(self, selector: str) -> list[object]:
        return [object()] * int(self.counts.get(selector, 0))


class FakeElement:
    def __init__(self, snapshot: dict) -> None:
        self.snapshot = snapshot

    def evaluate(self, _script: str) -> dict:
        return self.snapshot


def test_promote_clickable_ancestor_prefers_anchor_stable_locator() -> None:
    element = FakeElement(
        {
            "tag": "a",
            "role": "link",
            "attrs": {"data-testid": "scheduleBoxHotel"},
        }
    )
    page = FakePage({'a[data-testid="scheduleBoxHotel"]': 1})

    drafts = _build_promoted_clickable_ancestor_drafts(page, element)

    assert drafts is not None
    css_locators = [draft.locator for draft in drafts if draft.locator_type == "CSS"]
    assert css_locators == ['a[data-testid="scheduleBoxHotel"]']
    assert all(" div" not in locator for locator in css_locators)


def test_prune_descendant_when_parent_is_unique() -> None:
    page = FakePage({'a[data-testid="scheduleBoxHotel"]': 1})
    locator = 'a[data-testid="scheduleBoxHotel"] div'

    pruned = _prune_descendant_css_locator(page, locator)

    assert pruned == 'a[data-testid="scheduleBoxHotel"]'


def test_keep_descendant_when_parent_is_not_unique() -> None:
    page = FakePage({'a[data-testid="scheduleBoxHotel"]': 2})
    locator = 'a[data-testid="scheduleBoxHotel"] div'

    pruned = _prune_descendant_css_locator(page, locator)

    assert pruned == locator


def test_blocklisted_root_id_does_not_generate_stable_attr_id_candidate() -> None:
    drafts = _build_stable_attr_drafts("div", "id", "__next")
    id_rules = [draft for draft in drafts if draft.rule == "stable_attr:id"]
    assert id_rules == []


def test_promote_child_inside_button_to_button_id() -> None:
    element = FakeElement(
        {
            "tag": "button",
            "role": "",
            "inputType": "",
            "attrs": {"id": "bookNowBtn"},
        }
    )
    page = FakePage({"#bookNowBtn": 1})

    drafts = _build_promoted_clickable_ancestor_drafts(page, element)

    assert drafts is not None
    css_locators = [draft.locator for draft in drafts if draft.locator_type == "CSS"]
    assert css_locators == ["#bookNowBtn"]
