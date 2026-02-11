from inspectelement.models import LocatorCandidate, OverrideEntry
from inspectelement.override_logic import build_override_candidate, inject_override_candidate


def test_override_candidate_inserted_at_top() -> None:
    existing = [
        LocatorCandidate(
            locator_type="Playwright",
            locator='page.get_by_role("button", name="Save")',
            rule="text_role",
            uniqueness_count=1,
            score=120.0,
        ),
        LocatorCandidate(
            locator_type="CSS",
            locator='button[data-testid="save"]',
            rule="stable_attr:data-testid",
            uniqueness_count=1,
            score=118.0,
        ),
    ]
    override = OverrideEntry(
        hostname="example.com",
        element_signature="tag=button|name=save",
        locator_type="XPath",
        locator="//button[normalize-space()='Save']",
        created_at="2026-02-11T00:00:00",
    )

    override_candidate = build_override_candidate(override, uniqueness_count=1)
    merged = inject_override_candidate(existing, override_candidate, limit=5)

    assert merged[0].rule == "custom_override"
    assert merged[0].locator == "//button[normalize-space()='Save']"
    assert merged[0].locator_type == "XPath"
