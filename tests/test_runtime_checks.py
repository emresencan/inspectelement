from inspectelement.runtime_checks import (
    build_id_selector_candidates,
    is_css_safe_id,
    payload_matches_observed_element,
)


def test_css_safe_id_detection() -> None:
    assert is_css_safe_id("username_input")
    assert is_css_safe_id("-heroTitle")
    assert not is_css_safe_id("123-start")
    assert not is_css_safe_id("has space")


def test_build_id_selector_candidates_for_css_safe_id() -> None:
    assert build_id_selector_candidates("submitBtn") == [
        "#submitBtn",
        '[id="submitBtn"]',
    ]


def test_build_id_selector_candidates_escapes_attribute_selector() -> None:
    selectors = build_id_selector_candidates('a"b\\c d')
    assert selectors == ['[id="a\\"b\\\\c d"]']


def test_payload_matches_observed_element_with_tag_and_matching_text() -> None:
    payload = {"tag": "button", "text": "Save   changes"}
    observed = {"tag": "BUTTON", "text": "Save changes", "aria_label": "", "placeholder": "", "name": ""}
    assert payload_matches_observed_element(payload, observed)


def test_payload_matches_observed_element_fails_when_aux_fields_do_not_match() -> None:
    payload = {"tag": "input", "name": "email", "placeholder": "Work email"}
    observed = {"tag": "input", "name": "username", "placeholder": "Personal email", "text": "", "aria_label": ""}
    assert not payload_matches_observed_element(payload, observed)


def test_payload_matches_observed_element_accepts_tag_only_when_no_aux_fields() -> None:
    payload = {"tag": "div"}
    observed = {"tag": "div", "text": "", "aria_label": "", "placeholder": "", "name": ""}
    assert payload_matches_observed_element(payload, observed)
