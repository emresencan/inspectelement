from inspectelement.locator_generator import (
    build_dynamic_id_partial_locators,
    extract_dynamic_id_prefix_suffix,
    is_dynamic_id,
)


def test_is_dynamic_id_detects_jsf_primefaces_pattern() -> None:
    assert is_dynamic_id("mainForm:pricingItemEditDT:jdt_36240:input")
    assert is_dynamic_id("form:table:12:name")
    assert is_dynamic_id("mainForm:rows:3:cell")


def test_is_dynamic_id_rejects_static_ids() -> None:
    assert not is_dynamic_id("loginForm:username")
    assert not is_dynamic_id("submitButton")
    assert not is_dynamic_id("mainForm:pricingItemEditDT:input")


def test_extract_dynamic_id_prefix_suffix() -> None:
    parts = extract_dynamic_id_prefix_suffix("mainForm:pricingItemEditDT:jdt_36240:input")
    assert parts == ("mainForm:pricingItemEditDT:", ":input")

    parts_numeric = extract_dynamic_id_prefix_suffix("mainForm:table:7:editBtn")
    assert parts_numeric == ("mainForm:table:", ":editBtn")


def test_generate_dynamic_id_partial_css_and_xpath() -> None:
    selectors = build_dynamic_id_partial_locators("mainForm:pricingItemEditDT:jdt_36240:input")
    assert selectors is not None
    css, xpath = selectors

    assert css == '[id^="mainForm:pricingItemEditDT:"][id$=":input"]'
    assert (
        xpath
        == "//*[starts-with(@id,'mainForm:pricingItemEditDT:') and "
        "substring(@id, string-length(@id) - string-length(':input') + 1) = ':input']"
    )
